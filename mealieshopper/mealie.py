from concurrent.futures import ThreadPoolExecutor
from os import environ
from typing import Any

import requests

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.8",
}


def mealie_url() -> str:
    return environ.get("MEALIE_URL", "").rstrip("/")


def mealie_token() -> str:
    return environ.get("MEALIE_API_TOKEN", "")


def auth_headers() -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {mealie_token()}",
    }


def ensure_configured() -> None:
    if not mealie_url() or not mealie_token():
        raise RuntimeError("Mealie is niet geconfigureerd. Controleer MEALIE_URL en MEALIE_API_TOKEN.")


def import_via_url(url: str) -> str:
    response = requests.post(
        f"{mealie_url()}/api/recipes/create/url",
        headers=auth_headers(),
        json={"url": url, "include_tags": True, "include_categories": True},
        timeout=60,
    )
    if not response.ok:
        raise RuntimeError(str(response.status_code))
    return response.json()


def import_via_html(url: str) -> str:
    page_response = requests.get(url, headers=BROWSER_HEADERS, timeout=60)
    if not page_response.ok:
        raise RuntimeError(f"Kon de pagina niet ophalen ({page_response.status_code})")

    response = requests.post(
        f"{mealie_url()}/api/recipes/create/html-or-json",
        headers=auth_headers(),
        json={
            "data": page_response.text,
            "url": url,
            "include_tags": True,
            "include_categories": True,
        },
        timeout=60,
    )
    if not response.ok:
        raise RuntimeError(f"Mealie import mislukt ({response.status_code}): {response.text}")
    return response.json()


def import_from_url(url: str) -> str:
    ensure_configured()
    try:
        return import_via_url(url)
    except Exception:
        return import_via_html(url)


def recipe_page_url(slug: str) -> str:
    return f"{mealie_url()}/recipe/{slug}"


def get_meal_plan(start_date: str, end_date: str) -> list[dict[str, Any]]:
    ensure_configured()
    response = requests.get(
        f"{mealie_url()}/api/households/mealplans",
        headers=auth_headers(),
        params={"start_date": start_date, "end_date": end_date, "perPage": "50"},
        timeout=30,
    )
    if not response.ok:
        raise RuntimeError(f"Weekmenu ophalen mislukt ({response.status_code}): {response.text}")
    return response.json().get("items", [])


def get_recipe(slug: str) -> dict[str, Any]:
    response = requests.get(
        f"{mealie_url()}/api/recipes/{slug}",
        headers=auth_headers(),
        timeout=30,
    )
    if not response.ok:
        raise RuntimeError(f"Recept ophalen mislukt ({response.status_code})")
    return response.json()


def get_meal_plan_with_recipes(start_date: str, end_date: str) -> list[dict[str, Any]]:
    entries = get_meal_plan(start_date, end_date)
    slugs = sorted(
        {
            entry.get("recipe", {}).get("slug")
            for entry in entries
            if entry.get("recipe", {}).get("slug")
        }
    )

    recipe_map: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=8) as executor:
        future_map = {executor.submit(get_recipe, slug): slug for slug in slugs}
        for future, slug in future_map.items():
            try:
                recipe_map[slug] = future.result()
            except Exception:
                recipe_map[slug] = None

    result = []
    for entry in entries:
        recipe = entry.get("recipe") or {}
        enriched = dict(entry)
        enriched["recipeDetail"] = recipe_map.get(recipe.get("slug"))
        result.append(enriched)
    return result
