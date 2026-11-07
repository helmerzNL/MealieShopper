"""Receptafbeeldingen uit Mealie, via MealieShopper geserveerd en lokaal bewaard.

Mealie staat vaak op een adres dat de browser niet kan bereiken (of achter een
token). De server haalt de afbeelding dus op met de API-token en bewaart hem in
SQLite; daarna is Mealie niet meer nodig om het weekmenu met plaatjes te tonen.
"""
from __future__ import annotations

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import aiosqlite
import httpx

from . import auth, http_client, mealie

SIZES = ("min-original", "tiny-original", "original")
DEFAULT_SIZE = "min-original"
MAX_BYTES = 8 * 1024 * 1024
TIMEOUT = 20

_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS mealie_images (
  recipe_id TEXT NOT NULL,
  size TEXT NOT NULL,
  version TEXT NOT NULL DEFAULT '',
  content_type TEXT NOT NULL,
  data BLOB NOT NULL,
  fetched_at TEXT NOT NULL,
  PRIMARY KEY (recipe_id, size)
);
"""


class ImageNotFound(Exception):
    """Mealie heeft geen afbeelding voor dit recept."""


def valid_recipe_id(value: Any) -> bool:
    return bool(_UUID_RE.match(str(value or "")))


def normalize_size(value: Any) -> str:
    return value if value in SIZES else DEFAULT_SIZE


def image_path(recipe: dict[str, Any] | None) -> str | None:
    """Het adres (binnen MealieShopper) waar de afbeelding van een Mealie-recept te vinden is."""
    recipe = recipe or {}
    recipe_id = recipe.get("id")
    image = recipe.get("image")
    if not valid_recipe_id(recipe_id) or not image or not isinstance(image, str):
        return None
    return f"/api/mealie/recipes/{recipe_id}/image?v={image}"


@asynccontextmanager
async def _connect() -> AsyncIterator[aiosqlite.Connection]:
    """Een verbinding met de tabel van de afbeeldingen. Sluit zonder commit, zoals `auth.connect`."""
    async with auth.connect() as conn:
        await conn.executescript(SCHEMA)
        yield conn


async def _cached(recipe_id: str, size: str) -> tuple[str, str, bytes] | None:
    async with _connect() as conn:
        async with conn.execute(
            "SELECT version, content_type, data FROM mealie_images WHERE recipe_id = ? AND size = ?",
            (recipe_id, size),
        ) as cursor:
            row = await cursor.fetchone()
    return (row["version"], row["content_type"], bytes(row["data"])) if row else None


async def _store(recipe_id: str, size: str, version: str, content_type: str, data: bytes) -> None:
    async with _connect() as conn:
        await conn.execute(
            """
            INSERT INTO mealie_images (recipe_id, size, version, content_type, data, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(recipe_id, size) DO UPDATE SET
              version = excluded.version, content_type = excluded.content_type,
              data = excluded.data, fetched_at = excluded.fetched_at
            """,
            (recipe_id, size, version, content_type, data, datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )
        await conn.commit()


async def _fetch(recipe_id: str, size: str) -> tuple[str, bytes]:
    await mealie.ensure_configured()
    # Geen doorverwijzingen volgen (zoals voorheen met requests): het token gaat niet mee naar een ander adres.
    client: httpx.AsyncClient = http_client.client("mealie-images", timeout=TIMEOUT, follow_redirects=False)
    response: httpx.Response = await client.get(
        f"{await mealie.mealie_url()}/api/media/recipes/{recipe_id}/images/{size}.webp",
        headers={"Authorization": f"Bearer {await mealie.mealie_token()}", "Accept": "image/*"},
    )
    if response.status_code == 404:
        raise ImageNotFound(recipe_id)
    if response.status_code >= 400:
        raise RuntimeError(f"Afbeelding ophalen mislukt ({response.status_code})")
    content_type: str = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
    if not content_type.startswith("image/") or content_type == "image/svg+xml":
        raise RuntimeError("Mealie gaf geen afbeelding terug")
    if len(response.content) > MAX_BYTES:
        raise RuntimeError("Afbeelding is te groot")
    return content_type, response.content


async def get_image(recipe_id: str, size: str = DEFAULT_SIZE, version: str = "") -> tuple[str, bytes]:
    """(content_type, bytes) uit de lokale kopie, of ververst vanuit Mealie als die ontbreekt of verouderd is."""
    recipe_id = str(recipe_id)
    size = normalize_size(size)
    version = str(version or "")
    cached = await _cached(recipe_id, size)
    if cached and (not version or cached[0] == version):
        return cached[1], cached[2]
    try:
        content_type, data = await _fetch(recipe_id, size)
    except ImageNotFound:
        raise
    except Exception:
        if cached:  # Mealie onbereikbaar: een oud plaatje is beter dan geen
            return cached[1], cached[2]
        raise
    await _store(recipe_id, size, version, content_type, data)
    return content_type, data
