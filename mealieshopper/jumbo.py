"""Jumbo: onofficiële app-API, op dezelfde manier gebruikt als de Jumbo-app en -website zelf.

Jumbo heeft geen officiële API. Sinds de eerdere REST-koppeling (v1.2.0, verwijderd) gebruikt Jumbo
Auth0 (`auth.jumbo.com`, PKCE) en één GraphQL-adres (`www.jumbo.com/api/graphql`). De constanten en de
vorm van de aanroepen volgen het community-project `jumbo-mcp` (MIT) en de beschrijving van `jumbo-cli`;
`jumbo_explore.py` toont welke vorm de antwoorden nu hebben als Jumbo iets wijzigt.

Koppelen werkt zoals bij AH, zonder headless browser: de beheerder logt in zijn eigen browser in (daar
lost die de bot-controle van Jumbo zelf op) en plakt de regel `jumbo://callbacks/login?code=...` uit de
console. Wij ruilen die code in voor een refresh-token en bewaren alleen dat, versleuteld; nooit een
wachtwoord. Jumbo kan een server-IP weigeren; de verkenner meldt dat.

Net als bij Picnic schrijft `add_to_cart` direct in het echte winkelmandje en plaatst nooit een bestelling.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import time
from typing import Any
from urllib.parse import urlencode

import requests

from . import auth
from .ah import extract_oauth_code
from .i18n import tr
from .packs import assess_packs, normalize_quantity

AUTH_BASE = "https://auth.jumbo.com"
CLIENT_ID = "JxF3wi1b7MOTd9qXutaxqd9uHt4KmG3U"
AUDIENCE = "https://jumbo.com/web"
SCOPE = "openid profile email offline_access"
REDIRECT_URI = "jumbo://callbacks/login"
GRAPHQL_URL = "https://www.jumbo.com/api/graphql"
SITE_URL = "https://www.jumbo.com"
CLIENT_VERSION = "master-v39.2.0-web"
BASKET_CLIENT = "JUMBO_WEB-basket"
# Auth0 vraagt om een mobiele browser, de GraphQL-kant om een gewone desktopbrowser (zoals jumbo-mcp).
AUTH_USER_AGENT = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)"
WEB_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

REFRESH_SECRET = "JUMBO_REFRESH_TOKEN"
PENDING_SECRET = "JUMBO_PENDING_LOGIN"
PENDING_TTL_SECONDS = 1800  # zo lang mag het inloggen in de browser duren
TOKEN_MARGIN_SECONDS = 1800  # ruim voor het verlopen (24 uur) al vernieuwen

_STATE_RE = re.compile(r"(?:^|[?&#\s'\"(])state=([^&\s'\"<>)]+)")
_REFRESH_TOKEN_RE = re.compile(r"^v\d+\.\S+$")


class JumboAuthError(RuntimeError):
    """De koppeling is verlopen of geweigerd."""


_access_cache: dict[str, Any] = {"token": "", "expires_at": 0.0}


def _not_linked() -> RuntimeError:
    return RuntimeError(
        tr(
            "Jumbo account niet gekoppeld. De beheerder koppelt het bij Beheer > Jumbo.",
            "Jumbo account not linked. The admin links it under Admin > Jumbo.",
        )
    )


def _blocked(where: str) -> RuntimeError:
    return RuntimeError(
        tr(
            f"Jumbo weigert het verzoek ({where}). Jumbo weert soms server- en datacenter-adressen; "
            "probeer het later opnieuw of vanaf een thuisnetwerk.",
            f"Jumbo refuses the request ({where}). Jumbo sometimes blocks server and datacenter "
            "addresses; try again later or from a home network.",
        )
    )


# --- koppelen (Auth0 met PKCE) --------------------------------------------------------------


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def start_login() -> str:
    """Maak de inlog-URL en bewaar de PKCE-gegevens kort, zodat de code later ingeruild kan worden."""
    verifier = secrets.token_urlsafe(48)
    state = secrets.token_urlsafe(16)
    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "response_mode": "query",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPE,
        "audience": AUDIENCE,
        "code_challenge": _b64url(hashlib.sha256(verifier.encode("ascii")).digest()),
        "code_challenge_method": "S256",
        "state": state,
        "nonce": secrets.token_urlsafe(16),
        "platform": "mobile",
        "prompt": "login",
    }
    auth.set_secret(PENDING_SECRET, json.dumps({"verifier": verifier, "state": state, "at": time.time()}))
    return f"{AUTH_BASE}/authorize?{urlencode(params)}"


def _pending() -> dict[str, Any]:
    raw = auth.get_secret(PENDING_SECRET)
    try:
        pending = json.loads(raw) if raw else {}
    except ValueError:
        pending = {}
    if not pending.get("verifier") or time.time() - float(pending.get("at") or 0) > PENDING_TTL_SECONDS:
        raise RuntimeError(
            tr(
                "Geen lopende Jumbo-login of de login is te oud. Kies opnieuw 'Inloggen bij Jumbo'.",
                "No Jumbo login in progress, or it has expired. Choose 'Log in to Jumbo' again.",
            )
        )
    return pending


def _auth_post(payload: dict[str, str]) -> dict[str, Any]:
    response = requests.post(
        f"{AUTH_BASE}/oauth/token",
        json=payload,
        headers={"Content-Type": "application/json", "User-Agent": AUTH_USER_AGENT},
        timeout=30,
    )
    if response.status_code == 403:
        raise _blocked("403")
    if not response.ok:
        detail = response.text[:300]
        if response.status_code in {400, 401} and ("invalid_grant" in detail or "expired" in detail.lower()):
            raise JumboAuthError(
                tr(
                    "Jumbo wijst de code of het token af: ze zijn verlopen of al gebruikt. "
                    "Log opnieuw in en plak de nieuwe regel direct.",
                    "Jumbo rejects the code or token: it has expired or was already used. "
                    "Log in again and paste the new line right away.",
                )
            )
        raise RuntimeError(f"{response.status_code}: {detail}")
    data = response.json()
    if not data.get("access_token"):
        raise RuntimeError(tr("Jumbo gaf geen toegangstoken terug", "Jumbo returned no access token"))
    return data


def _remember_tokens(data: dict[str, Any], used_refresh: str = "") -> None:
    _access_cache["token"] = data["access_token"]
    _access_cache["expires_at"] = time.time() + int(data.get("expires_in") or 86400) - TOKEN_MARGIN_SECONDS
    new = str(data.get("refresh_token") or "").strip()
    if new and new != used_refresh:
        auth.set_secret(REFRESH_SECRET, new)  # Auth0 kan het refresh-token bij elke vernieuwing vervangen


def link_account(value: str) -> dict[str, Any]:
    """Koppel vanuit de geplakte `jumbo://callbacks/login?code=...`-regel (of een los refresh-token)."""
    text = str(value or "").strip()
    if not text:
        raise ValueError(tr("Geen code of token opgegeven", "No code or token provided"))

    if _REFRESH_TOKEN_RE.match(text) and "code=" not in text:
        data = _auth_post(
            {"grant_type": "refresh_token", "client_id": CLIENT_ID, "refresh_token": text,
             "audience": AUDIENCE, "scope": SCOPE}
        )
        auth.set_secret(REFRESH_SECRET, text)
        _remember_tokens(data, used_refresh=text)
        return {"connected": True, "type": "refresh"}

    pending = _pending()
    pasted_state = _STATE_RE.search(text)
    if pasted_state and pasted_state.group(1) != pending.get("state"):
        raise JumboAuthError(
            tr(
                "Deze code hoort bij een andere inlogpoging. Kies opnieuw 'Inloggen bij Jumbo' en plak de nieuwe regel.",
                "This code belongs to a different login attempt. Choose 'Log in to Jumbo' again and paste the new line.",
            )
        )
    data = _auth_post(
        {
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "code": extract_oauth_code(text),
            "redirect_uri": REDIRECT_URI,
            "code_verifier": pending["verifier"],
            "audience": AUDIENCE,
            "scope": SCOPE,
        }
    )
    if not data.get("refresh_token"):
        raise RuntimeError(
            tr("Jumbo gaf geen refresh-token terug", "Jumbo returned no refresh token")
        )
    _remember_tokens(data)
    auth.delete_secret(PENDING_SECRET)
    return {"connected": True, "type": "code"}


def configured_refresh_token() -> str:
    return auth.get_secret(REFRESH_SECRET)


def unlink() -> dict[str, Any]:
    auth.delete_secret(REFRESH_SECRET)
    auth.delete_secret(PENDING_SECRET)
    _access_cache.update(token="", expires_at=0.0)
    return {"connected": False}


def get_access_token(force: bool = False) -> str:
    if not force and _access_cache["token"] and time.time() < _access_cache["expires_at"]:
        return _access_cache["token"]
    refresh = configured_refresh_token()
    if not refresh:
        raise _not_linked()
    # `audience` en `scope` zijn nodig: zonder krijgt het token geen klant-id en weigert Jumbo het.
    data = _auth_post(
        {"grant_type": "refresh_token", "client_id": CLIENT_ID, "refresh_token": refresh,
         "audience": AUDIENCE, "scope": SCOPE}
    )
    _remember_tokens(data, used_refresh=refresh)
    return _access_cache["token"]


def status(verify: bool = False) -> dict[str, Any]:
    """Is er een koppeling, en klopt die nog? Geeft geen persoonsgegevens terug."""
    result: dict[str, Any] = {"connected": bool(configured_refresh_token())}
    try:
        _pending()
        result["loginPending"] = True
    except RuntimeError:
        result["loginPending"] = False
    if verify and result["connected"]:
        try:
            get_access_token(force=True)
            result["verified"] = True
        except Exception as exc:  # noqa: BLE001 - de reden tonen we aan de beheerder
            result["verified"] = False
            result["error"] = type(exc).__name__
    return result


# --- GraphQL ----------------------------------------------------------------------------------


def _headers(token: str, client: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": WEB_USER_AGENT,
        "apollographql-client-name": client,
        "apollographql-client-version": CLIENT_VERSION,
        "x-source": client,
    }


def _looks_like_auth_failure(errors: list[Any]) -> bool:
    text = json.dumps(errors).lower()
    return any(word in text for word in ("unauthorized", "unauthenticated", "customer id", "401"))


def _is_schema_error(errors: list[Any]) -> bool:
    text = json.dumps(errors)
    return "GRAPHQL_VALIDATION_FAILED" in text or "Cannot query field" in text or "must have a selection" in text


class JumboSchemaError(RuntimeError):
    """Jumbo kent een veld of type uit de query niet (meer)."""


def _graphql(query: str, variables: dict[str, Any] | None, operation: str, client: str = BASKET_CLIENT) -> dict[str, Any]:
    payload = {"operationName": operation, "query": query, "variables": variables or {}}
    for attempt in (0, 1):
        response = requests.post(
            GRAPHQL_URL, json=payload, headers=_headers(get_access_token(force=attempt == 1), client), timeout=30
        )
        if response.status_code == 403:
            raise _blocked("403")
        if response.status_code == 401 and attempt == 0:
            continue  # het token kan net verlopen zijn: één keer vernieuwen
        if not response.ok:
            raise RuntimeError(f"{response.status_code}: {response.text[:500]}")
        try:
            body = response.json()
        except ValueError:
            raise _blocked("geen JSON terug / no JSON back") from None
        errors = body.get("errors") or []
        if errors and attempt == 0 and _looks_like_auth_failure(errors):
            continue
        if errors:
            if _looks_like_auth_failure(errors):
                raise JumboAuthError(
                    tr(
                        "Jumbo-koppeling verlopen of geweigerd. Koppel opnieuw bij Beheer > Jumbo.",
                        "Jumbo link expired or refused. Link again under Admin > Jumbo.",
                    )
                )
            message = "; ".join(str(e.get("message") or e) for e in errors if isinstance(e, dict))[:400]
            if _is_schema_error(errors):
                raise JumboSchemaError(message)
            raise RuntimeError(f"GraphQL: {message}")
        return body.get("data") or {}
    raise JumboAuthError(tr("Jumbo-koppeling geweigerd.", "Jumbo link refused."))


# --- producten ----------------------------------------------------------------------------------

# De zoekquery in drie stappen: eerst met pakmaat (`subtitle`) en plaatje, dan alleen pakmaat, dan de
# kale velden die zeker bestaan. Jumbo's schema is niet gedocumenteerd; een onbekend veld laat de hele
# query mislukken, dus we vallen terug op de volgende. De werkende stap onthouden we.
_SEARCH_CORE = """
    title
    sku
    link
    price { price promoPrice pricePerUnit { price } }
    availability { isAvailable }
    promotions { tags { text } }
