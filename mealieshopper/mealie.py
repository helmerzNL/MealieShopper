import json
import logging
import re
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from os import environ
from typing import Any
from urllib.parse import urlparse

import requests

from . import ah, auth, jumbo, jumbo_recipes

log = logging.getLogger(__name__)

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.8",
}


SECRET_URL = "MEALIE_URL"
SECRET_TOKEN = "MEALIE_API_TOKEN"


def normalize_url(value: Any) -> str:
    """Maak van wat de gebruiker intypt een bruikbaar basisadres (zonder slash en zonder /api)."""
    text = str(value or "").strip()
    if not text:
        raise ValueError("Vul het adres van je Mealie in.")
    if "://" not in text:
        text = "https://" + text
    parsed = urlparse(text)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("Dit is geen geldig adres. Gebruik bijvoorbeeld https://mealie.jouwdomein.nl")
    if parsed.username or parsed.password:
        raise ValueError("Zet geen gebruikersnaam of wachtwoord in het adres; gebruik een API-token.")
    path = parsed.path.rstrip("/")
    if path.endswith("/api"):
        path = path[: -len("/api")]
    return f"{parsed.scheme}://{parsed.netloc}{path}"


_db_warned = False


def _ui_secret(key: str) -> str:
    """Lees een in de UI opgeslagen waarde; een onleesbare database laat de omgevingsvariabelen intact."""
    global _db_warned
    try:
        return auth.get_secret(key).strip()
    except (sqlite3.Error, OSError) as exc:
        if not _db_warned:
            _db_warned = True
            log.warning("Mealie-koppeling niet uit de database te lezen (%s); omgevingsvariabelen worden gebruikt.", exc)
        return ""


def _config() -> tuple[str, str, str | None]:
    """(adres, token, bron). Adres en token komen altijd uit dezelfde bron, nooit gemengd."""
    ui_url, ui_token = _ui_secret(SECRET_URL), _ui_secret(SECRET_TOKEN)
    if ui_url and ui_token:
        return ui_url.rstrip("/"), ui_token, "ui"
    env_url, env_token = environ.get("MEALIE_URL", "").strip(), environ.get("MEALIE_API_TOKEN", "").strip()
    if env_url and env_token:
        return env_url.rstrip("/"), env_token, "env"
    return "", "", None


def mealie_url() -> str:
    return _config()[0]


def mealie_token() -> str:
    return _config()[1]


def auth_headers() -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {mealie_token()}",
    }


def ensure_configured() -> None:
    if not mealie_url() or not mealie_token():
        raise RuntimeError("Mealie is niet gekoppeld. Koppel Mealie bij Instellingen > Koppelen > Mealie.")


def check_connection(url: str, token: str) -> dict[str, Any]:
    """Controleer adres en token bij Mealie zelf; geeft gebruiker en versie, of een duidelijke fout."""
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    try:
        response = requests.get(
            f"{url}/api/users/self", headers=headers, timeout=15, allow_redirects=False
        )
    except requests.exceptions.SSLError:
        raise RuntimeError(
            "Het certificaat van Mealie wordt niet vertrouwd. Controleer het adres of het certificaat."
        ) from None
    except requests.exceptions.Timeout:
        raise RuntimeError("Mealie reageert niet (time-out). Controleer het adres en je verbinding.") from None
    except requests.exceptions.ConnectionError:
        raise RuntimeError(
            f"Mealie is niet bereikbaar op {url}. Controleer het adres en of deze server erbij kan."
        ) from None
    except requests.RequestException as exc:
        raise RuntimeError(f"Verbinding met Mealie mislukt: {exc}") from None

    status = response.status_code
    if 300 <= status < 400:
        location = response.headers.get("Location", "")
        raise RuntimeError(
            f"Mealie stuurt door naar {location}. Gebruik dat adres." if location
            else "Mealie stuurt door naar een ander adres. Gebruik het adres waar je Mealie in de browser opent."
        )
    if status in (401, 403):
        raise RuntimeError(
            f"Mealie weigert de token ({status}). Maak een nieuwe API-token en controleer of hij niet is verlopen."
        )
    if status == 404:
        raise RuntimeError("Op dit adres is geen Mealie-API gevonden (404). Controleer het adres.")
    if not response.ok:
        raise RuntimeError(f"Mealie gaf een fout ({status}): {response.text[:200]}")
    try:
        data = response.json()
    except ValueError:
        raise RuntimeError(
            "Dit adres geeft geen Mealie-antwoord (geen JSON). Controleer het adres en je reverse proxy."
        ) from None
    if not isinstance(data, dict) or "username" not in data:
        raise RuntimeError("Dit adres lijkt geen Mealie te zijn: het antwoord mist een gebruiker.")

    version = None
    try:
        about = requests.get(f"{url}/api/app/about", timeout=10, allow_redirects=False)
        if about.ok:
            version = (about.json() or {}).get("version")
    except (requests.RequestException, ValueError):
        pass  # de versie is een extraatje
    return {"username": data.get("username"), "version": version}


