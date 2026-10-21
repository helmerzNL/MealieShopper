import json
import os
import tempfile
import unittest
from unittest import mock

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

import requests  # noqa: E402

from mealieshopper import audit, auth, mcp, oauth  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402


def fake_response(url, status=200, body=b"{}", content_type="application/json", headers=None):
    response = requests.Response()
    response.status_code = status
    response._content = body if isinstance(body, bytes) else body.encode()
    response.headers.update({"Content-Type": content_type, **(headers or {})})
    response.url = url
    return response


class AuditTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(os.environ, {"MEALIESHOPPER_DATA_DIR": tmp.name, "PASSKEY_AUTH_ENABLED": "false"})
        env.start()
        self.addCleanup(env.stop)
        for name in ("MEALIESHOPPER_AUDIT_LOG", "MEALIESHOPPER_AUDIT_BODIES", "MEALIESHOPPER_AUDIT_BODY_CHARS", "MEALIESHOPPER_AUDIT_RETENTION_DAYS"):
            os.environ.pop(name, None)
        audit.install()
        self.app = create_app()
        self.client = self.app.test_client()

    def entries(self, **kwargs):
        return audit.list_entries(**kwargs)["entries"]

    def send(self, method, url, status=200, body=b"{}", content_type="application/json", **kwargs):
        """Een echt verzoek via requests, met alleen het netwerk nagebootst."""
        with mock.patch("requests.adapters.HTTPAdapter.send", return_value=fake_response(url, status, body, content_type)):
            return requests.request(method, url, **kwargs)


