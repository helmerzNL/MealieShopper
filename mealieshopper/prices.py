"""Prijsgeschiedenis van producten: wanneer iets in de aanbieding was, wat het normaal kostte en wat je ervoor betaalde (#178).

Drie bronnen, per winkel en product samengevoegd:

- **aanbiedingen**: `offer_history` (zie `offers._record_history`), elke periode met de actieprijs, de prijs ervoor en het label;
- **normale prijs**: `price_observations`, één prijs per product per dag. Die wordt bijgehouden waar MealieShopper een product bij
  de winkel opzoekt (de controlelijst van het weekmenu, *Zoek een ander product*, het zoeken per winkel en de MCP-tools). Daarnaast
  telt de prijs vóór de actie van een aanbieding mee, en de normale prijs die in een bestelling staat;
- **wat je betaalde**: je bestellingen en kassabonnen (AH, Jumbo, Picnic en Crisp), gelezen uit de kopie die de app daar al van
  bewaart. Zo staat alles wat je ooit via de app ophaalde meteen in de geschiedenis; een aparte import is niet nodig.

Lezen vraagt nooit iets aan een winkel. Het bijhouden laat nooit een zoekopdracht mislukken: een fout wordt gelogd en verder genegeerd.

`history(..., bought=True)` toont alleen de producten die je minstens één keer kocht (de schakelaar *Alleen eerder gekocht*). Van een
aanbieding die je kocht bewaart `product_images.py` de afbeelding (in `product_images`, hooguit 1024 pixels breed, met de naam van toen);
`history` geeft die per product in `savedImages`, om later te vergelijken als het product anders gaat heten.

Daarbovenop (deel 2 van #178): de **prijs per kilo, liter of stuk** (`unit_price`), een **oordeel bij elke aanbieding**
(`annotate`: is de actieprijs lager dan de gewone prijs (`regular_price.choose`), hoe vaak was het product dit jaar in de aanbieding,
en is een andere verpakking van hetzelfde product per kilo goedkoper) en **verpakkingen vergelijken** (`compare`: één zoekopdracht
bij de winkel naar hetzelfde product in andere maten). Ideeën en testgevallen voor de prijs per eenheid komen uit het userscript
*Albert Heijn kortingspercentages en prijs per kg/ml/etc.* van wolph (BSD-3-Clause); de code is eigen werk.
"""
from __future__ import annotations

import json
import logging
import math
import re
import weakref
from collections import Counter
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Iterable, NamedTuple

import aiosqlite

from . import ah, ah_lists, ah_orders, auth, deals, line_offers, name_keys, offer_categories, offer_ranking, offers, packs, price_import, product_links, shared_links
from .i18n import tr
from .regular_price import Chosen, RegularPoint, choose

log = logging.getLogger(__name__)

STORES = offers.STORES
MAX_LIMIT = 200
PUBLIC_OBSERVED_UNKNOWN: str = "public_observed_unknown"
# De gewone prijs per week van ahprijsindex.nl (`ah_price_index.py`). Telt als gemeten prijs in de geschiedenis en het oordeel,
# niet in de ladder van laagste prijzen (`_history_points`).
PRICE_INDEX: str = "ahprijsindex"

SCHEMA = """
-- De versie van de catalogus van de prijzen (`_catalogue_version`): triggers op de tabellen eronder hogen hem op. `token` is
-- willekeurig per database, zodat een nieuwe of teruggezette database niet bij een versie begint die al in de cache staat.
CREATE TABLE IF NOT EXISTS catalogue_changes (id INTEGER PRIMARY KEY CHECK (id = 1), token TEXT NOT NULL, version INTEGER NOT NULL);
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
CREATE INDEX IF NOT EXISTS price_observations_day ON price_observations (store, day);

-- De afbeelding van een aanbieding die je minstens één keer kocht, verkleind tot hooguit 1024 pixels breed (zie product_images.py).
-- `title` is de naam die het product had toen de afbeelding werd bewaard: zo zie je later of het hetzelfde product is, ook als de
-- naam veranderde. Dezelfde afbeelding (`digest`, de SHA-256 van `data`) staat er per product één keer in.
CREATE TABLE IF NOT EXISTS product_images (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  store TEXT NOT NULL,
  product_id TEXT NOT NULL,
  title TEXT NOT NULL,
  source_url TEXT NOT NULL,
  digest TEXT NOT NULL,
  content_type TEXT NOT NULL,
  width INTEGER NOT NULL,
  height INTEGER NOT NULL,
  data BLOB NOT NULL,
  saved_at TEXT NOT NULL,
  UNIQUE (store, product_id, digest)
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
_REGULAR_RANK = {"search": 0, "order": 1, "import": 1, "receipt": 1, "offer": 2}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@asynccontextmanager
async def _connect() -> AsyncIterator[aiosqlite.Connection]:
    """Een verbinding met het schema. Sluit zonder commit, zoals `auth.connect`.

    Eerst het schema van de aanbiedingen en zijn migraties (`offers.prepare`, het maakt ook `offer_history` aan), dan dit schema en
    `product_links.ensure` (de tabel en de SQL-functie `ms_fold`) en `shared_links.ensure` (de afgewezen gedeelde koppelingen)."""
    async with auth.connect() as conn:
        await offers.prepare(conn)
        await auth.ensure_schema(conn, "prices", SCHEMA)
        yield await shared_links.ensure(await product_links.ensure(conn))


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


async def record(store: str, products: Iterable[Any], source: str = "search", now: str | None = None) -> int:
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
        async with _connect() as conn:
            await conn.executemany(
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
            await conn.commit()
        return len(rows)
    except Exception:  # noqa: BLE001 - het bijhouden mag een zoekopdracht nooit laten mislukken
        log.warning("Prijzen bijhouden mislukt voor %s", store, exc_info=True)
        return 0


def recorded(store: str, search: Callable[..., Awaitable[list[dict[str, Any]]]]) -> Callable[..., Awaitable[list[dict[str, Any]]]]:
    """`search(query, limit)` van een winkel, die daarna de normale prijs van de gevonden producten bewaart."""

    async def wrapper(query: Any, limit: int = 5) -> list[dict[str, Any]]:
        products: list[dict[str, Any]] = await search(query, limit)
        await record(store, products)
        return products

    return wrapper


# --- lezen ---------------------------------------------------------------------------------------------------------


_TABLES_SQL = "SELECT name FROM sqlite_master WHERE type = 'table'"

# De tabellen per verbinding, een keer gelezen: een verzoek vraagt ze tot vijftien keer (`_purchase_sources` per bron). Een verbinding
# van `_connect` heeft haar schema al voordat iemand dit vraagt, en de tabellen van een winkel komen op een andere verbinding erbij.
_TABLE_CACHE: weakref.WeakKeyDictionary[aiosqlite.Connection, set[str]] = weakref.WeakKeyDictionary()


async def _tables(conn: aiosqlite.Connection) -> set[str]:
    tables: set[str] | None = _TABLE_CACHE.get(conn)
    if tables is None:
        tables = _TABLE_CACHE[conn] = {row["name"] for row in await conn.execute_fetchall(_TABLES_SQL)}
    return tables


def native_id(store: str, product_id: str) -> Any:
    """Het product-id zoals de tabel van de winkel het bewaart: AH als INTEGER, de andere winkels als TEXT. Zo vergelijkt SQL de kolom
    zonder CAST met de waarde en doet de index op `product_id` het werk (SQLite past anders de affiniteit van de kolom toe, Postgres
    weigert een tekst naast een getal)."""
    return int(product_id) if store == "ah" and product_id.isdigit() else product_id


def _purchase_sources(tables: set[str], store: str) -> list[str]:
    """De SQL van de aankoopbronnen van een winkel waarvan de tabellen bestaan (`tables`, van `_tables`: een winkel
    die je nooit koppelde heeft ze niet)."""
    return [sql for needed, sql in _PURCHASES.get(store, ()) if set(needed) <= tables]


def _fold_name(value: Any) -> str:
    """Een naam om op te vergelijken (`product_links.fold`): ook je eigen koppelingen op naam gebruiken deze vouw."""
    return product_links.fold(value)


def _without_house_brand(store: str, name: str) -> str | None:
    """De naam zonder het huismerk vooraan ("ah bloemkool" -> "bloemkool"): een kassabon laat "AH" vaak weg. Alleen bij AH."""
    return name[3:] if store == "ah" and name.startswith("ah ") else (name if store == "ah" else None)


# Een bestelling van AH of Jumbo die nog niet geleverd is, telt niet als aankoop (`_PURCHASES`), maar het product heeft er wel
# zijn naam en zijn prijs.
_ORDER_NAMES_SQL = """
SELECT CAST(product_id AS TEXT) AS product_id, title,
       CASE WHEN COALESCE(bonus, 0) = 0 THEN price ELSE price_before_bonus END AS price
FROM {store}_order_items WHERE product_id IS NOT NULL AND CAST(product_id AS TEXT) NOT IN ('', '0')
"""
# Een artikelnummer van de kassabon dat AH aan de webwinkel koppelde: de naam uit de webwinkel, met de prijzen op de bon.
_RECEIPT_NAMES_SQL = """
SELECT CAST(p.product_id AS TEXT) AS product_id, p.title AS title,
       COALESCE(l.price, CASE WHEN l.quantity > 0 THEN round(l.amount / l.quantity, 2) END) AS price
FROM ah_receipt_products p LEFT JOIN ah_receipt_items l ON l.product_id = p.receipt_product_id
WHERE p.product_id IS NOT NULL AND COALESCE(p.title, '') != ''
"""
_PRICE_CHUNK = 500  # zoveel product-id's per query: ruim onder de grens van SQLite voor het aantal parameters
# Wanneer de prijs tussen producten met dezelfde naam kiest (`_nearest_priced`), als verhouding tussen twee prijzen:
_PRICE_NEAR = math.log(1.5)  # verder dan dit van elke bekende prijs (een krat naast een fles): de prijs zegt niets
_PRICE_CLEAR = math.log(1.15)  # zoveel dichterbij dan het eerste product met die naam, anders blijft het daarbij


def _name_sources(tables: set[str], store: str) -> list[tuple[str, tuple[Any, ...]]]:
    """De bronnen van `_name_index`, elk met `product_id`, `title` en `price` (de gewone prijs, als de bron die kent). In volgorde:
    de aanbiedingen, de bewaarde prijzen en de aankopen, en als laatste wat alleen een naam geeft: een bestelling die nog open
    staat en een bonkoppeling van AH. Bij dezelfde naam wint de eerste bron (`_add_names`)."""
    sources: list[tuple[str, tuple[Any, ...]]] = [
        ("SELECT product_id, title, price_before AS price FROM offer_history WHERE store = ?", (store,)),
        ("SELECT product_id, title, price FROM price_observations WHERE store = ?", (store,)),
    ] + [(f"SELECT product_id, title, COALESCE(regular, paid) AS price FROM ({sql})", ()) for sql in _purchase_sources(tables, store)]
    if store in ("ah", "jumbo") and f"{store}_order_items" in tables:
        sources.append((_ORDER_NAMES_SQL.format(store=store), ()))
    if store == "ah" and {"ah_receipt_products", "ah_receipt_items"} <= tables:
        sources.append((_RECEIPT_NAMES_SQL, ()))
    return sources


def _add_names(index: dict[str, str], rows: Iterable[Any]) -> None:
    for row in rows:
        if row["product_id"] and row["title"]:
            index.setdefault(_fold_name(row["title"]), str(row["product_id"]))


async def _name_rows(conn: aiosqlite.Connection, sources: list[tuple[str, tuple[Any, ...]]]) -> list[Any]:
    """Elk paar (product, naam) uit de bronnen van `_name_sources`, in hun volgorde."""
    rows: list[Any] = []
    for sql, args in sources:
        rows += await conn.execute_fetchall(f"SELECT product_id, title FROM ({sql}) GROUP BY product_id, title", args)
    return rows


async def _name_index(conn: aiosqlite.Connection, store: str) -> dict[str, str]:
    """Naam (gevouwen: kleine letters, zonder accenten) -> product-id, uit de bronnen van `_name_sources`."""
    index: dict[str, str] = {}
    _add_names(index, await _name_rows(conn, _name_sources(await _tables(conn), store)))
    return index


class _Names(NamedTuple):
    """De namen van een winkel om een aankoop zonder product-id aan een product te koppelen (`_link_receipt_rows`)."""

    index: dict[str, str]  # `_name_index`: naam -> het eerste product met die naam
    # Een naam waar meer dan een product bij hoort ("AH Sweet Palermo rode puntpaprika": 2 stuks en een zak van 500 gram)
    # -> per product de gewone prijzen die we ervan kennen. De prijs van de aankoop kiest dan (`_nearest_priced`).
    shared: dict[str, dict[str, tuple[float, ...]]]


async def _known_prices(conn: aiosqlite.Connection, sources: list[tuple[str, tuple[Any, ...]]], ids: list[str]) -> dict[str, set[float]]:
    """Per product de gewone prijzen uit de bronnen van `_name_sources`."""
    found: dict[str, set[float]] = {}
    for start in range(0, len(ids), _PRICE_CHUNK):
        chunk: list[str] = ids[start : start + _PRICE_CHUNK]
        for sql, args in sources:
            query: str = f"SELECT DISTINCT product_id, price FROM ({sql}) WHERE product_id IN ({_in(chunk)}) AND price > 0"
            for row in await conn.execute_fetchall(query, (*args, *chunk)):
                found.setdefault(str(row["product_id"]), set()).add(float(row["price"]))
    return found


async def _names(conn: aiosqlite.Connection, store: str) -> _Names:
    sources: list[tuple[str, tuple[Any, ...]]] = _name_sources(await _tables(conn), store)
    rows: list[Any] = await _name_rows(conn, sources)
    index: dict[str, str] = {}
    _add_names(index, rows)
    products: dict[str, set[str]] = {}
    for row in rows:
        if row["product_id"] and row["title"]:
            products.setdefault(_fold_name(row["title"]), set()).add(str(row["product_id"]))
    several: dict[str, set[str]] = {name: ids for name, ids in products.items() if len(ids) > 1}
    known: dict[str, set[float]] = await _known_prices(conn, sources, sorted(set().union(*several.values()))) if several else {}
    shared: dict[str, dict[str, tuple[float, ...]]] = {
        name: {product_id: tuple(sorted(known.get(product_id, ()))) for product_id in sorted(ids)} for name, ids in several.items()
    }
    return _Names(index, shared)


def _nearest_priced(choices: dict[str, tuple[float, ...]], row: Any, first: str | None) -> str | None:
    """Van de producten met dezelfde naam het product waarvan een bekende prijs het dichtst bij de gewone prijs van de aankoop
    ligt (naar verhouding: 1,55 hoort bij 1,69 en niet bij 3,49). None als de prijs niet kiest, dan blijft het bij `first` (het
    eerste product met die naam): de aankoop of de producten hebben geen prijs, geen prijs ligt in de buurt (`_PRICE_NEAR`), of
    `first` ligt bijna even dichtbij (`_PRICE_CLEAR`)."""
    price: float | None = _number(row["regular"]) or _number(row["paid"])
    if not price or price <= 0:
        return None
    distance: dict[str, float] = {
        product_id: min(abs(math.log(price / known)) for known in prices) for product_id, prices in choices.items() if prices
    }
    if not distance:
        return None
    best: str = min(distance, key=lambda product_id: (distance[product_id], product_id))
    if distance[best] > _PRICE_NEAR or (first in distance and distance[first] - distance[best] < _PRICE_CLEAR):
        return None
    return best


def _match_name(index: dict[str, str], bare: dict[str, str], store: str, name: str) -> str | None:
    """Het product bij een naam: precies (gevouwen), of anders zonder het huismerk vooraan aan beide kanten ("BLOEMKOOL" en
    "AH Bloemkool"). Dat laatste alleen als die kale naam bij precies een product hoort."""
    if name in index:
        return index[name]
    stripped = _without_house_brand(store, name)
    return (bare.get(stripped) if stripped else None) or bare.get(_near_name(name))


def _near_name(name: str) -> str:
    """Een naam om bijna-gelijke namen te herkennen, in de index van `_bare_index`. Zie `name_keys.near`."""
    return name_keys.near(name)


def _words(name: str) -> set[str]:
    """De woorden van een (gevouwen) naam die iets zeggen: zonder het huismerk en zonder woorden van een of twee letters."""
    return {word for word in name.split() if len(word) >= 3 and word != "ah"}


def _names_agree(receipt_name: str, titles: Iterable[str]) -> bool:
    """Deelt de naam op de bon minstens een woord met een naam van het product? Een bon kort woorden af ("RUNDERGEH"), dus een
    woord dat met het andere begint telt ook. "AH RIJST BASMATI" en "AH Basmati rijst" wel, "AH ROOKWORST" en "Dr. Oetker
    Ristorante pizza hawaii 3-pack" niet."""
    mine = _words(receipt_name)
    return any(a.startswith(b) or b.startswith(a) for title in titles for a in mine for b in _words(title))


