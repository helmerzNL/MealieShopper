"""De afbeeldingencache (mealieshopper/image_cache.py): winkelfoto's een keer ophalen en daarna van schijf serveren."""

import io
import os
import tempfile
import unittest
from pathlib import Path
from contextlib import closing
from unittest import mock

from PIL import Image

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

from mealieshopper import ah, ah_orders, auth, image_cache, offers  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402

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
        self.redirects: dict[str, str] = {}
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
        response.headers = {"Content-Type": content_type, **({"Location": self.redirects[url]} if url in self.redirects else {})}
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

    def test_a_token_only_decodes_to_a_strict_store_address(self):
        # Een token komt van de browser en is dus van iedereen. Dezelfde eisen als aan een doorverwijzing: https, en geen adres dat
        # de ene parser als static.ah.nl leest en de andere (urllib3, die het verzoek verstuurt) als iets anders.
        for url in ("http://static.ah.nl/dam/plain", "https://user@static.ah.nl/dam/x", "https://static.ah.nl\t/dam/x"):
            with self.subTest(url=url):
                self.assertEqual(image_cache.store_for(url), "ah", "de host alleen laat het door")
                self.assertFalse(image_cache.safe_url(url))
                self.assertIsNone(image_cache.url_for(image_cache.token_for(url)))
        self.assertTrue(image_cache.safe_url(AH))
        self.assertFalse(image_cache.safe_url("https://mealie.local/x.png"))

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

    def test_a_fetch_of_an_address_that_only_passes_the_host_check_sends_nothing(self):
        for url in ("http://static.ah.nl/dam/plain", "https://user@static.ah.nl/dam/x"):
            with self.subTest(url=url):
                self.serve(url, 200, "image/png", picture(4, 4))
                self.assertIsNone(image_cache.fetch(url, now=NOW))
                self.assertIsNone(image_cache.lookup(url), "ook niet geregistreerd")
        self.get.assert_not_called()

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

    def test_a_photo_that_picnic_does_not_have_is_missing_and_does_not_pause_the_host(self):
        # #236: Picnic bewaart zijn foto's in Amazon S3. Een foto die er niet is, geeft daar 403 AccessDenied in XML, geen 404. Dat
        # zette de hele host twaalf uur stil, zodat na een recept zonder foto ook de foto's van alle andere recepten wegbleven.
        base = "https://storefront-prod.nl.picnicinternational.com/static/images/recipes"
        gone, other = f"{base}/ac1b02/medium.png", f"{base}/c09b4e/medium.png"
        denied = b'<?xml version="1.0" encoding="UTF-8"?>\n<Error><Code>AccessDenied</Code><Message>Access Denied</Message><RequestId>X</RequestId></Error>'
        self.serve(gone, 403, "application/xml", denied)
        self.serve(other, 200, "image/png", picture(4, 4))
        self.assertIsNone(image_cache.fetch(gone, now=NOW))
        self.assertIsNone(image_cache.paused_until("storefront-prod.nl.picnicinternational.com"))
        self.assertIsNotNone(image_cache.fetch(other, now=NOW), "de andere foto's van Picnic komen gewoon")
        row = {row["url"]: row for row in image_cache._rows()}[gone]
        self.assertEqual(row["attempts"], 3, "telt als een 404: een week wachten")
        self.assertIn("bestaat niet", row["error"])

    def test_a_403_that_is_not_an_s3_error_still_pauses_the_host(self):
        picnic = "https://storefront-prod.nl.picnicinternational.com/static/images/abc/medium.png"
        for content_type, body in (
            ("application/xml", b"<Error><Code>RequestLimitExceeded</Code></Error>"),  # andere XML: geen ontbrekend bestand
            ("text/html", b"<html><body><Error><Code>AccessDenied</Code></Error></body></html>"),  # alleen XML telt
        ):
            with self.subTest(content_type=content_type):
                auth.set_setting(image_cache.SETTING_PAUSED + "storefront-prod.nl.picnicinternational.com", "")
                with closing(image_cache._connect()) as conn, conn:
                    conn.execute("DELETE FROM image_cache")
                self.serve(picnic, 403, content_type, body)
                self.assertIsNone(image_cache.fetch(picnic, now=NOW))
                self.assertEqual(image_cache.paused_until("storefront-prod.nl.picnicinternational.com"), "2026-11-04T15:00:00+00:00")

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

    def test_a_request_that_saw_the_photo_missing_just_before_it_arrived_does_not_fetch_it_again(self):
        # De volgorde die de test hierboven af en toe liet zien: verzoek B kijkt (geen foto), verzoek A haalt hem op en is klaar
        # voordat B het slot pakt. B ziet dan geen lopende download meer en wordt zelf eigenaar; het moet eerst opnieuw kijken.
        self.serve(AH, 200, "image/png", picture(4, 4))
        first = image_cache.fetch(AH, now=NOW)  # verzoek A
        real_lookup = image_cache.lookup
        stale = {**first, "path": None, "fetched_at": None}  # wat B zag, net voordat A klaar was
        calls = []

        def lookup(url):
            calls.append(url)
            return stale if len(calls) == 1 else real_lookup(url)

        with mock.patch.object(image_cache, "lookup", side_effect=lookup):
            second = image_cache.fetch(AH, now=NOW)  # verzoek B
        self.assertEqual((second["path"], self.get.call_count), (first["path"], 1))

    def test_the_pace_sleeps_between_two_requests_on_the_same_host(self):
        other = "https://static.ah.nl/dam/other"
        self.serve(AH, 200, "image/png", picture(4, 4))
        self.serve(other, 200, "image/png", picture(4, 4))
        image_cache.fetch(AH, now=NOW)
        image_cache.fetch(other, now=NOW)
        waited = [call.args[0] for call in self.sleep.call_args_list]
        self.assertEqual(len(waited), 1, "alleen tussen twee verzoeken op dezelfde host")
        self.assertTrue(0.3 <= waited[0] <= 0.5, waited)

    def test_the_pace_never_sleeps_while_holding_the_lock_or_a_slot(self):
        # Een thread die wacht op zijn beurt bij een host mag een verzoek aan een andere host (of een ander verzoek) niet ophouden.
        seen = []

        def sleeping(seconds):
            free = image_cache._PACE_LOCK.acquire(blocking=False)
            if free:
                image_cache._PACE_LOCK.release()
            seen.append((free, image_cache._SLOTS._value))

        self.sleep.side_effect = sleeping
        other = "https://static.ah.nl/dam/other"
        self.serve(AH, 200, "image/png", picture(4, 4))
        self.serve(other, 200, "image/png", picture(4, 4))
        image_cache.fetch(AH, now=NOW)
        image_cache.fetch(other, now=NOW)
        self.assertEqual(seen, [(True, image_cache.CONCURRENCY)], "tijdens het slapen: lock vrij en geen slot bezet")

    def test_a_cache_hit_does_not_write_to_the_database(self):
        self.serve(AH, 200, "image/png", picture(4, 4))
        image_cache.fetch(AH, now=NOW)
        with mock.patch.object(image_cache, "register") as register, mock.patch.object(image_cache, "_record_success") as success:
            self.assertIsNotNone(image_cache.fetch(AH, now=NOW))
        register.assert_not_called()
        success.assert_not_called()

    def test_a_disk_error_is_recorded_as_a_failure_and_leaves_no_part_file(self):
        self.serve(AH, 200, "image/png", picture(4, 4))
        with mock.patch.object(image_cache.os, "replace", side_effect=PermissionError("alleen-lezen")):
            self.assertIsNone(image_cache.fetch(AH, now=NOW))
        row = image_cache.lookup(AH)
        self.assertEqual((row["attempts"], row["path"]), (1, None))
        self.assertIn("alleen-lezen", row["error"])
        self.assertEqual(list(image_cache.images_dir().rglob("*.part")), [])
        self.assertEqual(self.get.call_count, 1)
        self.assertIsNone(image_cache.fetch(AH, now="2026-11-04T04:00:00+00:00"), "wacht nu een dag, geen tweede download")
        self.assertEqual(self.get.call_count, 1)

    def test_a_waiting_request_gives_up_after_ten_seconds(self):
        timeouts = []

        class Stuck:
            def wait(self, timeout=None):
                timeouts.append(timeout)
                return False

        with mock.patch.dict(image_cache._INFLIGHT, {AH: Stuck()}):
            self.assertIsNone(image_cache.fetch(AH, now=NOW))
        self.assertEqual(timeouts, [10])
        self.get.assert_not_called()

    def test_a_redirect_is_followed_only_to_a_store_host(self):
        # Een winkel-CDN mag doorverwijzen naar een ander winkeladres, nooit naar iets anders (een intern adres bijvoorbeeld).
        moved = "https://static.ah.nl/dam/product/moved"
        self.serve(moved, 302, "text/plain", b"")
        self.redirects[moved] = "https://static.ah.nl/dam/product/final"
        self.serve("https://static.ah.nl/dam/product/final", 200, "image/png", picture(4, 4))
        row = image_cache.fetch(moved, now=NOW)
        self.assertEqual((row["url"], row["width"]), (moved, 4))
        self.assertFalse(self.get.call_args_list[0].kwargs.get("allow_redirects", True), "requests mag zelf geen redirect volgen")
        bad = "https://static.ah.nl/dam/product/bad"
        self.serve(bad, 302, "text/plain", b"")
        self.redirects[bad] = "http://127.0.0.1:8000/api/system/images"
        self.assertIsNone(image_cache.fetch(bad, now=NOW))
        self.assertNotIn("127.0.0.1", " ".join(call.args[1] for call in self.get.call_args_list))
        loop = "https://static.ah.nl/dam/product/loop"
        self.serve(loop, 302, "text/plain", b"")
        self.redirects[loop] = loop
        self.assertIsNone(image_cache.fetch(loop, now=NOW))
        self.assertLessEqual(sum(1 for call in self.get.call_args_list if call.args[1] == loop), image_cache.MAX_REDIRECTS + 1)

    def test_a_redirect_target_is_checked_with_the_parser_that_sends_the_request(self):
        # Een adres dat de ene parser als static.ah.nl leest en de andere als iets anders, en een stap terug naar http: nooit.
        for location in (
            "https://static.ah.nl\\@127.0.0.1/dam/x",
            "https://static.ah.nl@127.0.0.1/dam/x",
            "https://static.ah.nl /dam/x",
            "https://static.ah.nl%0d%0a/dam/x",
            "http://static.ah.nl/dam/plain",
        ):
            with self.subTest(location=location):
                self.get.reset_mock()
                moved = f"https://static.ah.nl/dam/product/moved-{abs(hash(location))}"
                self.serve(moved, 302, "text/plain", b"")
                self.redirects[moved] = location
                self.serve(location, 200, "image/png", picture(4, 4))
                self.assertIsNone(image_cache.fetch(moved, now=NOW))
                self.assertEqual([call.args[1] for call in self.get.call_args_list], [moved], "na de doorverwijzing geen verzoek meer")

    def test_the_images_folder_is_created_on_first_use(self):
        self.assertFalse(image_cache.images_dir().exists())
        self.serve(AH, 200, "image/png", picture(4, 4))
        image_cache.fetch(AH, now=NOW)
        self.assertTrue(image_cache.images_dir().is_dir())


