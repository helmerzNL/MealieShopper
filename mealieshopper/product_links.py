"""Je eigen koppelingen (#238): een regel op een kassabon of in een ingelezen bestelling die MealieShopper niet (of verkeerd) aan een
product van de winkel koppelde, koppel je zelf. Daarna hoort elke regel met hetzelfde artikelnummer of dezelfde naam bij dat product:
op elke andere en elke volgende bon, in de ingelezen bestellingen en in de Prijsgeschiedenis.

- `kind = 'article'`: het artikelnummer op een AH-kassabon (`ah_receipt_items.product_id`, AH's `hqId`). Alleen bij AH, en alleen
  als de regel er een heeft (niet leeg en niet 0).
- `kind = 'name'`: de gevouwen naam (`fold`: kleine letters, zonder accenten en dubbele spaties). Voor een bonregel zonder
  artikelnummer en voor een regel uit een bevestigingsmail (`imported_purchases.title`), bij elke winkel.

Een eigen koppeling wint altijd van de automatische: eerst op artikelnummer, dan op naam, dan `ah_receipt_products` en de koppeling
op naam in `prices`. Ze staat bewust apart van `ah_receipt_products`: die tabel is opnieuw op te zoeken en zit dus niet in de
back-up, en het automatisch koppelen overschrijft er rijen in. Een eigen koppeling is werk van de gebruiker en gaat wel mee in de
back-up (`backup.DATA_TABLES`).

Titel, merk, inhoud en foto van het gekozen product staan in de rij zelf, zodat een regel ze ook heeft als het product nergens
anders in de app voorkomt.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, NamedTuple

import aiosqlite

from . import ah_lists, auth, offers
from .i18n import tr

STORES = offers.SHOP_STORES  # koppelen zoekt bij de winkel: Lidl en Aldi hebben geen webwinkel
KINDS = ("article", "name")
MAX_NAME_CHARS = 300
MAX_ID_CHARS = 64
MAX_SIZE_CHARS = 100
MAX_IMAGE_CHARS = 2000

SCHEMA = """
-- Je eigen koppelingen (zie product_links.py): een artikelnummer op een AH-bon (`kind` 'article') of een gevouwen naam ('name')
-- -> een product van de winkel. `source_name` is de naam zoals hij op de bon of in de mail stond, om in het overzicht te tonen.
CREATE TABLE IF NOT EXISTS product_links (
  store TEXT NOT NULL,
  kind TEXT NOT NULL,
  key TEXT NOT NULL,
  product_id TEXT NOT NULL,
  title TEXT NOT NULL,
  brand TEXT,
  unit_size TEXT,
  image TEXT,
  source_name TEXT,
  created_at TEXT NOT NULL,
  PRIMARY KEY (store, kind, key)
);
"""


class Links(NamedTuple):
    """De eigen koppelingen van een winkel: artikelnummer -> koppeling en gevouwen naam -> koppeling (zie `link_json`)."""

    articles: dict[str, dict[str, Any]]
    names: dict[str, dict[str, Any]]

    def find(self, article: Any, name: Any) -> dict[str, Any] | None:
        """De koppeling van een regel: eerst op artikelnummer, dan op naam, of None."""
        key = article_key(article)
        if key and key in self.articles:
            return self.articles[key]
        return self.names.get(fold(name)) if name else None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fold(value: Any) -> str:
    """Een naam om op te vergelijken: kleine letters, zonder accenten en met enkele spaties. "AH Ah zachte spinazie" in een
    bevestigingsmail heeft het merk twee keer; dat telt als een keer. Dezelfde vouw als de koppeling op naam in `prices`."""
    words = ah_lists._fold(value).split()
    if len(words) > 1 and words[0] == words[1]:
        words = words[1:]
    return " ".join(words)


def article_key(value: Any) -> str | None:
    """Het artikelnummer van een bonregel als tekst, of None als de regel er geen bruikbaar heeft (leeg, 0 of geen getal)."""
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        number = int(str(value).strip())
    except ValueError:
        return None
    return str(number) if number > 0 else None


# Het product van een bonregel `l` (`ah_receipt_items`): je eigen koppeling op artikelnummer (`k1`), anders je eigen koppeling op
# naam (`k2`), anders de automatische (`p`, `ah_receipt_products`). `ms_fold` is `fold` (zie `ensure`). Voor `ah_orders` (de bon),
# `prices` (de gewone prijs per regel) en `purchase_lines` (hoe vaak je iets eerder kocht), zodat ze alle drie hetzelfde product zien.
RECEIPT_LINE_JOIN_SQL = """
    LEFT JOIN product_links k1 ON k1.store = 'ah' AND k1.kind = 'article' AND k1.key = CAST(l.product_id AS TEXT)
    LEFT JOIN product_links k2 ON k1.key IS NULL AND k2.store = 'ah' AND k2.kind = 'name' AND k2.key = ms_fold(l.name)
    LEFT JOIN ah_receipt_products p ON p.receipt_product_id = l.product_id
