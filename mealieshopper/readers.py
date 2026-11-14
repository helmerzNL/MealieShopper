"""Leesverbindingen die openblijven, per event loop en database: voor de korte SELECTs die bij elke aanvraag lopen (de sessie
en de instellingen in auth.py via `auth.read`, de foto's in `image_cache.lookup`).

Elke `aiosqlite.connect` start een eigen thread, en op de event loop wacht `Thread.start` tot die draait. Rekent er een andere
thread, dan kost elke overdracht van de GIL tot het wisselinterval (het konvooi van loop_guard.py). Gemeten in taak 10b met een
drukke rekenthread bij 1 ms: `auth.connect` met een SELECT 32 ms, waarvan de loop 1,5 ms (tot 6 ms) vastzat in `Thread.start`,
en dezelfde SELECT op een open verbinding 13 ms, zonder `Thread.start`. Een aanvraag met inloggen opende er drie tot zeven.

Hooguit `READERS_KEPT` verbindingen per loop en database, elk voor een lener tegelijk: wie er geen krijgt, wacht op de eerste die
vrijkomt. Een lener krijgt geen verbinding maar een `Reader`, met alleen `fetchone` en `fetchall`: die sluiten hun cursor altijd,
want een open cursor houdt een oude stand van de WAL vast zonder dat `in_transaction` het laat zien. De verbinding zelf is
alleen-lezen (`PRAGMA query_only`), gaat zonder open transactie terug (`in_transaction`), en na een fout of een annulering bij de
lener dicht in plaats van terug. Een tweede lening binnen een lening op dezelfde loop geeft een `RuntimeError`: met alle
verbindingen uitgeleend zou die eeuwig wachten.

Een task per pool (`_Pool._keep`, in een lege context zodat het sluiten bij geen aanvraag telt) sluit wat `READER_IDLE_SECONDS`
vrij was, stopt als er niets meer open is, en haalt de pool dan weg. `retire` (bij `tasks.cancel_all`, `retire_all` en
`close_running_loop`) wekt die task met een Event in plaats van hem te annuleren: hij sluit de vrije verbindingen en wacht
hooguit `RETURN_SECONDS` op de uitgeleende, die bij terugkomst dichtgaan. Annuleert `asyncio.Runner` hem bij het sluiten van de
loop, dan sluit hij wat nog in `_idle` staat. Hij haalt een verbinding pas uit `_idle` op het moment dat hij hem sluit, zodat een
annulering halverwege er geen laat liggen: de threads van aiosqlite zijn geen daemon-threads, en een verbinding die niemand
sluit, houdt het proces bij het afsluiten tegen.
"""

from __future__ import annotations

import asyncio
import contextvars
import math
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, TypeAlias

import aiosqlite

READERS_KEPT: int = 4  # per event loop en database: zoveel threads van aiosqlite hooguit voor het lezen
READER_IDLE_SECONDS: float = 30.0  # zo lang blijft een vrije verbinding open
# Zo lang wacht `retire` op uitgeleende verbindingen. Ruim binnen de 3 s die `tasks.cancel_all` bij het afsluiten wacht: een
# lezing duurt milliseconden.
RETURN_SECONDS: float = 2.0

Opener: TypeAlias = Callable[[str], Awaitable[aiosqlite.Connection]]

# De loop en de task van de lening waarin de huidige code staat, of None: zo valt een lening binnen een lening op (`borrow`). Met
# de task erbij: een task die binnen een lening start, krijgt een kopie van de context, en mag zelf lenen.
_Holder: TypeAlias = tuple[asyncio.AbstractEventLoop, "asyncio.Task[Any] | None"]
_borrowing: contextvars.ContextVar[_Holder | None] = contextvars.ContextVar("readers_borrowing", default=None)


class Reader:
    """Wat een lener van `borrow` krijgt: een leesverbinding met alleen `fetchone` en `fetchall`, die hun cursor altijd sluiten."""

    __slots__ = ("_conn",)

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn: aiosqlite.Connection = conn

    async def fetchone(self, sql: str, params: tuple[Any, ...] = ()) -> aiosqlite.Row | None:
        async with self._conn.execute(sql, params) as cursor:
            return await cursor.fetchone()

    async def fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list[aiosqlite.Row]:
        async with self._conn.execute(sql, params) as cursor:
            return list(await cursor.fetchall())


