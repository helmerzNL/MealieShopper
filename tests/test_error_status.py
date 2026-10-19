import os
import tempfile
import unittest
from unittest import mock

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

from mealieshopper import ah, jumbo, mealie  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402


class ExternalErrorStatusTest(unittest.TestCase):
    """Externe fouten gaan als 424 met JSON, zodat een reverse proxy ze niet vervangt."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"MEALIESHOPPER_DATA_DIR": tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = create_app().test_client()

    def assert_424(self, response, message):
        self.assertEqual(response.status_code, 424)
        self.assertTrue(response.is_json)
        self.assertEqual(response.get_json()["error"], message)

    def test_ah_saved_recipes(self):
        with mock.patch.object(ah, "get_saved_recipes", side_effect=RuntimeError("AH sessie verlopen")):
            self.assert_424(self.client.get("/api/ah/recipes/saved"), "AH sessie verlopen")

    def test_ah_recipe_search(self):
        with mock.patch.object(ah, "search_recipes", side_effect=RuntimeError("AH zoeken mislukt (500)")):
            self.assert_424(self.client.get("/api/ah/search?q=soep"), "AH zoeken mislukt (500)")

    def test_mealplan(self):
        with mock.patch.object(mealie, "get_meal_plan_with_recipes", side_effect=RuntimeError("Mealie onbereikbaar")):
            self.assert_424(
                self.client.get("/api/mealplan?start=2026-10-19&end=2026-10-25"), "Mealie onbereikbaar"
            )

    def test_jumbo_saved_recipes(self):
        with mock.patch.object(jumbo, "get_saved_recipes", side_effect=RuntimeError("Jumbo sessie verlopen")):
            self.assert_424(self.client.get("/api/jumbo/recipes/saved"), "Jumbo sessie verlopen")


if __name__ == "__main__":
    unittest.main()
