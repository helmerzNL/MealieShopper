"""De afbeeldingencache: elke productfoto van een winkel een keer ophalen en daarna voor altijd van schijf serveren.

De interface toont foto's van AH, Jumbo, Picnic, Crisp, Lidl en Aldi (bestellingen, het mandje, aanbiedingen, zoekresultaten, recepten).
Rechtstreeks van de winkel laden is hotlinken: het vertelt de winkel wat iemand bekijkt en breekt zodra een adres verandert.
Daarom vraagt de interface ze op via `/api/images/<token>` (`storeImage()` in static/app.js, het token is het winkeladres in
URL-veilige base64). De route haalt een foto die er nog niet is op zoals een browser dat zou doen (zie `HEADERS`), bewaart
het origineel in `<datamap>/images/<ab>/<sha256-van-het-adres>.<ext>` en serveert daarna altijd het bestand.

Zuinig voor de winkel: hooguit twee verzoeken tegelijk, op afroep minstens 0,3 s tussen twee verzoeken op dezelfde host en
in de achtergrond 2 tot 5 s, en een 403 of 429 zet de host twaalf uur stil. Een adres dat mislukt wacht een dag, vanaf drie
mislukkingen een week en vanaf tien nooit meer (een 404 telt voor drie). Een 403 met de XML-foutmelding van een opslag als Amazon S3
(`AccessDenied`, zoals bij Picnic) is geen blokkade maar een foto die er niet is: die telt als een 404 en zet de host niet stil.

De tabel `image_cache` (in auth.sqlite3) kent per adres de host, het pad, het type, de maten, wanneer het is opgehaald en de
mislukkingen. Ze staat niet in de back-up (backup.EXCLUDED_TABLES): de cache is opnieuw op te bouwen. Een nachtelijke ronde
(`prefetch`, vanuit offers_schedule) haalt in een rustig tempo binnen wat nog ontbreekt.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import os
import logging
import random
import re
import time
import weakref
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from os import environ
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import aiosqlite
import httpx
from PIL import Image, UnidentifiedImageError

from . import ah, audit, auth, http_client, tasks

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
TIMEOUT = httpx.Timeout(connect=5, read=15, write=15, pool=15)
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
    ("product_links", "image"),  # de foto van een product dat je zelf aan een bonregel koppelde (#238)
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
    "lidl": _browser_headers("https://www.lidl.nl/", "same-origin"),
    "aldi": _browser_headers("https://www.aldi.nl/", "cross-site"),
}

_EXTENSIONS = {
    "image/webp": "webp",
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/avif": "avif",
    "image/gif": "gif",
}
_PICNIC_SUFFIX = ".picnicinternational.com"

# Een opslag als Amazon S3 (de foto's van Picnic) antwoordt op een bestand dat er niet is met een 403 `AccessDenied` in XML, niet
# met een 404: zonder leesrecht op de inhoud van de bucket verraadt hij niet welke bestanden er zijn. Zo'n 403 gaat over dat ene
# adres en zegt niet dat de winkel de app weert; een blokkade van een winkel (HTML van een firewall, of een 429) wel (#236).
_MISSING_OBJECT = re.compile(rb"<Error>\s*<Code>(?:AccessDenied|NoSuchKey)</Code>")
_ERROR_BODY_BYTES = 4096

# Per event loop: een `asyncio`-semafoor hoort bij de loop waarin hij voor het eerst wacht. Productie heeft een loop, een test
# (`asyncio.run`) krijgt zo een eigen semafoor, net als de clients van http_client.
_SLOTS: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.BoundedSemaphore] = weakref.WeakKeyDictionary()
_PREFETCH_LOCKS: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = weakref.WeakKeyDictionary()
_PACE: dict[str, float] = {}  # host -> monotonic tijdstip van het laatste verzoek
_INFLIGHT: dict[str, asyncio.Event] = {}  # adres -> het verzoek dat het nu ophaalt
_PREFETCH_TASK: asyncio.Task[None] | None = None  # de ronde van `prefetch_async`, hooguit een tegelijk


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _monotonic() -> float:
    """De klok van het tempo en de deadline. Een eigen functie, zodat een test de klok kan vastzetten zonder `time.monotonic`
    voor het hele proces (en de event loop) te vervangen."""
    return time.monotonic()


async def _wait(seconds: float) -> None:
    """De pauze tussen twee verzoeken (het tempo per host en de rustpauze van de achtergrond). Een eigen functie, zodat een test
    de pauze kan vervangen zonder `asyncio.sleep` voor het hele proces te vervangen."""
    await asyncio.sleep(seconds)


@asynccontextmanager
async def _connect() -> AsyncIterator[aiosqlite.Connection]:
    """Een verbinding met de tabel `image_cache`. Sluit zonder commit, zoals `auth.connect`."""
    async with auth.connect() as conn:
        await conn.executescript(SCHEMA)
        yield conn


def images_dir() -> Path:
    return auth.data_dir() / "images"


def _slots() -> asyncio.BoundedSemaphore:
    """De `CONCURRENCY` slots van de lopende event loop: zoveel downloads tegelijk, over alle hosts samen."""
    loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
    slots: asyncio.BoundedSemaphore | None = _SLOTS.get(loop)
    if slots is None:
        slots = _SLOTS[loop] = asyncio.BoundedSemaphore(CONCURRENCY)
    return slots


def _prefetch_lock() -> asyncio.Lock:
    """Het slot van de lopende event loop dat zorgt voor hooguit een ronde tegelijk (de nachtelijke en die na een
    synchronisatie)."""
    loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
    lock: asyncio.Lock | None = _PREFETCH_LOCKS.get(loop)
    if lock is None:
        lock = _PREFETCH_LOCKS[loop] = asyncio.Lock()
    return lock


def _reset_pace() -> None:
    """Voor tests: vergeet het tempo en de slots."""
    _PACE.clear()
    _SLOTS.clear()


# --- adressen en tokens ----------------------------------------------------------------------------


# Hosts van Lidl en Aldi met het pad waaronder hun productfoto's staan. Op www.lidl.nl staat ook de rest van de site, en scene7.com is
# het gedeelde CDN van Adobe: daar hoort alleen het pad van Aldi bij.
_PATH_HOSTS = {
    "www.lidl.nl": ("lidl", "/assets/"),
    "imgproxy-retcat.assets.schwarz": ("lidl", "/"),
    "s7g10.scene7.com": ("aldi", "/is/image/aldinord/"),
}


def _store_of_host(host: str) -> str | None:
    """De winkel bij een host, zonder naar het pad te kijken."""
    if host == "static.ah.nl":
        return "ah"
    if host == "jumbo.com" or host.endswith(".jumbo.com"):
        return "jumbo"
    if host.startswith("storefront-prod.") and host.endswith(_PICNIC_SUFFIX):
        return "picnic"
    if host == "crispapp.nl":
        return "crisp"
    if host in _PATH_HOSTS:
        return _PATH_HOSTS[host][0]
    return None


def store_for(url: Any) -> str | None:
    """De winkel bij een adres (`ah`, `jumbo`, `picnic`, `crisp`, `lidl`, `aldi`), of None als het geen foto van een winkel is."""
    if not isinstance(url, str) or not url:
        return None
    try:
        parts = urlsplit(url)
    except ValueError:  # bijvoorbeeld een kapotte IPv6-host (`https://[static.ah.nl]/`)
        return None
    if parts.scheme not in ("http", "https"):
        return None
    host = parts.hostname or ""
    if host in _PATH_HOSTS and not parts.path.startswith(_PATH_HOSTS[host][1]):
        return None
    return _store_of_host(host)


def _plain_authority(authority: str) -> bool:
    """Bestaat het stuk met de host alleen uit gewone zichtbare ASCII-tekens, zonder `@`, backslash of `%`? Een gecodeerd teken
    (`%2e`, `%2f`) of een teken buiten ASCII (een host in Unicode, die httpx eerst omzet) leest de ene parser anders dan de andere."""
    return not any(char in "@\\%" or not 32 < ord(char) < 127 for char in authority)


def safe_url(url: Any) -> bool:
    """Mag de app dit adres ophalen? Alleen https naar een winkelhost (`store_for`), en de parser die het verzoek verstuurt (httpx)
    moet dezelfde host lezen als de onze: een adres met een `@`, een backslash, een `%`, spatie, stuurteken of teken buiten ASCII
    in de host valt zo af, net als een poort buiten 1 tot 65535. Dezelfde eis voor een token uit de browser, een adres uit een
    tabel en een doorverwijzing van een CDN: een adres dat hier niet door komt, krijgt nooit een verzoek. Geeft nooit een fout."""
    if not store_for(url):
        return False
    parts = urlsplit(url)
    if parts.scheme != "https" or not _plain_authority(parts.netloc):
        return False
    try:
        sent: httpx.URL = httpx.URL(url)
        # Een host met een A-label (`xn--...`) mag, zoals voorheen, als httpx hem kan decoderen: `xn--mnchen-3ya.jumbo.com`
        # gaat door, een kapot label (`xn--a`, `xn--`) geeft hier een idna-fout (een ValueError) en valt af.
        decoded: str = sent.host
    except (httpx.InvalidURL, ValueError):
        return False
    if not decoded or sent.scheme != "https" or (sent.port is not None and not 1 <= sent.port <= 65535):
        return False
    return sent.raw_host.decode("ascii") == parts.hostname  # de host waar httpx echt mee verbindt


def token_for(url: str) -> str:
    """Het winkeladres als URL-veilige base64 zonder `=`: het stuk achter /api/images/."""
    return base64.urlsafe_b64encode(url.encode("utf-8")).decode("ascii").rstrip("=")


def url_for(token: Any) -> str | None:
    """Het adres achter een token, of None als het niet decodeert of geen adres van een winkel is dat de app mag ophalen (`safe_url`)."""
    if not isinstance(token, str) or not token:
        return None
    try:
        url = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    return url if safe_url(url) else None


# --- de tabel --------------------------------------------------------------------------------------


async def register(url: str, now: str | None = None) -> int:
    """Zet een adres in de tabel (of ververs `seen_at`) en geef het id."""
    now = now or _now()
    host: str = urlsplit(url).hostname or ""
    async with _connect() as conn:
        await conn.execute(
            "INSERT INTO image_cache (url, host, seen_at) VALUES (?, ?, ?) ON CONFLICT(url) DO UPDATE SET seen_at = excluded.seen_at",
            (url, host, now),
        )
        await conn.commit()
        async with conn.execute("SELECT id FROM image_cache WHERE url = ?", (url,)) as cursor:
            row: aiosqlite.Row | None = await cursor.fetchone()
    assert row is not None  # net geschreven
    return int(row[0])


async def lookup(url: str) -> dict[str, Any] | None:
    """De rij van een adres als dict, of None."""
    async with _connect() as conn, conn.execute("SELECT * FROM image_cache WHERE url = ?", (url,)) as cursor:
        row: aiosqlite.Row | None = await cursor.fetchone()
    return dict(row) if row else None


async def _rows() -> list[dict[str, Any]]:
    async with _connect() as conn, conn.execute("SELECT * FROM image_cache ORDER BY id") as cursor:
        return [dict(row) for row in await cursor.fetchall()]


async def stats(now: str | None = None) -> dict[str, Any]:
    """Hoeveel foto's er in de cache staan en hoeveel bytes dat is, en welke winkels nu in pauze staan (`pauses`)."""
    async with _connect() as conn, conn.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(bytes), 0) AS b FROM image_cache WHERE path IS NOT NULL"
    ) as cursor:
        row: aiosqlite.Row | None = await cursor.fetchone()
    assert row is not None  # een COUNT geeft altijd een rij
    return {"count": int(row["n"]), "bytes": int(row["b"]), "paused": await pauses(now)}


