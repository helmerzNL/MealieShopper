"""Het weekmenu plannen vanuit MealieShopper: elke wijziging gaat meteen naar Mealie (write-through)."""

import asyncio
import os
import tempfile
import unittest
from unittest import mock

import httpx

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

from mealieshopper import auth, mealie  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402
from helpers import AppTestCase, routes, run, sent_json, sent_params, sent_url  # noqa: E402

URL = "https://mealie.example.nl"
TOKEN = "mealie-token"
RECIPE_ID = "6f1c8d1e-2b3a-4c5d-8e9f-0a1b2c3d4e5f"
GROUP_ID = "11111111-2222-3333-4444-555555555555"
USER_ID = "66666666-7777-8888-9999-000000000000"


PLANS = f"{URL}/api/households/mealplans"
ABOUT = f"{URL}/api/app/about"


def response(status=200, body=None):
    return httpx.Response(status, json=body)


def about(version):
    return response(body={"version": version})


class MealPlanTestCase(AppTestCase):
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
        mealie._group_slug_cache.clear()
        self.addCleanup(mealie._group_slug_cache.clear)
        mealie._week_start_cache.clear()
        self.addCleanup(mealie._week_start_cache.clear)
        self.client = self.client_for(create_app())


class CreateTest(MealPlanTestCase):
    async def test_plan_a_recipe(self):
        created = {"id": 7, "date": "2026-11-03", "entryType": "dinner", "recipeId": RECIPE_ID}
        with routes() as api:
            post = api.post(PLANS).mock(return_value=response(201, created))
            answer = await self.client.post("/api/mealplan", json={"date": "2026-11-03", "entryType": "dinner", "recipeId": RECIPE_ID})
        self.assertEqual(answer.status_code, 201)
        self.assertEqual(answer.get_json()["id"], 7)
        self.assertEqual(sent_json(post.calls.last), {"date": "2026-11-03", "entryType": "dinner", "title": "", "text": "", "recipeId": RECIPE_ID})
        self.assertEqual(post.calls.last.request.headers["Authorization"], f"Bearer {TOKEN}")

    async def test_a_slug_becomes_the_recipe_id(self):
        # Na een import kent de app alleen de slug; Mealie wil het id.
        with routes() as api:
            get = api.get(f"{URL}/api/recipes/pasta-pesto").mock(return_value=response(body={"id": RECIPE_ID, "slug": "pasta-pesto"}))
            post = api.post(PLANS).mock(return_value=response(201, {"id": 8}))
            answer = await self.client.post("/api/mealplan", json={"date": "2026-11-04", "recipeSlug": "pasta-pesto"})
        self.assertEqual(answer.status_code, 201)
        self.assertTrue(get.called)
        self.assertEqual(sent_json(post.calls.last)["recipeId"], RECIPE_ID)
        self.assertEqual(sent_json(post.calls.last)["entryType"], "dinner", "zonder keuze is het diner")

    async def test_plan_a_note(self):
        with routes() as api:
            post = api.post(PLANS).mock(return_value=response(201, {"id": 9}))
            answer = await self.client.post("/api/mealplan", json={"date": "2026-11-05", "entryType": "lunch", "title": "  Uit   eten ", "text": "Pizzeria\nom 18 uur"})
        self.assertEqual(answer.status_code, 201)
        self.assertEqual(sent_json(post.calls.last), {"date": "2026-11-05", "entryType": "lunch", "title": "Uit eten", "text": "Pizzeria\nom 18 uur", "recipeId": None})

    async def test_invalid_input_never_reaches_mealie(self):
        cases = [
            {"date": "2026-11-05"},  # geen recept en geen titel
            {"date": "morgen", "title": "Restjes"},
            {"date": "2026-02-30", "title": "Restjes"},
            {"date": "2026-11-05", "entryType": "brunch", "title": "Restjes"},
            {"date": "2026-11-05", "recipeId": "geen-uuid"},
            {"date": "2026-11-05", "recipeSlug": "../../api/users"},
        ]
        with routes() as api:
            anything = api.route()
            for body in cases:
                answer = await self.client.post("/api/mealplan", json=body)
                self.assertEqual(answer.status_code, 400, body)
                self.assertTrue(answer.get_json()["error"], body)
        self.assertFalse(anything.called)

    async def test_snack_drink_and_dessert_need_mealie_3_7(self):
        with routes() as api:
            api.get(ABOUT).mock(return_value=about("v2.8.0"))
            post = api.post(PLANS)
            answer = await self.client.post("/api/mealplan", json={"date": "2026-11-05", "entryType": "snack", "title": "Fruit"})
        self.assertEqual(answer.status_code, 400)
        self.assertIn("3.7", answer.get_json()["error"])
        self.assertFalse(post.called)
        mealie._version_cache.clear()
        with routes() as api:
            api.get(ABOUT).mock(return_value=about("v3.7.0"))
            post = api.post(PLANS).mock(return_value=response(201, {"id": 10}))
            answer = await self.client.post("/api/mealplan", json={"date": "2026-11-05", "entryType": "snack", "title": "Fruit"})
        self.assertEqual(answer.status_code, 201)
        self.assertEqual(sent_json(post.calls.last)["entryType"], "snack")

    async def test_options_follow_the_mealie_version(self):
        with routes() as api:
            about_route = api.get(ABOUT).mock(return_value=about("v3.12.0"))
            self.assertEqual((await self.client.get("/api/mealplan/options")).get_json()["entryTypes"],
                             ["breakfast", "lunch", "dinner", "side", "snack", "drink", "dessert"])
        self.assertNotIn("Authorization", about_route.calls.last.request.headers, "de versie vraagt geen token")
        mealie._version_cache.clear()
        with routes() as api:
            api.get(ABOUT).mock(side_effect=httpx.ConnectError("weg"))
            self.assertEqual((await self.client.get("/api/mealplan/options")).get_json()["entryTypes"], ["breakfast", "lunch", "dinner", "side"])

    def test_the_version_is_remembered(self):
        with routes() as api:
            get = api.get(ABOUT).mock(return_value=about("v3.7.0"))
            run(mealie.entry_types())
            run(mealie.entry_types())
        self.assertEqual(get.call_count, 1)

    def test_the_version_does_not_follow_a_redirect(self):
        with routes() as api:
            api.get(ABOUT).mock(return_value=httpx.Response(302, headers={"Location": "https://elders.example/about"}))
            elsewhere = api.get("https://elders.example/about").mock(return_value=about("v3.7.0"))
            self.assertIsNone(run(mealie.version()))
        self.assertFalse(elsewhere.called)

    async def test_a_refusal_from_mealie_is_readable(self):
        refusal = response(422, {"detail": [{"loc": ["body", "recipeId"], "msg": "Input should be a valid UUID", "type": "uuid_parsing"}]})
        with routes() as api:
            api.post(PLANS).mock(return_value=refusal)
            answer = await self.client.post("/api/mealplan", json={"date": "2026-11-05", "title": "Restjes"})
        self.assertEqual(answer.status_code, 424)
        self.assertIn("(422)", answer.get_json()["error"])
        self.assertIn("valid UUID", answer.get_json()["error"])
        with routes() as api:
            api.post(PLANS).mock(return_value=response(401, {"detail": "Not authenticated"}))
            answer = await self.client.post("/api/mealplan", json={"date": "2026-11-05", "title": "Restjes"})
        self.assertIn("token", answer.get_json()["error"])

    async def test_without_mealie_nothing_is_sent(self):
        with mock.patch.dict(os.environ, {"MEALIE_URL": "", "MEALIE_API_TOKEN": ""}), routes() as api:
            anything = api.route()
            answer = await self.client.post("/api/mealplan", json={"date": "2026-11-05", "title": "Restjes"})
        self.assertEqual(answer.status_code, 424)
        self.assertIn("niet gekoppeld", answer.get_json()["error"])
        self.assertFalse(anything.called)


