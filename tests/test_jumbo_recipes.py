"""Jumbo-recepten: zoeken, favorieten, één recept met ingrediënten en bereiding, en importeren in Mealie.

De fixtures hebben de vorm van de echte antwoorden van jumbo.com (getRecipe en getRecipesListers), met
verzonnen recepten.
"""
import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("MEALIESHOPPER_DATA_DIR", tempfile.mkdtemp())

from mealieshopper import auth, jumbo, jumbo_explore, jumbo_recipes, mcp, mealie  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402


def card(recipe_id, title, *, time=20, persons=4, rating=(3, 4.5), image="https://img/r.jpg", author="jumbo", tip=""):
    return {
        "id": recipe_id, "link": f"/recepten/{title.lower().replace(' ', '-')}-{recipe_id}", "fullImageUrl": image,
        "numberOfIngredients": 7, "numberOfPersons": persons, "title": title, "time": time,
        "author": {"name": author, "__typename": "RecipeAuthor"},
        "rating": {"count": rating[0], "value": rating[1], "__typename": "Rating"},
        "cookingTip": tip, "course": "hoofdgerecht", "calories": 0, "__typename": "Recipe",
    }


def search_answer(items, total=None):
    return {"searchRecipes": {"count": len(items) if total is None else total, "facets": [
        {"displayName": "Keuken", "key": "keuken", "values": [{"count": 5, "link": "/recepten/nee-1", "name": "Hollands", "title": "geen recept"}]}],
        "items": items, "start": 0, "__typename": "RecipeSearchResult"}}


def ingredient(name, amount, sku=None, title=None):
    product = None if not sku else {
        "id": sku, "sku": sku, "title": title or f"Jumbo {name}", "link": f"/producten/jumbo-{sku}", "brand": "Jumbo",
        "prices": {"price": 95}, "__typename": "Product",
    }
    return {"amount": amount, "name": name, "quantityOfProductRequired": {"amount": 1} if product else None, "product": product, "__typename": "RecipeIngredient"}


RECIPE = {
    "id": "700001", "favourited": True, "title": "Romige pasta met tomaat", "description": "Een snelle pasta.",
    "author": {"name": "Wat eten we", "__typename": "RecipeAuthor"},
    "instructions": ["Snijd de ui.", "Kook de pasta gaar.", "Meng alles."],
    "ingredients": [
        ingredient("Uien", "2", "100001NET", "Jumbo Gele Uien 1 kg"),
        ingredient("Gerookte zalm", "200 g", "100002STK"),
        ingredient("Platte peterselie", "15 g"),
        ingredient("Zout", ""),
    ],
    "time": 20, "cookingTip": "Ook lekker met venkel.", "calories": 699,
    "nutritions": [
        {"name": "Energie", "quantity": 699, "unit": "kcal"}, {"name": "Koolhydraten", "quantity": 65, "unit": "g"},
        {"name": "Eiwitten", "quantity": 24, "unit": "g"}, {"name": "Vet", "quantity": 37, "unit": "g"},
        {"name": "Onbekend", "quantity": 1, "unit": "g"},
    ],
    "course": "hoofdgerecht", "dishType": "vis", "fullImageUrl": "https://img/700001.jpg",
    "rating": {"count": 40, "value": 4.5}, "numberOfPersons": 4, "link": "/recepten/romige-pasta-met-tomaat-700001", "__typename": "Recipe",
}


class TempDataDirTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"MEALIESHOPPER_DATA_DIR": tmp.name, "PASSKEY_AUTH_ENABLED": "false"})
        patcher.start()
        self.addCleanup(patcher.stop)
        auth.init_db()
        jumbo._access_cache.update(token="acc", expires_at=jumbo.time.time() + 3600)
        jumbo._persisted_route.clear()
        auth.set_secret(jumbo.REFRESH_SECRET, "ref")


