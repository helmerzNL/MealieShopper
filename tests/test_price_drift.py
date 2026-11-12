"""Tests voor de meting van het prijsverloop tussen aanbiedingen (`price_drift` en `scripts/folder_price_margin.py`)."""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT: Path = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import folder_price_margin

from mealieshopper.price_drift import pairs, summary

OFFER_HISTORY: str = """
CREATE TABLE offer_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT, store TEXT NOT NULL, product_id TEXT NOT NULL, title TEXT NOT NULL, brand TEXT,
  unit_size TEXT, category TEXT, price REAL, price_before REAL, promo_label TEXT, image TEXT, url TEXT, valid_from TEXT,
  valid_until TEXT, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, ended_at TEXT, syncs INTEGER NOT NULL DEFAULT 1,
  continued INTEGER NOT NULL DEFAULT 0, personal INTEGER NOT NULL DEFAULT 0
)"""


class PairsTests(unittest.TestCase):
    def test_pairs_gives_days_and_relative_difference(self) -> None:
        self.assertEqual(
            pairs([("2026-01-01", 2.00), ("2026-01-31", 2.20), ("2027-01-31", 2.20)]),
            [(30, 0.1), (365, 0.0)],
        )

    def test_pairs_ignores_zero_prices_and_single_points(self) -> None:
        self.assertEqual(pairs([("2026-01-01", 0.0), ("2026-02-01", 1.0)]), [])
        self.assertEqual(pairs([("2026-01-01", 1.0)]), [])

    def test_summary_buckets_by_gap(self) -> None:
        rows = summary([(30, 0.10), (60, 0.20), (400, 0.50)])
        self.assertEqual(
            [(row["gap"], row["count"]) for row in rows][:3],
            [("tot 3 maanden", 2), ("tot 1 jaar", 0), ("tot 2 jaar", 1)],
        )

    def test_empty_bucket_has_no_percentiles(self) -> None:
        row = summary([(30, 0.10)])[1]
        self.assertEqual(
            (row["count"], row["p50"], row["p90"], row["p95"]), (0, None, None, None)
        )

    def test_percentiles_use_nearest_rank(self) -> None:
        row = summary([(10, v / 100) for v in range(1, 101)])[0]
        self.assertEqual((row["p50"], row["p90"], row["p95"]), (0.5, 0.9, 0.95))


class ReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp: tempfile.TemporaryDirectory[str] = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path: str = str(Path(self.tmp.name) / "auth.sqlite3")
        conn = sqlite3.connect(self.path)
        conn.execute(OFFER_HISTORY)
        rows = [
            ("ah", "100", "AH Melk", "1 l", 2.00, "2026-01-01"),
            ("ah", "100", "AH Melk", "1 l", 2.20, "2026-01-31"),
            ("ah", "folder:1", "Folder: AH Melk 1 liter", "1 l", 2.10, "2026-02-01"),
            ("ah", "folder:1", "Folder: AH Melk 2 liter", "2 l", 3.00, "2026-03-01"),
            ("ah", "folder:2", "Folder: Alle kaas", None, None, "2026-02-01"),
            ("jumbo", "J1", "Jumbo Kaas", "500 g", 4.00, "2026-01-01"),
            (
                "jumbo",
                "folder:9",
                "Folder: Jumbo Kaas 500 gram",
                "500 g",
                4.10,
                "2026-02-01",
            ),
        ]
        for store, pid, title, unit, before, seen in rows:
            conn.execute(
                "INSERT INTO offer_history (store, product_id, title, unit_size, price_before, first_seen, last_seen) VALUES (?,?,?,?,?,?,?)",
                (store, pid, title, unit, before, seen, seen),
            )
        conn.execute(
            "CREATE TABLE imported_purchases (store TEXT, order_ref TEXT, position INTEGER, day TEXT, title TEXT, quantity REAL)"
        )
        conn.execute(
            "INSERT INTO imported_purchases VALUES ('ah', 'o1', 1, '2026-02-02', 'AH Melk 1 liter', 1)"
        )
        conn.commit()
        conn.close()

    def test_report_counts(self) -> None:
        text = folder_price_margin.report(self.path)
        self.assertIn("## ah", text)
        self.assertIn("## jumbo", text)
        self.assertIn("Folderproducten: 2", text)
        self.assertIn("Folderregels: 3", text)
        self.assertIn("Assortimentsregels: 1", text)
        self.assertIn("Folderregels met van-prijs: 2", text)
        self.assertIn("Folderproducten met meer dan een verpakking: 1", text)
        self.assertIn("Gekochte producten: 1", text)
        self.assertIn("Met folderproduct van dezelfde naam: 1", text)
        self.assertIn("Met gelijke verpakking: 1", text)

    def test_missing_tables_report_zero(self) -> None:
        text = folder_price_margin.report(self.path)
        jumbo = text.split("## jumbo")[1]
        self.assertIn("Gekochte producten: 0", jumbo)

    def test_database_is_opened_read_only(self) -> None:
        conn = folder_price_margin.open_readonly(self.path)
        self.addCleanup(conn.close)
        with self.assertRaises(sqlite3.OperationalError):
            conn.execute("DELETE FROM offer_history")


if __name__ == "__main__":
    unittest.main()
