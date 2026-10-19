import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("MEALIESHOPPER_DATA_DIR", tempfile.mkdtemp())

from mealieshopper import ah  # noqa: E402

API_RESULT = {"recipes": [{"id": "1", "title": "Soep", "image": "", "url": "u"}], "total": 1}
BROWSER_RESULT = {"recipes": [{"id": "2", "title": "Stamppot", "image": "", "url": "v"}], "total": 1}


def patched(token="tok", creds=("", ""), api=None, browser=None):
    """Patch de bronnen van get_saved_recipes; api/browser zijn resultaat of exception."""
    def runner(value):
        def call(*args, **kwargs):
            if isinstance(value, Exception):
                raise value
            return value
        return call

    return (
        mock.patch.object(ah, "configured_refresh_token", return_value=token),
        mock.patch.object(ah, "configured_browser_credentials", return_value=creds),
        mock.patch.object(ah, "_get_saved_recipes_api", side_effect=runner(api)),
        mock.patch.object(ah, "_get_saved_recipes_browser", side_effect=runner(browser)),
    )


def run(**kwargs):
    patches = patched(**kwargs)
    for patch in patches:
        patch.start()
    try:
        return ah.get_saved_recipes()
    finally:
        mock.patch.stopall()


class SavedRecipesSelectionTest(unittest.TestCase):
    def test_api_result_wins_without_browser(self):
        result = run(api=API_RESULT, creds=("a@b.nl", "pw"), browser=RuntimeError("niet aanroepen"))
        self.assertEqual(result, API_RESULT)

    def test_api_only_needs_no_password(self):
        self.assertEqual(run(api=API_RESULT), API_RESULT)

    def test_api_error_falls_back_to_browser(self):
        result = run(api=RuntimeError("404"), creds=("a@b.nl", "pw"), browser=BROWSER_RESULT)
        self.assertEqual(result, BROWSER_RESULT)

    def test_empty_api_result_tries_browser(self):
        empty = {"recipes": [], "total": 0}
        result = run(api=empty, creds=("a@b.nl", "pw"), browser=BROWSER_RESULT)
        self.assertEqual(result, BROWSER_RESULT)

    def test_empty_api_result_without_browser_is_returned(self):
        empty = {"recipes": [], "total": 0}
        self.assertEqual(run(api=empty), empty)

    def test_empty_api_result_kept_when_browser_fails(self):
        empty = {"recipes": [], "total": 0}
        result = run(api=empty, creds=("a@b.nl", "pw"), browser=RuntimeError("login mislukt"))
        self.assertEqual(result, empty)

    def test_both_fail_reports_both_errors(self):
        with self.assertRaises(RuntimeError) as ctx:
            run(api=RuntimeError("AH bewaarde recepten ophalen mislukt (404)"),
                creds=("a@b.nl", "pw"), browser=RuntimeError("nog steeds op de inlogpagina"))
        message = str(ctx.exception)
        self.assertIn("404", message)
        self.assertIn("website: nog steeds op de inlogpagina", message)

    def test_api_error_without_browser_credentials(self):
        with self.assertRaises(RuntimeError) as ctx:
            run(api=RuntimeError("AH bewaarde recepten ophalen mislukt (404)"))
        self.assertIn("404", str(ctx.exception))

    def test_nothing_linked(self):
        with self.assertRaises(RuntimeError) as ctx:
            run(token="")
        self.assertIn("Beheer > Albert Heijn", str(ctx.exception))


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
