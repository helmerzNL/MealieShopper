import unittest
from datetime import date

import os
import tempfile

os.environ.setdefault("MEALIESHOPPER_DATA_DIR", tempfile.mkdtemp())

from unittest import mock  # noqa: E402

from mealieshopper import ah, ah_explore, deals, jumbo, jumbo_explore, picnic  # noqa: E402


class ParseMechanicTests(unittest.TestCase):
    def test_second_half_price(self):
        self.assertEqual(deals.parse_mechanic("2e halve prijs"), {"type": "second_half", "minQuantity": 2})

    def test_one_plus_one_free(self):
        self.assertEqual(
            deals.parse_mechanic("1+1 gratis"), {"type": "nth_free", "minQuantity": 2, "payQuantity": 1}
        )
        self.assertEqual(
            deals.parse_mechanic("2 + 1 gratis"), {"type": "nth_free", "minQuantity": 3, "payQuantity": 2}
        )

    def test_nth_free(self):
        self.assertEqual(deals.parse_mechanic("3e gratis"), {"type": "nth_free", "minQuantity": 3, "payQuantity": 2})

    def test_x_for_y(self):
        self.assertEqual(deals.parse_mechanic("2 voor 5.00"), {"type": "x_for_y", "minQuantity": 2, "total": 5.0})
        self.assertEqual(deals.parse_mechanic("3 voor € 10,50"), {"type": "x_for_y", "minQuantity": 3, "total": 10.5})
        self.assertEqual(deals.parse_mechanic("2 stuks voor 3"), {"type": "x_for_y", "minQuantity": 2, "total": 3.0})

    def test_percent(self):
        self.assertEqual(deals.parse_mechanic("25% korting"), {"type": "percent", "minQuantity": 1, "percent": 25.0})
        self.assertEqual(
            deals.parse_mechanic("20% korting vanaf 2 stuks"), {"type": "percent", "minQuantity": 2, "percent": 20.0}
        )

    def test_amount_off(self):
        self.assertEqual(
            deals.parse_mechanic("€ 1,50 korting"), {"type": "amount_off", "minQuantity": 1, "amountOff": 1.5}
        )
        self.assertEqual(
            deals.parse_mechanic("korting 2 euro"), {"type": "amount_off", "minQuantity": 1, "amountOff": 2.0}
        )

    def test_unknown_and_empty(self):
        for text in (None, "", "   ", "Bonus", "Op=op", "Nu extra voordelig"):
            self.assertEqual(deals.parse_mechanic(text), {"type": "unknown", "minQuantity": 1}, text)

    def test_first_recognisable_text_wins(self):
        self.assertEqual(deals.parse_mechanic("BONUS", "2e halve prijs")["type"], "second_half")
        self.assertEqual(deals.parse_mechanic(None, "2 VOOR 5")["type"], "x_for_y")

    def test_nth_item_with_a_percentage_is_not_a_plain_percentage(self):
        # Picnic: "2e = 50% korting" is het tweede stuk met 50% korting, niet 50% op alles.
        for text in ("2e = 50% korting", "2e 50% korting", "2e stuk 50% korting"):
            self.assertEqual(deals.parse_mechanic(text), {"type": "nth_percent", "minQuantity": 2, "percent": 50.0}, text)
        self.assertEqual(deals.parse_mechanic("3e: 100% korting")["minQuantity"], 3)

    def test_now_price(self):
        self.assertEqual(deals.parse_mechanic("nu €1.99"), {"type": "fixed", "minQuantity": 1, "fixedPrice": 1.99})
        self.assertEqual(deals.parse_mechanic("Nu € 2,49")["fixedPrice"], 2.49)
        self.assertEqual(deals.parse_mechanic("20% korting nu €1.99")["type"], "percent", "een percentage wint")

    def test_percent_is_not_read_as_x_for_y(self):
        self.assertEqual(deals.parse_mechanic("2 voor 25%")["type"], "percent")


