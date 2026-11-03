"""Prijsgeschiedenis (#178): normale prijs, aanbiedingen en aankopen per product."""
import os
import tempfile
import unittest
from contextlib import closing
from unittest import mock

os.environ.setdefault("MEALIESHOPPER_DATA_DIR", tempfile.mkdtemp())

from mealieshopper import ah, ah_orders, auth, mcp, offers, picnic_orders, prices  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402

TODAY = "2026-10-20"


class PricesCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"MEALIESHOPPER_DATA_DIR": tmp.name, "PASSKEY_AUTH_ENABLED": "false"})
        patcher.start()
        self.addCleanup(patcher.stop)
        auth.init_db()

    def sync_offer(self, when, *items, store="ah"):
        with mock.patch.object(offers, "_now", return_value=when), mock.patch.dict(offers.FETCHERS, {store: lambda: list(items)}):
            offers.sync(store)

    def ah_order(self, order_id, kind, day, *items):
        with closing(ah_orders._connect()) as conn, conn:
            ah_orders._save_order(conn, {"id": order_id, "deliveryDate": day}, kind, list(items) if items else None)

    def ah_receipt(self, receipt_id, when, *lines):
        with closing(ah_orders._connect()) as conn, conn:
            conn.execute("INSERT INTO ah_receipts (receipt_id, date_time, total, details_synced, first_seen) VALUES (?, ?, 0, 1, ?)", (receipt_id, when, when))
            conn.executemany(
                "INSERT INTO ah_receipt_items (receipt_id, position, product_id, name, quantity, price, amount) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(receipt_id, position, *line) for position, line in enumerate(lines)],
            )

    def history(self, store="ah", query="", limit=50):
        return prices.history(store, query, limit, today=TODAY)

    def product(self, product_id, **kwargs):
        return next(p for p in self.history(**kwargs)["products"] if p["productId"] == product_id)


def offer(product_id, title, price=1.0, before=1.5, label="25% korting", **extra):
    return {"id": product_id, "title": title, "price": price, "priceBefore": before, "promoLabel": label, **extra}


def order_item(product_id, title, price, quantity=1, before=None, bonus=False):
    return {"productId": product_id, "title": title, "brand": "AH", "unitSize": "500 g", "quantity": quantity, "amount": price * quantity,
            "price": price, "priceBeforeBonus": before, "bonus": bonus, "taxonomy": "", "image": ""}


class RegularPriceTest(unittest.TestCase):
    def test_without_an_offer_the_price_is_the_regular_price(self):
        self.assertEqual(prices.regular_price({"price": 1.29}), 1.29)
        self.assertEqual(prices.regular_price({"price": {"now": 2.5}}), 2.5)  # de vorm van AH

    def test_with_an_offer_only_the_price_before_counts(self):
        self.assertEqual(prices.regular_price({"price": 1.0, "isBonus": True, "promo": {"priceBefore": 1.49}}), 1.49)
        self.assertEqual(prices.regular_price({"price": 1.0, "listPrice": 1.2}), 1.2)
        self.assertEqual(prices.regular_price({"price": 1.0, "bonus": True, "priceBeforeBonus": 1.3}), 1.3)

    def test_an_offer_without_the_price_before_is_skipped(self):
        self.assertIsNone(prices.regular_price({"price": 1.0, "isBonus": True}))
        self.assertIsNone(prices.regular_price({"price": 1.0, "promo": {"label": "2 voor 3"}}))

    def test_nonsense_is_no_price(self):
        for value in (None, 0, -1, True, "1,29", {"now": None}):
            self.assertIsNone(prices.regular_price({"price": value}), value)


class RecordTest(PricesCase):
    def rows(self):
        with closing(prices._connect()) as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM price_observations ORDER BY store, product_id, day")]

    def test_one_price_per_product_per_day_the_last_one_wins(self):
        products = [{"id": "1", "title": "AH Penne", "price": 1.19, "unitSize": "500 g"}, {"id": "2", "title": "Spaghetti", "price": 0.99}]
        self.assertEqual(prices.record("ah", products, now="2026-10-01T08:00:00+00:00"), 2)
        prices.record("ah", [{"id": "1", "title": "AH Penne", "price": 1.25}], now="2026-10-01T18:00:00+00:00")
        prices.record("ah", [{"id": "1", "title": "AH Penne", "price": 1.25}], now="2026-10-02T08:00:00+00:00")
        rows = self.rows()
        self.assertEqual([(r["product_id"], r["day"], r["price"]) for r in rows], [("1", "2026-10-01", 1.25), ("1", "2026-10-02", 1.25), ("2", "2026-10-01", 0.99)])
        self.assertEqual(rows[0]["unit_size"], "500 g")  # een latere zoekopdracht zonder pakmaat wist hem niet
        self.assertEqual(rows[0]["url"], None)

    def test_products_without_id_title_or_regular_price_are_skipped(self):
        found = [{"title": "Geen id", "price": 1}, {"id": "3", "price": 1}, {"id": "4", "title": "Bonus", "price": 1, "isBonus": True}, "rommel"]
        self.assertEqual(prices.record("ah", found), 0)
        self.assertEqual(prices.record("lidl", [{"id": "1", "title": "X", "price": 1}]), 0)
        self.assertEqual(self.rows(), [])

    def test_a_failure_never_breaks_the_search(self):
        with mock.patch.object(prices, "_connect", side_effect=OSError("schijf vol")), self.assertLogs(prices.log, "WARNING"):
            self.assertEqual(prices.record("ah", [{"id": "1", "title": "X", "price": 1}]), 0)
        search = mock.Mock(return_value=[{"id": "1", "title": "X", "price": 1}])
        with mock.patch.object(prices, "_connect", side_effect=OSError("schijf vol")), self.assertLogs(prices.log, "WARNING"):
            self.assertEqual(prices.recorded("ah", search)("x", 5), search.return_value)
        search.assert_called_once_with("x", 5)

    def test_the_wrapper_records_what_the_store_found(self):
        search = mock.Mock(return_value=[{"webshopId": "9", "title": "Melk", "price": {"now": 1.09}, "images": [{"url": "https://img/9"}]}])
        self.assertEqual(prices.recorded("ah", search)("melk"), search.return_value)
        row = self.rows()[0]
        self.assertEqual((row["product_id"], row["price"], row["image"], row["source"]), ("9", 1.09, "https://img/9", "search"))


