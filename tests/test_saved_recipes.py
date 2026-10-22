import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("MEALIESHOPPER_DATA_DIR", tempfile.mkdtemp())

from mealieshopper import ah  # noqa: E402

API_RESULT = {"recipes": [{"id": "1", "title": "Soep", "image": "", "url": "u"}], "total": 1}
BROWSER_RESULT = {"recipes": [{"id": "2", "title": "Stamppot", "image": "", "url": "v"}], "total": 1}


class SavedRecipesEntryPointTest(unittest.TestCase):
    def test_not_linked(self):
        with mock.patch.object(ah, "configured_refresh_token", return_value=""), \
                mock.patch.object(ah, "_get_saved_recipes_api", side_effect=AssertionError("niet aanroepen")):
            with self.assertRaises(RuntimeError) as ctx:
                ah.get_saved_recipes()
        self.assertIn("Beheer > Koppelen > Albert Heijn", str(ctx.exception))

    def test_linked_uses_the_api_and_passes_paging(self):
        with mock.patch.object(ah, "configured_refresh_token", return_value="tok"), \
                mock.patch.object(ah, "_get_saved_recipes_api", return_value=API_RESULT) as api:
            self.assertEqual(ah.get_saved_recipes(page=2, size=10), API_RESULT)
        api.assert_called_once_with(2, 10)

    def test_api_error_is_not_swallowed(self):
        with mock.patch.object(ah, "configured_refresh_token", return_value="tok"), \
                mock.patch.object(ah, "_get_saved_recipes_api", side_effect=RuntimeError("collectie: 404")):
            with self.assertRaises(RuntimeError) as ctx:
                ah.get_saved_recipes()
        self.assertIn("collectie: 404", str(ctx.exception))


def graphql_ok(data):
    response = mock.Mock(ok=True)
    response.json.return_value = {"data": data}
    return response


def graphql_error(message):
    response = mock.Mock(ok=True)
    response.json.return_value = {"errors": [{"message": message}]}
    return response


class SavedRecipesCollectionsTest(unittest.TestCase):
    def run_collections(self, responses, page=0, size=50):
        with mock.patch.object(ah, "get_user_token", return_value="tok"), \
                mock.patch.object(ah.requests, "post", side_effect=responses) as post:
            return ah._get_saved_recipes_collections(page, size), post

    def test_ids_are_deduplicated_and_hydrated(self):
        collections = graphql_ok({"recipeCollectionCategories": [
            {"id": 1, "name": "Alles", "isDefault": True, "recipes": [{"id": 11}, {"id": 12}]},
            {"id": 2, "name": "Taarten", "isDefault": False, "recipes": [{"id": 12}, {"id": 13}]},
        ]})
        summaries = graphql_ok({"recipeSummaries": [
            {"id": 11, "title": "Soep", "slug": "soep", "images": [{"url": "https://img/11.jpg"}]},
            {"id": 12, "title": "Stamppot", "slug": "", "images": []},
            {"id": 13, "title": "Taart", "slug": "taart"},
        ]})
        result, post = self.run_collections([collections, summaries])
        self.assertEqual(result["total"], 3)
        self.assertEqual(post.call_args_list[1].kwargs["json"]["variables"], {"ids": [11, 12, 13]})
        self.assertEqual(
            result["recipes"][0],
            {"id": "11", "title": "Soep", "image": "https://img/11.jpg",
             "url": "https://www.ah.nl/allerhande/recept/R-R11/soep"},
        )
        self.assertEqual(result["recipes"][1]["url"], "https://www.ah.nl/allerhande/recept/R-R12")
        self.assertEqual(result["recipes"][2]["image"], "")

    def test_scalar_recipe_ids_are_accepted_after_selection_error(self):
        rejected = graphql_error('Field "recipes" must not have a selection since type "[Int!]" has no subfields')
        scalar = graphql_ok({"recipeCollectionCategories": [{"id": 1, "recipes": [5, 6]}]})
        summaries = graphql_ok({"recipeSummaries": [{"id": 5, "title": "A", "slug": "a"}, {"id": 6, "title": "B", "slug": "b"}]})
        result, post = self.run_collections([rejected, scalar, summaries])
        self.assertEqual([r["id"] for r in result["recipes"]], ["5", "6"])
        self.assertEqual(post.call_count, 3)

    def test_titles_fall_back_to_id_cards_when_details_fail(self):
        collections = graphql_ok({"recipeCollectionCategories": [{"id": 1, "recipes": [{"id": 7}]}]})
        broken = graphql_error("Unknown argument ids")
        result, _ = self.run_collections([collections, broken, broken])
        self.assertEqual(
            result["recipes"],
            [{"id": "7", "title": "Recept 7", "image": "",
              "url": "https://www.ah.nl/allerhande/recept/R-R7"}],
        )

    def test_paging_hydrates_only_the_requested_page(self):
        collections = graphql_ok({"recipeCollectionCategories": [
            {"id": 1, "recipes": [{"id": n} for n in (1, 2, 3)]}
        ]})
        summaries = graphql_ok({"recipeSummaries": [{"id": 3, "title": "Drie", "slug": "drie"}]})
        result, post = self.run_collections([collections, summaries], page=1, size=2)
        self.assertEqual(post.call_args_list[1].kwargs["json"]["variables"], {"ids": [3]})
        self.assertEqual((result["total"], [r["id"] for r in result["recipes"]]), (3, ["3"]))

    def test_no_collections_is_empty(self):
        result, post = self.run_collections([graphql_ok({"recipeCollectionCategories": []})])
        self.assertEqual(result, {"recipes": [], "total": 0})
        self.assertEqual(post.call_count, 1)

    def test_validation_error_names_every_variant(self):
        errors = [graphql_error("Unknown argument 'x'"), graphql_error("Cannot query field recipes")]
        with mock.patch.object(ah, "get_user_token", return_value="tok"), \
                mock.patch.object(ah.requests, "post", side_effect=errors):
            with self.assertRaises(RuntimeError) as ctx:
                ah._get_saved_recipes_collections(0, 50)
        self.assertIn("Unknown argument 'x'", str(ctx.exception))
        self.assertIn("Cannot query field recipes", str(ctx.exception))


