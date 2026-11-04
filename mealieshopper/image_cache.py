"""De afbeeldingencache: elke productfoto van een winkel een keer ophalen en daarna voor altijd van schijf serveren.

De interface toont foto's van AH, Jumbo, Picnic en Crisp (bestellingen, het mandje, aanbiedingen, zoekresultaten, recepten).
Rechtstreeks van de winkel laden is hotlinken: het vertelt de winkel wat iemand bekijkt en breekt zodra een adres verandert.
Daarom vraagt de interface ze op via `/api/images/<token>` (`storeImage()` in static/app.js, het token is het winkeladres in
URL-veilige base64). De route haalt een foto die er nog niet is op zoals een browser dat zou doen (zie `HEADERS`), bewaart
het origineel in `<datamap>/images/<ab>/<sha256-van-het-adres>.<ext>` en serveert daarna altijd het bestand.

Zuinig voor de winkel: hooguit twee verzoeken tegelijk, op afroep minstens 0,3 s tussen twee verzoeken op dezelfde host en
in de achtergrond 2 tot 5 s, en een 403 of 429 zet de host twaalf uur stil. Een adres dat mislukt wacht een dag, vanaf drie
mislukkingen een week en vanaf tien nooit meer (een 404 telt voor drie).

De tabel `image_cache` (in auth.sqlite3) kent per adres de host, het pad, het type, de maten, wanneer het is opgehaald en de
mislukkingen. Ze staat niet in de back-up (backup.EXCLUDED_TABLES): de cache is opnieuw op te bouwen. Een nachtelijke ronde
(`prefetch`, vanuit offers_schedule) haalt in een rustig tempo binnen wat nog ontbreekt.
"""

from __future__ import annotations

import base64
import hashlib
import io
import os
import random
import threading
import time
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import requests
from PIL import Image, UnidentifiedImageError

from . import ah, auth

SCHEMA = """
CREATE TABLE IF NOT EXISTS image_cache (
  id INTEGER PRIMARY KEY,
  url TEXT NOT NULL UNIQUE,
  host TEXT NOT NULL,
  path TEXT,
  content_type TEXT,
  bytes INTEGER,
  width INTEGER,
  height INTEGER,
  digest TEXT,
  fetched_at TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  last_attempt_at TEXT,
  error TEXT,
  seen_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS image_cache_todo ON image_cache (fetched_at, seen_at);
"""

MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 40_000_000  # meer pixels openen we niet (een "decompressiebom")
TIMEOUT = (5, 15)
CONCURRENCY = 2
PACE_SECONDS = 0.3  # op afroep: minstens zoveel tussen twee verzoeken op dezelfde host, plus jitter
PACE_JITTER = 0.2
BACKGROUND_PACE = (2.0, 5.0)  # in de achtergrondtaak
PAUSE_HOURS = 12  # na een 403 of 429
RETRY_AFTER = {
    1: timedelta(days=1),
    3: timedelta(days=7),
}  # vanaf zoveel mislukkingen zo lang wachten
GIVE_UP_AFTER = 10
NOT_FOUND_COUNTS = 3
SETTING_PAUSED = "image_cache_paused_until:"  # plus de host

_BROWSER_ACCEPT = "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8"
_BROWSER_LANGUAGE = "nl-NL,nl;q=0.9,en;q=0.8"


def _browser_headers(referer: str, site: str) -> dict[str, str]:
    return {
        "User-Agent": ah.AH_BROWSER_USER_AGENT,
        "Accept": _BROWSER_ACCEPT,
        "Accept-Language": _BROWSER_LANGUAGE,
        "Referer": referer,
        "Sec-Fetch-Dest": "image",
        "Sec-Fetch-Mode": "no-cors",
        "Sec-Fetch-Site": site,
    }


# Per winkel de headers van een browser op de site van de winkel die een productfoto laadt.
HEADERS: dict[str, dict[str, str]] = {
    "ah": _browser_headers("https://www.ah.nl/", "same-site"),
    "jumbo": _browser_headers("https://www.jumbo.com/", "same-site"),
    "picnic": _browser_headers("https://picnic.app/", "cross-site"),
    "crisp": _browser_headers("https://crispapp.nl/", "same-origin"),
}

_EXTENSIONS = {
    "image/webp": "webp",
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/avif": "avif",
    "image/gif": "gif",
}
_PICNIC_SUFFIX = ".picnicinternational.com"

_SLOTS = threading.BoundedSemaphore(CONCURRENCY)
_PACE_LOCK = threading.Lock()
_PACE: dict[str, float] = {}  # host -> monotonic tijdstip van het laatste verzoek
_SESSIONS: dict[str, requests.Session] = {}
_INFLIGHT_LOCK = threading.Lock()
_INFLIGHT: dict[str, threading.Event] = {}  # adres -> het verzoek dat het nu ophaalt


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _connect():
    conn = auth.connect()
    conn.executescript(SCHEMA)
    return conn


def images_dir() -> Path:
    return auth.data_dir() / "images"


