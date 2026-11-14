"""Een groot bestand in stukken uploaden: een back-up of een prijsarchief.

Eén verzoek van 100 MB komt niet altijd aan. Wat voor de app staat (een reverse proxy, een tunnel of CDN zoals Cloudflare,
dat op de gratis versie hoogstens 100 MB doorlaat) kan zo'n verzoek weigeren of nooit doorgeven; de browser blijft dan
staan bij de megabytes die in de buffers passen en hoort nooit waarom. Daarom stuurt het scherm het bestand in stukken
van een paar MB, elk een eigen verzoek (static/upload.js). De server zet ze achter elkaar in één bestand in de datamap
en geeft het, als het compleet is, aan de module die het controleert (backup.py, price_archive.py).

    POST   <route>                    {"size": n}       -> {"token", "chunkBytes"}    begin; een te groot bestand: 413
    PUT    <route>/<token>?offset=n   (het stuk)        -> {"received"}                een stuk achter het vorige
    DELETE <route>/<token>                                                            de beheerder bedenkt zich
    POST   <route>/<token>/stage      {"size": n}       -> wat `/stage` geeft          compleet: controleren (de module)

Een stuk dat niet aansluit (`offset` is niet wat er al staat) krijgt 409 met wat er al staat; zo gaat het scherm na een
weggevallen antwoord verder waar de server is, zonder iets dubbel of verkeerd te plakken. Het afmaken (controleren)
doet de module zelf, met `take`: dat geeft het bestand alleen als het precies zo groot is als het scherm zei.

Het bestand heet `<prefix><token>.part` met het voorvoegsel van de module (`.restore-`, `.price-archive-`), zodat het
opruimen na een uur en de fabrieksreset het al kennen. Een nieuw voorvoegsel hoort ook in `factory_reset.STAGED_PREFIXES`;
`tests/test_factory_reset.py` faalt als het daar ontbreekt.

Een stuk past ruim in de standaardgrens van 16 MiB per verzoek (body_limits.py). Er geldt geen tijdslimiet op een stuk als
geheel, wel op de stilte: komt er READ_TIMEOUT_SECONDS niets binnen, dan geeft de server het stuk op met 408. Gaat de client
weg terwijl het stuk binnenkomt, dan annuleert Quart de route (keep_running.py) voordat er iets geschreven is.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets
import time
import weakref
from collections.abc import AsyncGenerator, AsyncIterable, AsyncIterator
from contextlib import aclosing
from pathlib import Path
from typing import Any

from quart import Quart, Response, jsonify, request
from werkzeug.exceptions import RequestTimeout

from . import auth, tasks
from .i18n import tr

CHUNK_BYTES: int = 4 * 1024 * 1024  # wat het scherm per verzoek stuurt: ver onder de grens van elke gangbare proxy
PART_SUFFIX: str = ".part"
STALE_SECONDS: int = 3600
READ_TIMEOUT_SECONDS: float = 30  # zo lang mag een stuk stil blijven voordat de server het opgeeft
TOKEN_PATTERN: re.Pattern[str] = re.compile(r"^[0-9a-f]{32}$")

MESSAGES: dict[str, tuple[str, str]] = {
    "too_large": ("Het bestand is groter dan {limit} MB.", "The file is larger than {limit} MB."),
    "empty": ("Het bestand is leeg.", "The file is empty."),
    "missing": (
        "De upload is niet meer beschikbaar (na een uur zonder nieuwe stukken ruimt de server hem op). Kies het bestand opnieuw.",
        "The upload is no longer available (the server removes it after an hour without new parts). Choose the file again.",
    ),
    "out_of_order": ("Dit stuk sluit niet aan op wat de server al heeft.", "This part does not follow on from what the server already has."),
    "chunk_too_large": ("Een stuk mag hoogstens {limit} MB zijn.", "A part may be at most {limit} MB."),
    "stalled": (
        "Dit stuk kwam niet (helemaal) aan: de verbinding viel weg of bleef te lang stil.",
        "This part did not (fully) arrive: the connection dropped or stayed silent for too long.",
    ),
    "incomplete": (
        "De upload is niet compleet: de server heeft {received} van {size} bytes. Kies het bestand opnieuw.",
        "The upload is not complete: the server has {received} of {size} bytes. Choose the file again.",
    ),
}

log: logging.Logger = logging.getLogger(__name__)

# Alleen om het schrijven: twee stukken voor dezelfde upload mogen elkaar niet inhalen. Een slot per event loop, zoals in
# http_client: elke test heeft een eigen loop. Het lezen van een stuk gebeurt ervoor, zonder slot.
_locks: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = weakref.WeakKeyDictionary()


def _lock() -> asyncio.Lock:
    loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
    lock: asyncio.Lock | None = _locks.get(loop)
    if lock is None:
        lock = _locks[loop] = asyncio.Lock()
    return lock


class UploadError(Exception):
    """Een melding voor de beheerder, als sleutel plus waarden; de tekst volgt pas bij het lezen, in de taal van de lezer."""

    def __init__(self, code: str, status: int = 400, **params: Any) -> None:
        super().__init__(code)
        self.key: str = code
        self.status: int = status
        self.params: dict[str, Any] = params

    def payload(self) -> dict[str, Any]:
        nl, en = MESSAGES[self.key]
        text: str = tr(nl, en)
        try:
            text = text.format(**self.params)
        except (KeyError, IndexError):
            pass
        body: dict[str, Any] = {"error": text, "code": f"upload_{self.key}"}
        if "received" in self.params:
            body["received"] = self.params["received"]
        return body


def _megabytes(limit: int) -> int:
    return limit // (1024 * 1024)


def part_path(prefix: str, token: str) -> Path:
    if not TOKEN_PATTERN.fullmatch(str(token or "")):  # niet `match`: "$" past ook voor een laatste regeleinde
        raise UploadError("missing", 404)
    return auth.data_dir() / f"{prefix}{token}{PART_SUFFIX}"


def remove_old_parts(prefix: str) -> None:
    """Een upload waar een uur geen stuk bij kwam, is verlaten (elk stuk zet de tijd van het bestand op nu)."""
    cutoff: float = time.time() - STALE_SECONDS
    for path in auth.data_dir().glob(f"{prefix}*{PART_SUFFIX}"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            continue


async def read_while_arriving(body: AsyncIterable[bytes], idle_seconds: float | None) -> AsyncGenerator[bytes, None]:
    """De stukken van `body`, met een grens op de stilte tussen twee stukken (408 daarna), niet op het hele lichaam.

    Een upload van 200 MB over een trage verbinding duurt langer dan `BODY_TIMEOUT`, en mag daar niet op stranden. Een
    upload die stilvalt, stopt wel: anders bleef de route er voor altijd op wachten. (`await request.get_data()` houdt
    zich aan `BODY_TIMEOUT` voor het geheel, `async for` over `request.body` kent geen grens.)
    """
    iterator: AsyncIterator[bytes] = aiter(body)
    chunk: bytes
    while True:
        try:
            chunk = await asyncio.wait_for(anext(iterator), idle_seconds)
        except StopAsyncIteration:
            return
        except TimeoutError:
            raise RequestTimeout() from None
        yield chunk


async def begin(prefix: str, size: Any, limit: int) -> dict[str, Any]:
    """Maakt een leeg bestand voor een upload van `size` bytes. De grens geldt meteen, niet pas na 100 MB."""
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise UploadError("empty")
    if size > limit:
        raise UploadError("too_large", 413, limit=_megabytes(limit))
    auth.data_dir().mkdir(parents=True, exist_ok=True)
    remove_old_parts(prefix)
    token: str = secrets.token_hex(16)
    path: Path = part_path(prefix, token)
    path.touch(mode=0o600, exist_ok=False)
    os.chmod(path, 0o600)
    return {"token": token, "chunkBytes": CHUNK_BYTES}


async def append(prefix: str, token: str, offset: Any, body: AsyncIterable[bytes], content_length: int | None, limit: int) -> dict[str, int]:
    """Zet één stuk achter het bestand. `offset` moet precies zijn wat er al staat; anders 409 met dat getal.

    Het stuk wordt eerst helemaal gelezen, zonder slot: een stuk dat onderweg blijft steken, mag de andere stukken niet
    laten wachten. Komt er READ_TIMEOUT_SECONDS niets binnen, dan geven we het stuk op met 408 en stuurt het scherm het
    opnieuw. Het schrijven (tot 4 MB) loopt in een thread, onder het slot.
    """
    path: Path = part_path(prefix, token)
    if content_length is not None and content_length > CHUNK_BYTES:
        raise UploadError("chunk_too_large", 413, limit=_megabytes(CHUNK_BYTES))
    if not path.exists():
        raise UploadError("missing", 404)
    data: bytes = await _read_chunk(body, token)
    async with _lock():
        writing: asyncio.Future[int] = asyncio.ensure_future(asyncio.to_thread(_write_chunk, path, offset, data, limit))
        try:
            received: int = await tasks.share(writing)  # niet `asyncio.shield`: die meldt een fout na de annulering ook zelf (3.15)
        except asyncio.CancelledError:
            # Een thread is niet te annuleren: hij schrijft door. Het slot blijft tot hij klaar is, zodat geen volgend stuk
            # tegelijk in hetzelfde bestand schrijft.
            await asyncio.wait({writing})
            if not writing.cancelled():
                writing.exception()  # opgehaald: een geweigerd stuk hoort bij een verzoek dat er niet meer is
            raise
    return {"received": received}


async def _read_chunk(body: AsyncIterable[bytes], token: str) -> bytes:
    data: bytearray = bytearray()
    started: float = time.monotonic()
    try:
        async with aclosing(read_while_arriving(body, READ_TIMEOUT_SECONDS)) as blocks:
            async for block in blocks:
                data += block
                if len(data) > CHUNK_BYTES:
                    raise UploadError("chunk_too_large", 413, limit=_megabytes(CHUNK_BYTES))
    except RequestTimeout:
        log.warning(
            "Upload %s: stuk afgebroken na %d bytes en %.0f s (de verbinding bleef %d s stil)",
            token[:8],
            len(data),
            time.monotonic() - started,
            READ_TIMEOUT_SECONDS,
        )
        raise UploadError("stalled", 408) from None
    return bytes(data)


def _write_chunk(path: Path, offset: Any, data: bytes, limit: int) -> int:
    """Schrijft `data` op `offset` als dat het einde van het bestand is, en geeft de nieuwe grootte. Voor `asyncio.to_thread`."""
    try:
        handle = path.open("r+b")
    except FileNotFoundError:
        raise UploadError("missing", 404) from None
    with handle:
        received: int = os.fstat(handle.fileno()).st_size
        if offset != received:
            raise UploadError("out_of_order", 409, received=received)
        if received + len(data) > limit:
            raise UploadError("too_large", 413, limit=_megabytes(limit))
        handle.seek(received)
        try:
            handle.write(data)
            handle.flush()
        except BaseException:
            handle.truncate(received)  # een half stuk blijft niet staan: het scherm stuurt het opnieuw
            raise
    return received + len(data)


async def take(prefix: str, token: str, size: Any) -> Path:
    """Het complete bestand, als het precies `size` bytes is. Wat niet compleet is, wordt weggegooid."""
    path: Path = part_path(prefix, token)
    async with _lock():
        try:
            received: int = path.stat().st_size
        except FileNotFoundError:
            raise UploadError("missing", 404) from None
        if received != size:
            path.unlink(missing_ok=True)
            raise UploadError("incomplete", 409, received=received, size=size)
    return path


async def discard(prefix: str, token: str) -> None:
    part_path(prefix, token).unlink(missing_ok=True)


def parse_offset(value: str | None) -> int | None:
    """De `offset` uit de query; iets anders dan een niet-negatief geheel getal is nooit gelijk aan wat er staat."""
    if value is None or not value.isdigit():
        return None
    return int(value)


def error_response(exc: UploadError) -> tuple[Response, int]:
    return jsonify(exc.payload()), exc.status


def register_routes(app: Quart, route: str, prefix: str, limit: int) -> None:
    """Begin, stukken en weggooien onder `route`. Het afmaken (`<route>/<token>/stage`) registreert de module zelf.

    Alleen de beheerder: `route` valt onder een voorvoegsel in auth.ADMIN_PREFIXES (/api/backup, /api/price-archive).
    """
    name: str = route.strip("/").replace("/", "_").replace("-", "_")

    async def start() -> Response | tuple[Response, int]:
        body: Any = await request.get_json(silent=True)
        size: Any = body.get("size") if isinstance(body, dict) else None  # geen object: zonder size, zoals `{}`
        try:
            return jsonify(await begin(prefix, size, limit))
        except UploadError as exc:
            return error_response(exc)

    async def put(token: str) -> Response | tuple[Response, int]:
        try:
            return jsonify(await append(prefix, token, parse_offset(request.args.get("offset")), request.body, request.content_length, limit))
        except UploadError as exc:
            return error_response(exc)

    async def remove(token: str) -> Response | tuple[Response, int]:
        try:
            await discard(prefix, token)
        except UploadError as exc:
            return error_response(exc)
        return jsonify({"status": "ok"})

    app.add_url_rule(route, f"{name}_begin", start, methods=["POST"])
    app.add_url_rule(f"{route}/<token>", f"{name}_append", put, methods=["PUT"])
    app.add_url_rule(f"{route}/<token>", f"{name}_discard", remove, methods=["DELETE"])
