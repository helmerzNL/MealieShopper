"""Het onthouden per naam (`name_keys.TextMemo`): dezelfde uitkomst als zonder geheugen, en geen werk voor een naam die al bekend is."""

from __future__ import annotations

import random
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
    assert all(isinstance(needed, frozenset) for needed, _ in parts)
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


#: Elke cache moet groter zijn dan wat een opbouw van alle catalogi erin zet, anders verdringt de opbouw zijn eigen namen en daalt het
#: aantal treffers naar bijna nul. `_significant` onthoudt alleen wat vlak achter elkaar terugkomt (`_parts` vraagt dezelfde tekst
#: drie keer) en doet niet mee.
ROOM: float = 1.25


@pytest.mark.volume
async def test_every_cache_has_room_for_a_full_catalogue_build(volume_dir: Path) -> None:
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
    async with auth.connect() as conn:
        for store in offers.STORES:
            await prices._catalogue(conn, store, volume.TODAY)
    for cache in caches:
        info: Any = cache.cache_info()
        assert info.currsize > 0, cache.__name__
        assert info.maxsize >= ROOM * info.currsize, (cache.__name__, info.maxsize, info.currsize)
