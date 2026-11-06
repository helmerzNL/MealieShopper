"""Tests voor mealieshopper/tasks.py en de levenscyclus van de app."""

import asyncio
import logging
import threading
from collections.abc import AsyncIterator
from pathlib import Path

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
