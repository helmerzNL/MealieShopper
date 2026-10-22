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
import datetime
import hashlib
import json
import re
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import urlencode

import requests

from . import auth, deals
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
            "Jumbo account niet gekoppeld. De beheerder koppelt het bij Instellingen > Koppelen > Jumbo.",
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


class JumboPersistedQueryError(JumboSchemaError):
    """Jumbo kent de hash van een opgeslagen query niet (meer): de website is waarschijnlijk vernieuwd."""


def _graphql(
    query: str | None,
    variables: dict[str, Any] | None,
    operation: str,
    client: str = BASKET_CLIENT,
    extensions: dict[str, Any] | None = None,
    method: str = "POST",
) -> dict[str, Any]:
    payload: dict[str, Any] = {"operationName": operation, "variables": variables or {}}
    if query is not None:
        payload["query"] = query
    if extensions:
        payload["extensions"] = extensions
    for attempt in (0, 1):
        headers = _headers(get_access_token(force=attempt == 1), client)
        if method == "GET":
            # Zoals de website een opgeslagen query vraagt: alles in de querystring.
            params = {"operationName": operation}
            for key in ("variables", "extensions"):
                if key in payload:
                    params[key] = json.dumps(payload[key], separators=(",", ":"))
            if "query" in payload:
                params["query"] = payload["query"]
            response = requests.get(GRAPHQL_URL, params=params, headers=headers, timeout=30)
        else:
            response = requests.post(GRAPHQL_URL, json=payload, headers=headers, timeout=30)
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
                        "Jumbo-koppeling verlopen of geweigerd. Koppel opnieuw bij Instellingen > Koppelen > Jumbo.",
                        "Jumbo link expired or refused. Link again under Admin > Jumbo.",
                    )
                )
            message = "; ".join(str(e.get("message") or e) for e in errors if isinstance(e, dict))[:400]
            if "PersistedQueryNotFound" in json.dumps(errors) or "PERSISTED_QUERY_NOT_FOUND" in json.dumps(errors):
                raise JumboPersistedQueryError("PersistedQueryNotFound")
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
    is_bonus = isinstance(promo, (int, float)) or bool(tags)
    regular = _euros(price.get("price"))
    product = {
        "id": str(sku),
        "title": str(title),
        "brand": None,
        # De verpakkingsgrootte staat in de ondertitel, of anders vaak in de naam ("Halfvolle melk 1 l").
        "unitSize": raw.get("subtitle") or str(title),
        "price": _euros(promo if isinstance(promo, (int, float)) else price.get("price")),
        "image": _image(raw.get("image")),
        "isBonus": is_bonus,
        "available": (raw.get("availability") or {}).get("isAvailable") is not False,
        "url": f"{SITE_URL}{link}" if isinstance(link, str) and link.startswith("/") else None,
    }
    if is_bonus:
        # De actietekst ("2 voor 5") en de gewone prijs, zodat `deals` kan uitrekenen wat de aanbieding inhoudt.
        # Jumbo noemt hier geen looptijd; die blijft leeg tot een echte respons uitwijst waar die staat.
        product["promo"] = {
            "label": " | ".join(tags) or None,
            "priceBefore": regular if isinstance(promo, (int, float)) and regular and regular > _euros(promo) else None,
            "validFrom": None,
            "validUntil": None,
        }
    return product


def products_from_search(data: Any) -> list[dict[str, Any]]:
    found = ((data or {}).get("searchProducts") or {}).get("products") or []
    products = [_product(item) for item in found if isinstance(item, dict)]
    return [p for p in products if p]


def _search_data(term: str) -> dict[str, Any]:
    """Het ruwe antwoord van een zoekopdracht, met de terugvalstappen van de zoekquery."""
    global _search_variant
    variables = {"input": {"searchType": "KEYWORD", "searchTerms": term}}
    last_error: Exception | None = None
    for step in range(_search_variant, len(SEARCH_EXTRA_FIELDS)):
        try:
            data = _graphql(_search_query(SEARCH_EXTRA_FIELDS[step]), variables, "SearchProductsByKeyword")
        except JumboSchemaError as exc:
            last_error = exc
            continue
        _search_variant = step
        return data
    raise RuntimeError(str(last_error))


