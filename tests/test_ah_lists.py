import os
import tempfile
import unittest
from unittest import mock

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

from mealieshopper import ah, ah_lists  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402


def live_list(list_id="abc", name="Favorieten", items=None):
    items = items if items is not None else [
        {
            "id": "i1",
            "productId": 10,
            "url": "https://www.ah.nl/producten/product/wi10",
            "quantity": 2,
            "product": {
                "id": 10, "title": "Melk", "brand": "AH", "unitSize": "1 l",
                "price": 1.25, "image": "https://img/10.png", "isBonus": True,
                "url": "https://www.ah.nl/producten/product/wi10",
            },
        },
        {"id": "i2", "productId": 11, "url": "", "quantity": 1, "product": None},
    ]
    return {"id": list_id, "name": name, "totalSize": len(items), "items": items}


class AhListsTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patcher = mock.patch.dict(os.environ, {"MEALIESHOPPER_DATA_DIR": self.tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        self.client = create_app().test_client()


class SnapshotTest(AhListsTestCase):
    def test_save_and_read_back_with_clickable_urls(self):
        snapshot = ah_lists.save_snapshot(live_list())
        self.assertEqual(snapshot["id"], "ABC")
        self.assertEqual(snapshot["source"], "local")
        first, second = snapshot["items"]
        self.assertEqual(first["quantity"], 2)
        self.assertEqual(first["product"]["title"], "Melk")
        self.assertTrue(first["product"]["isBonus"])
        self.assertEqual(first["url"], "https://www.ah.nl/producten/product/wi10")
        # Product zonder details houdt toch een aanklikbare link.
        self.assertIsNone(second["product"])
        self.assertEqual(second["url"], "https://www.ah.nl/producten/product/wi11")

    def test_resync_replaces_items(self):
        ah_lists.save_snapshot(live_list())
        ah_lists.save_snapshot(live_list(items=[]))
        self.assertEqual(ah_lists.get_snapshot("abc")["items"], [])
        self.assertEqual(ah_lists.list_snapshots()[0]["itemCount"], 0)

    def test_missing_snapshot(self):
        self.assertIsNone(ah_lists.get_snapshot("nope"))


class MergedListsTest(AhListsTestCase):
    def test_ah_down_still_shows_local_copy(self):
        ah_lists.save_snapshot(live_list())
        with mock.patch.object(ah, "get_favorite_lists", side_effect=RuntimeError("404 not_found")):
            data = ah_lists.merged_lists()
        self.assertEqual(data["ahError"], "404 not_found")
        self.assertEqual(len(data["lists"]), 1)
        self.assertEqual(data["lists"][0]["source"], "local")
        self.assertTrue(data["lists"][0]["synced"])

    def test_live_and_local_are_merged(self):
        ah_lists.save_snapshot(live_list("abc", "Favorieten"))
        live = [
            {"id": "abc", "name": "Favorieten", "itemCount": 5, "updatedAt": "2026-10-01"},
            {"id": "def", "name": "Borrel", "itemCount": 2, "updatedAt": None},
        ]
        with mock.patch.object(ah, "get_favorite_lists", return_value=live):
            data = ah_lists.merged_lists()
        by_id = {entry["id"]: entry for entry in data["lists"]}
        self.assertIsNone(data["ahError"])
        self.assertEqual(by_id["ABC"]["source"], "both")
        self.assertEqual(by_id["ABC"]["syncedCount"], 2)
        self.assertEqual(by_id["ABC"]["itemCount"], 5)
        self.assertEqual(by_id["DEF"]["source"], "ah")
        self.assertFalse(by_id["DEF"]["synced"])

    def test_deleted_at_ah_keeps_local_copy(self):
        ah_lists.save_snapshot(live_list("abc"))
        with mock.patch.object(ah, "get_favorite_lists", return_value=[]):
            data = ah_lists.merged_lists()
        self.assertEqual([entry["source"] for entry in data["lists"]], ["local"])


class RoutesTest(AhListsTestCase):
    def test_overview_is_200_even_when_ah_fails(self):
        with mock.patch.object(ah, "get_favorite_lists", side_effect=RuntimeError("AH account niet gekoppeld")):
            response = self.client.get("/api/ah/lists")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["ahError"], "AH account niet gekoppeld")

    def test_sync_then_open_works_without_ah(self):
        with mock.patch.object(ah, "get_favorite_list_items", return_value=live_list()):
            response = self.client.post("/api/ah/lists/abc/sync")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["totalSize"], 2)
        with mock.patch.object(ah, "get_favorite_list_items", side_effect=AssertionError("AH mag niet worden gebruikt")):
            detail = self.client.get("/api/ah/lists/abc")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.get_json()["source"], "local")
        self.assertEqual(detail.get_json()["items"][0]["product"]["title"], "Melk")

    def test_open_unsynced_list_goes_live(self):
        with mock.patch.object(ah, "get_favorite_list_items", return_value=live_list("zzz")):
            detail = self.client.get("/api/ah/lists/zzz")
        self.assertEqual(detail.get_json()["source"], "ah")

    def test_failed_sync_keeps_previous_copy(self):
        ah_lists.save_snapshot(live_list())
        with mock.patch.object(ah, "get_favorite_list_items", side_effect=RuntimeError("boom")):
            response = self.client.post("/api/ah/lists/abc/sync")
        self.assertEqual(response.status_code, 424)
        self.assertEqual(response.get_json()["error"], "boom")
        self.assertEqual(len(ah_lists.get_snapshot("abc")["items"]), 2)

    def test_open_unsynced_list_reports_ah_error(self):
        with mock.patch.object(ah, "get_favorite_list_items", side_effect=RuntimeError("niet gevonden")):
            response = self.client.get("/api/ah/lists/zzz")
        self.assertEqual(response.status_code, 424)


