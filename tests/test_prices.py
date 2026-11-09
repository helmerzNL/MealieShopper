"""Prijsgeschiedenis (#178): normale prijs, aanbiedingen en aankopen per product."""
import os
import tempfile
import unittest
from contextlib import closing
from unittest import mock

os.environ.setdefault("MEALIESHOPPER_DATA_DIR", tempfile.mkdtemp())

from mealieshopper import ah, ah_orders, auth, mcp, offers, picnic_orders, price_import, prices, product_links  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402
from helpers import AppTestCase, db, in_db, returned, run, served  # noqa: E402

TODAY = "2026-10-20"


class PricesCase(AppTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"MEALIESHOPPER_DATA_DIR": tmp.name, "PASSKEY_AUTH_ENABLED": "false"})
        patcher.start()
        self.addCleanup(patcher.stop)
        run(auth.init_db())

    async def sync_offer(self, when, *items, store="ah"):
        with mock.patch.object(offers, "_now", return_value=when), mock.patch.dict(offers.FETCHERS, {store: lambda: returned(list(items))}):
            await offers.sync(store)

    async def ah_order(self, order_id, kind, day, *items):
        await in_db(ah_orders._connect, lambda conn: ah_orders._save_order(conn, {"id": order_id, "deliveryDate": day}, kind, list(items) if items else None))

    def ah_receipt(self, receipt_id, when, *lines):
        with closing(db(ah_orders)) as conn, conn:
            conn.execute("INSERT INTO ah_receipts (receipt_id, date_time, total, details_synced, first_seen) VALUES (?, ?, 0, 1, ?)", (receipt_id, when, when))
            conn.executemany(
                "INSERT INTO ah_receipt_items (receipt_id, position, product_id, name, quantity, price, amount) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(receipt_id, position, *line) for position, line in enumerate(lines)],
            )

    async def history(self, store="ah", query="", limit=50):
        return await prices.history(store, query, limit, today=TODAY)

    async def product(self, product_id, **kwargs):
        return next(p for p in (await self.history(**kwargs))["products"] if p["productId"] == product_id)


async def history_points(store: str = "ah") -> dict[str, list[tuple[str, float]]]:
    """`prices._history_points` zoals `_annotate` hem aanroept: met de bonregels van `_areceipt_rows` erbij, op dezelfde verbinding."""

    async def points(conn):
        return await prices._history_points(conn, store, TODAY, await prices._receipt_rows(conn, store, TODAY))

    return (await in_db(prices._connect, points))[0]


def offer(product_id, title, price=1.0, before=1.5, label="25% korting", **extra):
    return {"id": product_id, "title": title, "price": price, "priceBefore": before, "promoLabel": label, **extra}


def order_item(product_id, title, price, quantity=1, before=None, bonus=False):
    return {"productId": product_id, "title": title, "brand": "AH", "unitSize": "500 g", "quantity": quantity, "amount": price * quantity,
            "price": price, "priceBeforeBonus": before, "bonus": bonus, "taxonomy": "", "image": ""}


class RegularPriceTest(AppTestCase):
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
        with closing(db(prices)) as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM price_observations ORDER BY store, product_id, day")]

    def test_one_price_per_product_per_day_the_last_one_wins(self):
        products = [{"id": "1", "title": "AH Penne", "price": 1.19, "unitSize": "500 g"}, {"id": "2", "title": "Spaghetti", "price": 0.99}]
        self.assertEqual(run(prices.record("ah", products, now="2026-10-01T08:00:00+00:00")), 2)
        run(prices.record("ah", [{"id": "1", "title": "AH Penne", "price": 1.25}], now="2026-10-01T18:00:00+00:00"))
        run(prices.record("ah", [{"id": "1", "title": "AH Penne", "price": 1.25}], now="2026-10-02T08:00:00+00:00"))
        rows = self.rows()
        self.assertEqual([(r["product_id"], r["day"], r["price"]) for r in rows], [("1", "2026-10-01", 1.25), ("1", "2026-10-02", 1.25), ("2", "2026-10-01", 0.99)])
        self.assertEqual(rows[0]["unit_size"], "500 g")  # een latere zoekopdracht zonder pakmaat wist hem niet
        self.assertEqual(rows[0]["url"], None)

    def test_products_without_id_title_or_regular_price_are_skipped(self):
        found = [{"title": "Geen id", "price": 1}, {"id": "3", "price": 1}, {"id": "4", "title": "Bonus", "price": 1, "isBonus": True}, "rommel"]
        self.assertEqual(run(prices.record("ah", found)), 0)
        self.assertEqual(run(prices.record("nope", [{"id": "1", "title": "X", "price": 1}])), 0)
        self.assertEqual(self.rows(), [])

    def test_a_failure_never_breaks_the_search(self):
        with mock.patch.object(prices, "_connect", side_effect=OSError("schijf vol")), self.assertLogs(prices.log, "WARNING"):
            self.assertEqual(run(prices.record("ah", [{"id": "1", "title": "X", "price": 1}])), 0)
        search = mock.AsyncMock(return_value=[{"id": "1", "title": "X", "price": 1}])
        with mock.patch.object(prices, "_connect", side_effect=OSError("schijf vol")), self.assertLogs(prices.log, "WARNING"):
            self.assertEqual(run(prices.recorded("ah", search)("x", 5)), search.return_value)
        search.assert_called_once_with("x", 5)

    def test_the_wrapper_records_what_the_store_found(self):
        search = mock.AsyncMock(return_value=[{"webshopId": "9", "title": "Melk", "price": {"now": 1.09}, "images": [{"url": "https://img/9"}]}])
        self.assertEqual(run(prices.recorded("ah", search)("melk")), search.return_value)
        row = self.rows()[0]
        self.assertEqual((row["product_id"], row["price"], row["image"], row["source"]), ("9", 1.09, "https://img/9", "search"))


