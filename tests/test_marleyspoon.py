import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

os.environ.setdefault("MEALIESHOPPER_DATA_DIR", tempfile.mkdtemp())

from mealieshopper import audit, auth, marleyspoon, mealie  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402

SLUG = "710152-bulgurbowl-met-kikkererwten-en-bloemkool-met-tahin-granaatappel-en-amandelen"
SLUG_B = "710142-butter-chicken-gehaktballen-met-jasmijnrijst-en-komkommersalade"


def recipe(slug, title, **extra):
    data = {
        "id": 1, "slug": slug, "title": title, "subtitle": "met tahin en amandelen",
        "recipeServingTime": "30-40 minuten", "category": {"displayText": "vegetarisch"},
        "image": {"url": "https://marleyspoon.com/media/recipes/383922/main_photos/medium/bowl.jpeg"},
    }
    data.update(extra)
    return data


TOKEN = "eyJ" + "a" * 60
DETAIL = {
    "id": 710152, "title": "Bulgurbowl met kikkererwten", "subtitle": "met tahin", "description": "Alles uit __één__ kom.",
    "duration": {"from": 20, "to": 30, "unit": "minuten"}, "image": {"url": "https://marleyspoon.com/media/recipes/1/large/a.jpeg"},
    "nutritionalInformation": [{"key": "energy_kcal", "perPortion": 945.0}, {"key": "total_fat", "perPortion": 44.5}, {"key": "sodium", "perPortion": 145.5}, {"key": "salt", "perPortion": 1.0}, {"key": "fiber", "perPortion": None}],
    "shippedIngredients": [{"nameWithQuantity": "150g bulgur"}, {"nameWithQuantity": "1 kleine bloemkool"}],
    "assumedIngredients": [{"name": "peper en zout"}],
    "steps": [{"title": "Bulgur koken", "description": "Voeg de __bulgur__ toe.\nRoer door."}, {"title": "", "description": "Serveren."}],
}


def page(token=TOKEN, url="https://marleyspoon.nl/menu/x"):
    return mock.Mock(status_code=200, url=url, text=f'<script>gon.current_brand="ms";gon.api_token="{token}";gon.api_host="x";</script>' if token else "<html></html>")


def detail_requests(recipe=DETAIL):
    """Eerst de receptpagina (voor het token), dan het receptdetail."""
    return [page(), answer(None, body={"data": {"recipe": recipe}}, url=marleyspoon.API_AUTH)]


def answer(recipes, start="2026-10-26", status=200, url=marleyspoon.API, body=None):
    reply = mock.Mock(status_code=status, url=url)
    reply.json.return_value = body if body is not None else {"data": {"menu": [{"startOfWeek": start, "recipes": recipes}]}}
    return reply


class TempDataDirTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"MEALIESHOPPER_DATA_DIR": tmp.name, "PASSKEY_AUTH_ENABLED": "false"})
        patcher.start()
        self.addCleanup(patcher.stop)
        auth.init_db()
        gap = mock.patch.object(marleyspoon, "MIN_GAP", 0)
        gap.start()
        self.addCleanup(gap.stop)