class QuantityTest(AhListsTestCase):
    def test_new_snapshot_has_ah_quantities_as_default(self):
        first = ah_lists.save_snapshot(live_list())["items"][0]
        self.assertEqual((first["quantity"], first["defaultQuantity"], first["edited"]), (2, 2, False))

    def test_quantity_can_be_changed_and_survives_a_sync(self):
        ah_lists.save_snapshot(live_list())
        snapshot = ah_lists.set_quantity("abc", 0, 5)
        self.assertEqual(snapshot["items"][0]["quantity"], 5)
        self.assertEqual(snapshot["items"][0]["defaultQuantity"], 2)
        self.assertTrue(snapshot["items"][0]["edited"])
        self.assertEqual(snapshot["editedCount"], 1)

        after = ah_lists.save_snapshot(live_list())
        self.assertEqual(after["items"][0]["quantity"], 5)
        self.assertEqual(after["items"][0]["defaultQuantity"], 2)

    def test_sync_follows_the_product_when_the_order_changes_and_takes_new_ah_defaults(self):
        ah_lists.save_snapshot(live_list())
        ah_lists.set_quantity("abc", 0, 7)  # Melk
        live = live_list()
        live["items"] = [live["items"][1], {**live["items"][0], "quantity": 3}]  # andere volgorde, AH zegt nu 3
        after = ah_lists.save_snapshot(live)
        by_product = {item["productId"]: item for item in after["items"]}
        self.assertEqual(by_product[10]["quantity"], 7)
        self.assertEqual(by_product[10]["defaultQuantity"], 3)
        self.assertEqual(by_product[11]["quantity"], 1)

    def test_untouched_quantities_follow_ah_on_sync(self):
        ah_lists.save_snapshot(live_list())
        live = live_list()
        live["items"][0]["quantity"] = 4
        after = ah_lists.save_snapshot(live)
        self.assertEqual(after["items"][0]["quantity"], 4)
        self.assertFalse(after["items"][0]["edited"])

    def test_reset_restores_the_ah_values_only_for_this_list(self):
        ah_lists.save_snapshot(live_list("abc"))
        ah_lists.save_snapshot(live_list("def"))
        ah_lists.set_quantity("abc", 0, 9)
        ah_lists.set_quantity("def", 0, 9)
        reset = ah_lists.reset_quantities("abc")
        self.assertEqual(reset["items"][0]["quantity"], 2)
        self.assertEqual(reset["editedCount"], 0)
        self.assertEqual(ah_lists.get_snapshot("def")["items"][0]["quantity"], 9)

    def test_invalid_quantities_and_unknown_rows(self):
        ah_lists.save_snapshot(live_list())
        for bad in (0, 100, -1, "veel", None):
            with self.assertRaises(ValueError, msg=repr(bad)):
                ah_lists.set_quantity("abc", 0, bad)
        self.assertIsNone(ah_lists.set_quantity("abc", 5, 2))
        self.assertIsNone(ah_lists.set_quantity("nope", 0, 2))
        self.assertIsNone(ah_lists.reset_quantities("nope"))

    def test_database_from_before_editable_quantities_is_upgraded(self):
        from contextlib import closing

        from mealieshopper import auth

        with closing(auth.connect()) as conn, conn:
            conn.executescript(
                """
                CREATE TABLE ah_lists (id TEXT PRIMARY KEY, name TEXT NOT NULL, synced_at TEXT NOT NULL);
                CREATE TABLE ah_list_items (
                  list_id TEXT NOT NULL, position INTEGER NOT NULL, item_id TEXT, product_id INTEGER NOT NULL,
                  quantity INTEGER NOT NULL DEFAULT 1, title TEXT, brand TEXT, unit_size TEXT, price REAL,
                  image TEXT, url TEXT, is_bonus INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (list_id, position));
                INSERT INTO ah_lists VALUES ('OLD', 'Oud', 'x');
                INSERT INTO ah_list_items (list_id, position, product_id, quantity, title) VALUES ('OLD', 0, 10, 3, 'Melk');
                """
            )
        item = ah_lists.get_snapshot("old")["items"][0]
        self.assertEqual((item["quantity"], item["defaultQuantity"], item["edited"]), (3, 3, False))