"""
RECEIPT_LINE_PRODUCT_SQL = "CASE WHEN k1.key IS NOT NULL THEN k1.product_id WHEN k2.key IS NOT NULL THEN k2.product_id ELSE p.product_id END"
RECEIPT_LINE_TITLE_SQL = "CASE WHEN k1.key IS NOT NULL THEN k1.title WHEN k2.key IS NOT NULL THEN k2.title ELSE p.title END"
RECEIPT_LINE_IMAGE_SQL = "CASE WHEN k1.key IS NOT NULL THEN k1.image WHEN k2.key IS NOT NULL THEN k2.image ELSE p.image END"


async def ensure(conn: aiosqlite.Connection) -> aiosqlite.Connection:
    """Maak de tabel als die er nog niet is, en geef SQL de functie `ms_fold` (`fold`), zodat een query op naam kan koppelen.

    Voor de `_connect` van elke module die bonregels koppelt (en `purchase_lines.times_before`)."""
    await conn.executescript(SCHEMA)
    await conn.create_function("ms_fold", 1, fold, deterministic=True)
    return conn


@asynccontextmanager
async def _connect() -> AsyncIterator[aiosqlite.Connection]:
    """Een verbinding met de tabel. Sluit zonder commit, zoals `auth.connect`."""
    await auth.ensure_db()
    async with auth.connect() as conn:
        yield await ensure(conn)


def link_json(row: Any) -> dict[str, Any]:
    return {
        "store": row["store"], "kind": row["kind"], "key": row["key"], "productId": row["product_id"], "title": row["title"],
        "brand": row["brand"] or None, "unitSize": row["unit_size"] or None, "image": row["image"] or None,
        "name": row["source_name"] or row["key"], "createdAt": row["created_at"],
    }


_LOAD_SQL: str = "SELECT * FROM product_links WHERE store = ?"


def _links(rows: Iterable[Any]) -> Links:
    links = Links({}, {})
    for row in rows:
        (links.articles if row["kind"] == "article" else links.names)[row["key"]] = link_json(row)
    return links


async def load(conn: aiosqlite.Connection, store: str) -> Links:
    """De eigen koppelingen van een winkel, uit een open verbinding (die `ensure` al zag)."""
    return _links(await conn.execute_fetchall(_LOAD_SQL, (store,)))


def _store(value: Any) -> str:
    store = str(value or "").strip().lower()
    if store not in STORES:
        raise ValueError(tr("Onbekende winkel", "Unknown store"))
    return store


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _image(value: Any) -> str | None:
    """Alleen een gewoon webadres: de foto komt uit de browser, en een ander soort adres hoort niet in een `<img>`."""
    text = str(value or "").strip()
    return text if text.startswith("https://") and len(text) <= MAX_IMAGE_CHARS and not any(ch.isspace() for ch in text) else None


async def save(store: Any, name: Any, product: Any, article: Any = None, now: str | None = None) -> dict[str, Any]:
    """Koppel een regel (de naam op de bon of in de mail, en bij een AH-bon het artikelnummer) aan een product van de winkel.

    Met een bruikbaar artikelnummer (alleen bij AH) wordt het een koppeling op artikelnummer, anders een op naam. Een bestaande
    koppeling van dezelfde regel wordt vervangen. `product` is een product uit de zoeker: `productId`, `title` en eventueel `brand`,
    `unitSize` en `image`. Geeft de koppeling (`link_json`); een fout in de invoer geeft een ValueError met een leesbare melding."""
    key_store = _store(store)
    product = product if isinstance(product, dict) else {}
    product_id = _text(product.get("productId") or product.get("id"), MAX_ID_CHARS)
    title = _text(product.get("title"), MAX_NAME_CHARS)
    source_name = _text(name, MAX_NAME_CHARS)
    if not product_id or not title:
        raise ValueError(tr("Kies een product om te koppelen", "Choose a product to link"))
    article_number = article_key(article) if key_store == "ah" else None
    kind, key = ("article", article_number) if article_number else ("name", fold(source_name))
    if not key:
        raise ValueError(tr("De regel heeft geen naam om op te koppelen", "The line has no name to link by"))
    values = (key_store, kind, key, product_id, title, _text(product.get("brand"), MAX_NAME_CHARS) or None,
              _text(product.get("unitSize"), MAX_SIZE_CHARS) or None, _image(product.get("image")), source_name or None, now or _now())
    async with _connect() as conn:
        await conn.execute(
            """INSERT INTO product_links (store, kind, key, product_id, title, brand, unit_size, image, source_name, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (store, kind, key) DO UPDATE SET product_id = excluded.product_id, title = excluded.title,
                 brand = excluded.brand, unit_size = excluded.unit_size, image = excluded.image, source_name = excluded.source_name,
                 created_at = excluded.created_at""",
            values,
        )
        async with conn.execute("SELECT * FROM product_links WHERE store = ? AND kind = ? AND key = ?", values[:3]) as cursor:
            row: aiosqlite.Row | None = await cursor.fetchone()
        await conn.commit()
    return link_json(row)


async def remove(store: Any, kind: Any, key: Any) -> bool:
    """Haal een eigen koppeling weg; daarna geldt voor die regel weer de automatische. Geeft of er een was."""
    key_store = _store(store)
    if kind not in KINDS:
        raise ValueError(tr("Onbekend soort koppeling", "Unknown kind of link"))
    async with _connect() as conn:
        cursor: aiosqlite.Cursor = await conn.execute(
            "DELETE FROM product_links WHERE store = ? AND kind = ? AND key = ?", (key_store, kind, str(key or ""))
        )
        await conn.commit()
    return cursor.rowcount > 0


async def list_links(store: Any = None) -> dict[str, Any]:
    """Alle eigen koppelingen (of die van een winkel), de nieuwste eerst."""
    where, args = ("WHERE store = ?", (_store(store),)) if store not in (None, "") else ("", ())
    async with _connect() as conn:
        rows = await conn.execute_fetchall(f"SELECT * FROM product_links {where} ORDER BY created_at DESC, store, kind, key", args)
    return {"links": [link_json(row) for row in rows]}
