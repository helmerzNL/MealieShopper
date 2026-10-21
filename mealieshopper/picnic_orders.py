"""Lokale kopie van je afgeleverde Picnic-bestellingen.

Alles wat bij Picnic wordt opgehaald, staat daarna in SQLite. Lezen gaat altijd uit die kopie en vraagt nooit iets
aan Picnic, dus de bestelhistorie is ook te bekijken (en door de MCP-server te bevragen) als Picnic niet bereikbaar
is. Alleen een synchronisatie praat met Picnic.

- Een **afgeleverde bestelling** verandert niet meer en wordt nooit verwijderd, ook niet als Picnic hem later niet
  meer toont. Het overzicht komt in één keer binnen; de producten van een bestelling halen we per synchronisatie
  voor een beperkt aantal bestellingen op (de rest volgt bij de volgende).
- Lopende bestellingen (`CURRENT`) bewaren we niet: die staan live in de app. Geannuleerde bestellingen bewaren we niet.
Het afleveradres bewaren we bewust niet; alleen dag, tijdvak, status, totaal en de producten.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from typing import Any

from . import auth, picnic
from .ah_lists import _fold

ORDER_DETAILS_PER_SYNC = 25  # een synchronisatie blijft kort; de rest volgt bij de volgende
MAX_FIRST_FAILURES = 3  # zoveel bestellingen achter elkaar zonder één gelukte: stoppen met proberen
NOT_STORED = {"CURRENT", "CANCELLED", "CANCELED"}  # alleen wat is afgeleverd bewaren we

SCHEMA = """
CREATE TABLE IF NOT EXISTS picnic_orders (
  order_id TEXT PRIMARY KEY,
  status TEXT,
  total REAL,
  delivery_date TEXT,
  slot_start TEXT,
  slot_end TEXT,
  delivered_start TEXT,
  delivered_end TEXT,
  items_synced INTEGER NOT NULL DEFAULT 0,
  first_seen TEXT NOT NULL,
  synced_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS picnic_order_items (
  order_id TEXT NOT NULL,
  position INTEGER NOT NULL,
  product_id TEXT, title TEXT, unit_size TEXT, quantity INTEGER, price REAL, list_price REAL, image TEXT,
  PRIMARY KEY (order_id, position)
);

CREATE TABLE IF NOT EXISTS picnic_orders_meta (
  key TEXT PRIMARY KEY,
  synced_at TEXT NOT NULL
);
"""

ITEM_COLUMNS = ("product_id", "title", "unit_size", "quantity", "price", "list_price", "image")


def _connect():
    conn = auth.connect()
    conn.executescript(SCHEMA)
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _item_values(item: dict[str, Any]) -> tuple:
    return (item.get("id"), item.get("title"), item.get("unitSize"), item.get("quantity"), item.get("price"), item.get("listPrice"), item.get("image"))


def _item_json(row: Any) -> dict[str, Any]:
    return {
        "id": row["product_id"], "title": row["title"], "unitSize": row["unit_size"], "quantity": row["quantity"],
        "price": row["price"], "listPrice": row["list_price"], "image": row["image"],
    }


def synced_at() -> str | None:
    with closing(_connect()) as conn:
        row = conn.execute("SELECT synced_at FROM picnic_orders_meta WHERE key = 'orders'").fetchone()
    return row["synced_at"] if row else None


def is_delivered(status: Any) -> bool:
    return str(status or "").upper() not in NOT_STORED


def _save_order(conn, header: dict[str, Any], items: list[dict[str, Any]] | None) -> None:
    """Bewaar of werk een bestelling bij. `items=None` laat de bewaarde producten staan."""
    now = _now()
    existing = conn.execute("SELECT first_seen, items_synced FROM picnic_orders WHERE order_id = ?", (header["id"],)).fetchone()
    day = header.get("deliveredStart") or header.get("windowStart") or header.get("etaStart")
    conn.execute(
        """
        INSERT INTO picnic_orders (order_id, status, total, delivery_date, slot_start, slot_end, delivered_start, delivered_end, items_synced, first_seen, synced_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(order_id) DO UPDATE SET status = COALESCE(NULLIF(excluded.status, ''), picnic_orders.status),
          total = COALESCE(excluded.total, picnic_orders.total),
          delivery_date = COALESCE(NULLIF(excluded.delivery_date, ''), picnic_orders.delivery_date),
          slot_start = COALESCE(NULLIF(excluded.slot_start, ''), picnic_orders.slot_start),
          slot_end = COALESCE(NULLIF(excluded.slot_end, ''), picnic_orders.slot_end),
          delivered_start = COALESCE(NULLIF(excluded.delivered_start, ''), picnic_orders.delivered_start),
          delivered_end = COALESCE(NULLIF(excluded.delivered_end, ''), picnic_orders.delivered_end),
          items_synced = excluded.items_synced, synced_at = excluded.synced_at
        """,
        (header["id"], header.get("status"), header.get("total"), day, header.get("windowStart"), header.get("windowEnd"),
         header.get("deliveredStart"), header.get("deliveredEnd"),
         1 if items is not None else (existing["items_synced"] if existing else 0),
         existing["first_seen"] if existing else now, now),
    )
    if items is not None:
        conn.execute("DELETE FROM picnic_order_items WHERE order_id = ?", (header["id"],))
        conn.executemany(
            f"INSERT INTO picnic_order_items (order_id, position, {', '.join(ITEM_COLUMNS)}) VALUES (?, ?{', ?' * len(ITEM_COLUMNS)})",
            [(header["id"], position, *_item_values(item)) for position, item in enumerate(items)],
        )


def sync_orders() -> dict[str, Any]:
    """Haal het overzicht van je leveringen op en daarna de producten van een aantal afgeleverde bestellingen.

    Nieuwste eerst: een eerste synchronisatie vult dus eerst de recente bestellingen met producten aan en elke volgende
    de rest. Lukt het ophalen van de producten niet, dan blijft het overzicht staan en melden we `detailError`.
    """
    listed = [d for d in picnic.get_deliveries() if d.get("id")]
    gone = {str(d["id"]) for d in listed if str(d.get("status") or "").upper() in {"CANCELLED", "CANCELED"}}
    orders = [{**d, "id": str(d["id"])} for d in listed if is_delivered(d.get("status"))]
    with closing(_connect()) as conn:
        complete = {row["order_id"] for row in conn.execute("SELECT order_id FROM picnic_orders WHERE items_synced = 1")}
        known = {row["order_id"] for row in conn.execute("SELECT order_id FROM picnic_orders")}
    new = sum(1 for order in orders if order["id"] not in known)
    wanted = [order for order in orders if order["id"] not in complete][:ORDER_DETAILS_PER_SYNC]

    details: dict[str, dict[str, Any]] = {}
    detail_error: str | None = None
    failures = 0
    for order in wanted:
        try:
            details[order["id"]] = picnic.get_delivery(order["id"])
        except picnic.PicnicAuthError:
            raise
        except Exception as exc:  # noqa: BLE001 - de kop bewaren we toch; de producten volgen bij de volgende synchronisatie
            detail_error = type(exc).__name__
            failures += 1
            if not details and failures >= MAX_FIRST_FAILURES:
                break  # er lukt niets: niet nog 20 keer proberen

    with closing(_connect()) as conn, conn:
        for order_id in gone:
            conn.execute("DELETE FROM picnic_order_items WHERE order_id = ?", (order_id,))
            conn.execute("DELETE FROM picnic_orders WHERE order_id = ?", (order_id,))
        for order in orders:
            info = details.get(order["id"])
            header = {**order, **({key: value for key, value in info.items() if key != "items" and value} if info else {}), "id": order["id"]}
            _save_order(conn, header, info["items"] if info else None)
        conn.execute(
            "INSERT INTO picnic_orders_meta (key, synced_at) VALUES ('orders', ?) ON CONFLICT(key) DO UPDATE SET synced_at = excluded.synced_at",
            (_now(),),
        )
        remaining = conn.execute("SELECT COUNT(*) FROM picnic_orders WHERE items_synced = 0").fetchone()[0]
    return {"orders": len(orders), "new": new, "remaining": remaining, **({"detailError": detail_error} if detail_error else {})}


def _order_json(row: Any, count: int | None = None, quantity: int | None = None) -> dict[str, Any]:
    return {
        "id": row["order_id"], "status": row["status"], "total": row["total"], "deliveryDate": row["delivery_date"],
        "windowStart": row["slot_start"], "windowEnd": row["slot_end"],
        "deliveredStart": row["delivered_start"], "deliveredEnd": row["delivered_end"],
        "itemsSynced": bool(row["items_synced"]), "syncedAt": row["synced_at"],
        **({"lineCount": count} if count is not None else {}),
        **({"itemCount": quantity} if quantity is not None else {}),
    }


def list_orders() -> dict[str, Any]:
    with closing(_connect()) as conn:
        rows = conn.execute(
            """
            SELECT o.*, (SELECT COUNT(*) FROM picnic_order_items i WHERE i.order_id = o.order_id) AS line_count,
                   (SELECT COALESCE(SUM(i.quantity), 0) FROM picnic_order_items i WHERE i.order_id = o.order_id) AS item_count
            FROM picnic_orders o
            ORDER BY COALESCE(NULLIF(o.delivery_date, ''), o.first_seen) DESC, o.order_id DESC
            """
        ).fetchall()
    return {"syncedAt": synced_at(), "orders": [_order_json(row, row["line_count"], row["item_count"]) for row in rows]}


def get_order(order_id: Any) -> dict[str, Any] | None:
    with closing(_connect()) as conn:
        row = conn.execute("SELECT * FROM picnic_orders WHERE order_id = ?", (str(order_id),)).fetchone()
        if not row:
            return None
        items = conn.execute("SELECT * FROM picnic_order_items WHERE order_id = ? ORDER BY position", (str(order_id),)).fetchall()
    return {**_order_json(row, len(items), sum(item["quantity"] or 0 for item in items)), "items": [_item_json(item) for item in items]}


def search_purchases(query: Any, limit: int = 50) -> dict[str, Any]:
    """Wanneer kocht je een product? Zoekt in de producten van je bestellingen (alle woorden moeten erin staan)."""
    words = _fold(query).split()
    if not words:
        return {"query": "", "products": [], "occurrences": [], "total": 0}
    occurrences: list[dict[str, Any]] = []
    with closing(_connect()) as conn:
        for row in conn.execute(
            """
            SELECT i.title, i.quantity, o.order_id, o.delivery_date, o.first_seen
            FROM picnic_order_items i JOIN picnic_orders o ON o.order_id = i.order_id
            """
        ):
            if all(word in _fold(row["title"]) for word in words):
                occurrences.append({"product": row["title"], "quantity": row["quantity"],
                                    "date": (row["delivery_date"] or row["first_seen"] or "")[:10], "id": str(row["order_id"])})
        unsearched = conn.execute("SELECT COUNT(*) FROM picnic_orders WHERE items_synced = 0").fetchone()[0]
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
