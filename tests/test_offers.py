import asyncio
import os
import tempfile
from contextlib import closing
from datetime import date, timedelta
from collections.abc import Awaitable
from typing import Any, Callable
import unittest
from unittest import mock

import httpx

os.environ.setdefault("MEALIESHOPPER_DATA_DIR", tempfile.mkdtemp())

from mealieshopper import ah, ah_recipes, auth, offers, prices  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402
from helpers import AppTestCase, db, returned, routes, run, served  # noqa: E402

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


def _promotion(pid, label, start, end, products, kind="NATIONAL", category="Vlees"):
    top, _, bottom = label.partition(" ")
    return {"id": pid, "title": f"Actie {pid}", "periodStart": start, "periodEnd": end, "promotionType": kind, "category": category,
            "promotionLabels": [{"title": None, "topText": top, "centerText": None, "bottomText": bottom}], "products": products}


def _gql_product(pid, title, price, image=True):
    return {"id": pid, "title": title, "brand": "AH", "salesUnitSize": "300 g", "price": {"now": {"amount": price}, "was": {"amount": price}},
            "imagePack": [{"small": {"url": f"https://img/{pid}.webp"}}] if image else []}


# Zoals de GraphQL van de AH-app de bonus geeft (bonusPeriods en bonusPromotions, met een echt antwoord bevestigd op 1 nov 2026).
BONUS_PROMOTIONS = {
    "bonusPeriods": [{"startDate": "2026-10-26", "endDate": "2026-11-01", "weekNumber": 44},
                     {"startDate": "2026-11-02", "endDate": "2026-11-08", "weekNumber": 45}],
    "bonusPromotions": [
        _promotion("1", "25% korting", "2026-10-26", "2026-11-01", [_gql_product(4004, "AH Rundergehakt", 4.0)]),
        _promotion("2", "2 voor 2.99", "2026-11-02", "2026-11-08", [_gql_product(4004, "AH Rundergehakt", 4.0), _gql_product(4164, "AH Courgette", 0.89)]),
        _promotion("3", "voor 1.99", "2026-11-02", "2026-11-08", [_gql_product(171425, "AH Prei", 2.49, image=False)], category="Groente, aardappelen"),
        _promotion("4", "5% volume", "2026-11-02", "2999-12-31", [_gql_product(5, "AH Kip 4-pack", 20.0)]),
        _promotion("FREE4526_A", "gratis bezorging", "2026-11-02", "2026-11-08", [_gql_product(6, "Maggi", 1.0)], kind="INCENTIVE"),
        _promotion("5", "2e halve prijs", "2026-11-02", "2026-11-15", [_gql_product(7, "Etos Shampoo", 3.0)], kind="ETOS"),
        _promotion("6", "50% korting", "2026-10-12", "2026-10-25", [_gql_product(8, "Oude actie", 2.0)]),
        _promotion("7", "1+1 gratis", "2026-11-16", "2026-11-22", [_gql_product(9, "Buiten de periodes", 2.0)]),
    ],
}


class TempDataDirTest(AppTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"MEALIESHOPPER_DATA_DIR": tmp.name, "PASSKEY_AUTH_ENABLED": "false"})
        patcher.start()
        self.addCleanup(patcher.stop)
        run(auth.init_db())


class MatchingTest(AppTestCase):
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


class BonusParsingTest(AppTestCase):
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

        response = httpx.Response(200, json=search)
        with mock.patch.object(ah, "get_token", return_value="t"), \
                mock.patch.object(ah, "get_bonus_promotions", side_effect=RuntimeError("AH bonus ophalen mislukt (500)")), \
                mock.patch.object(ah, "_request_json", side_effect=fake_request), \
                served(return_value=response), \
                mock.patch.object(ah, "BONUS_FALLBACK_TERMS", ("pasta",)):
            products = run(ah.get_bonus_products())
        self.assertEqual([p["id"] for p in products], ["5"])

    def test_the_search_fallback_asks_at_most_six_terms_at_once(self):
        # Elk zoekverzoek geeft de loop een paar keer vrij, zoals een echt antwoord van AH: zonder grens liepen alle termen
        # tegelijk, met de grens precies zes. De producten komen in de volgorde van de termen.
        state: list[int] = [0, 0]  # nu bezig, hoogste aantal tegelijk

        async def search(request: httpx.Request) -> httpx.Response:
            term = request.url.params["query"]
            state[0] += 1
            state[1] = max(state[1], state[0])
            for _ in range(3):
                await asyncio.sleep(0)
            state[0] -= 1
            return httpx.Response(200, json={"products": [{"webshopId": term, "title": term, "currentPrice": 1.0, "isBonus": True}]})

        with mock.patch.object(ah, "get_token", return_value="t"), \
                mock.patch.object(ah, "get_bonus_promotions", side_effect=RuntimeError("AH bonus ophalen mislukt (500)")), \
                mock.patch.object(ah, "_request_json", side_effect=RuntimeError("404: niet gevonden")), \
                served(side_effect=search) as route:
            products = run(ah.get_bonus_products())
        self.assertEqual(route.call_count, len(ah.BONUS_FALLBACK_TERMS))
        self.assertEqual([p["id"] for p in products], list(ah.BONUS_FALLBACK_TERMS))
        self.assertEqual(state[1], ah.BONUS_SEARCH_WORKERS)
        self.assertEqual(ah.BONUS_SEARCH_WORKERS, 6)

    def test_the_bonus_comes_per_bonus_period_from_graphql(self):
        with mock.patch.object(ah, "get_token", return_value="t"), mock.patch.object(ah, "_graphql", return_value=BONUS_PROMOTIONS) as graphql:
            products = run(ah.get_bonus_promotions(today=date(2026, 11, 1)))
        self.assertIn("bonusPeriods", graphql.call_args.args[1])
        by_key = {(p["id"], p["validFrom"]): p for p in products}
        self.assertEqual(sorted(by_key), [("171425", "2026-11-02"), ("4004", "2026-10-26"), ("4004", "2026-11-02"), ("4164", "2026-11-02"), ("5", "2026-11-02")],
                         "bezorgkorting, Etos, afgelopen acties en acties buiten AH's bonusperiodes doen niet mee; gehakt staat in beide weken")
        this_week = by_key[("4004", "2026-10-26")]
        self.assertEqual((this_week["price"], this_week["priceBefore"], this_week["promoLabel"], this_week["validUntil"]), (3.0, 4.0, "25% korting", "2026-11-01"))
        next_week = by_key[("4004", "2026-11-02")]
        self.assertEqual((next_week["price"], next_week["priceBefore"], next_week["promoLabel"]), (4.0, None, "2 voor 2.99"),
                         "met een minimumaantal blijft de normale prijs staan, zoals bij AH")
        prei = by_key[("171425", "2026-11-02")]
        self.assertEqual((prei["price"], prei["priceBefore"], prei["image"], prei["categoryHint"]), (1.99, 2.49, None, "Groente, aardappelen"))
        self.assertEqual(next_week["image"], "https://img/4004.webp")
        self.assertEqual(next_week["url"], "https://www.ah.nl/producten/product/wi4004")
        self.assertIsNone(by_key[("5", "2026-11-02")]["validUntil"], "AH's einddatum 2999 is doorlopend")

    def test_graphql_comes_first_and_the_old_routes_are_the_fallback(self):
        with mock.patch.object(ah, "get_token", return_value="t"), mock.patch.object(ah, "_graphql", return_value=BONUS_PROMOTIONS), \
                mock.patch.object(ah, "_request_json") as bonus_page, mock.patch.object(ah, "date") as fake_date:
            fake_date.today.return_value = date(2026, 11, 1)
            products = run(ah.get_bonus_products())
        self.assertEqual(len(products), 5)
        bonus_page.assert_not_called()


