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
import gc
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar
from unittest import mock

import helpers
import httpx
import loop_watch
import pytest
import volume
from quart import Quart, Response

from mealieshopper import (
    ah,
    ah_orders,
    ah_price_index,
    ai_match,
    ai_review,
    backup,
    gc_watch,
    image_cache,
    line_offers,
    loop_guard,
    loose_names,
    mcp,
    name_keys,
    offer_categories,
    offers,
    offers_schedule,
    price_archive,
    price_archive_export,
    prices,
    product_images,
    tasks,
)
from mealieshopper import app as app_module
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
    machine niet. De test faalt pas als elke poging een stilstand had, met `describe()` van elke stilstand per poging en de
    lange rondes van de GC in die poging (`loop_watch.GC`): die houden de GIL vast en staan op geen stack. `prepare`
    loopt voor elke poging buiten de meting (zoals het koud maken: een grote catalogus opruimen houdt de loop zelf even vast).
    Wat `quiet` meet, haalt het zelf uit de bewaker (`loop_watch.drain`): de controle na de test ziet het niet nog eens.
    """
    seen: list[list[loop_guard.Stall]] = []
    collected: list[list[gc_watch.Collection]] = []
    for _ in range(attempts):
        if prepare is not None:
            await prepare()
            await settle()
        loop_watch.drain()
        loop_watch.GC.drain()
        result: T = await action()
        await settle()
        stalls: list[loop_guard.Stall] = _too_long(loop_watch.drain())
        if not stalls:
            return result
        seen.append(stalls)
        collected.append(loop_watch.GC.drain())
    lines: list[str] = [
        f"De event loop stond in alle {attempts} pogingen langer dan {round(loop_watch.LIMIT * 1000)} ms stil:"
    ]
    for number, stalls in enumerate(seen, 1):
        lines += [f"  poging {number}:"] + [
            f"    {stall.describe()}" for stall in stalls
        ]
        lines += [f"    {collection.describe()}" for collection in collected[number - 1]]
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


# De catalogus (taak 7) en het oordeel (taak 8) staan niet meer stil: geen winkel heeft nog een markering.
COLD_STALLS: dict[str, Any] = {}
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


def built(data_dir: Path, store: str) -> Callable[[], Awaitable[None]]:
    """Koud zoals `cold`, maar met de catalogus van `store` daarna alweer gebouwd (buiten de meting): wat er dan nog gemeten
    wordt, is het oordeel, de geschiedenis van de lijnacties en de JSON van de lijst. Het bouwen van de catalogus zelf is
    taak 7, en het oordeel doet het werk van een nieuwe catalogus (de lijnindex zoekt elk categoriewoord een keer uit)."""
    make_cold: Callable[[], Awaitable[None]] = cold(data_dir)

    async def prepare() -> None:
        await make_cold()
        await prices.warm_catalogues([store], today=volume.TODAY)

    return prepare


@pytest.mark.volume
@pytest.mark.parametrize("store", ("ah", "jumbo"))
async def test_judging_with_a_built_catalogue_does_not_stall(
    volume_client: TestClientProtocol, volume_dir: Path, store: str
) -> None:
    answer: Any = await quiet(
        lambda: _get(volume_client, f"/api/offers?store={store}&week=2026-11-09"),
        prepare=built(volume_dir, store),
    )
    assert answer["offers"]


@pytest.mark.volume
@pytest.mark.parametrize("query", ("store=ah", "store=ah&q=melk", "store=ah&bought=1"))
async def test_price_history_with_a_built_catalogue_does_not_stall(
    volume_client: TestClientProtocol, volume_dir: Path, query: str
) -> None:
    answer: Any = await quiet(
        lambda: _get(volume_client, f"/api/prices/history?{query}"),
        prepare=built(volume_dir, "ah"),
    )
    assert answer["products"]


@pytest.mark.volume
async def test_the_offers_body_is_serialised_once(
    volume_client: TestClientProtocol,
) -> None:
    path: str = "/api/offers?store=ah&week=2026-11-09"
    with mock.patch.object(
        app_module, "json_bytes", wraps=app_module.json_bytes
    ) as spy:
        first: Response = await volume_client.get(path)
        second: Response = await volume_client.get(path)
    assert first.status_code == second.status_code == 200
    assert spy.call_count == 1
    assert await first.get_data() == await second.get_data()


# Een kleine lijst met een oordeel, een prijs per eenheid en tekens buiten ASCII. Wat de route ervoor gaf (met `jsonify`, voor
# taak 8): de SHA-256 van het lichaam, byte voor byte, de lengte en de Content-Type.
SMALL_OFFERS: list[dict[str, Any]] = [
    {
        "id": "101",
        "title": "AH Crème fraîche",
        "brand": "AH",
        "unitSize": "200 ml",
        "price": 1.19,
        "priceBefore": 1.59,
        "promoLabel": "25% korting",
        "validFrom": "2026-11-09",
        "validUntil": "2026-11-15",
        "category": "zuivel",
    },
    {
        "id": "102",
        "title": "Paté de campagne",
        "brand": "Délifrance",
        "unitSize": "150 g",
        "price": 2.0,
        "priceBefore": None,
        "promoLabel": "2 voor 4.00",
        "validFrom": "2026-11-09",
        "validUntil": "2026-11-15",
    },
    {
        "id": "103",
        "title": "AH Rundergehakt",
        "brand": "AH",
        "unitSize": "500 g",
        "price": 4.99,
        "priceBefore": 6.49,
        "promoLabel": "1+1 gratis",
        "validFrom": "2026-11-02",
        "validUntil": None,
    },
]
SMALL_OBSERVED: list[dict[str, Any]] = [
    {
        "id": "101",
        "title": "AH Crème fraîche",
        "brand": "AH",
        "unitSize": "200 ml",
        "price": 1.49,
    },
    {
        "id": "103",
        "title": "AH Rundergehakt",
        "brand": "AH",
        "unitSize": "500 g",
        "price": 6.29,
    },
]
SMALL_BODY_SHA256: str = (
    "e6db0ad545ade72d9e460829e62c61d0d78cbbc1eb39b76982c77c2f1b0a5cee"
)
SMALL_BODY_LENGTH: int = 3893
SMALL_CONTENT_TYPE: str = "application/json"


SMALL_HEADERS: dict[
    str, str
] = {  # zonder de ETag, die per run verschilt (het token van de catalogus)
    "content-type": "application/json",
    "content-length": "3893",
    "cache-control": "private, no-cache",
    "vary": "Accept-Encoding",
}
SMALL_PATH: str = "/api/offers?store=ah&week=2026-11-09"


async def small_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """De kleine lijst (`SMALL_OFFERS`) in de datamap, met de klok op `volume.TODAY` en zonder inloggen."""
    monkeypatch.setenv("PASSKEY_AUTH_ENABLED", "false")
    volume.freeze_clock(monkeypatch)
    await prices.record("ah", SMALL_OBSERVED, now="2026-11-01T08:00:00+00:00")
    with mock.patch.dict(
        offers.FETCHERS, {"ah": lambda: helpers.returned(SMALL_OFFERS)}
    ):
        await offers.sync("ah")


async def test_the_offers_response_is_the_same_json_as_jsonify(
    client: TestClientProtocol, monkeypatch: pytest.MonkeyPatch
) -> None:
    await small_list(monkeypatch)
    response: Response = await client.get(SMALL_PATH)
    body: bytes = await response.get_data()
    assert response.status_code == 200
    assert response.headers["Content-Type"] == SMALL_CONTENT_TYPE
    assert response.headers["Content-Length"] == str(SMALL_BODY_LENGTH)
    assert {
        name.lower(): value
        for name, value in response.headers.items()
        if name.lower() != "etag"
    } == SMALL_HEADERS
    # wat de digest vastlegt: ensure_ascii, gesorteerde sleutels zonder spaties, een regeleinde, en een lijst met oordeel
    assert b"Cr\\u00e8me fra\\u00eeche" in body and b'"verdict":{' in body
    assert body.startswith(b'{"categories":') and body.endswith(b"}\n")
    assert (hashlib.sha256(body).hexdigest(), len(body)) == (
        SMALL_BODY_SHA256,
        SMALL_BODY_LENGTH,
    )


async def test_an_error_in_the_json_reaches_the_request_and_is_not_kept(
    client: TestClientProtocol, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Review Focus 1: de aanvraag krijgt de fout, de cache bewaart hem niet en de volgende aanvraag rekent opnieuw
    await small_list(monkeypatch)
    with mock.patch.object(app_module, "json_bytes", side_effect=TypeError("stuk")):
        failed: Response = await client.get(SMALL_PATH)
    assert failed.status_code == 424
    assert (await failed.get_json())["error"] == "stuk"
    with mock.patch.object(
        app_module, "json_bytes", wraps=app_module.json_bytes
    ) as spy:
        again: Response = await client.get(SMALL_PATH)
    assert again.status_code == 200
    assert spy.call_count == 1
    assert hashlib.sha256(await again.get_data()).hexdigest() == SMALL_BODY_SHA256


async def test_a_request_that_goes_away_leaves_the_whole_list(
    client: TestClientProtocol, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Review Focus 2: de eerste aanvraag gaat weg terwijl de JSON wordt gemaakt, de tweede krijgt de hele lijst, en de cache
    # heeft daarna de hele lijst (de derde rekent niets meer)
    await small_list(monkeypatch)
    started: threading.Event = threading.Event()
    release: threading.Event = threading.Event()
    real: Callable[[Any], bytes] = app_module.json_bytes

    def slow(data: Any) -> bytes:
        started.set()
        release.wait(5)
        return real(data)

    with mock.patch.object(app_module, "json_bytes", side_effect=slow) as spy:
        first: asyncio.Task[Response] = asyncio.ensure_future(client.get(SMALL_PATH))
        assert await asyncio.to_thread(started.wait, 5)
        second: asyncio.Task[Response] = asyncio.ensure_future(client.get(SMALL_PATH))
        await asyncio.sleep(0.05)
        first.cancel()
        release.set()
        answer: Response = await second
        third: Response = await client.get(SMALL_PATH)
    assert first.cancelled()
    assert answer.status_code == third.status_code == 200
    assert spy.call_count == 1
    for response in (answer, third):
        assert (
            hashlib.sha256(await response.get_data()).hexdigest() == SMALL_BODY_SHA256
        )


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
# Koud: de catalogus (taak 7) en de lijnacties van de pagina (taak 8) staan niet meer stil.
PRICE_STALLS: dict[str, Any] = {}


@pytest.mark.volume
@pytest.mark.parametrize(
    "query",
    [pytest.param(query, marks=PRICE_STALLS.get(query, ())) for query in PRICE_QUERIES],
)
async def test_price_history_does_not_stall(
    volume_client: TestClientProtocol, volume_dir: Path, query: str
) -> None:
    answer: Any = await quiet(
        lambda: _get(volume_client, f"/api/prices/history?{query}"),
        prepare=cold(volume_dir),
    )
    assert answer["products"]


@pytest.mark.volume
async def test_startup_warming_does_not_stall(volume_dir: Path) -> None:
    app: Quart = create_app()
    async with app.test_app():

        async def warm_at_start() -> None:
            """Wat `before_serving` buiten de tests start (`warm_at_start` in app.py)."""
            await prices.warm_catalogues()
            await app.extensions["mealieshopper_warm_offers"]()

        await quiet(warm_at_start, prepare=cold(volume_dir))


@pytest.mark.volume
async def test_two_cold_requests_build_the_catalogue_once(volume_dir: Path) -> None:
    # De versie van de catalogus staat vast voordat de twee aanvragen haar lezen.
    await prices.signature("ah")
    with mock.patch.object(
        prices, "_build_catalogue", wraps=prices._build_catalogue
    ) as built:
        await asyncio.gather(
            prices.annotate("ah", [], today=volume.TODAY),
            prices.annotate("ah", [], today=volume.TODAY),
        )
    assert built.call_count == 1


@pytest.mark.volume
async def test_building_the_catalogue_does_not_stall(volume_dir: Path) -> None:
    await quiet(
        lambda: prices.warm_catalogues(["ah"], today=volume.TODAY),
        prepare=cold(volume_dir),
    )


class _Wrapper:
    """Zoals hypercorn 0.18 `ProtocolWrapper`: zijn `send` is een gebonden methode van de server."""

    def __init__(self, send: Callable[[object], None]) -> None:
        self.send: Callable[[object], None] = send


class _Server:
    """Zoals hypercorn 0.18 `TCPServer`: `protocol` wijst naar de wrapper en de wrapper terug naar de server, een kring die bij
    het sluiten niemand breekt. Alleen een volle ronde van de GC ruimt hem op, en dan telt `removed` hem."""

    removed: int = 0

    def __init__(self) -> None:
        self.protocol: _Wrapper = _Wrapper(self.protocol_send)

    def protocol_send(self, event: object) -> None:
        del event

    def __del__(self) -> None:
        _Server.removed += 1


def _dead_cycles_in_the_old_generation(count: int) -> None:
    """`count` gesloten verbindingen als afval in generatie 2: eerst levend naar generatie 2 (een ronde over de jonge
    generaties), dan los. Alleen een volle ronde ruimt zo'n kring op."""
    servers: list[_Server] = [_Server() for _ in range(count)]
    gc.collect(1)
    del servers


