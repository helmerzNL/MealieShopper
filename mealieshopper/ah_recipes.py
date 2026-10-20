"""Lokale kopie van de bewaarde AH-recepten.

De recepten worden bij AH opgehaald en in SQLite bewaard. Daarna komt de lijst uit
de lokale kopie en gaat er geen aanroep naar AH meer uit, tot je zelf vernieuwt.
Een vernieuwing vervangt de kopie in een keer; mislukt het ophalen, dan blijft de
vorige kopie ongemoeid.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from typing import Any

from . import ah, auth

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
"""


def _connect():
    conn = auth.connect()
    conn.executescript(SCHEMA)
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load() -> dict[str, Any] | None:
    """De lokale kopie, of None als er nog nooit is opgehaald."""
    with closing(_connect()) as conn:
        meta = conn.execute("SELECT synced_at FROM ah_saved_recipes_meta WHERE id = 1").fetchone()
        if not meta:
            return None
        rows = conn.execute(
            "SELECT recipe_id, title, image, url FROM ah_saved_recipes ORDER BY position"
        ).fetchall()
    recipes = [
        {"id": row["recipe_id"], "title": row["title"], "image": row["image"] or "", "url": row["url"] or ""}
        for row in rows
    ]
    return {"recipes": recipes, "total": len(recipes), "syncedAt": meta["synced_at"], "source": "local"}


def _fetch_all() -> list[dict[str, Any]]:
    recipes: list[dict[str, Any]] = []
    seen: set[str] = set()
    total = None
    for page in range(MAX_PAGES):
        data = ah.get_saved_recipes(page, PAGE_SIZE)
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


def refresh() -> dict[str, Any]:
    """Haal alle bewaarde recepten bij AH op en vervang de lokale kopie."""
    recipes = _fetch_all()
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
    with closing(_connect()) as conn, conn:
        conn.execute("DELETE FROM ah_saved_recipes")
        conn.executemany(
            "INSERT INTO ah_saved_recipes (recipe_id, position, title, image, url) VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        conn.execute(
            """
            INSERT INTO ah_saved_recipes_meta (id, synced_at) VALUES (1, ?)
            ON CONFLICT(id) DO UPDATE SET synced_at = excluded.synced_at
            """,
            (_now(),),
        )
    return load() or {"recipes": [], "total": 0, "syncedAt": None, "source": "local"}


EMPTY = {"recipes": [], "total": 0, "syncedAt": None, "source": "none"}


def get(force: bool = False, fetch_if_missing: bool = True) -> dict[str, Any]:
    """Uit de lokale kopie; bij `force` of zonder kopie eerst bij AH ophalen.

    Met `fetch_if_missing=False` gaat er nooit een aanroep naar AH uit: zonder kopie komt
    een lege uitkomst (`source: "none"`) terug. Mislukt een vernieuwing terwijl er een
    kopie is, dan komt die terug met `ahError`.
    """
    cached = load()
    if cached and not force:
        return cached
    if not cached and not force and not fetch_if_missing:
        return dict(EMPTY)
    try:
        return refresh()
    except Exception as exc:  # noqa: BLE001 - AH is onofficieel; val terug op de kopie
        if cached:
            return {**cached, "ahError": str(exc) or "Vernieuwen mislukt"}
        raise
