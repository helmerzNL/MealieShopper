"""Conservative, resumable classification of retained public price imports.

A source name never proves ownership. A complete imported representation must
match a public generation before it becomes a retained public shadow. Local writes
revoke that proof in the same transaction.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from collections.abc import Sequence
from os import environ
from typing import Any

import aiosqlite

from . import auth, compute

TABLES: tuple[str, ...] = (
    "shared_price_equivalence",
    "shared_price_migration",
    "shared_price_keys",
    "shared_price_build",
    "shared_price_receipts",
    "shared_price_receipt_meta",
    "shared_price_lines",
)
ORIGIN_TABLE: str = "shared_price_origins"
COLUMNS: tuple[str, ...] = ("store", "product_id", "day", "source", "title", "brand", "unit_size", "image", "url", "price", "seen_at")
BATCH_SIZE: int = 64
SCHEMA: str = """
CREATE TABLE IF NOT EXISTS shared_price_equivalence (
 store TEXT NOT NULL, product_id TEXT NOT NULL, day TEXT NOT NULL,
 generation TEXT NOT NULL, fingerprint TEXT NOT NULL, projection_generation TEXT NOT NULL, PRIMARY KEY(store,product_id,day)
);
CREATE TABLE IF NOT EXISTS shared_price_origins (
 store TEXT NOT NULL, product_id TEXT NOT NULL, day TEXT NOT NULL,
 origin TEXT NOT NULL, PRIMARY KEY(store,product_id,day)
);
CREATE TABLE IF NOT EXISTS shared_price_migration (
 store TEXT PRIMARY KEY, generation TEXT NOT NULL, cursor INTEGER NOT NULL DEFAULT 0,
 revision TEXT NOT NULL DEFAULT '', ready_revision TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS shared_price_receipts (
 store TEXT NOT NULL, product_id TEXT NOT NULL, doc TEXT NOT NULL, PRIMARY KEY(store,product_id)
);
CREATE TABLE IF NOT EXISTS shared_price_receipt_meta (store TEXT PRIMARY KEY, revision TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS shared_price_build (
 store TEXT PRIMARY KEY, generation TEXT NOT NULL, cursor TEXT NOT NULL DEFAULT '',
 revision TEXT NOT NULL, ready_revision TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS shared_price_keys (
 store TEXT NOT NULL, product_id TEXT NOT NULL, same_key TEXT NOT NULL, family_key TEXT NOT NULL,
 PRIMARY KEY(store,product_id)
);
CREATE INDEX IF NOT EXISTS shared_price_keys_family ON shared_price_keys(store,family_key,product_id);
CREATE TRIGGER IF NOT EXISTS shared_price_changed_insert AFTER INSERT ON price_observations BEGIN
 DELETE FROM shared_price_equivalence WHERE store=NEW.store AND product_id=NEW.product_id AND day=NEW.day;
 INSERT INTO shared_price_origins VALUES(NEW.store,NEW.product_id,NEW.day,'local')
 ON CONFLICT(store,product_id,day) DO UPDATE SET origin='local';
 UPDATE shared_price_migration SET cursor=0,ready_revision='' WHERE store=NEW.store;
END;
CREATE TRIGGER IF NOT EXISTS shared_price_changed_update AFTER UPDATE ON price_observations BEGIN
 DELETE FROM shared_price_equivalence WHERE store=OLD.store AND product_id=OLD.product_id AND day=OLD.day;
 DELETE FROM shared_price_equivalence WHERE store=NEW.store AND product_id=NEW.product_id AND day=NEW.day;
 INSERT INTO shared_price_origins VALUES(NEW.store,NEW.product_id,NEW.day,'local')
 ON CONFLICT(store,product_id,day) DO UPDATE SET origin='local';
 UPDATE shared_price_migration SET cursor=0,ready_revision='' WHERE store IN (OLD.store,NEW.store);
END;
CREATE TRIGGER IF NOT EXISTS shared_price_changed_delete AFTER DELETE ON price_observations BEGIN
 DELETE FROM shared_price_equivalence WHERE store=OLD.store AND product_id=OLD.product_id AND day=OLD.day;
 DELETE FROM shared_price_origins WHERE store=OLD.store AND product_id=OLD.product_id AND day=OLD.day;
 UPDATE shared_price_migration SET cursor=0,ready_revision='' WHERE store=OLD.store;
END;
"""


async def ensure(conn: aiosqlite.Connection) -> None:
    await auth.ensure_schema(conn, "shared_price_overlay", _setup)


async def _setup(conn: aiosqlite.Connection) -> None:
    from . import shared_price_lines

    existed: Any = await auth.fetchone(conn, "SELECT 1 FROM sqlite_master WHERE type='table' AND name='shared_price_lines'")
    await conn.executescript(SCHEMA + shared_price_lines.SCHEMA)
    if existed is None:
        await conn.execute("DELETE FROM shared_price_build")
        await conn.commit()


def fingerprint(row: Any) -> str:
    values: list[Any] = [row.get(key) if isinstance(row, dict) else row[key] for key in COLUMNS]
    # SQLite stores REAL prices, so JSON integer and floating representations agree.
    values[9] = float(values[9]) if isinstance(values[9], (int, float)) else values[9]
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def equivalents(snapshot: Any, rows: list[dict[str, Any]]) -> list[tuple[str, str, str, str, str]]:
    historical: list[tuple[str, str, str, str, str]] = []
    unknown: list[dict[str, Any]] = []
    for row in rows:
        origin: str = str(row.get("previous_origin") or "")
        pieces: list[str] = origin.split(":", 2)
        if len(pieces) == 3 and pieces[0] == "public" and pieces[2] == fingerprint(row):
            historical.append((row["store"], row["product_id"], row["day"], pieces[1], pieces[2]))
        else:
            unknown.append(row)
    rows = unknown
    if not rows:
        return historical
    dates: dict[str, list[str]] = {}
    for row in rows:
        dates.setdefault(row["product_id"], []).append(row["day"])
    windows: dict[tuple[str, str], list[str]] = {}
    for pid, days in dates.items():
        windows.setdefault((min(days), max(days)), []).append(pid)
    public: dict[str, list[dict[str, Any]]] = {}
    for (since, until), ids in windows.items():
        for start in range(0, len(ids), 8):
            batch: dict[str, list[dict[str, Any]]] = normalise_public(snapshot.observations(ids[start : start + 8], since, until))
            for pid, observations in batch.items():
                public.setdefault(pid, []).extend(observations)
    by_day: dict[tuple[str, str], str] = {(pid, row["day"]): fingerprint(row) for pid, observations in public.items() for row in observations}
    return historical + [
        (row["store"], row["product_id"], row["day"], snapshot.generation, fingerprint(row))
        for row in rows
        if row["source"] == "ahprijsindex" and by_day.get((row["product_id"], row["day"])) == fingerprint(row)
    ]


async def reconcile_batch(snapshot: Any, *, batch_size: int = BATCH_SIZE, store: str = "ah") -> bool:
    from . import prices

    async with prices._connect() as conn:
        version: str = await revision(conn, store)
        row: Any = await auth.fetchone(conn, "SELECT generation,cursor,revision,ready_revision FROM shared_price_migration WHERE store=?", (store,))
        cursor: int = int(row[1]) if row and row[0] == snapshot.generation and row[2] == version else 0
        if row and row[0] == snapshot.generation and row[3] == version:
            return True
        rows: list[Any] = list(
            await conn.execute_fetchall(
                "SELECT p.rowid AS cursor,p.*,EXISTS(SELECT 1 FROM shared_price_equivalence e "
                "WHERE e.store=p.store AND e.product_id=p.product_id AND e.day=p.day) AS proven, "
                "(SELECT origin FROM shared_price_origins o WHERE o.store=p.store AND o.product_id=p.product_id AND o.day=p.day) AS previous_origin "
                "FROM price_observations p NOT INDEXED WHERE p.store=? AND p.rowid>? "
                "AND p.source='ahprijsindex' AND NOT EXISTS(SELECT 1 FROM shared_price_equivalence e "
                "WHERE e.store=p.store AND e.product_id=p.product_id AND e.day=p.day AND e.projection_generation=?) "
                "AND NOT EXISTS(SELECT 1 FROM shared_price_origins o WHERE o.store=p.store AND o.product_id=p.product_id AND o.day=p.day AND o.origin='local') "
                "ORDER BY p.rowid LIMIT ?",
                (store, cursor, snapshot.generation, batch_size),
            )
        )
    plain: list[dict[str, Any]] = [dict(row) for row in rows if not row["proven"]]
    matched: list[tuple[str, str, str, str, str]] = await offload(equivalents, snapshot, plain)
    async with prices._connect() as conn:
        await conn.execute("BEGIN IMMEDIATE")
        if await revision(conn, store) != version:
            await conn.rollback()
            return False
        await conn.execute(
            "INSERT INTO shared_price_migration(store,generation,revision) VALUES(?,?,?) "
            "ON CONFLICT(store) DO UPDATE SET generation=excluded.generation,revision=excluded.revision,ready_revision=''",
            (store, snapshot.generation, version),
        )
        await conn.executemany(
            "INSERT OR IGNORE INTO shared_price_equivalence VALUES(?,?,?,?,?,?)", [(*match, snapshot.generation) for match in matched]
        )
        await conn.executemany(
            "INSERT INTO shared_price_origins VALUES(?,?,?,?) ON CONFLICT(store,product_id,day) "
            "DO UPDATE SET origin=excluded.origin WHERE shared_price_origins.origin!='local'",
            [(store, pid, day, f"public:{generation}:{digest}") for store, pid, day, generation, digest in matched],
        )
        raw_ids: str = json.dumps([int(row["cursor"]) for row in rows])
        covered: str = "1"
        if hasattr(snapshot, "path"):
            from . import shared_price_readmodel

            await shared_price_readmodel.attach(conn, snapshot)
            covered = coverage_sql("shared_price_equivalence")
        await conn.execute(
            "UPDATE shared_price_equivalence SET projection_generation=CASE WHEN " + covered + " THEN ? ELSE '' END "
            "WHERE (store,product_id,day) IN(SELECT store,product_id,day FROM price_observations WHERE rowid IN(SELECT value FROM json_each(?)))",
            (snapshot.generation, raw_ids),
        )
        last: int = int(rows[-1]["cursor"]) if rows else cursor
        done: bool = len(rows) < batch_size
        await conn.execute("UPDATE shared_price_migration SET cursor=?,ready_revision=? WHERE store=?", (last, version if done else "", store))
        await conn.commit()
        return done


async def reconcile(snapshot: Any, *, batch_size: int = BATCH_SIZE, store: str = "ah") -> None:
    from . import shared_prices

    while True:
        await shared_prices.yield_background()
        if await reconcile_batch(snapshot, batch_size=batch_size, store=store):
            return
        await asyncio.sleep(0)


def merge_observations(
    snapshot: Any, local: Sequence[Any], public: dict[str, list[dict[str, Any]]], equal: set[tuple[str, str, str]]
) -> dict[str, list[Any]]:
    generation: str = snapshot.generation if snapshot is not None else ""
    found: dict[str, dict[str, Any]] = {pid: {row["day"]: row for row in rows} for pid, rows in public.items()}
    for row in local:
        if (row["product_id"], row["day"], generation) in equal:
            continue
        found.setdefault(row["product_id"], {})[row["day"]] = row
    return {pid: [days[day] for day in sorted(days)] for pid, days in found.items()}


async def observations(conn: aiosqlite.Connection, snapshot: Any, store: str, ids: list[str]) -> dict[str, list[Any]]:
    if not ids:
        return {}
    wanted: str = json.dumps(ids)
    if snapshot is not None and hasattr(snapshot, "path"):
        from . import shared_price_readmodel

        await shared_price_readmodel.attach(conn, snapshot)
    shadow: str = (
        " AND NOT (" + shadow_sql("p") + ")"
        if snapshot is not None and hasattr(snapshot, "path")
        else (
            " AND NOT EXISTS(SELECT 1 FROM shared_price_equivalence e WHERE e.store=p.store AND e.product_id=p.product_id AND e.day=p.day)"
            if snapshot is not None
            else ""
        )
    )
    local: list[Any] = list(
        await conn.execute_fetchall(
            "SELECT p.* FROM price_observations p WHERE p.store=? AND p.product_id IN (SELECT value FROM json_each(?))"
            + shadow
            + " ORDER BY p.product_id,p.day",
            (store, wanted),
        )
    )
    public: dict[str, list[dict[str, Any]]] = {}
    if snapshot is not None:
        for start in range(0, len(ids), 32):
            batch: dict[str, list[dict[str, Any]]] = await offload(snapshot.observations, ids[start : start + 32])
            public.update(await compute.run(normalise_public, batch))
    return await compute.run(merge_observations, snapshot, local, public, set())


def reset(conn: sqlite3.Connection) -> None:
    names: set[str] = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table in TABLES:
        if table in names:
            conn.execute(f"DELETE FROM {table}")


async def revision(conn: aiosqlite.Connection, store: str) -> str:
    from . import prices

    row: Any = await prices._catalogue_row(conn, store)
    return f"{row['token']}:{row['products']}:{row['prices']}"


async def prepare(store: str = "ah") -> None:
    from . import prices, shared_price_readmodel, shared_prices

    with shared_prices.lease(store) as snapshot:
        if snapshot is not None:
            await reconcile(snapshot, store=store)
        generation: str = snapshot.generation if snapshot else ""
        while True:
            await shared_prices.yield_background()
            async with prices._connect() as conn:
                version: str = await revision(conn, store)
                state: Any = await auth.fetchone(
                    conn, "SELECT generation,cursor,revision,ready_revision FROM shared_price_build WHERE store=?", (store,)
                )
                if state and state[0] == generation and state[3] == version:
                    return
                cursor: str = str(state[1]) if state and state[0] == generation and state[2] == version else ""
                tables: set[str] = await prices._tables(conn)
                sources: list[tuple[str, tuple[Any, ...]]] = [
                    ("SELECT product_id FROM price_observations WHERE store=?", (store,)),
                    ("SELECT product_id FROM offer_history WHERE store=?", (store,)),
                    ("SELECT product_id FROM offers WHERE store=?", (store,)),
                    ("SELECT product_id FROM price_history_products WHERE store=?", (store,)),
                    *[(f"SELECT product_id FROM ({sql})", ()) for sql in prices._purchase_sources(tables, store)],
                ]
                await shared_price_readmodel.prepare_receipts(conn, store, prices._now()[:10], version, snapshot)
                sources.append(("SELECT product_id FROM shared_price_receipts WHERE store=?", (store,)))
                candidates: set[str] = set()
                for sql, args in sources:
                    candidates.update(
                        row[0]
                        for row in await conn.execute_fetchall(
                            "SELECT DISTINCT product_id FROM (" + sql + ") WHERE product_id>? ORDER BY product_id LIMIT ?",
                            (*args, cursor, BATCH_SIZE),
                        )
                    )
                ids: list[str] = sorted(candidates)[:BATCH_SIZE]
                await conn.execute("BEGIN IMMEDIATE")
                if await revision(conn, store) != version:
                    await conn.rollback()
                    continue
                await shared_price_readmodel.index_batch(conn, snapshot, store, prices._now()[:10], ids)
                ready: bool = len(ids) < BATCH_SIZE
                await conn.execute(
                    "INSERT INTO shared_price_build VALUES(?,?,?,?,?) ON CONFLICT(store) DO UPDATE SET "
                    "generation=excluded.generation,cursor=excluded.cursor,revision=excluded.revision,ready_revision=excluded.ready_revision",
                    (store, generation, ids[-1] if ids else cursor, version, version if ready else ""),
                )
                await conn.commit()
                if ready:
                    return
            await asyncio.sleep(0)


async def reconcile_if_due() -> None:
    await prepare()


async def offload(function: Any, *args: Any) -> Any:
    """Do not release a generation while a cancelled thread still reads its file."""
    work: asyncio.Task[Any] = asyncio.create_task(compute.run(function, *args))
    try:
        from . import tasks

        return await tasks.share(work)
    except asyncio.CancelledError:
        try:
            await tasks.share(work)
        except Exception:
            pass
        raise


def enabled() -> bool:
    return environ.get("MEALIESHOPPER_SHARED_PRICES", "true").strip().lower() not in {"0", "false", "no", "off"}


async def candidates(conn: aiosqlite.Connection, snapshot: Any, store: str, ids: list[str], since: str, until: str) -> dict[str, list[Any]]:
    from . import prices, shared_price_readmodel

    if snapshot is not None:
        await shared_price_readmodel.attach(conn, snapshot)
    shadow: str = " AND NOT (" + shadow_sql("p") + ")" if snapshot is not None else ""
    # Two local rows per product suffice: the lowest fresh point and the newest point.
    # The mask query below still sees every private day, including non-regular observations.
    local: list[Any] = list(
        await conn.execute_fetchall(
            "WITH selected AS (SELECT p.* FROM price_observations p WHERE p.store=? "
            "AND p.product_id IN (SELECT value FROM json_each(?)) AND p.day>=? AND p.day<=? "
            "AND p.source!=? AND typeof(p.price) IN ('real','integer') AND p.price>0" + shadow + "), "
            "ranked AS (SELECT *,row_number() OVER(PARTITION BY product_id ORDER BY "
            "CASE WHEN day>=? THEN 0 ELSE 1 END, CASE WHEN day>=? THEN price END,day DESC) AS chosen,"
            "row_number() OVER(PARTITION BY product_id ORDER BY day DESC) AS newest FROM selected) "
            "SELECT * FROM ranked WHERE chosen=1 OR newest=1 ORDER BY product_id,day",
            (
                store,
                json.dumps(ids),
                since,
                until,
                prices.PUBLIC_OBSERVED_UNKNOWN,
                prices._days_ago(until, prices.FRESH_DAYS),
                prices._days_ago(until, prices.FRESH_DAYS),
            ),
        )
    )
    public: dict[str, list[dict[str, Any]]] = (
        await shared_price_readmodel.public_candidates(conn, snapshot, store, ids, since, until) if snapshot else {}
    )
    return await compute.run(merge_observations, snapshot, local, public, set())


async def await_owned(coroutine: Any) -> Any:
    from . import tasks

    work: asyncio.Task[Any] = asyncio.create_task(coroutine)
    try:
        return await tasks.share(work)
    except asyncio.CancelledError:
        try:
            await work
        except Exception:
            pass
        raise


def normalise_public(observed: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    from . import price_archive

    bounds: Any = price_archive.Today()
    return {pid: [dict(zip(COLUMNS, price_archive.normalise_price(row, bounds), strict=True)) for row in rows] for pid, rows in observed.items()}


def coverage_sql(row: str) -> str:
    return (
        "EXISTS(SELECT 1 FROM shared_public.price_periods c WHERE c.store=" + row + ".store AND c.product_id=" + row + ".product_id "
        "AND " + row + ".day>=c.first_day AND " + row + ".day<=c.last_day "
        "AND CAST(julianday(" + row + ".day)-julianday(c.first_day) AS INTEGER)%c.step_days=0)"
    )


def shadow_sql(row: str) -> str:
    return (
        "EXISTS(SELECT 1 FROM main.shared_price_equivalence e WHERE e.store=" + row + ".store "
        "AND e.product_id=" + row + ".product_id AND e.day=" + row + ".day) AND " + coverage_sql(row)
    )
