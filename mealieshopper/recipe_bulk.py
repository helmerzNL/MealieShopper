"""Recepten van Albert Heijn, Jumbo en Crisp in één keer in Mealie zetten: een selectie, of een hele receptgroep of -lijst
(bij Crisp: al je bewaarde recepten).

Er loopt hooguit één import tegelijk, op de achtergrond (zoals de AI-ronde in `ai_match.py`). De stand staat in het
geheugen van dit proces: de container draait één hypercorn-worker. Met meer workers ziet een andere worker de voortgang
niet, en na een herstart is de import weg. Opnieuw starten is veilig: een recept dat al in Mealie staat (zelfde bron)
wordt overgeslagen en telt niet mee voor het maximum, dus een nieuwe import gaat verder waar de vorige stopte.

Per recept zoeken we eerst in Mealie op de bron. Alleen als het er nog niet staat, halen we het bij de winkel op en
zetten we het als schema.org-recept in Mealie, net als bij het importeren van één recept. Elk recept krijgt de tag met
de naam van de winkel, en uit een groep of lijst ook de categorie met die naam (niet uit *Alle recepten*, de
Jumbo-Favorieten of de bewaarde Crisp-recepten: dat is geen eigen indeling). Rustig voor de winkels: anderhalve seconde tussen twee recepten, en bij
een weigering (403 of 429) stopt de import.

Fouten staan als code in de voortgang; de browser vertaalt ze. De tekst van de winkel of van Mealie komt alleen in het
logboek, nooit bij de gebruiker.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from . import ah, ah_recipes, audit, crisp, jumbo, jumbo_recipes, mealie, tasks
from .i18n import tr

log = logging.getLogger(__name__)

STORES = {"ah": "Albert Heijn", "jumbo": "Jumbo", "crisp": "Crisp"}
MAX_SELECTION = 50  # een selectie mag iedereen; meer in één keer is een hele groep, en die is voor de beheerder
MAX_IMPORTS = 250  # zoveel recepten per import halen we bij de winkel op; wat al in Mealie staat telt niet mee
MAX_ITEMS = 1000  # zoveel recepten bekijken we hooguit per import
PAUSE_SECONDS = 1.5  # tussen twee aanroepen bij de winkel
MAX_FAILURES_IN_A_ROW = 5  # daarna stopt de import: dan is er iets mis met de winkel of Mealie
MAX_TITLE = 200
_LIST_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_CRISP_REFUSED = re.compile(r"^(?:403|429):")  # zo begint de fout van crisp._call bij een weigering (403 wordt meestal CrispAuthError)
FINAL = ("imported", "existing", "failed", "skipped")


class Busy(ValueError):
    """Er loopt al een import."""


_job: dict[str, Any] = {"state": "idle"}
_cancel: asyncio.Event = asyncio.Event()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- de stand -----------------------------------------------------------------------------------------

def progress() -> dict[str, Any]:
    """De stand van de lopende (of laatste) import, met de tellingen erbij. Een kopie: de task werkt het origineel bij."""
    job = dict(_job)
    items = [{key: value for key, value in item.items() if not key.startswith("_")} for item in _job.get("items") or []]
    if job.get("state") == "idle" and not items:
        return {"state": "idle", "limits": limits()}
    counts = {status: 0 for status in FINAL}
    remaining = 0
    for item in items:
        if item["status"] in counts:
            counts[item["status"]] += 1
        if item["status"] == "skipped" and item.get("error") == "limit":
            remaining += 1
    job.pop("startedById", None)
    return {
        **job,
        "items": items,
        "total": len(items),
        "done": sum(counts.values()),
        **counts,
        "remaining": remaining,
        "warnings": list(job.get("warnings") or []),
        "limits": limits(),
    }


def limits() -> dict[str, int]:
    return {"selection": MAX_SELECTION, "imports": MAX_IMPORTS, "items": MAX_ITEMS}


def _set(**values: Any) -> None:
    _job.update(values)


def _set_item(index: int, **values: Any) -> None:
    _job["items"][index].update(values)


def _add_items(items: list[dict[str, Any]]) -> None:
    _job["items"].extend(_new_item(item) for item in items)


def _warn(code: str) -> None:
    warnings = _job.setdefault("warnings", [])
    if code not in warnings:
        warnings.append(code)


def _skip_rest(start: int, code: str) -> None:
    """Zet de recepten vanaf `start` die nog niet aan de beurt waren op overgeslagen."""
    for item in _job["items"][start:]:
        if item["status"] not in FINAL:
            item.update(status="skipped", error=code)


def _finish(state: str, error: str = "") -> None:
    _set(state=state, error=error, finishedAt=_now(), phase="")


def _new_item(item: dict[str, Any]) -> dict[str, Any]:
    return {"id": item["id"], "title": item.get("title") or "", "status": "pending", "slug": "", "mealieUrl": "", "error": "", "_source": item["source"]}


# --- wat er geïmporteerd wordt ------------------------------------------------------------------------

def _title(value: Any) -> str:
    return " ".join(str(value or "").split())[:MAX_TITLE]


def _ids(raw: Any) -> list[str]:
    """De gekozen recepten, zonder dubbele en in de volgorde van de keuze."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(tr("Geef de recepten als lijst", "Give the recipes as a list"))
    ids: list[str] = []
    for value in raw:
        text = str(value if not isinstance(value, dict) else value.get("id") or "").strip()
        if text and text not in ids:
            ids.append(text)
    return ids


