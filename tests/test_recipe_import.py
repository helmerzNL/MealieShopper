import json
import os
import tempfile
import unittest
from unittest import mock

import httpx

os.environ.setdefault("MEALIESHOPPER_DATA_DIR", tempfile.mkdtemp())

from mealieshopper import ah, mealie, picnic_recipes  # noqa: E402
from support import routes, run, sent_json, served  # noqa: E402

CREATE_HTML = "http://mealie/api/recipes/create/html-or-json"
CREATE_URL = "http://mealie/api/recipes/create/url"

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
    response = httpx.Response(200, json={"data": {"recipe": recipe}})
    return response


def graphql_error(message):
    response = httpx.Response(200, json={"errors": [{"message": message}]})
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
                served(side_effect=responses) as post:
            return run(ah.get_recipe_jsonld(1234)), post

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
        self.assertEqual(sent_json(post.calls.last)["variables"], {"id": 1234})
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
        # Het analyseren van de ingrediënten na de import heeft zijn eigen tests (test_ingredients).
        parser = mock.patch.object(mealie, "parse_imported_ingredients", new_callable=mock.AsyncMock, side_effect=lambda slug: slug)
        parser.start()
        self.addCleanup(parser.stop)

    def response(self, status=200, body="slug"):
        return httpx.Response(status, json=body) if status < 400 else httpx.Response(status, text=str(body))

    def test_ah_recipe_goes_through_the_api_without_fetching_the_page(self):
        ld = {"@type": "Recipe", "name": "X"}
        with mock.patch.object(ah, "get_recipe_jsonld", return_value=ld), routes() as api:
            post = api.post(CREATE_HTML).mock(return_value=self.response(body="stamppot"))
            get = api.get()
            slug = run(mealie.import_from_url(self.URL))
        self.assertEqual(slug, "stamppot")
        self.assertFalse(get.called)
        sent = sent_json(post.calls.last)
        self.assertEqual(json.loads(sent["data"]), ld)
        self.assertEqual(sent["url"], self.URL)

    def test_ah_api_failure_falls_back_to_mealie_url_import(self):
        with mock.patch.object(ah, "get_recipe_jsonld", side_effect=RuntimeError("niet gevonden")), routes() as api:
            post = api.post(CREATE_URL).mock(return_value=self.response(body="via-url"))
            self.assertEqual(run(mealie.import_from_url(self.URL)), "via-url")
        self.assertEqual(sent_json(post.calls.last), {"url": self.URL, "include_tags": True, "include_categories": True})

    def test_non_ah_url_skips_the_ah_api(self):
        with mock.patch.object(ah, "get_recipe_jsonld", side_effect=AssertionError("niet aanroepen")), routes() as api:
            api.post(CREATE_URL).mock(return_value=self.response(body="ok"))
            self.assertEqual(run(mealie.import_from_url("https://example.com/r")), "ok")

    def test_the_html_of_a_page_keeps_its_address_for_the_scraper_of_that_site(self):
        with routes() as api:
            page = api.get("https://www.example.com/soep").mock(return_value=httpx.Response(200, text="<html>recept</html>"))
            post = api.post(CREATE_HTML).mock(return_value=self.response(body="soep"))
            self.assertEqual(run(mealie.import_via_html("https://www.example.com/soep")), "soep")
        sent = sent_json(post.calls.last)
        self.assertEqual((sent["data"], sent["url"]), ("<html>recept</html>", "https://www.example.com/soep"))
        self.assertEqual(page.calls.last.request.headers["User-Agent"], mealie.BROWSER_HEADERS["User-Agent"], "de pagina krijgt een browser")
        self.assertNotIn("Authorization", page.calls.last.request.headers, "het token gaat alleen naar Mealie")

    def test_a_cookie_from_a_redirect_goes_along_to_the_page(self):
        # Een cookiemuur: eerst een 302 met een cookie, en pas met die cookie komt de pagina zelf.
        with routes() as api:
            page = api.get("https://www.example.com/soep").mock(side_effect=[
                httpx.Response(302, headers={"Location": "https://www.example.com/soep", "Set-Cookie": "consent=yes; Path=/"}),
                httpx.Response(200, text="<html>recept</html>"),
            ])
            post = api.post(CREATE_HTML).mock(return_value=self.response(body="soep"))
            self.assertEqual(run(mealie.import_via_html("https://www.example.com/soep")), "soep")
        self.assertNotIn("Cookie", page.calls[0].request.headers)
        self.assertEqual(page.calls[1].request.headers["Cookie"], "consent=yes")
        self.assertEqual(sent_json(post.calls.last)["data"], "<html>recept</html>")
        self.assertNotIn("Cookie", post.calls.last.request.headers, "de cookie gaat niet mee naar Mealie")

    def test_the_page_follows_a_redirect(self):
        with routes() as api:
            api.get("https://example.com/soep").mock(return_value=httpx.Response(301, headers={"Location": "https://www.example.com/soep"}))
            api.get("https://www.example.com/soep").mock(return_value=httpx.Response(200, text="<html>recept</html>"))
            post = api.post(CREATE_HTML).mock(return_value=self.response(body="soep"))
            self.assertEqual(run(mealie.import_via_html("https://example.com/soep")), "soep")
        self.assertEqual(sent_json(post.calls.last)["data"], "<html>recept</html>")

    def test_all_failures_are_reported_together(self):
        with mock.patch.object(ah, "get_recipe_jsonld", side_effect=RuntimeError("niet gevonden")), routes() as api:
            api.post(CREATE_URL).mock(return_value=self.response(status=400, body="Recipe scrape failed"))
            api.get(self.URL).mock(return_value=self.response(status=403, body="Access Denied"))
            with self.assertRaises(RuntimeError) as ctx:
                run(mealie.import_from_url(self.URL))
        message = str(ctx.exception)
        self.assertIn("AH-API: niet gevonden", message)
        self.assertIn("Mealie: Mealie kon de pagina niet importeren (400): Recipe scrape failed", message)
        self.assertIn("pagina: Kon de pagina niet ophalen (403)", message)


