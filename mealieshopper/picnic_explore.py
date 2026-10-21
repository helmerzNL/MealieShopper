"""Verkenner voor de onofficiële Picnic-routes.

Picnic kan zijn API zonder bericht veranderen. Deze verkenner roept de leesroutes aan met jouw
Picnic-koppeling en meldt per route of het werkt en **welke velden er in het antwoord zitten**.
Er wordt niets gewijzigd en niets bewaard.

Zoals bij `ah_explore` bevat het rapport bewust geen persoonlijke gegevens: namen, adressen, prijzen,
producten en id's worden vervangen door hun type. Zo kun je het rapport veilig delen.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any
from urllib.parse import quote

from . import picnic
from .ah_explore import _probe, _union, shape

SEARCH_TERM = "melk"

# Receptpagina's van Picnic, zoals de community-libraries `picnic-api` (MRVDH) en `mcp-de-picnic` ze noemen.
RECIPE_PAGES = (
    ("opgeslagen recepten: GET pages/saved-deep-dive-page-content", "saved-deep-dive-page-content"),
    ("kookboek: GET pages/cookbook-page-content", "cookbook-page-content"),
    ("recepten-startpagina: GET pages/meals-page-root", "meals-page-root"),
    ("opnieuw bestellen: GET pages/meals-purchase-page-root", "meals-purchase-page-root"),
)
# Pagina's die de productpagina noemt rond aankopen en favorieten; welke de app gebruikt hangt van het account af.
PURCHASE_PAGES = (
    "purchases-page-root",
    "purchases-page-root,aisle_to_focus=DAIRY&is_refresh_needed=true",
    "purchases_page_no_consent_root",
)
# Gewone routes (geen pagina's) waar de app de favorieten van een gebruiker vandaan kan halen. Gokken; 404 is een prima antwoord.
FAVORITE_ROUTES = (
    "/favorites", "/favourites", "/sellable-favorites", "/sellable_favorites", "/sellables/favorites", "/liked_sellables",
    "/user/favorites", "/user/sellable-favorites", "/my_favorites", "/lists/favorites", "/favorites/sellables", "/purchases",
)
# Waar de favorieten staan weet geen enkele library. Dit zijn gokken op basis van de naamgeving van de
# andere pagina's; een 404 kost niets en de pagina's die wel bestaan verraden zich.
FAVORITE_GUESSES = (
    "main-page-root",
    "home-page-root",
    "my-picnic-page-root",
    "favorite-products-page-root",
    "favourite-products-page-root",
    "product-favorites-page-root",
    "liked-products-page-root",
    "bought-before-page-root",
    "purchase-history-page-root",
    "frequently-bought-page-root",
    "my-lists-page-root",
    "lists-page-root",
    "shopping-list-page-root",
    "profile-deep-dive-page-content",
    "favorites-deep-dive-page-content",
    "favourites-deep-dive-page-content",
    "favorites-page-root",
    "favorites-page-content",
    "favourites-page-root",
    "my-favorites-page-root",
    "purchased-deep-dive-page-content",
    "previously-bought-page-root",
    "my-products-page-root",
    "my-profile-page-root",
    "profile-page-root",
    "account-page-root",
    "favorites-tab-page-root",
    "favourites-tab-page-root",
    "favorite-selling-units-page-root",
    "favorites-deep-dive-page",
    "saved-products-deep-dive-page-content",
    "liked-deep-dive-page-content",
    "wishlist-deep-dive-page-content",
)
_FAVORITE_WORD = re.compile(r"favou?rite|bookmark|wish|liked|heart|saved", re.I)
# Velden waarvan de waarde een vaste naam is (een id van een onderdeel, een soort, een pictogram), geen tekst van jou.
_NAME_FIELDS = {"id", "name", "type", "testID", "iconKey", "actionType", "callback", "schema", "component"}
_RECIPE_KEY = re.compile(r"selling_group|recipe|sellable", re.I)
_LINK = re.compile(r"(?:picnic://|/pages/|page_id=|pageId=)[^\s\"'<>)]*")


def short_error(message: str) -> str:
    """Een foutmelding van Picnic als statuscode en foutcode (`404 NOT_FOUND`), zonder de rest van het antwoord."""
    status, _, rest = str(message).partition(":")
    if not status.strip().isdigit():
        return str(message)[:80]
    try:
        code = (json.loads(rest).get("error") or {}).get("code")
    except (ValueError, AttributeError):
        code = None
    return f"{status.strip()} {code}" if isinstance(code, str) else status.strip()


_contexts = picnic.analytics_data


def recipe_tiles(page: Any) -> list[dict[str, Any]]:
    """Onderdelen van een pagina waarvan de analysegegevens een recept (selling group) noemen."""
    return [node for node in picnic._walk(page) if any(_RECIPE_KEY.search(key) for data in _contexts(node) for key in data)]


def _clean_link(link: str) -> str:
    """Een verwijzing naar een pagina zonder waarden: alleen de naam van de pagina en de namen van de parameters."""
    path, _, query = link.partition("?")
    names = sorted({part.split("=")[0] for part in query.split("&") if part})
    path = re.sub(r"[0-9a-f]{8,}|\d{3,}", "<id>", path)
    return path + ("?" + "&".join(names) if names else "")


def favorite_hits(page: Any) -> list[str]:
    """Namen van velden en onderdelen op een pagina die over favorieten gaan (`is_favorite`, `favorite-button`).

    We kijken alleen naar veldnamen en naar vaste namen in velden als `id` en `type`, nooit naar teksten die
    bij een product of recept horen, zodat het rapport veilig te delen blijft.
    """
    hits: set[str] = set()
    for node in picnic._walk(page):
        for key, value in node.items():
            if _FAVORITE_WORD.search(key):
                hits.add(f"veld: {key}")
            if key in _NAME_FIELDS and isinstance(value, str) and len(value) <= 80 and " " not in value and _FAVORITE_WORD.search(value):
                hits.add(f"{key}: " + re.sub(r"[0-9a-f]{8,}|\d{3,}", "<id>", value))
    return sorted(hits)[:40]


def favorite_values(page: Any) -> dict[str, Any]:
    """Velden op een pagina die over favorieten gaan, met hun waarde (alleen waar/onwaar/getal) of hun vorm.

    Zo zien we of een productpagina zegt of dit product een favoriet is. Teksten komen er niet in.
    """
    found: dict[str, set[str]] = {}
    for node in picnic._walk(page):
        for key, value in node.items():
            if _FAVORITE_WORD.search(key):
                shown = str(value).lower() if isinstance(value, (bool, int)) or value is None else json.dumps(shape(value))
                found.setdefault(key, set()).add(shown)
    return {key: sorted(values)[:6] for key, values in sorted(found.items())}


_CODE_KEYS = {"expression", "name", "callback", "actionType", "id", "type"}
_CODE_WORD = re.compile(r"favou?rit|saved|purchase|heart|fetch|request|resource|endpoint|query|mutation|task|load", re.I)
_ADDRESS_KEYS = {"url", "path", "endpoint", "resource", "uri", "href", "method", "query", "request", "mutation", "operation", "task"}


def _redact(text: str) -> str:
    return re.sub(r"[0-9a-f]{8,}|\d{3,}", "<id>", text)[:200]


_HEART_CODE = re.compile(r"sellable-favorites|likedSellables|onPressHeart|favoriteAction|toggleFavorite", re.I)


def _redact_long(text: str, limit: int = 2500) -> str:
    return re.sub(r"[0-9a-f]{8,}|\d{3,}", "<id>", text)[:limit]


def code_report(page: Any) -> dict[str, Any]:
    """Hoe een pagina haar gegevens ophaalt: de 'code' in de paginaboom, zonder persoonlijke waarden.

    Een Picnic-pagina bevat naast onderdelen ook uitdrukkingen, acties en wachtonderdelen (`SUSPENSE`) die
    gegevens elders ophalen. Daar zit de bron van de favorieten. We melden alleen de velden van dat soort onderdelen,
    adressen en korte stukjes code over favorieten of opslaan, met getallen en id's vervangen door `<id>`.
    """
    code: set[str] = set()
    addresses: set[str] = set()
    suspense: list[Any] = []
    states: list[Any] = []
    heart: set[str] = set()
    lazy: set[str] = set()
    for node in picnic._walk(page):
        config = node.get("pageConfig")
        if isinstance(config, dict) and isinstance(config.get("id"), str):
            params = config.get("parameters") if isinstance(config.get("parameters"), dict) else {}
            lazy.add(_redact(config["id"]) + " ? " + ", ".join(f"{k}={_redact(str(v))[:40]}" for k, v in sorted(params.items())))
        if isinstance(node.get("expression"), str) and _HEART_CODE.search(node["expression"]):
            heart.add(_redact_long(node["expression"]))
        for key, value in node.items():
            if isinstance(value, str) and key in _ADDRESS_KEYS:
                addresses.add(f"{key}: {_redact(value)}")
            elif isinstance(value, str) and key in _CODE_KEYS and _CODE_WORD.search(value):
                code.add(f"{key}: {_redact(value)}")
        if node.get("type") == "SUSPENSE" and len(suspense) < 3:
            suspense.append(shape(node))
        if isinstance(node.get("state"), dict) and node["state"]:
            states.append(shape(node["state"]))
    return {
        "adressen en verzoeken": sorted(addresses)[:30],
        "code over favorieten of opslaan": sorted(code)[:40],
        "volledige code van de hartjesknop": sorted(heart)[:6],
        "pagina's die later laden (pageConfig)": sorted(lazy)[:30],
        "wachtonderdelen (SUSPENSE)": suspense,
        "toestand (state)": _union(states) if states else "leeg",
    }


def page_outline(page: Any, limit: int = 80) -> list[str]:
    """De opbouw van een pagina in leesvolgorde: koppen (tekst van onderdelen zonder producten) en groepen producttegels.

    Zo zien we of er een eigen kopje bij de favorieten staat en hoeveel producten daaronder komen. Alleen korte
    teksten van onderdelen zonder producten komen erin, nooit productnamen.
    """
    out: list[str] = []
    run = 0

    def flush() -> None:
        nonlocal run
        if run:
            out.append(f"tegels: {run}")
            run = 0

    def has_tile(node: Any) -> bool:
        return any(n.get("type") == "SELLING_UNIT_TILE" for n in picnic._walk(node))

    def visit(node: Any) -> None:
        nonlocal run
        if isinstance(node, list):
            for item in node:
                visit(item)
        elif isinstance(node, dict):
            if node.get("type") == "SELLING_UNIT_TILE":
                run += 1
            elif node.get("type") == "PML" and not has_tile(node):
                heads = [n["markdown"].strip() for n in picnic._walk(node) if n.get("type") == "RICH_TEXT" and isinstance(n.get("markdown"), str)]
                heads = [_redact(h)[:40] for h in heads if 0 < len(h.strip()) <= 40]
                if heads:
                    flush()
                    out.append("kop: " + " | ".join(heads[:3]))
            else:
                for value in node.values():
                    visit(value)

    visit(page)
    flush()
    return out[:limit]


_LIKE_CONSTANTS = (
    re.compile(r"===\s*undefined\s*\?\s*(true|false)"),
    re.compile(r"isSavedValue\s*=\s*(true|false)"),
    re.compile(r"likedSellables:\s*\{\s*\[\"s\d+\"\]:\s*(true|false)"),
)


def like_constants(page: Any) -> dict[str, list[str]]:
    """De waar/onwaar-waarden die de server in de hartjescode van een productpagina heeft gezet (per soort plek)."""
    found: dict[str, set[str]] = {}
    for node in picnic._walk(page):
        text = node.get("expression")
        if isinstance(text, str):
            for index, pattern in enumerate(_LIKE_CONSTANTS):
                found.setdefault(f"plek {index + 1}", set()).update(pattern.findall(text))
    return {key: sorted(values) for key, values in sorted(found.items())}


def saved_report(page: dict[str, Any]) -> dict[str, Any]:
    """Hoe de pagina markeert welke producten favoriet zijn: de vorm van het veld `saved` en wat we ervan maken."""
    values = [node["saved"] for node in picnic._walk(page) if "saved" in node]
    products = picnic.mark_favorites(picnic.products_from_page(page), page)
    marks = Counter({True: "favoriet", False: "niet favoriet", None: "onbekend"}[p["favorite"]] for p in products)
    return {
        "vorm van saved": _union([shape(value) for value in values]) if values else "komt niet voor",
        "aantal keer saved": len(values),
        "producten": dict(marks),
        "alleen favorieten": picnic.only_favorites(products)[1],
    }


def type_census(page: Any) -> dict[str, int]:
    """Hoe vaak elke soort onderdeel (`type`) op een pagina voorkomt. Zo zien we of een pagina leeg is of anders is opgebouwd."""
    counts = Counter(node["type"] for node in picnic._walk(page) if isinstance(node.get("type"), str) and len(node["type"]) <= 40)
    return dict(counts.most_common(15))


def page_links(page: Any) -> list[str]:
    """De pagina's waar een pagina naar verwijst (diepe links), zonder id's. Zo vinden we pagina's die we niet kennen."""
    strings = [page] if isinstance(page, str) else [v for node in picnic._walk(page) for v in node.values() if isinstance(v, str)]
    found = {_clean_link(match) for text in strings for match in _LINK.findall(text)}
    return sorted(found)[:40]


