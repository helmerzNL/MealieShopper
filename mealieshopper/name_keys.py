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

MEMO_SIZE: int = 524_288  # het maximum per functie met `TextMemo`
# De grootte per functie: 1,3 tot 1,5 keer het aantal namen dat een opbouw van de catalogus van elke winkel op de volumedatabase
# (`tests/volume.py`, de schaal van productie) in die cache zet. Is een cache kleiner dan die opbouw, dan verdringt de opbouw zijn
# eigen namen voor ze terugkomen en daalt het aantal treffers meteen naar bijna nul. Een opbouw zet erin: fold 341.326, near 68.249,
# categorize 65.870, offer_category 70.692, _parts 59.979, _size_of 96.208. `tests/test_name_memo.py` bewaakt de ruimte (1,25 keer).
# Alles op 1,5 keer past niet: fold (1,3) en _parts (1,4) zijn kleiner gehouden, want met elke cache vol piekt de app dan op 600 MiB,
# de ontwerpgrens. Nu 559 MiB.
# Een volle cache kost per naam van 27 tekens: fold 235 byte, categorize 227, offer_category 387, _size_of 306, near 712, _parts 1360.
FOLD_SIZE: int = 450_560
CATEGORY_SIZE: int = 98_304
OFFER_CATEGORY_SIZE: int = 98_304
NEAR_SIZE: int = 102_400
PARTS_SIZE: int = 81_920
SIZE_OF_SIZE: int = 147_456
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
        self._defaults: tuple[Any, ...] = self._positional_defaults(self._signature)
        self.size: int = size
        self._cached: Callable[..., _Result] = lru_cache(maxsize=size)(function)

    @staticmethod
    def _positional_defaults(signature: inspect.Signature) -> tuple[Any, ...]:
        """De standaardwaarde per plek (`inspect.Parameter.empty` zonder). Een functie met `*args`, `**kwargs` of een parameter die
        alleen met een keyword kan, past niet: de sleutel is de rij argumenten op volgorde."""
        for parameter in signature.parameters.values():
            if parameter.kind not in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD):
                raise TypeError(f"TextMemo kan geen {parameter.kind.description} parameter aan: {parameter.name}")
        return tuple(parameter.default for parameter in signature.parameters.values())

    def __call__(self, *args: Any, **kwargs: Any) -> _Result:
        if kwargs:
            bound: inspect.BoundArguments = self._signature.bind(*args, **kwargs)
            bound.apply_defaults()  # anders houdt `args` op bij de eerste weggelaten parameter
            args = bound.args
        if len(args) < len(self._defaults):  # `categorize(title)`, `categorize(title, "")` en `categorize(title=title)`: een sleutel
            missing: list[Any] = []
            for default in self._defaults[len(args):]:
                if default is inspect.Parameter.empty:
                    break  # een verplichte parameter ontbreekt: de functie zelf geeft de fout
                missing.append(default)
            args = (*args, *missing)
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
