"""Recepten van Jumbo (jumbo.com/recepten): zoeken, je receptenlijsten, één recept met ingrediënten en
bereiding, en het omzetten naar een schema.org Recipe zodat Mealie het kan importeren.

De site vraagt alles met opgeslagen queries (hash en variabelen, geen querytekst); dezelfde aanvragen sturen wij
via `jumbo._persisted_query`. Een recept (`getRecipe`) kennen we uit een echt antwoord. De lijsten (favorieten en
zoekresultaten) lezen we aan hun vorm: een receptkaart is een object met een `id`, een `title` en een `link` naar
`/recepten/...`, waar ook in het antwoord het staat. Zo breekt een andere nesting van Jumbo er niets aan af.
"""
from __future__ import annotations

import re
from typing import Any

from . import jumbo
from .i18n import tr

RECIPE_PAGE = 24  # zoveel recepten per zoekpagina, zoals de website
MAX_RECIPE_LISTS = 24
MAX_ID_LENGTH = 30
# Een receptnummer is meestal een getal (`504898`), bij recepten van Smulweb en anderen `1416890-7`.
_RECIPE_ID = re.compile(r"^[0-9A-Za-z_-]{1,%d}$" % MAX_ID_LENGTH)
_URL_ID = re.compile(r"^https?://(?:www\.)?jumbo\.com/recepten/[^?#/]*?-(\d{1,20}(?:-\d{1,3})?)/?(?:[?#].*)?$", re.I)
_NUTRITION = {
    "energie": "calories",
    "koolhydraten": "carbohydrateContent",
    "eiwitten": "proteinContent",
    "eiwit": "proteinContent",
    "vet": "fatContent",
    "verzadigd vet": "saturatedFatContent",
    "vezels": "fiberContent",
    "suikers": "sugarContent",
    "zout": "sodiumContent",
}


def recipe_id_from_url(url: Any) -> str | None:
    """Het receptnummer uit een jumbo.com-receptlink (`.../recepten/romige-pasta-504898`), anders None."""
    found = _URL_ID.match(str(url or "").strip())
    return found.group(1) if found else None


def _valid_id(recipe_id: Any) -> str:
    recipe_id = str(recipe_id or "").strip()
    if not _RECIPE_ID.match(recipe_id):
        raise ValueError(tr("Geen geldig recept", "Not a valid recipe"))
    return recipe_id


def _site(path: Any) -> str | None:
    return f"{jumbo.SITE_URL}{path}" if isinstance(path, str) and path.startswith("/") else None


def _image(raw: dict[str, Any]) -> str | None:
    for key in ("fullImageUrl", "imageUrl", "image"):
        value = raw.get(key)
        if isinstance(value, dict):
            value = value.get("url") or value.get("src")
        if isinstance(value, str) and value.startswith("http"):
            return value
    return None


def _minutes(raw: Any) -> int | None:
    return raw if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0 else None


