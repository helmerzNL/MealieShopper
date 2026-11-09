"""Aldi: de aanbiedingen van de openbare aanbiedingenpagina lezen (mealieshopper/aldi.py). Zie docs/onderzoek-supermarkten.md."""
import json
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("MEALIESHOPPER_DATA_DIR", tempfile.mkdtemp())

import httpx

from mealieshopper import aldi, deals  # noqa: E402
from helpers import run, served  # noqa: E402

FIXTURE = (Path(__file__).parent / "fixtures" / "aldi-aanbiedingen.html").read_text(encoding="utf-8")
SUNDAY = date(2026, 11, 8)  # de dag van de kopie


def stamp(day, end=False):
    """Unix-seconden van het begin (of het einde) van een dag in Nederland, in de winter."""
    moment = datetime(day.year, day.month, day.day, 23 if end else 0, 59 if end else 0, 59 if end else 0) - timedelta(hours=1)
    return int(moment.replace(tzinfo=timezone.utc).timestamp())


def item(product_id="1", name="Kaas", label="OP=OP", **extra):
    """Een product zoals op de pagina, met een prijs van vandaag tot over een week."""
    today = date.today()
    data = {
        "objectID": product_id,
        "name": name,
        "salesUnit": "400 g",
        "productSlug": f"kaas-{product_id}",
        "assets": [{"type": "primary", "url": f"https://s7g10.scene7.com/is/image/aldinord/product_{product_id}"}],
        "currentPrice": {"priceValue": 2.49, "priceTagLabels": {"promoText1": label}, "validFrom": stamp(today), "validUntil": stamp(today + timedelta(days=7), True)},
    }
    data.update(extra)
    return data


def page(items, categories=(), nest=False, as_text=True):
    api = [["OTHER", {"res": {}}], ["OFFER_GET", {"res": {"algoliaDataMap": {i["objectID"]: i for i in items}, "categories": list(categories)}}]]
    value = json.dumps(api) if as_text else api
    props = {"pageProps": {"deeper": {"apiData": value}}} if nest else {"pageProps": {"apiData": value}}
    return f'<script id="__NEXT_DATA__" type="application/json">{json.dumps({"props": props})}</script>'


def group(title, product_ids, start, end):
    return {"title": "Vanaf maandag", "startDate": str(start), "endDate": str(end), "content": [{"title": title, "productIds": list(product_ids)}]}


