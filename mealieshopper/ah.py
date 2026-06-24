import json
import time
from dataclasses import dataclass
from os import environ
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from . import ah_browser, auth

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


def extract_oauth_code(value: str) -> str:
    candidate = str(value or "").strip()
    if "code=" not in candidate:
        return candidate
    parsed = urlparse(candidate)
    code = parse_qs(parsed.query).get("code", [""])[0]
    return code.strip() or candidate


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
            "AH account niet gekoppeld. Log in via de tab AH koppelen."
        )

    data = _request_json(
        "POST",
        f"{AH_API_BASE}/mobile-auth/v1/auth/token/refresh",
        headers=_auth_headers(),
        json={"clientId": AH_CLIENT_ID, "refreshToken": refresh_token},
    )
    user_token_cache = TokenCache(
        token=data["access_token"],
        expires_at=time.time() + max(0, int(data.get("expires_in", 3600)) - 60),
    )
    return user_token_cache.token


def configured_refresh_token() -> str:
    return environ.get("AH_REFRESH_TOKEN", "").strip() or auth.get_secret("AH_REFRESH_TOKEN")


def save_refresh_token(refresh_token: str) -> None:
    token = str(refresh_token or "").strip()
    if not token:
        raise RuntimeError("Refresh token ontbreekt")
    auth.set_secret("AH_REFRESH_TOKEN", token)


# ---------- Browser (website) credentials for saved-recipe scraping ----------


def configured_browser_credentials() -> tuple[str, str]:
    username = (environ.get("AH_USERNAME") or "").strip() or auth.get_secret("AH_USERNAME")
    password = (environ.get("AH_PASSWORD") or "").strip() or auth.get_secret("AH_PASSWORD")
    return username.strip(), password


def save_browser_credentials(username: str, password: str) -> None:
    username = str(username or "").strip()
    password = str(password or "")
    if not username or not password:
        raise RuntimeError("E-mail en wachtwoord zijn verplicht")
    auth.set_secret("AH_USERNAME", username)
    auth.set_secret("AH_PASSWORD", password)


def clear_browser_credentials() -> None:
    auth.set_secret("AH_USERNAME", "")
    auth.set_secret("AH_PASSWORD", "")
    _save_browser_cookies({})


def _save_browser_cookies(cookies: dict[str, str]) -> None:
    try:
        auth.set_secret("AH_COOKIES", json.dumps(cookies or {}))
    except Exception:
        pass


def _load_browser_cookies() -> dict[str, str]:
    raw = auth.get_secret("AH_COOKIES")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def link_browser_account(username: str, password: str) -> dict[str, Any]:
    username = str(username or "").strip()
    password = str(password or "")
    if not username or not password:
        return {"connected": False, "error": "E-mail en wachtwoord zijn verplicht"}
    try:
        cookies = ah_browser.browser_login(username, password)
    except Exception as exc:
        return {"connected": False, "error": str(exc)}

    save_browser_credentials(username, password)
    _save_browser_cookies(cookies)
    return {"connected": True}


def browser_auth_status() -> dict[str, Any]:
    username, password = configured_browser_credentials()
    return {"connected": bool(username and password), "username": username}


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
                save_refresh_token(raw)
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
    return {
        "connected": bool(result.get("ok")),
        "verified": True,
        "type": result.get("type"),
        "error": result.get("error"),
    }


def exchange_oauth_code(code: str, redirect_uri: str | None = None) -> dict[str, str]:
    payload: dict[str, str] = {"clientId": AH_CLIENT_ID, "code": extract_oauth_code(code)}
    if redirect_uri:
        payload["redirect_uri"] = redirect_uri

    data = _request_json(
        "POST",
        f"{AH_API_BASE}/mobile-auth/v1/auth/token",
        headers=_auth_headers(),
        json=payload,
    )
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
        return {"ok": True, "type": "refresh"}

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


def search_recipes(query: str, page: int = 0, size: int = 12) -> dict[str, Any]:
    token = get_token()
    response = requests.get(
        f"{AH_API_BASE}/mobile-services/recipe/search/v2",
        headers=_auth_headers(token, include_content_type=False),
        params={
            "searchTerms": query,
            "page": page,
            "size": size,
            "sortBy": "RELEVANCE",
        },
        timeout=30,
    )
    if not response.ok:
        raise RuntimeError(f"AH zoeken mislukt ({response.status_code})")

    data = response.json()
    return {
        "recipes": data.get("result") or data.get("recipes") or [],
        "total": data.get("totalFound") or data.get("total") or 0,
    }


def search_product(query: str) -> dict[str, Any] | None:
    token = get_token()
    response = requests.get(
        f"{AH_API_BASE}/mobile-services/product/search/v2",
        headers=_auth_headers(token, include_content_type=False),
        params={"query": query, "page": 0, "size": 1, "sortOn": "RELEVANCE"},
        timeout=30,
    )
    if not response.ok:
        raise RuntimeError(f"AH productzoekopdracht mislukt ({response.status_code})")

    data = response.json()
    products = data.get("products") or []
    if not products:
        return None

    first = products[0]
    unit_size = first.get("salesUnitSize")
    price_now = first.get("currentPrice") or first.get("priceBeforeBonus") or 0
    return {
        "webshopId": str(first.get("webshopId") or first.get("id")),
        "title": first.get("title") or query,
        "price": {"now": price_now, "unitSize": unit_size},
        "images": first.get("images") or [],
        "brand": first.get("brand"),
        "unitSize": unit_size,
    }