async def _ah_plan(ids: list[str], group_id: str, whole: bool) -> dict[str, Any]:
    data = await ah_recipes.load()
    if not data or not data.get("recipes"):
        raise ValueError(tr(
            "Haal eerst je bewaarde recepten op bij AH (Winkels > Albert Heijn > Recepten).",
            "Fetch your saved recipes from AH first (Stores > Albert Heijn > Recipes).",
        ))
    group = ah_recipes.group_by_id(data.get("groups") or [], group_id) if group_id else None
    if group_id and not group:
        raise ValueError(tr("Deze receptgroep bestaat niet (meer). Haal je recepten opnieuw op.", "This recipe group no longer exists. Fetch your recipes again."))
    by_key = {str(recipe["id"]): recipe for recipe in data["recipes"]}
    by_number = {}
    for recipe in data["recipes"]:
        number = ah_recipes.recipe_number(recipe)
        if number:
            by_number.setdefault(str(number), recipe)
    if whole:
        keys = group["recipeIds"] if group else [str(recipe["id"]) for recipe in data["recipes"]]
        chosen = [by_key[key] for key in keys if key in by_key]
    else:
        chosen = []
        for key in ids:
            recipe = by_number.get(key) or by_key.get(key)
            if not recipe:
                raise ValueError(tr("Een gekozen recept staat niet bij je bewaarde AH-recepten.", "A chosen recipe is not among your saved AH recipes."))
            chosen.append(recipe)
    items, seen = [], set()
    for recipe in chosen:
        number = ah_recipes.recipe_number(recipe)
        if not number or number in seen:
            continue
        seen.add(number)
        items.append({"id": str(number), "title": _title(recipe.get("title")), "source": ah.recipe_url(number)})
    return {
        "items": items[:MAX_ITEMS],
        "group": {"id": group["id"], "name": group["name"]} if group else None,
        "category": group["name"] if group else None,
        "groupSize": len(items),
    }


def _jumbo_plan(ids: list[str], titles: Any, group_id: str, whole: bool) -> dict[str, Any]:
    if not jumbo.configured_refresh_token():
        raise ValueError(tr(
            "Jumbo is niet gekoppeld. De beheerder koppelt het bij Instellingen > Koppelen > Jumbo.",
            "Jumbo is not linked. The admin links it under Settings > Link > Jumbo.",
        ))
    if group_id and not _LIST_ID.match(group_id):
        raise ValueError(tr("Onbekend lijstje", "Unknown list"))
    if whole and not group_id:
        raise ValueError(tr("Kies een receptenlijst", "Choose a recipe list"))
    names = titles if isinstance(titles, dict) else {}
    items = []
    for key in ids:
        recipe_id = jumbo_recipes.valid_id(key)
        items.append({"id": recipe_id, "title": _title(names.get(key)), "source": f"{jumbo.SITE_URL}/recepten/{recipe_id}"})
    # De naam van de lijst (en bij een hele lijst de recepten) haalt de import zelf op: dat zijn aanvragen bij Jumbo.
    return {"items": items, "group": {"id": group_id, "name": ""} if group_id else None, "category": None, "groupSize": len(items)}