class RecipeTest(TempDataDirTest):
    def test_the_request_is_the_stored_query_with_the_variable_of_the_website(self):
        with mock.patch.object(jumbo, "_persisted_query", return_value={"recipe": RECIPE}) as query:
            jumbo_recipes.get_recipe("700001")
        self.assertEqual(query.call_args.args, ("getRecipe", {"id": "700001"}))
        self.assertEqual(jumbo.PERSISTED_QUERIES["getRecipe"], "6726ea8758d025225b93db5f464f3e292d18982b1f43d4b6eeec09c081485b41")

    def test_a_recipe_has_lines_steps_times_and_nutrition(self):
        recipe = jumbo_recipes.parse_recipe(RECIPE, "700001")
        self.assertEqual((recipe["name"], recipe["portions"], recipe["minutes"], recipe["author"]), ("Romige pasta met tomaat", 4, 20, "Wat eten we"))
        self.assertEqual([i["line"] for i in recipe["ingredients"]], ["2 Uien", "200 g Gerookte zalm", "15 g Platte peterselie", "Zout"])
        self.assertEqual(recipe["ingredients"][0]["productId"], "100001NET")
        self.assertEqual(recipe["ingredients"][0]["product"], "Jumbo Gele Uien 1 kg")
        self.assertIsNone(recipe["ingredients"][2]["productId"])  # zonder Jumbo-product
        self.assertEqual(recipe["steps"], ["Snijd de ui.", "Kook de pasta gaar.", "Meng alles."])
        self.assertEqual((recipe["tip"], recipe["image"], recipe["url"]), ("Ook lekker met venkel.", "https://img/700001.jpg", "https://www.jumbo.com/recepten/romige-pasta-met-tomaat-700001"))
        self.assertEqual((recipe["rating"], recipe["ratingCount"]), (4.5, 40))

    def test_an_unknown_recipe_is_an_error(self):
        for raw in (None, {}, {"title": 5}):
            with self.assertRaises(RuntimeError):
                jumbo_recipes.parse_recipe(raw, "1")

    def test_ids_are_checked_and_may_have_a_suffix(self):
        for good in ("504898", "1416890-7", "956089-7"):
            self.assertEqual(jumbo_recipes._valid_id(good), good)
        for bad in ("", "5 4", "a/b", "x" * 40, "1;2"):
            with self.assertRaises(ValueError):
                jumbo_recipes._valid_id(bad)

    def test_the_jsonld_is_a_schema_org_recipe_mealie_can_import(self):
        data = jumbo_recipes.jsonld(jumbo_recipes.parse_recipe(RECIPE, "700001"))
        self.assertEqual((data["@type"], data["name"], data["recipeYield"], data["totalTime"]), ("Recipe", "Romige pasta met tomaat", "4", "PT20M"))
        self.assertEqual(data["recipeIngredient"], ["2 Uien", "200 g Gerookte zalm", "15 g Platte peterselie", "Zout"])
        self.assertEqual([s["text"] for s in data["recipeInstructions"]], ["Snijd de ui.", "Kook de pasta gaar.", "Meng alles."])
        self.assertEqual(data["image"], ["https://img/700001.jpg"])
        self.assertEqual(data["url"], "https://www.jumbo.com/recepten/romige-pasta-met-tomaat-700001")
        self.assertIn("Tip: Ook lekker met venkel.", data["description"])
        self.assertEqual(data["nutrition"], {"@type": "NutritionInformation", "calories": "699 kcal", "carbohydrateContent": "65 g", "proteinContent": "24 g", "fatContent": "37 g"})
        self.assertEqual((data["recipeCategory"], data["keywords"], data["author"]["name"]), ("hoofdgerecht", "vis", "Wat eten we"))
        self.assertEqual(data["aggregateRating"]["ratingCount"], 40)

    def test_a_recipe_without_content_cannot_be_imported(self):
        empty = jumbo_recipes.parse_recipe({"title": "Leeg", "id": "1"}, "1")
        with self.assertRaises(RuntimeError):
            jumbo_recipes.jsonld(empty)

    def test_url_ids(self):
        f = jumbo_recipes.recipe_id_from_url
        self.assertEqual(f("https://www.jumbo.com/recepten/romige-pasta-met-gerookte-zalm-504898"), "504898")
        self.assertEqual(f("https://www.jumbo.com/recepten/kabeljauw-met-pompoenpuree-en-gnocchi--1001249?x=1"), "1001249")
        self.assertEqual(f("http://jumbo.com/recepten/pasta-allamatriciana-1416890-7/"), "1416890-7")
        for other in ("https://www.jumbo.com/producten/jumbo-melk-123456STK", "https://allerhande.nl/recept/R-R123/pasta", "", None,
                      "https://www.jumbo.com/recepten/"):
            self.assertIsNone(f(other))