class RecipeTest(TempDataDirTest):
    def test_the_recipe_is_asked_with_the_token_of_its_page_and_nothing_is_written(self):
        with mock.patch.object(marleyspoon.requests, "request", side_effect=detail_requests()) as ask:
            recipe = marleyspoon.get_recipe(SLUG)
        page_call, detail_call = ask.call_args_list
        self.assertEqual(page_call.args[:2], ("GET", f"https://marleyspoon.nl/menu/{SLUG}"))
        self.assertNotIn("Authorization", page_call.kwargs["headers"])
        self.assertEqual(detail_call.args[:2], ("POST", "https://api.marleyspoon.com/graphql"))
        self.assertEqual(detail_call.kwargs["headers"]["Authorization"], f"Bearer {TOKEN}")
        self.assertEqual(detail_call.kwargs["json"]["variables"], {"recipeId": "710152"})
        self.assertFalse(detail_call.kwargs["allow_redirects"])
        self.assertEqual(recipe["ingredients"], ["150g bulgur", "1 kleine bloemkool", "peper en zout"])
        self.assertEqual(recipe["steps"], ["Bulgur koken: Voeg de bulgur toe.\nRoer door.", "Serveren."])
        self.assertEqual((recipe["name"], recipe["minutes"], recipe["portions"], recipe["description"]), ("Bulgurbowl met kikkererwten met tahin", 30, "2", "Alles uit één kom."))
        self.assertEqual(recipe["nutrition"], {"calories": "945 kcal", "fatContent": "44.5 g", "sodiumContent": "145.5 mg"})

    def test_the_recipe_becomes_a_schema_org_recipe_for_mealie(self):
        with mock.patch.object(marleyspoon.requests, "request", side_effect=detail_requests()):
            recipe = marleyspoon.get_recipe(SLUG)
        data = marleyspoon.jsonld(recipe)
        self.assertEqual((data["@type"], data["url"], data["recipeYield"], data["totalTime"]), ("Recipe", f"https://marleyspoon.nl/menu/{SLUG}", "2", "PT30M"))
        self.assertEqual(data["nutrition"]["calories"], "945 kcal")
        self.assertEqual(data["image"], ["https://marleyspoon.com/media/recipes/1/large/a.jpeg"])
        minimal = marleyspoon.jsonld(recipe, minimal=True)
        self.assertNotIn("image", minimal)
        self.assertNotIn("nutrition", minimal)
        self.assertNotIn(TOKEN, json.dumps(data))

    def test_a_page_without_a_token_or_a_foreign_redirect_is_refused(self):
        for first in (page(token=""), page(url="https://evil.example/x")):
            with mock.patch.object(marleyspoon.requests, "request", side_effect=[first]):
                with self.assertRaises(marleyspoon.MarleySpoonError):
                    marleyspoon.get_recipe(SLUG)

    def test_a_recipe_that_is_gone_or_in_another_shape_is_not_guessed_at(self):
        with mock.patch.object(marleyspoon.requests, "request", side_effect=detail_requests(recipe=None)):
            with self.assertRaises(ValueError):
                marleyspoon.get_recipe(SLUG)
        with mock.patch.object(marleyspoon.requests, "request", side_effect=[page(), answer(None, body={"errors": [{"message": "x"}]})]):
            with self.assertRaises(marleyspoon.MarleySpoonError):
                marleyspoon.get_recipe(SLUG)
        with mock.patch.object(marleyspoon.requests, "request", side_effect=detail_requests(recipe={**DETAIL, "shippedIngredients": [], "assumedIngredients": [], "steps": []})):
            with self.assertRaises(marleyspoon.MarleySpoonError):
                marleyspoon.jsonld(marleyspoon.get_recipe(SLUG))

    def test_only_a_key_of_our_own_shape_is_ever_requested(self):
        with mock.patch.object(marleyspoon.requests, "request", side_effect=AssertionError("geen verzoek")):
            for key in ("zonder-id", "../710152-x", "710152", "", None):
                with self.assertRaises(ValueError, msg=key):
                    marleyspoon.get_recipe(key)


class HelperTest(unittest.TestCase):
    def test_a_range_of_minutes_becomes_its_upper_bound(self):
        self.assertEqual([marleyspoon.minutes(v) for v in ("30-40 minuten", "20-30 minuten", "45 minuten", "", None, "snel")], [40, 30, 45, None, None, None])

    def test_the_key_is_the_slug_with_its_id(self):
        self.assertEqual(marleyspoon.recipe_key(SLUG), SLUG)
        for key in ("zonder-id", "../710152-x", "710152-x/y", "710152-X", "", None):
            self.assertIsNone(marleyspoon.recipe_key(key), key)

    def test_summary_needs_a_title_and_a_slug(self):
        card = marleyspoon.summary(recipe(SLUG, "Bulgurbowl"))
        self.assertEqual((card["key"], card["name"], card["headline"], card["minutes"], card["label"]), (SLUG, "Bulgurbowl", "met tahin en amandelen", 40, "vegetarisch"))
        self.assertEqual(card["url"], f"https://marleyspoon.nl/menu/{SLUG}")
        self.assertTrue(card["image"].startswith("https://marleyspoon.com/"))
        self.assertIsNone(marleyspoon.summary(recipe(SLUG, "")))
        self.assertIsNone(marleyspoon.summary(recipe("zonder-id", "Naam")))

    def test_an_image_that_is_not_https_is_left_out(self):
        self.assertEqual(marleyspoon.summary(recipe(SLUG, "A", image={"url": "http://x.example/a.jpg"}))["image"], "")
        self.assertEqual(marleyspoon.summary(recipe(SLUG, "A", image=None))["image"], "")


