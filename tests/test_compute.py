"""De helper voor rekenwerk buiten de event loop (mealieshopper/compute.py).

Regel van het project: de server houdt de event loop nooit langer dan 100 ms vast. `compute.run` laat een pure synchrone
functie in een eigen threadpool lopen, `SingleFlight` zorgt dat een dure bouw per sleutel maar een keer tegelijk loopt.
"""

from __future__ import annotations

import asyncio
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
    token = telemetry._current.set(recording)
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
        )  # bewijst dat er er twee tegelijk bezig kunnen zijn
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
