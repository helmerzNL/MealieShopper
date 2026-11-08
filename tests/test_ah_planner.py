"""De weekplanner van de AH-app: uitlezen bij AH (alleen lezen) en de Allerhande-recepten in het weekmenu van Mealie zetten."""

import os
import tempfile
import unittest
from unittest import mock

import httpx

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

from mealieshopper import ah, ah_planner, auth, mealie  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402
from helpers import AppTestCase, run, sent_json, served  # noqa: E402


def graphql(data=None, errors=None):
    body = {"data": data}
    if errors:
        body["errors"] = [{"message": message} for message in errors]
    return httpx.Response(200, json=body)


def planner(*days):
    return {"recipeWeekPlannerOverview": {"days": list(days)}}


def day(date, *items):
    return {"date": date, "items": list(items)}


def recipe(recipe_id, title="Pasta", slug="pasta-pesto"):
    return {"__typename": "WeekPlannerRecipeItem", "recipeId": recipe_id, "recipeTitle": title, "recipe": {"slug": slug}}


def summaries(*items):
    return graphql({"recipeSummaries": [{"id": n, "title": f"Recept {n}", "slug": f"recept-{n}", "images": [{"url": f"https://img/{n}.jpg"}]} for n in items]})


class ReadTest(unittest.TestCase):
    """`ah.get_week_planner`: de vraag aan AH en hoe het antwoord een planning wordt."""

    def read(self, responses, token="tok"):
        with mock.patch.object(ah, "configured_refresh_token", return_value="refresh"), \
                mock.patch.object(ah, "get_user_token", return_value=token), \
                served(side_effect=responses) as post:
            return run(ah.get_week_planner()), post

    def test_not_linked_says_where_to_link(self):
        with mock.patch.object(ah, "configured_refresh_token", return_value=""), \
                served(side_effect=AssertionError("niet bij AH vragen")):
            with self.assertRaises(LookupError) as ctx:
                run(ah.get_week_planner())
        self.assertIn("Instellingen > Koppelen > Albert Heijn", str(ctx.exception))

    def test_every_kind_of_item_is_kept(self):
        data = planner(
            day(
                "2026-11-09",
                recipe(1234, "Pasta &amp; pesto"),
                {"__typename": "WeekPlannerMemberRecipeItem", "recipeId": 77, "recipeTitle": "Oma's soep"},
                {"__typename": "WeekPlannerProductItem", "productId": 4242, "productTitle": "AH Stokbrood"},
                {"__typename": "WeekPlannerNoteItem", "text": "  Uit   eten "},
                {"__typename": "WeekPlannerSomethingNew", "recipeTitle": "Nieuw soort"},
                {"__typename": "WeekPlannerProductItem", "productTitle": "Zonder nummer"},
                None,
            )
        )
        result, post = self.read([graphql(data), summaries(1234)])
        self.assertFalse(result["incomplete"])
        self.assertEqual([d["date"] for d in result["days"]], ["2026-11-09"])
        self.assertEqual(
            result["days"][0]["items"],
            [
                {"kind": "recipe", "recipeId": 1234, "title": "Pasta & pesto", "image": "https://img/1234.jpg", "url": "https://www.ah.nl/allerhande/recept/R-R1234/pasta-pesto"},
                {"kind": "own_recipe", "recipeId": 77, "title": "Oma's soep"},
                {"kind": "product", "productId": 4242, "title": "AH Stokbrood", "url": "https://www.ah.nl/producten/product/wi4242"},
                {"kind": "note", "text": "Uit eten"},
                {"kind": "other", "title": "Nieuw soort"},
                {"kind": "other", "title": "Zonder nummer"},
                {"kind": "other", "title": ""},
            ],
        )
        # Alleen lezen: de vraag aan AH is een query, geen mutation, met het token van de koppeling.
        sent = sent_json(post.calls[0])
        self.assertIn("recipeWeekPlannerOverview", sent["query"])
        self.assertNotIn("mutation", sent["query"])
        self.assertEqual(post.calls[0].request.headers["Authorization"], "Bearer tok")
        self.assertEqual(sent_json(post.calls[1])["variables"], {"ids": [1234]})

    def test_days_come_sorted_and_a_time_after_the_date_is_dropped(self):
        data = planner(day("2026-11-10T00:00:00", recipe(2)), day("2026-11-09", recipe(1)), day("geen datum", recipe(3)), day("2026-11-11"))
        result, _ = self.read([graphql(data), summaries(1, 2)])
        self.assertEqual([(d["date"], len(d["items"])) for d in result["days"]], [("2026-11-09", 1), ("2026-11-10", 1), ("2026-11-11", 0)])

    def test_a_partial_answer_is_marked_incomplete(self):
        data = planner(day("2026-11-09", recipe(1), None), None)
        result, _ = self.read([graphql(data, errors=["Recipe 99 not found"]), summaries(1)])
        self.assertTrue(result["incomplete"])
        self.assertEqual([item["kind"] for item in result["days"][0]["items"]], ["recipe", "other"])

    def test_only_errors_is_a_failure(self):
        with self.assertRaises(RuntimeError) as ctx:
            self.read([graphql(None, errors=["Something broke"])])
        self.assertIn("Something broke", str(ctx.exception))
        with self.assertRaises(RuntimeError):
            self.read([graphql(planner(None, None), errors=["Days broke"])])

    def test_never_used_is_an_empty_planner(self):
        result, post = self.read([graphql({"recipeWeekPlannerOverview": None})])
        self.assertEqual(result, {"days": [], "incomplete": False})
        self.assertEqual(post.call_count, 1, "zonder recepten geen vraag om plaatjes")

    def test_images_are_best_effort(self):
        data = planner(day("2026-11-09", recipe(5, title="", slug=None)))
        result, _ = self.read([graphql(data), graphql(None, errors=["summaries down"])])
        item = result["days"][0]["items"][0]
        self.assertEqual((item["image"], item["title"], item["url"]), ("", "", "https://www.ah.nl/allerhande/recept/R-R5"))
        # Gaf de planner geen titel of slug, dan vullen de receptgegevens die aan.
        result, _ = self.read([graphql(data), summaries(5)])
        item = result["days"][0]["items"][0]
        self.assertEqual((item["title"], item["url"]), ("Recept 5", "https://www.ah.nl/allerhande/recept/R-R5/recept-5"))

    def test_a_strange_slug_does_not_end_up_in_the_link(self):
        data = planner(day("2026-11-09", recipe(6, slug="../../evil?x=1")))
        result, _ = self.read([graphql(data), graphql({"recipeSummaries": []})])
        self.assertEqual(result["days"][0]["items"][0]["url"], "https://www.ah.nl/allerhande/recept/R-R6")

    def test_a_rejected_token_is_tried_once_more_with_a_fresh_one(self):
        tokens = iter(["oud", "nieuw"])
        responses = [httpx.Response(401, json={"error": "invalid_token"}), graphql(planner(day("2026-11-09")))]
        with mock.patch.object(ah, "configured_refresh_token", return_value="refresh"), \
                mock.patch.object(ah, "get_user_token", side_effect=lambda: next(tokens)), \
                served(side_effect=responses) as post:
            result = run(ah.get_week_planner())
        self.assertEqual(result["days"], [{"date": "2026-11-09", "items": []}])
        self.assertEqual([call.request.headers["Authorization"] for call in post.calls], ["Bearer oud", "Bearer nieuw"])

    def test_graphql_still_fails_on_any_error(self):
        # `_graphql` gedraagt zich als voorheen: een gedeeltelijk antwoord is daar een fout.
        with served(return_value=graphql({"x": 1}, errors=["kapot"])):
            with self.assertRaises(RuntimeError) as ctx:
                run(ah._graphql("tok", "query { x }", {}, "Test"))
        self.assertIn("kapot", str(ctx.exception))


