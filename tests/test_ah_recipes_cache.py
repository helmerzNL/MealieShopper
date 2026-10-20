import os
import tempfile
import unittest
from unittest import mock

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

from mealieshopper import ah, ah_recipes  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402


def recipe(n):
    return {"id": str(n), "title": f"Recept {n}", "image": f"https://img/{n}.jpg", "url": f"https://www.ah.nl/allerhande/recept/R-R{n}"}


def pager(total, size=ah_recipes.PAGE_SIZE):
    everything = [recipe(n) for n in range(1, total + 1)]

    def fake(page=0, size_=size):
        return {"recipes": everything[page * size_:(page + 1) * size_], "total": total}

    return fake


class SavedRecipesCacheTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(os.environ, {"MEALIESHOPPER_DATA_DIR": tmp.name})
        env.start()
        self.addCleanup(env.stop)
        self.client = create_app().test_client()


class CacheTest(SavedRecipesCacheTestCase):
    def test_nothing_cached_yet(self):
        self.assertIsNone(ah_recipes.load())

    def test_first_call_fetches_every_page_and_keeps_the_order(self):
        with mock.patch.object(ah, "get_saved_recipes", side_effect=pager(120)) as live:
            data = ah_recipes.get()
        self.assertEqual(live.call_count, 3)
        self.assertEqual(data["total"], 120)
        self.assertEqual([r["id"] for r in data["recipes"]][:3], ["1", "2", "3"])
        self.assertEqual(data["recipes"][-1]["id"], "120")
        self.assertTrue(data["syncedAt"])

    def test_second_call_does_not_touch_ah(self):
        with mock.patch.object(ah, "get_saved_recipes", side_effect=pager(5)):
            ah_recipes.get()
        with mock.patch.object(ah, "get_saved_recipes", side_effect=AssertionError("niet bij AH vragen")):
            data = ah_recipes.get()
        self.assertEqual(data["total"], 5)
        self.assertEqual(data["source"], "local")

    def test_forced_refresh_replaces_the_copy(self):
        with mock.patch.object(ah, "get_saved_recipes", side_effect=pager(5)):
            ah_recipes.get()
        with mock.patch.object(ah, "get_saved_recipes", side_effect=pager(2)):
            data = ah_recipes.get(force=True)
        self.assertEqual([r["id"] for r in data["recipes"]], ["1", "2"])
        self.assertEqual(ah_recipes.load()["total"], 2)

    def test_failed_refresh_keeps_the_copy_and_reports_the_error(self):
        with mock.patch.object(ah, "get_saved_recipes", side_effect=pager(3)):
            ah_recipes.get()
        with mock.patch.object(ah, "get_saved_recipes", side_effect=RuntimeError("AH sessie verlopen")):
            data = ah_recipes.get(force=True)
        self.assertEqual(data["total"], 3)
        self.assertEqual(data["ahError"], "AH sessie verlopen")
        self.assertEqual(ah_recipes.load()["total"], 3)

    def test_failure_halfway_leaves_the_old_copy_intact(self):
        with mock.patch.object(ah, "get_saved_recipes", side_effect=pager(3)):
            ah_recipes.get()
        first = pager(120)

        def flaky(page=0, size=50):
            if page == 1:
                raise RuntimeError("AH 500")
            return first(page, size)

        with mock.patch.object(ah, "get_saved_recipes", side_effect=flaky):
            ah_recipes.get(force=True)
        self.assertEqual(ah_recipes.load()["total"], 3)

    def test_failure_without_a_copy_is_raised(self):
        with mock.patch.object(ah, "get_saved_recipes", side_effect=RuntimeError("AH account niet gekoppeld")):
            with self.assertRaisesRegex(RuntimeError, "niet gekoppeld"):
                ah_recipes.get()

    def test_duplicates_and_endless_pages_are_cut_off(self):
        same = {"recipes": [recipe(1), recipe(2)], "total": 99}
        with mock.patch.object(ah, "get_saved_recipes", return_value=same) as live:
            data = ah_recipes.get()
        self.assertEqual(live.call_count, 2, "stopt zodra een pagina niets nieuws geeft")
        self.assertEqual(data["total"], 2)

    def test_cache_only_never_asks_ah(self):
        with mock.patch.object(ah, "get_saved_recipes", side_effect=AssertionError("niet bij AH vragen")):
            data = ah_recipes.get(fetch_if_missing=False)
        self.assertEqual(data["source"], "none")
        self.assertEqual(data["recipes"], [])
        self.assertIsNone(data["syncedAt"])
        self.assertIsNone(ah_recipes.load(), "een lege uitkomst is geen kopie")

    def test_cache_only_returns_an_existing_copy(self):
        with mock.patch.object(ah, "get_saved_recipes", side_effect=pager(3)):
            ah_recipes.get()
        with mock.patch.object(ah, "get_saved_recipes", side_effect=AssertionError):
            self.assertEqual(ah_recipes.get(fetch_if_missing=False)["total"], 3)

    def test_empty_collection_is_a_valid_copy(self):
        with mock.patch.object(ah, "get_saved_recipes", return_value={"recipes": [], "total": 0}):
            data = ah_recipes.get()
        self.assertEqual(data["recipes"], [])
        with mock.patch.object(ah, "get_saved_recipes", side_effect=AssertionError):
            self.assertEqual(ah_recipes.get()["total"], 0)


class RouteTest(SavedRecipesCacheTestCase):
    def test_route_serves_the_copy_and_refresh_goes_to_ah(self):
        with mock.patch.object(ah, "get_saved_recipes", side_effect=pager(4)) as live:
            first = self.client.get("/api/ah/recipes/saved").get_json()
            again = self.client.get("/api/ah/recipes/saved").get_json()
            self.assertEqual(live.call_count, 1)
            self.assertEqual(first["recipes"], again["recipes"])
            self.client.get("/api/ah/recipes/saved?refresh=1")
            self.assertEqual(live.call_count, 2)

    def test_route_cached_flag_never_calls_ah(self):
        with mock.patch.object(ah, "get_saved_recipes", side_effect=AssertionError("niet bij AH vragen")):
            res = self.client.get("/api/ah/recipes/saved?cached=1")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()["source"], "none")
        with mock.patch.object(ah, "get_saved_recipes", side_effect=pager(2)) as live:
            self.client.get("/api/ah/recipes/saved?refresh=1&cached=1")
        self.assertEqual(live.call_count, 1, "refresh wint van cached")
        with mock.patch.object(ah, "get_saved_recipes", side_effect=AssertionError):
            self.assertEqual(self.client.get("/api/ah/recipes/saved?cached=1").get_json()["total"], 2)

    def test_route_error_without_a_copy_is_424(self):
        with mock.patch.object(ah, "get_saved_recipes", side_effect=RuntimeError("AH sessie verlopen")):
            res = self.client.get("/api/ah/recipes/saved")
        self.assertEqual(res.status_code, 424)
        self.assertEqual(res.get_json()["error"], "AH sessie verlopen")


if __name__ == "__main__":
    unittest.main()
