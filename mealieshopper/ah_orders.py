"""Lokale kopie van AH-bestellingen, het bestelmandje en kassabonnen.

Alles wat bij AH wordt opgehaald, staat daarna in SQLite. Lezen gaat altijd uit die kopie en vraagt
nooit iets aan AH, dus de bestelhistorie is ook te bekijken (en door de MCP-server te bevragen) als AH
niet bereikbaar is. Alleen een synchronisatie praat met AH.

- Het **bestelmandje** (de actieve bestelling) is een momentopname: elke synchronisatie vervangt hem.
- Een **open bestelling** (geplaatst, nog niet geleverd) wordt bijgewerkt zolang hij open is. Verdwijnt hij
  uit de open bestellingen, dan halen we hem een laatste keer op en bewaren we hem als afgeleverd.
- Een **afgeleverde bestelling** verandert niet meer en wordt nooit verwijderd, ook niet als AH hem later
  niet meer toont.
- **Kassabonnen** (aankopen in de winkel) worden aangevuld: alleen nieuwe bonnen worden opgehaald.
Het afleveradres bewaren we bewust niet; alleen datum en tijdvak.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import time
import weakref
from collections import deque
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, TypeVar

import aiosqlite

from . import ah, ah_lists, audit, auth, image_cache, price_import, product_links, purchase_lines, tasks

log = logging.getLogger(__name__)

RECEIPT_PAGE = 100
MAX_RECEIPT_PAGES = 30  # 3.000 bonnen; voorkomt een eindeloze lus bij een afwijkend antwoord
ORDER_DETAILS_PER_SYNC = 25  # idem voor de producten van afgeleverde bestellingen
MAX_CLOSED_PAGES = 30  # pagina's van 10 afgeronde bestellingen per synchronisatie
RECEIPT_DETAILS_PER_SYNC = 40  # een synchronisatie blijft kort; de rest volgt bij de volgende

SCHEMA = """
CREATE TABLE IF NOT EXISTS ah_basket (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  order_id INTEGER,
  state TEXT,
  shopping_type TEXT,
  total REAL,
  discount REAL,
  delivery_date TEXT,
  slot_start TEXT,
  slot_end TEXT,
  synced_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ah_basket_items (
  position INTEGER PRIMARY KEY,
  product_id INTEGER, title TEXT, brand TEXT, unit_size TEXT, quantity INTEGER,
  amount REAL, price REAL, price_before_bonus REAL, bonus INTEGER, taxonomy TEXT, image TEXT
);

CREATE TABLE IF NOT EXISTS ah_orders (
  order_id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,
  state TEXT,
  state_text TEXT,
  shopping_type TEXT,
  method TEXT,
  total REAL,
  delivery_date TEXT,
  slot_start TEXT,
  slot_end TEXT,
  items_synced INTEGER NOT NULL DEFAULT 0,
  first_seen TEXT NOT NULL,
  synced_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ah_order_items (
  order_id INTEGER NOT NULL,
  position INTEGER NOT NULL,
  product_id INTEGER, title TEXT, brand TEXT, unit_size TEXT, quantity INTEGER,
  amount REAL, price REAL, price_before_bonus REAL, bonus INTEGER, taxonomy TEXT, image TEXT,
  PRIMARY KEY (order_id, position)
);

CREATE TABLE IF NOT EXISTS ah_receipts (
  receipt_id TEXT PRIMARY KEY,
  date_time TEXT,
  total REAL,
  details_synced INTEGER NOT NULL DEFAULT 0,
  discounts TEXT,
  payments TEXT,
  first_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ah_receipt_items (
  receipt_id TEXT NOT NULL,
  position INTEGER NOT NULL,
  product_id INTEGER, name TEXT, quantity INTEGER, price REAL, amount REAL,
  PRIMARY KEY (receipt_id, position)
);

-- Het id op een kassabon is AH's artikelnummer (`hqId`), niet het id van de webwinkel. Deze tabel koppelt ze, zodat "BLOEMKOOL" op de
-- bon bij "AH Bloemkool" uit de bestellingen hoort (issue #178). `product_id` NULL: AH vond het artikelnummer niet (opnieuw na een tijd).
-- `image`: de foto van het webwinkelproduct (NULL: nog niet opgezocht, "": geen foto), zodat een bon ook foto's kan tonen.
CREATE TABLE IF NOT EXISTS ah_receipt_products (
  receipt_product_id INTEGER PRIMARY KEY,
  product_id INTEGER,
  title TEXT,
  checked_at TEXT NOT NULL,
  image TEXT
);

CREATE TABLE IF NOT EXISTS ah_orders_meta (
  key TEXT PRIMARY KEY,
  synced_at TEXT NOT NULL
);
"""

ITEM_COLUMNS = ("product_id", "title", "brand", "unit_size", "quantity", "amount", "price", "price_before_bonus", "bonus", "taxonomy", "image")

# De gewone prijs van een aankoopregel: de laagste van de 30 dagen voor de aankoopdag (`prices.fill_regular_prices` vult ze 's nachts).
# `regular_price` NULL met een `regular_checked_at`: bepaald, maar er was geen gewone prijs bekend. Ook op `jumbo_order_items`.
REGULAR_COLUMNS: tuple[tuple[str, str], ...] = (("regular_price", "REAL"), ("regular_checked_at", "TEXT"))


def _connect() -> sqlite3.Connection:
    """Een sqlite3-verbinding met het schema, voor `backup.ensure_schemas`. De rest gebruikt `_aconnect`."""
    auth.ensure_db()
    conn = auth.connect()
    conn.executescript(SCHEMA)
    # Een database van vóór de bonfoto's heeft die kolom nog niet; `backfill_receipt_images` vult hem 's nachts.
    _ensure_columns(conn, "ah_receipt_products", (("image", "TEXT"),))
    for table in ("ah_order_items", "ah_receipt_items"):
        _ensure_columns(conn, table, REGULAR_COLUMNS)
    return product_links.ensure(conn)  # je eigen koppelingen (#238) gaan voor `ah_receipt_products`


@asynccontextmanager
async def _aconnect() -> AsyncIterator[aiosqlite.Connection]:
    """Async tweeling van `_connect`: hetzelfde schema en dezelfde migraties, plus de tabel en de SQL-functie `ms_fold` van
    `product_links` (`product_links.aensure`). Sluit zonder commit, zoals `auth.aconnect`."""
    await auth.aensure_db()
    async with auth.aconnect() as conn:
        await conn.executescript(SCHEMA)
        await _aensure_columns(conn, "ah_receipt_products", (("image", "TEXT"),))
        for table in ("ah_order_items", "ah_receipt_items"):
            await _aensure_columns(conn, table, REGULAR_COLUMNS)
        yield await product_links.aensure(conn)


async def _fetchone(conn: aiosqlite.Connection, sql: str, params: tuple[Any, ...] = ()) -> aiosqlite.Row | None:
    async with conn.execute(sql, params) as cursor:
        return await cursor.fetchone()


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: tuple[tuple[str, str], ...]) -> None:
    """Voeg de kolommen toe die een oudere database nog niet heeft."""
    present: set[str] = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    for column, kind in columns:
        if column not in present:
            _add_column(conn, table, column, kind)


def _add_column(conn: sqlite3.Connection, table: str, column: str, kind: str) -> None:
    """De migratie: twee verbindingen die hem tegelijk zien ontbreken mogen niet struikelen over elkaars ALTER TABLE."""
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
    except sqlite3.OperationalError as exc:
        if "duplicate column" not in str(exc):
            raise


async def _aensure_columns(conn: aiosqlite.Connection, table: str, columns: tuple[tuple[str, str], ...]) -> None:
    """Async tweeling van `_ensure_columns`, voor `_aconnect` hier, in `jumbo_orders` en in `prices`."""
    present: set[str] = {row["name"] for row in await conn.execute_fetchall(f"PRAGMA table_info({table})")}
    for column, kind in columns:
        if column in present:
            continue
        try:
            await conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
        except sqlite3.OperationalError as exc:  # een andere verbinding was ons net voor
            if "duplicate column" not in str(exc):
                raise


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _item_values(item: dict[str, Any]) -> tuple:
    return (
        item.get("productId"), item.get("title"), item.get("brand"), item.get("unitSize"), item.get("quantity"),
        item.get("amount"), item.get("price"), item.get("priceBeforeBonus"), 1 if item.get("bonus") else 0,
        item.get("taxonomy"), item.get("image"),
    )


def _line_amount(row: Any) -> float | None:
    """Het bedrag van een regel: aantal x prijs. (Versie 2.4.0 bewaarde hier het aantal van AH; dit herstelt dat bij het lezen.)"""
    if row["price"] is not None and row["quantity"] is not None:
        return round(row["quantity"] * row["price"], 2)
    return row["amount"]


def _item_json(row: Any) -> dict[str, Any]:
    return {
        "productId": row["product_id"], "title": row["title"], "brand": row["brand"], "unitSize": row["unit_size"],
        "quantity": row["quantity"], "amount": _line_amount(row), "price": row["price"],
        "priceBeforeBonus": row["price_before_bonus"], "bonus": bool(row["bonus"]),
        "taxonomy": row["taxonomy"], "image": row["image"],
    }


async def _stamp(conn: aiosqlite.Connection, key: str) -> None:
    await conn.execute(
        "INSERT INTO ah_orders_meta (key, synced_at) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET synced_at = excluded.synced_at",
        (key, _now()),
    )


async def synced_at(key: str) -> str | None:
    async with _aconnect() as conn:
        row = await _fetchone(conn, "SELECT synced_at FROM ah_orders_meta WHERE key = ?", (key,))
    return row["synced_at"] if row else None


# --- bestelmandje ---------------------------------------------------------------------------------

async def save_basket(basket: dict[str, Any]) -> None:
    async with _aconnect() as conn:
        await conn.execute("DELETE FROM ah_basket")
        await conn.execute("DELETE FROM ah_basket_items")
        await conn.execute(
            """
            INSERT INTO ah_basket (id, order_id, state, shopping_type, total, discount, delivery_date, slot_start, slot_end, synced_at)
            VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (basket.get("id"), basket.get("state"), basket.get("shoppingType"), basket.get("total"), basket.get("discount"),
             basket.get("deliveryDate"), basket.get("slotStart"), basket.get("slotEnd"), _now()),
        )
        await conn.executemany(
            f"INSERT INTO ah_basket_items (position, {', '.join(ITEM_COLUMNS)}) VALUES (?{', ?' * len(ITEM_COLUMNS)})",
            [(position, *_item_values(item)) for position, item in enumerate(basket.get("items") or [])],
        )
        await conn.commit()


async def get_basket() -> dict[str, Any]:
    """Het laatst opgehaalde bestelmandje. `syncedAt` is None als er nog nooit is gesynchroniseerd."""
    async with _aconnect() as conn:
        head = await _fetchone(conn, "SELECT * FROM ah_basket WHERE id = 1")
        rows = await conn.execute_fetchall("SELECT * FROM ah_basket_items ORDER BY position")
    if not head:
        return {"syncedAt": None, "items": [], "itemCount": 0, "units": 0}
    items = [_item_json(row) for row in rows]
    return {
        "syncedAt": head["synced_at"], "orderId": head["order_id"], "state": head["state"], "total": head["total"],
        "discount": head["discount"], "deliveryDate": head["delivery_date"], "slotStart": head["slot_start"],
        "slotEnd": head["slot_end"], "items": items, "itemCount": len(items),
        "units": sum(item["quantity"] or 0 for item in items),
    }


async def sync_basket() -> dict[str, Any]:
    """Bewaar het AH-mandje: de boodschappenlijst waar het weekmenu in schrijft (niet de actieve bestelling)."""
    await save_basket(await ah.get_shopping_list())
    return await get_basket()


# --- bestellingen ---------------------------------------------------------------------------------

async def _save_order(conn: aiosqlite.Connection, header: dict[str, Any], kind: str, items: list[dict[str, Any]] | None) -> None:
    """Bewaar of werk een bestelling bij. `items=None` laat de bewaarde producten staan. Wie aanroept, commit."""
    now = _now()
    existing = await _fetchone(conn, "SELECT first_seen, items_synced FROM ah_orders WHERE order_id = ?", (header["id"],))
    await conn.execute(
        """
        INSERT INTO ah_orders (order_id, kind, state, state_text, shopping_type, method, total, delivery_date, slot_start, slot_end, items_synced, first_seen, synced_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(order_id) DO UPDATE SET kind = excluded.kind, state = excluded.state,
          state_text = COALESCE(NULLIF(excluded.state_text, ''), ah_orders.state_text),
          shopping_type = COALESCE(NULLIF(excluded.shopping_type, ''), ah_orders.shopping_type),
          method = COALESCE(NULLIF(excluded.method, ''), ah_orders.method),
          total = COALESCE(excluded.total, ah_orders.total),
          delivery_date = COALESCE(NULLIF(excluded.delivery_date, ''), ah_orders.delivery_date),
          slot_start = COALESCE(NULLIF(excluded.slot_start, ''), ah_orders.slot_start),
          slot_end = COALESCE(NULLIF(excluded.slot_end, ''), ah_orders.slot_end),
          items_synced = excluded.items_synced, synced_at = excluded.synced_at
        """,
        (header["id"], kind, header.get("state"), header.get("stateText"), header.get("shoppingType"), header.get("method"),
         header.get("total"), header.get("deliveryDate"), header.get("slotStart"), header.get("slotEnd"),
         1 if items is not None else (existing["items_synced"] if existing else 0),
         existing["first_seen"] if existing else now, now),
    )
    if items is not None:
        await conn.execute("DELETE FROM ah_order_items WHERE order_id = ?", (header["id"],))
        await conn.executemany(
            f"INSERT INTO ah_order_items (order_id, position, {', '.join(ITEM_COLUMNS)}) VALUES (?, ?{', ?' * len(ITEM_COLUMNS)})",
            [(header["id"], position, *_item_values(item)) for position, item in enumerate(items)],
        )


async def _details_or_none(order_id: int) -> dict[str, Any] | None:
    try:
        return await ah.get_order_details(order_id)
    except Exception:  # noqa: BLE001 - de kop van de bestelling bewaren we toch; de producten volgen bij de volgende sync
        return None


async def _forget_order(conn: aiosqlite.Connection, order_id: int) -> None:
    await conn.execute("DELETE FROM ah_order_items WHERE order_id = ?", (order_id,))
    await conn.execute("DELETE FROM ah_orders WHERE order_id = ?", (order_id,))


async def _closed_orders(complete: set[int]) -> list[dict[str, Any]]:
    """Alle afgeronde bestellingen, nieuwste eerst, bladerend met `offset` (AH geeft er per keer 10 en telt de offset in rijen).

    Het bladeren stopt als een pagina niets nieuws geeft (einde, of AH negeert de offset) en zodra alles op een pagina al is
    bewaard of geannuleerd: oudere bestellingen zijn dan ook al binnen. Een mislukte pagina houdt het gevondene.
    """
    found: list[dict[str, Any]] = []
    seen: set[int] = set()
    offset = 0
    for _ in range(MAX_CLOSED_PAGES):
        try:
            page = await ah.get_closed_fulfillments(offset)
        except Exception:  # noqa: BLE001 - wat al binnen is, blijft; de rest volgt bij de volgende synchronisatie
            break
        new = [order for order in page if order["id"] not in seen]
        if not new:
            break
        found.extend(new)
        seen.update(order["id"] for order in new)
        if all(order["id"] in complete or order.get("deliveryStatus") == "CANCELLED" for order in new):
            break
        offset += len(page)
    return found


async def sync_orders() -> dict[str, Any]:
    """Haal de open en de afgeronde bestellingen op; wat is afgeleverd bewaren we voor altijd.

    Afgeronde bestellingen komen er pagina voor pagina (nieuwste eerst) uit; een volgende synchronisatie stopt zodra ze
    bij bestellingen komt die al bewaard zijn. Geannuleerde bestellingen bewaren we niet (ze komen ook bij "open" voorbij).
    """
    listed_open = await ah.get_open_fulfillments()
    async with _aconnect() as conn:
        previously_open = [row["order_id"] for row in await conn.execute_fetchall("SELECT order_id FROM ah_orders WHERE kind = 'open'")]
        complete = {row["order_id"] for row in await conn.execute_fetchall("SELECT order_id FROM ah_orders WHERE kind = 'delivered' AND items_synced = 1")}
        incomplete = [row["order_id"] for row in await conn.execute_fetchall("SELECT order_id FROM ah_orders WHERE kind = 'delivered' AND items_synced = 0")]
    closed_orders = await _closed_orders(complete)
    cancelled = {order["id"] for order in listed_open + closed_orders if order.get("deliveryStatus") == "CANCELLED"}
    open_orders = [order for order in listed_open if order["id"] not in cancelled]
    open_ids = {order["id"] for order in open_orders}
    delivered = [order for order in closed_orders if order["id"] not in cancelled and order["id"] not in open_ids]

    fetched = {order["id"]: await _details_or_none(order["id"]) for order in open_orders}
    # Wat open was en het nu niet meer is, is afgeleverd (of geannuleerd): een laatste keer ophalen.
    gone = [order_id for order_id in previously_open if order_id not in open_ids and order_id not in cancelled]
    listed_ids = {order["id"] for order in delivered}
    todo = [order for order in delivered if order["id"] not in complete]
    wanted = [order["id"] for order in todo] + [order_id for order_id in gone + incomplete if order_id not in listed_ids]
    details = {order_id: await _details_or_none(order_id) for order_id in list(dict.fromkeys(wanted))[:ORDER_DETAILS_PER_SYNC]}

    archived = 0
    async with _aconnect() as conn:
        for order in open_orders:
            info = fetched[order["id"]]
            header = {**order, **({"deliveryDate": info["deliveryDate"] or order["deliveryDate"]} if info else {})}
            await _save_order(conn, header, "open", info["items"] if info else None)
        for order_id in cancelled:
            await _forget_order(conn, order_id)
        by_id = {order["id"]: order for order in delivered}
        for order_id, info in details.items():
            if info is None:
                continue  # niet gelukt: we proberen het bij de volgende synchronisatie opnieuw
            header = {**by_id.get(order_id, {}), **{key: value for key, value in info.items() if key != "items" and value}, "id": order_id}
            known = order_id in complete or order_id in incomplete
            await _save_order(conn, header, "delivered", info["items"])
            archived += 0 if known else 1
        # Afgeleverd maar de producten nog niet binnen (meer dan er in een keer passen): kop alvast bewaren
        for order in todo:
            if order["id"] not in details:
                await _save_order(conn, order, "delivered", None)
                archived += 1
        await _stamp(conn, "orders")
        remaining = (await _fetchone(conn, "SELECT COUNT(*) FROM ah_orders WHERE kind = 'delivered' AND items_synced = 0"))[0]
        unfinished = sum(1 for order_id in gone if details.get(order_id) is None)
        await conn.commit()
    await image_cache.prefetch_async(limit=image_cache.PREFETCH_AFTER_SYNC)  # de foto's van de nieuwe bestellingen alvast in de cache
    return {"open": len(open_orders), "archived": archived, "remaining": remaining, "unfinished": unfinished}


async def add_order_by_id(order_id: int) -> dict[str, Any]:
    """Haal één bestelling op uit AH (elke bestelling-id) en bewaar hem als afgeleverd."""
    details = await ah.get_order_details(order_id)
    async with _aconnect() as conn:
        await _save_order(conn, {**details, "id": order_id}, "delivered", details["items"])
        await conn.commit()
    return await get_order(order_id) or {}


def _order_json(row: Any, count: int | None = None) -> dict[str, Any]:
    return {
        "id": row["order_id"], "kind": row["kind"], "state": row["state"], "stateText": row["state_text"],
        "shoppingType": row["shopping_type"], "method": row["method"], "total": row["total"],
        "deliveryDate": row["delivery_date"], "slotStart": row["slot_start"], "slotEnd": row["slot_end"],
        "itemsSynced": bool(row["items_synced"]), "syncedAt": row["synced_at"],
        **({"itemCount": count} if count is not None else {}),
    }


SUMMARY_IMAGES = 6  # zoveel foto's per aankoop in de lijst (de kaarten en het overzicht)
SUMMARY_NAMES = 4

# De samenvatting per bestelling voor de kaarten en het overzicht, in dezelfde query: de eerste foto's en namen in de volgorde van
# de bestelling (GROUP_CONCAT over een gesorteerde subquery, een regel per adres), het bespaarde bedrag en het aantal bonusregels.
# Het bespaarde bedrag is dat tegen de gewone prijs van 30 dagen (`regular_price`), en zonder die prijs tegen de prijs ervoor van AH.
_ORDER_SUMMARY_SQL = f"""
    (SELECT GROUP_CONCAT(image, char(10)) FROM (SELECT image FROM ah_order_items i WHERE i.order_id = o.order_id AND image != ''
        ORDER BY position LIMIT {SUMMARY_IMAGES})) AS summary_images,
    (SELECT GROUP_CONCAT(title, char(10)) FROM (SELECT title FROM ah_order_items i WHERE i.order_id = o.order_id AND title != ''
        ORDER BY position LIMIT {SUMMARY_NAMES})) AS summary_names,
    (SELECT SUM(MAX(COALESCE(regular_price, CASE WHEN bonus THEN price_before_bonus END, price) - price, 0) * COALESCE(quantity, 1))
        FROM ah_order_items i WHERE i.order_id = o.order_id) AS summary_savings,
    (SELECT SUM(bonus) FROM ah_order_items i WHERE i.order_id = o.order_id) AS summary_bonus
"""


def _order_summary(row: Any) -> dict[str, Any]:
    savings = row["summary_savings"]
    return {
        "images": price_import.split_lines(row["summary_images"]), "productNames": price_import.split_lines(row["summary_names"]),
        "savings": round(float(savings), 2) if savings is not None else None, "bonusCount": int(row["summary_bonus"] or 0),
        **_status_fields("order", row),
    }


def _status_fields(kind: str, row: Any) -> dict[str, Any]:
    status_kind, status_text = purchase_lines.status_kind(kind, row)
    return {"statusKind": status_kind, "statusText": status_text}


async def list_orders(summary: bool = False) -> dict[str, Any]:
    """Alle bestellingen, open bovenaan. Met `summary` (de lijstroute) per bestelling ook `images`, `productNames`, `savings` en
    `bonusCount`, `statusKind` en `statusText` voor de kaarten en het overzicht; de MCP-tools vragen dat niet."""
    async with _aconnect() as conn:
        rows = await conn.execute_fetchall(
            f"""
            SELECT o.*, (SELECT COUNT(*) FROM ah_order_items i WHERE i.order_id = o.order_id) AS item_count
            {"," + _ORDER_SUMMARY_SQL if summary else ""}
            FROM ah_orders o
            ORDER BY (o.kind = 'open') DESC, COALESCE(NULLIF(o.delivery_date, ''), o.first_seen) DESC, o.order_id DESC
            """
        )
    return {
        "syncedAt": await synced_at("orders"),
        "orders": [{**_order_json(row, row["item_count"]), **(_order_summary(row) if summary else {})} for row in rows],
    }


def _order_day(row: Any) -> str:
    """De aankoopdag van een bestelling: de bezorgdag, anders de dag waarop de app hem voor het eerst zag (zoals in `prices`)."""
    return str(row["delivery_date"] or row["first_seen"] or "")[:10]


async def get_order(order_id: int) -> dict[str, Any] | None:
    """Een bestelling met haar producten. Elke regel krijgt de velden van `purchase_lines.enrich` (categorie, korting tegen de gewone
    prijs van 30 dagen, eerder gekocht), de bestelling `saved`, `savedPercent`, `statusKind` en `statusText`. De gewone prijs van de
    regels wordt eerst bijgewerkt (`purchase_lines.fill_regular`)."""
    async with _aconnect() as conn:
        row = await _fetchone(conn, "SELECT * FROM ah_orders WHERE order_id = ?", (int(order_id),))
    if not row:
        return None
    await purchase_lines.fill_regular("ah", order_ids=[int(order_id)])
    async with _aconnect() as conn:
        rows = await conn.execute_fetchall("SELECT * FROM ah_order_items WHERE order_id = ? ORDER BY position", (int(order_id),))
        items = [{**_item_json(item), "regularPrice": item["regular_price"]} for item in rows]
        totals = await purchase_lines.enrich(conn, "ah", "order", _order_day(row), items)
    kind, text = purchase_lines.status_kind("order", row)
    return {**_order_json(row, len(items)), "items": items, **totals, "statusKind": kind, "statusText": text}


# --- kassabonnen ----------------------------------------------------------------------------------

async def sync_receipts() -> dict[str, Any]:
    """Vul de kassabonnen aan: nieuwe bonnen ophalen, daarna van een aantal bonnen de regels."""
    listed: list[dict[str, Any]] = []
    for page in range(MAX_RECEIPT_PAGES):
        batch = await ah.get_receipts(page * RECEIPT_PAGE, RECEIPT_PAGE)
        listed.extend(batch)
        if len(batch) < RECEIPT_PAGE:
            break
    now = _now()
    async with _aconnect() as conn:
        known = {row["receipt_id"] for row in await conn.execute_fetchall("SELECT receipt_id FROM ah_receipts")}
        new = [receipt for receipt in listed if receipt["id"] not in known]
        await conn.executemany(
            "INSERT INTO ah_receipts (receipt_id, date_time, total, first_seen) VALUES (?, ?, ?, ?)",
            [(receipt["id"], receipt["dateTime"], receipt["total"], now) for receipt in new],
        )
        pending = [row["receipt_id"] for row in await conn.execute_fetchall(
            "SELECT receipt_id FROM ah_receipts WHERE details_synced = 0 ORDER BY date_time DESC LIMIT ?", (RECEIPT_DETAILS_PER_SYNC,))]
        await conn.commit()
    failed = 0
    for receipt_id in pending:
        try:
            detail = await ah.get_receipt(receipt_id)
        except Exception:  # noqa: BLE001 - deze bon proberen we bij de volgende synchronisatie opnieuw
            failed += 1
            continue
        async with _aconnect() as conn:
            await conn.execute(
                "UPDATE ah_receipts SET details_synced = 1, discounts = ?, payments = ? WHERE receipt_id = ?",
                (json.dumps(detail["discounts"]), json.dumps(detail["payments"]), receipt_id),
            )
            await conn.execute("DELETE FROM ah_receipt_items WHERE receipt_id = ?", (receipt_id,))
            await conn.executemany(
                "INSERT INTO ah_receipt_items (receipt_id, position, product_id, name, quantity, price, amount) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(receipt_id, position, line["productId"], line["name"], line["quantity"], line["price"], line["amount"])
                 for position, line in enumerate(detail["items"])],
            )
            await conn.commit()
    async with _aconnect() as conn:
        remaining = (await _fetchone(conn, "SELECT COUNT(*) FROM ah_receipts WHERE details_synced = 0"))[0]
        await _stamp(conn, "receipts")
        await conn.commit()
    try:
        products = await resolve_receipt_products(now)
    except Exception:  # noqa: BLE001 - het koppelen van producten mag het ophalen van de bonnen nooit laten mislukken
        products = {"found": 0, "missing": 0, "failed": 0, "searches": 0}
    await image_cache.prefetch_async(limit=image_cache.PREFETCH_AFTER_SYNC)  # de foto's van de gekoppelde producten alvast in de cache
    return {"new": len(new), "total": len(listed), "remaining": remaining, "failed": failed, "products": products}


# Hoeveel zoekopdrachten bij AH per keer: na het ophalen van de bonnen weinig (je wacht erop), in de nachtelijke taak wat meer.
# Tussen twee zoekopdrachten zit een pauze: AH beperkt het aantal verzoeken en de firewall blokkeert pieken. Geeft AH een 403 of 429,
# dan stopt het koppelen meteen en wacht het `RECEIPT_PRODUCTS_BACKOFF_HOURS` uur.
RECEIPT_PRODUCTS_SEARCHES = 20
RECEIPT_PRODUCTS_SEARCHES_NIGHTLY = 60
RECEIPT_PRODUCTS_PAUSE_SECONDS = 1.5
RECEIPT_PRODUCTS_DEADLINE_SECONDS = 45
RECEIPT_PRODUCTS_DEADLINE_NIGHTLY = 150
RECEIPT_PRODUCTS_RECHECK_DAYS = 30  # een artikelnummer dat AH niet vond, proberen we na zoveel dagen opnieuw
RECEIPT_PRODUCTS_BACKOFF_HOURS = 12
RECEIPT_PRODUCTS_PER_HOUR = 120  # alle zoekopdrachten samen (na een bon-sync, 's nachts en bij het zoeken in de Prijsgeschiedenis)
RECEIPT_PRODUCTS_LOOKUP_SEARCHES = 2  # bij een zoekopdracht in de Prijsgeschiedenis: de namen op de bon die erbij passen
RECEIPT_PRODUCTS_OPEN_SEARCHES = 5  # bij het openen van een bon: de regels die nog niet gekoppeld zijn (binnen de pauze van 10 s)
T = TypeVar("T")

SETTING_RECEIPT_PRODUCTS_PAUSED = "receipt_products_paused_until"
# Waarom het koppelen stilligt, naast de pauze: "<status van AH>@<tijdstip>", bijvoorbeeld "429@2026-11-05T14:02:00+00:00".
SETTING_RECEIPT_PRODUCTS_PAUSED_REASON = "receipt_products_paused_reason"
# De uitkomst van de laatste ronde (koppelen en foto's samen, zie `_record_round`), voor de melding op de bestelpagina.
SETTING_LAST_ROUND = "receipt_products_last_round"
# Bij een betere manier van zoeken (versie 2: per naam, met het langste woord als tweede poging) krijgt wat eerder niet gevonden
# werd een nieuwe kans, zonder 30 dagen te wachten.
RECEIPT_PRODUCTS_VERSION = "2"
SETTING_RECEIPT_PRODUCTS_VERSION = "receipt_products_version"

_UPSERT_RECEIPT_PRODUCT = """
INSERT INTO ah_receipt_products (receipt_product_id, product_id, title, checked_at, image) VALUES (?, ?, ?, ?, ?)
ON CONFLICT(receipt_product_id) DO UPDATE SET product_id = excluded.product_id, title = excluded.title, checked_at = excluded.checked_at,
  image = excluded.image
"""
RECEIPT_IMAGES_BATCH = 25  # zoveel webwinkel-id's per verzoek aan `get_products_by_ids`
RECEIPT_IMAGES_BATCHES = 20  # en zoveel verzoeken per nachtelijke ronde (500 producten)
RECEIPT_IMAGES_BATCHES_OPEN = 4  # en zoveel in de ronde op de achtergrond als de bonnenlijst opent (100 producten)
_BACKFILL_TASK: asyncio.Task[None] | None = None  # de ronde op de achtergrond (`receipt-images`), hooguit een tegelijk
_IMAGES_MISSING_FROM = "FROM ah_receipt_products WHERE image IS NULL AND product_id IS NOT NULL"
_MISSING_RECEIPT_PRODUCT = """
INSERT INTO ah_receipt_products (receipt_product_id, product_id, title, checked_at) VALUES (?, NULL, NULL, ?)
ON CONFLICT(receipt_product_id) DO UPDATE SET checked_at = excluded.checked_at WHERE product_id IS NULL
"""


class _HourlyBudgetSpent(Exception):
    """Het aantal zoekopdrachten van dit uur is op (`RECEIPT_PRODUCTS_PER_HOUR`)."""


# Een pauze en een uurbudget voor het hele proces: de nachtelijke taak, een bon-sync, het zoeken in de Prijsgeschiedenis en
# een ronde op afroep delen ze, zodat AH nooit meer dan een zoekopdracht per `RECEIPT_PRODUCTS_PAUSE_SECONDS` van ons krijgt.
# Het slot `_lock("pace")` zet de verzoeken achter elkaar (ook tijdens de pauze en het verzoek zelf). `_PACE["recent"]` (de monotone
# tijdstippen van de verzoeken van het afgelopen uur) heeft geen slot nodig: niemand wacht terwijl hij het bijwerkt. Zo leest
# `linking_status` het uurbudget meteen, ook als een ronde net op zijn beurt wacht.
_PACE = {"last": float("-inf"), "recent": deque()}

# De sloten van deze module, per event loop: een `asyncio.Lock` hoort bij de loop waarin hij voor het eerst wacht. In de app is
# er een loop per proces, de tests draaien elk een eigen loop.
_LOCKS: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, asyncio.Lock]] = weakref.WeakKeyDictionary()


