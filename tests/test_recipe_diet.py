"""Menugang, vegetarisch, vlees, vis en gevogelte voor alle bronnen van Recepten zoeken (4.21.0, vervolg op issue #322):
het afleiden uit de ingrediënten, de labels, de filters per bron, de zoekfunctie van AH en Jumbo, en de tags in Mealie."""

import json
import os
from unittest import mock

import httpx

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

from helpers import AppTestCase, routes, sent_json  # noqa: E402
from test_recipe_nutrition import URL, NutritionCase, recipe  # noqa: E402

from mealieshopper import ah, auth, jumbo_recipes, recipe_diet, recipe_nutrition  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402

D = recipe_diet


def lines(*items):
    return D.classify(items)


class ClassifyTest(AppTestCase):
    def test_vegetarian_vegan_and_the_main_proteins(self):
        self.assertEqual(lines("250 g spaghetti", "50 g Parmezaanse kaas", "1 bakje basilicum"), {"diet": "vegetarisch", "proteins": []})
        self.assertEqual(lines("400 ml kokosmelk", "2 el pindakaas", "1 blik kikkererwten"), {"diet": "veganistisch", "proteins": []})
        self.assertEqual(lines("500 g kipfilet", "1 ui"), {"diet": "", "proteins": ["gevogelte"]})
        self.assertEqual(lines("2 zalmfilets", "boter"), {"diet": "", "proteins": ["vis"]})
        self.assertEqual(lines("500 g rundergehakt", "lasagnebladen"), {"diet": "", "proteins": ["vlees"]})
        self.assertEqual(lines("150 g pancetta", "2 eieren", "1 kipfilet", "200 g garnalen"), {"diet": "", "proteins": ["gevogelte", "vis", "vlees"]})
        self.assertIsNone(D.classify([]))
        self.assertIsNone(D.classify(["", "  "]))

    def test_a_stock_or_sauce_is_not_vegetarian_but_no_protein(self):
        self.assertEqual(lines("1 kippenbouillonblokje", "500 g wortel"), {"diet": "", "proteins": []})
        self.assertEqual(lines("2 el vissaus", "rijstnoedels"), {"diet": "", "proteins": []})
        self.assertEqual(lines("4 blaadjes gelatine", "slagroom"), {"diet": "", "proteins": []})

    def test_plant_based_lines_and_words_that_only_look_like_meat_or_dairy(self):
        self.assertEqual(
            lines("250 g vegetarische kipstuckjes", "1 paprika"), {"diet": "vegetarisch", "proteins": []}, "vegetarisch, niet zeker veganistisch"
        )
        self.assertEqual(lines("200 g tofu", "sojasaus"), {"diet": "veganistisch", "proteins": []})
        self.assertEqual(lines("2 vleestomaten", "1 ui"), {"diet": "veganistisch", "proteins": []})
        self.assertEqual(lines("1 butternut squash", "1 pot boterbonen", "150 g lamsoor"), {"diet": "veganistisch", "proteins": []})
        self.assertEqual(lines("1 honingmeloen", "2 el havermelk"), {"diet": "veganistisch", "proteins": []})
        self.assertEqual(lines("4 burgerbroodjes", "sla"), {"diet": "veganistisch", "proteins": []})
        self.assertEqual(lines("2 chicken breasts", "olive oil"), {"diet": "", "proteins": ["gevogelte"]}, "Engelse recepten ook")

    def test_ingredient_lines_of_a_mealie_recipe(self):
        found = D.ingredient_lines(
            {"recipeIngredient": [{"display": "500 g kipfilet"}, {"food": {"name": "ui"}, "note": "gesnipperd"}, "zout", {"note": ""}, 3]}
        )
        self.assertEqual(found, ["500 g kipfilet", "ui gesnipperd", "zout"])


