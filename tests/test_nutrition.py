"""Voedingswaarden per portie (issue #283): getallen lezen uit elke bron, omrekenen en terugschrijven."""

import unittest

from mealieshopper import nutrition


class NumberTest(unittest.TestCase):
    def test_the_first_number_in_a_value(self):
        for raw, expected in ((490, 490.0), (20.5, 20.5), ("490", 490.0), ("490 kcal", 490.0), ("20,5 g", 20.5), (" 7.25g", 7.25)):
            self.assertEqual(nutrition.number(raw), expected, raw)

    def test_nothing_usable_is_none(self):
        for raw in (None, "", "geen", True, False, float("nan"), float("inf"), -3, [], {}):
            self.assertIsNone(nutrition.number(raw), raw)


class ConvertTest(unittest.TestCase):
    def test_the_unit_of_the_field_without_a_unit(self):
        self.assertEqual(nutrition.convert("calories", "490"), 490)
        self.assertEqual(nutrition.convert("sodiumContent", 200), 200)

    def test_kilojoule_becomes_kcal_and_grams_become_milligrams(self):
        self.assertAlmostEqual(nutrition.convert("calories", "2092 kJ"), 500, places=0)
        self.assertAlmostEqual(nutrition.convert("calories", 2092, "kJ"), 500, places=0)
        self.assertEqual(nutrition.convert("sodiumContent", "0,4 g"), 400)
        self.assertEqual(nutrition.convert("fatContent", 1500, "mg"), 1.5)
        self.assertEqual(nutrition.convert("fatContent", "12 gram"), 12)

    def test_a_unit_that_does_not_fit_or_an_unknown_field_is_none(self):
        self.assertIsNone(nutrition.convert("calories", "12 g"))
        self.assertIsNone(nutrition.convert("fatContent", "12 kcal"))
        self.assertIsNone(nutrition.convert("servingSize", "300"))

    def test_salt_is_not_sodium(self):
        self.assertEqual(nutrition.sodium_from_salt(1), 400)
        self.assertEqual(nutrition.sodium_from_salt("1,5", "g"), 600)
        self.assertEqual(nutrition.sodium_from_salt(500, "mg"), 200)
        self.assertIsNone(nutrition.sodium_from_salt(None))


class ItemsTest(unittest.TestCase):
    MEALIE = {
        "calories": "490", "fatContent": "23", "proteinContent": "27", "sodiumContent": "200", "sugarContent": "0",
        "carbohydrateContent": None, "cholesterolContent": "", "fiberContent": "geen",
    }

    def test_items_in_the_order_of_the_screen_with_units(self):
        self.assertEqual(nutrition.items(self.MEALIE), [
            {"key": "calories", "quantity": 490, "unit": "kcal"},
            {"key": "proteinContent", "quantity": 27, "unit": "g"},
            {"key": "fatContent", "quantity": 23, "unit": "g"},
            {"key": "sugarContent", "quantity": 0, "unit": "g"},  # 0 g suiker is een waarde, geen leeg veld
            {"key": "sodiumContent", "quantity": 200, "unit": "mg"},
        ])

    def test_schema_org_text_reads_the_same(self):
        self.assertEqual(nutrition.items({"calories": "600 kcal", "fatContent": "20.5 g"}), [
            {"key": "calories", "quantity": 600, "unit": "kcal"}, {"key": "fatContent", "quantity": 20.5, "unit": "g"},
        ])

    def test_kcal_is_per_serving_and_zero_or_nonsense_is_unknown(self):
        self.assertEqual(nutrition.kcal(self.MEALIE), 490)
        for data in ({"calories": "0"}, {"calories": "25000"}, {}, None, "490", {"calories": "12 g"}):
            self.assertIsNone(nutrition.kcal(data), data)
        self.assertEqual(nutrition.items({"calories": "0", "fatContent": "3"}), [{"key": "fatContent", "quantity": 3, "unit": "g"}])

    def test_numbers_are_tidied_to_one_decimal(self):
        self.assertEqual(nutrition.items({"fatContent": "12.345"}), [{"key": "fatContent", "quantity": 12.3, "unit": "g"}])


class WriteTest(unittest.TestCase):
    def test_schema_for_an_import_and_nothing_for_nothing(self):
        self.assertEqual(
            nutrition.schema({"calories": 490.0, "fatContent": 20.5, "sodiumContent": 200}),
            {"@type": "NutritionInformation", "calories": "490 kcal", "fatContent": "20.5 g", "sodiumContent": "200 mg"},
        )
        self.assertEqual(nutrition.schema({}), {})

    def test_mealie_fields_are_text_without_a_unit_like_mealie_keeps_them(self):
        self.assertEqual(nutrition.mealie_fields({"calories": 490.0, "fatContent": 20.5}), {"calories": "490", "fatContent": "20.5"})

    def test_the_total_of_a_meal(self):
        self.assertEqual(nutrition.total(490, 2), 980)
        self.assertEqual(nutrition.total(490.4, 1.5), 736)
        self.assertIsNone(nutrition.total(None, 2))
        self.assertIsNone(nutrition.total(490, None))
        self.assertIsNone(nutrition.total(490, 0))


class SaltAndTotalsTest(unittest.TestCase):
    VALUES = {"calories": 490.0, "proteinContent": 27.0, "sugarContent": 6.0, "sodiumContent": 200.0}

    def test_salt_is_sodium_times_two_and_a_half_in_grams(self):
        self.assertEqual(nutrition.salt_g(200), 0.5)
        self.assertEqual(nutrition.sodium_mg(1.5), 600)
        self.assertIsNone(nutrition.salt_g(None))
        self.assertIsNone(nutrition.sodium_mg(None))

    def test_the_value_of_a_filter_and_salt_comes_from_sodium(self):
        self.assertEqual(nutrition.value_of(self.VALUES, "kcal"), 490)
        self.assertEqual(nutrition.value_of(self.VALUES, "protein"), 27)
        self.assertEqual(nutrition.value_of(self.VALUES, "salt"), 0.5)
        self.assertIsNone(nutrition.value_of(self.VALUES, "fiber"))
        self.assertEqual(list(nutrition.FILTERS), ["kcal", "protein", "fat", "carbs", "sugar", "fiber", "salt"])

    def test_brief_for_a_card(self):
        self.assertEqual(nutrition.brief(self.VALUES), {"kcal": 490, "protein": 27, "sugar": 6, "salt": 0.5})
        self.assertEqual(nutrition.brief({}), {"kcal": None, "protein": None, "sugar": None, "salt": None})

    def test_totals_add_up_and_scale(self):
        totals = nutrition.add({}, self.VALUES)
        nutrition.add(totals, {"calories": 510.0, "fiberContent": 4.0, "servingSize": 300})
        self.assertEqual(totals, {"calories": 1000, "proteinContent": 27, "sugarContent": 6, "sodiumContent": 200, "fiberContent": 4})
        self.assertEqual(nutrition.scaled(self.VALUES, 2)["sodiumContent"], 400)
        self.assertIsNone(nutrition.scaled(self.VALUES, None))
        self.assertIsNone(nutrition.scaled({}, 2))


if __name__ == "__main__":
    unittest.main()
