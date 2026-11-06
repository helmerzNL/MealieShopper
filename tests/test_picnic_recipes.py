import json
import os
import tempfile
import unittest
from unittest import mock

import httpx

os.environ.setdefault("MEALIESHOPPER_DATA_DIR", tempfile.mkdtemp())

from mealieshopper import auth, mealie, picnic, picnic_recipes  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402
from helpers import AppTestCase, run, served  # noqa: E402


def rich(markdown, kind=None):
    node = {"type": "RICH_TEXT", "markdown": markdown, "numberOfLines": 1}
    if kind:
        node["textType"] = kind
    return node


def tile(recipe_id, texts, *, name=None, image="recipes/img-1", segment=None):
    """Een receptonderdeel van een lijstpagina, zoals de verkenner het meldde: analysegegevens plus opmaak."""
    data = {"recipe_id": recipe_id, "template_id": "t", "type": "recipe_tile"}
    if name:
        data["recipe_name"] = name
    if segment:
        data["segment_name"] = segment
    children = [{"type": "IMAGE", "source": {"id": image}}] if image else []
    children += [rich(text) for text in texts]
    return {"id": f"tile-{recipe_id}", "type": "PML", "analytics": {"contexts": [{"schema": "x/recipe", "data": data}]},
            "pml": {"component": {"type": "STACK", "children": children}, "images": {}}}


def list_page(*tiles):
    return {"id": "page", "type": "STATE_BOUNDARY", "child": {"children": [{"children": list(tiles)}]}}


def detail_page(recipe_id="r1"):
    return {"id": "page", "body": {"children": [
        {"id": "context", "analytics": {"contexts": [{"data": {
            "recipe_id": recipe_id, "recipe_name": " Pasta pesto ", "portions": 4, "image_type": "x",
            "selling_units": [
                {"ingredient_id": "i1", "selling_unit_id": "s1", "quantity": 2, "status": "ACTIVE", "checked": True},
                {"ingredient_id": "i2", "selling_unit_id": "s2", "quantity": 1},
                {"ingredient_id": "i3", "quantity": 0},
            ]}}]}},
        {"id": "selling-group-details-image", "pml": {"component": {"type": "CONTAINER", "child": {"type": "IMAGE", "source": {"id": "recipes/big"}}}}},
        {"id": "selling-group-time-and-price-section", "pml": {"component": {"children": [
            rich("**20 min**"), rich("Totaal"), rich("€ 8,50"), rich("Per portie")]}}},
        {"type": "PML", "id": "selling-unit-tile-a",
         "analytics": {"contexts": [{"data": {"selling_units": [{"ingredient_id": "i1"}]}}]},
         "pml": {"component": {"children": [rich("Rode uien", "SUBTITLE1"), rich("1 kilo"), rich("(150 g nodig)")]}}},
        {"type": "PML", "id": "selling-unit-tile-b",
         "pml": {"component": {"onPress": {"expression": "open(picnic://store/page;id=x,ingredient_id=abc-12&is_udr=false)"},
                               "children": [rich("Pesto", "SUBTITLE1"), rich("190 gram")]}}},
        {"id": "instructions-portions-section", "pml": {"component": {"children": [rich("Voor 4 personen")]}}},
        {"id": "instructions-section", "pml": {"component": {"children": [
            {"children": [rich("Stap 1"), rich("Snijd de #(ff0000)uien#(000000) fijn.")]},
            {"children": [rich("**Stap 2**"), rich("Bak ze   met de pesto.")]}]}}},
    ]}}


def response(data):
    """Een antwoord van Picnic, voor `served`."""
    return httpx.Response(200, content=json.dumps(data).encode())


class TempDataDirTest(AppTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"MEALIESHOPPER_DATA_DIR": tmp.name, "PASSKEY_AUTH_ENABLED": "false"})
        patcher.start()
        self.addCleanup(patcher.stop)
        run(auth.init_db())


class TextTest(AppTestCase):
    def test_markdown_is_cleaned(self):
        self.assertEqual(picnic_recipes.clean("#(ff0000)**Snijd**#(000000)\xa0de   ui__"), "Snijd de ui")
        self.assertEqual(picnic_recipes.clean(None), "")

    def test_minutes(self):
        for text, minutes in (("20 min", 20), ("1 uur 10 min", 70), ("2 u", 120), ("45 minuten", 45), ("45", None), ("", None), (None, None)):
            self.assertEqual(picnic_recipes.parse_minutes(text), minutes, text)


