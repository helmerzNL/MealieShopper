"""De rangorde van de aanbiedingen: de laagste prijs van het afgelopen jaar per product (zie prices._annotate).

"Beste" is voor de eigenaar: de prijs van nu is de laagste prijs van de afgelopen twaalf maanden. Die laagste prijs komt uit vier
bronnen, alles wat de app van het product bewaart: eigen aanbiedingen (de actieprijs per stuk), folderacties voor de hele lijn
(per stuk, line_offers.per_piece), aankopen (wat je per stuk betaalde) en bewaarde gewone prijzen. De aanbieding van deze week
zelf telt niet mee: die is wat we vergelijken.

Een punt is `(dag, prijs per stuk, bron)`. `year_low` geeft per aanbieding:

* `status`: `lowest` als de actieprijs van nu op of onder de laagste van de afgelopen 365 dagen ligt (binnen een halve cent),
  `above` als hij erboven ligt, `unknown` zonder prijs van nu, zonder punt in het jaar of met een dekking korter dan een jaar.
* `price`, `month` en `source`: de laagste prijs in het jaar, de maand waarin hij het laatst gold en waar hij vandaan kwam.
* `coveredSince`: de oudste dag waarop een bron iets van het product weet. Pas vanaf 365 dagen dekking is "laagste in een jaar"
  een uitspraak en geen toeval van een app die het product drie weken volgt.
* `above`: het verschil met de laagste, `0.0` bij `lowest`.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

YEAR_DAYS: int = 365
HALF_CENT: float = 0.005

Point = tuple[str, float, str]


def _empty() -> dict[str, Any]:
    return {
        "status": "unknown",
        "price": None,
        "month": None,
        "source": None,
        "coveredSince": None,
        "above": None,
    }


def year_low(
    effective: float | None, points: list[Point], today: str
) -> dict[str, Any]:
    """De laagste prijs van de afgelopen twaalf maanden en hoe de prijs van nu zich daartoe verhoudt (zie de module)."""
    valid: list[Point] = [
        point
        for point in points
        if point[0] and point[1] is not None and point[1] > 0 and point[0][:10] <= today
    ]
    if effective is None or not valid:
        return _empty()
    covered_since: str = min(point[0][:10] for point in valid)
    since: str = (date.fromisoformat(today) - timedelta(days=YEAR_DAYS)).isoformat()
    in_year: list[Point] = [point for point in valid if point[0][:10] > since]
    covered: int = (date.fromisoformat(today) - date.fromisoformat(covered_since)).days
    result: dict[str, Any] = {**_empty(), "coveredSince": covered_since}
    if covered < YEAR_DAYS or not in_year:
        return result
    lowest: float = min(point[1] for point in in_year)
    last: Point = max(
        (point for point in in_year if abs(point[1] - lowest) <= HALF_CENT),
        key=lambda point: point[0],
    )
    result.update(price=round(lowest, 2), month=last[0][:7], source=last[2])
    if effective <= lowest + HALF_CENT:
        result.update(status="lowest", above=0.0)
    else:
        result.update(status="above", above=round(effective - lowest, 2))
    return result


# --- verpakkingsfamilies ------------------------------------------------------------------------------------------------
#
# Rundergehakt van 300 g, 500 g en 1000 g zijn drie producten met drie prijzen, maar een koper vergelijkt ze per kilo: een
# aanbieding op de 300 g die per kilo duurder is dan de 1000 g tegen gewone prijs is geen koopje. Een familie is dezelfde naam
# zonder maten en verpakkingswoorden (prices.base_title) met hetzelfde merk. Mager en biologisch blijven in de naam staan en zijn
# dus een eigen familie.


def family_key(store: str, title: Any, brand: Any, known_brands: dict[str, set[str]] | None = None) -> str:
    """De sleutel van de familie: winkel, naam zonder maten en gevouwen merk. Een leeg merk neemt het merk over dat de andere
    verpakkingen met die naam hebben (`known_brands`: naam -> merken), als dat er precies een is."""
    from . import ah_lists  # noqa: PLC0415 - prices importeert deze module, dus hier pas
    from .prices import _without_house_brand, base_title  # noqa: PLC0415

    full: str = base_title(title)
    # Het huismerk vooraan ("ah rundergehakt") hoort bij het merk, niet bij de naam: een bon schrijft het vaak zonder.
    base: str = _without_house_brand(store, full) or full
    folded: str = ah_lists._fold(brand or "").strip()
    if not folded and base != full:
        folded = store
    if not folded and known_brands:
        candidates: set[str] = known_brands.get(base) or set()
        if len(candidates) == 1:
            folded = next(iter(candidates))
    return f"{store}|{base}|{folded}"


def _size_order(unit_size: Any) -> tuple[int, float]:
    from . import packs  # noqa: PLC0415

    size = packs.parse_pack_size(unit_size)
    return (0, size[0]) if size else (1, 0.0)


def _mark_cheapest(found: list[dict[str, Any]], unit: str) -> None:
    """De goedkoopste per eenheid krijgt `cheapest`, onder de verpakkingen met eenheid `unit`. Binnen `PACK_MARGIN` van de laagste
    telt als gelijk, en dan wint de verpakking die in de aanbieding is, daarna de laagste prijs."""
    from .prices import PACK_MARGIN  # noqa: PLC0415

    priced = [pack for pack in found if pack.get("unitPrice") and pack["unitPrice"].get("value") is not None and pack["unitPrice"].get("unit") == unit]
    for pack in found:
        pack["cheapest"] = False
    if not priced:
        return
    lowest: float = min(pack["unitPrice"]["value"] for pack in priced)
    tied = [pack for pack in priced if pack["unitPrice"]["value"] <= lowest * (1 + PACK_MARGIN)]
    best = min(tied, key=lambda pack: (not pack.get("onOffer"), pack["unitPrice"]["value"], pack.get("price") or 0))
    best["cheapest"] = True


def families(store: str, packs_known: list[dict[str, Any]], known_titles: dict[str, str]) -> dict[str, dict[str, Any]]:
    """Per familiesleutel de verpakkingen met prijs per eenheid (`packs`, op grootte), de goedkoopste gemarkeerd, en `unknownPacks`
    als de app nog een verpakking van deze familie kent (`known_titles`: product-id -> titel) zonder prijs van de laatste tijd.

    `packs_known` zijn de verpakkingen met een prijs: de aanbiedingen van de week en de bewaarde prijzen (zie prices._annotate).
    Hetzelfde product twee keer (Bonus Box en gewone bonus) is een verpakking met de laagste prijs. Elke verpakking blijft in de
    familie, maar de goedkoopste wordt alleen gezocht onder de verpakkingen met de eenheid van de meerderheid (een 4-pack per stuk
    naast gehakt per kilo doet niet mee)."""
    from . import ah_lists  # noqa: PLC0415

    known_brands: dict[str, set[str]] = {}
    for pack in packs_known:
        folded = ah_lists._fold(pack.get("brand") or "").strip()
        if folded:
            known_brands.setdefault(family_key(store, pack.get("title"), None).split("|")[1], set()).add(folded)
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    for pack in packs_known:
        if not pack.get("unitPrice") or pack.get("price") is None:
            continue
        key = family_key(store, pack.get("title"), pack.get("brand"), known_brands)
        product_id = str(pack["productId"])
        current = grouped.setdefault(key, {}).get(product_id)
        if current is None or pack["price"] < current["price"]:
            grouped[key][product_id] = {
                "productId": product_id, "title": pack.get("title"), "unitSize": pack.get("unitSize"), "price": pack["price"],
                "unitPrice": pack["unitPrice"], "onOffer": bool(pack.get("onOffer")) or bool(current and current["onOffer"]),
                "known": "offer" if pack.get("onOffer") else "observation",
            }
    known_keys: dict[str, set[str]] = {}
    for product_id, title in known_titles.items():
        known_keys.setdefault(family_key(store, title, None, known_brands), set()).add(str(product_id))
    result: dict[str, dict[str, Any]] = {}
    for key, by_id in grouped.items():
        units: dict[str, int] = {}
        for pack in by_id.values():
            units[pack["unitPrice"]["unit"]] = units.get(pack["unitPrice"]["unit"], 0) + 1
        unit = max(units, key=lambda name: (units[name], name))
        found = sorted(by_id.values(), key=lambda pack: (pack["unitPrice"]["unit"] != unit, *_size_order(pack["unitSize"])))
        _mark_cheapest(found, unit)
        result[key] = {"key": key, "packs": found, "unknownPacks": bool(known_keys.get(key, set()) - set(by_id))}
    return result


def family_of(found: dict[str, dict[str, Any]], store: str, title: Any, brand: Any) -> dict[str, Any]:
    """De familie van een aanbieding uit `families`, of een lege familie met alleen de sleutel. Zonder merk past de aanbieding bij
    de enige familie met die naam die wel een merk heeft (dezelfde regel als `family_key` met `known_brands`)."""
    key: str = family_key(store, title, brand)
    if key in found:
        return found[key]
    if key.endswith("|"):
        matches = [candidate for candidate in found if candidate.rsplit("|", 1)[0] == key[:-1]]
        if len(matches) == 1:
            return found[matches[0]]
    return {"key": key, "packs": [], "unknownPacks": False}
