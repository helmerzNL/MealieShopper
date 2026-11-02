"""Prijsgeschiedenis van producten: wanneer iets in de aanbieding was, wat het normaal kostte en wat je ervoor betaalde (#178).

Drie bronnen, per winkel en product samengevoegd:

- **aanbiedingen**: `offer_history` (zie `offers._record_history`), elke periode met de actieprijs, de prijs ervoor en het label;
- **normale prijs**: `price_observations`, één prijs per product per dag. Die wordt bijgehouden waar MealieShopper een product bij
  de winkel opzoekt (de controlelijst van het weekmenu, *Zoek een ander product*, het zoeken per winkel en de MCP-tools). Daarnaast
  telt de prijs vóór de actie van een aanbieding mee, en de normale prijs die in een bestelling staat;
- **wat je betaalde**: je bestellingen en kassabonnen (AH, Jumbo, Picnic en Crisp), gelezen uit de kopie die de app daar al van
  bewaart. Zo staat alles wat je ooit via de app ophaalde meteen in de geschiedenis; een aparte import is niet nodig.

Lezen vraagt nooit iets aan een winkel. Het bijhouden laat nooit een zoekopdracht mislukken: een fout wordt gelogd en verder genegeerd.

Daarbovenop (deel 2 van #178): de **prijs per kilo, liter of stuk** (`unit_price`), een **oordeel bij elke aanbieding**
(`annotate`: is de actieprijs lager dan de gewone prijs van de laatste 30 dagen, hoe vaak was het product dit jaar in de aanbieding,
en is een andere verpakking van hetzelfde product per kilo goedkoper) en **verpakkingen vergelijken** (`compare`: één zoekopdracht
bij de winkel naar hetzelfde product in andere maten). Ideeën en testgevallen voor de prijs per eenheid komen uit het userscript
*Albert Heijn kortingspercentages en prijs per kg/ml/etc.* van wolph (BSD-3-Clause); de code is eigen werk.
"""
from __future__ import annotations

import logging
import re
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Iterable

from . import ah, ah_lists, deals, offers, packs
from .i18n import tr

log = logging.getLogger(__name__)

STORES = offers.STORES
MAX_LIMIT = 200

SCHEMA = """
-- De normale prijs van een product, één per winkel, product en dag (de laatste die we die dag zagen). `source` zegt waar hij
-- vandaan kwam: `search` (een zoekopdracht bij de winkel).
CREATE TABLE IF NOT EXISTS price_observations (
  store TEXT NOT NULL,
  product_id TEXT NOT NULL,
  day TEXT NOT NULL,
  source TEXT NOT NULL,
  title TEXT NOT NULL,
  brand TEXT,
  unit_size TEXT,
  image TEXT,
  url TEXT,
  price REAL NOT NULL,
  seen_at TEXT NOT NULL,
  PRIMARY KEY (store, product_id, day)
);
"""

# Wat je kocht, per winkel: een of meer bronnen die elk regels geven in dezelfde vorm. `paid` en `regular` zijn prijzen per stuk.
# Picnic en Crisp bewaren het bedrag van de hele regel, dus daar delen we door het aantal. Een bestelling die nog niet geleverd is
# telt niet mee (AH en Jumbo: `kind = 'delivered'`; Picnic en Crisp: een geannuleerde of toekomstige levering valt weg).
_DAY = "COALESCE(NULLIF(substr(o.delivery_date, 1, 10), ''), substr(o.first_seen, 1, 10))"
_PURCHASES: dict[str, tuple[tuple[tuple[str, ...], str], ...]] = {
    "ah": (
        (
            ("ah_order_items", "ah_orders"),
            f"""
            SELECT CAST(i.product_id AS TEXT) AS product_id, i.title AS title, i.brand AS brand, i.unit_size AS unit_size,
                   i.image AS image, {_DAY} AS day, i.quantity AS quantity, i.price AS paid,
                   CASE WHEN COALESCE(i.bonus, 0) = 0 THEN i.price ELSE i.price_before_bonus END AS regular,
                   'order' AS source, CAST(o.order_id AS TEXT) AS ref
            FROM ah_order_items i JOIN ah_orders o ON o.order_id = i.order_id
            WHERE o.kind = 'delivered' AND i.product_id IS NOT NULL AND i.product_id != 0
            """,
        ),
    ),
    "jumbo": (
        (
            ("jumbo_order_items", "jumbo_orders"),
            f"""
            SELECT i.product_id AS product_id, i.title AS title, i.brand AS brand, i.unit_size AS unit_size, i.image AS image,
                   {_DAY} AS day, i.quantity AS quantity, i.price AS paid,
                   CASE WHEN COALESCE(i.bonus, 0) = 0 THEN i.price ELSE i.price_before_bonus END AS regular,
                   'order' AS source, o.order_id AS ref
            FROM jumbo_order_items i JOIN jumbo_orders o ON o.order_id = i.order_id
            WHERE o.kind = 'delivered' AND i.product_id IS NOT NULL
            """,
        ),
    ),
    **{
        store: (
            (
                (f"{store}_order_items", f"{store}_orders"),
                f"""
                SELECT i.product_id AS product_id, i.title AS title, NULL AS brand, i.unit_size AS unit_size, i.image AS image,
                       {_DAY} AS day, i.quantity AS quantity,
                       CASE WHEN i.quantity > 0 THEN round(i.price / i.quantity, 2) END AS paid,
                       CASE WHEN i.quantity > 0 THEN round(COALESCE(i.list_price, i.price) / i.quantity, 2) END AS regular,
                       'order' AS source, o.order_id AS ref
                FROM {store}_order_items i JOIN {store}_orders o ON o.order_id = i.order_id
                WHERE i.product_id IS NOT NULL AND UPPER(COALESCE(o.status, '')) NOT LIKE '%CANCEL%'
                """,
            ),
        )
        for store in ("picnic", "crisp")
    },
}