class HistoryTest(PricesCase):
    async def test_offers_regular_prices_and_purchases_come_together(self):
        await self.sync_offer("2026-10-05T05:00:00+00:00", offer("1", "AH Penne", 0.89, 1.19, validFrom="2026-10-05", validUntil="2026-10-11"))
        await prices.record("ah", [{"id": "1", "title": "AH Penne", "price": 1.25}], now="2026-10-15T08:00:00+00:00")
        await self.ah_order(101, "delivered", "2026-10-07", order_item(1, "AH Penne", 0.89, quantity=2, before=1.19, bonus=True))
        await self.ah_order(102, "open", "2026-10-22", order_item(1, "AH Penne", 1.25, quantity=5))  # nog niet geleverd: telt niet
        self.ah_receipt("R1", "2026-10-16T12:30:00", (1, "AH PENNE", 1, 1.25, 1.25))

        product = await self.product("1")
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

    async def test_a_product_you_only_bought_is_found_too(self):
        await self.ah_order(101, "delivered", "2026-09-01", order_item(7, "AH Volkoren spaghetti", 1.09))
        product = await self.product("7", query="spaghetti")
        self.assertEqual((product["timesOnOffer"], product["episodes"], product["onOfferNow"]), (0, [], False))
        self.assertEqual((product["title"], product["brand"], product["unitSize"]), ("AH Volkoren spaghetti", "AH", "500 g"))
        self.assertEqual(product["regular"], [{"from": "2026-09-01", "to": "2026-09-01", "price": 1.09}])

    async def test_a_receipt_name_finds_the_product_and_brings_its_other_data(self):
        await prices.record("ah", [{"id": "5", "title": "AH Basmati rijst", "price": 2.19}], now="2026-10-01T08:00:00+00:00")
        self.ah_receipt("R1", "2026-10-02T09:00:00", (5, "AH RIJST BASMATI", 2, 2.19, 4.38))
        product = await self.product("5", query="RIJST BASMATI")
        self.assertEqual(product["title"], "AH Basmati rijst")  # de zoekopdracht geeft een betere naam dan de bon
        self.assertEqual(len(product["regular"]), 1)

    async def test_receipt_lines_without_a_usable_id_are_linked_by_name(self):
        # Rundergehakt tien keer gekocht: twee keer bezorgd, de rest in de winkel. Op de bon staat geen id (of een onbekend id), wel de naam.
        await self.ah_order(101, "delivered", "2026-09-01", order_item(11, "AH Rundergehakt", 3.49))
        await self.ah_order(102, "delivered", "2026-09-15", order_item(11, "AH Rundergehakt", 3.49))
        for number in range(1, 7):
            self.ah_receipt(f"R{number}", f"2026-10-0{number}T12:00:00", (None, "AH RUNDERGEHAKT", 1, 3.59, 3.59))
        self.ah_receipt("R7", "2026-10-08T12:00:00", (0, "AH Rundergehakt", 1, 3.59, 3.59))
        self.ah_receipt("R8", "2026-10-09T12:00:00", (987654, "AH  RUNDERGEHAKT", 2, 3.59, 7.18))  # een id dat we niet kennen, wel de naam
        data = await self.history(query="rundergehakt")
        self.assertEqual(data["total"], 1)
        product = data["products"][0]
        self.assertEqual((product["productId"], product["title"], product["timesBought"]), ("11", "AH Rundergehakt", 10))
        self.assertEqual([p["source"] for p in product["purchases"]].count("receipt"), 8)

    async def test_a_linked_article_number_joins_the_product_from_the_orders(self):
        # Op de bon "AH BLOEMK" (afgekort) met artikelnummer 20960, in de bestelling "AH Bloemkool" met id 4183: een product (issue #178).
        await self.ah_order(101, "delivered", "2026-09-01", order_item(4183, "AH Bloemkool", 1.69))
        self.ah_receipt("R1", "2026-10-02T09:00:00", (20960, "AH BLOEMK", 2, 1.99, 3.98))
        self.ah_receipt("R2", "2026-10-09T09:00:00", (20960, "AH BLOEMK", 1, 1.85, 1.85))
        self.assertEqual(sorted(p["productId"] for p in (await self.history(query="bloem"))["products"]), ["20960", "4183"], "zonder koppeling: twee")
        with closing(db(ah_orders)) as conn, conn:
            conn.execute("INSERT INTO ah_receipt_products (receipt_product_id, product_id, title, checked_at) VALUES (20960, 4183, 'AH Bloemkool', 'x')")
        data = await self.history(query="bloem")
        self.assertEqual(data["total"], 1)
        product = data["products"][0]
        self.assertEqual((product["productId"], product["title"], product["timesBought"]), ("4183", "AH Bloemkool", 3))
        self.assertEqual([p["source"] for p in product["purchases"]], ["order", "receipt", "receipt"])
        self.assertEqual((await self.history(query="BLOEMK"))["total"], 1, "de naam op de bon vindt hem ook")

    async def test_a_receipt_id_that_is_also_the_id_of_another_product_is_not_trusted(self):
        # Het artikelnummer van "AH ROOKWORST" op de bon (627748) is ook het id van een pizza in de webwinkel (issue #178).
        await self.sync_offer("2026-11-02T05:00:00+00:00", offer("627748", "Dr. Oetker Ristorante pizza hawaii 3-pack", 8.80, 9.57))
        await self.ah_order(101, "delivered", "2026-09-01", order_item(55, "AH Rookworst", 2.79))
        self.ah_receipt("R1", "2025-12-06T18:57:00", (627748, "AH ROOKWORST", 1, None, 2.99))
        pizza = next(p for p in (await prices.history("ah", "ristorante", 50, today="2026-11-03"))["products"] if p["productId"] == "627748")
        self.assertEqual(pizza["timesBought"], 0)
        sausage = next(p for p in (await prices.history("ah", "rookworst", 50, today="2026-11-03"))["products"] if p["productId"] == "55")
        self.assertEqual((sausage["timesBought"], [p["source"] for p in sausage["purchases"]]), (2, ["receipt", "order"]))

    async def test_a_receipt_id_of_another_product_without_a_name_match_stays_its_own_product(self):
        # Zonder bestelling van rookworst valt de bonregel niet terug op het id van de pizza, maar wordt hij een eigen product.
        await self.sync_offer("2026-11-02T05:00:00+00:00", offer("627748", "Dr. Oetker Ristorante pizza hawaii 3-pack", 8.80, 9.57))
        self.ah_receipt("R1", "2025-12-06T18:57:00", (627748, "AH ROOKWORST", 1, None, 2.99))
        products = {p["productId"]: p for p in (await prices.history("ah", "", 50, today="2026-11-03"))["products"]}
        self.assertEqual(products["627748"]["timesBought"], 0)
        self.assertEqual(products["bon:ah rookworst"]["timesBought"], 1)

    def test_names_agree_on_a_shared_or_shortened_word(self):
        self.assertTrue(prices._names_agree("ah rijst basmati", ["ah basmati rijst"]))
        self.assertTrue(prices._names_agree("ah rundergeh", ["ah rundergehakt"]))
        self.assertFalse(prices._names_agree("ah rookworst", ["dr. oetker ristorante pizza hawaii 3-pack"]))
        self.assertFalse(prices._names_agree("ah ui", ["ah uien"]), "woorden van twee letters tellen niet")

    async def test_a_receipt_name_without_the_house_brand_finds_the_product(self):
        # Op de bon "BLOEMKOOL", in de bestelling "AH Bloemkool" (issue #178): zonder "AH" vooraan is het dezelfde naam.
        await self.ah_order(101, "delivered", "2026-09-01", order_item(4183, "AH Bloemkool", 1.69))
        self.ah_receipt("R1", "2026-10-02T09:00:00", (None, "BLOEMKOOL", 1, 1.99, 1.99))
        product = await self.product("4183", query="bloemkool")
        self.assertEqual((product["timesBought"], [p["source"] for p in product["purchases"]]), (2, ["order", "receipt"]))

    async def test_a_bare_name_that_fits_two_products_is_not_guessed(self):
        await self.ah_order(101, "delivered", "2026-09-01", order_item(1, "AH Bloemkool", 1.69), order_item(2, "Bloemkool", 1.49))
        self.ah_receipt("R1", "2026-10-02T09:00:00", (None, "BLOEMKOOL ", 1, 1.99, 1.99))
        ids = sorted(p["productId"] for p in (await self.history(query="bloemkool"))["products"])
        self.assertEqual(ids, ["1", "2"], "precies dezelfde naam gaat voor: Bloemkool")
        self.assertEqual((await self.product("2", query="bloemkool"))["timesBought"], 2)

    async def test_an_article_number_that_ah_did_not_find_changes_nothing(self):
        self.ah_receipt("R1", "2026-10-02T09:00:00", (43947, "DRAAGTAS", 1, 0.25, 0.25))
        with closing(db(ah_orders)) as conn, conn:
            conn.execute("INSERT INTO ah_receipt_products (receipt_product_id, product_id, title, checked_at) VALUES (43947, NULL, NULL, 'x')")
        self.assertEqual([p["productId"] for p in (await self.history(query="draagtas"))["products"]], ["43947"])

    async def test_a_linked_receipt_line_gets_the_web_shop_name_and_stays_findable_by_the_receipt_name(self):
        self.ah_receipt("R1", "2026-10-02T09:00:00", (77234, "RED BAND", 1, 1.99, 1.99), (511669, "RB PICOLIENT", 1, 1.49, 1.49))
        with closing(db(ah_orders)) as conn, conn:
            conn.executemany("INSERT INTO ah_receipt_products (receipt_product_id, product_id, title, checked_at) VALUES (?, ?, ?, 'x')",
                             [(77234, 547751, "Red Band Winegums original"), (511669, 4242, "Red Band Picolient")])
        titles = {p["productId"]: p["title"] for p in (await self.history(query="redband"))["products"]}
        self.assertEqual(titles, {"547751": "Red Band Winegums original", "4242": "Red Band Picolient"}, "redband vindt Red Band (spaties tellen niet)")
        self.assertEqual([p["productId"] for p in (await self.history(query="RB PICOLIENT"))["products"]], ["4242"], "de naam op de bon werkt ook")

    def test_a_search_looks_up_matching_unlinked_receipt_names_first(self):
        self.ah_receipt("R1", "2026-10-02T09:00:00", (501520, "REDBAND", 1, 1.99, 1.99), (20960, "BLOEMKOOL", 1, 1.99, 1.99))
        asked = []

        async def lookup(names: list[str]) -> None:
            asked.append(names)
            with closing(db(ah_orders)) as conn, conn:
                conn.execute("INSERT INTO ah_receipt_products (receipt_product_id, product_id, title, checked_at) VALUES (501520, 88624, 'Red Band Schatkistjes', 'x')")

        data = run(prices.history("ah", "redband", 50, today=TODAY, lookup=lookup))
        self.assertEqual(asked, [["REDBAND"]], "alleen de namen die bij de zoekterm passen")
        self.assertEqual([(p["productId"], p["title"]) for p in data["products"]], [("88624", "Red Band Schatkistjes")])
        run(prices.history("ah", "redband", 50, today=TODAY, lookup=lookup))
        self.assertEqual(len(asked), 1, "gekoppeld: niet opnieuw opzoeken")
        run(prices.history("ah", "", 50, today=TODAY, lookup=lookup))
        self.assertEqual(len(asked), 1, "zonder zoekterm niets opzoeken")

    def test_a_failing_lookup_leaves_the_receipt_name(self):
        self.ah_receipt("R1", "2026-10-02T09:00:00", (501520, "REDBAND", 1, 1.99, 1.99))
        data = run(prices.history("ah", "redband", 50, today=TODAY, lookup=lambda names: 1 / 0))
        self.assertEqual([p["title"] for p in data["products"]], ["REDBAND"])

    async def test_a_receipt_line_that_matches_nothing_is_still_found(self):
        self.ah_receipt("R1", "2026-10-02T09:00:00", (None, "AH VERSE KNOFLOOK", 1, 0.79, 0.79))
        product = (await self.history(query="knoflook"))["products"][0]
        self.assertEqual((product["productId"], product["title"], product["timesBought"], product["url"]), ("bon:ah verse knoflook", "AH VERSE KNOFLOOK", 1, ""))
        self.assertEqual((await self.product("bon:ah verse knoflook", query=""))["timesBought"], 1)

    async def test_purchases_that_are_not_fetched_yet_are_counted(self):
        await self.ah_order(101, "delivered", "2026-09-01")  # geen producten: items_synced blijft 0
        with closing(db(ah_orders)) as conn, conn:
            conn.execute("INSERT INTO ah_receipts (receipt_id, date_time, total, details_synced, first_seen) VALUES ('R9', '2026-10-01', 1, 0, 'x')")
        self.assertEqual((await self.history())["unsynced"], {"ah": {"orders": 1, "receipts": 1}})

    async def test_search_by_words_and_pack_size(self):
        async def record(pid: str, title: str, size: str) -> None:
            await prices.record("ah", [{"id": pid, "title": title, "price": 3.0, "unitSize": size}], now="2026-10-01T08:00:00+00:00")

        await record("1", "AH Rundergehakt", "500 g")
        await record("2", "AH Rundergehakt", "300 g")
        await record("3", "AH Half-om-half gehakt", "0,5 kg")
        await record("4", "AH Kipgehakt", "1 kg")
        await record("5", "AH Gehaktkruiden", "per stuk")
        self.ah_receipt("R1", "2026-10-02T09:00:00", (None, "SLAGER GEHAKT", 1, 4.0, 4.0), (None, "AH GEHAKT 300G", 1, 2.0, 2.0))
        async def found(query: str) -> list[str]:
            return sorted(p["productId"] for p in (await self.history(query=query))["products"])

        self.assertEqual(await found("Gehakt 500 gram"), ["1", "3", "bon:slager gehakt"])  # de bon zonder maat blijft erin, die van 300 g niet
        self.assertEqual(await found("500g gehakt"), await found("Gehakt 500 gram"))  # volgorde en schrijfwijze van de maat maken niet uit
        self.assertEqual(await found("gehakt 0,5 kg"), await found("Gehakt 500 gram"))
        self.assertEqual(await found("rundergehakt 300 gram"), ["2"])
        self.assertEqual(await found("gehakt"), ["1", "2", "3", "4", "5", "bon:ah gehakt 300g", "bon:slager gehakt"])
        self.assertEqual(await found("half om half"), ["3"])

    async def test_the_same_price_on_more_days_is_one_period_and_a_change_starts_a_new_one(self):
        for day, price in (("01", 1.0), ("02", 1.0), ("05", 1.0), ("06", 1.2), ("09", 1.0)):
            await prices.record("ah", [{"id": "1", "title": "Kaas", "price": price}], now=f"2026-10-{day}T08:00:00+00:00")
        self.assertEqual(
            (await self.product("1"))["regular"],
            [{"from": "2026-10-01", "to": "2026-10-05", "price": 1.0}, {"from": "2026-10-06", "to": "2026-10-06", "price": 1.2},
             {"from": "2026-10-09", "to": "2026-10-09", "price": 1.0}],
        )

    def test_on_one_day_the_search_beats_an_order_and_an_order_beats_an_offer(self):
        points = [("2026-10-01", 1.5, "offer"), ("2026-10-01", 1.4, "order"), ("2026-10-02", 1.5, "offer"), ("2026-10-02", 1.3, "search")]
        self.assertEqual(
            prices._regular_segments(points),
            [{"from": "2026-10-01", "to": "2026-10-01", "price": 1.4}, {"from": "2026-10-02", "to": "2026-10-02", "price": 1.3}],
        )

    async def test_picnic_lines_are_divided_by_the_quantity_and_cancelled_or_future_orders_are_left_out(self):
        line = {"id": "s1", "title": "Picnic penne", "unitSize": "500 g", "quantity": 3, "price": 2.67, "listPrice": 3.57}
        await in_db(
            picnic_orders._connect,
            lambda conn: picnic_orders._save_order(conn, {"id": "P1", "status": "COMPLETED", "deliveredStart": "2026-10-03T18:00:00+02:00"}, [line]),
            lambda conn: picnic_orders._save_order(conn, {"id": "P2", "status": "CANCELLED", "windowStart": "2026-10-04T18:00:00+02:00"}, [{**line, "price": 0.3}]),
            lambda conn: picnic_orders._save_order(conn, {"id": "P3", "status": "CURRENT", "windowStart": "2026-10-30T18:00:00+02:00"}, [{**line, "price": 0.6}]),
        )
        product = await self.product("s1", store="picnic")
        self.assertEqual([(p["date"], p["price"], p["regularPrice"]) for p in product["purchases"]], [("2026-10-03", 0.89, 1.19)])
        self.assertEqual(product["regular"], [{"from": "2026-10-03", "to": "2026-10-03", "price": 1.19}])

    async def test_newest_first_with_a_limit_and_the_total(self):
        for number in range(1, 6):
            await prices.record("ah", [{"id": str(number), "title": f"Product {number}", "price": 1}], now=f"2026-10-0{number}T08:00:00+00:00")
        data = await self.history(limit=2)
        self.assertEqual(([p["productId"] for p in data["products"]], data["total"]), (["5", "4"], 5))
        self.assertEqual((await self.history(query="product 3"))["total"], 1)
        self.assertEqual((await self.history(query="100%"))["total"], 0)  # % en _ zijn letters, geen jokers

    async def test_stores_are_kept_apart_and_an_unlinked_store_is_simply_empty(self):
        await prices.record("ah", [{"id": "1", "title": "Melk", "price": 1}])
        self.assertEqual(await self.history(store="jumbo"), {"products": [], "total": 0, "unsynced": {"jumbo": {"orders": 0, "receipts": 0}}})  # geen tabellen
        self.assertEqual((await prices.history(None, "melk", 10))["total"], 1)
        with self.assertRaises(ValueError):
            await prices.history("nope")