class OffersStoreTest(TempDataDirTest):
    def test_sync_replaces_the_copy_and_load_reads_it(self):
        first = ah.bonus_products_in(BONUS_PAGE)
        with mock.patch.dict(offers.FETCHERS, {"ah": lambda: returned(first)}):
            data = run(offers.sync("ah"))
        self.assertEqual(data["total"], 3)
        self.assertTrue(data["syncedAt"] and data["supported"])
        with mock.patch.dict(offers.FETCHERS, {"ah": lambda: returned(first[:1])}):
            self.assertEqual(run(offers.sync("ah"))["total"], 1)

    def test_load_adds_the_conditions_of_each_offer(self):
        with mock.patch.dict(offers.FETCHERS, {"ah": lambda: returned(ah.bonus_products_in(BONUS_PAGE))}):
            run(offers.sync("ah"))
        by_title = {o["title"]: o for o in run(offers.load("ah"))["offers"]}
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
        with mock.patch.dict(offers.FETCHERS, {"ah": lambda: returned([old, current])}):
            run(offers.sync("ah"))
        data = run(offers.load("ah"))
        self.assertEqual([o["id"] for o in data["offers"]], ["22"])
        self.assertEqual((data["total"], data["expired"]), (1, 1))
        self.assertEqual(data["offers"][0]["validity"], "valid")

    def test_period_of_the_offers_is_given_for_the_header(self):
        week = {"validFrom": "2026-10-26", "validUntil": "2999-01-01", "promoLabel": "50% korting", "price": 1.0, "priceBefore": 2.0}
        items = [
            {"id": "31", "title": "Pasta", **week},
            {"id": "32", "title": "Rijst", **week},
            {"id": "33", "title": "Volume voordeel", "validFrom": "2026-10-26", "validUntil": None, "price": 1.0, "priceBefore": 1.1},
        ]
        with mock.patch.dict(offers.FETCHERS, {"ah": lambda: returned(items)}):
            run(offers.sync("ah"))
        data = run(offers.load("ah"))
        self.assertEqual(data["period"], {"from": "2026-10-26", "until": "2999-01-01"})
        self.assertEqual(data["offers"][0]["validFrom"], "2026-10-26")
        with mock.patch.dict(offers.FETCHERS, {"ah": lambda: returned([items[2]])}):
            run(offers.sync("ah"))
        self.assertIsNone(run(offers.load("ah"))["period"])

    def test_saving_uses_the_unit_price_of_the_condition(self):
        second_half = {"price": 4.0, "priceBefore": 4.0, "unitPrice": 3.0}
        self.assertEqual(offers.saving_of(second_half), 1.0)
        plain = {"price": 1.5, "priceBefore": 2.0, "unitPrice": None}
        self.assertEqual(offers.saving_of(plain), 0.5)
        self.assertEqual(offers.saving_of({"price": 1.5, "priceBefore": None, "unitPrice": 1.5}), 0.0)
        self.assertEqual(offers.saving_of({"price": 3.0, "priceBefore": 2.0, "unitPrice": None}), 0.0, "nooit negatief")

    def test_failed_sync_keeps_the_previous_copy(self):
        with mock.patch.dict(offers.FETCHERS, {"ah": lambda: returned(ah.bonus_products_in(BONUS_PAGE))}):
            run(offers.sync("ah"))

        def boom():
            raise RuntimeError("AH is stuk")

        with mock.patch.dict(offers.FETCHERS, {"ah": boom}):
            with self.assertRaises(RuntimeError):
                run(offers.sync("ah"))
        self.assertEqual(run(offers.load("ah"))["total"], 3)

    def test_every_store_has_a_fetcher_and_unknown_stores_are_rejected(self):
        for store in offers.STORES:
            self.assertTrue(run(offers.load(store))["supported"], store)
        with self.assertRaises(ValueError):
            run(offers.sync("nope"))
        with self.assertRaises(ValueError):
            run(offers.load("nope"))


class OffersWeekTest(TempDataDirTest):
    """De aanbiedingen van de week die je plant (issue #160): op zondag stond de bonus van volgende week tussen die van vandaag."""

    def setUp(self):
        super().setUp()
        today = date.today()
        self.monday = today - timedelta(days=today.weekday())
        self.next_monday = self.monday + timedelta(days=7)
        sunday, next_sunday = self.monday + timedelta(days=6), self.next_monday + timedelta(days=6)
        deal = {"price": 1.0, "priceBefore": 2.0, "promoLabel": "50% korting"}
        self.items = [
            {"id": "deze", "title": "Bonus deze week", "validFrom": self.monday.isoformat(), "validUntil": sunday.isoformat(), **deal},
            {"id": "volgende", "title": "AH Kipfilet", "validFrom": self.next_monday.isoformat(), "validUntil": next_sunday.isoformat(), **deal},
            {"id": "doorlopend", "title": "Volume voordeel", "validFrom": (self.monday - timedelta(days=14)).isoformat(), "validUntil": None, **deal},
            {"id": "vandaag", "title": "Pakketkorting", "validFrom": today.isoformat(), "validUntil": today.isoformat(), **deal},
            {"id": "zonder", "title": "Picnic-achtige aanbieding", **deal},
        ]
        with mock.patch.dict(offers.FETCHERS, {"ah": lambda: returned(self.items)}):
            run(offers.sync("ah"))

    def test_this_week_leaves_out_next_weeks_bonus_and_counts_it(self):
        data = run(offers.load("ah", self.monday.isoformat()))
        self.assertEqual([o["id"] for o in data["offers"]], ["deze", "vandaag", "zonder", "doorlopend"], "de bonus van de week voor de doorlopende kortingen")
        self.assertEqual((data["total"], data["later"], data["undated"]), (4, 1, 0))
        self.assertEqual(data["week"], {"from": self.monday.isoformat(), "until": (self.monday + timedelta(days=6)).isoformat(), "current": True})
        self.assertEqual(data["period"], {"from": self.monday.isoformat(), "until": (self.monday + timedelta(days=6)).isoformat()},
                         "de bonusweek, niet de looptijd van één dag van de pakketkortingen")

    def test_next_week_shows_its_bonus_and_what_keeps_running(self):
        data = run(offers.load("ah", (self.next_monday + timedelta(days=3)).isoformat()))
        self.assertEqual([o["id"] for o in data["offers"]], ["volgende", "doorlopend"], "elke dag van de week wijst dezelfde week aan")
        self.assertEqual((data["later"], data["undated"]), (0, 1), "zonder looptijd is alleen vandaag bekend")
        self.assertEqual(data["week"]["from"], self.next_monday.isoformat())
        self.assertFalse(data["week"]["current"])
        self.assertEqual(data["period"]["from"], self.next_monday.isoformat())

    def test_without_a_week_everything_that_has_not_expired_is_given(self):
        data = run(offers.load("ah"))
        self.assertEqual(data["total"], 5, "zo blijft de AI in alles zoeken wat bekend is")
        self.assertIsNone(data["week"])

    def test_a_week_that_is_not_a_date_is_refused(self):
        for week in ("volgende", "2026-13-01", ""):
            with self.assertRaises(ValueError, msg=week):
                run(offers.load("ah", week))

    def test_each_week_has_its_own_signature(self):
        this_week = run(offers.signature("ah", self.monday.isoformat()))
        self.assertEqual(this_week, run(offers.signature("ah", (self.monday + timedelta(days=2)).isoformat())))
        self.assertNotEqual(this_week, run(offers.signature("ah", self.next_monday.isoformat())))

    def test_recipe_suggestions_use_the_offers_of_the_week(self):
        with mock.patch.object(offers, "COLLECTORS", (("mealie", lambda: returned([
                {"id": "r1", "title": "Pasta", "image": "", "url": "", "ingredients": ["400 g kipfilet"], "course": "hoofdgerecht"}])),)):
            run(offers.sync_recipes())
        later = run(offers.suggest("ah", week=self.next_monday.isoformat()))
        self.assertEqual([r["id"] for r in later["recipes"]], ["r1"])
        self.assertEqual(later["week"]["from"], self.next_monday.isoformat())
        self.assertEqual(run(offers.suggest("ah", week=self.monday.isoformat()))["recipes"], [], "dat product is deze week niet in de aanbieding")

    def test_a_product_on_offer_this_week_and_next_week_keeps_both_deals(self):
        sunday = self.monday + timedelta(days=6)
        twice = [
            {"id": "4004", "title": "AH Rundergehakt", "price": 3.0, "priceBefore": 4.0, "promoLabel": "25% korting",
             "validFrom": self.monday.isoformat(), "validUntil": sunday.isoformat()},
            {"id": "4004", "title": "AH Rundergehakt", "price": 4.0, "priceBefore": None, "promoLabel": "2 voor 5.00",
             "validFrom": self.next_monday.isoformat(), "validUntil": (self.next_monday + timedelta(days=6)).isoformat()},
        ]
        with mock.patch.dict(offers.FETCHERS, {"ah": lambda: returned(twice + twice[:1])}):
            run(offers.sync("ah"))
        self.assertEqual([o["promoLabel"] for o in run(offers.load("ah", self.monday.isoformat()))["offers"]], ["25% korting"])
        self.assertEqual(run(offers.load("ah", self.monday.isoformat()))["later"], 1)
        self.assertEqual([o["promoLabel"] for o in run(offers.load("ah", self.next_monday.isoformat()))["offers"]], ["2 voor 5.00"])
        self.assertEqual([o["promoLabel"] for o in run(offers.load("ah"))["offers"]], ["25% korting"], "zonder week: elk product een keer, de actie van nu")
        with closing(db(offers)) as conn:
            history = conn.execute("SELECT promo_label FROM offer_history WHERE product_id = '4004'").fetchall()
        self.assertEqual([row["promo_label"] for row in history], ["25% korting"], "de actie van volgende week komt in de geschiedenis als hij begint")

    def test_two_deals_in_the_same_week_show_the_most_specific_one(self):
        long_running = {"id": "9", "title": "Kerstkoekjes", "price": 2.0, "promoLabel": "Feestfavoriet",
                        "validFrom": (self.monday - timedelta(days=14)).isoformat(), "validUntil": (self.monday + timedelta(days=40)).isoformat()}
        weekly = {**long_running, "promoLabel": "2e halve prijs", "validFrom": self.next_monday.isoformat(),
                  "validUntil": (self.next_monday + timedelta(days=6)).isoformat()}
        with mock.patch.dict(offers.FETCHERS, {"ah": lambda: returned([long_running, weekly])}):
            run(offers.sync("ah"))
        self.assertEqual([o["promoLabel"] for o in run(offers.load("ah", self.next_monday.isoformat()))["offers"]], ["2e halve prijs"])
        self.assertEqual([o["promoLabel"] for o in run(offers.load("ah", self.monday.isoformat()))["offers"]], ["Feestfavoriet"])

    async def test_the_routes_take_the_week_and_default_to_this_week(self):
        client = self.client_for(create_app())
        default = await client.get("/api/offers?store=ah")
        self.assertTrue(default.get_json()["week"]["current"])
        self.assertEqual(default.get_json()["later"], 1)
        later = await client.get(f"/api/offers?store=ah&week={self.next_monday.isoformat()}")
        self.assertEqual({o["id"] for o in later.get_json()["offers"]}, {"volgende", "doorlopend"})
        self.assertNotEqual(later.headers["ETag"], default.headers["ETag"], "elke week heeft een eigen ETag")
        self.assertEqual((await client.get("/api/offers?store=ah&week=morgen")).status_code, 400)
        self.assertEqual((await client.get("/api/offers/recipes?store=ah&week=morgen")).status_code, 400)
        self.assertEqual((await client.get(f"/api/offers/recipes?store=ah&week={self.next_monday.isoformat()}")).status_code, 200)


