import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from os import environ
from typing import Any
from urllib.parse import unquote, urlencode, urlparse

import requests

from . import auth
from .i18n import tr
from .packs import (  # noqa: F401 - blijft hier bereikbaar voor bestaande aanroepers
    LARGE_PACK_COUNT,
    MAX_CART_QUANTITY,
    assess_packs,
    normalize_quantity,
    packs_needed,
    parse_pack_size,
)

AH_API_BASE = "https://api.ah.nl"
AH_LOGIN_BASE = "https://login.ah.nl"
AH_CLIENT_ID = "appie-ios"
AH_CLIENT_VERSION = "9.28"
AH_MOBILE_USER_AGENT = "Appie/9.28 (iPhone17,3; iPhone; CPU OS 26_1 like Mac OS X)"
AH_AUTH_HEADERS = {
    "User-Agent": AH_MOBILE_USER_AGENT,
    "x-client-name": AH_CLIENT_ID,
    "x-client-version": AH_CLIENT_VERSION,
    "x-application": "AHWEBSHOP",
    "Accept": "application/json",
    "Content-Type": "application/json",
}
AH_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Receptzoeken: de oude REST-route (recipe/search/v2) bestaat niet meer. De zoekinvoer kent
# searchText, size en start (de offset); `page`, `offset` en `pageSize` bestaan niet.
RECIPE_SEARCH_QUERY = """
  query RecipeSearch($query: RecipeSearchParams!) {
    recipeSearch(query: $query) {
      page {
        total
        hasNextPage
      }
      result {
        id
        title
        slug
        time {
          cook
          oven
          wait
        }
        serving {
          number
        }
        images {
          url
          width
        }
        courses
      }
    }
  }
"""
# AH geeft een fout als start + size boven 2000 uitkomt; zo ver kun je bladeren.
RECIPE_SEARCH_WINDOW = 2000
RECIPE_CARD_IMAGE_WIDTH = 440

FAVORITE_LISTS_QUERY = """
  query FavoriteLists($ids: [String!]!) {
    favoriteListV2(ids: $ids) {
      id
      description
      totalSize
      updatedAt
    }
  }
"""

COOKBOOK_RECIPES_QUERY = """
  query SavedRecipes {
    cookBookRecipesCollectedMember {
      result {
        id
        title
      }
      page {
        total
        hasNextPage
      }
    }
  }
"""

# "Bewaarde recepten" in de AH-app zijn recepten in de receptencollectie van het lid
# (categorieen met recepten). Het type van `recipes` staat niet in de schema-snapshot,
# dus probeer eerst met en dan zonder selectie.
RECIPE_COLLECTIONS_QUERIES = (
    """
  query SavedRecipeCollections {
    recipeCollectionCategories {
      id
      name
      isDefault
      recipes {
        id
      }
    }
  }
""",
    """
  query SavedRecipeCollections {
    recipeCollectionCategories {
      id
      name
      isDefault
      recipes
    }
  }
""",
)

RECIPE_SUMMARIES_QUERIES = (
    """
  query RecipeSummaries($ids: [Int!]!) {
    recipeSummaries(ids: $ids) {
      id
      title
      slug
      images {
        url
      }
    }
  }
""",
    """
  query RecipeSummaries($ids: [Int!]!) {
    recipeSummaries(ids: $ids) {
      id
      title
      slug
    }
  }
""",
)

# Receptdetails voor het importeren in Mealie. De laatste variant bevat alleen velden
# waarvan bekend is dat ze werken; de eerdere voegen portie en afbeelding toe en
# proberen beide vormen van `preparation.steps`.
RECIPE_DETAIL_QUERIES = tuple(
    """
  query Recipe($id: Int!) {
    recipe(id: $id) {
      id
      title
      description
      cookTime
      %s
      ingredients {
        text
      }
      preparation {
        steps%s
      }
    }
  }
""" % (extra, steps)
    for extra, steps in (
        ("ovenTime\n      waitTime\n      servings { number }\n      images { url }", " { text }"),
        ("ovenTime\n      waitTime\n      servings { number }\n      images { url }", ""),
        ("", ""),
    )
)

FAVORITE_LIST_QUERY = """
  query FavoriteListV2($ids: [String!]!) {
    favoriteListV2(ids: $ids) {
      id
      description
      totalSize
      items {
        id
        productId
        quantity
      }
    }
  }
"""


_CODE_PARAM_RE = re.compile(r"(?:^|[?&#\s'\"(])code=([^&\s'\"<>)]+)")


def extract_oauth_code(value: str) -> str:
    """Haal de OAuth code uit een geplakte URL, console-regel of kale code.

    Browsers tonen de ``appie://login-exit?code=...`` redirect alleen als
    foutmelding in de console, dus de geplakte tekst bevat vaak ruis eromheen
    (aanhalingstekens, "Failed to launch ..." enzovoort).
    """
    candidate = str(value or "").strip()
    match = _CODE_PARAM_RE.search(candidate)
    if match:
        return unquote(match.group(1)).strip()
    return candidate.strip("'\"` ")


def login_url(redirect_uri: str = "appie://login-exit") -> str:
    params = {
        "client_id": AH_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": redirect_uri,
    }
    return f"{AH_LOGIN_BASE}/login?{urlencode(params)}"


def proxied_login_url(proxy_base_url: str) -> str:
    params = {
        "client_id": AH_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": "appie://login-exit",
    }
    return f"{proxy_base_url.rstrip('/')}/login?{urlencode(params)}"


def sanitize_login_cookie(cookie: str, path: str = "/api/ah/auth/proxy") -> str:
    parts = str(cookie or "").split(";")
    result = parts[:1]
    for part in parts[1:]:
        attr = part.strip()
        lower = attr.lower()
        if (
            lower == "secure"
            or lower.startswith("samesite")
            or lower.startswith("domain")
            or lower.startswith("path")
        ):
            continue
        result.append(part)
    result.append(f" Path={path}")
    return ";".join(result)


def rewrite_login_location(location: str, proxy_base_url: str) -> str:
    value = str(location or "")
    proxy_base = proxy_base_url.rstrip("/")
    if value.startswith("appie://"):
        parsed = urlparse(value)
        return f"{proxy_base}/callback?{parsed.query}"
    if value.startswith(AH_LOGIN_BASE):
        return value.replace(AH_LOGIN_BASE, proxy_base, 1)
    if value.startswith("/"):
        return f"{proxy_base}{value}"
    return value


