"""Tests voor de meting van het prijsverloop tussen aanbiedingen (`price_drift` en `scripts/folder_price_margin.py`)."""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT: Path = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import folder_price_margin  # noqa: E402 - na het pad naar scripts/

from mealieshopper.price_drift import chain_offers, daily_points, pairs, summary  # noqa: E402

OFFER_HISTORY: str = """
CREATE TABLE offer_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT, store TEXT NOT NULL, product_id TEXT NOT NULL, title TEXT NOT NULL, brand TEXT,
  unit_size TEXT, category TEXT, price REAL, price_before REAL, promo_label TEXT, image TEXT, url TEXT, valid_from TEXT,
  valid_until TEXT, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, ended_at TEXT, syncs INTEGER NOT NULL DEFAULT 1,
  continued INTEGER NOT NULL DEFAULT 0, personal INTEGER NOT NULL DEFAULT 0
)"""


def row(
    day: str | None,
    price_before: float | None,
    continued: int = 0,
    label: str | None = None,
) -> dict[str, Any]:
    return {
        "day": day,
        "price_before": price_before,
        "continued": continued,
        "promo_label": label,
    }


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
            [(r["gap"], r["count"]) for r in rows][:3],
            [("tot 3 maanden", 2), ("tot 1 jaar", 0), ("tot 2 jaar", 1)],
        )

    def test_empty_bucket_has_no_percentiles(self) -> None:
        bucket = summary([(30, 0.10)])[1]
        self.assertEqual(
            (bucket["count"], bucket["p50"], bucket["p90"], bucket["p95"]),
            (0, None, None, None),
        )

    def test_percentiles_use_nearest_rank(self) -> None:
        bucket = summary([(10, v / 100) for v in range(1, 101)])[0]
        self.assertEqual((bucket["p50"], bucket["p90"], bucket["p95"]), (0.5, 0.9, 0.95))


class ChainTests(unittest.TestCase):
    def test_continued_chain_gives_one_point_with_newest_price_and_first_day(
        self,
    ) -> None:
        offers = chain_offers(
            [
                row("2026-01-01", 2.0),
                row("2026-01-01", 2.0, 1),
                row("2026-01-03", None, 1),
                row("2026-02-01", 3.0),
            ]
        )
        self.assertEqual(
            [(o["day"], o["price_before"], len(o["rows"])) for o in offers],
            [("2026-01-01", 2.0, 3), ("2026-02-01", 3.0, 1)],
        )

    def test_two_rows_on_one_day_give_one_point(self) -> None:
        offers = chain_offers([row("2026-01-01", 2.0), row("2026-01-01", 2.5)])
        self.assertEqual(daily_points(offers), [("2026-01-01", 2.5)])

    def test_same_day_offers_give_no_pair(self) -> None:
        offers = chain_offers([row("2026-01-01", 2.0), row("2026-01-01", 2.5)])
        self.assertEqual(pairs(daily_points(offers)), [])

    def test_unparseable_day_is_skipped(self) -> None:
        offers = chain_offers([row("", 2.0), row(None, 2.0), row("kapot", 2.0), row("2026-01-01", 2.0)])
        self.assertEqual([o["day"] for o in offers], ["2026-01-01"])

    def test_offer_without_price_before_gives_no_point(self) -> None:
        self.assertEqual(daily_points(chain_offers([row("2026-01-01", None)])), [])


def add_offer(
    conn: sqlite3.Connection,
    store: str,
    pid: str,
    title: str,
    unit: str | None,
    before: float | None,
    seen: str,
    **extra: Any,
) -> None:
    columns: dict[str, Any] = {
        "store": store,
        "product_id": pid,
        "title": title,
        "unit_size": unit,
        "price_before": before,
        "first_seen": seen,
        "last_seen": seen,
        "valid_from": seen,
        **extra,
    }
    conn.execute(
        f"INSERT INTO offer_history ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
        tuple(columns.values()),
    )


class ReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp: tempfile.TemporaryDirectory[str] = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path: str = str(Path(self.tmp.name) / "auth.sqlite3")
        conn: sqlite3.Connection = sqlite3.connect(self.path)
        conn.execute(OFFER_HISTORY)
        add_offer(conn, "ah", "100", "AH Melk", "1 l", 2.00, "2026-01-01")
        add_offer(conn, "ah", "100", "AH Melk", "1 l", 2.00, "2026-01-01", continued=1)
        add_offer(conn, "ah", "100", "AH Melk", "1 l", 2.40, "2026-04-01")
        add_offer(
            conn,
            "ah",
            "200",
            "AH Vla",
            "1 l",
            1.65,
            "2026-01-01",
            promo_label="3 stapelen voor: 1.00 Los",
        )
        add_offer(
            conn,
            "ah",
            "200",
            "AH Vla",
            "1 l",
            0.59,
            "2026-02-01",
            promo_label="2 voor: 0.79",
        )
        add_offer(conn, "ah", "300", "AH Boter", None, 1.00, "2026-01-01", valid_from="")
        add_offer(conn, "ah", "300", "AH Boter", None, 1.10, "2026-02-01")
        add_offer(conn, "ah", "folder:1", "Folder: AH Melk", "1 liter", 2.10, "2026-02-01")
        add_offer(conn, "ah", "folder:1", "Folder: AH Melk", "2 liter", 3.00, "2026-03-01")
        add_offer(conn, "ah", "folder:2", "Folder: Alle kaas", None, None, "2026-02-01")
        add_offer(conn, "jumbo", "J1", "Jumbo Kaas", "500 g", 4.00, "2026-01-01")
        add_offer(
            conn,
            "jumbo",
            "folder:9",
            "Folder: Jumbo Kaas 500 gram",
            "500 g",
            4.10,
            "2026-02-01",
        )
        conn.execute("CREATE TABLE ah_orders (order_id INTEGER, kind TEXT)")
        conn.execute("CREATE TABLE ah_order_items (order_id INTEGER, product_id INTEGER, title TEXT, unit_size TEXT)")
        conn.execute("INSERT INTO ah_orders VALUES (1, 'delivered')")
        conn.execute("INSERT INTO ah_order_items VALUES (1, 100, 'AH Melk', '1 l')")
        conn.execute("CREATE TABLE imported_purchases (store TEXT, order_ref TEXT, position INTEGER, day TEXT, title TEXT, quantity REAL)")
        conn.execute("INSERT INTO imported_purchases VALUES ('ah', 'o1', 1, '2026-02-02', 'AH Kaas', 1)")
        conn.commit()
        conn.close()

    def test_drift_counts_offers_not_rows(self) -> None:
        text: str = folder_price_margin.report(self.path)
        ah: str = text.split("## ah")[1].split("## jumbo")[0]
        self.assertIn("Gelezen rijen van producten met productnummer (ook door het archief gekoppelde): 7", ah)
        self.assertIn("Aanbiedingen na samenvoegen van ketens: 6", ah)
        self.assertIn("Producten: 3", ah)

    def test_multi_buy_offers_have_their_own_table(self) -> None:
        text: str = folder_price_margin.report(self.path)
        ah: str = text.split("## ah")[1].split("## jumbo")[0]
        all_part, single_part = ah.split("zonder minimumaantal")
        self.assertIn("Paren: 3", all_part)
        self.assertIn("Paren: 2", single_part.split("### Folderregels")[0])

    def test_report_counts(self) -> None:
        text: str = folder_price_margin.report(self.path)
        self.assertIn("## jumbo", text)
        self.assertIn("Folderproducten: 2", text)
        self.assertIn("Folderregels: 3", text)
        self.assertIn("Assortimentsregels: 1", text)
        self.assertIn("Folderregels met van-prijs: 2", text)
        self.assertIn("Folderproducten met meer dan een unit_size: 1", text)
        self.assertIn("Folderproducten met meer dan een verpakking uit de tekst: 1", text)

    def test_purchases_match_when_unit_spelling_differs(self) -> None:
        text: str = folder_price_margin.report(self.path)
        ah: str = text.split("## ah")[1].split("## jumbo")[0]
        self.assertIn("Gekochte producten met productnummer: 1", ah)
        self.assertIn("Daarvan met folderregel van dezelfde naam: 1", ah)
        self.assertIn("Daarvan met dezelfde naam en een leesbare verpakking: 1", ah)
        self.assertIn("Daarvan met gelijke verpakking (ondergrens, over de vorige regel): 1", ah)
        self.assertIn("verpakking uit de titel): 1", ah)
        self.assertIn("Daarvan met folderregel van dezelfde naam: 0", ah)

    def test_missing_tables_report_zero(self) -> None:
        text: str = folder_price_margin.report(self.path)
        jumbo: str = text.split("## jumbo")[1]
        self.assertIn("Gekochte producten met productnummer: 0", jumbo)
        self.assertIn("verpakking uit de titel): 0", jumbo)

    def test_database_is_opened_read_only(self) -> None:
        conn: sqlite3.Connection = folder_price_margin.open_readonly(self.path)
        self.addCleanup(conn.close)
        with self.assertRaises(sqlite3.OperationalError):
            conn.execute("DELETE FROM offer_history")

    def test_path_with_special_characters_opens(self) -> None:
        odd: Path = Path(self.tmp.name) / "a?b#c.sqlite3"
        odd.write_bytes(Path(self.path).read_bytes())
        self.assertIn("## ah", folder_price_margin.report(str(odd)))


if __name__ == "__main__":
    unittest.main()
