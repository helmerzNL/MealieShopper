"""Jumbo's "Mijn lijstjes": eigen lijstjes en Favorieten, via opgeslagen queries.

De fixtures hebben de vorm van de echte antwoorden van jumbo.com (GetCustomerProductLists en GetProductList),
met verzonnen id's, titels en plaatjes.
"""
import json
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("MEALIESHOPER_DATA_DIR", tempfile.mkdtemp())

from mealieshopper import auth, jumbo, jumbo_explore, jumbo_lists, mcp  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402

OWN = "5e8f398d5c1add2c185b229d"
OWN2 = "5eeb8bd189e849bb83470697"
FAV = "5daedbf40b105b0010e28ce6"


def summary(list_id, title, count, kind="CUSTOMER", description=None):
    return {"id": list_id, "productsCount": count, "title": title, "type": kind, "followersCount": 0,
            "description": description, "author": None, "__typename": "ProductList",
            "items": [{"product": {"image": "https://img/1.png", "__typename": "Product"}, "__typename": "ProductListItem"}]}


OVERVIEW = {
    "customerLists": {"items": [summary(OWN, "Basis Lijstje", 24), summary(OWN2, "Dit vinden we lekker", 35, description="Lekker")], "total": 2},
    "favouriteLists": {"items": [summary(FAV, "Favorieten", 16, "FAVORITE")], "total": 1},
    "followingLists": {"total": 1},
}


def product_item(n, *, price=449, promo=None, amount=1, image="https://img/p.png", promotions=None, available=True):
    sku = f"{n:06d}ZK"
    return {
        "id": f"item{n}", "sku": sku, "orderIndex": n, "__typename": "ProductListItem",
        "quantity": {"amount": amount, "unit": "pieces", "__typename": "ProductQuantity"},
        "product": {
            "sku": sku, "id": sku, "brand": "Jumbo", "category": "Diepvries", "subtitle": "750 g",
            "title": f"Jumbo Mango Blokjes {n}", "image": image,
            "availability": {"availability": "AVAILABLE", "isAvailable": available},
            "link": f"/producten/jumbo-mango-blokjes-{sku}",
            "price": {"price": price, "promoPrice": promo, "pricePerUnit": {"price": 599, "unit": "kg"}},
            "promotions": promotions or [], "quantityDetails": {"maxAmount": 99.0}, "__typename": "Product",
        },
    }


def list_page(items, count, title="Basis Lijstje", list_id=OWN):
    return {"productListV2": {"id": list_id, "type": "CUSTOMER", "title": title, "productsCount": count, "items": items}}


class TempDataDirTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"MEALIESHOPPER_DATA_DIR": tmp.name, "PASSKEY_AUTH_ENABLED": "false"})
        patcher.start()
        self.addCleanup(patcher.stop)
        auth.init_db()
        jumbo._access_cache.update(token="acc", expires_at=jumbo.time.time() + 3600)
        jumbo._working_variant.clear()
        jumbo._persisted_route.clear()
        auth.set_secret(jumbo.REFRESH_SECRET, "ref")


class OverviewTest(TempDataDirTest):
    def test_own_lists_come_before_favourites_and_followed_lists_are_ignored(self):
        with mock.patch.object(jumbo, "_persisted_query", return_value=OVERVIEW) as query:
            lists = jumbo.get_product_lists()
        self.assertEqual(query.call_args.args[0], "GetCustomerProductLists")
        self.assertEqual(query.call_args.args[1], {"listItemPagination": {"offset": 0, "limit": 4}, "listPagination": {"offset": 0, "limit": 25}})
        self.assertEqual([(i["name"], i["kind"], i["itemCount"]) for i in lists],
                         [("Basis Lijstje", "own", 24), ("Dit vinden we lekker", "own", 35), ("Favorieten", "favorites", 16)])
        self.assertEqual((lists[0]["id"], lists[1]["description"], lists[0]["description"]), (OWN, "Lekker", None))

    def test_odd_entries_are_skipped(self):
        data = {"customerLists": {"items": [None, {"id": "x"}, {"title": "zonder id"}, summary(OWN, "Goed", 1)]}, "favouriteLists": None}
        with mock.patch.object(jumbo, "_persisted_query", return_value=data):
            self.assertEqual([i["name"] for i in jumbo.get_product_lists()], ["Goed"])
        with mock.patch.object(jumbo, "_persisted_query", return_value={}):
            self.assertEqual(jumbo.get_product_lists(), [])


