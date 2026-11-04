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
import logging
import random
import threading
import time
from contextlib import closing
from datetime import UTC, datetime, timedelta
from os import environ
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import requests
from PIL import Image, UnidentifiedImageError
from urllib3.exceptions import LocationParseError
from urllib3.util import parse_url

from . import ah, audit, auth

log = logging.getLogger(__name__)

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
WAIT_SECONDS = 10  # zo lang wacht een verzoek op een download die een ander verzoek al doet
MAX_REDIRECTS = 3
PACE_SECONDS = 0.3  # op afroep: minstens zoveel tussen twee verzoeken op dezelfde host, plus jitter
PACE_JITTER = 0.2
BACKGROUND_PACE = (2.0, 5.0)  # de rustpauze tussen twee foto's in de achtergrondtaak
PAUSE_HOURS = 12  # na een 403 of 429
RETRY_AFTER = {
    1: timedelta(days=1),
    3: timedelta(days=7),
}  # vanaf zoveel mislukkingen zo lang wachten
GIVE_UP_AFTER = 10
NOT_FOUND_COUNTS = 3
SETTING_PAUSED = "image_cache_paused_until:"  # plus de host
PREFETCH_LIMIT = 300  # per nachtelijke ronde
PREFETCH_DEADLINE_SECONDS = 600
PREFETCH_AFTER_SYNC = 40  # de kleine ronde na een synchronisatie van bestellingen of bonnen
PREFETCH_AFTER_SYNC_SECONDS = 120

# Waar de interface foto's van winkels laat zien: (tabel, kolom). Een tabel die er (nog) niet is slaan we over.
KNOWN_IMAGE_COLUMNS = (
    ("ah_order_items", "image"), ("ah_basket_items", "image"), ("ah_receipt_products", "image"),
    ("offers", "image"), ("offer_history", "image"), ("offer_recipes", "image"),
    ("jumbo_order_items", "image"), ("picnic_order_items", "image"), ("crisp_order_items", "image"),
)

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
_PREFETCH_THREADS: list[threading.Thread] = []
_PREFETCH_RUNNING = threading.Lock()  # hooguit een ronde tegelijk (de nachtelijke en die na een synchronisatie)


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