class WeekTest(MealPlanTestCase):
    async def test_a_note_without_a_recipe_does_not_break_the_week(self):
        # Mealie geeft bij een notitie "recipe": null; dat liet het hele weekmenu eerder mislukken.
        week = {"items": [
            {"id": 1, "date": "2026-11-02", "entryType": "dinner", "title": "Uit eten", "text": "", "recipeId": None, "recipe": None},
            {"id": 2, "date": "2026-11-03", "entryType": "dinner", "title": "", "recipeId": RECIPE_ID, "recipe": {"id": RECIPE_ID, "slug": "pasta", "name": "Pasta", "image": "v1"}},
        ]}
        recipe = {"id": RECIPE_ID, "slug": "pasta", "name": "Pasta", "image": "v1", "recipeIngredient": []}

        with routes() as api:
            plans = api.get(PLANS).mock(return_value=response(body=week))
            api.get(f"{URL}/api/recipes/pasta").mock(return_value=response(body=recipe))
            api.get(f"{URL}/api/users/self").mock(return_value=response(body={"groupSlug": None}))
            answer = await self.client.get("/api/mealplan?start=2026-11-02&end=2026-11-08")
        self.assertEqual(sent_params(plans.calls.last), {"start_date": "2026-11-02", "end_date": "2026-11-08", "perPage": "50"})
        self.assertEqual(answer.status_code, 200)
        entries = answer.get_json()
        self.assertEqual([(e["id"], e["recipeDetail"] and e["recipeDetail"]["name"]) for e in entries], [(1, None), (2, "Pasta")])
        self.assertIsNone(entries[0]["imageUrl"])
        self.assertIsNone(entries[0]["servings"], "een notitie heeft geen porties")

    async def test_a_recipe_brings_its_time_and_link_for_the_recipe_window(self):
        # Een tik op een maaltijd opent het recept in een venster: met de bereidingstijd en de link naar Mealie erbij.
        week = {"items": [
            {"id": 1, "date": "2026-11-02", "entryType": "dinner", "title": "Uit eten", "text": "", "recipeId": None, "recipe": None},
            {"id": 2, "date": "2026-11-03", "entryType": "dinner", "title": "", "recipeId": RECIPE_ID, "recipe": {"id": RECIPE_ID, "slug": "pasta", "name": "Pasta"}},
        ]}
        recipe = {"id": RECIPE_ID, "slug": "pasta", "name": "Pasta", "totalTime": "1 hour 30 minutes",
                  "recipeIngredient": [], "recipeInstructions": [{"text": "Kook de pasta."}]}

        with routes() as api, mock.patch.object(mealie, "group_slug", new_callable=mock.AsyncMock, return_value="thuis"):
            api.get(PLANS).mock(return_value=response(body=week))
            api.get(f"{URL}/api/recipes/pasta").mock(return_value=response(body=recipe))
            entries = (await self.client.get("/api/mealplan?start=2026-11-02&end=2026-11-08")).get_json()
        note, meal = entries
        self.assertEqual((meal["recipeMinutes"], meal["recipeUrl"]), (90, f"{URL}/g/thuis/r/pasta"))
        self.assertEqual(meal["recipeDetail"]["recipeInstructions"], [{"text": "Kook de pasta."}])
        self.assertEqual((note["recipeMinutes"], note["recipeUrl"]), (None, None), "een notitie heeft geen recept")

    async def test_a_recipe_mealie_could_not_give_keeps_its_link(self):
        week = {"items": [{"id": 2, "date": "2026-11-03", "entryType": "dinner", "recipeId": RECIPE_ID, "recipe": {"id": RECIPE_ID, "slug": "pasta", "name": "Pasta"}}]}

        with routes() as api, mock.patch.object(mealie, "group_slug", new_callable=mock.AsyncMock, return_value=None):
            api.get(PLANS).mock(return_value=response(body=week))
            api.get(f"{URL}/api/recipes/pasta").mock(return_value=response(500, {"detail": "kapot"}))
            entry = (await self.client.get("/api/mealplan?start=2026-11-02&end=2026-11-08")).get_json()[0]
        self.assertIsNone(entry["recipeDetail"])
        self.assertEqual((entry["recipeMinutes"], entry["recipeUrl"]), (None, f"{URL}/recipe/pasta"))


    def test_recipes_are_fetched_eight_at_a_time_in_the_order_of_the_week(self):
        slugs = [f"recept-{number:02d}" for number in range(12)]
        week = {"items": [
            {"id": number, "date": "2026-11-03", "entryType": "dinner", "recipe": {"id": RECIPE_ID, "slug": slug, "name": slug}}
            for number, slug in enumerate(slugs)
        ]}
        state = {"active": 0, "most": 0}

        async def recipe(request: httpx.Request) -> httpx.Response:
            slug = request.url.path.rsplit("/", 1)[1]
            state["active"] += 1
            state["most"] = max(state["most"], state["active"])
            try:
                for _ in range(30):  # hooguit 0,3 s wachten tot de pool vol is
                    if state["active"] >= mealie.RECIPE_WORKERS:
                        break
                    await asyncio.sleep(0.01)
                await asyncio.sleep(0.01)
            finally:
                state["active"] -= 1
            if slug == "recept-03":
                return response(500, {"detail": "kapot"})
            return response(body={"id": RECIPE_ID, "slug": slug, "name": slug.upper(), "recipeIngredient": []})

        with routes() as api, mock.patch.object(mealie, "group_slug", new_callable=mock.AsyncMock, return_value=None):
            api.get(PLANS).mock(return_value=response(body=week))
            fetched = api.get(url__startswith=f"{URL}/api/recipes/").mock(side_effect=recipe)
            entries = run(mealie.get_meal_plan_with_recipes("2026-11-02", "2026-11-08"))
        self.assertEqual(mealie.RECIPE_WORKERS, 8)
        self.assertEqual(state["most"], mealie.RECIPE_WORKERS, "acht recepten tegelijk, niet meer en niet minder")
        self.assertEqual(fetched.call_count, 12)
        expected = [None if slug == "recept-03" else slug.upper() for slug in slugs]
        self.assertEqual([entry["recipeDetail"] and entry["recipeDetail"]["name"] for entry in entries], expected,
                         "de volgorde van de week, en een recept dat niet lukt heeft geen details")