class _Pool:
    """De leesverbindingen van een event loop voor een database."""

    def __init__(self, loop: asyncio.AbstractEventLoop, path: str, opener: Opener) -> None:
        self.loop: asyncio.AbstractEventLoop = loop
        self.path: str = path
        self._opener: Opener = opener
        self._slots: asyncio.Semaphore = asyncio.Semaphore(READERS_KEPT)
        self._idle: list[tuple[aiosqlite.Connection, float]] = []  # vrij, met het moment waarop ze vrijkwam, oudste eerst
        self._open: int = 0  # vrij en uitgeleend samen
        self._busy: int = 0  # leners met een plek, ook als hun verbinding nog opengaat
        self._wake: asyncio.Event = asyncio.Event()  # `retire`: de task stopt meteen in plaats van na zijn wachttijd
        self._drained: asyncio.Event = asyncio.Event()  # geen lener en geen open verbinding meer
        self._keeper: asyncio.Task[None] | None = None
        self.retired: bool = False

    async def enter(self) -> bool:
        """Wacht op een plek. False als de pool intussen dicht ging (`retire`): dan opent hij niets meer, want na
        `close_running_loop` kan het bestand weg zijn, en de lener gaat naar een nieuwe pool (`borrow`)."""
        await self._slots.acquire()
        if self.retired:
            self._slots.release()
            return False
        self._busy += 1
        self._drained.clear()
        return True

    @asynccontextmanager
    async def lend(self) -> AsyncIterator[Reader]:
        """Een verbinding voor wie een plek heeft (`enter`). Geeft de plek bij het verlaten terug."""
        try:
            conn: aiosqlite.Connection = await self._take()
            try:
                yield Reader(conn)
            except BaseException:
                await self._close(conn)  # een fout of annulering halverwege: niet terug in de pool
                raise
            await self._give(conn)
        finally:
            self._busy -= 1
            self._settled()
            self._slots.release()

    def _settled(self) -> None:
        if not self._busy and not self._open:
            self._drained.set()

    async def _take(self) -> aiosqlite.Connection:
        if self._idle:
            return self._idle.pop()[0]
        conn: aiosqlite.Connection = await self._opener(self.path)
        self._open += 1
        self._drained.clear()
        if self._keeper is None:
            # lokaal: auth importeert deze module, en tasks importeert via http_client audit, dat bij het laden auth gebruikt
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
        try:
            await conn.close()  # ook bij een annulering halverwege stopt de thread: `close` zet de stop in zijn `finally`
        finally:
            self._settled()

    async def _close_idle(self, before: float) -> None:
        """Sluit de vrije verbindingen die voor `before` vrijkwamen, een voor een: elke verbinding gaat pas uit `_idle` als hij
        dichtgaat, dus een annulering halverwege laat de rest in `_idle` staan voor de `finally` van `_keep`."""
        while self._idle and self._idle[0][1] <= before:
            conn, _ = self._idle.pop(0)
            await self._close(conn)

    def retire(self) -> None:
        """Laat de task de vrije verbindingen sluiten en hooguit `RETURN_SECONDS` op de uitgeleende wachten, die bij terugkomst
        dichtgaan. Op de loop van de pool."""
        self.retired = True
        _forget(self)
        self._wake.set()

    async def _keep(self) -> None:
        """Sluit wat `READER_IDLE_SECONDS` vrij was, tot er niets meer open is of `retire` hem wekt."""
        try:
            while self._open and not self.retired:
                try:
                    await asyncio.wait_for(self._wake.wait(), READER_IDLE_SECONDS / 2)
                except TimeoutError:
                    pass
                await self._close_idle(time.monotonic() - READER_IDLE_SECONDS)
            self.retired = True
            _forget(self)
            await self._close_idle(math.inf)
            if self._open or self._busy:
                try:
                    await asyncio.wait_for(self._drained.wait(), RETURN_SECONDS)
                except TimeoutError:
                    pass
        finally:
            self.retired = True
            _forget(self)
            try:
                # Na een annulering (`asyncio.Runner` bij het sluiten van de loop): netjes sluiten wat nog vrij staat. Een task die
                # een keer geannuleerd is, kan hier gewoon wachten. Zonder te wachten (`conn.stop`) meldt de thread van aiosqlite
                # een fout als de loop al dicht is voor hij antwoordt.
                await self._close_idle(math.inf)
            finally:
                self._stop_idle()

    def _stop_idle(self) -> None:
        """Na een tweede annulering tijdens het sluiten: wat nog vrij staat, krijgt de stop zonder erop te wachten, zodat zijn thread
        eindigt."""
        for conn, _ in self._idle:
            self._open -= 1
            conn.stop()
        self._idle = []