class OutboundTest(AuditTestCase):
    def test_every_store_is_recognised_by_its_host(self):
        for url, service in (
            ("https://api.ah.nl/mobile-services/product/search/v2", "ah"),
            ("https://login.ah.nl/x", "ah"),
            ("https://www.ah.nl/allerhande", "ah"),
            ("https://storefront-prod.nl.picnicinternational.com/api/15/cart", "picnic"),
            ("https://www.jumbo.com/api/graphql", "jumbo"),
            ("https://auth.jumbo.com/oauth/token", "jumbo"),
        ):
            self.assertEqual(audit.service_for(url), service, url)
        for url in ("https://mealie.example.com/api", "https://evilah.nl/", "https://ah.nl.evil.example/", "https://github.com/x"):
            self.assertEqual(audit.service_for(url), "", url)

    def test_a_request_is_logged_with_status_duration_and_body(self):
        self.send("GET", "https://api.ah.nl/mobile-services/product/search/v2?query=melk", body=json.dumps({"products": [{"title": "Melk"}]}))
        (entry,) = self.entries()
        self.assertEqual((entry["kind"], entry["service"], entry["method"], entry["status"], entry["ok"]), ("outbound", "ah", "GET", 200, True))
        self.assertEqual(entry["action"], "GET /mobile-services/product/search/v2")
        self.assertIn("query=melk", entry["url"])
        full = audit.get_entry(entry["id"])
        self.assertIn("Melk", full["response"])
        self.assertEqual(entry["actor"], "system")

    def test_other_hosts_are_not_logged(self):
        self.send("GET", "https://mealie.example.com/api/recipes")
        self.send("GET", "https://github.com/helmerzNL/MealieShopper/releases/latest")
        self.assertEqual(self.entries(), [])

    def test_graphql_requests_are_named_after_their_operation(self):
        self.send("POST", "https://www.jumbo.com/api/graphql", json={"operationName": "FavoriteRecipes", "variables": {}})
        (entry,) = self.entries()
        self.assertEqual((entry["service"], entry["action"]), ("jumbo", "GraphQL FavoriteRecipes"))

    def test_http_errors_and_network_failures_are_logged_as_failures(self):
        self.send("GET", "https://storefront-prod.nl.picnicinternational.com/api/15/cart", status=500, body=b"kapot")
        with mock.patch("requests.adapters.HTTPAdapter.send", side_effect=requests.ConnectionError("geen verbinding")):
            with self.assertRaises(requests.ConnectionError):
                requests.get("https://www.jumbo.com/api/graphql")
        crashed, failed = self.entries()
        self.assertEqual((crashed["service"], crashed["ok"], crashed["status"]), ("jumbo", False, None))
        self.assertIn("ConnectionError: geen verbinding", crashed["error"])
        self.assertEqual((failed["service"], failed["ok"], failed["status"]), ("picnic", False, 500))

    def test_secrets_never_reach_the_log(self):
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
        self.send("POST", "https://login.ah.nl/oauth?code=URLCODE&client_id=appie-ios", json=secret_body, body=answer, headers={"Authorization": "Bearer HEADER123", "x-picnic-auth": "PICNIC123"})
        (entry,) = self.entries()
        full = audit.get_entry(entry["id"])
        everything = json.dumps(full)
        for secret in ("AUTHCODE123", "VERIFIER123", "REFRESH123", "REFRESH456", "ACCESS123", "me@example.com", "md5hashofpassword", "URLCODE", "HEADER123", "PICNIC123"):
            self.assertNotIn(secret, everything, secret)
        self.assertIn("appie-ios", everything, "niet-geheime velden blijven leesbaar")
        self.assertIn("authorization_code", everything)
        self.assertIn(audit.HIDDEN, full["request"])
        self.assertIn(audit.HIDDEN, full["response"])

    def test_secrets_in_plain_text_bodies_are_hidden_too(self):
        self.send("POST", "https://auth.jumbo.com/oauth/token", body=b"error: bad token=abc123 password=hunter2", content_type="text/plain")
        (entry,) = self.entries()
        response = audit.get_entry(entry["id"])["response"]
        self.assertNotIn("abc123", response)
        self.assertNotIn("hunter2", response)

    def test_redirects_count_as_one_request(self):
        target = "https://www.jumbo.com/final"
        first = fake_response("https://www.jumbo.com/start", 302, b"", headers={"Location": target})
        second = fake_response(target, 200, b'{"ok": true}')
        with mock.patch("requests.adapters.HTTPAdapter.send", side_effect=[first, second]):
            requests.get("https://www.jumbo.com/start")
        (entry,) = self.entries()
        self.assertEqual(entry["status"], 200)
        self.assertEqual(entry["detail"]["redirects"], 1)

    def test_streamed_responses_are_not_read(self):
        response = fake_response("https://www.ah.nl/image.png", body=b"x" * 10, content_type="image/png", headers={"Content-Length": "10"})
        with mock.patch("requests.adapters.HTTPAdapter.send", return_value=response):
            requests.get("https://www.ah.nl/image.png", stream=True)
        (entry,) = self.entries()
        self.assertEqual(audit.get_entry(entry["id"])["response"], "[stream]")

    def test_binary_responses_are_summarised_not_stored(self):
        self.send("GET", "https://www.ah.nl/image.png", body=b"\x89PNG\r\n", content_type="image/png")
        (entry,) = self.entries()
        self.assertTrue(audit.get_entry(entry["id"])["response"].startswith("[image/png"))

    def test_long_bodies_are_cut(self):
        os.environ["MEALIESHOPPER_AUDIT_BODY_CHARS"] = "100"
        self.send("GET", "https://www.ah.nl/x", body=json.dumps({"text": "a" * 1000}))
        (entry,) = self.entries()
        response = audit.get_entry(entry["id"])["response"]
        self.assertLess(len(response), 200)
        self.assertIn("ingekort", response)
        self.assertGreater(entry["respBytes"], 1000, "de echte grootte blijft bekend")

    def test_bodies_can_be_turned_off_but_the_request_is_still_logged(self):
        os.environ["MEALIESHOPPER_AUDIT_BODIES"] = "false"
        self.send("POST", "https://www.jumbo.com/api/graphql", json={"operationName": "Basket"}, body=b'{"data": 1}')
        (entry,) = self.entries()
        full = audit.get_entry(entry["id"])
        self.assertEqual((full["request"], full["response"]), ("", ""))
        self.assertEqual(entry["action"], "GraphQL Basket")

    def test_the_log_can_be_turned_off(self):
        os.environ["MEALIESHOPPER_AUDIT_LOG"] = "false"
        self.send("GET", "https://www.ah.nl/x")
        self.assertEqual(self.entries(), [])

    def test_a_logging_failure_never_breaks_the_request(self):
        with mock.patch.object(audit, "_connect", side_effect=RuntimeError("schijf vol")):
            response = self.send("GET", "https://www.ah.nl/x")
        self.assertEqual(response.status_code, 200)

    def test_the_login_proxy_keeps_pages_but_skips_assets_and_bodies(self):
        with audit.login_proxy():
            self.send("GET", "https://login.ah.nl/style.css", content_type="text/css")
            self.send("GET", "https://login.ah.nl/", body="<html>wachtwoord</html>", content_type="text/html")
        (entry,) = self.entries()
        self.assertTrue(entry["detail"]["loginProxy"])
        self.assertEqual(audit.get_entry(entry["id"])["response"], "")

    def test_install_is_idempotent(self):
        audit.install()
        audit.install()
        self.send("GET", "https://www.ah.nl/x")
        self.assertEqual(len(self.entries()), 1)


