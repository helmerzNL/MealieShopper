"""De regels van een aankoop verrijkt: korting, bonusregels van een bon, eerder gekocht, bespaard en status."""

import os
import tempfile
import unittest
from contextlib import closing
from unittest import mock

os.environ.setdefault("MEALIESHOPPER_DATA_DIR", tempfile.mkdtemp())

from helpers import db, in_db, run

from mealieshopper import ah_orders, auth, jumbo_orders, product_links, purchase_lines


class DiscountTest(unittest.TestCase):
    def test_the_percentage_is_rounded_and_capped(self):
        self.assertEqual(purchase_lines.discount_percent(1.19, 0.89), 25)
        self.assertEqual(purchase_lines.discount_percent(1.00, 0.995), None)
        self.assertEqual(purchase_lines.discount_percent(1.00, -0.5), 100)
        self.assertIsNone(purchase_lines.discount_percent(None, 0.89))
        self.assertIsNone(purchase_lines.discount_percent(0, 0.89))

    def test_a_higher_price_is_no_discount(self):
        self.assertIsNone(purchase_lines.discount_percent(1.00, 1.20))
        self.assertIsNone(purchase_lines.discount_percent(1.00, None))


class ReceiptBonusTest(unittest.TestCase):
    def test_a_bonus_line_matches_a_line_on_a_folded_prefix(self):
        lines = [
            {"name": "AH ROOMKAAS", "quantity": 4, "price": 0.99, "amount": 3.96},
            {"name": "KOMKOMMER", "quantity": 4, "price": 0.85, "amount": 3.4},
        ]
        discounts = [
            {"name": "AH ROOMKAAS", "amount": -3.97},
            {"name": "AHKOMKOMMERP", "amount": -0.4},
            {"name": "PERLAHUISBLE", "amount": -17.97},
        ]
        self.assertEqual(purchase_lines.match_receipt_bonus(lines, discounts), {0: 3.96, 1: 0.4})

    def test_the_house_brand_in_front_is_left_out_on_both_sides(self):
        # De bonusregel heeft "AH" vooraan en is afgekort, de regel niet: zonder "ah" is "komkommer" een voorvoegsel van "komkommerp".
        lines = [
            {"name": "KOMKOMMER", "quantity": 1, "price": 0.85},
            {"name": "AH PENNE", "quantity": 1, "price": 1.19},
        ]
        discounts = [
            {"name": "AHKOMKOMMERP", "amount": -0.4},
            {"name": "PENNE", "amount": -0.3},
        ]
        self.assertEqual(purchase_lines.match_receipt_bonus(lines, discounts), {0: 0.4, 1: 0.3})

    def test_a_key_under_three_characters_never_matches(self):
        # "AH" wordt "ah" of "", "EI" blijft "ei": te kort, anders paste de bonusregel op elke regel die zo begint.
        lines = [
            {"name": "AH MELK", "quantity": 1, "price": 1.0},
            {"name": "EIEREN", "quantity": 1, "price": 2.0},
        ]
        discounts = [{"name": "AH", "amount": -0.5}, {"name": "EI", "amount": -0.2}]
        self.assertEqual(purchase_lines.match_receipt_bonus(lines, discounts), {})
        self.assertEqual(
            purchase_lines.match_receipt_bonus([{"name": "EI"}], [{"name": "EIEREN", "amount": -0.2}]),
            {},
        )

    def test_a_bonus_line_counts_once_at_the_first_matching_line(self):
        lines = [
            {"name": "KOMKOMMER", "quantity": 1, "price": 0.85},
            {"name": "KOMKOMMER", "quantity": 1, "price": 0.85},
        ]
        discounts = [
            {"name": "KOMKOMMER", "amount": -0.4},
            {"name": "Bonus", "amount": -1.0},
            "kapot",
            {"name": "KOMK", "amount": "x"},
        ]
        self.assertEqual(purchase_lines.match_receipt_bonus(lines, discounts), {0: 0.4})

    def test_a_bonus_larger_than_the_line_caps_at_the_line(self):
        lines = [{"name": "FOCACCIA", "quantity": 1, "price": 2.59, "amount": 2.59}]
        items = [{**lines[0], "bonus": 4.36}]
        totals = run(purchase_lines.enrich(None, "ah", "receipt", "2025-05-19", items))
        self.assertEqual(
            (
                items[0]["paidEach"],
                items[0]["discount"],
                items[0]["discountSource"],
                items[0]["regularPrice"],
            ),
            (0.0, 100, "store", 2.59),
        )
        self.assertEqual(items[0]["timesBefore"], 0)  # zonder database: niets eerder
        self.assertEqual((totals["saved"], totals["savedPercent"]), (2.59, 100))

    def test_a_bonus_line_larger_than_its_line_keeps_the_excess_for_the_total(self):
        # 3.97 bonus op een regel van 4 x 0.99 = 3.96: de regel krijgt 3.96, de cent erboven telt alleen in het totaal.
        lines = [
            {"name": "AH ROOMKAAS", "quantity": 4, "price": 0.99, "amount": 3.96},
            {"name": "FOCACCIA", "quantity": 1, "price": None, "amount": 2.59},
            {"name": "MELK", "quantity": 0, "price": 1.0, "amount": 0.0},
        ]
        discounts = [
            {"name": "AH ROOMKAAS", "amount": -3.97},
            {"name": "FOCACCIA", "amount": -4.36},
            {"name": "MELK", "amount": -0.5},
            {"name": "PERLAHUISBLE", "amount": -17.97},
        ]
        self.assertEqual(purchase_lines.match_receipt_bonus(lines, discounts), {0: 3.96, 1: 2.59, 2: 0.0})
        # 0.01 + 1.77 + 0.50 + 17.97: de bonus boven de regels en de bonusregel die nergens past.
        self.assertEqual(purchase_lines.unmatched_bonus(lines, discounts), 20.25)

    def test_a_receipt_line_of_one_without_a_price_uses_its_amount(self):
        # AH zet bij één stuk geen prijs op de bon, alleen het bedrag. Zonder die terugval was er niets betaald en 100% bespaard.
        items = [
            {"name": "RED VELVET", "quantity": 1, "price": None, "amount": 7.99, "bonus": 2.8},
            {"name": "MELK", "quantity": 2, "price": None, "amount": 2.0, "bonus": 0.0},
        ]
        totals = run(purchase_lines.enrich(None, "ah", "receipt", "2026-10-30", items))
        self.assertEqual(
            (items[0]["paidEach"], items[0]["regularPrice"], items[0]["discountSource"], items[0]["discount"]), (5.19, 7.99, "store", 35)
        )
        self.assertEqual((items[1]["paidEach"], items[1]["regularPrice"], items[1]["discount"]), (1.0, None, None))
        # 2.80 bespaard. Betaald 5.19 + 2.00 = 7.19, zonder korting 9.99: 28%.
        self.assertEqual((totals["saved"], totals["savedPercent"]), (2.8, 28))

    def test_a_bonus_line_that_matches_no_line_counts_only_in_the_total(self):
        lines = [{"name": "BROOD", "quantity": 1, "price": 2.0, "amount": 2.0}, {"name": "WASMIDDEL", "quantity": 2, "price": 19.0, "amount": 38.0}]
        discounts = [{"name": "PERLAHUISBLE", "amount": -17.97}, {"name": "BROOD", "amount": -0.5}]
        self.assertEqual(purchase_lines.unmatched_bonus(lines, discounts), 17.97)
        items = [{**lines[0], "bonus": 0.5}, {**lines[1], "bonus": 0.0}]
        totals = run(purchase_lines.enrich(None, "ah", "receipt", "2025-05-19", items, unmatched_bonus=17.97))
        self.assertEqual([item["paidEach"] for item in items], [1.5, 19.0])  # de regels zelf krijgen er niets van
        # 0.50 + 17.97 = 18.47 bespaard. Betaald: 1.50 + 38.00 - 17.97 = 21.53, zonder korting 40.00: 46%.
        self.assertEqual((totals["saved"], totals["savedPercent"]), (18.47, 46))


