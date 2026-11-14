"""Leesverbindingen die openblijven, per event loop en database: voor de korte SELECTs die bij elke aanvraag lopen (de sessie
en de instellingen in auth.py, via `auth.read`).

Elke `aiosqlite.connect` start een eigen thread, en op de event loop wacht `Thread.start` tot die draait. Rekent er een andere
thread, dan kost elke overdracht van de GIL tot het wisselinterval (het konvooi van loop_guard.py). Gemeten in taak 10b met een
drukke rekenthread bij 1 ms: `auth.connect` met een SELECT 32 ms, waarvan de loop 1,5 ms (tot 6 ms) vastzat in `Thread.start`,
en dezelfde SELECT op een open verbinding 13 ms, zonder `Thread.start`. Een aanvraag met inloggen opende er drie tot zeven.

Hooguit `READERS_KEPT` verbindingen per loop en database, elk voor een lener tegelijk: wie er geen krijgt, wacht op de eerste die
vrijkomt. Een verbinding is alleen-lezen (`PRAGMA query_only`): een schrijfopdracht faalt. Ze gaat terug in de pool zonder open
transactie (`in_transaction`), anders zag de volgende lener een oude stand van de WAL. Na een fout of een annulering bij de lener
gaat ze dicht in plaats van terug. Een verbinding die `READER_IDLE_SECONDS` niet gebruikt is, sluit een task per pool (`_Pool._keep`),
in een lege context zodat het sluiten bij geen aanvraag telt. Die task stopt als er niets meer open is, en haalt de pool dan weg.

Dicht gaan ze ook bij `tasks.cancel_all` (het afsluiten, `on_cancel_all`), bij het sluiten van de loop (`asyncio.Runner`
annuleert de task), en met `retire_all` (auth.py: de database is gewist of teruggezet).
"""

from __future__ import annotations

import asyncio
import contextvars
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import aiosqlite

READERS_KEPT: int = 4  # per event loop en database: zoveel threads van aiosqlite hooguit voor het lezen
READER_IDLE_SECONDS: float = 30.0  # zo lang blijft een vrije verbinding open

Opener = Callable[[str], Awaitable[aiosqlite.Connection]]


class _Pool:
    """De leesverbindingen van een event loop voor een database."""

    def __init__(self, loop: asyncio.AbstractEventLoop, path: str, opener: Opener) -> None:
        self.loop: asyncio.AbstractEventLoop = loop
        self.path: str = path
        self._opener: Opener = opener
        self._slots: asyncio.Semaphore = asyncio.Semaphore(READERS_KEPT)
        self._idle: list[tuple[aiosqlite.Connection, float]] = []  # vrij, met het moment waarop ze vrijkwam
        self._open: int = 0  # vrij en uitgeleend samen
        self._keeper: asyncio.Task[None] | None = None
        self.retired: bool = False

    @asynccontextmanager
    async def borrow(self) -> AsyncIterator[aiosqlite.Connection]:
        async with self._slots:
            conn: aiosqlite.Connection = await self._take()
            try:
                yield conn
            except BaseException:
                await self._close(conn)  # een fout of annulering halverwege: niet terug in de pool
                raise
            await self._give(conn)

    async def _take(self) -> aiosqlite.Connection:
        if self._idle:
            return self._idle.pop()[0]
        conn: aiosqlite.Connection = await self._opener(self.path)
        self._open += 1
        if self._keeper is None:
            # lokaal: tasks importeert via http_client en audit auth.py, en auth importeert deze module
            from . import tasks

            self._keeper = self.loop.create_task(self._keep(), name="leesverbindingen", context=contextvars.Context())
            tasks.wait_at_shutdown(self._keeper)
            tasks.on_cancel_all(_retire_running_loop)
        return conn

    async def _give(self, conn: aiosqlite.Connection) -> None:
        if self.retired or conn.in_transaction:
            await self._close(conn)
            return
        self._idle.append((conn, time.monotonic()))

    async def _close(self, conn: aiosqlite.Connection) -> None:
        self._open -= 1
        await conn.close()  # ook bij een annulering halverwege stopt de thread: `close` zet de stop in zijn `finally`

    def retire(self) -> None:
        """Sluit de vrije verbindingen (via de task) en een uitgeleende als hij terugkomt. Op de loop van de pool."""
        self.retired = True
        _forget(self)
        if self._keeper is not None:
            self._keeper.cancel()

    async def _keep(self) -> None:
        """Sluit wat `READER_IDLE_SECONDS` vrij was, tot er niets meer open is. Geannuleerd: alles wat vrij is meteen."""
        try:
            while self._open:
                await asyncio.sleep(READER_IDLE_SECONDS / 2)
                limit: float = time.monotonic() - READER_IDLE_SECONDS
                old: list[aiosqlite.Connection] = [conn for conn, since in self._idle if since <= limit]
                self._idle = [(conn, since) for conn, since in self._idle if since > limit]
                for conn in old:
                    await self._close(conn)
        finally:
            self.retired = True
            _forget(self)
            idle: list[aiosqlite.Connection] = [conn for conn, _ in self._idle]
            self._idle = []
            for conn in idle:
                await self._close(conn)


# De pool per (event loop, database). Een pool haalt zichzelf weg als zijn task stopt (`_Pool._keep`).
_POOLS: dict[tuple[asyncio.AbstractEventLoop, str], _Pool] = {}


def _forget(pool: _Pool) -> None:
    if _POOLS.get((pool.loop, pool.path)) is pool:
        del _POOLS[(pool.loop, pool.path)]


def borrow(path: str, opener: Opener) -> AbstractAsyncContextManager[aiosqlite.Connection]:
    """Een leesverbinding op `path` voor de lopende event loop, met `opener` als er een bij moet. Gebruik: `async with`."""
    loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
    pool: _Pool | None = _POOLS.get((loop, path))
    if pool is None or pool.retired:
        pool = _POOLS[(loop, path)] = _Pool(loop, path, opener)
    return pool.borrow()


def _retire_running_loop() -> None:
    """Voor `tasks.cancel_all`: de pools van de lopende loop gaan dicht, en `cancel_all` wacht op hun task."""
    loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
    for pool in [pool for pool in _POOLS.values() if pool.loop is loop]:
        pool.retire()


def retire_all() -> None:
    """Elke pool gaat dicht, ook die van een andere loop (via `call_soon_threadsafe`). Mag buiten een event loop."""
    try:
        running: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    for pool in list(_POOLS.values()):
        _forget(pool)
        if pool.loop is running:
            pool.retire()
            continue
        try:
            pool.loop.call_soon_threadsafe(pool.retire)
        except RuntimeError:  # de loop is dicht: zijn task is al gestopt
            continue


async def close_running_loop() -> None:
    """Sluit de pools van de lopende loop en wacht tot hun verbindingen dicht zijn: voor wie daarna het bestand weghaalt."""
    loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
    keepers: list[asyncio.Task[None]] = []
    for pool in [pool for pool in _POOLS.values() if pool.loop is loop]:
        pool.retire()
        if pool._keeper is not None:
            keepers.append(pool._keeper)
    if keepers:
        await asyncio.wait(keepers)