class LabelsTest(AppTestCase):
    def test_tags_categories_and_store_labels(self):
        self.assertEqual(D.from_labels([{"name": "Vegetarisch"}], "Hoofdgerecht"), {"diet": "vegetarisch", "proteins": []})
        self.assertEqual(D.from_labels(["zonder-vlees-vis"]), {"diet": "vegetarisch", "proteins": []}, "AH: zonder vlees is geen vlees")
        self.assertEqual(D.from_labels("Veggie"), {"diet": "vegetarisch", "proteins": []})
        self.assertEqual(D.from_labels(["Vegan"]), {"diet": "veganistisch", "proteins": []})
        self.assertEqual(D.from_labels(["Kip", "Diner"], [{"name": "Vis"}]), {"diet": "", "proteins": ["gevogelte", "vis"]})
        self.assertEqual(D.from_labels(None, [], ""), {"diet": "", "proteins": []})

    def test_a_vegetarian_tag_wins_and_the_tags_mealieshopper_adds(self):
        labels = D.from_labels(["Vegetarisch"])
        self.assertEqual(D.combined(labels, {"diet": "", "proteins": ["vlees"]}), {"diet": "vegetarisch", "proteins": []}, "je kent je recept")
        self.assertEqual(D.combined(D.from_labels(["Vis"]), {"diet": "", "proteins": ["vlees"]}), {"diet": "", "proteins": ["vis", "vlees"]})
        self.assertEqual(D.wanted_tags({"diet": "veganistisch", "proteins": []}, D.from_labels([])), ["Veganistisch", "Vegetarisch"])
        self.assertEqual(D.wanted_tags({"diet": "", "proteins": ["vis", "vlees"]}, D.from_labels(["Vis"])), ["Vlees"], "Vis staat er al")
        self.assertEqual(D.wanted_tags({"diet": "", "proteins": ["vlees"]}, D.from_labels(["Vegetarisch"])), [], "niet tegen je eigen tag in")
        self.assertEqual(D.wanted_tags(None, D.from_labels([])), [])

    def test_vegetarian_lets_vegan_recipes_through(self):
        vegan = {"diet": "veganistisch", "proteins": []}
        self.assertTrue(D.matches(vegan, "vegetarisch", ""))
        self.assertFalse(D.matches({"diet": "vegetarisch", "proteins": []}, "veganistisch", ""))
        self.assertTrue(D.matches({"diet": "", "proteins": ["vis"]}, "", "vis"))
        self.assertFalse(D.matches({"diet": "", "proteins": ["vis"]}, "", "vlees"))


class WantedTest(AppTestCase):
    def test_from_a_request_and_what_each_source_knows(self):
        wanted = D.wanted_from({"course": "dessert", "diet": "vegetarisch", "protein": "", "maxMinutes": "30"})
        self.assertEqual(wanted, D.Wanted(course="dessert", diet="vegetarisch", max_minutes=30))
        self.assertEqual(D.wanted_from({"course": "taart", "diet": "keto", "protein": "tofu", "maxMinutes": "0"}), D.Wanted())
        with self.assertRaises(ValueError):
            D.wanted_from({"maxMinutes": "snel"})
        self.assertTrue(D.supports("jumbo", D.Wanted(course="soep", max_minutes=30)))
        self.assertFalse(D.supports("jumbo", D.Wanted(diet="vegetarisch")))
        self.assertFalse(D.supports("picnic", D.Wanted(protein="vis")))
        self.assertTrue(D.supports("ah", D.Wanted(course="soep", diet="veganistisch", protein="", max_minutes=60)))

    def test_store_cards(self):
        jumbo = {"course": "Hoofdgerecht", "minutes": 25}
        self.assertTrue(D.card_passes("jumbo", jumbo, D.Wanted(course="hoofdgerecht", max_minutes=30)))
        self.assertFalse(D.card_passes("jumbo", jumbo, D.Wanted(max_minutes=20)))
        self.assertFalse(D.card_passes("jumbo", {"course": "Hoofdgerecht"}, D.Wanted(max_minutes=60)), "zonder tijd valt hij weg")
        self.assertTrue(
            D.card_passes("picnic", {"tags": ["Vegetarisch", "Snel"], "minutes": 20}, D.Wanted(diet="vegetarisch", course="hoofdgerecht"))
        )
        self.assertFalse(D.card_passes("picnic", {"tags": ["Snel"], "minutes": 20}, D.Wanted(diet="vegetarisch")), "zonder label geen vegetarisch")
        self.assertTrue(D.card_passes("hellofresh", {"label": "Veggie", "minutes": 35}, D.Wanted(diet="vegetarisch")))
        self.assertFalse(D.card_passes("crisp", {"tags": ["Toetje"], "minutes": 10}, D.Wanted(course="hoofdgerecht")), "een label gaat voor")
        self.assertTrue(D.card_passes("ah", {"totalTime": 25}, D.Wanted(max_minutes=30)), "AH heet het totalTime")
        cards = [{"minutes": 10}, {"minutes": 50}]
        self.assertEqual(D.filter_cards("crisp", cards, D.Wanted(max_minutes=30)), [{"minutes": 10}])
        self.assertIs(D.filter_cards("crisp", cards, D.Wanted()), cards)

    def test_the_filters_of_albert_heijn(self):
        self.assertEqual(
            D.ah_filters(D.Wanted(course="soep", diet="veganistisch", protein="", max_minutes=15)),
            [
                {"group": "soort-gerecht", "values": ["soep"]},
                {"group": "speciale-wensen", "values": ["veganistisch"]},
                {"group": "veel-gebruikt", "values": ["snel"]},
            ],
        )
        self.assertEqual(
            D.ah_filters(D.Wanted(course="dessert", protein="vis")),
            [{"group": "menugang", "values": ["nagerecht"]}, {"group": "recepten-met", "values": ["vis"]}],
        )
        self.assertEqual(D.ah_filters(D.Wanted(max_minutes=30)), [], "een ruimere tijd filteren we zelf")
        self.assertTrue(D.ah_scan_needed(D.Wanted(max_minutes=30)))
        self.assertFalse(D.ah_scan_needed(D.Wanted(max_minutes=15)))


