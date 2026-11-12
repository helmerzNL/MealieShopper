"""Antwoorden met tekst gaan met gzip naar een browser die dat aankan.

De lijst met aanbiedingen van AH is 3,7 MB aan JSON, de scripts samen 1,2 MB en de stijlen 0,3 MB. Tekst wordt met gzip vijf
tot vijftien keer kleiner (de lijst: 254 KB). Een reverse proxy doet dat vaak alleen voor HTML (nginx, tenzij `gzip_types`
is uitgebreid), en een installatie zonder proxy deed het helemaal niet. Daarom verpakt de app het zelf: een antwoord dat al
een `Content-Encoding` heeft, laat een proxy met rust.

Verpakt wordt een antwoord met status 200 en een tekstsoort uit `COMPRESSIBLE`, van minstens `MIN_BYTES`, als de browser
`gzip` accepteert en niet om een deel van het bestand vraagt (`Range`). Een antwoord zonder bekende lengte is een stroom en
blijft een stroom. Plaatjes, lettertypen en back-ups zijn al verpakt en blijven zoals ze zijn.

De ETag blijft dezelfde: de routes vergelijken `If-None-Match` met hun eigen ETag (een 304 voor `/api/offers` en voor de
statische bestanden), en `Vary: Accept-Encoding` vertelt een cache dat het antwoord per codering verschilt.

Heeft het antwoord een ETag, dan bewaart `CompressedCache` de verpakte bytes onder het pad en die ETag: dezelfde lijst of
hetzelfde script wordt een keer verpakt, niet bij elke aanvraag. Het verpakken zelf loopt in een thread, buiten de event loop.
"""

from __future__ import annotations

import asyncio
import gzip
from collections import OrderedDict

from quart import Quart, Response, request

MIN_BYTES: int = 1024  # daaronder past het antwoord in een pakket en kost verpakken meer dan het oplevert
LEVEL: int = 6
CACHE_BYTES: int = 32 * 1024 * 1024
COMPRESSIBLE: frozenset[str] = frozenset(
    {
        "application/javascript",
        "application/json",
        "application/manifest+json",
        "application/xml",
        "image/svg+xml",
        "text/css",
        "text/html",
        "text/javascript",
        "text/plain",
        "text/xml",
    }
)


class CompressedCache:
    """De verpakte bytes per sleutel, tot `limit` bytes samen: de sleutel die het langst niet gebruikt is gaat er eerst uit."""

    def __init__(self, limit: int = CACHE_BYTES) -> None:
        self.limit: int = limit
        self.size: int = 0
        self._entries: OrderedDict[tuple[str, str], bytes] = OrderedDict()

    def get(self, key: tuple[str, str]) -> bytes | None:
        found: bytes | None = self._entries.get(key)
        if found is not None:
            self._entries.move_to_end(key)
        return found

    def put(self, key: tuple[str, str], value: bytes) -> None:
        if len(value) > self.limit:
            return
        old: bytes | None = self._entries.pop(key, None)
        self.size += len(value) - (len(old) if old is not None else 0)
        self._entries[key] = value
        while self.size > self.limit:
            _, dropped = self._entries.popitem(last=False)
            self.size -= len(dropped)


def _eligible(response: Response) -> bool:
    """Is dit een antwoord met tekst dat verpakt mag worden, los van wat de browser accepteert?"""
    length: int | None = response.content_length
    return (
        response.status_code == 200
        and response.mimetype in COMPRESSIBLE
        and "Content-Encoding" not in response.headers
        and length is not None  # zonder lengte is het een stroom
        and length >= MIN_BYTES
    )


def _accepts_gzip() -> bool:
    return request.method == "GET" and "Range" not in request.headers and request.accept_encodings.quality("gzip") > 0


async def _compressed(cache: CompressedCache, response: Response) -> bytes:
    etag: str | None = response.headers.get("ETag")
    key: tuple[str, str] | None = (request.path, etag) if etag else None
    found: bytes | None = cache.get(key) if key else None
    if found is not None:
        return found
    body: bytes = await response.get_data()
    packed: bytes = await asyncio.to_thread(gzip.compress, body, LEVEL, mtime=0)
    if key:
        cache.put(key, packed)
    return packed


def register(app: Quart) -> CompressedCache:
    """Hangt het verpakken achter elke route van `app`. Geeft de cache van deze app (voor de tests)."""
    cache: CompressedCache = CompressedCache()

    @app.after_request
    async def compress(response: Response) -> Response:
        if not _eligible(response):
            return response
        response.vary.add("Accept-Encoding")
        if not _accepts_gzip():
            return response
        packed: bytes = await _compressed(cache, response)
        response.set_data(packed)
        response.headers["Content-Encoding"] = "gzip"
        response.headers["Content-Length"] = str(len(packed))
        response.headers.pop("Accept-Ranges", None)  # een deel van het verpakte bestand is niets waard
        return response

    return cache
