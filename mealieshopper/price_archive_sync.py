"""Import shared AH weekly JSON documents without scanning existing price history.

Document checkpoints and the scheduler interval are derived state. Each document and
its checkpoint commit together. A renewable SQLite lease fences concurrent workers.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import sqlite3
import time
import uuid
from contextlib import closing
from os import environ
from typing import Any

from . import auth, price_archive, prices, thread_db

log: logging.Logger = logging.getLogger(__name__)
MAX_DOCUMENT_BYTES: int = 4 * 1024 * 1024
REFRESH_SECONDS: int = 24 * 60 * 60
RETRY_SECONDS: int = 15 * 60
LEASE_SECONDS: int = 180
DOCUMENT: re.Pattern[str] = re.compile(r"ah/prices/ahprijsindex-[A-Za-z0-9._-]+\.json\Z")
SCHEMA: str = """
CREATE TABLE IF NOT EXISTS shared_price_documents (
 path TEXT PRIMARY KEY, sha256 TEXT NOT NULL, bytes INTEGER NOT NULL, imported_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS shared_price_sync (
 id INTEGER PRIMARY KEY CHECK (id=1), owner TEXT, lease_until REAL NOT NULL DEFAULT 0,
 next_check REAL NOT NULL DEFAULT 0
);
"""
SAVE_SQL: str = """
INSERT INTO price_observations (store, product_id, day, source, title, brand, unit_size, image, url, price, seen_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (store, product_id, day) DO UPDATE SET
 title=excluded.title, brand=excluded.brand, unit_size=excluded.unit_size,
 image=excluded.image, url=excluded.url, price=excluded.price, seen_at=excluded.seen_at
WHERE price_observations.source=excluded.source AND
 (price_observations.title, price_observations.brand, price_observations.unit_size,
  price_observations.image, price_observations.url, price_observations.price, price_observations.seen_at)
 IS NOT (excluded.title, excluded.brand, excluded.unit_size,
         excluded.image, excluded.url, excluded.price, excluded.seen_at)
"""


def enabled() -> bool:
    return environ.get("MEALIESHOPPER_SHARED_PRICES", "true").strip().lower() not in {"0", "false", "no", "off"}


async def ensure_schema() -> None:
    await price_archive.ensure_schema()
    async with auth.connect() as conn:
        await auth.ensure_schema(conn, "shared_prices", SCHEMA)


def reset(conn: sqlite3.Connection) -> None:
    """Invalidate checkpoints and fence any importer during a destructive transaction."""
    names: set[str] = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('shared_price_documents','shared_price_sync')")
    }
    for name in names:
        conn.execute(f"DELETE FROM {name}")


def _claim(owner: str, due: bool, now: float) -> bool:
    with closing(thread_db.connect()) as conn:
        conn.execute("INSERT OR IGNORE INTO shared_price_sync(id) VALUES (1)")
        claimed: sqlite3.Cursor = conn.execute(
            "UPDATE shared_price_sync SET owner=?, lease_until=? WHERE id=1 AND lease_until<=? AND (?=0 OR next_check<=?)",
            (owner, time.time() + LEASE_SECONDS, time.time(), int(due), now),
        )
        conn.commit()
        return claimed.rowcount == 1


def _renew(owner: str) -> None:
    with closing(thread_db.connect()) as conn:
        renewed: sqlite3.Cursor = conn.execute(
            "UPDATE shared_price_sync SET lease_until=? WHERE id=1 AND owner=? AND lease_until>?",
            (time.time() + LEASE_SECONDS, owner, time.time()),
        )
        conn.commit()
        if renewed.rowcount != 1:
            raise RuntimeError("Shared price lease expired")


def _release(owner: str, next_check: float | None) -> None:
    with closing(thread_db.connect()) as conn:
        conn.execute(
            "UPDATE shared_price_sync SET owner=NULL, lease_until=0, next_check=COALESCE(?,next_check) WHERE id=1 AND owner=?",
            (next_check, owner),
        )
        conn.commit()


def _unchanged(path: str, size: int, digest: str) -> bool:
    with closing(thread_db.connect()) as conn:
        return conn.execute("SELECT 1 FROM shared_price_documents WHERE path=? AND sha256=? AND bytes=?", (path, digest, size)).fetchone() is not None


def _document_rows(body: bytes) -> list[tuple[Any, ...]]:
    data: Any = price_archive._strict_json(body)
    if not isinstance(data, list):
        raise ValueError("Shared price document must be a list")
    rows: list[tuple[Any, ...]] = []
    seen: set[tuple[Any, ...]] = set()
    bounds: price_archive.Today = price_archive.Today()
    for item in data:
        if not isinstance(item, dict) or item.get("store") != "ah" or item.get("source") != prices.PRICE_INDEX:
            raise ValueError("Shared prices require AH ahprijsindex rows")
        row: tuple[Any, ...] = price_archive.normalise_price(item, bounds)
        key: tuple[Any, ...] = row[:3]
        if key in seen:
            raise ValueError("Duplicate shared price observation")
        seen.add(key)
        rows.append(row)
    return rows


def _import_document(owner: str, path: str, size: int, digest: str, body: bytes) -> int:
    if len(body) != size or hashlib.sha256(body).hexdigest() != digest:
        raise price_archive.ArchiveError("site_checksum", 502, file=path)
    rows: list[tuple[Any, ...]] = _document_rows(body)
    with closing(thread_db.connect()) as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            lease: sqlite3.Row | None = conn.execute(
                "SELECT 1 FROM shared_price_sync WHERE id=1 AND owner=? AND lease_until>?", (owner, time.time())
            ).fetchone()
            if lease is None:
                raise RuntimeError("Shared price lease expired")
            saved: sqlite3.Cursor = conn.executemany(SAVE_SQL, rows)
            changed: int = saved.rowcount
            conn.execute(
                "INSERT INTO shared_price_documents VALUES (?,?,?,?) ON CONFLICT(path) DO UPDATE SET "
                "sha256=excluded.sha256, bytes=excluded.bytes, imported_at=excluded.imported_at",
                (path, digest, size, time.time()),
            )
            conn.commit()
            return changed
        except BaseException:
            conn.rollback()
            raise


async def _thread(function: Any, *args: Any) -> Any:
    # A cancelled to_thread call still runs. Wait for its transaction before releasing the lease.
    work: asyncio.Task[Any] = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(work)
    except asyncio.CancelledError:
        try:
            await work
        finally:
            raise


async def _heartbeat(owner: str) -> None:
    while True:
        await asyncio.sleep(LEASE_SECONDS / 3)
        await _thread(_renew, owner)


async def _consume(owner: str) -> dict[str, Any]:
    raw: bytes = await price_archive._site_get("index.json", price_archive.MAX_CATALOG_BYTES)
    catalog: Any = await _thread(price_archive._strict_json, raw)
    files: list[tuple[str, int, str]] = await _thread(price_archive.site_files, catalog)
    result: dict[str, Any] = {"documents": 0, "changed": 0, "skipped": 0}
    for path, size, digest in files:
        if DOCUMENT.fullmatch(path) is None:
            continue
        if size > MAX_DOCUMENT_BYTES:
            raise ValueError("Shared price document exceeds 4 MiB")
        await _thread(_renew, owner)
        if await _thread(_unchanged, path, size, digest):
            result["skipped"] += 1
            continue
        body: bytes = await price_archive._site_get(path, size)
        result["changed"] += await _thread(_import_document, owner, path, size, digest, body)
        result["documents"] += 1
    return result


async def sync_site(*, _due: bool = False, _now: float | None = None) -> dict[str, Any]:
    """Check the index and transactionally import changed weekly documents."""
    await ensure_schema()
    owner: str = uuid.uuid4().hex
    now: float = time.time() if _now is None else _now
    heartbeat: asyncio.Task[None] | None = None
    next_check: float | None = None
    try:
        if not await _thread(_claim, owner, _due, now):
            return {"busy": True}
        heartbeat = asyncio.create_task(_heartbeat(owner))
        result: dict[str, Any] = await _consume(owner)
        next_check = now + REFRESH_SECONDS
        return result
    except Exception:
        next_check = now + RETRY_SECONDS
        raise
    finally:
        if heartbeat is not None:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
        await _thread(_release, owner, next_check)


async def run_if_due(now: float | None = None) -> dict[str, Any] | None:
    """Default-on scheduler entry point, with independent retry and failure isolation."""
    if not enabled():
        return None
    try:
        result: dict[str, Any] = await sync_site(_due=True, _now=now)
        return None if result.get("busy") else result
    except Exception as exc:
        log.warning("Gedeelde weekprijzen ophalen mislukt", exc_info=True)
        return {"error": type(exc).__name__}
