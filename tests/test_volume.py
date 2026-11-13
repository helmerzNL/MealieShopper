"""De volumedatabase (`tests/volume.py`): een database op de schaal van productie, en wat de app er bij die omvang mee doet.

Drie soorten tests:

- de generator zelf: dezelfde rijen bij elke run, en de aantallen van productie;
- de vastgelegde antwoorden (`tests/golden/volume.json`): wat de app nu antwoordt, als SHA-256 van het antwoord in JSON met
  gesorteerde sleutels. Het werk aan de event loop mag de antwoorden niet veranderen. `MEALIESHOPPER_GOLDEN=write` schrijft
  het bestand opnieuw in plaats van te vergelijken;
- de stilstanden: elke zware aanvraag laat de event loop nergens langer dan `loop_watch.LIMIT` stilstaan (`quiet`). Wat dat
  nu nog wel doet, staat op `xfail` met de taak die het oplost.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import sqlite3
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

import loop_watch
import pytest
import volume
from quart import Quart

from mealieshopper import ah_price_index, loop_guard, prices
from mealieshopper.app import create_app

if (
    TYPE_CHECKING
):  # niet in de module zelf: pytest zou de klasse `Test...` als test willen verzamelen
    from quart.typing import TestClientProtocol

T = TypeVar("T")

GOLDEN_FILE: Path = Path(__file__).parent / "golden" / "volume.json"
GOLDEN_REQUESTS: dict[str, str] = {
    "offers_ah": "/api/offers?store=ah&week=2026-11-09",
    "offers_jumbo": "/api/offers?store=jumbo&week=2026-11-09",
    "offers_picnic": "/api/offers?store=picnic&week=2026-11-09",
    "offers_crisp": "/api/offers?store=crisp&week=2026-11-09",
    "offer_history": "/api/offers/history?limit=200",
    "offer_history_kaas": "/api/offers/history?q=kaas&limit=50",
    "price_history_ah": "/api/prices/history?store=ah",
    "price_history_ah_melk": "/api/prices/history?store=ah&q=melk",
    "price_history_ah_bought": "/api/prices/history?store=ah&bought=1",
    "price_history_jumbo": "/api/prices/history?store=jumbo",
}
SETTLE_SECONDS: float = 0.05  # zo lang krijgt de bewakerthread om een stilstand te melden, zoals in test_loop_watch.py


def _rows(path: Path, table: str) -> list[tuple[object, ...]]:
    """Alle rijen van een tabel, op volgorde, om twee databases te vergelijken."""
    with contextlib.closing(sqlite3.connect(path)) as conn:
        return sorted(conn.execute(f"SELECT * FROM {table}").fetchall(), key=repr)


def test_the_generator_is_deterministic(tmp_path: Path) -> None:
    first: Path = tmp_path / "a"
    second: Path = tmp_path / "b"
    volume.build(first, scale=0.01)
    volume.build(second, scale=0.01)
    for table in volume.TABLES:
        found: list[tuple[object, ...]] = _rows(first / volume.DB_NAME, table)
        assert found, table
        assert found == _rows(second / volume.DB_NAME, table), table


def test_the_generator_reaches_the_sizes(volume_template: Path) -> None:
    counted: dict[str, int] = volume.counts(volume_template / volume.DB_NAME)
    for name, wanted in volume.SIZES.items():
        assert abs(counted[name] - wanted) <= 0.02 * wanted, (
            name,
            counted[name],
            wanted,
        )


# --- de vastgelegde antwoorden ----------------------------------------------------------------------------------------


def answer_digest(answer: Any) -> str:
    """De SHA-256 van een antwoord als JSON met gesorteerde sleutels. Het antwoord heeft geen waarde die per run verschilt: de
    klok staat vast (`volume.freeze_clock`) en de database ook, dus er hoeft niets uit."""
    text: str = json.dumps(answer, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _recorded() -> dict[str, str]:
    return (
        json.loads(GOLDEN_FILE.read_text(encoding="utf-8"))
        if GOLDEN_FILE.exists()
        else {}
    )


def _record(name: str, digest: str) -> None:
    recorded: dict[str, str] = _recorded()
    recorded[name] = digest
    GOLDEN_FILE.parent.mkdir(exist_ok=True)
    GOLDEN_FILE.write_text(
        json.dumps(recorded, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


@pytest.mark.volume
@pytest.mark.parametrize("name", sorted(GOLDEN_REQUESTS))
async def test_the_answer_is_unchanged(
    volume_client: TestClientProtocol, name: str, tmp_path: Path
) -> None:
    response = await volume_client.get(GOLDEN_REQUESTS[name])
    assert response.status_code == 200
    answer: Any = await response.get_json()
    digest: str = answer_digest(answer)
    if os.environ.get("MEALIESHOPPER_GOLDEN") == "write":
        _record(name, digest)
        return
    expected: str | None = _recorded().get(name)
    if digest != expected:
        saved: Path = tmp_path / f"{name}.json"
        saved.write_text(
            json.dumps(answer, sort_keys=True, indent=1, ensure_ascii=False),
            encoding="utf-8",
        )
        pytest.fail(
            f"Het antwoord op {GOLDEN_REQUESTS[name]} is veranderd ({digest}, vastgelegd {expected}): zie {saved}"
        )


# --- stilstanden ------------------------------------------------------------------------------------------------------


async def settle() -> None:
    """Wacht tot de bewaker een stilstand van zojuist heeft gemeld: hij meldt pas als de loop weer antwoordt, vanuit zijn
    eigen thread. Dus de loop een keer laten draaien en de thread even de tijd geven."""
    await asyncio.sleep(0)
    await asyncio.sleep(SETTLE_SECONDS)


def _too_long(stalls: list[loop_guard.Stall]) -> list[loop_guard.Stall]:
    """Elke stilstand boven de grens telt, ook zonder regel van de app: een rekenthread die de GIL vasthoudt, houdt de loop net
    zo goed tegen."""
    return [stall for stall in stalls if stall.seconds > loop_watch.LIMIT]


async def quiet(
    action: Callable[[], Awaitable[T]],
    attempts: int = 3,
    prepare: Callable[[], Awaitable[None]] | None = None,
) -> T:
    """Voert `action` uit en faalt als de event loop daarbij langer dan `loop_watch.LIMIT` stilstond.

    Bij een stilstand draait `action` opnieuw, tot `attempts` keer: een stilstand door de code herhaalt zich, een hik van de
    machine niet. De test faalt pas als elke poging een stilstand had, met `describe()` van elke stilstand per poging. `prepare`
    loopt voor elke poging buiten de meting (zoals het koud maken: een grote catalogus opruimen houdt de loop zelf even vast).
    Wat `quiet` meet, haalt het zelf uit de bewaker (`loop_watch.drain`): de controle na de test ziet het niet nog eens.
    """
    seen: list[list[loop_guard.Stall]] = []
    for _ in range(attempts):
        if prepare is not None:
            await prepare()
            await settle()
        loop_watch.drain()
        result: T = await action()
        await settle()
        stalls: list[loop_guard.Stall] = _too_long(loop_watch.drain())
        if not stalls:
            return result
        seen.append(stalls)
    lines: list[str] = [
        f"De event loop stond in alle {attempts} pogingen langer dan {round(loop_watch.LIMIT * 1000)} ms stil:"
    ]
    for number, stalls in enumerate(seen, 1):
        lines += [f"  poging {number}:"] + [
            f"    {stall.describe()}" for stall in stalls
        ]
    pytest.fail("\n".join(lines), pytrace=False)


def _execute(path: Path, sql: str) -> None:
    with contextlib.closing(sqlite3.connect(path)) as conn:
        conn.execute(sql)
        conn.commit()


def cold(data_dir: Path) -> Callable[[], Awaitable[None]]:
    """Maakt de aanbiedingen en de prijsgeschiedenis koud, zoals na een synchronisatie: geen catalogus in `prices._CATALOGUES`
    en een nieuwe versie van de catalogus. Die versie staat in de ETag van /api/offers, dus ook de cache van de app heeft
    dan niets. De database wordt in een thread bijgewerkt."""

    async def prepare() -> None:
        prices._CATALOGUES.clear()
        await asyncio.to_thread(
            _execute,
            data_dir / volume.DB_NAME,
            "UPDATE catalogue_versions SET products = products + 1",
        )

    return prepare


def new_sync(data_dir: Path) -> Callable[[], Awaitable[None]]:
    """Een nieuwe synchronisatietijd van de aanbiedingen zonder dat de catalogus verandert (`offers_meta` hoort er niet bij):
    de cache van /api/offers heeft dan niets, de catalogus blijft warm."""
    counter: list[int] = [0]

    async def prepare() -> None:
        counter[0] += 1
        stamp: str = f"2026-11-13T05:{counter[0]:02d}:00+00:00"
        await asyncio.to_thread(
            _execute,
            data_dir / volume.DB_NAME,
            f"UPDATE offers_meta SET synced_at = '{stamp}'",
        )

    return prepare


async def _get(client: TestClientProtocol, path: str) -> Any:
    response = await client.get(path)
    assert response.status_code == 200, path
    return await response.get_json()


def stalls_until(task: str) -> pytest.MarkDecorator:
    """`xfail` voor een aanvraag die nu nog stilstaat, tot `task` dat oplost. Alleen de melding van `quiet`
    (`pytest.fail`) telt als verwacht: een kapotte fixture, een 500 of een `KeyError` is een gewone fout, en een test die
    niet meer stilstaat wordt rood (`strict`)."""
    return pytest.mark.xfail(strict=True, raises=pytest.fail.Exception, reason=task)


COLD_STALLS: dict[str, Any] = {
    "ah": stalls_until("taak 7 en 8"),
    "jumbo": stalls_until("taak 7 en 8"),
}
COLD_STORES: list[Any] = [
    pytest.param(store, marks=COLD_STALLS.get(store, ()))
    for store in ("ah", "jumbo", "picnic", "lidl", "aldi")
]


@pytest.mark.volume
@pytest.mark.parametrize("store", COLD_STORES)
async def test_cold_offers_do_not_stall(
    volume_client: TestClientProtocol, volume_dir: Path, store: str
) -> None:
    answer: Any = await quiet(
        lambda: _get(volume_client, f"/api/offers?store={store}&week=2026-11-09"),
        prepare=cold(volume_dir),
    )
    assert answer["offers"]


@pytest.mark.volume
@stalls_until("taak 8")
async def test_warm_offers_do_not_stall(
    volume_client: TestClientProtocol, volume_dir: Path
) -> None:
    await _get(
        volume_client, "/api/offers?store=ah&week=2026-11-09"
    )  # de catalogus opbouwen
    answer: Any = await quiet(
        lambda: _get(volume_client, "/api/offers?store=ah&week=2026-11-09"),
        prepare=new_sync(volume_dir),
    )
    assert answer["offers"]


@pytest.mark.volume  # geen xfail: sinds 4.21.1 leest de pagina alleen de rijen van zijn producten, en staat hij niet stil
async def test_offer_history_does_not_stall(volume_client: TestClientProtocol) -> None:
    answer: Any = await quiet(
        lambda: _get(volume_client, "/api/offers/history?limit=200")
    )
    assert answer["products"]


PRICE_QUERIES: tuple[str, ...] = (
    "store=ah",
    "store=ah&q=melk",
    "store=ah&bought=1",
    "store=jumbo",
)


@pytest.mark.volume
@stalls_until("taak 7 en 8")  # koud: de catalogus en de lijnacties van de pagina
@pytest.mark.parametrize("query", PRICE_QUERIES)
async def test_price_history_does_not_stall(
    volume_client: TestClientProtocol, volume_dir: Path, query: str
) -> None:
    answer: Any = await quiet(
        lambda: _get(volume_client, f"/api/prices/history?{query}"),
        prepare=cold(volume_dir),
    )
    assert answer["products"]


@pytest.mark.volume
@stalls_until("taak 7 en 8")  # de catalogi en daarna het oordeel van warm_offers
async def test_startup_warming_does_not_stall(volume_dir: Path) -> None:
    app: Quart = create_app()
    async with app.test_app():

        async def warm_at_start() -> None:
            """Wat `before_serving` buiten de tests start (`warm_at_start` in app.py)."""
            await prices.warm_catalogues()
            await app.extensions["mealieshopper_warm_offers"]()

        await quiet(warm_at_start, prepare=cold(volume_dir))


def _bought_outside_the_list(path: Path, count: int, reached: tuple[str, ...]) -> list[ah_price_index.Series]:
    """`count` producten van AH uit de prijsindex (dus gekocht) die het antwoord van /api/offers niet raken (niet in `reached`,
    `prices._answer_ids`: niet in de aanbiedingen, geen andere verpakking en geen naamgenoot), elk met een prijs voor vandaag. Zoals
    de ronde meestal schrijft: de naam, het merk, de verpakking en de prijs van de laatste week die al in `price_observations`
    staat, op een nieuwe dag. Een nieuwe prijswaarde kan een bonregel naar een ander product met dezelfde naam verschuiven, en
    dan bouwt de prijsgeschiedenis de catalogus terecht opnieuw op (`prices._catch_up`)."""
    with contextlib.closing(sqlite3.connect(path)) as conn:
        rows: list[tuple[Any, ...]] = conn.execute(
            "SELECT o.product_id, o.title, o.brand, o.unit_size, o.image, o.price FROM price_observations o "
            "WHERE o.store = 'ah' AND o.source = ? AND o.product_id NOT IN (SELECT value FROM json_each(?)) "
            "AND o.day = (SELECT MAX(day) FROM price_observations p WHERE p.store = 'ah' AND p.product_id = o.product_id) "
            "ORDER BY o.product_id LIMIT ?",
            (prices.PRICE_INDEX, json.dumps(list(reached)), count),
        ).fetchall()
    return [
        ah_price_index.Series(product_id, title, brand, unit_size, image, ((volume.TODAY, price),))
        for product_id, title, brand, unit_size, image, price in rows
    ]


@pytest.mark.volume
async def test_offers_stay_warm_during_a_price_index_round(
    volume_client: TestClientProtocol, volume_dir: Path
) -> None:
    """De ronde langs de prijsindex bewaart een prijs per tien seconden. Een prijs van een product buiten de lijst laat de
    ETag van /api/offers staan, en een prijs van een bekend product bouwt de catalogus niet opnieuw op."""
    path: str = "/api/offers?store=ah&week=2026-11-09"
    first: Any = await volume_client.get(path)  # de catalogus en de lijst opbouwen, buiten de meting
    assert first.status_code == 200
    etag: str = first.headers["ETag"]
    key: tuple[str, str] = (str(volume_dir / volume.DB_NAME), "ah")
    # Na de merge met taak 7 telt deze test de aanroepen van `_build_catalogue`: tot dan hetzelfde object in de cache.
    catalogue: Any = prices._CATALOGUES[key][2]
    async with prices._connect() as conn:
        version: str = await prices._catalogue_version(conn, "ah")
        reached: tuple[str, ...] = await prices._answer_ids(conn, "ah", version, volume.TODAY)
    assert len(reached) > 2000, len(reached)  # de aanbiedingen van AH en wat erbij hoort
    series: list[ah_price_index.Series] = _bought_outside_the_list(volume_dir / volume.DB_NAME, 50, reached)
    assert len(series) == 50
    stamp: str = volume.NOW.isoformat(timespec="seconds")
    for found in series:
        await ah_price_index._mark(found.product_id, "ok", stamp, found)
        again: Any = await quiet(lambda: volume_client.get(path, headers={"If-None-Match": etag}))
        assert again.status_code == 304, found.product_id
    assert prices._CATALOGUES[key][2] is catalogue
    history: Any = await _get(volume_client, "/api/prices/history?store=ah&bought=1")
    assert history["products"]
    assert prices._CATALOGUES[key][2] is catalogue, "de prijsgeschiedenis werkt de catalogus bij, zonder hem opnieuw op te bouwen"