class RouteAndToolTest(PricesCase):
    async def test_the_route(self):
        await prices.record("ah", [{"id": "1", "title": "Kipfilet", "price": 5.49}])
        client = self.client_for(create_app())
        reply = (await client.get("/api/prices/history?store=ah&q=kip&limit=5")).get_json()
        self.assertEqual((reply["total"], reply["products"][0]["regularPrice"]), (1, 5.49))
        self.assertEqual((await client.get("/api/prices/history?store=nope")).status_code, 400)
        self.assertEqual((await client.get("/api/prices/history?limit=abc")).status_code, 200)

    async def test_one_product_by_id_skips_the_search_and_the_lookup(self):
        await prices.record("ah", [{"id": "1", "title": "Kipfilet", "price": 5.49}, {"id": "2", "title": "Kipdij", "price": 4.49}])
        lookup = mock.AsyncMock()
        reply = await prices.history("ah", "", 50, today=TODAY, lookup=lookup, product="2")
        self.assertEqual([p["productId"] for p in reply["products"]], ["2"])
        self.assertEqual(reply["total"], 1)
        lookup.assert_not_called()
        self.assertEqual((await prices.history("ah", "", 50, today=TODAY, product="nope"))["products"], [])
        client = self.client_for(create_app())
        self.assertEqual((await client.get("/api/prices/history?store=ah&product=1")).get_json()["products"][0]["regularPrice"], 5.49)

    async def test_the_check_list_of_the_week_menu_records_the_regular_price(self):
        found = [{"webshopId": "42", "title": "AH Penne", "price": {"now": 1.19}, "unitSize": "500 g", "images": [], "isBonus": False}]
        with mock.patch.object(ah, "search_products", return_value=found):
            reply = await self.client_for(create_app()).post("/api/ah/cart/preview", json={"items": [{"query": "penne", "quantity": 500, "unit": "g"}]})
        self.assertEqual(reply.status_code, 200)
        self.assertEqual((await prices.history("ah", "penne"))["products"][0]["regularPrice"], 1.19)

    async def test_the_mcp_tool_is_compact(self):
        await self.sync_offer("2026-10-05T05:00:00+00:00", offer("1", "AH Penne", 0.89, 1.19))
        await self.ah_order(101, "delivered", "2026-10-07", order_item(1, "AH Penne", 0.89, quantity=2, before=1.19, bonus=True))
        data = await mcp.TOOLS["get_price_history"]["handler"]({"query": "penne", "store": "ah"})
        product = data["products"][0]
        self.assertEqual((product["title"], product["timesOnOffer"], product["timesBought"], product["regularPrice"]), ("AH Penne", 1, 1, 1.19))
        self.assertEqual(product["purchases"], [{"date": "2026-10-07", "pricePerItem": 0.89, "quantity": 2, "source": "order"}])
        self.assertIn("hint", data)
        with self.assertRaises(mcp.ToolError):
            await mcp.TOOLS["get_price_history"]["handler"]({"store": "nope"})



class PublicObservedPriceTest(PricesCase):
    def observation(self, day: str, price: float, source: str = "public_observed_unknown", size: str | None = "1 l") -> None:
        with closing(db(prices)) as conn, conn:
            conn.execute(
                "INSERT INTO price_observations (store, product_id, day, source, title, brand, unit_size, url, price, seen_at) "
                "VALUES ('picnic', 'milk', ?, ?, 'Halfvolle melk', 'Picnic', ?, ?, ?, ?)",
                (day, source, size, f"https://example.org/prices/{day}", price, day + "T12:00:00+00:00"),
            )

    async def test_unknown_only_product_is_searchable_without_regular_offer_or_purchase_claims(self) -> None:
        self.observation("2026-10-02", 1.09)
        self.observation("2026-10-18", 1.19)
        data: dict = await self.history(store="picnic", query="melk 1 l")
        self.assertEqual(data["total"], 1)
        product: dict = data["products"][0]
        self.assertEqual(product["observed"], [
            {"date": "2026-10-02", "price": 1.09, "unitSize": "1 l", "sourceUrl": "https://example.org/prices/2026-10-02"},
            {"date": "2026-10-18", "price": 1.19, "unitSize": "1 l", "sourceUrl": "https://example.org/prices/2026-10-18"},
        ])
        self.assertEqual((product["regular"], product["episodes"], product["purchases"]), ([], [], []))
        for key in ("regularPrice", "regularPerUnit", "lowestRegular", "highestRegular", "regularSince", "lastPaid", "lowestPaid"):
            self.assertIsNone(product.get(key), key)
        self.assertEqual((product["timesBought"], product["timesOnOffer"], product["onOfferNow"]), (0, 0, False))
        self.assertEqual((product["title"], product["unitSize"], product["lastActivity"]), ("Halfvolle melk", "1 l", "2026-10-18"))
        self.assertEqual((await prices.history("picnic", product="milk", today=TODAY))["products"][0], product)
        self.assertEqual((await prices.history("picnic", bought=True, today=TODAY))["total"], 0)

    async def test_archive_source_is_not_labelled_as_a_product_link_at_the_store(self) -> None:
        self.observation("2026-10-01", 1.09)
        self.assertEqual((await self.product("milk", store="picnic"))["url"], "")

    async def test_mixed_sources_keep_confirmed_regular_prices_separate(self) -> None:
        self.observation("2026-10-01", 1.49, "search")
        self.observation("2026-10-02", 0.99)
        self.observation("2026-10-03", 1.59, "public_press_release")
        product: dict = await self.product("milk", store="picnic")
        self.assertEqual([point["price"] for point in product["observed"]], [0.99])
        self.assertEqual([point["price"] for point in product["regular"]], [1.49, 1.59])
        self.assertEqual((product["regularPrice"], product["lowestRegular"], product["regularSince"]), (1.59, 1.49, "2026-10-03"))

    async def test_regular_unit_price_uses_the_confirmed_prices_own_pack_size(self) -> None:
        self.observation("2026-10-01", 2.00, "search", size="1 l")
        self.observation("2026-10-02", 1.00, size="2 l")
        product: dict = await self.product("milk", store="picnic")
        self.assertEqual(product["regularPrice"], 2.00)
        self.assertEqual(product["unitSize"], "1 l")
        self.assertEqual(product["observed"][0]["unitSize"], "2 l")
        self.assertEqual(product["regularPerUnit"], {"value": 2.00, "unit": "l"})
        compact: dict = (await mcp.tool_get_price_history({"store": "picnic", "query": "melk"}))["products"][0]
        self.assertEqual(compact["regularPricePerUnit"], product["regularPerUnit"])

    async def test_missing_confirmed_pack_size_does_not_borrow_unknown_pack_size(self) -> None:
        self.observation("2026-10-01", 2.00, "search", size=None)
        self.observation("2026-10-02", 1.00, size="2 l")
        product: dict = await self.product("milk", store="picnic")
        self.assertEqual(product["regularPrice"], 2.00)
        self.assertIsNone(product["regularPerUnit"])
        self.assertIsNone(product["unitSize"])
        self.assertEqual(product["observed"][0]["unitSize"], "2 l")

    async def test_compare_fallback_uses_confirmed_observation_with_its_own_pack(self) -> None:
        self.observation("2026-10-01", 2.00, "public_press_release", size="1 l")
        self.observation("2026-10-02", 1.00, size="2 l")

        async def nothing(*_: object) -> list:
            return []

        with mock.patch.object(offers, "load", new_callable=mock.AsyncMock, return_value={"offers": []}), \
                mock.patch.object(prices, "_search_function", return_value=nothing):
            result: dict = await prices.compare("picnic", "milk")
        self.assertEqual((result["product"]["price"], result["product"]["unitSize"]), (2.00, "1 l"))
        self.assertEqual(result["product"]["unitPrice"], {"value": 2.00, "unit": "l"})
        self.assertEqual(result["candidates"][0]["price"], 2.00)
        self.assertEqual(result["candidates"][0]["unitSize"], "1 l")

    async def test_compare_fallback_rejects_unknown_only_observations(self) -> None:
        self.observation("2026-10-02", 1.00, size="2 l")
        with mock.patch.object(offers, "load", new_callable=mock.AsyncMock, return_value={"offers": []}):
            self.assertIsNone(await prices._known_product("picnic", "milk"))
            with self.assertRaises(ValueError):
                await prices.compare("picnic", "milk")

    async def test_unknown_prices_never_supply_a_regular_baseline_but_count_as_observed_history(self) -> None:
        self.observation("2026-10-01", 0.79)
        measured: set[str] = set()

        async def regulars(conn):
            return await prices._regulars_between(conn, "picnic", "2026-09-20", TODAY, measured=measured)

        self.assertEqual((await in_db(prices._connect, regulars))[0], {})
        self.assertEqual(measured, set())
        self.assertEqual((await history_points("picnic"))["milk"], [("2026-10-01", 0.79)])
        self.assertEqual(await prices.regular_before("picnic", ["milk"], TODAY), {})
        current: dict = offer("milk", "Halfvolle melk", price=0.69, before=None)
        await prices._annotate("picnic", [current], TODAY)
        self.assertEqual(current["verdict"]["kind"], "unknown")
        self.assertIsNone(current["verdict"]["lowestRegular"])
        self.assertIsNone(current["badge"])

    async def test_ordinary_products_always_include_an_empty_observed_series(self) -> None:
        self.observation("2026-10-01", 1.49, "search")
        self.assertEqual((await self.product("milk", store="picnic"))["observed"], [])

    async def test_api_and_mcp_expose_the_same_observed_entries(self) -> None:
        self.observation("2026-10-01", 1.09)
        client = self.client_for(create_app())
        api: dict = (await client.get("/api/prices/history?store=picnic&product=milk")).get_json()["products"][0]
        result: dict = (await mcp.tool_get_price_history({"store": "picnic", "query": "melk"}))["products"][0]
        self.assertEqual(result["observed"], api["observed"])
        self.assertEqual((result["regularPrices"], result["regularPrice"]), ([], None))


