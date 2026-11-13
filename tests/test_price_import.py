"""Oude aankopen inlezen uit bevestigingsmails (#178): de JSON van een assistent, zonder dubbele bestellingen."""

import json
import os
import re
import tempfile
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path
from unittest import mock

os.environ.setdefault("MEALIESHOPPER_DATA_DIR", tempfile.mkdtemp())

from helpers import AppTestCase, db, in_db, run  # noqa: E402

from mealieshopper import ah_orders, auth, i18n, jumbo_orders, offers, price_import, prices, product_images  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402

NOW = "2026-10-20T08:00:00+00:00"
TODAY = "2026-10-20"


def mail_order(number="225301", day="2025-03-14", total=141.53, items=None):
    return {
        "orderNumber": number,
        "date": day,
        "total": total,
        "items": items
        if items is not None
        else [
            {"name": "AH Bananen tros", "quantity": 2, "price": 1.19, "total": 2.38, "discount": 0},
            {"name": "AH Greenfields Beefburger naturel 2 stuks", "quantity": 16, "price": 4.99, "total": 79.84, "discount": 39.92},
        ],
    }


class ImportCase(AppTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"MEALIESHOPPER_DATA_DIR": tmp.name, "PASSKEY_AUTH_ENABLED": "false"})
        patcher.start()
        self.addCleanup(patcher.stop)
        run(auth.init_db())

    async def run_import(self, *orders, dry_run=False, store="ah"):
        return await price_import.run(json.dumps({"store": store, "orders": list(orders)}), store, dry_run=dry_run, now=NOW)

    async def ah_order(self, order_id, day, *items):
        await in_db(ah_orders._connect, lambda conn: ah_orders._save_order(conn, {"id": order_id, "deliveryDate": day}, "delivered", list(items)))

    async def jumbo_order(self, order_id, day, items=None, total=128.84):
        """Een Jumbo-bestelling uit je account. `items=None`: alleen de kop, zoals bij een oude bestelling waarvan Jumbo de details
        weigert (*producten nog niet opgehaald*)."""
        await in_db(jumbo_orders._connect, lambda conn: jumbo_orders._save_order(conn, {"id": order_id, "deliveryDate": day, "total": total}, items))


def jumbo_mail_order(number="6005201234", day="2022-01-12"):
    return mail_order(
        number=number,
        day=day,
        total=128.84,
        items=[
            {"name": "Jumbo Kipfilet 500g", "quantity": 2, "price": 5.49, "total": 10.98},
            {"name": "Jumbo Halfvolle Melk 1L", "quantity": 3, "price": 1.15, "total": 3.45},
        ],
    )


KIPFILET = {"productId": "123KIP", "title": "Jumbo Kipfilet 500g", "quantity": 2, "price": 5.49, "amount": 10.98}


class PromptTest(AppTestCase):
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
        example = text[text.index("{") : text.rindex("}") + 1]
        store, orders, problems = price_import.parse(example, "ah")
        self.assertEqual((store, len(orders), problems), ("ah", 1, []))
        self.assertEqual((orders[0]["status"], orders[0]["slotStart"], orders[0]["slotEnd"], orders[0]["notes"]), ("delivered", "18:00", "20:00", []))

    def test_the_prompt_asks_for_the_status_and_the_delivery_time(self):
        # #229: zo staat een ingelezen bestelling er net zo bij als een bestelling uit je account.
        with mock.patch.object(i18n, "current_lang", return_value="nl"):
            text = price_import.prompt("picnic")
        for part in ('"status"', '"delivered"', '"cancelled"', "geannuleerd", '"deliveryWindow"', "UU:MM", '"end" dan null'):
            self.assertIn(part, text)
        with mock.patch.object(i18n, "current_lang", return_value="en"):
            english = price_import.prompt("crisp")
        for part in ('"status"', '"delivered"', '"cancelled"', "cancelled", '"deliveryWindow"', "HH:MM"):
            self.assertIn(part, english)

    def test_an_unknown_store_is_refused(self):
        with self.assertRaises(ValueError):
            price_import.prompt("nope")


class ParseTest(AppTestCase):
    def parse(self, data, store="ah"):
        return price_import.parse(data, store, today=__import__("datetime").date(2026, 10, 20))

    def test_paid_per_item_includes_the_discount_on_that_product(self):
        _, orders, _ = self.parse({"orders": [mail_order()]})
        burger = orders[0]["items"][1]
        self.assertEqual((burger["quantity"], burger["regular"], burger["paid"]), (16, 4.99, 2.5))  # (79.84 - 39.92) / 16 = 2.495
        self.assertEqual((orders[0]["ref"], orders[0]["date"], orders[0]["total"]), ("225301", "2025-03-14", 141.53))

    def test_the_json_of_an_assistant_is_read_leniently(self):
        text = (
            "Hier is de JSON:\n```json\n"
            + json.dumps(
                [
                    {
                        "orderNumber": None,
                        "date": "2025-01-02",
                        "total": "12,50",
                        "items": [{"name": " AH  Mozzarella ", "quantity": "2", "price": "0,86"}],
                    }
                ]
            )
            + "\n```"
        )
        _, orders, problems = self.parse(text)
        self.assertEqual(problems, [])
        self.assertEqual(orders[0]["ref"], "dag:2025-01-02:12.50", "zonder bestelnummer: de dag en het totaal")
        self.assertEqual(orders[0]["items"][0], {"title": "AH Mozzarella", "quantity": 2.0, "paid": 0.86, "regular": 0.86})

    def test_an_order_without_a_usable_date_or_products_is_skipped_and_reported(self):
        _, orders, problems = self.parse(
            {
                "orders": [
                    mail_order(number="1", day=None),
                    mail_order(number="2", day="14-03-2025"),
                    mail_order(number="3", day="2027-01-01"),
                    mail_order(number="4", items=[{"name": "Gratis tasje", "quantity": 1, "price": 0, "total": 0}]),
                    "geen bestelling",
                    mail_order(number="5"),
                ]
            }
        )
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


