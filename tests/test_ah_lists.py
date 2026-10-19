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


if __name__ == "__main__":
    unittest.main()
