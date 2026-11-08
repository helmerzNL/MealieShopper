"""De weekplanner van de AH-app in MealieShopper: tonen, en de recepten ervan in het weekmenu van Mealie zetten.

Bij AH wordt alleen gelezen (`ah.get_week_planner`); plannen doe je in de AH-app. *Naar weekmenu* zet de
Allerhande-recepten op dezelfde dag in het weekmenu van Mealie, als diner (AH kent geen soort maaltijd). Een recept dat
nog niet in Mealie staat, wordt eerst geïmporteerd; een recept dat er al staat (zelfde receptnummer als bron) wordt
hergebruikt. Eigen recepten van het lid, producten en notities gaan niet mee: dat besloot de eigenaar.

Er komt alleen iets bij in Mealie. Staat het recept op die dag al in het weekmenu, dan blijft het bij die ene keer, zodat
twee keer op de knop drukken niets dubbel zet. Wat al in het weekmenu staat, blijft staan. MealieShopper bewaart zelf geen
planning (zie docs/onderzoek-weekmenu-plannen.md en docs/onderzoek-ah-weekplanner.md).
"""
from __future__ import annotations

import datetime
import logging
from typing import Any

from . import ah, mealie
from .i18n import tr

log = logging.getLogger(__name__)

MAX_ITEMS = 20  # per verzoek; het scherm stuurt een dag per keer, zodat een verzoek binnen de 120 seconden blijft
ENTRY_TYPE = "dinner"

PLANNED = "planned"
ALREADY_PLANNED = "already_planned"
FAILED = "failed"


async def get() -> dict[str, Any]:
    """De weekplanner van de AH-app, zoals `ah.get_week_planner` hem geeft."""
    return await ah.get_week_planner()


def _date(value: Any) -> str:
    try:
        return datetime.date.fromisoformat(str(value or "").strip()).isoformat()
    except ValueError:
        raise ValueError(tr("Kies een geldige datum (JJJJ-MM-DD).", "Choose a valid date (YYYY-MM-DD).")) from None


def _recipe_id(value: Any) -> int:
    text = str(value if value is not None else "").strip()
    if not text.isdigit() or len(text) > 12 or int(text) <= 0:
        raise ValueError(tr("Ongeldig receptnummer van AH.", "Invalid AH recipe number."))
    return int(text)


def clean_items(body: Any) -> list[dict[str, Any]]:
    """De recepten om in het weekmenu te zetten, uit `{items: [{date, recipeId}]}`: gecontroleerd en zonder dubbele."""
    items = body.get("items") if isinstance(body, dict) else None
    if not isinstance(items, list) or not items:
        raise ValueError(tr("Geen recepten om in het weekmenu te zetten.", "No recipes to put in the week menu."))
    if len(items) > MAX_ITEMS:
        raise ValueError(
            tr(f"Te veel recepten in één keer (hooguit {MAX_ITEMS}).", f"Too many recipes at once (at most {MAX_ITEMS}).")
        )
    cleaned: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError(tr("Ongeldige invoer.", "Invalid input."))
        key = (_date(item.get("date")), _recipe_id(item.get("recipeId")))
        if key not in seen:
            seen.add(key)
            cleaned.append({"date": key[0], "recipeId": key[1]})
    return cleaned


def _planned(entries: list[dict[str, Any]]) -> set[tuple[str, str]]:
    """Wat al in het weekmenu staat: `(datum, slug)` en `(datum, recept-id)` per maaltijd met een recept."""
    found: set[tuple[str, str]] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        day = str(entry.get("date") or "")[:10]
        recipe = entry.get("recipe") if isinstance(entry.get("recipe"), dict) else {}
        for value in (recipe.get("slug"), recipe.get("id"), entry.get("recipeId")):
            if value:
                found.add((day, str(value)))
    return found


async def _recipe_in_mealie(recipe_id: int, known: dict[int, dict[str, Any]]) -> dict[str, Any]:
    """Het recept in Mealie, `{slug, id, imported}`: uit dit verzoek, al in Mealie (zelfde bron) of net geïmporteerd."""
    if recipe_id in known:
        return {**known[recipe_id], "imported": False}
    url = ah.recipe_url(recipe_id)
    summary = await mealie.find_recipe_summary_by_source(url)
    if summary:
        found = {"slug": str(summary["slug"]), "id": str(summary.get("id") or ""), "imported": False}
    else:
        found = {"slug": await mealie.import_from_url(url), "id": "", "imported": True}
    known[recipe_id] = {"slug": found["slug"], "id": found["id"]}
    return found


async def to_meal_plan(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Zet deze Allerhande-recepten (`clean_items`) in het weekmenu van Mealie. Een recept dat mislukt, houdt de rest niet op.

    Per recept `{date, recipeId, status, imported, slug, mealieUrl}`; `status` is `planned`, `already_planned` of `failed`
    (dan met `error`, een algemene tekst: het antwoord van AH of Mealie staat alleen in het logboek).
    """
    await mealie.ensure_configured()
    if not items:
        return {"results": []}
    dates = sorted({item["date"] for item in items})
    planned = _planned(await mealie.get_meal_plan(dates[0], dates[-1]))
    known: dict[int, dict[str, Any]] = {}
    results: list[dict[str, Any]] = []
    for item in items:
        result: dict[str, Any] = {"date": item["date"], "recipeId": item["recipeId"], "imported": False, "slug": "", "mealieUrl": ""}
        try:
            recipe = await _recipe_in_mealie(item["recipeId"], known)
            result.update(imported=recipe["imported"], slug=recipe["slug"], mealieUrl=await mealie.recipe_page_url(recipe["slug"]))
            if {(item["date"], recipe["slug"]), (item["date"], recipe["id"])} & planned:
                result["status"] = ALREADY_PLANNED
            else:
                await mealie.create_meal_plan_entry({"date": item["date"], "entryType": ENTRY_TYPE, "recipeSlug": recipe["slug"]})
                planned.add((item["date"], recipe["slug"]))
                result["status"] = PLANNED
        except Exception:  # noqa: BLE001 - AH of Mealie: alleen in het logboek, de volgende recepten gaan door
            log.exception("AH-weekplanner: recept %s op %s in het weekmenu zetten mislukt", item["recipeId"], item["date"])
            result["status"] = FAILED
            result["error"] = (
                tr("In Mealie zetten mislukt", "Putting it in Mealie failed")
                if result["slug"]
                else tr("Importeren in Mealie mislukt", "Importing into Mealie failed")
            )
        results.append(result)
    return {"results": results}
