import time
from dataclasses import dataclass
from os import environ
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests

from . import auth

AH_API_BASE = "https://api.ah.nl"
AH_CLIENT_ID = "appie"
AH_AUTH_HEADERS = {"Content-Type": "application/json"}

PRODUCT_SEARCH_QUERY = """
  query Search($input: SearchProductsInput!) {
    searchProductsExperimental(input: $input) {
      products {
        id
        title
        brand
        salesUnitSize
        priceV2 { now }
      }
      totalFound
    }
  }
"""

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


@dataclass
class TokenCache:
    token: str
    expires_at: float


anon_token_cache: TokenCache | None = None
user_token_cache: TokenCache | None = None


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
        headers=AH_AUTH_HEADERS,
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
        headers=AH_AUTH_HEADERS,
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


def auth_status() -> dict[str, Any]:
    token = configured_refresh_token()
    if not token:
        return {"connected": False}
    result = verify_token(token)
    return {"connected": bool(result.get("ok")), "type": result.get("type"), "error": result.get("error")}


def exchange_oauth_code(code: str, redirect_uri: str | None = None) -> dict[str, str]:
    payload: dict[str, str] = {"clientId": AH_CLIENT_ID, "code": extract_oauth_code(code)}
    if redirect_uri:
        payload["redirect_uri"] = redirect_uri

    data = _request_json(
        "POST",
        f"{AH_API_BASE}/mobile-auth/v1/auth/token",
        headers=AH_AUTH_HEADERS,
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
        headers=AH_AUTH_HEADERS,
        json={"clientId": AH_CLIENT_ID, "refreshToken": token},
        timeout=30,
    )
    if refresh_response.ok:
        return {"ok": True, "type": "refresh"}

    access_response = requests.get(
        f"{AH_API_BASE}/mobile-services/member/v2/profile",
        headers={"Authorization": f"Bearer {token}"},
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
        headers={"Authorization": f"Bearer {token}"},
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
    response = requests.post(
        f"{AH_API_BASE}/graphql",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        json={"query": PRODUCT_SEARCH_QUERY, "variables": {"input": {"query": query}}},
        timeout=30,
    )
    if not response.ok:
        raise RuntimeError(f"AH productzoekopdracht mislukt ({response.status_code})")

    data = response.json()
    if data.get("errors"):
        return None

    products = data.get("data", {}).get("searchProductsExperimental", {}).get("products", [])
    if not products:
        return None

    first = products[0]
    unit_size = first.get("salesUnitSize")
    return {
        "webshopId": str(first["id"]),
        "title": first.get("title") or query,
        "price": {"now": first.get("priceV2", {}).get("now") or 0, "unitSize": unit_size},
        "images": [],
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
        headers={"Authorization": f"Bearer {token}"},
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
        headers={"Authorization": f"Bearer {token}"},
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
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
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
    payload = [
        {"type": "PRODUCT", "productId": item["productId"], "quantity": item["quantity"]}
        for item in items
    ]
    response = requests.post(
        f"{AH_API_BASE}/mobile-services/shoppinglist/v2",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        json=payload,
        timeout=30,
    )
    if not response.ok:
        raise RuntimeError(f"AH winkelmandje vullen mislukt ({response.status_code}): {response.text}")
