"""Lokale kopie van je bezorgde Crisp-bestellingen.

Alles wat bij Crisp wordt opgehaald, staat daarna in SQLite. Lezen gaat altijd uit die kopie en vraagt nooit iets aan
Crisp, dus de bestelhistorie is ook te bekijken als Crisp niet bereikbaar is. Alleen een synchronisatie praat met Crisp.

- Een **bezorgde bestelling** verandert niet meer en wordt niet verwijderd, ook niet als Crisp hem later niet meer toont.
  De geschiedenis komt per pagina binnen (`/order/history`, nieuwste eerst); we stoppen bij een pagina die we al helemaal
  kennen. De producten halen we per synchronisatie voor een beperkt aantal bestellingen op (de rest volgt bij de volgende).
- Aankomende bestellingen (`ready`, `picked`, `delivery`) bewaren we niet: die toont het tabblad live. Een geannuleerde
  bestelling halen we weg.
- Het adres, het IBAN, de betaalwijze, de factuur en de track-and-trace bewaren we bewust niet; alleen dag, tijdvak,
  status, totaal en de producten (zie `crisp._order_header`).
"""
from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from . import auth, crisp
from .ah_lists import _fold

ORDER_DETAILS_PER_SYNC = 25  # een synchronisatie blijft kort; de rest volgt bij de volgende
MAX_FIRST_FAILURES = 3  # zoveel bestellingen achter elkaar zonder één gelukte: stoppen met proberen
MAX_PAGES = 20  # zoveel pagina's geschiedenis per synchronisatie (de app vraagt er een per keer)

SCHEMA = """
CREATE TABLE IF NOT EXISTS crisp_orders (
  order_id TEXT PRIMARY KEY,
  status TEXT,
  total REAL,
  delivery_date TEXT,
  slot_start TEXT,
  slot_end TEXT,
  items_synced INTEGER NOT NULL DEFAULT 0,
  first_seen TEXT NOT NULL,
  synced_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS crisp_order_items (
  order_id TEXT NOT NULL,
  position INTEGER NOT NULL,
  product_id TEXT, title TEXT, unit_size TEXT, quantity INTEGER, price REAL, list_price REAL, image TEXT,
  PRIMARY KEY (order_id, position)
);

CREATE TABLE IF NOT EXISTS crisp_orders_meta (
  key TEXT PRIMARY KEY,
  synced_at TEXT NOT NULL
);
"""

ITEM_COLUMNS = ("product_id", "title", "unit_size", "quantity", "price", "list_price", "image")


def _connect() -> sqlite3.Connection:
    """Een sqlite3-verbinding met het schema, voor `backup.ensure_schemas`. De rest gebruikt `_aconnect`."""
    conn: sqlite3.Connection = auth.connect()
    conn.executescript(SCHEMA)
    return conn


@asynccontextmanager
async def _aconnect() -> AsyncIterator[aiosqlite.Connection]:
    """Async tweeling van `_connect`: hetzelfde schema. Sluit zonder commit, zoals `auth.aconnect`."""
    async with auth.aconnect() as conn:
        await conn.executescript(SCHEMA)
        yield conn


async def _fetchone(conn: aiosqlite.Connection, sql: str, params: tuple[Any, ...] = ()) -> aiosqlite.Row | None:
    async with conn.execute(sql, params) as cursor:
        return await cursor.fetchone()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _item_values(item: dict[str, Any]) -> tuple[Any, ...]:
    return (item.get("id"), item.get("title"), item.get("unitSize"), item.get("quantity"), item.get("price"), item.get("listPrice"), item.get("image"))


def _item_json(row: Any) -> dict[str, Any]:
    return {
        "id": row["product_id"], "title": row["title"], "unitSize": row["unit_size"], "quantity": row["quantity"],
        "price": row["price"], "listPrice": row["list_price"], "image": row["image"],
    }


async def synced_at() -> str | None:
    async with _aconnect() as conn:
        row = await _fetchone(conn, "SELECT synced_at FROM crisp_orders_meta WHERE key = 'orders'")
    return row["synced_at"] if row else None


def is_stored(status: Any) -> bool:
    """Bewaren we een bestelling met deze status? Alleen wat niet meer verandert: niet aankomend en niet geannuleerd."""
    status = str(status or "").strip().lower()
    return status not in crisp.OPEN_STATUSES and status not in crisp.CANCELLED_STATUSES


