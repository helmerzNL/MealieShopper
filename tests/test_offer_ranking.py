"""De rangorde van de aanbiedingen (offer_ranking.py): de laagste prijs van het afgelopen jaar per product."""

import unittest

from helpers import db  # noqa: F401 - zet het pad naar de app

from mealieshopper.offer_ranking import families, family_key, family_of, year_low
from mealieshopper.prices import unit_price

TODAY: str = "2026-11-09"
POINTS: list[tuple[str, float, str]] = [
    ("2025-11-01", 2.49, "observation"),
    ("2026-02-02", 3.0, "line"),
    ("2026-09-01", 2.15, "purchase"),
    ("2026-10-27", 3.0, "offer"),
]
EMPTY: dict = {
    "status": "unknown",
    "price": None,
    "month": None,
    "source": None,
    "coveredSince": None,
    "above": None,
}


class YearLowTest(unittest.TestCase):
    def test_lowest_when_at_or_below_the_lowest_of_twelve_months(self) -> None:
        self.assertEqual(
            year_low(2.15, POINTS, TODAY),
            {
                "status": "lowest",
                "price": 2.15,
                "month": "2026-09",
                "source": "purchase",
                "coveredSince": "2025-11-01",
                "above": 0.0,
            },
        )

    def test_above_gives_the_difference(self) -> None:
        found: dict = year_low(3.65, POINTS, TODAY)
        self.assertEqual((found["status"], found["above"]), ("above", 1.5))

    def test_a_point_older_than_a_year_only_counts_for_coverage(self) -> None:
        found: dict = year_low(2.49, POINTS, TODAY)
        self.assertEqual(
            (found["status"], found["price"]),
            ("above", 2.15),
            "2.49 van 2025-11-01 ligt buiten de 365 dagen",
        )

    def test_unknown_without_a_year_of_coverage_or_without_points(self) -> None:
        self.assertEqual(
            year_low(2.0, POINTS[1:], TODAY)["status"],
            "unknown",
            "oudste punt 2026-02-02",
        )
        self.assertEqual(year_low(2.0, [], TODAY), EMPTY)
        self.assertEqual(year_low(None, POINTS, TODAY)["status"], "unknown")

    def test_half_a_cent_counts_as_equal(self) -> None:
        self.assertEqual(year_low(2.154, POINTS, TODAY)["status"], "lowest")


def pack(pid: str, size: str, price: float, on_offer: bool, title: str = "AH Rundergehakt", brand: str | None = "AH") -> dict:
    return {
        "productId": pid,
        "title": f"{title} {size}",
        "brand": brand,
        "unitSize": size,
        "price": price,
        "unitPrice": unit_price(price, size),
        "onOffer": on_offer,
    }


