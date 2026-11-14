"""Tests voor mealieshopper/tasks.py en de levenscyclus van de app."""

import asyncio
import logging
import threading
from collections.abc import AsyncIterator, Coroutine
from pathlib import Path
from typing import Any

import pytest
from quart import Quart

from mealieshopper import compute, response_cache, tasks


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


async def test_map_bounded_keeps_the_context_of_the_error() -> None:
    # De fout komt naar boven met haar eigen `__context__` (hier de KeyError waarin ze ontstond), niet met de ExceptionGroup.
    async def work(item: int) -> int:
        try:
            raise KeyError("oorzaak")
        except KeyError:
            raise RuntimeError(f"gevolg {item}")

    with pytest.raises(RuntimeError, match="gevolg") as caught:
        await tasks.map_bounded(2, work, [1])
    assert isinstance(caught.value.__context__, KeyError)
    assert caught.value.__suppress_context__ is False


async def test_map_bounded_passes_an_outer_cancel_on_and_cancels_the_children() -> None:
    started: asyncio.Event = asyncio.Event()
    cancelled: list[int] = []

    async def work(item: int) -> int:
        started.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.append(item)
            raise
        return item

    outer: asyncio.Task = asyncio.create_task(tasks.map_bounded(3, work, range(3)))
    await started.wait()
    await _yield(2)
    outer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await outer
    assert sorted(cancelled) == [0, 1, 2]


async def test_map_bounded_an_outer_cancel_wins_over_an_error_while_cleaning_up() -> None:
    # Gooit een kind bij het annuleren een fout, dan blijft het een annulering: wie map_bounded annuleerde, krijgt die terug.
    started: asyncio.Event = asyncio.Event()

    async def work(item: int) -> int:
        started.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            raise RuntimeError("opruimen mislukt") from None
        return item

    outer: asyncio.Task = asyncio.create_task(tasks.map_bounded(2, work, range(2)))
    await started.wait()
    await _yield(2)
    outer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await outer


async def test_shielded_work_finishes_although_its_caller_is_cancelled() -> None:
    release: asyncio.Event = asyncio.Event()
    done: list[str] = []

    async def store() -> str:
        await release.wait()
        done.append("bewaard")
        return "klaar"

    caller: asyncio.Task = asyncio.create_task(tasks.shielded(store()))
    await _yield(2)
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    release.set()
    await tasks.cancel_all()  # wacht ook op afgeschermd werk dat nog loopt
    assert done == ["bewaard"]



async def test_map_bounded_stops_what_it_started_when_the_items_raise() -> None:
    """Gooit de iterable zelf halverwege, dan blijven de al gestarte items niet als losse tasks achter."""
    started: list[int] = []
    release: asyncio.Event = asyncio.Event()

    async def slow(item: int) -> int:
        started.append(item)
        await release.wait()
        return item

    def items() -> Any:
        yield 1
        yield 2
        raise ValueError("kapotte invoer")

    before: set[asyncio.Task[Any]] = asyncio.all_tasks()
    with pytest.raises(ValueError, match="kapotte invoer"):
        await tasks.map_bounded(2, slow, items())
    await _yield(2)
    assert asyncio.all_tasks() - before == set(), "geen achtergebleven tasks"


async def test_a_failed_shielded_task_without_a_caller_is_logged_as_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    """Een afgeschermde task die faalt nadat zijn aanroeper weg is: de fout (een token dat niet bewaard is) moet zichtbaar zijn."""
    release: asyncio.Event = asyncio.Event()

    async def store() -> None:
        await release.wait()
        raise RuntimeError("niet bewaard")

    caller: asyncio.Task = asyncio.create_task(tasks.shielded(store()))
    await _yield(2)
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    with caplog.at_level(logging.WARNING, logger=tasks.log.name):
        release.set()
        await tasks.cancel_all()
    assert any(record.levelno == logging.WARNING and "niet bewaard" in str(record.exc_info[1]) for record in caplog.records if record.exc_info)


async def test_a_failed_shielded_task_with_its_caller_waiting_is_no_warning(caplog: pytest.LogCaptureFixture) -> None:
    """Wacht de aanroeper nog, dan krijgt die de fout zelf: geen waarschuwing erbij."""

    async def store() -> None:
        raise RuntimeError("niet gekoppeld")

    with caplog.at_level(logging.WARNING, logger=tasks.log.name), pytest.raises(RuntimeError):
        await tasks.shielded(store())
    await _yield(2)
    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]


async def test_shutdown_waits_at_most_five_seconds_for_tasks_so_docker_does_not_kill_the_app(monkeypatch: pytest.MonkeyPatch) -> None:
    """3 seconden van hypercorn voor lopende verzoeken plus deze 5 blijft onder de 10 seconden van `docker stop`."""
    seen: list[float] = []

    async def record(timeout: float = 10.0) -> None:
        seen.append(timeout)

    monkeypatch.setattr(tasks, "cancel_all", record)
    await tasks._cancel_all_hook()
    assert seen == [5.0]


async def test_after_serving_reports_that_the_app_stopped(app: Quart, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="mealieshopper.tasks"):
        async with app.test_app():
            assert "afgesloten" not in caplog.text
    assert "MealieShopper afgesloten" in caplog.text


async def test_cancel_all_waits_for_a_build_whose_callers_are_gone() -> None:
    """Een bouw van `compute.SingleFlight` en een lading van `response_cache.ResponseCache` lopen door als hun aanroeper wordt
    geannuleerd. `cancel_all` wacht er nu op, zoals op `shielded`: anders las zo'n bouw nog in de database als de app dicht was,
    of als een test zijn map al weggooide (taak 9c: "Directory not empty")."""
    release: asyncio.Event = asyncio.Event()
    finished: list[str] = []

    async def build(name: str) -> str:
        await release.wait()
        finished.append(name)
        return name

    flights: compute.SingleFlight[str, str] = compute.SingleFlight()
    cache: response_cache.ResponseCache = response_cache.ResponseCache()
    callers: list[asyncio.Task[Any]] = [
        tasks.spawn("catalogus", flights.get("ah", lambda: build("bouw"))),
        tasks.spawn("aanbiedingen", cache.get("ah", "etag", lambda: build("lading"))),
    ]
    await asyncio.sleep(0)
    asyncio.get_running_loop().call_later(0.05, release.set)
    await tasks.cancel_all(timeout=5)
    assert all(caller.cancelled() for caller in callers)
    assert sorted(finished) == ["bouw", "lading"]