async def _save_order(conn: aiosqlite.Connection, header: dict[str, Any], items: list[dict[str, Any]] | None) -> None:
    """Bewaar of werk een bestelling bij. `items=None` laat de bewaarde producten staan. Wie aanroept, commit."""
    now = _now()
    existing = await _fetchone(conn, "SELECT first_seen, items_synced FROM crisp_orders WHERE order_id = ?", (header["id"],))
    await conn.execute(
        """
        INSERT INTO crisp_orders (order_id, status, total, delivery_date, slot_start, slot_end, items_synced, first_seen, synced_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(order_id) DO UPDATE SET status = COALESCE(NULLIF(excluded.status, ''), crisp_orders.status),
          total = COALESCE(excluded.total, crisp_orders.total),
          delivery_date = COALESCE(NULLIF(excluded.delivery_date, ''), crisp_orders.delivery_date),
          slot_start = COALESCE(NULLIF(excluded.slot_start, ''), crisp_orders.slot_start),
          slot_end = COALESCE(NULLIF(excluded.slot_end, ''), crisp_orders.slot_end),
          items_synced = excluded.items_synced, synced_at = excluded.synced_at
        """,
        (header["id"], header.get("status"), header.get("total"), header.get("deliveryDate"), header.get("windowStart"),
         header.get("windowEnd"), 1 if items is not None else (existing["items_synced"] if existing else 0),
         existing["first_seen"] if existing else now, now),
    )
    if items is not None:
        await conn.execute("DELETE FROM crisp_order_items WHERE order_id = ?", (header["id"],))
        await conn.executemany(
            f"INSERT INTO crisp_order_items (order_id, position, {', '.join(ITEM_COLUMNS)}) VALUES (?, ?{', ?' * len(ITEM_COLUMNS)})",
            [(header["id"], position, *_item_values(item)) for position, item in enumerate(items)],
        )


def _history(complete: set[str]) -> list[dict[str, Any]]:
    """De geschiedenis, pagina voor pagina, tot een pagina waarvan we alle bezorgde bestellingen al met producten hebben."""
    orders: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(MAX_PAGES):
        page, cursor = crisp.order_history_page(cursor)
        orders += page
        stored = [order for order in page if is_stored(order.get("status"))]
        if not cursor or (stored and all(order["id"] in complete for order in stored)):
            break
    return orders


async def sync_orders() -> dict[str, Any]:
    """Haal je bestelgeschiedenis op en daarna de producten van een aantal bezorgde bestellingen.

    Nieuwste eerst: een eerste synchronisatie vult dus eerst de recente bestellingen met producten aan en elke volgende
    de rest. Lukt het ophalen van de producten niet, dan blijft het overzicht staan en melden we `detailError`.
    """
    async with _aconnect() as conn:
        complete = {row["order_id"] for row in await conn.execute_fetchall("SELECT order_id FROM crisp_orders WHERE items_synced = 1")}
        known = {row["order_id"] for row in await conn.execute_fetchall("SELECT order_id FROM crisp_orders")}
    listed = [order for order in _history(complete) if order.get("id")]
    gone = {order["id"] for order in listed if str(order.get("status") or "") in crisp.CANCELLED_STATUSES}
    orders = list({order["id"]: order for order in listed if is_stored(order.get("status"))}.values())
    new = sum(1 for order in orders if order["id"] not in known)
    wanted = [order for order in orders if order["id"] not in complete][:ORDER_DETAILS_PER_SYNC]

    details: dict[str, dict[str, Any]] = {}
    detail_error: str | None = None
    failures = 0
    for order in wanted:
        try:
            details[order["id"]] = crisp.get_order(order["id"])
        except crisp.CrispAuthError:
            raise
        except Exception as exc:  # noqa: BLE001 - de kop bewaren we toch; de producten volgen bij de volgende synchronisatie
            detail_error = type(exc).__name__
            failures += 1
            if not details and failures >= MAX_FIRST_FAILURES:
                break  # er lukt niets: niet nog 20 keer proberen

    async with _aconnect() as conn:
        for order_id in gone:
            await conn.execute("DELETE FROM crisp_order_items WHERE order_id = ?", (order_id,))
            await conn.execute("DELETE FROM crisp_orders WHERE order_id = ?", (order_id,))
        for order in orders:
            info = details.get(order["id"])
            header = {**order, **({key: value for key, value in info.items() if key != "items" and value} if info else {}), "id": order["id"]}
            await _save_order(conn, header, info["items"] if info else None)
        await conn.execute(
            "INSERT INTO crisp_orders_meta (key, synced_at) VALUES ('orders', ?) ON CONFLICT(key) DO UPDATE SET synced_at = excluded.synced_at",
            (_now(),),
        )
        counted = await _fetchone(conn, "SELECT COUNT(*) FROM crisp_orders WHERE items_synced = 0")
        await conn.commit()
    remaining: int = counted[0] if counted else 0
    return {"orders": len(orders), "new": new, "remaining": remaining, **({"detailError": detail_error} if detail_error else {})}


