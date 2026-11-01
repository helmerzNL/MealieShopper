"""Ingrediëntregels uit Mealie begrijpelijk maken voor het zoeken en het boodschappenmandje.

Een recept dat Mealie niet heeft laten analyseren bewaart een regel als "200 ml melk" in de notitie, met
aantal 1 (Mealie's standaard) en zonder eenheid of ingrediënt. De weergave wordt dan "1 200 ml melk", en
het zoeken in de winkel gaat op die hele tekst en vindt niets. Hier halen we aantal, eenheid en naam uit
zo'n regel, zodat hij zich gedraagt als een door Mealie zelf geanalyseerde regel ("200 ml melk").

Een regel die Mealie al wel analyseerde (met een ingrediënt of een eenheid) blijft ongemoeid.
"""
from __future__ import annotations

import math
import re
from typing import Any

from .i18n import current_lang

_FRACTIONS = {
    "½": 0.5, "¼": 0.25, "¾": 0.75, "⅓": 1 / 3, "⅔": 2 / 3,
    "⅛": 0.125, "⅜": 0.375, "⅝": 0.625, "⅞": 0.875,
}
_FRACTION_CLASS = "".join(_FRACTIONS)

_NUMBER = (
    rf"(?:\d+[ \u00a0]+\d+/\d+|\d+/\d+|\d+(?:[.,]\d+)?[ \u00a0]?[{_FRACTION_CLASS}]|[{_FRACTION_CLASS}]|\d+(?:[.,]\d+)?)"
)
_AMOUNT_RE = re.compile(
    rf"^\s*(?:ca\.?|circa|ongeveer|\u00b1|~)?\s*(?P<a>{_NUMBER})(?:\s*(?:-|\u2013|tot)\s*(?P<b>{_NUMBER}))?",
    re.IGNORECASE,
)
# "2 x tomaten": de x hoort bij het aantal.
_TIMES_RE = re.compile(r"^\s*[x\u00d7]\s+", re.IGNORECASE)

# Eenheden zoals ze in Nederlandse (en wat Engelse) recepten staan, zonder punt en in kleine letters.
UNITS = frozenset(
    """
    g gr gram kg kilo kilogram mg ml milliliter cl centiliter dl deciliter l liter
    el eetlepel eetlepels tl theelepel theelepels kl koffielepel koffielepels
    st stuk stuks snufje snufjes snuf mespunt mespuntje
    teen tenen teentje teentjes takje takjes tak takken bosje bosjes bos bos
    blik blikje blikjes blikken pak pakje pakjes pakken zak zakje zakjes plak plakje plakjes plakken
    schijf schijfje schijfjes schijven bol bolletje bolletjes kop kopje kopjes handje handjes
    scheut scheutje scheutjes klont klontje klontjes rol rolletje pot potje potjes bak bakje bakjes
    stengel stengels steel stelen blad blaadjes blaadje bundel bundeltje doos doosje fles flesje
    cup cups oz lb tbsp tsp pinch clove cloves can cans slice slices bunch sprig sprigs
    """.split()
)

_LEADING_FILLER_RE = re.compile(r"^(?:van|aan|of|à|a)\s+(?:de\s+|het\s+|een\s+)?", re.IGNORECASE)
_PARENS_RE = re.compile(r"\s*\([^)]*\)")


def _to_float(text: str) -> float | None:
    text = text.replace("\u00a0", " ").strip()
    if not text:
        return None
    total = 0.0
    fraction = text[-1] if text[-1] in _FRACTIONS else ""
    if fraction:
        total += _FRACTIONS[fraction]
        text = text[:-1].strip()
        if not text:
            return total
    try:
        if " " in text:
            whole, part = text.split(None, 1)
            top, bottom = part.split("/")
            return total + float(whole) + float(top) / float(bottom)
        if "/" in text:
            top, bottom = text.split("/")
            return total + float(top) / float(bottom)
        return total + float(text.replace(",", "."))
    except (ValueError, ZeroDivisionError):
        return None