def _lock(name: str) -> asyncio.Lock:
    """Het slot `name` van de lopende event loop: `pace` (een verzoek aan AH tegelijk), `round` (de laatste ronde bijwerken) of
    `linking` (een ronde op afroep starten)."""
    loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
    locks: dict[str, asyncio.Lock] | None = _LOCKS.get(loop)
    if locks is None:
        locks = _LOCKS[loop] = {}
    lock: asyncio.Lock | None = locks.get(name)
    if lock is None:
        lock = locks[name] = asyncio.Lock()
    return lock


def _prune_pace(now: float) -> deque:
    """De verzoeken van langer dan een uur geleden vallen uit het uurbudget."""
    recent = _PACE["recent"]
    while recent and now - recent[0] > 3600:
        recent.popleft()
    return recent


async def _wait(seconds: float) -> None:
    """De pauze tussen twee verzoeken aan AH. Een eigen functie, zodat een test de pauze kan vervangen zonder `asyncio.sleep`
    voor het hele proces te vervangen."""
    await asyncio.sleep(seconds)


async def _polite(call: Callable[[], Awaitable[T]]) -> T:
    """Een verzoek aan AH binnen het tempo en het uurbudget van het koppelen (de zoekopdrachten en het ophalen van foto's samen).

    `call` geeft de coroutine van het verzoek. Het slot blijft vast tot het antwoord er is, zodat er een verzoek tegelijk loopt."""
    async with _lock("pace"):
        if len(_prune_pace(time.monotonic())) >= RECEIPT_PRODUCTS_PER_HOUR:
            raise _HourlyBudgetSpent()
        wait = _PACE["last"] + RECEIPT_PRODUCTS_PAUSE_SECONDS - time.monotonic()
        if wait > 0:
            await _wait(wait)
        _PACE["last"] = time.monotonic()
        _PACE["recent"].append(_PACE["last"])
        return await call()