async def _record_failure(url: str, now: str, count: int, error: str) -> None:
    async with _connect() as conn:
        await conn.execute(
            "UPDATE image_cache SET attempts = attempts + ?, last_attempt_at = ?, error = ? WHERE url = ?",
            (count, now, error[:300], url),
        )
        await conn.commit()


async def _record_success(
    url: str,
    now: str,
    path: str,
    content_type: str,
    size: int,
    width: int,
    height: int,
    digest: str,
) -> None:
    async with _connect() as conn:
        await conn.execute(
            """
            UPDATE image_cache SET path = ?, content_type = ?, bytes = ?, width = ?, height = ?, digest = ?, fetched_at = ?, error = NULL
            WHERE url = ?
            """,
            (path, content_type, size, width, height, digest, now, url),
        )
        await conn.commit()


async def _forget_file(url: str) -> None:
    """Het bestand is weg (een opgeruimde map): de rij weer op 'nog op te halen'."""
    async with _connect() as conn:
        await conn.execute("UPDATE image_cache SET path = NULL, fetched_at = NULL WHERE url = ?", (url,))
        await conn.commit()


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


async def paused_until(host: str) -> str | None:
    value: str = await auth.get_setting(SETTING_PAUSED + host, "")
    return value or None


async def pauses(now: str | None = None) -> list[dict[str, str]]:
    """De hosts die nu in pauze staan, met de winkel en tot wanneer: `[{host, store, until}]`, de vroegste eerst. Voor de regel
    onder Instellingen > Systeem, zodat je ziet waarom er van een winkel even geen nieuwe foto's komen."""
    now = now or _now()
    await auth.ensure_db()
    async with auth.connect() as conn, conn.execute(
        "SELECT key, value FROM app_settings WHERE key LIKE ? AND value > ? ORDER BY value", (SETTING_PAUSED + "%", now)
    ) as cursor:
        rows: list[aiosqlite.Row] = list(await cursor.fetchall())
    found: list[dict[str, str]] = []
    for row in rows:
        host = str(row["key"])[len(SETTING_PAUSED):]
        store = _store_of_host(host)
        if store:
            found.append({"host": host, "store": store, "until": str(row["value"])})
    return found