# De kassabonnen van AH, apart van de bestellingen: het id op een bon is niet altijd bruikbaar (soms leeg of 0, en het is niet zeker
# dat het het id van de webwinkel is). `_receipt_rows` koppelt een regel daarom ook op naam aan een bekend product. De prijs is die
# op de bon; een korting onderaan de bon (zoals de Bonus) is daar niet van afgetrokken.
_RECEIPTS_SQL = """
SELECT CASE WHEN l.product_id IS NULL OR l.product_id = 0 THEN NULL ELSE CAST(l.product_id AS TEXT) END AS receipt_product_id,
       l.name AS title, NULL AS brand, NULL AS unit_size, NULL AS image, substr(r.date_time, 1, 10) AS day, l.quantity AS quantity,
       COALESCE(l.price, CASE WHEN l.quantity > 0 THEN round(l.amount / l.quantity, 2) END) AS paid,
       NULL AS regular, 'receipt' AS source, r.receipt_id AS ref
FROM ah_receipt_items l JOIN ah_receipts r ON r.receipt_id = l.receipt_id
WHERE COALESCE(l.name, '') != ''
"""

# Hoeveel bestellingen en kassabonnen er wel zijn, maar waarvan de producten nog niet zijn opgehaald (een synchronisatie haalt er per
# keer een deel op). Die aankopen ontbreken dus nog in de geschiedenis.
_UNSYNCED: dict[str, tuple[tuple[str, str, str], ...]] = {
    "ah": (
        ("orders", "ah_orders", "SELECT COUNT(*) FROM ah_orders WHERE kind = 'delivered' AND items_synced = 0"),
        ("receipts", "ah_receipts", "SELECT COUNT(*) FROM ah_receipts WHERE details_synced = 0"),
    ),
    "jumbo": (("orders", "jumbo_orders", "SELECT COUNT(*) FROM jumbo_orders WHERE kind = 'delivered' AND items_synced = 0"),),
    **{
        store: (("orders", f"{store}_orders",
                 f"SELECT COUNT(*) FROM {store}_orders WHERE items_synced = 0 AND UPPER(COALESCE(status, '')) NOT LIKE '%CANCEL%'"),)
        for store in ("picnic", "crisp")
    },
}

# Welke bron voor de normale prijs voorgaat als er op één dag meer zijn: wat de winkel bij het zoeken zei, dan een bestelling,
# dan de prijs vóór een actie.
_REGULAR_RANK = {"search": 0, "order": 1, "offer": 2}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect():
    conn = offers._connect()  # maakt ook `offer_history` aan (en de rest van de aanbiedingen)
    conn.executescript(SCHEMA)
    return conn


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if value > 0 else None


def _product_id(product: dict[str, Any]) -> str:
    value = product.get("id") or product.get("webshopId") or product.get("productId")
    return str(value).strip() if value not in (None, "") else ""


def regular_price(product: dict[str, Any]) -> float | None:
    """De normale prijs van een product uit een zoekresultaat van een winkel, of None als die er niet zeker uit volgt.

    Zonder aanbieding is dat de prijs. Met een aanbieding is het de prijs vóór de actie (`promo.priceBefore`, `priceBefore`,
    `priceBeforeBonus` of `listPrice`); geeft de winkel die niet, dan weten we de normale prijs niet en slaan we het product over.
    """
    promo = product.get("promo") if isinstance(product.get("promo"), dict) else {}
    for value in (promo.get("priceBefore"), product.get("priceBefore"), product.get("priceBeforeBonus"), product.get("listPrice")):
        if _number(value) is not None:
            return _number(value)
    if product.get("isBonus") or product.get("bonus") or promo:
        return None
    price = product.get("price")
    return _number(price.get("now") if isinstance(price, dict) else price)


def _image(product: dict[str, Any]) -> str | None:
    if product.get("image"):
        return str(product["image"])
    images = product.get("images")
    first = images[0] if isinstance(images, list) and images else None
    return str(first["url"]) if isinstance(first, dict) and first.get("url") else None


def _url(store: str, product_id: str, given: Any = None) -> str:
    if given:
        return str(given)
    return ah.product_web_url(product_id) if store == "ah" else ""


def record(store: str, products: Iterable[Any], source: str = "search", now: str | None = None) -> int:
    """Bewaar de normale prijs van deze producten voor vandaag. Geeft het aantal bewaarde prijzen; een fout geeft 0 en wordt gelogd."""
    try:
        if store not in STORES:
            return 0
        stamp = now or _now()
        rows = []
        for product in products or []:
            if not isinstance(product, dict):
                continue
            product_id, title = _product_id(product), str(product.get("title") or "").strip()
            price = regular_price(product)
            if not product_id or not title or price is None:
                continue
            rows.append((store, product_id, stamp[:10], source, title, product.get("brand") or None, product.get("unitSize") or None,
                         _image(product), product.get("url") or None, price, stamp))
        if not rows:
            return 0
        with closing(_connect()) as conn, conn:
            conn.executemany(
                """
                INSERT INTO price_observations (store, product_id, day, source, title, brand, unit_size, image, url, price, seen_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (store, product_id, day) DO UPDATE SET
                  source = excluded.source, title = excluded.title, brand = COALESCE(excluded.brand, brand),
                  unit_size = COALESCE(excluded.unit_size, unit_size), image = COALESCE(excluded.image, image),
                  url = COALESCE(excluded.url, url), price = excluded.price, seen_at = excluded.seen_at
                """,
                rows,
            )
        return len(rows)
    except Exception:  # noqa: BLE001 - het bijhouden mag een zoekopdracht nooit laten mislukken
        log.warning("Prijzen bijhouden mislukt voor %s", store, exc_info=True)
        return 0