class HistoryTest(PricesCase):
    def test_offers_regular_prices_and_purchases_come_together(self):
        self.sync_offer("2026-10-05T05:00:00+00:00", offer("1", "AH Penne", 0.89, 1.19, validFrom="2026-10-05", validUntil="2026-10-11"))
        prices.record("ah", [{"id": "1", "title": "AH Penne", "price": 1.25}], now="2026-10-15T08:00:00+00:00")
        self.ah_order(101, "delivered", "2026-10-07", order_item(1, "AH Penne", 0.89, quantity=2, before=1.19, bonus=True))
        self.ah_order(102, "open", "2026-10-22", order_item(1, "AH Penne", 1.25, quantity=5))  # nog niet geleverd: telt niet
        self.ah_receipt("R1", "2026-10-16T12:30:00", (1, "AH PENNE", 1, 1.25, 1.25))

        product = self.product("1")
        self.assertEqual((product["title"], product["timesOnOffer"], product["lowestPrice"]), ("AH Penne", 1, 0.89))
        self.assertEqual(
            product["regular"],
            [{"from": "2026-10-05", "to": "2026-10-11", "price": 1.19}, {"from": "2026-10-15", "to": "2026-10-15", "price": 1.25}],
        )
        self.assertEqual((product["regularPrice"], product["lowestRegular"], product["highestRegular"]), (1.25, 1.19, 1.25))
        self.assertEqual(
            [(p["date"], p["price"], p["quantity"], p["source"]) for p in product["purchases"]],
            [("2026-10-07", 0.89, 2, "order"), ("2026-10-16", 1.25, 1, "receipt")],
        )
        self.assertEqual((product["timesBought"], product["lastPaid"], product["lowestPaid"]), (2, 1.25, 0.89))
        self.assertEqual(product["averagePaid"], round((0.89 * 2 + 1.25) / 3, 2))
        self.assertEqual(product["lastActivity"], "2026-10-16")
        self.assertEqual(product["url"], "https://www.ah.nl/producten/product/wi1")

    def test_a_product_you_only_bought_is_found_too(self):
        self.ah_order(101, "delivered", "2026-09-01", order_item(7, "AH Volkoren spaghetti", 1.09))
        product = self.product("7", query="spaghetti")
        self.assertEqual((product["timesOnOffer"], product["episodes"], product["onOfferNow"]), (0, [], False))
        self.assertEqual((product["title"], product["brand"], product["unitSize"]), ("AH Volkoren spaghetti", "AH", "500 g"))
        self.assertEqual(product["regular"], [{"from": "2026-09-01", "to": "2026-09-01", "price": 1.09}])

    def test_a_receipt_name_finds_the_product_and_brings_its_other_data(self):
        prices.record("ah", [{"id": "5", "title": "AH Basmati rijst", "price": 2.19}], now="2026-10-01T08:00:00+00:00")
        self.ah_receipt("R1", "2026-10-02T09:00:00", (5, "AH RIJST BASMATI", 2, 2.19, 4.38))
        product = self.product("5", query="RIJST BASMATI")
        self.assertEqual(product["title"], "AH Basmati rijst")  # de zoekopdracht geeft een betere naam dan de bon
        self.assertEqual(len(product["regular"]), 1)

    def test_receipt_lines_without_a_usable_id_are_linked_by_name(self):
        # Rundergehakt tien keer gekocht: twee keer bezorgd, de rest in de winkel. Op de bon staat geen id (of een onbekend id), wel de naam.
        self.ah_order(101, "delivered", "2026-09-01", order_item(11, "AH Rundergehakt", 3.49))
        self.ah_order(102, "delivered", "2026-09-15", order_item(11, "AH Rundergehakt", 3.49))
        for number in range(1, 7):
            self.ah_receipt(f"R{number}", f"2026-10-0{number}T12:00:00", (None, "AH RUNDERGEHAKT", 1, 3.59, 3.59))
        self.ah_receipt("R7", "2026-10-08T12:00:00", (0, "AH Rundergehakt", 1, 3.59, 3.59))
        self.ah_receipt("R8", "2026-10-09T12:00:00", (987654, "AH  RUNDERGEHAKT", 2, 3.59, 7.18))  # een id dat we niet kennen, wel de naam
        data = self.history(query="rundergehakt")
        self.assertEqual(data["total"], 1)
        product = data["products"][0]
        self.assertEqual((product["productId"], product["title"], product["timesBought"]), ("11", "AH Rundergehakt", 10))
        self.assertEqual([p["source"] for p in product["purchases"]].count("receipt"), 8)

    def test_a_linked_article_number_joins_the_product_from_the_orders(self):
        # Op de bon "AH BLOEMK" (afgekort) met artikelnummer 20960, in de bestelling "AH Bloemkool" met id 4183: een product (issue #178).
        self.ah_order(101, "delivered", "2026-09-01", order_item(4183, "AH Bloemkool", 1.69))
        self.ah_receipt("R1", "2026-10-02T09:00:00", (20960, "AH BLOEMK", 2, 1.99, 3.98))
        self.ah_receipt("R2", "2026-10-09T09:00:00", (20960, "AH BLOEMK", 1, 1.85, 1.85))
        self.assertEqual(sorted(p["productId"] for p in self.history(query="bloem")["products"]), ["20960", "4183"], "zonder koppeling: twee")
        with closing(ah_orders._connect()) as conn, conn:
            conn.execute("INSERT INTO ah_receipt_products (receipt_product_id, product_id, title, checked_at) VALUES (20960, 4183, 'AH Bloemkool', 'x')")
        data = self.history(query="bloem")
        self.assertEqual(data["total"], 1)
        product = data["products"][0]
        self.assertEqual((product["productId"], product["title"], product["timesBought"]), ("4183", "AH Bloemkool", 3))
        self.assertEqual([p["source"] for p in product["purchases"]], ["order", "receipt", "receipt"])
        self.assertEqual(self.history(query="BLOEMK")["total"], 1, "de naam op de bon vindt hem ook")

    def test_a_receipt_name_without_the_house_brand_finds_the_product(self):
        # Op de bon "BLOEMKOOL", in de bestelling "AH Bloemkool" (issue #178): zonder "AH" vooraan is het dezelfde naam.
        self.ah_order(101, "delivered", "2026-09-01", order_item(4183, "AH Bloemkool", 1.69))
        self.ah_receipt("R1", "2026-10-02T09:00:00", (None, "BLOEMKOOL", 1, 1.99, 1.99))
        product = self.product("4183", query="bloemkool")
        self.assertEqual((product["timesBought"], [p["source"] for p in product["purchases"]]), (2, ["order", "receipt"]))

    def test_a_bare_name_that_fits_two_products_is_not_guessed(self):
        self.ah_order(101, "delivered", "2026-09-01", order_item(1, "AH Bloemkool", 1.69), order_item(2, "Bloemkool", 1.49))
        self.ah_receipt("R1", "2026-10-02T09:00:00", (None, "BLOEMKOOL ", 1, 1.99, 1.99))
        ids = sorted(p["productId"] for p in self.history(query="bloemkool")["products"])
        self.assertEqual(ids, ["1", "2"], "precies dezelfde naam gaat voor: Bloemkool")
        self.assertEqual(self.product("2", query="bloemkool")["timesBought"], 2)

    def test_an_article_number_that_ah_did_not_find_changes_nothing(self):
        self.ah_receipt("R1", "2026-10-02T09:00:00", (43947, "DRAAGTAS", 1, 0.25, 0.25))
        with closing(ah_orders._connect()) as conn, conn:
            conn.execute("INSERT INTO ah_receipt_products (receipt_product_id, product_id, title, checked_at) VALUES (43947, NULL, NULL, 'x')")
        self.assertEqual([p["productId"] for p in self.history(query="draagtas")["products"]], ["43947"])

    def test_a_receipt_line_that_matches_nothing_is_still_found(self):
        self.ah_receipt("R1", "2026-10-02T09:00:00", (None, "AH VERSE KNOFLOOK", 1, 0.79, 0.79))
        product = self.history(query="knoflook")["products"][0]
        self.assertEqual((product["productId"], product["title"], product["timesBought"], product["url"]), ("bon:ah verse knoflook", "AH VERSE KNOFLOOK", 1, ""))
        self.assertEqual(self.product("bon:ah verse knoflook", query="")["timesBought"], 1)

    def test_purchases_that_are_not_fetched_yet_are_counted(self):
        self.ah_order(101, "delivered", "2026-09-01")  # geen producten: items_synced blijft 0
        with closing(ah_orders._connect()) as conn, conn:
            conn.execute("INSERT INTO ah_receipts (receipt_id, date_time, total, details_synced, first_seen) VALUES ('R9', '2026-10-01', 1, 0, 'x')")
        self.assertEqual(self.history()["unsynced"], {"ah": {"orders": 1, "receipts": 1}})

    def test_search_by_words_and_pack_size(self):
        record = lambda pid, title, size: prices.record("ah", [{"id": pid, "title": title, "price": 3.0, "unitSize": size}], now="2026-10-01T08:00:00+00:00")
        record("1", "AH Rundergehakt", "500 g")
        record("2", "AH Rundergehakt", "300 g")
        record("3", "AH Half-om-half gehakt", "0,5 kg")
        record("4", "AH Kipgehakt", "1 kg")
        record("5", "AH Gehaktkruiden", "per stuk")
        self.ah_receipt("R1", "2026-10-02T09:00:00", (None, "SLAGER GEHAKT", 1, 4.0, 4.0), (None, "AH GEHAKT 300G", 1, 2.0, 2.0))
        found = lambda query: sorted(p["productId"] for p in self.history(query=query)["products"])
        self.assertEqual(found("Gehakt 500 gram"), ["1", "3", "bon:slager gehakt"])  # de bon zonder maat blijft erin, die van 300 g niet
        self.assertEqual(found("500g gehakt"), found("Gehakt 500 gram"))  # volgorde en schrijfwijze van de maat maken niet uit
        self.assertEqual(found("gehakt 0,5 kg"), found("Gehakt 500 gram"))
        self.assertEqual(found("rundergehakt 300 gram"), ["2"])
        self.assertEqual(found("gehakt"), ["1", "2", "3", "4", "5", "bon:ah gehakt 300g", "bon:slager gehakt"])
        self.assertEqual(found("half om half"), ["3"])

    def test_the_same_price_on_more_days_is_one_period_and_a_change_starts_a_new_one(self):
        for day, price in (("01", 1.0), ("02", 1.0), ("05", 1.0), ("06", 1.2), ("09", 1.0)):
            prices.record("ah", [{"id": "1", "title": "Kaas", "price": price}], now=f"2026-10-{day}T08:00:00+00:00")
        self.assertEqual(
            self.product("1")["regular"],
            [{"from": "2026-10-01", "to": "2026-10-05", "price": 1.0}, {"from": "2026-10-06", "to": "2026-10-06", "price": 1.2},
             {"from": "2026-10-09", "to": "2026-10-09", "price": 1.0}],
        )

    def test_on_one_day_the_search_beats_an_order_and_an_order_beats_an_offer(self):
        points = [("2026-10-01", 1.5, "offer"), ("2026-10-01", 1.4, "order"), ("2026-10-02", 1.5, "offer"), ("2026-10-02", 1.3, "search")]
        self.assertEqual(
            prices._regular_segments(points),
            [{"from": "2026-10-01", "to": "2026-10-01", "price": 1.4}, {"from": "2026-10-02", "to": "2026-10-02", "price": 1.3}],
        )

    def test_picnic_lines_are_divided_by_the_quantity_and_cancelled_or_future_orders_are_left_out(self):
        with closing(picnic_orders._connect()) as conn, conn:
            line = {"id": "s1", "title": "Picnic penne", "unitSize": "500 g", "quantity": 3, "price": 2.67, "listPrice": 3.57}
            picnic_orders._save_order(conn, {"id": "P1", "status": "COMPLETED", "deliveredStart": "2026-10-03T18:00:00+02:00"}, [line])
            picnic_orders._save_order(conn, {"id": "P2", "status": "CANCELLED", "windowStart": "2026-10-04T18:00:00+02:00"}, [{**line, "price": 0.3}])
            picnic_orders._save_order(conn, {"id": "P3", "status": "CURRENT", "windowStart": "2026-10-30T18:00:00+02:00"}, [{**line, "price": 0.6}])
        product = self.product("s1", store="picnic")
        self.assertEqual([(p["date"], p["price"], p["regularPrice"]) for p in product["purchases"]], [("2026-10-03", 0.89, 1.19)])
        self.assertEqual(product["regular"], [{"from": "2026-10-03", "to": "2026-10-03", "price": 1.19}])

    def test_newest_first_with_a_limit_and_the_total(self):
        for number in range(1, 6):
            prices.record("ah", [{"id": str(number), "title": f"Product {number}", "price": 1}], now=f"2026-10-0{number}T08:00:00+00:00")
        data = self.history(limit=2)
        self.assertEqual(([p["productId"] for p in data["products"]], data["total"]), (["5", "4"], 5))
        self.assertEqual(self.history(query="product 3")["total"], 1)
        self.assertEqual(self.history(query="100%")["total"], 0)  # % en _ zijn letters, geen jokers

    def test_stores_are_kept_apart_and_an_unlinked_store_is_simply_empty(self):
        prices.record("ah", [{"id": "1", "title": "Melk", "price": 1}])
        self.assertEqual(self.history(store="jumbo"), {"products": [], "total": 0, "unsynced": {"jumbo": {"orders": 0, "receipts": 0}}})  # geen tabellen
        self.assertEqual(prices.history(None, "melk", 10)["total"], 1)
        with self.assertRaises(ValueError):
            prices.history("lidl")