def status(verify: bool = False) -> dict[str, Any]:
    """Toestand van de koppeling; bevat nooit de token."""
    url, token, source = _config()
    result: dict[str, Any] = {
        "configured": bool(url and token),
        "url": url,
        "source": source,
    }
    if verify and result["configured"]:
        result["verified"] = True
        try:
            result.update(check_connection(url, token))
            result["connected"] = True
        except RuntimeError as exc:
            result["connected"] = False
            result["error"] = str(exc)
    return result


def link(url: Any, token: Any) -> dict[str, Any]:
    """Controleer en bewaar (versleuteld) het adres en de token. Mislukt de controle, dan wordt niets bewaard."""
    clean_url = normalize_url(url)
    clean_token = str(token or "").strip()
    if not clean_token:
        raise ValueError("Plak de API-token van Mealie.")
    if re.search(r"\s", clean_token):
        raise ValueError("De token bevat spaties of regeleinden. Kopieer hem opnieuw uit Mealie.")
    checked = check_connection(clean_url, clean_token)
    auth.set_secret(SECRET_URL, clean_url)
    auth.set_secret(SECRET_TOKEN, clean_token)
    return {**status(), **checked, "verified": True, "connected": True}


def clear() -> dict[str, Any]:
    """Verwijder de koppeling uit de UI; daarna geldt weer een eventuele omgevingsconfiguratie."""
    auth.delete_secret(SECRET_URL)
    auth.delete_secret(SECRET_TOKEN)
    return status()


def import_via_url(url: str) -> str:
    response = requests.post(
        f"{mealie_url()}/api/recipes/create/url",
        headers=auth_headers(),
        json={"url": url, "include_tags": True, "include_categories": True},
        timeout=60,
    )
    if not response.ok:
        raise RuntimeError(f"Mealie kon de pagina niet importeren ({response.status_code}): {response.text[:200]}")
    return response.json()


def import_via_html(url: str) -> str:
    page_response = requests.get(url, headers=BROWSER_HEADERS, timeout=60)
    if not page_response.ok:
        raise RuntimeError(f"Kon de pagina niet ophalen ({page_response.status_code})")
    return _post_html_or_json(url, page_response.text)


def import_via_jsonld(url: str, recipe: dict[str, Any]) -> str:
    """Importeer een schema.org Recipe (JSON-LD); Mealie hoeft de pagina dan niet op te halen."""
    return _post_html_or_json(url, json.dumps(recipe))