class StatusAndWindowTest(AppTestCase):
    """#229: de status en het bezorgmoment uit de mail, ruim gelezen; een geannuleerde bestelling wordt niet ingelezen."""

    def parse(self, **fields):
        _, orders, problems = price_import.parse({"orders": [{**mail_order(), **fields}]}, "ah", date(2026, 10, 20))
        return orders, problems

    def test_the_status_is_read_in_the_words_an_assistant_uses(self):
        for given, expected in (
            ("delivered", "delivered"),
            ("Bezorgd", "delivered"),
            ("afgehaald", "delivered"),
            ("collected", "delivered"),
            ("COMPLETED", "delivered"),
            (None, None),
            ("null", None),
        ):
            with self.subTest(given=given):
                orders, problems = self.parse(status=given)
                self.assertEqual((orders[0]["status"], orders[0]["notes"], problems), (expected, [], []))

    def test_an_unknown_status_is_left_empty_with_a_note(self):
        orders, problems = self.parse(status="onderweg")
        self.assertEqual((orders[0]["status"], problems), (None, []))
        self.assertIn("onderweg", orders[0]["notes"][0])

    def test_a_cancelled_order_is_not_imported(self):
        for given in ("cancelled", "Geannuleerd", "canceled"):
            with self.subTest(given=given):
                orders, problems = self.parse(status=given)
                self.assertEqual(orders, [])
                self.assertIn("2025-03-14", problems[0])

    def test_the_delivery_window_is_read_leniently(self):
        for given, expected in (
            ({"start": "18:00", "end": "20:00"}, ("18:00", "20:00")),
            ({"start": "8.30", "end": "9.00"}, ("08:30", "09:00")),
            ("18:00 - 20:00", ("18:00", "20:00")),
            ({"start": "17:20", "end": None}, ("17:20", None)),
            ({"start": None, "end": "21:00"}, ("21:00", None)),
            ({"start": "2025-03-14T18:00:00", "end": "2025-03-14T20:00:00"}, ("18:00", "20:00")),
            (None, (None, None)),
        ):
            with self.subTest(given=given):
                orders, problems = self.parse(deliveryWindow=given)
                self.assertEqual(((orders[0]["slotStart"], orders[0]["slotEnd"]), orders[0]["notes"], problems), (expected, [], []))

    def test_an_unreadable_window_is_left_empty_with_a_note(self):
        for given in ("'s avonds", {"start": "20:00", "end": "18:00"}, {"start": "25:00", "end": "26:00"}):
            with self.subTest(given=given):
                orders, problems = self.parse(deliveryWindow=given)
                self.assertEqual((orders[0]["slotStart"], orders[0]["slotEnd"], problems), (None, None, []))
                self.assertEqual(len(orders[0]["notes"]), 1)


