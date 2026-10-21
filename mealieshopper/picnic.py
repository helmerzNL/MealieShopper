"""Picnic: onofficiële app-API, op dezelfde manier gebruikt als de Picnic-app zelf.

Picnic heeft geen officiële API. De routes en headers hieronder volgen de community-libraries
`python-picnic-api2` en `picnic-api` en kunnen zonder bericht veranderen; `picnic_explore.py`
toont in dat geval welke vorm de antwoorden nu hebben.

Inloggen werkt met e-mail en wachtwoord (het wachtwoord gaat als MD5 over de lijn, zoals de app
dat doet) en daarna meestal een code per sms. We bewaren alleen de auth-key die Picnic teruggeeft,
nooit het wachtwoord. Picnic kan die key bij elk antwoord vernieuwen; die nieuwe waarde bewaren we.

In tegenstelling tot AH schrijft `add_to_cart` direct in het echte winkelmandje van Picnic. Er wordt
nooit een bestelling geplaatst: de gebruiker rondt dat zelf af in de app.
"""
from __future__ import annotations

import json
import time
from hashlib import md5
from os import environ
from typing import Any
from urllib.parse import quote

import requests

from . import auth
from .i18n import tr
from .packs import MAX_CART_QUANTITY as MAX_QUANTITY, assess_packs, normalize_quantity

API_VERSION = "15"
BASE_URL = "https://storefront-prod.{country}.picnicinternational.com/api/{version}"
COUNTRIES = {"NL", "DE", "FR"}
AUTH_HEADER = "x-picnic-auth"
CLIENT_ID = 30100
HEADERS = {
    "User-Agent": "okhttp/4.9.0",
    "Content-Type": "application/json; charset=UTF-8",
    "x-picnic-agent": "30100;1.206.1-#15408",
    "x-picnic-did": "598F770380CA54B6",
}
AUTH_ERROR_CODES = {"AUTH_ERROR", "AUTH_INVALID_CRED"}
TWO_FACTOR_CHANNELS = {"SMS", "EMAIL"}
PENDING_TTL_SECONDS = 600  # zo lang blijft een login die op de sms-code wacht geldig

AUTH_KEY_SECRET = "PICNIC_AUTH_KEY"
PENDING_SECRET = "PICNIC_PENDING_LOGIN"


class PicnicAuthError(RuntimeError):
    """De login is geweigerd of de koppeling is verlopen."""


class Picnic2FAError(RuntimeError):
    """De tweestapscode is onjuist of verlopen."""


def country_code() -> str:
    code = environ.get("PICNIC_COUNTRY_CODE", "NL").strip().upper()
    return code if code in COUNTRIES else "NL"


def base_url() -> str:
    return BASE_URL.format(country=country_code().lower(), version=API_VERSION)


def _not_linked() -> RuntimeError:
    return RuntimeError(
        tr(
            "Picnic account niet gekoppeld. De beheerder koppelt het bij Beheer > Picnic.",
            "Picnic account not linked. The admin links it under Admin > Picnic.",
        )
    )


def _call(
    method: str, path: str, *, token: str | None = None, body: Any = None, text: bool = False
) -> tuple[Any, str | None]:
    """Eén verzoek aan Picnic. Geeft (antwoord, nieuwe auth-key uit de headers) terug.

    Met `text` komt een antwoord dat geen JSON is (sommige pagina's) als tekst terug in plaats van als None.
    """
    headers = dict(HEADERS)
    if token:
        headers[AUTH_HEADER] = token
    response = requests.request(
        method, base_url() + path, headers=headers, json=body, timeout=30
    )
    new_token = response.headers.get(AUTH_HEADER)

    data: Any = None
    if response.content:
        try:
            data = response.json()
        except ValueError:
            data = response.text if text else None
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict) and error.get("code") in AUTH_ERROR_CODES:
        # Bewust niet de tekst van Picnic zelf: de aanroeper kiest een eigen, begrijpelijke melding.
        raise PicnicAuthError(tr("Picnic weigerde de sessie", "Picnic refused the session"))
    if not response.ok:
        raise RuntimeError(f"{response.status_code}: {response.text[:500]}")
    return data, new_token


# --- koppeling ---------------------------------------------------------------------------


def configured_auth_key() -> str:
    # Picnic vernieuwt de key bij gebruik en we bewaren de nieuwste. Een omgevingsvariabele als
    # startwaarde is er niet: de key komt uit een login met sms-code en is dus niet vooraf te maken.
    return auth.get_secret(AUTH_KEY_SECRET)


