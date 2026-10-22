"""De soorten gerecht waarop de receptvoorstellen te filteren zijn (ontbijt, lunch, hoofdgerecht ...).

Elke receptbron noemt de gang anders: Jumbo geeft een `course`, Mealie categorieën en tags die je zelf hebt aangemaakt, AH een
`recipeCategory` in het schema.org-recept en Picnic alleen losse labels. We brengen die vrije tekst naar een vaste lijst. De regels
staan op volgorde: de eerste die past wint, dus een recept met "Lunch, Hoofdgerecht" valt bij Lunch. Past niets, dan is het `overig`.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

# Ook de volgorde waarin de keuzes boven de recepten staan.
COURSES = ("ontbijt", "lunch", "voorgerecht", "soep", "hoofdgerecht", "bijgerecht", "dessert", "snack", "overig")
OTHER = "overig"

# (soort, patroon op de tekst zonder accenten en in kleine letters). Alleen categorieën en labels, nooit de titel van het recept.
_RULES: tuple[tuple[str, str], ...] = (
    ("ontbijt", r"ontbijt|breakfast|brunch"),
    ("dessert", r"dessert|nagerecht|toetje|gebak|pudding|\bcake\b|\bsweet\b|zoete lekkernij"),
    ("soep", r"\bsoep|\bsoup"),
    ("voorgerecht", r"voorgerecht|starter|appetizer|amuse"),
    ("bijgerecht", r"bijgerecht|side dish|\bsides?\b"),
    ("snack", r"snack|borrel|tussendoor|hapje|tapas|finger ?food"),
    ("lunch", r"lunch|broodmaaltijd|sandwich|tosti"),
    ("hoofdgerecht", r"hoofdgerecht|hoofdmaaltijd|\bmain\b|\bdinner\b|\bdiner\b|avondeten|avondmaal|warme maaltijd"),
)
_COMPILED = tuple((course, re.compile(pattern)) for course, pattern in _RULES)


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def _texts(value: Any) -> list[str]:
    """De teksten in wat een bron meegeeft: een tekst, een lijst, of objecten met een `name` (zo geeft Mealie categorieën)."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return _texts(value.get("name"))
    if isinstance(value, (list, tuple, set)):
        return [text for item in value for text in _texts(item)]
    return []


def course_of(*labels: Any) -> str:
    """De soort gerecht bij de labels van een recept; `overig` als er niets herkenbaars bij staat."""
    text = " | ".join(_fold(text) for label in labels for text in _texts(label))
    if text.strip():
        for course, pattern in _COMPILED:
            if pattern.search(text):
                return course
    return OTHER


def summarize(courses: list[str]) -> list[dict[str, Any]]:
    """Hoeveel recepten per soort, in de vaste volgorde van `COURSES`. Soorten zonder recepten ontbreken."""
    counts: dict[str, int] = {}
    for course in courses:
        counts[course] = counts.get(course, 0) + 1
    return [{"id": course, "count": counts[course]} for course in COURSES if counts.get(course)]