class UnitPriceTest(AppTestCase):
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
    async def annotate(self, *items, store="ah"):
        return {item["id"]: item for item in await prices.annotate(store, list(items), today=TODAY)}

    async def observe(self, day, product_id, title, price, size="500 g", brand="AH", store="ah"):
        await prices.record(store, [{"id": product_id, "title": title, "price": price, "unitSize": size, "brand": brand}], now=f"{day}T08:00:00+00:00")

    async def test_a_real_deal_is_far_below_the_lowest_regular_price_of_30_days(self):
        await self.observe("2026-10-10", "1", "AH Penne", 1.19)
        offer = (await self.annotate(deal("1", "AH Penne", 0.89, 1.19, "25% korting")))["1"]
        self.assertEqual({k: offer["verdict"][k] for k in ("kind", "percent", "lowestRegular", "inflated")},
                         {"kind": "good", "percent": 25, "lowestRegular": 1.19, "inflated": False})
        self.assertEqual(offer["unitPricePer"], {"value": 1.78, "unit": "kg"})

    async def test_a_price_before_that_was_raised_is_seen_through(self):
        await self.observe("2026-10-05", "1", "AH Kaas", 1.10)  # binnen 30 dagen: de gewone prijs was lager dan de prijs ervoor van de actie
        await self.observe("2026-09-01", "1", "AH Kaas", 0.90)  # ouder dan 30 dagen: telt niet
        verdict = (await self.annotate(deal("1", "AH Kaas", 1.12, 1.50, "nu 1,12")))["1"]["verdict"]
        self.assertEqual((verdict["kind"], verdict["lowestRegular"], verdict["inflated"]), ("none", 1.10, True))

    async def test_a_small_saving(self):
        self.assertEqual((await self.annotate(deal("1", "AH Rijst", 1.80, 2.00)))["1"]["verdict"]["kind"], "small")

    async def test_a_deal_with_a_minimum_uses_the_price_per_item(self):
        # AH geeft bij "2 voor 3,00" de gewone prijs als prijs en geen prijs ervoor.
        verdict = (await self.annotate(deal("1", "AH Hummus", 2.00, None, "2 voor 3,00")))["1"]["verdict"]
        self.assertEqual((verdict["kind"], verdict["percent"], verdict["lowestRegular"]), ("good", 25, 2.0))
        for label, expected in (("2e halve prijs", 25), ("1+1 gratis", 50)):
            self.assertEqual((await self.annotate(deal("2", "AH Soep", 2.00, 2.00, label)))["2"]["verdict"]["percent"], expected, label)

    async def test_ah_gives_the_regular_price_for_one_plus_one_free(self):
        # Zo staat "1+1 gratis" in de aanbiedingen van AH: de prijs is de gewone prijs, zonder prijs ervoor (wolph: 300 g rundergehakt).
        offer = (await self.annotate(deal("1", "AH Rundergehakt", 3.49, None, "1+1 gratis", size="300 g")))["1"]
        self.assertEqual((offer["unitPrice"], offer["unitPricePer"]), (1.75, {"value": 5.83, "unit": "kg"}))
        self.assertEqual((offer["verdict"]["kind"], offer["verdict"]["percent"], offer["verdict"]["lowestRegular"]), ("good", 50, 3.49))
        half = (await self.annotate(deal("2", "AH Soep", 2.00, None, "2e halve prijs")))["2"]
        self.assertEqual((half["unitPrice"], half["verdict"]["percent"]), (1.5, 25))
        unknown = (await self.annotate(deal("3", "AH Kaas", 2.00, None, "25% korting")))["3"]  # is 2,00 al verlaagd? Dat weten we niet.
        self.assertEqual((unknown["unitPrice"], unknown["verdict"]["kind"]), (None, "unknown"))

    async def test_the_bulk_pack_is_compared_with_the_price_per_item_of_one_plus_one(self):
        await self.observe("2026-10-15", "bulk", "AH Rundergehakt", 9.99, size="1000 g")  # €9,99/kg
        verdict = (await self.annotate(deal("1", "AH Rundergehakt", 3.49, None, "1+1 gratis", size="300 g")))["1"]["verdict"]  # €5,83/kg
        self.assertIsNone(verdict["cheaperPack"])
        verdict = (await self.annotate(deal("1", "AH Rundergehakt", 3.49, None, "2e halve prijs", size="300 g")))["1"]["verdict"]  # €8,73/kg
        self.assertIsNone(verdict["cheaperPack"])
        verdict = (await self.annotate(deal("1", "AH Rundergehakt", 3.49, 3.69, "nu 3,49", size="300 g")))["1"]["verdict"]  # €11,63/kg
        self.assertEqual(verdict["cheaperPack"]["productId"], "bulk")

    async def test_without_a_regular_price_or_a_price_per_item_there_is_no_verdict(self):
        verdict = (await self.annotate(deal("p1", "Picnic yoghurt", 1.20, None, "20% korting"), store="picnic"))["p1"]["verdict"]
        self.assertEqual((verdict["kind"], verdict["percent"]), ("unknown", None))

    async def test_often_on_offer_and_the_lowest_offer_price_in_a_year(self):
        weeks = ("2026-01-05", "2026-03-02", "2026-04-27", "2026-06-22", "2026-08-17")
        for start in weeks:
            end = (prices.date.fromisoformat(start) + prices.timedelta(days=6)).isoformat()
            await self.sync_offer(f"{start}T05:00:00+00:00", offer("1", "AH Pindakaas", 2.49, 2.99, "nu 2,49", validFrom=start, validUntil=end))
            await self.sync_offer(f"{end}T23:00:00+00:00")
        await self.sync_offer("2026-10-19T05:00:00+00:00", offer("1", "AH Pindakaas", 1.99, 2.99, "nu 1,99", validFrom="2026-10-19", validUntil="2026-10-25"))
        annotated = (await self.annotate(deal("1", "AH Pindakaas", 1.99, 2.99, "nu 1,99")))["1"]
        verdict = annotated["verdict"]
        self.assertEqual((verdict["timesThisYear"], verdict["often"]), (6, True))
        self.assertEqual(annotated["priceLabel"]["kind"], "lowest_6m")
        higher = (await self.annotate(deal("1", "AH Pindakaas", 2.79, 2.99, "nu 2,79")))["1"]
        self.assertEqual(higher["priceLabel"]["kind"], "lower_before")

    def test_the_price_label_ladder(self):
        cases = [  # (punten, effectieve prijs, verwachte trede)
            ([("2025-09-20", 2.49), ("2026-03-01", 2.29), ("2026-09-01", 2.19)], 1.99, "lowest_ever"),
            ([("2025-09-20", 1.89), ("2026-03-01", 2.29), ("2026-09-01", 2.19)], 1.99, "lowest_12m"),
            ([("2025-09-20", 2.49), ("2026-03-01", 1.89), ("2026-09-01", 2.19)], 1.99, "lowest_6m"),
            ([("2026-07-12", 2.49), ("2026-09-01", 2.19)], 1.99, "lowest_seen"),
            ([("2026-07-12", 1.89), ("2026-09-01", 2.19)], 1.99, "lower_before"),
            ([("2026-10-10", 2.49)], 1.99, None),
        ]
        for points, effective, kind in cases:
            self.assertEqual(prices.price_label(effective, points, TODAY)["kind"], kind, points)

    def test_the_price_label_carries_the_price_the_month_and_the_coverage(self):
        label = prices.price_label(1.99, [("2026-06-03", 1.89), ("2026-09-01", 2.29)], TODAY)
        self.assertEqual(label, {"kind": "lower_before", "price": 1.89, "month": "2026-06", "coveredSince": "2026-06-03"})
        # Een jaar dekking en een lagere prijs van langer dan een jaar geleden: wel de laagste in 12 maanden, niet ooit.
        self.assertEqual(prices.price_label(1.99, [("2025-06-03", 1.89), ("2026-03-01", 2.29)], TODAY)["kind"], "lowest_12m")
        seen = prices.price_label(1.99, [("2026-08-12", 2.49), ("2026-09-01", 2.19)], TODAY)
        self.assertEqual(seen, {"kind": "lowest_seen", "price": None, "month": "2026-08", "coveredSince": "2026-08-12"})
        empty = {"kind": None, "price": None, "month": None, "coveredSince": None}
        self.assertEqual(prices.price_label(1.99, [], TODAY), empty)
        self.assertEqual(prices.price_label(None, [("2026-08-12", 2.49)], TODAY)["kind"], None)

    def test_a_lower_price_that_came_back_names_its_latest_month(self):
        label = prices.price_label(1.99, [("2025-06-03", 1.89), ("2026-02-10", 1.89), ("2026-03-01", 2.29)], TODAY)
        self.assertEqual((label["price"], label["month"]), (1.89, "2026-02"))

    def test_one_old_purchase_is_not_the_lowest_price_ever(self):
        # Eén aankoop uit 2022 geeft geen "laagste prijs ooit": zonder meting in het afgelopen jaar valt de ladder terug op
        # "laagste sinds", en "laagste in 12 maanden" heeft net zo goed een meting in dat jaar nodig.
        label = prices.price_label(2.50, [("2022-05-31", 2.59)], TODAY)
        self.assertEqual((label["kind"], label["month"]), ("lowest_seen", "2022-05"))
        recent = prices.price_label(2.50, [("2022-05-31", 2.59), ("2026-06-01", 2.79)], TODAY)
        self.assertEqual(recent["kind"], "lowest_ever")

    def test_a_half_cent_counts_as_equal(self):
        self.assertEqual(prices.price_label(1.99, [("2025-06-03", 1.994), ("2026-03-01", 2.29)], TODAY)["kind"], "lowest_ever")

    async def test_history_points_come_from_offers_prices_and_purchases_and_skip_the_running_offer(self):
        await self.sync_offer("2026-01-05T05:00:00+00:00", offer("1", "AH Pindakaas", 2.49, 2.99, "nu 2,49", validFrom="2026-01-05", validUntil="2026-01-11"))
        await self.sync_offer("2026-01-11T23:00:00+00:00")
        await self.observe("2026-05-01", "1", "AH Pindakaas", 2.99)
        await self.ah_order(101, "delivered", "2026-07-07", order_item(1, "AH Pindakaas", 2.79, quantity=2, before=2.99, bonus=True))
        await self.ah_order(102, "delivered", "2026-08-07", order_item(1, "AH Pindakaas", 0.0, quantity=1))  # gratis: telt niet
        await self.sync_offer("2026-10-19T05:00:00+00:00", offer("1", "AH Pindakaas", 1.99, 2.99, "nu 1,99", validFrom="2026-10-19", validUntil="2026-10-25"))
        points = (await history_points())["1"]
        self.assertEqual(points, [("2026-01-05", 2.49), ("2026-05-01", 2.99), ("2026-07-07", 2.79)])

    async def test_history_points_count_receipts_and_imported_purchases_but_not_free_or_unknown(self):
        await self.observe("2026-05-01", "1", "AH Pindakaas", 2.99)
        # Een bon: het artikelnummer op de bon is het id van het product (bekend uit de bewaarde prijzen), de prijs per stuk telt.
        self.ah_receipt("R1", "2026-06-10T10:00:00", (1, "AH PINDAKAAS", 2, 2.79, 5.58), (1, "AH PINDAKAAS", 1, None, 0.0), (1, "AH PINDAKAAS", 1, 0.0, 0.0))
        with closing(db(price_import)) as conn, conn:
            conn.executemany(
                "INSERT INTO imported_purchases (store, order_ref, position, day, title, quantity, paid, regular, order_total, imported_at) VALUES ('ah', ?, ?, ?, 'AH Pindakaas', 1, ?, NULL, 9.99, 'x')",
                [("M1", 0, "2026-07-15", 2.59), ("M1", 1, "2026-07-15", None), ("M2", 0, "2026-08-15", 0.0)],
            )
        points = (await history_points())["1"]
        self.assertEqual(points, [("2026-05-01", 2.99), ("2026-06-10", 2.79), ("2026-07-15", 2.59)])

    async def test_one_product_by_id_with_only_bought_follows_the_purchases(self):
        await self.observe("2026-05-01", "1", "AH Pindakaas", 2.99)
        await self.observe("2026-05-01", "2", "AH Hagelslag", 1.99)
        await self.ah_order(101, "delivered", "2026-07-07", order_item(1, "AH Pindakaas", 2.79))
        bought = await prices.history("ah", "", 50, today=TODAY, bought=True, product="1")
        self.assertEqual([p["productId"] for p in bought["products"]], ["1"])
        self.assertEqual((await prices.history("ah", "", 50, today=TODAY, bought=True, product="2"))["products"], [])
        self.assertEqual([p["productId"] for p in (await prices.history("ah", "", 50, today=TODAY, product="2"))["products"]], ["2"])

    async def test_a_failing_history_ladder_keeps_the_verdict_and_the_badge(self):
        await self.observe("2026-10-10", "1", "AH Penne", 1.19)
        with mock.patch.object(prices, "_history_points", new_callable=mock.AsyncMock, side_effect=RuntimeError("stuk")):
            annotated = (await self.annotate(deal("1", "AH Penne", 0.89, 1.19, "nu 0,89")))["1"]
        self.assertEqual(annotated["verdict"]["kind"], "good")
        self.assertEqual(annotated["badge"]["source"], "history")
        self.assertEqual(annotated["priceLabel"]["kind"], None)

    async def test_a_bundle_episode_counts_with_its_price_per_item(self):
        await self.sync_offer("2026-03-02T05:00:00+00:00", offer("1", "AH Soep", 2.00, None, "1+1 gratis", validFrom="2026-03-02", validUntil="2026-03-08"))
        await self.sync_offer("2026-03-08T23:00:00+00:00")
        self.assertEqual((await history_points())["1"], [("2026-03-02", 1.0)])

    async def test_annotate_sets_the_price_label_and_drops_best_in_year(self):
        await self.sync_offer("2026-01-05T05:00:00+00:00", offer("1", "AH Pindakaas", 2.49, 2.99, "nu 2,49", validFrom="2026-01-05", validUntil="2026-01-11"))
        await self.sync_offer("2026-01-11T23:00:00+00:00")
        annotated = (await self.annotate(deal("1", "AH Pindakaas", 1.99, 2.99, "nu 1,99")))["1"]
        # Dekking vanaf 5 januari (288 dagen), maar geen meetpunt in de laatste zes maanden: dan "laagste sinds januari".
        self.assertEqual((annotated["priceLabel"]["kind"], annotated["priceLabel"]["month"]), ("lowest_seen", "2026-01"))
        self.assertNotIn("bestInYear", annotated["verdict"])
        self.assertEqual((await self.annotate(deal("9", "AH Nieuw", 1.00, 1.50, "nu 1,00")))["9"]["priceLabel"]["kind"], None)

    async def test_the_badge_takes_the_measured_difference_and_says_where_it_comes_from(self):
        await self.observe("2026-10-10", "1", "AH Penne", 1.19)
        badge = (await self.annotate(deal("1", "AH Penne", 0.89, 1.19, "25% korting")))["1"]["badge"]
        self.assertEqual(badge, {"percent": 25, "source": "history", "claimed": 25, "inflated": False})

    async def test_without_history_the_badge_is_the_claim_of_the_store(self):
        badge = (await self.annotate(deal("1", "AH Rijst", 1.50, 2.00, "25% korting")))["1"]["badge"]
        self.assertEqual(badge, {"percent": 25, "source": "store", "claimed": 25, "inflated": False})

    async def test_the_price_before_of_an_earlier_offer_is_a_claim_and_not_a_measurement(self):
        await self.sync_offer("2026-10-05T05:00:00+00:00", offer("1", "AH Rijst", 1.50, 2.00, "25% korting", validFrom="2026-10-05", validUntil="2026-10-11"))
        await self.sync_offer("2026-10-11T23:00:00+00:00")
        self.assertEqual((await self.annotate(deal("1", "AH Rijst", 1.50, 2.00, "25% korting")))["1"]["badge"]["source"], "store")
        await self.ah_order(101, "delivered", "2026-10-12", order_item(1, "AH Rijst", 2.00, quantity=1))
        self.assertEqual((await self.annotate(deal("1", "AH Rijst", 1.50, 2.00, "25% korting")))["1"]["badge"]["source"], "history")

    async def test_an_inflated_price_before_gives_a_lower_badge_than_the_claim(self):
        await self.observe("2026-10-05", "1", "AH Kaas", 1.10)
        badge = (await self.annotate(deal("1", "AH Kaas", 0.88, 1.50, "nu 0,88")))["1"]["badge"]
        self.assertEqual((badge["percent"], badge["claimed"], badge["inflated"], badge["source"]), (20, 41, True, "history"))

    async def test_the_claim_of_a_bundle_deal_follows_the_mechanic(self):
        self.assertEqual((await self.annotate(deal("1", "AH Soep", 2.00, None, "1+1 gratis")))["1"]["badge"]["claimed"], 50)
        self.assertEqual((await self.annotate(deal("2", "AH Hummus", 2.00, None, "2 voor 3,00")))["2"]["badge"]["claimed"], 25)
        self.assertEqual((await self.annotate(deal("3", "AH Thee", 2.00, None, "2e halve prijs")))["3"]["badge"]["claimed"], 25)

    async def test_without_a_verdict_there_is_no_badge(self):
        self.assertIsNone((await self.annotate(deal("p1", "Picnic yoghurt", 1.20, None, "20% korting"), store="picnic"))["p1"]["badge"])

    async def test_a_zero_difference_keeps_a_badge_of_zero(self):
        await self.observe("2026-10-10", "1", "AH Kaas", 1.10)
        badge = (await self.annotate(deal("1", "AH Kaas", 1.12, 1.50, "nu 1,12")))["1"]["badge"]
        self.assertEqual((badge["percent"], badge["inflated"]), (0, True))

    async def test_a_bigger_pack_that_is_cheaper_per_kilo(self):
        await self.observe("2026-10-15", "big", "AH Zalmfilet", 7.00, size="500 g")  # €14/kg
        await self.observe("2026-10-15", "other", "Kerstmis Zalmfilet", 5.00, size="500 g", brand="Vishandel")  # ander merk: telt niet
        verdict = (await self.annotate(deal("small", "AH Zalmfilet 2 stuks", 4.00, 4.50, "nu 4,00", size="250 g")))["small"]["verdict"]  # €16/kg
        self.assertEqual(verdict["cheaperPack"], {"productId": "big", "title": "AH Zalmfilet", "unitSize": "500 g", "price": 7.0,
                                                  "unitPrice": {"value": 14.0, "unit": "kg"}, "onOffer": False})
        cheap = (await self.annotate(deal("small", "AH Zalmfilet 2 stuks", 3.00, 4.50, "nu 3,00", size="250 g")))["small"]["verdict"]  # €12/kg
        self.assertIsNone(cheap["cheaperPack"])

    def test_a_failure_leaves_the_offers_as_they_were(self):
        items = [deal("1", "AH Penne", 0.89, 1.19, "25% korting")]
        with mock.patch.object(prices, "_connect", side_effect=OSError("kapot")), self.assertLogs(prices.log, "WARNING"):
            self.assertIs(run(prices.annotate("ah", items, today=TODAY)), items)
        self.assertNotIn("verdict", items[0])


