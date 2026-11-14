"""Aanbiedingen die voor elke supermarkt hetzelfde eruitzien.

De winkels geven een aanbieding elk op een eigen manier door: AH met een mechaniek ("2e halve prijs"),
Jumbo met een tekst per promotie ("2 voor 5") en Picnic met een decorator. Tot nu toe bleef daarvan alleen
een ja/nee-vlag `isBonus` over, waardoor niet te zien was wat de aanbieding inhoudt, of ze nog geldt en
wat een stuk dan kost. Deze module zet wat de winkels leveren om naar één vorm, zonder AI:

- `parse_mechanic` leest de Nederlandse actietekst en geeft de soort aanbieding en het minimumaantal;
- `effective_unit_price` rekent uit wat een stuk kost als je de voorwaarde haalt;
- `validity` zegt of de aanbieding nog geldt, verlopen is, nog moet beginnen of onbekend is.

Wat we niet met zekerheid kunnen lezen blijft `unknown`: liever een aanbieding zonder rekenwerk dan een
verkeerde prijs.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

from . import packs

# Soorten aanbieding.
PERCENT = "percent"  # "25% korting"
AMOUNT_OFF = "amount_off"  # "€ 1,00 korting"
SECOND_HALF = "second_half"  # "2e halve prijs"
NTH_PERCENT = "nth_percent"  # "2e = 50% korting": het n-de stuk met korting
NTH_FREE = "nth_free"  # "2e gratis", "1+1 gratis", "2+1 gratis"
X_FOR_Y = "x_for_y"  # "2 voor 5,00"
FIXED = "fixed"  # alleen een lagere prijs, zonder voorwaarde
PER_MEASURE = "per_measure"  # "per 100 gram voor 1,59": een prijs per gewicht of inhoud, niet per pak
UNKNOWN = "unknown"

# Geldigheid.
VALID = "valid"
EXPIRED = "expired"
UPCOMING = "upcoming"
NO_DATES = "unknown"

_NUMBER = r"(\d+(?:[.,]\d+)?)"
_EURO = r"(?:€|eur(?:o)?)?\s*"
_BUY_GET_FREE_RE = re.compile(r"\b(\d+)\s*\+\s*(\d+)\s*(?:stuks?\s*)?gratis\b")
_NTH_FREE_RE = re.compile(r"\b(\d+)\s*(?:e|de|ste)\s*(?:stuk\s*)?gratis\b")
_SECOND_HALF_RE = re.compile(r"\b(\d+)\s*(?:e|de|ste)\s*(?:stuk\s*)?(?:voor\s*)?(?:de\s*)?halve\s*prijs\b")
_X_FOR_Y_RE = re.compile(rf"\b(\d+)\s*(?:stuks?\s*)?voor\s*{_EURO}{_NUMBER}(?![\d%])(?!\s*%)")
_NTH_PERCENT_RE = re.compile(rf"\b(\d+)\s*(?:e|de|ste)\s*(?:stuk\s*)?(?:=|:|-)?\s*{_NUMBER}\s*%")
# "nu 1,99", en AH's schildje "voor 1.99" (een aantal ervoor, "2 voor 5", is al eerder als x voor y gelezen).
_NOW_PRICE_RE = re.compile(rf"\b(?:nu|voor)\s*{_EURO}{_NUMBER}(?![\d%])(?!\s*%)")
_PERCENT_RE = re.compile(rf"{_NUMBER}\s*%")
_AMOUNT_OFF_RE = re.compile(rf"{_EURO}{_NUMBER}\s*(?:euro\s*)?korting|korting\s*{_EURO}{_NUMBER}")
_FROM_QUANTITY_RE = re.compile(r"\bvanaf\s*(\d+)\s*(?:stuks?|st)\b")
# "per 100 gram voor 1.59", "per kilo voor 9,99": het bedrag geldt voor dat gewicht of die inhoud.
_PER_MEASURE_RE = re.compile(
    rf"\bper\s*(\d+(?:[.,]\d+)?)?\s*(kilogram|kilo|kg|gram|gr|g|milliliter|liter|ml|cl|dl|l)\s*voor\s*{_EURO}{_NUMBER}(?![\d%])(?!\s*%)"
)


def _number(text: str) -> float:
    return float(text.replace(",", "."))


def parse_mechanic(*texts: Any) -> dict[str, Any]:
    """Lees de actietekst van een winkel: soort, minimumaantal en wat er per stuk betaald wordt.

    Geeft altijd een dict met `type`, `minQuantity` en de getallen die bij die soort horen
    (`percent`, `amountOff`, `total`, `payQuantity`). Meerdere teksten (label, mechaniek, beschrijving) worden
    achter elkaar gelezen; de eerste die iets herkenbaars bevat wint. Niet herkend: `type == "unknown"`.
    """
    for raw in texts:
        text = re.sub(r"\s+", " ", str(raw or "").strip().lower())
        if not text:
            continue
        match = _PER_MEASURE_RE.search(text)
        if match:
            return {
                "type": PER_MEASURE,
                "minQuantity": 1,
                "measure": f"{match.group(1) or '1'} {match.group(2)}",
                "measurePrice": _number(match.group(3)),
            }
        match = _BUY_GET_FREE_RE.search(text)
        if match:
            pay, free = int(match.group(1)), int(match.group(2))
            return {"type": NTH_FREE, "minQuantity": pay + free, "payQuantity": pay}
        match = _SECOND_HALF_RE.search(text)
        if match:
            return {"type": SECOND_HALF, "minQuantity": int(match.group(1))}
        match = _NTH_FREE_RE.search(text)
        if match:
            nth = int(match.group(1))
            return {"type": NTH_FREE, "minQuantity": nth, "payQuantity": nth - 1}
        match = _NTH_PERCENT_RE.search(text)
        if match:
            return {"type": NTH_PERCENT, "minQuantity": int(match.group(1)), "percent": _number(match.group(2))}
        match = _X_FOR_Y_RE.search(text)
        if match:
            return {"type": X_FOR_Y, "minQuantity": int(match.group(1)), "total": _number(match.group(2))}
        match = _PERCENT_RE.search(text)
        if match:
            minimum = _FROM_QUANTITY_RE.search(text)
            return {"type": PERCENT, "minQuantity": int(minimum.group(1)) if minimum else 1, "percent": _number(match.group(1))}
        match = _AMOUNT_OFF_RE.search(text)
        if match:
            minimum = _FROM_QUANTITY_RE.search(text)
            amount = match.group(1) or match.group(2)
            return {"type": AMOUNT_OFF, "minQuantity": int(minimum.group(1)) if minimum else 1, "amountOff": _number(amount)}
    for raw in texts:
        match = _NOW_PRICE_RE.search(str(raw or "").lower())
        if match:
            return {"type": FIXED, "minQuantity": 1, "fixedPrice": _number(match.group(1))}
    return {"type": UNKNOWN, "minQuantity": 1}


def pack_price(mechanic: dict[str, Any], unit_size: Any) -> float | None:
    """Wat een pak kost bij een aanbieding per gewicht of inhoud ("per 100 gram voor 1,59" op een pak van ca. 755 g: 12,00), of
    None als de aanbieding er geen is of de inhoud van het pak niet in dezelfde maat te lezen is."""
    if mechanic.get("type") != PER_MEASURE:
        return None
    measure, size = packs.parse_pack_size(mechanic.get("measure")), packs.parse_pack_size(unit_size)
    if not measure or not size or measure[1] != size[1] or measure[1] == "count" or measure[0] <= 0:
        return None
    return round(mechanic["measurePrice"] * size[0] / measure[0], 2)


def measure_priced(label: Any, unit_size: Any, price: Any) -> float | None:
    """De prijs van het pak als `price` de prijs per gewicht uit de actietekst is, anders None. AH geeft bij "per 100 GRAM voor
    1.59" die 1,59 door als prijs van het product, naast de gewone prijs van het hele pak: dat leest als 90% korting."""
    return pack_price(parse_mechanic(label), unit_size) if is_measure_price(label, price) else None


