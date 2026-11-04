import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from os import environ
from typing import Any, Callable, TypeVar
from urllib.parse import unquote, urlencode, urlparse

import requests

from . import auth, deals
from .i18n import tr
from .packs import (  # noqa: F401 - blijft hier bereikbaar voor bestaande aanroepers
    LARGE_PACK_COUNT,
    MAX_CART_QUANTITY,
    assess_packs,
    normalize_quantity,
    packs_needed,
    parse_pack_size,
)

AH_API_BASE = "https://api.ah.nl"
AH_LOGIN_BASE = "https://login.ah.nl"
AH_CLIENT_ID = "appie-ios"
AH_CLIENT_VERSION = "9.28"
AH_MOBILE_USER_AGENT = "Appie/9.28 (iPhone17,3; iPhone; CPU OS 26_1 like Mac OS X)"
AH_AUTH_HEADERS = {
    "User-Agent": AH_MOBILE_USER_AGENT,
    "x-client-name": AH_CLIENT_ID,
    "x-client-version": AH_CLIENT_VERSION,
    "x-application": "AHWEBSHOP",
    "Accept": "application/json",
    "Content-Type": "application/json",
}
AH_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Receptzoeken: de oude REST-route (recipe/search/v2) bestaat niet meer. De zoekinvoer kent
# searchText, size en start (de offset); `page`, `offset` en `pageSize` bestaan niet.
RECIPE_SEARCH_QUERY = """
  query RecipeSearch($query: RecipeSearchParams!) {
    recipeSearch(query: $query) {
      page {
        total
        hasNextPage
      }
      result {
        id
        title
        slug
        time {
          cook
          oven
          wait
        }
        serving {
          number
        }
        images {
          url
          width
        }
        courses
      }
    }
  }
"""
# AH geeft een fout als start + size boven 2000 uitkomt; zo ver kun je bladeren.
RECIPE_SEARCH_WINDOW = 2000
RECIPE_CARD_IMAGE_WIDTH = 440

FAVORITE_LISTS_QUERY = """
  query FavoriteLists($ids: [String!]!) {
    favoriteListV2(ids: $ids) {
      id
      description
      totalSize
      updatedAt
    }
  }
"""

COOKBOOK_RECIPES_QUERY = """
  query SavedRecipes {
    cookBookRecipesCollectedMember {
      result {
        id
        title
      }
      page {
        total
        hasNextPage
      }
    }
  }
"""

# "Bewaarde recepten" in de AH-app zijn recepten in de receptencollectie van het lid
# (categorieen met recepten). Het type van `recipes` staat niet in de schema-snapshot,
# dus probeer eerst met en dan zonder selectie.
RECIPE_COLLECTIONS_QUERIES = (
    """
  query SavedRecipeCollections {
    recipeCollectionCategories {
      id
      name
      isDefault
      recipes {
        id
      }
    }
  }
""",
    """
  query SavedRecipeCollections {
    recipeCollectionCategories {
      id
      name
      isDefault
      recipes
    }
  }
""",
)

RECIPE_SUMMARIES_QUERIES = (
    """
  query RecipeSummaries($ids: [Int!]!) {
    recipeSummaries(ids: $ids) {
      id
      title
      slug
      images {
        url
      }
    }
  }
""",
    """
  query RecipeSummaries($ids: [Int!]!) {
    recipeSummaries(ids: $ids) {
      id
      title
      slug
    }
  }
""",
)

# Receptdetails voor het importeren in Mealie. De laatste variant bevat alleen velden
# waarvan bekend is dat ze werken; de eerdere voegen portie en afbeelding toe en
# proberen beide vormen van `preparation.steps`.
RECIPE_DETAIL_QUERIES = tuple(
    """
  query Recipe($id: Int!) {
    recipe(id: $id) {
      id
      title
      description
      cookTime
      %s
      ingredients {
        text
      }
      preparation {
        steps%s
      }
    }
  }
""" % (extra, steps)
    for extra, steps in (
        ("ovenTime\n      waitTime\n      servings { number }\n      images { url }", " { text }"),
        ("ovenTime\n      waitTime\n      servings { number }\n      images { url }", ""),
        ("", ""),
    )
)

FAVORITE_LIST_QUERY = """
  query FavoriteListV2($ids: [String!]!) {
    favoriteListV2(ids: $ids) {
      id
      description
      totalSize
      items {
        id
        productId
        quantity
      }
    }
  }
"""


_CODE_PARAM_RE = re.compile(r"(?:^|[?&#\s'\"(])code=([^&\s'\"<>)]+)")


def extract_oauth_code(value: str) -> str:
    """Haal de OAuth code uit een geplakte URL, console-regel of kale code.

    Browsers tonen de ``appie://login-exit?code=...`` redirect alleen als
    foutmelding in de console, dus de geplakte tekst bevat vaak ruis eromheen
    (aanhalingstekens, "Failed to launch ..." enzovoort).
    """
    candidate = str(value or "").strip()
    match = _CODE_PARAM_RE.search(candidate)
    if match:
        return unquote(match.group(1)).strip()
    return candidate.strip("'\"` ")


def login_url(redirect_uri: str = "appie://login-exit") -> str:
    params = {
        "client_id": AH_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": redirect_uri,
    }
    return f"{AH_LOGIN_BASE}/login?{urlencode(params)}"


def proxied_login_url(proxy_base_url: str) -> str:
    params = {
        "client_id": AH_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": "appie://login-exit",
    }
    return f"{proxy_base_url.rstrip('/')}/login?{urlencode(params)}"


def sanitize_login_cookie(cookie: str, path: str = "/api/ah/auth/proxy") -> str:
    parts = str(cookie or "").split(";")
    result = parts[:1]
    for part in parts[1:]:
        attr = part.strip()
        lower = attr.lower()
        if (
            lower == "secure"
            or lower.startswith("samesite")
            or lower.startswith("domain")
            or lower.startswith("path")
        ):
            continue
        result.append(part)
    result.append(f" Path={path}")
    return ";".join(result)


def rewrite_login_location(location: str, proxy_base_url: str) -> str:
    value = str(location or "")
    proxy_base = proxy_base_url.rstrip("/")
    if value.startswith("appie://"):
        parsed = urlparse(value)
        return f"{proxy_base}/callback?{parsed.query}"
    if value.startswith(AH_LOGIN_BASE):
        return value.replace(AH_LOGIN_BASE, proxy_base, 1)
    if value.startswith("/"):
        return f"{proxy_base}{value}"
    return value


def rewrite_login_body(body: bytes, proxy_base_url: str) -> bytes:
    proxy_base = proxy_base_url.rstrip("/").encode("utf-8")
    proxy_base_escaped = proxy_base.replace(b"/", b"\\/")
    rewritten = body.replace(b"appie://login-exit", proxy_base + b"/callback")
    rewritten = rewritten.replace(AH_LOGIN_BASE.encode("utf-8"), proxy_base)
    rewritten = rewritten.replace(b"https:\\/\\/login.ah.nl", proxy_base_escaped)
    rewritten = rewritten.replace(b"//login.ah.nl", proxy_base)
    for attr in (b"href", b"src", b"action", b"formaction"):
        rewritten = rewritten.replace(attr + b'="/', attr + b'="' + proxy_base + b"/")
        rewritten = rewritten.replace(attr + b"='/", attr + b"='" + proxy_base + b"/")
    rewritten = rewritten.replace(b'":"/', b'":"' + proxy_base + b"/")
    rewritten = rewritten.replace(b'":"\\/', b'":"' + proxy_base_escaped + b"\\/")
    rewritten = rewritten.replace(b'fetch("/', b'fetch("' + proxy_base + b"/")
    rewritten = rewritten.replace(b"fetch('/", b"fetch('" + proxy_base + b"/")
    rewritten = rewritten.replace(b"url(/", b"url(" + proxy_base + b"/")
    return rewritten


@dataclass
class TokenCache:
    token: str
    expires_at: float


anon_token_cache: TokenCache | None = None
user_token_cache: TokenCache | None = None

# AH geeft een anoniem token dat zeven dagen geldig heet, maar trekt het soms eerder in. Dan antwoordt elke aanroep met een 400
# `invalid_grant` ("member not active") en bleven de bonus en het zoeken tot een week lang mislukken. Daarom bewaren we het hooguit
# een uur, en vergeten we een token zodra AH het weigert (zie `TokenRejected` en `_anonymous`).
ANON_TOKEN_MAX_AGE = 3600
_REJECTED_MARKERS = ("invalid_grant", "member not active", "invalid_token")

T = TypeVar("T")


class TokenRejected(RuntimeError):
    """AH weigert het token: ingetrokken of verlopen voordat het zou verlopen. Een nieuw token lost het meestal op."""


def _token_rejected(status: int, body: str) -> bool:
    text = str(body or "").lower()
    return status == 401 or (status in (400, 403) and any(marker in text for marker in _REJECTED_MARKERS))


def forget_token(token: str | None) -> None:
    """Vergeet een bewaard token dat AH weigerde, zodat de volgende aanroep een nieuw haalt (anoniem of met de koppeling)."""
    global anon_token_cache, user_token_cache
    if not token:
        return
    if anon_token_cache and anon_token_cache.token == token:
        anon_token_cache = None
    if user_token_cache and user_token_cache.token == token:
        user_token_cache = None


def _bearer(headers: Any) -> str | None:
    value = str((headers or {}).get("Authorization") or "")
    return value[len("Bearer "):] if value.startswith("Bearer ") else None


def _body(response: Any) -> str:
    """De tekst van een antwoord (ingekort), of niets als die er niet is."""
    text = getattr(response, "text", "")
    return text[:500] if isinstance(text, str) else ""


def _reject_if_token(status: int, body: str, token: str | None, message: str) -> None:
    """Geeft `TokenRejected` (en vergeet het token) als het antwoord zegt dat AH het token weigert."""
    if _token_rejected(status, body):
        forget_token(token)
        raise TokenRejected(message)


def _anonymous(call: Callable[[str], T]) -> T:
    """`call(token)` met het anonieme token. Weigert AH dat token, dan één keer opnieuw met een vers token."""
    token = get_token()
    try:
        return call(token)
    except TokenRejected:
        forget_token(token)
        return call(get_token())


