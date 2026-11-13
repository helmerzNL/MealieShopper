"""Bladeren door je recepten in Mealie met filters (issue #322): de tags, categorieën, bereidingstijd, datum en waardering in de
receptencache (recipe_nutrition.py), het zoeken met die filters, de lijsten met tags en categorieën, en de route."""

import os
import time
from unittest import mock

import httpx

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

from helpers import routes, sent_params  # noqa: E402
from test_recipe_nutrition import LIST, NutritionCase, page, recipe  # noqa: E402

from mealieshopper import mealie, recipe_nutrition  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402


def tag(slug, name=None):
    return {"id": f"id-{slug}", "slug": slug, "name": name or slug.capitalize()}


def dish(number, name, *, tags=(), categories=(), minutes=None, created="", rating=None, kcal=None, **extra):
    """Een recept zoals Mealie het geeft, met tags, categorieën, een tijd, een datum en een waardering."""
    return recipe(
        number,
        name,
        kcal,
        tags=[tag(slug) for slug in tags],
        recipeCategory=[tag(slug) for slug in categories],
        totalTime=f"{minutes} minutes" if minutes else None,
        createdAt=created,
        rating=rating,
        **extra,
    )


class MinutesTest(NutritionCase):
    def test_the_total_time_or_else_preparation_plus_cooking(self):
        self.assertEqual(mealie.recipe_minutes({"totalTime": "1 hour 15 minutes"}), 75)
        self.assertEqual(mealie.recipe_minutes({"prepTime": "15 minutes", "cookTime": "PT30M"}), 45)
        self.assertEqual(mealie.recipe_minutes({"prepTime": "10 min", "performTime": "20 min"}), 30, "de oventijd telt als er geen kooktijd is")
        self.assertEqual(mealie.recipe_minutes({"cookTime": "25"}), 25)
        self.assertIsNone(mealie.recipe_minutes({"totalTime": "", "prepTime": None}))
        self.assertEqual(mealie.recipe_card({"slug": "s", "prepTime": "5 min", "cookTime": "10 min"})["minutes"], 15, "de kaart toont dezelfde tijd")


class CacheFactsTest(NutritionCase):
    async def test_a_recipe_from_mealie_keeps_its_tags_categories_time_date_and_rating(self):
        await recipe_nutrition.remember(
            dish(1, "Pasta pesto", tags=["pasta", "vega"], categories=["diner"], minutes=20, created="2026-10-01T10:00:00", rating=4)
        )
        async with recipe_nutrition._connect() as conn:
            rows = await conn.execute_fetchall("SELECT tags, categories, minutes, created_at, rating FROM mealie_recipe_nutrition")
        facts = recipe_nutrition._row_facts(rows[0])
        self.assertEqual([item["slug"] for item in facts["tags"]], ["pasta", "vega"])
        self.assertEqual(facts["categories"], [{"slug": "diner", "name": "Diner"}])
        self.assertEqual((facts["minutes"], facts["created_at"], facts["rating"]), (20, "2026-10-01T10:00:00", 4.0))

    async def test_a_round_updates_the_tags_from_the_list_without_fetching_every_recipe(self):
        self.add(dish(1, "Pasta pesto", tags=["pasta"], minutes=20), dish(2, "Curry", tags=["rijst"]))
        with routes() as api:
            self.mock_mealie(api)
            await self.refresh()
            self.details.clear()
            # In Mealie hernoemd en een tijd erbij gezet, zonder dat `updatedAt` van het recept verandert.
            self.add(dish(1, "Pasta pesto", tags=["pasta", "snel"], minutes=15), dish(2, "Curry", tags=["rijst"], minutes=40))
            await self.refresh()
        self.assertEqual(self.details, [], "de lijst van Mealie heeft de tags en tijden al")
        found = await recipe_nutrition.facets(wait=0)
        self.assertEqual([(item["value"], item["count"]) for item in found["tags"]], [("pasta", 1), ("rijst", 1), ("snel", 1)])
        self.assertEqual(found["withTime"], 2)

    async def test_a_row_from_before_the_tags_makes_the_next_search_read_the_list(self):
        await recipe_nutrition.remember(dish(1, "Pasta pesto", tags=["pasta"]))
        async with recipe_nutrition._connect() as conn:
            await conn.execute("UPDATE mealie_recipe_nutrition SET tags = NULL")
            await conn.execute(
                "INSERT INTO mealie_recipe_nutrition_meta (id, base, synced_at) VALUES (1, ?, ?)",
                (mealie.normalize_url(os.environ["MEALIE_URL"]), time.time()),
            )
            await conn.commit()
        self.add(dish(1, "Pasta pesto", tags=["pasta"]))
        with routes() as api:
            self.mock_mealie(api)
            found = await recipe_nutrition.search("", {}, wanted=recipe_nutrition.Filters(tags=("pasta",)))
        self.assertEqual([r["name"] for r in found["recipes"]], ["Pasta pesto"], "de tags zijn na de ronde weer bekend")

    async def test_the_nightly_round_only_with_mealie_linked(self):
        with mock.patch.object(recipe_nutrition, "start_refresh") as start:
            self.assertTrue(await recipe_nutrition.refresh_nightly())
            with mock.patch.dict(os.environ, {"MEALIE_URL": "", "MEALIE_API_TOKEN": ""}):
                self.assertFalse(await recipe_nutrition.refresh_nightly())
        self.assertEqual(start.call_count, 1)


