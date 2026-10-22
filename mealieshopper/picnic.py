"""Picnic: onofficiële app-API, op dezelfde manier gebruikt als de Picnic-app zelf.

Picnic heeft geen officiële API. De routes en headers hieronder volgen de community-libraries
`python-picnic-api2` en `picnic-api` en kunnen zonder bericht veranderen; `picnic_explore.py`
toont in dat geval welke vorm de antwoorden nu hebben.

Inloggen werkt met e-mail en wachtwoord (het wachtwoord gaat als MD5 over de lijn, zoals de app
dat doet) en daarna meestal een code per sms. We bewaren alleen de auth-key die Picnic teruggeeft,
nooit het wachtwoord. Picnic kan die key bij elk antwoord vernieuwen; die nieuwe waarde bewaren we.

In tegenstelling tot AH schrijft `add_to_cart` direct in het echte winkelmandje van Picnic. Er wordt
nooit een bestelling geplaatst: de gebruiker rondt dat zelf af in de app.
"""
from __future__ import annotations

import json
import re
import time
from hashlib import md5
from os import environ
from typing import Any
from urllib.parse import quote

import requests

from . import auth, deals
from .i18n import tr
from .packs import MAX_CART_QUANTITY as MAX_QUANTITY, assess_packs, normalize_quantity

API_VERSION = "15"
BASE_URL = "https://storefront-prod.{country}.picnicinternational.com/api/{version}"
COUNTRIES = {"NL", "DE", "FR"}
AUTH_HEADER = "x-picnic-auth"
CLIENT_ID = 30100
HEADERS = {
    "User-Agent": "okhttp/4.9.0",
    "Content-Type": "application/json; charset=UTF-8",
    "x-picnic-agent": "30100;1.206.1-#15408",
    "x-picnic-did": "598F770380CA54B6",
}
AUTH_ERROR_CODES = {"AUTH_ERROR", "AUTH_INVALID_CRED"}
TWO_FACTOR_CHANNELS = {"SMS", "EMAIL"}
PENDING_TTL_SECONDS = 600  # zo lang blijft een login die op de sms-code wacht geldig

AUTH_KEY_SECRET = "PICNIC_AUTH_KEY"
PENDING_SECRET = "PICNIC_PENDING_LOGIN"


class PicnicAuthError(RuntimeError):
    """De login is geweigerd of de koppeling is verlopen."""


class Picnic2FAError(RuntimeError):
    """De tweestapscode is onjuist of verlopen."""


def country_code() -> str:
    code = environ.get("PICNIC_COUNTRY_CODE", "NL").strip().upper()
    return code if code in COUNTRIES else "NL"


def base_url() -> str:
    return BASE_URL.format(country=country_code().lower(), version=API_VERSION)


def _not_linked() -> RuntimeError:
    return RuntimeError(
        tr(
            "Picnic account niet gekoppeld. De beheerder koppelt het bij Beheer > Winkels > Picnic.",
            "Picnic account not linked. The admin links it under Admin > Picnic.",
        )
    )


def _call(
    method: str, path: str, *, token: str | None = None, body: Any = None, text: bool = False
) -> tuple[Any, str | None]:
    """Eén verzoek aan Picnic. Geeft (antwoord, nieuwe auth-key uit de headers) terug.

    Met `text` komt een antwoord dat geen JSON is (sommige pagina's) als tekst terug in plaats van als None.
    """
    headers = dict(HEADERS)
    if token:
        headers[AUTH_HEADER] = token
    response = requests.request(
        method, base_url() + path, headers=headers, json=body, timeout=30
    )
    new_token = response.headers.get(AUTH_HEADER)

    data: Any = None
    if response.content:
        try:
            data = response.json()
        except ValueError:
            data = response.text if text else None
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict) and error.get("code") in AUTH_ERROR_CODES:
        # Bewust niet de tekst van Picnic zelf: de aanroeper kiest een eigen, begrijpelijke melding.
        raise PicnicAuthError(tr("Picnic weigerde de sessie", "Picnic refused the session"))
    if not response.ok:
        raise RuntimeError(f"{response.status_code}: {response.text[:500]}")
    return data, new_token


# --- koppeling ---------------------------------------------------------------------------


def configured_auth_key() -> str:
    # Picnic vernieuwt de key bij gebruik en we bewaren de nieuwste. Een omgevingsvariabele als
    # startwaarde is er niet: de key komt uit een login met sms-code en is dus niet vooraf te maken.
    return auth.get_secret(AUTH_KEY_SECRET)


def save_auth_key(key: str) -> None:
    value = str(key or "").strip()
    if not value:
        raise RuntimeError(tr("Picnic gaf geen sessie terug", "Picnic returned no session"))
    auth.set_secret(AUTH_KEY_SECRET, value)