def entry(item_id, product_id, quantity, title=None):
    return {"id": item_id, "productId": product_id, "quantity": quantity, "url": "",
            "product": {"title": title or f"Product {product_id}", "price": 1.0}}


class SyncKeepsEditsTest(AhListsTestCase):
    def quantities(self, snapshot):
        return {item["productId"]: (item["quantity"], item["defaultQuantity"], item["edited"]) for item in snapshot["items"]}

    def test_sync_adds_new_items_and_leaves_edited_quantities_alone(self):
        ah_lists.save_snapshot(live_list("abc", items=[entry("a", 1, 2), entry("b", 2, 1)]))
        ah_lists.set_quantity("abc", 0, 6)
        after = ah_lists.save_snapshot(live_list("abc", items=[entry("a", 1, 2), entry("b", 2, 1), entry("c", 3, 4)]))
        self.assertEqual(self.quantities(after), {1: (6, 2, True), 2: (1, 1, False), 3: (4, 4, False)})
        self.assertEqual(after["sync"], {"new": 1, "removed": 0, "keptQuantities": 1})

    def test_the_same_edit_survives_many_syncs(self):
        live = lambda: live_list("abc", items=[entry("a", 1, 2), entry("b", 2, 1)])
        ah_lists.save_snapshot(live())
        ah_lists.set_quantity("abc", 1, 8)
        for _ in range(4):
            after = ah_lists.save_snapshot(live())
            self.assertEqual(self.quantities(after)[2], (8, 1, True))
            self.assertEqual(after["sync"]["keptQuantities"], 1)

    def test_items_removed_at_ah_disappear_and_are_counted(self):
        ah_lists.save_snapshot(live_list("abc", items=[entry("a", 1, 2), entry("b", 2, 1)]))
        ah_lists.set_quantity("abc", 1, 5)
        after = ah_lists.save_snapshot(live_list("abc", items=[entry("a", 1, 2)]))
        self.assertEqual(list(self.quantities(after)), [1])
        self.assertEqual(after["sync"], {"new": 0, "removed": 1, "keptQuantities": 0})

    def test_an_item_removed_and_added_again_at_ah_keeps_its_edit(self):
        ah_lists.save_snapshot(live_list("abc", items=[entry("a", 1, 2)]))
        ah_lists.set_quantity("abc", 0, 7)
        after = ah_lists.save_snapshot(live_list("abc", items=[entry("new-id", 1, 2)]))  # AH gaf het een nieuw id
        self.assertEqual(self.quantities(after)[1], (7, 2, True))
        self.assertEqual(after["sync"]["new"], 0)

    def test_the_same_product_twice_keeps_each_edit_on_its_own_line(self):
        ah_lists.save_snapshot(live_list("abc", items=[entry("a", 1, 1), entry("b", 1, 1)]))
        ah_lists.set_quantity("abc", 0, 3)
        ah_lists.set_quantity("abc", 1, 9)
        swapped = ah_lists.save_snapshot(live_list("abc", items=[entry("b", 1, 1), entry("a", 1, 1)]))
        self.assertEqual([item["quantity"] for item in swapped["items"]], [9, 3])

    def test_an_unedited_quantity_follows_ah(self):
        ah_lists.save_snapshot(live_list("abc", items=[entry("a", 1, 2)]))
        after = ah_lists.save_snapshot(live_list("abc", items=[entry("a", 1, 5)]))
        self.assertEqual(self.quantities(after)[1], (5, 5, False))
        self.assertEqual(after["sync"]["keptQuantities"], 0)

    def test_an_edited_quantity_stays_when_ah_changes_its_own(self):
        ah_lists.save_snapshot(live_list("abc", items=[entry("a", 1, 2)]))
        ah_lists.set_quantity("abc", 0, 6)
        after = ah_lists.save_snapshot(live_list("abc", items=[entry("a", 1, 3)]))
        self.assertEqual(self.quantities(after)[1], (6, 3, True))  # reset zet hem terug naar 3

    def test_sync_through_the_route_reports_what_happened(self):
        ah_lists.save_snapshot(live_list("abc", items=[entry("a", 1, 2)]))
        ah_lists.set_quantity("abc", 0, 4)
        with mock.patch.object(ah, "get_favorite_list_items", return_value=live_list("abc", items=[entry("a", 1, 2), entry("b", 2, 1)])):
            data = self.client.post("/api/ah/lists/abc/sync").get_json()
        self.assertEqual(data["sync"], {"new": 1, "removed": 0, "keptQuantities": 1})
        self.assertEqual([item["quantity"] for item in data["items"]], [4, 1])