def save_auth_key(key: str) -> None:
    value = str(key or "").strip()
    if not value:
        raise RuntimeError(tr("Picnic gaf geen sessie terug", "Picnic returned no session"))
    auth.set_secret(AUTH_KEY_SECRET, value)


def _authed(method: str, path: str, *, body: Any = None, text: bool = False) -> Any:
    token = configured_auth_key()
    if not token:
        raise _not_linked()
    try:
        data, new_token = _call(method, path, token=token, body=body, text=text)
    except PicnicAuthError as exc:
        raise PicnicAuthError(
            tr(
                "Picnic-koppeling verlopen of geweigerd. Koppel opnieuw bij Beheer > Picnic.",
                "Picnic link expired or refused. Link again under Admin > Picnic.",
            )
        ) from exc
    if new_token and new_token != token:
        save_auth_key(new_token)
    return data


def _save_pending(token: str) -> None:
    auth.set_secret(PENDING_SECRET, json.dumps({"token": token, "at": time.time()}))


def _pending_token() -> str:
    raw = auth.get_secret(PENDING_SECRET)
    try:
        pending = json.loads(raw) if raw else {}
    except ValueError:
        pending = {}
    token = str(pending.get("token") or "")
    if not token or time.time() - float(pending.get("at") or 0) > PENDING_TTL_SECONDS:
        raise RuntimeError(
            tr(
                "Geen lopende Picnic-login. Log opnieuw in.",
                "No Picnic login in progress. Log in again.",
            )
        )
    return token


def login(email: str, password: str) -> dict[str, Any]:
    """Log in. Vraagt Picnic om een tweestapscode, dan is `twoFactorRequired` waar."""
    email = str(email or "").strip()
    if not email or not password:
        raise ValueError(
            tr("E-mailadres en wachtwoord zijn verplicht", "Email address and password are required")
        )
    secret = md5(password.encode("utf-8")).hexdigest()
    try:
        data, token = _call(
            "POST",
            "/user/login",
            body={"key": email, "secret": secret, "client_id": CLIENT_ID},
        )
    except PicnicAuthError as exc:
        raise PicnicAuthError(
            tr("Picnic weigerde de login: controleer e-mailadres en wachtwoord", "Picnic refused the login: check email address and password")
        ) from exc
    if isinstance(data, dict) and data.get("second_factor_authentication_required") is True:
        if not token:
            raise RuntimeError(tr("Picnic gaf geen sessie terug", "Picnic returned no session"))
        _save_pending(token)
        return {"connected": False, "twoFactorRequired": True}
    save_auth_key(token or "")
    auth.delete_secret(PENDING_SECRET)
    return {"connected": True, "twoFactorRequired": False}


def request_2fa_code(channel: str = "SMS") -> None:
    channel = str(channel or "SMS").strip().upper()
    if channel not in TWO_FACTOR_CHANNELS:
        raise ValueError(tr("Onbekend kanaal voor de code", "Unknown channel for the code"))
    _two_factor("/user/2fa/generate", {"channel": channel})


def verify_2fa(code: str) -> dict[str, Any]:
    code = str(code or "").strip()
    if not code:
        raise ValueError(tr("Geen code opgegeven", "No code provided"))
    token = _pending_token()
    new_token = _two_factor("/user/2fa/verify", {"otp": code}, token=token)
    save_auth_key(new_token or token)
    auth.delete_secret(PENDING_SECRET)
    return {"connected": True}


def _two_factor(path: str, body: dict[str, Any], *, token: str | None = None) -> str | None:
    token = token or _pending_token()
    data, new_token = _call("POST", path, token=token, body=body)
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict) and error.get("code"):
        raise Picnic2FAError(
            tr(
                "Picnic weigerde de code: controleer hem of vraag een nieuwe aan",
                "Picnic refused the code: check it or request a new one",
            )
        )
    return new_token


def unlink() -> dict[str, Any]:
    auth.delete_secret(AUTH_KEY_SECRET)
    auth.delete_secret(PENDING_SECRET)
    return {"connected": False}


def status(verify: bool = False) -> dict[str, Any]:
    """Is er een koppeling, en klopt die nog? Geeft geen persoonsgegevens terug."""
    result: dict[str, Any] = {
        "connected": bool(configured_auth_key()),
        "twoFactorPending": False,
    }
    try:
        _pending_token()
        result["twoFactorPending"] = True
    except RuntimeError:
        pass
    if verify and result["connected"]:
        try:
            _authed("GET", "/user")
            result["verified"] = True
        except Exception as exc:  # noqa: BLE001 - de reden tonen we aan de beheerder
            result["verified"] = False
            # Onze eigen tekst bij een verlopen koppeling; bij andere fouten alleen het type, want het
            # bericht kan een stuk van Picnics antwoord bevatten.
            result["error"] = str(exc) if isinstance(exc, PicnicAuthError) else type(exc).__name__
    return result