class SavedRecipesCookbookTest(unittest.TestCase):
    def test_recipes_are_mapped_and_paged(self):
        collected = {
            "result": [{"id": 1, "title": "Soep"}, {"id": 2, "title": None}, {"id": 3, "title": "Taart"}],
            "page": {"total": 3, "hasNextPage": False},
        }
        response = graphql_ok({"cookBookRecipesCollectedMember": collected})
        with mock.patch.object(ah, "get_user_token", return_value="tok"), \
                mock.patch.object(ah.requests, "post", return_value=response):
            first = ah._get_saved_recipes_cookbook(page=0, size=2)
            second = ah._get_saved_recipes_cookbook(page=1, size=2)
        self.assertEqual(first["total"], 3)
        self.assertEqual(
            first["recipes"][0],
            {"id": "1", "title": "Soep", "image": "", "url": "https://www.ah.nl/allerhande/recept/R-R1"},
        )
        self.assertEqual(first["recipes"][1]["title"], "Recept 2")
        self.assertEqual([r["id"] for r in second["recipes"]], ["3"])

    def test_empty_result(self):
        response = graphql_ok({"cookBookRecipesCollectedMember": None})
        with mock.patch.object(ah, "get_user_token", return_value="tok"), \
                mock.patch.object(ah.requests, "post", return_value=response):
            self.assertEqual(ah._get_saved_recipes_cookbook(0, 50), {"recipes": [], "total": 0})


class SavedRecipesApiChainTest(unittest.TestCase):
    EMPTY = {"recipes": [], "total": 0}

    def chain(self, collections, cookbook):
        def source(value):
            def call(*args, **kwargs):
                if isinstance(value, Exception):
                    raise value
                return value
            return call

        return (
            mock.patch.object(ah, "_get_saved_recipes_collections", side_effect=source(collections)),
            mock.patch.object(ah, "_get_saved_recipes_cookbook", side_effect=source(cookbook)),
        )

    def run_chain(self, collections, cookbook):
        patches = self.chain(collections, cookbook)
        for patch in patches:
            patch.start()
        try:
            return ah._get_saved_recipes_api(0, 50)
        finally:
            mock.patch.stopall()

    def test_collection_wins(self):
        self.assertEqual(self.run_chain(API_RESULT, RuntimeError("niet aanroepen")), API_RESULT)

    def test_empty_collection_falls_through_to_cookbook(self):
        self.assertEqual(self.run_chain(self.EMPTY, API_RESULT), API_RESULT)

    def test_collection_error_falls_through_to_cookbook(self):
        self.assertEqual(self.run_chain(RuntimeError("boom"), API_RESULT), API_RESULT)

    def test_empty_everywhere_is_empty_not_an_error(self):
        self.assertEqual(self.run_chain(self.EMPTY, RuntimeError("boom")), self.EMPTY)

    def test_both_fail_reports_both(self):
        with self.assertRaises(RuntimeError) as ctx:
            self.run_chain(RuntimeError("argument x"), RuntimeError("404"))
        self.assertIn("collectie: argument x", str(ctx.exception))
        self.assertIn("kookboek: 404", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