class CleanItemsTest(unittest.TestCase):
    def test_valid_items_without_duplicates(self):
        body = {"items": [{"date": "2026-11-09", "recipeId": 12}, {"date": "2026-11-09", "recipeId": "12"}, {"date": "2026-11-10", "recipeId": 12}]}
        self.assertEqual(ah_planner.clean_items(body), [{"date": "2026-11-09", "recipeId": 12}, {"date": "2026-11-10", "recipeId": 12}])

    def test_invalid_input_is_refused(self):
        for body in (
            None,
            {},
            {"items": []},
            {"items": "12"},
            {"items": [12]},
            {"items": [{"date": "morgen", "recipeId": 12}]},
            {"items": [{"date": "2026-11-09", "recipeId": "R-R12"}]},
            {"items": [{"date": "2026-11-09", "recipeId": 0}]},
            {"items": [{"date": "2026-11-09", "recipeId": "1234567890123"}]},
            {"items": [{"date": "2026-11-09", "recipeId": n} for n in range(1, ah_planner.MAX_ITEMS + 2)]},
        ):
            with self.subTest(body=body), self.assertRaises(ValueError):
                ah_planner.clean_items(body)


class ToMealPlanTest(unittest.IsolatedAsyncioTestCase):
    """`ah_planner.to_meal_plan`: importeren waar nodig, niets dubbel plannen, en een fout houdt de rest niet op."""

    def setUp(self):
        self.plan: list[dict] = []
        self.in_mealie: dict[str, dict] = {}
        self.imported: list[str] = []
        self.created: list[dict] = []
        self.fail_import: set[str] = set()

        async def find(url, **kwargs):
            return self.in_mealie.get(url)

        async def import_from_url(url):
            if url in self.fail_import:
                raise RuntimeError("Mealie zegt nee: geheime details")
            self.imported.append(url)
            slug = "recept-" + url.rsplit("R-R", 1)[1]
            self.in_mealie[url] = {"slug": slug, "id": f"uuid-{slug}"}
            return slug

        async def create(values):
            self.created.append(values)
            return {"id": len(self.created)}

        async def page_url(slug):
            return f"https://mealie.example.nl/g/thuis/r/{slug}"

        async def get_meal_plan(start, end):
            self.plan_range = (start, end)
            return self.plan

        for name, fake in (
            ("ensure_configured", mock.AsyncMock(return_value=None)),
            ("find_recipe_summary_by_source", find),
            ("import_from_url", import_from_url),
            ("create_meal_plan_entry", create),
            ("recipe_page_url", page_url),
            ("get_meal_plan", get_meal_plan),
        ):
            patcher = mock.patch.object(mealie, name, side_effect=fake) if name != "ensure_configured" else mock.patch.object(mealie, name, fake)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def test_a_missing_recipe_is_imported_and_planned_as_dinner(self):
        result = await ah_planner.to_meal_plan([{"date": "2026-11-09", "recipeId": 12}])
        self.assertEqual(self.imported, ["https://www.ah.nl/allerhande/recept/R-R12"])
        self.assertEqual(self.created, [{"date": "2026-11-09", "entryType": "dinner", "recipeSlug": "recept-12"}])
        self.assertEqual(
            result["results"],
            [{"date": "2026-11-09", "recipeId": 12, "imported": True, "slug": "recept-12", "mealieUrl": "https://mealie.example.nl/g/thuis/r/recept-12", "status": "planned"}],
        )

    async def test_a_recipe_already_in_mealie_is_reused(self):
        self.in_mealie["https://www.ah.nl/allerhande/recept/R-R12"] = {"slug": "pasta", "id": "uuid-pasta"}
        result = await ah_planner.to_meal_plan([{"date": "2026-11-09", "recipeId": 12}])
        self.assertEqual(self.imported, [])
        self.assertEqual(self.created, [{"date": "2026-11-09", "entryType": "dinner", "recipeSlug": "pasta"}])
        self.assertFalse(result["results"][0]["imported"])

    async def test_a_recipe_already_planned_that_day_is_not_planned_twice(self):
        self.in_mealie["https://www.ah.nl/allerhande/recept/R-R12"] = {"slug": "pasta", "id": "uuid-pasta"}
        self.plan = [
            {"date": "2026-11-09", "recipe": {"slug": "pasta", "id": "uuid-pasta"}},
            {"date": "2026-11-10", "recipeId": "uuid-pasta", "recipe": None},
            {"date": "2026-11-11", "title": "Uit eten", "recipe": None},
        ]
        items = [{"date": "2026-11-09", "recipeId": 12}, {"date": "2026-11-10", "recipeId": 12}, {"date": "2026-11-11", "recipeId": 12}]
        result = await ah_planner.to_meal_plan(items)
        self.assertEqual([r["status"] for r in result["results"]], ["already_planned", "already_planned", "planned"])
        self.assertEqual(self.created, [{"date": "2026-11-11", "entryType": "dinner", "recipeSlug": "pasta"}])
        self.assertEqual(self.plan_range, ("2026-11-09", "2026-11-11"))

    async def test_the_same_recipe_on_two_days_is_imported_once(self):
        result = await ah_planner.to_meal_plan([{"date": "2026-11-09", "recipeId": 12}, {"date": "2026-11-12", "recipeId": 12}])
        self.assertEqual(len(self.imported), 1)
        self.assertEqual([c["date"] for c in self.created], ["2026-11-09", "2026-11-12"])
        self.assertEqual([r["imported"] for r in result["results"]], [True, False])

    async def test_a_failed_import_does_not_stop_the_others(self):
        self.fail_import.add("https://www.ah.nl/allerhande/recept/R-R13")
        with self.assertLogs("mealieshopper.ah_planner", "ERROR"):
            result = await ah_planner.to_meal_plan([{"date": "2026-11-09", "recipeId": 13}, {"date": "2026-11-10", "recipeId": 14}])
        failed, planned = result["results"]
        self.assertEqual(failed["status"], "failed")
        self.assertNotIn("geheime details", failed["error"])
        self.assertEqual(planned["status"], "planned")
        self.assertEqual(self.created, [{"date": "2026-11-10", "entryType": "dinner", "recipeSlug": "recept-14"}])