SETTING_STALE_PAUSES_LIFTED = "image_cache_stale_pauses_lifted"


async def lift_stale_pauses() -> list[str]:
    """Hef eenmalig de pauze op van de foto's van Picnic. Tot 3.7.1 las de cache een 403 van de opslag van Picnic (Amazon S3,
    voor een foto die er niet is) als een blokkade en zette hij Picnic twaalf uur stil. Die pauze stond in de database en bleef
    na een update gewoon lopen, zonder dat je het zag: na de update naar 3.7.2 kwam er daardoor geen enkele receptfoto (#236).
    Geeft de hosts waarvan de pauze is opgeheven. Een echte blokkade zet een host bij het volgende verzoek opnieuw stil."""
    if await auth.get_setting(SETTING_STALE_PAUSES_LIFTED, ""):
        return []
    lifted: list[str] = []
    for pause in await pauses():
        if pause["store"] == "picnic":
            await auth.set_setting(SETTING_PAUSED + pause["host"], "")
            lifted.append(pause["host"])
    await auth.set_setting(SETTING_STALE_PAUSES_LIFTED, _now())
    return lifted


async def _pause(host: str, now: str) -> str:
    until: str = (datetime.fromisoformat(now) + timedelta(hours=PAUSE_HOURS)).isoformat(
        timespec="seconds"
    )
    await auth.set_setting(SETTING_PAUSED + host, until)
    return until


