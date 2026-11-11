import asyncio
import json
import os
import tempfile
import unittest
from collections.abc import Awaitable, Callable
from contextlib import closing
from typing import Any, TypeVar
from unittest import mock

import aiosqlite

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

from mealieshopper import ah, ah_explore, ah_orders, auth, auth_password, image_cache, product_links, tasks  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402
from helpers import AppClient, AppTestCase, ReadResponse, db, in_db, returned, run  # noqa: E402

T = TypeVar("T")

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
        {"taxonomyName": "Zuivel", "orderedProducts": [{"amount": 2, "quantity": 2, "product": {"webshopId": 111, "title": "Halfvolle melk", "brand": "AH", "salesUnitSize": "1 l", "priceBeforeBonus": 1.45, "currentPrice": 1.29, "isBonus": True}}]},
        {"taxonomyName": "Broodbeleg", "orderedProducts": [{"amount": 1, "quantity": 1, "product": {"webshopId": 222, "title": "Pindakaas", "brand": "Calvé", "salesUnitSize": "350 g", "priceBeforeBonus": 1.99, "currentPrice": None, "isBonus": False}}]},
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



async def stale_columns(conn: aiosqlite.Connection, table: str, columns: tuple[tuple[str, str], ...]) -> None:
    """Een tweede verbinding die de kolom ook nog miste: `_ensure_columns` struikelt niet over "duplicate column name"."""
    with mock.patch.object(conn, "execute_fetchall", new_callable=mock.AsyncMock, return_value=[]):
        await ah_orders._ensure_columns(conn, table, columns)

