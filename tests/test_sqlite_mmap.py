"""Elke verbinding met de database leest via mmap (`PRAGMA mmap_size`, `auth.mmap_bytes`).

Op de server staat de database op ZFS met records van 128 KB, en SQLite haalt zonder mmap elke pagina van 4 KB met een eigen
leesopdracht op. In de container van productie kostte een scan van `offer_history` zo 290 ms, met mmap 80 ms (17-11-2026).
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import aiosqlite
import pytest

from mealieshopper import auth, thread_db


async def _mapped(conn: aiosqlite.Connection) -> int:
    row: aiosqlite.Row | None = await auth.fetchone(conn, "PRAGMA mmap_size")
    assert row is not None
    return int(row[0])


def test_the_default_maps_a_gigabyte(data_dir: Path) -> None:
    assert auth.mmap_bytes() == 1024 * 1024 * 1024


@pytest.mark.parametrize(("value", "expected"), [("0", 0), ("256", 256 * 1024 * 1024), ("-5", 0), ("veel", 1024 * 1024 * 1024)])
def test_the_environment_sets_the_size(data_dir: Path, monkeypatch: pytest.MonkeyPatch, value: str, expected: int) -> None:
    monkeypatch.setenv("MEALIESHOPPER_SQLITE_MMAP_MB", value)
    assert auth.mmap_bytes() == expected


async def test_connect_maps_the_database(data_dir: Path) -> None:
    async with auth.connect() as conn:
        assert await _mapped(conn) == auth.mmap_bytes()


async def test_a_reader_maps_the_database(data_dir: Path) -> None:
    try:
        async with auth.read() as reader:
            row: aiosqlite.Row | None = await reader.fetchone("PRAGMA mmap_size")
        assert row is not None and int(row[0]) == auth.mmap_bytes()
    finally:
        await auth.close_readers()


def test_a_thread_connection_maps_the_database(data_dir: Path) -> None:
    with closing(thread_db.connect()) as conn:
        row: sqlite3.Row = conn.execute("PRAGMA mmap_size").fetchone()
    assert int(row[0]) == auth.mmap_bytes()


async def test_zero_turns_mmap_off(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEALIESHOPPER_SQLITE_MMAP_MB", "0")
    async with auth.connect() as conn:
        assert await _mapped(conn) == 0