def _authed(method: str, path: str, *, body: Any = None, text: bool = False) -> Any:
    token = configured_auth_key()
    if not token:
        raise _not_linked()
    try:
        data, new_token = _call(method, path, token=token, body=body, text=text)
    except PicnicAuthError as exc:
        raise PicnicAuthError(
            tr(
                "Picnic-koppeling verlopen of geweigerd. Koppel opnieuw bij Beheer > Winkels > Picnic.",
                "Picnic link expired or refused. Link again under Admin > Picnic.",
            )
        ) from exc
    if new_token and new_token != token:
        save_auth_key(new_token)
    return data


def _save_pending(token: str) -> None:
    auth.set_secret(PENDING_SECRET, json.dumps({"token": token, "at": time.time()}))


def _pending_token() -> str:
    raw = auth.get_secret(PENDING_SECRET)
    try:
        pending = json.loads(raw) if raw else {}
    except ValueError:
        pending = {}
    token = str(pending.get("token") or "")
    if not token or time.time() - float(pending.get("at") or 0) > PENDING_TTL_SECONDS:
        raise RuntimeError(
            tr(
                "Geen lopende Picnic-login. Log opnieuw in.",
                "No Picnic login in progress. Log in again.",
            )
        )
    return token


def login(email: str, password: str) -> dict[str, Any]:
    """Log in. Vraagt Picnic om een tweestapscode, dan is `twoFactorRequired` waar."""
    email = str(email or "").strip()
    if not email or not password:
        raise ValueError(
            tr("E-mailadres en wachtwoord zijn verplicht", "Email address and password are required")
        )
    secret = md5(password.encode("utf-8")).hexdigest()
    try:
        data, token = _call(
            "POST",
            "/user/login",
            body={"key": email, "secret": secret, "client_id": CLIENT_ID},
        )
    except PicnicAuthError as exc:
        raise PicnicAuthError(
            tr("Picnic weigerde de login: controleer e-mailadres en wachtwoord", "Picnic refused the login: check email address and password")
        ) from exc
    if isinstance(data, dict) and data.get("second_factor_authentication_required") is True:
        if not token:
            raise RuntimeError(tr("Picnic gaf geen sessie terug", "Picnic returned no session"))
        _save_pending(token)
        return {"connected": False, "twoFactorRequired": True}
    save_auth_key(token or "")
    auth.delete_secret(PENDING_SECRET)
    return {"connected": True, "twoFactorRequired": False}


def request_2fa_code(channel: str = "SMS") -> None:
    channel = str(channel or "SMS").strip().upper()
    if channel not in TWO_FACTOR_CHANNELS:
        raise ValueError(tr("Onbekend kanaal voor de code", "Unknown channel for the code"))
    _two_factor("/user/2fa/generate", {"channel": channel})


def verify_2fa(code: str) -> dict[str, Any]:
    code = str(code or "").strip()
    if not code:
        raise ValueError(tr("Geen code opgegeven", "No code provided"))
    token = _pending_token()
    new_token = _two_factor("/user/2fa/verify", {"otp": code}, token=token)
    save_auth_key(new_token or token)
    auth.delete_secret(PENDING_SECRET)
    return {"connected": True}


def _two_factor(path: str, body: dict[str, Any], *, token: str | None = None) -> str | None:
    token = token or _pending_token()
    data, new_token = _call("POST", path, token=token, body=body)
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict) and error.get("code"):
        raise Picnic2FAError(
            tr(
                "Picnic weigerde de code: controleer hem of vraag een nieuwe aan",
                "Picnic refused the code: check it or request a new one",
            )
        )
    return new_token


def unlink() -> dict[str, Any]:
    auth.delete_secret(AUTH_KEY_SECRET)
    auth.delete_secret(PENDING_SECRET)
    return {"connected": False}


def status(verify: bool = False) -> dict[str, Any]:
    """Is er een koppeling, en klopt die nog? Geeft geen persoonsgegevens terug."""
    result: dict[str, Any] = {
        "connected": bool(configured_auth_key()),
        "twoFactorPending": False,
    }
    try:
        _pending_token()
        result["twoFactorPending"] = True
    except RuntimeError:
        pass
    if verify and result["connected"]:
        try:
            _authed("GET", "/user")
            result["verified"] = True
        except Exception as exc:  # noqa: BLE001 - de reden tonen we aan de beheerder
            result["verified"] = False
            # Onze eigen tekst bij een verlopen koppeling; bij andere fouten alleen het type, want het
            # bericht kan een stuk van Picnics antwoord bevatten.
            result["error"] = str(exc) if isinstance(exc, PicnicAuthError) else type(exc).__name__
    return result


# --- producten ---------------------------------------------------------------------------


