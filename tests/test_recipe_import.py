import json
import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("MEALIESHOPPER_DATA_DIR", tempfile.mkdtemp())

from mealieshopper import ah, mealie  # noqa: E402

RECIPE = {
    "id": 1234,
    "title": "Stamppot boerenkool",
    "description": "Winters klassiek.",
    "cookTime": 20,
    "ovenTime": 0,
    "waitTime": 5,
    "servings": {"number": 4},
    "images": [{"url": "https://img/1234.jpg"}, {"url": "https://img/1234b.jpg"}],
    "ingredients": [{"text": "800 g aardappelen"}, {"text": " "}, {"text": "500 g boerenkool"}],
    "preparation": {"steps": [{"text": "Kook de aardappelen."}, {"text": "Stamp alles."}]},
}


def graphql_ok(recipe):
    response = mock.Mock(ok=True)
    response.json.return_value = {"data": {"recipe": recipe}}
    return response


def graphql_error(message):
    response = mock.Mock(ok=True)
    response.json.return_value = {"errors": [{"message": message}]}
    return response


class AllerhandeRecipeIdTest(unittest.TestCase):
    def test_urls(self):
        for url in (
            "https://www.ah.nl/allerhande/recept/R-R1234/stamppot-boerenkool",
            "https://www.ah.nl/allerhande/recept/R-R1234",
            "http://ah.nl/allerhande/recept/r-r1234/x?utm=1",
        ):
            self.assertEqual(ah.allerhande_recipe_id(url), 1234, url)

    def test_other_urls(self):
        for url in ("https://example.com/recept/1", "https://www.ah.nl/producten/product/wi1", "", None):
            self.assertIsNone(ah.allerhande_recipe_id(url), url)


class RecipeJsonLdTest(unittest.TestCase):
    def build(self, responses):
        with mock.patch.object(ah, "get_token", return_value="anon"), \
                mock.patch.object(ah.requests, "post", side_effect=responses) as post:
            return ah.get_recipe_jsonld(1234), post

    def test_schema_org_recipe(self):
        ld, post = self.build([graphql_ok(RECIPE)])
        self.assertEqual(ld["@type"], "Recipe")
        self.assertEqual(ld["name"], "Stamppot boerenkool")
        self.assertEqual(ld["url"], "https://www.ah.nl/allerhande/recept/R-R1234")
        self.assertEqual(ld["recipeIngredient"], ["800 g aardappelen", "500 g boerenkool"])
        self.assertEqual(
            ld["recipeInstructions"],
            [{"@type": "HowToStep", "text": "Kook de aardappelen."}, {"@type": "HowToStep", "text": "Stamp alles."}],
        )
        self.assertEqual(ld["image"], ["https://img/1234.jpg", "https://img/1234b.jpg"])
        self.assertEqual(ld["recipeYield"], "4 personen")
        self.assertEqual(ld["cookTime"], "PT20M")
        self.assertEqual(ld["totalTime"], "PT25M")
        self.assertEqual(post.call_args.kwargs["json"]["variables"], {"id": 1234})
        json.dumps(ld)  # serialiseerbaar voor Mealie

    def test_falls_back_to_simpler_query_and_string_steps(self):
        minimal = dict(RECIPE, preparation={"steps": ["Kook.", "Stamp."]})
        for key in ("servings", "images", "ovenTime", "waitTime"):
            minimal.pop(key)
        ld, post = self.build([graphql_error("Unknown field"), graphql_error("Unknown field"), graphql_ok(minimal)])
        self.assertEqual(post.call_count, 3)
        self.assertEqual([step["text"] for step in ld["recipeInstructions"]], ["Kook.", "Stamp."])
        self.assertNotIn("image", ld)
        self.assertNotIn("recipeYield", ld)
        self.assertEqual(ld["totalTime"], "PT20M")

    def test_missing_recipe(self):
        with self.assertRaises(RuntimeError) as ctx:
            self.build([graphql_ok(None)])
        self.assertIn("niet gevonden", str(ctx.exception))

    def test_recipe_without_content_is_rejected(self):
        empty = dict(RECIPE, ingredients=[], preparation={"steps": []})
        with self.assertRaises(RuntimeError):
            self.build([graphql_ok(empty)])

    def test_all_variants_failing_reports_errors(self):
        with self.assertRaises(RuntimeError) as ctx:
            self.build([graphql_error("a"), graphql_error("b"), graphql_error("c")])
        self.assertIn("a", str(ctx.exception))
        self.assertIn("c", str(ctx.exception))


class ImportFromUrlTest(unittest.TestCase):
    URL = "https://www.ah.nl/allerhande/recept/R-R1234/stamppot"

    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"MEALIE_URL": "http://mealie", "MEALIE_API_TOKEN": "t"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def response(self, ok=True, status=200, body="slug"):
        response = mock.Mock(ok=ok, status_code=status, text=str(body))
        response.json.return_value = body
        return response

    def test_ah_recipe_goes_through_the_api_without_fetching_the_page(self):
        ld = {"@type": "Recipe", "name": "X"}
        with mock.patch.object(ah, "get_recipe_jsonld", return_value=ld), \
                mock.patch.object(mealie.requests, "post", return_value=self.response(body="stamppot")) as post, \
                mock.patch.object(mealie.requests, "get") as get:
            slug = mealie.import_from_url(self.URL)
        self.assertEqual(slug, "stamppot")
        get.assert_not_called()
        self.assertEqual(post.call_args.args[0], "http://mealie/api/recipes/create/html-or-json")
        sent = post.call_args.kwargs["json"]
        self.assertEqual(json.loads(sent["data"]), ld)
        self.assertEqual(sent["url"], self.URL)

    def test_ah_api_failure_falls_back_to_mealie_url_import(self):
        with mock.patch.object(ah, "get_recipe_jsonld", side_effect=RuntimeError("niet gevonden")), \
                mock.patch.object(mealie.requests, "post", return_value=self.response(body="via-url")) as post:
            self.assertEqual(mealie.import_from_url(self.URL), "via-url")
        self.assertEqual(post.call_args.args[0], "http://mealie/api/recipes/create/url")

    def test_non_ah_url_skips_the_ah_api(self):
        with mock.patch.object(ah, "get_recipe_jsonld", side_effect=AssertionError("niet aanroepen")), \
                mock.patch.object(mealie.requests, "post", return_value=self.response(body="ok")):
            self.assertEqual(mealie.import_from_url("https://example.com/r"), "ok")

    def test_all_failures_are_reported_together(self):
        mealie_error = self.response(ok=False, status=400, body="Recipe scrape failed")
        page_error = self.response(ok=False, status=403, body="Access Denied")
        with mock.patch.object(ah, "get_recipe_jsonld", side_effect=RuntimeError("niet gevonden")), \
                mock.patch.object(mealie.requests, "post", return_value=mealie_error), \
                mock.patch.object(mealie.requests, "get", return_value=page_error):
            with self.assertRaises(RuntimeError) as ctx:
                mealie.import_from_url(self.URL)
        message = str(ctx.exception)
        self.assertIn("AH-API: niet gevonden", message)
        self.assertIn("Mealie: Mealie kon de pagina niet importeren (400): Recipe scrape failed", message)
        self.assertIn("pagina: Kon de pagina niet ophalen (403)", message)


if __name__ == "__main__":
    unittest.main()
