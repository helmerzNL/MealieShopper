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
Het terugzetten van een back-up startte 24 tot 26 volle rondes per keer (3.15, 2026-11-14), omdat de GC elke rij van het bestand
volgde. Sinds `backup.Rows` geen een. Wat een volle ronde kost, volgt uit het aantal objecten dat hij langsloopt: Python 3.15 geeft
dat aan de callback (`candidates`), en `describe` noemt het.
Het grootste deel van wat een volle ronde langsloopt, is van de start: 110.000 objecten na de imports (functies en dicts van
Quart, werkzeug, cryptography, httpx en de app zelf), 127.000 als de app draait. Die blijven het hele proces bestaan, dus de app
zet ze een keer na de start in de vaste generatie (`freeze_after_start`, voor de eerste aanvraag). Daarna loopt een volle ronde
alleen langs wat sindsdien bij kwam: in de nachtelijke ronde 9 tot 17 ms in plaats van 33 tot 55 ms (3.15, 2026-11-14), op
een apparaat dat vijf tot acht keer trager is het verschil tussen ongeveer 100 en 300 tot 450 ms stilstand.
Bevriezen tijdens een bouw (taak 9b) is weer weggehaald: een verbinding die op dat moment net gesloten was, zat nog in een dode
kring en bleef voor altijd bewaard. `freeze_after_start` ruimt daarom eerst alles op (`gc.collect()`) en bevriest voordat de
server een verbinding aanneemt. Wat bij de start leeft en later in een kring vrijkomt, ruimt de GC niet meer op: dat is
eenmalig en begrensd. Wat na de start ontstaat, ruimt hij gewoon op (`tests/test_gc_watch.py`).

`GcWatch.start()` hangt de meting aan, `stop()` haalt hem weg. Rondes van `keep` seconden of langer worden bewaard en aan
`on_collection` gegeven (in de thread van de GC, dus kort houden). De telemetrie gebruikt dit als `MEALIESHOPPER_TELEMETRY_GC`
aan staat, de volumetests altijd.
"""

from __future__ import annotations

import gc
import os
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

GC_SECONDS: float = 0.02  # een ronde van zo lang of langer wordt bewaard
KEPT: int = 100
FREEZE_ENV: str = "MEALIESHOPPER_GC_FREEZE"
OFF: frozenset[str] = frozenset({"0", "false", "no", "off"})


def freeze_wanted(environ: Mapping[str, str] = os.environ) -> bool:
    """Of de app na de start bevriest (`MEALIESHOPPER_GC_FREEZE`, standaard aan). In de tests standaard uit
    (`MEALIESHOPPER_TESTING`): die maken veel apps in een proces, en een test die het nodig heeft, zet het zelf aan."""
    value: str | None = environ.get(FREEZE_ENV)
    if value is None:
        return not environ.get("MEALIESHOPPER_TESTING")
    return value.strip().lower() not in OFF


def freeze_after_start() -> int:
    """Ruimt eerst alles op wat al dood is en zet dan wat leeft in de vaste generatie (`gc.freeze`), zodat een volle ronde daarna
    alleen langs nieuwe objecten loopt. Een keer, na de start en voor de eerste aanvraag: er is dan nog geen verbinding van de
    server. Geeft het aantal bevroren objecten."""
    gc.collect()
    gc.freeze()
    return gc.get_freeze_count()


@dataclass(frozen=True)
class Collection:
    """Een ronde van de GC: de generatie, hoe lang hij duurde, de thread, hoeveel objecten hij opruimde en (vanaf 3.15) langs
    hoeveel objecten hij liep (`candidates`, 0 als Python het niet zegt)."""

    generation: int
    seconds: float
    thread: str
    collected: int
    candidates: int = 0

    def describe(self) -> str:
        line: str = f"GC generatie {self.generation} {round(self.seconds * 1000)} ms in {self.thread}"
        if self.candidates:  # wat een volle ronde kost, volgt uit dit aantal: zo is te zien of de app of de tests groeiden
            line += ", " + f"{self.candidates:,}".replace(",", ".") + " objecten"
        return line

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
        self._started: dict[int, float] = {}  # per thread: wanneer de lopende ronde begon
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
            int(info.get("candidates", 0)),
        )
        self.recent.append(found)
        if self._on_collection is not None:
            self._on_collection(found)
