"""Maaltijdboxen: het weekmenu en de recepten van Marley Spoon Nederland, zonder account.

Marley Spoon heeft geen publieke API en zijn receptpagina's hebben geen schema.org-gegevens. Het menu vraagt de menupagina
van de site zelf op bij een GraphQL-eindpunt dat zonder inloggen en zonder sleutel antwoordt; dat vragen we hier op dezelfde
manier, voor één week tegelijk. Het detail van een recept (ingrediënten, stappen, voedingswaarden) vraagt de site op bij een
tweede eindpunt, met het bezoekerstoken dat de receptpagina aan iedere bezoeker geeft. Dat doen wij bij een import ook: eerst
de receptpagina, dan één vraag met het token van die pagina, dat nergens wordt bewaard of gelogd. Zie docs/onderzoek-marleyspoon.md.

- Het menu van een week bewaren we in SQLite met een tijdstip, zoals bij HelloFresh. Lezen en zoeken gaat uit die kopie;
  Marley Spoon wordt alleen gevraagd als de kopie ontbreekt, verouderd is of de gebruiker ververst. Recepten bewaren we niet.
- We vragen zuinig (één verzoek tegelijk, met een pauze ertussen) en noemen ons in de User-Agent.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import weakref
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import urljoin, urlsplit

import aiosqlite
import httpx

from . import about, auth, http_client, mealie, mealkit_import, nutrition
from .ah_lists import _fold
from .hellofresh import load_upcoming, monday, neighbours, nutrition_fields, week_code
from .i18n import tr

BASE = "https://marleyspoon.nl"
API = "https://api.marleyspoon.com/public/graphql"
API_AUTH = "https://api.marleyspoon.com/graphql"  # receptdetail; vraagt het bezoekerstoken van de receptpagina
API_HOST = "api.marleyspoon.com"
HOST = "marleyspoon.nl"
COUNTRY = "NL"
BRAND = "MS"
RECIPE_TYPES = ("STANDARD", "CORE_DOWN", "PREMIUM")  # dezelfde soorten als de menupagina zelf vraagt
TIMEOUT = 20
MIN_GAP = 1.0  # seconden tussen twee verzoeken aan Marley Spoon
MENU_TTL = timedelta(hours=6)
MAX_RESULTS = 60

_KEY = re.compile(r"^\d{1,12}-[a-z0-9-]{1,200}$")
_NUMBER = re.compile(r"\d+")
_TOKEN = re.compile(r'gon\.api_token="([^"]{20,2000})"')
_EMPHASIS = re.compile(r"__(.+?)__")
PORTIONS = "2"  # de menupagina rekent met twee personen
# Voedingswaarden per portie: sleutel bij Marley Spoon -> (veld van schema.org, eenheid).
NUTRITION = {
    "energy_kcal": ("calories", "kcal"),
    "total_fat": ("fatContent", "g"),
    "saturated_fat": ("saturatedFatContent", "g"),
    "total_carbs": ("carbohydrateContent", "g"),
    "sugar_carbs": ("sugarContent", "g"),
    "protein": ("proteinContent", "g"),
    "fiber": ("fiberContent", "g"),
    "sodium": ("sodiumContent", "mg"),
}

QUERY = """
query GetMenu($country: CountryEnum!, $brand: BrandEnum!, $startDate: Date, $supportedRecipeTypes: [RecipeTypeEnum!]) {
  menu(country: $country, brand: $brand, startDate: $startDate, numberOfWeeks: 1, supportedRecipeTypes: $supportedRecipeTypes) {
    startOfWeek
    recipes {
      id
      slug
      title
      subtitle
      recipeServingTime
      category { displayText }
      image(size: MEDIUM) { url }
    }
  }
}
"""

RECIPE_QUERY = """
query GetRecipeDetails_Web($recipeId: String!) {
  recipe(id: $recipeId) {
    id
    title
    subtitle
    description
    duration { from to unit }
    image(size: LARGE) { url }
    nutritionalInformation { key perPortion }
    shippedIngredients { nameWithQuantity }
    assumedIngredients { name }
    steps { title description }
  }
}
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS marleyspoon_menus (
  week TEXT PRIMARY KEY,
  fetched_at TEXT NOT NULL,
  payload TEXT NOT NULL
);
"""


class MarleySpoonError(RuntimeError):
    """Een fout met een tekst van onszelf (nooit het antwoord van Marley Spoon), die de gebruiker mag zien."""


# Het slot van de pauze, per event loop: een `asyncio.Lock` hoort bij de loop waarin hij voor het eerst wacht.
# Een slot waarop iemand wacht houdt zijn loop in leven (de wachtende task verwijst naar het slot), net als bij de clients van http_client.
_gap_locks: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = weakref.WeakKeyDictionary()
_last_request = 0.0


def _gap_lock() -> asyncio.Lock:
    """Het slot van de lopende event loop: een verzoek aan Marley Spoon tegelijk, pauze en verzoek erbij."""
    loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
    lock: asyncio.Lock | None = _gap_locks.get(loop)
    if lock is None:
        lock = _gap_locks[loop] = asyncio.Lock()
    return lock


async def _wait(seconds: float) -> None:
    """De pauze tussen twee verzoeken. Een eigen functie, zodat een test hem kan vervangen zonder `asyncio.sleep` voor het hele proces."""
    await asyncio.sleep(seconds)


@asynccontextmanager
async def _connect() -> AsyncIterator[aiosqlite.Connection]:
    """Een verbinding met het schema, via `auth.connect()`. Sluit zonder commit."""
    async with auth.connect() as conn:
        await auth.ensure_schema(conn, "marleyspoon", SCHEMA)
        yield conn


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- menu ophalen ---------------------------------------------------------------------------------


def _user_agent() -> str:
    return f"MealieShopper/{about.version()} (+https://github.com/helmerzNL/MealieShopper)"


async def _send(method: str, url: str, host: str, **kwargs: Any) -> httpx.Response:
    """Eén verzoek aan Marley Spoon: één tegelijk, met een pauze ertussen, zonder redirects, alleen naar `host`.

    Een doorverwijzing volgen we niet. Wijst die naar een andere host, dan zegt de fout dat, anders telt hij als elk ander
    antwoord dat geen 200 is."""
    global _last_request
    headers = {"User-Agent": _user_agent(), "Accept-Language": "nl-NL,nl;q=0.9", **kwargs.pop("headers", {})}
    async with _gap_lock():
        wait = MIN_GAP - (time.monotonic() - _last_request)
        if wait > 0:
            await _wait(wait)
        try:
            response = await http_client.client("marleyspoon").request(
                method, url, headers=headers, timeout=TIMEOUT, follow_redirects=False, **kwargs
            )
        except httpx.HTTPError as exc:
            raise MarleySpoonError(tr("Marley Spoon is niet bereikbaar", "Marley Spoon is not reachable")) from exc
        finally:
            _last_request = time.monotonic()
    location: str = response.headers.get("Location", "") if response.is_redirect else ""
    if location and (urlsplit(urljoin(str(response.url), location)).hostname or "").lower() != host:
        raise MarleySpoonError(tr("Marley Spoon stuurde ons ergens anders heen", "Marley Spoon redirected us elsewhere"))
    if response.status_code == 404:
        raise ValueError(tr("Marley Spoon heeft dit niet (meer)", "Marley Spoon does not have this (any more)"))
    if response.status_code != 200:
        raise MarleySpoonError(
            tr(f"Marley Spoon antwoordde met status {response.status_code}", f"Marley Spoon answered with status {response.status_code}")
        )
    return response


async def _graphql(url: str, query: str, variables: dict[str, Any], what: str, token: str = "") -> dict[str, Any]:
    """Het `data`-deel van een GraphQL-antwoord; `variables` komt van onszelf (een gecontroleerde datum of sleutel)."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    response = await _send("POST", url, API_HOST, json={"query": query, "variables": variables}, headers=headers)
    unknown = MarleySpoonError(tr(f"Marley Spoon gaf {what} in een onbekend formaat", f"Marley Spoon returned the {what} in an unknown format"))
    try:
        body = response.json()
    except ValueError as exc:
        raise unknown from exc
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict) or body.get("errors"):
        raise unknown
    return data