class OffersTableMigrationTest(TempDataDirTest):
    def test_a_table_from_before_2_84_loses_its_key_and_keeps_its_rows(self):
        with closing(db()) as conn, conn:
            conn.execute(
                "CREATE TABLE offers (store TEXT NOT NULL, product_id TEXT NOT NULL, position INTEGER NOT NULL, title TEXT NOT NULL, brand TEXT, "
                "unit_size TEXT, price REAL, price_before REAL, promo_label TEXT, image TEXT, url TEXT, valid_until TEXT, PRIMARY KEY (store, product_id))"
            )
            conn.execute("INSERT INTO offers (store, product_id, position, title, price, promo_label) VALUES ('ah', '1', 0, 'AH Kipfilet', 3.0, '1+1 gratis')")
        data = run(offers.load("ah"))
        self.assertEqual([(o["id"], o["title"], o["promoLabel"]) for o in data["offers"]], [("1", "AH Kipfilet", "1+1 gratis")])
        with closing(db(offers)) as conn:
            self.assertFalse(any(row["pk"] for row in conn.execute("PRAGMA table_info(offers)")))
            conn.execute("INSERT INTO offers (store, product_id, position, title) VALUES ('ah', '1', 1, 'AH Kipfilet')")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM offers WHERE product_id = '1'").fetchone()[0], 2, "een product mag er nu twee keer in")