"""
SEARCH_EXTRA_FIELDS = ("subtitle image", "subtitle", "")
_search_variant = 0


def _search_query(extra: str) -> str:
    return (
        "query SearchProductsByKeyword($input: ProductSearchInput!) {\n"
        "  searchProducts(input: $input) {\n    count\n    products {" + _SEARCH_CORE + f"      {extra}\n    }}\n  }}\n}}"
    )


def _image(raw: Any) -> str | None:
    if isinstance(raw, str):
        return raw or None
    if isinstance(raw, dict):
        return _image(raw.get("url") or raw.get("src"))
    if isinstance(raw, list) and raw:
        return _image(raw[0])
    return None


def _euros(cents: Any) -> float | None:
    return round(cents / 100, 2) if isinstance(cents, (int, float)) else None


def _product(raw: dict[str, Any]) -> dict[str, Any] | None:
    sku, title = raw.get("sku"), raw.get("title")
    if not sku or not title:
        return None
    price = raw.get("price") if isinstance(raw.get("price"), dict) else {}
    promo = price.get("promoPrice")
    tags = [
        str(tag.get("text"))
        for promotion in raw.get("promotions") or []
        if isinstance(promotion, dict)
        for tag in promotion.get("tags") or []
        if isinstance(tag, dict) and tag.get("text")
    ]
    link = raw.get("link")
    return {
        "id": str(sku),
        "title": str(title),
        "brand": None,
        # De verpakkingsgrootte staat in de ondertitel, of anders vaak in de naam ("Halfvolle melk 1 l").
        "unitSize": raw.get("subtitle") or str(title),
        "price": _euros(promo if isinstance(promo, (int, float)) else price.get("price")),
        "image": _image(raw.get("image")),
        "isBonus": isinstance(promo, (int, float)) or bool(tags),
        "available": (raw.get("availability") or {}).get("isAvailable") is not False,
        "url": f"{SITE_URL}{link}" if isinstance(link, str) and link.startswith("/") else None,
    }


def products_from_search(data: Any) -> list[dict[str, Any]]:
    found = ((data or {}).get("searchProducts") or {}).get("products") or []
    products = [_product(item) for item in found if isinstance(item, dict)]
    return [p for p in products if p]


def search_products(query: str, limit: int = 5) -> list[dict[str, Any]]:
    global _search_variant
    term = str(query or "").strip()
    if not term:
        return []
    variables = {"input": {"searchType": "KEYWORD", "searchTerms": term}}
    last_error: Exception | None = None
    for step in range(_search_variant, len(SEARCH_EXTRA_FIELDS)):
        try:
            data = _graphql(_search_query(SEARCH_EXTRA_FIELDS[step]), variables, "SearchProductsByKeyword")
        except JumboSchemaError as exc:
            last_error = exc
            continue
        _search_variant = step
        products = [p for p in products_from_search(data) if p["available"]]
        return products[: max(1, limit)]
    raise RuntimeError(str(last_error))


def search_product(query: str) -> dict[str, Any] | None:
    found = search_products(query, 1)
    return found[0] if found else None


def candidate(product: dict[str, Any], quantity: Any, unit: Any) -> dict[str, Any]:
    """Zelfde vorm als de kandidaten die de AH- en Picnic-controlelijst toont."""
    packs, doubt = assess_packs(quantity, unit, product.get("unitSize"))
    return {
        "productId": product["id"],
        "title": product["title"],
        "brand": product.get("brand"),
        "unitSize": product.get("unitSize"),
        "price": product.get("price"),
        "image": product.get("image"),
        "isBonus": bool(product.get("isBonus")),
        "url": product.get("url"),
        "packs": packs,
        "doubt": doubt,
    }


# --- mandje ---------------------------------------------------------------------------------------

_BASKET_LINES = """
      lines {
        sku
        id
        quantity
        details { title price { price promoPrice } }
      }
