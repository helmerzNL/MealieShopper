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
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar
from unittest import mock

import helpers
import loop_watch
import pytest
import volume
from quart import Quart, Response

from mealieshopper import app as app_module
from mealieshopper import loop_guard, offers, prices
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
            "UPDATE catalogue_changes SET version = version + 1 WHERE id = 1",
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
    "ah": stalls_until("taak 7"),  # het oordeel staat niet meer stil (taak 8), het bouwen van de catalogus nog wel
    "jumbo": stalls_until("taak 7"),
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
    with mock.patch.object(app_module, "json_bytes", wraps=app_module.json_bytes) as spy:
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
        assert hashlib.sha256(await response.get_data()).hexdigest() == SMALL_BODY_SHA256


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
@stalls_until("taak 7")  # koud: de catalogus (de lijnacties van de pagina staan niet meer stil, taak 8)
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
@stalls_until("taak 7")  # de catalogi (het oordeel van warm_offers staat niet meer stil, taak 8)
async def test_startup_warming_does_not_stall(volume_dir: Path) -> None:
    app: Quart = create_app()
    async with app.test_app():

        async def warm_at_start() -> None:
            """Wat `before_serving` buiten de tests start (`warm_at_start` in app.py)."""
            await prices.warm_catalogues()
            await app.extensions["mealieshopper_warm_offers"]()

        await quiet(warm_at_start, prepare=cold(volume_dir))