class RouteTest(AppTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(os.environ, {"MEALIESHOPPER_DATA_DIR": tmp.name, "MEALIESHOPPER_AUTH_SECRET": "test-secret"})
        env.start()
        self.addCleanup(env.stop)
        auth._INITIALIZED = False
        self.client = self.client_for(create_app())

    async def test_get_serves_the_planner(self):
        data = {"days": [{"date": "2026-11-09", "items": []}], "incomplete": False}
        with mock.patch.object(ah, "get_week_planner", return_value=data):
            res = await self.client.get("/api/ah/planner")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json(), data)

    async def test_get_errors_are_424_and_hide_what_ah_said(self):
        with mock.patch.object(ah, "get_week_planner", side_effect=LookupError("AH account niet gekoppeld. Koppel het.")):
            res = await self.client.get("/api/ah/planner")
        self.assertEqual((res.status_code, res.get_json()["error"]), (424, "AH account niet gekoppeld. Koppel het."))
        with mock.patch.object(ah, "get_week_planner", side_effect=RuntimeError("AH zegt: interne details")), self.assertLogs("mealieshopper", "ERROR"):
            res = await self.client.get("/api/ah/planner")
        self.assertEqual(res.status_code, 424)
        self.assertNotIn("interne details", res.get_json()["error"])

    async def test_post_without_mealie_is_400(self):
        with mock.patch.object(ah_planner, "to_meal_plan", side_effect=AssertionError("niet plannen")):
            res = await self.client.post("/api/ah/planner/to-meal-plan", json={"items": [{"date": "2026-11-09", "recipeId": 1}]})
        self.assertEqual(res.status_code, 400)
        self.assertIn("Mealie", res.get_json()["error"])

    async def test_post_with_bad_input_is_400(self):
        with mock.patch.object(mealie, "ensure_configured", new_callable=mock.AsyncMock), \
                mock.patch.object(ah_planner, "to_meal_plan", side_effect=AssertionError("niet plannen")):
            res = await self.client.post("/api/ah/planner/to-meal-plan", json={"items": [{"date": "morgen", "recipeId": 1}]})
        self.assertEqual(res.status_code, 400)

    async def test_post_plans_the_cleaned_items(self):
        answer = {"results": [{"date": "2026-11-09", "recipeId": 1, "status": "planned"}]}
        with mock.patch.object(mealie, "ensure_configured", new_callable=mock.AsyncMock), \
                mock.patch.object(ah_planner, "to_meal_plan", return_value=answer) as plan:
            res = await self.client.post("/api/ah/planner/to-meal-plan", json={"items": [{"date": "2026-11-09", "recipeId": "1"}]})
        self.assertEqual((res.status_code, res.get_json()), (200, answer))
        plan.assert_called_once_with([{"date": "2026-11-09", "recipeId": 1}])

    async def test_every_user_may_read_and_plan(self):
        # Net als plannen en recepten importeren: niet alleen de beheerder.
        for path in ("/api/ah/planner", "/api/ah/planner/to-meal-plan"):
            self.assertFalse(auth.admin_only(path), path)
        regular = {"id": "anna-id", "username": "anna"}
        with mock.patch.object(auth, "auth_required", new_callable=mock.AsyncMock, return_value=True), \
                mock.patch.object(auth, "current_user", new_callable=mock.AsyncMock, return_value=regular), \
                mock.patch.object(ah, "get_week_planner", return_value={"days": [], "incomplete": False}), \
                mock.patch.object(mealie, "ensure_configured", new_callable=mock.AsyncMock), \
                mock.patch.object(ah_planner, "to_meal_plan", return_value={"results": []}):
            self.assertEqual((await self.client.get("/api/ah/planner")).status_code, 200)
            res = await self.client.post("/api/ah/planner/to-meal-plan", json={"items": [{"date": "2026-11-09", "recipeId": 1}]})
            self.assertEqual(res.status_code, 200)


if __name__ == "__main__":
    unittest.main()