PRODUCT_CHANGE: str = (
    "UPDATE price_observations SET title = title || ' (nieuw)'"
    " WHERE rowid = (SELECT MIN(rowid) FROM price_observations WHERE store = 'ah')"
)
PLANTED: int = 500  # gesloten verbindingen per ronde, elk vijf objecten die de GC volgt


@pytest.mark.volume
async def test_rebuilds_do_not_freeze_dead_cycles(volume_client: TestClientProtocol, volume_dir: Path) -> None:
    # Review van taak 9b: gc.collect(1) en gc.freeze() bij een bouw bevroren ook dode kringen uit generatie 2, voor de rest van
    # het proces. Sinds taak 9c bevriest een bouw niets meer: dit houdt dat zo.
    # Elke ronde: dode kringen zoals gesloten verbindingen van hypercorn, dan een nieuwe catalogus (koud, of na een wijziging
    # van een product). De drempel van generatie 2 staat hoog, zodat geen volle ronde van zichzelf het afval opruimt voor de
    # bouw: zo ligt het er nog op het moment dat de catalogus opzij gaat, zoals in productie tussen twee volle rondes.
    thresholds: tuple[int, int, int] = gc.get_threshold()
    removed: int = _Server.removed
    frozen: list[int] = []
    gc.set_threshold(thresholds[0], thresholds[1], 1_000_000)
    try:
        for number in range(4):
            _dead_cycles_in_the_old_generation(PLANTED)
            if number == 2:
                await asyncio.to_thread(_execute, volume_dir / volume.DB_NAME, PRODUCT_CHANGE)
            else:
                await cold(volume_dir)()
            await _get(volume_client, "/api/prices/history?store=ah")
            frozen.append(gc.get_freeze_count())
    finally:
        gc.set_threshold(*thresholds)
    gc.collect()
    assert _Server.removed - removed == 4 * PLANTED  # niets van het afval is voor altijd bevroren
    assert len(set(frozen)) == 1, frozen  # er komt niets bij in de bevroren generatie


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
    ETag van /api/offers staan, en een prijs van een bekend product bouwt de catalogus niet opnieuw op (`_build_catalogue`)."""
    path: str = "/api/offers?store=ah&week=2026-11-09"
    first: Any = await volume_client.get(path)  # de catalogus en de lijst opbouwen, buiten de meting
    assert first.status_code == 200
    etag: str = first.headers["ETag"]
    async with prices._connect() as conn:
        version: str = await prices._catalogue_version(conn, "ah")
        reached: tuple[str, ...] = await prices._answer_ids(conn, "ah", version, volume.TODAY)
    assert len(reached) > 2000, len(reached)  # de aanbiedingen van AH en wat erbij hoort
    series: list[ah_price_index.Series] = _bought_outside_the_list(volume_dir / volume.DB_NAME, 50, reached)
    assert len(series) == 50
    stamp: str = volume.NOW.isoformat(timespec="seconds")
    with mock.patch.object(prices, "_build_catalogue", wraps=prices._build_catalogue) as built:
        for found in series:
            await ah_price_index._mark(found.product_id, "ok", stamp, found)
            again: Any = await quiet(lambda: volume_client.get(path, headers={"If-None-Match": etag}))
            assert again.status_code == 304, found.product_id
        history: Any = await _get(volume_client, "/api/prices/history?store=ah&bought=1")
    assert history["products"]
    assert built.call_count == 0, "de prijzen en de prijsgeschiedenis werken de catalogus bij, zonder hem opnieuw op te bouwen"


# Wat alle catalogi en de memo's van hun namen aan een volle ronde van de GC toevoegen: het verschil voor en na het laden, want in
# de hele suite leeft de rest van de tests mee (een volle ronde loopt dan ook langs hun 340.000 objecten). Gemeten na taak 9c:
# 1.300 gevolgde objecten en 2,4 ms erbij (3.12), 17.000 en 8 ms (3.15, die ook de dicts van de bonregels volgt). Ervoor voegden
# de catalogi 525.000 objecten en 125 ms per ronde toe, zes tot acht keer per koude bouw van AH (gc_watch.py).
ADDED_TRACKED_LIMIT: int = 60_000
ADDED_COLLECTION_SECONDS: float = 0.05
NAME_MEMOS: tuple[Any, ...] = (
    name_keys.fold,
    name_keys.near,
    offer_categories.categorize,
    offer_categories.offer_category,
    line_offers._parts,
    line_offers._stem,
    line_offers._significant,
    prices._size_of,
)


def _full_collection() -> tuple[float, int]:
    """De beste van drie volle rondes van de GC (een hik van de machine telt niet), en hoeveel objecten de GC daarna volgt."""
    seconds: list[float] = []
    for _ in range(3):
        started: float = time.perf_counter()
        gc.collect()
        seconds.append(time.perf_counter() - started)
    return min(seconds), len(gc.get_objects())


@pytest.mark.volume
async def test_the_catalogues_keep_a_full_collection_short(volume_client: TestClientProtocol) -> None:
    for memo in NAME_MEMOS:
        memo.cache_clear()
    # In een thread: een volle ronde op de loop telde bij de bewaker als stilstand van de app (de callback van gc_watch is de
    # eerste regel van de app na de ronde), met de hele suite erbij 270 ms op 3.15. De ronde zelf is hier wat de test meet.
    before_seconds, before_tracked = await asyncio.to_thread(_full_collection)
    await prices.warm_catalogues(today=volume.TODAY)
    for store in ("ah", "jumbo"):
        await _get(volume_client, f"/api/offers?store={store}&week=2026-11-09")
        await _get(volume_client, f"/api/prices/history?store={store}")
    assert len(prices._CATALOGUES) == len(offers.STORES)
    after_seconds, after_tracked = await asyncio.to_thread(_full_collection)
    assert after_tracked - before_tracked < ADDED_TRACKED_LIMIT, (before_tracked, after_tracked)
    assert after_seconds - before_seconds < ADDED_COLLECTION_SECONDS, (before_seconds, after_seconds)


# Hoeveel gevolgde objecten een koude bouw van AH er hooguit bij heeft op het moment dat een volle ronde van de GC begint: dan
# loopt de ronde langs alles, met de GIL vast. Na taak 9c is de vaste stand met alle catalogi zo'n 120.000 (de app en de tests
# erbij), dus dit houdt de piek ruim binnen twee keer de vaste stand. Gemeten (3.12): voor de review van taak 9c 363.590 erbij (de
# rijen als sqlite3.Row, een dict en twee sets per product, een lijst per product in `_link_receipt_rows`), daarna 50.283. Wat
# overblijft zijn vooral de regels van de bon als sqlite3.Row (17.000) en de tuples van de producten tot een ronde ze loslaat.
BUILD_PEAK_ADDED_LIMIT: int = 80_000


@pytest.mark.volume
async def test_a_cold_build_keeps_the_tracked_set_small(volume_dir: Path) -> None:
    peaks: list[int] = []

    def at_full_collection(phase: str, info: dict[str, Any]) -> None:
        if phase == "start" and info.get("generation") == 2:
            peaks.append(len(gc.get_objects()))

    # De heap van de rest van de suite gaat in de vaste generatie: een volle ronde komt pas als er sinds de vorige een kwart van
    # wat de oude generatie telt bij kwam, en met de hele suite erin kwam die tijdens de bouw niet (de test mat dan niets). De
    # tweede `collect` zet die telling op wat na `freeze` over is: bijna niets. `get_objects` telt de vaste generatie niet.
    gc.collect()
    gc.freeze()
    try:
        gc.collect()
        before: int = len(gc.get_objects())
        gc.callbacks.append(at_full_collection)
        try:
            await prices.warm_catalogues(["ah"], today=volume.TODAY)
        finally:
            gc.callbacks.remove(at_full_collection)
    finally:
        gc.unfreeze()
    assert ("ah" in {key[1] for key in prices._CATALOGUES}), "de catalogus van AH is gebouwd"
    assert peaks, "geen volle ronde van de GC tijdens de bouw: dan meet deze test niets"
    assert max(peaks) - before < BUILD_PEAK_ADDED_LIMIT, (before, peaks)


# --- doeltijden -------------------------------------------------------------------------------------------------------
#
# Het ontwerp van taak 9 zet doeltijden voor /api/offers?store=ah, als `total` van Server-Timing (MEALIESHOPPER_TELEMETRY): opnieuw
# oordelen met de catalogus in het geheugen onder 1 s, koud onder 1,5 s. De grenzen hieronder hebben marge voor een tragere
# CI-machine, zodat een test pas faalt bij een echte terugval. Gemeten na taak 9d op de volumedatabase (beste van drie, Python 3.12,
# met andere agents op de machine): opnieuw oordelen 957 ms, koud 2.618 ms. Het grootste deel van koud is de bouw van de
# catalogus, die taak 9c kleiner maakt.
OFFERS_AH: str = "/api/offers?store=ah&week=2026-11-09"
REJUDGE_LIMIT_MS: float = 1500.0
COLD_LIMIT_MS: float = 2500.0
_TOTAL: re.Pattern[str] = re.compile(r"\btotal;dur=([0-9.]+)")


def server_total(response: Response) -> float:
    """De `total` van de header Server-Timing, in milliseconden."""
    found: re.Match[str] | None = _TOTAL.search(response.headers.get("Server-Timing", ""))
    assert found is not None, response.headers.get("Server-Timing")
    return float(found.group(1))


async def best_total(
    volume_dir: Path, monkeypatch: pytest.MonkeyPatch, prepare: Callable[[], Awaitable[None]], attempts: int = 3
) -> float:
    """De beste `total` van `attempts` aanvragen van `OFFERS_AH`, elk na `prepare`, op een app met de telemetrie aan. De eerste
    aanvraag bouwt de catalogus en vult de caches van de namen (buiten de meting), zoals een app die al even draait."""
    monkeypatch.setenv("MEALIESHOPPER_TELEMETRY", "true")
    app: Quart = create_app()
    totals: list[float] = []
    async with app.test_app() as test_app:
        client: TestClientProtocol = test_app.test_client()
        assert (await client.get(OFFERS_AH)).status_code == 200
        for _ in range(attempts):
            await prepare()
            response: Response = await client.get(OFFERS_AH)
            assert response.status_code == 200
            totals.append(server_total(response))
    return min(totals)


@pytest.mark.volume
async def test_judging_with_a_built_catalogue_is_fast_enough(volume_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    best: float = await best_total(volume_dir, monkeypatch, built(volume_dir, "ah"))
    assert best < REJUDGE_LIMIT_MS, f"opnieuw oordelen duurde {best:.0f} ms (grens {REJUDGE_LIMIT_MS:.0f} ms)"


@pytest.mark.volume
async def test_cold_offers_are_fast_enough(volume_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    best: float = await best_total(volume_dir, monkeypatch, cold(volume_dir))
    assert best < COLD_LIMIT_MS, f"koud duurde {best:.0f} ms (grens {COLD_LIMIT_MS:.0f} ms)"
# --- de andere zware paden (taak 10) ----------------------------------------------------------------------------------
#
# Elk pad dat met de rijen van productie rekent: de status van de aanbiedingen, het vergelijken van verpakkingen, de pagina's van
# de aankopen, een synchronisatie, de rondes op de achtergrond (prijsindex, losse namen, koppelen) en de nachtelijke run. Een
# winkel is nagebootst: een zoekopdracht geeft een vaste lijst, een pagina van de prijsindex is de opgenomen pagina, en wachten
# tussen twee verzoeken duurt niets.

PRICE_INDEX_PAGE: Path = (
    Path(__file__).parent / "fixtures" / "ahprijsindex-product-446406.html"
)
PRICE_INDEX_PRODUCTS: int = (
    25  # producten per ronde van de prijsindex, elk met 225 weekprijzen
)
STAMP: str = volume.NOW.isoformat(timespec="seconds")


async def _no_wait(seconds: float) -> None:
    """Wachten tussen twee verzoeken aan een winkel: in een volumetest meteen klaar."""
    del seconds


async def _always_linked(store: str) -> bool:
    """`offers.linked` voor een nagebootste winkel: elke winkel is gekoppeld."""
    del store
    return True


def _scalar(path: Path, sql: str) -> Any:
    with contextlib.closing(sqlite3.connect(path)) as conn:
        return conn.execute(sql).fetchone()[0]


def _store_offers(path: Path, store: str) -> list[dict[str, Any]]:
    """De aanbiedingen van `store` uit de volumedatabase in de vorm van `offers.FETCHERS`: wat een nagebootste winkel teruggeeft."""
    with contextlib.closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        rows: list[sqlite3.Row] = conn.execute(
            "SELECT * FROM offers WHERE store = ? ORDER BY position", (store,)
        ).fetchall()
    return [
        {"id": row["product_id"], "title": row["title"], "brand": row["brand"], "unitSize": row["unit_size"], "price": row["price"],
         "priceBefore": row["price_before"], "promoLabel": row["promo_label"], "image": row["image"], "url": row["url"],
         "validFrom": row["valid_from"], "validUntil": row["valid_until"], "categoryHint": row["category_hint"]}
        for row in rows
    ]  # fmt: skip


def _fetcher(
    found: list[dict[str, Any]],
) -> Callable[[], Awaitable[list[dict[str, Any]]]]:
    """Een fetcher van `offers.FETCHERS` die `found` geeft, elke keer een nieuwe kopie (`offers.sync` past de dicts niet aan)."""

    async def fetch() -> list[dict[str, Any]]:
        return [dict(offer) for offer in found]

    return fetch


def _shifted(found: list[dict[str, Any]], step: int) -> list[dict[str, Any]]:
    """`found` met een andere prijs voor een op de tien aanbiedingen: een synchronisatie sluit dan rijen in `offer_history` en
    begint nieuwe, zoals elke week."""
    return [
        {**offer, "price": round(float(offer["price"] or 1) + 0.01 * step, 2)}
        if number % 10 == step % 10
        else offer
        for number, offer in enumerate(found)
    ]


async def _pack_search(query: Any, limit: int = 5) -> list[dict[str, Any]]:
    """Een zoekopdracht bij een nagebootste winkel: `limit` verpakkingen van het gezochte product, in de vorm van `search_products`."""
    return [
        {"id": f"{9_000_000 + number}", "title": f"{query} {100 * (number + 1)} g", "brand": "AH",
         "unitSize": f"{100 * (number + 1)} g", "price": round(0.49 + 0.37 * number, 2), "image": "", "url": ""}
        for number in range(limit)
    ]  # fmt: skip


def _hq_search(
    path: Path,
) -> Callable[[str], Awaitable[list[tuple[int, int, str, str]]]]:
    """`ah.search_hq_ids` van een nagebootste AH: een zoekopdracht vindt de artikelnummers van de bonregels met die naam (de helft
    van de namen), zoals AH ze teruggeeft: (hqId, webshopId, titel, foto)."""
    with contextlib.closing(sqlite3.connect(path)) as conn:
        rows: list[tuple[Any, ...]] = conn.execute(
            "SELECT DISTINCT name, product_id FROM ah_receipt_items WHERE product_id IS NOT NULL"
        ).fetchall()
    by_name: dict[str, list[int]] = {}
    for name, product_id in rows:
        by_name.setdefault(" ".join(str(name).split()).casefold(), []).append(
            int(product_id)
        )

    async def search(query: str) -> list[tuple[int, int, str, str]]:
        ids: list[int] = by_name.get(" ".join(query.split()).casefold(), [])
        if hash(query) % 2:
            return []
        return [
            (
                hq_id,
                7_000_000 + hq_id,
                query.title(),
                f"https://static.ah.nl/dam/product/{hq_id}",
            )
            for hq_id in ids
        ]

    return search


async def _ah_products(product_ids: list[int]) -> list[dict[str, Any]]:
    """`ah.get_products_by_ids` van een nagebootste AH: elk product met een foto, een merk en een inhoud."""
    return [
        {"id": product_id, "title": f"AH Product {product_id}", "brand": "AH", "unitSize": "1 stuk",
         "image": f"https://static.ah.nl/dam/product/{product_id}"}
        for product_id in product_ids
    ]  # fmt: skip


async def _no_image(*args: Any) -> tuple[bytes, str]:
    """`_download` van de afbeeldingencache en van `product_images` zonder winkel: geen bruikbare afbeelding, zoals een winkel
    die iets anders terugstuurt."""
    del args
    raise ValueError("geen afbeelding in een volumetest")


@pytest.fixture
def fake_stores(volume_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Nagebootste winkels: AH voor het koppelen en de foto's van de bonnen, zonder wachten en zonder uurbudget (de tests draaien
    de rondes vaker na elkaar dan een uur toestaat), en geen enkele foto (de afbeeldingencache en `product_images`)."""
    monkeypatch.setattr(ah, "search_hq_ids", _hq_search(volume_dir / volume.DB_NAME))
    monkeypatch.setattr(ah, "get_products_by_ids", _ah_products)
    monkeypatch.setattr(ah_orders, "_wait", _no_wait)
    monkeypatch.setattr(ah_orders, "RECEIPT_PRODUCTS_PER_HOUR", 1_000_000)
    monkeypatch.setattr(image_cache, "_wait", _no_wait)
    monkeypatch.setattr(image_cache, "_download", _no_image)
    monkeypatch.setattr(product_images, "_download", _no_image)


