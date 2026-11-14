"""Meet vanuit een eigen thread wanneer de event loop stilstaat, en welke regels er toen liepen.

Regel van het project: de server blokkeert de event loop nooit langer dan 100 ms, want elke andere aanvraag wacht mee. Een
taak op de loop kan zichzelf niet betrappen (die loopt dan juist niet), dus de bewaker draait in een thread:

- Elke `interval` seconden stuurt de thread een tik naar de loop (`call_soon_threadsafe`) en wacht tot die is beantwoord.
- Blijft een tik `threshold` seconden onbeantwoord, dan leest de thread eenmalig de stack van de thread van de loop
  (`sys._current_frames`). Dat is het moment dat de loop nog vastzit, dus de stack wijst naar de regel die blokkeert.
- Zodra de loop de tik alsnog beantwoordt, roept de thread `on_stall` aan met de hele duur en die stack.

Wacht de loop zelf (de binnenste regel staat in de selector, of er staat geen regel van de app op de stack), dan hield iets
anders hem tegen: meestal een andere thread die de GIL vasthoudt met een lange aanroep in C, zoals een grote `sorted`, of een
volle ronde van de garbage collector (die staat op geen stack, zie gc_watch.py). Python wisselt pas tussen twee bytecodes van
thread, dus zo'n aanroep houdt alle andere threads tegen, de loop en de bewaker ook. Een `fetchall` hoort er niet bij: sqlite3
laat de GIL los bij elke stap. De bewaker leest dan ook de stacks van de andere threads (`Stall.threads`, met `sys._current_frames`),
zodra hij zelf aan de beurt is: bij het lezen van de stack van de loop, of anders meteen bij het antwoord van de loop. De thread
die de GIL had, staat dan nog bij de regel na zijn lange aanroep, of is al klaar en wacht weer. Een stilstand die begint terwijl
er geen tik openstaat, ziet de bewaker niet: hij kan zelf pas weer een tik sturen als hij de GIL terug heeft.

Er is een tweede manier waarop een andere thread de loop tegenhoudt, zonder zelf lang de GIL te houden: het konvooi. Laat de
loop de GIL los in een systeemaanroep (een stat, een lstat, `Thread.start`, de select van de selector) terwijl een rekenthread
of een thread van aiosqlite Python draait, dan krijgt de loop hem pas terug als die thread hem afstaat, na het wisselinterval
(`sys.getswitchinterval()`, 5 ms). Elke aanroep die de GIL loslaat kost de loop dan tot 5 ms, en een pad met twintig van zulke
aanroepen stond onder belasting 100 tot 200 ms stil. De stack van de loop staat dan in die aanroep, en een andere thread is bezig
(`Stall.threads`). Gevonden in taak 9c: `Path.resolve` in `auth.data_dir` (een lstat per deel van het pad, bij elke
verbinding), de mislukte `import sniffio` van httpcore (een stat per map van sys.path, bij elke verbinding), en `Thread.start`
in `aiosqlite.connect` (een thread per verbinding, ook per auditregel). Daarom zoekt de controle van taak 10 op de loop naar
aanroepen die de GIL loslaten en vaak terugkomen, niet alleen naar lang rekenwerk. Sinds taak 9c zet `app.create_app` het
wisselinterval op 1 ms (`app.SWITCH_INTERVAL_SECONDS`, met de metingen): dat maakt elke overdracht korter, maar haalt het
konvooi niet weg, want tien aanroepen kosten dan nog tot 10 ms.

Wat de bewaker leest terwijl de loop vastzit, legt hij vast zonder bestanden te lezen: per regel het bestand, het
regelnummer en de functie (`_raw_frames`). `traceback.extract_stack` deed per bestand een stat (`linecache.checkcache`) en las
de regels, en met een drukke rekenthread kostte dat lezen 25 tot 45 ms binnen de stilstand die hij meet (taak 10b). Opgemaakt
(`Frame`, `ThreadStack`, de tekst van een regel voor `ThreadStack.idle`) wordt pas bij de melding, na het antwoord van de loop.

`on_stall` loopt in de bewakerthread, niet op de loop. Een fout daarin wordt gelogd en stopt de bewaker niet.
"""

from __future__ import annotations

import asyncio
import linecache
import logging
import os
import sys
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from types import FrameType
from typing import TypeAlias

