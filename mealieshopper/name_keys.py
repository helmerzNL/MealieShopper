"""De sleutel van een productnaam: hoe twee schrijfwijzen van dezelfde naam bij elkaar komen.

Een bevestigingsmail van 2022 noemt een product "AH Les pain triomphe heel online" en nu heet het "AH Les pains triomphe heel".
`fold` maakt de naam vergelijkbaar (kleine letters, geen accenten, enkele spaties), `near` laat ook het meervoud, de volgorde en
het woord "online" weg, en `key` is beide achter elkaar. Het gedeelde bestand per winkel (`shared_links`) en de koppeling op naam
(`prices`) gebruiken dezelfde sleutel, zodat ze niet uit elkaar lopen.
"""

from __future__ import annotations

from typing import Any

from . import ah_lists, line_offers

NEAR_MARK: str = "~"  # voor een sleutel van `near`: geen gevouwen naam begint zo
NEAR_NOISE: frozenset[str] = frozenset(
    {"online"}
)  # "AH Les pain triomphe heel online" in een oude bevestigingsmail


def fold(value: Any) -> str:
    """Een naam om op te vergelijken: kleine letters, zonder accenten en met enkele spaties. "AH Ah zachte spinazie" in een
    bevestigingsmail heeft het merk twee keer, en dat telt als een keer."""
    words: list[str] = ah_lists._fold(value).split()
    if len(words) > 1 and words[0] == words[1]:
        words = words[1:]
    return " ".join(words)


def near(name: str) -> str:
    """Een naam om bijna-gelijke namen te herkennen: de woorden in enkelvoud, op volgorde van het alfabet. AH noemde het brood
    in de mails van 2022 "AH Les pain triomphe heel" en nu "AH Les pains triomphe heel": hetzelfde product."""
    return NEAR_MARK + " ".join(sorted(line_offers.words(name) - NEAR_NOISE))


def key(value: Any) -> str:
    """De sleutel waarmee een naam in het gedeelde bestand wordt opgezocht: `near` van de gevouwen naam."""
    return near(fold(value))
