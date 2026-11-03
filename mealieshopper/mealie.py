import datetime
import json
import logging
import math
import re
import sqlite3
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from os import environ
from typing import Any
from urllib.parse import urlparse

import requests

from . import ah, auth, ingredients, jumbo, jumbo_recipes
from .i18n import tr

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
        raise RuntimeError(
            tr(
                "Mealie is niet gekoppeld. Koppel Mealie bij Instellingen > Koppelen > Mealie.",
                "Mealie is not linked. Link Mealie under Settings > Link > Mealie.",
            )
        )


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
        _week_start_cache.pop(url, None)  # Koppeling controleren: een andere eerste dag in Mealie meteen overnemen
        try:
            result.update(check_connection(url, token))
            result["connected"] = True
        except RuntimeError as exc:
            result["connected"] = False
            result["error"] = str(exc)
    result["weekStart"] = week_start()
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
        raise MealieImportError(f"Mealie kon de pagina niet importeren ({response.status_code}): {response.text[:200]}", response.status_code)
    return parse_imported_ingredients(response.json())


def import_via_html(url: str) -> str:
    page_response = requests.get(url, headers=BROWSER_HEADERS, timeout=60)
    if not page_response.ok:
        raise RuntimeError(f"Kon de pagina niet ophalen ({page_response.status_code})")
    return _post_html_or_json(url, page_response.text)


class MealieImportError(RuntimeError):
    """Mealie weigerde een import. `status_code` is te tonen; `str(self)` kan het antwoord van Mealie bevatten (alleen in het logboek)."""

    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code


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
        raise MealieImportError(f"Mealie import mislukt ({response.status_code}): {response.text}", response.status_code)
    return parse_imported_ingredients(response.json())


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


def find_recipe_by_source(source_url: Any) -> str | None:
    """De slug van een recept dat al uit deze bron in Mealie staat, of None.

    Mealie bewaart bij elke import de meegegeven link als `org_url` en kan daarop zoeken (`queryFilter` met `LIKE`,
    hoofdletterongevoelig). Allerhande zoeken we op het receptnummer, want de link staat er met of zonder naam in.
    Best effort: lukt het zoeken niet (een oudere Mealie, een vreemde link), dan None en wordt er gewoon geïmporteerd.
    """
    url = str(source_url or "").strip()
    if not url or '"' in url or len(url) > 1000 or not mealie_url() or not mealie_token():
        return None
    recipe_id = ah.allerhande_recipe_id(url)
    query = f'org_url LIKE "%/r-r{recipe_id}/%" OR org_url LIKE "%/r-r{recipe_id}"' if recipe_id else f'org_url LIKE "{url}"'
    try:
        response = requests.get(
            f"{mealie_url()}/api/recipes",
            headers=auth_headers(),
            params={"queryFilter": query, "page": 1, "perPage": 1},
            timeout=15,
        )
        if not response.ok:
            log.info("Mealie kon niet zoeken op bron (%s); er wordt geïmporteerd.", response.status_code)
            return None
        items = (response.json() or {}).get("items") or []
    except (requests.RequestException, ValueError, AttributeError) as exc:
        log.info("Zoeken op bron in Mealie mislukt (%s); er wordt geïmporteerd.", exc)
        return None
    slug = items[0].get("slug") if items and isinstance(items[0], dict) else None
    return str(slug) if slug else None


def _catalog(kind: str) -> list[dict[str, Any]]:
    """Alle eenheden (`units`) of ingrediënten (`foods`) die Mealie kent."""
    items: list[dict[str, Any]] = []
    page, per_page = 1, 500
    while True:
        response = requests.get(
            f"{mealie_url()}/api/{kind}",
            headers=auth_headers(),
            params={"page": page, "perPage": per_page},
            timeout=30,
        )
        if not response.ok:
            raise RuntimeError(f"{kind} ophalen mislukt ({response.status_code})")
        data = response.json()
        batch = data.get("items", []) if isinstance(data, dict) else []
        items.extend(item for item in batch if isinstance(item, dict))
        total_pages = data.get("total_pages") if isinstance(data, dict) else None
        if not batch or len(batch) < per_page or (isinstance(total_pages, int) and page >= total_pages):
            return items
        page += 1


