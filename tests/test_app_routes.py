"""Tests voor het eigen werk van app.py: de AH-loginproxy op httpx."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest
import respx
from quart import Quart

from mealieshopper import ah, http_client

if TYPE_CHECKING:  # niet bij de import: pytest zou TestClientProtocol voor een testklasse aanzien
    from quart.typing import TestClientProtocol

PUBLIC: str = "https://shopper.example"
PROXY: str = f"{PUBLIC}/api/ah/auth/proxy"
STEP: str = f"{ah.AH_LOGIN_BASE}/login/step"
PAGE: bytes = b'<a href="/x">https://login.ah.nl/y</a>'
COOKIES: tuple[str, ...] = (
    "sid=1; Domain=login.ah.nl; Path=/; Secure; HttpOnly",
    "csrf=2; Path=/; SameSite=None",
)


@pytest.fixture(autouse=True)
def open_proxy(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Zonder inlog en met een vast publiek adres, zodat de herschreven adressen vastliggen."""
    monkeypatch.setenv("PASSKEY_AUTH_ENABLED", "false")
    monkeypatch.setenv("MEALIESHOPPER_PUBLIC_BASE_URL", PUBLIC)


def login_page() -> httpx.Response:
    """Een doorverwijzing van AH met twee cookies, een eigen header (twee keer) en de headers die de proxy tegenhoudt."""
    return httpx.Response(
        302,
        headers=[
            ("Content-Type", "text/html; charset=utf-8"),
            ("Location", f"{ah.AH_LOGIN_BASE}/next?a=1"),
            *(("Set-Cookie", cookie) for cookie in COOKIES),
            ("X-Spam", "eggs"),
            ("X-Dup", "a"),
            ("X-Dup", "b"),
            ("Content-Security-Policy", "default-src 'self'"),
            ("Strict-Transport-Security", "max-age=1"),
            ("X-Frame-Options", "DENY"),
        ],
        content=PAGE,
    )


async def test_the_proxy_forwards_status_body_and_headers(client: TestClientProtocol, respx_mock: respx.MockRouter) -> None:
    route: respx.Route = respx_mock.get(STEP).mock(return_value=login_page())
    reply = await client.get(
        "/api/ah/auth/proxy/login/step",
        query_string={"client_id": "appie"},
        headers={
            "Referer": f"{PROXY}/login",
            "User-Agent": "Browser/1",
            "Accept-Encoding": "br",
        },
    )
    assert reply.status_code == 302
    assert await reply.get_data() == ah.rewrite_login_body(PAGE, PROXY)
    assert reply.headers["Content-Type"] == "text/html; charset=utf-8"
    assert reply.headers["Location"] == f"{PROXY}/next?a=1"
    assert reply.headers.getlist("Set-Cookie") == [ah.sanitize_login_cookie(cookie) for cookie in COOKIES]
    assert reply.headers["X-Spam"] == "eggs"
    assert reply.headers["X-Dup"] == "a, b", "dubbele headers komen samen, zoals bij requests"
    for blocked in (
        "Content-Security-Policy",
        "Strict-Transport-Security",
        "X-Frame-Options",
    ):
        assert blocked not in reply.headers, blocked
    assert respx_mock.calls.call_count == 1, "de proxy volgt een doorverwijzing niet zelf, de browser doet dat"
    sent: httpx.Request = route.calls.last.request
    assert str(sent.url) == f"{STEP}?client_id=appie"
    assert sent.headers["Host"] == "login.ah.nl"
    assert sent.headers["Referer"] == f"{ah.AH_LOGIN_BASE}/login"
    assert sent.headers["User-Agent"] == "Browser/1", "die van de browser, niet de standaard van http_client"
    assert sent.headers["Accept-Encoding"] == "gzip, deflate", "de keuze van de browser gaat niet mee"
    assert sent.headers["Accept"] == "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    assert sent.headers["Accept-Language"] == "nl-NL,nl;q=0.9,en;q=0.8"
    assert "Remote-Addr" not in sent.headers, "Quart zet die er zelf bij, de browser stuurde hem niet"
    assert "Content-Length" not in sent.headers
    assert sent.extensions["timeout"] == httpx.Timeout(30).as_dict()


