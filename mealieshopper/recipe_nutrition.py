"""De calorieën van je recepten in Mealie, om op te zoeken (issue #283), en het aanvullen van oude AH-recepten.

Mealie geeft in zijn lijst van recepten (`/api/recipes`) geen voedingswaarden, en bewaart ze als tekst: op "tussen 400 en
600 kcal" kan Mealie zelf niet zoeken. Daarom houden we per recept de kcal per portie bij, met de kaart voor de lijst:

- elk recept dat MealieShopper uit Mealie ophaalt (het weekmenu, een recept openen, de receptindex) komt meteen in de cache
  (`remember`, vanuit `mealie.get_recipe`);
- zoek je op calorieën en is de cache ouder dan `INDEX_TTL`, dan leest `refresh` de lijst van Mealie en haalt alleen de
  recepten op die nieuw zijn of een andere `updatedAt` hebben.

De cache is opnieuw op te bouwen en gaat niet mee in de back-up (zie `backup.EXCLUDED_TABLES`). Hoort hij bij een ander
Mealie-adres, dan begint hij opnieuw.

Recepten die je vroeger uit Allerhande importeerde, hebben geen voedingswaarden: die vroeg MealieShopper toen niet aan AH.
`start_backfill` (alleen de beheerder) haalt ze voor die recepten bij AH op en zet ze in Mealie.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from . import ah, audit, auth, mealie, nutrition, tasks
from .i18n import tr

log = logging.getLogger(__name__)

INDEX_TTL = 600  # seconden: zo lang geldt de cache als bij, daarna leest een zoekopdracht op calorieën de lijst opnieuw
RETRY_AFTER = 60  # seconden na een mislukte ronde (Mealie onbereikbaar) voordat een zoekopdracht het opnieuw probeert
STALE_WITHOUT_STAMP = 24 * 3600  # een recept zonder `updatedAt` halen we na een dag opnieuw op
MAX_RECIPES = 2000  # zoveel recepten lezen we hooguit in
MAX_KCAL = 5000  # de hoogste grens die je kunt opgeven, per portie
SEARCH_WAIT = 15.0  # seconden die een zoekopdracht op de eerste ronde wacht; daarna komt wat er al is, met de voortgang

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

_refresh_task: asyncio.Task[Any] | None = None
_refresh: dict[str, Any] = {"done": 0, "total": 0, "error": "", "failedAt": 0.0}


@asynccontextmanager
async def _connect() -> AsyncIterator[aiosqlite.Connection]:
    """Een verbinding met de tabellen van de cache. Sluit zonder commit, zoals `auth.connect`."""
    async with auth.connect() as conn:
        await conn.executescript(SCHEMA)
        yield conn


def _stamp(recipe: dict[str, Any]) -> str:
    """Wanneer Mealie het recept het laatst wijzigde; verandert dat, dan halen we het opnieuw op."""
    return str(recipe.get("updatedAt") or recipe.get("dateUpdated") or "").strip()


def _sort_name(name: Any) -> str:
    return " ".join(str(name or "").split()).casefold()


# --- de cache bijhouden -------------------------------------------------------------------------------------------


async def remember(recipe: dict[str, Any]) -> None:
    """Zet een volledig recept uit Mealie (`GET /api/recipes/{slug}`) in de cache. Gooit nooit: het ophalen van het recept
    zelf mag hier niet op stuklopen."""
    try:
        recipe_id, slug = str(recipe.get("id") or "").strip(), str(recipe.get("slug") or "").strip()
        if not recipe_id or not slug:
            return
        card = mealie.recipe_card(recipe)
        async with _connect() as conn:
            await conn.execute(
                """
                INSERT INTO mealie_recipe_nutrition (recipe_id, slug, name, updated_at, kcal, card, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(recipe_id) DO UPDATE SET slug = excluded.slug, name = excluded.name, updated_at = excluded.updated_at,
                  kcal = excluded.kcal, card = excluded.card, fetched_at = excluded.fetched_at
                """,
                (recipe_id, slug, card["name"], _stamp(recipe), nutrition.kcal(recipe.get("nutrition")), json.dumps(card), time.time()),
            )
            await conn.commit()
    except Exception:  # noqa: BLE001 - een cache die niet bijgewerkt wordt, is geen reden om het recept niet te tonen
        log.debug("Calorieën van een recept niet bewaard", exc_info=True)


async def lookup(slugs: list[str]) -> dict[str, float | None]:
    """De bekende kcal per portie bij deze slugs (alleen de recepten die al in de cache staan)."""
    wanted = [str(slug) for slug in slugs if slug]
    if not wanted:
        return {}
    try:
        async with _connect() as conn:
            rows = await conn.execute_fetchall(
                f"SELECT slug, kcal FROM mealie_recipe_nutrition WHERE slug IN ({','.join('?' * len(wanted))})", wanted
            )
    except Exception:  # noqa: BLE001 - zonder cache zijn er gewoon geen calorieën bij de kaarten
        log.debug("Calorieën van de recepten niet gelezen", exc_info=True)
        return {}
    return {row["slug"]: row["kcal"] for row in rows}


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
    _refresh_task = tasks.spawn("recipe-nutrition", _run_refresh())
    return _refresh_task


def refresh_progress() -> dict[str, int]:
    return {"done": int(_refresh["done"]), "total": int(_refresh["total"])}


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
            rows = await conn.execute_fetchall("SELECT recipe_id, updated_at, fetched_at FROM mealie_recipe_nutrition")
        known = {row["recipe_id"]: (row["updated_at"], row["fetched_at"]) for row in rows}
        now = time.time()

        def stale(summary: dict[str, Any]) -> bool:
            row = known.get(str(summary.get("id") or ""))
            if row is None:
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
        async with _connect() as conn:
            if len(summaries) < MAX_RECIPES:  # anders weten we niet welke er weg zijn
                await conn.execute(
                    f"DELETE FROM mealie_recipe_nutrition WHERE recipe_id NOT IN ({','.join('?' * len(ids)) or 'NULL'})", ids
                )
            await conn.execute(
                """
                INSERT INTO mealie_recipe_nutrition_meta (id, base, synced_at) VALUES (1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET base = excluded.base, synced_at = excluded.synced_at
                """,
                (base or "", time.time()),
            )
            await conn.commit()
        _refresh["failedAt"] = 0.0
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
    fresh = meta is not None and meta["base"] == (base or "") and time.time() - meta["synced_at"] < INDEX_TTL
    task = refresh_running()
    if task is None and not fresh and time.time() - _refresh["failedAt"] >= RETRY_AFTER:
        task = start_refresh()
    if task is None:
        return None
    try:
        await asyncio.wait_for(asyncio.shield(task), wait)
    except asyncio.TimeoutError:
        return refresh_progress()
    return None


# --- zoeken -----------------------------------------------------------------------------------------------------


def kcal_bound(value: Any) -> int | None:
    """Een grens uit een verzoek: een heel getal van 0 tot en met `MAX_KCAL`, of None als er niets staat."""
    if value is None or isinstance(value, bool) or (isinstance(value, str) and not value.strip()):
        return None
    try:
        number = int(float(str(value).strip().replace(",", ".")))
    except (TypeError, ValueError):
        raise ValueError(tr("Geef de calorieën op als een getal.", "Give the calories as a number.")) from None
    return max(0, min(MAX_KCAL, number))


def kcal_range(low: Any, high: Any) -> tuple[int | None, int | None]:
    """De grenzen van een zoekopdracht; staat de ondergrens boven de bovengrens, dan draaien we ze om."""
    minimum, maximum = kcal_bound(low), kcal_bound(high)
    if minimum is not None and maximum is not None and minimum > maximum:
        minimum, maximum = maximum, minimum
    return minimum, maximum


async def search(
    query: Any, kcal_min: int | None, kcal_max: int | None, page: Any = 1, per_page: int = mealie.SEARCH_PAGE,
    wait: float = SEARCH_WAIT,
) -> dict[str, Any]:
    """Je recepten in Mealie met een kcal per portie tussen `kcal_min` en `kcal_max` (elk mag None zijn).

    Met een zoekterm bepaalt Mealie welke recepten passen en in welke volgorde; zonder zoekterm komen alle recepten uit de
    cache, op naam. Een recept zonder bekende kcal valt weg en telt mee in `withoutKcal`. Loopt de eerste ronde nog, dan staat
    de voortgang in `indexing` (`{done, total}`) en komt wat er al bekend is.
    """
    await mealie.ensure_configured()
    try:
        number = max(1, int(page or 1))
    except (TypeError, ValueError):
        number = 1
    size = max(1, min(100, int(per_page)))
    indexing = await _fresh(wait)
    async with _connect() as conn:
        rows = await conn.execute_fetchall("SELECT slug, name, kcal, card FROM mealie_recipe_nutrition")
    if not rows and indexing is None and _refresh["error"]:
        raise RuntimeError(_refresh["error"])
    known = {row["slug"]: row for row in rows}
    text = " ".join(str(query or "").split())[:200]
    if text:
        found = await mealie.list_recipes(MAX_RECIPES, search=text)
        candidates = [(mealie.recipe_card(summary), (known.get(summary["slug"]) or {"kcal": None})["kcal"]) for summary in found]
    else:
        candidates = [(json.loads(row["card"]), row["kcal"]) for row in sorted(rows, key=lambda row: _sort_name(row["name"]))]

    def fits(kcal: float | None) -> bool:
        return kcal is not None and (kcal_min is None or kcal >= kcal_min) and (kcal_max is None or kcal <= kcal_max)

    matches = [{**card, "kcal": round(kcal)} for card, kcal in candidates if kcal is not None and fits(kcal)]
    start = (number - 1) * size
    return {
        "recipes": matches[start:start + size],
        "total": len(matches),
        "page": number,
        "more": start + size < len(matches),
        "withoutKcal": sum(1 for _, kcal in candidates if kcal is None),
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
                    _finish("stopped", tr("AH weigert even verzoeken; probeer het later opnieuw.", "AH refuses requests for a while; try again later."))
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