class SummariesTest(AppTestCase):
    def test_a_saved_tile_has_its_name_in_the_analytics(self):
        page = list_page(tile("r1", ["Bewaard", "25 min"], name="Pasta pesto"))
        recipe = picnic_recipes.summaries(page)[0]
        self.assertEqual((recipe["id"], recipe["name"], recipe["minutes"]), ("r1", "Pasta pesto", 25))
        self.assertEqual(recipe["tags"], ["Bewaard"])

    def test_a_cookbook_tile_without_a_name_uses_its_first_text(self):
        page = list_page(tile("r2", ["35 min", "Vegetarisch curry", "Vegetarisch"], segment="Nieuw"))
        recipe = picnic_recipes.summaries(page)[0]
        self.assertEqual((recipe["name"], recipe["minutes"], recipe["segment"]), ("Vegetarisch curry", 35, "Nieuw"))
        self.assertEqual(recipe["tags"], ["Vegetarisch"])

    def test_the_image_keeps_its_namespace(self):
        recipe = picnic_recipes.summaries(list_page(tile("r1", ["Pasta"], image="recipes/abc def")))[0]
        self.assertTrue(recipe["image"].endswith("/static/images/recipes/abc%20def/300x300.png"), recipe["image"])
        self.assertIsNone(picnic_recipes.summaries(list_page(tile("r1", ["Pasta"], image=None)))[0]["image"])

    def test_a_recipe_photo_has_a_size_in_pixels_and_a_product_photo_keeps_the_name(self):
        # #236: een nieuwer recept van Picnic heeft geen `medium.png` of `large.png` (403 AccessDenied), wel `300x300.png` en
        # `1000x1000.png`. Een ouder recept heeft beide, met dezelfde foto: `medium` past in 300x300, `large` in 1000x1000.
        base = "https://storefront-prod.nl.picnicinternational.com/static/images"
        with mock.patch.object(picnic, "country_code", return_value="NL"):
            self.assertEqual(picnic.image_url("recipes/ac1b02"), f"{base}/recipes/ac1b02/300x300.png")
            self.assertEqual(picnic.image_url("recipes/ac1b02", "large"), f"{base}/recipes/ac1b02/1000x1000.png")
            self.assertEqual(picnic.image_url("recipes/ac1b02", "600x600"), f"{base}/recipes/ac1b02/600x600.png")
            self.assertEqual(picnic.image_url("8560e1"), f"{base}/8560e1/medium.png")
            self.assertEqual(picnic.image_url("8560e1", "large"), f"{base}/8560e1/large.png")
        self.assertEqual(picnic.resized_image(f"{base}/recipes/ac1b02/300x300.png", "large"), f"{base}/recipes/ac1b02/1000x1000.png")
        self.assertEqual(picnic.resized_image(f"{base}/8560e1/medium.png", "large"), f"{base}/8560e1/large.png")

    def test_a_stored_recipe_photo_with_a_size_name_gets_the_size_in_pixels(self):
        base = "https://storefront-prod.nl.picnicinternational.com/static/images"
        self.assertEqual(picnic.current_image(f"{base}/recipes/ac1b02/large.png"), f"{base}/recipes/ac1b02/1000x1000.png")
        self.assertEqual(picnic.current_image(f"{base}/recipes/ac1b02/medium.png"), f"{base}/recipes/ac1b02/300x300.png")
        for unchanged in (f"{base}/recipes/ac1b02/1000x1000.png", f"{base}/8560e1/large.png", "https://static.ah.nl/x.jpg", "", None):
            with self.subTest(url=unchanged):
                self.assertEqual(picnic.current_image(unchanged), unchanged)

    def test_duplicates_nodes_without_markup_and_nameless_tiles_are_skipped(self):
        analytics_only = {"id": "x", "analytics": {"contexts": [{"data": {"recipe_id": "r9", "recipe_name": "Alleen analyse"}}]}}
        page = list_page(tile("r1", ["Pasta"]), tile("r1", ["Pasta"]), analytics_only, tile("r3", ["20 min"]))
        self.assertEqual([r["id"] for r in picnic_recipes.summaries(page)], ["r1"])

    def test_odd_pages_give_nothing(self):
        for page in (None, {}, [], "tekst", {"analytics": {"contexts": "x"}}):
            self.assertEqual(picnic_recipes.summaries(page), [])


