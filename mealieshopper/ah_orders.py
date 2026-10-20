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

import json
from contextlib import closing
from datetime import datetime, timezone
from typing import Any

from . import ah, ah_lists, auth

RECEIPT_PAGE = 100
MAX_RECEIPT_PAGES = 30  # 3.000 bonnen; voorkomt een eindeloze lus bij een afwijkend antwoord
ORDER_DETAILS_PER_SYNC = 15  # idem voor de producten van afgeleverde bestellingen
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

CREATE TABLE IF NOT EXISTS ah_orders_meta (
  key TEXT PRIMARY KEY,
  synced_at TEXT NOT NULL
);
"""

ITEM_COLUMNS = ("product_id", "title", "brand", "unit_size", "quantity", "amount", "price", "price_before_bonus", "bonus", "taxonomy", "image")


def _connect():
    auth.ensure_db()
    conn = auth.connect()
    conn.executescript(SCHEMA)
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _item_values(item: dict[str, Any]) -> tuple:
    return (
        item.get("productId"), item.get("title"), item.get("brand"), item.get("unitSize"), item.get("quantity"),
        item.get("amount"), item.get("price"), item.get("priceBeforeBonus"), 1 if item.get("bonus") else 0,
        item.get("taxonomy"), item.get("image"),
    )


def _item_json(row: Any) -> dict[str, Any]:
    return {
        "productId": row["product_id"], "title": row["title"], "brand": row["brand"], "unitSize": row["unit_size"],
        "quantity": row["quantity"], "amount": row["amount"], "price": row["price"],
        "priceBeforeBonus": row["price_before_bonus"], "bonus": bool(row["bonus"]),
        "taxonomy": row["taxonomy"], "image": row["image"],
    }


def _stamp(conn, key: str) -> None:
    conn.execute(
        "INSERT INTO ah_orders_meta (key, synced_at) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET synced_at = excluded.synced_at",
        (key, _now()),
    )


def synced_at(key: str) -> str | None:
    with closing(_connect()) as conn:
        row = conn.execute("SELECT synced_at FROM ah_orders_meta WHERE key = ?", (key,)).fetchone()
    return row["synced_at"] if row else None


# --- bestelmandje ---------------------------------------------------------------------------------

def save_basket(basket: dict[str, Any]) -> None:
    with closing(_connect()) as conn, conn:
        conn.execute("DELETE FROM ah_basket")
        conn.execute("DELETE FROM ah_basket_items")
        conn.execute(
            """
            INSERT INTO ah_basket (id, order_id, state, shopping_type, total, discount, delivery_date, slot_start, slot_end, synced_at)
            VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (basket.get("id"), basket.get("state"), basket.get("shoppingType"), basket.get("total"), basket.get("discount"),
             basket.get("deliveryDate"), basket.get("slotStart"), basket.get("slotEnd"), _now()),
        )
        conn.executemany(
            f"INSERT INTO ah_basket_items (position, {', '.join(ITEM_COLUMNS)}) VALUES (?{', ?' * len(ITEM_COLUMNS)})",
            [(position, *_item_values(item)) for position, item in enumerate(basket.get("items") or [])],
        )


def get_basket() -> dict[str, Any]:
    """Het laatst opgehaalde bestelmandje. `syncedAt` is None als er nog nooit is gesynchroniseerd."""
    with closing(_connect()) as conn:
        head = conn.execute("SELECT * FROM ah_basket WHERE id = 1").fetchone()
        rows = conn.execute("SELECT * FROM ah_basket_items ORDER BY position").fetchall()
    if not head:
        return {"syncedAt": None, "items": [], "itemCount": 0, "units": 0}
    items = [_item_json(row) for row in rows]
    return {
        "syncedAt": head["synced_at"], "orderId": head["order_id"], "state": head["state"], "total": head["total"],
        "discount": head["discount"], "deliveryDate": head["delivery_date"], "slotStart": head["slot_start"],
        "slotEnd": head["slot_end"], "items": items, "itemCount": len(items),
        "units": sum(item["quantity"] or 0 for item in items),
    }


def sync_basket() -> dict[str, Any]:
    save_basket(ah.get_active_order())
    return get_basket()


# --- bestellingen ---------------------------------------------------------------------------------

