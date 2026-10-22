import unittest

from mealieshopper import offer_translate


def first(line):
    return offer_translate.variants(line)[0]


class VariantsTest(unittest.TestCase):
    def test_english_ingredients_become_dutch_and_keep_amounts_and_units(self):
        for english, dutch in (
            ("6 tbsp mayo", "6 tbsp mayonaise"),
            ("1 tbsp curry sauce", "1 tbsp currysaus"),
            ("Bread roll", "broodje"),
            ("1 shallot", "1 sjalot"),
            ("1 zucchini", "1 courgette"),
            ("2 red bell peppers", "2 rode paprika"),
            ("200 ml heavy cream", "200 ml slagroom"),
            ("250 g ground beef", "250 g rundergehakt"),
            ("4 cloves garlic", "4 cloves knoflook"),
            ("1 tin chickpeas", "1 tin kikkererwten"),
            ("Old mature cheese", "oude kaas"),
            ("1 lb shrimp", "1 lb garnalen"),
            ("Cornstarch", "maizena"),
        ):
            self.assertEqual(first(english), dutch, english)

    def test_plurals_and_capitals_do_not_matter(self):
        self.assertEqual(first("3 Tomatoes"), "3 tomaat")
        self.assertEqual(first("2 CHICKEN BREASTS"), "2 kipfilet")
        self.assertEqual(first("5 cherries"), "5 kers")
        self.assertEqual(first("2 eggs"), "2 ei")
        self.assertEqual(first("1 cup oats"), "1 cup havermout")

    def test_a_word_with_several_dutch_names_gives_a_variant_for_each(self):
        self.assertEqual(
            offer_translate.variants("800 gr chicken thighs"),
            ["800 gr kippendijen", "800 gr kipdijfilet", "800 gr kippendij"],
        )
        self.assertEqual(offer_translate.variants("250 g mince"), ["250 g gehakt"])
        self.assertIn("2 eieren", offer_translate.variants("2 eggs"))
        self.assertIn("1 bosui", offer_translate.variants("1 spring onion"))
        self.assertIn("1 lente-ui", offer_translate.variants("1 spring onion"))

    def test_the_longest_phrase_wins(self):
        self.assertEqual(first("100 g cream cheese"), "100 g roomkaas")
        self.assertEqual(first("2 tbsp olive oil"), "2 tbsp olijfolie")
        self.assertEqual(first("1 tsp tomato paste"), "1 tsp tomatenpuree")
        self.assertEqual(first("100 g parmesan cheese"), "100 g parmezaanse kaas")
        self.assertEqual(first("1 stock cube"), "1 bouillonblokje")

    def test_text_in_parentheses_is_left_alone(self):
        self.assertEqual(first("800 gr chicken thighs (or/and pork/bacon)"), "800 gr kippendijen (or/and pork/bacon)")

    def test_words_that_are_not_in_the_list_stay(self):
        self.assertEqual(offer_translate.variants("2 tbsp harissa"), ["2 tbsp harissa"])
        self.assertEqual(offer_translate.variants("grillpan"), ["grillpan"])
        self.assertEqual(offer_translate.variants(""), [""])
        self.assertEqual(offer_translate.variants(None), [""])

    def test_the_number_of_variants_is_capped(self):
        many = " ".join(["chicken thighs"] * 4)
        self.assertLessEqual(len(offer_translate.variants(many)), offer_translate.MAX_VARIANTS)


class DutchStaysDutchTest(unittest.TestCase):
    DUTCH = [
        "400 g kipfilet", "4 tomaten", "2 el olijfolie", "1 ui", "250 g gehakt", "300 g penne", "1 teentje knoflook", "100 g roomkaas",
        "zalmfilets zonder huid", "Ketjap manis", "6 eieren", "1 komkommer", "avocado’s", "400 g spinazie", "1 courgette", "200 ml slagroom",
        "2 el sojasaus", "1 bosje peterselie", "500 ml melk", "250 g bloem", "snufje zout", "1 pak bouillonblokjes", "witte wijn",
        "1 rode paprika", "2 el mayonaise", "300 g tagliatelle", "1 bosui", "1 blik kikkererwten", "400 g rundergehakt", "Bloemkoolrijst",
        "Taugé", "grillpan", "2 el tomatenpuree", "100 g geraspte kaas", "1 el honing", "1 broodje", "kippendijen", "100 g pindakaas",
        "1 el ketjap", "200 g broccoli", "1 mango", "1 pot pesto", "100 g ham", "2 el pasta", "50 g parmezaanse kaas", "1 limoen",
    ]

    def test_dutch_lines_are_not_changed(self):
        for line in self.DUTCH:
            self.assertEqual(offer_translate.variants(line), [line], line)

    def test_translating_the_result_again_changes_nothing(self):
        for english in ("6 tbsp mayo", "250 g ground beef", "Old mature cheese", "2 red bell peppers", "1 tin chickpeas", "200 ml heavy cream"):
            dutch = first(english)
            self.assertEqual(offer_translate.variants(dutch), [dutch], english)


class LexiconTest(unittest.TestCase):
    def test_every_entry_is_lowercase_and_has_a_dutch_name(self):
        for english, dutch in offer_translate.LEXICON.items():
            self.assertEqual(english, english.lower(), english)
            names = (dutch,) if isinstance(dutch, str) else dutch
            self.assertTrue(names and all(name.strip() for name in names), english)
            self.assertLessEqual(len(english.split()), offer_translate.MAX_PHRASE, english)


if __name__ == "__main__":
    unittest.main()
