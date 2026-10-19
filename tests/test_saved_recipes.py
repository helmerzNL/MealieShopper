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


class SavedRecipesRestTest(unittest.TestCase):
    def test_ids_are_paged_and_hydrated(self):
        ids = mock.Mock(ok=True)
        ids.json.return_value = {"recipeIds": [1, 2, 3]}
        with mock.patch.object(ah, "get_user_token", return_value="tok"), \
                mock.patch.object(ah.requests, "get", return_value=ids), \
                mock.patch.object(ah, "_fetch_recipe_details", return_value=["x"]) as fetch:
            result = ah._get_saved_recipes_rest(page=1, size=2)
        fetch.assert_called_once_with("tok", [3])
        self.assertEqual(result, {"recipes": ["x"], "total": 3})

    def test_http_error_is_reported_with_status(self):
        bad = mock.Mock(ok=False, status_code=404, text="not_found")
        with mock.patch.object(ah, "get_user_token", return_value="tok"), \
                mock.patch.object(ah.requests, "get", return_value=bad):
            with self.assertRaises(RuntimeError) as ctx:
                ah._get_saved_recipes_rest(0, 50)
        self.assertIn("404", str(ctx.exception))


class SavedRecipesGraphqlTest(unittest.TestCase):
    def graphql_response(self, collected):
        response = mock.Mock(ok=True)
        response.json.return_value = {"data": {"cookBookRecipesCollectedMember": collected}}
        return response

    def test_recipes_are_mapped_and_paged(self):
        collected = {
            "result": [{"id": 1, "title": "Soep"}, {"id": 2, "title": None}, {"id": 3, "title": "Taart"}],
            "page": {"total": 3, "hasNextPage": False},
        }
        with mock.patch.object(ah, "get_user_token", return_value="tok"), \
                mock.patch.object(ah.requests, "post", return_value=self.graphql_response(collected)):
            first = ah._get_saved_recipes_graphql(page=0, size=2)
            second = ah._get_saved_recipes_graphql(page=1, size=2)
        self.assertEqual(first["total"], 3)
        self.assertEqual(
            first["recipes"][0],
            {"id": "1", "title": "Soep", "image": "", "url": "https://www.ah.nl/allerhande/recept/R-R1"},
        )
        self.assertEqual(first["recipes"][1]["title"], "Recept 2")
        self.assertEqual([r["id"] for r in second["recipes"]], ["3"])

    def test_empty_result(self):
        with mock.patch.object(ah, "get_user_token", return_value="tok"), \
                mock.patch.object(ah.requests, "post", return_value=self.graphql_response(None)):
            self.assertEqual(ah._get_saved_recipes_graphql(0, 50), {"recipes": [], "total": 0})

    def test_wrapper_prefers_graphql(self):
        with mock.patch.object(ah, "_get_saved_recipes_graphql", return_value=API_RESULT), \
                mock.patch.object(ah, "_get_saved_recipes_rest", side_effect=AssertionError("niet aanroepen")):
            self.assertEqual(ah._get_saved_recipes_api(0, 50), API_RESULT)

    def test_wrapper_falls_back_to_rest(self):
        with mock.patch.object(ah, "_get_saved_recipes_graphql", side_effect=RuntimeError("Cannot query field")), \
                mock.patch.object(ah, "_get_saved_recipes_rest", return_value=API_RESULT):
            self.assertEqual(ah._get_saved_recipes_api(0, 50), API_RESULT)

    def test_wrapper_reports_both_errors(self):
        with mock.patch.object(ah, "_get_saved_recipes_graphql", side_effect=RuntimeError("argument 'x' is required")), \
                mock.patch.object(ah, "_get_saved_recipes_rest", side_effect=RuntimeError("404 not_found")):
            with self.assertRaises(RuntimeError) as ctx:
                ah._get_saved_recipes_api(0, 50)
        self.assertIn("argument 'x' is required", str(ctx.exception))
        self.assertIn("REST: 404 not_found", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