class SourcesTest(TempDataDirTest):
    def setUp(self):
        super().setUp()
        run(picnic.save_auth_key("key"))

    def test_each_source_has_its_page(self):
        for source, page_id in (("saved", "saved-deep-dive-page-content"), ("cookbook", "cookbook-page-content"),
                                ("ordered", "meals-purchase-page-root"), (None, "saved-deep-dive-page-content")):
            with served(return_value=response(list_page(tile("r1", ["Pasta"])))) as call:
                self.assertEqual(run(picnic_recipes.get_recipes(source))[0]["id"], "r1")
            self.assertTrue(str(call.calls.last.request.url).endswith(f"/pages/{page_id}"), source)
            self.assertEqual(call.calls.last.request.method, "GET")

    def test_unknown_sources_are_refused_before_any_request(self):
        with served(side_effect=AssertionError("geen verzoek")):
            with self.assertRaises(ValueError):
                run(picnic_recipes.get_recipes("favorieten"))

    def test_a_page_that_is_not_json_is_reported(self):
        reply = httpx.Response(200, content=b"RSC")
        with served(return_value=reply):
            with self.assertRaisesRegex(RuntimeError, "onbekend formaat"):
                run(picnic_recipes.get_recipes("saved"))


class SearchTest(TempDataDirTest):
    """Recepten zoeken: Picnic heeft geen zoekfunctie, dus we filteren je bewaarde recepten en het kookboek op naam."""

    LISTS = {
        "saved": [{"id": "r1", "name": "Romige pasta met zalm"}, {"id": "r2", "name": "Rode curry"}],
        "cookbook": [{"id": "r1", "name": "Romige pasta met zalm"}, {"id": "r3", "name": "Pasta Pesto"}, {"id": "r4", "name": "Pâté met brood"}],
    }

    def test_all_words_must_be_in_the_name_without_accents_and_capitals_and_without_duplicates(self):
        with mock.patch.object(picnic_recipes, "get_recipes", new_callable=mock.AsyncMock, side_effect=lambda source: self.LISTS[source]):
            found = run(picnic_recipes.search("PASTA"))
            both = run(picnic_recipes.search("pasta zalm"))
            accents = run(picnic_recipes.search("pate"))
        self.assertEqual([r["id"] for r in found["recipes"]], ["r1", "r3"])  # r1 staat in beide lijsten, maar telt één keer
        self.assertEqual(found["total"], 2)
        self.assertEqual([r["id"] for r in both["recipes"]], ["r1"])
        self.assertEqual([r["id"] for r in accents["recipes"]], ["r4"])

    def test_an_empty_term_is_refused(self):
        with self.assertRaises(ValueError):
            run(picnic_recipes.search("   "))

    def test_one_list_failing_still_searches_the_other_and_both_failing_is_an_error(self):
        def only_cookbook(source):
            if source == "saved":
                raise RuntimeError("stuk")
            return self.LISTS[source]

        with mock.patch.object(picnic_recipes, "get_recipes", new_callable=mock.AsyncMock, side_effect=only_cookbook):
            self.assertEqual([r["id"] for r in run(picnic_recipes.search("pesto"))["recipes"]], ["r3"])
        with mock.patch.object(picnic_recipes, "get_recipes", new_callable=mock.AsyncMock, side_effect=RuntimeError("stuk")):
            with self.assertRaises(RuntimeError):
                run(picnic_recipes.search("pesto"))

    def test_a_broken_link_is_not_hidden_behind_the_other_list(self):
        with mock.patch.object(picnic_recipes, "get_recipes", new_callable=mock.AsyncMock, side_effect=picnic.PicnicAuthError("koppeling")) as get:
            with self.assertRaises(picnic.PicnicAuthError):
                run(picnic_recipes.search("pasta"))
        self.assertEqual(get.call_count, 1)

    async def test_the_route_searches_when_q_is_given(self):
        client = self.client_for(create_app())
        with mock.patch.object(picnic_recipes, "get_recipes", new_callable=mock.AsyncMock, side_effect=lambda source: self.LISTS[source]):
            body = (await client.get("/api/picnic/recipes?q=pesto")).get_json()
        self.assertEqual((body["source"], body["total"], body["recipes"][0]["id"]), ("search", 1, "r3"))


