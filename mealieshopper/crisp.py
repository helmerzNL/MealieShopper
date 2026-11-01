"""Crisp: onofficiële app-API, op dezelfde manier gebruikt als de Crisp-app zelf.

Crisp heeft geen officiële API en geen webshop: bestellen gaat alleen in de app. Inloggen en de headers volgen het
open-source project `home-assistant-crisp` (NLthijs48, MIT). De routes voor zoeken, producten, recepten en het
mandje komen uit de broncode die in de openbare Android-app zit (zie docs/onderzoek-crisp.md). Ze kunnen zonder bericht
veranderen; `explore` laat dan zien welke velden de antwoorden nu hebben.

Inloggen gaat met een code per mail. We maken zelf een apparaat-id (`client_id`), zoals de app dat doet; Crisp koppelt
het account daaraan zodra de code klopt. Er is geen wachtwoord en het id verloopt niet. We bewaren het versleuteld.

`add_to_cart` schrijft direct in het echte mandje van Crisp. Er wordt nooit een bestelling geplaatst: de gebruiker
rondt dat zelf af in de app.
"""
from __future__ import annotations

import json
import re
import secrets
import string
import time
from typing import Any
from urllib.parse import quote

import requests

from . import about, auth, deals
from .i18n import tr
from .packs import assess_packs, normalize_quantity

API = "https://crispapp.nl/v1"
ORIGIN = "https://crispapp.nl"
HOST = "crispapp.nl"
# Zoals home-assistant-crisp: de app-versie van oktober 2026. Crisp kan een oude versie op den duur weigeren.
AGENT = "crisp/app/android/667"
COUNTRIES = ("nl", "be")
TIMEOUT = 30
PENDING_TTL_SECONDS = 900  # zo lang blijft een login die op de code uit de mail wacht geldig
CLIENT_ID_LENGTH = 20  # zelfde lengte en tekens als de app
CLIENT_ID_CHARS = string.ascii_letters + string.digits
SEARCH_LIMIT = 10  # zoveel vraagt de app per zoekopdracht
RECIPE_SEARCH_LIMIT = 30
SOURCE_URL = "https://crisp.nl/"
FROM = {"screenName": "MealieShopper"}  # waar een wijziging in het mandje vandaan komt (de app geeft hier haar scherm)
# Foto's zijn blobs op dezelfde host als de API: `https://crispapp.nl/blob/<id>?thumb=<maat>`. De app vraagt altijd een
# `thumb` (een macht van 2 tot 1024, daarboven een veelvoud van 512), anders komt het volledige origineel.
BLOB_PATH = "/blob/"
THUMB = 512  # kaarten en lijsten
THUMB_LARGE = 1024  # het receptdetail en de foto in het groot in de controlelijst

CLIENT_SECRET = "CRISP_CLIENT_ID"
COUNTRY_SECRET = "CRISP_COUNTRY"
PENDING_SECRET = "CRISP_PENDING_LOGIN"

_ID = re.compile(r"^\d{1,12}$")
_MARKUP = re.compile(r"\*\*|__")
_BLOB = re.compile(r"^[^\s:/?#]{1,200}$")  # een blob-id: geen adres, geen pad, geen schema
_THUMB = re.compile(r"([?&]thumb=)\d+")


class CrispAuthError(RuntimeError):
    """De koppeling is verlopen of geweigerd."""


class CrispLoginError(RuntimeError):
    """Crisp weigerde het e-mailadres of de code; de tekst is van onszelf en zegt wat te doen."""


def _not_linked() -> RuntimeError:
    return RuntimeError(
        tr(
            "Crisp-account niet gekoppeld. De beheerder koppelt het bij Instellingen > Koppelen > Crisp.",
            "Crisp account not linked. The admin links it under Settings > Link > Crisp.",
        )
    )


def _user_agent() -> str:
    return f"MealieShopper/{about.read_version()} (+https://github.com/helmerzNL/MealieShopper)"


def generate_client_id() -> str:
    """Een nieuw apparaat-id, zoals de app het maakt: 20 letters en cijfers."""
    return "".join(secrets.choice(CLIENT_ID_CHARS) for _ in range(CLIENT_ID_LENGTH))


