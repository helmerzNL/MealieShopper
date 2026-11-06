import asyncio
import json
import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("MEALIESHOPPER_DATA_DIR", tempfile.mkdtemp())

from mealieshopper import auth, jumbo, jumbo_explore, jumbo_lists  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402
from helpers import AppTestCase, run  # noqa: E402


def list_data(*items, active=True, typename="ShoppingList"):
    return {"shoppingList": {"__typename": typename, "isActive": active, "items": list(items)}}


def raw(item_id, term, sku=None, completed=False):
    return {"id": item_id, "term": term, "sku": sku, "completed": completed}


class TempDataDirTest(AppTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"MEALIESHOPPER_DATA_DIR": tmp.name, "PASSKEY_AUTH_ENABLED": "false"})
        patcher.start()
        self.addCleanup(patcher.stop)
        run(auth.init_db())
        jumbo._access_cache.update(token="acc", expires_at=jumbo.time.time() + 3600)
        jumbo._working_variant.clear()
        run(auth.set_secret(jumbo.REFRESH_SECRET, "ref"))


class ShoppingListApiTest(TempDataDirTest):
    def test_the_list_is_read_with_the_loyalty_client(self):
        data = list_data(raw("1", "melk"), raw("2", "brood", "131278BAK"), raw("3", "kaas", completed=True), {"id": "x"})
        with mock.patch.object(jumbo, "_graphql", return_value=data) as graphql:
            result = run(jumbo.get_shopping_list())
        self.assertEqual(graphql.call_args.args[2], "GetShoppingList")
        self.assertEqual(graphql.call_args.args[3], jumbo.LOYALTY_CLIENT)
        self.assertEqual([(i["term"], i["sku"], i["completed"]) for i in result["items"]],
                         [("melk", None, False), ("brood", "131278BAK", False), ("kaas", None, True)])
        self.assertTrue(result["active"])

    def test_an_inactive_list_is_reported(self):
        with mock.patch.object(jumbo, "_graphql", return_value=list_data(active=False)):
            self.assertFalse(run(jumbo.get_shopping_list())["active"])

    def test_the_query_falls_back_when_the_error_type_is_unknown(self):
        calls = []

        def fake(query, variables, operation, client):
            calls.append(query)
            if "ShoppingListError" in query:
                raise jumbo.JumboSchemaError('Fragment cannot be spread here as objects of type "ShoppingList"')
            return list_data(raw("1", "melk"))

        with mock.patch.object(jumbo, "_graphql", side_effect=fake):
            self.assertEqual(len(run(jumbo.get_shopping_list())["items"]), 1)
            self.assertEqual(len(run(jumbo.get_shopping_list())["items"]), 1)
        self.assertEqual(len(calls), 3)  # eerst de mislukte variant, daarna direct de werkende
        self.assertNotIn("ShoppingListError", calls[-1])

    def test_an_unexpected_answer_names_the_type(self):
        with mock.patch.object(jumbo, "_graphql", return_value=list_data(typename="NieuweFout")):
            with self.assertRaisesRegex(RuntimeError, "NieuweFout"):
                run(jumbo.get_shopping_list())
        with mock.patch.object(jumbo, "_graphql", return_value={}):
            with self.assertRaises(RuntimeError):
                run(jumbo.get_shopping_list())

    def test_adding_sends_term_and_sku_and_skips_what_is_already_there(self):
        answers = [list_data(raw("1", "Melk")), {"editShoppingListItems": {"__typename": "EditShoppingListItemsResult"}}]
        with mock.patch.object(jumbo, "_graphql", side_effect=answers) as graphql:
            result = run(jumbo.add_to_shopping_list([
                {"term": "melk"}, {"term": "  Halfvolle   melk 1 l ", "sku": "SKU1"}, {"term": ""}, {"term": "Halfvolle melk 1 l"}]))
        sent = graphql.call_args.args[1]["input"]["items"]
        self.assertEqual(sent, [{"term": "Halfvolle melk 1 l", "completed": False, "sku": "SKU1"}])
        self.assertEqual(result, {"added": ["Halfvolle melk 1 l"], "skipped": ["melk", "Halfvolle melk 1 l"]})

    def test_nothing_new_means_no_write(self):
        with mock.patch.object(jumbo, "_graphql", return_value=list_data(raw("1", "melk"))) as graphql:
            result = run(jumbo.add_to_shopping_list([{"term": "melk"}]))
        self.assertEqual(graphql.call_count, 1)
        self.assertEqual(result["added"], [])

    def test_an_inactive_list_is_switched_on_first(self):
        answers = [list_data(active=False), {"toggleShoppingList": {"__typename": "ShoppingListState"}},
                   {"editShoppingListItems": {"__typename": "EditShoppingListItemsResult"}}]
        with mock.patch.object(jumbo, "_graphql", side_effect=answers) as graphql:
            run(jumbo.add_to_shopping_list([{"term": "melk"}]))
        self.assertEqual([c.args[2] for c in graphql.call_args_list], ["GetShoppingList", "toggleShoppingList", "EditShoppingListItems"])
        self.assertEqual(graphql.call_args_list[1].args[1], {"input": {"active": True}})

    def test_a_refused_write_names_the_type(self):
        answers = [list_data(), {"editShoppingListItems": {"__typename": "ShoppingListError"}}]
        with mock.patch.object(jumbo, "_graphql", side_effect=answers):
            with self.assertRaisesRegex(RuntimeError, "ShoppingListError"):
                run(jumbo.add_to_shopping_list([{"term": "melk"}]))

    def test_product_details_fall_back_to_fewer_fields(self):
        def fake(query, variables, operation, client=jumbo.BASKET_CLIENT):
            if "subtitle" in query:
                raise jumbo.JumboSchemaError('Cannot query field "subtitle"')
            return {"product": {"sku": "S1", "title": "Melk", "link": "/p/S1", "price": {"price": 129, "promoPrice": None}}}

        with mock.patch.object(jumbo, "_graphql", side_effect=fake):
            product = run(jumbo.get_product("S1"))
        self.assertEqual((product["id"], product["price"], product["url"]), ("S1", 1.29, "https://www.jumbo.com/p/S1"))

    def test_an_unknown_product_is_none(self):
        with mock.patch.object(jumbo, "_graphql", return_value={"product": None}):
            self.assertIsNone(run(jumbo.get_product("S1")))


