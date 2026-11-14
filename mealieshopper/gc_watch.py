"""Meet elke ronde van de garbage collector van Python, met de generatie, de duur en de thread waarin hij liep.

Een ronde van de GC loopt in de thread die toevallig een object aanmaakt als de teller over de drempel gaat, en houdt de GIL
vast tot hij klaar is. Een volle ronde (generatie 2) loopt langs elk object dat de GC volgt. Zo'n ronde in een rekenthread houdt
de event loop net zo goed tegen als een lange aanroep in C, en op de stack is hij niet te zien (de GC heeft geen eigen regel),
dus deze module meet hem zelf, met `gc.callbacks`.

Gemeten op de volumedatabase (Python 3.12, met drie processen die elk een kern bezet hielden): met de catalogi van vroeger volgde
de GC zo'n 650.000 objecten, en een koude bouw van de catalogus van AH gaf zes tot acht volle rondes van 100 tot 210 ms. Nu houdt
de catalogus bijna niets meer vast dat de GC volgt: een product en een regel van de lijnindex zijn gewone tuples van tekst
(`prices._Known`, `line_offers.Line`), die CPython na de eerste ronde overslaat, en de memo's van de namen zijn gewone dicts
(`name_keys.TextMemo`). Met alle catalogi volgt de GC zo'n 120.000 objecten en duurt een volle ronde 11 ms (3.12) of 25 ms (3.15).
Tijdens een bouw komen er tijdelijk objecten bij: de regels van de bon als `sqlite3.Row` en de tuples van de producten tot een
ronde ze loslaat. De rest van de rijen komt als gewone tuples (`prices._plain_rows`), en een rekenthread laat de rijen los zodra
de bouw klaar is (`compute.py`). Een koude bouw van AH heeft er bij het begin van een volle ronde hooguit zo'n 50.000 bij
(`tests/test_volume.py` bewaakt dat), en onder dezelfde belasting duurt de langste volle ronde tijdens vijf koude bouwen 29 ms
(3.12) of 37 ms (3.15). Onder hypercorn, waar twee rekenthreads tegelijk bouwen en het verkeer doorloopt, duurde de langste
ronde onder dezelfde belasting 53 ms (3.12) of 59 ms (3.15).
Bevriezen (`gc.freeze`) is geprobeerd en weer weggehaald: een verbinding die bij het bevriezen open stond, bleef na het sluiten
voor altijd bewaard.

`GcWatch.start()` hangt de meting aan, `stop()` haalt hem weg. Rondes van `keep` seconden of langer worden bewaard en aan
`on_collection` gegeven (in de thread van de GC, dus kort houden). De telemetrie gebruikt dit als `MEALIESHOPPER_TELEMETRY_GC`
aan staat, de volumetests altijd.
"""

from __future__ import annotations

import gc
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

GC_SECONDS: float = 0.02  # een ronde van zo lang of langer wordt bewaard
KEPT: int = 100


@dataclass(frozen=True)
class Collection:
    """Een ronde van de GC: de generatie, hoe lang hij duurde, de thread en hoeveel objecten hij opruimde."""

    generation: int
    seconds: float
    thread: str
    collected: int

    def describe(self) -> str:
        return f"GC generatie {self.generation} {round(self.seconds * 1000)} ms in {self.thread}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "generation": self.generation,
            "ms": round(self.seconds * 1000, 1),
            "thread": self.thread,
            "collected": self.collected,
        }


class GcWatch:
    """Meet de rondes van de GC zolang hij aan staat. `recent` houdt de laatste `KEPT` lange rondes."""

    def __init__(
        self,
        keep: float = GC_SECONDS,
        on_collection: Callable[[Collection], None] | None = None,
    ) -> None:
        self._keep: float = keep
        self._on_collection: Callable[[Collection], None] | None = on_collection
        self._started: dict[
            int, float
        ] = {}  # per thread: wanneer de lopende ronde begon
        self.recent: deque[Collection] = deque(maxlen=KEPT)

    def start(self) -> None:
        if self._callback not in gc.callbacks:
            gc.callbacks.append(self._callback)

    def stop(self) -> None:
        if self._callback in gc.callbacks:
            gc.callbacks.remove(self._callback)

    def drain(self) -> list[Collection]:
        """De lange rondes sinds de vorige aanroep."""
        found: list[Collection] = list(self.recent)
        self.recent.clear()
        return found

    def _callback(self, phase: str, info: dict[str, Any]) -> None:
        """Loopt in de thread van de GC, aan het begin en het eind van elke ronde."""
        ident: int = threading.get_ident()
        if phase == "start":
            self._started[ident] = time.perf_counter()
            return
        started: float | None = self._started.pop(ident, None)
        if started is None:  # de meting ging aan tijdens deze ronde
            return
        seconds: float = time.perf_counter() - started
        if seconds < self._keep:
            return
        found: Collection = Collection(
            int(info.get("generation", -1)),
            seconds,
            threading.current_thread().name,
            int(info.get("collected", 0)),
        )
        self.recent.append(found)
        if self._on_collection is not None:
            self._on_collection(found)