def ah_result(cards, total):
    return httpx.Response(
        200,
        json={
            "data": {
                "recipeSearch": {
                    "page": {"total": total, "hasNextPage": True},
                    "result": [
                        {
                            "id": number,
                            "title": f"Recept {number}",
                            "slug": f"r{number}",
                            "time": {"cook": minutes, "oven": None, "wait": None},
                            "images": [],
                        }
                        for number, minutes in cards
                    ],
                }
            }
        },
    )


class AhSearchTest(AppTestCase):
    def search(self, responses, *args, **kwargs):
        with mock.patch.object(ah, "get_token", return_value="anon"), routes() as router:
            post = router.post().mock(side_effect=responses)
            result = self.run_async(ah.search_recipes(*args, **kwargs))
        return result, [sent_json(call)["variables"]["query"] for call in post.calls]

    def run_async(self, coro):
        import asyncio

        return asyncio.run(coro)

    def test_albert_heijn_filters_itself(self):
        result, sent = self.search([ah_result([(1, 15)], 87)], "pasta", 1, wanted=D.Wanted(diet="vegetarisch", max_minutes=15))
        self.assertEqual(
            sent[0]["filters"], [{"group": "speciale-wensen", "values": ["vegetarisch"]}, {"group": "veel-gebruikt", "values": ["snel"]}]
        )
        self.assertEqual((sent[0]["start"], sent[0]["size"]), (12, 12))
        self.assertEqual((result["total"], [r["id"] for r in result["recipes"]]), (87, ["1"]))

    def test_without_filters_nothing_changes(self):
        _, sent = self.search([ah_result([(1, 30)], 1)], "pasta")
        self.assertNotIn("filters", sent[0])

    def test_a_longer_time_is_filtered_by_us_over_larger_pages(self):
        first = ah_result([(1, 60), (2, 45)], 200)
        second = ah_result([(3, 25), (4, 90)], 200)
        result, sent = self.search([first, second], "pasta", 0, wanted=D.Wanted(max_minutes=30))
        self.assertEqual([query["start"] for query in sent], [0, ah.RECIPE_SCAN_SIZE], "de eerste keer kwam er niets door: nog een keer")
        self.assertEqual([r["id"] for r in result["recipes"]], ["3"])
        self.assertEqual((result["total"], result["page"], result["scanned"], result["hasNextPage"]), (None, 1, ah.RECIPE_SCAN_SIZE + 2, True))


class JumboSearchTest(AppTestCase):
    async def test_course_and_time_are_filtered_by_us_over_a_few_pages(self):
        pages = {
            0: {"recipes": [{"id": "1", "course": "Nagerecht", "minutes": 20}, {"id": "2", "course": "Hoofdgerecht", "minutes": 60}], "total": 60},
            24: {"recipes": [{"id": "3", "course": "Hoofdgerecht", "minutes": 25}], "total": 60},
            48: {"recipes": [{"id": "4", "course": "Hoofdgerecht", "minutes": 15}], "total": 60},
        }
        real = jumbo_recipes.search

        async def fake(query, offset=0, wanted=None):
            if wanted is not None:
                return await real(query, offset, wanted)
            return pages[int(offset)]

        with mock.patch.object(jumbo_recipes, "search", side_effect=fake):
            found = await jumbo_recipes.search("kip", 0, D.Wanted(course="hoofdgerecht", max_minutes=30))
        self.assertEqual([r["id"] for r in found["recipes"]], ["3", "4"])
        self.assertEqual((found["total"], found["scanned"], found["nextOffset"], found["more"]), (None, 60, 72, False))


