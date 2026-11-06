"""Tests voor `mealieshopper.http_client`: een httpx-client per naam en per event loop."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator

import httpx
import pytest
import respx

from mealieshopper import http_client

URL: str = "https://winkel.example/api"


@pytest.fixture(autouse=True)
def fresh_clients() -> Iterator[None]:
    """Elke test begint en eindigt zonder bewaarde clients."""
    http_client.reset()
    yield
    http_client.reset()


@pytest.fixture
async def closing_clients() -> AsyncIterator[None]:
    """Sluit na een async test de clients van zijn loop."""
    yield
    await http_client.aclose_all()


async def test_default_user_agent_is_chrome_on_android(
    respx_mock: respx.MockRouter, closing_clients: None
) -> None:
    route: respx.Route = respx_mock.get(URL).respond(200)
    await http_client.client("test").get(URL)
    assert (
        route.calls.last.request.headers["User-Agent"] == http_client.DEFAULT_USER_AGENT
    )
    assert "Chrome/157.0.0.0 Mobile" in http_client.DEFAULT_USER_AGENT


async def test_explicit_headers_win_over_the_default(
    respx_mock: respx.MockRouter, closing_clients: None
) -> None:
    route: respx.Route = respx_mock.get(URL).respond(200)
    await http_client.client(
        "test", headers={"User-Agent": "Appie/9.0", "X-Extra": "ja"}
    ).get(URL)
    sent: httpx.Request = route.calls.last.request
    assert sent.headers["User-Agent"] == "Appie/9.0"
    assert sent.headers["X-Extra"] == "ja"


async def test_settings_are_applied(closing_clients: None) -> None:
    made: httpx.AsyncClient = http_client.client(
        "test", timeout=5.0, follow_redirects=False
    )
    assert made.timeout == httpx.Timeout(5.0)
    assert made.follow_redirects is False
    default: httpx.AsyncClient = http_client.client("standaard")
    assert default.timeout == httpx.Timeout(30.0)
    assert default.follow_redirects is True


async def test_a_name_reused_with_other_settings_is_refused(
    closing_clients: None,
) -> None:
    http_client.client("test", timeout=5.0)
    with pytest.raises(ValueError, match="test"):
        http_client.client("test", timeout=10.0)
    with pytest.raises(ValueError, match="test"):
        http_client.client("test", timeout=5.0, headers={"User-Agent": "Anders/1.0"})


def test_client_is_reused_within_a_loop_and_not_across_loops() -> None:
    async def get(name: str) -> httpx.AsyncClient:
        return http_client.client(name)

    first_loop: asyncio.AbstractEventLoop = asyncio.new_event_loop()
    second_loop: asyncio.AbstractEventLoop = asyncio.new_event_loop()
    try:
        first: httpx.AsyncClient = first_loop.run_until_complete(get("test"))
        assert first_loop.run_until_complete(get("test")) is first
        assert first_loop.run_until_complete(get("ander")) is not first
        second: httpx.AsyncClient = second_loop.run_until_complete(get("test"))
        assert second is not first
        assert second_loop.run_until_complete(get("test")) is second
    finally:
        first_loop.run_until_complete(http_client.aclose_all())
        second_loop.run_until_complete(http_client.aclose_all())
        first_loop.close()
        second_loop.close()
    assert first.is_closed and second.is_closed


def test_client_needs_a_running_loop() -> None:
    with pytest.raises(RuntimeError):
        http_client.client("test")


async def test_cookies_from_a_response_are_not_sent_back(
    respx_mock: respx.MockRouter, closing_clients: None
) -> None:
    respx_mock.get(f"{URL}/inloggen").respond(
        200, headers={"Set-Cookie": "a=b; Path=/"}
    )
    route: respx.Route = respx_mock.get(f"{URL}/mandje").respond(200)
    shared: httpx.AsyncClient = http_client.client("test")
    await shared.get(f"{URL}/inloggen")
    await shared.get(f"{URL}/mandje")
    assert "Cookie" not in route.calls.last.request.headers
    assert len(shared.cookies) == 0


async def test_trust_env_is_off(
    monkeypatch: pytest.MonkeyPatch, respx_mock: respx.MockRouter, closing_clients: None
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:3128")
    route: respx.Route = respx_mock.get(URL).respond(200)
    made: httpx.AsyncClient = http_client.client("test")
    assert made.trust_env is False
    await made.get(URL)
    assert route.called


async def test_closed_client_is_replaced(closing_clients: None) -> None:
    first: httpx.AsyncClient = http_client.client("test")
    await first.aclose()
    assert http_client.client("test") is not first


async def test_aclose_all_closes_and_forgets_the_clients_of_this_loop() -> None:
    first: httpx.AsyncClient = http_client.client("test")
    await http_client.aclose_all()
    assert first.is_closed
    second: httpx.AsyncClient = http_client.client("test")
    assert second is not first
    await http_client.aclose_all()


async def test_reset_forgets_clients(closing_clients: None) -> None:
    first: httpx.AsyncClient = http_client.client("test")
    http_client.reset()
    second: httpx.AsyncClient = http_client.client("test")
    assert second is not first
    assert not first.is_closed, "reset vergeet alleen, sluiten doet aclose_all"
    await first.aclose()