def _auth_headers(token: str | None = None, *, include_content_type: bool = True) -> dict[str, str]:
    headers = dict(AH_AUTH_HEADERS)
    if not include_content_type:
        headers.pop("Content-Type", None)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _request_json(method: str, url: str, **kwargs: Any) -> Any:
    response = requests.request(method, url, timeout=30, **kwargs)
    if not response.ok:
        body = response.text[:500]
        _reject_if_token(response.status_code, body, _bearer(kwargs.get("headers")), f"{response.status_code}: {body}")
        raise AhHttpError(f"{response.status_code}: {body}", response.status_code)
    return response.json()


def get_token() -> str:
    global anon_token_cache

    if anon_token_cache and time.time() < anon_token_cache.expires_at:
        return anon_token_cache.token

    data = _request_json(
        "POST",
        f"{AH_API_BASE}/mobile-auth/v1/auth/token/anonymous",
        headers=_auth_headers(),
        json={"clientId": AH_CLIENT_ID},
    )
    anon_token_cache = TokenCache(
        token=data["access_token"],
        expires_at=time.time() + min(ANON_TOKEN_MAX_AGE, max(0, int(data.get("expires_in", 3600)) - 60)),
    )
    return anon_token_cache.token


def get_user_token() -> str:
    global user_token_cache

    if user_token_cache and time.time() < user_token_cache.expires_at:
        return user_token_cache.token

    refresh_token = configured_refresh_token()
    if not refresh_token:
        raise RuntimeError(
            tr(
                "AH account niet gekoppeld. De beheerder koppelt het bij Instellingen > Koppelen > Albert Heijn.",
                "AH account not linked. The admin links it under Settings > Link > Albert Heijn.",
            )
        )

    data = _request_json(
        "POST",
        f"{AH_API_BASE}/mobile-auth/v1/auth/token/refresh",
        headers=_auth_headers(),
        json={"clientId": AH_CLIENT_ID, "refreshToken": refresh_token},
    )
    _store_rotated_refresh_token(refresh_token, data.get("refresh_token"))
    user_token_cache = TokenCache(
        token=data["access_token"],
        expires_at=time.time() + max(0, int(data.get("expires_in", 3600)) - 60),
    )
    return user_token_cache.token


def configured_refresh_token() -> str:
    # De opgeslagen token gaat voor: AH kan de refresh token bij elke vernieuwing
    # vervangen, en die nieuwe waarde bewaren we in de database. De omgevings-
    # variabele is alleen de startwaarde.
    return auth.get_secret("AH_REFRESH_TOKEN") or environ.get("AH_REFRESH_TOKEN", "").strip()


def _store_rotated_refresh_token(used: str, new: Any) -> None:
    new_token = str(new or "").strip()
    if new_token and new_token != used:
        save_refresh_token(new_token)


def save_refresh_token(refresh_token: str) -> None:
    token = str(refresh_token or "").strip()
    if not token:
        raise RuntimeError("Refresh token ontbreekt")
    auth.set_secret("AH_REFRESH_TOKEN", token)


def link_account(value: str) -> dict[str, Any]:
    """Koppel een AH account vanuit een OAuth code (of appie:// URL) of een refresh token.

    De gebruiker kan beide plakken in hetzelfde veld; we raden niet op lengte maar
    proberen de meest waarschijnlijke interpretatie en vallen terug op de andere.
    """
    text = str(value or "").strip()
    if not text:
        return {"connected": False, "error": "Geen code of token opgegeven"}

    errors: list[str] = []

    def try_code(raw: str) -> dict[str, Any] | None:
        try:
            exchange_and_store_oauth_code(extract_oauth_code(raw))
            return {"connected": True, "type": "code"}
        except Exception as exc:  # noqa: BLE001
            errors.append(f"code: {exc}")
            return None

    def try_refresh(raw: str) -> dict[str, Any] | None:
        result = verify_token(raw)
        if result.get("ok"):
            if result.get("type") == "refresh":
                save_refresh_token(result.get("refreshToken") or raw)
                return {"connected": True, "type": "refresh"}
            errors.append(
                "dit is een access token (verloopt snel); plak de refresh token"
            )
            return None
        errors.append(f"token: {result.get('error')}")
        return None

    looks_like_code = "code=" in text or text.startswith("appie://")
    order = (try_code, try_refresh) if looks_like_code else (try_refresh, try_code)
    for attempt in order:
        outcome = attempt(text)
        if outcome:
            return outcome

    return {
        "connected": False,
        "error": "; ".join(e for e in errors if e) or "Koppelen mislukt",
    }


def auth_status(verify: bool = False) -> dict[str, Any]:
    token = configured_refresh_token()
    if not token:
        return {"connected": False}
    if not verify:
        return {"connected": True, "verified": False}
    result = verify_token(token)
    if result.get("ok") and result.get("type") == "refresh":
        _store_rotated_refresh_token(token, result.get("refreshToken"))
    return {
        "connected": bool(result.get("ok")),
        "verified": True,
        "type": result.get("type"),
        "error": result.get("error"),
    }


def _explain_exchange_error(error: str) -> str:
    status = error.split(":", 1)[0].strip()
    lowered = error.lower()
    if status == "403" or "access denied" in lowered:
        return (
            "AH weigert het verzoek (403). Probeer het later opnieuw; "
            "blijft dit gebeuren, dan blokkeert AH deze server."
        )
    if status in {"400", "401"}:
        return (
            "AH wijst de code af: hij is verlopen of al gebruikt. Log opnieuw in "
            "bij AH en plak de nieuwe code direct."
        )
    return error


def exchange_oauth_code(code: str, redirect_uri: str | None = None) -> dict[str, str]:
    payload: dict[str, str] = {"clientId": AH_CLIENT_ID, "code": extract_oauth_code(code)}
    if redirect_uri:
        payload["redirect_uri"] = redirect_uri

    try:
        data = _request_json(
            "POST",
            f"{AH_API_BASE}/mobile-auth/v1/auth/token",
            headers=_auth_headers(),
            json=payload,
        )
    except RuntimeError as exc:
        raise RuntimeError(_explain_exchange_error(str(exc))) from exc
    global user_token_cache
    user_token_cache = TokenCache(
        token=data["access_token"],
        expires_at=time.time() + max(0, int(data.get("expires_in", 3600)) - 60),
    )
    return {"accessToken": data["access_token"], "refreshToken": data["refresh_token"]}


def exchange_and_store_oauth_code(code: str, redirect_uri: str | None = None) -> dict[str, str]:
    tokens = exchange_oauth_code(code, redirect_uri)
    save_refresh_token(tokens["refreshToken"])
    return tokens


def verify_token(token: str) -> dict[str, Any]:
    refresh_response = requests.post(
        f"{AH_API_BASE}/mobile-auth/v1/auth/token/refresh",
        headers=_auth_headers(),
        json={"clientId": AH_CLIENT_ID, "refreshToken": token},
        timeout=30,
    )
    if refresh_response.ok:
        try:
            rotated = (refresh_response.json() or {}).get("refresh_token")
        except ValueError:
            rotated = None
        return {"ok": True, "type": "refresh", "refreshToken": rotated or token}

    access_response = requests.get(
        f"{AH_API_BASE}/mobile-services/member/v2/profile",
        headers=_auth_headers(token, include_content_type=False),
        timeout=30,
    )
    if access_response.ok:
        return {"ok": True, "type": "access"}

    return {
        "ok": False,
        "error": f"Token werkt niet ({refresh_response.status_code}): {refresh_response.text[:200]}",
    }


def _pick_image(images: list[dict[str, Any]], target: int = RECIPE_CARD_IMAGE_WIDTH) -> str:
    """Kies uit de formaten van AH de afbeelding die het dichtst bij de gewenste breedte ligt."""
    candidates = [image for image in images or [] if isinstance(image, dict) and image.get("url")]
    if not candidates:
        return ""
    best = min(candidates, key=lambda image: abs(int(image.get("width") or 0) - target))
    return best["url"]


# De productfoto in het groot (het venster bij de controlelijst van het mandje): rond 400 css-pixels breed, dus 800
# beeldpunten voor een scherm met dubbele dichtheid.
PRODUCT_PHOTO_WIDTH = 800


def product_photo(images: list[dict[str, Any]]) -> str | None:
    """De productfoto in het groot: het formaat van AH dat het dichtst bij PRODUCT_PHOTO_WIDTH ligt."""
    return _pick_image(images, PRODUCT_PHOTO_WIDTH) or None


def _recipe_card(item: dict[str, Any]) -> dict[str, Any]:
    recipe_id = item.get("id")
    slug = str(item.get("slug") or "").strip()
    times = item.get("time") or {}
    total_time = sum(int(times.get(key) or 0) for key in ("cook", "oven", "wait"))
    image = _pick_image(item.get("images") or [])
    return {
        "id": str(recipe_id),
        "title": item.get("title") or f"Recept {recipe_id}",
        "webPath": f"/allerhande/recept/R-R{recipe_id}" + (f"/{slug}" if slug else ""),
        "images": [{"url": image}] if image else [],
        "totalTime": total_time,
        "servings": (item.get("serving") or {}).get("number"),
        "courses": item.get("courses") or [],
    }


def search_recipes(query: str, page: int = 0, size: int = 12) -> dict[str, Any]:
    """Allerhande-recepten zoeken via GraphQL (anoniem token)."""
    size = max(1, int(size))
    start = max(0, int(page)) * size
    if start >= RECIPE_SEARCH_WINDOW:
        raise RuntimeError(
            f"AH laat niet verder bladeren dan {RECIPE_SEARCH_WINDOW:,} recepten. Verfijn je zoekopdracht.".replace(",", ".")
        )
    size = min(size, RECIPE_SEARCH_WINDOW - start)  # de laatste pagina wordt afgekapt in plaats van een fout

    data = _anonymous(lambda token: _graphql(
        token,
        RECIPE_SEARCH_QUERY,
        {"query": {"searchText": str(query), "size": size, "start": start}},
        "AH zoeken",
    ))
    found = data.get("recipeSearch") or {}
    recipes = [_recipe_card(item) for item in found.get("result") or [] if item.get("id") is not None]
    total = int((found.get("page") or {}).get("total") or 0)
    reachable = min(total, RECIPE_SEARCH_WINDOW)
    return {
        "recipes": recipes,
        "total": total,
        "hasNextPage": start + len(recipes) < reachable,
    }


