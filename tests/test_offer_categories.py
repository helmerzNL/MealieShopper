import unittest

from mealieshopper import offer_categories


class SnackBrandsTest(unittest.TestCase):
    def test_chips_and_snack_brands_are_snacks_not_the_flavour_on_the_bag(self):
        for title in ("Lay's Paprika", "Lays Paprika chips", "Doritos Nacho Cheese", "Pringles Original", "Cheetos Flamin Hot",
                      "Bifi Roll XXL 2-pack", "Bugles Nacho Cheese", "Tostitos Salsa"):
            self.assertEqual(offer_categories.categorize(title), "snacks_snoep", title)

    def test_real_produce_stays_produce(self):
        self.assertEqual(offer_categories.categorize("AH Paprika rood"), "groente_fruit")
        self.assertEqual(offer_categories.categorize("AH Biologische paprika mix"), "groente_fruit")

    def test_shampoo_with_a_food_word_is_not_food(self):
        self.assertEqual(offer_categories.categorize("Loving Blends Avocado olie shampoo 4-pack"), "drogisterie")


if __name__ == "__main__":
    unittest.main()
