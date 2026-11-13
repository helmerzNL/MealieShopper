"""De laatste lijst van een route per sleutel, zolang de ETag niet verandert, en een enkele berekening voor gelijktijdige aanvragen.

`/api/offers` rekent bij elke aanvraag het prijsoordeel uit (`prices.annotate`): bij AH met honderden aanbiedingen een flink deel
van een seconde rekenwerk. De browser bewaart de lijst al met een ETag, maar een tweede apparaat, een lege browsercache of drie
aanvragen tegelijk bij het openen van de pagina rekenden alles opnieuw uit. Deze cache bewaart per sleutel (winkel en week) de
laatste lijst met zijn ETag. Is de ETag gelijk, dan is de lijst dezelfde die de browser met een 304 zou houden: de cache is dus
nooit verser of ouder dan wat de browser al deed. Vragen meer aanvragen tegelijk om dezelfde sleutel en ETag, dan rekent er een,
en wachten de andere op die uitkomst.

De cache bewaart wat `load()` teruggeeft, ongewijzigd: voor /api/offers de bytes van de JSON (`app.json_bytes`), zodat een
tweede aanvraag de lijst niet opnieuw naar JSON omzet. Een fout wordt niet bewaard: de volgende aanvraag probeert het opnieuw.
Er staan hooguit `size` sleutels in, de oudste gaat er eerst uit.
De cache hoort bij een app (`create_app`), zodat tests elkaar niets meegeven.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Hashable
from typing import Any


class ResponseCache:
    def __init__(self, size: int = 16) -> None:
        self.size: int = size
        self._entries: OrderedDict[Hashable, tuple[str, Any]] = OrderedDict()
        self._running: dict[tuple[Hashable, str], asyncio.Task[Any]] = {}

    async def get(
        self, key: Hashable, etag: str, load: Callable[[], Awaitable[Any]]
    ) -> Any:
        """De bewaarde uitkomst bij `key` als zijn ETag `etag` is, anders `load()` (een keer, ook bij gelijktijdige aanvragen)."""
        found: tuple[str, Any] | None = self._entries.get(key)
        if found is not None and found[0] == etag:
            self._entries.move_to_end(key)
            return found[1]
        running: asyncio.Task[Any] | None = self._running.get((key, etag))
        if running is None:
            running = asyncio.ensure_future(self._load(key, etag, load))
            self._running[(key, etag)] = running
            running.add_done_callback(lambda _: self._running.pop((key, etag), None))
        # shield: gaat een van de wachtende aanvragen weg (de browser sluit), dan rekent de taak door voor de andere.
        return await asyncio.shield(running)

    async def _load(
        self, key: Hashable, etag: str, load: Callable[[], Awaitable[Any]]
    ) -> Any:
        value: Any = await load()
        self._entries[key] = (etag, value)
        self._entries.move_to_end(key)
        while len(self._entries) > self.size:
            self._entries.popitem(last=False)
        return value

    def clear(self) -> None:
        self._entries.clear()
