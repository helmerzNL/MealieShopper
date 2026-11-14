"""Een bewaker om elke event loop in de tests: een test die de event loop van de app langer dan de limiet blokkeert, valt op.

`install()` verpakt `BaseEventLoop.run_forever`, en daar komen alle manieren waarop de tests een loop starten samen:
pytest-asyncio, `unittest.IsolatedAsyncioTestCase` en `asyncio.run` (`helpers.run` en `helpers.run_aside`). Per aanroep
draait een `loop_guard.LoopGuard` mee, in een eigen thread. De stilstanden komen in een lijst die de fixture in `conftest.py`
na elke test leegt met `drain()`.

Omgeving, gelezen bij het importeren:

- `MEALIESHOPPER_LOOP_GUARD`: `enforce` (standaard), `report` of `off`. `enforce` laat de test falen, `report` somt de
  stilstanden aan het eind van de run op en laat geen test falen (handig om een lijst te maken), `off` zet de bewaker uit.
- `MEALIESHOPPER_LOOP_GUARD_MS`: de limiet in milliseconden, standaard 100.

Alleen een stilstand met een regel van de app op de stack telt (`Stall.in_app`): een `time.sleep` in de test zelf niet. Een test
die de loop met opzet blokkeert, krijgt `@pytest.mark.may_block("reden")`.
"""

from __future__ import annotations

import asyncio
import functools
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from mealieshopper import gc_watch, loop_guard

MODES: tuple[str, ...] = ("report", "enforce", "off")
MODE: str = os.environ.get("MEALIESHOPPER_LOOP_GUARD", "enforce")
LIMIT: float = float(os.environ.get("MEALIESHOPPER_LOOP_GUARD_MS", "100")) / 1000
if MODE not in MODES:
    raise ValueError(f"MEALIESHOPPER_LOOP_GUARD moet een van {MODES} zijn, niet {MODE!r}")

GUARD_ATTRIBUTE: str = "_loop_watch_guard"
WRAPPED_ATTRIBUTE: str = "_loop_watch_wrapped"

_stalls: list[loop_guard.Stall] = []
_lock: threading.Lock = threading.Lock()


@dataclass(frozen=True)
class Reported:
    """Een stilstand in `report`-modus, met de test waarin hij viel."""

    test: str
    stall: loop_guard.Stall


_reported: list[Reported] = []
# De lange rondes van de GC: een volle ronde houdt de GIL vast, dus ook de loop. `quiet` in test_volume.py noemt ze.
GC: gc_watch.GcWatch = gc_watch.GcWatch()


def _collect(stall: loop_guard.Stall) -> None:
    """Loopt in een bewakerthread, van welke loop ook."""
    with _lock:
        _stalls.append(stall)


def drain() -> list[loop_guard.Stall]:
    """De stilstanden sinds de vorige aanroep, allemaal (ook die zonder regel van de app). Veilig vanuit elke thread."""
    with _lock:
        found: list[loop_guard.Stall] = list(_stalls)
        _stalls.clear()
    return found


def install() -> None:
    """Verpakt `run_forever`: bewaker aan, het origineel, bewaker uit. Een tweede aanroep doet niets."""
    original: Callable[[asyncio.BaseEventLoop], None] = (
        asyncio.base_events.BaseEventLoop.run_forever
    )
    if MODE == "off" or getattr(original, WRAPPED_ATTRIBUTE, False):
        return
    GC.start()

    @functools.wraps(original)
    def run_forever(self: asyncio.BaseEventLoop) -> None:
        if getattr(self, GUARD_ATTRIBUTE, None) is not None:  # een geneste aanroep
            return original(self)
        guard: loop_guard.LoopGuard = loop_guard.LoopGuard(self, LIMIT, _collect)
        setattr(self, GUARD_ATTRIBUTE, guard)
        guard.start(loop_thread=threading.get_ident())  # run_forever draait op de thread van de loop
        try:
            return original(self)
        finally:
            guard.stop()
            setattr(self, GUARD_ATTRIBUTE, None)

    setattr(run_forever, WRAPPED_ATTRIBUTE, True)
    asyncio.base_events.BaseEventLoop.run_forever = run_forever  # type: ignore[method-assign]


