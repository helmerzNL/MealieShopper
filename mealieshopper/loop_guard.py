"""Meet vanuit een eigen thread wanneer de event loop stilstaat, en welke regels er toen liepen.

Regel van het project: de server blokkeert de event loop nooit langer dan 100 ms, want elke andere aanvraag wacht mee. Een
taak op de loop kan zichzelf niet betrappen (die loopt dan juist niet), dus de bewaker draait in een thread:

- Elke `interval` seconden stuurt de thread een tik naar de loop (`call_soon_threadsafe`) en wacht tot die is beantwoord.
- Blijft een tik `threshold` seconden onbeantwoord, dan leest de thread eenmalig de stack van de thread van de loop
  (`sys._current_frames`). Dat is het moment dat de loop nog vastzit, dus de stack wijst naar de regel die blokkeert.
- Zodra de loop de tik alsnog beantwoordt, roept de thread `on_stall` aan met de hele duur en die stack.

`on_stall` loopt in de bewakerthread, niet op de loop. Een fout daarin wordt gelogd en stopt de bewaker niet.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass

APP_DIR: str = os.path.dirname(os.path.abspath(__file__))
EXEMPT_FUNCTIONS: frozenset[str] = frozenset(
    {"create_app", "_startup"}
)  # opstarten telt niet
THREAD_NAME: str = "loop-guard"
JOIN_TIMEOUT: float = 2.0

logger: logging.Logger = logging.getLogger("mealieshopper.loop_guard")


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
class Stall:
    """Een keer dat de event loop `seconds` stilstond, met de stack van buiten naar binnen (de laatste regel liep)."""

    seconds: float
    stack: tuple[Frame, ...]

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

    def describe(self, limit: int = 6) -> str:
        """Een regel voor het log: de duur en de binnenste `limit` regels, van binnen naar buiten."""
        frames: tuple[Frame, ...] = self.app_frames or self.stack
        shown: list[str] = [frame.short() for frame in reversed(frames[-limit:])]
        return f"{round(self.seconds * 1000)} ms: " + " < ".join(shown)


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

    def start(self) -> None:
        """Start de thread. Nog eens aanroepen doet niets."""
        if self._thread is not None:
            return
        self._loop_thread = self._own_thread_if_running()
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

    def _capture(self) -> tuple[Frame, ...]:
        thread_id: int | None = self._loop_thread
        frame = sys._current_frames().get(thread_id) if thread_id else None
        if frame is None:
            return ()
        return tuple(
            Frame(entry.filename, entry.lineno or 0, entry.name)
            for entry in traceback.extract_stack(frame)
        )

    def _report(self, seconds: float, stack: tuple[Frame, ...]) -> None:
        try:
            self._on_stall(Stall(seconds, stack))
        except Exception:
            logger.exception("on_stall van de event-loopbewaker faalde")

    def _run(self) -> None:
        pending_since: float | None = None
        stack: tuple[Frame, ...] | None = None
        while not self._stop.wait(self._interval):
            now: float = time.perf_counter()
            if pending_since is None:
                if not self._send_tick():
                    return
                pending_since = now
            elif self._answered.is_set():
                seconds: float = self._answered_at - pending_since
                if stack is not None or seconds >= self._threshold:
                    self._report(seconds, stack or ())
                pending_since, stack = None, None
            elif stack is None and now - pending_since >= self._threshold:
                stack = self._capture()