APP_DIR: str = os.path.dirname(os.path.abspath(__file__))
EXEMPT_FUNCTIONS: frozenset[str] = frozenset(
    {"create_app", "_startup"}
)  # opstarten telt niet
THREAD_NAME: str = "loop-guard"
JOIN_TIMEOUT: float = 2.0
# Een loop met zijn binnenste regel in een van deze bestanden wacht op de selector (of op de GIL om daaruit terug te komen).
SELECTOR_FILES: frozenset[str] = frozenset({"selectors.py", "selector_events.py"})
# Een thread waarvan de binnenste regel in zo'n functie staat, wacht op werk, een slot of de selector en hield de GIL dus niet
# vast: (het einde van het pad, de functie) -> de aanroep die op die regel moet staan, of "" voor elke regel van de functie. Een
# wachtrij in C (`SimpleQueue.get`: aiosqlite, compute.py en de threadpool van asyncio) heeft geen eigen regel op de stack, dus
# daar telt alleen de regel met de wachtrij: dezelfde functie roept ook het werk aan, en een aanroep in C (een grote `sorted`)
# staat dan op die andere regel. Al het andere telt als bezig, ook een `d.get(k)` of een `time.sleep` in eigen code: liever een
# thread te veel genoemd dan de thread met de GIL verstopt.
WAITING_FRAMES: dict[tuple[str, str], str] = {
    ("threading.py", "wait"): "",  # Condition.wait, en zo ook Event.wait, Semaphore.acquire en queue.Queue.get
    ("threading.py", "_wait_for_tstate_lock"): "",  # Thread.join tot en met 3.12
    ("threading.py", "join"): "",  # Thread.join vanaf 3.13, in C
    ("queue.py", "get"): "",
    ("selectors.py", "select"): "",  # kqueue.control, epoll.poll: de event loop van een andere thread
    ("concurrent/futures/thread.py", "_worker"): "work_queue.get(",
    ("aiosqlite/core.py", "_connection_worker_thread"): "tx.get()",
    ("mealieshopper/compute.py", "_work"): "self._jobs.get()",
}
THREAD_FRAMES: int = 4  # zoveel regels per andere thread noemt `describe`

logger: logging.Logger = logging.getLogger("mealieshopper.loop_guard")
# Een regel van de stack zoals de bewaker hem tijdens een stilstand vastlegt: het bestand, het regelnummer en de functie.
RawFrame: TypeAlias = tuple[str, int, str]
# Een andere thread zoals de bewaker hem tijdens een stilstand vastlegt: de naam en de regels van buiten naar binnen.
RawThread: TypeAlias = tuple[str, tuple[RawFrame, ...]]


@dataclass(frozen=True)
class Frame:
    """Een regel van de stack: het bestand, het regelnummer en de functie."""

    path: str
    line: int
    function: str

    @property
    def in_app(self) -> bool:
        return self.path.startswith(APP_DIR + os.sep)

    def short(self) -> str:
        return f"{os.path.basename(self.path)}:{self.line} {self.function}"


@dataclass(frozen=True)
class ThreadStack:
    """De stack van een andere thread tijdens een stilstand: de naam en de regels van buiten naar binnen."""

    name: str
    stack: tuple[Frame, ...]

    @property
    def idle(self) -> bool:
        """Of de thread op werk of een slot wachtte: dan hield hij de GIL niet vast."""
        if not self.stack:
            return True
        inner: Frame = self.stack[-1]
        path: str = inner.path.replace(os.sep, "/")
        for (suffix, function), call in WAITING_FRAMES.items():
            if inner.function == function and (path == suffix or path.endswith("/" + suffix)):
                return call in linecache.getline(inner.path, inner.line)
        return False

    def short(self, limit: int = THREAD_FRAMES) -> str:
        """De naam en de binnenste `limit` regels, van binnen naar buiten. Ook regels buiten de app: die liep echt."""
        return " ".join(
            [
                self.name,
                " < ".join(frame.short() for frame in reversed(self.stack[-limit:])),
            ]
        )


@dataclass(frozen=True)
class Stall:
    """Een keer dat de event loop `seconds` stilstond, met de stack van buiten naar binnen (de laatste regel liep).

    `threads` zijn de andere threads, alleen als de loop zelf wachtte (`loop_waiting`): een daarvan hield hem tegen.
    """

    seconds: float
    stack: tuple[Frame, ...]
    threads: tuple[ThreadStack, ...] = ()

    @property
    def app_frames(self) -> tuple[Frame, ...]:
        """De regels van de app zelf (onder `APP_DIR`), in dezelfde volgorde als `stack`."""
        return tuple(frame for frame in self.stack if frame.in_app)

    @property
    def in_app(self) -> bool:
        """Of de app dit veroorzaakte: er staat een regel van de app op de stack en die hoort niet bij het opstarten."""
        frames: tuple[Frame, ...] = self.app_frames
        return bool(frames) and not any(
            frame.function in EXEMPT_FUNCTIONS for frame in frames
        )

    @property
    def busy_threads(self) -> tuple[ThreadStack, ...]:
        """De andere threads die niet wachtten: wie de GIL had, staat hiertussen."""
        return tuple(thread for thread in self.threads if not thread.idle)

    def describe(self, limit: int = 6) -> str:
        """Een regel voor het log: de duur en de binnenste `limit` regels, van binnen naar buiten, en dan de andere threads."""
        frames: tuple[Frame, ...] = self.app_frames or self.stack
        shown: list[str] = [frame.short() for frame in reversed(frames[-limit:])]
        line: str = f"{round(self.seconds * 1000)} ms: " + " < ".join(shown)
        return line + self._describe_threads()

    def _describe_threads(self) -> str:
        if not self.threads:
            return ""
        busy: tuple[ThreadStack, ...] = self.busy_threads
        idle: int = len(self.threads) - len(busy)
        shown: str = " | ".join(thread.short() for thread in busy) or "geen bezig"
        return f". Andere threads: {shown}" + (f" ({idle} wachtend)" if idle else "")


