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

    def test_oversized_quantity_is_clamped_before_it_reaches_ah(self):
        product = {"webshopId": "42", "title": "Aardappelen", "price": {"now": 2.0}, "images": []}
        with mock.patch.object(ah, "search_product", return_value=product), \
                mock.patch.object(ah, "add_to_shopping_list") as add:
            response = self.client.post(
                "/api/ah/cart", json={"items": [{"query": "aardappelen", "quantity": 500}]}
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(add.call_args.args[0], [{"productId": "42", "quantity": 99}])
        self.assertEqual(response.get_json()["items"][0]["quantity"], 99)


if __name__ == "__main__":
    unittest.main()
