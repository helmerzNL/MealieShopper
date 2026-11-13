import unittest

from mealieshopper.regular_price import Chosen, RegularPoint, choose

ON: str = "2026-11-12"


class ChooseTest(unittest.TestCase):
    def test_lowest_within_thirty_days(self) -> None:
        points: list[RegularPoint] = [
            RegularPoint("2026-11-01", 2.49, "search"),
            RegularPoint("2026-10-20", 2.29, "offer"),
            RegularPoint("2026-03-01", 1.99, "order"),
        ]
        self.assertEqual(choose(points, ON), Chosen(2.29, "2026-10-20", "offer", True, True))

    def test_newest_when_nothing_fresh(self) -> None:
        points: list[RegularPoint] = [
            RegularPoint("2026-07-12", 2.39, "receipt"),
            RegularPoint("2026-09-28", 2.49, "offer"),
        ]
        self.assertEqual(choose(points, ON), Chosen(2.49, "2026-09-28", "offer", False, True))

    def test_same_day_purchase_beats_search_beats_offer(self) -> None:
        points: list[RegularPoint] = [
            RegularPoint("2026-08-01", 2.59, "offer"),
            RegularPoint("2026-08-01", 2.49, "search"),
            RegularPoint("2026-08-01", 2.39, "import"),
        ]
        chosen: Chosen | None = choose(points, ON)
        assert chosen is not None
        self.assertEqual(chosen.source, "import")

    def test_older_than_a_year_is_shown_not_judged(self) -> None:
        self.assertEqual(
            choose([RegularPoint("2022-05-01", 1.79, "offer")], ON),
            Chosen(1.79, "2022-05-01", "offer", False, False),
        )

    def test_exactly_365_days_is_judged_and_366_is_not(self) -> None:
        within: Chosen | None = choose([RegularPoint("2025-11-12", 1.0, "order")], ON)
        beyond: Chosen | None = choose([RegularPoint("2025-11-11", 1.0, "order")], ON)
        assert within is not None and beyond is not None
        self.assertTrue(within.judged)
        self.assertFalse(beyond.judged)

    def test_thirty_days_back_is_fresh_and_thirty_one_is_not(self) -> None:
        # FRESH_DAYS 30 en `p.day >= on - 30 dagen`: op 2026-11-12 is 2026-10-13 nog vers en 2026-10-12 niet meer.
        within: Chosen | None = choose([RegularPoint("2026-10-13", 1.0, "order")], ON)
        beyond: Chosen | None = choose([RegularPoint("2026-10-12", 1.0, "order")], ON)
        assert within is not None and beyond is not None
        self.assertTrue(within.fresh)
        self.assertFalse(beyond.fresh)

    def test_future_points_and_empty(self) -> None:
        self.assertIsNone(choose([RegularPoint("2026-11-13", 1.0, "search")], ON))
        self.assertIsNone(choose([], ON))