class RunTest(ImportCase):
    async def test_the_check_shows_the_status_and_the_delivery_time(self):
        report = await self.run_import(
            {**mail_order(), "status": "delivered", "deliveryWindow": {"start": "18:00", "end": "20:00"}},
            {**mail_order(number="777", day="2025-04-01"), "status": "cancelled"},
            dry_run=True,
        )
        self.assertEqual(
            [(o["ref"], o["status"], o["slotStart"], o["slotEnd"], o["notes"]) for o in report["orders"]],
            [("225301", "delivered", "18:00", "20:00", [])],
        )
        self.assertEqual(report["problemCount"], 1, "de geannuleerde bestelling")

    async def test_a_dry_run_shows_what_would_happen_and_stores_nothing(self):
        report = await self.run_import(mail_order(), dry_run=True)
        self.assertEqual((report["new"], report["duplicates"], report["lines"], report["imported"]), (1, 0, 2, 0))
        self.assertEqual((await price_import.summary("ah"))["orders"], 0)

    async def test_the_same_order_is_imported_once(self):
        first = await self.run_import(mail_order(), mail_order())
        self.assertEqual((first["new"], first["duplicates"], first["imported"]), (1, 1, 2))
        self.assertEqual(first["orders"][1]["reason"], "staat twee keer in deze JSON")
        again = await self.run_import(mail_order(), mail_order(number=None, day="2025-04-01", total=10.0))
        self.assertEqual([(o["new"], o["reason"]) for o in again["orders"]], [(False, "is al ingelezen"), (True, None)])
        same_day_no_number = await self.run_import(mail_order(number=None, day="2025-04-01", total=10.0))
        self.assertEqual(same_day_no_number["new"], 0, "zonder nummer: zelfde dag en totaal is dezelfde bestelling")
        summary = await price_import.summary("ah")
        self.assertEqual((summary["orders"], summary["lines"], summary["first"], summary["last"]), (2, 4, "2025-03-14", "2025-04-01"))

    async def test_an_order_the_app_already_has_from_the_account_is_skipped(self):
        await self.ah_order(225301, "2025-03-14", {"productId": 1, "title": "AH Bananen tros", "quantity": 2, "price": 1.19, "amount": 2.38})
        await self.ah_order(999, "2025-05-01", {"productId": 1, "title": "AH Bananen tros", "quantity": 1, "price": 1.19, "amount": 1.19})
        report = await self.run_import(mail_order(), mail_order(number="777", day="2025-05-01"), mail_order(number="778", day="2025-05-02"))
        self.assertEqual(
            [o["reason"] for o in report["orders"]], ["staat al in je bestellingen", "op deze dag staat al een bestelling in je account", None]
        )

    async def test_an_account_order_without_products_does_not_block_the_import(self):
        # #222: oude Jumbo-bestellingen staan alleen als kop in je account (Jumbo weigert de details). Op nummer en op datum.
        await self.jumbo_order("6005201234", "2022-01-12")
        await self.jumbo_order("6005209999", "2022-01-05", items=[])  # wel opgehaald, maar zonder één regel
        report = await self.run_import(jumbo_mail_order(), jumbo_mail_order(number=None, day="2022-01-05"), store="jumbo", dry_run=True)
        self.assertEqual([(o["new"], o["reason"]) for o in report["orders"]], [(True, None), (True, None)])
        self.assertEqual((report["new"], report["duplicates"], report["lines"]), (2, 0, 4))
        self.assertEqual((await self.run_import(jumbo_mail_order(), store="jumbo"))["imported"], 2)

    async def test_an_account_order_with_products_still_blocks_the_import(self):
        await self.jumbo_order("6005201234", "2022-01-12", items=[KIPFILET])
        await self.jumbo_order("6005205555", "2022-01-19", items=[KIPFILET])
        report = await self.run_import(jumbo_mail_order(), jumbo_mail_order(number="777", day="2022-01-19"), store="jumbo")
        self.assertEqual(
            [o["reason"] for o in report["orders"]], ["staat al in je bestellingen", "op deze dag staat al een bestelling in je account"]
        )
        self.assertEqual(report["imported"], 0)

    async def test_nothing_usable_is_an_error(self):
        with self.assertRaises(ValueError):
            await self.run_import(mail_order(day=None))

    async def test_remove_takes_away_the_imported_purchases_of_one_store(self):
        await self.run_import(mail_order())
        await self.run_import(mail_order(items=[{"name": "Jumbo Kipfilet", "quantity": 1, "price": 5.0}]), store="jumbo")
        self.assertEqual(await price_import.remove("ah"), {"store": "ah", "removed": 1})
        self.assertEqual(((await price_import.summary("ah"))["orders"], (await price_import.summary("jumbo"))["orders"]), (0, 1))


class HistoryTest(ImportCase):
    async def history(self, query=""):
        return await prices.history("ah", query, 50, today=TODAY)

    async def test_imported_lines_join_the_product_from_the_orders_by_name(self):
        await self.ah_order(101, "2026-09-01", {"productId": 4183, "title": "AH Bananen tros", "quantity": 1, "price": 1.29, "amount": 1.29})
        await self.run_import(
            mail_order(
                items=[
                    {"name": "AH Bananen tros", "quantity": 2, "price": 1.19, "total": 2.38},
                    {"name": "AH Ah zachte spinazie", "quantity": 1, "price": 1.29},
                    {"name": "Iets uit de mail", "quantity": 1, "price": 2.0, "discount": 0.5},
                ]
            )
        )
        bananas = next(p for p in (await self.history("bananen"))["products"] if p["productId"] == "4183")
        self.assertEqual(
            [(p["date"], p["price"], p["source"]) for p in bananas["purchases"]], [("2025-03-14", 1.19, "import"), ("2026-09-01", 1.29, "order")]
        )
        self.assertEqual(bananas["timesBought"], 2)
        other = (await self.history("iets uit"))["products"][0]
        self.assertEqual((other["productId"], other["purchases"][0]["price"]), ("import:iets uit de mail", 1.5))
        self.assertIn(
            "import:ah zachte spinazie", [p["productId"] for p in (await self.history("spinazie"))["products"]], "het dubbele merk valt weg"
        )

    async def test_an_imported_order_that_later_comes_from_the_account_counts_once(self):
        await self.run_import(mail_order(number="225301", day="2026-09-01"))
        self.assertEqual((await self.history("bananen"))["products"][0]["timesBought"], 1)
        await self.ah_order(225301, "2026-09-01", {"productId": 4183, "title": "AH Bananen tros", "quantity": 2, "price": 1.19, "amount": 2.38})
        products = (await self.history("bananen"))["products"]
        self.assertEqual([(p["productId"], p["timesBought"]) for p in products], [("4183", 1)])

    async def test_an_imported_order_counts_while_the_account_order_has_no_products(self):
        # #222: de bestelling staat zonder producten in je account; de ingelezen versie telt, tot de producten alsnog binnenkomen.
        await self.jumbo_order("6005201234", "2022-01-12")
        await self.run_import(jumbo_mail_order(), store="jumbo")
        found = (await prices.history("jumbo", "kipfilet", 50, today=TODAY))["products"]
        self.assertEqual(
            [(p["productId"], p["timesBought"], p["purchases"][0]["source"]) for p in found], [("import:jumbo kipfilet 500g", 1, "import")]
        )
        await self.jumbo_order("6005201234", "2022-01-12", items=[KIPFILET])
        found = (await prices.history("jumbo", "kipfilet", 50, today=TODAY))["products"]
        self.assertEqual([(p["productId"], p["timesBought"], p["purchases"][0]["source"]) for p in found], [("123KIP", 1, "order")])

    async def test_imported_purchases_count_as_bought(self):
        await self.run_import(mail_order())
        self.assertIn("import:ah bananen tros", await prices.bought_ids("ah", TODAY))
        self.assertEqual((await prices.history("ah", "", 50, today=TODAY, bought=True))["total"], 2)


