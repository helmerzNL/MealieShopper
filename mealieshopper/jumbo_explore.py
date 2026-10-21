"""Verkenner voor de onofficiële Jumbo-routes.

Jumbo kan zijn API zonder bericht veranderen en weert soms server-adressen. Deze verkenner begint daarom
met een bereikbaarheidstest (zonder inloggen) en roept daarna met jouw Jumbo-koppeling de leesroutes aan.
Er wordt niets gewijzigd en niets bewaard: ook het mandje vullen wordt alleen met een lege lijst
geprobeerd, om te zien of die route en het invoertype bestaan.

Zoals bij `ah_explore` bevat het rapport bewust geen persoonlijke gegevens: namen, prijzen, producten en
id's worden vervangen door hun type. Zo kun je het rapport veilig delen.
"""
from __future__ import annotations

from typing import Any, Callable

import requests

from . import jumbo, jumbo_recipes
from .ah_explore import shape

EXAMPLE_TERM = "melk"
EXAMPLE_RECIPE = "pasta"


def _probe(name: str, call: Callable[[], Any], summarize: Callable[[Any], Any] | None = None) -> tuple[dict[str, Any], Any]:
    try:
        result = call()
    except jumbo.JumboSchemaError as exc:
        # Jumbo's eigen validatiemelding noemt alleen velden en typen uit onze query ("Cannot query field ..."):
        # dat is precies wat nodig is om een query te herstellen en bevat geen persoonsgegevens.
        return {"name": name, "ok": False, "error": type(exc).__name__, "schema": str(exc)[:300]}, None
    except Exception as exc:  # noqa: BLE001 - het fouttype is genoeg om de route te onderzoeken
        return {"name": name, "ok": False, "error": type(exc).__name__}, None
    return {"name": name, "ok": True, **({"info": summarize(result)} if summarize else {"shape": shape(result)})}, result


