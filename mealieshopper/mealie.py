import asyncio
import datetime
import json
import logging
import math
import re
import sqlite3
import ssl
import time
import unicodedata
import uuid
from os import environ
from typing import Any
from urllib.parse import urlparse

import httpx

from . import ah, auth, http_client, ingredients, jumbo, jumbo_recipes
from .i18n import tr

log = logging.getLogger(__name__)

TIMEOUT: float = 30.0  # seconden, de standaard van de client: elk verzoek geeft zijn eigen time-out mee
RECIPE_WORKERS: int = 8  # recepten tegelijk ophalen voor het weekmenu, zoals de threadpool van voorheen

# Mealie niet bereikbaar: de verbinding kwam er niet, of viel weg voordat het antwoord binnen was.
UNREACHABLE: tuple[type[httpx.HTTPError], ...] = (httpx.NetworkError, httpx.RemoteProtocolError)


def _client() -> httpx.AsyncClient:
    """De gedeelde client voor Mealie. Het token gaat niet in de client maar per verzoek mee (`_headers`): het kan
    wisselen via Instellingen > Koppelen > Mealie. Volgt doorverwijzingen, zoals voor de omzetting naar httpx. Een verzoek
    dat dat niet mag, geeft zelf `follow_redirects=False` mee. Een receptpagina gaat via `http_client.session`."""
    return http_client.client("mealie", timeout=TIMEOUT)