async def _polite_search(query: str) -> list[tuple[int, int, str, str]]:
    return await _polite(lambda: ah.search_hq_ids(query))


def _receipt_queries(name: str) -> list[str]:
    """De zoekopdrachten voor een naam op de bon: de naam zelf en, als die uit meer woorden bestaat, het langste woord. Een bon
    kort af ("RB PICOLIENT" is Red Band Picolient); "PICOLIENT" alleen vindt hem wel."""
    text = " ".join(str(name).split())
    words = re.findall(r"[^\W\d_]{4,}", text)
    longest = max(words, key=len) if words else ""
    return [text] + ([longest] if longest and longest.casefold() != text.casefold() else [])


# Een bonregel `l` (met `ah_receipt_products p` erbij via een LEFT JOIN) zonder webwinkelproduct, en zo'n regel die aan de beurt is
# om opgezocht te worden: met een naam, nooit opgezocht of langer dan `RECEIPT_PRODUCTS_RECHECK_DAYS` geleden niet gevonden (de
# parameter is `_recheck_before`).
# Een bonregel zonder webwinkelproduct: geen automatische koppeling (`p`) en geen eigen koppeling op het artikelnummer (#238).
_UNLINKED_SQL = """l.product_id IS NOT NULL AND l.product_id != 0 AND p.product_id IS NULL
    AND NOT EXISTS (SELECT 1 FROM product_links k WHERE k.store = 'ah' AND k.kind = 'article' AND k.key = CAST(l.product_id AS TEXT))"""
