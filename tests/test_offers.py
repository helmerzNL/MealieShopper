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

    def test_a_linking_s_is_not_mistaken_for_the_start_of_the_next_word(self):
        # sinaasappel|sap: de s hoort bij "sap", dus het is geen sinaasappel.
        self.assertFalse(offers.matches(offers.head_of("sinaasappels"), set(offers.tokens("Sinaasappelsap 1 l"))))
        self.assertFalse(offers.matches(offers.head_of("appel"), set(offers.tokens("Appelsap"))))
        self.assertTrue(offers.matches(offers.head_of("sinaasappels"), set(offers.tokens("Sinaasappelen net"))))

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

    def test_load_adds_the_conditions_of_each_offer(self):
        with mock.patch.dict(offers.FETCHERS, {"ah": lambda: ah.bonus_products_in(BONUS_PAGE)}):
            offers.sync("ah")
        by_title = {o["title"]: o for o in offers.load("ah")["offers"]}
        kip = by_title["Kipfilet"]
        self.assertEqual((kip["mechanic"], kip["minQuantity"], kip["unitPrice"]), ("nth_free", 2, 2.25))
        tomaten = by_title["AH Tomaten rond"]
        self.assertEqual((tomaten["mechanic"], tomaten["minQuantity"], tomaten["unitPrice"]), ("x_for_y", 2, 1.5))
        puree = by_title["Tomatenpuree"]
        self.assertEqual((puree["mechanic"], puree["unitPrice"]), ("fixed", 0.9), "lagere prijs zonder label")
        self.assertEqual(kip["validity"], "unknown", "zonder einddatum is niet te controleren")

    def test_expired_offers_are_not_shown_but_counted(self):
        old = {"id": "21", "title": "Oude aanbieding", "price": 1.0, "priceBefore": 2.0, "promoLabel": "50% korting", "validUntil": "2000-01-01"}
        current = {"id": "22", "title": "Lopende aanbieding", "price": 1.0, "priceBefore": 2.0, "promoLabel": "50% korting", "validUntil": "2999-01-01"}
        with mock.patch.dict(offers.FETCHERS, {"ah": lambda: [old, current]}):
            offers.sync("ah")
        data = offers.load("ah")
        self.assertEqual([o["id"] for o in data["offers"]], ["22"])
        self.assertEqual((data["total"], data["expired"]), (1, 1))
        self.assertEqual(data["offers"][0]["validity"], "valid")

    def test_saving_uses_the_unit_price_of_the_condition(self):
        second_half = {"price": 4.0, "priceBefore": 4.0, "unitPrice": 3.0}
        self.assertEqual(offers.saving_of(second_half), 1.0)
        plain = {"price": 1.5, "priceBefore": 2.0, "unitPrice": None}
        self.assertEqual(offers.saving_of(plain), 0.5)
        self.assertEqual(offers.saving_of({"price": 1.5, "priceBefore": None, "unitPrice": 1.5}), 0.0)
        self.assertEqual(offers.saving_of({"price": 3.0, "priceBefore": 2.0, "unitPrice": None}), 0.0, "nooit negatief")

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


