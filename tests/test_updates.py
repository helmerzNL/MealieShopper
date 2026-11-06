import asyncio
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

import httpx  # noqa: E402

from mealieshopper import about, auth_password, http_client, updates  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402
from helpers import AppTestCase, routes, sent_timeout  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
JS = (ROOT / "static" / "updates.js").read_text(encoding="utf-8")
PASSWORD = "koffie-met-melk-42"
ANNA = "een-heel-ander-wachtwoord"


LATEST = "https://github.com/helmerzNL/MealieShopper/releases/latest"


def release(tag="v9.9.9"):
    """Het antwoord van github.com/<repo>/releases/latest: een doorverwijzing naar de tag."""
    return httpx.Response(302, headers={"Location": f"https://github.com/helmerzNL/MealieShopper/releases/tag/{tag}"})


class VersionCompareTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(updates.parse_version("v1.12.0"), (1, 12, 0))
        self.assertEqual(updates.parse_version("1.2"), (1, 2))
        self.assertEqual(updates.parse_version("onzin"), ())

    def test_newer_is_numeric_not_alphabetical(self):
        self.assertTrue(updates.is_newer("v1.13.0", "1.12.0"))
        self.assertTrue(updates.is_newer("1.10.0", "1.9.9"))
        self.assertTrue(updates.is_newer("2.0", "1.99.9"))
        self.assertFalse(updates.is_newer("1.12.0", "1.12.0"))
        self.assertFalse(updates.is_newer("1.2.0", "1.12.0"))
        self.assertFalse(updates.is_newer("", "1.0.0"))
        self.assertFalse(updates.is_newer("1.0.0", ""))


class StatusTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        updates.reset()
        self.addCleanup(updates.reset)
        env = mock.patch.dict(os.environ, {"MEALIESHOPPER_VERSION": "1.0.0"})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("MEALIESHOPPER_UPDATE_CHECK", None)
        about.reset()
        self.addCleanup(about.reset)
        self.github = self.enterContext(routes())
        self.route = self.github.get(LATEST)

    async def asyncTearDown(self):
        await http_client.aclose_all()

    async def test_new_release_is_reported(self):
        self.route.mock(return_value=release("v9.9.9"))
        result = await updates.status()
        self.assertTrue(result["available"])
        self.assertEqual((result["current"], result["latest"]), ("1.0.0", "9.9.9"))
        self.assertFalse(result["error"])
        self.assertTrue(result["url"].startswith("https://github.com/helmerzNL/MealieShopper/"))
        request = self.route.calls.last.request
        self.assertEqual(str(request.url), LATEST)
        self.assertEqual(request.headers["User-Agent"], "MealieShopper-update-check")
        self.assertEqual(sent_timeout(self.route.calls.last), updates.TIMEOUT)
        self.assertNotIn("api.github.com", str(request.url), "de API heeft een lage limiet voor anonieme aanvragen")

    async def test_the_redirect_is_not_followed(self):
        self.route.mock(return_value=release("v9.9.9"))
        tag = self.github.get("https://github.com/helmerzNL/MealieShopper/releases/tag/v9.9.9").mock(side_effect=AssertionError("gevolgd"))
        self.assertEqual((await updates.status())["latest"], "9.9.9")
        self.assertFalse(tag.called)

    async def test_same_version_is_not_an_update(self):
        self.route.mock(return_value=release("v1.0.0"))
        self.assertFalse((await updates.status())["available"])

    async def test_answer_is_remembered_so_github_is_not_asked_again(self):
        self.route.mock(return_value=release())
        await updates.status()
        await updates.status()
        self.assertEqual(self.route.call_count, 1)

    async def test_two_checks_at_once_ask_github_once(self):
        self.route.mock(return_value=release())
        first, second = await asyncio.gather(updates.status(), updates.status())
        self.assertEqual(first, second)
        self.assertEqual(self.route.call_count, 1)

    async def test_check_now_asks_again_but_not_within_the_minimum_gap(self):
        self.route.mock(return_value=release())
        await updates.status()
        await updates.status(force=True)
        self.assertEqual(self.route.call_count, 1, "binnen een paar seconden: geen tweede aanvraag")
        with mock.patch.object(updates.time, "time", return_value=updates.time.time() + updates.MIN_FORCE_GAP + 1):
            await updates.status(force=True)
        self.assertEqual(self.route.call_count, 2)

    async def test_unreachable_github_is_an_error_not_a_crash(self):
        self.route.mock(side_effect=httpx.ConnectError("geen netwerk"))
        result = await updates.status()
        self.assertTrue(result["error"])
        self.assertFalse(result["available"])

    async def test_the_reason_is_reported_and_logged(self):
        cases = [
            (httpx.Response(403), "HTTP 403 (rate limit)"),
            (httpx.Response(429), "HTTP 429 (rate limit)"),
            (httpx.Response(500), "HTTP 500"),
            (httpx.ConnectError("dns"), "ConnectionError"),
            (httpx.ReadError("verbinding verbroken"), "ConnectionError"),
            (httpx.ConnectTimeout("te traag"), "Timeout"),
            (httpx.ReadTimeout("te traag"), "Timeout"),
            (httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"), "SSLError"),
        ]
        for answer, expected in cases:
            updates.reset()
            if isinstance(answer, httpx.Response):
                self.route.mock(return_value=answer, side_effect=None)
            else:
                self.route.mock(side_effect=answer)
            with self.subTest(expected=expected), self.assertLogs("mealieshopper.updates", "WARNING") as logged:
                result = await updates.status()
            self.assertTrue(result["error"])
            self.assertIn(expected, result["errorDetail"])
            self.assertIn(expected, logged.output[0])

    async def test_a_failure_keeps_the_last_good_answer(self):
        self.route.mock(return_value=release())
        await updates.status()
        self.route.mock(side_effect=httpx.ReadTimeout("te traag"))
        with mock.patch.object(updates.time, "time", return_value=updates.time.time() + updates.SUCCESS_TTL + 1):
            result = await updates.status()
        self.assertTrue(result["error"])
        self.assertTrue(result["available"], "de eerder gevonden update blijft zichtbaar")

    async def test_unexpected_answer_is_an_error(self):
        for bad in (
            httpx.Response(302, headers={"Location": "https://github.com/helmerzNL/MealieShopper/releases/tag/geen-versie"}),
            httpx.Response(302),
            httpx.Response(200),
        ):
            updates.reset()
            self.route.mock(return_value=bad)
            self.assertTrue((await updates.status())["error"])

    async def test_no_release_yet_is_not_an_error(self):
        for none in (
            httpx.Response(404),
            httpx.Response(302, headers={"Location": "https://github.com/helmerzNL/MealieShopper/releases"}),
        ):
            updates.reset()
            self.route.mock(return_value=none)
            result = await updates.status()
            self.assertFalse(result["error"])
            self.assertFalse(result["available"])
            self.assertEqual(result["latest"], "")

    async def test_link_always_points_at_the_repository(self):
        self.route.mock(return_value=httpx.Response(302, headers={"Location": "https://evil.example/helmerzNL/MealieShopper/releases/tag/v9.9.9"}))
        self.assertEqual((await updates.status())["url"], "https://github.com/helmerzNL/MealieShopper/releases/tag/v9.9.9")

    async def test_can_be_turned_off(self):
        self.route.mock(side_effect=AssertionError("geen verzoek"))
        with mock.patch.dict(os.environ, {"MEALIESHOPPER_UPDATE_CHECK": "false"}):
            result = await updates.status()
        self.assertFalse(result["enabled"])
        self.assertFalse(result["available"])
        self.assertFalse(self.route.called)


class RouteTest(AppTestCase):
    async def asyncSetUp(self):
        updates.reset()
        self.addCleanup(updates.reset)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(
            os.environ,
            {"PASSKEY_AUTH_ENABLED": "true", "MEALIESHOPPER_DATA_DIR": tmp.name, "MEALIESHOPPER_AUTH_SECRET": "x"},
        )
        env.start()
        self.addCleanup(env.stop)
        cost = mock.patch.object(auth_password, "SCRYPT_N", 2**4)
        cost.start()
        self.addCleanup(cost.stop)
        self.app = create_app()
        self.admin = self.client_for(self.app)
        await self.admin.post("/api/auth/password", json={"username": "admin", "password": PASSWORD})

    async def test_owner_gets_the_status_and_can_check_now(self):
        with routes() as github:
            github.get(LATEST).mock(return_value=release())
            data = (await self.admin.get("/api/update")).get_json()
            self.assertEqual((await self.admin.post("/api/update/check")).status_code, 200)
        self.assertTrue(data["available"])
        self.assertEqual(data["latest"], "9.9.9")

    async def test_only_the_owner_and_only_when_logged_in(self):
        self.assertEqual((await self.client_for(self.app).get("/api/update")).status_code, 401)
        token = (await self.admin.post("/api/users/invites", json={"username": "anna"})).get_json()["token"]
        anna = self.client_for(self.app)
        await anna.post("/api/invites/password", json={"token": token, "password": ANNA})
        self.assertEqual((await anna.get("/api/update")).status_code, 403)
        self.assertEqual((await anna.post("/api/update/check")).status_code, 403)


class PageTest(unittest.TestCase):
    def test_settings_has_an_updates_tab_and_the_page_has_a_banner(self):
        self.assertIn('data-subtab="settings-system" data-admin-only', HTML)
        self.assertIn('id="settings-system"', HTML)
        for ident in ("update-banner", "update-dot-tab", "update-dot-subtab", "update-dot-user", "update-dot-settings", "update-check", "update-status"):
            self.assertIn(f'id="{ident}"', HTML)
        link = re.search(r'<a[^>]*id="update-notes"[^>]*>', HTML).group(0)
        self.assertIn('rel="noopener noreferrer"', link)
        self.assertIn("updates.js", HTML)

    def test_banner_is_not_shown_by_the_admin_toggle_and_can_be_dismissed_per_version(self):
        banner = re.search(r'<div[^>]*id="update-banner"[^>]*>', HTML).group(0)
        self.assertNotIn("data-admin-only", banner, "applyRole() zou de melding steeds weer tonen")
        self.assertIn("hidden", banner)
        self.assertIn("rememberUpdateDismissed(updateState.data.latest)", JS)
        self.assertIn("updateDismissedVersion() === data.latest", JS)

    def test_script_starts_after_login_and_redraws_on_a_language_change(self):
        app_js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("startUpdateCheck()", app_js)
        handler = app_js[app_js.index('addEventListener("ms:languagechange"'):]
        self.assertIn("renderUpdate()", handler)


if __name__ == "__main__":
    unittest.main()
