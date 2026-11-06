"""Register voor achtergrondtaken van de app (asyncio-tasks) en hun opruiming bij het afsluiten."""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Coroutine, Iterable
from typing import Any, TypeVar

from quart import Quart

from . import http_client

log: logging.Logger = logging.getLogger(__name__)

_tasks: dict[str, asyncio.Task[Any]] = {}

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
    (verzoek, taal, auditlog). Een aanroep maakt zijn coroutine pas als hij aan de beurt is. Gooit er een, dan annuleert de
    `TaskGroup` de rest en komt die eerste fout zelf naar boven, niet als `ExceptionGroup`: een aanroeper die op het type
    vangt (zoals `JumboAuthError`) blijft zo werken.
    """
    slots: asyncio.Semaphore = asyncio.Semaphore(limit)

    async def one(item: T) -> R:
        async with slots:
            return await fn(item)

    try:
        async with asyncio.TaskGroup() as group:
            started: list[asyncio.Task[R]] = [group.create_task(one(item)) for item in items]
    except BaseExceptionGroup as failed:
        first: BaseException = failed.exceptions[0]
        raise first from first.__cause__
    return [task.result() for task in started]


def running() -> dict[str, asyncio.Task[Any]]:
    """Een kopie van de register: naam (zoals `Task.get_name()`) naar de nog lopende task."""
    return dict(_tasks)


async def cancel_all(timeout: float = 10.0) -> None:
    """Annuleert alle geregistreerde tasks en wacht er hoogstens `timeout` seconden op."""
    pending: list[asyncio.Task[Any]] = list(_tasks.values())
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.wait(pending, timeout=timeout)


async def _cancel_all_hook() -> None:
    await cancel_all()


def register(app: Quart) -> None:
    """Hangt het opruimen van tasks en httpx-clients aan het afsluiten van de app."""
    app.after_serving(_cancel_all_hook)
    app.after_serving(http_client.aclose_all)
