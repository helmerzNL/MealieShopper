"""Inplannen van een recept uit een winkel: eerst kijken of het al in Mealie staat, en Allerhande als gewone winkel."""

import os
import tempfile
import unittest
from unittest import mock

import httpx

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

from mealieshopper import ah, auth, hellofresh, jumbo_recipes, marleyspoon, mealie, picnic_recipes  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402
from support import SyncClient, routes, run, sent_params  # noqa: E402

URL = "https://mealie.example.nl"
HF_KEY = "pasta-pesto-0123456789abcdef01234567"
MS_KEY = "123-pasta-pesto"


RECIPES = f"{URL}/api/recipes"


def response(status=200, body=None):
    return httpx.Response(status, json=body)


class ReuseTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = {"MEALIESHOPPER_DATA_DIR": tmp.name, "MEALIESHOPPER_AUTH_SECRET": "test-secret", "MEALIE_URL": URL, "MEALIE_API_TOKEN": "token"}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        auth._INITIALIZED = False
        self.client = SyncClient(create_app())
        group = mock.patch.object(mealie, "group_slug", new_callable=mock.AsyncMock, return_value="thuis")
        group.start()
        self.addCleanup(group.stop)


class FindBySourceTest(ReuseTestCase):
    def test_allerhande_is_found_by_its_number(self):
        # De link staat in Mealie met of zonder de naam van het recept; het nummer is wat telt.
        with routes() as api:
            get = api.get(RECIPES).mock(return_value=response(body={"items": [{"slug": "pasta-pesto"}]}))
            slug = run(mealie.find_recipe_by_source("https://www.ah.nl/allerhande/recept/R-R1199999/pasta-pesto"))
        self.assertEqual(slug, "pasta-pesto")
        self.assertEqual(sent_params(get.calls.last), {
            "queryFilter": 'org_url LIKE "%/r-r1199999/%" OR org_url LIKE "%/r-r1199999"', "page": "1", "perPage": "1",
        })

    def test_jumbo_is_found_by_its_number(self):
        # De link op een kaart, die van het recept zelf en de kale link /recepten/<nummer>: allemaal hetzelfde recept.
        expected = (
            'org_url LIKE "%jumbo.com/recepten/%-504898" OR org_url LIKE "%jumbo.com/recepten/%-504898/" '
            'OR org_url LIKE "%jumbo.com/recepten/504898" OR org_url LIKE "%jumbo.com/recepten/504898/"'
        )
        for url in ("https://www.jumbo.com/recepten/romige-pasta-504898", "https://www.jumbo.com/recepten/504898"):
            with routes() as api:
                get = api.get(RECIPES).mock(return_value=response(body={"items": [{"slug": "romige-pasta"}]}))
                self.assertEqual(run(mealie.find_recipe_by_source(url)), "romige-pasta")
            self.assertEqual(sent_params(get.calls.last)["queryFilter"], expected, url)

    def test_other_links_are_matched_as_a_whole(self):
        with routes() as api:
            get = api.get(RECIPES).mock(return_value=response(body={"items": []}))
            self.assertIsNone(run(mealie.find_recipe_by_source(hellofresh.source_url(HF_KEY))))
        self.assertEqual(sent_params(get.calls.last)["queryFilter"], f'org_url LIKE "{hellofresh.source_url(HF_KEY)}"')

    def test_best_effort(self):
        # Een link met een aanhalingsteken zou het filter breken: dan niet zoeken, gewoon importeren.
        with routes() as api:
            anything = api.route()
            self.assertIsNone(run(mealie.find_recipe_by_source('https://example.com/"recept')))
            self.assertIsNone(run(mealie.find_recipe_by_source("")))
        self.assertFalse(anything.called)
        # Een oudere Mealie die het filter niet kent, of een Mealie die niet antwoordt: ook dan importeren.
        with routes() as api:
            api.get(RECIPES).mock(return_value=response(400, {"detail": "invalid query string"}))
            self.assertIsNone(run(mealie.find_recipe_by_source("https://example.com/recept")))
        with routes() as api:
            api.get(RECIPES).mock(side_effect=httpx.ConnectError("weg"))
            self.assertIsNone(run(mealie.find_recipe_by_source("https://example.com/recept")))