def _pace(host: str) -> None:
    """Wacht tot er genoeg tijd zit tussen dit verzoek en het vorige op dezelfde host (`PACE_SECONDS` plus jitter). Onder de lock
    reserveren we alleen ons tijdstip; het slapen gebeurt zonder lock en zonder slot, zodat een wachtende thread niemand anders
    ophoudt. De rustpauze van de achtergrondtaak (2 tot 5 s) zit in `prefetch`, niet hier."""
    gap = PACE_SECONDS + random.uniform(0, PACE_JITTER)
    with _PACE_LOCK:
        start = max(time.monotonic(), _PACE.get(host, float("-inf")) + gap)
        _PACE[host] = start
    wait = start - time.monotonic()
    if wait > 0:
        time.sleep(wait)


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
    voor een antwoord dat geen 200 is. Een doorverwijzing volgen we zelf (hooguit `MAX_REDIRECTS`), en alleen naar een adres van
    een winkel: zo kan een CDN de app nooit naar een intern adres sturen."""
    session = _session(host)
    for _hop in range(MAX_REDIRECTS + 1):
        with session.get(url, headers=HEADERS[store], timeout=TIMEOUT, stream=True, allow_redirects=False) as response:
            if response.status_code in (301, 302, 303, 307, 308):
                target = urljoin(url, str(response.headers.get("Location") or ""))
                if not _safe_redirect(target):
                    raise ValueError(f"doorverwijzing naar een onbekende host ({urlsplit(target).hostname or '?'})")
                url = target
                continue
            return _read_image(response)
    raise ValueError("te veel doorverwijzingen")


def _safe_redirect(target: str) -> bool:
    """Mag een doorverwijzing hierheen? Alleen https naar een winkelhost, en de parser die het verzoek verstuurt (urllib3) moet
    dezelfde host lezen als de onze: een adres met een `@`, een backslash, spatie of stuurteken in de host valt zo af."""
    if not store_for(target) or urlsplit(target).scheme != "https":
        return False
    try:
        sent = parse_url(target)
    except LocationParseError:
        return False
    authority = urlsplit(target).netloc
    if any(char in authority for char in "@\\") or any(char.isspace() or ord(char) < 32 for char in authority):
        return False
    return bool(sent.host) and sent.host == urlsplit(target).hostname and sent.scheme == "https"


def _read_image(response: requests.Response) -> tuple[bytes, str]:
    """(inhoud, type) uit een antwoord van de winkel, met de grenzen op type en grootte."""
    if response.status_code != 200:
        error = requests.HTTPError(f"HTTP {response.status_code}")
        error.status = response.status_code  # type: ignore[attr-defined]
        raise error
    content_type = str(response.headers.get("Content-Type", "")).split(";")[0].strip().lower()
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
    try:
        partial.write_bytes(data)
        os.replace(partial, target)
    except OSError:
        partial.unlink(missing_ok=True)
        raise
    return path, hashlib.sha256(data).hexdigest()


def _fetch_once(url: str, now: str) -> dict[str, Any] | None:
    store = store_for(url)
    host = urlsplit(url).hostname or ""
    if not store or _paused(host, now):
        return None
    try:
        _pace(host)
        with _SLOTS:
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
        path, digest = _store_file(url, data, content_type)
    except (ValueError, OSError) as exc:  # geen afbeelding, of de schijf is vol of alleen-lezen: niet meteen opnieuw proberen
        _record_failure(url, now, 1, str(exc) or exc.__class__.__name__)
        return None
    _record_success(url, now, path, content_type, len(data), width, height, digest)
    return lookup(url)


def fetch(url: str, *, now: str | None = None) -> dict[str, Any] | None:
    """De rij van een foto die op schijf staat, na hem zo nodig op te halen. None als dat niet kan: een onbekende host, een
    host in pauze, een adres dat na een mislukking nog wacht, of een mislukking nu. Vraagt een ander verzoek dezelfde foto
    al op, dan wacht dit verzoek hooguit `WAIT_SECONDS` op dat resultaat. Een hit leest alleen: geen schrijfactie per foto."""
    if not store_for(url):
        return None
    now = now or _now()
    row = lookup(url)
    if row and row.get("path") and (images_dir() / row["path"]).is_file():
        return row
    if not row:
        register(url, now)
        row = lookup(url)
    if row and row.get("path"):
        if (images_dir() / row["path"]).is_file():
            # Een ander verzoek heeft de foto net opgehaald, tussen onze eerste blik (nog geen rij) en het registreren. Dat pad
            # is goed: niet wissen, anders halen we de foto een tweede keer op.
            return row
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
        running.wait(WAIT_SECONDS)
        found = lookup(url)
        return found if found and found.get("path") else None
    try:
        # Nog een keer kijken: een ander verzoek kan de foto net hebben opgehaald, tussen onze eerste blik hierboven en het slot.
        # Dan zag dit verzoek geen lopende download meer en zou het de foto een tweede keer ophalen.
        latest = lookup(url)
        if latest and latest.get("path") and (images_dir() / latest["path"]).is_file():
            return latest
        if not latest or not due(latest, now):
            return None
        return _fetch_once(url, now)
    finally:
        with _INFLIGHT_LOCK:
            _INFLIGHT.pop(url, None)
        running.set()


# --- de achtergrondtaak ----------------------------------------------------------------------------


def enabled() -> bool:
    """Of de achtergrondtaak foto's vooruit mag ophalen (`MEALIESHOPPER_IMAGE_PREFETCH`, standaard aan)."""
    return environ.get("MEALIESHOPPER_IMAGE_PREFETCH", "true").strip().lower() not in {"0", "false", "no", "off"}


