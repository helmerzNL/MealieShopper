import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

os.environ.setdefault("MEALIESHOPPER_DATA_DIR", tempfile.mkdtemp())

from mealieshopper import auth, hellofresh, mealie  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402

KEY = "pasta-met-pesto-" + "a" * 24
KEY_B = "salade-met-kip-" + "b" * 24


def course(key, name, **extra):
    recipe = {
        "name": name, "headline": "met verse basilicum", "websiteUrl": f"https://www.hellofresh.nl/recipes/{key}",
        "imagePath": "/image/pasta.jpg", "totalTime": "PT25M", "prepTime": "PT10M", "difficulty": 1,
        "cuisines": [{"name": "Italiaans"}], "label": {"text": "Herfstsmaken"},
    }
    recipe.update(extra)
    return {"recipe": recipe}


def menu_page(courses, week="2026-W44"):
    payload = {"props": {"pageProps": {"ssrPayload": {"courses": courses, "activeWeek": week, "currentWeek": "2026-W45"}}}}
    return f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script></html>'


def recipe_page(**extra):
    recipe = {
        "@context": "http://schema.org/", "@type": "Recipe", "name": "Pasta  met pesto", "description": "<p>Lekker <b>snel</b>.</p>",
        "image": "https://img.hellofresh.com/big.jpg", "totalTime": "PT1H5M", "recipeYield": 2,
        "recipeIngredient": ["200 gram Pasta", "1 stuk(s) Ui", "  naar smaak   Peper en zout "],
        "recipeInstructions": [
            {"@type": "HowToStep", "text": "<p><span>Kook de pasta.</span></p>\n<p><em>Tip: zout het water.</em></p>"},
            {"@type": "HowToStep", "text": "<p>Meng alles &amp; serveer.</p>"},
        ],
        "nutrition": {"@type": "NutritionInformation", "calories": "600 kcal", "fatContent": "20.5 g", "cholesterolContent": None, "servingSize": "757"},
    }
    recipe.update(extra)
    crumbs = {"@context": "http://schema.org/", "@type": "BreadcrumbList", "itemListElement": []}
    return "".join(f'<script type="application/ld+json">{json.dumps(block)}</script>' for block in (crumbs, recipe))


def page(text, status=200, url="https://www.hellofresh.nl/x"):
    return mock.Mock(status_code=status, text=text, url=url)


class TempDataDirTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"MEALIESHOPPER_DATA_DIR": tmp.name, "PASSKEY_AUTH_ENABLED": "false"})
        patcher.start()
        self.addCleanup(patcher.stop)
        auth.init_db()
        # Geen echte pauze tussen verzoeken in de tests.
        gap = mock.patch.object(hellofresh, "MIN_GAP", 0)
        gap.start()
        self.addCleanup(gap.stop)


class HelperTest(unittest.TestCase):
    def test_durations_become_minutes(self):
        self.assertEqual([hellofresh.minutes(v) for v in ("PT25M", "PT1H5M", "PT2H", "PT0M", "", None, "25 min")], [25, 65, 120, None, None, None, None])

    def test_the_key_is_the_slug_and_the_id_of_a_recipe_page(self):
        self.assertEqual(hellofresh.recipe_key(f"https://www.hellofresh.nl/recipes/{KEY}"), KEY)
        for url in (f"https://evil.example/recipes/{KEY}", "https://www.hellofresh.nl/recipes/zonder-id", "https://www.hellofresh.nl/menus/2026-W44", None):
            self.assertIsNone(hellofresh.recipe_key(url), url)

    def test_weeks_are_checked_and_have_neighbours(self):
        self.assertEqual(hellofresh.neighbours("2026-W44"), ("2026-W43", "2026-W45"))
        self.assertEqual(hellofresh.neighbours("2026-W01"), ("2025-W52", "2026-W02"))
        self.assertEqual(hellofresh.neighbours("2020-W53")[1], "2021-W01")
        for week in ("2026-W00", "2026-W54", "2026-44", "../menus", "2026-W44/x", ""):
            with self.assertRaises(ValueError, msg=week):
                hellofresh.neighbours(week)

    def test_summary_needs_a_name_and_a_recipe_address(self):
        card = hellofresh.summary(course(KEY, "Pasta met pesto")["recipe"])
        self.assertEqual((card["key"], card["minutes"], card["cuisine"], card["label"]), (KEY, 25, "Italiaans", "Herfstsmaken"))
        self.assertEqual(card["url"], f"https://www.hellofresh.nl/recipes/{KEY}")
        self.assertTrue(card["image"].startswith("https://img.hellofresh.com/") and card["image"].endswith("/image/pasta.jpg"))
        self.assertIsNone(hellofresh.summary({"name": "Zonder adres"}))
        self.assertIsNone(hellofresh.summary({"websiteUrl": f"https://www.hellofresh.nl/recipes/{KEY}"}))

    def test_a_page_in_another_format_is_not_guessed_at(self):
        for text in ("<html></html>", '<script id="__NEXT_DATA__">{"props": 1}</script>', '<script id="__NEXT_DATA__">nope</script>'):
            with self.assertRaises(hellofresh.HelloFreshError):
                hellofresh.parse_menu(text)