def _save_order(conn, header: dict[str, Any], kind: str, items: list[dict[str, Any]] | None) -> None:
    """Bewaar of werk een bestelling bij. `items=None` laat de bewaarde producten staan."""
    now = _now()
    existing = conn.execute("SELECT first_seen, items_synced FROM ah_orders WHERE order_id = ?", (header["id"],)).fetchone()
    conn.execute(
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
        conn.execute("DELETE FROM ah_order_items WHERE order_id = ?", (header["id"],))
        conn.executemany(
            f"INSERT INTO ah_order_items (order_id, position, {', '.join(ITEM_COLUMNS)}) VALUES (?, ?{', ?' * len(ITEM_COLUMNS)})",
            [(header["id"], position, *_item_values(item)) for position, item in enumerate(items)],
        )


def _details_or_none(order_id: int) -> dict[str, Any] | None:
    try:
        return ah.get_order_details(order_id)
    except Exception:  # noqa: BLE001 - de kop van de bestelling bewaren we toch; de producten volgen bij de volgende sync
        return None


def _forget_order(conn, order_id: int) -> None:
    conn.execute("DELETE FROM ah_order_items WHERE order_id = ?", (order_id,))
    conn.execute("DELETE FROM ah_orders WHERE order_id = ?", (order_id,))


def sync_orders() -> dict[str, Any]:
    """Haal de open en de afgeronde bestellingen op; wat is afgeleverd bewaren we voor altijd.

    AH geeft van de afgeronde bestellingen per keer de laatste 10, dus een eerste synchronisatie vindt er
    hoogstens 10 en elke volgende voegt toe wat sindsdien is afgeleverd. Geannuleerde bestellingen bewaren we niet.
    """
    open_orders = ah.get_open_fulfillments()
    open_ids = {order["id"] for order in open_orders}
    try:
        closed_orders = ah.get_closed_fulfillments()
    except Exception:  # noqa: BLE001 - de open bestellingen zijn al binnen; de afgeronde volgen bij de volgende sync
        closed_orders = []
    cancelled = {order["id"] for order in closed_orders if order["deliveryStatus"] == "CANCELLED"}
    delivered = [order for order in closed_orders if order["deliveryStatus"] != "CANCELLED" and order["id"] not in open_ids]
    with closing(_connect()) as conn:
        previously_open = [row["order_id"] for row in conn.execute("SELECT order_id FROM ah_orders WHERE kind = 'open'")]
        complete = {row["order_id"] for row in conn.execute("SELECT order_id FROM ah_orders WHERE kind = 'delivered' AND items_synced = 1")}
        incomplete = [row["order_id"] for row in conn.execute("SELECT order_id FROM ah_orders WHERE kind = 'delivered' AND items_synced = 0")]

    fetched = {order["id"]: _details_or_none(order["id"]) for order in open_orders}
    # Wat open was en het nu niet meer is, is afgeleverd (of geannuleerd): een laatste keer ophalen.
    gone = [order_id for order_id in previously_open if order_id not in open_ids and order_id not in cancelled]
    listed_ids = {order["id"] for order in delivered}
    todo = [order for order in delivered if order["id"] not in complete]
    wanted = [order["id"] for order in todo] + [order_id for order_id in gone + incomplete if order_id not in listed_ids]
    details = {order_id: _details_or_none(order_id) for order_id in list(dict.fromkeys(wanted))[:ORDER_DETAILS_PER_SYNC]}

    archived = 0
    with closing(_connect()) as conn, conn:
        for order in open_orders:
            info = fetched[order["id"]]
            header = {**order, **({"deliveryDate": info["deliveryDate"] or order["deliveryDate"]} if info else {})}
            _save_order(conn, header, "open", info["items"] if info else None)
        for order_id in cancelled:
            _forget_order(conn, order_id)
        by_id = {order["id"]: order for order in delivered}
        for order_id, info in details.items():
            if info is None:
                continue  # niet gelukt: we proberen het bij de volgende synchronisatie opnieuw
            header = {**by_id.get(order_id, {}), **{key: value for key, value in info.items() if key != "items" and value}, "id": order_id}
            known = order_id in complete or order_id in incomplete
            _save_order(conn, header, "delivered", info["items"])
            archived += 0 if known else 1
        # Afgeleverd maar de producten nog niet binnen (meer dan er in een keer passen): kop alvast bewaren
        for order in todo:
            if order["id"] not in details:
                _save_order(conn, order, "delivered", None)
                archived += 1
        _stamp(conn, "orders")
        remaining = conn.execute("SELECT COUNT(*) FROM ah_orders WHERE kind = 'delivered' AND items_synced = 0").fetchone()[0]
        unfinished = sum(1 for order_id in gone if details.get(order_id) is None)
    return {"open": len(open_orders), "archived": archived, "remaining": remaining, "unfinished": unfinished}


def add_order_by_id(order_id: int) -> dict[str, Any]:
    """Haal één bestelling op uit AH (elke bestelling-id) en bewaar hem als afgeleverd."""
    details = ah.get_order_details(order_id)
    with closing(_connect()) as conn, conn:
        _save_order(conn, {**details, "id": order_id}, "delivered", details["items"])
    return get_order(order_id) or {}


def _order_json(row: Any, count: int | None = None) -> dict[str, Any]:
    return {
        "id": row["order_id"], "kind": row["kind"], "state": row["state"], "stateText": row["state_text"],
        "shoppingType": row["shopping_type"], "method": row["method"], "total": row["total"],
        "deliveryDate": row["delivery_date"], "slotStart": row["slot_start"], "slotEnd": row["slot_end"],
        "itemsSynced": bool(row["items_synced"]), "syncedAt": row["synced_at"],
        **({"itemCount": count} if count is not None else {}),
    }


def list_orders() -> dict[str, Any]:
    with closing(_connect()) as conn:
        rows = conn.execute(
            """
            SELECT o.*, (SELECT COUNT(*) FROM ah_order_items i WHERE i.order_id = o.order_id) AS item_count
            FROM ah_orders o
            ORDER BY (o.kind = 'open') DESC, COALESCE(NULLIF(o.delivery_date, ''), o.first_seen) DESC, o.order_id DESC
            """
        ).fetchall()
    return {"syncedAt": synced_at("orders"), "orders": [_order_json(row, row["item_count"]) for row in rows]}


def get_order(order_id: int) -> dict[str, Any] | None:
    with closing(_connect()) as conn:
        row = conn.execute("SELECT * FROM ah_orders WHERE order_id = ?", (int(order_id),)).fetchone()
        if not row:
            return None
        items = conn.execute("SELECT * FROM ah_order_items WHERE order_id = ? ORDER BY position", (int(order_id),)).fetchall()
    return {**_order_json(row, len(items)), "items": [_item_json(item) for item in items]}


# --- kassabonnen ----------------------------------------------------------------------------------

def sync_receipts() -> dict[str, Any]:
    """Vul de kassabonnen aan: nieuwe bonnen ophalen, daarna van een aantal bonnen de regels."""
    listed: list[dict[str, Any]] = []
    for page in range(MAX_RECEIPT_PAGES):
        batch = ah.get_receipts(page * RECEIPT_PAGE, RECEIPT_PAGE)
        listed.extend(batch)
        if len(batch) < RECEIPT_PAGE:
            break
    now = _now()
    with closing(_connect()) as conn, conn:
        known = {row["receipt_id"] for row in conn.execute("SELECT receipt_id FROM ah_receipts")}
        new = [receipt for receipt in listed if receipt["id"] not in known]
        conn.executemany(
            "INSERT INTO ah_receipts (receipt_id, date_time, total, first_seen) VALUES (?, ?, ?, ?)",
            [(receipt["id"], receipt["dateTime"], receipt["total"], now) for receipt in new],
        )
        pending = [row["receipt_id"] for row in conn.execute(
            "SELECT receipt_id FROM ah_receipts WHERE details_synced = 0 ORDER BY date_time DESC LIMIT ?", (RECEIPT_DETAILS_PER_SYNC,))]
    failed = 0
    for receipt_id in pending:
        try:
            detail = ah.get_receipt(receipt_id)
        except Exception:  # noqa: BLE001 - deze bon proberen we bij de volgende synchronisatie opnieuw
            failed += 1
            continue
        with closing(_connect()) as conn, conn:
            conn.execute(
                "UPDATE ah_receipts SET details_synced = 1, discounts = ?, payments = ? WHERE receipt_id = ?",
                (json.dumps(detail["discounts"]), json.dumps(detail["payments"]), receipt_id),
            )
            conn.execute("DELETE FROM ah_receipt_items WHERE receipt_id = ?", (receipt_id,))
            conn.executemany(
                "INSERT INTO ah_receipt_items (receipt_id, position, product_id, name, quantity, price, amount) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(receipt_id, position, line["productId"], line["name"], line["quantity"], line["price"], line["amount"])
                 for position, line in enumerate(detail["items"])],
            )
    with closing(_connect()) as conn, conn:
        remaining = conn.execute("SELECT COUNT(*) FROM ah_receipts WHERE details_synced = 0").fetchone()[0]
        _stamp(conn, "receipts")
    return {"new": len(new), "total": len(listed), "remaining": remaining, "failed": failed}


