"""De Picnic-verkenner als async functies: `offer_probes` en `explore` wachten op elke probe en sturen zelf niets via httpx.

De vorm van het rapport per route staat in tests/test_picnic.py en tests/test_offers_stores.py. Hier staat wat de omzetting
raakt: de probes lopen via `ah_explore._probe` en wachten op de async `picnic`, en een fout van Picnic komt alleen als
statuscode en foutcode in het rapport.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest import mock

import httpx
import pytest
from helpers import run, served

from mealieshopper import auth, picnic, picnic_explore


@pytest.fixture
def linked(data_dir: Path) -> None:
    run(auth.init_db())
    run(picnic.save_auth_key("key"))


def answers(asked: list[str]) -> Any:
    """Een nagebootste `picnic._authed`: de tabbalk, een lege pagina voor de rest, en `/lists` bestaat niet."""

    def authed(method: str, path: str, body: Any = None, text: bool = False) -> Any:
        asked.append(path)
        if path == "/bootstrap":
            return {
                "tabs": [
                    {
                        "id": "home",
                        "type": "PAGE",
                        "title": "Start",
                        "page": "home_page_root",
                    }
                ]
            }
        if path == "/lists":
            raise RuntimeError(
                '404: {"error": {"code": "NOT_FOUND", "message": "geheim antwoord"}}'
            )
        if path == "/deliveries/summary":
            return []
        return {"body": {}}

    return authed


async def test_offer_probes_wait_for_each_page(linked: None) -> None:
    asked: list[str] = []
    with (
        served(httpx.Response(500)) as route,
        mock.patch.object(picnic, "_authed", side_effect=answers(asked)),
    ):
        probes: list[dict[str, Any]] = await picnic_explore.offer_probes()
    assert route.call_count == 0, "alles loopt via de nagebootste picnic._authed"
    assert (
        probes[0]["name"] == "aanbiedingen: welke pagina's zoeken we (pagina, streng)"
    )
    assert all(probe["ok"] for probe in probes), probes
    assert "coroutine" not in json.dumps(probes)


async def test_offer_probes_without_a_tab_bar_report_one_error(linked: None) -> None:
    with mock.patch.object(
        picnic, "offer_references", side_effect=RuntimeError("geen tabbalk")
    ):
        probes: list[dict[str, Any]] = await picnic_explore.offer_probes()
    assert probes == [
        {
            "name": "aanbiedingen: welke pagina's zoeken we",
            "ok": False,
            "error": "geen tabbalk",
        }
    ]


async def test_explore_reports_errors_as_codes_only_and_counts_what_worked(
    linked: None,
) -> None:
    asked: list[str] = []
    with (
        served(httpx.Response(500)) as route,
        mock.patch.object(picnic, "_authed", side_effect=answers(asked)),
    ):
        report: dict[str, Any] = await picnic_explore.explore()
    assert route.call_count == 0
    assert report["linked"] is True
    lists: dict[str, Any] = next(
        probe for probe in report["probes"] if probe["name"] == "lijstjes: GET lists"
    )
    assert lists == {
        "name": "lijstjes: GET lists",
        "ok": False,
        "error": "404 NOT_FOUND",
    }
    assert report["ok"] == sum(1 for probe in report["probes"] if probe["ok"])
    dumped: str = json.dumps(report)
    assert "geheim antwoord" not in dumped
    assert "coroutine" not in dumped, "elke probe heeft op zijn uitkomst gewacht"


async def test_explore_names_a_network_error_by_its_type(linked: None) -> None:
    with served(side_effect=httpx.ConnectError("geheim adres")):
        report: dict[str, Any] = await picnic_explore.explore()
    search: dict[str, Any] = next(
        probe
        for probe in report["probes"]
        if probe["name"] == "zoeken: GET pages/search-page-results"
    )
    assert search["error"] == "netwerkfout: ConnectError"
    assert "geheim adres" not in json.dumps(report)
