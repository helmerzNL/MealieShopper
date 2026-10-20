"""Lokale kopie van AH favoriete lijstjes.

De lijstjes worden bij AH opgehaald en als snapshot in SQLite bewaard, zodat ze
zichtbaar blijven als de AH-koppeling (tijdelijk) niet werkt. Een synchronisatie
vervangt de snapshot van dat lijstje in een keer; mislukt het ophalen, dan blijft
de vorige snapshot ongemoeid.
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
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _item_row(row: Any) -> dict[str, Any]:
    product_id = row["product_id"]
    has_product = bool(row["title"])
    return {
        "id": row["item_id"],
        "productId": product_id,
        "url": row["url"] or ah.product_web_url(product_id),
        "quantity": row["quantity"],
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


def save_snapshot(live: dict[str, Any]) -> dict[str, Any]:
    """Vervang de lokale kopie van een lijstje door `live` (uitvoer van ah.get_favorite_list_items)."""
    list_id = normalize_id(live.get("id"))
    if not list_id:
        raise RuntimeError("AH lijst heeft geen id")
    name = str(live.get("name") or "AH lijst")
    synced_at = _now()

    rows = []
    for position, item in enumerate(live.get("items") or []):
        product = item.get("product") or {}
        product_id = int(item.get("productId") or 0)
        price = product.get("price")
        rows.append(
            (
                list_id,
                position,
                item.get("id"),
                product_id,
                max(1, int(item.get("quantity") or 1)),
                product.get("title"),
                product.get("brand"),
                product.get("unitSize"),
                float(price) if price not in (None, "") else None,
                product.get("image"),
                item.get("url") or product.get("url") or ah.product_web_url(product_id),
                1 if product.get("isBonus") else 0,
            )
        )

    with closing(_connect()) as conn, conn:
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
              list_id, position, item_id, product_id, quantity, title, brand,
              unit_size, price, image, url, is_bonus
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    return get_snapshot(list_id) or {}


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
