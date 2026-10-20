"""Lokale kopie van AH favoriete lijstjes.

De lijstjes worden bij AH opgehaald en als snapshot in SQLite bewaard, zodat ze
zichtbaar blijven als de AH-koppeling (tijdelijk) niet werkt. Een synchronisatie
vervangt de snapshot van dat lijstje in een keer; mislukt het ophalen, dan blijft
de vorige snapshot ongemoeid.

Aantallen zijn aan te passen. `default_quantity` is het aantal zoals AH het geeft,
`quantity` het aantal dat de gebruiker wil. Een synchronisatie bewaart de door de
gebruiker aangepaste aantallen (per product) en haalt de rest weer bij AH op.
"""
from __future__ import annotations

import unicodedata
from contextlib import closing
from datetime import datetime, timezone
from typing import Any

from . import ah, auth

SCHEMA = """
CREATE TABLE IF NOT EXISTS ah_lists (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  synced_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ah_list_items (
  list_id TEXT NOT NULL,
  position INTEGER NOT NULL,
  item_id TEXT,
  product_id INTEGER NOT NULL,
  quantity INTEGER NOT NULL DEFAULT 1,
  default_quantity INTEGER,
  title TEXT,
  brand TEXT,
  unit_size TEXT,
  price REAL,
  image TEXT,
  url TEXT,
  is_bonus INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (list_id, position),
  FOREIGN KEY (list_id) REFERENCES ah_lists(id) ON DELETE CASCADE
);
"""


def normalize_id(list_id: Any) -> str:
    return str(list_id or "").strip().upper()


def _connect():
    conn = auth.connect()
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(ah_list_items)")}
    if "default_quantity" not in columns:  # database van voor de aanpasbare aantallen
        conn.execute("ALTER TABLE ah_list_items ADD COLUMN default_quantity INTEGER")
        conn.execute("UPDATE ah_list_items SET default_quantity = quantity")
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _item_row(row: Any) -> dict[str, Any]:
    product_id = row["product_id"]
    has_product = bool(row["title"])
    default = row["default_quantity"] or row["quantity"]
    return {
        "id": row["item_id"],
        "position": row["position"],
        "productId": product_id,
        "url": row["url"] or ah.product_web_url(product_id),
        "quantity": row["quantity"],
        "defaultQuantity": default,
        "edited": row["quantity"] != default,
        "product": {
            "id": product_id,
            "url": row["url"] or ah.product_web_url(product_id),
            "title": row["title"],
            "brand": row["brand"],
            "unitSize": row["unit_size"],
            "price": row["price"] or 0,
            "image": row["image"],
            "isBonus": bool(row["is_bonus"]),
        }
        if has_product
        else None,
    }


def _match_previous(previous: list[Any], items: list[dict[str, Any]]) -> list[Any | None]:
    """Koppel elk nieuw item aan de regel die er al stond: eerst op het id van AH, daarna op het product.

    Zo blijft een regel dezelfde regel als de volgorde verandert of er iets bijkomt, en komt een
    product dat er dubbel in staat niet door elkaar. Wat nergens bij hoort is nieuw (None).
    """
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
        product_id = int(item.get("productId") or 0)
        for row in previous:
            if row["position"] not in used and row["product_id"] == product_id:
                matches[index] = row
                used.add(row["position"])
                break
    return matches


def save_snapshot(live: dict[str, Any]) -> dict[str, Any]:
    """Werk de lokale kopie van een lijstje bij met `live` (uitvoer van ah.get_favorite_list_items).

    - Nieuwe items komen erbij met het aantal van AH, en wat bij AH is verdwenen verdwijnt hier ook.
    - Een aantal dat de gebruiker heeft aangepast blijft staan; AH ververst alleen het aantal van AH
      (`default_quantity`), zodat terugzetten naar AH blijft kloppen.
    - Een aantal dat niet is aangepast volgt AH.
    Het geeft de lijst terug, met onder `sync` wat er is gebeurd (nieuw, verwijderd, behouden).
    """
    list_id = normalize_id(live.get("id"))
    if not list_id:
        raise RuntimeError("AH lijst heeft geen id")
    name = str(live.get("name") or "AH lijst")
    synced_at = _now()
    items = live.get("items") or []

    with closing(_connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")  # een aanpassing van een aantal tijdens het synchroniseren gaat niet verloren
        previous = conn.execute(
            "SELECT position, item_id, product_id, quantity, default_quantity FROM ah_list_items WHERE list_id = ? ORDER BY position",
            (list_id,),
        ).fetchall()
        matches = _match_previous(previous, items)

        rows = []
        kept = 0
        for position, (item, old) in enumerate(zip(items, matches)):
            product = item.get("product") or {}
            product_id = int(item.get("productId") or 0)
            price = product.get("price")
            default = max(1, int(item.get("quantity") or 1))
            quantity = default
            if old is not None and old["quantity"] != (old["default_quantity"] or old["quantity"]):
                quantity = old["quantity"]  # door de gebruiker aangepast
                kept += 1
            rows.append(
                (
                    list_id,
                    position,
                    item.get("id"),
                    product_id,
                    quantity,
                    default,
                    product.get("title"),
                    product.get("brand"),
                    product.get("unitSize"),
                    float(price) if price not in (None, "") else None,
                    product.get("image"),
                    item.get("url") or product.get("url") or ah.product_web_url(product_id),
                    1 if product.get("isBonus") else 0,
                )
            )
        info = {
            "new": sum(1 for old in matches if old is None),
            "removed": len(previous) - sum(1 for old in matches if old is not None),
            "keptQuantities": kept,
        }
        conn.execute(
            """
            INSERT INTO ah_lists (id, name, synced_at) VALUES (?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET name = excluded.name, synced_at = excluded.synced_at
            """,
            (list_id, name, synced_at),
        )
        conn.execute("DELETE FROM ah_list_items WHERE list_id = ?", (list_id,))
        conn.executemany(
            """
            INSERT INTO ah_list_items (
              list_id, position, item_id, product_id, quantity, default_quantity, title, brand,
              unit_size, price, image, url, is_bonus
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
                   (SELECT COUNT(*) FROM ah_list_items i WHERE i.list_id = l.id) AS item_count
            FROM ah_lists l
            ORDER BY LOWER(l.name)
            """
        ).fetchall()
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "syncedAt": row["synced_at"],
            "itemCount": row["item_count"],
        }
        for row in rows
    ]