def search_products(query: str, limit: int = 5) -> list[dict[str, Any]]:
    term = str(query or "").strip()
    if not term:
        return []
    products = [p for p in products_from_search(_search_data(term)) if p["available"]]
    return products[: max(1, limit)]


# --- aanbiedingen -------------------------------------------------------------------------------
# Jumbo heeft geen route die alle aanbiedingen in een keer geeft die wij kennen. Daarom zoeken we met de bekende
# zoekquery op veel gangbare termen en houden de producten over die een actieprijs of een actielabel hebben, zoals
# bij AH als de bonuspagina niets geeft. Het zijn dus de aanbiedingen die bij deze termen horen, niet per se alle.
OFFER_TERMS = (
    "kip", "gehakt", "vlees", "worst", "spek", "ham", "vis", "zalm", "garnalen", "kaas", "melk", "yoghurt", "kwark",
    "boter", "room", "ei", "brood", "beleg", "ontbijtgranen", "pasta", "rijst", "aardappel", "groente", "tomaat",
    "salade", "fruit", "banaan", "appel", "saus", "soep", "pizza", "diepvries", "ijs", "chips", "noten", "snoep",
    "chocolade", "koek", "koffie", "thee", "frisdrank", "sap", "water", "bier", "wijn", "olie", "kruiden", "bonen",
    "schoonmaak", "wasmiddel", "tandpasta", "shampoo", "luiers", "kattenvoer", "hondenvoer",
)


