"""Het onthouden per naam (`name_keys.TextMemo`): dezelfde uitkomst als zonder geheugen, en geen werk voor een naam die al bekend is."""

from __future__ import annotations

import gc
import random
import sys
import threading
import tracemalloc
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import volume

from mealieshopper import (
    auth,
    line_offers,
    name_keys,
    offer_categories,
    offers,
    prices,
)

MEMOISED: list[Callable[..., Any]] = [
    name_keys.fold,
    name_keys.near,
    offer_categories.categorize,
    line_offers._stem,
    line_offers._significant,
    line_offers._parts,
]


def names(count: int = 500) -> list[str]:
    """Namen uit de volumedatabase, met een paar die niet gewoon zijn: accenten, een dubbel merk, een lijst, een maat."""
    rng: random.Random = random.Random(6)
    found: list[str] = [
        product.title for product in volume.products(rng, "ah", count - 6)
    ]
    found += [
        "AH Ah zachte spinazie",
        "Crème fraîche 200 g",
        "Pizza's, pasta en rijst",
        "Folder: Kipfilet of gehakt",
        "  ",
        "AH Les pains triomphe heel online",
    ]
    return found


@pytest.mark.parametrize("function", MEMOISED, ids=lambda function: function.__name__)
def test_the_memo_gives_the_same_answer(function: Callable[..., Any]) -> None:
    for name in names():
        assert function(name) == function.__wrapped__(name), name
        assert function(name) == function.__wrapped__(name), (
            name
        )  # de tweede keer komt uit het geheugen


def test_offer_category_gives_the_same_answer() -> None:
    for store, name in ((store, name) for store in ("ah", "jumbo") for name in names()):
        for hint in ("", "Zuivel", "Drogisterij, baby", None):
            assert offer_categories.offer_category(
                store, name, hint
            ) == offer_categories.offer_category.__wrapped__(store, name, hint), (
                store,
                name,
                hint,
            )


@pytest.mark.parametrize(
    "function",
    [*MEMOISED, offer_categories.offer_category],
    ids=lambda function: function.__name__,
)
def test_every_function_has_cache_info_and_clear(function: Callable[..., Any]) -> None:
    function.cache_clear()
    assert function.cache_info().currsize == 0
    assert function.cache_info().maxsize == function.size
    assert 0 < function.size <= name_keys.MEMO_SIZE


def test_keywords_give_the_same_key_as_positions() -> None:
    offer_categories.categorize.cache_clear()
    assert offer_categories.categorize("Kipfilet", hint="Vlees") == offer_categories.categorize("Kipfilet", "Vlees")
    assert offer_categories.categorize(title="Kipfilet", hint="Vlees") == "vlees"
    assert offer_categories.offer_category("ah", title="Kipfilet") == offer_categories.offer_category("ah", "Kipfilet", "")
    info = offer_categories.categorize.cache_info()
    assert (info.misses, info.hits) == (2, 2)  # (Kipfilet, Vlees) een keer, (Kipfilet, "") via offer_category
    assert offer_categories.categorize.__name__ == "categorize"
    assert offer_categories.categorize.__doc__
    with pytest.raises(TypeError):
        offer_categories.categorize("Kipfilet", nope="x")
    offer_categories.categorize.cache_clear()
    for call in (
        lambda: offer_categories.categorize("Kipfilet"),
        lambda: offer_categories.categorize(title="Kipfilet"),
        lambda: offer_categories.categorize("Kipfilet", ""),
        lambda: offer_categories.categorize(title="Kipfilet", hint=None),
    ):
        call()
    info = offer_categories.categorize.cache_info()
    assert (info.misses, info.hits, info.currsize) == (1, 3, 1)


def test_a_function_with_keyword_only_parameters_is_refused() -> None:
    def keyword_only(name: str, *, extra: str = "") -> str:
        return name + extra

    def variadic(*names: str) -> str:
        return "".join(names)

    for function in (keyword_only, variadic):
        with pytest.raises(TypeError, match="TextMemo"):
            name_keys.TextMemo(function)


def test_none_and_numbers_are_accepted() -> None:
    assert name_keys.fold(None) == ""
    assert name_keys.fold(12) == "12"
    assert name_keys.near(None) == name_keys.NEAR_MARK
    assert offer_categories.categorize(None, None) == offer_categories.OTHER
    assert offer_categories.categorize(12, 3.5) == offer_categories.OTHER
    assert offer_categories.offer_category("ah", None, None) == offer_categories.OTHER
    assert prices._size_of(None, None) is None
    assert line_offers._tokens(None) == ()