async def test_the_proxy_forwards_the_body_and_the_origin_of_a_post(client: TestClientProtocol, respx_mock: respx.MockRouter) -> None:
    route: respx.Route = respx_mock.post(STEP).mock(return_value=httpx.Response(200, json={"next": "/login/done"}))
    reply = await client.post(
        "/api/ah/auth/proxy/login/step",
        data=b"user=spam&pw=eggs",
        headers={"Content-Type": "application/x-www-form-urlencoded", "Origin": PUBLIC},
    )
    assert reply.status_code == 200
    assert await reply.get_json() == {"next": f"{PROXY}/login/done"}
    sent: httpx.Request = route.calls.last.request
    assert sent.method == "POST"
    assert sent.content == b"user=spam&pw=eggs"
    assert sent.headers["Content-Type"] == "application/x-www-form-urlencoded"
    assert sent.headers["Content-Length"] == "17"
    assert sent.headers["Origin"] == ah.AH_LOGIN_BASE


async def test_an_empty_delete_says_content_length_zero_like_requests(client: TestClientProtocol, respx_mock: respx.MockRouter) -> None:
    route: respx.Route = respx_mock.delete(STEP).respond(204)
    reply = await client.delete("/api/ah/auth/proxy/login/step")
    assert reply.status_code == 204
    assert route.calls.last.request.headers["Content-Length"] == "0"


async def test_the_cookies_of_ah_reach_the_browser_and_not_the_shared_client(
    app: Quart, client: TestClientProtocol, respx_mock: respx.MockRouter
) -> None:
    route: respx.Route = respx_mock.get(STEP).mock(return_value=login_page())
    reply = await client.get("/api/ah/auth/proxy/login/step")
    assert len(reply.headers.getlist("Set-Cookie")) == 2
    assert not http_client.client("ah-login-proxy").cookies, "de cookie hoort in de browser, niet in de client van de server"
    other_browser: TestClientProtocol = app.test_client()
    await other_browser.get("/api/ah/auth/proxy/login/step")
    assert "Cookie" not in route.calls.last.request.headers, "een andere browser krijgt de cookies van de eerste niet"


async def test_a_network_error_is_a_502_with_the_reason(
    client: TestClientProtocol,
    respx_mock: respx.MockRouter,
    caplog: pytest.LogCaptureFixture,
) -> None:
    respx_mock.get(STEP).mock(side_effect=httpx.ConnectError("geen verbinding"))
    reply = await client.get("/api/ah/auth/proxy/login/step")
    assert reply.status_code == 502
    assert await reply.get_data(as_text=True) == "AH login proxy error: geen verbinding"
    assert "AH login proxy error" in caplog.text


async def test_a_control_character_in_the_path_is_a_502_and_not_a_crash(client: TestClientProtocol, respx_mock: respx.MockRouter) -> None:
    reply = await client.get("/api/ah/auth/proxy/login%7Fstep")
    assert reply.status_code == 502
    assert (await reply.get_data(as_text=True)).startswith("AH login proxy error: ")
    assert respx_mock.calls.call_count == 0


async def test_a_header_with_a_utf8_byte_reaches_ah_byte_for_byte(client: TestClientProtocol, respx_mock: respx.MockRouter) -> None:
    # Quart leest headers als latin-1. requests schreef ze ook zo terug, dus AH kreeg de bytes van de browser ongewijzigd.
    route: respx.Route = respx_mock.get(STEP).respond(200, text="ok")
    reply = await client.get("/api/ah/auth/proxy/login/step", headers={"User-Agent": "Browser/café"})
    assert reply.status_code == 200
    sent: dict[bytes, bytes] = {key.lower(): value for key, value in route.calls.last.request.headers.raw}
    assert sent[b"user-agent"] == "Browser/café".encode("utf-8")


async def test_the_proxy_never_sends_mealieshoppers_own_cookies(client: TestClientProtocol, respx_mock: respx.MockRouter) -> None:
    route: respx.Route = respx_mock.get(STEP).respond(200, text="ok")
    reply = await client.get(
        "/api/ah/auth/proxy/login/step",
        headers={"Cookie": "ms_session=geheim; ms_lang=en; ah_sess=abc; other=1"},
    )
    assert reply.status_code == 200
    assert route.calls.last.request.headers["Cookie"] == "ah_sess=abc; other=1"


async def test_the_proxy_sends_no_cookie_header_when_only_own_cookies_remain(client: TestClientProtocol, respx_mock: respx.MockRouter) -> None:
    route: respx.Route = respx_mock.get(STEP).respond(200, text="ok")
    reply = await client.get("/api/ah/auth/proxy/login/step", headers={"Cookie": "ms_session=x; ms_lang=nl"})
    assert reply.status_code == 200
    assert "Cookie" not in route.calls.last.request.headers


