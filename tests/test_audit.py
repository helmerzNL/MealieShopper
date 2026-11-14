import asyncio
import contextvars
import json
import os
import sqlite3
import tempfile
import threading
import types
import unittest
from collections.abc import Awaitable, Callable
from contextlib import closing
from pathlib import Path
from typing import Any, TypeAlias
from unittest import mock

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

import httpx  # noqa: E402
import pytest  # noqa: E402
import respx  # noqa: E402

from mealieshopper import audit, auth, http_client, mcp, oauth, tasks, telemetry  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402
from helpers import AppTestCase, db, settle_audit  # noqa: E402


# Wat een respx-route teruggeeft: een vast antwoord of een functie die er een maakt.
Answer: TypeAlias = httpx.Response | Callable[[httpx.Request], Awaitable[httpx.Response]]


class AuditTestCase(AppTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(os.environ, {"MEALIESHOPPER_DATA_DIR": tmp.name, "PASSKEY_AUTH_ENABLED": "false"})
        env.start()
        self.addCleanup(env.stop)
        for name in ("MEALIESHOPPER_AUDIT_LOG", "MEALIESHOPPER_AUDIT_BODIES", "MEALIESHOPPER_AUDIT_BODY_CHARS", "MEALIESHOPPER_AUDIT_RETENTION_DAYS"):
            os.environ.pop(name, None)
        http_client.reset()
        self.addCleanup(http_client.reset)
        self.app = create_app()
        self.client = self.client_for(self.app)

    async def entries(self, **kwargs):
        return (await audit.list_entries(**kwargs))["entries"]

    async def fetch(self, method: str, url: str, routes: dict[str, Answer], **kwargs: Any) -> httpx.Response:
        """Een echt verzoek via `http_client`, met alleen het netwerk nagebootst door respx."""
        follow_redirects: bool = kwargs.pop("follow_redirects", True)
        try:
            with respx.mock(assert_all_called=False) as router:
                for route_url, answer in routes.items():
                    if callable(answer):
                        router.route(url=route_url).mock(side_effect=answer)
                    else:
                        router.route(url=route_url).mock(return_value=answer)
                response: httpx.Response = await http_client.client("test", follow_redirects=follow_redirects).request(method, url, **kwargs)
                # Een niet gevolgde doorverwijzing logt `audit` als eigen task: wacht tot die regel echt geschreven is.
                await settle_audit()
                return response
        finally:
            await http_client.aclose_all()

    async def send(self, method, url, status=200, body=b"{}", content_type="application/json", **kwargs):
        """Een verzoek naar `url` met een vast antwoord (status, lichaam en type)."""
        answer = httpx.Response(status, content=body if isinstance(body, bytes) else body.encode(), headers={"Content-Type": content_type})
        return await self.fetch(method, url, {url: answer}, **kwargs)


class OutboundTest(AuditTestCase):
    def test_every_store_is_recognised_by_its_host(self):
        for url, service in (
            ("https://api.ah.nl/mobile-services/product/search/v2", "ah"),
            ("https://login.ah.nl/x", "ah"),
            ("https://www.ah.nl/allerhande", "ah"),
            ("https://storefront-prod.nl.picnicinternational.com/api/15/cart", "picnic"),
            ("https://www.jumbo.com/api/graphql", "jumbo"),
            ("https://auth.jumbo.com/oauth/token", "jumbo"),
            ("https://marleyspoon.nl/menu", "marleyspoon"),
            ("https://api.marleyspoon.com/public/graphql", "marleyspoon"),
            ("https://www.lidl.nl/c/aanbiedingen/a10008785", "lidl"),
            ("https://imgproxy-retcat.assets.schwarz/x.jpg", "lidl"),
            ("https://www.aldi.nl/aanbiedingen.html", "aldi"),
            ("https://s7g10.scene7.com/is/image/aldinord/x", "aldi"),
        ):
            self.assertEqual(audit.service_for(url), service, url)
        for url in ("https://mealie.example.com/api", "https://evilah.nl/", "https://ah.nl.evil.example/", "https://github.com/x",
                    "https://evillidl.nl/", "https://s7g11.scene7.com/x"):
            self.assertEqual(audit.service_for(url), "", url)

    async def test_a_request_is_logged_with_status_duration_and_body(self):
        await self.send("GET", "https://api.ah.nl/mobile-services/product/search/v2?query=melk", body=json.dumps({"products": [{"title": "Melk"}]}))
        (entry,) = await self.entries()
        self.assertEqual((entry["kind"], entry["service"], entry["method"], entry["status"], entry["ok"]), ("outbound", "ah", "GET", 200, True))
        self.assertEqual(entry["action"], "GET /mobile-services/product/search/v2")
        self.assertIn("query=melk", entry["url"])
        full = await audit.get_entry(entry["id"])
        self.assertIn("Melk", full["response"])
        self.assertEqual(entry["actor"], "system")

    async def test_other_hosts_are_not_logged(self):
        await self.send("GET", "https://mealie.example.com/api/recipes")
        await self.send("GET", "https://github.com/helmerzNL/MealieShopper/releases/latest")
        self.assertEqual(await self.entries(), [])

    async def test_graphql_requests_are_named_after_their_operation(self):
        await self.send("POST", "https://www.jumbo.com/api/graphql", json={"operationName": "FavoriteRecipes", "variables": {}})
        (entry,) = await self.entries()
        self.assertEqual((entry["service"], entry["action"]), ("jumbo", "GraphQL FavoriteRecipes"))

    async def test_http_errors_are_logged_as_failures_and_network_failures_not_at_all(self):
        # Een netwerkfout bereikt de response-hook van httpx niet (zie audit.py): die staat alleen in het logboek van de aanroeper.
        await self.send("GET", "https://storefront-prod.nl.picnicinternational.com/api/15/cart", status=500, body=b"kapot")
        with self.assertRaises(httpx.ConnectError):
            await self.fetch("GET", "https://www.jumbo.com/api/graphql", {"https://www.jumbo.com/api/graphql": lambda request: (_ for _ in ()).throw(httpx.ConnectError("geen verbinding"))})
        (failed,) = await self.entries()
        self.assertEqual((failed["service"], failed["ok"], failed["status"]), ("picnic", False, 500))

    async def test_secrets_never_reach_the_log(self):
        secret_body = {
            "grant_type": "authorization_code",
            "code": "AUTHCODE123",
            "code_verifier": "VERIFIER123",
            "refresh_token": "REFRESH123",
            "key": "me@example.com",
            "secret": "md5hashofpassword",
            "client_id": "appie-ios",
        }
        answer = json.dumps({"access_token": "ACCESS123", "refresh_token": "REFRESH456", "expires_in": 3600})
        await self.send("POST", "https://login.ah.nl/oauth?code=URLCODE&client_id=appie-ios", json=secret_body, body=answer, headers={"Authorization": "Bearer HEADER123", "x-picnic-auth": "PICNIC123"})
        (entry,) = await self.entries()
        full = await audit.get_entry(entry["id"])
        everything = json.dumps(full)
        for secret in ("AUTHCODE123", "VERIFIER123", "REFRESH123", "REFRESH456", "ACCESS123", "me@example.com", "md5hashofpassword", "URLCODE", "HEADER123", "PICNIC123"):
            self.assertNotIn(secret, everything, secret)
        self.assertIn("appie-ios", everything, "niet-geheime velden blijven leesbaar")
        self.assertIn("authorization_code", everything)
        self.assertIn(audit.HIDDEN, full["request"])
        self.assertIn(audit.HIDDEN, full["response"])

    async def test_secrets_in_plain_text_bodies_are_hidden_too(self):
        await self.send("POST", "https://auth.jumbo.com/oauth/token", body=b"error: bad token=abc123 password=hunter2", content_type="text/plain")
        (entry,) = await self.entries()
        response = (await audit.get_entry(entry["id"]))["response"]
        self.assertNotIn("abc123", response)
        self.assertNotIn("hunter2", response)

    async def test_binary_responses_are_summarised_not_stored(self):
        await self.send("GET", "https://www.ah.nl/image.png", body=b"\x89PNG\r\n", content_type="image/png")
        (entry,) = await self.entries()
        self.assertTrue((await audit.get_entry(entry["id"]))["response"].startswith("[image/png"))

    async def test_long_bodies_are_cut(self):
        os.environ["MEALIESHOPPER_AUDIT_BODY_CHARS"] = "100"
        await self.send("GET", "https://www.ah.nl/x", body=json.dumps({"text": "a" * 1000}))
        (entry,) = await self.entries()
        response = (await audit.get_entry(entry["id"]))["response"]
        self.assertLess(len(response), 200)
        self.assertIn("ingekort", response)
        self.assertGreater(entry["respBytes"], 1000, "de echte grootte blijft bekend")

    async def test_bodies_can_be_turned_off_but_the_request_is_still_logged(self):
        os.environ["MEALIESHOPPER_AUDIT_BODIES"] = "false"
        await self.send("POST", "https://www.jumbo.com/api/graphql", json={"operationName": "Basket"}, body=b'{"data": 1}')
        (entry,) = await self.entries()
        full = await audit.get_entry(entry["id"])
        self.assertEqual((full["request"], full["response"]), ("", ""))
        self.assertEqual(entry["action"], "GraphQL Basket")

    async def test_the_log_can_be_turned_off(self):
        os.environ["MEALIESHOPPER_AUDIT_LOG"] = "false"
        await self.send("GET", "https://www.ah.nl/x")
        self.assertEqual(await self.entries(), [])

    async def test_the_login_proxy_keeps_pages_but_skips_assets_and_bodies(self):
        with audit.login_proxy():
            await self.send("GET", "https://login.ah.nl/style.css", content_type="text/css")
            await self.send("GET", "https://login.ah.nl/", body="<html>wachtwoord</html>", content_type="text/html")
        (entry,) = await self.entries()
        self.assertTrue(entry["detail"]["loginProxy"])
        self.assertEqual((await audit.get_entry(entry["id"]))["response"], "")

class HttpxOutboundTest(AuditTestCase):
    """Hetzelfde auditlog voor httpx, via de hooks uit `audit.httpx_hooks()` die `http_client` aanhaakt."""

    def setUp(self) -> None:
        super().setUp()
        http_client.reset()
        self.addCleanup(http_client.reset)

    async def test_outbound_httpx_call_is_logged_with_service_status_and_duration(self) -> None:
        async def slow(request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(0.02)
            return httpx.Response(200, json={"products": [{"title": "Melk"}]})

        url: str = "https://api.ah.nl/mobile-services/product/search/v2?query=melk&access_token=GEHEIM"
        await self.fetch("GET", url, {url: slow})
        entry: dict[str, Any]
        (entry,) = await self.entries()
        self.assertEqual((entry["kind"], entry["service"], entry["method"], entry["status"], entry["ok"]), ("outbound", "ah", "GET", 200, True))
        self.assertEqual(entry["action"], "GET /mobile-services/product/search/v2")
        self.assertIn("query=melk", entry["url"])
        self.assertNotIn("GEHEIM", entry["url"])
        self.assertGreaterEqual(entry["durationMs"], 20)
        self.assertEqual(entry["actor"], "system")
        self.assertEqual(entry["detail"]["contentType"], "application/json")
        full: dict[str, Any] = await audit.get_entry(entry["id"])
        self.assertIn("Melk", full["response"])
        self.assertEqual(entry["respBytes"], len(b'{"products":[{"title":"Melk"}]}'))

    async def test_httpx_request_body_and_graphql_name_are_logged_without_secrets(self) -> None:
        url: str = "https://www.jumbo.com/api/graphql"
        await self.fetch("POST", url, {url: httpx.Response(500, text="kapot")}, json={"operationName": "Basket", "password": "hunter2"})
        entry: dict[str, Any]
        (entry,) = await self.entries()
        self.assertEqual((entry["service"], entry["action"], entry["status"], entry["ok"]), ("jumbo", "GraphQL Basket", 500, False))
        full: dict[str, Any] = await audit.get_entry(entry["id"])
        self.assertIn("Basket", full["request"])
        self.assertNotIn("hunter2", json.dumps(full))
        self.assertEqual(full["response"], "kapot")

    async def test_httpx_other_hosts_are_not_logged(self) -> None:
        url: str = "https://mealie.example.com/api/recipes"
        await self.fetch("GET", url, {url: httpx.Response(200, json={})})
        self.assertEqual(await self.entries(), [])

    async def test_httpx_redirects_count_as_one_request(self) -> None:
        start: str = "https://www.jumbo.com/start"
        target: str = "https://www.jumbo.com/final"
        await self.fetch("GET", start, {start: httpx.Response(302, headers={"Location": target}), target: httpx.Response(200, json={"ok": True})})
        entry: dict[str, Any]
        (entry,) = await self.entries()
        self.assertEqual((entry["status"], entry["url"]), (200, start))
        self.assertEqual(entry["detail"]["redirects"], 1)
        self.assertEqual(entry["detail"]["finalUrl"], target)

    async def test_httpx_redirect_that_is_not_followed_is_logged_once(self) -> None:
        start: str = "https://marleyspoon.nl/menu"
        target: str = "https://marleyspoon.nl/login"
        response: httpx.Response = await self.fetch("GET", start, {start: httpx.Response(302, headers={"Location": target})}, follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        entry: dict[str, Any]
        (entry,) = await self.entries()
        self.assertEqual((entry["service"], entry["status"], entry["url"]), ("marleyspoon", 302, start))
        self.assertNotIn("redirects", entry["detail"])

    async def test_fetch_waits_until_the_redirect_line_is_written(self) -> None:
        # De regel van een niet gevolgde doorverwijzing komt uit een eigen task (`audit-redirect`); `fetch` wacht daar echt op
        # (`settle_audit`), in plaats van een paar rondjes van de loop te hopen dat het schrijven klaar is.
        start: str = "https://www.jumbo.com/api/graphql"
        response: httpx.Response = await self.fetch("GET", start, {start: httpx.Response(301, headers={"Location": "https://www.jumbo.com/elders"})}, follow_redirects=False)
        self.assertEqual(response.status_code, 301)
        self.assertEqual([name for name in tasks.running() if name.startswith("audit-")], [])
        entry: dict[str, Any]
        (entry,) = await self.entries()
        self.assertEqual((entry["service"], entry["status"]), ("jumbo", 301))

    async def test_httpx_binary_responses_are_not_read(self) -> None:
        url: str = "https://www.ah.nl/image.png"
        await self.fetch("GET", url, {url: httpx.Response(200, content=b"\x89PNG\r\n", headers={"Content-Type": "image/png"})})
        entry: dict[str, Any]
        (entry,) = await self.entries()
        self.assertEqual((await audit.get_entry(entry["id"]))["response"], "[image/png, 6 bytes]")
        self.assertEqual(entry["respBytes"], 6)

    async def test_httpx_bodies_can_be_turned_off_but_the_request_is_still_logged(self) -> None:
        os.environ["MEALIESHOPPER_AUDIT_BODIES"] = "false"
        url: str = "https://www.jumbo.com/api/graphql"
        await self.fetch("POST", url, {url: httpx.Response(200, json={"data": 1})}, json={"operationName": "Basket"})
        entry: dict[str, Any]
        (entry,) = await self.entries()
        full: dict[str, Any] = await audit.get_entry(entry["id"])
        self.assertEqual((full["request"], full["response"]), ("", ""))
        self.assertEqual(entry["action"], "GraphQL Basket")

    async def test_httpx_log_can_be_turned_off(self) -> None:
        os.environ["MEALIESHOPPER_AUDIT_LOG"] = "false"
        url: str = "https://www.ah.nl/x"
        await self.fetch("GET", url, {url: httpx.Response(200, json={})})
        self.assertEqual(await self.entries(), [])

    async def test_httpx_logging_failure_never_breaks_the_request(self) -> None:
        url: str = "https://www.ah.nl/x"
        with mock.patch.object(audit, "_connect", side_effect=RuntimeError("schijf vol")):
            response: httpx.Response = await self.fetch("GET", url, {url: httpx.Response(200, json={"ok": True})})
        self.assertEqual(response.json(), {"ok": True})

    async def test_httpx_hook_failure_never_breaks_the_request(self) -> None:
        url: str = "https://www.ah.nl/x"
        for target in ("_record_outbound", "_httpx_mark_start"):
            with self.subTest(target), mock.patch.object(audit, target, side_effect=RuntimeError("kapot in de hook")):
                with self.assertLogs("mealieshopper.audit", "WARNING") as logged:
                    response: httpx.Response = await self.fetch("GET", url, {url: httpx.Response(200, json={"ok": True})})
                self.assertEqual(response.json(), {"ok": True})
                self.assertIn("Kon een uitgaand verzoek niet loggen", logged.output[0])
        self.assertEqual(await self.entries(), [])

    async def test_httpx_mcp_caller_is_kept(self) -> None:
        url: str = "https://www.jumbo.com/api/graphql"
        token: contextvars.Token[str] = audit._actor.set("mcp:Claude")
        corr: contextvars.Token[str] = audit._corr.set("abc123")
        try:
            await self.fetch("GET", url, {url: httpx.Response(200, json={})})
        finally:
            audit._actor.reset(token)
            audit._corr.reset(corr)
        entry: dict[str, Any]
        (entry,) = await self.entries()
        self.assertEqual((entry["actor"], entry["corr"]), ("mcp:Claude", "abc123"))


def _async_result(value: Any) -> Callable[[dict[str, Any]], Awaitable[Any]]:
    """Een tool-handler die zonder te wachten `value` teruggeeft."""

    async def handler(args: dict[str, Any]) -> Any:
        return value

    return handler


class McpTest(AuditTestCase):
    IDENTITY = {"name": "Claude", "username": "admin", "client_id": "c1"}

    async def call(self, message, handler=None):
        return await audit.mcp_message(message, handler or mcp.handle_message, self.IDENTITY)

    async def test_a_tool_call_is_logged_with_its_arguments_and_result(self):
        with mock.patch.dict(mcp.TOOLS["search_jumbo_products"], {"handler": _async_result([{"name": "Melk"}])}):
            await self.call({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "search_jumbo_products", "arguments": {"query": "melk"}}})
        (entry,) = await self.entries()
        self.assertEqual((entry["kind"], entry["service"], entry["action"], entry["method"]), ("mcp", "mcp", "search_jumbo_products", "tools/call"))
        self.assertEqual((entry["actor"], entry["ok"], entry["detail"]["user"]), ("mcp:Claude", True, "admin"))
        full = await audit.get_entry(entry["id"])
        self.assertIn("melk", full["request"])
        self.assertIn("Melk", full["response"])

    async def test_amcp_message_records_the_full_row(self) -> None:
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "search_jumbo_products", "arguments": {"query": "melk"}}}
        with mock.patch.dict(mcp.TOOLS["search_jumbo_products"], {"handler": _async_result({"name": "Melk"})}):
            reply: dict[str, Any] | None = await self.call(message)
        self.assertFalse(reply["result"]["isError"])
        (entry,) = await self.entries()
        self.assertEqual(
            (entry["kind"], entry["service"], entry["action"], entry["method"], entry["url"], entry["status"], entry["ok"], entry["actor"], entry["error"]),
            ("mcp", "mcp", "search_jumbo_products", "tools/call", "/mcp", 200, True, "mcp:Claude", ""),
        )
        self.assertEqual(entry["detail"], {"user": "admin", "clientId": "c1"})
        self.assertTrue(entry["corr"])
        full: dict[str, Any] = await audit.get_entry(entry["id"])
        self.assertIn("melk", full["request"])
        self.assertIn("Melk", full["response"])
        self.assertGreater(full["reqBytes"], 0)
        self.assertGreater(full["respBytes"], 0)
        self.assertIsInstance(full["durationMs"], int)
        self.assertGreaterEqual(full["durationMs"], 0)

    async def test_a_cancelled_tool_is_logged_as_interrupted_and_the_cancellation_propagates(self) -> None:
        async def tool(args: dict[str, Any]) -> Any:
            raise asyncio.CancelledError()

        message: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "search_jumbo_products", "arguments": {}}}
        with mock.patch.dict(mcp.TOOLS["search_jumbo_products"], {"handler": tool}):
            with self.assertRaises(asyncio.CancelledError):
                await self.call(message)
        (entry,) = await self.entries()
        self.assertEqual((entry["ok"], entry["status"], entry["error"]), (False, 499, "onderbroken"))

    async def test_store_requests_made_by_a_tool_are_linked_to_it(self):
        async def tool(args):
            await self.send("GET", "https://www.jumbo.com/api/graphql")
            return {"ok": True}

        with mock.patch.dict(mcp.TOOLS["search_jumbo_products"], {"handler": tool}):
            await self.call({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "search_jumbo_products", "arguments": {}}})
        await self.send("GET", "https://www.jumbo.com/api/graphql")  # los verzoek, buiten MCP
        loose, tool_row, outbound = await self.entries()  # nieuwste eerst: de MCP-regel komt na zijn verzoeken
        self.assertEqual((outbound["service"], outbound["actor"]), ("jumbo", "mcp:Claude"))
        self.assertTrue(outbound["corr"])
        self.assertEqual(outbound["corr"], tool_row["corr"])
        self.assertEqual((loose["corr"], loose["actor"]), ("", "system"))
        detail = await audit.get_entry(tool_row["id"])
        self.assertEqual([item["id"] for item in detail["related"]], [outbound["id"]])
        back = await audit.get_entry(outbound["id"])
        self.assertEqual([item["id"] for item in back["related"]], [tool_row["id"]])

    async def test_a_failing_tool_is_logged_as_an_error(self):
        async def tool(args):
            raise mcp.ToolError("Jumbo wil niet")

        with mock.patch.dict(mcp.TOOLS["search_jumbo_products"], {"handler": tool}):
            await self.call({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "search_jumbo_products", "arguments": {}}})
        (entry,) = await self.entries()
        self.assertFalse(entry["ok"])
        self.assertIn("Jumbo wil niet", entry["error"])

    async def test_unknown_tools_and_methods_are_errors(self):
        await self.call({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "bestaat_niet"}})
        await self.call({"jsonrpc": "2.0", "id": 2, "method": "iets/anders"})
        for entry in await self.entries():
            self.assertFalse(entry["ok"])

    async def test_discovery_is_logged_but_ping_and_notifications_are_not(self):
        await self.call({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "clientInfo": {"name": "Claude"}}})
        await self.call({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        await self.call({"jsonrpc": "2.0", "id": 3, "method": "ping"})
        await self.call({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertEqual([entry["action"] for entry in await self.entries()], ["tools/list", "initialize"])
        self.assertRegex((await audit.get_entry((await self.entries())[0]["id"]))["response"], r"^\d+ tools$")

    async def test_a_secret_in_the_arguments_is_hidden(self):
        with mock.patch.dict(mcp.TOOLS["search_jumbo_products"], {"handler": _async_result([])}):
            await self.call({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "search_jumbo_products", "arguments": {"query": "melk", "token": "GEHEIM123"}}})
        self.assertNotIn("GEHEIM123", json.dumps(await audit.get_entry((await self.entries())[0]["id"])))


class EndpointTest(AuditTestCase):
    """Het hele pad: HTTP-verzoek van een assistent naar /mcp."""

    def setUp(self):
        super().setUp()
        for name, value in (("MEALIESHOPPER_AUTH_SECRET", "x"), ("PASSKEY_AUTH_ENABLED", "true")):
            patcher = mock.patch.dict(os.environ, {name: value})
            patcher.start()
            self.addCleanup(patcher.stop)
        self.app = create_app()
        self.client = self.client_for(self.app)

    async def post(self, message, token="msa_goed"):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return await self.client.post("/mcp", json=message, headers=headers)

    async def test_mcp_requests_are_logged_through_the_http_endpoint(self):
        identity = {"client_id": "c1", "name": "ChatGPT", "user_id": auth.OWNER_ID, "username": "admin"}
        with mock.patch.object(mcp, "enabled", new_callable=mock.AsyncMock, return_value=(True, "")), mock.patch.object(oauth, "verify_access_token", new_callable=mock.AsyncMock, return_value=identity), mock.patch.dict(mcp.TOOLS["list_picnic_orders"], {"handler": _async_result([])}):
            reply = await self.post([{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "list_picnic_orders", "arguments": {}}}, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}])
        self.assertEqual(reply.status_code, 200)
        self.assertEqual([(e["action"], e["actor"]) for e in await self.entries()], [("tools/list", "mcp:ChatGPT"), ("list_picnic_orders", "mcp:ChatGPT")])

    async def test_rejected_requests_are_logged(self):
        with mock.patch.object(mcp, "enabled", new_callable=mock.AsyncMock, return_value=(True, "")):
            self.assertEqual((await self.post({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, token="")).status_code, 401)
            self.assertEqual((await self.post({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, token="msa_fout")).status_code, 401)
        first, second = reversed(await self.entries())
        self.assertEqual((first["status"], first["ok"], first["detail"]["token"].split(" /")[0]), (401, False, "ontbreekt"))
        self.assertEqual(second["detail"]["token"].split(" /")[0], "ongeldig")

    async def test_oauth_steps_are_logged_without_secrets(self):
        with mock.patch.object(mcp, "enabled", new_callable=mock.AsyncMock, return_value=(True, "")):
            registered = await self.client.post("/oauth/register", json={"client_name": "Claude", "redirect_uris": ["https://claude.ai/cb"]})
            self.assertEqual(registered.status_code, 201)
            await self.client.post("/oauth/token", data={"grant_type": "authorization_code", "code": "GEHEIMECODE", "code_verifier": "GEHEIMVERIFIER", "client_id": registered.get_json()["client_id"]})
        token_entry, register_entry = await self.entries()
        self.assertEqual((token_entry["kind"], token_entry["service"]), ("oauth", "mcp"))
        self.assertIn("authorization_code", token_entry["action"])
        self.assertEqual(register_entry["actor"], "mcp:Claude")
        self.assertNotIn("GEHEIME", json.dumps([await audit.get_entry(e["id"]) for e in await self.entries()]))
        self.assertEqual((await audit.get_entry(token_entry["id"]))["response"], "", "tokens in het antwoord worden niet bewaard")


class ApiTest(AuditTestCase):
    async def asyncSetUp(self):
        # de setUp van AuditTestCase is al gelopen
        await self.send("GET", "https://api.ah.nl/a")
        await self.send("GET", "https://www.jumbo.com/b", status=500, body=b"nee")
        await self.send("GET", "https://storefront-prod.nl.picnicinternational.com/c")

    async def get(self, query=""):
        response = await self.client.get("/api/audit" + query)
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    async def test_newest_first_with_counts_and_settings(self):
        data = await self.get()
        self.assertEqual([e["service"] for e in data["entries"]], ["picnic", "jumbo", "ah"])
        self.assertEqual(data["total"], 3)
        self.assertEqual(data["last24h"]["jumbo"], {"total": 1, "errors": 1})
        self.assertEqual(data["settings"]["retentionDays"], audit.DEFAULT_RETENTION_DAYS)
        self.assertNotIn("response", data["entries"][0], "de lijst bevat geen bodies")

    async def test_filters(self):
        self.assertEqual([e["service"] for e in (await self.get("?service=ah"))["entries"]], ["ah"])
        self.assertEqual([e["service"] for e in (await self.get("?status=error"))["entries"]], ["jumbo"])
        self.assertEqual([e["service"] for e in (await self.get("?q=/c"))["entries"]], ["picnic"])
        self.assertEqual((await self.get("?service=mcp"))["entries"], [])
        self.assertEqual(len((await self.get("?q=%25"))["entries"]), 0, "% in de zoekterm is letterlijk")

    async def test_paging(self):
        first = await self.get("?limit=2")
        self.assertEqual(len(first["entries"]), 2)
        self.assertIsNotNone(first["next"])
        second = await self.get(f"?limit=2&before={first['next']}")
        self.assertEqual([e["service"] for e in second["entries"]], ["ah"])
        self.assertIsNone(second["next"])

    async def test_detail_and_missing_entry(self):
        entry = (await self.get())["entries"][1]
        detail = (await self.client.get(f"/api/audit/{entry['id']}")).get_json()
        self.assertEqual(detail["response"], "nee")
        self.assertEqual((await self.client.get("/api/audit/99999")).status_code, 404)

    async def test_bad_numbers_are_rejected(self):
        self.assertEqual((await self.client.get("/api/audit?limit=veel")).status_code, 400)

    async def test_clearing_leaves_a_trace(self):
        response = await self.client.delete("/api/audit")
        self.assertEqual(response.get_json()["removed"], 3)
        (entry,) = (await self.get())["entries"]
        self.assertEqual((entry["kind"], entry["action"]), ("audit", "log cleared"))
        self.assertEqual(entry["detail"]["removed"], 3)

    def test_only_the_admin_may_read_it(self):
        self.assertTrue(auth.admin_only("/api/audit"))
        self.assertTrue(auth.admin_only("/api/audit/12"))


class ExportTest(AuditTestCase):
    async def asyncSetUp(self):
        # de setUp van AuditTestCase is al gelopen
        await self.send("GET", "https://api.ah.nl/a", body=json.dumps({"naam": "café"}))
        await self.send("POST", "https://www.jumbo.com/api/graphql", status=500, body=b"=HYPERLINK(1)", json={"operationName": "=Boom", "token": "GEHEIM"})

    async def test_json_export_has_bodies_and_respects_filters(self):
        response = await self.client.get("/api/audit/export?format=json&status=error")
        self.assertIn("attachment; filename=\"mealieshopper-audit-", response.headers["Content-Disposition"])
        self.assertTrue(response.headers["Content-Disposition"].endswith('.json"'))
        data = response.get_json()
        (entry,) = data["entries"]
        self.assertEqual(entry["service"], "jumbo")
        self.assertIn("request", entry)
        self.assertNotIn("GEHEIM", json.dumps(data))
        everything = (await self.client.get("/api/audit/export")).get_json()["entries"]
        self.assertEqual([e["service"] for e in everything], ["ah", "jumbo", "audit"], "oudste eerst; de eerste export staat er al in")

    async def test_csv_export_is_readable_and_formula_safe(self):
        import csv, io

        response = await self.client.get("/api/audit/export?format=csv")
        self.assertEqual(response.mimetype, "text/csv")
        text = response.get_data(as_text=True)
        self.assertTrue(text.startswith("\ufeff"))
        rows = list(csv.DictReader(io.StringIO(text.lstrip("\ufeff"))))
        self.assertEqual([r["service"] for r in rows], ["ah", "jumbo"])
        self.assertIn("café", rows[0]["response"])
        self.assertEqual(rows[1]["response"], "'=HYPERLINK(1)")
        self.assertEqual(rows[1]["action"], "GraphQL =Boom", "alleen een cel die ermee begint wordt beschermd")

    async def test_the_export_is_converted_off_the_event_loop(self):
        """Tot 5000 regels omzetten is rekenwerk: CSV en JSON in een thread, niet op de thread van de event loop."""
        loop_thread: int = threading.get_ident()
        threads: list[int] = []
        entries_csv: Callable[..., str] = audit.entries_csv

        def csv_in_thread(entries: list[dict[str, Any]]) -> str:
            threads.append(threading.get_ident())
            return entries_csv(entries)

        def dumps(value: Any, **kwargs: Any) -> str:
            if kwargs.get("indent") == 1:  # de export zelf, niet het auditlog van het verzoek
                threads.append(threading.get_ident())
            return json.dumps(value, **kwargs)

        recording_json = types.SimpleNamespace(**{name: getattr(json, name) for name in ("loads", "JSONDecodeError")}, dumps=dumps)
        with mock.patch.object(audit, "entries_csv", csv_in_thread), mock.patch.object(audit, "json", recording_json):
            self.assertEqual((await self.client.get("/api/audit/export?format=csv")).status_code, 200)
            self.assertEqual((await self.client.get("/api/audit/export?format=json")).status_code, 200)
        self.assertEqual(len(threads), 2)
        self.assertNotIn(loop_thread, threads)

    async def test_an_export_leaves_a_trace(self):
        await self.client.get("/api/audit/export")
        newest = (await self.client.get("/api/audit")).get_json()["entries"][0]
        self.assertEqual((newest["kind"], newest["action"], newest["detail"]["entries"]), ("audit", "log exported", 2))


class RetentionTest(AuditTestCase):
    async def test_old_entries_and_excess_rows_are_pruned(self):
        old = (auth.utcnow() - audit.timedelta(days=45)).isoformat()
        await audit.record(kind="outbound", service="ah", action="oud")
        with closing(db(audit)) as conn, conn:
            conn.execute("UPDATE audit_log SET ts = ?", (old,))
        with mock.patch.object(audit, "PRUNE_EVERY", 1):
            await audit.record(kind="outbound", service="ah", action="nieuw")
        self.assertEqual([e["action"] for e in await self.entries()], ["nieuw"])

    async def test_zero_days_keeps_everything(self):
        os.environ["MEALIESHOPPER_AUDIT_RETENTION_DAYS"] = "0"
        await audit.record(kind="outbound", service="ah", action="oud")
        with closing(db(audit)) as conn, conn:
            conn.execute("UPDATE audit_log SET ts = '2000-01-01T00:00:00'")
        with mock.patch.object(audit, "PRUNE_EVERY", 1):
            await audit.record(kind="outbound", service="ah", action="nieuw")
        self.assertEqual(len(await self.entries()), 2)

    async def test_the_row_cap_is_enforced(self):
        with mock.patch.object(audit, "PRUNE_EVERY", 1), mock.patch.object(audit, "MAX_ROWS", 3):
            for number in range(6):
                await audit.record(kind="outbound", service="ah", action=f"r{number}")
        self.assertEqual([e["action"] for e in await self.entries()], ["r5", "r4", "r3"])


@pytest.fixture
def audit_dir(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Een lege datamap met het auditlog aan en de standaardinstellingen."""
    for name in ("MEALIESHOPPER_AUDIT_LOG", "MEALIESHOPPER_AUDIT_BODIES", "MEALIESHOPPER_AUDIT_BODY_CHARS", "MEALIESHOPPER_AUDIT_RETENTION_DAYS"):
        monkeypatch.delenv(name, raising=False)
    return data_dir


AUDIT_FIELDS: dict[str, Any] = {
    "kind": "outbound",
    "service": "ah",
    "action": "GET /mobile-services/v1/receipts",
    "method": "GET",
    "url": "https://api.ah.nl/mobile-services/v1/receipts?page=1",
    "status": 502,
    "ok": False,
    "duration_ms": 1234,
    "actor": "mcp:Claude",
    "corr": "abc123",
    "request_body": '{"spam": "eggs"}',
    "response_body": "kapot",
    "req_bytes": 16,
    "resp_bytes": 5,
    "error": "Bad gateway",
    "detail": {"hops": 2},
}


# Boven elke inkortgrens (action en actor 200, url 2000, error 500) en met een fout die redact_text verandert.
OVERSIZED_FIELDS: dict[str, Any] = {
    **AUDIT_FIELDS,
    "action": "spam " * 60,
    "url": "https://api.ah.nl/" + "eggs/" * 500,
    "actor": "mcp:" + "ham" * 80,
    "ok": True,
    "duration_ms": 12.7,
    "error": "password=hunter2 upstream sent Bearer abc.def.ghi " + "x" * 600,
    "detail": {"naam": "crème brûlée"},
}


def audit_rows() -> list[dict[str, Any]]:
    """Alle rijen zonder id en tijdstip, oudste eerst."""
    with closing(db()) as conn:
        rows: list[dict[str, Any]] = [dict(row) for row in conn.execute("SELECT * FROM audit_log ORDER BY id")]
    for row in rows:
        del row["id"], row["ts"]
    return rows


@pytest.mark.parametrize("fields", [AUDIT_FIELDS, OVERSIZED_FIELDS], ids=["gewoon", "ingekort-en-geredigeerd"])
async def test_record_writes_one_row_per_call(audit_dir: Path, fields: dict[str, Any]) -> None:
    first: int | None = await audit.record(**fields)
    second: int | None = await audit.record(**fields)
    assert isinstance(first, int) and second == first + 1
    rows: list[dict[str, Any]] = audit_rows()
    assert rows[0] == rows[1]
    if fields is AUDIT_FIELDS:  # binnen elke grens: de rij is precies wat er werd meegegeven
        columns: dict[str, str] = {"request_body": "request", "response_body": "response"}
        expected: dict[str, Any] = {columns.get(key, key): value for key, value in AUDIT_FIELDS.items()}
        expected.update(ok=1 if AUDIT_FIELDS["ok"] else 0, detail=json.dumps(AUDIT_FIELDS["detail"], ensure_ascii=False))
        assert rows[0] == expected
    assert (rows[0]["kind"], rows[0]["service"], rows[0]["corr"]) == (fields["kind"], fields["service"], fields["corr"])
    assert (await audit.get_entry(second))["detail"] == fields["detail"]


async def test_the_oversized_row_is_cut_and_redacted(audit_dir: Path) -> None:
    """Bewaakt dat OVERSIZED_FIELDS echt elke grens en de redactie raakt, anders bewijst de pariteitstest weinig."""
    await audit.record(**OVERSIZED_FIELDS)
    row: dict[str, Any] = audit_rows()[0]
    assert (len(row["action"]), len(row["url"]), len(row["actor"]), len(row["error"])) == (200, 2000, 200, 500)
    assert "hunter2" not in row["error"] and "abc.def.ghi" not in row["error"]
    assert audit.HIDDEN in row["error"]
    assert (row["ok"], row["duration_ms"]) == (1, 12)
    assert json.loads(row["detail"]) == {"naam": "crème brûlée"}


async def test_record_creates_the_table_in_a_fresh_data_dir(audit_dir: Path) -> None:
    row_id: int | None = await audit.record(kind="inbound", service="mcp", action="tools/list")
    assert [entry["action"] for entry in (await audit.list_entries())["entries"]] == ["tools/list"]
    assert row_id == (await audit.list_entries())["entries"][0]["id"]


async def test_record_respects_the_switch(audit_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEALIESHOPPER_AUDIT_LOG", "false")
    assert await audit.record(kind="outbound", service="ah", action="uit") is None
    assert (await audit.list_entries())["entries"] == []


async def test_record_never_raises(audit_dir: Path) -> None:
    with mock.patch.object(audit, "_connect", side_effect=sqlite3.OperationalError("disk I/O error")), mock.patch.object(audit.log, "warning") as warning:
        assert await audit.record(kind="outbound", service="ah", action="kapot") is None
    warning.assert_called_once()


async def test_record_prunes_to_the_row_cap(audit_dir: Path) -> None:
    with mock.patch.object(audit, "PRUNE_EVERY", 1), mock.patch.object(audit, "MAX_ROWS", 3):
        for number in range(6):
            await audit.record(kind="outbound", service="ah", action=f"r{number}")
    assert [entry["action"] for entry in (await audit.list_entries())["entries"]] == ["r5", "r4", "r3"]


async def test_record_writes_through_auth_connect(audit_dir: Path) -> None:
    with mock.patch.object(auth, "connect", wraps=auth.connect) as connect:
        assert isinstance(await audit.record(kind="outbound", service="ah", action="async"), int)
    connect.assert_called()


@pytest.mark.parametrize(
    "text",
    [
        "Authorization: Bearer abc.def.ghi",
        "authorization: bearer abc.def.ghi",
        "AUTHORIZATION=Bearer abc.def.ghi",
        '{"Authorization": "Bearer abc.def.ghi"}',
        "upstream sent Bearer abc.def.ghi and gave up",
        "upstream sent bearer abc.def.ghi and gave up",
        "Authorization: Basic abc.def.ghi",
    ],
)
def test_redact_text_hides_the_token_after_bearer(text: str) -> None:
    """In de kopvorm (`Authorization: Bearer <token>`) bleef het token staan: alleen het woord Bearer werd verborgen."""
    redacted: str = audit.redact_text(text)
    assert "abc.def.ghi" not in redacted
    assert audit.HIDDEN in redacted


def test_a_lone_bearer_at_the_end_of_a_line_keeps_the_next_line() -> None:
    """Een kop zonder token aan het eind van een regel neemt de volgende regel niet mee als "token"."""
    assert audit.redact_text("Authorization: Bearer\nstatus 401 van AH") .splitlines()[1] == "status 401 van AH"


def test_redact_text_leaves_ordinary_words_alone() -> None:
    assert audit.redact_text("Authorization ontbreekt, probeer opnieuw") == "Authorization ontbreekt, probeer opnieuw"
    assert audit.redact_text("de beer zat in de boom") == "de beer zat in de boom"


async def test_an_unfollowed_redirect_is_recorded_by_a_task_that_is_held(audit_dir: Path) -> None:
    """De regel voor een doorverwijzing die httpx niet volgt, komt een ronde later: als task die `tasks.spawn` vasthoudt
    (een losse task zonder verwijzing kan halverwege worden opgeruimd)."""
    request: httpx.Request = httpx.Request("GET", "https://www.ah.nl/start")
    await audit._httpx_request(request)
    response: httpx.Response = httpx.Response(302, headers={"Location": "https://www.ah.nl/elders", "Content-Type": "text/html"}, request=request)
    response.next_request = httpx.Request("GET", "https://www.ah.nl/elders")  # httpx zet dit als hij niet volgt
    await audit._httpx_response(response)
    held: dict[str, asyncio.Task[Any]] = {name: task for name, task in tasks.running().items() if name.startswith("audit-redirect")}
    assert len(held) == 1, tasks.running()
    await asyncio.wait(list(held.values()))
    (entry,) = (await audit.list_entries())["entries"]
    assert (entry["status"], entry["url"]) == (302, "https://www.ah.nl/start")


async def test_outbound_calls_share_one_writer_connection(audit_dir: Path) -> None:
    """Taak 9c: elk uitgaand verzoek opende voor zijn auditregel een eigen aiosqlite-verbinding, dus een nieuwe thread, en op
    de event loop wacht `Thread.start` tot die draait (onder belasting een stilstand van 131 ms). Nu schrijft een schrijver per
    loop alle regels over een verbinding: twintig verzoeken, tegelijk en na elkaar, starten hooguit een thread."""
    url: str = "https://api.ah.nl/mobile-services/product/search/v2?query=melk"
    started: list[threading.Thread] = []
    original_start: Callable[[threading.Thread], None] = threading.Thread.start

    def counted(thread: threading.Thread) -> None:
        if "_connection_worker_thread" in thread.name:
            started.append(thread)
        original_start(thread)

    await audit.record(kind="outbound", service="ah", action="opwarmen")  # het schema, een keer per database
    try:
        with respx.mock() as router, mock.patch.object(threading.Thread, "start", counted):
            router.get(url).mock(return_value=httpx.Response(200, json={}))
            client: httpx.AsyncClient = http_client.client("test")
            await asyncio.gather(*(client.get(url) for _ in range(10)))
            for _ in range(10):
                await client.get(url)
    finally:
        await http_client.aclose_all()
    assert len(started) <= 1, [thread.name for thread in started]
    assert len((await audit.list_entries())["entries"]) == 21


def recording(target: str) -> telemetry.Recording:
    return telemetry.Recording("GET", target)


async def record_under(found: telemetry.Recording, action: str) -> int | None:
    """`audit.record` in een eigen context met `found` als meting van de aanvraag, zoals de middleware van de telemetrie doet."""

    async def inside() -> int | None:
        telemetry._current.set(found)
        return await audit.record(kind="outbound", service="ah", action=action)

    return await asyncio.create_task(inside(), context=contextvars.Context())


async def test_each_row_counts_for_the_request_that_wrote_it(audit_dir: Path) -> None:
    """Review van taak 9c: de schrijver erfde de context van de eerste aanroeper, met diens meting. Elke volgende regel telde dan
    bij die al klare aanvraag. Nu loopt hij in een lege context, en telt elke regel bij zijn eigen aanvraag."""
    first: telemetry.Recording = recording("/eerste")
    second: telemetry.Recording = recording("/tweede")
    assert isinstance(await record_under(first, "een"), int)
    assert isinstance(await record_under(second, "twee"), int)
    assert (first.counts.get("db"), second.counts.get("db")) == (1, 1)
    assert second.slowest_sql == "INSERT audit_log"
    writer: Any = audit._WRITERS[asyncio.get_running_loop()]
    assert writer.task is not None and writer.task.get_context().get(telemetry._current) is None


async def test_a_row_that_arrives_as_the_wait_times_out_is_kept(audit_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review van taak 9c: kwam een regel binnen op het moment dat het wachten op de eerste afliep, dan gaf de schrijver een lege
    lijst en stopte hij, met de regel nog in de wachtrij. Nu hoort die regel bij de laatste ronde."""
    writer: Any = audit._Writer(asyncio.get_running_loop())
    late: Any = audit._Pending("x", (), asyncio.get_running_loop().create_future(), None)

    async def times_out(awaitable: Any, timeout: float) -> Any:
        awaitable.close()
        writer.queue.put_nowait(late)
        raise TimeoutError

    monkeypatch.setattr(audit.asyncio, "wait_for", times_out)
    assert await writer._next_batch() == [late]


async def test_cancel_all_writes_what_is_queued_and_record_writes_directly_after(audit_dir: Path) -> None:
    """Review van taak 9c: `cancel_all` annuleerde de schrijver, en de regels in de wachtrij kregen None. Nu laat hij hem
    leeglopen. Een regel na het afsluiten schrijft `record` zelf, zonder een schrijver te starten waar niemand op wacht."""
    queued: list[asyncio.Future[int | None]] = [audit._writer().submit(audit._row_values(**{**AUDIT_FIELDS, "action": f"r{n}"})) for n in range(3)]
    await tasks.cancel_all(timeout=5)
    assert all(isinstance(future.result(), int) for future in queued)
    assert isinstance(await audit.record(kind="outbound", service="ah", action="na"), int)
    assert asyncio.get_running_loop() not in audit._WRITERS
    assert [entry["action"] for entry in (await audit.list_entries())["entries"]] == ["na", "r2", "r1", "r0"]


async def test_a_row_after_shutdown_starts_no_writer_also_when_none_was_written_before(
    audit_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Taak 10b: `_stop_writers` hing pas aan `cancel_all` bij de eerste schrijver (`_writer`). Schreef een proces voor het
    afsluiten nog geen regel, dan wist `record` daarna niet dat de loop gestopt was en startte hij een schrijver waar niemand op
    wachtte. Nu onthoudt `tasks.cancel_all` zelf welke loop gestopt is. Het lege register van haken is dat van een vers proces."""
    monkeypatch.setattr(tasks, "_before_cancel", [])
    await tasks.cancel_all(timeout=5)
    assert isinstance(await audit.record(kind="outbound", service="ah", action="na"), int)
    assert asyncio.get_running_loop() not in audit._WRITERS
    assert [entry["action"] for entry in (await audit.list_entries())["entries"]] == ["na"]


async def test_an_idle_writer_lets_go_of_its_loop(audit_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review van taak 9c: `_WRITERS` hield elke loop vast (de schrijver verwijst via zijn task naar de loop). Een schrijver die
    stopt, haalt zichzelf weg, en de volgende regel start een nieuwe."""
    monkeypatch.setattr(audit, "WRITER_IDLE_SECONDS", 0.05)
    await audit.record(kind="outbound", service="ah", action="een")
    writer: Any = audit._WRITERS[asyncio.get_running_loop()]
    await writer.task
    assert asyncio.get_running_loop() not in audit._WRITERS
    assert isinstance(await audit.record(kind="outbound", service="ah", action="twee"), int)
    assert audit._WRITERS[asyncio.get_running_loop()] is not writer


async def test_wiping_the_data_retires_the_writer(audit_dir: Path) -> None:
    """Review van taak 9c: de fabrieksreset haalt `auth.sqlite3` weg terwijl de schrijver er een verbinding op heeft. Na
    `auth.forget_runtime_state` stopt die schrijver, en de volgende regel komt via een nieuwe in het nieuwe bestand."""
    await audit.record(kind="outbound", service="ah", action="oud")
    old: Any = audit._WRITERS[asyncio.get_running_loop()]
    auth.forget_runtime_state()
    await old.task
    assert isinstance(await audit.record(kind="outbound", service="ah", action="nieuw"), int)
    assert audit._WRITERS[asyncio.get_running_loop()] is not old


if __name__ == "__main__":
    unittest.main()