async def _paused(host: str, now: str) -> bool:
    until: str | None = await paused_until(host)
    return bool(until) and until > now


def _client(store: str, host: str) -> httpx.AsyncClient:
    """De client van een host, met de browserheaders van de winkel. Hij volgt zelf geen doorverwijzing: dat doet `_download`."""
    return http_client.client(f"image-cache:{host}", headers=HEADERS[store], timeout=TIMEOUT, follow_redirects=False)


async def _pace(host: str) -> None:
    """Wacht tot er genoeg tijd zit tussen dit verzoek en het vorige op dezelfde host (`PACE_SECONDS` plus jitter). Het reserveren
    van ons tijdstip heeft geen `await`, dus geen ander verzoek komt ertussen en er is geen lock nodig. Het slapen gebeurt zonder
    slot, zodat een wachtend verzoek niemand anders ophoudt. De rustpauze van de achtergrondtaak (2 tot 5 s) zit in `prefetch`."""
    gap: float = PACE_SECONDS + random.uniform(0, PACE_JITTER)
    start: float = max(_monotonic(), _PACE.get(host, float("-inf")) + gap)
    _PACE[host] = start
    wait: float = start - _monotonic()
    if wait > 0:
        await _wait(wait)


# --- ophalen ---------------------------------------------------------------------------------------


class _Refused(httpx.HTTPStatusError):
    """Een antwoord van de winkel dat geen 200 is. `missing` als de foto er niet is: een 404, of een 403 van een opslag als S3."""

    def __init__(self, message: str, response: httpx.Response, missing: bool) -> None:
        super().__init__(message, request=response.request, response=response)
        self.missing: bool = missing


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