class LinkingTest(unittest.TestCase):
    """Een ingrediënt hoort alleen bij een aanbieding die dat product zelf is, niet bij een bereid product waar het in zit."""

    def match(self, ingredient, title):
        core, rest = offers.offer_parts(title)
        return offers.match_offer(offers.head_of(ingredient), set(offers.tokens(ingredient)), core, rest)

    def test_products_that_only_contain_the_ingredient_do_not_match(self):
        # Het voorbeeld van "Romige tagliatelle met zalm": dit klopte allemaal niet.
        for ingredient, title in (
            ("1 middelgrote ui", "AH Gehakt met ui 3-pack"),
            ("250 g champignons", "AH Champignon saus romig en vol 3-pack"),
            ("2 el milde olijfolie", "Princes Tonijnstukken in olijfolie 3-pack"),
            ("400 g verse spinazie", "Hak Spinazie met room 4-pack"),
            ("200 g zuivelspread knoflook en kruiden", "Verstegen Kruidenmix gehakt slagersrecept 3-pack"),
        ):
            self.assertIsNone(self.match(ingredient, title), (ingredient, title))

    def test_the_product_itself_still_matches(self):
        for ingredient, title in (
            ("300 g tagliatelle", "AH Tagliatelle all' uovo 4-pack"),
            ("100 g Noorse gerookte zalmfilet", "AH Zalmfilet 2-pack"),
            ("400 g kipfilet", "AH Kipfilet"),
            ("1 l melk", "Jumbo Halfvolle Melk 1 l"),
            ("4 tomaten", "AH Tomaten rond"),
            ("6 eieren", "AH Scharrel eieren 10 stuks"),
            ("2 uien", "AH Uien 1 kg"),
            ("500 g gehakt", "Picnic Rundergehakt"),
        ):
            if ingredient == "500 g gehakt":
                continue  # samenstellingen met het ingrediënt aan het eind worden nog niet herkend
            self.assertIsNotNone(self.match(ingredient, title), (ingredient, title))

    def test_a_prepared_variant_matches_when_the_ingredient_asks_for_it(self):
        self.assertIsNone(self.match("tomaat", "Heinz Tomatenketchup"))
        self.assertIsNotNone(self.match("1 el tomatenketchup", "Heinz Tomatenketchup"))
        self.assertIsNotNone(self.match("250 g gehakt met ui", "AH Gehakt met ui 3-pack"), "het ingrediënt noemt de ui zelf")
        self.assertIsNone(self.match("sinaasappels", "Appelsientje Sinaasappelsap"))
        self.assertIsNotNone(self.match("200 ml sinaasappelsap", "Appelsientje Sinaasappelsap"))

    def test_the_head_of_an_ingredient_stops_at_a_preposition(self):
        self.assertEqual(offers.head_of("250 g gehakt met ui"), "gehakt")
        self.assertEqual(offers.head_of("400 g kipfilet in blokjes"), "kipfilet")
        self.assertEqual(offers.head_of("400 g kipfilet voor de wok"), "kipfilet")
        self.assertIsNone(offers.head_of("3 el olie voor het bakken"), "olie is een basisingrediënt")
        self.assertEqual(offers.head_of("1 bosje bieslook (in ringetjes)"), offers.head_of("bieslook"))
        self.assertEqual(offers.head_of("in blokjes gesneden ui"), "ui", "begint de regel met een voorzetsel, dan geldt de hele regel")

    def test_flavour_words_are_not_an_ingredient(self):
        self.assertIsNone(self.match("verse kruiden", "AH Italiaanse kruiden"))
        self.assertIsNone(self.match("2 el kruiden", "Verstegen Kruidenmix"))

    def test_brand_amounts_and_packaging_do_not_count(self):
        self.assertEqual(offers.offer_parts("AH Gehakt met ui 3-pack"), (["ah", "gehakt"], ["ui"]))
        self.assertEqual(offers.offer_parts("Jumbo Halfvolle Melk 1 l"), (["jumbo", "halfvolle", "melk"], []))
        self.assertEqual(offers.offer_parts("Ben & Jerry's Vanilla 465 ml")[1][0], "jerry")

    def test_fewer_unrelated_words_is_the_better_match(self):
        core_a, rest_a = offers.offer_parts("AH Kipfilet")
        core_b, rest_b = offers.offer_parts("AH Kipfilet Mediterraan gekruid")
        stems = set(offers.tokens("400 g kipfilet"))
        head = offers.head_of("400 g kipfilet")
        self.assertLess(offers.match_offer(head, stems, core_a, rest_a), offers.match_offer(head, stems, core_b, rest_b))


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
        self.assertEqual((recipe["matchedCount"], recipe["ingredientCount"]), (2, 2), "zout en olijfolie zijn basis en tellen niet mee")
        # Tomaten "2 voor 3" van 2,00: 1,50 per stuk, dus 0,50 goedkoper. Kipfilet "1+1 gratis" van 4,50: 2,25 per stuk,
        # dus 2,25 goedkoper (niet 1,50, het verschil met de huidige prijs die de winkel noemt).
        self.assertEqual(recipe["saving"], 2.75)
        self.assertEqual(sorted(m["offer"]["id"] for m in recipe["matched"]), ["11", "12"])

    def test_the_tagliatelle_example_links_only_what_is_really_on_offer(self):
        titles = [
            ("1", "AH Tagliatelle all' uovo 4-pack"), ("2", "AH Gehakt met ui 3-pack"),
            ("3", "AH Champignon saus romig en vol 3-pack"), ("4", "Princes Tonijnstukken in olijfolie 3-pack"),
            ("5", "Hak Spinazie met room 4-pack"), ("6", "AH Zalmfilet 2-pack"),
            ("7", "Verstegen Kruidenmix gehakt slagersrecept 3-pack"),
        ]
        fetched = [{"id": i, "title": t, "price": 2.0, "priceBefore": 3.0, "promoLabel": "Bonus"} for i, t in titles]
        with mock.patch.dict(offers.FETCHERS, {"ah": lambda: fetched}):
            offers.sync("ah")
        self.index({"mealie": [{
            "id": "r1", "title": "Romige tagliatelle met zalm", "image": "", "url": "",
            "ingredients": ["300 g tagliatelle", "1 middelgrote ui", "250 g champignons", "2 el milde olijfolie",
                            "400 g verse spinazie", "100 g Noorse gerookte zalmfilet", "200 g zuivelspread knoflook en kruiden"],
        }]})
        recipe = offers.suggest("ah")["recipes"][0]
        self.assertEqual((recipe["matchedCount"], recipe["ingredientCount"]), (2, 6), "de olijfolie is een basisingrediënt en telt niet mee")
        self.assertEqual(sorted(m["offer"]["id"] for m in recipe["matched"]), ["1", "6"])
        self.assertEqual(recipe["saving"], 2.0, "twee keer 1,00 goedkoper, niet 11,41")

    def course_setup(self):
        fetched = [
            {"id": "1", "title": "AH Kipfilet", "price": 3.0, "priceBefore": 4.0, "promoLabel": "Bonus"},
            {"id": "2", "title": "AH Tomaten rond", "price": 1.0, "priceBefore": 2.0, "promoLabel": "Bonus"},
            {"id": "3", "title": "AH Eieren 10 stuks", "price": 2.0, "priceBefore": 3.0, "promoLabel": "Bonus"},
        ]
        with mock.patch.dict(offers.FETCHERS, {"ah": lambda: fetched}):
            offers.sync("ah")
        self.index({
            "mealie": [
                {"id": "a", "title": "Kip met tomaat", "image": "", "url": "", "course": "hoofdgerecht",
                 "ingredients": ["400 g kipfilet", "4 tomaten", "2 el olijfolie", "zout", "1 courgette"]},
                {"id": "b", "title": "Roerei", "image": "", "url": "", "course": "ontbijt",
                 "ingredients": ["6 eieren", "2 tomaten", "30 g roomboter"]},
                {"id": "c", "title": "Tomatensalade", "image": "", "url": "", "course": "overig",
                 "ingredients": ["4 tomaten", "1 komkommer", "2 el azijn"]},
            ]
        })

    def test_basics_do_not_count_and_missing_ingredients_are_listed(self):
        self.course_setup()
        recipe = {r["id"]: r for r in offers.suggest("ah")["recipes"]}["a"]
        self.assertEqual((recipe["matchedCount"], recipe["ingredientCount"]), (2, 3), "olijfolie en zout zijn basis")
        self.assertEqual(recipe["missing"], ["1 courgette"])
        roerei = {r["id"]: r for r in offers.suggest("ah")["recipes"]}["b"]
        self.assertEqual((roerei["matchedCount"], roerei["ingredientCount"], roerei["missing"]), (2, 2, []), "roomboter is basis")

    def test_the_most_complete_recipe_comes_first(self):
        self.course_setup()
        self.assertEqual([r["id"] for r in offers.suggest("ah")["recipes"]], ["b", "a", "c"])

    def test_courses_are_counted_over_all_suggestions_and_the_filter_applies_before_the_limit(self):
        self.course_setup()
        data = offers.suggest("ah")
        self.assertEqual(data["courses"], [{"id": "ontbijt", "count": 1}, {"id": "hoofdgerecht", "count": 1}, {"id": "overig", "count": 1}])
        self.assertEqual(data["course"], "all")
        main = offers.suggest("ah", limit=1, course="hoofdgerecht")
        self.assertEqual([r["id"] for r in main["recipes"]], ["a"], "het filter geldt vóór de limiet")
        self.assertEqual((main["total"], main["course"]), (1, "hoofdgerecht"))
        self.assertEqual(main["courses"], data["courses"], "de aantallen blijven die van alle voorstellen")
        self.assertEqual(offers.suggest("ah", course="dessert")["recipes"], [])
        self.assertEqual(len(offers.suggest("ah", course="all")["recipes"]), 3)

    def test_an_unknown_course_is_refused(self):
        self.course_setup()
        with self.assertRaises(ValueError):
            offers.suggest("ah", course="pannenkoek")

    def test_recipes_without_a_course_are_other(self):
        self.course_setup()
        self.index({"mealie": [{"id": "z", "title": "Oud", "image": "", "url": "", "ingredients": ["4 tomaten", "400 g kipfilet"]}]})
        self.assertEqual(offers.suggest("ah")["recipes"][0]["course"], "overig")

    def test_an_index_from_before_the_courses_gets_the_column(self):
        import sqlite3
        from contextlib import closing

        with closing(offers._connect()) as conn:
            conn.execute("DROP TABLE offer_recipes")
            conn.execute("CREATE TABLE offer_recipes (source TEXT NOT NULL, recipe_id TEXT NOT NULL, title TEXT NOT NULL, image TEXT, url TEXT, ingredients TEXT NOT NULL, PRIMARY KEY (source, recipe_id))")
            conn.execute("INSERT INTO offer_recipes VALUES ('mealie', 'old', 'Oud recept', '', '', '[\"4 tomaten\"]')")
            conn.commit()
        with closing(offers._connect()) as conn:
            self.assertIn("course", {row["name"] for row in conn.execute("PRAGMA table_info(offer_recipes)")})
            self.assertIsNone(conn.execute("SELECT course FROM offer_recipes").fetchone()["course"])

    def test_the_best_fitting_offer_wins_over_the_first_one(self):
        fetched = [
            {"id": "1", "title": "AH Kipfilet Mediterraan gekruid", "price": 4.0, "priceBefore": 5.0, "promoLabel": "Bonus"},
            {"id": "2", "title": "AH Kipfilet", "price": 3.0, "priceBefore": 4.0, "promoLabel": "Bonus"},
        ]
        with mock.patch.dict(offers.FETCHERS, {"ah": lambda: fetched}):
            offers.sync("ah")
        self.index({"mealie": [{"id": "r1", "title": "Kip", "image": "", "url": "", "ingredients": ["400 g kipfilet"]}]})
        self.assertEqual(offers.suggest("ah")["recipes"][0]["matched"][0]["offer"]["id"], "2")

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


