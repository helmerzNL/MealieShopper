"""Prijsgeschiedenis: de schakelaar "Alleen eerder gekocht" en de bewaarde afbeeldingen van aanbiedingen die je kocht."""
import io
import os
import re
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from PIL import Image

os.environ.setdefault("MEALIESHOPPER_DATA_DIR", tempfile.mkdtemp())

from mealieshopper import ah_orders, auth, crisp, mcp, offers, picnic, prices, product_images  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402
from support import SyncClient, in_db, run  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TODAY = "2026-10-20"


def picture(width, height, color=(200, 30, 30), kind="JPEG", mode="RGB", **save):
    out = io.BytesIO()
    Image.new(mode, (width, height), color).save(out, kind, **save)
    return out.getvalue()


def offer(product_id, title, image="", price=1.0, before=1.5):
    return {"id": product_id, "title": title, "price": price, "priceBefore": before, "promoLabel": "25% korting", "image": image}


def order_item(product_id, title, price=1.0):
    return {"productId": product_id, "title": title, "brand": "AH", "unitSize": "500 g", "quantity": 1, "amount": price,
            "price": price, "priceBeforeBonus": None, "bonus": False, "taxonomy": "", "image": ""}


class Case(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"MEALIESHOPPER_DATA_DIR": tmp.name, "PASSKEY_AUTH_ENABLED": "false"})
        patcher.start()
        self.addCleanup(patcher.stop)
        auth.init_db()
        # Nooit echt het internet op: elke test zegt zelf wat een adres teruggeeft.
        self.served: dict[str, bytes] = {}
        self.asked: list[str] = []
        download = mock.patch.object(product_images, "_download", side_effect=self.download)
        download.start()
        self.addCleanup(download.stop)

    def download(self, url):
        self.asked.append(url)
        if url not in self.served:
            raise RuntimeError("HTTP 404")
        return self.served[url]

    def sync(self, when, *items, store="ah"):
        with mock.patch.object(offers, "_now", return_value=when), mock.patch.dict(offers.FETCHERS, {store: lambda: list(items)}):
            offers.sync(store)

    def bought(self, order_id, day, *items):
        in_db(ah_orders._aconnect, lambda conn: ah_orders._save_order(conn, {"id": order_id, "deliveryDate": day}, "delivered", list(items)))

    def rows(self, store="ah"):
        with closing(prices._connect()) as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM product_images WHERE store = ? ORDER BY id", (store,))]


class BoughtFilterTest(Case):
    def test_the_switch_keeps_only_what_you_bought(self):
        self.sync("2026-10-05T05:00:00+00:00", offer("1", "AH Penne"), offer("2", "AH Fusilli"), offer("3", "AH Macaroni"))
        self.bought(101, "2026-10-07", order_item(1, "AH Penne"))
        run(prices.record("ah", [{"id": "4", "title": "AH Spaghetti", "price": 1.25}], now="2026-10-15T08:00:00+00:00"))
        everything = run(prices.history("ah", "", 50, today=TODAY))
        mine = run(prices.history("ah", "", 50, today=TODAY, bought=True))
        self.assertEqual(everything["total"], 4)
        self.assertEqual(([p["productId"] for p in mine["products"]], mine["total"]), (["1"], 1))
        self.assertEqual(mine["products"][0]["timesBought"], 1)

    def test_a_receipt_counts_as_bought_and_the_search_still_applies(self):
        self.sync("2026-10-05T05:00:00+00:00", offer("1", "AH Penne"), offer("2", "AH Fusilli"))
        with closing(ah_orders._connect()) as conn, conn:
            conn.execute("INSERT INTO ah_receipts (receipt_id, date_time, total, details_synced, first_seen) VALUES ('R1', '2026-10-16T12:00:00', 0, 1, 'x')")
            conn.execute("INSERT INTO ah_receipt_items (receipt_id, position, product_id, name, quantity, price, amount) VALUES ('R1', 0, NULL, 'AH FUSILLI', 1, 1.1, 1.1)")
        self.assertEqual([p["productId"] for p in run(prices.history("ah", "", 50, today=TODAY, bought=True))["products"]], ["2"])
        self.assertEqual(run(prices.history("ah", "penne", 50, today=TODAY, bought=True))["products"], [])

    def test_an_order_that_is_not_delivered_yet_does_not_count(self):
        self.sync("2026-10-05T05:00:00+00:00", offer("1", "AH Penne"))
        in_db(ah_orders._aconnect, lambda conn: ah_orders._save_order(conn, {"id": 102, "deliveryDate": "2026-10-22"}, "open", [order_item(1, "AH Penne")]))
        self.assertEqual(run(prices.history("ah", "", 50, today=TODAY, bought=True))["total"], 0)

    def test_the_route_and_the_mcp_tool(self):
        self.sync("2026-10-05T05:00:00+00:00", offer("1", "AH Penne"), offer("2", "AH Fusilli"))
        self.bought(101, "2026-10-07", order_item(2, "AH Fusilli"))
        client = SyncClient(create_app())
        self.assertEqual(client.get("/api/prices/history?store=ah").get_json()["total"], 2)
        reply = client.get("/api/prices/history?store=ah&bought=1").get_json()
        self.assertEqual(([p["productId"] for p in reply["products"]], reply["total"]), (["2"], 1))
        self.assertEqual(client.get("/api/prices/history?store=ah&bought=0").get_json()["total"], 2)
        tool = mcp.TOOLS["get_price_history"]
        self.assertIn("bought", tool["inputSchema"]["properties"])
        self.assertEqual([p["productId"] for p in run(tool["handler"]({"store": "ah", "bought": True}))["products"]], ["2"])


