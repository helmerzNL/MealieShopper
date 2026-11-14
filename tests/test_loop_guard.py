"""De bewaker die stilstand van de event loop meet met de stack (mealieshopper/loop_guard.py).

Regel van het project: de server blokkeert de event loop nooit langer dan 100 ms. De bewaker meet vanuit een eigen thread
wanneer dat toch gebeurt en welke regels er toen liepen. De tests blokkeren de loop met opzet met `time.sleep`.
"""

from __future__ import annotations

import asyncio
import linecache
import os
import sys
import threading
import time
import traceback
from typing import Any

import pytest

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


async def test_a_slow_report_is_not_counted_as_the_next_stall() -> None:
    """Taak 9c: onder hypercorn kwamen twee stilstanden die elkaar overlapten, 101 en 226 ms. De tweede was het melden van de
    eerste: de nieuwe tik kreeg de tijd van voor het melden mee. Een melding die 0,3 s duurt, geeft geen tweede stilstand."""
    stalls: list[loop_guard.Stall] = []

    def slow(stall: loop_guard.Stall) -> None:
        stalls.append(stall)
        time.sleep(0.3)

    guard: loop_guard.LoopGuard = loop_guard.LoopGuard(asyncio.get_running_loop(), 0.05, slow)
    guard.start()
    time.sleep(0.15)  # noqa: ASYNC251
    await asyncio.sleep(0.6)  # de loop wacht, terwijl de bewaker meldt
    guard.stop()
    assert len(stalls) == 1, [stall.seconds for stall in stalls]


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
        0.3,
        (
            loop_guard.Frame("/usr/lib/x.py", 1, "run"),
            loop_guard.Frame(APP_PATH, 5, "_catalogue"),
        ),
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


async def test_a_block_of_threshold_plus_one_and_a_half_intervals_is_always_reported() -> (
    None
):
    """Een tik per twee wakes liet een blinde plek van twee intervallen: 70 ms bij een drempel van 40 ms viel erdoorheen."""
    stalls: list[loop_guard.Stall] = []
    guard: loop_guard.LoopGuard = loop_guard.LoopGuard(
        asyncio.get_running_loop(), 0.04, stalls.append, interval=0.02
    )
    guard.start()
    for step in range(8):
        await asyncio.sleep(0.06 + step * 0.0037)  # elke keer een andere fase
        time.sleep(0.07)  # noqa: ASYNC251
    await asyncio.sleep(0.1)
    guard.stop()
    # Minstens acht: geen blok valt erdoorheen. Een tragere machine (een runner van CI) kan er een stilstand bij hebben, als een
    # `asyncio.sleep` daar zelf langer dan de drempel uitloopt, dus niet precies acht.
    assert len(stalls) >= 8, [round(stall.seconds, 3) for stall in stalls]


async def test_stop_right_after_the_block_still_reports_it() -> None:
    stalls: list[loop_guard.Stall] = []
    guard: loop_guard.LoopGuard = loop_guard.LoopGuard(
        asyncio.get_running_loop(), 0.05, stalls.append
    )
    guard.start()
    time.sleep(0.2)  # noqa: ASYNC251
    await asyncio.sleep(
        0
    )  # de loop beantwoordt de tik, de thread heeft het nog niet gezien
    guard.stop()
    assert len(stalls) == 1


def test_a_stall_in_the_first_iteration_has_a_stack_when_the_loop_thread_is_given() -> (
    None
):
    stalls: list[loop_guard.Stall] = []
    loop: asyncio.AbstractEventLoop = asyncio.new_event_loop()
    guard: loop_guard.LoopGuard = loop_guard.LoopGuard(loop, 0.05, stalls.append)

    async def block_at_once() -> None:
        time.sleep(0.2)  # noqa: ASYNC251
        await asyncio.sleep(0.05)

    try:
        guard.start(
            loop_thread=threading.get_ident()
        )  # de loop draait nog niet: zonder dit is de stack leeg
        loop.run_until_complete(block_at_once())
    finally:
        guard.stop()
        loop.close()
    assert len(stalls) == 1
    assert any(frame.function == "block_at_once" for frame in stalls[0].stack)


# --- de andere threads ------------------------------------------------------------------------------------------------