_DUE_SQL = "COALESCE(l.name, '') != '' AND (p.receipt_product_id IS NULL OR p.checked_at < ?)"


def _recheck_before(now: str) -> str:
    """Wie vóór dit moment niet gevonden werd, mag opnieuw opgezocht worden."""
    return (datetime.fromisoformat(now) - timedelta(days=RECEIPT_PRODUCTS_RECHECK_DAYS)).isoformat(timespec="seconds")


async def _todo_by_name(conn: aiosqlite.Connection, now: str) -> list[dict[str, Any]]:
    """De artikelnummers die nog niet gekoppeld zijn en aan de beurt zijn, per naam op de bon (een naam als "REDBAND" hoort bij
    meer artikelnummers en kost dan een zoekopdracht), de vaakst gekochte naam eerst. Een artikelnummer dat je zelf koppelde (#238)
    zoeken we niet meer op: dat spaart zoekopdrachten bij AH."""
    rows = await conn.execute_fetchall(
        f"""
        SELECT l.product_id AS id, MAX(l.name) AS name, COUNT(*) AS bought FROM ah_receipt_items l
        LEFT JOIN ah_receipt_products p ON p.receipt_product_id = l.product_id
        WHERE {_UNLINKED_SQL} AND {_DUE_SQL}
        GROUP BY l.product_id
        """,
        (_recheck_before(now),),
    )
    groups: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = _name_key(row["name"])
        group = groups.setdefault(key, {"key": key, "name": " ".join(str(row["name"]).split()), "ids": [], "bought": 0})
        group["ids"].append(int(row["id"]))
        group["bought"] += row["bought"]
    return sorted(groups.values(), key=lambda group: (-group["bought"], group["name"]))


async def receipt_linking_todo(conn: aiosqlite.Connection, receipt_id: str, now: str) -> dict[str, int]:
    """De regels van een bon zonder webwinkelproduct (`unlinked`) en hoeveel daarvan een ronde nu zou opzoeken (`due`, dezelfde regel
    als `_todo_by_name`). Een regel die AH de laatste `RECEIPT_PRODUCTS_RECHECK_DAYS` dagen niet vond, telt wel bij `unlinked` en
    niet bij `due`: de knop op de bon zou er niets voor doen."""
    row = await _fetchone(
        conn,
        f"""
        SELECT COUNT(*), COALESCE(SUM(CASE WHEN {_DUE_SQL} THEN 1 ELSE 0 END), 0) FROM ah_receipt_items l
        LEFT JOIN ah_receipt_products p ON p.receipt_product_id = l.product_id
        WHERE l.receipt_id = ? AND {_UNLINKED_SQL}
        """,
        (_recheck_before(now), receipt_id),
    )
    return {"unlinked": int(row[0]), "due": int(row[1])}