SPAGHETTI = {"quantity": 400, "unit": {"name": "gram", "abbreviation": "g", "useAbbreviation": True}, "food": {"name": "spaghetti"}, "note": "", "display": "400 g spaghetti"}


class ServingsTest(MealPlanTestCase):
    """Issue #159: de porties uit de planner van Mealie (`recipeScale`, sinds v3.29.0) rekenen de hoeveelheden om."""

    async def week(self, *scales, servings=2):
        items = []
        for number, scale in enumerate(scales, start=1):
            item = {"id": number, "date": f"2026-11-0{number + 1}", "entryType": "dinner", "recipeId": RECIPE_ID,
                    "recipe": {"id": RECIPE_ID, "slug": "pasta", "name": "Pasta", "recipeServings": servings}}
            if scale is not ...:
                item["recipeScale"] = scale
            items.append(item)
        recipe = {"id": RECIPE_ID, "slug": "pasta", "name": "Pasta", "recipeServings": servings, "recipeIngredient": [dict(SPAGHETTI)]}

        with routes() as api, mock.patch.object(mealie, "group_slug", new_callable=mock.AsyncMock, return_value=None):
            api.get(PLANS).mock(return_value=response(body={"items": items}))
            api.get(f"{URL}/api/recipes/pasta").mock(return_value=response(body=recipe))
            answer = await self.client.get("/api/mealplan?start=2026-11-02&end=2026-11-08")
        self.assertEqual(answer.status_code, 200)
        return answer.get_json()

    async def test_planned_servings_scale_the_ingredients(self):
        entry = (await self.week(2))[0]
        self.assertEqual(entry["servings"], {"scale": 2.0, "base": 2, "planned": 4})
        self.assertEqual([(i["quantity"], i["display"]) for i in entry["recipeDetail"]["recipeIngredient"]], [(800, "800 g spaghetti")])

    async def test_the_same_recipe_twice_with_another_scale(self):
        # Mealie haalt het recept één keer op; elke maaltijd krijgt een eigen kopie, met haar eigen schaal.
        first, second, third = await self.week(1.5, 0.5, 1)
        self.assertEqual([e["servings"]["planned"] for e in (first, second, third)], [3, 1, 2])
        self.assertEqual([e["recipeDetail"]["recipeIngredient"][0]["quantity"] for e in (first, second, third)], [600, 200, 400])

    async def test_older_mealie_or_odd_values_mean_scale_one(self):
        for scale in (..., 0, -2, "x", True, None):
            entry = (await self.week(scale))[0]
            self.assertEqual(entry["servings"], {"scale": 1.0, "base": 2, "planned": 2}, scale)
            self.assertEqual(entry["recipeDetail"]["recipeIngredient"][0]["display"], "400 g spaghetti", scale)

    async def test_a_recipe_without_servings(self):
        entry = (await self.week(1.5, servings=None))[0]
        self.assertEqual(entry["servings"], {"scale": 1.5, "base": None, "planned": None})
        self.assertEqual(entry["recipeDetail"]["recipeIngredient"][0]["quantity"], 600)

    async def test_odd_servings_are_rounded(self):
        self.assertEqual((await self.week(1 / 3, servings=4))[0]["servings"]["planned"], 1.3)


