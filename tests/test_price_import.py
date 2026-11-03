"""Oude aankopen inlezen uit bevestigingsmails (#178): de JSON van een assistent, zonder dubbele bestellingen."""
import json
import os
import tempfile
import unittest
from contextlib import closing
from unittest import mock

os.environ.setdefault("MEALIESHOPPER_DATA_DIR", tempfile.mkdtemp())

from mealieshopper import ah_orders, auth, i18n, price_import, prices  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402

NOW = "2026-10-20T08:00:00+00:00"
TODAY = "2026-10-20"


def mail_order(number="225301", day="2025-03-14", total=141.53, items=None):
    return {"orderNumber": number, "date": day, "total": total, "items": items if items is not None else [
        {"name": "AH Bananen tros", "quantity": 2, "price": 1.19, "total": 2.38, "discount": 0},
        {"name": "AH Greenfields Beefburger naturel 2 stuks", "quantity": 16, "price": 4.99, "total": 79.84, "discount": 39.92},
    ]}


class ImportCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"MEALIESHOPPER_DATA_DIR": tmp.name, "PASSKEY_AUTH_ENABLED": "false"})
        patcher.start()
        self.addCleanup(patcher.stop)
        auth.init_db()

    def run_import(self, *orders, dry_run=False, store="ah"):
        return price_import.run(json.dumps({"store": store, "orders": list(orders)}), store, dry_run=dry_run, now=NOW)

    def ah_order(self, order_id, day, *items):
        with closing(ah_orders._connect()) as conn, conn:
            ah_orders._save_order(conn, {"id": order_id, "deliveryDate": day}, "delivered", list(items))


class PromptTest(unittest.TestCase):
    def test_the_prompt_asks_for_the_date_and_the_order_number_so_nothing_counts_twice(self):
        with mock.patch.object(i18n, "current_lang", return_value="nl"):
            text = price_import.prompt("ah")
        for part in ('"date"', "JJJJ-MM-DD", '"orderNumber"', "bestelnummer", "dubbel", "Albert Heijn", '"store": "ah"', "laatste versie"):
            self.assertIn(part, text)
        with mock.patch.object(i18n, "current_lang", return_value="en"):
            english = price_import.prompt("jumbo")
        for part in ('"date"', "YYYY-MM-DD", "order number", "twice", "Jumbo", '"store": "jumbo"'):
            self.assertIn(part, english)

    def test_the_example_in_the_prompt_is_valid_input(self):
        text = price_import.prompt("ah")
        example = text[text.index("{"):text.rindex("}") + 1]
        store, orders, problems = price_import.parse(example, "ah")
        self.assertEqual((store, len(orders), problems), ("ah", 1, []))

    def test_an_unknown_store_is_refused(self):
        with self.assertRaises(ValueError):
            price_import.prompt("lidl")


class ParseTest(unittest.TestCase):
    def parse(self, data, store="ah"):
        return price_import.parse(data, store, today=__import__("datetime").date(2026, 10, 20))

    def test_paid_per_item_includes_the_discount_on_that_product(self):
        _, orders, _ = self.parse({"orders": [mail_order()]})
        burger = orders[0]["items"][1]
        self.assertEqual((burger["quantity"], burger["regular"], burger["paid"]), (16, 4.99, 2.5))  # (79.84 - 39.92) / 16 = 2.495
        self.assertEqual((orders[0]["ref"], orders[0]["date"], orders[0]["total"]), ("225301", "2025-03-14", 141.53))

    def test_the_json_of_an_assistant_is_read_leniently(self):
        text = "Hier is de JSON:\n```json\n" + json.dumps([{"orderNumber": None, "date": "2025-01-02", "total": "12,50",
                                                          "items": [{"name": " AH  Mozzarella ", "quantity": "2", "price": "0,86"}]}]) + "\n```"
        _, orders, problems = self.parse(text)
        self.assertEqual(problems, [])
        self.assertEqual(orders[0]["ref"], "dag:2025-01-02:12.50", "zonder bestelnummer: de dag en het totaal")
        self.assertEqual(orders[0]["items"][0], {"title": "AH Mozzarella", "quantity": 2.0, "paid": 0.86, "regular": 0.86})

    def test_an_order_without_a_usable_date_or_products_is_skipped_and_reported(self):
        _, orders, problems = self.parse({"orders": [
            mail_order(number="1", day=None), mail_order(number="2", day="14-03-2025"), mail_order(number="3", day="2027-01-01"),
            mail_order(number="4", items=[{"name": "Gratis tasje", "quantity": 1, "price": 0, "total": 0}]), "geen bestelling",
            mail_order(number="5"),
        ]})
        self.assertEqual([order["ref"] for order in orders], ["5"])
        self.assertEqual(len(problems), 5)
        self.assertIn("datum", problems[0])

    def test_wrong_input_is_refused_with_a_reason(self):
        for data in ("geen json", '{"orders": "nee"}', "42"):
            with self.assertRaises(ValueError):
                self.parse(data)
        with self.assertRaisesRegex(ValueError, "jumbo"):
            self.parse({"store": "jumbo", "orders": []})
        with self.assertRaises(ValueError):
            self.parse("x" * (price_import.MAX_TEXT_CHARS + 1))


