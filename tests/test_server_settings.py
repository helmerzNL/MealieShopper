import os
import tempfile
import unittest
from unittest import mock

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

from mealieshopper import auth_password, server_settings  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402

PASSWORD = "koffie-met-melk-42"
ANNA = "een-heel-ander-wachtwoord"


class ServerSettingsTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(
            os.environ,
            {"PASSKEY_AUTH_ENABLED": "true", "MEALIESHOPPER_DATA_DIR": tmp.name, "MEALIESHOPPER_AUTH_SECRET": "test-secret"},
        )
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("MEALIESHOPPER_PUBLIC_BASE_URL", None)
        os.environ.pop("MEALIESHOPPER_MCP", None)
        cost = mock.patch.object(auth_password, "SCRYPT_N", 2**4)
        cost.start()
        self.addCleanup(cost.stop)
        self.app = create_app()
        self.admin = self.app.test_client()
        self.admin.post("/api/auth/password", json={"username": "admin", "password": PASSWORD})

    def put(self, body, client=None):
        return (client or self.admin).put("/api/server-settings", json=body)

    def get(self):
        return self.admin.get("/api/server-settings").get_json()

    def join(self):
        token = self.admin.post("/api/users/invites", json={"username": "anna"}).get_json()["token"]
        client = self.app.test_client()
        client.post("/api/invites/password", json={"token": token, "password": ANNA})
        return client


class OwnerOnlyTest(ServerSettingsTestCase):
    def test_only_the_owner_can_read_or_change_the_settings(self):
        anna = self.join()
        self.assertEqual(anna.get("/api/server-settings").status_code, 403)
        self.assertEqual(self.put({"mcpEnabled": False}, anna).status_code, 403)
        self.assertEqual(self.put({"publicBaseUrl": "https://evil.example.com"}, anna).status_code, 403)
        anonymous = self.app.test_client()
        self.assertEqual(anonymous.get("/api/server-settings").status_code, 401)
        self.assertEqual(self.put({"mcpEnabled": False}, anonymous).status_code, 401)
        self.assertEqual(self.get()["mcpEnabled"], True)  # niets veranderd

    def test_users_see_in_the_status_that_they_cannot_configure(self):
        anna = self.join()
        self.assertTrue(self.admin.get("/api/mcp/status").get_json()["canConfigure"])
        self.assertFalse(anna.get("/api/mcp/status").get_json()["canConfigure"])


class PublicBaseUrlTest(ServerSettingsTestCase):
    def test_detected_automatically_by_default(self):
        data = self.get()
        self.assertEqual((data["publicBaseUrl"], data["publicBaseUrlSource"]), ("http://localhost", "auto"))

    def test_proxy_headers_are_used_when_detecting(self):
        data = self.admin.get("/api/server-settings", headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "shop.example.com"}).get_json()
        self.assertEqual((data["publicBaseUrl"], data["detectedBaseUrl"]), ("https://shop.example.com", "https://shop.example.com"))

    def test_environment_beats_detection_and_the_setting_beats_both(self):
        with mock.patch.dict(os.environ, {"MEALIESHOPPER_PUBLIC_BASE_URL": "https://env.example.com/"}):
            data = self.get()
            self.assertEqual((data["publicBaseUrl"], data["publicBaseUrlSource"], data["envBaseUrl"]), ("https://env.example.com", "env", "https://env.example.com"))
            saved = self.put({"publicBaseUrl": "https://Mine.Example.com:8443/"}).get_json()
            self.assertEqual((saved["publicBaseUrl"], saved["publicBaseUrlSource"], saved["publicBaseUrlOverride"]), ("https://mine.example.com:8443", "setting", "https://mine.example.com:8443"))
            back = self.put({"publicBaseUrl": ""}).get_json()
            self.assertEqual((back["publicBaseUrl"], back["publicBaseUrlSource"]), ("https://env.example.com", "env"))

    def test_clearing_goes_back_to_automatic(self):
        self.put({"publicBaseUrl": "https://mine.example.com"})
        for empty in ("", "   ", None):
            self.put({"publicBaseUrl": "https://mine.example.com"})
            self.assertEqual(self.put({"publicBaseUrl": empty}).get_json()["publicBaseUrlSource"], "auto", repr(empty))

    def test_invalid_addresses_are_refused(self):
        for bad in ("mealieshopper.example.com", "ftp://x.example.com", "javascript:alert(1)", "https://", "https://x.example.com/pad", "https://x.example.com/?q=1",
                    "https://user:pw@x.example.com", "https://x.example.com#frag", "http://x.example.com:99999", "//x.example.com", 12):
            response = self.put({"publicBaseUrl": bad})
            self.assertEqual(response.status_code, 400, bad)
            self.assertIn("error", response.get_json())
        self.assertEqual(self.get()["publicBaseUrlSource"], "auto")

    def test_the_setting_is_used_everywhere(self):
        self.put({"publicBaseUrl": "https://shop.example.com"})
        client = self.app.test_client()
        self.assertEqual(client.get("/.well-known/oauth-authorization-server").get_json()["issuer"], "https://shop.example.com")
        self.assertEqual(client.get("/.well-known/oauth-protected-resource").get_json()["resource"], "https://shop.example.com/mcp")
        self.assertEqual(self.admin.get("/api/mcp/status").get_json()["url"], "https://shop.example.com/mcp")
        invite = self.admin.post("/api/users/invites", json={}).get_json()
        self.assertTrue(invite["url"].startswith("https://shop.example.com/#invite="))
        self.assertIn("https://shop.example.com/static/brand/social-card.png", client.get("/").get_data(as_text=True))


class McpSwitchTest(ServerSettingsTestCase):
    def test_the_switch_turns_the_server_off_and_on(self):
        client = self.app.test_client()
        self.assertEqual(client.get("/.well-known/oauth-authorization-server").status_code, 200)
        off = self.put({"mcpEnabled": False}).get_json()
        self.assertEqual((off["mcpEnabled"], off["mcpSource"]), (False, "setting"))
        self.assertEqual(client.get("/.well-known/oauth-authorization-server").status_code, 404)
        self.assertEqual(client.post("/mcp", json={}).status_code, 404)
        status = self.admin.get("/api/mcp/status").get_json()
        self.assertEqual((status["enabled"], status["reason"]), (False, "disabled"))
        self.put({"mcpEnabled": True})
        self.assertEqual(client.get("/.well-known/oauth-authorization-server").status_code, 200)

    def test_the_switch_beats_the_environment(self):
        with mock.patch.dict(os.environ, {"MEALIESHOPPER_MCP": "false"}):
            data = self.get()
            self.assertEqual((data["mcpEnabled"], data["mcpSource"]), (False, "default"))
            self.assertEqual(self.put({"mcpEnabled": True}).get_json()["mcpEnabled"], True)
            self.assertEqual(self.app.test_client().get("/.well-known/oauth-authorization-server").status_code, 200)

    def test_bad_input(self):
        for body in ({}, {"other": 1}, {"mcpEnabled": "nee"}, {"mcpEnabled": 1}, []):
            self.assertEqual(self.put(body).status_code, 400, body)
        self.assertEqual(self.admin.put("/api/server-settings", data="x").status_code, 400)

    def test_login_off_still_reports_why(self):
        self.assertTrue(self.get()["loginOn"])


if __name__ == "__main__":
    unittest.main()