def pml_vocabulary(tile: Any) -> dict[str, list[str]]:
    """Welke soorten onderdelen er in een tegel zitten en welke velden die hebben, zonder inhoud.

    Zo zien we waar de tekst (naam, ingrediënten) en de afbeelding van een tegel staan.
    """
    vocabulary: dict[str, set[str]] = {}
    for node in picnic._walk(tile.get("pml") if isinstance(tile, dict) else None):
        kind = node.get("type")
        if isinstance(kind, str):
            vocabulary.setdefault(kind, set()).update(k for k, v in node.items() if k != "type" and isinstance(v, (str, int, float, dict)))
    return {kind: sorted(keys) for kind, keys in sorted(vocabulary.items())}


def analytics_fields(page: Any) -> Any:
    """De velden in de analysegegevens van alle receptonderdelen van een pagina, samengevoegd."""
    shapes = [shape(data) for node in recipe_tiles(page) for data in _contexts(node)]
    return _union(shapes) if shapes else "leeg"


def _page_info(page: Any) -> dict[str, Any]:
    if isinstance(page, str):
        # Geen JSON: waarschijnlijk een React Server Components-antwoord. We melden alleen de grootte en de soorten.
        return {"formaat": "tekst (geen JSON)", "lengte": len(page), "noemt SELLING_GROUP": "SELLING_GROUP" in page,
                "verwijzingen": page_links(page)}
    return {"formaat": "JSON", "bovenste velden": sorted(page) if isinstance(page, dict) else shape(page),
            "receptonderdelen": len(recipe_tiles(page)),
            "producttegels": sum(1 for node in picnic._walk(page) if node.get("type") == "SELLING_UNIT_TILE"),
            "soorten onderdelen": type_census(page), "favoriet-namen": favorite_hits(page),
            "verwijzingen": page_links(page)}


