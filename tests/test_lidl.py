"""Lidl: de aanbiedingen van de openbare aanbiedingenpagina lezen (mealieshopper/lidl.py). Zie docs/onderzoek-lidl.md."""

import html
import json
import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

os.environ.setdefault("MEALIESHOPPER_DATA_DIR", tempfile.mkdtemp())

import httpx
from helpers import run, served  # noqa: E402

from mealieshopper import deals, lidl  # noqa: E402

FIXTURE = (Path(__file__).parent / "fixtures" / "lidl-aanbiedingen.html").read_text(encoding="utf-8")
WEEK = date(2026, 11, 9)  # de maandag van de week op de kopie


def card(product_id="1", title="Kaas", **extra):
    """Een kaart zoals op de pagina, met een gewone aanbieding van vandaag tot over een week."""
    today = date.today()
    data = {
        "productId": product_id,
        "fullTitle": title,
        "category": "Food",
        "canonicalUrl": f"/p/kaas/p{product_id}",
        "image": "https://www.lidl.nl/assets/kaas.png",
        "regionsPrices": {
            "1": {
                "currentPrice": {
                    "price": 2.49,
                    "startDate": f"{today}T00:00Z",
                    "endDate": f"{today + timedelta(days=7)}T22:59:59Z",
                    "discount": {"discountText": "Actie", "deletedPrice": 2.99},
                    "packaging": {"text": "400 g"},
                },
            },
        },
    }
    data.update(extra)
    return data


def page(*cards):
    return "".join(f'<div data-grid-data="{html.escape(json.dumps(item), quote=True)}"></div>' for item in cards)


class ReadPageTest(unittest.TestCase):
    def setUp(self):
        self.offers = {offer["id"]: offer for offer in lidl.offers_from_html(FIXTURE, today=WEEK)}

    def test_reads_the_food_offers_once_each(self):
        self.assertEqual(len(self.offers), 8, "tien kaarten, min planten en non-food; de kapotte kaart en de dubbele vallen weg")
        titles = {offer["title"] for offer in self.offers.values()}
        self.assertNotIn("Rozen", titles)
        self.assertNotIn("Schlumergera 4-kleurig", titles)

    def test_a_regular_offer(self):
        offer = self.offers["11563244"]
        self.assertEqual(
            offer,
            {
                "id": "11563244",
                "title": "Croky chips",
                "brand": None,
                "unitSize": "300 g",
                "price": 1.99,
                "priceBefore": None,
                "promoLabel": "XXL",
                "image": "https://www.lidl.nl/assets/gcpad4c3cfc15244ff09cf071ee7400b34f.png",
                "url": "https://www.lidl.nl/p/croky-chips/p11563244",
                "validFrom": "2026-11-09",
                "validUntil": "2026-11-15",
                "categoryHint": "Hartige hapjes",
            },
        )

    def test_old_price_and_percentage(self):
        wine = self.offers["11372028"]
        self.assertEqual((wine["price"], wine["priceBefore"], wine["promoLabel"]), (3.29, 4.39, "-25%"))
        self.assertEqual(wine["title"], "AT Grüner Veltliner niederösterreic", "zonder het streepje ervoor")
        self.assertIsNone(wine["brand"], "een merk '-' is geen merk")
        bread = self.offers["11309710"]
        self.assertEqual((bread["price"], bread["priceBefore"], bread["promoLabel"]), (1.19, 1.39, "Actie"))

    def test_a_lidl_plus_price(self):
        oranges = self.offers["11115702"]
        self.assertEqual((oranges["price"], oranges["priceBefore"], oranges["promoLabel"]), (1.99, 2.89, "-31% met Lidl Plus"))
        self.assertEqual((oranges["validFrom"], oranges["validUntil"]), ("2026-11-05", "2026-11-15"), "zonder winkeldatum de start van de prijs")
        described = deals.describe(oranges["promoLabel"], price_now=oranges["price"], price_before=oranges["priceBefore"])
        self.assertEqual((described["mechanic"], described["unitPrice"]), ("percent", 1.99))
        bananas = self.offers["11029818"]
        self.assertEqual((bananas["validFrom"], bananas["promoLabel"]), ("2026-11-09", "-32% met Lidl Plus"), "met winkeldatum die")

    def test_dates_are_dutch_days(self):
        snacks = self.offers["11001449"]
        self.assertEqual((snacks["validFrom"], snacks["validUntil"]), ("2026-11-12", "2026-11-15"), "vanaf donderdag, tot en met zondag")
        self.assertEqual(snacks["brand"], "Snack Day")

    def test_an_offer_without_a_label_stays(self):
        mince = self.offers["12181399"]
        self.assertEqual((mince["title"], mince["price"], mince["promoLabel"]), ("Rundergehakt", 10.49, ""))

    def test_a_regular_price_is_no_offer(self):
        regular = card(regionsPrices={"1": {"currentPrice": {"price": 1.0, "startDate": "2026-01-01T00:00Z", "endDate": "2200-01-01T22:59:59Z"}}})
        self.assertEqual(lidl.offers_from_html(page(regular)), [])

    def test_a_regular_offer_wins_from_lidl_plus(self):
        region = card()["regionsPrices"]["1"]
        region["currentLidlPlusPrice"] = {"price": {"price": 1.0, "oldPrice": 2.49, "endDate": region["currentPrice"]["endDate"]}}
        [offer] = lidl.offers_from_html(page(card(regionsPrices={"1": region})))
        self.assertEqual((offer["price"], offer["promoLabel"]), (2.49, "Actie"))

    def test_expired_offers_and_odd_cards_are_skipped(self):
        old = card("2", regionsPrices={"1": {"currentPrice": {"price": 1.0, "endDate": "2020-01-05T22:59:59Z"}}})
        nameless = card("3", fullTitle="")
        unpriced = card("4", regionsPrices={"1": {"currentPrice": {"endDate": card()["regionsPrices"]["1"]["currentPrice"]["endDate"]}}})
        self.assertEqual([offer["id"] for offer in lidl.offers_from_html(page(card("1"), old, nameless, unpriced, ["geen", "kaart"]))], ["1"])

    def test_pieces_and_relative_images(self):
        [offer] = lidl.offers_from_html(
            page(
                card(
                    image="/assets/kaas.png",
                    regionsPrices={"1": {"currentPrice": {**card()["regionsPrices"]["1"]["currentPrice"], "packaging": {"text": "1 Stk."}}}},
                )
            )
        )
        self.assertEqual((offer["unitSize"], offer["image"]), ("1 stuks", "https://www.lidl.nl/assets/kaas.png"))
        [offer] = lidl.offers_from_html(page(card(image="http://elders.example/kaas.png", canonicalUrl="https://elders.example/")))
        self.assertEqual((offer["image"], offer["url"]), ("", ""), "alleen https, en alleen een productpagina van Lidl")


