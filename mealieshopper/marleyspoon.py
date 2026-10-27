"""Maaltijdboxen: het weekmenu van Marley Spoon Nederland, zonder account.

Marley Spoon heeft geen publieke API en zijn receptpagina's hebben geen schema.org-gegevens. De menupagina van de site
haalt het menu zelf op bij een GraphQL-eindpunt dat zonder inloggen en zonder sleutel antwoordt; dat vragen we hier op
dezelfde manier, voor één week tegelijk. De ingrediënten en stappen van een recept komen op de site alleen via een
token dat de pagina per bezoek krijgt. Dat bootsen we niet na: we tonen het menu met een koppeling naar het recept bij
Marley Spoon, en importeren niet in Mealie. Zie docs/onderzoek-marleyspoon.md.

- Het menu van een week bewaren we in SQLite met een tijdstip, zoals bij HelloFresh. Lezen en zoeken gaat uit die kopie;
  Marley Spoon wordt alleen gevraagd als de kopie ontbreekt, verouderd is of de gebruiker ververst.
- We vragen zuinig (één verzoek tegelijk, met een pauze ertussen) en noemen ons in de User-Agent.
"""
from __future__ import annotations

import json
import re
import threading
import time
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

import requests

from . import about, auth
from .ah_lists import _fold
from .hellofresh import monday, neighbours, week_code
from .i18n import tr

BASE = "https://marleyspoon.nl"
API = "https://api.marleyspoon.com/public/graphql"
API_HOST = "api.marleyspoon.com"
COUNTRY = "NL"
BRAND = "MS"
RECIPE_TYPES = ("STANDARD", "CORE_DOWN", "PREMIUM")  # dezelfde soorten als de menupagina zelf vraagt
TIMEOUT = 20
MIN_GAP = 1.0  # seconden tussen twee verzoeken aan Marley Spoon
MENU_TTL = timedelta(hours=6)
MAX_RESULTS = 60

_KEY = re.compile(r"^\d{1,12}-[a-z0-9-]{1,200}$")
_NUMBER = re.compile(r"\d+")

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

SCHEMA = """
CREATE TABLE IF NOT EXISTS marleyspoon_menus (
  week TEXT PRIMARY KEY,
  fetched_at TEXT NOT NULL,
  payload TEXT NOT NULL
);
"""


class MarleySpoonError(RuntimeError):
    """Een fout met een tekst van onszelf (nooit het antwoord van Marley Spoon), die de gebruiker mag zien."""


_gap_lock = threading.Lock()
_last_request = 0.0


def _connect():
    conn = auth.connect()
    conn.executescript(SCHEMA)
    return conn


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- menu ophalen ---------------------------------------------------------------------------------


def _user_agent() -> str:
    return f"MealieShopper/{about.read_version()} (+https://github.com/helmerzNL/MealieShopper)"


def _post(variables: dict[str, Any]) -> dict[str, Any]:
    """Het `data`-deel van het antwoord op de menuvraag; `variables` komt van onszelf (een gecontroleerde datum)."""
    global _last_request
    with _gap_lock:  # één verzoek tegelijk, met een pauze ertussen
        wait = MIN_GAP - (time.monotonic() - _last_request)
        if wait > 0:
            time.sleep(wait)
        try:
            response = requests.post(
                API,
                json={"query": QUERY, "variables": variables},
                headers={"User-Agent": _user_agent(), "Accept-Language": "nl-NL,nl;q=0.9"},
                timeout=TIMEOUT,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise MarleySpoonError(tr("Marley Spoon is niet bereikbaar", "Marley Spoon is not reachable")) from exc
        finally:
            _last_request = time.monotonic()
    if (urlsplit(response.url).hostname or "").lower() != API_HOST:
        raise MarleySpoonError(tr("Marley Spoon stuurde ons ergens anders heen", "Marley Spoon redirected us elsewhere"))
    if response.status_code != 200:
        raise MarleySpoonError(tr(f"Marley Spoon antwoordde met status {response.status_code}", f"Marley Spoon answered with status {response.status_code}"))
    try:
        body = response.json()
    except ValueError as exc:
        raise MarleySpoonError(tr("Marley Spoon gaf het menu in een onbekend formaat", "Marley Spoon returned the menu in an unknown format")) from exc
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict) or body.get("errors"):
        raise MarleySpoonError(tr("Marley Spoon gaf het menu in een onbekend formaat", "Marley Spoon returned the menu in an unknown format"))
    return data


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


def _stored(week: str) -> tuple[datetime, dict[str, Any]] | None:
    with closing(_connect()) as conn:
        row = conn.execute("SELECT fetched_at, payload FROM marleyspoon_menus WHERE week = ?", (week,)).fetchone()
    if not row:
        return None
    try:
        return datetime.fromisoformat(row["fetched_at"]), json.loads(row["payload"])
    except ValueError:
        return None


def _store(week: str, menu: dict[str, Any]) -> None:
    with closing(_connect()) as conn, conn:
        conn.execute(
            "INSERT INTO marleyspoon_menus (week, fetched_at, payload) VALUES (?, ?, ?) "
            "ON CONFLICT(week) DO UPDATE SET fetched_at = excluded.fetched_at, payload = excluded.payload",
            (week, _now().isoformat(timespec="seconds"), json.dumps(menu, ensure_ascii=False)),
        )


def get_menu(week: Any = None, refresh: bool = False) -> dict[str, Any]:
    """Het menu van een week (standaard de actuele): uit de kopie als die vers is, anders bij Marley Spoon."""
    wanted = str(week or "").strip() or week_code(date.today())
    start = monday(wanted)
    cached = _stored(wanted)
    if cached and not refresh and _now() - cached[0] < MENU_TTL:
        menu, fetched = cached[1], cached[0]
    else:
        try:
            variables = {"country": COUNTRY, "brand": BRAND, "startDate": start.isoformat(), "supportedRecipeTypes": list(RECIPE_TYPES)}
            menu = parse_menu(_post(variables), start)
            if menu["recipes"]:  # een week die nog geen menu heeft, bewaren we niet: hij kan straks wel een menu krijgen
                _store(wanted, menu)
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


def search(query: Any) -> dict[str, Any]:
    """Zoek in alle bewaarde menu's; alle woorden moeten voorkomen in naam, kop of label. Vraagt Marley Spoon niets."""
    words = _fold(query).split()
    if not words:
        raise ValueError(tr("Vul een zoekwoord in", "Enter a search term"))
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT week, payload FROM marleyspoon_menus ORDER BY week DESC").fetchall()
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