def _reset_pace() -> None:
    """Voor tests: vergeet het tempo en de sessies."""
    with _PACE_LOCK:
        _PACE.clear()
        _SESSIONS.clear()


# --- adressen en tokens ----------------------------------------------------------------------------


def store_for(url: Any) -> str | None:
    """De winkel bij een adres (`ah`, `jumbo`, `picnic`, `crisp`), of None als het geen foto van een winkel is."""
    if not isinstance(url, str) or not url:
        return None
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return None
    host = parts.hostname or ""
    if host == "static.ah.nl":
        return "ah"
    if host == "jumbo.com" or host.endswith(".jumbo.com"):
        return "jumbo"
    if host.startswith("storefront-prod.") and host.endswith(_PICNIC_SUFFIX):
        return "picnic"
    if host == "crispapp.nl":
        return "crisp"
    return None


def token_for(url: str) -> str:
    """Het winkeladres als URL-veilige base64 zonder `=`: het stuk achter /api/images/."""
    return base64.urlsafe_b64encode(url.encode("utf-8")).decode("ascii").rstrip("=")


def url_for(token: Any) -> str | None:
    """Het adres achter een token, of None als het niet decodeert of geen foto van een winkel is."""
    if not isinstance(token, str) or not token:
        return None
    try:
        url = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    return url if store_for(url) else None


# --- de tabel --------------------------------------------------------------------------------------


def register(url: str, now: str | None = None) -> int:
    """Zet een adres in de tabel (of ververs `seen_at`) en geef het id."""
    now = now or _now()
    host = urlsplit(url).hostname or ""
    with closing(_connect()) as conn, conn:
        conn.execute(
            "INSERT INTO image_cache (url, host, seen_at) VALUES (?, ?, ?) ON CONFLICT(url) DO UPDATE SET seen_at = excluded.seen_at",
            (url, host, now),
        )
        return int(
            conn.execute("SELECT id FROM image_cache WHERE url = ?", (url,)).fetchone()[
                0
            ]
        )


def lookup(url: str) -> dict[str, Any] | None:
    """De rij van een adres als dict, of None."""
    with closing(_connect()) as conn:
        row = conn.execute("SELECT * FROM image_cache WHERE url = ?", (url,)).fetchone()
    return dict(row) if row else None


def _rows() -> list[dict[str, Any]]:
    with closing(_connect()) as conn:
        return [
            dict(row) for row in conn.execute("SELECT * FROM image_cache ORDER BY id")
        ]


def stats() -> dict[str, int]:
    """Hoeveel foto's er in de cache staan en hoeveel bytes dat is."""
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(bytes), 0) AS b FROM image_cache WHERE path IS NOT NULL"
        ).fetchone()
    return {"count": int(row["n"]), "bytes": int(row["b"])}


def _record_failure(url: str, now: str, count: int, error: str) -> None:
    with closing(_connect()) as conn, conn:
        conn.execute(
            "UPDATE image_cache SET attempts = attempts + ?, last_attempt_at = ?, error = ? WHERE url = ?",
            (count, now, error[:300], url),
        )


def _record_success(
    url: str,
    now: str,
    path: str,
    content_type: str,
    size: int,
    width: int,
    height: int,
    digest: str,
) -> None:
    with closing(_connect()) as conn, conn:
        conn.execute(
            """
            UPDATE image_cache SET path = ?, content_type = ?, bytes = ?, width = ?, height = ?, digest = ?, fetched_at = ?, error = NULL
            WHERE url = ?
            """,
            (path, content_type, size, width, height, digest, now, url),
        )


def due(row: dict[str, Any], now: str) -> bool:
    """Mag dit adres (weer) worden opgehaald? Niet als het er al staat, en na een mislukking pas na de wachttijd."""
    if row.get("path"):
        return False
    attempts = int(row.get("attempts") or 0)
    if attempts <= 0:
        return True
    if attempts >= GIVE_UP_AFTER:
        return False
    wait = RETRY_AFTER[3] if attempts >= 3 else RETRY_AFTER[1]
    last = row.get("last_attempt_at")
    if not last:
        return True
    return datetime.fromisoformat(last) + wait <= datetime.fromisoformat(now)


# --- pauze en tempo per host -----------------------------------------------------------------------


def paused_until(host: str) -> str | None:
    value = auth.get_setting(SETTING_PAUSED + host, "")
    return value or None


def _pause(host: str, now: str) -> str:
    until = (datetime.fromisoformat(now) + timedelta(hours=PAUSE_HOURS)).isoformat(
        timespec="seconds"
    )
    auth.set_setting(SETTING_PAUSED + host, until)
    return until


def _paused(host: str, now: str) -> bool:
    until = paused_until(host)
    return bool(until) and until > now


def _session(host: str) -> requests.Session:
    with _PACE_LOCK:
        session = _SESSIONS.get(host)
        if session is None:
            session = _SESSIONS[host] = requests.Session()
        return session


