"""Het weekmenu plannen vanuit MealieShopper: elke wijziging gaat meteen naar Mealie (write-through)."""

import json
import os
import tempfile
import unittest
from unittest import mock

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

from mealieshopper import auth, mealie  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402

URL = "https://mealie.example.nl"
TOKEN = "mealie-token"
RECIPE_ID = "6f1c8d1e-2b3a-4c5d-8e9f-0a1b2c3d4e5f"
GROUP_ID = "11111111-2222-3333-4444-555555555555"
USER_ID = "66666666-7777-8888-9999-000000000000"


def response(status=200, body=None):
    r = mock.Mock(status_code=status, ok=200 <= status < 300, text=json.dumps(body))
    r.json.return_value = body
    return r


def about(version):
    return response(body={"version": version})


class MealPlanTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = {"MEALIESHOPPER_DATA_DIR": tmp.name, "MEALIESHOPPER_AUTH_SECRET": "test-secret", "MEALIE_URL": URL, "MEALIE_API_TOKEN": TOKEN}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        auth._INITIALIZED = False
        mealie._version_cache.clear()
        self.addCleanup(mealie._version_cache.clear)
        self.client = create_app().test_client()


class CreateTest(MealPlanTestCase):
    def test_plan_a_recipe(self):
        created = {"id": 7, "date": "2026-11-03", "entryType": "dinner", "recipeId": RECIPE_ID}
        with mock.patch.object(mealie.requests, "post", return_value=response(201, created)) as post:
            answer = self.client.post("/api/mealplan", json={"date": "2026-11-03", "entryType": "dinner", "recipeId": RECIPE_ID})
        self.assertEqual(answer.status_code, 201)
        self.assertEqual(answer.get_json()["id"], 7)
        self.assertEqual(post.call_args.args[0], f"{URL}/api/households/mealplans")
        self.assertEqual(post.call_args.kwargs["json"], {"date": "2026-11-03", "entryType": "dinner", "title": "", "text": "", "recipeId": RECIPE_ID})
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], f"Bearer {TOKEN}")

    def test_a_slug_becomes_the_recipe_id(self):
        # Na een import kent de app alleen de slug; Mealie wil het id.
        with mock.patch.object(mealie.requests, "get", return_value=response(body={"id": RECIPE_ID, "slug": "pasta-pesto"})) as get, \
                mock.patch.object(mealie.requests, "post", return_value=response(201, {"id": 8})) as post:
            answer = self.client.post("/api/mealplan", json={"date": "2026-11-04", "recipeSlug": "pasta-pesto"})
        self.assertEqual(answer.status_code, 201)
        self.assertEqual(get.call_args.args[0], f"{URL}/api/recipes/pasta-pesto")
        self.assertEqual(post.call_args.kwargs["json"]["recipeId"], RECIPE_ID)
        self.assertEqual(post.call_args.kwargs["json"]["entryType"], "dinner", "zonder keuze is het diner")

    def test_plan_a_note(self):
        with mock.patch.object(mealie.requests, "post", return_value=response(201, {"id": 9})) as post:
            answer = self.client.post("/api/mealplan", json={"date": "2026-11-05", "entryType": "lunch", "title": "  Uit   eten ", "text": "Pizzeria\nom 18 uur"})
        self.assertEqual(answer.status_code, 201)
        self.assertEqual(post.call_args.kwargs["json"], {"date": "2026-11-05", "entryType": "lunch", "title": "Uit eten", "text": "Pizzeria\nom 18 uur", "recipeId": None})

    def test_invalid_input_never_reaches_mealie(self):
        cases = [
            {"date": "2026-11-05"},  # geen recept en geen titel
            {"date": "morgen", "title": "Restjes"},
            {"date": "2026-02-30", "title": "Restjes"},
            {"date": "2026-11-05", "entryType": "brunch", "title": "Restjes"},
            {"date": "2026-11-05", "recipeId": "geen-uuid"},
            {"date": "2026-11-05", "recipeSlug": "../../api/users"},
        ]
        with mock.patch.object(mealie.requests, "post") as post, mock.patch.object(mealie.requests, "get") as get:
            for body in cases:
                answer = self.client.post("/api/mealplan", json=body)
                self.assertEqual(answer.status_code, 400, body)
                self.assertTrue(answer.get_json()["error"], body)
        post.assert_not_called()
        get.assert_not_called()

    def test_snack_drink_and_dessert_need_mealie_3_7(self):
        with mock.patch.object(mealie.requests, "get", return_value=about("v2.8.0")), mock.patch.object(mealie.requests, "post") as post:
            answer = self.client.post("/api/mealplan", json={"date": "2026-11-05", "entryType": "snack", "title": "Fruit"})
        self.assertEqual(answer.status_code, 400)
        self.assertIn("3.7", answer.get_json()["error"])
        post.assert_not_called()
        mealie._version_cache.clear()
        with mock.patch.object(mealie.requests, "get", return_value=about("v3.7.0")), \
                mock.patch.object(mealie.requests, "post", return_value=response(201, {"id": 10})) as post:
            answer = self.client.post("/api/mealplan", json={"date": "2026-11-05", "entryType": "snack", "title": "Fruit"})
        self.assertEqual(answer.status_code, 201)
        self.assertEqual(post.call_args.kwargs["json"]["entryType"], "snack")

    def test_options_follow_the_mealie_version(self):
        with mock.patch.object(mealie.requests, "get", return_value=about("v3.12.0")):
            self.assertEqual(self.client.get("/api/mealplan/options").get_json()["entryTypes"],
                             ["breakfast", "lunch", "dinner", "side", "snack", "drink", "dessert"])
        mealie._version_cache.clear()
        with mock.patch.object(mealie.requests, "get", side_effect=mealie.requests.ConnectionError("weg")):
            self.assertEqual(self.client.get("/api/mealplan/options").get_json()["entryTypes"], ["breakfast", "lunch", "dinner", "side"])

    def test_the_version_is_remembered(self):
        with mock.patch.object(mealie.requests, "get", return_value=about("v3.7.0")) as get:
            mealie.entry_types()
            mealie.entry_types()
        self.assertEqual(get.call_count, 1)

    def test_a_refusal_from_mealie_is_readable(self):
        refusal = response(422, {"detail": [{"loc": ["body", "recipeId"], "msg": "Input should be a valid UUID", "type": "uuid_parsing"}]})
        with mock.patch.object(mealie.requests, "post", return_value=refusal):
            answer = self.client.post("/api/mealplan", json={"date": "2026-11-05", "title": "Restjes"})
        self.assertEqual(answer.status_code, 424)
        self.assertIn("(422)", answer.get_json()["error"])
        self.assertIn("valid UUID", answer.get_json()["error"])
        with mock.patch.object(mealie.requests, "post", return_value=response(401, {"detail": "Not authenticated"})):
            answer = self.client.post("/api/mealplan", json={"date": "2026-11-05", "title": "Restjes"})
        self.assertIn("token", answer.get_json()["error"])

    def test_without_mealie_nothing_is_sent(self):
        with mock.patch.dict(os.environ, {"MEALIE_URL": "", "MEALIE_API_TOKEN": ""}), mock.patch.object(mealie.requests, "post") as post:
            answer = self.client.post("/api/mealplan", json={"date": "2026-11-05", "title": "Restjes"})
        self.assertEqual(answer.status_code, 424)
        self.assertIn("niet gekoppeld", answer.get_json()["error"])
        post.assert_not_called()


