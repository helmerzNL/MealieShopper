"""De afbeeldingencache (mealieshopper/image_cache.py): winkelfoto's een keer ophalen en daarna van schijf serveren."""

import io
import os
import tempfile
import unittest
from contextlib import closing
from unittest import mock

from PIL import Image

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

from mealieshopper import ah, auth, image_cache

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


class FetchCase(Case):
    """Nooit echt het internet op: `served` zegt per adres wat de winkel teruggeeft als (status, type, inhoud)."""

    def setUp(self):
        super().setUp()
        self.served: dict[str, tuple[int, str, bytes]] = {}
        get = mock.patch.object(image_cache.requests.Session, "get", side_effect=self.get_response, autospec=True)
        self.get = get.start()
        self.addCleanup(get.stop)
        sleep = mock.patch.object(image_cache.time, "sleep")
        self.sleep = sleep.start()
        self.addCleanup(sleep.stop)
        image_cache._reset_pace()

    def serve(self, url, status, content_type, data):
        self.served[url] = (status, content_type, data)

    def get_response(self, session, url, **kwargs):
        status, content_type, data = self.served.get(url, (404, "text/plain", b"nope"))
        response = mock.Mock()
        response.status_code = status
        response.headers = {"Content-Type": content_type}
        response.iter_content = lambda size: (data[i : i + size] for i in range(0, len(data), size))
        response.__enter__ = lambda self_: self_
        response.__exit__ = lambda *args: False
        return response


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