def _search_product_dict(item: dict[str, Any], query: str) -> dict[str, Any]:
    unit_size = item.get("salesUnitSize")
    price_now = item.get("currentPrice") or item.get("priceBeforeBonus") or 0
    product = {
        "webshopId": str(item.get("webshopId") or item.get("id")),
        "title": item.get("title") or query,
        "price": {"now": price_now, "unitSize": unit_size},
        "images": item.get("images") or [],
        "brand": item.get("brand"),
        "unitSize": unit_size,
        "isBonus": bool(item.get("isBonus")),
        # AH's eigen kenmerk; "AH Biologisch Spaghetti" heeft ["vegan", "biologisch"] (#218).
        "organic": "biologisch" in [str(icon).casefold() for icon in item.get("propertyIcons") or [] if isinstance(icon, str)],
        # AH's hoofdcategorie ("Zuivel, eieren"): de controlelijst vergelijkt alleen binnen dezelfde (#223).
        "category": item.get("mainCategory") or None,
    }
    if product["isBonus"]:
        product["promo"] = _bonus_promo(item)
    return product


# AH geeft een doorlopende bonus (zoals "5% volume voordeel") als einddatum 2999-12-31. Dat is geen einddatum.
OPEN_ENDED_YEAR = 2900


def _bonus_end(item: dict[str, Any]) -> str | None:
    """De laatste dag van een bonus, of None als die doorloopt of onbekend is."""
    end = item.get("bonusEndDate") or None
    if not end or item.get("isInfiniteBonus"):
        return None
    text = str(end)
    return None if text[:4].isdigit() and int(text[:4]) >= OPEN_ENDED_YEAR else text


def _bonus_promo(item: dict[str, Any]) -> dict[str, Any]:
    """Wat AH over de bonus meegeeft: mechaniek, gewone prijs en looptijd.

    De veldnamen (`bonusMechanism`, `bonusStartDate`, `bonusEndDate`, `discountLabels`) zijn niet gedocumenteerd
    en zijn nog niet met een echte respons gecontroleerd; ontbreekt een veld, dan blijft de waarde leeg en
    wordt de aanbieding als onbekend getoond in plaats van met een gokwaarde.
    """
    labels = [
        str(label.get("defaultDescription") or label.get("description"))
        for label in item.get("discountLabels") or []
        if isinstance(label, dict) and (label.get("defaultDescription") or label.get("description"))
    ]
    return {
        "label": " | ".join(labels) or None,
        "mechanism": item.get("bonusMechanism") or None,
        "priceBefore": item.get("priceBeforeBonus") or None,
        "validFrom": item.get("bonusStartDate") or None,
        "validUntil": _bonus_end(item),
    }


def _get_with_retry(url: str, attempts: int = 3, **kwargs: Any) -> requests.Response:
    """GET die een verbroken verbinding opnieuw probeert. AH verbreekt er af en toe een, vooral bij
    veel zoekopdrachten kort na elkaar (het weekmenu zoekt elk ingrediënt apart). Alleen voor lezen."""
    for attempt in range(1, attempts + 1):
        try:
            return requests.get(url, **kwargs)
        except (requests.ConnectionError, requests.Timeout):
            if attempt == attempts:
                raise
            time.sleep(0.5 * attempt)
    raise AssertionError("onbereikbaar")  # pragma: no cover


# Hoeveel producten we AH per zoekopdracht vragen. AH's eigen volgorde zet soms iets bovenaan dat niet over het
# ingrediënt gaat: bij "AH Penne" kwam een "Guacamole seasoning mix 3-pack" (door AH onder Penne ingedeeld) eerst
# en stond "AH Penne" zelf pas op plek 9 (issue #175). Daarom vragen we er meer en zetten we ze zelf op volgorde.
SEARCH_WINDOW = 30

_WORD_RE = re.compile(r"[^\W_]+")


def _words(value: Any) -> list[str]:
    """De woorden van een tekst in kleine letters en zonder accenten: "Crème fraîche" -> ["creme", "fraiche"]."""
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    return _WORD_RE.findall("".join(ch for ch in text if not unicodedata.combining(ch)))


def _is_exact(query_words: list[str], item: dict[str, Any]) -> bool:
    """Heet het product precies zoals de zoekterm, ook met AH's eigen merk ervoor? "penne" is "AH Penne", "spaghetti" is
    "AH Biologisch Spaghetti". Alleen het eigen merk: "Pringles Paprika" is geen paprika."""
    title = _words(item.get("title"))
    brand = _words(item.get("brand"))
    own_brand = brand[:1] == ["ah"] and title[: len(brand)] == brand
    name = title[len(brand):] if own_brand else title
    return bool(query_words) and query_words in (title, name)


def _names_ingredient(ingredient: list[str], item: dict[str, Any]) -> bool:
    """Noemt de naam van het product het ingrediënt? Elk woord ervan staat in de titel, ook als deel van een woord, zoals bij
    `prices._same_kind`: bij "eieren" wel "AH Witte vrije uitloopeieren M", niet "AH Nederlandse aardbeien"."""
    title = _words(item.get("title"))
    return bool(ingredient) and all(any(word in other for other in title) for word in ingredient)


# AH's hoofdcategorieën voor vers vlees en voor vleeswaren (plakjes voor op brood); zie `rank_search_results` (#226).
FRESH_MEAT = "Vlees"
COLD_CUTS = "Vleeswaren"


def rank_search_results(query: str, products: list[Any]) -> list[dict[str, Any]]:
    """AH's zoekresultaten met de beste match voor een ingrediënt bovenaan.

    0. Vers vlees voor vleeswaren, als een los product uit AH's categorie *Vlees* het ingrediënt noemt (#226). Een recept
       vraagt rauwe kipfilet, maar AH zet bij "kipfilet" vleeswaren bovenaan: "AH Scharrel kipfilet naturel" (150 g) als
       eerste losse product, en "AH Biologisch Kipfilet" heet precies "kipfilet". De rauwe "AH Scharrel kipfilet 2 stuks"
       stond pas op plek 11. Noemt geen los stuk vers vlees het ingrediënt, zoals bij ham en rosbief, dan verandert er niets.
    1. Een product dat precies zo heet als de zoekterm (`_is_exact`).
    2. Een los product voor een multipack (`isVirtualBundle`, "AH Zalmfilet 3-pack"). Zo'n pakket stelt AH online
       samen uit losse producten; voor een recept wil je er één, en het aantal verpakkingen rekenen we zelf uit.
    3. Zette AH bovenaan, vóór het eerste losse product, een multipack van het ingrediënt (`_names_ingredient`), dan gaan
       binnen de losse producten (en binnen de multipacks) de producten die het ingrediënt noemen voor. Anders kwam bovenaan
       wat AH na die multipacks had, ook als dat iets anders is: AH zoekt ruim, zette bij "eieren" "AH Biologisch Eieren S M L
       4-pack" bovenaan en daarna "AH Nederlandse aardbeien", en de eerste losse doos eieren stond zo pas op plek 6 (#220).
    4. Verder de volgorde van AH. Buiten geval 3 telt het bewust niet of de zoekwoorden in de titel staan: "Milner Jong
       belegen" is kaas zonder "kaas" in de naam, en "Kruiden roomkaas" zou er dan voor komen.
    """
    items = [item for item in products if isinstance(item, dict)]
    query_words = _words(query)
    ingredient = query_words[1:] if query_words[:1] == ["ah"] else query_words  # "AH Penne" is penne
    first_single = next((index for index, item in enumerate(items) if not item.get("isVirtualBundle")), len(items))
    ingredient_on_top = any(_names_ingredient(ingredient, item) for item in items[:first_single])
    fresh_first = any(
        item.get("mainCategory") == FRESH_MEAT and not item.get("isVirtualBundle") and _names_ingredient(ingredient, item)
        for item in items
    )

    def rank(pair: tuple[int, dict[str, Any]]) -> tuple[bool, bool, bool, bool, int]:
        position, item = pair
        return (
            fresh_first and item.get("mainCategory") == COLD_CUTS,
            not _is_exact(query_words, item),
            bool(item.get("isVirtualBundle")),
            ingredient_on_top and not _names_ingredient(ingredient, item),
            position,
        )

    return [item for _, item in sorted(enumerate(items), key=rank)]


class AhHttpError(RuntimeError):
    """Een fout van AH met de HTTP-status erbij, zodat de aanroeper bij 403 (firewall) of 429 (te veel verzoeken) kan stoppen."""

    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.status = status


def _search_json(query: str, size: int) -> Any:
    """De ruwe zoekresultaten van AH (anoniem)."""

    def search(token: str) -> Any:
        response = _get_with_retry(
            f"{AH_API_BASE}/mobile-services/product/search/v2",
            headers=_auth_headers(token, include_content_type=False),
            params={"query": query, "page": 0, "size": size, "sortOn": "RELEVANCE"},
            timeout=30,
        )
        if not response.ok:
            message = f"AH productzoekopdracht mislukt ({response.status_code})"
            _reject_if_token(response.status_code, _body(response), token, message)
            raise AhHttpError(message, response.status_code)
        return response.json()

    return _anonymous(search)


def search_products(query: str, limit: int = 5) -> list[dict[str, Any]]:
    limit = max(1, int(limit))
    products = rank_search_results(query, _search_json(query, max(limit, SEARCH_WINDOW)).get("products") or [])
    return [_search_product_dict(item, query) for item in products[:limit]]


# Een bonregel heet kort en algemeen ("BROCCOLI", "KIPFILET"); het product staat dan vaak pas op plek 30 tot 45 van de resultaten.
HQ_SEARCH_WINDOW = 100


def search_hq_ids(query: str) -> list[tuple[int, int, str, str]]:
    """(hqId, webshopId, titel, foto) van de zoekresultaten. Het id op een kassabon is AH's `hqId` (artikelnummer), niet het id van
    de webwinkel: "BLOEMKOOL" op de bon heeft 20960, "AH Bloemkool" in de webwinkel 4183 (issue #178). Een `hqId` van 0 is onbekend.
    De foto is het adres van de eerste afbeelding (zoals `get_products_by_ids`), of "" zonder foto."""
    found = []
    for item in _search_json(query, HQ_SEARCH_WINDOW).get("products") or []:
        if not isinstance(item, dict):
            continue
        hq_id, webshop_id = item.get("hqId"), item.get("webshopId") or item.get("id")
        if isinstance(hq_id, int) and hq_id > 0 and isinstance(webshop_id, int) and webshop_id > 0:
            image = ((item.get("images") or [{}])[0] or {}).get("url") if isinstance(item.get("images"), list) else None
            found.append((hq_id, webshop_id, str(item.get("title") or "").strip(), str(image or "")))
    return found