HOLD_COUNT: int = 50_000_000  # `sum(range(...))` is een aanroep in C die de GIL zo'n 300 ms vasthoudt


def hold_the_gil(start: threading.Event, done: list[bool]) -> None:
    """Wacht op `start`, houdt dan de GIL vast met een lange aanroep in C (Python wisselt daarin niet van thread) en rekent daarna
    in Python door tot `done[0]` (zonder aanroep, die zou een eigen regel geven): zo staat de thread nog in deze functie als de bewaker de GIL terugkrijgt."""
    start.wait(5)
    time.sleep(0.03)  # de loop slaapt dan al, en de tik van de bewaker staat open
    sum(range(HOLD_COUNT))
    while not done[0]:
        pass


async def test_a_thread_holding_the_gil_is_reported_with_its_frame() -> None:
    """De loop wil verder, maar een andere thread houdt de GIL vast: de stilstand noemt die thread en zijn regel."""
    stalls: list[loop_guard.Stall] = []
    start: threading.Event = threading.Event()
    done: list[bool] = [False]
    holder: threading.Thread = threading.Thread(target=hold_the_gil, args=(start, done), name="holder")
    holder.start()
    guard: loop_guard.LoopGuard = loop_guard.LoopGuard(asyncio.get_running_loop(), 0.1, stalls.append)
    guard.start()
    try:
        await asyncio.sleep(0.05)
        start.set()
        time.sleep(0.05)  # noqa: ASYNC251 - geeft de GIL vrij: de loop blokkeert niet zelf, hij wil daarna verder
        await asyncio.sleep(0.05)
    finally:
        guard.stop()
        done[0] = True
        holder.join()
    stall: loop_guard.Stall = max(stalls, key=lambda found: found.seconds)
    assert stall.seconds >= 0.15
    holders: list[loop_guard.ThreadStack] = [thread for thread in stall.busy_threads if thread.name == "holder"]
    assert holders and holders[0].stack[-1].function == "hold_the_gil"
    assert "Andere threads: holder test_loop_guard.py:" in stall.describe()


async def test_the_other_threads_are_read_when_the_loop_waits() -> (
    None
):
    """De bewaker leest de andere threads als de loop geen regel van de app draait."""
    stalls: list[loop_guard.Stall] = []
    stop: threading.Event = threading.Event()

    def spin() -> None:
        while not stop.is_set():
            sum(range(1000))  # rekent in Python: de GIL wisselt tussen de bytecodes

    spinner: threading.Thread = threading.Thread(target=spin, name="spinner")
    spinner.start()
    guard: loop_guard.LoopGuard = loop_guard.LoopGuard(
        asyncio.get_running_loop(), 0.05, stalls.append
    )
    guard.start()
    try:
        time.sleep(0.2)  # noqa: ASYNC251
        await asyncio.sleep(0.05)
    finally:
        guard.stop()
        stop.set()
        spinner.join()
    assert len(stalls) == 1
    names: list[str] = [thread.name for thread in stalls[0].busy_threads]
    assert "spinner" in names
    assert loop_guard.THREAD_NAME not in [thread.name for thread in stalls[0].threads]


@pytest.mark.may_block("de test blokkeert de loop met opzet, met zichzelf als de app")
async def test_a_loop_stuck_in_a_call_outside_the_app_lists_the_busy_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    """Eindreview: het konvooi. De loop staat in een aanroep buiten de app die de app deed (hier `Event.wait`, in de app een
    `Path.resolve` of `Thread.start`), terwijl een andere thread rekent: de melding noemt die thread. De bewaker las de andere
    threads alleen als de loop wachtte, en de docstring beloofde ze ook hier."""
    monkeypatch.setattr(loop_guard, "APP_DIR", os.path.dirname(os.path.abspath(__file__)))  # deze test is hier de app
    stalls: list[loop_guard.Stall] = []
    stop: threading.Event = threading.Event()

    def spin() -> None:
        while not stop.is_set():
            sum(range(1000))  # rekent in Python: de GIL wisselt tussen de bytecodes

    spinner: threading.Thread = threading.Thread(target=spin, name="spinner")
    spinner.start()
    guard: loop_guard.LoopGuard = loop_guard.LoopGuard(asyncio.get_running_loop(), 0.05, stalls.append)
    guard.start()
    try:
        threading.Event().wait(0.2)  # de binnenste regel staat in threading.py, de app riep hem aan
        await asyncio.sleep(0.05)
    finally:
        guard.stop()
        stop.set()
        spinner.join()
    assert len(stalls) == 1
    assert stalls[0].in_app and os.path.basename(stalls[0].stack[-1].path) == "threading.py"
    assert "spinner" in [thread.name for thread in stalls[0].busy_threads]
    assert "Andere threads: " in stalls[0].describe()


