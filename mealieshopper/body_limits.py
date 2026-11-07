"""Grenzen op het lichaam van een aanvraag: 16 MiB voor elke route, een ruimere grens voor de paar die meer nodig hebben.

Quart legt de grens vast bij het maken van het verzoek (`Body` krijgt `MAX_CONTENT_LENGTH` mee), nog voor de route
bekend is. `request.max_content_length` aanpassen in een route geldt alleen voor formulieren, niet voor `get_json` of
`request.body`. Daarom kiest `SizedRequest` de grens zelf, op het pad. Een lichaam boven de grens geeft 413 zodra de route
het leest, en wordt daarvoor niet in het geheugen bewaard.

Wat de routes legitiem binnenkrijgen (de inventaris bij 4.0.0): JSON van hooguit enkele honderden kB (inloggen, OAuth,
koppelen, het weekmenu, het mandje), een avatar tot 256 kB (`profile.MAX_AVATAR_BYTES`), een MCP-bericht tot 1 MB
(`mcp_http.MAX_BODY`). Meer hebben alleen:
- het klaarzetten van een back-up: tot `backup.MAX_STAGE_BYTES`, met 1 MiB marge,
- de prijsimport: tot `price_import.MAX_TEXT_CHARS` (5 miljoen) tekens tekst in een JSON-string. Een teken kan daar tot
  6 bytes worden (`\\u0001`), dus 32 MiB.
"""

from typing import Any

from quart.wrappers import Request

from .backup import MAX_STAGE_BYTES

MIB: int = 1024 * 1024
DEFAULT_MAX_BODY: int = 16 * MIB
LARGE_BODIES: dict[str, int] = {
    "/api/backup/stage": MAX_STAGE_BYTES + MIB,
    "/api/prices/import": 32 * MIB,
}


class SizedRequest(Request):
    """Een verzoek met de grens van `LARGE_BODIES` voor zijn pad, anders die van `MAX_CONTENT_LENGTH`."""

    def __init__(self, method: str, scheme: str, path: str, *args: Any, max_content_length: int | None = None, **kwargs: Any) -> None:
        limit: int | None = LARGE_BODIES.get(path, max_content_length)
        super().__init__(method, scheme, path, *args, max_content_length=limit, **kwargs)
        if path in LARGE_BODIES:
            self.max_content_length = limit  # ook voor formulieren, die de grens via deze eigenschap lezen
