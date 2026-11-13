import os
import tempfile
import unittest
from unittest import mock

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

from helpers import AppTestCase  # noqa: E402

from mealieshopper import auth_password, mealie, server_settings  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402

PASSWORD = "koffie-met-melk-42"
ANNA = "een-heel-ander-wachtwoord"


class ServerSettingsTestCase(AppTestCase):
    async def asyncSetUp(self):
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
        self.admin = self.client_for(self.app)
        await self.admin.post("/api/auth/password", json={"username": "admin", "password": PASSWORD})

    async def put(self, body, client=None):
        return await (client or self.admin).put("/api/server-settings", json=body)

    async def get(self):
        return (await self.admin.get("/api/server-settings")).get_json()

    async def join(self):
        token = (await self.admin.post("/api/users/invites", json={"username": "anna"})).get_json()["token"]
        client = self.client_for(self.app)
        await client.post("/api/invites/password", json={"token": token, "password": ANNA})
        return client


class OwnerOnlyTest(ServerSettingsTestCase):
    async def test_only_the_owner_can_read_or_change_the_settings(self):
        anna = await self.join()
        self.assertEqual((await anna.get("/api/server-settings")).status_code, 403)
        self.assertEqual((await self.put({"mcpEnabled": False}, anna)).status_code, 403)
        self.assertEqual((await self.put({"publicBaseUrl": "https://evil.example.com"}, anna)).status_code, 403)
        anonymous = self.client_for(self.app)
        self.assertEqual((await anonymous.get("/api/server-settings")).status_code, 401)
        self.assertEqual((await self.put({"mcpEnabled": False}, anonymous)).status_code, 401)
        self.assertEqual((await self.get())["mcpEnabled"], True)  # niets veranderd

    async def test_users_see_in_the_status_that_they_cannot_configure(self):
        anna = await self.join()
        self.assertTrue((await self.admin.get("/api/mcp/status")).get_json()["canConfigure"])
        self.assertFalse((await anna.get("/api/mcp/status")).get_json()["canConfigure"])


class PublicBaseUrlTest(ServerSettingsTestCase):
    async def test_detected_automatically_by_default(self):
        data = await self.get()
        self.assertEqual((data["publicBaseUrl"], data["publicBaseUrlSource"]), ("http://localhost", "auto"))

    async def test_proxy_headers_are_used_when_detecting(self):
        data = (
            await self.admin.get("/api/server-settings", headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "shop.example.com"})
        ).get_json()
        self.assertEqual((data["publicBaseUrl"], data["detectedBaseUrl"]), ("https://shop.example.com", "https://shop.example.com"))

    async def test_environment_beats_detection_and_the_setting_beats_both(self):
        with mock.patch.dict(os.environ, {"MEALIESHOPPER_PUBLIC_BASE_URL": "https://env.example.com/"}):
            data = await self.get()
            self.assertEqual(
                (data["publicBaseUrl"], data["publicBaseUrlSource"], data["envBaseUrl"]),
                ("https://env.example.com", "env", "https://env.example.com"),
            )
            saved = (await self.put({"publicBaseUrl": "https://Mine.Example.com:8443/"})).get_json()
            self.assertEqual(
                (saved["publicBaseUrl"], saved["publicBaseUrlSource"], saved["publicBaseUrlOverride"]),
                ("https://mine.example.com:8443", "setting", "https://mine.example.com:8443"),
            )
            back = (await self.put({"publicBaseUrl": ""})).get_json()
            self.assertEqual((back["publicBaseUrl"], back["publicBaseUrlSource"]), ("https://env.example.com", "env"))

    async def test_clearing_goes_back_to_automatic(self):
        await self.put({"publicBaseUrl": "https://mine.example.com"})
        for empty in ("", "   ", None):
            await self.put({"publicBaseUrl": "https://mine.example.com"})
            self.assertEqual((await self.put({"publicBaseUrl": empty})).get_json()["publicBaseUrlSource"], "auto", repr(empty))

    async def test_invalid_addresses_are_refused(self):
        for bad in (
            "mealieshopper.example.com",
            "ftp://x.example.com",
            "javascript:alert(1)",
            "https://",
            "https://x.example.com/pad",
            "https://x.example.com/?q=1",
            "https://user:pw@x.example.com",
            "https://x.example.com#frag",
            "http://x.example.com:99999",
            "//x.example.com",
            12,
        ):
            response = await self.put({"publicBaseUrl": bad})
            self.assertEqual(response.status_code, 400, bad)
            self.assertIn("error", response.get_json())
        self.assertEqual((await self.get())["publicBaseUrlSource"], "auto")

    async def test_the_setting_is_used_everywhere(self):
        await self.put({"publicBaseUrl": "https://shop.example.com"})
        client = self.client_for(self.app)
        self.assertEqual((await client.get("/.well-known/oauth-authorization-server")).get_json()["issuer"], "https://shop.example.com")
        self.assertEqual((await client.get("/.well-known/oauth-protected-resource")).get_json()["resource"], "https://shop.example.com/mcp")
        self.assertEqual((await self.admin.get("/api/mcp/status")).get_json()["url"], "https://shop.example.com/mcp")
        invite = (await self.admin.post("/api/users/invites", json={})).get_json()
        self.assertTrue(invite["url"].startswith("https://shop.example.com/#invite="))
        self.assertIn("https://shop.example.com/static/brand/social-card.png", (await client.get("/")).get_data(as_text=True))


