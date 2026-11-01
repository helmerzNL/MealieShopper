"""Crisp-bestellingen: de lokale kopie van bezorgde bestellingen, synchroniseren per pagina, lezen zonder verbinding.

De fixtures hebben de vorm die de broncode van de Crisp-app laat zien (`/order/history` en `/order/{id}`), met verzonnen
ids, titels en bedragen. `crisp.order_history_page` en `crisp.get_order` worden nagebootst; hun eigen vorm testen we in
tests/test_crisp.py.
"""
import json
import os
import tempfile
import unittest
from contextlib import closing
from unittest import mock

os.environ.setdefault("MEALIESHOPPER_DATA_DIR", tempfile.mkdtemp())

from mealieshopper import auth, crisp, crisp_orders  # noqa: E402


def header(order_id, status="done", date="2026-10-10"):
    return {"id": str(order_id), "status": status, "deliveryDate": date,
            "windowStart": f"{date}T17:00:00+02:00", "windowEnd": f"{date}T19:00:00+02:00", "total": None}


def detail(order_id, *names, status="done", date="2026-10-10", total=15.0):
    items = [{"id": str(100 + n), "title": name, "quantity": 2 if n == 1 else 1, "unitSize": "1 liter", "price": 1.5 + n,
              "listPrice": None, "image": "https://crispapp.nl/blob/x?thumb=512"} for n, name in enumerate(names, 1)]
    return {**header(order_id, status, date), "total": total, "items": items,
            "itemCount": sum(item["quantity"] for item in items), "itemsSynced": True}


class CrispOrdersTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"MEALIESHOPPER_DATA_DIR": tmp.name, "PASSKEY_AUTH_ENABLED": "false"})
        patcher.start()
        self.addCleanup(patcher.stop)
        auth.init_db()
        self.pages = {}  # cursor -> (orders, volgende cursor)
        self.details = {}
        self.asked = []
        self.pages_asked = []

    def sync(self):
        def page(cursor=None):
            self.pages_asked.append(cursor)
            return self.pages[cursor]

        def get_order(order_id):
            self.asked.append(order_id)
            result = self.details[order_id]
            if isinstance(result, Exception):
                raise result
            return result

        with mock.patch.object(crisp, "order_history_page", side_effect=page), \
                mock.patch.object(crisp, "get_order", side_effect=get_order):
            return crisp_orders.sync_orders()

    def test_delivered_orders_are_stored_with_their_products_over_pages(self):
        self.pages = {None: ([header(2, date="2026-10-17"), header(9, status="ready")], "p2"), "p2": ([header(1)], None)}
        self.details = {"1": detail(1, "Melk", "Kaas"), "2": detail(2, "Prei", date="2026-10-17", total=5.0)}
        result = self.sync()
        self.assertEqual((result["orders"], result["new"], result["remaining"]), (2, 2, 0))
        self.assertEqual(self.pages_asked, [None, "p2"])
        self.assertNotIn("9", self.asked, "een aankomende bestelling bewaren we niet")
        data = crisp_orders.list_orders()
        self.assertEqual([order["id"] for order in data["orders"]], ["2", "1"])  # nieuwste eerst
        self.assertEqual((data["orders"][1]["itemCount"], data["orders"][1]["lineCount"], data["orders"][1]["total"]), (3, 2, 15.0))
        self.assertTrue(data["syncedAt"])
        order = crisp_orders.get_order("1")
        self.assertEqual([item["title"] for item in order["items"]], ["Melk", "Kaas"])
        self.assertTrue(order["itemsSynced"])

    def test_a_known_page_stops_the_walk(self):
        self.pages = {None: ([header(2), header(1)], "p2"), "p2": ([header(0)], None)}
        self.details = {"0": detail(0, "Oud"), "1": detail(1, "Melk"), "2": detail(2, "Prei")}
        self.sync()
        self.pages_asked = []
        self.asked = []
        self.pages = {None: ([header(3), header(2)], "p2"), "p2": ([header(1)], "p3"), "p3": AssertionError("niet zo ver")}
        self.details["3"] = detail(3, "Brood")
        result = self.sync()
        self.assertEqual(self.pages_asked, [None, "p2"], "pagina 2 is helemaal bekend: niet verder")
        self.assertEqual((result["new"], self.asked), (1, ["3"]))

    def test_details_per_sync_are_limited_and_the_rest_follows(self):
        many = [header(n) for n in range(30)]
        self.pages = {None: (many, None)}
        self.details = {str(n): detail(n, "Melk") for n in range(30)}
        first = self.sync()
        self.assertEqual((len(self.asked), first["remaining"]), (crisp_orders.ORDER_DETAILS_PER_SYNC, 30 - crisp_orders.ORDER_DETAILS_PER_SYNC))
        self.asked = []
        second = self.sync()
        self.assertEqual((len(self.asked), second["remaining"]), (5, 0))

    def test_failing_details_keep_the_overview_and_stop_early(self):
        self.pages = {None: ([header(n) for n in range(10)], None)}
        self.details = {str(n): RuntimeError("500: weg") for n in range(10)}
        result = self.sync()
        self.assertEqual(result["detailError"], "RuntimeError")
        self.assertEqual(len(self.asked), crisp_orders.MAX_FIRST_FAILURES)
        self.assertEqual(len(crisp_orders.list_orders()["orders"]), 10)

    def test_a_refused_session_stops_the_sync(self):
        self.pages = {None: ([header(1)], None)}
        self.details = {"1": crisp.CrispAuthError("weg")}
        with self.assertRaises(crisp.CrispAuthError):
            self.sync()

    def test_a_cancelled_order_is_removed(self):
        self.pages = {None: ([header(1)], None)}
        self.details = {"1": detail(1, "Melk")}
        self.sync()
        self.pages = {None: ([header(1, status="cancel")], None)}
        self.sync()
        self.assertIsNone(crisp_orders.get_order("1"))

    def test_no_personal_data_ends_up_in_the_database(self):
        page = {"data": [{"type": "order", "item": {"id": 1, "fulfillStatus": "done", "deliverySlot": {"date": "2026-10-10"}}}]}
        full = {"data": {"id": 1, "fulfillStatus": "done", "deliverySlot": {"date": "2026-10-10"}, "paidPrice": "9.99",
                         "address": {"street": "Dorpsstraat", "postal": "1234AB"}, "mandateIban": "NL00BANK0123456789",
                         "invoiceUrl": "https://crispapp.nl/factuur/geheim", "deliveryLink": "https://crispapp.nl/volg/geheim",
                         "products": [{"id": 5, "title": "Melk", "count": 1, "totalPrice": "1.00"}]}}
        auth.set_secret(crisp.CLIENT_SECRET, "CLIENTabc1234567890x")
        answers = {"/order/history": page, "/order/1": full}
        with mock.patch.object(crisp, "_call", side_effect=lambda method, path, client_id, body=None: answers[path]):
            crisp_orders.sync_orders()
        with closing(crisp_orders._connect()) as conn:
            rows = [dict(row) for table in ("crisp_orders", "crisp_order_items") for row in conn.execute(f"SELECT * FROM {table}")]
        dumped = json.dumps(rows)
        self.assertIn("Melk", dumped)
        for secret in ("Dorpsstraat", "1234AB", "NL00BANK", "factuur", "volg"):
            self.assertNotIn(secret, dumped)

    def test_search_purchases(self):
        self.pages = {None: ([header(2, date="2026-10-17"), header(1)], None)}
        self.details = {"1": detail(1, "Volle melk"), "2": detail(2, "Volle melk", "Kaas", date="2026-10-17")}
        self.sync()
        result = crisp_orders.search_purchases("melk")
        self.assertEqual((result["total"], result["products"][0]["times"], result["products"][0]["lastDate"]), (2, 2, "2026-10-17"))
        self.assertEqual(crisp_orders.search_purchases(" ")["total"], 0)


if __name__ == "__main__":
    unittest.main()
