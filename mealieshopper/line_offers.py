"""Folderacties voor de hele lijn bij een product in de Prijsgeschiedenis (zie prices.history).

Een folderarchief noemt een actie vaak voor een hele lijn: "Alle Dr. Oetker Casa di Mama pizza's", "Dr. Oetker Casa di Mama".
Zo'n regel staat als eigen folderproduct (`folder:...`) in `offer_history` (price_archive_tidy.py) en hoort bij geen enkel product.
Toch gold de actie voor "Dr. Oetker Casa di Mama pizza salame extra piccante". Hier staat welke folderproducten bij een product
horen: elk betekenisvol woord van de foldernaam staat in een naam van het product.

De regel is bewust streng, want een lijnactie op de verkeerde plek is erger dan een die ontbreekt:

* Woorden die de actie beschrijven en niet het product ("alle", "of", "diverse", "pizza's" telt als "pizza"), verpakkingen
  ("2 dozen", "380 gram") en getallen tellen niet. Er moeten minstens twee woorden overblijven: "Alle kaas" hoort nergens bij.
* Alle woorden moeten erin staan. Een opsomming ("Dr. Oetker Casa di Mama en Tradizionale", "A, B of C") is een actie voor
  elk deel apart: dan moeten alle woorden van een deel erin staan, plus het eerste woord van de naam, meestal het merk. Een
  deel telt alleen met minstens twee eigen woorden naast dat eerste woord: in "AH Biologisch en Excellent soep" hoort "soep"
  ook bij "AH Biologisch", dus dat deel zegt te weinig, en "Tradizionale" alleen ook (`line_alternatives`).
* Een woord dat de categorie van het product noemt mag in de naam ontbreken: "Alle AH Bakkersbrood Les Pains heel" hoort bij
  "AH Les pains triomphe heel" uit Brood & Bakkerij, want "bakkersbrood" eindigt op "brood". Dat mag een woord zijn, en de andere
  woorden moeten er wel staan, minstens drie: "Alle AH Bakkersbrood tijgerbrood heel" en "AH Tijgerbrood heel" horen er niet bij.
* Het huismerk telt als woord: "AH Chips paprika" hoort niet bij "Lay's Paprika chips".
* Een biologisch product krijgt alleen een biologische actie: "AH Rundergehakt" hoort niet bij "AH Biologisch Rundergehakt".
* Noemt de foldernaam een verpakking ("Coca-Cola 1,5 liter") en kent de app de verpakking van het product, dan moeten ze gelijk
  zijn. Een multipack ("4-pack", "6 x 330 ml") hoort alleen bij een multipack.

De Prijsgeschiedenis toont zo'n actie apart van de eigen aanbiedingen van het product, met de foldernaam erbij.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from itertools import combinations

from . import packs, product_links
from .name_keys import LOCAL_SIZE, PARTS_SIZE, STEM_SIZE, memo

MIN_WORDS: int = 2

# Woorden die zeggen hoe de actie werkt of welke vorm het product heeft, niet welk product het is.
_NOT_THE_PRODUCT: frozenset[str] = frozenset(
    [
        "folder",
        "archief",
        "alle",
        "of",
        "en",
        "diverse",
        "divers",
        "varianten",
        "variant",
        "combineren",
        "combinaties",
        "mogelijk",
        "bijv",
        "bijvoorbeeld",
        "naar",
        "keuze",
        "soorten",
        "soort",
        "smaken",
        "smaak",
        "producten",
        "product",
        "artikelen",
        "assortiment",
        "de",
        "het",
        "een",
        "van",
        "met",
        "voor",
        "per",
        "nu",
        "ook",
        "gratis",
        "bij",
        "aankoop",
        "actie",
        "pak",
        "pakken",
        "doos",
        "dozen",
        "zak",
        "zakken",
        "fles",
        "flessen",
        "flesje",
        "flesjes",
        "blik",
        "blikken",
        "blikje",
        "blikjes",
        "stuk",
        "stuks",
        "pack",
        "in",
        "op",
        "uit",
        "los",
        "packs",
        "multipack",
        "multipacks",
        "pakje",
        "pakjes",
        "pot",
        "potten",
        "potje",
        "potjes",
        "krat",
        "kratten",
        "tray",
        "gram",
        "gr",
        "kg",
        "kilo",
        "ml",
        "cl",
        "liter",
    ]
)
ORGANIC: frozenset[str] = frozenset({"biologisch", "biologische", "bio", "organic", "eko"})  # zoals prices.ORGANIC_WORDS
_WORD: re.Pattern[str] = re.compile(r"[a-z0-9]+")
_MULTIPACK: re.Pattern[str] = re.compile(
    r"\d\s*x\s*\d|\b\d+\s*-?\s*(?:pack|packs|pak|pakken)\b|\bmultipack\b"
)


@memo(STEM_SIZE)
def _stem(word: str) -> str:
    """Enkelvoud, grof: "pizzas" en "pizza" (uit "pizza's") tellen als hetzelfde woord, net als "soepen" en "soep"."""
    if len(word) > 4 and word.endswith("en"):
        return word[:-2]
    if len(word) > 3 and word.endswith("s"):
        return word[:-1]
    return word


CATEGORY_WORD_LENGTH: int = 4  # korter zegt te weinig: "vis" zit ook in "advies"
NAMED_WORDS: int = 3  # zoveel woorden van de regel moeten in de naam staan als een (en niet meer dan een) woord de categorie noemt


def category_words(category: str | None) -> frozenset[str]:
    """De woorden van een categorie ("brood_bakkerij": brood, bakkerij), enkelvoud, waarmee een folderwoord de categorie noemt."""
    return frozenset(word for word in (_stem(part) for part in str(category or "").split("_")) if len(word) >= CATEGORY_WORD_LENGTH)


def _tokens(name: str | None) -> tuple[str, ...]:
    folded: str = product_links.fold(name or "")
    return tuple(word for word in _WORD.findall(folded) if len(word) > 1 and not word.isdigit())


def words(name: str | None) -> frozenset[str]:
    """Alle woorden van een naam, gevouwen en enkelvoud, zonder getallen en losse letters."""
    return frozenset(_stem(word) for word in _tokens(name))


_LIST: re.Pattern[str] = re.compile(r",|/|\s(?:en|of)\s")


@memo(LOCAL_SIZE)
def _significant(text: str) -> tuple[str, ...]:
    return tuple(_stem(word) for word in _tokens(text) if word not in _NOT_THE_PRODUCT)


@memo(PARTS_SIZE)
def _parts(title: str | None) -> tuple[tuple[tuple[str, ...], str], ...]:
    """De delen van een foldernaam met de woorden die een product voor dat deel moet hebben (gesorteerd en elk een keer), en de
    tekst van het deel. Gewone tuples van tekst: die volgt de GC na de eerste ronde niet meer, een frozenset wel (zie `Line`)."""
    text: str = str(title or "")
    for prefix in ("Folder: ", "Archief: "):
        text = text.removeprefix(prefix)
    first: tuple[str, ...] = _significant(text)[:1]
    parts: list[str] = [part for part in _LIST.split(product_links.fold(text)) if _significant(part)]
    found: list[tuple[tuple[str, ...], str]] = []
    for part in parts:
        own: frozenset[str] = frozenset(_significant(part))
        needed: tuple[str, ...] = tuple(sorted(own | frozenset(first)))
        if len(parts) > 1 and len(own - frozenset(first)) < MIN_WORDS:
            continue
        if len(needed) >= MIN_WORDS and all(needed != other for other, _ in found):
            found.append((needed, part))
    return tuple(found)


def line_alternatives(title: str | None) -> list[frozenset[str]]:
    """De woorden die een product moet hebben om bij een folderactie te horen: een set per deel van een opsomming, elk met het
    eerste woord van de naam erbij. Een deel met te weinig woorden valt weg, een naam die te weinig zegt geeft niets."""
    return [frozenset(needed) for needed, _ in _parts(title)]


def multipack(name: str | None) -> bool:
    """Noemt de naam meer verpakkingen samen ("4-pack", "6 x 330 ml", "multipack")?"""
    return bool(_MULTIPACK.search(product_links.fold(name or "")))


def _size(title: str) -> tuple[float, str] | None:
    match: re.Match[str] | None = packs._PACK_SIZE_RE.search(product_links.fold(title))
    return packs.parse_pack_size(match.group(0)) if match else None


def _same_size(one: tuple[float, str], other: tuple[float, str]) -> bool:
    return one[1] == other[1] and abs(one[0] - other[0]) <= 0.005 * max(
        one[0], other[0]
    )


# Een regel van de lijnindex: (product, de woorden die een product moet hebben, gesorteerd, de verpakking, meer verpakkingen
# samen). Een gewone tuple en geen NamedTuple of frozenset: CPython volgt een tuple met alleen tekst, getallen, None of zulke
# tuples na de eerste ronde van de GC niet meer, en een volle ronde loopt dan niet langs de tienduizenden regels van AH
# (gc_watch.py). Een NamedTuple en een frozenset volgt hij altijd.
Line = tuple[str, tuple[str, ...], tuple[float, str] | None, bool]


Pair = tuple[str, str]


def _rank(word: str) -> tuple[int, str]:
    """De volgorde van de woorden in de index: het langste eerst, bij gelijke lengte het laatste in het alfabet."""
    return len(word), word


def _top_two(needed: Iterable[str]) -> Pair:
    """De eerste twee woorden op `_rank`: de sleutel van een regel in `LineIndex._pairs`. Een regel heeft er minstens twee."""
    first, second = sorted(needed, key=_rank, reverse=True)[:2]
    return first, second


def _grouped(entries: Iterable[tuple[Pair, Line]]) -> dict[Pair, tuple[Line, ...]]:
    """Regels per sleutel, in tuples: die volgt de GC na de eerste ronde niet meer, een lijst wel."""
    found: dict[Pair, list[Line]] = {}
    for key, line in entries:
        found.setdefault(key, []).append(line)
    return {key: tuple(lines) for key, lines in found.items()}


class LineIndex:
    """De folderproducten van een winkel, op hun langste woord: een product zoekt alleen bij zijn eigen woorden.

    Bij de eerste vraag zoekt de index de regels ook uit op hun twee langste woorden (`_pairs`). Een product kijkt dan alleen naar
    de regels waarvan die twee woorden in zijn naam staan, en niet naar elke regel met een van zijn woorden: op de volumedatabase
    scheelt dat voor de aanbiedingen van een week 2,7 miljoen keer `_fits`."""

    def __init__(self, folder_products: Iterable[tuple[str, str]]) -> None:
        self._by_word: dict[str, list[Line]] = {}
        self._by_ending: dict[str, dict[Pair, tuple[Line, ...]]] = {}  # categoriewoord -> regels op hun andere woorden, zie `_ending`
        self._by_pair: dict[Pair, tuple[Line, ...]] | None = None  # `_pairs`, bij de eerste vraag
        self._vocabulary: frozenset[str] = frozenset()  # elk woord van een regel, samen met `_pairs` uitgezocht
        seen: set[str] = set()
        for product_id, title in folder_products:
            if product_id in seen:
                continue
            seen.add(product_id)
            for needed, part in _parts(title):
                key: str = max(needed, key=lambda word: (len(word), word))
                self._by_word.setdefault(key, []).append((product_id, needed, _size(part), multipack(part)))

    def __len__(self) -> int:
        return sum(len(lines) for lines in self._by_word.values())

    def _pairs(self) -> dict[Pair, tuple[Line, ...]]:
        """Elke regel op zijn twee langste woorden (`_top_two`), een keer uitgezocht bij de eerste vraag, met de woorden van alle
        regels (`_vocabulary`). Een regel past alleen als al zijn woorden in de naam staan (`_fits` zonder categorie), dus ook die twee.

        Dit schrijft in de index, ook vanuit een thread van `compute.run` (`prices._lines_wanted`). Dat is veilig onder de GIL: het
        zet een dict die al helemaal gebouwd is, en twee threads tegelijk bouwen dezelfde. Elke winkel heeft zijn eigen index in
        zijn eigen catalogus."""
        if self._by_pair is None:
            lines: list[Line] = [line for found in self._by_word.values() for line in found]
            self._vocabulary = frozenset(word for line in lines for word in line[1])
            self._by_pair = _grouped((_top_two(line[1]), line) for line in lines)
        return self._by_pair

    def _ending(self, kind: str) -> dict[Pair, tuple[Line, ...]]:
        """De regels met een woord dat op een categoriewoord eindigt ("bakkersbrood" op "brood"), op de twee langste van hun andere
        woorden, een keer uitgezocht per categoriewoord. Zo'n regel past met dat ene woord buiten de naam (`_fits` met categorie),
        dus dan staan de andere woorden er wel in. Alleen regels met genoeg andere woorden (`NAMED_WORDS`): met minder past een regel
        alleen met al zijn woorden in de naam, en die vindt `_pairs` al. Veilig onder de GIL zoals `_pairs`."""
        if kind not in self._by_ending:
            self._pairs()
            ending: frozenset[str] = frozenset(word for word in self._vocabulary if word.endswith(kind))
            self._by_ending[kind] = _grouped(
                (key, line)
                for found in (self._by_word.values() if ending else ())
                for line in found
                if len(line[1]) - 1 >= NAMED_WORDS and not ending.isdisjoint(line[1])
                for key in {_top_two(word for word in line[1] if word != outside) for outside in line[1] if outside in ending}
            )
        return self._by_ending[kind]

    def _pairs_of(self, have: frozenset[str]) -> list[Pair]:
        """De paren van woorden van een naam die een sleutel van `_pairs` of `_ending` kunnen zijn: alleen woorden die in een
        regel voorkomen, en op de volgorde van `_rank`, zoals `_top_two` ze zet."""
        ranked: list[str] = sorted(have & self._vocabulary, key=_rank, reverse=True)
        return list(combinations(ranked, 2))

    def _fits(
        self,
        line: Line,
        have: frozenset[str],
        several: bool,
        sizes: list[tuple[float, str]],
        kinds: frozenset[str] = frozenset(),
    ) -> bool:
        _, needed, size, line_several = line
        missing: list[str] = [word for word in needed if word not in have]
        named: bool = not missing or (
            len(missing) == 1 and len(needed) - 1 >= NAMED_WORDS and all(any(word.endswith(kind) for kind in kinds) for word in missing)
        )
        if line_several != several or not named or (have & ORGANIC and not ORGANIC.intersection(needed)):
            return False
        return (
            size is None
            or not sizes
            or any(_same_size(size, known) for known in sizes)
        )

    def lines_for(
        self, names: Iterable[str], sizes: Iterable[tuple[float, str]] = (), kinds: frozenset[str] = frozenset()
    ) -> list[str]:
        """De folderproducten waarvan elk woord in een van de namen staat, en waarvan de verpakking (als hij er een noemt) bij
        een van de verpakkingen past (als die bekend zijn). Gesorteerd, zodat de uitkomst vastligt."""
        known_sizes: list[tuple[float, str]] = list(sizes)
        found: set[str] = set()
        by_pair: dict[Pair, tuple[Line, ...]] = self._pairs()
        endings: list[dict[Pair, tuple[Line, ...]]] = [ending for ending in (self._ending(kind) for kind in kinds) if ending]
        for name in names:
            have: frozenset[str] = words(name)
            several: bool = multipack(name)
            for pair in self._pairs_of(have):
                found.update(line[0] for line in by_pair.get(pair, ()) if self._fits(line, have, several, known_sizes))
                for ending in endings:  # een woord van de regel noemt de categorie van het product (`category_words`)
                    found.update(line[0] for line in ending.get(pair, ()) if self._fits(line, have, several, known_sizes, kinds))
        return sorted(found)


# --- de prijs per stuk van een lijnactie ---------------------------------------------------------------------------------
#
# Het folderarchief is hier niet consequent: "2 voor 6.00" staat er de ene week als 3.00 per stuk (was 4.29), de andere week
# als 6.00 (was 8.58), en soms zonder prijs, alleen als tekst ("1+1 gratis"). `per_piece` maakt er per stuk van: eerst uit de
# actietekst, dan uit de prijzen van het archief, en een prijs die het totaal van meer stuks is, herkent hij aan de gewone
# prijs in de weken eromheen.

_MONEY: str = r"(\d+[.,]\d{2})"
_N_FOR: re.Pattern[str] = re.compile(r"(\d+)\s*voor:?\s*" + _MONEY)
_FOR: re.Pattern[str] = re.compile(r"voor:?\s*" + _MONEY)  # na `_N_FOR`: "voor 1.99 per stuk"
_FREE: re.Pattern[str] = re.compile(r"(\d+)\s*\+\s*(\d+)\s*(?:gratis|kosteloos)")
_HALF: re.Pattern[str] = re.compile(r"2e\s*halve[\s-]*prijs")
_PERCENT: re.Pattern[str] = re.compile(r"(\d{1,2})\s*%\s*korting")
_WAS: re.Pattern[str] = re.compile(r"\bwas\s+(?:(\d+)\s*stuks\s+)?" + _MONEY)
_ACTION: re.Pattern[str] = re.compile(r"\bactie\s+" + _MONEY)
_COUNT: re.Pattern[str] = re.compile(r"\b(\d+)\s*(?:stuks|dozen|pakken)\b")
NEIGHBOURS: int = 6  # zoveel lijnacties met een gewone prijs, het dichtst in de tijd, geven de gewone prijs van een week
OTHER_PRODUCT: float = 0.3  # een gewone prijs zoveel onder of boven die van de weken eromheen: de actie gold een ander product


def _money(value: str) -> float:
    return float(value.replace(",", "."))


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else None


def _from_text(label: str) -> tuple[float | None, float | None, tuple[int, int] | None, float | None, bool]:
    """Uit de actietekst: (prijs per stuk, gewone prijs per stuk, gratis als (betaald, totaal), korting als factor, en of de prijs
    een totaal kan zijn: "actie 5.00" zonder aantal)."""
    text: str = label.lower()
    pieces: int | None = None
    price: float | None = None
    free: tuple[int, int] | None = None
    factor: float | None = None
    if found := _N_FOR.search(text):
        pieces = int(found[1])
        price = _money(found[2]) / pieces if pieces else None
    elif found := _FREE.search(text):
        paid, extra = int(found[1]), int(found[2])
        free = (paid, paid + extra)
        pieces = paid + extra
    elif _HALF.search(text):
        factor, pieces = 0.75, 2
    elif found := _PERCENT.search(text):
        factor = 1 - int(found[1]) / 100
    elif found := _FOR.search(text):
        price = _money(found[1])
    regular: float | None = None
    if found := _WAS.search(text):
        count = int(found[1]) if found[1] else (pieces or _count(text))
        regular = _money(found[2]) / count if count else _money(found[2])
    maybe_total: bool = False
    if price is None and free is None and factor is None and (found := _ACTION.search(text)):
        count = pieces or _count(text)
        price = _money(found[1]) / count if count else _money(found[1])
        maybe_total = not count
    return price, regular, free, factor, maybe_total


def _count(text: str) -> int | None:
    found = _COUNT.search(text)
    return int(found[1]) if found and int(found[1]) > 1 else None


def _median(values: list[float]) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def _local_regular(regulars: list[tuple[str, float]], day: str) -> float | None:
    """De gewone prijs rond een dag: de mediaan van de `NEIGHBOURS` lijnacties met een gewone prijs die er het dichtst bij liggen."""
    if not regulars:
        return None
    nearest = sorted(regulars, key=lambda item: abs(_days(item[0]) - _days(day)))[:NEIGHBOURS]
    return _median([value for _, value in nearest])


def _days(day: str) -> int:
    year, month, date = (int(part) for part in day[:10].split("-"))
    return year * 372 + month * 31 + date  # alleen om afstanden te vergelijken


def _as_pieces(value: float | None, typical: float | None) -> tuple[float | None, int]:
    """Een gewone prijs die het totaal van 2 tot 4 stuks is (8.58 waar het er 4.29 hoort te zijn): per stuk, met het aantal."""
    if value is None or typical is None or value < typical * 1.6:
        return value, 1
    pieces = round(value / typical)
    if 2 <= pieces <= 4 and abs(value / pieces - typical) <= 0.25 * typical:
        return value / pieces, pieces
    return value, 1


def per_piece(lines: list[dict]) -> list[dict]:
    """Lijnacties met `price` en `priceBefore` per stuk, en `priceSource`: "text" (uit de actietekst), "archive" (de prijs van het
    archief), "regular" (berekend uit de gewone prijs in de weken eromheen) of None (geen prijs te geven). Oudste eerst.

    Een lijnactie waarvan de gewone prijs meer dan `OTHER_PRODUCT` afwijkt van die in de weken eromheen, valt weg: "Diverse Dr.
    Oetker" met brownies van 2.09 is geen actie voor een pizza van 4.19."""
    first: list[tuple[dict, float | None, float | None, tuple[int, int] | None, float | None]] = []
    for line in lines:
        price, regular, free, factor, maybe_total = _from_text(str(line.get("promoLabel") or ""))
        source = ("total" if maybe_total else "text") if price is not None else None
        if price is None and free is None and factor is None:
            price = _number(line.get("price"))
            source = "total" if price is not None else None
        regular = regular if regular is not None else _number(line.get("priceBefore"))
        first.append(({**line, "priceSource": source}, price, regular, free, factor))
    plain = [(str(line["start"]), regular) for line, _, regular, _, _ in first if regular is not None]
    typical_all = _median([value for _, value in plain])
    corrected = [(day, _as_pieces(value, typical_all)[0]) for day, value in plain]
    result: list[dict] = []
    for line, price, regular, free, factor in first:
        typical = _local_regular(corrected, str(line["start"]))
        regular, pieces = _as_pieces(regular, typical)
        if price is not None and pieces > 1 and line["priceSource"] == "total":
            price = price / pieces
        if line["priceSource"] == "total":
            line["priceSource"] = "archive" if line.get("price") is not None and price == _number(line.get("price")) else "text"
        if regular is not None and typical is not None and abs(regular - typical) > OTHER_PRODUCT * typical:
            continue
        if regular is None:
            regular = typical
        if price is None and regular is not None and (free or factor):
            price = regular * free[0] / free[1] if free else regular * factor
            line["priceSource"] = "regular"
        if price is not None and regular is not None and price > regular * 1.05:
            price, line["priceSource"] = None, None  # een "actieprijs" boven de gewone prijs is een totaal dat we niet kunnen delen
        line["price"] = round(price, 2) if price is not None else None
        line["priceBefore"] = round(regular, 2) if regular is not None else None
        result.append(line)
    return result
