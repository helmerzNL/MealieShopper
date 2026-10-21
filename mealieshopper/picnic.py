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
from .packs import assess_packs, normalize_quantity

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
    method: str, path: str, *, token: str | None = None, body: Any = None
) -> tuple[Any, str | None]:
    """Eén verzoek aan Picnic. Geeft (antwoord, nieuwe auth-key uit de headers) terug."""
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
            data = None
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict) and error.get("code") in AUTH_ERROR_CODES:
        raise PicnicAuthError(error.get("message") or error["code"])
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


def _authed(method: str, path: str, *, body: Any = None) -> Any:
    token = configured_auth_key()
    if not token:
        raise _not_linked()
    try:
        data, new_token = _call(method, path, token=token, body=body)
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
        raise Picnic2FAError(error.get("message") or error["code"])
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
            result["error"] = str(exc)
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


def image_url(image_id: Any) -> str | None:
    if not image_id:
        return None
    return (
        f"https://storefront-prod.{country_code().lower()}.picnicinternational.com"
        f"/static/images/{quote(str(image_id), safe='')}/medium.png"
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


def cart_summary(cart: Any) -> dict[str, Any]:
    """Het Picnic-mandje in een compacte vorm."""
    cart = cart if isinstance(cart, dict) else {}
    items = []
    for line in cart.get("items") or []:
        if not isinstance(line, dict):
            continue
        article = next((a for a in line.get("items") or [] if isinstance(a, dict)), {})
        image_ids = article.get("image_ids") or []
        items.append(
            {
                "id": article.get("id"),
                "title": article.get("name"),
                "quantity": _line_quantity(line),
                "unitSize": article.get("unit_quantity"),
                "price": _euros(line.get("display_price", line.get("price"))),
                "image": image_url(image_ids[0]) if image_ids else None,
            }
        )
    return {
        "items": items,
        "totalCount": cart.get("total_count"),
        "totalPrice": _euros(cart.get("total_price")),
    }


def get_cart() -> dict[str, Any]:
    return cart_summary(_authed("GET", "/cart"))


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
