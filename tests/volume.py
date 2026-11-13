"""Een database op de schaal van productie, uit verzonnen gegevens, voor de volumetests (`tests/test_volume.py`).

De gewone tests draaien op een handvol rijen en zien dus nooit wat de app met tienduizenden producten doet: de eerste
`/api/offers?store=ah` na een wijziging van de catalogus hield in productie de event loop 4,4 seconden vast. `build` maakt
een database met de aantallen van productie (`SIZES`) en dezelfde verdeling: 65% van `offer_history` hangt aan een
folderproduct (`folder:...`, de helft zonder van-prijs), AH heeft 73% van de rijen en Jumbo 26%. De namen komen uit de
woordenlijsten hieronder (verzonnen merken, gewone productsoorten en verpakkingen), zodat de categorieregels
(`offer_categories`) en de folderregels (`line_offers`) echt werk hebben. Er staat geen enkele waarde uit productie in.

`products` telt de verschillende producten van AH (zo komt de 72.000 van productie tot stand), `purchases` de regels in de
tabellen van `prices._PURCHASES`. Daarnaast staan er kassabonnen en ingelezen aankopen in (`EXTRA`, in de verhouding van
productie): dat zijn de regels die `prices._receipt_rows` op naam koppelt, en zonder die regels doet dat deel van de
catalogus geen werk.

Het schema komt van de app zelf (de `_connect` van elke module), het vullen gaat met `sqlite3` en `executemany`. Een vast
zaad (`SEED`) en een vaste dag (`TODAY`, met `freeze_clock` ook voor de app) maken de database en de antwoorden bij elke
run gelijk.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import random
import sqlite3
import sys
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, NamedTuple

import pytest

from mealieshopper import (
    ah_orders,
    auth,
    crisp_orders,
    jumbo_orders,
    name_keys,
    offer_categories,
    picnic_orders,
    price_import,
    prices,
)

TODAY: str = "2026-11-13"
SEED: int = 20261113
SIZES: dict[str, int] = {
    "products": 72_000,
    "offer_history": 146_000,
    "price_observations": 310_000,
    "purchases": 8_000,
    "offers_ah": 2_800,
    "offers_jumbo": 400,
    "offers_picnic": 900,
    "offers_lidl": 40,
    "offers_aldi": 130,
}
EXTRA: dict[str, int] = {
    "imports": 8_200,
    "receipt_items": 2_600,
    "receipt_products": 6_500,
}
DB_NAME: str = "auth.sqlite3"
NOW: datetime = datetime(
    2026, 11, 13, 12, 0, tzinfo=UTC
)  # de klok van de app in een volumetest
SYNCED_AT: str = (
    "2026-11-13T05:00:00+00:00"  # de laatste synchronisatie van de aanbiedingen
)
MONDAY: date = date(2026, 11, 9)  # het begin van de week van TODAY
TABLES: tuple[str, ...] = (
    "offer_history", "offers", "offers_meta", "price_observations", "ah_orders", "ah_order_items",
    "jumbo_orders", "jumbo_order_items", "picnic_orders", "picnic_order_items", "crisp_orders",
    "crisp_order_items", "ah_receipts", "ah_receipt_items", "ah_receipt_products", "imported_purchases",
)  # fmt: skip

# Per winkel: (gewone producten, folderproducten, rijen van gewone producten, rijen van folderproducten) in offer_history.
SHAPE: dict[str, tuple[int, int, int, int]] = {
    "ah": (27_000, 45_000, 40_500, 66_080),
    "jumbo": (6_000, 19_800, 9_110, 28_850),
    "picnic": (1_000, 0, 1_100, 0),
    "aldi": (200, 0, 250, 0),
    "lidl": (80, 0, 110, 0),
}
# De soorten aanbieding in `offers`, als deel van het aantal: deze week, doorlopend, persoonlijk (de Bonus Box), volgende
# week, verlopen (nog in de kopie) en zonder datums (Picnic). Alles behalve `next` heeft een open rij in offer_history.
OFFER_MIX: dict[str, dict[str, float]] = {
    "ah": {"week": 0.715, "open": 0.107, "personal": 0.036, "next": 0.142},
    "jumbo": {"week": 0.9, "expired": 0.1},
    "picnic": {"undated": 1.0},
    "lidl": {"week": 1.0},
    "aldi": {"week": 0.892, "next": 0.108},
}
ORDERS: dict[str, tuple[int, int]] = {
    "ah": (240, 25),
    "jumbo": (48, 25),
    "picnic": (25, 24),
    "crisp": (10, 20),
}
INDEX_WEEKS: int = 225  # zoveel weekprijzen van de prijsindex per gekocht AH-product
INDEX_PRODUCTS: int = 1_300
SEARCHED: dict[str, tuple[int, int]] = {
    "ah": (2_000, 3_000),
    "jumbo": (1_000, 1_500),
    "picnic": (5_000, 13_000),
}
HOUSE: dict[str, tuple[str, ...]] = {
    "ah": ("AH", "AH", "AH Biologisch", "AH Excellent", "AH Basic"),
    "jumbo": ("Jumbo", "Jumbo"),
    "picnic": ("Picnic",),
    "crisp": ("Crisp",),
    "aldi": ("Zuidhoeve",),
    "lidl": ("Kroonvelde",),
}
BRANDS: tuple[str, ...] = (
    "Weidegoud", "Polderpracht", "Duinvers", "Molenhoek", "Zuidertuin", "Noorderlicht", "Kroonhof", "Lindevelde",
    "Vlietzoet", "Eikenhof", "Heideland", "Klaverweide", "Rivierdal", "Zeeuwse Kust", "Brabants Erf", "Fries Goud",
    "Twentse Hoeve", "Gouden Aar", "Zilverdistel", "Bosrand", "Dijkzicht", "Hoevemeester", "Lentedauw", "Zonnegaarde",
    "Waddenzout", "Kasteelhof", "Veldkeur", "Merelhof", "Stroomdal", "Graanmolen",
)  # fmt: skip
OLD_BRANDS: tuple[str, ...] = (
    "Oud Rozendaal",
    "Vroeger Best",
    "Ouwe Trouw",
    "Stadsbakker",
    "Kruidenier Vos",
)
VARIANTS: tuple[str, ...] = (
    "", "", "", "naturel", "light", "volkoren", "extra belegen", "mild", "pittig", "original", "zero", "ongezouten",
    "gerookt", "vers", "mini", "klassiek", "romig", "krokant", "zacht", "rood", "groen", "huisgemaakt", "met kruiden",
)  # fmt: skip
# Productsoort | gewone prijs van de eerste verpakking | verpakkingen, gescheiden door een slash.
KINDS_TEXT: str = """
Bananen|1.89|5 stuks;Elstar appels|2.49|1 kg/1,5 kg;Trostomaten|2.29|500 g;Broccoli|1.49|500 g;Bloemkool|1.99|1 stuk
Kruimige aardappelen|2.99|2 kg/5 kg;Sperziebonen|1.79|400 g;Spinazie|1.69|300 g/600 g;Champignons|1.39|250 g/400 g
Komkommer|0.89|1 stuk;Paprika mix|2.19|3 stuks;Avocado|1.29|2 stuks;Blauwe bessen|2.99|200 g;Mandarijnen|2.79|1 kg
Rundergehakt|3.49|300 g/500 g/1000 g;Kipfilet|4.99|300 g/600 g/1000 g;Half-om-half gehakt|2.99|300 g/500 g
Speklappen|3.29|400 g;Rookworst|2.49|275 g;Kipdrumsticks|3.99|600 g;Hamburgers|3.79|4 stuks;Biefstuk|6.49|250 g
Varkenshaas|5.99|400 g;Shoarma reepjes|3.49|400 g;Zalmfilet|5.99|250 g/500 g;Kabeljauwfilet|5.49|300 g
Garnalen|4.29|150 g;Vissticks|2.89|10 stuks;Makreel|2.59|200 g;Jonge kaas|4.79|500 g/1000 g
Belegen kaas|5.29|500 g/1000 g;Oude kaas|6.19|500 g;Geraspte kaas|2.69|150 g/300 g;Mozzarella|1.29|125 g
Salami|2.19|80 g/150 g;Achterham|2.39|150 g;Smeerkaas|1.89|200 g;Brie|2.99|200 g;Geitenkaas|3.49|150 g
Halfvolle melk|1.15|1 l/1,5 l;Volle melk|1.25|1 l;Magere yoghurt|1.39|1 l;Griekse yoghurt|2.29|500 g/1 kg
Vanillevla|1.49|1 l;Roomboter|2.89|250 g;Scharreleieren|2.79|10 stuks/6 stuks;Slagroom|1.69|250 ml;Kwark|1.99|500 g
Karnemelk|1.09|1 l;Chocolademelk|1.79|1 l;Volkorenbrood|2.39|800 g;Croissants|2.49|6 stuks;Krentenbollen|2.29|6 stuks
Tortilla wraps|1.99|8 stuks;Afbakbroodjes|1.79|6 stuks;Beschuit|1.29|13 stuks;Spaghetti|1.19|500 g/1 kg;Penne|1.19|500 g
Basmati rijst|2.49|1 kg/2 kg;Pizza margherita|2.99|350 g;Lasagne|4.49|1000 g;Noedels|1.69|250 g;Couscous|1.59|500 g
Tortelloni|2.79|250 g;Tomatensoep|2.29|570 ml;Pastasaus|2.19|500 g;Mayonaise|2.49|500 ml;Ketchup|2.19|500 ml
Olijfolie|7.99|500 ml/1 l;Mosterd|1.29|370 ml;Kikkererwten|1.09|400 g;Bouillonblokjes|1.49|8 stuks
Pindakaas|3.29|350 g/600 g;Hagelslag|2.59|400 g;Aardbeienjam|2.19|450 g;Muesli|2.99|750 g;Havermout|1.69|500 g
Honing|3.49|350 g;Ontbijtkoek|1.99|500 g;Chips paprika|1.79|225 g;Chips naturel|1.79|225 g;Stroopwafels|1.99|8 stuks
Pure chocolade|1.69|200 g;Melkchocolade|1.69|200 g;Gemengde noten|3.99|200 g/400 g;Winegums|1.49|300 g;Drop|1.79|300 g
Speculaas|1.59|400 g;Popcorn|1.29|100 g;Cola|2.19|1,5 l/6 x 330 ml;Sinaasappelsap|2.49|1 l/1,5 l;Appelsap|1.89|1,5 l
Mineraalwater|0.89|1,5 l/6 x 1,5 l;Ice tea|1.79|1,5 l;Limonadesiroop|2.29|750 ml;Tonic|1.49|1 l
Koffiebonen|7.99|500 g/1 kg;Filterkoffie|4.99|500 g;Koffiecups|3.49|20 stuks;Groene thee|1.99|20 stuks
Rooibos thee|1.99|20 stuks;Pils|6.99|6 x 330 ml/24 x 300 ml;Witbier|7.49|6 x 300 ml;Rode wijn|5.99|750 ml
Witte wijn|5.49|750 ml;Diepvries spinazie|1.39|450 g;Roomijs vanille|3.49|1 l;Wasmiddel|9.99|1,5 l
Afwasmiddel|1.89|500 ml;Toiletpapier|4.99|12 stuks;Keukenpapier|2.99|4 stuks;Vuilniszakken|2.49|20 stuks
Allesreiniger|1.99|1 l;Shampoo|3.49|250 ml;Tandpasta|2.29|75 ml;Douchegel|2.79|250 ml;Deodorant|3.29|150 ml
Handzeep|1.99|300 ml;Luiers|11.99|40 stuks;Billendoekjes|2.49|64 stuks;Kattenvoer|4.99|12 stuks;Hondenvoer|7.49|4 kg
Bloemen boeket|5.99|1 stuk;Kaarsen|3.99|10 stuks
"""


class Kind(NamedTuple):
    name: str
    price: float
    sizes: tuple[str, ...]


class Product(NamedTuple):
    """Een product van een winkel: wat elke tabel over hem bewaart, met zijn gewone prijs op TODAY."""

    store: str
    product_id: str
    title: str
    brand: str
    unit_size: str | None
    regular: float
    category: str


def _kinds(text: str) -> tuple[Kind, ...]:
    entries: list[str] = [
        entry for line in text.strip().splitlines() for entry in line.split(";")
    ]
    found: list[Kind] = []
    for entry in entries:
        name, price, sizes = entry.split("|")
        found.append(Kind(name, float(price), tuple(sizes.split("/"))))
    return tuple(found)


KINDS: tuple[Kind, ...] = _kinds(KINDS_TEXT)
TAXONOMY: dict[str, str] = {
    category: key for key, category in reversed(offer_categories.AH_TAXONOMY.items())
}


def scaled(value: int, scale: float) -> int:
    return max(1, round(value * scale))


def _shelf(value: float) -> float:
    """Een prijs zoals op het schap: op negen cent."""
    return max(0.19, round(round(value, 1) - 0.01, 2))


def price_on(product: Product, day: date) -> float:
    """De gewone prijs op een dag: elk jaar terug 3,5% lager."""
    years: float = (date.fromisoformat(TODAY) - day).days / 365
    return _shelf(product.regular * (1 - 0.035 * years))


def _size_factor(kind: Kind, size: str) -> float:
    return 1.0 + 0.6 * kind.sizes.index(size)


def _product(
    rng: random.Random, store: str, product_id: str, brands: tuple[str, ...]
) -> Product:
    kind: Kind = rng.choice(KINDS)
    brand: str = rng.choice(brands)
    variant: str = rng.choice(VARIANTS)
    size: str = rng.choice(kind.sizes)
    title: str = " ".join(part for part in (brand, kind.name, variant) if part)
    factor: float = _size_factor(kind, size) * (
        0.85 if brand in HOUSE.get(store, ()) else 1.1
    )
    category: str = offer_categories.offer_category(store, title)
    return Product(
        store, product_id, title, brand, size, _shelf(kind.price * factor), category
    )


def _product_id(store: str, number: int) -> str:
    prefixes: dict[str, str] = {"jumbo": "J", "picnic": "s", "crisp": "c"}
    return f"{prefixes.get(store, '')}{100_000 + number * 7}"


def products(rng: random.Random, store: str, count: int) -> list[Product]:
    """De gewone producten van een winkel: huismerk en verzonnen merken door elkaar, een op de honderd een tweeling (dezelfde
    naam, hetzelfde merk en dezelfde verpakking onder een tweede nummer, zoals AH die voert)."""
    brands: tuple[str, ...] = HOUSE[store] * 4 + BRANDS
    found: list[Product] = []
    for number in range(count):
        if found and rng.random() < 0.01:
            found.append(found[-1]._replace(product_id=_product_id(store, number)))
        else:
            found.append(_product(rng, store, _product_id(store, number), brands))
    return found


def _folder_title(rng: random.Random, store: str) -> tuple[str, Kind]:
    """Een foldernaam zoals in een prijsarchief: een lijn, een variant, een verpakking of een opsomming."""
    kind: Kind = rng.choice(KINDS)
    brand: str = (
        rng.choice(OLD_BRANDS)
        if rng.random() < 0.3
        else rng.choice(HOUSE[store] + BRANDS)
    )
    variant: str = rng.choice(VARIANTS[3:])
    other: str = rng.choice(KINDS).name.lower()
    forms: tuple[str, ...] = (
        f"Alle {brand} {kind.name.lower()}",
        f"{brand} {kind.name} {variant}",
        f"{brand} {kind.name} {rng.choice(kind.sizes)}",
        f"{brand} {kind.name.lower()} en {other}",
        f"Diverse {brand} {kind.name.lower()} {variant}",
        f"{brand} {kind.name}, {variant} of {rng.choice(VARIANTS[3:])}",
    )
    return rng.choice(forms), kind


def folder_products(rng: random.Random, store: str, count: int) -> list[Product]:
    """De folderproducten (`folder:...`): het id uit de foldernaam, zoals `price_archive_tidy.detach` het maakt."""
    found: dict[str, Product] = {}
    while len(found) < count:
        title, kind = _folder_title(rng, store)
        key: str = f"detached|{store}|{name_keys.fold(title)}"
        product_id: str = (
            "folder:" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
        )
        size: str | None = rng.choice(kind.sizes) if rng.random() < 0.35 else None
        found.setdefault(
            product_id,
            Product(
                store,
                product_id,
                f"Folder: {title}",
                "Folderarchief",
                size,
                kind.price,
                "",
            ),
        )
    return list(found.values())


def deal(rng: random.Random, regular: float) -> tuple[float, float | None, str]:
    """(prijs, van-prijs, actietekst) van een aanbieding van de winkel. Bij een actie met een minimumaantal geeft de winkel
    de gewone prijs als prijs en geen van-prijs, zoals AH."""
    kind: int = rng.randrange(5)
    if kind == 0:
        percent: int = rng.choice((15, 20, 25, 30, 35, 40, 50))
        return round(regular * (1 - percent / 100), 2), regular, f"{percent}% korting"
    if kind == 1:
        return regular, None, rng.choice(("1+1 gratis", "2+1 gratis"))
    if kind == 2:
        return regular, None, "2e halve prijs"
    if kind == 3:
        count: int = rng.choice((2, 3))
        return (
            regular,
            None,
            f"{count} voor {_shelf(regular * count * 0.8):.2f}".replace(".", ","),
        )
    now: float = _shelf(regular * rng.choice((0.7, 0.8, 0.9)))
    return now, regular, f"nu {now:.2f}".replace(".", ",")


def folder_deal(
    rng: random.Random, regular: float
) -> tuple[float | None, float | None, str]:
    """(prijs, van-prijs, tekst) van een folderregel, in de vormen die `line_offers.per_piece` leest. De helft zonder van-prijs."""
    kind: int = rng.randrange(6)
    now: float = _shelf(regular * rng.choice((0.6, 0.75, 0.85)))
    if kind == 0:
        return now, regular, f"2 voor: {now * 2:.2f}, was {regular * 2:.2f}"
    if kind == 1:
        return now, regular, f"actie {now:.2f}, was {regular:.2f}"
    if kind == 2:
        return None, regular, f"{rng.choice((20, 25, 30, 40))}% korting"
    if kind == 3:
        return None, None, rng.choice(("1+1 gratis", "1+1 kosteloos"))
    if kind == 4:
        return None, None, "2e halve prijs"
    return now, None, f"voor {now:.2f}"


def spread(rng: random.Random, total: int, weights: list[float]) -> list[int]:
    """`total` rijen over de producten: elk minstens een, de rest naar gewicht."""
    counts: list[int] = [1] * len(weights)
    for index in rng.choices(
        range(len(weights)), weights=weights, k=max(0, total - len(weights))
    ):
        counts[index] += 1
    return counts


def _stamp(day: date, clock: str) -> str:
    return f"{day.isoformat()}T{clock}+00:00"


class Shown(NamedTuple):
    """Een aanbieding in `offers`, met de looptijd die bij haar soort (`OFFER_MIX`) hoort."""

    product: Product
    kind: str
    price: float
    before: float | None
    label: str
    valid_from: str | None
    valid_until: str | None


def _validity(kind: str) -> tuple[str | None, str | None]:
    def week(offset: int) -> tuple[str, str]:
        start: date = MONDAY + timedelta(weeks=offset)
        return start.isoformat(), (start + timedelta(days=6)).isoformat()

    spans: dict[str, tuple[str | None, str | None]] = {
        "week": week(0), "personal": week(0), "next": week(1), "expired": week(-1), "open": ("2026-10-05", None),
        "undated": (None, None),
    }  # fmt: skip
    return spans[kind]


def _kinds_of(store: str, total: int) -> list[str]:
    mix: dict[str, float] = OFFER_MIX[store]
    names: list[str] = list(mix)
    kinds: list[str] = [
        name for name in names[:-1] for _ in range(round(total * mix[name]))
    ]
    return kinds + [names[-1]] * max(0, total - len(kinds))


def shown_offers(
    rng: random.Random, store: str, normal: list[Product], total: int
) -> list[Shown]:
    """De aanbiedingen van een winkel. Een op de vier van volgende week staat ook deze week in de aanbieding (AH zet de
    bonus van volgende week naast die van deze week)."""
    found: list[Shown] = []
    fresh: int = 0
    for kind in _kinds_of(store, total):
        current: list[Shown] = [offer for offer in found if offer.kind == "week"]
        if kind == "next" and current and rng.random() < 0.25:
            product: Product = rng.choice(current).product
        else:
            product, fresh = normal[fresh], fresh + 1
        price, before, label = deal(rng, product.regular)
        if kind == "personal":
            price, before, label = (
                round(product.regular * 0.85, 2),
                product.regular,
                "Bonus Box 15% korting",
            )
        if kind == "undated":
            before = None
        found.append(Shown(product, kind, price, before, label, *_validity(kind)))
    return found


def history_row(
    product: Product,
    offer: tuple[Any, Any, str],
    valid: tuple[str | None, str | None],
    open_: bool,
    personal: bool,
) -> tuple[Any, ...]:
    """Een rij van offer_history: een periode in de aanbieding. `open_`: de open rij van nu (`ended_at` leeg)."""
    folder: bool = product.product_id.startswith("folder:")
    start: date = date.fromisoformat(valid[0]) if valid[0] else MONDAY
    first: str = _stamp(start, "00:00:00" if folder else "05:00:00")
    last: str = (
        SYNCED_AT
        if open_
        else _stamp(start + timedelta(days=6), "23:59:59" if folder else "05:00:00")
    )
    image: str = (
        ""
        if folder
        else f"https://img.example/{product.store}/{product.product_id}.webp"
    )
    url: str = (
        f"https://folders.example/{product.store}/{start}"
        if folder
        else prices._url(product.store, product.product_id)
    )
    return (
        product.store, product.product_id, product.title, product.brand, product.unit_size, product.category or None,
        offer[0], offer[1], offer[2], image, url, valid[0], valid[1], first, last, None if open_ else last,
        1 if folder else 6, 0, 1 if personal else 0,
    )  # fmt: skip


def _week(start: date) -> tuple[str, str]:
    return start.isoformat(), (start + timedelta(days=6)).isoformat()


def normal_history(
    rng: random.Random, normal: list[Product], shown: list[Shown], rows: int
) -> list[tuple[Any, ...]]:
    """De rijen van de gewone producten: afgesloten weken uit de laatste anderhalf jaar, en voor elke aanbieding van nu
    (behalve die van volgende week) een open rij. Een product dat nu in de aanbieding is, was dat vaker."""
    running: dict[str, Shown] = {
        offer.product.product_id: offer for offer in shown if offer.kind != "next"
    }
    counts: list[int] = spread(
        rng, rows, [3.0 if product.product_id in running else 1.0 for product in normal]
    )
    weeks: list[date] = [MONDAY - timedelta(weeks=n) for n in range(1, 79)]
    found: list[tuple[Any, ...]] = []
    for product, count in zip(normal, counts, strict=True):
        offer: Shown | None = running.get(product.product_id)
        for week in sorted(
            rng.sample(weeks, min(count - (offer is not None), len(weeks)))
        ):
            found.append(
                history_row(
                    product,
                    deal(rng, price_on(product, week)),
                    _week(week),
                    False,
                    False,
                )
            )
        if offer is not None:
            valid: tuple[str | None, str | None] = (offer.valid_from, offer.valid_until)
            found.append(
                history_row(product, offer[2:5], valid, True, offer.kind == "personal")
            )
    return found


def folder_history(
    rng: random.Random, folders: list[Product], rows: int
) -> list[tuple[Any, ...]]:
    """De rijen van de folderproducten: weken uit 2009 tot nu, de laatste jaren vaker, een paar producten heel vaak."""
    weeks: list[date] = [
        date(2009, 1, 5) + timedelta(weeks=n)
        for n in range((MONDAY - date(2009, 1, 5)).days // 7 + 1)
    ]
    weights: list[float] = [1 + 4 * index / len(weeks) for index in range(len(weeks))]
    counts: list[int] = spread(rng, rows, [rng.paretovariate(2.0) for _ in folders])
    found: list[tuple[Any, ...]] = []
    for product, count in zip(folders, counts, strict=True):
        start: int = rng.choices(range(len(weeks)), weights=weights)[0]
        later: list[int] = list(range(start, len(weeks)))
        chosen: list[int] = rng.sample(later, min(count, len(later)))
        chosen += rng.sample(range(start), min(count - len(chosen), start))
        for index in sorted(chosen):
            found.append(
                history_row(
                    product,
                    folder_deal(rng, price_on(product, weeks[index])),
                    _week(weeks[index]),
                    False,
                    False,
                )
            )
    return found


def offer_row(offer: Shown, position: int) -> tuple[Any, ...]:
    product: Product = offer.product
    hint: str | None = TAXONOMY.get(product.category) if product.store == "ah" else None
    return (
        product.store, product.product_id, position, product.title, product.brand, product.unit_size, offer.price,
        offer.before, offer.label, f"https://img.example/{product.store}/{product.product_id}.webp",
        prices._url(product.store, product.product_id), offer.valid_from, offer.valid_until, product.category,
        1 if offer.kind == "personal" else 0, (position % 2) if offer.kind == "personal" else None, hint,
    )  # fmt: skip


def bought(
    rng: random.Random, normal: list[Product], shown: list[Shown], count: int
) -> list[Product]:
    """De producten die je kocht: de helft uit de aanbiedingen van nu, de rest uit de catalogus."""
    offered: list[Product] = list(
        {offer.product.product_id: offer.product for offer in shown}.values()
    )
    taken: set[str] = {product.product_id for product in offered}
    others: list[Product] = [
        product for product in normal if product.product_id not in taken
    ]
    half: int = min(count // 2, len(offered))
    return rng.sample(offered, half) + rng.sample(
        others, min(count - half, len(others))
    )


def _observation(
    product: Product, day: date, source: str, price: float
) -> tuple[Any, ...]:
    shown: bool = source == "search"
    image: str | None = (
        f"https://img.example/{product.store}/{product.product_id}.webp"
        if shown
        else None
    )
    url: str | None = (
        prices._url(product.store, product.product_id) or None if shown else None
    )
    if source == prices.PUBLIC_OBSERVED_UNKNOWN:
        url = f"https://archief.example/{product.store}/{product.product_id}"
    return (
        product.store, product.product_id, day.isoformat(), source, product.title, product.brand, product.unit_size,
        image, url, price, _stamp(day, "06:00:00"),
    )  # fmt: skip


def index_observations(bought_ah: list[Product], count: int) -> list[tuple[Any, ...]]:
    """De weekprijzen van de prijsindex (`ah_price_index.py`) voor de gekochte AH-producten: elke maandag, jaren terug."""
    days: list[date] = [MONDAY - timedelta(weeks=n) for n in range(INDEX_WEEKS)]
    return [
        _observation(product, day, prices.PRICE_INDEX, price_on(product, day))
        for product in bought_ah[:count]
        for day in days
    ]


def searched_observations(
    rng: random.Random, chosen: list[Product], rows: int, days: list[date], source: str
) -> list[tuple[Any, ...]]:
    """Prijzen uit een zoekopdracht (`search`) of uit een openbaar archief, elk product op eigen dagen."""
    found: list[tuple[Any, ...]] = []
    for product, count in zip(
        chosen, spread(rng, rows, [1.0] * len(chosen)), strict=True
    ):
        for day in sorted(rng.sample(days, min(count, len(days)))):
            found.append(_observation(product, day, source, price_on(product, day)))
    return found


def _picked(
    rng: random.Random, preferred: list[Product], rest: list[Product], count: int
) -> list[Product]:
    first: list[Product] = preferred[: count // 2]
    taken: set[str] = {product.product_id for product in first}
    pool: list[Product] = [
        product for product in rest if product.product_id not in taken
    ]
    return first + rng.sample(pool, min(count - len(first), len(pool)))


def order_rows(
    rng: random.Random, store: str, chosen: list[Product], scale: float
) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    """Bestellingen en hun regels, in de vorm van de tabellen van `prices._PURCHASES`: AH en Jumbo met de bonus per regel,
    Picnic en Crisp met het bedrag van de hele regel. Een op de twintig bestellingen van AH en Jumbo is nog niet geleverd."""
    total, per_order = ORDERS[store]
    count: int = scaled(total, scale)
    weights: list[float] = [1 / (rank + 1) ** 0.8 for rank in range(len(chosen))]
    orders: list[tuple[Any, ...]] = []
    items: list[tuple[Any, ...]] = []
    for number in range(count):
        waiting: bool = store in ("ah", "jumbo") and number >= count - max(
            1, count // 20
        )
        day: date = date(2024, 1, 8) + timedelta(days=(number * 1040) // count)
        day = (
            date.fromisoformat(TODAY) + timedelta(days=1 + number % 5)
            if waiting
            else day
        )
        order_id: Any = 900_000 + number if store == "ah" else f"{store}-{number:05d}"
        lines: list[tuple[Any, ...]] = [
            _order_item(rng, store, order_id, position, product, day)
            for position, product in enumerate(
                rng.choices(chosen, weights=weights, k=per_order)
            )
        ]
        items += lines
        orders.append(
            _order(
                store, order_id, day, waiting, round(sum(line[-1] for line in lines), 2)
            )
        )
    return orders, [line[:-1] for line in items]


def _order(
    store: str, order_id: Any, day: date, waiting: bool, total: float
) -> tuple[Any, ...]:
    seen: str = _stamp(day - timedelta(days=2), "10:00:00")
    slot: tuple[str, str] = (_stamp(day, "17:00:00"), _stamp(day, "19:00:00"))
    if store == "ah":
        return (
            order_id,
            "open" if waiting else "delivered",
            "x",
            None,
            "DELIVERY",
            None,
            total,
            day.isoformat(),
            *slot,
            1,
            seen,
            seen,
        )
    if store == "jumbo":
        return (
            order_id,
            "open" if waiting else "delivered",
            "x",
            "DELIVERY",
            None,
            total,
            day.isoformat(),
            *slot,
            1,
            seen,
            seen,
        )
    if store == "picnic":
        return (
            order_id,
            "COMPLETED",
            total,
            day.isoformat(),
            *slot,
            *slot,
            1,
            seen,
            seen,
        )
    return (order_id, "DELIVERED", total, day.isoformat(), *slot, 1, seen, seen)


def _order_item(
    rng: random.Random,
    store: str,
    order_id: Any,
    position: int,
    product: Product,
    day: date,
) -> tuple[Any, ...]:
    """Een regel, met als laatste waarde het bedrag van de regel (voor het totaal van de bestelling, niet voor de tabel)."""
    regular: float = price_on(product, day)
    quantity: int = rng.choice((1, 1, 1, 2, 3))
    image: str = f"https://img.example/{store}/{product.product_id}.webp"
    if store in ("picnic", "crisp"):
        line: float = round(
            regular * quantity * (0.8 if rng.random() < 0.2 else 1.0), 2
        )
        return (
            order_id,
            position,
            product.product_id,
            product.title,
            product.unit_size,
            quantity,
            line,
            round(regular * quantity, 2),
            image,
            line,
        )
    bonus: bool = rng.random() < 0.25
    paid: float = round(regular * 0.75, 2) if bonus else regular
    product_id: Any = int(product.product_id) if store == "ah" else product.product_id
    taxonomy: str | None = TAXONOMY.get(product.category) if store == "ah" else None
    amount: float = round(paid * quantity, 2)
    return (
        order_id, position, product_id, product.title, product.brand, product.unit_size, quantity, amount, paid,
        regular if bonus else None, 1 if bonus else 0, taxonomy, image, amount,
    )  # fmt: skip


def receipt_name(product: Product, rng: random.Random) -> str:
    """De naam op een kassabon: in hoofdletters, de helft met afgekorte woorden ("AH RUNDERGEH")."""
    if rng.random() < 0.5:
        return product.title.upper()
    return " ".join(word[:9] for word in product.title.upper().split())


def receipt_products(
    rng: random.Random,
    chosen: list[Product],
    normal: list[Product],
    articles: dict[str, int],
    count: int,
) -> list[tuple[Any, ...]]:
    """`ah_receipt_products`: driekwart van de gekochte producten op hun artikelnummer, aangevuld met andere producten die
    AH bij een bon vond. Een op de vijfentwintig van die andere vond AH niet (`product_id` NULL)."""
    linked: list[Product] = chosen[: len(chosen) * 3 // 4]
    found: list[tuple[Any, ...]] = [
        (articles[product.product_id], int(product.product_id), product.title, "2026-01-01T00:00:00+00:00", "",
         product.brand, product.unit_size)
        for product in linked
    ]  # fmt: skip
    for index, product in enumerate(rng.sample(normal, max(0, count - len(linked)))):
        article: int = 3_000_000 + index
        if index % 25 == 0:
            found.append(
                (article, None, None, "2026-01-01T00:00:00+00:00", None, None, None)
            )
        else:
            found.append((article, int(product.product_id), product.title, "2026-01-01T00:00:00+00:00", "",
                          product.brand, product.unit_size))  # fmt: skip
    return found


def receipt_rows(
    rng: random.Random, chosen: list[Product], normal: list[Product], scale: float
) -> dict[str, list[tuple[Any, ...]]]:
    """Kassabonnen van AH met tien regels per bon. Het nummer op de bon is AH's artikelnummer: driekwart is gekoppeld aan
    een product van de webwinkel (`ah_receipt_products`), de rest koppelt de app op naam."""
    articles: dict[str, int] = {
        product.product_id: 2_000_000 + index for index, product in enumerate(chosen)
    }
    found: list[tuple[Any, ...]] = receipt_products(
        rng, chosen, normal, articles, scaled(EXTRA["receipt_products"], scale)
    )
    lines: list[tuple[Any, ...]] = []
    receipts: list[tuple[Any, ...]] = []
    count: int = max(1, scaled(EXTRA["receipt_items"], scale) // 10)
    for number in range(count):
        day: date = date(2024, 1, 6) + timedelta(days=(number * 1040) // count)
        receipt_id: str = f"bon-{number:05d}"
        for position, product in enumerate(rng.choices(chosen, k=10)):
            quantity: int = rng.choice((1, 1, 2))
            price: float | None = None if rng.random() < 0.6 else price_on(product, day)
            lines.append((receipt_id, position, articles[product.product_id], receipt_name(product, rng), quantity, price,
                          round(price_on(product, day) * quantity, 2)))  # fmt: skip
        receipts.append(
            (
                receipt_id,
                f"{day.isoformat()}T17:12:00",
                None,
                1,
                None,
                None,
                _stamp(day, "18:00:00"),
            )
        )
    return {
        "ah_receipts": receipts,
        "ah_receipt_items": lines,
        "ah_receipt_products": found,
    }


def _import_title(
    rng: random.Random, chosen: list[Product], normal: list[Product]
) -> str:
    """De naam in een bevestigingsmail: meestal die van het product, soms bijna (andere volgorde, "online" erachter), soms
    een naam die de app niet kent (een merk van vroeger)."""
    roll: float = rng.random()
    if roll < 0.5:
        return rng.choice(chosen).title
    if roll < 0.6:
        return rng.choice(chosen).title + " online"
    if roll < 0.75:
        return rng.choice(normal).title
    return f"{rng.choice(OLD_BRANDS)} {rng.choice(KINDS).name.lower()} {rng.choice(VARIANTS[3:])}"


def import_rows(
    rng: random.Random, chosen: list[Product], normal: list[Product], scale: float
) -> list[tuple[Any, ...]]:
    """Aankopen uit bevestigingsmails (`price_import.py`), twintig per bestelling, uit 2019 tot en met 2023: van voor de
    bestellingen in het account, dus niets telt dubbel."""
    count: int = scaled(EXTRA["imports"], scale)
    found: list[tuple[Any, ...]] = []
    for number in range(count):
        order: int = number // 20
        day: date = date(2019, 1, 7) + timedelta(
            days=(order * 20 * 1820) // count  # vijf jaar, 2019 tot en met 2023
        )
        paid: float = _shelf(rng.uniform(0.5, 9.0))
        regular: float | None = None if rng.random() < 0.6 else paid
        found.append(("ah", f"mail-{order:05d}", number % 20, day.isoformat(), _import_title(rng, chosen, normal),
                      float(rng.choice((1, 1, 2))), paid, regular, 80.0, "2026-10-01T10:00:00+00:00", "delivered", None, None))  # fmt: skip
    return found


def store_rows(
    rng: random.Random, store: str, scale: float
) -> tuple[list[Product], list[Shown], list[tuple[Any, ...]]]:
    """De gewone producten, de aanbiedingen en de rijen van offer_history van een winkel, volgens `SHAPE` en `OFFER_MIX`."""
    normal_count, folder_count, normal_rows, folder_rows = (
        scaled(value, scale) if value else 0 for value in SHAPE[store]
    )
    normal: list[Product] = products(rng, store, normal_count)
    shown: list[Shown] = shown_offers(
        rng, store, normal, min(scaled(SIZES[f"offers_{store}"], scale), normal_count)
    )
    rows: list[tuple[Any, ...]] = normal_history(
        rng, normal, shown, max(normal_rows, normal_count)
    )
    if folder_count:
        rows += folder_history(
            rng,
            folder_products(rng, store, folder_count),
            max(folder_rows, folder_count),
        )
    return normal, shown, rows


def observation_rows(
    rng: random.Random,
    normal: dict[str, list[Product]],
    shown: dict[str, list[Shown]],
    chosen_ah: list[Product],
    scale: float,
) -> list[tuple[Any, ...]]:
    """De 310.000 prijzen: de weekprijzen van de prijsindex, recente zoekopdrachten bij AH en Jumbo (de producten van de
    aanbiedingen voorop) en de openbare prijzen van Picnic uit 2022 en 2023."""
    recent: list[date] = [
        date.fromisoformat(TODAY) - timedelta(days=n)
        for n in range(1, 60)
        if n % 7 != 4
    ]  # geen maandag
    old: list[date] = [date(2022, 1, 3) + timedelta(days=n) for n in range(730)]
    found: list[tuple[Any, ...]] = index_observations(
        chosen_ah, scaled(INDEX_PRODUCTS, scale)
    )
    for store in ("ah", "jumbo"):
        count, rows = (scaled(value, scale) for value in SEARCHED[store])
        chosen: list[Product] = _picked(
            rng, [offer.product for offer in shown[store]], normal[store], count
        )
        found += searched_observations(rng, chosen, rows, recent, "search")
    count, rows = (scaled(value, scale) for value in SEARCHED["picnic"])
    archive: list[Product] = (
        normal["picnic"] + products(rng, "picnic", count)[len(normal["picnic"]) :]
    )
    found += searched_observations(
        rng, archive[:count], rows, old, prices.PUBLIC_OBSERVED_UNKNOWN
    )
    return found


def generate(scale: float = 1.0) -> dict[str, list[tuple[Any, ...]]]:
    """Alle rijen per tabel, met een vast zaad: dezelfde `scale` geeft dezelfde rijen."""
    rng: random.Random = random.Random(SEED)
    normal: dict[str, list[Product]] = {}
    shown: dict[str, list[Shown]] = {}
    tables: dict[str, list[tuple[Any, ...]]] = {table: [] for table in TABLES}
    for store in SHAPE:
        normal[store], shown[store], rows = store_rows(rng, store, scale)
        tables["offer_history"] += rows
        tables["offers"] += [
            offer_row(offer, position) for position, offer in enumerate(shown[store])
        ]
        tables["offers_meta"].append((store, SYNCED_AT, None))
    chosen: dict[str, list[Product]] = {
        store: bought(rng, normal[store], shown[store], scaled(1_600, scale))
        for store in ("ah", "jumbo", "picnic")
    }
    chosen["crisp"] = products(rng, "crisp", scaled(150, scale))
    for store in ORDERS:
        orders, items = order_rows(rng, store, chosen[store], scale)
        tables[f"{store}_orders"], tables[f"{store}_order_items"] = orders, items
    tables["price_observations"] = observation_rows(
        rng, normal, shown, chosen["ah"], scale
    )
    tables.update(receipt_rows(rng, chosen["ah"], normal["ah"], scale))
    tables["imported_purchases"] = import_rows(rng, chosen["ah"], normal["ah"], scale)
    return tables


COLUMNS: dict[str, str] = {
    "offer_history": "store, product_id, title, brand, unit_size, category, price, price_before, promo_label, image, url, "
    "valid_from, valid_until, first_seen, last_seen, ended_at, syncs, continued, personal",
    "offers": "store, product_id, position, title, brand, unit_size, price, price_before, promo_label, image, url, valid_from, "
    "valid_until, category, personal, activated, category_hint",
    "offers_meta": "store, synced_at, personal_error",
    "price_observations": "store, product_id, day, source, title, brand, unit_size, image, url, price, seen_at",
    "ah_orders": "order_id, kind, state, state_text, shopping_type, method, total, delivery_date, slot_start, slot_end, "
    "items_synced, first_seen, synced_at",
    "jumbo_orders": "order_id, kind, state, shopping_type, method, total, delivery_date, slot_start, slot_end, items_synced, "
    "first_seen, synced_at",
    "picnic_orders": "order_id, status, total, delivery_date, slot_start, slot_end, delivered_start, delivered_end, "
    "items_synced, first_seen, synced_at",
    "crisp_orders": "order_id, status, total, delivery_date, slot_start, slot_end, items_synced, first_seen, synced_at",
    "ah_receipts": "receipt_id, date_time, total, details_synced, discounts, payments, first_seen",
    "ah_receipt_items": "receipt_id, position, product_id, name, quantity, price, amount",
    "ah_receipt_products": "receipt_product_id, product_id, title, checked_at, image, brand, unit_size",
    "imported_purchases": "store, order_ref, position, day, title, quantity, paid, regular, order_total, imported_at, status, "
    "slot_start, slot_end",
}  # fmt: skip
ITEM_COLUMNS: dict[str, str] = {
    "ah_order_items": "order_id, position, product_id, title, brand, unit_size, quantity, amount, price, price_before_bonus, "
    "bonus, taxonomy, image",
    "picnic_order_items": "order_id, position, product_id, title, unit_size, quantity, price, list_price, image",
}  # fmt: skip
COLUMNS["jumbo_order_items"] = ITEM_COLUMNS["ah_order_items"]
COLUMNS.update(ITEM_COLUMNS, crisp_order_items=ITEM_COLUMNS["picnic_order_items"])


async def _create_schema() -> None:
    """Het schema zoals de app het maakt: `auth.init_db` en de `_connect` van elke module met tabellen in deze database."""
    await auth.init_db()
    connects: tuple[Callable[[], Any], ...] = (
        prices._connect, ah_orders._connect, jumbo_orders._connect, picnic_orders._connect, crisp_orders._connect,
        price_import._connect,
    )  # fmt: skip
    for connect in connects:
        async with connect():
            pass


def create_schema(data_dir: Path) -> None:
    """Draait `_create_schema` met `data_dir` als datamap, en laat de omgeving en wat `auth` onthoudt daarna zoals het was."""
    previous: str | None = os.environ.get("MEALIESHOPPER_DATA_DIR")
    os.environ["MEALIESHOPPER_DATA_DIR"] = str(data_dir)
    auth.forget_runtime_state()
    try:
        asyncio.run(_create_schema())
    finally:
        if previous is None:
            os.environ.pop("MEALIESHOPPER_DATA_DIR", None)
        else:
            os.environ["MEALIESHOPPER_DATA_DIR"] = previous
        auth.forget_runtime_state()


def build(data_dir: Path, scale: float = 1.0) -> None:
    """Maakt `data_dir/auth.sqlite3` met het schema van de app en vult het met `generate(scale)`. Niet vanuit een draaiende
    event loop aanroepen: het schema maakt een eigen loop (`asyncio.run`)."""
    data_dir.mkdir(parents=True, exist_ok=True)
    create_schema(data_dir)
    conn: sqlite3.Connection = sqlite3.connect(data_dir / DB_NAME)
    try:
        conn.execute("PRAGMA synchronous = OFF")
        for table, rows in generate(scale).items():
            columns: str = COLUMNS[table]
            marks: str = ", ".join("?" * (columns.count(",") + 1))
            conn.executemany(f"INSERT INTO {table} ({columns}) VALUES ({marks})", rows)
        conn.commit()
        conn.execute(
            "PRAGMA wal_checkpoint(TRUNCATE)"
        )  # alles in het bestand zelf: een kopie heeft dan alleen dat nodig
    finally:
        conn.close()


COUNT_SQL: dict[str, str] = {
    "products": "SELECT COUNT(*) FROM (SELECT product_id FROM offer_history WHERE store = 'ah' UNION "
    "SELECT product_id FROM price_observations WHERE store = 'ah' UNION SELECT product_id FROM offers WHERE store = 'ah')",
    "offer_history": "SELECT COUNT(*) FROM offer_history",
    "price_observations": "SELECT COUNT(*) FROM price_observations",
    "purchases": "SELECT (SELECT COUNT(*) FROM ah_order_items) + (SELECT COUNT(*) FROM jumbo_order_items) + "
    "(SELECT COUNT(*) FROM picnic_order_items) + (SELECT COUNT(*) FROM crisp_order_items)",
    **{f"offers_{store}": f"SELECT COUNT(*) FROM offers WHERE store = '{store}'" for store in ("ah", "jumbo", "picnic", "lidl", "aldi")},
}  # fmt: skip


def counts(path: Path) -> dict[str, int]:
    """Per naam uit `SIZES` hoeveel de database er heeft."""
    conn: sqlite3.Connection = sqlite3.connect(path)
    try:
        return {
            name: int(conn.execute(sql).fetchone()[0])
            for name, sql in COUNT_SQL.items()
        }
    finally:
        conn.close()


class FrozenDate(date):
    """`date` met `today()` op TODAY, zoals de tests dat met `mock.patch.object(module, "date", ...)` doen."""

    @classmethod
    def today(cls) -> FrozenDate:
        return cls.fromisoformat(TODAY)


class FrozenDateTime(datetime):
    """`datetime` met `now()` op NOW (in UTC, ook zonder tijdzone: zo hangt niets af van de tijdzone van de machine)."""

    @classmethod
    def now(cls, tz: Any = None) -> datetime:  # type: ignore[override]
        return NOW.astimezone(tz) if tz is not None else NOW.replace(tzinfo=None)

    @classmethod
    def utcnow(cls) -> datetime:  # type: ignore[override]
        return NOW.replace(tzinfo=None)

    @classmethod
    def today(cls) -> datetime:  # type: ignore[override]
        return NOW.replace(tzinfo=None)


def freeze_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zet de klok van de app op TODAY en NOW: elke module van `mealieshopper` die `date` of `datetime` importeerde, krijgt
    de bevroren klasse. Zo is de week 2026-11-09 van de vastgelegde antwoorden voor de app de week van nu."""
    for name, module in sorted(sys.modules.items()):
        if module is None or not (
            name == "mealieshopper" or name.startswith("mealieshopper.")
        ):
            continue
        if getattr(module, "date", None) is date:
            monkeypatch.setattr(module, "date", FrozenDate)
        if getattr(module, "datetime", None) is datetime:
            monkeypatch.setattr(module, "datetime", FrozenDateTime)


async def no_receipt_lookup(
    names: list[str], *args: Any, **kwargs: Any
) -> dict[str, int]:
    """`ah_orders.resolve_receipt_names` zonder AH: de prijsgeschiedenis met een zoekterm zoekt de namen op de bon die nog
    bij geen product horen meteen op bij AH. Een volumetest bereikt geen winkel, dus AH vindt hier niets."""
    del names, args, kwargs
    return {"found": 0, "missing": 0, "failed": 0, "searches": 0}