def _order_json(row: Any, count: int | None = None, quantity: int | None = None) -> dict[str, Any]:
    return {
        "id": row["order_id"], "status": row["status"], "total": row["total"], "deliveryDate": row["delivery_date"],
        "windowStart": row["slot_start"], "windowEnd": row["slot_end"],
        "itemsSynced": bool(row["items_synced"]), "syncedAt": row["synced_at"],
        **({"lineCount": count} if count is not None else {}),
        **({"itemCount": quantity} if quantity is not None else {}),
    }


async def list_orders() -> dict[str, Any]:
    async with _aconnect() as conn:
        rows = await conn.execute_fetchall(
            """
            SELECT o.*, (SELECT COUNT(*) FROM crisp_order_items i WHERE i.order_id = o.order_id) AS line_count,
                   (SELECT COALESCE(SUM(i.quantity), 0) FROM crisp_order_items i WHERE i.order_id = o.order_id) AS item_count
            FROM crisp_orders o
            ORDER BY COALESCE(NULLIF(o.delivery_date, ''), o.first_seen) DESC, o.order_id DESC
            """
        )
    return {"syncedAt": await synced_at(), "orders": [_order_json(row, row["line_count"], row["item_count"]) for row in rows]}


async def get_order(order_id: Any) -> dict[str, Any] | None:
    async with _aconnect() as conn:
        row = await _fetchone(conn, "SELECT * FROM crisp_orders WHERE order_id = ?", (str(order_id),))
        if not row:
            return None
        items = await conn.execute_fetchall("SELECT * FROM crisp_order_items WHERE order_id = ? ORDER BY position", (str(order_id),))
    return {**_order_json(row, len(items), sum(item["quantity"] or 0 for item in items)), "items": [_item_json(item) for item in items]}


async def search_purchases(query: Any, limit: int = 50) -> dict[str, Any]:
    """Wanneer kocht je een product? Zoekt in de producten van je bestellingen (alle woorden moeten erin staan)."""
    words = _fold(query).split()
    if not words:
        return {"query": "", "products": [], "occurrences": [], "total": 0}
    occurrences: list[dict[str, Any]] = []
    async with _aconnect() as conn:
        for row in await conn.execute_fetchall(
            """
            SELECT i.title, i.quantity, o.order_id, o.delivery_date, o.first_seen
            FROM crisp_order_items i JOIN crisp_orders o ON o.order_id = i.order_id
            """
        ):
            if all(word in _fold(row["title"]) for word in words):
                occurrences.append({"product": row["title"], "quantity": row["quantity"],
                                    "date": (row["delivery_date"] or row["first_seen"] or "")[:10], "id": str(row["order_id"])})
        counted = await _fetchone(conn, "SELECT COUNT(*) FROM crisp_orders WHERE items_synced = 0")
    unsearched: int = counted[0] if counted else 0
    occurrences.sort(key=lambda entry: entry["date"], reverse=True)
    grouped: dict[str, dict[str, Any]] = {}
    for entry in occurrences:
        group = grouped.setdefault(_fold(entry["product"]), {"product": entry["product"], "lastDate": entry["date"], "times": 0, "totalQuantity": 0})
        group["times"] += 1
        group["totalQuantity"] += entry["quantity"] or 0
    return {
        "query": str(query).strip(),
        "products": sorted(grouped.values(), key=lambda group: group["lastDate"], reverse=True),
        "occurrences": occurrences[: max(1, int(limit))],
        "total": len(occurrences),
        "unsearched": unsearched,
    }
