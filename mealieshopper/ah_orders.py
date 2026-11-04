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
import re
import threading
import time
from collections import deque
from contextlib import closing
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, TypeVar

from . import ah, ah_lists, auth, image_cache, price_import

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


def _connect():
    auth.ensure_db()
    conn = auth.connect()
    conn.executescript(SCHEMA)
    # Een database van vóór de bonfoto's heeft die kolom nog niet; `backfill_receipt_images` vult hem 's nachts.
    if "image" not in {row["name"] for row in conn.execute("PRAGMA table_info(ah_receipt_products)")}:
        conn.execute("ALTER TABLE ah_receipt_products ADD COLUMN image TEXT")
    return conn


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
    """Bewaar het AH-mandje: de boodschappenlijst waar het weekmenu in schrijft (niet de actieve bestelling)."""
    save_basket(ah.get_shopping_list())
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


def _closed_orders(complete: set[int]) -> list[dict[str, Any]]:
    """Alle afgeronde bestellingen, nieuwste eerst, bladerend met `offset` (AH geeft er per keer 10 en telt de offset in rijen).

    Het bladeren stopt als een pagina niets nieuws geeft (einde, of AH negeert de offset) en zodra alles op een pagina al is
    bewaard of geannuleerd: oudere bestellingen zijn dan ook al binnen. Een mislukte pagina houdt het gevondene.
    """
    found: list[dict[str, Any]] = []
    seen: set[int] = set()
    offset = 0
    for _ in range(MAX_CLOSED_PAGES):
        try:
            page = ah.get_closed_fulfillments(offset)
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


def sync_orders() -> dict[str, Any]:
    """Haal de open en de afgeronde bestellingen op; wat is afgeleverd bewaren we voor altijd.

    Afgeronde bestellingen komen er pagina voor pagina (nieuwste eerst) uit; een volgende synchronisatie stopt zodra ze
    bij bestellingen komt die al bewaard zijn. Geannuleerde bestellingen bewaren we niet (ze komen ook bij "open" voorbij).
    """
    listed_open = ah.get_open_fulfillments()
    with closing(_connect()) as conn:
        previously_open = [row["order_id"] for row in conn.execute("SELECT order_id FROM ah_orders WHERE kind = 'open'")]
        complete = {row["order_id"] for row in conn.execute("SELECT order_id FROM ah_orders WHERE kind = 'delivered' AND items_synced = 1")}
        incomplete = [row["order_id"] for row in conn.execute("SELECT order_id FROM ah_orders WHERE kind = 'delivered' AND items_synced = 0")]
    closed_orders = _closed_orders(complete)
    cancelled = {order["id"] for order in listed_open + closed_orders if order.get("deliveryStatus") == "CANCELLED"}
    open_orders = [order for order in listed_open if order["id"] not in cancelled]
    open_ids = {order["id"] for order in open_orders}
    delivered = [order for order in closed_orders if order["id"] not in cancelled and order["id"] not in open_ids]

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
    image_cache.prefetch_async(limit=image_cache.PREFETCH_AFTER_SYNC)  # de foto's van de nieuwe bestellingen alvast in de cache
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
    try:
        products = resolve_receipt_products(now)
    except Exception:  # noqa: BLE001 - het koppelen van producten mag het ophalen van de bonnen nooit laten mislukken
        products = {"found": 0, "missing": 0, "failed": 0, "searches": 0}
    image_cache.prefetch_async(limit=image_cache.PREFETCH_AFTER_SYNC)  # de foto's van de gekoppelde producten alvast in de cache
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
RECEIPT_IMAGES_BATCHES = 8  # en zoveel verzoeken per nachtelijke ronde
_MISSING_RECEIPT_PRODUCT = """
INSERT INTO ah_receipt_products (receipt_product_id, product_id, title, checked_at) VALUES (?, NULL, NULL, ?)
ON CONFLICT(receipt_product_id) DO UPDATE SET checked_at = excluded.checked_at WHERE product_id IS NULL
"""