# --- producten ---------------------------------------------------------------------------


def _walk(node: Any):
    """Alle dicts in een JSON-boom, dieper en dieper. Picnic levert zoekresultaten als widgetboom."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def image_url(image_id: Any, size: str = "medium") -> str | None:
    """Het adres van een afbeelding. Receptafbeeldingen hebben een naamruimte (`recipes/<hash>`), dus de slash blijft."""
    if not image_id:
        return None
    return (
        f"https://storefront-prod.{country_code().lower()}.picnicinternational.com"
        f"/static/images/{quote(str(image_id), safe='/')}/{quote(size, safe='')}.png"
    )


def _euros(cents: Any) -> float | None:
    return round(cents / 100, 2) if isinstance(cents, (int, float)) else None


def _product(unit: dict[str, Any]) -> dict[str, Any] | None:
    product_id = unit.get("id")
    if not product_id or not unit.get("name"):
        return None
    decorators = [d for d in unit.get("decorators") or [] if isinstance(d, dict)]
    price = _euros(unit.get("display_price"))
    return {
        "id": str(product_id),
        "title": str(unit["name"]),
        "brand": None,  # de zoektegel noemt het merk niet apart; het zit in de naam
        "unitSize": unit.get("unit_quantity"),
        "price": price,
        "image": image_url(unit.get("image_id")),
        "isBonus": any("PROMO" in str(d.get("type", "")).upper() for d in decorators)
        or bool(unit.get("price_ranges")),
        "maxCount": unit.get("max_count"),
    }


def products_from_search_page(page: Any) -> list[dict[str, Any]]:
    """Haal de producten uit het zoekresultaat van Picnic (een boom met SELLING_UNIT_TILE-knopen)."""
    seen: set[str] = set()
    products: list[dict[str, Any]] = []
    for node in _walk(page.get("body") if isinstance(page, dict) else None):
        unit = node.get("sellingUnit") if node.get("type") == "SELLING_UNIT_TILE" else None
        product = _product(unit) if isinstance(unit, dict) else None
        if product and product["id"] not in seen:
            seen.add(product["id"])
            products.append(product)
    return products


def search_products(query: str, limit: int = 5) -> list[dict[str, Any]]:
    term = str(query or "").strip()
    if not term:
        return []
    page = _authed("GET", f"/pages/search-page-results?search_term={quote(term)}")
    return products_from_search_page(page)[: max(1, limit)]


def search_product(query: str) -> dict[str, Any] | None:
    found = search_products(query, 1)
    return found[0] if found else None


def candidate(product: dict[str, Any], quantity: Any, unit: Any) -> dict[str, Any]:
    """Zelfde vorm als de kandidaten die de AH-controlelijst toont."""
    packs, doubt = assess_packs(quantity, unit, product.get("unitSize"))
    if isinstance(product.get("maxCount"), int) and product["maxCount"] > 0:
        packs = min(packs, product["maxCount"])
    return {
        "productId": product["id"],
        "title": product["title"],
        "brand": product.get("brand"),
        "unitSize": product.get("unitSize"),
        "price": product.get("price"),
        "image": product.get("image"),
        "isBonus": bool(product.get("isBonus")),
        "url": None,  # Picnic heeft geen webwinkel waar een product op staat
        "packs": packs,
        "doubt": doubt,
    }


# --- mandje en leveringen -------------------------------------------------------------------


def _line_quantity(line: dict[str, Any]) -> int:
    for article in line.get("items") or []:
        for decorator in (article or {}).get("decorators") or []:
            if isinstance(decorator, dict) and decorator.get("type") == "QUANTITY":
                return max(1, int(decorator.get("quantity") or 1))
    return 1


def _cents(value: Any) -> int | float | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _line_prices(line: dict[str, Any]) -> tuple[float | None, float | None]:
    """(prijs die je betaalt, prijs vóór korting of None) van een regel in het mandje.

    Picnic geeft twee bedragen per regel: `display_price` en `price`. Bij een aanbieding of
    lidmaatschapskorting verschilt het ene van het andere; het lagere is wat je betaalt, het hogere de
    prijs zonder korting. Zo blijven de regels optellen tot het totaal, welk veld Picnic ook gebruikt.
    """
    amounts = [c for c in (_cents(line.get("display_price")), _cents(line.get("price"))) if c is not None]
    if not amounts:
        return None, None
    paid, listed = min(amounts), max(amounts)
    return _euros(paid), _euros(listed) if listed > paid else None


def _availability(cart: dict[str, Any]) -> dict[str, bool]:
    """Product-id -> is het nu leverbaar, uit de analysegegevens die Picnic bij het mandje meegeeft."""
    context = cart.get("analytics_context_data") if isinstance(cart.get("analytics_context_data"), dict) else {}
    return {
        str(entry["product_id"]): bool(entry.get("is_available"))
        for entry in context.get("items_list") or []
        if isinstance(entry, dict) and entry.get("product_id") and "is_available" in entry
    }


def cart_summary(cart: Any) -> dict[str, Any]:
    """Het Picnic-mandje in een compacte vorm."""
    cart = cart if isinstance(cart, dict) else {}
    available = _availability(cart)
    items = []
    for line in cart.get("items") or []:
        if not isinstance(line, dict):
            continue
        article = next((a for a in line.get("items") or [] if isinstance(a, dict)), {})
        image_ids = article.get("image_ids") or []
        paid, listed = _line_prices(line)
        max_count = article.get("max_count")
        items.append(
            {
                "id": article.get("id"),
                "title": article.get("name"),
                "quantity": _line_quantity(line),
                "unitSize": article.get("unit_quantity"),
                "price": paid,
                "listPrice": listed,
                "available": available.get(str(article.get("id"))),
                "maxCount": max_count if isinstance(max_count, int) and max_count > 0 else None,
                "image": image_url(image_ids[0]) if image_ids else None,
            }
        )
    return {
        "items": items,
        "totalCount": cart.get("total_count"),
        "totalPrice": _euros(cart.get("total_price")),
        # Wat je echt betaalt (met bezorgkosten en statiegeld) en wat het lidmaatschap bespaart.
        "checkoutTotal": _euros(cart.get("checkout_total_price")),
        "savings": _euros(cart.get("membership_savings")),
    }


def get_cart() -> dict[str, Any]:
    return cart_summary(_authed("GET", "/cart"))


def slots_from_cart(cart: Any) -> list[dict[str, Any]]:
    """De bezorgmomenten uit het mandje (Picnic zet ze in `delivery_slots`), op tijd gesorteerd."""
    cart = cart if isinstance(cart, dict) else {}
    chosen = cart.get("selected_slot") if isinstance(cart.get("selected_slot"), dict) else {}
    slots = []
    for slot in cart.get("delivery_slots") or []:
        if not isinstance(slot, dict) or not slot.get("slot_id") or not slot.get("window_start"):
            continue
        slots.append(
            {
                "id": str(slot["slot_id"]),
                "windowStart": slot.get("window_start"),
                "windowEnd": slot.get("window_end"),
                "cutOff": slot.get("cut_off_time"),
                "available": bool(slot.get("is_available")),
                "reserved": bool(slot.get("reserved")),
                "selected": bool(slot.get("selected")) or slot.get("slot_id") == chosen.get("slot_id"),
                "minimumOrder": _euros(slot.get("minimum_order_value")),
            }
        )
    slots.sort(key=lambda slot: str(slot["windowStart"]))
    return slots


def get_delivery_slots(limit: int = 12, available_only: bool = True) -> list[dict[str, Any]]:
    """De eerstvolgende bezorgmomenten. Alleen lezen: een moment kiezen doe je in de Picnic-app."""
    slots = slots_from_cart(_authed("GET", "/cart"))
    if available_only:
        slots = [slot for slot in slots if slot["available"]]
    return slots[: max(1, limit)]


def add_to_cart(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Zet producten in het Picnic-winkelmandje. Plaatst nooit een bestelling.

    Elk product is één verzoek. Mislukt er een, dan staan de eerdere al in het mandje; dat
    melden we per product terug in plaats van alles af te breken.
    """
    added: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    cart: Any = None
    for item in items:
        product_id = str(item.get("productId") or "").strip()
        if not product_id:
            continue
        count = normalize_quantity(item.get("quantity"))
        try:
            cart = _authed("POST", "/cart/add_product", body={"product_id": product_id, "count": count})
            added.append({"productId": product_id, "quantity": count})
        except PicnicAuthError:
            raise  # de koppeling zelf is stuk: elke volgende aanroep faalt ook
        except Exception as exc:  # noqa: BLE001 - de fout hoort bij dit product
            failed.append({"productId": product_id, "error": str(exc)})
    return {"added": added, "failed": failed, "cart": cart_summary(cart) if cart else None}


