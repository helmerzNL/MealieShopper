"""De meting van de rondes van de garbage collector (mealieshopper/gc_watch.py)."""

from __future__ import annotations

import gc
import weakref
from unittest import mock

import pytest

from mealieshopper import gc_watch
from mealieshopper.app import create_app


class _Node:
    """Een object in een kring met zichzelf: alleen een ronde van de GC ruimt het op, het tellen van verwijzingen niet."""

    def __init__(self) -> None:
        self.me: _Node = self


def _dead_cycle(freed: list[str], name: str) -> None:
    node: _Node = _Node()
    weakref.finalize(node, freed.append, name)


def test_freeze_is_on_by_default_and_off_in_the_tests_unless_asked() -> None:
    assert gc_watch.freeze_wanted({}) is True
    assert gc_watch.freeze_wanted({"MEALIESHOPPER_TESTING": "1"}) is False
    assert gc_watch.freeze_wanted({"MEALIESHOPPER_TESTING": "1", "MEALIESHOPPER_GC_FREEZE": "true"}) is True
    assert gc_watch.freeze_wanted({"MEALIESHOPPER_GC_FREEZE": " Off "}) is False
    assert gc_watch.freeze_wanted({"MEALIESHOPPER_GC_FREEZE": "false"}) is False


def test_the_freeze_after_start_first_clears_what_is_already_dead() -> None:
    # Taak 9b bevroor tijdens een bouw ook dode kringen (net gesloten verbindingen), en die bleven voor altijd bewaard.
    freed: list[str] = []
    _dead_cycle(freed, "voor")
    try:
        assert gc_watch.freeze_after_start() > 0
        assert freed == ["voor"]  # opgeruimd voor het bevriezen, niet mee de vaste generatie in
        _dead_cycle(freed, "na")
        gc.collect()
        assert freed == ["voor", "na"]  # een kring van na de start ruimt de GC gewoon op
    finally:
        gc.unfreeze()


async def test_the_app_freezes_once_after_its_startup_work(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEALIESHOPPER_GC_FREEZE", "true")
    with mock.patch.object(gc_watch, "freeze_after_start", return_value=1234) as freeze:
        app = create_app()
        async with app.test_app():
            freeze.assert_called_once_with()


async def test_the_app_does_not_freeze_in_the_tests_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MEALIESHOPPER_GC_FREEZE", raising=False)
    with mock.patch.object(gc_watch, "freeze_after_start") as freeze:
        app = create_app()
        async with app.test_app():
            freeze.assert_not_called()


def test_a_collection_is_recorded_with_its_generation_and_thread() -> None:
    seen: list[gc_watch.Collection] = []
    watch: gc_watch.GcWatch = gc_watch.GcWatch(keep=0.0, on_collection=seen.append)
    watch.start()
    try:
        gc.collect()
    finally:
        watch.stop()
    assert seen and seen[-1].generation == 2
    assert seen[-1].thread == "MainThread"
    assert watch.drain()[-1] == seen[-1]
    assert watch.drain() == []
    assert seen[-1].describe().startswith("GC generatie 2 ")
    assert set(seen[-1].as_dict()) == {"generation", "ms", "thread", "collected"}


def test_a_short_collection_is_not_kept() -> None:
    watch: gc_watch.GcWatch = gc_watch.GcWatch(keep=60.0)
    watch.start()
    try:
        gc.collect()
    finally:
        watch.stop()
    assert watch.drain() == []


def test_stop_removes_the_callback() -> None:
    watch: gc_watch.GcWatch = gc_watch.GcWatch()
    watch.start()
    watch.start()  # nog eens aanzetten hangt hem niet twee keer aan
    assert gc.callbacks.count(watch._callback) == 1
    watch.stop()
    watch.stop()
    assert watch._callback not in gc.callbacks
