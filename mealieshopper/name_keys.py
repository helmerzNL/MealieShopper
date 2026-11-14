"""De sleutel van een productnaam: hoe twee schrijfwijzen van dezelfde naam bij elkaar komen.

Een bevestigingsmail van 2022 noemt een product "AH Les pain triomphe heel online" en nu heet het "AH Les pains triomphe heel".
`fold` maakt de naam vergelijkbaar (kleine letters, geen accenten, enkele spaties), `near` laat ook het meervoud, de volgorde en
het woord "online" weg, en `key` is beide achter elkaar. Het gedeelde bestand per winkel (`shared_links`) en de koppeling op naam
(`prices`) gebruiken dezelfde sleutel, zodat ze niet uit elkaar lopen.
"""

from __future__ import annotations

from collections.abc import Callable
import inspect
from functools import update_wrapper
from typing import Any, Generic, NamedTuple, TypeVar

from . import ah_lists

MEMO_SIZE: int = 524_288  # hoeveel namen `TextMemo` hooguit vasthoudt als een functie niets opgeeft
# De grootte per functie, de namen die de memo hooguit vasthoudt: 1,3 tot 1,5 keer het aantal namen dat een opbouw van de
# catalogus van elke winkel op de volumedatabase (`tests/volume.py`, de schaal van productie) erin zet, zodat een volgende opbouw
# alles terugvindt. Een opbouw zet erin: fold 341.326, near 68.249, categorize 65.870, offer_category 70.692, _parts 59.979,
# _size_of 96.208. `tests/test_name_memo.py` eist dat een tweede opbouw geen enkele naam opnieuw uitrekent.
# Het geheugen: een gewone dict kost per naam de sleutel, het antwoord en een plek in de tabel, zonder de schakel van lru_cache.
# Het slechtste geval: de volumedatabase met alle catalogi gebouwd, en dan elke memo tot `size` gevuld met namen die niet in de
# catalogus staan. Een testproces piekt dan op 371 MiB (3.12) of 398 MiB (3.15), gemeten in taak 9c. Met twee generaties van
# elk `size` namen, die naast elkaar bleven staan, was dat 589 en 619 MiB.
FOLD_SIZE: int = 450_560
CATEGORY_SIZE: int = 98_304
OFFER_CATEGORY_SIZE: int = 98_304
NEAR_SIZE: int = 102_400
PARTS_SIZE: int = 81_920
SIZE_OF_SIZE: int = 147_456
STEM_SIZE: int = 32_768  # de woorden van alle namen: een paar duizend verschillende, elk kort
LOCAL_SIZE: int = 8_192  # voor wat alleen binnen een aanroep van een andere onthouden functie terugkomt (`_significant`)
_Result = TypeVar("_Result")
_MISSING: object = object()  # voor `dict.get`: None kan een antwoord zijn


class MemoInfo(NamedTuple):
    """Wat `TextMemo.cache_info` geeft: de velden van `functools.lru_cache` en `evicted`."""

    hits: int
    misses: int
    maxsize: int
    currsize: int  # de namen in beide generaties samen: een naam staat in een van de twee
    evicted: int  # hoeveel namen eruit gingen omdat de memo vol was (`TextMemo`): binnen een opbouw hoort dat 0 te zijn


def memo(size: int) -> Callable[[Callable[..., _Result]], "TextMemo[_Result]"]:
    """`@memo(size)`: `TextMemo` met een eigen grootte."""
    return lambda function: TextMemo(function, size)


