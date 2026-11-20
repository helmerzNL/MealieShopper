"""Change tracking for the prepared private read model (`shared_price_overlay.prepare`).

Every source table of the read model carries a trigger that writes one row into `shared_price_dirty` for each product,
product name, own or rejected link and receipt input a write touches. `refresh` drains those rows: it relinks the receipt
lines only when a change can reach one of them, re-indexes only the touched products and moves the model to a new UTC day
by re-indexing only the products whose rows depend on the day. A write therefore no longer makes the whole model stale.
The triggers write into this table and nothing else, so an older version of the app keeps writing every source table, and
the rows wait until a version with the read model drains them.

The derived tables, their sources and whether a row depends on the day:

- `price_history_products`, `shared_price_keys` and `shared_price_families`: one row per product, from `price_observations`,
  `offer_history`, `offers`, the order tables and the linked receipt lines. The rows of a product in `offers`, and of a
  product with a purchase or receipt line dated after the build day, depend on the day.
- `shared_price_lines`: one row per word pair of a folder product, from the `folder:` rows of `offer_history`. No day.
- `shared_price_receipts`: the linked receipt lines per product, from `ah_receipts`, `ah_receipt_items`, `imported_purchases`,
  the order tables (which imports an account order replaces), every source of product names, `ah_receipt_products`,
  `product_links` and `shared_link_rejections`. Lines dated after the build day are left out, so the day matters only when
  such a line exists. `shared_price_receipt_keys`, `shared_price_receipt_names` and `shared_price_receipt_prices` keep what
  the linking read, so that `refresh` can prove that a change leaves every line where it was.
- `shared_price_equivalence` and `shared_price_origins`: one row per public observation, kept by `shared_price_overlay`
  and its triggers. No day.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import weakref
from collections.abc import Iterable, Sequence
from contextvars import ContextVar
from typing import Any, NamedTuple

import aiosqlite

from . import auth, compute

log: logging.Logger = logging.getLogger(__name__)

# The formats of the derived rows. A write no longer rebuilds the whole model, so a change to how a product row, a folder line
# or a linked receipt line is derived must bump its format: the next start then builds again. A deploy of other code alone does
# not, so the first visitor after a deploy pays nothing.
FORMAT: str = "search-v4"
RECEIPTS_FORMAT: str = "receipts-v3"
BATCH_SIZE: int = 64
MAX_ROUNDS: int = 4096
REQUEST_WAIT_MS: int = 500  # how long a request waits for another writer before it answers from the fallbacks
# How many pending products a request re-indexes itself (about 1 s on the copy of production). A larger backlog (an import of the
# price archive) goes on in the background, and the request answers from the fallbacks meanwhile.
REQUEST_PRODUCTS: int = 2048
RECEIPT_BATCH: int = 2000  # pending names and links read per round

PRODUCT: str = "product"
NAME: str = "name"
LINK: str = "link"
RECEIPTS: str = "receipts"
ACCOUNTS: str = "accounts"
RECEIPT_KINDS: tuple[str, ...] = (NAME, LINK, RECEIPTS, ACCOUNTS)

SCHEMA: str = """
CREATE TABLE IF NOT EXISTS shared_price_dirty (
 store TEXT NOT NULL, kind TEXT NOT NULL, product_id TEXT NOT NULL, title TEXT NOT NULL, n INTEGER NOT NULL DEFAULT 1,
 PRIMARY KEY(store,kind,product_id,title)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS shared_price_receipt_keys (
 store TEXT NOT NULL, kind TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY(store,kind,value)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS shared_price_receipt_names (
 store TEXT NOT NULL, source INTEGER NOT NULL, product_id TEXT NOT NULL, title TEXT NOT NULL,
 PRIMARY KEY(store,product_id,title,source)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS shared_price_receipt_prices (
 store TEXT NOT NULL, product_id TEXT NOT NULL, prices TEXT NOT NULL, PRIMARY KEY(store,product_id)
) WITHOUT ROWID;
"""
TABLES: tuple[str, ...] = ("shared_price_dirty", "shared_price_receipt_keys", "shared_price_receipt_names", "shared_price_receipt_prices")
TRIGGER_PREFIX: str = "shared_price_dirty_"
OPS: tuple[str, ...] = ("INSERT", "UPDATE", "DELETE", "REPLACE", "REPLACE_UPDATE")

# The stores a request found clean after its own `refresh`, so that the readers of that request skip the check.
_CLEAN: ContextVar[tuple[tuple[str, str], ...]] = ContextVar("shared_price_clean", default=())
_LOCKS: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[tuple[str, str], asyncio.Lock]] = weakref.WeakKeyDictionary()
_DRAINS: dict[tuple[str, str], asyncio.Task[Any]] = {}  # the background drain per database and store (`_drain_later`)


class Source(NamedTuple):
    """A source table and what a write to one of its rows marks. `store` is SQL with `{row}` for OLD or NEW."""

    table: str
    store: str
    marks: tuple[str, ...]
    items: str = ""  # an order table: the table with its lines, whose products and names the order marks
    columns: tuple[str, ...] = ()  # only an update of one of these columns marks (all columns when empty)
    added: tuple[str, ...] = ()  # marks only for an insert or a delete


def sources() -> tuple[Source, ...]:
    from . import prices

    found: list[Source] = [
        Source("price_observations", "{row}.store", (PRODUCT, NAME)),
        Source("offer_history", "{row}.store", (PRODUCT, NAME)),
        Source("offers", "{row}.store", (PRODUCT,)),
        # The receipt tables mark only for an update of a column the linking reads: the nightly `prices.fill_regular_prices`
        # writes `regular_price` and `regular_checked_at` of every line it checks.
        Source(
            "imported_purchases",
            "{row}.store",
            (RECEIPTS,),
            columns=("store", "order_ref", "position", "day", "title", "quantity", "paid", "regular"),
        ),
        Source("ah_receipts", "'ah'", (RECEIPTS,), columns=("receipt_id", "date_time")),
        Source("ah_receipt_items", "'ah'", (RECEIPTS,), columns=("receipt_id", "position", "product_id", "name", "quantity", "price", "amount")),
        Source("ah_receipt_products", "'ah'", (RECEIPTS,), columns=("receipt_product_id", "product_id", "title", "brand", "unit_size")),
        Source("product_links", "{row}.store", ("own_link",)),
        Source("shared_link_rejections", "{row}.store", ("rejection",)),
        Source("shared_price_equivalence", "{row}.store", (PRODUCT, "equivalent_name")),
    ]
    for store, purchases in prices._PURCHASES.items():
        for (items, orders), _ in purchases:
            found.append(Source(items, f"'{store}'", (PRODUCT, NAME), columns=ITEM_COLUMNS, added=(ACCOUNTS,)))
            found.append(Source(orders, f"'{store}'", ("order", ACCOUNTS), items=items, columns=("kind", "status", "delivery_date", "first_seen")))
    return tuple(found)


# The columns of a line of an order that the read model reads (`prices._PURCHASES`, the names and the account orders): not
# `regular_price` and `regular_checked_at`, which the nightly `prices.fill_regular_prices` writes.
ITEM_COLUMNS: tuple[str, ...] = (
    "order_id", "position", "product_id", "title", "brand", "unit_size", "image", "quantity", "amount", "price", "price_before_bonus", "bonus", "list_price",
)  # fmt: skip

_UPSERT_SQL: str = " ON CONFLICT(store,kind,product_id,title) DO UPDATE SET n=n+1"
_UPSERT: str = _UPSERT_SQL + ";"
_INSERT: str = "INSERT INTO shared_price_dirty(store,kind,product_id,title) "


def _parts(source: Source, row: str, added: bool) -> list[tuple[str, str, str]]:
    """Per mark of one row (`OLD`, `NEW` or an alias): the selected columns, the tables it joins and its condition. Plain SQL
    only: a function registered per connection, such as `ms_fold`, would make a write from a connection without it fail."""
    store: str = source.store.format(row=row)
    pid: str = f"CAST({row}.product_id AS TEXT)"
    parts: list[tuple[str, str, str]] = []
    for mark in source.marks + (source.added if added else ()):
        if mark == PRODUCT:
            parts.append((f"{store},'{PRODUCT}',{pid},''", "", f"{row}.product_id IS NOT NULL"))
        elif mark == NAME:
            parts.append((f"{store},'{NAME}',{pid},{row}.title", "", f"{row}.product_id IS NOT NULL AND COALESCE({row}.title,'')!=''"))
        elif mark in (RECEIPTS, ACCOUNTS):
            parts.append((f"{store},'{mark}','',''", "", "1"))
        elif mark == "own_link":
            parts.append((f"{store},'{LINK}',{row}.kind,{row}.key", "", f"{row}.kind IS NOT NULL AND {row}.key IS NOT NULL"))
        elif mark == "rejection":
            parts.append((f"{store},'{LINK}','rejection',{row}.key", "", f"{row}.key IS NOT NULL"))
        elif mark == "order":
            joined: str = f"{source.items} i"
            parts.append((f"{store},'{PRODUCT}',CAST(i.product_id AS TEXT),''", joined, f"i.order_id={row}.order_id AND i.product_id IS NOT NULL"))
            parts.append(
                (
                    f"{store},'{NAME}',CAST(i.product_id AS TEXT),i.title",
                    joined,
                    f"i.order_id={row}.order_id AND i.product_id IS NOT NULL AND COALESCE(i.title,'')!=''",
                )
            )
        elif mark == "equivalent_name":
            parts.append(
                (
                    f"{store},'{NAME}',o.product_id,o.title",
                    "price_observations o",
                    f"o.store={row}.store AND o.product_id={row}.product_id AND o.day={row}.day AND COALESCE(o.title,'')!=''",
                )
            )
    return parts


def _statements(parts: list[tuple[str, str, str]], joined: str = "", where: str = "") -> str:
    found: list[str] = []
    for columns, tables, condition in parts:
        sources: str = ",".join(table for table in (joined, tables) if table)
        found.append(f"{_INSERT}SELECT {columns}{' FROM ' + sources if sources else ''} WHERE {where + ' AND ' if where else ''}{condition}{_UPSERT}")
    return " ".join(found)


def trigger_name(table: str, op: str) -> str:
    return f"{TRIGGER_PREFIX}{table}_{op.lower()}"


def trigger_sql(source: Source, op: str, columns: set[str], keys: list[list[str]]) -> str:
    """The trigger of `op` on a source table. `REPLACE` and `REPLACE_UPDATE` run before every insert and update and mark the
    rows with the same key as the new row: `INSERT OR REPLACE` and `UPDATE OR REPLACE` delete those without firing a delete
    trigger (unless `recursive_triggers` is on). For an update that keeps its key that is the row itself, marked anyway."""
    if op in ("REPLACE", "REPLACE_UPDATE"):
        # Only the product and name of the row that goes: every other mark of a table depends on its key, which the new row
        # shares and marks itself. Before an insert only a row that differs from the new one (an upsert updates the row instead,
        # and its update trigger sees the old one), before an update only when a key column changes (else nothing can conflict).
        named: Source = source._replace(marks=tuple(mark for mark in source.marks if mark in (PRODUCT, NAME)), added=())
        differs: str = " OR ".join(f"x.{column} IS NOT NEW.{column}" for column in ("product_id", "title") if column in columns) or "0"
        body: str = " ".join(
            _statements(
                _parts(named, "x", False),
                f"{source.table} x",
                " AND ".join(f"x.{column}=NEW.{column}" for column in key) + (f" AND ({differs})" if op == "REPLACE" else ""),
            )
            for key in keys
        )
        changed: str = " OR ".join(sorted({f"NEW.{column} IS NOT OLD.{column}" for key in keys for column in key})) or "0"
        event: str = "INSERT" if op == "REPLACE" else "UPDATE"
        when: str = "" if op == "REPLACE" else f" WHEN {changed}"
        return f"CREATE TRIGGER IF NOT EXISTS {trigger_name(source.table, op)} BEFORE {event} ON {source.table}{when} BEGIN {body or 'SELECT 1;'} END"
    rows: tuple[str, ...] = {"INSERT": ("NEW",), "DELETE": ("OLD",), "UPDATE": ("OLD", "NEW")}[op]
    event = op
    if op == "UPDATE" and source.columns:
        watched: list[str] = [column for column in source.columns if column in columns]
        event = "UPDATE OF " + ",".join(watched) if watched else op
    body = " ".join(_statements(_parts(source, row, op != "UPDATE")) for row in rows)
    return f"CREATE TRIGGER IF NOT EXISTS {trigger_name(source.table, op)} AFTER {event} ON {source.table} BEGIN {body} END"


async def _unique_keys(conn: aiosqlite.Connection, table: str) -> list[list[str]]:
    """The column lists of the primary key and of every unique index of `table` (an index on an expression is left out)."""
    keys: list[list[str]] = []
    primary: list[str] = [str(row[1]) for row in sorted(await conn.execute_fetchall(f"PRAGMA table_info({table})"), key=lambda row: row[5]) if row[5]]
    if primary:
        keys.append(primary)
    for index in await conn.execute_fetchall(f"PRAGMA index_list({table})"):
        if not index[2]:
            continue
        found: list[Any] = list(await conn.execute_fetchall(f"PRAGMA index_info({index[1]})"))
        names: list[str] = [str(row[2]) for row in found if row[2] is not None]
        if names and len(names) == len(found) and names not in keys:
            keys.append(names)
    return keys


def _stores_of(source: Source) -> list[str] | None:
    """The stores a table feeds, or None for a table with a `store` column (every store)."""
    return None if "{row}" in source.store else [source.store.strip("'")]


async def ensure_triggers(conn: aiosqlite.Connection) -> None:
    """Create the triggers of every source table that exists. A trigger that was missing let writes through unmarked, so the
    build of each store that table feeds starts again (a missing trigger means a new table, a rebuilt one or a first start)."""
    from . import prices

    tables: set[str] = await prices._tables(conn)
    present: set[str] = {
        row[0]
        for row in await conn.execute_fetchall(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND substr(name,1,?)=?", (len(TRIGGER_PREFIX), TRIGGER_PREFIX)
        )
    }
    missing: list[tuple[Source, str]] = [
        (source, op) for source in sources() if source.table in tables for op in OPS if trigger_name(source.table, op) not in present
    ]
    if not missing:
        return
    stale: set[str] | None = set()
    for source, op in missing:
        columns: set[str] = {row[1] for row in await conn.execute_fetchall(f"PRAGMA table_info({source.table})")}
        await conn.execute(trigger_sql(source, op, columns, await _unique_keys(conn, source.table)))
        stores: list[str] | None = _stores_of(source)
        stale = None if stores is None or stale is None else stale | set(stores)
    if stale is None:
        await conn.execute("DELETE FROM shared_price_build")
        await conn.execute("DELETE FROM shared_price_receipt_meta")
    else:
        await conn.executemany("DELETE FROM shared_price_build WHERE store=?", [(store,) for store in sorted(stale)])
        await conn.executemany("DELETE FROM shared_price_receipt_meta WHERE store=?", [(store,) for store in sorted(stale)])
    await conn.commit()


def build_revision(token: str, day: str) -> str:
    return f"{FORMAT}|{token}|{day}"


def parse_build(value: Any) -> tuple[str, str] | None:
    """(token, day) of a build revision of this format, or None."""
    parts: list[str] = str(value or "").split("|")
    return (parts[1], parts[2]) if len(parts) == 3 and parts[0] == FORMAT else None


def receipt_revision(token: str, generation: str, day: str) -> str:
    return f"{RECEIPTS_FORMAT}|{token}|{generation}|{day}"


def parse_receipts(value: Any) -> tuple[str, str, str] | None:
    """(token, generation, day) of a receipt revision of this format, or None."""
    parts: list[str] = str(value or "").split("|")
    return (parts[1], parts[2], parts[3]) if len(parts) == 4 and parts[0] == RECEIPTS_FORMAT else None


async def token(conn: aiosqlite.Connection, store: str) -> str:
    """The random token of the catalogue of `store`: a restored or new database has another one. Read straight from its row,
    without the check of the catalogue triggers (`prices._catalogue_row`, which scans every trigger) unless the row is missing."""
    from . import prices

    row: Any = await auth.fetchone(conn, "SELECT token FROM catalogue_versions WHERE store=?", (store,))
    return str(row[0] if row is not None else (await prices._catalogue_row(conn, store))["token"])


def _clean_here(store: str) -> bool:
    return (str(auth.db_path()), store) in _CLEAN.get()


def _mark_clean(store: str, clean: bool) -> None:
    key: tuple[str, str] = (str(auth.db_path()), store)
    current: tuple[tuple[str, str], ...] = tuple(found for found in _CLEAN.get() if found != key)
    _CLEAN.set(current + ((key,) if clean else ()))


async def _triggers_complete(conn: aiosqlite.Connection) -> bool:
    from . import prices

    tables: set[str] = await prices._tables(conn)
    wanted: set[str] = {trigger_name(source.table, op) for source in sources() if source.table in tables for op in OPS}
    present: set[str] = {
        row[0]
        for row in await conn.execute_fetchall(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND substr(name,1,?)=?", (len(TRIGGER_PREFIX), TRIGGER_PREFIX)
        )
    }
    return wanted <= present


async def _no_dirt(conn: aiosqlite.Connection, store: str, kinds: Sequence[str] | None = None) -> bool:
    if kinds is None:
        rows: list[Any] = list(await conn.execute_fetchall("SELECT 1 FROM shared_price_dirty WHERE store=? LIMIT 1", (store,)))
    else:
        rows = list(
            await conn.execute_fetchall(
                "SELECT 1 FROM shared_price_dirty WHERE store=? AND kind IN(SELECT value FROM json_each(?)) LIMIT 1", (store, json.dumps(list(kinds)))
            )
        )
    return not rows


async def ready(conn: aiosqlite.Connection, store: str, generation: str | None, today: str | None) -> bool:
    """Whether the product rows of `store` are complete and current: a finished build of this format for the token of the
    catalogue (and `generation` and `today` when given), with nothing left to drain. A request that drained the store itself
    (`refresh`) skips the check for pending rows."""
    row: Any = await auth.fetchone(conn, "SELECT generation,ready_revision FROM shared_price_build WHERE store=?", (store,))
    if row is None or (generation is not None and row[0] != generation):
        return False
    parsed: tuple[str, str] | None = parse_build(row[1])
    if parsed is None or parsed[0] != await token(conn, store) or (today is not None and parsed[1] != today):
        return False
    return _clean_here(store) or (await _triggers_complete(conn) and await _no_dirt(conn, store))


async def receipts_ready(conn: aiosqlite.Connection, store: str, generation: str, today: str) -> bool:
    """Whether the stored receipt lines of `store` are those a fresh link would give today."""
    row: Any = await auth.fetchone(conn, "SELECT revision FROM shared_price_receipt_meta WHERE store=?", (store,))
    if row is None or row[0] != receipt_revision(await token(conn, store), generation, today):
        return False
    return _clean_here(store) or (await _triggers_complete(conn) and await _no_dirt(conn, store, RECEIPT_KINDS))


async def receipts_linked(conn: aiosqlite.Connection, store: str, generation: str) -> bool:
    """Whether the stored receipt lines of `store` belong to this token and generation, whatever their day: the build and the
    drain read them while they bring every product to the current state themselves."""
    row: Any = await auth.fetchone(conn, "SELECT revision FROM shared_price_receipt_meta WHERE store=?", (store,))
    parsed: tuple[str, str, str] | None = parse_receipts(row[0] if row else None)
    return parsed is not None and parsed[0] == await token(conn, store) and parsed[1] == generation


# --- the drain ----------------------------------------------------------------------------------------------------------


async def refresh(conn: aiosqlite.Connection, snapshot: Any, store: str, today: str, *, request: bool = True) -> bool:
    """Drain the pending changes of `store`, one bounded round per transaction, and remember for this request whether the
    store ended clean. A request waits at most `REQUEST_WAIT_MS` for another writer and re-indexes at most `REQUEST_PRODUCTS`
    products: past that the rest goes on in the background (`_drain_later`), and the readers of the request use the fallbacks.
    The build (`request=False`) waits as long as any connection and drains everything."""
    wait_ms: int | None = REQUEST_WAIT_MS if request else None
    budget: int | None = REQUEST_PRODUCTS if request else None
    key: tuple[str, str] = (str(auth.db_path()), store)
    locks: dict[tuple[str, str], asyncio.Lock] = _LOCKS.setdefault(asyncio.get_running_loop(), {})
    lock: asyncio.Lock = locks.setdefault(key, asyncio.Lock())
    clean: bool = False
    indexed: int = 0
    if wait_ms is not None:
        await conn.execute(f"PRAGMA busy_timeout={int(wait_ms)}")
    try:
        await ensure_triggers(conn)
        for _ in range(MAX_ROUNDS):
            if budget is not None and indexed >= budget:
                _drain_later(store)
                break
            async with lock:  # per round, so that a long drain in the background lets a request in between
                worked, products = await _round(conn, snapshot, store, today)
            if not worked:
                clean = True
                break
            indexed += products
            await asyncio.sleep(0)
    except sqlite3.OperationalError as exc:
        if "locked" not in str(exc) and "busy" not in str(exc):
            log.warning("Could not refresh the read model of %s", store, exc_info=True)
    except Exception:  # noqa: BLE001 - the readers fall back to the sources, so a failed drain never fails a request
        log.warning("Could not refresh the read model of %s", store, exc_info=True)
    finally:
        if wait_ms is not None:
            await conn.execute(f"PRAGMA busy_timeout={int(auth.BUSY_SECONDS * 1000)}")
    _mark_clean(store, clean)
    return clean


def _drain_later(store: str) -> None:
    """Drain the rest of `store` in the background (`shared_price_overlay.prepare`), once per database and store at a time."""
    from . import shared_price_overlay, tasks

    key: tuple[str, str] = (str(auth.db_path()), store)
    running: asyncio.Task[Any] | None = _DRAINS.get(key)
    if running is not None and not running.done():
        return
    task: asyncio.Task[Any] = tasks.spawn(f"shared-price-drain-{store}", shared_price_overlay.prepare(store))
    _DRAINS[key] = task
    task.add_done_callback(lambda done: _DRAINS.pop(key, None) if _DRAINS.get(key) is done else None)


async def drained(store: str) -> None:
    """Wait for the background drain of `store`, if one runs (the tests)."""
    running: asyncio.Task[Any] | None = _DRAINS.get((str(auth.db_path()), store))
    if running is not None:
        await running


async def _pending(conn: aiosqlite.Connection, snapshot: Any, store: str, today: str) -> bool:
    """Whether a round has anything to do: a pending row, or a build or receipt state of another day."""
    if not await _no_dirt(conn, store):
        return True
    generation: str = getattr(snapshot, "generation", "") if snapshot is not None else ""
    build: Any = await auth.fetchone(conn, "SELECT generation,ready_revision FROM shared_price_build WHERE store=?", (store,))
    parsed: tuple[str, str] | None = parse_build(build[1]) if build and build[0] == generation else None
    if parsed is not None and parsed[1] != today:
        return True
    meta: Any = await auth.fetchone(conn, "SELECT revision FROM shared_price_receipt_meta WHERE store=?", (store,))
    receipts: tuple[str, str, str] | None = parse_receipts(meta[0] if meta else None)
    return receipts is not None and receipts[1] == generation and receipts[2] != today


async def _round(conn: aiosqlite.Connection, snapshot: Any, store: str, today: str) -> tuple[bool, int]:
    """One transaction of the drain: the pending names and links first (a relink changes the products of its lines), then one
    batch of products. Whether this round changed something (so another may have work), and how many products it re-indexed."""
    if not await _pending(conn, snapshot, store, today):
        return False, 0
    generation: str = getattr(snapshot, "generation", "") if snapshot is not None else ""
    await conn.execute("BEGIN IMMEDIATE")
    try:
        current: str = await token(conn, store)
        products: int = 0
        worked: bool = await _receipts_round(conn, snapshot, store, today, generation, current)
        if not worked:
            worked, products = await _products_round(conn, snapshot, store, today, generation, current)
        await conn.commit()
        return worked, products
    except BaseException:
        await conn.rollback()
        raise


async def _taken(conn: aiosqlite.Connection, store: str, rows: Iterable[Any]) -> None:
    """Remove the drained rows, only where no write marked them again since they were read (`n`)."""
    await conn.executemany(
        "DELETE FROM shared_price_dirty WHERE store=? AND kind=? AND product_id=? AND title=? AND n=?",
        [(store, row[0], row[1], row[2], row[3]) for row in rows],
    )


_RECEIPT_ROWS_SQL: str = "DELETE FROM shared_price_dirty WHERE store=? AND kind IN(SELECT value FROM json_each(?))"


async def _receipts_round(conn: aiosqlite.Connection, snapshot: Any, store: str, today: str, generation: str, current: str) -> bool:
    """The pending names and links, `RECEIPT_BATCH` at a time. True when it relinked or left more of them for the next round.
    Inside the transaction of the round no other writer adds a row, so a relink covers every pending one."""
    kinds: str = json.dumps(list(RECEIPT_KINDS))
    rows: list[Any] = list(
        await conn.execute_fetchall(
            "SELECT kind,product_id,title,n FROM shared_price_dirty WHERE store=? AND kind IN(SELECT value FROM json_each(?)) LIMIT ?",
            (store, kinds, RECEIPT_BATCH),
        )
    )
    meta: Any = await auth.fetchone(conn, "SELECT revision FROM shared_price_receipt_meta WHERE store=?", (store,))
    parsed: tuple[str, str, str] | None = parse_receipts(meta[0] if meta else None)
    if parsed is None or parsed[0] != current or parsed[1] != generation:
        # No stored lines of this token and generation: a reader links from scratch until a build links them again.
        await conn.execute(_RECEIPT_ROWS_SQL, (store, kinds))
        return False
    relink: str = await _relink(conn, snapshot, store, rows, parsed[2], today)
    if relink == SAME_NAMES and len(rows) >= RECEIPT_BATCH:
        relink = ALL_LINES  # the rows beyond this batch were not checked, and the relink below takes them all
    if relink:
        from . import shared_price_readmodel

        await shared_price_readmodel.relink_receipts(conn, snapshot, store, today, generation, current, mark=True, same_names=relink == SAME_NAMES)
        await conn.execute(_RECEIPT_ROWS_SQL, (store, kinds))
        return True
    if parsed[2] != today:
        await conn.execute("UPDATE shared_price_receipt_meta SET revision=? WHERE store=?", (receipt_revision(current, generation, today), store))
    await _taken(conn, store, rows)
    return len(rows) >= RECEIPT_BATCH


ALL_LINES: str = "all"
SAME_NAMES: str = "same names"


async def _relink(conn: aiosqlite.Connection, snapshot: Any, store: str, rows: list[Any], linked_day: str, today: str) -> str:
    """How a pending change can move a receipt line, or "" when it cannot. `ALL_LINES`: a new receipt or import, an account
    order, a line that a new day reveals or hides, a link with the key of a line, or a name the linking read (or could now read).
    `SAME_NAMES`: only a price of a product whose name has more than one product, so the selected names stay as they are."""
    kinds: set[str] = {row[0] for row in rows}
    if RECEIPTS in kinds:
        return ALL_LINES
    if ACCOUNTS in kinds and await _accounts_moved(conn, store):
        return ALL_LINES
    if linked_day != today and await _lines_between(conn, store, min(linked_day, today), max(linked_day, today)):
        return ALL_LINES
    links: list[tuple[str, str]] = [_link_key(str(row[1]), str(row[2])) for row in rows if row[0] == LINK]
    if any(kind == "?" for kind, _ in links):
        return ALL_LINES
    names: list[tuple[str, str]] = await compute.run(_names_of, rows)
    found: set[tuple[str, str]] = await _keys(conn, store, await compute.run(_probes, links, names))
    if any(key in found for key in links):
        return ALL_LINES
    relevant: list[tuple[str, str]] = await compute.run(_relevant_names, names, found)
    if relevant and await _names_moved(conn, snapshot, store, relevant):
        return ALL_LINES
    several: list[str] = sorted({pid for pid, _ in names if ("several", pid) in found})
    if several and await _prices_moved(conn, snapshot, store, several):
        return SAME_NAMES if snapshot is None else ALL_LINES
    return ""


async def _accounts_moved(conn: aiosqlite.Connection, store: str) -> bool:
    """Whether the account orders that replace an import (`price_import.account_orders`) are another set than the linking
    used. A sync saves an order again and deletes and inserts its lines: that changes nothing here as long as the same orders
    keep lines. Without imports of the store they change nothing at all."""
    from . import price_import

    if not await conn.execute_fetchall("SELECT 1 FROM imported_purchases WHERE store=? LIMIT 1", (store,)):
        return False
    numbers, days = await price_import.account_orders(conn, store)
    stored: list[Any] = list(
        await conn.execute_fetchall("SELECT kind,value FROM shared_price_receipt_keys WHERE store=? AND kind IN('account','account_day')", (store,))
    )
    return {("account", number) for number in numbers} | {("account_day", day) for day in days} != {(str(row[0]), str(row[1])) for row in stored}


def _link_key(kind: str, key: str) -> tuple[str, str]:
    return ({"article": "article", "name": "name", "rejection": "shared"}.get(kind, "?"), key)


def _names_of(rows: list[Any]) -> list[tuple[str, str]]:
    return [(str(row[1]), str(row[2])) for row in rows if row[0] == NAME]


def _probes(links: list[tuple[str, str]], names: list[tuple[str, str]]) -> str:
    """As JSON, the keys to look up for the pending links and names: the key of each link, and for each (product, title) pair
    the keys that put it among the names the linking reads (its product, folded title and key) and its product as one of a
    name with more products."""
    from . import name_keys, product_links

    found: set[tuple[str, str]] = set(links)
    for pid, title in names:
        found.update({("id", pid), ("fold", product_links.fold(title)), ("near", name_keys.key(title)), ("several", pid)})
    return json.dumps([list(probe) for probe in sorted(found)])


def _relevant_names(names: list[tuple[str, str]], found: set[tuple[str, str]]) -> list[tuple[str, str]]:
    from . import name_keys, product_links

    return sorted(
        {
            (pid, title)
            for pid, title in names
            if ("id", pid) in found or ("fold", product_links.fold(title)) in found or ("near", name_keys.key(title)) in found
        }
    )


async def _keys(conn: aiosqlite.Connection, store: str, probes: str) -> set[tuple[str, str]]:
    rows: list[Any] = list(
        await conn.execute_fetchall(
            "SELECT k.kind,k.value FROM json_each(?) w CROSS JOIN shared_price_receipt_keys k "
            "ON k.store=? AND k.kind=json_extract(w.value,'$[0]') AND k.value=json_extract(w.value,'$[1]')",
            (probes, store),
        )
    )
    return {(str(row[0]), str(row[1])) for row in rows}


async def _names_moved(conn: aiosqlite.Connection, snapshot: Any, store: str, pairs: list[tuple[str, str]]) -> bool:
    """Whether the sources of the linking now hold another set of these (product, title) pairs than the linking read."""
    from . import shared_price_readmodel

    ids: str = json.dumps(sorted({pid for pid, _ in pairs}))
    wanted: set[tuple[str, str]] = set(pairs)
    now: set[tuple[int, str, str]] = set()
    for index, (sql, args) in enumerate(await shared_price_readmodel.receipt_name_sources(conn, snapshot, store)):
        rows: list[Any] = list(
            await conn.execute_fetchall(
                f"SELECT DISTINCT product_id,title FROM ({sql}) WHERE product_id IN (SELECT value FROM json_each(?))", (*args, ids)
            )
        )
        now.update((index, str(row[0]), str(row[1])) for row in rows if (str(row[0]), str(row[1])) in wanted)
    stored: list[Any] = list(
        await conn.execute_fetchall(
            "SELECT n.source,n.product_id,n.title FROM json_each(?) w CROSS JOIN shared_price_receipt_names n "
            "ON n.store=? AND n.product_id=json_extract(w.value,'$[0]') AND n.title=json_extract(w.value,'$[1]')",
            (json.dumps([list(pair) for pair in pairs]), store),
        )
    )
    return now != {(int(row[0]), str(row[1]), str(row[2])) for row in stored}


async def _prices_moved(conn: aiosqlite.Connection, snapshot: Any, store: str, ids: list[str]) -> bool:
    """Whether a product whose name has more than one product now has other prices than the linking compared."""
    from . import prices, shared_price_readmodel

    known: dict[str, set[float]] = await prices._known_prices(conn, await shared_price_readmodel.receipt_name_sources(conn, snapshot, store), ids)
    stored: dict[str, str] = {
        str(row[0]): str(row[1])
        for row in await conn.execute_fetchall(
            "SELECT product_id,prices FROM shared_price_receipt_prices WHERE store=? AND product_id IN(SELECT value FROM json_each(?))",
            (store, json.dumps(ids)),
        )
    }
    return any(stored.get(pid, "[]") != json.dumps(sorted(known.get(pid, ()))) for pid in ids)


async def _lines_between(conn: aiosqlite.Connection, store: str, after: str, until: str) -> bool:
    """Whether a receipt or import line is dated after `after` and up to `until`: a new day shows or hides it."""
    from . import prices

    tables: set[str] = await prices._tables(conn)
    if "imported_purchases" in tables and await conn.execute_fetchall(
        "SELECT 1 FROM imported_purchases WHERE store=? AND day>? AND day<=? LIMIT 1", (store, after, until)
    ):
        return True
    if store == "ah" and prices._RECEIPT_TABLES <= tables:
        return bool(await conn.execute_fetchall(f"SELECT 1 FROM ({prices._RECEIPTS_SQL}) WHERE day>? AND day<=? LIMIT 1", (after, until)))
    return False


async def _products_round(conn: aiosqlite.Connection, snapshot: Any, store: str, today: str, generation: str, current: str) -> tuple[bool, int]:
    from . import shared_price_readmodel

    build: Any = await auth.fetchone(conn, "SELECT generation,ready_revision FROM shared_price_build WHERE store=?", (store,))
    parsed: tuple[str, str] | None = parse_build(build[1]) if build and build[0] == generation else None
    if parsed is None or parsed[0] != current:
        return False, 0  # the pending products wait for the build, which reads them as they are when it gets there
    worked: bool = False
    if parsed[1] != today:
        await mark_day(conn, store, min(parsed[1], today), max(parsed[1], today))
        revision: str = build_revision(current, today)
        await conn.execute("UPDATE shared_price_build SET revision=?,ready_revision=? WHERE store=?", (revision, revision, store))
        worked = True
    rows: list[Any] = list(
        await conn.execute_fetchall(
            "SELECT kind,product_id,title,n FROM shared_price_dirty WHERE store=? AND kind=? ORDER BY product_id LIMIT ?",
            (store, PRODUCT, BATCH_SIZE),
        )
    )
    if not rows:
        return worked, 0
    await shared_price_readmodel.index_batch(conn, snapshot, store, today, [str(row[1]) for row in rows])
    await _taken(conn, store, rows)
    return True, len(rows)


async def mark_day(conn: aiosqlite.Connection, store: str, after: str, until: str) -> None:
    """Mark the products whose rows a day change between `after` and `until` can change: every product in `offers` (which
    offers count as upcoming, and the day of one without a start), and every product with a purchase or a linked receipt line
    dated after `after` and up to `until`."""
    from . import prices

    await conn.execute(
        f"{_INSERT}SELECT DISTINCT store,'{PRODUCT}',CAST(product_id AS TEXT),'' FROM offers WHERE store=? AND product_id IS NOT NULL{_UPSERT_SQL}",
        (store,),
    )
    for sql in prices._purchase_sources(await prices._tables(conn), store):
        await conn.execute(
            f"{_INSERT}SELECT DISTINCT ?,'{PRODUCT}',CAST(product_id AS TEXT),'' FROM ({sql}) WHERE day>? AND day<=? AND product_id IS NOT NULL{_UPSERT_SQL}",
            (store, after, until),
        )
    await conn.execute(
        f"{_INSERT}SELECT DISTINCT r.store,'{PRODUCT}',r.product_id,'' FROM shared_price_receipts r, json_each(r.doc) l "
        f"WHERE r.store=? AND json_extract(l.value,'$.day')>? AND json_extract(l.value,'$.day')<=?{_UPSERT_SQL}",
        (store, after, until),
    )


async def mark_products(conn: aiosqlite.Connection, store: str, ids: Iterable[str]) -> None:
    await conn.executemany(f"{_INSERT}VALUES(?,'{PRODUCT}',?,''){_UPSERT_SQL}", [(store, pid) for pid in ids])
