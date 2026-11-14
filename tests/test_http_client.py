"""Tests voor `mealieshopper.http_client`: een httpx-client per naam en per event loop."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path

import certifi
import httpcore
import httpx
import httpx._utils
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


def one_certificate(target: Path) -> Path:
    """Een CA-bestand met precies een echt certificaat (het eerste uit certifi)."""
    text: str = Path(certifi.where()).read_text(encoding="ascii")
    end: str = "-----END CERTIFICATE-----"
    target.write_text(
        text[text.index("-----BEGIN CERTIFICATE-----") : text.index(end) + len(end)]
        + "\n",
        encoding="ascii",
    )
    return target


def ca_count(made: httpx.AsyncClient) -> int:
    """Hoeveel CA-certificaten de TLS-context van de client kent."""
    return len(made._transport._pool._ssl_context.get_ca_certs())  # type: ignore[attr-defined]


async def test_a_proxy_from_the_environment_is_used(
    monkeypatch: pytest.MonkeyPatch, closing_clients: None
) -> None:
    """Zoals requests in 3.x: HTTPS_PROXY (en de andere proxy-variabelen) tellen mee, voor de gedeelde client en een sessie."""
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:3128")
    made: httpx.AsyncClient = http_client.client("test")
    assert made.trust_env is True
    assert isinstance(
        made._transport_for_url(httpx.URL(URL))._pool, httpcore.AsyncHTTPProxy
    )
    async with http_client.session("test") as session:
        assert isinstance(
            session._transport_for_url(httpx.URL(URL))._pool, httpcore.AsyncHTTPProxy
        )


async def test_no_proxy_without_the_environment(
    monkeypatch: pytest.MonkeyPatch, closing_clients: None
) -> None:
    # httpx leest de proxy met urllib.request.getproxies(), en die valt op macOS en Windows zonder variabelen terug op de
    # proxy van het systeem. Zonder omgeving betekent hier: ook geen systeemproxy, dus we vervangen alleen die ene naam in httpx.
    no_proxies: Callable[[], dict[str, str]] = dict
    monkeypatch.setattr(httpx._utils, "getproxies", no_proxies)
    made: httpx.AsyncClient = http_client.client("test")
    assert not isinstance(
        made._transport_for_url(httpx.URL(URL))._pool, httpcore.AsyncHTTPProxy
    )


@pytest.mark.parametrize("variable", ["REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"])
async def test_a_ca_bundle_from_the_environment_is_the_trust_store(
    variable: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    closing_clients: None,
) -> None:
    """Een eigen CA (een Mealie achter een eigen certificaat): REQUESTS_CA_BUNDLE, anders CURL_CA_BUNDLE, zoals requests."""
    monkeypatch.setenv(variable, str(one_certificate(tmp_path / "ca.pem")))
    assert ca_count(http_client.client("test")) == 1
    async with http_client.session("test") as session:
        assert ca_count(session) == 1


async def test_requests_ca_bundle_goes_before_curl_ca_bundle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, closing_clients: None
) -> None:
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(one_certificate(tmp_path / "ca.pem")))
    monkeypatch.setenv("CURL_CA_BUNDLE", certifi.where())
    assert ca_count(http_client.client("test")) == 1


async def test_without_a_bundle_the_default_trust_store(
    monkeypatch: pytest.MonkeyPatch, closing_clients: None
) -> None:
    monkeypatch.setenv("CURL_CA_BUNDLE", "")
    assert ca_count(http_client.client("test")) > 1


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
    respx_mock.get(CONSENT).respond(
        302, headers={"Location": PAGE, "Set-Cookie": "consent=yes; Path=/"}
    )
    async with http_client.session(
        "test", headers={"User-Agent": "Browser/1.0"}
    ) as first:
        answer: httpx.Response = await first.get(PAGE)
        assert first.trust_env is True
    assert answer.text == "<html>recept</html>"
    assert pages.calls.last.request.headers["Cookie"] == "consent=yes"
    assert pages.calls.last.request.headers["User-Agent"] == "Browser/1.0"
    assert first.is_closed, "de sessie sluit na het blok"
    async with http_client.session("test") as second:
        await second.get(CONSENT, follow_redirects=False)
        assert "Cookie" not in respx_mock.calls.last.request.headers, (
            "een nieuwe sessie kent de cookie van de vorige niet"
        )
        assert (
            respx_mock.calls.last.request.headers["User-Agent"]
            == http_client.DEFAULT_USER_AGENT
        )


async def test_a_session_cookie_never_reaches_the_shared_client(
    respx_mock: respx.MockRouter, closing_clients: None
) -> None:
    respx_mock.get(CONSENT).respond(200, headers={"Set-Cookie": "consent=yes; Path=/"})
    shared: respx.Route = respx_mock.get(PAGE).respond(200)
    async with http_client.session("test") as page:
        await page.get(CONSENT)
        await http_client.client("test").get(PAGE)
    assert "Cookie" not in shared.calls.last.request.headers


def test_httpcore_does_not_search_sys_path_for_sniffio_on_every_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """Taak 9c: zonder sniffio zocht `current_async_library` van httpcore bij elke aanroep sys.path af, met een stat per map op
    de event loop. Na het laden van http_client raakt die vraag de import-machine niet meer."""
    searched: list[str] = []

    class Spy:
        @staticmethod
        def find_spec(name: str, path: object = None, target: object = None) -> None:
            searched.append(name)

    monkeypatch.setattr(sys, "meta_path", [Spy, *sys.meta_path])
    from httpcore._synchronization import current_async_library

    assert [current_async_library() for _ in range(3)] == ["asyncio"] * 3
    assert "sniffio" not in searched