class ShrinkTest(unittest.TestCase):
    def test_a_wide_image_becomes_1024_pixels_wide(self):
        data, content_type, width, height = product_images.shrink(picture(2000, 1000))
        self.assertEqual((width, height), (1024, 512))
        with Image.open(io.BytesIO(data)) as image:
            self.assertEqual((image.size, Image.MIME[image.format]), ((1024, 512), content_type))
        self.assertIn(content_type, ("image/webp", "image/jpeg"))

    def test_a_small_image_stays_as_it_is(self):
        original = picture(800, 800, kind="PNG")
        self.assertEqual(product_images.shrink(original), (original, "image/png", 800, 800))
        jpeg = picture(1024, 300)
        self.assertEqual(product_images.shrink(jpeg)[0], jpeg)

    def test_a_small_image_is_never_enlarged_and_other_formats_are_converted(self):
        data, content_type, width, height = product_images.shrink(picture(300, 200, kind="GIF"))
        self.assertEqual((width, height), (300, 200))
        self.assertNotEqual(content_type, "image/gif")

    def test_transparency_survives(self):
        data, content_type, _, _ = product_images.shrink(picture(1600, 800, color=(0, 0, 0, 0), kind="PNG", mode="RGBA"))
        with Image.open(io.BytesIO(data)) as image:
            self.assertIn("A", image.getbands())

    def test_a_turned_photo_is_measured_as_it_is_shown(self):
        exif = Image.Exif()
        exif[0x0112] = 6  # 90 graden gedraaid: wat in het bestand de hoogte is, is in beeld de breedte
        original = picture(1600, 800, exif=exif.tobytes())
        data, _, width, height = product_images.shrink(original)
        self.assertEqual((width, height), (800, 1600))  # in beeld 800 breed: niet verkleind, wel rechtop gezet
        self.assertNotEqual(data, original)
        data, _, width, height = product_images.shrink(picture(800, 1600, exif=exif.tobytes()))
        self.assertEqual((width, height), (1024, 512))  # in beeld 1600 breed

    def test_what_is_not_an_image_is_refused(self):
        for data in (b"", b"<html>niet gevonden</html>", b"<svg></svg>"):
            with self.assertRaises(ValueError):
                product_images.shrink(data)
        with mock.patch.object(product_images, "MAX_PIXELS", 100):
            with self.assertRaises(ValueError):
                product_images.shrink(picture(20, 20))

    def test_checked_works_out_the_values_from_the_image(self):
        data = picture(30, 20, kind="PNG")
        found = product_images.checked(data)
        self.assertEqual((found["content_type"], found["width"], found["height"]), ("image/png", 30, 20))
        self.assertEqual(len(found["digest"]), 64)
        for bad in (None, b"", b"<svg></svg>", picture(1100, 10, kind="PNG")):
            with self.assertRaises(ValueError):
                product_images.checked(bad)


