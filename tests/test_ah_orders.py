import json
import os
import tempfile
import unittest
from unittest import mock

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

from mealieshopper import ah, ah_explore, ah_orders, auth_password  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402

ACTIVE = {
    "id": 4242, "state": "OPEN", "shoppingType": "DELIVERY",
    "totalPrice": {"priceBeforeDiscount": 30.0, "priceAfterDiscount": 25.5, "priceDiscount": 4.5, "priceTotalPayable": 25.5},
    "deliveryInformation": {"deliveryDate": "2026-10-23", "deliveryStartTime": "18:00", "deliveryEndTime": "20:00",
                            "address": {"street": "Geheimstraat", "houseNumber": "1", "zipCode": "1234AB", "city": "Plaats"}},
    "orderedProducts": [
        {"amount": 2.58, "quantity": 2, "product": {"webshopId": 111, "title": "Halfvolle melk", "brand": "AH", "images": [{"url": "https://img/111.png"}]}},
        {"amount": 1.99, "quantity": 1, "product": {"webshopId": 222, "title": "Pindakaas", "brand": "Calvé"}},
    ],
}
DETAILS = {
    "orderId": 4242, "orderState": "DELIVERED", "deliveryDate": "2026-10-23", "deliveryType": "DELIVERY",
    "deliveryTimePeriod": {"startDateTime": "2026-10-23T18:00:00", "endDateTime": "2026-10-23T20:00:00"},
    "groupedProductsInTaxonomy": [
        {"taxonomyName": "Zuivel", "orderedProducts": [{"amount": 2.58, "quantity": 2, "product": {"webshopId": 111, "title": "Halfvolle melk", "brand": "AH", "salesUnitSize": "1 l", "priceBeforeBonus": 1.45, "currentPrice": 1.29, "isBonus": True}}]},
        {"taxonomyName": "Broodbeleg", "orderedProducts": [{"amount": 1.99, "quantity": 1, "product": {"webshopId": 222, "title": "Pindakaas", "brand": "Calvé", "salesUnitSize": "350 g", "priceBeforeBonus": 1.99, "currentPrice": None, "isBonus": False}}]},
    ],
}
FULFILLMENTS = {"orderFulfillments": {"result": [{
    "orderId": 4242, "statusCode": "SCHEDULED", "statusDescription": "Ingepland", "shoppingType": "DELIVERY", "transactionCompleted": True, "modifiable": True,
    "totalPrice": {"totalPrice": {"amount": 25.5}},
    "delivery": {"status": "SCHEDULED", "method": "HOME", "slot": {"date": "2026-10-23", "dateDisplay": "vrijdag", "timeDisplay": "18-20", "startTime": "18:00", "endTime": "20:00"}},
}]}}
RECEIPTS = {"posReceiptsPage": {"posReceipts": [
    {"id": "R2", "dateTime": "2026-10-18T10:15:00", "totalAmount": {"amount": 12.34}},
    {"id": "R1", "dateTime": "2026-10-10T09:00:00", "totalAmount": {"amount": 5.0}},
]}}
RECEIPT = {"posReceiptDetails": {"id": "R2",
    "products": [{"id": 777, "quantity": 2, "name": "AH Halfvolle melk", "price": {"amount": 1.29}, "amount": {"amount": 2.58}}],
    "discounts": [{"name": "Bonus", "amount": {"amount": -0.5}}], "payments": [{"method": "PIN", "amount": {"amount": 12.34}}]}}


class AhOrdersTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(os.environ, {"MEALIESHOPPER_DATA_DIR": tmp.name})
        env.start()
        self.addCleanup(env.stop)
        self.client = create_app().test_client()