"""
ACTIVE_BASKET_QUERY = (
    "query ActiveBasket {\n  activeBasket {\n    __typename\n    ... on ActiveBasketResult {\n      basket {\n"
    "        id\n        totalProductCount\n" + _BASKET_LINES +
    "        calculation { totals { totalToPay { amount currency } } }\n      }\n    }\n"
    "    ... on BasketError { reason errorMessage }\n  }\n}"
)
ADD_LINES_MUTATION = (
    "mutation BasketPageAddBasketItems($input: AddBasketLinesInput!) {\n  addBasketLines(input: $input) {\n"
    "    __typename\n    ... on Basket {\n      id\n      totalProductCount\n" + _BASKET_LINES +
    "    }\n  }\n}"
)
# De foutvariant van addBasketLines heet in Jumbo's schema niet `BasketError` (dat geeft een validatiefout,
# bevestigd met de verkenner op een echte server). We kennen de naam niet, dus selecteren we alleen
# `__typename` en melden die bij een fout; `jumbo_explore.py` toont hem.
_EMPTY_BASKET_REASONS = ("NOT_FOUND", "NO_ACTIVE", "EMPTY", "NO_BASKET")


def basket_summary(basket: Any, total: Any = None) -> dict[str, Any]:
    basket = basket if isinstance(basket, dict) else {}
    items = []
    for line in basket.get("lines") or []:
        if not isinstance(line, dict):
            continue
        details = line.get("details") if isinstance(line.get("details"), dict) else {}
        price = details.get("price") if isinstance(details.get("price"), dict) else {}
        unit = price.get("promoPrice") if isinstance(price.get("promoPrice"), (int, float)) else price.get("price")
        quantity = line.get("quantity") if isinstance(line.get("quantity"), int) else 1
        items.append(
            {
                "id": line.get("sku"),
                "title": details.get("title"),
                "quantity": quantity,
                "price": _euros(unit * quantity) if isinstance(unit, (int, float)) else None,
            }
        )
    return {
        "items": items,
        "totalCount": basket.get("totalProductCount"),
        "totalPrice": _euros(total) if isinstance(total, (int, float)) else None,
    }


def _added_basket(data: dict[str, Any]) -> dict[str, Any]:
    """Het mandje na `addBasketLines`. Jumbo meldt fouten als gewoon antwoord (HTTP 200) met een andere typenaam."""
    value = data.get("addBasketLines") or {}
    typename = value.get("__typename")
    if typename != "Basket":
        raise RuntimeError(
            tr(
                f"Jumbo weigerde het toevoegen aan het mandje ({typename or 'geen antwoord'}).",
                f"Jumbo refused to add to the basket ({typename or 'no answer'}).",
            )
        )
    return value


def get_cart() -> dict[str, Any]:
    data = _graphql(ACTIVE_BASKET_QUERY, None, "ActiveBasket")
    value = data.get("activeBasket") or {}
    if value.get("__typename") == "BasketError":
        reason = str(value.get("reason") or "").upper()
        if any(word in reason for word in _EMPTY_BASKET_REASONS):
            return basket_summary({})
        raise RuntimeError(f"{value.get('reason')}: {value.get('errorMessage') or ''}".strip(": "))
    basket = value.get("basket") or {}
    amount = (((basket.get("calculation") or {}).get("totals") or {}).get("totalToPay") or {}).get("amount")
    return basket_summary(basket, amount)


def _add_lines(lines: list[dict[str, Any]]) -> Any:
    data = _graphql(
        ADD_LINES_MUTATION, {"input": {"lines": lines, "type": "ECOMMERCE"}}, "BasketPageAddBasketItems"
    )
    return _added_basket(data)


def add_to_cart(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Zet producten in het Jumbo-winkelmandje. Plaatst nooit een bestelling.

    Eerst alles in één verzoek. Weigert Jumbo dat, dan proberen we elk product apart, zodat één
    afgekeurd product de rest niet tegenhoudt en we kunnen melden welke ontbreken.
    """
    lines = []
    for item in items:
        sku = str(item.get("productId") or "").strip()
        if sku:
            lines.append({"sku": sku, "quantity": normalize_quantity(item.get("quantity"))})
    added: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    basket: Any = None
    if not lines:
        return {"added": added, "failed": failed, "cart": None}
    try:
        basket = _add_lines(lines)
        added = [{"productId": line["sku"], "quantity": line["quantity"]} for line in lines]
    except JumboAuthError:
        raise
    except RuntimeError as batch_error:
        if len(lines) == 1:
            failed = [{"productId": lines[0]["sku"], "error": str(batch_error)}]
        else:
            for line in lines:
                try:
                    basket = _add_lines([line])
                    added.append({"productId": line["sku"], "quantity": line["quantity"]})
                except JumboAuthError:
                    raise
                except RuntimeError as exc:
                    failed.append({"productId": line["sku"], "error": str(exc)})
    return {"added": added, "failed": failed, "cart": basket_summary(basket) if basket else None}