def test_a_loop_busy_in_the_app_lists_no_other_threads() -> None:
    selector: loop_guard.Frame = loop_guard.Frame(
        "/usr/lib/python3.12/selectors.py", 566, "select"
    )
    app: loop_guard.Frame = loop_guard.Frame(APP_PATH, 5, "_catalogue")
    assert not loop_guard.loop_waiting((selector, app))
    assert loop_guard.loop_waiting((app, selector))
    assert loop_guard.loop_waiting((selector,))
    assert loop_guard.loop_waiting(())


def line_of(path: str, text: str) -> int:
    """Het nummer van de eerste regel van `path` met `text`: bij een wachtrij in C kijkt de bewaker ook naar de regel."""
    with open(path, encoding="utf-8") as source:
        return next(number for number, line in enumerate(source, 1) if text in line)


def test_describe_names_the_busy_threads_and_counts_the_waiting_ones() -> None:
    compute_path: str = os.path.join(loop_guard.APP_DIR, "compute.py")
    busy: loop_guard.ThreadStack = loop_guard.ThreadStack(
        "compute-0",
        (
            loop_guard.Frame(compute_path, 70, "_work"),
            loop_guard.Frame("/app/mealieshopper/prices.py", 1234, "_rows"),
        ),
    )
    idle: loop_guard.ThreadStack = loop_guard.ThreadStack(
        "compute-1",
        (loop_guard.Frame(compute_path, line_of(compute_path, "self._jobs.get()"), "_work"),),
    )
    waiting: loop_guard.ThreadStack = loop_guard.ThreadStack(
        "Thread-3 (_connection_worker_thread)",
        (loop_guard.Frame(threading.__file__, line_of(threading.__file__, "waiter.acquire()"), "wait"),),
    )
    stall: loop_guard.Stall = loop_guard.Stall(0.145, (), (busy, idle, waiting))
    assert stall.busy_threads == (busy,)
    assert (
        stall.describe()
        == "145 ms: . Andere threads: compute-0 prices.py:1234 _rows < compute.py:70 _work (2 wachtend)"
    )
    assert (
        loop_guard.Stall(0.2, (), (idle,)).describe()
        == "200 ms: . Andere threads: geen bezig (1 wachtend)"
    )
    assert loop_guard.Stall(0.2, ()).describe() == "200 ms: "


def _stuck_until(release: threading.Event, ready: threading.Event) -> None:
    """De "loop" van de test hieronder: staat stil in `Event.wait`, zoals een loop die op de GIL wacht."""
    ready.set()
    release.wait()