def rewrite_login_body(body: bytes, proxy_base_url: str) -> bytes:
    proxy_base = proxy_base_url.rstrip("/").encode("utf-8")
    proxy_base_escaped = proxy_base.replace(b"/", b"\\/")
    rewritten = body.replace(b"appie://login-exit", proxy_base + b"/callback")
    rewritten = rewritten.replace(AH_LOGIN_BASE.encode("utf-8"), proxy_base)
    rewritten = rewritten.replace(b"https:\\/\\/login.ah.nl", proxy_base_escaped)
    rewritten = rewritten.replace(b"//login.ah.nl", proxy_base)
    for attr in (b"href", b"src", b"action", b"formaction"):
        rewritten = rewritten.replace(attr + b'="/', attr + b'="' + proxy_base + b"/")
        rewritten = rewritten.replace(attr + b"='/", attr + b"='" + proxy_base + b"/")
    rewritten = rewritten.replace(b'":"/', b'":"' + proxy_base + b"/")
    rewritten = rewritten.replace(b'":"\\/', b'":"' + proxy_base_escaped + b"\\/")
    rewritten = rewritten.replace(b'fetch("/', b'fetch("' + proxy_base + b"/")
    rewritten = rewritten.replace(b"fetch('/", b"fetch('" + proxy_base + b"/")
    rewritten = rewritten.replace(b"url(/", b"url(" + proxy_base + b"/")
    return rewritten


@dataclass
class TokenCache:
    token: str
    expires_at: float


anon_token_cache: TokenCache | None = None
user_token_cache: TokenCache | None = None


def _auth_headers(token: str | None = None, *, include_content_type: bool = True) -> dict[str, str]:
    headers = dict(AH_AUTH_HEADERS)
    if not include_content_type:
        headers.pop("Content-Type", None)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _request_json(method: str, url: str, **kwargs: Any) -> Any:
    response = requests.request(method, url, timeout=30, **kwargs)
    if not response.ok:
        body = response.text[:500]
        raise RuntimeError(f"{response.status_code}: {body}")
    return response.json()


def get_token() -> str:
    global anon_token_cache

    if anon_token_cache and time.time() < anon_token_cache.expires_at:
        return anon_token_cache.token

    data = _request_json(
        "POST",
        f"{AH_API_BASE}/mobile-auth/v1/auth/token/anonymous",
        headers=_auth_headers(),
        json={"clientId": AH_CLIENT_ID},
    )
    anon_token_cache = TokenCache(
        token=data["access_token"],
        expires_at=time.time() + max(0, int(data.get("expires_in", 3600)) - 60),
    )
    return anon_token_cache.token


def get_user_token() -> str:
    global user_token_cache

    if user_token_cache and time.time() < user_token_cache.expires_at:
        return user_token_cache.token

    refresh_token = configured_refresh_token()
    if not refresh_token:
        raise RuntimeError(
            tr(
                "AH account niet gekoppeld. De beheerder koppelt het bij Instellingen > Koppelen > Albert Heijn.",
                "AH account not linked. The admin links it under Settings > Link > Albert Heijn.",
            )
        )

    data = _request_json(
        "POST",
        f"{AH_API_BASE}/mobile-auth/v1/auth/token/refresh",
        headers=_auth_headers(),
        json={"clientId": AH_CLIENT_ID, "refreshToken": refresh_token},
    )
    _store_rotated_refresh_token(refresh_token, data.get("refresh_token"))
    user_token_cache = TokenCache(
        token=data["access_token"],
        expires_at=time.time() + max(0, int(data.get("expires_in", 3600)) - 60),
    )
    return user_token_cache.token


def configured_refresh_token() -> str:
    # De opgeslagen token gaat voor: AH kan de refresh token bij elke vernieuwing
    # vervangen, en die nieuwe waarde bewaren we in de database. De omgevings-
    # variabele is alleen de startwaarde.
    return auth.get_secret("AH_REFRESH_TOKEN") or environ.get("AH_REFRESH_TOKEN", "").strip()


def _store_rotated_refresh_token(used: str, new: Any) -> None:
    new_token = str(new or "").strip()
    if new_token and new_token != used:
        save_refresh_token(new_token)


def save_refresh_token(refresh_token: str) -> None:
    token = str(refresh_token or "").strip()
    if not token:
        raise RuntimeError("Refresh token ontbreekt")
    auth.set_secret("AH_REFRESH_TOKEN", token)


def link_account(value: str) -> dict[str, Any]:
    """Koppel een AH account vanuit een OAuth code (of appie:// URL) of een refresh token.

    De gebruiker kan beide plakken in hetzelfde veld; we raden niet op lengte maar
    proberen de meest waarschijnlijke interpretatie en vallen terug op de andere.
    """
    text = str(value or "").strip()
    if not text:
        return {"connected": False, "error": "Geen code of token opgegeven"}

    errors: list[str] = []

    def try_code(raw: str) -> dict[str, Any] | None:
        try:
            exchange_and_store_oauth_code(extract_oauth_code(raw))
            return {"connected": True, "type": "code"}
        except Exception as exc:  # noqa: BLE001
            errors.append(f"code: {exc}")
            return None

    def try_refresh(raw: str) -> dict[str, Any] | None:
        result = verify_token(raw)
        if result.get("ok"):
            if result.get("type") == "refresh":
                save_refresh_token(result.get("refreshToken") or raw)
                return {"connected": True, "type": "refresh"}
            errors.append(
                "dit is een access token (verloopt snel); plak de refresh token"
            )
            return None
        errors.append(f"token: {result.get('error')}")
        return None

    looks_like_code = "code=" in text or text.startswith("appie://")
    order = (try_code, try_refresh) if looks_like_code else (try_refresh, try_code)
    for attempt in order:
        outcome = attempt(text)
        if outcome:
            return outcome

    return {
        "connected": False,
        "error": "; ".join(e for e in errors if e) or "Koppelen mislukt",
    }


def auth_status(verify: bool = False) -> dict[str, Any]:
    token = configured_refresh_token()
    if not token:
        return {"connected": False}
    if not verify:
        return {"connected": True, "verified": False}
    result = verify_token(token)
    if result.get("ok") and result.get("type") == "refresh":
        _store_rotated_refresh_token(token, result.get("refreshToken"))
    return {
        "connected": bool(result.get("ok")),
        "verified": True,
        "type": result.get("type"),
        "error": result.get("error"),
    }


def _explain_exchange_error(error: str) -> str:
    status = error.split(":", 1)[0].strip()
    lowered = error.lower()
    if status == "403" or "access denied" in lowered:
        return (
            "AH weigert het verzoek (403). Probeer het later opnieuw; "
            "blijft dit gebeuren, dan blokkeert AH deze server."
        )
    if status in {"400", "401"}:
        return (
            "AH wijst de code af: hij is verlopen of al gebruikt. Log opnieuw in "
            "bij AH en plak de nieuwe code direct."
        )
    return error


