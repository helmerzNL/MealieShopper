"""Lokale kopie van je Jumbo-bestellingen.

Alles wat bij Jumbo wordt opgehaald, staat daarna in SQLite. Lezen gaat altijd uit die kopie en vraagt nooit
iets aan Jumbo, dus de bestelhistorie is ook te bekijken (en door de MCP-server te bevragen) als Jumbo niet
bereikbaar is. Alleen een synchronisatie praat met Jumbo.

- Een **afgeronde bestelling** verandert niet meer en wordt nooit verwijderd, ook niet als Jumbo hem later
  niet meer toont. De lijst komt in pagina's van 10 binnen; de producten van een bestelling halen we per
  synchronisatie voor een beperkt aantal bestellingen op (de rest volgt bij de volgende).
- Geannuleerde bestellingen bewaren we niet.
Het afleveradres, je klantnummer en het reserveringsnummer bewaren we bewust niet; alleen datum en tijdvak.
Winkelbonnen kent MealieShopper bij Jumbo nog niet: we melden alleen hoeveel Jumbo er heeft.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from typing import Any

from . import auth, jumbo
from .ah_lists import _fold

ORDER_DETAILS_PER_SYNC = 25  # een synchronisatie blijft kort; de rest volgt bij de volgende
MAX_FIRST_FAILURES = 3  # zoveel bestellingen achter elkaar zonder één gelukte: stoppen met proberen

SCHEMA = """
CREATE TABLE IF NOT EXISTS jumbo_orders (
  order_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  state TEXT,
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

CREATE TABLE IF NOT EXISTS jumbo_order_items (
  order_id TEXT NOT NULL,
  position INTEGER NOT NULL,
  product_id TEXT, title TEXT, brand TEXT, unit_size TEXT, quantity INTEGER,
  amount REAL, price REAL, price_before_bonus REAL, bonus INTEGER, taxonomy TEXT, image TEXT,
  PRIMARY KEY (order_id, position)
);

CREATE TABLE IF NOT EXISTS jumbo_orders_meta (
  key TEXT PRIMARY KEY,
  synced_at TEXT NOT NULL
);
"""

ITEM_COLUMNS = ("product_id", "title", "brand", "unit_size", "quantity", "amount", "price", "price_before_bonus", "bonus", "taxonomy", "image")


def _connect():
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


def synced_at(key: str = "orders") -> str | None:
    with closing(_connect()) as conn:
        row = conn.execute("SELECT synced_at FROM jumbo_orders_meta WHERE key = ?", (key,)).fetchone()
    return row["synced_at"] if row else None


def _save_order(conn, header: dict[str, Any], items: list[dict[str, Any]] | None) -> None:
    """Bewaar of werk een bestelling bij. `items=None` laat de bewaarde producten staan."""
    now = _now()
    existing = conn.execute("SELECT first_seen, items_synced FROM jumbo_orders WHERE order_id = ?", (header["id"],)).fetchone()
    conn.execute(
        """
        INSERT INTO jumbo_orders (order_id, kind, state, shopping_type, method, total, delivery_date, slot_start, slot_end, items_synced, first_seen, synced_at)
        VALUES (?, 'delivered', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(order_id) DO UPDATE SET state = COALESCE(NULLIF(excluded.state, ''), jumbo_orders.state),
          shopping_type = COALESCE(NULLIF(excluded.shopping_type, ''), jumbo_orders.shopping_type),
          method = COALESCE(NULLIF(excluded.method, ''), jumbo_orders.method),
          total = COALESCE(excluded.total, jumbo_orders.total),
          delivery_date = COALESCE(NULLIF(excluded.delivery_date, ''), jumbo_orders.delivery_date),
          slot_start = COALESCE(NULLIF(excluded.slot_start, ''), jumbo_orders.slot_start),
          slot_end = COALESCE(NULLIF(excluded.slot_end, ''), jumbo_orders.slot_end),
          items_synced = excluded.items_synced, synced_at = excluded.synced_at
        """,
        (header["id"], header.get("state"), header.get("shoppingType"), header.get("method"), header.get("total"),
         header.get("deliveryDate"), header.get("slotStart"), header.get("slotEnd"),
         1 if items is not None else (existing["items_synced"] if existing else 0),
         existing["first_seen"] if existing else now, now),
    )
    if items is not None:
        conn.execute("DELETE FROM jumbo_order_items WHERE order_id = ?", (header["id"],))
        conn.executemany(
            f"INSERT INTO jumbo_order_items (order_id, position, {', '.join(ITEM_COLUMNS)}) VALUES (?, ?{', ?' * len(ITEM_COLUMNS)})",
            [(header["id"], position, *_item_values(item)) for position, item in enumerate(items)],
        )


def _error_name(exc: Exception) -> str:
    """Het fouttype; bij een schemafout (ook van de bestelling-aanvraag) ook Jumbo's melding, zonder nummers of id's."""
    if isinstance(exc, jumbo.JumboSchemaError):
        return f"{type(exc).__name__}: {str(exc)[:200]}"
    return type(exc).__name__


def sync_orders() -> dict[str, Any]:
    """Haal de lijst met bestellingen op en daarna de producten van een aantal bestellingen.

    Nieuwste eerst: een eerste synchronisatie vult dus eerst de recente bestellingen met producten aan en elke volgende
    de rest. Lukt het ophalen van de producten niet (Jumbo wijst de query af), dan blijft de lijst staan en melden we
    `detailError`.
    """
    listed = jumbo.get_orders()
    orders = [order for order in listed["orders"] if not order["cancelled"]]
    with closing(_connect()) as conn:
        complete = {row["order_id"] for row in conn.execute("SELECT order_id FROM jumbo_orders WHERE items_synced = 1")}
        known = {row["order_id"] for row in conn.execute("SELECT order_id FROM jumbo_orders")}
    cancelled = {order["id"] for order in listed["orders"] if order["cancelled"]}
    new = sum(1 for order in orders if order["id"] not in known)
    wanted = [order for order in orders if order["id"] not in complete][:ORDER_DETAILS_PER_SYNC]

    details: dict[str, dict[str, Any]] = {}
    detail_error: str | None = None
    failures = 0
    for order in wanted:
        try:
            details[order["id"]] = jumbo.get_order(order["id"])
        except jumbo.JumboAuthError:
            raise
        except Exception as exc:  # noqa: BLE001 - de kop bewaren we toch; de producten volgen bij de volgende synchronisatie
            detail_error = _error_name(exc)
            failures += 1
            if not details and failures >= MAX_FIRST_FAILURES:
                break  # er lukt niets: Jumbo wijst de query af, dus niet nog 20 keer proberen

    with closing(_connect()) as conn, conn:
        for order_id in cancelled:
            conn.execute("DELETE FROM jumbo_order_items WHERE order_id = ?", (order_id,))
            conn.execute("DELETE FROM jumbo_orders WHERE order_id = ?", (order_id,))
        for order in orders:
            info = details.get(order["id"])
            header = {**order, **({key: value for key, value in info.items() if key != "items" and value} if info else {}), "id": order["id"]}
            _save_order(conn, header, info["items"] if info else None)
        conn.execute(
            "INSERT INTO jumbo_orders_meta (key, synced_at) VALUES ('orders', ?) ON CONFLICT(key) DO UPDATE SET synced_at = excluded.synced_at",
            (_now(),),
        )
        remaining = conn.execute("SELECT COUNT(*) FROM jumbo_orders WHERE items_synced = 0").fetchone()[0]
    return {
        "orders": len(orders), "new": new, "remaining": remaining, "storeReceipts": listed["storeReceipts"],
        **({"detailError": detail_error} if detail_error else {}),
    }


def _order_json(row: Any, count: int | None = None) -> dict[str, Any]:
    return {
        "id": row["order_id"], "kind": row["kind"], "state": row["state"], "stateText": None,
        "shoppingType": row["shopping_type"], "method": row["method"], "total": row["total"],
        "deliveryDate": row["delivery_date"], "slotStart": row["slot_start"], "slotEnd": row["slot_end"],
        "itemsSynced": bool(row["items_synced"]), "syncedAt": row["synced_at"],
        **({"itemCount": count} if count is not None else {}),
    }


def list_orders() -> dict[str, Any]:
    with closing(_connect()) as conn:
        rows = conn.execute(
            """
            SELECT o.*, (SELECT COUNT(*) FROM jumbo_order_items i WHERE i.order_id = o.order_id) AS item_count
            FROM jumbo_orders o
            ORDER BY COALESCE(NULLIF(o.delivery_date, ''), o.first_seen) DESC, o.order_id DESC
            """
        ).fetchall()
    return {"syncedAt": synced_at(), "orders": [_order_json(row, row["item_count"]) for row in rows]}


def get_order(order_id: Any) -> dict[str, Any] | None:
    with closing(_connect()) as conn:
        row = conn.execute("SELECT * FROM jumbo_orders WHERE order_id = ?", (str(order_id),)).fetchone()
        if not row:
            return None
        items = conn.execute("SELECT * FROM jumbo_order_items WHERE order_id = ? ORDER BY position", (str(order_id),)).fetchall()
    return {**_order_json(row, len(items)), "items": [_item_json(item) for item in items]}


def search_purchases(query: Any, limit: int = 50) -> dict[str, Any]:
    """Wanneer kocht je een product? Zoekt in de producten van je bestellingen (alle woorden moeten erin staan)."""
    words = _fold(query).split()
    if not words:
        return {"query": "", "products": [], "occurrences": [], "total": 0}
    occurrences: list[dict[str, Any]] = []
    with closing(_connect()) as conn:
        for row in conn.execute(
            """
            SELECT i.title, i.brand, i.quantity, i.amount, o.order_id, o.delivery_date, o.first_seen
            FROM jumbo_order_items i JOIN jumbo_orders o ON o.order_id = i.order_id
            """
        ):
            if all(word in _fold(f"{row['title']} {row['brand'] or ''}") for word in words):
                occurrences.append({
                    "source": "bestelling", "product": row["title"], "brand": row["brand"], "quantity": row["quantity"],
                    "amount": row["amount"], "date": (row["delivery_date"] or row["first_seen"] or "")[:10], "id": str(row["order_id"]),
                    "open": False,
                })
        unsearched = conn.execute("SELECT COUNT(*) FROM jumbo_orders WHERE items_synced = 0").fetchone()[0]
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