def _crisp_plan(ids: list[str], titles: Any, group_id: str, whole: bool) -> dict[str, Any]:
    if not crisp.configured_client_id():
        raise ValueError(tr(
            "Crisp is niet gekoppeld. De beheerder koppelt het bij Instellingen > Koppelen > Crisp.",
            "Crisp is not linked. The admin links it under Settings > Link > Crisp.",
        ))
    if group_id:
        raise ValueError(tr("Crisp heeft geen receptgroepen", "Crisp has no recipe groups"))
    names = titles if isinstance(titles, dict) else {}
    items = []
    for key in ids:
        recipe_id = crisp.valid_recipe_id(key)
        items.append({"id": recipe_id, "title": _title(names.get(key)), "source": crisp.source_url(recipe_id)})
    # Bij al je bewaarde recepten haalt de import de lijst zelf op: dat is een aanvraag bij Crisp.
    return {"items": items, "group": None, "category": None, "groupSize": len(items)}


def _valid_store(store: Any) -> str:
    key = str(store or "").strip().lower()
    if key not in STORES:
        raise ValueError(tr("Kies Albert Heijn, Jumbo of Crisp", "Choose Albert Heijn, Jumbo or Crisp"))
    return key


def _on_cancelled(task: asyncio.Task[None]) -> None:
    """Een geannuleerde task (afsluiten van de app) laat de stand niet op "running" staan: dan weigert elke nieuwe import."""
    if not task.cancelled() or _job.get("state") != "running":
        return
    items: list[dict[str, Any]] = _job.get("items") or []
    first: int = next((i for i, item in enumerate(items) if item["status"] not in FINAL), len(items))
    _skip_rest(first, "cancelled")
    _finish("cancelled")


async def start(
    store: Any,
    ids: Any = None,
    group: Any = None,
    *,
    whole: bool = False,
    titles: Any = None,
    user: dict[str, Any] | None = None,
    background: bool = True,
) -> dict[str, Any]:
    """Start een import. `whole`: de hele groep of lijst `group` (bij AH zonder groep en bij Crisp: alle bewaarde recepten); anders
    de recepten `ids`, met `group` alleen voor de categorie. Of iemand een hele groep mag, bepaalt de route
    (`/api/mealie/bulk-import/group` is voor de beheerder). Er loopt hooguit één import; met `background=False` wacht
    deze aanroep tot de import klaar is. Doet zelf geen aanvragen bij de winkel of Mealie."""
    global _cancel
    store = _valid_store(store)
    try:
        await mealie.ensure_configured()
    except RuntimeError as exc:
        raise ValueError(str(exc)) from exc
    group_id = str(group or "").strip()
    chosen = [] if whole else _ids(ids)
    if not whole:
        if not chosen:
            raise ValueError(tr("Kies eerst een of meer recepten", "Choose one or more recipes first"))
        if len(chosen) > MAX_SELECTION:
            raise ValueError(tr(
                f"Kies hooguit {MAX_SELECTION} recepten tegelijk, of importeer de hele groep.",
                f"Choose at most {MAX_SELECTION} recipes at a time, or import the whole group.",
            ))
    if store == "ah":
        plan = await _ah_plan(chosen, group_id, whole)
    elif store == "jumbo":
        plan = _jumbo_plan(chosen, titles, group_id, whole)
    else:
        plan = _crisp_plan(chosen, titles, group_id, whole)
    plan.update(store=store, whole=bool(whole))
    if _job.get("state") == "running":
        raise Busy(tr("Er loopt al een import naar Mealie. Wacht tot die klaar is.", "An import into Mealie is already running. Wait until it is finished."))
    _cancel = asyncio.Event()
    _job.clear()
    _job.update(
        state="running",
        # Een Jumbo-lijst en al je bewaarde Crisp-recepten haalt de taak eerst bij de winkel op.
        phase="preparing" if (store == "jumbo" and (whole or group_id)) or (store == "crisp" and whole) else "importing",
        store=store,
        storeName=STORES[store],
        group=plan["group"],
        category=plan["category"],
        whole=bool(whole),
        groupSize=plan["groupSize"],
        items=[_new_item(item) for item in plan["items"]],
        warnings=[],
        error="",
        startedBy=(user or {}).get("username") or "",
        startedById=(user or {}).get("id") or "",
        startedAt=_now(),
        finishedAt="",
    )
    if background:
        tasks.spawn("recipe-bulk", _run(plan)).add_done_callback(_on_cancelled)
    else:
        await _run(plan)
    return progress()