class ReadPageTest(unittest.TestCase):
    def setUp(self):
        self.offers = {offer["id"]: offer for offer in aldi.offers_from_html(FIXTURE, today=SUNDAY)}

    def test_reads_the_food_offers(self):
        self.assertEqual(sorted(self.offers), ["1200722", "1219324", "91207482", "91239792"])

    def test_a_promotion_price_with_an_old_price(self):
        self.assertEqual(
            self.offers["1219324"],
            {
                "id": "1219324", "title": "Mandarijnen", "brand": None, "unitSize": "1 kg", "price": 1.69, "priceBefore": 1.99,
                "promoLabel": "-15%",
                "image": "https://s7g10.scene7.com/is/image/aldinord/product_1219324_main_cms_9_nl_nl_mandarijnen_cw01_2024?wid=500&hei=500",
                "url": "https://www.aldi.nl/product/mandarijnen-1219324.html", "validFrom": "2026-11-09", "validUntil": "2026-11-15",
                "categoryHint": "Aardappels, groente en fruit",
            },
        )

    def test_two_for_is_a_price_per_piece(self):
        water = self.offers["91239792"]
        self.assertEqual((water["price"], water["promoLabel"]), (1.5, "2 voor 3,00"))
        described = deals.describe(water["promoLabel"], price_now=water["price"], price_before=water["priceBefore"])
        self.assertEqual((described["mechanic"], described["minQuantity"], described["unitPrice"]), ("x_for_y", 2, 1.5))

    def test_a_labelled_current_price_counts_outside_its_group(self):
        """Op de zondag van de wissel staan de groepen nog op de oude week; een prijs met een label is dan al die van de nieuwe."""
        pomegranate = self.offers["1200722"]
        self.assertEqual((pomegranate["validFrom"], pomegranate["validUntil"], pomegranate["promoLabel"]), ("2026-11-09", "2026-11-20", "OP=OP"))

    def test_left_out(self):
        self.assertNotIn("1201156", self.offers, "een kale prijs na de periode van de groep is de gewone prijs")
        self.assertNotIn("91200167", self.offers, "zonder prijs")
        self.assertNotIn("1213036", self.offers, "speelgoed")
        self.assertNotIn("1244080", self.offers, "'-- Select option --' is geen label, en de prijs valt na de groep")

    def test_a_plain_price_inside_its_group_stays(self):
        today = date.today()
        plain = item("5", label="-- Select option --")
        [offer] = aldi.offers_from_html(page([plain], [group("Weekacties", ["5"], today - timedelta(days=1), today + timedelta(days=7))]))
        self.assertEqual((offer["promoLabel"], offer["price"]), ("", 2.49))

    def test_non_food_groups_and_categories(self):
        today = date.today()
        toys = item("6", "Speelkeuken")
        mixed = item("7", "Kerstkransjes")
        coat = item("8", "Jas", mainCategoryID="kleding-heren")
        categories = [group("Speelgoed", ["6", "7"], today, today + timedelta(days=7)), group("Weekacties", ["7"], today, today + timedelta(days=7))]
        self.assertEqual([offer["id"] for offer in aldi.offers_from_html(page([toys, mixed, coat], categories))], ["7"])

    def test_api_data_as_a_list_or_deeper(self):
        for nest, as_text in ((True, True), (False, False)):
            with self.subTest(nest=nest, as_text=as_text):
                self.assertEqual(len(aldi.offers_from_html(page([item()], nest=nest, as_text=as_text))), 1)

    def test_an_unknown_page_is_an_error(self):
        for text in ("<html></html>", '<script id="__NEXT_DATA__" type="application/json">{"props": {}}</script>', page([]).replace("OFFER_GET", "X").replace("algoliaDataMap", "y")):
            with self.subTest(text=text[:40]), self.assertRaises(aldi.AldiError):
                aldi.offers_from_html(text)

    def test_expired_periods_and_odd_items(self):
        old = item("2", currentPrice={"priceValue": 1.0, "validFrom": stamp(date(2020, 1, 1)), "validUntil": stamp(date(2020, 1, 5), True)})
        gone = item("3", isAvailable=False)
        unnamed = item("4", name="")
        bad = item("9", productSlug="../x", assets=[{"type": "primary", "url": "https://elders.example/x.jpg"}])
        offers = aldi.offers_from_html(page([item("1"), old, gone, unnamed, bad]))
        self.assertEqual([offer["id"] for offer in offers], ["1", "9"])
        self.assertEqual((offers[1]["url"], offers[1]["image"]), ("", ""), "geen vreemd adres en geen foto van een andere host")

    def test_per_kilo(self):
        [offer] = aldi.offers_from_html(page([item(salesUnit="Per kg")]))
        self.assertEqual(offer["unitSize"], "1 kg")


class FetchTest(unittest.TestCase):
    def test_fetches_the_page_and_names_itself(self):
        with served(return_value=httpx.Response(200, text=page([item()]))) as get:
            offers = run(aldi.get_offers())
        self.assertEqual(len(offers), 1)
        request = get.calls[0].request
        self.assertEqual(str(request.url), aldi.OFFERS_URL)
        self.assertTrue(request.headers["User-Agent"].startswith("MealieShopper/"))

    def test_errors_are_our_own_texts(self):
        cases = {
            "geweigerd": httpx.Response(403, text="nee"),
            "kapot": httpx.Response(503, text="oeps"),
            "leeg": httpx.Response(200, text=page([])),
            "onbekend": httpx.Response(200, text="<html></html>"),
            "elders": httpx.Response(302, headers={"Location": "https://elders.example/"}),
        }
        for name, response in cases.items():
            with self.subTest(name), served(side_effect=[response, httpx.Response(200, text=page([item()]))]):
                with self.assertRaises(aldi.AldiError):
                    run(aldi.get_offers())
        with served(side_effect=httpx.ConnectError("weg")), self.assertRaises(aldi.AldiError):
            run(aldi.get_offers())
        self.assertFalse(issubclass(aldi.AldiError, ValueError))


if __name__ == "__main__":
    unittest.main()