def _walk(node: Any):
    """Alle dicts in een JSON-boom, dieper en dieper. Picnic levert zoekresultaten als widgetboom."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def image_url(image_id: Any, size: str = "medium") -> str | None:
    """Het adres van een afbeelding. Receptafbeeldingen hebben een naamruimte (`recipes/<hash>`), dus de slash blijft."""
    if not image_id:
        return None
    return (
        f"https://storefront-prod.{country_code().lower()}.picnicinternational.com"
        f"/static/images/{quote(str(image_id), safe='/')}/{quote(size, safe='')}.png"
    )


def _euros(cents: Any) -> float | None:
    return round(cents / 100, 2) if isinstance(cents, (int, float)) else None


def _product(unit: dict[str, Any]) -> dict[str, Any] | None:
    product_id = unit.get("id")
    if not product_id or not unit.get("name"):
        return None
    decorators = [d for d in unit.get("decorators") or [] if isinstance(d, dict)]
    price = _euros(unit.get("display_price"))
    promo_decorators = [d for d in decorators if "PROMO" in str(d.get("type", "")).upper()]
    is_bonus = bool(promo_decorators) or bool(unit.get("price_ranges"))
    product = {
        "id": str(product_id),
        "title": str(unit["name"]),
        "brand": None,  # de zoektegel noemt het merk niet apart; het zit in de naam
        "unitSize": unit.get("unit_quantity"),
        "price": price,
        "image": image_url(unit.get("image_id")),
        "isBonus": is_bonus,
        "maxCount": unit.get("max_count"),
    }
    if is_bonus:
        # De tekst op het label en de gewone prijs. De velden zijn nog niet met een echte respons gecontroleerd
        # en Picnic noemt geen looptijd op de zoektegel.
        regular = _euros(unit.get("price"))
        product["promo"] = {
            "label": " | ".join(
                str(d.get("text") or d.get("label")) for d in promo_decorators if d.get("text") or d.get("label")
            )
            or None,
            "priceBefore": regular if regular is not None and price is not None and regular > price else None,
            "validFrom": None,
            "validUntil": None,
        }
    return product


def products_from_search_page(page: Any) -> list[dict[str, Any]]:
    """Haal de producten uit het zoekresultaat van Picnic (een boom met SELLING_UNIT_TILE-knopen)."""
    return products_from_page(page.get("body") if isinstance(page, dict) else None)


def products_from_page(page: Any) -> list[dict[str, Any]]:
    """De producten (SELLING_UNIT_TILE-onderdelen) op een willekeurige pagina van Picnic, zonder dubbelen."""
    seen: set[str] = set()
    products: list[dict[str, Any]] = []
    for node in _walk(page):
        unit = node.get("sellingUnit") if node.get("type") == "SELLING_UNIT_TILE" else None
        product = _product(unit) if isinstance(unit, dict) else None
        if product and product["id"] not in seen:
            seen.add(product["id"])
            products.append(product)
    return products


def page_events(page: Any):
    """De inhoud van een pagina in leesvolgorde: ("heading", [teksten]) voor onderdelen zonder producten, ("tile", eenheid) voor producten.

    Zo zie je onder welke kop een product staat, zoals de app het toont.
    """
    def has_tile(node: Any) -> bool:
        return any(n.get("type") == "SELLING_UNIT_TILE" for n in _walk(node))

    def visit(node: Any):
        if isinstance(node, list):
            for item in node:
                yield from visit(item)
        elif isinstance(node, dict):
            if node.get("type") == "SELLING_UNIT_TILE":
                if isinstance(node.get("sellingUnit"), dict):
                    yield "tile", node["sellingUnit"]
            elif node.get("type") == "PML" and not has_tile(node):
                heads = [n["markdown"].strip() for n in _walk(node) if n.get("type") == "RICH_TEXT" and isinstance(n.get("markdown"), str)]
                heads = [h for h in heads if 0 < len(h) <= 80]
                if heads:
                    yield "heading", heads
            else:
                for value in node.values():
                    yield from visit(value)

    yield from visit(page)


# --- favorieten ---------------------------------------------------------------------------------
# Waar de favorieten staan weet geen enkele library. Picnic zegt het zelf: `/bootstrap` bevat de tabbalk van
# de app, met per tab de pagina waar hij naartoe gaat. We zoeken de tab die over favorieten gaat.

_FAVORITE_TAB = re.compile(r"favou?rit|favoriet", re.I)
_PAGE_REFERENCE = re.compile(r"^[A-Za-z0-9_.:;,=&?%-]{1,200}$")
_LINKED_PAGE = re.compile(r"picnic://store/page;id=([A-Za-z0-9_.-]+)")
MAX_LINKED_PAGES = 4
# Zonder boodschappen wijst de tab naar een pagina met populaire producten; de echte favorieten staan dan misschien op
# deze pagina die de app zelf noemt. We proberen hem als de pagina van de tab niets markeert.
FAVORITE_FALLBACKS = ("purchases-page-root",)


def bootstrap_tabs() -> list[dict[str, Any]]:
    """De tabs van de Picnic-app: id, soort, titel, label en de pagina waar de tab naartoe gaat."""
    data = _authed("GET", "/bootstrap")
    tabs = []
    for tab in data.get("tabs") or [] if isinstance(data, dict) else []:
        if not isinstance(tab, dict):
            continue
        target = tab.get("target") if isinstance(tab.get("target"), dict) else {}
        accessibility = tab.get("accessibility") if isinstance(tab.get("accessibility"), dict) else {}
        tabs.append(
            {
                "id": tab.get("id"),
                "type": tab.get("tab_type"),
                "title": tab["title"][:60] if isinstance(tab.get("title"), str) else None,
                "label": accessibility["label"][:60] if isinstance(accessibility.get("label"), str) else None,
                "reference": target.get("reference") if target.get("type") == "PICNIC_PAGE_REFERENCE" and isinstance(target.get("reference"), str) else None,
            }
        )
    return tabs


def favorites_reference(tabs: list[dict[str, Any]]) -> str | None:
    """De pagina van de tab die over favorieten gaat, of None als de tabbalk er geen heeft."""
    for tab in tabs:
        words = " ".join(str(tab.get(key) or "") for key in ("id", "title", "label", "reference"))
        if tab.get("reference") and _FAVORITE_TAB.search(words) and _PAGE_REFERENCE.match(tab["reference"]):
            return tab["reference"]
    return None


# De hartjesknop op een productpagina krijgt van de server in de code mee of het product favoriet is:
# `likedSellables: {["s123"]: true}` (en `=== undefined ? true`). Dat is hoe de app het zelf weet.
_LIKE_INITIAL = re.compile(r'likedSellables:\s*\{\s*\[\s*"s\d+"\s*\]:\s*(true|false)')
_LIKE_DEFAULT = re.compile(r"===\s*undefined\s*\?\s*(true|false)")
_FAVORITE_HEADING = re.compile(r"favou?riet|favorit", re.I)
MAX_FAVORITE_CHECKS = 30
FAVORITE_CHECK_SECONDS = 25
# Wat we over een product weten bewaren we even: een vlag verandert alleen als je in de Picnic-app een hartje zet,
# en een product dat niet laadde proberen we over twee minuten opnieuw. De pagina van de tab bewaren we kort,
# zodat de stappen waarin de producten gecontroleerd worden niet elke keer de hele pagina ophalen.
FLAG_CACHE_SECONDS = 1800
FLAG_RETRY_SECONDS = 120
CANDIDATES_CACHE_SECONDS = 120
_flag_cache: dict[str, tuple[float, bool | None]] = {}
_candidates_cache: dict[str, Any] = {}


def clear_favorites_cache() -> None:
    """Vergeet wat we over favorieten weten (bij het opnieuw laden, en in de tests)."""
    _flag_cache.clear()
    _candidates_cache.clear()


def product_favorite_flag(product_id: Any) -> bool | None:
    """Is dit product een favoriet? Gelezen uit de hartjescode van zijn productpagina; None als die niets zegt."""
    page = _authed("GET", f"/pages/product-details-page-root?id={quote(_product_id(product_id), safe='')}")
    initial: list[str] = []
    default: list[str] = []
    for node in _walk(page):
        code = node.get("expression")
        if isinstance(code, str):
            initial += _LIKE_INITIAL.findall(code)
            default += _LIKE_DEFAULT.findall(code)
    found = initial or default
    return "true" in found if found else None


def favorites_section(page: Any, products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """De producten onder de kop *Favorieten* van de pagina; staat die kop er niet, dan de eerste producten van de pagina."""
    by_id = {p["id"]: p for p in products}
    section: list[dict[str, Any]] = []
    inside = False
    for kind, value in page_events(page):
        if kind == "heading":
            if any(_FAVORITE_HEADING.search(text) for text in value):
                inside = True
            elif inside and section:
                break
        elif inside:
            product = by_id.get(str(value.get("id")))
            if product and product not in section:
                section.append(product)
    return section or products


_UNSET = object()


def _cached_flag(product_id: str) -> Any:
    entry = _flag_cache.get(product_id)
    if entry is None:
        return _UNSET
    at, flag = entry
    if time.monotonic() - at > (FLAG_CACHE_SECONDS if flag is not None else FLAG_RETRY_SECONDS):
        return _UNSET
    return flag


def _checked_flag(product_id: str) -> bool | None:
    """De vlag van een product uit de bewaarde antwoorden, of vers opgehaald. Een product dat niet laadt geeft None."""
    flag = _cached_flag(product_id)
    if flag is not _UNSET:
        return flag
    try:
        flag = product_favorite_flag(product_id)
    except PicnicAuthError:
        raise
    except Exception:  # noqa: BLE001 - één product dat niet laadt mag de lijst niet breken
        flag = None
    _flag_cache[product_id] = (time.monotonic(), flag)
    return flag


def _page(reference: str) -> Any:
    return _authed("GET", f"/pages/{reference}", text=True)


def saved_markers(page: Any) -> tuple[set[str], dict[str, bool]]:
    """Wat een pagina zegt over opgeslagen producten (favorieten): (id's uit een lijst `saved`, vlag per product).

    De pagina van de Favorieten-tab bevat meer producten dan alleen je favorieten; het veld `saved` markeert welke
    echt zijn opgeslagen. Dat kan een lijst met product-id's zijn, of een waar/onwaar-vlag bij elk product.
    """
    ids: set[str] = set()
    for node in _walk(page):
        saved = node.get("saved")
        if isinstance(saved, list):
            ids |= {str(item) for item in saved if isinstance(item, (str, int)) and not isinstance(item, bool)}
        elif isinstance(saved, dict):
            ids |= {str(key) for key, value in saved.items() if value}
    flags: dict[str, bool] = {}
    for node in _walk(page):
        if node.get("type") != "PML":
            continue
        units = [d for d in _walk(node) if d.get("type") == "SELLING_UNIT_TILE" and isinstance(d.get("sellingUnit"), dict)]
        if len(units) != 1:
            continue  # een onderdeel met meer producten zegt niets over één van hen
        flag = next((d["saved"] for d in _walk(node) if isinstance(d.get("saved"), bool)), None)
        if flag is not None:
            flags[str(units[0]["sellingUnit"].get("id"))] = flag
    return ids, flags


def mark_favorites(products: list[dict[str, Any]], page: Any) -> list[dict[str, Any]]:
    """Zet bij elk product `favorite`: True, False, of None als de pagina daar niets over zegt."""
    ids, flags = saved_markers(page)
    for product in products:
        if flags.get(product["id"]) is True or product["id"] in ids:
            product["favorite"] = True
        else:
            product["favorite"] = False if (product["id"] in flags or ids) else None
    return products


def only_favorites(products: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    """(de favorieten, of er echt gefilterd is). Weet de pagina niet welke producten favoriet zijn, dan komen alle producten terug."""
    if any(p.get("favorite") is not None for p in products):
        return [p for p in products if p.get("favorite")], True
    return products, False


def _page_products(reference: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """De pagina en zijn producten, elk met de markering `favorite` (zie `mark_favorites`).

    Staan er op de pagina zelf geen producten, dan volgen we de pagina's waar hij naar verwijst (de inhoud van
    een tab zit bij Picnic vaak op een aparte pagina, zoals bij de bewaarde recepten).
    """
    page = _page(reference)
    if not isinstance(page, dict):
        raise RuntimeError(tr("Picnic gaf de favorieten in een onbekend formaat", "Picnic returned the favorites in an unknown format"))
    products = mark_favorites(products_from_page(page), page)
    followed: list[str] = []
    for linked in dict.fromkeys(_LINKED_PAGE.findall(json.dumps(page))):
        if products or len(followed) >= MAX_LINKED_PAGES:
            break
        if linked == reference or not _PAGE_REFERENCE.match(linked):
            continue
        followed.append(linked)
        sub_page = _page(linked)
        if isinstance(sub_page, dict):
            page, products = sub_page, mark_favorites(products_from_page(sub_page), sub_page)
    return page, products


def _favorite_candidates() -> dict[str, Any]:
    """Wat de Favorieten-tab toont: ofwel de favorieten zelf (de pagina markeert ze), ofwel de producten die we nog moeten controleren."""
    cached = _candidates_cache.get("value")
    if cached and time.monotonic() - _candidates_cache["at"] < CANDIDATES_CACHE_SECONDS:
        return cached
    reference = favorites_reference(bootstrap_tabs())
    if not reference:
        raise RuntimeError(
            tr(
                "Picnic heeft geen Favorieten-tab in de app gevonden",
                "Picnic has no Favorites tab in the app",
            )
        )
    result: dict[str, Any] = {"saved": None, "candidates": [], "total": 0}
    for candidate in dict.fromkeys([reference, *FAVORITE_FALLBACKS]):
        try:
            page, products = _page_products(candidate)
        except Exception:  # noqa: BLE001 - alleen de pagina van de tab zelf moet er zijn
            if candidate == reference:
                raise
            continue
        if not products:
            continue
        favorites, filtered = only_favorites(products)
        if filtered:
            result = {"saved": favorites, "candidates": [], "total": len(products)}
        else:
            result = {"saved": None, "candidates": favorites_section(page, products)[:MAX_FAVORITE_CHECKS], "total": len(products)}
        break
    _candidates_cache.update(value=result, at=time.monotonic())
    return result


def get_favorites(budget: int | None = None, refresh: bool = False) -> dict[str, Any]:
    """De favoriete producten van de gebruiker, uit de Favorieten-tab van de app.

    Die tab laat ook andere producten zien (bij een account zonder boodschappen is het een lijst met voorgestelde
    producten onder de kop *Favorieten* en een aantal gangpaden). Markeert de pagina zelf welke producten
    opgeslagen zijn (`saved_markers`), dan gebruiken we dat. Anders kijken we bij de producten onder de kop
    *Favorieten* naar de hartjescode van hun productpagina: daar zet Picnic of het product favoriet is.
    Weten we het dan nog niet, dan geven we geen producten terug: alle producten van de pagina zijn niet 'je favorieten'.

    Dat controleren gebeurt lui: per aanroep hooguit `budget` producten die we nog niet kenden (zonder budget
    alle, binnen een tijdsgrens); wat we al wisten komt uit het geheugen. `pending` zegt hoeveel producten nog
    gecontroleerd moeten worden: roep opnieuw aan tot dat 0 is. `refresh` vergeet eerst alles.
    Geeft de favorieten tot nu toe, hoeveel producten de pagina in totaal toonde en of we het konden vaststellen (`filtered`).
    """
    if refresh:
        clear_favorites_cache()
    found = _favorite_candidates()
    if found["saved"] is not None:
        return {"products": found["saved"], "total": found["total"], "filtered": True, "pending": 0}
    started = time.monotonic()
    fresh = 0
    pending = 0
    known = False
    favorites: list[dict[str, Any]] = []
    for product in found["candidates"]:
        asked_before = _cached_flag(product["id"]) is not _UNSET
        if not asked_before and ((budget is not None and fresh >= budget) or time.monotonic() - started > FAVORITE_CHECK_SECONDS):
            pending += 1
            continue
        flag = _checked_flag(product["id"])
        fresh += 0 if asked_before else 1
        if flag is None:
            continue
        known = True
        if flag:
            favorites.append({**product, "favorite": True})
    return {"products": favorites, "total": found["total"], "filtered": known or pending > 0, "pending": pending}


def search_products(query: str, limit: int = 5) -> list[dict[str, Any]]:
    term = str(query or "").strip()
    if not term:
        return []
    page = _authed("GET", f"/pages/search-page-results?search_term={quote(term)}")
    return products_from_search_page(page)[: max(1, limit)]


def search_product(query: str) -> dict[str, Any] | None:
    found = search_products(query, 1)
    return found[0] if found else None


def candidate(product: dict[str, Any], quantity: Any, unit: Any) -> dict[str, Any]:
    """Zelfde vorm als de kandidaten die de AH-controlelijst toont."""
    packs, doubt = assess_packs(quantity, unit, product.get("unitSize"))
    if isinstance(product.get("maxCount"), int) and product["maxCount"] > 0:
        packs = min(packs, product["maxCount"])
    return {
        "productId": product["id"],
        "title": product["title"],
        "brand": product.get("brand"),
        "unitSize": product.get("unitSize"),
        "price": product.get("price"),
        "image": product.get("image"),
        "isBonus": bool(product.get("isBonus")),
        "deal": deals.candidate_deal("picnic", product),
        "url": None,  # Picnic heeft geen webwinkel waar een product op staat
        "packs": packs,
        "doubt": doubt,
    }


# --- mandje en leveringen -------------------------------------------------------------------


def _line_quantity(line: dict[str, Any]) -> int:
    for article in line.get("items") or []:
        for decorator in (article or {}).get("decorators") or []:
            if isinstance(decorator, dict) and decorator.get("type") == "QUANTITY":
                return max(1, int(decorator.get("quantity") or 1))
    return 1


def _cents(value: Any) -> int | float | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _line_prices(line: dict[str, Any]) -> tuple[float | None, float | None]:
    """(prijs die je betaalt, prijs vóór korting of None) van een regel in het mandje.

    Picnic geeft twee bedragen per regel: `display_price` en `price`. Bij een aanbieding of
    lidmaatschapskorting verschilt het ene van het andere; het lagere is wat je betaalt, het hogere de
    prijs zonder korting. Zo blijven de regels optellen tot het totaal, welk veld Picnic ook gebruikt.
    """
    amounts = [c for c in (_cents(line.get("display_price")), _cents(line.get("price"))) if c is not None]
    if not amounts:
        return None, None
    paid, listed = min(amounts), max(amounts)
    return _euros(paid), _euros(listed) if listed > paid else None


def _availability(cart: dict[str, Any]) -> dict[str, bool]:
    """Product-id -> is het nu leverbaar, uit de analysegegevens die Picnic bij het mandje meegeeft."""
    context = cart.get("analytics_context_data") if isinstance(cart.get("analytics_context_data"), dict) else {}
    return {
        str(entry["product_id"]): bool(entry.get("is_available"))
        for entry in context.get("items_list") or []
        if isinstance(entry, dict) and entry.get("product_id") and "is_available" in entry
    }


def cart_summary(cart: Any) -> dict[str, Any]:
    """Het Picnic-mandje in een compacte vorm."""
    cart = cart if isinstance(cart, dict) else {}
    available = _availability(cart)
    items = []
    for line in cart.get("items") or []:
        if not isinstance(line, dict):
            continue
        article = next((a for a in line.get("items") or [] if isinstance(a, dict)), {})
        image_ids = article.get("image_ids") or []
        paid, listed = _line_prices(line)
        max_count = article.get("max_count")
        items.append(
            {
                "id": article.get("id"),
                "title": article.get("name"),
                "quantity": _line_quantity(line),
                "unitSize": article.get("unit_quantity"),
                "price": paid,
                "listPrice": listed,
                "available": available.get(str(article.get("id"))),
                "maxCount": max_count if isinstance(max_count, int) and max_count > 0 else None,
                "image": image_url(image_ids[0]) if image_ids else None,
            }
        )
    return {
        "items": items,
        "totalCount": cart.get("total_count"),
        "totalPrice": _euros(cart.get("total_price")),
        # Wat je echt betaalt (met bezorgkosten en statiegeld) en wat het lidmaatschap bespaart.
        "checkoutTotal": _euros(cart.get("checkout_total_price")),
        "savings": _euros(cart.get("membership_savings")),
    }


def get_cart() -> dict[str, Any]:
    return cart_summary(_authed("GET", "/cart"))


def slots_from_cart(cart: Any) -> list[dict[str, Any]]:
    """De bezorgmomenten uit het mandje (Picnic zet ze in `delivery_slots`), op tijd gesorteerd."""
    cart = cart if isinstance(cart, dict) else {}
    chosen = cart.get("selected_slot") if isinstance(cart.get("selected_slot"), dict) else {}
    slots = []
    for slot in cart.get("delivery_slots") or []:
        if not isinstance(slot, dict) or not slot.get("slot_id") or not slot.get("window_start"):
            continue
        slots.append(
            {
                "id": str(slot["slot_id"]),
                "windowStart": slot.get("window_start"),
                "windowEnd": slot.get("window_end"),
                "cutOff": slot.get("cut_off_time"),
                "available": bool(slot.get("is_available")),
                "reserved": bool(slot.get("reserved")),
                "selected": bool(slot.get("selected")) or slot.get("slot_id") == chosen.get("slot_id"),
                "minimumOrder": _euros(slot.get("minimum_order_value")),
            }
        )
    slots.sort(key=lambda slot: str(slot["windowStart"]))
    return slots


def get_delivery_slots(limit: int = 12, available_only: bool = True) -> list[dict[str, Any]]:
    """De eerstvolgende bezorgmomenten. Alleen lezen: een moment kiezen doe je in de Picnic-app."""
    slots = slots_from_cart(_authed("GET", "/cart"))
    if available_only:
        slots = [slot for slot in slots if slot["available"]]
    return slots[: max(1, limit)]


def add_to_cart(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Zet producten in het Picnic-winkelmandje. Plaatst nooit een bestelling.

    Elk product is één verzoek. Mislukt er een, dan staan de eerdere al in het mandje; dat
    melden we per product terug in plaats van alles af te breken.
    """
    added: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    cart: Any = None
    for item in items:
        product_id = str(item.get("productId") or "").strip()
        if not product_id:
            continue
        count = normalize_quantity(item.get("quantity"))
        try:
            cart = _authed("POST", "/cart/add_product", body={"product_id": product_id, "count": count})
            added.append({"productId": product_id, "quantity": count})
        except PicnicAuthError:
            raise  # de koppeling zelf is stuk: elke volgende aanroep faalt ook
        except Exception as exc:  # noqa: BLE001 - de fout hoort bij dit product
            failed.append({"productId": product_id, "error": str(exc)})
    return {"added": added, "failed": failed, "cart": cart_summary(cart) if cart else None}


