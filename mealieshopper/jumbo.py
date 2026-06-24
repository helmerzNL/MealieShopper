import time
from dataclasses import dataclass
from os import environ
from typing import Any

import requests

from . import auth

JUMBO_API_BASE = "https://mobileapi.jumbo.com/v17"
JUMBO_USER_AGENT = "Jumbo/7.5.2 (MealieShopper)"
JUMBO_HEADERS = {
    "User-Agent": JUMBO_USER_AGENT,
    "Accept": "application/json",
}

TOKEN_TTL_SECONDS = 50 * 60


@dataclass
class TokenCache:
    token: str
    expires_at: float


token_cache: TokenCache | None = None


def _headers(token: str | None = None, *, content_type: str | None = None) -> dict[str, str]:
    headers = dict(JUMBO_HEADERS)
    if content_type:
        headers["Content-Type"] = content_type
    if token:
        headers["x-jumbo-token"] = token
    return headers


def login(username: str, password: str) -> str:
    response = requests.post(
        f"{JUMBO_API_BASE}/users/login",
        headers=_headers(content_type="application/x-www-form-urlencoded"),
        data={"username": username, "password": password},
        timeout=30,
    )
    if not response.ok:
        raise RuntimeError(
            f"Jumbo inloggen mislukt ({response.status_code}): {response.text[:200]}"
        )

    token = (response.headers.get("x-jumbo-token") or "").strip()
    if not token:
        raise RuntimeError(
            "Jumbo inloggen mislukt: geen token ontvangen (controleer e-mail en wachtwoord)"
        )
    return token


def configured_credentials() -> tuple[str, str]:
    username = (environ.get("JUMBO_USERNAME") or "").strip() or auth.get_secret("JUMBO_USERNAME")
    password = (environ.get("JUMBO_PASSWORD") or "").strip() or auth.get_secret("JUMBO_PASSWORD")
    return username.strip(), password


def save_credentials(username: str, password: str) -> None:
    username = str(username or "").strip()
    password = str(password or "")
    if not username or not password:
        raise RuntimeError("E-mail en wachtwoord zijn verplicht")
    auth.set_secret("JUMBO_USERNAME", username)
    auth.set_secret("JUMBO_PASSWORD", password)


def clear_credentials() -> None:
    global token_cache
    token_cache = None
    auth.set_secret("JUMBO_USERNAME", "")
    auth.set_secret("JUMBO_PASSWORD", "")


def get_token(force: bool = False) -> str:
    global token_cache
    if not force and token_cache and time.time() < token_cache.expires_at:
        return token_cache.token

    username, password = configured_credentials()
    if not username or not password:
        raise RuntimeError(
            "Jumbo account niet gekoppeld. Log in via de tab 'Jumbo koppelen'."
        )

    token = login(username, password)
    token_cache = TokenCache(token=token, expires_at=time.time() + TOKEN_TTL_SECONDS)
    return token


def link_account(username: str, password: str) -> dict[str, Any]:
    try:
        token = login(username, password)
    except Exception as exc:
        return {"connected": False, "error": str(exc)}

    save_credentials(username, password)
    global token_cache
    token_cache = TokenCache(token=token, expires_at=time.time() + TOKEN_TTL_SECONDS)
    return {"connected": True}


def verify_credentials() -> dict[str, Any]:
    username, password = configured_credentials()
    if not username or not password:
        return {"ok": False, "error": "Geen Jumbo inloggegevens opgeslagen"}
    try:
        login(username, password)
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def auth_status(verify: bool = False) -> dict[str, Any]:
    username, password = configured_credentials()
    if not username or not password:
        return {"connected": False}
    if not verify:
        return {"connected": True, "verified": False, "username": username}
    result = verify_credentials()
    return {
        "connected": bool(result.get("ok")),
        "verified": True,
        "username": username,
        "error": result.get("error"),
    }


def _public_request(method: str, path: str, **kwargs: Any) -> requests.Response:
    url = f"{JUMBO_API_BASE}/{path.lstrip('/')}"
    return requests.request(method, url, headers=_headers(), timeout=30, **kwargs)


def _authed_request(
    method: str,
    path: str,
    *,
    json: Any = None,
    params: dict[str, Any] | None = None,
    content_type: str | None = None,
    _retry: bool = True,
) -> requests.Response:
    token = get_token()
    url = f"{JUMBO_API_BASE}/{path.lstrip('/')}"
    response = requests.request(
        method,
        url,
        headers=_headers(token, content_type=content_type),
        json=json,
        params=params,
        timeout=30,
    )
    if response.status_code in (401, 403) and _retry:
        get_token(force=True)
        return _authed_request(
            method, path, json=json, params=params, content_type=content_type, _retry=False
        )
    return response