def parse_line(text: Any) -> dict[str, Any] | None:
    """Splits "200 ml melk, lauw" in aantal, eenheid, naam en rest; None zonder aantal vooraan.

    Een bereik ("2-3 el") geeft het hoogste aantal: liever te veel dan te weinig kopen.
    """
    line = str(text or "").strip()
    match = _AMOUNT_RE.match(line)
    if not match:
        return None
    first = _to_float(match.group("a"))
    second = _to_float(match.group("b")) if match.group("b") else None
    quantity = max(first, second) if first is not None and second is not None else first
    if quantity is None or quantity <= 0:
        return None
    rest = _TIMES_RE.sub("", line[match.end():]).strip()
    if _AMOUNT_RE.match(rest):
        return None  # "2 x 400 g tomaten": twee hoeveelheden, dat laten we aan Mealie

    unit = ""
    word = re.match(r"^([^\W\d_]+)\.?(?=$|[\s,;(])", rest)
    if word and word.group(1).lower() in UNITS:
        unit = word.group(1).lower()
        rest = rest[word.end():].strip()
    rest = _LEADING_FILLER_RE.sub("", rest) if unit else rest

    name, _, extra = rest.partition(",")
    name = _PARENS_RE.sub("", name).strip(" .;")
    extra = extra.strip()
    if not name:
        # Zo'n regel heeft geen ingrediënt ("200 ml"): laat Mealie's eigen weergave gelden.
        return None
    return {"quantity": quantity, "unit": unit, "name": name, "extra": extra}


# Hoe Mealie een eenheid noemt: de korte vorm als sleutel, met de volledige naam waaronder hij wordt aangemaakt.
_UNIT_SHORT = {
    "gr": "g", "gram": "g", "kilo": "kg", "kilogram": "kg", "milliliter": "ml", "centiliter": "cl",
    "deciliter": "dl", "liter": "l", "eetlepel": "el", "eetlepels": "el", "theelepel": "tl", "theelepels": "tl",
    "koffielepel": "kl", "koffielepels": "kl", "stuk": "st", "stuks": "st", "tbsp": "el", "tsp": "tl",
}
_UNIT_NAME = {
    "g": "gram", "kg": "kilogram", "mg": "milligram", "ml": "milliliter", "cl": "centiliter", "dl": "deciliter",
    "l": "liter", "el": "eetlepel", "tl": "theelepel", "kl": "koffielepel", "st": "stuk",
}


def unit_lookup_names(unit: str) -> list[str]:
    """Namen waaronder een eenheid in Mealie kan staan, de meest letterlijke eerst: `gr` is ook `g` en `gram`."""
    typed = str(unit or "").strip().lower()
    short = _UNIT_SHORT.get(typed, typed)
    return [name for name in dict.fromkeys((typed, short, _UNIT_NAME.get(short, ""))) if name]


def unit_definition(unit: str) -> dict[str, str]:
    """Eenheid zoals we hem in Mealie aanmaken als die er nog niet is: `gr` wordt gram (g)."""
    typed = str(unit or "").strip().lower()
    short = _UNIT_SHORT.get(typed, typed)
    if short in _UNIT_NAME:
        return {"name": _UNIT_NAME[short], "abbreviation": short}
    return {"name": typed, "abbreviation": ""}


def format_quantity(value: float) -> str:
    """"200", "1,5" of "0,33"; in het Engels met een punt."""
    rounded = round(float(value), 2)
    text = str(int(rounded)) if rounded == int(rounded) else f"{rounded:.2f}".rstrip("0")
    return text.replace(".", ",") if current_lang() == "nl" else text


def _blank(value: Any) -> bool:
    if isinstance(value, dict):
        return not str(value.get("name") or value.get("abbreviation") or "").strip()
    return not value


def _line_text(quantity: float | None, unit: str, name: str, note: str) -> str:
    """"200 g bloem, gezeefd": aantal, eenheid en ingrediënt, met de notitie na een komma.

    Zonder ingrediënt is de notitie zelf het ingrediënt ("4 uien" uit een regel die Mealie niet analyseerde).
    """
    head = " ".join(part for part in (format_quantity(quantity) if quantity else "", unit, name) if part)
    note = " ".join(str(note or "").split())
    if not note:
        return head
    return f"{head}, {note}" if name else f"{head} {note}".strip()