def _receipt_json(row: Any, count: int | None = None) -> dict[str, Any]:
    return {
        "id": row["receipt_id"], "dateTime": row["date_time"], "total": row["total"],
        "detailsSynced": bool(row["details_synced"]), **({"itemCount": count} if count is not None else {}),
    }


def list_receipts() -> dict[str, Any]:
    with closing(_connect()) as conn:
        rows = conn.execute(
            """
            SELECT r.*, (SELECT COUNT(*) FROM ah_receipt_items i WHERE i.receipt_id = r.receipt_id) AS item_count
            FROM ah_receipts r ORDER BY r.date_time DESC
            """
        ).fetchall()
    return {"syncedAt": synced_at("receipts"), "receipts": [_receipt_json(row, row["item_count"]) for row in rows]}


def get_receipt(receipt_id: str) -> dict[str, Any] | None:
    with closing(_connect()) as conn:
        row = conn.execute("SELECT * FROM ah_receipts WHERE receipt_id = ?", (str(receipt_id),)).fetchone()
        if not row:
            return None
        lines = conn.execute("SELECT * FROM ah_receipt_items WHERE receipt_id = ? ORDER BY position", (str(receipt_id),)).fetchall()
    return {
        **_receipt_json(row, len(lines)),
        "items": [{"productId": l["product_id"], "name": l["name"], "quantity": l["quantity"], "price": l["price"], "amount": l["amount"]} for l in lines],
        "discounts": json.loads(row["discounts"] or "[]"),
        "payments": json.loads(row["payments"] or "[]"),
    }