class DetailsTest(TempDataDirTest):
    def test_the_recipe_is_read_from_its_page(self):
        recipe = picnic_recipes.details(detail_page(), "r1")
        self.assertEqual((recipe["name"], recipe["portions"], recipe["minutes"], recipe["stepsPortions"]), ("Pasta pesto", 4, 20, 4))
        self.assertTrue(recipe["image"].endswith("/static/images/recipes/big/1000x1000.png"), recipe["image"])
        self.assertEqual(recipe["times"], [{"value": "20 min", "label": "Totaal"}, {"value": "€ 8,50", "label": "Per portie"}])
        self.assertEqual(recipe["steps"], ["Snijd de uien fijn.", "Bak ze met de pesto."])

    def test_ingredients_follow_the_recipe_and_take_their_names_from_the_tiles(self):
        first, second, third = picnic_recipes.details(detail_page(), "r1")["ingredients"]
        self.assertEqual((first["name"], first["needed"], first["unitSize"], first["productId"], first["quantity"]),
                         ("Rode uien", "(150 g nodig)", "1 kilo", "s1", 2))
        # De tegel zonder analysegegevens wordt aan zijn ingrediënt gekoppeld via de verwijzing in de opmaak.
        self.assertEqual((second["name"], second["unitSize"]), (None, None))
        self.assertEqual((third["name"], third["productId"], third["quantity"]), (None, None, 1))
        self.assertEqual((first["line"], second["line"], third["line"]), ("150 g Rode uien", "Product s2", "Onbekend product"))

    def test_a_tile_without_analytics_is_found_through_its_link(self):
        page = detail_page()
        page["body"]["children"][0]["analytics"]["contexts"][0]["data"]["selling_units"][1]["ingredient_id"] = "abc-12"
        second = picnic_recipes.details(page, "r1")["ingredients"][1]
        self.assertEqual((second["name"], second["unitSize"]), ("Pesto", "190 gram"))

    def test_another_recipe_or_an_empty_page_is_not_found(self):
        for page in (detail_page("r2"), {}, None, {"body": {}}):
            with self.assertRaises(RuntimeError):
                picnic_recipes.details(page, "r1")

    def test_the_right_page_is_requested_and_ids_are_checked(self):
        run(picnic.save_auth_key("key"))
        with served(return_value=response(detail_page())) as call:
            self.assertEqual(run(picnic_recipes.get_recipe(" r1 "))["id"], "r1")
        self.assertTrue(str(call.calls.last.request.url).endswith("/pages/selling-group-details-page?selling_group_id=r1"))
        with served(side_effect=AssertionError("geen verzoek")):
            for bad in ("", None, "a/b", "a b", "r1&x=1", "x" * 101, "../cart"):
                with self.assertRaises(ValueError, msg=repr(bad)):
                    run(picnic_recipes.get_recipe(bad))


class MealieTest(AppTestCase):
    RECIPE = {"id": "r1", "name": "Pasta pesto", "image": "https://x/large.png", "portions": 4, "minutes": 20,
              "steps": ["Snijd.", "Bak."],
              "ingredients": [{"name": "Rode uien", "needed": "(150 g nodig)", "unitSize": "1 kilo", "productId": "s1"},
                              {"name": "Pesto", "needed": None, "unitSize": "190 gram", "productId": "s2"},
                              {"name": None, "needed": None, "unitSize": None, "productId": "s3"}]}

    def test_ingredient_lines(self):
        lines = [picnic_recipes.ingredient_line(i) for i in self.RECIPE["ingredients"]]
        self.assertEqual(lines, ["150 g Rode uien", "Pesto (190 gram)", "Product s3"])
        self.assertEqual(picnic_recipes.ingredient_line({"name": None, "productId": None}), "Onbekend product")

    def test_the_recipe_becomes_a_schema_org_recipe(self):
        data = picnic_recipes.jsonld(self.RECIPE)
        self.assertEqual((data["@type"], data["name"], data["recipeYield"], data["totalTime"]), ("Recipe", "Pasta pesto", "4", "PT20M"))
        self.assertEqual(data["image"], ["https://x/large.png"])
        self.assertEqual(data["recipeInstructions"], [{"@type": "HowToStep", "text": "Snijd."}, {"@type": "HowToStep", "text": "Bak."}])
        json.dumps(data)

    def test_optional_fields_are_left_out(self):
        data = picnic_recipes.jsonld({**self.RECIPE, "image": None, "portions": None, "minutes": None})
        for key in ("image", "recipeYield", "totalTime"):
            self.assertNotIn(key, data)

    def test_a_recipe_without_content_is_refused(self):
        with self.assertRaises(RuntimeError):
            picnic_recipes.jsonld({**self.RECIPE, "ingredients": [], "steps": []})

    def test_the_source_is_picnics_site(self):
        self.assertTrue(picnic_recipes.source_url("r1").startswith("https://picnic.app/"))


