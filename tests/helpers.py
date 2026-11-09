"""Hulpjes voor de tests: een async testclient op een lopende app, en kleine respx- en databasehulpjes.

`AppClient` praat met een app die draait zoals onder de server: `before_serving` loopt een keer, bij de eerste
aanroep, en `after_serving` aan het eind van de test (`AppTestCase`). Achtergrondwerk
van een route (terugzetten, bulkimport, AI-match) loopt dus door tot de test klaar is. Elke aanroep leest het antwoord
helemaal uit, zodat de test `status_code`, `get_json()` en `text` gewoon kan lezen.

De aanroepen nemen de argumenten van de testclient van Quart, met een paar gemakken die de tests al jaren gebruiken:

- `data=` met een dict is een formulier (met bestanden als `(bestand, naam)`), ruwe bytes of tekst blijven `data=`.
- `content_type=` wordt de header `Content-Type`, `base_url=` het schema, de header `Host` en het root-pad.
- Quart zet zelf geen `Content-Length` bij een testverzoek: de app leest die (back-up, avatar, MCP), dus de client zet hem
  bij elk verzoek met een lichaam.
- `set_cookie(sleutel, waarde, domain=...)` en `get_cookie(sleutel)` werken op de cookiejar van deze client.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import unittest
from collections.abc import Awaitable, Callable, Coroutine, Iterator, Mapping
from contextlib import AbstractAsyncContextManager, contextmanager
from http.cookiejar import Cookie
from typing import Any, TypeVar
from urllib.parse import urlsplit

import respx
from quart import Quart
from quart import Response as QuartResponse
from quart.testing.utils import make_test_body_with_headers, sentinel
from quart.typing import TestAppProtocol, TestClientProtocol, TestHTTPConnectionProtocol
from werkzeug.datastructures import FileStorage, Headers
from werkzeug.wrappers import Response

from mealieshopper import auth, http_client, tasks

T = TypeVar("T")


async def settle_audit(timeout: float = 5.0) -> None:
    """Wacht tot de auditregels klaar zijn die nog als eigen task worden geschreven.

    Een uitgaand verzoek met een doorverwijzing die httpx niet volgt, logt `audit` in een eigen task (`audit-redirect`): pas
    na de hooks weet httpx of het de doorverwijzing volgt. Die task schrijft via een thread van aiosqlite naar SQLite. Wie
    de regel leest, of de loop sluit, moet daar eerst op wachten: anders is er soms nog geen regel, of schrijft de thread
    nog terwijl de loop dicht is en de tijdelijke data-map wordt opgeruimd ("Event loop is closed", "Directory not empty").
    Alleen de taken van de auditlog in deze loop; ander achtergrondwerk van een test blijft zoals het was.
    """
    loop = asyncio.get_running_loop()
    pending = [task for name, task in tasks.running().items() if name.startswith("audit-") and not task.done() and task.get_loop() is loop]
    if pending:
        await asyncio.wait(pending, timeout=timeout)


def run(coro: Coroutine[Any, Any, T]) -> T:
    """Draait een coroutine in een eigen event loop, voor een synchrone test of `setUp`.

    Daarna worden de auditregels die nog worden geschreven afgewacht (`settle_audit`) en sluiten de http-clients van die
    loop (`http_client.aclose_all`), zoals bij elke losse `asyncio.run`."""

    async def closing_clients() -> T:
        try:
            return await coro
        finally:
            await settle_audit()
            await http_client.aclose_all()

    return asyncio.run(closing_clients())


async def returned(value: T) -> T:
    """Een vaste waarde als coroutine, voor een patch op een async callee met een `lambda` (zoals de collectors van
    `offers.COLLECTORS`): `lambda: returned(lijst)` geeft bij elke aanroep een nieuwe coroutine."""
    return value


def run_aside(coro: Coroutine[Any, Any, T]) -> T:
    """`asyncio.run(coro)`, ook vanuit een test die zelf in een event loop draait: dan in een eigen thread met een eigen loop."""
    if not _loop_running():
        return asyncio.run(coro)
    outcome: list[Any] = []

    def work() -> None:
        try:
            outcome.append((True, asyncio.run(coro)))
        except BaseException as exc:  # noqa: BLE001 - komt hieronder terug bij de test
            outcome.append((False, exc))

    worker: threading.Thread = threading.Thread(target=work)
    worker.start()
    worker.join()
    ok, value = outcome[0]
    if not ok:
        raise value
    return value


def _loop_running() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def db(owner: Any = None) -> sqlite3.Connection:
    """Een synchrone verbinding met de database van de test, om rijen klaar te zetten of na te kijken.

    De app zelf gebruikt alleen aiosqlite (`auth.connect`). Met `owner` (een module zoals `ah_orders`) maakt eerst diens
    `_connect` de tabellen en migraties, zoals de app bij het eerste gebruik doet. Gebruik hem met `contextlib.closing`,
    en commit zelf."""
    if owner is not None:

        async def make_schema() -> None:
            async with owner._connect():
                pass

        run_aside(make_schema())
    auth.data_dir().mkdir(parents=True, exist_ok=True)
    conn: sqlite3.Connection = sqlite3.connect(auth.db_path())
    conn.row_factory = sqlite3.Row
    return conn


class ReadResponse(Response):
    """Een volledig gelezen antwoord van de app.

    `status_code`, `headers`, `data`, `get_data(as_text=...)`, `get_json()`, `mimetype`, `is_json` en `location` komen
    van werkzeug. De headers blijven zoals de app ze stuurde: geen standaard-Content-Type, geen herberekende Content-Length.
    """

    default_mimetype: str | None = None
    automatically_set_content_length: bool = False

    def __init__(self, body: bytes, status: int, headers: Headers) -> None:
        super().__init__(body, status=status, headers=Headers(headers))

    @property
    def text(self) -> str:
        return self.get_data(as_text=True)


def _form_and_files(data: Mapping[str, Any]) -> tuple[dict[str, str], dict[str, FileStorage]]:
    """Splitst een `data=`-dict in velden en bestanden (`(bestand, naam)` of `(bestand, naam, type)`)."""
    form: dict[str, str] = {}
    files: dict[str, FileStorage] = {}
    for key, value in data.items():
        if isinstance(value, FileStorage):
            files[key] = value
        elif isinstance(value, tuple):
            stream, filename, *rest = value
            files[key] = FileStorage(stream=stream, filename=filename, name=key, content_type=rest[0] if rest else None)
        else:
            form[key] = str(value)
    return form, files


def _body(data: Any, json_body: Any) -> tuple[bytes, Headers]:
    """Het lichaam en zijn headers: een dict in `data` is een formulier, anders ruwe data of JSON."""
    if isinstance(data, Mapping):
        form, files = _form_and_files(data)
        return make_test_body_with_headers(form=form or None, files=files or None)
    return make_test_body_with_headers(data=data, json=json_body)


class AppClient:
    """Een async testclient met een eigen cookiejar, op een app die draait.

    `start` (als gegeven) start de app bij de eerste aanroep, als die nog niet draaide (zie `AppTestCase.client_for`).
    """

    def __init__(self, client: TestClientProtocol, start: Callable[[], Awaitable[None]] | None = None) -> None:
        self._client: TestClientProtocol = client
        self._start: Callable[[], Awaitable[None]] | None = start
        self.app: Quart = client.app

    async def get(self, path: str, **kwargs: Any) -> ReadResponse:
        return await self.open(path, "GET", **kwargs)

    async def post(self, path: str, **kwargs: Any) -> ReadResponse:
        return await self.open(path, "POST", **kwargs)

    async def put(self, path: str, **kwargs: Any) -> ReadResponse:
        return await self.open(path, "PUT", **kwargs)

    async def delete(self, path: str, **kwargs: Any) -> ReadResponse:
        return await self.open(path, "DELETE", **kwargs)

    async def patch(self, path: str, **kwargs: Any) -> ReadResponse:
        return await self.open(path, "PATCH", **kwargs)

    async def options(self, path: str, **kwargs: Any) -> ReadResponse:
        return await self.open(path, "OPTIONS", **kwargs)

    async def head(self, path: str, **kwargs: Any) -> ReadResponse:
        return await self.open(path, "HEAD", **kwargs)

    async def open(
        self,
        path: str,
        method: str = "GET",
        *,
        headers: Mapping[str, str] | list[tuple[str, str]] | Headers | None = None,
        data: Any = None,
        json: Any = sentinel,
        form: Mapping[str, str] | None = None,
        files: Mapping[str, FileStorage] | None = None,
        content_type: str | None = None,
        query_string: Mapping[str, Any] | str | None = None,
        base_url: str | None = None,
        follow_redirects: bool = False,
    ) -> ReadResponse:
        if self._start is not None:
            await self._start()
        request_headers: Headers = Headers(headers) if headers is not None else Headers()
        if content_type is not None:
            request_headers["Content-Type"] = content_type
        if form is not None or files is not None:
            body, body_headers = make_test_body_with_headers(form=form, files=files)
        else:
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
        response: QuartResponse = await self._client.open(
            path,
            method=method,
            headers=request_headers,
            data=body,
            query_string=dict(query_string) if query_string is not None else None,
            scheme=scheme,
            root_path=root_path,
            follow_redirects=follow_redirects,
        )
        content: bytes = await response.get_data(as_text=False)
        return ReadResponse(content, response.status_code, response.headers)

    def stream(self, path: str, headers: dict[str, str], method: str = "POST") -> TestHTTPConnectionProtocol:
        """Een verzoek waarvan de test het lichaam zelf in stukken stuurt (zonder Content-Length, zoals chunked)."""
        return self._client.request(path, method=method, headers=headers)

    def set_cookie(self, key: str, value: str = "", *, domain: str = "localhost", path: str = "/", **kwargs: Any) -> None:
        """`domain` is de host waarvoor de cookie geldt."""
        self._client.set_cookie(domain, key, value, path=path, **kwargs)

    def get_cookie(self, key: str, domain: str = "localhost", path: str = "/") -> Cookie | None:
        """De cookie uit de jar, of None. De jar bewaart een host zonder punt als `<host>.local`."""
        hosts: set[str] = {domain, f"{domain}.local", f".{domain}"}
        for cookie in self._client.cookie_jar or []:
            if cookie.name == key and cookie.path == path and cookie.domain in hosts:
                return cookie
        return None


class AppTestCase(unittest.IsolatedAsyncioTestCase):
    """Een testklasse waarvan de tests een app aanspreken met `self.client_for(app)`.

    De app start bij de eerste aanroep van een van zijn clients en stopt na de test (`after_serving`: tasks geannuleerd,
    http-clients dicht), voor de opruimers die de test zelf vroeger registreerde. Elke client heeft een eigen cookiejar,
    dus twee clients op een app zijn twee browsers.
    """

    def client_for(self, app: Quart) -> AppClient:
        return AppClient(app.test_client(), lambda: self._serve(app))

    async def _serve(self, app: Quart) -> None:
        """Start `app` een keer. Twee gelijktijdige eerste aanroepen wachten allebei op dezelfde start, zodat geen verzoek
        loopt voordat `before_serving` klaar is."""
        starting: dict[int, asyncio.Task[None]] = self.__dict__.setdefault("_starting_apps", {})
        if id(app) not in starting:
            test_app: TestAppProtocol = app.test_app()
            starting[id(app)] = asyncio.create_task(test_app.startup())
            self.addAsyncCleanup(test_app.shutdown)
        await asyncio.shield(starting[id(app)])


async def start_app(app: Quart) -> None:
    """Start en stop de app zoals de server doet: `before_serving` en `after_serving` lopen een keer."""
    async with app.test_app():
        pass


async def in_db(connect: Callable[[], AbstractAsyncContextManager[Any]], *steps: Callable[[Any], Awaitable[Any]]) -> list[Any]:
    """Voert `steps` na elkaar uit op een verbinding van `connect` (zoals `ah_orders._connect`), commit, en geeft hun uitkomsten.

    Voor testgegevens via een async schrijfhelper die een verbinding vraagt (`ah_orders._save_order`), en voor een async leeshelper
    met een verbinding (`ah_orders.receipt_linking_todo`). Een synchrone test schrijft `run(in_db(...))`.
    """
    async with connect() as conn:
        results: list[Any] = [await step(conn) for step in steps]
        await conn.commit()
        return results


@contextmanager
def served(return_value: Any = None, side_effect: Any = None) -> Iterator[respx.Route]:
    """Een respx-route die elk verzoek opvangt.

    `return_value` is een `httpx.Response` voor elk verzoek, `side_effect` een lijst antwoorden (een per verzoek, in volgorde),
    een uitzondering of een functie. De route geeft `calls` en `call_count` terug, om te controleren wat er is gevraagd."""
    with respx.mock(assert_all_called=False) as router:
        route: respx.Route = router.route()
        if side_effect is not None:
            route.side_effect = side_effect
        else:
            route.mock(return_value=return_value)
        yield route


@contextmanager
def routes() -> Iterator[respx.MockRouter]:
    """Een respx-router voor een test met routes per methode of adres.

    Een verzoek zonder passende route faalt met een `AssertionError` van respx (geen `httpx.HTTPError`), zodat een
    vergeten route nooit stil als "Mealie niet bereikbaar" telt."""
    with respx.mock(assert_all_called=False) as router:
        yield router


def sent_url(call: respx.models.Call) -> str:
    """Het adres van een opgevangen verzoek zonder query."""
    return str(call.request.url.copy_with(query=None))


def sent_params(call: respx.models.Call) -> dict[str, str]:
    """De query van een opgevangen verzoek. httpx stuurt elke waarde als tekst, dus `1` komt terug als `"1"`."""
    return dict(call.request.url.params)


def sent_json(call: respx.models.Call) -> Any:
    """Het JSON-lichaam van een opgevangen verzoek."""
    return json.loads(call.request.content)


def sent_timeout(call: respx.models.Call) -> float | None:
    """De leestime-out van een opgevangen verzoek (httpx zet `timeout=15` op alle vier de time-outs)."""
    timeout: dict[str, float | None] = call.request.extensions["timeout"]
    return timeout["read"]