class _HourlyBudgetSpent(Exception):
    """Het aantal zoekopdrachten van dit uur is op (`RECEIPT_PRODUCTS_PER_HOUR`)."""


# Een pauze en een uurbudget voor het hele proces: de nachtelijke taak, een bon-sync en het zoeken in de Prijsgeschiedenis
# delen ze, zodat AH nooit meer dan een zoekopdracht per `RECEIPT_PRODUCTS_PAUSE_SECONDS` van ons krijgt.
_PACE_LOCK = threading.Lock()
_PACE = {"last": float("-inf"), "recent": deque()}


def _polite(call: Callable[[], T]) -> T:
    """Een verzoek aan AH binnen het tempo en het uurbudget van het koppelen (de zoekopdrachten en het ophalen van foto's samen)."""
    with _PACE_LOCK:
        now = time.monotonic()
        recent = _PACE["recent"]
        while recent and now - recent[0] > 3600:
            recent.popleft()
        if len(recent) >= RECEIPT_PRODUCTS_PER_HOUR:
            raise _HourlyBudgetSpent()
        wait = _PACE["last"] + RECEIPT_PRODUCTS_PAUSE_SECONDS - now
        if wait > 0:
            time.sleep(wait)
        _PACE["last"] = time.monotonic()
        recent.append(_PACE["last"])
        return call()


def _polite_search(query: str) -> list[tuple[int, int, str, str]]:
    return _polite(lambda: ah.search_hq_ids(query))


def _receipt_queries(name: str) -> list[str]:
    """De zoekopdrachten voor een naam op de bon: de naam zelf en, als die uit meer woorden bestaat, het langste woord. Een bon
    kort af ("RB PICOLIENT" is Red Band Picolient); "PICOLIENT" alleen vindt hem wel."""
    text = " ".join(str(name).split())
    words = re.findall(r"[^\W\d_]{4,}", text)
    longest = max(words, key=len) if words else ""
    return [text] + ([longest] if longest and longest.casefold() != text.casefold() else [])


def _todo_by_name(conn, now: str) -> list[dict[str, Any]]:
    """De artikelnummers die nog niet gekoppeld zijn, per naam op de bon (een naam als "REDBAND" hoort bij meer artikelnummers en
    kost dan een zoekopdracht), de vaakst gekochte naam eerst."""
    recheck = (datetime.fromisoformat(now) - timedelta(days=RECEIPT_PRODUCTS_RECHECK_DAYS)).isoformat(timespec="seconds")
    rows = conn.execute(
        """
        SELECT l.product_id AS id, MAX(l.name) AS name, COUNT(*) AS bought FROM ah_receipt_items l
        LEFT JOIN ah_receipt_products p ON p.receipt_product_id = l.product_id
        WHERE l.product_id IS NOT NULL AND l.product_id != 0 AND COALESCE(l.name, '') != ''
          AND (p.receipt_product_id IS NULL OR (p.product_id IS NULL AND p.checked_at < ?))
        GROUP BY l.product_id
        """,
        (recheck,),
    ).fetchall()
    groups: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = _name_key(row["name"])
        group = groups.setdefault(key, {"key": key, "name": " ".join(str(row["name"]).split()), "ids": [], "bought": 0})
        group["ids"].append(int(row["id"]))
        group["bought"] += row["bought"]
    return sorted(groups.values(), key=lambda group: (-group["bought"], group["name"]))


def _linked(conn, ids: list[int]) -> set[int]:
    marks = ",".join("?" * len(ids))
    return {row[0] for row in conn.execute(f"SELECT receipt_product_id FROM ah_receipt_products WHERE product_id IS NOT NULL AND receipt_product_id IN ({marks})", ids)}


def _name_key(name: Any) -> str:
    return "".join(str(name).casefold().split())


