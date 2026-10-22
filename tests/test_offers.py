import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("MEALIESHOPPER_DATA_DIR", tempfile.mkdtemp())

from mealieshopper import ah, ah_recipes, auth, offers  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402

BONUS_PAGE = {
    "bonusGroupOrProducts": [
        {"bonusGroup": {"discountDescription": "2 voor 3", "products": [
            {"webshopId": 11, "title": "AH Tomaten rond", "brand": "AH", "salesUnitSize": "500 g",
             "currentPrice": 1.5, "priceBeforeBonus": 2.0, "isBonus": True, "images": [{"url": "https://img/t.png"}]},
        ]}},
        {"product": {"webshopId": 12, "title": "Kipfilet", "salesUnitSize": "400 g", "currentPrice": 3.0, "priceBeforeBonus": 4.5,
                     "discountLabels": [{"defaultDescription": "1+1 gratis"}]}},
        {"product": {"webshopId": 13, "title": "Tomatenpuree", "currentPrice": 0.9, "priceBeforeBonus": 1.2}},
    ]
}


class TempDataDirTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"MEALIESHOPPER_DATA_DIR": tmp.name, "PASSKEY_AUTH_ENABLED": "false"})
        patcher.start()
        self.addCleanup(patcher.stop)
        auth.init_db()


class MatchingTest(unittest.TestCase):
    def test_plural_and_double_vowels_match(self):
        for ingredient, title in (("tomaten", "AH Tomaat rond"), ("2 uien", "Ui geel"), ("6 eieren", "Ei"), ("kippen", "Kip")):
            head = offers.head_of(ingredient)
            self.assertTrue(offers.matches(head, set(offers.tokens(title))), (ingredient, title))

    def test_quantities_units_and_pantry_are_ignored(self):
        self.assertEqual(offers.tokens("400 g rode uien (gesneden)"), ["rode", "ui"])
        self.assertIsNone(offers.head_of("zout en peper"))
        self.assertIsNone(offers.head_of("water"))

    def test_derived_products_are_not_the_ingredient(self):
        self.assertFalse(offers.matches(offers.head_of("tomaat"), set(offers.tokens("Tomatenpuree 140 g"))))
        self.assertFalse(offers.matches(offers.head_of("kip"), set(offers.tokens("Kippensoep"))))
        self.assertTrue(offers.matches(offers.head_of("kip"), set(offers.tokens("Kipfilet 500 g"))))


class BonusParsingTest(unittest.TestCase):
    def test_products_are_found_by_shape_with_their_label_and_prices(self):
        products = {p["id"]: p for p in ah.bonus_products_in(BONUS_PAGE)}
        self.assertEqual(sorted(products), ["11", "12", "13"])
        tomato = products["11"]
        self.assertEqual((tomato["price"], tomato["priceBefore"], tomato["promoLabel"]), (1.5, 2.0, "2 voor 3"))
        self.assertEqual(tomato["image"], "https://img/t.png")
        self.assertEqual(products["12"]["promoLabel"], "1+1 gratis")

    def test_falls_back_on_search_when_the_page_has_no_products(self):
        search = {"products": [{"webshopId": 5, "title": "Pasta", "currentPrice": 1.0, "priceBeforeBonus": 1.5, "isBonus": True},
                               {"webshopId": 6, "title": "Rijst", "currentPrice": 1.0, "isBonus": False}]}

        def fake_request(method, url, **kwargs):
            raise RuntimeError("404: niet gevonden")

        response = mock.Mock(ok=True)
        response.json.return_value = search
        with mock.patch.object(ah, "get_token", return_value="t"), \
                mock.patch.object(ah, "_request_json", side_effect=fake_request), \
                mock.patch.object(ah.requests, "get", return_value=response), \
                mock.patch.object(ah, "BONUS_FALLBACK_TERMS", ("pasta",)):
            products = ah.get_bonus_products()
        self.assertEqual([p["id"] for p in products], ["5"])


class OffersStoreTest(TempDataDirTest):
    def test_sync_replaces_the_copy_and_load_reads_it(self):
        first = ah.bonus_products_in(BONUS_PAGE)
        with mock.patch.dict(offers.FETCHERS, {"ah": lambda: first}):
            data = offers.sync("ah")
        self.assertEqual(data["total"], 3)
        self.assertTrue(data["syncedAt"] and data["supported"])
        with mock.patch.dict(offers.FETCHERS, {"ah": lambda: first[:1]}):
            self.assertEqual(offers.sync("ah")["total"], 1)

    def test_failed_sync_keeps_the_previous_copy(self):
        with mock.patch.dict(offers.FETCHERS, {"ah": lambda: ah.bonus_products_in(BONUS_PAGE)}):
            offers.sync("ah")

        def boom():
            raise RuntimeError("AH is stuk")

        with mock.patch.dict(offers.FETCHERS, {"ah": boom}):
            with self.assertRaises(RuntimeError):
                offers.sync("ah")
        self.assertEqual(offers.load("ah")["total"], 3)

    def test_every_store_has_a_fetcher_and_unknown_stores_are_rejected(self):
        for store in offers.STORES:
            self.assertTrue(offers.load(store)["supported"], store)
        with self.assertRaises(ValueError):
            offers.sync("lidl")
        with self.assertRaises(ValueError):
            offers.load("lidl")