class ParsingTest(unittest.TestCase):
    def test_active_order(self):
        with mock.patch.object(ah, "_get_json", return_value=ACTIVE) as get:
            order = ah.get_active_order()
        get.assert_called_once_with("/mobile-services/order/v1/summaries/active", {"sortBy": "DEFAULT"})
        self.assertEqual((order["id"], order["state"], order["total"], order["discount"]), (4242, "OPEN", 25.5, 4.5))
        self.assertEqual((order["deliveryDate"], order["slotStart"], order["slotEnd"]), ("2026-10-23", "18:00", "20:00"))
        self.assertEqual([(i["productId"], i["title"], i["quantity"], i["amount"]) for i in order["items"]], [(111, "Halfvolle melk", 2, 2.58), (222, "Pindakaas", 1, 1.99)])
        self.assertEqual(order["items"][0]["image"], "https://img/111.png")
        self.assertNotIn("Geheimstraat", json.dumps(order))  # het adres nemen we niet over

    def test_no_active_order_means_an_empty_basket(self):
        body = '404: {"status": 404, "message": "Order does not exist: Order should not be null"}'
        with mock.patch.object(ah, "_get_json", side_effect=RuntimeError(body)):
            order = ah.get_active_order()
        self.assertEqual((order["id"], order["items"]), (None, []))
        with mock.patch.object(ah, "_get_json", side_effect=RuntimeError("500: kapot")), self.assertRaises(RuntimeError):
            ah.get_active_order()
        with mock.patch.object(ah, "_get_json", side_effect=RuntimeError("404: Not Found")), self.assertRaises(RuntimeError):
            ah.get_active_order()

    def test_missing_fields_become_empty_not_errors(self):
        with mock.patch.object(ah, "_get_json", return_value={}):
            order = ah.get_active_order()
        self.assertEqual((order["id"], order["items"], order["total"]), (None, [], None))
        with mock.patch.object(ah, "_get_json", return_value={"orderedProducts": [{"quantity": "x", "product": None}, "kapot"]}):
            self.assertEqual(len(ah.get_active_order()["items"]), 1)

    def test_order_details_are_flattened_with_their_category(self):
        with mock.patch.object(ah, "_get_json", return_value=DETAILS) as get:
            order = ah.get_order_details(4242)
        get.assert_called_once_with("/mobile-services/order/v1/4242/details-grouped-by-taxonomy")
        self.assertEqual([(i["title"], i["taxonomy"], i["unitSize"], i["bonus"]) for i in order["items"]], [("Halfvolle melk", "Zuivel", "1 l", True), ("Pindakaas", "Broodbeleg", "350 g", False)])
        self.assertEqual(order["items"][0]["price"], 1.29)
        self.assertEqual(order["items"][1]["price"], 1.99)  # geen bonusprijs: de gewone prijs
        self.assertEqual((order["state"], order["slotStart"]), ("DELIVERED", "2026-10-23T18:00:00"))

    def test_open_fulfillments(self):
        with mock.patch.object(ah, "get_user_token", return_value="t"), mock.patch.object(ah, "_graphql", return_value=FULFILLMENTS) as gql:
            orders = ah.get_open_fulfillments()
        self.assertIn("orderFulfillments(status: OPEN)", gql.call_args.args[1])
        self.assertNotIn("address", gql.call_args.args[1])  # we vragen het adres niet eens op
        self.assertEqual([(o["id"], o["state"], o["total"], o["deliveryDate"], o["modifiable"]) for o in orders], [(4242, "SCHEDULED", 25.5, "2026-10-23", True)])

    def test_receipts(self):
        with mock.patch.object(ah, "get_user_token", return_value="t"), mock.patch.object(ah, "_graphql", side_effect=[RECEIPTS, RECEIPT]):
            listed = ah.get_receipts(0, 100)
            detail = ah.get_receipt("R2")
        self.assertEqual([(r["id"], r["total"]) for r in listed], [("R2", 12.34), ("R1", 5.0)])
        self.assertEqual((detail["items"][0]["name"], detail["items"][0]["amount"]), ("AH Halfvolle melk", 2.58))
        self.assertEqual((detail["discounts"][0]["amount"], detail["payments"][0]["method"]), (-0.5, "PIN"))


