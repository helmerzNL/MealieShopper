"""De helper voor rekenwerk buiten de event loop (mealieshopper/compute.py).

Regel van het project: de server houdt de event loop nooit langer dan 100 ms vast. `compute.run` laat een pure synchrone
functie in een eigen threadpool lopen, `SingleFlight` zorgt dat een dure bouw per sleutel maar een keer tegelijk loopt.
"""

from __future__ import annotations

import asyncio
import contextvars
import gc
import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest

from mealieshopper import compute, loop_guard, telemetry


@pytest.fixture(autouse=True)
def fresh_pool() -> Iterator[None]:
    """Elke test begint en eindigt zonder pool, zodat geen thread de volgende test in loopt."""
    compute.shutdown()
    yield
    compute.shutdown()


async def test_run_returns_the_result_and_keeps_the_loop_free() -> None:
    stalls: list[loop_guard.Stall] = []
    guard: loop_guard.LoopGuard = loop_guard.LoopGuard(
        asyncio.get_running_loop(), 0.05, stalls.append
    )
    guard.start()
    result: str = await compute.run(lambda: time.sleep(0.3) or "klaar")
    guard.stop()
    assert result == "klaar"
    assert stalls == []


async def test_run_passes_arguments_and_keywords() -> None:
    def join(first: str, second: str, *, sep: str) -> str:
        return f"{first}{sep}{second}"

    assert await compute.run(join, "a", "b", sep="-") == "a-b"


async def test_run_raises_the_error_of_the_function() -> None:
    def fail() -> None:
        raise ValueError("kapot")

    with pytest.raises(ValueError, match="kapot"):
        await compute.run(fail)


async def test_run_counts_as_a_phase_in_the_telemetry() -> None:
    recording: telemetry.Recording = telemetry.Recording("GET", "/x")
    token: contextvars.Token[telemetry.Recording | None] = telemetry._current.set(
        recording
    )
    try:
        await compute.run(time.sleep, 0.05)
    finally:
        telemetry._current.reset(token)
    assert recording.phases["compute"] > 0
    assert recording.counts["compute"] == 1


async def test_at_most_two_run_at_once() -> None:
    lock: threading.Lock = threading.Lock()
    both_started: threading.Barrier = threading.Barrier(compute.WORKERS)
    active: list[int] = [0]
    most: list[int] = [0]

    def work() -> None:
        with lock:
            active[0] += 1
            most[0] = max(most[0], active[0])
        both_started.wait(
            timeout=5
        )  # bewijst dat er twee tegelijk bezig kunnen zijn
        time.sleep(0.02)
        with lock:
            active[0] -= 1

    await asyncio.gather(*(compute.run(work) for _ in range(compute.WORKERS * 2)))
    assert most[0] == compute.WORKERS


async def test_single_flight_builds_once_for_concurrent_callers() -> None:
    flight: compute.SingleFlight[str, int] = compute.SingleFlight()
    release: asyncio.Event = asyncio.Event()
    builds: list[int] = []

    async def build() -> int:
        builds.append(1)
        await release.wait()
        return 42

    callers: list[asyncio.Task[int]] = [
        asyncio.create_task(flight.get("k", build)) for _ in range(3)
    ]
    await asyncio.sleep(0)
    release.set()
    assert await asyncio.gather(*callers) == [42, 42, 42]
    assert len(builds) == 1
    assert flight._flights == {}


async def test_single_flight_does_not_keep_an_error() -> None:
    flight: compute.SingleFlight[str, int] = compute.SingleFlight()
    calls: list[int] = []

    async def build() -> int:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("eerste keer mislukt")
        return 7

    with pytest.raises(RuntimeError, match="eerste keer"):
        await flight.get("k", build)
    assert await flight.get("k", build) == 7
    assert len(calls) == 2


async def test_single_flight_survives_a_cancelled_caller() -> None:
    flight: compute.SingleFlight[str, int] = compute.SingleFlight()
    release: asyncio.Event = asyncio.Event()
    builds: list[int] = []

    async def build() -> int:
        builds.append(1)
        await release.wait()
        return 5

    first: asyncio.Task[int] = asyncio.create_task(flight.get("k", build))
    second: asyncio.Task[int] = asyncio.create_task(flight.get("k", build))
    await asyncio.sleep(0)
    first.cancel()
    await asyncio.sleep(0)
    release.set()
    assert await second == 5
    assert first.cancelled()
    assert len(builds) == 1
    assert flight._flights == {}


async def test_single_flight_leaves_nothing_behind_when_the_only_caller_is_cancelled() -> (
    None
):
    flight: compute.SingleFlight[str, int] = compute.SingleFlight()
    release: asyncio.Event = asyncio.Event()
    problems: list[dict[str, Any]] = []
    loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
    previous: Any = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: problems.append(context))

    async def failing() -> int:
        await release.wait()
        raise RuntimeError("mislukt nadat de aanroeper weg was")

    async def working() -> int:
        return 9

    try:
        caller: asyncio.Task[int] = asyncio.create_task(flight.get("k", failing))
        await asyncio.sleep(0)
        caller.cancel()
        await asyncio.sleep(0)
        release.set()
        for _ in range(5):
            await asyncio.sleep(0)
        gc.collect()
        assert flight._flights == {}
        assert await flight.get("k", working) == 9  # geen oude uitkomst, geen hang
    finally:
        loop.set_exception_handler(previous)
    assert problems == []