class MenuTest(TempDataDirTest):
    def test_the_menu_is_read_and_kept_with_a_time(self):
        with mock.patch.object(hellofresh.requests, "get", return_value=page(menu_page([course(KEY, "Pasta met pesto"), course(KEY, "Dubbel"), course(KEY_B, "Salade")]))) as get:
            menu = hellofresh.get_menu("2026-W44")
        self.assertEqual(get.call_args.args[0], "https://www.hellofresh.nl/menus/2026-W44")
        self.assertIn("MealieShopper/", get.call_args.kwargs["headers"]["User-Agent"])
        self.assertEqual([r["key"] for r in menu["recipes"]], [KEY, KEY_B], "een recept staat één keer in het menu")
        self.assertEqual((menu["week"], menu["previous"], menu["next"]), ("2026-W44", "2026-W43", "2026-W45"))
        with mock.patch.object(hellofresh.requests, "get", side_effect=AssertionError("uit de kopie")):
            self.assertEqual(hellofresh.get_menu("2026-W44")["recipes"], menu["recipes"])

    def test_a_stale_copy_or_a_refresh_asks_hellofresh_again(self):
        with mock.patch.object(hellofresh.requests, "get", return_value=page(menu_page([course(KEY, "Oud")]))):
            hellofresh.get_menu("2026-W44")
        with mock.patch.object(hellofresh.requests, "get", return_value=page(menu_page([course(KEY, "Nieuw")]))) as get:
            self.assertEqual(hellofresh.get_menu("2026-W44", refresh=True)["recipes"][0]["name"], "Nieuw")
        self.assertEqual(get.call_count, 1)
        old = (datetime.now(timezone.utc) - hellofresh.MENU_TTL - timedelta(minutes=1)).isoformat(timespec="seconds")
        with hellofresh._connect() as conn:
            conn.execute("UPDATE hellofresh_menus SET fetched_at = ?", (old,))
        with mock.patch.object(hellofresh.requests, "get", return_value=page(menu_page([course(KEY, "Nieuwst")]))) as get:
            self.assertEqual(hellofresh.get_menu("2026-W44")["recipes"][0]["name"], "Nieuwst")
        self.assertEqual(get.call_count, 1)

    def test_an_older_copy_is_better_than_nothing_when_hellofresh_fails(self):
        with mock.patch.object(hellofresh.requests, "get", return_value=page(menu_page([course(KEY, "Pasta")]))):
            hellofresh.get_menu("2026-W44")
        with mock.patch.object(hellofresh.requests, "get", side_effect=hellofresh.requests.ConnectionError("weg")):
            self.assertEqual(len(hellofresh.get_menu("2026-W44", refresh=True)["recipes"]), 1)
            with self.assertRaises(hellofresh.HelloFreshError):
                hellofresh.get_menu("2026-W45")

    def test_a_week_without_a_menu_and_a_foreign_redirect_are_refused(self):
        with mock.patch.object(hellofresh.requests, "get", return_value=page("", status=404)):
            with self.assertRaises(ValueError):
                hellofresh.get_menu("2026-W44")
        with mock.patch.object(hellofresh.requests, "get", return_value=page("<html></html>", url="https://evil.example/menus")):
            with self.assertRaises(hellofresh.HelloFreshError):
                hellofresh.get_menu("2026-W44")
        with mock.patch.object(hellofresh.requests, "get", side_effect=AssertionError("geen verzoek")):
            with self.assertRaises(ValueError):
                hellofresh.get_menu("../etc/passwd")

    def test_search_uses_only_the_menus_we_already_have(self):
        with mock.patch.object(hellofresh.requests, "get", return_value=page(menu_page([course(KEY, "Pasta met pesto"), course(KEY_B, "Salade met kip", cuisines=[{"name": "Frans"}])]))):
            hellofresh.get_menu("2026-W44")
        with mock.patch.object(hellofresh.requests, "get", side_effect=AssertionError("geen verzoek")):
            found = hellofresh.search("PESTO pasta")
            self.assertEqual((found["total"], found["weeks"], found["recipes"][0]["key"], found["recipes"][0]["week"]), (1, 1, KEY, "2026-W44"))
            self.assertEqual(hellofresh.search("frans")["total"], 1)
            self.assertEqual(hellofresh.search("sushi")["total"], 0)
            with self.assertRaises(ValueError):
                hellofresh.search("  ")