class BasketCacheTest(AhOrdersTestCase):
    def test_nothing_synced_yet(self):
        self.assertEqual(self.client.get("/api/ah/basket").get_json(), {"syncedAt": None, "items": [], "itemCount": 0, "units": 0})

    def test_sync_stores_a_snapshot_and_reading_never_asks_ah(self):
        with mock.patch.object(ah, "_get_json", return_value=ACTIVE):
            data = self.client.post("/api/ah/basket/sync").get_json()
        self.assertEqual((data["itemCount"], data["units"], data["total"]), (2, 3, 25.5))
        with mock.patch.object(ah, "_get_json", side_effect=AssertionError("AH mag niet worden gebruikt")):
            cached = self.client.get("/api/ah/basket").get_json()
        self.assertEqual((cached["itemCount"], cached["deliveryDate"]), (2, "2026-10-23"))

    def test_the_next_sync_replaces_the_snapshot(self):
        with mock.patch.object(ah, "_get_json", return_value=ACTIVE):
            self.client.post("/api/ah/basket/sync")
        with mock.patch.object(ah, "_get_json", return_value={**ACTIVE, "orderedProducts": ACTIVE["orderedProducts"][:1]}):
            self.assertEqual(self.client.post("/api/ah/basket/sync").get_json()["itemCount"], 1)

    def test_a_failed_sync_keeps_the_copy(self):
        with mock.patch.object(ah, "_get_json", return_value=ACTIVE):
            self.client.post("/api/ah/basket/sync")
        with mock.patch.object(ah, "_get_json", side_effect=RuntimeError("AH account niet gekoppeld")):
            response = self.client.post("/api/ah/basket/sync")
        self.assertEqual((response.status_code, response.get_json()["error"]), (424, "AH account niet gekoppeld"))
        self.assertEqual(self.client.get("/api/ah/basket").get_json()["itemCount"], 2)


def fake_details(order_id):
    return {"id": order_id, "state": "DELIVERED", "deliveryType": "DELIVERY", "deliveryDate": "2026-10-23", "slotStart": "18:00", "slotEnd": "20:00",
            "items": [{"productId": 111, "title": "Halfvolle melk", "brand": "AH", "unitSize": "1 l", "quantity": 2, "amount": 2.58, "price": 1.29, "priceBeforeBonus": 1.45, "bonus": True, "taxonomy": "Zuivel", "image": ""}]}


def fulfillment(order_id, date="2026-10-23"):
    return {"id": order_id, "state": "SCHEDULED", "stateText": "Ingepland", "shoppingType": "DELIVERY", "method": "HOME", "modifiable": True, "total": 25.5, "deliveryDate": date, "slotStart": "18:00", "slotEnd": "20:00", "deliveryStatus": "SUBMITTED"}


def closed_order(order_id, status="DELIVERED", date="2026-10-01"):
    return {**fulfillment(order_id, date), "state": "9", "stateText": "Afgeleverd", "deliveryStatus": status}


