"""Welke folderacties voor een hele lijn bij een product horen (line_offers.py)."""

import unittest

from helpers import db  # noqa: F401 - zet het pad naar de app

from mealieshopper.line_offers import LineIndex, line_alternatives, per_piece

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
        self.assertEqual(
            line_alternatives("Folder: Alle Dr. Oetker Big Americans, Casa di Mama, en Tradizionale pizza's"),
            [{"dr", "oetker", "big", "american"}, {"dr", "casa", "di", "mama"}, {"dr", "tradizionale", "pizza"}],
        )

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
        titles = [
            "Alle Dr. Oetker Casa di Mama pizza's",
            "Alle Dr. Oetker producten",
            "Casa di Mama salame pak 380 gram",
            "Dr. Oetker Ristorante of Casa di Mama",
            "Alle Dr. Oetker Ristorante pizza's",
            "Casa di Mama tonno",
            "Ristorante, Big Americans of Casa di Mama",
        ]
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


def line(start: str, label: str, price: float | None = None, before: float | None = None) -> dict:
    return {"start": start, "promoLabel": label, "price": price, "priceBefore": before, "title": "Folder: Alle Dr. Oetker Casa di Mama"}


class PerPieceTest(unittest.TestCase):
    """Echte regels uit het AH-folderarchief bij Dr. Oetker Casa di Mama pizza salami: het archief geeft de ene week een prijs per
    stuk, de andere het totaal, en soms alleen tekst. Je eigen aankopen in die weken: 3.00 per stuk bij "2 voor 6.00" en 2.15 bij
    "1+1 gratis", met 4.29 als gewone prijs."""

    LINES: list[dict] = [
        line("2024-04-02", "2 voor: 6.00, was 2 stuks 8.38", 3.0, 4.19),
        line("2024-07-01", "2 voor: 6.00, was 8.58", 3.0, 4.29),
        line("2024-09-30", "Diepvries2 voor: 6.00, was 2 stuks 8.58", 6.0, 8.58),
        line("2025-01-13", "Diepvries voor: 1.99 Per stuk, was 4.29", 1.99, 4.29),
        line("2025-06-23", "2 voor: 5.99, was 8.58", 3.0, 4.29),
        line("2026-01-12", "Diepvries2 voor: 5.99, was 8.58", 5.99, 8.58),
        line("2026-04-07", "2 voor: 5.99 Diepvries"),
        line("2026-08-31", "1+1 kosteloos Diepvries, actie 1+1 gratis"),
        line("2026-10-26", "2 voor: 4.99 Diepvries"),
    ]

    def test_every_week_gets_the_price_per_piece(self) -> None:
        found = [(entry["start"], entry["price"], entry["priceBefore"]) for entry in per_piece([dict(item) for item in self.LINES])]
        self.assertEqual(
            found,
            [
                ("2024-04-02", 3.0, 4.19),
                ("2024-07-01", 3.0, 4.29),
                ("2024-09-30", 3.0, 4.29),
                ("2025-01-13", 1.99, 4.29),
                ("2025-06-23", 3.0, 4.29),
                ("2026-01-12", 3.0, 4.29),
                ("2026-04-07", 3.0, 4.29),
                ("2026-08-31", 2.15, 4.29),
                ("2026-10-26", 2.5, 4.29),
            ],
        )

    def test_a_total_without_a_count_is_recognised_by_the_weeks_around_it(self) -> None:
        lines = [
            line("2021-04-26", "Per stuk, actie 1.99, was 3.99", 1.99, 3.99),
            line("2022-05-02", "Alle varianten, actie 5.00, was 7.98", 5.0, 7.98),
            line("2022-08-08", "Bijv. Tradizionale, actie 2.59, was 3.99", 2.59, 3.99),
        ]
        self.assertEqual([(entry["price"], entry["priceBefore"]) for entry in per_piece(lines)], [(1.99, 3.99), (2.5, 3.99), (2.59, 3.99)])

    def test_an_offer_for_another_product_of_the_brand_drops_out(self) -> None:
        lines = [
            line("2023-10-16", "2 voor: 5.00, was 2 stuks 8.38"),
            line("2023-11-06", "15 procent voordeel Bijv. brownies choco, actie 1.78, was 2.09", 1.78, 2.09),
            line("2023-11-13", "2 voor: 5.00 Diepvries, was 2 stuks 8.38"),
        ]
        self.assertEqual([entry["start"] for entry in per_piece(lines)], ["2023-10-16", "2023-11-13"])

    def test_without_any_price_the_offer_stays_without_one(self) -> None:
        self.assertEqual(
            [(entry["price"], entry["priceBefore"], entry["priceSource"]) for entry in per_piece([line("2026-08-31", "1+1 gratis")])],
            [(None, None, None)],
        )


if __name__ == "__main__":
    unittest.main()
