"""Het schema van de database: de indexen staan er, de hete queries gebruiken ze, en de opzet loopt een keer per proces.

De nachtelijke rondes en het dashboard zoeken telkens dezelfde rijen op (de bonnen zonder details, de bonproducten zonder foto, de
afbeeldingen die nog opgehaald moeten worden, een product in de aanbiedingen). Zonder index leest SQLite daarvoor de hele tabel; met
duizenden bonregels en afbeeldingen merk je dat. Deze tests houden de plannen vast met `EXPLAIN QUERY PLAN`, zodat een index die
wegvalt of een query die om de index heen gaat, meteen rood wordt.
"""

import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from unittest import mock

os.environ.setdefault("MEALIESHOPPER_DATA_DIR", tempfile.mkdtemp())

from helpers import AppTestCase, db, run

from mealieshopper import (
    ah_orders,
    audit,
    auth,
    crisp_orders,
    image_cache,
    jumbo_orders,
    oauth,
    offers,
    picnic_orders,
    prices,
    product_links,
)

SCHEMA_OWNERS = (
    ah_orders,
    audit,
    crisp_orders,
    image_cache,
    jumbo_orders,
    oauth,
    offers,
    picnic_orders,
    prices,
)

EXPECTED_INDEXES = {
    "ah_order_items_product",
    "ah_orders_unsynced",
    "ah_receipt_items_product",
    "ah_receipt_products_product",
    "ah_receipt_products_unimaged",
    "ah_receipts_unsynced",
    "audit_log_corr",
    "audit_log_service",
    "audit_log_ts",
    "crisp_orders_unsynced",
    "image_cache_pending",
    "jumbo_order_items_product",
    "jumbo_orders_unsynced",
    "mfa_recovery_codes_user",
    "oauth_codes_client",
    "oauth_tokens_client",
    "oauth_tokens_user",
    "offer_history_open",
    "offer_history_product",
    "offers_product",
    "offers_store",
    "passkey_credentials_user",
    "picnic_orders_unsynced",
    "price_observations_day",
}

# De query zoals de code hem stuurt, met de index die het plan moet noemen.
HOT_QUERIES: tuple[tuple[str, tuple, str], ...] = (
    (
        "SELECT * FROM image_cache WHERE path IS NULL ORDER BY seen_at DESC, id DESC",
        (),
        "image_cache_pending",
    ),
    (
        "SELECT COUNT(DISTINCT product_id) FROM ah_receipt_products WHERE image IS NULL AND product_id IS NOT NULL",
        (),
        "ah_receipt_products_unimaged",
    ),
    (
        "UPDATE ah_receipt_products SET image = ? WHERE product_id = ? AND image IS NULL",
        ("x", 1),
        "ah_receipt_products_unimaged",
    ),
    (
        "SELECT * FROM ah_receipt_products WHERE product_id = ? ORDER BY rowid DESC LIMIT 1",
        (1,),
        "ah_receipt_products_product",
    ),
    (
        "SELECT COUNT(*) FROM ah_receipts WHERE details_synced = 0",
        (),
        "ah_receipts_unsynced",
    ),
    (
        "SELECT receipt_id FROM ah_receipts WHERE details_synced = 0 ORDER BY date_time DESC LIMIT 5",
        (),
        "ah_receipts_unsynced",
    ),
    (
        "SELECT COUNT(*) FROM ah_orders WHERE kind = 'delivered' AND items_synced = 0",
        (),
        "ah_orders_unsynced",
    ),
    (
        "UPDATE ah_receipt_items SET regular_price = ? WHERE product_id = ?",
        (1.0, 5),
        "ah_receipt_items_product",
    ),
    (
        "SELECT * FROM ah_order_items WHERE product_id = ? ORDER BY rowid DESC LIMIT 1",
        (1,),
        "ah_order_items_product",
    ),
    ("DELETE FROM audit_log WHERE ts < ?", ("2026",), "audit_log_ts"),
    (
        "SELECT * FROM offers WHERE store = ? AND product_id = ? ORDER BY rowid DESC LIMIT 1",
        ("ah", "1"),
        "offers_product",
    ),
    (
        "SELECT * FROM offers WHERE store = ? AND product_id IN (?, ?) ORDER BY personal",
        ("ah", "1", "2"),
        "offers_product",
    ),
    (
        "SELECT * FROM offer_history WHERE store = ? AND product_id IN (?, ?) ORDER BY product_id, first_seen, id",
        ("ah", "1", "2"),
        "offer_history_product",
    ),
    (
        "SELECT * FROM price_observations WHERE store = ? AND day >= ? AND day <= ? ORDER BY day",
        ("ah", "2026-01-01", "2026-02-01"),
        "price_observations_day",
    ),
    ("DELETE FROM oauth_tokens WHERE user_id = ?", ("u",), "oauth_tokens_user"),
)


