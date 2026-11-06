import os
import tempfile
import threading
import unittest
from typing import NoReturn
from unittest import mock

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

from mealieshopper import ah  # noqa: E402
from mealieshopper.i18n import tr  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402
from support import SyncClient, run  # noqa: E402


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


def ah_product(webshop_id, title, brand="AH", bundle=False):
    return {"webshopId": webshop_id, "title": title, "brand": brand, "isVirtualBundle": bundle,
            "currentPrice": 1.0, "salesUnitSize": "500 g", "images": []}


class SearchRankTest(unittest.TestCase):
    """Issue #175: AH's eigen volgorde zette een multipack guacamolekruiden boven "AH Penne"."""

    # Zo gaf AH ze echt terug voor "AH Penne" (ingekort): de multipack stond door AH onder Penne.
    PENNE = [
        ah_product(578640, "AH Guacamole seasoning mix 3-pack", bundle=True),
        ah_product(543604, "AH Biologisch Mini penne volkoren", brand="AH Biologisch"),
        ah_product(594833, "AH Proteine penne"),
        ah_product(1, "Grand' Italia Penne tradizionali", brand="Grand' Italia"),
        ah_product(62027, "AH Penne"),
    ]

    def search(self, query, products, limit=5):
        answer = mock.Mock(ok=True)
        answer.json.return_value = {"products": products}
        with mock.patch.object(ah, "get_token", return_value="tok"), \
                mock.patch.object(ah.requests, "get", return_value=answer) as get:
            found = ah.search_products(query, limit)
        return found, get.call_args.kwargs["params"]

    def titles(self, query, products):
        return [item["title"] for item in ah.rank_search_results(query, products)]

    def test_the_product_with_the_ingredients_name_comes_first(self):
        found, params = self.search("AH Penne", self.PENNE, 1)
        self.assertEqual([item["webshopId"] for item in found], ["62027"])
        self.assertEqual(params["size"], ah.SEARCH_WINDOW)  # AH zette AH Penne op plek 9: vraag er meer

    def test_ahs_own_brand_may_be_left_out(self):
        self.assertEqual(self.titles("penne", self.PENNE)[0], "AH Penne")
        spaghetti = [ah_product(1, "Knorr Maaltijdmix spaghetti bolognese", brand="Knorr"),
                     ah_product(2, "AH Biologisch Spaghetti", brand="AH Biologisch")]
        self.assertEqual(self.titles("spaghetti", spaghetti)[0], "AH Biologisch Spaghetti")

    def test_another_brand_is_not_an_exact_match(self):
        paprika = [ah_product(1, "AH Paprika mild gemalen"), ah_product(2, "Pringles Paprika", brand="Pringles")]
        self.assertEqual(self.titles("paprika", paprika), ["AH Paprika mild gemalen", "Pringles Paprika"])

    def test_case_and_accents_do_not_matter(self):
        products = [ah_product(1, "AH Creme fraiche light 15% vet"), ah_product(2, "AH Crème Fraîche")]
        self.assertEqual(self.titles("crème fraîche", products)[0], "AH Crème Fraîche")
        self.assertEqual(self.titles("Creme Fraiche", products)[0], "AH Crème Fraîche")

    def test_a_multipack_comes_after_single_products_in_ahs_order(self):
        products = [
            ah_product(1, "AH Scharrel kipfilet blokjes 3-pack", bundle=True),
            ah_product(2, "AH Scharrel kipfilet naturel"),
            ah_product(3, "AH Scharrel roasted kipfilet 2-pack", bundle=True),
            ah_product(4, "AH Scharrel kipfilet 2 stuks"),
        ]
        self.assertEqual(
            [item["webshopId"] for item in ah.rank_search_results("kipfilet", products)], [2, 4, 1, 3]
        )

    def test_otherwise_ahs_order_stays(self):
        # Milner is kaas zonder "kaas" in de naam: woorden in de titel tellen niet mee.
        products = [ah_product(1, "Milner Jong belegen 35+ plakken", brand="Milner"),
                    ah_product(2, "AH Kruiden roomkaas")]
        self.assertEqual(self.titles("kaas", products), ["Milner Jong belegen 35+ plakken", "AH Kruiden roomkaas"])
        self.assertEqual(self.titles("", self.PENNE)[-1], "AH Guacamole seasoning mix 3-pack")

    # Zo gaf AH ze echt terug voor "eieren" (4 november 2026, ingekort). AH zoekt ruim en vindt ook "aardbeien"; de eieren staan
    # bovenaan, maar bijna allemaal als multipack.
    EGGS = [
        ah_product(1, "AH Biologisch Eieren S M L 4-pack", brand="AH Biologisch", bundle=True),
        ah_product(2, "AH Nederlandse aardbeien"),
        ah_product(3, "AH Vloeibaar eiwit"),
        ah_product(4, "AH Excellent Gekleurde scharreleieren 3-pack", brand="AH Excellent", bundle=True),
        ah_product(5, "Melkunie Breaker aardbei yoghurt", brand="Melkunie"),
        ah_product(6, "Johma Ei salade", brand="Johma"),
        ah_product(7, "AH Verse scharreleieren M L 3-pack", bundle=True),
        ah_product(8, "AH Brabantse eierkoeken 4-pack", bundle=True),
        ah_product(9, "AH Witte vrije uitloopeieren M"),
        ah_product(10, "AH Biologisch Eieren S M L", brand="AH Biologisch"),
        ah_product(11, "AH Verse scharreleieren M L"),
    ]

    def test_a_multipack_of_the_ingredient_on_top_puts_single_ones_first(self):
        # Issue #220: zonder de multipacks kwamen de aardbeien, het eiwit en de aardbeiyoghurt voor de eieren.
        found, _ = self.search("eieren", self.EGGS, 1)
        self.assertEqual(found[0]["title"], "AH Witte vrije uitloopeieren M")
        self.assertEqual(
            [item["webshopId"] for item in ah.rank_search_results("eieren", self.EGGS)],
            [9, 10, 11, 2, 3, 5, 6, 1, 4, 7, 8],
        )
        self.assertEqual(self.titles("AH Eieren", self.EGGS)[0], "AH Witte vrije uitloopeieren M")

    def test_only_a_multipack_of_the_ingredient_on_top_counts(self):
        milner = ah_product(1, "Milner Jong belegen 35+ plakken", brand="Milner")
        roomkaas = ah_product(2, "AH Kruiden roomkaas")
        cheese = ah_product(3, "AH Jong belegen kaas plakken 3-pack", bundle=True)
        guacamole = ah_product(4, "AH Guacamole seasoning mix 3-pack", bundle=True)
        ids = lambda products: [item["webshopId"] for item in ah.rank_search_results("kaas", products)]
        # Een multipack van iets anders bovenaan, of een los product boven de multipack kaas: de volgorde van AH.
        self.assertEqual(ids([guacamole, milner, roomkaas]), [1, 2, 4])
        self.assertEqual(ids([milner, cheese, roomkaas]), [1, 2, 3])

    @staticmethod
    def meat(webshop_id, title, category, brand="AH", bundle=False):
        return {**ah_product(webshop_id, title, brand=brand, bundle=bundle), "mainCategory": category}

    def test_fresh_meat_comes_before_cold_cuts(self):
        # Issue #226: zo gaf AH ze echt terug voor "kipfilet" (4 november 2026, ingekort). Vleeswaren en multipacks bovenaan, en
        # "AH Biologisch Kipfilet" (vleeswaren) heet precies "kipfilet"; de rauwe kipfilet stond pas op plek 11.
        chicken = [
            self.meat(1, "AH Scharrel kipfilet blokjes 3-pack", "Vlees", bundle=True),
            self.meat(2, "AH Scharrel roasted kipfilet 2-pack", "Vleeswaren", bundle=True),
            self.meat(3, "AH Scharrel kipfilet naturel", "Vleeswaren"),
            self.meat(4, "AH Biologisch Kipfilet", "Vleeswaren", brand="AH Biologisch"),
            self.meat(5, "AH Scharrel kipfilet 2 stuks", "Vlees"),
            self.meat(6, "AH Scharrel kipfilet 3 stuks", "Vlees"),
        ]
        self.assertEqual([item["webshopId"] for item in ah.rank_search_results("kipfilet", chicken)], [5, 6, 1, 4, 3, 2])

    def test_cold_cuts_stay_when_no_fresh_meat_names_the_ingredient(self):
        ids = lambda query, products: [item["webshopId"] for item in ah.rank_search_results(query, products)]
        # Ham en rosbief koop je als vleeswaren: geen los stuk vers vlees noemt het ingrediënt, dus de volgorde blijft.
        ham = [self.meat(1, "AH Schouderham 2-pack", "Vleeswaren", bundle=True), self.meat(2, "AH Achterham", "Vleeswaren"),
               self.meat(3, "AH Hamlappen", "Vlees")]
        self.assertEqual(ids("achterham", ham)[0], 2)
        # Alleen een multipack vers vlees noemt het ingrediënt: dat telt niet, de gerookte plakjes blijven voorop.
        smoked = [self.meat(1, "AH Gerookte kipfilet", "Vleeswaren"),
                  self.meat(2, "AH Scharrel gerookte kipfiletflakes 3-pack", "Vlees", bundle=True)]
        self.assertEqual(ids("gerookte kipfilet", smoked), [1, 2])
        # Zonder categorie (oude gegevens) verandert er niets.
        plain = [ah_product(1, "AH Biologisch Kipfilet", brand="AH Biologisch"), ah_product(2, "AH Scharrel kipfilet 2 stuks")]
        self.assertEqual(ids("kipfilet", plain), [1, 2])

    def test_the_limit_is_kept(self):
        found, params = self.search("AH Penne", self.PENNE, 2)
        self.assertEqual([item["title"] for item in found], ["AH Penne", "AH Biologisch Mini penne volkoren"])
        found, params = self.search("AH Penne", self.PENNE, 50)
        self.assertEqual(params["size"], 50)
        self.assertEqual(found[-1]["title"], "AH Guacamole seasoning mix 3-pack")


