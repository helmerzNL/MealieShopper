"""Recepten van Picnic: de lijsten (opgeslagen, kookboek, opnieuw bestellen), één recept met ingrediënten en
bereiding, en het omzetten naar een schema.org Recipe zodat Mealie het kan importeren.

Picnic levert een scherm als pagina: een boom van onderdelen met `id`, `analytics` (data over het onderdeel)
en `pml` (de opmaak, met `RICH_TEXT`-onderdelen voor de tekst en `IMAGE`-onderdelen). We lezen de pagina's
zoals de community-library `picnic-api` (MRVDH) dat doet; de namen van de onderdelen komen daaruit en uit
het rapport van de verkenner.
"""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote

from . import picnic
from .i18n import tr

SOURCES = {
    "saved": "saved-deep-dive-page-content",  # Koken > Bewaard: de recepten die je hebt opgeslagen
    "cookbook": "cookbook-page-content",  # het kookboek met alle recepten van Picnic
    "ordered": "meals-purchase-page-root",  # Opnieuw bestellen: recepten die je eerder bestelde
}
DETAILS_PAGE = "selling-group-details-page"
SOURCE_URL = "https://picnic.app/"
MAX_ID_LENGTH = 100

_COLOR_TAG = re.compile(r"#\([^)]*\)")
_STEP_HEADER = re.compile(r"^(?:stap|step|schritt|étape)\s*\d+\s*:?$", re.I)
_RECIPE_ID = re.compile(r"^[A-Za-z0-9._:-]+$")
_HOURS = re.compile(r"(\d+)\s*(?:uur|u|h|hours?|std)\b", re.I)
_MINUTES = re.compile(r"(\d+)\s*(?:min\w*|m)\b", re.I)
_TIME_LIKE = re.compile(r"^[\d\s:.,/-]*(?:uur|u|h|min\w*|m)\b[\w\s.,/-]*$", re.I)
_NEEDED = re.compile(r"nodig|needed|benötigt|nécessaire", re.I)


def clean(markdown: Any) -> str:
    """Tekst uit een `markdown`-veld van Picnic: zonder kleurcodes (`#(...)`), vet/cursief en harde spaties."""
    text = _COLOR_TAG.sub("", str(markdown or "")).replace("**", "").replace("__", "").replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def parse_minutes(text: Any) -> int | None:
    """`20 min` -> 20, `1 uur 10 min` -> 70, `45` zonder eenheid -> None."""
    text = str(text or "")
    hours, minutes = _HOURS.search(text), _MINUTES.search(text)
    if not hours and not minutes:
        return None
    return (int(hours.group(1)) * 60 if hours else 0) + (int(minutes.group(1)) if minutes else 0)


def _time_like(text: str) -> bool:
    return len(text) <= 24 and bool(_TIME_LIKE.match(text)) and parse_minutes(text) is not None


def _texts(node: Any) -> list[str]:
    """Alle zichtbare tekst onder een onderdeel, in paginavolgorde."""
    found = [clean(d.get("markdown")) for d in picnic._walk(node) if isinstance(d.get("markdown"), str)]
    return [text for text in found if text]


def _text_nodes(node: Any) -> list[tuple[str, str]]:
    """(soort tekst, tekst) per tekstonderdeel, zoals `SUBTITLE1`; de soort bepaalt wat het is."""
    nodes = [d for d in picnic._walk(node) if isinstance(d.get("markdown"), str)]
    return [(str(d.get("textType") or ""), clean(d["markdown"])) for d in nodes if clean(d["markdown"])]


def _image_id(node: Any) -> str | None:
    """De eerste afbeelding onder een onderdeel (`IMAGE` met `source.id`)."""
    for d in picnic._walk(node):
        if d.get("type") == "IMAGE":
            source = d.get("source")
            image_id = source.get("id") if isinstance(source, dict) else source
            if isinstance(image_id, str) and image_id:
                return image_id
    return None


def _find(page: Any, node_id: str) -> dict[str, Any] | None:
    return next((d for d in picnic._walk(page) if d.get("id") == node_id), None)


# --- lijsten ------------------------------------------------------------------------------------