class McpSwitchTest(ServerSettingsTestCase):
    async def test_the_switch_turns_the_server_off_and_on(self):
        client = self.client_for(self.app)
        self.assertEqual((await client.get("/.well-known/oauth-authorization-server")).status_code, 200)
        off = (await self.put({"mcpEnabled": False})).get_json()
        self.assertEqual((off["mcpEnabled"], off["mcpSource"]), (False, "setting"))
        self.assertEqual((await client.get("/.well-known/oauth-authorization-server")).status_code, 404)
        self.assertEqual((await client.post("/mcp", json={})).status_code, 404)
        status = (await self.admin.get("/api/mcp/status")).get_json()
        self.assertEqual((status["enabled"], status["reason"]), (False, "disabled"))
        await self.put({"mcpEnabled": True})
        self.assertEqual((await client.get("/.well-known/oauth-authorization-server")).status_code, 200)

    async def test_the_switch_beats_the_environment(self):
        with mock.patch.dict(os.environ, {"MEALIESHOPPER_MCP": "false"}):
            data = await self.get()
            self.assertEqual((data["mcpEnabled"], data["mcpSource"]), (False, "default"))
            self.assertEqual((await self.put({"mcpEnabled": True})).get_json()["mcpEnabled"], True)
            self.assertEqual((await self.client_for(self.app).get("/.well-known/oauth-authorization-server")).status_code, 200)

    async def test_bad_input(self):
        for body in ({}, {"other": 1}, {"mcpEnabled": "nee"}, {"mcpEnabled": 1}, []):
            self.assertEqual((await self.put(body)).status_code, 400, body)
        self.assertEqual((await self.admin.put("/api/server-settings", data="x")).status_code, 400)

    async def test_login_off_still_reports_why(self):
        self.assertTrue((await self.get())["loginOn"])


if __name__ == "__main__":
    unittest.main()


class McpToolSwitchTest(ServerSettingsTestCase):
    async def names(self):
        return {tool["name"]: tool["enabled"] for tool in (await self.get())["mcpTools"]}

    async def test_every_tool_is_on_by_default_and_can_be_switched_off_and_on(self):
        self.assertTrue(all((await self.names()).values()))
        self.assertIn("get_week_menu", await self.names())
        self.assertEqual((await self.put({"mcpTools": {"get_week_menu": False}})).status_code, 200)
        self.assertEqual([name for name, on in (await self.names()).items() if not on], ["get_week_menu"])
        await self.put({"mcpTools": {"get_week_menu": True}})
        self.assertTrue(all((await self.names()).values()))

    async def test_a_partial_change_keeps_the_rest(self):
        await self.put({"mcpTools": {"get_week_menu": False}})
        await self.put({"mcpTools": {"list_ah_lists": False}})
        self.assertEqual(sorted(name for name, on in (await self.names()).items() if not on), ["get_week_menu", "list_ah_lists"])

    async def test_bad_input(self):
        for body in (
            {"mcpTools": {"get_week_menu": "nee"}},
            {"mcpTools": {"get_week_menu": 0}},
            {"mcpTools": []},
            {"mcpTools": {"bestaat_niet": False}},
        ):
            self.assertEqual((await self.put(body)).status_code, 400, body)
        self.assertTrue(all((await self.names()).values()))

    async def test_unknown_names_are_not_stored_when_part_of_the_request_is_bad(self):
        await self.put({"mcpTools": {"get_week_menu": False, "bestaat_niet": False}})
        self.assertTrue(all((await self.names()).values()))

    async def test_only_the_owner_can_switch_tools(self):
        anna = await self.join()
        self.assertEqual((await self.put({"mcpTools": {"get_week_menu": False}}, anna)).status_code, 403)
        self.assertTrue(all((await self.names()).values()))


class WeekStartSettingTest(ServerSettingsTestCase):
    """De eerste dag van de week: standaard zoals in Mealie (leeg), of een vaste dag van 0 (zondag) t/m 6 (zaterdag)."""

    async def test_follows_mealie_by_default(self):
        self.assertEqual((await self.get())["weekStart"], "")
        self.assertEqual((await self.admin.get("/api/mealie/status")).get_json()["weekStart"]["source"], "default")

    async def test_the_owner_picks_a_fixed_day_or_goes_back_to_mealie(self):
        for day in (6, 0, 1):
            data = (await self.put({"weekStart": day})).get_json()
            self.assertEqual(data["weekStart"], str(day))
            status = (await self.admin.get("/api/mealie/status")).get_json()["weekStart"]
            self.assertEqual((status["day"], status["source"]), (day, "setting"))
        for empty in (None, ""):
            await self.put({"weekStart": 6})
            self.assertEqual((await self.put({"weekStart": empty})).get_json()["weekStart"], "", repr(empty))
        self.assertEqual((await self.admin.get("/api/mealie/status")).get_json()["weekStart"]["source"], "default")

    async def test_bad_input(self):
        await self.put({"weekStart": 6})
        for value in (True, False, 7, -1, "ma", "6", 1.5, [1], {"day": 1}):
            self.assertEqual((await self.put({"weekStart": value})).status_code, 400, repr(value))
        self.assertEqual((await self.get())["weekStart"], "6", "niets veranderd")
        self.assertIn("weekStart", (await self.put({"weekStart": "zondag"})).get_json()["error"])

    async def test_only_the_owner_chooses_but_everyone_gets_the_week(self):
        anna = await self.join()
        self.assertEqual((await self.put({"weekStart": 6}, anna)).status_code, 403)
        self.assertEqual((await self.get())["weekStart"], "")
        await self.put({"weekStart": 6})
        week = await anna.get("/api/mealie/status")
        self.assertEqual(week.status_code, 200)
        self.assertEqual(week.get_json()["weekStart"]["day"], 6, "het weekmenu van een gebruiker begint op dezelfde dag")
        self.assertEqual(await server_settings.auth.get_setting(mealie.SETTING_WEEK_START), "6")