class PersistedQueryTest(TempDataDirTest):
    def test_the_request_has_a_hash_and_no_query_text(self):
        with mock.patch.object(jumbo.requests, "post", return_value=mock.Mock(status_code=200, ok=True, json=lambda: {"data": {"x": 1}})) as post:
            jumbo._graphql(None, {"a": 1}, "GetCustomerProductLists", "JUMBO_WEB-lists",
                           {"persistedQuery": {"version": 1, "sha256Hash": "abc"}})
        sent = post.call_args.kwargs["json"]
        self.assertNotIn("query", sent)
        self.assertEqual(sent["extensions"]["persistedQuery"]["sha256Hash"], "abc")
        self.assertEqual(post.call_args.kwargs["headers"]["apollographql-client-name"], "JUMBO_WEB-lists")

    def test_get_puts_everything_in_the_query_string_like_the_website(self):
        with mock.patch.object(jumbo.requests, "get", return_value=mock.Mock(status_code=200, ok=True, json=lambda: {"data": {}})) as get:
            jumbo._graphql(None, {"a": 1}, "GetProductList", "JUMBO_WEB-lists",
                           {"persistedQuery": {"version": 1, "sha256Hash": "abc"}}, method="GET")
        params = get.call_args.kwargs["params"]
        self.assertEqual(params["operationName"], "GetProductList")
        self.assertEqual(json.loads(params["variables"]), {"a": 1})
        self.assertEqual(json.loads(params["extensions"])["persistedQuery"]["sha256Hash"], "abc")
        self.assertNotIn("query", params)

    def test_an_unknown_hash_is_named(self):
        body = {"errors": [{"message": "PersistedQueryNotFound", "extensions": {"code": "PERSISTED_QUERY_NOT_FOUND"}}]}
        with mock.patch.object(jumbo.requests, "post", return_value=mock.Mock(status_code=200, ok=True, json=lambda: body)):
            with self.assertRaises(jumbo.JumboPersistedQueryError):
                jumbo._graphql(None, {}, "GetProductList", "JUMBO_WEB", {"persistedQuery": {}})

    def test_the_ladder_tries_transport_and_client_and_remembers_what_worked(self):
        calls = []

        def fake(query, variables, operation, client, extensions, method):
            calls.append((method, client))
            if (method, client) != ("GET", "JUMBO_WEB-loyalty"):
                raise RuntimeError("400: nee")
            return {"customerLists": {"items": []}}

        with mock.patch.object(jumbo, "_graphql", side_effect=fake):
            jumbo._persisted_query("GetCustomerProductLists", {})
            first = list(calls)
            calls.clear()
            jumbo._persisted_query("GetCustomerProductLists", {})
        self.assertEqual(first[:3], [("POST", "JUMBO_WEB-lists"), ("GET", "JUMBO_WEB-lists"), ("POST", "JUMBO_WEB-loyalty")])
        self.assertEqual(first[-1], ("GET", "JUMBO_WEB-loyalty"))
        self.assertEqual(calls, [("GET", "JUMBO_WEB-loyalty")])
        self.assertEqual(jumbo._persisted_route["GetCustomerProductLists"], ("GET", "JUMBO_WEB-loyalty"))

    def test_a_route_that_stops_working_is_searched_again(self):
        jumbo._persisted_route["GetProductList"] = ("POST", "JUMBO_WEB-lists")

        def fake(query, variables, operation, client, extensions, method):
            if (method, client) == ("POST", "JUMBO_WEB-lists"):
                raise RuntimeError("gone")
            return {"ok": True}

        with mock.patch.object(jumbo, "_graphql", side_effect=fake):
            self.assertEqual(jumbo._persisted_query("GetProductList", {}), {"ok": True})
        self.assertNotEqual(jumbo._persisted_route["GetProductList"], ("POST", "JUMBO_WEB-lists"))

    def test_when_every_route_says_unknown_hash_that_is_what_is_reported(self):
        with mock.patch.object(jumbo, "_graphql", side_effect=jumbo.JumboPersistedQueryError("PersistedQueryNotFound")) as graphql:
            with self.assertRaisesRegex(jumbo.JumboPersistedQueryError, "PersistedQueryNotFound"):
                jumbo._persisted_query("GetProductList", {})
        self.assertEqual(graphql.call_count, len(jumbo.LIST_CLIENTS) * len(jumbo.LIST_TRANSPORTS))
        self.assertNotIn("GetProductList", jumbo._persisted_route)

    def test_a_dead_link_stops_the_ladder_at_once(self):
        with mock.patch.object(jumbo, "_graphql", side_effect=jumbo.JumboAuthError("weg")) as graphql:
            with self.assertRaises(jumbo.JumboAuthError):
                jumbo._persisted_query("GetProductList", {})
        self.assertEqual(graphql.call_count, 1)