class FetchTest(unittest.TestCase):
    def test_follows_the_redirect_and_names_itself(self):
        moved = httpx.Response(301, headers={"Location": "https://www.lidl.nl/c/store/aanbiedingen/a10008785"})
        with served(side_effect=[moved, httpx.Response(200, text=page(card()))]) as get:
            offers = run(lidl.get_offers())
        self.assertEqual([offer["id"] for offer in offers], ["1"])
        first, second = get.calls[0].request, get.calls[1].request
        self.assertEqual(str(first.url), lidl.OFFERS_URL)
        self.assertEqual(str(second.url), "https://www.lidl.nl/c/store/aanbiedingen/a10008785")
        self.assertTrue(first.headers["User-Agent"].startswith("MealieShopper/"))
        self.assertNotIn("Cookie", second.headers)

    def test_errors_are_our_own_texts(self):
        cases = {
            "geweigerd": httpx.Response(403, text="nee"),
            "te vaak": httpx.Response(429, text="later"),
            "kapot": httpx.Response(500, text="oeps"),
            "leeg": httpx.Response(200, text="<html></html>"),
            "elders": httpx.Response(302, headers={"Location": "https://elders.example/"}),
        }
        for name, response in cases.items():
            with self.subTest(name), served(side_effect=[response, httpx.Response(200, text=page(card()))]):
                with self.assertRaises(lidl.LidlError):
                    run(lidl.get_offers())
        with served(side_effect=httpx.ConnectError("weg")), self.assertRaises(lidl.LidlError):
            run(lidl.get_offers())

    def test_an_error_is_no_value_error(self):
        """Een ValueError wordt een 400 (een fout van de gebruiker); een fout van Lidl hoort een 424 te geven."""
        self.assertFalse(issubclass(lidl.LidlError, ValueError))


if __name__ == "__main__":
    unittest.main()
