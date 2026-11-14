"""Tests voor de leesverbindingen die openblijven (mealieshopper/readers.py): annuleren, sluiten na een tijd vrij, en het
afsluiten terwijl er een uitgeleend is. De threads van aiosqlite zijn geen daemon-threads: een verbinding die niemand sluit,
houdt het proces bij het afsluiten tegen, dus elke test kijkt of de threads echt stoppen."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest import mock

import aiosqlite
import pytest

from mealieshopper import readers


async def opener(path: str) -> aiosqlite.Connection:
    return await aiosqlite.connect(path)


@asynccontextmanager
async def reader_threads() -> AsyncIterator[list[threading.Thread]]:
    """De threads van aiosqlite die binnen het blok starten (een per `aiosqlite.connect`)."""
    started: list[threading.Thread] = []
    original: Callable[[threading.Thread], None] = threading.Thread.start

    def counted(thread: threading.Thread) -> None:
        if "_connection_worker_thread" in thread.name:
            started.append(thread)
        original(thread)

    with mock.patch.object(threading.Thread, "start", counted):
        yield started


def stopped(threads: list[threading.Thread]) -> bool:
    for thread in threads:
        thread.join(5)
    return not [thread for thread in threads if thread.is_alive()]


async def borrow_together(path: str, count: int) -> None:
    """`count` leningen tegelijk, zodat de pool `count` verbindingen opent."""
    together: asyncio.Barrier = asyncio.Barrier(count)

    async def one() -> None:
        async with readers.borrow(path, opener):
            await together.wait()

    await asyncio.gather(*(one() for _ in range(count)))


def pool_of(path: str) -> Any:
    return readers._POOLS[(asyncio.get_running_loop(), path)]


async def test_a_cancel_during_the_idle_sweep_closes_every_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review van taak 10b: de task haalde de verlopen verbindingen in een keer uit `_idle` en sloot ze een voor een. Werd hij
    tijdens een van die sluitingen geannuleerd (`asyncio.Runner` bij het sluiten van de loop, en tot de review ook `retire`), dan
    stond de rest nergens meer: nooit gesloten, en het proces bleef bij het afsluiten hangen op hun threads."""
    monkeypatch.setattr(readers, "READER_IDLE_SECONDS", 0.05)
    path: str = str(tmp_path / "lees.sqlite3")
    gate: asyncio.Event = asyncio.Event()
    entered: asyncio.Event = asyncio.Event()
    original: Callable[[aiosqlite.Connection], Any] = aiosqlite.Connection.close
    closes: list[aiosqlite.Connection] = []

    async def slow_close(conn: aiosqlite.Connection) -> None:
        closes.append(conn)
        if len(closes) > 1:
            await original(conn)
            return
        entered.set()
        try:
            await (
                gate.wait()
            )  # de eerste sluiting van de ronde hangt: daar komt de annulering
        finally:
            await original(
                conn
            )  # zoals aiosqlite: ook na een annulering stopt de thread

    async with reader_threads() as started:
        await borrow_together(path, 3)
    keeper: asyncio.Task[None] = pool_of(path)._keeper
    pool: Any = pool_of(path)
    monkeypatch.setattr(aiosqlite.Connection, "close", slow_close)
    await asyncio.wait_for(entered.wait(), 5)
    keeper.cancel()
    await asyncio.wait({keeper}, timeout=5)
    assert len(started) == 3
    assert stopped(started)
    assert pool._open == 0


async def test_a_borrower_cancelled_while_waiting_leaves_the_pool_as_it_was(
    tmp_path: Path,
) -> None:
    """Een lener die wacht omdat alle verbindingen uitgeleend zijn, en dan geannuleerd wordt: geen verbinding erbij of eraf, en
    de volgende lener krijgt er gewoon een."""
    path: str = str(tmp_path / "lees.sqlite3")
    release: asyncio.Event = asyncio.Event()
    holding: asyncio.Barrier = asyncio.Barrier(readers.READERS_KEPT + 1)

    async def hold() -> None:
        async with readers.borrow(path, opener):
            await holding.wait()
            await release.wait()

    async def wait_in_line() -> None:
        async with readers.borrow(path, opener):
            raise AssertionError("deze lener hoort te wachten")

    async with reader_threads() as started:
        holders: list[asyncio.Task[None]] = [
            asyncio.create_task(hold()) for _ in range(readers.READERS_KEPT)
        ]
        await holding.wait()
        waiter: asyncio.Task[None] = asyncio.create_task(wait_in_line())
        await asyncio.sleep(0.05)
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)
        assert pool_of(path)._open == readers.READERS_KEPT
        release.set()
        await asyncio.gather(*holders)
        async with readers.borrow(path, opener) as reader:
            assert (await reader.fetchone("SELECT 1"))[0] == 1
    assert len(started) == readers.READERS_KEPT
    pool: Any = pool_of(path)
    readers.retire_all()
    await asyncio.wait_for(pool._keeper, 5)
    assert stopped(started)


