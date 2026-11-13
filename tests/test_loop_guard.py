"""De bewaker die stilstand van de event loop meet met de stack (mealieshopper/loop_guard.py).

Regel van het project: de server blokkeert de event loop nooit langer dan 100 ms. De bewaker meet vanuit een eigen thread
wanneer dat toch gebeurt en welke regels er toen liepen. De tests blokkeren de loop met opzet met `time.sleep`.
"""

from __future__ import annotations

import asyncio
import os
import time

from mealieshopper import loop_guard

APP_PATH: str = os.path.join(loop_guard.APP_DIR, "prices.py")


async def test_a_blocking_call_is_reported_with_its_stack() -> None:
    stalls: list[loop_guard.Stall] = []
    guard: loop_guard.LoopGuard = loop_guard.LoopGuard(
        asyncio.get_running_loop(), 0.05, stalls.append
    )
    guard.start()
    time.sleep(0.2)  # noqa: ASYNC251
    await asyncio.sleep(0.05)
    guard.stop()
    assert len(stalls) == 1
    assert 0.15 <= stalls[0].seconds < 1.0
    assert (
        stalls[0].stack[-1].function
        == "test_a_blocking_call_is_reported_with_its_stack"
    )


async def test_waiting_is_not_a_stall() -> None:
    stalls: list[loop_guard.Stall] = []
    guard: loop_guard.LoopGuard = loop_guard.LoopGuard(
        asyncio.get_running_loop(), 0.05, stalls.append
    )
    guard.start()
    await asyncio.sleep(0.2)
    guard.stop()
    assert stalls == []


async def test_work_in_a_thread_is_not_a_stall() -> None:
    stalls: list[loop_guard.Stall] = []
    guard: loop_guard.LoopGuard = loop_guard.LoopGuard(
        asyncio.get_running_loop(), 0.05, stalls.append
    )
    guard.start()
    await asyncio.to_thread(time.sleep, 0.2)
    guard.stop()
    assert stalls == []


async def test_two_stalls_are_two_reports() -> None:
    stalls: list[loop_guard.Stall] = []
    guard: loop_guard.LoopGuard = loop_guard.LoopGuard(
        asyncio.get_running_loop(), 0.05, stalls.append
    )
    guard.start()
    time.sleep(0.15)  # noqa: ASYNC251
    await asyncio.sleep(0.05)
    time.sleep(0.15)  # noqa: ASYNC251
    await asyncio.sleep(0.05)
    guard.stop()
    assert len(stalls) == 2


async def test_a_failing_callback_does_not_stop_the_guard() -> None:
    calls: list[loop_guard.Stall] = []

    def on_stall(stall: loop_guard.Stall) -> None:
        calls.append(stall)
        raise RuntimeError("kapot")

    guard: loop_guard.LoopGuard = loop_guard.LoopGuard(
        asyncio.get_running_loop(), 0.05, on_stall
    )
    guard.start()
    time.sleep(0.15)  # noqa: ASYNC251
    await asyncio.sleep(0.05)
    time.sleep(0.15)  # noqa: ASYNC251
    await asyncio.sleep(0.05)
    guard.stop()
    assert len(calls) == 2


def test_in_app_needs_a_frame_of_the_app() -> None:
    outside: loop_guard.Stall = loop_guard.Stall(
        0.3, (loop_guard.Frame("/usr/lib/python3/asyncio/base_events.py", 10, "run"),)
    )
    inside: loop_guard.Stall = loop_guard.Stall(
        0.3, (loop_guard.Frame("/usr/lib/x.py", 1, "run"), loop_guard.Frame(APP_PATH, 5, "_catalogue"))
    )
    assert not outside.in_app
    assert outside.app_frames == ()
    assert inside.in_app
    assert [frame.function for frame in inside.app_frames] == ["_catalogue"]


def test_startup_is_exempt() -> None:
    stall: loop_guard.Stall = loop_guard.Stall(
        1.0,
        (
            loop_guard.Frame(APP_PATH, 5, "create_app"),
            loop_guard.Frame(APP_PATH, 9, "_load"),
        ),
    )
    assert not stall.in_app


def test_describe_lists_the_innermost_app_frames_first() -> None:
    stall: loop_guard.Stall = loop_guard.Stall(
        0.31,
        (
            loop_guard.Frame("/usr/lib/x.py", 1, "run"),
            loop_guard.Frame(APP_PATH, 914, "_known_products"),
            loop_guard.Frame(APP_PATH, 1040, "_catalogue"),
        ),
    )
    assert (
        stall.describe()
        == "310 ms: prices.py:1040 _catalogue < prices.py:914 _known_products"
    )
    assert stall.describe(limit=1) == "310 ms: prices.py:1040 _catalogue"


async def test_stop_after_the_loop_closed_does_not_raise() -> None:
    loop: asyncio.AbstractEventLoop = asyncio.new_event_loop()
    guard: loop_guard.LoopGuard = loop_guard.LoopGuard(loop, 0.05, lambda stall: None)
    guard.start()
    loop.close()
    time.sleep(0.05)  # noqa: ASYNC251
    guard.stop()
    loop_guard.LoopGuard(loop, 0.05, lambda stall: None).stop()