def line(product_id, price, bonus=0, before=None, quantity=1):
    """Een regel van een AH-bestelling, zoals `ah_orders._save_order` die bewaart."""
    return order_item(product_id, f"Product {product_id}", price, quantity=quantity, before=before, bonus=bool(bonus))


async def store_order(order_id, delivery_date, items, kind="delivered"):
    await in_db(ah_orders._connect, lambda conn: ah_orders._save_order(conn, {"id": order_id, "deliveryDate": delivery_date}, kind, items))


class RegularPricesTest(PricesCase):
    NOW = "2026-09-22T00:00:00+00:00"

    async def observe(self, day, product_id, title, price, store="ah"):
        await prices.record(store, [{"id": product_id, "title": title, "price": price, "unitSize": "500 g", "brand": "AH"}], now=f"{day}T08:00:00+00:00")

    def rows(self, sql):
        with closing(db(ah_orders)) as conn:
            return [dict(row) for row in conn.execute(sql)]

    def checked(self, table="ah_order_items"):
        return {str(row["product_id"]): row["regular_checked_at"] for row in self.rows(f"SELECT product_id, regular_checked_at FROM {table}")}

    async def test_the_lowest_regular_price_of_the_30_days_before_the_purchase_day(self):
        await self.observe("2026-09-01", "1", "AH Penne", 1.39)  # 20 dagen voor de aankoop
        await self.observe("2026-09-10", "1", "AH Penne", 1.19)
        await self.observe("2026-07-01", "1", "AH Penne", 0.99)  # ouder dan 30 dagen: telt niet
        self.assertEqual(await prices.regular_before("ah", ["1"], "2026-09-21"), {"1": 1.19})

    async def test_without_a_measurement_in_the_window_there_is_no_regular_price(self):
        await self.observe("2026-07-01", "1", "AH Penne", 0.99)
        self.assertEqual(await prices.regular_before("ah", ["1"], "2026-09-21"), {})

    async def test_an_earlier_order_and_the_price_before_of_an_offer_count_but_the_purchase_day_itself_does_not(self):
        await store_order(1, "2026-09-05", [line("1", price=1.29)])  # een eerdere bestelling: de gewone prijs die je toen betaalde
        await store_order(2, "2026-09-21", [line("2", price=0.50)])  # dezelfde dag: de aankoop zelf is geen gewone prijs ervoor
        await self.sync_offer("2026-09-12T08:00:00+00:00", offer("3", "AH Kaas", price=2.0, before=2.49))
        found = await prices.regular_before("ah", ["1", "2", "3"], "2026-09-21")
        self.assertEqual(found, {"1": 1.29, "3": 2.49})

    async def test_fill_writes_the_column_and_marks_lines_without_a_price_as_checked(self):
        await self.observe("2026-09-10", "1", "AH Penne", 1.19)
        await store_order(order_id=1, delivery_date="2026-09-21", items=[line("1", price=0.89, bonus=1, before=1.39), line("2", price=2.0)])
        self.assertEqual(await prices.fill_regular_prices("ah", now="2026-09-22T00:00:00+00:00"), 2)
        # het id staat als getal in ah_order_items, vandaar str()
        rows = {str(r["product_id"]): r for r in self.rows("SELECT product_id, regular_price, regular_checked_at FROM ah_order_items")}
        self.assertEqual(rows["1"]["regular_price"], 1.19)
        self.assertIsNone(rows["2"]["regular_price"])
        self.assertTrue(rows["2"]["regular_checked_at"])

    async def test_a_receipt_line_gets_the_regular_price_of_its_linked_webshop_product(self):
        await self.observe("2026-09-10", "1", "AH Penne", 1.19)
        self.ah_receipt("R1", "2026-09-21T10:00:00", (555, "PENNE", 1, 0.89, 0.89), (777, "ONBEKEND", 1, 2.0, 2.0))
        with closing(db(ah_orders)) as conn, conn:
            conn.execute("INSERT INTO ah_receipt_products (receipt_product_id, product_id, title, checked_at) VALUES (555, 1, 'AH Penne', 'x')")
        self.assertEqual(await prices.fill_regular_prices("ah", now=self.NOW), 2)
        rows = {r["name"]: r for r in self.rows("SELECT name, regular_price, regular_checked_at FROM ah_receipt_items")}
        self.assertEqual(rows["PENNE"]["regular_price"], 1.19)
        self.assertIsNone(rows["ONBEKEND"]["regular_price"])
        self.assertTrue(rows["ONBEKEND"]["regular_checked_at"])

    async def test_a_receipt_line_you_linked_yourself_gets_the_regular_price_of_that_product(self):
        # De automatische koppeling zegt product 2, jij koppelde de regel aan product 1 (#238): je eigen koppeling gaat voor.
        await self.observe("2026-09-10", "1", "AH Penne", 1.19)
        await self.observe("2026-09-10", "2", "AH Spaghetti", 2.49)
        self.ah_receipt("R1", "2026-09-21T10:00:00", (555, "PENNE", 1, 0.89, 0.89), (777, "FUSILLI", 1, 0.99, 0.99))
        with closing(db(ah_orders)) as conn, conn:
            conn.execute("INSERT INTO ah_receipt_products (receipt_product_id, product_id, title, checked_at) VALUES (555, 2, 'AH Spaghetti', 'x')")
        await product_links.save("ah", "PENNE", {"productId": "1", "title": "AH Penne"}, article=555, now=self.NOW)
        await product_links.save("ah", "FUSILLI", {"productId": "1", "title": "AH Penne"}, now=self.NOW)  # op naam, de regel heeft geen koppeling
        self.assertEqual(await prices.fill_regular_prices("ah", now=self.NOW), 2)
        rows = {r["name"]: r["regular_price"] for r in self.rows("SELECT name, regular_price FROM ah_receipt_items")}
        self.assertEqual(rows, {"PENNE": 1.19, "FUSILLI": 1.19})

    async def test_a_jumbo_order_line_gets_its_regular_price(self):
        from mealieshopper import jumbo_orders

        await self.observe("2026-09-10", "J1", "Jumbo Penne", 1.09, store="jumbo")
        await in_db(jumbo_orders._connect, lambda conn: jumbo_orders._save_order(conn, {"id": "J-1", "deliveryDate": "2026-09-21"}, [{"productId": "J1", "title": "Jumbo Penne", "quantity": 1, "price": 0.79}]))
        self.assertEqual(await prices.fill_regular_prices("jumbo", now=self.NOW), 1)
        self.assertEqual(self.rows("SELECT regular_price FROM jumbo_order_items")[0]["regular_price"], 1.09)
        self.assertEqual(await prices.fill_regular_prices("ah", now=self.NOW), 0)  # AH heeft hier niets: de jumbo-regels tellen niet mee

    async def test_a_second_fill_only_refreshes_recent_purchases_checked_more_than_a_day_ago(self):
        await store_order(1, "2026-09-21", [line("1", price=1.0)])
        await store_order(2, "2026-01-10", [line("2", price=1.0)])  # ouder dan 60 dagen
        self.assertEqual(await prices.fill_regular_prices("ah", now=self.NOW), 2)
        self.assertEqual(await prices.fill_regular_prices("ah", now="2026-09-22T12:00:00+00:00"), 0)  # nog geen dag geleden
        await self.observe("2026-09-15", "1", "AH Penne", 1.19)  # een meting die later binnenkwam
        self.assertEqual(await prices.fill_regular_prices("ah", now="2026-09-24T00:00:00+00:00"), 1)
        self.assertEqual(self.rows("SELECT regular_price FROM ah_order_items WHERE order_id = 1")[0]["regular_price"], 1.19)
        self.assertEqual(self.checked()["2"], self.NOW)  # de oude bestelling blijft zoals hij was

    async def test_the_limit_takes_the_newest_purchases_first(self):
        await store_order(1, "2026-09-01", [line("1", price=1.0)])
        await store_order(2, "2026-09-20", [line("2", price=1.0)])
        self.assertEqual(await prices.fill_regular_prices("ah", limit=1, now=self.NOW), 1)
        self.assertEqual(self.checked(), {"1": None, "2": self.NOW})

    async def test_given_purchases_only_their_lines_are_filled_whatever_their_age(self):
        await store_order(1, "2025-01-10", [line("1", price=1.0)])  # oud, maar gevraagd: wordt gevuld
        await store_order(2, "2026-09-20", [line("2", price=1.0)])
        self.ah_receipt("R1", "2026-09-21T10:00:00", (555, "PENNE", 1, 0.89, 0.89))
        self.assertEqual(await prices.fill_regular_prices("ah", order_ids=[1], now=self.NOW), 1)
        self.assertEqual(self.checked(), {"1": self.NOW, "2": None})
        self.assertIsNone(self.rows("SELECT regular_checked_at FROM ah_receipt_items")[0]["regular_checked_at"])
        self.assertEqual(await prices.fill_regular_prices("ah", order_ids=[], receipt_ids=["R1"], limit=0, now=self.NOW), 1)
        self.assertEqual(self.rows("SELECT regular_checked_at FROM ah_receipt_items")[0]["regular_checked_at"], self.NOW)
        self.assertEqual(await prices.fill_regular_prices("ah", order_ids=[1], now=self.NOW), 0)  # net bepaald: niet opnieuw

    async def test_the_nightly_round_reports_lines_and_stays_quiet_without_work(self):
        self.assertIsNone(await prices.fill_regular_prices_nightly())
        await store_order(1, "2026-09-21", [line("1", price=1.0)])
        self.assertEqual(await prices.fill_regular_prices_nightly(), {"lines": 1})
        with mock.patch.object(prices, "fill_regular_prices", new_callable=mock.AsyncMock, side_effect=RuntimeError("kapot")), self.assertLogs("mealieshopper.prices", "ERROR"):
            self.assertIsNone(await prices.fill_regular_prices_nightly())