def _offer(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Een aanbieding uit een zoekresultaat, of None als het product geen actie heeft."""
    sku, title = raw.get("sku"), raw.get("title")
    if not sku or not title or (raw.get("availability") or {}).get("isAvailable") is False:
        return None
    price = raw.get("price") if isinstance(raw.get("price"), dict) else {}
    promo = price.get("promoPrice") if isinstance(price.get("promoPrice"), (int, float)) else None
    tags = [
        str(tag.get("text")).strip()
        for promotion in raw.get("promotions") or []
        if isinstance(promotion, dict)
        for tag in promotion.get("tags") or []
        if isinstance(tag, dict) and tag.get("text")
    ]
    if promo is None and not tags:
        return None
    regular = price.get("price") if isinstance(price.get("price"), (int, float)) else None
    link = raw.get("link")
    return {
        "id": str(sku),
        "title": str(title),
        "brand": None,
        "unitSize": raw.get("subtitle") or None,
        "price": _euros(promo if promo is not None else regular),
        "priceBefore": _euros(regular) if promo is not None and regular is not None and regular > promo else None,
        "promoLabel": " · ".join(dict.fromkeys(tags)),
        "image": _image(raw.get("image")),
        "url": f"{SITE_URL}{link}" if isinstance(link, str) and link.startswith("/") else None,
        "validUntil": None,
    }


def offers_in_search(data: Any) -> list[dict[str, Any]]:
    found = ((data or {}).get("searchProducts") or {}).get("products") or []
    offers = [_offer(item) for item in found if isinstance(item, dict)]
    return [offer for offer in offers if offer]


def get_offers() -> list[dict[str, Any]]:
    """De aanbiedingen die Jumbo bij de gangbare zoektermen laat zien. Vraagt een gekoppeld Jumbo-account."""
    errors: list[Exception] = []

    def search(term: str) -> list[dict[str, Any]]:
        try:
            return offers_in_search(_search_data(term))
        except JumboAuthError:
            raise
        except Exception as exc:  # noqa: BLE001 - een term die mislukt mag de andere niet blokkeren
            errors.append(exc)
            return []

    found: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        for offers in pool.map(search, OFFER_TERMS):
            for offer in offers:
                found.setdefault(offer["id"], offer)
    if not found and errors:
        raise RuntimeError(str(errors[0])[:300])
    return list(found.values())


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
        "deal": deals.candidate_deal("jumbo", product),
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


# --- boodschappenlijstje --------------------------------------------------------------------------
# Jumbo heeft één lijstje met vrije tekst (`term`) en per regel een optionele SKU. Zoals bij het mandje
# kennen we de namen van de foutvarianten niet zeker (`addBasketLines` bleek anders te heten dan gedacht),
# dus elke query heeft een variant zonder foutfragment om op terug te vallen; een fout meldt de typenaam.

LOYALTY_CLIENT = "JUMBO_WEB-loyalty"
_ITEM_FIELDS = "id term sku completed"
_LIST_ERROR = "    ... on ShoppingListError { reason errorMessage }\n"
GET_LIST_QUERIES = tuple(
    "query GetShoppingList {\n  shoppingList {\n    __typename\n"
    f"    ... on ShoppingList {{ isActive items {{ {_ITEM_FIELDS} }} }}\n{error}  }}\n}}"
    for error in (_LIST_ERROR, "")
)
EDIT_LIST_QUERIES = tuple(
    "mutation EditShoppingListItems($input: EditShoppingListItemsInput!) {\n  editShoppingListItems(input: $input) {\n"
    f"    __typename\n    ... on EditShoppingListItemsResult {{ items {{ {_ITEM_FIELDS} }} }}\n{error}  }}\n}}"
    for error in (_LIST_ERROR, "")
)
TOGGLE_LIST_QUERIES = tuple(
    "mutation toggleShoppingList($input: ToggleShoppingListInput!) {\n  toggleShoppingList(input: $input) {\n"
    f"    __typename\n    ... on ShoppingListState {{ isActive }}\n{error}  }}\n}}"
    for error in (_LIST_ERROR, "")
)
MAX_LIST_PRODUCTS = 30  # zoveel producten per synchronisatie ophalen voor titel en prijs

_working_variant: dict[str, int] = {}


def _graphql_variants(operation: str, queries: tuple[str, ...], variables: dict[str, Any] | None, client: str) -> dict[str, Any]:
    """Probeer de varianten van een query na elkaar; onthoud welke werkte (Jumbo's schema is niet gedocumenteerd)."""
    last: Exception | None = None
    for step in range(_working_variant.get(operation, 0), len(queries)):
        try:
            data = _graphql(queries[step], variables, operation, client)
        except JumboSchemaError as exc:
            last = exc
            continue
        _working_variant[operation] = step
        return data
    raise RuntimeError(str(last))


def _expect(data: dict[str, Any], field: str, typename: str, what: str) -> dict[str, Any]:
    value = data.get(field) or {}
    if value.get("__typename") != typename:
        raise RuntimeError(
            tr(
                f"Jumbo gaf bij {what} een onverwacht antwoord ({value.get('__typename') or 'geen antwoord'}).",
                f"Jumbo gave an unexpected answer to {what} ({value.get('__typename') or 'no answer'}).",
            )
        )
    return value


def _list_item(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict) or not str(raw.get("term") or raw.get("sku") or "").strip():
        return None
    return {
        "id": str(raw["id"]) if raw.get("id") else None,
        "term": str(raw.get("term") or "").strip(),
        "sku": str(raw["sku"]) if raw.get("sku") else None,
        "completed": bool(raw.get("completed")),
    }


def get_shopping_list() -> dict[str, Any]:
    """Het Jumbo-lijstje: `{active, items:[{id, term, sku, completed}]}`."""
    data = _graphql_variants("GetShoppingList", GET_LIST_QUERIES, None, LOYALTY_CLIENT)
    value = _expect(data, "shoppingList", "ShoppingList", "het lijstje")
    items = [item for item in (_list_item(raw) for raw in value.get("items") or []) if item]
    return {"active": value.get("isActive") is not False, "items": items}


def _ensure_list_active() -> None:
    """Een lijstje dat in de app uit staat, zetten we aan voordat we er iets op zetten (zoals jumbo-mcp)."""
    data = _graphql_variants("toggleShoppingList", TOGGLE_LIST_QUERIES, {"input": {"active": True}}, LOYALTY_CLIENT)
    _expect(data, "toggleShoppingList", "ShoppingListState", "het aanzetten van het lijstje")


def add_to_shopping_list(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Zet regels op het Jumbo-lijstje. `items`: `[{term, sku?}]`; wat er al op staat slaan we over.

    Plaatst nooit een bestelling en haalt niets van het lijstje.
    """
    current = get_shopping_list()
    on_list = {item["term"].casefold() for item in current["items"] if not item["completed"]}
    lines: list[dict[str, Any]] = []
    skipped: list[str] = []
    for item in items:
        term = " ".join(str(item.get("term") or "").split())
        if not term:
            continue
        if term.casefold() in on_list:
            skipped.append(term)
            continue
        on_list.add(term.casefold())
        line: dict[str, Any] = {"term": term, "completed": False}
        if item.get("sku"):
            line["sku"] = str(item["sku"])
        lines.append(line)
    if lines:
        if not current["active"]:
            _ensure_list_active()
        data = _graphql_variants("EditShoppingListItems", EDIT_LIST_QUERIES, {"input": {"items": lines}}, LOYALTY_CLIENT)
        _expect(data, "editShoppingListItems", "EditShoppingListItemsResult", "het toevoegen aan het lijstje")
    return {"added": [line["term"] for line in lines], "skipped": skipped}


PRODUCT_EXTRA_FIELDS = ("link subtitle image", "link subtitle", "link", "")


def get_product(sku: str) -> dict[str, Any] | None:
    """Titel, prijs en link van een product op SKU, in dezelfde vorm als `search_products`."""
    last: Exception | None = None
    for extra in PRODUCT_EXTRA_FIELDS:
        query = (
            "query ProductDetail($sku: String!) {\n  product(sku: $sku) {\n    title\n    sku\n"
            f"    price {{ price promoPrice }}\n    {extra}\n  }}\n}}"
        )
        try:
            data = _graphql(query, {"sku": str(sku)}, "ProductDetail")
        except JumboSchemaError as exc:
            last = exc
            continue
        raw = data.get("product")
        return _product(raw) if isinstance(raw, dict) else None
    raise RuntimeError(str(last))


# --- eigen lijstjes en favorieten ("Mijn lijstjes") --------------------------------------------------
# De website vraagt deze lijstjes met opgeslagen queries (Apollo persisted queries): alleen een hash en de
# variabelen, geen querytekst. Dezelfde aanvraag sturen wij opnieuw. De hashes horen bij een versie van
# jumbo.com; verandert Jumbo ze, dan meldt de verkenner `PersistedQueryNotFound`. Verversen: open op
# jumbo.com Mijn lijstjes met het tabblad Netwerk (filter `graphql`), kopieer de `sha256Hash` van
# `GetCustomerProductLists` en `GetProductList` uit de aanvraag en zet ze hieronder. Zo ook voor de
# bestellingen (Mijn bestellingen): `OnlineOrdersAndStoreReceipts`.
PERSISTED_QUERIES = {
    "GetCustomerProductLists": "6f99c573278cea7151eb4b422b698cb14e5c0e19632e647a582bfa81f7b5f86c",
    "GetProductList": "2bbb5daa2373f8d00c5029298df31cef6529a89fe68babb7239654d668556e36",
    "OnlineOrdersAndStoreReceipts": "89b3f60a39a37eeed32055fd531a32925e504c566d190e0ca06b8efecd0bccdf",
    # Recepten (jumbo.com/recepten): je favoriete recepten, zoeken, één recept en recepten op id.
    "getRecipeLists": "855078174946522c415ad4e83706f30a5d48c8458885956dcd0db7a2e666ef56",
    "getRecipesListers": "00435d1bce6b5ea2104effb39acc45785a7af43880729d098bd7da99c596021e",
    "getRecipe": "6726ea8758d025225b93db5f464f3e292d18982b1f43d4b6eeec09c081485b41",
    "getRecipesByIds": "daae32449ff23a883e7eded6efdf1d47f29a172af8e538ec2ab05710f9d06da2",
    "FavoriteRecipes": "66516d14849720eac6d873da34dffe7cbd055ed42b8adeda8b31b8b79d5cebf6",  # de lijst Favorieten, per pagina
    "getRecipeListById": "ce93016895c6c68fb4c70ee182a773b7e3402a4f14a61f5e0c5bbb8019b97587",  # een eigen receptenlijst, per pagina
}
# Welke clientnaam en welk transport de router voor deze queries wil is niet bekend; we proberen ze na
# elkaar en onthouden wat werkte (de verkenner meldt het).
LIST_CLIENTS = ("JUMBO_WEB-lists", "JUMBO_WEB-loyalty", "JUMBO_WEB-basket", "JUMBO_WEB-orders", "JUMBO_WEB", "JUMBO_WEB-recipes")
LIST_TRANSPORTS = ("POST", "GET")
LIST_PAGE_SIZE = 25
MAX_LIST_PAGES = 10
_persisted_route: dict[str, tuple[str, str]] = {}
_LIST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _persisted_query(operation: str, variables: dict[str, Any]) -> dict[str, Any]:
    extensions = {"persistedQuery": {"version": 1, "sha256Hash": PERSISTED_QUERIES[operation]}}
    known = _persisted_route.get(operation)
    ladder = [(method, client) for client in LIST_CLIENTS for method in LIST_TRANSPORTS]
    attempts = ([known] if known else []) + [route for route in ladder if route != known]
    last: Exception | None = None
    not_found = False
    for method, client in attempts:
        try:
            data = _graphql(None, variables, operation, client, extensions, method)
        except JumboAuthError:
            raise  # de koppeling zelf is stuk: een andere clientnaam helpt niet
        except JumboPersistedQueryError as exc:
            last, not_found = exc, True
            continue
        except RuntimeError as exc:
            last = exc
            continue
        _persisted_route[operation] = (method, client)
        return data
    _persisted_route.pop(operation, None)
    if not_found:
        raise JumboPersistedQueryError("PersistedQueryNotFound")
    raise RuntimeError(str(last))


def get_product_lists() -> list[dict[str, Any]]:
    """Je eigen lijstjes en Favorieten: `[{id, name, kind, itemCount, description}]`."""
    data = _persisted_query(
        "GetCustomerProductLists",
        {"listItemPagination": {"offset": 0, "limit": 4}, "listPagination": {"offset": 0, "limit": LIST_PAGE_SIZE}},
    )
    lists: list[dict[str, Any]] = []
    for kind, key in (("own", "customerLists"), ("favorites", "favouriteLists")):
        for raw in (data.get(key) or {}).get("items") or []:
            if not isinstance(raw, dict) or not raw.get("id") or not raw.get("title"):
                continue
            count = raw.get("productsCount")
            lists.append(
                {
                    "id": str(raw["id"]),
                    "name": str(raw["title"]),
                    "kind": kind,
                    "itemCount": count if isinstance(count, int) else 0,
                    "description": raw.get("description") or None,
                }
            )
    return lists


def _product_list_item(raw: Any) -> dict[str, Any] | None:
    product = raw.get("product") if isinstance(raw, dict) and isinstance(raw.get("product"), dict) else None
    sku = str((raw or {}).get("sku") or (product or {}).get("sku") or "") if product else ""
    title = str((product or {}).get("title") or "").strip()
    if not sku or not title:
        return None
    price = product.get("price") if isinstance(product.get("price"), dict) else {}
    promo = price.get("promoPrice")
    cents = promo if isinstance(promo, (int, float)) else price.get("price")
    link = product.get("link")
    amount = (raw.get("quantity") or {}).get("amount") if isinstance(raw.get("quantity"), dict) else None
    try:
        quantity = min(99, max(1, int(round(float(amount)))))
    except (TypeError, ValueError, OverflowError):
        quantity = 1
    return {
        "id": str(raw["id"]) if raw.get("id") else None,
        "sku": sku,
        "term": title,
        "quantity": quantity,
        "product": {
            "id": sku,
            "title": title,
            "brand": product.get("brand") or None,
            "unitSize": product.get("subtitle") or None,
            "price": _euros(cents),
            "image": _image(product.get("image")),
            "isBonus": bool(product.get("promotions")) or isinstance(promo, (int, float)),
            "url": f"{SITE_URL}{link}" if isinstance(link, str) and link.startswith("/") else None,
            "available": (product.get("availability") or {}).get("isAvailable") is not False,
        },
    }


def get_product_list(list_id: str) -> dict[str, Any]:
    """Alle producten van één lijstje (in pagina's van 25), in dezelfde vorm als een AH-lijstje."""
    list_id = str(list_id or "").strip()
    if not _LIST_ID_RE.match(list_id):
        raise ValueError(tr("Onbekend lijstje", "Unknown list"))
    reference = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    items: list[dict[str, Any]] = []
    name = ""
    offset = 0
    for _ in range(MAX_LIST_PAGES):
        data = _persisted_query(
            "GetProductList",
            {
                "includeThumbnail": False,
                "includeAuthor": True,
                "includeDelistedItems": False,
                "listId": list_id,
                "listItemsPagination": {"offset": offset, "limit": LIST_PAGE_SIZE},
                "referenceDate": reference,
            },
        )
        value = data.get("productListV2")
        if not isinstance(value, dict):
            raise RuntimeError(tr("Jumbo gaf dit lijstje niet terug", "Jumbo did not return this list"))
        name = str(value.get("title") or name)
        raw_items = value.get("items") or []
        items += [item for item in (_product_list_item(raw) for raw in raw_items) if item]
        offset += len(raw_items)
        total = value.get("productsCount")
        if not raw_items or not isinstance(total, int) or offset >= total:
            break
    return {"id": list_id, "name": name, "items": items}


# --- bestellingen ("Mijn bestellingen") -----------------------------------------------------------------
# De lijst met bestellingen (en winkelbonnen) vraagt de website met de opgeslagen query
# `OnlineOrdersAndStoreReceipts`. Voor de producten van één bestelling kennen we de opgeslagen query nog
# niet; daarvoor probeert `get_order` gewone queries met de veldnamen uit het antwoord van de website.
# Jumbo zelf meldde dat `order(orderId: …)` een `Float!` verwacht (het nummer gaat als getal mee).
ORDER_PAGE_SIZE = 10
MAX_ORDER_PAGES = 30
ORDER_DETAIL_ARGUMENTS = tuple((name, kind) for kind in ("Float!", "Long!", "Int!", "String!", "ID!") for name in ("orderId", "id"))
_ORDER_DETAIL_FIELDS = """
    orderId
    deliveryDate
    fulfilmentType
    paymentMethod
    fulfilmentData { startTime endTime }
    progress { status }
    items {
      sku
      quantity
      pickedQuantity
      linePriceExDiscount { amount }
      linePriceIncDiscount { amount }
      promotions { description }
      details { title subtitle brand image category }
    }
    calculatedTotals { totalToPay { amount } }
"""
_working_order_detail: list[int] = []
_ORDER_ID_RE = re.compile(r"^[0-9]{1,20}$")
_CANCELLED_STATES = {"CANCELLED", "CANCELED"}


def _amount(raw: Any) -> float | None:
    """Bedragen als `{amount: "113.86"}` (euro, als tekst) naar een getal."""
    value = raw.get("amount") if isinstance(raw, dict) else raw
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _order_header(raw: Any) -> dict[str, Any] | None:
    """Een bestelling uit de lijst. Adres, klant- en reserveringsnummer bewaren we bewust niet."""
    if not isinstance(raw, dict) or not str(raw.get("orderId") or "").strip():
        return None
    status = str(raw.get("status") or "")
    return {
        "id": str(raw["orderId"]).strip(),
        "state": status or None,
        "shoppingType": raw.get("fulfilmentType") or None,
        "total": _amount(raw.get("totalToPay")),
        "deliveryDate": raw.get("deliveryDate") or raw.get("creationTime") or None,
        "cancelled": status.upper() in _CANCELLED_STATES,
    }


def get_orders() -> dict[str, Any]:
    """Alle afgeronde bestellingen (nieuwste eerst, per 10) en het aantal winkelbonnen dat Jumbo kent.

    `{orders: [{id, state, shoppingType, total, deliveryDate, cancelled}], storeReceipts: int}`.
    """
    orders: list[dict[str, Any]] = []
    receipts = 0
    for page in range(MAX_ORDER_PAGES):
        data = _persisted_query(
            "OnlineOrdersAndStoreReceipts",
            {
                "ordersInput": {
                    "offset": page * ORDER_PAGE_SIZE,
                    "limit": ORDER_PAGE_SIZE,
                    "direction": "DESC",
                    "sortBy": "DELIVERY_DATE",
                    "statusCategory": "CLOSED",
                },
                "page": page,
                "pageSize": ORDER_PAGE_SIZE,
            },
        )
        online = data.get("onlineOrders")
        if not isinstance(online, dict):
            raise RuntimeError(tr("Jumbo gaf de bestellingen niet terug", "Jumbo did not return the orders"))
        batch = online.get("orders") or []
        orders += [order for order in (_order_header(raw) for raw in batch) if order]
        stored = data.get("storeReceipts")
        if isinstance(stored, dict) and isinstance(stored.get("totalResults"), int):
            receipts = stored["totalResults"]
        total_pages = online.get("totalPages")
        if not batch or online.get("hasNext") is False or (isinstance(total_pages, int) and page + 1 >= total_pages):
            break
    return {"orders": orders, "storeReceipts": receipts}


def _order_line(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict) or not raw.get("sku"):
        return None
    sku = str(raw["sku"])
    details = raw.get("details") if isinstance(raw.get("details"), dict) else {}
    brand = details.get("brand") or None
    title = str(details.get("title") or "").strip()
    if not title or title == "__MISSING_PRODUCT__":  # Jumbo kent dit product niet meer: merk en SKU zijn het enige wat we weten
        title = f"{brand} ({sku})" if brand else sku
    picked = raw.get("pickedQuantity")
    quantity = picked if isinstance(picked, (int, float)) else raw.get("quantity")
    try:
        quantity = max(0, int(round(float(quantity))))
    except (TypeError, ValueError, OverflowError):
        quantity = 1
    paid = _amount(raw.get("linePriceIncDiscount"))
    before = _amount(raw.get("linePriceExDiscount"))
    bonus = bool(raw.get("promotions")) or (paid is not None and before is not None and paid < before)
    return {
        "productId": sku,
        "title": title,
        "brand": brand,
        "unitSize": details.get("subtitle") or None,
        "quantity": quantity,
        "amount": paid,
        "price": round(paid / quantity, 2) if paid is not None and quantity else None,
        "priceBeforeBonus": round(before / quantity, 2) if bonus and before is not None and quantity else None,
        "bonus": bonus,
        "taxonomy": details.get("category") or None,
        "image": _image(details.get("image")),
    }


def parse_order(raw: Any) -> dict[str, Any]:
    """Eén bestelling met zijn producten. Afleveradres en winkeladres worden niet meegenomen."""
    if not isinstance(raw, dict):
        raise RuntimeError(tr("Jumbo gaf deze bestelling niet terug", "Jumbo did not return this order"))
    slot = raw.get("fulfilmentData") if isinstance(raw.get("fulfilmentData"), dict) else {}
    progress = raw.get("progress") if isinstance(raw.get("progress"), dict) else {}
    totals = raw.get("calculatedTotals") if isinstance(raw.get("calculatedTotals"), dict) else {}
    return {
        "id": str(raw.get("orderId") or ""),
        "state": progress.get("status") or None,
        "shoppingType": raw.get("fulfilmentType") or None,
        "method": raw.get("paymentMethod") or None,
        "total": _amount(totals.get("totalToPay")),
        "deliveryDate": raw.get("deliveryDate") or None,
        "slotStart": slot.get("startTime") or None,
        "slotEnd": slot.get("endTime") or None,
        "items": [line for line in (_order_line(item) for item in raw.get("items") or []) if line],
    }


class JumboOrderDetailError(JumboSchemaError):
    """Jumbo weigerde elke variant van de aanvraag voor de producten van een bestelling."""


def _scrub(text: str, limit: int = 160) -> str:
    """Een foutmelding van Jumbo voor in een melding: korter, zonder lange cijferreeksen of id's (bestelnummers, klant-id)."""
    text = re.sub(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", "<id>", " ".join(str(text).split()))
    return re.sub(r"\d{6,}", "<nr>", text)[:limit]


def get_order(order_id: str) -> dict[str, Any]:
    """De producten van één bestelling (gewone query; de werkende variant onthouden we)."""
    order_id = str(order_id or "").strip()
    if not _ORDER_ID_RE.match(order_id):
        raise ValueError(tr("Onbekende bestelling", "Unknown order"))
    causes: list[str] = []
    start = _working_order_detail[0] if _working_order_detail else 0
    for step in list(range(start, len(ORDER_DETAIL_ARGUMENTS))) + list(range(start)):
        name, kind = ORDER_DETAIL_ARGUMENTS[step]
        query = f"query GetOrder($orderId: {kind}) {{\n  order({name}: $orderId) {{{_ORDER_DETAIL_FIELDS}  }}\n}}"
        value: Any = int(order_id) if kind in ("Float!", "Long!", "Int!") else order_id
        try:
            data = _graphql(query, {"orderId": value}, "GetOrder", "JUMBO_WEB-orders")
        except JumboAuthError:
            raise
        except RuntimeError as exc:  # onbekend veld of type: de volgende variant proberen
            cause = _scrub(str(exc))
            if cause not in causes:
                causes.append(cause)
            continue
        _working_order_detail[:] = [step]
        order = parse_order(data.get("order"))
        order["id"] = order["id"] or order_id
        return order
    raise JumboOrderDetailError(f"{len(ORDER_DETAIL_ARGUMENTS)} varianten geweigerd: " + " | ".join(causes[:3]))