class RouteAndToolTest(PricesCase):
    def test_the_route(self):
        prices.record("ah", [{"id": "1", "title": "Kipfilet", "price": 5.49}])
        client = create_app().test_client()
        reply = client.get("/api/prices/history?store=ah&q=kip&limit=5").get_json()
        self.assertEqual((reply["total"], reply["products"][0]["regularPrice"]), (1, 5.49))
        self.assertEqual(client.get("/api/prices/history?store=lidl").status_code, 400)
        self.assertEqual(client.get("/api/prices/history?limit=abc").status_code, 200)

    def test_the_check_list_of_the_week_menu_records_the_regular_price(self):
        found = [{"webshopId": "42", "title": "AH Penne", "price": {"now": 1.19}, "unitSize": "500 g", "images": [], "isBonus": False}]
        with mock.patch.object(ah, "search_products", return_value=found):
            reply = create_app().test_client().post("/api/ah/cart/preview", json={"items": [{"query": "penne", "quantity": 500, "unit": "g"}]})
        self.assertEqual(reply.status_code, 200)
        self.assertEqual(prices.history("ah", "penne")["products"][0]["regularPrice"], 1.19)

    def test_the_mcp_tool_is_compact(self):
        self.sync_offer("2026-10-05T05:00:00+00:00", offer("1", "AH Penne", 0.89, 1.19))
        self.ah_order(101, "delivered", "2026-10-07", order_item(1, "AH Penne", 0.89, quantity=2, before=1.19, bonus=True))
        data = mcp.TOOLS["get_price_history"]["handler"]({"query": "penne", "store": "ah"})
        product = data["products"][0]
        self.assertEqual((product["title"], product["timesOnOffer"], product["timesBought"], product["regularPrice"]), ("AH Penne", 1, 1, 1.19))
        self.assertEqual(product["purchases"], [{"date": "2026-10-07", "pricePerItem": 0.89, "quantity": 2, "source": "order"}])
        self.assertIn("hint", data)
        with self.assertRaises(mcp.ToolError):
            mcp.TOOLS["get_price_history"]["handler"]({"store": "lidl"})