def _product_id(value: Any) -> str:
    product_id = str(value or "").strip()
    if not product_id:
        raise ValueError(tr("Geen product opgegeven", "No product given"))
    return product_id


def _quantity_in(cart: dict[str, Any], product_id: str) -> int:
    return next((item["quantity"] for item in cart["items"] if item["id"] == product_id), 0)


def set_quantity(product_id: Any, quantity: Any) -> dict[str, Any]:
    """Zet een product op een exact aantal (0 haalt het helemaal weg).

    Picnic kent alleen toevoegen en verwijderen, dus we kijken eerst hoeveel er nu in het mandje zit
    en voegen het verschil toe of halen het weg.
    """
    product_id = _product_id(product_id)
    if isinstance(quantity, bool) or (isinstance(quantity, float) and not quantity.is_integer()):
        raise ValueError(tr("Het aantal moet een heel getal zijn", "The quantity must be a whole number"))
    try:
        target = int(quantity)
    except (TypeError, ValueError):
        raise ValueError(tr("Het aantal moet een heel getal zijn", "The quantity must be a whole number")) from None
    if not 0 <= target <= MAX_QUANTITY:
        raise ValueError(
            tr(f"Het aantal moet tussen 0 en {MAX_QUANTITY} liggen", f"The quantity must be between 0 and {MAX_QUANTITY}")
        )
    cart = get_cart()
    limit = next((item["maxCount"] for item in cart["items"] if item["id"] == product_id), None)
    if limit and target > limit:
        raise ValueError(
            tr(
                f"Picnic laat maximaal {limit} van dit product per bestelling toe",
                f"Picnic allows at most {limit} of this product per order",
            )
        )
    current = _quantity_in(cart, product_id)
    if target > current:
        return _authed_summary("/cart/add_product", product_id, target - current)
    if target < current:
        return _authed_summary("/cart/remove_product", product_id, current - target)
    return cart


