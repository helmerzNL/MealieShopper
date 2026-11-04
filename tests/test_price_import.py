"""Oude aankopen inlezen uit bevestigingsmails (#178): de JSON van een assistent, zonder dubbele bestellingen."""
import json
import os
import re
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

os.environ.setdefault("MEALIESHOPPER_DATA_DIR", tempfile.mkdtemp())

from mealieshopper import ah_orders, auth, i18n, jumbo_orders, price_import, prices  # noqa: E402
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


class OrdersTest(ImportCase):
    """De ingelezen bestellingen bij Bestellingen: in de lijst, te openen, te doorzoeken en een voor een te verwijderen."""

    def test_imported_orders_are_listed_newest_first_like_an_order(self):
        self.run_import(mail_order(), mail_order(number=None, day="2025-04-01", total=None))
        orders = price_import.list_orders("ah")["orders"]
        self.assertEqual([(o["ref"], o["number"], o["date"], o["total"], o["itemCount"], o["imported"]) for o in orders], [
            ("dag:2025-04-01", None, "2025-04-01", 42.38, 2, True),  # zonder totaal in de mail: wat je voor de producten betaalde
            ("225301", "225301", "2025-03-14", 141.53, 2, True),
        ])
        self.assertEqual((orders[1]["units"], orders[1]["importedAt"]), (18, NOW))
        self.assertEqual(price_import.list_orders("jumbo")["orders"], [])

    def test_an_imported_order_opens_with_its_products(self):
        self.run_import(mail_order())
        order = price_import.get_order("ah", "225301")
        self.assertEqual(order["deliveryDate"], "2025-03-14")
        self.assertEqual(order["items"], [
            {"title": "AH Bananen tros", "quantity": 2, "paid": 1.19, "price": 1.19, "amount": 2.38, "bonus": False},
            {"title": "AH Greenfields Beefburger naturel 2 stuks", "quantity": 16, "paid": 2.5, "price": 4.99, "amount": 40.0, "bonus": True},
        ])
        self.assertIsNone(price_import.get_order("ah", "999"))
        self.assertIsNone(price_import.get_order("jumbo", "225301"))

    def test_an_imported_order_that_later_comes_from_the_account_is_not_listed_twice(self):
        self.run_import(mail_order(number="225301", day="2026-09-01"), mail_order(number="777", day="2026-09-08"))
        self.ah_order(225301, "2026-09-01", {"productId": 4183, "title": "AH Bananen tros", "quantity": 2, "price": 1.19, "amount": 2.38})
        self.ah_order(888, "2026-09-08", {"productId": 4183, "title": "AH Bananen tros", "quantity": 1, "price": 1.19, "amount": 1.19})
        self.assertEqual(price_import.list_orders("ah")["orders"], [])
        self.assertIsNone(price_import.get_order("ah", "225301"))

    def test_one_imported_order_can_be_removed_on_its_own(self):
        self.run_import(mail_order(), mail_order(number="777", day="2025-05-01"))
        self.assertEqual(price_import.remove("ah", "225301"), {"store": "ah", "removed": 1, "ref": "225301"})
        self.assertEqual([o["ref"] for o in price_import.list_orders("ah")["orders"]], ["777"])
        self.assertEqual(price_import.remove("ah", "bestaat-niet")["removed"], 0)
        self.assertEqual(price_import.summary("ah")["orders"], 1)
        self.assertEqual(prices.history("ah", "beefburger", 50, today=TODAY)["products"][0]["timesBought"], 1, "ook weg uit de Prijsgeschiedenis")

    def test_the_purchase_search_finds_imported_lines(self):
        self.run_import(mail_order())
        self.ah_order(101, "2026-09-01", {"productId": 4183, "title": "AH Bananen tros", "quantity": 1, "price": 1.29, "amount": 1.29})
        found = ah_orders.search_purchases("bananen")
        self.assertEqual([(o["id"], o["date"], o.get("imported", False)) for o in found["occurrences"]],
                         [("101", "2026-09-01", False), ("225301", "2025-03-14", True)])
        self.assertEqual(found["products"][0]["times"], 2)
        self.run_import(mail_order(items=[{"name": "Jumbo Kipfilet", "quantity": 1, "price": 5.0}]), store="jumbo")
        jumbo = jumbo_orders.search_purchases("kipfilet")["occurrences"]
        self.assertEqual([(o["id"], o["amount"], o["imported"]) for o in jumbo], [("225301", 5.0, True)])
        self.assertEqual(jumbo_orders.search_purchases("bananen")["occurrences"], [])

    def test_the_purchase_search_skips_an_imported_order_the_account_also_has(self):
        self.run_import(mail_order(number="225301", day="2026-09-01"))
        self.ah_order(225301, "2026-09-01", {"productId": 4183, "title": "AH Bananen tros", "quantity": 2, "price": 1.19, "amount": 2.38})
        self.assertEqual([o["id"] for o in ah_orders.search_purchases("bananen")["occurrences"]], ["225301"])
        self.assertFalse(ah_orders.search_purchases("bananen")["occurrences"][0].get("imported"))