def exchange_oauth_code(code: str, redirect_uri: str | None = None) -> dict[str, str]:
    payload: dict[str, str] = {"clientId": AH_CLIENT_ID, "code": extract_oauth_code(code)}
    if redirect_uri:
        payload["redirect_uri"] = redirect_uri

    try:
        data = _request_json(
            "POST",
            f"{AH_API_BASE}/mobile-auth/v1/auth/token",
            headers=_auth_headers(),
            json=payload,
        )
    except RuntimeError as exc:
        raise RuntimeError(_explain_exchange_error(str(exc))) from exc
    global user_token_cache
    user_token_cache = TokenCache(
        token=data["access_token"],
        expires_at=time.time() + max(0, int(data.get("expires_in", 3600)) - 60),
    )
    return {"accessToken": data["access_token"], "refreshToken": data["refresh_token"]}


def exchange_and_store_oauth_code(code: str, redirect_uri: str | None = None) -> dict[str, str]:
    tokens = exchange_oauth_code(code, redirect_uri)
    save_refresh_token(tokens["refreshToken"])
    return tokens


def verify_token(token: str) -> dict[str, Any]:
    refresh_response = requests.post(
        f"{AH_API_BASE}/mobile-auth/v1/auth/token/refresh",
        headers=_auth_headers(),
        json={"clientId": AH_CLIENT_ID, "refreshToken": token},
        timeout=30,
    )
    if refresh_response.ok:
        try:
            rotated = (refresh_response.json() or {}).get("refresh_token")
        except ValueError:
            rotated = None
        return {"ok": True, "type": "refresh", "refreshToken": rotated or token}

    access_response = requests.get(
        f"{AH_API_BASE}/mobile-services/member/v2/profile",
        headers=_auth_headers(token, include_content_type=False),
        timeout=30,
    )
    if access_response.ok:
        return {"ok": True, "type": "access"}

    return {
        "ok": False,
        "error": f"Token werkt niet ({refresh_response.status_code}): {refresh_response.text[:200]}",
    }


def _pick_image(images: list[dict[str, Any]], target: int = RECIPE_CARD_IMAGE_WIDTH) -> str:
    """Kies uit de formaten van AH de afbeelding die het dichtst bij de gewenste breedte ligt."""
    candidates = [image for image in images or [] if isinstance(image, dict) and image.get("url")]
    if not candidates:
        return ""
    best = min(candidates, key=lambda image: abs(int(image.get("width") or 0) - target))
    return best["url"]


def _recipe_card(item: dict[str, Any]) -> dict[str, Any]:
    recipe_id = item.get("id")
    slug = str(item.get("slug") or "").strip()
    times = item.get("time") or {}
    total_time = sum(int(times.get(key) or 0) for key in ("cook", "oven", "wait"))
    image = _pick_image(item.get("images") or [])
    return {
        "id": str(recipe_id),
        "title": item.get("title") or f"Recept {recipe_id}",
        "webPath": f"/allerhande/recept/R-R{recipe_id}" + (f"/{slug}" if slug else ""),
        "images": [{"url": image}] if image else [],
        "totalTime": total_time,
        "servings": (item.get("serving") or {}).get("number"),
        "courses": item.get("courses") or [],
    }


def search_recipes(query: str, page: int = 0, size: int = 12) -> dict[str, Any]:
    """Allerhande-recepten zoeken via GraphQL (anoniem token)."""
    size = max(1, int(size))
    start = max(0, int(page)) * size
    if start >= RECIPE_SEARCH_WINDOW:
        raise RuntimeError(
            f"AH laat niet verder bladeren dan {RECIPE_SEARCH_WINDOW:,} recepten. Verfijn je zoekopdracht.".replace(",", ".")
        )
    size = min(size, RECIPE_SEARCH_WINDOW - start)  # de laatste pagina wordt afgekapt in plaats van een fout

    data = _graphql(
        get_token(),
        RECIPE_SEARCH_QUERY,
        {"query": {"searchText": str(query), "size": size, "start": start}},
        "AH zoeken",
    )
    found = data.get("recipeSearch") or {}
    recipes = [_recipe_card(item) for item in found.get("result") or [] if item.get("id") is not None]
    total = int((found.get("page") or {}).get("total") or 0)
    reachable = min(total, RECIPE_SEARCH_WINDOW)
    return {
        "recipes": recipes,
        "total": total,
        "hasNextPage": start + len(recipes) < reachable,
    }


def _search_product_dict(item: dict[str, Any], query: str) -> dict[str, Any]:
    unit_size = item.get("salesUnitSize")
    price_now = item.get("currentPrice") or item.get("priceBeforeBonus") or 0
    product = {
        "webshopId": str(item.get("webshopId") or item.get("id")),
        "title": item.get("title") or query,
        "price": {"now": price_now, "unitSize": unit_size},
        "images": item.get("images") or [],
        "brand": item.get("brand"),
        "unitSize": unit_size,
        "isBonus": bool(item.get("isBonus")),
    }
    if product["isBonus"]:
        product["promo"] = _bonus_promo(item)
    return product


# AH geeft een doorlopende bonus (zoals "5% volume voordeel") als einddatum 2999-12-31. Dat is geen einddatum.
OPEN_ENDED_YEAR = 2900


def _bonus_end(item: dict[str, Any]) -> str | None:
    """De laatste dag van een bonus, of None als die doorloopt of onbekend is."""
    end = item.get("bonusEndDate") or None
    if not end or item.get("isInfiniteBonus"):
        return None
    text = str(end)
    return None if text[:4].isdigit() and int(text[:4]) >= OPEN_ENDED_YEAR else text


def _bonus_promo(item: dict[str, Any]) -> dict[str, Any]:
    """Wat AH over de bonus meegeeft: mechaniek, gewone prijs en looptijd.

    De veldnamen (`bonusMechanism`, `bonusStartDate`, `bonusEndDate`, `discountLabels`) zijn niet gedocumenteerd
    en zijn nog niet met een echte respons gecontroleerd; ontbreekt een veld, dan blijft de waarde leeg en
    wordt de aanbieding als onbekend getoond in plaats van met een gokwaarde.
    """
    labels = [
        str(label.get("defaultDescription") or label.get("description"))
        for label in item.get("discountLabels") or []
        if isinstance(label, dict) and (label.get("defaultDescription") or label.get("description"))
    ]
    return {
        "label": " | ".join(labels) or None,
        "mechanism": item.get("bonusMechanism") or None,
        "priceBefore": item.get("priceBeforeBonus") or None,
        "validFrom": item.get("bonusStartDate") or None,
        "validUntil": _bonus_end(item),
    }


