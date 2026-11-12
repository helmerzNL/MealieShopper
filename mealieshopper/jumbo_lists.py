"""Lokale kopie van de Jumbo-lijstjes.

Jumbo kent twee soorten lijstjes en we bewaren beide als snapshot in SQLite, in dezelfde vorm als `ah_lists`,
zodat de lijsten-interface voor beide winkels hetzelfde werkt (kaarten die openen in de detailweergave):

- **Mijn lijstjes** (je eigen lijstjes en Favorieten): producten met titel, pakmaat, prijs en een aantal,
  zoals AH's favorietenlijstjes. Het id is dat van Jumbo (hoofdlettergevoelig).
- **Het boodschappenlijstje** (`JUMBO`): losse regels, vrije tekst of een SKU, zonder aantallen; `quantity`
  is dan helemaal lokaal (standaard 1). Afgevinkte regels (`completed`) komen niet in de kopie.

Een synchronisatie bewaart de door de gebruiker aangepaste aantallen, net als bij AH.
"""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from . import auth, jumbo, tasks
from .ah_lists import _fold
from .packs import MAX_CART_QUANTITY

LIST_ID = "JUMBO"
LIST_NAME = "Boodschappenlijstje"
DETAIL_WORKERS: int = 6  # zoveel productgegevens tegelijk, zoals de zes threads van voorheen

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
  brand TEXT,
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
    """Het boodschappenlijstje heet altijd `JUMBO`; id's van andere lijstjes blijven zoals Jumbo ze geeft."""
    text = str(list_id or "").strip()
    return LIST_ID if text.upper() == LIST_ID else text


# De migratie van een database van voor de eigen lijstjes (1.9.0), voor `_connect`.
MIGRATE_BRAND: str = "ALTER TABLE jumbo_list_items ADD COLUMN brand TEXT"


@asynccontextmanager
async def _connect() -> AsyncIterator[aiosqlite.Connection]:
    """Een verbinding met foreign keys aan, het schema en de migratie. Sluit zonder commit, zoals `auth.connect`."""
    async with auth.connect() as conn:
        await conn.execute("PRAGMA foreign_keys = ON")
        await auth.ensure_schema(conn, "jumbo_lists", _setup)
        yield conn


async def _setup(conn: aiosqlite.Connection) -> None:
    """Het schema en de migratie, een keer per proces (`auth.ensure_schema`)."""
    await conn.executescript(SCHEMA)
    columns: set[str] = {row["name"] for row in await conn.execute_fetchall("PRAGMA table_info(jumbo_list_items)")}
    if "brand" not in columns:  # database van voor de eigen lijstjes (1.9.0)
        try:
            await conn.execute(MIGRATE_BRAND)
        except sqlite3.OperationalError as exc:  # een andere verbinding was ons net voor
            if "duplicate column" not in str(exc):
                raise
        await conn.commit()


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
            "brand": row["brand"],
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


async def fetch_live(list_id: str = LIST_ID) -> dict[str, Any]:
    """Haal een lijstje bij Jumbo op: het boodschappenlijstje (met titel en prijs voor regels met een SKU) of een eigen lijstje."""
    list_id = normalize_id(list_id)
    if list_id != LIST_ID:
        return await jumbo.get_product_list(list_id)
    live = await jumbo.get_shopping_list()
    items = [item for item in live["items"] if not item["completed"]]

    async def detail(item: dict[str, Any]) -> dict[str, Any] | None:
        if not item["sku"]:
            return None
        try:
            return await jumbo.get_product(item["sku"])
        except Exception:  # noqa: BLE001 - zonder productgegevens tonen we gewoon de tekst van de regel
            return None

    # Hooguit `DETAIL_WORKERS` tegelijk, in de volgorde van de regels, elk met een kopie van de context (verzoek, auditlog).
    # Een fout buiten de `try` van `detail` annuleert de andere aanvragen in plaats van ze na het foutantwoord door te laten lopen.
    fetched: list[dict[str, Any] | None] = await tasks.map_bounded(DETAIL_WORKERS, detail, items[: jumbo.MAX_LIST_PRODUCTS])
    details: list[dict[str, Any] | None] = fetched + [None] * max(0, len(items) - jumbo.MAX_LIST_PRODUCTS)
    return {
        "id": LIST_ID,
        "name": LIST_NAME,
        "items": [{"id": item["id"], "sku": item["sku"], "term": item["term"], "product": product} for item, product in zip(items, details)],
    }