class ReuseOnImportTest(ReuseTestCase):
    def reused(self, path, source_url, patches=()):
        """Met ?reuse=1 komt het bestaande recept terug en wordt er niets geïmporteerd."""
        with mock.patch.object(mealie, "find_recipe_by_source", new_callable=mock.AsyncMock, return_value="al-in-mealie") as find, \
                mock.patch.object(mealie, "import_via_jsonld", new_callable=mock.AsyncMock, side_effect=AssertionError("niet importeren")), \
                mock.patch.object(mealie, "import_from_url", new_callable=mock.AsyncMock, side_effect=AssertionError("niet importeren")), \
                mock.patch.object(hellofresh, "run_import", side_effect=AssertionError("niet importeren")), \
                mock.patch.object(marleyspoon, "run_import", side_effect=AssertionError("niet importeren")):
            for patch in patches:
                patch.start()
            try:
                answer = self.client.post(f"{path}?reuse=1")
            finally:
                for patch in patches:
                    patch.stop()
        self.assertEqual(answer.status_code, 200, answer.get_data(as_text=True))
        self.assertEqual(answer.get_json(), {"slug": "al-in-mealie", "mealieUrl": f"{URL}/g/thuis/r/al-in-mealie", "reused": True})
        find.assert_awaited_once_with(source_url)

    def test_every_store_looks_in_mealie_first(self):
        jumbo_recipe = {"id": "700001", "url": "https://www.jumbo.com/recepten/pasta-pesto-700001"}
        self.reused("/api/ah/recipes/1199999/import", "https://www.ah.nl/allerhande/recept/R-R1199999")
        self.reused("/api/jumbo/recipes/700001/import", jumbo_recipe["url"],
                    [mock.patch.object(jumbo_recipes, "get_recipe", new_callable=mock.AsyncMock, return_value=jumbo_recipe)])
        self.reused("/api/picnic/recipes/r1/import", picnic_recipes.source_url("r1"))
        self.reused(f"/api/hellofresh/recipes/{HF_KEY}/import", hellofresh.source_url(HF_KEY))
        self.reused(f"/api/marleyspoon/recipes/{MS_KEY}/import", marleyspoon.source_url(MS_KEY))

    def test_without_reuse_nothing_changes(self):
        # Importeer (zonder plannen) importeert altijd, ook als het recept er al staat.
        with mock.patch.object(mealie, "find_recipe_by_source", new_callable=mock.AsyncMock, side_effect=AssertionError("niet zoeken")), \
                mock.patch.object(mealie, "import_from_url", new_callable=mock.AsyncMock, return_value="pasta-pesto-1") as send:
            answer = self.client.post("/api/ah/recipes/1199999/import")
        self.assertEqual(answer.get_json(), {"slug": "pasta-pesto-1", "mealieUrl": f"{URL}/g/thuis/r/pasta-pesto-1"})
        send.assert_awaited_once_with("https://www.ah.nl/allerhande/recept/R-R1199999")

    def test_not_found_imports_after_all(self):
        with mock.patch.object(mealie, "find_recipe_by_source", new_callable=mock.AsyncMock, return_value=None), \
                mock.patch.object(hellofresh, "run_import", return_value={"slug": "nieuw", "mealieUrl": "x"}) as run:
            answer = self.client.post(f"/api/hellofresh/recipes/{HF_KEY}/import?reuse=1")
        self.assertEqual(answer.get_json(), {"slug": "nieuw", "mealieUrl": "x"})
        run.assert_called_once_with(HF_KEY)

    def test_an_invalid_meal_kit_key_is_not_looked_up(self):
        with mock.patch.object(mealie, "find_recipe_by_source", new_callable=mock.AsyncMock, side_effect=AssertionError("niet zoeken")), \
                mock.patch.object(hellofresh, "run_import", side_effect=hellofresh.HelloFreshError("Ongeldig recept")):
            answer = self.client.post("/api/hellofresh/recipes/onzin/import?reuse=1")
        self.assertEqual(answer.status_code, 424)


class AllerhandeRecipeTest(ReuseTestCase):
    LD = {
        "@type": "Recipe", "name": "Pasta pesto", "url": "https://www.ah.nl/allerhande/recept/R-R1199999",
        "description": "Snel en groen.", "image": ["https://static.ah.nl/pasta.jpg"], "recipeYield": "4 personen", "totalTime": "PT25M",
        "recipeIngredient": ["300 g pasta", "1 potje pesto"], "recipeInstructions": [{"@type": "HowToStep", "text": "Kook de pasta."}],
    }

    def test_detail_in_the_shape_of_the_other_stores(self):
        with mock.patch.object(ah, "get_recipe_jsonld", return_value=self.LD) as get:
            answer = self.client.get("/api/ah/recipes/1199999")
        self.assertEqual(answer.status_code, 200)
        get.assert_called_once_with(1199999)
        self.assertEqual(answer.get_json(), {
            "id": "1199999", "name": "Pasta pesto", "description": "Snel en groen.", "image": "https://static.ah.nl/pasta.jpg",
            "portions": 4, "minutes": 25, "url": "https://www.ah.nl/allerhande/recept/R-R1199999",
            "ingredients": [{"line": "300 g pasta"}, {"line": "1 potje pesto"}], "steps": ["Kook de pasta."], "nutrition": [],
        })

    def test_errors_do_not_leak_the_answer_of_ah(self):
        with mock.patch.object(ah, "get_recipe_jsonld", side_effect=RuntimeError("AH zei: geheim")):
            answer = self.client.get("/api/ah/recipes/1199999")
        self.assertEqual(answer.status_code, 424)
        self.assertNotIn("geheim", answer.get_data(as_text=True))
        with mock.patch.object(mealie, "import_from_url", new_callable=mock.AsyncMock, side_effect=RuntimeError("Mealie import mislukt (500): geheim")):
            answer = self.client.post("/api/ah/recipes/1199999/import")
        self.assertEqual(answer.status_code, 424)
        self.assertNotIn("geheim", answer.get_data(as_text=True))

    def test_only_numbers_are_recipes(self):
        with mock.patch.object(ah, "get_recipe_jsonld", side_effect=AssertionError("niet ophalen")):
            self.assertEqual(self.client.get("/api/ah/recipes/abc").status_code, 404)
            self.assertEqual(self.client.post("/api/ah/recipes/abc/import").status_code, 404)
        # De bewaarde recepten houden hun eigen route.
        with mock.patch("mealieshopper.ah_recipes.get", new_callable=mock.AsyncMock, return_value={"recipes": [], "syncedAt": None}):
            self.assertEqual(self.client.get("/api/ah/recipes/saved").status_code, 200)

    def test_import_without_mealie_says_so(self):
        with mock.patch.dict(os.environ, {"MEALIE_URL": "", "MEALIE_API_TOKEN": ""}), \
                mock.patch.object(mealie, "import_from_url", new_callable=mock.AsyncMock, side_effect=AssertionError("niet importeren")):
            answer = self.client.post("/api/ah/recipes/1199999/import")
        self.assertEqual(answer.status_code, 400)
        self.assertIn("niet gekoppeld", answer.get_json()["error"])


if __name__ == "__main__":
    unittest.main()