def _walk(node: Any):
    """Alle objecten onder `node`, zo diep als het gaat."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


def _is_recipe_card(node: dict[str, Any]) -> bool:
    link = node.get("link")
    return bool(node.get("id")) and isinstance(node.get("title"), str) and isinstance(link, str) and link.startswith("/recepten/")


def summaries(data: Any) -> list[dict[str, Any]]:
    """De receptkaarten ergens in een antwoord: id, naam, afbeelding, tijd, personen, waardering en link. Geen dubbelen."""
    seen: set[str] = set()
    recipes: list[dict[str, Any]] = []
    for node in _walk(data):
        if not _is_recipe_card(node):
            continue
        recipe_id = str(node["id"])
        if recipe_id in seen or not _RECIPE_ID.match(recipe_id):
            continue
        seen.add(recipe_id)
        rating = node.get("rating")
        persons = node.get("numberOfPersons")
        author = node.get("author")
        count = node.get("numberOfIngredients")
        recipes.append(
            {
                "id": recipe_id,
                "name": node["title"].strip(),
                "image": _image(node),
                "minutes": _minutes(node.get("time")),
                "portions": persons if isinstance(persons, int) and persons > 0 else None,
                "rating": rating.get("value") if isinstance(rating, dict) and isinstance(rating.get("value"), (int, float)) and rating.get("value") else None,
                "ingredientCount": count if isinstance(count, int) and count > 0 else None,
                "course": node.get("course") or None,
                "author": author.get("name") if isinstance(author, dict) and author.get("name") else None,
                "url": _site(node.get("link")),
            }
        )
    return [recipe for recipe in recipes if recipe["name"]]


def get_lists() -> list[dict[str, Any]]:
    """Je receptenlijsten (*Mijn recepten*): `[{id, name, kind, count}]`, Favorieten eerst. Zonder de recepten zelf.

    Het overzicht van de site (`getRecipeLists`) toont per lijst alleen titel en plaatje van een paar recepten; de
    recepten van een lijst komen uit een andere aanvraag.
    """
    data = jumbo._persisted_query(
        "getRecipeLists",
        {"includeHasRecipe": False, "itemsInput": {"size": 1}, "listsSize": MAX_RECIPE_LISTS, "listsPage": 1},
    )
    lists: list[dict[str, Any]] = []
    for kind, key in (("favorites", "favouriteLists"), ("own", "customerLists")):
        for raw in (data.get(key) or {}).get("items") or []:
            if not isinstance(raw, dict) or not raw.get("id") or not raw.get("title"):
                continue
            count = raw.get("totalRecipes")
            lists.append({"id": str(raw["id"]), "name": str(raw["title"]).strip(), "kind": kind, "count": count if isinstance(count, int) else 0})
    return lists


def search(query: Any, offset: Any = 0) -> dict[str, Any]:
    """Zoek in alle recepten van Jumbo; per pagina `RECIPE_PAGE` stuks, vanaf `offset`.

    `{recipes, total}`: `total` is het aantal recepten dat bij de zoekterm past (de website toont "820 recepten").
    """
    query = " ".join(str(query or "").split())[:100]
    if not query:
        raise ValueError(tr("Zoekterm ontbreekt", "Search term missing"))
    try:
        start = max(0, int(offset or 0))
    except (TypeError, ValueError):
        start = 0
    data = jumbo._persisted_query(
        "getRecipesListers",
        {"input": {"searchTerms": query, "friendlyUrl": f"?searchTerms={query}", "offSet": start, "limit": RECIPE_PAGE}},
    )
    result = data.get("searchRecipes")
    if not isinstance(result, dict):
        raise RuntimeError(tr("Jumbo gaf de zoekresultaten niet terug", "Jumbo did not return the search results"))
    recipes = summaries(result.get("items"))
    total = result.get("count")
    return {"recipes": recipes, "total": total if isinstance(total, int) and total >= 0 else len(recipes)}


def get_by_ids(ids: list[str]) -> list[dict[str, Any]]:
    """Recepten op id (zoals de pagina's met een categorie die vragen)."""
    wanted = [_valid_id(recipe_id) for recipe_id in ids][:RECIPE_PAGE]
    data = jumbo._persisted_query("getRecipesByIds", {"ids": wanted, "limit": len(wanted), "offset": 0})
    return summaries(data)


# --- één recept ---------------------------------------------------------------------------------


def _ingredient_line(raw: dict[str, Any]) -> str:
    amount = str(raw.get("amount") or "").strip()
    name = str(raw.get("name") or "").strip()
    return " ".join(part for part in (amount, name) if part)


def parse_recipe(raw: Any, recipe_id: str) -> dict[str, Any]:
    """Een recept uit het antwoord van `getRecipe`: tekst, tijden, ingrediënten (met het Jumbo-product) en stappen."""
    if not isinstance(raw, dict) or not isinstance(raw.get("title"), str):
        raise RuntimeError(tr("Jumbo kent dit recept niet (meer)", "Jumbo doesn't know this recipe (any more)"))
    ingredients = []
    for item in raw.get("ingredients") or []:
        if not isinstance(item, dict) or not str(item.get("name") or "").strip():
            continue
        product = item.get("product") if isinstance(item.get("product"), dict) else None
        ingredients.append(
            {
                "name": str(item["name"]).strip(),
                "amount": str(item.get("amount") or "").strip() or None,
                "line": _ingredient_line(item),
                "productId": str(product["sku"]) if product and product.get("sku") else None,
                "product": str(product["title"]) if product and product.get("title") else None,
            }
        )
    nutrition = [
        {"name": str(n["name"]), "quantity": n.get("quantity"), "unit": n.get("unit") or ""}
        for n in raw.get("nutritions") or []
        if isinstance(n, dict) and n.get("name")
    ]
    author = raw.get("author") if isinstance(raw.get("author"), dict) else {}
    rating = raw.get("rating") if isinstance(raw.get("rating"), dict) else {}
    persons = raw.get("numberOfPersons")
    tip = str(raw.get("cookingTip") or "").strip()
    return {
        "id": str(raw.get("id") or recipe_id),
        "name": raw["title"].strip(),
        "description": str(raw.get("description") or "").strip() or None,
        "image": _image(raw),
        "portions": persons if isinstance(persons, int) and persons > 0 else None,
        "minutes": _minutes(raw.get("time")),
        "ingredients": ingredients,
        "steps": [str(step).strip() for step in raw.get("instructions") or [] if str(step).strip()],
        "tip": tip or None,
        "nutrition": nutrition,
        "course": raw.get("course") or None,
        "dishType": raw.get("dishType") or None,
        "author": author.get("name") or None,
        "rating": rating.get("value") if isinstance(rating.get("value"), (int, float)) else None,
        "ratingCount": rating.get("count") if isinstance(rating.get("count"), int) else None,
        "url": _site(raw.get("link")),
    }


def get_recipe(recipe_id: Any) -> dict[str, Any]:
    recipe_id = _valid_id(recipe_id)
    data = jumbo._persisted_query("getRecipe", {"id": recipe_id})
    return parse_recipe(data.get("recipe"), recipe_id)


# --- naar Mealie --------------------------------------------------------------------------------


def source_url(recipe: dict[str, Any]) -> str:
    """Waar het recept vandaan komt: zijn pagina op jumbo.com (Mealie bewaart het als bron)."""
    return recipe.get("url") or f"{jumbo.SITE_URL}/recepten/{recipe['id']}"


def _nutrition(items: list[dict[str, Any]]) -> dict[str, str]:
    data: dict[str, str] = {}
    for item in items:
        key = _NUTRITION.get(str(item["name"]).strip().casefold())
        if key and item.get("quantity") is not None:
            data[key] = f"{item['quantity']} {item.get('unit') or ''}".strip()
    return data


def jsonld(recipe: dict[str, Any]) -> dict[str, Any]:
    """Het recept als schema.org Recipe; Mealie importeert dat zonder een webpagina op te halen."""
    lines = [item["line"] for item in recipe["ingredients"]]
    steps = [{"@type": "HowToStep", "text": text} for text in recipe["steps"]]
    if not lines and not steps:
        raise RuntimeError(tr("Dit Jumbo-recept heeft geen ingrediënten of bereiding", "This Jumbo recipe has no ingredients or steps"))
    description = recipe.get("description") or ""
    if recipe.get("tip"):
        description = f"{description}\n\nTip: {recipe['tip']}".strip()
    data: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "Recipe",
        "name": recipe["name"],
        "url": source_url(recipe),
        "recipeIngredient": lines,
        "recipeInstructions": steps,
    }
    if description:
        data["description"] = description
    if recipe.get("image"):
        data["image"] = [recipe["image"]]
    if recipe.get("portions"):
        data["recipeYield"] = str(recipe["portions"])
    if recipe.get("minutes"):
        data["totalTime"] = f"PT{recipe['minutes']}M"
    if recipe.get("course"):
        data["recipeCategory"] = recipe["course"]
    if recipe.get("dishType"):
        data["keywords"] = recipe["dishType"]
    if recipe.get("author"):
        data["author"] = {"@type": "Organization", "name": recipe["author"]}
    if recipe.get("rating") and recipe.get("ratingCount"):
        data["aggregateRating"] = {"@type": "AggregateRating", "ratingValue": recipe["rating"], "ratingCount": recipe["ratingCount"]}
    nutrition = _nutrition(recipe.get("nutrition") or [])
    if nutrition:
        data["nutrition"] = {"@type": "NutritionInformation", **nutrition}
    return data
