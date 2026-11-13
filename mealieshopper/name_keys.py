"""De sleutel van een productnaam: hoe twee schrijfwijzen van dezelfde naam bij elkaar komen.

Een bevestigingsmail van 2022 noemt een product "AH Les pain triomphe heel online" en nu heet het "AH Les pains triomphe heel".
`fold` maakt de naam vergelijkbaar (kleine letters, geen accenten, enkele spaties), `near` laat ook het meervoud, de volgorde en
het woord "online" weg, en `key` is beide achter elkaar. Het gedeelde bestand per winkel (`shared_links`) en de koppeling op naam
(`prices`) gebruiken dezelfde sleutel, zodat ze niet uit elkaar lopen.
"""

from __future__ import annotations

from collections.abc import Callable
import inspect
from functools import lru_cache, update_wrapper
from typing import Any, Generic, TypeVar

from . import ah_lists

MEMO_SIZE: int = 262_144  # het maximum per functie met `TextMemo`: een catalogus van AH heeft er zo'n 70.000 namen
# De grootte per functie: net boven het aantal namen van een catalogus op de schaal van productie (`tests/volume.py`: AH 74.000, Jumbo
# 26.000), want een kleinere cache raakt bij een tweede opbouw niet (de namen komen in dezelfde volgorde terug en worden steeds
# verdrongen). Een volle cache kost per naam van 27 tekens: fold 235 byte, categorize 227, offer_category 387, _size_of 306, near 712,
# _parts 1360. Gemeten (catalogus van elke winkel gebouwd en daarna elke cache vol): piek 477 MiB, onder de 600 MiB van het ontwerp.
FOLD_SIZE: int = MEMO_SIZE
CATEGORY_SIZE: int = 81_920
NEAR_SIZE: int = 73_728
PARTS_SIZE: int = 65_536
SIZE_OF_SIZE: int = 98_304
STEM_SIZE: int = 32_768  # de woorden van alle namen: een paar duizend verschillende, elk kort
LOCAL_SIZE: int = 8_192  # voor wat alleen binnen een aanroep van een andere onthouden functie terugkomt (`_significant`)
_Result = TypeVar("_Result")


def memo(size: int) -> Callable[[Callable[..., _Result]], "TextMemo[_Result]"]:
    """`@memo(size)`: `TextMemo` met een eigen grootte."""
    return lambda function: TextMemo(function, size)


class TextMemo(Generic[_Result]):
    """Een functie van namen die haar antwoord per naam onthoudt. De argumenten worden eerst tekst (zoals de functie zelf al deed
    met `str(value or "")`), dus `None` en getallen kunnen erdoor, en de sleutel is altijd hashbaar. Het antwoord is een
    onveranderlijke waarde (str, tuple of frozenset): een aanroeper kan het gedeelde antwoord niet aanpassen. De functie zelf blijft
    in `__wrapped__`, voor een vergelijking zonder geheugen."""

    def __init__(self, function: Callable[..., _Result], size: int = MEMO_SIZE) -> None:
        update_wrapper(self, function)
        self._signature: inspect.Signature = inspect.signature(function)
        self.size: int = size
        self._cached: Callable[..., _Result] = lru_cache(maxsize=size)(function)

    def __call__(self, *args: Any, **kwargs: Any) -> _Result:
        if kwargs:  # `categorize(title, hint=x)` en `categorize(title, x)` zijn dezelfde sleutel
            bound: inspect.BoundArguments = self._signature.bind(*args, **kwargs)
            bound.apply_defaults()
            args = bound.args
        return self._cached(*(arg if isinstance(arg, str) else str(arg or "") for arg in args))

    def cache_info(self) -> Any:
        return self._cached.cache_info()  # type: ignore[attr-defined]

    def cache_clear(self) -> None:
        self._cached.cache_clear()  # type: ignore[attr-defined]


NEAR_MARK: str = "~"  # voor een sleutel van `near`: geen gevouwen naam begint zo
NEAR_NOISE: frozenset[str] = frozenset(
    {"online"}
)  # "AH Les pain triomphe heel online" in een oude bevestigingsmail


@memo(FOLD_SIZE)
def fold(value: Any) -> str:
    """Een naam om op te vergelijken: kleine letters, zonder accenten en met enkele spaties. "AH Ah zachte spinazie" in een
    bevestigingsmail heeft het merk twee keer, en dat telt als een keer."""
    words: list[str] = ah_lists._fold(value).split()
    if len(words) > 1 and words[0] == words[1]:
        words = words[1:]
    return " ".join(words)


@memo(NEAR_SIZE)
def near(name: str) -> str:
    """Een naam om bijna-gelijke namen te herkennen: de woorden in enkelvoud, op volgorde van het alfabet. AH noemde het brood
    in de mails van 2022 "AH Les pain triomphe heel" en nu "AH Les pains triomphe heel": hetzelfde product."""
    from . import line_offers  # pas hier: line_offers importeert product_links, en dat importeert deze module

    return NEAR_MARK + " ".join(sorted(line_offers.words(name) - NEAR_NOISE))


def key(value: Any) -> str:
    """De sleutel waarmee een naam in het gedeelde bestand wordt opgezocht: `near` van de gevouwen naam."""
    return near(fold(value))
