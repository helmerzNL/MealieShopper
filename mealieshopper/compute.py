"""Rekenwerk buiten de event loop, en een bouw per sleutel tegelijk.

Regel van het project: de server houdt de event loop nooit langer dan 100 ms vast. Een lus over duizenden rijen, een regex over
veel namen of een grote sortering staat daarom in een pure synchrone functie, en `run` laat die in een eigen thread lopen terwijl
de loop doorgaat met andere aanvragen. De functie leest en schrijft geen cache in een module: de aanroeper bewaart de uitkomst.

De pool is van de module en niet van een event loop, want de tests starten veel loops en apps in een proces. De threads zijn
daemonthreads, zodat een functie die blijft hangen het afsluiten van het proces niet tegenhoudt.
"""

from __future__ import annotations

import asyncio
import functools
import queue
import threading
import time
from collections.abc import Awaitable, Callable, Hashable
from concurrent.futures import Future
from typing import Generic, ParamSpec, TypeVar

from mealieshopper import telemetry

P = ParamSpec("P")
T = TypeVar("T")
K = TypeVar("K", bound=Hashable)
V = TypeVar("V")

WORKERS: int = 2
SHUTDOWN_SECONDS: float = (
    5.0  # zo lang wacht `shutdown` hooguit op threads die nog bezig zijn
)
PHASE: str = "compute"

_Job = tuple["Future[object]", Callable[[], object]]


class _Pool:
    """`WORKERS` daemonthreads die opdrachten uit een wachtrij halen. Een opdracht die nog niet begonnen is, valt te annuleren."""

    def __init__(self) -> None:
        self._jobs: queue.SimpleQueue[_Job | None] = queue.SimpleQueue()
        self._threads: list[threading.Thread] = [
            threading.Thread(target=self._work, name=f"compute-{number}", daemon=True)
            for number in range(WORKERS)
        ]
        for thread in self._threads:
            thread.start()

    def submit(self, call: Callable[[], T]) -> Future[T]:
        future: Future[T] = Future()
        self._jobs.put((future, call))  # type: ignore[arg-type]
        return future

    def _work(self) -> None:
        while True:
            job: _Job | None = self._jobs.get()
            if job is None:
                return
            future, call = job
            if not future.set_running_or_notify_cancel():
                continue  # de aanroeper is al weg
            try:
                future.set_result(call())
            except BaseException as exc:  # noqa: BLE001 - de aanroeper krijgt de fout
                future.set_exception(exc)

    def stop(self, seconds: float) -> None:
        """Annuleert wat nog wacht en wacht hooguit `seconds` op wat loopt. Een functie die dan nog loopt, kan niet gestopt worden."""
        while True:
            try:
                job: _Job | None = self._jobs.get_nowait()
            except queue.Empty:
                break
            if job is not None:
                job[0].cancel()
        for _ in self._threads:
            self._jobs.put(None)
        deadline: float = time.monotonic() + seconds
        for thread in self._threads:
            thread.join(max(0.0, deadline - time.monotonic()))


_pool: _Pool | None = None
_pool_lock: threading.Lock = threading.Lock()


def _get_pool() -> _Pool:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = _Pool()
        return _pool


async def run(function: Callable[P, T], /, *args: P.args, **kwargs: P.kwargs) -> T:
    """Draait `function(*args, **kwargs)` in de eigen threadpool en wacht erop zonder de loop vast te houden.

    De tijd telt als fase `compute` in de telemetry, gemeten aan de kant van de loop (het wachten), zodat er geen ContextVar
    de thread in hoeft. Een fout van de functie komt hier terug. Wordt het wachten geannuleerd, dan loopt een functie die al
    begonnen is af, en een functie die nog in de wachtrij stond begint niet meer.
    """
    future: Future[T] = _get_pool().submit(functools.partial(function, *args, **kwargs))
    with telemetry.span(PHASE):
        return await asyncio.wrap_future(future)


def shutdown() -> None:
    """Stopt de pool en wacht hooguit `SHUTDOWN_SECONDS` op lopend werk. De volgende `run` maakt een nieuwe pool.

    Wat nog in de wachtrij staat, wordt geannuleerd. Een functie die na de wachttijd nog loopt, kan Python niet doden: die
    thread loopt af op de achtergrond (hij is een daemonthread, dus hij houdt het proces niet vast) en levert zijn uitkomst
    aan wie er nog op wacht.
    """
    global _pool
    with _pool_lock:
        pool: _Pool | None = _pool
        _pool = None
    if pool is not None:
        pool.stop(SHUTDOWN_SECONDS)


class SingleFlight(Generic[K, V]):
    """Een bouw per sleutel tegelijk: wie dezelfde sleutel vraagt terwijl er een bouw loopt, wacht op diezelfde uitkomst.

    Er blijft niets over na de bouw: de aanroeper schrijft de uitkomst zelf in zijn cache. De bouw is een eigen taak die achter
    `asyncio.shield` staat, dus een aanroeper die geannuleerd wordt haalt de bouw voor de anderen niet neer. Een bouw die
    mislukt nadat de laatste aanroeper weg is, geeft geen waarschuwing over een fout die nooit is opgehaald.
    """

    def __init__(self) -> None:
        self._flights: dict[K, asyncio.Future[V]] = {}

    async def get(self, key: K, build: Callable[[], Awaitable[V]]) -> V:
        flight: asyncio.Future[V] | None = self._flights.get(key)
        if flight is None:
            flight = asyncio.ensure_future(build())
            self._flights[key] = flight
            flight.add_done_callback(functools.partial(self._finished, key))
        return await asyncio.shield(flight)

    def _finished(self, key: K, flight: asyncio.Future[V]) -> None:
        """Haalt de bouw uit de lijst, nog voor de wachters verder gaan, en leest de fout zodat die nooit "niet opgehaald" is."""
        if self._flights.get(key) is flight:
            del self._flights[key]
        if not flight.cancelled():
            flight.exception()
