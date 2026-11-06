"""De forwarded-middleware (mealieshopper/forwarded.py): het adres van de client en de publieke host achter de reverse proxy."""

import asyncio
import unittest
from typing import Any

from quart import Quart, request

from mealieshopper.forwarded import ForwardedHeaders


def make_app() -> Quart:
    app: Quart = Quart(__name__)

    @app.get("/who")
    async def who() -> dict[str, Any]:
        return {"scheme": request.scheme, "host": request.host, "remote_addr": request.remote_addr}

    app.asgi_app = ForwardedHeaders(app.asgi_app)  # type: ignore[method-assign]
    return app


def ask(headers: dict[str, str]) -> dict[str, Any]:
    async def go() -> dict[str, Any]:
        response = await make_app().test_client().get("/who", headers=headers)
        return await response.get_json()

    return asyncio.run(go())


class ForwardedHeadersTest(unittest.TestCase):
    def test_forwarded_proto_host_and_client_are_applied(self) -> None:
        seen = ask({"X-Forwarded-For": "203.0.113.9", "X-Forwarded-Proto": "https", "X-Forwarded-Host": "shopper.ytm.nl"})
        self.assertEqual(seen, {"scheme": "https", "host": "shopper.ytm.nl", "remote_addr": "203.0.113.9"})

    def test_the_forwarded_port_is_combined_with_the_host(self) -> None:
        seen = ask({"X-Forwarded-Proto": "https", "X-Forwarded-Host": "shopper.ytm.nl:8443", "X-Forwarded-Port": "9443"})
        self.assertEqual(seen["host"], "shopper.ytm.nl:9443")

    def test_without_forwarded_headers_nothing_changes(self) -> None:
        plain = asyncio.run(self.plain())
        self.assertEqual(ask({}), plain)
        self.assertEqual(plain["scheme"], "http")
        self.assertEqual(plain["host"], "localhost")

    async def plain(self) -> dict[str, Any]:
        app: Quart = make_app()
        app.asgi_app = app.asgi_app.app  # type: ignore[attr-defined,method-assign]
        return await (await app.test_client().get("/who")).get_json()

    def test_only_the_last_hop_of_x_forwarded_for_counts(self) -> None:
        seen = ask({"X-Forwarded-For": "6.6.6.6, 198.51.100.7, 203.0.113.9"})
        self.assertEqual(seen["remote_addr"], "203.0.113.9")

    def test_the_scope_of_the_server_is_not_altered(self) -> None:
        scope: dict[str, Any] = {"type": "http", "scheme": "http", "server": ("a", 80), "client": ("1.1.1.1", 5), "headers": [(b"host", b"a"), (b"x-forwarded-proto", b"https")]}
        seen: list[dict[str, Any]] = []

        async def inner(sc: dict[str, Any], receive: Any, send: Any) -> None:
            seen.append(sc)

        asyncio.run(ForwardedHeaders(inner)(scope, None, None))
        self.assertEqual(seen[0]["scheme"], "https")
        self.assertEqual((scope["scheme"], scope["server"], scope["headers"][0]), ("http", ("a", 80), (b"host", b"a")))


if __name__ == "__main__":
    unittest.main()