def _product_id(value: Any) -> str:
    product_id = str(value or "").strip()
    if not product_id:
        raise ValueError(tr("Geen product opgegeven", "No product given"))
    return product_id


def _quantity_in(cart: dict[str, Any], product_id: str) -> int:
    return next((item["quantity"] for item in cart["items"] if item["id"] == product_id), 0)


def set_quantity(product_id: Any, quantity: Any) -> dict[str, Any]:
    """Zet een product op een exact aantal (0 haalt het helemaal weg).

    Picnic kent alleen toevoegen en verwijderen, dus we kijken eerst hoeveel er nu in het mandje zit
    en voegen het verschil toe of halen het weg.
    """
    product_id = _product_id(product_id)
    if isinstance(quantity, bool) or (isinstance(quantity, float) and not quantity.is_integer()):
        raise ValueError(tr("Het aantal moet een heel getal zijn", "The quantity must be a whole number"))
    try:
        target = int(quantity)
    except (TypeError, ValueError):
        raise ValueError(tr("Het aantal moet een heel getal zijn", "The quantity must be a whole number")) from None
    if not 0 <= target <= MAX_QUANTITY:
        raise ValueError(
            tr(f"Het aantal moet tussen 0 en {MAX_QUANTITY} liggen", f"The quantity must be between 0 and {MAX_QUANTITY}")
        )
    cart = get_cart()
    limit = next((item["maxCount"] for item in cart["items"] if item["id"] == product_id), None)
    if limit and target > limit:
        raise ValueError(
            tr(
                f"Picnic laat maximaal {limit} van dit product per bestelling toe",
                f"Picnic allows at most {limit} of this product per order",
            )
        )
    current = _quantity_in(cart, product_id)
    if target > current:
        return _authed_summary("/cart/add_product", product_id, target - current)
    if target < current:
        return _authed_summary("/cart/remove_product", product_id, current - target)
    return cart