def resolve_receipt_products(now: str | None = None, searches: int = RECEIPT_PRODUCTS_SEARCHES,
                             deadline_seconds: float = RECEIPT_PRODUCTS_DEADLINE_SECONDS, only: set[str] | None = None) -> dict[str, Any]:
    """Koppel de artikelnummers op de bonnen aan producten van de webwinkel.

    Per naam op de bon zoeken we bij AH en koppelen een artikelnummer alleen aan het resultaat met precies dat artikelnummer (`hqId`):
    geen gok op naam. Vindt de naam niets, dan nog een keer met het langste woord. Elk ander resultaat met een artikelnummer bewaren
    we ook, dat scheelt latere zoekopdrachten. Hooguit `searches` zoekopdrachten, met een pauze ertussen en hooguit `deadline_seconds`
    lang. Bij een 403 of 429 van AH stopt het en wacht het een paar uur (`SETTING_RECEIPT_PRODUCTS_PAUSED`)."""
    now = now or _now()
    result: dict[str, Any] = {"found": 0, "missing": 0, "failed": 0, "searches": 0}
    paused = auth.get_setting(SETTING_RECEIPT_PRODUCTS_PAUSED, "")
    if paused and paused > now:
        return {**result, "pausedUntil": paused}
    if auth.get_setting(SETTING_RECEIPT_PRODUCTS_VERSION, "") != RECEIPT_PRODUCTS_VERSION:
        with closing(_connect()) as conn, conn:
            conn.execute("DELETE FROM ah_receipt_products WHERE product_id IS NULL")
        auth.set_setting(SETTING_RECEIPT_PRODUCTS_VERSION, RECEIPT_PRODUCTS_VERSION)
    with closing(_connect()) as conn:
        groups = [group for group in _todo_by_name(conn, now) if only is None or group["key"] in only]
    deadline = time.monotonic() + deadline_seconds
    for group in groups:
        with closing(_connect()) as conn:
            left = [product_id for product_id in group["ids"] if product_id not in _linked(conn, group["ids"])]
        result["found"] += len(group["ids"]) - len(left)  # gevonden bij de zoekopdracht van een andere naam
        complete = True
        for query in _receipt_queries(group["name"]) if left else []:
            if result["searches"] >= searches or time.monotonic() > deadline:
                return result
            try:
                hits = _polite_search(query)
            except _HourlyBudgetSpent:
                return {**result, "limited": True}
            except ah.AhHttpError as exc:
                result["searches"] += 1
                if exc.status in (403, 429):  # firewall of te veel verzoeken: niet aandringen
                    until = (datetime.fromisoformat(now) + timedelta(hours=RECEIPT_PRODUCTS_BACKOFF_HOURS)).isoformat(timespec="seconds")
                    auth.set_setting(SETTING_RECEIPT_PRODUCTS_PAUSED, until)
                    return {**result, "failed": result["failed"] + 1, "pausedUntil": until}
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
            with closing(_connect()) as conn, conn:
                conn.executemany(_UPSERT_RECEIPT_PRODUCT, [(hq_id, webshop_id, title, now, image) for hq_id, webshop_id, title, image in hits])
            result["found"] += sum(1 for product_id in left if product_id in found)
            left = [product_id for product_id in left if product_id not in found]
            if not left:
                break
        if left and complete:
            with closing(_connect()) as conn, conn:
                conn.executemany(_MISSING_RECEIPT_PRODUCT, [(product_id, now) for product_id in left])
            result["missing"] += len(left)
    return result


def resolve_receipt_names(names: list[str], searches: int = RECEIPT_PRODUCTS_LOOKUP_SEARCHES) -> dict[str, Any]:
    """Koppel de artikelnummers van deze namen op de bon meteen, bijvoorbeeld omdat je ze zoekt in de Prijsgeschiedenis ("REDBAND")
    of een bon opent (`get_receipt`).
    Hooguit `searches` zoekopdrachten, binnen dezelfde pauze en hetzelfde uurbudget. Een naam die net is opgezocht, slaat het over
    (die wacht `RECEIPT_PRODUCTS_RECHECK_DAYS`)."""
    keys = {_name_key(name) for name in names if str(name).strip()}
    if not keys:
        return {"found": 0, "missing": 0, "failed": 0, "searches": 0}
    return resolve_receipt_products(searches=searches, deadline_seconds=10, only=keys)