class ListTest(TempDataDirTest):
    def test_search_sends_the_variables_of_the_website_and_reads_cards_and_the_total(self):
        answer = search_answer([card("1001249", "Kabeljauw met puree"), card("1416890-7", "Pasta met spek", persons=1, rating=(0, 0), image="https://img/b.jpg", author="smulweb")], total=820)
        with mock.patch.object(jumbo, "_persisted_query", return_value=answer) as query:
            found = jumbo_recipes.search("  pasta ", 24)
        self.assertEqual(query.call_args.args, ("getRecipesListers", {"input": {"searchTerms": "pasta", "friendlyUrl": "?searchTerms=pasta", "offSet": 24, "limit": 24}}))
        self.assertEqual(jumbo.PERSISTED_QUERIES["getRecipesListers"], "00435d1bce6b5ea2104effb39acc45785a7af43880729d098bd7da99c596021e")
        self.assertEqual(found["total"], 820)
        first, second = found["recipes"]  # de facet in de filterlijst is geen recept
        self.assertEqual((first["id"], first["name"], first["minutes"], first["portions"], first["rating"]), ("1001249", "Kabeljauw met puree", 20, 4, 4.5))
        self.assertEqual((first["image"], first["url"], first["ingredientCount"], first["author"]), ("https://img/r.jpg", "https://www.jumbo.com/recepten/kabeljauw-met-puree-1001249", 7, "jumbo"))
        self.assertEqual((second["id"], second["rating"]), ("1416890-7", None))  # zonder beoordelingen geen cijfer

    def test_search_needs_a_term_and_a_sane_offset(self):
        with self.assertRaises(ValueError):
            jumbo_recipes.search("   ")
        with mock.patch.object(jumbo, "_persisted_query", return_value=search_answer([])) as query:
            jumbo_recipes.search("pasta", "onzin")
        self.assertEqual(query.call_args.args[1]["input"]["offSet"], 0)

    def test_search_without_a_result_object_is_an_error(self):
        with mock.patch.object(jumbo, "_persisted_query", return_value={"searchRecipes": None}):
            with self.assertRaises(RuntimeError):
                jumbo_recipes.search("pasta")

    def test_the_lists_overview_has_favourites_first_then_own_lists_with_counts(self):
        def overview(list_id, title, total, kind):
            return {"totalRecipes": total, "type": kind, "title": title, "id": list_id, "__typename": "RecipeListV2",
                    "items": {"recipeListItems": [{"recipe": {"fullImageUrl": "https://img/x.jpg", "title": "Alleen titel", "__typename": "Recipe"}}]}}

        answer = {
            "favouriteLists": {"items": [overview("61308753ccf27832bfe2ed55", "Favorieten", 75, "FAVORITE_LIST")]},
            "customerLists": {"items": [overview("63e68c068089d600134305cb", "Dit willen we vaker eten!", 14, "CATEGORY"), {"id": "x"}, None]},
        }
        with mock.patch.object(jumbo, "_persisted_query", return_value=answer) as query:
            lists = jumbo_recipes.get_lists()
        self.assertEqual(query.call_args.args[0], "getRecipeLists")
        self.assertEqual(query.call_args.args[1], {"includeHasRecipe": False, "itemsInput": {"size": 1}, "listsSize": 24, "listsPage": 1})
        self.assertEqual(lists, [
            {"id": "61308753ccf27832bfe2ed55", "name": "Favorieten", "kind": "favorites", "count": 75},
            {"id": "63e68c068089d600134305cb", "name": "Dit willen we vaker eten!", "kind": "own", "count": 14},
        ])
        self.assertEqual(jumbo.PERSISTED_QUERIES["getRecipeLists"], "855078174946522c415ad4e83706f30a5d48c8458885956dcd0db7a2e666ef56")
        self.assertEqual(jumbo_recipes.summaries(answer), [])  # de overzichtskaartjes hebben geen id of link: geen recepten om te openen

    def test_a_list_page_with_full_cards_is_read_wherever_it_sits(self):
        # Zo ziet een geopend lijstje eruit (FavoriteRecipes): de kaartjes hebben wel id en link.
        page = {"recipeListsV2": {"items": [{"id": "l1", "items": {"recipeListItems": [
            {"id": "i1", "recipeId": "700001", "recipe": card("700001", "Een")},
            {"id": "i2", "recipeId": "700002", "recipe": card("700002", "Twee")},
            {"id": "i3", "recipeId": "700002", "recipe": card("700002", "Twee")},
        ]}}]}}
        self.assertEqual([r["id"] for r in jumbo_recipes.summaries(page)], ["700001", "700002"])

    def test_favourites_read_the_list_page_with_total_and_next_page(self):
        def favourites(page, ids, total=75, has_next=True):
            return {"recipeListsV2": {"items": [{
                "id": "61308753ccf27832bfe2ed55", "title": "Favorieten", "type": "FAVORITE_LIST", "totalRecipes": total,
                "items": {
                    "paginationInfo": {"pageSize": 24, "currentPage": page, "totalPages": 4, "totalSize": total, "hasPreviousPage": page > 1, "hasNextPage": has_next},
                    "recipeListItems": [{"id": f"i{i}", "recipeId": str(i), "orderIndex": 0, "recipe": card(str(i), f"Recept {i}")} for i in ids],
                },
            }]}}

        with mock.patch.object(jumbo, "_persisted_query", return_value=favourites(2, [700001, 700002])) as query:
            found = jumbo_recipes.get_favorites("2")
        self.assertEqual(query.call_args.args, ("FavoriteRecipes", {"size": 24, "page": 2}))
        self.assertEqual(jumbo.PERSISTED_QUERIES["FavoriteRecipes"], "66516d14849720eac6d873da34dffe7cbd055ed42b8adeda8b31b8b79d5cebf6")
        self.assertEqual((found["total"], found["page"], found["hasNext"], [r["id"] for r in found["recipes"]]), (75, 2, True, ["700001", "700002"]))
        self.assertEqual(found["recipes"][0]["url"], "https://www.jumbo.com/recepten/recept-700001-700001")
        with mock.patch.object(jumbo, "_persisted_query", return_value=favourites(4, [700003], has_next=False)):
            self.assertFalse(jumbo_recipes.get_favorites(4)["hasNext"])
        with mock.patch.object(jumbo, "_persisted_query", return_value=favourites(1, [700001])) as query:
            jumbo_recipes.get_favorites("onzin")
            jumbo_recipes.get_favorites(0)
        self.assertEqual([c.args[1]["page"] for c in query.call_args_list], [1, 1])  # een slechte pagina wordt de eerste

    def test_an_own_list_is_asked_by_id_and_read_wherever_the_cards_sit(self):
        own = {"recipeList": {"id": "639d928592257300137bcad7", "title": "Kerst", "description": "", "type": "CATEGORY", "totalRecipes": 2, "__typename": "RecipeListV2", "items": {
            "paginationInfo": {"pageSize": 24, "currentPage": 1, "totalPages": 1, "totalSize": 2, "hasPreviousPage": False, "hasNextPage": False, "__typename": "PagePaginationInfo"},
            "recipeListItems": [{"recipe": dict(card("700001", "Een"), labels=[{"label": "kerst", "relativeUrl": "/recepten/kerst"}]), "__typename": "RecipeListItem"},
                                {"recipe": card("700002", "Twee"), "__typename": "RecipeListItem"}]}}}
        with mock.patch.object(jumbo, "_persisted_query", return_value=own) as query:
            found = jumbo_recipes.get_list_recipes("639d928592257300137bcad7", "3")
        self.assertEqual(query.call_args.args, ("getRecipeListById", {"listId": "639d928592257300137bcad7", "itemsInput": {"page": 3, "size": 24}}))
        self.assertEqual(jumbo.PERSISTED_QUERIES["getRecipeListById"], "ce93016895c6c68fb4c70ee182a773b7e3402a4f14a61f5e0c5bbb8019b97587")
        self.assertEqual((found["total"], found["page"], found["hasNext"], [r["id"] for r in found["recipes"]]), (2, 3, False, ["700001", "700002"]))
        # zonder paginationInfo: het totaal van het lijstje zelf, anders het aantal gevonden recepten
        bare = {"x": {"totalRecipes": 9, "items": [{"recipe": card("700001", "Een")}]}}
        with mock.patch.object(jumbo, "_persisted_query", return_value=bare):
            self.assertEqual(jumbo_recipes.get_list_recipes("abc")["total"], 9)
        with mock.patch.object(jumbo, "_persisted_query", return_value={"x": [{"recipe": card("700001", "Een")}]}):
            self.assertEqual(jumbo_recipes.get_list_recipes("abc")["total"], 1)
        with mock.patch.object(jumbo, "_persisted_query") as query:
            for bad in ("", "a b", "x;y", "z" * 70):
                with self.assertRaises(ValueError):
                    jumbo_recipes.get_list_recipes(bad)
        query.assert_not_called()

    LISTS = [{"id": "61308753ccf27832bfe2ed55", "name": "Favorieten", "kind": "favorites", "count": 75},
             {"id": "63e68c068089d600134305cb", "name": "Dit willen we vaker eten!", "kind": "own", "count": 14},
             {"id": "639d928592257300137bcad7", "name": "Kerst", "kind": "own", "count": 2}]

    def test_a_list_is_found_by_id_or_name_and_favourites_are_the_default(self):
        with mock.patch.object(jumbo_recipes, "get_lists", return_value=self.LISTS):
            self.assertEqual(jumbo_recipes.resolve_list("")["name"], "Favorieten")
            self.assertEqual(jumbo_recipes.resolve_list(None)["name"], "Favorieten")
            self.assertEqual(jumbo_recipes.resolve_list("kerst")["id"], "639d928592257300137bcad7")
            self.assertEqual(jumbo_recipes.resolve_list("639d928592257300137bcad7")["name"], "Kerst")
            self.assertEqual(jumbo_recipes.resolve_list("vaker eten")["name"], "Dit willen we vaker eten!")
            with self.assertRaises(ValueError) as nothing:
                jumbo_recipes.resolve_list("zomer")
            self.assertIn("Kerst", str(nothing.exception))
        with mock.patch.object(jumbo_recipes, "get_lists", return_value=self.LISTS + [{"id": "x", "name": "Kerst 2", "kind": "own", "count": 1}]):
            with self.assertRaises(ValueError) as several:
                jumbo_recipes.resolve_list("kerst")
            self.assertIn("Welke lijst", str(several.exception))
        with mock.patch.object(jumbo_recipes, "get_lists", return_value=[]):
            with self.assertRaises(ValueError):
                jumbo_recipes.resolve_list("")

    def test_favourites_use_their_own_request_and_other_lists_the_list_request(self):
        with mock.patch.object(jumbo_recipes, "get_favorites", return_value={"x": 1}) as fav, mock.patch.object(jumbo_recipes, "get_list_recipes", return_value={"x": 2}) as own:
            self.assertEqual(jumbo_recipes.get_recipes_of(self.LISTS[0], 2), {"x": 1})
            self.assertEqual(jumbo_recipes.get_recipes_of(self.LISTS[2], 3), {"x": 2})
        self.assertEqual((fav.call_args.args, own.call_args.args), ((2,), ("639d928592257300137bcad7", 3)))

    def test_a_missing_list_is_an_empty_page(self):
        for answer in ({}, {"recipeListsV2": None}, {"recipeListsV2": {"items": []}}):
            with mock.patch.object(jumbo, "_persisted_query", return_value=answer):
                self.assertEqual(jumbo_recipes.get_favorites(), {"recipes": [], "total": 0, "page": 1, "hasNext": False})

    def test_by_ids_sends_the_ids_of_the_website(self):
        with mock.patch.object(jumbo, "_persisted_query", return_value={"recipesByIds": [card("999582", "Een")]}) as query:
            recipes = jumbo_recipes.get_by_ids(["1000578", "999582"])
        self.assertEqual(query.call_args.args, ("getRecipesByIds", {"ids": ["1000578", "999582"], "limit": 2, "offset": 0}))
        self.assertEqual([r["id"] for r in recipes], ["999582"])
        with self.assertRaises(ValueError):
            jumbo_recipes.get_by_ids(["1; DROP"])