def _get_with_retry(url: str, attempts: int = 3, **kwargs: Any) -> requests.Response:
    """GET die een verbroken verbinding opnieuw probeert. AH verbreekt er af en toe een, vooral bij
    veel zoekopdrachten kort na elkaar (het weekmenu zoekt elk ingrediënt apart). Alleen voor lezen."""
    for attempt in range(1, attempts + 1):
        try:
            return requests.get(url, **kwargs)
        except (requests.ConnectionError, requests.Timeout):
            if attempt == attempts:
                raise
            time.sleep(0.5 * attempt)
    raise AssertionError("onbereikbaar")  # pragma: no cover


def search_products(query: str, limit: int = 5) -> list[dict[str, Any]]:
    token = get_token()
    response = _get_with_retry(
        f"{AH_API_BASE}/mobile-services/product/search/v2",
        headers=_auth_headers(token, include_content_type=False),
        params={"query": query, "page": 0, "size": max(1, int(limit)), "sortOn": "RELEVANCE"},
        timeout=30,
    )
    if not response.ok:
        raise RuntimeError(f"AH productzoekopdracht mislukt ({response.status_code})")
    products = response.json().get("products") or []
    return [_search_product_dict(item, query) for item in products[: max(1, int(limit))]]


def search_product(query: str) -> dict[str, Any] | None:
    products = search_products(query, 1)
    return products[0] if products else None


def product_web_url(product_id: Any) -> str:
    try:
        return f"https://www.ah.nl/producten/product/wi{int(product_id)}"
    except (TypeError, ValueError):
        return ""


def get_products_by_ids(product_ids: list[int]) -> list[dict[str, Any]]:
    if not product_ids:
        return []
    token = get_token()
    params: list[tuple[str, str]] = [("ids", str(product_id)) for product_id in product_ids]
    params.append(("sortOn", "INPUT_PRODUCT_IDS"))
    response = _get_with_retry(
        f"{AH_API_BASE}/mobile-services/product/search/v2/products",
        headers=_auth_headers(token, include_content_type=False),
        params=params,
        timeout=30,
    )
    if not response.ok:
        raise RuntimeError(f"AH producten ophalen mislukt ({response.status_code}): {response.text[:200]}")

    products = []
    for item in response.json() or []:
        products.append(
            {
                "id": item.get("webshopId"),
                "url": product_web_url(item.get("webshopId")),
                "title": item.get("title"),
                "brand": item.get("brand"),
                "unitSize": item.get("salesUnitSize"),
                "price": item.get("currentPrice") or item.get("priceBeforeBonus") or 0,
                "image": ((item.get("images") or [{}])[0]).get("url"),
                "isBonus": bool(item.get("isBonus")),
            }
        )
    return products


# --- Bonus (aanbiedingen) -----------------------------------------------------------------------
# De bonuspagina van de AH-app is een onofficiële route die we niet kunnen garanderen. Daarom lezen we het
# antwoord op vorm (elk object met een `webshopId` en een `title` is een product) en vallen we terug op
# zoekopdrachten met de bonusvlag als de pagina niets teruggeeft. De verkenner (`ah_explore`) toont wat AH geeft.
BONUS_SECTION_PATH = "/mobile-services/bonuspage/v2/section"
BONUS_FALLBACK_TERMS = (
    "kip", "gehakt", "vis", "zalm", "worst", "spek", "rundvlees", "varkensvlees", "pasta", "rijst", "aardappel",
    "groente", "tomaat", "ui", "paprika", "champignon", "salade", "fruit", "kaas", "melk", "yoghurt", "room", "ei",
    "brood", "saus", "soep", "pizza", "noten", "olie", "bonen",
)
BONUS_PAGE_SIZE = 50


def _bonus_label(item: dict[str, Any], group: str = "") -> str:
    labels = item.get("discountLabels")
    if isinstance(labels, list):
        for label in labels:
            text = label.get("defaultDescription") if isinstance(label, dict) else None
            if text:
                return str(text).strip()
    return str(item.get("bonusMechanism") or group or "").strip()


def _bonus_product(item: dict[str, Any], group: str = "") -> dict[str, Any] | None:
    product_id = item.get("webshopId") or item.get("id")
    title = str(item.get("title") or "").strip()
    if not product_id or not title:
        return None
    now = item.get("currentPrice")
    before = item.get("priceBeforeBonus")
    images = item.get("images") or []
    return {
        "id": str(product_id),
        "title": title,
        "brand": item.get("brand") or None,
        "unitSize": item.get("salesUnitSize") or None,
        "price": now if isinstance(now, (int, float)) else before if isinstance(before, (int, float)) else None,
        "priceBefore": before if isinstance(before, (int, float)) and isinstance(now, (int, float)) and before > now else None,
        "promoLabel": _bonus_label(item, group),
        "image": (images[0].get("url") if images and isinstance(images[0], dict) else None) or None,
        "url": product_web_url(product_id),
        "validFrom": item.get("bonusStartDate") or None,
        "validUntil": _bonus_end(item),
        "categoryHint": item.get("mainCategory") or item.get("subCategory") or None,
    }


def bonus_products_in(node: Any, group: str = "") -> list[dict[str, Any]]:
    """Alle producten ergens in een antwoord, met de naam van de bonusgroep waarin ze staan. Geen dubbelen."""
    found: dict[str, dict[str, Any]] = {}

    def walk(inner: Any, label: str) -> None:
        if isinstance(inner, list):
            for entry in inner:
                walk(entry, label)
        elif isinstance(inner, dict):
            product = _bonus_product(inner, label) if "webshopId" in inner and "title" in inner else None
            if product:
                found.setdefault(product["id"], product)
                return
            own = str(inner.get("discountDescription") or inner.get("segmentDescription") or inner.get("title") or label or "")
            for value in inner.values():
                walk(value, own if isinstance(value, (list, dict)) else label)

    walk(node, group)
    return list(found.values())


def _bonus_from_search(term: str, token: str) -> list[dict[str, Any]]:
    response = _get_with_retry(
        f"{AH_API_BASE}/mobile-services/product/search/v2",
        headers=_auth_headers(token, include_content_type=False),
        params={"query": term, "page": 0, "size": BONUS_PAGE_SIZE, "sortOn": "RELEVANCE"},
        timeout=30,
    )
    if not response.ok:
        raise RuntimeError(f"AH productzoekopdracht mislukt ({response.status_code})")
    products = [_bonus_product(item) for item in response.json().get("products") or [] if isinstance(item, dict) and item.get("isBonus")]
    return [product for product in products if product]