def is_measure_price(label: Any, price: Any) -> bool:
    """Is `price` de prijs per gewicht of inhoud uit de actietekst, en dus niet die van het pak?"""
    mechanic = parse_mechanic(label)
    return mechanic["type"] == PER_MEASURE and isinstance(price, (int, float)) and abs(price - mechanic["measurePrice"]) <= 0.005


def effective_unit_price(mechanic: dict[str, Any], price_before: float | None, price_now: float | None) -> float | None:
    """Wat één stuk kost als je de voorwaarde van de aanbieding haalt, of None als dat niet te weten is.

    `price_before` is de gewone prijs. `price_now` is de prijs die de winkel noemt; bij een aanbieding met
    voorwaarde ("2 voor 5") weten we niet zeker of dat de stukprijs of de gewone prijs is, dus voor die soorten
    rekenen we alleen vanuit de gewone prijs of het totaalbedrag in de tekst.
    """
    kind = mechanic.get("type")
    base = price_before if price_before else None
    if kind == X_FOR_Y and mechanic.get("total") and mechanic.get("minQuantity"):
        return round(mechanic["total"] / mechanic["minQuantity"], 2)
    if kind == SECOND_HALF and base:
        return round(base * 0.75, 2)
    if kind == NTH_FREE and base and mechanic.get("minQuantity"):
        return round(base * mechanic.get("payQuantity", 1) / mechanic["minQuantity"], 2)
    if kind == NTH_PERCENT and base and mechanic.get("minQuantity"):
        count = mechanic["minQuantity"]
        return round(base * (count - mechanic["percent"] / 100) / count, 2)
    if kind in (PERCENT, AMOUNT_OFF) and base:
        if kind == PERCENT:
            computed = round(base * (1 - mechanic["percent"] / 100), 2)
        else:
            computed = round(max(0.0, base - mechanic["amountOff"]), 2)
        # Een afgerond percentage ("-30%" op 1,69) geeft soms een cent verschil met de prijs die de winkel zelf noemt (1,19). Geldt de
        # korting al vanaf één stuk en ligt de prijs van de winkel binnen twee cent, dan wint die.
        if mechanic.get("minQuantity", 1) <= 1 and price_now is not None and 0 < price_now < base and abs(price_now - computed) <= 0.02:
            return round(price_now, 2)
        return computed
    if kind == FIXED and mechanic.get("fixedPrice") is not None:
        return round(mechanic["fixedPrice"], 2)
    if kind in (FIXED, PER_MEASURE) and price_now is not None:  # per gewicht: de winkelprijs is al die van het pak (`measure_priced`)
        return round(price_now, 2)
    return None