async def test_a_borrower_cancelled_while_holding_closes_its_connection(
    tmp_path: Path,
) -> None:
    """Een lener die midden in zijn lening geannuleerd wordt, geeft zijn verbinding niet terug maar sluit hem: hij kan midden in
    een opdracht zitten."""
    path: str = str(tmp_path / "lees.sqlite3")
    inside: asyncio.Event = asyncio.Event()

    async def hold() -> None:
        async with readers.borrow(path, opener):
            inside.set()
            await asyncio.Event().wait()

    async with reader_threads() as started:
        holder: asyncio.Task[None] = asyncio.create_task(hold())
        await inside.wait()
        holder.cancel()
        await asyncio.gather(holder, return_exceptions=True)
    assert stopped(started)
    assert pool_of(path)._open == 0 and pool_of(path)._idle == []


async def test_an_idle_connection_closes_and_the_pool_goes_away(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Na `READER_IDLE_SECONDS` vrij gaat een verbinding dicht, en de task stopt en haalt de pool weg: de loop blijft niet aan
    het register hangen."""
    monkeypatch.setattr(readers, "READER_IDLE_SECONDS", 0.05)
    path: str = str(tmp_path / "lees.sqlite3")
    async with reader_threads() as started, readers.borrow(path, opener):
        pass
    keeper: asyncio.Task[None] = pool_of(path)._keeper
    await asyncio.wait_for(keeper, 5)
    assert stopped(started)
    assert (asyncio.get_running_loop(), path) not in readers._POOLS


async def test_retire_all_while_a_connection_is_borrowed_closes_it_when_it_comes_back(
    tmp_path: Path,
) -> None:
    """`retire_all` (na het wissen of terugzetten van de database) terwijl een lener bezig is: die leest gewoon verder, en zijn
    verbinding gaat dicht als hij terugkomt. De volgende lener krijgt een nieuwe pool."""
    path: str = str(tmp_path / "lees.sqlite3")
    async with reader_threads() as started:
        async with readers.borrow(path, opener) as reader:
            old: Any = pool_of(path)
            readers.retire_all()
            assert (await reader.fetchone("SELECT 2"))[0] == 2
        await asyncio.wait_for(old._keeper, 5)
    assert stopped(started)
    assert old._open == 0
    async with readers.borrow(path, opener):
        assert pool_of(path) is not old
    readers.retire_all()


async def test_close_running_loop_waits_for_a_borrowed_connection(
    tmp_path: Path,
) -> None:
    """`close_running_loop` (voor de fabrieksreset het bestand weghaalt) wacht ook op een uitgeleende verbinding, hooguit
    `RETURN_SECONDS`: daarna staat er niets meer open."""
    path: str = str(tmp_path / "lees.sqlite3")
    inside: asyncio.Event = asyncio.Event()

    async def hold() -> None:
        async with readers.borrow(path, opener):
            inside.set()
            await asyncio.sleep(0.1)

    async with reader_threads() as started:
        holder: asyncio.Task[None] = asyncio.create_task(hold())
        await inside.wait()
        pool: Any = pool_of(path)
        await readers.close_running_loop()
        assert pool._open == 0  # de uitgeleende verbinding is al dicht als `close_running_loop` terugkomt
        await holder
    assert stopped(started)


async def test_a_borrow_inside_a_borrow_fails_instead_of_waiting_forever(
    tmp_path: Path,
) -> None:
    """Met alle verbindingen uitgeleend wacht een lening binnen een lening op zichzelf. Daarom geeft hij meteen een fout, ook als
    er nog plek is."""
    path: str = str(tmp_path / "lees.sqlite3")
    async with readers.borrow(path, opener):
        with pytest.raises(RuntimeError, match="binnen een lening"):
            async with readers.borrow(path, opener):
                pass
    async with readers.borrow(path, opener) as reader:  # daarna gewoon weer een lening
        assert (await reader.fetchone("SELECT 3"))[0] == 3
    readers.retire_all()


async def test_a_reader_offers_only_queries_that_close_their_cursor(
    tmp_path: Path,
) -> None:
    """Een open cursor houdt een oude stand van de WAL vast zonder dat `in_transaction` het ziet. Een lener krijgt daarom geen
    verbinding maar een `Reader` met `fetchone` en `fetchall`, die hun cursor sluiten."""
    path: str = str(tmp_path / "lees.sqlite3")
    async with readers.borrow(path, opener) as reader:
        assert [name for name in dir(reader) if not name.startswith("_")] == [
            "fetchall",
            "fetchone",
        ]
        assert [
            tuple(row) for row in await reader.fetchall("SELECT 1 UNION ALL SELECT 2")
        ] == [(1,), (2,)]
    readers.retire_all()
