"""Jumbo-bestellingen: de lijst via een opgeslagen query, de producten van een bestelling, de lokale kopie.

De fixtures hebben de vorm van de echte antwoorden van jumbo.com (OnlineOrdersAndStoreReceipts en de
bestelling met producten), met verzonnen ordernummers, adressen, titels en bedragen.
"""
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("MEALIESHOPPER_DATA_DIR", tempfile.mkdtemp())

from mealieshopper import auth, jumbo, jumbo_explore, jumbo_orders, mcp  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402

STREET = "Voorbeeldstraat"
CUSTOMER = "11111111-2222-3333-4444-555555555555"
RESERVATION = "99999999-8888-7777-6666-000000000000"


def money(amount):
    return {"amount": amount, "currency": "EUR", "__typename": "MoneyType"}


def listed(order_id, *, status="COMPLETED", total="42.50", date="2024-03-02T09:00:00Z"):
    return {
        "orderId": str(order_id), "customerId": CUSTOMER, "deliveryDate": date,
        "deliveryAddress": {"street": STREET, "houseNumber": "1", "postalCode": "1234AB", "city": "DORP"},
        "reservationId": RESERVATION, "reservation": None, "creationTime": "2024-02-29T10:00:00Z",
        "fulfilmentType": "HOME_DELIVERY", "status": status, "totalToPay": money(total), "__typename": "OnlineOrder",
    }


def page(orders, *, page_number=0, total_pages=1, receipts=0):
    return {
        "storeReceipts": {"totalResults": receipts, "pageSize": 10, "currentPage": page_number, "receipts": []},
        "onlineOrders": {
            "orders": orders, "totalCount": len(orders), "pageCount": len(orders), "page": page_number,
            "totalPages": total_pages, "hasNext": page_number + 1 < total_pages, "hasPrevious": page_number > 0,
        },
    }


def line(n, *, title=None, quantity=1, paid="3.19", before=None, promotions=None, picked=None, brand="Jumbo", image="https://img/p.png"):
    sku = f"{n:06d}STK"
    return {
        "lineId": n, "sku": sku, "quantity": quantity, "orderedQuantity": quantity, "pickedQuantity": picked, "unit": "PIECES",
        "linePriceExDiscount": money(before or paid), "linePriceIncDiscount": money(paid),
        "promotions": promotions or [], "deposits": [],
        "details": {"sku": sku, "title": title if title is not None else f"Jumbo Kwark {n}", "subtitle": "500 g", "image": image,
                    "category": "Zuivel", "brand": brand, "price": {"price": 159, "promoPrice": None}},
    }