def collect_known(now: str | None = None) -> int:
    """Zet elk winkeladres uit de tabellen van de interface in `image_cache`; geeft het aantal nieuwe adressen."""
    now = now or _now()
    added = 0
    with closing(_connect()) as conn, conn:
        tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        for table, column in KNOWN_IMAGE_COLUMNS:
            if table not in tables or column not in {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}:
                continue  # een database van vóór die kolom (de migratie volgt bij het eerste gebruik van die module)
            urls = {row[0] for row in conn.execute(f"SELECT DISTINCT {column} FROM {table} WHERE {column} IS NOT NULL AND {column} != ''")}
            for url in urls:
                if not store_for(url):
                    continue
                cursor = conn.execute(
                    "INSERT OR IGNORE INTO image_cache (url, host, seen_at) VALUES (?, ?, ?)", (url, urlsplit(url).hostname or "", now)
                )
                added += cursor.rowcount
    return added


def prefetch(*, limit: int = PREFETCH_LIMIT, deadline_seconds: float = PREFETCH_DEADLINE_SECONDS, now: str | None = None) -> dict[str, Any]:
    """Haal in een rustig tempo op wat nog ontbreekt, nieuwste adressen eerst: hooguit `limit` verzoeken en `deadline_seconds` lang.
    `skipped` zijn adressen die na een mislukking nog wachten of op een host in pauze staan, `paused` het aantal hosts in pauze."""
    now = now or _now()
    result = {"fetched": 0, "failed": 0, "skipped": 0, "paused": 0}
    if not _PREFETCH_RUNNING.acquire(blocking=False):
        return {**result, "running": True}
    try:
        collect_known(now)
        with closing(_connect()) as conn:
            rows = [dict(row) for row in conn.execute("SELECT * FROM image_cache WHERE path IS NULL ORDER BY seen_at DESC, id DESC")]
        paused_hosts: set[str] = set()
        deadline = time.monotonic() + deadline_seconds
        for row in rows:
            if result["fetched"] + result["failed"] >= limit or time.monotonic() > deadline:
                break
            host = row["host"]
            if host in paused_hosts or _paused(host, now):
                paused_hosts.add(host)
                result["skipped"] += 1
                continue
            if not due(row, now):
                result["skipped"] += 1
                continue
            if result["fetched"] + result["failed"]:
                time.sleep(random.uniform(*BACKGROUND_PACE))  # rustig aan: de winkel merkt ons liever niet op
            if fetch(row["url"], now=now):
                result["fetched"] += 1
            else:
                result["failed"] += 1
                if _paused(host, now):
                    paused_hosts.add(host)
        result["paused"] = len(paused_hosts)
    finally:
        _PREFETCH_RUNNING.release()
    return result


def prefetch_nightly() -> dict[str, Any] | None:
    """Voor de nachtelijke taak (offers_schedule): de grote ronde, met een regel in het auditlog. None als de taak uitstaat of
    misging; een fout laat de rest van de nachtelijke taak doorgaan."""
    if not enabled():
        return None
    try:
        result = prefetch()
    except Exception:  # noqa: BLE001 - de cache is een extraatje
        log.exception("Afbeeldingencache: de nachtelijke ronde mislukte")
        return None
    audit.record(kind="task", service="images", action="prefetch", detail=result)
    return result


def _prefetch_threads() -> list[threading.Thread]:
    with _INFLIGHT_LOCK:
        _PREFETCH_THREADS[:] = [worker for worker in _PREFETCH_THREADS if worker.is_alive()]
        return list(_PREFETCH_THREADS)


def prefetch_async(limit: int = PREFETCH_AFTER_SYNC) -> None:
    """Een kleine ronde in een eigen thread, na een synchronisatie van bestellingen of bonnen: de nieuwe foto's staan er dan al
    voordat iemand ze opent. Loopt er al een ronde, dan niet nog een."""
    if not enabled():
        return
    with _INFLIGHT_LOCK:
        _PREFETCH_THREADS[:] = [worker for worker in _PREFETCH_THREADS if worker.is_alive()]
        if _PREFETCH_THREADS:
            return

        def run() -> None:
            try:
                prefetch(limit=limit, deadline_seconds=PREFETCH_AFTER_SYNC_SECONDS)
            except Exception:  # noqa: BLE001
                log.exception("Afbeeldingencache: de ronde na de synchronisatie mislukte")

        worker = threading.Thread(target=run, name="image-prefetch", daemon=True)
        _PREFETCH_THREADS.append(worker)
        worker.start()
