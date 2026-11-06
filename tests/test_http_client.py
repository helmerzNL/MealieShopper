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


async def test_trust_env_is_off(monkeypatch: pytest.MonkeyPatch, closing_clients: None) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:3128")
    trusting: httpx.AsyncClient = httpx.AsyncClient(trust_env=True)
    try:
        assert trusting._mounts, "met trust_env=True hangt httpx een proxy-transport voor HTTPS_PROXY"
    finally:
        await trusting.aclose()
    made: httpx.AsyncClient = http_client.client("test")
    assert made.trust_env is False
    assert made._mounts == {}, "geen proxy uit de omgeving"


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


PAGE: str = "https://recept.example/soep"
CONSENT: str = "https://recept.example/toestemming"


async def test_a_session_carries_a_cookie_across_a_redirect_and_forgets_it_afterwards(
    respx_mock: respx.MockRouter,
) -> None:
    # Een cookiemuur: de pagina stuurt door naar de toestemming, die zet een cookie en stuurt terug naar de pagina.
    def page(request: httpx.Request) -> httpx.Response:
        if request.headers.get("Cookie") == "consent=yes":
            return httpx.Response(200, text="<html>recept</html>")
        return httpx.Response(302, headers={"Location": CONSENT})

    pages: respx.Route = respx_mock.get(PAGE).mock(side_effect=page)
    respx_mock.get(CONSENT).respond(302, headers={"Location": PAGE, "Set-Cookie": "consent=yes; Path=/"})
    async with http_client.session("test", headers={"User-Agent": "Browser/1.0"}) as first:
        answer: httpx.Response = await first.get(PAGE)
        assert first.trust_env is False
    assert answer.text == "<html>recept</html>"
    assert pages.calls.last.request.headers["Cookie"] == "consent=yes"
    assert pages.calls.last.request.headers["User-Agent"] == "Browser/1.0"
    assert first.is_closed, "de sessie sluit na het blok"
    async with http_client.session("test") as second:
        await second.get(CONSENT, follow_redirects=False)
        assert "Cookie" not in respx_mock.calls.last.request.headers, "een nieuwe sessie kent de cookie van de vorige niet"
        assert respx_mock.calls.last.request.headers["User-Agent"] == http_client.DEFAULT_USER_AGENT


async def test_a_session_cookie_never_reaches_the_shared_client(
    respx_mock: respx.MockRouter, closing_clients: None
) -> None:
    respx_mock.get(CONSENT).respond(200, headers={"Set-Cookie": "consent=yes; Path=/"})
    shared: respx.Route = respx_mock.get(PAGE).respond(200)
    async with http_client.session("test") as page:
        await page.get(CONSENT)
        await http_client.client("test").get(PAGE)
    assert "Cookie" not in shared.calls.last.request.headers