# De pool per (event loop, database). Een pool haalt zichzelf weg als hij stopt (`_Pool._keep`, `_Pool.retire`).
_POOLS: dict[tuple[asyncio.AbstractEventLoop, str], _Pool] = {}


def _forget(pool: _Pool) -> None:
    if _POOLS.get((pool.loop, pool.path)) is pool:
        del _POOLS[(pool.loop, pool.path)]


@asynccontextmanager
async def borrow(path: str, opener: Opener) -> AsyncIterator[Reader]:
    """Een leesverbinding op `path` voor de lopende event loop, met `opener` als er een bij moet. Een lening binnen een lening op
    dezelfde loop geeft een `RuntimeError` (de module-docstring)."""
    loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
    holder: _Holder = (loop, asyncio.current_task())
    if _borrowing.get() == holder:
        raise RuntimeError("readers.borrow binnen een lening: lees alles in de eerste lening, anders wacht hij onder belasting eeuwig")
    token: contextvars.Token[_Holder | None] = _borrowing.set(holder)
    try:
        pool: _Pool = await _entered(loop, path, opener)
        async with pool.lend() as reader:
            yield reader
    finally:
        _borrowing.reset(token)


async def _entered(loop: asyncio.AbstractEventLoop, path: str, opener: Opener) -> _Pool:
    """De pool van (`loop`, `path`) met een plek voor deze lener. Ging de pool dicht terwijl hij wachtte, dan de volgende."""
    while True:
        pool: _Pool | None = _POOLS.get((loop, path))
        if pool is None or pool.retired:
            pool = _POOLS[(loop, path)] = _Pool(loop, path, opener)
        if await pool.enter():
            return pool


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
    """Sluit de pools van de lopende loop en wacht tot hun verbindingen dicht zijn, de uitgeleende ook: hooguit `RETURN_SECONDS`
    op een lener die zijn verbinding nog niet teruggaf. Voor wie daarna het databasebestand weghaalt (de fabrieksreset). Een pool
    waarvan de eerste verbinding nog opengaat, heeft nog geen task (`_keeper`): op zijn leners wacht dit zelf (`_wait_drained`)."""
    loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
    pools: list[_Pool] = [pool for pool in _POOLS.values() if pool.loop is loop]
    for pool in pools:
        pool.retire()
    opening: list[_Pool] = [pool for pool in pools if pool._keeper is None and pool._busy]
    keepers: list[asyncio.Task[None]] = [pool._keeper for pool in pools if pool._keeper is not None]
    if keepers:
        await asyncio.wait(keepers)
    if opening:
        await _wait_drained(opening)


async def _wait_drained(pools: list[_Pool]) -> None:
    """Wacht tot elke pool geen lener en geen open verbinding meer heeft (`_drained`), samen hooguit `RETURN_SECONDS`, en dan op de
    task die een lener intussen startte (`_take`): die sluit wat er nog vrij staat."""
    waiters: list[asyncio.Task[bool]] = [asyncio.ensure_future(pool._drained.wait()) for pool in pools]
    try:
        await asyncio.wait(waiters, timeout=RETURN_SECONDS)
    finally:
        for waiter in waiters:
            waiter.cancel()
    started: list[asyncio.Task[None]] = [pool._keeper for pool in pools if pool._keeper is not None]
    if started:
        await asyncio.wait(started, timeout=RETURN_SECONDS)
