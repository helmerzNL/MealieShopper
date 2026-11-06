"""Tests voor mealieshopper/tasks.py en de levenscyclus van de app."""

import asyncio
import logging
import threading
from collections.abc import AsyncIterator, Coroutine
from pathlib import Path
from typing import Any

import pytest
from quart import Quart

from mealieshopper import tasks


@pytest.fixture(autouse=True)
async def _clean_registry() -> AsyncIterator[None]:
    """Een falende assert laat geen tasks achter in de globale register."""
    yield
    await tasks.cancel_all()


async def _forever() -> None:
    await asyncio.sleep(3600)


async def test_spawn_registers_and_unregisters_a_task() -> None:
    gate: asyncio.Event = asyncio.Event()

    async def work() -> str:
        await gate.wait()
        return "klaar"

    task: asyncio.Task = tasks.spawn("test-spawn", work())
    assert tasks.running() == {"test-spawn": task}
    assert task.get_name() == "test-spawn"
    gate.set()
    assert await task == "klaar"
    assert "test-spawn" not in tasks.running()


async def test_duplicate_name_gets_a_suffix() -> None:
    first: asyncio.Task = tasks.spawn("dubbel", _forever())
    second: asyncio.Task = tasks.spawn("dubbel", _forever())
    assert set(tasks.running()) == {"dubbel", "dubbel-2"}
    assert second.get_name() == "dubbel-2"
    await tasks.cancel_all()
    assert first.cancelled() and second.cancelled()


async def test_a_crashing_task_is_logged_and_leaves_others_running(caplog: pytest.LogCaptureFixture) -> None:
    async def boom() -> None:
        raise RuntimeError("kapot")

    survivor: asyncio.Task = tasks.spawn("survivor", _forever())
    with caplog.at_level(logging.ERROR, logger="mealieshopper.tasks"):
        crashing: asyncio.Task = tasks.spawn("crasher", boom())
        await asyncio.wait([crashing])
        await asyncio.sleep(0)
    assert "Achtergrondtaak crasher mislukt" in caplog.text
    assert "Traceback" in caplog.text
    assert "RuntimeError: kapot" in caplog.text
    assert not survivor.done()
    assert set(tasks.running()) == {"survivor"}
    await tasks.cancel_all()


async def test_cancel_all_stops_every_task(caplog: pytest.LogCaptureFixture) -> None:
    started: list[asyncio.Task] = [tasks.spawn(f"t{i}", _forever()) for i in range(3)]
    await asyncio.sleep(0)
    with caplog.at_level(logging.ERROR, logger="mealieshopper.tasks"):
        await tasks.cancel_all()
    assert all(task.cancelled() for task in started)
    assert tasks.running() == {}
    assert "mislukt" not in caplog.text


async def test_startup_hook_initialises_the_database(app: Quart, data_dir: Path) -> None:
    assert not (data_dir / "auth.sqlite3").exists()
    async with app.test_app():
        assert (data_dir / "auth.sqlite3").exists()


async def test_startup_does_not_start_the_offers_thread(app: Quart) -> None:
    async with app.test_app():
        names: list[str] = [thread.name for thread in threading.enumerate()]
    assert "offers-schedule" not in names


async def _yield(times: int) -> None:
    """Geeft de event loop `times` keer vrij, zodat andere aanroepen kunnen beginnen."""
    for _ in range(times):
        await asyncio.sleep(0)


async def test_map_bounded_runs_at_most_limit_at_once_and_keeps_the_order() -> None:
    state: list[int] = [0, 0]  # nu bezig, hoogste aantal tegelijk

    async def double(item: int) -> int:
        state[0] += 1
        state[1] = max(state[1], state[0])
        await _yield(20 - item)  # een later item is eerder klaar
        state[0] -= 1
        return item * 2

    assert await tasks.map_bounded(6, double, range(20)) == [item * 2 for item in range(20)]
    assert state[1] == 6


async def test_map_bounded_makes_each_coroutine_only_when_its_turn_comes() -> None:
    # Zoals `pool.map`: `fn(item)` wordt pas aangeroepen als er een plek vrij is. Zo blijft er bij een fout of een annulering
    # geen coroutine achter die nooit is afgewacht.
    running: list[int] = []
    seen_when_made: list[int] = []

    async def work(item: int) -> int:
        running.append(item)
        await _yield(2)
        running.remove(item)
        return item

    def make(item: int) -> Coroutine[Any, Any, int]:
        seen_when_made.append(len(running))
        return work(item)

    assert await tasks.map_bounded(2, make, range(5)) == list(range(5))
    assert max(seen_when_made) < 2


async def test_map_bounded_cancels_the_rest_and_raises_the_first_error_itself() -> None:
    started: list[int] = []
    cancelled: list[int] = []
    finished: list[int] = []

    class Refused(RuntimeError):
        pass

    async def work(item: int) -> int:
        started.append(item)
        try:
            await _yield(3 if item == 0 else 50)
        except asyncio.CancelledError:
            cancelled.append(item)
            raise
        if item == 0:
            raise Refused("koppeling verlopen")
        finished.append(item)
        return item

    with pytest.raises(Refused, match="koppeling verlopen"):
        await tasks.map_bounded(6, work, range(10))
    # Wie liep of net de vrije plek van item 0 kreeg, is geannuleerd voordat de fout bovenkwam. Niemand maakte zijn werk af
    # en de rest is nooit begonnen.
    assert sorted(cancelled) == sorted(set(started) - {0})
    assert set(range(1, 6)) <= set(cancelled)
    assert len(started) <= 7
    assert finished == []
