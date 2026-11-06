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
opruimen na een uur en de fabrieksreset het al kennen.
"""
from __future__ import annotations

import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request

from . import auth
from .i18n import tr

CHUNK_BYTES = 4 * 1024 * 1024  # wat het scherm per verzoek stuurt: ver onder de grens van elke gangbare proxy
READ_BYTES = 1024 * 1024
PART_SUFFIX = ".part"
STALE_SECONDS = 3600
TOKEN_PATTERN = re.compile(r"^[0-9a-f]{32}$")

MESSAGES: dict[str, tuple[str, str]] = {
    "too_large": ("Het bestand is groter dan {limit} MB.", "The file is larger than {limit} MB."),
    "empty": ("Het bestand is leeg.", "The file is empty."),
    "missing": (
        "De upload is niet meer beschikbaar (na een uur zonder nieuwe stukken ruimt de server hem op). Kies het bestand opnieuw.",
        "The upload is no longer available (the server removes it after an hour without new parts). Choose the file again.",
    ),
    "out_of_order": ("Dit stuk sluit niet aan op wat de server al heeft.", "This part does not follow on from what the server already has."),
    "chunk_too_large": ("Een stuk mag hoogstens {limit} MB zijn.", "A part may be at most {limit} MB."),
    "incomplete": (
        "De upload is niet compleet: de server heeft {received} van {size} bytes. Kies het bestand opnieuw.",
        "The upload is not complete: the server has {received} of {size} bytes. Choose the file again.",
    ),
}

_LOCK = threading.Lock()  # twee stukken voor dezelfde upload tegelijk mogen elkaar niet inhalen


class UploadError(Exception):
    """Een melding voor de beheerder, als sleutel plus waarden; de tekst volgt pas bij het lezen, in de taal van de lezer."""

    def __init__(self, code: str, status: int = 400, **params: Any):
        super().__init__(code)
        self.key = code
        self.status = status
        self.params = params

    def payload(self) -> dict[str, Any]:
        nl, en = MESSAGES[self.key]
        text = tr(nl, en)
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
    if not TOKEN_PATTERN.match(str(token or "")):
        raise UploadError("missing", 404)
    return auth.data_dir() / f"{prefix}{token}{PART_SUFFIX}"


def remove_old_parts(prefix: str) -> None:
    """Een upload waar een uur geen stuk bij kwam, is verlaten (elk stuk zet de tijd van het bestand op nu)."""
    cutoff = time.time() - STALE_SECONDS
    for path in auth.data_dir().glob(f"{prefix}*{PART_SUFFIX}"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            continue


def begin(prefix: str, size: Any, limit: int) -> dict[str, Any]:
    """Maakt een leeg bestand voor een upload van `size` bytes. De grens geldt meteen, niet pas na 100 MB."""
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        raise UploadError("empty")
    if size == 0:
        raise UploadError("empty")
    if size > limit:
        raise UploadError("too_large", 413, limit=_megabytes(limit))
    auth.data_dir().mkdir(parents=True, exist_ok=True)
    remove_old_parts(prefix)
    token = secrets.token_hex(16)
    path = part_path(prefix, token)
    path.touch(mode=0o600, exist_ok=False)
    os.chmod(path, 0o600)
    return {"token": token, "chunkBytes": CHUNK_BYTES}


def append(prefix: str, token: str, offset: Any, stream: Any, content_length: int | None, limit: int) -> dict[str, int]:
    """Zet één stuk achter het bestand. `offset` moet precies zijn wat er al staat; anders 409 met dat getal."""
    path = part_path(prefix, token)
    if content_length is not None and content_length > CHUNK_BYTES:
        raise UploadError("chunk_too_large", 413, limit=_megabytes(CHUNK_BYTES))
    with _LOCK:
        try:
            handle = path.open("r+b")
        except FileNotFoundError:
            raise UploadError("missing", 404) from None
        with handle:
            received = os.fstat(handle.fileno()).st_size
            if offset != received:
                raise UploadError("out_of_order", 409, received=received)
            handle.seek(received)
            written = 0
            try:
                while True:
                    block = stream.read(READ_BYTES)
                    if not block:
                        break
                    written += len(block)
                    if written > CHUNK_BYTES:
                        raise UploadError("chunk_too_large", 413, limit=_megabytes(CHUNK_BYTES))
                    if received + written > limit:
                        raise UploadError("too_large", 413, limit=_megabytes(limit))
                    handle.write(block)
            except BaseException:
                handle.truncate(received)  # een half stuk blijft niet staan: het scherm stuurt het opnieuw
                raise
    return {"received": received + written}


def take(prefix: str, token: str, size: Any) -> Path:
    """Het complete bestand, als het precies `size` bytes is. Wat niet compleet is, wordt weggegooid."""
    path = part_path(prefix, token)
    with _LOCK:
        try:
            received = path.stat().st_size
        except FileNotFoundError:
            raise UploadError("missing", 404) from None
        if received != size:
            path.unlink(missing_ok=True)
            raise UploadError("incomplete", 409, received=received, size=size)
    return path


def discard(prefix: str, token: str) -> None:
    part_path(prefix, token).unlink(missing_ok=True)


def parse_offset(value: str | None) -> int | None:
    """De `offset` uit de query; iets anders dan een niet-negatief geheel getal is nooit gelijk aan wat er staat."""
    if value is None or not value.isdigit():
        return None
    return int(value)


def error_response(exc: UploadError):
    return jsonify(exc.payload()), exc.status


def register_routes(app: Flask, route: str, prefix: str, limit: int) -> None:
    """Begin, stukken en weggooien onder `route`. Het afmaken (`<route>/<token>/stage`) registreert de module zelf.

    Alleen de beheerder: `route` valt onder een voorvoegsel in auth.ADMIN_PREFIXES (/api/backup, /api/price-archive).
    """
    name = route.strip("/").replace("/", "_").replace("-", "_")

    def start():
        body = request.get_json(silent=True) or {}
        try:
            return jsonify(begin(prefix, body.get("size"), limit))
        except UploadError as exc:
            return error_response(exc)

    def put(token: str):
        try:
            return jsonify(append(prefix, token, parse_offset(request.args.get("offset")), request.stream, request.content_length, limit))
        except UploadError as exc:
            return error_response(exc)

    def remove(token: str):
        try:
            discard(prefix, token)
        except UploadError as exc:
            return error_response(exc)
        return jsonify({"status": "ok"})

    app.add_url_rule(route, f"{name}_begin", start, methods=["POST"])
    app.add_url_rule(f"{route}/<token>", f"{name}_append", put, methods=["PUT"])
    app.add_url_rule(f"{route}/<token>", f"{name}_discard", remove, methods=["DELETE"])