class WeekTest(MealPlanTestCase):
    def test_a_note_without_a_recipe_does_not_break_the_week(self):
        # Mealie geeft bij een notitie "recipe": null; dat liet het hele weekmenu eerder mislukken.
        week = {"items": [
            {"id": 1, "date": "2026-11-02", "entryType": "dinner", "title": "Uit eten", "text": "", "recipeId": None, "recipe": None},
            {"id": 2, "date": "2026-11-03", "entryType": "dinner", "title": "", "recipeId": RECIPE_ID, "recipe": {"id": RECIPE_ID, "slug": "pasta", "name": "Pasta", "image": "v1"}},
        ]}
        recipe = {"id": RECIPE_ID, "slug": "pasta", "name": "Pasta", "image": "v1", "recipeIngredient": []}

        def get(url, **kwargs):
            return response(body=week if url.endswith("/mealplans") else recipe)

        with mock.patch.object(mealie.requests, "get", side_effect=get):
            answer = self.client.get("/api/mealplan?start=2026-11-02&end=2026-11-08")
        self.assertEqual(answer.status_code, 200)
        entries = answer.get_json()
        self.assertEqual([(e["id"], e["recipeDetail"] and e["recipeDetail"]["name"]) for e in entries], [(1, None), (2, "Pasta")])
        self.assertIsNone(entries[0]["imageUrl"])