def _reach(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
    """Of Jumbo dit adres beantwoordt, en of het antwoord een bot-blokkade lijkt (HTML in plaats van JSON)."""
    response = requests.request(
        method, url, timeout=20, headers={"User-Agent": jumbo.WEB_USER_AGENT, "Accept": "application/json"}, **kwargs
    )
    kind = (response.headers.get("content-type") or "").split(";")[0].strip()
    return {
        "status": response.status_code,
        "contentType": kind,
        "looksLikeBotBlock": response.status_code in {403, 429, 503} or kind == "text/html",
    }


def _empty_add_probe() -> dict[str, Any]:
    """Het mandje-vullen-pad met een lege lijst: er wordt niets toegevoegd, we zien alleen of het pad klopt.

    `typename` is `Basket` bij succes of de naam van Jumbo's foutvariant (een typenaam, geen persoonsgegeven).
    """
    data = jumbo._graphql(
        jumbo.ADD_LINES_MUTATION, {"input": {"lines": [], "type": "ECOMMERCE"}}, "BasketPageAddBasketItems"
    )
    return {"typename": (data.get("addBasketLines") or {}).get("__typename")}


def _route(operation: str) -> str:
    """Met welk transport en welke clientnaam Jumbo de opgeslagen query beantwoordde (geen persoonsgegevens)."""
    method, client = jumbo._persisted_route.get(operation, ("?", "?"))
    return f"{method} met {client}"


def _empty_list_edit_probe() -> dict[str, Any]:
    """Schrijfpad van het lijstje met een lege lijst: er wordt niets toegevoegd, we zien alleen de typenaam."""
    data = jumbo._graphql_variants(
        "EditShoppingListItems", jumbo.EDIT_LIST_QUERIES, {"input": {"items": []}}, jumbo.LOYALTY_CLIENT
    )
    return {"typename": (data.get("editShoppingListItems") or {}).get("__typename")}


def _possible_result_types() -> list[str]:
    """Welke antwoorden kan `addBasketLines` geven? Introspectie staat bij Jumbo meestal uit; de fout zegt dat dan."""
    data = jumbo._graphql(
        'query AddBasketLinesResultTypes { __type(name: "AddBasketLinesResult") { possibleTypes { name } } }',
        None,
        "AddBasketLinesResultTypes",
    )
    return [str(entry.get("name")) for entry in ((data.get("__type") or {}).get("possibleTypes") or []) if isinstance(entry, dict)]


def explore() -> dict[str, Any]:
    """Controleer de bereikbaarheid en roep de leesroutes aan; geef een rapport met de vorm van de antwoorden."""
    probes: list[dict[str, Any]] = []

    for name, method, url, kwargs in (
        ("bereikbaar: auth.jumbo.com (OpenID-configuratie)", "GET", f"{jumbo.AUTH_BASE}/.well-known/openid-configuration", {}),
        ("bereikbaar: www.jumbo.com/api/graphql (zonder inloggen)", "POST", jumbo.GRAPHQL_URL,
         {"json": {"operationName": "Reach", "query": "query Reach { __typename }"}}),
    ):
        report, _ = _probe(name, lambda m=method, u=url, k=kwargs: _reach(m, u, **k), lambda data: data)
        probes.append(report)

    linked = bool(jumbo.configured_refresh_token())
    report, _ = _probe(
        "toegangstoken vernieuwen (auth.jumbo.com/oauth/token)",
        lambda: jumbo.get_access_token(force=True),
        lambda _: "ok",
    )
    probes.append(report)

    report, _ = _probe(
        "zoeken: GraphQL searchProducts",
        lambda: jumbo.search_products(EXAMPLE_TERM, 3),
        lambda products: {
            "producten gevonden": len(products),
            "velden gebruikt voor de query": jumbo.SEARCH_EXTRA_FIELDS[jumbo._search_variant] or "(alleen de kernvelden)",
            "eerste product": shape(products[0] if products else None),
        },
    )
    probes.append(report)

    report, _ = _probe("mandje: GraphQL activeBasket", jumbo.get_cart)
    probes.append(report)

    report, _ = _probe(
        "mandje vullen: addBasketLines met een lege lijst (voegt niets toe)",
        _empty_add_probe,
        lambda data: data,
    )
    probes.append(report)

    report, live_list = _probe("lijstje: GraphQL shoppingList", jumbo.get_shopping_list)
    probes.append(report)

    report, _ = _probe(
        "lijstje: editShoppingListItems met een lege lijst (voegt niets toe)", _empty_list_edit_probe, lambda data: data
    )
    probes.append(report)

    sku = next((item["sku"] for item in (live_list or {}).get("items", []) if item.get("sku")), None)
    if sku:
        report, _ = _probe("productgegevens van een regel van het lijstje: ProductDetail", lambda: jumbo.get_product(sku))
    else:
        report = {"name": "productgegevens van een regel van het lijstje: ProductDetail", "ok": False, "error": "overgeslagen: geen regel met SKU"}
    probes.append(report)

    report, lists = _probe(
        "mijn lijstjes: opgeslagen query GetCustomerProductLists",
        jumbo.get_product_lists,
        lambda found: {
            "eigen lijstjes": sum(1 for item in found if item["kind"] == "own"),
            "favorieten": sum(1 for item in found if item["kind"] == "favorites"),
            "eerste lijstje": shape(found[0] if found else None),
            "werkt via": _route("GetCustomerProductLists"),
        },
    )
    probes.append(report)

    first = next(iter(lists or []), None)
    if first:
        report, _ = _probe(
            "een lijstje: opgeslagen query GetProductList",
            lambda: jumbo.get_product_list(first["id"]),
            lambda found: {
                "producten": len(found["items"]),
                "aantal volgens Jumbo": first["itemCount"],
                "eerste product": shape(found["items"][0] if found["items"] else None),
                "werkt via": _route("GetProductList"),
            },
        )
    else:
        report = {"name": "een lijstje: opgeslagen query GetProductList", "ok": False, "error": "overgeslagen: geen lijstje"}
    probes.append(report)

    report, orders = _probe(
        "mijn bestellingen: opgeslagen query OnlineOrdersAndStoreReceipts",
        jumbo.get_orders,
        lambda found: {
            "bestellingen": len(found["orders"]),
            "winkelbonnen volgens Jumbo": found["storeReceipts"],
            "statussen": sorted({str(order["state"]) for order in found["orders"]}),
            "eerste bestelling": shape(found["orders"][0] if found["orders"] else None),
            "werkt via": _route("OnlineOrdersAndStoreReceipts"),
        },
    )
    probes.append(report)

    first_order = next(iter((orders or {}).get("orders") or []), None)
    if first_order:
        report, _ = _probe(
            "een bestelling met producten: GetOrder",
            lambda: jumbo.get_order(first_order["id"]),
            lambda found: {
                "producten": len(found["items"]),
                "eerste product": shape(found["items"][0] if found["items"] else None),
                "werkende variant": "{} ({})".format(*jumbo.ORDER_DETAIL_ARGUMENTS[jumbo._working_order_detail[0]]) if jumbo._working_order_detail else "?",
            },
        )
    else:
        report = {"name": "een bestelling met producten: GetOrder", "ok": False, "error": "overgeslagen: geen bestelling"}
    probes.append(report)

    report, found = _probe(
        "recepten zoeken: opgeslagen query getRecipesListers",
        lambda: jumbo_recipes.search(EXAMPLE_RECIPE),
        lambda found: {
            "recepten op de pagina": len(found["recipes"]),
            "recepten in totaal": found["total"],
            "eerste recept": shape(found["recipes"][0] if found["recipes"] else None),
            "werkt via": _route("getRecipesListers"),
        },
    )
    probes.append(report)

    report, _ = _probe(
        "receptenlijsten: opgeslagen query getRecipeLists",
        jumbo_recipes.get_lists,
        lambda lists: {
            "lijsten": len(lists),
            "favorieten": sum(1 for item in lists if item["kind"] == "favorites"),
            "eerste lijst": shape(lists[0] if lists else None),
            "werkt via": _route("getRecipeLists"),
        },
    )
    probes.append(report)

    report, _ = _probe(
        "favoriete recepten: opgeslagen query FavoriteRecipes",
        jumbo_recipes.get_favorites,
        lambda found: {
            "recepten op de pagina": len(found["recipes"]),
            "recepten in totaal": found["total"],
            "volgende pagina": found["hasNext"],
            "eerste recept": shape(found["recipes"][0] if found["recipes"] else None),
            "werkt via": _route("FavoriteRecipes"),
        },
    )
    probes.append(report)

    first_recipe = next(iter((found or {}).get("recipes") or []), None)
    if first_recipe:
        report, _ = _probe(
            "een recept: opgeslagen query getRecipe",
            lambda: jumbo_recipes.get_recipe(first_recipe["id"]),
            lambda recipe: {
                "ingrediënten": len(recipe["ingredients"]),
                "stappen": len(recipe["steps"]),
                "eerste ingrediënt": shape(recipe["ingredients"][0] if recipe["ingredients"] else None),
                "werkt via": _route("getRecipe"),
            },
        )
        probes.append(report)
        # Recepten op id (de categoriepagina's): alleen de vorm van het antwoord, om die later te kunnen tonen.
        report, _ = _probe(
            "recepten op id: opgeslagen query getRecipesByIds",
            lambda: jumbo_recipes.get_by_ids([r["id"] for r in found["recipes"][:3]]),
            lambda recipes: {"recepten gevonden": len(recipes), "eerste recept": shape(recipes[0] if recipes else None), "werkt via": _route("getRecipesByIds")},
        )
    else:
        report = {"name": "een recept: opgeslagen query getRecipe", "ok": False, "error": "overgeslagen: geen recept gevonden"}
    probes.append(report)

    report, _ = _probe(
        "typen van het antwoord op addBasketLines (introspectie)",
        _possible_result_types,
        lambda names: names or ["leeg"],
    )
    probes.append(report)

    return {
        "note": "Alleen de vorm van de antwoorden: geen namen, prijzen of id's. Veilig te delen.",
        "linked": linked,
        "probes": probes,
        "ok": sum(1 for probe in probes if probe["ok"]),
    }