class JsonLdSourceTest(unittest.TestCase):
    """#242: Mealie leest JSON-LD met recipe-scrapers, en dat kiest zijn scraper op de host van de link. Voor picnic.app heeft
    het een eigen scraper die de JSON-LD negeert: in Mealie stonden dan alleen de titel en de foto. Zo'n recept gaat onder een
    neutrale link naar Mealie, en daarna krijgt het zijn echte bron terug."""

    LD = {"@context": "https://schema.org", "@type": "Recipe", "name": "Pasta pesto", "recipeIngredient": ["200 g pasta"],
          "recipeInstructions": [{"@type": "HowToStep", "text": "Kook de pasta."}]}

    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"MEALIE_URL": "http://mealie", "MEALIE_API_TOKEN": "t"})
        patcher.start()
        self.addCleanup(patcher.stop)
        parser = mock.patch.object(mealie, "parse_imported_ingredients", new_callable=mock.AsyncMock, side_effect=lambda slug, *args: slug)
        self.parser = parser.start()
        self.addCleanup(parser.stop)

    def response(self, status=200, body="pasta-pesto"):
        return httpx.Response(status, json=body) if status < 400 else httpx.Response(status, text=str(body))

    def test_a_picnic_recipe_is_read_under_a_neutral_link_and_keeps_its_source(self):
        source = picnic_recipes.source_url("r1")
        with routes() as api:
            post = api.post(CREATE_HTML).mock(return_value=self.response())
            patch = api.patch("http://mealie/api/recipes/pasta-pesto").mock(return_value=self.response(body={}))
            self.assertEqual(run(mealie.import_via_jsonld(source, self.LD)), "pasta-pesto")
        sent = sent_json(post.calls.last)
        self.assertEqual(sent["url"], mealie.NEUTRAL_SOURCE)
        self.assertEqual(json.loads(sent["data"]), self.LD, "de JSON-LD zelf verandert niet")
        self.assertEqual(sent_json(patch.calls.last), {"orgURL": source}, "de bron blijft de link naar Picnic")
        self.parser.assert_awaited_once_with("pasta-pesto")

    def test_every_picnic_link_counts_and_other_stores_keep_their_own_link(self):
        for url in ("https://picnic.app/#recipe-r1", "https://www.picnic.app/nl/recepten/x", "https://PICNIC.app/r"):
            self.assertEqual(mealie._scrape_url(url), mealie.NEUTRAL_SOURCE, url)
        for url in (
            "https://www.ah.nl/allerhande/recept/R-R1234/stamppot",  # recipe-scrapers leest daar de JSON-LD wel
            "https://www.jumbo.com/recepten/romige-pasta-123",
            "https://crisp.nl/#recipe-123",
            "https://www.hellofresh.nl/recipes/pasta-abc",
            "https://notpicnic.app/r",
        ):
            self.assertEqual(mealie._scrape_url(url), url, url)

    def test_another_source_is_sent_as_is_and_not_patched(self):
        url = "https://www.jumbo.com/recepten/romige-pasta-123"
        with routes() as api:
            post = api.post(CREATE_HTML).mock(return_value=self.response())
            patch = api.patch()
            self.assertEqual(run(mealie.import_via_jsonld(url, self.LD)), "pasta-pesto")
        self.assertEqual(sent_json(post.calls.last)["url"], url)
        self.assertFalse(patch.called)

    def test_a_failed_source_update_keeps_the_imported_recipe(self):
        for failure in ({"return_value": self.response(status=403, body="Forbidden")},
                        {"side_effect": httpx.ConnectError("weg")}):
            with self.subTest(failure=failure), routes() as api, self.assertLogs(mealie.log, "WARNING") as logs:
                api.post(CREATE_HTML).mock(return_value=self.response())
                api.patch("http://mealie/api/recipes/pasta-pesto").mock(**failure)
                self.assertEqual(run(mealie.import_via_jsonld(picnic_recipes.source_url("r1"), self.LD)), "pasta-pesto")
            self.assertNotIn("Forbidden", " ".join(logs.output))
            self.assertNotIn("weg", " ".join(logs.output))


if __name__ == "__main__":
    unittest.main()