class McpTest(AuditTestCase):
    IDENTITY = {"name": "Claude", "username": "admin", "client_id": "c1"}

    def call(self, message, handler=None):
        return audit.mcp_message(message, handler or mcp.handle_message, self.IDENTITY)

    def test_a_tool_call_is_logged_with_its_arguments_and_result(self):
        with mock.patch.dict(mcp.TOOLS["search_jumbo_products"], {"handler": lambda args: [{"name": "Melk"}]}):
            self.call({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "search_jumbo_products", "arguments": {"query": "melk"}}})
        (entry,) = self.entries()
        self.assertEqual((entry["kind"], entry["service"], entry["action"], entry["method"]), ("mcp", "mcp", "search_jumbo_products", "tools/call"))
        self.assertEqual((entry["actor"], entry["ok"], entry["detail"]["user"]), ("mcp:Claude", True, "admin"))
        full = audit.get_entry(entry["id"])
        self.assertIn("melk", full["request"])
        self.assertIn("Melk", full["response"])

    def test_store_requests_made_by_a_tool_are_linked_to_it(self):
        def tool(args):
            self.send("GET", "https://www.jumbo.com/api/graphql")
            return {"ok": True}

        with mock.patch.dict(mcp.TOOLS["search_jumbo_products"], {"handler": tool}):
            self.call({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "search_jumbo_products", "arguments": {}}})
        self.send("GET", "https://www.jumbo.com/api/graphql")  # los verzoek, buiten MCP
        loose, tool_row, outbound = self.entries()  # nieuwste eerst: de MCP-regel komt na zijn verzoeken
        self.assertEqual((outbound["service"], outbound["actor"]), ("jumbo", "mcp:Claude"))
        self.assertTrue(outbound["corr"])
        self.assertEqual(outbound["corr"], tool_row["corr"])
        self.assertEqual((loose["corr"], loose["actor"]), ("", "system"))
        detail = audit.get_entry(tool_row["id"])
        self.assertEqual([item["id"] for item in detail["related"]], [outbound["id"]])
        back = audit.get_entry(outbound["id"])
        self.assertEqual([item["id"] for item in back["related"]], [tool_row["id"]])

    def test_a_failing_tool_is_logged_as_an_error(self):
        def tool(args):
            raise mcp.ToolError("Jumbo wil niet")

        with mock.patch.dict(mcp.TOOLS["search_jumbo_products"], {"handler": tool}):
            self.call({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "search_jumbo_products", "arguments": {}}})
        (entry,) = self.entries()
        self.assertFalse(entry["ok"])
        self.assertIn("Jumbo wil niet", entry["error"])

    def test_unknown_tools_and_methods_are_errors(self):
        self.call({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "bestaat_niet"}})
        self.call({"jsonrpc": "2.0", "id": 2, "method": "iets/anders"})
        for entry in self.entries():
            self.assertFalse(entry["ok"])

    def test_discovery_is_logged_but_ping_and_notifications_are_not(self):
        self.call({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "clientInfo": {"name": "Claude"}}})
        self.call({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.call({"jsonrpc": "2.0", "id": 3, "method": "ping"})
        self.call({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertEqual([entry["action"] for entry in self.entries()], ["tools/list", "initialize"])
        self.assertRegex(audit.get_entry(self.entries()[0]["id"])["response"], r"^\d+ tools$")

    def test_a_secret_in_the_arguments_is_hidden(self):
        with mock.patch.dict(mcp.TOOLS["search_jumbo_products"], {"handler": lambda args: []}):
            self.call({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "search_jumbo_products", "arguments": {"query": "melk", "token": "GEHEIM123"}}})
        self.assertNotIn("GEHEIM123", json.dumps(audit.get_entry(self.entries()[0]["id"])))


class EndpointTest(AuditTestCase):
    """Het hele pad: HTTP-verzoek van een assistent naar /mcp."""

    def setUp(self):
        super().setUp()
        for name, value in (("MEALIESHOPPER_AUTH_SECRET", "x"), ("PASSKEY_AUTH_ENABLED", "true")):
            patcher = mock.patch.dict(os.environ, {name: value})
            patcher.start()
            self.addCleanup(patcher.stop)
        self.app = create_app()
        self.client = self.app.test_client()

    def post(self, message, token="msa_goed"):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return self.client.post("/mcp", json=message, headers=headers)

    def test_mcp_requests_are_logged_through_the_http_endpoint(self):
        identity = {"client_id": "c1", "name": "ChatGPT", "user_id": auth.OWNER_ID, "username": "admin"}
        with mock.patch.object(mcp, "enabled", return_value=(True, "")), mock.patch.object(oauth, "verify_access_token", return_value=identity), mock.patch.dict(mcp.TOOLS["list_picnic_orders"], {"handler": lambda args: []}):
            reply = self.post([{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "list_picnic_orders", "arguments": {}}}, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}])
        self.assertEqual(reply.status_code, 200)
        self.assertEqual([(e["action"], e["actor"]) for e in self.entries()], [("tools/list", "mcp:ChatGPT"), ("list_picnic_orders", "mcp:ChatGPT")])

    def test_rejected_requests_are_logged(self):
        with mock.patch.object(mcp, "enabled", return_value=(True, "")):
            self.assertEqual(self.post({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, token="").status_code, 401)
            self.assertEqual(self.post({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, token="msa_fout").status_code, 401)
        first, second = reversed(self.entries())
        self.assertEqual((first["status"], first["ok"], first["detail"]["token"].split(" /")[0]), (401, False, "ontbreekt"))
        self.assertEqual(second["detail"]["token"].split(" /")[0], "ongeldig")

    def test_oauth_steps_are_logged_without_secrets(self):
        with mock.patch.object(mcp, "enabled", return_value=(True, "")):
            registered = self.client.post("/oauth/register", json={"client_name": "Claude", "redirect_uris": ["https://claude.ai/cb"]})
            self.assertEqual(registered.status_code, 201)
            self.client.post("/oauth/token", data={"grant_type": "authorization_code", "code": "GEHEIMECODE", "code_verifier": "GEHEIMVERIFIER", "client_id": registered.get_json()["client_id"]})
        token_entry, register_entry = self.entries()
        self.assertEqual((token_entry["kind"], token_entry["service"]), ("oauth", "mcp"))
        self.assertIn("authorization_code", token_entry["action"])
        self.assertEqual(register_entry["actor"], "mcp:Claude")
        self.assertNotIn("GEHEIME", json.dumps([audit.get_entry(e["id"]) for e in self.entries()]))
        self.assertEqual(audit.get_entry(token_entry["id"])["response"], "", "tokens in het antwoord worden niet bewaard")


class ApiTest(AuditTestCase):
    def setUp(self):
        super().setUp()
        self.send("GET", "https://api.ah.nl/a")
        self.send("GET", "https://www.jumbo.com/b", status=500, body=b"nee")
        self.send("GET", "https://storefront-prod.nl.picnicinternational.com/c")

    def get(self, query=""):
        response = self.client.get("/api/audit" + query)
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    def test_newest_first_with_counts_and_settings(self):
        data = self.get()
        self.assertEqual([e["service"] for e in data["entries"]], ["picnic", "jumbo", "ah"])
        self.assertEqual(data["total"], 3)
        self.assertEqual(data["last24h"]["jumbo"], {"total": 1, "errors": 1})
        self.assertEqual(data["settings"]["retentionDays"], audit.DEFAULT_RETENTION_DAYS)
        self.assertNotIn("response", data["entries"][0], "de lijst bevat geen bodies")

    def test_filters(self):
        self.assertEqual([e["service"] for e in self.get("?service=ah")["entries"]], ["ah"])
        self.assertEqual([e["service"] for e in self.get("?status=error")["entries"]], ["jumbo"])
        self.assertEqual([e["service"] for e in self.get("?q=/c")["entries"]], ["picnic"])
        self.assertEqual(self.get("?service=mcp")["entries"], [])
        self.assertEqual(len(self.get("?q=%25")["entries"]), 0, "% in de zoekterm is letterlijk")

    def test_paging(self):
        first = self.get("?limit=2")
        self.assertEqual(len(first["entries"]), 2)
        self.assertIsNotNone(first["next"])
        second = self.get(f"?limit=2&before={first['next']}")
        self.assertEqual([e["service"] for e in second["entries"]], ["ah"])
        self.assertIsNone(second["next"])

    def test_detail_and_missing_entry(self):
        entry = self.get()["entries"][1]
        detail = self.client.get(f"/api/audit/{entry['id']}").get_json()
        self.assertEqual(detail["response"], "nee")
        self.assertEqual(self.client.get("/api/audit/99999").status_code, 404)

    def test_bad_numbers_are_rejected(self):
        self.assertEqual(self.client.get("/api/audit?limit=veel").status_code, 400)

    def test_clearing_leaves_a_trace(self):
        response = self.client.delete("/api/audit")
        self.assertEqual(response.get_json()["removed"], 3)
        (entry,) = self.get()["entries"]
        self.assertEqual((entry["kind"], entry["action"]), ("audit", "log cleared"))
        self.assertEqual(entry["detail"]["removed"], 3)

    def test_only_the_admin_may_read_it(self):
        self.assertTrue(auth.admin_only("/api/audit"))
        self.assertTrue(auth.admin_only("/api/audit/12"))


class ExportTest(AuditTestCase):
    def setUp(self):
        super().setUp()
        self.send("GET", "https://api.ah.nl/a", body=json.dumps({"naam": "café"}))
        self.send("POST", "https://www.jumbo.com/api/graphql", status=500, body=b"=HYPERLINK(1)", json={"operationName": "=Boom", "token": "GEHEIM"})

    def test_json_export_has_bodies_and_respects_filters(self):
        response = self.client.get("/api/audit/export?format=json&status=error")
        self.assertIn("attachment; filename=\"mealieshopper-audit-", response.headers["Content-Disposition"])
        self.assertTrue(response.headers["Content-Disposition"].endswith('.json"'))
        data = response.get_json()
        (entry,) = data["entries"]
        self.assertEqual(entry["service"], "jumbo")
        self.assertIn("request", entry)
        self.assertNotIn("GEHEIM", json.dumps(data))
        everything = self.client.get("/api/audit/export").get_json()["entries"]
        self.assertEqual([e["service"] for e in everything], ["ah", "jumbo", "audit"], "oudste eerst; de eerste export staat er al in")

    def test_csv_export_is_readable_and_formula_safe(self):
        import csv, io

        response = self.client.get("/api/audit/export?format=csv")
        self.assertEqual(response.mimetype, "text/csv")
        text = response.get_data(as_text=True)
        self.assertTrue(text.startswith("\ufeff"))
        rows = list(csv.DictReader(io.StringIO(text.lstrip("\ufeff"))))
        self.assertEqual([r["service"] for r in rows], ["ah", "jumbo"])
        self.assertIn("café", rows[0]["response"])
        self.assertEqual(rows[1]["response"], "'=HYPERLINK(1)")
        self.assertEqual(rows[1]["action"], "GraphQL =Boom", "alleen een cel die ermee begint wordt beschermd")

    def test_an_export_leaves_a_trace(self):
        self.client.get("/api/audit/export")
        newest = self.client.get("/api/audit").get_json()["entries"][0]
        self.assertEqual((newest["kind"], newest["action"], newest["detail"]["entries"]), ("audit", "log exported", 2))


class RetentionTest(AuditTestCase):
    def test_old_entries_and_excess_rows_are_pruned(self):
        old = (auth.utcnow() - audit.timedelta(days=45)).isoformat()
        audit.record(kind="outbound", service="ah", action="oud")
        with audit.closing(audit._connect()) as conn, conn:
            conn.execute("UPDATE audit_log SET ts = ?", (old,))
        with mock.patch.object(audit, "PRUNE_EVERY", 1):
            audit.record(kind="outbound", service="ah", action="nieuw")
        self.assertEqual([e["action"] for e in self.entries()], ["nieuw"])

    def test_zero_days_keeps_everything(self):
        os.environ["MEALIESHOPPER_AUDIT_RETENTION_DAYS"] = "0"
        audit.record(kind="outbound", service="ah", action="oud")
        with audit.closing(audit._connect()) as conn, conn:
            conn.execute("UPDATE audit_log SET ts = '2000-01-01T00:00:00'")
        with mock.patch.object(audit, "PRUNE_EVERY", 1):
            audit.record(kind="outbound", service="ah", action="nieuw")
        self.assertEqual(len(self.entries()), 2)

    def test_the_row_cap_is_enforced(self):
        with mock.patch.object(audit, "PRUNE_EVERY", 1), mock.patch.object(audit, "MAX_ROWS", 3):
            for number in range(6):
                audit.record(kind="outbound", service="ah", action=f"r{number}")
        self.assertEqual([e["action"] for e in self.entries()], ["r5", "r4", "r3"])


class PropagateTest(AuditTestCase):
    def test_pool_threads_inherit_the_caller(self):
        from concurrent.futures import ThreadPoolExecutor

        def work(_):
            self.send("GET", "https://www.jumbo.com/api/graphql")

        token = audit._actor.set("mcp:Claude")
        corr = audit._corr.set("abc123")
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(audit.propagate(work), range(3)))
        finally:
            audit._actor.reset(token)
            audit._corr.reset(corr)
        self.assertEqual({(e["actor"], e["corr"]) for e in self.entries()}, {("mcp:Claude", "abc123")})


if __name__ == "__main__":
    unittest.main()
