import time
from dataclasses import dataclass
from os import environ
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from . import auth

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
