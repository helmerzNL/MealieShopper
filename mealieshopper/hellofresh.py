"""Maaltijdboxen: de menu's en recepten van HelloFresh Nederland, zonder account.

HelloFresh heeft geen publieke API. We lezen daarom alleen de pagina's die ook zonder inloggen openstaan (een
weekmenu en een receptpagina) zoals een zoekmachine dat doet, en zetten het recept uit de schema.org-gegevens van
de pagina om voor Mealie. Zie docs/onderzoek-hellofresh.md.

- Een weekmenu staat in het `__NEXT_DATA__`-blok van de pagina; dat is een intern formaat dat kan veranderen. Het
  recept zelf komt uit het JSON-LD-blok (`Recipe`), dat zoekmachines gebruiken en dus stabieler is.
- Het menu van een week bewaren we in SQLite met een tijdstip. Lezen en zoeken gaat uit die kopie; HelloFresh wordt
  alleen gevraagd als de kopie ontbreekt, verouderd is of de gebruiker ververst. Receptpagina's bewaren we niet.
- We vragen zuinig (één verzoek tegelijk, met een pauze ertussen), noemen ons in de User-Agent en komen alleen op de
  paden die `robots.txt` niet uitsluit: `/menus/<week>` en `/recipes/<recept>`.
"""
from __future__ import annotations

import html
import json
import re
import threading
import time
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

import requests

from . import about, auth, mealie, mealkit_import
from .ah_lists import _fold
from .i18n import tr

BASE = "https://www.hellofresh.nl"
IMAGE_BASE = "https://img.hellofresh.com/f_auto,fl_lossy,h_300,q_auto,w_450/hellofresh_s3"
HOST = "hellofresh.nl"
TIMEOUT = 20
MIN_GAP = 1.0  # seconden tussen twee verzoeken aan HelloFresh
MENU_TTL = timedelta(hours=6)
MAX_RESULTS = 60