async def _download(store: str, host: str, url: str) -> tuple[bytes, str]:
    """(inhoud, type) van de winkel. Een ValueError voor wat geen bruikbare afbeelding is, een `_Refused` met de status voor een
    antwoord dat geen 200 is. Een doorverwijzing volgen we zelf (hooguit `MAX_REDIRECTS`), en alleen naar een adres van een
    winkel: zo kan een CDN de app nooit naar een intern adres sturen."""
    client: httpx.AsyncClient = _client(store, host)
    for _hop in range(MAX_REDIRECTS + 1):
        async with client.stream("GET", url) as response:
            if response.status_code in (301, 302, 303, 307, 308):
                target: str = urljoin(url, str(response.headers.get("Location") or ""))
                if not safe_url(target):
                    raise ValueError(f"doorverwijzing naar een onbekende host ({urlsplit(target).hostname or '?'})")
                url = target
                continue
            return await _read_image(response)
    raise ValueError("te veel doorverwijzingen")


async def _missing_object(response: httpx.Response) -> bool:
    """Is dit antwoord de foutmelding van een opslag als S3 over een bestand dat er niet is (`_MISSING_OBJECT`)? We lezen hooguit
    `_ERROR_BODY_BYTES` van de inhoud, en alleen bij XML."""
    content_type: str = str(response.headers.get("Content-Type", "")).split(";")[0].strip().lower()
    if content_type not in ("application/xml", "text/xml"):
        return False
    head: bytes = b""
    async for chunk in response.aiter_bytes(1024):
        head += chunk
        if len(head) >= _ERROR_BODY_BYTES:
            break
    return bool(_MISSING_OBJECT.search(head[:_ERROR_BODY_BYTES]))


async def _read_image(response: httpx.Response) -> tuple[bytes, str]:
    """(inhoud, type) uit een antwoord van de winkel, met de grenzen op type en grootte. Een antwoord dat geen 200 is, wordt een
    `_Refused` met de status, en met `missing` als de foto er niet is: een 404, of een 403 van een opslag als S3."""
    if response.status_code != 200:
        bucket_missing: bool = response.status_code == 403 and await _missing_object(response)
        message: str = f"HTTP {response.status_code}" + (" (de foto bestaat niet)" if bucket_missing else "")
        raise _Refused(message, response, response.status_code == 404 or bucket_missing)
    content_type: str = str(response.headers.get("Content-Type", "")).split(";")[0].strip().lower()
    if not content_type.startswith("image/") or content_type == "image/svg+xml":
        raise ValueError(f"geen afbeelding ({content_type or 'zonder type'})")
    chunks: list[bytes] = []
    size: int = 0
    async for chunk in response.aiter_bytes(64 * 1024):
        size += len(chunk)
        if size > MAX_DOWNLOAD_BYTES:
            raise ValueError("afbeelding is te groot")
        chunks.append(chunk)
    return b"".join(chunks), content_type


def _store_file(url: str, data: bytes, content_type: str) -> tuple[str, str]:
    """Schrijf de inhoud naar images/<ab>/<sha256 van het adres>.<ext>. Geeft (pad, digest van de inhoud)."""
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


async def _fetch_once(url: str, now: str) -> dict[str, Any] | None:
    store: str | None = store_for(url)
    host: str = urlsplit(url).hostname or ""
    if not store or not safe_url(url) or await _paused(host, now):
        return None
    try:
        await _pace(host)
        async with _slots():
            data, content_type = await _download(store, host, url)
    except _Refused as exc:
        status: int = exc.response.status_code
        if status in (403, 429) and not exc.missing:  # de winkel weert ons: de hele host even met rust laten
            await _pause(host, now)
        await _record_failure(url, now, NOT_FOUND_COUNTS if exc.missing else 1, str(exc))  # een foto die er niet is, telt voor drie
        return None
    except (httpx.HTTPError, ValueError, OSError) as exc:
        await _record_failure(url, now, 1, str(exc) or exc.__class__.__name__)
        return None
    try:
        width, height = await asyncio.to_thread(_measure, data)  # Pillow: buiten de event loop
        path, digest = await asyncio.to_thread(_store_file, url, data, content_type)  # tot 10 MB naar schijf: ook buiten de loop
    except (ValueError, OSError) as exc:  # geen afbeelding, of de schijf is vol of alleen-lezen: niet meteen opnieuw proberen
        await _record_failure(url, now, 1, str(exc) or exc.__class__.__name__)
        return None
    await _record_success(url, now, path, content_type, len(data), width, height, digest)
    return await lookup(url)