class RoutesTest(FetchCase):
    def setUp(self):
        super().setUp()
        self.client = create_app().test_client()

    def test_the_route_serves_the_cached_file_with_a_long_cache_header(self):
        self.serve(AH, 200, "image/webp", picture(8, 8, "WEBP"))
        reply = self.client.get(f"/api/images/{image_cache.token_for(AH)}")
        self.assertEqual((reply.status_code, reply.content_type, reply.headers["Cache-Control"]), (200, "image/webp", "private, max-age=31536000, immutable"))
        self.assertEqual(reply.data, picture(8, 8, "WEBP"))
        self.assertEqual(reply.headers["X-Content-Type-Options"], "nosniff")
        again = self.client.get(f"/api/images/{image_cache.token_for(AH)}", headers={"If-None-Match": reply.headers["ETag"]})
        self.assertEqual(again.status_code, 304)
        self.assertEqual(self.get.call_count, 1)

    def test_unknown_tokens_and_hosts_give_404_without_a_request(self):
        for token in ("%%%", image_cache.token_for("https://mealie.local/x.png"), image_cache.token_for("ftp://static.ah.nl/x")):
            self.assertEqual(self.client.get(f"/api/images/{token}").status_code, 404)
        self.get.assert_not_called()

    def test_a_paused_host_gives_404_without_a_request(self):
        image_cache._pause("static.ah.nl", NOW)
        with mock.patch.object(image_cache, "_now", return_value=NOW):
            self.assertEqual(self.client.get(f"/api/images/{image_cache.token_for(AH)}").status_code, 404)
        self.get.assert_not_called()

    def test_a_failed_download_gives_404(self):
        self.serve(AH, 200, "text/html", b"<html>")
        self.assertEqual(self.client.get(f"/api/images/{image_cache.token_for(AH)}").status_code, 404)

    def test_the_system_page_sees_how_big_the_cache_is(self):
        self.assertEqual(self.client.get("/api/system/images").get_json(), {"count": 0, "bytes": 0})
        self.serve(AH, 200, "image/png", picture(4, 4))
        self.client.get(f"/api/images/{image_cache.token_for(AH)}")
        self.assertEqual(self.client.get("/api/system/images").get_json(), {"count": 1, "bytes": len(picture(4, 4))})

    def test_the_container_serves_with_four_threads_so_a_slow_store_does_not_block_the_app(self):
        dockerfile = (Path(__file__).resolve().parent.parent / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn('"--threads", "4"', dockerfile)

    def test_the_route_needs_a_login_like_the_rest_of_the_api(self):
        with mock.patch.object(auth, "auth_required", return_value=True), mock.patch.object(auth, "current_user", return_value=None):
            self.assertEqual(self.client.get(f"/api/images/{image_cache.token_for(AH)}").status_code, 401)
        self.get.assert_not_called()


class PrefetchTest(FetchCase):
    def setUp(self):
        super().setUp()
        env = mock.patch.dict(os.environ, {"MEALIESHOPPER_IMAGE_PREFETCH": "true"})
        env.start()
        self.addCleanup(env.stop)

    def known(self):
        with closing(ah_orders._connect()) as conn, conn:
            conn.execute(
                "INSERT INTO ah_order_items (order_id, position, product_id, title, image) VALUES (1, 0, 10, 'Melk', ?)",
                ("https://static.ah.nl/dam/product/melk",),
            )
            conn.execute("INSERT INTO ah_receipt_products (receipt_product_id, product_id, title, checked_at, image) VALUES (1, 10, 'Melk', 'x', 'https://static.ah.nl/dam/product/bon')")
        with closing(offers._connect()) as conn, conn:
            conn.execute(
                "INSERT INTO offers (store, product_id, position, title, image) VALUES ('ah', '1', 0, 'Kaas', 'https://static.ah.nl/dam/product/kaas')"
            )

    def test_collect_known_finds_the_addresses_in_every_table(self):
        self.known()
        self.assertEqual(image_cache.collect_known(now=NOW), 3)
        self.assertEqual(image_cache.collect_known(now=NOW), 0)
        self.assertEqual(sorted(row["url"].rsplit("/", 1)[1] for row in image_cache._rows()), ["bon", "kaas", "melk"])

    def test_prefetch_fetches_the_newest_first_up_to_the_limit_and_skips_what_waits(self):
        urls = [f"https://static.ah.nl/dam/product/{n}" for n in range(3)]
        for n, url in enumerate(urls):
            image_cache.register(url, now=f"2026-11-0{n + 1}T00:00:00+00:00")
            self.serve(url, 200, "image/png", picture(4, 4))
        image_cache._record_failure(urls[1], "2026-11-04T02:00:00+00:00", 1, "oeps")  # wacht nog een dag
        result = image_cache.prefetch(limit=2, now=NOW)
        self.assertEqual(result, {"fetched": 2, "failed": 0, "skipped": 1, "paused": 0})
        self.assertEqual([call.args[1] for call in self.get.call_args_list], [urls[2], urls[0]], "nieuwste eerst, de wachtende overgeslagen")
        self.assertTrue(any(2.0 <= call.args[0] <= 5.0 for call in self.sleep.call_args_list), "het rustige tempo van de achtergrond")
        result = image_cache.prefetch(limit=2, now=NOW)
        self.assertEqual(result, {"fetched": 0, "failed": 0, "skipped": 1, "paused": 0})

    def test_prefetch_rests_two_to_five_seconds_between_fetches_but_not_before_the_first(self):
        urls = [f"https://static.ah.nl/dam/product/{n}" for n in range(3)]
        for url in urls:
            image_cache.register(url, now=NOW)
            self.serve(url, 200, "image/png", picture(4, 4))
        image_cache.prefetch(now=NOW)
        rests = [call.args[0] for call in self.sleep.call_args_list if call.args[0] >= 2.0]
        self.assertEqual(len(rests), 2, "een rustpauze tussen elke twee foto's")
        self.assertTrue(all(2.0 <= rest <= 5.0 for rest in rests), rests)

    def test_only_one_prefetch_round_runs_at_a_time(self):
        image_cache.register(AH, now=NOW)
        self.serve(AH, 200, "image/png", picture(4, 4))
        self.assertTrue(image_cache._PREFETCH_RUNNING.acquire(blocking=False))
        try:
            self.assertEqual(image_cache.prefetch(now=NOW), {"fetched": 0, "failed": 0, "skipped": 0, "paused": 0, "running": True})
        finally:
            image_cache._PREFETCH_RUNNING.release()
        self.get.assert_not_called()
        self.assertEqual(image_cache.prefetch(now=NOW)["fetched"], 1)

    def test_collect_known_skips_an_address_that_only_passes_the_host_check(self):
        with closing(ah_orders._connect()) as conn, conn:
            for position, url in enumerate(("http://static.ah.nl/dam/plain", "https://user@static.ah.nl/dam/x")):
                conn.execute("INSERT INTO ah_order_items (order_id, position, product_id, title, image) VALUES (1, ?, ?, 'Melk', ?)", (position, 10 + position, url))
        self.assertEqual(image_cache.collect_known(now=NOW), 0)
        self.assertEqual(image_cache._rows(), [])

    def test_collect_known_survives_a_table_that_has_no_image_column_yet(self):
        with closing(auth.connect()) as conn, conn:
            conn.execute("CREATE TABLE ah_receipt_products (receipt_product_id INTEGER PRIMARY KEY, product_id INTEGER, title TEXT, checked_at TEXT NOT NULL)")
            conn.execute("INSERT INTO ah_receipt_products VALUES (1, 10, 'Melk', 'x')")
        self.assertEqual(image_cache.collect_known(now=NOW), 0)

    def test_prefetch_counts_failures_and_paused_hosts(self):
        image_cache.register("https://static.ah.nl/dam/product/x", now=NOW)
        image_cache.register("https://crispapp.nl/blob/y", now=NOW)
        self.serve("https://static.ah.nl/dam/product/x", 200, "text/html", b"oeps")
        image_cache._pause("crispapp.nl", NOW)
        self.assertEqual(image_cache.prefetch(now=NOW), {"fetched": 0, "failed": 1, "skipped": 1, "paused": 1})

    def test_prefetch_stops_at_the_deadline_and_can_be_switched_off(self):
        image_cache.register(AH, now=NOW)
        self.serve(AH, 200, "image/png", picture(4, 4))
        self.assertEqual(image_cache.prefetch(deadline_seconds=0, now=NOW)["fetched"], 0)
        with mock.patch.dict(os.environ, {"MEALIESHOPPER_IMAGE_PREFETCH": "false"}):
            self.assertFalse(image_cache.enabled())
            self.assertIsNone(image_cache.prefetch_nightly())
            with mock.patch.object(image_cache.threading, "Thread") as thread:
                image_cache.prefetch_async()
            thread.assert_not_called()
        self.get.assert_not_called()

    def test_the_nightly_round_runs_and_is_written_to_the_audit_log(self):
        image_cache.register(AH, now=NOW)
        self.serve(AH, 200, "image/png", picture(4, 4))
        with mock.patch.object(image_cache.audit, "record") as record:
            result = image_cache.prefetch_nightly()
        self.assertEqual(result["fetched"], 1)
        self.assertEqual((record.call_args.kwargs["service"], record.call_args.kwargs["action"], record.call_args.kwargs["detail"]), ("images", "prefetch", result))

    def test_prefetch_async_runs_a_small_round_in_a_daemon_thread(self):
        image_cache.register(AH, now=NOW)
        self.serve(AH, 200, "image/png", picture(4, 4))
        with mock.patch.object(image_cache.threading, "Thread", wraps=image_cache.threading.Thread) as thread:
            image_cache.prefetch_async(limit=40)
            self.assertTrue(thread.call_args.kwargs["daemon"])
        for worker in image_cache._prefetch_threads():
            worker.join(5)
        self.assertEqual(self.get.call_count, 1)


if __name__ == "__main__":
    unittest.main()
