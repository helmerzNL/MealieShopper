"""De Jumbo-verkenner: de bereikbaarheidstest op httpx (client `jumbo-explore`) en probes die op async recepten wachten.

De vorm van het rapport per route staat in tests/test_jumbo.py, tests/test_jumbo_my_lists.py, tests/test_jumbo_orders.py en
tests/test_jumbo_recipes.py. Hier staat wat de omzetting raakt: `_reach` stuurt dezelfde headers, time-out en
doorverwijzingen als met requests, en een probe op een async functie (`jumbo_recipes`) wacht op haar uitkomst.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest import mock

import httpx
import pytest
import respx
from helpers import routes, run, sent_json, sent_timeout, served

from mealieshopper import auth, jumbo, jumbo_explore

OPENID: str = f"{jumbo.AUTH_BASE}/.well-known/openid-configuration"


@pytest.fixture
def linked(data_dir: Path) -> Iterator[None]:
    """Een Jumbo-koppeling met een geldig toegangstoken, zodat niets een token hoeft te vernieuwen."""
    run(auth.init_db())
    run(auth.set_secret(jumbo.REFRESH_SECRET, "ref"))
    jumbo._access_cache.update(token="acc", expires_at=jumbo.time.time() + 3600)
    jumbo._persisted_route.clear()
    yield
    jumbo._access_cache.update(token="", expires_at=0.0)


async def test_reach_sends_the_website_user_agent_json_and_twenty_seconds() -> None:
    with routes() as router:
        route: respx.Route = router.get(OPENID).mock(return_value=httpx.Response(200, json={"issuer": "x"}))
        info: dict[str, Any] = await jumbo_explore._reach("GET", OPENID)
    assert info == {
        "status": 200,
        "contentType": "application/json",
        "looksLikeBotBlock": False,
    }
    sent: httpx.Request = route.calls.last.request
    assert sent.headers["User-Agent"] == jumbo.WEB_USER_AGENT
    assert sent.headers["Accept"] == "application/json"
    assert sent_timeout(route.calls.last) == 20


async def test_reach_posts_the_json_body() -> None:
    body: dict[str, Any] = {
        "operationName": "Reach",
        "query": "query Reach { __typename }",
    }
    with routes() as router:
        route: respx.Route = router.post(jumbo.GRAPHQL_URL).mock(return_value=httpx.Response(200, json={"data": {}}))
        await jumbo_explore._reach("POST", jumbo.GRAPHQL_URL, json=body)
    assert sent_json(route.calls.last) == body


async def test_reach_follows_a_redirect_like_requests_did() -> None:
    elsewhere: str = "https://auth.jumbo.com/elders"
    with routes() as router:
        router.get(OPENID).mock(return_value=httpx.Response(302, headers={"Location": elsewhere}))
        final: respx.Route = router.get(elsewhere).mock(return_value=httpx.Response(200, json={}))
        info: dict[str, Any] = await jumbo_explore._reach("GET", OPENID)
    assert final.called
    assert info["status"] == 200


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (403, "application/json"),
        (429, "application/json"),
        (503, "application/json"),
        (200, "text/html"),
    ],
)
async def test_reach_calls_a_refusal_or_an_html_page_a_bot_block(status: int, kind: str) -> None:
    with served(httpx.Response(status, headers={"content-type": f"{kind}; charset=utf-8"})):
        info: dict[str, Any] = await jumbo_explore._reach("GET", OPENID)
    assert info == {"status": status, "contentType": kind, "looksLikeBotBlock": True}


async def test_an_unreachable_jumbo_is_reported_by_the_type_of_the_error_only() -> None:
    with served(side_effect=httpx.ConnectError("geheim")):
        report, result = await jumbo_explore._probe("bereikbaar", lambda: jumbo_explore._reach("GET", OPENID), lambda data: data)
    assert result is None
    assert report == {"name": "bereikbaar", "ok": False, "error": "ConnectError"}


async def test_explore_waits_for_the_reachability_and_the_recipe_probes(
    linked: None,
) -> None:
    def answer(operation: str, variables: dict[str, Any]) -> dict[str, Any]:
        if operation == "getRecipesListers":
            return {"searchRecipes": {"count": 3, "items": []}}
        return {}

    with (
        served(httpx.Response(403, headers={"content-type": "text/html"})) as reach,
        mock.patch.object(jumbo, "get_access_token", return_value="acc"),
        mock.patch.object(jumbo, "search_products", return_value=[]),
        mock.patch.object(jumbo, "get_cart", return_value={"items": []}),
        mock.patch.object(jumbo, "get_shopping_list", return_value={"active": True, "items": []}),
        mock.patch.object(jumbo, "get_product_lists", return_value=[]),
        mock.patch.object(jumbo, "get_orders", return_value={"orders": [], "storeReceipts": 0}),
        mock.patch.object(jumbo, "_graphql", return_value={}),
        mock.patch.object(jumbo, "_graphql_variants", return_value={}),
        mock.patch.object(jumbo, "_persisted_query", side_effect=answer),
    ):
        report: dict[str, Any] = await jumbo_explore.explore()
    assert reach.call_count == 2, "de OpenID-configuratie en GraphQL zonder inloggen"
    by_name: dict[str, dict[str, Any]] = {probe["name"]: probe for probe in report["probes"]}
    assert by_name["bereikbaar: auth.jumbo.com (OpenID-configuratie)"]["info"]["looksLikeBotBlock"] is True
    recipes: dict[str, Any] = by_name["recepten zoeken: opgeslagen query getRecipesListers"]
    assert recipes["ok"] is True
    assert recipes["info"]["recepten in totaal"] == 3
    assert "coroutine" not in json.dumps(report), "elke probe heeft op zijn uitkomst gewacht"
