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
* Het huismerk telt als woord: "AH Chips paprika" hoort niet bij "Lay's Paprika chips".
* Een biologisch product krijgt alleen een biologische actie: "AH Rundergehakt" hoort niet bij "AH Biologisch Rundergehakt".
* Noemt de foldernaam een verpakking ("Coca-Cola 1,5 liter") en kent de app de verpakking van het product, dan moeten ze gelijk
  zijn. Een multipack ("4-pack", "6 x 330 ml") hoort alleen bij een multipack.

De Prijsgeschiedenis toont zo'n actie apart van de eigen aanbiedingen van het product, met de foldernaam erbij.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import NamedTuple

from . import packs, product_links

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


def _stem(word: str) -> str:
    """Enkelvoud, grof: "pizzas" en "pizza" (uit "pizza's") tellen als hetzelfde woord, net als "soepen" en "soep"."""
    if len(word) > 4 and word.endswith("en"):
        return word[:-2]
    if len(word) > 3 and word.endswith("s"):
        return word[:-1]
    return word


def _tokens(name: str | None) -> list[str]:
    folded: str = product_links.fold(name or "")
    return [word for word in _WORD.findall(folded) if len(word) > 1 and not word.isdigit()]


def words(name: str | None) -> frozenset[str]:
    """Alle woorden van een naam, gevouwen en enkelvoud, zonder getallen en losse letters."""
    return frozenset(_stem(word) for word in _tokens(name))


_LIST: re.Pattern[str] = re.compile(r",|/|\s(?:en|of)\s")


def _significant(text: str) -> list[str]:
    return [_stem(word) for word in _tokens(text) if word not in _NOT_THE_PRODUCT]


def _parts(title: str | None) -> list[tuple[frozenset[str], str]]:
    """De delen van een foldernaam met de woorden die een product voor dat deel moet hebben, en de tekst van het deel."""
    text: str = str(title or "")
    for prefix in ("Folder: ", "Archief: "):
        text = text.removeprefix(prefix)
    first: list[str] = _significant(text)[:1]
    parts: list[str] = [part for part in _LIST.split(product_links.fold(text)) if _significant(part)]
    found: list[tuple[frozenset[str], str]] = []
    for part in parts:
        own: frozenset[str] = frozenset(_significant(part))
        needed: frozenset[str] = own | frozenset(first)
        if len(parts) > 1 and len(own - frozenset(first)) < MIN_WORDS:
            continue
        if len(needed) >= MIN_WORDS and all(needed != other for other, _ in found):
            found.append((needed, part))
    return found


def line_alternatives(title: str | None) -> list[frozenset[str]]:
    """De woorden die een product moet hebben om bij een folderactie te horen: een set per deel van een opsomming, elk met het
    eerste woord van de naam erbij. Een deel met te weinig woorden valt weg, een naam die te weinig zegt geeft niets."""
    return [needed for needed, _ in _parts(title)]


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


class Line(NamedTuple):
    product_id: str
    words: frozenset[str]
    size: tuple[float, str] | None
    multipack: bool


class LineIndex:
    """De folderproducten van een winkel, op hun langste woord: een product zoekt alleen bij zijn eigen woorden."""

    def __init__(self, folder_products: Iterable[tuple[str, str]]) -> None:
        self._by_word: dict[str, list[Line]] = {}
        seen: set[str] = set()
        for product_id, title in folder_products:
            if product_id in seen:
                continue
            seen.add(product_id)
            for needed, part in _parts(title):
                key: str = max(needed, key=lambda word: (len(word), word))
                self._by_word.setdefault(key, []).append(Line(product_id, needed, _size(part), multipack(part)))

    def __len__(self) -> int:
        return sum(len(lines) for lines in self._by_word.values())

    def _fits(
        self,
        line: Line,
        have: frozenset[str],
        several: bool,
        sizes: list[tuple[float, str]],
    ) -> bool:
        if line.multipack != several or not line.words <= have or (have & ORGANIC and not line.words & ORGANIC):
            return False
        return (
            line.size is None
            or not sizes
            or any(_same_size(line.size, size) for size in sizes)
        )

    def lines_for(
        self, names: Iterable[str], sizes: Iterable[tuple[float, str]] = ()
    ) -> list[str]:
        """De folderproducten waarvan elk woord in een van de namen staat, en waarvan de verpakking (als hij er een noemt) bij
        een van de verpakkingen past (als die bekend zijn). Gesorteerd, zodat de uitkomst vastligt."""
        known_sizes: list[tuple[float, str]] = list(sizes)
        found: set[str] = set()
        for name in names:
            have: frozenset[str] = words(name)
            several: bool = multipack(name)
            for word in have:
                found.update(
                    line.product_id
                    for line in self._by_word.get(word, ())
                    if self._fits(line, have, several, known_sizes)
                )
        return sorted(found)