class SourceUrlTest(unittest.TestCase):
    def test_the_largest_version_comes_first(self):
        ah_url = "https://static.ah.nl/dam/product/AHI_123?revLabel=1&rendition=200x200_JPG_Q85&fileType=binary"
        self.assertEqual(
            product_images.source_urls("ah", ah_url),
            ["https://static.ah.nl/dam/product/AHI_123?revLabel=1&rendition=800x800_JPG_Q90&fileType=binary", ah_url],
        )
        with mock.patch.object(picnic, "country_code", return_value="NL"):
            picnic_url = picnic.image_url("abc")
        self.assertEqual(product_images.source_urls("picnic", picnic_url), [picnic.resized_image(picnic_url, "large"), picnic_url])
        crisp_url = f"{crisp.ORIGIN}{crisp.BLOB_PATH}abc?thumb={crisp.THUMB}"
        self.assertEqual(product_images.source_urls("crisp", crisp_url)[0], f"{crisp.ORIGIN}{crisp.BLOB_PATH}abc?thumb=1024")
        self.assertEqual(product_images.source_urls("jumbo", "https://static.jumbo.com/a.png"), ["https://static.jumbo.com/a.png"])

    def test_only_web_addresses(self):
        for url in ("", "javascript:alert(1)", "file:///etc/passwd", "/api/iets", "ftp://host/a.png"):
            self.assertEqual(product_images.source_urls("jumbo", url), [])