def parse_date(value: Any) -> date | None:
    """Een datum uit een ISO-tekst ("2026-10-19", "2026-10-19T00:00:00Z") of een datum(tijd); anders None."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if len(text) < 10:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _last_sunday(year: int, month: int) -> date:
    """De laatste zondag van een maand met 31 dagen (maart of oktober): de dag waarop de zomertijd begint of eindigt."""
    last = date(year, month, 31)
    return last - timedelta(days=(last.weekday() + 1) % 7)


def _amsterdam_offset(moment: datetime) -> timedelta:
    """Het verschil met UTC in Nederland op een moment in UTC (naïef): de zomertijd van de EU loopt van de laatste zondag van
    maart tot de laatste zondag van oktober, steeds om 01:00 UTC. Een vaste regel, zodat de image geen tijdzonegegevens nodig heeft."""
    start = datetime.combine(_last_sunday(moment.year, 3), datetime.min.time()) + timedelta(hours=1)
    end = datetime.combine(_last_sunday(moment.year, 10), datetime.min.time()) + timedelta(hours=1)
    return timedelta(hours=2) if start <= moment < end else timedelta(hours=1)


def local_day(value: Any) -> str | None:
    """De dag in Nederland (`JJJJ-MM-DD`) van een tijdstip: unix-seconden, of een ISO-tekst met `Z` of een tijdverschil.

    Lidl en Aldi geven het einde van een aanbieding als `2026-11-15T22:59:59Z` of als unix-tijd: de eerste tien tekens daarvan
    zijn soms de dag ervoor. Een ISO-tekst zonder tijdverschil telt al als Nederlandse tijd. Wat geen tijdstip is, geeft None.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            moment = datetime(1970, 1, 1) + timedelta(seconds=float(value))
        except (OverflowError, ValueError):
            return None
        return (moment + _amsterdam_offset(moment)).date().isoformat()
    text = str(value or "").strip()
    if len(text) < 10:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.date().isoformat()
    utc = (parsed - parsed.utcoffset()).replace(tzinfo=None)
    return (utc + _amsterdam_offset(utc)).date().isoformat()


def validity(valid_from: Any, valid_until: Any, today: date | None = None) -> str:
    """`valid`, `expired`, `upcoming` of `unknown` (geen datums bekend, dus niet te controleren).

    De einddatum geldt tot en met die dag. Is alleen een van de twee datums bekend, dan controleren we die.
    """
    start, end = parse_date(valid_from), parse_date(valid_until)
    if start is None and end is None:
        return NO_DATES
    today = today or date.today()
    if end is not None and today > end:
        return EXPIRED
    if start is not None and today < start:
        return UPCOMING
    return VALID