def get_bonus_products() -> list[dict[str, Any]]:
    """De aanbiedingen (AH Bonus): eerst de bonuspagina, anders zoekopdrachten met de bonusvlag. Werkt zonder AH-login."""
    token = get_token()
    errors: list[str] = []
    found: dict[str, dict[str, Any]] = {}
    try:
        data = _request_json(
            "GET",
            f"{AH_API_BASE}{BONUS_SECTION_PATH}",
            headers=_auth_headers(token, include_content_type=False),
            params={"date": date.today().isoformat(), "application": "AH", "promotionType": "NATIONAL"},
        )
        for product in bonus_products_in(data):
            found.setdefault(product["id"], product)
    except Exception as exc:  # noqa: BLE001 - onofficiële route; val terug op zoeken
        errors.append(str(exc)[:200])
    if not found:
        def search(term: str) -> list[dict[str, Any]]:
            try:
                return _bonus_from_search(term, token)
            except Exception as exc:  # noqa: BLE001
                errors.append(str(exc)[:200])
                return []

        with ThreadPoolExecutor(max_workers=6) as pool:
            for products in pool.map(search, BONUS_FALLBACK_TERMS):
                for product in products:
                    found.setdefault(product["id"], product)
    if not found and errors:
        raise RuntimeError(errors[0])
    return list(found.values())


def _graphql(token: str, query: str, variables: dict[str, Any], what: str) -> dict[str, Any]:
    response = requests.post(
        f"{AH_API_BASE}/graphql",
        headers=_auth_headers(token),
        json={"query": query, "variables": variables},
        timeout=30,
    )
    if not response.ok:
        raise RuntimeError(f"{what} mislukt ({response.status_code}): {response.text[:200]}")
    data = response.json()
    if data.get("errors"):
        raise RuntimeError(f"{what} mislukt: {data['errors'][0].get('message', 'GraphQL error')}")
    return data.get("data") or {}


def _get_favorite_lists_rest(token: str, product_id: int) -> list[dict[str, Any]]:
    # Oude REST-route; AH geeft hier inmiddels een 404 op. Alleen vangnet.
    # AH eist een productId maar geeft alle lijstjes terug; welke waarde wordt
    # geaccepteerd verschilt per AH-release, dus probeer er een paar.
    response = None
    for candidate in (max(1, int(product_id or 1)), 0):
        response = requests.get(
            f"{AH_API_BASE}/mobile-services/lists/v3/lists",
            headers=_auth_headers(token, include_content_type=False),
            params={"productId": candidate},
            timeout=30,
        )
        if response.ok or response.status_code not in {400, 404, 422}:
            break
    if not response.ok:
        raise RuntimeError(f"AH lijstjes ophalen mislukt ({response.status_code}): {response.text[:200]}")
    return [
        {
            "id": item.get("id"),
            "name": item.get("description"),
            "itemCount": item.get("itemCount") or 0,
        }
        for item in response.json() or []
    ]


def get_favorite_lists(product_id: int = 1) -> list[dict[str, Any]]:
    token = get_user_token()
    try:
        # Een lege ids-lijst geeft alle lijstjes terug.
        data = _graphql(token, FAVORITE_LISTS_QUERY, {"ids": []}, "AH lijstjes ophalen")
    except RuntimeError as graphql_error:
        try:
            return _get_favorite_lists_rest(token, product_id)
        except RuntimeError as rest_error:
            raise RuntimeError(f"{graphql_error}; REST: {rest_error}") from rest_error
    return [
        {
            "id": item.get("id"),
            "name": item.get("description"),
            "itemCount": item.get("totalSize") or 0,
            "updatedAt": item.get("updatedAt"),
        }
        for item in data.get("favoriteListV2") or []
    ]


def get_favorite_list_items(list_id: str) -> dict[str, Any]:
    token = get_user_token()
    data = _graphql(
        token,
        FAVORITE_LIST_QUERY,
        {"ids": [str(list_id).upper()]},
        "AH lijstinhoud ophalen",
    )
    lists = data.get("favoriteListV2") or []
    if not lists:
        raise RuntimeError("AH lijst niet gevonden")

    favorite_list = lists[0]
    raw_items = favorite_list.get("items") or []
    product_ids = [int(item.get("productId")) for item in raw_items if item.get("productId")]
    product_map = {int(product["id"]): product for product in get_products_by_ids(product_ids) if product.get("id")}
    items = []
    for item in raw_items:
        product_id = int(item.get("productId") or 0)
        product = product_map.get(product_id)
        items.append(
            {
                "id": item.get("id"),
                "productId": product_id,
                "url": (product or {}).get("url") or product_web_url(product_id),
                "quantity": max(1, int(item.get("quantity") or 1)),
                "product": product,
            }
        )

    return {
        "id": favorite_list.get("id"),
        "name": favorite_list.get("description"),
        "totalSize": favorite_list.get("totalSize") or len(items),
        "items": items,
    }


def _recipe_web_url(item: dict[str, Any]) -> str:
    web_path = (item.get("webPath") or item.get("href") or "").strip()
    if web_path:
        if web_path.startswith("http"):
            return web_path
        return f"https://www.ah.nl{web_path if web_path.startswith('/') else '/' + web_path}"
    recipe_id = item.get("id") or item.get("recipeId")
    if recipe_id:
        return f"https://www.ah.nl/allerhande/recept/R-R{recipe_id}"
    return ""


def _normalize_recipe(item: Any) -> dict[str, Any] | None:
    if isinstance(item, (int, str)):
        recipe_id = str(item)
        return {
            "id": recipe_id,
            "title": f"Recept {recipe_id}",
            "image": "",
            "url": f"https://www.ah.nl/allerhande/recept/R-R{recipe_id}",
        }
    if not isinstance(item, dict):
        return None

    images = item.get("images") or []
    image = ""
    if images and isinstance(images[0], dict):
        image = images[0].get("url") or ""
    elif isinstance(item.get("image"), str):
        image = item.get("image") or ""

    url = _recipe_web_url(item)
    if not url:
        return None

    return {
        "id": item.get("id") or item.get("recipeId"),
        "title": item.get("title") or item.get("name") or "Onbekend recept",
        "image": image,
        "url": url,
    }


def _fetch_recipe_details(token: str, recipe_ids: list[int]) -> list[dict[str, Any]]:
    """Hydrate recipe ids to full recipe objects. Falls back to id-only cards."""
    if not recipe_ids:
        return []
    params: list[tuple[str, str]] = [("ids", str(recipe_id)) for recipe_id in recipe_ids]
    try:
        response = requests.get(
            f"{AH_API_BASE}/mobile-services/recipes/v1/recipe/by-ids",
            headers=_auth_headers(token, include_content_type=False),
            params=params,
            timeout=20,
        )
        if response.ok:
            data = response.json()
            if isinstance(data, dict):
                data = (
                    data.get("recipes")
                    or data.get("result")
                    or data.get("items")
                    or []
                )
            details = [normalized for item in (data or []) if (normalized := _normalize_recipe(item))]
            if details:
                return details
    except (requests.exceptions.RequestException, ValueError):
        pass
    # Fallback: build minimal cards from the ids (links still import in Mealie).
    return [normalized for rid in recipe_ids if (normalized := _normalize_recipe(rid))]


