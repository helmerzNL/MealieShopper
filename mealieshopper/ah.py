import time
from dataclasses import dataclass
from os import environ
from typing import Any

import requests

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

    refresh_token = environ.get("AH_REFRESH_TOKEN")
    if not refresh_token:
        raise RuntimeError(
            "AH account niet gekoppeld. Voeg AH_REFRESH_TOKEN toe aan je omgeving."
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


def exchange_oauth_code(code: str, redirect_uri: str | None = None) -> dict[str, str]:
    payload: dict[str, str] = {"clientId": AH_CLIENT_ID, "code": code}
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