def _pace(host: str, background: bool) -> None:
    """Wacht tot er genoeg tijd zit tussen dit verzoek en het vorige op dezelfde host."""
    gap = (
        random.uniform(*BACKGROUND_PACE)
        if background
        else PACE_SECONDS + random.uniform(0, PACE_JITTER)
    )
    with _PACE_LOCK:
        last = _PACE.get(host)
        wait = last + gap - time.monotonic() if last is not None else 0.0
        if wait > 0:
            time.sleep(wait)
        _PACE[host] = time.monotonic()


# --- ophalen ---------------------------------------------------------------------------------------


def _measure(data: bytes) -> tuple[int, int]:
    try:
        with Image.open(io.BytesIO(data)) as image:
            if (
                image.width <= 0
                or image.height <= 0
                or image.width * image.height > MAX_PIXELS
            ):
                raise ValueError("afbeelding is te groot")
            return image.width, image.height
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("geen afbeelding") from exc


def _download(store: str, host: str, url: str) -> tuple[bytes, str]:
    """(inhoud, type) van de winkel. Een ValueError voor wat geen bruikbare afbeelding is, een requests.HTTPError met de status
    voor een antwoord dat geen 200 is."""
    with _session(host).get(
        url, headers=HEADERS[store], timeout=TIMEOUT, stream=True
    ) as response:
        if response.status_code != 200:
            error = requests.HTTPError(f"HTTP {response.status_code}")
            error.status = response.status_code  # type: ignore[attr-defined]
            raise error
        content_type = (
            str(response.headers.get("Content-Type", "")).split(";")[0].strip().lower()
        )
        if not content_type.startswith("image/") or content_type == "image/svg+xml":
            raise ValueError(f"geen afbeelding ({content_type or 'zonder type'})")
        chunks, size = [], 0
        for chunk in response.iter_content(64 * 1024):
            size += len(chunk)
            if size > MAX_DOWNLOAD_BYTES:
                raise ValueError("afbeelding is te groot")
            chunks.append(chunk)
    return b"".join(chunks), content_type


def _store_file(url: str, data: bytes, content_type: str) -> tuple[str, str]:
    """Schrijf de inhoud naar images/<ab>/<sha256 van het adres>.<ext>; (pad, digest van de inhoud)."""
    name = hashlib.sha256(url.encode("utf-8")).hexdigest()
    path = f"{name[:2]}/{name}.{_EXTENSIONS.get(content_type, 'bin')}"
    target = images_dir() / path
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    partial.write_bytes(data)
    os.replace(partial, target)
    return path, hashlib.sha256(data).hexdigest()


def _fetch_once(url: str, now: str, background: bool) -> dict[str, Any] | None:
    store = store_for(url)
    host = urlsplit(url).hostname or ""
    if not store or _paused(host, now):
        return None
    try:
        with _SLOTS:
            _pace(host, background)
            data, content_type = _download(store, host, url)
    except requests.HTTPError as exc:
        status = getattr(exc, "status", 0)
        if status in (403, 429):
            _pause(host, now)
        _record_failure(url, now, NOT_FOUND_COUNTS if status == 404 else 1, str(exc))
        return None
    except (requests.RequestException, ValueError, OSError) as exc:
        _record_failure(url, now, 1, str(exc) or exc.__class__.__name__)
        return None
    try:
        width, height = _measure(data)
    except ValueError as exc:
        _record_failure(url, now, 1, str(exc))
        return None
    path, digest = _store_file(url, data, content_type)
    _record_success(url, now, path, content_type, len(data), width, height, digest)
    return lookup(url)


def fetch(
    url: str, *, now: str | None = None, background: bool = False
) -> dict[str, Any] | None:
    """De rij van een foto die op schijf staat, na hem zo nodig op te halen. None als dat niet kan: een onbekende host, een
    host in pauze, een adres dat na een mislukking nog wacht, of een mislukking nu. Vraagt een ander verzoek dezelfde foto
    al op, dan wacht dit verzoek op dat resultaat."""
    if not store_for(url):
        return None
    now = now or _now()
    register(url, now)
    row = lookup(url)
    if row and row.get("path") and (images_dir() / row["path"]).is_file():
        return row
    if row and row.get("path"):
        with (
            closing(_connect()) as conn,
            conn,
        ):  # het bestand is weg (een opgeruimde map): opnieuw ophalen
            conn.execute(
                "UPDATE image_cache SET path = NULL, fetched_at = NULL WHERE url = ?",
                (url,),
            )
        row = lookup(url)
    if not row or not due(row, now):
        return None
    with _INFLIGHT_LOCK:
        running = _INFLIGHT.get(url)
        if running is None:
            running = _INFLIGHT[url] = threading.Event()
            owner = True
        else:
            owner = False
    if not owner:
        running.wait(TIMEOUT[0] + TIMEOUT[1] + 5)
        found = lookup(url)
        return found if found and found.get("path") else None
    try:
        return _fetch_once(url, now, background)
    finally:
        with _INFLIGHT_LOCK:
            _INFLIGHT.pop(url, None)
        running.set()