class LocalCopyTest(TempDataDirTest):
    LIVE = {"id": "JUMBO", "name": "Jumbo-lijstje", "items": [
        {"id": "1", "sku": "S1", "term": "melk", "product": {"title": "Halfvolle melk", "price": 1.29, "unitSize": "1 l", "url": "https://www.jumbo.com/p/S1"}},
        {"id": "2", "sku": None, "term": "brood", "product": None},
    ]}

    def test_snapshot_shows_the_term_when_there_is_no_product(self):
        snapshot = run(jumbo_lists.save_snapshot(self.LIVE))
        self.assertEqual([i["product"]["title"] for i in snapshot["items"]], ["Halfvolle melk", "brood"])
        self.assertEqual(snapshot["source"], "local")
        self.assertEqual(snapshot["sync"], {"new": 2, "removed": 0, "keptQuantities": 0})
        self.assertEqual((snapshot["items"][0]["productId"], snapshot["items"][1]["productId"]), ("S1", None))

    def test_edited_quantities_survive_a_sync_even_when_the_order_changes(self):
        run(jumbo_lists.save_snapshot(self.LIVE))
        run(jumbo_lists.set_quantity("JUMBO", 1, 4))
        reordered = {**self.LIVE, "items": [self.LIVE["items"][1], self.LIVE["items"][0], {"id": "3", "sku": None, "term": "kaas", "product": None}]}
        snapshot = run(jumbo_lists.save_snapshot(reordered))
        self.assertEqual([(i["term"], i["quantity"]) for i in snapshot["items"]], [("brood", 4), ("melk", 1), ("kaas", 1)])
        self.assertEqual(snapshot["sync"], {"new": 1, "removed": 0, "keptQuantities": 1})
        self.assertEqual(snapshot["editedCount"], 1)

    def test_lines_are_matched_on_text_when_jumbo_gives_new_ids(self):
        run(jumbo_lists.save_snapshot(self.LIVE))
        run(jumbo_lists.set_quantity("JUMBO", 1, 3))
        changed = {**self.LIVE, "items": [{"id": "99", "sku": None, "term": "Brood", "product": None}]}
        snapshot = run(jumbo_lists.save_snapshot(changed))
        self.assertEqual(snapshot["items"][0]["quantity"], 3)
        self.assertEqual(snapshot["sync"]["removed"], 1)

    def test_quantity_limits_and_reset(self):
        run(jumbo_lists.save_snapshot(self.LIVE))
        for bad in (0, 100, "x", None):
            with self.assertRaises(ValueError):
                run(jumbo_lists.set_quantity("JUMBO", 0, bad))
        self.assertIsNone(run(jumbo_lists.set_quantity("JUMBO", 9, 2)))
        run(jumbo_lists.set_quantity("JUMBO", 0, 5))
        self.assertEqual(run(jumbo_lists.reset_quantities("jumbo"))["items"][0]["quantity"], 1)
        self.assertIsNone(run(jumbo_lists.reset_quantities("ANDERS")))

    def test_search_folds_case_and_accents_and_needs_all_words(self):
        run(jumbo_lists.save_snapshot({**self.LIVE, "items": [
            {"id": "1", "sku": None, "term": "Crème fraîche", "product": None}, {"id": "2", "sku": None, "term": "melk", "product": None}]}))
        self.assertEqual(run(jumbo_lists.search_items("creme FRAICHE")), {"JUMBO": ["Crème fraîche"]})
        self.assertEqual(run(jumbo_lists.search_items("creme melk")), {})
        self.assertEqual(run(jumbo_lists.search_items("  ")), {})

    def test_fetch_live_skips_completed_lines_and_survives_missing_products(self):
        data = list_data(raw("1", "melk", "S1"), raw("2", "brood", "S2"), raw("3", "kaas", completed=True))
        products = {"S1": {"id": "S1", "title": "Halfvolle melk", "price": 1.29, "unitSize": "1 l", "url": None}}

        def product(sku):
            if sku in products:
                return products[sku]
            raise RuntimeError("stuk")

        with mock.patch.object(jumbo, "_graphql", return_value=data), mock.patch.object(jumbo, "get_product", side_effect=product):
            live = run(jumbo_lists.fetch_live())
        self.assertEqual([(i["term"], bool(i["product"])) for i in live["items"]], [("melk", True), ("brood", False)])

    def test_fetch_live_keeps_the_order_fetches_at_most_six_at_a_time_and_skips_lines_past_the_limit(self) -> None:
        count: int = jumbo.MAX_LIST_PRODUCTS + 2
        data = list_data(*(raw(str(n), f"regel {n}", None if n == 3 else f"S{n}") for n in range(count)))
        running: int = 0
        peak: int = 0
        asked: list[str] = []

        async def product(sku: str) -> dict[str, str]:
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            asked.append(sku)
            try:
                for _ in range(3):
                    await asyncio.sleep(0)  # zoals een echte aanvraag: de andere regels kunnen intussen beginnen
                if sku == "S5":
                    raise RuntimeError("stuk")
                return {"id": sku, "title": f"Product {sku}"}
            finally:
                running -= 1

        with mock.patch.object(jumbo, "_graphql", return_value=data), mock.patch.object(jumbo, "get_product", side_effect=product):
            live = run(jumbo_lists.fetch_live())
        titles = [(item["term"], (item["product"] or {}).get("title")) for item in live["items"]]
        self.assertEqual(len(titles), count)
        self.assertEqual(titles[:6], [("regel 0", "Product S0"), ("regel 1", "Product S1"), ("regel 2", "Product S2"),
                                      ("regel 3", None), ("regel 4", "Product S4"), ("regel 5", None)])
        self.assertEqual(titles[-2:], [(f"regel {count - 2}", None), (f"regel {count - 1}", None)], "voorbij de grens geen productgegevens")
        self.assertEqual(len(asked), jumbo.MAX_LIST_PRODUCTS - 1, "geen aanvraag voor een regel zonder SKU of voorbij de grens")
        self.assertEqual(peak, jumbo_lists.DETAIL_WORKERS)
        self.assertEqual(jumbo_lists.DETAIL_WORKERS, 6)

    def test_merged_lists_when_jumbo_is_up_down_or_never_synced(self):
        with mock.patch.object(jumbo, "get_shopping_list", return_value={"active": True, "items": [{"term": "a", "completed": False}, {"term": "b", "completed": True}]}), \
                mock.patch.object(jumbo, "get_product_lists", return_value=[]):
            up = run(jumbo_lists.merged_lists())
        self.assertEqual((up["lists"][0]["itemCount"], up["lists"][0]["synced"], up["lists"][0]["source"]), (1, False, "jumbo"))
        with mock.patch.object(jumbo, "get_shopping_list", side_effect=RuntimeError("geheime details")), \
                mock.patch.object(jumbo, "get_product_lists", side_effect=RuntimeError("geheime details")):
            self.assertEqual(run(jumbo_lists.merged_lists()), {"lists": [], "storeError": "Jumbo fout"})
            run(jumbo_lists.save_snapshot(self.LIVE))
            down = run(jumbo_lists.merged_lists())
        self.assertEqual((down["lists"][0]["source"], down["lists"][0]["itemCount"], down["storeError"]), ("local", 2, "Jumbo fout"))
        self.assertNotIn("geheime", json.dumps(down))

    def test_live_view_has_the_snapshot_shape_without_local_quantities(self):
        view = jumbo_lists.live_view(self.LIVE)
        self.assertEqual((view["source"], view["editedCount"], view["items"][1]["product"]["title"]), ("jumbo", 0, "brood"))