class CourseCollectorsTest(TempDataDirTest):
    def test_each_source_gives_its_course(self):
        recipe = {"id": "m1", "name": "Pasta", "slug": "pasta", "recipeIngredient": [{"food": {"name": "pasta"}}],
                  "recipeCategory": [{"name": "Avondeten"}], "tags": [{"name": "Snel"}]}
        with mock.patch.object(offers.mealie, "list_recipes", return_value=[{"slug": "pasta"}]), \
                mock.patch.object(offers.mealie, "get_recipe", return_value=recipe), \
                mock.patch.object(offers.mealie, "recipe_page_url", return_value="http://m/pasta"), \
                mock.patch.object(offers.mealie_images, "image_path", return_value=""):
            self.assertEqual(offers._collect_mealie()[0]["course"], "hoofdgerecht")

        jumbo_recipe = {"id": "j1", "name": "Ontbijtbowl", "ingredients": [{"name": "yoghurt"}], "course": "Ontbijt"}
        with mock.patch.object(offers.jumbo, "configured_refresh_token", return_value="x"), \
                mock.patch.object(offers.jumbo_recipes, "get_favorites", return_value={"recipes": [{"id": "j1"}]}), \
                mock.patch.object(offers.jumbo_recipes, "get_recipe", return_value=jumbo_recipe), \
                mock.patch.object(offers.jumbo_recipes, "source_url", return_value=""):
            self.assertEqual(offers._collect_jumbo()[0]["course"], "ontbijt")

        picnic_recipe = {"id": "p1", "name": "Soep", "ingredients": [{"name": "prei"}]}
        with mock.patch.object(offers.picnic, "configured_auth_key", return_value="x"), \
                mock.patch.object(offers.picnic_recipes, "get_recipes", return_value=[{"id": "p1", "tags": ["Soepen"]}]), \
                mock.patch.object(offers.picnic_recipes, "get_recipe", return_value=picnic_recipe), \
                mock.patch.object(offers.picnic_recipes, "source_url", return_value=""):
            self.assertEqual(offers._collect_picnic()[0]["course"], "soep")

    def test_ah_reads_the_category_and_refetches_recipes_indexed_without_one(self):
        saved = {"recipes": [{"id": str(i), "title": f"R{i}", "image": "", "url": ""} for i in (1, 2)]}
        calls = []

        def jsonld(recipe_id):
            calls.append(recipe_id)
            return {"recipeIngredient": ["kip"], "recipeCategory": "Hoofdgerecht" if recipe_id == 1 else ["Dessert"]}

        with mock.patch.object(ah_recipes, "load", return_value=saved), mock.patch.object(ah, "get_recipe_jsonld", side_effect=jsonld):
            first = offers._collect_ah()
            self.assertEqual([item["course"] for item in first], ["hoofdgerecht", "dessert"])
        # In de index, recept 1 mét soort en recept 2 zonder (ingelezen vóór de soorten): alleen 2 wordt opnieuw opgehaald.
        from contextlib import closing

        with closing(offers._connect()) as conn, conn:
            for recipe_id, course in (("1", "hoofdgerecht"), ("2", None)):
                conn.execute(
                    "INSERT OR REPLACE INTO offer_recipes (source, recipe_id, title, image, url, ingredients, course) VALUES ('ah', ?, 'R', '', '', '[\"kip\"]', ?)",
                    (recipe_id, course),
                )
        calls.clear()
        with mock.patch.object(ah_recipes, "load", return_value=saved), mock.patch.object(ah, "get_recipe_jsonld", side_effect=jsonld):
            again = offers._collect_ah()
        self.assertEqual(calls, [2])
        self.assertEqual([item["course"] for item in again], ["hoofdgerecht", "dessert"])


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

    def test_recipes_have_a_heading_and_a_course_filter_above_them(self):
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        html = (root / "templates" / "index.html").read_text(encoding="utf-8")
        panel = html[html.index('<div id="offers-suggestions"'):html.index('<section id="url"')]
        order = ["offers-recipes-heading", "offers.recipes.explain", 'id="offers-courses"', 'id="offers-recipes-status"', 'id="offers-recipes"']
        positions = [panel.index(marker) for marker in order]
        self.assertEqual(positions, sorted(positions), "kop, uitleg, soortfilter, status, recepten")
        script = (root / "static" / "offers.js").read_text(encoding="utf-8")
        self.assertIn("&course=", script, "de gekozen soort gaat mee naar de server")
        self.assertIn('"#offers-courses").addEventListener("click"', script)
        self.assertIn("offersState.course = \"all\";\n  try {", script, "een andere winkel zet het filter terug op alles")

    def test_a_matched_product_opens_a_dialog_with_a_link_to_the_store(self):
        import re
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        html = (root / "templates" / "index.html").read_text(encoding="utf-8")
        dialog = html[html.index('<dialog id="offer-product-dialog"'):html.index("</dialog>")]
        for marker in ("offer-product-title", "offer-product-image", "offer-product-price", "offer-product-match", "offer-product-open",
                       "offer-product-close", "offer-product-nopage"):
            self.assertIn(f'id="{marker}"', dialog)
        self.assertIn('aria-labelledby="offer-product-title"', dialog, "het venster heeft een naam voor schermlezers")
        self.assertNotIn("<dialog", html[html.index("</dialog>"):], "er is maar één productvenster")
        # De globale afhandeling van data-hide-on-error haalt het plaatje uit de pagina; het venster hergebruikt het plaatje.
        self.assertNotIn("data-hide-on-error", dialog)

        script = (root / "static" / "offers.js").read_text(encoding="utf-8")
        card = script[script.index("function offerRecipeCard"):script.index("function renderOfferCourses")]
        self.assertIn('class="offer-match"', card, "het gekoppelde product is een knop")
        self.assertIn('data-recipe="${index}" data-match="${at}"', card)
        self.assertNotIn('<span class="muted">→', card, "geen kale tekst meer")
        self.assertIn("showModal()", script)
        self.assertIn('$("#offer-product-image").addEventListener("error"', script, "een kapot plaatje wordt verborgen, niet verwijderd")
        self.assertIn("openProductPopup(", script, "de winkelpagina opent in het bestaande losse venster")
        self.assertIn('event.target.closest(".offer-match")', script)
        self.assertIn("offersState.recipes = data.recipes", script)
        self.assertIn('t("offers.also_in"', script, "staat het recept ook in een andere bron, dan staat dat op de kaart")
        self.assertIsNotNone(re.search(r"hidden = !offer\.url", script), "zonder adres (Picnic) geen knop naar de winkel")
        self.assertIn('hidden = Boolean(offer.url)', script, "zonder adres staat er waarom")

    def test_the_dialog_follows_the_theme_and_fits_a_phone(self):
        from pathlib import Path

        css = (Path(__file__).resolve().parent.parent / "static" / "styles.css").read_text(encoding="utf-8")
        block = css[css.index(".product-dialog {"):css.index(".product-dialog::backdrop")]
        for token in ("var(--surface)", "var(--text)", "var(--border)"):
            self.assertIn(token, block)
        self.assertIn("calc(100vw - 32px)", block, "past op een smal scherm")
        self.assertIn(".offer-match:focus-visible", css, "het toetsenbord ziet waar je bent")

    def test_recipe_cards_say_where_the_recipe_is_from_and_whose_offers_are_used(self):
        from pathlib import Path

        script = (Path(__file__).resolve().parent.parent / "static" / "offers.js").read_text(encoding="utf-8")
        card = script[script.index("function offerRecipeCard"):script.index("function offerIndexSummary")]
        self.assertIn('t("offers.recipe_from"', card)
        self.assertNotIn("· ${escapeHtml(t(`offers.source", card, "de bron mag niet als winkel van de aanbieding lezen")
        status = script[script.index("async function showOfferRecipes"):script.index("async function offersIndexRecipes")]
        self.assertIn('t("offers.recipes.heading"', status, "boven de recepten staat van welke winkel de aanbiedingen zijn")
        self.assertIn("STORES[offersState.store].name", status)
        self.assertNotIn("offers.recipes.using", script)


