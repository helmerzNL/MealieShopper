"""De regels van een aankoop verrijkt: korting, bonusregels van een bon, eerder gekocht, bespaard en status."""

import os
import tempfile
import unittest
from contextlib import closing
from unittest import mock

os.environ.setdefault("MEALIESHOPPER_DATA_DIR", tempfile.mkdtemp())

from mealieshopper import ah_orders, auth, jumbo_orders, purchase_lines


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
        self.assertEqual(
            purchase_lines.match_receipt_bonus(lines, discounts), {0: 3.97, 1: 0.4}
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
        totals = purchase_lines.enrich(None, "ah", "receipt", "2025-05-19", items)
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
        totals = purchase_lines.enrich(None, "ah", "order", "2026-09-21", items)
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
        totals = purchase_lines.enrich(None, "ah", "imported", "2025-03-14", items)
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
            purchase_lines.enrich(None, "ah", "order", "2026-09-21", items),
            {"saved": 0.0, "savedPercent": None},
        )


class StatusTest(unittest.TestCase):
    def test_the_status_of_each_kind_of_purchase(self):
        self.assertEqual(
            purchase_lines.status(
                "order", {"kind": "delivered", "state_text": "Bezorgd"}
            ),
            ("delivered", None),
        )
        self.assertEqual(
            purchase_lines.status("order", {"kind": "open", "state_text": "Ingepland"}),
            ("planned", "Ingepland"),
        )
        self.assertEqual(
            purchase_lines.status("order", {"kind": "open"}), ("planned", None)
        )  # Jumbo kent geen state_text
        self.assertEqual(purchase_lines.status("imported", {}), ("imported", None))
        self.assertEqual(purchase_lines.status("receipt", {}), ("receipt", None))


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
        auth.init_db()

    def test_orders_and_linked_receipt_lines_before_the_day_count_once_per_purchase(
        self,
    ):
        with closing(ah_orders._connect()) as conn, conn:
            line = {"productId": 1, "title": "AH Bananen", "quantity": 1, "price": 1.0}
            ah_orders._save_order(
                conn, {"id": 1, "deliveryDate": "2026-09-01"}, "delivered", [line, line]
            )  # twee regels, één bestelling
            ah_orders._save_order(
                conn, {"id": 2, "deliveryDate": "2026-09-21"}, "delivered", [line]
            )  # de dag zelf telt niet
            conn.execute(
                "INSERT INTO ah_receipts (receipt_id, date_time, total, details_synced, first_seen) VALUES ('R1', '2026-09-10T10:00:00', 1, 1, 'x')"
            )
            conn.execute(
                "INSERT INTO ah_receipt_items (receipt_id, position, product_id, name, quantity, price, amount) VALUES ('R1', 0, 555, 'BANANEN', 1, 1.0, 1.0)"
            )
            conn.execute(
                "INSERT INTO ah_receipt_products (receipt_product_id, product_id, title, checked_at) VALUES (555, 1, 'AH Bananen', 'x')"
            )
            found = purchase_lines.times_before(
                conn, "ah", ["1", None, "", 2], "2026-09-21"
            )
        self.assertEqual(found, {"1": 2})

    def test_a_store_without_tables_counts_nothing(self):
        with closing(auth.connect()) as conn:
            self.assertEqual(
                purchase_lines.times_before(conn, "picnic", ["1"], "2026-09-21"), {}
            )

    def test_jumbo_counts_its_own_orders_only(self):
        with closing(jumbo_orders._connect()) as conn, conn:
            jumbo_orders._save_order(
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
            )
            self.assertEqual(
                purchase_lines.times_before(conn, "jumbo", ["123STK"], "2026-09-21"),
                {"123STK": 1},
            )


class FillRegularTest(unittest.TestCase):
    def test_a_failing_fill_is_logged_and_never_raised(self):
        from mealieshopper import prices

        with (
            mock.patch.object(
                prices, "fill_regular_prices", side_effect=RuntimeError("kapot")
            ),
            self.assertLogs("mealieshopper.purchase_lines", "WARNING"),
        ):
            purchase_lines.fill_regular("ah", order_ids=[1])


if __name__ == "__main__":
    unittest.main()