def _bare_index(index: dict[str, str], store: str) -> dict[str, str]:
    """Kale naam -> product, voor de namen waar maar een product bij hoort. Ook de bijna-gelijke naam (`_near_name`) -> product,
    met dezelfde eis."""
    found: dict[str, set[str]] = {}
    for name, product_id in index.items():
        stripped = _without_house_brand(store, name)
        if stripped:
            found.setdefault(stripped, set()).add(product_id)
        found.setdefault(_near_name(name), set()).add(product_id)
    return {name: next(iter(ids)) for name, ids in found.items() if len(ids) == 1}


_RECEIPT_PRODUCTS_SQL = "SELECT * FROM ah_receipt_products WHERE product_id IS NOT NULL"


class _Linked(NamedTuple):
    """Het webwinkelproduct bij een artikelnummer van de kassabon (`ah_receipt_products`)."""

    product_id: str
    title: str
    brand: str | None  # het merk en de inhoud: None als ze (nog) niet zijn opgezocht of AH ze niet geeft
    unit_size: str | None


def _receipt_product_map(rows: Iterable[Any]) -> dict[str, _Linked]:
    found: dict[str, _Linked] = {}
    for row in rows:
        columns = row.keys()  # `brand` en `unit_size` zijn er pas als ah_orders zijn schema heeft bijgewerkt
        brand: str | None = (row["brand"] if "brand" in columns else None) or None
        unit_size: str | None = (row["unit_size"] if "unit_size" in columns else None) or None
        found[str(row["receipt_product_id"])] = _Linked(str(row["product_id"]), row["title"] or "", brand, unit_size)
    return found


async def _receipt_products(conn: aiosqlite.Connection) -> dict[str, _Linked]:
    """Artikelnummer op de bon -> het product van de webwinkel (`_Linked`), voor zover AH ze koppelde (zie
    `ah_orders.resolve_receipt_products`)."""
    if "ah_receipt_products" not in await _tables(conn):
        return {}
    return _receipt_product_map(await conn.execute_fetchall(_RECEIPT_PRODUCTS_SQL))


# Aankopen uit bevestigingsmails (zie price_import.py), in dezelfde vorm als de regels van een kassabon: zonder id van de webwinkel.
_IMPORTED_SQL = """
SELECT NULL AS receipt_product_id, title, NULL AS brand, NULL AS unit_size, NULL AS image, day, quantity, paid, regular,
       'import' AS source, order_ref AS ref
FROM imported_purchases WHERE store = ? AND day <= ?
"""


def _not_in_account(rows: Iterable[Any], numbers: set[str], days: set[str]) -> list[Any]:
    """De ingelezen aankopen zonder die van een bestelling uit je account (`price_import.account_orders`)."""
    return [row for row in rows if row["ref"] not in numbers and row["day"] not in days]


async def _imported_rows(conn: aiosqlite.Connection, store: str, today: str) -> list[Any]:
    """De ingelezen aankopen van een winkel, zonder de bestellingen die de app inmiddels ook uit je account heeft (zelfde
    bestelnummer, of een bestelling op dezelfde dag): die tellen anders dubbel."""
    if "imported_purchases" not in await _tables(conn):
        return []
    return _not_in_account(await conn.execute_fetchall(_IMPORTED_SQL, (store, today)), *await price_import.account_orders(conn, store))


def _shared_link_row(row: Any, link: shared_links.SharedLink) -> dict[str, Any]:
    """Een ingelezen aankoop met een koppeling uit het gedeelde bestand (`shared_links`): het product, met de naam en de inhoud uit
    het bestand. De naam uit de mail blijft vindbaar (`receipt_name`), `shared` laat het scherm Klopt niet tonen."""
    return {
        **dict(row), "product_id": link.product_id, "title": link.title, "unit_size": link.unit_size, "receipt_name": row["title"], "shared": True,
        "linked_by": "shared",
    }


def _own_link_row(row: Any, link: dict[str, Any]) -> dict[str, Any]:
    """Een bonregel of ingelezen aankoop met je eigen koppeling (#238): het product, met de naam, het merk, de inhoud en de foto
    die je koos. De naam op de bon of in de mail blijft vindbaar (`receipt_name`)."""
    return {
        **dict(row), "product_id": link["productId"], "title": link["title"], "brand": link["brand"], "unit_size": link["unitSize"],
        "image": link["image"], "receipt_name": row["title"], "linked_by": "own",
    }


_DATED_RECEIPTS_SQL = f"SELECT * FROM ({_RECEIPTS_SQL}) WHERE day <= ?"
_RECEIPT_TABLES = frozenset({"ah_receipt_items", "ah_receipts"})


async def _receipt_rows(conn: aiosqlite.Connection, store: str, today: str) -> list[dict[str, Any]]:
    """De regels van de kassabonnen (alleen AH) en de ingelezen aankopen (`_imported_rows`), elk met het product waar ze bij
    horen in `product_id`.

    Je eigen koppeling (`product_links`, #238) gaat altijd voor: eerst op het artikelnummer van de bon, dan op de naam. Pas als er
    geen is, geldt wat hieronder staat.

    Het id op een bon is AH's artikelnummer; is dat gekoppeld aan een product van de webwinkel (`ah_orders.resolve_receipt_products`,
    "BLOEMKOOL" 20960 -> "AH Bloemkool" 4183), dan is het dat product. Is het id op de bon bekend uit de aanbiedingen, de bewaarde
    prijzen of de bestellingen, dan is het dat product. Anders telt de naam:
    "AH RUNDERGEHAKT" hoort bij "AH Rundergehakt". Past de naam ook nergens, dan is het id van de bon het product, of, zonder id, de
    naam zelf (`bon:ah rundergehakt`), zodat de aankoop altijd te vinden is.

    Het koppelen zelf is `_link_receipt_rows`.
    """
    rows = list(await _imported_rows(conn, store, today))
    if store == "ah" and _RECEIPT_TABLES <= await _tables(conn):
        rows += await conn.execute_fetchall(_DATED_RECEIPTS_SQL, (today,))
    if not rows:
        return []
    shared: dict[str, shared_links.SharedLink] = shared_links.active(store, await shared_links.rejected(conn, store))
    return _link_receipt_rows(store, rows, await _names(conn, store), await _receipt_products(conn), await product_links.load(conn, store), shared)


def _link_receipt_rows(
    store: str, rows: list[Any], names: _Names, linked: dict[str, _Linked], mine: product_links.Links,
    shared: dict[str, shared_links.SharedLink] | None = None,
) -> list[dict[str, Any]]:
    """Het koppelen van `_receipt_rows`, zonder database: de regels, de namen (`_names`), de koppelingen van AH
    (`_receipt_products`), je eigen koppelingen (`product_links.load`) en de gedeelde (`shared_links.active`).

    Een gedeelde koppeling geldt alleen voor een ingelezen aankoop, na je eigen koppeling en voor al het andere. Elke regel zegt in
    `linked_by` hoe hij bij zijn product kwam: `own` (je eigen koppeling), `shared` (het gedeelde bestand), `article` (de koppeling van
    AH op het artikelnummer), `id`, `name` of `fallback` (`_guessed_row`). De prijsgeschiedenis toont ze allemaal, het oordeel telt
    alleen de bevestigde (`CONFIRMED_LINKS`, zie `_receipt_points`)."""
    bare = _bare_index(names.index, store)
    webshop: dict[str, _Linked] = {link.product_id: link for link in linked.values()}
    titles: dict[str, list[str]] = {}
    for title, product_id in names.index.items():
        titles.setdefault(product_id, []).append(title)
    found: list[dict[str, Any]] = []
    for row in rows:
        own, name = row["receipt_product_id"], _fold_name(row["title"])
        link = mine.find(own if row["source"] == "receipt" else None, name)
        if link:
            found.append(_own_link_row(row, link))
            continue
        shared_link = shared.get(name_keys.key(row["title"])) if shared and row["source"] == "import" else None
        if shared_link:
            found.append(_shared_link_row(row, shared_link))
        elif own in linked:
            found.append(_article_row(row, linked[own]))
        else:
            found.append(_guessed_row(store, row, names, bare, titles, webshop))
    return found


def _article_row(row: Any, shop: _Linked) -> dict[str, Any]:
    """Een bonregel gekoppeld via het artikelnummer (`ah_orders.resolve_receipt_products`): de echte naam uit de webwinkel ("Red Band
    Winegums original" in plaats van "RED BAND"). De naam op de bon blijft vindbaar (`receipt_name`). Het merk en de inhoud komen ook
    van de koppeling: een bon heeft ze niet."""
    return {
        **dict(row), "product_id": shop.product_id, "title": shop.title or row["title"], "brand": shop.brand,
        "unit_size": shop.unit_size, "receipt_name": row["title"], "linked_by": "article",
    }


def _guessed_row(
    store: str, row: Any, names: _Names, bare: dict[str, str], titles: dict[str, list[str]], webshop: dict[str, _Linked]
) -> dict[str, Any]:
    """Een regel zonder bevestigde koppeling (`_link_receipt_rows`), met `linked_by`: `id` (het nummer op de bon is ook het id van
    een product in de webwinkel), `name` (op naam of bijna-naam, zonder verpakking) of `fallback` (bij geen product, `bon:` of
    `import:`). Geen van drie is een bron voor het oordeel (`CONFIRMED_LINKS`).

    Het id op een bon is AH's artikelnummer, dat toevallig gelijk kan zijn aan het id van een ander product in de webwinkel
    ("AH ROOKWORST" 627748 en "Dr. Oetker Ristorante pizza hawaii 3-pack" 627748). Een bekend id telt dus alleen als de namen
    minstens een woord delen. Hoort het id bij een ander product, dan valt het ook als laatste keus af."""
    own, name = row["receipt_product_id"], _fold_name(row["title"])
    fallback: str = f"import:{name}" if row["source"] == "import" else f"bon:{name}"
    same_id: bool = bool(own) and own in titles and _names_agree(name, titles[own])
    other_product: bool = bool(own) and own in titles and not same_id
    matched: str | None = _match_name(names.index, bare, store, name)
    if name in names.shared:  # twee producten met precies deze naam: de prijs kiest
        matched = _nearest_priced(names.shared[name], row, matched) or matched
    key = own if same_id else (matched or (None if other_product else own) or fallback)
    # Een bonregel met een artikelnummer dat (nog) bij geen enkel product hoort: die kan `history` meteen laten opzoeken.
    # Op naam bij een product dat de app van de bonkoppeling kent: dan ook met het merk en de inhoud van daar.
    known: dict[str, Any] = {"brand": webshop[key].brand, "unit_size": webshop[key].unit_size} if key in webshop and not row["unit_size"] else {}
    linked_by: str = "id" if own and key == own else ("name" if key == matched else "fallback")
    return {
        **dict(row), **known, "product_id": key, "lookup": row["source"] == "receipt" and bool(own) and key in (own, fallback),
        "linked_by": linked_by,
    }


async def _product_details(conn: aiosqlite.Connection, store: str, ids: list[str]) -> dict[str, dict[str, Any]]:
    """Wat de app van deze producten weet: `title`, `brand`, `unitSize` en `image`, per veld de nieuwste waarde uit je bestellingen,
    dan de bewaarde prijzen, dan de aanbiedingen. Zonder foto van de winkel de nieuwste bewaarde afbeelding (`product_images`)."""
    found: dict[str, dict[str, Any]] = {product_id: {} for product_id in ids}
    if not ids:
        return found
    sources = [(f"SELECT product_id, title, brand, unit_size, image, day FROM ({sql})", ()) for sql in _purchase_sources(await _tables(conn), store)] + [
        ("SELECT product_id, title, brand, unit_size, image, day FROM price_observations WHERE store = ?", (store,)),
        ("SELECT product_id, title, brand, unit_size, image, last_seen AS day FROM offer_history WHERE store = ?", (store,)),
    ]
    fields = (("title", "title"), ("brand", "brand"), ("unitSize", "unit_size"), ("image", "image"))
    for sql, args in sources:
        for row in await conn.execute_fetchall(f"SELECT * FROM ({sql}) WHERE product_id IN ({_in(ids)}) ORDER BY day DESC", (*args, *ids)):
            details = found[str(row["product_id"])]
            for key, column in fields:
                if not details.get(key) and row[column]:
                    details[key] = row[column]
    for linked in (await _receipt_products(conn)).values() if store == "ah" else ():  # als laatste: wat de bonkoppeling weet
        details = found.get(linked.product_id)
        for key, value in (("title", linked.title), ("brand", linked.brand), ("unitSize", linked.unit_size)) if details is not None else ():
            if not details.get(key) and value:
                details[key] = value
    missing = [product_id for product_id, details in found.items() if not details.get("image")]
    for product_id, images in (await _saved_images(conn, store, missing) if missing else {}).items():
        found[product_id]["image"] = images[0]["url"]
    return found