class OffersRouteVerdictTest(PricesCase):
    async def test_the_offers_carry_a_verdict_and_a_new_price_refreshes_the_cache(self):
        await self.sync_offer("2026-11-02T05:00:00+00:00", offer("1", "AH Penne", 0.89, 1.19, "25% korting", unitSize="500 g"))
        client = self.client_for(create_app())
        first = await client.get("/api/offers?store=ah")
        item = first.get_json()["offers"][0]
        self.assertEqual((item["verdict"]["kind"], item["unitPricePer"]["unit"]), ("good", "kg"))
        self.assertEqual((await client.get("/api/offers?store=ah", headers={"If-None-Match": first.headers["ETag"]})).status_code, 304)
        await prices.record("ah", [{"id": "9", "title": "AH Spaghetti", "price": 1.0}])
        self.assertEqual((await client.get("/api/offers?store=ah", headers={"If-None-Match": first.headers["ETag"]})).status_code, 200)


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

    async def test_pack_sizes_side_by_side_cheapest_first(self):
        await self.sync_offer("2026-11-02T05:00:00+00:00", offer("1", "AH Penne", 0.89, 1.19, "25% korting", brand="AH", unitSize="500 g"))
        with mock.patch.object(ah, "search_products", return_value=self.found()) as search:
            data = await prices.compare("ah", "1")
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
        self.assertEqual((await prices.history("ah", "voordeelverpakking"))["products"][0]["regularPrice"], 1.99)  # de zoekopdracht is bewaard

    def test_another_variant_is_not_the_same_product(self):
        self.assertTrue(prices._same_product("ah penne", "AH Penne voordeelverpakking"))
        self.assertFalse(prices._same_product("ah penne", "AH Biologisch Penne"))
        self.assertFalse(prices._same_product("", "AH Penne"))

    def test_a_cheaper_bigger_pack_is_marked(self):
        run(prices.record("ah", [{"id": "1", "title": "AH Penne", "price": 1.19, "unitSize": "500 g", "brand": "AH"}]))
        with mock.patch.object(ah, "search_products", return_value=self.found()):
            data = run(prices.compare("ah", "1"))
        self.assertEqual([c["id"] for c in data["candidates"] if c["cheaper"]], ["2"])  # €1,99/kg tegen €2,38/kg

    async def test_one_plus_one_free_is_compared_per_item(self):
        await self.sync_offer("2026-11-02T05:00:00+00:00", offer("1", "AH Rundergehakt", 3.49, None, "1+1 gratis", brand="AH", unitSize="300 g"))
        with mock.patch.object(ah, "search_products", return_value=[]):
            data = await prices.compare("ah", "1")
        self.assertEqual((data["product"]["price"], data["product"]["unitPrice"]), (1.75, {"value": 5.83, "unit": "kg"}))

    def test_same_kind_and_same_brand_for_the_filters(self):
        run(prices.record("ah", [{"id": "1", "title": "AH Rundergehakt", "price": 3.49, "unitSize": "300 g", "brand": "AH"}]))
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
            data = run(prices.compare("ah", "1"))
        search.assert_called_once_with("AH Rundergehakt", 30)  # genoeg resultaten voor de grote verpakking
        self.assertEqual(data["kind"], "rundergehakt")
        flags = {c["id"]: (c["sameKind"], c["sameBrand"], c["onOffer"]) for c in data["candidates"]}
        self.assertEqual(flags, {
            "1": (True, True, False), "2": (True, True, False), "3": (True, True, False), "4": (True, False, True),
            "5": (False, True, False), "6": (False, True, False),
        })

    async def test_the_route_refuses_an_unknown_product(self):
        client = self.client_for(create_app())
        self.assertEqual((await client.get("/api/prices/compare?store=ah&id=404")).status_code, 400)
        self.assertEqual((await client.get("/api/prices/compare?store=nope&id=1")).status_code, 400)

    async def test_lidl_and_aldi_cannot_be_searched(self):
        """Lidl en Aldi hebben geen webwinkel: vergelijken en koppelen zoeken bij de winkel, dus dat kan daar niet (400, geen verzoek)."""
        with served(side_effect=AssertionError("geen verzoek naar buiten")):
            for store in ("lidl", "aldi"):
                with self.assertRaises(ValueError) as caught:
                    await prices.compare(store, "1")
                self.assertIn("alleen", str(caught.exception))
                with self.assertRaises(ValueError):
                    await prices.link_candidates(store, "melk")
            client = self.client_for(create_app())
            reply = await client.get("/api/prices/compare?store=lidl&id=1")
            self.assertEqual(reply.status_code, 400)
            self.assertIn("Lidl", reply.get_json()["error"])
            reply = await client.get("/api/product-links/search?store=aldi&q=melk")
            self.assertEqual(reply.status_code, 400)
            self.assertIn("Aldi", reply.get_json()["error"])


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

    async def preview(self, compare, quantity=500, unit="g"):
        body = {"items": [{"query": "rundergehakt", "quantity": quantity, "unit": unit}]}
        if compare:
            body["compare"] = True
        with mock.patch.object(ah, "search_products", side_effect=lambda query, limit: self.found()[:limit]) as search:
            reply = await self.client_for(create_app()).post("/api/ah/cart/preview", json=body)
        self.assertEqual(reply.status_code, 200)
        return reply.get_json()["items"][0]["candidates"], search

    async def test_the_week_menu_shows_only_the_same_product_or_cheaper_per_unit(self):
        candidates, search = await self.preview(compare=True)
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

    async def test_searching_yourself_shows_the_first_five_as_before(self):
        candidates, search = await self.preview(compare=False)
        search.assert_called_once_with("rundergehakt", 5)
        self.assertEqual([c["productId"] for c in candidates], ["300", "bio", "500", "175", "green"])
        self.assertEqual(candidates[1]["unitPrice"], {"value": 19.96, "unit": "kg"})  # de prijs per eenheid staat er wel bij
        self.assertNotIn("same", candidates[1])

    async def test_one_plus_one_counts_per_item_in_the_check_list(self):
        one_plus_one = {"label": "1+1 gratis", "mechanism": "1+1 gratis"}  # AH geeft de gewone prijs en geen prijs ervoor
        self.assertEqual(prices.per_item_price({"price": 3.49, "deal": {"label": "1+1 gratis", "unitPrice": None, "validity": "valid"}}), 1.75)
        self.assertEqual(prices.per_item_price({"price": 3.49, "deal": {"label": "1+1 gratis", "unitPrice": None, "validity": "expired"}}), 3.49)
        self.assertEqual(prices.per_item_price({"price": 3.49}), 3.49)
        found = [ah_found("300", "AH Rundergehakt", 3.49, "300 g", bonus=one_plus_one), ah_found("1000", "AH Rundergehakt", 9.99, "1000 g")]
        with mock.patch.object(ah, "search_products", return_value=found):
            reply = await self.client_for(create_app()).post("/api/ah/cart/preview", json={"compare": True, "items": [{"query": "rundergehakt"}]})
        candidates = reply.get_json()["items"][0]["candidates"]
        self.assertEqual([(c["productId"], c["unitPrice"]["value"]) for c in candidates], [("300", 5.83), ("1000", 9.99)])  # zelfde product
        self.assertFalse(candidates[1]["cheaper"])

    def test_without_a_pack_size_only_the_same_product_is_added(self):
        found = [ah_found("a", "AH Basilicum", 1.29, ""), ah_found("b", "AH Basilicum", 2.49, "1 bos"), ah_found("c", "AH Basilicum pesto", 2.0, "190 g")]
        result = prices.check_list_candidates([{"productId": f["webshopId"], "title": f["title"], "brand": "AH", "unitSize": f["unitSize"], "price": f["price"]["now"]} for f in found], True)
        self.assertEqual([c["productId"] for c in result], ["a", "b"])