class MenuTest(TempDataDirTest):
    def test_the_menu_is_asked_for_one_week_and_kept_with_a_time(self):
        with mock.patch.object(marleyspoon.requests, "request", return_value=answer([recipe(SLUG, "Bulgurbowl"), recipe(SLUG, "Dubbel"), recipe(SLUG_B, "Butter chicken")])) as post:
            menu = marleyspoon.get_menu("2026-W44")
        sent = post.call_args.kwargs["json"]["variables"]
        self.assertEqual((sent["country"], sent["brand"], sent["startDate"]), ("NL", "MS", "2026-10-26"))
        self.assertEqual(post.call_args.args[:2], ("POST", "https://api.marleyspoon.com/public/graphql"))
        self.assertNotIn("Authorization", post.call_args.kwargs["headers"], "het menu vraagt geen token")
        self.assertIn("MealieShopper/", post.call_args.kwargs["headers"]["User-Agent"])
        self.assertEqual([r["key"] for r in menu["recipes"]], [SLUG, SLUG_B], "een recept staat één keer in het menu")
        self.assertEqual((menu["week"], menu["previous"], menu["next"]), ("2026-W44", "2026-W43", "2026-W45"))
        with mock.patch.object(marleyspoon.requests, "request", side_effect=AssertionError("uit de kopie")):
            self.assertEqual(marleyspoon.get_menu("2026-W44")["recipes"], menu["recipes"])

    def test_a_stale_copy_or_a_refresh_asks_marley_spoon_again(self):
        with mock.patch.object(marleyspoon.requests, "request", return_value=answer([recipe(SLUG, "Oud")])):
            marleyspoon.get_menu("2026-W44")
        with mock.patch.object(marleyspoon.requests, "request", return_value=answer([recipe(SLUG, "Nieuw")])) as post:
            self.assertEqual(marleyspoon.get_menu("2026-W44", refresh=True)["recipes"][0]["name"], "Nieuw")
        self.assertEqual(post.call_count, 1)
        old = (datetime.now(timezone.utc) - marleyspoon.MENU_TTL - timedelta(minutes=1)).isoformat(timespec="seconds")
        with marleyspoon._connect() as conn:
            conn.execute("UPDATE marleyspoon_menus SET fetched_at = ?", (old,))
        with mock.patch.object(marleyspoon.requests, "request", return_value=answer([recipe(SLUG, "Nieuwst")])) as post:
            self.assertEqual(marleyspoon.get_menu("2026-W44")["recipes"][0]["name"], "Nieuwst")
        self.assertEqual(post.call_count, 1)

    def test_an_older_copy_is_better_than_nothing_when_marley_spoon_fails(self):
        with mock.patch.object(marleyspoon.requests, "request", return_value=answer([recipe(SLUG, "Oud")])):
            marleyspoon.get_menu("2026-W44")
        with mock.patch.object(marleyspoon.requests, "request", return_value=answer([], status=500)):
            self.assertEqual(marleyspoon.get_menu("2026-W44", refresh=True)["recipes"][0]["name"], "Oud")
        with mock.patch.object(marleyspoon.requests, "request", return_value=answer([], status=500)):
            with self.assertRaises(marleyspoon.MarleySpoonError):
                marleyspoon.get_menu("2026-W45")

    def test_a_week_without_a_menu_is_empty_and_not_kept(self):
        with mock.patch.object(marleyspoon.requests, "request", return_value=answer([], body={"data": {"menu": []}})):
            self.assertEqual(marleyspoon.get_menu("2027-W20")["recipes"], [])
        self.assertIsNone(marleyspoon._stored("2027-W20"))

    def test_the_menu_of_another_week_is_never_shown_under_this_one(self):
        with mock.patch.object(marleyspoon.requests, "request", return_value=answer([recipe(SLUG, "Andere week")], start="2026-11-02")):
            self.assertEqual(marleyspoon.get_menu("2026-W44")["recipes"], [])

    def test_a_foreign_redirect_an_error_and_another_format_are_refused(self):
        for reply in (answer([], url="https://evil.example/graphql"), answer([], status=403),
                      answer([], body={"errors": [{"message": "nope"}]}), answer([], body={"data": {"menu": {}}}), answer([], body=["x"])):
            with mock.patch.object(marleyspoon.requests, "request", return_value=reply):
                with self.assertRaises(marleyspoon.MarleySpoonError):
                    marleyspoon.get_menu("2026-W44", refresh=True)

    def test_an_unreachable_marley_spoon_says_so_in_our_words(self):
        with mock.patch.object(marleyspoon.requests, "request", side_effect=marleyspoon.requests.ConnectionError("secret detail")):
            with self.assertRaises(marleyspoon.MarleySpoonError) as caught:
                marleyspoon.get_menu("2026-W44")
        self.assertNotIn("secret", str(caught.exception))

    def test_an_invalid_week_is_refused_before_any_request(self):
        with mock.patch.object(marleyspoon.requests, "request", side_effect=AssertionError("geen verzoek")):
            for week in ("2026-W00", "2026-44", "../x"):
                with self.assertRaises(ValueError, msg=week):
                    marleyspoon.get_menu(week)

    def test_search_uses_only_the_menus_we_already_have(self):
        with mock.patch.object(marleyspoon.requests, "request", return_value=answer([recipe(SLUG, "Bulgurbowl"), recipe(SLUG_B, "Butter chicken", subtitle="met rijst")])):
            marleyspoon.get_menu("2026-W44")
        with mock.patch.object(marleyspoon.requests, "request", side_effect=AssertionError("geen verzoek")):
            found = marleyspoon.search("BUTTER rijst")
            self.assertEqual((found["total"], found["weeks"], found["recipes"][0]["week"]), (1, 1, "2026-W44"))
            self.assertEqual(marleyspoon.search("pizza")["total"], 0)
            with self.assertRaises(ValueError):
                marleyspoon.search("  ")


