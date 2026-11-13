"""De bewaker om elke event loop in de tests (`tests/loop_watch.py`)."""

from __future__ import annotations

import asyncio
import os
import time
import types
from typing import Any

import helpers
import loop_watch
import pytest

from mealieshopper import loop_guard

APP_FILE: str = os.path.join(loop_guard.APP_DIR, "blocker.py")
BLOCK_SECONDS: float = loop_watch.LIMIT + 0.15


def app_stall(seconds: float = 0.3) -> loop_guard.Stall:
    """Een stilstand met een regel van de app op de stack."""
    return loop_guard.Stall(
        seconds,
        (
            loop_guard.Frame("/x/test_a.py", 3, "test_a"),
            loop_guard.Frame(APP_FILE, 12, "slow_call"),
        ),
    )


def test_a_stall_in_the_app_fails_the_test_in_enforce_mode() -> None:
    stalls: list[loop_guard.Stall] = loop_watch.counted([app_stall()], exempt=False)
    message: str | None = loop_watch.failure_message("enforce", stalls)
    assert message is not None
    assert app_stall().describe() in message


def test_a_stall_outside_the_app_does_not_count() -> None:
    outside: loop_guard.Stall = loop_guard.Stall(
        0.5, (loop_guard.Frame("/x/test_a.py", 3, "test_a"),)
    )
    counted: list[loop_guard.Stall] = loop_watch.counted([outside], exempt=False)
    assert counted == []
    assert loop_watch.failure_message("enforce", counted) is None


def test_may_block_exempts_a_test() -> None:
    assert loop_watch.counted([app_stall()], exempt=True) == []
    marker: Any = pytest.mark.may_block("de test blokkeert met opzet").mark
    assert loop_watch.exemption_reason(marker) == "de test blokkeert met opzet"
    assert loop_watch.exemption_reason(None) is None
    with pytest.raises(ValueError, match="eist een reden"):
        loop_watch.exemption_reason(pytest.mark.may_block.mark)


def test_report_mode_lists_stalls_without_failing() -> None:
    stalls: list[loop_guard.Stall] = loop_watch.counted(
        [app_stall(0.2), app_stall(0.4)], exempt=False
    )
    assert loop_watch.failure_message("report", stalls) is None
    lines: list[str] = loop_watch.summary_lines(
        [loop_watch.Reported("t1", stalls[0]), loop_watch.Reported("t2", stalls[1])]
    )
    assert len(lines) == 1  # beide op dezelfde binnenste regel van de app
    assert (
        "400 ms" in lines[0]
        and "2 tests" in lines[0]
        and "blocker.py:12 slow_call" in lines[0]
    )


def check(mode: str, stalls: list[loop_guard.Stall], *marks: str) -> str | None:
    marker: Any = pytest.mark.may_block(*marks).mark if marks else None
    return loop_watch.check("tests/test_x.py::test_y", marker, mode, stalls)


def test_check_enforce_fails_a_stall_in_the_app() -> None:
    message: str | None = check("enforce", [app_stall()])
    assert message is not None
    assert app_stall().describe() in message


def test_check_enforce_ignores_a_stall_without_an_app_frame() -> None:
    outside: loop_guard.Stall = loop_guard.Stall(
        0.5, (loop_guard.Frame("/x/test_a.py", 3, "test_a"),)
    )
    assert check("enforce", [outside]) is None


def test_check_enforce_honours_may_block_with_a_reason() -> None:
    assert check("enforce", [app_stall()], "met opzet") is None


def test_check_may_block_without_a_reason_is_an_error() -> None:
    with pytest.raises(ValueError, match="eist een reden"):
        check("enforce", [app_stall()], "")
    with pytest.raises(ValueError, match="eist een reden"):
        loop_watch.check("t", pytest.mark.may_block.mark, "enforce", [])


def test_check_report_records_without_failing_and_off_does_nothing() -> None:
    loop_watch._reported.clear()
    assert check("report", [app_stall()]) is None
    assert [entry.test for entry in loop_watch._reported] == ["tests/test_x.py::test_y"]
    loop_watch._reported.clear()
    assert check("off", [app_stall()]) is None
    assert check("report", [app_stall()], "met opzet") is None
    assert loop_watch._reported == []  # uit en vrijgesteld: niet in het overzicht


def sleep_synchronously(seconds: float) -> None:
    time.sleep(seconds)  # de test blokkeert met opzet


def blocking_in_the_app() -> None:
    """Blokkeert met een regel onder `mealieshopper/` op de stack: dezelfde functie, met het pad van een module van de app."""
    code: types.CodeType = sleep_synchronously.__code__.replace(co_filename=APP_FILE)
    types.FunctionType(code, globals())(BLOCK_SECONDS)


def counted_after(drained: list[loop_guard.Stall]) -> list[loop_guard.Stall]:
    return loop_watch.counted(drained, exempt=False)


def test_a_loop_started_by_helpers_run_is_watched() -> None:
    loop_watch.drain()

    async def block() -> None:
        blocking_in_the_app()
        await asyncio.sleep(
            0.05
        )  # de bewaker meldt pas als de loop weer aan de beurt is

    helpers.run(block())
    stalls: list[loop_guard.Stall] = counted_after(loop_watch.drain())
    assert stalls
    assert stalls[0].app_frames[-1].path == APP_FILE


class TestIsolatedCase(helpers.AppTestCase):
    async def test_a_loop_of_an_isolated_asyncio_test_case_is_watched(self) -> None:
        loop_watch.drain()
        blocking_in_the_app()
        await asyncio.sleep(0.05)
        stalls: list[loop_guard.Stall] = counted_after(loop_watch.drain())
        assert stalls
        assert stalls[0].app_frames[-1].path == APP_FILE


@pytest.mark.may_block("de test blokkeert de event loop met opzet")
async def test_a_loop_of_a_pytest_asyncio_test_is_watched() -> None:
    loop_watch.drain()
    blocking_in_the_app()
    await asyncio.sleep(0.05)
    stalls: list[loop_guard.Stall] = counted_after(loop_watch.drain())
    assert stalls
    assert stalls[0].app_frames[-1].path == APP_FILE


def test_install_is_idempotent_and_run_forever_is_reentrant() -> None:
    wrapped: Any = asyncio.base_events.BaseEventLoop.run_forever
    loop_watch.install()
    assert asyncio.base_events.BaseEventLoop.run_forever is wrapped
    loop: asyncio.AbstractEventLoop = asyncio.new_event_loop()
    try:  # run_until_complete roept run_forever elke keer opnieuw aan
        assert loop.run_until_complete(asyncio.sleep(0, "a")) == "a"
        assert loop.run_until_complete(asyncio.sleep(0, "b")) == "b"
    finally:
        loop.close()


def test_a_block_in_the_very_first_iteration_is_reported_with_its_app_frame() -> None:
    loop_watch.drain()

    async def block_at_once() -> None:
        blocking_in_the_app()

    asyncio.run(block_at_once())
    stalls: list[loop_guard.Stall] = counted_after(loop_watch.drain())
    assert stalls
    assert stalls[0].app_frames[-1].path == APP_FILE