def summaries(page: Any) -> list[dict[str, Any]]:
    """De recepten op een lijstpagina: id, naam, afbeelding, kooktijd en labels. Geen dubbelen, in paginavolgorde."""
    seen: set[str] = set()
    recipes: list[dict[str, Any]] = []
    for node in picnic._walk(page):
        if not isinstance(node.get("pml"), dict):
            continue  # een tegel heeft opmaak; de analysegegevens van de pagina zelf niet
        for data in picnic.analytics_data(node):
            recipe_id = data.get("recipe_id")
            if not isinstance(recipe_id, str) or not recipe_id or recipe_id in seen:
                continue
            seen.add(recipe_id)
            texts = _texts(node["pml"])
            named = data.get("recipe_name")
            name = named.strip() if isinstance(named, str) and named.strip() else next((t for t in texts if not _time_like(t)), "")
            if not name and isinstance(data.get("name"), str):
                name = data["name"].strip()
            tags = [t for t in texts if t != name]
            minutes = next((parse_minutes(t) for t in tags if _time_like(t)), None)
            segment = data.get("segment_name")
            recipes.append(
                {
                    "id": recipe_id,
                    "name": name,
                    "image": picnic.image_url(_image_id(node["pml"])),
                    "minutes": minutes,
                    "tags": [t for t in tags if not _time_like(t)][:4],
                    "segment": segment if isinstance(segment, str) and segment else None,
                }
            )
    return [recipe for recipe in recipes if recipe["name"]]


def get_recipes(source: Any = "saved") -> list[dict[str, Any]]:
    """Recepten van Picnic: `saved` (Koken > Bewaard), `cookbook` of `ordered` (Opnieuw bestellen)."""
    source = str(source or "saved").strip().lower()
    if source not in SOURCES:
        raise ValueError(
            tr(
                "Onbekende bron voor recepten (kies saved, cookbook of ordered)",
                "Unknown recipe source (choose saved, cookbook or ordered)",
            )
        )
    page = picnic._authed("GET", f"/pages/{SOURCES[source]}", text=True)
    if not isinstance(page, dict):
        raise RuntimeError(tr("Picnic gaf de receptenpagina in een onbekend formaat", "Picnic returned the recipes page in an unknown format"))
    return summaries(page)


# --- één recept ---------------------------------------------------------------------------------


def _ingredient_tiles(page: Any) -> dict[str, dict[str, Any]]:
    """Per ingrediënt-id wat de tegel in de pagina laat zien: naam, benodigde hoeveelheid en verpakking."""
    tiles: dict[str, dict[str, Any]] = {}
    for node in picnic._walk(page):
        if node.get("type") != "PML" or not isinstance(node.get("id"), str) or "selling-unit-tile" not in node["id"]:
            continue
        ingredient_id = None
        for data in picnic.analytics_data(node):
            units = data.get("selling_units")
            if isinstance(units, list) and len(units) == 1 and isinstance(units[0], dict):
                ingredient_id = units[0].get("ingredient_id")
            ingredient_id = ingredient_id or data.get("ingredient_id")
        if not isinstance(ingredient_id, str):
            found = re.search(r"ingredient_id=([a-f0-9-]+)&", json.dumps(node))
            ingredient_id = found.group(1) if found else None
        if not ingredient_id:
            continue
        texts = _text_nodes(node)
        name = next((text for kind, text in texts if kind == "SUBTITLE1"), None)
        others = [text for _, text in texts if text != name]
        needed = next((text for text in others if _NEEDED.search(text)), None)
        size = next((text for text in others if text != needed and re.search(r"\d", text)), None)
        tiles[ingredient_id] = {"name": name, "needed": needed, "unitSize": size}
    return tiles


