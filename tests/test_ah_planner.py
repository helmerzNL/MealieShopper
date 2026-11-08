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


class CleanDayTest(unittest.TestCase):
    def test_valid_day_without_duplicates_and_with_the_defaults(self):
        body = {
            "date": "2026-11-09",
            "recipeIds": [12, "12", 13],
            "products": [{"productId": 4242, "title": "  AH   Stokbrood "}, {"productId": "4242", "title": "dubbel"}, {"productId": 7}],
            "note": " Uit  eten ",
        }
        self.assertEqual(
            ah_planner.clean_day(body),
            {
                "date": "2026-11-09",
                "recipeIds": [12, 13],
                "products": [{"productId": 4242, "title": "AH Stokbrood"}, {"productId": 7, "title": ""}],
                "note": "Uit eten",
                "combine": True,
                "splitMeals": True,
            },
        )
        day = ah_planner.clean_day({"date": "2026-11-09", "products": [{"productId": 1, "title": "x" * 300}], "combine": False, "splitMeals": False})
        self.assertEqual((day["combine"], day["splitMeals"], len(day["products"][0]["title"])), (False, False, ah_planner.TEXT_MAX))

    def test_invalid_input_is_refused(self):
        for body in (
            None,
            {},
            {"date": "2026-11-09"},
            {"date": "2026-11-09", "recipeIds": [], "products": []},
            {"date": "morgen", "recipeIds": [12]},
            {"date": "2026-11-09", "recipeIds": "12"},
            {"date": "2026-11-09", "recipeIds": ["R-R12"]},
            {"date": "2026-11-09", "recipeIds": [0]},
            {"date": "2026-11-09", "recipeIds": [True]},
            {"date": "2026-11-09", "recipeIds": ["1234567890123"]},
            {"date": "2026-11-09", "recipeIds": list(range(1, ah_planner.MAX_RECIPES + 2))},
            {"date": "2026-11-09", "products": [4242]},
            {"date": "2026-11-09", "products": [{"productId": "wi4242"}]},
            {"date": "2026-11-09", "products": [{"productId": 4242, "title": 5}]},
            {"date": "2026-11-09", "products": [{"productId": n} for n in range(1, ah_planner.MAX_PRODUCTS + 2)]},
            {"date": "2026-11-09", "recipeIds": [12], "note": ["Pizza"]},
            {"date": "2026-11-09", "recipeIds": [12], "combine": "ja"},
            {"date": "2026-11-09", "recipeIds": [12], "splitMeals": 0},
        ):
            with self.subTest(body=body), self.assertRaises(ValueError):
                ah_planner.clean_day(body)


class StandaloneTest(unittest.TestCase):
    """Soep en pizza zijn een maaltijd op zich; wat een onderdeel van een maaltijd is, niet."""

    def test_soup_and_pizza(self):
        for title in ("Padifood Chinese tomatensoep", "Unox Stevige erwtensoep", "AH Soep van de dag", "Dr. Oetker Big Americans pizza Texas", "Mini pizza's", "Mini pizzas", "Unox soepen"):
            with self.subTest(title=title):
                self.assertTrue(ah_planner.standalone(title))
        for title in ("AH Soepgroente", "AH Soepballetjes", "Soepvlees", "AH Pizzadeeg", "AH Pizzasaus", "Pizzabodem", "AH Bakaardappeltjes", "AH Burger smokey 2 stuks", ""):
            with self.subTest(title=title):
                self.assertFalse(ah_planner.standalone(title))

    def test_the_planner_marks_each_product(self):
        planner = {"days": [{"date": "2026-11-12", "items": [
            {"kind": "product", "productId": 1, "title": "Unox Stevige erwtensoep"},
            {"kind": "product", "productId": 2, "title": "AH Bakaardappeltjes"},
            {"kind": "note", "text": "Soep"},
        ]}], "incomplete": False}
        with mock.patch.object(ah, "get_week_planner", return_value=planner):
            items = run(ah_planner.get())["days"][0]["items"]
        self.assertEqual([item.get("standalone") for item in items], [True, False, None])


