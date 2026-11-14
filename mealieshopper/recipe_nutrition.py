"""De calorieën van je recepten in Mealie, om op te zoeken (issue #283), en het aanvullen van oude AH-recepten.

Mealie geeft in zijn lijst van recepten (`/api/recipes`) geen voedingswaarden, en bewaart ze als tekst: op "tussen 400 en
600 kcal" kan Mealie zelf niet zoeken. Daarom houden we per recept de kcal per portie bij, met de kaart voor de lijst:

- elk recept dat MealieShopper uit Mealie ophaalt (het weekmenu, een recept openen, de receptindex) komt meteen in de cache
  (`remember`, vanuit `mealie.get_recipe`);
- zoek je op calorieën en is de cache ouder dan `INDEX_TTL`, dan leest `refresh` de lijst van Mealie en haalt alleen de
  recepten op die nieuw zijn of een andere `updatedAt` hebben.

Sinds 4.20.0 (issue #322) staan bij elk recept ook de tags, de categorieën, de bereidingstijd, de datum waarop het in Mealie
kwam en de waardering. Daarop filtert en sorteert Recepten zoeken (`search` met `Filters`, en `facets` voor de lijsten met
tags en categorieën). Op de bereidingstijd kan Mealie zelf niet filteren. Die gegevens staan in de lijst van Mealie, dus elke
ronde werkt ze voor alle recepten bij, zonder elk recept op te halen. De dagelijkse run (`offers_schedule.run_all`) start
een ronde met `refresh_nightly`.

Sinds 4.22.0 leidt MealieShopper uit de ingrediënten af of een recept vegetarisch of veganistisch is, of vlees, vis of
gevogelte bevat (recipe_diet.py, kolom `derived`), en filtert Recepten zoeken op menugang, dieet en vlees/vis/gevogelte. Na
elke ronde zet `start_tags` die tags ook in Mealie (de instelling `SETTING_DIET_TAGS`, standaard aan). Een tag die je zelf
zette blijft altijd staan. Een tag die MealieShopper zette en die niet meer klopt, haalt hij weg; haal jij er een weg, dan zet
MealieShopper hem bij dat recept niet opnieuw (kolom `auto_tags`).

De cache is opnieuw op te bouwen en gaat niet mee in de back-up (zie `backup.EXCLUDED_TABLES`). Hoort hij bij een ander
Mealie-adres, dan begint hij opnieuw.

Recepten die je vroeger uit Allerhande importeerde, hebben geen voedingswaarden: die vroeg MealieShopper toen niet aan AH.
`start_backfill` (alleen de beheerder) haalt ze voor die recepten bij AH op en zet ze in Mealie.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from . import ah, audit, auth, mealie, nutrition, offer_courses, recipe_diet, tasks
from .i18n import tr

log = logging.getLogger(__name__)

INDEX_TTL = 600  # seconden: zo lang geldt de cache als bij, daarna leest een zoekopdracht op calorieën de lijst opnieuw
RETRY_AFTER = 60  # seconden na een mislukte ronde (Mealie onbereikbaar) voordat een zoekopdracht het opnieuw probeert
STALE_WITHOUT_STAMP = 24 * 3600  # een recept zonder `updatedAt` halen we na een dag opnieuw op
MAX_RECIPES = 2000  # zoveel recepten lezen we hooguit in
SEARCH_WAIT = 15.0  # seconden die een zoekopdracht op de eerste ronde wacht; daarna komt wat er al is, met de voortgang
FACETS_WAIT = 5.0  # seconden die de lijsten met tags en categorieën op de eerste ronde wachten
MAX_FILTER_VALUES = 50  # zoveel tags of categorieën per kant (erbij, eruit) in één zoekopdracht
MAX_MINUTES = 24 * 60  # de hoogste grens op de bereidingstijd
SORTS = ("", "name", "newest", "rating")  # "" is de standaard: de beste treffer bij een zoekterm, anders op naam
SETTING_DIET_TAGS = "MEALIE_DIET_TAGS"  # "0" = geen tags in Mealie zetten; leeg of "1" = wel (standaard)
MAX_TAG_WRITES = 300  # zoveel recepten krijgen hooguit nieuwe tags per ronde; de rest volgt de volgende ronde

SCHEMA = """
CREATE TABLE IF NOT EXISTS mealie_recipe_nutrition (
  recipe_id TEXT PRIMARY KEY,
  slug TEXT NOT NULL,
  name TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT '',
  kcal REAL,
  card TEXT NOT NULL,
  fetched_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS mealie_recipe_nutrition_slug ON mealie_recipe_nutrition (slug);
CREATE TABLE IF NOT EXISTS mealie_recipe_nutrition_meta (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  base TEXT NOT NULL,
  synced_at REAL NOT NULL
);
"""

# Kolommen die later kwamen, zoals `offers.COLUMN_MIGRATIONS`: (tabel, kolom, soort).
# - `nutrition` (4.7.0): alle voedingswaarden als JSON (`{veld: getal}`, zie nutrition.values). Een rij van daarvoor heeft NULL
#   en wordt bij de volgende ronde opnieuw opgehaald.
# - `tags` en `categories` (4.20.0): JSON, `[{"slug": .., "name": ..}]`; `minutes` (de bereidingstijd), `created_at` (wanneer het
#   recept in Mealie kwam) en `rating`. Een rij van daarvoor heeft NULL in `tags`; de volgende ronde vult ze uit de lijst van Mealie.
COLUMN_MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    ("mealie_recipe_nutrition", "nutrition", "TEXT"),
    ("mealie_recipe_nutrition", "tags", "TEXT"),
    ("mealie_recipe_nutrition", "categories", "TEXT"),
    ("mealie_recipe_nutrition", "minutes", "INTEGER"),
    ("mealie_recipe_nutrition", "created_at", "TEXT"),
    ("mealie_recipe_nutrition", "rating", "REAL"),
    # 4.22.0: wat de ingrediënten zeggen (recipe_diet.classify, JSON; "null" zonder ingrediënten, NULL = nog niet bekeken) en
    # welke tags MealieShopper zelf in Mealie zette (`{"added": [...], "declined": [...]}`).
    ("mealie_recipe_nutrition", "derived", "TEXT"),
    ("mealie_recipe_nutrition", "auto_tags", "TEXT"),
)

_refresh_task: asyncio.Task[Any] | None = None
_tags_task: asyncio.Task[Any] | None = None
_refresh: dict[str, Any] = {"done": 0, "total": 0, "error": "", "failedAt": 0.0}


@asynccontextmanager
async def _connect() -> AsyncIterator[aiosqlite.Connection]:
    """Een verbinding met de tabellen van de cache. Sluit zonder commit, zoals `auth.connect`."""
    async with auth.connect() as conn:
        await auth.ensure_schema(conn, "recipe_nutrition", _setup)
        yield conn


async def _setup(conn: aiosqlite.Connection) -> None:
    """Het schema en de kolommen die later kwamen, een keer per proces (`auth.ensure_schema`)."""
    await conn.executescript(SCHEMA)
    for table, column, kind in COLUMN_MIGRATIONS:
        if column not in {row["name"] for row in await conn.execute_fetchall(f"PRAGMA table_info({table})")}:
            await conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
    await conn.commit()


def _values(row: Any) -> dict[str, float]:
    """De voedingswaarden van een rij; een rij van vóór 4.7.0 (`nutrition` NULL) heeft alleen de kcal."""
    raw = row["nutrition"]
    if raw is None:
        return {"calories": row["kcal"]} if row["kcal"] is not None else {}
    try:
        found = json.loads(raw)
    except ValueError:
        return {}
    return (
        {key: float(amount) for key, amount in found.items() if key in nutrition.UNITS and isinstance(amount, (int, float))}
        if isinstance(found, dict)
        else {}
    )


def _stamp(recipe: dict[str, Any]) -> str:
    """Wanneer Mealie het recept het laatst wijzigde; verandert dat, dan halen we het opnieuw op."""
    return str(recipe.get("updatedAt") or recipe.get("dateUpdated") or "").strip()


def _sort_name(name: Any) -> str:
    return " ".join(str(name or "").split()).casefold()


def _organizers(items: Any) -> list[dict[str, str]]:
    """De tags of categorieën van een recept als `[{"slug": .., "name": ..}]`; zonder slug telt een tag niet mee."""
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        slug = str(item.get("slug") or "").strip()
        name = " ".join(str(item.get("name") or "").split()) or slug
        if slug and slug not in seen:
            seen.add(slug)
            found.append({"slug": slug, "name": name})
    return found


def _facts(recipe: dict[str, Any]) -> dict[str, Any]:
    """Waarop Recepten zoeken filtert en sorteert (issue #322). Staat zowel in de lijst van Mealie als in een volledig recept."""
    rating = recipe.get("rating")
    return {
        "tags": _organizers(recipe.get("tags")),
        "categories": _organizers(recipe.get("recipeCategory")),
        "minutes": mealie.recipe_minutes(recipe),
        "created_at": str(recipe.get("createdAt") or recipe.get("dateAdded") or "").strip(),
        "rating": float(rating) if isinstance(rating, (int, float)) and not isinstance(rating, bool) and rating > 0 else None,
    }


def _facts_params(facts: dict[str, Any]) -> tuple[Any, ...]:
    """De kolommen `tags`, `categories`, `minutes`, `created_at` en `rating`, in die volgorde."""
    return (json.dumps(facts["tags"]), json.dumps(facts["categories"]), facts["minutes"], facts["created_at"], facts["rating"])


def _row_facts(row: Any) -> dict[str, Any]:
    """De gegevens van `_facts` uit een rij van de cache."""

    def listed(raw: Any) -> list[dict[str, str]]:
        try:
            return _organizers(json.loads(raw)) if raw else []
        except ValueError:
            return []

    rating = row["rating"]
    return {
        "tags": listed(row["tags"]),
        "categories": listed(row["categories"]),
        "minutes": int(row["minutes"]) if row["minutes"] else None,
        "created_at": str(row["created_at"] or ""),
        "rating": float(rating) if rating else None,
    }


# --- de cache bijhouden -------------------------------------------------------------------------------------------


async def remember(recipe: dict[str, Any]) -> None:
    """Zet een volledig recept uit Mealie (`GET /api/recipes/{slug}`) in de cache. Gooit nooit: het ophalen van het recept
    zelf mag hier niet op stuklopen."""
    try:
        recipe_id, slug = str(recipe.get("id") or "").strip(), str(recipe.get("slug") or "").strip()
        if not recipe_id or not slug:
            return
        card = mealie.recipe_card(recipe)
        found = nutrition.values(recipe.get("nutrition"))
        async with _connect() as conn:
            await conn.execute(
                """
                INSERT INTO mealie_recipe_nutrition (recipe_id, slug, name, updated_at, kcal, card, fetched_at, nutrition,
                  tags, categories, minutes, created_at, rating, derived)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(recipe_id) DO UPDATE SET slug = excluded.slug, name = excluded.name, updated_at = excluded.updated_at,
                  kcal = excluded.kcal, card = excluded.card, fetched_at = excluded.fetched_at, nutrition = excluded.nutrition,
                  tags = excluded.tags, categories = excluded.categories, minutes = excluded.minutes, created_at = excluded.created_at,
                  rating = excluded.rating, derived = excluded.derived
                """,
                (
                    recipe_id,
                    slug,
                    card["name"],
                    _stamp(recipe),
                    found.get("calories"),
                    json.dumps(card),
                    time.time(),
                    json.dumps(found),
                    *_facts_params(_facts(recipe)),
                    json.dumps(recipe_diet.classify(recipe_diet.ingredient_lines(recipe))),
                ),
            )
            await conn.commit()
    except Exception:  # noqa: BLE001 - een cache die niet bijgewerkt wordt, is geen reden om het recept niet te tonen
        log.debug("Calorieën van een recept niet bewaard", exc_info=True)


async def lookup(slugs: list[str]) -> dict[str, dict[str, float]]:
    """De bekende voedingswaarden per portie bij deze slugs (alleen de recepten die al in de cache staan)."""
    wanted = [str(slug) for slug in slugs if slug]
    if not wanted:
        return {}
    try:
        async with _connect() as conn:
            rows = await conn.execute_fetchall(
                f"SELECT slug, kcal, nutrition FROM mealie_recipe_nutrition WHERE slug IN ({','.join('?' * len(wanted))})", wanted
            )
    except Exception:  # noqa: BLE001 - zonder cache zijn er gewoon geen voedingswaarden bij de kaarten
        log.debug("Voedingswaarden van de recepten niet gelezen", exc_info=True)
        return {}
    return {row["slug"]: _values(row) for row in rows}


def refresh_running() -> asyncio.Task[Any] | None:
    """De ronde die nu loopt, op deze event loop (een task van een gesloten loop telt niet)."""
    task = _refresh_task
    if task is None or task.done():
        return None
    try:
        return task if task.get_loop() is asyncio.get_running_loop() else None
    except RuntimeError:
        return None


def start_refresh() -> asyncio.Task[Any]:
    """Start een ronde (als er nog geen loopt) en geeft de task."""
    global _refresh_task
    running = refresh_running()
    if running is not None:
        return running
    _refresh.update(done=0, total=0, error="")
    # Niet zwaar: de lijst van Mealie (hooguit MAX_RECIPES samenvattingen), per nieuw recept een verzoek en een rij (taak 10).
    _refresh_task = tasks.spawn("recipe-nutrition", _run_refresh())
    return _refresh_task


def refresh_progress() -> dict[str, int]:
    return {"done": int(_refresh["done"]), "total": int(_refresh["total"])}


async def refresh_nightly() -> bool:
    """De ronde van de dagelijkse run (offers_schedule.run_all), alleen als Mealie gekoppeld is. Zo zijn de tijden, tags en
    voedingswaarden ook bij als niemand overdag zoekt. Start de ronde in een eigen task; True als er een liep of startte."""
    try:
        await mealie.ensure_configured()
    except Exception:  # noqa: BLE001 - zonder Mealie is er niets bij te werken
        return False
    start_refresh()
    return True


# --- tags voor vegetarisch, veganistisch, vlees, vis en gevogelte in Mealie (4.22.0) ---------------------------------------


async def diet_tags_enabled() -> bool:
    """Of MealieShopper de tags uit de ingrediënten in Mealie zet (Instellingen, Koppelen, Mealie); standaard wel."""
    return (await auth.get_setting(SETTING_DIET_TAGS)).strip() != "0"


def tags_running() -> asyncio.Task[Any] | None:
    task = _tags_task
    if task is None or task.done():
        return None
    try:
        return task if task.get_loop() is asyncio.get_running_loop() else None
    except RuntimeError:
        return None


def start_tags() -> asyncio.Task[Any]:
    """Start het zetten van tags (als het nog niet loopt) en geeft de task."""
    global _tags_task
    running = tags_running()
    if running is not None:
        return running
    _tags_task = tasks.spawn("recipe-diet-tags", _run_tags())
    return _tags_task


def _tag_plan(row: Any) -> tuple[list[str], list[str], dict[str, list[str]]]:
    """Wat er bij een recept moet: (tags erbij, tags eraf, de nieuwe stand van `auto_tags`).

    Een tag die MealieShopper zette en die niet meer bij het recept staat, haalde jij weg: die komt bij `declined` en wordt bij
    dit recept niet opnieuw gezet. Een tag die jij zelf zette, haalt MealieShopper nooit weg."""
    state = _auto_state(row["auto_tags"])
    facts = _row_facts(row)
    present = {item["name"].casefold() for item in facts["tags"]}
    declined = {name.casefold() for name in state["declined"]} | {name.casefold() for name in state["added"] if name.casefold() not in present}
    added = [name for name in state["added"] if name.casefold() in present]
    mine = {name.casefold() for name in added}
    labels = recipe_diet.from_labels([item for item in facts["tags"] if item["name"].casefold() not in mine], facts["categories"])
    wanted = [name for name in recipe_diet.wanted_tags(_derived(row["derived"]), labels) if name.casefold() not in declined]
    wanted_folded = {name.casefold() for name in wanted}
    to_add = [name for name in wanted if name.casefold() not in present]
    to_remove = [name for name in added if name.casefold() not in wanted_folded]
    kept = [name for name in added if name.casefold() in wanted_folded]
    return to_add, to_remove, {"added": sorted(set(kept) | set(to_add)), "declined": sorted(declined)}


async def _run_tags() -> None:
    """Zet de tags uit de ingrediënten bij de recepten in Mealie en haal de tags weg die MealieShopper zette en die niet meer
    kloppen. Hooguit `MAX_TAG_WRITES` recepten per ronde. Mag MealieShopper geen tags aanmaken (het recht *organiseren* in
    Mealie), dan stopt de ronde; een fout bij één recept niet."""
    try:
        if not await diet_tags_enabled():
            return
        async with _connect() as conn:
            rows = await conn.execute_fetchall(
                "SELECT recipe_id, slug, tags, categories, minutes, created_at, rating, derived, auto_tags FROM mealie_recipe_nutrition"
                " WHERE derived IS NOT NULL"
            )
        refs: dict[str, dict[str, str]] = {}
        written = 0
        for row in rows:
            to_add, to_remove, state = _tag_plan(row)
            if not to_add and not to_remove:
                if state != _auto_state(row["auto_tags"]):
                    async with _connect() as conn:
                        await conn.execute(
                            "UPDATE mealie_recipe_nutrition SET auto_tags = ? WHERE recipe_id = ?", (json.dumps(state), row["recipe_id"])
                        )
                        await conn.commit()
                continue
            if written >= MAX_TAG_WRITES:
                break
            written += 1
            try:
                for name in to_add:
                    if name.casefold() not in refs:
                        refs[name.casefold()] = await mealie.ensure_tag(name)
                tags = await mealie.change_tags(row["slug"], [refs[name.casefold()] for name in to_add], to_remove)
            except mealie.OrganizerError as exc:
                log.warning("Tags in Mealie zetten gestopt: %s", exc)
                return
            except Exception as exc:  # noqa: BLE001 - dit recept de volgende ronde opnieuw
                log.warning("Tags bij %s zetten mislukt: %s", row["slug"], exc)
                continue
            async with _connect() as conn:
                await conn.execute(
                    "UPDATE mealie_recipe_nutrition SET tags = ?, auto_tags = ? WHERE recipe_id = ?",
                    (json.dumps(_organizers(tags)), json.dumps(state), row["recipe_id"]),
                )
                await conn.commit()
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - de volgende ronde probeert het opnieuw
        log.warning("Tags in Mealie zetten mislukt: %s", exc)


async def _run_refresh() -> None:
    """Lees de lijst van Mealie en haal de recepten op die nieuw of gewijzigd zijn. Een fout wordt bewaard, niet gegooid."""
    try:
        base = await mealie.mealie_url()
        summaries = await mealie.list_recipes(MAX_RECIPES)
        async with _connect() as conn:
            meta = await auth.fetchone(conn, "SELECT base FROM mealie_recipe_nutrition_meta WHERE id = 1")
            if meta is not None and meta["base"] != base:
                await conn.execute("DELETE FROM mealie_recipe_nutrition")  # een ander Mealie: begin opnieuw
                await conn.commit()
            rows = await conn.execute_fetchall("SELECT recipe_id, updated_at, fetched_at, nutrition, derived FROM mealie_recipe_nutrition")
        known = {row["recipe_id"]: (row["updated_at"], row["fetched_at"], row["nutrition"], row["derived"]) for row in rows}
        now = time.time()

        def stale(summary: dict[str, Any]) -> bool:
            row = known.get(str(summary.get("id") or ""))
            if row is None or row[2] is None or row[3] is None:  # nieuw, van vóór 4.7.0 (alleen de kcal) of van vóór 4.22.0
                return True
            stamp = _stamp(summary)
            return stamp != row[0] if stamp else now - row[1] > STALE_WITHOUT_STAMP

        todo = [summary for summary in summaries if stale(summary)]
        _refresh["total"] = len(todo)

        async def one(summary: dict[str, Any]) -> None:
            try:
                await mealie.get_recipe(summary["slug"])  # `get_recipe` zet het in de cache
            except Exception:  # noqa: BLE001 - een recept dat niet lukt, proberen we de volgende ronde weer
                log.debug("Recept %s niet ingelezen", summary.get("slug"), exc_info=True)
            finally:
                _refresh["done"] += 1

        await tasks.map_bounded(mealie.RECIPE_WORKERS, one, todo)
        ids = [str(summary.get("id")) for summary in summaries if summary.get("id")]
        # De kaart, de tags, de categorieën, de tijd en de waardering staan in de lijst: die werken we voor elk recept bij, ook als
        # het recept zelf niet veranderde (een tag die je in Mealie hernoemt, verandert de `updatedAt` van het recept niet).
        current = []
        for summary in summaries:
            if summary.get("id"):
                card = mealie.recipe_card(summary)
                current.append((card["name"], json.dumps(card), *_facts_params(_facts(summary)), str(summary["id"])))
        async with _connect() as conn:
            await conn.executemany(
                """
                UPDATE mealie_recipe_nutrition SET name = ?, card = ?, tags = ?, categories = ?, minutes = ?, created_at = ?, rating = ?
                WHERE recipe_id = ?
                """,
                current,
            )
            if len(summaries) < MAX_RECIPES:  # anders weten we niet welke er weg zijn
                await conn.execute(f"DELETE FROM mealie_recipe_nutrition WHERE recipe_id NOT IN ({','.join('?' * len(ids)) or 'NULL'})", ids)
            await conn.execute(
                """
                INSERT INTO mealie_recipe_nutrition_meta (id, base, synced_at) VALUES (1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET base = excluded.base, synced_at = excluded.synced_at
                """,
                (base or "", time.time()),
            )
            await conn.commit()
        _refresh["failedAt"] = 0.0
        start_tags()  # in een eigen task: een zoekopdracht wacht niet op het zetten van tags
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - de volgende zoekopdracht toont de fout of probeert het opnieuw
        log.warning("Calorieën van de recepten inlezen mislukt: %s", exc)
        _refresh.update(error=str(exc)[:200] or tr("mislukt", "failed"), failedAt=time.time())


async def _fresh(wait: float) -> dict[str, int] | None:
    """Zorg dat de cache bij is: start zo nodig een ronde en wacht er hooguit `wait` seconden op. None als hij bij is, anders
    de voortgang (de zoekopdracht toont dan wat er al is)."""
    base = await mealie.mealie_url()
    async with _connect() as conn:
        meta = await auth.fetchone(conn, "SELECT base, synced_at FROM mealie_recipe_nutrition_meta WHERE id = 1")
        # Een rij zonder tags (vóór 4.20.0) of zonder `derived` (vóór 4.22.0): de eerste ronde daarna vult ze, ook als de cache
        # verder bij is.
        unfilled = await auth.fetchone(conn, "SELECT 1 FROM mealie_recipe_nutrition WHERE tags IS NULL OR derived IS NULL LIMIT 1")
    fresh = meta is not None and meta["base"] == (base or "") and time.time() - meta["synced_at"] < INDEX_TTL and unfilled is None
    task = refresh_running()
    if task is None and not fresh and time.time() - _refresh["failedAt"] >= RETRY_AFTER:
        task = start_refresh()
    if task is None:
        return None
    # Wachten zonder de ronde te annuleren als de tijd op is. Niet `asyncio.wait_for(asyncio.shield(task))`: vanaf Python 3.15
    # meldt shield een fout van de ronde na de time-out ook zelf bij de loop, naast de melding van `tasks.spawn`.
    done, _ = await asyncio.wait((task,), timeout=wait)
    if not done:
        return refresh_progress()
    task.result()  # een fout van de ronde komt bij de zoekopdracht, zoals voorheen
    return None


# --- zoeken -----------------------------------------------------------------------------------------------------


Limits = dict[str, tuple[float | None, float | None]]


def _bound(value: Any, top: int, decimals: int) -> float | None:
    """Eén grens uit een verzoek: een getal van 0 tot en met `top`, afgerond op `decimals`, of None als er niets staat."""
    if value is None or isinstance(value, bool) or (isinstance(value, str) and not value.strip()):
        return None
    try:
        amount = float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        amount = math.nan
    if not math.isfinite(amount):
        raise ValueError(tr("Geef de voedingswaarden op als een getal.", "Give the nutrition values as numbers."))
    amount = max(0.0, min(float(top), amount))
    return round(amount, decimals) if decimals else float(int(amount))


def bounds(source: Any, *, mcp: bool = False) -> Limits:
    """De grenzen van een zoekopdracht per waarde (zie `nutrition.FILTERS`): `<naam>Min` en `<naam>Max` uit een verzoek, of
    met `mcp` `<naam>_min` en `<naam>_max`. Alleen wat er staat; een ondergrens boven de bovengrens draaien we om."""
    found: Limits = {}
    for name, (_field, top, decimals) in nutrition.FILTERS.items():
        keys = (f"{name}_min", f"{name}_max") if mcp else (f"{name}Min", f"{name}Max")
        low, high = (_bound(source.get(key), top, decimals) for key in keys)
        if low is None and high is None:
            continue
        if low is not None and high is not None and low > high:
            low, high = high, low
        found[name] = (low, high)
    return found


def fits(found: dict[str, float], limits: Limits) -> bool:
    """Of een recept binnen alle grenzen valt; een recept zonder een gevraagde waarde valt erbuiten."""
    for name, (low, high) in limits.items():
        amount = nutrition.value_of(found, name)
        if amount is None or (low is not None and amount < low - 1e-9) or (high is not None and amount > high + 1e-9):
            return False
    return True


def card_with(card: dict[str, Any], found: dict[str, float]) -> dict[str, Any]:
    """Een kaart met de kcal per portie (`kcal`, of None) en alle voedingswaarden (`nutrition`, zie nutrition.items)."""
    kcal = found.get("calories")
    return {**card, "kcal": round(kcal) if kcal is not None else None, "nutrition": nutrition.items(found)}


@dataclass(frozen=True)
class Filters:
    """De filters van Recepten zoeken naast de voedingswaarden (issue #322). Tags en categorieën zijn slugs: een recept moet
    alles uit `tags` en `categories` hebben en niets uit `tags_out` en `categories_out`. `max_minutes` laat alleen recepten
    met een bekende bereidingstijd tot en met die grens door. `sort` is een van `SORTS`. Sinds 4.22.0 ook de menugang
    (`course`, recipe_diet.COURSES), het dieet (`diet`: vegetarisch of veganistisch) en `protein` (vlees, vis of gevogelte)."""

    tags: tuple[str, ...] = ()
    tags_out: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    categories_out: tuple[str, ...] = ()
    max_minutes: int | None = None
    sort: str = ""
    course: str = ""
    diet: str = ""
    protein: str = ""

    def active(self) -> bool:
        """Of er iets anders gevraagd is dan de standaard; zonder filters zoekt de route gewoon in Mealie zelf."""
        return bool(
            self.tags
            or self.tags_out
            or self.categories
            or self.categories_out
            or self.max_minutes
            or self.sort
            or self.course
            or self.diet
            or self.protein
        )


def _slugs(value: Any) -> tuple[str, ...]:
    """Een lijst slugs uit een verzoek: `a,b` of herhaald (`tags=a&tags=b`), zonder lege en dubbele, hooguit `MAX_FILTER_VALUES`."""
    raw = value if isinstance(value, (list, tuple)) else [value]
    found: list[str] = []
    for part in raw:
        for slug in str(part or "").split(","):
            slug = slug.strip()[:100]
            if slug and slug not in found:
                found.append(slug)
    return tuple(found[:MAX_FILTER_VALUES])


def filters(source: Any) -> Filters:
    """De filters uit een verzoek: `tags`, `tagsOut`, `categories`, `categoriesOut` (slugs, met komma's), `maxMinutes`, `sort`,
    `course`, `diet` en `protein` (zie recipe_diet.wanted_from)."""

    def values(key: str) -> tuple[str, ...]:
        getlist = getattr(source, "getlist", None)
        return _slugs(getlist(key) if callable(getlist) else source.get(key))

    common = recipe_diet.wanted_from(source)
    sort = str(source.get("sort") or "").strip()
    return Filters(
        tags=values("tags"),
        tags_out=values("tagsOut"),
        categories=values("categories"),
        categories_out=values("categoriesOut"),
        max_minutes=common.max_minutes,
        sort=sort if sort in SORTS else "",
        course=common.course,
        diet=common.diet,
        protein=common.protein,
    )


def _passes(facts: dict[str, Any], wanted: Filters) -> bool:
    """Of een recept door de tags, categorieën, menugang, dieet en vlees/vis/gevogelte van `wanted` komt (de tijd telt `search`
    apart). `facts` heeft daarvoor ook `kind` (zie `_kind`)."""
    for chosen, excluded, key in ((wanted.tags, wanted.tags_out, "tags"), (wanted.categories, wanted.categories_out, "categories")):
        own = {item["slug"] for item in facts[key]}
        if not own.issuperset(chosen) or own.intersection(excluded):
            return False
    kind = facts.get("kind") or {}
    if wanted.course and kind.get("course") != wanted.course:
        return False
    return not (wanted.diet or wanted.protein) or recipe_diet.matches(kind, wanted.diet, wanted.protein)


def _auto_state(raw: Any) -> dict[str, list[str]]:
    """De tags die MealieShopper zelf zette (`added`) en die jij weghaalde (`declined`), uit de kolom `auto_tags`."""
    try:
        found = json.loads(raw) if raw else {}
    except ValueError:
        found = {}
    found = found if isinstance(found, dict) else {}
    return {key: [str(name) for name in found.get(key) or [] if str(name).strip()] for key in ("added", "declined")}


def _derived(raw: Any) -> dict[str, Any] | None:
    """Wat de ingrediënten zeggen (recipe_diet.classify), uit de kolom `derived`; None zonder ingrediënten of nog niet bekeken."""
    try:
        found = json.loads(raw) if raw else None
    except ValueError:
        return None
    return found if isinstance(found, dict) and "diet" in found else None


def _kind(facts: dict[str, Any], derived: dict[str, Any] | None, added: list[str]) -> dict[str, Any]:
    """De menugang, het dieet en vlees/vis/gevogelte van een recept: je eigen tags en categorieën (zonder de tags die MealieShopper
    zelf zette) met wat de ingrediënten zeggen. Een recept zonder menugang telt als hoofdgerecht, zoals bij de maaltijdboxen."""
    mine = {name.casefold() for name in added}
    own_tags = [item for item in facts["tags"] if item["name"].casefold() not in mine]
    course = offer_courses.course_of(facts["categories"], own_tags)
    labels = recipe_diet.from_labels(own_tags, facts["categories"])
    return {"course": "hoofdgerecht" if course == offer_courses.OTHER else course, **recipe_diet.combined(labels, derived)}


def _ordered(candidates: list[tuple[dict[str, Any], dict[str, float], dict[str, Any]]], sort: str, by_name: bool) -> list[Any]:
    """De volgorde van de treffers: nieuwste eerst, waardering of naam (een recept zonder datum of waardering achteraan, dan op
    naam). `by_name` False houdt de volgorde zoals ze binnenkwam: de beste treffer van Mealie bij een zoekterm."""
    if sort == "newest":
        named = sorted(candidates, key=lambda item: _sort_name(item[0].get("name")))
        return sorted(named, key=lambda item: item[2]["created_at"] or "", reverse=True)
    if sort == "rating":
        return sorted(candidates, key=lambda item: (-(item[2]["rating"] or 0), _sort_name(item[0].get("name"))))
    if sort == "name" or by_name:
        return sorted(candidates, key=lambda item: _sort_name(item[0].get("name")))
    return candidates


async def search(
    query: Any,
    limits: Limits,
    page: Any = 1,
    per_page: int = mealie.SEARCH_PAGE,
    wait: float = SEARCH_WAIT,
    wanted: Filters | None = None,
) -> dict[str, Any]:
    """Je recepten in Mealie die binnen de grenzen vallen (`limits`, zie `bounds`, per portie) en door de filters komen
    (`wanted`, zie `Filters`).

    Met een zoekterm bepaalt Mealie welke recepten passen (en, zonder `sort`, in welke volgorde); zonder zoekterm komen alle
    recepten uit de cache, op naam of zoals `sort` zegt. Een recept zonder een gevraagde voedingswaarde valt weg en telt mee in
    `withoutValues` (en, zoals in 4.4, `withoutKcal`); een recept zonder bereidingstijd bij een grens op de tijd telt mee in
    `withoutTime`. Loopt de eerste ronde nog, dan staat de voortgang in `indexing` (`{done, total}`) en komt wat er al bekend is.
    """
    wanted = wanted or Filters()
    await mealie.ensure_configured()
    try:
        number = max(1, int(page or 1))
    except (TypeError, ValueError):
        number = 1
    size = max(1, min(100, int(per_page)))
    indexing = await _fresh(wait)
    async with _connect() as conn:
        rows = await conn.execute_fetchall(
            "SELECT slug, name, kcal, card, nutrition, tags, categories, minutes, created_at, rating, derived, auto_tags FROM mealie_recipe_nutrition"
        )
    if not rows and indexing is None and _refresh["error"]:
        raise RuntimeError(_refresh["error"])
    known = {row["slug"]: _values(row) for row in rows}
    extra = {row["slug"]: (_derived(row["derived"]), _auto_state(row["auto_tags"])["added"]) for row in rows}

    def with_kind(slug: str, facts: dict[str, Any]) -> dict[str, Any]:
        derived, added = extra.get(slug, (None, []))
        return {**facts, "kind": _kind(facts, derived, added)}

    text = " ".join(str(query or "").split())[:200]
    if text:
        # De lijst van Mealie heeft de tags, categorieën en tijden zelf al: die zijn hier het verst bij.
        listed = await mealie.list_recipes(MAX_RECIPES, search=text)
        candidates = [
            (mealie.recipe_card(summary), known.get(summary["slug"]) or {}, with_kind(summary["slug"], _facts(summary))) for summary in listed
        ]
    else:
        candidates = [(json.loads(row["card"]), known[row["slug"]], with_kind(row["slug"], _row_facts(row))) for row in rows]

    candidates = [item for item in candidates if _passes(item[2], wanted)]
    without_values = sum(1 for _, found, _ in candidates if any(nutrition.value_of(found, name) is None for name in limits))
    without_time = sum(1 for _, _, facts in candidates if facts["minutes"] is None) if wanted.max_minutes else 0
    kept = [
        item
        for item in candidates
        if fits(item[1], limits) and (not wanted.max_minutes or (item[2]["minutes"] is not None and item[2]["minutes"] <= wanted.max_minutes))
    ]
    matches = [card_with(card, found) for card, found, _ in _ordered(kept, wanted.sort, by_name=not text)]
    start = (number - 1) * size
    return {
        "recipes": matches[start : start + size],
        "total": len(matches),
        "page": number,
        "more": start + size < len(matches),
        "withoutValues": without_values,
        "withoutKcal": without_values,
        "withoutTime": without_time,
        "indexing": indexing,
    }


def _counted(lists: Iterable[list[dict[str, str]]]) -> list[dict[str, Any]]:
    """Telt tags of categorieën over de recepten: `[{value: slug, label: naam, count}]`, op aantal en dan op naam."""
    counts: dict[str, list[Any]] = {}
    for items in lists:
        for item in items:
            entry = counts.setdefault(item["slug"], [item["name"], 0])
            entry[1] += 1
    return sorted(
        ({"value": slug, "label": label, "count": count} for slug, (label, count) in counts.items()),
        key=lambda item: (-item["count"], _sort_name(item["label"])),
    )


async def facets(wait: float = FACETS_WAIT) -> dict[str, Any]:
    """De tags en categorieën van je recepten in Mealie met het aantal recepten per stuk, voor de filters van Recepten zoeken.
    Loopt de eerste ronde nog, dan staat de voortgang in `indexing` en tellen alleen de recepten die al binnen zijn."""
    await mealie.ensure_configured()
    indexing = await _fresh(wait)
    async with _connect() as conn:
        rows = await conn.execute_fetchall("SELECT tags, categories, minutes, created_at, rating FROM mealie_recipe_nutrition")
    if not rows and indexing is None and _refresh["error"]:
        raise RuntimeError(_refresh["error"])
    facts = [_row_facts(row) for row in rows]
    return {
        "tags": _counted(item["tags"] for item in facts),
        "categories": _counted(item["categories"] for item in facts),
        "recipes": len(facts),
        "withTime": sum(1 for item in facts if item["minutes"] is not None),
        "indexing": indexing,
    }


# --- oude AH-recepten aanvullen (alleen de beheerder) ---------------------------------------------------------------

PAUSE_SECONDS = 1.5  # tussen twee recepten bij AH, zoals de bulkimport
MAX_FAILURES_IN_A_ROW = 5  # daarna stopt het aanvullen: dan is er iets mis met AH of Mealie


class Busy(ValueError):
    """Het aanvullen loopt al."""


_backfill: dict[str, Any] = {"state": "idle"}
_backfill_cancel: asyncio.Event = asyncio.Event()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def backfill_progress() -> dict[str, Any]:
    """De stand van het lopende (of laatste) aanvullen: `state` (idle, running, done, stopped of error) en de tellingen."""
    return dict(_backfill)


def start_backfill() -> dict[str, Any]:
    global _backfill_cancel
    if _backfill.get("state") == "running":
        raise Busy(tr("Het aanvullen loopt al.", "Filling in is already running."))
    _backfill.clear()
    _backfill.update(state="running", startedAt=_now(), total=0, checked=0, updated=0, skipped=0, failed=0, error="")
    _backfill_cancel = asyncio.Event()  # een nieuwe per keer: een Event hoort bij de event loop waarop hij wacht
    # Niet zwaar: per recept van Allerhande een verzoek aan Mealie en aan AH, met een pauze ertussen (taak 10).
    tasks.spawn("recipe-nutrition-backfill", _run_backfill())
    return backfill_progress()


def stop_backfill() -> dict[str, Any]:
    if _backfill.get("state") == "running":
        _backfill_cancel.set()
    return backfill_progress()


async def _pause() -> bool:
    """Wacht `PAUSE_SECONDS`; True als intussen op Stoppen is gedrukt."""
    if PAUSE_SECONDS <= 0:
        return _backfill_cancel.is_set()
    try:
        await asyncio.wait_for(_backfill_cancel.wait(), PAUSE_SECONDS)
    except asyncio.TimeoutError:
        return False
    return True


def _finish(state: str, error: str = "") -> None:
    _backfill.update(state=state, error=error, finishedAt=_now())


async def _run_backfill() -> None:
    started = time.perf_counter()
    try:
        summaries = await mealie.list_recipes(MAX_RECIPES)
        todo = [(summary, recipe_id) for summary in summaries if (recipe_id := ah.allerhande_recipe_id(str(summary.get("orgURL") or "")))]
        _backfill["total"] = len(todo)
        failures = 0
        asked_ah = False
        for summary, ah_id in todo:
            # Een pauze na elke vraag aan AH, niet na een recept dat al calorieën had; Stoppen werkt ook tijdens de pauze.
            if _backfill_cancel.is_set() or (asked_ah and await _pause()):
                _finish("stopped")
                return
            asked_ah = False
            try:
                recipe = await mealie.get_recipe(summary["slug"])
                current = recipe.get("nutrition") if isinstance(recipe.get("nutrition"), dict) else {}
                if nutrition.kcal(current) is not None:
                    _backfill["skipped"] += 1  # heeft al calorieën: niets aan veranderen
                    continue
                asked_ah = True
                try:
                    found = nutrition.values((await ah.get_recipe_jsonld(ah_id)).get("nutrition"))
                except ah.RecipeUnavailable:
                    found = {}
                if "calories" not in found:
                    _backfill["skipped"] += 1  # AH heeft (dit recept of zijn voedingswaarden) niet
                    continue
                # Wat al in Mealie staat, blijft staan; AH vult alleen aan wat ontbreekt.
                kept = {key: str(value).strip() for key, value in current.items() if key in nutrition.UNITS and str(value or "").strip()}
                fields = {**nutrition.mealie_fields(found), **kept}
                await mealie.update_recipe_nutrition(summary["slug"], fields)
                await remember({**recipe, "nutrition": fields})
                _backfill["updated"] += 1
                failures = 0
            except ah.AhHttpError as exc:
                if exc.status in (403, 429):
                    _finish(
                        "stopped", tr("AH weigert even verzoeken; probeer het later opnieuw.", "AH refuses requests for a while; try again later.")
                    )
                    return
                failures += 1
                _backfill["failed"] += 1
            except Exception as exc:  # noqa: BLE001 - een recept dat niet lukt, telt mee; te veel achter elkaar stopt het aanvullen
                log.warning("Voedingswaarden aanvullen voor %s mislukt: %s", summary.get("slug"), exc)
                failures += 1
                _backfill["failed"] += 1
                if failures >= MAX_FAILURES_IN_A_ROW:
                    _finish("error", str(exc)[:200])
                    return
            finally:
                _backfill["checked"] += 1
        _finish("done")
    except asyncio.CancelledError:
        _finish("stopped")
        raise
    except Exception as exc:  # noqa: BLE001 - de stand toont de fout
        log.warning("Voedingswaarden aanvullen mislukt: %s", exc)
        _finish("error", str(exc)[:200] or tr("mislukt", "failed"))
    finally:
        await audit.record(
            kind="outbound",
            service="mealie",
            action="Fill in nutrition from AH",
            method="PATCH",
            ok=_backfill.get("state") == "done",
            duration_ms=round((time.perf_counter() - started) * 1000),
            actor=await audit.current_actor(),
            error=str(_backfill.get("error") or ""),
            detail={key: _backfill.get(key) for key in ("state", "total", "checked", "updated", "skipped", "failed")},
        )