GRILLWORST = [
    "Bread roll", "6 tbsp mayo", "1 tbsp curry sauce", "1 tsp paprika", "2 tbsp paprika",
    "800 gr chicken thighs (or/and pork/bacon)", "Spice mixture (below)", "Cornstarch", "Old mature cheese", "1 shallot",
    "1 tbsp ketchup", "2 tsp curry", "1 tsp salt", "½ tsp cayenne", "½ tsp turmeric", "2 tsp salt", "1 tbsp onion powder",
    "1 tsp coriander", "1 tsp ginger", "½ / 1 tsp foelie or ½ tsp nutmeg",
]
SUSHIBOWL = ["zalmfilets zonder huid", "Ketjap manis", "Mayonaise", "komkommer", "avocado’s", "Bloemkoolrijst", "Taugé", "grillpan"]
REAL_TITLES = [
    "Bifi Roll XXL 2-pack", "Hellmann's Real mayonaise430 3-pack", "Go-Tan Sweet chilli sauce", "Lay's Paprika",
    "AH Focaccia paprika olijf 3-pack", "AH Zalmfilet 2-pack", "Conimex Ketjap manis 2pack", "AH Tomaat komkommer M",
    "Loving Blends Avocado olie shampoo 4-pack",
]


def real_offer(title, offer_id):
    from mealieshopper import offer_categories

    return {"id": str(offer_id), "title": title, "price": 2.0, "priceBefore": 3.0, "promoLabel": "Bonus",
            "category": offer_categories.categorize(title)}