class RoutesTest(TempDataDirTest):
    def setUp(self):
        super().setUp()
        self.client = create_app().test_client()

    def test_menu_and_search_routes(self):
        with mock.patch.object(marleyspoon.requests, "request", return_value=answer([recipe(SLUG, "Bulgurbowl")])):
            body = self.client.get("/api/marleyspoon/menu?week=2026-W44").get_json()
        self.assertEqual(body["recipes"][0]["key"], SLUG)
        self.assertEqual(self.client.get("/api/marleyspoon/search?q=bulgur").get_json()["total"], 1)
        self.assertEqual(self.client.get("/api/marleyspoon/menu?week=nope").status_code, 400)
        self.assertEqual(self.client.get("/api/marleyspoon/search").status_code, 400)

    def test_a_menu_we_cannot_read_says_so_in_our_words(self):
        with mock.patch.object(marleyspoon.requests, "request", return_value=answer([], status=503)):
            reply = self.client.get("/api/marleyspoon/menu?week=2026-W44")
        self.assertEqual(reply.status_code, 424)
        self.assertIn("503", reply.get_json()["error"])

    def test_import_sends_a_schema_org_recipe_to_mealie(self):
        with mock.patch.object(mealie, "ensure_configured"), \
                mock.patch.object(mealie, "mealie_url", return_value="https://mealie.example"), \
                mock.patch.object(mealie, "group_slug", return_value="home"), \
                mock.patch.object(marleyspoon.requests, "request", side_effect=detail_requests()), \
                mock.patch.object(mealie, "import_via_jsonld", return_value="bulgurbowl") as send:
            body = self.client.post(f"/api/marleyspoon/recipes/{SLUG}/import").get_json()
        self.assertEqual(body, {"slug": "bulgurbowl", "mealieUrl": "https://mealie.example/g/home/r/bulgurbowl"})
        url, data = send.call_args.args
        self.assertEqual(url, f"https://marleyspoon.nl/menu/{SLUG}")
        self.assertEqual(data["recipeIngredient"], ["150g bulgur", "1 kleine bloemkool", "peper en zout"])

    def test_import_without_mealie_says_so_and_asks_marley_spoon_nothing(self):
        with mock.patch.object(mealie, "ensure_configured", side_effect=RuntimeError("Mealie is niet gekoppeld.")), \
                mock.patch.object(marleyspoon.requests, "request", side_effect=AssertionError("niet ophalen")):
            reply = self.client.post(f"/api/marleyspoon/recipes/{SLUG}/import")
        self.assertEqual((reply.status_code, reply.get_json()), (400, {"error": "Mealie is niet gekoppeld."}))

    def test_an_invalid_key_is_refused_before_any_request(self):
        with mock.patch.object(mealie, "ensure_configured"), mock.patch.object(marleyspoon.requests, "request", side_effect=AssertionError("geen verzoek")):
            reply = self.client.post("/api/marleyspoon/recipes/zonder-id/import")
        self.assertEqual((reply.status_code, reply.get_json()), (400, {"error": "Geen geldig recept"}))

    def test_a_refusal_is_retried_smaller_never_by_page_and_the_answer_of_mealie_stays_out(self):
        refused = mealie.MealieImportError("Mealie import mislukt (400): geheim", 400)
        with mock.patch.object(mealie, "ensure_configured"), mock.patch.object(mealie, "recipe_page_url", return_value="u"), \
                mock.patch.object(marleyspoon.requests, "request", side_effect=detail_requests() * 2), \
                mock.patch.object(mealie, "import_via_jsonld", side_effect=[refused, "slim"]) as send, \
                mock.patch.object(mealie, "import_via_url") as by_page:
            self.assertEqual(self.client.post(f"/api/marleyspoon/recipes/{SLUG}/import").get_json()["slug"], "slim")
            self.assertIn("image", send.call_args_list[0].args[1])
            self.assertNotIn("image", send.call_args_list[1].args[1])
            with mock.patch.object(mealie, "import_via_jsonld", side_effect=refused):
                reply = self.client.post(f"/api/marleyspoon/recipes/{SLUG}/import")
        by_page.assert_not_called()
        self.assertEqual((reply.status_code, reply.get_json()), (424, {"error": "Importeren in Mealie mislukt (Mealie antwoordde met status 400)"}))
        self.assertNotIn("geheim", reply.get_data(as_text=True))

    def test_a_successful_and_a_failed_import_are_in_the_audit_log_without_the_token(self):
        with mock.patch.object(mealie, "ensure_configured"), mock.patch.object(mealie, "recipe_page_url", return_value="u"), \
                mock.patch.object(marleyspoon.requests, "request", side_effect=detail_requests() * 2), \
                mock.patch.object(mealie, "import_via_jsonld", side_effect=["pasta", KeyError("x")]):
            self.client.post(f"/api/marleyspoon/recipes/{SLUG}/import")
            self.client.post(f"/api/marleyspoon/recipes/{SLUG}/import")
        rows = audit.list_entries(service="marleyspoon", query="Mealie import")["entries"]
        self.assertEqual(sorted(row["ok"] for row in rows), [False, True])
        self.assertEqual({row["url"] for row in rows}, {f"https://marleyspoon.nl/menu/{SLUG}"})
        self.assertNotIn(TOKEN, json.dumps([audit.get_entry(row["id"]) for row in rows]))


if __name__ == "__main__":
    unittest.main()