class FamiliesTest(unittest.TestCase):
    def test_the_family_key_drops_sizes_and_folds_the_brand(self) -> None:
        self.assertEqual(family_key("ah", "AH Rundergehakt 500 g", "AH"), "ah|rundergehakt|ah")
        self.assertEqual(family_key("ah", "Dr. Oetker Casa di mama pizza salami", "Dr. Oetker"), "ah|dr oetker casa di mama pizza salami|dr. oetker")

    def test_packs_of_one_product_form_a_family_with_the_cheapest_per_unit_marked(self) -> None:
        found = families("ah", [pack("300", "300 g", 2.49, True), pack("500", "500 g", 4.19, False), pack("1000", "1000 g", 7.49, False)], {})
        family = found["ah|rundergehakt|ah"]
        self.assertEqual([(p["productId"], p["cheapest"]) for p in family["packs"]], [("300", False), ("500", False), ("1000", True)])
        self.assertFalse(family["unknownPacks"])

    def test_variants_are_not_the_same_family(self) -> None:
        found = families(
            "ah",
            [
                pack("1", "500 g", 4.19, True),
                pack("2", "500 g", 5.49, True, title="AH Biologisch rundergehakt"),
                pack("3", "300 g", 3.99, True, title="AH Mager rundergehakt"),
            ],
            {},
        )
        self.assertEqual(len(found), 3)

    def test_the_same_product_id_twice_in_a_week_is_one_pack_with_the_lowest_price(self) -> None:
        found = families("ah", [pack("300", "300 g", 2.49, True), pack("300", "300 g", 1.75, True)], {})
        self.assertEqual([p["price"] for p in found["ah|rundergehakt|ah"]["packs"]], [1.75])

    def test_an_empty_brand_joins_the_brand_of_the_others(self) -> None:
        found = families("ah", [pack("1", "300 g", 2.49, True, brand=None), pack("2", "1000 g", 7.49, False)], {})
        self.assertEqual(len(found), 1)
        self.assertEqual(family_key("ah", "AH Rundergehakt 300 g", None, known_brands={"rundergehakt": {"ah"}}), "ah|rundergehakt|ah")

    def test_an_offer_without_a_brand_finds_the_family_of_its_packs(self) -> None:
        found = families("ah", [pack("1", "300 g", 2.49, True, brand=None), pack("2", "1000 g", 7.49, False)], {})
        self.assertEqual(family_of(found, "ah", "AH Rundergehakt 300 g", None)["key"], "ah|rundergehakt|ah")
        self.assertEqual(
            family_of(found, "ah", "Unox Rookworst", None), {"key": "ah|unox rookworst|", "packs": [], "unknownPacks": False, "bought": False}
        )

    def test_an_offer_that_is_not_a_pack_of_its_family_gets_an_empty_family(self) -> None:
        # Een aanbieding zonder prijs per eenheid staat niet in packs. Met de sleutel van de familie zou de kaartenweergave haar
        # achter de kop van de familie verbergen: dus een lege familie, met alleen de sleutel.
        found = families("ah", [pack("300", "300 g", 2.49, True), pack("1000", "1000 g", 7.49, False)], {})
        self.assertEqual(
            family_of(found, "ah", "AH Rundergehakt", "AH", product_id="999"),
            {"key": "ah|rundergehakt|ah", "packs": [], "unknownPacks": False, "bought": False},
        )
        self.assertEqual(len(family_of(found, "ah", "AH Rundergehakt", "AH", product_id="300")["packs"]), 2)

    def test_unknown_packs_when_the_app_knows_another_pack_without_a_recent_price(self) -> None:
        found = families("ah", [pack("300", "300 g", 2.49, True)], {"300": "AH Rundergehakt 300 g", "1000": "AH Rundergehakt 1000 g"})
        self.assertTrue(found["ah|rundergehakt|ah"]["unknownPacks"])

    def test_a_pack_with_another_unit_stays_in_the_family_without_competing(self) -> None:
        # AH Rundergehakt 300 g (per kilo) naast een 4-pack van 4 stuks (per stuk): allebei in de familie, de goedkoopste alleen
        # onder de verpakkingen met de eenheid van de meerderheid, hier de kilo.
        found = families("ah", [pack("300", "300 g", 2.49, True), pack("500", "500 g", 3.99, False), pack("4p", "4 stuks", 20.33, True)], {})
        family = found["ah|rundergehakt|ah"]
        self.assertEqual([(p["productId"], p["cheapest"]) for p in family["packs"]], [("300", False), ("500", True), ("4p", False)])

    def test_mixed_units_prefer_mass_and_need_two_packs_for_a_cheapest(self) -> None:
        # Een 300 g per kilo naast een 4-pack per stuk: gelijkspel, dan telt de kilo. En een eenheid met maar een verpakking kent
        # geen goedkoopste: er is niets om mee te vergelijken.
        found = families("ah", [pack("300", "300 g", 3.65, True), pack("4p", "4 stuks", 20.33, True)], {})
        self.assertEqual([(p["productId"], p["cheapest"]) for p in found["ah|rundergehakt|ah"]["packs"]], [("300", False), ("4p", False)])
        found = families("ah", [pack("300", "300 g", 3.65, True), pack("500", "500 g", 4.19, True), pack("4p", "4 stuks", 20.33, True)], {})
        self.assertEqual(
            [(p["productId"], p["cheapest"]) for p in found["ah|rundergehakt|ah"]["packs"]], [("300", False), ("500", True), ("4p", False)]
        )

    def test_cheapest_needs_the_pack_margin(self) -> None:
        found = families("ah", [pack("a", "500 g", 4.19, True), pack("b", "1000 g", 8.30, False)], {})  # 8.38 tegen 8.30 per kilo: binnen 2 procent
        self.assertEqual([p["cheapest"] for p in found["ah|rundergehakt|ah"]["packs"]], [True, False])


if __name__ == "__main__":
    unittest.main()