def search_product(query: str) -> dict[str, Any] | None:
    products = search_products(query, 1)
    return products[0] if products else None


def product_web_url(product_id: Any) -> str:
    try:
        return f"https://www.ah.nl/producten/product/wi{int(product_id)}"
    except (TypeError, ValueError):
        return ""


def get_products_by_ids(product_ids: list[int]) -> list[dict[str, Any]]:
    if not product_ids:
        return []
    params: list[tuple[str, str]] = [("ids", str(product_id)) for product_id in product_ids]
    params.append(("sortOn", "INPUT_PRODUCT_IDS"))

    def fetch(token: str) -> Any:
        response = _get_with_retry(
            f"{AH_API_BASE}/mobile-services/product/search/v2/products",
            headers=_auth_headers(token, include_content_type=False),
            params=params,
            timeout=30,
        )
        if not response.ok:
            message = f"AH producten ophalen mislukt ({response.status_code}): {response.text[:200]}"
            _reject_if_token(response.status_code, _body(response), token, message)
            raise RuntimeError(message)
        return response

    response = _anonymous(fetch)

    products = []
    for item in response.json() or []:
        products.append(
            {
                "id": item.get("webshopId"),
                "url": product_web_url(item.get("webshopId")),
                "title": item.get("title"),
                "brand": item.get("brand"),
                "unitSize": item.get("salesUnitSize"),
                "price": item.get("currentPrice") or item.get("priceBeforeBonus") or 0,
                "image": ((item.get("images") or [{}])[0]).get("url"),
                "isBonus": bool(item.get("isBonus")),
            }
        )
    return products


# --- Bonus (aanbiedingen) -----------------------------------------------------------------------
# De bonus komt uit de GraphQL van de AH-app, per bonusperiode: AH's bonusweek loopt van maandag t/m zondag, net als het
# weekmenu. Eén aanvraag geeft de periodes die AH kent (deze week en, vanaf het weekend, volgende week) en alle acties
# daarin, met hun producten, de normale prijs en de actietekst ("2e halve prijs", "2 voor 2.99"). Zo staat de bonus
# van volgende week er al compleet in terwijl deze week nog loopt. Lukt dat niet, dan valt `get_bonus_products` terug
# op de bonuspagina en daarna op zoekopdrachten met de bonusvlag (die vinden alleen een deel, en van volgende week weinig).
# Het zijn onofficiële routes die we niet kunnen garanderen; de verkenner (`ah_explore`) toont wat AH geeft.
BONUS_PROMOTIONS_QUERY = """
query BonusPromotions {
  bonusPeriods { startDate endDate weekNumber }
  bonusPromotions {
    id
    title
    periodStart
    periodEnd
    promotionType
    category
    promotionLabels { title topText centerText bottomText }
    products {
      id
      title
      brand
      salesUnitSize
      price { now { amount } was { amount } }
      imagePack { small { url } }
    }
  }
}
"""
# De acties die bij de Bonus horen: de landelijke bonus en de online bonus. INCENTIVE is bezorgkorting ("gratis bezorging bij
# 12 euro"), ETOS en GALL zijn andere winkels.
BONUS_PROMOTION_TYPES = frozenset({"NATIONAL", "AHONLINE"})
# Soorten actie met een lagere prijs per stuk, zonder minimumaantal: daar is de actieprijs uit te rekenen.
_SINGLE_UNIT_MECHANICS = (deals.PERCENT, deals.AMOUNT_OFF, deals.FIXED)


def _promotion_label(promotion: dict[str, Any]) -> str:
    """De actietekst zoals AH hem op het schildje zet: "50% korting", "2 voor 2.99", "voor 1.99", "online Bonus"."""
    labels = promotion.get("promotionLabels") or []
    first = labels[0] if labels and isinstance(labels[0], dict) else {}
    parts = (first.get(key) for key in ("title", "topText", "centerText", "bottomText"))
    return " ".join(str(part).strip() for part in parts if part and str(part).strip())


def _amount(node: Any) -> float | None:
    value = node.get("amount") if isinstance(node, dict) else None
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _promotion_products(promotion: dict[str, Any]) -> list[dict[str, Any]]:
    """De producten van een actie, in de vorm van `_bonus_product`, met de looptijd van de actie.

    AH geeft per product de normale prijs (ook voor volgende week, als de actie nog niet loopt). De actieprijs rekenen we uit de
    actietekst uit als dat zonder voorwaarde kan (25% korting, €1 korting, voor 1.99); bij een actie met een minimumaantal
    (2 voor 3, 2e halve prijs) blijft de prijs de normale prijs, zoals AH hem ook toont, en rekent `deals` de prijs per stuk uit.
    """
    label = _promotion_label(promotion)
    mechanic = deals.parse_mechanic(label)
    valid_from = promotion.get("periodStart") or None
    valid_until = _bonus_end({"bonusEndDate": promotion.get("periodEnd")})
    products = []
    for item in promotion.get("products") or []:
        if not isinstance(item, dict):
            continue
        product_id, title = item.get("id"), str(item.get("title") or "").strip()
        if not product_id or not title:
            continue
        price = item.get("price") if isinstance(item.get("price"), dict) else {}
        now, regular = _amount(price.get("now")), _amount(price.get("was"))
        regular = regular if regular is not None else now
        unit = None
        if mechanic["type"] in _SINGLE_UNIT_MECHANICS and mechanic["minQuantity"] == 1:
            unit = deals.effective_unit_price(mechanic, regular, now)
        on_offer = unit is not None and regular is not None and unit < regular
        images = item.get("imagePack") or []
        small = images[0].get("small") if images and isinstance(images[0], dict) else None
        products.append(
            {
                "id": str(product_id),
                "title": title,
                "brand": item.get("brand") or None,
                "unitSize": item.get("salesUnitSize") or None,
                "price": unit if on_offer else regular,
                "priceBefore": regular if on_offer else None,
                "promoLabel": label,
                "image": (small.get("url") if isinstance(small, dict) else None) or None,
                "url": product_web_url(product_id),
                "validFrom": valid_from,
                "validUntil": valid_until,
                "categoryHint": promotion.get("category") or None,
            }
        )
    return products


def get_bonus_promotions(today: date | None = None) -> list[dict[str, Any]]:
    """De bonus van alle bonusperiodes die AH kent, als producten met hun looptijd. Een product kan in twee periodes staan
    (deze week en volgende week, elk met een eigen actie); dan komt het twee keer terug. Werkt zonder AH-login."""
    data = _anonymous(lambda token: _graphql(token, BONUS_PROMOTIONS_QUERY, {}, "AH bonus ophalen"))
    periods = [
        (deals.parse_date(period.get("startDate")), deals.parse_date(period.get("endDate")))
        for period in data.get("bonusPeriods") or []
        if isinstance(period, dict)
    ]
    periods = [(start, end) for start, end in periods if start and end]
    today = today or date.today()
    found: dict[tuple[str, Any, Any], dict[str, Any]] = {}
    for promotion in data.get("bonusPromotions") or []:
        if not isinstance(promotion, dict) or promotion.get("promotionType") not in BONUS_PROMOTION_TYPES:
            continue
        start, end = deals.parse_date(promotion.get("periodStart")), deals.parse_date(promotion.get("periodEnd"))
        if end is not None and end < today:
            continue  # al afgelopen
        # Alleen acties in een bonusperiode die AH zelf noemt; zonder periodes houden we alles.
        if periods and not any(deals.in_week(start, end, first, last) for first, last in periods):
            continue
        for product in _promotion_products(promotion):
            found.setdefault((product["id"], product["validFrom"], product["validUntil"]), product)
    return list(found.values())


BONUS_SECTION_PATH = "/mobile-services/bonuspage/v2/section"
BONUS_FALLBACK_TERMS = (
    "kip", "gehakt", "vis", "zalm", "worst", "spek", "rundvlees", "varkensvlees", "pasta", "rijst", "aardappel",
    "groente", "tomaat", "ui", "paprika", "champignon", "salade", "fruit", "kaas", "melk", "yoghurt", "room", "ei",
    "brood", "saus", "soep", "pizza", "noten", "olie", "bonen",
)
BONUS_PAGE_SIZE = 50


def _bonus_label(item: dict[str, Any], group: str = "") -> str:
    labels = item.get("discountLabels")
    if isinstance(labels, list):
        for label in labels:
            text = label.get("defaultDescription") if isinstance(label, dict) else None
            if text:
                return str(text).strip()
    return str(item.get("bonusMechanism") or group or "").strip()


def _bonus_product(item: dict[str, Any], group: str = "") -> dict[str, Any] | None:
    product_id = item.get("webshopId") or item.get("id")
    title = str(item.get("title") or "").strip()
    if not product_id or not title:
        return None
    now = item.get("currentPrice")
    before = item.get("priceBeforeBonus")
    images = item.get("images") or []
    return {
        "id": str(product_id),
        "title": title,
        "brand": item.get("brand") or None,
        "unitSize": item.get("salesUnitSize") or None,
        "price": now if isinstance(now, (int, float)) else before if isinstance(before, (int, float)) else None,
        "priceBefore": before if isinstance(before, (int, float)) and isinstance(now, (int, float)) and before > now else None,
        "promoLabel": _bonus_label(item, group),
        "image": (images[0].get("url") if images and isinstance(images[0], dict) else None) or None,
        "url": product_web_url(product_id),
        "validFrom": item.get("bonusStartDate") or None,
        "validUntil": _bonus_end(item),
        "categoryHint": item.get("mainCategory") or item.get("subCategory") or None,
    }