class UnitPriceTest(unittest.TestCase):
    """Testgevallen uit het userscript van wolph (prijs per kg/ml/etc.)."""

    def test_packs_become_a_price_per_kilo_litre_or_piece(self):
        cases = [
            (2.50, "2 x 250 g", {"value": 5.0, "unit": "kg"}),
            (3.00, "ca. 500 g", {"value": 6.0, "unit": "kg"}),
            (1.29, "per stuk", {"value": 1.29, "unit": "stuk"}),
            (6.49, "6 x 1 l", {"value": 1.08, "unit": "l"}),  # de prijs van het pak, gedeeld door zes liter
            (3.00, "75 cl", {"value": 4.0, "unit": "l"}),
            (2.00, "4 stuks", {"value": 0.5, "unit": "stuk"}),
            (1.50, "1,5 l", {"value": 1.0, "unit": "l"}),
            (2.19, "0,5 kg", {"value": 4.38, "unit": "kg"}),
        ]
        for price, size, expected in cases:
            self.assertEqual(prices.unit_price(price, size), expected, size)

    def test_unknown_packs_and_prices_give_nothing(self):
        for price, size in ((1.0, "1 bos"), (1.0, ""), (None, "500 g"), (0, "500 g"), (1.0, "0 g")):
            self.assertIsNone(prices.unit_price(price, size), (price, size))

    def test_the_base_title_leaves_out_sizes_and_pack_words(self):
        self.assertEqual(prices.base_title("AH Zalmfilet 3-pack"), "ah zalmfilet")
        self.assertEqual(prices.base_title("AH Penne voordeelverpakking"), "ah penne")
        self.assertEqual(prices.base_title("Coca-Cola Zero 6 x 1,5 l"), "coca cola zero")
        self.assertEqual(prices._search_query("Coca-Cola Zero 6 x 1,5 l"), "Coca-Cola Zero")
        self.assertEqual(prices._search_query("AH Zalmfilet 3-pack"), "AH Zalmfilet")