async def _post(variables: dict[str, Any]) -> dict[str, Any]:
    return await _graphql(API, QUERY, variables, tr("het menu", "menu"))


def minutes(value: Any) -> int | None:
    """`30-40 minuten` wordt 40: de bovengrens van wat Marley Spoon opgeeft."""
    numbers = [int(n) for n in _NUMBER.findall(str(value or ""))]
    return max(numbers) or None if numbers else None


def recipe_key(slug: Any) -> str | None:
    key = str(slug or "").strip()
    return key if _KEY.match(key) else None


def summary(recipe: dict[str, Any]) -> dict[str, Any] | None:
    key = recipe_key(recipe.get("slug"))
    title = str(recipe.get("title") or "").strip()
    if not key or not title:
        return None
    image = recipe.get("image") if isinstance(recipe.get("image"), dict) else {}
    category = recipe.get("category") if isinstance(recipe.get("category"), dict) else {}
    url = str(image.get("url") or "")
    return {
        "key": key,
        "name": title,
        "headline": str(recipe.get("subtitle") or "").strip(),
        "image": url if urlsplit(url).scheme == "https" else "",
        "minutes": minutes(recipe.get("recipeServingTime")),
        "cuisine": "",
        "label": str(category.get("displayText") or "").strip(),
        "url": f"{BASE}/menu/{key}",
    }