def _certificate_error(exc: BaseException) -> bool:
    """Of een verbindingsfout van httpx uit ssl komt (een certificaat dat niet klopt). httpx heeft daar geen eigen soort
    fout voor: de fout van ssl hangt als oorzaak aan de `ConnectError`."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        if isinstance(current, ssl.SSLError):
            return True
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return False

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


async def _ui_secret(key: str) -> str:
    """Lees een in de UI opgeslagen waarde; een onleesbare database laat de omgevingsvariabelen intact."""
    global _db_warned
    try:
        return (await auth.get_secret(key)).strip()
    except (sqlite3.Error, OSError) as exc:
        if not _db_warned:
            _db_warned = True
            log.warning("Mealie-koppeling niet uit de database te lezen (%s); omgevingsvariabelen worden gebruikt.", exc)
        return ""


async def _config() -> tuple[str, str, str | None]:
    """(adres, token, bron). Adres en token komen altijd uit dezelfde bron, nooit gemengd."""
    ui_url, ui_token = await _ui_secret(SECRET_URL), await _ui_secret(SECRET_TOKEN)
    if ui_url and ui_token:
        return ui_url.rstrip("/"), ui_token, "ui"
    env_url, env_token = environ.get("MEALIE_URL", "").strip(), environ.get("MEALIE_API_TOKEN", "").strip()
    if env_url and env_token:
        return env_url.rstrip("/"), env_token, "env"
    return "", "", None


async def mealie_url() -> str:
    return (await _config())[0]


async def mealie_token() -> str:
    return (await _config())[1]


def _headers(token: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }


async def auth_headers() -> dict[str, str]:
    """De headers voor een verzoek aan Mealie, met het token van dit moment."""
    return _headers(await mealie_token())


async def _endpoint() -> tuple[str, str]:
    """Adres en token voor een verzoek aan Mealie uit een keer `_config()`: per verzoek een keer lezen, en adres en token
    altijd van hetzelfde moment. Leeg als Mealie niet gekoppeld is."""
    url, token, _ = await _config()
    return url, token


async def _configured() -> tuple[str, str]:
    """Zoals `_endpoint`, maar met de fout van `ensure_configured` als Mealie niet gekoppeld is."""
    url, token = await _endpoint()
    if not url or not token:
        raise RuntimeError(
            tr(
                "Mealie is niet gekoppeld. Koppel Mealie bij Instellingen > Koppelen > Mealie.",
                "Mealie is not linked. Link Mealie under Settings > Link > Mealie.",
            )
        )
    return url, token


async def ensure_configured() -> None:
    await _configured()


async def check_connection(url: str, token: str) -> dict[str, Any]:
    """Controleer adres en token bij Mealie zelf; geeft gebruiker en versie, of een duidelijke fout."""
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    try:
        response = await _client().get(
            f"{url}/api/users/self", headers=headers, timeout=15, follow_redirects=False
        )
    except httpx.TimeoutException:
        raise RuntimeError("Mealie reageert niet (time-out). Controleer het adres en je verbinding.") from None
    except UNREACHABLE as exc:
        if _certificate_error(exc):
            raise RuntimeError(
                "Het certificaat van Mealie wordt niet vertrouwd. Controleer het adres of het certificaat."
            ) from None
        raise RuntimeError(
            f"Mealie is niet bereikbaar op {url}. Controleer het adres en of deze server erbij kan."
        ) from None
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
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
    if response.status_code >= 400:
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
        about = await _client().get(f"{url}/api/app/about", timeout=10, follow_redirects=False)
        if about.status_code < 400:
            version = (about.json() or {}).get("version")
    except (httpx.HTTPError, ValueError):
        pass  # de versie is een extraatje
    return {"username": data.get("username"), "version": version}


async def status(verify: bool = False) -> dict[str, Any]:
    """Toestand van de koppeling; bevat nooit de token."""
    url, token, source = await _config()
    result: dict[str, Any] = {
        "configured": bool(url and token),
        "url": url,
        "source": source,
    }
    if verify and result["configured"]:
        result["verified"] = True
        _week_start_cache.pop(url, None)  # Koppeling controleren: een andere eerste dag in Mealie meteen overnemen
        try:
            result.update(await check_connection(url, token))
            result["connected"] = True
        except RuntimeError as exc:
            result["connected"] = False
            result["error"] = str(exc)
    result["weekStart"] = await week_start()
    return result


async def link(url: Any, token: Any) -> dict[str, Any]:
    """Controleer en bewaar (versleuteld) het adres en de token. Mislukt de controle, dan wordt niets bewaard."""
    clean_url = normalize_url(url)
    clean_token = str(token or "").strip()
    if not clean_token:
        raise ValueError("Plak de API-token van Mealie.")
    if re.search(r"\s", clean_token):
        raise ValueError("De token bevat spaties of regeleinden. Kopieer hem opnieuw uit Mealie.")
    checked = await check_connection(clean_url, clean_token)
    await auth.set_secret(SECRET_URL, clean_url)
    await auth.set_secret(SECRET_TOKEN, clean_token)
    return {**(await status()), **checked, "verified": True, "connected": True}


async def clear() -> dict[str, Any]:
    """Verwijder de koppeling uit de UI; daarna geldt weer een eventuele omgevingsconfiguratie."""
    await auth.delete_secret(SECRET_URL)
    await auth.delete_secret(SECRET_TOKEN)
    return await status()


async def import_via_url(url: str) -> str:
    base, token = await _endpoint()
    response = await _client().post(
        f"{base}/api/recipes/create/url",
        headers=_headers(token),
        json={"url": url, "include_tags": True, "include_categories": True},
        timeout=60,
    )
    if response.status_code >= 400:
        raise MealieImportError(f"Mealie kon de pagina niet importeren ({response.status_code}): {response.text[:200]}", response.status_code)
    return await parse_imported_ingredients(response.json())


async def import_via_html(url: str) -> str:
    # Een eigen sessie, niet de gedeelde client: zet de site bij een doorverwijzing een cookie (toestemming, een
    # cookiecontrole), dan gaat die mee naar de volgende stap. Na deze ene pagina sluit de sessie met haar cookies.
    async with http_client.session("mealie-page", headers=BROWSER_HEADERS, timeout=60, follow_redirects=True) as page:
        page_response = await page.get(url)
    if page_response.status_code >= 400:
        raise RuntimeError(f"Kon de pagina niet ophalen ({page_response.status_code})")
    return await _post_html_or_json(url, page_response.text)


class MealieImportError(RuntimeError):
    """Mealie weigerde een import. `status_code` is te tonen; `str(self)` kan het antwoord van Mealie bevatten (alleen in het logboek)."""

    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code


# Mealie leest een recept met recipe-scrapers, en dat kiest zijn scraper op de host van de link die we meegeven. Voor deze hosts
# heeft het een eigen scraper die alleen de pagina van die site leest en de JSON-LD die wij sturen negeert: dan komen alleen
# de titel en de foto in Mealie, zonder ingrediënten en bereiding (#242). Zo'n recept sturen we onder een neutrale link
# (`.invalid` bestaat nooit), zodat de algemene schema.org-scraper het leest, en daarna zetten we de echte bron terug.
JSONLD_IGNORING_HOSTS = frozenset({"picnic.app"})
NEUTRAL_SOURCE = "https://mealieshopper.invalid/recipe"


def _scrape_url(url: str) -> str:
    """De link waaronder Mealie een recept uit JSON-LD leest: de bron zelf, of `NEUTRAL_SOURCE` voor een host uit
    `JSONLD_IGNORING_HOSTS` (ook met `www.` of een subdomein ervoor)."""
    host = (urlparse(url).hostname or "").lower()
    if any(host == known or host.endswith(f".{known}") for known in JSONLD_IGNORING_HOSTS):
        return NEUTRAL_SOURCE
    return url


async def import_via_jsonld(url: str, recipe: dict[str, Any], catalog: "Catalog | None" = None) -> str:
    """Importeer een schema.org Recipe (JSON-LD); Mealie hoeft de pagina dan niet op te halen. `url` is de bron die
    Mealie bij het recept bewaart (en waarop `find_recipe_by_source` zoekt).

    `catalog`: bij een reeks imports (de bulkimport) de eenheden en ingrediënten van Mealie, één keer opgehaald.
    """
    return await _post_html_or_json(_scrape_url(url), json.dumps(recipe), catalog, source=url)


async def _set_source(slug: str, source: str) -> None:
    """Zet de bron (`orgURL`) van een recept. Best effort: lukt het niet, dan staat het recept er al wel, met alles erin."""
    try:
        base, token = await _endpoint()
        response = await _client().patch(
            f"{base}/api/recipes/{slug}", headers=_headers(token), json={"orgURL": source}, timeout=30
        )
        if response.status_code >= 400:
            log.warning("Bron van %s niet gezet (%s)", slug, response.status_code)
    except httpx.HTTPError:
        log.warning("Bron van %s niet gezet door een verbindingsfout", slug)


async def _post_html_or_json(url: str, data: str, catalog: "Catalog | None" = None, *, source: str | None = None) -> str:
    """Stuur HTML of JSON-LD naar Mealie onder link `url`. Is `source` een andere link (zie `_scrape_url`), dan wordt die
    daarna de bron van het recept."""
    base, token = await _endpoint()
    response = await _client().post(
        f"{base}/api/recipes/create/html-or-json",
        headers=_headers(token),
        json={
            "data": data,
            "url": url,
            "include_tags": True,
            "include_categories": True,
        },
        timeout=60,
    )
    if response.status_code >= 400:
        raise MealieImportError(f"Mealie import mislukt ({response.status_code}): {response.text}", response.status_code)
    slug = response.json()
    if source and source != url:
        await _set_source(slug, source)
    return await parse_imported_ingredients(slug) if catalog is None else await parse_imported_ingredients(slug, catalog)


async def import_from_url(url: str) -> str:
    await ensure_configured()
    errors: list[str] = []

    # Allerhande-recepten komen via de AH-API: de websitepagina van AH blokkeert servers (403).
    recipe_id = ah.allerhande_recipe_id(url)
    if recipe_id:
        try:
            return await import_via_jsonld(url, await ah.get_recipe_jsonld(recipe_id))
        except Exception as exc:  # noqa: BLE001 - val terug op de pagina-import
            errors.append(f"AH-API: {exc}")

    # Jumbo-recepten ook via de API: jumbo.com weert servers, en zo komen ingrediënten en bereiding zeker mee.
    jumbo_id = jumbo_recipes.recipe_id_from_url(url)
    if jumbo_id and await jumbo.configured_refresh_token():
        try:
            recipe = await jumbo_recipes.get_recipe(jumbo_id)
            return await import_via_jsonld(jumbo_recipes.source_url(recipe), jumbo_recipes.jsonld(recipe))
        except Exception as exc:  # noqa: BLE001 - val terug op de pagina-import
            errors.append(f"Jumbo-API: {exc}")

    try:
        return await import_via_url(url)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"Mealie: {exc}")
    try:
        return await import_via_html(url)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"pagina: {exc}")
    raise RuntimeError("; ".join(errors))


class MealieLookupError(RuntimeError):
    """Mealie kon niet zoeken op bron. Bij een bulkimport stoppen we dan: anders komt alles er dubbel in."""


_JUMBO_BARE_URL = re.compile(r"^https?://(?:www\.)?jumbo\.com/recepten/(\d{1,20}(?:-\d{1,3})?)/?$", re.I)


def _source_filter(url: str) -> str:
    """Het `queryFilter` waarmee Mealie een recept uit deze bron vindt.

    Allerhande en Jumbo op het receptnummer: de link staat er met of zonder (dezelfde) naam in, en Jumbo past de naam
    in de link aan als een recept een andere titel krijgt. Andere links als geheel.
    """
    recipe_id = ah.allerhande_recipe_id(url)
    if recipe_id:
        return f'org_url LIKE "%/r-r{recipe_id}/%" OR org_url LIKE "%/r-r{recipe_id}"'
    jumbo_id = jumbo_recipes.recipe_id_from_url(url) or next(iter(_JUMBO_BARE_URL.findall(url)), None)
    if jumbo_id:
        return (
            f'org_url LIKE "%jumbo.com/recepten/%-{jumbo_id}" OR org_url LIKE "%jumbo.com/recepten/%-{jumbo_id}/" '
            f'OR org_url LIKE "%jumbo.com/recepten/{jumbo_id}" OR org_url LIKE "%jumbo.com/recepten/{jumbo_id}/"'
        )
    return f'org_url LIKE "{url}"'


async def find_recipe_summary_by_source(source_url: Any, *, strict: bool = False) -> dict[str, Any] | None:
    """Het recept (zoals in de receptenlijst van Mealie, met `slug`, `tags` en `recipeCategory`) dat al uit deze bron
    in Mealie staat, of None.

    Mealie bewaart bij elke import de meegegeven link als `org_url` en kan daarop zoeken (`queryFilter` met `LIKE`,
    hoofdletterongevoelig); zie `_source_filter`. Lukt het zoeken niet (een oudere Mealie, Mealie niet bereikbaar),
    dan None, of met `strict` een `MealieLookupError`. Een link die niet te zoeken is (leeg, te lang, met een
    aanhalingsteken) geeft altijd None.
    """
    url = str(source_url or "").strip()
    if not url or '"' in url or len(url) > 1000:
        return None
    base, token = await _endpoint()
    if not base or not token:
        return None
    try:
        response = await _client().get(
            f"{base}/api/recipes",
            headers=_headers(token),
            params={"queryFilter": _source_filter(url), "page": 1, "perPage": 1},
            timeout=15,
        )
        if response.status_code >= 400:
            log.info("Mealie kon niet zoeken op bron (%s).", response.status_code)
            if strict:
                raise MealieLookupError(f"Zoeken op bron mislukt ({response.status_code})")
            return None
        items = (response.json() or {}).get("items") or []
    except (httpx.HTTPError, ValueError, AttributeError) as exc:
        log.info("Zoeken op bron in Mealie mislukt (%s).", exc)
        if strict:
            raise MealieLookupError(f"Zoeken op bron mislukt ({type(exc).__name__})") from exc
        return None
    found = items[0] if items and isinstance(items[0], dict) and items[0].get("slug") else None
    return found


async def find_recipe_by_source(source_url: Any) -> str | None:
    """De slug van een recept dat al uit deze bron in Mealie staat, of None.

    Best effort: lukt het zoeken niet (een oudere Mealie, een vreemde link), dan None en wordt er gewoon geïmporteerd.
    """
    found = await find_recipe_summary_by_source(source_url)
    return str(found["slug"]) if found else None


class OrganizerError(RuntimeError):
    """Mealie geeft of maakt een tag of categorie niet. `forbidden`: de gekoppelde gebruiker mag niet organiseren."""

    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status

    @property
    def forbidden(self) -> bool:
        return self.status in (401, 403)


def _organizer_slug(name: str) -> str:
    """Ongeveer het slug dat Mealie van een naam maakt (kleine letters, zonder accenten, met streepjes)."""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def _organizer_ref(item: Any) -> dict[str, str] | None:
    if not isinstance(item, dict) or not item.get("id") or not str(item.get("name") or "").strip():
        return None
    return {"id": str(item["id"]), "name": str(item["name"]).strip(), "slug": str(item.get("slug") or "")}


async def _organizer(kind: str, name: Any) -> dict[str, str]:
    """De tag of categorie (`kind`: tags of categories) met deze naam, hoofdletters maken niet uit; anders een nieuwe.

    Eerst zoeken op naam, dan op slug (een oudere Mealie kent `search` niet), en pas daarna aanmaken. Aanmaken vraagt
    in Mealie het recht *organiseren*; zonder dat recht volgt een `OrganizerError` met `forbidden`.
    """
    label = " ".join(str(name or "").split())[:100]
    if not label:
        raise ValueError("Naam ontbreekt")
    root, token = await _endpoint()
    base = f"{root}/api/organizers/{kind}"
    wanted = label.casefold()
    response = await _client().get(base, headers=_headers(token), params={"search": label, "page": 1, "perPage": 100}, timeout=15)
    if response.status_code in (401, 403):
        raise OrganizerError(f"{kind} opzoeken geweigerd ({response.status_code})", response.status_code)
    if response.status_code < 400:
        items = (response.json() or {}).get("items") or []
        for item in items:
            found = _organizer_ref(item)
            if found and found["name"].casefold() == wanted:
                return found
    slug = _organizer_slug(label)
    if slug:
        response = await _client().get(f"{base}/slug/{slug}", headers=_headers(token), timeout=15)
        found = _organizer_ref(response.json()) if response.status_code < 400 else None
        if found and found["name"].casefold() == wanted:
            return found
    response = await _client().post(base, headers=_headers(token), json={"name": label}, timeout=15)
    if response.status_code >= 400:
        raise OrganizerError(f"{kind} aanmaken mislukt ({response.status_code})", response.status_code)
    created = _organizer_ref(response.json())
    if not created:
        raise OrganizerError(f"{kind} aanmaken gaf geen id terug")
    return created


async def ensure_tag(name: Any) -> dict[str, str]:
    """De tag met deze naam in Mealie (`{id, name, slug}`); bestaat hij nog niet, dan wordt hij aangemaakt."""
    return await _organizer("tags", name)


async def ensure_category(name: Any) -> dict[str, str]:
    """De categorie met deze naam in Mealie (`{id, name, slug}`); bestaat ze nog niet, dan wordt ze aangemaakt."""
    return await _organizer("categories", name)


async def add_organizers(
    slug: str,
    tags: list[dict[str, str]] | None = None,
    categories: list[dict[str, str]] | None = None,
    current: dict[str, Any] | None = None,
) -> bool:
    """Zet tags en categorieën bij een recept, naast wat het al heeft. Geeft True als er iets bij kwam.

    `current`: het recept zoals Mealie het in de lijst geeft (uit `find_recipe_summary_by_source`); staan de tags en
    categorieën daarin, dan hoeft het niet opnieuw opgehaald te worden. Er gaat alleen een PATCH uit als er iets
    ontbreekt (op id of op naam), zodat niets dubbel komt. Bewust niet `bulk-actions/tag` van Mealie: dat voegt toe
    zonder te kijken wat het recept al heeft.
    """
    if not tags and not categories:
        return False
    known = isinstance(current, dict) and isinstance(current.get("tags"), list) and isinstance(current.get("recipeCategory"), list)
    recipe = current if known else await _fetch_recipe(slug)
    changes: dict[str, list[dict[str, Any]]] = {}
    for field, wanted in (("tags", tags or []), ("recipeCategory", categories or [])):
        current = [item for item in (recipe.get(field) or []) if isinstance(item, dict)]
        ids = {str(item.get("id")) for item in current if item.get("id")}
        names = {str(item.get("name") or "").strip().casefold() for item in current}
        slugs = {str(item.get("slug") or "") for item in current if item.get("slug")}
        missing = [
            item for item in wanted
            if item["id"] not in ids and item["name"].casefold() not in names and not (item.get("slug") and item["slug"] in slugs)
        ]
        if missing:
            changes[field] = current + missing
    if not changes:
        return False
    base, token = await _endpoint()
    response = await _client().patch(f"{base}/api/recipes/{slug}", headers=_headers(token), json=changes, timeout=30)
    if response.status_code >= 400:
        raise RuntimeError(f"Tags en categorieën zetten mislukt ({response.status_code})")
    return True


async def _catalog(kind: str) -> list[dict[str, Any]]:
    """Alle eenheden (`units`) of ingrediënten (`foods`) die Mealie kent."""
    items: list[dict[str, Any]] = []
    page, per_page = 1, 500
    while True:
        base, token = await _endpoint()
        response = await _client().get(
            f"{base}/api/{kind}",
            headers=_headers(token),
            params={"page": page, "perPage": per_page},
            timeout=30,
        )
        if response.status_code >= 400:
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


async def _create_catalog_item(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    base, token = await _endpoint()
    response = await _client().post(f"{base}/api/{kind}", headers=_headers(token), json=payload, timeout=30)
    if response.status_code >= 400:
        raise RuntimeError(f"{kind} aanmaken mislukt ({response.status_code}): {response.text[:200]}")
    return response.json()


async def _link_to_catalog(parsed: dict[str, Any], units: dict[str, Any], foods: dict[str, Any]) -> dict[str, Any]:
    """Zet de eenheid en het ingrediënt van een geanalyseerde regel om in de objecten van Mealie (met id).

    `units` en `foods` zijn de indexen uit `_catalog_index`; wat ontbreekt wordt aangemaakt en erin gezet.
    """
    unit = None
    unit_name = parsed["unit"]["name"] if isinstance(parsed.get("unit"), dict) else ""
    if unit_name:
        unit = next((units[key] for key in ingredients.unit_lookup_names(unit_name) if key in units), None)
        if unit is None:
            unit = await _create_catalog_item("units", ingredients.unit_definition(unit_name))
            units.update(_catalog_index([unit]))
    food = None
    food_name = parsed["food"]["name"].strip() if isinstance(parsed.get("food"), dict) else ""
    if food_name:
        food = foods.get(food_name.lower())
        if food is None:
            food = await _create_catalog_item("foods", {"name": food_name})
            foods.update(_catalog_index([food]))
    linked = {**parsed, "unit": unit, "food": food, "isFood": bool(food)}
    linked.pop("analyzedByMealieShopper", None)
    return linked


class Catalog:
    """De eenheden en ingrediënten van Mealie, één keer opgehaald voor een reeks imports (de bulkimport).

    Zonder dit object haalt elke import de hele lijst opnieuw op. Wat tijdens het koppelen wordt aangemaakt, komt er
    meteen bij (`_link_to_catalog` werkt de indexen bij), zodat het volgende recept het niet nog eens aanmaakt.
    """

    def __init__(self) -> None:
        self.units: dict[str, dict[str, Any]] | None = None
        self.foods: dict[str, dict[str, Any]] | None = None

    def reset(self) -> None:
        self.units = self.foods = None

    async def indexes(self) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        if self.units is None:
            self.units = _catalog_index(await _catalog("units"))
        if self.foods is None:
            self.foods = _catalog_index(await _catalog("foods"))
        return self.units, self.foods


async def parse_imported_ingredients(slug: str, catalog: Catalog | None = None) -> str:
    """Zet de ingrediëntregels van een net geïmporteerd recept om in aantal, eenheid en ingrediënt.

    Mealie bewaart een geïmporteerde regel als losse tekst ("200 ml melk") in de notitie. Hier halen we aantal,
    eenheid en ingrediënt eruit en koppelen die aan de eenheden en ingrediënten van Mealie (ontbrekende worden
    aangemaakt), zodat Mealie ze zelf kent: het recept schaalt, de boodschappenlijst telt op en de weergave klopt.
    Geeft altijd het slug terug: lukt dit niet, dan blijft het recept zoals Mealie het importeerde.
    """
    try:
        recipe = await _fetch_recipe(slug)
        lines = recipe.get("recipeIngredient") if isinstance(recipe, dict) else None
        parsed_lines = [ingredients.normalize(line) for line in lines or []]
        if not any(new is not old for new, old in zip(parsed_lines, lines or [])):
            return slug
        if catalog is not None:
            units, foods = await catalog.indexes()
        else:
            units, foods = _catalog_index(await _catalog("units")), _catalog_index(await _catalog("foods"))
        # Na elkaar, niet tegelijk: een eenheid die de eerste regel aanmaakt, vindt de volgende regel in de index.
        linked = [
            await _link_to_catalog(new, units, foods) if new is not old else old
            for new, old in zip(parsed_lines, lines)
        ]
        base, token = await _endpoint()
        response = await _client().patch(
            f"{base}/api/recipes/{slug}",
            headers=_headers(token),
            json={"recipeIngredient": linked},
            timeout=60,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Recept bijwerken mislukt ({response.status_code}): {response.text[:200]}")
    except Exception as exc:  # noqa: BLE001 - het recept is al geïmporteerd; analyseren is een extra
        log.warning("Ingrediënten van %s niet geanalyseerd: %s", slug, exc)
        if catalog is not None:
            catalog.reset()  # bijvoorbeeld een ingrediënt dat iemand anders net aanmaakte: het volgende recept haalt opnieuw op
    return slug


GROUP_SLUG_TTL = 3600  # seconden dat we de groep onthouden
GROUP_SLUG_RETRY = 60  # na een mislukte poging proberen we het pas na zoveel seconden opnieuw
_group_slug_cache: dict[str, tuple[float, str | None]] = {}


async def group_slug() -> str | None:
    """De groep (slug) van de gekoppelde gebruiker, zoals Mealie die in het adres van een recept zet (/g/<groep>/r/<recept>).

    Wordt een uur onthouden. Geeft None als Mealie het niet zegt (een oudere versie) of niet te bereiken is; dan gebruiken we het
    oude adres. Een mislukte poging blokkeert niets en wordt na een minuut opnieuw geprobeerd.
    """
    base, token = await _endpoint()
    if not base:
        return None
    now = time.time()
    cached = _group_slug_cache.get(base)
    if cached and now < cached[0]:
        return cached[1]
    slug: str | None = None
    try:
        response = await _client().get(f"{base}/api/users/self", headers=_headers(token), timeout=10)
        if response.status_code < 400:
            value = (response.json() or {}).get("groupSlug")
            slug = str(value).strip() or None if value else None
    except (httpx.HTTPError, ValueError):
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


async def first_day_of_week() -> int | None:
    """De eerste dag van de week zoals het huishouden in Mealie die heeft ingesteld (0 = zondag), tien minuten onthouden.

    Mealie (v2 en later) geeft die in `GET /api/households/preferences` als `firstDayOfWeek`; elke gebruiker met een token mag dat
    lezen. Geeft None als Mealie niet gekoppeld of niet te bereiken is, of geen geldige dag geeft; na een minuut proberen we het opnieuw.
    """
    base, token = await _endpoint()
    if not base:
        return None
    now = time.time()
    cached = _week_start_cache.get(base)
    if cached and now < cached[0]:
        return cached[1]
    day: int | None = None
    try:
        response = await _client().get(f"{base}/api/households/preferences", headers=_headers(token), timeout=10)
        if response.status_code < 400:
            day = _weekday((response.json() or {}).get("firstDayOfWeek"))
    except Exception:  # noqa: BLE001 - de eerste dag is een extra: een fout mag de status of het koppelen nooit breken
        day = None
    _week_start_cache[base] = (now + (WEEK_START_TTL if day is not None else GROUP_SLUG_RETRY), day)
    return day


async def week_start_setting() -> str:
    """De keuze van de beheerder: "" (zoals in Mealie) of "0".."6". Een onleesbare of ongeldige waarde telt als "zoals in Mealie"."""
    try:
        value = (await auth.get_setting(SETTING_WEEK_START)).strip()
    except (sqlite3.Error, OSError):
        return ""
    return value if value in {str(day) for day in range(7)} else ""


async def week_start() -> dict[str, Any]:
    """Op welke dag de week begint, voor het weekmenu en de MCP-tool.

    Een vaste dag van de beheerder gaat voor (bron "setting", zonder Mealie te vragen); anders de dag uit Mealie (bron "mealie");
    en zegt Mealie niets, dan maandag (bron "default"). `mealie` is wat Mealie zelf zegt (None als dat niet bekend is).
    """
    setting = await week_start_setting()
    from_mealie = await first_day_of_week() if not setting else None
    if setting:
        day, source = int(setting), "setting"
    elif from_mealie is not None:
        day, source = from_mealie, "mealie"
    else:
        day, source = WEEK_START_DEFAULT, "default"
    return {"day": day, "source": source, "setting": setting, "mealie": from_mealie}


async def recipe_page_url(slug: str) -> str:
    """Het adres van een recept in Mealie: /g/<groep>/r/<recept>, of het oude /recipe/<recept> als de groep onbekend is."""
    base = await mealie_url()
    group = await group_slug()
    return f"{base}/g/{group}/r/{slug}" if group else f"{base}/recipe/{slug}"


async def get_meal_plan(start_date: str, end_date: str) -> list[dict[str, Any]]:
    base, token = await _configured()
    response = await _client().get(
        f"{base}/api/households/mealplans",
        headers=_headers(token),
        params={"start_date": start_date, "end_date": end_date, "perPage": "50"},
        timeout=30,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Weekmenu ophalen mislukt ({response.status_code}): {response.text}")
    return response.json().get("items", [])


async def _fetch_recipe(slug: str) -> dict[str, Any]:
    base, token = await _endpoint()
    response = await _client().get(
        f"{base}/api/recipes/{slug}",
        headers=_headers(token),
        timeout=30,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Recept ophalen mislukt ({response.status_code})")
    return response.json()


async def get_recipe(slug: str) -> dict[str, Any]:
    # Regels die Mealie niet analyseerde ("1" + "200 ml melk") krijgen aantal, eenheid en ingrediënt.
    return ingredients.normalize_recipe(await _fetch_recipe(slug))


def _image_path(recipe: dict[str, Any] | None) -> str | None:
    # Late import: mealie_images gebruikt zelf dit module voor de verbinding.
    from . import mealie_images

    return mealie_images.image_path(recipe)


async def _recipes(slugs: list[str]) -> dict[str, dict[str, Any] | None]:
    """De recepten bij `slugs`, hooguit `RECIPE_WORKERS` tegelijk (een semafoor per aanroep). Een recept dat niet lukt, is None."""
    slots = asyncio.Semaphore(RECIPE_WORKERS)

    async def one(slug: str) -> dict[str, Any] | None:
        async with slots:
            try:
                return await get_recipe(slug)
            except Exception:  # noqa: BLE001 - de maaltijd staat er dan zonder details, zoals bij de threadpool
                return None

    found: list[dict[str, Any] | None] = await asyncio.gather(*(one(slug) for slug in slugs))
    return dict(zip(slugs, found))


async def get_meal_plan_with_recipes(start_date: str, end_date: str) -> list[dict[str, Any]]:
    entries = await get_meal_plan(start_date, end_date)
    # Een notitie zonder recept ("Uit eten") heeft "recipe": null, niet een ontbrekend veld.
    slugs = sorted({(entry.get("recipe") or {}).get("slug") for entry in entries if (entry.get("recipe") or {}).get("slug")})
    recipe_map = await _recipes(slugs)

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
        enriched["recipeUrl"] = await recipe_page_url(recipe["slug"]) if recipe.get("slug") else None
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


async def version() -> str | None:
    """De versie van de gekoppelde Mealie ("v3.7.0"), een uur onthouden zoals `group_slug`. None als Mealie het niet zegt."""
    base = await mealie_url()
    if not base:
        return None
    now = time.time()
    cached = _version_cache.get(base)
    if cached and now < cached[0]:
        return cached[1]
    value: str | None = None
    try:
        response = await _client().get(f"{base}/api/app/about", timeout=10, follow_redirects=False)
        if response.status_code < 400:
            value = str((response.json() or {}).get("version") or "").strip() or None
    except (httpx.HTTPError, ValueError, AttributeError):
        value = None
    _version_cache[base] = (now + (GROUP_SLUG_TTL if value else GROUP_SLUG_RETRY), value)
    return value


def _version_tuple(text: Any) -> tuple[int, ...] | None:
    match = re.match(r"v?(\d+)\.(\d+)(?:\.(\d+))?", str(text or "").strip())
    return tuple(int(part or 0) for part in match.groups()) if match else None


async def entry_types() -> list[str]:
    """De maaltijden die de gekoppelde Mealie kent; bij een onbekende versie alleen de vier die elke versie kent."""
    parsed = _version_tuple(await version())
    extra = parsed is not None and parsed >= EXTRA_ENTRY_TYPES_SINCE
    return list(ENTRY_TYPES + (EXTRA_ENTRY_TYPES if extra else ()))


def _mealie_error(response: httpx.Response, nl: str, en: str) -> RuntimeError:
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


async def _entry_type(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in ENTRY_TYPES:
        return text
    if text in EXTRA_ENTRY_TYPES:
        if text in await entry_types():
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


async def _clean_entry(values: dict[str, Any], partial: bool = False) -> dict[str, Any]:
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
        fields["entryType"] = await _entry_type(values.get("entryType") or ("" if partial else "dinner"))
    if not partial or "title" in values:
        fields["title"] = " ".join(str(values.get("title") or "").split())[:TITLE_MAX]
    if not partial or "text" in values:
        fields["text"] = str(values.get("text") or "").strip()[:TEXT_MAX]
    if values.get("recipeId"):
        fields["recipeId"] = _recipe_uuid(values["recipeId"])
    elif values.get("recipeSlug"):
        fields["recipeId"] = _recipe_uuid((await _fetch_recipe(valid_slug(values["recipeSlug"]))).get("id"))
    elif "recipeId" in values or not partial:
        fields["recipeId"] = None
    return fields


def _require_recipe_or_title(fields: dict[str, Any]) -> None:
    if not fields.get("recipeId") and not fields.get("title"):
        raise ValueError(tr("Kies een recept of schrijf een notitie.", "Choose a recipe or write a note."))


async def create_meal_plan_entry(values: dict[str, Any]) -> dict[str, Any]:
    """Zet een recept of een notitie op een dag in het weekmenu van Mealie."""
    base, token = await _configured()
    fields = await _clean_entry(values)
    _require_recipe_or_title(fields)
    response = await _client().post(
        f"{base}/api/households/mealplans", headers=_headers(token), json=fields, timeout=30
    )
    if response.status_code >= 400:
        raise _mealie_error(response, "Mealie kon de maaltijd niet plannen", "Mealie could not plan the meal")
    return response.json()


async def _get_entry(entry_id: int) -> dict[str, Any]:
    base, token = await _endpoint()
    response = await _client().get(
        f"{base}/api/households/mealplans/{entry_id}", headers=_headers(token), timeout=30
    )
    if response.status_code >= 400:
        raise _mealie_error(response, "Maaltijd ophalen uit Mealie mislukt", "Could not fetch the meal from Mealie")
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError(tr("Mealie gaf een onverwacht antwoord", "Mealie gave an unexpected answer"))
    return data


def _same_recipe(new: Any, old: Any) -> bool:
    return str(new or "").strip().lower() == str(old or "").strip().lower()


async def update_meal_plan_entry(entry_id: Any, values: dict[str, Any]) -> dict[str, Any]:
    """Verplaats of wijzig een maaltijd. Mealie wil bij wijzigen het hele item, dus eerst ophalen en dan samenvoegen."""
    base, token = await _configured()
    number = _entry_id(entry_id)
    changes = await _clean_entry(values, partial=True)
    current = await _get_entry(number)
    # `recipeScale` (Mealie v3.29+) gaat zo mee terug; zonder dat veld zet Mealie de porties weer op 1.
    merged = {key: value for key, value in current.items() if key not in _READ_ONLY_ENTRY_FIELDS}
    merged.update(changes)
    merged["id"] = number
    if "recipeScale" in current and "recipeId" in changes and not _same_recipe(changes["recipeId"], current.get("recipeId")):
        merged["recipeScale"] = 1.0  # een ander recept (of een notitie): de porties van het oude gelden niet, net als in Mealie
    _require_recipe_or_title(merged)
    response = await _client().put(
        f"{base}/api/households/mealplans/{number}", headers=_headers(token), json=merged, timeout=30
    )
    if response.status_code >= 400:
        raise _mealie_error(response, "Mealie kon de maaltijd niet wijzigen", "Mealie could not change the meal")
    return response.json()


async def delete_meal_plan_entry(entry_id: Any) -> dict[str, Any]:
    """Haal een maaltijd uit het weekmenu. Het recept zelf blijft in Mealie staan."""
    base, token = await _configured()
    number = _entry_id(entry_id)
    response = await _client().delete(
        f"{base}/api/households/mealplans/{number}", headers=_headers(token), timeout=30
    )
    if response.status_code >= 400:
        raise _mealie_error(response, "Mealie kon de maaltijd niet verwijderen", "Mealie could not remove the meal")
    return {"id": number, "deleted": True}


async def random_meal_plan_entry(values: dict[str, Any]) -> dict[str, Any]:
    """Laat Mealie een recept kiezen volgens de planregels van het huishouden en zet het meteen in het weekmenu."""
    base, token = await _configured()
    if not isinstance(values, dict):
        raise ValueError(tr("Ongeldige invoer.", "Invalid input."))
    payload = {"date": _plan_date(values.get("date")), "entryType": await _entry_type(values.get("entryType") or "dinner")}
    response = await _client().post(
        f"{base}/api/households/mealplans/random", headers=_headers(token), json=payload, timeout=30
    )
    if response.status_code == 404:
        raise RuntimeError(
            tr(
                "Mealie vond geen recept dat bij je planregels past. Kies zelf een recept, of pas de regels aan in Mealie.",
                "Mealie found no recipe that matches your meal plan rules. Choose a recipe yourself, or change the rules in Mealie.",
            )
        )
    if response.status_code >= 400:
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


async def search_recipes(query: Any, page: Any = 1, per_page: int = SEARCH_PAGE) -> dict[str, Any]:
    """Zoek in de recepten van Mealie. Zonder zoekterm alle recepten op naam."""
    base, token = await _configured()
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
    response = await _client().get(f"{base}/api/recipes", headers=_headers(token), params=params, timeout=30)
    if response.status_code >= 400:
        raise _mealie_error(response, "Zoeken in Mealie mislukt", "Searching Mealie failed")
    data = response.json()
    items = [item for item in (data.get("items") if isinstance(data, dict) else None) or [] if isinstance(item, dict) and item.get("slug")]
    total = data.get("total") if isinstance(data, dict) and isinstance(data.get("total"), int) else len(items)
    total_pages = data.get("total_pages") if isinstance(data, dict) else None
    more = number < total_pages if isinstance(total_pages, int) else len(items) >= size
    return {"recipes": [recipe_card(item) for item in items], "total": total, "page": number, "more": bool(more)}


async def recipe_detail(slug: Any) -> dict[str, Any]:
    """Een Mealie-recept met ingrediënten en bereiding, in de vorm van het detailscherm bij Recepten zoeken."""
    await ensure_configured()
    clean = valid_slug(slug)
    recipe = await get_recipe(clean)
    # Een kopje in Mealie ("Voor de saus") is de `title` van het eerste ingrediënt eronder; staat het op een regel zonder
    # tekst, dan schuift het door naar de volgende regel. De kookweergave (static/recipe_view.js) toont het als sectie.
    ingredients = []
    section = ""
    for item in recipe.get("recipeIngredient") or []:
        if not isinstance(item, dict):
            continue
        section = " ".join(str(item.get("title") or "").split()) or section
        line = " ".join(str(item.get("display") or item.get("note") or "").split())
        if line:
            ingredients.append({"line": line, **({"section": section} if section else {})})
            section = ""
    steps, titles = [], []
    for step in recipe.get("recipeInstructions") or []:
        if isinstance(step, dict) and str(step.get("text") or "").strip():
            steps.append(str(step.get("text")).strip())
            titles.append(" ".join(str(step.get("title") or "").split()))
    detail = {
        **recipe_card(recipe),
        "description": str(recipe.get("description") or "").strip(),
        "ingredients": ingredients,
        "steps": steps,
        "url": await recipe_page_url(clean),
    }
    if any(titles):
        detail["stepTitles"] = titles  # even lang als steps; "" is een stap zonder titel
    return detail


async def list_recipes(limit: int = 500) -> list[dict[str, Any]]:
    """Alle recepten in Mealie (naam, slug, afbeelding), voor het zoeken naar receptvoorstellen."""
    base, token = await _configured()
    items: list[dict[str, Any]] = []
    page = 1
    per_page = 100
    while len(items) < limit:
        response = await _client().get(
            f"{base}/api/recipes",
            headers=_headers(token),
            params={"page": page, "perPage": per_page, "orderBy": "name", "orderDirection": "asc"},
            timeout=30,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Recepten ophalen mislukt ({response.status_code})")
        data = response.json()
        batch = data.get("items", []) if isinstance(data, dict) else []
        items.extend(item for item in batch if isinstance(item, dict) and item.get("slug"))
        total_pages = data.get("total_pages") if isinstance(data, dict) else None
        if not batch or (isinstance(total_pages, int) and page >= total_pages) or len(batch) < per_page:
            break
        page += 1
    return items[:limit]
