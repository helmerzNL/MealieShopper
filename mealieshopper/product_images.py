"""Afbeeldingen van aanbiedingen die je eerder kocht, lokaal bewaard: om later te vergelijken.

Een winkel verandert soms de naam van een product, of zet hetzelfde product onder een andere naam in de aanbieding. Met de afbeelding
van toen zie je in de Prijsgeschiedenis of het hetzelfde product is. Daarom bewaart `save_bought` bij elke synchronisatie van de
aanbiedingen (`offers.sync`) de afbeelding van elke aanbieding waarvan je het product minstens één keer kocht (volgens je bestellingen
en kassabonnen, zie `prices.bought_ids`). Ook de afbeelding van een eerdere aanbieding uit de geschiedenis komt erbij, zolang de winkel
die nog geeft.

- De afbeelding wordt verkleind tot hooguit `MAX_WIDTH` (1024) pixels breed; een kleinere blijft zoals hij is (nooit vergroot). Waar de
  winkel een grotere versie heeft (AH, Picnic en Crisp), halen we die op, zodat de bewaarde afbeelding zo scherp mogelijk is.
- Per afbeelding staat de naam die het product toen had (`title`). Verandert de afbeelding later, dan komt de nieuwe erbij en blijft de
  oude staan: per product hooguit `MAX_PER_PRODUCT`, waarbij de eerste altijd blijft.
- Het bewaren laat een synchronisatie nooit mislukken en duurt hooguit `DEADLINE_SECONDS`; wat niet lukt, volgt de volgende keer.
- De afbeeldingen staan in `product_images` (zie `prices.SCHEMA`) en gaan mee in de back-up: ze zijn niet opnieuw op te halen als de
  winkel ze vervangt. Bij het terugzetten wordt elke afbeelding opnieuw gelezen (`checked`).
"""
from __future__ import annotations

import hashlib
import io
import logging
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import closing
from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.parse import urlsplit

import requests
from PIL import Image, ImageOps, UnidentifiedImageError, features

from . import crisp, offers, picnic, prices, profile

log = logging.getLogger(__name__)

MAX_WIDTH = 1024
MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024
MAX_STORED_BYTES = 2 * 1024 * 1024
MAX_PIXELS = 40_000_000  # meer pixels openen we niet (een "decompressiebom")
MAX_PER_PRODUCT = 10
MAX_PER_SYNC = 40  # zoveel nieuwe afbeeldingen per synchronisatie; de rest volgt de volgende keer
DEADLINE_SECONDS = 20
WORKERS = 6
TIMEOUT = (5, 10)
QUALITY = 85

_KEEP_FORMATS = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
_AH_RENDITION = "800x800_JPG_Q90"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _web_url(url: Any) -> bool:
    return isinstance(url, str) and urlsplit(url).scheme in ("http", "https") and bool(urlsplit(url).netloc)


def source_urls(store: str, url: str) -> list[str]:
    """De adressen om te proberen, de grootste versie eerst: AH, Picnic en Crisp geven dezelfde foto in meer maten."""
    larger = None
    if store == "ah" and "rendition=" in url:
        head, _, tail = url.partition("rendition=")
        rest = tail.partition("&")
        larger = f"{head}rendition={_AH_RENDITION}{rest[1]}{rest[2]}"
    elif store == "picnic":
        larger = picnic.resized_image(url, "large")
    elif store == "crisp":
        larger = crisp._resized(url, crisp.THUMB_LARGE)
    return [candidate for candidate in dict.fromkeys([larger, url]) if _web_url(candidate)]


def _download(url: str) -> bytes:
    """De afbeelding achter een adres, of een fout. Leest hooguit `MAX_DOWNLOAD_BYTES`."""
    with requests.get(url, timeout=TIMEOUT, stream=True, headers={"Accept": "image/*"}) as response:
        if response.status_code != 200:
            raise RuntimeError(f"HTTP {response.status_code}")
        content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if content_type.startswith("text/") or content_type == "image/svg+xml":
            raise RuntimeError(f"geen afbeelding ({content_type})")
        chunks, size = [], 0
        for chunk in response.iter_content(64 * 1024):
            size += len(chunk)
            if size > MAX_DOWNLOAD_BYTES:
                raise RuntimeError("afbeelding is te groot")
            chunks.append(chunk)
    return b"".join(chunks)


def _open(data: bytes) -> Image.Image:
    try:
        image = Image.open(io.BytesIO(data))
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("geen afbeelding") from exc
    if image.width <= 0 or image.height <= 0 or image.width * image.height > MAX_PIXELS:
        image.close()
        raise ValueError("afbeelding is te groot")
    return image