def backfill_receipt_images(now: str | None = None, batches: int = RECEIPT_IMAGES_BATCHES) -> dict[str, Any]:
    """De foto bij koppelingen van vóór de bonfoto's (`image` NULL): per `RECEIPT_IMAGES_BATCH` webwinkel-id's een verzoek aan AH,
    hooguit `batches` verzoeken, binnen het tempo, het uurbudget en de pauze van het koppelen. Een id dat AH niet teruggeeft krijgt
    "" (geen foto), zodat het niet elke nacht opnieuw gevraagd wordt."""
    now = now or _now()
    result: dict[str, Any] = {"products": 0, "requests": 0, "failed": 0}
    paused = auth.get_setting(SETTING_RECEIPT_PRODUCTS_PAUSED, "")
    if paused and paused > now:
        return {**result, "pausedUntil": paused}
    with closing(_connect()) as conn:
        todo = [int(row[0]) for row in conn.execute(
            "SELECT DISTINCT product_id FROM ah_receipt_products WHERE image IS NULL AND product_id IS NOT NULL ORDER BY checked_at DESC LIMIT ?",
            (RECEIPT_IMAGES_BATCH * batches,),
        )]
    for start in range(0, len(todo), RECEIPT_IMAGES_BATCH):
        ids = todo[start : start + RECEIPT_IMAGES_BATCH]
        try:
            products = _polite(lambda: ah.get_products_by_ids(ids))
        except _HourlyBudgetSpent:
            return {**result, "limited": True}
        except ah.AhHttpError as exc:
            result["requests"] += 1
            result["failed"] += 1
            if exc.status in (403, 429):
                until = (datetime.fromisoformat(now) + timedelta(hours=RECEIPT_PRODUCTS_BACKOFF_HOURS)).isoformat(timespec="seconds")
                auth.set_setting(SETTING_RECEIPT_PRODUCTS_PAUSED, until)
                return {**result, "pausedUntil": until}
            return result
        except Exception:  # noqa: BLE001 - de volgende nacht opnieuw
            result["requests"] += 1
            result["failed"] += 1
            return result
        result["requests"] += 1
        images = {int(product["id"]): str(product.get("image") or "") for product in products if product.get("id") is not None}
        with closing(_connect()) as conn, conn:
            for product_id in ids:
                conn.execute(
                    "UPDATE ah_receipt_products SET image = ?, title = COALESCE(NULLIF(title, ''), ?) WHERE product_id = ? AND image IS NULL",
                    (images.get(product_id, ""), next((p.get("title") for p in products if p.get("id") == product_id), None), product_id),
                )
        result["products"] += sum(1 for product_id in ids if images.get(product_id))  # met een foto
    return result


def backfill_receipt_images_nightly() -> dict[str, Any] | None:
    """Voor de nachtelijke taak: foto's bij oude koppelingen, als er iets te doen is. Een fout laat de taak doorgaan."""
    try:
        with closing(_connect()) as conn:
            if not conn.execute("SELECT 1 FROM ah_receipt_products WHERE image IS NULL AND product_id IS NOT NULL LIMIT 1").fetchone():
                return None
        return backfill_receipt_images()
    except Exception:  # noqa: BLE001 - de foto's zijn een extraatje
        return None


def resolve_receipt_products_nightly() -> dict[str, Any] | None:
    """Voor de nachtelijke taak: wat meer artikelnummers koppelen, als er bonnen zijn. Een fout laat de taak doorgaan."""
    try:
        with closing(_connect()) as conn:
            if not conn.execute("SELECT 1 FROM ah_receipt_items LIMIT 1").fetchone():
                return None
        return resolve_receipt_products(searches=RECEIPT_PRODUCTS_SEARCHES_NIGHTLY, deadline_seconds=RECEIPT_PRODUCTS_DEADLINE_NIGHTLY)
    except Exception:  # noqa: BLE001 - het koppelen is een extraatje bij de aanbiedingen
        return None


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


