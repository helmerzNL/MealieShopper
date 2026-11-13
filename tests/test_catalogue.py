"""De catalogus van de prijzen (`prices._catalogue`) wordt buiten de event loop gebouwd, een keer per versie.

`_catalogue_rows` leest de rijen op de loop (alleen queries), `_build_catalogue` rekent ze in de threadpool van compute.py om,
en `_catalogue` houdt de bouw met een `SingleFlight` op een per sleutel tegelijk. Wat dat moet waarmaken: een fout in de bouw
komt bij de aanvraag terecht en blijft niet bewaard, een aanvraag die wegvalt haalt de bouw voor de anderen niet neer, en een
winkel zonder gegevens geeft een lege catalogus.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable, Hashable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from quart import Quart

from mealieshopper import auth, compute, prices
from mealieshopper.app import create_app

TODAY: str = "2026-11-13"
WAIT_SECONDS: float = 5.0  # zo lang wacht een test hooguit op de bouw in de threadpool


@pytest.fixture(autouse=True)
def empty_catalogues() -> Iterator[None]:
    """Geen catalogus van een vorige test, en de pool na de test weg."""
    prices._CATALOGUES.clear()
    yield
    prices._CATALOGUES.clear()
    compute.shutdown()


@pytest.fixture
async def database(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Een lege database, zonder inloggen, met de triggers van de catalogus al aangemaakt: twee aanvragen tegelijk op een database
    zonder triggers maken ze allebei en lezen dan elk een andere versie."""
    monkeypatch.setenv("PASSKEY_AUTH_ENABLED", "false")
    await auth.init_db()
    await prices.signature("ah")
    return data_dir


async def _catalogue(store: str = "ah") -> prices._Catalogue:
    async with prices._connect() as conn:
        return await prices._catalogue(conn, store, TODAY)


class HeldBuild:
    """Houdt `_build_catalogue` in de threadpool vast tot de test `release` zet, en doet dan `then`. `joined` gaat aan zodra
    `waiting` aanvragen bij `_CATALOGUE_BUILDS` om de bouw vroegen: op een lege database is de bouw anders klaar voordat de
    tweede aanvraag er is, en bouwt die gewoon opnieuw."""

    def __init__(self, waiting: int, then: Callable[..., prices._Catalogue]) -> None:
        self.waiting: int = waiting
        self.then: Callable[..., prices._Catalogue] = then
        self.asked: int = 0
        self.joined: threading.Event = threading.Event()
        self.release: threading.Event = threading.Event()

    def build(self, *args: Any) -> prices._Catalogue:
        self.release.wait(WAIT_SECONDS)
        return self.then(*args)

    @contextmanager
    def installed(self) -> Iterator[mock.MagicMock]:
        get: Callable[..., Awaitable[prices._Catalogue]] = prices._CATALOGUE_BUILDS.get

        async def counted(
            key: Hashable, build: Callable[[], Awaitable[prices._Catalogue]]
        ) -> prices._Catalogue:
            self.asked += 1
            if self.asked >= self.waiting:
                self.joined.set()
            return await get(
                key, build
            )  # zonder await ervoor: de bouw die er al is, krijgt deze aanvraag erbij

        with (
            mock.patch.object(prices._CATALOGUE_BUILDS, "get", counted),
            mock.patch.object(
                prices, "_build_catalogue", side_effect=self.build
            ) as built,
        ):
            yield built


def _fail(*args: Any) -> prices._Catalogue:
    raise RuntimeError("kapot")


async def test_a_store_without_data_gives_an_empty_catalogue(database: Path) -> None:
    app: Quart = create_app()
    async with app.test_app() as test_app:
        response = await test_app.test_client().get("/api/offers?store=crisp")
        assert response.status_code == 200
        assert (await response.get_json())["offers"] == []
    await prices.warm_catalogues(["crisp"], today=TODAY)
    catalogue: prices._Catalogue = await _catalogue("crisp")
    assert (
        catalogue.receipts,
        catalogue.products,
        catalogue.twins,
        len(catalogue.lines),
    ) == ([], {}, {}, 0)


def test_build_catalogue_does_not_touch_the_module_cache() -> None:
    before: dict[Any, Any] = dict(prices._CATALOGUES)
    rows: prices._CatalogueRows = prices._CatalogueRows(
        [], [], [], [], [], [], [], None
    )
    catalogue: prices._Catalogue = prices._build_catalogue("ah", TODAY, rows)
    assert (catalogue.receipts, catalogue.products, catalogue.twins) == ([], {}, {})
    assert prices._CATALOGUES == before


async def test_an_error_in_the_build_reaches_the_request_and_is_not_kept(
    database: Path,
) -> None:
    held: HeldBuild = HeldBuild(2, _fail)
    with held.installed() as failing:
        requests: asyncio.Future[list[Any]] = asyncio.gather(
            _catalogue(), _catalogue(), return_exceptions=True
        )
        assert await asyncio.to_thread(held.joined.wait, WAIT_SECONDS)
        held.release.set()
        found: list[Any] = await requests
    assert [type(error) for error in found] == [RuntimeError, RuntimeError]
    assert failing.call_count == 1, "twee aanvragen tegelijk: een bouw"
    assert prices._CATALOGUES == {}
    with mock.patch.object(
        prices, "_build_catalogue", wraps=prices._build_catalogue
    ) as built:
        await _catalogue()
    assert built.call_count == 1, "de volgende aanvraag bouwt opnieuw"
    assert [key[1] for key in prices._CATALOGUES] == ["ah"]


async def test_a_cancelled_request_does_not_stop_the_build_for_the_others(
    database: Path,
) -> None:
    held: HeldBuild = HeldBuild(2, prices._build_catalogue)
    with held.installed() as built:
        first: asyncio.Task[prices._Catalogue] = asyncio.ensure_future(_catalogue())
        second: asyncio.Task[prices._Catalogue] = asyncio.ensure_future(_catalogue())
        assert await asyncio.to_thread(held.joined.wait, WAIT_SECONDS)
        await asyncio.sleep(0)  # de tweede aanvraag zit nu op de bouw
        first.cancel()
        held.release.set()
        catalogue: prices._Catalogue = await second
    assert first.cancelled()
    assert built.call_count == 1
    assert catalogue.products == {}
    assert [key[1] for key in prices._CATALOGUES] == ["ah"], (
        "de bouw schreef de catalogus, ook zonder de eerste aanvraag"
    )
