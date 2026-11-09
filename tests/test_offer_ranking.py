"""De rangorde van de aanbiedingen (offer_ranking.py): de laagste prijs van het afgelopen jaar per product."""

import unittest

from helpers import db  # noqa: F401 - zet het pad naar de app
from mealieshopper.offer_ranking import year_low

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


if __name__ == "__main__":
    unittest.main()