class ResetWarningTest(unittest.TestCase):
    def test_the_reset_button_asks_before_it_resets(self):
        from pathlib import Path

        source = (Path(__file__).resolve().parent.parent / "static" / "app.js").read_text(encoding="utf-8")
        body = source[source.index("async function resetOpenAhList()"):]
        body = body[:body.index("\n}\n")]
        self.assertIn('window.confirm(tn("list.reset_confirm", edited))', body)
        self.assertLess(body.index("window.confirm"), body.index("/reset"))  # eerst vragen, dan terugzetten


class QuantityRoutesTest(AhListsTestCase):
    def test_patch_reset_and_validation(self):
        ah_lists.save_snapshot(live_list())
        response = self.client.patch("/api/ah/lists/abc/items/0", json={"quantity": 4})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["items"][0]["quantity"], 4)
        self.assertEqual(self.client.patch("/api/ah/lists/abc/items/0", json={"quantity": 0}).status_code, 400)
        self.assertEqual(self.client.patch("/api/ah/lists/abc/items/9", json={"quantity": 2}).status_code, 404)
        reset = self.client.post("/api/ah/lists/abc/reset")
        self.assertEqual(reset.get_json()["items"][0]["quantity"], 2)
        self.assertEqual(self.client.post("/api/ah/lists/nope/reset").status_code, 404)


class ListToCartTest(AhListsTestCase):
    def test_whole_list_goes_to_the_basket_with_the_edited_quantities(self):
        ah_lists.save_snapshot(live_list())
        ah_lists.set_quantity("abc", 0, 6)
        with mock.patch.object(ah, "add_to_shopping_list") as add:
            response = self.client.post("/api/ah/lists/abc/cart")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"added": 2, "units": 7, "name": "Favorieten"})
        sent = add.call_args.args[0]
        self.assertEqual([(i["productId"], i["quantity"]) for i in sent], [(10, 6), (11, 1)])
        self.assertEqual(sent[0]["title"], "Melk")

    def test_unsynced_list_is_added_live(self):
        with mock.patch.object(ah, "get_favorite_list_items", return_value=live_list("zzz")), \
                mock.patch.object(ah, "add_to_shopping_list") as add:
            response = self.client.post("/api/ah/lists/zzz/cart")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["added"], 2)
        self.assertEqual(add.call_args.args[0][0]["quantity"], 2)

    def test_empty_list_and_ah_errors(self):
        ah_lists.save_snapshot(live_list(items=[]))
        with mock.patch.object(ah, "add_to_shopping_list") as add:
            self.assertEqual(self.client.post("/api/ah/lists/abc/cart").status_code, 400)
        add.assert_not_called()
        ah_lists.save_snapshot(live_list())
        with mock.patch.object(ah, "add_to_shopping_list", side_effect=RuntimeError("AH account niet gekoppeld")):
            response = self.client.post("/api/ah/lists/abc/cart")
        self.assertEqual(response.status_code, 424)
        self.assertEqual(response.get_json()["error"], "AH account niet gekoppeld")


if __name__ == "__main__":
    unittest.main()