_WEEK = re.compile(r"^(\d{4})-W(\d{2})$")
_KEY = re.compile(r"^[a-z0-9-]{1,200}-[0-9a-f]{24}$")
_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
_JSON_LD = re.compile(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
_DURATION = re.compile(r"^PT(?:(\d+)H)?(?:(\d+)M)?$")
_TAGS = re.compile(r"<[^>]+>")

_IMAGE = re.compile(r"^(https://img\.hellofresh\.com)/[^/]+/(hellofresh_s3/image/[^/?#]+)$")
NUTRITION_FIELDS = (
    "calories", "fatContent", "saturatedFatContent", "carbohydrateContent", "sugarContent", "proteinContent",
    "fiberContent", "sodiumContent",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS hellofresh_menus (
  week TEXT PRIMARY KEY,
  fetched_at TEXT NOT NULL,
  payload TEXT NOT NULL
);
"""

class HelloFreshError(RuntimeError):
    """Een fout met een tekst van onszelf (nooit het antwoord van HelloFresh), die de gebruiker mag zien."""


_gap_lock = threading.Lock()
_last_request = 0.0


def _connect():
    conn = auth.connect()
    conn.executescript(SCHEMA)
    return conn


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- pagina's ophalen -----------------------------------------------------------------------------


def _user_agent() -> str:
    return f"MealieShopper/{about.read_version()} (+https://github.com/helmerzNL/MealieShopper)"


def _get(path: str) -> str:
    """De tekst van een pagina; `path` komt van onszelf (een gecontroleerde week of receptsleutel)."""
    global _last_request
    with _gap_lock:  # één verzoek tegelijk, met een pauze ertussen
        wait = MIN_GAP - (time.monotonic() - _last_request)
        if wait > 0:
            time.sleep(wait)
        try:
            response = requests.get(BASE + path, headers={"User-Agent": _user_agent(), "Accept-Language": "nl-NL,nl;q=0.9"}, timeout=TIMEOUT)
        except requests.RequestException as exc:
            raise HelloFreshError(tr("HelloFresh is niet bereikbaar", "HelloFresh is not reachable")) from exc
        finally:
            _last_request = time.monotonic()
    host = (urlsplit(response.url).hostname or "").lower()
    if host != f"www.{HOST}" and host != HOST:
        raise HelloFreshError(tr("HelloFresh stuurde ons ergens anders heen", "HelloFresh redirected us elsewhere"))
    if response.status_code == 404:
        raise ValueError(tr("HelloFresh heeft dit niet (meer)", "HelloFresh does not have this (any more)"))
    if response.status_code != 200:
        raise HelloFreshError(tr(f"HelloFresh antwoordde met status {response.status_code}", f"HelloFresh answered with status {response.status_code}"))
    return response.text


def _next_payload(page: str) -> dict[str, Any]:
    found = _NEXT_DATA.search(page)
    try:
        payload = json.loads(found.group(1))["props"]["pageProps"]["ssrPayload"] if found else None
    except (ValueError, KeyError, TypeError):
        payload = None
    if not isinstance(payload, dict):
        raise HelloFreshError(tr("HelloFresh gaf de pagina in een onbekend formaat", "HelloFresh returned the page in an unknown format"))
    return payload


def _json_ld_recipe(page: str) -> dict[str, Any]:
    for block in _JSON_LD.findall(page):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for item in data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []:
            if isinstance(item, dict) and item.get("@type") == "Recipe":
                return item
    raise HelloFreshError(tr("HelloFresh gaf het recept in een onbekend formaat", "HelloFresh returned the recipe in an unknown format"))


# --- weken ------------------------------------------------------------------------------------------


def week_code(day: date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def monday(week: str) -> date:
    """De maandag van een weekcode (`2026-W44`); ook voor de andere maaltijdboxen."""
    found = _WEEK.match(week or "")
    if not found:
        raise ValueError(tr("Geen geldige week", "Not a valid week"))
    try:
        return date.fromisocalendar(int(found.group(1)), int(found.group(2)), 1)
    except ValueError as exc:
        raise ValueError(tr("Geen geldige week", "Not a valid week")) from exc


MAX_LOAD_WEEKS = 4  # Recepten zoeken kijkt hooguit zoveel weken vooruit (de actuele week meegerekend)


def upcoming_weeks(count: int) -> list[str]:
    """De actuele week en de weken erna, `count` in totaal."""
    start = monday(week_code(date.today()))
    return [week_code(start + timedelta(days=7 * n)) for n in range(max(0, min(int(count), MAX_LOAD_WEEKS)))]


def load_upcoming(get: Any, count: int, errors: tuple[type[Exception], ...]) -> Exception | None:
    """Haal de menu's van de komende weken op met `get(week)`, zodat een zoekopdracht ze vindt. Een week die mislukt of nog
    geen menu heeft slaan we over. Geeft de laatste fout terug als er geen enkele week gelukt is, anders `None`."""
    last: Exception | None = None
    loaded = 0
    for week in upcoming_weeks(count):
        try:
            get(week)
            loaded += 1
        except errors as exc:
            last = exc
    return None if loaded else last


def neighbours(week: str) -> tuple[str, str]:
    start = monday(week)
    return week_code(start - timedelta(days=7)), week_code(start + timedelta(days=7))


# --- menu -------------------------------------------------------------------------------------------


def minutes(value: Any) -> int | None:
    found = _DURATION.match(str(value or ""))
    if not found or not (found.group(1) or found.group(2)):
        return None
    return int(found.group(1) or 0) * 60 + int(found.group(2) or 0) or None


def recipe_key(url: Any) -> str | None:
    """`slug-id` uit een HelloFresh-receptadres; een recept is alleen met beide op te halen."""
    parts = urlsplit(str(url or ""))
    if (parts.hostname or "").lower() not in {HOST, f"www.{HOST}"} or not parts.path.startswith("/recipes/"):
        return None
    key = parts.path[len("/recipes/"):].strip("/")
    return key if _KEY.match(key) else None


def _name(entry: Any, field: str = "name") -> str:
    return str(entry.get(field) or "") if isinstance(entry, dict) else ""


def summary(recipe: dict[str, Any]) -> dict[str, Any] | None:
    key = recipe_key(recipe.get("websiteUrl"))
    if not key or not recipe.get("name"):
        return None
    image = recipe.get("imagePath")
    cuisines = recipe.get("cuisines") if isinstance(recipe.get("cuisines"), list) else []
    return {
        "key": key,
        "name": str(recipe["name"]).strip(),
        "headline": str(recipe.get("headline") or "").strip(),
        "image": IMAGE_BASE + image if isinstance(image, str) and image.startswith("/image/") else "",
        "minutes": minutes(recipe.get("totalTime")) or minutes(recipe.get("prepTime")),
        "difficulty": recipe.get("difficulty") if isinstance(recipe.get("difficulty"), int) else None,
        "cuisine": _name(cuisines[0]) if cuisines else "",
        "label": _name(recipe.get("label"), "text"),
        "url": f"{BASE}/recipes/{key}",
    }


def parse_menu(page: str) -> dict[str, Any]:
    payload = _next_payload(page)
    courses = payload.get("courses")
    if not isinstance(courses, list):
        raise HelloFreshError(tr("HelloFresh gaf het menu in een onbekend formaat", "HelloFresh returned the menu in an unknown format"))
    seen: set[str] = set()
    recipes: list[dict[str, Any]] = []
    for course in courses:
        item = summary(course.get("recipe") or {}) if isinstance(course, dict) else None
        if item and item["key"] not in seen:
            seen.add(item["key"])
            recipes.append(item)
    return {"week": str(payload.get("activeWeek") or ""), "currentWeek": str(payload.get("currentWeek") or ""), "recipes": recipes}


def _stored(week: str) -> tuple[datetime, dict[str, Any]] | None:
    with closing(_connect()) as conn:
        row = conn.execute("SELECT fetched_at, payload FROM hellofresh_menus WHERE week = ?", (week,)).fetchone()
    if not row:
        return None
    try:
        return datetime.fromisoformat(row["fetched_at"]), json.loads(row["payload"])
    except ValueError:
        return None


def _store(menu: dict[str, Any]) -> None:
    with closing(_connect()) as conn, conn:
        conn.execute(
            "INSERT INTO hellofresh_menus (week, fetched_at, payload) VALUES (?, ?, ?) "
            "ON CONFLICT(week) DO UPDATE SET fetched_at = excluded.fetched_at, payload = excluded.payload",
            (menu["week"], _now().isoformat(timespec="seconds"), json.dumps(menu, ensure_ascii=False)),
        )


def get_menu(week: Any = None, refresh: bool = False) -> dict[str, Any]:
    """Het menu van een week (standaard de actuele): uit de kopie als die vers is, anders bij HelloFresh."""
    wanted = str(week or "").strip() or week_code(date.today())
    monday(wanted)
    cached = _stored(wanted)
    if cached and not refresh and _now() - cached[0] < MENU_TTL:
        menu, fetched = cached[1], cached[0]
    else:
        try:
            menu = parse_menu(_get(f"/menus/{wanted}"))
            menu["week"] = wanted
            _store(menu)
            fetched = _now()
        except (HelloFreshError, ValueError):
            if not cached:
                raise
            menu, fetched = cached[1], cached[0]  # liever een oudere kopie dan niets
    previous, following = neighbours(wanted)
    return {
        "week": wanted,
        "currentWeek": week_code(date.today()),
        "previous": previous,
        "next": following,
        "fetchedAt": fetched.isoformat(timespec="seconds"),
        "recipes": menu.get("recipes", []),
    }


def search(query: Any, load: int = 0) -> dict[str, Any]:
    """Zoek in alle bewaarde menu's; alle woorden moeten voorkomen in naam, kop, keuken of label. Zonder `load` vraagt dit
    HelloFresh niets; met `load` worden eerst de komende weken opgehaald (en bewaard) die er nog niet zijn."""
    words = _fold(query).split()
    if not words:
        raise ValueError(tr("Vul een zoekwoord in", "Enter a search term"))
    failure = load_upcoming(get_menu, load, (HelloFreshError, ValueError)) if load else None
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT week, payload FROM hellofresh_menus ORDER BY week DESC").fetchall()
    if failure and not rows:
        raise failure
    found: dict[str, dict[str, Any]] = {}
    for row in rows:
        try:
            recipes = json.loads(row["payload"]).get("recipes", [])
        except ValueError:
            continue
        for recipe in recipes:
            text = _fold(" ".join((recipe.get("name", ""), recipe.get("headline", ""), recipe.get("cuisine", ""), recipe.get("label", ""))))
            if recipe.get("key") not in found and all(word in text for word in words):
                found[recipe["key"]] = {**recipe, "week": row["week"]}
    results = list(found.values())
    return {"weeks": len(rows), "total": len(results), "recipes": results[:MAX_RESULTS]}


# --- recept -----------------------------------------------------------------------------------------


def _text(value: Any) -> str:
    """Tekst uit HTML: alinea's worden regels, tags verdwijnen."""
    text = re.sub(r"</(?:p|li|div)>|<br\s*/?>", "\n", str(value or ""), flags=re.I)
    lines = (re.sub(r"\s+", " ", html.unescape(_TAGS.sub("", line))).strip() for line in text.split("\n"))
    return "\n".join(line for line in lines if line)


def ingredient_line(line: Any) -> str:
    """Eén regel zoals HelloFresh hem geeft (`1 stuk(s) Ui`); `ingredients.normalize` leest de eenheden daarvan al."""
    return re.sub(r"\s+", " ", html.unescape(str(line or ""))).strip()


def get_recipe(key: Any) -> dict[str, Any]:
    key = str(key or "").strip()
    if not _KEY.match(key):
        raise ValueError(tr("Geen geldig recept", "Not a valid recipe"))
    data = _json_ld_recipe(_get(f"/recipes/{key}"))
    image = data.get("image")
    steps = [_text(step.get("text") if isinstance(step, dict) else step) for step in data.get("recipeInstructions") or []]
    description = _text(data.get("description"))
    return {
        "key": key,
        "name": re.sub(r"\s+", " ", str(data.get("name") or "")).strip(),
        "description": description if re.search(r"\w", description) else "",
        "image": (image[0] if isinstance(image, list) and image else image) or "",
        "minutes": minutes(data.get("totalTime")),
        "portions": str(data.get("recipeYield") or "").strip(),
        "ingredients": [line for line in (ingredient_line(i) for i in data.get("recipeIngredient") or []) if line],
        "steps": [step for step in steps if step],
        "nutrition": data.get("nutrition") if isinstance(data.get("nutrition"), dict) else None,
    }


def source_url(key: str) -> str:
    return f"{BASE}/recipes/{key}"


def image_url(url: Any) -> str:
    """Het foto-adres als vaste JPG: `f_auto` van HelloFresh geeft soms WebP of AVIF, en Mealie bewaart liever een gewone afbeelding."""
    found = _IMAGE.match(str(url or ""))
    return f"{found.group(1)}/f_jpg,q_auto,w_1200/{found.group(2)}" if found else str(url or "")


def nutrition_fields(data: Any) -> dict[str, str]:
    """Alleen de gevulde, bekende velden als tekst: de pagina heeft ook `null`-waarden en een eigen `servingSize`."""
    if not isinstance(data, dict):
        return {}
    return {key: str(data[key]).strip() for key in NUTRITION_FIELDS if data.get(key) not in (None, "")}


def jsonld(recipe: dict[str, Any], minimal: bool = False) -> dict[str, Any]:
    """Het recept als schema.org Recipe; Mealie importeert dat zonder zelf de pagina op te halen.

    Met `minimal` zonder foto en voedingswaarden: de import van het recept zelf hoeft daar niet op stuk te lopen.
    """
    if not recipe["name"] or not (recipe["ingredients"] or recipe["steps"]):
        raise HelloFreshError(tr("Dit HelloFresh-recept heeft geen ingrediënten of bereiding", "This HelloFresh recipe has no ingredients or steps"))
    data: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "Recipe",
        "name": recipe["name"],
        "url": source_url(recipe["key"]),
        "recipeIngredient": recipe["ingredients"],
        "recipeInstructions": [{"@type": "HowToStep", "text": text} for text in recipe["steps"]],
    }
    if recipe.get("description"):
        data["description"] = recipe["description"]
    if recipe.get("portions"):
        data["recipeYield"] = recipe["portions"]
    if recipe.get("minutes"):
        data["totalTime"] = f"PT{recipe['minutes']}M"
    if not minimal:
        if recipe.get("image"):
            data["image"] = [image_url(recipe["image"])]
        nutrition = nutrition_fields(recipe.get("nutrition"))
        if nutrition:
            data["nutrition"] = {"@type": "NutritionInformation", **nutrition}
    return data


MEALIE_TIMEOUT = mealkit_import.MEALIE_TIMEOUT


def import_into_mealie(recipe: dict[str, Any], attempts_log: list[dict[str, Any]] | None = None) -> str:
    """Zet het recept in Mealie en geeft het slug: volledig, dan zonder foto en voedingswaarden, en daarna door Mealie de
    HelloFresh-pagina zelf te laten lezen. De regels voor het opnieuw proberen staan in `mealkit_import`."""
    url = source_url(recipe["key"])
    attempts = (
        ("volledig", lambda: mealie.import_via_jsonld(url, jsonld(recipe))),
        ("zonder foto en voedingswaarden", lambda: mealie.import_via_jsonld(url, jsonld(recipe, minimal=True))),
        ("via de pagina", lambda: mealie.import_via_url(url)),
    )
    return mealkit_import.import_into_mealie("HelloFresh", recipe["key"], attempts, HelloFreshError, attempts_log)


def run_import(key: Any) -> dict[str, str]:
    """Haal het recept op en zet het in Mealie: `{"slug", "mealieUrl"}`; zie `mealkit_import.run_import`."""
    return mealkit_import.run_import(
        "hellofresh", "HelloFresh", key, lambda k: bool(_KEY.match(k)), source_url,
        lambda k, attempts: import_into_mealie(get_recipe(k), attempts), HelloFreshError,
    )


def detail(key: Any) -> dict[str, Any]:
    """Het recept in de vorm van het zoekscherm (ingrediënten als regels); zoals Jumbo en Picnic, zonder voedingswaarden."""
    recipe = get_recipe(key)
    return {
        "id": recipe["key"], "name": recipe["name"], "description": recipe["description"], "image": recipe["image"],
        "minutes": recipe["minutes"], "portions": recipe["portions"], "url": source_url(recipe["key"]),
        "ingredients": [{"line": line} for line in recipe["ingredients"]], "steps": recipe["steps"], "nutrition": [],
    }