def _graphql_variants(
    token: str, queries: tuple[str, ...], variables: dict[str, Any], what: str
) -> dict[str, Any]:
    """Probeer query-varianten achter elkaar; de fouten van alle varianten staan in de melding."""
    errors: list[str] = []
    for query in queries:
        try:
            return _graphql(token, query, variables, what)
        except RuntimeError as exc:
            errors.append(str(exc))
    raise RuntimeError(" | ".join(dict.fromkeys(errors)))


def _recipe_page(recipes: list[dict[str, Any]], total: int, page: int, size: int) -> dict[str, Any]:
    size = max(1, int(size))
    start = max(0, int(page)) * size
    return {"recipes": recipes[start:start + size], "total": max(total, len(recipes))}


def _collection_recipe_ids(token: str) -> list[int]:
    data = _graphql_variants(
        token, RECIPE_COLLECTIONS_QUERIES, {}, "AH receptencollectie ophalen"
    )
    ids: list[int] = []
    seen: set[int] = set()
    for category in data.get("recipeCollectionCategories") or []:
        for entry in category.get("recipes") or []:
            raw = entry.get("id") if isinstance(entry, dict) else entry
            try:
                recipe_id = int(raw)
            except (TypeError, ValueError):
                continue
            if recipe_id not in seen:
                seen.add(recipe_id)
                ids.append(recipe_id)
    return ids


def _recipe_summaries(token: str, recipe_ids: list[int]) -> dict[int, dict[str, Any]]:
    data = _graphql_variants(
        token, RECIPE_SUMMARIES_QUERIES, {"ids": recipe_ids}, "AH receptdetails ophalen"
    )
    summaries: dict[int, dict[str, Any]] = {}
    for item in data.get("recipeSummaries") or []:
        try:
            recipe_id = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        images = item.get("images") or []
        image = images[0].get("url") if images and isinstance(images[0], dict) else ""
        slug = (item.get("slug") or "").strip()
        summaries[recipe_id] = {
            "id": str(recipe_id),
            "title": item.get("title") or f"Recept {recipe_id}",
            "image": image or "",
            "url": f"https://www.ah.nl/allerhande/recept/R-R{recipe_id}"
            + (f"/{slug}" if slug else ""),
        }
    return summaries


def _get_saved_recipes_collections(page: int, size: int) -> dict[str, Any]:
    """Bewaarde recepten uit de receptencollectie van het lid (GraphQL)."""
    token = get_user_token()
    ids = _collection_recipe_ids(token)
    size = max(1, int(size))
    start = max(0, int(page)) * size
    page_ids = ids[start:start + size]
    if not page_ids:
        return {"recipes": [], "total": len(ids)}
    try:
        summaries = _recipe_summaries(token, page_ids)
    except RuntimeError:
        summaries = {}
    recipes = []
    for recipe_id in page_ids:
        recipe = summaries.get(recipe_id)
        if recipe is None:
            # Zonder details blijft er een kaart met id en werkende link over.
            recipe = _normalize_recipe(recipe_id)
        if recipe:
            recipes.append(recipe)
    return {"recipes": recipes, "total": len(ids)}


def _get_saved_recipes_cookbook(page: int, size: int) -> dict[str, Any]:
    """Verzamelde recepten uit het kookboek van het lid (GraphQL)."""
    token = get_user_token()
    data = _graphql(token, COOKBOOK_RECIPES_QUERY, {}, "AH kookboek ophalen")
    collected = data.get("cookBookRecipesCollectedMember") or {}
    recipes = []
    for item in collected.get("result") or []:
        recipe_id = item.get("id")
        if recipe_id is None:
            continue
        recipes.append(
            {
                "id": str(recipe_id),
                "title": item.get("title") or f"Recept {recipe_id}",
                "image": "",
                "url": f"https://www.ah.nl/allerhande/recept/R-R{recipe_id}",
            }
        )
    total = (collected.get("page") or {}).get("total") or len(recipes)
    return _recipe_page(recipes, total, page, size)


def _get_saved_recipes_api(page: int, size: int) -> dict[str, Any]:
    """Bewaarde recepten via de OAuth-koppeling: eerst de receptencollectie, dan het kookboek."""
    errors: list[str] = []
    empty: dict[str, Any] | None = None
    for label, source in (
        ("collectie", _get_saved_recipes_collections),
        ("kookboek", _get_saved_recipes_cookbook),
    ):
        try:
            result = source(page, size)
        except RuntimeError as exc:
            errors.append(f"{label}: {exc}")
            continue
        if result["recipes"] or result["total"]:
            return result
        empty = result
    if empty is not None:
        return empty
    raise RuntimeError("; ".join(errors))


def get_saved_recipes(page: int = 0, size: int = 50) -> dict[str, Any]:
    """Bewaarde recepten uit de receptencollectie van het AH-account (GraphQL)."""
    if not configured_refresh_token():
        raise RuntimeError(
            tr(
                "AH account niet gekoppeld. De beheerder koppelt het bij Instellingen > Koppelen > Albert Heijn "
                "om bewaarde recepten op te halen.",
                "AH account not linked. The admin links it under Settings > Link > Albert Heijn "
                "to fetch saved recipes.",
            )
        )
    return _get_saved_recipes_api(page, size)


def add_to_shopping_list(items: list[dict[str, Any]]) -> None:
    token = get_user_token()
    # AH weigt hetzelfde product twee keer in één verzoek met een 409 ("Grocery items cannot be
    # duplicate"). Twee recepten in het weekmenu die dezelfde melk nodig hebben worden dus één regel.
    by_product: dict[int, dict[str, Any]] = {}
    for item in items:
        product_id_raw = item.get("productId")
        try:
            product_id = int(product_id_raw)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"Ongeldig productId voor AH winkelmandje: {product_id_raw}") from exc

        quantity = normalize_quantity(item.get("quantity"))
        description = str(item.get("title") or item.get("query") or "").strip()
        if product_id in by_product:
            line = by_product[product_id]
            line["quantity"] = min(MAX_CART_QUANTITY, line["quantity"] + quantity)
            if not line["description"]:
                line["description"] = line["searchTerm"] = description
            continue
        by_product[product_id] = {
            "description": description,
            "productId": product_id,
            "quantity": quantity,
            "type": "SHOPPABLE",
            "originCode": "PRD",
            "searchTerm": description,
            "strikeThrough": False,
        }
    shopping_items = list(by_product.values())

    if not shopping_items:
        raise RuntimeError("Geen geldige producten om toe te voegen")

    payload = {"items": shopping_items}
    response = requests.patch(
        f"{AH_API_BASE}/mobile-services/shoppinglist/v2/items",
        headers=_auth_headers(token),
        json=payload,
        timeout=30,
    )
    if response.status_code == 412 and "order mode" in response.text.lower():
        # Er staat een bestelling open: dan weigert AH de boodschappenlijst. Zeg dat in gewone woorden.
        raise RuntimeError(
            tr(
                "Er staat een bestelling open bij AH, bijvoorbeeld op een ander apparaat (AH-app of website). "
                "Sluit die bestelling eerst af en probeer het daarna opnieuw (412, Server in order mode).",
                "An order is open at AH, for example on another device (AH app or website). "
                "Close that order first and try again (412, Server in order mode).",
            )
        )
    if not response.ok:
        raise RuntimeError(f"AH winkelmandje vullen mislukt ({response.status_code}): {response.text}")