class RoutesTest(TempDataDirTest):
    def setUp(self):
        super().setUp()
        self.client = create_app().test_client()

    def test_list_search_detail_and_import(self):
        with mock.patch.object(jumbo, "_persisted_query", return_value=search_answer([card("1001249", "Een")], total=820)):
            found = self.client.get("/api/jumbo/recipes?q=pasta&offset=24").get_json()
        self.assertEqual((found["total"], found["pageSize"], found["recipes"][0]["id"]), (820, 24, "1001249"))
        self.assertEqual(self.client.get("/api/jumbo/recipes").status_code, 400)  # zonder zoekterm
        with mock.patch.object(jumbo, "_persisted_query", return_value={"recipe": RECIPE}):
            detail = self.client.get("/api/jumbo/recipes/700001").get_json()
            self.assertEqual((detail["name"], len(detail["ingredients"]), len(detail["steps"])), ("Romige pasta met tomaat", 4, 3))
            with mock.patch.object(mealie, "ensure_configured"), \
                    mock.patch.object(mealie, "import_via_jsonld", return_value="romige-pasta") as imported, \
                    mock.patch.object(mealie, "recipe_page_url", return_value="http://mealie/g/home/r/romige-pasta"):
                done = self.client.post("/api/jumbo/recipes/700001/import")
        self.assertEqual((done.status_code, done.get_json()["slug"]), (200, "romige-pasta"))
        url, data = imported.call_args.args
        self.assertEqual((url, data["name"]), ("https://www.jumbo.com/recepten/romige-pasta-met-tomaat-700001", "Romige pasta met tomaat"))

    def test_the_favourites_route_pages(self):
        answer = {"recipeListsV2": {"items": [{"items": {"paginationInfo": {"totalSize": 30, "hasNextPage": True}, "recipeListItems": [{"recipe": card("700001", "Een")}]}}]}}
        with mock.patch.object(jumbo, "_persisted_query", return_value=answer) as query:
            body = self.client.get("/api/jumbo/favorite-recipes?page=2").get_json()
        self.assertEqual(query.call_args.args[1], {"size": 24, "page": 2})
        self.assertEqual((body["total"], body["hasNext"], body["pageSize"], body["recipes"][0]["id"]), (30, True, 24, "700001"))
        with mock.patch.object(jumbo, "_persisted_query", side_effect=jumbo.JumboPersistedQueryError("PersistedQueryNotFound")):
            self.assertEqual(self.client.get("/api/jumbo/favorite-recipes").status_code, 424)

    def test_the_list_routes(self):
        with mock.patch.object(jumbo_recipes, "get_lists", return_value=ListTest.LISTS):
            lists = self.client.get("/api/jumbo/recipe-lists").get_json()["lists"]
        self.assertEqual([item["name"] for item in lists], ["Favorieten", "Dit willen we vaker eten!", "Kerst"])
        own = {"x": {"items": {"paginationInfo": {"totalSize": 2, "hasNextPage": False}, "recipeListItems": [{"recipe": card("700001", "Een")}]}}}
        with mock.patch.object(jumbo, "_persisted_query", return_value=own) as query:
            body = self.client.get("/api/jumbo/recipe-lists/639d928592257300137bcad7?page=1").get_json()
        self.assertEqual(query.call_args.args[0], "getRecipeListById")
        self.assertEqual((body["total"], body["pageSize"], body["recipes"][0]["id"]), (2, 24, "700001"))
        self.assertEqual(self.client.get("/api/jumbo/recipe-lists/a%20b").status_code, 400)
        with mock.patch.object(jumbo, "_persisted_query", side_effect=jumbo.JumboPersistedQueryError("PersistedQueryNotFound")):
            self.assertEqual(self.client.get("/api/jumbo/recipe-lists/abc").status_code, 424)

    def test_errors_are_clear(self):
        self.assertEqual(self.client.get("/api/jumbo/recipes/niet%20geldig").status_code, 400)
        with mock.patch.object(jumbo, "_persisted_query", side_effect=jumbo.JumboPersistedQueryError("PersistedQueryNotFound")):
            self.assertEqual(self.client.get("/api/jumbo/recipes?q=pasta").status_code, 424)
        with mock.patch.object(mealie, "ensure_configured", side_effect=RuntimeError("Mealie is niet gekoppeld.")):
            refused = self.client.post("/api/jumbo/recipes/700001/import")
        self.assertEqual((refused.status_code, refused.get_json()["error"]), (400, "Mealie is niet gekoppeld."))
        with mock.patch.object(jumbo, "_persisted_query", return_value={"recipe": RECIPE}), mock.patch.object(mealie, "ensure_configured"), \
                mock.patch.object(mealie, "import_via_jsonld", side_effect=RuntimeError("Mealie import mislukt (500): geheim")):
            failed = self.client.post("/api/jumbo/recipes/700001/import")
        self.assertEqual((failed.status_code, failed.get_json()["error"]), (424, "Importeren in Mealie mislukt"))


