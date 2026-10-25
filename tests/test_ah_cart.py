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


class AssessPacksTest(unittest.TestCase):
    def test_clear_cases_have_no_doubt(self):
        for args, packs in (
            ((6, None, "10 stuks"), 1),
            ((8, None, "6 stuks"), 2),
            ((500, "g", "250 g"), 2),
            ((2, "el", "500 g"), 1),       # olie: een fles
            ((3, "teentje", "1 stuk"), 1),
            ((2, None, "1 kg"), 1),        # "2 uien" bij gewicht
            ((None, "tl", "100 g"), 1),    # onbekende hoeveelheid zout
            ((3, None, "per stuk"), 3),
        ):
            self.assertEqual(ah.assess_packs(*args), (packs, None), args)

    def test_unknown_pack_size_is_doubtful(self):
        self.assertEqual(ah.assess_packs(6, None, None), (6, "Verpakkingsgrootte onbekend"))
        self.assertEqual(ah.assess_packs(500, "g", ""), (1, "Verpakkingsgrootte onbekend"))

    def test_weight_against_count_is_doubtful(self):
        packs, doubt = ah.assess_packs(500, "g", "6 stuks")
        self.assertEqual(packs, 1)
        self.assertIn("past niet bij de verpakking (6 stuks)", doubt)
        packs, doubt = ah.assess_packs(300, "ml", "500 g")
        self.assertEqual(packs, 1)
        self.assertIsNotNone(doubt)

    def test_large_numbers_are_doubtful(self):
        self.assertEqual(ah.assess_packs(11, None, "per stuk"), (11, "Groot aantal verpakkingen"))
        self.assertEqual(ah.assess_packs(10, None, "per stuk"), (10, None))
        packs, doubt = ah.assess_packs(100000, "g", "10 g")
        self.assertEqual((packs, doubt), (99, "Groot aantal verpakkingen"))

    def test_packs_needed_matches(self):
        self.assertEqual(ah.packs_needed(8, None, "6 stuks"), ah.assess_packs(8, None, "6 stuks")[0])


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

    def test_the_same_product_is_sent_once_with_the_quantities_added_up(self):
        ok = mock.Mock(ok=True)
        with mock.patch.object(ah, "get_user_token", return_value="tok"), \
                mock.patch.object(ah.requests, "patch", return_value=ok) as patch:
            ah.add_to_shopping_list([
                {"productId": 7, "quantity": 1, "query": "melk"},
                {"productId": 8, "quantity": 2, "query": "eieren"},
                {"productId": "7", "quantity": 2, "title": "AH Halfvolle melk"},
                {"productId": 8, "quantity": 99, "query": "eieren"},
            ])
        items = patch.call_args.kwargs["json"]["items"]
        self.assertEqual([item["productId"] for item in items], [7, 8])
        self.assertEqual([item["quantity"] for item in items], [3, 99])
        self.assertEqual(items[0]["description"], "melk")


    def test_an_open_order_gets_a_readable_message(self):
        refused = mock.Mock(ok=False, status_code=412, text='{"status":412,"message":"Server in order mode"}')
        with mock.patch.object(ah, "get_user_token", return_value="tok"), \
                mock.patch.object(ah.requests, "patch", return_value=refused):
            with self.assertRaises(RuntimeError) as caught:
                ah.add_to_shopping_list([{"productId": 1, "quantity": 1}])
        self.assertIn("bestelling open", str(caught.exception))
        self.assertIn("ander apparaat", str(caught.exception))
        self.assertIn("412", str(caught.exception))

    def test_other_errors_keep_the_raw_answer(self):
        refused = mock.Mock(ok=False, status_code=500, text="kapot")
        with mock.patch.object(ah, "get_user_token", return_value="tok"), \
                mock.patch.object(ah.requests, "patch", return_value=refused):
            with self.assertRaisesRegex(RuntimeError, r"\(500\): kapot"):
                ah.add_to_shopping_list([{"productId": 1, "quantity": 1}])


