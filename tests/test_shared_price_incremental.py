"""The incrementally maintained read model equals a build from scratch (`shared_price_dirty`).

A seeded random series of writes (prices, offers, offer history with folder rows, receipts, imports, orders, own links,
rejected links, deletes and new days) runs against one database. After every write the drain of a request brings the read
model up to date, and a copy of the database rebuilds every derived table from nothing. The derived tables, the price
history page and the body of /api/offers must be the same on both.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from mealieshopper import ah_orders, auth, name_keys, offers, price_import, prices, product_links, shared_links, shared_price_overlay

TITLES: dict[str, tuple[str, str, str]] = {
    "1": ("AH Halfvolle melk", "AH", "1 l"),
    "2": ("AH Halfvolle melk", "AH", "1,5 l"),
    "3": ("AH Bloemkool", "AH", "1 stuk"),
    "4": ("Unox Rookworst", "Unox", "275 g"),
    "5": ("AH Basmati rijst", "AH", "1 kg"),
    "6": ("AH Kaas jong belegen 48+", "AH", "500 g"),
}
OTHER_TITLES: tuple[str, ...] = ("AH Halfvolle melk voordeel", "AH Rijst basmati", "Unox Rookworst mager")
FOLDERS: dict[str, str] = {"folder:1": "Alle AH Halfvolle melk", "folder:2": "AH Kaas en Unox Rookworst", "folder:3": "Alle AH rijst"}
RECEIPT_NAMES: tuple[str, ...] = ("AH HALFVOLLE MELK", "BLOEMKOOL", "ROOKWORST", "AH BASMATI RIJST", "KAAS 48+", "ONBEKEND PRODUCT")
DERIVED: tuple[tuple[str, str], ...] = (
    ("price_history_products", "store,product_id"),
    ("shared_price_keys", "store,product_id"),
    ("shared_price_families", "store,family_key,product_id"),
    ("shared_price_lines", "store,product_id,first_word,second_word,kind"),
    ("shared_price_receipts", "store,product_id"),
    ("shared_price_receipt_meta", "store"),
    ("shared_price_receipt_keys", "store,kind,value"),
    ("shared_price_receipt_names", "store,product_id,title,source"),
    ("shared_price_receipt_prices", "store,product_id"),
    ("shared_price_dirty", "store,kind,product_id,title"),
)


# More seeds for a longer run on your own machine: MEALIESHOPPER_INCREMENTAL_SEEDS=200.
SEEDS: list[int] = list(range(1, 1 + int(os.environ.get("MEALIESHOPPER_INCREMENTAL_SEEDS", "6"))))
STEPS: int = 18


class Clock:
    def __init__(self, day: str) -> None:
        self.day: str = day

    def now(self) -> str:
        return f"{self.day}T12:00:00+00:00"

    def ago(self, days: int) -> str:
        return (date.fromisoformat(self.day) - timedelta(days=days)).isoformat()


async def _tables() -> None:
    """The schemas of every source table, as the app creates them."""
    async with prices._connect():
        pass
    async with ah_orders._connect():
        pass
    async with price_import._connect():
        pass
    async with auth.connect() as conn:
        await product_links.ensure(conn)
        await shared_links.ensure(conn)


async def _write(sql: str, args: tuple[Any, ...] = ()) -> None:
    async with prices._connect() as conn:
        await conn.execute(sql, args)
        await conn.commit()


async def _rows(sql: str, args: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    async with prices._connect() as conn:
        return [tuple(row) for row in await conn.execute_fetchall(sql, args)]


async def _observe(rng: random.Random, clock: Clock) -> None:
    pid: str = rng.choice(list(TITLES))
    title, brand, unit = TITLES[pid]
    if rng.random() < 0.25:
        title = rng.choice(OTHER_TITLES)
    await _write(
        "INSERT INTO price_observations(store,product_id,day,source,title,brand,unit_size,image,url,price,seen_at) VALUES('ah',?,?,?,?,?,?,NULL,NULL,?,?) "
        "ON CONFLICT(store,product_id,day) DO UPDATE SET title=excluded.title,price=excluded.price,source=excluded.source,seen_at=excluded.seen_at",
        (
            pid,
            clock.ago(rng.randrange(0, 40)),
            rng.choice(("search", "ahprijsindex")),
            title,
            brand,
            unit,
            round(rng.uniform(0.8, 6.0), 2),
            clock.now(),
        ),
    )


async def _forget_observation(rng: random.Random, clock: Clock) -> None:
    found: list[tuple[Any, ...]] = await _rows("SELECT product_id,day FROM price_observations ORDER BY product_id,day")
    if found:
        pid, day = rng.choice(found)
        await _write("DELETE FROM price_observations WHERE store='ah' AND product_id=? AND day=?", (pid, day))


async def _offer(rng: random.Random, clock: Clock) -> None:
    pid: str = rng.choice(list(TITLES))
    title, brand, unit = TITLES[pid]
    starts: str | None = rng.choice((None, clock.ago(2), clock.ago(-1), clock.ago(-3)))
    ends: str | None = rng.choice((None, clock.ago(-1), clock.ago(-6), clock.ago(1)))
    await _write("DELETE FROM offers WHERE store='ah' AND product_id=?", (pid,))
    await _write(
        "INSERT INTO offers(store,product_id,position,title,brand,unit_size,price,price_before,valid_from,valid_until) VALUES('ah',?,?,?,?,?,?,?,?,?)",
        (pid, rng.randrange(0, 20), title, brand, unit, round(rng.uniform(0.5, 4.0), 2), round(rng.uniform(4.0, 6.0), 2), starts, ends),
    )


async def _forget_offer(rng: random.Random, clock: Clock) -> None:
    await _write("DELETE FROM offers WHERE store='ah' AND product_id=?", (rng.choice(list(TITLES)),))


async def _history(rng: random.Random, clock: Clock) -> None:
    if rng.random() < 0.4:
        pid: str = rng.choice(list(FOLDERS))
        title, brand, unit = FOLDERS[pid], None, None
    else:
        pid = rng.choice(list(TITLES))
        title, brand, unit = TITLES[pid]
    first: str = clock.ago(rng.randrange(3, 60))
    ended: str | None = rng.choice((None, clock.ago(1) + "T08:00:00+00:00"))
    await _write(
        "INSERT INTO offer_history(store,product_id,title,brand,unit_size,category,price,price_before,promo_label,valid_from,valid_until,first_seen,last_seen,ended_at) "
        "VALUES('ah',?,?,?,?,?,?,?,'2e halve prijs',?,?,?,?,?)",
        (
            pid,
            title,
            brand,
            unit,
            rng.choice((None, "Zuivel")),
            1.5,
            round(rng.uniform(2.0, 5.0), 2),
            first,
            None,
            first + "T08:00:00+00:00",
            clock.now(),
            ended,
        ),
    )


async def _end_history(rng: random.Random, clock: Clock) -> None:
    found: list[tuple[Any, ...]] = await _rows("SELECT id FROM offer_history ORDER BY id")
    if found:
        await _write(
            "UPDATE offer_history SET ended_at=?,title=title || ? WHERE id=?", (clock.now(), rng.choice(("", " extra")), rng.choice(found)[0])
        )


async def _receipt(rng: random.Random, clock: Clock) -> None:
    receipt: str = f"r{rng.randrange(0, 6)}"
    async with ah_orders._connect() as conn:
        await conn.execute(
            "INSERT OR IGNORE INTO ah_receipts(receipt_id,date_time,first_seen,details_synced) VALUES(?,?,?,1)",
            (receipt, clock.ago(rng.randrange(-1, 30)) + "T10:00:00", clock.now()),
        )
        position: int = rng.randrange(0, 4)
        await conn.execute(
            "INSERT OR REPLACE INTO ah_receipt_items(receipt_id,position,product_id,name,quantity,price,amount) VALUES(?,?,?,?,?,?,?)",
            (
                receipt,
                position,
                rng.choice((None, 0, 20960, 627748)),
                rng.choice(RECEIPT_NAMES),
                rng.choice((1, 2)),
                round(rng.uniform(0.8, 5.0), 2),
                None,
            ),
        )
        await conn.commit()


async def _forget_receipt_line(rng: random.Random, clock: Clock) -> None:
    found: list[tuple[Any, ...]] = await _rows("SELECT receipt_id,position FROM ah_receipt_items ORDER BY receipt_id,position")
    if found:
        receipt, position = rng.choice(found)
        await _write("DELETE FROM ah_receipt_items WHERE receipt_id=? AND position=?", (receipt, position))


async def _receipt_product(rng: random.Random, clock: Clock) -> None:
    await _write(
        "INSERT OR REPLACE INTO ah_receipt_products(receipt_product_id,product_id,title,checked_at) VALUES(?,?,?,?)",
        (rng.choice((20960, 627748)), int(rng.choice(list(TITLES))), rng.choice([title for title, _, _ in TITLES.values()]), clock.now()),
    )


async def _imported(rng: random.Random, clock: Clock) -> None:
    await _write(
        "INSERT OR REPLACE INTO imported_purchases(store,order_ref,position,day,title,quantity,paid,regular,imported_at) VALUES('ah',?,?,?,?,1,?,?,?)",
        (
            rng.choice(("mail-1", "mail-2", "1001")),
            rng.randrange(0, 3),
            clock.ago(rng.randrange(-1, 20)),
            rng.choice([title for title, _, _ in TITLES.values()] + list(RECEIPT_NAMES)),
            round(rng.uniform(0.8, 5.0), 2),
            round(rng.uniform(1.0, 6.0), 2),
            clock.now(),
        ),
    )


async def _order(rng: random.Random, clock: Clock) -> None:
    order: int = rng.choice((1001, 1002))
    pid: str = rng.choice(list(TITLES))
    title, brand, unit = TITLES[pid]
    async with ah_orders._connect() as conn:
        await conn.execute(
            "INSERT OR IGNORE INTO ah_orders(order_id,kind,delivery_date,first_seen,synced_at,items_synced) VALUES(?,?,?,?,?,1)",
            (order, rng.choice(("delivered", "open")), clock.ago(rng.randrange(-1, 10)), clock.now(), clock.now()),
        )
        await conn.execute(
            "INSERT OR REPLACE INTO ah_order_items(order_id,position,product_id,title,brand,unit_size,quantity,price,price_before_bonus,bonus) VALUES(?,?,?,?,?,?,1,?,?,0)",
            (order, rng.randrange(0, 3), int(pid), title, brand, unit, round(rng.uniform(1.0, 5.0), 2), None),
        )
        await conn.commit()


async def _flip_order(rng: random.Random, clock: Clock) -> None:
    await _write(
        "UPDATE ah_orders SET kind=CASE kind WHEN 'delivered' THEN 'open' ELSE 'delivered' END WHERE order_id=?", (rng.choice((1001, 1002)),)
    )


async def _link(rng: random.Random, clock: Clock) -> None:
    pid: str = rng.choice(list(TITLES))
    kind, key = rng.choice((("name", product_links.fold(rng.choice(RECEIPT_NAMES))), ("article", "20960"), ("name", "zz probe")))
    await _write(
        "INSERT OR REPLACE INTO product_links(store,kind,key,product_id,title,brand,unit_size,image,source_name,created_at,origin) VALUES('ah',?,?,?,?,?,?,'',?,?,'manual')",
        (kind, key, pid, *TITLES[pid], key, clock.now()),
    )


async def _forget_link(rng: random.Random, clock: Clock) -> None:
    found: list[tuple[Any, ...]] = await _rows("SELECT kind,key FROM product_links ORDER BY kind,key")
    if found:
        kind, key = rng.choice(found)
        await _write("DELETE FROM product_links WHERE store='ah' AND kind=? AND key=?", (kind, key))


async def _rejection(rng: random.Random, clock: Clock) -> None:
    key: str = name_keys.key(rng.choice(RECEIPT_NAMES))
    if rng.random() < 0.5:
        await _write("DELETE FROM shared_link_rejections WHERE store='ah' AND key=?", (key,))
    else:
        await _write(
            "INSERT OR REPLACE INTO shared_link_rejections(store,key,product_id,created_at) VALUES('ah',?,?,?)",
            (key, rng.choice(("4", "5")), clock.now()),
        )


async def _next_day(rng: random.Random, clock: Clock) -> None:
    clock.day = clock.ago(-rng.choice((1, 1, 2)))


WRITES: tuple[Any, ...] = (
    _observe,
    _observe,
    _forget_observation,
    _offer,
    _forget_offer,
    _history,
    _end_history,
    _receipt,
    _forget_receipt_line,
    _receipt_product,
    _imported,
    _order,
    _flip_order,
    _link,
    _forget_link,
    _rejection,
    _next_day,
)


async def _seed(rng: random.Random, clock: Clock) -> None:
    for write in (_observe, _observe, _observe, _offer, _offer, _history, _history, _receipt, _receipt, _imported, _order, _link):
        await write(rng, clock)


async def _state(clock: Clock) -> dict[str, Any]:
    """What the read model holds and what the pages show, after the drain of the requests themselves."""
    data: dict[str, Any] = await offers.load("ah", clock.day)
    await prices.annotate("ah", data["offers"], clock.day)
    history: dict[str, Any] = await prices.history("ah", today=clock.day, limit=50)
    bought: dict[str, Any] = await prices.history("ah", today=clock.day, limit=50, bought=True)
    tables: dict[str, Any] = {}
    async with prices._connect() as conn:
        for table, order in DERIVED:
            tables[table] = [tuple(row) for row in await conn.execute_fetchall(f"SELECT * FROM {table} ORDER BY {order}")]
        build: list[Any] = list(await conn.execute_fetchall("SELECT store,generation,ready_revision FROM shared_price_build WHERE store='ah'"))
        tables["shared_price_build"] = [tuple(row) for row in build]
    return {
        "offers": json.dumps(data["offers"], sort_keys=True),
        "history": json.dumps(history, sort_keys=True),
        "bought": json.dumps(bought, sort_keys=True),
        **tables,
    }


def _copy(source: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source) as origin, sqlite3.connect(target / source.name) as copy:
        origin.backup(copy)
        copy.execute("DELETE FROM shared_price_build")
        for table in (*shared_price_overlay.TABLES, "price_history_products"):
            copy.execute(f"DELETE FROM {table}")
        copy.commit()


async def _from_scratch(data_dir: Path, scratch: Path, monkeypatch: pytest.MonkeyPatch, clock: Clock) -> dict[str, Any]:
    await asyncio.to_thread(_copy, data_dir / "auth.sqlite3", scratch)
    with monkeypatch.context() as patch:
        patch.setenv("MEALIESHOPPER_DATA_DIR", str(scratch))
        await shared_price_overlay.prepare("ah")
        return await _state(clock)


def _differences(incremental: dict[str, Any], scratch: dict[str, Any]) -> list[str]:
    return [name for name in incremental if incremental[name] != scratch[name]]


@pytest.mark.asyncio
@pytest.mark.parametrize("seed", SEEDS)
async def test_incremental_read_model_equals_a_build_from_scratch(
    data_dir: Path, tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch, seed: int
) -> None:
    rng: random.Random = random.Random(seed)
    clock: Clock = Clock("2026-10-10")
    monkeypatch.setattr(prices, "_now", clock.now)
    monkeypatch.setattr(offers, "_now", clock.now)  # an open offer runs until now: the same now on both sides
    shared: dict[str, shared_links.SharedLink] = {
        name_keys.key("ROOKWORST"): shared_links.SharedLink("ROOKWORST", "4", "Unox Rookworst", "275 g"),
        name_keys.key("AH BASMATI RIJST"): shared_links.SharedLink("AH BASMATI RIJST", "5", "AH Basmati rijst", "1 kg"),
    }
    monkeypatch.setitem(shared_links._cache, "ah", shared)
    monkeypatch.setenv("MEALIESHOPPER_SHARED_PRICES", "true")
    await _tables()
    await _seed(rng, clock)
    await shared_price_overlay.prepare("ah")
    steps: list[str] = []
    for step in range(STEPS):
        write: Any = rng.choice(WRITES)
        steps.append(write.__name__)
        await write(rng, clock)
        incremental: dict[str, Any] = await _state(clock)
        scratch: dict[str, Any] = await _from_scratch(data_dir, tmp_path_factory.mktemp(f"scratch{seed}-{step}"), monkeypatch, clock)
        different: list[str] = _differences(incremental, scratch)
        assert not different, (steps, different, {name: (incremental[name], scratch[name]) for name in different[:2]})
