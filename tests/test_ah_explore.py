"""De AH-verkenner: probes die synchroon of async zijn, netwerkfouten van httpx, en geen eigen verzoeken.

De vorm van het rapport (welke routes, welke velden) staat in tests/test_ah_orders.py en tests/test_deals.py. Hier
staat wat de omzetting naar async raakt: een probe wacht op een coroutine, een netwerkfout heet naar haar type, en de
verkenner zelf stuurt geen verzoek via httpx (alles loopt via `ah`).
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest import mock

import httpx
import pytest
from support import served

from mealieshopper import ah, ah_explore


async def later(value: Any) -> Any:
    """`value` na een echte wisseling van de event loop, zoals een async winkelclient."""
    await asyncio.sleep(0)
    return value


async def test_resolved_gives_a_value_back_and_waits_for_an_awaitable() -> None:
    assert await ah_explore.resolved({"a": 1}) == {"a": 1}
    assert await ah_explore.resolved(later([1, 2])) == [1, 2]
    assert await ah_explore.resolved(None) is None


async def test_a_probe_waits_for_an_async_call_and_shows_the_shape_of_its_result() -> (
    None
):
    report, result = await ah_explore._probe(
        "async", lambda: later({"orderId": 42, "state": "OPEN"})
    )
    assert result == {"orderId": 42, "state": "OPEN"}
    assert report == {
        "name": "async",
        "ok": True,
        "shape": {"orderId": "int", "state": "str: OPEN"},
    }


async def test_a_probe_with_a_summary_summarises_the_awaited_result() -> None:
    report, _ = await ah_explore._probe("samenvatting", lambda: later([1, 2, 3]), len)
    assert report == {"name": "samenvatting", "ok": True, "info": 3}


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError("geheim adres"),
        httpx.ReadTimeout("geheim adres"),
        httpx.RemoteProtocolError("geheim adres"),
    ],
    ids=["httpx-verbinding", "httpx-timeout", "httpx-opgehangen"],
)
async def test_a_network_error_is_named_by_its_type_without_the_message(
    error: Exception,
) -> None:
    async def failing() -> None:
        raise error

    for call in (mock.Mock(side_effect=error), failing):
        report, result = await ah_explore._probe("netwerk", call)
        assert result is None
        assert report == {
            "name": "netwerk",
            "ok": False,
            "error": f"netwerkfout: {type(error).__name__}",
        }


async def test_another_error_keeps_its_message() -> None:
    report, _ = await ah_explore._probe(
        "fout", mock.Mock(side_effect=RuntimeError("404: not found"))
    )
    assert report == {"name": "fout", "ok": False, "error": "404: not found"}


async def test_the_promotion_probe_takes_a_sync_and_an_async_fetch_alike() -> None:
    pages = {
        "kip": [
            {"isBonus": True, "bonusMechanism": "2e halve prijs"},
            {"isBonus": False},
        ],
        "kaas": [{"isBonus": True, "bonusMechanism": "1+1 gratis"}],
    }
    synchronous = await ah_explore.promo_probe(
        ("kip", "kaas"),
        lambda term: pages[term],
        lambda item: bool(item.get("isBonus")),
    )
    asynchronous = await ah_explore.promo_probe(
        ("kip", "kaas"),
        lambda term: later(pages[term]),
        lambda item: bool(item.get("isBonus")),
    )
    assert synchronous == asynchronous
    assert (synchronous["producten"], synchronous["met aanbieding"]) == (3, 2)
    assert synchronous["teksten"]["bonusMechanism"] == ["1+1 gratis", "2e halve prijs"]


async def test_explore_sends_nothing_itself_and_reports_every_route() -> None:
    with (
        served(httpx.Response(500)) as route,
        mock.patch.object(ah_explore, "_raw_get", return_value={}),
        mock.patch.object(ah_explore, "_raw_graphql", return_value={}),
        mock.patch.object(ah_explore, "_anonymous_graphql", return_value={}),
        mock.patch.object(ah, "_request_json", return_value={}),
        mock.patch.object(ah, "get_token", return_value="anoniem"),
        mock.patch.object(ah, "configured_refresh_token", return_value="x"),
    ):
        report = await ah_explore.explore()
    assert route.call_count == 0, (
        "de verkenner praat alleen via ah, en die is hier nagebootst"
    )
    assert report["linked"] is True
    assert report["ok"] == sum(1 for probe in report["probes"] if probe["ok"])
    assert all("coroutine" not in repr(probe) for probe in report["probes"]), (
        "elke probe heeft op zijn uitkomst gewacht"
    )