def _unit_label(unit: Any, plural: bool) -> str:
    """De eenheid zoals Mealie haar toont: de afkorting als die aanstaat, en het meervoud als Mealie dat kent."""
    if not isinstance(unit, dict):
        return str(unit or "").strip()
    abbreviation = str(unit.get("abbreviation") or "").strip()
    if unit.get("useAbbreviation") and abbreviation:
        return str(unit.get("pluralAbbreviation") or "").strip() if plural and unit.get("pluralAbbreviation") else abbreviation
    name = str(unit.get("name") or "").strip()
    return str(unit.get("pluralName") or "").strip() if plural and unit.get("pluralName") else name or abbreviation


def _food_label(food: Any, plural: bool) -> str:
    if not isinstance(food, dict):
        return str(food or "").strip()
    name = str(food.get("name") or "").strip()
    return str(food.get("pluralName") or "").strip() if plural and food.get("pluralName") else name


def _amount(value: Any) -> float | None:
    """Het aantal van een regel als het een echt getal boven nul is; anders None (ook voor True en False)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and value > 0 else None


def scale_ingredient(ingredient: Any, factor: float) -> Any:
    """Een kopie van een regel met het aantal maal `factor` en een nieuwe weergave ("200 g bloem" x 2 = "400 g bloem").

    Een regel zonder aantal ("zout naar smaak") of een factor van 1 komt ongewijzigd terug.
    """
    if not isinstance(ingredient, dict) or factor == 1:
        return ingredient
    quantity = _amount(ingredient.get("quantity"))
    if quantity is None:
        return ingredient
    scaled = round(quantity * factor, 4)
    unit = _unit_label(ingredient.get("unit"), scaled > 1)
    food = _food_label(ingredient.get("food"), scaled > 1 and not unit)
    return {
        **ingredient,
        "quantity": scaled,
        "display": _line_text(scaled, unit, food, ingredient.get("note") or ""),
        "unscaledQuantity": quantity,
    }


def scale_recipe(recipe: Any, factor: float) -> Any:
    """Een kopie van een recept met alle regels omgerekend, voor de porties van een maaltijd in het weekmenu.

    Het recept zelf verandert niet: hetzelfde recept kan twee keer in de week staan, met een andere schaal. Staan de
    hoeveelheden in Mealie uit (`disableAmount`), dan rekent Mealie ook niets om, en wij dus ook niet.
    """
    if not isinstance(recipe, dict) or factor == 1 or not isinstance(recipe.get("recipeIngredient"), list):
        return recipe
    if (recipe.get("settings") or {}).get("disableAmount"):
        return recipe
    return {**recipe, "recipeIngredient": [scale_ingredient(item, factor) for item in recipe["recipeIngredient"]]}


def normalize(ingredient: Any) -> Any:
    """Geef een regel die Mealie niet analyseerde dezelfde vorm als een geanalyseerde regel.

    Alleen een regel zonder ingrediënt en zonder eenheid, met de tekst in de notitie en een aantal dat nog
    Mealie's standaard is (leeg, 0 of 1), wordt aangepast. Al het andere komt ongewijzigd terug.
    """
    if not isinstance(ingredient, dict) or not _blank(ingredient.get("food")) or not _blank(ingredient.get("unit")):
        return ingredient
    quantity = ingredient.get("quantity")
    if quantity not in (None, 0, 1, 1.0):
        return ingredient
    text = str(ingredient.get("note") or ingredient.get("originalText") or "").strip()
    parsed = parse_line(text)
    if not parsed and text and text.split(None, 1)[0].lower().rstrip(".") in UNITS:
        parsed = parse_line(f"1 {text}")  # "snufje zout": de eenheid zonder aantal betekent één
    if not parsed:
        return ingredient

    unit = {"name": parsed["unit"], "abbreviation": parsed["unit"]} if parsed["unit"] else None
    return {
        **ingredient,
        "quantity": parsed["quantity"],
        "unit": unit,
        "food": {"name": parsed["name"]},
        "note": parsed["extra"],
        "display": _line_text(parsed["quantity"], parsed["unit"], parsed["name"], parsed["extra"]),
        "analyzedByMealieShopper": True,
    }


def normalize_recipe(recipe: Any) -> Any:
    """Past `normalize` toe op alle regels van een Mealie-recept."""
    if isinstance(recipe, dict) and isinstance(recipe.get("recipeIngredient"), list):
        recipe["recipeIngredient"] = [normalize(item) for item in recipe["recipeIngredient"]]
    return recipe