class OrganicTest(PricesCase):
    """*Liever biologisch* (#218): de controlelijst van het weekmenu stelt eerst een biologisch product van dezelfde soort voor."""

    REGULAR = [
        ah_found("300", "AH Rundergehakt", 3.79, "300 g"),  # €12,63/kg: het gewone voorstel
        ah_found("500", "AH Rundergehakt", 5.35, "500 g"),  # €10,70/kg
        ah_found("ballen", "AH Biologisch Rundergehakt balletjes", 3.99, "200 g"),  # biologisch, maar balletjes
        ah_found("bio", "AH Biologisch Rundergehakt", 4.99, "250 g", brand="AH Biologisch"),  # €19,96/kg
        ah_found("gehakt", "AH Gemengd gehakt", 8.99, "1 kg"),
    ]

    async def preview(self, results, organic=True, compare=True):
        body = {"compare": compare, "organic": organic, "items": [{"query": "rundergehakt", "quantity": 500, "unit": "g"}]}
        calls = []

        def search(query, limit):
            calls.append((query, limit))
            found = results.get(query)
            if isinstance(found, Exception):
                raise found
            return list(found or [])[:limit]

        with mock.patch.object(ah, "search_products", side_effect=search):
            reply = await self.client_for(create_app()).post("/api/ah/cart/preview", json=body)
        self.assertEqual(reply.status_code, 200)
        return reply.get_json()["items"][0], calls

    def test_what_counts_as_organic(self):
        for product in (
            {"title": "AH Spaghetti", "organic": True},  # AH's eigen kenmerk
            {"title": "AH Biologisch Rundergehakt balletjes"},  # zonder kenmerk, maar met "Biologisch" in de naam
            {"title": "Rundergehakt", "brand": "AH Biologisch"},
            {"title": "Jumbo Biologische Halfvolle Melk 1L"},
            {"title": "Bio yoghurt"},
            {"title": "Organic Oat Drink"},
        ):
            self.assertTrue(prices.is_organic(product), product)
        for product in ({"title": "AH Rundergehakt", "organic": False}, {"title": "Biografie"}, {"title": "Antibiotica"}, {}):
            self.assertFalse(prices.is_organic(product), product)

    def test_organic_is_not_part_of_the_kind(self):
        self.assertEqual(prices._kind_words("AH Biologisch Rundergehakt 500g", "AH"), ["rundergehakt"])
        self.assertEqual(prices._kind_words("Jumbo Biologische Halfvolle Melk 1L", None), ["halfvolle", "melk"])

    async def test_the_organic_product_of_the_same_kind_comes_first(self):
        row, calls = await self.preview({"rundergehakt": self.REGULAR})
        self.assertEqual(calls, [("rundergehakt", prices.COMPARE_LIMIT)])  # het stond er al tussen: geen extra zoekopdracht
        self.assertEqual(row["candidates"][0]["productId"], "bio")  # precies rundergehakt, niet de balletjes die eerder stonden
        self.assertTrue(row["candidates"][0]["organic"] and row["candidates"][0]["suggested"])
        self.assertEqual((row["organic"], row["organicMissing"]), (True, False))
        regular = {c["productId"]: c for c in row["candidates"][1:]}
        self.assertTrue(regular["300"]["cheaper"] and regular["500"]["cheaper"])  # zo zie je wat biologisch kost
        self.assertFalse(regular["300"]["organic"])
        self.assertNotIn("gehakt", regular)  # een andere soort blijft weg
        self.assertEqual(row["candidates"][0]["packs"], 2)  # 500 g: twee keer 250 g

    async def test_otherwise_one_extra_search_for_organic(self):
        regular = [c for c in self.REGULAR if c["webshopId"] not in ("bio", "ballen")]
        extra = [ah_found("gb", "AH Biologisch Gehaktballen", 4.49, "300 g"), ah_found("bio2", "AH Biologisch Rundergehakt", 5.49, "300 g")]
        row, calls = await self.preview({"rundergehakt": regular, "biologisch rundergehakt": extra})
        self.assertEqual(calls, [("rundergehakt", prices.COMPARE_LIMIT), ("biologisch rundergehakt", prices.COMPARE_LIMIT)])
        self.assertEqual(row["candidates"][0]["productId"], "bio2")  # gehaktballen zijn een andere soort
        self.assertFalse(row["organicMissing"])
        self.assertIn("300", [c["productId"] for c in row["candidates"]])  # het gewone product blijft te kiezen

    async def test_without_organic_the_regular_product_stays_with_a_note(self):
        regular = [c for c in self.REGULAR if c["webshopId"] not in ("bio", "ballen")]
        for extra in ([ah_found("gb", "AH Biologisch Gehaktballen", 4.49, "300 g")], RuntimeError("AH is even weg")):
            row, calls = await self.preview({"rundergehakt": regular, "biologisch rundergehakt": extra})
            self.assertEqual(len(calls), 2)
            self.assertEqual(row["candidates"][0]["productId"], "300")
            self.assertTrue(row["organicMissing"])
            self.assertNotIn("error", row)  # een mislukte extra zoekopdracht is geen fout van de regel

    async def test_an_organic_suggestion_needs_no_extra_search(self):
        spaghetti = [ah_found("1", "AH Biologisch Spaghetti", 1.99, "500 g", brand="AH Biologisch"), ah_found("2", "AH Spaghetti", 1.29, "500 g")]
        body = {"compare": True, "organic": True, "items": [{"query": "spaghetti"}]}
        with mock.patch.object(ah, "search_products", return_value=spaghetti) as search:
            row = (await self.client_for(create_app()).post("/api/ah/cart/preview", json=body)).get_json()["items"][0]
        search.assert_called_once()
        self.assertEqual((row["candidates"][0]["productId"], row["organicMissing"]), ("1", False))

    async def test_switched_off_or_searching_yourself_changes_nothing(self):
        row, calls = await self.preview({"rundergehakt": self.REGULAR}, organic=False)
        self.assertEqual((len(calls), row["candidates"][0]["productId"]), (1, "300"))
        self.assertNotIn("organicMissing", row)
        row, calls = await self.preview({"rundergehakt": self.REGULAR}, compare=False)
        self.assertEqual([c["productId"] for c in row["candidates"]], ["300", "500", "ballen", "bio", "gehakt"])
        self.assertEqual([c["organic"] for c in row["candidates"]], [False, False, True, True, False])  # wel het label
        self.assertNotIn("organicMissing", row)



