"""Lokale kopie van de bewaarde AH-recepten, met hun receptgroepen.

De recepten worden bij AH opgehaald en in SQLite bewaard. Daarna komt de lijst uit
de lokale kopie en gaat er geen aanroep naar AH meer uit, tot je zelf vernieuwt.
Een vernieuwing vervangt de kopie in een keer; mislukt het ophalen, dan blijft de
vorige kopie ongemoeid.

De receptgroepen (zoals in de AH-app: je eigen groepen naast de standaardgroep) komen
mee in dezelfde vernieuwing. Lukt het ophalen van de groepen niet, dan worden de
recepten wel bewaard, zonder groepen.
"""
from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from . import ah, auth
from .ah_lists import _fold
from .i18n import tr

PAGE_SIZE = 50
MAX_PAGES = 100  # 5.000 recepten; voorkomt een eindeloze lus bij een afwijkend antwoord

SCHEMA = """
CREATE TABLE IF NOT EXISTS ah_saved_recipes (
  recipe_id TEXT PRIMARY KEY,
  position INTEGER NOT NULL,
  title TEXT NOT NULL,
  image TEXT,
  url TEXT
);

CREATE TABLE IF NOT EXISTS ah_saved_recipes_meta (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  synced_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ah_saved_recipe_groups (
  group_id TEXT PRIMARY KEY,
  position INTEGER NOT NULL,
  name TEXT NOT NULL,
  is_default INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS ah_saved_recipe_group_items (
  group_id TEXT NOT NULL,
  recipe_id TEXT NOT NULL,
  position INTEGER NOT NULL,
  PRIMARY KEY (group_id, recipe_id)
);
"""


def _connect() -> sqlite3.Connection:
    """Een sqlite3-verbinding met het schema, voor `backup.ensure_schemas`. De rest gebruikt `_aconnect`."""
    conn: sqlite3.Connection = auth.connect()
    conn.executescript(SCHEMA)
    return conn


@asynccontextmanager
async def _aconnect() -> AsyncIterator[aiosqlite.Connection]:
    """Async tweeling van `_connect`: hetzelfde schema. Sluit zonder commit, zoals `auth.aconnect`."""
    async with auth.aconnect() as conn:
        await conn.executescript(SCHEMA)
        yield conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def _groups(conn: aiosqlite.Connection, recipe_ids: list[str]) -> list[dict[str, Any]]:
    """De receptgroepen uit de kopie, in de volgorde van AH, elk met zijn recepten in groepsvolgorde.

    Een standaardgroep met precies alle recepten valt weg: die is gelijk aan *Alle recepten*.
    """
    members: dict[str, list[str]] = {}
    for row in await conn.execute_fetchall("SELECT group_id, recipe_id FROM ah_saved_recipe_group_items ORDER BY group_id, position"):
        members.setdefault(row["group_id"], []).append(row["recipe_id"])
    everything = set(recipe_ids)
    groups: list[dict[str, Any]] = []
    for row in await conn.execute_fetchall("SELECT group_id, name, is_default FROM ah_saved_recipe_groups ORDER BY position"):
        ids = [recipe_id for recipe_id in members.get(row["group_id"], []) if recipe_id in everything]
        if row["is_default"] and set(ids) == everything:
            continue
        groups.append({"id": row["group_id"], "name": row["name"], "isDefault": bool(row["is_default"]), "count": len(ids), "recipeIds": ids})
    return groups


async def load() -> dict[str, Any] | None:
    """De lokale kopie, of None als er nog nooit is opgehaald."""
    async with _aconnect() as conn:
        found = await conn.execute_fetchall("SELECT synced_at FROM ah_saved_recipes_meta WHERE id = 1")
        meta = next(iter(found), None)
        if not meta:
            return None
        rows = await conn.execute_fetchall(
            "SELECT recipe_id, title, image, url FROM ah_saved_recipes ORDER BY position"
        )
        groups = await _groups(conn, [row["recipe_id"] for row in rows])
    recipes = [
        {"id": row["recipe_id"], "title": row["title"], "image": row["image"] or "", "url": row["url"] or ""}
        for row in rows
    ]
    return {"recipes": recipes, "total": len(recipes), "groups": groups, "syncedAt": meta["synced_at"], "source": "local"}


async def _fetch_all() -> list[dict[str, Any]]:
    recipes: list[dict[str, Any]] = []
    seen: set[str] = set()
    total = None
    for page in range(MAX_PAGES):
        data = await ah.get_saved_recipes(page, PAGE_SIZE)
        batch = data.get("recipes") or []
        total = int(data.get("total") or 0)
        fresh = 0
        for recipe in batch:
            key = str(recipe.get("id") or recipe.get("url") or "")
            if not key or key in seen:
                continue
            seen.add(key)
            recipes.append(recipe)
            fresh += 1
        if not fresh or len(recipes) >= total:
            break
    return recipes


async def _fetch_groups() -> list[dict[str, Any]]:
    """De receptgroepen bij AH; mislukt dat, dan geen groepen (de recepten zelf gaan voor)."""
    try:
        groups = await ah.get_saved_recipe_groups()
    except Exception:  # noqa: BLE001 - AH is onofficieel; zonder groepen werkt de lijst nog steeds
        return []
    return [group for group in groups or [] if isinstance(group, dict) and group.get("id") is not None]


