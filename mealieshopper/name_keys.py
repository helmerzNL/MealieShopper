"""De sleutel van een productnaam: hoe twee schrijfwijzen van dezelfde naam bij elkaar komen.

Een bevestigingsmail van 2022 noemt een product "AH Les pain triomphe heel online" en nu heet het "AH Les pains triomphe heel".
`fold` maakt de naam vergelijkbaar (kleine letters, geen accenten, enkele spaties), `near` laat ook het meervoud, de volgorde en
het woord "online" weg, en `key` is beide achter elkaar. Het gedeelde bestand per winkel (`shared_links`) en de koppeling op naam
(`prices`) gebruiken dezelfde sleutel, zodat ze niet uit elkaar lopen.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache
from typing import Any, Generic, TypeVar

from . import ah_lists

MEMO_SIZE: int = 262_144  # zoveel namen onthoudt elke functie met `TextMemo`: een catalogus van AH heeft er zo'n 70.000
_Result = TypeVar("_Result")


class TextMemo(Generic[_Result]):
    """Een functie van namen die haar antwoord per naam onthoudt. De argumenten worden eerst tekst (zoals de functie zelf al deed
    met `str(value or "")`), dus `None` en getallen kunnen erdoor, en de sleutel is altijd hashbaar. Het antwoord is een
    onveranderlijke waarde (str, tuple of frozenset): een aanroeper kan het gedeelde antwoord niet aanpassen. De functie zelf blijft
    in `__wrapped__`, voor een vergelijking zonder geheugen."""

    def __init__(self, function: Callable[..., _Result]) -> None:
        self.__wrapped__: Callable[..., _Result] = function
        self.__doc__ = function.__doc__
        self.__name__: str = function.__name__
        self._cached: Callable[..., _Result] = lru_cache(maxsize=MEMO_SIZE)(function)

    def __call__(self, *args: Any) -> _Result:
        return self._cached(*(arg if isinstance(arg, str) else str(arg or "") for arg in args))

    def cache_info(self) -> Any:
        return self._cached.cache_info()  # type: ignore[attr-defined]

    def cache_clear(self) -> None:
        self._cached.cache_clear()  # type: ignore[attr-defined]


NEAR_MARK: str = "~"  # voor een sleutel van `near`: geen gevouwen naam begint zo
NEAR_NOISE: frozenset[str] = frozenset(
    {"online"}
)  # "AH Les pain triomphe heel online" in een oude bevestigingsmail


@TextMemo
def fold(value: Any) -> str:
    """Een naam om op te vergelijken: kleine letters, zonder accenten en met enkele spaties. "AH Ah zachte spinazie" in een
    bevestigingsmail heeft het merk twee keer, en dat telt als een keer."""
    words: list[str] = ah_lists._fold(value).split()
    if len(words) > 1 and words[0] == words[1]:
        words = words[1:]
    return " ".join(words)


@TextMemo
def near(name: str) -> str:
    """Een naam om bijna-gelijke namen te herkennen: de woorden in enkelvoud, op volgorde van het alfabet. AH noemde het brood
    in de mails van 2022 "AH Les pain triomphe heel" en nu "AH Les pains triomphe heel": hetzelfde product."""
    from . import line_offers  # pas hier: line_offers importeert product_links, en dat importeert deze module

    return NEAR_MARK + " ".join(sorted(line_offers.words(name) - NEAR_NOISE))


def key(value: Any) -> str:
    """De sleutel waarmee een naam in het gedeelde bestand wordt opgezocht: `near` van de gevouwen naam."""
    return near(fold(value))