class EnrichTest(unittest.TestCase):
    def test_the_column_wins_over_the_price_before_the_bonus_of_the_store(self):
        items = [
            {
                "productId": 1,
                "title": "AH Bananen",
                "quantity": 2,
                "price": 0.89,
                "priceBeforeBonus": 1.39,
                "regularPrice": 1.19,
                "taxonomy": "Fruit, verse sappen",
            },
            {
                "productId": 2,
                "title": "AH Halfvolle melk",
                "quantity": 1,
                "price": 1.0,
                "priceBeforeBonus": 1.25,
                "regularPrice": None,
                "taxonomy": "",
            },
            {
                "productId": 3,
                "title": "Iets onbekends",
                "quantity": 1,
                "price": 2.0,
                "priceBeforeBonus": None,
                "taxonomy": None,
            },
        ]
        totals = run(purchase_lines.enrich(None, "ah", "order", "2026-09-21", items))
        self.assertEqual(
            [
                (
                    i["category"],
                    i["regularPrice"],
                    i["discount"],
                    i["discountSource"],
                    i["paidEach"],
                )
                for i in items
            ],
            [
                ("groente_fruit", 1.19, 25, "history", 0.89),
                ("zuivel_eieren", 1.25, 20, "store", 1.0),
                ("overig", None, None, None, 2.0),
            ],
        )
        # (1.19 - 0.89) * 2 + (1.25 - 1.0) = 0.85 bespaard op 0.85 + 4.78 betaald: 15%
        self.assertEqual((totals["saved"], totals["savedPercent"]), (0.85, 15))

    def test_an_imported_line_takes_its_regular_price_from_the_mail(self):
        items = [
            {
                "title": "AH Greenfields Beefburger",
                "quantity": 16,
                "paid": 2.5,
                "price": 4.99,
                "productId": None,
            }
        ]
        totals = run(purchase_lines.enrich(None, "ah", "imported", "2025-03-14", items))
        self.assertEqual(
            (
                items[0]["regularPrice"],
                items[0]["discount"],
                items[0]["discountSource"],
                items[0]["paidEach"],
            ),
            (4.99, 50, "store", 2.5),
        )
        self.assertEqual((totals["saved"], totals["savedPercent"]), (39.84, 50))

    def test_nothing_saved_means_no_percentage(self):
        items = [
            {
                "title": "Pindakaas",
                "quantity": 1,
                "price": 1.99,
                "priceBeforeBonus": 1.99,
            }
        ]
        self.assertEqual(
            run(purchase_lines.enrich(None, "ah", "order", "2026-09-21", items)),
            {"saved": 0.0, "savedPercent": None},
        )