class ListRoutesTest(TempDataDirTest):
    def setUp(self):
        super().setUp()
        self.client = self.client_for(create_app())

    async def test_overview_is_always_200(self):
        with mock.patch.object(jumbo, "get_shopping_list", side_effect=RuntimeError("x")), \
                mock.patch.object(jumbo, "get_product_lists", side_effect=RuntimeError("x")):
            response = await self.client.get("/api/jumbo/lists")
        self.assertEqual((response.status_code, response.get_json()["lists"]), (200, []))

    async def test_sync_detail_quantity_reset_roundtrip(self):
        with mock.patch.object(jumbo_lists, "fetch_live", new_callable=mock.AsyncMock, return_value=LocalCopyTest.LIVE):
            synced = (await self.client.post("/api/jumbo/lists/jumbo/sync")).get_json()
        self.assertEqual(synced["totalSize"], 2)
        self.assertEqual((await self.client.get("/api/jumbo/lists/JUMBO")).get_json()["source"], "local")
        changed = (await self.client.patch("/api/jumbo/lists/JUMBO/items/0", json={"quantity": 3})).get_json()
        self.assertEqual(changed["items"][0]["quantity"], 3)
        self.assertEqual((await self.client.patch("/api/jumbo/lists/JUMBO/items/0", json={"quantity": 0})).status_code, 400)
        self.assertEqual((await self.client.patch("/api/jumbo/lists/JUMBO/items/7", json={"quantity": 2})).status_code, 404)
        self.assertEqual((await self.client.post("/api/jumbo/lists/JUMBO/reset")).get_json()["items"][0]["quantity"], 1)
        self.assertEqual((await self.client.get("/api/jumbo/lists/search?q=melk")).get_json()["items"], {"JUMBO": ["Halfvolle melk"]})

    async def test_an_unsynced_list_is_shown_live(self):
        with mock.patch.object(jumbo_lists, "fetch_live", new_callable=mock.AsyncMock, return_value=LocalCopyTest.LIVE):
            body = (await self.client.get("/api/jumbo/lists/JUMBO")).get_json()
        self.assertEqual((body["source"], body["items"][0]["product"]["title"]), ("jumbo", "Halfvolle melk"))

    async def test_sync_errors_are_generic(self):
        with mock.patch.object(jumbo_lists, "fetch_live", new_callable=mock.AsyncMock, side_effect=RuntimeError("geheim")):
            response = await self.client.post("/api/jumbo/lists/JUMBO/sync")
        self.assertEqual((response.status_code, response.get_json()["error"]), (424, "Jumbo fout"))

    async def test_list_to_cart_adds_skus_directly_and_searches_text_lines(self):
        await jumbo_lists.save_snapshot(LocalCopyTest.LIVE)
        await jumbo_lists.set_quantity("JUMBO", 1, 2)
        result = {"added": [], "failed": [], "cart": None}
        with mock.patch.object(jumbo, "search_product", return_value={"id": "S9", "title": "Brood"}) as search, \
                mock.patch.object(jumbo, "add_to_cart", return_value=result) as add:
            body = (await self.client.post("/api/jumbo/lists/JUMBO/cart")).get_json()
        search.assert_called_once_with("brood")
        self.assertEqual([(m["productId"], m["quantity"]) for m in add.call_args.args[0]], [("S1", 1), ("S9", 2)])
        self.assertEqual((body["added"], body["units"], body["name"]), (2, 3, "Jumbo-lijstje"))

    async def test_list_to_cart_reports_lines_it_could_not_place(self):
        await jumbo_lists.save_snapshot(LocalCopyTest.LIVE)
        result = {"added": [], "failed": [{"productId": "S1", "error": "x"}], "cart": None}
        with mock.patch.object(jumbo, "search_product", return_value=None), mock.patch.object(jumbo, "add_to_cart", return_value=result):
            body = (await self.client.post("/api/jumbo/lists/JUMBO/cart")).get_json()
        self.assertEqual((body["added"], sorted(body["skippedItems"])), (0, ["brood", "melk"]))

    async def test_list_to_cart_without_products_is_400(self):
        await jumbo_lists.save_snapshot({**LocalCopyTest.LIVE, "items": []})
        self.assertEqual((await self.client.post("/api/jumbo/lists/JUMBO/cart")).status_code, 400)

    async def test_ingredients_go_on_the_list_with_the_chosen_product(self):
        with mock.patch.object(jumbo, "add_to_shopping_list", return_value={"added": ["Halfvolle melk"], "skipped": ["eieren"]}) as add:
            body = (await self.client.post("/api/jumbo/list", json={"items": [
                {"query": "melk", "title": "Halfvolle melk", "productId": "S1", "quantity": 2}, {"query": "eieren"}]})).get_json()
        self.assertEqual(add.call_args.args[0], [{"term": "Halfvolle melk", "sku": "S1"}, {"term": "eieren", "sku": None}])
        self.assertEqual((body["added"], body["skipped"], body["skippedItems"]), (1, 1, ["eieren"]))
        self.assertEqual((await self.client.post("/api/jumbo/list", json={"items": []})).status_code, 400)

    async def test_list_errors_are_generic_and_users_can_read_the_list(self):
        with mock.patch.object(jumbo, "add_to_shopping_list", side_effect=RuntimeError("geheim")):
            response = await self.client.post("/api/jumbo/list", json={"items": [{"query": "melk"}]})
        self.assertEqual((response.status_code, response.get_json()["error"]), (424, "Jumbo fout"))