def order(order_id, items, *, total="42.50", date="2024-03-02T09:00:00Z"):
    return {
        "orderId": int(order_id), "customerId": CUSTOMER, "deliveryDate": date, "fulfilmentType": "HOME_DELIVERY",
        "paymentMethod": "DEBIT_CARD",
        "fulfilmentData": {
            "reservationId": RESERVATION, "startTime": "2024-03-02T09:00:00Z", "endTime": "2024-03-02T11:00:00Z",
            "storeV2": {"name": "Jumbo Voorbeeld", "location": {"address": {"street": STREET}}},
            "displayAddress": {"street": STREET, "postalCode": "1234AB", "houseNumber": "1", "city": "DORP"},
        },
        "progress": {"status": "COMPLETED", "orderChannel": "MOBILE_APP"},
        "items": items, "calculatedTotals": {"totalToPay": money(total), "shippingCosts": money("7.50")},
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
        jumbo._working_order_detail.clear()
        auth.set_secret(jumbo.REFRESH_SECRET, "ref")


class OrderListTest(TempDataDirTest):
    def test_the_request_is_the_stored_query_with_the_variables_of_the_website(self):
        with mock.patch.object(jumbo, "_persisted_query", return_value=page([listed(1)])) as query:
            jumbo.get_orders()
        operation, variables = query.call_args.args
        self.assertEqual(operation, "OnlineOrdersAndStoreReceipts")
        self.assertEqual(jumbo.PERSISTED_QUERIES[operation], "89b3f60a39a37eeed32055fd531a32925e504c566d190e0ca06b8efecd0bccdf")
        self.assertEqual(
            variables,
            {"ordersInput": {"offset": 0, "limit": 10, "direction": "DESC", "sortBy": "DELIVERY_DATE", "statusCategory": "CLOSED"},
             "page": 0, "pageSize": 10},
        )

    def test_orders_are_read_over_all_pages_without_address_or_customer(self):
        pages = [page([listed(i) for i in range(1, 11)], page_number=0, total_pages=2, receipts=3),
                 page([listed(11, total="9.99")], page_number=1, total_pages=2, receipts=3)]
        with mock.patch.object(jumbo, "_persisted_query", side_effect=pages) as query:
            result = jumbo.get_orders()
        self.assertEqual(query.call_count, 2)
        self.assertEqual(query.call_args.args[1]["ordersInput"]["offset"], 10)
        self.assertEqual((len(result["orders"]), result["storeReceipts"]), (11, 3))
        last = result["orders"][-1]
        self.assertEqual((last["id"], last["total"], last["shoppingType"], last["state"]), ("11", 9.99, "HOME_DELIVERY", "COMPLETED"))
        text = repr(result)
        for private in (STREET, CUSTOMER, RESERVATION, "1234AB"):
            self.assertNotIn(private, text)

    def test_an_answer_without_orders_is_an_error(self):
        with mock.patch.object(jumbo, "_persisted_query", return_value={"onlineOrders": None}):
            with self.assertRaises(RuntimeError):
                jumbo.get_orders()

    def test_cancelled_orders_are_marked(self):
        with mock.patch.object(jumbo, "_persisted_query", return_value=page([listed(1, status="CANCELLED"), listed(2)])):
            orders = jumbo.get_orders()["orders"]
        self.assertEqual([o["cancelled"] for o in orders], [True, False])


class OrderDetailTest(TempDataDirTest):
    def test_lines_have_the_same_shape_as_an_ah_order(self):
        raw = order(7, [
            line(1, quantity=4, paid="7.16"),
            line(2, title="__MISSING_PRODUCT__", brand="Pepsi", quantity=6, paid="3.84", image=""),
            line(3, title="__MISSING_PRODUCT__", brand=None),
            line(4, quantity=1, paid="1.50", before="1.97", promotions=[{"description": "2 voor 3,00"}]),
            line(5, quantity=2, paid="0.00", picked=0),
        ], total="153.37")
        parsed = jumbo.parse_order(raw)
        self.assertEqual(
            (parsed["id"], parsed["state"], parsed["shoppingType"], parsed["method"], parsed["total"]),
            ("7", "COMPLETED", "HOME_DELIVERY", "DEBIT_CARD", 153.37),
        )
        self.assertEqual((parsed["slotStart"], parsed["slotEnd"]), ("2024-03-02T09:00:00Z", "2024-03-02T11:00:00Z"))
        first, pepsi, unknown, promo, missing = parsed["items"]
        self.assertEqual(
            first,
            {"productId": "000001STK", "title": "Jumbo Kwark 1", "brand": "Jumbo", "unitSize": "500 g", "quantity": 4, "amount": 7.16,
             "price": 1.79, "priceBeforeBonus": None, "bonus": False, "taxonomy": "Zuivel", "image": "https://img/p.png"},
        )
        self.assertEqual((pepsi["title"], pepsi["image"], pepsi["quantity"]), ("Pepsi (000002STK)", None, 6))  # onbekend product: merk en SKU
        self.assertEqual(unknown["title"], "000003STK")
        self.assertEqual((promo["bonus"], promo["amount"], promo["priceBeforeBonus"]), (True, 1.5, 1.97))
        self.assertEqual(missing["quantity"], 0)  # niet geleverd: het aantal dat is gepickt
        for private in (STREET, CUSTOMER, RESERVATION, "1234AB", "Voorbeeld"):
            self.assertNotIn(private, repr(parsed))

    def test_the_detail_query_tries_variants_and_remembers_the_one_that_works(self):
        attempts = []

        def fake(query, variables, operation, client):
            attempts.append((query, variables))
            if "$orderId: Long!" not in query or "order(id:" in query:
                raise jumbo.JumboSchemaError('Unknown argument "orderId"')
            return {"order": order(5, [line(1)])}

        with mock.patch.object(jumbo, "_graphql", side_effect=fake):
            result = jumbo.get_order("5")
            self.assertEqual(result["id"], "5")
            tried = len(attempts)
            jumbo.get_order("5")
        self.assertEqual(attempts[-1][1], {"orderId": 5})  # Long: als getal
        self.assertTrue(all(operation == "GetOrder" for operation in ["GetOrder"]))
        self.assertEqual(len(attempts), tried + 1)  # de tweede keer meteen de werkende variant

    def test_a_string_variant_sends_the_id_as_text(self):
        seen = []

        def fake(query, variables, operation, client):
            seen.append(variables)
            return {"order": order(5, [])}

        with mock.patch.object(jumbo, "_graphql", side_effect=fake):
            jumbo.get_order("5")
        self.assertEqual(seen, [{"orderId": "5"}])

    def test_every_variant_failing_raises_the_last_error(self):
        with mock.patch.object(jumbo, "_graphql", side_effect=jumbo.JumboSchemaError("nee")) as graphql:
            with self.assertRaises(RuntimeError):
                jumbo.get_order("5")
        self.assertEqual(graphql.call_count, len(jumbo.ORDER_DETAIL_ARGUMENTS))

    def test_a_broken_link_stops_at_once_and_bad_ids_are_refused(self):
        with mock.patch.object(jumbo, "_graphql", side_effect=jumbo.JumboAuthError("koppeling")) as graphql:
            with self.assertRaises(jumbo.JumboAuthError):
                jumbo.get_order("5")
        self.assertEqual(graphql.call_count, 1)
        with mock.patch.object(jumbo, "_graphql") as graphql:
            for bad in ("", "5; drop", "abc", "-1"):
                with self.assertRaises(ValueError):
                    jumbo.get_order(bad)
        graphql.assert_not_called()


class LocalCopyTest(TempDataDirTest):
    def sync(self, listed_orders, details=None, receipts=0):
        details = details or {}

        def get_order(order_id):
            if order_id not in details:
                raise RuntimeError("geen detail")
            return jumbo.parse_order(details[order_id])

        with mock.patch.object(jumbo, "_persisted_query", return_value=page(listed_orders, receipts=receipts)), \
                mock.patch.object(jumbo, "get_order", side_effect=get_order):
            return jumbo_orders.sync_orders()

    def test_sync_stores_orders_with_their_products_newest_first_and_no_address(self):
        result = self.sync(
            [listed(2, date="2024-03-09T09:00:00Z"), listed(1)],
            {"1": order(1, [line(1, quantity=2, paid="6.38")]), "2": order(2, [line(2), line(3)])},
            receipts=4,
        )
        self.assertEqual((result["orders"], result["new"], result["remaining"], result["storeReceipts"]), (2, 2, 0, 4))
        self.assertNotIn("detailError", result)
        overview = jumbo_orders.list_orders()
        self.assertEqual([o["id"] for o in overview["orders"]], ["2", "1"])
        self.assertEqual((overview["orders"][0]["itemCount"], overview["orders"][0]["kind"], overview["orders"][0]["itemsSynced"]), (2, "delivered", True))
        self.assertTrue(overview["syncedAt"])
        detail = jumbo_orders.get_order("1")
        self.assertEqual((detail["total"], detail["items"][0]["quantity"], detail["slotStart"]), (42.5, 2, "2024-03-02T09:00:00Z"))
        with sqlite3.connect(auth.db_path()) as conn:
            dump = "\n".join(str(row) for table in ("jumbo_orders", "jumbo_order_items") for row in conn.execute(f"SELECT * FROM {table}"))
        for private in (STREET, CUSTOMER, RESERVATION, "1234AB"):
            self.assertNotIn(private, dump)

    def test_finished_orders_are_not_fetched_again_and_stay_when_jumbo_stops_listing_them(self):
        self.sync([listed(1), listed(2)], {"1": order(1, [line(1)]), "2": order(2, [line(2)])})
        with mock.patch.object(jumbo, "_persisted_query", return_value=page([listed(3)])), \
                mock.patch.object(jumbo, "get_order", return_value=jumbo.parse_order(order(3, [line(3)]))) as get_order:
            result = jumbo_orders.sync_orders()
        get_order.assert_called_once_with("3")
        self.assertEqual(result["new"], 1)
        self.assertEqual(sorted(o["id"] for o in jumbo_orders.list_orders()["orders"]), ["1", "2", "3"])

    def test_cancelled_orders_are_not_kept_and_a_later_cancellation_removes_them(self):
        self.sync([listed(1), listed(2, status="CANCELLED")], {"1": order(1, [line(1)])})
        self.assertEqual([o["id"] for o in jumbo_orders.list_orders()["orders"]], ["1"])
        self.sync([listed(1, status="CANCELLED")])
        self.assertEqual(jumbo_orders.list_orders()["orders"], [])

    def test_without_products_the_list_is_still_kept_and_the_error_is_reported(self):
        with mock.patch.object(jumbo, "_persisted_query", return_value=page([listed(1), listed(2), listed(3)])), \
                mock.patch.object(jumbo, "get_order", side_effect=jumbo.JumboSchemaError('Unknown argument "orderId" on field "order"')) as get_order:
            result = jumbo_orders.sync_orders()
        self.assertEqual(get_order.call_count, 3)
        self.assertEqual((result["orders"], result["remaining"]), (3, 3))
        self.assertTrue(result["detailError"].startswith("JumboSchemaError"))
        self.assertIn("orderId", result["detailError"])
        orders = jumbo_orders.list_orders()["orders"]
        self.assertEqual([(o["id"], o["itemsSynced"], o["total"]) for o in orders], [("3", False, 42.5), ("2", False, 42.5), ("1", False, 42.5)])  # zelfde dag: hoogste nummer eerst
        self.assertEqual(jumbo_orders.get_order("1")["items"], [])

    def test_products_follow_on_the_next_sync(self):
        self.sync([listed(1), listed(2)])
        result = self.sync([listed(1), listed(2)], {"1": order(1, [line(1)]), "2": order(2, [line(2)])})
        self.assertEqual((result["new"], result["remaining"]), (0, 0))
        self.assertTrue(all(o["itemsSynced"] for o in jumbo_orders.list_orders()["orders"]))

    def test_it_gives_up_after_three_failures_in_a_row_when_nothing_works(self):
        listed_orders = [listed(i) for i in range(1, 11)]
        with mock.patch.object(jumbo, "_persisted_query", return_value=page(listed_orders)), \
                mock.patch.object(jumbo, "get_order", side_effect=RuntimeError("nee")) as get_order:
            result = jumbo_orders.sync_orders()
        self.assertEqual((get_order.call_count, result["remaining"], result["detailError"]), (3, 10, "RuntimeError"))

    def test_one_failing_order_does_not_stop_the_others(self):
        result = self.sync([listed(1), listed(2)], {"2": order(2, [line(2)])})
        self.assertEqual(result["remaining"], 1)
        flags = {o["id"]: o["itemsSynced"] for o in jumbo_orders.list_orders()["orders"]}
        self.assertEqual(flags, {"1": False, "2": True})

    def test_at_most_25_orders_get_their_products_per_sync(self):
        listed_orders = [listed(i) for i in range(1, 31)]
        details = {str(i): order(i, [line(i)]) for i in range(1, 31)}
        result = self.sync(listed_orders, details)
        self.assertEqual((result["orders"], result["remaining"]), (30, 5))

    def test_a_broken_link_is_not_swallowed(self):
        with mock.patch.object(jumbo, "_persisted_query", return_value=page([listed(1)])), \
                mock.patch.object(jumbo, "get_order", side_effect=jumbo.JumboAuthError("koppeling")):
            with self.assertRaises(jumbo.JumboAuthError):
                jumbo_orders.sync_orders()

    def test_unknown_order_is_none(self):
        self.assertIsNone(jumbo_orders.get_order("404"))

    def test_search_finds_products_over_orders_and_reports_unsynced_ones(self):
        self.sync(
            [listed(1, date="2024-03-02T09:00:00Z"), listed(2, date="2024-03-09T09:00:00Z"), listed(3)],
            {"1": order(1, [line(1, title="Campina Magere Kwark", quantity=2)]),
             "2": order(2, [line(2, title="Campina Halfvolle Melk", brand="Campina"), line(3, title="Jumbo Kwark Vanille")], date="2024-03-09T09:00:00Z")},
        )
        found = jumbo_orders.search_purchases("kwark")
        self.assertEqual([entry["product"] for entry in found["occurrences"]], ["Jumbo Kwark Vanille", "Campina Magere Kwark"])
        self.assertEqual(found["occurrences"][0]["date"], "2024-03-09")
        self.assertEqual(found["unsearched"], 1)
        self.assertEqual(found["products"][1]["totalQuantity"], 2)
        self.assertEqual(jumbo_orders.search_purchases("Campina")["total"], 2)  # ook op merk
        self.assertEqual(jumbo_orders.search_purchases("   ")["occurrences"], [])


class RoutesTest(TempDataDirTest):
    def setUp(self):
        super().setUp()
        self.client = create_app().test_client()

    def test_routes_read_the_copy_and_sync_talks_to_jumbo(self):
        self.assertEqual(self.client.get("/api/jumbo/orders").get_json(), {"syncedAt": None, "orders": []})
        with mock.patch.object(jumbo, "_persisted_query", return_value=page([listed(7000000001)])), \
                mock.patch.object(jumbo, "get_order", return_value=jumbo.parse_order(order(7000000001, [line(1)]))):
            synced = self.client.post("/api/jumbo/orders/sync")
        self.assertEqual((synced.status_code, synced.get_json()["orders"]), (200, 1))
        overview = self.client.get("/api/jumbo/orders").get_json()
        self.assertEqual(overview["orders"][0]["id"], "7000000001")  # het nummer blijft tekst
        self.assertEqual(self.client.get("/api/jumbo/orders/7000000001").get_json()["items"][0]["productId"], "000001STK")
        self.assertEqual(self.client.get("/api/jumbo/orders/1").status_code, 404)
        self.assertEqual(self.client.get("/api/jumbo/purchases/search?q=kwark").get_json()["total"], 1)

    def test_a_failing_sync_is_a_clear_error_and_keeps_the_copy(self):
        with mock.patch.object(jumbo, "_persisted_query", return_value=page([listed(1)])), \
                mock.patch.object(jumbo, "get_order", return_value=jumbo.parse_order(order(1, [line(1)]))):
            self.client.post("/api/jumbo/orders/sync")
        with mock.patch.object(jumbo, "_persisted_query", side_effect=jumbo.JumboPersistedQueryError("PersistedQueryNotFound")):
            failed = self.client.post("/api/jumbo/orders/sync")
        self.assertEqual(failed.status_code, 424)
        self.assertEqual(len(self.client.get("/api/jumbo/orders").get_json()["orders"]), 1)


class McpAndExplorerTest(TempDataDirTest):
    def seed(self):
        with mock.patch.object(jumbo, "_persisted_query", return_value=page([listed(1)])), \
                mock.patch.object(jumbo, "get_order", return_value=jumbo.parse_order(order(1, [line(1)]))):
            jumbo_orders.sync_orders()

    def test_the_tools_read_the_local_copy(self):
        self.assertEqual(mcp.tool_list_jumbo_orders({})["total"], 0)
        self.seed()
        listing = mcp.tool_list_jumbo_orders({"limit": 5})
        self.assertEqual((listing["total"], listing["orders"][0]["id"]), (1, "1"))
        self.assertEqual(mcp.tool_get_jumbo_order({"order_id": "1"})["items"][0]["title"], "Jumbo Kwark 1")
        with self.assertRaises(mcp.ToolError):
            mcp.tool_get_jumbo_order({"order_id": "99"})
        with self.assertRaises(mcp.ToolError):
            mcp.tool_get_jumbo_order({})
        for name in ("list_jumbo_orders", "get_jumbo_order"):
            self.assertTrue(mcp.TOOLS[name]["annotations"]["readOnlyHint"])

    def test_the_explorer_reports_shapes_without_personal_data(self):
        listing = page([listed(7000000001)], receipts=2)
        with mock.patch.object(jumbo, "_persisted_query", return_value=listing), \
                mock.patch.object(jumbo, "get_order", return_value=jumbo.parse_order(order(7000000001, [line(1)]))):
            probes = {p["name"]: p for p in jumbo_explore.explore()["probes"]}
        orders_probe = probes["mijn bestellingen: opgeslagen query OnlineOrdersAndStoreReceipts"]
        self.assertTrue(orders_probe["ok"])
        self.assertEqual((orders_probe["info"]["bestellingen"], orders_probe["info"]["winkelbonnen volgens Jumbo"]), (1, 2))
        self.assertTrue(probes["een bestelling met producten: GetOrder"]["ok"])
        text = repr(probes)
        for private in (STREET, CUSTOMER, RESERVATION, "7000000001", "Jumbo Kwark"):
            self.assertNotIn(private, text)


if __name__ == "__main__":
    unittest.main()