class OrganicFlagTest(unittest.TestCase):
    """#218: AH zegt zelf of een product biologisch is, met het kenmerk "biologisch" in `propertyIcons`."""

    def test_the_organic_property_icon(self):
        for icons, expected in ((["vegan", "biologisch"], True), (["Biologisch"], True), (["vegan"], False), (None, False), ([{"x": 1}], False)):
            item = {**ah_product(1, "AH Spaghetti"), "propertyIcons": icons}
            self.assertEqual(ah._search_product_dict(item, "spaghetti")["organic"], expected, icons)

    def test_the_main_category_comes_along(self):
        # #223: de controlelijst vergelijkt bij AH alleen binnen dezelfde hoofdcategorie.
        item = {**ah_product(1, "Van Gilse Fijne kristalsuiker"), "mainCategory": "Maaltijden, salades", "subCategory": "Suiker"}
        self.assertEqual(ah._search_product_dict(item, "suiker")["category"], "Maaltijden, salades")
        self.assertIsNone(ah._search_product_dict(ah_product(2, "AH Penne"), "penne")["category"])


class CartRouteTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"MEALIESHOPPER_DATA_DIR": tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = SyncClient(create_app())

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
        self.client = SyncClient(create_app())

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
        self.assertEqual(eggs["candidates"][0]["imageLarge"], "https://img/1.jpg")
        self.assertEqual(cheese["candidates"][0]["doubt"], "Verpakkingsgrootte onbekend")

    def test_large_photo_is_the_size_closest_to_800_pixels(self):
        # De foto in het groot (het venster bij de controlelijst) neemt het formaat van AH dat het best past.
        found = search_result(1, "AH Eieren 10", "10 stuks")
        found["images"] = [{"url": f"https://img/1-{width}.jpg", "width": width} for width in (48, 200, 400, 800, 1600)]
        response = self.preview([{"query": "eieren", "quantity": 1}], lambda query, limit: [found])
        candidate = response.get_json()["items"][0]["candidates"][0]
        self.assertEqual(candidate["imageLarge"], "https://img/1-800.jpg")

    def test_no_photos_means_no_large_photo(self):
        found = search_result(1, "AH Eieren 10", "10 stuks")
        found["images"] = []
        response = self.preview([{"query": "eieren", "quantity": 1}], lambda query, limit: [found])
        candidate = response.get_json()["items"][0]["candidates"][0]
        self.assertIsNone(candidate["image"])
        self.assertIsNone(candidate["imageLarge"])

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

    def test_a_single_row_can_be_searched_again_under_another_name(self):
        # De controlelijst zoekt een niet gevonden regel opnieuw met de zoekterm van de gebruiker. Het antwoord
        # moet dan de oorspronkelijke hoeveelheid en eenheid meegeven, en de verpakkingen daarvan berekenen.
        def search(query, limit):
            return [search_result(9, "AH Pak choi", "250 g")] if query == "pak choi" else []

        response = self.preview(
            [{"query": "pak choi", "display": "500 g paksoi", "quantity": 500, "unit": "g"}], search
        )
        (row,) = response.get_json()["items"]
        self.assertEqual((row["query"], row["display"], row["quantity"], row["unit"]), ("pak choi", "500 g paksoi", 500, "g"))
        self.assertEqual([(c["productId"], c["packs"]) for c in row["candidates"]], [("9", 2)])

    def test_requires_items(self):
        response = self.client.post("/api/ah/cart/preview", json={"items": []})
        self.assertEqual(response.status_code, 400)

    def test_order_is_preserved(self):
        queries = [f"p{n}" for n in range(12)]
        response = self.preview(
            [{"query": q, "quantity": 1} for q in queries],
            lambda query, limit: [search_result(int(query[1:]) + 1, query, "per stuk")],
        )
        rows = response.get_json()["items"]
        self.assertEqual([i["query"] for i in rows], queries)
        self.assertEqual([[c["productId"] for c in row["candidates"]] for row in rows], [[str(n + 1)] for n in range(12)])

    def test_searches_run_inside_the_request(self) -> None:
        # Geen threadpool meer: elke zoekopdracht draait in de event loop van het verzoek en ziet dus ook de taal van de
        # gebruiker. Hoeveel er tegelijk lopen (hooguit zes) toetst Taak 21, als de winkelmodules async zijn.
        threads: set[int] = set()

        def search(query: str, limit: int) -> NoReturn:
            threads.add(threading.get_ident())
            raise RuntimeError(tr("Zoeken mislukt", "Search failed"))

        with mock.patch.object(ah, "search_products", side_effect=search):
            response = self.client.post(
                "/api/ah/cart/preview", json={"items": [{"query": f"p{n}"} for n in range(8)]}, headers={"Cookie": "ms_lang=en"}
            )
        self.assertEqual([row["error"] for row in response.get_json()["items"]], ["Search failed"] * 8)
        self.assertEqual(threads, {threading.get_ident()})


if __name__ == "__main__":
    unittest.main()


class DoubtLanguageTest(unittest.TestCase):
    """De twijfelmeldingen bij het winkelmandje volgen de taal van de gebruiker."""

    def doubt(self, lang, *args):
        from mealieshopper.app import create_app

        app = create_app()

        async def assess():
            async with app.test_request_context("/", headers={"Cookie": f"ms_lang={lang}"}):
                return ah.assess_packs(*args)[1]

        return run(assess())

    def test_unknown_pack_size(self):
        self.assertEqual(self.doubt("nl", 500, "g", None), "Verpakkingsgrootte onbekend")
        self.assertEqual(self.doubt("en", 500, "g", None), "Pack size unknown")

    def test_mismatching_unit(self):
        self.assertEqual(self.doubt("nl", 500, "g", "1 l"), "g past niet bij de verpakking (1 l)")
        self.assertEqual(self.doubt("en", 500, "g", "1 l"), "g doesn't match the pack (1 l)")

    def test_large_number_of_packs(self):
        self.assertEqual(self.doubt("nl", 5000, "g", "250 g"), "Groot aantal verpakkingen")
        self.assertEqual(self.doubt("en", 5000, "g", "250 g"), "Large number of packs")
