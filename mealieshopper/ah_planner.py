"""De weekplanner van de AH-app in MealieShopper: tonen, en wat erin staat in het weekmenu van Mealie zetten.

Bij AH wordt alleen gelezen (`ah.get_week_planner`); plannen doe je in de AH-app. *Naar weekmenu* zet per dag in het
weekmenu van Mealie, als diner (AH kent geen soort maaltijd):

- **Allerhande-recepten.** Een recept dat nog niet in Mealie staat, wordt eerst geïmporteerd; een recept dat er al staat
  (zelfde receptnummer als bron) wordt hergebruikt.
- **Losse producten** (4.3.0). Heet een recept in Mealie al zoals het product (de Padifood Chinese tomatensoep), dan komt
  dat recept op die dag. Van de andere producten maakt MealieShopper zelf een recept in Mealie, met de producten als
  ingrediënten. Met *samen als één menu* (`combine`) worden de producten van een dag één menu, met de notitie van die dag
  als naam ("Pizza"); met *soep en pizza altijd los* (`splitMeals`) krijgen soep en pizza dan toch een eigen recept. Zonder
  `combine` wordt elk product een eigen recept. Een recept of menu dat MealieShopper eerder van dezelfde producten maakte,
  wordt teruggevonden aan zijn bron en hergebruikt.

Eigen recepten van het lid gaan niet mee, en een notitie alleen als naam van een menu: dat besloot de eigenaar. Er komt
alleen iets bij in Mealie. Staat een recept op die dag al in het weekmenu, dan blijft het bij die ene keer, zodat twee keer
op de knop drukken niets dubbel zet. MealieShopper bewaart zelf geen planning (zie docs/onderzoek-weekmenu-plannen.md en
docs/onderzoek-ah-weekplanner.md).
"""
from __future__ import annotations

import datetime
import logging
import re
from typing import Any

from . import ah, mealie
from .i18n import tr

log = logging.getLogger(__name__)

MAX_RECIPES = 20  # per verzoek (een dag); zo blijft een verzoek binnen de 120 seconden van RESPONSE_TIMEOUT
MAX_PRODUCTS = 20
TEXT_MAX = 200
ENTRY_TYPE = "dinner"

PLANNED = "planned"
ALREADY_PLANNED = "already_planned"
FAILED = "failed"

RECIPE = "recipe"  # een Allerhande-recept
PRODUCT = "product"  # een los product: een bestaand recept met die naam, of een eigen recept van dat ene product
MENU = "menu"  # de losse producten van een dag samen

# Een product dat een maaltijd op zich is, ook als de producten van een dag samen een menu worden (`splitMeals`): soep en
# pizza. Op de woorden van de titel (kleine letters, zonder accenten), zodat "tomatensoep" en "pizza's" tellen en
# "soepgroente", "soepballetjes", "pizzadeeg" en "pizzasaus" (een onderdeel van een maaltijd) niet. Uitbreiden kan hier.
_STANDALONE = re.compile(r"soep(?:en)?\b|pizza(?:s| s)?\b")


def standalone(title: Any) -> bool:
    """Is dit product een maaltijd op zich (soep, pizza)? Zie `_STANDALONE`."""
    return bool(_STANDALONE.search(" ".join(ah._words(title))))


def _name_key(name: Any) -> tuple[str, ...]:
    """Een naam om op te vergelijken: de woorden zonder hoofdletters, accenten en leestekens, en zonder AH's eigen merk
    vooraan, zodat "Bakaardappeltjes" in Mealie gelijk is aan het product "AH Bakaardappeltjes" (zoals `ah._is_exact`)."""
    words = ah._words(name)
    return tuple(words[1:] if words[:1] == ["ah"] and len(words) > 1 else words)


async def get() -> dict[str, Any]:
    """De weekplanner van de AH-app, met bij elk product `standalone`: zo toont het scherm vooraf wat er met het product
    gebeurt, met dezelfde regel als *Naar weekmenu*."""
    planner = await ah.get_week_planner()
    for day in planner.get("days") or []:
        for item in day.get("items") or []:
            if item.get("kind") == "product":
                item["standalone"] = standalone(item.get("title"))
    return planner


# --- de invoer van *Naar weekmenu* -----------------------------------------------------------------------------------