class RecipeTest(TempDataDirTest):
    def test_a_recipe_comes_from_the_schema_org_data_of_its_page(self):
        with mock.patch.object(hellofresh.requests, "get", return_value=page(recipe_page())) as get:
            recipe = hellofresh.get_recipe(KEY)
        self.assertEqual(get.call_args.args[0], f"https://www.hellofresh.nl/recipes/{KEY}")
        self.assertEqual((recipe["name"], recipe["minutes"], recipe["portions"]), ("Pasta met pesto", 65, "2"))
        self.assertEqual(recipe["ingredients"], ["200 gram Pasta", "1 stuk(s) Ui", "naar smaak Peper en zout"])
        self.assertEqual(recipe["steps"], ["Kook de pasta.\nTip: zout het water.", "Meng alles & serveer."])
        self.assertEqual(recipe["description"], "Lekker snel.")

    def test_a_description_without_words_is_left_out(self):
        with mock.patch.object(hellofresh.requests, "get", return_value=page(recipe_page(description="."))):
            self.assertEqual(hellofresh.get_recipe(KEY)["description"], "")

    def test_the_recipe_becomes_a_schema_org_recipe_for_mealie(self):
        with mock.patch.object(hellofresh.requests, "get", return_value=page(recipe_page())):
            data = hellofresh.jsonld(hellofresh.get_recipe(KEY))
        self.assertEqual((data["@type"], data["name"], data["totalTime"], data["recipeYield"]), ("Recipe", "Pasta met pesto", "PT65M", "2"))
        self.assertEqual(data["url"], hellofresh.source_url(KEY))
        self.assertEqual(data["recipeInstructions"][0], {"@type": "HowToStep", "text": "Kook de pasta.\nTip: zout het water."})
        self.assertEqual(data["nutrition"]["calories"], "600 kcal")

    def test_the_payload_for_mealie_is_clean(self):
        with mock.patch.object(hellofresh.requests, "get", return_value=page(recipe_page(image="https://img.hellofresh.com/f_auto,fl_lossy,h_640,q_auto,w_1200/hellofresh_s3/image/pasta-1.jpg"))):
            recipe = hellofresh.get_recipe(KEY)
        data = hellofresh.jsonld(recipe)
        self.assertEqual(data["nutrition"], {"@type": "NutritionInformation", "calories": "600 kcal", "fatContent": "20.5 g"}, "geen null en geen servingSize")
        self.assertEqual(data["image"], ["https://img.hellofresh.com/f_jpg,q_auto,w_1200/hellofresh_s3/image/pasta-1.jpg"], "een vaste JPG, geen f_auto")
        minimal = hellofresh.jsonld(recipe, minimal=True)
        self.assertNotIn("image", minimal)
        self.assertNotIn("nutrition", minimal)
        self.assertEqual(minimal["recipeIngredient"], data["recipeIngredient"])
        json.dumps(data)

    def test_an_image_address_of_another_shape_is_left_alone(self):
        for url in ("https://example.com/a.jpg", "", None, "https://img.hellofresh.com/x/other/a.jpg"):
            self.assertEqual(hellofresh.image_url(url), url or "")

    def test_only_a_key_of_our_own_shape_is_ever_requested(self):
        with mock.patch.object(hellofresh.requests, "get", side_effect=AssertionError("geen verzoek")):
            for key in ("", "6a0472a6b5c58db9dbcf6138", "../../my-account", f"{KEY}/../x", f"{KEY}?page=2", KEY.upper(), None):
                with self.assertRaises(ValueError, msg=key):
                    hellofresh.get_recipe(key)

    def test_a_recipe_without_content_or_in_another_format_is_not_imported(self):
        with mock.patch.object(hellofresh.requests, "get", return_value=page(recipe_page(recipeIngredient=[], recipeInstructions=[]))):
            with self.assertRaises(hellofresh.HelloFreshError):
                hellofresh.jsonld(hellofresh.get_recipe(KEY))
        with mock.patch.object(hellofresh.requests, "get", return_value=page("<html>geen recept</html>")):
            with self.assertRaises(hellofresh.HelloFreshError):
                hellofresh.get_recipe(KEY)