# --- Bestellingen, het bestelmandje en kassabonnen (alleen lezen) ---------------------------------
# Onofficiële routes van de AH-app, zoals de community-bibliotheek appie-go ze gebruikt. Het
# bestelmandje is de actieve bestelling bij AH, iets anders dan de boodschappenlijst waar
# add_to_shopping_list naartoe schrijft. De antwoorden worden hier tot platte dicts herleid;
# ontbrekende velden worden leeg in plaats van een fout.


def _number(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _get_json(path: str, params: dict[str, Any] | None = None) -> Any:
    return _request_json(
        "GET",
        f"{AH_API_BASE}{path}",
        headers=_auth_headers(get_user_token(), include_content_type=False),
        params=params,
    )


def _order_item(entry: dict[str, Any], taxonomy: str = "") -> dict[str, Any]:
    product = entry.get("product") or {}
    images = product.get("images") or []
    # In de bestelling is `amount` een aantal (een geheel getal), geen bedrag: het bedrag van de regel is aantal x prijs.
    # Alleen een kommagetal zonder bekende prijs behandelen we als bedrag.
    quantity = _int(entry.get("quantity")) or _int(entry.get("amount")) or 0
    price = _number(product.get("currentPrice") if product.get("currentPrice") is not None else product.get("priceBeforeBonus"))
    return {
        "productId": _int(product.get("webshopId") or product.get("id")),
        "title": _text(product.get("title")),
        "brand": _text(product.get("brand")),
        "unitSize": _text(product.get("salesUnitSize")),
        "quantity": quantity,
        "amount": round(quantity * price, 2) if price is not None else (_number(entry.get("amount")) if isinstance(entry.get("amount"), float) else None),
        "price": price,
        "priceBeforeBonus": _number(product.get("priceBeforeBonus")),
        "bonus": bool(product.get("isBonus")),
        "taxonomy": _text(taxonomy),
        "image": images[0].get("url") if images and isinstance(images[0], dict) else "",
    }


def get_shopping_list() -> dict[str, Any]:
    """Het AH-mandje zoals de app het toont: de boodschappenlijst waar `add_to_shopping_list` in schrijft.

    Het antwoord is `{items: [{quantity, strikedthrough, type, productDetails: {product: {...}}}]}`. Een vrije tekstregel heeft
    geen product maar een `description`. Afgevinkte regels (`strikedthrough`) laten we weg. De lezing is ruim: ook een kale
    lijst, `product` direct in de regel en de schrijfwijze `strikeThrough` worden begrepen.
    """
    data = _get_json("/mobile-services/shoppinglist/v2/items") or {}
    entries = data if isinstance(data, list) else data.get("items") or data.get("products") or []
    items = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("strikedthrough") or entry.get("strikeThrough"):
            continue
        details = entry.get("productDetails") if isinstance(entry.get("productDetails"), dict) else {}
        product = details.get("product") if isinstance(details.get("product"), dict) else entry.get("product")
        product = product if isinstance(product, dict) else {}
        title = _text(product.get("title") or entry.get("description") or entry.get("searchTerm"))
        if not title:
            continue
        quantity = _int(entry.get("quantity")) or 1
        item = _order_item({"quantity": quantity, "product": {**product, "webshopId": product.get("webshopId") or entry.get("productId"), "title": title}})
        items.append({**item, "quantity": quantity})
    return {"id": None, "state": "", "shoppingType": "", "total": None, "discount": None, "deliveryDate": "", "slotStart": "", "slotEnd": "", "items": items}


def get_active_order() -> dict[str, Any]:
    """Het bestelmandje: de actieve bestelling bij AH (producten, totaal, gekozen bezorgmoment)."""
    try:
        data = _get_json("/mobile-services/order/v1/summaries/active", {"sortBy": "DEFAULT"}) or {}
    except RuntimeError as exc:
        # Zonder actieve bestelling antwoordt AH met een 404: dan is het mandje gewoon leeg.
        if not str(exc).startswith("404") or "Order does not exist" not in str(exc):
            raise
        data = {}
    total = data.get("totalPrice") or {}
    delivery = data.get("deliveryInformation") or {}
    return {
        "id": _int(data.get("id")),
        "state": _text(data.get("state")),
        "shoppingType": _text(data.get("shoppingType")),
        "total": _number(total.get("priceTotalPayable")),
        "discount": _number(total.get("priceDiscount")),
        "deliveryDate": _text(delivery.get("deliveryDate")),
        "slotStart": _text(delivery.get("deliveryStartTime")),
        "slotEnd": _text(delivery.get("deliveryEndTime")),
        "items": [_order_item(entry) for entry in data.get("orderedProducts") or [] if isinstance(entry, dict)],
    }


def get_order_details(order_id: int) -> dict[str, Any]:
    """Een bestelling met zijn producten, per categorie (werkt voor elke bestelling-id)."""
    data = _get_json(f"/mobile-services/order/v1/{int(order_id)}/details-grouped-by-taxonomy") or {}
    period = data.get("deliveryTimePeriod") or {}
    items = []
    for group in data.get("groupedProductsInTaxonomy") or []:
        for entry in group.get("orderedProducts") or []:
            if isinstance(entry, dict):
                items.append(_order_item(entry, group.get("taxonomyName") or ""))
    return {
        "id": _int(data.get("orderId")) or int(order_id),
        "state": _text(data.get("orderState")),
        "deliveryType": _text(data.get("deliveryType")),
        "deliveryDate": _text(data.get("deliveryDate")),
        "slotStart": _text(period.get("startDateTime")),
        "slotEnd": _text(period.get("endDateTime")),
        "items": items,
    }


FULFILLMENTS_QUERY = """
query OrderFulfillments {
  orderFulfillments(status: OPEN) {
    result {
      orderId statusCode statusDescription shoppingType transactionCompleted modifiable
      totalPrice { totalPrice { amount } }
      delivery { status method slot { date dateDisplay timeDisplay startTime endTime } }
    }
  }
}
"""


def get_open_fulfillments() -> list[dict[str, Any]]:
    """Geplaatste bestellingen die nog niet zijn geleverd."""
    return _fulfillments("OPEN")


def get_closed_fulfillments(offset: int = 0) -> list[dict[str, Any]]:
    """Afgeronde bestellingen, nieuwste eerst: afgeleverd of geannuleerd (`deliveryStatus`). AH geeft er per keer 10; `offset` bladert verder."""
    return _fulfillments("CLOSED", offset)


def _fulfillments(status: str, offset: int = 0) -> list[dict[str, Any]]:
    arguments = f"status: {status}" + (f", offset: {int(offset)}" if offset else "")
    data = _graphql(get_user_token(), FULFILLMENTS_QUERY.replace("status: OPEN", arguments), {}, "AH bestellingen ophalen")
    result = ((data.get("orderFulfillments") or {}).get("result")) or []
    orders = []
    for entry in result:
        if not isinstance(entry, dict) or entry.get("orderId") is None:
            continue
        delivery = entry.get("delivery") or {}
        slot = delivery.get("slot") or {}
        orders.append(
            {
                "id": _int(entry.get("orderId")),
                "state": _text(entry.get("statusCode") or delivery.get("status")),
                "stateText": _text(entry.get("statusDescription")),
                "shoppingType": _text(entry.get("shoppingType")),
                "method": _text(delivery.get("method")),
                "deliveryStatus": _text(delivery.get("status")),
                "modifiable": bool(entry.get("modifiable")),
                "total": _number(((entry.get("totalPrice") or {}).get("totalPrice") or {}).get("amount")),
                "deliveryDate": _text(slot.get("date")),
                "slotStart": _text(slot.get("startTime")),
                "slotEnd": _text(slot.get("endTime")),
            }
        )
    return orders


RECEIPTS_QUERY = """
query FetchPosReceipts($offset: Int!, $limit: Int!) {
  posReceiptsPage(pagination: {offset: $offset, limit: $limit}) {
    posReceipts { id dateTime totalAmount { amount } }
  }
}
"""

RECEIPT_QUERY = """
query FetchReceipt($id: String!) {
  posReceiptDetails(id: $id) {
    id
    products { id quantity name price { amount } amount { amount } }
    discounts { name amount { amount } }
    payments { method amount { amount } }
  }
}
"""


def get_receipts(offset: int = 0, limit: int = 100) -> list[dict[str, Any]]:
    """Kassabonnen van aankopen in de winkel, nieuwste eerst (een pagina)."""
    data = _graphql(get_user_token(), RECEIPTS_QUERY, {"offset": int(offset), "limit": int(limit)}, "AH kassabonnen ophalen")
    page = ((data.get("posReceiptsPage") or {}).get("posReceipts")) or []
    return [
        {
            "id": _text(entry.get("id")),
            "dateTime": _text(entry.get("dateTime")),
            "total": _number((entry.get("totalAmount") or {}).get("amount")),
        }
        for entry in page
        if isinstance(entry, dict) and entry.get("id")
    ]


def get_receipt(receipt_id: str) -> dict[str, Any]:
    """Eén kassabon met regels, kortingen en betalingen."""
    data = _graphql(get_user_token(), RECEIPT_QUERY, {"id": str(receipt_id)}, "AH kassabon ophalen")
    detail = data.get("posReceiptDetails") or {}
    return {
        "id": _text(detail.get("id")) or str(receipt_id),
        "items": [
            {
                "productId": _int(line.get("id")),
                "name": _text(line.get("name")),
                "quantity": _int(line.get("quantity")) or 0,
                "price": _number((line.get("price") or {}).get("amount")),
                "amount": _number((line.get("amount") or {}).get("amount")),
            }
            for line in detail.get("products") or []
            if isinstance(line, dict)
        ],
        "discounts": [
            {"name": _text(entry.get("name")), "amount": _number((entry.get("amount") or {}).get("amount"))}
            for entry in detail.get("discounts") or []
            if isinstance(entry, dict)
        ],
        "payments": [
            {"method": _text(entry.get("method")), "amount": _number((entry.get("amount") or {}).get("amount"))}
            for entry in detail.get("payments") or []
            if isinstance(entry, dict)
        ],
    }


_ALLERHANDE_RECIPE_RE = re.compile(r"ah\.nl/allerhande/recept/R-R(\d+)", re.IGNORECASE)


def allerhande_recipe_id(url: str) -> int | None:
    match = _ALLERHANDE_RECIPE_RE.search(str(url or ""))
    return int(match.group(1)) if match else None


def _iso_minutes(minutes: Any) -> str | None:
    try:
        value = int(minutes)
    except (TypeError, ValueError):
        return None
    return f"PT{value}M" if value > 0 else None


def get_recipe_jsonld(recipe_id: int) -> dict[str, Any]:
    """Allerhande-recept als schema.org Recipe (JSON-LD), via de AH-API zonder paginascraping."""
    data = _graphql_variants(
        get_token(), RECIPE_DETAIL_QUERIES, {"id": int(recipe_id)}, "AH recept ophalen"
    )
    recipe = data.get("recipe")
    if not recipe:
        raise RuntimeError("AH recept niet gevonden")

    ingredients = [
        str(item.get("text")).strip()
        for item in recipe.get("ingredients") or []
        if isinstance(item, dict) and str(item.get("text") or "").strip()
    ]
    steps = []
    for step in (recipe.get("preparation") or {}).get("steps") or []:
        text = step.get("text") if isinstance(step, dict) else step
        if str(text or "").strip():
            steps.append({"@type": "HowToStep", "text": str(text).strip()})
    if not ingredients and not steps:
        raise RuntimeError("AH recept heeft geen ingredienten of bereiding")

    times = [recipe.get(key) or 0 for key in ("cookTime", "ovenTime", "waitTime")]
    images = [
        image.get("url")
        for image in recipe.get("images") or []
        if isinstance(image, dict) and image.get("url")
    ]
    servings = (recipe.get("servings") or {}).get("number")

    ld: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "Recipe",
        "name": recipe.get("title") or f"Recept {recipe_id}",
        "url": f"https://www.ah.nl/allerhande/recept/R-R{recipe_id}",
        "recipeIngredient": ingredients,
        "recipeInstructions": steps,
    }
    if recipe.get("description"):
        ld["description"] = recipe["description"]
    if images:
        ld["image"] = images
    if servings:
        ld["recipeYield"] = f"{servings} personen"
    if iso := _iso_minutes(recipe.get("cookTime")):
        ld["cookTime"] = iso
    if iso := _iso_minutes(sum(int(t) for t in times if isinstance(t, (int, float)))):
        ld["totalTime"] = iso
    return ld
