from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import aiosqlite
import pytest

from mealieshopper import shared_price_search


@asynccontextmanager
async def search_connection() -> AsyncIterator[aiosqlite.Connection]:
    async with aiosqlite.connect(":memory:") as conn:
        await conn.executescript("""
        ATTACH DATABASE ':memory:' AS shared_public;
        CREATE TABLE price_history_products(
          store TEXT,product_id TEXT,last TEXT,names TEXT,sizes TEXT,brand TEXT,category TEXT,search TEXT,lead TEXT,twins TEXT,day TEXT,
          PRIMARY KEY(store,product_id));
        CREATE TABLE shared_price_keys(store TEXT,product_id TEXT,same_key TEXT,PRIMARY KEY(store,product_id));
        CREATE INDEX shared_price_keys_same ON shared_price_keys(store,same_key,product_id);
        CREATE TEMP TABLE _private_history_context(store TEXT PRIMARY KEY);
        INSERT INTO _private_history_context VALUES('ah'),('jumbo');
        CREATE TABLE shared_public.products(store TEXT,product_id TEXT,last_day TEXT,brand TEXT,title TEXT,twin_key TEXT,
          PRIMARY KEY(store,product_id));
        CREATE TABLE shared_public.search_names(store TEXT,product_id TEXT,search TEXT,amount REAL,unit TEXT);
        CREATE INDEX shared_public.search_product ON search_names(store,product_id);
        """)

        def category(store: str, title: str) -> str:
            return "public-category"

        await conn.create_function("ms_history_category", 2, category, deterministic=True)
        yield conn


async def install_work(grouped: bool) -> tuple[int, list[Any]]:
    async with search_connection() as conn:
        products: list[tuple[str, str, str, str, str, str]] = [
            ("ah", f"single-{number:04d}", "2026-10-01", "AH", "Singleton", f"single-{number}|[]") for number in range(800)
        ]
        products.extend(
            ("ah", f"twin-{group:02d}-{member}", "2026-10-02", "AH", "Twin", f"twin-{group if grouped else group * 3 + member}|[]")
            for group in range(32)
            for member in range(3)
        )
        await conn.executemany("INSERT INTO shared_public.products VALUES(?,?,?,?,?,?)", products)
        work: int = 0

        def progress() -> int:
            nonlocal work
            work += 1000
            return 0

        await conn.set_progress_handler(progress, 1000)
        try:
            await shared_price_search.install(conn, object())
        finally:
            await conn.set_progress_handler(None, 0)
        rows: list[Any] = await conn.execute_fetchall("SELECT product_id,lead,twins,day FROM price_history_products ORDER BY product_id")
        return work, rows


@pytest.mark.asyncio
async def test_warm_twins_work_is_bounded_with_many_unrelated_groups() -> None:
    singleton_work: int
    grouped_work: int
    singleton_rows: list[Any]
    grouped_rows: list[Any]
    singleton_work, singleton_rows = await install_work(False)
    grouped_work, grouped_rows = await install_work(True)
    assert len(singleton_rows) == len(grouped_rows) == 896
    assert all(row[2] is None for row in singleton_rows)
    twins: list[Any] = [row for row in grouped_rows if row[2] is not None]
    assert len(twins) == 96
    assert twins[:3] == [
        ("twin-00-0", "twin-00-2", '["twin-00-0","twin-00-1","twin-00-2"]', "2026-10-02"),
        ("twin-00-1", "twin-00-2", '["twin-00-0","twin-00-1","twin-00-2"]', "2026-10-02"),
        ("twin-00-2", "twin-00-2", '["twin-00-0","twin-00-1","twin-00-2"]', "2026-10-02"),
    ]
    # Joining twins may visit their members, but must not rescan all unrelated store rows for each member.
    assert grouped_work < singleton_work * 2, (singleton_work, grouped_work)


@pytest.mark.asyncio
async def test_warm_groups_preserve_complete_merged_rows_and_private_shadowing() -> None:
    async with search_connection() as conn:
        await conn.executemany(
            "INSERT INTO shared_public.products VALUES(?,?,?,?,?,?)",
            [
                ("ah", "10", "2026-10-01", "Public", "Milk", "milk|[]"),
                ("ah", "20", "2026-10-03", "Public", "Milk", "milk|[]"),
                ("ah", "30", "2026-10-02", "", "Unknown", ""),
                ("jumbo", "10", "2026-10-05", "Jumbo", "Milk", "milk|[]"),
            ],
        )
        await conn.executemany(
            "INSERT INTO shared_public.search_names VALUES(?,?,?,?,?)",
            [("ah", "10", "milk", 1, "l"), ("ah", "20", "milk alias", 1, "l"), ("jumbo", "10", "milk", 1, "l")],
        )
        private: list[tuple[Any, ...]] = [
            ("ah", "10", "2026-10-04", '["private alias"]', '[[1.0,"l"]]', "Private", "private-category", "private search", "10", None, "2026-10-04"),
            ("ah", "40", "2026-10-04", '["private twin"]', '[[1.0,"l"]]', "Private", "private-category", "twin search", "40", None, "2026-10-04"),
            ("ah", "50", "2026-10-06", '["original twin"]', "[]", "Private", "private-category", "original search", "50", None, "2026-10-06"),
            ("ah", "60", "2026-10-07", '["untouched"]', "[]", "Private", "private-category", "untouched search", "60", None, "2026-10-07"),
        ]
        await conn.executemany("INSERT INTO main.price_history_products VALUES(?,?,?,?,?,?,?,?,?,?,?)", private)
        await conn.executemany(
            "INSERT INTO shared_price_keys VALUES(?,?,?)",
            [("ah", "10", "milk|[]"), ("ah", "40", "milk|[(1.0, 'l')]"), ("ah", "50", "milk|[]"), ("ah", "60", "unaffected|[]")],
        )
        await shared_price_search.install(conn, object())
        rows: list[Any] = await conn.execute_fetchall("SELECT * FROM price_history_products ORDER BY store,product_id")
        assert rows == [
            (
                "ah",
                "10",
                "2026-10-04",
                '["milk","private alias"]',
                '[[1.0,"l"]]',
                "Private",
                "private-category",
                "milk\nmilk\nprivate search",
                "40",
                '["10","20","40"]',
                "2026-10-04",
            ),
            (
                "ah",
                "20",
                "2026-10-03",
                '["milk alias"]',
                '[[1.0,"l"]]',
                "Public",
                "public-category",
                "milk alias\nmilkalias\n",
                "40",
                '["10","20","40"]',
                "2026-10-04",
            ),
            ("ah", "30", "2026-10-02", "[]", "[]", "", "public-category", "\n", "30", None, "2026-10-02"),
            ("ah", "40", *private[1][2:8], "40", '["10","20","40"]', "2026-10-04"),
            private[2],
            private[3],
            ("jumbo", "10", "2026-10-05", '["milk"]', '[[1.0,"l"]]', "Jumbo", "public-category", "milk\nmilk\n", "10", None, "2026-10-05"),
        ]
