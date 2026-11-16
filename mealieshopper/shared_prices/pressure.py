"""Give foreground requests priority without disk writes or telemetry settings."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass

from mealieshopper import tasks

LATENCY_SECONDS: float = 0.1
COOLDOWN_SECONDS: float = 1.0
_current: ContextVar[ForegroundRequest | None] = ContextVar("shared_prices_foreground", default=None)


@dataclass(frozen=True)
class ForegroundRequest:
    identifier: int
    started: float


class BackgroundCancelled(Exception):
    """A cancelled updater must leave a cooperative thread wait promptly."""


class Pressure:
    def __init__(self, clock: Callable[[], float] = time.monotonic, cooldown_seconds: float = COOLDOWN_SECONDS) -> None:
        self._clock: Callable[[], float] = clock
        self._cooldown: float = cooldown_seconds
        self._condition: threading.Condition = threading.Condition()
        self._active: set[ForegroundRequest] = set()
        self._next: int = 0
        self._cool_until: float = 0.0

    def started(self) -> ForegroundRequest:
        with self._condition:
            self._next += 1
            token: ForegroundRequest = ForegroundRequest(self._next, self._clock())
            self._active.add(token)
            self._condition.notify_all()
        _current.set(token)
        return token

    def finished(self, token: ForegroundRequest) -> None:
        with self._condition:
            if token not in self._active:
                return
            self._active.remove(token)
            now: float = self._clock()
            if now - token.started > LATENCY_SECONDS:
                self._cool_until = max(self._cool_until, now + self._cooldown)
            self._condition.notify_all()
        if _current.get() == token:
            _current.set(None)

    def delay(self, excluded: ForegroundRequest | None = None) -> float | None:
        with self._condition:
            if any(token != excluded for token in self._active):
                return None
            return max(0.0, self._cool_until - self._clock())

    def wait(self, cancelled: threading.Event, excluded: ForegroundRequest | None) -> None:
        with self._condition:
            while True:
                if cancelled.is_set():
                    raise BackgroundCancelled
                delay: float | None = self.delay(excluded)
                if delay == 0:
                    return
                self._condition.wait(timeout=delay)

    def wake(self) -> None:
        with self._condition:
            self._condition.notify_all()


_pressure: Pressure = Pressure()


class Budget:
    """One updater's cancellation and foreground exclusion, shared with its worker."""

    def __init__(self) -> None:
        self._pressure: Pressure = _pressure
        self._excluded: ForegroundRequest | None = _current.get()
        self._cancelled: threading.Event = threading.Event()

    def checkpoint(self) -> None:
        self._pressure.wait(self._cancelled, self._excluded)

    def cancel(self) -> None:
        self._cancelled.set()
        self._pressure.wake()

    def progress(self) -> int:
        try:
            self.checkpoint()
        except BackgroundCancelled:
            return 1
        return 0


def foreground_started() -> ForegroundRequest:
    return _pressure.started()


def foreground_finished(token: ForegroundRequest) -> None:
    _pressure.finished(token)


async def yield_background() -> None:
    """Wait between background batches, outside transactions and foreground locks."""
    budget: Budget = Budget()
    work: asyncio.Task[None] = asyncio.create_task(asyncio.to_thread(budget.checkpoint))
    try:
        await tasks.share(work)
    except asyncio.CancelledError:
        budget.cancel()
        try:
            await work
        except BackgroundCancelled:
            pass
        raise