def bonus_products_in(node: Any, group: str = "") -> list[dict[str, Any]]:
    """Alle producten ergens in een antwoord, met de naam van de bonusgroep waarin ze staan. Geen dubbelen."""
    found: dict[str, dict[str, Any]] = {}

    def walk(inner: Any, label: str) -> None:
        if isinstance(inner, list):
            for entry in inner:
                walk(entry, label)
        elif isinstance(inner, dict):
            product = _bonus_product(inner, label) if "webshopId" in inner and "title" in inner else None
            if product:
                found.setdefault(product["id"], product)
                return
            own = str(inner.get("discountDescription") or inner.get("segmentDescription") or inner.get("title") or label or "")
            for value in inner.values():
                walk(value, own if isinstance(value, (list, dict)) else label)

    walk(node, group)
    return list(found.values())


def _bonus_from_search(term: str, token: str) -> list[dict[str, Any]]:
    response = _get_with_retry(
        f"{AH_API_BASE}/mobile-services/product/search/v2",
        headers=_auth_headers(token, include_content_type=False),
        params={"query": term, "page": 0, "size": BONUS_PAGE_SIZE, "sortOn": "RELEVANCE"},
        timeout=30,
    )
    if not response.ok:
        message = f"AH productzoekopdracht mislukt ({response.status_code})"
        _reject_if_token(response.status_code, _body(response), token, message)
        raise RuntimeError(message)
    products = [_bonus_product(item) for item in response.json().get("products") or [] if isinstance(item, dict) and item.get("isBonus")]
    return [product for product in products if product]


def get_bonus_products() -> list[dict[str, Any]]:
    """De aanbiedingen (AH Bonus): eerst de bonus per bonusperiode (GraphQL, deze en volgende week), anders de bonuspagina,
    anders zoekopdrachten met de bonusvlag. Werkt zonder AH-login."""
    errors: list[str] = []
    try:
        promotions = get_bonus_promotions()
        if promotions:
            return promotions
    except Exception as exc:  # noqa: BLE001 - onofficiële route; val terug op de bonuspagina en zoeken
        errors.append(str(exc)[:200])
    token = get_token()
    found: dict[str, dict[str, Any]] = {}
    try:
        data = _request_json(
            "GET",
            f"{AH_API_BASE}{BONUS_SECTION_PATH}",
            headers=_auth_headers(token, include_content_type=False),
            params={"date": date.today().isoformat(), "application": "AH", "promotionType": "NATIONAL"},
        )
        for product in bonus_products_in(data):
            found.setdefault(product["id"], product)
    except Exception as exc:  # noqa: BLE001 - onofficiële route; val terug op zoeken
        errors.append(str(exc)[:200])
    if not found:
        def search(term: str) -> list[dict[str, Any]]:
            try:
                return _bonus_from_search(term, token)
            except Exception as exc:  # noqa: BLE001
                errors.append(str(exc)[:200])
                return []

        with ThreadPoolExecutor(max_workers=6) as pool:
            for products in pool.map(search, BONUS_FALLBACK_TERMS):
                for product in products:
                    found.setdefault(product["id"], product)
    if not found and errors:
        raise RuntimeError(errors[0])
    return list(found.values())


# --- Bonus Box (persoonlijke bonus) ---------------------------------------------------------------
# De Bonus Box is de persoonlijke bonus in de AH-app: elke week een stel aanbiedingen die AH op je aankopen afstemt, waarvan je er een
# aantal activeert (5, met Premium 10). Ze is vanaf vrijdag te zien en geldt van maandag t/m zondag, net als de bonus. Omdat ze bij jou
# hoort, vraagt ze de AH-koppeling (het gebruikerstoken); anoniem is er geen Bonus Box.
# Eerst de GraphQL van de app: `bonusPromotions` met `filterSet: APP_BONUS_BOX`, per bonusperiode, met dezelfde acties en producten als
# de gewone bonus en daarbij `activationStatus`. Lukt dat niet, dan de REST-route `bonuspage/v1/personal`, die de vorm van de bonuspagina
# heeft. Beide zijn onofficieel (gevonden in de community-projecten appie-go en bonbonus) en nog niet met een eigen antwoord gecontroleerd;
# de verkenner (`ah_explore`) laat zien wat AH geeft. Activeren doet MealieShopper niet: dat kan niet ongedaan worden, dus dat blijft in de app.
BONUS_PERIODS_QUERY = """
query BonusPeriods {
  bonusPeriods { startDate endDate weekNumber }
}
"""
BONUS_BOX_QUERY = """
query BonusBox($periodStart: String, $periodEnd: String, $weekNumber: Int) {
  bonusPromotions(filterSet: APP_BONUS_BOX, input: {periodStart: $periodStart, periodEnd: $periodEnd, weekNumber: $weekNumber}) {
    id
    title
    periodStart
    periodEnd
    promotionType
    category
    activationStatus
    promotionLabels { title topText centerText bottomText }
    price { now { amount } was { amount } }
    products {
      id
      title
      brand
      salesUnitSize
      price { now { amount } was { amount } }
      imagePack { small { url } }
    }
  }
}
"""
BONUS_BOX_PATH = "/mobile-services/bonuspage/v1/personal"
BONUS_BOX_ACTIVATED = "ACTIVATED"
# Soorten actie die zeker geen Bonus Box zijn. Negeert AH het filter, dan komt anders de hele bonus als persoonlijke bonus binnen.
_NOT_PERSONAL_TYPES = BONUS_PROMOTION_TYPES | {"INCENTIVE", "ETOS", "GALL"}


def _activated(status: Any) -> bool | None:
    """Of een aanbieding uit de Bonus Box geactiveerd is: True, False, of None als AH het niet zegt."""
    text = str(status or "").strip().upper()
    return (text == BONUS_BOX_ACTIVATED) if text else None


def _bonus_box_periods(token: str, today: date) -> list[tuple[str, str, int | None]]:
    """De bonusweken die nog niet voorbij zijn: (begin, einde, weeknummer). Geeft AH er geen, dan deze week (maandag t/m zondag)."""
    try:
        data = _graphql(token, BONUS_PERIODS_QUERY, {}, "AH bonusweken ophalen")
    except TokenRejected:
        raise
    except Exception:  # noqa: BLE001 - zonder de weken van AH nemen we deze week
        data = {}
    periods = []
    for period in data.get("bonusPeriods") or []:
        if not isinstance(period, dict):
            continue
        start, end = deals.parse_date(period.get("startDate")), deals.parse_date(period.get("endDate"))
        if start and end and end >= today:
            week = period.get("weekNumber")
            periods.append((start.isoformat(), end.isoformat(), week if isinstance(week, int) and not isinstance(week, bool) else None))
    if not periods:
        monday, sunday = deals.week_of(today.isoformat())
        periods.append((monday.isoformat(), sunday.isoformat(), today.isocalendar()[1]))
    return periods


def bonus_box_promotions(promotions: Any, start: str, end: str) -> list[dict[str, Any]]:
    """De producten van de acties in een Bonus Box (GraphQL), in de vorm van `_bonus_product`, met `activated`.

    Een actie zonder looptijd krijgt die van de bonusweek. Een actie zonder producten wordt één aanbieding met haar eigen titel en prijs,
    zodat ze toch in de lijst staat (zonder productpagina)."""
    found: dict[tuple[str, Any, Any], dict[str, Any]] = {}
    for promotion in promotions if isinstance(promotions, list) else []:
        if not isinstance(promotion, dict):
            continue
        if promotion.get("promotionType") in _NOT_PERSONAL_TYPES and promotion.get("activationStatus") is None:
            continue
        promotion = {**promotion, "periodStart": promotion.get("periodStart") or start, "periodEnd": promotion.get("periodEnd") or end}
        products = _promotion_products(promotion)
        if not products and promotion.get("id") and str(promotion.get("title") or "").strip():
            products = _promotion_products({**promotion, "products": [
                {"id": f"bonusbox-{promotion['id']}", "title": promotion["title"], "price": promotion.get("price")},
            ]})
        activated = _activated(promotion.get("activationStatus"))
        for product in products:
            product["activated"] = activated
            found.setdefault((product["id"], product["validFrom"], product["validUntil"]), product)
    return list(found.values())


def bonus_box_in(data: Any, start: str, end: str) -> list[dict[str, Any]]:
    """De producten in een antwoord van `bonuspage/v1/personal` (de vorm van de bonuspagina), met `activated` en de looptijd van de week
    als het product of de groep er zelf geen geeft."""
    sections = data if isinstance(data, list) else [data]
    found: dict[str, dict[str, Any]] = {}
    for section in sections:
        entries = section.get("bonusGroupOrProducts") if isinstance(section, dict) else None
        for entry in entries if isinstance(entries, list) else []:
            node = (entry.get("product") or entry.get("bonusGroup")) if isinstance(entry, dict) else None
            if not isinstance(node, dict):
                continue
            for product in bonus_products_in(entry):
                product["validFrom"] = product["validFrom"] or node.get("bonusStartDate") or start
                product["validUntil"] = product["validUntil"] or _bonus_end(node) or (None if node.get("isInfiniteBonus") else end)
                product["activated"] = _activated(node.get("activationStatus"))
                found.setdefault(product["id"], product)
    return list(found.values())


def _bonus_box_week(token: str, start: str, end: str, week: int | None) -> list[dict[str, Any]]:
    """De Bonus Box van één bonusweek: eerst GraphQL, anders de REST-route."""
    try:
        data = _graphql(token, BONUS_BOX_QUERY, {"periodStart": start, "periodEnd": end, "weekNumber": week}, "AH Bonus Box ophalen")
        return bonus_box_promotions(data.get("bonusPromotions"), start, end)
    except TokenRejected:
        raise
    except Exception as exc:  # noqa: BLE001 - onofficiële route; val terug op de REST-route
        graphql_error = exc
    try:
        data = _request_json(
            "GET", f"{AH_API_BASE}{BONUS_BOX_PATH}",
            headers=_auth_headers(token, include_content_type=False), params={"bonusStartDate": start},
        )
    except TokenRejected:
        raise
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"{str(graphql_error)[:200]}; {str(exc)[:200]}") from exc
    return bonus_box_in(data, start, end)