class OrdersCacheTest(AhOrdersTestCase):
    def sync(self, open_orders, details=fake_details, closed=()):
        with mock.patch.object(ah, "get_open_fulfillments", return_value=open_orders), mock.patch.object(ah, "get_closed_fulfillments", return_value=list(closed)), mock.patch.object(ah, "get_order_details", side_effect=details):
            return self.client.post("/api/ah/orders/sync")

    def test_open_orders_are_stored_with_their_products(self):
        response = self.sync([fulfillment(1)])
        self.assertEqual(response.get_json(), {"open": 1, "archived": 0, "remaining": 0, "unfinished": 0})
        listed = self.client.get("/api/ah/orders").get_json()
        self.assertTrue(listed["syncedAt"])
        self.assertEqual([(o["id"], o["kind"], o["itemCount"], o["total"]) for o in listed["orders"]], [(1, "open", 1, 25.5)])
        order = self.client.get("/api/ah/orders/1").get_json()
        self.assertEqual((order["items"][0]["title"], order["items"][0]["taxonomy"], order["itemsSynced"]), ("Halfvolle melk", "Zuivel", True))

    def test_an_order_that_is_no_longer_open_is_archived_as_delivered_and_kept(self):
        self.sync([fulfillment(1), fulfillment(2)])
        response = self.sync([fulfillment(2)])
        self.assertEqual(response.get_json(), {"open": 1, "archived": 1, "remaining": 0, "unfinished": 0})
        kinds = {o["id"]: o["kind"] for o in self.client.get("/api/ah/orders").get_json()["orders"]}
        self.assertEqual(kinds, {1: "delivered", 2: "open"})
        # En hij blijft staan, ook als AH hem later nergens meer toont.
        self.sync([])
        self.assertEqual(len(self.client.get("/api/ah/orders").get_json()["orders"]), 2)

    def test_closed_orders_from_ah_are_kept_as_delivered_and_cancelled_ones_are_not(self):
        data = self.sync([], closed=[closed_order(5, "DELIVERED", "2026-10-02"), closed_order(6, "CANCELLED"), closed_order(7, "DELIVERED", "2026-09-20")]).get_json()
        self.assertEqual((data["archived"], data["remaining"]), (2, 0))
        listed = self.client.get("/api/ah/orders").get_json()["orders"]
        self.assertEqual([(o["id"], o["kind"], o["itemCount"]) for o in listed], [(7, "delivered", 1), (5, "delivered", 1)])
        # Een volgende keer worden bewaarde bestellingen niet opnieuw opgehaald.
        calls = []
        again = self.sync([], lambda order_id: calls.append(order_id) or fake_details(order_id), closed=[closed_order(5), closed_order(8)]).get_json()
        self.assertEqual((calls, again["archived"]), ([8], 1))

    def test_a_cancelled_order_is_forgotten_and_not_archived_as_delivered(self):
        self.sync([fulfillment(1)])
        data = self.sync([], closed=[closed_order(1, "CANCELLED")]).get_json()
        self.assertEqual((data["archived"], data["unfinished"]), (0, 0))
        self.assertEqual(self.client.get("/api/ah/orders").get_json()["orders"], [])

    def test_more_closed_orders_than_fit_in_one_sync_finish_next_time(self):
        closed = [closed_order(100 + number) for number in range(ah_orders.ORDER_DETAILS_PER_SYNC + 3)]
        first = self.sync([], closed=closed).get_json()
        self.assertEqual((first["archived"], first["remaining"]), (len(closed), 3))
        second = self.sync([], closed=closed).get_json()
        self.assertEqual(second["remaining"], 0)
        self.assertTrue(all(o["itemsSynced"] for o in self.client.get("/api/ah/orders").get_json()["orders"]))

    def test_failing_closed_query_does_not_stop_the_open_orders(self):
        with mock.patch.object(ah, "get_open_fulfillments", return_value=[fulfillment(1)]), mock.patch.object(ah, "get_closed_fulfillments", side_effect=RuntimeError("AH even weg")), mock.patch.object(ah, "get_order_details", side_effect=fake_details):
            data = self.client.post("/api/ah/orders/sync").get_json()
        self.assertEqual(data["open"], 1)

    def test_archiving_that_fails_is_retried_next_time(self):
        self.sync([fulfillment(1)])

        def flaky(order_id):
            raise RuntimeError("AH even weg")

        self.assertEqual(self.sync([], flaky).get_json(), {"open": 0, "archived": 0, "remaining": 0, "unfinished": 1})
        self.assertEqual(self.client.get("/api/ah/orders").get_json()["orders"][0]["kind"], "open")
        self.assertEqual(self.sync([]).get_json()["archived"], 1)

    def test_missing_products_do_not_wipe_what_we_had(self):
        self.sync([fulfillment(1)])

        def broken(order_id):
            raise RuntimeError("kapot")

        self.sync([fulfillment(1)], broken)
        order = self.client.get("/api/ah/orders/1").get_json()
        self.assertEqual(len(order["items"]), 1)
        self.assertTrue(order["itemsSynced"])

    def test_open_first_then_newest_delivered(self):
        self.sync([fulfillment(1, "2026-10-01"), fulfillment(2, "2026-10-09"), fulfillment(3, "2026-10-20")])
        self.sync([fulfillment(3, "2026-10-20")])
        self.assertEqual([o["id"] for o in self.client.get("/api/ah/orders").get_json()["orders"]], [3, 2, 1])

    def test_unknown_order(self):
        self.assertEqual(self.client.get("/api/ah/orders/999").status_code, 404)

    def test_the_address_is_never_stored(self):
        self.sync([fulfillment(1)])
        with mock.patch.object(ah, "_get_json", return_value=ACTIVE):
            self.client.post("/api/ah/basket/sync")
        from contextlib import closing

        from mealieshopper import auth

        with closing(auth.connect()) as conn:
            dump = "\n".join(conn.iterdump())
        for secret in ("Geheimstraat", "1234AB"):
            self.assertNotIn(secret, dump)

    def test_a_delivered_order_can_be_added_by_id(self):
        with mock.patch.object(ah, "get_order_details", side_effect=fake_details):
            order = ah_orders.add_order_by_id(77)
        self.assertEqual((order["id"], order["kind"], order["itemCount"]), (77, "delivered", 1))


