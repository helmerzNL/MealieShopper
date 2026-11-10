import json
import unittest
from pathlib import Path

from mealieshopper import offer_categories


class SnackBrandsTest(unittest.TestCase):
    def test_chips_and_snack_brands_are_snacks_not_the_flavour_on_the_bag(self):
        for title in ("Lay's Paprika", "Lays Paprika chips", "Doritos Nacho Cheese", "Pringles Original", "Cheetos Flamin Hot",
                      "Bifi Roll XXL 2-pack", "Bugles Nacho Cheese", "Tostitos Salsa"):
            self.assertEqual(offer_categories.categorize(title), "snacks_snoep", title)

    def test_real_produce_stays_produce(self):
        self.assertEqual(offer_categories.categorize("AH Paprika rood"), "groente_fruit")
        self.assertEqual(offer_categories.categorize("AH Biologische paprika mix"), "groente_fruit")

    def test_filled_pasta_is_a_meal_and_not_cheese(self):
        self.assertEqual(offer_categories.categorize("AH Tortelloni verdi ricotta spinaci 3-pack"), "maaltijden")
        self.assertEqual(offer_categories.categorize("AH Ravioli mascarpone"), "maaltijden")
        self.assertEqual(offer_categories.categorize("AH Tagliatelle all' uovo 4-pack"), "maaltijden")

    def test_cheeses_are_cheese(self):
        for title in ("AH Ricotta", "AH Mascarpone", "AH Burrata", "Pecorino romano", "Gorgonzola dolce", "AH Camembert"):
            self.assertEqual(offer_categories.categorize(title), "kaas_vleeswaren", title)

    def test_shampoo_with_a_food_word_is_not_food(self):
        self.assertEqual(offer_categories.categorize("Loving Blends Avocado olie shampoo 4-pack"), "drogisterie")


class OfferCategoryTest(unittest.TestCase):
    """De categorie van een aanbieding: bij AH eerst de categorie die de winkel meegeeft (AH_TAXONOMY), dan de regexes."""

    def test_an_ah_hint_from_the_taxonomy_wins(self):
        self.assertEqual(offer_categories.offer_category("ah", "Dr. Oetker Casa di mama pizza salami", "Diepvries"), "diepvries")
        self.assertEqual(offer_categories.offer_category("ah", "GUM Soft-picks comfort flex", "Drogisterij"), "drogisterie")

    def test_a_hint_with_a_comma_matches_its_first_part(self):
        self.assertEqual(offer_categories.offer_category("ah", "Zwitsal shampoo", "Drogisterij, baby"), "drogisterie")
        self.assertEqual(offer_categories.offer_category("ah", "Philips Led kogel", "huishouden"), "huishouden", "gevouwen")

    def test_kitchen_and_leisure_goods_are_household(self):
        # "Koken, tafelen, vrije tijd": pannen, glazen, kaarsen en speelgoed. Geen eten, en het dichtst bij huishouden.
        self.assertEqual(offer_categories.offer_category("ah", "Tefal Koekenpan 28 cm", "Koken, tafelen, vrije tijd"), "huishouden")

    def test_at_most_eight_percent_of_a_production_week_is_other(self):
        # De AH-aanbiedingen van de week van 9 november 2026 (titel en categorie van AH, zonder prijzen en zonder Bonus Box).
        rows = json.loads((Path(__file__).parent / "fixtures" / "ah_offer_hints.json").read_text())
        self.assertGreater(len(rows), 2000)
        other = sum(1 for row in rows if offer_categories.offer_category("ah", row["title"], row["hint"]) == offer_categories.OTHER)
        self.assertLessEqual(other / len(rows), 0.08, f"{other} van {len(rows)} in Overig")

    def test_a_hint_that_maps_to_other_falls_through(self):
        self.assertEqual(offer_categories.offer_category("ah", "Doritos Sweet chilli pepper", "Overige"), "snacks_snoep")

    def test_without_a_hint_the_rules_decide(self):
        self.assertEqual(offer_categories.offer_category("ah", "AH Spaghetti", None), "maaltijden")
        self.assertEqual(offer_categories.offer_category("ah", "Lowlander Blond", "Nieuw schap"), offer_categories.categorize("Lowlander Blond", "Nieuw schap"))

    def test_other_stores_ignore_the_ah_taxonomy(self):
        self.assertEqual(offer_categories.offer_category("jumbo", "Jumbo Halfvolle melk", "Diepvries"), offer_categories.categorize("Jumbo Halfvolle melk", "Diepvries"))


class PurchaseCategoryTest(unittest.TestCase):
    def test_an_ah_taxonomy_maps_to_an_offer_category(self):
        self.assertEqual(offer_categories.purchase_category("ah", "Groente, aardappelen", "AH Komkommer"), "groente_fruit")
        self.assertEqual(offer_categories.purchase_category("ah", "Vegetarisch, vegan en plantaardig", "Vivera stukjes"), "vlees")
        self.assertEqual(offer_categories.purchase_category("ah", "Overige", "Cadeaukaart"), "overig")

    def test_an_unknown_taxonomy_and_other_stores_fall_back_to_the_title(self):
        self.assertEqual(offer_categories.purchase_category("ah", "Nieuw schap", "AH Spaghetti"), "maaltijden")
        self.assertEqual(offer_categories.purchase_category("jumbo", "Zuivel, kaas en eieren", "Jumbo Halfvolle melk"), "zuivel_eieren")
        self.assertEqual(offer_categories.purchase_category("ah", None, "AH ROOMKAAS"), "kaas_vleeswaren")

    def test_every_ah_taxonomy_in_the_table_names_a_known_category(self):
        for category in offer_categories.AH_TAXONOMY.values():
            self.assertIn(category, offer_categories.CATEGORIES)


if __name__ == "__main__":
    unittest.main()