def get_bonus_box(today: date | None = None) -> list[dict[str, Any]]:
    """Je Bonus Box: de persoonlijke bonus van deze week en (vanaf vrijdag) volgende week, als producten met hun looptijd en `activated`.
    Vraagt de AH-koppeling: zonder koppeling een LookupError. Weigert AH het token, dan één keer opnieuw met een vers token."""
    if not configured_refresh_token():
        raise LookupError(tr("AH-account niet gekoppeld", "AH account not linked"))
    today = today or date.today()

    def fetch(token: str) -> list[dict[str, Any]]:
        found: dict[tuple[str, Any, Any], dict[str, Any]] = {}
        errors: list[str] = []
        for start, end, week in _bonus_box_periods(token, today):
            try:
                products = _bonus_box_week(token, start, end, week)
            except TokenRejected:
                raise
            except Exception as exc:  # noqa: BLE001 - een week die niet lukt laat de andere staan
                errors.append(str(exc)[:300])
                continue
            for product in products:
                found.setdefault((product["id"], product["validFrom"], product["validUntil"]), product)
        if not found and errors:
            raise RuntimeError(errors[0])
        return list(found.values())

    token = get_user_token()
    try:
        return fetch(token)
    except TokenRejected:
        forget_token(token)
        return fetch(get_user_token())


def _graphql(token: str, query: str, variables: dict[str, Any], what: str) -> dict[str, Any]:
    response = requests.post(
        f"{AH_API_BASE}/graphql",
        headers=_auth_headers(token),
        json={"query": query, "variables": variables},
        timeout=30,
    )
    if not response.ok:
        message = f"{what} mislukt ({response.status_code}): {response.text[:200]}"
        _reject_if_token(response.status_code, _body(response), token, message)
        raise AhHttpError(message, response.status_code)  # met de status, zodat een aanroeper bij 403/429 kan stoppen
    data = response.json()
    if data.get("errors"):
        raise RuntimeError(f"{what} mislukt: {data['errors'][0].get('message', 'GraphQL error')}")
    return data.get("data") or {}


def _get_favorite_lists_rest(token: str, product_id: int) -> list[dict[str, Any]]:
    # Oude REST-route; AH geeft hier inmiddels een 404 op. Alleen vangnet.
    # AH eist een productId maar geeft alle lijstjes terug; welke waarde wordt
    # geaccepteerd verschilt per AH-release, dus probeer er een paar.
    response = None
    for candidate in (max(1, int(product_id or 1)), 0):
        response = requests.get(
            f"{AH_API_BASE}/mobile-services/lists/v3/lists",
            headers=_auth_headers(token, include_content_type=False),
            params={"productId": candidate},
            timeout=30,
        )
        if response.ok or response.status_code not in {400, 404, 422}:
            break
    if not response.ok:
        raise RuntimeError(f"AH lijstjes ophalen mislukt ({response.status_code}): {response.text[:200]}")
    return [
        {
            "id": item.get("id"),
            "name": item.get("description"),
            "itemCount": item.get("itemCount") or 0,
        }
        for item in response.json() or []
    ]


def get_favorite_lists(product_id: int = 1) -> list[dict[str, Any]]:
    token = get_user_token()
    try:
        # Een lege ids-lijst geeft alle lijstjes terug.
        data = _graphql(token, FAVORITE_LISTS_QUERY, {"ids": []}, "AH lijstjes ophalen")
    except RuntimeError as graphql_error:
        try:
            return _get_favorite_lists_rest(token, product_id)
        except RuntimeError as rest_error:
            raise RuntimeError(f"{graphql_error}; REST: {rest_error}") from rest_error
    return [
        {
            "id": item.get("id"),
            "name": item.get("description"),
            "itemCount": item.get("totalSize") or 0,
            "updatedAt": item.get("updatedAt"),
        }
        for item in data.get("favoriteListV2") or []
    ]


def get_favorite_list_items(list_id: str) -> dict[str, Any]:
    token = get_user_token()
    data = _graphql(
        token,
        FAVORITE_LIST_QUERY,
        {"ids": [str(list_id).upper()]},
        "AH lijstinhoud ophalen",
    )
    lists = data.get("favoriteListV2") or []
    if not lists:
        raise RuntimeError("AH lijst niet gevonden")

    favorite_list = lists[0]
    raw_items = favorite_list.get("items") or []
    product_ids = [int(item.get("productId")) for item in raw_items if item.get("productId")]
    product_map = {int(product["id"]): product for product in get_products_by_ids(product_ids) if product.get("id")}
    items = []
    for item in raw_items:
        product_id = int(item.get("productId") or 0)
        product = product_map.get(product_id)
        items.append(
            {
                "id": item.get("id"),
                "productId": product_id,
                "url": (product or {}).get("url") or product_web_url(product_id),
                "quantity": max(1, int(item.get("quantity") or 1)),
                "product": product,
            }
        )

    return {
        "id": favorite_list.get("id"),
        "name": favorite_list.get("description"),
        "totalSize": favorite_list.get("totalSize") or len(items),
        "items": items,
    }


def _recipe_web_url(item: dict[str, Any]) -> str:
    web_path = (item.get("webPath") or item.get("href") or "").strip()
    if web_path:
        if web_path.startswith("http"):
            return web_path
        return f"https://www.ah.nl{web_path if web_path.startswith('/') else '/' + web_path}"
    recipe_id = item.get("id") or item.get("recipeId")
    if recipe_id:
        return f"https://www.ah.nl/allerhande/recept/R-R{recipe_id}"
    return ""


def _normalize_recipe(item: Any) -> dict[str, Any] | None:
    if isinstance(item, (int, str)):
        recipe_id = str(item)
        return {
            "id": recipe_id,
            "title": f"Recept {recipe_id}",
            "image": "",
            "url": f"https://www.ah.nl/allerhande/recept/R-R{recipe_id}",
        }
    if not isinstance(item, dict):
        return None

    images = item.get("images") or []
    image = ""
    if images and isinstance(images[0], dict):
        image = images[0].get("url") or ""
    elif isinstance(item.get("image"), str):
        image = item.get("image") or ""

    url = _recipe_web_url(item)
    if not url:
        return None

    return {
        "id": item.get("id") or item.get("recipeId"),
        "title": item.get("title") or item.get("name") or "Onbekend recept",
        "image": image,
        "url": url,
    }


def _fetch_recipe_details(token: str, recipe_ids: list[int]) -> list[dict[str, Any]]:
    """Hydrate recipe ids to full recipe objects. Falls back to id-only cards."""
    if not recipe_ids:
        return []
    params: list[tuple[str, str]] = [("ids", str(recipe_id)) for recipe_id in recipe_ids]
    try:
        response = requests.get(
            f"{AH_API_BASE}/mobile-services/recipes/v1/recipe/by-ids",
            headers=_auth_headers(token, include_content_type=False),
            params=params,
            timeout=20,
        )
        if response.ok:
            data = response.json()
            if isinstance(data, dict):
                data = (
                    data.get("recipes")
                    or data.get("result")
                    or data.get("items")
                    or []
                )
            details = [normalized for item in (data or []) if (normalized := _normalize_recipe(item))]
            if details:
                return details
    except (requests.exceptions.RequestException, ValueError):
        pass
    # Fallback: build minimal cards from the ids (links still import in Mealie).
    return [normalized for rid in recipe_ids if (normalized := _normalize_recipe(rid))]


def _graphql_variants(
    token: str, queries: tuple[str, ...], variables: dict[str, Any], what: str
) -> dict[str, Any]:
    """Probeer query-varianten achter elkaar; de fouten van alle varianten staan in de melding."""
    errors: list[str] = []
    for query in queries:
        try:
            return _graphql(token, query, variables, what)
        except TokenRejected:
            raise  # een ander token helpt, een andere variant van de vraag niet
        except AhHttpError as exc:
            if exc.status in (403, 429):
                raise  # firewall of te veel verzoeken: een andere variant maakt het alleen erger
            errors.append(str(exc))
        except RuntimeError as exc:
            errors.append(str(exc))
    raise RuntimeError(" | ".join(dict.fromkeys(errors)))


def _recipe_page(recipes: list[dict[str, Any]], total: int, page: int, size: int) -> dict[str, Any]:
    size = max(1, int(size))
    start = max(0, int(page)) * size
    return {"recipes": recipes[start:start + size], "total": max(total, len(recipes))}


def _category_recipe_ids(category: dict[str, Any]) -> list[int]:
    """De receptnummers van een receptgroep, in de volgorde van AH en zonder dubbelen."""
    ids: list[int] = []
    seen: set[int] = set()
    for entry in category.get("recipes") or []:
        raw = entry.get("id") if isinstance(entry, dict) else entry
        try:
            recipe_id = int(raw)
        except (TypeError, ValueError):
            continue
        if recipe_id not in seen:
            seen.add(recipe_id)
            ids.append(recipe_id)
    return ids


def _collection_categories(token: str) -> list[dict[str, Any]]:
    """De receptgroepen van het lid (zoals in de AH-app): `[{id, name, isDefault, recipeIds}]`, in de volgorde van AH."""
    data = _graphql_variants(
        token, RECIPE_COLLECTIONS_QUERIES, {}, "AH receptencollectie ophalen"
    )
    groups: list[dict[str, Any]] = []
    seen: set[str] = set()
    for category in data.get("recipeCollectionCategories") or []:
        if not isinstance(category, dict) or category.get("id") is None:
            continue
        group_id = str(category["id"])
        if group_id in seen:
            continue
        seen.add(group_id)
        name = " ".join(str(category.get("name") or "").split())
        groups.append(
            {
                "id": group_id,
                "name": name or tr(f"Groep {group_id}", f"Group {group_id}"),
                "isDefault": bool(category.get("isDefault")),
                "recipeIds": _category_recipe_ids(category),
            }
        )
    return groups


def _collection_recipe_ids(token: str) -> list[int]:
    """Alle bewaarde recepten: de receptnummers van alle groepen samen, zonder dubbelen."""
    data = _graphql_variants(
        token, RECIPE_COLLECTIONS_QUERIES, {}, "AH receptencollectie ophalen"
    )
    ids: list[int] = []
    seen: set[int] = set()
    for category in data.get("recipeCollectionCategories") or []:
        if not isinstance(category, dict):
            continue
        for recipe_id in _category_recipe_ids(category):
            if recipe_id not in seen:
                seen.add(recipe_id)
                ids.append(recipe_id)
    return ids