class ReceiptsCacheTest(AhOrdersTestCase):
    def sync(self, pages, detail=lambda rid: {"id": rid, "items": [{"productId": 1, "name": "AH Halfvolle melk", "quantity": 2, "price": 1.29, "amount": 2.58}], "discounts": [], "payments": [{"method": "PIN", "amount": 2.58}]}):
        with mock.patch.object(ah, "get_receipts", side_effect=pages), mock.patch.object(ah, "get_receipt", side_effect=detail):
            return self.client.post("/api/ah/receipts/sync")

    def test_new_receipts_are_added_with_their_lines(self):
        data = self.sync([[{"id": "R2", "dateTime": "2026-10-18T10:00:00", "total": 12.34}, {"id": "R1", "dateTime": "2026-10-10T09:00:00", "total": 5.0}]]).get_json()
        self.assertEqual(data, {"new": 2, "total": 2, "remaining": 0, "failed": 0})
        listed = self.client.get("/api/ah/receipts").get_json()
        self.assertEqual([(r["id"], r["itemCount"], r["detailsSynced"]) for r in listed["receipts"]], [("R2", 1, True), ("R1", 1, True)])
        receipt = self.client.get("/api/ah/receipts/R2").get_json()
        self.assertEqual((receipt["items"][0]["name"], receipt["payments"][0]["method"]), ("AH Halfvolle melk", "PIN"))

    def test_only_new_receipts_are_fetched_next_time(self):
        self.sync([[{"id": "R1", "dateTime": "2026-10-10T09:00:00", "total": 5.0}]])
        calls = []

        def detail(rid):
            calls.append(rid)
            return {"id": rid, "items": [], "discounts": [], "payments": []}

        self.sync([[{"id": "R2", "dateTime": "2026-10-18T10:00:00", "total": 1.0}, {"id": "R1", "dateTime": "2026-10-10T09:00:00", "total": 5.0}]], detail)
        self.assertEqual(calls, ["R2"])

    def test_many_receipts_are_fetched_in_batches(self):
        page = [{"id": f"R{i:03d}", "dateTime": f"2026-01-{i % 28 + 1:02d}T10:00:00", "total": 1.0} for i in range(60)]
        with mock.patch.object(ah_orders, "RECEIPT_DETAILS_PER_SYNC", 25):
            first = self.sync([page]).get_json()
            self.assertEqual((first["new"], first["remaining"]), (60, 35))
            second = self.sync([page]).get_json()
            self.assertEqual((second["new"], second["remaining"]), (0, 10))

    def test_a_failing_receipt_is_retried_and_the_rest_still_arrives(self):
        def detail(rid):
            if rid == "R1":
                raise RuntimeError("503")
            return {"id": rid, "items": [], "discounts": [], "payments": []}

        data = self.sync([[{"id": "R1", "dateTime": "2026-10-10T09:00:00", "total": 1.0}, {"id": "R2", "dateTime": "2026-10-11T09:00:00", "total": 1.0}]], detail).get_json()
        self.assertEqual((data["failed"], data["remaining"]), (1, 1))

    def test_paging_stops_at_a_short_page(self):
        full = [{"id": f"A{i}", "dateTime": "2026-01-01T00:00:00", "total": 1.0} for i in range(100)]
        short = [{"id": "B1", "dateTime": "2026-01-02T00:00:00", "total": 1.0}]
        with mock.patch.object(ah, "get_receipts", side_effect=[full, short]) as get, mock.patch.object(ah, "get_receipt", return_value={"id": "x", "items": [], "discounts": [], "payments": []}):
            self.client.post("/api/ah/receipts/sync")
        self.assertEqual([call.args for call in get.call_args_list], [(0, 100), (100, 100)])

    def test_unknown_receipt(self):
        self.assertEqual(self.client.get("/api/ah/receipts/nope").status_code, 404)