def _receipt_lines(conn, receipt_id: str) -> list[dict[str, Any]]:
    """De regels van een bon, elk met het webwinkelproduct (`product`: id en titel) als het artikelnummer al gekoppeld is, anders None."""
    rows = conn.execute(
        """
        SELECT l.*, p.product_id AS webshop_id, p.title AS webshop_title, p.image AS webshop_image FROM ah_receipt_items l
        LEFT JOIN ah_receipt_products p ON p.receipt_product_id = l.product_id
        WHERE l.receipt_id = ? ORDER BY l.position
        """,
        (receipt_id,),
    ).fetchall()
    return [
        {
            "productId": row["product_id"], "name": row["name"], "quantity": row["quantity"], "price": row["price"], "amount": row["amount"],
            "product": {"id": row["webshop_id"], "title": row["webshop_title"], "image": row["webshop_image"] or ""} if row["webshop_id"] else None,
        }
        for row in rows
    ]


def get_receipt(receipt_id: str, lookup: Callable[[list[str]], Any] | None = None) -> dict[str, Any] | None:
    """Een bon met zijn regels. Met `lookup` (bij het openen van de bon) worden de regels die nog bij geen webwinkelproduct horen
    eerst bij AH opgezocht (`resolve_receipt_names`, hooguit `RECEIPT_PRODUCTS_OPEN_SEARCHES` zoekopdrachten); het resultaat staat
    in `products`, of None als het opzoeken zelf misging: de bon komt er dan gewoon zonder koppelingen."""
    receipt_id = str(receipt_id)
    with closing(_connect()) as conn:
        row = conn.execute("SELECT * FROM ah_receipts WHERE receipt_id = ?", (receipt_id,)).fetchone()
        if not row:
            return None
        lines = _receipt_lines(conn, receipt_id)
    result: dict[str, Any] = {}
    if lookup:
        unlinked = [line["name"] for line in lines if line["product"] is None and line["productId"]]
        try:
            result["products"] = lookup(unlinked) if unlinked else {"found": 0, "missing": 0, "failed": 0, "searches": 0}
        except Exception:  # noqa: BLE001 - de koppeling is een extraatje; de bon zelf is wat je wilde zien
            result["products"] = None
        if unlinked:
            with closing(_connect()) as conn:
                lines = _receipt_lines(conn, receipt_id)
    return {
        **_receipt_json(row, len(lines)),
        "items": lines,
        "discounts": json.loads(row["discounts"] or "[]"),
        "payments": json.loads(row["payments"] or "[]"),
        **result,
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
        for row in conn.execute(
            "SELECT l.name, l.quantity, l.amount, r.receipt_id, r.date_time FROM ah_receipt_items l JOIN ah_receipts r ON r.receipt_id = l.receipt_id"
        ):
            if all(word in ah_lists._fold(row["name"]) for word in words):
                occurrences.append({
                    "source": "kassabon", "product": row["name"], "brand": None, "quantity": row["quantity"],
                    "amount": row["amount"], "date": (row["date_time"] or "")[:10], "id": row["receipt_id"], "open": False,
                })
        occurrences += price_import.search_lines(conn, "ah", words, ah_lists._fold)  # oude bestellingen uit je bevestigingsmails
        # Bonnen en bestellingen waarvan de regels nog niet zijn opgehaald: die kunnen nog niet worden doorzocht.
        unsearched = conn.execute(
            "SELECT (SELECT COUNT(*) FROM ah_receipts WHERE details_synced = 0) + (SELECT COUNT(*) FROM ah_orders WHERE items_synced = 0)"
        ).fetchone()[0]
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