def parse_menu(data: dict[str, Any], start: date) -> dict[str, Any]:
    weeks = data.get("menu")
    if not isinstance(weeks, list):
        raise MarleySpoonError(tr("Marley Spoon gaf het menu in een onbekend formaat", "Marley Spoon returned the menu in an unknown format"))
    recipes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for week in weeks:
        if not isinstance(week, dict) or str(week.get("startOfWeek") or "") != start.isoformat():
            continue  # alleen de gevraagde week: een menu van een andere week tonen we nooit onder deze
        for recipe in week.get("recipes") or []:
            item = summary(recipe) if isinstance(recipe, dict) else None
            if item and item["key"] not in seen:
                seen.add(item["key"])
                recipes.append(item)
    return {"recipes": recipes}


async def _stored(week: str) -> tuple[datetime, dict[str, Any]] | None:
    async with _connect() as conn, conn.execute("SELECT fetched_at, payload FROM marleyspoon_menus WHERE week = ?", (week,)) as cursor:
        row = await cursor.fetchone()
    if not row:
        return None
    try:
        return datetime.fromisoformat(row["fetched_at"]), json.loads(row["payload"])
    except ValueError:
        return None


async def _store(week: str, menu: dict[str, Any]) -> None:
    async with _connect() as conn:
        await conn.execute(
            "INSERT INTO marleyspoon_menus (week, fetched_at, payload) VALUES (?, ?, ?) "
            "ON CONFLICT(week) DO UPDATE SET fetched_at = excluded.fetched_at, payload = excluded.payload",
            (week, _now().isoformat(timespec="seconds"), json.dumps(menu, ensure_ascii=False)),
        )
        await conn.commit()