def explore() -> dict[str, Any]:
    """Roep de leesroutes aan en geef een rapport met de vorm van de antwoorden."""
    probes: list[dict[str, Any]] = []

    report, page = _probe(
        "zoeken: GET pages/search-page-results",
        lambda: picnic._authed("GET", f"/pages/search-page-results?search_term={SEARCH_TERM}"),
        lambda data: {"producten gevonden": len(picnic.products_from_search_page(data)),
                      "eerste product": shape(next(iter(picnic.products_from_search_page(data)), None))},
    )
    probes.append(report)
    if page is not None:
        # De ruwe boom is groot; de vorm van één tegel is wat we nodig hebben om het zoeken te herstellen.
        tile = next((n for n in picnic._walk(page.get("body")) if n.get("type") == "SELLING_UNIT_TILE"), None)
        probes.append({"name": "een zoektegel (ruwe vorm)", "ok": tile is not None, "shape": shape(tile)})

    for name, path, method, body in (
        ("winkelmandje: GET cart", "/cart", "GET", None),
        ("leveringen: POST deliveries/summary", "/deliveries/summary", "POST", []),
        ("lopende leveringen: POST deliveries/summary [CURRENT]", "/deliveries/summary", "POST", ["CURRENT"]),
    ):
        report, result = _probe(name, lambda m=method, p=path, b=body: picnic._authed(m, p, body=b))
        probes.append(report)
        if path == "/deliveries/summary" and body == [] and isinstance(result, list):
            delivery_ids = [picnic._delivery_summary(d)["id"] for d in result if isinstance(d, dict)]
            probes.append({"name": "bestellingen: aantal leveringen in het overzicht", "ok": True, "info": {"aantal": len(result)}})
            # Met een eerste levering kijken we ook hoe één bestelling eruitziet (de producten zitten daarin).
            first_id = next((str(i) for i in delivery_ids if i and picnic._DELIVERY_ID.match(str(i))), None)
            if first_id:
                detail_report, _ = _probe("bestelling: GET deliveries/<id> (de eerste levering)", lambda i=first_id: picnic._authed("GET", f"/deliveries/{i}"))
                probes.append(detail_report)

    # Routes die we nog niet gebruiken maar uit de community-libraries kennen. Zo zie je welke
    # uitbreidingen mogelijk zijn zonder dat er iets wordt gewijzigd.
    first = next(iter(picnic.products_from_search_page(page)), None) if page is not None else None
    candidates = [
        ("account: GET user (alleen de vorm)", "/user"),
        ("lijstjes: GET lists", "/lists"),
        ("winkel: GET my_store", "/my_store"),
        ("suggesties: GET suggest", f"/suggest?search_term={SEARCH_TERM}"),
    ]
    if first:
        candidates.append(
            ("productpagina: GET pages/product-details-page-root", f"/pages/product-details-page-root?id={first['id']}")
        )
    for name, path in candidates:
        report, result = _probe(name, lambda p=path: picnic._authed("GET", p))
        probes.append(report)
        if path.startswith("/pages/") and result is not None:
            probes.append({"name": f"{name}: namen en verwijzingen rond favorieten", "ok": True,
                           "info": {"favoriet-namen": favorite_hits(result), "verwijzingen": page_links(result)}})
    report, _ = _probe(
        "account: leveringen en toestemming voor aankopen (alleen aantallen en waar/onwaar)",
        lambda: picnic._authed("GET", "/user"),
        lambda user: {
            "completed_deliveries": user.get("completed_deliveries"),
            "total_deliveries": user.get("total_deliveries"),
            "PURCHASES_CATEGORY_CONSENT": (user.get("consent_decisions") or {}).get("PURCHASES_CATEGORY_CONSENT"),
            "check_general_consent": user.get("check_general_consent"),
            "functies over favorieten of aankopen": sorted(
                {t["name"] for t in user.get("feature_toggles") or [] if isinstance(t, dict) and isinstance(t.get("name"), str)
                 and re.search(r"favou?rit|purchase|saved|heart", t["name"], re.I)}
            )[:30],
        },
    )
    probes.append(report)
    if page is not None:
        probes.append({"name": "zoekpagina: namen en verwijzingen rond favorieten", "ok": True,
                       "info": {"favoriet-namen": favorite_hits(page), "verwijzingen": page_links(page)}})

    # Recepten: eerst de pagina's, dan de vorm van één receptonderdeel en van een receptpagina.
    recipe_page: Any = None
    pages: dict[str, Any] = {}
    for name, page_id in RECIPE_PAGES:
        report, result = _probe(name, lambda p=page_id: picnic._authed("GET", f"/pages/{p}", text=True), _page_info)
        probes.append(report)
        pages[page_id] = result
        if result is not None and not isinstance(result, str):
            tile = next(iter(recipe_tiles(result)), None)
            if tile is not None and recipe_page is None:
                recipe_page = (page_id, tile)
    if recipe_page:
        probes.append({"name": f"een receptonderdeel uit {recipe_page[0]} (ruwe vorm)", "ok": True, "shape": shape(recipe_page[1])})
        recipe_id = next(
            (data[key] for data in _contexts(recipe_page[1]) for key in ("recipe_id", "selling_group_id", "sellable_id") if isinstance(data.get(key), str)),
            "",
        )
        if recipe_id:
            report, details = _probe(
                "receptdetails: GET pages/selling-group-details-page",
                lambda: picnic._authed("GET", f"/pages/selling-group-details-page?selling_group_id={quote(recipe_id, safe='')}", text=True),
                _page_info,
            )
            pages["details"] = details
            probes.append(report)

    # Waar staan namen, ingrediënten en afbeeldingen? Alleen veldnamen en soorten onderdelen, geen inhoud.
    for key, label in (("saved-deep-dive-page-content", "opgeslagen"), ("cookbook-page-content", "kookboek"), ("details", "receptdetails")):
        page = pages.get(key)
        if isinstance(page, dict) and recipe_tiles(page):
            probes.append({"name": f"analysegegevens van de receptonderdelen ({label})", "ok": True, "shape": analytics_fields(page)})
            probes.append({"name": f"onderdelen in een receptonderdeel ({label}): soort en velden", "ok": True,
                           "shape": pml_vocabulary(recipe_tiles(page)[0])})

    # De tabbalk van de app: daar staat de pagina van de Favorieten-tab in.
    report, tabs = _probe(
        "tabbalk van de app: GET bootstrap (id, soort, titel, label en pagina per tab)",
        picnic.bootstrap_tabs,
        lambda found: {"tabs": found, "favorietenpagina": picnic.favorites_reference(found)},
    )
    probes.append(report)
    reference = picnic.favorites_reference(tabs or [])
    if reference:
        report, favorites_page = _probe(f"favorieten: GET pages/{reference}", lambda: picnic._authed("GET", f"/pages/{reference}", text=True), _page_info)
        probes.append(report)
        if isinstance(favorites_page, dict):
            probes.append({"name": "favorieten: de markering `saved` (vorm, aantallen per product)", "ok": True, "info": saved_report(favorites_page)})

        # Op de pagina van de tab staan misschien niet je favorieten (zonder boodschappen is het een lijst met
        # populaire producten). Kijk welke pagina's de app daarnaast kent, en of een productpagina zegt of het een favoriet is.
        for page_id in PURCHASE_PAGES:
            report, purchases = _probe(f"favorieten: GET pages/{page_id}", lambda p=page_id: picnic._authed("GET", f"/pages/{p}", text=True), _page_info)
            probes.append(report)
            if isinstance(purchases, dict):
                probes.append({"name": f"favorieten: de markering `saved` op {page_id}", "ok": True, "info": saved_report(purchases)})
        for page_id in (reference, "purchases-page-root", "purchased-deep-dive-page-content", "meals-purchase-page-root"):
            report, code_page = _probe(f"favorieten: hoe {page_id} zijn gegevens ophaalt", lambda p=page_id: picnic._authed("GET", f"/pages/{p}", text=True), code_report)
            probes.append(report)
        first_product = next(iter(picnic.products_from_page(favorites_page)), None) if isinstance(favorites_page, dict) else None
        if first_product:
            for name, page_id in (
                ("de bevestiging bij een favoriet weghalen", f"unfavorite-confirmation-dialog?sellable_id={first_product['id']}"),
                ("de bevestiging bij een favoriet weghalen (met presentatie)", f"unfavorite-confirmation-dialog?presentation-mode=MODAL_OVER_CONTEXT&sellable_id={first_product['id']}"),
                ("een productpagina", f"product-details-page-root?id={first_product['id']}"),
            ):
                report, code_page = _probe(f"favorieten: hoe {name} zijn gegevens ophaalt", lambda p=page_id: picnic._authed("GET", f"/pages/{p}"), code_report)
                probes.append(report)
        # De opbouw van de pagina van de tab, en de pagina's die zij zelf later laadt (`pageConfig`).
        if isinstance(favorites_page, dict):
            probes.append({"name": "favorieten: opbouw van de pagina van de tab (koppen en groepen tegels)", "ok": True,
                           "info": {"opbouw": page_outline(favorites_page)}})
            lazy_ids = list(dict.fromkeys(
                n["pageConfig"]["id"] for n in picnic._walk(favorites_page)
                if isinstance(n.get("pageConfig"), dict) and isinstance(n["pageConfig"].get("id"), str)
                and picnic._PAGE_REFERENCE.match(n["pageConfig"]["id"])))[:4]
            for lazy_id in lazy_ids:
                report, lazy_page = _probe(f"favorieten: later geladen pagina {_redact(lazy_id)}",
                                           lambda i=lazy_id: picnic._authed("GET", f"/pages/{i}", text=True), _page_info)
                probes.append(report)
                if isinstance(lazy_page, dict):
                    probes.append({"name": f"favorieten: opbouw en markering van {_redact(lazy_id)}", "ok": True,
                                   "info": {"opbouw": page_outline(lazy_page), "saved": saved_report(lazy_page)}})
        # Zet de server zelf in de hartjescode van een productpagina of het product favoriet is? Kijk bij een paar producten.
        sample = (picnic.products_from_page(favorites_page)[:8] if isinstance(favorites_page, dict) else [])
        flags: dict[str, Any] = {}
        for index, product in enumerate(sample):
            try:
                flags[f"product {index + 1}"] = like_constants(picnic._authed("GET", f"/pages/product-details-page-root?id={product['id']}"))
            except Exception as exc:  # noqa: BLE001 - een product dat niet laadt slaan we over
                flags[f"product {index + 1}"] = f"fout: {type(exc).__name__}"
        probes.append({"name": "favorieten: waar/onwaar in de hartjescode van de productpagina's (per product)", "ok": True, "info": flags})
        for index, product in enumerate(picnic.products_from_page(favorites_page)[:3] if isinstance(favorites_page, dict) else []):
            report, detail = _probe(f"favorieten: productpagina {index + 1}: velden over favorieten",
                                    lambda i=product["id"]: picnic._authed("GET", f"/pages/product-details-page-root?id={i}"),
                                    lambda data: {"waarden": favorite_values(data)})
            probes.append(report)

    # De hartjesknop leest `getGlobalState("sellable-favorites")`: een toestand die de app zelf vult, niet de pagina.
    # Misschien zit die in /bootstrap of komt hij van een eigen route; kijk naar beide.
    report, _ = _probe(
        "favorieten: de velden van /bootstrap naast de tabs (alleen de vorm)",
        lambda: picnic._authed("GET", "/bootstrap"),
        lambda data: {key: shape(value) for key, value in sorted(data.items()) if key != "tabs"} if isinstance(data, dict) else shape(data),
    )
    probes.append(report)
    for path in FAVORITE_ROUTES:
        report, _ = _probe(f"favorieten? GET {path}", lambda p=path: picnic._authed("GET", p))
        probes.append(report)

    # Favorieten: onbekend. Probeer de gokken; een pagina die bestaat, wordt met zijn vorm en verwijzingen gemeld.
    for page_id in FAVORITE_GUESSES:
        report, _ = _probe(f"favorieten? GET pages/{page_id}", lambda p=page_id: picnic._authed("GET", f"/pages/{p}", text=True), _page_info)
        probes.append(report)

    # Het antwoord van Picnic bij een fout kan van alles bevatten; in het rapport staan alleen de codes.
    for probe in probes:
        if not probe["ok"] and "error" in probe:
            probe["error"] = short_error(probe["error"])

    return {
        "note": "Alleen de vorm van de antwoorden: geen namen, adressen, prijzen of id's. Veilig te delen.",
        "linked": bool(picnic.configured_auth_key()),
        "probes": probes,
        "ok": sum(1 for probe in probes if probe["ok"]),
    }