def _recipe_summaries(token: str, recipe_ids: list[int]) -> dict[int, dict[str, Any]]:
    data = _graphql_variants(
        token, RECIPE_SUMMARIES_QUERIES, {"ids": recipe_ids}, "AH receptdetails ophalen"
    )
    summaries: dict[int, dict[str, Any]] = {}
    for item in data.get("recipeSummaries") or []:
        try:
            recipe_id = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        images = item.get("images") or []
        image = images[0].get("url") if images and isinstance(images[0], dict) else ""
        slug = (item.get("slug") or "").strip()
        summaries[recipe_id] = {
            "id": str(recipe_id),
            "title": item.get("title") or f"Recept {recipe_id}",
            "image": image or "",
            "url": f"https://www.ah.nl/allerhande/recept/R-R{recipe_id}"
            + (f"/{slug}" if slug else ""),
        }
    return summaries


def _get_saved_recipes_collections(page: int, size: int) -> dict[str, Any]:
    """Bewaarde recepten uit de receptencollectie van het lid (GraphQL)."""
    token = get_user_token()
    ids = _collection_recipe_ids(token)
    size = max(1, int(size))
    start = max(0, int(page)) * size
    page_ids = ids[start:start + size]
    if not page_ids:
        return {"recipes": [], "total": len(ids)}
    try:
        summaries = _recipe_summaries(token, page_ids)
    except RuntimeError:
        summaries = {}
    recipes = []
    for recipe_id in page_ids:
        recipe = summaries.get(recipe_id)
        if recipe is None:
            # Zonder details blijft er een kaart met id en werkende link over.
            recipe = _normalize_recipe(recipe_id)
        if recipe:
            recipes.append(recipe)
    return {"recipes": recipes, "total": len(ids)}


def _get_saved_recipes_cookbook(page: int, size: int) -> dict[str, Any]:
    """Verzamelde recepten uit het kookboek van het lid (GraphQL)."""
    token = get_user_token()
    data = _graphql(token, COOKBOOK_RECIPES_QUERY, {}, "AH kookboek ophalen")
    collected = data.get("cookBookRecipesCollectedMember") or {}
    recipes = []
    for item in collected.get("result") or []:
        recipe_id = item.get("id")
        if recipe_id is None:
            continue
        recipes.append(
            {
                "id": str(recipe_id),
                "title": item.get("title") or f"Recept {recipe_id}",
                "image": "",
                "url": f"https://www.ah.nl/allerhande/recept/R-R{recipe_id}",
            }
        )
    total = (collected.get("page") or {}).get("total") or len(recipes)
    return _recipe_page(recipes, total, page, size)


def _get_saved_recipes_api(page: int, size: int) -> dict[str, Any]:
    """Bewaarde recepten via de OAuth-koppeling: eerst de receptencollectie, dan het kookboek."""
    errors: list[str] = []
    empty: dict[str, Any] | None = None
    for label, source in (
        ("collectie", _get_saved_recipes_collections),
        ("kookboek", _get_saved_recipes_cookbook),
    ):
        try:
            result = source(page, size)
        except RuntimeError as exc:
            errors.append(f"{label}: {exc}")
            continue
        if result["recipes"] or result["total"]:
            return result
        empty = result
    if empty is not None:
        return empty
    raise RuntimeError("; ".join(errors))


def get_saved_recipes(page: int = 0, size: int = 50) -> dict[str, Any]:
    """Bewaarde recepten uit de receptencollectie van het AH-account (GraphQL)."""
    if not configured_refresh_token():
        raise RuntimeError(
            tr(
                "AH account niet gekoppeld. De beheerder koppelt het bij Instellingen > Koppelen > Albert Heijn "
                "om bewaarde recepten op te halen.",
                "AH account not linked. The admin links it under Settings > Link > Albert Heijn "
                "to fetch saved recipes.",
            )
        )
    return _get_saved_recipes_api(page, size)


def get_saved_recipe_groups() -> list[dict[str, Any]]:
    """De receptgroepen van het AH-account met hun receptnummers: `[{id, name, isDefault, recipeIds}]`."""
    if not configured_refresh_token():
        raise RuntimeError(
            tr(
                "AH account niet gekoppeld. De beheerder koppelt het bij Instellingen > Koppelen > Albert Heijn "
                "om receptgroepen op te halen.",
                "AH account not linked. The admin links it under Settings > Link > Albert Heijn "
                "to fetch recipe groups.",
            )
        )
    return _collection_categories(get_user_token())


def add_to_shopping_list(items: list[dict[str, Any]]) -> None:
    token = get_user_token()
    # AH weigt hetzelfde product twee keer in één verzoek met een 409 ("Grocery items cannot be
    # duplicate"). Twee recepten in het weekmenu die dezelfde melk nodig hebben worden dus één regel.
    by_product: dict[int, dict[str, Any]] = {}
    for item in items:
        product_id_raw = item.get("productId")
        try:
            product_id = int(product_id_raw)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"Ongeldig productId voor AH winkelmandje: {product_id_raw}") from exc

        quantity = normalize_quantity(item.get("quantity"))
        description = str(item.get("title") or item.get("query") or "").strip()
        if product_id in by_product:
            line = by_product[product_id]
            line["quantity"] = min(MAX_CART_QUANTITY, line["quantity"] + quantity)
            if not line["description"]:
                line["description"] = line["searchTerm"] = description
            continue
        by_product[product_id] = {
            "description": description,
            "productId": product_id,
            "quantity": quantity,
            "type": "SHOPPABLE",
            "originCode": "PRD",
            "searchTerm": description,
            "strikeThrough": False,
        }
    shopping_items = list(by_product.values())

    if not shopping_items:
        raise RuntimeError("Geen geldige producten om toe te voegen")

    payload = {"items": shopping_items}
    response = requests.patch(
        f"{AH_API_BASE}/mobile-services/shoppinglist/v2/items",
        headers=_auth_headers(token),
        json=payload,
        timeout=30,
    )
    if response.status_code == 412 and "order mode" in response.text.lower():
        # Er staat een bestelling open: dan weigert AH de boodschappenlijst. Zeg dat in gewone woorden.
        raise RuntimeError(
            tr(
                "Er staat een bestelling open bij AH, bijvoorbeeld op een ander apparaat (AH-app of website). "
                "Sluit die bestelling eerst af en probeer het daarna opnieuw (412, Server in order mode).",
                "An order is open at AH, for example on another device (AH app or website). "
                "Close that order first and try again (412, Server in order mode).",
            )
        )
    if not response.ok:
        raise RuntimeError(f"AH winkelmandje vullen mislukt ({response.status_code}): {response.text}")


# --- Bestellingen, het bestelmandje en kassabonnen (alleen lezen) ---------------------------------
# Onofficiële routes van de AH-app, zoals de community-bibliotheek appie-go ze gebruikt. Het
# bestelmandje is de actieve bestelling bij AH, iets anders dan de boodschappenlijst waar
# add_to_shopping_list naartoe schrijft. De antwoorden worden hier tot platte dicts herleid;
# ontbrekende velden worden leeg in plaats van een fout.


