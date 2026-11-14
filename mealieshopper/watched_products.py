"""Gevolgde producten: losse artikelen waarvan MealieShopper elke nacht de prijs, de maat, de verpakking en de afbeelding bewaart.

De Prijsgeschiedenis (`prices.py`) laat vooral de aanbiedingen zien, en de gewone prijs alleen als je een product toevallig opzoekt
of koopt. Wie wil zien of een product dat je vaak koopt langzaam duurder wordt (of kleiner, voor dezelfde prijs), zet het hier op
de lijst: zoeken bij de winkel (`search`), een product volgen (`add`) en weer loslaten (`remove`).

- **Elke nacht** (`refresh_nightly`, vanuit `offers_schedule.run_all`) haalt MealieShopper van elk gevolgd product de gegevens van
  vandaag op: AH in groepjes op product-id (`ah.get_products_by_ids`, in het tempo en met het uurbudget van de bonnen,
  `ah_orders.polite`), Jumbo per artikel (`jumbo.get_product`), Picnic en Crisp met een zoekopdracht op de naam waarin hetzelfde
  product-id moet zitten. Per product en dag staat er één punt in `watched_prices`: wat het nu in de winkel kost (`price`), de gewone
  prijs zonder aanbieding (`regular`), of er een aanbieding liep (`on_offer`, alleen ter informatie: de grafiek volgt de gewone prijs),
  de maat (`unit_size`), het soort verpakking (`packaging`) en het adres van de afbeelding.
- **De afbeelding zelf** komt in `product_images` (dezelfde tabel als die van de gekochte aanbiedingen, `product_images.py`): een
  nieuwe afbeelding komt erbij en de oude blijft, zodat je ziet wanneer de verpakking veranderde.
- **De gewone prijs** gaat ook naar `price_observations` (`prices.record`), zodat het product met zijn prijslijn in de
  Prijsgeschiedenis staat, naast de aanbiedingen en je aankopen.
- **Het soort verpakking** leidt `packaging_type` af uit de naam en de maat ("6 x 330 ml" is een multiverpakking, "Pindakaas pot" een
  pot). Staat er geen verpakkingswoord in, dan is het onbekend: de winkels geven het niet als apart veld.

Een product loslaten haalt het van de lijst; de bewaarde prijzen blijven staan, zodat je ze terugziet als je het opnieuw volgt. Een
fout bij één product laat de rest doorgaan; de nachtelijke run faalt er nooit door.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

import aiosqlite
from quart import Quart, jsonify, request

from . import ah, ah_lists, ah_orders, auth, offers, prices, product_images, request_body
from .i18n import tr

log = logging.getLogger(__name__)

STORES = offers.SHOP_STORES  # alleen waar MealieShopper kan zoeken: Lidl en Aldi hebben alleen hun aanbiedingen
MAX_WATCHED = 500  # meer producten per nacht wordt een flinke ronde langs de winkels
SEARCH_LIMIT = 12
AH_BATCH = 30  # zoveel AH-producten per aanvraag
PAUSE_SECONDS = 1.0  # tussen twee aanvragen bij Jumbo, Picnic en Crisp: rustig, zoals de rest van de nachtelijke run
MAX_POINTS = 4000  # punten per product in een antwoord: ruim tien jaar

SCHEMA = """
-- De producten die je volgt (watched_products.py), per winkel en product-id, met hoe ze er de laatste keer uitzagen.
CREATE TABLE IF NOT EXISTS watched_products (
  store TEXT NOT NULL,
  product_id TEXT NOT NULL,
  title TEXT NOT NULL,
  brand TEXT,
  unit_size TEXT,
  packaging TEXT,
  image TEXT,
  url TEXT,
  added_at TEXT NOT NULL,
  added_by TEXT,
  checked_at TEXT,
  missing_since TEXT,
  PRIMARY KEY (store, product_id)
);
-- Wat een gevolgd product op een dag was: één punt per winkel, product en dag (het laatste van die dag). `price` is wat het in de
-- winkel kostte, `regular` de gewone prijs (zonder aanbieding; leeg als de winkel die bij een aanbieding niet noemt).
CREATE TABLE IF NOT EXISTS watched_prices (
  store TEXT NOT NULL,
  product_id TEXT NOT NULL,
  day TEXT NOT NULL,
  price REAL NOT NULL,
  regular REAL,
  on_offer INTEGER NOT NULL DEFAULT 0,
  offer_label TEXT,
  title TEXT NOT NULL,
  brand TEXT,
  unit_size TEXT,
  packaging TEXT,
  image TEXT,
  seen_at TEXT NOT NULL,
  PRIMARY KEY (store, product_id, day)
);
"""

# Het soort verpakking, met de woorden die erop wijzen. De volgorde telt: "multipack" gaat voor "blik" ("6 x 330 ml blik").
PACKAGING_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("multipack", ("multipack", "multiverpakking", "voordeelverpakking", "tray")),
    ("can", ("blik", "blikje", "blikjes", "blikken", "can")),
    ("bottle", ("fles", "flesje", "flessen", "petfles", "knijpfles", "bottle")),
    ("jar", ("pot", "potje", "glas", "jar")),
    ("carton", ("pak", "pakje", "pakken", "brik", "carton")),
    ("tub", ("kuipje", "kuip", "bakje", "bak", "beker", "bekertje", "tub")),
    ("tube", ("tube",)),
    ("bag", ("zak", "zakje", "zakken", "bag", "stazak", "net", "netje")),
    ("box", ("doos", "doosje", "box")),
    ("bunch", ("bos", "bosje", "tros")),
    ("piece", ("stuk", "stuks", "per stuk", "los", "piece")),
    ("weighed", ("per kilo", "per kg", "per 100 g", "per 500 g")),
)
_MULTI_RE = re.compile(r"\b\d+\s*[x×]\s*\d", re.IGNORECASE)
_WORD_RE = re.compile(r"[a-zà-ÿ]+(?: [a-zà-ÿ]+)?", re.IGNORECASE)


def packaging_type(title: Any, unit_size: Any) -> str | None:
    """Het soort verpakking uit de naam en de maat, of None als het er niet uit volgt (zie `PACKAGING_WORDS`)."""
    size = str(unit_size or "")
    if _MULTI_RE.search(size):
        return "multipack"
    text = f" {ah_lists._fold(size)} {ah_lists._fold(title)} "
    words = set(re.findall(r"[a-z]+", text))
    for kind, hints in PACKAGING_WORDS:
        for hint in hints:
            if (" " in hint and f" {hint} " in text) or hint in words:
                return kind
    return None


# Een productlink van de winkel: het product-id staat erin, dus we hoeven niet te zoeken.
# AH: https://www.ah.nl/producten/product/wi4183/ah-halfvolle-melk (ook de app-link ah.nl/producten/product/wi4183).
# Jumbo: https://www.jumbo.com/producten/jumbo-halfvolle-melk-1l-123456PAK (de SKU is het laatste stuk na het streepje).
_URL_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ah", re.compile(r"^https?://(?:www\.)?ah\.(?:nl|be)/producten/product/wi(\d+)", re.IGNORECASE)),
    ("jumbo", re.compile(r"^https?://(?:www\.)?jumbo\.com/producten/(?:[^/?#]*-)?(\d+[A-Z]{2,4})(?:[/?#]|$)", re.IGNORECASE)),
)


def parse_url(text: Any) -> tuple[str, str] | None:
    """(winkel, product-id) uit een geplakte productlink van AH of Jumbo, of None als het geen (bekende) productlink is."""
    value = str(text or "").strip()
    for store, rule in _URL_RULES:
        match = rule.match(value)
        if match:
            product_id = match.group(1)
            return store, product_id.upper() if store == "jumbo" else product_id
    return None


def looks_like_url(text: Any) -> bool:
    return bool(re.match(r"^\s*(https?://|www\.)", str(text or ""), re.IGNORECASE))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@asynccontextmanager
async def _connect() -> AsyncIterator[aiosqlite.Connection]:
    """Een verbinding met het schema van de prijzen (voor `product_images` en `price_observations`) en dat van deze module."""
    async with prices._connect() as conn:
        await auth.ensure_schema(conn, "watched_products", SCHEMA)
        yield conn


def _store(value: Any) -> str:
    key = str(value or "").strip().lower()
    if key not in STORES:
        raise ValueError(tr("Bij deze winkel kan MealieShopper geen producten volgen", "MealieShopper cannot follow products at this store"))
    return key


def _product_id(value: Any) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 64:
        raise ValueError(tr("Geen geldig product", "Not a valid product"))
    return text


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return round(float(value), 2) if value > 0 else None


def snapshot(store: str, product: dict[str, Any]) -> dict[str, Any] | None:
    """Een product uit een antwoord van de winkel (de vorm van `search_products` of `get_products_by_ids`) als punt, of None zonder
    id, naam of prijs. `price` is wat het nu kost (bij een aanbieding de actieprijs van één stuk als de winkel die noemt), `regular`
    de gewone prijs (`prices.regular_price`)."""
    product_id = prices._product_id(product)
    title = str(product.get("title") or "").strip()
    raw = product.get("price")
    price = _number(raw.get("now") if isinstance(raw, dict) else raw)
    regular = _number(prices.regular_price(product))
    on_offer = bool(product.get("isBonus") or product.get("bonus"))
    if price is None:
        price = regular
    if not product_id or not title or price is None:
        return None
    if not on_offer and regular is None:
        regular = price
    promo = product.get("promo") if isinstance(product.get("promo"), dict) else {}
    unit_size = str(product.get("unitSize") or "").strip() or None
    if unit_size and unit_size == title:  # Jumbo zet de naam als maat als er geen ondertitel is
        unit_size = None
    return {
        "productId": product_id,
        "title": title,
        "brand": product.get("brand") or None,
        "unitSize": unit_size,
        "packaging": packaging_type(title, unit_size),
        "image": prices._image(product),
        "url": prices._url(store, product_id, product.get("url")) or None,
        "price": price,
        "regular": regular,
        "onOffer": on_offer,
        "offerLabel": str(promo.get("label") or product.get("promoLabel") or "").strip() or None,
    }


# --- bij de winkel --------------------------------------------------------------------------------------------------


async def _pause() -> None:
    await asyncio.sleep(PAUSE_SECONDS)


async def _search(store: str, query: str, limit: int) -> list[dict[str, Any]]:
    search: Callable[..., Awaitable[list[dict[str, Any]]]] = prices._search_function(store)
    if store == "ah":
        return await ah_orders.polite(lambda: search(query, limit))
    return await search(query, limit)


async def _fetch_ah(ids: list[str]) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    numbers = [int(product_id) for product_id in ids if product_id.isdigit()]
    for start in range(0, len(numbers), AH_BATCH):
        batch = numbers[start : start + AH_BATCH]
        for product in await ah_orders.polite(lambda batch=batch: ah.get_products_by_ids(batch)):  # type: ignore[misc]
            if isinstance(product, dict) and product.get("id") is not None:
                found[str(product["id"])] = product
    return found


async def _fetch_jumbo(ids: list[str]) -> dict[str, dict[str, Any]]:
    from . import jumbo  # pylint: disable=import-outside-toplevel - zoals prices._search_function

    found: dict[str, dict[str, Any]] = {}
    for number, product_id in enumerate(ids):
        if number:
            await _pause()
        try:
            product = await jumbo.get_product(product_id)
        except Exception as exc:  # noqa: BLE001 - één product dat niet lukt, laat de rest doorgaan
            log.info("Gevolgd product %s bij Jumbo niet opgehaald: %s", product_id, exc)
            continue
        if product:
            found[product_id] = product
    return found


async def _fetch_by_name(store: str, wanted: dict[str, str]) -> dict[str, dict[str, Any]]:
    """Picnic en Crisp: zoek op de naam en neem het resultaat met hetzelfde product-id. Zonder treffer is het product (even) weg."""
    found: dict[str, dict[str, Any]] = {}
    for number, (product_id, title) in enumerate(wanted.items()):
        if number:
            await _pause()
        try:
            results = await _search(store, title, SEARCH_LIMIT)
        except Exception as exc:  # noqa: BLE001 - één product dat niet lukt, laat de rest doorgaan
            log.info("Gevolgd product %s bij %s niet opgehaald: %s", product_id, store, exc)
            continue
        match = next((item for item in results if prices._product_id(item) == product_id), None)
        if match:
            found[product_id] = match
    return found


async def fetch(store: str, wanted: dict[str, str]) -> dict[str, dict[str, Any]]:
    """De producten van vandaag bij de winkel, per product-id. `wanted` is product-id -> naam (voor de winkels zonder opvragen op id).
    Wat niet gevonden wordt, ontbreekt in het antwoord."""
    if not wanted:
        return {}
    if store == "ah":
        return await _fetch_ah(list(wanted))
    if store == "jumbo":
        return await _fetch_jumbo(list(wanted))
    return await _fetch_by_name(store, wanted)


# --- opslaan -------------------------------------------------------------------------------------------------------


async def _save_point(conn: aiosqlite.Connection, store: str, point: dict[str, Any], stamp: str) -> None:
    await conn.execute(
        """
        INSERT INTO watched_prices (store, product_id, day, price, regular, on_offer, offer_label, title, brand, unit_size, packaging,
                                    image, seen_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (store, product_id, day) DO UPDATE SET
          price = excluded.price, regular = excluded.regular, on_offer = excluded.on_offer, offer_label = excluded.offer_label,
          title = excluded.title, brand = COALESCE(excluded.brand, brand), unit_size = excluded.unit_size,
          packaging = excluded.packaging, image = COALESCE(excluded.image, image), seen_at = excluded.seen_at
        """,
        (
            store,
            point["productId"],
            stamp[:10],
            point["price"],
            point["regular"],
            1 if point["onOffer"] else 0,
            point["offerLabel"],
            point["title"],
            point["brand"],
            point["unitSize"],
            point["packaging"],
            point["image"],
            stamp,
        ),
    )
    await conn.execute(
        """
        UPDATE watched_products SET title = ?, brand = COALESCE(?, brand), unit_size = ?, packaging = ?, image = COALESCE(?, image),
               url = COALESCE(?, url), checked_at = ?, missing_since = NULL
        WHERE store = ? AND product_id = ?
        """,
        (point["title"], point["brand"], point["unitSize"], point["packaging"], point["image"], point["url"], stamp, store, point["productId"]),
    )


async def _save_images(store: str, points: list[dict[str, Any]], stamp: str) -> int:
    """Bewaar de afbeelding van elk punt in `product_images` (een nieuwe komt erbij, de oude blijft). Geeft het aantal nieuwe."""
    wanted = [(point["productId"], point["title"], point["image"]) for point in points if product_images._web_url(point.get("image"))]
    if not wanted:
        return 0
    try:
        async with _connect() as conn:
            have = {
                (row["product_id"], row["source_url"])
                for row in await conn.execute_fetchall("SELECT product_id, source_url FROM product_images WHERE store = ?", (store,))
            }
        wanted = [entry for entry in wanted if (entry[0], entry[2]) not in have]
        if not wanted:
            return 0
        fetched, _ = await product_images._fetch_all(store, wanted)
        saved = 0
        async with _connect() as conn:
            for (product_id, title, url), image in fetched:
                saved += await product_images._store(conn, store, product_id, title, url, image, stamp)
            await conn.commit()
        return saved
    except Exception:  # noqa: BLE001 - de afbeelding is een extraatje bij de prijs
        log.warning("Afbeeldingen van gevolgde producten bewaren mislukt voor %s", store, exc_info=True)
        return 0


async def _record(store: str, found: dict[str, dict[str, Any]], stamp: str) -> list[dict[str, Any]]:
    points = [point for point in (snapshot(store, product) for product in found.values()) if point]
    if not points:
        return []
    async with _connect() as conn:
        for point in points:
            await _save_point(conn, store, point, stamp)
        await conn.commit()
    # De gewone prijs ook in de Prijsgeschiedenis (price_observations), zoals bij elke zoekopdracht.
    await prices.record(store, list(found.values()), now=stamp)
    await _save_images(store, points, stamp)
    return points


# --- de lijst ------------------------------------------------------------------------------------------------------


async def _watched_ids(conn: aiosqlite.Connection, store: str | None = None) -> list[Any]:
    sql = "SELECT * FROM watched_products"
    args: tuple[Any, ...] = ()
    if store:
        sql += " WHERE store = ?"
        args = (store,)
    return list(await conn.execute_fetchall(sql + " ORDER BY store, title COLLATE NOCASE", args))


async def keys() -> list[str]:
    """`winkel:product-id` van elk gevolgd product: de Prijsgeschiedenis zet er een markering bij."""
    async with _connect() as conn:
        return [f"{row['store']}:{row['product_id']}" for row in await _watched_ids(conn)]


async def add(store: Any, product_id: Any, given: dict[str, Any] | None = None, user: str | None = None) -> dict[str, Any]:
    """Volg een product. Haalt het meteen bij de winkel op (dat is het eerste punt); lukt dat niet, dan geldt `given` (de naam, maat,
    prijs en afbeelding zoals de pagina ze uit een zoekresultaat kent) zolang de winkel het niet geeft."""
    key, product_id = _store(store), _product_id(product_id)
    given = given if isinstance(given, dict) else {}
    async with _connect() as conn:
        count = await auth.fetchone(conn, "SELECT COUNT(*) AS n FROM watched_products")
        exists = await auth.fetchone(conn, "SELECT 1 FROM watched_products WHERE store = ? AND product_id = ?", (key, product_id))
    if not exists and count and count["n"] >= MAX_WATCHED:
        raise ValueError(
            tr(f"Je volgt al {MAX_WATCHED} producten: laat er eerst een los", f"You already follow {MAX_WATCHED} products: unfollow one first")
        )
    title = str(given.get("title") or "").strip()
    try:
        found = await fetch(key, {product_id: title or product_id})
    except Exception as exc:  # noqa: BLE001 - de winkel is onofficieel: volg het product met wat de pagina weet
        log.info("Gevolgd product %s bij %s nu niet opgehaald: %s", product_id, key, exc)
        found = {}
    live = snapshot(key, found[product_id]) if product_id in found else None
    first = live or snapshot(key, {**given, "id": product_id})
    if not first and not title:
        raise ValueError(tr("Dit product is niet gevonden bij de winkel", "This product was not found at the store"))
    stamp = _now()
    shown = first or {"title": title, "brand": None, "unitSize": None, "packaging": None, "image": None, "url": None}
    async with _connect() as conn:
        await conn.execute(
            """
            INSERT INTO watched_products (store, product_id, title, brand, unit_size, packaging, image, url, added_at, added_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (store, product_id) DO NOTHING
            """,
            (key, product_id, shown["title"], shown["brand"], shown["unitSize"], shown["packaging"], shown["image"], shown["url"], stamp, user),
        )
        await conn.commit()
    if live:
        await _record(key, {product_id: found[product_id]}, stamp)
    return await entry(key, product_id)


async def remove(store: Any, product_id: Any) -> bool:
    """Laat een product los. De bewaarde prijzen blijven staan (ze komen terug als je het opnieuw volgt)."""
    key, product_id = _store(store), _product_id(product_id)
    async with _connect() as conn:
        cursor = await conn.execute("DELETE FROM watched_products WHERE store = ? AND product_id = ?", (key, product_id))
        await conn.commit()
        return cursor.rowcount > 0


def _point(row: Any) -> dict[str, Any]:
    return {
        "day": row["day"],
        "price": row["price"],
        "regular": row["regular"],
        "onOffer": bool(row["on_offer"]),
        "offerLabel": row["offer_label"],
        "title": row["title"],
        "unitSize": row["unit_size"],
        "packaging": row["packaging"],
        "image": row["image"],
    }


def _shelf(point: dict[str, Any]) -> float | None:
    """De prijs die telt voor de trend: de gewone prijs, of zonder die (een aanbieding zonder prijs ervoor) niets."""
    return point["regular"] if isinstance(point["regular"], (int, float)) else None


def changes(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Wat er tussen twee punten veranderde (oudste eerst): de gewone prijs, de maat, de verpakking, de naam of de afbeelding.
    Een dag zonder gewone prijs (een aanbieding) slaat de prijs over, zodat een aanbieding geen prijsverhoging lijkt."""
    found: list[dict[str, Any]] = []
    last: dict[str, Any] = {}
    for point in points:
        current = {
            "regular": _shelf(point),
            "unitSize": point["unitSize"],
            "packaging": point["packaging"],
            "title": point["title"],
            "image": point["image"],
        }
        for field, value in current.items():
            if value is None:
                continue
            before = last.get(field)
            if before is not None and before != value:
                found.append({"day": point["day"], "field": field, "from": before, "to": value})
            last[field] = value
    return found


def _summary(points: list[dict[str, Any]]) -> dict[str, Any]:
    regulars = [(point["day"], _shelf(point)) for point in points if _shelf(point) is not None]
    first = regulars[0] if regulars else None
    last = regulars[-1] if regulars else None
    change = round(last[1] - first[1], 2) if first and last else None
    percent = round((last[1] - first[1]) / first[1] * 100, 1) if first and last and first[1] else None
    return {
        "firstRegular": {"day": first[0], "price": first[1]} if first else None,
        "lastRegular": {"day": last[0], "price": last[1]} if last else None,
        "change": change,
        "changePercent": percent,
        "lowest": min(price for _, price in regulars) if regulars else None,
        "highest": max(price for _, price in regulars) if regulars else None,
        "unitPrice": prices.unit_price(last[1], points[-1]["unitSize"]) if last and points else None,
    }


async def _entry(conn: aiosqlite.Connection, row: Any, images: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    rows = await conn.execute_fetchall(
        "SELECT * FROM watched_prices WHERE store = ? AND product_id = ? ORDER BY day DESC LIMIT ?", (row["store"], row["product_id"], MAX_POINTS)
    )
    points = [_point(found) for found in reversed(rows)]
    latest = points[-1] if points else None
    return {
        "store": row["store"],
        "productId": row["product_id"],
        "title": row["title"],
        "brand": row["brand"],
        "unitSize": row["unit_size"],
        "packaging": row["packaging"],
        "image": row["image"],
        "url": row["url"],
        "addedAt": row["added_at"],
        "checkedAt": row["checked_at"],
        "missingSince": row["missing_since"],
        "price": latest["price"] if latest else None,
        "regular": latest["regular"] if latest else None,
        "onOffer": latest["onOffer"] if latest else False,
        "offerLabel": latest["offerLabel"] if latest else None,
        "points": points,
        "changes": changes(points),
        "savedImages": images.get(row["product_id"], []),
        **_summary(points),
    }


async def entry(store: str, product_id: str) -> dict[str, Any]:
    async with _connect() as conn:
        row = await auth.fetchone(conn, "SELECT * FROM watched_products WHERE store = ? AND product_id = ?", (store, product_id))
        if not row:
            raise ValueError(tr("Dit product volg je niet", "You do not follow this product"))
        images = await prices._saved_images(conn, store, [product_id])
        return await _entry(conn, row, images)


async def listing(store: Any = None) -> dict[str, Any]:
    """De gevolgde producten (per winkel, op naam), elk met zijn punten, wat er veranderde en de samenvatting."""
    key = _store(store) if store else None
    async with _connect() as conn:
        rows = await _watched_ids(conn, key)
        images: dict[str, dict[str, list[dict[str, Any]]]] = {}
        for shop in {row["store"] for row in rows}:
            images[shop] = await prices._saved_images(conn, shop, [row["product_id"] for row in rows if row["store"] == shop])
        products = [await _entry(conn, row, images.get(row["store"], {})) for row in rows]
    return {"products": products, "stores": list(STORES), "max": MAX_WATCHED}


async def search(store: Any, query: Any, limit: Any = SEARCH_LIMIT) -> dict[str, Any]:
    """Zoek bij de winkel naar een product om te volgen. Het antwoord zegt per product of je het al volgt."""
    term = str(query or "").strip()
    if not term:
        return {"products": []}
    if looks_like_url(term):
        return await _search_url(term)
    key = _store(store)
    try:
        size = max(1, min(int(limit), 30))
    except (TypeError, ValueError):
        size = SEARCH_LIMIT
    found = await _search(key, term, size)
    await prices.record(key, found)
    watched = {row.split(":", 1)[1] for row in await keys() if row.startswith(f"{key}:")}
    products = []
    for product in found:
        point = snapshot(key, product)
        if point:
            products.append(
                {
                    **point,
                    "store": key,
                    "watched": point["productId"] in watched,
                    "unitPrice": prices.unit_price(point["regular"] or point["price"], point["unitSize"]),
                }
            )
    return {"products": products}


async def _search_url(link: str) -> dict[str, Any]:
    """Een geplakte productlink: haal precies dat product op, bij de winkel van de link (die kan een andere zijn dan de gekozen)."""
    parsed = parse_url(link)
    if not parsed:
        raise ValueError(
            tr(
                "Dit is geen productlink die MealieShopper kent. Plak een link van een product op ah.nl of jumbo.com, of zoek op naam",
                "This is not a product link MealieShopper knows. Paste a link to a product on ah.nl or jumbo.com, or search by name",
            )
        )
    key, product_id = parsed
    found = await fetch(key, {product_id: product_id})
    product = found.get(product_id)
    point = snapshot(key, product) if product else None
    if not point:
        raise ValueError(tr("Dit product is niet gevonden bij de winkel", "This product was not found at the store"))
    await prices.record(key, [product])
    watched = f"{key}:{product_id}" in set(await keys())
    unit = prices.unit_price(point["regular"] or point["price"], point["unitSize"])
    return {"products": [{**point, "store": key, "watched": watched, "unitPrice": unit}], "fromUrl": True, "store": key}


# --- de nachtelijke ronde ------------------------------------------------------------------------------------------


async def refresh(store: str | None = None, now: str | None = None) -> dict[str, int]:
    """Haal van elk gevolgd product (van één winkel, of alle) de gegevens van vandaag op. Geeft hoeveel er bijgewerkt zijn en hoeveel
    de winkel niet gaf; een winkel die faalt, telt als niet gevonden en laat de andere doorgaan."""
    stamp = now or _now()
    async with _connect() as conn:
        rows = await _watched_ids(conn, store)
    by_store: dict[str, dict[str, str]] = {}
    for row in rows:
        by_store.setdefault(row["store"], {})[row["product_id"]] = row["title"]
    updated = missing = 0
    for shop, wanted in by_store.items():
        try:
            found = await fetch(shop, wanted)
        except Exception:  # noqa: BLE001 - een winkel die faalt, laat de andere doorgaan
            log.warning("Gevolgde producten bij %s bijwerken mislukt", shop, exc_info=True)
            found = {}
        points = await _record(shop, {product_id: found[product_id] for product_id in wanted if product_id in found}, stamp)
        updated += len(points)
        gone = [product_id for product_id in wanted if product_id not in {point["productId"] for point in points}]
        missing += len(gone)
        if gone:
            async with _connect() as conn:
                await conn.executemany(
                    "UPDATE watched_products SET checked_at = ?, missing_since = COALESCE(missing_since, ?) WHERE store = ? AND product_id = ?",
                    [(stamp, stamp, shop, product_id) for product_id in gone],
                )
                await conn.commit()
    return {"updated": updated, "missing": missing}


async def refresh_nightly() -> dict[str, int] | None:
    """Voor de nachtelijke run: alle gevolgde producten bijwerken, als er iets te volgen is. Een fout laat de run doorgaan."""
    try:
        result = await refresh()
    except Exception:  # noqa: BLE001 - een extraatje bij de aanbiedingen
        log.exception("Gevolgde producten: de nachtelijke ronde mislukte")
        return None
    return result if result["updated"] or result["missing"] else None


# --- routes --------------------------------------------------------------------------------------------------------


def register_routes(app: Quart) -> None:
    async def action(call: Callable[[], Awaitable[Any]], error_text: str) -> Any:
        try:
            return jsonify(await call())
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:  # noqa: BLE001 - de winkels zijn onofficieel; toon de fout
            app.logger.exception("Watched products error")
            return jsonify({"error": str(exc) or error_text}), 424

    @app.get("/api/watched")
    async def watched_list():
        return await action(
            lambda: listing(request.args.get("store") or None), tr("Gevolgde producten laden mislukt", "Loading followed products failed")
        )

    @app.get("/api/watched/keys")
    async def watched_keys():
        return await action(lambda: _keys_payload(), tr("Gevolgde producten laden mislukt", "Loading followed products failed"))

    @app.get("/api/watched/search")
    async def watched_search():
        return await action(
            lambda: search(request.args.get("store"), request.args.get("q"), request.args.get("limit") or SEARCH_LIMIT),
            tr("Zoeken bij de winkel mislukt", "Searching the store failed"),
        )

    @app.post("/api/watched")
    async def watched_add():
        body = await request_body.json_object()
        user = await auth.current_user()

        async def run() -> dict[str, Any]:
            return await add(body.get("store"), body.get("productId"), body.get("product"), user["username"] if user else None)

        return await action(run, tr("Product volgen mislukt", "Following the product failed"))

    @app.delete("/api/watched/<store>/<product_id>")
    async def watched_remove(store: str, product_id: str):
        return await action(lambda: _removed(store, product_id), tr("Product loslaten mislukt", "Unfollowing the product failed"))

    @app.post("/api/watched/refresh")
    async def watched_refresh():
        body = await request_body.json_object()
        return await action(lambda: refresh(_store(body["store"]) if body.get("store") else None), tr("Bijwerken mislukt", "Updating failed"))


async def _keys_payload() -> dict[str, Any]:
    return {"keys": await keys()}


async def _removed(store: str, product_id: str) -> dict[str, Any]:
    return {"removed": await remove(store, product_id)}
