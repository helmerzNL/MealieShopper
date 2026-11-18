"""Het leesmodel van de prijsgeschiedenis (`prices.history`, /api/prices/history): per winkel en product vooraf in de database
wat bij elke aanvraag hetzelfde is, bijgewerkt na het schrijven.

Zonder leesmodel liep elke aanvraag zonder zoekterm over alle producten van de catalogus (bij AH 74.000) om de pagina, het
totaal en de keuzelijsten te bepalen, en las ze daarna de prijzen en de lijnacties van de pagina opnieuw. Die gegevens
veranderen bijna alleen door de taken van de nacht en de synchronisaties, dus het lezen moet snel zijn en het bijwerken mag
tijd kosten.

Twee tabellen met afgeleide gegevens:

- `price_history_products`: per product van de catalogus (`prices._known_products`) wat de lijst nodig heeft om te zoeken, te
  filteren en te sorteren. De laatste dag, de namen, de verpakkingen, merk en categorie, en van de groep van nummers van
  hetzelfde product (`prices._twins`) het nummer dat de lijst toont (`lead`) en de laatste dag van de groep (`day`). Een index
  op `day` geeft de pagina zonder zoekterm zonder iets te sorteren.
- `price_history_docs`: per product de bewaarde prijzen (`price_observations`) en de lijnacties (`prices._line_offers`), als
  JSON. De eerste aanvraag die het product toont, vult het document.

De grens: wat van de gebruiker is of van vandaag afhangt, komt er bij het lezen bij, zoals voorheen. Dat zijn de aankopen en
de bonnen, de bewaarde foto's, de aanbiedingen (`offers.history_of`, die een open periode tot nu laat lopen), een aanbieding
die nog moet beginnen, Eerder gekocht en het product uit een link. Die kun je buiten de taken om veranderen, of ze hangen van
de klok af, en lezen kost daar maar een paar milliseconden per pagina.

Bijhouden:

- `price_history_meta` zegt per winkel van welke versie van de catalogus (`catalogue_versions`, het token en `products`) en
  van welke dag de rijen zijn. Verandert er iets anders dan een prijs, dan bouwt de volgende aanvraag de rijen opnieuw op uit
  de catalogus. Dat loopt in een thread en schrijft alleen de rijen die anders werden. `generation` gaat dan omhoog, en een
  document van een eerdere generatie geldt niet meer.
- Een prijs in `price_observations` zet via een trigger zijn product in `price_history_stale`. De volgende aanvraag werkt
  alleen die producten bij: de laatste dag (zoals `prices._with_last`) en hun document. De trigger schrijft alleen in die
  eigen tabel, dus ook een oudere versie van de app (na een terugkeer naar 4.25.1) kan gewoon prijzen schrijven. De rijen
  wachten dan tot een versie met het leesmodel ze leest.

Na het terugzetten van een back-up heeft `catalogue_versions` een nieuw token, dus de volgende aanvraag bouwt alles opnieuw op.
De tabellen horen bij `backup.EXCLUDED_TABLES`: ze zijn afgeleid.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import sqlite3
from collections.abc import AsyncIterator, Iterable, Sequence
from typing import Any, NamedTuple

import aiosqlite

from . import auth, compute

SCHEMA_NAME: str = "price_history_index"
TABLES: tuple[str, ...] = ("price_history_products", "price_history_docs", "price_history_meta", "price_history_stale")
SCHEMA: str = """
-- Per product van de catalogus (`prices._Known`): `names` en `sizes` als JSON, `search` de namen met en zonder spaties onder
-- elkaar (om met LIKE voor te filteren), `lead` het nummer dat de lijst voor de groep van dit product toont, `twins` de groep
-- als JSON (NULL zonder andere nummers) en `day` de laatste dag van de groep.
CREATE TABLE IF NOT EXISTS price_history_products (
  store TEXT NOT NULL,
  product_id TEXT NOT NULL,
  last TEXT NOT NULL,
  names TEXT NOT NULL,
  sizes TEXT NOT NULL,
  brand TEXT NOT NULL,
  category TEXT NOT NULL,
  search TEXT NOT NULL,
  lead TEXT NOT NULL,
  twins TEXT,
  day TEXT NOT NULL,
  PRIMARY KEY (store, product_id)
);
-- De pagina zonder zoekterm: alleen de rijen die de lijst toont, op de laatste dag van hun groep. Een winkel, en alle winkels.
CREATE INDEX IF NOT EXISTS price_history_products_page ON price_history_products (store, day, product_id) WHERE product_id = lead;
CREATE INDEX IF NOT EXISTS price_history_products_pages ON price_history_products (day, store, product_id) WHERE product_id = lead;
CREATE INDEX IF NOT EXISTS price_history_products_group ON price_history_products (store, lead);
-- De aantallen per merk en per categorie (`page`): de index heeft alles wat de telling leest, op volgorde van wat ze telt.
CREATE INDEX IF NOT EXISTS price_history_products_brands ON price_history_products (store, brand, category, product_id) WHERE product_id = lead;
CREATE INDEX IF NOT EXISTS price_history_products_categories ON price_history_products (store, category, brand, product_id)
  WHERE product_id = lead;