class RealRecipesTest(TempDataDirTest):
    """De recepten waarin de gebruiker verkeerde koppelingen zag, met hun eigen regels en aanbiedingen."""

    def link(self, ingredient, title):
        return offers.link_offer(ingredient, real_offer(title, 1))

    def test_the_wrong_links_from_grillworst_and_the_sushi_bowl_are_gone(self):
        for ingredient, title in (
            ("Bread roll", "Bifi Roll XXL 2-pack"),
            ("1 tbsp curry sauce", "Go-Tan Sweet chilli sauce"),
            ("1 tsp paprika", "Lay's Paprika"),
            ("2 tbsp paprika", "Lay's Paprika"),
            ("2 tbsp paprika", "AH Focaccia paprika olijf 3-pack"),
            ("avocado’s", "Loving Blends Avocado olie shampoo 4-pack"),
            ("400 g kipfilet", "AH Kipfilet wrap"),
        ):
            self.assertIsNone(self.link(ingredient, title), (ingredient, title))

    def test_the_right_links_stay(self):
        for ingredient, title in (
            ("6 tbsp mayo", "Hellmann's Real mayonaise430 3-pack"),
            ("Mayonaise", "Hellmann's Real mayonaise430 3-pack"),
            ("Ketjap manis", "Conimex Ketjap manis 2pack"),
            ("zalmfilets zonder huid", "AH Zalmfilet 2-pack"),
            ("avocado’s", "AH Avocado eetrijp 2-pack"),
            ("1 rode paprika", "AH Paprika rood"),
            ("geraspte kaas", "AH Geraspte kaas"),
            ("4 tomaten", "AH Tomaten rond"),
            ("300 g tagliatelle", "AH Tagliatelle all' uovo 4-pack"),
        ):
            self.assertIsNotNone(self.link(ingredient, title), (ingredient, title))

    def test_the_wrong_links_from_the_second_report_are_gone(self):
        # Zes koppelingen uit een schermafbeelding van de receptvoorstellen bij AH.
        for ingredient, title in (
            ("Verspakket plaattaart groente", "Knorr Good noodles groente 4-pack"),
            ("500 g broccoli", "AH Terra Groenteburger broccoli paprika 4-pack"),
            ("2 middelgrote uien", "AH Pie runderstoof ui 3-pack"),
            ("150 g koelverse roomkaas met bieslook", "AH Mascarpone roomkaas chili dip 4-pack"),
            ("250 g verse spaghetti all'uovo", "AH Tagliatelle all' uovo 4-pack"),
            ("125 g ricotta", "AH Tortelloni verdi ricotta spinaci 3-pack"),
            ("400 g Provençaalse wokgroente", "AH Wokgroente Chinees 2-pack"),
        ):
            self.assertIsNone(self.link(ingredient, title), (ingredient, title))

    def test_a_different_colour_cuisine_or_fat_content_is_a_different_product(self):
        for ingredient, title in (
            ("1 rode paprika", "AH Paprika geel 3-pack"),
            ("1 oranje paprika", "AH Paprika rood geel groen 3-pack"),
            ("500 ml halfvolle melk", "AH Volle melk"),
            ("200 g witte rijst", "AH Rijst bruin"),
            ("250 g Italiaanse tomaten", "AH Spaanse tomaten"),
            ("400 g Provençaalse wokgroente", "AH Wokgroente Chinees 2-pack"),
        ):
            self.assertIsNone(self.link(ingredient, title), (ingredient, title))

    def test_when_one_side_names_no_such_property_or_they_agree_it_matches(self):
        for ingredient, title in (
            ("400 g wokgroente", "AH Wokgroente Chinees 2-pack"),
            ("400 g Chinese wokgroente", "AH Wokgroente Chinees 2-pack"),
            ("1 rode paprika", "AH Paprika rood"),
            ("2 rode uien", "AH Rode uien 1 kg"),
            ("2 rode uien", "AH Uien 1 kg"),
            ("1 groene paprika", "AH Paprika rood geel groen 3-pack"),
            ("500 ml halfvolle melk", "AH Halfvolle melk"),
            ("500 ml melk", "AH Volle melk"),
            ("200 g witte rijst", "AH Rijst wit"),
            ("100 g Noorse gerookte zalmfilet", "AH Gerookte zalmfilet"),
        ):
            self.assertIsNotNone(self.link(ingredient, title), (ingredient, title))

    def test_the_variant_words_are_compared_in_every_form(self):
        # rode/rood, witte/wit, chinese/chinees en groen (dat `_stem` als meervoud ziet: gro) zijn hetzelfde woord.
        for a, b in (("rode", "rood"), ("witte", "wit"), ("chinese", "chinees"), ("groene", "groen"), ("gele", "geel"), ("italiaanse", "italiaans")):
            self.assertFalse(offers.variant_clash([offers._stem(a)], [offers._stem(b)]), (a, b))
        self.assertTrue(offers.variant_clash([offers._stem("rode")], [offers._stem("geel")]))
        self.assertFalse(offers.variant_clash(["paprika"], ["geel"]), "alleen één kant noemt een kleur")

    def test_what_was_right_in_that_report_stays(self):
        for ingredient, title in (
            ("200 g gerookte zalmsnippers", "AH Gerookte zalmsnippers 2-pack"),
            ("195 g pastasaus sugo basilicum", "AH Pastasaus pomodori basilicum 2-pack"),
            ("300 g tagliatelle all'uovo", "AH Tagliatelle all' uovo 4-pack"),
            ("125 g ricotta", "AH Ricotta"),
            ("300 g gemengde groente", "AH Gemengde groente 500 g"),
            ("500 g broccoli", "AH Broccoli"),
            ("2 uien", "AH Uien 1 kg"),
            ("200 g roomkaas", "AH Roomkaas naturel"),
        ):
            self.assertIsNotNone(self.link(ingredient, title), (ingredient, title))

    def test_two_different_raw_kinds_do_not_go_together(self):
        # Een ui in een vleesproduct is geen ui; een groenteburger is geen broccoli.
        self.assertIsNone(self.link("2 uien", "AH Pie runderstoof ui 3-pack"))
        self.assertIsNone(self.link("400 g kipfilet", "AH Rode paprika 3-pack"))

    def test_an_italian_addition_is_not_the_head_of_the_ingredient(self):
        self.assertEqual(offers.head_of("250 g verse spaghetti all'uovo"), offers.head_of("spaghetti"))
        self.assertEqual(offers.head_of("tagliatelle all’uovo"), offers.head_of("tagliatelle"), "ook met een typografische apostrof")
        self.assertEqual(offers.offer_parts("AH Tagliatelle all' uovo 4-pack"), (["ah", "tagliatelle"], []))

    def test_a_dip_a_burger_and_a_gratin_are_prepared_variants(self):
        self.assertIsNone(self.link("100 g roomkaas", "AH Roomkaas dip naturel"))
        self.assertIsNone(self.link("400 g gehakt", "AH Runderburger"))
        self.assertIsNotNone(self.link("2 runderburgers", "AH Runderburger 4-pack"), "het ingrediënt vraagt er zelf om")

    def test_a_curry_sauce_needs_the_curry_and_a_bread_roll_needs_the_bread(self):
        self.assertIsNone(self.link("curry sauce", "Go-Tan Sweet chilli sauce"))
        self.assertIsNotNone(self.link("sweet chilli sauce", "Go-Tan Sweet chilli sauce"))
        self.assertIsNone(self.link("bread roll", "Bifi Roll XXL"))

    def test_seasonings_basics_and_headings_are_not_ingredients(self):
        for line in ("1 tsp salt", "½ tsp cayenne", "½ tsp turmeric", "1 tsp paprika", "2 tl kurkuma", "1 snuf nootmuskaat",
                     "1 tbsp onion powder", "1 el knoflookpoeder", "Cornstarch", "Spice mixture (below)", "Kruidenmix (hieronder)"):
            self.assertIsNone(offers.head_of(line), line)
        for line in ("1 tbsp curry sauce", "1 tbsp ketjap", "6 tbsp mayo", "2 rode paprika", "1 paprika"):
            self.assertIsNotNone(offers.head_of(line), line)
        # Een eetlepel van een gedroogde specerij is een kruid, geen groente.
        for line in ("2 tbsp paprika", "1 el kurkuma", "1 tbsp cumin", "2 el kerrie", "1 tablespoon cinnamon"):
            self.assertIsNone(offers.head_of(line), line)

    def test_inflected_adjectives_do_not_count_as_words(self):
        self.assertEqual(offers.tokens("geraspte kaas"), offers.tokens("kaas"))
        self.assertEqual(offers.tokens("gerookte zalmfilet"), offers.tokens("zalmfilet"))

    def test_grillworst_is_not_suggested_and_the_sushi_bowl_has_no_shampoo(self):
        fetched = [real_offer(title, index) for index, title in enumerate(REAL_TITLES, 1)]
        with mock.patch.dict(offers.FETCHERS, {"ah": lambda: fetched}):
            offers.sync("ah")
        collectors = (("mealie", lambda: [
            {"id": "g", "title": "Grillworst", "image": "", "url": "", "ingredients": GRILLWORST},
            {"id": "s", "title": "Warme sushibowl", "image": "", "url": "", "ingredients": SUSHIBOWL},
        ]),)
        with mock.patch.object(offers, "COLLECTORS", collectors):
            offers.sync_recipes()
        recipes = {recipe["id"]: recipe for recipe in offers.suggest("ah")["recipes"]}
        self.assertNotIn("g", recipes, "één koppeling (mayo) is te weinig voor een recept met zoveel ingrediënten")
        bowl = recipes["s"]
        matched = {item["ingredient"]: item["offer"]["title"] for item in bowl["matched"]}
        self.assertEqual(matched["zalmfilets zonder huid"], "AH Zalmfilet 2-pack")
        self.assertEqual(matched["Ketjap manis"], "Conimex Ketjap manis 2pack")
        self.assertEqual(matched["Mayonaise"], "Hellmann's Real mayonaise430 3-pack")
        self.assertNotIn("avocado’s", matched, "shampoo is geen ingrediënt")
        self.assertFalse(any("shampoo" in title for title in matched.values()))

    def test_pasta_with_salmon_and_broccoli_is_no_longer_suggested_on_three_wrong_links(self):
        titles = ["AH Terra Groenteburger broccoli paprika 4-pack", "AH Pie runderstoof ui 3-pack", "AH Gerookte zalmsnippers 2-pack",
                  "AH Mascarpone roomkaas chili dip 4-pack"]
        fetched = [real_offer(title, index) for index, title in enumerate(titles, 1)]
        with mock.patch.dict(offers.FETCHERS, {"ah": lambda: fetched}):
            offers.sync("ah")
        collectors = (("mealie", lambda: [{"id": "p", "title": "Pasta met zalm en broccoli", "image": "", "url": "", "ingredients": [
            "500 g broccoli", "2 middelgrote uien", "200 g gerookte zalmsnippers", "150 g koelverse roomkaas met bieslook", "300 g fusilli"]}]),)
        with mock.patch.object(offers, "COLLECTORS", collectors):
            offers.sync_recipes()
        self.assertEqual(offers.suggest("ah")["recipes"], [], "nog maar één koppeling (de zalm) van vijf: onder de ondergrens")

    def test_mealie_links_in_an_old_index_are_rebuilt_with_the_group(self):
        fetched = [real_offer("AH Kipfilet", 1), real_offer("AH Tomaten rond", 2)]
        with mock.patch.dict(offers.FETCHERS, {"ah": lambda: fetched}):
            offers.sync("ah")
        lines = ["400 g kipfilet", "4 tomaten"]
        collectors = (
            ("mealie", lambda: [{"id": "a", "title": "A", "image": "", "url": "https://mealie.example/recipe/witlof-met-ham", "ingredients": lines},
                                {"id": "b", "title": "B", "image": "", "url": "https://mealie.example/g/zb/r/al-goed", "ingredients": lines}]),
            ("jumbo", lambda: [{"id": "c", "title": "C", "image": "", "url": "https://www.jumbo.com/recepten/recipe/xyz", "ingredients": lines}]),
        )
        with mock.patch.object(offers, "COLLECTORS", collectors):
            offers.sync_recipes()
        with mock.patch.object(offers.mealie, "mealie_url", return_value="https://mealie.example"), \
                mock.patch.object(offers.mealie, "group_slug", return_value="zb"):
            urls = {recipe["id"]: recipe["url"] for recipe in offers.suggest("ah")["recipes"]}
        self.assertEqual(urls["a"], "https://mealie.example/g/zb/r/witlof-met-ham", "het oude adres is gecorrigeerd")
        self.assertEqual(urls["b"], "https://mealie.example/g/zb/r/al-goed", "een goed adres blijft goed")
        self.assertEqual(urls["c"], "https://www.jumbo.com/recepten/recipe/xyz", "alleen Mealie-adressen worden aangepast")
        # Zonder koppeling met Mealie blijft het bewaarde adres staan.
        with mock.patch.object(offers.mealie, "mealie_url", return_value=""):
            urls = {recipe["id"]: recipe["url"] for recipe in offers.suggest("ah")["recipes"]}
        self.assertEqual(urls["a"], "https://mealie.example/recipe/witlof-met-ham")

    def test_a_mealie_that_cannot_be_reached_does_not_block_the_suggestions(self):
        with mock.patch.dict(offers.FETCHERS, {"ah": lambda: [real_offer("AH Kipfilet", 1), real_offer("AH Tomaten rond", 2)]}):
            offers.sync("ah")
        collectors = (("mealie", lambda: [{"id": "a", "title": "A", "image": "", "url": "https://mealie.example/recipe/x",
                                           "ingredients": ["400 g kipfilet", "4 tomaten"]}]),)
        with mock.patch.object(offers, "COLLECTORS", collectors):
            offers.sync_recipes()
        with mock.patch.object(offers.mealie, "mealie_url", return_value="https://mealie.example"), \
                mock.patch.object(offers.mealie, "recipe_page_url", side_effect=RuntimeError("weg")):
            recipes = offers.suggest("ah")["recipes"]
        self.assertEqual(recipes[0]["url"], "https://mealie.example/recipe/x")

    def duplicate_setup(self, recipes):
        fetched = [real_offer("AH Kipfilet", 1), real_offer("AH Tomaten rond", 2), real_offer("AH Rijst basmati", 3)]
        with mock.patch.dict(offers.FETCHERS, {"ah": lambda: fetched}):
            offers.sync("ah")
        collectors = tuple((name, (lambda items=items: items)) for name, items in recipes.items())
        with mock.patch.object(offers, "COLLECTORS", collectors):
            offers.sync_recipes()
        return offers.suggest("ah")

    def test_the_same_recipe_from_two_sources_is_one_card(self):
        lines = ["400 g kipfilet", "4 tomaten", "200 g rijst"]
        data = self.duplicate_setup({
            "mealie": [{"id": "m", "title": "Supersnelle spaghetti met runderballetjes", "image": "", "url": "http://m/x", "ingredients": lines}],
            "ah": [{"id": "a", "title": "Supersnelle  spaghetti met Runderballetjes!", "image": "", "url": "http://ah/x", "ingredients": lines}],
        })
        self.assertEqual(len(data["recipes"]), 1)
        recipe = data["recipes"][0]
        self.assertEqual((recipe["source"], recipe["alsoIn"]), ("mealie", ["ah"]), "bij gelijke dekking staat het eigen recept bovenaan")
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["courses"], [{"id": "overig", "count": 1}], "ook de aantallen per soort tellen het recept één keer")

    def test_the_better_covered_copy_is_the_one_that_stays(self):
        data = self.duplicate_setup({
            "mealie": [{"id": "m", "title": "Kip en rijst", "image": "", "url": "", "ingredients": ["400 g kipfilet", "200 g rijst", "1 grillpan", "1 wokpan"]}],
            "ah": [{"id": "a", "title": "Kip en rijst", "image": "", "url": "", "ingredients": ["400 g kipfilet", "200 g rijst"]}],
        })
        recipe = data["recipes"][0]
        self.assertEqual((recipe["source"], recipe["alsoIn"], recipe["matchedCount"], recipe["ingredientCount"]), ("ah", ["mealie"], 2, 2))

    def test_two_recipes_with_the_same_title_from_the_same_source_stay_two(self):
        lines = ["400 g kipfilet", "4 tomaten"]
        data = self.duplicate_setup({"mealie": [
            {"id": "1", "title": "Pasta", "image": "", "url": "", "ingredients": lines},
            {"id": "2", "title": "Pasta", "image": "", "url": "", "ingredients": lines},
        ]})
        self.assertEqual(len(data["recipes"]), 2)
        self.assertTrue(all(recipe["alsoIn"] == [] for recipe in data["recipes"]))

    def test_different_titles_are_not_merged_and_three_sources_list_both_others(self):
        lines = ["400 g kipfilet", "4 tomaten"]
        data = self.duplicate_setup({
            "mealie": [{"id": "1", "title": "Kip met tomaat", "image": "", "url": "", "ingredients": lines},
                       {"id": "2", "title": "Kip met tomaat en rijst", "image": "", "url": "", "ingredients": lines}],
            "ah": [{"id": "3", "title": "Kip met tomaat", "image": "", "url": "", "ingredients": lines}],
            "jumbo": [{"id": "4", "title": "kip met tomaat", "image": "", "url": "", "ingredients": lines}],
        })
        by_title = {recipe["title"]: recipe for recipe in data["recipes"]}
        self.assertEqual(set(by_title), {"Kip met tomaat", "Kip met tomaat en rijst"})
        self.assertEqual(by_title["Kip met tomaat"]["alsoIn"], ["ah", "jumbo"])

    def test_the_same_ingredient_twice_counts_once(self):
        fetched = [real_offer("AH Paprika rood", 1), real_offer("AH Tomaten rond", 2)]
        with mock.patch.dict(offers.FETCHERS, {"ah": lambda: fetched}):
            offers.sync("ah")
        collectors = (("mealie", lambda: [
            {"id": "p", "title": "Paprikaschotel", "image": "", "url": "", "ingredients": ["1 paprika", "2 paprika", "4 tomaten"]}]),)
        with mock.patch.object(offers, "COLLECTORS", collectors):
            offers.sync_recipes()
        recipe = offers.suggest("ah")["recipes"][0]
        self.assertEqual((recipe["matchedCount"], recipe["ingredientCount"]), (2, 2))

    def test_offers_that_are_never_ingredients_are_left_out(self):
        for title in ("Loving Blends Avocado olie shampoo 4-pack", "Pampers luiers maat 3", "Whiskas kattenvoer met kip", "Glorix allesreiniger citroen"):
            self.assertIsNone(self.link("avocado" if "Avocado" in title else "kip", title), title)