class UrlImportTest(TempDataDirTest):
    def test_a_jumbo_recipe_link_goes_through_the_api_when_jumbo_is_linked(self):
        with mock.patch.object(mealie, "ensure_configured"), mock.patch.object(jumbo, "_persisted_query", return_value={"recipe": RECIPE}), \
                mock.patch.object(mealie, "import_via_jsonld", return_value="slug") as imported, mock.patch.object(mealie, "import_via_url") as page:
            slug = mealie.import_from_url("https://www.jumbo.com/recepten/romige-pasta-met-tomaat-700001")
        self.assertEqual(slug, "slug")
        page.assert_not_called()
        self.assertEqual(imported.call_args.args[1]["recipeIngredient"][0], "2 Uien")

    def test_without_a_link_or_when_the_api_fails_the_page_import_is_tried(self):
        url = "https://www.jumbo.com/recepten/romige-pasta-met-tomaat-700001"
        with mock.patch.object(mealie, "ensure_configured"), mock.patch.object(jumbo, "_persisted_query", side_effect=RuntimeError("stuk")), \
                mock.patch.object(mealie, "import_via_url", return_value="viapagina"):
            self.assertEqual(mealie.import_from_url(url), "viapagina")
        auth.delete_secret(jumbo.REFRESH_SECRET)
        with mock.patch.object(mealie, "ensure_configured"), mock.patch.object(jumbo, "_persisted_query") as query, \
                mock.patch.object(mealie, "import_via_url", return_value="viapagina"):
            self.assertEqual(mealie.import_from_url(url), "viapagina")
        query.assert_not_called()