class SearchTest(AhOrdersTestCase):
    def setUp(self):
        super().setUp()
        with mock.patch.object(ah, "get_open_fulfillments", return_value=[fulfillment(1, "2026-10-01"), fulfillment(2, "2026-10-20")]), mock.patch.object(ah, "get_order_details", side_effect=fake_details):
            self.client.post("/api/ah/orders/sync")
        with mock.patch.object(ah, "get_receipts", return_value=[{"id": "R1", "dateTime": "2026-10-18T10:00:00", "total": 5.0}]), mock.patch.object(
            ah, "get_receipt", return_value={"id": "R1", "items": [{"productId": 5, "name": "AH Halfvolle melk", "quantity": 3, "price": 1.0, "amount": 3.0}, {"productId": 6, "name": "Koffiebonen", "quantity": 1, "price": 5.0, "amount": 5.0}], "discounts": [], "payments": []}
        ):
            self.client.post("/api/ah/receipts/sync")

    def test_finds_products_in_orders_and_receipts_newest_first(self):
        data = self.client.get("/api/ah/purchases/search?q=melk").get_json()
        self.assertEqual([(o["source"], o["date"]) for o in data["occurrences"]], [("bestelling", "2026-10-23"), ("bestelling", "2026-10-23"), ("kassabon", "2026-10-18")])
        names = {p["product"]: p for p in data["products"]}
        self.assertEqual(names["AH Halfvolle melk"]["totalQuantity"], 3)
        self.assertEqual(names["Halfvolle melk"]["times"], 2)
        self.assertEqual(data["total"], 3)

    def test_words_accents_case_and_empty_queries(self):
        self.assertEqual(self.client.get("/api/ah/purchases/search?q=HALFVOLLE%20Melk").get_json()["total"], 3)
        self.assertEqual(self.client.get("/api/ah/purchases/search?q=cafe").get_json()["total"], 0)
        self.assertEqual(self.client.get("/api/ah/purchases/search?q=koffie").get_json()["products"][0]["product"], "Koffiebonen")
        self.assertEqual(self.client.get("/api/ah/purchases/search?q=").get_json()["products"], [])

    def test_receipts_with_a_product_are_listed_with_the_matching_lines(self):
        with mock.patch.object(ah, "get_receipts", return_value=[{"id": "R2", "dateTime": "2026-10-20T10:00:00", "total": 9.0}, {"id": "R1", "dateTime": "2026-10-18T10:00:00", "total": 5.0}, {"id": "R0", "dateTime": "2026-10-01T10:00:00", "total": 7.0}]), mock.patch.object(
            ah, "get_receipt", side_effect=lambda rid: {"id": rid, "items": [{"productId": 1, "name": "Cola Frisdank 1,5L", "quantity": 2, "price": 1.5, "amount": 3.0}, {"productId": 2, "name": "Brood", "quantity": 1, "price": 2.0, "amount": 2.0}] if rid == "R2" else [{"productId": 3, "name": "Appelsap", "quantity": 1, "price": 1.0, "amount": 1.0}], "discounts": [], "payments": []}
        ):
            self.client.post("/api/ah/receipts/sync")
        data = self.client.get("/api/ah/receipts/search?q=FRISDANK").get_json()
        self.assertEqual((data["total"], data["unsearched"]), (1, 0))
        self.assertEqual(data["receipts"][0]["id"], "R2")
        self.assertEqual(data["receipts"][0]["matches"], [{"name": "Cola Frisdank 1,5L", "quantity": 2, "amount": 3.0}])
        both = self.client.get("/api/ah/receipts/search?q=a").get_json()
        self.assertEqual([r["id"] for r in both["receipts"]], ["R2", "R1", "R0"][: both["total"]])
        self.assertEqual(self.client.get("/api/ah/receipts/search?q=").get_json(), {"query": "", "receipts": [], "total": 0, "unsearched": 0})

    def test_receipts_without_their_lines_are_counted_as_not_searched(self):
        with mock.patch.object(ah, "get_receipts", return_value=[{"id": "R9", "dateTime": "2026-10-19T10:00:00", "total": 4.0}]), mock.patch.object(ah, "get_receipt", side_effect=RuntimeError("AH even weg")):
            self.client.post("/api/ah/receipts/sync")
        data = self.client.get("/api/ah/receipts/search?q=melk").get_json()
        self.assertEqual((data["total"], data["unsearched"]), (1, 1))  # R1 (melk) is gevonden; R9 kon niet worden doorzocht

    def test_search_is_local(self):
        with mock.patch.object(ah, "_get_json", side_effect=AssertionError("AH")), mock.patch.object(ah, "_graphql", side_effect=AssertionError("AH")):
            self.assertEqual(self.client.get("/api/ah/purchases/search?q=melk").status_code, 200)
            self.assertEqual(self.client.get("/api/ah/receipts/search?q=melk").status_code, 200)