class EffectiveUnitPriceTests(unittest.TestCase):
    def test_x_for_y_uses_the_total_in_the_text(self):
        mechanic = deals.parse_mechanic("3 voor 10")
        self.assertEqual(deals.effective_unit_price(mechanic, None, 4.0), 3.33)

    def test_second_half_needs_the_regular_price(self):
        mechanic = deals.parse_mechanic("2e halve prijs")
        self.assertEqual(deals.effective_unit_price(mechanic, 2.0, 2.0), 1.5)
        self.assertIsNone(deals.effective_unit_price(mechanic, None, 2.0))

    def test_one_plus_one_halves_the_price(self):
        self.assertEqual(deals.effective_unit_price(deals.parse_mechanic("1+1 gratis"), 3.0, 3.0), 1.5)
        self.assertEqual(deals.effective_unit_price(deals.parse_mechanic("2+1 gratis"), 3.0, 3.0), 2.0)

    def test_percent_and_amount_off(self):
        self.assertEqual(deals.effective_unit_price(deals.parse_mechanic("25% korting"), 4.0, 3.0), 3.0)
        self.assertEqual(deals.effective_unit_price(deals.parse_mechanic("€ 1,00 korting"), 4.0, 3.0), 3.0)
        self.assertEqual(deals.effective_unit_price(deals.parse_mechanic("€ 9 korting"), 4.0, 3.0), 0.0)

    def test_nth_percent_averages_over_the_pieces(self):
        mechanic = deals.parse_mechanic("2e = 50% korting")
        self.assertEqual(deals.effective_unit_price(mechanic, 4.0, 4.0), 3.0)
        self.assertEqual(deals.effective_unit_price(deals.parse_mechanic("3e = 100% korting"), 3.0, 3.0), 2.0)
        self.assertIsNone(deals.effective_unit_price(mechanic, None, 4.0), "zonder gewone prijs niet te rekenen")

    def test_now_price_is_the_price_per_item(self):
        self.assertEqual(deals.effective_unit_price(deals.parse_mechanic("nu €1.99"), None, 2.49), 1.99)

    def test_fixed_uses_the_shop_price_but_unknown_does_not(self):
        self.assertEqual(deals.effective_unit_price({"type": "fixed", "minQuantity": 1}, 4.0, 3.0), 3.0)
        self.assertIsNone(deals.effective_unit_price({"type": "unknown", "minQuantity": 1}, 4.0, 3.0))


class ValidityTests(unittest.TestCase):
    TODAY = date(2026, 10, 22)

    def test_inside_the_period(self):
        self.assertEqual(deals.validity("2026-10-19", "2026-10-25", self.TODAY), "valid")

    def test_last_day_still_counts(self):
        self.assertEqual(deals.validity("2026-10-19", "2026-10-22T00:00:00", self.TODAY), "valid")

    def test_expired_and_upcoming(self):
        self.assertEqual(deals.validity("2026-10-12", "2026-10-18", self.TODAY), "expired")
        self.assertEqual(deals.validity("2026-10-23", "2026-10-29", self.TODAY), "upcoming")

    def test_one_date_is_enough_to_check(self):
        self.assertEqual(deals.validity(None, "2026-10-18", self.TODAY), "expired")
        self.assertEqual(deals.validity("2026-10-19", None, self.TODAY), "valid")

    def test_no_dates_is_unknown_not_valid(self):
        self.assertEqual(deals.validity(None, None, self.TODAY), "unknown")
        self.assertEqual(deals.validity("onzin", "", self.TODAY), "unknown")