def _waiting(paths: tuple[str, ...]) -> bool:
    """`loop_waiting` op de bestanden van de stack, van buiten naar binnen."""
    if any(path.startswith(APP_DIR + os.sep) for path in paths):
        return os.path.basename(paths[-1]) in SELECTOR_FILES
    return True


def loop_waiting(stack: tuple[Frame, ...]) -> bool:
    """Of de loop zelf niets deed: de binnenste regel staat in de selector, of er staat geen regel van de app op de stack."""
    return _waiting(tuple(frame.path for frame in stack))


def _raw_frames(frame: FrameType | None) -> tuple[RawFrame, ...]:
    """De stack vanaf `frame`, van buiten naar binnen, zoals `traceback.extract_stack` hem geeft maar zonder de tekst van de regels:
    alleen attributen van de frames, geen linecache. Dit loopt terwijl de loop vastzit (de module-docstring)."""
    found: list[RawFrame] = []
    while frame is not None:
        found.append((frame.f_code.co_filename, frame.f_lineno or 0, frame.f_code.co_name))
        frame = frame.f_back
    found.reverse()
    return tuple(found)


def _frames(raw: tuple[RawFrame, ...]) -> tuple[Frame, ...]:
    return tuple(Frame(path, line, function) for path, line, function in raw)


def _thread_names() -> dict[int, str]:
    return {
        thread.ident: thread.name
        for thread in threading.enumerate()
        if thread.ident is not None
    }


def _raw_threads(skip: Iterable[int | None]) -> dict[int, RawThread]:
    """De naam en de stack (`_raw_frames`) van elke levende thread nu, behalve `skip`, per id van de thread."""
    names: dict[int, str] = _thread_names()
    skipped: set[int | None] = set(skip)
    return {
        ident: (names.get(ident, hex(ident)), _raw_frames(frame))
        for ident, frame in sys._current_frames().items()
        if ident not in skipped
    }


def current_threads(skip: Iterable[int | None]) -> dict[int, ThreadStack]:
    """De stack van elke levende thread nu, behalve `skip`, per id van de thread."""
    return {ident: ThreadStack(name, _frames(raw)) for ident, (name, raw) in _raw_threads(skip).items()}


def _by_name(threads: Iterable[RawThread]) -> tuple[ThreadStack, ...]:
    """De threads op naam, zonder bewakers: die van een andere loop houdt de GIL niet vast."""
    return tuple(
        ThreadStack(name, _frames(raw))
        for name, raw in sorted(threads, key=lambda thread: thread[0])
        if name != THREAD_NAME
    )


@dataclass
class _Tick:
    """Een tik die nog niet is afgehandeld: wanneer hij verstuurd is, en wat de bewaker zelf zag terwijl hij openstond (nog niet
    opgemaakt: `_raw_frames`)."""

    sent: float
    stack: tuple[RawFrame, ...] | None = None
    threads: tuple[RawThread, ...] = ()