class SearchRetryTest(unittest.TestCase):
    def search(self, side_effect):
        with mock.patch.object(ah, "get_token", return_value="tok"), \
                mock.patch.object(ah.time, "sleep"), \
                mock.patch.object(ah.requests, "get", side_effect=side_effect) as get:
            try:
                return ah.search_products("melk", 1), get
            except Exception as exc:  # noqa: BLE001
                return exc, get

    def test_a_dropped_connection_is_tried_again(self):
        answer = mock.Mock(ok=True)
        answer.json.return_value = {"products": []}
        result, get = self.search([ah.requests.ConnectionError("reset"), answer])
        self.assertEqual(result, [])
        self.assertEqual(get.call_count, 2)

    def test_it_gives_up_after_three_attempts(self):
        result, get = self.search(ah.requests.ConnectionError("reset"))
        self.assertIsInstance(result, ah.requests.ConnectionError)
        self.assertEqual(get.call_count, 3)

    def test_an_error_answer_is_not_retried(self):
        result, get = self.search([mock.Mock(ok=False, status_code=500)])
        self.assertIsInstance(result, RuntimeError)
        self.assertEqual(get.call_count, 1)


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

    def test_confirmed_products_skip_the_search(self):
        with mock.patch.object(ah, "search_product", side_effect=AssertionError("niet zoeken")), \
                mock.patch.object(ah, "add_to_shopping_list") as add:
            response = self.client.post("/api/ah/cart", json={"items": [
                {"query": "eieren", "productId": 7, "quantity": 500, "title": "AH Eieren 10 stuks",
                 "price": 2.99, "unitSize": "10 stuks", "image": "https://img/7.jpg"},
            ]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(add.call_args.args[0], [{"productId": 7, "quantity": 99}])
        item = response.get_json()["items"][0]
        self.assertEqual(item["quantity"], 99)
        self.assertEqual(item["product"]["title"], "AH Eieren 10 stuks")
        self.assertEqual(item["product"]["unitSize"], "10 stuks")


def search_result(webshop_id, title, unit_size, price=1.5):
    return {
        "webshopId": str(webshop_id), "title": title, "brand": "AH", "unitSize": unit_size,
        "price": {"now": price, "unitSize": unit_size}, "images": [{"url": f"https://img/{webshop_id}.jpg"}],
        "isBonus": False,
    }


class CartPreviewRouteTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"MEALIESHOPPER_DATA_DIR": tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = create_app().test_client()

    def preview(self, items, search):
        with mock.patch.object(ah, "search_products", side_effect=search):
            return self.client.post("/api/ah/cart/preview", json={"items": items})

    def test_candidates_carry_packs_and_doubt(self):
        results = {
            "eieren": [search_result(1, "AH Eieren 10", "10 stuks"), search_result(2, "AH Eieren 6", "6 stuks")],
            "kaas": [search_result(3, "Kaas plak", None)],
        }
        response = self.preview(
            [
                {"query": "eieren", "display": "8 eieren", "quantity": 8, "unit": None},
                {"query": "kaas", "display": "200 g kaas", "quantity": 200, "unit": "g"},
            ],
            lambda query, limit: results[query],
        )
        self.assertEqual(response.status_code, 200)
        eggs, cheese = response.get_json()["items"]
        self.assertEqual(eggs["display"], "8 eieren")
        self.assertEqual([(c["productId"], c["packs"], c["doubt"]) for c in eggs["candidates"]],
                         [("1", 1, None), ("2", 2, None)])
        self.assertEqual(eggs["candidates"][0]["url"], "https://www.ah.nl/producten/product/wi1")
        self.assertEqual(eggs["candidates"][0]["image"], "https://img/1.jpg")
        self.assertEqual(cheese["candidates"][0]["doubt"], "Verpakkingsgrootte onbekend")

    def test_not_found_and_errors_are_per_row(self):
        def search(query, limit):
            if query == "kapot":
                raise RuntimeError("AH productzoekopdracht mislukt (500)")
            return []

        response = self.preview(
            [{"query": "niets", "quantity": 1}, {"query": "kapot", "quantity": 1}], search
        )
        self.assertEqual(response.status_code, 200)
        nothing, broken = response.get_json()["items"]
        self.assertEqual(nothing["candidates"], [])
        self.assertNotIn("error", nothing)
        self.assertEqual(broken["error"], "AH productzoekopdracht mislukt (500)")

    def test_requires_items(self):
        response = self.client.post("/api/ah/cart/preview", json={"items": []})
        self.assertEqual(response.status_code, 400)

    def test_order_is_preserved(self):
        queries = [f"p{n}" for n in range(12)]
        response = self.preview(
            [{"query": q, "quantity": 1} for q in queries],
            lambda query, limit: [search_result(int(query[1:]) + 1, query, "per stuk")],
        )
        self.assertEqual([i["query"] for i in response.get_json()["items"]], queries)


if __name__ == "__main__":
    unittest.main()


class DoubtLanguageTest(unittest.TestCase):
    """De twijfelmeldingen bij het winkelmandje volgen de taal van de gebruiker."""

    def doubt(self, lang, *args):
        from mealieshopper.app import create_app

        app = create_app()
        with app.test_request_context("/", headers={"Cookie": f"ms_lang={lang}"}):
            return ah.assess_packs(*args)[1]

    def test_unknown_pack_size(self):
        self.assertEqual(self.doubt("nl", 500, "g", None), "Verpakkingsgrootte onbekend")
        self.assertEqual(self.doubt("en", 500, "g", None), "Pack size unknown")

    def test_mismatching_unit(self):
        self.assertEqual(self.doubt("nl", 500, "g", "1 l"), "g past niet bij de verpakking (1 l)")
        self.assertEqual(self.doubt("en", 500, "g", "1 l"), "g doesn't match the pack (1 l)")

    def test_large_number_of_packs(self):
        self.assertEqual(self.doubt("nl", 5000, "g", "250 g"), "Groot aantal verpakkingen")
        self.assertEqual(self.doubt("en", 5000, "g", "250 g"), "Large number of packs")