class LinkingTest(AppTestCase):
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
        collectors = tuple((name, (lambda items=items: returned(items))) for name, items in collected.items())
        with mock.patch.object(offers, "COLLECTORS", collectors):
            return run(offers.sync_recipes())

    def test_recipes_are_ranked_by_share_of_ingredients_on_offer(self):
        with mock.patch.dict(offers.FETCHERS, {"ah": lambda: returned(ah.bonus_products_in(BONUS_PAGE))}):
            run(offers.sync("ah"))
        self.index({
            "mealie": [
                {"id": "r1", "title": "Kip met tomaat", "image": "", "url": "http://m/r1",
                 "ingredients": ["400 g kipfilet", "4 tomaten", "zout", "2 el olijfolie"]},
                {"id": "r2", "title": "Stoofpot", "image": "", "url": "", "ingredients": ["kipfilet", "wortelen", "aardappelen", "bouillon", "prei"]},
                {"id": "r3", "title": "Taart", "image": "", "url": "", "ingredients": ["bloem", "suiker", "eieren"]},
            ]
        })
        data = run(offers.suggest("ah"))
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
        with mock.patch.dict(offers.FETCHERS, {"ah": lambda: returned(fetched)}):
            run(offers.sync("ah"))
        self.index({"mealie": [{
            "id": "r1", "title": "Romige tagliatelle met zalm", "image": "", "url": "",
            "ingredients": ["300 g tagliatelle", "1 middelgrote ui", "250 g champignons", "2 el milde olijfolie",
                            "400 g verse spinazie", "100 g Noorse gerookte zalmfilet", "200 g zuivelspread knoflook en kruiden"],
        }]})
        recipe = run(offers.suggest("ah"))["recipes"][0]
        self.assertEqual((recipe["matchedCount"], recipe["ingredientCount"]), (2, 6), "de olijfolie is een basisingrediënt en telt niet mee")
        self.assertEqual(sorted(m["offer"]["id"] for m in recipe["matched"]), ["1", "6"])
        self.assertEqual(recipe["saving"], 2.0, "twee keer 1,00 goedkoper, niet 11,41")

    def reported_setup(self):
        fetched = [
            {"id": "1", "title": "AH Kipfilet", "price": 3.0, "priceBefore": 4.0, "promoLabel": "Bonus"},
            {"id": "2", "title": "Jumbo Kipfilet", "price": 3.5, "priceBefore": 4.0, "promoLabel": "Bonus"},
            {"id": "3", "title": "AH Tomaten rond", "price": 1.0, "priceBefore": 2.0, "promoLabel": "Bonus"},
        ]
        with mock.patch.dict(offers.FETCHERS, {"ah": lambda: returned(fetched)}):
            run(offers.sync("ah"))
        self.index({"mealie": [
            {"id": "r1", "title": "Kip met tomaat", "image": "", "url": "", "ingredients": ["400 g kipfilet", "4 tomaten"]},
            {"id": "r2", "title": "Kipsalade", "image": "", "url": "", "ingredients": ["kipfilet", "tomaten", "sla", "komkommer", "ui"]},
        ]})

    def matched_ids(self, recipe_id):
        recipe = next(r for r in run(offers.suggest("ah"))["recipes"] if r["id"] == recipe_id)
        return {m["ingredient"]: m["offer"]["id"] for m in recipe["matched"]}

    def test_a_reported_match_does_not_come_back_and_the_next_best_product_takes_its_place(self):
        self.reported_setup()
        self.assertEqual(self.matched_ids("r1")["400 g kipfilet"], "1")
        reported = run(offers.report_mismatch("ah", "400 g kipfilet", "1"))
        self.assertEqual((reported["offerId"], reported["head"]), ("1", offers.head_of("400 g kipfilet")))
        self.assertEqual(self.matched_ids("r1")["400 g kipfilet"], "2", "het andere kipfilet-product blijft over")
        self.assertEqual(self.matched_ids("r1")["4 tomaten"], "3", "andere koppelingen blijven staan")

    def test_a_report_counts_for_every_recipe_with_the_same_main_word_but_only_in_that_store(self):
        self.reported_setup()
        run(offers.report_mismatch("ah", "400 g kipfilet", "1"))
        self.assertEqual(self.matched_ids("r2")["kipfilet"], "2", "ook in een ander recept met kipfilet")
        with mock.patch.dict(offers.FETCHERS, {"jumbo": lambda: returned([{"id": "1", "title": "Jumbo Kipfilet", "price": 3.0, "priceBefore": 4.0, "promoLabel": "Bonus"}])}):
            run(offers.sync("jumbo"))
        recipe = next(r for r in run(offers.suggest("jumbo"))["recipes"] if r["id"] == "r1")
        self.assertEqual({m["offer"]["id"] for m in recipe["matched"]}, {"1"}, "product 1 bij Jumbo is een ander product dan product 1 bij AH")

    def test_a_report_can_be_undone_and_survives_a_new_sync(self):
        self.reported_setup()
        run(offers.report_mismatch("ah", "400 g kipfilet", "1"))
        fetched = [{"id": "1", "title": "AH Kipfilet", "price": 3.0, "priceBefore": 4.0, "promoLabel": "Bonus"},
                   {"id": "3", "title": "AH Tomaten rond", "price": 1.0, "priceBefore": 2.0, "promoLabel": "Bonus"}]
        with mock.patch.dict(offers.FETCHERS, {"ah": lambda: returned(fetched)}):
            run(offers.sync("ah"))
        self.assertNotIn("400 g kipfilet", self.matched_ids("r1"), "na een nieuwe synchronisatie blijft de melding gelden")
        run(offers.undo_mismatch("ah", "400 g kipfilet", "1"))
        self.assertEqual(self.matched_ids("r1")["400 g kipfilet"], "1")

    def test_reporting_validates_store_ingredient_and_product(self):
        self.reported_setup()
        with self.assertRaises(ValueError):
            run(offers.report_mismatch("nope", "kipfilet", "1"))
        with self.assertRaises(ValueError):
            run(offers.report_mismatch("ah", "zout", "1"))  # een basisingrediënt koppelt niet, dus valt er niets te melden
        with self.assertRaises(ValueError):
            run(offers.report_mismatch("ah", "kipfilet", "999"))
        self.assertEqual(self.matched_ids("r1")["400 g kipfilet"], "1")

    def test_reports_from_before_reason_and_source_still_work(self):
        self.reported_setup()
        with closing(db(offers)) as conn, conn:
            conn.execute("DROP TABLE offer_rejections")
            conn.execute("CREATE TABLE offer_rejections (store TEXT NOT NULL, product_id TEXT NOT NULL, head TEXT NOT NULL, ingredient TEXT NOT NULL, "
                         "title TEXT NOT NULL, reported_at TEXT NOT NULL, PRIMARY KEY (store, product_id, head))")
            conn.execute("INSERT INTO offer_rejections VALUES ('ah', '1', 'kipfilet', '400 g kipfilet', 'AH Kipfilet', '2026-10-22T20:00:00+00:00')")
        listed = run(offers.list_mismatches("ah"))
        self.assertEqual((listed["total"], listed["reports"][0]["source"], listed["reports"][0]["reason"]), (1, "app", ""))
        run(offers.report_mismatch("ah", "4 tomaten", "3", "geen tomaat", source="assistant"))
        self.assertEqual(run(offers.list_mismatches("ah"))["total"], 2)

    def test_an_english_line_is_reported_under_its_dutch_main_word(self):
        self.reported_setup()
        self.index({"mealie": [{"id": "e1", "title": "Chicken", "image": "", "url": "", "ingredients": ["400 g chicken breast", "4 tomatoes"]}]})
        self.assertEqual(self.matched_ids("e1")["400 g chicken breast"], "1")
        run(offers.report_mismatch("ah", "400 g chicken breast", "1"))
        self.assertEqual(self.matched_ids("e1")["400 g chicken breast"], "2")

    def course_setup(self):
        fetched = [
            {"id": "1", "title": "AH Kipfilet", "price": 3.0, "priceBefore": 4.0, "promoLabel": "Bonus"},
            {"id": "2", "title": "AH Tomaten rond", "price": 1.0, "priceBefore": 2.0, "promoLabel": "Bonus"},
            {"id": "3", "title": "AH Eieren 10 stuks", "price": 2.0, "priceBefore": 3.0, "promoLabel": "Bonus"},
        ]
        with mock.patch.dict(offers.FETCHERS, {"ah": lambda: returned(fetched)}):
            run(offers.sync("ah"))
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
        recipe = {r["id"]: r for r in run(offers.suggest("ah"))["recipes"]}["a"]
        self.assertEqual((recipe["matchedCount"], recipe["ingredientCount"]), (2, 3), "olijfolie en zout zijn basis")
        self.assertEqual(recipe["missing"], ["1 courgette"])
        roerei = {r["id"]: r for r in run(offers.suggest("ah"))["recipes"]}["b"]
        self.assertEqual((roerei["matchedCount"], roerei["ingredientCount"], roerei["missing"]), (2, 2, []), "roomboter is basis")

    def test_the_most_complete_recipe_comes_first(self):
        self.course_setup()
        self.assertEqual([r["id"] for r in run(offers.suggest("ah"))["recipes"]], ["b", "a", "c"])

    def test_courses_are_counted_over_all_suggestions_and_the_filter_applies_before_the_limit(self):
        self.course_setup()
        data = run(offers.suggest("ah"))
        self.assertEqual(data["courses"], [{"id": "ontbijt", "count": 1}, {"id": "hoofdgerecht", "count": 1}, {"id": "overig", "count": 1}])
        self.assertEqual(data["course"], "all")
        main = run(offers.suggest("ah", limit=1, course="hoofdgerecht"))
        self.assertEqual([r["id"] for r in main["recipes"]], ["a"], "het filter geldt vóór de limiet")
        self.assertEqual((main["total"], main["course"]), (1, "hoofdgerecht"))
        self.assertEqual(main["courses"], data["courses"], "de aantallen blijven die van alle voorstellen")
        self.assertEqual(run(offers.suggest("ah", course="dessert"))["recipes"], [])
        self.assertEqual(len(run(offers.suggest("ah", course="all"))["recipes"]), 3)

    def test_an_unknown_course_is_refused(self):
        self.course_setup()
        with self.assertRaises(ValueError):
            run(offers.suggest("ah", course="pannenkoek"))

    def test_recipes_without_a_course_are_other(self):
        self.course_setup()
        self.index({"mealie": [{"id": "z", "title": "Oud", "image": "", "url": "", "ingredients": ["4 tomaten", "400 g kipfilet"]}]})
        self.assertEqual(run(offers.suggest("ah"))["recipes"][0]["course"], "overig")

    def test_an_index_from_before_the_courses_gets_the_column(self):
        from contextlib import closing

        with closing(db(offers)) as conn:
            conn.execute("DROP TABLE offer_recipes")
            conn.execute("CREATE TABLE offer_recipes (source TEXT NOT NULL, recipe_id TEXT NOT NULL, title TEXT NOT NULL, image TEXT, url TEXT, ingredients TEXT NOT NULL, PRIMARY KEY (source, recipe_id))")
            conn.execute("INSERT INTO offer_recipes VALUES ('mealie', 'old', 'Oud recept', '', '', '[\"4 tomaten\"]')")
            conn.commit()
        with closing(db(offers)) as conn:
            self.assertIn("course", {row["name"] for row in conn.execute("PRAGMA table_info(offer_recipes)")})
            self.assertIsNone(conn.execute("SELECT course FROM offer_recipes").fetchone()["course"])

    def test_a_picnic_recipe_photo_from_before_3_7_2_gets_the_size_in_pixels(self):
        # #236: een nieuwer recept van Picnic heeft geen `large.png`, wel `1000x1000.png`. De index wordt alleen op verzoek opnieuw
        # ingelezen, dus de adressen die er al staan zetten we een keer per proces om.
        base = "https://storefront-prod.nl.picnicinternational.com/static/images"
        rows = {
            ("picnic", "new"): f"{base}/recipes/ac1b02/large.png",
            ("picnic", "done"): f"{base}/recipes/c09b4e/1000x1000.png",
            ("picnic", "none"): "",
            ("jumbo", "other"): "https://www.jumbo.com/dam-images/recipes/large.png",
        }
        with closing(db(offers)) as conn, conn:
            conn.executemany(
                "INSERT INTO offer_recipes (source, recipe_id, title, image, url, ingredients) VALUES (?, ?, 'R', ?, '', '[\"kip\"]')",
                [(source, recipe_id, image) for (source, recipe_id), image in rows.items()],
            )
        offers._PICNIC_IMAGES_REPAIRED.clear()  # zoals een nieuwe start van de app
        with closing(db(offers)) as conn:
            images = {(row["source"], row["recipe_id"]): row["image"] for row in conn.execute("SELECT source, recipe_id, image FROM offer_recipes")}
        self.assertEqual(images, {**rows, ("picnic", "new"): f"{base}/recipes/ac1b02/1000x1000.png"})
        with closing(db(offers)) as conn, conn:
            conn.execute("UPDATE offer_recipes SET image = ? WHERE recipe_id = 'new'", (f"{base}/recipes/ac1b02/large.png",))
        with closing(db(offers)) as conn:
            image = conn.execute("SELECT image FROM offer_recipes WHERE recipe_id = 'new'").fetchone()["image"]
        self.assertEqual(image, f"{base}/recipes/ac1b02/large.png", "niet bij elke verbinding opnieuw")

    def test_the_best_fitting_offer_wins_over_the_first_one(self):
        fetched = [
            {"id": "1", "title": "AH Kipfilet Mediterraan gekruid", "price": 4.0, "priceBefore": 5.0, "promoLabel": "Bonus"},
            {"id": "2", "title": "AH Kipfilet", "price": 3.0, "priceBefore": 4.0, "promoLabel": "Bonus"},
        ]
        with mock.patch.dict(offers.FETCHERS, {"ah": lambda: returned(fetched)}):
            run(offers.sync("ah"))
        self.index({"mealie": [{"id": "r1", "title": "Kip", "image": "", "url": "", "ingredients": ["400 g kipfilet"]}]})
        self.assertEqual(run(offers.suggest("ah"))["recipes"][0]["matched"][0]["offer"]["id"], "2")

    def test_a_failing_source_does_not_block_the_others(self):
        def broken():
            raise RuntimeError("Jumbo kapot")

        def unlinked():
            raise LookupError("niet gekoppeld")

        collectors = (("mealie", lambda: returned([{"id": "r1", "title": "A", "image": "", "url": "", "ingredients": ["kip"]}])),
                      ("jumbo", broken), ("picnic", unlinked))
        with mock.patch.object(offers, "COLLECTORS", collectors):
            result = run(offers.sync_recipes())
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

        with mock.patch.object(ah_recipes, "load", new_callable=mock.AsyncMock, return_value=saved), \
                mock.patch.object(ah, "get_recipe_jsonld", side_effect=jsonld), \
                mock.patch.object(offers, "AH_NEW_RECIPES_PER_SYNC", 2):
            self.assertEqual(len(run(offers._collect_ah())), 2)
        self.assertEqual(calls, [1, 2])