class SchemaCase(AppTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(
            os.environ,
            {"MEALIESHOPPER_DATA_DIR": tmp.name, "PASSKEY_AUTH_ENABLED": "false"},
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        auth.forget_runtime_state()
        run(auth.init_db())
        for owner in SCHEMA_OWNERS:
            with closing(db(owner)):
                pass

    @staticmethod
    def indexes(conn: sqlite3.Connection) -> set[str]:
        return {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL"
            )
        }

    @staticmethod
    def plan(conn: sqlite3.Connection, sql: str, params: tuple) -> str:
        return "\n".join(
            row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + sql, params)
        )


class IndexTest(SchemaCase):
    def test_every_expected_index_exists_and_the_old_one_is_gone(self):
        with closing(db()) as conn:
            present = self.indexes(conn)
        self.assertEqual(EXPECTED_INDEXES - present, set(), "indexen die ontbreken")
        self.assertNotIn(
            "image_cache_todo",
            present,
            "de index op fetched_at gebruikte geen enkele query",
        )

    def test_an_older_database_gets_the_new_indexes_at_the_start(self):
        # Een database van voor 4.6.1: wel de tabellen, niet de indexen, en de oude index op image_cache nog wel.
        with closing(db()) as conn, conn:
            for name in EXPECTED_INDEXES - {
                "offers_store",
                "offer_history_open",
                "offer_history_product",
                "audit_log_corr",
                "audit_log_service",
            }:
                conn.execute(f"DROP INDEX IF EXISTS {name}")
            conn.execute(
                "CREATE INDEX image_cache_todo ON image_cache (fetched_at, seen_at)"
            )
        auth.forget_runtime_state()  # zoals een nieuwe start van de app
        for owner in SCHEMA_OWNERS:
            with closing(db(owner)):
                pass
        run(auth.init_db())
        with closing(db()) as conn:
            present = self.indexes(conn)
        self.assertEqual(EXPECTED_INDEXES - present, set())
        self.assertNotIn("image_cache_todo", present)

    def test_the_hot_queries_use_their_index(self):
        with closing(db()) as conn:
            conn.create_function("ms_fold", 1, product_links.fold, deterministic=True)
            for sql, params, index in HOT_QUERIES:
                with self.subTest(sql=sql):
                    self.assertIn(
                        f"USING INDEX {index}",
                        self.plan(conn, sql, params).replace(
                            "USING COVERING INDEX", "USING INDEX"
                        ),
                    )

    def test_the_rebuilt_offers_table_keeps_both_indexes(self):
        # `offers._drop_offers_key` bouwt een tabel van voor 2.84.0 opnieuw op; de indexen komen daarna.
        with closing(db()) as conn, conn:
            conn.execute("DROP TABLE offers")
            conn.execute(
                offers.OFFERS_TABLE.format(name="offers").replace(
                    "CREATE TABLE IF NOT EXISTS offers (", "CREATE TABLE offers (", 1
                )
            )
        auth.forget_runtime_state()
        with closing(db(offers)) as conn:
            self.assertLessEqual({"offers_store", "offers_product"}, self.indexes(conn))


class OncePerProcessTest(SchemaCase):
    def test_the_schema_runs_once_per_process_and_database(self):
        calls: list[str] = []
        original = offers._setup

        async def counting(conn):
            calls.append("setup")
            await original(conn)

        with mock.patch.object(offers, "_setup", counting):
            for _ in range(3):
                with closing(db(offers)):
                    pass
            self.assertEqual(calls, [], "het schema stond al klaar sinds setUp")
            auth.forget_runtime_state()
            with closing(db(offers)):
                pass
            self.assertEqual(
                calls, ["setup"], "na een nieuwe start loopt de opzet precies een keer"
            )

    def test_a_factory_reset_gets_a_fresh_schema(self):
        # `factory_reset.wipe_data` wist de database en roept `forget_runtime_state`: de volgende verbinding maakt alles opnieuw.
        os.remove(auth.db_path())
        auth.forget_runtime_state()
        run(auth.init_db())
        with closing(db(audit)) as conn:
            self.assertIn(
                "audit_log",
                {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                },
            )
            self.assertIn("audit_log_ts", self.indexes(conn))


if __name__ == "__main__":
    unittest.main()
