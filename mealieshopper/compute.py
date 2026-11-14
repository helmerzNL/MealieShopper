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

from mealieshopper import tasks, telemetry

P = ParamSpec("P")
T = TypeVar("T")
K = TypeVar("K", bound=Hashable)
V = TypeVar("V")

WORKERS: int = 2
# Zo lang wacht `shutdown` hooguit op threads die nog bezig zijn.
SHUTDOWN_SECONDS: float = 2.5  # na `tasks.SHUTDOWN_TIMEOUT`: samen met hypercorn onder de 10 s van docker stop (tasks.py)
PHASE: str = "compute"

_Job = tuple["Future[object]", Callable[[], object]]


class PoolStopped(RuntimeError):
    """Een opdracht naar een pool die al gestopt is: `run` pakt dan de nieuwe pool."""


class _Pool:
    """`WORKERS` daemonthreads die opdrachten uit een wachtrij halen. Een opdracht die nog niet begonnen is, valt te annuleren."""

    def __init__(self) -> None:
        self._jobs: queue.SimpleQueue[_Job | None] = queue.SimpleQueue()
        self._lock: threading.Lock = threading.Lock()
        self._stopped: bool = False
        self._threads: list[threading.Thread] = [
            threading.Thread(target=self._work, name=f"compute-{number}", daemon=True) for number in range(WORKERS)
        ]
        for thread in self._threads:
            thread.start()

    def submit(self, call: Callable[[], T]) -> Future[T]:
        """Zet de opdracht in de wachtrij. Na `stop` faalt dit met `PoolStopped`: niemand zou hem nog uitvoeren."""
        future: Future[T] = Future()
        with self._lock:
            if self._stopped:
                raise PoolStopped
            self._jobs.put((future, call))  # type: ignore[arg-type]
        return future

    def _work(self) -> None:
        while True:
            job: _Job | None = self._jobs.get()
            if job is None:
                return
            future, call = job
            del job
            if future.set_running_or_notify_cancel():  # anders is de aanroeper al weg
                try:
                    future.set_result(call())
                except BaseException as exc:  # noqa: BLE001 - de aanroeper krijgt de fout
                    future.set_exception(exc)
            # Zonder dit houdt de wachtende thread de opdracht en zijn argumenten vast tot de volgende opdracht: bij een bouw van
            # de catalogus van AH de rijen, 22.000 objecten die de GC bij elke volle ronde langsloopt (gc_watch.py).
            del future, call

    def stop(self, seconds: float) -> None:
        """Annuleert wat nog wacht en wacht hooguit `seconds` op wat loopt. Een functie die dan nog loopt, kan niet gestopt worden."""
        with self._lock:
            self._stopped = True  # onder hetzelfde slot als `submit`: wat hierna komt, wordt geweigerd
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


def _submit(call: Callable[[], T]) -> Future[T]:
    """Geeft de opdracht aan de pool. Staat die intussen op stoppen (een `shutdown` in een andere thread), dan volgt een nieuwe."""
    while True:
        try:
            return _get_pool().submit(call)
        except PoolStopped:
            continue  # `shutdown` laat een pool los voor hij hem stopt, dus `_get_pool` maakt nu een nieuwe


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
    future: Future[T] = _submit(functools.partial(function, *args, **kwargs))
    with telemetry.span(PHASE):
        return await asyncio.wrap_future(future)


def shutdown() -> None:
    """Stopt de pool en wacht hooguit `SHUTDOWN_SECONDS` op lopend werk. De volgende `run` maakt een nieuwe pool.

    Wat nog in de wachtrij staat, wordt geannuleerd. Een functie die na de wachttijd nog loopt, kan Python niet doden: die
    thread loopt af op de achtergrond (hij is een daemonthread, dus hij houdt het proces niet vast) en levert zijn uitkomst
    aan wie er nog op wacht.
    """
    pool: _Pool | None = _detach()
    if pool is not None:
        pool.stop(SHUTDOWN_SECONDS)


def _detach() -> _Pool | None:
    global _pool
    with _pool_lock:
        pool: _Pool | None = _pool
        _pool = None
    return pool


async def stop() -> None:
    """Hetzelfde als `shutdown`, voor een `async def`: het wachten op de threads gebeurt in een eigen thread en niet op de loop.

    Een functie die nog loopt houdt de event loop dus niet vast, ook niet tot `SHUTDOWN_SECONDS`. Een fout bij het stoppen gaat
    naar `threading.excepthook`, en het wachten houdt dan ook op. Is het wachten al geannuleerd, dan zet de thread niets meer.
    """
    pool: _Pool | None = _detach()
    if pool is None:
        return
    loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
    done: asyncio.Future[None] = loop.create_future()

    def settle() -> None:
        if not done.done():  # een geannuleerd wachten
            done.set_result(None)

    def finish() -> None:
        try:
            pool.stop(SHUTDOWN_SECONDS)
        finally:
            try:
                loop.call_soon_threadsafe(settle)
            except RuntimeError:  # de loop is intussen gesloten
                pass

    threading.Thread(target=finish, name="compute-stop", daemon=True).start()
    await done


class SingleFlight(Generic[K, V]):
    """Een bouw per sleutel tegelijk: wie dezelfde sleutel vraagt terwijl er een bouw loopt, wacht op diezelfde uitkomst.

    Er blijft niets over na de bouw: de aanroeper schrijft de uitkomst zelf in zijn cache. De bouw is een eigen taak waar elke
    aanroeper met `tasks.share` op wacht, dus een aanroeper die geannuleerd wordt haalt de bouw voor de anderen niet neer. Een
    bouw die mislukt nadat de laatste aanroeper weg is, geeft geen melding over een fout die nooit is opgehaald.
    """

    def __init__(self) -> None:
        self._flights: dict[K, asyncio.Future[V]] = {}

    async def get(self, key: K, build: Callable[[], Awaitable[V]]) -> V:
        flight: asyncio.Future[V] | None = self._flights.get(key)
        if flight is None or flight.done() or flight.get_loop() is not asyncio.get_running_loop():
            # Een bouw die klaar is maar waarvan `_finished` nog niet liep, of van een andere (gesloten) loop, telt niet mee.
            flight = asyncio.ensure_future(build())
            tasks.wait_at_shutdown(flight)  # een bouw zonder aanroeper loopt door: het afsluiten wacht erop
            self._flights[key] = flight
            flight.add_done_callback(functools.partial(self._finished, key))
        return await tasks.share(flight)

    def _finished(self, key: K, flight: asyncio.Future[V]) -> None:
        """Haalt de bouw uit de lijst, nog voor de wachters verder gaan, en leest de fout zodat die nooit "niet opgehaald" is."""
        if self._flights.get(key) is flight:
            del self._flights[key]
        if not flight.cancelled():
            flight.exception()