def _without_gc_callback(stall: loop_guard.Stall) -> loop_guard.Stall:
    """De stilstand zonder de regels van gc_watch.py binnenin de stack: na een ronde van de GC op de thread van de loop is
    `GcWatch._callback` de binnenste regel van de app, wie de ronde ook aanriep. Zonder die regels telt alleen wat de ronde
    aanriep: code van de app, of asyncio en de test zelf."""
    stack: tuple[loop_guard.Frame, ...] = stall.stack
    while stack and stack[-1].in_app and os.path.basename(stack[-1].path) == "gc_watch.py":
        stack = stack[:-1]
    return replace(stall, stack=stack)


def counted(stalls: list[loop_guard.Stall], exempt: bool) -> list[loop_guard.Stall]:
    """De stilstanden die tellen: die de app veroorzaakte, tenzij de test is vrijgesteld (`may_block`). Een ronde van de GC telt
    alleen als de app hem aanriep (`_without_gc_callback`)."""
    if exempt:
        return []
    return [stall for stall in stalls if _without_gc_callback(stall).in_app]


def failure_message(mode: str, stalls: list[loop_guard.Stall]) -> str | None:
    """De foutmelding voor een test, of `None`: alleen `enforce` laat een test falen."""
    if mode != "enforce" or not stalls:
        return None
    lines: list[str] = [f"De event loop stond {len(stalls)} keer stil:"]
    lines.extend(f"  {stall.describe()}" for stall in stalls)
    return "\n".join(lines)


def record(test: str, stalls: list[loop_guard.Stall]) -> None:
    """Onthoudt de stilstanden van een test voor het overzicht aan het eind van de run."""
    _reported.extend(Reported(test, stall) for stall in stalls)


def exemption_reason(marker: Any) -> str | None:
    """De reden van `@pytest.mark.may_block("reden")`, `None` zonder markering. Zonder reden: `ValueError`."""
    if marker is None:
        return None
    reason: Any = marker.args[0] if marker.args else marker.kwargs.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("may_block eist een reden: @pytest.mark.may_block('waarom de test de loop blokkeert')")
    return reason


def check(nodeid: str, marker: Any, mode: str, stalls: list[loop_guard.Stall]) -> str | None:
    """Het oordeel over een test na afloop: de foutmelding, of `None` als de test door mag.

    `marker` is de `may_block`-markering van de test (of `None`) en `stalls` alles wat `drain()` gaf. In `report` worden de
    stilstanden onthouden voor het overzicht, in `enforce` geeft dit de foutmelding, in `off` gebeurt niets.
    Een `may_block` zonder reden geeft een `ValueError`.
    """
    reason: str | None = exemption_reason(marker)
    if mode == "off":
        return None
    relevant: list[loop_guard.Stall] = counted(stalls, reason is not None)
    if mode == "report":
        record(nodeid, relevant)
    return failure_message(mode, relevant)


def summary_lines(reported: list[Reported] | None = None) -> list[str]:
    """Het overzicht voor `pytest_terminal_summary`: per binnenste regel van de app het aantal tests en de langste duur."""
    items: list[Reported] = _reported if reported is None else reported
    groups: dict[str, list[Reported]] = {}
    for entry in items:
        frames: tuple[loop_guard.Frame, ...] = entry.stall.app_frames
        groups.setdefault(frames[-1].short() if frames else "?", []).append(entry)
    ordered: list[tuple[str, list[Reported]]] = sorted(
        groups.items(), key=lambda pair: -max(entry.stall.seconds for entry in pair[1])
    )
    lines: list[str] = []
    for where, entries in ordered:
        tests: set[str] = {entry.test for entry in entries}
        longest: float = max(entry.stall.seconds for entry in entries)
        lines.append(
            f"{round(longest * 1000):>6} ms  {len(tests):>3} tests  {len(entries):>3}x  {where}"
        )
    return lines
