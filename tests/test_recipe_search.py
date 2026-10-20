import os
import tempfile
import unittest
from unittest import mock

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

from mealieshopper import ah  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402


def graphql_response(result, total=None, has_next=True):
    response = mock.Mock(ok=True, status_code=200)
    response.json.return_value = {
        "data": {"recipeSearch": {
            "page": {"total": len(result) if total is None else total, "hasNextPage": has_next},
            "result": result,
        }}
    }
    return response


def item(recipe_id=1302917, **extra):
    base = {
        "id": recipe_id, "title": "Panlasagne met zalm", "slug": "panlasagne-met-zalm",
        "time": {"cook": 30, "oven": None, "wait": None}, "serving": {"number": 4},
        "images": [{"url": "https://img/220.jpg", "width": 220}, {"url": "https://img/440.jpg", "width": 440},
                   {"url": "https://img/1024.jpg", "width": 1024}],
        "courses": ["hoofdgerecht"],
    }
    base.update(extra)
    return base


class SearchTestCase(unittest.TestCase):
    def search(self, response, *args, **kwargs):
        with mock.patch.object(ah, "get_token", return_value="anon"), \
                mock.patch.object(ah.requests, "post", return_value=response) as post, \
                mock.patch.object(ah.requests, "get", side_effect=AssertionError("de oude REST-route is weg")):
            result = ah.search_recipes(*args, **kwargs)
        return result, post

    @staticmethod
    def sent(post):
        return post.call_args.kwargs["json"]["variables"]["query"]


class PickImageTest(unittest.TestCase):
    def test_closest_to_card_width(self):
        images = [{"url": "a", "width": 220}, {"url": "b", "width": 440}, {"url": "c", "width": 2048}]
        self.assertEqual(ah._pick_image(images), "b")
        self.assertEqual(ah._pick_image([{"url": "a", "width": 220}, {"url": "c", "width": 2048}]), "a")
        self.assertEqual(ah._pick_image([{"url": "c", "width": 2048}, {"url": "d", "width": 612}]), "d")

    def test_missing_or_broken_images(self):
        self.assertEqual(ah._pick_image([]), "")
        self.assertEqual(ah._pick_image(None), "")
        self.assertEqual(ah._pick_image([{"width": 440}, "kapot"]), "")
        self.assertEqual(ah._pick_image([{"url": "z"}]), "z")  # zonder breedte


class SearchRecipesTest(SearchTestCase):
    def test_maps_a_result_to_a_card(self):
        result, _ = self.search(graphql_response([item()], total=2396), "pasta")
        card = result["recipes"][0]
        self.assertEqual(card, {
            "id": "1302917", "title": "Panlasagne met zalm",
            "webPath": "/allerhande/recept/R-R1302917/panlasagne-met-zalm",
            "images": [{"url": "https://img/440.jpg"}], "totalTime": 30, "servings": 4, "courses": ["hoofdgerecht"],
        })
        self.assertEqual(result["total"], 2396)

    def test_total_time_adds_cooking_oven_and_waiting(self):
        result, _ = self.search(graphql_response([item(time={"cook": 20, "oven": 30, "wait": 15})]), "x")
        self.assertEqual(result["recipes"][0]["totalTime"], 65)
        result, _ = self.search(graphql_response([item(time=None)]), "x")
        self.assertEqual(result["recipes"][0]["totalTime"], 0)

    def test_gaps_in_the_data_do_not_break_a_card(self):
        sparse = item(slug=None, title=None, serving=None, images=None, courses=None)
        card = self.search(graphql_response([sparse]), "x")[0]["recipes"][0]
        self.assertEqual(card["webPath"], "/allerhande/recept/R-R1302917")
        self.assertEqual(card["title"], "Recept 1302917")
        self.assertEqual((card["servings"], card["images"], card["courses"]), (None, [], []))

    def test_entries_without_id_are_skipped(self):
        result, _ = self.search(graphql_response([item(), {"title": "zonder id"}]), "x")
        self.assertEqual(len(result["recipes"]), 1)

    def test_no_hits(self):
        result, _ = self.search(graphql_response([], total=0, has_next=False), "xqzvbnm")
        self.assertEqual(result, {"recipes": [], "total": 0, "hasNextPage": False})

    def test_search_text_travels_as_a_variable(self):
        text = 'pasta "met" \\ kip\n{ evil }'
        _, post = self.search(graphql_response([item()]), text)
        payload = post.call_args.kwargs["json"]
        self.assertEqual(self.sent(post)["searchText"], text)
        self.assertNotIn("evil", payload["query"])
        self.assertIn("$query: RecipeSearchParams!", payload["query"])

    def test_uses_the_graphql_endpoint_with_the_anonymous_token(self):
        _, post = self.search(graphql_response([item()]), "pasta")
        self.assertEqual(post.call_args.args[0], "https://api.ah.nl/graphql")
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer anon")