def common_period(offers: list[dict[str, Any]]) -> dict[str, str] | None:
    """De looptijd die de meeste aanbiedingen delen, als `{"from", "until"}` met ISO-datums; None als er geen is.

    Alleen aanbiedingen met een begin- én einddatum tellen mee: een doorlopende aanbieding heeft geen einde en zegt niets over
    de week. Een looptijd van één dag telt alleen als er geen langere is: AH geeft zijn pakketkortingen en volumevoordelen
    elke dag opnieuw met een looptijd van alleen die dag, en dat zijn er zoveel dat de kop anders "zo 1 nov t/m zo 1 nov"
    zei terwijl de bonusweek van ma t/m zo liep. Bij gelijke aantallen wint de periode die het laatst eindigt.
    """
    counts: dict[tuple[date, date], int] = {}
    for offer in offers:
        start, end = parse_date(offer.get("validFrom")), parse_date(offer.get("validUntil"))
        if start is not None and end is not None and start <= end:
            counts[(start, end)] = counts.get((start, end), 0) + 1
    longer = {period: count for period, count in counts.items() if period[0] < period[1]}
    counts = longer or counts
    if not counts:
        return None
    start, end = max(counts, key=lambda period: (counts[period], period[1]))
    return {"from": start.isoformat(), "until": end.isoformat()}


def week_of(day: Any) -> tuple[date, date] | None:
    """De maandag en de zondag van de week waarin `day` valt (een datum of ISO-tekst); None als het geen datum is."""
    parsed = parse_date(day)
    if parsed is None:
        return None
    monday = parsed - timedelta(days=parsed.weekday())
    return monday, monday + timedelta(days=6)


def in_week(valid_from: Any, valid_until: Any, monday: date, sunday: date) -> bool:
    """Of een aanbieding met deze looptijd op een of meer dagen van de week geldt. Een ontbrekende datum is open aan die kant."""
    start, end = parse_date(valid_from), parse_date(valid_until)
    return (start is None or start <= sunday) and (end is None or end >= monday)


def describe(
    *texts: Any,
    price_now: float | None = None,
    price_before: float | None = None,
    valid_from: Any = None,
    valid_until: Any = None,
    today: date | None = None,
) -> dict[str, Any]:
    """Wat een aanbieding inhoudt: soort, minimumaantal, prijs per stuk bij de voorwaarde en of ze nog geldt.

    `texts` zijn de actieteksten van de winkel (label, mechaniek). Zonder herkenbare tekst maar met een lagere prijs dan de
    gewone prijs is het een gewone prijsverlaging (`fixed`). Is de soort onbekend, dan is `unitPrice` None.
    """
    mechanic = parse_mechanic(*texts)
    if mechanic["type"] == UNKNOWN and price_before and price_now is not None and price_now < price_before:
        mechanic = {"type": FIXED, "minQuantity": 1}
    return {
        "mechanic": mechanic["type"],
        "minQuantity": mechanic["minQuantity"],
        "unitPrice": effective_unit_price(mechanic, price_before, price_now),
        "validity": validity(valid_from, valid_until, today),
    }


def make_deal(store: str, product: dict[str, Any], today: date | None = None) -> dict[str, Any] | None:
    """Zet een winkelproduct met aanbieding om naar een genormaliseerde aanbieding, of None als het geen aanbieding is.

    `product` is een productdict van `ah`, `jumbo` of `picnic`. Het veld `promo` bevat wat de winkel over de
    actie meegaf (`label`, `mechanism`, `priceBefore`, `validFrom`, `validUntil`); ontbreekt het, dan blijft
    alleen de bonusvlag over en is de aanbieding `unknown` zonder datums.
    """
    if not product.get("isBonus"):
        return None
    promo = product.get("promo") or {}
    price_now = product.get("price")
    price_now = price_now.get("now") if isinstance(price_now, dict) else price_now
    price_before = promo.get("priceBefore")
    described = describe(
        promo.get("mechanism"),
        promo.get("label"),
        price_now=price_now,
        price_before=price_before,
        valid_from=promo.get("validFrom"),
        valid_until=promo.get("validUntil"),
        today=today,
    )
    return {
        "store": store,
        "productId": str(product.get("id") or product.get("webshopId") or ""),
        "title": product.get("title"),
        "unitSize": product.get("unitSize"),
        "priceBefore": price_before,
        "priceNow": price_now,
        "label": promo.get("label") or promo.get("mechanism"),
        **described,
        "validFrom": promo.get("validFrom"),
        "validUntil": promo.get("validUntil"),
    }


def candidate_deal(store: str, product: dict[str, Any]) -> dict[str, Any] | None:
    """De aanbieding van een product in de vorm die de controlelijst nodig heeft, of None zonder aanbieding."""
    deal = make_deal(store, product)
    if deal is None:
        return None
    keys = ("label", "mechanic", "minQuantity", "unitPrice", "validUntil", "validity")
    return {key: deal[key] for key in keys}