def egg(pid, title, price, size, category="Zuivel, eieren", brand="AH"):
    return {**ah_found(pid, title, price, size, brand=brand), "category": category}


class IngredientKindTest(PricesCase):
    """#223: de soort in de controlelijst komt uit het ingrediënt als het voorstel dat noemt, met bij AH dezelfde hoofdcategorie."""

    async def preview(self, query, found, organic=False, quantity=None, unit=None):
        body = {"compare": True, "organic": organic, "items": [{"query": query, "quantity": quantity, "unit": unit}]}
        with mock.patch.object(ah, "search_products", side_effect=lambda q, limit: found if q == query else []):
            reply = await self.client_for(create_app()).post("/api/ah/cart/preview", json=body)
        self.assertEqual(reply.status_code, 200)
        return reply.get_json()["items"][0]

    EGGS = [
        egg("uit", "AH Witte vrije uitloopeieren M", 1.99, "6 stuks"),  # €0,33 per ei: het voorstel
        egg("koek", "AH Brabantse eierkoeken", 0.99, "4 stuks", category="Koek, snoep, chocolade"),  # €0,25 per stuk, maar geen ei
        egg("mais", "Natuurfarm de Boed Maïs scharreleieren M L", 8.49, "30 stuks", brand="Natuurfarm"),  # €0,28 per ei
        egg("bio", "AH Biologisch Eieren S M L", 4.59, "10 stuks", brand="AH Biologisch"),
        egg("L", "AH Witte vrije uitloopeieren L", 2.29, "6 stuks"),  # dezelfde eieren in maat L
    ]

    async def test_cheaper_eggs_per_piece_come_under_the_suggestion(self):
        row = await self.preview("eieren", self.EGGS, quantity=6, unit="stuks")
        rows = {c["productId"]: (c.get("same"), c.get("cheaper")) for c in row["candidates"][1:]}
        self.assertEqual(row["candidates"][0]["productId"], "uit")
        self.assertEqual(rows, {"mais": (False, True), "L": (True, False)})  # eierkoeken zijn geen eieren; maat L is hetzelfde product

    async def test_prefer_organic_finds_the_organic_eggs(self):
        row = await self.preview("eieren", self.EGGS, organic=True)
        self.assertEqual((row["candidates"][0]["productId"], row["organicMissing"]), ("bio", False))
        self.assertIn("mais", [c["productId"] for c in row["candidates"]])  # goedkoper per ei, dus erbij

    def test_egg_sizes_are_a_size_not_a_kind(self):
        self.assertEqual(prices._kind_words("AH Witte vrije uitloopeieren M", "AH"), ["witte", "vrije", "uitloopeieren"])
        self.assertTrue(prices._same_product(prices.base_title("AH Witte vrije uitloopeieren M"), "AH Witte vrije uitloopeieren L"))
        self.assertEqual(prices._ingredient_kind({"title": "AH Witte vrije uitloopeieren M", "brand": "AH"}, "ei"), [("eieren", True)])

    async def test_a_standalone_word_needs_a_standalone_word(self):
        # "AH Halfvolle melk" noemt melk als los woord: karnemelk is dan geen melk, volle melk wel.
        found = [egg("half", "AH Halfvolle melk", 1.79, "1,5 l"), egg("karne", "AH Karnemelk", 0.99, "1 l"), egg("vol", "AH Volle melk", 1.09, "1 l")]
        row = await self.preview("melk", found)
        self.assertEqual([c["productId"] for c in row["candidates"]], ["half", "vol"])

    async def test_at_ah_the_main_category_has_to_match(self):
        found = [
            egg("gilse", "Van Gilse Fijne kristalsuiker", 2.79, "1 kg", category="Maaltijden, salades", brand="Van Gilse"),
            egg("moes", "AH Appelmoes 0% suiker toegevoegd", 0.79, "355 g", category="Conserven, soepen, sauzen"),
            egg("kristal", "Van Gilse Kristalsuiker", 1.99, "1 kg", category="Maaltijden, salades", brand="Van Gilse"),
        ]
        row = await self.preview("suiker", found)
        self.assertEqual([c["productId"] for c in row["candidates"]], ["gilse", "kristal"])  # appelmoes noemt suiker, maar is het niet
        for item in found:
            item.pop("category")  # zonder categorie (Picnic, Jumbo, Crisp) telt alleen het ingrediënt
        self.assertIn("moes", [c["productId"] for c in (await self.preview("suiker", found))["candidates"]])

    async def test_minced_beef_keeps_its_kind(self):
        # #178: bij rundergehakt geen gemengd gehakt, half-om-half of gehaktballen; #218: niet de biologische balletjes.
        found = [
            egg("rund", "AH Rundergehakt", 3.79, "300 g", category="Vlees"),
            egg("hoh", "AH Half-om-half gehakt", 2.99, "500 g", category="Vlees"),
            egg("ballen", "AH Rundergehaktballen", 2.49, "300 g", category="Vlees"),
            egg("bal", "AH Biologisch Rundergehakt balletjes", 3.99, "200 g", category="Vlees", brand="AH Biologisch"),
            egg("bio", "AH Biologisch Rundergehakt", 4.99, "250 g", category="Vlees", brand="AH Biologisch"),
            egg("500", "AH Rundergehakt", 5.35, "500 g", category="Vlees"),
        ]
        row = await self.preview("rundergehakt", found)
        self.assertEqual([c["productId"] for c in row["candidates"]], ["rund", "500"])
        self.assertEqual((await self.preview("rundergehakt", found, organic=True))["candidates"][0]["productId"], "bio")

    async def test_without_the_ingredient_in_the_name_the_suggestion_decides(self):
        # "Milner Jong belegen" is kaas zonder "kaas" in de naam: dan geldt de soort van het voorstel, zoals voorheen.
        found = [
            egg("milner", "Milner Jong belegen 48+", 4.99, "400 g", category="Kaas", brand="Milner"),
            egg("room", "AH Kruiden roomkaas", 1.29, "150 g", category="Kaas"),
            egg("blok", "Milner Jong belegen 48+", 8.99, "800 g", category="Kaas", brand="Milner"),
        ]
        self.assertIsNone(prices._ingredient_kind({"title": "Milner Jong belegen 48+", "brand": "Milner"}, "kaas"))
        row = await self.preview("kaas", found)
        self.assertEqual([c["productId"] for c in row["candidates"]], ["milner", "blok"])

if __name__ == "__main__":
    unittest.main()
