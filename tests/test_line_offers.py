"""Welke folderacties voor een hele lijn bij een product horen (line_offers.py)."""

import unittest

from helpers import db  # noqa: F401 - zet het pad naar de app

from mealieshopper.line_offers import LineIndex, line_alternatives

PIZZA: str = "Dr. Oetker Casa di Mama pizza salame extra piccante"


def matches(titles: list[str], name: str, sizes: tuple = ()) -> list[str]:
    index: LineIndex = LineIndex((f"folder:{n}", title) for n, title in enumerate(titles))
    return [titles[int(found.split(":")[1])] for found in index.lines_for([name], sizes)]


class LineWordsTest(unittest.TestCase):
    def test_words_about_the_offer_and_the_pack_do_not_count(self) -> None:
        self.assertEqual(line_alternatives("Folder: Alle Dr. Oetker Casa di Mama pizza's 2 dozen"), [{"dr", "oetker", "casa", "di", "mama", "pizza"}])
        self.assertEqual(line_alternatives("Folder: Casa di Mama salame pak 380 gram"), [{"casa", "di", "mama", "salame"}])

    def test_each_part_of_a_list_is_an_offer_with_the_first_word(self) -> None:
        self.assertEqual(line_alternatives("Folder: Dr. Oetker Casa di Mama en Tradizionale"), [{"dr", "oetker", "casa", "di", "mama"}])
        self.assertEqual(line_alternatives("Folder: Alle Dr. Oetker Big Americans, Casa di Mama, en Tradizionale pizza's"),
                         [{"dr", "oetker", "big", "american"}, {"dr", "casa", "di", "mama"}, {"dr", "tradizionale", "pizza"}])

    def test_a_part_of_a_list_needs_two_words_of_its_own(self) -> None:
        # "soep" hoort ook bij "AH Biologisch": dat deel alleen zou bij elk biologisch product van AH horen.
        self.assertEqual(line_alternatives("Folder: AH Biologisch en Excellent soep in zak"), [{"ah", "excellent", "soep"}])
        self.assertEqual(line_alternatives("Folder: AH Biologische yoghurt, kwark of melk"), [{"ah", "biologische", "yoghurt"}])

    def test_a_name_that_says_too_little_belongs_nowhere(self) -> None:
        self.assertEqual(line_alternatives("Folder: Alle kaas"), [])
        self.assertEqual(line_alternatives("Folder: Alle producten"), [])
        # Streng: "Unox rookworst" heeft naast het merk maar een eigen woord, en "salami" ook. Liever niets dan een verkeerde.
        self.assertEqual(line_alternatives("Folder: Unox rookworst of salami"), [])


class LinesForTest(unittest.TestCase):
    def test_the_line_offers_of_a_pizza(self) -> None:
        titles = ["Alle Dr. Oetker Casa di Mama pizza's", "Alle Dr. Oetker producten", "Casa di Mama salame pak 380 gram",
                  "Dr. Oetker Ristorante of Casa di Mama", "Alle Dr. Oetker Ristorante pizza's", "Casa di Mama tonno",
                  "Ristorante, Big Americans of Casa di Mama"]
        self.assertEqual(matches(titles, PIZZA), titles[:4], "het laatste deel zonder Ristorante, het eerste woord: niet")

    def test_the_house_brand_counts(self) -> None:
        self.assertEqual(matches(["AH Chips paprika", "Alle paprika chips"], "Lay's Paprika chips"), ["Alle paprika chips"])

    def test_an_organic_product_gets_only_organic_offers(self) -> None:
        titles = ["AH Rundergehakt", "AH Biologisch rundergehakt"]
        self.assertEqual(matches(titles, "AH Biologisch Rundergehakt"), ["AH Biologisch rundergehakt"])
        self.assertEqual(matches(titles, "AH Rundergehakt"), ["AH Rundergehakt"])

    def test_a_named_pack_must_be_the_pack_of_the_product(self) -> None:
        titles = ["Alle Coca-Cola 1.5 liter", "Alle Coca-Cola 0.5 liter", "Alle Coca-Cola"]
        self.assertEqual(matches(titles, "Coca-Cola Zero", ((1500.0, "volume"),)), ["Alle Coca-Cola 1.5 liter", "Alle Coca-Cola"])

    def test_each_part_of_a_list_has_its_own_pack(self) -> None:
        title = "AH Rundergehakt 500 gram, AH Mager rundergehakt 300 gram en AH Biologisch rundergehakt 300 gram"
        self.assertEqual(matches([title], "AH Biologisch Rundergehakt", ((300.0, "mass"),)), [title])
        self.assertEqual(matches([title], "AH Rundergehakt", ((300.0, "mass"),)), [], "300 gram gewoon rundergehakt stond er niet in")

    def test_a_multipack_belongs_only_to_a_multipack(self) -> None:
        titles = ["Alle Coca-Cola 4-packs", "Coca-Cola 6 x 330 ml", "Coca-Cola"]
        self.assertEqual(matches(titles, "Coca-Cola Original 1,5 liter", ((1500.0, "volume"),)), ["Coca-Cola"])
        self.assertEqual(matches(titles, "Coca-Cola Original 4 x 1,5 liter", ((6000.0, "volume"),)), ["Alle Coca-Cola 4-packs"])


if __name__ == "__main__":
    unittest.main()