class UpdateDeleteTest(MealPlanTestCase):
    CURRENT = {
        "id": 7, "groupId": GROUP_ID, "userId": USER_ID, "householdId": "h1", "date": "2026-11-03", "entryType": "dinner",
        "title": "", "text": "", "recipeId": RECIPE_ID, "recipe": {"id": RECIPE_ID, "slug": "pasta", "name": "Pasta"}, "updatedAt": "2026-11-01T10:00:00",
    }

    def test_move_to_another_day_keeps_the_rest(self):
        with mock.patch.object(mealie.requests, "get", return_value=response(body=dict(self.CURRENT))) as get, \
                mock.patch.object(mealie.requests, "put", return_value=response(body={**self.CURRENT, "date": "2026-11-06"})) as put:
            answer = self.client.patch("/api/mealplan/7", json={"date": "2026-11-06", "entryType": "lunch"})
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(get.call_args.args[0], f"{URL}/api/households/mealplans/7")
        self.assertEqual(put.call_args.args[0], f"{URL}/api/households/mealplans/7")
        sent = put.call_args.kwargs["json"]
        # Mealie wil het hele item terug, met id, groep en gebruiker; wat alleen gelezen wordt, blijft weg.
        self.assertEqual(sent, {"id": 7, "groupId": GROUP_ID, "userId": USER_ID, "date": "2026-11-06", "entryType": "lunch",
                                "title": "", "text": "", "recipeId": RECIPE_ID})

    def test_a_recipe_becomes_a_note(self):
        with mock.patch.object(mealie.requests, "get", return_value=response(body=dict(self.CURRENT))), \
                mock.patch.object(mealie.requests, "put", return_value=response(body={})) as put:
            answer = self.client.patch("/api/mealplan/7", json={"title": "Uit eten", "recipeId": None})
        self.assertEqual(answer.status_code, 200)
        self.assertEqual((put.call_args.kwargs["json"]["recipeId"], put.call_args.kwargs["json"]["title"]), (None, "Uit eten"))

    def test_removing_the_recipe_without_a_title_is_refused(self):
        with mock.patch.object(mealie.requests, "get", return_value=response(body=dict(self.CURRENT))), mock.patch.object(mealie.requests, "put") as put:
            answer = self.client.patch("/api/mealplan/7", json={"recipeId": None})
        self.assertEqual(answer.status_code, 400)
        put.assert_not_called()

    def test_unknown_meal(self):
        with mock.patch.object(mealie.requests, "get", return_value=response(404, {"detail": {"message": "Not found"}})), mock.patch.object(mealie.requests, "put") as put:
            answer = self.client.patch("/api/mealplan/99", json={"date": "2026-11-06"})
        self.assertEqual(answer.status_code, 424)
        self.assertIn("Not found", answer.get_json()["error"])
        put.assert_not_called()

    def test_delete(self):
        with mock.patch.object(mealie.requests, "delete", return_value=response(body=dict(self.CURRENT))) as delete:
            answer = self.client.delete("/api/mealplan/7")
        self.assertEqual((answer.status_code, answer.get_json()), (200, {"id": 7, "deleted": True}))
        self.assertEqual(delete.call_args.args[0], f"{URL}/api/households/mealplans/7")
        self.assertEqual(self.client.delete("/api/mealplan/abc").status_code, 404, "alleen een getal is een maaltijd")
        with self.assertRaises(ValueError):
            mealie.delete_meal_plan_entry(0)


class SurpriseTest(MealPlanTestCase):
    def test_mealie_picks_a_recipe(self):
        with mock.patch.object(mealie.requests, "post", return_value=response(200, {"id": 11, "recipe": {"name": "Curry"}})) as post:
            answer = self.client.post("/api/mealplan/random", json={"date": "2026-11-07", "entryType": "dinner"})
        self.assertEqual(answer.status_code, 201)
        self.assertEqual(post.call_args.args[0], f"{URL}/api/households/mealplans/random")
        self.assertEqual(post.call_args.kwargs["json"], {"date": "2026-11-07", "entryType": "dinner"})

    def test_no_recipe_matches_the_rules(self):
        with mock.patch.object(mealie.requests, "post", return_value=response(404, {"detail": {"message": "No recipes match your rules"}})):
            answer = self.client.post("/api/mealplan/random", json={"date": "2026-11-07"})
        self.assertEqual(answer.status_code, 424)
        self.assertIn("planregels", answer.get_json()["error"])

    def test_invalid_day(self):
        with mock.patch.object(mealie.requests, "post") as post:
            self.assertEqual(self.client.post("/api/mealplan/random", json={"date": ""}).status_code, 400)
        post.assert_not_called()