class CourseCollectorsTest(TempDataDirTest):
    def test_each_source_gives_its_course(self):
        recipe = {"id": "m1", "name": "Pasta", "slug": "pasta", "recipeIngredient": [{"food": {"name": "pasta"}}],
                  "recipeCategory": [{"name": "Avondeten"}], "tags": [{"name": "Snel"}]}
        with mock.patch.object(offers.mealie, "list_recipes", new_callable=mock.AsyncMock, return_value=[{"slug": "pasta"}]), \
                mock.patch.object(offers.mealie, "get_recipe", new_callable=mock.AsyncMock, return_value=recipe), \
                mock.patch.object(offers.mealie, "recipe_page_url", new_callable=mock.AsyncMock, return_value="http://m/pasta"), \
                mock.patch.object(offers.mealie_images, "image_path", return_value=""):
            self.assertEqual(run(offers._collect_mealie())[0]["course"], "hoofdgerecht")

        jumbo_recipe = {"id": "j1", "name": "Ontbijtbowl", "ingredients": [{"name": "yoghurt"}], "course": "Ontbijt"}
        with mock.patch.object(offers.jumbo, "configured_refresh_token", return_value="x"), \
                mock.patch.object(offers.jumbo_recipes, "get_favorites", new_callable=mock.AsyncMock, return_value={"recipes": [{"id": "j1"}]}), \
                mock.patch.object(offers.jumbo_recipes, "get_recipe", new_callable=mock.AsyncMock, return_value=jumbo_recipe), \
                mock.patch.object(offers.jumbo_recipes, "source_url", return_value=""):
            self.assertEqual(run(offers._collect_jumbo())[0]["course"], "ontbijt")

        picnic_recipe = {"id": "p1", "name": "Soep", "ingredients": [{"name": "prei"}]}
        with mock.patch.object(offers.picnic, "configured_auth_key", return_value="x"), \
                mock.patch.object(offers.picnic_recipes, "get_recipes", new_callable=mock.AsyncMock, return_value=[{"id": "p1", "tags": ["Soepen"]}]), \
                mock.patch.object(offers.picnic_recipes, "get_recipe", new_callable=mock.AsyncMock, return_value=picnic_recipe), \
                mock.patch.object(offers.picnic_recipes, "source_url", return_value=""):
            self.assertEqual(run(offers._collect_picnic())[0]["course"], "soep")

        crisp_recipe = {"id": "55", "name": "Curry", "image": "", "url": None,
                        "ingredients": [{"line": "150 g rijst", "name": "rijst"}, {"line": "olie", "name": ""}, {"line": "", "name": ""}]}
        with mock.patch.object(offers.crisp, "configured_client_id", return_value="x"), \
                mock.patch.object(offers.crisp, "get_saved_recipes", return_value={"recipes": [{"id": "55", "tags": ["Soep"]}], "total": 1}), \
                mock.patch.object(offers.crisp, "get_recipe", return_value=crisp_recipe):
            found = run(offers._collect_crisp())[0]
        self.assertEqual((found["course"], found["ingredients"], found["url"]), ("soep", ["rijst", "olie"], "https://crisp.nl/#recipe-55"))
        with mock.patch.object(offers.crisp, "get_recipe", return_value={**crisp_recipe, "url": "https://crisp.nl/r/abc"}), \
                mock.patch.object(offers.crisp, "configured_client_id", return_value="x"), \
                mock.patch.object(offers.crisp, "get_saved_recipes", return_value={"recipes": [{"id": "55", "tags": []}], "total": 1}):
            self.assertEqual(run(offers._collect_crisp())[0]["url"], "https://crisp.nl/r/abc", "de deellink van Crisp als die er is")
        with mock.patch.object(offers.crisp, "configured_client_id", return_value=""), \
                mock.patch.object(offers.crisp, "get_saved_recipes", side_effect=AssertionError("niet ophalen")), \
                self.assertRaises(LookupError):
            run(offers._collect_crisp())

    def test_ah_reads_the_category_and_refetches_recipes_indexed_without_one(self):
        saved = {"recipes": [{"id": str(i), "title": f"R{i}", "image": "", "url": ""} for i in (1, 2)]}
        calls = []

        def jsonld(recipe_id):
            calls.append(recipe_id)
            return {"recipeIngredient": ["kip"], "recipeCategory": "Hoofdgerecht" if recipe_id == 1 else ["Dessert"]}

        with mock.patch.object(ah_recipes, "load", new_callable=mock.AsyncMock, return_value=saved), mock.patch.object(ah, "get_recipe_jsonld", side_effect=jsonld):
            first = run(offers._collect_ah())
            self.assertEqual([item["course"] for item in first], ["hoofdgerecht", "dessert"])
        # In de index, recept 1 mét soort en recept 2 zonder (ingelezen vóór de soorten): alleen 2 wordt opnieuw opgehaald.
        from contextlib import closing

        with closing(db(offers)) as conn, conn:
            for recipe_id, course in (("1", "hoofdgerecht"), ("2", None)):
                conn.execute(
                    "INSERT OR REPLACE INTO offer_recipes (source, recipe_id, title, image, url, ingredients, course) VALUES ('ah', ?, 'R', '', '', '[\"kip\"]', ?)",
                    (recipe_id, course),
                )
        calls.clear()
        with mock.patch.object(ah_recipes, "load", new_callable=mock.AsyncMock, return_value=saved), mock.patch.object(ah, "get_recipe_jsonld", side_effect=jsonld):
            again = run(offers._collect_ah())
        self.assertEqual(calls, [2])
        self.assertEqual([item["course"] for item in again], ["hoofdgerecht", "dessert"])