def shrink(data: bytes) -> tuple[bytes, str, int, int]:
    """(inhoud, type, breedte, hoogte): de afbeelding hooguit `MAX_WIDTH` pixels breed.

    Een PNG, JPEG of WebP die al smal genoeg is (en niet gedraaid moet worden) blijft zoals hij is. De rest wordt verkleind en als
    WebP bewaard (of, zonder WebP in Pillow, als JPEG of met doorzichtigheid als PNG). Geeft een ValueError als het geen afbeelding is.
    """
    with _open(data) as image:
        rotated = image.getexif().get(0x0112, 1) in (5, 6, 7, 8)  # de breedte in beeld is dan de hoogte van het bestand
        if (image.format in _KEEP_FORMATS and not rotated and image.width <= MAX_WIDTH and len(data) <= MAX_STORED_BYTES
                and profile.sniff(data) == _KEEP_FORMATS[image.format]):
            return data, _KEEP_FORMATS[image.format], image.width, image.height
        try:
            picture = ImageOps.exif_transpose(image)
            alpha = picture.mode in ("RGBA", "LA", "PA") or (picture.mode == "P" and "transparency" in picture.info)
            picture = picture.convert("RGBA" if alpha else "RGB")
            if picture.width > MAX_WIDTH:
                height = max(1, round(picture.height * MAX_WIDTH / picture.width))
                picture = picture.resize((MAX_WIDTH, height), Image.Resampling.LANCZOS)
            out = io.BytesIO()
            if features.check("webp"):
                picture.save(out, "WEBP", quality=QUALITY, method=4)
                content_type = "image/webp"
            elif alpha:
                picture.save(out, "PNG", optimize=True)
                content_type = "image/png"
            else:
                picture.save(out, "JPEG", quality=QUALITY, optimize=True, progressive=True)
                content_type = "image/jpeg"
        except (OSError, ValueError) as exc:
            raise ValueError("afbeelding kan niet worden verkleind") from exc
    result = out.getvalue()
    if len(result) > MAX_STORED_BYTES:
        raise ValueError("afbeelding is te groot")
    return result, content_type, picture.width, picture.height


def checked(data: Any) -> dict[str, Any]:
    """Lees een bewaarde afbeelding opnieuw (voor het terugzetten van een back-up): `digest`, `content_type`, `width` en `height`
    volgen uit de inhoud, niet uit wat het bestand zegt. Een ValueError als het geen PNG, JPEG of WebP van hooguit 1024 pixels breed is."""
    if not isinstance(data, bytes) or not data or len(data) > MAX_STORED_BYTES:
        raise ValueError("image missing or too large")
    content_type = profile.sniff(data)
    if not content_type:
        raise ValueError("not a PNG, JPEG or WebP image")
    with _open(data) as image:
        if _KEEP_FORMATS.get(image.format or "") != content_type:
            raise ValueError("image is not what it says it is")
        if image.width > MAX_WIDTH:
            raise ValueError(f"image is wider than {MAX_WIDTH} pixels")
        width, height = image.width, image.height
    return {"digest": hashlib.sha256(data).hexdigest(), "content_type": content_type, "width": width, "height": height}


def _fetch(store: str, url: str) -> tuple[bytes, str, int, int]:
    """Haal de afbeelding op (de grootste versie eerst) en verklein hem."""
    error: Exception | None = None
    for candidate in source_urls(store, url):
        try:
            return shrink(_download(candidate))
        except Exception as exc:  # noqa: BLE001 - de volgende versie proberen
            error = exc
    raise error or ValueError("geen adres")


def _wanted(conn, store: str, items: Iterable[dict[str, Any]], bought: set[str]) -> list[tuple[str, str, str]]:
    """(product-id, naam, adres) van de afbeeldingen die we nog niet hebben: eerst de aanbiedingen van nu, dan die uit de geschiedenis
    (per product de nieuwste). Per product één afbeelding per keer."""
    have = {(row["product_id"], row["source_url"]) for row in conn.execute("SELECT product_id, source_url FROM product_images WHERE store = ?", (store,))}
    history = conn.execute(
        "SELECT product_id, title, image FROM offer_history WHERE store = ? AND COALESCE(image, '') != '' ORDER BY last_seen DESC, id DESC",
        (store,),
    ).fetchall()
    found: dict[str, tuple[str, str, str]] = {}
    for product_id, title, url in [(str(i.get("id") or ""), str(i.get("title") or ""), i.get("image")) for i in items] + [
        (row["product_id"], row["title"], row["image"]) for row in history
    ]:
        if product_id in bought and product_id not in found and title and _web_url(url):
            found[product_id] = (product_id, title, url)
    return [entry for entry in found.values() if (entry[0], entry[2]) not in have][:MAX_PER_SYNC]