class SaveBoughtTest(Case):
    URL = "https://static.ah.nl/dam/product/AHI_1?revLabel=1&rendition=200x200_JPG_Q85&fileType=binary"
    LARGE = "https://static.ah.nl/dam/product/AHI_1?revLabel=1&rendition=800x800_JPG_Q90&fileType=binary"

    def test_an_offer_you_bought_keeps_its_image_at_most_1024_wide(self):
        self.bought(101, "2026-10-01", order_item(1, "AH Penne"))
        self.served[self.LARGE] = picture(2000, 2000)
        self.sync("2026-10-05T05:00:00+00:00", offer("1", "AH Penne", self.URL), offer("2", "AH Fusilli", "https://static.ah.nl/2.jpg"))
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual((row["product_id"], row["title"], row["source_url"], row["width"], row["height"]), ("1", "AH Penne", self.URL, 1024, 1024))
        self.assertEqual(row["saved_at"], "2026-10-05T05:00:00+00:00")
        self.assertEqual(self.asked, [self.LARGE], "alleen wat je kocht, en de grote versie eerst")

    def test_the_given_image_is_used_when_the_larger_one_fails(self):
        self.bought(101, "2026-10-01", order_item(1, "AH Penne"))
        self.served[self.URL] = picture(200, 200)
        self.sync("2026-10-05T05:00:00+00:00", offer("1", "AH Penne", self.URL))
        self.assertEqual(self.asked, [self.LARGE, self.URL])
        self.assertEqual([(r["width"], r["height"]) for r in self.rows()], [(200, 200)])

    def test_the_same_image_is_not_fetched_again_and_a_new_one_is_added(self):
        self.bought(101, "2026-10-01", order_item(1, "AH Penne"))
        self.served[self.LARGE] = picture(400, 400)
        self.sync("2026-10-05T05:00:00+00:00", offer("1", "AH Penne", self.URL))
        self.sync("2026-10-06T05:00:00+00:00", offer("1", "AH Penne", self.URL))
        self.assertEqual(self.asked, [self.LARGE])
        # De winkel geeft een nieuwe foto onder een nieuwe naam: die komt erbij, de oude blijft met de naam van toen.
        self.served["https://static.ah.nl/nieuw.jpg"] = picture(500, 500, color=(10, 200, 10))
        self.sync("2026-10-12T05:00:00+00:00", offer("1", "AH Penne rigate", "https://static.ah.nl/nieuw.jpg"))
        self.assertEqual([r["title"] for r in self.rows()], ["AH Penne", "AH Penne rigate"])
        # Dezelfde foto onder een ander adres: geen tweede kopie, wel het nieuwe adres (zodat hij niet steeds opnieuw komt).
        self.served["https://static.ah.nl/kopie.jpg"] = picture(500, 500, color=(10, 200, 10))
        self.sync("2026-10-13T05:00:00+00:00", offer("1", "AH Penne rigate", "https://static.ah.nl/kopie.jpg"))
        self.assertEqual([r["source_url"] for r in self.rows()], [self.URL, "https://static.ah.nl/kopie.jpg"])
        asked = len(self.asked)
        self.sync("2026-10-14T05:00:00+00:00", offer("1", "AH Penne rigate", "https://static.ah.nl/kopie.jpg"))
        self.assertEqual(len(self.asked), asked)

    def test_an_earlier_offer_you_bought_gets_its_image_too(self):
        self.sync("2026-09-01T05:00:00+00:00", offer("7", "AH Rundergehakt", "https://static.ah.nl/7.jpg"))
        self.sync("2026-09-08T05:00:00+00:00")  # de aanbieding is voorbij
        self.bought(101, "2026-09-03", order_item(7, "AH Rundergehakt"))
        self.served["https://static.ah.nl/7.jpg"] = picture(300, 300)
        self.sync("2026-09-15T05:00:00+00:00", offer("1", "AH Penne", self.URL))
        self.assertEqual([(r["product_id"], r["title"]) for r in self.rows()], [("7", "AH Rundergehakt")])

    def test_a_failure_never_stops_the_sync(self):
        self.bought(101, "2026-10-01", order_item(1, "AH Penne"))
        self.served[self.LARGE] = b"<html>kapot</html>"
        self.sync("2026-10-05T05:00:00+00:00", offer("1", "AH Penne", self.URL))
        self.assertEqual(self.rows(), [])
        with mock.patch.object(prices, "bought_ids", side_effect=RuntimeError("stuk")):
            self.sync("2026-10-06T05:00:00+00:00", offer("1", "AH Penne", self.URL))
        self.assertEqual(offers.load("ah")["total"], 1)

    def test_a_slow_store_does_not_hold_up_the_sync(self):
        self.bought(101, "2026-10-01", order_item(1, "AH Penne"))

        def slow(url):
            time.sleep(0.5)
            return picture(100, 100)

        with mock.patch.object(product_images, "_download", side_effect=slow), mock.patch.object(product_images, "DEADLINE_SECONDS", 0.1):
            started = time.monotonic()
            self.sync("2026-10-05T05:00:00+00:00", offer("1", "AH Penne", self.URL))
        self.assertLess(time.monotonic() - started, 0.45)
        self.assertEqual(self.rows(), [])

    def test_at_most_ten_per_product_and_the_first_one_stays(self):
        self.bought(101, "2026-10-01", order_item(1, "AH Penne"))
        for day in range(1, 14):
            url = f"https://static.ah.nl/{day}.jpg"
            self.served[url] = picture(50, 50, color=(day * 15, 0, 0))
            self.sync(f"2026-10-{day:02d}T05:00:00+00:00", offer("1", f"AH Penne {day}", url))
        titles = [r["title"] for r in self.rows()]
        self.assertEqual(len(titles), product_images.MAX_PER_PRODUCT)
        self.assertEqual(titles[0], "AH Penne 1")
        self.assertEqual(titles[-1], "AH Penne 13")

    def test_the_price_history_and_the_route_show_the_saved_images(self):
        self.bought(101, "2026-10-01", order_item(1, "AH Penne"))
        self.served[self.LARGE] = picture(100, 80)
        self.sync("2026-10-05T05:00:00+00:00", offer("1", "AH Penne", self.URL))
        product = run(prices.history("ah", "penne", 5, today=TODAY))["products"][0]
        [saved] = product["savedImages"]
        self.assertEqual((saved["title"], saved["savedAt"], saved["width"], saved["height"]), ("AH Penne", "2026-10-05T05:00:00+00:00", 100, 80))
        self.assertRegex(saved["url"], r"^/api/prices/images/\d+\?v=[0-9a-f]{16}$")
        client = SyncClient(create_app())
        reply = client.get(saved["url"])
        self.assertEqual(reply.status_code, 200)
        self.assertEqual(reply.mimetype, "image/jpeg")
        self.assertEqual(reply.data, self.rows()[0]["data"])
        self.assertIn("immutable", reply.headers["Cache-Control"])
        self.assertEqual(reply.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(client.get("/api/prices/images/999").status_code, 404)
        self.assertEqual(client.get("/api/prices/images/abc").status_code, 404)
        self.assertEqual(run(prices.history("ah", "fusilli", 5, today=TODAY))["products"], [])


class PageTest(unittest.TestCase):
    def test_the_switch_and_the_images_are_on_the_page(self):
        html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "static" / "offers_history.js").read_text(encoding="utf-8")
        self.assertRegex(html, r'<input type="checkbox" id="offers-history-bought" role="switch">')
        self.assertIn('params.set("bought", "1")', script)
        self.assertIn("savedImages", script)
        self.assertTrue(re.search(r"localStorage\.(get|set)Item\(OFFER_HISTORY_BOUGHT_KEY", script))

    def test_pillow_is_a_requirement(self):
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        self.assertRegex(requirements, r"(?im)^pillow==\d")


if __name__ == "__main__":
    unittest.main()
