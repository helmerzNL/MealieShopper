import unittest

from mealieshopper import offer_courses


class CourseOfTest(unittest.TestCase):
    def test_labels_from_every_source_are_recognised(self):
        for labels, expected in (
            (("Hoofdgerecht",), "hoofdgerecht"),  # Jumbo
            (("Main course",), "hoofdgerecht"),
            (([{"id": "1", "name": "Avondeten", "slug": "avondeten"}],), "hoofdgerecht"),  # Mealie
            ((["Ontbijt"],), "ontbijt"),  # AH recipeCategory
            (("Breakfast",), "ontbijt"),
            (("Brunch",), "ontbijt"),
            (("Lunch",), "lunch"),
            (("Voorgerecht",), "voorgerecht"),
            (("Starter",), "voorgerecht"),
            (("Soepen",), "soep"),
            (("Bijgerecht",), "bijgerecht"),
            (("Side dish",), "bijgerecht"),
            (("Dessert",), "dessert"),
            (("Nagerecht",), "dessert"),
            (("Borrelhapjes",), "snack"),
            (("Tussendoortje",), "snack"),
        ):
            self.assertEqual(offer_courses.course_of(*labels), expected, labels)

    def test_accents_and_case_do_not_matter(self):
        self.assertEqual(offer_courses.course_of("VOORGERECHT"), "voorgerecht")
        self.assertEqual(offer_courses.course_of("Amuse-bouche"), "voorgerecht")

    def test_the_first_matching_rule_wins(self):
        self.assertEqual(offer_courses.course_of("Hoofdgerecht", "Lunch"), "lunch")
        self.assertEqual(offer_courses.course_of(["Vegetarisch", {"name": "Dessert"}]), "dessert")

    def test_unknown_or_missing_labels_are_other(self):
        for labels in ((), (None,), ("",), ("Vegetarisch", "Pasta"), ([],), ({},), (42,)):
            self.assertEqual(offer_courses.course_of(*labels), "overig", labels)

    def test_words_that_merely_contain_a_label_do_not_count(self):
        # Alleen woordgrenzen waar dat uitmaakt: een zijspoor ("sides") is een bijgerecht, een "maintenance" is geen hoofdgerecht.
        self.assertEqual(offer_courses.course_of("Maintenance"), "overig")
        self.assertEqual(offer_courses.course_of("Soup"), "soep")


class SummarizeTest(unittest.TestCase):
    def test_fixed_order_and_no_empty_courses(self):
        counts = offer_courses.summarize(["hoofdgerecht", "ontbijt", "hoofdgerecht", "overig", "dessert"])
        self.assertEqual(
            counts,
            [{"id": "ontbijt", "count": 1}, {"id": "hoofdgerecht", "count": 2}, {"id": "dessert", "count": 1}, {"id": "overig", "count": 1}],
        )

    def test_empty(self):
        self.assertEqual(offer_courses.summarize([]), [])

    def test_every_course_has_a_rule_or_is_other(self):
        ruled = {course for course, _ in offer_courses._RULES}
        self.assertEqual(set(offer_courses.COURSES) - ruled, {"overig"})


if __name__ == "__main__":
    unittest.main()