def _store(conn, store: str, product_id: str, title: str, url: str, image: tuple[bytes, str, int, int], now: str) -> bool:
    """Bewaar een afbeelding; True als hij nieuw is. Dezelfde afbeelding onder een ander adres krijgt alleen het nieuwe adres, zodat
    we hem de volgende keer niet opnieuw ophalen."""
    data, content_type, width, height = image
    digest = hashlib.sha256(data).hexdigest()
    exists = conn.execute("SELECT 1 FROM product_images WHERE store = ? AND product_id = ? AND digest = ?", (store, product_id, digest)).fetchone()
    if exists:
        conn.execute("UPDATE product_images SET source_url = ? WHERE store = ? AND product_id = ? AND digest = ?", (url, store, product_id, digest))
        return False
    conn.execute(
        """INSERT INTO product_images (store, product_id, title, source_url, digest, content_type, width, height, data, saved_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (store, product_id, title, url, digest, content_type, width, height, data, now),
    )
    rows = conn.execute("SELECT id FROM product_images WHERE store = ? AND product_id = ? ORDER BY saved_at, id", (store, product_id)).fetchall()
    if len(rows) > MAX_PER_PRODUCT:
        doomed = [row["id"] for row in rows[1:1 + len(rows) - MAX_PER_PRODUCT]]  # de eerste blijft altijd
        conn.execute(f"DELETE FROM product_images WHERE id IN ({','.join('?' * len(doomed))})", doomed)
    return True


def save_bought(store: str, items: Iterable[dict[str, Any]] = (), now: str | None = None) -> int:
    """Bewaar de afbeeldingen van de aanbiedingen (`items`, in de vorm van `offers.sync`) en van eerdere aanbiedingen die je minstens één
    keer kocht. Geeft het aantal nieuwe afbeeldingen; een fout geeft 0 en wordt gelogd, zodat de synchronisatie altijd doorgaat."""
    try:
        return _save_bought(store, list(items), now or _now())
    except Exception:  # noqa: BLE001 - het bewaren van afbeeldingen mag een synchronisatie nooit laten mislukken
        log.warning("Afbeeldingen van gekochte aanbiedingen bewaren mislukt voor %s", store, exc_info=True)
        return 0


def _save_bought(store: str, items: list[dict[str, Any]], now: str) -> int:
    if store not in offers.STORES:
        return 0
    bought = prices.bought_ids(store, now[:10])
    if not bought:
        return 0
    with closing(prices._connect()) as conn:
        wanted = _wanted(conn, store, items, bought)
    if not wanted:
        return 0
    fetched: list[tuple[tuple[str, str, str], tuple[bytes, str, int, int]]] = []
    failed = 0
    deadline = time.monotonic() + DEADLINE_SECONDS
    pool = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="product-images")
    try:
        pending = {pool.submit(_fetch, store, entry[2]): entry for entry in wanted}
        while pending and time.monotonic() < deadline:
            done, _ = wait(pending, timeout=max(0.0, deadline - time.monotonic()), return_when=FIRST_COMPLETED)
            for future in done:
                entry = pending.pop(future)
                try:
                    fetched.append((entry, future.result()))
                except Exception as exc:  # noqa: BLE001 - de volgende synchronisatie probeert het opnieuw
                    failed += 1
                    log.info("Afbeelding van %s %s niet bewaard: %s", store, entry[0], exc)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)  # wat na de deadline nog loopt, volgt de volgende keer
    saved = 0
    if fetched:
        with closing(prices._connect()) as conn, conn:
            for (product_id, title, url), image in fetched:
                saved += _store(conn, store, product_id, title, url, image, now)
    if saved or failed:
        log.info("Afbeeldingen van gekochte aanbiedingen (%s): %d bewaard, %d mislukt", store, saved, failed)
    return saved


def get(image_id: Any) -> tuple[str, bytes] | None:
    """(type, inhoud) van een bewaarde afbeelding, of None."""
    try:
        key = int(image_id)
    except (TypeError, ValueError):
        return None
    with closing(prices._connect()) as conn:
        row = conn.execute("SELECT content_type, data FROM product_images WHERE id = ?", (key,)).fetchone()
    return (row["content_type"], bytes(row["data"])) if row else None