async def _linked(conn: aiosqlite.Connection, ids: list[int]) -> set[int]:
    marks = ",".join("?" * len(ids))
    rows = await conn.execute_fetchall(
        f"SELECT receipt_product_id FROM ah_receipt_products WHERE product_id IS NOT NULL AND receipt_product_id IN ({marks})", ids
    )
    return {row[0] for row in rows}


def _name_key(name: Any) -> str:
    return "".join(str(name).casefold().split())


async def _pause(now: str, status: int) -> str:
    """Zet het koppelen `RECEIPT_PRODUCTS_BACKOFF_HOURS` uur stil na een 403 of 429 van AH, met de reden ernaast. Geeft het einde van de pauze."""
    until = (datetime.fromisoformat(now) + timedelta(hours=RECEIPT_PRODUCTS_BACKOFF_HOURS)).isoformat(timespec="seconds")
    await auth.aset_setting(SETTING_RECEIPT_PRODUCTS_PAUSED, until)
    await auth.aset_setting(SETTING_RECEIPT_PRODUCTS_PAUSED_REASON, f"{status}@{now}")
    return until


# Het koppelen en de foto's van een ronde kunnen in verschillende tasks tegelijk eindigen: `_lock("round")` houdt het lezen en
# schrijven van de laatste ronde bij elkaar.
_ROUND_COUNTS = ("searches", "found", "missing", "failed", "images")


async def _record_round(now: str, trigger: str, counts: dict[str, int]) -> None:
    """Schrijf de uitkomst van een ronde naar `SETTING_LAST_ROUND`. Het koppelen en de foto's van dezelfde ronde (dezelfde `now`
    en `trigger`) tellen bij elkaar op, een andere ronde begint opnieuw."""
    async with _lock("round"):
        try:
            last = json.loads(await auth.aget_setting(SETTING_LAST_ROUND, "") or "{}")
        except ValueError:
            last = {}
        if not isinstance(last, dict) or last.get("at") != now or last.get("trigger") != trigger:
            last = {"at": now, **{key: 0 for key in _ROUND_COUNTS}, "trigger": trigger}
        for key in _ROUND_COUNTS:
            last[key] = int(last.get(key) or 0) + int(counts.get(key) or 0)
        await auth.aset_setting(SETTING_LAST_ROUND, json.dumps(last))


async def resolve_receipt_products(now: str | None = None, searches: int = RECEIPT_PRODUCTS_SEARCHES,
                             deadline_seconds: float = RECEIPT_PRODUCTS_DEADLINE_SECONDS, only: set[str] | None = None,
                             trigger: str = "sync") -> dict[str, Any]:
    """Koppel de artikelnummers op de bonnen aan producten van de webwinkel.

    Per naam op de bon zoeken we bij AH en koppelen een artikelnummer alleen aan het resultaat met precies dat artikelnummer (`hqId`):
    geen gok op naam. Vindt de naam niets, dan nog een keer met het langste woord. Elk ander resultaat met een artikelnummer bewaren
    we ook, dat scheelt latere zoekopdrachten. Hooguit `searches` zoekopdrachten, met een pauze ertussen en hooguit `deadline_seconds`
    lang. Bij een 403 of 429 van AH stopt het en wacht het een paar uur (`SETTING_RECEIPT_PRODUCTS_PAUSED`, met de reden in
    `SETTING_RECEIPT_PRODUCTS_PAUSED_REASON`). De uitkomst komt in `SETTING_LAST_ROUND`, met `trigger` als aanleiding ("sync",
    "nightly", "lookup", "open", "receipt" of "manual"). Een ronde die door de pauze niets doet, laat de vorige staan."""
    now = now or _now()
    result: dict[str, Any] = {"found": 0, "missing": 0, "failed": 0, "searches": 0}
    paused = await auth.aget_setting(SETTING_RECEIPT_PRODUCTS_PAUSED, "")
    if paused and paused > now:
        return {**result, "pausedUntil": paused}
    result = await _resolve_groups(now, searches, deadline_seconds, only)
    await _record_round(now, trigger, result)
    return result


async def _resolve_groups(now: str, searches: int, deadline_seconds: float, only: set[str] | None) -> dict[str, Any]:
    """Het eigenlijke koppelen van `resolve_receipt_products`, buiten de pauze."""
    result: dict[str, Any] = {"found": 0, "missing": 0, "failed": 0, "searches": 0}
    if await auth.aget_setting(SETTING_RECEIPT_PRODUCTS_VERSION, "") != RECEIPT_PRODUCTS_VERSION:
        async with _aconnect() as conn:
            await conn.execute("DELETE FROM ah_receipt_products WHERE product_id IS NULL")
            await conn.commit()
        await auth.aset_setting(SETTING_RECEIPT_PRODUCTS_VERSION, RECEIPT_PRODUCTS_VERSION)
    async with _aconnect() as conn:
        groups = [group for group in await _todo_by_name(conn, now) if only is None or group["key"] in only]
    deadline = time.monotonic() + deadline_seconds
    for group in groups:
        async with _aconnect() as conn:
            linked = await _linked(conn, group["ids"])
        left = [product_id for product_id in group["ids"] if product_id not in linked]
        result["found"] += len(group["ids"]) - len(left)  # gevonden bij de zoekopdracht van een andere naam
        complete = True
        for query in _receipt_queries(group["name"]) if left else []:
            if result["searches"] >= searches or time.monotonic() > deadline:
                return result
            try:
                hits = await _polite_search(query)
            except _HourlyBudgetSpent:
                return {**result, "limited": True}
            except ah.AhHttpError as exc:
                result["searches"] += 1
                if exc.status in (403, 429):  # firewall of te veel verzoeken: niet aandringen
                    return {**result, "failed": result["failed"] + 1, "pausedUntil": await _pause(now, exc.status)}
                result["failed"] += 1
                complete = False
                break
            except Exception:  # noqa: BLE001 - de volgende keer opnieuw
                result["searches"] += 1
                result["failed"] += 1
                complete = False
                break
            result["searches"] += 1
            found = {hit[0] for hit in hits}
            async with _aconnect() as conn:
                await conn.executemany(_UPSERT_RECEIPT_PRODUCT, [(hq_id, webshop_id, title, now, image) for hq_id, webshop_id, title, image in hits])
                await conn.commit()
            result["found"] += sum(1 for product_id in left if product_id in found)
            left = [product_id for product_id in left if product_id not in found]
            if not left:
                break
        if left and complete:
            async with _aconnect() as conn:
                await conn.executemany(_MISSING_RECEIPT_PRODUCT, [(product_id, now) for product_id in left])
                await conn.commit()
            result["missing"] += len(left)
    return result


async def resolve_receipt_names(names: list[str], searches: int = RECEIPT_PRODUCTS_LOOKUP_SEARCHES, *, now: str | None = None,
                          deadline_seconds: float = 10, trigger: str = "lookup") -> dict[str, Any]:
    """Koppel de artikelnummers van deze namen op de bon meteen, bijvoorbeeld omdat je ze zoekt in de Prijsgeschiedenis ("REDBAND")
    of een bon opent (`get_receipt`).
    Hooguit `searches` zoekopdrachten, binnen dezelfde pauze en hetzelfde uurbudget. Een naam die net is opgezocht, slaat het over
    (die wacht `RECEIPT_PRODUCTS_RECHECK_DAYS`)."""
    keys = {_name_key(name) for name in names if str(name).strip()}
    if not keys:
        return {"found": 0, "missing": 0, "failed": 0, "searches": 0}
    return await resolve_receipt_products(now, searches=searches, deadline_seconds=deadline_seconds, only=keys, trigger=trigger)


async def backfill_receipt_images(now: str | None = None, batches: int = RECEIPT_IMAGES_BATCHES, only: set[int] | None = None,
                            trigger: str = "open") -> dict[str, Any]:
    """De foto bij koppelingen van vóór de bonfoto's (`image` NULL): per `RECEIPT_IMAGES_BATCH` webwinkel-id's een verzoek aan AH,
    hooguit `batches` verzoeken, binnen het tempo, het uurbudget en de pauze van het koppelen. Met `only` alleen die webwinkel-id's
    (de producten van een bon die opengaat). Een id dat AH niet teruggeeft krijgt "" (geen foto), zodat het niet elke nacht opnieuw
    gevraagd wordt. De uitkomst komt in `SETTING_LAST_ROUND` (zie `resolve_receipt_products`)."""
    now = now or _now()
    result: dict[str, Any] = {"products": 0, "requests": 0, "failed": 0}
    paused = await auth.aget_setting(SETTING_RECEIPT_PRODUCTS_PAUSED, "")
    if paused and paused > now:
        return {**result, "pausedUntil": paused}
    result = await _backfill_images(now, batches, only)
    await _record_round(now, trigger, {"failed": result["failed"], "images": result["products"]})
    return result


async def _backfill_images(now: str, batches: int, only: set[int] | None) -> dict[str, Any]:
    """Het eigenlijke ophalen van de foto's van `backfill_receipt_images`, buiten de pauze."""
    result: dict[str, Any] = {"products": 0, "requests": 0, "failed": 0}
    async with _aconnect() as conn:
        rows = await conn.execute_fetchall(
            "SELECT DISTINCT product_id FROM ah_receipt_products WHERE image IS NULL AND product_id IS NOT NULL ORDER BY checked_at DESC LIMIT ?",
            (RECEIPT_IMAGES_BATCH * batches if only is None else -1,),
        )
    todo = [int(row[0]) for row in rows if only is None or int(row[0]) in only][: RECEIPT_IMAGES_BATCH * batches]
    for start in range(0, len(todo), RECEIPT_IMAGES_BATCH):
        ids = todo[start : start + RECEIPT_IMAGES_BATCH]
        try:
            products = await _polite(lambda: ah.get_products_by_ids(ids))
        except _HourlyBudgetSpent:
            return {**result, "limited": True}
        except ah.AhHttpError as exc:
            result["requests"] += 1
            result["failed"] += 1
            if exc.status in (403, 429):
                return {**result, "pausedUntil": await _pause(now, exc.status)}
            return result
        except Exception:  # noqa: BLE001 - de volgende nacht opnieuw
            result["requests"] += 1
            result["failed"] += 1
            return result
        result["requests"] += 1
        images = {int(product["id"]): str(product.get("image") or "") for product in products if product.get("id") is not None}
        async with _aconnect() as conn:
            for product_id in ids:
                await conn.execute(
                    "UPDATE ah_receipt_products SET image = ?, title = COALESCE(NULLIF(title, ''), ?) WHERE product_id = ? AND image IS NULL",
                    (images.get(product_id, ""), next((p.get("title") for p in products if p.get("id") == product_id), None), product_id),
                )
            await conn.commit()
        result["products"] += sum(1 for product_id in ids if images.get(product_id))  # met een foto
    return result


