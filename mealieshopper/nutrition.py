"""Voedingswaarden van een recept, per portie (issue #283).

Elke bron noemt ze anders: Mealie bewaart tekst zonder eenheid (`"490"`, zijn import haalt die eraf), een schema.org-recept
tekst met eenheid (`"490 kcal"`), AH en Jumbo een getal met een eenheid ernaast en Marley Spoon een getal per portie. Hier
komen ze samen in de velden van schema.org (en van Mealie), elk met een vaste eenheid: kcal voor energie, mg voor natrium en
cholesterol, gram voor de rest. Alle bronnen geven waarden per portie (per persoon), dus dat doen wij ook: het totaal van een
maaltijd is de waarde per portie maal het aantal porties.
"""
from __future__ import annotations

import math
import re
from typing import Any

# De velden in de volgorde waarin ze op het scherm staan, met de eenheid waarin we ze bewaren. Dezelfde namen als in Mealie.
FIELDS: tuple[tuple[str, str], ...] = (
    ("calories", "kcal"),
    ("proteinContent", "g"),
    ("fatContent", "g"),
    ("saturatedFatContent", "g"),
    ("unsaturatedFatContent", "g"),
    ("transFatContent", "g"),
    ("carbohydrateContent", "g"),
    ("sugarContent", "g"),
    ("fiberContent", "g"),
    ("sodiumContent", "mg"),
    ("cholesterolContent", "mg"),
)
KEYS: tuple[str, ...] = tuple(key for key, _ in FIELDS)
UNITS: dict[str, str] = dict(FIELDS)
KCAL_PER_KJ = 1 / 4.184
SODIUM_MG_PER_SALT_G = 400  # zout is voor 40% natrium: 1 g zout = 400 mg natrium
MAX_KCAL = 10_000  # per portie; meer is een tikfout of het hele recept, en dat filteren we niet mee

_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_UNIT_ALIASES = {"gr": "g", "gram": "g", "grams": "g", "milligram": "mg", "milligrams": "mg", "kilojoule": "kj", "kilocalorie": "kcal", "kilocalories": "kcal"}