def _date(value: Any) -> str:
    try:
        return datetime.date.fromisoformat(str(value or "").strip()).isoformat()
    except ValueError:
        raise ValueError(tr("Kies een geldige datum (JJJJ-MM-DD).", "Choose a valid date (YYYY-MM-DD).")) from None


def _number(value: Any, nl: str, en: str) -> int:
    text = str(value if value is not None and not isinstance(value, bool) else "").strip()
    if not text.isdigit() or len(text) > 12 or int(text) <= 0:
        raise ValueError(tr(nl, en))
    return int(text)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(tr("Ongeldige invoer.", "Invalid input."))
    return " ".join(value.split())[:TEXT_MAX]


def _flag(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError(tr("Ongeldige invoer.", "Invalid input."))
    return value


def _list(value: Any, limit: int, nl: str, en: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(tr("Ongeldige invoer.", "Invalid input."))
    if len(value) > limit:
        raise ValueError(tr(nl, en))
    return value


def clean_day(body: Any) -> dict[str, Any]:
    """Wat van één dag in het weekmenu moet, uit `{date, recipeIds, products: [{productId, title}], note, combine,
    splitMeals}`: gecontroleerd, zonder dubbele, met `combine` en `splitMeals` standaard aan."""
    if not isinstance(body, dict):
        raise ValueError(tr("Ongeldige invoer.", "Invalid input."))
    recipe_ids: list[int] = []
    for value in _list(body.get("recipeIds"), MAX_RECIPES, f"Te veel recepten in één keer (hooguit {MAX_RECIPES}).", f"Too many recipes at once (at most {MAX_RECIPES})."):
        number = _number(value, "Ongeldig receptnummer van AH.", "Invalid AH recipe number.")
        if number not in recipe_ids:
            recipe_ids.append(number)
    products: list[dict[str, Any]] = []
    for value in _list(body.get("products"), MAX_PRODUCTS, f"Te veel producten in één keer (hooguit {MAX_PRODUCTS}).", f"Too many products at once (at most {MAX_PRODUCTS})."):
        if not isinstance(value, dict):
            raise ValueError(tr("Ongeldige invoer.", "Invalid input."))
        number = _number(value.get("productId"), "Ongeldig productnummer van AH.", "Invalid AH product number.")
        if all(product["productId"] != number for product in products):
            products.append({"productId": number, "title": _text(value.get("title"))})
    if not recipe_ids and not products:
        raise ValueError(tr("Niets om in het weekmenu te zetten.", "Nothing to put in the week menu."))
    return {
        "date": _date(body.get("date")),
        "recipeIds": recipe_ids,
        "products": products,
        "note": _text(body.get("note")),
        "combine": _flag(body.get("combine"), True),
        "splitMeals": _flag(body.get("splitMeals"), True),
    }


# --- recepten en menu's in Mealie ------------------------------------------------------------------------------------


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


async def _recipe_in_mealie(recipe_id: int) -> dict[str, Any]:
    """Het Allerhande-recept in Mealie, `{slug, id, imported}`: al in Mealie (zelfde bron) of net geïmporteerd."""
    url = ah.recipe_url(recipe_id)
    summary = await mealie.find_recipe_summary_by_source(url)
    if summary:
        return {"slug": str(summary["slug"]), "id": str(summary.get("id") or ""), "imported": False}
    return {"slug": await mealie.import_from_url(url), "id": "", "imported": True}


async def _recipe_by_name(title: str) -> dict[str, Any] | None:
    """Het recept in Mealie dat heet zoals dit product (`_name_key`), of None. Lukt het zoeken niet, dan ook None: dan wordt
    het product een eigen recept, zoals bij een product dat nog niet in Mealie staat."""
    wanted = _name_key(title)
    if not wanted:
        return None
    try:
        found = await mealie.search_recipes(title, per_page=20)
    except Exception as exc:  # noqa: BLE001 - zoeken is een hulp; het product gaat dan als eigen recept
        log.info("AH-weekplanner: zoeken op naam in Mealie mislukt (%s).", type(exc).__name__)
        return None
    for card in found.get("recipes") or []:
        if card.get("slug") and _name_key(card.get("name")) == wanted:
            return {"slug": str(card["slug"]), "id": str(card.get("recipeId") or ""), "imported": False}
    return None


async def _product_details(products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """De producten met de titel en het plaatje van AH. Lukt dat niet of ontbreekt er een, dan de titel uit het verzoek."""
    found: dict[int, dict[str, Any]] = {}
    try:
        for item in await ah.get_products_by_ids([product["productId"] for product in products]):
            try:
                found[int(item.get("id"))] = item
            except (TypeError, ValueError):
                continue
    except Exception as exc:  # noqa: BLE001 - de titels uit de planner volstaan, alleen het plaatje ontbreekt dan
        log.info("AH-weekplanner: productgegevens ophalen mislukt (%s).", type(exc).__name__)
    details = []
    for product in products:
        item = found.get(product["productId"]) or {}
        title = _text(item.get("title") if isinstance(item.get("title"), str) else "") or product["title"]
        details.append({"productId": product["productId"], "title": title or tr(f"Product {product['productId']}", f"Product {product['productId']}"), "image": item.get("image") or ""})
    return details


def _menu_name(note: str, products: list[dict[str, Any]]) -> str:
    """De naam van een menu: de notitie van die dag, anders de producten ("A, B en C")."""
    if note:
        return note
    titles = [product["title"] for product in products]
    if len(titles) == 1:
        return titles[0]
    return f"{', '.join(titles[:-1])} {tr('en', 'and')} {titles[-1]}"[:TEXT_MAX]


def _source(products: list[dict[str, Any]], menu: bool) -> str:
    """De vaste bron waaraan Mealie een recept of menu van deze producten later teruggeeft (`find_recipe_summary_by_source`,
    een exacte vergelijking). Een eigen recept van één product: de link naar dat product. Een menu: de link naar het product
    met het kleinste nummer, met `#menu-` en alle nummers erachter, zoals `crisp.source_url` een fragment gebruikt. Zo is een
    menu van één product (met de notitie als naam) nooit hetzelfde recept als het eigen recept van dat product."""
    ids = sorted(product["productId"] for product in products)
    base = ah.product_web_url(ids[0])
    return f"{base}#menu-{'-'.join(str(number) for number in ids)}" if menu else base


def _jsonld(name: str, source: str, products: list[dict[str, Any]], with_image: bool) -> dict[str, Any]:
    """Een schema.org-recept van deze producten: elk product één keer als ingrediënt, met de naam zoals AH hem geeft, zodat
    *Weekmenu naar mandje* precies dat product vindt."""
    titles = [product["title"] for product in products]
    recipe: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "Recipe",
        "name": name,
        "url": source,
        "description": tr("Uit de weekplanner van de AH-app: ", "From the week planner of the AH app: ") + ", ".join(titles),
        "recipeIngredient": [f"1 {title}" for title in titles],
        "recipeInstructions": [{"@type": "HowToStep", "text": tr("Bereid volgens de verpakking.", "Prepare as stated on the pack.")}],
    }
    image = next((product["image"] for product in products if product.get("image")), "")
    if with_image and image:
        recipe["image"] = [image]
    return recipe


async def _create(name: str, source: str, products: list[dict[str, Any]]) -> str:
    """Maak het recept in Mealie. Weigert Mealie het (4xx), dan nog één keer zonder plaatje, zoals de poging "zonder foto"
    bij de maaltijdboxen; bij een fout van Mealie zelf (5xx) niet, zodat er niets dubbel ontstaat."""
    recipe = _jsonld(name, source, products, with_image=True)
    try:
        return await mealie.import_via_jsonld(source, recipe)
    except mealie.MealieImportError as exc:
        if "image" in recipe and 400 <= exc.status_code < 500:
            return await mealie.import_via_jsonld(source, _jsonld(name, source, products, with_image=False))
        raise


async def _schedule(result: dict[str, Any], recipe: dict[str, Any], planned: set[tuple[str, str]]) -> None:
    """Zet het recept op de dag van `result`, tenzij het daar al staat; vult `slug`, `mealieUrl`, `imported` en `status`."""
    date = result["date"]
    result.update(imported=recipe["imported"], slug=recipe["slug"], mealieUrl=await mealie.recipe_page_url(recipe["slug"]))
    if {(date, recipe["slug"]), (date, recipe["id"])} & planned:
        result["status"] = ALREADY_PLANNED
        return
    await mealie.create_meal_plan_entry({"date": date, "entryType": ENTRY_TYPE, "recipeSlug": recipe["slug"]})
    planned.add((date, recipe["slug"]))
    result["status"] = PLANNED


def _failed(result: dict[str, Any], what: str) -> dict[str, Any]:
    log.exception("AH-weekplanner: %s op %s in het weekmenu zetten mislukt", what, result["date"])
    result["status"] = FAILED
    result["error"] = (
        tr("In het weekmenu zetten mislukt", "Putting it in the week menu failed")
        if result["slug"]
        else tr("In Mealie zetten mislukt", "Putting it in Mealie failed")
    )
    return result


def _result(date: str, kind: str, title: str = "", **extra: Any) -> dict[str, Any]:
    return {"date": date, "kind": kind, "title": title, "imported": False, "slug": "", "mealieUrl": "", **extra}


async def _own(date: str, kind: str, name: str, products: list[dict[str, Any]], planned: set[tuple[str, str]]) -> dict[str, Any]:
    """Een eigen recept van één product (`kind` product) of een menu van een paar producten (`kind` menu): het recept dat
    MealieShopper er eerder van maakte (zelfde bron), anders een nieuw recept, en dan op de dag zetten."""
    menu = kind == MENU
    result = _result(date, kind, name, productIds=[product["productId"] for product in products])
    source = _source(products, menu)
    try:
        summary = await mealie.find_recipe_summary_by_source(source)
        if summary:
            recipe = {"slug": str(summary["slug"]), "id": str(summary.get("id") or ""), "imported": False}
        else:
            recipe = {"slug": await _create(name, source, products), "id": "", "imported": True}
        await _schedule(result, recipe, planned)
    except Exception:  # noqa: BLE001 - Mealie: alleen in het logboek, de rest van de dag gaat door
        return _failed(result, "menu" if menu else f"product {products[0]['productId']}")
    return result


def _split(products: list[dict[str, Any]], combine: bool, split_meals: bool) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(apart, samen): zonder `combine` alles apart; met `combine` alles samen, behalve soep en pizza bij `split_meals`."""
    if not combine:
        return products, []
    alone = [product for product in products if split_meals and standalone(product["title"])]
    return alone, [product for product in products if product not in alone]


async def to_meal_plan(day: dict[str, Any]) -> dict[str, Any]:
    """Zet één dag (`clean_day`) in het weekmenu van Mealie. Een onderdeel dat mislukt, houdt de rest niet op.

    Per onderdeel `{date, kind, title, status, imported, slug, mealieUrl}` met `recipeId` (een Allerhande-recept) of
    `productIds` (een product of menu); `status` is `planned`, `already_planned` of `failed` (dan met `error`, een algemene
    tekst: het antwoord van AH of Mealie staat alleen in het logboek). `imported` is waar voor een recept dat net in Mealie
    is gekomen: geïmporteerd of door MealieShopper gemaakt.
    """
    await mealie.ensure_configured()
    date = day["date"]
    planned = _planned(await mealie.get_meal_plan(date, date))
    results: list[dict[str, Any]] = []
    for recipe_id in day["recipeIds"]:
        result = _result(date, RECIPE, recipeId=recipe_id)
        try:
            await _schedule(result, await _recipe_in_mealie(recipe_id), planned)
        except Exception:  # noqa: BLE001 - AH of Mealie: alleen in het logboek, de volgende onderdelen gaan door
            _failed(result, f"recept {recipe_id}")
        results.append(result)
    if not day["products"]:
        return {"results": results}
    rest: list[dict[str, Any]] = []
    for product in await _product_details(day["products"]):
        existing = await _recipe_by_name(product["title"])
        if not existing:
            rest.append(product)
            continue
        result = _result(date, PRODUCT, product["title"], productIds=[product["productId"]])
        try:
            await _schedule(result, existing, planned)
        except Exception:  # noqa: BLE001 - Mealie: alleen in het logboek
            _failed(result, f"product {product['productId']}")
        results.append(result)
    alone, together = _split(rest, day["combine"], day["splitMeals"])
    for product in alone:
        results.append(await _own(date, PRODUCT, product["title"], [product], planned))
    if together:
        results.append(await _own(date, MENU, _menu_name(day["note"], together), together, planned))
    return {"results": results}
