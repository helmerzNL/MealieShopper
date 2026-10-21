"""Picnic-bestellingen: de lokale kopie van afgeleverde bestellingen, synchroniseren, lezen zonder verbinding.

De fixtures hebben de vorm van de bekende Picnic-antwoorden, met verzonnen ids, titels en bedragen.
"""
import json
import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("MEALIESHOPPER_DATA_DIR", tempfile.mkdtemp())

from mealieshopper import auth, mcp, picnic, picnic_orders  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402


def summary(delivery_id, status="COMPLETED", start="2026-10-10T10:00:00+02:00", total=15.0):
    return {"id": delivery_id, "status": status, "windowStart": start, "windowEnd": "2026-10-10T11:00:00+02:00",
            "etaStart": None, "etaEnd": None, "deliveredStart": None, "deliveredEnd": None, "total": total}


def detail(delivery_id, *names, status="COMPLETED", start="2026-10-10T10:00:00+02:00", total=15.0):
    return {**summary(delivery_id, status, start, total), "itemCount": len(names),
            "items": [{"id": f"s{n}", "title": name, "quantity": 2 if n == 1 else 1, "unitSize": "1 l", "price": 1.5 + n,
                       "listPrice": None, "image": "https://img/p.png"} for n, name in enumerate(names, 1)]}


class PicnicOrdersTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"MEALIESHOPPER_DATA_DIR": tmp.name, "PASSKEY_AUTH_ENABLED": "false"})
        patcher.start()
        self.addCleanup(patcher.stop)
        auth.init_db()
        self.listed = []
        self.details = {}
        self.asked = []

    def sync(self):
        def get_delivery(delivery_id):
            self.asked.append(delivery_id)
            result = self.details[delivery_id]
            if isinstance(result, Exception):
                raise result
            return result
        with mock.patch.object(picnic, "get_deliveries", side_effect=lambda: list(self.listed)), \
                mock.patch.object(picnic, "get_delivery", side_effect=get_delivery):
            return picnic_orders.sync_orders()

    def test_delivered_orders_are_stored_with_their_products(self):
        self.listed = [summary("d2", start="2026-10-17T10:00:00+02:00"), summary("d1")]
        self.details = {"d1": detail("d1", "Melk", "Kaas"), "d2": detail("d2", "Prei", start="2026-10-17T10:00:00+02:00", total=5.0)}
        result = self.sync()
        self.assertEqual((result["orders"], result["new"], result["remaining"]), (2, 2, 0))
        data = picnic_orders.list_orders()
        self.assertEqual([o["id"] for o in data["orders"]], ["d2", "d1"])  # nieuwste eerst
        self.assertEqual((data["orders"][1]["itemCount"], data["orders"][1]["lineCount"], data["orders"][1]["total"]), (3, 2, 15.0))
        self.assertTrue(data["syncedAt"])
        order = picnic_orders.get_order("d1")
        self.assertEqual([(i["id"], i["title"], i["quantity"], i["price"]) for i in order["items"]], [("s1", "Melk", 2, 2.5), ("s2", "Kaas", 1, 3.5)])
        self.assertTrue(order["itemsSynced"])
        self.assertIsNone(picnic_orders.get_order("nope"))

    def test_open_and_cancelled_orders_are_not_stored_and_cancelled_ones_are_removed(self):
        self.listed = [summary("d1"), summary("d2", status="CURRENT"), summary("d3", status="CANCELLED")]
        self.details = {"d1": detail("d1", "Melk"), "d3": detail("d3", "Kaas", status="CANCELLED")}
        picnic_orders_save = self.sync()
        self.assertEqual(picnic_orders_save["orders"], 1)
        self.assertEqual([o["id"] for o in picnic_orders.list_orders()["orders"]], ["d1"])
        self.assertNotIn("d2", self.asked)
        # later geannuleerd: de bewaarde kopie verdwijnt
        self.listed = [summary("d1", status="CANCELLED")]
        self.sync()
        self.assertEqual(picnic_orders.list_orders()["orders"], [])

    def test_a_synced_order_is_kept_and_not_fetched_again(self):
        self.listed = [summary("d1")]
        self.details = {"d1": detail("d1", "Melk")}
        self.sync()
        self.listed = []  # Picnic toont hem niet meer
        self.sync()
        self.assertEqual([o["id"] for o in picnic_orders.list_orders()["orders"]], ["d1"])
        self.listed = [summary("d1")]
        self.sync()
        self.assertEqual(self.asked, ["d1"])  # de producten zijn maar één keer opgehaald

    def test_products_follow_in_steps_and_failures_keep_the_overview(self):
        ids = [f"d{n:02d}" for n in range(30)]
        self.listed = [summary(i, start=f"2026-09-{n + 1:02d}T10:00:00+02:00") for n, i in enumerate(ids)]
        self.details = {i: detail(i, "Melk") for i in ids}
        first = self.sync()
        self.assertEqual((first["orders"], first["remaining"], len(self.asked)), (30, 30 - picnic_orders.ORDER_DETAILS_PER_SYNC, picnic_orders.ORDER_DETAILS_PER_SYNC))
        second = self.sync()
        self.assertEqual(second["remaining"], 0)
        self.assertEqual(len(self.asked), 30)

    def test_when_picnic_refuses_the_products_the_overview_still_lands(self):
        self.listed = [summary("d1"), summary("d2"), summary("d3"), summary("d4"), summary("d5")]
        self.details = {i: RuntimeError("500") for i in ("d1", "d2", "d3", "d4", "d5")}
        result = self.sync()
        self.assertEqual((result["orders"], result["remaining"], result["detailError"]), (5, 5, "RuntimeError"))
        self.assertEqual(len(self.asked), picnic_orders.MAX_FIRST_FAILURES)  # na een paar mislukkingen stoppen we
        self.assertEqual(len(picnic_orders.list_orders()["orders"]), 5)
        self.assertFalse(picnic_orders.list_orders()["orders"][0]["itemsSynced"])

    def test_an_expired_link_stops_the_sync(self):
        self.listed = [summary("d1")]
        self.details = {"d1": picnic.PicnicAuthError("verlopen")}
        with self.assertRaises(picnic.PicnicAuthError):
            self.sync()
        self.assertEqual(picnic_orders.list_orders()["orders"], [])

    def test_reading_never_asks_picnic(self):
        self.listed = [summary("d1")]
        self.details = {"d1": detail("d1", "Melk")}
        self.sync()
        with mock.patch.object(picnic, "_authed", side_effect=AssertionError("geen verbinding")), \
                mock.patch.object(picnic.requests, "request", side_effect=AssertionError("geen verbinding")):
            self.assertEqual(len(picnic_orders.list_orders()["orders"]), 1)
            self.assertEqual(picnic_orders.get_order("d1")["items"][0]["title"], "Melk")
            self.assertEqual(picnic_orders.search_purchases("melk")["total"], 1)

    def test_no_address_is_stored(self):
        self.listed = [summary("d1")]
        self.details = {"d1": {**detail("d1", "Melk"), "address": {"street": "Geheimstraat"}}}
        self.sync()
        import sqlite3
        with auth.connect() as conn:
            dump = " ".join(str(tuple(row)) for table in ("picnic_orders", "picnic_order_items") for row in conn.execute(f"SELECT * FROM {table}"))
        self.assertNotIn("Geheimstraat", dump)

    def test_search_purchases_finds_when_a_product_was_bought(self):
        self.listed = [summary("d2", start="2026-10-17T10:00:00+02:00"), summary("d1")]
        self.details = {"d1": detail("d1", "Halfvolle melk", "Kaas"), "d2": detail("d2", "Melk halfvol", start="2026-10-17T10:00:00+02:00")}
        self.sync()
        found = picnic_orders.search_purchases("Melk")
        self.assertEqual((found["total"], [o["id"] for o in found["occurrences"]]), (2, ["d2", "d1"]))
        self.assertEqual(picnic_orders.search_purchases("")["total"], 0)
        self.assertEqual(picnic_orders.search_purchases("zalm")["total"], 0)

    def test_routes(self):
        self.listed = [summary("d1")]
        self.details = {"d1": detail("d1", "Melk")}
        client = create_app().test_client()
        with mock.patch.object(picnic, "get_deliveries", side_effect=lambda: list(self.listed)), \
                mock.patch.object(picnic, "get_delivery", side_effect=lambda i: self.details[i]):
            reply = client.post("/api/picnic/orders/sync")
        self.assertEqual((reply.status_code, reply.get_json()["orders"]), (200, 1))
        self.assertEqual(client.get("/api/picnic/orders").get_json()["orders"][0]["id"], "d1")
        self.assertEqual(client.get("/api/picnic/orders/d1").get_json()["items"][0]["title"], "Melk")
        self.assertEqual(client.get("/api/picnic/orders/nope").status_code, 404)
        with mock.patch.object(picnic, "get_deliveries", side_effect=RuntimeError("500: geheim")):
            reply = client.post("/api/picnic/orders/sync")
        self.assertEqual((reply.status_code, reply.get_json()), (424, {"error": "Picnic fout"}))
        self.assertEqual(len(client.get("/api/picnic/orders").get_json()["orders"]), 1)  # de kopie blijft staan

    def test_mcp_tools_read_the_local_copy(self):
        self.listed = [summary("d1")]
        self.details = {"d1": detail("d1", "Melk")}
        self.sync()
        listing = mcp.tool_list_picnic_orders({"limit": 5})
        self.assertEqual((listing["total"], listing["orders"][0]["id"]), (1, "d1"))
        self.assertEqual(mcp.tool_get_picnic_order({"order_id": "d1"})["items"][0]["title"], "Melk")
        with self.assertRaises(mcp.ToolError):
            mcp.tool_get_picnic_order({"order_id": "nope"})
        self.assertNotIn("address", json.dumps(listing))


if __name__ == "__main__":
    unittest.main()
