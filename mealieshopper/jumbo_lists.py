"""Lokale kopie van het Jumbo-boodschappenlijstje.

Jumbo heeft één lijstje (vrije tekst, soms met een SKU) in plaats van meerdere favorietenlijsten zoals AH.
We bewaren het als snapshot in SQLite en geven het in dezelfde vorm als `ah_lists`, zodat de lijsten-
interface voor beide winkels hetzelfde werkt: één kaart "Jumbo-lijstje" die opent in de detailweergave.

Jumbo kent geen aantallen op het lijstje; `quantity` is dus helemaal lokaal (standaard 1) en gaat mee naar
het mandje. Een synchronisatie bewaart de aangepaste aantallen, net als bij AH. Afgevinkte regels
(`completed`) horen bij wat al gekocht is en komen niet in de kopie.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
from typing import Any

from . import auth, jumbo
from .ah_lists import _fold
from .packs import MAX_CART_QUANTITY

LIST_ID = "JUMBO"
LIST_NAME = "Jumbo-lijstje"

SCHEMA = """
CREATE TABLE IF NOT EXISTS jumbo_lists (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  synced_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jumbo_list_items (
  list_id TEXT NOT NULL,
  position INTEGER NOT NULL,
  item_id TEXT,
  sku TEXT,
  term TEXT NOT NULL,
  quantity INTEGER NOT NULL DEFAULT 1,
  default_quantity INTEGER NOT NULL DEFAULT 1,
  title TEXT,
  unit_size TEXT,
  price REAL,
  image TEXT,
  url TEXT,
  is_bonus INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (list_id, position),
  FOREIGN KEY (list_id) REFERENCES jumbo_lists(id) ON DELETE CASCADE
);
"""


def normalize_id(list_id: Any) -> str:
    return str(list_id or "").strip().upper()


def _connect():
    conn = auth.connect()
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _item_row(row: Any) -> dict[str, Any]:
    default = row["default_quantity"] or row["quantity"]
    return {
        "id": row["item_id"],
        "position": row["position"],
        "productId": row["sku"],
        "url": row["url"],
        "quantity": row["quantity"],
        "defaultQuantity": default,
        "edited": row["quantity"] != default,
        # Zonder SKU of productgegevens tonen we de tekst van het lijstje zelf als titel.
        "product": {
            "id": row["sku"],
            "url": row["url"],
            "title": row["title"] or row["term"],
            "brand": None,
            "unitSize": row["unit_size"],
            "price": row["price"] or 0,
            "image": row["image"],
            "isBonus": bool(row["is_bonus"]),
        },
        "term": row["term"],
    }


def _match_previous(previous: list[Any], items: list[dict[str, Any]]) -> list[Any | None]:
    """Koppel elk nieuw item aan de regel die er al stond: eerst op Jumbo's id, daarna op SKU of tekst."""
    by_id = {row["item_id"]: row for row in previous if row["item_id"]}
    used: set[int] = set()
    matches: list[Any | None] = [None] * len(items)
    for index, item in enumerate(items):
        row = by_id.get(item.get("id")) if item.get("id") else None
        if row is not None and row["position"] not in used:
            matches[index] = row
            used.add(row["position"])
    for index, item in enumerate(items):
        if matches[index] is not None:
            continue
        for row in previous:
            same = (item.get("sku") and row["sku"] == item["sku"]) or _fold(row["term"]) == _fold(item.get("term"))
            if row["position"] not in used and same:
                matches[index] = row
                used.add(row["position"])
                break
    return matches


def fetch_live() -> dict[str, Any]:
    """Haal het lijstje bij Jumbo op en vul titel en prijs aan voor regels met een SKU."""
    live = jumbo.get_shopping_list()
    items = [item for item in live["items"] if not item["completed"]]

    def detail(item: dict[str, Any]) -> dict[str, Any] | None:
        if not item["sku"]:
            return None
        try:
            return jumbo.get_product(item["sku"])
        except Exception:  # noqa: BLE001 - zonder productgegevens tonen we gewoon de tekst van de regel
            return None

    with ThreadPoolExecutor(max_workers=6) as executor:
        details = list(executor.map(detail, items[: jumbo.MAX_LIST_PRODUCTS])) + [None] * max(0, len(items) - jumbo.MAX_LIST_PRODUCTS)
    return {
        "id": LIST_ID,
        "name": LIST_NAME,
        "items": [
            {"id": item["id"], "sku": item["sku"], "term": item["term"], "product": product}
            for item, product in zip(items, details)
        ],
    }


def save_snapshot(live: dict[str, Any]) -> dict[str, Any]:
    """Werk de lokale kopie bij met `live` (uitvoer van `fetch_live`); aangepaste aantallen blijven staan."""
    list_id = normalize_id(live.get("id")) or LIST_ID
    items = live.get("items") or []
    synced_at = _now()
    with closing(_connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")  # een aanpassing van een aantal tijdens het synchroniseren gaat niet verloren
        previous = conn.execute(
            "SELECT position, item_id, sku, term, quantity, default_quantity FROM jumbo_list_items WHERE list_id = ? ORDER BY position",
            (list_id,),
        ).fetchall()
        matches = _match_previous(previous, items)
        rows = []
        kept = 0
        for position, (item, old) in enumerate(zip(items, matches)):
            product = item.get("product") or {}
            quantity = 1
            if old is not None and old["quantity"] != (old["default_quantity"] or old["quantity"]):
                quantity = old["quantity"]  # door de gebruiker aangepast
                kept += 1
            price = product.get("price")
            rows.append(
                (
                    list_id, position, item.get("id"), item.get("sku"), item["term"], quantity, 1,
                    product.get("title"), product.get("unitSize"),
                    float(price) if price not in (None, "") else None,
                    product.get("image"), product.get("url"), 1 if product.get("isBonus") else 0,
                )
            )
        info = {
            "new": sum(1 for old in matches if old is None),
            "removed": len(previous) - sum(1 for old in matches if old is not None),
            "keptQuantities": kept,
        }
        conn.execute(
            """
            INSERT INTO jumbo_lists (id, name, synced_at) VALUES (?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET name = excluded.name, synced_at = excluded.synced_at
            """,
            (list_id, str(live.get("name") or LIST_NAME), synced_at),
        )
        conn.execute("DELETE FROM jumbo_list_items WHERE list_id = ?", (list_id,))
        conn.executemany(
            """
            INSERT INTO jumbo_list_items (
              list_id, position, item_id, sku, term, quantity, default_quantity, title, unit_size, price, image, url, is_bonus
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    return {**(get_snapshot(list_id) or {}), "sync": info}


def list_snapshots() -> list[dict[str, Any]]:
    with closing(_connect()) as conn:
        rows = conn.execute(
            """
            SELECT l.id, l.name, l.synced_at,
                   (SELECT COUNT(*) FROM jumbo_list_items i WHERE i.list_id = l.id) AS item_count
            FROM jumbo_lists l ORDER BY LOWER(l.name)
            """
        ).fetchall()
    return [
        {"id": row["id"], "name": row["name"], "syncedAt": row["synced_at"], "itemCount": row["item_count"]}
        for row in rows
    ]


def get_snapshot(list_id: str) -> dict[str, Any] | None:
    list_id = normalize_id(list_id)
    with closing(_connect()) as conn:
        head = conn.execute("SELECT id, name, synced_at FROM jumbo_lists WHERE id = ?", (list_id,)).fetchone()
        if not head:
            return None
        rows = conn.execute("SELECT * FROM jumbo_list_items WHERE list_id = ? ORDER BY position", (list_id,)).fetchall()
    items = [_item_row(row) for row in rows]
    return {
        "id": head["id"],
        "name": head["name"],
        "totalSize": len(items),
        "items": items,
        "editedCount": sum(1 for item in items if item["edited"]),
        "syncedAt": head["synced_at"],
        "source": "local",
    }


def live_view(live: dict[str, Any]) -> dict[str, Any]:
    """Het lijstje zoals het bij Jumbo staat, in dezelfde vorm als een snapshot maar zonder lokale aantallen."""
    items = [
        {
            "id": item["id"], "position": index, "productId": item["sku"], "url": (item.get("product") or {}).get("url"),
            "quantity": 1, "defaultQuantity": 1, "edited": False,
            "product": {**(item.get("product") or {}), "title": (item.get("product") or {}).get("title") or item["term"]},
            "term": item["term"],
        }
        for index, item in enumerate(live["items"])
    ]
    return {"id": live["id"], "name": live["name"], "totalSize": len(items), "items": items, "editedCount": 0, "syncedAt": None, "source": "jumbo"}


def search_items(query: Any, limit: int = 3) -> dict[str, list[str]]:
    """Gesynchroniseerde lijstjes waarin een regel de zoekwoorden bevat: {lijst-id: [titels]}. Alleen lokaal."""
    words = _fold(query).split()
    if not words:
        return {}
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT list_id, term, title FROM jumbo_list_items ORDER BY list_id, position").fetchall()
    found: dict[str, list[str]] = {}
    for row in rows:
        label = row["title"] or row["term"]
        if all(word in _fold(f"{label} {row['term']}") for word in words):
            titles = found.setdefault(row["list_id"], [])
            if len(titles) < limit:
                titles.append(label)
    return found


MAX_QUANTITY = MAX_CART_QUANTITY


def set_quantity(list_id: str, position: int, quantity: Any) -> dict[str, Any] | None:
    """Pas het aantal van een regel aan. None als de regel niet bestaat."""
    try:
        value = int(quantity)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("Aantal moet een geheel getal zijn") from None
    if not 1 <= value <= MAX_QUANTITY:
        raise ValueError(f"Aantal moet tussen 1 en {MAX_QUANTITY} liggen")
    with closing(_connect()) as conn, conn:
        changed = conn.execute(
            "UPDATE jumbo_list_items SET quantity = ? WHERE list_id = ? AND position = ?",
            (value, normalize_id(list_id), position),
        ).rowcount
    return get_snapshot(list_id) if changed else None


def reset_quantities(list_id: str) -> dict[str, Any] | None:
    if not get_snapshot(list_id):
        return None
    with closing(_connect()) as conn, conn:
        conn.execute(
            "UPDATE jumbo_list_items SET quantity = COALESCE(default_quantity, quantity) WHERE list_id = ?",
            (normalize_id(list_id),),
        )
    return get_snapshot(list_id)


def sync_list(list_id: str = LIST_ID) -> dict[str, Any]:
    return save_snapshot(fetch_live())


def merged_lists() -> dict[str, Any]:
    """Het lijstje, lokaal en live samengevoegd. Een Jumbo-fout breekt de lokale weergave niet."""
    local = {item["id"]: item for item in list_snapshots()}
    store_error = None
    live_count = None
    try:
        live_count = len(
            [item for item in jumbo.get_shopping_list()["items"] if not item["completed"]]
        )
    except Exception:  # noqa: BLE001 - Jumbo is onofficieel; toon dat het niet lukt en val terug op lokaal
        store_error = "Jumbo fout"
    copy = local.get(LIST_ID)
    if live_count is None and copy is None:
        return {"lists": [], "storeError": store_error}
    return {
        "lists": [
            {
                "id": LIST_ID,
                "name": (copy or {}).get("name") or LIST_NAME,
                "itemCount": live_count if live_count is not None else copy["itemCount"],
                "updatedAt": None,
                "synced": bool(copy),
                "syncedAt": copy["syncedAt"] if copy else None,
                "syncedCount": copy["itemCount"] if copy else None,
                "source": "both" if copy and live_count is not None else ("jumbo" if live_count is not None else "local"),
            }
        ],
        "storeError": store_error,
    }