class RecipeSearchTest(MealPlanTestCase):
    PAGE = {
        "page": 1, "per_page": 24, "total": 30, "total_pages": 2,
        "items": [
            {"id": RECIPE_ID, "slug": "pasta-pesto", "name": "Pasta pesto", "image": "abc1", "totalTime": "1 hour 15 minutes",
             "recipeServings": 4.0, "rating": 4, "tags": [{"name": "Pasta"}, {"name": "Snel"}, {"name": "Vega"}, {"name": "Zomer"}]},
            {"id": "x", "name": "Zonder slug"},
        ],
    }

    def test_search_gives_cards_like_the_stores(self):
        with mock.patch.object(mealie.requests, "get", return_value=response(body=self.PAGE)) as get:
            answer = self.client.get("/api/mealie/recipes?q=pasta&page=1")
        self.assertEqual(answer.status_code, 200)
        data = answer.get_json()
        self.assertEqual((data["total"], data["page"], data["more"]), (30, 1, True))
        self.assertEqual(data["recipes"], [{
            "id": "pasta-pesto", "slug": "pasta-pesto", "recipeId": RECIPE_ID, "name": "Pasta pesto",
            "image": f"/api/mealie/recipes/{RECIPE_ID}/image?v=abc1", "minutes": 75, "portions": 4, "rating": 4, "tags": ["Pasta", "Snel", "Vega"],
        }])
        self.assertEqual(get.call_args.args[0], f"{URL}/api/recipes")
        self.assertEqual(get.call_args.kwargs["params"], {"page": 1, "perPage": 24, "search": "pasta"}, "Mealie sorteert zoekresultaten zelf")

    def test_without_a_query_all_recipes_by_name(self):
        with mock.patch.object(mealie.requests, "get", return_value=response(body={**self.PAGE, "page": 2})) as get:
            data = self.client.get("/api/mealie/recipes?page=2").get_json()
        self.assertEqual(get.call_args.kwargs["params"], {"page": 2, "perPage": 24, "orderBy": "name", "orderDirection": "asc"})
        self.assertFalse(data["more"], "pagina 2 van 2 is de laatste")

    def test_search_failure(self):
        with mock.patch.object(mealie.requests, "get", return_value=response(500, {"detail": "kapot"})):
            answer = self.client.get("/api/mealie/recipes?q=pasta")
        self.assertEqual(answer.status_code, 424)
        self.assertIn("(500)", answer.get_json()["error"])

    def test_detail(self):
        recipe = {
            "id": RECIPE_ID, "slug": "pasta-pesto", "name": "Pasta pesto", "image": "abc1", "description": " Snel en groen. ",
            "recipeServings": 2, "totalTime": "20 min",
            "recipeIngredient": [{"display": "200 g pasta", "quantity": 200, "unit": {"name": "g"}, "food": {"name": "pasta"}}, {}],
            "recipeInstructions": [{"text": "Kook de pasta."}, {"text": "  "}, {"text": "Roer de pesto erdoor."}],
        }
        with mock.patch.object(mealie.requests, "get", return_value=response(body=recipe)), mock.patch.object(mealie, "group_slug", return_value="thuis"):
            answer = self.client.get("/api/mealie/recipes/pasta-pesto")
        self.assertEqual(answer.status_code, 200)
        data = answer.get_json()
        self.assertEqual((data["name"], data["portions"], data["minutes"], data["description"]), ("Pasta pesto", 2, 20, "Snel en groen."))
        self.assertEqual(data["ingredients"], [{"line": "200 g pasta"}])
        self.assertEqual(data["steps"], ["Kook de pasta.", "Roer de pesto erdoor."])
        self.assertEqual(data["url"], f"{URL}/g/thuis/r/pasta-pesto")

    def test_detail_refuses_odd_slugs(self):
        with mock.patch.object(mealie.requests, "get") as get:
            for slug in ("..", "a.b", "-pasta", "a" * 201):
                self.assertIn(self.client.get(f"/api/mealie/recipes/{slug}").status_code, (400, 404), slug)
        get.assert_not_called()

    def test_minutes_from_free_text(self):
        cases = {"1 hour 30 minutes": 90, "45 min": 45, "PT30M": 30, "PT1H": 60, "2 uur 15 minuten": 135, "30": 30, "": None, "even": None, "0": None}
        for text, minutes in cases.items():
            self.assertEqual(mealie._minutes(text), minutes, text)


class AccessTest(MealPlanTestCase):
    def test_every_user_may_plan(self):
        # Net als recepten importeren: plannen en zoeken in Mealie mag iedereen die is ingelogd, niet alleen de beheerder.
        for path in ("/api/mealplan", "/api/mealplan/7", "/api/mealplan/random", "/api/mealplan/options", "/api/mealie/recipes", "/api/mealie/recipes/pasta"):
            self.assertFalse(auth.admin_only(path), path)
        regular = {"id": "anna-id", "username": "anna"}  # niet auth.OWNER_ID: een gewone gebruiker
        with mock.patch.object(auth, "auth_required", return_value=True), mock.patch.object(auth, "current_user", return_value=regular), \
                mock.patch.object(mealie.requests, "post", return_value=response(201, {"id": 12})):
            answer = self.client.post("/api/mealplan", json={"date": "2026-11-05", "title": "Restjes"})
        self.assertEqual(answer.status_code, 201)

    def test_not_logged_in_is_refused(self):
        with mock.patch.object(auth, "auth_required", return_value=True), mock.patch.object(auth, "current_user", return_value=None), \
                mock.patch.object(mealie.requests, "post") as post:
            answer = self.client.post("/api/mealplan", json={"date": "2026-11-05", "title": "Restjes"})
        self.assertEqual(answer.status_code, 401)
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
