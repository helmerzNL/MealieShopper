"""Lidl: de aanbiedingen van de week, zonder account en zonder mandje.

Lidl Nederland verkoopt geen boodschappen online, dus er valt niets in een mandje te zetten en er is niets om te koppelen.
De aanbiedingen staan wel op een openbare pagina (`/c/aanbiedingen/...`, gelinkt vanaf de homepage), met per product een
blok JSON in het attribuut `data-grid-data`: titel, prijs, oude prijs, actietekst, looptijd, verpakking, foto en de prijs
met Lidl Plus. `robots.txt` staat die pagina toe; de zoek-API (`*search?q=*`) en doorbladeren (`*?offset=*`) niet, en die
gebruiken we dus ook niet. Zie docs/onderzoek-lidl.md.

- Een gewone aanbieding (een prijs met een einddatum) gaat voor. Heeft een product alleen een vaste prijs, maar wel een prijs
  met Lidl Plus (de gratis app van Lidl), dan is dat de aanbieding, met "met Lidl Plus" in het label. Een vaste prijs zonder
  meer is geen aanbieding.
- `validFrom` is de eerste dag in de winkel (`storeStartDate`), `validUntil` de laatste dag van de prijs, beide in
  Nederlandse tijd (`deals.local_day`).
- Non-food en planten (`NonFood`, `P+F`) laten we weg: MealieShopper gaat over eten.
- Een lege lijst is een fout (`LidlError`): dan blijft de vorige kopie staan, zoals bij de andere winkels.
"""

from __future__ import annotations

import html
import json
import re
from datetime import date
from typing import Any
from urllib.parse import urlsplit

import httpx

from . import about, deals, http_client
from .i18n import tr

SITE = "https://www.lidl.nl"
HOST = "www.lidl.nl"
OFFERS_URL = f"{SITE}/c/aanbiedingen/a10008785"
TIMEOUT = 30
REGION = "1"  # Lidl kent zes prijsregio's; de aanbiedingenpagina geeft alleen deze
SKIPPED_CATEGORIES = frozenset({"NonFood", "P+F"})
OPEN_ENDED_YEAR = 2100  # een einddatum daarna (Lidl gebruikt 2200-01-01) is een vaste prijs, geen aanbieding

_CARD = re.compile(r'data-grid-data="([^"]*)"')
_LEADING_DASH = re.compile(r"^[\s\-–—]+")
_PIECES = re.compile(r"\bStk\b\.?", re.I)


class LidlError(RuntimeError):
    """Een fout met een tekst van onszelf (nooit het antwoord van Lidl), die de gebruiker mag zien."""


def _user_agent() -> str:
    return f"MealieShopper/{about.version()} (+https://github.com/helmerzNL/MealieShopper)"


async def _page() -> str:
    """De HTML van de aanbiedingenpagina. Lidl verwijst door naar `/c/store/aanbiedingen/...`, op dezelfde host."""
    try:
        response = await http_client.client("lidl").get(
            OFFERS_URL,
            headers={"User-Agent": _user_agent(), "Accept-Language": "nl-NL,nl;q=0.9"},
            timeout=TIMEOUT,
            follow_redirects=True,
        )
    except httpx.HTTPError as exc:
        raise LidlError(tr("Lidl is niet bereikbaar", "Lidl is not reachable")) from exc
    if (urlsplit(str(response.url)).hostname or "").lower() != HOST:
        raise LidlError(tr("Lidl stuurde ons ergens anders heen", "Lidl redirected us elsewhere"))
    if response.status_code in (403, 429):
        raise LidlError(tr(f"Lidl weigerde het verzoek (status {response.status_code})", f"Lidl refused the request (status {response.status_code})"))
    if response.status_code != 200:
        raise LidlError(tr(f"Lidl antwoordde met status {response.status_code}", f"Lidl answered with status {response.status_code}"))
    return response.text


# --- de pagina lezen ------------------------------------------------------------------------------


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if value > 0 else None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _region(card: dict[str, Any]) -> dict[str, Any]:
    """De prijzen van regio 1, of anders van de eerste regio op de kaart."""
    regions = _dict(card.get("regionsPrices"))
    if REGION in regions:
        return _dict(regions[REGION])
    return _dict(next(iter(regions.values()), None))


def _ends(price: dict[str, Any]) -> str | None:
    """De laatste dag van een prijs, of None als hij geen einde heeft (een vaste prijs)."""
    end = deals.local_day(price.get("endDate"))
    if end is None or int(end[:4]) >= OPEN_ENDED_YEAR:
        return None
    return end


