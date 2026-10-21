"""Verpakkingsrekenwerk dat voor elke supermarkt hetzelfde is.

Een ingredient ("500 g") en een verpakkingstekst ("2 x 250 g") worden naar gram,
milliliter of stuks omgerekend, waarna blijkt hoeveel verpakkingen nodig zijn. De
winkels leveren de verpakkingstekst aan; de berekening zelf is hier gedeeld.
"""

import math
import re
from typing import Any

from .i18n import tr

MAX_CART_QUANTITY = 99  # AH weigert een aantal buiten 1..99 met een 400


def normalize_quantity(value: Any) -> int:
    """Aantal verpakkingen voor het AH-winkelmandje, altijd binnen 1..99."""
    try:
        quantity = int(round(float(value)))
    except (TypeError, ValueError, OverflowError):
        return 1
    return min(MAX_CART_QUANTITY, max(1, quantity))


_MASS_UNITS = {"g": 1, "gr": 1, "gram": 1, "kg": 1000, "kilo": 1000, "kilogram": 1000}
_VOLUME_UNITS = {
    "ml": 1, "milliliter": 1, "cl": 10, "centiliter": 10,
    "dl": 100, "deciliter": 100, "l": 1000, "liter": 1000,
}
_COUNT_UNITS = {"", "stuk", "stuks", "st"}
_PACK_SIZE_RE = re.compile(
    r"(?:(\d+)\s*[x\u00d7]\s*)?(\d+(?:[.,]\d+)?)\s*"
    r"(kilogram|kilo|kg|gram|gr|g|milliliter|centiliter|deciliter|liter|ml|cl|dl|l|stuks|stuk|st)\b",
    re.IGNORECASE,
)


def _measure(amount: float, unit: str) -> tuple[float, str] | None:
    """Reken een hoeveelheid om naar (waarde, soort): gram, milliliter of aantal stuks."""
    unit = unit.strip().lower().rstrip(".")
    if unit in _MASS_UNITS:
        return amount * _MASS_UNITS[unit], "mass"
    if unit in _VOLUME_UNITS:
        return amount * _VOLUME_UNITS[unit], "volume"
    if unit in _COUNT_UNITS:
        return amount, "count"
    return None


def parse_pack_size(unit_size: Any) -> tuple[float, str] | None:
    """Verpakkingsgrootte uit AH-tekst als "6 stuks", "1 kg", "4 x 125 g" of "per stuk"."""
    text = str(unit_size or "").strip().lower()
    if not text:
        return None
    match = _PACK_SIZE_RE.search(text)
    if match:
        multiplier = int(match.group(1) or 1)
        amount = float(match.group(2).replace(",", ".")) * multiplier
        return _measure(amount, match.group(3))
    if re.search(r"\bper stuk\b|\bstuk\b", text):
        return 1.0, "count"
    return None


LARGE_PACK_COUNT = 10  # meer verpakkingen dan dit laten we de gebruiker controleren


def assess_packs(quantity: Any, unit: Any, unit_size: Any) -> tuple[int, str | None]:
    """Aantal verpakkingen voor een ingredient, plus de reden als de berekening onzeker is.

    "6 eieren" bij "10 stuks" is een doosje, "500 g" bij "250 g" zijn er twee. Wat niet
    te vergelijken valt (el, tl, teentje) of waar een gok volstaat ("2 uien" bij "1 kg")
    is een verpakking zonder twijfel. Twijfel is er bij een onbekende verpakkingsgrootte,
    gewicht of volume tegen stuks, of een groot aantal.
    """
    unit_text = str(unit or "").strip()
    try:
        needed = float(quantity)
    except (TypeError, ValueError):
        needed = 1.0
    if not needed > 0 or math.isinf(needed):
        needed = 1.0

    ingredient = _measure(needed, unit_text)
    if ingredient is None:
        return 1, None
    pack = parse_pack_size(unit_size)
    if pack is None:
        packs = normalize_quantity(needed) if ingredient[1] == "count" else 1
        return packs, tr("Verpakkingsgrootte onbekend", "Pack size unknown")
    if pack[1] != ingredient[1] or pack[0] <= 0:
        if ingredient[1] == "count":
            return 1, None
        return 1, tr(
            f"{unit_text} past niet bij de verpakking ({unit_size})",
            f"{unit_text} doesn't match the pack ({unit_size})",
        )

    packs = normalize_quantity(math.ceil(ingredient[0] / pack[0] - 1e-9))
    if packs > LARGE_PACK_COUNT:
        return packs, tr("Groot aantal verpakkingen", "Large number of packs")
    return packs, None


def packs_needed(quantity: Any, unit: Any, unit_size: Any) -> int:
    return assess_packs(quantity, unit, unit_size)[0]