class ExploreTest(unittest.TestCase):
    def test_shape_keeps_structure_and_hides_values(self):
        value = {"orderId": 12345, "street": "Geheimstraat", "state": "SCHEDULED", "items": [{"title": "Melk", "price": 1.2, "x": None}, {"title": "Brood"}], "empty": [], "flag": True}
        shape = ah_explore.shape(value)
        self.assertEqual(shape, {"orderId": "int", "street": "str", "state": "str: SCHEDULED", "items": [{"title": "str", "price": "float", "x": "null"}, "×2"], "empty": ["leeg"], "flag": "bool"})
        text = json.dumps(shape)
        for secret in ("12345", "Geheimstraat", "Melk", "1.2"):
            self.assertNotIn(secret, text)

    def test_a_list_shows_the_union_of_all_elements(self):
        shape = ah_explore.shape({"result": [{"status": "SUBMITTED", "amount": 0}, {"status": "CANCELLED", "amount": 12.5, "extra": "x"}]})
        self.assertEqual(shape, {"result": [{"status": "str: SUBMITTED|CANCELLED", "amount": "int|float", "extra": "str"}, "×2"]})

    def test_explore_reports_each_probe_and_never_stores_anything(self):
        def get(path, params=None):
            if path.endswith("summaries/active"):
                return ACTIVE
            if "details-grouped" in path:
                return DETAILS
            raise AssertionError(path)

        def graphql(query, variables=None):
            if "orderFulfillments(status: OPEN)" in query:
                return FULFILLMENTS
            if "orderFulfillments" in query:
                raise RuntimeError("Value 'CLOSED' does not exist in 'FulfillmentStatus'")
            if "posReceiptsPage" in query:
                return RECEIPTS
            return RECEIPT

        with mock.patch.object(ah_explore, "_raw_get", side_effect=get), mock.patch.object(ah_explore, "_raw_graphql", side_effect=graphql), mock.patch.object(ah, "configured_refresh_token", return_value="x"):
            report = ah_explore.explore()
        names = [p["name"] for p in report["probes"]]
        self.assertTrue(any("met status CLOSED" in n for n in names))
        self.assertEqual(sum(1 for p in report["probes"] if p["ok"]), report["ok"])
        closed = next(p for p in report["probes"] if "status CLOSED" in p["name"])
        self.assertFalse(closed["ok"])
        self.assertIn("does not exist", closed["error"])
        opened = next(p for p in report["probes"] if "status: OPEN" in p["name"])
        self.assertEqual(opened["shape"]["orderFulfillments"]["result"][0], {"orderId": "int", "statusCode": "str: SCHEDULED", "statusDescription": "str: Ingepland", "shoppingType": "str: DELIVERY", "transactionCompleted": "bool", "modifiable": "bool", "totalPrice": {"totalPrice": {"amount": "float"}}, "delivery": {"status": "str: SCHEDULED", "method": "str: HOME", "slot": {"date": "str", "dateDisplay": "str", "timeDisplay": "str", "startTime": "str", "endTime": "str"}}})
        text = json.dumps(report, ensure_ascii=False)
        for secret in ("Geheimstraat", "1234AB", "Halfvolle", "Calvé", "4242"):
            self.assertNotIn(secret, text)

    def test_a_route_that_fails_is_reported_not_raised(self):
        with mock.patch.object(ah_explore, "_raw_get", side_effect=RuntimeError("404: not found")), mock.patch.object(ah_explore, "_raw_graphql", side_effect=RuntimeError("boom")), mock.patch.object(ah, "configured_refresh_token", return_value=""):
            report = ah_explore.explore()
        self.assertEqual(report["ok"], 0)
        self.assertIn("overgeslagen", next(p for p in report["probes"] if p["name"].startswith("inhoud"))["error"])


class PermissionsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(os.environ, {"PASSKEY_AUTH_ENABLED": "true", "MEALIESHOPPER_DATA_DIR": tmp.name, "MEALIESHOPPER_AUTH_SECRET": "t"})
        env.start()
        self.addCleanup(env.stop)
        cost = mock.patch.object(auth_password, "SCRYPT_N", 2**4)
        cost.start()
        self.addCleanup(cost.stop)
        self.app = create_app()
        self.admin = self.app.test_client()
        self.admin.post("/api/auth/password", json={"username": "admin", "password": "koffie-met-melk-42"})
        token = self.admin.post("/api/users/invites", json={"username": "anna"}).get_json()["token"]
        self.anna = self.app.test_client()
        self.anna.post("/api/invites/password", json={"token": token, "password": "een-heel-ander-wachtwoord"})

    def test_everything_needs_a_login(self):
        anonymous = self.app.test_client()
        for path in ("/api/ah/basket", "/api/ah/orders", "/api/ah/receipts", "/api/ah/purchases/search?q=x", "/api/ah/receipts/search?q=x", "/api/ah/orders/1"):
            self.assertEqual(anonymous.get(path).status_code, 401, path)
        for path in ("/api/ah/basket/sync", "/api/ah/orders/sync", "/api/ah/receipts/sync", "/api/ah/explore"):
            self.assertEqual(anonymous.post(path).status_code, 401, path)

    def test_users_can_read_and_sync_but_only_the_owner_can_explore(self):
        for path in ("/api/ah/basket", "/api/ah/orders", "/api/ah/receipts"):
            self.assertEqual(self.anna.get(path).status_code, 200, path)
        with mock.patch.object(ah, "get_open_fulfillments", return_value=[]):
            self.assertEqual(self.anna.post("/api/ah/orders/sync").status_code, 200)
        self.assertEqual(self.anna.post("/api/ah/explore").status_code, 403)
        with mock.patch.object(ah_explore, "explore", return_value={"probes": [], "ok": 0}):
            self.assertEqual(self.admin.post("/api/ah/explore").status_code, 200)


if __name__ == "__main__":
    unittest.main()