class McpAndExplorerTest(TempDataDirTest):
    def test_the_tools_read_recipes(self):
        with mock.patch.object(jumbo, "_persisted_query", return_value=search_answer([card("1001249", "Een"), card("1001250", "Twee")], total=9)):
            found = mcp.tool_search_jumbo_recipes({"query": "pasta", "limit": 1})
        self.assertEqual((found["total"], [r["id"] for r in found["recipes"]]), (9, ["1001249"]))
        with self.assertRaises(mcp.ToolError):
            mcp.tool_search_jumbo_recipes({})
        with mock.patch.object(jumbo_recipes, "get_lists", return_value=ListTest.LISTS):
            self.assertEqual([item["name"] for item in mcp.tool_list_jumbo_recipe_lists({})["lists"]], ["Favorieten", "Dit willen we vaker eten!", "Kerst"])
            own = {"x": {"items": {"paginationInfo": {"totalSize": 2, "hasNextPage": False}, "recipeListItems": [{"recipe": card("700001", "Een")}]}}}
            with mock.patch.object(jumbo, "_persisted_query", return_value=own) as query:
                kerst = mcp.tool_get_jumbo_recipe_list({"list": "Kerst"})
            self.assertEqual((query.call_args.args[0], kerst["list"], kerst["total"], [r["id"] for r in kerst["recipes"]]), ("getRecipeListById", "Kerst", 2, ["700001"]))
            favourites = {"recipeListsV2": {"items": [{"items": {"paginationInfo": {"totalSize": 5, "hasNextPage": True}, "recipeListItems": [{"recipe": card("700003", "Drie")}]}}]}}
            with mock.patch.object(jumbo, "_persisted_query", return_value=favourites) as query:
                mine = mcp.tool_get_jumbo_recipe_list({"page": 1})
            self.assertEqual((query.call_args.args[0], mine["list"], mine["hasNext"]), ("FavoriteRecipes", "Favorieten", True))
            with self.assertRaises(mcp.ToolError):
                mcp.tool_get_jumbo_recipe_list({"list": "zomer"})
        for name in ("list_jumbo_recipe_lists", "get_jumbo_recipe_list"):
            self.assertTrue(mcp.TOOLS[name]["annotations"]["readOnlyHint"])
        with mock.patch.object(jumbo, "_persisted_query", return_value={"recipe": RECIPE}):
            recipe = mcp.tool_get_jumbo_recipe({"recipe_id": "700001"})
        self.assertEqual(recipe["ingredients"][0], "2 Uien")
        self.assertEqual(len(recipe["steps"]), 3)
        with self.assertRaises(mcp.ToolError):
            mcp.tool_get_jumbo_recipe({"recipe_id": "a b"})
        for name in ("search_jumbo_recipes", "get_jumbo_recipe"):
            self.assertTrue(mcp.TOOLS[name]["annotations"]["readOnlyHint"])

    def test_the_explorer_reports_shapes_without_titles(self):
        def answer(operation, variables):
            if operation == "getRecipe":
                return {"recipe": RECIPE}
            if operation == "getRecipesByIds":
                return {"recipes": [card("700001", "Geheime titel")]}
            if operation == "getRecipesListers":
                return search_answer([card("700001", "Geheime titel")], total=3)
            if operation == "getRecipeListById":
                return {"x": {"items": {"paginationInfo": {"totalSize": 2, "hasNextPage": False}, "recipeListItems": [{"recipe": card("700001", "Geheime titel")}]}}}
            if operation == "FavoriteRecipes":
                return {"recipeListsV2": {"items": [{"items": {"paginationInfo": {"totalSize": 9, "hasNextPage": False}, "recipeListItems": [{"recipe": card("700001", "Geheime titel")}]}}]}}
            return {"favouriteLists": {"items": [{"id": "l1", "title": "Geheime lijst", "totalRecipes": 5, "type": "FAVORITE_LIST"}]},
                    "customerLists": {"items": [{"id": "l2", "title": "Geheime eigen lijst", "totalRecipes": 2, "type": "CATEGORY"}]}}

        with mock.patch.object(jumbo, "_persisted_query", side_effect=answer):
            probes = {p["name"]: p for p in jumbo_explore.explore()["probes"] if "recept" in p["name"]}
        self.assertEqual(len(probes), 6)
        self.assertTrue(all(p["ok"] for p in probes.values()), probes)
        self.assertEqual(probes["recepten zoeken: opgeslagen query getRecipesListers"]["info"]["recepten in totaal"], 3)
        self.assertNotIn("Geheime titel", repr(probes))
        self.assertNotIn("Geheime lijst", repr(probes))
        self.assertNotIn("Geheime eigen lijst", repr(probes))
        self.assertNotIn("Romige pasta", repr(probes))


if __name__ == "__main__":
    unittest.main()
