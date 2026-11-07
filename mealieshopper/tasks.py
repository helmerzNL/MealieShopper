"""Register voor achtergrondtaken van de app (asyncio-tasks) en hun opruiming bij het afsluiten."""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Coroutine, Iterable
from typing import Any, TypeVar

from quart import Quart

from . import http_client

log: logging.Logger = logging.getLogger(__name__)

_tasks: dict[str, asyncio.Task[Any]] = {}
# Afgeschermd werk (`shielded`) dat nog loopt. De set houdt een sterke verwijzing vast: een task waar niemand meer naar
# verwijst, kan anders halverwege worden opgeruimd.
_shielded: set[asyncio.Task[Any]] = set()

T = TypeVar("T")
R = TypeVar("R")


def _unique_name(name: str) -> str:
    """Geeft `name`, of `name-2`, `name-3` enzovoort als die naam al in de register staat."""
    if name not in _tasks:
        return name
    counter: int = 2
    while f"{name}-{counter}" in _tasks:
        counter += 1
    return f"{name}-{counter}"


def spawn(name: str, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
    """Start `coro` als achtergrondtask en registreert hem. Moet binnen een lopende event loop worden aangeroepen.

    Een naam die al in gebruik is krijgt het achtervoegsel `-2`, `-3` enzovoort (de task heet dan ook zo).
    Een crash wordt gelogd met traceback en laat de andere tasks en de app ongemoeid.
    """
    unique: str = _unique_name(name)
    task: asyncio.Task[Any] = asyncio.create_task(coro, name=unique)
    _tasks[unique] = task

    def _done(finished: asyncio.Task[Any]) -> None:
        if _tasks.get(unique) is finished:
            del _tasks[unique]
        if finished.cancelled():
            return
        exc: BaseException | None = finished.exception()
        if exc is not None:
            log.error("Achtergrondtaak %s mislukt", unique, exc_info=exc)

    task.add_done_callback(_done)
    return task


async def map_bounded(limit: int, fn: Callable[[T], Awaitable[R]], items: Iterable[T]) -> list[R]:
    """`await fn(item)` voor elk item, hooguit `limit` tegelijk, met de uitkomsten in de volgorde van `items`.

    De vervanger van `map` op een threadpool met `limit` werkers. Elke aanroep krijgt een kopie van de context
    (verzoek, taal, auditlog). Een aanroep maakt zijn coroutine pas als hij aan de beurt is. Gooit er een, dan annuleren
    we de rest, wachten tot die gestopt is en gooien die eerste fout zelf, met haar eigen `__context__` (geen
    `ExceptionGroup` zoals bij `asyncio.TaskGroup`): een aanroeper die op het type vangt (zoals `JumboAuthError`) blijft
    zo werken. Wordt map_bounded zelf geannuleerd, dan annuleren we alle items en komt de annulering terug, ook als een
    item bij het opruimen een fout gooit.
    """
    slots: asyncio.Semaphore = asyncio.Semaphore(limit)

    async def one(item: T) -> R:
        async with slots:
            return await fn(item)

    started: list[asyncio.Task[R]] = []
    try:
        # In de try: gooit `items` zelf halverwege, dan stoppen we wat al liep in plaats van het los te laten.
        for item in items:
            started.append(asyncio.create_task(one(item)))
        if not started:
            return []
        await asyncio.wait(started, return_when=asyncio.FIRST_EXCEPTION)
    except BaseException:
        await _stop(started)
        raise
    failed: list[asyncio.Task[R]] = [task for task in started if task.done() and not task.cancelled() and task.exception()]
    if failed:
        await _stop(started)
        for other in failed[1:]:
            log.debug("Nog een fout in map_bounded", exc_info=other.exception())
        error: BaseException | None = failed[0].exception()
        assert error is not None
        raise error
    return [task.result() for task in started]


async def _stop(started: list[asyncio.Task[Any]]) -> None:
    """Annuleert de items die nog lopen en wacht tot ze allemaal gestopt zijn (hun fouten zijn dan opgehaald)."""
    for task in started:
        task.cancel()
    await asyncio.gather(*started, return_exceptions=True)


def _shielded_done(task: asyncio.Task[Any]) -> None:
    """Vergeet afgeschermd werk dat klaar is. Een fout is dan opgehaald: wie wachtte, kreeg hem al via `asyncio.shield`."""
    _shielded.discard(task)
    if not task.cancelled() and task.exception() is not None:
        log.debug("Afgeschermd werk mislukt", exc_info=task.exception())


def _orphan_done(task: asyncio.Task[Any]) -> None:
    """Afgeschermd werk waarvan de aanroeper weg is (geannuleerd): niemand anders ziet een fout, dus die komt als
    waarschuwing in het logboek. Een vernieuwd token dat niet bewaard is, moet opvallen."""
    if not task.cancelled() and task.exception() is not None:
        log.warning("Afgeschermd werk mislukt nadat de aanroeper wegging", exc_info=task.exception())


async def shielded(coro: Coroutine[Any, Any, T]) -> T:
    """Draait `coro` als eigen task en wacht erop, zonder dat een annulering van de aanroeper die task afbreekt.

    Voor werk dat niet halverwege mag stoppen, zoals een token vernieuwen en het nieuwe refresh-token bewaren: de winkel
    heeft het oude dan misschien al vervangen. Quart annuleert een verzoek als de browser weggaat terwijl het lichaam nog
    binnenkomt of het antwoord al loopt (daartussen houdt `keep_running` de melding weg), `cancel_all` bij het afsluiten,
    `map_bounded` de andere items bij een fout. De aanroeper krijgt dan zelf een `CancelledError`, de task loopt door.
    `cancel_all` wacht er ook op.
    """
    task: asyncio.Task[T] = asyncio.create_task(coro)
    _shielded.add(task)
    task.add_done_callback(_shielded_done)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        task.add_done_callback(_orphan_done)
        raise


def running() -> dict[str, asyncio.Task[Any]]:
    """Een kopie van de register: naam (zoals `Task.get_name()`) naar de nog lopende task."""
    return dict(_tasks)


async def cancel_all(timeout: float = 10.0) -> None:
    """Annuleert alle geregistreerde tasks en wacht er hoogstens `timeout` seconden op, samen met het afgeschermde werk
    (`shielded`) dat nog loopt. Dat laatste annuleren we niet: het mag niet halverwege stoppen."""
    pending: list[asyncio.Task[Any]] = list(_tasks.values())
    for task in pending:
        task.cancel()
    waiting: list[asyncio.Task[Any]] = pending + list(_shielded)
    if waiting:
        await asyncio.wait(waiting, timeout=timeout)


async def _cancel_all_hook() -> None:
    await cancel_all()


def register(app: Quart) -> None:
    """Hangt het opruimen van tasks en httpx-clients aan het afsluiten van de app."""
    app.after_serving(_cancel_all_hook)
    app.after_serving(http_client.aclose_all)