def _authed_summary(path: str, product_id: str, count: int) -> dict[str, Any]:
    return cart_summary(_authed("POST", path, body={"product_id": product_id, "count": count}))


def clear_cart() -> dict[str, Any]:
    """Maak het Picnic-mandje leeg. Plaatst nooit een bestelling.

    Eerst Picnics eigen route; weigert die, dan halen we elk product met zijn eigen aantal weg.
    """
    before = get_cart()
    if not before["items"]:
        return {"removed": 0, "cart": before}
    try:
        cart = cart_summary(_authed("POST", "/cart/clear"))
    except PicnicAuthError:
        raise
    except Exception:  # noqa: BLE001 - de route kan bij Picnic anders heten; val terug op per product
        cart = before
        for item in before["items"]:
            if item["id"]:
                cart = _authed_summary("/cart/remove_product", str(item["id"]), item["quantity"])
    return {"removed": sum(item["quantity"] for item in before["items"]), "cart": cart}


# --- pagina's -----------------------------------------------------------------------------------
# Picnic levert recepten (en andere schermen) als pagina's: een boom van onderdelen.


def analytics_data(node: Any) -> list[dict[str, Any]]:
    """De analysegegevens (`analytics.contexts[].data`) van een onderdeel van een pagina."""
    analytics = node.get("analytics") if isinstance(node, dict) and isinstance(node.get("analytics"), dict) else {}
    return [c["data"] for c in analytics.get("contexts") or [] if isinstance(c, dict) and isinstance(c.get("data"), dict)]