async def refresh() -> dict[str, Any]:
    """Haal alle bewaarde recepten en hun receptgroepen bij AH op en vervang de lokale kopie."""
    recipes = await _fetch_all()
    groups = await _fetch_groups()
    rows = [
        (
            str(recipe.get("id") or recipe.get("url")),
            position,
            str(recipe.get("title") or "Onbekend recept"),
            recipe.get("image") or "",
            recipe.get("url") or "",
        )
        for position, recipe in enumerate(recipes)
    ]
    known = {row[0] for row in rows}
    group_rows: list[tuple[Any, ...]] = []
    member_rows: list[tuple[Any, ...]] = []
    for position, group in enumerate(groups):
        group_id = str(group["id"])
        group_rows.append((group_id, position, str(group.get("name") or group_id), 1 if group.get("isDefault") else 0))
        seen: set[str] = set()
        for recipe_id in group.get("recipeIds") or []:
            recipe_id = str(recipe_id)
            if recipe_id in known and recipe_id not in seen:
                seen.add(recipe_id)
                member_rows.append((group_id, recipe_id, len(seen) - 1))
    async with _aconnect() as conn:
        await conn.execute("DELETE FROM ah_saved_recipes")
        await conn.execute("DELETE FROM ah_saved_recipe_group_items")
        await conn.execute("DELETE FROM ah_saved_recipe_groups")
        await conn.executemany(
            "INSERT INTO ah_saved_recipes (recipe_id, position, title, image, url) VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        await conn.executemany(
            "INSERT OR IGNORE INTO ah_saved_recipe_groups (group_id, position, name, is_default) VALUES (?, ?, ?, ?)",
            group_rows,
        )
        await conn.executemany(
            "INSERT OR IGNORE INTO ah_saved_recipe_group_items (group_id, recipe_id, position) VALUES (?, ?, ?)",
            member_rows,
        )
        await conn.execute(
            """
            INSERT INTO ah_saved_recipes_meta (id, synced_at) VALUES (1, ?)
            ON CONFLICT(id) DO UPDATE SET synced_at = excluded.synced_at
            """,
            (_now(),),
        )
        await conn.commit()
    return await load() or {"recipes": [], "total": 0, "groups": [], "syncedAt": None, "source": "local"}


EMPTY = {"recipes": [], "total": 0, "groups": [], "syncedAt": None, "source": "none"}


async def get(force: bool = False, fetch_if_missing: bool = True) -> dict[str, Any]:
    """Uit de lokale kopie; bij `force` of zonder kopie eerst bij AH ophalen.

    Met `fetch_if_missing=False` gaat er nooit een aanroep naar AH uit: zonder kopie komt
    een lege uitkomst (`source: "none"`) terug. Mislukt een vernieuwing terwijl er een
    kopie is, dan komt die terug met `ahError`.
    """
    cached = await load()
    if cached and not force:
        return cached
    if not cached and not force and not fetch_if_missing:
        return {**EMPTY, "groups": []}
    try:
        return await refresh()
    except Exception as exc:  # noqa: BLE001 - AH is onofficieel; val terug op de kopie
        if cached:
            return {**cached, "ahError": str(exc) or "Vernieuwen mislukt"}
        raise


def recipe_number(recipe: dict[str, Any]) -> int | None:
    """Het Allerhande-receptnummer van een bewaard recept: het id als dat een getal is, anders uit de link.

    Zoals `ahRecipeNumber` in static/app.js; bij het ophalen kan het id een link zijn (`str(id or url)`).
    """
    key = str(recipe.get("id") or "").strip()
    if key.isdigit() and len(key) <= 12:
        return int(key)
    return ah.allerhande_recipe_id(str(recipe.get("url") or "")) or ah.allerhande_recipe_id(key)


def group_by_id(groups: list[dict[str, Any]], group_id: Any) -> dict[str, Any] | None:
    """Een receptgroep op precies dit id (voor een aanvraag uit de app; `resolve_group` zoekt ook op naam)."""
    wanted = str(group_id or "").strip()
    return next((group for group in groups if wanted and group["id"] == wanted), None)


def resolve_group(groups: list[dict[str, Any]], wanted: Any) -> dict[str, Any] | None:
    """Een receptgroep op id of (deel van de) naam; zonder opgave None (= alle recepten)."""
    text = " ".join(str(wanted or "").split())
    if not text:
        return None
    exact = [group for group in groups if group["id"] == text]
    if exact:
        return exact[0]
    words = _fold(text).split()
    found = [group for group in groups if all(word in _fold(group["name"]) for word in words)]
    if len(found) == 1:
        return found[0]
    same = [group for group in found if _fold(group["name"]) == _fold(text)]
    if len(same) == 1:
        return same[0]
    if not groups:
        raise ValueError(tr("Er zijn geen receptgroepen bij AH", "There are no recipe groups at AH"))
    names = ", ".join(f"'{group['name']}'" for group in (found or groups))
    raise ValueError(
        tr(f"Welke groep bedoel je? Kies uit: {names}", f"Which group do you mean? Choose from: {names}")
        if found
        else tr(f"Geen receptgroep met die naam. Kies uit: {names}", f"No recipe group with that name. Choose from: {names}")
    )