class AhOrdersTestCase(AppTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(os.environ, {"MEALIESHOPPER_DATA_DIR": tmp.name})
        env.start()
        self.addCleanup(env.stop)
        search = mock.patch.object(ah, "search_hq_ids", return_value=[])  # geen echte zoekopdrachten bij AH
        self.search_hq_ids = search.start()
        self.addCleanup(search.stop)
        products = mock.patch.object(ah, "get_products_by_ids", return_value=[])  # en geen echte foto's ophalen
        self.get_products_by_ids = products.start()
        self.addCleanup(products.stop)
        sleep = mock.patch.object(ah_orders, "_wait", new_callable=mock.AsyncMock)  # de pauze tussen zoekopdrachten
        self.sleep = sleep.start()
        self.addCleanup(sleep.stop)
        pace = mock.patch.dict(ah_orders._PACE, {"last": float("-inf"), "recent": ah_orders.deque()})
        pace.start()
        self.addCleanup(pace.stop)
        prefetch = mock.patch.object(image_cache, "prefetch_async", new_callable=mock.AsyncMock)  # geen task die echt foto's bij AH ophaalt
        prefetch.start()
        self.addCleanup(prefetch.stop)
        self.client = self.client_for(create_app())


class ParsingTest(AppTestCase):
    def test_active_order(self):
        with mock.patch.object(ah, "_get_json", return_value=ACTIVE) as get:
            order = run(ah.get_active_order())
        get.assert_called_once_with("/mobile-services/order/v1/summaries/active", {"sortBy": "DEFAULT"})
        self.assertEqual((order["id"], order["state"], order["total"], order["discount"]), (4242, "OPEN", 25.5, 4.5))
        self.assertEqual((order["deliveryDate"], order["slotStart"], order["slotEnd"]), ("2026-10-23", "18:00", "20:00"))
        self.assertEqual([(i["productId"], i["title"], i["quantity"], i["amount"]) for i in order["items"]], [(111, "Halfvolle melk", 2, 2.58), (222, "Pindakaas", 1, 1.99)])
        self.assertEqual(order["items"][0]["image"], "https://img/111.png")
        self.assertNotIn("Geheimstraat", json.dumps(order))  # het adres nemen we niet over

    def test_no_active_order_means_an_empty_basket(self):
        body = '404: {"status": 404, "message": "Order does not exist: Order should not be null"}'
        with mock.patch.object(ah, "_get_json", side_effect=RuntimeError(body)):
            order = run(ah.get_active_order())
        self.assertEqual((order["id"], order["items"]), (None, []))
        with mock.patch.object(ah, "_get_json", side_effect=RuntimeError("500: kapot")), self.assertRaises(RuntimeError):
            run(ah.get_active_order())
        with mock.patch.object(ah, "_get_json", side_effect=RuntimeError("404: Not Found")), self.assertRaises(RuntimeError):
            run(ah.get_active_order())

    def test_missing_fields_become_empty_not_errors(self):
        with mock.patch.object(ah, "_get_json", return_value={}):
            order = run(ah.get_active_order())
        self.assertEqual((order["id"], order["items"], order["total"]), (None, [], None))
        with mock.patch.object(ah, "_get_json", return_value={"orderedProducts": [{"quantity": "x", "product": None}, "kapot"]}):
            self.assertEqual(len(run(ah.get_active_order())["items"]), 1)

    def test_order_details_are_flattened_with_their_category(self):
        with mock.patch.object(ah, "_get_json", return_value=DETAILS) as get:
            order = run(ah.get_order_details(4242))
        get.assert_called_once_with("/mobile-services/order/v1/4242/details-grouped-by-taxonomy")
        self.assertEqual([(i["title"], i["taxonomy"], i["unitSize"], i["bonus"]) for i in order["items"]], [("Halfvolle melk", "Zuivel", "1 l", True), ("Pindakaas", "Broodbeleg", "350 g", False)])
        self.assertEqual(order["items"][0]["price"], 1.29)
        self.assertEqual(order["items"][1]["price"], 1.99)  # geen bonusprijs: de gewone prijs
        self.assertEqual([i["amount"] for i in order["items"]], [2.58, 1.99])  # aantal x prijs: `amount` van AH is een aantal
        self.assertEqual((order["state"], order["slotStart"]), ("DELIVERED", "2026-10-23T18:00:00"))

    def test_open_fulfillments(self):
        with mock.patch.object(ah, "get_user_token", return_value="t"), mock.patch.object(ah, "_graphql", return_value=FULFILLMENTS) as gql:
            orders = run(ah.get_open_fulfillments())
        self.assertIn("orderFulfillments(status: OPEN)", gql.call_args.args[1])
        self.assertNotIn("address", gql.call_args.args[1])  # we vragen het adres niet eens op
        self.assertEqual([(o["id"], o["state"], o["total"], o["deliveryDate"], o["modifiable"]) for o in orders], [(4242, "SCHEDULED", 25.5, "2026-10-23", True)])

    def test_receipts(self):
        with mock.patch.object(ah, "get_user_token", return_value="t"), mock.patch.object(ah, "_graphql", side_effect=[RECEIPTS, RECEIPT]):
            listed = run(ah.get_receipts(0, 100))
            detail = run(ah.get_receipt("R2"))
        self.assertEqual([(r["id"], r["total"]) for r in listed], [("R2", 12.34), ("R1", 5.0)])
        self.assertEqual((detail["items"][0]["name"], detail["items"][0]["amount"]), ("AH Halfvolle melk", 2.58))
        self.assertEqual((detail["discounts"][0]["amount"], detail["payments"][0]["method"]), (-0.5, "PIN"))


def shopping_entry(title, quantity=1, struck=False, product=True, **extra):
    """Een regel zoals AH hem geeft: het product zit in productDetails.product."""
    entry = {"listItemId": 1, "originCode": "PRD", "position": 1, "quantity": quantity, "strikedthrough": struck, "type": "SHOPPABLE", "sorting": {"position": 1}, **extra}
    if product:
        entry["productDetails"] = {"missingBonusOffersQuantity": 0, "product": {
            "webshopId": 111, "title": title, "brand": "AH", "salesUnitSize": "1 l", "currentPrice": 1.29, "priceBeforeBonus": 1.45, "isBonus": True,
            "images": [{"url": "https://img/111.png", "width": 200, "height": 200}]}}
    return entry


SHOPPING = {"id": "x", "storeNumber": 1, "items": [
    shopping_entry("Halfvolle melk", 2),
    shopping_entry("Brood", 1, struck=True),
    {"listItemId": 3, "type": "FREE_TEXT", "description": "Bloemen voor mama", "quantity": 1, "strikedthrough": False},
]}


class BasketCacheTest(AhOrdersTestCase):
    async def test_nothing_synced_yet(self):
        self.assertEqual((await self.client.get("/api/ah/basket")).get_json(), {"syncedAt": None, "items": [], "itemCount": 0, "units": 0})

    def test_the_basket_is_the_shopping_list_without_ticked_off_lines(self):
        with mock.patch.object(ah, "_get_json", return_value=SHOPPING) as get:
            basket = run(ah.get_shopping_list())
        get.assert_called_once_with("/mobile-services/shoppinglist/v2/items")
        self.assertEqual([(i["productId"], i["title"], i["quantity"]) for i in basket["items"]], [(111, "Halfvolle melk", 2), (None, "Bloemen voor mama", 1)])
        melk = basket["items"][0]
        self.assertEqual((melk["brand"], melk["unitSize"], melk["price"], melk["amount"], melk["bonus"], melk["image"]), ("AH", "1 l", 1.29, 2.58, True, "https://img/111.png"))

    def test_the_shopping_list_is_read_leniently(self):
        for body in ([{"description": "Melk", "quantity": 3}], {"products": [{"product": {"title": "Melk", "webshopId": 5}, "quantity": 3}]}, {"items": [None, "kapot", {"quantity": 1}]}, {}, None):
            with mock.patch.object(ah, "_get_json", return_value=body):
                items = run(ah.get_shopping_list())["items"]
            self.assertEqual([(i["title"], i["quantity"]) for i in items], [("Melk", 3)] if body and ("products" in str(body) or "description" in str(body)) else [], body)

    async def test_sync_stores_a_snapshot_and_reading_never_asks_ah(self):
        with mock.patch.object(ah, "_get_json", return_value=SHOPPING):
            data = (await self.client.post("/api/ah/basket/sync")).get_json()
        self.assertEqual((data["itemCount"], data["units"], data["total"]), (2, 3, None))
        with mock.patch.object(ah, "_get_json", side_effect=AssertionError("AH mag niet worden gebruikt")):
            cached = (await self.client.get("/api/ah/basket")).get_json()
        self.assertEqual((cached["itemCount"], [i["title"] for i in cached["items"]]), (2, ["Halfvolle melk", "Bloemen voor mama"]))

    async def test_the_next_sync_replaces_the_snapshot(self):
        with mock.patch.object(ah, "_get_json", return_value=SHOPPING):
            await self.client.post("/api/ah/basket/sync")
        with mock.patch.object(ah, "_get_json", return_value={"items": SHOPPING["items"][:1]}):
            self.assertEqual((await self.client.post("/api/ah/basket/sync")).get_json()["itemCount"], 1)
        with mock.patch.object(ah, "_get_json", return_value={"items": []}):
            self.assertEqual((await self.client.post("/api/ah/basket/sync")).get_json()["itemCount"], 0)

    async def test_a_failed_sync_keeps_the_copy(self):
        with mock.patch.object(ah, "_get_json", return_value=SHOPPING):
            await self.client.post("/api/ah/basket/sync")
        with mock.patch.object(ah, "_get_json", side_effect=RuntimeError("AH account niet gekoppeld")):
            response = await self.client.post("/api/ah/basket/sync")
        self.assertEqual((response.status_code, response.get_json()["error"]), (424, "AH account niet gekoppeld"))
        self.assertEqual((await self.client.get("/api/ah/basket")).get_json()["itemCount"], 2)


def fake_details(order_id):
    return {"id": order_id, "state": "DELIVERED", "deliveryType": "DELIVERY", "deliveryDate": "2026-10-23", "slotStart": "18:00", "slotEnd": "20:00",
            "items": [{"productId": 111, "title": "Halfvolle melk", "brand": "AH", "unitSize": "1 l", "quantity": 2, "amount": 2.58, "price": 1.29, "priceBeforeBonus": 1.45, "bonus": True, "taxonomy": "Zuivel", "image": ""}]}


def fulfillment(order_id, date="2026-10-23"):
    return {"id": order_id, "state": "SCHEDULED", "stateText": "Ingepland", "shoppingType": "DELIVERY", "method": "HOME", "modifiable": True, "total": 25.5, "deliveryDate": date, "slotStart": "18:00", "slotEnd": "20:00", "deliveryStatus": "SUBMITTED"}


def closed_order(order_id, status="DELIVERED", date="2026-10-01"):
    return {**fulfillment(order_id, date), "state": "9", "stateText": "Afgeleverd", "deliveryStatus": status}


class OrdersCacheTest(AhOrdersTestCase):
    async def sync(self, open_orders, details=fake_details, closed=()):
        # Een lijst met bestellingen wordt als een pagina geleverd; een volgende pagina (offset > 0) is leeg.
        def pages(offset=0):
            return list(closed) if not offset else []

        with mock.patch.object(ah, "get_open_fulfillments", return_value=open_orders), mock.patch.object(ah, "get_closed_fulfillments", side_effect=pages), mock.patch.object(ah, "get_order_details", side_effect=details):
            return await self.client.post("/api/ah/orders/sync")

    async def test_a_sync_starts_a_small_prefetch_round_for_the_photos(self):
        with mock.patch.object(image_cache, "prefetch_async", new_callable=mock.AsyncMock) as prefetch:
            await self.sync([{"id": 1, "state": "SUBMITTED", "deliveryDate": "2026-10-10", "slotStart": None, "slotEnd": None, "total": 1.0}])
        prefetch.assert_awaited_once_with(limit=40)

    async def test_the_list_route_carries_a_summary_but_the_function_does_not_by_default(self):
        def details(order_id):
            return {**fake_details(order_id), "items": [{**fake_details(order_id)["items"][0], "image": "https://static.ah.nl/dam/1"}]}

        await self.sync([fulfillment(1)], details)
        listed = (await self.client.get("/api/ah/orders")).get_json()["orders"][0]
        self.assertEqual((listed["images"], listed["productNames"], listed["bonusCount"]), (["https://static.ah.nl/dam/1"], ["Halfvolle melk"], 1))
        self.assertAlmostEqual(listed["savings"], 0.32)  # (1.45 - 1.29) * 2
        self.assertNotIn("images", (await ah_orders.list_orders())["orders"][0])

    async def test_the_list_summary_prefers_the_30_day_regular_price_and_falls_back_to_the_price_before_bonus(self):
        first = {"productId": 1, "title": "AH Bananen", "quantity": 2, "price": 0.89, "priceBeforeBonus": None, "bonus": False, "taxonomy": "", "image": ""}
        await self.sync([], lambda order_id: {**fake_details(order_id), "items": [first, fake_details(order_id)["items"][0]]}, closed=[closed_order(1)])
        with closing(db(ah_orders)) as conn, conn:
            conn.execute("UPDATE ah_order_items SET regular_price = 1.19 WHERE product_id = 1")
        listed = (await self.client.get("/api/ah/orders")).get_json()["orders"][0]
        # regel 1: gewone prijs 1.19 tegen 0.89, twee keer: 0.60. Regel 2 heeft geen gewone prijs: (1.45 - 1.29) * 2 = 0.32.
        self.assertAlmostEqual(listed["savings"], 0.92)
        self.assertEqual((listed["statusKind"], listed["statusText"]), ("delivered", None))

    async def test_an_open_order_in_the_list_carries_the_status_text_of_ah(self):
        await in_db(ah_orders._connect, lambda conn: ah_orders._save_order(conn, {"id": 1, "deliveryDate": "2026-10-23", "stateText": "Ingepland"}, "open", []))
        listed = (await self.client.get("/api/ah/orders")).get_json()["orders"][0]
        self.assertEqual((listed["statusKind"], listed["statusText"]), ("planned", "Ingepland"))

    async def test_the_summary_keeps_the_order_of_the_lines_and_stops_at_six_photos_and_four_names(self):
        def line(n, image=True):
            return {"productId": n, "title": f"Product {n}", "brand": "AH", "unitSize": "1 st", "quantity": 1, "amount": 1.0, "price": 1.0,
                    "priceBeforeBonus": None, "bonus": False, "taxonomy": "", "image": f"https://static.ah.nl/dam/{n}" if image else ""}

        def details(order_id):
            return {**fake_details(order_id), "items": [line(n, image=n != 3) for n in range(1, 9)]}

        await self.sync([fulfillment(1)], details)
        listed = (await self.client.get("/api/ah/orders")).get_json()["orders"][0]
        self.assertEqual(listed["images"], [f"https://static.ah.nl/dam/{n}" for n in (1, 2, 4, 5, 6, 7)])
        self.assertEqual(listed["productNames"], ["Product 1", "Product 2", "Product 3", "Product 4"])
        self.assertEqual((listed["savings"], listed["bonusCount"]), (0.0, 0))
        with closing(db(ah_orders)) as conn, conn:
            conn.execute("DELETE FROM ah_order_items")
        listed = (await self.client.get("/api/ah/orders")).get_json()["orders"][0]
        self.assertEqual((listed["images"], listed["productNames"], listed["savings"], listed["bonusCount"]), ([], [], None, 0))

    async def test_open_orders_are_stored_with_their_products(self):
        response = await self.sync([fulfillment(1)])
        self.assertEqual(response.get_json(), {"open": 1, "archived": 0, "remaining": 0, "unfinished": 0})
        listed = (await self.client.get("/api/ah/orders")).get_json()
        self.assertTrue(listed["syncedAt"])
        self.assertEqual([(o["id"], o["kind"], o["itemCount"], o["total"]) for o in listed["orders"]], [(1, "open", 1, 25.5)])
        order = (await self.client.get("/api/ah/orders/1")).get_json()
        self.assertEqual((order["items"][0]["title"], order["items"][0]["taxonomy"], order["itemsSynced"]), ("Halfvolle melk", "Zuivel", True))

    async def test_an_order_that_is_no_longer_open_is_archived_as_delivered_and_kept(self):
        await self.sync([fulfillment(1), fulfillment(2)])
        response = await self.sync([fulfillment(2)])
        self.assertEqual(response.get_json(), {"open": 1, "archived": 1, "remaining": 0, "unfinished": 0})
        kinds = {o["id"]: o["kind"] for o in (await self.client.get("/api/ah/orders")).get_json()["orders"]}
        self.assertEqual(kinds, {1: "delivered", 2: "open"})
        # En hij blijft staan, ook als AH hem later nergens meer toont.
        await self.sync([])
        self.assertEqual(len((await self.client.get("/api/ah/orders")).get_json()["orders"]), 2)

    async def test_closed_orders_from_ah_are_kept_as_delivered_and_cancelled_ones_are_not(self):
        data = (await self.sync([], closed=[closed_order(5, "DELIVERED", "2026-10-02"), closed_order(6, "CANCELLED"), closed_order(7, "DELIVERED", "2026-09-20")])).get_json()
        self.assertEqual((data["archived"], data["remaining"]), (2, 0))
        listed = (await self.client.get("/api/ah/orders")).get_json()["orders"]
        self.assertEqual([(o["id"], o["kind"], o["itemCount"]) for o in listed], [(7, "delivered", 1), (5, "delivered", 1)])
        # Een volgende keer worden bewaarde bestellingen niet opnieuw opgehaald.
        calls = []
        again = (await self.sync([], lambda order_id: calls.append(order_id) or fake_details(order_id), closed=[closed_order(5), closed_order(8)])).get_json()
        self.assertEqual((calls, again["archived"]), ([8], 1))

    async def test_a_cancelled_order_is_forgotten_and_not_archived_as_delivered(self):
        await self.sync([fulfillment(1)])
        data = (await self.sync([], closed=[closed_order(1, "CANCELLED")])).get_json()
        self.assertEqual((data["archived"], data["unfinished"]), (0, 0))
        self.assertEqual((await self.client.get("/api/ah/orders")).get_json()["orders"], [])

    async def test_more_closed_orders_than_fit_in_one_sync_finish_next_time(self):
        closed = [closed_order(100 + number) for number in range(ah_orders.ORDER_DETAILS_PER_SYNC + 3)]
        first = (await self.sync([], closed=closed)).get_json()
        self.assertEqual((first["archived"], first["remaining"]), (len(closed), 3))
        second = (await self.sync([], closed=closed)).get_json()
        self.assertEqual(second["remaining"], 0)
        self.assertTrue(all(o["itemsSynced"] for o in (await self.client.get("/api/ah/orders")).get_json()["orders"]))

    async def test_closed_orders_are_fetched_page_by_page_until_nothing_new_is_left(self):
        newest = [closed_order(1000 + n, date="2026-10-%02d" % (28 - n)) for n in range(10)]
        older = [closed_order(2000 + n, date="2026-09-%02d" % (28 - n)) for n in range(10)]
        asked = []

        def pages(offset=0):
            asked.append(offset)
            return {0: newest, 10: older}.get(offset, [])

        with mock.patch.object(ah, "get_open_fulfillments", return_value=[]), mock.patch.object(ah, "get_closed_fulfillments", side_effect=pages), mock.patch.object(ah, "get_order_details", side_effect=fake_details):
            await self.client.post("/api/ah/orders/sync")
            self.assertEqual(asked, [0, 10, 20])  # tot een lege pagina
            self.assertEqual(len((await self.client.get("/api/ah/orders")).get_json()["orders"]), 20)
            await self.client.post("/api/ah/orders/sync")  # alles is bewaard en compleet
            await self.client.post("/api/ah/orders/sync")
        self.assertEqual(asked[3:], [0, 0])  # de eerste pagina bevat niets nieuws meer, dus geen tweede pagina

    async def test_a_page_that_repeats_itself_stops_the_paging(self):
        page = [closed_order(7)]
        asked = []

        def pages(offset=0):
            asked.append(offset)
            return page  # AH negeert de offset

        with mock.patch.object(ah, "get_open_fulfillments", return_value=[]), mock.patch.object(ah, "get_closed_fulfillments", side_effect=pages), mock.patch.object(ah, "get_order_details", side_effect=fake_details):
            await self.client.post("/api/ah/orders/sync")
        self.assertEqual(asked, [0, 1])  # na een pagina van 1 bestelling is offset 1 de volgende; die herhaalt zich

    async def test_cancelled_orders_in_the_open_list_are_not_kept(self):
        data = (await self.sync([fulfillment(1), {**fulfillment(2), "deliveryStatus": "CANCELLED", "stateText": "Geannuleerd"}])).get_json()
        self.assertEqual(data["open"], 1)
        self.assertEqual([o["id"] for o in (await self.client.get("/api/ah/orders")).get_json()["orders"]], [1])

    async def test_failing_closed_query_does_not_stop_the_open_orders(self):
        with mock.patch.object(ah, "get_open_fulfillments", return_value=[fulfillment(1)]), mock.patch.object(ah, "get_closed_fulfillments", side_effect=RuntimeError("AH even weg")), mock.patch.object(ah, "get_order_details", side_effect=fake_details):
            data = (await self.client.post("/api/ah/orders/sync")).get_json()
        self.assertEqual(data["open"], 1)

    async def test_archiving_that_fails_is_retried_next_time(self):
        await self.sync([fulfillment(1)])

        def flaky(order_id):
            raise RuntimeError("AH even weg")

        self.assertEqual((await self.sync([], flaky)).get_json(), {"open": 0, "archived": 0, "remaining": 0, "unfinished": 1})
        self.assertEqual((await self.client.get("/api/ah/orders")).get_json()["orders"][0]["kind"], "open")
        self.assertEqual((await self.sync([])).get_json()["archived"], 1)

    async def test_missing_products_do_not_wipe_what_we_had(self):
        await self.sync([fulfillment(1)])

        def broken(order_id):
            raise RuntimeError("kapot")

        await self.sync([fulfillment(1)], broken)
        order = (await self.client.get("/api/ah/orders/1")).get_json()
        self.assertEqual(len(order["items"]), 1)
        self.assertTrue(order["itemsSynced"])

    async def test_open_first_then_newest_delivered(self):
        await self.sync([fulfillment(1, "2026-10-01"), fulfillment(2, "2026-10-09"), fulfillment(3, "2026-10-20")])
        await self.sync([fulfillment(3, "2026-10-20")])
        self.assertEqual([o["id"] for o in (await self.client.get("/api/ah/orders")).get_json()["orders"]], [3, 2, 1])

    async def test_line_amounts_are_quantity_times_price_even_for_orders_stored_before(self):
        await self.sync([fulfillment(1)])
        from contextlib import closing


        with closing(db()) as conn, conn:  # zoals 2.4.0 het bewaarde: het aantal in plaats van het bedrag
            conn.execute("UPDATE ah_order_items SET amount = 2")
        item = (await self.client.get("/api/ah/orders/1")).get_json()["items"][0]
        self.assertEqual((item["quantity"], item["price"], item["amount"]), (2, 1.29, 2.58))
        hit = next(o for o in (await self.client.get("/api/ah/purchases/search?q=melk")).get_json()["occurrences"] if o["source"] == "bestelling")
        self.assertEqual(hit["amount"], 2.58)

    async def test_unknown_order(self):
        self.assertEqual((await self.client.get("/api/ah/orders/999")).status_code, 404)

    async def test_the_address_is_never_stored(self):
        await self.sync([fulfillment(1)])
        with mock.patch.object(ah, "_get_json", return_value=ACTIVE):
            await self.client.post("/api/ah/basket/sync")
        from contextlib import closing


        with closing(db()) as conn:
            dump = "\n".join(conn.iterdump())
        for secret in ("Geheimstraat", "1234AB"):
            self.assertNotIn(secret, dump)

    def test_a_delivered_order_can_be_added_by_id(self):
        with mock.patch.object(ah, "get_order_details", side_effect=fake_details):
            order = run(ah_orders.add_order_by_id(77))
        self.assertEqual((order["id"], order["kind"], order["itemCount"]), (77, "delivered", 1))


class ReceiptsCacheTest(AhOrdersTestCase):
    async def sync(self, pages, detail=lambda rid: {"id": rid, "items": [{"productId": 1, "name": "AH Halfvolle melk", "quantity": 2, "price": 1.29, "amount": 2.58}], "discounts": [], "payments": [{"method": "PIN", "amount": 2.58}]}):
        with mock.patch.object(ah, "get_receipts", side_effect=pages), mock.patch.object(ah, "get_receipt", side_effect=detail):
            return await self.client.post("/api/ah/receipts/sync")

    async def test_new_receipts_are_added_with_their_lines(self):
        data = (await self.sync([[{"id": "R2", "dateTime": "2026-10-18T10:00:00", "total": 12.34}, {"id": "R1", "dateTime": "2026-10-10T09:00:00", "total": 5.0}]])).get_json()
        self.assertEqual(data, {"new": 2, "total": 2, "remaining": 0, "failed": 0, "products": {"found": 0, "missing": 1, "failed": 0, "searches": 2}})
        listed = (await self.client.get("/api/ah/receipts")).get_json()
        self.assertEqual([(r["id"], r["itemCount"], r["detailsSynced"]) for r in listed["receipts"]], [("R2", 1, True), ("R1", 1, True)])
        receipt = (await self.client.get("/api/ah/receipts/R2")).get_json()
        self.assertEqual((receipt["items"][0]["name"], receipt["payments"][0]["method"]), ("AH Halfvolle melk", "PIN"))

    async def test_the_receipt_list_carries_photos_of_linked_lines_the_names_the_savings_and_the_payment(self):
        detail = lambda rid: {"id": rid, "items": [  # noqa: E731
            {"productId": 11, "name": "MELK", "quantity": 1, "price": 1.0, "amount": 1.0},
            {"productId": 12, "name": "KAAS", "quantity": 1, "price": 2.0, "amount": 2.0},
            {"productId": 13, "name": "BROOD", "quantity": 1, "price": 3.0, "amount": 3.0},
        ], "discounts": [{"name": "Bonus", "amount": -0.5}, {"name": "Bonus", "amount": -1.0}], "payments": [{"method": "PIN", "amount": 4.5}]}
        await self.sync([[{"id": "R1", "dateTime": "2026-10-10T09:00:00", "total": 4.5}]], detail)
        with closing(db(ah_orders)) as conn, conn:
            conn.execute("DELETE FROM ah_receipt_products")
            conn.execute("INSERT INTO ah_receipt_products (receipt_product_id, product_id, title, checked_at, image, unit_size) VALUES (12, 4183, 'AH Halfvolle melk 1 l', 'x', 'https://static.ah.nl/dam/kaas', '1 l')")
        listed = (await self.client.get("/api/ah/receipts")).get_json()["receipts"][0]
        self.assertEqual(listed["images"], ["https://static.ah.nl/dam/kaas"])
        self.assertEqual(listed["productNames"], ["MELK", "AH Halfvolle melk 1 l", "BROOD"])
        self.assertEqual((listed["savings"], listed["payment"]), (1.5, "PIN"))
        self.assertNotIn("images", (await ah_orders.list_receipts())["receipts"][0])
        with closing(db(ah_orders)) as conn, conn:
            conn.execute("UPDATE ah_receipts SET discounts = '[]', payments = '[]'")
        listed = (await self.client.get("/api/ah/receipts")).get_json()["receipts"][0]
        self.assertEqual((listed["savings"], listed["payment"]), (0.0, None))
        with closing(db(ah_orders)) as conn, conn:
            conn.execute("DELETE FROM ah_receipt_items")
        self.assertIsNone((await self.client.get("/api/ah/receipts")).get_json()["receipts"][0]["savings"])

    async def test_a_receipt_summary_adds_the_history_saving_to_the_bonus_lines(self):
        detail = lambda rid: {"id": rid, "items": [  # noqa: E731
            {"productId": 11, "name": "MELK", "quantity": 4, "price": 0.99, "amount": 3.96},
            {"productId": 12, "name": "KAAS", "quantity": 1, "price": 2.0, "amount": 2.0},
        ], "discounts": [{"name": "Bonus", "amount": -3.97}], "payments": [{"method": "PIN", "amount": 2.0}]}
        await self.sync([[{"id": "R1", "dateTime": "2026-10-10T09:00:00", "total": 2.0}]], detail)
        with closing(db(ah_orders)) as conn, conn:
            conn.execute("UPDATE ah_receipt_items SET regular_price = 1.19 WHERE product_id = 11")
        listed = (await self.client.get("/api/ah/receipts")).get_json()["receipts"][0]
        self.assertAlmostEqual(listed["savings"], 4.77)  # 3.97 van de bonusregels + (1.19 - 0.99) * 4
        self.assertEqual((listed["statusKind"], listed["statusText"]), ("receipt", None))

    async def test_a_receipt_with_broken_discount_json_does_not_break_the_list(self):
        await self.sync([[{"id": "R1", "dateTime": "2026-10-10T09:00:00", "total": 4.5}]])
        with closing(db(ah_orders)) as conn, conn:
            conn.execute("UPDATE ah_receipts SET discounts = 'rommel', payments = ''")
        response = await self.client.get("/api/ah/receipts")
        self.assertEqual(response.status_code, 200)
        self.assertEqual((response.get_json()["receipts"][0]["savings"], response.get_json()["receipts"][0]["payment"]), (0.0, None))

    async def test_only_new_receipts_are_fetched_next_time(self):
        await self.sync([[{"id": "R1", "dateTime": "2026-10-10T09:00:00", "total": 5.0}]])
        calls = []

        def detail(rid):
            calls.append(rid)
            return {"id": rid, "items": [], "discounts": [], "payments": []}

        await self.sync([[{"id": "R2", "dateTime": "2026-10-18T10:00:00", "total": 1.0}, {"id": "R1", "dateTime": "2026-10-10T09:00:00", "total": 5.0}]], detail)
        self.assertEqual(calls, ["R2"])

    async def test_many_receipts_are_fetched_in_batches(self):
        page = [{"id": f"R{i:03d}", "dateTime": f"2026-01-{i % 28 + 1:02d}T10:00:00", "total": 1.0} for i in range(60)]
        with mock.patch.object(ah_orders, "RECEIPT_DETAILS_PER_SYNC", 25):
            first = (await self.sync([page])).get_json()
            self.assertEqual((first["new"], first["remaining"]), (60, 35))
            second = (await self.sync([page])).get_json()
            self.assertEqual((second["new"], second["remaining"]), (0, 10))

    async def test_a_failing_receipt_is_retried_and_the_rest_still_arrives(self):
        def detail(rid):
            if rid == "R1":
                raise RuntimeError("503")
            return {"id": rid, "items": [], "discounts": [], "payments": []}

        data = (await self.sync([[{"id": "R1", "dateTime": "2026-10-10T09:00:00", "total": 1.0}, {"id": "R2", "dateTime": "2026-10-11T09:00:00", "total": 1.0}]], detail)).get_json()
        self.assertEqual((data["failed"], data["remaining"]), (1, 1))

    async def test_paging_stops_at_a_short_page(self):
        full = [{"id": f"A{i}", "dateTime": "2026-01-01T00:00:00", "total": 1.0} for i in range(100)]
        short = [{"id": "B1", "dateTime": "2026-01-02T00:00:00", "total": 1.0}]
        with mock.patch.object(ah, "get_receipts", side_effect=[full, short]) as get, mock.patch.object(ah, "get_receipt", return_value={"id": "x", "items": [], "discounts": [], "payments": []}):
            await self.client.post("/api/ah/receipts/sync")
        self.assertEqual([call.args for call in get.call_args_list], [(0, 100), (100, 100)])

    async def test_unknown_receipt(self):
        self.assertEqual((await self.client.get("/api/ah/receipts/nope")).status_code, 404)
        self.assertEqual((await self.client.post("/api/ah/receipts/nope/products")).status_code, 404)

    async def test_a_line_carries_the_webshop_product_once_it_is_linked(self):
        # Op de bon staat het artikelnummer (hqId); de koppeling naar het webwinkelproduct komt uit ah_receipt_products.
        await self.sync([[{"id": "R1", "dateTime": "2026-10-10T09:00:00", "total": 2.58}]])
        line = (await self.client.get("/api/ah/receipts/R1")).get_json()["items"][0]
        self.assertEqual((line["productId"], line["product"]), (1, None))
        with closing(db(ah_orders)) as conn, conn:  # de sync probeerde het al (niet gevonden), nu is het er wel
            conn.execute("UPDATE ah_receipt_products SET product_id = 4183, title = 'AH Halfvolle melk 1 l' WHERE receipt_product_id = 1")
        line = (await self.client.get("/api/ah/receipts/R1")).get_json()["items"][0]
        self.assertEqual(line["product"], {"id": 4183, "title": "AH Halfvolle melk 1 l", "image": ""})

    async def test_opening_a_receipt_looks_up_the_lines_that_are_not_linked_yet(self):
        await self.sync([[{"id": "R1", "dateTime": "2026-10-10T09:00:00", "total": 2.58}]])
        with closing(db(ah_orders)) as conn, conn:  # alsof de sync het nog niet geprobeerd had
            conn.execute("DELETE FROM ah_receipt_products")
        self.search_hq_ids.reset_mock()
        self.search_hq_ids.return_value = [(1, 4183, "AH Halfvolle melk 1 l", "")]
        data = (await self.client.post("/api/ah/receipts/R1/products")).get_json()
        self.assertEqual(data["items"][0]["product"], {"id": 4183, "title": "AH Halfvolle melk 1 l", "image": ""})
        self.assertEqual((data["products"]["found"], data["products"]["searches"]), (1, 1))
        self.assertEqual(self.search_hq_ids.call_args.args[0], "AH Halfvolle melk")
        # Alles gekoppeld: geen zoekopdracht meer.
        self.search_hq_ids.reset_mock()
        again = (await self.client.post("/api/ah/receipts/R1/products")).get_json()
        self.assertEqual((again["products"]["searches"], self.search_hq_ids.call_count), (0, 0))

    async def test_opening_a_receipt_fetches_the_photos_of_its_linked_lines(self):
        await self.sync([[{"id": "R1", "dateTime": "2026-10-10T09:00:00", "total": 2.58}]])
        with closing(db(ah_orders)) as conn, conn:  # gekoppeld voor de bonfoto's bestonden: nog geen foto opgezocht
            conn.execute("UPDATE ah_receipt_products SET product_id = 4183, title = 'AH Halfvolle melk 1 l', image = NULL WHERE receipt_product_id = 1")
        self.get_products_by_ids.return_value = [{"id": 4183, "title": "AH Halfvolle melk 1 l", "image": "https://static.ah.nl/dam/melk"}]
        data = (await self.client.post("/api/ah/receipts/R1/products")).get_json()
        self.assertEqual(data["items"][0]["product"]["image"], "https://static.ah.nl/dam/melk")
        self.assertEqual(data["images"], {"products": 1, "requests": 1, "failed": 0})
        self.assertEqual(self.get_products_by_ids.call_args.args[0], [4183])
        self.get_products_by_ids.reset_mock()
        again = (await self.client.post("/api/ah/receipts/R1/products")).get_json()
        self.assertEqual((again["images"]["requests"], self.get_products_by_ids.call_count), (0, 0), "de foto staat er: niets meer te vragen")

    async def test_a_failing_photo_lookup_still_returns_the_receipt(self):
        await self.sync([[{"id": "R1", "dateTime": "2026-10-10T09:00:00", "total": 2.58}]])
        with closing(db(ah_orders)) as conn, conn:
            conn.execute("UPDATE ah_receipt_products SET product_id = 4183, image = NULL WHERE receipt_product_id = 1")
        self.get_products_by_ids.side_effect = RuntimeError("503")
        data = (await self.client.post("/api/ah/receipts/R1/products")).get_json()
        self.assertEqual((data["id"], data["items"][0]["product"]["image"], data["images"]["failed"]), ("R1", "", 1))

    async def test_the_receipt_list_starts_a_background_round_for_the_missing_photos(self):
        with mock.patch.object(ah_orders, "backfill_receipt_images_async", new_callable=mock.AsyncMock) as backfill:
            await self.client.get("/api/ah/receipts")
        backfill.assert_called_once_with()

    def open_list_twice(self) -> None:
        """Twee keer de bonnenlijst openen terwijl de eerste ronde nog niet liep, en wachten tot die ronde klaar is."""

        async def scenario() -> None:
            await ah_orders.backfill_receipt_images_async()
            await ah_orders.backfill_receipt_images_async()
            task = ah_orders._BACKFILL_TASK
            if task is not None and not task.done():
                await asyncio.wait([task], timeout=5)

        run(scenario())

    def test_the_background_round_runs_once_at_a_time_and_only_when_photos_are_missing(self) -> None:
        with mock.patch.object(ah_orders, "backfill_receipt_images", new_callable=mock.AsyncMock) as backfill:
            self.open_list_twice()
        backfill.assert_not_called()
        with closing(db(ah_orders)) as conn, conn:
            conn.execute("INSERT INTO ah_receipt_products (receipt_product_id, product_id, title, checked_at) VALUES (1, 4183, 'P', 'x')")
        with mock.patch.object(ah_orders, "backfill_receipt_images", new_callable=mock.AsyncMock) as backfill:
            self.open_list_twice()
        backfill.assert_called_once_with(batches=ah_orders.RECEIPT_IMAGES_BATCHES_OPEN, trigger="list")
        self.assertEqual(ah_orders._BACKFILL_TASK.get_name(), "receipt-images")

    def test_a_background_round_cancelled_at_shutdown_lets_the_next_one_start(self) -> None:
        with closing(db(ah_orders)) as conn, conn:
            conn.execute("INSERT INTO ah_receipt_products (receipt_product_id, product_id, title, checked_at) VALUES (1, 4183, 'P', 'x')")
        started = asyncio.Event()

        async def hang(**kwargs: Any) -> None:
            started.set()
            await asyncio.Event().wait()  # tot het afsluiten van de app

        async def scenario() -> tuple[asyncio.Task[None], asyncio.Task[None]]:
            async with self.client.app.test_app():
                await ah_orders.backfill_receipt_images_async()
                await asyncio.wait_for(started.wait(), 5)
                first = ah_orders._BACKFILL_TASK
                await ah_orders.backfill_receipt_images_async()  # loopt nog: geen tweede
                return first, ah_orders._BACKFILL_TASK

        with mock.patch.object(ah_orders, "backfill_receipt_images", new_callable=mock.AsyncMock, side_effect=hang):
            first, again = run(scenario())
        self.assertIs(first, again)
        self.assertTrue(first.cancelled(), "het afsluiten annuleert de ronde")
        with mock.patch.object(ah_orders, "backfill_receipt_images", new_callable=mock.AsyncMock) as backfill:
            self.open_list_twice()
        backfill.assert_called_once_with(batches=ah_orders.RECEIPT_IMAGES_BATCHES_OPEN, trigger="list")

    async def test_a_sync_starts_a_small_prefetch_round(self):
        with mock.patch.object(image_cache, "prefetch_async", new_callable=mock.AsyncMock) as prefetch:
            await self.sync([[{"id": "R1", "dateTime": "2026-10-10T09:00:00", "total": 2.58}]])
        prefetch.assert_awaited_once_with(limit=40)

    def test_an_old_database_gets_the_image_column_once_even_from_two_connections(self):
        with closing(db()) as conn, conn:
            conn.execute("DROP TABLE IF EXISTS ah_receipt_products")
            conn.execute("CREATE TABLE ah_receipt_products (receipt_product_id INTEGER PRIMARY KEY, product_id INTEGER, title TEXT, checked_at TEXT NOT NULL)")
        with closing(db(ah_orders)) as conn:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(ah_receipt_products)")}
        self.assertIn("image", columns)
        run(in_db(ah_orders._connect, lambda conn: stale_columns(conn, "ah_receipt_products", (("image", "TEXT"),))))

    def test_the_schema_gains_the_regular_price_columns(self):
        with closing(db(ah_orders)) as conn:
            for table in ("ah_order_items", "ah_receipt_items"):
                names = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
                self.assertLessEqual({"regular_price", "regular_checked_at"}, names)

    def test_an_old_database_gains_the_regular_price_columns_and_keeps_its_lines(self):
        with closing(db()) as conn, conn:
            conn.execute("DROP TABLE IF EXISTS ah_order_items")
            conn.execute(
                "CREATE TABLE ah_order_items (order_id INTEGER NOT NULL, position INTEGER NOT NULL, product_id INTEGER, title TEXT, brand TEXT, "
                "unit_size TEXT, quantity INTEGER, amount REAL, price REAL, price_before_bonus REAL, bonus INTEGER, taxonomy TEXT, image TEXT, "
                "PRIMARY KEY (order_id, position))"
            )
            conn.execute("INSERT INTO ah_order_items (order_id, position, product_id, title) VALUES (1, 0, 4183, 'AH Bloemkool')")
        with closing(db(ah_orders)) as conn:
            row = dict(conn.execute("SELECT * FROM ah_order_items").fetchone())
        self.assertEqual((row["title"], row["regular_price"], row["regular_checked_at"]), ("AH Bloemkool", None, None))
        run(in_db(ah_orders._connect, lambda conn: stale_columns(conn, "ah_order_items", (("regular_price", "REAL"),))))

    def test_the_async_connection_gives_an_old_database_the_same_columns(self) -> None:
        with closing(db()) as conn, conn:
            conn.execute("DROP TABLE IF EXISTS ah_receipt_products")
            conn.execute("CREATE TABLE ah_receipt_products (receipt_product_id INTEGER PRIMARY KEY, product_id INTEGER, title TEXT, checked_at TEXT NOT NULL)")
            conn.execute("DROP TABLE IF EXISTS ah_order_items")
            conn.execute("CREATE TABLE ah_order_items (order_id INTEGER NOT NULL, position INTEGER NOT NULL, product_id INTEGER, title TEXT)")
            conn.execute("DROP TABLE IF EXISTS product_links")

        async def columns(conn: aiosqlite.Connection) -> dict[str, set[str]]:
            return {
                table: {row["name"] for row in await conn.execute_fetchall(f"PRAGMA table_info({table})")}
                for table in ("ah_receipt_products", "ah_order_items", "ah_receipt_items", "product_links")
            }

        found = run(in_db(ah_orders._connect, columns))[0]
        self.assertIn("image", found["ah_receipt_products"])
        for table in ("ah_order_items", "ah_receipt_items"):
            self.assertLessEqual({"regular_price", "regular_checked_at"}, found[table], table)
        self.assertTrue(found["product_links"], "de tabel van je eigen koppelingen")
        folded = run(in_db(ah_orders._connect, lambda conn: auth.fetchone(conn, "SELECT ms_fold('AH Crème')")))[0][0]
        self.assertEqual(folded, product_links.fold("AH Crème"), "ms_fold staat op de verbinding")

    def test_jumbo_order_lines_gain_the_regular_price_columns(self):
        from mealieshopper import jumbo_orders

        with closing(db(jumbo_orders)) as conn:
            names = {row["name"] for row in conn.execute("PRAGMA table_info(jumbo_order_items)")}
        self.assertLessEqual({"regular_price", "regular_checked_at"}, names)

    async def test_linking_keeps_the_product_image_and_the_receipt_shows_it(self):
        await self.sync([[{"id": "R1", "dateTime": "2026-10-10T09:00:00", "total": 2.58}]])
        with closing(db(ah_orders)) as conn, conn:
            conn.execute("DELETE FROM ah_receipt_products")
        self.search_hq_ids.return_value = [(1, 4183, "AH Halfvolle melk 1 l", "https://static.ah.nl/dam/product/x?rendition=800x800_WEBP")]
        await self.client.post("/api/ah/receipts/R1/products")
        line = (await self.client.get("/api/ah/receipts/R1")).get_json()["items"][0]
        self.assertEqual(line["product"], {"id": 4183, "title": "AH Halfvolle melk 1 l", "image": "https://static.ah.nl/dam/product/x?rendition=800x800_WEBP"})

    def test_old_links_without_an_image_are_filled_in_batches_of_25(self):
        with closing(db(ah_orders)) as conn, conn:
            conn.executemany("INSERT INTO ah_receipt_products (receipt_product_id, product_id, title, checked_at) VALUES (?, ?, ?, 'x')", [(n, 1000 + n, f"P{n}") for n in range(30)])
            conn.execute("INSERT INTO ah_receipt_products (receipt_product_id, product_id, title, checked_at, image, unit_size) VALUES (99, 1099, 'P99', 'x', 'https://static.ah.nl/done', '1 l')")
            conn.execute("INSERT INTO ah_receipt_products (receipt_product_id, product_id, title, checked_at) VALUES (98, NULL, NULL, 'x')")
        with mock.patch.object(ah, "get_products_by_ids", side_effect=lambda ids: [{"id": i, "title": f"P{i - 1000}", "image": f"https://static.ah.nl/{i}"} for i in ids if i != 1005]) as lookup:
            result = run(ah_orders.backfill_receipt_images("2026-11-04T03:00:00+00:00"))
        self.assertEqual([len(call.args[0]) for call in lookup.call_args_list], [25, 5])
        self.assertEqual(result, {"products": 29, "requests": 2, "failed": 0})
        with closing(db(ah_orders)) as conn:
            self.assertEqual(conn.execute("SELECT image FROM ah_receipt_products WHERE product_id = 1005").fetchone()[0], "")
            self.assertEqual(conn.execute("SELECT image FROM ah_receipt_products WHERE product_id = 1007").fetchone()[0], "https://static.ah.nl/1007")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM ah_receipt_products WHERE image IS NULL AND product_id IS NOT NULL").fetchone()[0], 0)
        with mock.patch.object(ah, "get_products_by_ids") as lookup:
            self.assertEqual(run(ah_orders.backfill_receipt_images("2026-11-04T04:00:00+00:00")), {"products": 0, "requests": 0, "failed": 0})
        lookup.assert_not_called()

    def test_the_backfill_shares_the_pause_and_the_hourly_budget(self):
        with closing(db(ah_orders)) as conn, conn:
            conn.execute("INSERT INTO ah_receipt_products (receipt_product_id, product_id, title, checked_at) VALUES (1, 4183, 'P', 'x')")
        with mock.patch.object(ah, "get_products_by_ids", side_effect=ah.AhHttpError("te veel", 429)):
            result = run(ah_orders.backfill_receipt_images("2026-11-04T03:00:00+00:00"))
        self.assertEqual((result["failed"], result["requests"], result["pausedUntil"]), (1, 1, "2026-11-04T15:00:00+00:00"))
        with closing(db(ah_orders)) as conn, conn:
            conn.execute("INSERT INTO ah_receipts (receipt_id, date_time, total, details_synced, first_seen) VALUES ('R9', '2026-10-01', 0, 1, 'x')")
            conn.execute("INSERT INTO ah_receipt_items (receipt_id, position, product_id, name, quantity, price, amount) VALUES ('R9', 0, 20960, 'BLOEMKOOL', 1, 1.0, 1.0)")
        self.search_hq_ids.reset_mock()
        self.assertEqual(run(ah_orders.resolve_receipt_products("2026-11-04T04:00:00+00:00"))["pausedUntil"], "2026-11-04T15:00:00+00:00")
        self.search_hq_ids.assert_not_called()
        with mock.patch.object(ah, "get_products_by_ids") as lookup:
            self.assertEqual(run(ah_orders.backfill_receipt_images("2026-11-04T04:00:00+00:00"))["pausedUntil"], "2026-11-04T15:00:00+00:00")
        lookup.assert_not_called()

    async def test_a_failing_lookup_still_returns_the_receipt(self):
        await self.sync([[{"id": "R1", "dateTime": "2026-10-10T09:00:00", "total": 2.58}]])
        with mock.patch.object(ah_orders, "resolve_receipt_names", new_callable=mock.AsyncMock, side_effect=RuntimeError("kapot")):
            reply = await self.client.post("/api/ah/receipts/R1/products")
        self.assertEqual(reply.status_code, 200)
        self.assertEqual((reply.get_json()["items"][0]["name"], reply.get_json()["products"]), ("AH Halfvolle melk", None))


class DetailEnrichmentTest(AhOrdersTestCase):
    async def store_order(self, order_id, day, items, kind="delivered", state_text=None):
        await in_db(ah_orders._connect, lambda conn: ah_orders._save_order(conn, {"id": order_id, "deliveryDate": day, "stateText": state_text}, kind, items))

    async def test_an_order_detail_carries_category_discount_and_times_before(self):
        # Bestelling A op 2026-09-01 met product 1 voor de gewone prijs: die is de gewone prijs van 30 dagen bij bestelling B.
        await self.store_order(1, "2026-09-01", [{"productId": 1, "title": "AH Bananen", "quantity": 1, "price": 1.19, "priceBeforeBonus": 1.19, "taxonomy": "Fruit, verse sappen"}])
        await self.store_order(2, "2026-09-21", [{"productId": 1, "title": "AH Bananen", "quantity": 2, "price": 0.89, "priceBeforeBonus": 1.39, "bonus": True, "taxonomy": "Fruit, verse sappen"}])
        order = (await self.client.get("/api/ah/orders/2")).get_json()
        item = order["items"][0]
        self.assertEqual((item["category"], item["regularPrice"], item["discount"], item["discountSource"], item["timesBefore"]), ("groente_fruit", 1.19, 25, "history", 1))
        self.assertEqual((order["saved"], order["savedPercent"], order["statusKind"]), (0.6, 25, "delivered"))
        self.assertNotIn("status", order)  # de nieuwe status staat naast de bestaande velden, niet in de plaats ervan
        self.assertEqual((item["priceBeforeBonus"], item["paidEach"], order["statusText"]), (1.39, 0.89, None))  # de bestaande velden blijven

    async def test_a_line_with_a_product_but_no_quantity_keeps_its_count_but_gets_no_discount(self):
        # Een vervangen of geschrapte regel van AH: aantal 0. Eerder gekocht volgt het product, de korting en het bespaarde niet.
        bought = {"productId": 1, "title": "AH Komkommer", "quantity": 1, "price": 0.85, "taxonomy": ""}
        await self.store_order(1, "2026-09-01", [bought])
        await self.store_order(2, "2026-09-08", [bought])
        await self.store_order(3, "2026-09-21", [{"productId": 1, "title": "AH Komkommer", "quantity": 0, "price": 0.85, "priceBeforeBonus": 1.0, "bonus": True, "taxonomy": ""}],
                         kind="open", state_text="Ingepland")
        response = await self.client.get("/api/ah/orders/3")
        self.assertEqual(response.status_code, 200)
        order = response.get_json()
        item = order["items"][0]
        self.assertEqual((item["timesBefore"], item["discount"], item["category"]), (2, None, "groente_fruit"))
        self.assertEqual((order["saved"], order["savedPercent"], order["statusKind"], order["statusText"]), (0.0, None, "planned", "Ingepland"))

    async def test_a_line_without_a_product_gets_no_count_and_its_category_from_the_title(self):
        await self.store_order(1, "2026-09-01", [{"productId": None, "title": "AH Komkommer", "quantity": 1, "price": 0.85, "taxonomy": ""}])
        await self.store_order(2, "2026-09-21", [{"productId": None, "title": "AH Komkommer", "quantity": 2, "price": 0.85, "priceBeforeBonus": 1.0, "bonus": True, "taxonomy": ""}])
        order = (await self.client.get("/api/ah/orders/2")).get_json()
        item = order["items"][0]
        self.assertEqual((item["timesBefore"], item["category"]), (0, "groente_fruit"))
        # Zonder product geen gewone prijs van 30 dagen: de korting is die tegen de prijs ervoor van AH.
        self.assertEqual((item["regularPrice"], item["discountSource"], item["discount"], order["saved"]), (1.0, "store", 15, 0.3))

    async def test_a_failing_regular_price_fill_still_shows_the_order(self):
        from mealieshopper import prices

        await self.store_order(1, "2026-09-21", [{"productId": 1, "title": "AH Bananen", "quantity": 2, "price": 0.89, "priceBeforeBonus": 1.39, "bonus": True, "taxonomy": ""}])
        with mock.patch.object(prices, "fill_regular_prices", new_callable=mock.AsyncMock, side_effect=RuntimeError("kapot")), self.assertLogs("mealieshopper.purchase_lines", "WARNING"):
            response = await self.client.get("/api/ah/orders/1")
        self.assertEqual(response.status_code, 200)
        item = response.get_json()["items"][0]
        self.assertEqual((item["regularPrice"], item["discountSource"], item["discount"]), (1.39, "store", 36))  # de prijs ervoor van AH

    async def test_a_receipt_detail_matches_its_bonus_lines_and_sums_the_saving(self):
        with closing(db(ah_orders)) as conn, conn:
            conn.execute(
                "INSERT INTO ah_receipts (receipt_id, date_time, total, details_synced, discounts, payments, first_seen) VALUES ('R1', '2026-09-21T10:00:00', 0, 1, ?, '[]', 'x')",
                (json.dumps([{"name": "AH ROOMKAAS", "amount": -3.97}]),),
            )
            conn.execute("INSERT INTO ah_receipt_items (receipt_id, position, product_id, name, quantity, price, amount) VALUES ('R1', 0, 555, 'AH ROOMKAAS', 4, 0.99, 3.96)")
            conn.execute("INSERT INTO ah_receipt_items (receipt_id, position, product_id, name, quantity, price, amount) VALUES ('R1', 1, 556, 'BROOD', 1, 2.0, 2.0)")
        receipt = (await self.client.get("/api/ah/receipts/R1")).get_json()
        first, second = receipt["items"]
        self.assertEqual((first["bonus"], first["paidEach"], first["discount"], first["category"]), (3.96, 0.0, 100, "kaas_vleeswaren"))
        self.assertEqual((second["bonus"], second["paidEach"], second["regularPrice"], second["discount"]), (0.0, 2.0, None, None))
        # De bonus is groter dan de regel (4 x 0.99 = 3.96): de regel krijgt 3.96, de cent erboven telt alleen in het totaal.
        self.assertEqual((receipt["saved"], receipt["statusKind"], receipt["statusText"]), (3.97, "receipt", None))
        self.assertEqual(receipt["discounts"], [{"name": "AH ROOMKAAS", "amount": -3.97}])  # de bestaande velden blijven

    async def test_the_receipt_list_and_detail_show_the_same_saving(self):
        # Een bonus groter dan zijn regel en regels zonder prijs (AH zet bij één stuk alleen het bedrag op de bon): de lijst en de
        # detailroute (en dus de hover) moeten dezelfde som geven.
        with closing(db(ah_orders)) as conn, conn:
            conn.execute(
                "INSERT INTO ah_receipts (receipt_id, date_time, total, details_synced, discounts, payments, first_seen) VALUES ('R1', '2026-09-21T10:00:00', 0, 1, ?, '[]', 'x')",
                (json.dumps([{"name": "FOCACCIA", "amount": -4.36}, {"name": "PERLAHUISBLE", "amount": -1.0}]),),
            )
            conn.execute("INSERT INTO ah_receipt_items (receipt_id, position, product_id, name, quantity, price, amount) VALUES ('R1', 0, 555, 'FOCACCIA', 1, NULL, 2.59)")
            conn.execute("INSERT INTO ah_receipt_items (receipt_id, position, product_id, name, quantity, price, amount) VALUES ('R1', 1, 556, 'KAAS', 1, NULL, 3.0)")
            conn.execute("INSERT INTO ah_receipt_items (receipt_id, position, product_id, name, quantity, price, amount) VALUES ('R1', 2, 557, 'MELK', 2, 1.0, 2.0)")
            # De gewone prijs van 30 dagen, al bepaald (zodat het openen hem niet opnieuw zoekt).
            conn.execute("UPDATE ah_receipt_items SET regular_price = 3.5, regular_checked_at = '9999-12-31T00:00:00' WHERE position = 1")
            conn.execute("UPDATE ah_receipt_items SET regular_price = 1.25, regular_checked_at = '9999-12-31T00:00:00' WHERE position = 2")
        detail = (await self.client.get("/api/ah/receipts/R1")).get_json()
        listed = (await self.client.get("/api/ah/receipts")).get_json()["receipts"][0]
        # 4.36 + 1.00 van de bonusregels, (3.50 - 3.00) voor de kaas zonder prijs en (1.25 - 1.00) x 2 voor de melk.
        self.assertEqual((detail["saved"], listed["savings"]), (6.36, 6.36))

    async def test_a_bonus_line_that_matches_no_receipt_line_still_counts_in_the_saving(self):
        with closing(db(ah_orders)) as conn, conn:
            conn.execute(
                "INSERT INTO ah_receipts (receipt_id, date_time, total, details_synced, discounts, payments, first_seen) VALUES ('R1', '2026-09-21T10:00:00', 0, 1, ?, '[]', 'x')",
                (json.dumps([{"name": "PERLAHUISBLE", "amount": -17.97}]),),
            )
            conn.execute("INSERT INTO ah_receipt_items (receipt_id, position, product_id, name, quantity, price, amount) VALUES ('R1', 0, 556, 'BROOD', 1, 2.0, 2.0)")
            conn.execute("INSERT INTO ah_receipt_items (receipt_id, position, product_id, name, quantity, price, amount) VALUES ('R1', 1, 557, 'WASMIDDEL', 2, 19.0, 38.0)")
        receipt = (await self.client.get("/api/ah/receipts/R1")).get_json()
        self.assertEqual([(item["bonus"], item["discount"]) for item in receipt["items"]], [(0.0, None), (0.0, None)])
        # 17.97 van 40.00 zonder korting: 45%.
        self.assertEqual((receipt["saved"], receipt["savedPercent"]), (17.97, 45))

    async def test_a_linked_receipt_line_uses_the_regular_price_of_its_webshop_product(self):
        await self.store_order(1, "2026-09-01", [{"productId": 4183, "title": "AH Roomkaas", "quantity": 1, "price": 1.19, "priceBeforeBonus": 1.19, "taxonomy": "Kaas"}])
        with closing(db(ah_orders)) as conn, conn:
            conn.execute("INSERT INTO ah_receipts (receipt_id, date_time, total, details_synced, discounts, payments, first_seen) VALUES ('R1', '2026-09-21T10:00:00', 0, 1, '[]', '[]', 'x')")
            conn.execute("INSERT INTO ah_receipt_items (receipt_id, position, product_id, name, quantity, price, amount) VALUES ('R1', 0, 555, 'AH ROOMKAAS', 1, 0.89, 0.89)")
            conn.execute("INSERT INTO ah_receipt_products (receipt_product_id, product_id, title, checked_at) VALUES (555, 4183, 'AH Roomkaas', 'x')")
        receipt = (await self.client.get("/api/ah/receipts/R1")).get_json()
        item = receipt["items"][0]
        self.assertEqual((item["regularPrice"], item["discountSource"], item["discount"], item["timesBefore"]), (1.19, "history", 25, 1))
        self.assertEqual((receipt["saved"], receipt["savedPercent"]), (0.3, 25))


class ReceiptProductsTest(AhOrdersTestCase):
    """Het id op een bon is AH's artikelnummer (hqId), niet het id van de webwinkel (issue #178)."""

    def receipt(self, receipt_id, *lines):
        with closing(db(ah_orders)) as conn, conn:
            conn.execute("INSERT INTO ah_receipts (receipt_id, date_time, total, details_synced, first_seen) VALUES (?, '2026-10-01', 0, 1, 'x')", (receipt_id,))
            conn.executemany(
                "INSERT INTO ah_receipt_items (receipt_id, position, product_id, name, quantity, price, amount) VALUES (?, ?, ?, ?, 1, 1.0, 1.0)",
                [(receipt_id, position, product_id, name) for position, (product_id, name) in enumerate(lines)],
            )

    def links(self):
        with closing(db(ah_orders)) as conn:
            return {row["receipt_product_id"]: row["product_id"] for row in conn.execute("SELECT * FROM ah_receipt_products")}

    def test_only_a_result_with_the_same_article_number_is_linked(self):
        self.receipt("R1", (20960, "BLOEMKOOL"), (20961, "BROCCOLI"))
        self.search_hq_ids.side_effect = lambda name: {
            "BLOEMKOOL": [(532357, 186629, "AH Biologisch Bloemkool", ""), (20960, 4183, "AH Bloemkool", "")],
            "BROCCOLI": [(999, 1, "AH Broccoli rijst", "")],
        }[name]
        result = run(ah_orders.resolve_receipt_products("2026-10-02T00:00:00+00:00"))
        self.assertEqual(result, {"found": 1, "missing": 1, "failed": 0, "searches": 2})
        self.assertEqual(self.links(), {20960: 4183, 532357: 186629, 999: 1, 20961: None}, "andere resultaten bewaren we ook")

    def test_a_missing_article_number_waits_before_it_is_searched_again(self):
        self.receipt("R1", (20961, "BROCCOLI"))
        run(ah_orders.resolve_receipt_products("2026-10-02T00:00:00+00:00"))
        run(ah_orders.resolve_receipt_products("2026-10-20T00:00:00+00:00"))
        self.assertEqual(self.search_hq_ids.call_count, 1)
        self.search_hq_ids.return_value = [(20961, 210154, "AH Broccoli", "")]
        run(ah_orders.resolve_receipt_products("2026-11-02T00:00:00+00:00"))
        self.assertEqual((self.search_hq_ids.call_count, self.links()), (2, {20961: 210154}))

    def test_an_article_number_found_by_another_search_is_not_searched(self):
        self.receipt("R1", (20960, "BLOEMKOOL"), (20960, "BLOEMKOOL"), (4711, "AH BLOEMKOOLROOSJES"))
        self.search_hq_ids.return_value = [(20960, 4183, "AH Bloemkool", ""), (4711, 121475, "AH Bloemkoolroosjes", "")]
        result = run(ah_orders.resolve_receipt_products("2026-10-02T00:00:00+00:00"))
        self.assertEqual((result["found"], self.search_hq_ids.call_args_list), (2, [mock.call("BLOEMKOOL")]), "de vaakst gekochte eerst")
        run(ah_orders.resolve_receipt_products("2026-10-03T00:00:00+00:00"))
        self.assertEqual(self.search_hq_ids.call_count, 1, "alles al gekoppeld")

    def test_lines_without_an_article_number_are_left_alone(self):
        self.receipt("R1", (None, "AH VERSE KNOFLOOK"), (0, "DRAAGTAS"))
        self.assertEqual(run(ah_orders.resolve_receipt_products()), {"found": 0, "missing": 0, "failed": 0, "searches": 0})
        self.search_hq_ids.assert_not_called()

    def test_a_failing_search_is_retried_next_time(self):
        self.receipt("R1", (20960, "BLOEMKOOL"))
        self.search_hq_ids.side_effect = RuntimeError("503")
        self.assertEqual(run(ah_orders.resolve_receipt_products())["failed"], 1)
        self.assertEqual(self.links(), {})

    def test_the_receipts_sync_links_and_never_fails_on_it(self):
        detail = {"id": "R1", "items": [{"productId": 20960, "name": "BLOEMKOOL", "quantity": 1, "price": 1.99, "amount": 1.99}], "discounts": [], "payments": []}
        with mock.patch.object(ah, "get_receipts", return_value=[{"id": "R1", "dateTime": "2026-10-10T09:00:00", "total": 1.99}]), mock.patch.object(ah, "get_receipt", return_value=detail):
            self.search_hq_ids.return_value = [(20960, 4183, "AH Bloemkool", "")]
            self.assertEqual(run(ah_orders.sync_receipts())["products"], {"found": 1, "missing": 0, "failed": 0, "searches": 1})
            with mock.patch.object(ah_orders, "resolve_receipt_products", new_callable=mock.AsyncMock, side_effect=RuntimeError("kapot")):
                self.assertEqual(run(ah_orders.sync_receipts())["failed"], 0)


class ReceiptProductsPolitenessTest(AhOrdersTestCase):
    """Zoeken per naam op de bon, met een tweede poging, en zuinig met AH (rate limiting en firewall)."""

    def receipt(self, receipt_id, *lines):
        with closing(db(ah_orders)) as conn, conn:
            conn.execute("INSERT INTO ah_receipts (receipt_id, date_time, total, details_synced, first_seen) VALUES (?, '2026-10-01', 0, 1, 'x')", (receipt_id,))
            conn.executemany(
                "INSERT INTO ah_receipt_items (receipt_id, position, product_id, name, quantity, price, amount) VALUES (?, ?, ?, ?, 1, 1.0, 1.0)",
                [(receipt_id, position, product_id, name) for position, (product_id, name) in enumerate(lines)],
            )

    def links(self):
        with closing(db(ah_orders)) as conn:
            return {row["receipt_product_id"]: row["product_id"] for row in conn.execute("SELECT * FROM ah_receipt_products")}

    def test_one_search_per_name_on_the_receipt(self):
        # "REDBAND" en "RED BAND" staan op de bon bij verschillende artikelnummers: een zoekopdracht per naam (spaties tellen niet).
        self.receipt("R1", (501520, "REDBAND"), (854572, "REDBAND"), (77234, "RED BAND"))
        self.search_hq_ids.return_value = [(501520, 88624, "Red Band Schatkistjes", ""), (854572, 410055, "Red Band Zoete aardbeien", ""),
                                           (77234, 547751, "Red Band Winegums original", "")]
        result = run(ah_orders.resolve_receipt_products("2026-11-03T12:00:00+00:00"))
        self.assertEqual((result["found"], result["searches"]), (3, 1))
        self.assertEqual(self.search_hq_ids.call_count, 1, "REDBAND en RED BAND zijn een naam")

    def test_an_abbreviated_name_gets_a_second_try_with_its_longest_word(self):
        self.receipt("R1", (511669, "RB PICOLIENT"))
        self.search_hq_ids.side_effect = lambda query: {"PICOLIENT": [(511669, 4242, "Red Band Picolient", "")]}.get(query, [])
        result = run(ah_orders.resolve_receipt_products("2026-11-03T12:00:00+00:00"))
        self.assertEqual([c.args[0] for c in self.search_hq_ids.call_args_list], ["RB PICOLIENT", "PICOLIENT"])
        self.assertEqual((result["found"], self.links()), (1, {511669: 4242}))

    def test_searches_are_paced_and_capped(self):
        words = ["APPELS", "PEREN", "BANANEN", "DRUIVEN", "KIWIS", "MANGO", "PRUIMEN", "CITROEN", "MELOEN", "ANANAS"]
        self.receipt("R1", *[(1000 + n, word) for n, word in enumerate(words)])
        result = run(ah_orders.resolve_receipt_products("2026-11-03T12:00:00+00:00", searches=4))
        self.assertEqual((result["searches"], self.search_hq_ids.call_count), (4, 4))
        pauses = [c.args[0] for c in self.sleep.call_args_list]
        self.assertEqual(len(pauses), 3, "een pauze tussen elke twee")
        self.assertTrue(all(0 < pause <= ah_orders.RECEIPT_PRODUCTS_PAUSE_SECONDS for pause in pauses), pauses)
        self.assertEqual(len(self.links()), 4, "wat niet aan de beurt kwam, blijft open voor de volgende keer")

    def test_a_403_or_429_from_ah_stops_and_waits_hours(self):
        for status in (429, 403):
            with self.subTest(status=status):
                run(ah_orders.auth.set_setting(ah_orders.SETTING_RECEIPT_PRODUCTS_PAUSED, ""))
                self.receipt(f"R{status}", (status, "BLOEMKOOL"), (status + 1, "BROCCOLI"))
                self.search_hq_ids.reset_mock()
                self.search_hq_ids.side_effect = ah.AhHttpError("AH productzoekopdracht mislukt", status)
                result = run(ah_orders.resolve_receipt_products("2026-11-03T12:00:00+00:00"))
                self.assertEqual((self.search_hq_ids.call_count, result["pausedUntil"]), (1, "2026-11-04T00:00:00+00:00"))
                self.search_hq_ids.side_effect = None
                later = run(ah_orders.resolve_receipt_products("2026-11-03T20:00:00+00:00"))
                self.assertEqual((self.search_hq_ids.call_count, later["searches"]), (1, 0), "tijdens de pauze niet opnieuw")
                after = run(ah_orders.resolve_receipt_products("2026-11-04T01:00:00+00:00"))
                self.assertGreater(after["searches"], 0)

    def test_misses_from_the_old_way_of_searching_get_one_new_try(self):
        self.receipt("R1", (511669, "RB PICOLIENT"))
        with closing(db(ah_orders)) as conn, conn:
            conn.execute("INSERT INTO ah_receipt_products (receipt_product_id, product_id, title, checked_at) VALUES (511669, NULL, NULL, '2026-11-03T02:56:36+00:00')")
        run(ah_orders.resolve_receipt_products("2026-11-03T12:00:00+00:00"))
        self.assertEqual(self.search_hq_ids.call_count, 2, "naam en langste woord")
        run(ah_orders.resolve_receipt_products("2026-11-03T13:00:00+00:00"))
        self.assertEqual(self.search_hq_ids.call_count, 2, "maar een keer: daarna weer na 30 dagen")

    def test_the_pause_holds_across_runs_and_there_is_an_hourly_budget(self):
        self.receipt("R1", (1, "APPELS"), (2, "PEREN"), (3, "BANANEN"))
        run(ah_orders.resolve_receipt_products("2026-11-03T12:00:00+00:00", searches=1))
        run(ah_orders.resolve_receipt_products("2026-11-03T12:00:00+00:00", searches=1))
        self.assertEqual(self.sleep.call_count, 1, "ook tussen twee losse runs een pauze")
        with mock.patch.object(ah_orders, "RECEIPT_PRODUCTS_PER_HOUR", 2):
            result = run(ah_orders.resolve_receipt_products("2026-11-03T12:00:00+00:00"))
        self.assertEqual((result.get("limited"), self.search_hq_ids.call_count), (True, 2), "het uurbudget is op")

    def test_names_from_a_search_are_looked_up_right_away(self):
        self.receipt("R1", (501520, "REDBAND"), (77234, "RED BAND"), (20960, "BLOEMKOOL"))
        self.search_hq_ids.return_value = [(501520, 88624, "Red Band Schatkistjes", ""), (77234, 547751, "Red Band Winegums original", "")]
        result = run(ah_orders.resolve_receipt_names(["REDBAND", "RED BAND"]))
        self.assertEqual((result["found"], self.search_hq_ids.call_count), (2, 1), "alleen deze namen, een zoekopdracht")
        self.assertIn(self.search_hq_ids.call_args.args[0], ("REDBAND", "RED BAND"))
        self.assertEqual(self.links(), {501520: 88624, 77234: 547751})
        self.assertEqual(run(ah_orders.resolve_receipt_names(["REDBAND"]))["searches"], 0, "al gekoppeld: niet opnieuw")
        self.assertEqual(run(ah_orders.resolve_receipt_names([" "]))["searches"], 0)

    def test_the_nightly_task_links_more_but_only_with_receipts(self):
        self.assertIsNone(run(ah_orders.resolve_receipt_products_nightly()))
        self.receipt("R1", (20960, "BLOEMKOOL"))
        with mock.patch.object(ah_orders, "resolve_receipt_products", new_callable=mock.AsyncMock, return_value={"found": 1}) as resolve:
            self.assertEqual(run(ah_orders.resolve_receipt_products_nightly()), {"found": 1})
        resolve.assert_called_once_with(None, searches=ah_orders.RECEIPT_PRODUCTS_SEARCHES_NIGHTLY, deadline_seconds=ah_orders.RECEIPT_PRODUCTS_DEADLINE_NIGHTLY, trigger="nightly")
        with mock.patch.object(ah_orders, "resolve_receipt_products", new_callable=mock.AsyncMock, side_effect=RuntimeError("kapot")):
            self.assertIsNone(run(ah_orders.resolve_receipt_products_nightly()), "de nachtelijke taak gaat altijd door")


class LinkingStatusTest(AhOrdersTestCase):
    """De toestand van het koppelen (pauze, uurbudget, wat er nog open staat) en een ronde op afroep."""

    NOW = "2026-11-05T15:00:00+00:00"

    async def asyncSetUp(self):
        await super().asyncSetUp()
        with closing(db(ah_orders)) as conn, conn:
            conn.execute("INSERT INTO ah_receipts (receipt_id, date_time, total, details_synced, first_seen) VALUES ('R1', '2026-11-01', 0, 1, 'x')")
            conn.executemany(
                "INSERT INTO ah_receipt_items (receipt_id, position, product_id, name, quantity, price, amount) VALUES ('R1', ?, ?, ?, 1, 1.0, 1.0)",
                [(0, 555, "KOMKOMMER"), (1, 556, "TOMATEN")],
            )
        now = mock.patch.object(ah_orders, "_now", return_value=self.NOW)  # de pauze in de tests hangt niet af van de echte datum
        now.start()
        self.addCleanup(now.stop)

    async def live(self, scenario: Callable[[AppClient], Awaitable[T]], wait: bool = True) -> T:
        """Draait `scenario(client)` in een app die openblijft tot de ronde op afroep klaar is (met `wait`): de task van de
        ronde loopt in die app. Zonder `wait` sluit de app meteen en annuleert hij een ronde die nog loopt, zoals bij het afsluiten."""

        async with self.client.app.test_app() as test_app:
            result = await scenario(AppClient(test_app.test_client()))
            task = ah_orders._LINKING_TASK
            if wait and task is not None and not task.done():
                await asyncio.wait([task], timeout=5)
            return result

    async def post_round(self, path: str, **kwargs: Any) -> ReadResponse:
        """Een POST naar de ronde op afroep, en wachten tot die ronde klaar is."""
        return await self.live(lambda client: client.post(path, **kwargs))

    async def test_the_status_reports_pause_budget_todo_and_next_round(self):
        await auth.set_setting(ah_orders.SETTING_RECEIPT_PRODUCTS_PAUSED, "2026-11-06T02:02:00+00:00")
        await auth.set_setting(ah_orders.SETTING_RECEIPT_PRODUCTS_PAUSED_REASON, "429@2026-11-05T14:02:00+00:00")
        status = (await self.client.get("/api/ah/linking")).get_json()
        self.assertEqual((status["pausedUntil"], status["pausedSince"], status["pausedStatus"]), ("2026-11-06T02:02:00+00:00", "2026-11-05T14:02:00+00:00", 429))
        self.assertEqual(status["budget"]["limit"], ah_orders.RECEIPT_PRODUCTS_PER_HOUR)
        self.assertEqual(status["todo"], {"lines": 2, "receipts": 1, "images": 0})  # twee ongekoppelde regels op een bon in de fixture
        self.assertIsNone(status["running"])
        self.assertIsNotNone(status["nextRound"])
        self.assertGreater(status["nextRound"], self.NOW)

    async def test_a_receipt_counts_its_unlinked_lines_and_the_ones_that_are_due(self):
        # 555 zocht AH gisteren en vond hem niet: ongekoppeld, maar pas na RECEIPT_PRODUCTS_RECHECK_DAYS weer aan de beurt. 556 is nooit
        # opgezocht, 557 is gekoppeld, 558 werd 40 dagen geleden niet gevonden en is weer aan de beurt.
        with closing(db(ah_orders)) as conn, conn:
            conn.executemany(
                "INSERT INTO ah_receipt_items (receipt_id, position, product_id, name, quantity, price, amount) VALUES ('R1', ?, ?, ?, 1, 1.0, 1.0)",
                [(2, 557, "MELK"), (3, 558, "KAAS"), (4, None, "STATIEGELD")],
            )
            conn.executemany(
                "INSERT INTO ah_receipt_products (receipt_product_id, product_id, title, checked_at) VALUES (?, ?, ?, ?)",
                [(555, None, None, "2026-11-04T15:00:00+00:00"), (557, 4183, "AH Melk", "2026-11-04T15:00:00+00:00"), (558, None, None, "2026-09-26T15:00:00+00:00")],
            )
        todo = (await in_db(ah_orders._connect, lambda conn: ah_orders.receipt_linking_todo(conn, "R1", self.NOW)))[0]
        self.assertEqual(todo, {"unlinked": 3, "due": 2})
        receipt = (await self.client.get("/api/ah/receipts/R1")).get_json()
        self.assertEqual(receipt["linkingTodo"], {"unlinked": 3, "due": 2})

    def test_a_line_you_linked_yourself_is_linked_for_the_count_and_the_round(self):
        # 556 koppelde je zelf (#238): geen regel zonder product meer, en geen zoekopdracht bij AH. 555 staat nog open.
        run(product_links.save("ah", "TOMATEN", {"productId": "4711", "title": "AH Tomaten"}, article=556, now=self.NOW))
        todo, groups = run(in_db(
            ah_orders._connect,
            lambda conn: ah_orders.receipt_linking_todo(conn, "R1", self.NOW),
            lambda conn: ah_orders._todo_by_name(conn, self.NOW),
        ))
        self.assertEqual(todo, {"unlinked": 1, "due": 1})
        self.assertEqual([group["ids"] for group in groups], [[555]])
        self.assertEqual(run(ah_orders.linking_status())["todo"]["lines"], 1)

    def test_an_expired_pause_is_no_pause(self):
        run(auth.set_setting(ah_orders.SETTING_RECEIPT_PRODUCTS_PAUSED, "2026-11-05T02:02:00+00:00"))
        run(auth.set_setting(ah_orders.SETTING_RECEIPT_PRODUCTS_PAUSED_REASON, "403@2026-11-04T14:02:00+00:00"))
        status = run(ah_orders.linking_status())
        self.assertEqual((status["pausedUntil"], status["pausedSince"], status["pausedStatus"]), (None, None, None))

    def test_the_budget_counts_the_requests_of_the_last_hour(self):
        self.assertEqual(run(ah_orders.linking_status())["budget"], {"used": 0, "limit": ah_orders.RECEIPT_PRODUCTS_PER_HOUR, "resetAt": None})
        run(ah_orders._polite(lambda: returned(None)))
        run(ah_orders._polite(lambda: returned(None)))
        budget = run(ah_orders.linking_status())["budget"]
        self.assertEqual(budget["used"], 2)
        self.assertGreater(budget["resetAt"], "2026-11-05T15:59:00+00:00")
        self.assertLess(budget["resetAt"], "2026-11-05T16:01:00+00:00")

    def test_the_status_reads_the_budget_while_a_request_waits_its_turn(self) -> None:
        held: list[bool] = []
        self.sleep.side_effect = lambda seconds: held.append(ah_orders._lock("pace").locked())

        async def scenario() -> int:
            await ah_orders._polite(lambda: returned(None))
            await ah_orders._polite(lambda: returned(None))
            async with ah_orders._lock("pace"):  # een ronde die op zijn beurt wacht of een verzoek doet
                return (await asyncio.wait_for(ah_orders.linking_status(), 5))["budget"]["used"]

        self.assertEqual(run(scenario()), 2)
        self.assertEqual(held, [True], "de pauze hoort bij het slot: een tweede verzoek wacht er ook op")

    def test_requests_at_once_go_one_after_the_other_with_a_pause_between_each_two(self) -> None:
        events: list[str] = []

        async def pause(seconds: float) -> None:
            events.append("pauze")
            await asyncio.sleep(0)  # de pauze geeft de beurt aan wie er nog meer wacht

        self.sleep.side_effect = pause

        async def request(name: str) -> None:
            async def send() -> None:
                # Het verzoek zelf geeft de loop vrij, zoals een echt verzoek aan AH: het slot blijft toch vast tot het klaar is.
                events.append(name)
                await asyncio.sleep(0)
                events.append(f"{name} klaar")

            await ah_orders._polite(send)

        async def scenario() -> None:
            await asyncio.gather(request("eerste"), request("tweede"), request("derde"))

        run(scenario())
        self.assertEqual(
            events,
            ["eerste", "eerste klaar", "pauze", "tweede", "tweede klaar", "pauze", "derde", "derde klaar"],
            "de derde wacht tot de tweede klaar is",
        )
        self.assertTrue(all(0 < c.args[0] <= ah_orders.RECEIPT_PRODUCTS_PAUSE_SECONDS for c in self.sleep.call_args_list))

    async def test_polite_pauses_on_403_and_429_under_the_lock_and_reraises_the_same_error(self) -> None:
        # Wie op het slot wacht (een tweede ronde, de zoekronde van losse namen) moet de pauze zien voordat hij zijn verzoek stuurt.
        for status in (403, 429):
            await auth.set_setting(ah_orders.SETTING_RECEIPT_PRODUCTS_PAUSED, "")
            error = ah.AhHttpError(f"AH productzoekopdracht mislukt ({status})", status)
            seen: list[str | None] = []

            async def refused() -> None:
                raise error

            async def waiting() -> None:
                seen.append(await ah_orders.paused_until(self.NOW))

            queued = asyncio.ensure_future(ah_orders._polite(waiting))
            with self.assertRaises(ah.AhHttpError) as raised:
                await ah_orders._polite(refused)
            self.assertIs(raised.exception, error)
            await asyncio.wait_for(queued, 5)
            self.assertEqual(seen, ["2026-11-06T03:00:00+00:00"], "de wachtende zag de pauze al")
            self.assertEqual(await auth.get_setting(ah_orders.SETTING_RECEIPT_PRODUCTS_PAUSED_REASON), f"{status}@{self.NOW}")

    async def test_polite_sets_no_pause_on_other_errors(self) -> None:
        for error in (ah.AhHttpError("AH productzoekopdracht mislukt (500)", 500), RuntimeError("verbinding verbroken")):

            async def failing(error: Exception = error) -> None:
                raise error

            with self.assertRaises(type(error)) as raised:
                await ah_orders._polite(failing)
            self.assertIs(raised.exception, error)
            self.assertEqual(await auth.get_setting(ah_orders.SETTING_RECEIPT_PRODUCTS_PAUSED), "")
            self.assertIsNone(await ah_orders.paused_until(self.NOW))

    def test_a_403_records_the_reason_next_to_the_pause(self):
        with mock.patch.object(ah_orders, "_polite_search", new_callable=mock.AsyncMock, side_effect=ah.AhHttpError("AH productzoekopdracht mislukt", 403)):
            result = run(ah_orders.resolve_receipt_products("2026-11-05T14:02:00+00:00"))
        self.assertEqual(result["pausedUntil"], "2026-11-06T02:02:00+00:00")
        self.assertEqual(run(auth.get_setting(ah_orders.SETTING_RECEIPT_PRODUCTS_PAUSED_REASON)), "403@2026-11-05T14:02:00+00:00")
        run(auth.set_setting(ah_orders.SETTING_RECEIPT_PRODUCTS_PAUSED, ""))
        with closing(db(ah_orders)) as conn, conn:
            conn.execute("INSERT INTO ah_receipt_products (receipt_product_id, product_id, title, checked_at) VALUES (555, 1, 'AH Komkommer', 'x')")
        self.get_products_by_ids.side_effect = ah.AhHttpError("AH producten mislukt", 429)
        result = run(ah_orders.backfill_receipt_images("2026-11-05T14:30:00+00:00"))
        self.assertEqual(result["pausedUntil"], "2026-11-06T02:30:00+00:00")
        self.assertEqual(run(auth.get_setting(ah_orders.SETTING_RECEIPT_PRODUCTS_PAUSED_REASON)), "429@2026-11-05T14:30:00+00:00")

    def test_a_round_writes_the_last_round_with_linking_and_photos_merged(self):
        with closing(db(ah_orders)) as conn, conn:
            conn.execute("INSERT INTO ah_receipt_products (receipt_product_id, product_id, title, checked_at) VALUES (556, 2, 'AH Tomaten', 'x')")
        self.search_hq_ids.return_value = [(555, 1, "AH Komkommer", "")]
        self.get_products_by_ids.return_value = [{"id": 1, "image": "https://static.ah.nl/1.jpg"}, {"id": 2, "image": "https://static.ah.nl/2.jpg"}]
        run(ah_orders.resolve_receipt_products(self.NOW, trigger="manual"))
        run(ah_orders.backfill_receipt_images(self.NOW, trigger="manual"))
        last = json.loads(run(auth.get_setting(ah_orders.SETTING_LAST_ROUND)))
        self.assertEqual(last, {"at": self.NOW, "searches": 1, "found": 1, "missing": 0, "failed": 0, "images": 1, "trigger": "manual"})
        run(ah_orders.backfill_receipt_images("2026-11-05T16:00:00+00:00", trigger="list"))
        last = json.loads(run(auth.get_setting(ah_orders.SETTING_LAST_ROUND)))
        self.assertEqual((last["at"], last["trigger"], last["searches"], last["images"]), ("2026-11-05T16:00:00+00:00", "list", 0, 0), "een nieuwe ronde begint opnieuw")
        self.assertEqual(run(ah_orders.linking_status())["lastRound"], last)

    def test_the_nightly_linking_and_photos_land_in_one_last_round(self):
        with closing(db(ah_orders)) as conn, conn:
            conn.execute("INSERT INTO ah_receipt_products (receipt_product_id, product_id, title, checked_at) VALUES (556, 2, 'AH Tomaten', 'x')")
        self.search_hq_ids.return_value = [(555, 1, "AH Komkommer", "")]
        self.get_products_by_ids.return_value = [{"id": 2, "image": "https://static.ah.nl/2.jpg"}]
        night = "2026-11-05T05:00:00+00:00"
        run(ah_orders.resolve_receipt_products_nightly(night))
        run(ah_orders.backfill_receipt_images_nightly(night))
        last = json.loads(run(auth.get_setting(ah_orders.SETTING_LAST_ROUND)))
        self.assertEqual(last, {"at": night, "searches": 1, "found": 1, "missing": 0, "failed": 0, "images": 1, "trigger": "nightly"})

    def test_a_paused_round_does_not_overwrite_the_last_round(self):
        run(auth.set_setting(ah_orders.SETTING_LAST_ROUND, json.dumps({"at": "2026-11-05T02:00:00+00:00", "found": 3})))
        run(auth.set_setting(ah_orders.SETTING_RECEIPT_PRODUCTS_PAUSED, "2026-11-06T02:02:00+00:00"))
        run(ah_orders.resolve_receipt_products(self.NOW))
        run(ah_orders.backfill_receipt_images(self.NOW))
        self.assertEqual(json.loads(run(auth.get_setting(ah_orders.SETTING_LAST_ROUND)))["found"], 3)

    async def test_a_round_on_demand_runs_as_a_task_and_writes_the_last_round(self) -> None:
        with mock.patch.object(ah_orders, "_polite_search", new_callable=mock.AsyncMock, return_value=[(555, 1, "AH Komkommer", "https://static.ah.nl/x.jpg")]):
            response = await self.post_round("/api/ah/linking/run", json={"receiptId": "R1"})
        self.assertEqual(response.status_code, 202)
        self.assertEqual(ah_orders._LINKING_TASK.get_name(), "linking-run")
        last = json.loads(await auth.get_setting(ah_orders.SETTING_LAST_ROUND))
        self.assertEqual((last["found"], last["trigger"]), (1, "receipt"))
        self.assertEqual((await ah_orders.linking_status())["todo"]["lines"], 0, "TOMATEN is gezocht en niet gevonden: die wacht 30 dagen")

    async def test_a_round_on_demand_for_a_receipt_links_only_that_receipt(self):
        with closing(db(ah_orders)) as conn, conn:
            conn.execute("INSERT INTO ah_receipts (receipt_id, date_time, total, details_synced, first_seen) VALUES ('R2', '2026-11-02', 0, 1, 'x')")
            conn.execute("INSERT INTO ah_receipt_items (receipt_id, position, product_id, name, quantity, price, amount) VALUES ('R2', 0, 777, 'AH BLOEMKOOL', 1, 1.0, 1.0)")
            conn.execute("INSERT INTO ah_receipt_items (receipt_id, position, product_id, name, quantity, price, amount) VALUES ('R2', 1, 888, 'AH PREI', 1, 1.0, 1.0)")
            conn.execute("INSERT INTO ah_receipt_products (receipt_product_id, product_id, title, checked_at) VALUES (556, 2, 'AH Tomaten', 'x')")  # gekoppeld, nog geen foto
            conn.execute("INSERT INTO ah_receipt_products (receipt_product_id, product_id, title, checked_at) VALUES (888, 3, 'AH Prei', 'x')")  # op een andere bon
        with mock.patch.object(ah_orders, "_polite_search", return_value=[]) as search, mock.patch.object(ah_orders, "backfill_receipt_images", new_callable=mock.AsyncMock, return_value={"products": 0}) as images:
            await self.post_round("/api/ah/linking/run", json={"receiptId": "R1"})
        self.assertEqual([c.args[0] for c in search.call_args_list], ["KOMKOMMER"], "alleen de ongekoppelde regel van deze bon")
        self.assertEqual((images.call_args.kwargs["batches"], images.call_args.kwargs["only"]), (2, {2}), "alleen de foto's van deze bon")

    async def test_a_round_on_demand_for_a_receipt_gets_every_search_its_names_need(self):
        with closing(db(ah_orders)) as conn, conn:
            conn.execute("UPDATE ah_receipt_items SET name = 'RB PICOLIENT' WHERE product_id = 555")
        with mock.patch.object(ah_orders, "_polite_search", new_callable=mock.AsyncMock, return_value=[]) as search:
            await self.post_round("/api/ah/linking/run", json={"receiptId": "R1"})
        self.assertEqual(sorted(c.args[0] for c in search.call_args_list), ["PICOLIENT", "RB PICOLIENT", "TOMATEN"], "ook de tweede poging met het langste woord")

    async def test_a_round_on_demand_without_a_receipt_runs_the_nightly_round(self):
        with mock.patch.object(ah_orders, "resolve_receipt_products", new_callable=mock.AsyncMock, return_value={"found": 0}) as resolve, \
                mock.patch.object(ah_orders, "backfill_receipt_images", new_callable=mock.AsyncMock, return_value={"products": 0}) as images, \
                mock.patch.object(ah_orders.audit, "record", new_callable=mock.AsyncMock) as record:
            self.assertEqual((await self.post_round("/api/ah/linking/run", json={})).status_code, 202)
        self.assertEqual(resolve.call_args.kwargs["searches"], ah_orders.RECEIPT_PRODUCTS_SEARCHES_NIGHTLY)
        self.assertEqual(resolve.call_args.kwargs["deadline_seconds"], ah_orders.RECEIPT_PRODUCTS_DEADLINE_NIGHTLY)
        self.assertEqual(resolve.call_args.kwargs["trigger"], "manual")
        self.assertEqual(images.call_args.kwargs["trigger"], "manual")
        self.assertEqual((record.call_args.kwargs["kind"], record.call_args.kwargs["ok"]), ("task", True))
        self.assertEqual(record.call_args.kwargs["detail"]["products"], {"found": 0})

    async def test_a_failing_round_on_demand_is_logged_and_recorded(self):
        with mock.patch.object(ah_orders, "resolve_receipt_products", new_callable=mock.AsyncMock, side_effect=RuntimeError("kapot")), \
                mock.patch.object(ah_orders.audit, "record", new_callable=mock.AsyncMock) as record, self.assertLogs(ah_orders.log, "ERROR"):
            await self.post_round("/api/ah/linking/run", json={})
        self.assertFalse(record.call_args.kwargs["ok"])
        self.assertIn("kapot", record.call_args.kwargs["error"])
        self.assertIsNone((await ah_orders.linking_status())["running"])

    async def test_a_second_run_while_one_is_busy_is_refused(self) -> None:
        started, release = asyncio.Event(), asyncio.Event()

        async def busy(receipt_id: str | None, now: str) -> None:
            started.set()
            await release.wait()

        async def scenario(client: AppClient) -> tuple[ReadResponse, ReadResponse, dict[str, Any], list[str]]:
            first = await client.post("/api/ah/linking/run", json={"receiptId": "R1"})
            await asyncio.wait_for(started.wait(), 5)
            second = await client.post("/api/ah/linking/run", json={})
            status = (await client.get("/api/ah/linking")).get_json()
            names = [name for name in tasks.running() if name.startswith("linking-run")]
            release.set()
            return first, second, status, names

        with mock.patch.object(ah_orders, "_linking_round", new_callable=mock.AsyncMock, side_effect=busy):
            first, second, status, names = await self.live(scenario)
        self.assertEqual((first.status_code, second.status_code), (202, 409))
        self.assertEqual(second.get_json()["running"], {"receiptId": "R1"})
        self.assertEqual(status["running"], {"receiptId": "R1"})
        self.assertEqual(names, ["linking-run"], "een ronde tegelijk")
        self.assertIsNone((await ah_orders.linking_status())["running"])

    async def test_a_round_cancelled_at_shutdown_is_not_left_running(self) -> None:
        started = asyncio.Event()

        async def hang(receipt_id: str | None, now: str) -> None:
            started.set()
            await asyncio.Event().wait()  # tot het afsluiten van de app

        async def scenario(client: AppClient) -> tuple[int, dict[str, Any] | None]:
            response = await client.post("/api/ah/linking/run", json={})
            await asyncio.wait_for(started.wait(), 5)
            return response.status_code, (await client.get("/api/ah/linking")).get_json()["running"]

        with mock.patch.object(ah_orders, "_linking_round", new_callable=mock.AsyncMock, side_effect=hang):
            status, running = await self.live(scenario, wait=False)
        self.assertEqual((status, running), (202, {"receiptId": None}))
        self.assertTrue(ah_orders._LINKING_TASK.cancelled(), "het afsluiten annuleert de ronde")
        self.assertIsNone((await ah_orders.linking_status())["running"], "een geannuleerde ronde staat niet op bezig")
        with mock.patch.object(ah_orders, "_linking_round", new_callable=mock.AsyncMock, return_value={}) as round_:
            self.assertEqual((await self.post_round("/api/ah/linking/run", json={})).status_code, 202, "geen 409 na de annulering")
        round_.assert_called_once_with(None, self.NOW)

    async def test_force_is_for_the_administrator_only_and_clears_the_pause(self):
        await auth.set_setting(ah_orders.SETTING_RECEIPT_PRODUCTS_PAUSED, "2026-11-06T02:02:00+00:00")
        await auth.set_setting(ah_orders.SETTING_RECEIPT_PRODUCTS_PAUSED_REASON, "429@2026-11-05T14:02:00+00:00")
        with mock.patch.object(ah_orders, "_linking_round", new_callable=mock.AsyncMock, return_value={}):
            with mock.patch.object(auth, "is_owner", new_callable=mock.AsyncMock, return_value=False):
                refused = await self.client.post("/api/ah/linking/run", json={"force": True})
            self.assertEqual(refused.status_code, 403)
            self.assertIn("error", refused.get_json())
            self.assertIsNone((await ah_orders.linking_status())["running"])
            self.assertEqual(await auth.get_setting(ah_orders.SETTING_RECEIPT_PRODUCTS_PAUSED), "2026-11-06T02:02:00+00:00")
            accepted = await self.post_round("/api/ah/linking/run", json={"force": True})
        self.assertEqual(accepted.status_code, 202)
        self.assertEqual(await auth.get_setting(ah_orders.SETTING_RECEIPT_PRODUCTS_PAUSED), "")
        self.assertEqual(await auth.get_setting(ah_orders.SETTING_RECEIPT_PRODUCTS_PAUSED_REASON), "")
        self.assertIsNone(accepted.get_json()["pausedUntil"])

    async def test_a_run_without_force_keeps_the_pause(self):
        await auth.set_setting(ah_orders.SETTING_RECEIPT_PRODUCTS_PAUSED, "2026-11-06T02:02:00+00:00")
        with mock.patch.object(ah_orders, "_linking_round", new_callable=mock.AsyncMock, return_value={}):
            await self.post_round("/api/ah/linking/run", json={})
        self.assertEqual(await auth.get_setting(ah_orders.SETTING_RECEIPT_PRODUCTS_PAUSED), "2026-11-06T02:02:00+00:00")

    async def test_a_garbled_body_is_refused_and_starts_nothing(self):
        for data, content_type in (("not json", "application/json"), ("receiptId=R1", "application/x-www-form-urlencoded"), ('"R1"', "application/json")):
            with self.subTest(data=data):
                with mock.patch.object(ah_orders, "run_linking", new_callable=mock.AsyncMock) as run:
                    response = await self.client.post("/api/ah/linking/run", data=data, content_type=content_type)
                self.assertEqual(response.status_code, 400)
                self.assertIn("error", response.get_json())
                run.assert_not_called()

    async def test_an_empty_body_runs_all_receipts(self):
        with mock.patch.object(ah_orders, "_linking_round", new_callable=mock.AsyncMock, return_value={}) as round_:
            response = await self.post_round("/api/ah/linking/run")
        self.assertEqual(response.status_code, 202)
        round_.assert_called_once_with(None, self.NOW)

    async def test_a_round_on_demand_with_nothing_to_do_still_writes_the_last_round(self):
        with closing(db(ah_orders)) as conn, conn:
            conn.executemany("INSERT INTO ah_receipt_products (receipt_product_id, product_id, title, checked_at, image) VALUES (?, ?, ?, 'x', ?)",
                             [(555, 1, "AH Komkommer", "https://static.ah.nl/1.jpg"), (556, 2, "AH Tomaten", "https://static.ah.nl/2.jpg")])
        await auth.set_setting(ah_orders.SETTING_LAST_ROUND, json.dumps({"at": "2026-11-05T02:00:00+00:00", "searches": 9, "found": 3, "trigger": "nightly"}))
        with mock.patch.object(ah_orders, "_polite_search", new_callable=mock.AsyncMock) as search:
            self.assertEqual((await self.post_round("/api/ah/linking/run", json={"receiptId": "R1"})).status_code, 202)
        search.assert_not_called()
        last = json.loads(await auth.get_setting(ah_orders.SETTING_LAST_ROUND))
        self.assertEqual(last, {"at": self.NOW, "searches": 0, "found": 0, "missing": 0, "failed": 0, "images": 0, "trigger": "receipt"})

    async def test_a_round_on_demand_that_did_something_keeps_its_counts(self):
        with mock.patch.object(ah_orders, "_polite_search", new_callable=mock.AsyncMock, return_value=[(555, 1, "AH Komkommer", "")]):
            await self.post_round("/api/ah/linking/run", json={"receiptId": "R1"})
        last = json.loads(await auth.get_setting(ah_orders.SETTING_LAST_ROUND))
        self.assertEqual((last["searches"], last["found"], last["missing"], last["trigger"]), (2, 1, 1, "receipt"), "het nul-record telt niets bij")

    async def test_a_bad_request_is_refused(self):
        for body in ({"receiptId": 5}, {"force": "ja"}, ["R1"]):
            with self.subTest(body=body):
                self.assertEqual((await self.client.post("/api/ah/linking/run", json=body)).status_code, 400)
        with mock.patch.object(ah_orders, "run_linking", new_callable=mock.AsyncMock) as start:
            self.assertEqual((await self.client.post("/api/ah/linking/run", json={"receiptId": "R9"})).status_code, 404)
        start.assert_not_called()

    async def test_an_images_run_only_fills_in_photos(self):
        with mock.patch.object(ah_orders, "resolve_receipt_products", new_callable=mock.AsyncMock) as resolve, \
                mock.patch.object(ah_orders, "_polite_search", new_callable=mock.AsyncMock) as search, \
                mock.patch.object(ah_orders, "backfill_receipt_images", new_callable=mock.AsyncMock, return_value={"products": 3, "requests": 1, "failed": 0}) as images, \
                mock.patch.object(ah_orders.audit, "record", new_callable=mock.AsyncMock) as record:
            self.assertEqual((await self.post_round("/api/ah/linking/run", json={"images": True})).status_code, 202)
        resolve.assert_not_called()
        search.assert_not_called()
        images.assert_called_once()
        self.assertEqual(images.call_args.kwargs["batches"], ah_orders.RECEIPT_IMAGES_BATCHES)
        self.assertEqual(images.call_args.kwargs["trigger"], "images")
        self.assertEqual(json.loads(await auth.get_setting(ah_orders.SETTING_LAST_ROUND))["trigger"], "images")
        self.assertEqual(record.call_args.kwargs["action"], "images")
        self.assertEqual(record.call_args.kwargs["detail"]["images"], {"products": 3, "requests": 1, "failed": 0})

    async def test_an_images_run_fetches_photos_and_writes_the_last_round(self):
        with closing(db(ah_orders)) as conn, conn:
            conn.execute("INSERT INTO ah_receipt_products (receipt_product_id, product_id, title, checked_at) VALUES (556, 2, 'AH Tomaten', 'x')")
        self.get_products_by_ids.return_value = [{"id": 2, "image": "https://static.ah.nl/2.jpg"}]
        with mock.patch.object(ah_orders, "_polite_search", new_callable=mock.AsyncMock) as search:
            self.assertEqual((await self.post_round("/api/ah/linking/run", json={"images": True})).status_code, 202)
        search.assert_not_called()
        last = json.loads(await auth.get_setting(ah_orders.SETTING_LAST_ROUND))
        self.assertEqual((last["searches"], last["images"], last["trigger"]), (0, 1, "images"))

    async def test_force_with_images_clears_the_pause_for_the_administrator(self):
        await auth.set_setting(ah_orders.SETTING_RECEIPT_PRODUCTS_PAUSED, "2026-11-06T02:02:00+00:00")
        with mock.patch.object(ah_orders, "backfill_receipt_images", new_callable=mock.AsyncMock, return_value={"products": 0}):
            with mock.patch.object(auth, "is_owner", new_callable=mock.AsyncMock, return_value=False):
                self.assertEqual((await self.client.post("/api/ah/linking/run", json={"force": True, "images": True})).status_code, 403)
            self.assertEqual(await auth.get_setting(ah_orders.SETTING_RECEIPT_PRODUCTS_PAUSED), "2026-11-06T02:02:00+00:00")
            self.assertEqual((await self.post_round("/api/ah/linking/run", json={"force": True, "images": True})).status_code, 202)
        self.assertEqual(await auth.get_setting(ah_orders.SETTING_RECEIPT_PRODUCTS_PAUSED), "")

    async def test_a_bad_images_flag_is_refused(self):
        for body in ({"images": "ja"}, {"images": 1}, {"images": True, "receiptId": "R1"}):
            with self.subTest(body=body):
                with mock.patch.object(ah_orders, "run_linking", new_callable=mock.AsyncMock) as run:
                    self.assertEqual((await self.client.post("/api/ah/linking/run", json=body)).status_code, 400)
                run.assert_not_called()


class SearchHqIdsTest(AppTestCase):
    def test_only_real_article_numbers_are_kept(self):
        raw = {"products": [
            {"webshopId": 4183, "hqId": 20960, "title": "AH Bloemkool", "images": [{"url": "https://static.ah.nl/bloemkool", "width": 200}]},
            {"webshopId": 615270, "hqId": 0, "title": "AH Bloemkoolrijst kleinverpakking 3-pack"},
            {"webshopId": 1, "title": "zonder hqId"},
            "geen product",
        ]}
        with mock.patch.object(ah, "_search_json", return_value=raw) as search:
            self.assertEqual(run(ah.search_hq_ids("BLOEMKOOL")), [(20960, 4183, "AH Bloemkool", "https://static.ah.nl/bloemkool")])
        search.assert_called_once_with("BLOEMKOOL", ah.HQ_SEARCH_WINDOW)


class SearchTest(AhOrdersTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        with mock.patch.object(ah, "get_open_fulfillments", return_value=[fulfillment(1, "2026-10-01"), fulfillment(2, "2026-10-20")]), mock.patch.object(ah, "get_order_details", side_effect=fake_details):
            await self.client.post("/api/ah/orders/sync")
        with mock.patch.object(ah, "get_receipts", return_value=[{"id": "R1", "dateTime": "2026-10-18T10:00:00", "total": 5.0}]), mock.patch.object(
            ah, "get_receipt", return_value={"id": "R1", "items": [{"productId": 5, "name": "AH Halfvolle melk", "quantity": 3, "price": 1.0, "amount": 3.0}, {"productId": 6, "name": "Koffiebonen", "quantity": 1, "price": 5.0, "amount": 5.0}], "discounts": [], "payments": []}
        ):
            await self.client.post("/api/ah/receipts/sync")

    async def test_finds_products_in_orders_and_receipts_newest_first(self):
        data = (await self.client.get("/api/ah/purchases/search?q=melk")).get_json()
        self.assertEqual([(o["source"], o["date"]) for o in data["occurrences"]], [("bestelling", "2026-10-23"), ("bestelling", "2026-10-23"), ("kassabon", "2026-10-18")])
        names = {p["product"]: p for p in data["products"]}
        self.assertEqual(names["AH Halfvolle melk"]["totalQuantity"], 3)
        self.assertEqual(names["Halfvolle melk"]["times"], 2)
        self.assertEqual(data["total"], 3)

    async def test_words_accents_case_and_empty_queries(self):
        self.assertEqual((await self.client.get("/api/ah/purchases/search?q=HALFVOLLE%20Melk")).get_json()["total"], 3)
        self.assertEqual((await self.client.get("/api/ah/purchases/search?q=cafe")).get_json()["total"], 0)
        self.assertEqual((await self.client.get("/api/ah/purchases/search?q=koffie")).get_json()["products"][0]["product"], "Koffiebonen")
        self.assertEqual((await self.client.get("/api/ah/purchases/search?q=")).get_json()["products"], [])

    async def test_receipts_with_a_product_are_listed_with_the_matching_lines(self):
        with mock.patch.object(ah, "get_receipts", return_value=[{"id": "R2", "dateTime": "2026-10-20T10:00:00", "total": 9.0}, {"id": "R1", "dateTime": "2026-10-18T10:00:00", "total": 5.0}, {"id": "R0", "dateTime": "2026-10-01T10:00:00", "total": 7.0}]), mock.patch.object(
            ah, "get_receipt", side_effect=lambda rid: {"id": rid, "items": [{"productId": 1, "name": "Cola Frisdank 1,5L", "quantity": 2, "price": 1.5, "amount": 3.0}, {"productId": 2, "name": "Brood", "quantity": 1, "price": 2.0, "amount": 2.0}] if rid == "R2" else [{"productId": 3, "name": "Appelsap", "quantity": 1, "price": 1.0, "amount": 1.0}], "discounts": [], "payments": []}
        ):
            await self.client.post("/api/ah/receipts/sync")
        data = (await self.client.get("/api/ah/receipts/search?q=FRISDANK")).get_json()
        self.assertEqual((data["total"], data["unsearched"]), (1, 0))
        self.assertEqual(data["receipts"][0]["id"], "R2")
        self.assertEqual(data["receipts"][0]["matches"], [{"name": "Cola Frisdank 1,5L", "quantity": 2, "amount": 3.0}])
        both = (await self.client.get("/api/ah/receipts/search?q=a")).get_json()
        self.assertEqual([r["id"] for r in both["receipts"]], ["R2", "R1", "R0"][: both["total"]])
        self.assertEqual((await self.client.get("/api/ah/receipts/search?q=")).get_json(), {"query": "", "receipts": [], "total": 0, "unsearched": 0})

    async def test_the_table_asks_for_every_hit_with_limit_all(self):
        await self.client.post("/api/ah/receipts/sync")
        with mock.patch.object(ah_orders, "search_purchases", wraps=ah_orders.search_purchases) as search:
            await self.client.get("/api/ah/purchases/search?q=melk&limit=all")
            await self.client.get("/api/ah/purchases/search?q=melk&limit=7")
            await self.client.get("/api/ah/purchases/search?q=melk&limit=nope")
        self.assertEqual([call.kwargs["limit"] for call in search.call_args_list], [100000, 7, 50])
        with mock.patch.object(ah_orders, "search_receipts", wraps=ah_orders.search_receipts) as search:
            await self.client.get("/api/ah/receipts/search?q=melk&limit=all")
            await self.client.get("/api/ah/receipts/search?q=melk")
        self.assertEqual([call.kwargs["limit"] for call in search.call_args_list], [100000, 200])

    async def test_receipts_without_their_lines_are_counted_as_not_searched(self):
        with mock.patch.object(ah, "get_receipts", return_value=[{"id": "R9", "dateTime": "2026-10-19T10:00:00", "total": 4.0}]), mock.patch.object(ah, "get_receipt", side_effect=RuntimeError("AH even weg")):
            await self.client.post("/api/ah/receipts/sync")
        data = (await self.client.get("/api/ah/receipts/search?q=melk")).get_json()
        self.assertEqual((data["total"], data["unsearched"]), (1, 1))  # R1 (melk) is gevonden; R9 kon niet worden doorzocht

    async def test_search_is_local(self):
        with mock.patch.object(ah, "_get_json", side_effect=AssertionError("AH")), mock.patch.object(ah, "_graphql", side_effect=AssertionError("AH")):
            self.assertEqual((await self.client.get("/api/ah/purchases/search?q=melk")).status_code, 200)
            self.assertEqual((await self.client.get("/api/ah/receipts/search?q=melk")).status_code, 200)


class ExploreTest(AppTestCase):
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

        bonus = {
            "bonusPeriods": [{"startDate": "2026-10-26", "endDate": "2026-11-01", "weekNumber": 44}, {"startDate": "2026-11-02", "endDate": "2026-11-08", "weekNumber": 45}],
            "bonusPromotions": [
                {"periodStart": "2026-11-02", "promotionType": "NATIONAL", "products": [{"id": 4242, "title": "Calvé pindakaas"}]},
                {"periodStart": "2026-11-02", "promotionType": "INCENTIVE", "products": [{"id": 1}]},
            ],
        }
        with mock.patch.object(ah_explore, "_raw_get", side_effect=get), mock.patch.object(ah_explore, "_raw_graphql", side_effect=graphql), \
                mock.patch.object(ah_explore, "_anonymous_graphql", return_value=bonus), mock.patch.object(ah, "configured_refresh_token", return_value="x"), \
                mock.patch.object(ah, "get_token", return_value="anoniem"), mock.patch.object(ah, "_request_json", side_effect=RuntimeError("geen netwerk in de test")):
            report = run(ah_explore.explore())
        names = [p["name"] for p in report["probes"]]
        periods = next(p for p in report["probes"] if "bonusPeriods" in p["name"])
        self.assertEqual(periods["info"]["periodes"][1], {"week": 45, "van": "2026-11-02", "tot": "2026-11-08", "acties": 1, "producten": 1},
                         "per bonusperiode het aantal acties en producten van de bonus, zonder bezorgkorting")
        self.assertEqual(periods["info"]["soorten"], ["INCENTIVE", "NATIONAL"])
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
        with mock.patch.object(ah_explore, "_raw_get", side_effect=RuntimeError("404: not found")), mock.patch.object(ah_explore, "_raw_graphql", side_effect=RuntimeError("boom")), \
                mock.patch.object(ah_explore, "_anonymous_graphql", side_effect=RuntimeError("boom")), mock.patch.object(ah, "configured_refresh_token", return_value=""), \
                mock.patch.object(ah, "get_token", return_value="anoniem"), mock.patch.object(ah, "_request_json", side_effect=RuntimeError("geen netwerk in de test")):
            report = run(ah_explore.explore())
        self.assertEqual(report["ok"], 0)
        self.assertIn("overgeslagen", next(p for p in report["probes"] if p["name"].startswith("inhoud"))["error"])


class PermissionsTest(AppTestCase):
    async def asyncSetUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(os.environ, {"PASSKEY_AUTH_ENABLED": "true", "MEALIESHOPPER_DATA_DIR": tmp.name, "MEALIESHOPPER_AUTH_SECRET": "t"})
        env.start()
        self.addCleanup(env.stop)
        cost = mock.patch.object(auth_password, "SCRYPT_N", 2**4)
        cost.start()
        self.addCleanup(cost.stop)
        self.app = create_app()
        self.admin = self.client_for(self.app)
        await self.admin.post("/api/auth/password", json={"username": "admin", "password": "koffie-met-melk-42"})
        token = (await self.admin.post("/api/users/invites", json={"username": "anna"})).get_json()["token"]
        self.anna = self.client_for(self.app)
        await self.anna.post("/api/invites/password", json={"token": token, "password": "een-heel-ander-wachtwoord"})

    async def test_everything_needs_a_login(self):
        anonymous = self.client_for(self.app)
        for path in ("/api/ah/basket", "/api/ah/orders", "/api/ah/receipts", "/api/ah/purchases/search?q=x", "/api/ah/receipts/search?q=x", "/api/ah/orders/1",
                     "/api/ah/linking"):
            self.assertEqual((await anonymous.get(path)).status_code, 401, path)
        for path in ("/api/ah/basket/sync", "/api/ah/orders/sync", "/api/ah/receipts/sync", "/api/ah/explore", "/api/ah/linking/run"):
            self.assertEqual((await anonymous.post(path)).status_code, 401, path)

    async def test_users_can_read_and_sync_but_only_the_owner_can_explore(self):
        for path in ("/api/ah/basket", "/api/ah/orders", "/api/ah/receipts", "/api/ah/linking"):
            self.assertEqual((await self.anna.get(path)).status_code, 200, path)
        self.assertEqual((await self.anna.post("/api/ah/linking/run", json={"force": True})).status_code, 403, "de pauze opheffen mag alleen de beheerder")
        with mock.patch.object(ah, "get_open_fulfillments", return_value=[]):
            self.assertEqual((await self.anna.post("/api/ah/orders/sync")).status_code, 200)
        self.assertEqual((await self.anna.post("/api/ah/explore")).status_code, 403)
        with mock.patch.object(ah_explore, "explore", new_callable=mock.AsyncMock, return_value={"probes": [], "ok": 0}):
            self.assertEqual((await self.admin.post("/api/ah/explore")).status_code, 200)


if __name__ == "__main__":
    unittest.main()