class FilterTest(NutritionCase):
    def setUp(self):
        super().setUp()
        self.add(
            dish(1, "Pasta pesto", tags=["pasta", "vega"], categories=["diner"], minutes=20, created="2026-09-01", rating=3, kcal=550),
            dish(2, "Lasagne", tags=["pasta"], categories=["diner"], minutes=90, created="2026-10-05", rating=5, kcal=700),
            dish(3, "Groentesoep", tags=["vega", "soep"], categories=["lunch"], minutes=None, created="2026-11-01", kcal=200),
            dish(4, "Pasta carbonara", tags=["pasta"], categories=["diner"], minutes=25, created="", rating=4, kcal=650),
        )

    async def found(self, query="", limits=None, **wanted):
        with routes() as api:
            self.mock_mealie(api, search_order=wanted.pop("order", None))
            answer = await recipe_nutrition.search(query, limits or {}, wanted=recipe_nutrition.Filters(**wanted))
        return answer, [r["name"] for r in answer["recipes"]]

    async def test_every_chosen_tag_must_be_on_the_recipe(self):
        _, names = await self.found(tags=("pasta", "vega"))
        self.assertEqual(names, ["Pasta pesto"], "pasta plus vegetarisch is vegetarische pasta")
        _, names = await self.found(tags=("pasta",))
        self.assertEqual(names, ["Lasagne", "Pasta carbonara", "Pasta pesto"])

    async def test_a_hidden_tag_or_category_drops_the_recipe(self):
        _, names = await self.found(tags=("pasta",), tags_out=("vega",))
        self.assertEqual(names, ["Lasagne", "Pasta carbonara"])
        _, names = await self.found(categories_out=("diner",))
        self.assertEqual(names, ["Groentesoep"])
        _, names = await self.found(categories=("lunch",))
        self.assertEqual(names, ["Groentesoep"])

    async def test_a_maximum_time_and_the_recipes_without_a_time(self):
        answer, names = await self.found(max_minutes=30)
        self.assertEqual(names, ["Pasta carbonara", "Pasta pesto"])
        self.assertEqual(answer["withoutTime"], 1, "de soep heeft geen tijd in Mealie")
        answer, _ = await self.found(tags=("pasta",))
        self.assertEqual(answer["withoutTime"], 0, "zonder grens op de tijd telt dat niet")

    async def test_sorting_by_newest_rating_and_name(self):
        _, names = await self.found(sort="newest")
        self.assertEqual(names, ["Groentesoep", "Lasagne", "Pasta pesto", "Pasta carbonara"], "zonder datum achteraan")
        _, names = await self.found(sort="rating")
        self.assertEqual(names, ["Lasagne", "Pasta carbonara", "Pasta pesto", "Groentesoep"], "zonder waardering achteraan")
        _, names = await self.found(sort="name", tags=("vega",))
        self.assertEqual(names, ["Groentesoep", "Pasta pesto"])

    async def test_with_a_query_mealie_decides_what_matches_and_the_filters_still_apply(self):
        order = ["recept-4", "recept-2", "recept-1"]
        _, names = await self.found("pasta", tags=("pasta",), max_minutes=60, order=order)
        self.assertEqual(names, ["Pasta carbonara", "Pasta pesto"], "de volgorde van Mealie: de beste treffer bovenaan")
        _, names = await self.found("pasta", sort="name", tags=("pasta",), order=order)
        self.assertEqual(names, ["Lasagne", "Pasta carbonara", "Pasta pesto"], "op naam als je dat kiest")

    async def test_together_with_the_nutrition_limits(self):
        answer, names = await self.found(limits={"kcal": (None, 600)}, tags=("pasta",))
        self.assertEqual(names, ["Pasta pesto"])
        self.assertEqual(answer["withoutValues"], 0)

    async def test_the_lists_count_the_recipes_per_tag_and_category(self):
        with routes() as api:
            self.mock_mealie(api)
            found = await recipe_nutrition.facets()
        self.assertEqual(found["tags"][0], {"value": "pasta", "label": "Pasta", "count": 3})
        self.assertEqual([item["value"] for item in found["tags"]], ["pasta", "vega", "soep"], "op aantal, dan op naam")
        self.assertEqual([(item["value"], item["count"]) for item in found["categories"]], [("diner", 3), ("lunch", 1)])
        self.assertEqual((found["recipes"], found["withTime"], found["indexing"]), (4, 3, None))

    def test_filters_from_a_request(self):
        wanted = recipe_nutrition.filters({"tags": "pasta, vega,pasta", "tagsOut": "", "categories": "diner", "maxMinutes": "30", "sort": "rating"})
        self.assertEqual(wanted, recipe_nutrition.Filters(tags=("pasta", "vega"), categories=("diner",), max_minutes=30, sort="rating"))
        self.assertFalse(recipe_nutrition.filters({}).active())
        self.assertEqual(recipe_nutrition.filters({"sort": "willekeurig", "maxMinutes": "0"}), recipe_nutrition.Filters())
        self.assertEqual(recipe_nutrition.filters({"maxMinutes": "99999"}).max_minutes, recipe_nutrition.MAX_MINUTES)
        with self.assertRaises(ValueError):
            recipe_nutrition.filters({"maxMinutes": "snel"})