async def link_names(store: str, names: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Per naam uit een bevestigingsmail (`imported_purchases.title`) het product van de winkel waar hij bij hoort, met wat de app
    ervan weet: {"productId", "title", "brand", "unitSize", "image"}. Dezelfde koppeling op naam als de Prijsgeschiedenis
    (`_receipt_rows`), dus de link naar de prijsgeschiedenis komt bij hetzelfde product uit. Een naam die nergens bij past, ontbreekt.
    Zo ziet een ingelezen bestelling eruit als een bestelling uit je account (#229).

    Je eigen koppeling op naam (#238) gaat voor; die naam heeft ook `link` ({"kind", "key"}), zodat het scherm hem kan wijzigen of
    weghalen. Daarna komt het gedeelde bestand (`shared_links`): die naam heeft `shared` True, zodat het scherm Klopt niet kan tonen.
    Wat de app van het product weet, gaat voor wat in de koppeling staat (een nieuwere foto of naam van de winkel)."""
    names = {name for name in names if name}
    if not names:
        return {}
    async with _connect() as conn:
        mine = await product_links.load(conn, store)
        own = {name: link for name in names if (link := mine.names.get(_fold_name(name)))}
        shared_all = shared_links.active(store, await shared_links.rejected(conn, store))
        shared = {name: shared_all[key] for name in names if name not in own and (key := name_keys.key(name)) in shared_all}
        index = await _name_index(conn, store)
        bare = _bare_index(index, store)
        matched = {name: link["productId"] for name, link in own.items()}
        matched.update({name: link.product_id for name, link in shared.items()})
        matched.update({
            name: product_id for name in names if name not in own and name not in shared
            and (product_id := _match_name(index, bare, store, _fold_name(name)))
        })
        details = await _product_details(conn, store, sorted(set(matched.values())))
    found: dict[str, dict[str, Any]] = {}
    for name, product_id in matched.items():
        known = {key: value for key, value in details.get(product_id, {}).items() if value}
        link = own.get(name)
        if link:
            saved = {"title": link["title"], "brand": link["brand"], "unitSize": link["unitSize"], "image": link["image"]}
            known = {**{key: value for key, value in saved.items() if value}, **known, "link": {"kind": link["kind"], "key": link["key"]}}
        elif name in shared:
            saved = {"title": shared[name].title, "unitSize": shared[name].unit_size}
            known = {**{key: value for key, value in saved.items() if value}, **known, "shared": True}
        found[name] = {"productId": product_id, **known}
    return found


def _loose_name(key: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Een losse naam van `loose_import_names` uit zijn ingelezen aankopen (rijen van de catalogus)."""
    spellings: Counter[str] = Counter(" ".join(str(row["title"]).split()) for row in rows)
    years: list[int] = [int(str(row["day"])[:4]) for row in rows]
    paid: list[float] = [value for row in rows if (value := _number(row["paid"])) is not None]
    return {
        "key": key, "name": sorted(spellings.items(), key=lambda pair: (-pair[1], pair[0]))[0][0], "bought": len(rows),
        "firstYear": min(years), "lastYear": max(years), "paidMin": min(paid) if paid else None, "paidMax": max(paid) if paid else None,
        "spellings": sorted(spellings),
    }


async def loose_import_names(store: str) -> list[dict[str, Any]]:
    """De losse namen uit de bevestigingsmails van een winkel, voor de zoekronde (`loose_names`): de ingelezen aankopen die na je
    eigen koppeling, het gedeelde bestand en de naamherkenning (`_link_receipt_rows`) bij geen product horen, dus met een
    `product_id` dat met `import:` begint.

    Per sleutel (`name_keys.key`) een dict {key, name, bought, firstYear, lastYear, paidMin, paidMax, spellings}, de vaakst gekochte
    eerst. `bought` telt de aankopen, `paidMin` en `paidMax` zijn de prijs per stuk (None zonder prijs), `name` is de schrijfwijze
    die het vaakst in de mail stond en `spellings` zijn ze allemaal (een eigen koppeling geldt per gevouwen naam, `product_links`)."""
    async with _connect() as conn:
        catalogue: _Catalogue = await _catalogue(conn, store, _now()[:10])
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in catalogue.receipts:
        if row["source"] == "import" and str(row["product_id"]).startswith("import:"):
            groups.setdefault(name_keys.key(row["title"]), []).append(row)
    found: list[dict[str, Any]] = [_loose_name(key, rows) for key, rows in groups.items()]
    return sorted(found, key=lambda entry: (-entry["bought"], entry["name"]))


LINK_SEARCH_LIMIT = 10  # zoveel producten toont het venster Koppelen per zoekopdracht


def _price_now(product: dict[str, Any]) -> float | None:
    price = product.get("price")
    return _number(price.get("now") if isinstance(price, dict) else price)


async def link_candidates(store: Any, query: Any, limit: int = LINK_SEARCH_LIMIT) -> dict[str, Any]:
    """De producten van een winkel om een regel aan te koppelen (#238): een zoekopdracht bij die winkel, in één vorm voor alle
    winkels ({"productId", "title", "brand", "unitSize", "image", "price"}). Zoals elke zoekopdracht bewaart hij de normale prijs
    van wat hij vindt (`recorded`), dus het gekozen product staat daarna ook in de Prijsgeschiedenis."""
    key = _shop_store(store)
    term = " ".join(str(query or "").split())[:product_links.MAX_NAME_CHARS]
    if not term:
        return {"store": key, "query": "", "products": []}
    products = []
    for product in await recorded(key, _search_function(key))(term, max(1, min(int(limit), 30))):
        product_id, title = _product_id(product), str(product.get("title") or "").strip()
        if product_id and title:
            products.append({
                "productId": product_id, "title": title, "brand": product.get("brand") or None,
                "unitSize": product.get("unitSize") or None, "image": _image(product), "price": _price_now(product),
            })
    return {"store": key, "query": term, "products": products}


_BOUGHT_SQL = "SELECT DISTINCT product_id FROM ({sql}) WHERE day <= ? AND paid > 0"


def _add_bought(found: set[str], rows: Iterable[Any]) -> None:
    found.update(str(row["product_id"]) for row in rows if row["product_id"])


def _bought_receipts(found: set[str], receipts: list[dict[str, Any]]) -> set[str]:
    found.update(str(row["product_id"]) for row in receipts if row["product_id"] and _number(row["paid"]) is not None)
    return found


async def _bought(conn: aiosqlite.Connection, store: str, today: str, receipts: list[dict[str, Any]]) -> set[str]:
    """De producten die je minstens één keer kocht: met een betaalde prijs in een bestelling of op een kassabon (zoals `timesBought`)."""
    found: set[str] = set()
    for sql in _purchase_sources(await _tables(conn), store):
        _add_bought(found, await conn.execute_fetchall(_BOUGHT_SQL.format(sql=sql), (today,)))
    return _bought_receipts(found, receipts)


async def bought_ids(store: str, today: str | None = None) -> set[str]:
    """De product-id's van een winkel die je minstens één keer kocht (uit je bestellingen en kassabonnen)."""
    today = today or _now()[:10]
    async with _connect() as conn:
        catalogue = await _catalogue(conn, store, today)
        return _with_twins(await _bought(conn, store, today, catalogue.receipts), catalogue.twins)


def _with_twins(ids: set[str], twins: dict[str, tuple[str, ...]]) -> set[str]:
    """De nummers en de andere nummers van dezelfde producten: wat je onder het ene nummer kocht, kocht je ook van het andere."""
    return ids | {twin for product_id in ids for twin in twins.get(product_id, ())}


def _twin_rows(found: dict[str, list[Any]], product_id: str, twins: dict[str, tuple[str, ...]], order: Callable[[Any], Any]) -> list[Any]:
    """De rijen van een product en van zijn andere nummers samen, op `order`."""
    rows = [row for twin in twins.get(product_id, (product_id,)) for row in found.get(twin, [])]
    return sorted(rows, key=order) if product_id in twins else rows


async def _saved_images(conn: aiosqlite.Connection, store: str, ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    """De bewaarde afbeeldingen van deze producten (zonder de afbeelding zelf), de nieuwste eerst."""
    found: dict[str, list[dict[str, Any]]] = {}
    rows = await conn.execute_fetchall(
        f"""SELECT id, product_id, title, digest, width, height, saved_at FROM product_images
            WHERE store = ? AND product_id IN ({_in(ids)}) ORDER BY saved_at DESC, id DESC""",
        (store, *ids),
    )
    for row in rows:
        found.setdefault(row["product_id"], []).append({
            "url": image_url(row["id"], row["digest"]), "title": row["title"], "savedAt": row["saved_at"],
            "width": row["width"], "height": row["height"],
        })
    return found


def image_url(image_id: Any, digest: Any) -> str:
    """Het adres (binnen MealieShopper) van een bewaarde afbeelding. `v` verandert met de inhoud, dus de browser mag hem lang bewaren."""
    return f"/api/prices/images/{int(image_id)}?v={str(digest or '')[:16]}"


async def _unsynced(conn: aiosqlite.Connection, store: str) -> dict[str, int]:
    tables = await _tables(conn)
    counts = {"orders": 0, "receipts": 0}
    for kind, table, sql in _UNSYNCED.get(store, ()):
        if table in tables:
            counts[kind] += (await auth.fetchone(conn, sql))[0]
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
        # Spaties tellen niet: "redband" vindt ook "Red Band Winegums" (een bon schrijft het merk vaak aan elkaar).
        if self.words and not any(all(word in name or word in name.replace(" ", "") for word in self.words) for name in names):
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


def _candidates(products: dict[str, _Known], query: _Query, always: str | None = None) -> dict[str, str]:
    """Product-id -> de laatste dag dat we iets van het product weten, voor elk product uit de catalogus (`_known_products`) dat
    bij de zoekterm past, en `always` (een product-id) ook als de naam niet past."""
    return {product_id: known.last for product_id, known in products.items() if product_id == always or query.wants(known.names, known.sizes)}


class _Known(NamedTuple):
    """Wat de catalogus van een product weet om erop te zoeken: de laatste dag, de gevouwen namen en de verpakkingen, en om erop
    te filteren het merk en de categorie (leeg als geen bron ze noemt)."""

    last: str
    names: frozenset[str]
    sizes: frozenset[tuple[float, str]]
    brand: str = ""
    category: str = ""
    same: str = ""  # naam, merk en verpakking samen: twee productnummers met dezelfde waarde zijn hetzelfde product (`_twins`)


def _same_key(product_id: str, entry: dict[str, Any]) -> str:
    """Waaraan je ziet dat twee productnummers hetzelfde product zijn: dezelfde naam, hetzelfde merk en dezelfde verpakking. Leeg
    zonder naam of verpakking, en voor een folderproduct of een ingelezen aankoop: die hebben geen eigen nummer van de winkel."""
    if ":" in product_id or not entry["title"] or not entry["sizes"]:
        return ""
    return f"{_fold_name(entry['title'])}|{_fold_name(entry['brand'])}|{sorted(entry['sizes'])}"


def _twins(products: dict[str, _Known]) -> dict[str, tuple[str, ...]]:
    """Per productnummer de nummers van hetzelfde product (zichzelf erbij), alleen voor producten met meer dan een nummer. AH
    voert "AH Les pains triomphe heel" onder 230786 (de kassabon) en 446408 (de webwinkel en de bonus)."""
    groups: dict[str, list[str]] = {}
    for product_id, known in products.items():
        if known.same:
            groups.setdefault(known.same, []).append(product_id)
    return {product_id: tuple(sorted(group)) for group in groups.values() if len(group) > 1 for product_id in group}


async def _known_products(conn: aiosqlite.Connection, store: str, today: str, receipts: list[dict[str, Any]]) -> dict[str, _Known]:
    """Per product van een winkel de laatste dag dat we er iets van weten, en al zijn namen en verpakkingen samen (uit de
    aanbiedingen, de bewaarde prijzen, de bestellingen en de bonnen). Een aanbieding die nog moet beginnen, telt ook (met haar
    begindag): die staat nog niet in `offer_history`. Zonder zoekterm: die filtert `_candidates` daarna."""
    found: dict[str, dict[str, Any]] = {}

    def add(rows) -> None:
        for row in rows:
            product_id = row["product_id"]
            if not product_id:
                continue
            entry = found.setdefault(product_id, {"last": "", "names": set(), "sizes": set(), "title": "", "brand": ""})
            entry["last"] = max(entry["last"], str(row["last"] or "")[:10])
            entry["title"] = entry["title"] or str(row["title"] or "").strip()
            entry["brand"] = entry["brand"] or str(row["brand"] or "").strip()
            entry["names"].add(_fold_name(f"{row['title'] or ''} {row['brand'] or ''}"))
            size = _size_of(row["unit_size"], row["title"])
            if size:
                entry["sizes"].add(size)

    columns = "product_id, title, brand, unit_size"
    add(await conn.execute_fetchall(f"SELECT {columns}, MAX(last_seen) AS last FROM offer_history WHERE store = ? GROUP BY {columns}", (store,)))
    add(await conn.execute_fetchall(f"SELECT {columns}, MAX(day) AS last FROM price_observations WHERE store = ? GROUP BY {columns}", (store,)))
    for sql in _purchase_sources(await _tables(conn), store):
        add(await conn.execute_fetchall(f"SELECT {columns}, MAX(day) AS last FROM ({sql}) WHERE day <= ? GROUP BY {columns}", (today,)))
    add({**row, "last": row["day"]} for row in receipts)
    add(await conn.execute_fetchall(_UPCOMING_SQL.format(columns=f"{columns}, COALESCE(valid_from, ?) AS last"), (today, store, today, today, store)))
    for row in receipts:  # de naam op de bon van een gekoppelde regel blijft vindbaar ("RB PICOLIENT")
        if row.get("receipt_name") and row["product_id"] in found:
            found[row["product_id"]]["names"].add(_fold_name(row["receipt_name"]))
    # De categorie: die de winkel bij een aanbieding meegaf, anders die van de naam (zoals de aanbiedingen en de aankopen doen).
    stored: dict[str, str] = {
        row["product_id"]: row["category"]
        for row in await conn.execute_fetchall(
            "SELECT product_id, MAX(category) AS category FROM offer_history WHERE store = ? AND COALESCE(category, '') != '' GROUP BY product_id", (store,)
        )
    }
    return {
        product_id: _Known(
            entry["last"], frozenset(entry["names"]), frozenset(entry["sizes"]), entry["brand"],
            stored.get(product_id) or (offer_categories.offer_category(store, entry["title"]) if entry["title"] else ""),
            _same_key(product_id, entry),
        )
        for product_id, entry in found.items()
    }


# De aanbiedingen in de lokale kopie die (nog) niet in de geschiedenis staan: die nog moeten beginnen (de bonus van volgende
# week: `offers._started` zet ze pas in `offer_history` als ze gelden), en die al begonnen zijn maar nog geen open periode
# hebben (de nieuwe week begon om middernacht, de synchronisatie die hem vastlegt komt pas 's nachts). Parameters: winkel,
# vandaag, vandaag, winkel.
_UPCOMING_SQL = (
    "SELECT {columns} FROM offers WHERE store = ? AND (valid_from > ? OR (COALESCE(valid_until, '9999-12-31') >= ? "
    "AND product_id NOT IN (SELECT product_id FROM offer_history WHERE store = ? AND ended_at IS NULL)))"
)


# --- de catalogus: de gekoppelde bonregels en de namen van alle producten, bewaard tot er iets aan verandert ----------------

# De tabellen waaruit de catalogus put (`_receipt_rows`, `_known_products`). Op elk staat per bewerking een trigger die
# `catalogue_changes.version` ophoogt, welke code of verbinding de tabel ook wijzigt: een synchronisatie, een import, je eigen
# koppeling of een test die direct in SQLite schrijft.
CATALOGUE_TABLES: frozenset[str] = frozenset({table for sources in _PURCHASES.values() for needed, _ in sources for table in needed}) | {
    "offer_history", "offers", "price_observations", "imported_purchases", "ah_receipts", "ah_receipt_items", "ah_receipt_products",
    "product_links", "shared_link_rejections",
}
_CATALOGUE_OPS: tuple[str, ...] = ("INSERT", "UPDATE", "DELETE")
_CATALOGUE_TRIGGERS_SQL = "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'catalogue!_%' ESCAPE '!'"
_CATALOGUE_START_SQL = "INSERT INTO catalogue_changes (id, token, version) VALUES (1, lower(hex(randomblob(8))), 0) ON CONFLICT(id) DO NOTHING"


class _Catalogue(NamedTuple):
    receipts: list[dict[str, Any]]  # `_receipt_rows`: niet aanpassen, de volgende aanvraag krijgt dezelfde lijst
    products: dict[str, _Known]  # `_known_products`
    lines: line_offers.LineIndex  # `_line_index`: de folderproducten, om lijnacties bij een product te vinden
    twins: dict[str, tuple[str, ...]]  # `_twins`: de andere nummers van hetzelfde product


_FOLDER_PRODUCTS_SQL = "SELECT DISTINCT product_id, title FROM offer_history WHERE store = ? AND product_id LIKE 'folder:%' ORDER BY product_id"


async def _line_index(conn: aiosqlite.Connection, store: str) -> line_offers.LineIndex:
    """De folderproducten van een winkel (uit een prijsarchief, `folder:...`), om lijnacties bij een product te vinden."""
    rows = await conn.execute_fetchall(_FOLDER_PRODUCTS_SQL, (store,))
    return line_offers.LineIndex((row["product_id"], row["title"] or "") for row in rows)


# (database, winkel) -> (versie, dag, catalogus). Een winkel heeft er een; bij AH met tienduizenden producten scheelt hij per
# zoekopdracht in de prijsgeschiedenis en per lijst aanbiedingen het grootste deel van een seconde.
_CATALOGUES: dict[tuple[str, str], tuple[str, str, _Catalogue]] = {}


async def _catalogue_version(conn: aiosqlite.Connection) -> str:
    """De versie van de catalogus: verandert bij elke wijziging in `CATALOGUE_TABLES`. Ontbreekt een trigger (de eerste keer, een
    tabel die later is aangemaakt of opnieuw opgebouwd), dan maakt dit hem aan en hoogt het de versie zelf op: wat er daarvoor
    veranderde, telde nog niet."""
    tables = await _tables(conn)
    present = {row["name"] for row in await conn.execute_fetchall(_CATALOGUE_TRIGGERS_SQL)}
    wanted = {f"catalogue_{table}_{op.lower()}": (table, op) for table in sorted(CATALOGUE_TABLES & tables) for op in _CATALOGUE_OPS}
    missing = [name for name in wanted if name not in present]
    row = await auth.fetchone(conn, "SELECT token, version FROM catalogue_changes WHERE id = 1")
    if missing or row is None:
        await conn.execute(_CATALOGUE_START_SQL)
        for name in missing:
            table, op = wanted[name]
            await conn.execute(
                f"CREATE TRIGGER IF NOT EXISTS {name} AFTER {op} ON {table} BEGIN UPDATE catalogue_changes SET version = version + 1 WHERE id = 1; END"
            )
        await conn.execute("UPDATE catalogue_changes SET version = version + 1 WHERE id = 1")
        await conn.commit()
        row = await auth.fetchone(conn, "SELECT token, version FROM catalogue_changes WHERE id = 1")
    return f"{row['token']}:{row['version']}"


async def _catalogue(conn: aiosqlite.Connection, store: str, today: str) -> _Catalogue:
    """De catalogus van een winkel op een dag: uit de cache zolang de versie (`_catalogue_version`) en de dag gelijk zijn, anders
    opnieuw opgebouwd. Verandert er iets terwijl hij wordt opgebouwd, dan is de versie daarna anders en bouwt de volgende aanvraag
    hem opnieuw."""
    key = (str(auth.db_path()), store)
    version = await _catalogue_version(conn)
    found = _CATALOGUES.get(key)
    if found is not None and found[0] == version and found[1] == today:
        return found[2]
    receipts = await _receipt_rows(conn, store, today)
    products = await _known_products(conn, store, today, receipts)
    catalogue = _Catalogue(receipts, products, await _line_index(conn, store), _twins(products))
    _CATALOGUES[key] = (version, today, catalogue)
    return catalogue


async def _upcoming(conn: aiosqlite.Connection, store: str, ids: list[str], today: str) -> dict[str, dict[str, Any]]:
    """Per product de eerstvolgende aanbieding die nog moet beginnen, met wat de kaart van de aanbieding ook toont. Een aanbieding
    die al begon maar nog niet in de geschiedenis staat, telt `offers.history_of` als lopende periode."""
    found: dict[str, dict[str, Any]] = {}
    if not ids:
        return found
    rows = await conn.execute_fetchall(
        _UPCOMING_SQL.format(columns="*") + f" AND valid_from > ? AND product_id IN ({_in(ids)}) ORDER BY valid_from, personal",
        (store, today, today, store, today, *ids)
    )
    for row in rows:
        found.setdefault(str(row["product_id"]), {
            "validFrom": row["valid_from"], "validUntil": row["valid_until"], "price": row["price"], "priceBefore": row["price_before"],
            "promoLabel": row["promo_label"] or "", "personal": bool(row["personal"]), "title": row["title"], "brand": row["brand"],
            "unitSize": row["unit_size"], "image": row["image"], "url": row["url"],
        })
    return found


# Waar een product uit een link nog meer kan staan dan in de bronnen van de catalogus: de aanbiedingen van nu, al je bestellingen
# (ook een die nog niet geleverd is: de catalogus telt alleen geleverde) en bij AH de koppeling van een artikelnummer op de bon.
_PINNED_FIELDS: tuple[tuple[str, str], ...] = (("title", "title"), ("brand", "brand"), ("unitSize", "unit_size"), ("image", "image"))


async def _pinned(conn: aiosqlite.Connection, store: str, product_id: str, tables: set[str]) -> dict[str, Any] | None:
    """Naam, merk, verpakking en foto van een product uit een link (`pin`) dat nergens in de prijsgeschiedenis staat, of None als
    de app het product nergens kent. Zo opent een link van een aankomende bestelling of een bon het product zelf, ook zonder
    prijzen, in plaats van alleen andere producten met die naam."""
    sources: list[tuple[str, str, tuple[Any, ...]]] = [
        ("offers", "store = ? AND product_id = ?", (store, product_id)),
        (f"{store}_order_items", "product_id = ?", (native_id(store, product_id),)),
    ]
    if store == "ah":
        sources.append(("ah_receipt_products", "product_id = ?", (native_id(store, product_id),)))
    for table, where, args in sources:
        if table not in tables:
            continue
        row = await auth.fetchone(conn, f"SELECT * FROM {table} WHERE {where} ORDER BY rowid DESC LIMIT 1", args)
        if row is not None:
            columns = set(row.keys())
            return {key: row[column] for key, column in _PINNED_FIELDS if column in columns and row[column]}
    return None


def _in(ids: list[str]) -> str:
    return ",".join("?" * len(ids))


# Per product op dag, de volgorde van de primaire sleutel (winkel, product, dag): zo zoekt SQLite de producten op. Met alleen
# `ORDER BY day` koos het de index op (winkel, dag) en las het alle prijzen van de winkel om er een paar producten uit te halen.
OBSERVATIONS_SQL = "SELECT * FROM price_observations WHERE store = ? AND product_id IN ({marks}) ORDER BY product_id, day"


async def _observations(conn: aiosqlite.Connection, store: str, ids: list[str]) -> dict[str, list[Any]]:
    found: dict[str, list[Any]] = {}
    for row in await conn.execute_fetchall(OBSERVATIONS_SQL.format(marks=_in(ids)), (store, *ids)):
        found.setdefault(row["product_id"], []).append(row)
    return found


async def _purchases(conn: aiosqlite.Connection, store: str, ids: list[str], today: str, receipts: list[dict[str, Any]]) -> dict[str, list[Any]]:
    found: dict[str, list[Any]] = {}
    for sql in _purchase_sources(await _tables(conn), store):
        for row in await conn.execute_fetchall(f"SELECT * FROM ({sql}) WHERE product_id IN ({_in(ids)}) AND day <= ? ORDER BY day, ref", (*ids, today)):
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


def _product(store: str, product_id: str, offer: dict[str, Any] | None, observed: list[Any], bought: list[Any], today: str,
             images: list[dict[str, Any]] | None = None, upcoming: dict[str, Any] | None = None,
             known: dict[str, Any] | None = None, lines: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    product: dict[str, Any] = dict(offer) if offer else {
        "store": store, "productId": product_id, "episodes": [], "timesOnOffer": 0, "lowestPrice": None, "highestPrice": None,
        "regularPrice": None, "onOfferNow": False, "category": None, "firstSeen": None, "lastSeen": None,
    }
    # Wat een stuk bij elke periode kostte (`perPiece`): bij "2e halve prijs" noemt AH de gewone prijs als prijs, en de grafiek, de
    # laagste actieprijs en de kaart bij de aanbiedingen (`_effective`, `offer_ranking.year_low`) horen dezelfde prijs te noemen.
    product["episodes"] = [
        {**episode, "perPiece": _episode_effective({"promo_label": episode.get("promoLabel"), "price": episode.get("price"), "price_before": episode.get("priceBefore")})}
        for episode in product["episodes"]
    ]
    per_piece: list[float] = [episode["perPiece"] for episode in product["episodes"] if episode["perPiece"] is not None]
    if per_piece:
        product["lowestPrice"], product["highestPrice"] = min(per_piece), max(per_piece)
    # De folderacties voor de hele lijn (`_line_offers`), zonder de weken waarin het product zelf al in de aanbieding was.
    product["lineOffers"] = _without_own_weeks(lines or [], product["episodes"])
    product["timesInLineOffer"] = len(product["lineOffers"])
    # De aanbieding die nog moet beginnen (`_upcoming`): niet in de geschiedenis, wel op het scherm ("vanaf ma 9 nov").
    product["upcoming"] = {key: upcoming[key] for key in ("validFrom", "validUntil", "price", "priceBefore", "promoLabel", "personal")} if upcoming else None
    for key in ("title", "brand", "unitSize", "image", "url"):
        if upcoming and not product.get(key) and upcoming.get(key):
            product[key] = upcoming[key]
    # Een product uit een link dat nergens in de geschiedenis staat (`_pinned`): naam, merk, verpakking en foto van daar.
    for key, value in (known or {}).items():
        if not product.get(key):
            product[key] = value
    # Titel, merk en plaatje: uit de aanbiedingen, anders de nieuwste zoekopdracht, anders de nieuwste bestelling (een kassabon
    # heeft alleen een afgekorte naam, die nemen we als er niets anders is).
    named = [row for row in reversed(bought) if row["source"] == "order"] + list(reversed(bought))
    for row in list(reversed(observed)) + named:
        for key, column in (("title", "title"), ("brand", "brand"), ("unitSize", "unit_size"), ("image", "image")):
            if not product.get(key) and row[column]:
                product[key] = row[column]
    product.setdefault("title", product_id)
    product["url"] = product.get("url") or next(
        (row["url"] for row in reversed(observed) if row["url"] and row["source"] != PUBLIC_OBSERVED_UNKNOWN), ""
    ) or _url(store, product_id)

    product["observed"] = [
        {"date": row["day"], "price": row["price"], "unitSize": row["unit_size"], "sourceUrl": row["url"]}
        for row in observed if row["source"] == PUBLIC_OBSERVED_UNKNOWN and row["day"] <= today
    ]
    points: list[tuple[str, float, str, str | None]] = [
        (row["day"], row["price"], "search", row["unit_size"]) for row in observed if row["source"] != PUBLIC_OBSERVED_UNKNOWN
    ]
    for episode in product["episodes"]:
        if _number(episode.get("priceBefore")) is not None:
            for day in (episode.get("start"), episode.get("end")):
                if day:
                    points.append((str(day)[:10], episode["priceBefore"], "offer", offer.get("unitSize") if offer else None))
    # De gewone prijs bij een aankoop: van een bestelling, en ook van een ingelezen aankoop (*Oude aankopen importeren*) of een bon.
    points += [(row["day"], row["regular"], row["source"], row["unit_size"]) for row in bought if _number(row["regular"]) is not None]
    # Een aanbieding die nog loopt heeft een einddatum in de toekomst. Wat de prijs dan is, weten we nog niet.
    points = [point for point in points if point[0] <= today]
    segments = _regular_segments([(day, price, source) for day, price, source, _ in points])
    latest_point: tuple[str, float, str, str | None] | None = max(
        points, key=lambda point: (point[0], -_REGULAR_RANK.get(point[2], 9)), default=None
    )
    product["regular"] = segments
    if segments:
        product["regularPrice"] = segments[-1]["price"]
        product["regularSince"] = segments[-1]["from"]
        # De bron van de nieuwste gewone prijs: `latest_point` is de nieuwste dag en op die dag de bron met de hoogste rang, en dat
        # is ook het punt dat `_regular_segments` voor het einde van de laatste periode kiest.
        product["regularSource"] = latest_point[2] if latest_point else None
    # De verpakking van de nieuwste gewone prijs die er een noemt: een ingelezen aankoop of een bon heeft er geen.
    confirmed_unit: str | None = next((point[3] for point in sorted(points, key=lambda point: point[0], reverse=True) if point[3]), None)
    if latest_point:
        product["unitSize"] = confirmed_unit
    product["regularPerUnit"] = unit_price(product.get("regularPrice"), confirmed_unit)
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
        product["observed"][-1]["date"] if product["observed"] else "",
    ) if d]
    product["lastActivity"] = max(days) if days else None
    product["savedImages"] = images or []
    return product


def _known_pack_titles(store: str, catalogue: "_Catalogue | None", packs_known: list[dict[str, Any]], bases: set[str]) -> dict[str, str]:
    """Per product dat de catalogus kent en dat bij een familie van deze week hoort (`bases`: de namen zonder maat), een naam om de
    familiesleutel mee te maken. De catalogus vouwt het merk achter de naam ("ah rundergehakt 1000 g ah"), dus elk merk van de
    verpakkingen van deze week (en het huismerk) wordt er eerst afgehaald. Zo weet een kaart dat de app nog een verpakking kent
    zonder recente prijs (`unknownPacks`)."""
    if catalogue is None:
        return {}
    suffixes = {ah_lists._fold(pack.get("brand") or "").strip() for pack in packs_known} | {store}
    suffixes.discard("")
    found: dict[str, str] = {}
    for product_id, known in catalogue.products.items():
        for name in known.names:
            candidates = [name] + [name[: -len(suffix) - 1] for suffix in suffixes if name.endswith(f" {suffix}")]
            title = next((candidate for candidate in candidates if base_title(candidate) in bases), None)
            if title:
                found[product_id] = title
                break
    return found


async def warm_catalogues(stores: Iterable[str] | None = None, today: str | None = None) -> None:
    """Bouwt de catalogus van elke winkel alvast op (`_catalogue`: de bekende producten en de lijnindex, bij AH ruim twee
    seconden), zodat de eerste aanvraag van /api/offers na een start of een synchronisatie er niet op hoeft te wachten. Een fout
    bij een winkel laat de andere met rust: de aanvraag bouwt de catalogus dan zelf."""
    day = today or date.today().isoformat()
    for store in stores or offers.STORES:
        try:
            async with _connect() as conn:
                await _catalogue(conn, store, day)
        except Exception:  # noqa: BLE001 - opwarmen is een extraatje
            log.warning("Catalogus van %s opwarmen mislukt", store, exc_info=True)


async def _line_offers(
    store: str, catalogue: _Catalogue, ids: list[str], fallback: dict[str, _Known] | None = None
) -> dict[str, list[dict[str, Any]]]:
    """Per product de folderacties voor zijn hele lijn (line_offers.py): elke periode van een folderproduct waarvan alle woorden
    in een naam van het product staan, met de foldernaam erbij, oudste eerst. Een week met meer van zulke acties ("Alle Dr.
    Oetker Casa di Mama pizza's" en "Dr. Oetker pizza's") telt een keer, met de eerste naam. Een folderproduct zelf krijgt er
    geen. `fallback` geeft de namen van producten die nog niet in de catalogus staan (een aanbieding van deze week)."""
    wanted: dict[str, list[str]] = {}
    for product_id in ids:
        known = catalogue.products.get(product_id) or (fallback or {}).get(product_id)
        if known is None or product_id.startswith("folder:"):
            continue
        found = catalogue.lines.lines_for(known.names, known.sizes, line_offers.category_words(known.category))
        if found:
            wanted[product_id] = found
    if not wanted:
        return {}
    history = await offers.history_of(store, sorted({line for found in wanted.values() for line in found}))
    result: dict[str, list[dict[str, Any]]] = {}
    for product_id, found in wanted.items():
        weeks: dict[str, dict[str, Any]] = {}
        for line in found:
            folder = history.get(line) or {}
            for episode in folder.get("episodes", []):
                start = episode.get("start")
                if not start or start in weeks:
                    continue
                weeks[start] = {
                    "productId": line, "title": folder.get("title") or line, "start": start, "end": episode.get("end"),
                    "validFrom": episode.get("validFrom"), "validUntil": episode.get("validUntil"), "price": episode.get("price"),
                    "priceBefore": episode.get("priceBefore"), "promoLabel": episode.get("promoLabel") or "",
                }
        # Per stuk, en zonder de acties die bij de prijs van dit product niet passen (line_offers.per_piece).
        result[product_id] = line_offers.per_piece([weeks[start] for start in sorted(weeks)])
    return result


def _without_own_weeks(lines: list[dict[str, Any]], episodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """De lijnacties die niet samenvallen met een eigen aanbieding van het product: die zegt het al, en preciezer."""
    own = [(str(e["start"]), str(e.get("end") or "9999-12-31")) for e in episodes if e.get("start")]
    return [line for line in lines if not any(start <= str(line.get("end") or line["start"]) and line["start"] <= end for start, end in own)]


async def _lookup_receipt_names(lookup: Callable[[list[str]], Awaitable[Any]], wanted: "_Query", today: str) -> None:
    """Laat de namen op de bon die bij de zoekterm passen en nog bij geen product horen meteen opzoeken (zie `history`)."""
    async with _connect() as conn:
        rows = (await _catalogue(conn, "ah", today)).receipts
    names = sorted({row["title"] for row in rows if row.get("lookup") and wanted.wants({_fold_name(row["title"])}, set())})
    if names:
        try:
            await lookup(names)
        except Exception:  # noqa: BLE001 - zonder koppeling staat de regel er gewoon met de naam op de bon
            log.warning("Namen op de bon opzoeken mislukt", exc_info=True)


FACET_BRANDS: int = 500  # zoveel merken hooguit in de keuzelijst van de prijsgeschiedenis, de meest voorkomende eerst


class _Choice(NamedTuple):
    """Een keuzelijst van de prijsgeschiedenis: met iets in `shown` alleen die waarden, wat in `hidden` staat valt altijd weg."""

    shown: frozenset[str]
    hidden: frozenset[str]

    @classmethod
    def of(cls, shown: Any, hidden: Any) -> "_Choice":
        def texts(value: Any) -> frozenset[str]:
            return frozenset(str(item) for item in value if isinstance(item, str) and item) if isinstance(value, (list, tuple, set, frozenset)) else frozenset()

        return cls(texts(shown), texts(hidden))

    def passes(self, value: str) -> bool:
        return value not in self.hidden and (not self.shown or value in self.shown)


def _facet_counts(values: Iterable[str], chosen: _Choice, limit: int | None = None) -> list[dict[str, Any]]:
    """[{"value", "count"}], op aantal en dan op waarde. Een lege waarde telt niet. Met `limit` de meest voorkomende, en wat
    gekozen is staat er altijd bij: de lijst op het scherm moet het aantal van een gekozen merk kunnen noemen."""
    counts: dict[str, int] = {}
    for value in values:
        if value:
            counts[value] = counts.get(value, 0) + 1
    ordered: list[tuple[str, int]] = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    kept: list[tuple[str, int]] = ordered if limit is None else ordered[:limit] + [item for item in ordered[limit:] if item[0] in chosen.shown | chosen.hidden]
    return [{"value": value, "count": count} for value, count in kept]


async def history(store: Any = None, query: Any = "", limit: Any = 50, today: str | None = None, bought: bool = False,
                  lookup: Callable[[list[str]], Awaitable[Any]] | None = None, product: Any = None, pin: Any = None,
                  brands: Any = None, brands_out: Any = None, categories: Any = None, categories_out: Any = None) -> dict[str, Any]:
    """De prijsgeschiedenis van producten: aanbiedingen, normale prijs en aankopen, het meest recente bovenaan.

    Met `product` (een product-id) alleen dat ene product, zonder zoekterm en zonder `lookup`: zo haalt de kaart van een aanbieding
    het prijsverloop voor de hover-zoom op.

    Een product past als elk woord van de zoekterm in een van zijn namen staat (uit de aanbiedingen, een zoekopdracht of een aankoop) en,
    als de zoekterm een maat noemt ("Gehakt 500 gram"), zijn verpakking die maat heeft; zie `_Query`. Met `bought` blijven alleen de
    producten over die je minstens één keer kocht. Per product staan de velden van `offers.history` erin, plus `regular` (periodes met
    dezelfde normale prijs), `purchases` (wat je betaalde, per stuk, oudste eerst), `savedImages` (de bewaarde afbeeldingen, de nieuwste
    eerst; zie product_images.py) en samenvattingen als `regularPrice`, `lastPaid`, `averagePaid` en `timesBought`. `unsynced` zegt per
    winkel van hoeveel bestellingen en kassabonnen de producten nog niet zijn opgehaald: die aankopen ontbreken nog.

    Met `pin` (een product-id) staat dat product er altijd in, bovenaan, ook als de naam niet bij de zoekterm past of je het nooit
    kocht: een link van een aanbieding of een bon naar de prijsgeschiedenis noemt het product op id, en de naam erbij geeft de
    andere producten met die naam. Een product met alleen een aanbieding die nog moet beginnen (de bonus van volgende week staat
    pas in `offer_history` als hij begint) is ook te vinden, met die aanbieding in `upcoming`.

    Met `brands` en `categories` blijven alleen de producten van die merken en categorieen over, en `brands_out` en
    `categories_out` halen merken en categorieen weg (dat wint). Het product van `pin` blijft altijd staan. `facets` geeft per merk
    en per categorie hoeveel producten er met de zoekterm en de andere lijst zijn (`{"brands", "categories"}`, elk
    `[{"value", "count"}]`, de merken hooguit `FACET_BRANDS`). Zonder `product`: de hover heeft geen lijsten.

    Met `lookup` (een zoekterm en AH erbij) worden de namen op de bon die bij de zoekterm passen en nog bij geen product horen eerst
    bij AH opgezocht (`ah_orders.resolve_receipt_names`): zo staat "REDBAND" er meteen als "Red Band Winegums original".
    """
    stores = [offers._valid_store(store)] if store else list(STORES)
    wanted = _Query(query)
    limit = _limit(limit)
    today = today or _now()[:10]
    only = str(product) if product not in (None, "") else None
    pinned = str(pin) if pin not in (None, "") else None
    if lookup and wanted.words and "ah" in stores and only is None:
        await _lookup_receipt_names(lookup, wanted, today)
    async with _connect() as conn:
        catalogues = {name: await _catalogue(conn, name, today) for name in stores}
        receipts = {name: catalogue.receipts for name, catalogue in catalogues.items()}
        unsynced = {name: await _unsynced(conn, name) for name in stores}
        candidates: dict[tuple[str, str], str] = {}
        pinned_known: dict[str, dict[str, Any]] = {}  # winkel -> wat `_pinned` van het product uit de link weet
        for name in stores:
            twins = catalogues[name].twins
            keep = _with_twins(await _bought(conn, name, today, receipts[name]), twins) if bought else None
            found_here = _candidates(catalogues[name].products, wanted, always=pinned)
            for product_id, day in found_here.items():
                # Twee nummers voor hetzelfde product: een rij, onder het gevraagde nummer of anders het nummer met de nieuwste gegevens.
                group = twins.get(product_id, (product_id,))
                asked = next((twin for twin in group if twin in (only, pinned)), None)
                lead = asked or max(group, key=lambda twin: (catalogues[name].products[twin].last, twin))
                if only is not None and lead != only:
                    continue
                if keep is None or lead in keep or lead == pinned:
                    candidates[(name, lead)] = max(day, candidates.get((name, lead), ""))
            # Het product uit de link staat nergens in de prijsgeschiedenis (alleen in een open bestelling, of in een
            # aanbieding die net begon): dan toch het product zelf, met wat de app ervan weet. Alleen met een winkel: een id
            # hoort bij een winkel.
            if pinned is not None and store and only is None and (name, pinned) not in candidates:
                known = await _pinned(conn, name, pinned, await _tables(conn))
                if known is not None:
                    candidates[(name, pinned)] = today
                    pinned_known[name] = known
        by_brand: _Choice = _Choice.of(brands, brands_out)
        by_category: _Choice = _Choice.of(categories, categories_out)
        nothing: _Known = _Known("", frozenset(), frozenset())
        known: dict[tuple[str, str], _Known] = {key: catalogues[key[0]].products.get(key[1], nothing) for key in candidates}
        facets: dict[str, list[dict[str, Any]]] = {} if only is not None else {
            "brands": _facet_counts((item.brand for item in known.values() if by_category.passes(item.category)), by_brand, FACET_BRANDS),
            "categories": _facet_counts((item.category for item in known.values() if by_brand.passes(item.brand)), by_category),
        }
        candidates = {
            key: day for key, day in candidates.items()
            if key[1] == pinned or (by_brand.passes(known[key].brand) and by_category.passes(known[key].category))
        }
        ranked = sorted(candidates, key=lambda key: (key[1] == pinned, candidates[key], key[0], key[1]), reverse=True)
        chosen = ranked[:limit]
        found: dict[str, tuple[dict[str, list[Any]], dict[str, list[Any]], dict[str, list[dict[str, Any]]]]] = {}
        for name in stores:
            ids = sorted(_with_twins({product_id for store_name, product_id in chosen if store_name == name}, catalogues[name].twins))
            found[name] = (
                (await _observations(conn, name, ids), await _purchases(conn, name, ids, today, receipts[name]), await _saved_images(conn, name, ids))
                if ids else ({}, {}, {})
            )
        upcoming = {name: await _upcoming(conn, name, [pid for s, pid in chosen if s == name], today) for name in stores}
    offered = {name: await offers.history_of(name, [pid for s, pid in chosen if s == name]) for name in stores}
    lines = {name: await _line_offers(name, catalogues[name], [pid for s, pid in chosen if s == name]) for name in stores}
    products = [
        _product(name, product_id, offered[name].get(product_id),
                 _twin_rows(found[name][0], product_id, catalogues[name].twins, lambda row: row["day"]),
                 _twin_rows(found[name][1], product_id, catalogues[name].twins, lambda row: (row["day"] or "", row["source"], str(row["ref"]))), today,
                 found[name][2].get(product_id, []), upcoming[name].get(product_id), pinned_known.get(name) if product_id == pinned else None,
                 lines[name].get(product_id, []))
        for name, product_id in chosen
    ]
    return {"products": products, "total": len(ranked), "unsynced": unsynced, "facets": facets}


# --- prijs per eenheid, oordeel en verpakkingen vergelijken (deel 2) ----------------------------------------------------

YEAR_DAYS = 365
EVER_DAYS = 365  # "laagste prijs ooit" pas als de app het product minstens zo lang volgt
HALF_YEAR_DAYS = 182  # "laagste in 6 maanden" pas bij een half jaar dekking
SEEN_DAYS = 30  # "laagste sinds <maand>": de milde trede voor een installatie die nog kort meet
GOOD_DISCOUNT = 0.20  # vanaf 20% onder de gewone prijs (regular_price.choose) is het echt voordelig
OFTEN_PER_YEAR = 6  # zo vaak of vaker per jaar in de aanbieding: het komt vaak terug
PACK_DAYS = 60  # hoe oud een prijs van een andere verpakking mag zijn om mee te vergelijken
REGULAR_HISTORY_DAYS = 3650  # zo ver terug zoekt het oordeel bij de aanbiedingen een gewone prijs (regular_price.choose kiest)
PACK_MARGIN = 0.02  # een andere verpakking telt pas als goedkoper bij minstens 2% verschil per eenheid
COMPARE_LIMIT = 30  # genoeg om ook de grote verpakking (zoals 1000 g rundergehakt) tussen de resultaten te krijgen

# Woorden die een verpakking aanduiden, niet het product: zonder deze woorden (en zonder maten) is "AH Zalmfilet 3-pack" hetzelfde
# product als "AH Zalmfilet".
_PACK_WORDS = frozenset({
    "voordeelverpakking", "voordeelpak", "voordeel", "multipack", "familieverpakking", "familiepak", "grootverpakking", "navulling",
    "pack", "xl", "xxl", "groot", "grote", "klein", "kleine", "mini", "per", "stuk", "stuks", "st", "x",
    "s", "m", "l",  # de maat van een ei: "AH Witte vrije uitloopeieren M" en "... L" zijn hetzelfde product (#223)
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


_STORE_WORDS = frozenset({"ah", "jumbo", "picnic", "crisp", "lidl", "aldi"})

# Woorden die zeggen dat een product biologisch is (#218). Ze horen niet bij de soort: "AH Biologisch rundergehakt" is
# rundergehakt, net als "AH Rundergehakt".
ORGANIC_WORDS = frozenset({"biologisch", "biologische", "bio", "organic", "eko"})


def is_organic(product: dict[str, Any]) -> bool:
    """Biologisch: AH zegt het zelf (`organic`, het kenmerk "biologisch" in de zoekresultaten); anders, en bij de andere winkels, een
    woord als "biologisch" of "bio" in de naam of het merk. Niet elk biologisch product van AH heeft het kenmerk: "AH Biologisch
    Rundergehakt balletjes" niet."""
    if product.get("organic"):
        return True
    text = ah_lists._fold(f"{product.get('title') or ''} {product.get('brand') or ''}")
    return any(word in ORGANIC_WORDS for word in re.findall(r"[a-z0-9]+", text))


def _kind_words(title: Any, brand: Any) -> list[str]:
    """De woorden die zeggen wat voor product het is: de naam zonder merk, winkel, maten, verpakkingswoorden en "biologisch"
    ("rundergehakt")."""
    brand_words = set(re.findall(r"[a-z0-9]+", ah_lists._fold(brand or "")))
    return [
        word for word in base_title(title).split()
        if word not in brand_words and word not in _STORE_WORDS and word not in ORGANIC_WORDS
    ]


def _same_kind(kind: list[str], other_title: Any) -> bool:
    """Hetzelfde soort product: elk woord van de soort staat in de naam, ook als deel van een woord. Bij "AH Rundergehakt" horen
    "AH Rundergehakt 1000 g" en "AH Biologisch rundergehakt", maar niet "AH Hamburgers" of "AH Half-om-half gehakt"."""
    words = base_title(other_title).split()
    return all(any(word in other for other in words) for word in kind)


def _forms(word: str) -> list[str]:
    """Het woord en zijn meervoud, langste eerst: "ei" is ook "eieren", "ui" ook "uien"."""
    return sorted({word, word + "en", word + "eren", word + "s", word + "n"}, key=len, reverse=True)


def _ingredient_kind(first: dict[str, Any], query: Any) -> list[tuple[str, bool]] | None:
    """De soort voor de controlelijst, uit het ingrediënt waar het voorstel `first` bij hoort (#223): per woord van het ingrediënt
    (woord, mag deel van een samenstelling zijn). None als het voorstel het ingrediënt niet noemt ("Milner Jong belegen" bij
    "kaas"); dan geldt de soort van het voorstel zelf (`_kind_words`).

    Zo maken eigenschappen als "witte" en "vrije" de soort niet te smal: bij "eieren" en "AH Witte vrije uitloopeieren M" is de
    soort "eieren", en omdat het voorstel het als deel van een samenstelling noemt, horen ook "scharreleieren" en "AH Biologisch
    Eieren" erbij. Noemt het voorstel het ingrediënt als los woord ("AH Halfvolle melk"), dan moet een ander product het ook als
    los woord noemen: "AH Karnemelk" is geen melk. Bij "rundergehakt" dus geen "AH Half-om-half gehakt" (#178)."""
    words = [w for w in base_title(query).split() if w not in ORGANIC_WORDS and w not in _STORE_WORDS]
    title = _kind_words(first.get("title"), first.get("brand"))
    kind = []
    for word in words:
        for form in _forms(word):
            found = next(((form, other != form) for other in title if other == form or other.endswith(form)), None)
            if found:
                kind.append(found)
                break
    return kind or None


def _matches(kind: list[tuple[str, bool]], word: str) -> bool:
    return any(word == form or (compound and word.endswith(form)) for form, compound in kind)


def _same_ingredient(kind: list[tuple[str, bool]], other_title: Any) -> bool:
    """Hoort het product bij de soort uit `_ingredient_kind`? Elk woord moet erin staan, als los woord of (als het mag) als
    einde van een samenstelling."""
    words = base_title(other_title).split()
    return all(any(_matches([part], word) for word in words) for part in kind)


def _same_product(base: str, other_title: Any) -> bool:
    """Hetzelfde product in een andere verpakking: precies dezelfde naam zonder maten en verpakkingswoorden. "AH Biologisch Penne" is
    dus niet hetzelfde als "AH Penne", maar "AH Penne voordeelverpakking" wel."""
    return bool(base) and base_title(other_title) == base


# Acties met een voorwaarde waarbij de winkel de gewone prijs als prijs noemt en geen prijs ervoor: AH zet bij "1+1 gratis",
# "2e halve prijs" en "2e = 50% korting" de gewone prijs neer (zie `ah._promotion_products`), Jumbo zonder actieprijs ook. Wat een stuk
# kost, volgt dan uit die prijs. Bij "25% korting" zonder prijs ervoor weten we niet of de prijs al verlaagd is; die rekenen we niet uit.
_FROM_REGULAR = frozenset({deals.SECOND_HALF, deals.NTH_FREE, deals.NTH_PERCENT})


def _effective(offer: dict[str, Any]) -> float | None:
    """Wat één stuk kost bij de aanbieding (als je de voorwaarde haalt)."""
    if _number(offer.get("unitPrice")) is not None:
        return _number(offer.get("unitPrice"))
    price = _number(offer.get("price"))
    if _number(offer.get("priceBefore")) is not None:
        return price
    mechanic = deals.parse_mechanic(offer.get("promoLabel"))
    if price is not None and mechanic["type"] in _FROM_REGULAR:
        return _number(deals.effective_unit_price(mechanic, price, price))
    return None


def _claimed_percent(offer: dict[str, Any]) -> int | None:
    """De korting volgens de winkel: `(van - voor) / van`, of bij een actie zonder "van"-prijs ("1+1 gratis", "2 voor 3,00") wat de
    actietekst zegt, zoals het userscript dat ook berekent. None als er niets te berekenen valt."""
    price, before = _number(offer.get("price")), _number(offer.get("priceBefore"))
    if price is not None and before:
        return max(0, round((before - price) / before * 100))
    if price is None or price <= 0:
        return None
    mechanic = deals.parse_mechanic(offer.get("promoLabel"))
    per_item = _number(deals.effective_unit_price(mechanic, price, price))
    if per_item is None or mechanic["type"] == deals.UNKNOWN:
        return None
    return max(0, round((1 - per_item / price) * 100))


def _stated_regular(offer: dict[str, Any]) -> float | None:
    """De gewone prijs die de winkel bij de aanbieding noemt. AH geeft bij een actie met een minimumaantal ("2 voor 3", "1+1 gratis")
    geen prijs ervoor maar de gewone prijs als prijs; dan is de prijs hoger dan wat een stuk bij de actie kost."""
    if _number(offer.get("priceBefore")) is not None:
        return _number(offer.get("priceBefore"))
    price, unit = _number(offer.get("price")), _effective(offer)
    return price if price is not None and unit is not None and unit < price else None


def _days_ago(today: str, days: int) -> str:
    return (date.fromisoformat(today) - timedelta(days=days)).isoformat()


def _episode_effective(row: Any) -> float | None:
    """Wat een stuk kostte bij een periode uit `offer_history`: uit de actietekst, en zonder "van"-prijs zoals `_effective` dat bij
    de aanbieding zelf doet ("1+1 gratis" met de gewone prijs als prijs), anders de prijs van de periode."""
    described = deals.describe(row["promo_label"], price_now=row["price"], price_before=row["price_before"])
    if _number(described["unitPrice"]) is not None:
        return _number(described["unitPrice"])
    as_offer = {"price": row["price"], "priceBefore": row["price_before"], "promoLabel": row["promo_label"]}
    effective = _effective(as_offer)
    return effective if effective is not None else _number(row["price"])


async def signature(store: Any) -> str:
    """Verandert als er een prijs, een aankoop of een aanbieding is bewaard: zo ververst het oordeel bij de aanbiedingen (ETag).

    Dat is de versie van de catalogus: `price_observations` en `imported_purchases` staan in `CATALOGUE_TABLES`, dus een trigger
    hoogt de versie op bij elke rij die erbij komt, verandert of verdwijnt. De rijen van de winkel tellen (zoals tot 4.21.0) las
    bij elke aanvraag van de aanbiedingen alle prijzen van die winkel, ook voor een 304: bij AH 1,6 seconde voor 309.000 prijzen.
    """
    del store  # de versie geldt voor alle winkels, de aanroepers geven de winkel al mee in hun eigen sleutel
    try:
        async with _connect() as conn:
            return await _catalogue_version(conn)
    except Exception:  # noqa: BLE001 - de cache mag nooit de aanbiedingen tegenhouden
        log.warning("Handtekening van de prijzen mislukt", exc_info=True)
        return ""


async def annotate(store: str, items: list[dict[str, Any]], today: str | None = None) -> list[dict[str, Any]]:
    """Zet bij elke aanbieding een oordeel (`verdict`) en de prijs per eenheid (`unitPricePer`). Past de dicts aan en geeft ze terug.

    De gewone prijs is de nieuwste bekende uit elke bron (`_regular_points`, gekozen door `regular_price.choose`, zie `_verdict_of`),
    en pas zonder bron de prijs ervoor die de aanbieding zelf noemt. `verdict.kind` is `good` (minstens 20% onder de gewone prijs),
    `small` (wel lager, maar minder), `none` (niet lager) of `unknown` (de actieprijs per stuk of de gewone prijs is niet bekend, of de
    gewone prijs is ouder dan een jaar). Daarnaast: `percent`, `regular` (de gekozen gewone prijs als `{price, day, source, judged,
    fresh}`, ook als hij ouder is dan een jaar, en None als de lagere van-prijs van de aanbieding telt), `lowestRegular` (de gewone
    prijs waarmee is gerekend, of None), `inflated` (de prijs ervoor die de winkel noemt is hoger dan die gewone prijs), `timesThisYear`,
    `often` en `cheaperPack` (een andere verpakking van hetzelfde product die per eenheid goedkoper is, uit de bewaarde prijzen en de
    andere aanbiedingen). Daarnaast `badge` (het getal op de kaart: `percent`, `source` `history`, `dated` of `store`, `regular` bij
    `history` en `dated`, `known` bij `store` als de app een hogere gewone prijs kent, `claimed`, `inflated`) en `priceLabel` (de
    ladder uit de geschiedenis, zie `price_label`). Een fout laat de aanbiedingen staan zonder oordeel.
    """
    try:
        await _annotate(store, items, today or _now()[:10])
    except Exception:  # noqa: BLE001 - het oordeel is een extraatje; de aanbiedingen gaan altijd door
        log.warning("Oordeel bij de aanbiedingen mislukt voor %s", store, exc_info=True)
    return items


# De producten van `_history_points` als een parameter: een JSON-lijst die SQLite met json_each uitpakt. Zo past een lijst van
# honderden ids in een enkele `?`, ook boven de grens van het aantal parameters.
_WANTED_SQL = "product_id IN (SELECT value FROM json_each(?))"


async def _history_points(
    conn: aiosqlite.Connection, store: str, today: str, receipts: list[dict[str, Any]], wanted: Iterable[str]
) -> dict[str, list[tuple[str, float, str]]]:
    """Per product uit `wanted` de (dag, effectieve prijs per stuk, bron) uit alles wat de app bewaart: de afgesloten aanbiedingen
    (bron `offer`, op hun begindag, wat een stuk met de actie kostte), de bewaarde gewone prijzen (`observation`) en de aankopen
    (`purchase`, wat je per stuk betaalde, gratis telt niet). De lopende aanbieding zelf staat er niet in: die is wat we
    vergelijken. Gesorteerd op dag. `receipts` zijn de bonregels en ingelezen aankopen van `_receipt_rows`.

    Alleen de gevraagde producten: de aanbiedingen van een week zijn er een paar honderd, de geschiedenis van een winkel heeft er
    tienduizenden, en de actietekst van elke afgesloten periode lezen (`_episode_effective`) kost per regel tijd."""
    ids = {str(product_id) for product_id in wanted if product_id}
    points: dict[str, list[tuple[str, float, str]]] = {}
    if not ids:
        return points
    selected = json.dumps(sorted(ids))

    def add(product_id: Any, day: Any, price: Any, source: str) -> None:
        value = _number(price)
        if product_id and day and value is not None and value > 0 and str(day)[:10] <= today:
            points.setdefault(str(product_id), []).append((str(day)[:10], value, source))

    for row in await conn.execute_fetchall(
        f"SELECT * FROM offer_history WHERE store = ? AND ended_at IS NOT NULL AND {_WANTED_SQL} ORDER BY product_id, first_seen, id",
        (store, selected),
    ):
        add(row["product_id"], offers._episode_span(row)["start"], _episode_effective(row), "offer")
    # Zonder de weekprijzen van de prijsindex: die gaan jaren terug en kennen alleen de gewone prijs. Telden ze mee als dekking,
    # dan werd een actieprijs "laagste ooit" over jaren waarvan de app de acties niet kent.
    for row in await conn.execute_fetchall(
        f"SELECT product_id, day, price FROM price_observations WHERE store = ? AND source != ? AND {_WANTED_SQL}", (store, PRICE_INDEX, selected)
    ):
        add(row["product_id"], row["day"], row["price"], "observation")
    for sql in _purchase_sources(await _tables(conn), store):
        for row in await conn.execute_fetchall(f"SELECT product_id, day, paid FROM ({sql}) WHERE paid > 0 AND {_WANTED_SQL}", (selected,)):
            add(row["product_id"], row["day"], row["paid"], "purchase")
    for row in receipts:
        if str(row["product_id"]) in ids:
            add(row["product_id"], row["day"], row["paid"], "purchase")
    return {product_id: sorted(found) for product_id, found in points.items()}


def _lowest_since(points: list[tuple[str, float]], since: str) -> float | None:
    """De laagste prijs op of na een dag, of None als er in dat venster geen meting is."""
    inside = [price for day, price in points if day >= since]
    return min(inside) if inside else None


def price_label(effective: float | None, points: list[tuple[str, float]], today: str) -> dict[str, Any]:
    """De trede op de ladder van de prijsgeschiedenis voor een actieprijs: `lowest_ever`, `lowest_12m`, `lowest_6m`, `lowest_seen`,
    `lower_before` of None, als `{"kind", "price", "month", "coveredSince"}`.

    Alleen de strengste trede die waar is. Een venster telt pas als de app het product langer dan dat venster volgt (`coveredSince`)
    en er in dat venster een meting is: na een week is "laagste in 6 maanden" anders triviaal waar, en één aankoop uit 2022 maakt een
    prijs van nu geen "laagste ooit" (daarvoor moet er een meting in het afgelopen jaar zijn). `lower_before` geeft de laagste eerdere
    prijs en de maand waarin die het laatst gold. Prijzen binnen een halve cent zijn gelijk.
    """
    empty: dict[str, Any] = {"kind": None, "price": None, "month": None, "coveredSince": None}
    value = _number(effective)
    if value is None or not points:
        return empty
    ordered = sorted(points)
    since = ordered[0][0]
    covered = (date.fromisoformat(today) - date.fromisoformat(since)).days
    lowest_all = min(price for _, price in ordered)
    lowest_year: float | None = _lowest_since(ordered, _days_ago(today, YEAR_DAYS))
    lowest_half: float | None = _lowest_since(ordered, _days_ago(today, HALF_YEAR_DAYS))
    is_lowest = value <= lowest_all + 0.005
    label = {**empty, "coveredSince": since}
    if covered >= EVER_DAYS and is_lowest and lowest_year is not None:
        return {**label, "kind": "lowest_ever"}
    if covered >= YEAR_DAYS and lowest_year is not None and value <= lowest_year + 0.005:
        return {**label, "kind": "lowest_12m"}
    if covered >= HALF_YEAR_DAYS and lowest_half is not None and value <= lowest_half + 0.005:
        return {**label, "kind": "lowest_6m"}
    if covered >= SEEN_DAYS and is_lowest:
        return {**label, "kind": "lowest_seen", "month": since[:7]}
    if lowest_all < value - 0.005:
        latest = max(day for day, price in ordered if price == lowest_all)
        return {**label, "kind": "lower_before", "price": lowest_all, "month": latest[:7]}
    return label


def _point(points: dict[str, list[RegularPoint]], product_id: Any, day: Any, price: Any, source: str) -> None:
    """Voegt een gedateerd punt toe als de prijs een getal boven nul is en de dag een ISO-dag."""
    value = _number(price)
    if value is None or not day:
        return
    points.setdefault(str(product_id), []).append(RegularPoint(str(day)[:10], value, source))


def _in_window(day: Any, since: str, until: str, before_until: bool) -> bool:
    """Of een dag tussen `since` en `until` valt, met `until` zelf buiten het venster als `before_until`."""
    text = str(day or "")[:10]
    return bool(text) and text >= since and (text < until if before_until else text <= until)


async def _search_points(
    conn: aiosqlite.Connection, store: str, since: str, until: str, ids: list[str] | None
) -> list[tuple[Any, Any, Any]]:
    """De bewaarde prijzen (`price_observations`) van de dagen in het venster, als (product, dag, prijs)."""
    wanted = f" AND {_WANTED_SQL}" if ids is not None else ""
    args: tuple[Any, ...] = (store, since, until, PUBLIC_OBSERVED_UNKNOWN) + ((json.dumps(ids),) if ids is not None else ())
    rows = await conn.execute_fetchall(
        f"SELECT product_id, day, price FROM price_observations WHERE store = ? AND day >= ? AND day <= ? AND source != ?{wanted}", args
    )
    return [(row["product_id"], row["day"], row["price"]) for row in rows]


async def _order_points(
    conn: aiosqlite.Connection, store: str, since: str, until: str, before_until: bool, ids: list[str] | None
) -> list[tuple[Any, Any, Any]]:
    """De gewone prijs in je bestellingen (`regular` van `_purchase_sources`), als (product, dag, prijs)."""
    last_day = "day < ?" if before_until else "day <= ?"
    wanted = f" AND {_WANTED_SQL}" if ids is not None else ""
    args: tuple[Any, ...] = (since, until) + ((json.dumps(ids),) if ids is not None else ())
    found: list[tuple[Any, Any, Any]] = []
    for sql in _purchase_sources(await _tables(conn), store):
        for row in await conn.execute_fetchall(
            f"SELECT product_id, day, regular FROM ({sql}) WHERE day >= ? AND {last_day} AND regular IS NOT NULL{wanted}", args
        ):
            found.append((row["product_id"], row["day"], row["regular"]))
    return found


# Hoe een bonregel of ingelezen aankoop aan zijn product hangt (`_link_receipt_rows`): alleen deze koppelingen zijn bevestigd. Een
# regel op naam ("AH RUNDERGEHAKT" bij "AH Rundergehakt 500 g") kan een andere verpakking zijn, en een regel op `id` is een
# artikelnummer van AH dat toevallig ook een id van de webwinkel is: die staan in de prijsgeschiedenis, maar zijn geen bron voor het
# oordeel en de gewone prijs van een aankoop (antwoord van de eigenaar en herreview, 2026-11-12).
CONFIRMED_LINKS: frozenset[str] = frozenset({"own", "shared", "article"})


def _receipt_points(
    receipts: list[dict[str, Any]], since: str, until: str, before_until: bool
) -> list[tuple[Any, Any, Any, str]]:
    """De punten uit de bonregels en ingelezen aankopen, als (product, dag, prijs, bron).

    Alleen regels met een bevestigde koppeling (`CONFIRMED_LINKS`). Een ingelezen aankoop geeft zijn `regular`, een bonregel de prijs
    die is betaald, maar alleen bij een geheel aantal: een gewogen product (0,482 kg) heeft een prijs per kilo en geen prijs per stuk."""
    found: list[tuple[Any, Any, Any, str]] = []
    for row in receipts:
        if row.get("linked_by") not in CONFIRMED_LINKS or not _in_window(row.get("day"), since, until, before_until):
            continue
        if row.get("source") == "import":
            found.append((row["product_id"], row["day"], row.get("regular"), "import"))
        elif row.get("source") == "receipt":
            quantity = _number(row.get("quantity"))
            if quantity is not None and float(quantity).is_integer():
                found.append((row["product_id"], row["day"], row.get("paid"), "receipt"))
    return found


def _offer_chains(rows: list[Any]) -> list[list[Any]]:
    """De rijen van `offer_history` per aanbieding. Verandert de prijs of het label halverwege, dan sluit `offers._record_history`
    de rij en opent een rij met `continued`: die hoort bij de rij ervoor van hetzelfde product. `rows` is gesorteerd op product,
    `first_seen` en id."""
    chains: list[list[Any]] = []
    for row in rows:
        if chains and row["continued"] and chains[-1][-1]["product_id"] == row["product_id"]:
            chains[-1].append(row)
        else:
            chains.append([row])
    return chains


async def _offer_points(
    conn: aiosqlite.Connection, store: str, since: str, until: str, ids: list[str] | None
) -> list[tuple[Any, Any, Any]]:
    """De prijs vóór de actie van elke aanbieding, op de dag dat ze eindigde (`until` als ze nog loopt of later eindigt).

    Een aanbieding is een keten van rijen (`_offer_chains`): ze geeft een punt, met de laatste prijs vóór de actie van de keten,
    op het einde van de laatste rij. Zo telt een aanbieding die nog loopt maar halverwege veranderde niet als afgelopen. Een rij
    van een folder (`folder:...`) is geen product en telt niet. De voorselectie in SQL is ruim (een jaar voor `since`):
    `_episode_span` bepaalt het einde ook uit `ended_at`, wat de SQL niet ziet."""
    wanted: str = f" AND {_WANTED_SQL}" if ids is not None else ""
    args: tuple[Any, ...] = (store, _days_ago(since, YEAR_DAYS)) + ((json.dumps(ids),) if ids is not None else ())
    rows: list[Any] = list(await conn.execute_fetchall(
        "SELECT * FROM offer_history WHERE store = ? AND product_id NOT LIKE 'folder:%' "
        f"AND COALESCE(valid_until, substr(last_seen, 1, 10)) >= ?{wanted} ORDER BY product_id, first_seen, id", args
    ))
    found: list[tuple[Any, Any, Any]] = []
    for chain in _offer_chains(rows):
        claimed: list[Any] = [row for row in chain if row["price_before"] is not None]
        end: str | None = offers._episode_span(chain[-1])["end"]
        day: str = until if end is None or end > until else end
        if claimed and since <= day:
            found.append((claimed[-1]["product_id"], day, claimed[-1]["price_before"]))
    return found


async def _regular_points(
    conn: aiosqlite.Connection, store: str, since: str, until: str, *, receipts: list[dict[str, Any]],
    purchases_before_until: bool = False, ids: list[str] | None = None,
) -> dict[str, list[RegularPoint]]:
    """De gedateerde gewone prijzen per product tussen `since` en `until` (ISO-dagen, beide meegerekend), uit elke bron.

    Bronnen (de naam is `RegularPoint.source`): `search` (bewaarde prijzen), `order` (de gewone prijs in je bestellingen),
    `import` (ingelezen aankopen met een gewone prijs), `receipt` (de betaalde prijs op een bon, alleen bij een geheel aantal)
    en `offer` (de prijs vóór de actie, op de dag dat de periode eindigde). `receipts` is `_Catalogue.receipts`.
    `purchases_before_until` laat de aankopen van de dag `until` zelf weg. `ids` beperkt het tot die producten."""
    points: dict[str, list[RegularPoint]] = {}
    wanted: set[str] | None = None if ids is None else {str(i) for i in ids}
    for product_id, day, price in await _search_points(conn, store, since, until, ids):
        _point(points, product_id, day, price, "search")
    for product_id, day, price in await _order_points(conn, store, since, until, purchases_before_until, ids):
        _point(points, product_id, day, price, "order")
    for product_id, day, price, source in _receipt_points(receipts, since, until, purchases_before_until):
        if wanted is None or str(product_id) in wanted:
            _point(points, product_id, day, price, source)
    for product_id, day, price in await _offer_points(conn, store, since, until, ids):
        _point(points, product_id, day, price, "offer")
    return points


def _regular_for_verdict(points: list[RegularPoint], today: str) -> Chosen | None:
    """De gewone prijs voor het oordeel op `today` (`regular_price.choose`), zonder de van-prijs van een aanbieding die nog loopt.

    `_offer_points` dateert een aanbieding (een keten van rijen) op haar einde, of op `until` (hier `today`) als ze nog loopt of
    later eindigt. Een punt `offer` van `today` is dus de van-prijs van een lopende aanbieding: de claim van de winkel die we
    beoordelen, geen bron. De van-prijs van een aanbieding die voor vandaag eindigde telt wel."""
    return choose([point for point in points if point.source != "offer" or point.day < today], today)


def _regular_of(chosen: Chosen | None) -> dict[str, Any] | None:
    """De gekozen gewone prijs zoals het oordeel en de badge hem tonen. `judged`: hoogstens een jaar oud, dus bruikbaar voor een oordeel.
    `fresh`: van de laatste 30 dagen, dus de laagste van die dagen en niet de laatst bekende."""
    if chosen is None:
        return None
    return {"price": chosen.price, "day": chosen.day, "source": chosen.source, "judged": chosen.judged, "fresh": chosen.fresh}


def _compared(effective: float | None, regular: float | None, stated: float | None) -> dict[str, Any]:
    """`kind`, `percent`, `lowestRegular` en `inflated`: de actieprijs per stuk tegen de gewone prijs `regular` (None: geen oordeel).
    `inflated` zegt of de van-prijs die de aanbieding noemt (`stated`) hoger is dan die gewone prijs."""
    verdict: dict[str, Any] = {"kind": "unknown", "percent": None, "lowestRegular": regular, "inflated": False}
    if effective is None or not regular:
        return verdict
    ratio: float = 1 - effective / regular
    verdict["percent"] = max(0, round(ratio * 100))
    verdict["kind"] = "none" if effective >= regular - 0.005 else ("good" if ratio >= GOOD_DISCOUNT - 1e-9 else "small")
    verdict["inflated"] = stated is not None and stated > regular + 0.005
    return verdict


def _badge_source(chosen: Chosen | None) -> str:
    """`history` bij een verse gewone prijs uit een meting of aankoop, `dated` bij een oudere of bij de van-prijs van een eerdere
    aanbieding, `store` als alleen de van-prijs van de aanbieding zelf bekend is."""
    if chosen is None:
        return "store"
    return "history" if chosen.fresh and chosen.source != "offer" else "dated"


def _stated_is_stricter(stated: float | None, chosen: Chosen | None) -> bool:
    """De van-prijs die de aanbieding noemt (`stated`) is lager dan een gekozen gewone prijs van hoogstens een jaar oud. Een winkel
    noemt zijn van-prijs niet te laag, dus de gewone prijs is sindsdien gedaald: dan vergelijkt het oordeel met de van-prijs, zodat
    een oudere prijs een oordeel alleen strenger kan maken en nooit ruimer dan de winkel zelf."""
    return chosen is not None and chosen.judged and stated is not None and 0 < stated < chosen.price - 0.005


def _verdict_of(effective: float | None, stated: float | None, chosen: Chosen | None) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Het oordeel en de badge van een aanbieding, de badge zonder `claimed` (dat komt uit de aanbieding zelf).

    De gekozen gewone prijs (`_regular_for_verdict`) gaat voor de van-prijs die de aanbieding noemt (`stated`), tenzij die lager is
    (`_stated_is_stricter`): dan rekent het oordeel met de van-prijs, is `regular` None en is de badge `store`, met de gekozen prijs
    alleen als hogere bekende prijs in `known`. Is de gekozen prijs ouder dan een jaar (niet `judged`), dan staat hij in `regular`
    met `lowestRegular` None, en oordeelt de van-prijs van de aanbieding zoals bij een product zonder bron, met de badge `store`
    (antwoord van de eigenaar, 2026-11-12). Noemt de aanbieding geen van-prijs, dan is er geen oordeel en geen badge. Zonder gekozen
    prijs oordeelt de van-prijs, met de badge `store`."""
    known: dict[str, Any] | None = _regular_of(chosen)
    stricter: bool = _stated_is_stricter(stated, chosen)
    too_old: bool = chosen is not None and not chosen.judged
    claim: bool = chosen is None or stricter or too_old  # de van-prijs van de winkel is de vergelijkingsprijs
    base: float | None = chosen.price if chosen is not None and not claim else stated
    verdict: dict[str, Any] = {**_compared(effective, base, stated), "regular": None if stricter else known}
    if too_old:
        verdict["lowestRegular"] = None  # de te oude prijs is geen vergelijkingsprijs, en de kaart streept hem niet door
    if verdict["kind"] == "unknown":
        return verdict, None
    badge: dict[str, Any] = {"percent": verdict["percent"], "source": "store" if claim else _badge_source(chosen), "inflated": verdict["inflated"]}
    if stricter:
        badge["known"] = known
    elif not claim:
        badge["regular"] = known
    return verdict, badge


async def _catalogue_or_none(conn: aiosqlite.Connection, store: str, today: str) -> _Catalogue | None:
    """De catalogus (`_catalogue`), of None als hij niet te lezen is: dan oordelen de aanbiedingen zonder de bonnen en de
    ingelezen aankopen, en zonder ladder en folderacties."""
    try:
        return await _catalogue(conn, store, today)
    except Exception:  # noqa: BLE001 - zonder catalogus blijft het oordeel uit de andere bronnen staan
        log.exception("Oordeel: de catalogus van %s kon niet worden gelezen", store)
        return None


async def _ladder(
    conn: aiosqlite.Connection, store: str, today: str, catalogue: _Catalogue | None, ids: list[str]
) -> tuple[_Catalogue | None, dict[str, list[tuple[str, float, str]]], set[str]]:
    """De catalogus, de punten van de ladder (`_history_points`) en de gekochte producten (voor het filter Eerder gekocht). Zonder
    catalogus of als de geschiedenis niet te lezen is `(None, {}, set())`: de ladder is een extraatje, het oordeel blijft staan."""
    if catalogue is None:
        return None, {}, set()
    try:
        points: dict[str, list[tuple[str, float, str]]] = await _history_points(conn, store, today, catalogue.receipts, ids)
        bought_ids: set[str] = _with_twins(await _bought(conn, store, today, catalogue.receipts), catalogue.twins)
    except Exception:  # noqa: BLE001 - de ladder is een extraatje: zonder blijven het oordeel en de badge staan
        log.exception("Prijslabels: de geschiedenis van %s kon niet worden gelezen", store)
        return None, {}, set()
    return catalogue, points, bought_ids


async def _annotate(store: str, items: list[dict[str, Any]], today: str) -> None:
    since_year, since_pack = _days_ago(today, YEAR_DAYS), _days_ago(today, PACK_DAYS)
    episodes: dict[str, list[Any]] = {}
    latest: dict[str, Any] = {}
    ids = sorted({str(offer.get("id")) for offer in items})  # alleen de geschiedenis van deze producten telt, zie _WANTED_SQL
    async with _connect() as conn:
        catalogue: _Catalogue | None = await _catalogue_or_none(conn, store, today)
        regulars: dict[str, list[RegularPoint]] = await _regular_points(
            conn, store, _days_ago(today, REGULAR_HISTORY_DAYS), today, receipts=catalogue.receipts if catalogue else [], ids=ids
        )
        for row in await conn.execute_fetchall(
            "SELECT * FROM price_observations WHERE store = ? AND day >= ? AND day <= ? AND source != ? ORDER BY day",
            (store, since_pack, today, PUBLIC_OBSERVED_UNKNOWN),
        ):
            latest[row["product_id"]] = row
        for row in await conn.execute_fetchall(
            f"SELECT * FROM offer_history WHERE store = ? AND COALESCE(valid_until, substr(last_seen, 1, 10)) >= ? AND {_WANTED_SQL} "
            "ORDER BY product_id, first_seen, id",
            (store, since_year, json.dumps(ids)),
        ):
            episodes.setdefault(row["product_id"], []).append(row)
        catalogue, points, bought_ids = await _ladder(conn, store, today, catalogue, ids)
    # De folderacties voor de hele lijn (per stuk), zonder de weken waarin het product zelf in de aanbieding was: ze tellen mee als
    # keer in de aanbieding en als prijs van het jaar (offer_ranking.year_low). Voor een product dat nog niet in de catalogus staat
    # (de aanbieding van deze week is zijn eerste spoor) zoekt de app op de naam van de aanbieding.
    lines: dict[str, list[dict[str, Any]]] = {}
    if catalogue is not None:
        fallback = {
            str(offer.get("id")): _Known("", frozenset({_fold_name(f"{offer.get('title') or ''} {offer.get('brand') or ''}")}),
                                   frozenset(size for size in (_size_of(offer.get("unitSize"), offer.get("title")),) if size))
            for offer in items
        }
        try:
            lines = await _line_offers(store, catalogue, ids, fallback)
        except Exception:  # noqa: BLE001 - zonder folderacties blijft de rest van het oordeel staan
            log.exception("Folderacties bij de aanbiedingen van %s konden niet worden gelezen", store)
    for product_id, found in lines.items():
        spans = [{"start": offers._episode_span(row)["start"], "end": offers._episode_span(row)["end"]} for row in episodes.get(product_id, [])]
        lines[product_id] = _without_own_weeks(found, spans)
        points.setdefault(product_id, []).extend((line["start"], line["price"], "line") for line in lines[product_id] if _number(line.get("price")))
        points[product_id].sort()

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
    # De verpakkingsfamilies (offer_ranking.families): alle verpakkingen van een product op een kaart, de goedkoopste per eenheid
    # gemarkeerd. `known_titles` zegt welke verpakkingen de app ooit zag, voor "andere verpakkingen onbekend".
    known_titles = _known_pack_titles(store, catalogue, packs_known, set(by_base))
    fams = offer_ranking.families(store, packs_known, known_titles)
    # Eerder gekocht geldt voor de hele familie: de 1000 g tegen gewone prijs gekocht houdt de 300 g in de aanbieding in beeld.
    for family in fams.values():
        family["bought"] = any(pack["productId"] in bought_ids for pack in family["packs"])

    for offer in items:
        product_id = str(offer.get("id"))
        effective = _effective(offer)
        stated = _stated_regular(offer)
        if _number(offer.get("unitPrice")) is None and effective is not None and stated is not None:
            offer["unitPrice"] = effective  # zo toont de kaart ook bij "1+1 gratis" van AH wat een stuk kost
        offer["unitPricePer"] = unit_price(effective if effective is not None else offer.get("price"), offer.get("unitSize"))
        # De badge op de kaart: het verschil met de gekozen gewone prijs, of de claim van de winkel als de app er geen kent.
        verdict: dict[str, Any]
        badge: dict[str, Any] | None
        verdict, badge = _verdict_of(effective, stated, _regular_for_verdict(regulars.get(product_id, []), today))
        offer["badge"] = None if badge is None else {**badge, "claimed": _claimed_percent(offer)}
        mine = [row for row in episodes.get(product_id, []) if not row["continued"]]
        started = [row for row in mine if (offers._episode_span(row)["start"] or "") >= since_year]
        line_weeks = [line for line in lines.get(product_id, []) if str(line["start"]) >= since_year]
        verdict["timesThisYear"] = max(len(started) + len(line_weeks), 1)
        verdict["often"] = verdict["timesThisYear"] >= OFTEN_PER_YEAR
        offer["priceLabel"] = price_label(effective, [(day, price) for day, price, _ in points.get(product_id, [])], today)
        offer["yearLow"] = offer_ranking.year_low(effective, points.get(product_id, []), today)
        offer["family"] = offer_ranking.family_of(fams, store, offer.get("title"), offer.get("brand"), product_id)
        offer["bought"] = product_id in bought_ids
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


REGULAR_LIMIT = 500  # zoveel aankoopregels per winkel per nachtelijke ronde: de rest volgt de volgende nacht
REGULAR_RECENT_DAYS = 60  # de gewone prijs van een aankoop van de laatste 60 dagen wordt dagelijks opnieuw bepaald


class _LineSource(NamedTuple):
    """Aankoopregels die een gewone prijs krijgen: de tabel, de kolom met de aankoop en de SQL die per regel `purchase`, `position`,
    het product van de webwinkel (`product_id`), de aankoopdag (`day`), `checked` (`regular_checked_at`) en `stored` (`regular_price`) geeft."""

    table: str
    purchase_column: str
    needed: frozenset[str]
    kind: str  # "order" of "receipt": welke ids (`order_ids` of `receipt_ids`) bij deze bron horen
    sql: str


# Een bonregel heeft AH's artikelnummer, het product van de webwinkel komt uit je eigen koppeling (#238) of de automatische in
# `ah_receipt_products` (`product_links.RECEIPT_LINE_JOIN_SQL`, dezelfde regel als de bon zelf). Zonder koppeling is er geen product,
# en dus geen gewone prijs.
_RECEIPT_PRODUCT = product_links.RECEIPT_LINE_PRODUCT_SQL
_REGULAR_LINES: dict[str, tuple[_LineSource, ...]] = {
    "ah": (
        _LineSource(
            "ah_order_items", "order_id", frozenset({"ah_order_items", "ah_orders"}), "order",
            f"""
            SELECT i.order_id AS purchase, i.position AS position,
                   CASE WHEN i.product_id IS NULL OR i.product_id = 0 THEN NULL ELSE CAST(i.product_id AS TEXT) END AS product_id,
                   {_DAY} AS day, i.regular_checked_at AS checked,
                   i.regular_price AS stored
            FROM ah_order_items i JOIN ah_orders o ON o.order_id = i.order_id
            """,
        ),
        _LineSource(
            "ah_receipt_items", "receipt_id", frozenset({"ah_receipt_items", "ah_receipts", "ah_receipt_products", "product_links"}), "receipt",
            f"""
            SELECT l.receipt_id AS purchase, l.position AS position,
                   CASE WHEN {_RECEIPT_PRODUCT} IS NULL OR {_RECEIPT_PRODUCT} = 0 THEN NULL ELSE CAST({_RECEIPT_PRODUCT} AS TEXT) END AS product_id,
                   substr(r.date_time, 1, 10) AS day, l.regular_checked_at AS checked,
                   l.regular_price AS stored
            FROM ah_receipt_items l JOIN ah_receipts r ON r.receipt_id = l.receipt_id
            {product_links.RECEIPT_LINE_JOIN_SQL}
            """,
        ),
    ),
    "jumbo": (
        _LineSource(
            "jumbo_order_items", "order_id", frozenset({"jumbo_order_items", "jumbo_orders"}), "order",
            f"""
            SELECT i.order_id AS purchase, i.position AS position, NULLIF(i.product_id, '') AS product_id, {_DAY} AS day,
                   i.regular_checked_at AS checked, i.regular_price AS stored
            FROM jumbo_order_items i JOIN jumbo_orders o ON o.order_id = i.order_id
            """,
        ),
    ),
}


def _iso_day(value: Any) -> str | None:
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError:
        return None


async def _chosen_before(
    conn: aiosqlite.Connection, store: str, day: str, receipts: list[dict[str, Any]], ids: list[str] | None = None
) -> dict[str, Chosen]:
    """De gekozen gewone prijs (`regular_price.choose`) per product bij een aankoop op `day`, uit de punten van het jaar ervoor.

    Een aankoop van `day` zelf telt niet als gewone prijs (`purchases_before_until`), de van-prijs van een aanbieding die op `day`
    loopt wel (hij staat op `day`). Alleen een keuze die `judged` is (hoogstens een jaar oud) komt in het resultaat."""
    points: dict[str, list[RegularPoint]] = await _regular_points(
        conn, store, _days_ago(day, YEAR_DAYS), day, receipts=receipts, purchases_before_until=True, ids=ids
    )
    chosen: dict[str, Chosen] = {}
    for product_id, product_points in points.items():
        pick: Chosen | None = choose(product_points, day)
        if pick is not None and pick.judged:
            chosen[product_id] = pick
    return chosen


async def regular_before(store: str, product_ids: list[str], day: str) -> dict[str, float]:
    """De gewone prijs per product bij een aankoop op `day`: dezelfde keuze (`regular_price.choose`) uit dezelfde bronnen als het oordeel
    bij de aanbiedingen (`_regular_points`), uit het jaar vóór `day`. Een bestelling van `day` zelf telt niet mee: anders is een aankoop
    zonder actie zijn eigen gewone prijs. Een product zonder gewone prijs van hoogstens een jaar oud ontbreekt.
    """
    wanted: list[str] = sorted({str(product_id) for product_id in product_ids if product_id not in (None, "")})
    if not wanted or _iso_day(day) is None:
        return {}
    async with _connect() as conn:
        catalogue: _Catalogue | None = await _catalogue_or_none(conn, store, day)
        chosen: dict[str, Chosen] = await _chosen_before(conn, store, day, catalogue.receipts if catalogue else [], wanted)
    return {product_id: pick.price for product_id, pick in chosen.items()}


async def _lines_to_fill(
    conn: aiosqlite.Connection, source: _LineSource, ids: list[Any] | None, *, recent_since: str, checked_before: str, limit: int
) -> list[Any]:
    """De regels van één bron die (opnieuw) een gewone prijs nodig hebben, de nieuwste aankoop eerst.

    Zonder `ids`: nog nooit bepaald, of een aankoop van na `recent_since` die voor het laatst vóór `checked_before` is bepaald. Met
    `ids` (de aankopen die je opent): alle regels van die aankopen die nooit of vóór `checked_before` zijn bepaald, zonder `limit`.
    """
    if ids is not None:
        if not ids:
            return []
        marks = ", ".join("?" * len(ids))
        return list(await conn.execute_fetchall(
            f"SELECT * FROM ({source.sql}) WHERE CAST(purchase AS TEXT) IN ({marks}) AND (checked IS NULL OR checked < ?)",
            (*[str(value) for value in ids], checked_before),
        ))
    return list(await conn.execute_fetchall(
        f"""SELECT * FROM ({source.sql}) WHERE checked IS NULL OR (day >= ? AND checked < ?)
            ORDER BY day DESC, purchase DESC, position LIMIT ?""",
        (recent_since, checked_before, limit),
    ))


def _price_to_store(stored: Any, pick: Chosen | None) -> float | None:
    """De gewone prijs die een aankoopregel krijgt. Een bewaarde prijs blijft staan tenzij de nieuwe keuze vers is (`Chosen.fresh`):
    een oudere bron mag een eerder bepaalde prijs niet vervangen. Een regel zonder prijs krijgt de keuze, of NULL zonder keuze."""
    if pick is None:
        return stored
    return pick.price if stored is None or pick.fresh else stored


class _Picks:
    """De keuzes van `_chosen_before` per aankoopdag, voor `fill_regular_prices`. De catalogus (voor de bonnen en de ingelezen
    aankopen) wordt pas gelezen bij de eerste regel die hem nodig heeft: een aankoop openen zonder te vullen regels bouwt hem niet."""

    def __init__(self, conn: aiosqlite.Connection, store: str, today: str) -> None:
        self.conn: aiosqlite.Connection = conn
        self.store: str = store
        self.today: str = today
        self.receipts: list[dict[str, Any]] | None = None
        self.by_day: dict[str, dict[str, Chosen]] = {}

    async def ready(self) -> bool:
        """Leest de catalogus bij de eerste vraag. False als hij niet te lezen is (`_catalogue_or_none` logt dat)."""
        if self.receipts is None:
            catalogue: _Catalogue | None = await _catalogue_or_none(self.conn, self.store, self.today)
            if catalogue is None:
                return False
            self.receipts = catalogue.receipts
        return True

    async def pick(self, day: str, product_id: str) -> Chosen | None:
        """De keuze voor dit product bij een aankoop op `day`. Pas na `ready`."""
        if day not in self.by_day:
            self.by_day[day] = await _chosen_before(self.conn, self.store, day, self.receipts or [])
        return self.by_day[day].get(product_id)


async def _line_updates(picks: _Picks, lines: list[Any], stamp: str) -> list[tuple[Any, ...]] | None:
    """Per regel de nieuwe `regular_price` en `regular_checked_at`, of None als de catalogus niet te lezen is. Dan blijft alles
    ongestempeld: een regel die zonder de bonnen en de ingelezen aankopen is bepaald, komt nooit meer terug."""
    updates: list[tuple[Any, ...]] = []
    for line in lines:
        day: str | None = _iso_day(line["day"])
        pick: Chosen | None = None
        if day is not None and line["product_id"] is not None:
            if not await picks.ready():
                return None
            pick = await picks.pick(day, str(line["product_id"]))
        updates.append((_price_to_store(line["stored"], pick), stamp, line["purchase"], line["position"]))
    return updates


async def fill_regular_prices(
    store: str, *, limit: int = REGULAR_LIMIT, recent_days: int = REGULAR_RECENT_DAYS, now: str | None = None,
    order_ids: list[Any] | None = None, receipt_ids: list[Any] | None = None,
) -> int:
    """Bewaar per aankoopregel de gewone prijs (`_chosen_before`) in `regular_price`, met het moment in `regular_checked_at`.

    Het gaat om de bestellingen van AH en Jumbo en de kassabonnen van AH (via het gekoppelde product van de webwinkel). Een regel krijgt
    een prijs als hij nooit is bepaald, of als de aankoop van de laatste `recent_days` dagen is en de prijs meer dan een dag oud is
    (er kan sindsdien een meting uit het venster zijn bijgekomen). Hooguit `limit` regels, de nieuwste aankopen eerst. Met `order_ids`
    of `receipt_ids` alleen de regels van die aankopen, ongeacht `limit` en `recent_days`. Een bewaarde prijs wordt alleen vervangen
    door een verse keuze (`_price_to_store`). Een regel zonder keuze houdt wat er stond (NULL als er niets stond) en krijgt wel
    `regular_checked_at`, zodat hij niet elke nacht terugkomt. Is de catalogus niet te lezen, dan stopt de ronde zonder de regels
    met een product te stempelen (`_line_updates`). Geeft het aantal bijgewerkte regels.
    """
    stamp = now or _now()
    checked_before = (datetime.fromisoformat(stamp) - timedelta(days=1)).isoformat(timespec="seconds")
    recent_since = _days_ago(stamp[:10], recent_days)
    targeted = order_ids is not None or receipt_ids is not None
    done = 0
    async with _connect() as conn:
        tables = await _tables(conn)
        picks: _Picks = _Picks(conn, store, stamp[:10])
        for source in _REGULAR_LINES.get(store, ()):
            if not source.needed <= tables or (not targeted and done >= limit):
                continue
            ids = (order_ids if source.kind == "order" else receipt_ids) if targeted else None
            if targeted and ids is None:
                continue
            await ah_orders._ensure_columns(conn, source.table, ah_orders.REGULAR_COLUMNS)
            lines = await _lines_to_fill(conn, source, ids, recent_since=recent_since, checked_before=checked_before, limit=limit - done)
            updates: list[tuple[Any, ...]] | None = await _line_updates(picks, lines, stamp)
            if updates is None:
                break
            await conn.executemany(
                f"UPDATE {source.table} SET regular_price = ?, regular_checked_at = ? WHERE {source.purchase_column} = ? AND position = ?",
                updates,
            )
            await conn.commit()
            done += len(updates)
    return done


async def fill_regular_prices_nightly() -> dict[str, int] | None:
    """Voor de nachtelijke taak: de gewone prijs bij AH- en Jumbo-aankopen, als er iets te doen was. Een fout laat de taak doorgaan."""
    try:
        lines = await fill_regular_prices("ah") + await fill_regular_prices("jumbo")
    except Exception:  # noqa: BLE001 - de gewone prijs per regel is een extraatje bij de aanbiedingen
        log.exception("Gewone prijs per aankoopregel: de nachtelijke ronde mislukte")
        return None
    return {"lines": lines} if lines else None


def _shop_store(store: Any) -> str:
    """Een winkel waar we kunnen zoeken. Lidl en Aldi hebben geen webwinkel (`offers.OFFER_ONLY_STORES`): daar kan het niet."""
    key = offers._valid_store(store)
    if key not in offers.SHOP_STORES:
        name = offers.STORE_NAMES.get(key, key)
        raise ValueError(tr(
            f"Bij {name} kan MealieShopper niet zoeken: van die winkel zijn er alleen de aanbiedingen",
            f"MealieShopper cannot search at {name}: that store only has its offers",
        ))
    return key


def _search_function(store: str) -> Callable[..., Awaitable[list[dict[str, Any]]]]:
    from . import crisp, jumbo, picnic  # pylint: disable=import-outside-toplevel - alleen nodig bij het vergelijken

    found = {"ah": ah.search_products, "jumbo": jumbo.search_products, "picnic": picnic.search_products, "crisp": crisp.search_products}.get(store)
    if found is None:
        raise ValueError(tr("Bij deze winkel kan MealieShopper niet zoeken", "MealieShopper cannot search at this store"))
    return found


def _search_query(title: Any) -> str:
    """De zoekterm voor andere verpakkingen: de naam zonder maten en verpakkingswoorden, maar met de hoofdletters van de winkel."""
    text = re.sub(r"(\d)\s*-\s*(pack|pak)\b", r"\1 \2", str(title or ""), flags=re.IGNORECASE)
    words = [word for word in _SIZE_RE.sub(" ", text).split() if ah_lists._fold(word) not in _PACK_WORDS]
    return " ".join(words).strip() or str(title or "").strip()


async def _known_product(store: str, product_id: str) -> dict[str, Any] | None:
    """Een product uit de aanbiedingen van nu, anders uit de bewaarde prijzen: naam, merk, verpakking en prijs per stuk."""
    current = next((offer for offer in (await offers.load(store))["offers"] if str(offer["id"]) == product_id), None)
    if current:
        effective = _effective(current)
        return {"id": product_id, "title": current["title"], "brand": current.get("brand"), "unitSize": current.get("unitSize"),
                "price": effective if effective is not None else current.get("price"), "onOffer": True, "promoLabel": current.get("promoLabel") or ""}
    async with _connect() as conn:
        row = await auth.fetchone(
            conn,
            "SELECT * FROM price_observations WHERE store = ? AND product_id = ? AND source != ? ORDER BY day DESC LIMIT 1",
            (store, product_id, PUBLIC_OBSERVED_UNKNOWN),
        )
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


async def compare(store: Any, product_id: Any) -> dict[str, Any]:
    """Andere verpakkingen van hetzelfde product, per kilo, liter of stuk naast elkaar. Doet één zoekopdracht bij de winkel.

    De gevonden producten met dezelfde soort eenheid staan op prijs per eenheid, de goedkoopste eerst. `same` zegt of het hetzelfde
    product is (dezelfde naam zonder maten en verpakkingswoorden, en hetzelfde merk), `sameKind` of het hetzelfde soort product is (zie
    `_same_kind`; daarop filtert het venster standaard), `sameBrand` of het merk gelijk is, `cheaper` of het per eenheid goedkoper is dan
    het gekozen product, en `rank` (0 tot 1) waar het tussen de rest staat, voor de kleur. De gevonden prijzen worden ook bewaard.
    """
    store = _shop_store(store)
    product_id = str(product_id or "").strip()
    product = await _known_product(store, product_id) if product_id else None
    if not product:
        raise ValueError(tr("Dit product is niet bekend bij de aanbiedingen of de bewaarde prijzen", "This product is not known in the offers or the tracked prices"))
    product["unitPrice"] = unit_price(product["price"], product["unitSize"])
    query = _search_query(product["title"])
    found = await recorded(store, _search_function(store))(query, COMPARE_LIMIT)
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
    kind = _kind_words(product["title"], product.get("brand"))
    for index, candidate in enumerate(candidates):
        candidate["self"] = candidate["id"] == product_id
        candidate["sameKind"] = candidate["self"] or _same_kind(kind, candidate["title"])
        candidate["sameBrand"] = candidate["self"] or bool(brand) and ah_lists._fold(candidate.get("brand") or "") == brand
        candidate["same"] = candidate["self"] or (
            _same_product(base, candidate["title"]) and (not brand or not candidate.get("brand") or ah_lists._fold(candidate["brand"]) == brand)
        )
        candidate["cheaper"] = mine is not None and not candidate["self"] and candidate["unitPrice"]["value"] < mine * (1 - PACK_MARGIN)
        candidate["rank"] = round(index / (len(candidates) - 1), 2) if len(candidates) > 1 else 0
    return {"store": store, "product": product, "query": query, "unit": unit, "kind": " ".join(kind), "candidates": candidates}


# --- de controlelijst van het weekmenu -------------------------------------------------------------------------------

CHECK_LIST_SIZE = 5  # zoveel producten toont de controlelijst zonder vergelijking (zoals voorheen)
CHECK_LIST_EXTRAS = 6  # hooguit zoveel vergelijkingsopties onder het voorgestelde product
ORGANIC_QUERY = "biologisch"  # de extra zoekopdracht met *Liever biologisch*: "biologisch rundergehakt"


def _kind_test(first: dict[str, Any], query: Any) -> tuple[Callable[[dict[str, Any]], bool], Callable[[dict[str, Any]], bool]] | None:
    """(hetzelfde soort, precies dat soort) voor de controlelijst: uit het ingrediënt (`_ingredient_kind`) als het voorstel `first`
    het noemt, anders uit de naam van het voorstel. "Precies" betekent zonder extra woorden: "AH Biologisch Rundergehakt", niet
    "AH Biologisch Rundergehakt balletjes". None als er geen soort is."""
    ingredient = _ingredient_kind(first, query) if query else None
    if ingredient:
        # Bij AH ook dezelfde hoofdcategorie: "Van Gilse Fijne kristalsuiker" en "AH Appelmoes 0% suiker toegevoegd" noemen allebei
        # suiker, maar de appelmoes staat bij AH ergens anders. Zonder categorie (Picnic, Jumbo, Crisp) telt alleen het ingrediënt.
        category = first.get("category")

        def same_kind(c: dict[str, Any]) -> bool:
            other = c.get("category")
            return _same_ingredient(ingredient, c.get("title")) and (not category or not other or other == category)

        return (
            same_kind,
            lambda c: all(_matches(ingredient, word) for word in _kind_words(c.get("title"), c.get("brand"))),
        )
    kind = _kind_words(first.get("title"), first.get("brand"))
    if not kind:
        return None
    return (
        lambda c: _same_kind(kind, c.get("title")),
        lambda c: set(_kind_words(c.get("title"), c.get("brand"))) == set(kind),
    )


def _organic_pick(first: dict[str, Any], candidates: list[dict[str, Any]], query: Any = None) -> dict[str, Any] | None:
    """Het biologische product van dezelfde soort als het gewone voorstel `first`, in de volgorde van de winkel. Eerst precies
    dezelfde soort: bij rundergehakt "AH Biologisch Rundergehakt 500g" en niet "AH Biologisch Rundergehakt balletjes"."""
    test = _kind_test(first, query)
    if test is None:
        return None
    same_kind, exact_kind = test
    same = [c for c in candidates if c.get("organic") and same_kind(c)]
    exact = [c for c in same if exact_kind(c)]
    return (exact or same or [None])[0]


async def organic_first(
    candidates: list[dict[str, Any]], search_more: Callable[[], Awaitable[list[dict[str, Any]]]], query: Any = None
) -> tuple[list[dict[str, Any]], bool]:
    """Met *Liever biologisch* (#218): een biologisch product van dezelfde soort vooraan, als de winkel het heeft.

    De kandidaten hebben `organic` (zie `is_organic`). Is het voorstel al biologisch, dan blijft alles zoals het is. Anders het
    eerste biologische product van dezelfde soort uit de zoekresultaten; zit dat er niet bij, dan één extra zoekopdracht
    (`search_more`, "biologisch rundergehakt"). Geeft ook die niets, dan blijft het gewone product voorop. Geeft (kandidaten,
    gevonden) terug; het gewone product blijft in de lijst, zodat de vergelijking het als goedkoper per eenheid kan tonen."""
    if not candidates:
        return candidates, False
    first = candidates[0]
    if first.get("organic"):
        return candidates, True
    pick = _organic_pick(first, candidates, query) or _organic_pick(first, await search_more(), query)
    if pick is None:
        return candidates, False
    key = str(pick.get("productId"))
    return [pick] + [c for c in candidates if str(c.get("productId")) != key], True


def per_item_price(candidate: dict[str, Any]) -> float | None:
    """Wat één stuk van een kandidaat uit de controlelijst kost: bij een aanbieding met de actie (ook "1+1 gratis", waar AH de gewone
    prijs als prijs geeft), anders de prijs."""
    deal = candidate.get("deal") if isinstance(candidate.get("deal"), dict) else {}
    price = _number(candidate.get("price"))
    if deal and deal.get("validity") != deals.EXPIRED:
        if _number(deal.get("unitPrice")) is not None:
            return _number(deal.get("unitPrice"))
        mechanic = deals.parse_mechanic(deal.get("label"))
        if price is not None and mechanic["type"] in _FROM_REGULAR:
            return _number(deals.effective_unit_price(mechanic, price, price))
    return price


def check_list_candidates(
    candidates: list[dict[str, Any]], compare: bool, query: Any = None, kind_of: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """De producten voor één regel van de controlelijst, elk met zijn prijs per kilo, liter of stuk (`unitPrice`).

    Zonder `compare` (zelf zoeken met *Zoek een ander product*) blijven de eerste vijf zoals ze zijn. Met `compare` (het weekmenu in het
    mandje) komt na het voorgestelde product alleen wat **hetzelfde product** is in een andere maat (`same`) of **per eenheid goedkoper**
    (`cheaper`), en alleen hetzelfde soort product (`_kind_test`, uit het ingrediënt `query` als het voorstel dat noemt): bij
    rundergehakt dus geen gemengd gehakt of gehaktballen, bij eieren wel alle eieren (#223). De
    goedkoopste per eenheid eerst, hooguit `CHECK_LIST_EXTRAS`. Andere zoekresultaten vallen weg; die vind je met *Zoek een ander product*.

    `kind_of` is het product waaruit de soort volgt, standaard het voorstel. Met *Liever biologisch* is dat het gewone voorstel: zo blijft
    de soort dezelfde als het biologische product anders heet ("AH Biologisch Eieren" tegenover "AH Witte vrije uitloopeieren M").
    """
    for candidate in candidates:
        candidate["unitPrice"] = unit_price(per_item_price(candidate), candidate.get("unitSize"))
    if not compare or not candidates:
        return candidates[:CHECK_LIST_SIZE]
    first = candidates[0]
    first["suggested"] = True
    test = _kind_test(kind_of or first, query)
    base = base_title(first.get("title"))
    brand = ah_lists._fold(first.get("brand") or "")
    mine = first["unitPrice"]
    seen = {str(first.get("productId"))}
    extras = []
    for candidate in candidates[1:]:
        key = str(candidate.get("productId"))
        if key in seen or not (test[0](candidate) if test else _same_kind([], candidate.get("title"))):
            continue
        seen.add(key)
        other_brand = ah_lists._fold(candidate.get("brand") or "")
        same = _same_product(base, candidate.get("title")) and (not brand or not other_brand or other_brand == brand)
        unit = candidate["unitPrice"]
        cheaper = bool(mine and unit and unit["unit"] == mine["unit"] and unit["value"] < mine["value"] * (1 - PACK_MARGIN))
        if same or cheaper:
            extras.append({**candidate, "same": same, "cheaper": cheaper})
    extras.sort(key=lambda c: (c["unitPrice"]["value"] if c["unitPrice"] else float("inf")))
    return [first] + extras[:CHECK_LIST_EXTRAS]