class LoopGuard:
    """Bewaakt een event loop vanuit een daemon-thread en meldt elke stilstand van `threshold` seconden of langer."""

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        threshold: float,
        on_stall: Callable[[Stall], None],
        interval: float = 0.01,
    ) -> None:
        self._loop: asyncio.AbstractEventLoop = loop
        self._threshold: float = threshold
        self._on_stall: Callable[[Stall], None] = on_stall
        self._interval: float = interval
        self._stop: threading.Event = threading.Event()
        self._answered: threading.Event = threading.Event()
        self._answered_at: float = 0.0
        self._loop_thread: int | None = None
        self._thread: threading.Thread | None = None

    def start(self, loop_thread: int | None = None) -> None:
        """Start de thread. Nog eens aanroepen doet niets.

        `loop_thread` is het id van de thread die de loop draait (`threading.get_ident()` daar). Geef het mee als `start()`
        wordt aangeroepen voordat de loop draait, op de thread die hem gaat draaien: dan heeft ook een stilstand in de allereerste
        ronde van de loop een stack. Zonder wordt de thread van de aanroeper genomen als die de loop al draait, anders leert de
        bewaker hem bij het eerste antwoord van de loop.
        """
        if self._thread is not None:
            return
        self._loop_thread = loop_thread if loop_thread is not None else self._own_thread_if_running()
        self._thread = threading.Thread(target=self._run, name=THREAD_NAME, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stopt de thread en wacht erop. Veilig als de loop al dicht is of de bewaker nooit startte."""
        self._stop.set()
        thread: threading.Thread | None = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(JOIN_TIMEOUT)

    def _own_thread_if_running(self) -> int | None:
        """Het id van de aanroepende thread als die de loop draait: dan weten we het al voor de eerste tik."""
        try:
            running: asyncio.AbstractEventLoop = asyncio.get_running_loop()
        except RuntimeError:
            return None
        return threading.get_ident() if running is self._loop else None

    def _answer(self) -> None:
        """Loopt op de event loop: laat de bewaker weten dat de loop weer aan de beurt was."""
        self._answered_at = time.perf_counter()
        self._loop_thread = threading.get_ident()
        self._answered.set()

    def _send_tick(self) -> bool:
        self._answered.clear()
        try:
            self._loop.call_soon_threadsafe(self._answer)
        except RuntimeError:  # de loop is dicht, er valt niets meer te bewaken
            return False
        return True

    def _capture(self) -> tuple[RawFrame, ...]:
        thread_id: int | None = self._loop_thread
        frame: FrameType | None = (
            sys._current_frames().get(thread_id) if thread_id else None
        )
        return _raw_frames(frame)

    def _others(self) -> tuple[RawThread, ...]:
        """De andere threads nu, zonder de loop en de bewaker, nog niet opgemaakt."""
        return tuple(_raw_threads((self._loop_thread, threading.get_ident())).values())

    def _look(self, tick: _Tick) -> None:
        """De tik staat `threshold` open: leg de stack van de loop vast, en de andere threads als de loop zelf wachtte. Zonder
        bestanden te lezen: de loop zit nog vast (de module-docstring)."""
        tick.stack = self._capture()
        if _waiting(tuple(path for path, _, _ in tick.stack)):
            tick.threads = self._others()

    def _witnesses(self, tick: _Tick) -> tuple[tuple[Frame, ...], tuple[ThreadStack, ...]]:
        """De stack van de loop en de andere threads voor de melding, opgemaakt. Zag de bewaker de loop niet (hij kwam zelf niet
        aan de beurt voor de loop antwoordde), dan is de stack leeg en leest hij de andere threads nu: eerder kon niet."""
        stack: tuple[Frame, ...] = _frames(tick.stack or ())
        if not loop_waiting(stack):
            return stack, ()
        return stack, _by_name(tick.threads or self._others())

    def _report(
        self, seconds: float, stack: tuple[Frame, ...], threads: tuple[ThreadStack, ...]
    ) -> None:
        try:
            self._on_stall(Stall(seconds, stack, threads))
        except Exception:
            logger.exception("on_stall van de event-loopbewaker faalde")

    def _settle(self, tick: _Tick) -> None:
        """Verwerkt een beantwoorde tik: meld de stilstand als de loop te lang wegbleef."""
        seconds: float = self._answered_at - tick.sent
        if tick.stack is not None or seconds >= self._threshold:
            self._report(seconds, *self._witnesses(tick))

    def _run(self) -> None:
        """Eén keer per `interval`: een beantwoorde tik afhandelen en meteen de volgende sturen, zodat er geen blinde tik tussen zit.

        De meting loopt van het sturen van een tik tot het antwoord, dus een stilstand die vlak na een antwoord begint, wordt
        hooguit één `interval` te kort gemeten. Bij het stoppen volgt een laatste controle, zodat een al beantwoorde maar nog
        niet gemelde stilstand niet verloren gaat.
        """
        tick: _Tick | None = None
        while not self._stop.wait(self._interval):
            now: float = time.perf_counter()
            if tick is not None and self._answered.is_set():
                self._settle(tick)
                tick = None
            if tick is None:
                # De tijd van nu, niet `now`: het melden hierboven kan lang duren (de bewaker wacht dan ook op de GIL), en die
                # tijd is geen stilstand van de loop.
                sent: float = time.perf_counter()
                if not self._send_tick():
                    return
                tick = _Tick(sent)
            elif tick.stack is None and now - tick.sent >= self._threshold:
                self._look(tick)
        if tick is not None and self._answered.is_set():
            self._settle(tick)