class MakeDealTests(unittest.TestCase):
    TODAY = date(2026, 10, 22)

    def test_product_without_bonus_is_no_deal(self):
        self.assertIsNone(deals.make_deal("ah", {"id": "1", "title": "Melk", "price": 1.2, "isBonus": False}))

    def test_full_promo_is_normalised(self):
        product = {
            "id": "wi1",
            "title": "AH Kipfilet",
            "unitSize": "500 g",
            "price": 4.0,
            "isBonus": True,
            "promo": {
                "label": "2e halve prijs",
                "priceBefore": 4.0,
                "validFrom": "2026-10-19",
                "validUntil": "2026-10-25",
            },
        }
        deal = deals.make_deal("ah", product, self.TODAY)
        self.assertEqual(deal["store"], "ah")
        self.assertEqual(deal["mechanic"], "second_half")
        self.assertEqual(deal["minQuantity"], 2)
        self.assertEqual(deal["unitPrice"], 3.0)
        self.assertEqual(deal["validity"], "valid")

    def test_bonus_flag_only_stays_unknown(self):
        deal = deals.make_deal("jumbo", {"id": "9", "title": "Pasta", "price": 1.5, "isBonus": True}, self.TODAY)
        self.assertEqual(deal["mechanic"], "unknown")
        self.assertIsNone(deal["unitPrice"])
        self.assertEqual(deal["validity"], "unknown")

    def test_lower_price_without_label_is_a_fixed_deal(self):
        product = {"id": "9", "title": "Pasta", "price": 1.5, "isBonus": True, "promo": {"priceBefore": 2.0}}
        deal = deals.make_deal("jumbo", product, self.TODAY)
        self.assertEqual(deal["mechanic"], "fixed")
        self.assertEqual(deal["unitPrice"], 1.5)

    def test_price_dict_of_ah_is_read(self):
        product = {"webshopId": "7", "title": "Ei", "price": {"now": 2.0, "unitSize": "10"}, "isBonus": True}
        self.assertEqual(deals.make_deal("ah", product, self.TODAY)["priceNow"], 2.0)


class DescribeTests(unittest.TestCase):
    TODAY = date(2026, 10, 22)

    def test_label_and_prices_give_the_conditions_and_the_unit_price(self):
        described = deals.describe("2e halve prijs", price_now=4.0, price_before=4.0, valid_until="2026-10-25", today=self.TODAY)
        self.assertEqual(described, {"mechanic": "second_half", "minQuantity": 2, "unitPrice": 3.0, "validity": "valid"})

    def test_cheaper_without_a_label_is_a_fixed_price(self):
        described = deals.describe("", price_now=1.5, price_before=2.0, today=self.TODAY)
        self.assertEqual((described["mechanic"], described["unitPrice"], described["validity"]), ("fixed", 1.5, "unknown"))

    def test_unreadable_label_stays_unknown_without_a_unit_price(self):
        described = deals.describe("Op=op", price_now=1.5, price_before=None, today=self.TODAY)
        self.assertEqual((described["mechanic"], described["unitPrice"]), ("unknown", None))

    def test_expired_period_is_reported(self):
        self.assertEqual(deals.describe("25% korting", valid_until="2026-10-18", today=self.TODAY)["validity"], "expired")


class CommonPeriodTests(unittest.TestCase):
    def test_most_shared_period_wins(self):
        week = {"validFrom": "2026-10-26", "validUntil": "2026-11-01"}
        other = {"validFrom": "2026-10-19", "validUntil": "2026-10-25"}
        self.assertEqual(deals.common_period([week, other, week]), {"from": "2026-10-26", "until": "2026-11-01"})

    def test_tie_goes_to_the_latest_end(self):
        week = {"validFrom": "2026-10-26", "validUntil": "2026-11-01"}
        other = {"validFrom": "2026-10-19", "validUntil": "2026-10-25"}
        self.assertEqual(deals.common_period([other, week])["until"], "2026-11-01")

    def test_open_ended_and_undated_offers_do_not_count(self):
        self.assertIsNone(deals.common_period([{"validFrom": "2026-10-26", "validUntil": None}, {}, {"validUntil": "2026-11-01"}]))
        self.assertIsNone(deals.common_period([]))