def test_a_cached_result_cannot_be_changed_by_a_caller() -> None:
    tokens: Any = line_offers._tokens("AH Pizza margherita 300 g")
    parts: Any = line_offers._parts("Folder: AH Verse kipfilet naturel")
    significant: Any = line_offers._significant("pizza margherita")
    assert isinstance(tokens, tuple)
    assert isinstance(significant, tuple)
    assert isinstance(parts, tuple)
    assert parts
    # gesorteerde tuples van tekst en geen frozensets: die volgt de GC na de eerste ronde niet meer (taak 9c)
    assert all(isinstance(needed, tuple) and list(needed) == sorted(set(needed)) for needed, _ in parts)
    assert isinstance(line_offers.words("pizza margherita"), frozenset)
    with pytest.raises(AttributeError):
        tokens.append("x")
    with pytest.raises(AttributeError):
        parts[0][0].add("x")


@pytest.mark.volume
async def test_a_second_catalogue_build_hits_the_memo(volume_dir: Path) -> None:
    async def build() -> None:
        async with auth.connect() as conn:
            await prices._catalogue(conn, "ah", volume.TODAY)

    offer_categories.offer_category.cache_clear()
    await build()
    first: int = offer_categories.offer_category.cache_info().misses
    assert first > 0
    prices._CATALOGUES.clear()
    await build()
    assert offer_categories.offer_category.cache_info().misses == first
    assert offer_categories.offer_category.cache_info().hits > 0


def shouting(size: int) -> tuple[name_keys.TextMemo[str], list[str]]:
    """Een `TextMemo` van `size` namen om `str.upper` heen, met de lijst van wat hij echt uitrekende."""
    calls: list[str] = []

    def shout(text: str) -> str:
        calls.append(text)
        return text.upper()

    return name_keys.TextMemo(shout, size=size), calls


def test_a_full_memo_drops_the_oldest_name_that_did_not_come_back() -> None:
    """`TextMemo` van drie namen: bij de vierde wordt de nieuwe generatie de oude, en gaat de oudste eruit. Een naam die terugkwam
    (verhuisd naar de nieuwe) blijft, de volgende misser haalt de oudste die niet terugkwam weg."""
    memo, calls = shouting(3)
    for text in ("a", "b", "c", "d"):  # "a" gaat eruit
        memo(text)
    memo("c")  # komt terug: verhuist naar de nieuwe generatie
    memo("e")  # "b" gaat eruit, niet "c"
    assert memo.cache_info().evicted == 2
    assert memo.cache_info().currsize == 3
    calls.clear()
    for text in ("c", "d", "e"):
        memo(text)
    assert calls == []
    memo("a")
    assert calls == ["a"]


def test_a_full_memo_stays_full_instead_of_starting_over() -> None:
    """Review van taak 9c: na een wissel bij `size` gooide de memo de hele oude generatie weg, dus een volle memo hield na de
    volgende misser bijna niets over. Nu gaat er per misser een naam uit en blijft hij vol."""
    memo, _ = shouting(1000)
    for number in range(5000):
        memo(f"naam {number}")
        if number >= 1000:
            assert memo.cache_info().currsize == 1000, number
    assert memo.cache_info().evicted == 4000


def test_the_order_lets_go_of_evicted_names() -> None:
    """Review van taak 9c: `_order` hield elke weggehaalde sleutel vast tot de volgende wissel. Nu staan er voor de cursor hooguit
    een kwart van de sleutels, en de rest is precies wat de oude generatie nog heeft (alleen missers, dus niets verhuisd)."""
    memo, _ = shouting(1000)
    for number in range(2000):
        memo(f"naam {number}")
        assert memo._next * name_keys.ORDER_SLACK <= len(memo._order), number
        if number >= 1000:
            assert len(memo._order) - memo._next == len(memo._old), number


