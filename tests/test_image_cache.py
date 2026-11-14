"""De afbeeldingencache (mealieshopper/image_cache.py): winkelfoto's een keer ophalen en daarna van schijf serveren."""

import asyncio
import io
import os
import tempfile
import threading
import time
import unittest
from contextlib import closing, suppress
from pathlib import Path
from typing import Any
from unittest import mock

import httpx
import respx
from PIL import Image

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

from helpers import AppTestCase, db, in_db, run, start_app  # noqa: E402

from mealieshopper import ah, ah_orders, auth, image_cache, offers, readers, tasks  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402

AH = "https://static.ah.nl/dam/product/AHI_4354523130323030333238?revLabel=1&rendition=800x800_WEBP&fileType=binary"
JUMBO = "https://images.jumbo.com/product/x.png"
CRISP = "https://crispapp.nl/blob/abc?thumb=200"
NOW = "2026-11-04T03:00:00+00:00"


def picture(width: int, height: int, kind: str = "PNG") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), (200, 30, 30)).save(out, kind)
    return out.getvalue()


async def fetch(url: str, now: str | None = NOW) -> dict[str, Any] | None:
    return await image_cache.fetch(url, now=now)


async def rows() -> list[dict[str, Any]]:
    return await image_cache._rows()


class Case(AppTestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(
            os.environ,
            {"MEALIESHOPPER_DATA_DIR": tmp.name, "PASSKEY_AUTH_ENABLED": "false"},
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        run(auth.init_db())


class FetchCase(Case):
    """Nooit echt het internet op: `served` zegt per adres wat de winkel teruggeeft als (status, type, inhoud). `self.get` is de
    respx-route die elk verzoek opvangt, `self.sleep` de pauze (`image_cache._wait`) die nooit echt slaapt."""

    def setUp(self) -> None:
        super().setUp()
        self.served: dict[str, tuple[int, str, bytes]] = {}
        self.redirects: dict[str, str] = {}
        router: respx.MockRouter = respx.mock(assert_all_called=False)
        router.start()
        self.addCleanup(router.stop)
        self.get: respx.Route = router.route()
        self.get.side_effect = self.respond
        sleep = mock.patch.object(image_cache, "_wait", new_callable=mock.AsyncMock)
        self.sleep: mock.AsyncMock = sleep.start()
        self.addCleanup(sleep.stop)
        image_cache._reset_pace()

    def serve(self, url: str, status: int, content_type: str, data: bytes) -> None:
        self.served[url] = (status, content_type, data)

    def respond(self, request: httpx.Request) -> httpx.Response:
        url: str = str(request.url)
        status, content_type, data = self.served.get(url, (404, "text/plain", b"nope"))
        headers: dict[str, str] = {"Content-Type": content_type, **({"Location": self.redirects[url]} if url in self.redirects else {})}
        return httpx.Response(status, headers=headers, content=data)

    def urls(self) -> list[str]:
        return [str(call.request.url) for call in self.get.calls]


class StoreOffTheLoopTest(FetchCase):
    async def test_the_file_is_written_off_the_event_loop(self) -> None:
        """Een foto kan 10 MB zijn: het schrijven naar schijf loopt in een thread, zoals het meten met Pillow."""
        loop_thread: int = threading.get_ident()
        seen: list[int] = []
        real = image_cache._store_file

        def spy(*args: Any) -> tuple[str, str]:
            seen.append(threading.get_ident())
            return real(*args)

        self.serve(AH, 200, "image/png", picture(40, 40))
        with mock.patch.object(image_cache, "_store_file", side_effect=spy):
            row = await fetch(AH)
        self.assertTrue(row and row["path"])
        self.assertEqual(len(seen), 1)
        self.assertNotEqual(seen[0], loop_thread)


class LookupThreadsTest(Case):
    async def test_twenty_lookups_start_at_most_a_few_threads(self) -> None:
        """Taak 10b: een pagina met aanbiedingen vraagt honderden foto's, en `lookup` opende per foto een eigen `auth.connect`,
        dus een thread (op de loop wacht `Thread.start` tot die draait). Nu leest hij over `auth.read`: twintig lookups, tegelijk
        en na elkaar, starten hooguit `readers.READERS_KEPT` threads."""
        await image_cache.lookup(AH)  # het schema, een keer per database
        started: list[threading.Thread] = []
        original = threading.Thread.start

        def counted(thread: threading.Thread) -> None:
            if "_connection_worker_thread" in thread.name:
                started.append(thread)
            original(thread)

        with mock.patch.object(threading.Thread, "start", counted):
            await asyncio.gather(*(image_cache.lookup(f"{AH}?n={number}") for number in range(10)))
            for number in range(10):
                await image_cache.lookup(f"{AH}?m={number}")
        self.assertLessEqual(len(started), readers.READERS_KEPT, started)


class HostsAndTokensTest(Case):
    def test_only_store_hosts_are_known(self) -> None:
        self.assertEqual(image_cache.store_for(AH), "ah")
        self.assertEqual(image_cache.store_for("https://images.jumbo.com/x.png"), "jumbo")
        self.assertEqual(image_cache.store_for("https://jumbo.com/x.png"), "jumbo")
        self.assertEqual(
            image_cache.store_for("https://storefront-prod.nl.picnicinternational.com/static/images/1/medium.png"),
            "picnic",
        )
        self.assertEqual(image_cache.store_for("https://crispapp.nl/blob/abc?thumb=200"), "crisp")
        self.assertEqual(image_cache.store_for("https://www.lidl.nl/assets/gcp58e1.png"), "lidl")
        self.assertEqual(image_cache.store_for("https://imgproxy-retcat.assets.schwarz/abc/w:1278/x.jpg"), "lidl")
        self.assertEqual(image_cache.store_for("https://s7g10.scene7.com/is/image/aldinord/product_1219324?wid=500&hei=500"), "aldi")
        for url in (
            "https://www.lidl.nl/c/aanbiedingen/a10008785",  # de rest van de site van Lidl is geen foto
            "https://s7g10.scene7.com/is/image/anderbedrijf/x",  # scene7 is het gedeelde CDN van Adobe
            "https://lidl.nl/assets/x.png",
        ):
            self.assertIsNone(image_cache.store_for(url), url)
        for url in (
            "https://evil.example/static.ah.nl/x",
            "https://notjumbo.com/x",
            "ftp://static.ah.nl/x",
            "https://mealie.local/x.png",
            "",
            None,
        ):
            self.assertIsNone(image_cache.store_for(url), url)

    def test_the_token_is_url_safe_and_round_trips(self) -> None:
        token = image_cache.token_for(AH)
        self.assertNotRegex(token, r"[+/=]")
        self.assertEqual(image_cache.url_for(token), AH)
        self.assertIsNone(image_cache.url_for("%%%"))
        self.assertIsNone(image_cache.url_for(image_cache.token_for("https://mealie.local/x.png")))

    def test_a_token_only_decodes_to_a_strict_store_address(self) -> None:
        # Een token komt van de browser en is dus van iedereen. Dezelfde eisen als aan een doorverwijzing: https, en geen adres dat
        # de ene parser als static.ah.nl leest en de andere (httpx, die het verzoek verstuurt) als iets anders.
        for url in ("http://static.ah.nl/dam/plain", "https://user@static.ah.nl/dam/x", "https://static.ah.nl\t/dam/x"):
            with self.subTest(url=url):
                self.assertEqual(image_cache.store_for(url), "ah", "de host alleen laat het door")
                self.assertFalse(image_cache.safe_url(url))
                self.assertIsNone(image_cache.url_for(image_cache.token_for(url)))
        self.assertTrue(image_cache.safe_url(AH))
        self.assertFalse(image_cache.safe_url("https://mealie.local/x.png"))

    def test_the_check_uses_the_parser_of_httpx(self) -> None:
        # httpx leest `static.ah.nl\@127.0.0.1` als host 127.0.0.1 (en weigert een tab): wat httpx anders leest dan wij, valt af.
        self.assertEqual(httpx.URL("https://static.ah.nl\\@127.0.0.1/dam/x").host, "127.0.0.1")
        self.assertFalse(image_cache.safe_url("https://static.ah.nl\\@127.0.0.1/dam/x"))
        with mock.patch.object(image_cache.httpx, "URL", side_effect=httpx.InvalidURL("nee")):
            self.assertFalse(image_cache.safe_url(AH), "een adres dat httpx weigert, gaat nooit de deur uit")

    def test_hostile_addresses_are_refused_without_an_error(self) -> None:
        # Adressen die de oude controle (urllib3) weigerde of waarop httpx een andere host leest dan wij, of een fout geeft.
        for url in (
            "https://evil%2ecom.jumbo.com/",  # een gecodeerd teken in de host
            "https://evil.com%2f.jumbo.com/",
            "https://evil.com%23.jumbo.com/",
            "https://127.0.0.1%2f.jumbo.com/",
            "https://evil%.jumbo.com/x",
            "https://static%2eah.nl/",
            "https://münchen.jumbo.com/x",  # Unicode: httpx zet de host eerst om
            "https://MÜNCHEN.jumbo.com/x",
            "https://evil.com\u3002x.jumbo.com/x",
            "https://evil\u200b.jumbo.com/x",
            "https://static.ah.nl:99999/x",  # poort buiten 1 tot 65535: gaf eerst een OverflowError bij het verzoek
            "https://static.ah.nl:0/x",
            "https://xn--a.jumbo.com/x",  # een kapot A-label: httpx gaf een idna-fout
            "https://xn--.jumbo.com/x",
            "https://xn--ls8h.jumbo.com/x",
            "https://[::ffff:127.0.0.1].jumbo.com/x",  # urlsplit gaf een ValueError
            "https://[static.ah.nl]/x",
            "https://static.ah.nl\x7f/x",
        ):
            with self.subTest(url=url):
                self.assertFalse(image_cache.safe_url(url))
                self.assertIsNone(image_cache.url_for(image_cache.token_for(url)))
        for url in (
            AH,
            "https://STATIC.AH.NL/dam/x",
            "https://static.ah.nl:443/dam/x",
            JUMBO,
            "https://xn--mnchen-3ya.jumbo.com/x",  # een geldig A-label mag, zoals voorheen
            "https://storefront-prod.nl.picnicinternational.com/static/images/1/medium.png",
            CRISP,
        ):
            with self.subTest(url=url):
                self.assertTrue(image_cache.safe_url(url))

    def test_register_keeps_one_row_per_address_and_refreshes_seen_at(self) -> None:
        first = run(image_cache.register(AH, now="2026-11-04T01:00:00+00:00"))
        second = run(image_cache.register(AH, now="2026-11-05T01:00:00+00:00"))
        self.assertEqual(first, second)
        with closing(db()) as conn:
            row = conn.execute("SELECT host, seen_at, attempts, path FROM image_cache").fetchone()
        self.assertEqual(
            (row["host"], row["seen_at"], row["attempts"], row["path"]),
            ("static.ah.nl", "2026-11-05T01:00:00+00:00", 0, None),
        )

    def test_the_files_live_in_images_next_to_the_database(self) -> None:
        self.assertEqual(image_cache.images_dir(), auth.data_dir() / "images")


class FetchTest(FetchCase):
    async def test_a_fetch_stores_the_file_and_the_row_with_browser_headers(self) -> None:
        self.serve(AH, 200, "image/webp", picture(8, 8, "WEBP"))
        row = await fetch(AH)
        self.assertEqual(
            (row["content_type"], row["bytes"], row["width"], row["height"], row["fetched_at"]), ("image/webp", len(picture(8, 8, "WEBP")), 8, 8, NOW)
        )
        self.assertTrue((image_cache.images_dir() / row["path"]).is_file())
        self.assertRegex(row["path"], r"^[0-9a-f]{2}/[0-9a-f]{64}\.webp$")
        headers = self.get.calls.last.request.headers
        self.assertEqual(headers["User-Agent"], ah.AH_BROWSER_USER_AGENT)
        self.assertEqual(headers["Referer"], "https://www.ah.nl/")
        self.assertEqual(headers["Sec-Fetch-Dest"], "image")
        self.assertEqual(headers["Sec-Fetch-Site"], "same-site")
        self.assertEqual(headers["Accept"], "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8")
        self.assertEqual(headers["Accept-Language"], "nl-NL,nl;q=0.9,en;q=0.8")
        self.assertNotIn("Cookie", headers)

    async def test_each_store_gets_its_own_browser_headers(self) -> None:
        self.serve(AH, 200, "image/png", picture(4, 4))
        self.serve(JUMBO, 200, "image/png", picture(4, 4))
        await fetch(AH)
        await fetch(JUMBO)
        referers = [call.request.headers["Referer"] for call in self.get.calls]
        self.assertEqual(referers, ["https://www.ah.nl/", "https://www.jumbo.com/"])

    def test_the_client_has_the_timeouts_and_never_follows_a_redirect_itself(self) -> None:
        async def made() -> httpx.AsyncClient:
            return image_cache._client("ah", "static.ah.nl")

        client = run(made())
        self.assertEqual(client.timeout, httpx.Timeout(connect=5, read=15, write=15, pool=15))
        self.assertFalse(client.follow_redirects)

    async def test_the_second_fetch_of_the_same_address_does_not_go_to_the_store(self) -> None:
        self.serve(AH, 200, "image/png", picture(4, 4))
        first = await fetch(AH)
        second = await fetch(AH)
        self.assertEqual((first["path"], self.get.call_count), (second["path"], 1))
        self.assertEqual(await image_cache.stats(), {"count": 1, "bytes": len(picture(4, 4)), "paused": []})

    async def test_a_fetch_of_an_address_that_only_passes_the_host_check_sends_nothing(self) -> None:
        for url in ("http://static.ah.nl/dam/plain", "https://user@static.ah.nl/dam/x"):
            with self.subTest(url=url):
                self.serve(url, 200, "image/png", picture(4, 4))
                self.assertIsNone(await fetch(url))
                self.assertIsNone(await image_cache.lookup(url), "ook niet geregistreerd")
        self.assertEqual(self.get.call_count, 0)

    async def test_html_svg_and_too_large_are_refused_and_counted(self) -> None:
        self.serve(AH, 200, "text/html; charset=utf-8", b"<html>oeps</html>")
        self.assertIsNone(await fetch(AH))
        svg = "https://static.ah.nl/dam/svg"
        self.serve(svg, 200, "image/svg+xml", b"<svg/>")
        self.assertIsNone(await fetch(svg))
        big = "https://static.ah.nl/dam/big"
        self.serve(big, 200, "image/png", b"x" * (image_cache.MAX_DOWNLOAD_BYTES + 1))
        self.assertIsNone(await fetch(big))
        found = {row["url"]: row for row in await rows()}
        self.assertEqual((found[AH]["attempts"], found[AH]["last_attempt_at"]), (1, NOW))
        self.assertIn("text/html", found[AH]["error"])
        self.assertEqual((found[svg]["attempts"], found[big]["attempts"]), (1, 1))
        self.assertIn("te groot", found[big]["error"])
        self.assertFalse(image_cache.images_dir().exists() and any(image_cache.images_dir().rglob("*")))

    async def test_a_large_download_is_read_in_pieces_and_stopped_at_the_limit(self) -> None:
        # Het lichaam komt in stukken (`aiter_bytes`): bij een te grote foto stopt het lezen, de rest komt nooit binnen.
        given: list[int] = []

        async def chunks() -> Any:
            for _ in range(image_cache.MAX_DOWNLOAD_BYTES // (64 * 1024) + 50):
                given.append(1)
                yield b"x" * (64 * 1024)

        self.get.side_effect = lambda request: httpx.Response(200, headers={"Content-Type": "image/png"}, content=chunks())
        self.assertIsNone(await fetch(AH))
        self.assertIn("te groot", (await image_cache.lookup(AH))["error"])
        self.assertLess(len(given), image_cache.MAX_DOWNLOAD_BYTES // (64 * 1024) + 50)

    async def test_a_403_pauses_the_host_for_twelve_hours_and_a_404_counts_three_times(self) -> None:
        self.serve(AH, 403, "text/html", b"blocked")
        self.assertIsNone(await fetch(AH))
        self.assertEqual(await image_cache.paused_until("static.ah.nl"), "2026-11-04T15:00:00+00:00")
        other = "https://static.ah.nl/dam/other"
        self.serve(other, 200, "image/png", picture(4, 4))
        self.assertIsNone(await fetch(other))
        self.assertEqual(self.get.call_count, 1, "tijdens de pauze geen verzoek")
        self.assertIsNotNone(await fetch(other, now="2026-11-04T15:00:01+00:00"))
        gone = "https://static.ah.nl/dam/gone"
        self.assertIsNone(await fetch(gone, now="2026-11-04T15:00:02+00:00"))
        self.assertEqual({row["url"]: row["attempts"] for row in await rows()}[gone], 3)

    async def test_a_429_pauses_the_host_too(self) -> None:
        self.serve(AH, 429, "text/plain", b"slow down")
        self.assertIsNone(await fetch(AH))
        self.assertEqual(await image_cache.paused_until("static.ah.nl"), "2026-11-04T15:00:00+00:00")
        self.assertEqual((await image_cache.lookup(AH))["attempts"], 1)

    async def test_a_network_error_is_counted_once_and_does_not_pause(self) -> None:
        self.get.side_effect = httpx.ConnectTimeout("te traag")
        self.assertIsNone(await fetch(AH))
        row = await image_cache.lookup(AH)
        self.assertEqual((row["attempts"], row["error"]), (1, "te traag"))
        self.assertIsNone(await image_cache.paused_until("static.ah.nl"))

    async def test_a_photo_that_picnic_does_not_have_is_missing_and_does_not_pause_the_host(self) -> None:
        # #236: Picnic bewaart zijn foto's in Amazon S3. Een foto die er niet is, geeft daar 403 AccessDenied in XML, geen 404. Dat
        # zette de hele host twaalf uur stil, zodat na een recept zonder foto ook de foto's van alle andere recepten wegbleven.
        base = "https://storefront-prod.nl.picnicinternational.com/static/images/recipes"
        gone, other = f"{base}/ac1b02/medium.png", f"{base}/c09b4e/medium.png"
        denied = b'<?xml version="1.0" encoding="UTF-8"?>\n<Error><Code>AccessDenied</Code><Message>Access Denied</Message><RequestId>X</RequestId></Error>'
        self.serve(gone, 403, "application/xml", denied)
        self.serve(other, 200, "image/png", picture(4, 4))
        self.assertIsNone(await fetch(gone))
        self.assertIsNone(await image_cache.paused_until("storefront-prod.nl.picnicinternational.com"))
        self.assertIsNotNone(await fetch(other), "de andere foto's van Picnic komen gewoon")
        row = {row["url"]: row for row in await rows()}[gone]
        self.assertEqual(row["attempts"], 3, "telt als een 404: een week wachten")
        self.assertIn("bestaat niet", row["error"])

    async def test_a_403_that_is_not_an_s3_error_still_pauses_the_host(self) -> None:
        picnic = "https://storefront-prod.nl.picnicinternational.com/static/images/abc/medium.png"
        for content_type, body in (
            ("application/xml", b"<Error><Code>RequestLimitExceeded</Code></Error>"),  # andere XML: geen ontbrekend bestand
            ("text/html", b"<html><body><Error><Code>AccessDenied</Code></Error></body></html>"),  # alleen XML telt
            ("application/xml", b" " * image_cache._ERROR_BODY_BYTES + b"<Error><Code>AccessDenied</Code></Error>"),  # te ver weg
        ):
            with self.subTest(content_type=content_type, size=len(body)):
                await auth.set_setting(image_cache.SETTING_PAUSED + "storefront-prod.nl.picnicinternational.com", "")
                await in_db(image_cache._connect, lambda conn: conn.execute("DELETE FROM image_cache"))
                self.serve(picnic, 403, content_type, body)
                self.assertIsNone(await fetch(picnic))
                self.assertEqual(await image_cache.paused_until("storefront-prod.nl.picnicinternational.com"), "2026-11-04T15:00:00+00:00")

    def test_failures_wait_a_day_then_a_week_then_forever(self) -> None:
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

    async def test_a_failed_address_is_not_tried_again_before_it_is_due(self) -> None:
        self.serve(AH, 200, "text/html", b"oeps")
        await fetch(AH)
        self.serve(AH, 200, "image/png", picture(4, 4))
        self.assertIsNone(await fetch(AH, now="2026-11-04T10:00:00+00:00"))
        self.assertEqual(self.get.call_count, 1)
        self.assertIsNotNone(await fetch(AH, now="2026-11-05T10:00:00+00:00"))

    def test_two_requests_for_the_same_missing_image_share_one_download(self) -> None:
        self.serve(AH, 200, "image/png", picture(4, 4))

        async def both() -> list[dict[str, Any] | None]:
            started, release = asyncio.Event(), asyncio.Event()

            async def slow(request: httpx.Request) -> httpx.Response:
                started.set()
                await release.wait()
                return self.respond(request)

            self.get.side_effect = slow
            pair = asyncio.gather(image_cache.fetch(AH, now=NOW), image_cache.fetch(AH, now=NOW))
            await asyncio.wait_for(started.wait(), 5)
            await asyncio.sleep(0.2)  # het tweede verzoek staat nu te wachten op de download van het eerste
            release.set()
            return list(await pair)

        results = run(both())
        self.assertEqual(self.get.call_count, 1)
        self.assertEqual([row["path"] for row in results], [results[0]["path"]] * 2)
        self.assertEqual(image_cache._INFLIGHT, {}, "geen lopende download blijft achter")

    async def test_a_request_that_saw_the_photo_missing_just_before_it_arrived_does_not_fetch_it_again(self) -> None:
        # De volgorde die de test hierboven af en toe liet zien: verzoek B kijkt (geen foto), verzoek A haalt hem op en is klaar
        # voordat B eigenaar wordt. B ziet dan geen lopende download meer en wordt zelf eigenaar, het moet eerst opnieuw kijken.
        self.serve(AH, 200, "image/png", picture(4, 4))
        first = await fetch(AH)  # verzoek A
        real_lookup = image_cache.lookup
        stale = {**first, "path": None, "fetched_at": None}  # wat B zag, net voordat A klaar was
        calls: list[str] = []

        async def lookup(url: str) -> dict[str, Any] | None:
            calls.append(url)
            return stale if len(calls) == 1 else await real_lookup(url)

        with mock.patch.object(image_cache, "lookup", side_effect=lookup):
            second = await fetch(AH)  # verzoek B
        self.assertEqual((second["path"], self.get.call_count), (first["path"], 1))

    async def test_a_request_that_saw_no_row_before_another_fetched_the_photo_keeps_that_photo(self) -> None:
        # Ook een volgorde die de test met twee verzoeken af en toe liet zien: verzoek B kijkt (nog geen rij), verzoek A registreert
        # het adres, haalt de foto op en is klaar, en dan registreert B. B ziet daarna een pad: dat is de foto van A, geen verdwenen
        # bestand. B mag dat pad niet wissen en de foto niet opnieuw ophalen.
        self.serve(AH, 200, "image/png", picture(4, 4))
        first = await fetch(AH)  # verzoek A
        real_lookup = image_cache.lookup
        calls: list[str] = []

        async def lookup(url: str) -> dict[str, Any] | None:
            calls.append(url)
            return None if len(calls) == 1 else await real_lookup(url)  # wat B zag, voordat A de rij aanmaakte

        with mock.patch.object(image_cache, "lookup", side_effect=lookup):
            second = await fetch(AH)  # verzoek B
        self.assertEqual((second["path"], self.get.call_count), (first["path"], 1))
        self.assertEqual((await image_cache.lookup(AH))["path"], first["path"])

    async def test_a_photo_whose_file_is_gone_is_fetched_again(self) -> None:
        self.serve(AH, 200, "image/png", picture(4, 4))
        first = await fetch(AH)
        (image_cache.images_dir() / first["path"]).unlink()
        second = await fetch(AH)
        self.assertEqual((second["path"], self.get.call_count), (first["path"], 2))
        self.assertTrue((image_cache.images_dir() / second["path"]).is_file())

    async def test_the_pace_sleeps_between_two_requests_on_the_same_host(self) -> None:
        # Een vaste klok en de grootste jitter: de pauze is precies het gat (0,3 s plus 0,2 s jitter) min de tijd sinds het vorige
        # verzoek (0,25 s). Met de echte klok zat er een paar microseconden speling in, en die liet de test af en toe vallen.
        clock: list[float] = [1024.0]
        other = "https://static.ah.nl/dam/other"
        self.serve(AH, 200, "image/png", picture(4, 4))
        self.serve(other, 200, "image/png", picture(4, 4))
        self.serve(JUMBO, 200, "image/png", picture(4, 4))
        with (
            mock.patch.object(image_cache, "_monotonic", side_effect=lambda: clock[0]),
            mock.patch.object(image_cache, "random", mock.Mock(uniform=lambda low, high: high)),
        ):
            await fetch(AH)
            await fetch(JUMBO)
            clock[0] += 0.25
            await fetch(other)
        waited = [call.args[0] for call in self.sleep.call_args_list]
        self.assertEqual(waited, [0.25], "alleen tussen twee verzoeken op dezelfde host")

    async def test_the_pace_never_sleeps_while_holding_a_slot(self) -> None:
        # Een verzoek dat wacht op zijn beurt bij een host, mag geen slot bezet houden: anders houdt het een verzoek aan een andere
        # host op.
        free: list[int] = []

        async def sleeping(seconds: float) -> None:
            free.append(image_cache._slots()._value)

        self.sleep.side_effect = sleeping
        other = "https://static.ah.nl/dam/other"
        self.serve(AH, 200, "image/png", picture(4, 4))
        self.serve(other, 200, "image/png", picture(4, 4))
        await fetch(AH)
        await fetch(other)
        self.assertEqual(free, [image_cache.CONCURRENCY], "tijdens het slapen is geen slot bezet")

    def test_three_fetches_to_three_hosts_never_have_more_than_two_downloads_at_once(self) -> None:
        for url in (AH, JUMBO, CRISP):
            self.serve(url, 200, "image/png", picture(4, 4))

        async def three() -> tuple[int, list[dict[str, Any] | None]]:
            inside: list[int] = [0]
            most: list[int] = [0]
            crowded = asyncio.Event()

            async def counted(request: httpx.Request) -> httpx.Response:
                inside[0] += 1
                most[0] = max(most[0], inside[0])
                if inside[0] > image_cache.CONCURRENCY:
                    crowded.set()
                try:
                    await asyncio.sleep(0)
                    with suppress(TimeoutError):
                        await asyncio.wait_for(crowded.wait(), 0.3)  # geef een derde de kans om erbij te komen
                    return self.respond(request)
                finally:
                    inside[0] -= 1

            self.get.side_effect = counted
            found = await asyncio.gather(*(image_cache.fetch(url, now=NOW) for url in (AH, JUMBO, CRISP)))
            return most[0], list(found)

        most, found = run(three())
        self.assertEqual(most, image_cache.CONCURRENCY)
        self.assertTrue(all(row and row["path"] for row in found), "alle drie komen er wel")
        self.assertEqual(self.get.call_count, 3)

    async def test_a_cache_hit_does_not_write_to_the_database(self) -> None:
        self.serve(AH, 200, "image/png", picture(4, 4))
        await fetch(AH)
        with (
            mock.patch.object(image_cache, "register", new_callable=mock.AsyncMock) as register,
            mock.patch.object(image_cache, "_record_success", new_callable=mock.AsyncMock) as success,
        ):
            self.assertIsNotNone(await fetch(AH))
        register.assert_not_called()
        success.assert_not_called()

    async def test_a_disk_error_is_recorded_as_a_failure_and_leaves_no_part_file(self) -> None:
        self.serve(AH, 200, "image/png", picture(4, 4))
        with mock.patch.object(image_cache.os, "replace", side_effect=PermissionError("alleen-lezen")):
            self.assertIsNone(await fetch(AH))
        row = await image_cache.lookup(AH)
        self.assertEqual((row["attempts"], row["path"]), (1, None))
        self.assertIn("alleen-lezen", row["error"])
        self.assertEqual(list(image_cache.images_dir().rglob("*.part")), [])
        self.assertEqual(self.get.call_count, 1)
        self.assertIsNone(await fetch(AH, now="2026-11-04T04:00:00+00:00"), "wacht nu een dag, geen tweede download")
        self.assertEqual(self.get.call_count, 1)

    async def test_a_waiting_request_gives_up_after_ten_seconds(self) -> None:
        self.assertEqual(image_cache.WAIT_SECONDS, 10)
        stuck = asyncio.Event()  # een download die nooit klaar komt
        started = time.perf_counter()
        with mock.patch.dict(image_cache._INFLIGHT, {AH: stuck}), mock.patch.object(image_cache, "WAIT_SECONDS", 0.05):
            self.assertIsNone(await fetch(AH))
        self.assertLess(time.perf_counter() - started, 1, "het wachten volgt WAIT_SECONDS, geen vaste tijd")
        self.assertEqual(self.get.call_count, 0)

    async def test_a_redirect_is_followed_only_to_a_store_host(self) -> None:
        # Een winkel-CDN mag doorverwijzen naar een ander winkeladres, nooit naar iets anders (een intern adres bijvoorbeeld).
        moved = "https://static.ah.nl/dam/product/moved"
        self.serve(moved, 302, "text/plain", b"")
        self.redirects[moved] = "https://static.ah.nl/dam/product/final"
        self.serve("https://static.ah.nl/dam/product/final", 200, "image/png", picture(4, 4))
        row = await fetch(moved)
        self.assertEqual((row["url"], row["width"]), (moved, 4))
        self.assertEqual(self.urls(), [moved, "https://static.ah.nl/dam/product/final"])
        bad = "https://static.ah.nl/dam/product/bad"
        self.serve(bad, 302, "text/plain", b"")
        self.redirects[bad] = "http://127.0.0.1:8000/api/system/images"
        self.assertIsNone(await fetch(bad))
        self.assertNotIn("127.0.0.1", " ".join(self.urls()), "httpx volgt zelf geen doorverwijzing")
        self.assertIn("onbekende host", (await image_cache.lookup(bad))["error"])
        loop = "https://static.ah.nl/dam/product/loop"
        self.serve(loop, 302, "text/plain", b"")
        self.redirects[loop] = loop
        self.assertIsNone(await fetch(loop))
        self.assertEqual(sum(1 for url in self.urls() if url == loop), image_cache.MAX_REDIRECTS + 1)
        self.assertIn("te veel doorverwijzingen", (await image_cache.lookup(loop))["error"])

    async def test_a_redirect_target_is_checked_with_the_parser_that_sends_the_request(self) -> None:
        # Een adres dat de ene parser als static.ah.nl leest en de andere als iets anders, en een stap terug naar http: nooit.
        for location in (
            "https://static.ah.nl\\@127.0.0.1/dam/x",
            "https://static.ah.nl@127.0.0.1/dam/x",
            "https://static.ah.nl /dam/x",
            "https://static.ah.nl%0d%0a/dam/x",
            "http://static.ah.nl/dam/plain",
        ):
            with self.subTest(location=location):
                self.get.calls.clear()
                moved = f"https://static.ah.nl/dam/product/moved-{abs(hash(location))}"
                self.serve(moved, 302, "text/plain", b"")
                self.redirects[moved] = location
                self.serve(location, 200, "image/png", picture(4, 4))
                self.assertIsNone(await fetch(moved))
                self.assertEqual(self.urls(), [moved], "na de doorverwijzing geen verzoek meer")

    async def test_the_images_folder_is_created_on_first_use(self) -> None:
        self.assertFalse(image_cache.images_dir().exists())
        self.serve(AH, 200, "image/png", picture(4, 4))
        await fetch(AH)
        self.assertTrue(image_cache.images_dir().is_dir())


class RoutesTest(FetchCase):
    def setUp(self) -> None:
        super().setUp()
        self.client = self.client_for(create_app())

    async def test_the_route_serves_the_cached_file_with_a_long_cache_header(self) -> None:
        self.serve(AH, 200, "image/webp", picture(8, 8, "WEBP"))
        reply = await self.client.get(f"/api/images/{image_cache.token_for(AH)}")
        self.assertEqual(
            (reply.status_code, reply.content_type, reply.headers["Cache-Control"]), (200, "image/webp", "private, max-age=31536000, immutable")
        )
        self.assertEqual(reply.data, picture(8, 8, "WEBP"))
        self.assertEqual(reply.headers["X-Content-Type-Options"], "nosniff")
        again = await self.client.get(f"/api/images/{image_cache.token_for(AH)}", headers={"If-None-Match": reply.headers["ETag"]})
        self.assertEqual(again.status_code, 304)
        self.assertEqual(self.get.call_count, 1)

    async def test_image_route_answers_304_on_matching_etag(self) -> None:
        self.serve(AH, 200, "image/png", picture(4, 4))
        url = f"/api/images/{image_cache.token_for(AH)}"
        first = await self.client.get(url)
        digest = (await fetch(AH))["digest"]
        self.assertEqual(first.headers["ETag"], f'"{digest}"')
        again = await self.client.get(url, headers={"If-None-Match": first.headers["ETag"]})
        self.assertEqual((again.status_code, again.data), (304, b""))
        self.assertEqual(again.headers["ETag"], f'"{digest}"')
        self.assertEqual(again.headers["Cache-Control"], "private, max-age=31536000, immutable")
        self.assertEqual(again.headers["X-Content-Type-Options"], "nosniff")

    async def test_unknown_tokens_and_hosts_give_404_without_a_request(self) -> None:
        for token in ("%%%", image_cache.token_for("https://mealie.local/x.png"), image_cache.token_for("ftp://static.ah.nl/x")):
            self.assertEqual((await self.client.get(f"/api/images/{token}")).status_code, 404)
        self.assertEqual(self.get.call_count, 0)

    async def test_a_paused_host_gives_404_without_a_request(self) -> None:
        await image_cache._pause("static.ah.nl", NOW)
        with mock.patch.object(image_cache, "_now", return_value=NOW):
            self.assertEqual((await self.client.get(f"/api/images/{image_cache.token_for(AH)}")).status_code, 404)
        self.assertEqual(self.get.call_count, 0)

    async def test_a_failed_download_gives_404(self) -> None:
        self.serve(AH, 200, "text/html", b"<html>")
        self.assertEqual((await self.client.get(f"/api/images/{image_cache.token_for(AH)}")).status_code, 404)

    async def test_the_system_page_sees_how_big_the_cache_is(self) -> None:
        self.assertEqual((await self.client.get("/api/system/images")).get_json(), {"count": 0, "bytes": 0, "paused": []})
        self.serve(AH, 200, "image/png", picture(4, 4))
        await self.client.get(f"/api/images/{image_cache.token_for(AH)}")
        self.assertEqual((await self.client.get("/api/system/images")).get_json(), {"count": 1, "bytes": len(picture(4, 4)), "paused": []})

    async def test_the_system_page_sees_which_store_is_paused_and_until_when(self) -> None:
        until = await image_cache._pause("static.ah.nl", image_cache._now())
        paused = (await self.client.get("/api/system/images")).get_json()["paused"]
        self.assertEqual(paused, [{"host": "static.ah.nl", "store": "ah", "until": until}])

    def test_the_container_serves_with_one_asyncio_process_so_a_slow_store_does_not_block_the_app(self) -> None:
        dockerfile = (Path(__file__).resolve().parent.parent / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn('"--workers", "0"', dockerfile)  # een proces, de app zelf: zie test_restart.py
        self.assertNotIn("--threads", dockerfile)

    async def test_the_route_needs_a_login_like_the_rest_of_the_api(self) -> None:
        with (
            mock.patch.object(auth, "auth_required", new_callable=mock.AsyncMock, return_value=True),
            mock.patch.object(auth, "current_user", new_callable=mock.AsyncMock, return_value=None),
        ):
            self.assertEqual((await self.client.get(f"/api/images/{image_cache.token_for(AH)}")).status_code, 401)
        self.assertEqual(self.get.call_count, 0)


class PausesTest(Case):
    PICNIC = "storefront-prod.nl.picnicinternational.com"

    def test_pauses_lists_only_running_pauses_of_store_hosts(self) -> None:
        run(image_cache._pause(self.PICNIC, NOW))
        run(image_cache._pause("crispapp.nl", "2026-11-03T03:00:00+00:00"))  # al afgelopen
        run(auth.set_setting(image_cache.SETTING_PAUSED + "static.ah.nl", ""))  # opgeheven
        run(auth.set_setting(image_cache.SETTING_PAUSED + "example.com", "2026-11-04T16:00:00+00:00"))  # geen winkel
        self.assertEqual(run(image_cache.pauses(NOW)), [{"host": self.PICNIC, "store": "picnic", "until": "2026-11-04T15:00:00+00:00"}])
        self.assertEqual(run(image_cache.stats(NOW))["paused"], run(image_cache.pauses(NOW)))

    def test_a_picnic_pause_from_before_3_7_1_is_lifted_once(self) -> None:
        # #236: 3.6.2 zette Picnic na een 403 van S3 twaalf uur stil. Die pauze bleef na de update naar 3.9.0 lopen, en omdat de
        # receptfoto's sinds 3.7.2 een nieuw adres hebben, kwam er toen geen enkele foto meer.
        now = image_cache._now()
        run(image_cache._pause(self.PICNIC, now))
        run(image_cache._pause("static.ah.nl", now))
        self.assertEqual(run(image_cache.lift_stale_pauses()), [self.PICNIC])
        self.assertIsNone(run(image_cache.paused_until(self.PICNIC)))
        self.assertIsNotNone(run(image_cache.paused_until("static.ah.nl")), "een pauze van AH kan echt zijn en blijft staan")
        run(image_cache._pause(self.PICNIC, now))  # een echte blokkade na de update
        self.assertEqual(run(image_cache.lift_stale_pauses()), [], "maar één keer")
        self.assertIsNotNone(run(image_cache.paused_until(self.PICNIC)))

    def test_starting_the_app_lifts_the_stale_picnic_pause(self) -> None:
        run(image_cache._pause(self.PICNIC, image_cache._now()))
        with self.assertLogs(level="INFO") as logs:
            run(start_app(create_app()))
        self.assertIsNone(run(image_cache.paused_until(self.PICNIC)))
        self.assertTrue(any(self.PICNIC in line for line in logs.output))


class PrefetchTest(FetchCase):
    def setUp(self) -> None:
        super().setUp()
        env = mock.patch.dict(os.environ, {"MEALIESHOPPER_IMAGE_PREFETCH": "true"})
        env.start()
        self.addCleanup(env.stop)

    def known(self) -> None:
        with closing(db(ah_orders)) as conn, conn:
            conn.execute(
                "INSERT INTO ah_order_items (order_id, position, product_id, title, image) VALUES (1, 0, 10, 'Melk', ?)",
                ("https://static.ah.nl/dam/product/melk",),
            )
            conn.execute(
                "INSERT INTO ah_receipt_products (receipt_product_id, product_id, title, checked_at, image) VALUES (1, 10, 'Melk', 'x', 'https://static.ah.nl/dam/product/bon')"
            )
        with closing(db(offers)) as conn, conn:
            conn.execute(
                "INSERT INTO offers (store, product_id, position, title, image) VALUES ('ah', '1', 0, 'Kaas', 'https://static.ah.nl/dam/product/kaas')"
            )

    async def register(self, url: str, now: str = NOW) -> None:
        await image_cache.register(url, now=now)

    async def test_collect_known_finds_the_addresses_in_every_table(self) -> None:
        self.known()
        self.assertEqual(await image_cache.collect_known(now=NOW), 3)
        self.assertEqual(await image_cache.collect_known(now=NOW), 0)
        self.assertEqual(sorted(row["url"].rsplit("/", 1)[1] for row in await rows()), ["bon", "kaas", "melk"])

    async def test_collect_known_checks_the_addresses_off_the_event_loop(self) -> None:
        """`safe_url` over alle foto's (een paar ms per duizend) loopt in een thread van `compute`, niet op de loop (lage-eindapparaten)."""
        self.known()
        loop_thread = threading.get_ident()
        threads: list[int] = []
        real = image_cache.safe_url

        def spy(url: Any) -> bool:
            threads.append(threading.get_ident())
            return real(url)

        with mock.patch.object(image_cache, "safe_url", spy):
            self.assertEqual(await image_cache.collect_known(now=NOW), 3)
        self.assertEqual(len(threads), 3)
        self.assertNotIn(loop_thread, threads)

    async def test_prefetch_fetches_the_newest_first_up_to_the_limit_and_skips_what_waits(self) -> None:
        urls = [f"https://static.ah.nl/dam/product/{n}" for n in range(3)]
        for n, url in enumerate(urls):
            await self.register(url, now=f"2026-11-0{n + 1}T00:00:00+00:00")
            self.serve(url, 200, "image/png", picture(4, 4))
        await image_cache._record_failure(urls[1], "2026-11-04T02:00:00+00:00", 1, "oeps")  # wacht nog een dag
        result = await image_cache.prefetch(limit=2, now=NOW)
        self.assertEqual(result, {"fetched": 2, "failed": 0, "skipped": 1, "paused": 0})
        self.assertEqual(self.urls(), [urls[2], urls[0]], "nieuwste eerst, de wachtende overgeslagen")
        self.assertTrue(any(2.0 <= call.args[0] <= 5.0 for call in self.sleep.call_args_list), "het rustige tempo van de achtergrond")
        result = await image_cache.prefetch(limit=2, now=NOW)
        self.assertEqual(result, {"fetched": 0, "failed": 0, "skipped": 1, "paused": 0})

    async def test_prefetch_rests_two_to_five_seconds_between_fetches_but_not_before_the_first(self) -> None:
        urls = [f"https://static.ah.nl/dam/product/{n}" for n in range(3)]
        for url in urls:
            await self.register(url)
            self.serve(url, 200, "image/png", picture(4, 4))
        await image_cache.prefetch(now=NOW)
        rests = [call.args[0] for call in self.sleep.call_args_list if call.args[0] >= 2.0]
        self.assertEqual(len(rests), 2, "een rustpauze tussen elke twee foto's")
        self.assertTrue(all(2.0 <= rest <= 5.0 for rest in rests), rests)

    async def test_only_one_prefetch_round_runs_at_a_time(self) -> None:
        await self.register(AH)
        self.serve(AH, 200, "image/png", picture(4, 4))

        async def while_busy() -> dict[str, Any]:
            lock = image_cache._prefetch_lock()
            await lock.acquire()
            try:
                return await asyncio.wait_for(image_cache.prefetch(now=NOW), 5)  # zonder de controle wacht hij op het slot
            finally:
                lock.release()

        self.assertEqual(await while_busy(), {"fetched": 0, "failed": 0, "skipped": 0, "paused": 0, "running": True})
        self.assertEqual(self.get.call_count, 0)
        self.assertEqual((await image_cache.prefetch(now=NOW))["fetched"], 1)

    async def test_collect_known_skips_an_address_that_only_passes_the_host_check(self) -> None:
        with closing(db(ah_orders)) as conn, conn:
            for position, url in enumerate(("http://static.ah.nl/dam/plain", "https://user@static.ah.nl/dam/x")):
                conn.execute(
                    "INSERT INTO ah_order_items (order_id, position, product_id, title, image) VALUES (1, ?, ?, 'Melk', ?)",
                    (position, 10 + position, url),
                )
        self.assertEqual(await image_cache.collect_known(now=NOW), 0)
        self.assertEqual(await rows(), [])

    def test_collect_known_survives_a_table_that_has_no_image_column_yet(self) -> None:
        with closing(db()) as conn, conn:
            conn.execute(
                "CREATE TABLE ah_receipt_products (receipt_product_id INTEGER PRIMARY KEY, product_id INTEGER, title TEXT, checked_at TEXT NOT NULL)"
            )
            conn.execute("INSERT INTO ah_receipt_products VALUES (1, 10, 'Melk', 'x')")
        self.assertEqual(run(image_cache.collect_known(now=NOW)), 0)

    async def test_prefetch_counts_failures_and_paused_hosts(self) -> None:
        await self.register("https://static.ah.nl/dam/product/x")
        await self.register("https://crispapp.nl/blob/y")
        self.serve("https://static.ah.nl/dam/product/x", 200, "text/html", b"oeps")
        await image_cache._pause("crispapp.nl", NOW)
        self.assertEqual(await image_cache.prefetch(now=NOW), {"fetched": 0, "failed": 1, "skipped": 1, "paused": 1})

    async def test_prefetch_stops_at_the_deadline_and_can_be_switched_off(self) -> None:
        await self.register(AH)
        self.serve(AH, 200, "image/png", picture(4, 4))
        self.assertEqual((await image_cache.prefetch(deadline_seconds=0, now=NOW))["fetched"], 0)
        self.assertEqual((await image_cache.prefetch(deadline_seconds=-1, now=NOW))["fetched"], 0)
        with mock.patch.dict(os.environ, {"MEALIESHOPPER_IMAGE_PREFETCH": "false"}):
            self.assertFalse(image_cache.enabled())
            self.assertIsNone(await image_cache.prefetch_nightly())
            with mock.patch.object(image_cache.tasks, "spawn") as spawn:
                await image_cache.prefetch_async()
            spawn.assert_not_called()
        self.assertEqual(self.get.call_count, 0)

    async def test_the_nightly_round_runs_and_is_written_to_the_audit_log(self) -> None:
        await self.register(AH)
        self.serve(AH, 200, "image/png", picture(4, 4))
        with mock.patch.object(image_cache.audit, "record", new_callable=mock.AsyncMock) as record:
            result = await image_cache.prefetch_nightly()
        self.assertEqual(result["fetched"], 1)
        self.assertEqual(
            (record.call_args.kwargs["service"], record.call_args.kwargs["action"], record.call_args.kwargs["detail"]), ("images", "prefetch", result)
        )

    def test_a_failing_nightly_round_is_logged_and_gives_none(self) -> None:
        with (
            mock.patch.object(image_cache, "prefetch", new_callable=mock.AsyncMock, side_effect=RuntimeError("stuk")),
            self.assertLogs("mealieshopper.image_cache", level="ERROR"),
        ):
            self.assertIsNone(run(image_cache.prefetch_nightly()))

    async def test_prefetch_async_runs_a_small_round_in_one_background_task(self) -> None:
        await self.register(AH)
        self.serve(AH, 200, "image/png", picture(4, 4))

        async def go() -> tuple[list[str], list[str]]:
            await image_cache.prefetch_async(limit=40)
            await image_cache.prefetch_async(limit=40)  # loopt er al een ronde, dan niet nog een
            names = [name for name in tasks.running() if name.startswith("image-prefetch")]
            task = image_cache._PREFETCH_TASK
            await task
            return names, [task.get_name()]

        names, name = await go()
        self.assertEqual((names, name), (["image-prefetch"], ["image-prefetch"]))
        self.assertEqual(self.get.call_count, 1)

    def test_prefetch_async_passes_its_limit_and_short_deadline(self) -> None:
        async def go() -> None:
            with mock.patch.object(image_cache, "prefetch", new_callable=mock.AsyncMock, return_value={}) as prefetch:
                await image_cache.prefetch_async(limit=7)
                await image_cache._PREFETCH_TASK
            prefetch.assert_awaited_once_with(limit=7, deadline_seconds=image_cache.PREFETCH_AFTER_SYNC_SECONDS)

        run(go())

    async def test_a_cancelled_round_leaves_nothing_running(self) -> None:
        # Bij het afsluiten annuleert de app zijn tasks. Een ronde die midden in de rustpauze stopt, laat het slot vrij, laat geen
        # lopende download achter, en de volgende ronde start gewoon.
        urls = [f"https://static.ah.nl/dam/product/{n}" for n in range(2)]
        for url in urls:
            await self.register(url)
            self.serve(url, 200, "image/png", picture(4, 4))

        async def go() -> tuple[bool, bool, bool, dict[str, asyncio.Event]]:
            resting = asyncio.Event()

            async def forever(seconds: float) -> None:
                if seconds >= image_cache.BACKGROUND_PACE[0]:
                    resting.set()
                    await asyncio.Event().wait()

            self.sleep.side_effect = forever
            await image_cache.prefetch_async(limit=5)
            first = image_cache._PREFETCH_TASK
            await asyncio.wait_for(resting.wait(), 5)
            first.cancel()
            with suppress(asyncio.CancelledError):
                await first
            locked = image_cache._prefetch_lock().locked()
            self.sleep.side_effect = None
            await image_cache.prefetch_async(limit=5)
            second = image_cache._PREFETCH_TASK
            await second
            return first.cancelled(), locked, second is not first, dict(image_cache._INFLIGHT)

        cancelled, locked, again, inflight = await go()
        self.assertEqual((cancelled, locked, again, inflight), (True, False, True, {}))
        self.assertEqual(self.urls(), [urls[1], urls[0]], "de eerste ronde stopte voor de tweede foto")
        self.assertEqual(sum(1 for row in await rows() if row["path"]), 2, "de tweede ronde haalt op wat de eerste liet liggen")

    def test_a_round_from_an_earlier_loop_does_not_block_a_new_one(self) -> None:
        async def leftover() -> asyncio.Task[Any]:
            async def hang() -> None:
                await asyncio.Event().wait()

            task = asyncio.get_running_loop().create_task(hang())
            image_cache._PREFETCH_TASK = task
            return task

        run(leftover())  # de loop sluit en annuleert de task
        with mock.patch.object(image_cache, "prefetch", new_callable=mock.AsyncMock, return_value={}) as prefetch:

            async def go() -> None:
                await image_cache.prefetch_async(limit=3)
                await image_cache._PREFETCH_TASK

            run(go())
        prefetch.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