async def _receipt_images_missing() -> bool:
    async with _aconnect() as conn:
        return await _fetchone(conn, f"SELECT 1 {_IMAGES_MISSING_FROM} LIMIT 1") is not None


def _backfill_running() -> bool:
    return _BACKFILL_TASK is not None and not _BACKFILL_TASK.done()


async def backfill_receipt_images_async() -> None:
    """Een kleine ronde als achtergrondtask (`receipt-images`) als de bonnenlijst opent en er nog foto's ontbreken (hooguit
    `RECEIPT_IMAGES_BATCHES_OPEN` verzoeken, binnen het tempo en het uurbudget): zo komen de foto's in de lijst er met elke keer
    kijken bij, in plaats van alleen 's nachts. Loopt er al een ronde, dan niet nog een. Een fout blijft in de task. Een task die
    bij het afsluiten wordt geannuleerd, is klaar: de volgende keer start er gewoon weer een."""
    global _BACKFILL_TASK
    if _backfill_running():
        return
    try:
        if not await _receipt_images_missing():
            return
    except Exception:  # noqa: BLE001 - een database van vóór de bonnen: niets te doen
        return
    if _backfill_running():  # een ander verzoek startte er een terwijl wij in de database keken
        return

    async def run() -> None:
        try:
            await backfill_receipt_images(batches=RECEIPT_IMAGES_BATCHES_OPEN, trigger="list")
        except Exception:  # noqa: BLE001
            log.exception("Bonfoto's: de ronde op de achtergrond mislukte")

    _BACKFILL_TASK = tasks.spawn("receipt-images", run())


async def backfill_receipt_images_nightly(now: str | None = None) -> dict[str, Any] | None:
    """Voor de nachtelijke taak: foto's bij oude koppelingen, als er iets te doen is. Een fout laat de taak doorgaan. `now` is
    hetzelfde tijdstip als bij `resolve_receipt_products_nightly`, zodat koppelen en foto's in een laatste ronde samenkomen."""
    try:
        if not await _receipt_images_missing():
            return None
        return await backfill_receipt_images(now, trigger="nightly")
    except Exception:  # noqa: BLE001 - de foto's zijn een extraatje
        return None


async def resolve_receipt_products_nightly(now: str | None = None) -> dict[str, Any] | None:
    """Voor de nachtelijke taak: wat meer artikelnummers koppelen, als er bonnen zijn. Een fout laat de taak doorgaan. `now` deelt
    het met `backfill_receipt_images_nightly` (zie `offers_schedule.run_all`)."""
    try:
        async with _aconnect() as conn:
            if await _fetchone(conn, "SELECT 1 FROM ah_receipt_items LIMIT 1") is None:
                return None
        return await resolve_receipt_products(
            now, searches=RECEIPT_PRODUCTS_SEARCHES_NIGHTLY, deadline_seconds=RECEIPT_PRODUCTS_DEADLINE_NIGHTLY, trigger="nightly"
        )
    except Exception:  # noqa: BLE001 - het koppelen is een extraatje bij de aanbiedingen
        return None


# --- de toestand van het koppelen en een ronde op afroep ------------------------------------------------------------------------
# Een ronde op afroep (de knop "Nu ophalen") loopt als achtergrondtask (`linking-run`), hooguit een tegelijk, binnen hetzelfde
# tempo en uurbudget als de rest (`_polite`). `_lock("linking")` zet het starten achter elkaar, ook het opheffen van de pauze
# ervoor. `_LINKING_TASK` is de task van de laatste ronde en `_LINKING_RECEIPT_ID` zijn bon (None voor alle bonnen). Een ronde
# loopt zolang die task niet klaar is: een task die bij het afsluiten wordt geannuleerd, staat dus nooit op "bezig".
_LINKING_TASK: asyncio.Task[None] | None = None
_LINKING_RECEIPT_ID: str | None = None


class LinkingBusy(Exception):
    """Er loopt al een ronde op afroep. `receipt_id` is de bon waarvoor de nieuwe ronde gevraagd werd."""

    def __init__(self, receipt_id: str | None) -> None:
        super().__init__(f"linking already running (requested for {receipt_id or 'all receipts'})")
        self.receipt_id: str | None = receipt_id


async def _pause_reason(now: str) -> tuple[str | None, str | None, int | None]:
    """De lopende pauze als (tot, sinds, status van AH), alle drie None als het koppelen niet stilligt."""
    until = await auth.aget_setting(SETTING_RECEIPT_PRODUCTS_PAUSED, "")
    if not until or until <= now:
        return None, None, None
    status, _, since = (await auth.aget_setting(SETTING_RECEIPT_PRODUCTS_PAUSED_REASON, "")).partition("@")
    return until, since or None, int(status) if status.isdigit() else None


def _budget(now: str) -> dict[str, Any]:
    """Het uurbudget: hoeveel verzoeken het afgelopen uur, en wanneer de oudste ervan weer vrijkomt (als ISO-tijdstip, omgerekend
    van de monotone klok van `_PACE` naar `now`)."""
    clock = time.monotonic()
    recent = _prune_pace(clock)
    used, oldest = len(recent), (recent[0] if recent else None)
    reset_at = None
    if oldest is not None:
        reset_at = (datetime.fromisoformat(now) + timedelta(seconds=oldest + 3600 - clock)).isoformat(timespec="seconds")
    return {"used": used, "limit": RECEIPT_PRODUCTS_PER_HOUR, "resetAt": reset_at}


async def _todo(now: str) -> dict[str, int]:
    """Wat er nog te koppelen is: artikelnummers zonder product (`_todo_by_name`), op hoeveel bonnen, en foto's die ontbreken."""
    async with _aconnect() as conn:
        ids = [product_id for group in await _todo_by_name(conn, now) for product_id in group["ids"]]
        receipts = 0
        if ids:
            marks = ",".join("?" * len(ids))
            receipts = (await _fetchone(conn, f"SELECT COUNT(DISTINCT receipt_id) FROM ah_receipt_items WHERE product_id IN ({marks})", tuple(ids)))[0]
        images = (await _fetchone(conn, f"SELECT COUNT(DISTINCT product_id) {_IMAGES_MISSING_FROM}"))[0]
    return {"lines": len(ids), "receipts": int(receipts), "images": int(images)}


def _next_round(now: str) -> str | None:
    """De volgende nachtelijke ronde (die van de aanbiedingen), of None als die uit staat."""
    from . import offers_schedule  # offers_schedule importeert deze module

    if not offers_schedule.enabled():
        return None
    local = datetime.fromisoformat(now).astimezone()
    return offers_schedule.next_slot(local).astimezone(timezone.utc).isoformat(timespec="seconds")


async def _last_round() -> dict[str, Any] | None:
    try:
        last = json.loads(await auth.aget_setting(SETTING_LAST_ROUND, "") or "null")
    except ValueError:
        return None
    return last if isinstance(last, dict) else None


def _linking_busy() -> bool:
    return _LINKING_TASK is not None and not _LINKING_TASK.done()


def _running() -> dict[str, Any] | None:
    return {"receiptId": _LINKING_RECEIPT_ID} if _linking_busy() else None


async def linking_status(now: str | None = None) -> dict[str, Any]:
    """De toestand van het koppelen voor de melding op de bestelpagina: de pauze na een 403 of 429 van AH, het uurbudget, wat er
    nog te doen is, de laatste en de volgende ronde, en of er nu een ronde op afroep loopt."""
    now = now or _now()
    paused_until, paused_since, paused_status = await _pause_reason(now)
    return {
        "pausedUntil": paused_until,
        "pausedSince": paused_since,
        "pausedStatus": paused_status,
        "budget": _budget(now),
        "todo": await _todo(now),
        "lastRound": await _last_round(),
        "nextRound": _next_round(now),
        "running": _running(),
    }


async def receipt_exists(receipt_id: str) -> bool:
    async with _aconnect() as conn:
        return await _fetchone(conn, "SELECT 1 FROM ah_receipts WHERE receipt_id = ?", (receipt_id,)) is not None


async def _receipt_round(receipt_id: str, now: str) -> dict[str, Any]:
    """Een ronde voor een bon: de namen op die bon die nog niet gekoppeld zijn, elk met al zijn zoekopdrachten (de naam en het
    langste woord), en daarna de foto's die nog ontbreken bij de webwinkelproducten van die bon (`_RECEIPT_IMAGES_MISSING_SQL`)."""
    async with _aconnect() as conn:
        on_receipt = {int(row[0]) for row in await conn.execute_fetchall(
            "SELECT DISTINCT product_id FROM ah_receipt_items WHERE receipt_id = ? AND product_id IS NOT NULL", (receipt_id,))}
        names = [group["name"] for group in await _todo_by_name(conn, now) if on_receipt.intersection(group["ids"])]
    searches = sum(len(_receipt_queries(name)) for name in names)
    products = await resolve_receipt_names(
        names, searches=searches, now=now, deadline_seconds=RECEIPT_PRODUCTS_DEADLINE_NIGHTLY, trigger=_on_demand_trigger(receipt_id)
    )
    async with _aconnect() as conn:
        missing = {int(row[0]) for row in await conn.execute_fetchall(_RECEIPT_IMAGES_MISSING_SQL, (receipt_id,))}
    images = (
        await backfill_receipt_images(now, batches=2, only=missing, trigger=_on_demand_trigger(receipt_id))
        if missing else {"products": 0, "requests": 0, "failed": 0}
    )
    return {"products": products, "images": images}