async def _finished(*names: str) -> None:
    """Wacht op de achtergrondtasks met deze namen (`tasks.running`), als ze lopen: hun werk hoort bij de meting."""
    for name, task in tasks.running().items():
        base, _, number = name.rpartition("-")
        if name in names or (base in names and number.isdigit()):  # `tasks.spawn` geeft een tweede task "-2" erachter
            await task


@pytest.mark.volume
async def test_offer_status_and_overview_do_not_stall(
    volume_client: TestClientProtocol,
) -> None:
    answer: Any = await quiet(lambda: _get(volume_client, "/api/offers/status"))
    assert answer["stores"]["ah"]["total"] == volume.SIZES["offers_ah"]


@pytest.mark.volume
async def test_price_compare_does_not_stall(
    volume_client: TestClientProtocol, volume_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Een gekocht product buiten de aanbiedingen: `prices._known_product` leest dan de lijst en de bewaarde prijzen.
    product_id: str = _scalar(
        volume_dir / volume.DB_NAME,
        "SELECT CAST(i.product_id AS TEXT) FROM ah_order_items i JOIN price_observations o ON o.store = 'ah' "
        "AND o.product_id = CAST(i.product_id AS TEXT) AND o.source != 'public_unknown' WHERE CAST(i.product_id AS TEXT) NOT IN "
        "(SELECT product_id FROM offers WHERE store = 'ah') ORDER BY i.product_id LIMIT 1",
    )
    monkeypatch.setattr(prices, "_search_function", lambda store: _pack_search)
    answer: Any = await quiet(
        lambda: _get(volume_client, f"/api/prices/compare?store=ah&id={product_id}")
    )
    assert answer["candidates"]


PURCHASE_PAGES: tuple[str, ...] = (
    "/api/ah/orders",
    "/api/ah/orders/900000",
    "/api/ah/receipts",
    "/api/ah/receipts/bon-00000",
    "POST /api/ah/receipts/bon-00000/products",
    "/api/ah/receipts/search?q=",
    "/api/ah/receipts/search?q=melk&limit=all",
    "/api/ah/purchases/search?q=&limit=all",
    "/api/ah/purchases/search?q=melk&limit=all",
    "/api/jumbo/orders",
    "/api/jumbo/purchases/search?q=&limit=all",
    "/api/picnic/orders",
    "/api/crisp/orders",
)


@pytest.mark.volume
@pytest.mark.parametrize("path", PURCHASE_PAGES)
async def test_purchase_and_receipt_pages_do_not_stall(
    volume_client: TestClientProtocol, fake_stores: None, path: str
) -> None:
    method, _, address = path.rpartition(" ")

    async def page() -> Any:
        response: Response = await volume_client.open(address, method=method or "GET")
        assert response.status_code == 200, (path, response.status_code)
        await _finished(
            "receipt-images"
        )  # de bonnenlijst vult de foto's aan op de achtergrond
        return await response.get_json()

    assert await quiet(page)


@pytest.mark.volume
async def test_an_offers_sync_does_not_stall(
    volume_client: TestClientProtocol, volume_dir: Path, fake_stores: None
) -> None:
    found: list[dict[str, Any]] = _store_offers(volume_dir / volume.DB_NAME, "ah")
    assert len(found) == volume.SIZES["offers_ah"]
    step: list[int] = [0]

    async def sync_and_list() -> Any:
        step[0] += 1
        with mock.patch.dict(
            offers.FETCHERS, {"ah": _fetcher(_shifted(found, step[0]))}
        ):
            await offers.sync("ah")
        return await _get(volume_client, "/api/offers?store=ah&week=2026-11-09")

    answer: Any = await quiet(sync_and_list)
    assert answer["offers"]


def _price_index_page(request: httpx.Request) -> httpx.Response:
    """De opgenomen pagina van ahprijsindex.nl, voor het product van het verzoek: 225 weekprijzen."""
    product_id: str = str(request.url.path).rsplit("/", 1)[-1]
    text: str = PRICE_INDEX_PAGE.read_text(encoding="utf-8").replace(
        "446406", product_id
    )
    return httpx.Response(
        200, text=text, headers={"Content-Type": "text/html; charset=utf-8"}
    )


@pytest.mark.volume
async def test_a_price_index_round_does_not_stall(
    volume_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEALIESHOPPER_PRICE_INDEX", "true")
    monkeypatch.setattr(ah_price_index, "_sleep", _no_wait)
    with helpers.served(side_effect=_price_index_page):
        result: Any = await quiet(
            lambda: ah_price_index.run_round(limit=PRICE_INDEX_PRODUCTS, now=STAMP)
        )
    assert result["checked"] == PRICE_INDEX_PRODUCTS and result["prices"] > 0, result


async def _loose_search(name: Any, limit: int = 5) -> list[dict[str, Any]]:
    """Een zoekopdracht van de ronde voor losse namen bij een nagebootste winkel: een paar producten met de naam erin."""
    return [
        {"id": f"{8_000_000 + number}", "title": f"{name} {('', 'mini', 'groot', 'bio')[number % 4]}".strip(), "brand": "AH",
         "unitSize": f"{250 * (number + 1)} g", "price": round(1.29 + 0.5 * number, 2), "image": "", "url": ""}
        for number in range(min(limit, 6))
    ]  # fmt: skip


@pytest.mark.volume
async def test_the_loose_names_round_does_not_stall(
    volume_dir: Path, fake_stores: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(loose_names, "_default_search", lambda store: _loose_search)
    monkeypatch.setattr(loose_names, "_wait", _no_wait)
    result: Any = await quiet(
        lambda: loose_names.run_all_stores("nightly", now=STAMP, only="ah")
    )
    assert result["stores"]["ah"]["searches"] > 0, result


@pytest.mark.volume
async def test_linking_receipt_lines_does_not_stall(
    volume_dir: Path, fake_stores: None
) -> None:
    async def linking_round() -> Any:
        await ah_orders.run_linking(None)
        await _finished("linking-run")
        return await ah_orders.linking_status()

    status: Any = await quiet(linking_round)
    assert status


MCP_TOOLS: tuple[Any, ...] = (
    pytest.param(
        "get_price_history", {"store": "ah", "limit": 50}, id="get_price_history"
    ),
    pytest.param(
        "get_price_history",
        {"store": "ah", "bought": True, "limit": 50},
        id="get_price_history-bought",
    ),
    pytest.param(
        "get_price_history", {"query": "melk", "limit": 50}, id="get_price_history-melk"
    ),
    pytest.param(
        "review_offer_matches", {"store": "ah", "limit": 100}, id="review_offer_matches"
    ),
    pytest.param("list_offer_mismatches", {}, id="list_offer_mismatches"),
    pytest.param("list_ah_orders", {"limit": 200}, id="list_ah_orders"),
    pytest.param("list_ah_receipts", {"limit": 200}, id="list_ah_receipts"),
    pytest.param("get_ah_receipt", {"receipt_id": "bon-00000"}, id="get_ah_receipt"),
    pytest.param(
        "search_ah_purchases", {"query": "melk", "limit": 200}, id="search_ah_purchases"
    ),
)


@pytest.mark.volume
@pytest.mark.parametrize(("tool", "arguments"), MCP_TOOLS)
async def test_mcp_tools_do_not_stall(
    volume_client: TestClientProtocol, tool: str, arguments: dict[str, Any]
) -> None:
    answer: Any = await quiet(lambda: mcp.call_tool(tool, arguments))
    assert not answer["isError"], answer


@pytest.mark.volume
async def test_the_nightly_tasks_do_not_stall(
    volume_dir: Path, fake_stores: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    path: Path = volume_dir / volume.DB_NAME
    stores: dict[str, list[dict[str, Any]]] = {
        store: _store_offers(path, store) for store in offers.STORES
    }
    monkeypatch.setattr(offers, "linked", _always_linked)
    monkeypatch.setattr(loose_names, "_default_search", lambda store: _loose_search)
    monkeypatch.setattr(loose_names, "_wait", _no_wait)
    step: list[int] = [0]
    app: Quart = (
        create_app()
    )  # met de opwarmer van /api/offers, die de run als laatste aanroept
    async with app.test_app():

        async def nightly() -> Any:
            step[0] += 1
            fetchers: dict[str, Any] = {
                store: _fetcher(_shifted(found, step[0]))
                for store, found in stores.items()
            }
            with mock.patch.dict(offers.FETCHERS, fetchers):
                result: Any = await offers_schedule.run_all("scheduled", volume.NOW)
            await _finished(
                loose_names.TASK_NAME,
                ah_price_index.TASK_NAME,
                "warm-offers",
                "image-prefetch",
            )
            return result

        result: Any = await quiet(nightly)
    assert result["stores"]["ah"]["count"], result


FILE_CHUNK: int = 1024 * 1024


async def _file_chunks(path: Path) -> AsyncIterator[bytes]:
    """Een bestand in stukken van een MB, zoals `request.body` een upload aanlevert. Het lezen gaat in een thread."""
    with path.open("rb") as handle:
        while chunk := await asyncio.to_thread(handle.read, FILE_CHUNK):
            yield chunk


@pytest.mark.volume
async def test_a_backup_and_its_restore_do_not_stall(volume_client: TestClientProtocol, volume_dir: Path) -> None:
    # Wat de knoppen onder Instellingen > Back-up doen: het bestand maken (in een thread, zoals de route), het klaarzetten en
    # controleren, en het terugzetten als achtergrondtask (`backup-restore-<id>`).
    made: Path = volume_dir / "volume-backup.json"
    await backup.ensure_schemas()

    async def backup_and_restore() -> Any:
        await asyncio.to_thread(backup.write_backup, made, links=False, box=None)
        staged: dict[str, Any] = await backup.stage(_file_chunks(made), None)
        job: dict[str, Any] = await backup.start_job(staged["token"], "")
        await _finished(f"backup-restore-{job['id']}")
        return await backup.latest_job()

    try:
        job: Any = await quiet(backup_and_restore)
    finally:
        made.unlink(missing_ok=True)
    assert job["status"] == "done", job


@pytest.mark.volume
async def test_a_price_archive_import_does_not_stall(volume_client: TestClientProtocol, volume_dir: Path) -> None:
    # Een export van het prijsarchief (in een thread, zoals de route) en die weer inlezen: het klaarzetten en controleren, en
    # het invoegen als achtergrondtask (`price-archive-<id>`). Alles staat er al, dus het inlezen vergelijkt elke regel.
    made: Path = volume_dir / "volume-archive.zip"
    await price_archive.ensure_schema()

    async def export_and_import() -> Any:
        await asyncio.to_thread(price_archive_export.write_export, made)
        staged: dict[str, Any] = await price_archive.stage(_file_chunks(made), None)
        job: dict[str, Any] = await price_archive.start_job(staged["token"])
        await _finished(f"price-archive-{job['id']}")
        return await price_archive.latest_job()

    try:
        job: Any = await quiet(export_and_import)
    finally:
        made.unlink(missing_ok=True)
    assert job["status"] == "done", job
AI_RECIPES: int = (
    1_000  # recepten in de receptindex (`offer_recipes`), elk met tien regels
)
_RECIPE_NUMBER: re.Pattern[str] = re.compile(r"^RECIPE (\d+):", re.MULTILINE)


def _recipe_rows(path: Path) -> list[tuple[str, str, str, str]]:
    """`AI_RECIPES` recepten voor de receptindex, met regels uit de namen van de aanbiedingen van AH: (bron, id, titel, regels)."""
    with contextlib.closing(sqlite3.connect(path)) as conn:
        titles: list[str] = [
            row[0]
            for row in conn.execute(
                "SELECT title FROM offers WHERE store = 'ah' ORDER BY position"
            )
        ]
    return [
        ("mealie", f"recept-{number:04d}", f"Recept {number}",
         json.dumps([f"{100 + line} g {titles[(number * 10 + line) % len(titles)].lower()}" for line in range(10)]))
        for number in range(AI_RECIPES)
    ]  # fmt: skip


async def _ai_answer(
    provider: str, key: str, model: str, system: str, payload: str, **options: Any
) -> tuple[Any, int]:
    """`ai_review._call` zonder AI: elk recept van de groep krijgt twee regels aan een aanbieding gekoppeld."""
    del provider, key, model, system, options
    numbers: list[int] = [int(found) for found in _RECIPE_NUMBER.findall(payload)]
    answer: list[dict[str, Any]] = [
        {
            "recipe": number,
            "matches": [
                {"line": 1, "product": number},
                {"line": 2, "product": number + 40},
            ],
        }
        for number in numbers
    ]
    return {"recipes": answer}, 1000 * len(numbers)


@pytest.mark.volume
async def test_an_ai_match_round_does_not_stall(
    volume_client: TestClientProtocol, volume_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # De ronde van de AI langs de recepten (`ai-match`): de aanbiedingen van deze week, de recepten die nog moeten, en per groep
    # van veertig recepten een antwoord om te bewaren. Elke poging doet de volgende `MAX_RECIPES` recepten.
    path: Path = volume_dir / volume.DB_NAME
    async with offers._connect():
        pass  # de tabellen van de receptindex
    with contextlib.closing(sqlite3.connect(path)) as conn:
        conn.executemany(
            "INSERT INTO offer_recipes (source, recipe_id, title, ingredients) VALUES (?, ?, ?, ?)",
            _recipe_rows(path),
        )
        conn.commit()
    monkeypatch.setattr(
        ai_review, "active_provider", lambda: helpers.returned("openai")
    )
    monkeypatch.setattr(
        ai_review, "api_key", lambda provider: helpers.returned(("sleutel", "env"))
    )
    monkeypatch.setattr(
        ai_review, "model_of", lambda provider: helpers.returned("model")
    )
    monkeypatch.setattr(ai_review, "_call", _ai_answer)

    async def ai_round() -> Any:
        await ai_match.start("ah", ai_match.MAX_RECIPES)
        await _finished("ai-match")
        return ai_match.progress()

    result: Any = await quiet(ai_round)
    assert result["state"] == "done" and result["result"]["matched"], result