CREATE TABLE IF NOT EXISTS price_history_docs (
  store TEXT NOT NULL,
  product_id TEXT NOT NULL,
  generation INTEGER NOT NULL,
  doc TEXT NOT NULL,
  PRIMARY KEY (store, product_id)
);
CREATE TABLE IF NOT EXISTS price_history_meta (
  store TEXT PRIMARY KEY,
  version TEXT NOT NULL,
  day TEXT NOT NULL,
  prices INTEGER NOT NULL,
  receipts TEXT NOT NULL,
  generation INTEGER NOT NULL
);
-- `n` telt de prijzen sinds het product hier kwam: wie bijwerkt, haalt alleen de rij weg met de `n` die hij las.
CREATE TABLE IF NOT EXISTS price_history_stale (
  store TEXT NOT NULL,
  product_id TEXT NOT NULL,
  n INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (store, product_id)
);
"""
_MARK: str = (
    "INSERT INTO price_history_stale (store, product_id) VALUES ({row}.store, {row}.product_id) "
    "ON CONFLICT (store, product_id) DO UPDATE SET n = n + 1;"
)
TRIGGERS: dict[str, str] = {
    "price_history_stale_insert": f"AFTER INSERT ON price_observations BEGIN {_MARK.format(row='NEW')} END",
    "price_history_stale_update": f"AFTER UPDATE ON price_observations BEGIN {_MARK.format(row='OLD')} {_MARK.format(row='NEW')} END",
    "price_history_stale_delete": f"AFTER DELETE ON price_observations BEGIN {_MARK.format(row='OLD')} END",
}
_TRIGGERS_SQL: str = "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'price!_history!_stale!_%' ESCAPE '!'"
CHUNK: int = 2_000  # zoveel rijen per executemany bij het opnieuw opbouwen, en zoveel producten per query
DOC_BUSY_SECONDS: float = 0.1  # zo lang wacht het bewaren van documenten op een andere schrijver: daarna slaat het over

# De velden van `prices._Known` (een test houdt ze gelijk): de laatste dag, de namen, de verpakkingen, merk en categorie.
LAST: int = 0
NAMES: int = 1
SIZES: int = 2
BRAND: int = 3
CATEGORY: int = 4

Known = tuple[str, tuple[str, ...], tuple[tuple[float, str], ...], str, str, str]
# Een rij van `price_history_products` zonder de winkel: product, last, names, sizes, brand, category, search, lead, twins, day.
Row = tuple[str, str, str, str, str, str, str, str, str | None, str]
_COLUMNS: str = "product_id, last, names, sizes, brand, category, search, lead, twins, day"
_READ_COLUMNS: str = "store, product_id, last, names, sizes, brand, category, lead, twins, day"
_UPSERT_SQL: str = (
    f"INSERT INTO price_history_products (store, {_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
    "ON CONFLICT (store, product_id) DO UPDATE SET last = excluded.last, names = excluded.names, sizes = excluded.sizes, "
    "brand = excluded.brand, category = excluded.category, search = excluded.search, lead = excluded.lead, twins = excluded.twins, "
    "day = excluded.day"
)
_META_SQL: str = (
    "INSERT INTO price_history_meta (store, version, day, prices, receipts, generation) VALUES (?, ?, ?, ?, ?, 1) "
    "ON CONFLICT (store) DO UPDATE SET version = excluded.version, day = excluded.day, prices = excluded.prices, "
    "receipts = excluded.receipts, generation = generation + 1"
)
_WANTED: str = "product_id IN (SELECT value FROM json_each(?))"
# Een document bewaren alleen als niets het intussen ouder maakte: geen nieuwe prijs van het product en dezelfde generatie en
# dezelfde stand van `prices` als toen het bijwerken klaar was.
_SAVE_DOC_SQL: str = (
    "INSERT INTO price_history_docs (store, product_id, generation, doc) SELECT ?, ?, ?, ? "
    "WHERE NOT EXISTS (SELECT 1 FROM price_history_stale WHERE store = ? AND product_id = ?) "
    "AND EXISTS (SELECT 1 FROM price_history_meta WHERE store = ? AND generation = ? AND prices = ?) "
    "ON CONFLICT (store, product_id) DO UPDATE SET generation = excluded.generation, doc = excluded.doc"
)
_OBSERVED_COLUMNS: tuple[str, ...] = ("day", "price", "unit_size", "url", "source", "title", "brand", "image")


async def prepare(conn: aiosqlite.Connection) -> None:
    """De tabellen (een keer per proces en database, `auth.ensure_schema`) en de triggers op `price_observations`, na het schema
    van de prijzen. De triggers controleert elke aanvraag, zoals `prices._catalogue_row` die van de catalogus: een tabel die
    opnieuw is aangemaakt, verliest ze. Ontbrak er een, dan kan er een prijs zijn geschreven die niemand markeerde, en dan gaat
    `price_history_meta` leeg: de volgende aanvraag bouwt alles opnieuw op."""
    await auth.ensure_schema(conn, SCHEMA_NAME, SCHEMA)
    present: set[str] = {row[0] for row in await conn.execute_fetchall(_TRIGGERS_SQL)}
    missing: list[str] = [name for name in TRIGGERS if name not in present]
    if missing:
        for name in missing:
            await conn.execute(f"CREATE TRIGGER IF NOT EXISTS {name} {TRIGGERS[name]}")
        await conn.execute("DELETE FROM price_history_meta")
        await conn.commit()


class Before(NamedTuple):
    """Wat de aanvraag zag voordat ze de catalogus las: de versie en `prices` van `catalogue_versions`, de rij van
    `price_history_meta` en de gemarkeerde producten met hun `n`. In die volgorde, zodat de catalogus minstens zo nieuw is."""

    version: str
    prices: int
    meta: Any  # sqlite3.Row of None
    stale: tuple[tuple[str, int], ...]


async def before(conn: aiosqlite.Connection, store: str, version: str, prices: int) -> Before:
    meta: Any = await auth.fetchone(conn, "SELECT version, day, prices, receipts, generation FROM price_history_meta WHERE store = ?", (store,))
    stale: list[Any] = list(await conn.execute_fetchall("SELECT product_id, n FROM price_history_stale WHERE store = ?", (store,)))
    return Before(version, prices, meta, tuple((str(row[0]), int(row[1])) for row in stale))


class Fresh(NamedTuple):
    """De stand van het leesmodel van een winkel na `up_to_date`: de generatie van de documenten en `prices` in de meta."""

    generation: int
    prices: int


# Een bouw per (database, winkel, versie, dag, prijzen) tegelijk, en een keer bijwerken per lijst van gemarkeerde producten.
_REBUILDS: compute.SingleFlight[tuple[Any, ...], Fresh] = compute.SingleFlight()
_UPDATES: compute.SingleFlight[tuple[Any, ...], None] = compute.SingleFlight()


async def up_to_date(
    store: str, today: str, seen: Before, products: dict[str, Known], twins: dict[str, tuple[str, ...]], receipts: list[dict[str, Any]]
) -> Fresh:
    """Brengt de rijen van een winkel bij met de catalogus van de aanvraag (`products`, `twins` en `receipts` van
    `prices._catalogue`, gelezen na `seen`).

    Een andere versie of dag: alles opnieuw. Alleen nieuwe prijzen: de gemarkeerde producten, tenzij de gekoppelde bonregels
    anders werden (een prijs kan bepalen welk product met dezelfde naam een regel krijgt, `prices._catch_up`). Dan ook alles."""
    meta: Any = seen.meta
    key: tuple[Any, ...] = (str(auth.db_path()), store, seen.version, today, seen.prices)
    if meta is None or meta["version"] != seen.version or meta["day"] != today:
        return await _REBUILDS.get(key, lambda: _rebuild(store, today, seen, products, twins, receipts))
    if meta["prices"] != seen.prices and await compute.run(receipts_digest, receipts) != meta["receipts"]:
        return await _REBUILDS.get(key, lambda: _rebuild(store, today, seen, products, twins, receipts))
    if seen.stale or meta["prices"] != seen.prices:
        await _UPDATES.get((str(auth.db_path()), store, seen.prices, seen.stale), lambda: _apply_stale(store, seen))
    return Fresh(int(meta["generation"]), seen.prices)


@contextlib.asynccontextmanager
async def _writing() -> AsyncIterator[aiosqlite.Connection]:
    """Een eigen verbinding met een schrijfslot (`BEGIN IMMEDIATE`) die commit als alles lukte. Een eigen verbinding, want het
    bijwerken loopt door als de aanvraag die het startte wegvalt (`compute.SingleFlight`). De rijen zijn gewone tuples: bij AH
    zijn het er 74.000, en een `sqlite3.Row` volgt de GC."""
    async with auth.connect() as conn:
        conn.row_factory = None
        await conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            await conn.rollback()
            raise
        await conn.commit()


async def _rebuild(
    store: str, today: str, seen: Before, products: dict[str, Known], twins: dict[str, tuple[str, ...]], receipts: list[dict[str, Any]]
) -> Fresh:
    """Schrijft de rijen van een winkel opnieuw, alleen wat anders werd, en hoogt de generatie op. Het rekenwerk (de rijen maken
    en vergelijken, bij AH 74.000) loopt via `compute.run`, het schrijven in stukken van `CHUNK`."""
    rows: list[Row] = await compute.run(rows_of, products, twins)
    digest: str = await compute.run(receipts_digest, receipts)
    async with _writing() as conn:
        current: Any = await auth.fetchone(conn, "SELECT version, day, prices, generation FROM price_history_meta WHERE store = ?", (store,))
        if covers(current, seen, today):
            return Fresh(int(current[3]), int(current[2]))
        old: list[Any] = list(await conn.execute_fetchall(f"SELECT {_COLUMNS} FROM price_history_products WHERE store = ?", (store,)))
        changed, gone = await compute.run(changes, store, rows, old)
        for chunk in _chunks(gone):
            await conn.executemany("DELETE FROM price_history_products WHERE store = ? AND product_id = ?", chunk)
        for chunk in _chunks(changed):
            await conn.executemany(_UPSERT_SQL, chunk)
        await conn.execute("DELETE FROM price_history_docs WHERE store = ?", (store,))
        await _clear_stale(conn, store, seen.stale)
        await conn.execute(_META_SQL, (store, seen.version, today, seen.prices, digest))
        found: Any = await auth.fetchone(conn, "SELECT generation FROM price_history_meta WHERE store = ?", (store,))
    return Fresh(int(found[0]), seen.prices)


def covers(current: Any, seen: Before, today: str) -> bool:
    """Of de meta (`current`: versie, dag, prijzen, generatie) al minstens zo nieuw is als wat de aanvraag zag: dan schreef een
    andere aanvraag de rijen tijdens het wachten op het schrijfslot al, met een catalogus die minstens zo nieuw was. Schrijven
    met de oudere catalogus zou een markering kunnen terugdraaien die die andere al wegnam. `products` en `prices` gaan bij
    hetzelfde token alleen omhoog."""
    if current is None or current[1] != today:
        return False
    token, _, products = str(current[0]).partition(":")
    seen_token, _, seen_products = seen.version.partition(":")
    return token == seen_token and int(products) >= int(seen_products) and int(current[2]) >= seen.prices


def changes(store: str, rows: list[Row], old: list[Any]) -> tuple[list[tuple[Any, ...]], list[tuple[str, str]]]:
    """Wat `_rebuild` schrijft: de rijen die nieuw of anders zijn (met de winkel ervoor, voor `_UPSERT_SQL`) en de producten die
    weg zijn. Puur, voor `compute.run`."""
    before: dict[str, tuple[Any, ...]] = {row[0]: tuple(row[1:]) for row in old}
    changed: list[tuple[Any, ...]] = [(store, *row) for row in rows if before.get(row[0]) != row[1:]]
    kept: set[str] = {row[0] for row in rows}
    return changed, [(store, product_id) for product_id in sorted(set(before) - kept)]


def receipts_digest(receipts: Iterable[dict[str, Any]]) -> str:
    """Wat de gekoppelde bonregels aan de catalogus bijdragen (product, namen, verpakking en dag), als SHA-256. Puur, voor
    `compute.run`: bij AH tienduizend regels."""
    digest: Any = hashlib.sha256()
    for row in receipts:
        fields: tuple[Any, ...] = tuple(row.get(key) for key in ("product_id", "title", "brand", "unit_size", "day", "receipt_name"))
        digest.update(repr(fields).encode("utf-8"))
    return digest.hexdigest()


def _search(names: Sequence[str]) -> str:
    """De namen met en zonder spaties, elk op een regel: `prices._Query.wants` zoekt een woord in beide."""
    return "\n".join([*names, *(name.replace(" ", "") for name in names)])


def rows_of(products: dict[str, Known], twins: dict[str, tuple[str, ...]]) -> list[Row]:
    """De rijen van `price_history_products` voor een catalogus. Het nummer dat de lijst toont, is zoals in
    `prices._history_candidates` het nummer met de nieuwste gegevens (en bij gelijke dag het hoogste). Puur, voor `compute.run`:
    bij AH 74.000 producten."""
    rows: list[Row] = []
    for product_id, known in products.items():
        group: tuple[str, ...] = twins.get(product_id, (product_id,))
        lead: str = max(group, key=lambda twin: (products[twin][LAST], twin))
        day: str = max(products[twin][LAST] for twin in group)
        names: tuple[str, ...] = known[NAMES]
        rows.append(
            (
                product_id,
                known[LAST],
                json.dumps(names, ensure_ascii=False),
                json.dumps(known[SIZES], ensure_ascii=False),
                known[BRAND],
                known[CATEGORY],
                _search(names),
                lead,
                json.dumps(group, ensure_ascii=False) if product_id in twins else None,
                day,
            )
        )
    return rows


def _chunks(items: Sequence[Any]) -> Iterable[Sequence[Any]]:
    for start in range(0, len(items), CHUNK):
        yield items[start : start + CHUNK]


async def _clear_stale(conn: aiosqlite.Connection, store: str, stale: Sequence[tuple[str, int]]) -> None:
    """Haalt de gemarkeerde producten weg die nog dezelfde `n` hebben: kwam er intussen een prijs bij, dan blijft de markering."""
    for chunk in _chunks(stale):
        await conn.executemany("DELETE FROM price_history_stale WHERE store = ? AND product_id = ? AND n = ?", [(store, pid, n) for pid, n in chunk])


async def _apply_stale(store: str, seen: Before) -> None:
    """Werkt de gemarkeerde producten van een winkel bij. Een prijs van een bekend product verandert geen naam en geen verpakking
    (anders was de versie van de catalogus hoger), alleen de laatste dag, en die schuift alleen op (`prices._with_last`).
    Daarmee kan het nummer dat de groep toont en de dag van de groep veranderen. Het document van het product gaat weg."""
    async with _writing() as conn:
        stored: list[Any] = []
        observed: list[Any] = []
        for chunk in _chunks([pid for pid, _ in seen.stale]):
            wanted: str = json.dumps(list(chunk), ensure_ascii=False)
            stored += await conn.execute_fetchall(
                f"SELECT product_id, last FROM price_history_products WHERE store = ? AND {_WANTED}", (store, wanted)
            )
            observed += await conn.execute_fetchall(
                f"SELECT product_id, MAX(day) FROM price_observations WHERE store = ? AND {_WANTED} GROUP BY product_id", (store, wanted)
            )
            await conn.execute(f"DELETE FROM price_history_docs WHERE store = ? AND {_WANTED}", (store, wanted))
        later: list[tuple[str, str, str]] = await compute.run(later_days, store, stored, observed)
        if later:
            await conn.executemany("UPDATE price_history_products SET last = ? WHERE store = ? AND product_id = ?", later)
            members: list[Any] = []
            for chunk in _chunks([pid for _, _, pid in later]):
                members += await conn.execute_fetchall(
                    "SELECT lead, product_id, last FROM price_history_products WHERE store = ? AND lead IN "
                    f"(SELECT lead FROM price_history_products WHERE store = ? AND {_WANTED})",
                    (store, store, json.dumps(list(chunk), ensure_ascii=False)),
                )
            updates: list[tuple[str, str, str, str]] = await compute.run(group_updates, store, members)
            await conn.executemany("UPDATE price_history_products SET lead = ?, day = ? WHERE store = ? AND product_id = ?", updates)
        await _clear_stale(conn, store, seen.stale)
        await conn.execute("UPDATE price_history_meta SET prices = MAX(prices, ?) WHERE store = ?", (seen.prices, store))


def later_days(store: str, stored: Iterable[Any], observed: Iterable[Any]) -> list[tuple[str, str, str]]:
    """(dag, winkel, product) voor elk product met een nieuwere laatste dag dan de rij, zoals `prices._with_last`. Puur."""
    last_of: dict[str, str] = {row[0]: row[1] for row in stored}
    later: list[tuple[str, str, str]] = []
    for product_id, day in observed:
        last: str = str(day or "")[:10]
        if product_id in last_of and last > last_of[product_id]:
            later.append((last, store, product_id))
    return later


def group_updates(store: str, members: Iterable[Any]) -> list[tuple[str, str, str, str]]:
    """(lead, dag, winkel, product) voor elk lid van de groepen van `members` (lead, product, laatste dag), uitgerekend zoals
    `rows_of`. Een groep kan in twee stukken van `members` staan: elk lid telt een keer. Puur."""
    groups: dict[str, dict[str, str]] = {}
    for lead, product_id, last in members:
        groups.setdefault(lead, {})[product_id] = last
    updates: list[tuple[str, str, str, str]] = []
    for group in groups.values():
        lead: str = max(group, key=lambda product_id: (group[product_id], product_id))
        day: str = max(group.values())
        updates += [(lead, day, store, product_id) for product_id in group]
    return updates


# --- lezen ------------------------------------------------------------------------------------------------------------


class Choice(NamedTuple):
    """Een keuzelijst (`prices._Choice`): met iets in `shown` alleen die waarden, wat in `hidden` staat valt altijd weg."""

    shown: frozenset[str]
    hidden: frozenset[str]

    def passes(self, value: str) -> bool:
        return value not in self.hidden and (not self.shown or value in self.shown)


def _passes(column: str, choice: Choice) -> tuple[str, list[Any]]:
    """`Choice.passes` als SQL op een kolom, met de parameters. Een lege waarde staat nooit in `shown`."""
    parts: list[str] = ["1"]
    args: list[Any] = []
    if choice.hidden:
        parts.append(f"{column} NOT IN (SELECT value FROM json_each(?))")
        args.append(json.dumps(sorted(choice.hidden), ensure_ascii=False))
    if choice.shown:
        parts.append(f"{column} IN (SELECT value FROM json_each(?))")
        args.append(json.dumps(sorted(choice.shown), ensure_ascii=False))
    return " AND ".join(parts), args


class Pinned(NamedTuple):
    """Het product uit een link (`pin`) in een winkel: het staat bovenaan, onder zijn eigen nummer, met de dag van zijn groep."""

    store: str
    product_id: str
    day: str
    brand: str
    category: str


class Page(NamedTuple):
    """De pagina zonder zoekterm (`page`): het product uit de link per winkel waar het een rij heeft, de rest van de pagina op
    volgorde, hoeveel er na de keuzelijsten over zijn (zonder het product uit de link) en de aantallen per merk en categorie
    (met lege waarden, zonder het product uit de link)."""

    pinned: list[Pinned]
    rest: list[tuple[str, str]]
    total: int
    brands: dict[str, int]
    categories: dict[str, int]


async def page(
    conn: aiosqlite.Connection,
    stores: list[str],
    pinned: str | None,
    keeps: dict[str, set[str] | None],
    by_brand: Choice,
    by_category: Choice,
    limit: int,
    *,
    table: str = "price_history_products",
    shadowed: bool = False,
) -> Page:
    """De pagina van de prijsgeschiedenis zonder zoekterm en zonder `product`, uit `price_history_products`: zoals
    `prices._history_candidates` en `prices._rank_history` het over de hele catalogus doen, maar met de index. Elke groep
    telt een keer onder zijn `lead`. Het product uit de link (`pinned`) neemt de plek van zijn groep over, en komt apart terug.
    Met Eerder gekocht (`keeps`: de gekochte nummers met hun andere nummers, per winkel) alleen de groepen die je kocht."""
    extra: str = " AND NOT EXISTS(SELECT 1 FROM temp._shared_history_rows x WHERE x.store=p.store AND x.product_id=p.product_id)" if shadowed else ""
    found: list[Pinned] = []
    excluded: list[tuple[str, str]] = []  # (winkel, lead) van de groep van het product uit de link
    if pinned is not None:
        for row in await conn.execute_fetchall(
            f"SELECT store, product_id, day, brand, category, lead FROM {table} AS p WHERE store IN ({','.join('?' * len(stores))}) "
            f"AND product_id = ?{extra}",
            (*stores, pinned),
        ):
            found.append(Pinned(row["store"], row["product_id"], row["day"], row["brand"], row["category"]))
            excluded.append((row["store"], row["lead"]))
    if any(keeps.get(name) is not None for name in stores):
        return Page(found, *await _bought_page(conn, stores, keeps, excluded, by_brand, by_category, limit, table=table, shadowed=shadowed))
    where: list[str] = [f"store IN ({','.join('?' * len(stores))})", "product_id = lead"]
    args: list[Any] = list(stores)
    for name, lead in excluded:
        where.append("NOT (store = ? AND product_id = ?)")
        args += [name, lead]
    base: str = " AND ".join(where) + extra
    brand_sql, brand_args = _passes("brand", by_brand)
    category_sql, category_args = _passes("category", by_category)
    brands: dict[str, int] = {
        row[0]: row[1]
        for row in await conn.execute_fetchall(
            f"SELECT brand, COUNT(*) FROM {table} AS p WHERE {base} AND {category_sql} GROUP BY brand", (*args, *category_args)
        )
    }
    categories: dict[str, int] = {
        row[0]: row[1]
        for row in await conn.execute_fetchall(
            f"SELECT category, COUNT(*) FROM {table} AS p WHERE {base} AND {brand_sql} GROUP BY category", (*args, *brand_args)
        )
    }
    total: int = sum(count for brand, count in brands.items() if by_brand.passes(brand))
    rest: list[tuple[str, str]] = [
        (row[0], row[1])
        for row in await conn.execute_fetchall(
            f"SELECT store, product_id FROM {table} AS p WHERE {base} AND {brand_sql} AND {category_sql} "
            "ORDER BY day DESC, store DESC, product_id DESC LIMIT ?",
            (*args, *brand_args, *category_args, limit),
        )
    ]
    return Page(found, rest, total, brands, categories)


async def _bought_page(
    conn: aiosqlite.Connection,
    stores: list[str],
    keeps: dict[str, set[str] | None],
    excluded: list[tuple[str, str]],
    by_brand: Choice,
    by_category: Choice,
    limit: int,
    *,
    table: str = "price_history_products",
    shadowed: bool = False,
) -> tuple[list[tuple[str, str]], int, dict[str, int], dict[str, int]]:
    """`page` met Eerder gekocht: de rijen van wat je kocht (een paar duizend) op hun nummer opgezocht, en de telling en de
    volgorde in `bought_page`. De index over alle producten zou hier langs alle 74.000 rijen van AH lopen."""
    extra: str = " AND NOT EXISTS(SELECT 1 FROM temp._shared_history_rows x WHERE x.store=p.store AND x.product_id=p.product_id)" if shadowed else ""
    rows: list[Any] = []
    for name in stores:
        keep: set[str] | None = keeps.get(name)
        if keep:
            rows += await conn.execute_fetchall(
                f"SELECT p.store, p.product_id, p.day, p.brand, p.category FROM json_each(?) AS k CROSS JOIN {table} AS p "
                f"ON p.store = ? AND p.product_id = k.value WHERE p.product_id = p.lead{extra}",
                (json.dumps(sorted(keep), ensure_ascii=False), name),
            )
    return await compute.run(bought_page, [tuple(row) for row in rows], set(excluded), by_brand, by_category, limit)


def bought_page(
    rows: list[tuple[str, str, str, str, str]], excluded: set[tuple[str, str]], by_brand: Choice, by_category: Choice, limit: int
) -> tuple[list[tuple[str, str]], int, dict[str, int], dict[str, int]]:
    """De pagina, het totaal en de aantallen per merk en categorie van de rijen (winkel, product, dag, merk, categorie) van wat
    je kocht, zonder de groep van het product uit de link (`excluded`), zoals `page` ze met SQL telt. Puur, voor `compute.run`."""
    kept: list[tuple[str, str, str, str, str]] = [row for row in rows if (row[0], row[1]) not in excluded]
    brands: dict[str, int] = {}
    categories: dict[str, int] = {}
    shown: list[tuple[str, str, str]] = []
    for name, product_id, day, brand, category in kept:
        if by_category.passes(category):
            brands[brand] = brands.get(brand, 0) + 1
        if by_brand.passes(brand):
            categories[category] = categories.get(category, 0) + 1
        if by_brand.passes(brand) and by_category.passes(category):
            shown.append((day, name, product_id))
    shown.sort(reverse=True)
    return [(name, product_id) for _, name, product_id in shown[:limit]], len(shown), brands, categories


async def rows_for(conn: aiosqlite.Connection, store: str, words: list[str], ids: list[str], everything: bool) -> list[Any]:
    """De rijen van een winkel die bij een zoekterm kunnen passen, met elk ander nummer van hun groep erbij: elk woord staat in
    `search` (ruimer dan `prices._Query.wants`, dat eist dat de woorden in dezelfde naam staan, en dat daarna precies kiest), plus
    de producten van `ids` (het product uit de link, het ene product van `product`, of wat je kocht). Zonder woorden alleen
    `ids`, met `everything` alles (een zoekterm met alleen een maat)."""
    if everything:
        return list(await conn.execute_fetchall(f"SELECT {_READ_COLUMNS} FROM price_history_products WHERE store = ?", (store,)))
    tests: list[str] = [f"{_WANTED}"]
    args: list[Any] = [json.dumps(ids, ensure_ascii=False)]
    if words:
        tests.append("(" + " AND ".join("search LIKE ?" for _ in words) + ")")
        args += [f"%{word}%" for word in words]
    matched: list[Any] = list(
        await conn.execute_fetchall(f"SELECT {_READ_COLUMNS} FROM price_history_products WHERE store = ? AND ({' OR '.join(tests)})", (store, *args))
    )
    leads: list[str] = sorted({row["lead"] for row in matched if row["twins"] is not None})
    if not leads:
        return matched
    seen: set[str] = {row["product_id"] for row in matched}
    group: list[Any] = await conn.execute_fetchall(
        f"SELECT {_READ_COLUMNS} FROM price_history_products WHERE store = ? AND lead IN (SELECT value FROM json_each(?))",
        (store, json.dumps(leads, ensure_ascii=False)),
    )
    return matched + [row for row in group if row["product_id"] not in seen]


def known_of(rows: Iterable[Any]) -> tuple[dict[str, Known], dict[str, tuple[str, ...]]]:
    """De producten en groepen van `rows_for`, in de vorm van de catalogus (`prices._Known`, `prices._twins`). Puur, voor
    `compute.run`: een zoekterm als "melk" geeft duizenden rijen."""
    products: dict[str, Known] = {}
    twins: dict[str, tuple[str, ...]] = {}
    for row in rows:
        sizes: tuple[tuple[float, str], ...] = tuple((size[0], size[1]) for size in json.loads(row["sizes"]))
        products[row["product_id"]] = (row["last"], tuple(json.loads(row["names"])), sizes, row["brand"], row["category"], "")
        if row["twins"] is not None:
            twins[row["product_id"]] = tuple(json.loads(row["twins"]))
    return products, twins


async def docs(conn: aiosqlite.Connection, store: str, ids: list[str], generation: int) -> dict[str, str]:
    """De documenten van deze producten die bij de generatie horen, als tekst (`decode_docs` leest ze)."""
    if not ids:
        return {}
    rows: Iterable[Any] = await conn.execute_fetchall(
        f"SELECT product_id, doc FROM price_history_docs WHERE store = ? AND generation = ? AND {_WANTED}",
        (store, generation, json.dumps(ids, ensure_ascii=False)),
    )
    return {row[0]: row[1] for row in rows}


def decode_docs(texts: dict[str, str]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    """De bewaarde prijzen (in de vorm van de rijen van `prices.OBSERVATIONS_SQL`, alleen de kolommen die `prices._product` leest)
    en de lijnacties per product. Puur, voor `compute.run`."""
    observed: dict[str, list[dict[str, Any]]] = {}
    lines: dict[str, list[dict[str, Any]]] = {}
    for product_id, text in texts.items():
        doc: dict[str, Any] = json.loads(text)
        if doc["observed"]:
            observed[product_id] = [dict(zip(_OBSERVED_COLUMNS, row, strict=True)) for row in doc["observed"]]
        if doc["lines"]:
            lines[product_id] = doc["lines"]
    return observed, lines


def encode_docs(ids: Iterable[str], observed: dict[str, list[Any]], lines: dict[str, list[dict[str, Any]]]) -> dict[str, str]:
    """Het document van elk product van `ids` uit zijn prijzen (rijen van `prices._observations`) en lijnacties. Puur."""
    return {
        product_id: json.dumps(
            {
                "observed": [[row[column] for column in _OBSERVED_COLUMNS] for row in observed.get(product_id, [])],
                "lines": lines.get(product_id, []),
            },
            ensure_ascii=False,
        )
        for product_id in ids
    }


async def save_docs(conn: aiosqlite.Connection, store: str, texts: dict[str, str], fresh: Fresh) -> None:
    """Bewaart documenten op de verbinding van de aanvraag. Een document is een versnelling: is de database bezet door een
    andere schrijver, dan wacht dit hooguit `DOC_BUSY_SECONDS` en slaat het daarna over. De volgende aanvraag vult het dan."""
    if not texts:
        return
    await conn.execute(f"PRAGMA busy_timeout = {round(DOC_BUSY_SECONDS * 1000)}")
    try:
        await conn.executemany(
            _SAVE_DOC_SQL, [(store, pid, fresh.generation, text, store, pid, store, fresh.generation, fresh.prices) for pid, text in texts.items()]
        )
        await conn.commit()
    except sqlite3.OperationalError as error:
        await conn.rollback()
        if "locked" not in str(error) and "busy" not in str(error):
            raise
    finally:
        await conn.execute(f"PRAGMA busy_timeout = {round(auth.BUSY_SECONDS * 1000)}")