def _post_html_or_json(url: str, data: str) -> str:
    response = requests.post(
        f"{mealie_url()}/api/recipes/create/html-or-json",
        headers=auth_headers(),
        json={
            "data": data,
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
    errors: list[str] = []

    # Allerhande-recepten komen via de AH-API: de websitepagina van AH blokkeert servers (403).
    recipe_id = ah.allerhande_recipe_id(url)
    if recipe_id:
        try:
            return import_via_jsonld(url, ah.get_recipe_jsonld(recipe_id))
        except Exception as exc:  # noqa: BLE001 - val terug op de pagina-import
            errors.append(f"AH-API: {exc}")

    # Jumbo-recepten ook via de API: jumbo.com weert servers, en zo komen ingrediënten en bereiding zeker mee.
    jumbo_id = jumbo_recipes.recipe_id_from_url(url)
    if jumbo_id and jumbo.configured_refresh_token():
        try:
            recipe = jumbo_recipes.get_recipe(jumbo_id)
            return import_via_jsonld(jumbo_recipes.source_url(recipe), jumbo_recipes.jsonld(recipe))
        except Exception as exc:  # noqa: BLE001 - val terug op de pagina-import
            errors.append(f"Jumbo-API: {exc}")

    try:
        return import_via_url(url)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"Mealie: {exc}")
    try:
        return import_via_html(url)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"pagina: {exc}")
    raise RuntimeError("; ".join(errors))


GROUP_SLUG_TTL = 3600  # seconden dat we de groep onthouden
GROUP_SLUG_RETRY = 60  # na een mislukte poging proberen we het pas na zoveel seconden opnieuw
_group_slug_cache: dict[str, tuple[float, str | None]] = {}


def group_slug() -> str | None:
    """De groep (slug) van de gekoppelde gebruiker, zoals Mealie die in het adres van een recept zet (/g/<groep>/r/<recept>).

    Wordt een uur onthouden. Geeft None als Mealie het niet zegt (een oudere versie) of niet te bereiken is; dan gebruiken we het
    oude adres. Een mislukte poging blokkeert niets en wordt na een minuut opnieuw geprobeerd.
    """
    base = mealie_url()
    if not base:
        return None
    now = time.time()
    cached = _group_slug_cache.get(base)
    if cached and now < cached[0]:
        return cached[1]
    slug: str | None = None
    try:
        response = requests.get(f"{base}/api/users/self", headers=auth_headers(), timeout=10)
        if response.ok:
            value = (response.json() or {}).get("groupSlug")
            slug = str(value).strip() or None if value else None
    except (requests.RequestException, ValueError):
        slug = None
    _group_slug_cache[base] = (now + (GROUP_SLUG_TTL if slug else GROUP_SLUG_RETRY), slug)
    return slug


def recipe_page_url(slug: str) -> str:
    """Het adres van een recept in Mealie: /g/<groep>/r/<recept>, of het oude /recipe/<recept> als de groep onbekend is."""
    base = mealie_url()
    group = group_slug()
    return f"{base}/g/{group}/r/{slug}" if group else f"{base}/recipe/{slug}"


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


def _image_path(recipe: dict[str, Any] | None) -> str | None:
    # Late import: mealie_images gebruikt zelf dit module voor de verbinding.
    from . import mealie_images

    return mealie_images.image_path(recipe)


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
        detail = recipe_map.get(recipe.get("slug"))
        enriched["recipeDetail"] = detail
        enriched["imageUrl"] = _image_path(detail) or _image_path(recipe)
        result.append(enriched)
    return result


def list_recipes(limit: int = 500) -> list[dict[str, Any]]:
    """Alle recepten in Mealie (naam, slug, afbeelding), voor het zoeken naar receptvoorstellen."""
    ensure_configured()
    items: list[dict[str, Any]] = []
    page = 1
    per_page = 100
    while len(items) < limit:
        response = requests.get(
            f"{mealie_url()}/api/recipes",
            headers=auth_headers(),
            params={"page": page, "perPage": per_page, "orderBy": "name", "orderDirection": "asc"},
            timeout=30,
        )
        if not response.ok:
            raise RuntimeError(f"Recepten ophalen mislukt ({response.status_code})")
        data = response.json()
        batch = data.get("items", []) if isinstance(data, dict) else []
        items.extend(item for item in batch if isinstance(item, dict) and item.get("slug"))
        total_pages = data.get("total_pages") if isinstance(data, dict) else None
        if not batch or (isinstance(total_pages, int) and page >= total_pages) or len(batch) < per_page:
            break
        page += 1
    return items[:limit]