def _call(method: str, path: str, client_id: str, body: Any = None) -> Any:
    """Eén verzoek aan Crisp met het apparaat-id als sleutel. Geeft het JSON-antwoord terug."""
    headers = {
        "Origin": ORIGIN,
        "Authorization": f"bearer {client_id}",
        "X-Crisp-Agent": AGENT,
        "User-Agent": _user_agent(),
        "Accept": "application/json",
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
    response = requests.request(method, API + path, headers=headers, json=body, timeout=TIMEOUT)
    if response.status_code in (401, 403):
        # Bewust niet de tekst van Crisp zelf: de aanroeper kiest een eigen, begrijpelijke melding.
        raise CrispAuthError(tr("Crisp weigerde de sessie", "Crisp refused the session"))
    if not response.ok:
        raise RuntimeError(f"{response.status_code}: {response.text[:500]}")
    try:
        return response.json() if response.content else None
    except ValueError as exc:
        raise RuntimeError(tr("Crisp gaf een antwoord in een onbekend formaat", "Crisp returned an answer in an unknown format")) from exc


# --- koppeling ---------------------------------------------------------------------------


def configured_client_id() -> str:
    return auth.get_secret(CLIENT_SECRET)


def country() -> str:
    value = (auth.get_secret(COUNTRY_SECRET) or "").strip().lower()
    return value if value in COUNTRIES else "nl"


def _authed(method: str, path: str, body: Any = None) -> Any:
    client_id = configured_client_id()
    if not client_id:
        raise _not_linked()
    try:
        return _call(method, path, client_id, body)
    except CrispAuthError as exc:
        raise CrispAuthError(
            tr(
                "Crisp-koppeling verlopen of geweigerd. Koppel opnieuw bij Instellingen > Koppelen > Crisp.",
                "Crisp link expired or refused. Link again under Settings > Link > Crisp.",
            )
        ) from exc


def _save_link(client_id: str, chosen_country: str) -> None:
    auth.set_secret(CLIENT_SECRET, client_id)
    auth.set_secret(COUNTRY_SECRET, chosen_country)
    auth.delete_secret(PENDING_SECRET)


def _pending() -> dict[str, str]:
    raw = auth.get_secret(PENDING_SECRET)
    try:
        pending = json.loads(raw) if raw else {}
    except ValueError:
        pending = {}
    if not isinstance(pending, dict) or not pending.get("clientId") or time.time() - float(pending.get("at") or 0) > PENDING_TTL_SECONDS:
        raise CrispLoginError(tr("Geen lopende Crisp-login. Vraag een nieuwe code aan.", "No Crisp login in progress. Request a new code."))
    return {key: str(pending.get(key) or "") for key in ("clientId", "email", "country")}


def start_login(email: Any, chosen_country: Any = "nl") -> dict[str, Any]:
    """Vraag Crisp om een code per mail. Is dit apparaat-id al ingelogd, dan is de koppeling meteen klaar."""
    email = str(email or "").strip()
    chosen_country = str(chosen_country or "nl").strip().lower()
    if not email or "@" not in email:
        raise ValueError(tr("Vul het e-mailadres van je Crisp-account in", "Enter the email address of your Crisp account"))
    if chosen_country not in COUNTRIES:
        raise ValueError(tr("Kies Nederland of België", "Choose the Netherlands or Belgium"))
    client_id = generate_client_id()
    data = _call("POST", "/user/login", client_id, {"email": email, "country": chosen_country})
    data = data if isinstance(data, dict) else {}
    if data.get("id"):
        _save_link(client_id, chosen_country)
        return {"connected": True, "codeRequired": False}
    if data.get("error"):
        if data.get("error") == "webLogin":
            # Zo meldt Crisp een e-mailadres zonder account: de app stuurt je dan naar het aanmelden.
            raise CrispLoginError(
                tr(
                    "Crisp kent geen account met dit e-mailadres in dit land. Maak eerst een account aan in de Crisp-app.",
                    "Crisp has no account with this email address in this country. Create an account in the Crisp app first.",
                )
            )
        raise CrispLoginError(tr("Crisp weigerde de login: controleer het e-mailadres en het land", "Crisp refused the login: check the email address and the country"))
    auth.set_secret(PENDING_SECRET, json.dumps({"clientId": client_id, "email": email, "country": chosen_country, "at": time.time()}))
    return {"connected": False, "codeRequired": True}


def verify_login(code: Any) -> dict[str, Any]:
    """Bevestig de login met de code uit de mail; daarna hoort het apparaat-id bij het account."""
    code = str(code or "").strip()
    if not code:
        raise ValueError(tr("Geen code opgegeven", "No code provided"))
    pending = _pending()
    data = _call("POST", "/user/login", pending["clientId"], {"email": pending["email"], "code": code, "country": pending["country"]})
    data = data if isinstance(data, dict) else {}
    if data.get("id"):
        _save_link(pending["clientId"], pending["country"])
        return {"connected": True}
    if data.get("error"):
        raise CrispLoginError(tr("Crisp weigerde de code: controleer hem of vraag een nieuwe aan", "Crisp refused the code: check it or request a new one"))
    raise RuntimeError(tr("Crisp gaf geen account terug", "Crisp returned no account"))


def unlink() -> dict[str, Any]:
    """Ontkoppel. We melden het apparaat bij Crisp af zoals de app (als dat lukt) en wissen het id."""
    client_id = configured_client_id()
    if client_id:
        try:
            _call("POST", "/user/login", client_id, {"logout": True})
        except Exception:  # noqa: BLE001 - ontkoppelen moet ook lukken als Crisp niet bereikbaar is
            pass
    for key in (CLIENT_SECRET, COUNTRY_SECRET, PENDING_SECRET):
        auth.delete_secret(key)
    return {"connected": False}


def status(verify: bool = False) -> dict[str, Any]:
    """Is er een koppeling, en klopt die nog? Geeft geen persoonsgegevens terug."""
    connected = bool(configured_client_id())
    result: dict[str, Any] = {"connected": connected, "codePending": False, "country": country() if connected else None}
    try:
        _pending()
        result["codePending"] = True
    except CrispLoginError:
        pass
    if verify and connected:
        try:
            user = _authed("GET", "/user")
            if not (isinstance(user, dict) and user.get("id")):
                raise CrispAuthError(tr("Crisp ziet dit apparaat niet meer als ingelogd. Koppel opnieuw.", "Crisp no longer sees this device as logged in. Link again."))
            result["verified"] = True
        except Exception as exc:  # noqa: BLE001 - de reden tonen we aan de beheerder
            result["verified"] = False
            # Onze eigen tekst bij een verlopen koppeling; bij andere fouten alleen het type, want het bericht kan een
            # stuk van het antwoord van Crisp bevatten.
            result["error"] = str(exc) if isinstance(exc, CrispAuthError) else type(exc).__name__
    return result


# --- producten ---------------------------------------------------------------------------


def _money(value: Any) -> float | None:
    """Crisp geeft bedragen als tekst met twee decimalen (`"2.49"`)."""
    if isinstance(value, bool):
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _image(value: Any, thumb: int = THUMB) -> str | None:
    """Het adres van de eerste foto, zoals de app het maakt (`BlobImage` en `resolveBlobUrl`).

    Crisp geeft meestal een blob-id; daar hoort `https://crispapp.nl/blob/<id>?thumb=<maat>` bij. Een volledig adres blijft
    zoals het is en een pad (`/asset/...`) komt op crispapp.nl. Al het andere (zoals `javascript:`) geeft geen foto.
    """
    if isinstance(value, list):
        value = next((v for v in value if v), None)
    if isinstance(value, dict):
        value = value.get("legacyParse")  # oudere vorm van een blob in de app (`blobParse`)
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    if value.startswith("https://"):
        return value
    if value.startswith("/"):
        return ORIGIN + value
    if _BLOB.match(value):
        return f"{ORIGIN}{BLOB_PATH}{quote(value, safe='')}?thumb={thumb}"
    return None


def _resized(url: Any, thumb: int) -> Any:
    """Dezelfde blob-foto in een andere maat; een ander adres blijft zoals het is."""
    if isinstance(url, str) and url.startswith(ORIGIN + BLOB_PATH):
        return _THUMB.sub(lambda match: f"{match.group(1)}{thumb}", url)
    return url


def _text(value: Any) -> str:
    """Een tekstveld, of de titel als Crisp er een object van maakt."""
    if isinstance(value, dict):
        value = value.get("title") or value.get("label") or value.get("name")
    return value.strip() if isinstance(value, str) else ""


def _promo(product: dict[str, Any], price: float | None) -> dict[str, Any] | None:
    promotion = product.get("promotion") if isinstance(product.get("promotion"), dict) else {}
    label = "" if promotion.get("isEmptyPromo") else _text(promotion.get("label")) or _text(promotion.get("title")) or _text(promotion.get("descrShort"))
    before = _money(product.get("basePrice"))
    before = before if before is not None and price is not None and before > price else None
    if not label and before is None:
        return None
    return {"label": label or None, "priceBefore": before, "validFrom": None, "validUntil": None}


def product_summary(product: dict[str, Any]) -> dict[str, Any]:
    """Een product van Crisp in de vorm die de rest van MealieShopper kent (zoals Picnic en Jumbo)."""
    price = _money(product.get("price"))
    promo = _promo(product, price)
    supplier = product.get("supplier") if isinstance(product.get("supplier"), dict) else {}
    summary: dict[str, Any] = {
        "id": str(product.get("id") or ""),
        "title": _text(product.get("title")),
        "brand": _text(supplier.get("name")) or None,
        # Zonder gewicht is het een product per stuk; zo toont de app het ook.
        "unitSize": _text(product.get("weightIndication")) or "per stuk",
        "price": price,
        "image": _image(product.get("images")) or _image(product.get("image")),
        "isBonus": promo is not None,
        "url": None,  # Crisp heeft geen webwinkel waar een product op staat
        "available": product.get("deliverable") is not False and not product.get("unpublished"),
    }
    if promo:
        summary["promo"] = promo
    return summary


def _items(data: Any) -> list[dict[str, Any]]:
    items = data.get("data") if isinstance(data, dict) else data
    return [item for item in items or [] if isinstance(item, dict)]


def search_products(query: str, limit: int = 5) -> list[dict[str, Any]]:
    term = str(query or "").strip()
    if not term:
        return []
    data = _authed("POST", "/search", {"query": term, "cursor": False, "limit": max(SEARCH_LIMIT, limit)})
    products = [
        product_summary(item)
        for item in _items(data)
        if item.get("itemType", "product") == "product" and item.get("id") and item.get("title")
    ]
    return [product for product in products if product["available"]][: max(1, limit)]


def search_product(query: str) -> dict[str, Any] | None:
    found = search_products(query, 1)
    return found[0] if found else None


def candidate(product: dict[str, Any], quantity: Any, unit: Any) -> dict[str, Any]:
    """Zelfde vorm als de kandidaten die de AH-controlelijst toont."""
    packs, doubt = assess_packs(quantity, unit, product.get("unitSize"))
    return {
        "productId": product["id"],
        "title": product["title"],
        "brand": product.get("brand"),
        "unitSize": product.get("unitSize"),
        "price": product.get("price"),
        "image": product.get("image"),
        # Crisp heeft geen productpagina, dus de foto is wat je ziet; in het groot in een grotere maat.
        "imageLarge": _resized(product.get("image"), THUMB_LARGE),
        "isBonus": bool(product.get("isBonus")),
        "deal": deals.candidate_deal("crisp", product),
        "url": None,
        "packs": packs,
        "doubt": doubt,
    }


# --- mandje ------------------------------------------------------------------------------


def _basket_products(basket: Any) -> list[tuple[str, dict[str, Any]]]:
    """De producten in het mandje: Crisp geeft een object met het product-id als sleutel."""
    products = basket.get("products") if isinstance(basket, dict) else None
    if isinstance(products, dict):
        pairs = [(str(key), value) for key, value in products.items()]
    else:
        pairs = [(str((value or {}).get("id") or ""), value) for value in products or []]
    return [(key, value) for key, value in pairs if key and isinstance(value, dict)]


def _count(line: dict[str, Any]) -> int:
    try:
        return max(0, int(line.get("count") or 0))
    except (TypeError, ValueError):
        return 0


def cart_summary(basket: Any) -> dict[str, Any]:
    """Het Crisp-mandje in een compacte vorm, zoals het mandje van Jumbo."""
    items = []
    for product_id, line in _basket_products(basket):
        quantity = _count(line)
        if not quantity:
            continue
        price = _money(line.get("totalPrice"))
        if price is None and _money(line.get("price")) is not None:
            price = round(_money(line.get("price")) * quantity, 2)
        items.append(
            {
                "id": product_id,
                "title": _text(line.get("title")),
                "quantity": quantity,
                "unitSize": _text(line.get("weightIndication")) or None,
                "price": price,
                "image": _image(line.get("images")) or _image(line.get("image")),
            }
        )
    prices = [item["price"] for item in items if item["price"] is not None]
    basket = basket if isinstance(basket, dict) else {}
    return {
        "items": items,
        "totalCount": sum(item["quantity"] for item in items),
        "totalPrice": round(sum(prices), 2) if prices else None,
        "checkoutTotal": _money(basket.get("payPrice")),  # wat je in de app betaalt, met bezorgkosten
    }


def _counts(basket: Any) -> dict[str, int]:
    return {product_id: _count(line) for product_id, line in _basket_products(basket)}


def get_cart() -> dict[str, Any]:
    return cart_summary(_authed("GET", "/basket/main"))


def add_to_cart(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Zet producten in het Crisp-winkelmandje. Plaatst nooit een bestelling.

    Eén verzoek voor alles, met het nieuwe aantal per product (wat er al lag plus wat erbij komt), zoals de app een
    lijst producten tegelijk toevoegt. Daarna vergelijken we per product of het aantal echt is gestegen: een product
    dat Crisp nu niet kan leveren, blijft staan en melden we terug.
    """
    wanted: dict[str, int] = {}
    for item in items:
        product_id = str(item.get("productId") or "").strip()
        if _ID.match(product_id):
            wanted[product_id] = wanted.get(product_id, 0) + normalize_quantity(item.get("quantity"))
    if not wanted:
        return {"added": [], "failed": [], "cart": None}
    before = _counts(_authed("GET", "/basket/main"))
    changes = {product_id: {"count": before.get(product_id, 0) + count, "from": FROM} for product_id, count in wanted.items()}
    basket = _authed("PUT", "/basket/main", {"products": changes})
    after = _counts(basket)
    added: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for product_id, count in wanted.items():
        grew = after.get(product_id, 0) - before.get(product_id, 0)
        if grew > 0:
            added.append({"productId": product_id, "quantity": min(grew, count)})
        else:
            failed.append({"productId": product_id, "error": tr("Crisp zette dit product niet in het mandje (niet leverbaar?)", "Crisp did not put this product in the basket (not available?)")})
    return {"added": added, "failed": failed, "cart": cart_summary(basket)}


# --- recepten ----------------------------------------------------------------------------


def _minutes(recipe: dict[str, Any]) -> int | None:
    methods = recipe.get("cookingMethods") if isinstance(recipe.get("cookingMethods"), dict) else {}
    short = methods.get("short") if isinstance(methods.get("short"), dict) else {}
    minutes = short.get("minutes")
    return minutes if isinstance(minutes, int) and not isinstance(minutes, bool) and minutes > 0 else None


def _portions(recipe: dict[str, Any]) -> int | None:
    portions = recipe.get("portions")
    return portions if isinstance(portions, int) and not isinstance(portions, bool) and portions > 0 else None


def _share_url(recipe: dict[str, Any]) -> str | None:
    url = recipe.get("shareUrl")
    return url if isinstance(url, str) and url.startswith("https://") else None


def recipe_summary(recipe: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(recipe.get("id") or ""),
        "name": _text(recipe.get("title")),
        "image": _image(recipe.get("images")) or _image(recipe.get("image")),
        "minutes": _minutes(recipe),
        "portions": _portions(recipe),
        "tags": [tag for tag in (_text(recipe.get("subTitle")),) if tag],
    }


def search_recipes(query: Any) -> dict[str, Any]:
    """Zoek in de recepten van Crisp, zoals het receptenzoeken in de app. Bundels (meerdere recepten) laten we weg."""
    term = str(query or "").strip()
    if not term:
        raise ValueError(tr("Zoekterm ontbreekt", "Search term missing"))
    data = _authed("POST", "/search/recipe", {"query": term, "cursor": False, "limit": RECIPE_SEARCH_LIMIT})
    recipes = [
        recipe_summary(item)
        for item in _items(data)
        if item.get("itemType", "recipe") == "recipe" and (item.get("type") or "") in ("", "recipe") and item.get("id")
    ]
    recipes = [recipe for recipe in recipes if recipe["name"]]
    return {"recipes": recipes, "total": len(recipes)}


def _recipe_id(value: Any) -> str:
    recipe_id = str(value or "").strip()
    if not _ID.match(recipe_id):
        raise ValueError(tr("Geen geldig recept", "Not a valid recipe"))
    return recipe_id


def _plain(value: Any) -> str:
    return re.sub(r"\s+", " ", _MARKUP.sub("", str(value or ""))).strip()


def recipe_details(recipe: dict[str, Any]) -> dict[str, Any]:
    """Eén recept met ingrediënten en bereiding, in de vorm die het receptdetail van MealieShopper toont."""
    ingredients = []
    for group in recipe.get("ingredientGroups") or []:
        if not isinstance(group, dict):
            continue
        for ingredient in group.get("ingredients") or []:
            if not isinstance(ingredient, dict):
                continue
            name = _plain(ingredient.get("formattedName"))
            if not name:
                continue
            quantity = _plain(ingredient.get("formattedQuantity"))
            product = ingredient.get("product") if isinstance(ingredient.get("product"), dict) else {}
            ingredients.append(
                {
                    "line": f"{quantity} {name}".strip(),
                    "productId": str(ingredient.get("productId") or product.get("id") or "") or None,
                    "addSelf": group.get("groupType") == "addSelf" or ingredient.get("type") == "addSelf",
                }
            )
    steps = [_plain(step.get("instruction") if isinstance(step, dict) else step) for step in recipe.get("instructions") or []]
    return {
        "id": str(recipe.get("id") or ""),
        "name": _text(recipe.get("title")),
        "description": _plain(recipe.get("description")) or _text(recipe.get("subTitle")),
        "image": _image(recipe.get("images"), THUMB_LARGE) or _image(recipe.get("image"), THUMB_LARGE),
        "portions": _portions(recipe),
        "minutes": _minutes(recipe),
        "ingredients": ingredients,
        "steps": [step for step in steps if step],
        "url": _share_url(recipe),
    }


def get_recipe(recipe_id: Any) -> dict[str, Any]:
    recipe = _authed("GET", f"/recipe/{_recipe_id(recipe_id)}")
    if not isinstance(recipe, dict) or not recipe.get("title"):
        raise RuntimeError(tr("Crisp gaf het recept in een onbekend formaat", "Crisp returned the recipe in an unknown format"))
    return recipe_details(recipe)


def jsonld(recipe: dict[str, Any]) -> dict[str, Any]:
    """Het recept als schema.org Recipe; Mealie importeert dat zonder een webpagina op te halen."""
    lines = [item["line"] for item in recipe["ingredients"]]
    steps = [{"@type": "HowToStep", "text": text} for text in recipe["steps"]]
    if not lines and not steps:
        raise RuntimeError(tr("Dit Crisp-recept heeft geen ingrediënten of bereiding", "This Crisp recipe has no ingredients or steps"))
    data: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "Recipe",
        "name": recipe["name"],
        "recipeIngredient": lines,
        "recipeInstructions": steps,
    }
    if recipe.get("description"):
        data["description"] = recipe["description"]
    if recipe.get("image"):
        data["image"] = [recipe["image"]]
    if recipe.get("portions"):
        data["recipeYield"] = str(recipe["portions"])
    if recipe.get("minutes"):
        data["totalTime"] = f"PT{recipe['minutes']}M"
    return data


def source_url(recipe_id: Any) -> str:
    """Mealie bewaart waar een recept vandaan komt; Crisp-recepten hebben geen webpagina, dus de site van Crisp."""
    return f"{SOURCE_URL}#recipe-{_recipe_id(recipe_id)}"


# --- verkenner ---------------------------------------------------------------------------


def _keys(value: Any) -> list[str]:
    return sorted(value) if isinstance(value, dict) else []


def _failure(exc: Exception) -> str:
    """Wat er misging, zonder de tekst van Crisp: onze eigen melding, de HTTP-status of alleen het soort fout."""
    if isinstance(exc, CrispAuthError):
        return str(exc)
    status_code = re.match(r"^(\d{3}):", str(exc))
    return f"HTTP {status_code.group(1)}" if status_code else type(exc).__name__


def explore() -> dict[str, Any]:
    """Roep de routes aan die MealieShopper gebruikt en meld welke werken en welke velden erin zitten.

    Alleen lezen: er wordt niets in het mandje gezet. Het rapport bevat alleen veldnamen en aantallen, geen namen,
    adressen, prijzen of id's, en is daarom veilig te delen.
    """
    report: dict[str, Any] = {"linked": bool(configured_client_id()), "agent": AGENT, "checks": []}
    if not report["linked"]:
        return report

    def check(name: str, route: str, path: str, body: Any = None, inspect: Any = None) -> Any:
        # `route` is de route zonder id's, zodat het rapport geen product- of recept-id bevat.
        method = route.split(" ", 1)[0]
        entry: dict[str, Any] = {"name": name, "route": route}
        try:
            data = _authed(method, path, body)
            entry["ok"] = True
            entry["fields"] = _keys(data)
            if inspect:
                entry.update(inspect(data))
        except Exception as exc:  # noqa: BLE001 - het rapport meldt wat er misging, zonder de tekst van Crisp
            entry["ok"] = False
            entry["error"] = _failure(exc)
            data = None
        report["checks"].append(entry)
        return data

    def first(items: list[dict[str, Any]], kind: str) -> dict[str, Any]:
        return next((item for item in items if item.get("itemType", kind) == kind), {})

    check("user", "GET /user", "/user", inspect=lambda data: {"loggedIn": bool(isinstance(data, dict) and data.get("id"))})
    found = check("search", "POST /search", "/search", {"query": "melk", "cursor": False, "limit": SEARCH_LIMIT},
                  inspect=lambda data: {"count": len(_items(data)), "itemFields": _keys(first(_items(data), "product"))})
    product = first(_items(found), "product")
    if product.get("id"):
        check("product", "GET /product/:id", f"/product/{product['id']}")
    check("basket", "GET /basket/main", "/basket/main", inspect=lambda data: {
        "productCount": len(_basket_products(data)),
        "productFields": _keys(next((line for _, line in _basket_products(data)), {})),
    })
    recipes = check("recipeSearch", "POST /search/recipe", "/search/recipe", {"query": "pasta", "cursor": False, "limit": SEARCH_LIMIT},
                    inspect=lambda data: {"count": len(_items(data)), "itemFields": _keys(first(_items(data), "recipe"))})
    recipe = first(_items(recipes), "recipe")
    if recipe.get("id"):
        def recipe_shape(data: Any) -> dict[str, Any]:
            groups = data.get("ingredientGroups") if isinstance(data, dict) else None
            group = groups[0] if isinstance(groups, list) and groups and isinstance(groups[0], dict) else {}
            ingredient = (group.get("ingredients") or [{}])[0]
            steps = data.get("instructions") if isinstance(data, dict) else None
            return {
                "groupFields": _keys(group),
                "ingredientFields": _keys(ingredient),
                "instructionFields": _keys(steps[0]) if isinstance(steps, list) and steps else [],
            }
        check("recipe", "GET /recipe/:id", f"/recipe/{recipe['id']}", inspect=recipe_shape)
    report["working"] = sum(1 for entry in report["checks"] if entry.get("ok"))
    return report