DUTCH_OFFERS = [
    "AH Kippendijfilet 600 g", "AH Rundergehakt 500 g", "Hellmann's Real mayonaise430 3-pack", "Heinz Tomaat ketchup", "AH Oude kaas plakken",
    "AH Sjalot 250 g", "AH Broodjes 6-pack", "Lay's Paprika", "Go-Tan Sweet chilli sauce", "AH Kipfilet", "AH Rijst basmati", "AH Tomaten rond",
    "AH Rode paprika 3-pack", "AH Zalmfilet 2-pack", "AH Spinazie", "Jumbo Sojasaus", "AH Bosui bos",
    "Loving Blends Avocado olie shampoo 4-pack",
]


class EnglishRecipesTest(TempDataDirTest):
    """Een Engelstalig recept uit Mealie tegen Nederlandse aanbiedingen."""

    def suggestions(self, recipes):
        fetched = [real_offer(title, index) for index, title in enumerate(DUTCH_OFFERS, 1)]
        with mock.patch.dict(offers.FETCHERS, {"ah": lambda: fetched}):
            offers.sync("ah")
        collectors = (("mealie", lambda: [
            {"id": key, "title": key, "image": "", "url": "", "ingredients": lines} for key, lines in recipes.items()]),)
        with mock.patch.object(offers, "COLLECTORS", collectors):
            offers.sync_recipes()
        return {recipe["id"]: recipe for recipe in offers.suggest("ah")["recipes"]}

    def test_english_ingredients_match_dutch_offers(self):
        recipes = self.suggestions({"rice": [
            "400 g chicken breast", "2 cups rice", "3 tomatoes", "1 red bell pepper", "2 tbsp olive oil", "2 tbsp soy sauce",
            "1 bunch spring onions", "salt and pepper", "200 g spinach"]})
        recipe = recipes["rice"]
        self.assertEqual((recipe["matchedCount"], recipe["ingredientCount"]), (7, 7), "olijfolie, zout en peper zijn basis")
        matched = {item["ingredient"]: item["offer"]["title"] for item in recipe["matched"]}
        self.assertEqual(matched["400 g chicken breast"], "AH Kipfilet")
        self.assertEqual(matched["2 cups rice"], "AH Rijst basmati")
        self.assertEqual(matched["1 red bell pepper"], "AH Rode paprika 3-pack")
        self.assertEqual(matched["1 bunch spring onions"], "AH Bosui bos")
        self.assertEqual(recipe["missing"], [])

    def test_the_original_english_line_is_what_you_see(self):
        recipe = self.suggestions({"x": ["800 gr chicken thighs (or/and pork/bacon)", "1 shallot"]})["x"]
        self.assertEqual([item["ingredient"] for item in recipe["matched"]], ["800 gr chicken thighs (or/and pork/bacon)", "1 shallot"])
        titles = {item["offer"]["title"] for item in recipe["matched"]}
        self.assertEqual(titles, {"AH Kippendijfilet 600 g", "AH Sjalot 250 g"}, "de tweede Nederlandse naam (kipdijfilet) wordt ook geprobeerd")

    def test_grillworst_in_english_matches_what_is_really_on_offer(self):
        grill = ["Bread roll", "6 tbsp mayo", "1 tbsp curry sauce", "1 tsp paprika", "2 tbsp paprika", "800 gr chicken thighs (or/and pork/bacon)",
                 "Spice mixture (below)", "Cornstarch", "Old mature cheese", "1 shallot", "1 tbsp ketchup", "2 tsp curry", "1 tsp salt",
                 "½ tsp cayenne", "1 tbsp onion powder"]
        recipe = self.suggestions({"grill": grill})["grill"]
        matched = {item["ingredient"]: item["offer"]["title"] for item in recipe["matched"]}
        self.assertEqual(matched["6 tbsp mayo"], "Hellmann's Real mayonaise430 3-pack")
        self.assertEqual(matched["Old mature cheese"], "AH Oude kaas plakken")
        self.assertEqual(matched["1 tbsp ketchup"], "Heinz Tomaat ketchup")
        self.assertNotIn("1 tbsp curry sauce", matched, "Sweet chilli sauce is geen currysaus")
        self.assertEqual(recipe["missing"], ["1 tbsp curry sauce"])
        self.assertFalse(any("Lay's" in title for title in matched.values()), "chips zijn geen paprika")
        self.assertEqual((recipe["matchedCount"], recipe["ingredientCount"]), (6, 7), "paprika, kruiden, zout en het kopje tellen niet mee")

    def test_dutch_recipes_are_unaffected(self):
        recipe = self.suggestions({"nl": ["400 g kipfilet", "4 tomaten", "2 el olijfolie", "1 rode paprika", "250 g bloem"]})["nl"]
        self.assertEqual((recipe["matchedCount"], recipe["ingredientCount"]), (3, 3))
        self.assertEqual({item["offer"]["title"] for item in recipe["matched"]}, {"AH Kipfilet", "AH Tomaten rond", "AH Rode paprika 3-pack"})


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
                {"id": "r1", "title": "Kip", "image": "", "url": "", "ingredients": ["kipfilet", "tomaten"], "course": "hoofdgerecht"}]),)):
            self.assertEqual(self.client.post("/api/offers/recipes/sync").status_code, 200)
        suggestions = self.client.get("/api/offers/recipes?store=ah").get_json()
        self.assertEqual([r["id"] for r in suggestions["recipes"]], ["r1"])
        self.assertEqual(suggestions["courses"], [{"id": "hoofdgerecht", "count": 1}])
        self.assertEqual([r["id"] for r in self.client.get("/api/offers/recipes?store=ah&course=hoofdgerecht").get_json()["recipes"]], ["r1"])
        self.assertEqual(self.client.get("/api/offers/recipes?store=ah&course=dessert").get_json()["recipes"], [])
        self.assertEqual(self.client.get("/api/offers/recipes?store=ah&course=pannenkoek").status_code, 400)

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