class FetchTest(FetchCase):
    def test_a_fetch_stores_the_file_and_the_row_with_browser_headers(self):
        self.serve(AH, 200, "image/webp", picture(8, 8, "WEBP"))
        row = image_cache.fetch(AH, now=NOW)
        self.assertEqual((row["content_type"], row["bytes"], row["width"], row["height"], row["fetched_at"]), ("image/webp", len(picture(8, 8, "WEBP")), 8, 8, NOW))
        self.assertTrue((image_cache.images_dir() / row["path"]).is_file())
        self.assertRegex(row["path"], r"^[0-9a-f]{2}/[0-9a-f]{64}\.webp$")
        headers = self.get.call_args.kwargs["headers"]
        self.assertEqual(headers["User-Agent"], ah.AH_BROWSER_USER_AGENT)
        self.assertEqual(headers["Referer"], "https://www.ah.nl/")
        self.assertEqual(headers["Sec-Fetch-Dest"], "image")
        self.assertEqual(headers["Sec-Fetch-Site"], "same-site")
        self.assertEqual(headers["Accept"], "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8")
        self.assertEqual(headers["Accept-Language"], "nl-NL,nl;q=0.9,en;q=0.8")

    def test_the_second_fetch_of_the_same_address_does_not_go_to_the_store(self):
        self.serve(AH, 200, "image/png", picture(4, 4))
        first = image_cache.fetch(AH, now=NOW)
        second = image_cache.fetch(AH, now=NOW)
        self.assertEqual((first["path"], self.get.call_count), (second["path"], 1))
        self.assertEqual(image_cache.stats(), {"count": 1, "bytes": len(picture(4, 4))})

    def test_html_svg_and_too_large_are_refused_and_counted(self):
        self.serve(AH, 200, "text/html; charset=utf-8", b"<html>oeps</html>")
        self.assertIsNone(image_cache.fetch(AH, now=NOW))
        svg = "https://static.ah.nl/dam/svg"
        self.serve(svg, 200, "image/svg+xml", b"<svg/>")
        self.assertIsNone(image_cache.fetch(svg, now=NOW))
        big = "https://static.ah.nl/dam/big"
        self.serve(big, 200, "image/png", b"x" * (image_cache.MAX_DOWNLOAD_BYTES + 1))
        self.assertIsNone(image_cache.fetch(big, now=NOW))
        rows = {row["url"]: row for row in image_cache._rows()}
        self.assertEqual((rows[AH]["attempts"], rows[AH]["last_attempt_at"]), (1, NOW))
        self.assertIn("text/html", rows[AH]["error"])
        self.assertEqual((rows[svg]["attempts"], rows[big]["attempts"]), (1, 1))
        self.assertFalse(image_cache.images_dir().exists() and any(image_cache.images_dir().rglob("*")))

    def test_a_403_pauses_the_host_for_twelve_hours_and_a_404_counts_three_times(self):
        self.serve(AH, 403, "text/html", b"blocked")
        self.assertIsNone(image_cache.fetch(AH, now=NOW))
        self.assertEqual(image_cache.paused_until("static.ah.nl"), "2026-11-04T15:00:00+00:00")
        other = "https://static.ah.nl/dam/other"
        self.serve(other, 200, "image/png", picture(4, 4))
        self.assertIsNone(image_cache.fetch(other, now=NOW))
        self.assertEqual(self.get.call_count, 1, "tijdens de pauze geen verzoek")
        self.assertIsNotNone(image_cache.fetch(other, now="2026-11-04T15:00:01+00:00"))
        gone = "https://static.ah.nl/dam/gone"
        self.assertIsNone(image_cache.fetch(gone, now="2026-11-04T15:00:02+00:00"))
        self.assertEqual({row["url"]: row["attempts"] for row in image_cache._rows()}[gone], 3)

    def test_failures_wait_a_day_then_a_week_then_forever(self):
        hours = lambda n: f"2026-11-{4 + n // 24:02d}T{n % 24:02d}:00:00+00:00"  # noqa: E731
        row = {"path": None, "attempts": 1, "last_attempt_at": NOW}
        self.assertFalse(image_cache.due(row, hours(26)))
        self.assertTrue(image_cache.due(row, hours(28)))
        row = {"path": None, "attempts": 3, "last_attempt_at": NOW}
        self.assertFalse(image_cache.due(row, "2026-11-10T03:00:00+00:00"))
        self.assertTrue(image_cache.due(row, "2026-11-12T03:00:00+00:00"))
        self.assertFalse(image_cache.due({"path": None, "attempts": 10, "last_attempt_at": NOW}, "2027-11-04T03:00:00+00:00"))
        self.assertFalse(image_cache.due({"path": "ab/x.png", "attempts": 0, "last_attempt_at": None}, NOW))
        self.assertTrue(image_cache.due({"path": None, "attempts": 0, "last_attempt_at": None}, NOW))

    def test_a_failed_address_is_not_tried_again_before_it_is_due(self):
        self.serve(AH, 200, "text/html", b"oeps")
        image_cache.fetch(AH, now=NOW)
        self.serve(AH, 200, "image/png", picture(4, 4))
        self.assertIsNone(image_cache.fetch(AH, now="2026-11-04T10:00:00+00:00"))
        self.assertEqual(self.get.call_count, 1)
        self.assertIsNotNone(image_cache.fetch(AH, now="2026-11-05T10:00:00+00:00"))

    def test_two_requests_for_the_same_missing_image_share_one_download(self):
        import threading

        release = threading.Event()
        started = threading.Event()
        data = picture(4, 4)

        def slow(session, url, **kwargs):
            started.set()
            release.wait(5)
            return self.get_response(session, url, **kwargs)

        self.get.side_effect = slow
        self.serve(AH, 200, "image/png", data)
        results = []
        workers = [threading.Thread(target=lambda: results.append(image_cache.fetch(AH, now=NOW))) for _ in range(2)]
        for worker in workers:
            worker.start()
        self.assertTrue(started.wait(5))
        release.set()
        for worker in workers:
            worker.join(5)
        self.assertEqual(self.get.call_count, 1)
        self.assertEqual([row["path"] for row in results], [results[0]["path"]] * 2)

    def test_the_pace_sleeps_between_two_requests_on_the_same_host(self):
        other = "https://static.ah.nl/dam/other"
        self.serve(AH, 200, "image/png", picture(4, 4))
        self.serve(other, 200, "image/png", picture(4, 4))
        image_cache.fetch(AH, now=NOW)
        image_cache.fetch(other, now=NOW)
        waited = [call.args[0] for call in self.sleep.call_args_list]
        self.assertEqual(len(waited), 1, "alleen tussen twee verzoeken op dezelfde host")
        self.assertTrue(0.3 <= waited[0] <= 0.5, waited)
        third = "https://static.ah.nl/dam/third"
        self.serve(third, 200, "image/png", picture(4, 4))
        image_cache.fetch(third, now=NOW, background=True)
        self.assertTrue(2.0 <= self.sleep.call_args.args[0] <= 5.0, self.sleep.call_args)

    def test_the_images_folder_is_created_on_first_use(self):
        self.assertFalse(image_cache.images_dir().exists())
        self.serve(AH, 200, "image/png", picture(4, 4))
        image_cache.fetch(AH, now=NOW)
        self.assertTrue(image_cache.images_dir().is_dir())


if __name__ == "__main__":
    unittest.main()