def deal(product_id, title, price, before=None, label="", size="500 g", brand="AH", **extra):
    from mealieshopper import deals

    return {"id": product_id, "title": title, "brand": brand, "unitSize": size, "price": price, "priceBefore": before, "promoLabel": label,
            **deals.describe(label, price_now=price, price_before=before), **extra}


class VerdictTest(PricesCase):
    def annotate(self, *items, store="ah"):
        return {item["id"]: item for item in prices.annotate(store, list(items), today=TODAY)}

    def observe(self, day, product_id, title, price, size="500 g", brand="AH", store="ah"):
        prices.record(store, [{"id": product_id, "title": title, "price": price, "unitSize": size, "brand": brand}], now=f"{day}T08:00:00+00:00")

    def test_a_real_deal_is_far_below_the_lowest_regular_price_of_30_days(self):
        self.observe("2026-10-10", "1", "AH Penne", 1.19)
        offer = self.annotate(deal("1", "AH Penne", 0.89, 1.19, "25% korting"))["1"]
        self.assertEqual({k: offer["verdict"][k] for k in ("kind", "percent", "lowestRegular", "inflated")},
                         {"kind": "good", "percent": 25, "lowestRegular": 1.19, "inflated": False})
        self.assertEqual(offer["unitPricePer"], {"value": 1.78, "unit": "kg"})

    def test_a_price_before_that_was_raised_is_seen_through(self):
        self.observe("2026-10-05", "1", "AH Kaas", 1.10)  # binnen 30 dagen: de gewone prijs was lager dan de prijs ervoor van de actie
        self.observe("2026-09-01", "1", "AH Kaas", 0.90)  # ouder dan 30 dagen: telt niet
        verdict = self.annotate(deal("1", "AH Kaas", 1.12, 1.50, "nu 1,12"))["1"]["verdict"]
        self.assertEqual((verdict["kind"], verdict["lowestRegular"], verdict["inflated"]), ("none", 1.10, True))

    def test_a_small_saving(self):
        self.assertEqual(self.annotate(deal("1", "AH Rijst", 1.80, 2.00))["1"]["verdict"]["kind"], "small")

    def test_a_deal_with_a_minimum_uses_the_price_per_item(self):
        # AH geeft bij "2 voor 3,00" de gewone prijs als prijs en geen prijs ervoor.
        verdict = self.annotate(deal("1", "AH Hummus", 2.00, None, "2 voor 3,00"))["1"]["verdict"]
        self.assertEqual((verdict["kind"], verdict["percent"], verdict["lowestRegular"]), ("good", 25, 2.0))
        for label, expected in (("2e halve prijs", 25), ("1+1 gratis", 50)):
            self.assertEqual(self.annotate(deal("2", "AH Soep", 2.00, 2.00, label))["2"]["verdict"]["percent"], expected, label)

    def test_ah_gives_the_regular_price_for_one_plus_one_free(self):
        # Zo staat "1+1 gratis" in de aanbiedingen van AH: de prijs is de gewone prijs, zonder prijs ervoor (wolph: 300 g rundergehakt).
        offer = self.annotate(deal("1", "AH Rundergehakt", 3.49, None, "1+1 gratis", size="300 g"))["1"]
        self.assertEqual((offer["unitPrice"], offer["unitPricePer"]), (1.75, {"value": 5.83, "unit": "kg"}))
        self.assertEqual((offer["verdict"]["kind"], offer["verdict"]["percent"], offer["verdict"]["lowestRegular"]), ("good", 50, 3.49))
        half = self.annotate(deal("2", "AH Soep", 2.00, None, "2e halve prijs"))["2"]
        self.assertEqual((half["unitPrice"], half["verdict"]["percent"]), (1.5, 25))
        unknown = self.annotate(deal("3", "AH Kaas", 2.00, None, "25% korting"))["3"]  # is 2,00 al verlaagd? Dat weten we niet.
        self.assertEqual((unknown["unitPrice"], unknown["verdict"]["kind"]), (None, "unknown"))

    def test_the_bulk_pack_is_compared_with_the_price_per_item_of_one_plus_one(self):
        self.observe("2026-10-15", "bulk", "AH Rundergehakt", 9.99, size="1000 g")  # €9,99/kg
        verdict = self.annotate(deal("1", "AH Rundergehakt", 3.49, None, "1+1 gratis", size="300 g"))["1"]["verdict"]  # €5,83/kg
        self.assertIsNone(verdict["cheaperPack"])
        verdict = self.annotate(deal("1", "AH Rundergehakt", 3.49, None, "2e halve prijs", size="300 g"))["1"]["verdict"]  # €8,73/kg
        self.assertIsNone(verdict["cheaperPack"])
        verdict = self.annotate(deal("1", "AH Rundergehakt", 3.49, 3.69, "nu 3,49", size="300 g"))["1"]["verdict"]  # €11,63/kg
        self.assertEqual(verdict["cheaperPack"]["productId"], "bulk")

    def test_without_a_regular_price_or_a_price_per_item_there_is_no_verdict(self):
        verdict = self.annotate(deal("p1", "Picnic yoghurt", 1.20, None, "20% korting"), store="picnic")["p1"]["verdict"]
        self.assertEqual((verdict["kind"], verdict["percent"]), ("unknown", None))

    def test_often_on_offer_and_the_lowest_offer_price_in_a_year(self):
        weeks = ("2026-01-05", "2026-03-02", "2026-04-27", "2026-06-22", "2026-08-17")
        for start in weeks:
            end = (prices.date.fromisoformat(start) + prices.timedelta(days=6)).isoformat()
            self.sync_offer(f"{start}T05:00:00+00:00", offer("1", "AH Pindakaas", 2.49, 2.99, "nu 2,49", validFrom=start, validUntil=end))
            self.sync_offer(f"{end}T23:00:00+00:00")
        self.sync_offer("2026-10-19T05:00:00+00:00", offer("1", "AH Pindakaas", 1.99, 2.99, "nu 1,99", validFrom="2026-10-19", validUntil="2026-10-25"))
        verdict = self.annotate(deal("1", "AH Pindakaas", 1.99, 2.99, "nu 1,99"))["1"]["verdict"]
        self.assertEqual((verdict["timesThisYear"], verdict["often"], verdict["bestInYear"]), (6, True, True))
        self.assertFalse(self.annotate(deal("1", "AH Pindakaas", 2.79, 2.99, "nu 2,79"))["1"]["verdict"]["bestInYear"])

    def test_a_bigger_pack_that_is_cheaper_per_kilo(self):
        self.observe("2026-10-15", "big", "AH Zalmfilet", 7.00, size="500 g")  # €14/kg
        self.observe("2026-10-15", "other", "Kerstmis Zalmfilet", 5.00, size="500 g", brand="Vishandel")  # ander merk: telt niet
        verdict = self.annotate(deal("small", "AH Zalmfilet 2 stuks", 4.00, 4.50, "nu 4,00", size="250 g"))["small"]["verdict"]  # €16/kg
        self.assertEqual(verdict["cheaperPack"], {"productId": "big", "title": "AH Zalmfilet", "unitSize": "500 g", "price": 7.0,
                                                  "unitPrice": {"value": 14.0, "unit": "kg"}, "onOffer": False})
        cheap = self.annotate(deal("small", "AH Zalmfilet 2 stuks", 3.00, 4.50, "nu 3,00", size="250 g"))["small"]["verdict"]  # €12/kg
        self.assertIsNone(cheap["cheaperPack"])

    def test_a_failure_leaves_the_offers_as_they_were(self):
        items = [deal("1", "AH Penne", 0.89, 1.19, "25% korting")]
        with mock.patch.object(prices, "_connect", side_effect=OSError("kapot")), self.assertLogs(prices.log, "WARNING"):
            self.assertIs(prices.annotate("ah", items, today=TODAY), items)
        self.assertNotIn("verdict", items[0])


