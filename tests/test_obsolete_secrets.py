import os
import tempfile
import unittest
from unittest import mock

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

from mealieshopper import auth  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402


class PurgeObsoleteSecretsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(
            os.environ,
            {"MEALIESHOPPER_DATA_DIR": tmp.name, "MEALIESHOPPER_AUTH_SECRET": "test-secret"},
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        auth._INITIALIZED = False

    def seed(self):
        for key in auth.OBSOLETE_SECRET_KEYS:
            auth.set_secret(key, "geheim")
        auth.set_secret("AH_REFRESH_TOKEN", "bewaren")

    def test_removes_obsolete_keys_only(self):
        self.seed()
        self.assertEqual(auth.purge_obsolete_secrets(), len(auth.OBSOLETE_SECRET_KEYS))
        for key in auth.OBSOLETE_SECRET_KEYS:
            self.assertEqual(auth.get_secret(key), "", key)
        self.assertEqual(auth.get_secret("AH_REFRESH_TOKEN"), "bewaren")

    def test_second_run_removes_nothing(self):
        self.seed()
        auth.purge_obsolete_secrets()
        self.assertEqual(auth.purge_obsolete_secrets(), 0)

    def test_app_start_purges(self):
        self.seed()
        create_app()
        self.assertEqual(auth.get_secret("AH_PASSWORD"), "")
        self.assertEqual(auth.get_secret("JUMBO_USERNAME"), "")
        self.assertEqual(auth.get_secret("AH_REFRESH_TOKEN"), "bewaren")

    def test_app_start_survives_unreachable_database(self):
        with mock.patch.object(auth, "purge_obsolete_secrets", side_effect=OSError("read-only")):
            create_app()  # mag niet crashen


class RemovedRoutesTest(unittest.TestCase):
    def test_the_old_jumbo_recipe_and_browser_routes_are_gone(self):
        # De huidige Jumbo-koppeling (GraphQL, geen headless browser) staat onder /api/jumbo/ en is
        # bedoeld (ook /api/jumbo/recipes, de recepten via de GraphQL-API); alleen wat met Chromium en de oude
        # REST-koppeling verdween blijft weg.
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"MEALIESHOPER_DATA_DIR": tmp}):
            client = create_app().test_client()
            for method, path in (
                ("get", "/api/jumbo/browser/status"),
                ("get", "/api/ah/browser/status"),
                ("post", "/api/ah/browser/auth"),
            ):
                self.assertEqual(getattr(client, method)(path).status_code, 404, path)
            page = client.get("/").get_data(as_text=True)
            self.assertNotIn("ah-browser", page)
            self.assertNotIn("Selenium", page)


if __name__ == "__main__":
    unittest.main()