# De aanleiding van een ronde op afroep met alleen foto's in `SETTING_LAST_ROUND` (de knop "Foto's aanvullen").
IMAGES_TRIGGER = "images"


def _on_demand_trigger(receipt_id: str | None) -> str:
    """De aanleiding van een ronde op afroep in `SETTING_LAST_ROUND`: "receipt" voor een bon, "manual" voor alle bonnen."""
    return "receipt" if receipt_id else "manual"


async def _linking_round(receipt_id: str | None, now: str) -> dict[str, Any]:
    """Een ronde op afroep: voor een bon (`_receipt_round`) of voor alle bonnen zoals 's nachts."""
    if receipt_id:
        return await _receipt_round(receipt_id, now)
    trigger = _on_demand_trigger(None)
    products = await resolve_receipt_products(
        now, searches=RECEIPT_PRODUCTS_SEARCHES_NIGHTLY, deadline_seconds=RECEIPT_PRODUCTS_DEADLINE_NIGHTLY, trigger=trigger
    )
    return {"products": products, "images": await backfill_receipt_images(now, trigger=trigger)}


async def _images_round(now: str) -> dict[str, Any]:
    """Een ronde op afroep met alleen foto's (de knop "Foto's aanvullen"): zo groot als 's nachts, zonder zoekopdrachten."""
    return {"images": await backfill_receipt_images(now, batches=RECEIPT_IMAGES_BATCHES, trigger=IMAGES_TRIGGER)}


async def _linking_worker(receipt_id: str | None, now: str, actor: str, images: bool = False) -> None:
    """De task van een ronde op afroep: vangt elke fout, schrijft hem in het logboek en legt de ronde vast in de audit-log, net
    als de nachtelijke ronde van de afbeeldingencache. Met `images` alleen de foto's (`_images_round`). Een annulering (het
    afsluiten van de app) gaat er gewoon doorheen: de task is dan klaar en de ronde staat niet meer op "bezig"."""
    started = time.perf_counter()
    result: dict[str, Any] | None = None
    problem: Exception | None = None
    trigger = IMAGES_TRIGGER if images else _on_demand_trigger(receipt_id)
    try:
        result = await _images_round(now) if images else await _linking_round(receipt_id, now)
    except Exception as exc:  # noqa: BLE001 - een task mag niet stilletjes sterven
        problem = exc
        log.exception("Koppelen: de ronde op afroep (%s) mislukte (bon %s)", trigger, receipt_id or "alle")
    try:
        # Ook een ronde zonder werk (alles al gekoppeld, of de pauze) staat daarna als laatste ronde, met nullen. Wat de ronde wel
        # deed, staat er al onder dezelfde `now` en `trigger` en blijft gelijk.
        await _record_round(now, trigger, {})
    except Exception:  # noqa: BLE001
        log.exception("Koppelen: de laatste ronde vastleggen mislukte")
    action = "images" if images else "receipt linking" if receipt_id else "linking"
    try:
        await audit.arecord(
            kind="task", service="ah", action=action, ok=problem is None,
            duration_ms=round((time.perf_counter() - started) * 1000), actor=actor,
            error=f"{type(problem).__name__}: {problem}" if problem else "",
            detail={"receiptId": receipt_id, **(result or {})},
        )
    except Exception:  # noqa: BLE001
        log.exception("Koppelen: de ronde op afroep vastleggen mislukte")


async def run_linking(receipt_id: str | None = None, force: bool = False, images: bool = False) -> dict[str, Any]:
    """Start een ronde op afroep als achtergrondtask (`linking-run`) en geef de toestand terug. Met `force` eerst de pauze na een
    403 of 429 opheffen (alleen de beheerder, zie de route). Met `images` alleen de foto's van alle bonnen, zonder zoekopdrachten
    (`_images_round`). Loopt er al een, dan `LinkingBusy` en blijft alles zoals het was."""
    if images and receipt_id:
        raise ValueError("an images round is for all receipts")
    global _LINKING_TASK, _LINKING_RECEIPT_ID
    now = _now()
    actor = audit.current_actor()  # de task kent het verzoek niet meer
    async with _lock("linking"):
        if _linking_busy():
            raise LinkingBusy(receipt_id)
        if force:
            await auth.aset_setting(SETTING_RECEIPT_PRODUCTS_PAUSED, "")
            await auth.aset_setting(SETTING_RECEIPT_PRODUCTS_PAUSED_REASON, "")
        _LINKING_TASK = tasks.spawn("linking-run", _linking_worker(receipt_id, now, actor, images))
        _LINKING_RECEIPT_ID = receipt_id
    return await linking_status(now)


def _receipt_json(row: Any, count: int | None = None) -> dict[str, Any]:
    return {
        "id": row["receipt_id"], "dateTime": row["date_time"], "total": row["total"],
        "detailsSynced": bool(row["details_synced"]), **({"itemCount": count} if count is not None else {}),
    }


# Het product van een bonregel: je eigen koppeling (#238) gaat voor de automatische, zie `product_links.RECEIPT_LINE_JOIN_SQL`.
_LINE_LINKS_SQL = product_links.RECEIPT_LINE_JOIN_SQL
_LINE_PRODUCT_SQL = product_links.RECEIPT_LINE_PRODUCT_SQL
_LINE_TITLE_SQL = product_links.RECEIPT_LINE_TITLE_SQL
_LINE_IMAGE_SQL = product_links.RECEIPT_LINE_IMAGE_SQL

# De samenvatting per bon: de foto's van de gekoppelde webwinkelproducten en de namen (die van de webwinkel als ze er zijn), in de
# volgorde van de bon; het bedrag van de bonusregels (positief) en de betaalwijze uit de JSON-kolommen.
_RECEIPT_SUMMARY_SQL = f"""
    (SELECT GROUP_CONCAT(image, char(10)) FROM (SELECT {_LINE_IMAGE_SQL} AS image FROM ah_receipt_items l
        {_LINE_LINKS_SQL}
        WHERE l.receipt_id = r.receipt_id AND COALESCE({_LINE_IMAGE_SQL}, '') != '' ORDER BY l.position LIMIT {SUMMARY_IMAGES})) AS summary_images,
    (SELECT GROUP_CONCAT(name, char(10)) FROM (SELECT COALESCE(NULLIF({_LINE_TITLE_SQL}, ''), l.name) AS name FROM ah_receipt_items l
        {_LINE_LINKS_SQL}
        WHERE l.receipt_id = r.receipt_id AND COALESCE(NULLIF({_LINE_TITLE_SQL}, ''), l.name) != '' ORDER BY l.position LIMIT {SUMMARY_NAMES})) AS summary_names,
    (SELECT COUNT(*) FROM ah_receipt_items l WHERE l.receipt_id = r.receipt_id) AS summary_lines,
    (SELECT -SUM(json_extract(value, '$.amount'))
        FROM json_each(CASE WHEN json_valid(r.discounts) THEN r.discounts ELSE '[]' END)) AS summary_savings,
    (SELECT SUM(MAX(l.regular_price - COALESCE(l.price, l.amount / l.quantity), 0) * COALESCE(l.quantity, 1)) FROM ah_receipt_items l
        WHERE l.receipt_id = r.receipt_id AND l.regular_price IS NOT NULL) AS summary_history,
    json_extract(CASE WHEN json_valid(r.payments) THEN r.payments ELSE '[]' END, '$[0].method') AS summary_payment
"""


def _receipt_summary(row: Any) -> dict[str, Any]:
    savings = row["summary_savings"]
    return {
        "images": price_import.split_lines(row["summary_images"]), "productNames": price_import.split_lines(row["summary_names"]),
        # De bonusregels plus wat de regels met een gewone prijs van 30 dagen scheelden, met als prijs per stuk het bedrag gedeeld door
        # het aantal als de bon geen prijs noemt (bij één stuk). Dezelfde som als de detailroute: die geeft een regel hooguit zijn eigen
        # bedrag aan bonus en telt de rest in het totaal. Alleen als de gewone prijs onder de prijs vóór bonus ligt, kan het verschillen.
        "savings": None if not row["summary_lines"] else round(float(savings or 0) + float(row["summary_history"] or 0), 2),
        "payment": row["summary_payment"] or None,
        **_status_fields("receipt", row),
    }


async def list_receipts(summary: bool = False) -> dict[str, Any]:
    """Alle bonnen, de nieuwste eerst. Met `summary` (de lijstroute) per bon ook `images`, `productNames`, `savings` en `payment`."""
    async with _aconnect() as conn:
        rows = await conn.execute_fetchall(
            f"""
            SELECT r.*, (SELECT COUNT(*) FROM ah_receipt_items i WHERE i.receipt_id = r.receipt_id) AS item_count
            {"," + _RECEIPT_SUMMARY_SQL if summary else ""}
            FROM ah_receipts r ORDER BY r.date_time DESC
            """
        )
    return {
        "syncedAt": await synced_at("receipts"),
        "receipts": [{**_receipt_json(row, row["item_count"]), **(_receipt_summary(row) if summary else {})} for row in rows],
    }