class OffersRouteVerdictTest(PricesCase):
    def test_the_offers_carry_a_verdict_and_a_new_price_refreshes_the_cache(self):
        self.sync_offer("2026-11-02T05:00:00+00:00", offer("1", "AH Penne", 0.89, 1.19, "25% korting", unitSize="500 g"))
        client = create_app().test_client()
        first = client.get("/api/offers?store=ah")
        item = first.get_json()["offers"][0]
        self.assertEqual((item["verdict"]["kind"], item["unitPricePer"]["unit"]), ("good", "kg"))
        self.assertEqual(client.get("/api/offers?store=ah", headers={"If-None-Match": first.headers["ETag"]}).status_code, 304)
        prices.record("ah", [{"id": "9", "title": "AH Spaghetti", "price": 1.0}])
        self.assertEqual(client.get("/api/offers?store=ah", headers={"If-None-Match": first.headers["ETag"]}).status_code, 200)


class CompareTest(PricesCase):
    def found(self):
        return [
            {"webshopId": "1", "title": "AH Penne", "price": {"now": 1.19}, "unitSize": "500 g", "brand": "AH", "isBonus": False},
            {"webshopId": "2", "title": "AH Penne voordeelverpakking", "price": {"now": 1.99}, "unitSize": "1 kg", "brand": "AH", "isBonus": False},
            {"webshopId": "3", "title": "Barilla Penne Rigate", "price": {"now": 1.69}, "unitSize": "500 g", "brand": "Barilla", "isBonus": False},
            {"webshopId": "4", "title": "AH Pastasaus", "price": {"now": 1.49}, "unitSize": "500 ml", "brand": "AH", "isBonus": False},
            {"webshopId": "5", "title": "De Cecco Penne", "price": {"now": 3.0}, "unitSize": "500 g", "brand": "De Cecco", "isBonus": True,
             "promo": {"label": "2e halve prijs", "priceBefore": 3.0}},
        ]

    def test_pack_sizes_side_by_side_cheapest_first(self):
        self.sync_offer("2026-11-02T05:00:00+00:00", offer("1", "AH Penne", 0.89, 1.19, "25% korting", brand="AH", unitSize="500 g"))
        with mock.patch.object(ah, "search_products", return_value=self.found()) as search:
            data = prices.compare("ah", "1")
        search.assert_called_once_with("AH Penne", prices.COMPARE_LIMIT)
        self.assertEqual((data["unit"], data["product"]["price"], data["product"]["unitPrice"]), ("kg", 0.89, {"value": 1.78, "unit": "kg"}))
        rows = [(c["id"], c["unitPrice"]["value"], c["self"], c["same"], c["cheaper"]) for c in data["candidates"]]
        self.assertEqual(rows, [
            ("1", 1.78, True, True, False),  # de actieprijs van het gekozen product, niet de gewone prijs uit de zoekopdracht
            ("2", 1.99, False, True, False),
            ("3", 3.38, False, False, False),
            ("5", 4.5, False, False, False),  # 2e halve prijs: €2,25 per stuk
        ])
        self.assertEqual([c["rank"] for c in data["candidates"]], [0, 0.33, 0.67, 1])
        self.assertEqual(prices.history("ah", "voordeelverpakking")["products"][0]["regularPrice"], 1.99)  # de zoekopdracht is bewaard

    def test_another_variant_is_not_the_same_product(self):
        self.assertTrue(prices._same_product("ah penne", "AH Penne voordeelverpakking"))
        self.assertFalse(prices._same_product("ah penne", "AH Biologisch Penne"))
        self.assertFalse(prices._same_product("", "AH Penne"))

    def test_a_cheaper_bigger_pack_is_marked(self):
        prices.record("ah", [{"id": "1", "title": "AH Penne", "price": 1.19, "unitSize": "500 g", "brand": "AH"}])
        with mock.patch.object(ah, "search_products", return_value=self.found()):
            data = prices.compare("ah", "1")
        self.assertEqual([c["id"] for c in data["candidates"] if c["cheaper"]], ["2"])  # €1,99/kg tegen €2,38/kg

    def test_one_plus_one_free_is_compared_per_item(self):
        self.sync_offer("2026-11-02T05:00:00+00:00", offer("1", "AH Rundergehakt", 3.49, None, "1+1 gratis", brand="AH", unitSize="300 g"))
        with mock.patch.object(ah, "search_products", return_value=[]):
            data = prices.compare("ah", "1")
        self.assertEqual((data["product"]["price"], data["product"]["unitPrice"]), (1.75, {"value": 5.83, "unit": "kg"}))

    def test_same_kind_and_same_brand_for_the_filters(self):
        prices.record("ah", [{"id": "1", "title": "AH Rundergehakt", "price": 3.49, "unitSize": "300 g", "brand": "AH"}])
        found = [
            {"webshopId": "1", "title": "AH Rundergehakt", "price": {"now": 3.49}, "unitSize": "300 g", "brand": "AH", "isBonus": False},
            {"webshopId": "2", "title": "AH Rundergehakt", "price": {"now": 9.99}, "unitSize": "1000 g", "brand": "AH", "isBonus": False},
            {"webshopId": "3", "title": "AH Biologisch rundergehakt", "price": {"now": 4.99}, "unitSize": "300 g", "brand": "AH", "isBonus": False},
            {"webshopId": "4", "title": "Slagerij Rundergehakt mager", "price": {"now": 5.49}, "unitSize": "500 g", "brand": "Slagerij", "isBonus": True,
             "promo": {"label": "25% korting", "priceBefore": 5.49}},
            {"webshopId": "5", "title": "AH Hamburgers", "price": {"now": 3.99}, "unitSize": "400 g", "brand": "AH", "isBonus": False},
            {"webshopId": "6", "title": "AH Half-om-half gehakt", "price": {"now": 2.99}, "unitSize": "500 g", "brand": "AH", "isBonus": False},
        ]
        with mock.patch.object(ah, "search_products", return_value=found) as search:
            data = prices.compare("ah", "1")
        search.assert_called_once_with("AH Rundergehakt", 30)  # genoeg resultaten voor de grote verpakking
        self.assertEqual(data["kind"], "rundergehakt")
        flags = {c["id"]: (c["sameKind"], c["sameBrand"], c["onOffer"]) for c in data["candidates"]}
        self.assertEqual(flags, {
            "1": (True, True, False), "2": (True, True, False), "3": (True, True, False), "4": (True, False, True),
            "5": (False, True, False), "6": (False, True, False),
        })

    def test_the_route_refuses_an_unknown_product(self):
        client = create_app().test_client()
        self.assertEqual(client.get("/api/prices/compare?store=ah&id=404").status_code, 400)
        self.assertEqual(client.get("/api/prices/compare?store=lidl&id=1").status_code, 400)