class RouteTest(ImportCase):
    def test_the_routes_for_imported_orders(self):
        client = create_app().test_client()
        self.run_import(mail_order(), mail_order(number="AB/12", day="2025-05-01"), mail_order(number=None, day="2025-06-01", total=9.5))
        listed = client.get("/api/imported-orders/ah").get_json()["orders"]
        self.assertEqual([o["ref"] for o in listed], ["dag:2025-06-01:9.50", "AB/12", "225301"])
        self.assertEqual(client.get("/api/imported-orders/ah/AB%2F12").get_json()["number"], "AB/12")
        self.assertEqual(len(client.get("/api/imported-orders/ah/dag:2025-06-01:9.50").get_json()["items"]), 2)
        self.assertEqual(client.get("/api/imported-orders/ah/999").status_code, 404)
        self.assertEqual(client.get("/api/imported-orders/lidl").status_code, 400)
        self.assertEqual(client.delete("/api/prices/import?store=ah&ref=AB%2F12").get_json(), {"store": "ah", "removed": 1, "ref": "AB/12"})
        self.assertEqual(len(client.get("/api/imported-orders/ah").get_json()["orders"]), 2)

    def test_everyone_sees_imported_orders_and_only_the_administrator_removes_them(self):
        self.assertFalse(auth.admin_only("/api/imported-orders/ah"))
        self.assertFalse(auth.admin_only("/api/imported-orders/ah/225301"))
        self.assertTrue(auth.admin_only("/api/prices/import"))


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


class OrdersPageTest(unittest.TestCase):
    """De knop staat ook bij Bestellingen van elke winkel, alleen voor de beheerder, en opent de dialoog voor die winkel."""

    ROOT = Path(__file__).resolve().parent.parent

    def test_every_orders_page_has_the_import_button(self):
        html = (self.ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        scripts = {name: (self.ROOT / "static" / name).read_text(encoding="utf-8") for name in ("orders.js", "picnic.js", "crisp.js")}
        for button, script, store in (("orders-import", "orders.js", "ordersStore()"), ("picnic-orders-import", "picnic.js", '"picnic"'),
                                      ("crisp-orders-import", "crisp.js", '"crisp"')):
            tag = re.search(rf'<button[^>]*id="{button}"[^>]*>', html)
            self.assertIsNotNone(tag, button)
            self.assertIn("data-admin-only", tag.group(0), button)
            self.assertIn(f'$("#{button}").addEventListener("click", () => openPriceImport({store}));', scripts[script])
            # Bij AH en Jumbo staan de ingelezen bestellingen als rij in de aankopentabel, bij Picnic en Crisp als kaart bij de afgelopen.
            listed = 'kind: "imported", id: String(order.ref)' if script == "orders.js" else "importedOrderCard(order"
            self.assertIn(listed, scripts[script], f"{script}: de ingelezen bestellingen staan tussen de afgelopen")


if __name__ == "__main__":
    unittest.main()