def _authed_summary(path: str, product_id: str, count: int) -> dict[str, Any]:
    return cart_summary(_authed("POST", path, body={"product_id": product_id, "count": count}))


def clear_cart() -> dict[str, Any]:
    """Maak het Picnic-mandje leeg. Plaatst nooit een bestelling.

    Eerst Picnics eigen route; weigert die, dan halen we elk product met zijn eigen aantal weg.
    """
    before = get_cart()
    if not before["items"]:
        return {"removed": 0, "cart": before}
    try:
        cart = cart_summary(_authed("POST", "/cart/clear"))
    except PicnicAuthError:
        raise
    except Exception:  # noqa: BLE001 - de route kan bij Picnic anders heten; val terug op per product
        cart = before
        for item in before["items"]:
            if item["id"]:
                cart = _authed_summary("/cart/remove_product", str(item["id"]), item["quantity"])
    return {"removed": sum(item["quantity"] for item in before["items"]), "cart": cart}


# --- pagina's -----------------------------------------------------------------------------------
# Picnic levert recepten (en andere schermen) als pagina's: een boom van onderdelen.


def analytics_data(node: Any) -> list[dict[str, Any]]:
    """De analysegegevens (`analytics.contexts[].data`) van een onderdeel van een pagina."""
    analytics = node.get("analytics") if isinstance(node, dict) and isinstance(node.get("analytics"), dict) else {}
    return [c["data"] for c in analytics.get("contexts") or [] if isinstance(c, dict) and isinstance(c.get("data"), dict)]


def _delivery_summary(delivery: dict[str, Any]) -> dict[str, Any]:
    slot = delivery.get("slot") if isinstance(delivery.get("slot"), dict) else {}
    eta = delivery.get("eta2") if isinstance(delivery.get("eta2"), dict) else {}
    orders = [o for o in delivery.get("orders") or [] if isinstance(o, dict)]
    return {
        "id": delivery.get("delivery_id") or delivery.get("id"),
        "status": delivery.get("status"),
        "windowStart": slot.get("window_start"),
        "windowEnd": slot.get("window_end"),
        "etaStart": eta.get("start"),
        "etaEnd": eta.get("end"),
        "total": _euros(sum(o.get("checkout_total_price") or o.get("total_price") or 0 for o in orders)),
    }


def get_deliveries(current_only: bool = False) -> list[dict[str, Any]]:
    """Leveringen van het account; alleen de lopende als `current_only` waar is."""
    data = _authed("POST", "/deliveries/summary", body=["CURRENT"] if current_only else [])
    return [_delivery_summary(d) for d in data or [] if isinstance(d, dict)]