class RoutesTest(TempDataDirTest):
    def setUp(self):
        super().setUp()
        self.client = create_app().test_client()

    def test_menu_and_search_routes(self):
        with mock.patch.object(hellofresh.requests, "get", return_value=page(menu_page([course(KEY, "Pasta met pesto")]))):
            body = self.client.get("/api/hellofresh/menu?week=2026-W44").get_json()
        self.assertEqual((body["week"], len(body["recipes"])), ("2026-W44", 1))
        self.assertEqual(self.client.get("/api/hellofresh/search?q=pesto").get_json()["total"], 1)
        bad = self.client.get("/api/hellofresh/menu?week=nope")
        self.assertEqual((bad.status_code, bad.get_json()), (400, {"error": "Geen geldige week"}))
        self.assertEqual(self.client.get("/api/hellofresh/search").status_code, 400)

    def test_a_menu_we_cannot_read_says_so_in_our_words(self):
        with mock.patch.object(hellofresh.requests, "get", side_effect=hellofresh.requests.ConnectionError("intern adres 10.0.0.1")):
            reply = self.client.get("/api/hellofresh/menu?week=2026-W44")
        self.assertEqual((reply.status_code, reply.get_json()), (424, {"error": "HelloFresh is niet bereikbaar"}))

    def test_import_sends_a_schema_org_recipe_to_mealie(self):
        with mock.patch.object(mealie, "ensure_configured"), \
                mock.patch.object(mealie, "mealie_url", return_value="https://mealie.example"), \
                mock.patch.object(mealie, "group_slug", return_value="home"), \
                mock.patch.object(hellofresh.requests, "get", return_value=page(recipe_page())), \
                mock.patch.object(mealie, "import_via_jsonld", return_value="pasta-met-pesto") as send:
            body = self.client.post(f"/api/hellofresh/recipes/{KEY}/import").get_json()
        self.assertEqual(body, {"slug": "pasta-met-pesto", "mealieUrl": "https://mealie.example/g/home/r/pasta-met-pesto"})
        url, data = send.call_args.args
        self.assertEqual(url, f"https://www.hellofresh.nl/recipes/{KEY}")
        self.assertEqual(data["name"], "Pasta met pesto")

    def test_import_without_mealie_says_so_and_asks_hellofresh_nothing(self):
        with mock.patch.object(mealie, "ensure_configured", side_effect=RuntimeError("Mealie is niet gekoppeld.")), \
                mock.patch.object(hellofresh.requests, "get", side_effect=AssertionError("niet ophalen")):
            reply = self.client.post(f"/api/hellofresh/recipes/{KEY}/import")
        self.assertEqual((reply.status_code, reply.get_json()), (400, {"error": "Mealie is niet gekoppeld."}))

    def test_an_invalid_key_is_refused_before_any_request(self):
        with mock.patch.object(mealie, "ensure_configured"), mock.patch.object(hellofresh.requests, "get", side_effect=AssertionError("geen verzoek")):
            reply = self.client.post("/api/hellofresh/recipes/6a0472a6b5c58db9dbcf6138/import")
        self.assertEqual((reply.status_code, reply.get_json()), (400, {"error": "Geen geldig recept"}))

    def test_a_failed_import_does_not_leak_the_answer_of_mealie(self):
        with mock.patch.object(mealie, "ensure_configured"), \
                mock.patch.object(hellofresh.requests, "get", return_value=page(recipe_page())), \
                mock.patch.object(mealie, "import_via_jsonld", side_effect=RuntimeError("Mealie import mislukt (500): geheim")):
            reply = self.client.post(f"/api/hellofresh/recipes/{KEY}/import")
        self.assertEqual(reply.status_code, 424)
        self.assertNotIn("geheim", reply.get_data(as_text=True))

    def test_when_mealie_refuses_the_recipe_we_try_again_smaller_and_then_by_page(self):
        refused = mealie.MealieImportError("Mealie import mislukt (400): BAD_RECIPE_DATA", 400)
        with mock.patch.object(mealie, "ensure_configured"), mock.patch.object(mealie, "recipe_page_url", return_value="u"), \
                mock.patch.object(hellofresh.requests, "get", return_value=page(recipe_page())), \
                mock.patch.object(mealie, "import_via_jsonld", side_effect=[refused, "slim"]) as send, \
                mock.patch.object(mealie, "import_via_url") as by_page:
            body = self.client.post(f"/api/hellofresh/recipes/{KEY}/import").get_json()
        self.assertEqual(body["slug"], "slim")
        self.assertIn("image", send.call_args_list[0].args[1])
        self.assertNotIn("image", send.call_args_list[1].args[1])
        by_page.assert_not_called()
        with mock.patch.object(mealie, "ensure_configured"), mock.patch.object(mealie, "recipe_page_url", return_value="u"), \
                mock.patch.object(hellofresh.requests, "get", return_value=page(recipe_page())), \
                mock.patch.object(mealie, "import_via_jsonld", side_effect=[refused, refused]), \
                mock.patch.object(mealie, "import_via_url", return_value="viapagina"):
            self.assertEqual(self.client.post(f"/api/hellofresh/recipes/{KEY}/import").get_json()["slug"], "viapagina")

    def test_a_refusal_shows_the_status_of_mealie_but_not_its_answer(self):
        refused = mealie.MealieImportError("Mealie import mislukt (400): geheim", 400)
        with mock.patch.object(mealie, "ensure_configured"), mock.patch.object(hellofresh.requests, "get", return_value=page(recipe_page())), \
                mock.patch.object(mealie, "import_via_jsonld", side_effect=refused), mock.patch.object(mealie, "import_via_url", side_effect=refused):
            reply = self.client.post(f"/api/hellofresh/recipes/{KEY}/import")
        self.assertEqual((reply.status_code, reply.get_json()), (424, {"error": "Importeren in Mealie mislukt (Mealie antwoordde met status 400)"}))
        self.assertNotIn("geheim", reply.get_data(as_text=True))

    def test_a_server_error_of_mealie_is_not_retried_because_the_recipe_may_exist_already(self):
        broken = mealie.MealieImportError("Mealie import mislukt (500): geheim", 500)
        with mock.patch.object(mealie, "ensure_configured"), mock.patch.object(hellofresh.requests, "get", return_value=page(recipe_page())), \
                mock.patch.object(mealie, "import_via_jsonld", side_effect=broken) as send, mock.patch.object(mealie, "import_via_url") as by_page:
            reply = self.client.post(f"/api/hellofresh/recipes/{KEY}/import")
        self.assertEqual(reply.get_json(), {"error": "Importeren in Mealie mislukt (Mealie antwoordde met status 500)"})
        self.assertEqual(send.call_count, 1)
        by_page.assert_not_called()

    def test_nothing_here_writes_to_hellofresh(self):
        with mock.patch.object(hellofresh.requests, "get", return_value=page(recipe_page())), \
                mock.patch.object(hellofresh.requests, "post", side_effect=AssertionError("alleen lezen")), \
                mock.patch.object(hellofresh.requests, "request", side_effect=AssertionError("alleen lezen")):
            hellofresh.get_recipe(KEY)


if __name__ == "__main__":
    unittest.main()