class AhPromoTests(unittest.TestCase):
    def test_bonus_fields_are_kept(self):
        item = {
            "webshopId": 1, "title": "AH Kipfilet", "currentPrice": 3.0, "priceBeforeBonus": 4.0, "isBonus": True,
            "bonusMechanism": "25% korting", "bonusStartDate": "2026-10-19", "bonusEndDate": "2026-10-25",
            "discountLabels": [{"defaultDescription": "25% korting"}, {"code": "x"}],
        }
        product = ah._search_product_dict(item, "kip")
        self.assertEqual(
            product["promo"],
            {"label": "25% korting", "mechanism": "25% korting", "priceBefore": 4.0,
             "validFrom": "2026-10-19", "validUntil": "2026-10-25"},
        )
        deal = deals.make_deal("ah", product, date(2026, 10, 22))
        self.assertEqual((deal["mechanic"], deal["unitPrice"], deal["validity"]), ("percent", 3.0, "valid"))

    def test_open_ended_bonus_has_no_end_date(self):
        item = {"webshopId": 1, "title": "Kaas", "currentPrice": 2.0, "priceBeforeBonus": 2.5, "isBonus": True,
                "bonusStartDate": "2026-10-26", "bonusEndDate": "2999-12-31", "bonusMechanism": "5% volume voordeel"}
        product = ah._bonus_product(item)
        self.assertEqual((product["validFrom"], product["validUntil"]), ("2026-10-26", None))
        self.assertIsNone(ah._bonus_promo(item)["validUntil"])
        self.assertEqual(ah._bonus_promo(item)["validFrom"], "2026-10-26")
        infinite = {"webshopId": 2, "title": "Melk", "isInfiniteBonus": True, "bonusEndDate": "2026-11-01"}
        self.assertIsNone(ah._bonus_product(infinite)["validUntil"])

    def test_weekly_bonus_keeps_both_dates(self):
        item = {"webshopId": 3, "title": "Pasta", "bonusStartDate": "2026-10-26", "bonusEndDate": "2026-11-01"}
        product = ah._bonus_product(item)
        self.assertEqual((product["validFrom"], product["validUntil"]), ("2026-10-26", "2026-11-01"))

    def test_missing_fields_stay_empty(self):
        product = ah._search_product_dict({"webshopId": 2, "title": "Ei", "currentPrice": 2.0, "isBonus": True}, "ei")
        self.assertEqual(
            product["promo"],
            {"label": None, "mechanism": None, "priceBefore": None, "validFrom": None, "validUntil": None},
        )
        self.assertEqual(deals.make_deal("ah", product, date(2026, 10, 22))["mechanic"], "unknown")

    def test_no_bonus_has_no_promo(self):
        self.assertNotIn("promo", ah._search_product_dict({"webshopId": 3, "title": "Melk"}, "melk"))


class CandidateDealTests(unittest.TestCase):
    def test_candidate_deal_is_compact(self):
        product = {"id": "1", "title": "Kaas", "price": 2.5, "isBonus": True, "promo": {"label": "2 voor 5", "validUntil": "2000-01-01"}}
        deal = deals.candidate_deal("jumbo", product)
        self.assertEqual(deal["label"], "2 voor 5")
        self.assertEqual(deal["mechanic"], "x_for_y")
        self.assertEqual(deal["validity"], "expired")
        self.assertEqual(set(deal), {"label", "mechanic", "minQuantity", "unitPrice", "validUntil", "validity"})

    def test_no_bonus_gives_no_deal(self):
        self.assertIsNone(deals.candidate_deal("picnic", {"id": "1", "title": "Brood", "price": 1.0, "isBonus": False}))

    def test_store_candidates_carry_the_deal(self):
        bonus = {"id": "1", "title": "Kaas", "unitSize": "400 g", "price": 2.5, "isBonus": True, "promo": {"label": "1+1 gratis"}}
        plain = {"id": "2", "title": "Brood", "unitSize": "800 g", "price": 1.0, "isBonus": False}
        for candidate in (jumbo.candidate, picnic.candidate):
            self.assertEqual(candidate(bonus, 400, "g")["deal"]["mechanic"], "nth_free")
            self.assertIsNone(candidate(plain, 400, "g")["deal"])


