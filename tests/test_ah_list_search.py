import os
import tempfile
import unittest
from unittest import mock

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

from mealieshopper import ah_lists  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402


def item(n, title, brand="AH"):
    return {"id": f"i{n}", "productId": n, "url": "", "quantity": 1,
            "product": {"id": n, "title": title, "brand": brand, "unitSize": "1 st", "price": 1, "image": "", "isBonus": False}}


class ListSearchTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(os.environ, {"MEALIESHOPPER_DATA_DIR": tmp.name})
        env.start()
        self.addCleanup(env.stop)
        self.client = create_app().test_client()
        ah_lists.save_snapshot({"id": "a", "name": "Weekboodschappen", "items": [
            item(1, "AH Halfvolle melk"), item(2, "Bio Café crème", brand="Douwe Egberts"), item(3, "Pindakaas")]})
        ah_lists.save_snapshot({"id": "b", "name": "Borrel", "items": [item(4, "Nootjes"), item(5, "AH Melkchocolade")]})

    def test_finds_lists_by_product_title(self):
        self.assertEqual(ah_lists.search_items("melk"), {"A": ["AH Halfvolle melk"], "B": ["AH Melkchocolade"]})

    def test_all_words_must_match_the_same_product(self):
        self.assertEqual(ah_lists.search_items("halfvolle melk"), {"A": ["AH Halfvolle melk"]})
        self.assertEqual(ah_lists.search_items("nootjes melk"), {})

    def test_case_and_accents_do_not_matter_and_brand_counts(self):
        self.assertEqual(list(ah_lists.search_items("CAFE")), ["A"])
        self.assertEqual(list(ah_lists.search_items("douwe")), ["A"])

    def test_empty_query_and_no_match(self):
        self.assertEqual(ah_lists.search_items("  "), {})
        self.assertEqual(ah_lists.search_items("zzz"), {})

    def test_hits_per_list_are_capped(self):
        ah_lists.save_snapshot({"id": "c", "name": "Veel", "items": [item(n, f"Appel {n}") for n in range(10, 20)]})
        self.assertEqual(len(ah_lists.search_items("appel")["C"]), 3)

    def test_route_is_local_only(self):
        with mock.patch.object(ah_lists.ah, "get_favorite_lists", side_effect=AssertionError("geen AH-aanroep")):
            res = self.client.get("/api/ah/lists/search?q=melk")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()["items"]["A"], ["AH Halfvolle melk"])
        self.assertEqual(self.client.get("/api/ah/lists/search").get_json()["items"], {})


if __name__ == "__main__":
    unittest.main()