def cancel(user: dict[str, Any] | None = None, owner: bool = False) -> dict[str, Any]:
    """Stop de lopende import na het recept waar hij mee bezig is. Mag wie hem startte, en de beheerder."""
    if _job.get("state") != "running":
        raise ValueError(tr("Er loopt geen import", "No import is running"))
    if not owner and _job.get("startedById") != (user or {}).get("id"):
        raise PermissionError(tr("Alleen wie de import startte of de beheerder kan hem stoppen.", "Only whoever started the import or the administrator can stop it."))
    _cancel.set()
    return progress()


# --- op de achtergrond --------------------------------------------------------------------------------

class _Stop(Exception):
    """Stop de hele import met deze code (de winkel weigert, Mealie weigert, te veel fouten)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _Organizers:
    """De tag van de winkel en de categorie van de groep in Mealie, één keer per import opgezocht of aangemaakt."""

    def __init__(self, store_name: str, category: str | None) -> None:
        self.store_name = store_name
        self.category = category
        self.tags: list[dict[str, str]] | None = None
        self.categories: list[dict[str, str]] = []

    async def _one(self, make: Callable[[str], Awaitable[dict[str, str]]], name: str) -> list[dict[str, str]]:
        try:
            return [await make(name)]
        except mealie.OrganizerError as exc:
            log.warning("Bulkimport: %s maken mislukt: %s", name, exc)
            _warn("organizers_denied" if exc.forbidden else "organizers_failed")
        except Exception as exc:  # noqa: BLE001 - zonder tag of categorie gaat de import gewoon door
            log.warning("Bulkimport: %s maken mislukt: %s", name, exc)
            _warn("organizers_failed")
        return []

    async def apply(self, slug: str, current: dict[str, Any] | None = None) -> None:
        if self.tags is None:
            self.tags = await self._one(mealie.ensure_tag, self.store_name)
            self.categories = await self._one(mealie.ensure_category, self.category) if self.category else []
        if not self.tags and not self.categories:
            return
        try:
            await mealie.add_organizers(slug, self.tags, self.categories, current)
        except Exception as exc:  # noqa: BLE001 - het recept staat in Mealie; alleen de tag ontbreekt
            log.warning("Bulkimport: tag en categorie bij %s zetten mislukt: %s", slug, exc)
            _warn("tagging_failed")


async def _pause() -> bool:
    """Wacht tussen twee aanroepen bij de winkel. Geeft True als de import intussen gestopt is."""
    if PAUSE_SECONDS <= 0:
        return _cancel.is_set()
    try:
        await asyncio.wait_for(_cancel.wait(), PAUSE_SECONDS)
    except asyncio.TimeoutError:
        return False
    return True


async def _jumbo_list(group: dict[str, Any]) -> dict[str, Any]:
    """De receptenlijst van Jumbo met dit id (`{id, name, kind, count}`)."""
    try:
        lists = await jumbo_recipes.get_lists()
    except jumbo.JumboBlocked as exc:
        raise _Stop("jumbo_blocked" if exc.refused else "list_failed") from exc
    except jumbo.JumboAuthError as exc:
        raise _Stop("jumbo_auth") from exc
    except Exception as exc:  # noqa: BLE001
        log.warning("Bulkimport: receptenlijsten van Jumbo ophalen mislukt: %s", exc)
        raise _Stop("list_failed") from exc
    found = next((item for item in lists if item["id"] == group["id"]), None)
    if not found:
        raise _Stop("list_unknown")
    return found


async def _collect_jumbo(info: dict[str, Any]) -> None:
    """Alle recepten van een Jumbo-lijst, pagina voor pagina (met een pauze), tot `MAX_ITEMS`."""
    seen: set[str] = set()
    page = 1
    while True:
        try:
            data = await jumbo_recipes.get_recipes_of(info, page)
        except jumbo.JumboBlocked as exc:
            raise _Stop("jumbo_blocked" if exc.refused else "list_failed") from exc
        except jumbo.JumboAuthError as exc:
            raise _Stop("jumbo_auth") from exc
        except Exception as exc:  # noqa: BLE001
            log.warning("Bulkimport: pagina %s van de Jumbo-lijst ophalen mislukt: %s", page, exc)
            raise _Stop("list_failed") from exc
        fresh = []
        for recipe in data.get("recipes") or []:
            recipe_id = str(recipe.get("id") or "")
            if not recipe_id or recipe_id in seen or len(seen) >= MAX_ITEMS:
                continue
            seen.add(recipe_id)
            fresh.append({"id": recipe_id, "title": _title(recipe.get("name")), "source": recipe.get("url") or f"{jumbo.SITE_URL}/recepten/{recipe_id}"})
        _add_items(fresh)
        total = data.get("total")
        _set(groupSize=max(total if isinstance(total, int) else 0, len(seen)))
        # Een pagina zonder nieuwe recepten: stop, anders bladert de import eindeloos (net als "Meer recepten").
        if len(seen) >= MAX_ITEMS or not data.get("hasNext") or not fresh:
            return
        page += 1
        if await _pause():
            return


def _collect_crisp() -> None:
    """Al je bewaarde Crisp-recepten, tot `MAX_ITEMS`. Crisp geeft de hele lijst in één antwoord."""
    try:
        recipes = crisp.get_saved_recipes()["recipes"]
    except Exception as exc:  # noqa: BLE001
        log.warning("Bulkimport: bewaarde recepten van Crisp ophalen mislukt: %s", exc)
        raise _Stop(_store_refusal("crisp", exc) or "crisp_saved_failed") from exc
    items = [{"id": recipe["id"], "title": _title(recipe.get("name")), "source": crisp.source_url(recipe["id"])} for recipe in recipes[:MAX_ITEMS]]
    _add_items(items)
    _set(groupSize=len(items))


async def _fetch(store: str, item: dict[str, Any]) -> tuple[str, dict[str, Any], str]:
    """Haal het recept bij de winkel op: (bron, schema.org-recept, titel)."""
    if store == "ah":
        number = int(item["id"])
        ld = ah.get_recipe_jsonld(number)
        return ah.recipe_url(number), ld, str(ld.get("name") or "")
    if store == "crisp":
        recipe = crisp.get_recipe(item["id"])
        return crisp.source_url(item["id"]), crisp.jsonld(recipe), str(recipe.get("name") or "")
    recipe = await jumbo_recipes.get_recipe(item["id"])
    return jumbo_recipes.source_url(recipe), jumbo_recipes.jsonld(recipe), str(recipe.get("name") or "")


def _store_refusal(store: str, exc: Exception) -> str | None:
    """De code om de hele import te stoppen als de winkel ons weert of de koppeling stuk is, anders None."""
    if store == "ah" and isinstance(exc, ah.AhHttpError) and exc.status in (403, 429):
        return "ah_blocked"
    if store == "jumbo" and isinstance(exc, jumbo.JumboBlocked) and exc.refused:
        return "jumbo_blocked"
    if store == "jumbo" and isinstance(exc, jumbo.JumboAuthError):
        return "jumbo_auth"
    if store == "crisp" and isinstance(exc, crisp.CrispAuthError):
        return "crisp_auth"
    if store == "crisp" and _CRISP_REFUSED.match(str(exc)):
        return "crisp_blocked"
    return None


async def _import_all(plan: dict[str, Any]) -> None:
    store = plan["store"]
    category = plan["category"]
    if store == "jumbo" and plan["group"]:
        info = await _jumbo_list(plan["group"])
        if info.get("kind") != "favorites":
            category = info["name"]
        _set(group={"id": info["id"], "name": info["name"]}, category=category)
        if plan["whole"]:
            await _collect_jumbo(info)
            if _cancel.is_set():
                _finish("cancelled")
                return
    if store == "crisp" and plan["whole"]:
        _collect_crisp()
    _set(phase="importing")
    organizers = _Organizers(STORES[store], category)
    catalog = mealie.Catalog()
    items = [dict(item) for item in _job["items"]]
    fetched = 0
    failures = 0
    for index, item in enumerate(items):
        if _cancel.is_set():
            _skip_rest(index, "cancelled")
            _finish("cancelled")
            return
        _set_item(index, status="busy")
        try:
            found = await mealie.find_recipe_summary_by_source(item["_source"], strict=True)
        except mealie.MealieLookupError as exc:
            log.warning("Bulkimport: zoeken in Mealie mislukt: %s", exc)
            _skip_rest(index, "stopped")
            raise _Stop("mealie_lookup") from exc
        if found:
            slug = str(found["slug"])
            await organizers.apply(slug, found)
            _set_item(index, status="existing", slug=slug, mealieUrl=await mealie.recipe_page_url(slug))
            continue
        if fetched >= MAX_IMPORTS:
            _set_item(index, status="skipped", error="limit")
            continue
        if fetched and await _pause():
            _skip_rest(index, "cancelled")
            _finish("cancelled")
            return
        fetched += 1
        stage = "store_failed"
        try:
            source, recipe, title = await _fetch(store, item)
            stage = "mealie_failed"
            slug = await mealie.import_via_jsonld(source, recipe, catalog=catalog)
        except (ah.RecipeUnavailable, jumbo_recipes.RecipeUnavailable, crisp.RecipeUnavailable) as exc:
            log.info("Bulkimport: %s-recept %s niet te importeren: %s", store, item["id"], exc)
            _set_item(index, status="failed", error="no_content")
            continue  # dit recept zelf: telt niet als fout van de winkel
        except Exception as exc:  # noqa: BLE001 - per recept; de tekst gaat alleen naar het logboek
            log.warning("Bulkimport: %s-recept %s mislukt: %s", store, item["id"], exc)
            _set_item(index, status="failed", error=stage)
            refusal = _store_refusal(store, exc)
            if refusal:
                _skip_rest(index + 1, "stopped")
                raise _Stop(refusal) from exc
            if isinstance(exc, mealie.MealieImportError) and exc.status_code in (401, 403):
                _skip_rest(index + 1, "stopped")
                raise _Stop("mealie_denied") from exc
            failures += 1
            if failures >= MAX_FAILURES_IN_A_ROW:
                _skip_rest(index + 1, "stopped")
                raise _Stop("too_many_failures") from exc
            continue
        failures = 0
        await organizers.apply(slug)
        _set_item(index, status="imported", slug=slug, mealieUrl=await mealie.recipe_page_url(slug), title=_title(title) or item["title"])
    _finish("done")


async def _run(plan: dict[str, Any]) -> None:
    started = time.perf_counter()
    try:
        await _import_all(plan)
    except _Stop as stop:
        _finish("error", stop.code)
    except Exception:  # noqa: BLE001 - een taak mag nooit stil stoppen zonder melding
        log.exception("Bulkimport mislukt")
        start = next((i for i, item in enumerate(_job["items"]) if item["status"] not in FINAL), len(_job["items"]))
        _skip_rest(start, "stopped")
        _finish("error", "internal")
    finally:
        _record(plan, started)


def _record(plan: dict[str, Any], started: float) -> None:
    """Eén regel in het auditlog per import, met de aantallen en de mislukte recepten."""
    job = progress()
    failures = [{"id": item["id"], "error": item["error"]} for item in job.get("items") or [] if item["status"] == "failed"]
    audit.record(
        kind="outbound",
        service=plan["store"],
        action="Mealie bulk import",
        method="POST",
        ok=job.get("state") == "done",
        duration_ms=round((time.perf_counter() - started) * 1000),
        actor=audit.current_actor(),
        error=job.get("error") or "",
        detail={
            "scope": "group" if plan["whole"] else "selection",
            "state": job.get("state"),
            "total": job.get("total"),
            "imported": job.get("imported"),
            "existing": job.get("existing"),
            "failed": job.get("failed"),
            "skipped": job.get("skipped"),
            "remaining": job.get("remaining"),
            "warnings": job.get("warnings"),
            "failures": failures[:50],
        },
    )