class OrdersTest(ImportCase):
    """De ingelezen bestellingen bij Bestellingen: in de lijst, te openen, te doorzoeken en een voor een te verwijderen."""

    async def test_imported_orders_are_listed_newest_first_like_an_order(self):
        await self.run_import(mail_order(), mail_order(number=None, day="2025-04-01", total=None))
        orders = (await price_import.list_orders("ah"))["orders"]
        self.assertEqual(
            [(o["ref"], o["number"], o["date"], o["total"], o["itemCount"], o["imported"]) for o in orders],
            [
                ("dag:2025-04-01", None, "2025-04-01", 42.38, 2, True),  # zonder totaal in de mail: wat je voor de producten betaalde
                ("225301", "225301", "2025-03-14", 141.53, 2, True),
            ],
        )
        self.assertEqual((orders[1]["units"], orders[1]["importedAt"]), (18, NOW))
        self.assertEqual((await price_import.list_orders("jumbo"))["orders"], [])

    async def test_the_imported_list_route_carries_the_first_four_names(self):
        items = [{"name": f"Product {n}", "quantity": 1, "price": 1.0, "total": 1.0, "discount": 0} for n in range(1, 7)]
        await self.run_import(mail_order(items=items))
        listed = (await self.client_for(create_app()).get("/api/imported-orders/ah")).get_json()["orders"][0]
        self.assertEqual(listed["productNames"], ["Product 1", "Product 2", "Product 3", "Product 4"])
        self.assertNotIn("productNames", (await price_import.list_orders("ah"))["orders"][0])

    async def test_an_imported_order_opens_with_its_products(self):
        await self.run_import(mail_order())
        order = await price_import.get_order("ah", "225301")
        self.assertEqual(order["deliveryDate"], "2025-03-14")
        # Nog geen product van AH bekend, en dus ook geen eigen koppeling (#238).
        unlinked = {"productId": None, "image": None, "brand": None, "unitSize": None, "productTitle": None, "link": None}
        mail_fields = ("title", "quantity", "paid", "price", "amount", "bonus", *unlinked)  # de verrijking staat erbij, zie hieronder
        self.assertEqual(
            [{field: item[field] for field in mail_fields} for item in order["items"]],
            [
                {"title": "AH Bananen tros", "quantity": 2, "paid": 1.19, "price": 1.19, "amount": 2.38, "bonus": False, **unlinked},
                {
                    "title": "AH Greenfields Beefburger naturel 2 stuks",
                    "quantity": 16,
                    "paid": 2.5,
                    "price": 4.99,
                    "amount": 40.0,
                    "bonus": True,
                    **unlinked,
                },
            ],
        )
        self.assertIsNone(await price_import.get_order("ah", "999"))
        self.assertIsNone(await price_import.get_order("jumbo", "225301"))

    async def test_an_imported_order_that_later_comes_from_the_account_is_not_listed_twice(self):
        await self.run_import(mail_order(number="225301", day="2026-09-01"), mail_order(number="777", day="2026-09-08"))
        await self.ah_order(225301, "2026-09-01", {"productId": 4183, "title": "AH Bananen tros", "quantity": 2, "price": 1.19, "amount": 2.38})
        await self.ah_order(888, "2026-09-08", {"productId": 4183, "title": "AH Bananen tros", "quantity": 1, "price": 1.19, "amount": 1.19})
        self.assertEqual((await price_import.list_orders("ah"))["orders"], [])
        self.assertIsNone(await price_import.get_order("ah", "225301"))

    async def test_an_imported_order_is_listed_while_the_account_order_has_no_products(self):
        # #222: op nummer (12 jan) en op datum (5 jan) staat er een accountbestelling zonder producten; de ingelezen versie staat erbij.
        await self.jumbo_order("6005201234", "2022-01-12")
        await self.jumbo_order("6005209999", "2022-01-05")
        await self.run_import(jumbo_mail_order(), jumbo_mail_order(number="777", day="2022-01-05"), store="jumbo")
        self.assertEqual([o["ref"] for o in (await price_import.list_orders("jumbo"))["orders"]], ["6005201234", "777"])
        self.assertEqual(len((await price_import.get_order("jumbo", "6005201234"))["items"]), 2)
        self.assertEqual(
            [(o["id"], o.get("imported", False)) for o in (await jumbo_orders.search_purchases("kipfilet"))["occurrences"]],
            [("6005201234", True), ("777", True)],
        )
        # Haalt de app de producten later alsnog op, dan valt de ingelezen versie weg: niets dubbel.
        await self.jumbo_order("6005201234", "2022-01-12", items=[KIPFILET])
        self.assertEqual([o["ref"] for o in (await price_import.list_orders("jumbo"))["orders"]], ["777"])
        self.assertIsNone(await price_import.get_order("jumbo", "6005201234"))
        self.assertEqual(
            [(o["id"], o.get("imported", False)) for o in (await jumbo_orders.search_purchases("kipfilet"))["occurrences"]],
            [("6005201234", False), ("777", True)],
        )

    async def test_an_imported_order_carries_the_status_and_the_delivery_time_from_the_mail(self):
        await self.run_import(
            {**mail_order(), "status": "delivered", "deliveryWindow": {"start": "18:00", "end": "20:00"}},
            {**mail_order(number="777", day="2025-04-01"), "deliveryWindow": {"start": "17:20"}},
        )
        listed = {o["ref"]: o for o in (await price_import.list_orders("ah"))["orders"]}
        self.assertEqual(
            (listed["225301"]["status"], listed["225301"]["slotStart"], listed["225301"]["slotEnd"]),
            ("delivered", "2025-03-14T18:00", "2025-03-14T20:00"),
        )
        # Zonder status in de mail is een ingelezen bestelling toch afgeleverd: ze telt als gekocht (3.6.2).
        self.assertEqual((listed["777"]["status"], listed["777"]["slotStart"], listed["777"]["slotEnd"]), ("delivered", "2025-04-01T17:20", None))
        opened = await price_import.get_order("ah", "225301")
        self.assertEqual((opened["status"], opened["slotStart"], opened["slotEnd"]), ("delivered", "2025-03-14T18:00", "2025-03-14T20:00"))

    async def test_an_import_from_before_the_status_still_works(self):
        # Een database van vóór #229: imported_purchases zonder status en bezorgmoment. Die kolommen komen erbij; de oude rij blijft.
        with closing(db()) as conn, conn:
            conn.execute("""CREATE TABLE imported_purchases (store TEXT NOT NULL, order_ref TEXT NOT NULL, position INTEGER NOT NULL,
                            day TEXT NOT NULL, title TEXT NOT NULL, quantity REAL NOT NULL, paid REAL, regular REAL, order_total REAL,
                            imported_at TEXT NOT NULL, PRIMARY KEY (store, order_ref, position))""")
            conn.execute("INSERT INTO imported_purchases VALUES ('ah', '225301', 0, '2025-03-14', 'AH Bananen tros', 2, 1.19, 1.19, 2.38, ?)", (NOW,))
        order = (await price_import.list_orders("ah"))["orders"][0]
        self.assertEqual(
            (order["ref"], order["status"], order["slotStart"], order["slotEnd"]),
            ("225301", "delivered", None, None),
            "een import van vóór 3.5.0 staat als afgeleverd, zonder tijdvak",
        )
        with closing(db()) as conn:
            self.assertTrue({"status", "slot_start", "slot_end"} <= {row["name"] for row in conn.execute("PRAGMA table_info(imported_purchases)")})
        await self.run_import({**mail_order(number="777", day="2025-04-01"), "status": "delivered"})
        self.assertEqual((await price_import.get_order("ah", "777"))["status"], "delivered")

    async def test_an_imported_order_detail_carries_the_category_the_discount_from_the_mail_and_the_saving(self):
        await self.run_import(mail_order())
        order = await price_import.get_order("ah", "225301")
        bananas, burgers = order["items"]
        self.assertEqual(
            (bananas["category"], bananas["regularPrice"], bananas["discount"], bananas["paidEach"]), ("groente_fruit", 1.19, None, 1.19)
        )
        # 16 x 4.99 met 39.92 korting (2.50 per stuk): de helft. De gewone prijs is die uit de mail.
        self.assertEqual(
            (burgers["category"], burgers["regularPrice"], burgers["discount"], burgers["discountSource"], burgers["timesBefore"]),
            ("vlees", 4.99, 50, "store", 0),
        )
        # (4.99 - 2.50) x 16 = 39.84 bespaard op 2.38 + 40.00 betaald: 48%.
        self.assertEqual((order["saved"], order["savedPercent"], order["statusKind"], order["statusText"]), (39.84, 48, "imported", None))
        self.assertEqual(
            (order["status"], burgers["price"], burgers["paid"], burgers["paidEach"]), ("delivered", 4.99, 2.5, 2.5)
        )  # de bestaande velden blijven

    async def test_an_imported_order_shows_the_products_it_belongs_to(self):
        # #229: dezelfde foto, het merk en de inhoud als in een bestelling uit je account, en het product voor de prijsgeschiedenis.
        await self.ah_order(
            101,
            "2026-09-01",
            {
                "productId": 4183,
                "title": "AH Bananen tros",
                "brand": "AH",
                "unitSize": "per tros",
                "quantity": 1,
                "price": 1.29,
                "amount": 1.29,
                "image": "https://static.ah.nl/bananen.jpg",
            },
        )
        await self.run_import(
            mail_order(
                items=[
                    {"name": "AH Bananen tros", "quantity": 2, "price": 1.19, "total": 2.38},
                    {"name": "Iets uit de mail", "quantity": 4, "price": 2.0, "total": 8.0, "discount": 2.0},
                ]
            )
        )
        items = (await price_import.get_order("ah", "225301"))["items"]
        self.assertEqual(
            [(i["title"], i["productId"], i["image"], i["brand"], i["unitSize"]) for i in items],
            [
                ("AH Bananen tros", "4183", "https://static.ah.nl/bananen.jpg", "AH", "per tros"),
                ("Iets uit de mail", None, None, None, None),
            ],
        )
        products = {p["productId"] for p in (await prices.history("ah", "bananen", 50, today=TODAY))["products"]}
        self.assertIn(items[0]["productId"], products, "de link naar de prijsgeschiedenis komt bij hetzelfde product uit")
        listed = (await self.client_for(create_app()).get("/api/imported-orders/ah")).get_json()["orders"][0]
        self.assertEqual((listed["images"], listed["savings"], listed["bonusCount"]), (["https://static.ah.nl/bananen.jpg"], 2.0, 1))
        self.assertEqual((listed["statusKind"], listed["statusText"], listed["status"]), ("imported", None, "delivered"))
        self.assertNotIn("images", (await price_import.list_orders("ah"))["orders"][0], "alleen de lijstroute rekent de samenvatting uit")

    async def test_every_store_links_the_lines_to_its_own_products(self):
        # Jumbo uit een bestelling in je account; Picnic en Crisp uit de bewaarde prijzen (zoeken in de app).
        await self.jumbo_order("6005209999", "2022-02-03", items=[{**KIPFILET, "image": "https://jumbo.com/kip.png", "unitSize": "500 g"}])
        await prices.record(
            "picnic", [{"id": "s1001", "title": "Picnic Kipfilet 500g", "price": 5.99, "image": "https://picnic.app/kip.png", "unitSize": "500 g"}]
        )
        await prices.record("crisp", [{"id": "c77", "title": "Crisp Kipfilet 500g", "price": 6.49, "image": "https://crisp.nl/kip.png"}])
        for store, name, expected in (
            ("jumbo", "Jumbo Kipfilet 500g", ("123KIP", "https://jumbo.com/kip.png")),
            ("picnic", "Picnic Kipfilet 500g", ("s1001", "https://picnic.app/kip.png")),
            ("crisp", "Crisp Kipfilet 500g", ("c77", "https://crisp.nl/kip.png")),
        ):
            with self.subTest(store=store):
                await self.run_import(
                    mail_order(number=f"{store}-1", day="2022-01-12", items=[{"name": name, "quantity": 1, "price": 5.0}]), store=store
                )
                item = (await price_import.get_order(store, f"{store}-1"))["items"][0]
                self.assertEqual((item["productId"], item["image"]), expected)

    async def test_a_product_without_a_store_photo_shows_its_saved_image(self):
        await prices.record("ah", [{"id": "4183", "title": "AH Bananen tros", "price": 1.29}])
        with closing(db(prices)) as conn, conn:
            conn.execute(
                """INSERT INTO product_images (store, product_id, title, source_url, digest, content_type, width, height, data, saved_at)
                            VALUES ('ah', '4183', 'AH Bananen tros', 'https://x', 'abcdef0123456789abcd', 'image/png', 1, 1, x'00', ?)""",
                (NOW,),
            )
        await self.run_import(mail_order(items=[{"name": "AH Bananen tros", "quantity": 1, "price": 1.29}]))
        image = (await price_import.get_order("ah", "225301"))["items"][0]["image"]
        self.assertTrue(image.startswith("/api/prices/images/") and image.endswith("?v=abcdef0123456789"), image)

    async def test_one_imported_order_can_be_removed_on_its_own(self):
        await self.run_import(mail_order(), mail_order(number="777", day="2025-05-01"))
        self.assertEqual(await price_import.remove("ah", "225301"), {"store": "ah", "removed": 1, "ref": "225301"})
        self.assertEqual([o["ref"] for o in (await price_import.list_orders("ah"))["orders"]], ["777"])
        self.assertEqual((await price_import.remove("ah", "bestaat-niet"))["removed"], 0)
        self.assertEqual((await price_import.summary("ah"))["orders"], 1)
        self.assertEqual(
            (await prices.history("ah", "beefburger", 50, today=TODAY))["products"][0]["timesBought"], 1, "ook weg uit de Prijsgeschiedenis"
        )

    async def test_the_purchase_search_finds_imported_lines(self):
        await self.run_import(mail_order())
        await self.ah_order(101, "2026-09-01", {"productId": 4183, "title": "AH Bananen tros", "quantity": 1, "price": 1.29, "amount": 1.29})
        found = await ah_orders.search_purchases("bananen")
        self.assertEqual(
            [(o["id"], o["date"], o.get("imported", False)) for o in found["occurrences"]],
            [("101", "2026-09-01", False), ("225301", "2025-03-14", True)],
        )
        self.assertEqual(found["products"][0]["times"], 2)
        await self.run_import(mail_order(items=[{"name": "Jumbo Kipfilet", "quantity": 1, "price": 5.0}]), store="jumbo")
        jumbo = (await jumbo_orders.search_purchases("kipfilet"))["occurrences"]
        self.assertEqual([(o["id"], o["amount"], o["imported"]) for o in jumbo], [("225301", 5.0, True)])
        self.assertEqual((await jumbo_orders.search_purchases("bananen"))["occurrences"], [])

    async def test_the_purchase_search_skips_an_imported_order_the_account_also_has(self):
        await self.run_import(mail_order(number="225301", day="2026-09-01"))
        await self.ah_order(225301, "2026-09-01", {"productId": 4183, "title": "AH Bananen tros", "quantity": 2, "price": 1.19, "amount": 2.38})
        self.assertEqual([o["id"] for o in (await ah_orders.search_purchases("bananen"))["occurrences"]], ["225301"])
        self.assertFalse((await ah_orders.search_purchases("bananen"))["occurrences"][0].get("imported"))