async def save_snapshot(live: dict[str, Any]) -> dict[str, Any]:
    """Werk de lokale kopie bij met `live` (uitvoer van `fetch_live`); aangepaste aantallen blijven staan."""
    list_id = normalize_id(live.get("id")) or LIST_ID
    items = live.get("items") or []
    synced_at = _now()
    async with _connect() as conn:
        await conn.execute("BEGIN IMMEDIATE")  # een aanpassing van een aantal tijdens het synchroniseren gaat niet verloren
        previous: list[Any] = list(
            await conn.execute_fetchall(
                "SELECT position, item_id, sku, term, quantity, default_quantity FROM jumbo_list_items WHERE list_id = ? ORDER BY position",
                (list_id,),
            )
        )
        matches = _match_previous(previous, items)
        rows: list[tuple[Any, ...]] = []
        kept = 0
        for position, (item, old) in enumerate(zip(items, matches)):
            product = item.get("product") or {}
            default = max(1, int(item.get("quantity") or 1))  # eigen lijstjes hebben een aantal, het boodschappenlijstje niet
            quantity = default
            if old is not None and old["quantity"] != (old["default_quantity"] or old["quantity"]):
                quantity = old["quantity"]  # door de gebruiker aangepast
                kept += 1
            price = product.get("price")
            rows.append(
                (
                    list_id,
                    position,
                    item.get("id"),
                    item.get("sku"),
                    item["term"],
                    quantity,
                    default,
                    product.get("title"),
                    product.get("brand"),
                    product.get("unitSize"),
                    float(price) if price not in (None, "") else None,
                    product.get("image"),
                    product.get("url"),
                    1 if product.get("isBonus") else 0,
                )
            )
        info = {
            "new": sum(1 for old in matches if old is None),
            "removed": len(previous) - sum(1 for old in matches if old is not None),
            "keptQuantities": kept,
        }
        await conn.execute(
            """
            INSERT INTO jumbo_lists (id, name, synced_at) VALUES (?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET name = excluded.name, synced_at = excluded.synced_at
            """,
            (list_id, str(live.get("name") or LIST_NAME), synced_at),
        )
        await conn.execute("DELETE FROM jumbo_list_items WHERE list_id = ?", (list_id,))
        await conn.executemany(
            """
            INSERT INTO jumbo_list_items (
              list_id, position, item_id, sku, term, quantity, default_quantity, title, brand, unit_size, price, image, url, is_bonus
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        await conn.commit()
    return {**(await get_snapshot(list_id) or {}), "sync": info}


async def list_snapshots() -> list[dict[str, Any]]:
    async with _connect() as conn:
        rows = await conn.execute_fetchall(
            """
            SELECT l.id, l.name, l.synced_at,
                   (SELECT COUNT(*) FROM jumbo_list_items i WHERE i.list_id = l.id) AS item_count
            FROM jumbo_lists l ORDER BY LOWER(l.name)
            """
        )
    return [{"id": row["id"], "name": row["name"], "syncedAt": row["synced_at"], "itemCount": row["item_count"]} for row in rows]


async def get_snapshot(list_id: str) -> dict[str, Any] | None:
    list_id = normalize_id(list_id)
    async with _connect() as conn:
        head = await auth.fetchone(conn, "SELECT id, name, synced_at FROM jumbo_lists WHERE id = ?", (list_id,))
        if not head:
            return None
        rows = await conn.execute_fetchall("SELECT * FROM jumbo_list_items WHERE list_id = ? ORDER BY position", (list_id,))
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
            "id": item["id"],
            "position": index,
            "productId": item["sku"],
            "url": (item.get("product") or {}).get("url"),
            "quantity": item.get("quantity") or 1,
            "defaultQuantity": item.get("quantity") or 1,
            "edited": False,
            "product": {**(item.get("product") or {}), "title": (item.get("product") or {}).get("title") or item["term"]},
            "term": item["term"],
        }
        for index, item in enumerate(live["items"])
    ]
    return {"id": live["id"], "name": live["name"], "totalSize": len(items), "items": items, "editedCount": 0, "syncedAt": None, "source": "jumbo"}


async def search_items(query: Any, limit: int = 3) -> dict[str, list[str]]:
    """Gesynchroniseerde lijstjes waarin een regel de zoekwoorden bevat: {lijst-id: [titels]}. Alleen lokaal."""
    words = _fold(query).split()
    if not words:
        return {}
    async with _connect() as conn:
        rows = await conn.execute_fetchall("SELECT list_id, term, title FROM jumbo_list_items ORDER BY list_id, position")
    found: dict[str, list[str]] = {}
    for row in rows:
        label = row["title"] or row["term"]
        if all(word in _fold(f"{label} {row['term']}") for word in words):
            titles = found.setdefault(row["list_id"], [])
            if len(titles) < limit:
                titles.append(label)
    return found


MAX_QUANTITY = MAX_CART_QUANTITY


async def set_quantity(list_id: str, position: int, quantity: Any) -> dict[str, Any] | None:
    """Pas het aantal van een regel aan. None als de regel niet bestaat."""
    try:
        value = int(quantity)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("Aantal moet een geheel getal zijn") from None
    if not 1 <= value <= MAX_QUANTITY:
        raise ValueError(f"Aantal moet tussen 1 en {MAX_QUANTITY} liggen")
    async with _connect() as conn:
        cursor = await conn.execute(
            "UPDATE jumbo_list_items SET quantity = ? WHERE list_id = ? AND position = ?",
            (value, normalize_id(list_id), position),
        )
        changed: int = cursor.rowcount
        await conn.commit()
    return await get_snapshot(list_id) if changed else None


async def reset_quantities(list_id: str) -> dict[str, Any] | None:
    if not await get_snapshot(list_id):
        return None
    async with _connect() as conn:
        await conn.execute(
            "UPDATE jumbo_list_items SET quantity = COALESCE(default_quantity, quantity) WHERE list_id = ?",
            (normalize_id(list_id),),
        )
        await conn.commit()
    return await get_snapshot(list_id)


async def sync_list(list_id: str = LIST_ID) -> dict[str, Any]:
    return await save_snapshot(await fetch_live(list_id))


def _entry(list_id: str, name: str, count: int | None, copy: dict[str, Any] | None, kind: str) -> dict[str, Any]:
    live = count is not None
    return {
        "id": list_id,
        "name": (copy or {}).get("name") if copy and not live else name,
        "itemCount": count if live else (copy or {}).get("itemCount", 0),
        "updatedAt": None,
        "synced": bool(copy),
        "syncedAt": copy["syncedAt"] if copy else None,
        "syncedCount": copy["itemCount"] if copy else None,
        "source": "both" if copy and live else ("jumbo" if live else "local"),
        "kind": kind,
    }


async def merged_lists() -> dict[str, Any]:
    """Alle lijstjes, lokaal en live samengevoegd: het boodschappenlijstje, je eigen lijstjes, dan Favorieten.

    Een Jumbo-fout breekt de lokale weergave niet: wat niet live op te halen is, blijft als kopie zichtbaar.
    """
    local = {item["id"]: item for item in await list_snapshots()}
    store_error = None
    shopping_count: int | None = None
    live_lists: list[dict[str, Any]] = []
    try:
        shopping_count = len([item for item in (await jumbo.get_shopping_list())["items"] if not item["completed"]])
    except Exception:  # noqa: BLE001 - Jumbo is onofficieel; toon dat het niet lukt en val terug op lokaal
        store_error = "Jumbo fout"
    try:
        live_lists = await jumbo.get_product_lists()
    except Exception:  # noqa: BLE001
        store_error = "Jumbo fout"

    result: list[dict[str, Any]] = []
    if shopping_count is not None or LIST_ID in local:
        result.append(_entry(LIST_ID, LIST_NAME, shopping_count, local.get(LIST_ID), "shopping"))
    seen = {LIST_ID}
    for item in sorted(live_lists, key=lambda entry: entry["kind"] == "favorites"):  # eigen lijstjes voor Favorieten
        seen.add(item["id"])
        result.append(_entry(item["id"], item["name"], item["itemCount"], local.get(item["id"]), item["kind"]))
    leftovers = sorted((copy for list_id, copy in local.items() if list_id not in seen), key=lambda copy: copy["name"].lower())
    result += [_entry(copy["id"], copy["name"], None, copy, "own") for copy in leftovers]
    return {"lists": result, "storeError": store_error}
