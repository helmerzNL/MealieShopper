import os
import tempfile
import unittest
from unittest import mock

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

from mealieshopper import ah  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402


class NormalizeQuantityTest(unittest.TestCase):
    def test_values(self):
        cases = {
            500: 99, 99: 99, 100: 99, 98: 98, 1: 1, 0: 1, -3: 1,
            2.6: 3, "7": 7, "500": 99, None: 1, "": 1, "abc": 1, float("nan"): 1,
        }
        for value, expected in cases.items():
            self.assertEqual(ah.normalize_quantity(value), expected, repr(value))


class ParsePackSizeTest(unittest.TestCase):
    def test_sizes(self):
        cases = {
            "6 stuks": (6, "count"), "10 stuks": (10, "count"), "per stuk": (1, "count"),
            "1 stuk": (1, "count"), "500 g": (500, "mass"), "1 kg": (1000, "mass"),
            "ca. 400 g": (400, "mass"), "4 x 125 g": (500, "mass"), "1 l": (1000, "volume"),
            "0,5 l": (500, "volume"), "330 ml": (330, "volume"), "75 cl": (750, "volume"),
        }
        for text, expected in cases.items():
            self.assertEqual(ah.parse_pack_size(text), expected, text)

    def test_unknown(self):
        for text in (None, "", "doos", "naar smaak"):
            self.assertIsNone(ah.parse_pack_size(text), repr(text))


class PacksNeededTest(unittest.TestCase):
    def test_eggs_by_the_pack(self):
        self.assertEqual(ah.packs_needed(6, None, "6 stuks"), 1)
        self.assertEqual(ah.packs_needed(6, None, "10 stuks"), 1)
        self.assertEqual(ah.packs_needed(8, None, "6 stuks"), 2)
        self.assertEqual(ah.packs_needed(8, None, "10 stuks"), 1)
        self.assertEqual(ah.packs_needed(12, "stuks", "10 stuks"), 2)

    def test_weight_and_volume(self):
        self.assertEqual(ah.packs_needed(500, "gram", "1 kg"), 1)
        self.assertEqual(ah.packs_needed(1500, "g", "1 kg"), 2)
        self.assertEqual(ah.packs_needed(500, "g", "250 g"), 2)
        self.assertEqual(ah.packs_needed(750, "g", "4 x 125 g"), 2)
        self.assertEqual(ah.packs_needed(1, "kg", "500 g"), 2)
        self.assertEqual(ah.packs_needed(0.5, "liter", "1 l"), 1)
        self.assertEqual(ah.packs_needed(300, "ml", "1 l"), 1)

    def test_single_items_are_counted(self):
        self.assertEqual(ah.packs_needed(3, None, "per stuk"), 3)
        self.assertEqual(ah.packs_needed(2, "stuk", "1 stuk"), 2)

    def test_unmeasurable_is_one_pack(self):
        self.assertEqual(ah.packs_needed(2, "el", "500 g"), 1)
        self.assertEqual(ah.packs_needed(3, "teentje", "1 stuk"), 1)
        self.assertEqual(ah.packs_needed(2, None, "1 kg"), 1)      # "2 uien", verkocht per gewicht
        self.assertEqual(ah.packs_needed(500, "g", "6 stuks"), 1)  # soort komt niet overeen
        self.assertEqual(ah.packs_needed(500, "g", None), 1)

    def test_unknown_pack_size_counts_plain_numbers(self):
        self.assertEqual(ah.packs_needed(6, None, None), 6)

    def test_bad_input_and_limits(self):
        self.assertEqual(ah.packs_needed(None, None, "6 stuks"), 1)
        self.assertEqual(ah.packs_needed(0, "g", "250 g"), 1)
        self.assertEqual(ah.packs_needed(-5, None, "6 stuks"), 1)
        self.assertEqual(ah.packs_needed("abc", None, "6 stuks"), 1)
        self.assertEqual(ah.packs_needed(100000, "g", "10 g"), 99)
        self.assertEqual(ah.packs_needed(250, None, "1 stuk"), 99)


class AddToShoppingListTest(unittest.TestCase):
    def test_quantities_are_clamped_in_the_payload(self):
        ok = mock.Mock(ok=True)
        with mock.patch.object(ah, "get_user_token", return_value="tok"), \
                mock.patch.object(ah.requests, "patch", return_value=ok) as patch:
            ah.add_to_shopping_list([
                {"productId": 1, "quantity": 500, "query": "aardappelen"},
                {"productId": 2, "quantity": 0, "query": "melk"},
                {"productId": 3, "quantity": "4", "query": "eieren"},
            ])
        quantities = [item["quantity"] for item in patch.call_args.kwargs["json"]["items"]]
        self.assertEqual(quantities, [99, 1, 4])


class CartRouteTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"MEALIESHOPPER_DATA_DIR": tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = create_app().test_client()

    def post(self, items, unit_size):
        product = {"webshopId": "42", "title": "Product", "unitSize": unit_size,
                   "price": {"now": 2.0}, "images": []}
        with mock.patch.object(ah, "search_product", return_value=product), \
                mock.patch.object(ah, "add_to_shopping_list") as add:
            response = self.client.post("/api/ah/cart", json={"items": items})
        return response, add

    def test_eggs_become_one_pack(self):
        response, add = self.post([{"query": "eieren", "quantity": 6, "unit": None}], "10 stuks")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(add.call_args.args[0], [{"productId": "42", "quantity": 1}])
        self.assertEqual(response.get_json()["items"][0]["quantity"], 1)

    def test_weight_uses_the_pack_size(self):
        _, add = self.post([{"query": "kaas", "quantity": 500, "unit": "gram"}], "200 g")
        self.assertEqual(add.call_args.args[0], [{"productId": "42", "quantity": 3}])

    def test_oversized_amounts_never_exceed_99(self):
        _, add = self.post([{"query": "iets", "quantity": 100000, "unit": "g"}], "10 g")
        self.assertEqual(add.call_args.args[0][0]["quantity"], 99)

    def test_old_clients_without_unit_still_work(self):
        _, add = self.post([{"query": "melk", "quantity": 2}], None)
        self.assertEqual(add.call_args.args[0], [{"productId": "42", "quantity": 2}])


if __name__ == "__main__":
    unittest.main()