MONDAY = [(101, "AH Burger smokey 2 stuks"), (88, "AH Bakaardappeltjes"), (95, "AH Zoete stoofpeertjes")]
PADIFOOD = (301, "Padifood Chinese tomatensoep")
UNOX = (302, "Unox Stevige erwtensoep")
PIZZA = (401, "Dr. Oetker Big Americans pizza Texas")


def product_url(number):
    return f"https://www.ah.nl/producten/product/wi{number}"


class ToMealPlanTestCase(unittest.IsolatedAsyncioTestCase):
    """Een nagebootste Mealie (bron, naam, import, weekmenu) en AH (productgegevens) voor `ah_planner.to_meal_plan`."""

    def setUp(self):
        self.plan: list[dict] = []
        self.in_mealie: dict[str, dict] = {}  # bron -> {slug, id}
        self.named: dict[str, dict] = {}  # naam -> {slug, recipeId}
        self.imported: list[str] = []
        self.made: list[tuple[str, dict]] = []  # (bron, JSON-LD)
        self.created: list[dict] = []
        self.fail_import: set[str] = set()
        self.refuse: list[int] = []  # statussen waarmee import_via_jsonld de eerstvolgende keren weigert
        self.products: dict[int, dict] = {}
        self.products_down = False
        self.search_down = False

        async def find(url, **kwargs):
            return self.in_mealie.get(url)

        async def import_from_url(url):
            if url in self.fail_import:
                raise RuntimeError("Mealie zegt nee: geheime details")
            self.imported.append(url)
            slug = "recept-" + url.rsplit("R-R", 1)[1]
            self.in_mealie[url] = {"slug": slug, "id": f"uuid-{slug}"}
            return slug

        async def import_via_jsonld(url, recipe, catalog=None):
            self.made.append((url, recipe))
            if self.refuse:
                status = self.refuse.pop(0)
                raise mealie.MealieImportError(f"Mealie import mislukt ({status}): geheime details", status)
            slug = f"eigen-{len(self.made)}"
            self.in_mealie[url] = {"slug": slug, "id": f"uuid-{slug}"}
            return slug

        async def search(query, page=1, per_page=24):
            if self.search_down:
                raise RuntimeError("Mealie weg")
            # Mealie zoekt ruim: alle recepten komen terug, de app kiest alleen een gelijke naam.
            return {"recipes": [{"slug": found["slug"], "recipeId": found["recipeId"], "name": name} for name, found in self.named.items()]}

        async def create(values):
            self.created.append(values)
            return {"id": len(self.created)}

        async def page_url(slug):
            return f"https://mealie.example.nl/g/thuis/r/{slug}"

        async def get_meal_plan(start, end):
            self.plan_range = (start, end)
            return self.plan

        async def products(ids):
            if self.products_down:
                raise RuntimeError("AH weg")
            return [self.products[number] for number in ids if number in self.products]

        for name, fake in (
            ("find_recipe_summary_by_source", find),
            ("import_from_url", import_from_url),
            ("import_via_jsonld", import_via_jsonld),
            ("search_recipes", search),
            ("create_meal_plan_entry", create),
            ("recipe_page_url", page_url),
            ("get_meal_plan", get_meal_plan),
        ):
            patcher = mock.patch.object(mealie, name, side_effect=fake)
            patcher.start()
            self.addCleanup(patcher.stop)
        for patcher in (mock.patch.object(mealie, "ensure_configured", mock.AsyncMock(return_value=None)), mock.patch.object(ah, "get_products_by_ids", side_effect=products)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def ah_has(self, *products):
        for number, title in products:
            self.products[number] = {"id": number, "title": title, "image": f"https://img/{number}.jpg", "url": product_url(number)}

    async def day(self, date="2026-11-09", recipes=(), products=(), note="", **flags):
        body = {"date": date, "recipeIds": list(recipes), "products": [{"productId": n, "title": t} for n, t in products], "note": note, **flags}
        return (await ah_planner.to_meal_plan(ah_planner.clean_day(body)))["results"]


class RecipesTest(ToMealPlanTestCase):
    """Allerhande-recepten: importeren waar nodig, niets dubbel plannen, en een fout houdt de rest niet op."""

    async def test_a_missing_recipe_is_imported_and_planned_as_dinner(self):
        results = await self.day(recipes=[12])
        self.assertEqual(self.imported, ["https://www.ah.nl/allerhande/recept/R-R12"])
        self.assertEqual(self.created, [{"date": "2026-11-09", "entryType": "dinner", "recipeSlug": "recept-12"}])
        self.assertEqual(
            results,
            [{"date": "2026-11-09", "kind": "recipe", "title": "", "recipeId": 12, "imported": True, "slug": "recept-12", "mealieUrl": "https://mealie.example.nl/g/thuis/r/recept-12", "status": "planned"}],
        )
        self.assertEqual(self.plan_range, ("2026-11-09", "2026-11-09"))

    async def test_a_recipe_already_in_mealie_is_reused(self):
        self.in_mealie["https://www.ah.nl/allerhande/recept/R-R12"] = {"slug": "pasta", "id": "uuid-pasta"}
        results = await self.day(recipes=[12])
        self.assertEqual(self.imported, [])
        self.assertEqual(self.created, [{"date": "2026-11-09", "entryType": "dinner", "recipeSlug": "pasta"}])
        self.assertFalse(results[0]["imported"])

    async def test_a_recipe_already_planned_that_day_is_not_planned_twice(self):
        self.in_mealie["https://www.ah.nl/allerhande/recept/R-R12"] = {"slug": "pasta", "id": "uuid-pasta"}
        self.in_mealie["https://www.ah.nl/allerhande/recept/R-R13"] = {"slug": "soep", "id": "uuid-soep"}
        self.plan = [{"date": "2026-11-09", "recipe": {"slug": "pasta", "id": "uuid-pasta"}}, {"date": "2026-11-09", "recipeId": "uuid-soep", "recipe": None}]
        results = await self.day(recipes=[12, 13])
        self.assertEqual([r["status"] for r in results], ["already_planned", "already_planned"])
        self.assertEqual(self.created, [])

    async def test_a_failed_import_does_not_stop_the_others(self):
        self.fail_import.add("https://www.ah.nl/allerhande/recept/R-R13")
        with self.assertLogs("mealieshopper.ah_planner", "ERROR"):
            failed, planned = await self.day(recipes=[13, 14])
        self.assertEqual(failed["status"], "failed")
        self.assertNotIn("geheime details", failed["error"])
        self.assertEqual(planned["status"], "planned")
        self.assertEqual(self.created, [{"date": "2026-11-09", "entryType": "dinner", "recipeSlug": "recept-14"}])


class ProductsTest(ToMealPlanTestCase):
    """Losse producten: een bestaand recept met die naam, een eigen recept, of samen een menu (4.3.0)."""

    async def test_monday_three_products_become_one_menu(self):
        self.ah_has(*MONDAY)
        results = await self.day(products=MONDAY)
        self.assertEqual(len(self.made), 1)
        source, recipe = self.made[0]
        self.assertEqual(source, f"{product_url(88)}#menu-88-95-101")
        self.assertEqual(recipe["name"], "AH Burger smokey 2 stuks, AH Bakaardappeltjes en AH Zoete stoofpeertjes")
        self.assertEqual(recipe["url"], source)
        self.assertEqual(recipe["recipeIngredient"], ["1 AH Burger smokey 2 stuks", "1 AH Bakaardappeltjes", "1 AH Zoete stoofpeertjes"])
        self.assertEqual(recipe["image"], ["https://img/101.jpg"])
        self.assertEqual(recipe["@type"], "Recipe")
        self.assertTrue(recipe["recipeInstructions"])
        self.assertEqual(self.created, [{"date": "2026-11-09", "entryType": "dinner", "recipeSlug": "eigen-1"}])
        self.assertEqual(
            results,
            [{"date": "2026-11-09", "kind": "menu", "title": recipe["name"], "productIds": [101, 88, 95], "imported": True, "slug": "eigen-1", "mealieUrl": "https://mealie.example.nl/g/thuis/r/eigen-1", "status": "planned"}],
        )

    async def test_without_combine_every_product_is_its_own_recipe(self):
        self.ah_has(*MONDAY)
        results = await self.day(products=MONDAY, note="Burger", combine=False)
        self.assertEqual([(source, recipe["name"]) for source, recipe in self.made], [(product_url(number), title) for number, title in MONDAY])
        self.assertEqual([r["kind"] for r in results], ["product"] * 3)
        self.assertEqual([r["title"] for r in results], [title for _, title in MONDAY])
        self.assertEqual(len(self.created), 3)

    async def test_thursday_existing_soup_is_reused_and_the_other_soup_is_its_own_recipe(self):
        self.ah_has(PADIFOOD, UNOX)
        self.named["Padifood Chinese Tomatensoep"] = {"slug": "padifood-chinese-tomatensoep", "recipeId": "uuid-padi"}
        self.named["Padifood Chinese tomatensoep met extra pit"] = {"slug": "padifood-pittig", "recipeId": "uuid-pit"}
        results = await self.day(date="2026-11-12", products=[PADIFOOD, UNOX], note="Soep")
        self.assertEqual([(r["kind"], r["title"], r["imported"], r["slug"]) for r in results], [
            ("product", "Padifood Chinese tomatensoep", False, "padifood-chinese-tomatensoep"),
            ("product", "Unox Stevige erwtensoep", True, "eigen-1"),
        ])
        self.assertEqual([(source, recipe["name"]) for source, recipe in self.made], [(product_url(302), "Unox Stevige erwtensoep")])
        self.assertEqual([c["recipeSlug"] for c in self.created], ["padifood-chinese-tomatensoep", "eigen-1"])

    async def test_thursday_without_split_the_rest_becomes_a_menu_named_after_the_note(self):
        self.ah_has(PADIFOOD, UNOX)
        self.named["Padifood Chinese tomatensoep"] = {"slug": "padifood", "recipeId": "uuid-padi"}
        results = await self.day(date="2026-11-12", products=[PADIFOOD, UNOX], note="Soep", splitMeals=False)
        self.assertEqual([(r["kind"], r["title"]) for r in results], [("product", "Padifood Chinese tomatensoep"), ("menu", "Soep")])
        self.assertEqual(self.made[0][0], f"{product_url(302)}#menu-302")
        self.assertEqual(self.made[0][1]["recipeIngredient"], ["1 Unox Stevige erwtensoep"])

    async def test_friday_the_pizza_is_its_own_recipe(self):
        self.ah_has(PIZZA)
        results = await self.day(date="2026-11-13", products=[PIZZA], note="Pizza")
        self.assertEqual([(r["kind"], r["title"]) for r in results], [("product", PIZZA[1])])
        self.assertEqual(self.made[0][0], product_url(401))

    async def test_a_day_with_a_note_burger_and_soup(self):
        self.ah_has((101, "AH Burger smokey 2 stuks"), UNOX)
        results = await self.day(products=[(101, "AH Burger smokey 2 stuks"), UNOX], note="Friet")
        self.assertEqual([(r["kind"], r["title"]) for r in results], [("product", UNOX[1]), ("menu", "Friet")])
        self.assertEqual(self.made[1][1]["recipeIngredient"], ["1 AH Burger smokey 2 stuks"])

    async def test_the_own_brand_may_be_missing_in_mealie(self):
        self.ah_has((88, "AH Bakaardappeltjes"))
        self.named["Bakaardappeltjes"] = {"slug": "bakaardappeltjes", "recipeId": "uuid-bak"}
        results = await self.day(products=[(88, "AH Bakaardappeltjes")])
        self.assertEqual((results[0]["kind"], results[0]["slug"], self.made), ("product", "bakaardappeltjes", []))

    async def test_a_menu_made_before_is_reused_and_not_planned_twice(self):
        self.ah_has(*MONDAY)
        self.in_mealie[f"{product_url(88)}#menu-88-95-101"] = {"slug": "burgermenu", "id": "uuid-burger"}
        results = await self.day(products=MONDAY)
        self.assertEqual((self.made, results[0]["imported"], results[0]["status"]), ([], False, "planned"))
        self.plan = [{"date": "2026-11-09", "recipe": {"slug": "burgermenu", "id": "uuid-burger"}}]
        results = await self.day(products=MONDAY)
        self.assertEqual(results[0]["status"], "already_planned")
        self.assertEqual(len(self.created), 1)

    async def test_without_product_details_the_titles_from_the_planner_are_used(self):
        self.products_down = True
        await self.day(products=MONDAY)
        recipe = self.made[0][1]
        self.assertEqual(recipe["recipeIngredient"][0], "1 AH Burger smokey 2 stuks")
        self.assertNotIn("image", recipe)

    async def test_a_failed_name_search_makes_an_own_recipe(self):
        self.ah_has(UNOX)
        self.search_down = True
        results = await self.day(products=[UNOX])
        self.assertEqual((results[0]["kind"], results[0]["status"]), ("product", "planned"))

    async def test_a_refused_import_is_tried_once_more_without_the_picture(self):
        self.ah_has(*MONDAY)
        self.refuse = [422]
        results = await self.day(products=MONDAY)
        self.assertEqual(["image" in recipe for _, recipe in self.made], [True, False])
        self.assertEqual(results[0]["status"], "planned")

    async def test_an_error_of_mealie_itself_is_not_tried_again_and_does_not_stop_the_recipes(self):
        self.ah_has(*MONDAY)
        self.refuse = [500]
        with self.assertLogs("mealieshopper.ah_planner", "ERROR"):
            results = await self.day(recipes=[12], products=MONDAY)
        self.assertEqual(len(self.made), 1)
        recipe, menu = results
        self.assertEqual((recipe["status"], menu["status"]), ("planned", "failed"))
        self.assertNotIn("geheime details", menu["error"])


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
        data = {"days": [{"date": "2026-11-09", "items": [{"kind": "product", "productId": 1, "title": "AH Pizza margherita"}]}], "incomplete": False}
        with mock.patch.object(ah, "get_week_planner", return_value=data):
            res = await self.client.get("/api/ah/planner")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()["days"][0]["items"][0]["standalone"], True)

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
            res = await self.client.post("/api/ah/planner/to-meal-plan", json={"date": "2026-11-09", "recipeIds": [1]})
        self.assertEqual(res.status_code, 400)
        self.assertIn("Mealie", res.get_json()["error"])

    async def test_post_with_bad_input_is_400(self):
        with mock.patch.object(mealie, "ensure_configured", new_callable=mock.AsyncMock), \
                mock.patch.object(ah_planner, "to_meal_plan", side_effect=AssertionError("niet plannen")):
            for body in ({"date": "morgen", "recipeIds": [1]}, {"items": [{"date": "2026-11-09", "recipeId": 1}]}):
                res = await self.client.post("/api/ah/planner/to-meal-plan", json=body)
                self.assertEqual(res.status_code, 400, body)

    async def test_post_plans_the_cleaned_day(self):
        answer = {"results": [{"date": "2026-11-09", "kind": "recipe", "recipeId": 1, "status": "planned"}]}
        body = {"date": "2026-11-09", "recipeIds": ["1"], "products": [{"productId": 7, "title": "AH Stokbrood"}], "note": "Soep", "combine": True, "splitMeals": False}
        with mock.patch.object(mealie, "ensure_configured", new_callable=mock.AsyncMock), \
                mock.patch.object(ah_planner, "to_meal_plan", return_value=answer) as plan:
            res = await self.client.post("/api/ah/planner/to-meal-plan", json=body)
        self.assertEqual((res.status_code, res.get_json()), (200, answer))
        plan.assert_called_once_with({"date": "2026-11-09", "recipeIds": [1], "products": [{"productId": 7, "title": "AH Stokbrood"}], "note": "Soep", "combine": True, "splitMeals": False})

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
            res = await self.client.post("/api/ah/planner/to-meal-plan", json={"date": "2026-11-09", "products": [{"productId": 1}]})
            self.assertEqual(res.status_code, 200)


if __name__ == "__main__":
    unittest.main()
