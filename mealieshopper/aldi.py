"""Aldi: de aanbiedingen van de week, zonder account en zonder mandje.

Aldi Nederland verkoopt geen boodschappen online. De aanbiedingen staan op de openbare pagina `aanbiedingen.html`, die met
Next.js is gebouwd: in het blok `__NEXT_DATA__` staat onder `apiData` (een JSON-tekst) het antwoord `OFFER_GET`, met per
product de naam, het merk, de verkoopeenheid, de prijs, de oude prijs, het label ("OP=OP", "-15%", "2 VOOR") en de looptijd,
plus de groepen van de folder ("Vanaf maandag", "Vanaf woensdag") met hun data. `robots.txt` staat die pagina toe. Zie
docs/onderzoek-supermarkten.md.

- Een actieperiode komt uit `promotionPrices` (met de datums in Nederlandse tijd erbij). Zonder die lijst telt `currentPrice`.
  Heeft die geen label en geen oude prijs, dan alleen binnen de periode van de groep: rond de wissel van de week staat daar soms
  al de gewone prijs van na de actie.
- "2 VOOR" met een prijs van 3,00 is een prijs voor twee: dat wordt 1,50 per stuk met het label "2 voor 3,00", dat
  `deals.parse_mechanic` leest als x voor y.
- Wat alleen in een groep met non-food staat (speelgoed, kleding, kerstverlichting), laten we weg.
- Een lege lijst is een fout (`AldiError`): dan blijft de vorige kopie staan, zoals bij de andere winkels.
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any
from urllib.parse import urlsplit

import httpx

from . import about, deals, http_client
from .i18n import tr

SITE = "https://www.aldi.nl"
HOST = "www.aldi.nl"
OFFERS_URL = f"{SITE}/aanbiedingen.html"
PRODUCT_URL = SITE + "/product/{slug}.html"
IMAGE_PREFIX = "https://s7g10.scene7.com/is/image/aldinord/"
IMAGE_SIZE = "?wid=500&hei=500"  # het origineel is ongeveer 260 kB, zo ongeveer 50 kB
TIMEOUT = 30

_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_X_FOR = re.compile(r"^(\d+)\s*voor$", re.I)
_PER_UNIT = re.compile(r"^per\s+(kg|kilo|liter|l)$", re.I)
# Groepen en categorieën van de folder met de Aldi-aankopen die geen boodschappen zijn (speelgoed, kleding, elektronica). Huishouden en
# drogisterij blijven: die hebben bij de andere winkels ook een eigen categorie. Niet het losse woord "kerst": dan viel ook kersteten weg.
_NON_FOOD = re.compile(
    r"speelgoed|spellen|puzzel|bouwset|actie-avontuur|kleding|schoen|textiel|wintersport|elektronica|verlichting|"
    r"versiering|slaapkamer|verwarm|kaars|bloemen|planten|tuin|klus|gereedschap|fiets|auto\b",
    re.I,
)


class AldiError(RuntimeError):
    """Een fout met een tekst van onszelf (nooit het antwoord van Aldi), die de gebruiker mag zien."""


def _user_agent() -> str:
    return f"MealieShopper/{about.version()} (+https://github.com/helmerzNL/MealieShopper)"


async def _page() -> str:
    """De HTML van de aanbiedingenpagina."""
    try:
        response = await http_client.client("aldi").get(
            OFFERS_URL,
            headers={"User-Agent": _user_agent(), "Accept-Language": "nl-NL,nl;q=0.9"},
            timeout=TIMEOUT,
            follow_redirects=True,
        )
    except httpx.HTTPError as exc:
        raise AldiError(tr("Aldi is niet bereikbaar", "Aldi is not reachable")) from exc
    if (urlsplit(str(response.url)).hostname or "").lower() != HOST:
        raise AldiError(tr("Aldi stuurde ons ergens anders heen", "Aldi redirected us elsewhere"))
    if response.status_code in (403, 429):
        raise AldiError(tr(f"Aldi weigerde het verzoek (status {response.status_code})", f"Aldi refused the request (status {response.status_code})"))
    if response.status_code != 200:
        raise AldiError(tr(f"Aldi antwoordde met status {response.status_code}", f"Aldi answered with status {response.status_code}"))
    return response.text


# --- de pagina lezen ------------------------------------------------------------------------------


def _unknown_format() -> AldiError:
    return AldiError(tr("Aldi gaf de pagina in een onbekend formaat", "Aldi returned the page in an unknown format"))


def _find_api_data(node: Any, depth: int = 0) -> Any:
    """De waarde van de eerste sleutel `apiData` in de gegevens van de pagina (nu onder `props.pageProps`)."""
    if depth > 12:
        return None
    if isinstance(node, dict):
        if "apiData" in node:
            return node["apiData"]
        children: Any = node.values()
    elif isinstance(node, list):
        children = node
    else:
        return None
    for child in children:
        found = _find_api_data(child, depth + 1)
        if found is not None:
            return found
    return None


def _offer_data(text: str) -> dict[str, Any]:
    """Het antwoord `OFFER_GET` uit de pagina: `algoliaDataMap` (de producten) en `categories` (de groepen van de folder)."""
    found = _NEXT_DATA.search(text or "")
    try:
        api = _find_api_data(json.loads(found.group(1))) if found else None
        if isinstance(api, str):
            api = json.loads(api)
    except ValueError:
        api = None
    if not isinstance(api, list):
        raise _unknown_format()
    fallback: dict[str, Any] | None = None
    for entry in api:
        if not (isinstance(entry, list) and len(entry) == 2 and isinstance(entry[1], dict)):
            continue
        res = entry[1].get("res")
        if not isinstance(res, dict) or not isinstance(res.get("algoliaDataMap"), dict):
            continue
        if entry[0] == "OFFER_GET":
            return res
        fallback = fallback or res
    if fallback is None:
        raise _unknown_format()
    return fallback


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if value > 0 else None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _groups(categories: Any) -> tuple[dict[str, tuple[str, str]], dict[str, list[str]]]:
    """Per product de periode van zijn groepen (vroegste begin, laatste einde) en de titels van die groepen."""
    windows: dict[str, tuple[str, str]] = {}
    titles: dict[str, list[str]] = {}
    for category in categories if isinstance(categories, list) else []:
        category = _dict(category)
        start, end = deals.parse_date(category.get("startDate")), deals.parse_date(category.get("endDate"))
        for group in category.get("content") if isinstance(category.get("content"), list) else []:
            group = _dict(group)
            title = str(group.get("title") or "").strip()
            for product_id in group.get("productIds") if isinstance(group.get("productIds"), list) else []:
                product_id = str(product_id)
                if title:
                    titles.setdefault(product_id, []).append(title)
                if start and end:
                    known = windows.get(product_id)
                    first, last = start.isoformat(), end.isoformat()
                    windows[product_id] = (min(known[0], first), max(known[1], last)) if known else (first, last)
    return windows, titles


def _non_food(item: dict[str, Any], titles: list[str]) -> bool:
    """Staat het product alleen in groepen met non-food, of heeft het een categorie met non-food?"""
    if _NON_FOOD.search(str(item.get("mainCategoryID") or "")):
        return True
    return bool(titles) and all(_NON_FOOD.search(title) for title in titles)


def _is_promotion(price: dict[str, Any]) -> bool:
    """Heeft een prijs een label of een oude prijs? Dan is het een actieprijs, ook buiten de periode van de groep (OP=OP loopt door)."""
    label = str(_dict(price.get("priceTagLabels")).get("promoText1") or "").strip()
    return (bool(label) and not label.startswith("--")) or _number(_dict(price.get("strikePrice")).get("strikePriceValue")) is not None


def _periods(item: dict[str, Any], window: tuple[str, str] | None) -> list[dict[str, Any]]:
    """De prijsperiodes van een product: `promotionPrices`, of anders `currentPrice`. Een kale `currentPrice` (zonder label of oude
    prijs) telt alleen binnen de periode van de groep: daarbuiten is het de gewone prijs van voor of na de actie."""
    found = []
    for period in item.get("promotionPrices") if isinstance(item.get("promotionPrices"), list) else []:
        period = _dict(period)
        start = deals.parse_date(period.get("validFromLocalDate")) or deals.parse_date(deals.local_day(period.get("validFrom")))
        end = deals.parse_date(period.get("validUntilLocalDate")) or deals.parse_date(deals.local_day(period.get("validUntil")))
        if _number(period.get("priceValue")) is not None:
            found.append((period, start, end))
    if found:
        return [{"price": period, "from": start, "until": end} for period, start, end in found]
    current = _dict(item.get("currentPrice"))
    if _number(current.get("priceValue")) is None:
        return []
    start = deals.parse_date(deals.local_day(current.get("validFrom")))
    end = deals.parse_date(deals.local_day(current.get("validUntil")))
    if window is not None and not _is_promotion(current):
        first, last = date.fromisoformat(window[0]), date.fromisoformat(window[1])
        if (start and start > last) or (end and end < first):
            return []  # de prijs na (of voor) de actie
    return [{"price": current, "from": start, "until": end}]


def _label_and_price(price: dict[str, Any]) -> tuple[str, float]:
    """Het label en de prijs per stuk. "2 VOOR" bij 3,00 wordt 1,50 per stuk met het label "2 voor 3,00"."""
    value = _number(price.get("priceValue")) or 0.0
    label = str(_dict(price.get("priceTagLabels")).get("promoText1") or "").strip()
    if label.startswith("--"):
        label = ""  # "-- Select option --": een leeg veld in het beheer van Aldi
    count = _X_FOR.match(label)
    if count and int(count.group(1)) > 1:
        pieces = int(count.group(1))
        return f"{pieces} voor {value:.2f}".replace(".", ","), round(value / pieces, 2)
    return label, value


def _unit_size(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    per_unit = _PER_UNIT.match(text)
    return f"1 {per_unit.group(1).lower()}" if per_unit else text


def _image(item: dict[str, Any]) -> str:
    assets = [asset for asset in item.get("assets") or [] if isinstance(asset, dict)]
    primary = next((asset for asset in assets if asset.get("type") == "primary"), assets[0] if assets else {})
    url = primary.get("url")
    if not isinstance(url, str) or not url.startswith(IMAGE_PREFIX) or "?" in url:
        return ""
    return url + IMAGE_SIZE


def _category_hint(item: dict[str, Any], titles: list[str]) -> str | None:
    levels = _dict(item.get("hierarchicalCategories")).get("lvl0")
    if isinstance(levels, list) and levels and isinstance(levels[0], str) and levels[0].strip():
        return levels[0].strip()
    return titles[0] if titles else None


def offers_from_html(text: str, today: date | None = None) -> list[dict[str, Any]]:
    """De aanbiedingen op de pagina, een per product en periode. Een product dat we niet kunnen lezen, slaan we over."""
    today = today or date.today()
    data = _offer_data(text)
    windows, titles = _groups(data.get("categories"))
    found: list[dict[str, Any]] = []
    for key, item in data["algoliaDataMap"].items():
        if not isinstance(item, dict):
            continue
        product_id = str(item.get("objectID") or key)
        name = str(item.get("name") or "").strip()
        groups = titles.get(product_id, [])
        if not name or item.get("isAvailable") is False or _non_food(item, groups):
            continue
        slug = item.get("productSlug")
        brand = item.get("brandName")
        for period in _periods(item, windows.get(product_id)):
            if period["until"] is not None and period["until"] < today:
                continue
            label, price = _label_and_price(period["price"])
            before = _number(_dict(period["price"].get("strikePrice")).get("strikePriceValue"))
            found.append(
                {
                    "id": product_id,
                    "title": name,
                    "brand": brand.strip() if isinstance(brand, str) and brand.strip() else None,
                    "unitSize": _unit_size(item.get("salesUnit")),
                    "price": price,
                    "priceBefore": before if before and before > price else None,
                    "promoLabel": label,
                    "image": _image(item),
                    "url": PRODUCT_URL.format(slug=slug) if isinstance(slug, str) and _SLUG.match(slug) else "",
                    "validFrom": period["from"].isoformat() if period["from"] else None,
                    "validUntil": period["until"].isoformat() if period["until"] else None,
                    "categoryHint": _category_hint(item, groups),
                }
            )
    return found


async def get_offers() -> list[dict[str, Any]]:
    """De aanbiedingen van Aldi voor `offers.sync`. Geen account nodig."""
    found = offers_from_html(await _page())
    if not found:
        raise AldiError(
            tr(
                "Aldi gaf geen aanbiedingen (de pagina is leeg of anders van vorm)",
                "Aldi returned no offers (the page is empty or has changed)",
            )
        )
    return found