class RunTest(ImportCase):
    def test_a_dry_run_shows_what_would_happen_and_stores_nothing(self):
        report = self.run_import(mail_order(), dry_run=True)
        self.assertEqual((report["new"], report["duplicates"], report["lines"], report["imported"]), (1, 0, 2, 0))
        self.assertEqual(price_import.summary("ah")["orders"], 0)

    def test_the_same_order_is_imported_once(self):
        first = self.run_import(mail_order(), mail_order())
        self.assertEqual((first["new"], first["duplicates"], first["imported"]), (1, 1, 2))
        self.assertEqual(first["orders"][1]["reason"], "staat twee keer in deze JSON")
        again = self.run_import(mail_order(), mail_order(number=None, day="2025-04-01", total=10.0))
        self.assertEqual([(o["new"], o["reason"]) for o in again["orders"]], [(False, "is al ingelezen"), (True, None)])
        same_day_no_number = self.run_import(mail_order(number=None, day="2025-04-01", total=10.0))
        self.assertEqual(same_day_no_number["new"], 0, "zonder nummer: zelfde dag en totaal is dezelfde bestelling")
        summary = price_import.summary("ah")
        self.assertEqual((summary["orders"], summary["lines"], summary["first"], summary["last"]), (2, 4, "2025-03-14", "2025-04-01"))

    def test_an_order_the_app_already_has_from_the_account_is_skipped(self):
        self.ah_order(225301, "2025-03-14", {"productId": 1, "title": "AH Bananen tros", "quantity": 2, "price": 1.19, "amount": 2.38})
        self.ah_order(999, "2025-05-01", {"productId": 1, "title": "AH Bananen tros", "quantity": 1, "price": 1.19, "amount": 1.19})
        report = self.run_import(mail_order(), mail_order(number="777", day="2025-05-01"), mail_order(number="778", day="2025-05-02"))
        self.assertEqual([o["reason"] for o in report["orders"]],
                         ["staat al in je bestellingen", "op deze dag staat al een bestelling in je account", None])

    def test_nothing_usable_is_an_error(self):
        with self.assertRaises(ValueError):
            self.run_import(mail_order(day=None))

    def test_remove_takes_away_the_imported_purchases_of_one_store(self):
        self.run_import(mail_order())
        self.run_import(mail_order(items=[{"name": "Jumbo Kipfilet", "quantity": 1, "price": 5.0}]), store="jumbo")
        self.assertEqual(price_import.remove("ah"), {"store": "ah", "removed": 1})
        self.assertEqual((price_import.summary("ah")["orders"], price_import.summary("jumbo")["orders"]), (0, 1))


class HistoryTest(ImportCase):
    def history(self, query=""):
        return prices.history("ah", query, 50, today=TODAY)

    def test_imported_lines_join_the_product_from_the_orders_by_name(self):
        self.ah_order(101, "2026-09-01", {"productId": 4183, "title": "AH Bananen tros", "quantity": 1, "price": 1.29, "amount": 1.29})
        self.run_import(mail_order(items=[{"name": "AH Bananen tros", "quantity": 2, "price": 1.19, "total": 2.38},
                                          {"name": "AH Ah zachte spinazie", "quantity": 1, "price": 1.29},
                                          {"name": "Iets uit de mail", "quantity": 1, "price": 2.0, "discount": 0.5}]))
        bananas = next(p for p in self.history("bananen")["products"] if p["productId"] == "4183")
        self.assertEqual([(p["date"], p["price"], p["source"]) for p in bananas["purchases"]],
                         [("2025-03-14", 1.19, "import"), ("2026-09-01", 1.29, "order")])
        self.assertEqual(bananas["timesBought"], 2)
        other = self.history("iets uit")["products"][0]
        self.assertEqual((other["productId"], other["purchases"][0]["price"]), ("import:iets uit de mail", 1.5))
        self.assertIn("import:ah zachte spinazie", [p["productId"] for p in self.history("spinazie")["products"]], "het dubbele merk valt weg")

    def test_an_imported_order_that_later_comes_from_the_account_counts_once(self):
        self.run_import(mail_order(number="225301", day="2026-09-01"))
        self.assertEqual(self.history("bananen")["products"][0]["timesBought"], 1)
        self.ah_order(225301, "2026-09-01", {"productId": 4183, "title": "AH Bananen tros", "quantity": 2, "price": 1.19, "amount": 2.38})
        products = self.history("bananen")["products"]
        self.assertEqual([(p["productId"], p["timesBought"]) for p in products], [("4183", 1)])

    def test_imported_purchases_count_as_bought(self):
        self.run_import(mail_order())
        self.assertIn("import:ah bananen tros", prices.bought_ids("ah", TODAY))
        self.assertEqual(prices.history("ah", "", 50, today=TODAY, bought=True)["total"], 2)


class RouteTest(ImportCase):
    def test_the_routes(self):
        client = create_app().test_client()
        summary = client.get("/api/prices/import?store=ah").get_json()
        self.assertEqual((summary["orders"], "date" in summary["prompt"]), (0, True))
        body = {"store": "ah", "data": json.dumps({"orders": [mail_order()]}), "dryRun": True}
        self.assertEqual(client.post("/api/prices/import", json=body).get_json()["new"], 1)
        self.assertEqual(price_import.summary("ah")["orders"], 0)
        body["dryRun"] = False
        self.assertEqual(client.post("/api/prices/import", json=body).get_json()["imported"], 2)
        self.assertEqual(client.post("/api/prices/import", json=body).get_json()["duplicates"], 1)
        self.assertEqual(client.post("/api/prices/import", json={"store": "ah", "data": "nee"}).status_code, 400)
        self.assertEqual(client.post("/api/prices/import", json={"store": "lidl", "data": "[]"}).status_code, 400)
        self.assertEqual(client.delete("/api/prices/import?store=ah").get_json()["removed"], 1)

    def test_only_the_administrator_may_import(self):
        self.assertTrue(auth.admin_only("/api/prices/import"))
        self.assertFalse(auth.admin_only("/api/prices/history"))


if __name__ == "__main__":
    unittest.main()