def _delivery_summary(delivery: dict[str, Any]) -> dict[str, Any]:
    slot = delivery.get("slot") if isinstance(delivery.get("slot"), dict) else {}
    eta = delivery.get("eta2") if isinstance(delivery.get("eta2"), dict) else {}
    delivered = delivery.get("delivery_time") if isinstance(delivery.get("delivery_time"), dict) else {}
    orders = [o for o in delivery.get("orders") or [] if isinstance(o, dict)]
    return {
        "id": delivery.get("delivery_id") or delivery.get("id"),
        "status": delivery.get("status"),
        "windowStart": slot.get("window_start"),
        "windowEnd": slot.get("window_end"),
        "etaStart": eta.get("start"),
        "etaEnd": eta.get("end"),
        "deliveredStart": delivered.get("start"),
        "deliveredEnd": delivered.get("end"),
        "total": _euros(sum(o.get("checkout_total_price") or o.get("total_price") or 0 for o in orders)),
    }


def get_deliveries(current_only: bool = False) -> list[dict[str, Any]]:
    """Leveringen van het account, de nieuwste eerst; alleen de lopende als `current_only` waar is."""
    data = _authed("POST", "/deliveries/summary", body=["CURRENT"] if current_only else [])
    deliveries = [_delivery_summary(d) for d in data or [] if isinstance(d, dict)]
    return sorted(deliveries, key=lambda d: str(d.get("windowStart") or d.get("deliveredStart") or ""), reverse=True)


_DELIVERY_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


def get_delivery(delivery_id: Any) -> dict[str, Any]:
    """Eén levering met de producten die erin zaten: naam, inhoud, aantal en prijs per product."""
    clean = str(delivery_id or "").strip()
    if not _DELIVERY_ID.match(clean):
        raise ValueError(tr("Ongeldige bestelling", "Invalid order"))
    data = _authed("GET", f"/deliveries/{clean}")
    if not isinstance(data, dict):
        raise RuntimeError(tr("Picnic gaf de bestelling in een onbekend formaat", "Picnic returned the order in an unknown format"))
    items: list[dict[str, Any]] = []
    for order in data.get("orders") or []:
        if isinstance(order, dict):
            items += cart_summary(order)["items"]
    for item in items:
        item.pop("available", None)  # of het nu leverbaar is, zegt een oude bestelling niet
        item.pop("maxCount", None)
    return {**_delivery_summary(data), "items": items, "itemCount": sum(item["quantity"] for item in items)}