async def get_menu(week: Any = None, refresh: bool = False) -> dict[str, Any]:
    """Het menu van een week (standaard de actuele): uit de kopie als die vers is, anders bij Marley Spoon."""
    wanted = str(week or "").strip() or week_code(date.today())
    start = monday(wanted)
    cached = await _stored(wanted)
    if cached and not refresh and _now() - cached[0] < MENU_TTL:
        menu, fetched = cached[1], cached[0]
    else:
        try:
            variables = {"country": COUNTRY, "brand": BRAND, "startDate": start.isoformat(), "supportedRecipeTypes": list(RECIPE_TYPES)}
            menu = parse_menu(await _post(variables), start)
            if menu["recipes"]:  # een week die nog geen menu heeft, bewaren we niet: hij kan straks wel een menu krijgen
                await _store(wanted, menu)
            fetched = _now()
        except MarleySpoonError:
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


async def search(query: Any, load: int = 0) -> dict[str, Any]:
    """Zoek in alle bewaarde menu's; alle woorden moeten voorkomen in naam, kop of label. Zonder `load` vraagt dit Marley Spoon
    niets; met `load` worden eerst de komende weken opgehaald (en bewaard) die er nog niet zijn."""
    words = _fold(query).split()
    if not words:
        raise ValueError(tr("Vul een zoekwoord in", "Enter a search term"))
    failure = await load_upcoming(get_menu, load, (MarleySpoonError, ValueError)) if load else None
    async with _connect() as conn, conn.execute("SELECT week, payload FROM marleyspoon_menus ORDER BY week DESC") as cursor:
        rows = await cursor.fetchall()
    if failure and not rows:
        raise failure
    found: dict[str, dict[str, Any]] = {}
    for row in rows:
        try:
            recipes = json.loads(row["payload"]).get("recipes", [])
        except ValueError:
            continue
        for recipe in recipes:
            text = _fold(" ".join((recipe.get("name", ""), recipe.get("headline", ""), recipe.get("label", ""))))
            if recipe.get("key") not in found and all(word in text for word in words):
                found[recipe["key"]] = {**recipe, "week": row["week"]}
    results = list(found.values())
    return {"weeks": len(rows), "total": len(results), "recipes": results[:MAX_RESULTS]}


# --- recept -----------------------------------------------------------------------------------------


def source_url(key: str) -> str:
    return f"{BASE}/menu/{key}"


async def _visitor_token(key: str) -> str:
    """Het token dat de receptpagina aan elke bezoeker geeft (`gon.api_token`); de site zelf vraagt er het receptdetail mee
    op. Het wordt niet bewaard en niet gelogd, en alleen gebruikt voor het ene verzoek hierna."""
    found = _TOKEN.search((await _send("GET", source_url(key), HOST)).text)
    if not found:
        raise MarleySpoonError(tr("Marley Spoon gaf het recept in een onbekend formaat", "Marley Spoon returned the recipe in an unknown format"))
    return found.group(1)