def _number(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _get_json(path: str, params: dict[str, Any] | None = None) -> Any:
    return _request_json(
        "GET",
        f"{AH_API_BASE}{path}",
        headers=_auth_headers(get_user_token(), include_content_type=False),
        params=params,
    )


def _order_item(entry: dict[str, Any], taxonomy: str = "") -> dict[str, Any]:
    product = entry.get("product") or {}
    images = product.get("images") or []
    # In de bestelling is `amount` een aantal (een geheel getal), geen bedrag: het bedrag van de regel is aantal x prijs.
    # Alleen een kommagetal zonder bekende prijs behandelen we als bedrag.
    quantity = _int(entry.get("quantity")) or _int(entry.get("amount")) or 0
    price = _number(product.get("currentPrice") if product.get("currentPrice") is not None else product.get("priceBeforeBonus"))
    return {
        "productId": _int(product.get("webshopId") or product.get("id")),
        "title": _text(product.get("title")),
        "brand": _text(product.get("brand")),
        "unitSize": _text(product.get("salesUnitSize")),
        "quantity": quantity,
        "amount": round(quantity * price, 2) if price is not None else (_number(entry.get("amount")) if isinstance(entry.get("amount"), float) else None),
        "price": price,
        "priceBeforeBonus": _number(product.get("priceBeforeBonus")),
        "bonus": bool(product.get("isBonus")),
        "taxonomy": _text(taxonomy),
        "image": images[0].get("url") if images and isinstance(images[0], dict) else "",
    }


def get_shopping_list() -> dict[str, Any]:
    """Het AH-mandje zoals de app het toont: de boodschappenlijst waar `add_to_shopping_list` in schrijft.

    Het antwoord is `{items: [{quantity, strikedthrough, type, productDetails: {product: {...}}}]}`. Een vrije tekstregel heeft
    geen product maar een `description`. Afgevinkte regels (`strikedthrough`) laten we weg. De lezing is ruim: ook een kale
    lijst, `product` direct in de regel en de schrijfwijze `strikeThrough` worden begrepen.
    """
    data = _get_json("/mobile-services/shoppinglist/v2/items") or {}
    entries = data if isinstance(data, list) else data.get("items") or data.get("products") or []
    items = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("strikedthrough") or entry.get("strikeThrough"):
            continue
        details = entry.get("productDetails") if isinstance(entry.get("productDetails"), dict) else {}
        product = details.get("product") if isinstance(details.get("product"), dict) else entry.get("product")
        product = product if isinstance(product, dict) else {}
        title = _text(product.get("title") or entry.get("description") or entry.get("searchTerm"))
        if not title:
            continue
        quantity = _int(entry.get("quantity")) or 1
        item = _order_item({"quantity": quantity, "product": {**product, "webshopId": product.get("webshopId") or entry.get("productId"), "title": title}})
        items.append({**item, "quantity": quantity})
    return {"id": None, "state": "", "shoppingType": "", "total": None, "discount": None, "deliveryDate": "", "slotStart": "", "slotEnd": "", "items": items}


def get_active_order() -> dict[str, Any]:
    """Het bestelmandje: de actieve bestelling bij AH (producten, totaal, gekozen bezorgmoment)."""
    try:
        data = _get_json("/mobile-services/order/v1/summaries/active", {"sortBy": "DEFAULT"}) or {}
    except RuntimeError as exc:
        # Zonder actieve bestelling antwoordt AH met een 404: dan is het mandje gewoon leeg.
        if not str(exc).startswith("404") or "Order does not exist" not in str(exc):
            raise
        data = {}
    total = data.get("totalPrice") or {}
    delivery = data.get("deliveryInformation") or {}
    return {
        "id": _int(data.get("id")),
        "state": _text(data.get("state")),
        "shoppingType": _text(data.get("shoppingType")),
        "total": _number(total.get("priceTotalPayable")),
        "discount": _number(total.get("priceDiscount")),
        "deliveryDate": _text(delivery.get("deliveryDate")),
        "slotStart": _text(delivery.get("deliveryStartTime")),
        "slotEnd": _text(delivery.get("deliveryEndTime")),
        "items": [_order_item(entry) for entry in data.get("orderedProducts") or [] if isinstance(entry, dict)],
    }


def get_order_details(order_id: int) -> dict[str, Any]:
    """Een bestelling met zijn producten, per categorie (werkt voor elke bestelling-id)."""
    data = _get_json(f"/mobile-services/order/v1/{int(order_id)}/details-grouped-by-taxonomy") or {}
    period = data.get("deliveryTimePeriod") or {}
    items = []
    for group in data.get("groupedProductsInTaxonomy") or []:
        for entry in group.get("orderedProducts") or []:
            if isinstance(entry, dict):
                items.append(_order_item(entry, group.get("taxonomyName") or ""))
    return {
        "id": _int(data.get("orderId")) or int(order_id),
        "state": _text(data.get("orderState")),
        "deliveryType": _text(data.get("deliveryType")),
        "deliveryDate": _text(data.get("deliveryDate")),
        "slotStart": _text(period.get("startDateTime")),
        "slotEnd": _text(period.get("endDateTime")),
        "items": items,
    }


FULFILLMENTS_QUERY = """
query OrderFulfillments {
  orderFulfillments(status: OPEN) {
    result {
      orderId statusCode statusDescription shoppingType transactionCompleted modifiable
      totalPrice { totalPrice { amount } }
      delivery { status method slot { date dateDisplay timeDisplay startTime endTime } }
    }
  }
}
"""


def get_open_fulfillments() -> list[dict[str, Any]]:
    """Geplaatste bestellingen die nog niet zijn geleverd."""
    return _fulfillments("OPEN")


def get_closed_fulfillments(offset: int = 0) -> list[dict[str, Any]]:
    """Afgeronde bestellingen, nieuwste eerst: afgeleverd of geannuleerd (`deliveryStatus`). AH geeft er per keer 10; `offset` bladert verder."""
    return _fulfillments("CLOSED", offset)


def _fulfillments(status: str, offset: int = 0) -> list[dict[str, Any]]:
    arguments = f"status: {status}" + (f", offset: {int(offset)}" if offset else "")
    data = _graphql(get_user_token(), FULFILLMENTS_QUERY.replace("status: OPEN", arguments), {}, "AH bestellingen ophalen")
    result = ((data.get("orderFulfillments") or {}).get("result")) or []
    orders = []
    for entry in result:
        if not isinstance(entry, dict) or entry.get("orderId") is None:
            continue
        delivery = entry.get("delivery") or {}
        slot = delivery.get("slot") or {}
        orders.append(
            {
                "id": _int(entry.get("orderId")),
                "state": _text(entry.get("statusCode") or delivery.get("status")),
                "stateText": _text(entry.get("statusDescription")),
                "shoppingType": _text(entry.get("shoppingType")),
                "method": _text(delivery.get("method")),
                "deliveryStatus": _text(delivery.get("status")),
                "modifiable": bool(entry.get("modifiable")),
                "total": _number(((entry.get("totalPrice") or {}).get("totalPrice") or {}).get("amount")),
                "deliveryDate": _text(slot.get("date")),
                "slotStart": _text(slot.get("startTime")),
                "slotEnd": _text(slot.get("endTime")),
            }
        )
    return orders


RECEIPTS_QUERY = """
query FetchPosReceipts($offset: Int!, $limit: Int!) {
  posReceiptsPage(pagination: {offset: $offset, limit: $limit}) {
    posReceipts { id dateTime totalAmount { amount } }
  }
}
"""

RECEIPT_QUERY = """
query FetchReceipt($id: String!) {
  posReceiptDetails(id: $id) {
    id
    products { id quantity name price { amount } amount { amount } }
    discounts { name amount { amount } }
    payments { method amount { amount } }
  }
}
"""


def get_receipts(offset: int = 0, limit: int = 100) -> list[dict[str, Any]]:
    """Kassabonnen van aankopen in de winkel, nieuwste eerst (een pagina)."""
    data = _graphql(get_user_token(), RECEIPTS_QUERY, {"offset": int(offset), "limit": int(limit)}, "AH kassabonnen ophalen")
    page = ((data.get("posReceiptsPage") or {}).get("posReceipts")) or []
    return [
        {
            "id": _text(entry.get("id")),
            "dateTime": _text(entry.get("dateTime")),
            "total": _number((entry.get("totalAmount") or {}).get("amount")),
        }
        for entry in page
        if isinstance(entry, dict) and entry.get("id")
    ]


def get_receipt(receipt_id: str) -> dict[str, Any]:
    """Eén kassabon met regels, kortingen en betalingen."""
    data = _graphql(get_user_token(), RECEIPT_QUERY, {"id": str(receipt_id)}, "AH kassabon ophalen")
    detail = data.get("posReceiptDetails") or {}
    return {
        "id": _text(detail.get("id")) or str(receipt_id),
        "items": [
            {
                "productId": _int(line.get("id")),
                "name": _text(line.get("name")),
                "quantity": _int(line.get("quantity")) or 0,
                "price": _number((line.get("price") or {}).get("amount")),
                "amount": _number((line.get("amount") or {}).get("amount")),
            }
            for line in detail.get("products") or []
            if isinstance(line, dict)
        ],
        "discounts": [
            {"name": _text(entry.get("name")), "amount": _number((entry.get("amount") or {}).get("amount"))}
            for entry in detail.get("discounts") or []
            if isinstance(entry, dict)
        ],
        "payments": [
            {"method": _text(entry.get("method")), "amount": _number((entry.get("amount") or {}).get("amount"))}
            for entry in detail.get("payments") or []
            if isinstance(entry, dict)
        ],
    }


_ALLERHANDE_RECIPE_RE = re.compile(r"ah\.nl/allerhande/recept/R-R(\d+)", re.IGNORECASE)


def allerhande_recipe_id(url: str) -> int | None:
    match = _ALLERHANDE_RECIPE_RE.search(str(url or ""))
    return int(match.group(1)) if match else None


def _iso_minutes(minutes: Any) -> str | None:
    try:
        value = int(minutes)
    except (TypeError, ValueError):
        return None
    return f"PT{value}M" if value > 0 else None


class RecipeUnavailable(RuntimeError):
    """AH kent het recept niet (meer), of het heeft geen ingrediënten en geen bereiding: opnieuw proberen helpt niet."""


def get_recipe_jsonld(recipe_id: int) -> dict[str, Any]:
    """Allerhande-recept als schema.org Recipe (JSON-LD), via de AH-API zonder paginascraping."""
    data = _anonymous(lambda token: _graphql_variants(token, RECIPE_DETAIL_QUERIES, {"id": int(recipe_id)}, "AH recept ophalen"))
    recipe = data.get("recipe")
    if not recipe:
        raise RecipeUnavailable("AH recept niet gevonden")

    ingredients = [
        str(item.get("text")).strip()
        for item in recipe.get("ingredients") or []
        if isinstance(item, dict) and str(item.get("text") or "").strip()
    ]
    steps = []
    for step in (recipe.get("preparation") or {}).get("steps") or []:
        text = step.get("text") if isinstance(step, dict) else step
        if str(text or "").strip():
            steps.append({"@type": "HowToStep", "text": str(text).strip()})
    if not ingredients and not steps:
        raise RecipeUnavailable("AH recept heeft geen ingredienten of bereiding")

    times = [recipe.get(key) or 0 for key in ("cookTime", "ovenTime", "waitTime")]
    images = [
        image.get("url")
        for image in recipe.get("images") or []
        if isinstance(image, dict) and image.get("url")
    ]
    servings = (recipe.get("servings") or {}).get("number")

    ld: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "Recipe",
        "name": recipe.get("title") or f"Recept {recipe_id}",
        "url": f"https://www.ah.nl/allerhande/recept/R-R{recipe_id}",
        "recipeIngredient": ingredients,
        "recipeInstructions": steps,
    }
    if recipe.get("description"):
        ld["description"] = recipe["description"]
    if images:
        ld["image"] = images
    if servings:
        ld["recipeYield"] = f"{servings} personen"
    if iso := _iso_minutes(recipe.get("cookTime")):
        ld["cookTime"] = iso
    if iso := _iso_minutes(sum(int(t) for t in times if isinstance(t, (int, float)))):
        ld["totalTime"] = iso
    return ld


def recipe_url(recipe_id: int) -> str:
    """De pagina van een Allerhande-recept; Mealie bewaart die als bron."""
    return f"https://www.ah.nl/allerhande/recept/R-R{int(recipe_id)}"


def recipe_detail(recipe_id: int) -> dict[str, Any]:
    """Een Allerhande-recept in de vorm van het detailscherm (zoals Jumbo, Picnic en de maaltijdboxen)."""
    ld = get_recipe_jsonld(recipe_id)
    images = ld.get("image") or []
    portions = re.match(r"\s*(\d+)", str(ld.get("recipeYield") or ""))
    minutes = re.fullmatch(r"PT(\d+)M", str(ld.get("totalTime") or ""))
    return {
        "id": str(int(recipe_id)),
        "name": ld.get("name") or "",
        "description": ld.get("description") or "",
        "image": images[0] if images else None,
        "portions": int(portions.group(1)) if portions else None,
        "minutes": int(minutes.group(1)) if minutes else None,
        "url": ld.get("url") or recipe_url(recipe_id),
        "ingredients": [{"line": line} for line in ld.get("recipeIngredient") or []],
        "steps": [step["text"] for step in ld.get("recipeInstructions") or [] if isinstance(step, dict) and step.get("text")],
        "nutrition": [],
    }
