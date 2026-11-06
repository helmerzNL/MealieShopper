"""Synchrone brug naar de testclient van Quart, tot de tests zelf async zijn.

De tests roepen de app nog synchroon aan, zoals met de testclient van Flask. `SyncClient` draait elke aanroep in een
eigen event loop (`asyncio.run`) binnen `app.test_app()` en leest het antwoord helemaal uit voordat die loop sluit: een
antwoord van Quart is na de loop niet meer te lezen. De cookiejar hoort bij de client en blijft dus over aanroepen heen
staan. De moduletaken zetten de tests om naar de fixture `client` (zie conftest.py), daarna verdwijnt deze brug.

Verschillen met Flask die hier worden overbrugd:

- `data=` met een dict is bij Flask een formulier (met bestanden als `(bestand, naam)`), bij Quart heten dat `form=` en
  `files=`. Ruwe bytes of tekst blijven `data=`.
- `content_type=` en `base_url=` kent Quart niet: ze worden de header `Content-Type`, en het schema, de header `Host` en
  het root-pad.
- Quart zet zelf geen `Content-Length` bij een testverzoek, Flask wel. De app leest die (back-up, avatar, MCP), dus
  de brug zet hem bij elk verzoek met een lichaam.
- `set_cookie(sleutel, waarde, domain=...)` heet bij Quart `set_cookie(servernaam, sleutel, waarde)`, en `get_cookie`
  bestaat niet.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from contextlib import AbstractAsyncContextManager
from http.cookiejar import Cookie
from typing import Any, TypeVar
from urllib.parse import urlsplit

from quart import Quart
from quart import Response as QuartResponse
from quart.testing.utils import make_test_body_with_headers, sentinel
from quart.typing import TestClientProtocol, TestHTTPConnectionProtocol
from werkzeug.datastructures import FileStorage, Headers
from werkzeug.wrappers import Response

from mealieshopper import http_client

T = TypeVar("T")


def run(coro: Coroutine[Any, Any, T]) -> T:
    """Draait een coroutine in een eigen event loop, voor `app.test_request_context()` en `app.app_context()`.

    Daarna sluiten de http-clients van die loop (`http_client.aclose_all`), zoals bij elke losse `asyncio.run`."""

    async def closing_clients() -> T:
        try:
            return await coro
        finally:
            await http_client.aclose_all()

    return asyncio.run(closing_clients())


async def returned(value: T) -> T:
    """Een vaste waarde als coroutine, voor een patch op een async callee met een `lambda` (zoals de collectors van
    `offers.COLLECTORS`): `lambda: returned(lijst)` geeft bij elke aanroep een nieuwe coroutine."""
    return value


class SyncResponse(Response):
    """Een volledig gelezen antwoord met de vorm van de Flask-testrespons.

    `status_code`, `headers`, `data`, `get_data(as_text=...)`, `get_json()`, `mimetype`, `is_json` en `location` komen
    van werkzeug, net als bij Flask. De headers blijven zoals de app ze stuurde: geen standaard-Content-Type, geen
    herberekende Content-Length.
    """

    default_mimetype: str | None = None
    automatically_set_content_length: bool = False

    def __init__(self, body: bytes, status: int, headers: Headers) -> None:
        super().__init__(body, status=status, headers=Headers(headers))

    @property
    def text(self) -> str:
        return self.get_data(as_text=True)


def _form_and_files(
    data: Mapping[str, Any],
) -> tuple[dict[str, str], dict[str, FileStorage]]:
    """Splitst een Flask-`data=`-dict in velden en bestanden (`(bestand, naam)` of `(bestand, naam, type)`)."""
    form: dict[str, str] = {}
    files: dict[str, FileStorage] = {}
    for key, value in data.items():
        if isinstance(value, FileStorage):
            files[key] = value
        elif isinstance(value, tuple):
            stream, filename, *rest = value
            files[key] = FileStorage(
                stream=stream,
                filename=filename,
                name=key,
                content_type=rest[0] if rest else None,
            )
        else:
            form[key] = str(value)
    return form, files


def _body(data: Any, json: Any) -> tuple[bytes, Headers]:
    """Het lichaam en zijn headers, met de betekenis die `data=` en `json=` bij Flask hebben."""
    if isinstance(data, Mapping):
        form, files = _form_and_files(data)
        return make_test_body_with_headers(form=form or None, files=files or None)
    return make_test_body_with_headers(data=data, json=json)


class SyncClient:
    """Een testclient met de aanroepen van de Flask-testclient, op een Quart-app."""

    def __init__(self, app: Quart) -> None:
        self.app: Quart = app
        self._client: TestClientProtocol = app.test_client()

    def get(self, path: str, **kwargs: Any) -> SyncResponse:
        return self.open(path, method="GET", **kwargs)

    def post(self, path: str, **kwargs: Any) -> SyncResponse:
        return self.open(path, method="POST", **kwargs)

    def put(self, path: str, **kwargs: Any) -> SyncResponse:
        return self.open(path, method="PUT", **kwargs)

    def delete(self, path: str, **kwargs: Any) -> SyncResponse:
        return self.open(path, method="DELETE", **kwargs)

    def patch(self, path: str, **kwargs: Any) -> SyncResponse:
        return self.open(path, method="PATCH", **kwargs)

    def options(self, path: str, **kwargs: Any) -> SyncResponse:
        return self.open(path, method="OPTIONS", **kwargs)

    def head(self, path: str, **kwargs: Any) -> SyncResponse:
        return self.open(path, method="HEAD", **kwargs)

    def open(
        self,
        path: str,
        method: str = "GET",
        *,
        headers: Mapping[str, str] | list[tuple[str, str]] | Headers | None = None,
        data: Any = None,
        json: Any = sentinel,
        content_type: str | None = None,
        query_string: Mapping[str, Any] | str | None = None,
        base_url: str | None = None,
        follow_redirects: bool = False,
    ) -> SyncResponse:
        request_headers: Headers = (
            Headers(headers) if headers is not None else Headers()
        )
        if content_type is not None:
            request_headers["Content-Type"] = content_type
        body, body_headers = _body(data, json)
        for key, value in body_headers.items():
            request_headers.setdefault(key, value)
        if body:
            request_headers.setdefault("Content-Length", str(len(body)))
        scheme: str = "http"
        root_path: str = ""
        if base_url is not None:
            parts = urlsplit(base_url)
            scheme = parts.scheme or "http"
            request_headers.setdefault("Host", parts.netloc)
            root_path = parts.path.rstrip("/")
        if isinstance(query_string, str):
            path, query_string = f"{path}?{query_string}", None
        return run(
            self._open(
                path,
                method,
                request_headers,
                body,
                query_string,
                scheme,
                root_path,
                follow_redirects,
            )
        )

    async def _open(
        self,
        path: str,
        method: str,
        headers: Headers,
        body: bytes,
        query_string: Mapping[str, Any] | None,
        scheme: str,
        root_path: str,
        follow_redirects: bool,
    ) -> SyncResponse:
        async with self.app.test_app():
            response: QuartResponse = await self._client.open(
                path,
                method=method,
                headers=headers,
                data=body,
                query_string=dict(query_string) if query_string is not None else None,
                scheme=scheme,
                root_path=root_path,
                follow_redirects=follow_redirects,
            )
            content: bytes = await response.get_data(as_text=False)
        return SyncResponse(content, response.status_code, response.headers)

    def set_cookie(
        self,
        key: str,
        value: str = "",
        *,
        domain: str = "localhost",
        path: str = "/",
        **kwargs: Any,
    ) -> None:
        """Zoals bij Flask: `domain` is de host waarvoor de cookie geldt."""
        self._client.set_cookie(domain, key, value, path=path, **kwargs)

    def get_cookie(
        self, key: str, domain: str = "localhost", path: str = "/"
    ) -> Cookie | None:
        """De cookie uit de jar, of None. De jar bewaart een host zonder punt als `<host>.local`."""
        hosts: set[str] = {domain, f"{domain}.local", f".{domain}"}
        for cookie in self._client.cookie_jar or []:
            if cookie.name == key and cookie.path == path and cookie.domain in hosts:
                return cookie
        return None


async def start_app(app: Quart) -> None:
    """Start en stop de app zoals de server doet: `before_serving` en `after_serving` lopen een keer."""
    async with app.test_app():
        pass


class LiveClient:
    """Een testclient binnen de lopende `test_app()` van de test, die elk antwoord meteen helemaal leest.

    Achtergrondwerk (terugzetten, bulkimport, AI-match) loopt als task in de app. De `SyncClient` sluit de app na elke
    aanroep en annuleert dan die tasks, dus deze tests houden een app open voor de hele test (zoals de fixture `client`).
    """

    def __init__(self, client: TestClientProtocol) -> None:
        self._client: TestClientProtocol = client

    async def open(self, path: str, method: str, **kwargs: Any) -> SyncResponse:
        response = await self._client.open(path, method=method, **kwargs)
        content: bytes = await response.get_data(as_text=False)
        return SyncResponse(content, response.status_code, response.headers)

    async def get(self, path: str, **kwargs: Any) -> SyncResponse:
        return await self.open(path, "GET", **kwargs)

    async def post(self, path: str, **kwargs: Any) -> SyncResponse:
        return await self.open(path, "POST", **kwargs)

    async def delete(self, path: str, **kwargs: Any) -> SyncResponse:
        return await self.open(path, "DELETE", **kwargs)

    def set_cookie(self, key: str, value: str) -> None:
        self._client.set_cookie("localhost", key, value)

    def stream(self, path: str, headers: dict[str, str], method: str = "POST") -> TestHTTPConnectionProtocol:
        """Een verzoek waarvan de test het lichaam zelf in stukken stuurt (zonder Content-Length, zoals chunked)."""
        return self._client.request(path, method=method, headers=headers)


def in_db(aconnect: Callable[[], AbstractAsyncContextManager[Any]], *steps: Callable[[Any], Awaitable[Any]]) -> list[Any]:
    """Voert `steps` na elkaar uit op een verbinding van `aconnect` (zoals `ah_orders._aconnect`), commit, en geeft hun uitkomsten.

    Voor testgegevens via een async schrijfhelper die een verbinding vraagt (`ah_orders._save_order`), en voor een async leeshelper
    met een verbinding (`ah_orders.receipt_linking_todo`).
    """

    async def go() -> list[Any]:
        async with aconnect() as conn:
            results: list[Any] = [await step(conn) for step in steps]
            await conn.commit()
            return results

    return run(go())