async def test_shutdown_returns_within_five_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(compute, "SHUTDOWN_SECONDS", 0.3)
    started: threading.Event = threading.Event()
    stuck: threading.Event = threading.Event()  # de functie zou 30 s wachten

    def wait_forever() -> bool:
        started.set()
        return stuck.wait(30)

    task: asyncio.Task[bool] = asyncio.create_task(compute.run(wait_forever))
    assert await asyncio.to_thread(started.wait, 5)
    begin: float = time.monotonic()
    await asyncio.to_thread(compute.shutdown)  # in een thread: de test meet de grens, niet een stilstaande loop
    assert time.monotonic() - begin < 5
    stuck.set()  # laat de thread afronden
    assert await task is True


async def test_run_works_again_after_shutdown() -> None:
    assert await compute.run(len, "abc") == 3
    compute.shutdown()
    assert await compute.run(len, "abcd") == 4


async def test_a_stopped_pool_refuses_a_job() -> None:
    pool: compute._Pool = compute._Pool()
    pool.stop(1.0)
    with pytest.raises(compute.PoolStopped):
        pool.submit(len)


async def test_run_lands_on_a_new_pool_when_shutdown_races_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`shutdown` in een andere thread tussen `_get_pool` en `submit`: de opdracht mag niet blijven hangen."""
    real: Any = compute._get_pool
    calls: list[int] = []

    def racing() -> compute._Pool:
        pool: compute._Pool = real()
        if not calls:
            calls.append(1)
            compute.shutdown()  # de pool is gestopt voor `submit` hem krijgt
        return pool

    monkeypatch.setattr(compute, "_get_pool", racing)
    assert await asyncio.wait_for(compute.run(len, "abc"), 5) == 3


async def test_stop_does_not_hold_the_loop_while_a_function_is_stuck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(compute, "SHUTDOWN_SECONDS", 0.5)
    started: threading.Event = threading.Event()
    stuck: threading.Event = threading.Event()
    stalls: list[loop_guard.Stall] = []
    guard: loop_guard.LoopGuard = loop_guard.LoopGuard(
        asyncio.get_running_loop(), 0.1, stalls.append
    )

    def wait_forever() -> bool:
        started.set()
        return stuck.wait(30)

    task: asyncio.Task[bool] = asyncio.create_task(compute.run(wait_forever))
    assert await asyncio.to_thread(started.wait, 5)
    guard.start()
    await compute.stop()
    guard.stop()
    assert stalls == []
    stuck.set()
    assert await task is True


async def test_single_flight_ignores_a_finished_flight_that_is_not_yet_removed() -> (
    None
):
    flight: compute.SingleFlight[str, int] = compute.SingleFlight()
    calls: list[int] = []

    async def build() -> int:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("oud")
        return 2

    failed: asyncio.Future[int] = asyncio.ensure_future(build())
    await asyncio.sleep(0)
    assert failed.done()
    flight._flights["k"] = failed  # klaar, maar `_finished` liep nog niet
    assert await flight.get("k", build) == 2
    with pytest.raises(RuntimeError):
        failed.result()


def test_single_flight_ignores_a_flight_of_another_loop() -> None:
    flight: compute.SingleFlight[str, int] = compute.SingleFlight()

    async def never() -> int:
        await asyncio.sleep(3600)
        return 0

    async def start_and_leave() -> None:
        flight._flights["k"] = asyncio.ensure_future(never())

    old: asyncio.AbstractEventLoop = asyncio.new_event_loop()
    old.run_until_complete(start_and_leave())
    stale: asyncio.Future[int] = flight._flights["k"]
    old.close()

    async def build() -> int:
        return 11

    async def ask() -> int:
        return await flight.get("k", build)

    new: asyncio.AbstractEventLoop = asyncio.new_event_loop()
    try:
        assert new.run_until_complete(ask()) == 11
    finally:
        new.close()
    del stale


async def test_app_shutdown_does_not_stall_the_loop_while_a_function_is_stuck(
    data_dir: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mealieshopper.app import create_app

    monkeypatch.setattr(compute, "SHUTDOWN_SECONDS", 0.5)
    started: threading.Event = threading.Event()
    stuck: threading.Event = threading.Event()
    stalls: list[loop_guard.Stall] = []
    guard: loop_guard.LoopGuard = loop_guard.LoopGuard(
        asyncio.get_running_loop(), 0.1, stalls.append
    )

    def wait_forever() -> bool:
        started.set()
        return stuck.wait(30)

    app: Any = create_app()
    async with app.test_app():
        task: asyncio.Task[bool] = asyncio.create_task(compute.run(wait_forever))
        assert await asyncio.to_thread(started.wait, 5)
        guard.start()
    guard.stop()  # na het afsluiten van de app (after_serving)
    assert stalls == []
    stuck.set()
    assert await task is True
