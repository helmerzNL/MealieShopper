import os
import sqlite3
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("MEALIESHOPPER_DATA_DIR", tempfile.mkdtemp())

from mealieshopper import auth, offer_categories, offers  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402


class TempDataDirTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"MEALIESHOPPER_DATA_DIR": tmp.name, "PASSKEY_AUTH_ENABLED": "false"})
        patcher.start()
        self.addCleanup(patcher.stop)
        auth.init_db()
        self.clock = iter(f"2026-10-{day:02d}T05:00:00+00:00" for day in range(1, 29))
        now = mock.patch.object(offers, "_now", side_effect=lambda: next(self.clock))
        now.start()
        self.addCleanup(now.stop)

    def sync(self, *items, store="ah"):
        with mock.patch.dict(offers.FETCHERS, {store: lambda: list(items)}):
            return offers.sync(store)


def item(product_id, title, price=1.0, before=2.0, label="2 voor 3", **extra):
    return {"id": product_id, "title": title, "price": price, "priceBefore": before, "promoLabel": label, **extra}


class CategoryTest(unittest.TestCase):
    def test_titles_land_in_the_expected_category(self):
        cases = {
            "Kipfilet 500 g": "vlees", "Kippensoep": "sauzen_conserven", "Appelsap 1 l": "frisdrank", "Elstar appels": "groente_fruit",
            "Coca-Cola 6 x 33 cl": "frisdrank", "Halfvolle melk": "zuivel_eieren", "Melkchocolade reep": "snacks_snoep",
            "Pindakaas": "ontbijt_beleg", "Gouda jong belegen": "kaas_vleeswaren", "Rookworst": "vlees", "Leverworst": "kaas_vleeswaren",
            "Zalmfilet": "vis", "Heineken pils": "bier_wijn", "Diepvries pizza": "diepvries", "Spaghetti": "maaltijden",
            "Douwe Egberts koffie": "koffie_thee", "Volkoren brood": "brood_bakkerij", "Eieren 10 stuks": "zuivel_eieren",
            "Pampers luiers": "baby", "Whiskas kattenvoer": "huisdier", "Shampoo": "drogisterie", "Wasmiddel": "huishouden",
            "Cheesecake": "brood_bakkerij", "Iets onbekends": "overig", "": "overig",
        }
        for title, expected in cases.items():
            self.assertEqual(offer_categories.categorize(title), expected, title)

    def test_the_store_hint_is_used_when_the_title_says_nothing(self):
        self.assertEqual(offer_categories.categorize("Elf", "Groente, aardappelen"), "groente_fruit")
        self.assertEqual(offer_categories.categorize("Kipfilet", "Zuivel"), "vlees", "de titel gaat voor")

    def test_summary_is_sorted_by_count_with_other_last(self):
        summary = offer_categories.summarize(["vlees", "overig", "overig", "overig", "groente_fruit", "groente_fruit", "vlees", "vlees", "frisdrank"])
        self.assertEqual([(c["id"], c["count"]) for c in summary], [("vlees", 3), ("groente_fruit", 2), ("frisdrank", 1), ("overig", 3)])

    def test_every_rule_names_a_known_category(self):
        for category, _ in offer_categories._RULES:
            self.assertIn(category, offer_categories.CATEGORIES)


class CategoriesInTheCopyTest(TempDataDirTest):
    def test_sync_stores_the_category_and_load_counts_them(self):
        data = self.sync(item("1", "Kipfilet"), item("2", "Gehakt"), item("3", "Bananen"), item("4", "Cola", categoryHint="x"))
        by_id = {o["id"]: o["category"] for o in data["offers"]}
        self.assertEqual(by_id, {"1": "vlees", "2": "vlees", "3": "groente_fruit", "4": "frisdrank"})
        self.assertEqual([(c["id"], c["count"]) for c in data["categories"]], [("vlees", 2), ("groente_fruit", 1), ("frisdrank", 1)])

    def test_a_copy_from_before_the_categories_gets_them_from_the_title(self):
        self.sync(item("1", "Kipfilet"))
        with auth.connect() as conn:
            conn.execute("UPDATE offers SET category = NULL")
        self.assertEqual(offers.load("ah")["offers"][0]["category"], "vlees")

    def test_an_old_database_without_the_column_is_upgraded(self):
        with auth.connect() as conn:
            conn.executescript("DROP TABLE IF EXISTS offers; CREATE TABLE offers (store TEXT NOT NULL, product_id TEXT NOT NULL, position INTEGER NOT NULL, title TEXT NOT NULL, brand TEXT, unit_size TEXT, price REAL, price_before REAL, promo_label TEXT, image TEXT, url TEXT, valid_until TEXT, PRIMARY KEY (store, product_id));")
        self.assertEqual(self.sync(item("1", "Kipfilet"))["total"], 1)


