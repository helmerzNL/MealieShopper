import os
import tempfile
import unittest
from unittest import mock

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

from mealieshopper import ah, ah_recipes  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402
from helpers import AppTestCase, run  # noqa: E402


def recipe(n):
    return {"id": str(n), "title": f"Recept {n}", "image": f"https://img/{n}.jpg", "url": f"https://www.ah.nl/allerhande/recept/R-R{n}"}


def pager(total, size=ah_recipes.PAGE_SIZE):
    everything = [recipe(n) for n in range(1, total + 1)]

    def fake(page=0, size_=size):
        return {"recipes": everything[page * size_:(page + 1) * size_], "total": total}

    return fake


def group(group_id, name, ids, default=False):
    return {"id": str(group_id), "name": name, "isDefault": default, "recipeIds": list(ids)}


class SavedRecipesCacheTestCase(AppTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(os.environ, {"MEALIESHOPPER_DATA_DIR": tmp.name})
        env.start()
        self.addCleanup(env.stop)
        # Standaard heeft AH geen receptgroepen; de groepentests zetten er zelf een paar neer.
        groups = mock.patch.object(ah, "get_saved_recipe_groups", return_value=[])
        self.groups = groups.start()
        self.addCleanup(groups.stop)
        self.client = self.client_for(create_app())


class CacheTest(SavedRecipesCacheTestCase):
    def test_nothing_cached_yet(self):
        self.assertIsNone(run(ah_recipes.load()))

    def test_first_call_fetches_every_page_and_keeps_the_order(self):
        with mock.patch.object(ah, "get_saved_recipes", side_effect=pager(120)) as live:
            data = run(ah_recipes.get())
        self.assertEqual(live.call_count, 3)
        self.assertEqual(data["total"], 120)
        self.assertEqual([r["id"] for r in data["recipes"]][:3], ["1", "2", "3"])
        self.assertEqual(data["recipes"][-1]["id"], "120")
        self.assertTrue(data["syncedAt"])

    def test_second_call_does_not_touch_ah(self):
        with mock.patch.object(ah, "get_saved_recipes", side_effect=pager(5)):
            run(ah_recipes.get())
        with mock.patch.object(ah, "get_saved_recipes", side_effect=AssertionError("niet bij AH vragen")):
            data = run(ah_recipes.get())
        self.assertEqual(data["total"], 5)
        self.assertEqual(data["source"], "local")

    def test_forced_refresh_replaces_the_copy(self):
        with mock.patch.object(ah, "get_saved_recipes", side_effect=pager(5)):
            run(ah_recipes.get())
        with mock.patch.object(ah, "get_saved_recipes", side_effect=pager(2)):
            data = run(ah_recipes.get(force=True))
        self.assertEqual([r["id"] for r in data["recipes"]], ["1", "2"])
        self.assertEqual(run(ah_recipes.load())["total"], 2)

    def test_failed_refresh_keeps_the_copy_and_reports_the_error(self):
        with mock.patch.object(ah, "get_saved_recipes", side_effect=pager(3)):
            run(ah_recipes.get())
        with mock.patch.object(ah, "get_saved_recipes", side_effect=RuntimeError("AH sessie verlopen")):
            data = run(ah_recipes.get(force=True))
        self.assertEqual(data["total"], 3)
        self.assertEqual(data["ahError"], "AH sessie verlopen")
        self.assertEqual(run(ah_recipes.load())["total"], 3)

    def test_failure_halfway_leaves_the_old_copy_intact(self):
        with mock.patch.object(ah, "get_saved_recipes", side_effect=pager(3)):
            run(ah_recipes.get())
        first = pager(120)

        def flaky(page=0, size=50):
            if page == 1:
                raise RuntimeError("AH 500")
            return first(page, size)

        with mock.patch.object(ah, "get_saved_recipes", side_effect=flaky):
            run(ah_recipes.get(force=True))
        self.assertEqual(run(ah_recipes.load())["total"], 3)

    def test_failure_without_a_copy_is_raised(self):
        with mock.patch.object(ah, "get_saved_recipes", side_effect=RuntimeError("AH account niet gekoppeld")):
            with self.assertRaisesRegex(RuntimeError, "niet gekoppeld"):
                run(ah_recipes.get())

    def test_duplicates_and_endless_pages_are_cut_off(self):
        same = {"recipes": [recipe(1), recipe(2)], "total": 99}
        with mock.patch.object(ah, "get_saved_recipes", return_value=same) as live:
            data = run(ah_recipes.get())
        self.assertEqual(live.call_count, 2, "stopt zodra een pagina niets nieuws geeft")
        self.assertEqual(data["total"], 2)

    def test_cache_only_never_asks_ah(self):
        with mock.patch.object(ah, "get_saved_recipes", side_effect=AssertionError("niet bij AH vragen")):
            data = run(ah_recipes.get(fetch_if_missing=False))
        self.assertEqual(data["source"], "none")
        self.assertEqual(data["recipes"], [])
        self.assertIsNone(data["syncedAt"])
        self.assertIsNone(run(ah_recipes.load()), "een lege uitkomst is geen kopie")

    def test_cache_only_returns_an_existing_copy(self):
        with mock.patch.object(ah, "get_saved_recipes", side_effect=pager(3)):
            run(ah_recipes.get())
        with mock.patch.object(ah, "get_saved_recipes", side_effect=AssertionError):
            self.assertEqual(run(ah_recipes.get(fetch_if_missing=False))["total"], 3)

    def test_empty_collection_is_a_valid_copy(self):
        with mock.patch.object(ah, "get_saved_recipes", return_value={"recipes": [], "total": 0}):
            data = run(ah_recipes.get())
        self.assertEqual(data["recipes"], [])
        with mock.patch.object(ah, "get_saved_recipes", side_effect=AssertionError):
            self.assertEqual(run(ah_recipes.get())["total"], 0)


class GroupsTest(SavedRecipesCacheTestCase):
    def fetch(self, total, groups, force=False):
        self.groups.return_value = groups
        self.groups.side_effect = None
        with mock.patch.object(ah, "get_saved_recipes", side_effect=pager(total)):
            return run(ah_recipes.get(force=force))

    def test_groups_are_kept_in_ah_order_with_their_own_recipe_order(self):
        data = self.fetch(5, [group(7, "Taarten", [4, 2]), group(3, "Soepen", [1, 5, 3])])
        self.assertEqual(
            [(g["id"], g["name"], g["count"], g["recipeIds"], g["isDefault"]) for g in data["groups"]],
            [("7", "Taarten", 2, ["4", "2"], False), ("3", "Soepen", 3, ["1", "5", "3"], False)],
        )
        with mock.patch.object(ah, "get_saved_recipe_groups", side_effect=AssertionError("niet bij AH vragen")):
            self.assertEqual(run(ah_recipes.get())["groups"], data["groups"], "de groepen komen uit de kopie")

    def test_default_group_with_everything_is_the_same_as_all_recipes(self):
        data = self.fetch(3, [group(1, "Bewaard", [3, 2, 1], default=True), group(2, "Snel", [2])])
        self.assertEqual([g["name"] for g in data["groups"]], ["Snel"])

    def test_default_group_with_a_part_stays(self):
        data = self.fetch(3, [group(1, "Bewaard", [1, 2], default=True), group(2, "Snel", [3])])
        self.assertEqual([(g["name"], g["isDefault"]) for g in data["groups"]], [("Bewaard", True), ("Snel", False)])

    def test_unknown_and_double_recipes_in_a_group_are_left_out(self):
        data = self.fetch(2, [group(1, "Mix", [2, 99, 2, 1]), group(2, "Leeg", [])])
        self.assertEqual([(g["name"], g["recipeIds"], g["count"]) for g in data["groups"]], [("Mix", ["2", "1"], 2), ("Leeg", [], 0)])

    def test_failing_groups_still_keep_the_recipes(self):
        self.groups.side_effect = RuntimeError("collectie: 500")
        with mock.patch.object(ah, "get_saved_recipes", side_effect=pager(4)):
            data = run(ah_recipes.get())
        self.assertEqual((data["total"], data["groups"]), (4, []))
        self.assertEqual(run(ah_recipes.load())["total"], 4)

    def test_refresh_replaces_the_groups(self):
        self.fetch(3, [group(1, "Oud", [1]), group(2, "Ook oud", [2])])
        data = self.fetch(3, [group(5, "Nieuw", [3])], force=True)
        self.assertEqual([g["name"] for g in data["groups"]], ["Nieuw"])
        self.assertEqual([g["name"] for g in run(ah_recipes.load())["groups"]], ["Nieuw"])

    def test_empty_and_cache_only_have_no_groups(self):
        self.assertEqual(run(ah_recipes.get(fetch_if_missing=False))["groups"], [])
        self.assertEqual(ah_recipes.EMPTY["groups"], [], "de lege uitkomst wordt niet gedeeld aangepast")

    def test_resolve_group_by_id_or_name(self):
        groups = self.fetch(3, [group(1, "Taarten", [1]), group(2, "Hartige taarten", [2]), group(3, "Crème brûlée", [3])])["groups"]
        self.assertIsNone(ah_recipes.resolve_group(groups, "  "))
        self.assertEqual(ah_recipes.resolve_group(groups, "2")["name"], "Hartige taarten")
        self.assertEqual(ah_recipes.resolve_group(groups, "hartig")["id"], "2")
        self.assertEqual(ah_recipes.resolve_group(groups, "taarten")["id"], "1", "een exacte naam wint van een deel")
        self.assertEqual(ah_recipes.resolve_group(groups, "creme")["id"], "3")
        with self.assertRaisesRegex(ValueError, "Kies uit"):
            ah_recipes.resolve_group(groups, "soep")
        with self.assertRaisesRegex(ValueError, "geen receptgroepen"):
            ah_recipes.resolve_group([], "soep")


class RouteTest(SavedRecipesCacheTestCase):
    async def test_route_serves_the_groups(self):
        self.groups.return_value = [group(4, "Feest", [2, 1])]
        with mock.patch.object(ah, "get_saved_recipes", side_effect=pager(2)):
            data = (await self.client.get("/api/ah/recipes/saved")).get_json()
        self.assertEqual(data["groups"], [{"id": "4", "name": "Feest", "isDefault": False, "count": 2, "recipeIds": ["2", "1"]}])

    async def test_route_serves_the_copy_and_refresh_goes_to_ah(self):
        with mock.patch.object(ah, "get_saved_recipes", side_effect=pager(4)) as live:
            first = (await self.client.get("/api/ah/recipes/saved")).get_json()
            again = (await self.client.get("/api/ah/recipes/saved")).get_json()
            self.assertEqual(live.call_count, 1)
            self.assertEqual(first["recipes"], again["recipes"])
            await self.client.get("/api/ah/recipes/saved?refresh=1")
            self.assertEqual(live.call_count, 2)

    async def test_route_cached_flag_never_calls_ah(self):
        with mock.patch.object(ah, "get_saved_recipes", side_effect=AssertionError("niet bij AH vragen")):
            res = await self.client.get("/api/ah/recipes/saved?cached=1")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()["source"], "none")
        with mock.patch.object(ah, "get_saved_recipes", side_effect=pager(2)) as live:
            await self.client.get("/api/ah/recipes/saved?refresh=1&cached=1")
        self.assertEqual(live.call_count, 1, "refresh wint van cached")
        with mock.patch.object(ah, "get_saved_recipes", side_effect=AssertionError):
            self.assertEqual((await self.client.get("/api/ah/recipes/saved?cached=1")).get_json()["total"], 2)

    async def test_route_error_without_a_copy_is_424(self):
        with mock.patch.object(ah, "get_saved_recipes", side_effect=RuntimeError("AH sessie verlopen")):
            res = await self.client.get("/api/ah/recipes/saved")
        self.assertEqual(res.status_code, 424)
        self.assertEqual(res.get_json()["error"], "AH sessie verlopen")


if __name__ == "__main__":
    unittest.main()