def _label(*parts: Any) -> str:
    return " ".join(str(part).strip() for part in parts if isinstance(part, str) and part.strip())


def _unit_size(*prices: dict[str, Any]) -> str | None:
    for price in prices:
        text = _dict(price.get("packaging")).get("text")
        if isinstance(text, str) and text.strip():
            return _PIECES.sub("stuks", text.strip())
    return None


def _category_hint(card: dict[str, Any]) -> str | None:
    """Het laatste deel van de categorie van Lidl ("…/Snoep & snacks/Hartige hapjes"), en "groente fruit" bij F+V."""
    path = _dict(card.get("keyfacts")).get("wonCategoryPrimary")
    hint = path.rsplit("/", 1)[-1].strip() if isinstance(path, str) else ""
    if card.get("category") == "F+V":
        hint = f"{hint} groente fruit".strip()
    return hint or None


def _image(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    if value.startswith("/"):
        value = SITE + value
    return value if value.startswith("https://") else ""


def _offer(card: dict[str, Any], today: date) -> dict[str, Any] | None:
    """Een kaart van de pagina als aanbieding in de vorm van `offers.sync`, of None als het geen (eetbare) aanbieding is."""
    product_id = card.get("productId") or card.get("itemId") or card.get("erpNumber")
    title = _LEADING_DASH.sub("", str(card.get("fullTitle") or card.get("title") or "")).strip()
    if not product_id or not title or card.get("category") in SKIPPED_CATEGORIES:
        return None
    region = _region(card)
    current = _dict(region.get("currentPrice")) or _dict(card.get("price"))
    plus = _dict(_dict(region.get("currentLidlPlusPrice")).get("price"))
    plus_texts = _dict(next(iter(card.get("lidlPlus") or []), None)) if isinstance(card.get("lidlPlus"), list) else {}
    regular = _number(current.get("price"))
    if _ends(current) and regular is not None:
        # Een gewone aanbieding: die geldt voor iedereen, dus die gaat voor op een prijs met Lidl Plus.
        chosen, price = current, regular
        discount = _dict(current.get("discount"))
        before = _number(discount.get("deletedPrice")) or _number(current.get("oldPrice"))
        label = _label(discount.get("discountText"))
    elif _ends(plus) and _number(plus.get("price")) is not None:
        chosen, price = plus, _number(plus.get("price"))
        discount = _dict(plus.get("discount"))
        before = _number(plus.get("oldPrice")) or _number(discount.get("deletedPrice")) or regular
        label = _label(discount.get("discountText") or plus_texts.get("highlightText"), plus_texts.get("lidlPlusText") or "met Lidl Plus")
    else:
        return None
    until = _ends(chosen)
    if until is None or price is None or date.fromisoformat(until) < today:
        return None
    start = deals.local_day(card.get("storeStartDate"))
    if start is None or start > until:
        start = deals.local_day(chosen.get("startDate"))
    brand = _dict(card.get("brand")).get("name")
    canonical = card.get("canonicalUrl")
    return {
        "id": str(product_id),
        "title": title,
        "brand": brand.strip() if isinstance(brand, str) and re.search(r"\w", brand) else None,  # Lidl zet soms "-" als merk
        "unitSize": _unit_size(chosen, current, _dict(card.get("price"))),
        "price": price,
        "priceBefore": before if before and before > price else None,
        "promoLabel": label,
        "image": _image(card.get("image")),
        "url": SITE + canonical if isinstance(canonical, str) and canonical.startswith("/p/") else "",
        "validFrom": start,
        "validUntil": until,
        "categoryHint": _category_hint(card),
    }


def offers_from_html(text: str, today: date | None = None) -> list[dict[str, Any]]:
    """De aanbiedingen op de pagina, elk product een keer. Een kaart die we niet kunnen lezen, slaan we over."""
    today = today or date.today()
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in _CARD.findall(text or ""):
        try:
            card = json.loads(html.unescape(raw))
        except ValueError:
            continue
        offer = _offer(card, today) if isinstance(card, dict) else None
        if offer is None or offer["id"] in seen:
            continue
        seen.add(offer["id"])
        found.append(offer)
    return found


async def get_offers() -> list[dict[str, Any]]:
    """De aanbiedingen van Lidl voor `offers.sync`. Geen account nodig."""
    found = offers_from_html(await _page())
    if not found:
        raise LidlError(
            tr(
                "Lidl gaf geen aanbiedingen (de pagina is leeg of anders van vorm)",
                "Lidl returned no offers (the page is empty or has changed)",
            )
        )
    return found