class ScriptsTest(AppTestCase):
    def test_every_script_function_is_defined_once(self):
        # Klassieke scripts delen één naamruimte: een tweede `function renderStoreChoice` in een later script
        # overschrijft stilletjes de eerste (zo verdween Jumbo uit de keuzelijst van de planner).
        import re
        from pathlib import Path

        seen: dict[str, str] = {}
        for path in sorted((Path(__file__).resolve().parent.parent / "static").glob("*.js")):
            for name in re.findall(r"^(?:async )?function (\w+)\(", path.read_text(encoding="utf-8"), re.M):
                if name in seen and name not in {"postJson", "foldText"}:  # bekende, gelijkwaardige dubbelen
                    self.fail(f"{name} staat in {seen[name]} en in {path.name}")
                seen[name] = path.name


class McpAndExploreTest(TempDataDirTest):
    def test_the_mcp_tool_prefers_the_local_copy(self):
        from mealieshopper import mcp

        run(jumbo_lists.save_snapshot(LocalCopyTest.LIVE))
        with mock.patch.object(jumbo_lists, "fetch_live", new_callable=mock.AsyncMock, side_effect=AssertionError("niet live")):
            result = run(mcp.tool_jumbo_list({}))
        self.assertEqual((result["source"], result["total"], result["items"][0]["item"]), ("lokale kopie", 2, "Halfvolle melk"))

    def test_the_mcp_tool_needs_a_link(self):
        from mealieshopper import mcp

        run(auth.delete_secret(jumbo.REFRESH_SECRET))
        with self.assertRaises(mcp.ToolError):
            run(mcp.tool_jumbo_list({}))

    def test_the_empty_list_write_probe_adds_nothing(self):
        with mock.patch.object(jumbo, "_graphql", return_value={"editShoppingListItems": {"__typename": "EditShoppingListItemsResult"}}) as graphql:
            result = run(jumbo_explore._empty_list_edit_probe())
        self.assertEqual(graphql.call_args.args[1], {"input": {"items": []}})
        self.assertEqual(result, {"typename": "EditShoppingListItemsResult"})

    def test_schema_errors_are_shown_but_other_errors_only_by_type(self):
        report, _ = run(jumbo_explore._probe("x", mock.Mock(side_effect=jumbo.JumboSchemaError('Cannot query field "foo" on type "Bar".'))))
        self.assertEqual((report["error"], report["schema"]), ("JumboSchemaError", 'Cannot query field "foo" on type "Bar".'))
        report, _ = run(jumbo_explore._probe("x", mock.Mock(side_effect=RuntimeError("persoonlijke details"))))
        self.assertEqual(report, {"name": "x", "ok": False, "error": "RuntimeError"})


if __name__ == "__main__":
    unittest.main()
