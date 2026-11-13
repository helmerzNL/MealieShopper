"""Meet elke ronde van de garbage collector van Python, met de generatie, de duur en de thread waarin hij liep, en houdt de
catalogi buiten die rondes (`set_aside`).

Een ronde van de GC loopt in de thread die toevallig een object aanmaakt als de teller over de drempel gaat, en houdt de GIL
vast tot hij klaar is. Een volle ronde (generatie 2) loopt langs elk object dat de GC volgt, en dat zijn er met de catalogi van
de volumedatabase zo'n 650.000 (`gc.collect()` duurt dan 175 ms): zo'n ronde in een rekenthread houdt de event loop net zo goed tegen als een lange aanroep in C. Op de stack
is hij niet te zien (de GC heeft geen eigen regel), dus deze module meet hem zelf, met `gc.callbacks`.

Gemeten op de volumedatabase (Python 3.12, met drie processen die elk een kern bezet hielden): een koude bouw van de catalogus
van AH gaf zes tot acht volle rondes van 100 tot 210 ms, die niets opruimden (de rijen en de catalogus leven nog). Python 3.15
heeft dezelfde drie generaties, dus productie ook. `set_aside` haalt wat er al leeft uit die rondes (`gc.freeze`): een volle
ronde loopt dan alleen nog langs wat er sinds de laatste keer bij kwam.

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


def set_aside() -> None:
    """Haalt wat nu leeft uit de volgende rondes van de GC (`gc.freeze`). De aanroeper is de bouw van een catalogus, ervoor en
    erna: ervoor gaan de app en de andere catalogi opzij, erna de nieuwe catalogus.

    Een bevroren object wordt nog steeds opgeruimd zodra niemand het meer gebruikt (de referentietelling), alleen een kring van
    objecten die naar elkaar wijzen niet meer. Daarom eerst een ronde over de twee jongste generaties (`gc.collect(1)`, een paar
    milliseconden): wat daar al afval is, gaat weg en wordt niet bevroren. Gemeten met dertig koude bouwen achter elkaar: het
    aantal bevroren objecten bleef gelijk, en `gc.unfreeze()` met een volle ronde ruimde daarna niets op.
    """
    gc.collect(1)
    gc.freeze()


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