def _on_disk(row: dict[str, Any] | None) -> bool:
    return bool(row and row.get("path") and (images_dir() / row["path"]).is_file())


async def _wait_for_other(url: str, running: asyncio.Event) -> dict[str, Any] | None:
    """Een ander verzoek haalt deze foto al op: wacht daar hooguit `WAIT_SECONDS` op en geef dan wat er staat."""
    try:
        async with asyncio.timeout(WAIT_SECONDS):
            await running.wait()
    except TimeoutError:
        pass
    found: dict[str, Any] | None = await lookup(url)
    return found if found and found.get("path") else None


async def fetch(url: str, *, now: str | None = None) -> dict[str, Any] | None:
    """De rij van een foto die op schijf staat, na hem zo nodig op te halen. None als dat niet kan: een onbekende host, een
    host in pauze, een adres dat na een mislukking nog wacht, of een mislukking nu. Vraagt een ander verzoek dezelfde foto
    al op, dan wacht dit verzoek hooguit `WAIT_SECONDS` op dat resultaat. Een hit leest alleen: geen schrijfactie per foto."""
    if not safe_url(url):
        return None
    now = now or _now()
    row: dict[str, Any] | None = await lookup(url)
    if _on_disk(row):
        return row
    if not row:
        await register(url, now)
        row = await lookup(url)
    if row and row.get("path"):
        if _on_disk(row):
            # Een ander verzoek heeft de foto net opgehaald, tussen onze eerste blik (nog geen rij) en het registreren. Dat pad
            # is goed: niet wissen, anders halen we de foto een tweede keer op.
            return row
        await _forget_file(url)  # het bestand is weg (een opgeruimde map): opnieuw ophalen
        row = await lookup(url)
    if not row or not due(row, now):
        return None
    # Tussen het opzoeken en het aanmaken van de Event staat geen `await`: geen ander verzoek kan er tussen komen.
    running: asyncio.Event | None = _INFLIGHT.get(url)
    if running is not None:
        return await _wait_for_other(url, running)
    running = _INFLIGHT[url] = asyncio.Event()
    try:
        # Nog een keer kijken: een ander verzoek kan de foto net hebben opgehaald, tussen onze eerste blik hierboven en het
        # aanmaken van de Event. Dan zag dit verzoek geen lopende download meer en zou het de foto een tweede keer ophalen.
        latest: dict[str, Any] | None = await lookup(url)
        if _on_disk(latest):
            return latest
        if not latest or not due(latest, now):
            return None
        return await _fetch_once(url, now)
    finally:
        _INFLIGHT.pop(url, None)
        running.set()


# --- de achtergrondtaak ----------------------------------------------------------------------------


def enabled() -> bool:
    """Of de achtergrondtaak foto's vooruit mag ophalen (`MEALIESHOPPER_IMAGE_PREFETCH`, standaard aan)."""
    return environ.get("MEALIESHOPPER_IMAGE_PREFETCH", "true").strip().lower() not in {"0", "false", "no", "off"}


async def collect_known(now: str | None = None) -> int:
    """Zet elk winkeladres uit de tabellen van de interface in `image_cache` en geef het aantal nieuwe adressen."""
    now = now or _now()
    added: int = 0
    async with _connect() as conn:
        async with conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'") as cursor:
            tables: set[str] = {row["name"] for row in await cursor.fetchall()}
        for table, column in KNOWN_IMAGE_COLUMNS:
            if table not in tables:
                continue
            async with conn.execute(f"PRAGMA table_info({table})") as cursor:
                columns: set[str] = {row["name"] for row in await cursor.fetchall()}
            if column not in columns:
                continue  # een database van vóór die kolom (de migratie volgt bij het eerste gebruik van die module)
            async with conn.execute(f"SELECT DISTINCT {column} FROM {table} WHERE {column} IS NOT NULL AND {column} != ''") as cursor:
                urls: set[str] = {row[0] for row in await cursor.fetchall()}
            for url in urls:
                if not safe_url(url):
                    continue
                cursor = await conn.execute(
                    "INSERT OR IGNORE INTO image_cache (url, host, seen_at) VALUES (?, ?, ?)", (url, urlsplit(url).hostname or "", now)
                )
                added += cursor.rowcount
        await conn.commit()
    return added


