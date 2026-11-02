"""Prijsgeschiedenis van producten: wanneer iets in de aanbieding was, wat het normaal kostte en wat je ervoor betaalde (#178).

Drie bronnen, per winkel en product samengevoegd:

- **aanbiedingen**: `offer_history` (zie `offers._record_history`), elke periode met de actieprijs, de prijs ervoor en het label;
- **normale prijs**: `price_observations`, één prijs per product per dag. Die wordt bijgehouden waar MealieShopper een product bij
  de winkel opzoekt (de controlelijst van het weekmenu, *Zoek een ander product*, het zoeken per winkel en de MCP-tools). Daarnaast
  telt de prijs vóór de actie van een aanbieding mee, en de normale prijs die in een bestelling staat;
- **wat je betaalde**: je bestellingen en kassabonnen (AH, Jumbo, Picnic en Crisp), gelezen uit de kopie die de app daar al van
  bewaart. Zo staat alles wat je ooit via de app ophaalde meteen in de geschiedenis; een aparte import is niet nodig.

Lezen vraagt nooit iets aan een winkel. Het bijhouden laat nooit een zoekopdracht mislukken: een fout wordt gelogd en verder genegeerd.
"""
from __future__ import annotations

import logging
from contextlib import closing
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from . import ah, offers

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
        (
            # De prijs op de kassabon; een korting onderaan de bon (zoals de Bonus) is daar niet van afgetrokken.
            ("ah_receipt_items", "ah_receipts"),
            """
            SELECT CAST(l.product_id AS TEXT) AS product_id, l.name AS title, NULL AS brand, NULL AS unit_size, NULL AS image,
                   substr(r.date_time, 1, 10) AS day, l.quantity AS quantity,
                   COALESCE(l.price, CASE WHEN l.quantity > 0 THEN round(l.amount / l.quantity, 2) END) AS paid,
                   NULL AS regular, 'receipt' AS source, r.receipt_id AS ref
            FROM ah_receipt_items l JOIN ah_receipts r ON r.receipt_id = l.receipt_id
            WHERE l.product_id IS NOT NULL AND l.product_id != 0
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


def _pattern(query: Any) -> str:
    return "%" + str(query or "").strip().replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_") + "%"


def _limit(value: Any) -> int:
    try:
        return max(1, min(MAX_LIMIT, int(value)))
    except (TypeError, ValueError):
        return 50


def _candidates(conn, store: str, pattern: str, today: str) -> dict[str, str]:
    """Product-id -> de laatste dag dat we iets van het product weten, voor elk product waarvan een titel bij de zoekterm past."""
    last: dict[str, str] = {}

    def add(rows) -> None:
        for row in rows:
            day = str(row["last"] or "")[:10]
            if row["product_id"] and day > last.get(row["product_id"], ""):
                last[row["product_id"]] = day

    add(conn.execute(
        "SELECT product_id, MAX(last_seen) AS last FROM offer_history WHERE store = ? AND title LIKE ? ESCAPE '\\' GROUP BY product_id",
        (store, pattern),
    ))
    add(conn.execute(
        "SELECT product_id, MAX(day) AS last FROM price_observations WHERE store = ? AND title LIKE ? ESCAPE '\\' GROUP BY product_id",
        (store, pattern),
    ))
    for sql in _purchase_sources(conn, store):
        add(conn.execute(
            f"SELECT product_id, MAX(day) AS last FROM ({sql}) WHERE title LIKE ? ESCAPE '\\' AND day <= ? GROUP BY product_id",
            (pattern, today),
        ))
    return last


def _in(ids: list[str]) -> str:
    return ",".join("?" * len(ids))


def _observations(conn, store: str, ids: list[str]) -> dict[str, list[Any]]:
    found: dict[str, list[Any]] = {}
    for row in conn.execute(f"SELECT * FROM price_observations WHERE store = ? AND product_id IN ({_in(ids)}) ORDER BY day", (store, *ids)):
        found.setdefault(row["product_id"], []).append(row)
    return found


def _purchases(conn, store: str, ids: list[str], today: str) -> dict[str, list[Any]]:
    found: dict[str, list[Any]] = {}
    for sql in _purchase_sources(conn, store):
        for row in conn.execute(f"SELECT * FROM ({sql}) WHERE product_id IN ({_in(ids)}) AND day <= ? ORDER BY day, ref", (*ids, today)):
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

    Een product past als een van zijn titels (uit de aanbiedingen, een zoekopdracht of een aankoop) de zoekterm bevat. Per product
    staan de velden van `offers.history` erin, plus `regular` (periodes met dezelfde normale prijs), `purchases` (wat je betaalde,
    per stuk, oudste eerst) en samenvattingen als `regularPrice`, `lastPaid`, `averagePaid` en `timesBought`.
    """
    stores = [offers._valid_store(store)] if store else list(STORES)
    pattern = _pattern(query)
    limit = _limit(limit)
    today = today or _now()[:10]
    with closing(_connect()) as conn:
        candidates = {(name, product_id): day for name in stores for product_id, day in _candidates(conn, name, pattern, today).items()}
        ranked = sorted(candidates, key=lambda key: (candidates[key], key[0], key[1]), reverse=True)
        chosen = ranked[:limit]
        found: dict[str, tuple[dict[str, list[Any]], dict[str, list[Any]]]] = {}
        for name in stores:
            ids = [product_id for store_name, product_id in chosen if store_name == name]
            found[name] = (_observations(conn, name, ids), _purchases(conn, name, ids, today)) if ids else ({}, {})
    offered = {name: offers.history_of(name, [pid for s, pid in chosen if s == name]) for name in stores}
    products = [
        _product(name, product_id, offered[name].get(product_id), found[name][0].get(product_id, []), found[name][1].get(product_id, []), today)
        for name, product_id in chosen
    ]
    return {"products": products, "total": len(ranked)}
