"""De afbeeldingencache (mealieshopper/image_cache.py): winkelfoto's een keer ophalen en daarna van schijf serveren."""

import io
import os
import tempfile
import unittest
from contextlib import closing
from unittest import mock

from PIL import Image

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

from mealieshopper import auth, image_cache

AH = "https://static.ah.nl/dam/product/AHI_4354523130323030333238?revLabel=1&rendition=800x800_WEBP&fileType=binary"
NOW = "2026-11-04T03:00:00+00:00"


def picture(width, height, kind="PNG"):
    out = io.BytesIO()
    Image.new("RGB", (width, height), (200, 30, 30)).save(out, kind)
    return out.getvalue()


class Case(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(
            os.environ,
            {"MEALIESHOPPER_DATA_DIR": tmp.name, "PASSKEY_AUTH_ENABLED": "false"},
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        auth.init_db()


class HostsAndTokensTest(Case):
    def test_only_store_hosts_are_known(self):
        self.assertEqual(image_cache.store_for(AH), "ah")
        self.assertEqual(
            image_cache.store_for("https://images.jumbo.com/x.png"), "jumbo"
        )
        self.assertEqual(image_cache.store_for("https://jumbo.com/x.png"), "jumbo")
        self.assertEqual(
            image_cache.store_for(
                "https://storefront-prod.nl.picnicinternational.com/static/images/1/medium.png"
            ),
            "picnic",
        )
        self.assertEqual(
            image_cache.store_for("https://crispapp.nl/blob/abc?thumb=200"), "crisp"
        )
        for url in (
            "https://evil.example/static.ah.nl/x",
            "https://notjumbo.com/x",
            "ftp://static.ah.nl/x",
            "https://mealie.local/x.png",
            "",
            None,
        ):
            self.assertIsNone(image_cache.store_for(url), url)

    def test_the_token_is_url_safe_and_round_trips(self):
        token = image_cache.token_for(AH)
        self.assertNotRegex(token, r"[+/=]")
        self.assertEqual(image_cache.url_for(token), AH)
        self.assertIsNone(image_cache.url_for("%%%"))
        self.assertIsNone(
            image_cache.url_for(image_cache.token_for("https://mealie.local/x.png"))
        )

    def test_register_keeps_one_row_per_address_and_refreshes_seen_at(self):
        first = image_cache.register(AH, now="2026-11-04T01:00:00+00:00")
        second = image_cache.register(AH, now="2026-11-05T01:00:00+00:00")
        self.assertEqual(first, second)
        with closing(image_cache._connect()) as conn:
            row = conn.execute(
                "SELECT host, seen_at, attempts, path FROM image_cache"
            ).fetchone()
        self.assertEqual(
            (row["host"], row["seen_at"], row["attempts"], row["path"]),
            ("static.ah.nl", "2026-11-05T01:00:00+00:00", 0, None),
        )

    def test_the_files_live_in_images_next_to_the_database(self):
        self.assertEqual(image_cache.images_dir(), auth.data_dir() / "images")


if __name__ == "__main__":
    unittest.main()