def details(page: Any, recipe_id: str) -> dict[str, Any]:
    """Een recept uit zijn detailpagina: naam, afbeelding, porties, tijden, ingrediënten en bereidingsstappen."""
    context = next(
        (
            d
            for d in picnic._walk(page)
            if d.get("recipe_id") == recipe_id and isinstance(d.get("selling_units"), list) and isinstance(d.get("recipe_name"), str)
        ),
        None,
    )
    if context is None:
        raise RuntimeError(tr("Picnic kent dit recept niet (meer)", "Picnic doesn't know this recipe (any more)"))
    tiles = _ingredient_tiles(page)
    ingredients = []
    for unit in context["selling_units"]:
        if not isinstance(unit, dict):
            continue
        tile = tiles.get(unit.get("ingredient_id"), {})
        ingredients.append(
            {
                "ingredientId": unit.get("ingredient_id"),
                "productId": unit.get("selling_unit_id") or None,
                "name": tile.get("name"),
                "quantity": unit["quantity"] if isinstance(unit.get("quantity"), int) and unit["quantity"] > 0 else 1,
                "needed": tile.get("needed"),
                "unitSize": tile.get("unitSize"),
            }
        )
    for ingredient in ingredients:
        ingredient["line"] = ingredient_line(ingredient)
    times_texts = _texts(_find(page, "selling-group-time-and-price-section"))
    times = [{"value": times_texts[i], "label": times_texts[i + 1]} for i in range(0, len(times_texts) - 1, 2)]
    steps_portions = next(iter(_texts(_find(page, "instructions-portions-section"))), "")
    portions = context.get("portions")
    return {
        "id": recipe_id,
        "name": context["recipe_name"].strip(),
        "image": picnic.image_url(_image_id(_find(page, "selling-group-details-image")), "large"),
        "portions": portions if isinstance(portions, int) and portions > 0 else None,
        "stepsPortions": int(m.group()) if (m := re.search(r"\d+", steps_portions)) else None,
        "minutes": next((parse_minutes(t["value"]) for t in times if parse_minutes(t["value"]) is not None), None),
        "times": times,
        "ingredients": ingredients,
        "steps": [text for text in _texts(_find(page, "instructions-section")) if not _STEP_HEADER.match(text)],
    }


def get_recipe(recipe_id: Any) -> dict[str, Any]:
    recipe_id = str(recipe_id or "").strip()
    if not recipe_id or len(recipe_id) > MAX_ID_LENGTH or not _RECIPE_ID.match(recipe_id):
        raise ValueError(tr("Geen geldig recept", "Not a valid recipe"))
    page = picnic._authed("GET", f"/pages/{DETAILS_PAGE}?selling_group_id={quote(recipe_id, safe='')}", text=True)
    if not isinstance(page, dict):
        raise RuntimeError(tr("Picnic gaf het recept in een onbekend formaat", "Picnic returned the recipe in an unknown format"))
    return details(page, recipe_id)


# --- naar Mealie --------------------------------------------------------------------------------


def ingredient_line(ingredient: dict[str, Any]) -> str:
    """Eén regel voor de ingrediëntenlijst: `150 g Rode uien` (de hoeveelheid die het recept nodig heeft), anders de verpakking."""
    product_id = ingredient.get("productId")
    name = ingredient.get("name") or (f"Product {product_id}" if product_id else tr("Onbekend product", "Unknown product"))
    needed = re.sub(r"[()]|\b(?:nodig|needed|benötigt)\b", "", ingredient.get("needed") or "", flags=re.I).strip()
    if needed:
        return f"{needed} {name}"
    if ingredient.get("unitSize"):
        return f"{name} ({ingredient['unitSize']})"
    return str(name)


def jsonld(recipe: dict[str, Any]) -> dict[str, Any]:
    """Het recept als schema.org Recipe; Mealie importeert dat zonder een webpagina op te halen."""
    lines = [ingredient_line(i) for i in recipe["ingredients"]]
    steps = [{"@type": "HowToStep", "text": text} for text in recipe["steps"]]
    if not lines and not steps:
        raise RuntimeError(tr("Dit Picnic-recept heeft geen ingrediënten of bereiding", "This Picnic recipe has no ingredients or steps"))
    data: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "Recipe",
        "name": recipe["name"],
        "recipeIngredient": lines,
        "recipeInstructions": steps,
    }
    if recipe.get("image"):
        data["image"] = [recipe["image"]]
    if recipe.get("portions"):
        data["recipeYield"] = str(recipe["portions"])
    if recipe.get("minutes"):
        data["totalTime"] = f"PT{recipe['minutes']}M"
    return data


def source_url(recipe_id: str) -> str:
    """Mealie bewaart waar een recept vandaan komt; Picnic-recepten hebben geen webpagina, dus de site van Picnic."""
    return f"{SOURCE_URL}#recipe-{recipe_id}"