async def test_the_proxy_drops_forwarding_and_authorization_headers(client: TestClientProtocol, respx_mock: respx.MockRouter) -> None:
    route: respx.Route = respx_mock.get(STEP).respond(200, text="ok")
    reply = await client.get(
        "/api/ah/auth/proxy/login/step",
        headers={
            "X-Forwarded-For": "203.0.113.9",
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "shopper.example",
            "X-Forwarded-Port": "443",
            "X-Forwarded-Prefix": "/shopper",
            "Forwarded": "for=203.0.113.9",
            "X-Real-IP": "203.0.113.9",
            "Authorization": "Bearer x",
            "User-Agent": "Browser/1",
            "Accept-Language": "en",
            "Referer": f"{PROXY}/login",
        },
    )
    assert reply.status_code == 200
    sent: httpx.Headers = route.calls.last.request.headers
    for name in (
        "X-Forwarded-For",
        "X-Forwarded-Proto",
        "X-Forwarded-Host",
        "X-Forwarded-Port",
        "X-Forwarded-Prefix",
        "Forwarded",
        "X-Real-IP",
        "Authorization",
    ):
        assert name not in sent, name
    assert sent["User-Agent"] == "Browser/1"
    assert sent["Accept-Language"] == "en"
    assert sent["Referer"] == f"{ah.AH_LOGIN_BASE}/login"


HOP_BY_HOP: tuple[str, ...] = (
    "Connection",
    "Keep-Alive",
    "Proxy-Authenticate",
    "Proxy-Authorization",
    "TE",
    "Trailer",
    "Transfer-Encoding",
    "Upgrade",
    "Date",
    "Server",  # zet hypercorn zelf, zoals Gunicorn in 3.x
)


async def test_the_proxy_does_not_pass_hop_by_hop_headers_to_the_browser(client: TestClientProtocol, respx_mock: respx.MockRouter) -> None:
    """Die gelden voor de verbinding met AH, niet voor die met de browser: hypercorn zet zijn eigen."""
    respx_mock.get(STEP).mock(
        return_value=httpx.Response(
            200,
            headers=[("Content-Type", "text/plain"), ("X-Spam", "eggs"), *((name, "x") for name in HOP_BY_HOP)],
            content=b"ok",
        )
    )
    reply = await client.get("/api/ah/auth/proxy/login/step")
    assert reply.status_code == 200
    assert reply.headers["X-Spam"] == "eggs"
    assert [name for name in HOP_BY_HOP if name in reply.headers] == []


async def test_every_cookie_header_of_the_browser_reaches_ah(client: TestClientProtocol, respx_mock: respx.MockRouter) -> None:
    """Over HTTP/2 stuurt een browser elke cookie in een eigen header: geen daarvan valt weg."""
    route: respx.Route = respx_mock.get(STEP).respond(200, text="ok")
    reply = await client.get("/api/ah/auth/proxy/login/step", headers=[("Cookie", "ah_sess=abc; ms_session=x"), ("Cookie", "other=1")])
    assert reply.status_code == 200
    assert route.calls.last.request.headers["Cookie"] == "ah_sess=abc; other=1"


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        (["ah_sess=abc; ms_session=geheim", "ms_lang=en; other=1"], "ah_sess=abc; other=1"),
        (["ah_sess=abc", "ah_sess=abc"], "ah_sess=abc; ah_sess=abc"),  # dubbel van de browser: AH beslist, wij filteren alleen
        (["ms_session=x; ms_lang=nl"], None),
        (["ms_session=x", "ms_lang=nl"], None),
        ([], None),
        (["", " ; ;"], None),
        ([" spam = eggs ;ms_x=1"], "spam = eggs"),
    ],
)
def test_ah_proxy_cookie_keeps_only_the_cookies_of_ah(headers: list[str], expected: str | None) -> None:
    from mealieshopper.app import _ah_proxy_cookie

    assert _ah_proxy_cookie(headers) == expected


def test_the_app_sets_a_switch_interval_of_one_millisecond(data_dir: Path) -> None:
    """Taak 9c: een loop die de GIL loslaat in een systeemaanroep, krijgt hem pas na het wisselinterval terug als een andere
    thread rekent (het konvooi van loop_guard.py). `create_app` zet het op 1 ms, voor run.py en hypercorn allebei."""
    from mealieshopper.app import SWITCH_INTERVAL_SECONDS, create_app

    before: float = sys.getswitchinterval()
    sys.setswitchinterval(0.005)
    try:
        create_app()
        assert SWITCH_INTERVAL_SECONDS == 0.001
        assert sys.getswitchinterval() == pytest.approx(0.001)
    finally:
        sys.setswitchinterval(before)