def test_threads_that_miss_together_keep_the_memo_bounded() -> None:
    """Review van taak 9c: zonder slot kon een thread midden in `self._next += 1` zitten terwijl een andere `_order` inkortte, en
    dan de oude cursor in de korte tuple zetten. Een wisselinterval van een microseconde laat de threads op elke bytecode wisselen:
    vier threads die alleen missen, houden de memo op hooguit `size` plus een naam per thread."""
    size: int = 64
    memo: name_keys.TextMemo[str] = name_keys.TextMemo(str.upper, size=size)
    threads: int = 4
    largest: list[int] = []

    def hammer(offset: int) -> None:
        for number in range(20_000):
            memo(f"naam {offset} {number}")
        largest.append(memo.cache_info().currsize)

    previous: float = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        workers: list[threading.Thread] = [threading.Thread(target=hammer, args=(n,)) for n in range(threads)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
    finally:
        sys.setswitchinterval(previous)
    assert len(largest) == threads
    assert max(largest) <= size + threads, largest
    for number in range(4 * size):  # en hij blijft werken: weer een thread, dan precies `size`
        memo(f"daarna {number}")
    assert memo.cache_info().currsize == size


def test_cache_clear_waits_for_an_eviction_that_is_running() -> None:
    """Review van taak 9c (taak 10b): `cache_clear` zette `_order` en `_next` zonder het slot, en een rekenthread midden in `_evict`
    kon dan een cursor van de oude tuple in de lege zetten. Nu wacht `cache_clear` op het slot, zoals `_evict` zelf."""
    memo, _ = shouting(3)
    for text in ("a", "b", "c", "d"):
        memo(text)
    cleared: threading.Event = threading.Event()

    def clear() -> None:
        memo.cache_clear()
        cleared.set()

    with memo._lock:  # zoals een rekenthread in `_evict`
        worker: threading.Thread = threading.Thread(target=clear)
        worker.start()
        assert not cleared.wait(0.05)
    worker.join(5)
    assert cleared.is_set()
    assert (memo.cache_info().currsize, memo._order, memo._next) == (0, (), 0)


def test_an_order_that_ran_out_starts_again_from_the_old_generation() -> None:
    """Het vangnet van `_evict`: is `_order` op terwijl de oude generatie nog namen heeft, dan begint de volgorde opnieuw. Zonder
    dat haalde de memo niets meer weg en groeide hij bij elke misser."""
    memo, _ = shouting(10)
    for number in range(11):  # de wissel: tien in de oude generatie, de oudste eruit
        memo(f"naam {number}")
    memo._next = len(memo._order)  # zoals na een overgeslagen stuk
    for number in range(20):
        memo(f"later {number}")
    assert memo.cache_info().currsize == 10


def test_a_full_memo_that_keeps_missing_stays_near_its_full_size() -> None:
    """Review van taak 9c: een volle memo die blijft missen, kostte tot 1,6 keer zijn volle geheugen (de sleutels in `_order`).
    tracemalloc over twee keer `size` aan missers, gemeten na elk 64e deel: hooguit 1,25 keer wat hij precies vol kost."""
    size: int = 20_000
    memo: name_keys.TextMemo[str] = name_keys.TextMemo(str.upper, size=size)  # niet `shouting`: die lijst houdt elke naam vast
    number: int = 0

    def miss(count: int) -> None:
        nonlocal number
        for _ in range(count):
            memo(f"AH Verse kipfilet naturel {number:08d} 300 g")
            number += 1

    tracemalloc.start()
    try:
        start: int = tracemalloc.get_traced_memory()[0]
        miss(size)
        full: int = tracemalloc.get_traced_memory()[0] - start
        worst: int = full
        for _ in range(64):
            miss(size // 64)
            worst = max(worst, tracemalloc.get_traced_memory()[0] - start)
    finally:
        tracemalloc.stop()
    assert worst <= 1.25 * full, (full, worst, worst / full)


def test_a_build_after_unrelated_names_keeps_its_own_names() -> None:
    """Een volle memo met andere namen, en dan twee keer dezelfde opbouw van zes namen: de tweede rekent niets opnieuw uit. De
    namen die eruit gaan, zijn de oude andere, niet die van de opbouw (zoals `dict.popitem` zou doen)."""
    memo, calls = shouting(10)
    for number in range(10):
        memo(f"ander {number}")
    build: list[str] = [f"opbouw {number}" for number in range(6)]
    for text in build:
        memo(text)
    calls.clear()
    for text in build:
        memo(text)
    assert calls == []


@pytest.mark.volume
async def test_every_cache_has_room_for_a_full_catalogue_build(volume_dir: Path) -> None:
    """Elke cache houdt meer dan een opbouw van alle catalogi erin zet: een tweede opbouw rekent geen enkele naam opnieuw uit, en
    er gaat geen naam uit. Is een cache te klein, dan laat de opbouw zijn eigen namen vallen en rekent de volgende ze opnieuw uit.
    `_significant` onthoudt alleen wat vlak achter elkaar terugkomt (`_parts` vraagt dezelfde tekst drie keer) en doet niet mee."""
    caches: list[Any] = [
        name_keys.fold,
        name_keys.near,
        offer_categories.categorize,
        offer_categories.offer_category,
        line_offers._parts,
        line_offers._stem,
        prices._size_of,
    ]
    for cache in caches:
        cache.cache_clear()
    misses: dict[str, int] = {}
    for _ in range(2):
        prices._CATALOGUES.clear()
        async with auth.connect() as conn:
            for store in offers.STORES:
                await prices._catalogue(conn, store, volume.TODAY)
        for cache in caches:
            info: Any = cache.cache_info()
            assert info.currsize > 0, cache.__name__
            assert info.evicted == 0, (cache.__name__, info)
            assert misses.setdefault(cache.__name__, info.misses) == info.misses, (cache.__name__, info)


# Om een memo te vullen met namen die niet in de catalogus staan: per memo de aanroep met een tekst.
FILLERS: dict[str, Callable[[str], Any]] = {
    "fold": name_keys.fold,
    "near": name_keys.near,
    "categorize": offer_categories.categorize,
    "offer_category": lambda text: offer_categories.offer_category("ah", text),
    "_parts": line_offers._parts,
    "_stem": line_offers._stem,
    "_size_of": lambda text: prices._size_of(text, text),
}


@pytest.mark.volume
async def test_a_full_build_keeps_its_hits_when_the_memos_were_already_full(volume_dir: Path) -> None:
    """Review van taak 9c: in een proces dat dagen draait, zijn de memo's vol met namen van eerder. Dan moet een opbouw nog steeds
    zijn eigen namen houden: de memo's beginnen vol met andere namen, en een tweede opbouw rekent geen naam opnieuw uit."""
    caches: dict[str, Any] = {
        "fold": name_keys.fold,
        "near": name_keys.near,
        "categorize": offer_categories.categorize,
        "offer_category": offer_categories.offer_category,
        "_parts": line_offers._parts,
        "_stem": line_offers._stem,
        "_size_of": prices._size_of,
    }
    for name, cache in caches.items():
        cache.cache_clear()
    try:  # de volle memo's mogen niet naar de volgende test lekken, ook niet als deze faalt
        for name, cache in caches.items():
            number: int = 0
            while cache.cache_info().currsize < cache.cache_info().maxsize:
                FILLERS[name](f"Zzz ander product {number:07d} 250 g")
                number += 1
        before: dict[str, int] = {}
        for round_ in range(2):
            prices._CATALOGUES.clear()
            async with auth.connect() as conn:
                for store in offers.STORES:
                    await prices._catalogue(conn, store, volume.TODAY)
            for name, cache in caches.items():
                misses: int = cache.cache_info().misses
                if round_ == 1:
                    assert misses == before[name], (name, cache.cache_info())
                before[name] = misses
    finally:
        for cache in caches.values():
            cache.cache_clear()


def test_the_memory_is_plain_dicts_the_collector_skips() -> None:
    """Taak 9c: `functools.lru_cache` hield per naam een schakel bij die de GC bij elke volle ronde langsliep (55 ms op de
    volumedatabase). Het geheugen van `TextMemo` is nu twee gewone dicts, en een antwoord van tekst of tuples volgt de GC niet."""
    line_offers._parts.cache_clear()
    for number in range(2000):
        line_offers._parts(f"Folder: AH Verse kipfilet naturel {number} of soep")
    for _ in range(3):  # een ronde laat een tuple pas los als wat erin zit al los is: per laag van de geneste tuples een ronde
        gc.collect()
    memo: Any = line_offers._parts
    assert type(memo._new) is dict and type(memo._old) is dict
    assert not any(type(item).__name__ == "_lru_cache_wrapper" for item in vars(memo).values())
    stored: list[Any] = [*memo._new.values(), *memo._old.values()]
    assert len(stored) == 2000
    assert not any(gc.is_tracked(value) for value in stored)  # gesorteerde tuples van tekst