class UpdateDeleteTest(MealPlanTestCase):
    CURRENT = {
        "id": 7, "groupId": GROUP_ID, "userId": USER_ID, "householdId": "h1", "date": "2026-11-03", "entryType": "dinner",
        "title": "", "text": "", "recipeId": RECIPE_ID, "recipe": {"id": RECIPE_ID, "slug": "pasta", "name": "Pasta"}, "updatedAt": "2026-11-01T10:00:00",
    }

    async def test_move_to_another_day_keeps_the_rest(self):
        with routes() as api:
            get = api.get(f"{PLANS}/7").mock(return_value=response(body=dict(self.CURRENT)))
            put = api.put(f"{PLANS}/7").mock(return_value=response(body={**self.CURRENT, "date": "2026-11-06"}))
            answer = await self.client.patch("/api/mealplan/7", json={"date": "2026-11-06", "entryType": "lunch"})
        self.assertEqual(answer.status_code, 200)
        self.assertTrue(get.called)
        sent = sent_json(put.calls.last)
        # Mealie wil het hele item terug, met id, groep en gebruiker; wat alleen gelezen wordt, blijft weg.
        self.assertEqual(sent, {"id": 7, "groupId": GROUP_ID, "userId": USER_ID, "date": "2026-11-06", "entryType": "lunch",
                                "title": "", "text": "", "recipeId": RECIPE_ID})

    async def test_the_servings_from_mealie_stay(self):
        # Mealie v3.29 zet een PUT zonder `recipeScale` terug op 1: de porties moeten dus mee terug.
        current = {**self.CURRENT, "recipeScale": 2.0}
        with routes() as api:
            api.get(f"{PLANS}/7").mock(return_value=response(body=current))
            put = api.put(f"{PLANS}/7").mock(return_value=response(body=current))
            await self.client.patch("/api/mealplan/7", json={"date": "2026-11-06"})
            await self.client.patch("/api/mealplan/7", json={"date": "2026-11-06", "recipeId": RECIPE_ID.upper()})
        self.assertEqual([sent_json(call)["recipeScale"] for call in put.calls], [2.0, 2.0], "zelfde recept: zelfde porties")

    async def test_another_recipe_or_a_note_starts_at_scale_one(self):
        current = {**self.CURRENT, "recipeScale": 2.0}
        other = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        with routes() as api:
            api.get(f"{PLANS}/7").mock(return_value=response(body=current))
            put = api.put(f"{PLANS}/7").mock(return_value=response(body=current))
            await self.client.patch("/api/mealplan/7", json={"recipeId": other})
            await self.client.patch("/api/mealplan/7", json={"title": "Uit eten", "recipeId": None})
        self.assertEqual([sent_json(call)["recipeScale"] for call in put.calls], [1.0, 1.0])

    async def test_an_older_mealie_gets_no_scale_field(self):
        with routes() as api:
            api.get(f"{PLANS}/7").mock(return_value=response(body=dict(self.CURRENT)))
            put = api.put(f"{PLANS}/7").mock(return_value=response(body={}))
            await self.client.patch("/api/mealplan/7", json={"recipeId": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"})
        self.assertNotIn("recipeScale", sent_json(put.calls.last))

    async def test_a_recipe_becomes_a_note(self):
        with routes() as api:
            api.get(f"{PLANS}/7").mock(return_value=response(body=dict(self.CURRENT)))
            put = api.put(f"{PLANS}/7").mock(return_value=response(body={}))
            answer = await self.client.patch("/api/mealplan/7", json={"title": "Uit eten", "recipeId": None})
        self.assertEqual(answer.status_code, 200)
        self.assertEqual((sent_json(put.calls.last)["recipeId"], sent_json(put.calls.last)["title"]), (None, "Uit eten"))

    async def test_removing_the_recipe_without_a_title_is_refused(self):
        with routes() as api:
            api.get(f"{PLANS}/7").mock(return_value=response(body=dict(self.CURRENT)))
            put = api.put(f"{PLANS}/7")
            answer = await self.client.patch("/api/mealplan/7", json={"recipeId": None})
        self.assertEqual(answer.status_code, 400)
        self.assertFalse(put.called)

    async def test_unknown_meal(self):
        with routes() as api:
            api.get(f"{PLANS}/99").mock(return_value=response(404, {"detail": {"message": "Not found"}}))
            put = api.put(f"{PLANS}/99")
            answer = await self.client.patch("/api/mealplan/99", json={"date": "2026-11-06"})
        self.assertEqual(answer.status_code, 424)
        self.assertIn("Not found", answer.get_json()["error"])
        self.assertFalse(put.called)

    async def test_delete(self):
        with routes() as api:
            delete = api.delete(f"{PLANS}/7").mock(return_value=response(body=dict(self.CURRENT)))
            answer = await self.client.delete("/api/mealplan/7")
        self.assertEqual((answer.status_code, answer.get_json()), (200, {"id": 7, "deleted": True}))
        self.assertEqual(sent_url(delete.calls.last), f"{URL}/api/households/mealplans/7")
        self.assertEqual((await self.client.delete("/api/mealplan/abc")).status_code, 404, "alleen een getal is een maaltijd")
        with self.assertRaises(ValueError):
            await mealie.delete_meal_plan_entry(0)


class SurpriseTest(MealPlanTestCase):
    async def test_mealie_picks_a_recipe(self):
        with routes() as api:
            post = api.post(f"{PLANS}/random").mock(return_value=response(200, {"id": 11, "recipe": {"name": "Curry"}}))
            answer = await self.client.post("/api/mealplan/random", json={"date": "2026-11-07", "entryType": "dinner"})
        self.assertEqual(answer.status_code, 201)
        self.assertEqual(sent_json(post.calls.last), {"date": "2026-11-07", "entryType": "dinner"})

    async def test_no_recipe_matches_the_rules(self):
        with routes() as api:
            api.post(f"{PLANS}/random").mock(return_value=response(404, {"detail": {"message": "No recipes match your rules"}}))
            answer = await self.client.post("/api/mealplan/random", json={"date": "2026-11-07"})
        self.assertEqual(answer.status_code, 424)
        self.assertIn("planregels", answer.get_json()["error"])

    async def test_invalid_day(self):
        with routes() as api:
            anything = api.route()
            self.assertEqual((await self.client.post("/api/mealplan/random", json={"date": ""})).status_code, 400)
        self.assertFalse(anything.called)


class RecipeSearchTest(MealPlanTestCase):
    PAGE = {
        "page": 1, "per_page": 24, "total": 30, "total_pages": 2,
        "items": [
            {"id": RECIPE_ID, "slug": "pasta-pesto", "name": "Pasta pesto", "image": "abc1", "totalTime": "1 hour 15 minutes",
             "recipeServings": 4.0, "rating": 4, "tags": [{"name": "Pasta"}, {"name": "Snel"}, {"name": "Vega"}, {"name": "Zomer"}]},
            {"id": "x", "name": "Zonder slug"},
        ],
    }

    async def test_search_gives_cards_like_the_stores(self):
        with routes() as api:
            get = api.get(f"{URL}/api/recipes").mock(return_value=response(body=self.PAGE))
            answer = await self.client.get("/api/mealie/recipes?q=pasta&page=1")
        self.assertEqual(answer.status_code, 200)
        data = answer.get_json()
        self.assertEqual((data["total"], data["page"], data["more"]), (30, 1, True))
        self.assertEqual(data["recipes"], [{
            "id": "pasta-pesto", "slug": "pasta-pesto", "recipeId": RECIPE_ID, "name": "Pasta pesto",
            "image": f"/api/mealie/recipes/{RECIPE_ID}/image?v=abc1", "minutes": 75, "portions": 4, "rating": 4, "tags": ["Pasta", "Snel", "Vega"],
        }])
        self.assertEqual(sent_params(get.calls.last), {"page": "1", "perPage": "24", "search": "pasta"}, "Mealie sorteert zoekresultaten zelf")

    async def test_without_a_query_all_recipes_by_name(self):
        with routes() as api:
            get = api.get(f"{URL}/api/recipes").mock(return_value=response(body={**self.PAGE, "page": 2}))
            data = (await self.client.get("/api/mealie/recipes?page=2")).get_json()
        self.assertEqual(sent_params(get.calls.last), {"page": "2", "perPage": "24", "orderBy": "name", "orderDirection": "asc"})
        self.assertFalse(data["more"], "pagina 2 van 2 is de laatste")

    async def test_search_failure(self):
        with routes() as api:
            api.get(f"{URL}/api/recipes").mock(return_value=response(500, {"detail": "kapot"}))
            answer = await self.client.get("/api/mealie/recipes?q=pasta")
        self.assertEqual(answer.status_code, 424)
        self.assertIn("(500)", answer.get_json()["error"])

    async def test_detail(self):
        recipe = {
            "id": RECIPE_ID, "slug": "pasta-pesto", "name": "Pasta pesto", "image": "abc1", "description": " Snel en groen. ",
            "recipeServings": 2, "totalTime": "20 min",
            "recipeIngredient": [{"display": "200 g pasta", "quantity": 200, "unit": {"name": "g"}, "food": {"name": "pasta"}}, {}],
            "recipeInstructions": [{"text": "Kook de pasta."}, {"text": "  "}, {"text": "Roer de pesto erdoor."}],
        }
        with routes() as api, mock.patch.object(mealie, "group_slug", new_callable=mock.AsyncMock, return_value="thuis"):
            api.get(f"{URL}/api/recipes/pasta-pesto").mock(return_value=response(body=recipe))
            answer = await self.client.get("/api/mealie/recipes/pasta-pesto")
        self.assertEqual(answer.status_code, 200)
        data = answer.get_json()
        self.assertEqual((data["name"], data["portions"], data["minutes"], data["description"]), ("Pasta pesto", 2, 20, "Snel en groen."))
        self.assertEqual(data["ingredients"], [{"line": "200 g pasta"}])
        self.assertEqual(data["steps"], ["Kook de pasta.", "Roer de pesto erdoor."])
        self.assertEqual(data["url"], f"{URL}/g/thuis/r/pasta-pesto")

    async def test_detail_refuses_odd_slugs(self):
        with routes() as api:
            anything = api.route()
            for slug in ("..", "a.b", "-pasta", "a" * 201):
                self.assertIn((await self.client.get(f"/api/mealie/recipes/{slug}")).status_code, (400, 404), slug)
        self.assertFalse(anything.called)

    def test_minutes_from_free_text(self):
        cases = {"1 hour 30 minutes": 90, "45 min": 45, "PT30M": 30, "PT1H": 60, "2 uur 15 minuten": 135, "30": 30, "": None, "even": None, "0": None}
        for text, minutes in cases.items():
            self.assertEqual(mealie._minutes(text), minutes, text)


class AccessTest(MealPlanTestCase):
    async def test_every_user_may_plan(self):
        # Net als recepten importeren: plannen en zoeken in Mealie mag iedereen die is ingelogd, niet alleen de beheerder.
        for path in ("/api/mealplan", "/api/mealplan/7", "/api/mealplan/random", "/api/mealplan/options", "/api/mealie/recipes", "/api/mealie/recipes/pasta"):
            self.assertFalse(auth.admin_only(path), path)
        regular = {"id": "anna-id", "username": "anna"}  # niet auth.OWNER_ID: een gewone gebruiker
        with mock.patch.object(auth, "auth_required", new_callable=mock.AsyncMock, return_value=True), mock.patch.object(auth, "current_user", new_callable=mock.AsyncMock, return_value=regular), \
                routes() as api:
            api.post(PLANS).mock(return_value=response(201, {"id": 12}))
            answer = await self.client.post("/api/mealplan", json={"date": "2026-11-05", "title": "Restjes"})
        self.assertEqual(answer.status_code, 201)

    async def test_not_logged_in_is_refused(self):
        with mock.patch.object(auth, "auth_required", new_callable=mock.AsyncMock, return_value=True), mock.patch.object(auth, "current_user", new_callable=mock.AsyncMock, return_value=None), \
                routes() as api:
            anything = api.route()
            answer = await self.client.post("/api/mealplan", json={"date": "2026-11-05", "title": "Restjes"})
        self.assertEqual(answer.status_code, 401)
        self.assertFalse(anything.called)


if __name__ == "__main__":
    unittest.main()