def number(value: Any) -> float | None:
    """Het eerste getal in een waarde: `490`, `"490"`, `"490 kcal"` en `"20,5 g"`. None bij niets bruikbaars of iets negatiefs."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        result = float(value)
    else:
        found = _NUMBER.search(str(value))
        if not found:
            return None
        result = float(found.group(0).replace(",", "."))
    return result if math.isfinite(result) and result >= 0 else None


def _unit_of(value: Any) -> str:
    """De eenheid in een tekst als `"2000 kJ"` of `"0,5 g"`, in kleine letters; leeg als er geen staat."""
    if not isinstance(value, str):
        return ""
    found = re.search(r"\d\s*([a-zA-Zµ]+)", value)
    return found.group(1).lower() if found else ""


def convert(key: str, value: Any, unit: Any = None) -> float | None:
    """Een waarde in de eenheid van `key` (zie `FIELDS`): kJ wordt kcal, g wordt mg en andersom. Zonder eenheid geldt die van
    het veld. Een eenheid die niet past (gram voor energie) geeft None."""
    if key not in UNITS:
        return None
    amount = number(value)
    if amount is None:
        return None
    given = str(unit or "").strip().lower() or _unit_of(value)
    given = _UNIT_ALIASES.get(given, given)
    wanted = UNITS[key]
    if key == "calories":
        if given in ("", "kcal", "cal", "calorie", "calories", "calorieen", "calorieën"):
            return amount
        return amount * KCAL_PER_KJ if given == "kj" else None
    if given in ("", wanted):
        return amount
    if (given, wanted) == ("g", "mg"):
        return amount * 1000
    if (given, wanted) == ("mg", "g"):
        return amount / 1000
    return None


def sodium_from_salt(value: Any, unit: Any = "g") -> float | None:
    """Natrium in mg bij een hoeveelheid zout (in g, of in mg)."""
    grams = number(value)
    if grams is None:
        return None
    if str(unit or "g").strip().lower() == "mg":
        grams /= 1000
    return grams * SODIUM_MG_PER_SALT_G


def _tidy(value: float) -> int | float:
    rounded = round(value, 1)
    return int(rounded) if float(rounded).is_integer() else rounded


def values(data: Any) -> dict[str, float]:
    """De bekende velden uit een dict als `{"calories": "490 kcal", "fatContent": "23"}` (Mealie of schema.org), als getallen in
    de eenheid van het veld. Energie van 0 kcal telt niet: dat is een leeg veld, geen recept zonder calorieën."""
    if not isinstance(data, dict):
        return {}
    found: dict[str, float] = {}
    for key in KEYS:
        amount = convert(key, data.get(key))
        if amount is None or (key == "calories" and not 0 < amount <= MAX_KCAL):
            continue
        found[key] = amount
    return found


def items(data: Any) -> list[dict[str, Any]]:
    """De voedingswaarden voor het scherm: `[{key, quantity, unit}]`, in de volgorde van `FIELDS`."""
    return [{"key": key, "quantity": _tidy(amount), "unit": UNITS[key]} for key, amount in values(data).items()]


def kcal(data: Any) -> float | None:
    """De energie per portie in kcal, of None als die onbekend is."""
    return values(data).get("calories")


def schema(found: dict[str, float]) -> dict[str, Any]:
    """Voor een schema.org-recept (de import in Mealie): `{"@type": "NutritionInformation", "calories": "490 kcal", ...}`.
    Leeg als er niets is, zodat een import zonder voedingswaarden er geen leeg blok bij krijgt."""
    fields = {key: f"{_tidy(found[key])} {UNITS[key]}" for key in KEYS if found.get(key) is not None}
    return {"@type": "NutritionInformation", **fields} if fields else {}


def mealie_fields(found: dict[str, float]) -> dict[str, str]:
    """Zoals Mealie ze zelf bewaart na een import: tekst zonder eenheid (`{"calories": "490"}`)."""
    return {key: str(_tidy(found[key])) for key in KEYS if found.get(key) is not None}


def total(per_serving: float | None, servings: Any) -> int | None:
    """Het totaal van een maaltijd: per portie maal het aantal porties, afgerond op hele kcal."""
    count = number(servings)
    if per_serving is None or not count:
        return None
    return round(per_serving * count)


# --- zout, filteren en optellen (vervolg op #283) ------------------------------------------------------------------------
# Op een verpakking staat zout in gram; Mealie en de bronnen geven natrium in mg (zout = natrium x 2,5). We bewaren natrium,
# zoals Mealie dat veld kent, en rekenen bij het tonen, filteren en in MCP om naar zout.


def salt_g(sodium_mg: float | None) -> float | None:
    """Zout in gram bij een hoeveelheid natrium in mg."""
    return None if sodium_mg is None else sodium_mg / SODIUM_MG_PER_SALT_G


def sodium_mg(salt: float | None) -> float | None:
    """Natrium in mg bij een hoeveelheid zout in gram."""
    return None if salt is None else salt * SODIUM_MG_PER_SALT_G


# De waarden waarop je kunt zoeken, in de volgorde van het scherm: naam in een verzoek (`<naam>Min`, `<naam>Max`, en in MCP
# `<naam>_min`, `<naam>_max`) -> (veld, hoogste grens, decimalen). `salt` is geen veld maar het zout uit `sodiumContent`.
FILTERS: dict[str, tuple[str, int, int]] = {
    "kcal": ("calories", 5000, 0),
    "protein": ("proteinContent", 500, 0),
    "fat": ("fatContent", 500, 0),
    "carbs": ("carbohydrateContent", 500, 0),
    "sugar": ("sugarContent", 500, 0),
    "fiber": ("fiberContent", 200, 0),
    "salt": ("sodiumContent", 50, 1),
}


def value_of(found: dict[str, float], name: str) -> float | None:
    """De waarde voor een filter uit `values()`: het veld, of bij `salt` het zout in gram uit het natrium."""
    field = FILTERS[name][0]
    amount = found.get(field)
    return salt_g(amount) if name == "salt" else amount


def add(totals: dict[str, float], found: dict[str, float], factor: float = 1.0) -> dict[str, float]:
    """Telt de waarden van `found` (maal `factor`) bij `totals` op en geeft `totals` terug."""
    for key, amount in found.items():
        if key in UNITS and amount is not None:
            totals[key] = totals.get(key, 0.0) + amount * factor
    return totals


def scaled(found: dict[str, float], factor: Any) -> dict[str, float] | None:
    """De waarden maal het aantal porties (het totaal van een maaltijd), of None zonder aantal."""
    count = number(factor)
    if not found or not count:
        return None
    return {key: amount * count for key, amount in found.items()}


def brief(found: dict[str, float]) -> dict[str, float | None]:
    """De korte vorm voor een kaart: kcal, eiwit, suiker en zout (g), elk None als het onbekend is."""
    return {name: value_of(found, name) for name in ("kcal", "protein", "sugar", "salt")}