class CollectorPoolTest(TempDataDirTest):
    """De vier pools van de receptenindex (Mealie, Jumbo, Picnic, Crisp): een semafoor van `WORKERS` met gather, de volgorde van de
    kaarten, en een recept dat niet lukt valt weg."""

    def test_each_runs_at_most_workers_at_once_keeps_the_order_and_drops_an_empty_item(self) -> None:
        running: list[int] = [0]
        most: list[int] = [0]

        async def one(item: int) -> dict[str, Any] | None:
            running[0] += 1
            most[0] = max(most[0], running[0])
            await asyncio.sleep(0.001 * (item % 4))  # door elkaar klaar: de volgorde komt uit de invoer, niet uit wie het eerst klaar is
            running[0] -= 1
            return None if item == 5 else {"id": item}

        items = list(range(offers.WORKERS * 3))
        found = run(offers._each(one, items))
        self.assertEqual(most[0], offers.WORKERS)
        self.assertEqual([item["id"] for item in found], [item for item in items if item != 5])

    def test_each_waits_for_the_rest_and_raises_the_first_failure_in_order(self) -> None:
        finished: list[int] = []

        async def one(item: int) -> dict[str, Any] | None:
            await asyncio.sleep(0.001 * (10 - item))
            if item in (3, 7):
                raise RuntimeError(f"kapot {item}")
            finished.append(item)
            return {"id": item}

        with self.assertRaisesRegex(RuntimeError, "kapot 3"):
            run(offers._each(one, range(10)))
        self.assertEqual(sorted(finished), [0, 1, 2, 4, 5, 6, 8, 9], "zoals de threadpool: de rest liep nog af")

    def cards(self) -> list[dict[str, Any]]:
        return [{"id": str(n), "slug": str(n), "tags": []} for n in range(12)]

    def counting(self, make: Callable[[str], dict[str, Any]]) -> tuple[Callable[[str], Awaitable[dict[str, Any]]], list[int]]:
        """Een async callee die telt hoeveel er tegelijk lopen, de loop een paar keer vrijgeeft (zoals een echte aanvraag bij
        de winkel) en voor recept 4 faalt."""
        state: list[int] = [0, 0]  # nu, hoogste

        async def fetch(recipe_id: str) -> dict[str, Any]:
            state[0] += 1
            state[1] = max(state[1], state[0])
            try:
                for _ in range(3):
                    await asyncio.sleep(0)
                if recipe_id == "4":
                    raise RuntimeError("recept 4 lukt niet")
                return make(recipe_id)
            finally:
                state[0] -= 1

        return fetch, state

    def assert_pool(self, found: list[dict[str, Any]], state: list[int]) -> None:
        self.assertEqual([item["id"] for item in found], [str(n) for n in range(12) if n != 4], "de volgorde van de kaarten, zonder het mislukte recept")
        self.assertEqual(state[1], offers.WORKERS)

    def test_the_mealie_pool(self) -> None:
        # Mealie is async: de pool laat echt WORKERS recepten tegelijk ophalen, niet meer en niet minder.
        state: list[int] = [0, 0]  # nu, hoogste

        async def recipe(request: httpx.Request) -> httpx.Response:
            slug = request.url.path.rsplit("/", 1)[1]
            state[0] += 1
            state[1] = max(state[1], state[0])
            try:
                for _ in range(30):  # hooguit 0,3 s wachten tot de pool vol is
                    if state[0] >= offers.WORKERS:
                        break
                    await asyncio.sleep(0.01)
                await asyncio.sleep(0.01)
            finally:
                state[0] -= 1
            if slug == "4":
                return httpx.Response(500, json={"detail": "recept 4 lukt niet"})
            return httpx.Response(200, json={"id": slug, "name": f"R{slug}", "recipeIngredient": [{"food": {"name": "kip"}}]})

        with mock.patch.object(offers.mealie, "list_recipes", new_callable=mock.AsyncMock, return_value=self.cards()), \
                mock.patch.object(offers.mealie, "_endpoint", new_callable=mock.AsyncMock, return_value=("http://mealie.test", "t")), \
                mock.patch.object(offers.mealie, "recipe_page_url", new_callable=mock.AsyncMock, return_value=""), \
                mock.patch.object(offers.mealie_images, "image_path", return_value=""), routes() as api:
            fetched = api.get(url__regex=r"^http://mealie\.test/api/recipes/\d+$").mock(side_effect=recipe)
            found = run(offers._collect_mealie())
        self.assertEqual(fetched.call_count, 12)
        self.assertEqual([item["id"] for item in found], [str(n) for n in range(12) if n != 4], "de volgorde van de kaarten, zonder het mislukte recept")
        self.assertEqual(state[1], offers.WORKERS)

    def test_the_jumbo_pool(self) -> None:
        fetch, state = self.counting(lambda recipe_id: {"id": recipe_id, "name": f"R{recipe_id}", "ingredients": [{"name": "kip"}]})
        with mock.patch.object(offers.jumbo, "configured_refresh_token", return_value="x"), \
                mock.patch.object(offers.jumbo_recipes, "get_favorites", new_callable=mock.AsyncMock, return_value={"recipes": self.cards()}), \
                mock.patch.object(offers.jumbo_recipes, "get_recipe", new_callable=mock.AsyncMock, side_effect=fetch), \
                mock.patch.object(offers.jumbo_recipes, "source_url", return_value=""):
            self.assert_pool(run(offers._collect_jumbo()), state)

    def test_the_picnic_pool(self) -> None:
        fetch, state = self.counting(lambda recipe_id: {"id": recipe_id, "name": f"R{recipe_id}", "ingredients": [{"name": "kip"}]})
        with mock.patch.object(offers.picnic, "configured_auth_key", return_value="x"), \
                mock.patch.object(offers.picnic_recipes, "get_recipes", new_callable=mock.AsyncMock, side_effect=lambda source: self.cards() if source == "saved" else []), \
                mock.patch.object(offers.picnic_recipes, "get_recipe", new_callable=mock.AsyncMock, side_effect=fetch), \
                mock.patch.object(offers.picnic_recipes, "source_url", return_value=""):
            self.assert_pool(run(offers._collect_picnic()), state)

    def test_the_crisp_pool(self) -> None:
        fetch, state = self.counting(lambda recipe_id: {"id": recipe_id, "name": f"R{recipe_id}", "ingredients": [{"name": "kip"}], "url": "u"})
        with mock.patch.object(offers.crisp, "configured_client_id", return_value="x"), \
                mock.patch.object(offers.crisp, "get_saved_recipes", new_callable=mock.AsyncMock, return_value={"recipes": self.cards(), "total": 12}), \
                mock.patch.object(offers.crisp, "get_recipe", new_callable=mock.AsyncMock, side_effect=fetch):
            self.assert_pool(run(offers._collect_crisp()), state)