def _catalog_index(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Naam, meervoud, afkorting en alias (kleine letters) naar het item; de eerste vermelding wint."""
    index: dict[str, dict[str, Any]] = {}
    for item in items:
        names = [item.get(key) for key in ("name", "pluralName", "abbreviation", "pluralAbbreviation")]
        names += [alias.get("name") for alias in item.get("aliases") or [] if isinstance(alias, dict)]
        for name in names:
            key = str(name or "").strip().lower()
            if key:
                index.setdefault(key, item)
    return index


def _create_catalog_item(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = requests.post(f"{mealie_url()}/api/{kind}", headers=auth_headers(), json=payload, timeout=30)
    if not response.ok:
        raise RuntimeError(f"{kind} aanmaken mislukt ({response.status_code}): {response.text[:200]}")
    return response.json()


def _link_to_catalog(parsed: dict[str, Any], units: dict[str, Any], foods: dict[str, Any]) -> dict[str, Any]:
    """Zet de eenheid en het ingrediënt van een geanalyseerde regel om in de objecten van Mealie (met id).

    `units` en `foods` zijn de indexen uit `_catalog_index`; wat ontbreekt wordt aangemaakt en erin gezet.
    """
    unit = None
    unit_name = parsed["unit"]["name"] if isinstance(parsed.get("unit"), dict) else ""
    if unit_name:
        unit = next((units[key] for key in ingredients.unit_lookup_names(unit_name) if key in units), None)
        if unit is None:
            unit = _create_catalog_item("units", ingredients.unit_definition(unit_name))
            units.update(_catalog_index([unit]))
    food = None
    food_name = parsed["food"]["name"].strip() if isinstance(parsed.get("food"), dict) else ""
    if food_name:
        food = foods.get(food_name.lower())
        if food is None:
            food = _create_catalog_item("foods", {"name": food_name})
            foods.update(_catalog_index([food]))
    linked = {**parsed, "unit": unit, "food": food, "isFood": bool(food)}
    linked.pop("analyzedByMealieShopper", None)
    return linked


def parse_imported_ingredients(slug: str) -> str:
    """Zet de ingrediëntregels van een net geïmporteerd recept om in aantal, eenheid en ingrediënt.

    Mealie bewaart een geïmporteerde regel als losse tekst ("200 ml melk") in de notitie. Hier halen we aantal,
    eenheid en ingrediënt eruit en koppelen die aan de eenheden en ingrediënten van Mealie (ontbrekende worden
    aangemaakt), zodat Mealie ze zelf kent: het recept schaalt, de boodschappenlijst telt op en de weergave klopt.
    Geeft altijd het slug terug: lukt dit niet, dan blijft het recept zoals Mealie het importeerde.
    """
    try:
        recipe = _fetch_recipe(slug)
        lines = recipe.get("recipeIngredient") if isinstance(recipe, dict) else None
        parsed_lines = [ingredients.normalize(line) for line in lines or []]
        if not any(new is not old for new, old in zip(parsed_lines, lines or [])):
            return slug
        units = _catalog_index(_catalog("units"))
        foods = _catalog_index(_catalog("foods"))
        linked = [
            _link_to_catalog(new, units, foods) if new is not old else old
            for new, old in zip(parsed_lines, lines)
        ]
        response = requests.patch(
            f"{mealie_url()}/api/recipes/{slug}",
            headers=auth_headers(),
            json={"recipeIngredient": linked},
            timeout=60,
        )
        if not response.ok:
            raise RuntimeError(f"Recept bijwerken mislukt ({response.status_code}): {response.text[:200]}")
    except Exception as exc:  # noqa: BLE001 - het recept is al geïmporteerd; analyseren is een extra
        log.warning("Ingrediënten van %s niet geanalyseerd: %s", slug, exc)
    return slug


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


# De eerste dag van de week. Zelfde nummering als Mealie en JavaScript (getDay): 0 = zondag, 1 = maandag ... 6 = zaterdag.
SETTING_WEEK_START = "WEEK_START"  # "" = zoals in Mealie (standaard), "0".."6" = een vaste dag die de beheerder koos
WEEK_START_DEFAULT = 1  # maandag: als Mealie niet gekoppeld is of het niet zegt
WEEK_START_TTL = 600  # seconden dat we de keuze uit Mealie onthouden; Koppeling controleren vraagt het meteen opnieuw
_week_start_cache: dict[str, tuple[float, int | None]] = {}


def _weekday(value: Any) -> int | None:
    """Een dag van 0 (zondag) tot 6 (zaterdag), of None. True/False tellen niet als getal."""
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 6:
        return value
    return None


def first_day_of_week() -> int | None:
    """De eerste dag van de week zoals het huishouden in Mealie die heeft ingesteld (0 = zondag), tien minuten onthouden.

    Mealie (v2 en later) geeft die in `GET /api/households/preferences` als `firstDayOfWeek`; elke gebruiker met een token mag dat
    lezen. Geeft None als Mealie niet gekoppeld of niet te bereiken is, of geen geldige dag geeft; na een minuut proberen we het opnieuw.
    """
    base = mealie_url()
    if not base:
        return None
    now = time.time()
    cached = _week_start_cache.get(base)
    if cached and now < cached[0]:
        return cached[1]
    day: int | None = None
    try:
        response = requests.get(f"{base}/api/households/preferences", headers=auth_headers(), timeout=10)
        if response.ok:
            day = _weekday((response.json() or {}).get("firstDayOfWeek"))
    except Exception:  # noqa: BLE001 - de eerste dag is een extra: een fout mag de status of het koppelen nooit breken
        day = None
    _week_start_cache[base] = (now + (WEEK_START_TTL if day is not None else GROUP_SLUG_RETRY), day)
    return day


def week_start_setting() -> str:
    """De keuze van de beheerder: "" (zoals in Mealie) of "0".."6". Een onleesbare of ongeldige waarde telt als "zoals in Mealie"."""
    try:
        value = auth.get_setting(SETTING_WEEK_START).strip()
    except (sqlite3.Error, OSError):
        return ""
    return value if value in {str(day) for day in range(7)} else ""


def week_start() -> dict[str, Any]:
    """Op welke dag de week begint, voor het weekmenu en de MCP-tool.

    Een vaste dag van de beheerder gaat voor (bron "setting", zonder Mealie te vragen); anders de dag uit Mealie (bron "mealie");
    en zegt Mealie niets, dan maandag (bron "default"). `mealie` is wat Mealie zelf zegt (None als dat niet bekend is).
    """
    setting = week_start_setting()
    from_mealie = first_day_of_week() if not setting else None
    if setting:
        day, source = int(setting), "setting"
    elif from_mealie is not None:
        day, source = from_mealie, "mealie"
    else:
        day, source = WEEK_START_DEFAULT, "default"
    return {"day": day, "source": source, "setting": setting, "mealie": from_mealie}


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


def _fetch_recipe(slug: str) -> dict[str, Any]:
    response = requests.get(
        f"{mealie_url()}/api/recipes/{slug}",
        headers=auth_headers(),
        timeout=30,
    )
    if not response.ok:
        raise RuntimeError(f"Recept ophalen mislukt ({response.status_code})")
    return response.json()


def get_recipe(slug: str) -> dict[str, Any]:
    # Regels die Mealie niet analyseerde ("1" + "200 ml melk") krijgen aantal, eenheid en ingrediënt.
    return ingredients.normalize_recipe(_fetch_recipe(slug))


def _image_path(recipe: dict[str, Any] | None) -> str | None:
    # Late import: mealie_images gebruikt zelf dit module voor de verbinding.
    from . import mealie_images

    return mealie_images.image_path(recipe)


def get_meal_plan_with_recipes(start_date: str, end_date: str) -> list[dict[str, Any]]:
    entries = get_meal_plan(start_date, end_date)
    # Een notitie zonder recept ("Uit eten") heeft "recipe": null, niet een ontbrekend veld.
    slugs = sorted({(entry.get("recipe") or {}).get("slug") for entry in entries if (entry.get("recipe") or {}).get("slug")})

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
        scale = recipe_scale(entry)
        # Een kopie per maaltijd: hetzelfde recept kan twee keer in de week staan, met een andere schaal.
        enriched["recipeDetail"] = ingredients.scale_recipe(detail, scale) if detail else detail
        enriched["servings"] = _servings(detail or recipe, scale) if recipe else None
        enriched["imageUrl"] = _image_path(detail) or _image_path(recipe)
        # Voor het recept in een venster (een tik op de maaltijd): de bereidingstijd en de link naar het recept in Mealie.
        enriched["recipeMinutes"] = _minutes(detail.get("totalTime")) if detail else None
        enriched["recipeUrl"] = recipe_page_url(recipe["slug"]) if recipe.get("slug") else None
        result.append(enriched)
    return result


def recipe_scale(entry: dict[str, Any]) -> float:
    """De schaal van een maaltijd in het weekmenu (`recipeScale`, sinds Mealie v3.29.0); 1 als Mealie die niet kent.

    De planner van Mealie toont de schaal als porties: de porties van het recept maal de schaal. De hoeveelheden in het
    recept zelf blijven staan; Mealie rekent ze pas bij het tonen om, en dat doen wij hier ook.
    """
    value = entry.get("recipeScale") if isinstance(entry, dict) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        return 1.0
    return float(value)


def _servings(recipe: dict[str, Any], scale: float) -> dict[str, Any]:
    """Porties van een maaltijd: `base` uit het recept (None als Mealie het niet weet), `planned` = `base` x `scale`."""
    base = _portions(recipe)
    planned = round(base * scale, 1) if base else None
    if planned is not None and float(planned).is_integer():
        planned = int(planned)
    return {"scale": scale, "base": base, "planned": planned}


# --- Het weekmenu plannen ------------------------------------------------------------------------
# MealieShopper bewaart zelf geen planning: elke wijziging gaat meteen naar Mealie (write-through) en daarna wordt de week
# opnieuw gelezen. Zie docs/onderzoek-weekmenu-plannen.md.

ENTRY_TYPES = ("breakfast", "lunch", "dinner", "side")
EXTRA_ENTRY_TYPES = ("snack", "drink", "dessert")  # Mealie kent deze pas vanaf v3.7.0; een ouder Mealie geeft 422
EXTRA_ENTRY_TYPES_SINCE = (3, 7, 0)
TITLE_MAX = 200
TEXT_MAX = 1000
SEARCH_PAGE = 24
SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,199}")
# Velden die Mealie bij het lezen meegeeft maar bij het wijzigen niet terug wil (of negeert).
_READ_ONLY_ENTRY_FIELDS = ("recipe", "householdId", "updatedAt", "createdAt", "user")

_version_cache: dict[str, tuple[float, str | None]] = {}


def version() -> str | None:
    """De versie van de gekoppelde Mealie ("v3.7.0"), een uur onthouden zoals `group_slug`. None als Mealie het niet zegt."""
    base = mealie_url()
    if not base:
        return None
    now = time.time()
    cached = _version_cache.get(base)
    if cached and now < cached[0]:
        return cached[1]
    value: str | None = None
    try:
        response = requests.get(f"{base}/api/app/about", timeout=10, allow_redirects=False)
        if response.ok:
            value = str((response.json() or {}).get("version") or "").strip() or None
    except (requests.RequestException, ValueError, AttributeError):
        value = None
    _version_cache[base] = (now + (GROUP_SLUG_TTL if value else GROUP_SLUG_RETRY), value)
    return value


def _version_tuple(text: Any) -> tuple[int, ...] | None:
    match = re.match(r"v?(\d+)\.(\d+)(?:\.(\d+))?", str(text or "").strip())
    return tuple(int(part or 0) for part in match.groups()) if match else None


def entry_types() -> list[str]:
    """De maaltijden die de gekoppelde Mealie kent; bij een onbekende versie alleen de vier die elke versie kent."""
    parsed = _version_tuple(version())
    extra = parsed is not None and parsed >= EXTRA_ENTRY_TYPES_SINCE
    return list(ENTRY_TYPES + (EXTRA_ENTRY_TYPES if extra else ()))


def _mealie_error(response: Any, nl: str, en: str) -> RuntimeError:
    """Een leesbare fout uit een weigering van Mealie; het volledige antwoord gaat alleen naar het logboek."""
    detail = ""
    try:
        body = response.json()
        found = body.get("detail") if isinstance(body, dict) else None
        if isinstance(found, dict):
            detail = str(found.get("message") or "")
        elif isinstance(found, list) and found and isinstance(found[0], dict):
            detail = str(found[0].get("msg") or "")  # een 422 van FastAPI: [{loc, msg, type}]
        elif isinstance(found, str):
            detail = found
    except (ValueError, AttributeError):
        detail = ""
    log.warning("%s (%s): %s", en, response.status_code, str(response.text)[:300])
    if response.status_code in (401, 403):
        detail = tr("Mealie weigert de token; koppel Mealie opnieuw", "Mealie refuses the token; link Mealie again")
    detail = " ".join(detail.split())[:200]
    return RuntimeError(f"{tr(nl, en)} ({response.status_code}){': ' + detail if detail else ''}")


def _plan_date(value: Any) -> str:
    try:
        return datetime.date.fromisoformat(str(value or "").strip()).isoformat()
    except ValueError:
        raise ValueError(tr("Kies een geldige datum (JJJJ-MM-DD).", "Choose a valid date (YYYY-MM-DD).")) from None


def _entry_type(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in ENTRY_TYPES:
        return text
    if text in EXTRA_ENTRY_TYPES:
        if text in entry_types():
            return text
        raise ValueError(
            tr(
                "Deze maaltijd kent je Mealie nog niet; die komt met Mealie 3.7. Kies ontbijt, lunch, diner of bijgerecht.",
                "Your Mealie does not know this meal yet; it arrived in Mealie 3.7. Choose breakfast, lunch, dinner or side.",
            )
        )
    raise ValueError(tr("Onbekende maaltijd. Kies bijvoorbeeld diner.", "Unknown meal. Choose dinner, for example."))


def _entry_id(value: Any) -> int:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        number = 0
    if number <= 0:
        raise ValueError(tr("Ongeldige maaltijd in het weekmenu.", "Invalid meal in the week menu."))
    return number


def valid_slug(value: Any) -> str:
    text = str(value or "").strip()
    if not SLUG.fullmatch(text):
        raise ValueError(tr("Ongeldig recept.", "Invalid recipe."))
    return text


def _recipe_uuid(value: Any) -> str:
    try:
        return str(uuid.UUID(str(value).strip()))
    except (TypeError, ValueError, AttributeError):
        raise ValueError(tr("Ongeldig recept.", "Invalid recipe.")) from None


def _clean_entry(values: dict[str, Any], partial: bool = False) -> dict[str, Any]:
    """Controleer de velden van een maaltijd en geef ze zoals Mealie ze wil.

    `values` kent `date`, `entryType`, `recipeId`, `recipeSlug` (wordt een `recipeId`), `title` en `text`. Bij `partial`
    (wijzigen) alleen wat is meegegeven; een lege `recipeId` haalt het recept weg (de maaltijd wordt een notitie).
    """
    if not isinstance(values, dict):
        raise ValueError(tr("Ongeldige invoer.", "Invalid input."))
    fields: dict[str, Any] = {}
    if not partial or "date" in values:
        fields["date"] = _plan_date(values.get("date"))
    if not partial or "entryType" in values:
        fields["entryType"] = _entry_type(values.get("entryType") or ("" if partial else "dinner"))
    if not partial or "title" in values:
        fields["title"] = " ".join(str(values.get("title") or "").split())[:TITLE_MAX]
    if not partial or "text" in values:
        fields["text"] = str(values.get("text") or "").strip()[:TEXT_MAX]
    if values.get("recipeId"):
        fields["recipeId"] = _recipe_uuid(values["recipeId"])
    elif values.get("recipeSlug"):
        fields["recipeId"] = _recipe_uuid(_fetch_recipe(valid_slug(values["recipeSlug"])).get("id"))
    elif "recipeId" in values or not partial:
        fields["recipeId"] = None
    return fields


def _require_recipe_or_title(fields: dict[str, Any]) -> None:
    if not fields.get("recipeId") and not fields.get("title"):
        raise ValueError(tr("Kies een recept of schrijf een notitie.", "Choose a recipe or write a note."))


def create_meal_plan_entry(values: dict[str, Any]) -> dict[str, Any]:
    """Zet een recept of een notitie op een dag in het weekmenu van Mealie."""
    ensure_configured()
    fields = _clean_entry(values)
    _require_recipe_or_title(fields)
    response = requests.post(
        f"{mealie_url()}/api/households/mealplans", headers=auth_headers(), json=fields, timeout=30
    )
    if not response.ok:
        raise _mealie_error(response, "Mealie kon de maaltijd niet plannen", "Mealie could not plan the meal")
    return response.json()


def _get_entry(entry_id: int) -> dict[str, Any]:
    response = requests.get(
        f"{mealie_url()}/api/households/mealplans/{entry_id}", headers=auth_headers(), timeout=30
    )
    if not response.ok:
        raise _mealie_error(response, "Maaltijd ophalen uit Mealie mislukt", "Could not fetch the meal from Mealie")
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError(tr("Mealie gaf een onverwacht antwoord", "Mealie gave an unexpected answer"))
    return data


def _same_recipe(new: Any, old: Any) -> bool:
    return str(new or "").strip().lower() == str(old or "").strip().lower()


def update_meal_plan_entry(entry_id: Any, values: dict[str, Any]) -> dict[str, Any]:
    """Verplaats of wijzig een maaltijd. Mealie wil bij wijzigen het hele item, dus eerst ophalen en dan samenvoegen."""
    ensure_configured()
    number = _entry_id(entry_id)
    changes = _clean_entry(values, partial=True)
    current = _get_entry(number)
    # `recipeScale` (Mealie v3.29+) gaat zo mee terug; zonder dat veld zet Mealie de porties weer op 1.
    merged = {key: value for key, value in current.items() if key not in _READ_ONLY_ENTRY_FIELDS}
    merged.update(changes)
    merged["id"] = number
    if "recipeScale" in current and "recipeId" in changes and not _same_recipe(changes["recipeId"], current.get("recipeId")):
        merged["recipeScale"] = 1.0  # een ander recept (of een notitie): de porties van het oude gelden niet, net als in Mealie
    _require_recipe_or_title(merged)
    response = requests.put(
        f"{mealie_url()}/api/households/mealplans/{number}", headers=auth_headers(), json=merged, timeout=30
    )
    if not response.ok:
        raise _mealie_error(response, "Mealie kon de maaltijd niet wijzigen", "Mealie could not change the meal")
    return response.json()


def delete_meal_plan_entry(entry_id: Any) -> dict[str, Any]:
    """Haal een maaltijd uit het weekmenu. Het recept zelf blijft in Mealie staan."""
    ensure_configured()
    number = _entry_id(entry_id)
    response = requests.delete(
        f"{mealie_url()}/api/households/mealplans/{number}", headers=auth_headers(), timeout=30
    )
    if not response.ok:
        raise _mealie_error(response, "Mealie kon de maaltijd niet verwijderen", "Mealie could not remove the meal")
    return {"id": number, "deleted": True}


def random_meal_plan_entry(values: dict[str, Any]) -> dict[str, Any]:
    """Laat Mealie een recept kiezen volgens de planregels van het huishouden en zet het meteen in het weekmenu."""
    ensure_configured()
    if not isinstance(values, dict):
        raise ValueError(tr("Ongeldige invoer.", "Invalid input."))
    payload = {"date": _plan_date(values.get("date")), "entryType": _entry_type(values.get("entryType") or "dinner")}
    response = requests.post(
        f"{mealie_url()}/api/households/mealplans/random", headers=auth_headers(), json=payload, timeout=30
    )
    if response.status_code == 404:
        raise RuntimeError(
            tr(
                "Mealie vond geen recept dat bij je planregels past. Kies zelf een recept, of pas de regels aan in Mealie.",
                "Mealie found no recipe that matches your meal plan rules. Choose a recipe yourself, or change the rules in Mealie.",
            )
        )
    if not response.ok:
        raise _mealie_error(response, "Mealie kon geen recept kiezen", "Mealie could not pick a recipe")
    return response.json()


def _minutes(value: Any) -> int | None:
    """Minuten uit de bereidingstijd van Mealie, die vrije tekst is ("1 hour 30 minutes", "45 min", "PT30M")."""
    text = str(value or "").strip().lower()
    if not text:
        return None
    iso = re.fullmatch(r"pt?(?:(\d+)h)?(?:(\d+)m)?(?:\d+s)?", text)
    if iso and (iso.group(1) or iso.group(2)):
        return int(iso.group(1) or 0) * 60 + int(iso.group(2) or 0) or None
    hours = re.search(r"(\d+)\s*(?:h|hr|hrs|hour|hours|uur|u)\b", text)
    minutes = re.search(r"(\d+)\s*(?:m|min|mins|minute|minutes|minuut|minuten)\b", text)
    if hours or minutes:
        return int(hours.group(1) if hours else 0) * 60 + int(minutes.group(1) if minutes else 0) or None
    return (int(text) or None) if text.isdigit() else None


def _portions(recipe: dict[str, Any]) -> int | None:
    for key in ("recipeServings", "recipeYieldQuantity"):
        value = recipe.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return int(value) if float(value).is_integer() else round(value, 1)
    match = re.match(r"\s*(\d+)", str(recipe.get("recipeYield") or ""))
    return (int(match.group(1)) or None) if match else None


def recipe_card(recipe: dict[str, Any]) -> dict[str, Any]:
    """Een Mealie-recept in de vorm van de receptkaarten van de winkels (Recepten zoeken en de receptkiezer)."""
    slug = str(recipe.get("slug") or "")
    tags = [str(tag["name"]) for tag in recipe.get("tags") or [] if isinstance(tag, dict) and tag.get("name")]
    rating = recipe.get("rating")
    return {
        "id": slug,
        "slug": slug,
        "recipeId": recipe.get("id"),
        "name": str(recipe.get("name") or slug),
        "image": _image_path(recipe),
        "minutes": _minutes(recipe.get("totalTime")),
        "portions": _portions(recipe),
        "rating": rating if isinstance(rating, (int, float)) and rating > 0 else None,
        "tags": tags[:3],
    }


def search_recipes(query: Any, page: Any = 1, per_page: int = SEARCH_PAGE) -> dict[str, Any]:
    """Zoek in de recepten van Mealie. Zonder zoekterm alle recepten op naam."""
    ensure_configured()
    try:
        number = max(1, int(page or 1))
    except (TypeError, ValueError):
        number = 1
    size = max(1, min(100, int(per_page)))
    text = " ".join(str(query or "").split())[:200]
    params: dict[str, Any] = {"page": number, "perPage": size}
    if text:
        params["search"] = text  # Mealie sorteert dan zelf op relevantie
    else:
        params.update({"orderBy": "name", "orderDirection": "asc"})
    response = requests.get(f"{mealie_url()}/api/recipes", headers=auth_headers(), params=params, timeout=30)
    if not response.ok:
        raise _mealie_error(response, "Zoeken in Mealie mislukt", "Searching Mealie failed")
    data = response.json()
    items = [item for item in (data.get("items") if isinstance(data, dict) else None) or [] if isinstance(item, dict) and item.get("slug")]
    total = data.get("total") if isinstance(data, dict) and isinstance(data.get("total"), int) else len(items)
    total_pages = data.get("total_pages") if isinstance(data, dict) else None
    more = number < total_pages if isinstance(total_pages, int) else len(items) >= size
    return {"recipes": [recipe_card(item) for item in items], "total": total, "page": number, "more": bool(more)}


def recipe_detail(slug: Any) -> dict[str, Any]:
    """Een Mealie-recept met ingrediënten en bereiding, in de vorm van het detailscherm bij Recepten zoeken."""
    ensure_configured()
    clean = valid_slug(slug)
    recipe = get_recipe(clean)
    ingredients = []
    for item in recipe.get("recipeIngredient") or []:
        if isinstance(item, dict):
            line = " ".join(str(item.get("display") or item.get("note") or "").split())
            if line:
                ingredients.append({"line": line})
    steps = [
        str(step.get("text")).strip()
        for step in recipe.get("recipeInstructions") or []
        if isinstance(step, dict) and str(step.get("text") or "").strip()
    ]
    return {
        **recipe_card(recipe),
        "description": str(recipe.get("description") or "").strip(),
        "ingredients": ingredients,
        "steps": steps,
        "url": recipe_page_url(clean),
    }


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