def recorded(store: str, search: Callable[..., list[dict[str, Any]]]) -> Callable[..., list[dict[str, Any]]]:
    """`search(query, limit)` van een winkel, die daarna de normale prijs van de gevonden producten bewaart."""

    def wrapper(query: Any, limit: int = 5) -> list[dict[str, Any]]:
        products = search(query, limit)
        record(store, products)
        return products

    return wrapper


# --- lezen ---------------------------------------------------------------------------------------------------------


def _tables(conn) -> set[str]:
    return {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _purchase_sources(conn, store: str) -> list[str]:
    """De SQL van de aankoopbronnen van een winkel waarvan de tabellen bestaan (een winkel die je nooit koppelde heeft ze niet)."""
    tables = _tables(conn)
    return [sql for needed, sql in _PURCHASES.get(store, ()) if set(needed) <= tables]


def _fold_name(value: Any) -> str:
    return " ".join(ah_lists._fold(value).split())


def _name_index(conn, store: str) -> dict[str, str]:
    """Naam (gevouwen: kleine letters, zonder accenten) -> product-id, uit de aanbiedingen, de bewaarde prijzen en de bestellingen."""
    index: dict[str, str] = {}
    sources = [
        ("SELECT product_id, title FROM offer_history WHERE store = ? GROUP BY product_id, title", (store,)),
        ("SELECT product_id, title FROM price_observations WHERE store = ? GROUP BY product_id, title", (store,)),
    ] + [(f"SELECT product_id, title FROM ({sql}) GROUP BY product_id, title", ()) for sql in _purchase_sources(conn, store)]
    for sql, args in sources:
        for row in conn.execute(sql, args):
            if row["product_id"] and row["title"]:
                index.setdefault(_fold_name(row["title"]), str(row["product_id"]))
    return index


def _receipt_rows(conn, store: str, today: str) -> list[dict[str, Any]]:
    """De regels van de kassabonnen (alleen AH), elk met het product waar ze bij horen in `product_id`.

    Is het id op de bon bekend uit de aanbiedingen, de bewaarde prijzen of de bestellingen, dan is het dat product. Anders telt de naam:
    "AH RUNDERGEHAKT" hoort bij "AH Rundergehakt". Past de naam ook nergens, dan is het id van de bon het product, of, zonder id, de
    naam zelf (`bon:ah rundergehakt`), zodat de aankoop altijd te vinden is.
    """
    if store != "ah" or not {"ah_receipt_items", "ah_receipts"} <= _tables(conn):
        return []
    rows = conn.execute(f"SELECT * FROM ({_RECEIPTS_SQL}) WHERE day <= ?", (today,)).fetchall()
    if not rows:
        return []
    index = _name_index(conn, store)
    known = set(index.values())
    found = []
    for row in rows:
        own, name = row["receipt_product_id"], _fold_name(row["title"])
        key = own if own and own in known else (index.get(name) or own or f"bon:{name}")
        found.append({**dict(row), "product_id": key})
    return found


def _unsynced(conn, store: str) -> dict[str, int]:
    tables = _tables(conn)
    counts = {"orders": 0, "receipts": 0}
    for kind, table, sql in _UNSYNCED.get(store, ()):
        if table in tables:
            counts[kind] += conn.execute(sql).fetchone()[0]
    return counts


def _limit(value: Any) -> int:
    try:
        return max(1, min(MAX_LIMIT, int(value)))
    except (TypeError, ValueError):
        return 50


class _Query:
    """Een zoekterm voor de prijsgeschiedenis: losse woorden en eventueel een maat.

    Elk woord moet in de naam (of het merk) van het product staan, ook als deel van een woord: "gehakt" vindt "AH Rundergehakt". Een
    maat in de zoekterm ("500 gram", "500g", "0,5 kg", "2 x 250 g") wordt vergeleken met de verpakking: "Gehakt 500 gram" vindt gehakt
    van 500 gram en niet dat van 300 gram. Een product waarvan de verpakking niet bekend is (zoals een regel van een kassabon) blijft erin.
    """

    def __init__(self, query: Any) -> None:
        text = _fold_name(query)
        self.size: tuple[float, str] | None = None
        match = packs._PACK_SIZE_RE.search(text)
        if match:
            self.size = packs.parse_pack_size(match.group(0))
            text = f"{text[:match.start()]} {text[match.end():]}"
        self.words = re.findall(r"[a-z0-9]+", text)

    def wants(self, names: set[str], sizes: set[tuple[float, str]]) -> bool:
        if self.words and not any(all(word in name for word in self.words) for name in names):
            return False
        return not self.size or not sizes or any(_same_size(size, self.size) for size in sizes)


def _same_size(one: tuple[float, str], other: tuple[float, str]) -> bool:
    return one[1] == other[1] and abs(one[0] - other[0]) <= 0.005 * max(one[0], other[0])


def _size_of(unit_size: Any, title: Any) -> tuple[float, str] | None:
    """De verpakking van een product: uit de pakmaat, anders uit een maat in de naam ("AH RUNDERGEHAKT 500G")."""
    size = packs.parse_pack_size(unit_size)
    if size:
        return size
    match = packs._PACK_SIZE_RE.search(_fold_name(title))
    return packs.parse_pack_size(match.group(0)) if match else None


def _candidates(conn, store: str, query: _Query, today: str, receipts: list[dict[str, Any]]) -> dict[str, str]:
    """Product-id -> de laatste dag dat we iets van het product weten, voor elk product dat bij de zoekterm past.

    Alle namen en verpakkingen van een product tellen samen (uit de aanbiedingen, de bewaarde prijzen, de bestellingen en de bonnen).
    """
    found: dict[str, dict[str, Any]] = {}

    def add(rows) -> None:
        for row in rows:
            product_id = row["product_id"]
            if not product_id:
                continue
            entry = found.setdefault(product_id, {"last": "", "names": set(), "sizes": set()})
            entry["last"] = max(entry["last"], str(row["last"] or "")[:10])
            entry["names"].add(_fold_name(f"{row['title'] or ''} {row['brand'] or ''}"))
            size = _size_of(row["unit_size"], row["title"])
            if size:
                entry["sizes"].add(size)

    columns = "product_id, title, brand, unit_size"
    add(conn.execute(f"SELECT {columns}, MAX(last_seen) AS last FROM offer_history WHERE store = ? GROUP BY {columns}", (store,)))
    add(conn.execute(f"SELECT {columns}, MAX(day) AS last FROM price_observations WHERE store = ? GROUP BY {columns}", (store,)))
    for sql in _purchase_sources(conn, store):
        add(conn.execute(f"SELECT {columns}, MAX(day) AS last FROM ({sql}) WHERE day <= ? GROUP BY {columns}", (today,)))
    add({**row, "last": row["day"]} for row in receipts)
    return {product_id: entry["last"] for product_id, entry in found.items() if query.wants(entry["names"], entry["sizes"])}


def _in(ids: list[str]) -> str:
    return ",".join("?" * len(ids))


def _observations(conn, store: str, ids: list[str]) -> dict[str, list[Any]]:
    found: dict[str, list[Any]] = {}
    for row in conn.execute(f"SELECT * FROM price_observations WHERE store = ? AND product_id IN ({_in(ids)}) ORDER BY day", (store, *ids)):
        found.setdefault(row["product_id"], []).append(row)
    return found


def _purchases(conn, store: str, ids: list[str], today: str, receipts: list[dict[str, Any]]) -> dict[str, list[Any]]:
    found: dict[str, list[Any]] = {}
    for sql in _purchase_sources(conn, store):
        for row in conn.execute(f"SELECT * FROM ({sql}) WHERE product_id IN ({_in(ids)}) AND day <= ? ORDER BY day, ref", (*ids, today)):
            found.setdefault(row["product_id"], []).append(row)
    wanted = set(ids)
    for row in receipts:
        if row["product_id"] in wanted:
            found.setdefault(row["product_id"], []).append(row)
    for rows in found.values():
        rows.sort(key=lambda row: (row["day"] or "", row["source"], str(row["ref"])))
    return found


def _regular_segments(points: list[tuple[str, float, str]]) -> list[dict[str, Any]]:
    """Losse normale prijzen (dag, prijs, bron) als periodes met dezelfde prijs: `from` en `to` zijn de eerste en laatste dag.

    Per dag telt één prijs (zie `_REGULAR_RANK`). Tussen twee periodes weten we niet precies wanneer de prijs veranderde.
    """
    per_day: dict[str, tuple[int, float]] = {}
    for day, price, source in points:
        if not day or price is None:
            continue
        rank = _REGULAR_RANK.get(source, 9)
        if day not in per_day or rank < per_day[day][0]:
            per_day[day] = (rank, round(price, 2))
    segments: list[dict[str, Any]] = []
    for day in sorted(per_day):
        price = per_day[day][1]
        if segments and segments[-1]["price"] == price:
            segments[-1]["to"] = day
        else:
            segments.append({"from": day, "to": day, "price": price})
    return segments


def _product(store: str, product_id: str, offer: dict[str, Any] | None, observed: list[Any], bought: list[Any], today: str) -> dict[str, Any]:
    product: dict[str, Any] = dict(offer) if offer else {
        "store": store, "productId": product_id, "episodes": [], "timesOnOffer": 0, "lowestPrice": None, "highestPrice": None,
        "regularPrice": None, "onOfferNow": False, "category": None, "firstSeen": None, "lastSeen": None,
    }
    # Titel, merk en plaatje: uit de aanbiedingen, anders de nieuwste zoekopdracht, anders de nieuwste bestelling (een kassabon
    # heeft alleen een afgekorte naam, die nemen we als er niets anders is).
    named = [row for row in reversed(bought) if row["source"] == "order"] + list(reversed(bought))
    for row in list(reversed(observed)) + named:
        for key, column in (("title", "title"), ("brand", "brand"), ("unitSize", "unit_size"), ("image", "image")):
            if not product.get(key) and row[column]:
                product[key] = row[column]
    product.setdefault("title", product_id)
    product["url"] = product.get("url") or next((row["url"] for row in reversed(observed) if row["url"]), "") or _url(store, product_id)

    points: list[tuple[str, float, str]] = [(row["day"], row["price"], "search") for row in observed]
    for episode in product["episodes"]:
        if _number(episode.get("priceBefore")) is not None:
            for day in (episode.get("start"), episode.get("end")):
                if day:
                    points.append((str(day)[:10], episode["priceBefore"], "offer"))
    points += [(row["day"], row["regular"], "order") for row in bought if row["source"] == "order" and _number(row["regular"]) is not None]
    # Een aanbieding die nog loopt heeft een einddatum in de toekomst; wat de prijs dan is, weten we nog niet.
    segments = _regular_segments([point for point in points if point[0] <= today])
    product["regular"] = segments
    if segments:
        product["regularPrice"] = segments[-1]["price"]
        product["regularSince"] = segments[-1]["from"]
    product["regularPerUnit"] = unit_price(product.get("regularPrice"), product.get("unitSize"))
    regulars = [segment["price"] for segment in segments]
    product["lowestRegular"] = min(regulars) if regulars else None
    product["highestRegular"] = max(regulars) if regulars else None

    purchases = [
        {
            "date": row["day"], "price": row["paid"], "regularPrice": row["regular"], "quantity": row["quantity"],
            "source": row["source"], "ref": str(row["ref"]),
        }
        for row in bought
        if _number(row["paid"]) is not None
    ]
    product["purchases"] = purchases
    product["timesBought"] = len(purchases)
    product["lastPaid"] = purchases[-1]["price"] if purchases else None
    weighted = [(p["price"], p["quantity"] if isinstance(p["quantity"], (int, float)) and p["quantity"] > 0 else 1) for p in purchases]
    count = sum(quantity for _, quantity in weighted)
    product["averagePaid"] = round(sum(price * quantity for price, quantity in weighted) / count, 2) if count else None
    product["lowestPaid"] = min(p["price"] for p in purchases) if purchases else None
    days = [d for d in (
        str(product.get("lastSeen") or "")[:10],
        segments[-1]["to"] if segments else "",
        purchases[-1]["date"] if purchases else "",
    ) if d]
    product["lastActivity"] = max(days) if days else None
    return product


def history(store: Any = None, query: Any = "", limit: Any = 50, today: str | None = None) -> dict[str, Any]:
    """De prijsgeschiedenis van producten: aanbiedingen, normale prijs en aankopen, het meest recente bovenaan.

    Een product past als elk woord van de zoekterm in een van zijn namen staat (uit de aanbiedingen, een zoekopdracht of een aankoop) en,
    als de zoekterm een maat noemt ("Gehakt 500 gram"), zijn verpakking die maat heeft; zie `_Query`. Per product
    staan de velden van `offers.history` erin, plus `regular` (periodes met dezelfde normale prijs), `purchases` (wat je betaalde,
    per stuk, oudste eerst) en samenvattingen als `regularPrice`, `lastPaid`, `averagePaid` en `timesBought`. `unsynced` zegt per winkel
    van hoeveel bestellingen en kassabonnen de producten nog niet zijn opgehaald: die aankopen ontbreken nog.
    """
    stores = [offers._valid_store(store)] if store else list(STORES)
    wanted = _Query(query)
    limit = _limit(limit)
    today = today or _now()[:10]
    with closing(_connect()) as conn:
        receipts = {name: _receipt_rows(conn, name, today) for name in stores}
        unsynced = {name: _unsynced(conn, name) for name in stores}
        candidates = {
            (name, product_id): day for name in stores
            for product_id, day in _candidates(conn, name, wanted, today, receipts[name]).items()
        }
        ranked = sorted(candidates, key=lambda key: (candidates[key], key[0], key[1]), reverse=True)
        chosen = ranked[:limit]
        found: dict[str, tuple[dict[str, list[Any]], dict[str, list[Any]]]] = {}
        for name in stores:
            ids = [product_id for store_name, product_id in chosen if store_name == name]
            found[name] = (_observations(conn, name, ids), _purchases(conn, name, ids, today, receipts[name])) if ids else ({}, {})
    offered = {name: offers.history_of(name, [pid for s, pid in chosen if s == name]) for name in stores}
    products = [
        _product(name, product_id, offered[name].get(product_id), found[name][0].get(product_id, []), found[name][1].get(product_id, []), today)
        for name, product_id in chosen
    ]
    return {"products": products, "total": len(ranked), "unsynced": unsynced}


# --- prijs per eenheid, oordeel en verpakkingen vergelijken (deel 2) ----------------------------------------------------

VERDICT_DAYS = 30  # de gewone prijs om mee te vergelijken: de laagste van de laatste 30 dagen (zoals de regel voor een "van"-prijs)
YEAR_DAYS = 365
GOOD_DISCOUNT = 0.20  # vanaf 20% onder de laagste gewone prijs van de laatste 30 dagen is het echt voordelig
OFTEN_PER_YEAR = 6  # zo vaak of vaker per jaar in de aanbieding: het komt vaak terug
PACK_DAYS = 60  # hoe oud een prijs van een andere verpakking mag zijn om mee te vergelijken
PACK_MARGIN = 0.02  # een andere verpakking telt pas als goedkoper bij minstens 2% verschil per eenheid
COMPARE_LIMIT = 10

# Woorden die een verpakking aanduiden, niet het product: zonder deze woorden (en zonder maten) is "AH Zalmfilet 3-pack" hetzelfde
# product als "AH Zalmfilet".
_PACK_WORDS = frozenset({
    "voordeelverpakking", "voordeelpak", "voordeel", "multipack", "familieverpakking", "familiepak", "grootverpakking", "navulling",
    "pack", "xl", "xxl", "groot", "grote", "klein", "kleine", "mini", "per", "stuk", "stuks", "st", "x",
})
_SIZE_RE = re.compile(
    r"\b\d+(?:[.,]\d+)?\s*(?:x|\u00d7)?\s*(?:\d+(?:[.,]\d+)?\s*)?"
    r"(?:kilogram|kilo|kg|gram|gr|g|milliliter|centiliter|deciliter|liter|ml|cl|dl|l|stuks|stuk|st|pack|pak)?\b"
)
_UNIT_NAMES = {"mass": "kg", "volume": "l", "count": "stuk"}


def unit_price(price: Any, unit_size: Any) -> dict[str, Any] | None:
    """De prijs per kilo, liter of stuk: `{"value": 5.0, "unit": "kg"}`, of None als de verpakking niet te lezen is.

    "2 x 250 g" is 500 gram, "6 x 1 l" zes liter (dat is de prijs van het pak gedeeld door zes, niet de literprijs zelf), "ca. 500 g"
    is 500 gram en "per stuk" één stuk. Het lezen van de verpakking is dat van het weekmenu (`packs.parse_pack_size`).
    """
    value = _number(price)
    size = packs.parse_pack_size(unit_size)
    if value is None or not size or size[0] <= 0:
        return None
    amount, kind = size
    per = amount / 1000 if kind in ("mass", "volume") else amount
    return {"value": round(value / per, 2), "unit": _UNIT_NAMES[kind]}


def base_title(title: Any) -> str:
    """De naam van een product zonder maten en verpakkingswoorden, in kleine letters: om verpakkingen van hetzelfde product te herkennen."""
    text = _SIZE_RE.sub(" ", ah_lists._fold(title).replace("-", " "))
    return " ".join(word for word in re.findall(r"[a-z0-9]+", text) if word not in _PACK_WORDS)


def _same_product(base: str, other_title: Any) -> bool:
    """Hetzelfde product in een andere verpakking: precies dezelfde naam zonder maten en verpakkingswoorden. "AH Biologisch Penne" is
    dus niet hetzelfde als "AH Penne", maar "AH Penne voordeelverpakking" wel."""
    return bool(base) and base_title(other_title) == base


def _effective(offer: dict[str, Any]) -> float | None:
    """Wat één stuk kost bij de aanbieding (als je de voorwaarde haalt)."""
    if _number(offer.get("unitPrice")) is not None:
        return _number(offer.get("unitPrice"))
    return _number(offer.get("price")) if _number(offer.get("priceBefore")) is not None else None


def _stated_regular(offer: dict[str, Any]) -> float | None:
    """De gewone prijs die de winkel bij de aanbieding noemt. AH geeft bij een actie met een minimumaantal ("2 voor 3") geen prijs ervoor
    maar de gewone prijs als prijs; dan is de prijs hoger dan wat een stuk bij de actie kost."""
    if _number(offer.get("priceBefore")) is not None:
        return _number(offer.get("priceBefore"))
    price, unit = _number(offer.get("price")), _number(offer.get("unitPrice"))
    return price if price is not None and unit is not None and unit < price else None


def _days_ago(today: str, days: int) -> str:
    return (date.fromisoformat(today) - timedelta(days=days)).isoformat()


def _episode_effective(row: Any) -> float | None:
    described = deals.describe(row["promo_label"], price_now=row["price"], price_before=row["price_before"])
    return _number(described["unitPrice"]) if _number(described["unitPrice"]) is not None else _number(row["price"])


def signature(store: Any) -> str:
    """Verandert als er voor deze winkel een nieuwe gewone prijs is bewaard: zo ververst het oordeel bij de aanbiedingen (ETag)."""
    try:
        with closing(_connect()) as conn:
            row = conn.execute("SELECT COUNT(*) AS n, MAX(seen_at) AS last FROM price_observations WHERE store = ?", (str(store),)).fetchone()
        return f"{row['n']}:{row['last'] or ''}"
    except Exception:  # noqa: BLE001 - de cache mag nooit de aanbiedingen tegenhouden
        log.warning("Handtekening van de prijzen mislukt", exc_info=True)
        return ""


def annotate(store: str, items: list[dict[str, Any]], today: str | None = None) -> list[dict[str, Any]]:
    """Zet bij elke aanbieding een oordeel (`verdict`) en de prijs per eenheid (`unitPricePer`). Past de dicts aan en geeft ze terug.

    `verdict.kind` is `good` (minstens 20% onder de laagste gewone prijs van de laatste 30 dagen), `small` (wel lager, maar minder),
    `none` (niet lager dan de gewone prijs van de laatste 30 dagen) of `unknown` (de actieprijs per stuk of de gewone prijs is niet bekend).
    Daarnaast: `percent`, `lowestRegular` (de laagste gewone prijs van de laatste 30 dagen), `inflated` (de prijs ervoor die de winkel noemt
    is hoger dan die laagste), `timesThisYear`, `often`, `bestInYear` en `cheaperPack` (een andere verpakking van hetzelfde product die
    per eenheid goedkoper is, uit de bewaarde prijzen en de andere aanbiedingen). Een fout laat de aanbiedingen staan zonder oordeel.
    """
    try:
        _annotate(store, items, today or _now()[:10])
    except Exception:  # noqa: BLE001 - het oordeel is een extraatje; de aanbiedingen gaan altijd door
        log.warning("Oordeel bij de aanbiedingen mislukt voor %s", store, exc_info=True)
    return items


def _annotate(store: str, items: list[dict[str, Any]], today: str) -> None:
    since30, since_year, since_pack = _days_ago(today, VERDICT_DAYS), _days_ago(today, YEAR_DAYS), _days_ago(today, PACK_DAYS)
    regulars: dict[str, list[float]] = {}
    episodes: dict[str, list[Any]] = {}
    latest: dict[str, Any] = {}
    with closing(_connect()) as conn:
        for row in conn.execute("SELECT * FROM price_observations WHERE store = ? AND day >= ? AND day <= ? ORDER BY day", (store, since_pack, today)):
            latest[row["product_id"]] = row
            if row["day"] >= since30:
                regulars.setdefault(row["product_id"], []).append(row["price"])
        for sql in _purchase_sources(conn, store):
            for row in conn.execute(f"SELECT product_id, regular FROM ({sql}) WHERE day >= ? AND day <= ? AND regular IS NOT NULL", (since30, today)):
                if _number(row["regular"]) is not None:
                    regulars.setdefault(row["product_id"], []).append(row["regular"])
        for row in conn.execute(
            "SELECT * FROM offer_history WHERE store = ? AND COALESCE(valid_until, substr(last_seen, 1, 10)) >= ? ORDER BY product_id, first_seen, id",
            (store, since_year),
        ):
            episodes.setdefault(row["product_id"], []).append(row)
            span = offers._episode_span(row)
            if _number(row["price_before"]) is not None and (span["end"] is None or span["end"] >= since30) and (span["start"] or "") <= today:
                regulars.setdefault(row["product_id"], []).append(row["price_before"])

    # Wat elke verpakking per eenheid kost: de gewone prijs uit de bewaarde prijzen en de actieprijs van de andere aanbiedingen.
    packs_known: list[dict[str, Any]] = [
        {"productId": pid, "title": row["title"], "brand": row["brand"], "unitSize": row["unit_size"], "price": row["price"],
         "unitPrice": unit_price(row["price"], row["unit_size"]), "onOffer": False}
        for pid, row in latest.items()
    ]
    for offer in items:
        effective = _effective(offer)
        packs_known.append({"productId": str(offer.get("id")), "title": offer.get("title"), "brand": offer.get("brand"), "unitSize": offer.get("unitSize"),
                            "price": effective, "unitPrice": unit_price(effective, offer.get("unitSize")), "onOffer": True})
    by_base: dict[str, list[dict[str, Any]]] = {}
    for pack in packs_known:
        if pack["unitPrice"]:
            by_base.setdefault(base_title(pack["title"]), []).append(pack)

    for offer in items:
        product_id = str(offer.get("id"))
        effective = _effective(offer)
        stated = _stated_regular(offer)
        offer["unitPricePer"] = unit_price(effective if effective is not None else offer.get("price"), offer.get("unitSize"))
        known = [price for price in regulars.get(product_id, []) if _number(price) is not None]
        if stated is not None:
            known.append(stated)
        lowest = min(known) if known else None
        verdict: dict[str, Any] = {"kind": "unknown", "percent": None, "lowestRegular": lowest, "inflated": False}
        if effective is not None and lowest:
            ratio = 1 - effective / lowest
            verdict["percent"] = max(0, round(ratio * 100))
            verdict["kind"] = "none" if effective >= lowest - 0.005 else ("good" if ratio >= GOOD_DISCOUNT - 1e-9 else "small")
            verdict["inflated"] = stated is not None and stated > lowest + 0.005
        mine = [row for row in episodes.get(product_id, []) if not row["continued"]]
        started = [row for row in mine if (offers._episode_span(row)["start"] or "") >= since_year]
        verdict["timesThisYear"] = max(len(started), 1)
        verdict["often"] = verdict["timesThisYear"] >= OFTEN_PER_YEAR
        others = [price for row in episodes.get(product_id, []) if row["ended_at"] is not None
                  for price in [_episode_effective(row)] if price is not None]
        verdict["bestInYear"] = bool(others) and effective is not None and effective <= min(others) + 0.005
        verdict["cheaperPack"] = None
        mine_unit = offer["unitPricePer"]
        base = base_title(offer.get("title"))
        if mine_unit and base:
            cheaper = [
                pack for pack in by_base.get(base, [])
                if pack["productId"] != product_id and pack["unitPrice"]["unit"] == mine_unit["unit"]
                and (not offer.get("brand") or not pack["brand"] or ah_lists._fold(pack["brand"]) == ah_lists._fold(offer["brand"]))
                and pack["unitPrice"]["value"] < mine_unit["value"] * (1 - PACK_MARGIN)
            ]
            if cheaper:
                best = min(cheaper, key=lambda pack: pack["unitPrice"]["value"])
                verdict["cheaperPack"] = {key: best[key] for key in ("productId", "title", "unitSize", "price", "unitPrice", "onOffer")}
        offer["verdict"] = verdict


def _search_function(store: str) -> Callable[..., list[dict[str, Any]]]:
    from . import crisp, jumbo, picnic  # pylint: disable=import-outside-toplevel - alleen nodig bij het vergelijken

    return {"ah": ah.search_products, "jumbo": jumbo.search_products, "picnic": picnic.search_products, "crisp": crisp.search_products}[store]


def _search_query(title: Any) -> str:
    """De zoekterm voor andere verpakkingen: de naam zonder maten en verpakkingswoorden, maar met de hoofdletters van de winkel."""
    text = re.sub(r"(\d)\s*-\s*(pack|pak)\b", r"\1 \2", str(title or ""), flags=re.IGNORECASE)
    words = [word for word in _SIZE_RE.sub(" ", text).split() if ah_lists._fold(word) not in _PACK_WORDS]
    return " ".join(words).strip() or str(title or "").strip()


def _known_product(store: str, product_id: str) -> dict[str, Any] | None:
    """Een product uit de aanbiedingen van nu, anders uit de bewaarde prijzen: naam, merk, verpakking en prijs per stuk."""
    current = next((offer for offer in offers.load(store)["offers"] if str(offer["id"]) == product_id), None)
    if current:
        effective = _effective(current)
        return {"id": product_id, "title": current["title"], "brand": current.get("brand"), "unitSize": current.get("unitSize"),
                "price": effective if effective is not None else current.get("price"), "onOffer": True, "promoLabel": current.get("promoLabel") or ""}
    with closing(_connect()) as conn:
        row = conn.execute("SELECT * FROM price_observations WHERE store = ? AND product_id = ? ORDER BY day DESC LIMIT 1", (store, product_id)).fetchone()
    if row:
        return {"id": product_id, "title": row["title"], "brand": row["brand"], "unitSize": row["unit_size"], "price": row["price"], "onOffer": False, "promoLabel": ""}
    return None


def _candidate(store: str, product: dict[str, Any]) -> dict[str, Any] | None:
    product_id = _product_id(product)
    price = product.get("price")
    price = _number(price.get("now") if isinstance(price, dict) else price)
    deal = deals.make_deal(store, product)
    effective = _number(deal["unitPrice"]) if deal and _number(deal.get("unitPrice")) is not None else price
    if not product_id or effective is None:
        return None
    return {
        "id": product_id, "title": product.get("title"), "brand": product.get("brand"), "unitSize": product.get("unitSize"),
        "price": effective, "onOffer": bool(product.get("isBonus")), "image": _image(product),
        "url": _url(store, product_id, product.get("url")), "unitPrice": unit_price(effective, product.get("unitSize")),
    }


def compare(store: Any, product_id: Any) -> dict[str, Any]:
    """Andere verpakkingen van hetzelfde product, per kilo, liter of stuk naast elkaar. Doet één zoekopdracht bij de winkel.

    De gevonden producten met dezelfde soort eenheid staan op prijs per eenheid, de goedkoopste eerst. `same` zegt of het hetzelfde
    product is (dezelfde naam zonder maten en verpakkingswoorden, en hetzelfde merk), `cheaper` of het per eenheid goedkoper is dan het
    gekozen product, en `rank` (0 tot 1) waar het tussen de rest staat, voor de kleur. De gevonden prijzen worden ook bewaard.
    """
    store = offers._valid_store(store)
    product_id = str(product_id or "").strip()
    product = _known_product(store, product_id) if product_id else None
    if not product:
        raise ValueError(tr("Dit product is niet bekend bij de aanbiedingen of de bewaarde prijzen", "This product is not known in the offers or the tracked prices"))
    product["unitPrice"] = unit_price(product["price"], product["unitSize"])
    query = _search_query(product["title"])
    found = recorded(store, _search_function(store))(query, COMPARE_LIMIT)
    base = base_title(product["title"])
    unit = (product["unitPrice"] or {}).get("unit")
    candidates = [c for c in (_candidate(store, item) for item in found if isinstance(item, dict)) if c and c["unitPrice"]]
    if unit:
        candidates = [c for c in candidates if c["unitPrice"]["unit"] == unit]
    for candidate in candidates:
        if candidate["id"] == product_id and product["unitPrice"]:
            # Het gekozen product met de prijs die we al kennen: bij een aanbieding wat een stuk met de actie kost.
            candidate.update(price=product["price"], unitPrice=product["unitPrice"], onOffer=product["onOffer"] or candidate["onOffer"])
    if not any(c["id"] == product_id for c in candidates) and product["unitPrice"]:
        candidates.append({**{key: product.get(key) for key in ("id", "title", "brand", "unitSize", "price", "onOffer", "unitPrice")}, "image": None,
                           "url": _url(store, product_id)})
    candidates.sort(key=lambda c: (c["unitPrice"]["value"], c["id"] != product_id))
    mine = product["unitPrice"]["value"] if product["unitPrice"] else None
    brand = ah_lists._fold(product.get("brand") or "")
    for index, candidate in enumerate(candidates):
        candidate["self"] = candidate["id"] == product_id
        candidate["same"] = candidate["self"] or (
            _same_product(base, candidate["title"]) and (not brand or not candidate.get("brand") or ah_lists._fold(candidate["brand"]) == brand)
        )
        candidate["cheaper"] = mine is not None and not candidate["self"] and candidate["unitPrice"]["value"] < mine * (1 - PACK_MARGIN)
        candidate["rank"] = round(index / (len(candidates) - 1), 2) if len(candidates) > 1 else 0
    return {"store": store, "product": product, "query": query, "unit": unit, "candidates": candidates}