class ProductListTest(TempDataDirTest):
    def test_all_pages_are_fetched_and_items_are_normalized(self):
        pages = [list_page([product_item(n) for n in range(25)], 30), list_page([product_item(n) for n in range(25, 30)], 30)]
        with mock.patch.object(jumbo, "_persisted_query", side_effect=pages) as query:
            result = jumbo.get_product_list(OWN)
        self.assertEqual([c.args[1]["listItemsPagination"] for c in query.call_args_list],
                         [{"offset": 0, "limit": 25}, {"offset": 25, "limit": 25}])
        first = query.call_args_list[0].args[1]
        self.assertEqual((first["listId"], first["includeDelistedItems"], first["includeThumbnail"]), (OWN, False, False))
        self.assertRegex(first["referenceDate"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.000Z$")
        self.assertEqual((result["id"], result["name"], len(result["items"])), (OWN, "Basis Lijstje", 30))

    def test_item_fields(self):
        data = list_page([product_item(1, price=449, promo=399, amount=3, promotions=[{"tags": []}])], 1)
        with mock.patch.object(jumbo, "_persisted_query", return_value=data):
            item = jumbo.get_product_list(OWN)["items"][0]
        self.assertEqual((item["id"], item["sku"], item["quantity"]), ("item1", "000001ZK", 3))
        product = item["product"]
        self.assertEqual((product["price"], product["isBonus"], product["brand"], product["unitSize"]), (3.99, True, "Jumbo", "750 g"))
        self.assertEqual(product["url"], "https://www.jumbo.com/producten/jumbo-mango-blokjes-000001ZK")
        self.assertEqual(item["term"], "Jumbo Mango Blokjes 1")

    def test_empty_images_and_odd_quantities_are_tolerated(self):
        items = [product_item(1, image=""), product_item(2, amount=None), product_item(3, amount=500), product_item(4, amount=0), {"sku": "x"}, None]
        with mock.patch.object(jumbo, "_persisted_query", return_value=list_page(items, 6)):
            result = jumbo.get_product_list(OWN)["items"]
        self.assertEqual([i["product"]["image"] for i in result][:1], [None])
        self.assertEqual([i["quantity"] for i in result], [1, 1, 99, 1])

    def test_an_unavailable_product_is_still_listed_but_marked(self):
        with mock.patch.object(jumbo, "_persisted_query", return_value=list_page([product_item(1, available=False)], 1)):
            self.assertFalse(jumbo.get_product_list(OWN)["items"][0]["product"]["available"])

    def test_pages_stop_when_jumbo_runs_out_of_items(self):
        with mock.patch.object(jumbo, "_persisted_query", side_effect=[list_page([product_item(1)], 50), list_page([], 50)]) as query:
            self.assertEqual(len(jumbo.get_product_list(OWN)["items"]), 1)
        self.assertEqual(query.call_count, 2)

    def test_a_missing_list_is_an_error_and_bad_ids_never_reach_jumbo(self):
        with mock.patch.object(jumbo, "_persisted_query", return_value={"productListV2": None}):
            with self.assertRaises(RuntimeError):
                jumbo.get_product_list(OWN)
        with mock.patch.object(jumbo, "_persisted_query", side_effect=AssertionError("niet vragen")):
            for bad in ("", "a b", "x" * 80, '1"}', None):
                with self.assertRaises(ValueError):
                    jumbo.get_product_list(bad)


class LocalCopyTest(TempDataDirTest):
    def live(self, *amounts):
        return {"id": OWN, "name": "Basis Lijstje", "items": [
            jumbo._product_list_item(product_item(n, amount=amount)) for n, amount in enumerate(amounts, 1)]}

    def test_ids_keep_their_case_except_the_shopping_list(self):
        self.assertEqual(jumbo_lists.normalize_id(f" {OWN} "), OWN)
        self.assertEqual(jumbo_lists.normalize_id("5E8F"), "5E8F")
        self.assertEqual((jumbo_lists.normalize_id("jumbo"), jumbo_lists.normalize_id("JUMBO")), ("JUMBO", "JUMBO"))

    def test_fetch_live_uses_the_right_source(self):
        with mock.patch.object(jumbo, "get_product_list", return_value={"id": OWN, "name": "x", "items": []}) as own, \
                mock.patch.object(jumbo, "get_shopping_list", return_value={"active": True, "items": []}) as shopping:
            jumbo_lists.fetch_live(OWN)
            own.assert_called_once_with(OWN)
            shopping.assert_not_called()
            self.assertEqual(jumbo_lists.fetch_live("jumbo")["name"], "Boodschappenlijstje")

    def test_jumbo_s_quantities_are_the_defaults_and_edits_survive_a_sync(self):
        snapshot = jumbo_lists.save_snapshot(self.live(2, 1))
        self.assertEqual([(i["quantity"], i["defaultQuantity"], i["edited"]) for i in snapshot["items"]], [(2, 2, False), (1, 1, False)])
        self.assertEqual(snapshot["items"][0]["product"]["brand"], "Jumbo")
        jumbo_lists.set_quantity(OWN, 0, 5)
        synced = jumbo_lists.save_snapshot(self.live(3, 1))  # Jumbo wijzigt het aantal; jouw aanpassing blijft
        self.assertEqual((synced["items"][0]["quantity"], synced["items"][0]["defaultQuantity"], synced["items"][0]["edited"]), (5, 3, True))
        self.assertEqual(synced["sync"]["keptQuantities"], 1)
        self.assertEqual(jumbo_lists.reset_quantities(OWN)["items"][0]["quantity"], 3)  # terug naar Jumbo's aantal

    def test_an_old_database_gets_the_brand_column(self):
        with sqlite3.connect(os.path.join(os.environ["MEALIESHOPPER_DATA_DIR"], "auth.sqlite3")) as conn:
            conn.execute("DROP TABLE IF EXISTS jumbo_list_items")
            conn.execute("DROP TABLE IF EXISTS jumbo_lists")
            conn.execute("CREATE TABLE jumbo_lists (id TEXT PRIMARY KEY, name TEXT NOT NULL, synced_at TEXT NOT NULL)")
            conn.execute("CREATE TABLE jumbo_list_items (list_id TEXT NOT NULL, position INTEGER NOT NULL, item_id TEXT, sku TEXT, "
                         "term TEXT NOT NULL, quantity INTEGER NOT NULL DEFAULT 1, default_quantity INTEGER NOT NULL DEFAULT 1, "
                         "title TEXT, unit_size TEXT, price REAL, image TEXT, url TEXT, is_bonus INTEGER NOT NULL DEFAULT 0, "
                         "PRIMARY KEY (list_id, position))")
        snapshot = jumbo_lists.save_snapshot(self.live(1))
        self.assertEqual(snapshot["items"][0]["product"]["brand"], "Jumbo")

    def test_merged_lists_order_and_kinds(self):
        jumbo_lists.save_snapshot({"id": "oud", "name": "Verdwenen lijst", "items": []})
        with mock.patch.object(jumbo, "get_shopping_list", return_value={"active": True, "items": [{"term": "a", "completed": False}]}), \
                mock.patch.object(jumbo, "get_product_lists", return_value=[
                    {"id": FAV, "name": "Favorieten", "kind": "favorites", "itemCount": 16, "description": None},
                    {"id": OWN, "name": "Basis Lijstje", "kind": "own", "itemCount": 24, "description": None}]):
            result = jumbo_lists.merged_lists()
        self.assertEqual([(i["name"], i["kind"], i["source"]) for i in result["lists"]],
                         [("Boodschappenlijstje", "shopping", "jumbo"), ("Basis Lijstje", "own", "jumbo"),
                          ("Favorieten", "favorites", "jumbo"), ("Verdwenen lijst", "own", "local")])
        self.assertIsNone(result["storeError"])

    def test_one_failing_source_does_not_hide_the_other(self):
        with mock.patch.object(jumbo, "get_shopping_list", return_value={"active": True, "items": []}), \
                mock.patch.object(jumbo, "get_product_lists", side_effect=jumbo.JumboPersistedQueryError("PersistedQueryNotFound")):
            result = jumbo_lists.merged_lists()
        self.assertEqual([i["kind"] for i in result["lists"]], ["shopping"])
        self.assertEqual(result["storeError"], "Jumbo fout")
        jumbo_lists.save_snapshot(self.live(1))
        with mock.patch.object(jumbo, "get_shopping_list", side_effect=RuntimeError("x")), \
                mock.patch.object(jumbo, "get_product_lists", return_value=[{"id": OWN, "name": "Basis Lijstje", "kind": "own", "itemCount": 24, "description": None}]):
            result = jumbo_lists.merged_lists()
        self.assertEqual([(i["kind"], i["synced"], i["syncedCount"]) for i in result["lists"]], [("own", True, 1)])

    def test_search_finds_products_in_every_synced_list(self):
        jumbo_lists.save_snapshot(self.live(1, 1))
        self.assertEqual(jumbo_lists.search_items("mango blokjes 2"), {OWN: ["Jumbo Mango Blokjes 2"]})


class RoutesAndToolsTest(TempDataDirTest):
    def setUp(self):
        super().setUp()
        self.client = create_app().test_client()

    def test_a_list_opens_live_with_its_own_id_and_jumbo_s_quantities(self):
        live = {"id": OWN, "name": "Basis Lijstje", "items": [jumbo._product_list_item(product_item(1, amount=3))]}
        with mock.patch.object(jumbo_lists, "fetch_live", return_value=live) as fetch:
            body = self.client.get(f"/api/jumbo/lists/{OWN}").get_json()
        fetch.assert_called_once_with(OWN)
        self.assertEqual((body["source"], body["items"][0]["quantity"], body["items"][0]["product"]["brand"]), ("jumbo", 3, "Jumbo"))

    def test_sync_cart_and_a_bad_id(self):
        live = {"id": OWN, "name": "Basis Lijstje", "items": [jumbo._product_list_item(product_item(n, amount=n)) for n in (1, 2)]}
        with mock.patch.object(jumbo_lists, "fetch_live", return_value=live):
            self.assertEqual(self.client.post(f"/api/jumbo/lists/{OWN}/sync").get_json()["totalSize"], 2)
        result = {"added": [], "failed": [], "cart": None}
        with mock.patch.object(jumbo, "search_product", side_effect=AssertionError("producten hebben al een SKU")), \
                mock.patch.object(jumbo, "add_to_cart", return_value=result) as add:
            body = self.client.post(f"/api/jumbo/lists/{OWN}/cart").get_json()
        self.assertEqual([(m["productId"], m["quantity"]) for m in add.call_args.args[0]], [("000001ZK", 1), ("000002ZK", 2)])
        self.assertEqual((body["added"], body["units"], body["name"]), (2, 3, "Basis Lijstje"))
        self.assertEqual(self.client.get("/api/jumbo/lists/bad%20id").status_code, 400)

    def test_the_overview_route_has_all_kinds(self):
        with mock.patch.object(jumbo, "get_shopping_list", return_value={"active": True, "items": []}), \
                mock.patch.object(jumbo, "get_product_lists", return_value=[{"id": OWN, "name": "Basis Lijstje", "kind": "own", "itemCount": 24, "description": None}]):
            body = self.client.get("/api/jumbo/lists").get_json()
        self.assertEqual([i["kind"] for i in body["lists"]], ["shopping", "own"])

    def overview(self):
        return mock.patch.object(jumbo_lists, "merged_lists", return_value={"storeError": None, "lists": [
            {"id": "JUMBO", "name": "Boodschappenlijstje", "kind": "shopping", "itemCount": 2, "synced": False, "syncedAt": None},
            {"id": OWN, "name": "Basis Lijstje", "kind": "own", "itemCount": 24, "synced": True, "syncedAt": "x"},
            {"id": FAV, "name": "Favorieten", "kind": "favorites", "itemCount": 16, "synced": False, "syncedAt": None}]})

    def test_mcp_lists_and_resolves_lists_by_name(self):
        with self.overview():
            result = mcp.tool_list_jumbo_lists({})
            self.assertEqual([(i["name"], i["kind"]) for i in result["lists"]],
                             [("Boodschappenlijstje", "shopping"), ("Basis Lijstje", "own"), ("Favorieten", "favorites")])
            self.assertEqual(mcp._resolve_jumbo_list(None), "JUMBO")
            self.assertEqual(mcp._resolve_jumbo_list("favo"), FAV)
            self.assertEqual(mcp._resolve_jumbo_list(OWN.upper()), OWN)
            with self.assertRaises(mcp.ToolError):
                mcp._resolve_jumbo_list("bestaat niet")
            with mock.patch.object(jumbo_lists, "merged_lists", return_value={"lists": [
                    {"id": "a", "name": "Basis 1"}, {"id": "b", "name": "Basis 2"}], "storeError": None}):
                with self.assertRaisesRegex(mcp.ToolError, "Welk lijstje"):
                    mcp._resolve_jumbo_list("basis")

    def test_mcp_reads_the_chosen_list(self):
        jumbo_lists.save_snapshot({"id": OWN, "name": "Basis Lijstje", "items": [jumbo._product_list_item(product_item(1, amount=2))]})
        with self.overview(), mock.patch.object(jumbo_lists, "fetch_live", side_effect=AssertionError("lokale kopie gebruiken")):
            result = mcp.tool_jumbo_list({"list": "basis"})
        self.assertEqual((result["name"], result["source"], result["total"]), ("Basis Lijstje", "lokale kopie", 1))
        self.assertEqual((result["items"][0]["quantity"], result["items"][0]["brand"]), (2, "Jumbo"))


class ExploreTest(TempDataDirTest):
    def test_the_report_names_the_working_route_and_contains_no_personal_data(self):
        jumbo._persisted_route["GetCustomerProductLists"] = ("GET", "JUMBO_WEB-loyalty")
        jumbo._persisted_route["GetProductList"] = ("GET", "JUMBO_WEB-loyalty")
        blocked = mock.Mock(status_code=200, headers={"content-type": "application/json"})
        found = [{"id": OWN, "name": "Geheime lijst", "kind": "own", "itemCount": 24, "description": "Persoonlijk"},
                 {"id": FAV, "name": "Favorieten", "kind": "favorites", "itemCount": 16, "description": None}]
        live = {"id": OWN, "name": "Geheime lijst", "items": [jumbo._product_list_item(product_item(7))]}
        with mock.patch.object(jumbo_explore.requests, "request", return_value=blocked), \
                mock.patch.object(jumbo, "get_access_token", return_value="acc"), \
                mock.patch.object(jumbo, "search_products", return_value=[]), \
                mock.patch.object(jumbo, "get_cart", return_value={"items": []}), \
                mock.patch.object(jumbo, "get_shopping_list", return_value={"active": True, "items": []}), \
                mock.patch.object(jumbo, "get_product_lists", return_value=found), \
                mock.patch.object(jumbo, "get_product_list", return_value=live), \
                mock.patch.object(jumbo, "_graphql", return_value={"addBasketLines": {"__typename": "Basket"}, "editShoppingListItems": {"__typename": "EditShoppingListItemsResult"}}):
            report = jumbo_explore.explore()
        by_name = {probe["name"]: probe for probe in report["probes"]}
        overview = by_name["mijn lijstjes: opgeslagen query GetCustomerProductLists"]
        self.assertEqual((overview["info"]["eigen lijstjes"], overview["info"]["favorieten"]), (1, 1))
        self.assertEqual(overview["info"]["werkt via"], "GET met JUMBO_WEB-loyalty")
        detail = by_name["een lijstje: opgeslagen query GetProductList"]
        self.assertEqual(detail["info"]["producten"], 1)
        dumped = json.dumps(report)
        for private in ("Geheime lijst", "Persoonlijk", OWN, FAV, "000007ZK", "Jumbo Mango Blokjes"):
            self.assertNotIn(private, dumped)

    def test_a_stale_hash_is_visible_in_the_report(self):
        report, _ = jumbo_explore._probe("x", mock.Mock(side_effect=jumbo.JumboPersistedQueryError("PersistedQueryNotFound")))
        self.assertEqual((report["error"], report["schema"]), ("JumboPersistedQueryError", "PersistedQueryNotFound"))


if __name__ == "__main__":
    unittest.main()