class EventLoopReadTest(ImportCase):
    """Het leespad van de prijzen, de ingelezen bestellingen, de aanbiedingen en de detail- en zoekfuncties van AH en Jumbo, op
    aiosqlite. Dat er geen synchrone verbinding meer in de event loop zit, bewaakt test_asyncio_guards.py."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.run_import(mail_order())
        await self.run_import(jumbo_mail_order(), store="jumbo")
        await self.ah_order(101, "2026-09-01", {"productId": 4183, "title": "AH Bananen tros", "quantity": 1, "price": 1.29, "amount": 1.29})
        await self.jumbo_order("J1", "2026-09-02", items=[KIPFILET])
        with closing(db(ah_orders)) as conn, conn:
            conn.execute(
                "INSERT INTO ah_receipts (receipt_id, date_time, total, details_synced, first_seen) VALUES ('R1', '2026-09-10T10:00:00', 1.29, 1, 'x')"
            )
            conn.execute(
                "INSERT INTO ah_receipt_items (receipt_id, position, product_id, name, quantity, price, amount) VALUES ('R1', 0, 555, 'BANANEN', 1, 1.29, 1.29)"
            )

    def test_the_ah_order_detail_and_receipt_enrich_on_the_async_connection(self):
        self.assertEqual(run(ah_orders.get_order(101))["items"][0]["timesBefore"], 0)
        self.assertEqual(run(ah_orders.get_receipt("R1"))["items"][0]["name"], "BANANEN")

    def test_the_purchase_searches_read_the_imported_lines_on_the_async_connection(self):
        self.assertEqual([o["id"] for o in run(ah_orders.search_purchases("bananen"))["occurrences"]], ["R1", "101", "225301"])
        self.assertEqual([o["id"] for o in run(jumbo_orders.search_purchases("kipfilet"))["occurrences"]], ["J1", "6005201234"])

    def test_the_jumbo_order_detail_enriches_on_the_async_connection(self):
        self.assertEqual(run(jumbo_orders.get_order("J1"))["items"][0]["productId"], "123KIP")

    def test_an_imported_order_and_its_linked_names_read_on_the_async_connection(self):
        self.assertEqual(run(price_import.get_order("ah", "225301"))["items"][0]["productId"], "4183")
        self.assertEqual([o["ref"] for o in run(price_import.list_orders("ah", summary=True))["orders"]], ["225301"])
        self.assertEqual(run(prices.link_names("ah", ["AH Bananen tros"]))["AH Bananen tros"]["productId"], "4183")

    def test_the_price_history_and_its_lookup_read_the_receipt_lines_on_the_async_connection(self):
        lookup = mock.AsyncMock()
        products = run(prices.history("ah", "bananen", 50, today=TODAY, bought=True, lookup=lookup))["products"]
        lookup.assert_awaited_once_with(["BANANEN"])
        self.assertEqual(sorted(p["productId"] for p in products), ["4183", "555"])

    def test_the_offer_verdicts_read_the_receipt_lines_on_the_async_connection(self):
        offer = {"id": "4183", "title": "AH Bananen tros", "price": 0.99, "priceBefore": 1.29}
        with self.assertNoLogs("mealieshopper.prices", "ERROR"):
            run(prices.annotate("ah", [offer], today=TODAY))
        self.assertIsNotNone(offer["priceLabel"])

    def test_what_you_bought_the_offers_and_a_saved_image_read_on_the_async_connection(self):
        self.assertIn("4183", run(prices.bought_ids("ah", TODAY)))
        self.assertEqual(run(offers.load("ah"))["offers"], [])
        self.assertIsNone(run(product_images.get(1)))


class RouteTest(ImportCase):
    async def test_the_routes_for_imported_orders(self):
        client = self.client_for(create_app())
        await self.run_import(mail_order(), mail_order(number="AB/12", day="2025-05-01"), mail_order(number=None, day="2025-06-01", total=9.5))
        listed = (await client.get("/api/imported-orders/ah")).get_json()["orders"]
        self.assertEqual([o["ref"] for o in listed], ["dag:2025-06-01:9.50", "AB/12", "225301"])
        self.assertEqual((await client.get("/api/imported-orders/ah/AB%2F12")).get_json()["number"], "AB/12")
        self.assertEqual(len((await client.get("/api/imported-orders/ah/dag:2025-06-01:9.50")).get_json()["items"]), 2)
        self.assertEqual((await client.get("/api/imported-orders/ah/999")).status_code, 404)
        self.assertEqual((await client.get("/api/imported-orders/nope")).status_code, 400)
        self.assertEqual((await client.delete("/api/prices/import?store=ah&ref=AB%2F12")).get_json(), {"store": "ah", "removed": 1, "ref": "AB/12"})
        self.assertEqual(len((await client.get("/api/imported-orders/ah")).get_json()["orders"]), 2)

    def test_everyone_sees_imported_orders_and_only_the_administrator_removes_them(self):
        self.assertFalse(auth.admin_only("/api/imported-orders/ah"))
        self.assertFalse(auth.admin_only("/api/imported-orders/ah/225301"))
        self.assertTrue(auth.admin_only("/api/prices/import"))

    async def test_the_routes(self):
        client = self.client_for(create_app())
        summary = (await client.get("/api/prices/import?store=ah")).get_json()
        self.assertEqual((summary["orders"], "date" in summary["prompt"]), (0, True))
        body = {"store": "ah", "data": json.dumps({"orders": [mail_order()]}), "dryRun": True}
        self.assertEqual((await client.post("/api/prices/import", json=body)).get_json()["new"], 1)
        self.assertEqual((await price_import.summary("ah"))["orders"], 0)
        body["dryRun"] = False
        self.assertEqual((await client.post("/api/prices/import", json=body)).get_json()["imported"], 2)
        self.assertEqual((await client.post("/api/prices/import", json=body)).get_json()["duplicates"], 1)
        self.assertEqual((await client.post("/api/prices/import", json={"store": "ah", "data": "nee"})).status_code, 400)
        self.assertEqual((await client.post("/api/prices/import", json={"store": "nope", "data": "[]"})).status_code, 400)
        self.assertEqual(
            (await client.post("/api/prices/import", json={"store": "lidl", "data": "[]"})).status_code, 400, "geen webwinkel, geen bestellingen"
        )
        self.assertEqual((await client.delete("/api/prices/import?store=ah")).get_json()["removed"], 1)

    def test_only_the_administrator_may_import(self):
        self.assertTrue(auth.admin_only("/api/prices/import"))
        self.assertFalse(auth.admin_only("/api/prices/history"))


class OrdersPageTest(AppTestCase):
    """De knop staat ook bij Bestellingen van elke winkel, alleen voor de beheerder, en opent de dialoog voor die winkel."""

    ROOT = Path(__file__).resolve().parent.parent

    def test_every_orders_page_has_the_import_button(self):
        html = (self.ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        scripts = {name: (self.ROOT / "static" / name).read_text(encoding="utf-8") for name in ("orders.js", "picnic.js", "crisp.js")}
        for button, script, store in (
            ("orders-import", "orders.js", "ordersStore()"),
            ("picnic-orders-import", "picnic.js", '"picnic"'),
            ("crisp-orders-import", "crisp.js", '"crisp"'),
        ):
            tag = re.search(rf'<button[^>]*id="{button}"[^>]*>', html)
            self.assertIsNotNone(tag, button)
            self.assertIn("data-admin-only", tag.group(0), button)
            self.assertIn(f'$("#{button}").addEventListener("click", () => openPriceImport({store}));', scripts[script])
            # Bij AH en Jumbo staan de ingelezen bestellingen als rij in de aankopentabel, bij Picnic en Crisp als kaart bij de afgelopen.
            listed = 'kind: "imported", id: String(order.ref)' if script == "orders.js" else "importedOrderCard(order"
            self.assertIn(listed, scripts[script], f"{script}: de ingelezen bestellingen staan tussen de afgelopen")


class ImportDialogTest(AppTestCase):
    """De importdialoog heeft geen knop om alle ingelezen aankopen in een keer te verwijderen: elke ingelezen bestelling heeft bij
    Bestellingen haar eigen prullenbak (3.6.2)."""

    ROOT = Path(__file__).resolve().parent.parent

    def test_the_dialog_has_no_button_to_remove_everything(self):
        html = (self.ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        dialog = re.search(r'<dialog[^>]*id="import-dialog".*?</dialog>', html, re.S)
        self.assertIsNotNone(dialog)
        self.assertNotIn("import-remove", dialog.group(0))
        for name in ("offers_history.js", "i18n/nl.js", "i18n/en.js"):
            source = (self.ROOT / "static" / name).read_text(encoding="utf-8")
            self.assertNotIn("import-remove", source, name)
            self.assertNotIn('"offers.import.remove', source, name)
        orders = (self.ROOT / "static" / "orders.js").read_text(encoding="utf-8")
        self.assertIn("data-imported-delete", orders, "de prullenbak per ingelezen bestelling blijft")


if __name__ == "__main__":
    unittest.main()