# --- zoeken in alles wat je kocht -----------------------------------------------------------------

def search_receipts(query: Any, limit: int = 200) -> dict[str, Any]:
    """Op welke kassabonnen staat een product? Per bon de regels die passen (alle woorden moeten erin staan), nieuwste eerst.

    `unsearched` telt de bonnen waarvan de regels nog niet zijn opgehaald: die kunnen nog niet worden doorzocht.
    """
    words = ah_lists._fold(query).split()
    with closing(_connect()) as conn:
        receipts = conn.execute("SELECT * FROM ah_receipts ORDER BY date_time DESC, receipt_id DESC").fetchall()
        lines = conn.execute("SELECT receipt_id, name, quantity, amount FROM ah_receipt_items ORDER BY receipt_id, position").fetchall() if words else []
    unsearched = sum(1 for row in receipts if not row["details_synced"])
    matches: dict[str, list[dict[str, Any]]] = {}
    for line in lines:
        if all(word in ah_lists._fold(line["name"]) for word in words):
            matches.setdefault(line["receipt_id"], []).append({"name": line["name"], "quantity": line["quantity"], "amount": line["amount"]})
    found = [{**_receipt_json(row), "matches": matches[row["receipt_id"]]} for row in receipts if row["receipt_id"] in matches]
    return {"query": str(query).strip(), "receipts": found[: max(1, int(limit))], "total": len(found), "unsearched": unsearched if words else 0}


def search_purchases(query: Any, limit: int = 50) -> dict[str, Any]:
    """Waar en wanneer kocht je een product? Zoekt in bestellingen en kassabonnen (alle woorden moeten erin staan)."""
    words = ah_lists._fold(query).split()
    if not words:
        return {"query": "", "products": [], "occurrences": []}
    occurrences: list[dict[str, Any]] = []
    with closing(_connect()) as conn:
        for row in conn.execute(
            """
            SELECT i.title, i.brand, i.quantity, i.amount, o.order_id, o.delivery_date, o.first_seen, o.kind
            FROM ah_order_items i JOIN ah_orders o ON o.order_id = i.order_id
            """
        ):
            if all(word in ah_lists._fold(f"{row['title']} {row['brand'] or ''}") for word in words):
                occurrences.append({
                    "source": "bestelling", "product": row["title"], "brand": row["brand"], "quantity": row["quantity"],
                    "amount": row["amount"], "date": (row["delivery_date"] or row["first_seen"] or "")[:10], "id": str(row["order_id"]),
                    "open": row["kind"] == "open",
                })
        for row in conn.execute(
            "SELECT l.name, l.quantity, l.amount, r.receipt_id, r.date_time FROM ah_receipt_items l JOIN ah_receipts r ON r.receipt_id = l.receipt_id"
        ):
            if all(word in ah_lists._fold(row["name"]) for word in words):
                occurrences.append({
                    "source": "kassabon", "product": row["name"], "brand": None, "quantity": row["quantity"],
                    "amount": row["amount"], "date": (row["date_time"] or "")[:10], "id": row["receipt_id"], "open": False,
                })
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
    }