class StoreRouteTest(AppTestCase):
    def setUp(self):
        self.client = self.client_for(create_app())

    async def test_picnic_and_crisp_keep_only_what_passes(self):
        from mealieshopper import crisp, picnic_recipes

        found = {"recipes": [{"id": "1", "tags": ["Vegetarisch"], "minutes": 20}, {"id": "2", "tags": [], "minutes": 20}], "total": 2}
        with mock.patch.object(picnic_recipes, "search", return_value=found):
            answer = (await self.client.get("/api/picnic/recipes?q=pasta&diet=vegetarisch")).get_json()
        self.assertEqual(([r["id"] for r in answer["recipes"]], answer["total"]), (["1"], 1))
        with mock.patch.object(
            crisp, "search_recipes", return_value={"recipes": [{"id": "1", "minutes": 50}, {"id": "2", "minutes": 10}], "total": 2}
        ):
            answer = (await self.client.get("/api/crisp/recipes?q=pasta&maxMinutes=30")).get_json()
        self.assertEqual([r["id"] for r in answer["recipes"]], ["2"])

    async def test_the_filters_reach_albert_heijn_and_the_meal_kits(self):
        from mealieshopper import hellofresh

        with mock.patch.object(ah, "search_recipes", return_value={"recipes": [], "total": 0}) as search:
            await self.client.get("/api/ah/search?q=pasta&course=dessert&maxMinutes=15")
        self.assertEqual(search.call_args.kwargs["wanted"], D.Wanted(course="dessert", max_minutes=15))
        with mock.patch.object(hellofresh, "search", return_value={"recipes": [], "total": 0}) as search:
            await self.client.get("/api/hellofresh/search?q=pasta&diet=vegetarisch")
        self.assertEqual(search.call_args.kwargs["wanted"], D.Wanted(diet="vegetarisch"))
        self.assertEqual((await self.client.get("/api/ah/search?q=pasta&maxMinutes=snel")).status_code, 400)


class MealieFilterTest(NutritionCase):
    def setUp(self):
        super().setUp()

        def dish(number, name, ingredients, tags=(), categories=()):
            return recipe(
                number,
                name,
                None,
                recipeIngredient=[{"display": line} for line in ingredients],
                tags=[{"id": f"t-{tag}", "slug": tag.lower(), "name": tag} for tag in tags],
                recipeCategory=[{"id": f"c-{cat}", "slug": cat.lower(), "name": cat} for cat in categories],
            )

        self.add(
            dish(1, "Pasta pesto", ["spaghetti", "parmezaan", "basilicum"], categories=["Hoofdgerecht"]),
            dish(2, "Kip curry", ["500 g kipfilet", "kokosmelk"]),
            dish(3, "Zalm", ["zalmfilet", "citroen"], tags=["Vis"]),
            dish(4, "Linzensoep", ["linzen", "wortel", "ui"], categories=["Soep"]),
            dish(5, "Chocolademousse", ["pure chocolade", "eieren", "slagroom"], categories=["Dessert"]),
            dish(6, "Vegaburger", ["2 vegaburgers", "broodjes"], tags=["Vegetarisch"]),
        )

    async def found(self, **wanted):
        with routes() as api:
            self.mock_mealie(api)
            answer = await recipe_nutrition.search("", {}, wanted=recipe_nutrition.Filters(**wanted))
        return sorted(r["name"] for r in answer["recipes"])

    async def test_vegetarian_vegan_meat_fish_and_poultry(self):
        self.assertEqual(await self.found(diet="vegetarisch"), ["Chocolademousse", "Linzensoep", "Pasta pesto", "Vegaburger"])
        self.assertEqual(await self.found(diet="veganistisch"), ["Linzensoep"])
        self.assertEqual(await self.found(protein="gevogelte"), ["Kip curry"])
        self.assertEqual(await self.found(protein="vis"), ["Zalm"])

    async def test_the_course_from_the_categories_and_without_one_a_main_course(self):
        self.assertEqual(await self.found(course="soep"), ["Linzensoep"])
        self.assertEqual(await self.found(course="dessert"), ["Chocolademousse"])
        self.assertEqual(await self.found(course="hoofdgerecht"), ["Kip curry", "Pasta pesto", "Vegaburger", "Zalm"])

    async def test_the_route_passes_them_on(self):
        client = self.client_for(create_app())
        with routes() as api:
            self.mock_mealie(api)
            answer = (await client.get("/api/mealie/recipes?diet=vegetarisch&course=soep")).get_json()
        self.assertEqual([r["name"] for r in answer["recipes"]], ["Linzensoep"])