class TextMemo(Generic[_Result]):
    """Een functie van namen die haar antwoord per naam onthoudt. De argumenten worden eerst tekst (zoals de functie zelf al deed
    met `str(value or "")`), dus `None` en getallen kunnen erdoor, en de sleutel is altijd hashbaar. Het antwoord is een
    onveranderlijke waarde (str, tuple of frozenset): een aanroeper kan het gedeelde antwoord niet aanpassen. De functie zelf blijft
    in `__wrapped__`, voor een vergelijking zonder geheugen.

    Het geheugen is een gewone dict en geen `functools.lru_cache`: die houdt per naam een schakel bij die de garbage collector bij
    elke volle ronde langsloopt (770.000 namen op de volumedatabase: 55 ms per ronde, gemeten), en zo'n ronde houdt de GIL vast
    (gc_watch.py). Een dict met tekst als sleutel en tekst of tuples van tekst als waarde slaat de GC over (3.12) of loopt hij snel
    door. Er zijn twee generaties: een naam wordt eerst in de nieuwe gezocht en dan in de oude, en een treffer in de oude verhuist
    naar de nieuwe. Een naam staat dus in een van de twee, en de nieuwe heeft alles wat sinds de laatste wissel terugkwam.

    Zijn er na een misser samen meer dan `size` namen, dan gaat er een naam uit de oude generatie: de oudste die sinds de
    wissel niet terugkwam (`evicted` telt ze). Is de oude leeg, dan wordt eerst de nieuwe de oude. Zo houdt de memo `size` namen
    vast en gaat bij een volle memo telkens een naam weg die het langst niet terugkwam, ongeveer zoals bij lru_cache, zonder
    een schakel per naam. De volgorde van de oude generatie staat in een tuple van haar sleutels (`_order`): een tuple van tekst
    volgt de GC niet, en zo kost het weghalen van de oudste geen zoektocht door de dict. Niet `dict.popitem`: dat haalt de
    jongste weg, en na een wissel zijn dat net de namen van de opbouw die nog bezig is.

    De groottes bovenaan zijn ruim genoeg voor de namen van een opbouw van alle catalogi: `tests/test_name_memo.py` eist dat een
    tweede opbouw niets mist, ook als de memo's al vol zaten met andere namen."""

    def __init__(self, function: Callable[..., _Result], size: int = MEMO_SIZE) -> None:
        update_wrapper(self, function)
        self._signature: inspect.Signature = inspect.signature(function)
        self._defaults: tuple[Any, ...] = self._positional_defaults(self._signature)
        self.size: int = size
        self._function: Callable[..., _Result] = function
        self._new: dict[Any, _Result] = {}
        self._old: dict[Any, _Result] = {}
        self._order: tuple[Any, ...] = ()  # de sleutels van de oude generatie op volgorde van binnenkomst, bij de wissel vastgelegd
        self._next: int = 0  # waar `_evict` in `_order` verder zoekt
        self._hits: int = 0
        self._misses: int = 0
        self._evicted: int = 0

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
        texts: tuple[str, ...] = tuple(arg if isinstance(arg, str) else str(arg or "") for arg in args)
        key: Any = texts[0] if len(texts) == 1 else texts  # zoals lru_cache: een enkele tekst is zelf de sleutel
        found: Any = self._new.get(key, _MISSING)
        if found is not _MISSING:
            self._hits += 1
            return found
        found = self._old.pop(key, _MISSING)
        if found is not _MISSING:  # verhuist naar de nieuwe generatie: samen blijven het evenveel namen
            self._hits += 1
            self._new[key] = found
            return found
        self._misses += 1
        result: _Result = self._function(*texts)
        self._keep(key, result)
        return result

    def _keep(self, key: Any, value: _Result) -> None:
        """Bewaart een nieuw antwoord. Zijn er dan samen meer dan `size` namen, dan gaat er een naam uit (`_evict`)."""
        self._new[key] = value
        if len(self._new) + len(self._old) > self.size:
            self._evict()

    def _evict(self) -> None:
        """Haalt de oudste naam uit de oude generatie die er nog in staat. Is de oude leeg, dan wordt eerst de nieuwe de oude."""
        if not self._old:
            self._old, self._new = self._new, {}
            self._order, self._next = tuple(self._old), 0
        while self._next < len(self._order):
            key: Any = self._order[self._next]
            self._next += 1
            if self._old.pop(key, _MISSING) is not _MISSING:  # anders kwam hij terug en staat hij in de nieuwe
                self._evicted += 1
                return

    def cache_info(self) -> MemoInfo:
        return MemoInfo(self._hits, self._misses, self.size, len(self._new) + len(self._old), self._evicted)

    def cache_clear(self) -> None:
        self._new = {}
        self._old = {}
        self._order = ()
        self._next = 0
        self._hits = 0
        self._misses = 0
        self._evicted = 0


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