def get_products_by_ids(product_ids: list[int]) -> list[dict[str, Any]]:
    if not product_ids:
        return []
    token = get_token()
    params: list[tuple[str, str]] = [("ids", str(product_id)) for product_id in product_ids]
    params.append(("sortOn", "INPUT_PRODUCT_IDS"))
    response = requests.get(
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
                "title": item.get("title"),
                "brand": item.get("brand"),
                "unitSize": item.get("salesUnitSize"),
                "price": item.get("currentPrice") or item.get("priceBeforeBonus") or 0,
                "image": ((item.get("images") or [{}])[0]).get("url"),
                "isBonus": bool(item.get("isBonus")),
            }
        )
    return products


def get_favorite_lists(product_id: int = 1) -> list[dict[str, Any]]:
    token = get_user_token()
    response = requests.get(
        f"{AH_API_BASE}/mobile-services/lists/v3/lists",
        headers=_auth_headers(token, include_content_type=False),
        params={"productId": max(1, int(product_id or 1))},
        timeout=30,
    )
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


def get_favorite_list_items(list_id: str) -> dict[str, Any]:
    token = get_user_token()
    response = requests.post(
        f"{AH_API_BASE}/graphql",
        headers=_auth_headers(token),
        json={"query": FAVORITE_LIST_QUERY, "variables": {"ids": [str(list_id).upper()]}},
        timeout=30,
    )
    if not response.ok:
        raise RuntimeError(f"AH lijstinhoud ophalen mislukt ({response.status_code}): {response.text[:200]}")
    data = response.json()
    if data.get("errors"):
        raise RuntimeError(f"AH lijstinhoud ophalen mislukt: {data['errors'][0].get('message', 'GraphQL error')}")

    lists = data.get("data", {}).get("favoriteListV2") or []
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


def _extract_recipe_ids(data: Any) -> list[int]:
    """Parse favourite recipe ids from the various shapes AH may return."""
    if isinstance(data, dict):
        data = (
            data.get("recipeIds")
            or data.get("ids")
            or data.get("result")
            or data.get("favourites")
            or data.get("favorites")
            or data.get("items")
            or []
        )
    ids: list[int] = []
    for entry in data or []:
        if isinstance(entry, dict):
            entry = entry.get("id") or entry.get("recipeId")
        try:
            if entry is not None:
                ids.append(int(entry))
        except (TypeError, ValueError):
            continue
    return ids


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


def get_saved_recipes(page: int = 0, size: int = 50) -> dict[str, Any]:
    username, password = configured_browser_credentials()
    if not username or not password:
        raise RuntimeError(
            "AH website-koppeling ontbreekt. Voeg je e-mailadres en wachtwoord "
            "toe bij Beheer > Albert Heijn om bewaarde recepten op te halen."
        )

    cookies = _load_browser_cookies()
    try:
        result = ah_browser.scrape_saved_recipes(cookies, username, password)
    except Exception as exc:
        raise RuntimeError(f"AH bewaarde recepten ophalen mislukt: {exc}")

    fresh_cookies = result.get("cookies") if isinstance(result, dict) else None
    if isinstance(fresh_cookies, dict) and fresh_cookies and fresh_cookies != cookies:
        _save_browser_cookies(fresh_cookies)

    recipes = result.get("recipes", []) if isinstance(result, dict) else []
    total = result.get("total", len(recipes)) if isinstance(result, dict) else len(recipes)

    start = max(0, int(page)) * max(1, int(size))
    end = start + max(1, int(size))
    return {"recipes": recipes[start:end], "total": total}


def add_to_shopping_list(items: list[dict[str, Any]]) -> None:
    token = get_user_token()
    shopping_items = []
    for item in items:
        product_id_raw = item.get("productId")
        try:
            product_id = int(product_id_raw)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"Ongeldig productId voor AH winkelmandje: {product_id_raw}") from exc

        quantity = max(1, int(item.get("quantity") or 1))
        description = str(item.get("title") or item.get("query") or "").strip()
        shopping_items.append(
            {
                "description": description,
                "productId": product_id,
                "quantity": quantity,
                "type": "SHOPPABLE",
                "originCode": "PRD",
                "searchTerm": description,
                "strikeThrough": False,
            }
        )

    if not shopping_items:
        raise RuntimeError("Geen geldige producten om toe te voegen")

    payload = {"items": shopping_items}
    response = requests.patch(
        f"{AH_API_BASE}/mobile-services/shoppinglist/v2/items",
        headers=_auth_headers(token),
        json=payload,
        timeout=30,
    )
    if not response.ok:
        raise RuntimeError(f"AH winkelmandje vullen mislukt ({response.status_code}): {response.text}")