class MealieTagsTest(NutritionCase):
    """Na een ronde zet MealieShopper de tags uit de ingrediënten in Mealie (een PATCH per recept met de hele lijst)."""

    def setUp(self):
        super().setUp()
        self.created = []
        self.patches = []

    def mock_tags(self, api):
        def search_tags(request):
            return httpx.Response(200, json={"items": []})

        def create_tag(request):
            name = json.loads(request.content)["name"]
            self.created.append(name)
            return httpx.Response(201, json={"id": f"id-{name}", "name": name, "slug": name.lower()})

        def patch(request, slug):
            tags = json.loads(request.content)["tags"]
            self.patches.append((slug, [tag["name"] for tag in tags]))
            self.recipes[slug] = {**self.recipes[slug], "tags": tags, "updatedAt": f"gewijzigd-{len(self.patches)}"}
            return httpx.Response(200, json=self.recipes[slug])

        api.get(f"{URL}/api/organizers/tags").mock(side_effect=search_tags)
        api.get(url__regex=rf"^{URL}/api/organizers/tags/slug/").mock(return_value=httpx.Response(404))
        api.post(f"{URL}/api/organizers/tags").mock(side_effect=create_tag)
        api.patch(url__regex=rf"^{URL}/api/recipes/(?P<slug>[a-z0-9-]+)$").mock(side_effect=patch)

    async def round(self):
        with routes() as api:
            self.mock_mealie(api)
            self.mock_tags(api)
            await self.refresh()
            await recipe_nutrition.tags_running()

    async def test_tags_from_the_ingredients_and_your_own_tags_stay(self):
        self.add(
            recipe(
                1, "Linzensoep", None, recipeIngredient=[{"display": "linzen"}, {"display": "ui"}], tags=[{"id": "x", "name": "Soep", "slug": "soep"}]
            ),
            recipe(2, "Kip curry", None, recipeIngredient=[{"display": "kipfilet"}, {"display": "rijst"}]),
            recipe(3, "Zonder ingrediënten", None),
        )
        await self.round()
        self.assertEqual(dict(self.patches), {"recept-1": ["Soep", "Veganistisch", "Vegetarisch"], "recept-2": ["Gevogelte"]})
        self.assertEqual(sorted(self.created), ["Gevogelte", "Veganistisch", "Vegetarisch"])
        self.patches.clear()
        await self.round()
        self.assertEqual(self.patches, [], "staat er al: niets opnieuw")

    async def test_a_tag_that_no_longer_fits_goes_and_one_you_removed_stays_away(self):
        self.add(recipe(1, "Pasta", None, recipeIngredient=[{"display": "spaghetti"}, {"display": "tomaat"}]))
        await self.round()
        self.assertEqual(self.patches, [("recept-1", ["Veganistisch", "Vegetarisch"])])
        # Het recept krijgt spek: Veganistisch en Vegetarisch kloppen niet meer, Vlees wel.
        self.patches.clear()
        self.recipes["recept-1"] = {
            **self.recipes["recept-1"],
            "recipeIngredient": [{"display": "spaghetti"}, {"display": "spekjes"}],
            "updatedAt": "nieuw",
        }
        await self.round()
        self.assertEqual(self.patches, [("recept-1", ["Vlees"])])
        # Jij haalt Vlees weg: MealieShopper zet hem niet opnieuw.
        self.patches.clear()
        self.recipes["recept-1"] = {**self.recipes["recept-1"], "tags": [], "updatedAt": "door-jou"}
        await self.round()
        self.assertEqual(self.patches, [])

    async def test_turned_off_no_tags(self):
        await auth.set_setting(recipe_nutrition.SETTING_DIET_TAGS, "0")
        self.add(recipe(1, "Pasta", None, recipeIngredient=[{"display": "spaghetti"}]))
        await self.round()
        self.assertEqual(self.patches, [])
        self.assertFalse(await recipe_nutrition.diet_tags_enabled())

    async def test_the_setting_in_the_app(self):
        client = self.client_for(create_app())
        self.assertIs((await client.get("/api/server-settings")).get_json()["dietTags"], True, "standaard aan")
        saved = await client.put("/api/server-settings", json={"dietTags": False})
        self.assertIs(saved.get_json()["dietTags"], False)
        self.assertEqual((await client.put("/api/server-settings", json={"dietTags": "nee"})).status_code, 400)