class HistoryTest(TempDataDirTest):
    def test_a_product_that_stays_on_offer_is_one_period_with_a_sync_count(self):
        self.sync(item("1", "Kipfilet"))
        self.sync(item("1", "Kipfilet"))
        self.sync(item("1", "Kipfilet"))
        product = offers.history("ah")["products"][0]
        self.assertEqual((product["timesOnOffer"], len(product["episodes"]), product["episodes"][0]["syncs"], product["onOfferNow"]), (1, 1, 3, True))
        self.assertEqual((product["episodes"][0]["from"], product["lastSeen"]), ("2026-10-01T05:00:00+00:00", "2026-10-03T05:00:00+00:00"))

    def test_coming_back_after_a_gap_is_a_new_time(self):
        self.sync(item("1", "Kipfilet"))
        self.sync()
        self.sync(item("1", "Kipfilet"))
        product = offers.history("ah")["products"][0]
        self.assertEqual((product["timesOnOffer"], len(product["episodes"]), product["onOfferNow"]), (2, 2, True))
        first = product["episodes"][0]
        self.assertEqual((first["from"], first["to"]), ("2026-10-01T05:00:00+00:00", "2026-10-01T05:00:00+00:00"), "gesloten op het laatste moment dat hij er stond")

    def test_a_price_change_keeps_the_details_of_each_step_but_is_not_a_new_time(self):
        self.sync(item("1", "Kipfilet", 3.0, 4.0, "Bonus"))
        self.sync(item("1", "Kipfilet", 2.5, 4.0, "Bonus"))
        product = offers.history("ah")["products"][0]
        self.assertEqual((product["timesOnOffer"], [e["price"] for e in product["episodes"]], product["lowestPrice"]), (1, [3.0, 2.5], 2.5))

    def test_the_details_are_kept(self):
        self.sync(item("1", "Kipfilet", 3.49, 4.99, "2e halve prijs", brand="AH", unitSize="400 g", validUntil="2026-10-07", image="i.png", url="u"))
        product = offers.history("ah")["products"][0]
        episode = product["episodes"][0]
        self.assertEqual((product["title"], product["brand"], product["unitSize"], product["category"], product["image"], product["url"]),
                         ("Kipfilet", "AH", "400 g", "vlees", "i.png", "u"))
        self.assertEqual((episode["price"], episode["priceBefore"], episode["promoLabel"], episode["validUntil"]), (3.49, 4.99, "2e halve prijs", "2026-10-07"))

    def test_the_period_of_the_store_is_kept_and_shown_as_start_and_end(self):
        self.sync(item("1", "Kipfilet", 3.49, 4.99, "2e halve prijs", validFrom="2026-10-26", validUntil="2026-11-01"))
        product = offers.history("ah")["products"][0]
        episode = product["episodes"][0]
        self.assertEqual((episode["validFrom"], episode["start"], episode["end"], episode["open"]), ("2026-10-26", "2026-10-26", "2026-11-01", False))
        self.assertEqual((product["regularPrice"], product["highestPrice"], product["lowestPrice"]), (4.99, 3.49, 3.49))

    def test_without_the_dates_of_the_store_the_span_follows_the_syncs(self):
        self.sync(item("1", "Kipfilet"))
        self.sync(item("1", "Kipfilet"))
        self.sync()
        episode = offers.history("ah")["products"][0]["episodes"][0]
        self.assertEqual((episode["start"], episode["end"], episode["open"]), ("2026-10-01", "2026-10-02", False))

    def test_an_ongoing_offer_has_no_end_while_it_runs(self):
        self.sync(item("1", "Kipfilet", validFrom="2026-10-26", validUntil=None))
        episode = offers.history("ah")["products"][0]["episodes"][0]
        self.assertEqual((episode["start"], episode["end"], episode["open"]), ("2026-10-26", None, True))

    def test_the_same_deal_in_a_new_week_is_a_new_time(self):
        self.sync(item("1", "Kipfilet", validFrom="2026-10-12", validUntil="2026-10-18"))
        self.sync(item("1", "Kipfilet", validFrom="2026-10-19", validUntil="2026-10-25"))
        self.sync(item("1", "Kipfilet", validFrom="2026-10-19", validUntil="2026-10-25"))
        product = offers.history("ah")["products"][0]
        self.assertEqual((product["timesOnOffer"], [(e["start"], e["end"]) for e in product["episodes"]]),
                         (2, [("2026-10-12", "2026-10-18"), ("2026-10-19", "2026-10-25")]))
        self.assertEqual(product["episodes"][1]["syncs"], 2)

    def test_a_row_from_before_the_dates_gets_its_start_without_splitting(self):
        self.sync(item("1", "Kipfilet"))
        self.sync(item("1", "Kipfilet", validFrom="2026-10-01", validUntil="2026-10-07"))
        product = offers.history("ah")["products"][0]
        self.assertEqual((product["timesOnOffer"], len(product["episodes"]), product["episodes"][0]["validFrom"]), (1, 1, "2026-10-01"))

    def test_an_old_history_table_without_the_column_is_upgraded(self):
        with auth.connect() as conn:
            conn.executescript("DROP TABLE IF EXISTS offer_history; CREATE TABLE offer_history (id INTEGER PRIMARY KEY AUTOINCREMENT, store TEXT NOT NULL, product_id TEXT NOT NULL, title TEXT NOT NULL, brand TEXT, unit_size TEXT, category TEXT, price REAL, price_before REAL, promo_label TEXT, image TEXT, url TEXT, valid_until TEXT, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, ended_at TEXT, syncs INTEGER NOT NULL DEFAULT 1, continued INTEGER NOT NULL DEFAULT 0);")
        self.sync(item("1", "Kipfilet", validFrom="2026-10-26", validUntil="2026-11-01"))
        self.assertEqual(offers.history("ah")["products"][0]["episodes"][0]["start"], "2026-10-26")

    def test_a_failed_sync_leaves_the_history_alone(self):
        self.sync(item("1", "Kipfilet"))

        def boom():
            raise RuntimeError("stuk")

        with mock.patch.dict(offers.FETCHERS, {"ah": boom}), self.assertRaises(RuntimeError):
            offers.sync("ah")
        product = offers.history("ah")["products"][0]
        self.assertEqual((product["onOfferNow"], product["episodes"][0]["syncs"]), (True, 1))

    def test_history_is_kept_per_store_and_searchable(self):
        self.sync(item("1", "Kipfilet"), item("2", "Bananen"))
        self.sync(item("1", "Kipfilet"), store="jumbo")
        everything = offers.history()
        self.assertEqual((everything["total"], sorted(p["store"] for p in everything["products"])), (3, ["ah", "ah", "jumbo"]))
        self.assertEqual([p["title"] for p in offers.history("ah", "kip")["products"]], ["Kipfilet"])
        self.assertEqual(offers.history("ah", "50%")["total"], 0, "% is geen jokerteken")
        self.assertEqual(offers.history("ah", "a\\b")["total"], 0)
        with self.assertRaises(ValueError):
            offers.history("lidl")

    def test_duplicates_in_one_sync_count_once(self):
        self.sync(item("1", "Kipfilet"), item("1", "Kipfilet"))
        self.assertEqual(offers.history("ah")["products"][0]["episodes"][0]["syncs"], 1)

    def test_the_route(self):
        self.sync(item("1", "Kipfilet"))
        client = create_app().test_client()
        reply = client.get("/api/offers/history?store=ah&q=kip&limit=5").get_json()
        self.assertEqual((reply["total"], reply["products"][0]["timesOnOffer"]), (1, 1))
        self.assertEqual(client.get("/api/offers/history?store=lidl").status_code, 400)
        self.assertEqual(client.get("/api/offers/history?limit=abc").status_code, 200)


if __name__ == "__main__":
    unittest.main()