class SuggestionsTest(TempDataDirTest):
    def index(self, collected):
        collectors = tuple((name, (lambda items=items: items)) for name, items in collected.items())
        with mock.patch.object(offers, "COLLECTORS", collectors):
            return offers.sync_recipes()

    def test_recipes_are_ranked_by_share_of_ingredients_on_offer(self):
        with mock.patch.dict(offers.FETCHERS, {"ah": lambda: ah.bonus_products_in(BONUS_PAGE)}):
            offers.sync("ah")
        self.index({
            "mealie": [
                {"id": "r1", "title": "Kip met tomaat", "image": "", "url": "http://m/r1",
                 "ingredients": ["400 g kipfilet", "4 tomaten", "zout", "2 el olijfolie"]},
                {"id": "r2", "title": "Stoofpot", "image": "", "url": "", "ingredients": ["kipfilet", "wortelen", "aardappelen", "bouillon", "prei"]},
                {"id": "r3", "title": "Taart", "image": "", "url": "", "ingredients": ["bloem", "suiker", "eieren"]},
            ]
        })
        data = offers.suggest("ah")
        self.assertEqual([r["id"] for r in data["recipes"]], ["r1"], "r2 heeft maar 1 van 5, r3 niets")
        recipe = data["recipes"][0]
        self.assertEqual((recipe["matchedCount"], recipe["ingredientCount"]), (2, 3), "zout telt niet mee")
        self.assertEqual(recipe["saving"], 2.0)
        self.assertEqual(sorted(m["offer"]["id"] for m in recipe["matched"]), ["11", "12"])

    def test_a_failing_source_does_not_block_the_others(self):
        def broken():
            raise RuntimeError("Jumbo kapot")

        def unlinked():
            raise LookupError("niet gekoppeld")

        collectors = (("mealie", lambda: [{"id": "r1", "title": "A", "image": "", "url": "", "ingredients": ["kip"]}]),
                      ("jumbo", broken), ("picnic", unlinked))
        with mock.patch.object(offers, "COLLECTORS", collectors):
            result = offers.sync_recipes()
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["sources"]["jumbo"]["error"], "Jumbo kapot")
        self.assertEqual(result["sources"]["picnic"]["skipped"], "niet gekoppeld")
        self.assertEqual(result["sources"]["mealie"]["count"], 1)

    def test_ah_collector_reuses_indexed_recipes_and_limits_new_fetches(self):
        saved = {"recipes": [{"id": str(i), "title": f"R{i}", "image": "", "url": ""} for i in range(1, 4)]}
        calls = []

        def jsonld(recipe_id):
            calls.append(recipe_id)
            return {"recipeIngredient": ["kip"]}

        with mock.patch.object(ah_recipes, "load", return_value=saved), \
                mock.patch.object(ah, "get_recipe_jsonld", side_effect=jsonld), \
                mock.patch.object(offers, "AH_NEW_RECIPES_PER_SYNC", 2):
            self.assertEqual(len(offers._collect_ah()), 2)
        self.assertEqual(calls, [1, 2])


class OffersTabMarkupTest(unittest.TestCase):
    def test_offers_follows_the_layout_of_the_stores_tab(self):
        import re
        from pathlib import Path

        html = (Path(__file__).resolve().parent.parent / "templates" / "index.html").read_text(encoding="utf-8")
        section = html[html.index('<section id="offers"'):html.index('<section id="url"')]
        self.assertIn('<div class="subtab-menu" id="offers-store-menu" hidden>', section)
        self.assertLess(section.index("offers-store-menu"), section.index('data-subtab="offers-items"'))
        self.assertEqual(re.findall(r'<button class="subtab[^"]*" data-subtab="([\w-]+)"', section), ["offers-items", "offers-suggestions"])
        self.assertIn('<div id="offers-items" class="subpanel is-active">', section)
        self.assertIn('<div id="offers-suggestions" class="subpanel">', section)
        self.assertNotIn("offers-store-slot", section)


class OffersRoutesTest(TempDataDirTest):
    def setUp(self):
        super().setUp()
        self.client = create_app().test_client()

    def test_routes_load_sync_and_suggest(self):
        self.assertEqual(self.client.get("/api/offers?store=ah").get_json()["syncedAt"], None)
        with mock.patch.dict(offers.FETCHERS, {"ah": lambda: ah.bonus_products_in(BONUS_PAGE)}):
            synced = self.client.post("/api/offers/sync", json={"store": "ah"})
        self.assertEqual((synced.status_code, synced.get_json()["total"]), (200, 3))
        with mock.patch.object(offers, "COLLECTORS", (("mealie", lambda: [
                {"id": "r1", "title": "Kip", "image": "", "url": "", "ingredients": ["kipfilet", "tomaten"]}]),)):
            self.assertEqual(self.client.post("/api/offers/recipes/sync").status_code, 200)
        suggestions = self.client.get("/api/offers/recipes?store=ah").get_json()
        self.assertEqual([r["id"] for r in suggestions["recipes"]], ["r1"])

    def test_unknown_store_is_a_400_and_a_store_failure_a_424(self):
        self.assertEqual(self.client.get("/api/offers?store=lidl").status_code, 400)
        self.assertEqual(self.client.post("/api/offers/sync", json={"store": "lidl"}).status_code, 400)

        def not_linked():
            raise RuntimeError("Jumbo account niet gekoppeld")

        with mock.patch.dict(offers.FETCHERS, {"jumbo": not_linked}):
            self.assertEqual(self.client.post("/api/offers/sync", json={"store": "jumbo"}).status_code, 424)

        def boom():
            raise RuntimeError("AH is stuk")

        with mock.patch.dict(offers.FETCHERS, {"ah": boom}):
            response = self.client.post("/api/offers/sync", json={"store": "ah"})
        self.assertEqual((response.status_code, response.get_json()["error"]), (424, "AH is stuk"))


if __name__ == "__main__":
    unittest.main()