def get_snapshot(list_id: str) -> dict[str, Any] | None:
    list_id = normalize_id(list_id)
    with closing(_connect()) as conn:
        head = conn.execute("SELECT id, name, synced_at FROM ah_lists WHERE id = ?", (list_id,)).fetchone()
        if not head:
            return None
        rows = conn.execute(
            "SELECT * FROM ah_list_items WHERE list_id = ? ORDER BY position", (list_id,)
        ).fetchall()
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


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def search_items(query: Any, limit: int = 3) -> dict[str, list[str]]:
    """Gesynchroniseerde lijstjes waarin een product de zoekwoorden bevat: {lijst-id: [producttitels]}.

    Alle woorden moeten in dezelfde titel (of merknaam) staan. Alleen lokale kopieen
    worden doorzocht, dus dit werkt ook als AH niet bereikbaar is.
    """
    words = _fold(query).split()
    if not words:
        return {}
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT list_id, title, brand FROM ah_list_items WHERE title IS NOT NULL ORDER BY list_id, position"
        ).fetchall()
    found: dict[str, list[str]] = {}
    for row in rows:
        haystack = _fold(f"{row['title']} {row['brand'] or ''}")
        if all(word in haystack for word in words):
            titles = found.setdefault(row["list_id"], [])
            if len(titles) < limit:
                titles.append(row["title"])
    return found


MAX_QUANTITY = ah.MAX_CART_QUANTITY


def set_quantity(list_id: str, position: int, quantity: Any) -> dict[str, Any] | None:
    """Pas het aantal van een regel in een gesynchroniseerd lijstje aan. None als de regel niet bestaat."""
    try:
        value = int(quantity)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("Aantal moet een geheel getal zijn") from None
    if not 1 <= value <= MAX_QUANTITY:
        raise ValueError(f"Aantal moet tussen 1 en {MAX_QUANTITY} liggen")
    with closing(_connect()) as conn, conn:
        changed = conn.execute(
            "UPDATE ah_list_items SET quantity = ? WHERE list_id = ? AND position = ?",
            (value, normalize_id(list_id), position),
        ).rowcount
    return get_snapshot(list_id) if changed else None


def reset_quantities(list_id: str) -> dict[str, Any] | None:
    """Zet alle aangepaste aantallen terug naar de waarden zoals AH ze gaf."""
    if not get_snapshot(list_id):
        return None
    with closing(_connect()) as conn, conn:
        conn.execute(
            "UPDATE ah_list_items SET quantity = COALESCE(default_quantity, quantity) WHERE list_id = ?",
            (normalize_id(list_id),),
        )
    return get_snapshot(list_id)


def cart_items(list_id: str) -> list[dict[str, Any]]:
    """De regels van een lokaal lijstje zoals ze naar het AH-mandje gaan."""
    snapshot = get_snapshot(list_id)
    if not snapshot:
        return []
    return cart_items_from(snapshot)


def cart_items_from(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "productId": item["productId"],
            "quantity": item["quantity"],
            "title": (item.get("product") or {}).get("title") or "",
        }
        for item in snapshot.get("items") or []
        if item.get("productId")
    ]


def sync_list(list_id: str) -> dict[str, Any]:
    live = ah.get_favorite_list_items(list_id)
    return save_snapshot(live)


def merged_lists() -> dict[str, Any]:
    """Lokale en live lijstjes samengevoegd. Een AH-fout breekt de lokale weergave niet."""
    local = {item["id"]: item for item in list_snapshots()}
    ah_error = None
    try:
        live = ah.get_favorite_lists()
    except Exception as exc:  # noqa: BLE001 - AH is onofficieel; toon de fout, val terug op lokaal
        live = []
        ah_error = str(exc) or "AH lijstjes ophalen mislukt"

    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in live:
        list_id = normalize_id(item.get("id"))
        if not list_id:
            continue
        seen.add(list_id)
        copy = local.get(list_id)
        result.append(
            {
                "id": list_id,
                "name": item.get("name") or (copy or {}).get("name") or "AH lijst",
                "itemCount": item.get("itemCount") or 0,
                "updatedAt": item.get("updatedAt"),
                "synced": bool(copy),
                "syncedAt": copy["syncedAt"] if copy else None,
                "syncedCount": copy["itemCount"] if copy else None,
                "source": "both" if copy else "ah",
            }
        )
    for list_id, copy in local.items():
        if list_id in seen:
            continue
        result.append(
            {
                "id": list_id,
                "name": copy["name"],
                "itemCount": copy["itemCount"],
                "updatedAt": None,
                "synced": True,
                "syncedAt": copy["syncedAt"],
                "syncedCount": copy["itemCount"],
                "source": "local",
            }
        )
    result.sort(key=lambda entry: str(entry["name"]).lower())
    return {"lists": result, "ahError": ah_error}