def _normalize_product(item: dict[str, Any], query: str = "") -> dict[str, Any]:
    prices = item.get("prices") or {}
    price = prices.get("price") or {}
    promo = prices.get("promotionalPrice") or {}

    quantity_options = item.get("quantityOptions") or []
    unit = (quantity_options[0].get("unit") if quantity_options else None) or "pieces"

    image = ""
    primary = (item.get("imageInfo") or {}).get("primaryView") or []
    if primary:
        image = primary[0].get("url", "") or ""

    amount_cents = promo.get("amount") or price.get("amount") or 0
    try:
        now = round(int(amount_cents) / 100, 2)
    except (TypeError, ValueError):
        now = 0

    return {
        "sku": str(item.get("id") or ""),
        "title": item.get("title") or item.get("regulatedTitle") or query,
        "unit": unit,
        "unitSize": item.get("quantity"),
        "price": {"now": now, "amount": amount_cents, "currency": price.get("currency") or "EUR"},
        "image": image,
        "available": item.get("available", True),
    }


def search_product(query: str) -> dict[str, Any] | None:
    response = _public_request("GET", "search", params={"q": query, "limit": 1, "offset": 0})
    if not response.ok:
        raise RuntimeError(f"Jumbo productzoekopdracht mislukt ({response.status_code})")

    data = response.json()
    products = ((data or {}).get("products") or {}).get("data") or []
    if not products:
        return None
    return _normalize_product(products[0], query)


def get_basket() -> dict[str, Any]:
    response = _authed_request("GET", "basket")
    if not response.ok:
        raise RuntimeError(
            f"Jumbo mandje ophalen mislukt ({response.status_code}): {response.text[:200]}"
        )
    data = response.json() or {}
    return data.get("basket") or data.get("data") or data


def add_to_basket(new_items: list[dict[str, Any]]) -> None:
    basket = get_basket()

    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for product in basket.get("products") or []:
        sku = str(product.get("sku") or "")
        if not sku:
            continue
        try:
            quantity = int(product.get("quantity") or 0)
        except (TypeError, ValueError):
            quantity = 0
        merged[sku] = {
            "sku": sku,
            "unit": product.get("unit") or "pieces",
            "quantity": quantity,
        }
        order.append(sku)

    for item in new_items:
        sku = str(item.get("sku") or "")
        if not sku:
            continue
        try:
            quantity = max(1, int(item.get("quantity") or 1))
        except (TypeError, ValueError):
            quantity = 1
        if sku in merged:
            merged[sku]["quantity"] += quantity
        else:
            merged[sku] = {
                "sku": sku,
                "unit": item.get("unit") or "pieces",
                "quantity": quantity,
            }
            order.append(sku)

    payload = {"items": [merged[sku] for sku in order], "vagueTerms": []}
    response = _authed_request("PUT", "basket", json=payload, content_type="application/json")
    if not response.ok:
        raise RuntimeError(
            f"Jumbo mandje vullen mislukt ({response.status_code}): {response.text[:200]}"
        )


def _normalize_recipe(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None

    recipe = item
    if isinstance(item.get("recipe"), dict):
        recipe = item["recipe"]
        if isinstance(recipe.get("data"), dict):
            recipe = recipe["data"]
    elif isinstance(item.get("data"), dict):
        recipe = item["data"]

    image = ""
    image_info = recipe.get("imageInfo") or {}
    primary = image_info.get("primaryView") or []
    if primary and isinstance(primary[0], dict):
        image = primary[0].get("url") or ""
    elif isinstance(recipe.get("image"), str):
        image = recipe.get("image") or ""

    url = (recipe.get("webUrl") or recipe.get("url") or recipe.get("link") or "").strip()
    recipe_id = recipe.get("id") or recipe.get("recipeId")
    if not url and recipe_id:
        url = f"https://www.jumbo.com/recepten/{recipe_id}"
    if not url:
        return None

    return {
        "id": recipe_id,
        "title": recipe.get("name") or recipe.get("title") or "Onbekend recept",
        "image": image,
        "url": url,
    }


def _extract_recipe_items(data: Any) -> list[Any]:
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for key in ("items", "recipes", "data"):
        value = data.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = value.get("data") or value.get("items")
            if isinstance(nested, list):
                return nested
    return []


def _find_favorite_recipe_list_id() -> str | None:
    response = _authed_request("GET", "lists/mylists")
    if not response.ok:
        return None
    data = response.json() or {}
    lists = data.get("lists") or data.get("data") or data.get("myLists") or []
    if isinstance(lists, dict):
        lists = lists.get("data") or []
    for entry in lists:
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("type") or entry.get("listType") or "").lower()
        title = str(entry.get("title") or entry.get("name") or "").lower()
        if "recipe" in kind or "recept" in title or "favoriet" in title:
            return str(entry.get("id") or entry.get("listId") or "") or None
    return None


def get_saved_recipes() -> dict[str, Any]:
    response = _authed_request("GET", "recipe-lists/favorites/items")
    if response.status_code == 404:
        list_id = _find_favorite_recipe_list_id()
        if list_id:
            response = _authed_request("GET", f"recipe-lists/{list_id}/items")
    if not response.ok:
        raise RuntimeError(
            f"Jumbo bewaarde recepten ophalen mislukt ({response.status_code}): {response.text[:200]}"
        )

    raw = _extract_recipe_items(response.json())
    recipes = [normalized for item in raw if (normalized := _normalize_recipe(item))]
    return {"recipes": recipes, "total": len(recipes)}