class PagingTest(SearchTestCase):
    def test_start_is_the_offset(self):
        for page, start in ((0, 0), (1, 12), (5, 60)):
            _, post = self.search(graphql_response([item()], total=500), "x", page, 12)
            self.assertEqual((self.sent(post)["start"], self.sent(post)["size"]), (start, 12), page)
            self.assertNotIn("page", self.sent(post))

    def test_has_next_page_follows_the_total(self):
        full = [item(n) for n in range(1, 13)]
        self.assertTrue(self.search(graphql_response(full, total=30), "x", 0)[0]["hasNextPage"])
        self.assertTrue(self.search(graphql_response(full, total=30), "x", 1)[0]["hasNextPage"])
        last = [item(n) for n in range(1, 7)]
        self.assertFalse(self.search(graphql_response(last, total=30), "x", 2)[0]["hasNextPage"])
        self.assertFalse(self.search(graphql_response(full, total=12), "x", 0)[0]["hasNextPage"])

    def test_window_of_2000_is_never_exceeded(self):
        # AH geeft een fout zodra start + size boven 2000 uitkomt.
        result, post = self.search(graphql_response([item(n) for n in range(8)], total=2396), "x", 166, 12)
        self.assertEqual((self.sent(post)["start"], self.sent(post)["size"]), (1992, 8))
        self.assertFalse(result["hasNextPage"], "voorbij het venster kan niet worden opgevraagd")
        self.assertEqual(result["total"], 2396, "het echte totaal blijft zichtbaar")
        _, post = self.search(graphql_response([item(n) for n in range(12)], total=2396), "x", 165, 12)
        self.assertEqual((self.sent(post)["start"], self.sent(post)["size"]), (1980, 12))

    def test_penultimate_window_page_still_has_a_next_page(self):
        result, _ = self.search(graphql_response([item(n) for n in range(12)], total=2396), "x", 165, 12)
        self.assertTrue(result["hasNextPage"])

    def test_beyond_the_window_is_a_clear_error_without_a_request(self):
        with mock.patch.object(ah.requests, "post", side_effect=AssertionError("geen verzoek")):
            with self.assertRaises(RuntimeError) as ctx:
                ah.search_recipes("x", 167, 12)
        self.assertIn("2.000", str(ctx.exception))
        self.assertIn("Verfijn", str(ctx.exception))


class SearchErrorsTest(SearchTestCase):
    def test_graphql_errors_name_the_problem(self):
        response = mock.Mock(ok=True, status_code=200)
        response.json.return_value = {"errors": [{"message": "unknown field input value at `$query.x`"}]}
        with self.assertRaises(RuntimeError) as ctx:
            self.search(response, "pasta")
        self.assertIn("AH zoeken mislukt", str(ctx.exception))
        self.assertIn("unknown field", str(ctx.exception))

    def test_http_errors_include_the_status(self):
        response = mock.Mock(ok=False, status_code=503, text="<HTML>Error</HTML>")
        with self.assertRaises(RuntimeError) as ctx:
            self.search(response, "pasta")
        self.assertIn("503", str(ctx.exception))

    def test_empty_data_is_no_hits_not_a_crash(self):
        response = mock.Mock(ok=True, status_code=200)
        response.json.return_value = {"data": {"recipeSearch": None}}
        result, _ = self.search(response, "pasta")
        self.assertEqual(result, {"recipes": [], "total": 0, "hasNextPage": False})


class SearchRouteTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"MEALIESHOPPER_DATA_DIR": tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = create_app().test_client()

    def test_returns_cards_with_paging(self):
        found = {"recipes": [{"id": "1"}], "total": 30, "hasNextPage": True}
        with mock.patch.object(ah, "search_recipes", return_value=found) as search:
            response = self.client.get("/api/ah/search?q=pasta&page=2")
        self.assertEqual(response.get_json(), found)
        search.assert_called_once_with("pasta", 2)

    def test_requires_a_search_term(self):
        self.assertEqual(self.client.get("/api/ah/search").status_code, 400)
        self.assertEqual(self.client.get("/api/ah/search?q=%20").status_code, 400)

    def test_errors_come_through_as_424(self):
        with mock.patch.object(ah, "search_recipes", side_effect=RuntimeError("AH zoeken mislukt: kapot")):
            response = self.client.get("/api/ah/search?q=pasta")
        self.assertEqual(response.status_code, 424)
        self.assertEqual(response.get_json()["error"], "AH zoeken mislukt: kapot")


if __name__ == "__main__":
    unittest.main()