async def prefetch(*, limit: int = PREFETCH_LIMIT, deadline_seconds: float = PREFETCH_DEADLINE_SECONDS, now: str | None = None) -> dict[str, Any]:
    """Haal in een rustig tempo op wat nog ontbreekt, nieuwste adressen eerst: hooguit `limit` verzoeken en `deadline_seconds` lang.
    `skipped` zijn adressen die na een mislukking nog wachten of op een host in pauze staan, `paused` het aantal hosts in pauze.
    Loopt er al een ronde, dan geeft dit meteen `running`. Het slot gaat ook bij annuleren weer open."""
    now = now or _now()
    result: dict[str, Any] = {"fetched": 0, "failed": 0, "skipped": 0, "paused": 0}
    lock: asyncio.Lock = _prefetch_lock()
    if lock.locked():
        return {**result, "running": True}
    async with lock:  # een vrij slot pakken heeft geen wachtmoment: niemand komt tussen de controle en het pakken
        await collect_known(now)
        async with _connect() as conn, conn.execute(
            "SELECT * FROM image_cache WHERE path IS NULL ORDER BY seen_at DESC, id DESC"
        ) as cursor:
            rows: list[dict[str, Any]] = [dict(row) for row in await cursor.fetchall()]
        paused_hosts: set[str] = set()
        deadline: float = _monotonic() + deadline_seconds
        for row in rows:
            if result["fetched"] + result["failed"] >= limit or _monotonic() > deadline:
                break
            host: str = row["host"]
            if host in paused_hosts or await _paused(host, now):
                paused_hosts.add(host)
                result["skipped"] += 1
                continue
            if not due(row, now):
                result["skipped"] += 1
                continue
            if result["fetched"] + result["failed"]:
                await _wait(random.uniform(*BACKGROUND_PACE))  # rustig aan: de winkel merkt ons liever niet op
            if await fetch(row["url"], now=now):
                result["fetched"] += 1
            else:
                result["failed"] += 1
                if await _paused(host, now):
                    paused_hosts.add(host)
        result["paused"] = len(paused_hosts)
    return result


async def prefetch_nightly() -> dict[str, Any] | None:
    """Voor de nachtelijke taak (offers_schedule): de grote ronde, met een regel in het auditlog. None als de taak uitstaat of
    misging. Een fout laat de rest van de nachtelijke taak doorgaan."""
    if not enabled():
        return None
    try:
        result: dict[str, Any] = await prefetch()
    except Exception:  # noqa: BLE001 - de cache is een extraatje
        log.exception("Afbeeldingencache: de nachtelijke ronde mislukte")
        return None
    await audit.record(kind="task", service="images", action="prefetch", detail=result)
    return result


def _prefetch_running() -> bool:
    """Loopt de ronde van `prefetch_async` nog, in deze event loop? Een task van een eerdere loop (een test) telt niet mee."""
    task: asyncio.Task[None] | None = _PREFETCH_TASK
    return task is not None and not task.done() and task.get_loop() is asyncio.get_running_loop()


async def prefetch_async(limit: int = PREFETCH_AFTER_SYNC) -> None:
    """Een kleine ronde als achtergrondtask (`image-prefetch`), na een synchronisatie van bestellingen of bonnen: de nieuwe foto's
    staan er dan al voordat iemand ze opent. Loopt er al een ronde, dan niet nog een. Een task die bij het afsluiten wordt
    geannuleerd, is klaar: het slot van `prefetch` gaat open en de volgende keer start er gewoon weer een."""
    global _PREFETCH_TASK
    if not enabled() or _prefetch_running():
        return

    async def run() -> None:
        try:
            await prefetch(limit=limit, deadline_seconds=PREFETCH_AFTER_SYNC_SECONDS)
        except Exception:  # noqa: BLE001
            log.exception("Afbeeldingencache: de ronde na de synchronisatie mislukte")

    _PREFETCH_TASK = tasks.spawn("image-prefetch", run())
