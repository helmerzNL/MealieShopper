"""Het leesmodel van de aanbiedingsgeschiedenis (`offers.history`): per winkel en product wat de pagina nodig heeft om haar
producten te kiezen en te sorteren, bijgewerkt bij het schrijven.

Waarom: met een prijsarchief heeft `offer_history` ruim honderdduizend rijen, en de pagina koos haar producten met een
window-functie over al die rijen, bij elke aanvraag (in productie rond een seconde). De geschiedenis verandert alleen bij een
synchronisatie, een import of het terugzetten van een back-up. Dus rekent het schrijven het uit, en leest de pagina een index.

Drie tabellen, alle drie afgeleid van `offer_history`. Ze horen daarom niet in een back-up (`backup.EXCLUDED_TABLES`). Het
terugzetten leegt en vult `offer_history`, dus de triggers markeren dan elk product, en `rebuild_now` bouwt alles in die
transactie in een keer opnieuw op:

- `offer_history_products`: een rij per winkel en product. `last_seen` is die van de nieuwste rij (op `first_seen` en id, zoals
  de pagina altijd koos), `times` telt de periodes zonder `continued` (zoals `offers._history_products`), en `title` is de titel
  als het product er maar een heeft. De indexen geven de pagina zonder zoekterm op volgorde, en met een zoekterm de producten
  met een titel.
- `offer_history_titles`: alleen de producten met meer dan een titel (een winkel die de naam wijzigde), met dezelfde waarden per
  titel. Een zoekterm kiest de rijen met een passende titel, en dan tellen alleen die mee: daarom per titel. `newest` is de plaats
  van de nieuwste rij van die titel tussen alle rijen van het product, zodat de nieuwste passende rij te vinden is.
- `offer_history_stale`: de producten waarvoor het leesmodel niet meer klopt. Triggers op `offer_history` zetten ze erin bij
  elke schrijver: de synchronisatie, het prijsarchief, het terugzetten, en ook een oudere versie van de app na een rollback (die
  kent het leesmodel niet, maar de triggers staan in de database en schrijven alleen in deze tabel). `refresh` rekent die
  producten opnieuw uit. De leesroute doet dat voor ze antwoordt, dus het antwoord is nooit verouderd.

Al het rekenwerk is SQL op de verbinding van de aanroeper: aiosqlite voert het uit in zijn eigen thread, en sqlite3 laat de
GIL los tijdens een stap, dus ook een volledige herbouw houdt de event loop niet vast.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import aiosqlite

from . import auth

PRODUCTS: str = "offer_history_products"
TITLES: str = "offer_history_titles"
STALE: str = "offer_history_stale"
TABLES: tuple[str, ...] = (PRODUCTS, TITLES, STALE)
TRIGGERS: tuple[str, ...] = ("offer_history_index_insert", "offer_history_index_update", "offer_history_index_delete")

# Een product markeren zonder ooit een conflict: een INSERT OR IGNORE in een trigger neemt de conflictregel over van de opdracht
# die hem afvuurt (een INSERT OR ROLLBACK zou dan bij een tweede markering alles terugdraaien), deze vorm heeft er geen nodig.
_MARK: str = (
    "INSERT INTO offer_history_stale (store, product_id) SELECT {row}.store, {row}.product_id "
    "WHERE NOT EXISTS (SELECT 1 FROM offer_history_stale WHERE store = {row}.store AND product_id = {row}.product_id);"
)

# `UPDATE OF` noemt de kolommen waar het leesmodel van afhangt. Een periode sluiten (`ended_at`), een prijs repareren
# (`offers._repair_measure_prices`) of een foto omzetten (image_cache.py) markeert niets.
SCHEMA: str = f"""
CREATE TABLE IF NOT EXISTS offer_history_products (
  store TEXT NOT NULL,
  product_id TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  times INTEGER NOT NULL,
  title TEXT,
  PRIMARY KEY (store, product_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS offer_history_products_recent ON offer_history_products (last_seen DESC, times DESC, store, product_id, title);
CREATE INDEX IF NOT EXISTS offer_history_products_store ON offer_history_products (store, last_seen DESC, times DESC, product_id, title);

CREATE TABLE IF NOT EXISTS offer_history_titles (
  store TEXT NOT NULL,
  product_id TEXT NOT NULL,
  title TEXT NOT NULL,
  times INTEGER NOT NULL,
  newest INTEGER NOT NULL,
  last_seen TEXT NOT NULL,
  PRIMARY KEY (store, product_id, title)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS offer_history_stale (
  store TEXT NOT NULL,
  product_id TEXT NOT NULL,
  PRIMARY KEY (store, product_id)
) WITHOUT ROWID;

CREATE TRIGGER IF NOT EXISTS offer_history_index_insert AFTER INSERT ON offer_history
BEGIN
  {_MARK.format(row="NEW")}
END;
CREATE TRIGGER IF NOT EXISTS offer_history_index_update
AFTER UPDATE OF id, store, product_id, title, first_seen, last_seen, continued ON offer_history
BEGIN
  {_MARK.format(row="OLD")}
  {_MARK.format(row="NEW")}
END;
CREATE TRIGGER IF NOT EXISTS offer_history_index_delete AFTER DELETE ON offer_history
BEGIN
  {_MARK.format(row="OLD")}
END;
"""

TRIGGERS_SQL: str = f"SELECT name FROM sqlite_master WHERE type = 'trigger' AND name IN ({', '.join('?' for _ in TRIGGERS)})"

# Per product en titel: de periodes zonder `continued`, en de nieuwste rij. `position` nummert de rijen van een product op
# `first_seen` en id, zoals de pagina de nieuwste koos (`ORDER BY first_seen DESC, id DESC`). `last_seen` naast MAX(position) is
# een kale kolom: SQLite neemt hem uit de rij met dat maximum (https://sqlite.org/lang_select.html#bareagg), en SUM telt daarbij
# niet mee, alleen een tweede min() of max() zou dat breken.
_PER_TITLE: str = """
SELECT store, product_id, title, SUM(CASE WHEN continued THEN 0 ELSE 1 END) AS times, MAX(position) AS newest, last_seen
FROM (
    SELECT h.store, h.product_id, h.title, h.continued, h.last_seen,
           ROW_NUMBER() OVER (PARTITION BY h.store, h.product_id ORDER BY h.first_seen, h.id) AS position
    FROM {source}
)
GROUP BY store, product_id, title
"""
# De rijen van de gemarkeerde producten. CROSS JOIN legt de volgorde vast: SQLite loopt de markeringen af en zoekt per product
# in `offer_history_product`. Met `(store, product_id) IN (...)` koos hij zonder statistieken de index op `store` alleen, en
# las hij voor honderd producten alle rijen van de winkel (230 ms op de volumedatabase).
_STALE_ROWS: str = "offer_history_stale AS s CROSS JOIN offer_history AS h ON h.store = s.store AND h.product_id = s.product_id"
_ALL_ROWS: str = "offer_history AS h"
# Per product uit de titels: de nieuwste titel (weer een kale kolom naast MAX(newest)) geeft `last_seen`, `times` telt alle
# titels op, en `title` blijft alleen staan bij een product met een titel.
_PRODUCTS_FROM: str = (
    """
INSERT INTO offer_history_products (store, product_id, last_seen, times, title)
SELECT store, product_id, last_seen, times, CASE WHEN titles = 1 THEN title END FROM (
    SELECT store, product_id, MAX(newest) AS newest, last_seen, SUM(times) AS times, COUNT(*) AS titles, title
    FROM ("""
    + _PER_TITLE
    + """) GROUP BY store, product_id
)
"""
)
_TITLES_FROM: str = (
    """
INSERT INTO offer_history_titles (store, product_id, title, times, newest, last_seen)
SELECT store, product_id, title, times, newest, last_seen FROM (
    SELECT store, product_id, title, times, newest, last_seen, COUNT(*) OVER (PARTITION BY store, product_id) AS titles
    FROM ("""
    + _PER_TITLE
    + """)
) WHERE titles > 1
"""
)
_STALE_SCOPE: str = "(store, product_id) IN (SELECT store, product_id FROM offer_history_stale)"
# De gemarkeerde producten opnieuw: eerst weg (ook een product zonder rijen meer), dan uit hun rijen in `offer_history`.
REFRESH: tuple[str, ...] = (
    f"DELETE FROM offer_history_products WHERE {_STALE_SCOPE}",
    f"DELETE FROM offer_history_titles WHERE {_STALE_SCOPE}",
    _PRODUCTS_FROM.format(source=_STALE_ROWS),
    _TITLES_FROM.format(source=_STALE_ROWS),
    "DELETE FROM offer_history_stale",
)
# Alles opnieuw: bij de eerste start met het leesmodel en na het terugzetten van een back-up. Niet sneller dan REFRESH met elk
# product gemarkeerd (op de volumedatabase 1,15 tegen 1,2 s, 100 markeringen 4 ms, 3000 markeringen 0,14 s), dus `refresh`
# kiest niet tussen de twee.
REBUILD: tuple[str, ...] = (
    "DELETE FROM offer_history_products",
    "DELETE FROM offer_history_titles",
    _PRODUCTS_FROM.format(source=_ALL_ROWS),
    _TITLES_FROM.format(source=_ALL_ROWS),
    "DELETE FROM offer_history_stale",
)
STALE_SQL: str = "SELECT EXISTS (SELECT 1 FROM offer_history_stale)"


async def prepare(conn: aiosqlite.Connection) -> None:
    """Het schema van het leesmodel, voor `offers._setup` (een keer per proces). Ontbrak een trigger, dan kan het leesmodel
    niet kloppen (de eerste start met het leesmodel, of een database waarin `offer_history` opnieuw is gemaakt): dan alles
    opnieuw. Commit zelf."""
    present: set[str] = {row[0] for row in await conn.execute_fetchall(TRIGGERS_SQL, TRIGGERS)}
    await conn.executescript(SCHEMA)
    if present != set(TRIGGERS):
        for statement in REBUILD:
            await conn.execute(statement)
    await conn.commit()


async def refresh(conn: aiosqlite.Connection) -> bool:
    """Werk de gemarkeerde producten bij, op `conn` en zonder commit: de synchronisatie doet het in haar eigen transactie, de
    leesroute commit zelf. Geeft of er iets te doen was."""
    row: Any = await auth.fetchone(conn, STALE_SQL)
    if not (row and row[0]):
        return False
    for statement in REFRESH:
        await conn.execute(statement)
    return True


def rebuild_now(conn: sqlite3.Connection) -> None:
    """Alles opnieuw op een synchrone verbinding, in de transactie van de aanroeper en zonder commit: voor wie `offer_history`
    in een thread vervangt (het terugzetten van een back-up, het prijsarchief)."""
    for statement in REBUILD:
        conn.execute(statement)


# --- lezen ---------------------------------------------------------------------------------------------------------

# Zonder zoekterm: de producten op volgorde van de index. Bij meer dan een winkel zet de unaire plus de index op `store` buiten
# spel: anders zoekt SQLite per winkel en sorteert alle producten, nu loopt hij `offer_history_products_recent` af tot de LIMIT.
PAGE_SQL: str = """
SELECT store, product_id, last_seen, times FROM offer_history_products WHERE {plus}store IN ({marks})
ORDER BY last_seen DESC, times DESC, store, product_id LIMIT ?
"""
COUNT_SQL: str = "SELECT COUNT(*) FROM offer_history_products WHERE store IN ({marks})"
# Met een zoekterm, de producten met een titel: dezelfde index, met de titel erin, dus zonder de tabel zelf te lezen.
SINGLE_SQL: str = """
SELECT store, product_id, last_seen, times FROM offer_history_products WHERE {plus}store IN ({marks}) AND title LIKE ? ESCAPE '\\'
ORDER BY last_seen DESC, times DESC, store, product_id LIMIT ?
"""
SINGLE_COUNT_SQL: str = "SELECT COUNT(*) FROM offer_history_products WHERE store IN ({marks}) AND title LIKE ? ESCAPE '\\'"
# Met een zoekterm, de producten met meer titels: alleen hun passende titels tellen, zoals de pagina altijd telde. De nieuwste
# passende titel (kale kolommen naast MAX(newest)) geeft `last_seen`. Er zijn er weinig, dus `total` komt uit de window.
SHARED_SQL: str = """
SELECT store, product_id, last_seen, times, COUNT(*) OVER () AS total FROM (
    SELECT store, product_id, MAX(newest) AS newest, last_seen, SUM(times) AS times FROM offer_history_titles
    WHERE store IN ({marks}) AND title LIKE ? ESCAPE '\\' GROUP BY store, product_id
)
ORDER BY last_seen DESC, times DESC, store, product_id LIMIT ?
"""

Key = tuple[str, str]


def _by_page_order(rows: list[Any]) -> list[Any]:
    """Rijen (winkel, product, last_seen, times) op de volgorde van de pagina: `last_seen` en `times` aflopend, dan winkel en
    product oplopend. Python vergelijkt tekst op codepunt en SQLite (BINARY) op de bytes van UTF-8: dat is dezelfde volgorde."""
    ordered: list[Any] = sorted(rows, key=lambda row: (row[0], row[1]))
    ordered.sort(key=lambda row: (row[2], row[3]), reverse=True)  # stabiel, ook met reverse: winkel en product blijven oplopend
    return ordered


async def page(conn: aiosqlite.Connection, stores: list[str], pattern: str | None, limit: int) -> tuple[list[Key], int]:
    """De producten van een pagina van `offers.history` op volgorde, en hoeveel er in totaal passen. `pattern` is een patroon
    voor LIKE (met `\\` als escape), of None zonder zoekterm. Werk eerst de markeringen bij (`refresh`)."""
    marks: str = ",".join("?" * len(stores))
    plus: str = "+" if len(stores) > 1 else ""
    if pattern is None:
        rows: list[Any] = list(await conn.execute_fetchall(PAGE_SQL.format(plus=plus, marks=marks), (*stores, limit)))
        total_row: Any = await auth.fetchone(conn, COUNT_SQL.format(marks=marks), tuple(stores))
        return [(row[0], row[1]) for row in rows], int(total_row[0]) if total_row else 0
    single: list[Any] = list(await conn.execute_fetchall(SINGLE_SQL.format(plus=plus, marks=marks), (*stores, pattern, limit)))
    shared: list[Any] = list(await conn.execute_fetchall(SHARED_SQL.format(marks=marks), (*stores, pattern, limit)))
    single_total: int = len(single)
    if single_total == limit:  # de pagina is vol: dan pas tellen, een zoekterm met weinig treffers kost zo een keer door de index
        counted: Any = await auth.fetchone(conn, SINGLE_COUNT_SQL.format(marks=marks), (*stores, pattern))
        single_total = int(counted[0]) if counted else 0
    total: int = single_total + (int(shared[0]["total"]) if shared else 0)
    merged: list[Any] = _by_page_order([tuple(row)[:4] for row in single] + [tuple(row)[:4] for row in shared])
    return [(row[0], row[1]) for row in merged[:limit]], total