class PromoExplorerTests(unittest.TestCase):
    def test_report_shows_texts_and_dates_but_not_prices_or_titles(self):
        items = [
            {"title": "AH Kipfilet", "webshopId": 5, "currentPrice": 3.5, "priceBeforeBonus": 4.5, "isBonus": True,
             "bonusMechanism": "2e halve prijs", "bonusEndDate": "2026-10-25",
             "discountLabels": [{"code": "X", "defaultDescription": "2e halve prijs"}]},
            {"title": "AH Kaas", "webshopId": 6, "currentPrice": 2.0, "isBonus": True, "bonusMechanism": "25% korting"},
        ]
        report = ah_explore.promo_report(items)
        self.assertEqual(report["met aanbieding"], 2)
        self.assertEqual(report["teksten"]["bonusMechanism"], ["25% korting", "2e halve prijs"])
        self.assertEqual(report["teksten"]["bonusEndDate"], ["2026-10-25"])
        self.assertEqual(report["teksten"]["defaultDescription"], ["2e halve prijs"])
        self.assertEqual(report["velden"]["currentPrice"], "float")
        text = str(report)
        for private in ("Kipfilet", "Kaas", "3.5", "4.5"):
            self.assertNotIn(private, text)
        self.assertNotIn("title", report["velden"])
        self.assertNotIn("webshopId", report["velden"])

    def test_report_without_deals(self):
        self.assertEqual(ah_explore.promo_report([]), {"met aanbieding": 0})

    def test_probe_counts_products_and_keeps_only_deals(self):
        pages = {"kip": [{"isBonus": True, "bonusMechanism": "1+1 gratis"}, {"isBonus": False}], "kaas": [{"isBonus": False}]}
        report = ah_explore.promo_probe(("kip", "kaas"), lambda term: pages[term], lambda item: item["isBonus"])
        self.assertEqual((report["producten"], report["met aanbieding"]), (3, 1))
        self.assertEqual(report["teksten"]["bonusMechanism"], ["1+1 gratis"])

    def test_jumbo_probe_drops_the_fields_jumbo_rejects(self):
        calls = []

        def fake_graphql(query, variables, operation, *args):
            calls.append(query)
            if " description" in query or " maxQuantity" in query:
                raise jumbo.JumboSchemaError('Cannot query field "description" on type "Promotion"; Cannot query field "maxQuantity" on type "Promotion".')
            return {"searchProducts": {"products": [{"promotions": [{"tags": [{"text": "2 voor 5"}], "id": "p1"}]}]}}

        with mock.patch.object(jumbo, "_graphql", side_effect=fake_graphql):
            report = jumbo_explore._promotion_probe()
        self.assertNotIn("description", report["promotievelden die bestaan"])
        self.assertNotIn("maxQuantity", report["promotievelden die bestaan"])
        self.assertIn("validityPeriod", report["promotievelden die bestaan"])
        self.assertTrue(all("start { iso } end { iso }" in query for query in calls), "de looptijd gaat altijd mee")
        self.assertEqual(report["teksten"]["text"], ["2 voor 5"])
        self.assertGreaterEqual(len(calls), 2)

    def test_jumbo_probe_gives_up_on_an_error_that_names_no_known_field(self):
        with mock.patch.object(jumbo, "_graphql", side_effect=jumbo.JumboSchemaError("iets anders")):
            with self.assertRaises(jumbo.JumboSchemaError):
                jumbo_explore._promotion_probe()


if __name__ == "__main__":
    unittest.main()
