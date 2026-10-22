import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

import requests  # noqa: E402

from mealieshopper import auth_password, updates  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
JS = (ROOT / "static" / "updates.js").read_text(encoding="utf-8")
PASSWORD = "koffie-met-melk-42"
ANNA = "een-heel-ander-wachtwoord"


def release(tag="v9.9.9"):
    """Het antwoord van github.com/<repo>/releases/latest: een doorverwijzing naar de tag."""
    return mock.Mock(
        status_code=302,
        headers={"Location": f"https://github.com/helmerzNL/MealieShopper/releases/tag/{tag}"},
    )


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


class StatusTest(unittest.TestCase):
    def setUp(self):
        updates.reset()
        self.addCleanup(updates.reset)
        env = mock.patch.dict(os.environ, {"MEALIESHOPPER_VERSION": "1.0.0"})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("MEALIESHOPPER_UPDATE_CHECK", None)
        from mealieshopper import about

        about.build_info.cache_clear()
        self.addCleanup(about.build_info.cache_clear)

    def test_new_release_is_reported(self):
        with mock.patch.object(updates.requests, "get", return_value=release("v9.9.9")) as get:
            result = updates.status()
        self.assertTrue(result["available"])
        self.assertEqual((result["current"], result["latest"]), ("1.0.0", "9.9.9"))
        self.assertFalse(result["error"])
        self.assertTrue(result["url"].startswith("https://github.com/helmerzNL/MealieShopper/"))
        self.assertEqual(get.call_args.args[0], "https://github.com/helmerzNL/MealieShopper/releases/latest")
        self.assertFalse(get.call_args.kwargs["allow_redirects"])
        self.assertNotIn("api.github.com", get.call_args.args[0], "de API heeft een lage limiet voor anonieme aanvragen")

    def test_same_version_is_not_an_update(self):
        with mock.patch.object(updates.requests, "get", return_value=release("v1.0.0")):
            self.assertFalse(updates.status()["available"])

    def test_answer_is_remembered_so_github_is_not_asked_again(self):
        with mock.patch.object(updates.requests, "get", return_value=release()) as get:
            updates.status()
            updates.status()
        self.assertEqual(get.call_count, 1)

    def test_check_now_asks_again_but_not_within_the_minimum_gap(self):
        with mock.patch.object(updates.requests, "get", return_value=release()) as get:
            updates.status()
            updates.status(force=True)
            self.assertEqual(get.call_count, 1, "binnen een paar seconden: geen tweede aanvraag")
            with mock.patch.object(updates.time, "time", return_value=updates.time.time() + updates.MIN_FORCE_GAP + 1):
                updates.status(force=True)
        self.assertEqual(get.call_count, 2)

    def test_unreachable_github_is_an_error_not_a_crash(self):
        with mock.patch.object(updates.requests, "get", side_effect=requests.ConnectionError("geen netwerk")):
            result = updates.status()
        self.assertTrue(result["error"])
        self.assertFalse(result["available"])

    def test_the_reason_is_reported_and_logged(self):
        denied = mock.Mock(status_code=403)
        denied.raise_for_status.side_effect = requests.HTTPError("403", response=denied)
        cases = [
            (denied, None, "HTTP 403 (rate limit)"),
            (None, requests.ConnectionError("dns"), "ConnectionError"),
            (None, requests.Timeout(), "Timeout"),
            (None, requests.exceptions.SSLError("cert"), "SSLError"),
        ]
        for response, error, expected in cases:
            updates.reset()
            with mock.patch.object(updates.requests, "get", return_value=response, side_effect=error), \
                    self.assertLogs("mealieshopper.updates", "WARNING") as logged:
                result = updates.status()
            self.assertTrue(result["error"])
            self.assertIn(expected, result["errorDetail"])
            self.assertIn(expected, logged.output[0])

    def test_a_failure_keeps_the_last_good_answer(self):
        with mock.patch.object(updates.requests, "get", return_value=release()):
            updates.status()
        with mock.patch.object(updates.requests, "get", side_effect=requests.Timeout()), \
                mock.patch.object(updates.time, "time", return_value=updates.time.time() + updates.SUCCESS_TTL + 1):
            result = updates.status()
        self.assertTrue(result["error"])
        self.assertTrue(result["available"], "de eerder gevonden update blijft zichtbaar")

    def test_unexpected_answer_is_an_error(self):
        for bad in (
            mock.Mock(status_code=302, headers={"Location": "https://github.com/helmerzNL/MealieShopper/releases/tag/geen-versie"}),
            mock.Mock(status_code=302, headers={}),
            mock.Mock(status_code=200, headers={}),
        ):
            updates.reset()
            with mock.patch.object(updates.requests, "get", return_value=bad):
                self.assertTrue(updates.status()["error"])

    def test_no_release_yet_is_not_an_error(self):
        for none in (
            mock.Mock(status_code=404, headers={}),
            mock.Mock(status_code=302, headers={"Location": "https://github.com/helmerzNL/MealieShopper/releases"}),
        ):
            updates.reset()
            with mock.patch.object(updates.requests, "get", return_value=none):
                result = updates.status()
            self.assertFalse(result["error"])
            self.assertFalse(result["available"])
            self.assertEqual(result["latest"], "")

    def test_link_always_points_at_the_repository(self):
        odd = release()
        odd.headers["Location"] = "https://evil.example/helmerzNL/MealieShopper/releases/tag/v9.9.9"
        with mock.patch.object(updates.requests, "get", return_value=odd):
            self.assertEqual(updates.status()["url"], "https://github.com/helmerzNL/MealieShopper/releases/tag/v9.9.9")

    def test_can_be_turned_off(self):
        with mock.patch.dict(os.environ, {"MEALIESHOPPER_UPDATE_CHECK": "false"}), \
                mock.patch.object(updates.requests, "get") as get:
            result = updates.status()
        self.assertFalse(result["enabled"])
        self.assertFalse(result["available"])
        get.assert_not_called()


class RouteTest(unittest.TestCase):
    def setUp(self):
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
        self.admin = self.app.test_client()
        self.admin.post("/api/auth/password", json={"username": "admin", "password": PASSWORD})

    def test_owner_gets_the_status_and_can_check_now(self):
        with mock.patch.object(updates.requests, "get", return_value=release()):
            data = self.admin.get("/api/update").get_json()
            self.assertEqual(self.admin.post("/api/update/check").status_code, 200)
        self.assertTrue(data["available"])
        self.assertEqual(data["latest"], "9.9.9")

    def test_only_the_owner_and_only_when_logged_in(self):
        self.assertEqual(self.app.test_client().get("/api/update").status_code, 401)
        token = self.admin.post("/api/users/invites", json={"username": "anna"}).get_json()["token"]
        anna = self.app.test_client()
        anna.post("/api/invites/password", json={"token": token, "password": ANNA})
        self.assertEqual(anna.get("/api/update").status_code, 403)
        self.assertEqual(anna.post("/api/update/check").status_code, 403)


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