def ah_found(pid, title, price, size, brand="AH", bonus=None):
    item = {"webshopId": pid, "title": title, "price": {"now": price}, "unitSize": size, "brand": brand, "images": [], "isBonus": bool(bonus)}
    if bonus:
        item["promo"] = bonus
    return item


class CheckListTest(PricesCase):
    """De vergelijking in de controlelijst van het weekmenu: alleen zelfde product of goedkoper per eenheid (#178)."""

    def found(self):
        return [
            ah_found("300", "AH Rundergehakt", 3.79, "300 g", bonus={"label": "voor 3.79", "priceBefore": 4.29}),  # €12,63/kg
            ah_found("bio", "AH Biologisch Rundergehakt", 4.99, "250 g"),  # €19,96/kg: zelfde soort, maar niet goedkoper
            ah_found("500", "AH Rundergehakt", 5.35, "500 g"),  # €10,70/kg: zelfde product en goedkoper
            ah_found("175", "AH Rundergehakt", 2.99, "175 g"),  # €17,09/kg: zelfde product, duurder
            ah_found("green", "AH Greenfields Rundergehakt", 5.99, "300 g"),  # €19,97/kg
            ah_found("mix", "AH Gemengd gehakt", 8.99, "1 kg"),  # €8,99/kg: goedkoper, maar geen rundergehakt
            ah_found("mager", "AH Mager rundergehakt", 6.79, "500 g"),  # €13,58/kg: duurder, geen zelfde product
            ah_found("minder", "AH Rundergehakt minder vet", 3.69, "300 g"),  # €12,30/kg: goedkoper per kg, zelfde soort
        ]

    def preview(self, compare, quantity=500, unit="g"):
        body = {"items": [{"query": "rundergehakt", "quantity": quantity, "unit": unit}]}
        if compare:
            body["compare"] = True
        with mock.patch.object(ah, "search_products", side_effect=lambda query, limit: self.found()[:limit]) as search:
            reply = create_app().test_client().post("/api/ah/cart/preview", json=body)
        self.assertEqual(reply.status_code, 200)
        return reply.get_json()["items"][0]["candidates"], search

    def test_the_week_menu_shows_only_the_same_product_or_cheaper_per_unit(self):
        candidates, search = self.preview(compare=True)
        search.assert_called_once_with("rundergehakt", prices.COMPARE_LIMIT)
        rows = [(c["productId"], c["unitPrice"]["value"], c.get("same", False), c.get("cheaper", False)) for c in candidates]
        self.assertEqual(rows, [
            ("300", 12.63, False, False),  # het voorgestelde product blijft bovenaan
            ("500", 10.7, True, True),
            ("minder", 12.3, False, True),
            ("175", 17.09, True, False),
        ])
        self.assertTrue(candidates[0]["suggested"])
        self.assertEqual([c["packs"] for c in candidates][:2], [2, 1])  # 500 g: twee keer 300 g of één keer 500 g

    def test_searching_yourself_shows_the_first_five_as_before(self):
        candidates, search = self.preview(compare=False)
        search.assert_called_once_with("rundergehakt", 5)
        self.assertEqual([c["productId"] for c in candidates], ["300", "bio", "500", "175", "green"])
        self.assertEqual(candidates[1]["unitPrice"], {"value": 19.96, "unit": "kg"})  # de prijs per eenheid staat er wel bij
        self.assertNotIn("same", candidates[1])

    def test_one_plus_one_counts_per_item_in_the_check_list(self):
        one_plus_one = {"label": "1+1 gratis", "mechanism": "1+1 gratis"}  # AH geeft de gewone prijs en geen prijs ervoor
        self.assertEqual(prices.per_item_price({"price": 3.49, "deal": {"label": "1+1 gratis", "unitPrice": None, "validity": "valid"}}), 1.75)
        self.assertEqual(prices.per_item_price({"price": 3.49, "deal": {"label": "1+1 gratis", "unitPrice": None, "validity": "expired"}}), 3.49)
        self.assertEqual(prices.per_item_price({"price": 3.49}), 3.49)
        found = [ah_found("300", "AH Rundergehakt", 3.49, "300 g", bonus=one_plus_one), ah_found("1000", "AH Rundergehakt", 9.99, "1000 g")]
        with mock.patch.object(ah, "search_products", return_value=found):
            reply = create_app().test_client().post("/api/ah/cart/preview", json={"compare": True, "items": [{"query": "rundergehakt"}]})
        candidates = reply.get_json()["items"][0]["candidates"]
        self.assertEqual([(c["productId"], c["unitPrice"]["value"]) for c in candidates], [("300", 5.83), ("1000", 9.99)])  # zelfde product
        self.assertFalse(candidates[1]["cheaper"])

    def test_without_a_pack_size_only_the_same_product_is_added(self):
        found = [ah_found("a", "AH Basilicum", 1.29, ""), ah_found("b", "AH Basilicum", 2.49, "1 bos"), ah_found("c", "AH Basilicum pesto", 2.0, "190 g")]
        result = prices.check_list_candidates([{"productId": f["webshopId"], "title": f["title"], "brand": "AH", "unitSize": f["unitSize"], "price": f["price"]["now"]} for f in found], True)
        self.assertEqual([c["productId"] for c in result], ["a", "b"])

if __name__ == "__main__":
    unittest.main()
