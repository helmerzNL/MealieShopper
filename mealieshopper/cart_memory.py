"""Wat het weekmenu per gebruiker onthoudt (4.11.0): het product dat je bij een ingrediënt koos, en wat je altijd in huis hebt.

Tot 4.11.0 zocht de controlelijst elke week opnieuw: een product dat je vorige week had vervangen, verving je deze week weer, en
zout en olie vinkte je elke week weer uit. Twee tabellen, allebei per gebruiker (zoals *Liever biologisch* in `profile`):

- `cart_choices`: per winkel en ingrediënt het product dat je in de controlelijst koos in plaats van het voorstel (een andere
  optie uit de lijst, of iets dat je zelf zocht met *Zoek een ander product*). De volgende controlelijst zet dat product
  vooraan (`cart_preview` in `app.py`). Titel, inhoud en foto staan in de rij zelf: daarmee is het product terug te vinden als
  het niet tussen de gewone zoekresultaten zit, en kan de lijst zeggen welk product er niet meer is.
- `pantry_items`: de ingrediënten die je altijd in huis hebt. Het weekmenu zet ze apart en zoekt ze niet.

De sleutel is de gevouwen naam (`key`): kleine letters, zonder accenten, met enkele spaties. Dat is de zoekterm die het weekmenu
stuurt: de naam van het ingrediënt uit Mealie (`food.name`), en bij een ingrediënt dat Mealie niet ontleedde de hele regel.

Beide tabellen zijn werk van de gebruiker en gaan mee in de back-up (`backup.DATA_TABLES`); een gebruiker die wordt verwijderd
neemt zijn rijen mee (`remove_user`).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from . import ah_lists, auth, offers
from .i18n import tr

STORES = offers.SHOP_STORES  # de winkels met een mandje
MAX_NAME_CHARS = 300
MAX_ID_CHARS = 64
MAX_SIZE_CHARS = 100
MAX_IMAGE_CHARS = 2000
MAX_PANTRY_ITEMS = 500  # ruim meer dan een voorraadkast; een rem op een lus die blijft toevoegen

SCHEMA = """
-- Het product dat een gebruiker bij een ingrediënt koos (zie cart_memory.py). `ingredient` is de gevouwen zoekterm.
CREATE TABLE IF NOT EXISTS cart_choices (
  user_id TEXT NOT NULL,
  store TEXT NOT NULL,
  ingredient TEXT NOT NULL,
  product_id TEXT NOT NULL,
  title TEXT NOT NULL,
  unit_size TEXT,
  image TEXT,
  chosen_at TEXT NOT NULL,
  PRIMARY KEY (user_id, store, ingredient)
);
-- Wat een gebruiker altijd in huis heeft. `ingredient` is de gevouwen naam, `name` de naam zoals hij in het weekmenu stond.
CREATE TABLE IF NOT EXISTS pantry_items (
  user_id TEXT NOT NULL,
  ingredient TEXT NOT NULL,
  name TEXT NOT NULL,
  added_at TEXT NOT NULL,
  PRIMARY KEY (user_id, ingredient)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def key(value: Any) -> str:
    """De naam van een ingrediënt om op te vergelijken: kleine letters, zonder accenten en met enkele spaties."""
    return " ".join(ah_lists._fold(value).split())[:MAX_NAME_CHARS]


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _image(value: Any) -> str | None:
    """Alleen een gewoon webadres: de foto komt uit de browser, en een ander soort adres hoort niet in een `<img>`."""
    text = str(value or "").strip()
    return text if text.startswith("https://") and len(text) <= MAX_IMAGE_CHARS and not any(ch.isspace() for ch in text) else None


def _store(value: Any) -> str:
    store = str(value or "").strip().lower()
    if store not in STORES:
        raise ValueError(tr("Onbekende winkel", "Unknown store"))
    return store


@asynccontextmanager
async def _connect() -> AsyncIterator[aiosqlite.Connection]:
    """Een verbinding met de tabellen. Sluit zonder commit, zoals `auth.connect`."""
    await auth.ensure_db()
    async with auth.connect() as conn:
        await auth.ensure_schema(conn, "cart_memory", SCHEMA)
        yield conn


# ---- het product dat je koos


def choice_json(row: Any) -> dict[str, Any]:
    return {
        "productId": row["product_id"],
        "title": row["title"],
        "unitSize": row["unit_size"] or None,
        "image": row["image"] or None,
        "chosenAt": row["chosen_at"],
    }


async def remember(user_id: str, store: Any, items: Iterable[Any], now: str | None = None) -> int:
    """Bewaar de keuzes uit een bevestigde controlelijst: `items` zijn de regels met `query`, `productId` en `title` (en eventueel
    `unitSize` en `image`). Een regel zonder zoekterm, product of titel valt weg; een bestaande keuze voor hetzelfde ingrediënt
    wordt vervangen. Geeft het aantal bewaarde keuzes."""
    key_store = _store(store)
    rows: dict[str, tuple[Any, ...]] = {}
    stamp = now or _now()
    for item in items:
        if not isinstance(item, dict):
            continue
        ingredient = key(item.get("query"))
        product_id = _text(item.get("productId"), MAX_ID_CHARS)
        title = _text(item.get("title"), MAX_NAME_CHARS)
        if not ingredient or not product_id or not title:
            continue
        rows[ingredient] = (
            user_id,
            key_store,
            ingredient,
            product_id,
            title,
            _text(item.get("unitSize"), MAX_SIZE_CHARS) or None,
            _image(item.get("image")),
            stamp,
        )
    if not rows:
        return 0
    async with _connect() as conn:
        await conn.executemany(
            "INSERT INTO cart_choices (user_id, store, ingredient, product_id, title, unit_size, image, chosen_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id, store, ingredient) DO UPDATE SET product_id = excluded.product_id, title = excluded.title, "
            "unit_size = excluded.unit_size, image = excluded.image, chosen_at = excluded.chosen_at",
            list(rows.values()),
        )
        await conn.commit()
    return len(rows)


async def forget(user_id: str, store: Any, query: Any) -> bool:
    """Vergeet de keuze bij een ingrediënt. Geeft of er een was."""
    key_store = _store(store)
    async with _connect() as conn:
        cursor = await conn.execute("DELETE FROM cart_choices WHERE user_id = ? AND store = ? AND ingredient = ?", (user_id, key_store, key(query)))
        await conn.commit()
        return cursor.rowcount > 0


async def chosen(user_id: str, store: Any) -> dict[str, dict[str, Any]]:
    """De keuzes van een gebruiker bij een winkel: gevouwen ingrediënt -> keuze (`choice_json`). Een onbekende winkel heeft er geen."""
    try:
        key_store = _store(store)
    except ValueError:
        return {}
    async with _connect() as conn:
        rows = await conn.execute_fetchall("SELECT * FROM cart_choices WHERE user_id = ? AND store = ?", (user_id, key_store))
    return {row["ingredient"]: choice_json(row) for row in rows}


# ---- wat je altijd in huis hebt


async def pantry(user_id: str) -> list[dict[str, str]]:
    """Wat een gebruiker altijd in huis heeft, op naam."""
    async with _connect() as conn:
        rows = await conn.execute_fetchall("SELECT ingredient, name FROM pantry_items WHERE user_id = ? ORDER BY name COLLATE NOCASE", (user_id,))
    return [{"ingredient": row["ingredient"], "name": row["name"]} for row in rows]


async def set_pantry(user_id: str, name: Any, present: bool, now: str | None = None) -> list[dict[str, str]]:
    """Zet een ingrediënt in de voorraadkast (`present`) of haal het eruit. Geeft de voorraadkast daarna."""
    ingredient = key(name)
    if not ingredient:
        raise ValueError(tr("Geen ingrediënt opgegeven", "No ingredient given"))
    async with _connect() as conn:
        if present:
            count = await auth.fetchone(conn, "SELECT COUNT(*) AS n FROM pantry_items WHERE user_id = ?", (user_id,))
            if count and count["n"] >= MAX_PANTRY_ITEMS:
                raise ValueError(tr("Je voorraadkast is vol; haal eerst iets weg.", "Your pantry is full; remove something first."))
            await conn.execute(
                "INSERT INTO pantry_items (user_id, ingredient, name, added_at) VALUES (?, ?, ?, ?) ON CONFLICT(user_id, ingredient) DO NOTHING",
                (user_id, ingredient, _text(name, MAX_NAME_CHARS), now or _now()),
            )
        else:
            await conn.execute("DELETE FROM pantry_items WHERE user_id = ? AND ingredient = ?", (user_id, ingredient))
        await conn.commit()
    return await pantry(user_id)


async def remove_user(user_id: str) -> None:
    """Een verwijderde gebruiker neemt zijn keuzes en zijn voorraadkast mee."""
    async with _connect() as conn:
        await conn.execute("DELETE FROM cart_choices WHERE user_id = ?", (user_id,))
        await conn.execute("DELETE FROM pantry_items WHERE user_id = ?", (user_id,))
        await conn.commit()