async def _receipt_lines(conn: aiosqlite.Connection, receipt_id: str) -> list[dict[str, Any]]:
    """De regels van een bon, elk met het webwinkelproduct (`product`: id, titel en foto) als de regel gekoppeld is, anders None.
    Een eigen koppeling (#238) gaat voor de automatische; die regel heeft ook `link` ({"kind", "key"}), zodat het scherm hem kan
    wijzigen of weghalen."""
    rows = await conn.execute_fetchall(
        f"""
        SELECT l.*, {_LINE_PRODUCT_SQL} AS webshop_id, {_LINE_TITLE_SQL} AS webshop_title, {_LINE_IMAGE_SQL} AS webshop_image,
               COALESCE(k1.kind, k2.kind) AS link_kind, COALESCE(k1.key, k2.key) AS link_key
        FROM ah_receipt_items l
        {_LINE_LINKS_SQL}
        WHERE l.receipt_id = ? ORDER BY l.position
        """,
        (receipt_id,),
    )
    return [
        {
            "productId": row["product_id"], "name": row["name"], "quantity": row["quantity"], "price": row["price"], "amount": row["amount"],
            "product": {"id": row["webshop_id"], "title": row["webshop_title"], "image": row["webshop_image"] or ""} if row["webshop_id"] else None,
            "regularPrice": row["regular_price"],
            "link": {"kind": row["link_kind"], "key": row["link_key"]} if row["link_kind"] else None,
        }
        for row in rows
    ]


def _json_list(value: Any) -> list[Any]:
    """Een JSON-lijst uit een kolom. Iets anders (leeg, kapot, geen lijst) wordt een lege lijst."""
    try:
        parsed: Any = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


async def _enrich_receipt(conn: aiosqlite.Connection, row: Any, lines: list[dict[str, Any]]) -> dict[str, Any]:
    """Zet bij elke bonregel het bonusbedrag (`bonus`, van de bonusregels onderaan de bon) en de velden van `purchase_lines.enrich`.
    Geeft `saved`, `savedPercent`, `statusKind` en `statusText` van de bon."""
    discounts = _json_list(row["discounts"])
    bonus = purchase_lines.match_receipt_bonus(lines, discounts)
    for index, line in enumerate(lines):
        line["bonus"] = bonus.get(index, 0.0)
    # Een bonusregel die bij geen regel past, telt alleen in het totaal.
    unmatched = purchase_lines.unmatched_bonus(lines, discounts)
    totals = await purchase_lines.enrich(conn, "ah", "receipt", str(row["date_time"] or "")[:10], lines, unmatched_bonus=unmatched)
    kind, text = purchase_lines.status_kind("receipt", row)
    return {**totals, "statusKind": kind, "statusText": text}


_RECEIPT_IMAGES_MISSING_SQL = """
SELECT DISTINCT p.product_id FROM ah_receipt_items l JOIN ah_receipt_products p ON p.receipt_product_id = l.product_id
WHERE l.receipt_id = ? AND p.image IS NULL AND p.product_id IS NOT NULL
"""


async def get_receipt(
    receipt_id: str, lookup: Callable[[list[str]], Awaitable[Any]] | None = None, images: bool = False
) -> dict[str, Any] | None:
    """Een bon met zijn regels. Met `lookup` (bij het openen van de bon) worden de regels die nog bij geen webwinkelproduct horen
    eerst bij AH opgezocht (`resolve_receipt_names`, hooguit `RECEIPT_PRODUCTS_OPEN_SEARCHES` zoekopdrachten); het resultaat staat
    in `products`, of None als het opzoeken zelf misging: de bon komt er dan gewoon zonder koppelingen. Met `images` komen daarna
    de foto's van de gekoppelde regels die nog geen foto hebben (een verzoek aan AH, `backfill_receipt_images`), in `images`."""
    receipt_id = str(receipt_id)
    async with _aconnect() as conn:
        row = await _fetchone(conn, "SELECT * FROM ah_receipts WHERE receipt_id = ?", (receipt_id,))
        if not row:
            return None
        lines = await _receipt_lines(conn, receipt_id)
    result: dict[str, Any] = {}
    if lookup:
        unlinked = [line["name"] for line in lines if line["product"] is None and line["productId"]]
        try:
            result["products"] = await lookup(unlinked) if unlinked else {"found": 0, "missing": 0, "failed": 0, "searches": 0}
        except Exception:  # noqa: BLE001 - de koppeling is een extraatje; de bon zelf is wat je wilde zien
            result["products"] = None
    if images:
        async with _aconnect() as conn:
            missing = {int(row_id[0]) for row_id in await conn.execute_fetchall(_RECEIPT_IMAGES_MISSING_SQL, (receipt_id,))}
        try:
            result["images"] = await backfill_receipt_images(batches=1, only=missing) if missing else {"products": 0, "requests": 0, "failed": 0}
        except Exception:  # noqa: BLE001 - de foto's zijn een extraatje
            result["images"] = None
    # Na het koppelen kunnen er regels bij een product horen, dus pas nu de gewone prijs van 30 dagen bijwerken en lezen.
    await purchase_lines.fill_regular("ah", receipt_ids=[receipt_id])
    async with _aconnect() as conn:
        lines = await _receipt_lines(conn, receipt_id)
        linking_todo = await receipt_linking_todo(conn, receipt_id, _now())
        enriched = await _enrich_receipt(conn, row, lines)
    return {
        **_receipt_json(row, len(lines)),
        "items": lines,
        "linkingTodo": linking_todo,
        "discounts": _json_list(row["discounts"]),
        "payments": _json_list(row["payments"]),
        **enriched,
        **result,
    }


# --- zoeken in alles wat je kocht -----------------------------------------------------------------

async def search_receipts(query: Any, limit: int = 200) -> dict[str, Any]:
    """Op welke kassabonnen staat een product? Per bon de regels die passen (alle woorden moeten erin staan), nieuwste eerst.

    `unsearched` telt de bonnen waarvan de regels nog niet zijn opgehaald: die kunnen nog niet worden doorzocht.
    """
    words = ah_lists._fold(query).split()
    async with _aconnect() as conn:
        receipts = await conn.execute_fetchall("SELECT * FROM ah_receipts ORDER BY date_time DESC, receipt_id DESC")
        lines = (
            await conn.execute_fetchall("SELECT receipt_id, name, quantity, amount FROM ah_receipt_items ORDER BY receipt_id, position")
            if words else []
        )
    unsearched = sum(1 for row in receipts if not row["details_synced"])
    matches: dict[str, list[dict[str, Any]]] = {}
    for line in lines:
        if all(word in ah_lists._fold(line["name"]) for word in words):
            matches.setdefault(line["receipt_id"], []).append({"name": line["name"], "quantity": line["quantity"], "amount": line["amount"]})
    found = [{**_receipt_json(row), "matches": matches[row["receipt_id"]]} for row in receipts if row["receipt_id"] in matches]
    return {"query": str(query).strip(), "receipts": found[: max(1, int(limit))], "total": len(found), "unsearched": unsearched if words else 0}


async def search_purchases(query: Any, limit: int = 50) -> dict[str, Any]:
    """Waar en wanneer kocht je een product? Zoekt in bestellingen en kassabonnen (alle woorden moeten erin staan)."""
    words = ah_lists._fold(query).split()
    if not words:
        return {"query": "", "products": [], "occurrences": []}
    occurrences: list[dict[str, Any]] = []
    async with _aconnect() as conn:
        for row in await conn.execute_fetchall(
            """
            SELECT i.title, i.brand, i.quantity, i.amount, i.price, o.order_id, o.delivery_date, o.first_seen, o.kind
            FROM ah_order_items i JOIN ah_orders o ON o.order_id = i.order_id
            """
        ):
            if all(word in ah_lists._fold(f"{row['title']} {row['brand'] or ''}") for word in words):
                occurrences.append({
                    "source": "bestelling", "product": row["title"], "brand": row["brand"], "quantity": row["quantity"],
                    "amount": _line_amount(row), "date": (row["delivery_date"] or row["first_seen"] or "")[:10], "id": str(row["order_id"]),
                    "open": row["kind"] == "open",
                })
        for row in await conn.execute_fetchall(
            "SELECT l.name, l.quantity, l.amount, r.receipt_id, r.date_time FROM ah_receipt_items l JOIN ah_receipts r ON r.receipt_id = l.receipt_id"
        ):
            if all(word in ah_lists._fold(row["name"]) for word in words):
                occurrences.append({
                    "source": "kassabon", "product": row["name"], "brand": None, "quantity": row["quantity"],
                    "amount": row["amount"], "date": (row["date_time"] or "")[:10], "id": row["receipt_id"], "open": False,
                })
        # Bonnen en bestellingen waarvan de regels nog niet zijn opgehaald: die kunnen nog niet worden doorzocht.
        unsearched = (await _fetchone(
            conn,
            "SELECT (SELECT COUNT(*) FROM ah_receipts WHERE details_synced = 0) + (SELECT COUNT(*) FROM ah_orders WHERE items_synced = 0)",
        ))[0]
        # Oude bestellingen uit je bevestigingsmails.
        occurrences += await price_import.search_lines(conn, "ah", words, ah_lists._fold)
    occurrences.sort(key=lambda entry: entry["date"], reverse=True)
    grouped: dict[str, dict[str, Any]] = {}
    for entry in occurrences:
        key = ah_lists._fold(entry["product"])
        group = grouped.setdefault(key, {"product": entry["product"], "lastDate": entry["date"], "times": 0, "totalQuantity": 0})
        group["times"] += 1
        group["totalQuantity"] += entry["quantity"] or 0
    return {
        "query": str(query).strip(),
        "products": sorted(grouped.values(), key=lambda group: group["lastDate"], reverse=True),
        "occurrences": occurrences[: max(1, int(limit))],
        "total": len(occurrences),
        "unsearched": unsearched,
    }