def test_looking_during_a_stall_reads_no_files(monkeypatch: pytest.MonkeyPatch) -> None:
    """Taak 10b: `_look` loopt terwijl de loop vastzit, en las de stacks met `traceback.extract_stack`. Dat roept per bestand
    `linecache.checkcache` aan (een stat) en leest de regels uit het bestand: elke stat laat de GIL los, en met een drukke
    rekenthread kostte `_look` zo 25 tot 45 ms, binnen de stilstand die de bewaker meet. Nu legt `_look` alleen de bestanden,
    regelnummers en functies vast, en maakt de melding ze pas op na het antwoord van de loop."""
    release: threading.Event = threading.Event()
    ready: threading.Event = threading.Event()
    stuck: threading.Thread = threading.Thread(target=_stuck_until, args=(release, ready), name="vast")
    other: threading.Thread = threading.Thread(target=release.wait, name="ander")
    stuck.start()
    other.start()
    ready.wait()
    loop: asyncio.AbstractEventLoop = asyncio.new_event_loop()
    guard: loop_guard.LoopGuard = loop_guard.LoopGuard(loop, 0.05, lambda stall: None)
    guard._loop_thread = stuck.ident
    tick: loop_guard._Tick = loop_guard._Tick(time.perf_counter())

    def no_files(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("de bewaker las een bestand terwijl de loop vastzat")

    try:
        with monkeypatch.context() as patched:
            for name in ("checkcache", "getline", "getlines", "updatecache", "lazycache"):
                patched.setattr(linecache, name, no_files)
            guard._look(tick)
        stack, threads = guard._witnesses(tick)
        assert stuck.ident is not None
        expected: list[tuple[str, int, str]] = [
            (entry.filename, entry.lineno or 0, entry.name) for entry in traceback.extract_stack(sys._current_frames()[stuck.ident])
        ]
    finally:
        release.set()
        stuck.join()
        other.join()
        loop.close()
    assert [(frame.path, frame.line, frame.function) for frame in stack] == expected
    assert "_stuck_until" in [frame.function for frame in stack]
    names: list[str] = [thread.name for thread in threads]
    assert "ander" in names and "vast" not in names, names  # de loop zelf hoort niet bij de andere threads


def test_a_busy_thread_on_a_lookup_line_is_not_taken_for_waiting() -> None:
    """Review van taak 9c: de bewaker zocht `.get(` in de tekst van de binnenste regel, dus een thread die in een lus `x = d.get(k)`
    deed, telde als wachtend en verdween uit het log. Nu telt alleen een functie die echt wacht (`WAITING_FRAMES`)."""
    found: dict[str, int] = {"melk": 1}
    started: threading.Event = threading.Event()
    done: list[bool] = []

    def look_up() -> None:
        started.set()
        while not done:
            x = found.get("melk")  # noqa: F841 - de regel waar de thread meestal staat

    worker: threading.Thread = threading.Thread(target=look_up, name="opzoeker")
    worker.start()
    started.wait()
    seen: list[loop_guard.ThreadStack] = []
    try:
        for _ in range(200):  # de thread staat afwisselend op de regel van de lus en op die van `found.get`
            # Even wachten: het lezen leest sinds taak 10b geen bestanden meer en laat de GIL dus niet los, en zonder pauze lezen
            # de 200 rondes allemaal dezelfde stilstaande regel als de machine druk is.
            time.sleep(0.001)
            threads: dict[int, loop_guard.ThreadStack] = loop_guard.current_threads([threading.get_ident()])
            seen += [thread for thread in threads.values() if thread.name == "opzoeker"]
    finally:
        done.append(True)
        worker.join()
    lookup: int = look_up.__code__.co_firstlineno + 3
    assert any(thread.stack[-1].line == lookup for thread in seen), "de thread stond nooit op de regel met `found.get`"
    assert all(thread.stack[-1].function == "look_up" for thread in seen)
    assert not [thread for thread in seen if thread.idle]
    assert loop_guard.Stall(0.2, (), (seen[-1],)).busy_threads == (seen[-1],)


def test_a_c_call_on_the_work_line_of_a_queue_loop_is_busy() -> None:
    """compute.py wacht en werkt in dezelfde functie: op de regel met de wachtrij wacht hij, op de regel van het werk (een aanroep
    in C zonder eigen regel op de stack, zoals een grote `sorted`) is hij bezig."""
    compute_path: str = os.path.join(loop_guard.APP_DIR, "compute.py")
    working: loop_guard.ThreadStack = loop_guard.ThreadStack(
        "compute-0", (loop_guard.Frame(compute_path, line_of(compute_path, "future.set_result(call())"), "_work"),)
    )
    waiting: loop_guard.ThreadStack = loop_guard.ThreadStack(
        "compute-1", (loop_guard.Frame(compute_path, line_of(compute_path, "self._jobs.get()"), "_work"),)
    )
    elsewhere: loop_guard.ThreadStack = loop_guard.ThreadStack(
        "Thread-9", (loop_guard.Frame("/app/vendor/compute.py", line_of(compute_path, "self._jobs.get()"), "_work"),)
    )
    assert (working.idle, waiting.idle, elsewhere.idle) == (False, True, False)