def _plain(value: Any) -> str:
    """Tekst zonder de `__vet__`-markeringen van Marley Spoon, met regels als regels."""
    text = _EMPHASIS.sub(r"\1", str(value or "").replace("\r", ""))
    return "\n".join(line for line in (re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")) if line)


def _number(value: Any) -> str:
    return f"{value:g}" if isinstance(value, (int, float)) and not isinstance(value, bool) else ""


async def get_recipe(key: Any) -> dict[str, Any]:
    key = str(key or "").strip()
    if not _KEY.match(key):
        raise ValueError(tr("Geen geldig recept", "Not a valid recipe"))
    data = (await _graphql(API_AUTH, RECIPE_QUERY, {"recipeId": key.split("-")[0]}, tr("het recept", "recipe"), token=await _visitor_token(key))).get(
        "recipe"
    )
    if not isinstance(data, dict):
        raise ValueError(tr("Marley Spoon heeft dit niet (meer)", "Marley Spoon does not have this (any more)"))
    shipped = [i.get("nameWithQuantity") for i in data.get("shippedIngredients") or [] if isinstance(i, dict)]
    assumed = [i.get("name") for i in data.get("assumedIngredients") or [] if isinstance(i, dict)]
    steps = []
    for step in data.get("steps") or []:
        if isinstance(step, dict):
            title, text = _plain(step.get("title")), _plain(step.get("description"))
            steps.append(f"{title}: {text}" if title and text else title or text)
    values = {item.get("key"): item.get("perPortion") for item in data.get("nutritionalInformation") or [] if isinstance(item, dict)}
    per_portion = {field: f"{_number(values[name])} {unit}" for name, (field, unit) in NUTRITION.items() if _number(values.get(name))}
    duration = data.get("duration") if isinstance(data.get("duration"), dict) else {}
    image = data.get("image") if isinstance(data.get("image"), dict) else {}
    title, subtitle = _plain(data.get("title")), _plain(data.get("subtitle"))
    return {
        "key": key,
        "name": f"{title} {subtitle}".strip() if subtitle else title,
        "description": _plain(data.get("description")),
        "image": str(image.get("url") or "") if urlsplit(str(image.get("url") or "")).scheme == "https" else "",
        "minutes": duration.get("to") if isinstance(duration.get("to"), int) and duration["to"] > 0 else None,
        "portions": PORTIONS,
        "ingredients": [_plain(line) for line in shipped + assumed if _plain(line)],
        "steps": [step for step in steps if step],
        "nutrition": per_portion,
    }


def jsonld(recipe: dict[str, Any], minimal: bool = False) -> dict[str, Any]:
    """Het recept als schema.org Recipe; Mealie importeert dat zonder zelf de pagina op te halen (die heeft geen receptgegevens).

    Met `minimal` zonder foto en voedingswaarden: de import van het recept zelf hoeft daar niet op stuk te lopen.
    """
    if not recipe["name"] or not (recipe["ingredients"] or recipe["steps"]):
        raise MarleySpoonError(
            tr("Dit Marley Spoon-recept heeft geen ingrediënten of bereiding", "This Marley Spoon recipe has no ingredients or steps")
        )
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
            data["image"] = [recipe["image"]]
        if fields := nutrition_fields(recipe.get("nutrition")):
            data["nutrition"] = {"@type": "NutritionInformation", **fields}
    return data


async def import_into_mealie(recipe: dict[str, Any], attempts_log: list[dict[str, Any]] | None = None) -> str:
    """Zet het recept in Mealie en geeft het slug: volledig, en bij een weigering nog eens zonder foto en voedingswaarden.
    Mealie de pagina zelf laten lezen helpt hier niet: die heeft geen receptgegevens."""
    url = source_url(recipe["key"])
    attempts = (
        ("volledig", lambda: mealie.import_via_jsonld(url, jsonld(recipe))),
        ("zonder foto en voedingswaarden", lambda: mealie.import_via_jsonld(url, jsonld(recipe, minimal=True))),
    )
    return await mealkit_import.import_into_mealie("Marley Spoon", recipe["key"], attempts, MarleySpoonError, attempts_log)


async def run_import(key: Any) -> dict[str, str]:
    """Haal het recept op en zet het in Mealie: `{"slug", "mealieUrl"}`; zie `mealkit_import.run_import`."""

    async def work(k: str, attempts: list[dict[str, Any]]) -> str:
        return await import_into_mealie(await get_recipe(k), attempts)

    return await mealkit_import.run_import("marleyspoon", "Marley Spoon", key, lambda k: bool(_KEY.match(k)), source_url, work, MarleySpoonError)


async def detail(key: Any) -> dict[str, Any]:
    """Het recept in de vorm van het zoekscherm (ingrediënten als regels), zoals Jumbo en Picnic, met de voedingswaarden per portie."""
    recipe = await get_recipe(key)
    return {
        "id": recipe["key"],
        "name": recipe["name"],
        "description": recipe["description"],
        "image": recipe["image"],
        "minutes": recipe["minutes"],
        "portions": recipe["portions"],
        "url": source_url(recipe["key"]),
        "ingredients": [{"line": line} for line in recipe["ingredients"]],
        "steps": recipe["steps"],
        "nutrition": nutrition.items(recipe.get("nutrition")),
    }