class BrowseRouteTest(NutritionCase):
    def setUp(self):
        super().setUp()
        self.add(dish(1, "Pasta pesto", tags=["pasta", "vega"], minutes=20), dish(2, "Lasagne", tags=["pasta"], minutes=90))
        self.client = self.client_for(create_app())

    async def test_without_a_query_or_filters_mealie_gives_all_recipes_by_name(self):
        with routes() as api:
            listing = api.get(LIST).mock(return_value=page([self.recipes["recept-2"], self.recipes["recept-1"]]))
            answer = await self.client.get("/api/mealie/recipes?q=&page=1")
        self.assertEqual(answer.status_code, 200)
        self.assertEqual([r["name"] for r in answer.get_json()["recipes"]], ["Lasagne", "Pasta pesto"])
        params = sent_params(listing.calls[-1])
        self.assertEqual((params["orderBy"], params["orderDirection"], params["page"]), ("name", "asc", "1"))
        self.assertNotIn("search", params)

    async def test_tags_and_a_time_go_through_the_cache(self):
        with routes() as api:
            self.mock_mealie(api)
            answer = (await self.client.get("/api/mealie/recipes?tags=pasta&tags=vega")).get_json()
            self.assertEqual([r["name"] for r in answer["recipes"]], ["Pasta pesto"], "herhaald werkt ook")
            answer = (await self.client.get("/api/mealie/recipes?tags=pasta&maxMinutes=45&sort=newest")).get_json()
        self.assertEqual([r["name"] for r in answer["recipes"]], ["Pasta pesto"])
        self.assertEqual(answer["withoutTime"], 0)

    async def test_the_facets_route(self):
        with routes() as api:
            self.mock_mealie(api)
            answer = await self.client.get("/api/mealie/recipes/facets")
        self.assertEqual(answer.status_code, 200)
        self.assertEqual([(item["value"], item["count"]) for item in answer.get_json()["tags"]], [("pasta", 2), ("vega", 1)])

    async def test_a_time_that_is_not_a_number(self):
        with routes() as api:
            anything = api.route()
            answer = await self.client.get("/api/mealie/recipes?maxMinutes=snel")
        self.assertEqual(answer.status_code, 400)
        self.assertFalse(anything.called)

    async def test_without_mealie_the_facets_say_so(self):
        with mock.patch.dict(os.environ, {"MEALIE_URL": "", "MEALIE_API_TOKEN": ""}), routes() as api:
            anything = api.route().mock(return_value=httpx.Response(500))
            answer = await self.client.get("/api/mealie/recipes/facets")
        self.assertEqual(answer.status_code, 424)
        self.assertIn("Mealie", answer.get_json()["error"])
        self.assertFalse(anything.called)