class StatusTest(unittest.TestCase):
    def test_the_status_of_each_kind_of_purchase(self):
        self.assertEqual(
            purchase_lines.status_kind("order", {"kind": "delivered", "state_text": "Bezorgd"}),
            ("delivered", None),
        )
        self.assertEqual(
            purchase_lines.status_kind("order", {"kind": "open", "state_text": "Ingepland"}),
            ("planned", "Ingepland"),
        )
        self.assertEqual(purchase_lines.status_kind("order", {"kind": "open"}), ("planned", None))  # Jumbo kent geen state_text
        self.assertEqual(purchase_lines.status_kind("imported", {}), ("imported", None))
        self.assertEqual(purchase_lines.status_kind("receipt", {}), ("receipt", None))


class TimesBeforeTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(
            os.environ,
            {"MEALIESHOPPER_DATA_DIR": tmp.name, "PASSKEY_AUTH_ENABLED": "false"},
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        run(auth.init_db())

    def test_orders_and_linked_receipt_lines_before_the_day_count_once_per_purchase(
        self,
    ):
        line = {"productId": 1, "title": "AH Bananen", "quantity": 1, "price": 1.0}
        run(
            in_db(
                ah_orders._connect,
                lambda conn: ah_orders._save_order(
                    conn, {"id": 1, "deliveryDate": "2026-09-01"}, "delivered", [line, line]
                ),  # twee regels, één bestelling
                lambda conn: ah_orders._save_order(conn, {"id": 2, "deliveryDate": "2026-09-21"}, "delivered", [line]),  # de dag zelf telt niet
            )
        )
        with closing(db(ah_orders)) as conn, conn:
            conn.execute(
                "INSERT INTO ah_receipts (receipt_id, date_time, total, details_synced, first_seen) VALUES ('R1', '2026-09-10T10:00:00', 1, 1, 'x')"
            )
            conn.execute(
                "INSERT INTO ah_receipt_items (receipt_id, position, product_id, name, quantity, price, amount) VALUES ('R1', 0, 555, 'BANANEN', 1, 1.0, 1.0)"
            )
            conn.execute("INSERT INTO ah_receipt_products (receipt_product_id, product_id, title, checked_at) VALUES (555, 1, 'AH Bananen', 'x')")
        found = run(
            in_db(
                ah_orders._connect,
                lambda conn: purchase_lines.times_before(conn, "ah", ["1", None, "", 2], "2026-09-21"),
            )
        )[0]
        self.assertEqual(found, {"1": 2})

    def test_a_receipt_line_you_linked_yourself_counts_as_an_earlier_purchase(self):
        # Geen automatische koppeling voor 555, wel je eigen (#238): de bon telt bij product 1. De naamkoppeling van "KWARK" telt bij 2.
        with closing(db(ah_orders)) as conn, conn:
            conn.execute(
                "INSERT INTO ah_receipts (receipt_id, date_time, total, details_synced, first_seen) VALUES ('R1', '2026-09-10T10:00:00', 1, 1, 'x')"
            )
            conn.executemany(
                "INSERT INTO ah_receipt_items (receipt_id, position, product_id, name, quantity, price, amount) VALUES ('R1', ?, ?, ?, 1, 1.0, 1.0)",
                [(0, 555, "BANANEN"), (1, None, "KWARK")],
            )
        run(product_links.save("ah", "BANANEN", {"productId": "1", "title": "AH Bananen"}, article=555))
        run(product_links.save("ah", "KWARK", {"productId": "2", "title": "AH Kwark"}))
        # Op een kale verbinding zonder `ms_fold`: `times_before` zet die (en de tabel van `product_links`) er zelf bij.
        found = run(in_db(auth.connect, lambda conn: purchase_lines.times_before(conn, "ah", ["1", "2", "3"], "2026-09-21")))[0]
        self.assertEqual(found, {"1": 1, "2": 1})

    def test_a_store_without_tables_counts_nothing(self):
        self.assertEqual(run(in_db(auth.connect, lambda conn: purchase_lines.times_before(conn, "picnic", ["1"], "2026-09-21")))[0], {})

    def test_jumbo_counts_its_own_orders_only(self):
        run(
            in_db(
                jumbo_orders._connect,
                lambda conn: jumbo_orders._save_order(
                    conn,
                    {"id": "J1", "deliveryDate": "2026-09-01"},
                    [
                        {
                            "productId": "123STK",
                            "title": "Jumbo Kwark",
                            "quantity": 1,
                            "price": 1.0,
                        }
                    ],
                ),
            )
        )
        self.assertEqual(
            run(in_db(jumbo_orders._connect, lambda conn: purchase_lines.times_before(conn, "jumbo", ["123STK"], "2026-09-21")))[0],
            {"123STK": 1},
        )


class FillRegularTest(unittest.TestCase):
    def test_a_failing_fill_is_logged_and_never_raised(self):
        from mealieshopper import prices

        with (
            mock.patch.object(
                prices,
                "fill_regular_prices",
                new_callable=mock.AsyncMock,
                side_effect=RuntimeError("kapot"),
            ),
            self.assertLogs("mealieshopper.purchase_lines", "WARNING"),
        ):
            run(purchase_lines.fill_regular("ah", order_ids=[1]))


if __name__ == "__main__":
    unittest.main()