class OffersTabMarkupTest(AppTestCase):
    def test_switching_store_shows_a_spinning_icon_while_the_offers_load(self):
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        script = (root / "static" / "offers.js").read_text(encoding="utf-8")
        html = (root / "templates" / "index.html").read_text(encoding="utf-8")
        css = (root / "static" / "styles.css").read_text(encoding="utf-8")
        app_js = (root / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn('const MDI_LOADING = "M12,4V2A10,10 0 0,0 2,12H4A8,8 0 0,1 12,4Z";', app_js, "het officiële MDI-icoon, geen eigen tekening")
        self.assertIn('<div id="offers-loading" class="offers-loading" role="status" aria-live="polite" hidden></div>', html)
        body = script[script.index("async function showOffers()"):script.index("function offerRecipeCard")]
        self.assertLess(body.index("renderOffersLoading()"), body.index("await offersRequest"), "de laadstand staat er al voordat er iets wordt opgehaald")
        # Een traag antwoord van de vorige winkel wordt genegeerd, ook bij een fout; en wisselt renderOffersStore naar de
        # voorkeurssupermarkt, dan laadt die zelf en stopt deze aanvraag.
        self.assertEqual(body.count("loadId !== offersLoadId"), 3)
        self.assertIn("mdiIcon(MDI_LOADING", script)
        self.assertIn("const offersCache = {};", script, "de laatst geladen lijst per winkel staat er bij terugkeren meteen")
        self.assertIn("if (cached) renderOffers(cached);\n  else renderOffersLoading();", body.replace(body[:0], ""), "met een lijst in de cache geen spinner")
        self.assertIn("JSON.stringify(cached) !== JSON.stringify(data)", script, "alleen bij een andere lijst wordt het scherm vernieuwd")
        self.assertIn('t("offers.loading", { store:', script)
        self.assertRegex(css, r"\.offers-loading__icon \{[^}]*animation: spin 1s linear infinite;")
        self.assertRegex(css, r"@media \(prefers-reduced-motion: reduce\) \{\s*\.offers-loading__icon \{\s*animation: none;", "zonder bewegingsvoorkeur draait het icoon niet; de tekst blijft")

    def test_a_wrong_match_can_be_reported_from_the_card_and_from_the_dialog_with_an_mdi_icon(self):
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        script = (root / "static" / "offers.js").read_text(encoding="utf-8")
        html = (root / "templates" / "index.html").read_text(encoding="utf-8")
        app_js = (root / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn('class="offer-flag"', script)
        self.assertIn("mdiIcon(MDI_FLAG_OUTLINE)", script, "het icoon is een MDI-icoon, geen eigen tekening")
        self.assertIn('const MDI_FLAG_OUTLINE = "M12.36,6L12.76,8H18V14H14.64L14.24,12H7V6H12.36M14,4H5V21H7V14H12.6L13,16H20V6H14.4";', app_js)
        self.assertIn('"/api/offers/rejections"', script)
        self.assertIn('method: undo ? "DELETE" : "POST"', script, "een melding is direct ongedaan te maken")
        self.assertIn('id="offer-product-report"', html)
        self.assertIn('d="M12.36,6L12.76,8H18V14H14.64L14.24,12H7V6H12.36M14,4H5V21H7V14H12.6L13,16H20V6H14.4"', html)

    def test_offers_follows_the_layout_of_the_stores_tab(self):
        import re
        from pathlib import Path

        html = (Path(__file__).resolve().parent.parent / "templates" / "index.html").read_text(encoding="utf-8")
        section = html[html.index('<section id="offers"'):html.index('<section id="url"')]
        self.assertIn('<div class="subtab-menu" id="offers-store-menu" hidden>', section)
        self.assertLess(section.index("offers-store-menu"), section.index('data-subtab="offers-items"'))
        self.assertEqual(re.findall(r'<a class="subtab[^"]*" data-subtab="([\w-]+)"', section), ["offers-items", "offers-suggestions", "offers-history"])
        self.assertIn('<div id="offers-items" class="subpanel is-active">', section)
        self.assertIn('<div id="offers-suggestions" class="subpanel">', section)
        self.assertIn('<div id="offers-history" class="subpanel">', section)
        self.assertNotIn("offers-store-slot", section)

    def test_the_price_history_tab_is_wired_to_the_history_route(self):
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        html = (root / "templates" / "index.html").read_text(encoding="utf-8")
        script = (root / "static" / "offers_history.js").read_text(encoding="utf-8")
        offers_js = (root / "static" / "offers.js").read_text(encoding="utf-8")
        self.assertLess(html.index("offers.js"), html.index("offers_history.js"))
        self.assertIn("/api/prices/history?", script)
        self.assertIn("offersState.store", script)
        self.assertIn('data-subtab="offers-history"', script)
        self.assertIn("offerHistoryStoreChanged", offers_js, "wisselen van winkel ververst de geschiedenis")
        self.assertIn("taken.some(", script, "prijslabels in de grafiek ontwijken elkaar, de balken en de tekst Gewone prijs")
        for hook in ("offers-history-search", "offers-history-list", "offers-history-detail", "offers-history-status"):
            self.assertIn(f'id="{hook}"', html)
            self.assertIn(f"#{hook}", script)

    def test_the_offers_follow_the_week_of_the_week_menu(self):
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        script = (root / "static" / "offers.js").read_text(encoding="utf-8")
        html = (root / "templates" / "index.html").read_text(encoding="utf-8")
        app_js = (root / "static" / "app.js").read_text(encoding="utf-8")
        section = html[html.index('<section id="offers"'):html.index('<section id="url"')]
        self.assertEqual(section.count('<div class="toolbar offers-week"'), 2, "een weekkeuze bij Aanbiedingen en bij Recepten")
        self.assertEqual(section.count('data-offers-week="-1"'), 2)
        self.assertIn('id="offers-later-show"', section)
        self.assertIn("&week=${encodeURIComponent(week)}", script, "de aanbiedingen worden per week opgehaald")
        self.assertIn("const week = `&week=${encodeURIComponent(offersWeek().start)}`;", script, "de receptvoorstellen ook")
        self.assertIn("const key = `${store}|${week}`;", script, "de cache houdt de weken uit elkaar")
        self.assertIn("return Math.max(0, state.weekOffset);", script, "dezelfde week als het weekmenu, en nooit een eerdere dan deze")
        self.assertIn("setPlanWeek(offset);", script, "een andere week hier zet het weekmenu op dezelfde week")
        self.assertIn("function setPlanWeek(offset) {\n  state.weekOffset = offset;", app_js)
        self.assertIn("$(\"#week-offset\").textContent = weekOffsetText(state.weekOffset);", app_js, "dezelfde tekst als in het weekmenu")

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
        switch = script[script.index("function offersSwitchStore("):script.index("// De voorwaarden van een aanbieding")]
        self.assertIn("offersState.course = \"all\";\n  showOffers();", switch, "een andere winkel zet het filter terug op alles")

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
        self.assertEqual(html.count('id="offer-product-dialog"'), 1, "er is maar één productvenster")
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
        self.assertIn('event.target.closest(".offer-match, .offer-flag")', script)
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
        self.assertIn("storeDisplayName(offersState.store)", status)
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
        with mock.patch.dict(offers.FETCHERS, {"ah": lambda: returned(fetched)}):
            run(offers.sync("ah"))
        collectors = (("mealie", lambda: returned([
            {"id": "g", "title": "Grillworst", "image": "", "url": "", "ingredients": GRILLWORST},
            {"id": "s", "title": "Warme sushibowl", "image": "", "url": "", "ingredients": SUSHIBOWL},
        ])),)
        with mock.patch.object(offers, "COLLECTORS", collectors):
            run(offers.sync_recipes())
        recipes = {recipe["id"]: recipe for recipe in run(offers.suggest("ah"))["recipes"]}
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
        with mock.patch.dict(offers.FETCHERS, {"ah": lambda: returned(fetched)}):
            run(offers.sync("ah"))
        collectors = (("mealie", lambda: returned([{"id": "p", "title": "Pasta met zalm en broccoli", "image": "", "url": "", "ingredients": [
            "500 g broccoli", "2 middelgrote uien", "200 g gerookte zalmsnippers", "150 g koelverse roomkaas met bieslook", "300 g fusilli"]}])),)
        with mock.patch.object(offers, "COLLECTORS", collectors):
            run(offers.sync_recipes())
        self.assertEqual(run(offers.suggest("ah"))["recipes"], [], "nog maar één koppeling (de zalm) van vijf: onder de ondergrens")

    def test_mealie_links_in_an_old_index_are_rebuilt_with_the_group(self):
        fetched = [real_offer("AH Kipfilet", 1), real_offer("AH Tomaten rond", 2)]
        with mock.patch.dict(offers.FETCHERS, {"ah": lambda: returned(fetched)}):
            run(offers.sync("ah"))
        lines = ["400 g kipfilet", "4 tomaten"]
        collectors = (
            ("mealie", lambda: returned([{"id": "a", "title": "A", "image": "", "url": "https://mealie.example/recipe/witlof-met-ham", "ingredients": lines},
                                {"id": "b", "title": "B", "image": "", "url": "https://mealie.example/g/zb/r/al-goed", "ingredients": lines}])),
            ("jumbo", lambda: returned([{"id": "c", "title": "C", "image": "", "url": "https://www.jumbo.com/recepten/recipe/xyz", "ingredients": lines}])),
        )
        with mock.patch.object(offers, "COLLECTORS", collectors):
            run(offers.sync_recipes())
        with mock.patch.object(offers.mealie, "mealie_url", new_callable=mock.AsyncMock, return_value="https://mealie.example"), \
                mock.patch.object(offers.mealie, "group_slug", new_callable=mock.AsyncMock, return_value="zb"):
            urls = {recipe["id"]: recipe["url"] for recipe in run(offers.suggest("ah"))["recipes"]}
        self.assertEqual(urls["a"], "https://mealie.example/g/zb/r/witlof-met-ham", "het oude adres is gecorrigeerd")
        self.assertEqual(urls["b"], "https://mealie.example/g/zb/r/al-goed", "een goed adres blijft goed")
        self.assertEqual(urls["c"], "https://www.jumbo.com/recepten/recipe/xyz", "alleen Mealie-adressen worden aangepast")
        # Zonder koppeling met Mealie blijft het bewaarde adres staan.
        with mock.patch.object(offers.mealie, "mealie_url", new_callable=mock.AsyncMock, return_value=""):
            urls = {recipe["id"]: recipe["url"] for recipe in run(offers.suggest("ah"))["recipes"]}
        self.assertEqual(urls["a"], "https://mealie.example/recipe/witlof-met-ham")

    def test_a_mealie_that_cannot_be_reached_does_not_block_the_suggestions(self):
        with mock.patch.dict(offers.FETCHERS, {"ah": lambda: returned([real_offer("AH Kipfilet", 1), real_offer("AH Tomaten rond", 2)])}):
            run(offers.sync("ah"))
        collectors = (("mealie", lambda: returned([{"id": "a", "title": "A", "image": "", "url": "https://mealie.example/recipe/x",
                                           "ingredients": ["400 g kipfilet", "4 tomaten"]}])),)
        with mock.patch.object(offers, "COLLECTORS", collectors):
            run(offers.sync_recipes())
        with mock.patch.object(offers.mealie, "mealie_url", new_callable=mock.AsyncMock, return_value="https://mealie.example"), \
                mock.patch.object(offers.mealie, "recipe_page_url", new_callable=mock.AsyncMock, side_effect=RuntimeError("weg")):
            recipes = run(offers.suggest("ah"))["recipes"]
        self.assertEqual(recipes[0]["url"], "https://mealie.example/recipe/x")

    def duplicate_setup(self, recipes):
        fetched = [real_offer("AH Kipfilet", 1), real_offer("AH Tomaten rond", 2), real_offer("AH Rijst basmati", 3)]
        with mock.patch.dict(offers.FETCHERS, {"ah": lambda: returned(fetched)}):
            run(offers.sync("ah"))
        collectors = tuple((name, (lambda items=items: returned(items))) for name, items in recipes.items())
        with mock.patch.object(offers, "COLLECTORS", collectors):
            run(offers.sync_recipes())
        return run(offers.suggest("ah"))

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
        with mock.patch.dict(offers.FETCHERS, {"ah": lambda: returned(fetched)}):
            run(offers.sync("ah"))
        collectors = (("mealie", lambda: returned([
            {"id": "p", "title": "Paprikaschotel", "image": "", "url": "", "ingredients": ["1 paprika", "2 paprika", "4 tomaten"]}])),)
        with mock.patch.object(offers, "COLLECTORS", collectors):
            run(offers.sync_recipes())
        recipe = run(offers.suggest("ah"))["recipes"][0]
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
        with mock.patch.dict(offers.FETCHERS, {"ah": lambda: returned(fetched)}):
            run(offers.sync("ah"))
        collectors = (("mealie", lambda: returned([
            {"id": key, "title": key, "image": "", "url": "", "ingredients": lines} for key, lines in recipes.items()])),)
        with mock.patch.object(offers, "COLLECTORS", collectors):
            run(offers.sync_recipes())
        return {recipe["id"]: recipe for recipe in run(offers.suggest("ah"))["recipes"]}

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
        self.client = self.client_for(create_app())

    async def test_routes_load_sync_and_suggest(self):
        self.assertEqual((await self.client.get("/api/offers?store=ah")).get_json()["syncedAt"], None)
        with mock.patch.dict(offers.FETCHERS, {"ah": lambda: returned(ah.bonus_products_in(BONUS_PAGE))}):
            synced = await self.client.post("/api/offers/sync", json={"store": "ah"})
        self.assertEqual((synced.status_code, synced.get_json()["total"]), (200, 3))
        with mock.patch.object(offers, "COLLECTORS", (("mealie", lambda: returned([
                {"id": "r1", "title": "Kip", "image": "", "url": "", "ingredients": ["kipfilet", "tomaten"], "course": "hoofdgerecht"}])),)):
            self.assertEqual((await self.client.post("/api/offers/recipes/sync")).status_code, 200)
        suggestions = (await self.client.get("/api/offers/recipes?store=ah")).get_json()
        self.assertEqual([r["id"] for r in suggestions["recipes"]], ["r1"])
        self.assertEqual(suggestions["courses"], [{"id": "hoofdgerecht", "count": 1}])
        self.assertEqual([r["id"] for r in (await self.client.get("/api/offers/recipes?store=ah&course=hoofdgerecht")).get_json()["recipes"]], ["r1"])
        self.assertEqual((await self.client.get("/api/offers/recipes?store=ah&course=dessert")).get_json()["recipes"], [])
        self.assertEqual((await self.client.get("/api/offers/recipes?store=ah&course=pannenkoek")).status_code, 400)

    async def test_reporting_a_match_over_the_api(self):
        fetched = [{"id": "1", "title": "AH Kipfilet", "price": 3.0, "priceBefore": 4.0, "promoLabel": "Bonus"}]
        with mock.patch.dict(offers.FETCHERS, {"ah": lambda: returned(fetched)}):
            await self.client.post("/api/offers/sync", json={"store": "ah"})
        body = {"store": "ah", "ingredient": "400 g kipfilet", "offerId": "1"}
        response = await self.client.post("/api/offers/rejections", json=body)
        self.assertEqual((response.status_code, response.get_json()["offerId"]), (200, "1"))
        self.assertEqual((await self.client.delete("/api/offers/rejections", json=body)).status_code, 200)
        self.assertEqual((await self.client.post("/api/offers/rejections", json={**body, "offerId": "nope"})).status_code, 400)
        self.assertEqual((await self.client.post("/api/offers/rejections", json={**body, "ingredient": ""})).status_code, 400)

    async def test_the_offers_are_cached_by_the_browser_and_only_resent_when_they_changed(self):
        fetched = [{"id": "1", "title": "AH Kipfilet", "price": 3.0, "priceBefore": 4.0, "promoLabel": "Bonus"}]
        with mock.patch.dict(offers.FETCHERS, {"ah": lambda: returned(fetched)}):
            await self.client.post("/api/offers/sync", json={"store": "ah"})
        first = await self.client.get("/api/offers?store=ah")
        etag = first.headers["ETag"]
        self.assertEqual((first.status_code, first.headers["Cache-Control"]), (200, "private, no-cache"))
        self.assertEqual(first.get_json()["total"], 1)
        again = await self.client.get("/api/offers?store=ah", headers={"If-None-Match": etag})
        self.assertEqual((again.status_code, again.get_data()), (304, b""), "ongewijzigd: geen lijst opnieuw versturen")
        self.assertEqual(again.headers["ETag"], etag)
        self.assertEqual((await self.client.get("/api/offers?store=ah", headers={"If-None-Match": '"iets-anders"'})).status_code, 200)
        self.assertNotEqual((await self.client.get("/api/offers?store=jumbo")).headers["ETag"], etag, "elke winkel heeft een eigen ETag")
        # Een nieuwe synchronisatie, een nieuwe dag en een nieuwe versie geven een andere ETag.
        with mock.patch.object(offers, "_now", return_value="2030-01-01T05:00:00+00:00"), mock.patch.dict(offers.FETCHERS, {"ah": lambda: returned(fetched)}):
            await offers.sync("ah")
        self.assertNotEqual((await self.client.get("/api/offers?store=ah")).headers["ETag"], etag)
        with mock.patch.object(offers, "date") as fake_date:
            fake_date.today.return_value.isoformat.return_value = "2031-02-03"
            self.assertNotIn("2031-02-03", etag)
            self.assertIn("2031-02-03", await offers.signature("ah"))
        with mock.patch.object(offers.about, "version", return_value="9.9.9"):
            self.assertIn("9.9.9", await offers.signature("ah"))
        # Ook een andere build van dezelfde versie (een deploy van een commit) geeft een andere ETag: anders houdt de browser na een
        # deploy de oude lijst vast, zonder de velden die de nieuwe build erbij zet.
        with mock.patch.object(offers.about, "build_commit", return_value="abc1234"):
            self.assertIn("abc1234", await offers.signature("ah"))
        self.assertNotEqual(await offers.signature("ah"), etag)
        self.assertEqual((await self.client.get("/api/offers?store=nope", headers={"If-None-Match": etag})).status_code, 400)

    async def test_the_server_keeps_the_list_per_etag_and_computes_concurrent_requests_once(self):
        # Het prijsoordeel is het dure deel: met dezelfde ETag rekent de server het niet opnieuw uit, ook niet voor een tweede
        # browser zonder If-None-Match of voor aanvragen die tegelijk binnenkomen.
        fetched = [{"id": "1", "title": "AH Kipfilet", "price": 3.0, "priceBefore": 4.0, "promoLabel": "Bonus"}]
        with mock.patch.dict(offers.FETCHERS, {"ah": lambda: returned(fetched)}):
            await self.client.post("/api/offers/sync", json={"store": "ah"})
        real_annotate = prices.annotate
        with mock.patch.object(prices, "annotate", side_effect=real_annotate) as annotate:
            first, second, third = await asyncio.gather(*(self.client.get("/api/offers?store=ah") for _ in range(3)))
            self.assertEqual({first.status_code, second.status_code, third.status_code}, {200})
            self.assertEqual(first.get_json(), second.get_json())
            self.assertEqual(annotate.call_count, 1, "drie aanvragen tegelijk: een berekening")
            later = await self.client.get("/api/offers?store=ah")
            self.assertEqual((later.get_json(), later.headers["ETag"]), (first.get_json(), first.headers["ETag"]))
            self.assertEqual(annotate.call_count, 1, "dezelfde ETag: de bewaarde lijst")
            await self.client.get("/api/offers?store=jumbo")
            self.assertEqual(annotate.call_count, 2, "een andere winkel is een andere sleutel")
            with mock.patch.object(offers, "_now", return_value="2030-01-01T05:00:00+00:00"), mock.patch.dict(offers.FETCHERS, {"ah": lambda: returned(fetched)}):
                await offers.sync("ah")
            renewed = await self.client.get("/api/offers?store=ah")
            self.assertNotEqual(renewed.headers["ETag"], first.headers["ETag"])
            self.assertEqual(annotate.call_count, 3, "een nieuwe synchronisatie: opnieuw uitrekenen")

    async def test_a_failed_offers_load_is_not_kept(self):
        with mock.patch.object(offers, "load", new_callable=mock.AsyncMock, side_effect=RuntimeError("stuk")) as load:
            self.assertEqual((await self.client.get("/api/offers?store=ah")).status_code, 424)
            self.assertEqual((await self.client.get("/api/offers?store=ah")).status_code, 424)
            self.assertEqual(load.call_count, 2, "een fout wordt niet bewaard: de volgende aanvraag probeert het opnieuw")
        self.assertEqual((await self.client.get("/api/offers?store=ah")).status_code, 200)

    async def test_the_recipes_subtab_is_hidden_unless_switched_on(self):
        import re

        async def subtab(client):
            html = (await client.get("/")).get_data(as_text=True)
            return re.search(r'<a class="subtab"[^>]*data-subtab="offers-suggestions"[^>]*>', html).group(0)

        self.assertIn(" hidden", await subtab(self.client))
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test-0123456789abcdef"}):
            self.assertNotIn(" hidden", await subtab(self.client), "met een AI-sleutel verschijnt het tabblad ook zonder de variabele")
        with mock.patch.dict(os.environ, {"MEALIESHOPPER_OFFERS_RECIPES": "true"}):
            self.assertNotIn(" hidden", await subtab(self.client_for(create_app())))
        self.assertEqual((await self.client.get("/api/offers/recipes?store=ah")).status_code, 200, "de API blijft werken")

    async def test_unknown_store_is_a_400_and_a_store_failure_a_424(self):
        self.assertEqual((await self.client.get("/api/offers?store=nope")).status_code, 400)
        self.assertEqual((await self.client.post("/api/offers/sync", json={"store": "nope"})).status_code, 400)

        def not_linked():
            raise RuntimeError("Jumbo account niet gekoppeld")

        with mock.patch.dict(offers.FETCHERS, {"jumbo": not_linked}):
            self.assertEqual((await self.client.post("/api/offers/sync", json={"store": "jumbo"})).status_code, 424)

        def boom():
            raise RuntimeError("AH is stuk")

        with mock.patch.dict(offers.FETCHERS, {"ah": boom}):
            response = await self.client.post("/api/offers/sync", json={"store": "ah"})
        self.assertEqual((response.status_code, response.get_json()["error"]), (424, "AH is stuk"))


if __name__ == "__main__":
    unittest.main()
