"""De meting van de rondes van de garbage collector (mealieshopper/gc_watch.py)."""

from __future__ import annotations

import gc
import weakref

from mealieshopper import gc_watch


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


class Node:
    """Een object dat de GC volgt en dat naar zichzelf kan wijzen (een kring)."""

    me: Node | None = None


def test_set_aside_freezes_what_lives_and_clears_young_cycles_first() -> None:
    before: int = gc.get_freeze_count()
    kept: list[Node] = [Node() for _ in range(1000)]
    cycle: Node = Node()
    cycle.me = cycle
    gone: weakref.ref[Node] = weakref.ref(cycle)
    del cycle  # nu afval, maar alleen de GC ruimt een kring op
    gc_watch.set_aside()
    assert gone() is None  # opgeruimd voor het bevriezen, dus niet voor altijd bewaard
    assert gc.get_freeze_count() >= before + len(kept)