class RoutesTest(TempDataDirTest):
    def setUp(self):
        super().setUp()
        self.client = self.client_for(create_app())

    async def test_list_and_detail_routes(self):
        listed = [{"id": "r1", "name": "Pasta"}]
        with mock.patch.object(picnic_recipes, "get_recipes", new_callable=mock.AsyncMock, return_value=listed) as get, \
                mock.patch.object(picnic_recipes, "get_recipe", new_callable=mock.AsyncMock, return_value={"id": "r1"}) as one:
            body = (await self.client.get("/api/picnic/recipes?source=ordered")).get_json()
            default = (await self.client.get("/api/picnic/recipes")).get_json()
            detail = (await self.client.get("/api/picnic/recipes/r1")).get_json()
        self.assertEqual(body, {"source": "ordered", "recipes": listed})
        self.assertEqual(default["source"], "saved")
        self.assertEqual([c.args for c in get.call_args_list], [("ordered",), ("saved",)])
        self.assertEqual((detail, one.call_args.args), ({"id": "r1"}, ("r1",)))

    async def test_bad_input_is_400_and_picnic_failures_are_424(self):
        self.assertEqual((await self.client.get("/api/picnic/recipes?source=x")).status_code, 400)
        self.assertEqual((await self.client.get("/api/picnic/recipes/a%20b")).status_code, 400)
        with mock.patch.object(picnic_recipes, "get_recipe", new_callable=mock.AsyncMock, side_effect=RuntimeError("500: antwoord met geheim")):
            reply = await self.client.get("/api/picnic/recipes/r1")
        self.assertEqual((reply.status_code, reply.get_json()), (424, {"error": "Picnic fout"}))

    async def test_import_sends_a_schema_org_recipe_to_mealie(self):
        await picnic.save_auth_key("key")
        with mock.patch.object(mealie, "ensure_configured", new_callable=mock.AsyncMock), \
                mock.patch.object(mealie, "mealie_url", new_callable=mock.AsyncMock, return_value="https://mealie.example"), \
                mock.patch.object(mealie, "group_slug", new_callable=mock.AsyncMock, return_value="home"), \
                mock.patch.object(picnic_recipes, "get_recipe", new_callable=mock.AsyncMock, return_value=MealieTest.RECIPE), \
                mock.patch.object(mealie, "import_via_jsonld", new_callable=mock.AsyncMock, return_value="pasta-pesto") as send:
            body = (await self.client.post("/api/picnic/recipes/r1/import")).get_json()
        self.assertEqual(body, {"slug": "pasta-pesto", "mealieUrl": "https://mealie.example/g/home/r/pasta-pesto"})
        url, data = send.call_args.args
        self.assertEqual(url, picnic_recipes.source_url("r1"))
        self.assertEqual(data["name"], "Pasta pesto")

    async def test_import_without_mealie_says_so_and_asks_picnic_nothing(self):
        with mock.patch.object(mealie, "ensure_configured", new_callable=mock.AsyncMock, side_effect=RuntimeError("Mealie is niet gekoppeld.")), \
                mock.patch.object(picnic_recipes, "get_recipe", new_callable=mock.AsyncMock, side_effect=AssertionError("niet ophalen")):
            reply = await self.client.post("/api/picnic/recipes/r1/import")
        self.assertEqual((reply.status_code, reply.get_json()), (400, {"error": "Mealie is niet gekoppeld."}))

    async def test_a_failed_import_does_not_leak_the_answer_of_mealie(self):
        with mock.patch.object(mealie, "ensure_configured", new_callable=mock.AsyncMock), \
                mock.patch.object(picnic_recipes, "get_recipe", new_callable=mock.AsyncMock, return_value=MealieTest.RECIPE), \
                mock.patch.object(mealie, "import_via_jsonld", new_callable=mock.AsyncMock, side_effect=RuntimeError("Mealie import mislukt (500): geheim")):
            reply = await self.client.post("/api/picnic/recipes/r1/import")
        self.assertEqual(reply.status_code, 424)
        self.assertNotIn("geheim", reply.get_data(as_text=True))

    def test_nothing_here_writes_to_picnic(self):
        run(picnic.save_auth_key("key"))
        with served(return_value=response(detail_page())) as call:
            run(picnic_recipes.get_recipe("r1"))
            run(picnic_recipes.get_recipes("saved"))
        self.assertEqual({c.request.method for c in call.calls}, {"GET"})


if __name__ == "__main__":
    unittest.main()
