"""Regressietests voor de bugs die de eigenaar van de installatie meldde, een test per melding, met de melding erbij.

Elke melding hier is een keer gerepareerd en daarna weer stukgegaan, of had dat gekund zonder test. De tests lopen waar het
kan door de echte routes (`/api/offers`, `/api/prices/history`, `/healthz`) en door de echte scripts in Node, zodat een
nieuwe versie die het gedrag terugdraait hier rood wordt. Een nieuwe melding krijgt hier een nieuwe test.
"""

import inspect
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
import zipfile
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from unittest import mock

import httpx
import jsonschema
import jwt
import test_ah_orders  # als module: de opzet van het AH-mandje
import test_history_chart_js  # als module, voor de Node-opbouw van offers_history.js
import test_history_stores_js  # als module: anders draait pytest die tests hier nog een keer
import test_loose_names  # idem: de opzet van de losse aankoopnamen
import test_offers  # idem: de opzet van de receptvoorstellen
import test_offers_scan_js  # idem: de Node-opbouw van offers.js
import test_order_views  # idem: de Node-opbouw van order_views.js en de gedeelde keuzelijst
import test_price_archive
import test_price_import  # idem
import test_shared_links  # idem: de gedeelde lijst en zijn opzet
from helpers import AppTestCase, db, run, sent_json, served  # helpers zet ook het pad naar de app
from test_auth_password import PASSWORD, AuthPasswordTestCase
from test_prices import PricesCase, offer

from mealieshopper import (
    ah,
    ah_orders,
    assets,
    auth,
    deals,
    image_cache,
    line_offers,
    loose_names,
    offer_categories,
    offer_ranking,
    offers,
    offers_schedule,
    price_archive,
    price_archive_tidy,
    price_import,
    prices,
    product_links,
)
from mealieshopper.app import create_app

ROOT: Path = Path(__file__).resolve().parents[1]
TODAY: str = date.today().isoformat()


def days(n: int) -> str:
    return (date.today() + timedelta(days=n)).isoformat()


def order_item(product_id: int, title: str, price: float, unit_size: str = "500 g") -> dict:
    return {
        "productId": product_id,
        "title": title,
        "brand": "AH",
        "unitSize": unit_size,
        "quantity": 1,
        "amount": price,
        "price": price,
        "priceBeforeBonus": None,
        "bonus": False,
        "taxonomy": "",
        "image": "https://static.ah.nl/x.png",
    }


class PriceHistoryLinkTest(PricesCase):
    """Meldingen over het doorklikken naar de prijsgeschiedenis: het product uit de link hoort er altijd in, bovenaan."""

    async def linked(self, query: str, pin: str, **params: str) -> list[dict]:
        client = self.client_for(create_app())
        response = await client.get(
            "/api/prices/history",
            query_string={
                "store": "ah",
                "q": query,
                "limit": "30",
                "pin": pin,
                **params,
            },
        )
        self.assertEqual(response.status_code, 200)
        return response.get_json()["products"]

    async def test_2026_11_08_offer_card_of_next_week_finds_the_product_by_id(self):
        # "als ik van /offers doorklik naar het eerste product dan zie ik geen resultaten. daarnaast lijkt hij weer te zoeken op
        # basis van product naam in plaats van product id" (4.1.4). De bonus van volgende week staat nog niet in offer_history.
        await self.sync_offer(
            f"{TODAY}T05:00:00+00:00",
            offer(
                "233387",
                "Dr. Oetker Casa di mama pizza salame extra piccante",
                3.65,
                4.29,
                "Bonus Box 15% korting",
                validFrom=days(7),
                validUntil=days(13),
            ),
        )
        await prices.record(
            "ah",
            [
                {
                    "id": "514010",
                    "title": "Dr. Oetker Casa di mama pizza salami",
                    "price": 4.29,
                }
            ],
        )
        found = await self.linked("Dr. Oetker Casa di mama", "233387")
        self.assertEqual(found[0]["productId"], "233387")
        self.assertEqual(found[0]["upcoming"]["validFrom"], days(7))
        self.assertIn(
            "514010",
            [p["productId"] for p in found],
            "de andere producten met die naam staan eronder",
        )
        # Ook als de naam in de link anders is dan in de geschiedenis, en met "Alleen eerder gekocht" aan.
        self.assertEqual(
            (await self.linked("een heel andere naam", "233387", bought="1"))[0]["productId"],
            "233387",
        )

    async def test_2026_11_09_product_from_an_open_order_is_found_by_id(self):
        # "als ik nu vanaf hier doorklik naar de prijsgeschiedenis gaat het weer niet per id en zie ik weer geen resultaten"
        # (4.1.7): AH Sweet Palermo rode puntpaprika stond alleen in een bestelling die nog open was.
        await self.ah_order(
            913252041,
            "open",
            days(-6),
            order_item(579310, "AH Sweet Palermo rode puntpaprika", 3.49),
        )
        await prices.record(
            "ah",
            [
                {
                    "id": "folder-like",
                    "title": "AH Sweet Palermo rode puntpaprika zak 500 gram",
                    "price": 2.49,
                }
            ],
        )
        found = await self.linked("AH Sweet Palermo rode puntpaprika", "579310")
        self.assertEqual(
            (found[0]["productId"], found[0]["title"], found[0]["unitSize"]),
            ("579310", "AH Sweet Palermo rode puntpaprika", "500 g"),
        )

    async def test_2026_11_09_product_from_a_receipt_link_is_found_by_id(self):
        # Een bonregel met een artikelnummer dat AH aan een webwinkelproduct koppelde, zonder aankoop die de catalogus telt.
        with closing(db(ah_orders)) as conn, conn:
            conn.execute(
                "INSERT INTO ah_receipt_products (receipt_product_id, product_id, title, checked_at, image) VALUES (22924, 100845, "
                "'AH Gekoelde sinaasappeldrank', '2026-11-06T09:42:43+00:00', '')"
            )
        self.assertEqual(
            (await self.linked("AH Gekoelde sinaasappeldrank", "100845"))[0]["productId"],
            "100845",
        )

    async def test_2026_11_09_offer_that_started_before_the_nightly_sync_is_found(self):
        # Om middernacht begint de nieuwe bonusweek; offer_history krijgt de aanbieding pas bij de synchronisatie van die nacht.
        await self.sync_offer(
            f"{TODAY}T05:00:00+00:00",
            offer(
                "188141",
                "AH Geschilde bospeen",
                1.29,
                1.79,
                "25% korting",
                validFrom=days(1),
                validUntil=days(7),
            ),
        )
        found = (await prices.history("ah", "bospeen", 30, today=days(1), pin="188141"))["products"]
        self.assertEqual(found[0]["productId"], "188141")  # hoe hij dan telt: zie de test hieronder

    async def test_2026_11_09_offer_that_started_at_midnight_counts_and_has_a_chart(self):
        # "werkt nogsteeds niet zo te zien. ik zie hier staan 0 keer in de aanbieding en ik zie geen prijs grafiek" (4.1.8): de
        # bonusweek begon om middernacht, offer_history krijgt hem pas bij de synchronisatie van die nacht.
        await self.sync_offer(
            f"{TODAY}T05:00:00+00:00",
            offer(
                "233387",
                "Dr. Oetker Casa di mama pizza salame extra piccante",
                3.65,
                4.29,
                "Bonus Box 15% korting",
                validFrom=days(1),
                validUntil=days(7),
            ),
        )

        class Tomorrow(date):
            @classmethod
            def today(cls):
                return date.today() + timedelta(days=1)

        with mock.patch.object(offers, "date", Tomorrow):
            product = (await prices.history("ah", "Dr. Oetker", 30, today=days(1), pin="233387"))["products"][0]
        self.assertEqual((product["productId"], product["timesOnOffer"], product["onOfferNow"], product["upcoming"]), ("233387", 1, True, None))
        self.assertEqual([(e["start"], e["price"], e["priceBefore"]) for e in product["episodes"]], [(days(1), 3.65, 4.29)])
        self.assertEqual(product["regularPrice"], 4.29, "de prijs ervoor geeft de grafiek een gewone prijs")

    async def test_2026_11_09_the_folder_offers_for_the_whole_line_are_in_the_history(self):
        # "maar dan nog klopt het niet, die dr. oetker pizza's zijn zeer regelmatig in de aanbieding, waar is de geschiedenis?"
        # (4.1.9). Het folderarchief noemt ze als actie voor de hele lijn ("Alle Dr. Oetker Casa di Mama pizza's"): de import gooide
        # die als combi weg, en een folderproduct "Dr. Oetker Casa di Mama" hoorde bij geen enkele pizza.
        await self.sync_offer(
            f"{TODAY}T05:00:00+00:00",
            offer(
                "233387",
                "Dr. Oetker Casa di mama pizza salame extra piccante",
                3.65,
                4.29,
                "Bonus Box 15% korting",
                validFrom=TODAY,
                validUntil=days(6),
            ),
        )
        folder = [
            ("folder:a", "Folder: Alle Dr. Oetker Casa di Mama pizza's", "2009-10-26", "2009-11-01", "2 dozen, actie 3.50, was 2 stuks 5.38"),
            ("folder:b", "Folder: Dr. Oetker Casa di Mama", "2016-11-14", "2016-11-20", "2 voor 4.00"),
            ("folder:c", "Folder: Dr. Oetker pizza's", "2016-11-14", "2016-11-20", "dezelfde week: telt een keer"),
            ("folder:d", "Folder: Alle Dr. Oetker Ristorante pizza's", "2017-01-09", "2017-01-15", "een andere lijn"),
            ("folder:e", "Folder: Dr. Oetker Casa di Mama", TODAY, days(6), "de week van de eigen aanbieding"),
        ]
        with closing(db(prices)) as conn, conn:
            for product_id, title, start, end, label in folder:
                conn.execute(
                    "INSERT INTO offer_history (store, product_id, title, brand, promo_label, valid_from, valid_until, first_seen, last_seen, "
                    "ended_at) VALUES ('ah', ?, ?, 'Folderarchief', ?, ?, ?, ?, ?, ?)",
                    (product_id, title, label, start, end, f"{start}T00:00:00+00:00", f"{end}T23:59:59+00:00", f"{end}T23:59:59+00:00"),
                )
        product = (await self.linked("Dr. Oetker Casa di mama pizza salame extra piccante", "233387"))[0]
        self.assertEqual(product["productId"], "233387")
        self.assertEqual(
            [(line["start"], line["title"], line["promoLabel"]) for line in product["lineOffers"]],
            [
                ("2009-10-26", "Folder: Alle Dr. Oetker Casa di Mama pizza's", "2 dozen, actie 3.50, was 2 stuks 5.38"),
                ("2016-11-14", "Folder: Dr. Oetker Casa di Mama", "2 voor 4.00"),
            ],
        )
        self.assertEqual((product["timesInLineOffer"], product["timesOnOffer"]), (2, 1))

    async def test_2026_11_09_line_offers_have_a_price_per_piece_and_purchases_give_the_regular_price(self):
        # "ik zie nogsteeds geen andere prijzen behalve de huidige hier ... ik weet zeker dat ik deze ook meermaals besteld heb dus
        # ook dat mist hierbij. de prijsgrafiek is ook stuk nu." (4.1.10, Dr. Oetker Casa di mama pizza salami, 514010). Het archief
        # gaf "2 voor 6.00" de ene week als 3.00 per stuk en de andere als 6.00 voor twee, de import gooide de prijs zonder
        # verpakking weg, en de gewone prijs kwam alleen uit de bestelling van de laatste tien dagen.
        await self.sync_offer(
            f"{TODAY}T05:00:00+00:00",
            offer(
                "514010",
                "Dr. Oetker Casa di mama pizza salami",
                3.65,
                4.29,
                "Bonus Box 15% korting",
                unitSize="390 g",
                validFrom=TODAY,
                validUntil=days(6),
            ),
        )
        folder = [
            ("folder:a", "Folder: Alle Dr. Oetker Casa di Mama en Tradizionale", "2024-07-01", 3.0, 4.29, "2 voor: 6.00, was 8.58"),
            ("folder:a", "Folder: Alle Dr. Oetker Casa di Mama en Tradizionale", "2024-09-30", 6.0, 8.58, "Diepvries2 voor: 6.00, was 2 stuks 8.58"),
            ("folder:b", "Folder: Alle Dr. Oetker Casa di Mama", "2026-08-31", None, None, "1+1 kosteloos Diepvries, actie 1+1 gratis"),
            ("folder:c", "Folder: Diverse Dr. Oetker", "2026-09-07", 1.78, 2.09, "15 procent voordeel Bijv. brownies choco, actie 1.78, was 2.09"),
        ]
        with closing(db(prices)) as conn, conn:
            for product_id, title, start, price, before, label in folder:
                end = (date.fromisoformat(start) + timedelta(days=6)).isoformat()
                conn.execute(
                    "INSERT INTO offer_history (store, product_id, title, brand, price, price_before, promo_label, valid_from, valid_until, "
                    "first_seen, last_seen, ended_at) VALUES ('ah', ?, ?, 'Folderarchief', ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        product_id,
                        title,
                        price,
                        before,
                        label,
                        start,
                        end,
                        f"{start}T00:00:00+00:00",
                        f"{end}T23:59:59+00:00",
                        f"{end}T23:59:59+00:00",
                    ),
                )
        with closing(db(price_import)) as conn, conn:
            conn.execute(
                "INSERT INTO imported_purchases (store, order_ref, position, day, title, quantity, paid, regular, imported_at) "
                "VALUES ('ah', '232668613', 1, '2024-07-04', 'Dr. Oetker Casa di mama pizza salami', 1, 3.0, 4.29, ?)",
                (f"{TODAY}T00:00:00+00:00",),
            )
        product = (await self.linked("Dr. Oetker Casa di mama pizza salami", "514010"))[0]
        self.assertEqual(product["productId"], "514010")
        self.assertEqual(
            [(line["start"], line["price"], line["priceBefore"]) for line in product["lineOffers"]],
            [("2024-07-01", 3.0, 4.29), ("2024-09-30", 3.0, 4.29), ("2026-08-31", 2.15, 4.29)],
            "per stuk, zonder de brownies",
        )
        self.assertEqual([(purchase["date"], purchase["price"]) for purchase in product["purchases"]], [("2024-07-04", 3.0)])
        self.assertEqual(product["regular"][0]["from"], "2024-07-04", "de gewone prijs uit de ingelezen aankoop")
        self.assertEqual(product["unitSize"], "390 g")

    def test_2026_11_09_line_offers_from_the_archive_are_kept_on_import(self):
        # Dezelfde melding: met "Ook regels bewaren die bij geen product van jou horen" blijft een combi als eigen folderproduct.
        line = {
            "store": "ah",
            "product_id": "folder:x",
            "title": "Folder: Alle Dr. Oetker Casa di Mama pizza's",
            "price": None,
            "price_before": None,
            "unit_size": None,
            "description": "2 dozen",
            "source_title": "Alle Dr. Oetker Casa di Mama pizza's",
            "promo_label": "Folder | 2 dozen | bron: oud=2 stuks 5.38, actie=3.50 | prijs onzeker",
            "url": "https://www.supermarktaanbiedingen.com/folders/albert_heijn/2009/44/x",
        }
        with closing(db(prices)) as conn:
            kept = price_archive_tidy.Tidy(conn, keep_unlinked=True).offer(dict(line))
            dropped = price_archive_tidy.Tidy(conn).offer(dict(line))
        self.assertEqual((kept["product_id"], kept["promo_label"]), ("folder:x", "2 dozen, actie 3.50, was 2 stuks 5.38"))
        self.assertIsNone(dropped, "zonder keep_unlinked komt een combi er nog steeds niet in")

    async def test_an_unknown_id_changes_nothing(self):
        await prices.record("ah", [{"id": "1", "title": "AH Pindakaas", "price": 2.99}])
        self.assertEqual([p["productId"] for p in await self.linked("pindakaas", "99999999")], ["1"])


class SameNameTwoProductsTest(PricesCase):
    """2026-11-10: "ik zie hier 3x hetzelfde product, waarom zijn die niet samengevoegd?" (AH Sweet Palermo rode puntpaprika).

    AH verkoopt twee artikelen met op een hoofdletter na dezelfde naam: 2 stuks (123865, bekend van de kassabon) en een zak van
    500 gram (579310, bekend van een bestelling die nog open staat). De ingelezen aankopen hebben alleen die naam en bleven als
    derde rij `import:<naam>` staan.
    """

    NAME: str = "AH Sweet Palermo rode puntpaprika"
    IMPORTED: tuple[tuple[str, str, str, float, float], ...] = (
        ("M1", "2021-04-28", "AH Sweet palermo rode puntpaprika", 1.19, 1.55),
        ("M2", "2026-07-24", "AH Sweet palermo rode puntpaprika", 2.29, 3.29),
        ("M3", "2026-07-28", "AH Sweet palermo rode puntpaprika", 0.99, 1.69),
        ("M4", "2026-09-05", "AH Sweet Palermo rode puntpaprika", 2.23, 3.19),
    )

    async def asyncSetUp(self) -> None:
        self.ah_receipt("R1", "2024-11-11T12:00:00", (526638, "PUNTPAPRIKA", 1, 1.59, 1.59))
        self.ah_receipt("R2", "2025-06-06T12:00:00", (526638, "PUNTPAPRIKA", 1, 1.69, 1.69))
        with closing(db(ah_orders)) as conn, conn:
            conn.execute(
                "INSERT INTO ah_receipt_products (receipt_product_id, product_id, title, checked_at) "
                "VALUES (526638, 123865, 'AH Sweet palermo rode puntpaprika', 'x')"
            )
        await self.ah_order(913252041, "open", days(-7), order_item(579310, self.NAME, 3.49))
        with closing(db(price_import)) as conn, conn:
            conn.executemany(
                "INSERT INTO imported_purchases (store, order_ref, position, day, title, quantity, paid, regular, imported_at) "
                "VALUES ('ah', ?, 1, ?, ?, 1, ?, ?, 'x')",
                self.IMPORTED,
            )

    async def bought(self) -> dict[str, list[str]]:
        products: list[dict] = (await prices.history("ah", self.NAME, 50, today=TODAY))["products"]
        return {product["productId"]: [purchase["date"] for purchase in product["purchases"]] for product in products}

    async def test_imported_purchases_join_the_product_with_the_nearest_price(self) -> None:
        self.assertEqual(
            await self.bought(),
            {"579310": ["2026-07-24", "2026-09-05"], "123865": ["2021-04-28", "2024-11-11", "2025-06-06", "2026-07-28"]},
            "geen derde rij import:<naam>: de prijs kiest tussen de twee artikelen",
        )

    async def test_one_product_with_the_name_gets_every_imported_purchase(self) -> None:
        # Zonder de bonkoppeling kent de app alleen de zak van 500 gram: dan is er niets te kiezen.
        with closing(db(ah_orders)) as conn, conn:
            conn.execute("DELETE FROM ah_receipt_products")
        found: dict[str, list[str]] = await self.bought()
        self.assertEqual(found["579310"], ["2021-04-28", "2026-07-24", "2026-07-28", "2026-09-05"])
        self.assertNotIn("import:ah sweet palermo rode puntpaprika", found)

    def test_a_price_that_does_not_clearly_choose_keeps_the_first_product(self) -> None:
        # Uit de productiedata: een krat naast twee flesjes zegt niets, en 6,03 naast 6,46 is geen keuze.
        bottles: dict[str, tuple[float, ...]] = {"230582": (1.47,), "364160": (1.64, 1.67)}
        self.assertIsNone(prices._nearest_priced(bottles, {"regular": 7.49, "paid": 7.49}, "230582"))
        close: dict[str, tuple[float, ...]] = {"232208": (6.0, 6.03), "4044": (4.26, 6.46, 17.29)}
        self.assertIsNone(prices._nearest_priced(close, {"regular": 7.12, "paid": None}, "232208"))
        self.assertIsNone(prices._nearest_priced(close, {"regular": None, "paid": None}, "232208"), "zonder prijs valt er niets te kiezen")
        pack: dict[str, tuple[float, ...]] = {"238770": (7.99,), "606448": (2.49,)}
        self.assertEqual(prices._nearest_priced(pack, {"regular": 1.79, "paid": None}, "238770"), "606448")
        unpriced: dict[str, tuple[float, ...]] = {"231530": (), "395946": (2.29, 2.49)}
        self.assertEqual(prices._nearest_priced(unpriced, {"regular": 3.09, "paid": None}, "231530"), "395946")

    async def test_the_orders_page_links_the_name_to_a_known_product(self) -> None:
        linked: dict[str, dict] = await prices.link_names("ah", [self.NAME])
        self.assertIn(linked[self.NAME]["productId"], {"579310", "123865"})


class ReceiptLinkedPackSizeTest(PricesCase):
    """2026-11-10: "bij dit product staat niet hoeveel het is. is het X gram, X aantal?" (123865, AH Sweet palermo rode
    puntpaprika). De app kende het product alleen van de kassabon, en de bonkoppeling bewaarde de naam en de foto, niet de inhoud."""

    def link(self, **columns: str | None) -> None:
        self.ah_receipt("R1", "2025-06-06T12:00:00", (526638, "PUNTPAPRIKA", 1, 1.69, 1.69))
        row: dict[str, str | int | None] = {
            "receipt_product_id": 526638,
            "product_id": 123865,
            "title": "AH Sweet palermo rode puntpaprika",
            "checked_at": "x",
            "image": "https://static.ah.nl/p.png",
            **columns,
        }
        with closing(db(ah_orders)) as conn, conn:
            conn.execute(f"INSERT INTO ah_receipt_products ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", tuple(row.values()))

    async def test_the_price_history_shows_the_pack_size_of_the_link(self) -> None:
        self.link(brand="AH", unit_size="2 stuks")
        product: dict = await self.product("123865")
        self.assertEqual((product["unitSize"], product["brand"]), ("2 stuks", "AH"))

    async def test_a_size_in_the_search_finds_the_product(self) -> None:
        self.link(brand="AH", unit_size="2 stuks")
        found: list[str] = [p["productId"] for p in (await self.history(query="puntpaprika 2 stuks"))["products"]]
        missed: list[str] = [p["productId"] for p in (await self.history(query="puntpaprika 500 gram"))["products"]]
        self.assertEqual((found, missed), (["123865"], []))

    async def test_an_imported_purchase_of_the_product_shows_the_pack_size_too(self) -> None:
        self.link(brand="AH", unit_size="2 stuks")
        with closing(db(price_import)) as conn, conn:
            conn.execute(
                "INSERT INTO imported_purchases (store, order_ref, position, day, title, quantity, paid, regular, imported_at) "
                "VALUES ('ah', 'M1', 1, '2025-07-28', 'AH Sweet palermo rode puntpaprika', 1, 0.99, 1.69, 'x')"
            )
        linked: dict[str, dict] = await prices.link_names("ah", ["AH Sweet palermo rode puntpaprika"])
        self.assertEqual(linked["AH Sweet palermo rode puntpaprika"]["unitSize"], "2 stuks", "de bestelpagina")
        product: dict = await self.product("123865")
        self.assertEqual(product["unitSize"], "2 stuks", "de nieuwste gewone prijs komt uit de ingelezen aankoop")
        self.assertEqual(product["regularPerUnit"], prices.unit_price(1.69, "2 stuks"), "en dus ook de prijs per stuk")

    async def test_an_old_link_with_a_photo_still_gets_its_pack_size(self) -> None:
        self.link()  # van voor deze fix: wel een foto, geen inhoud
        answer: list[dict] = [
            {
                "id": 123865,
                "title": "AH Sweet palermo rode puntpaprika",
                "brand": "AH",
                "unitSize": "2 stuks",
                "image": "https://static.ah.nl/new.png",
            }
        ]
        with mock.patch.object(ah, "get_products_by_ids", new_callable=mock.AsyncMock, return_value=answer) as lookup:
            first: dict | None = await ah_orders.backfill_receipt_images_nightly("2026-11-10T03:00:00+00:00")
            second: dict | None = await ah_orders.backfill_receipt_images_nightly("2026-11-11T03:00:00+00:00")
        self.assertEqual(
            (lookup.call_count, first, second), (1, {"products": 0, "requests": 1, "failed": 0}, None), "een keer, daarna is het compleet"
        )
        with closing(db(ah_orders)) as conn:
            saved: tuple = tuple(conn.execute("SELECT brand, unit_size, image FROM ah_receipt_products WHERE product_id = 123865").fetchone())
        self.assertEqual(saved, ("AH", "2 stuks", "https://static.ah.nl/p.png"), "de foto die er al was blijft")
        self.assertEqual((await self.product("123865"))["unitSize"], "2 stuks")

    async def test_a_link_to_another_product_forgets_the_old_pack_size(self) -> None:
        self.link(brand="AH", unit_size="2 stuks")
        with closing(db(ah_orders)) as conn, conn:
            conn.execute(ah_orders._UPSERT_RECEIPT_PRODUCT, (526638, 579310, "AH Sweet Palermo rode puntpaprika", "y", "https://static.ah.nl/q.png"))
            conn.execute(ah_orders._UPSERT_RECEIPT_PRODUCT, (526638, 579310, "AH Sweet Palermo rode puntpaprika", "z", "https://static.ah.nl/q.png"))
            saved: tuple = tuple(conn.execute("SELECT product_id, brand, unit_size FROM ah_receipt_products").fetchone())
        self.assertEqual(saved, (579310, None, None))


class OrdersSyncedNightlyTest(PricesCase):
    """2026-11-10: bij dezelfde melding stond de bestelling van 3 november een week na de bezorging nog op "open", en haar
    producten ontbraken in de prijsgeschiedenis. Bestellingen en kassabonnen kwamen alleen binnen via de knop op de bestelpagina."""

    async def run_daily(self, **kwargs: bool) -> dict:
        with (
            mock.patch.object(offers, "sync_all", new_callable=mock.AsyncMock, return_value={"stores": {}}),
            mock.patch.object(ah_orders, "resolve_receipt_products_nightly", new_callable=mock.AsyncMock, return_value=None),
            mock.patch.object(ah_orders, "backfill_receipt_images_nightly", new_callable=mock.AsyncMock, return_value=None),
            mock.patch.object(image_cache, "prefetch_nightly", new_callable=mock.AsyncMock, return_value=None),
            mock.patch.object(image_cache, "prefetch_async", new_callable=mock.AsyncMock, return_value=None),
        ):
            return await offers_schedule.run_all("scheduled", **kwargs)

    async def test_the_daily_run_marks_a_delivered_order_as_delivered(self) -> None:
        await auth.set_secret("AH_REFRESH_TOKEN", "token")
        item: dict = order_item(579310, "AH Sweet Palermo rode puntpaprika", 3.49)
        await self.ah_order(913252041, "open", days(-7), item)
        delivered: dict = {"id": 913252041, "deliveryDate": days(-7), "deliveryStatus": "DELIVERED"}
        with (
            mock.patch.object(ah, "get_open_fulfillments", new_callable=mock.AsyncMock, return_value=[]),
            mock.patch.object(ah_orders, "_closed_orders", new_callable=mock.AsyncMock, return_value=[delivered]),
            mock.patch.object(ah_orders, "_details_or_none", new_callable=mock.AsyncMock, return_value={"items": [item]}),
            mock.patch.object(ah_orders, "sync_receipts", new_callable=mock.AsyncMock, return_value={"new": 0}),
        ):
            result: dict = await self.run_daily()
        self.assertNotIn("error", result["purchases"]["ah"]["orders"])
        self.assertEqual(result["purchases"]["ah"]["receipts"], {"new": 0})
        bought: dict[str, list[str]] = {
            product["productId"]: [purchase["date"] for purchase in product["purchases"]]
            for product in (await prices.history("ah", "", 50, today=TODAY))["products"]
        }
        self.assertEqual(bought, {"579310": [days(-7)]}, "geleverd, dus een aankoop")

    async def test_the_button_does_not_wait_for_the_stores(self) -> None:
        await auth.set_secret("AH_REFRESH_TOKEN", "token")
        with mock.patch.object(ah_orders, "sync_orders", new_callable=mock.AsyncMock) as orders:
            result: dict = await self.run_daily(wait_for_images=False)
        self.assertEqual((orders.call_count, "purchases" in result), (0, False))


class BestOffersTest(PricesCase):
    """Meldingen over de volgorde van de aanbiedingen: de beste bovenaan."""

    async def test_2026_11_09_the_best_offer_is_the_lowest_price_of_the_year(self):
        # "daarnaast wil ik de beste aanbiedingen bovenaan, waarbij beste de laagste prijs van het afgelopen jaar is" (4.2.0). De
        # laagste prijs komt uit alles wat de app bewaart, ook het folderarchief, niet alleen uit wat de app zelf een jaar lang mat.
        with closing(db(prices)) as conn, conn:
            for start in (-370, -200):  # een folderweek van ruim een jaar terug geeft de dekking, een van dit jaar de laagste prijs
                conn.execute(
                    "INSERT INTO offer_history (store, product_id, title, brand, price, price_before, promo_label, valid_from, valid_until, first_seen, last_seen, ended_at) "
                    "VALUES ('ah', 'folder:a', 'Folder: Alle Dr. Oetker Casa di Mama', 'Folderarchief', 2.15, 4.29, '1+1 gratis', ?, ?, ?, ?, ?)",
                    (
                        days(start),
                        days(start + 6),
                        f"{days(start)}T00:00:00+00:00",
                        f"{days(start + 6)}T23:59:59+00:00",
                        f"{days(start + 6)}T23:59:59+00:00",
                    ),
                )
        await self.sync_offer(
            f"{TODAY}T05:00:00+00:00",
            offer(
                "514010",
                "Dr. Oetker Casa di mama pizza salami",
                2.15,
                4.29,
                "nu 2,15",
                unitSize="390 g",
                brand="Dr. Oetker",
                validFrom=TODAY,
                validUntil=days(6),
            ),
            offer(
                "233387",
                "Dr. Oetker Casa di mama pizza salame extra piccante",
                3.65,
                4.29,
                "nu 3,65",
                unitSize="415 g",
                brand="Dr. Oetker",
                validFrom=TODAY,
                validUntil=days(6),
            ),
        )
        client = self.client_for(create_app())
        response = await client.get("/api/offers?store=ah")
        self.assertEqual(response.status_code, 200)
        by_id = {item["id"]: item for item in response.get_json()["offers"]}
        self.assertEqual((by_id["514010"]["yearLow"]["status"], by_id["514010"]["yearLow"]["price"]), ("lowest", 2.15))
        self.assertEqual((by_id["233387"]["yearLow"]["status"], by_id["233387"]["yearLow"]["above"]), ("above", 1.5))


class JsonBodyTest(AppTestCase):
    """Melding van 2026-11-07: `/api/backup/jobs` en `/api/price-archive/jobs` gaven een 500 op een JSON-body die geen object is.
    Elke POST-route die een JSON-body leest, krijgt hier `"x"` en geeft dan een 400 (request_body.json_object), of de status uit
    de uitzonderingslijst voor routes die de body anders lezen. Een nieuwe route zonder vermelding laat de test falen."""

    # Routes die de body niet als JSON-object lezen, met de status die ze op `"x"` geven: de uploadroutes controleren eerst het
    # token in het pad (404 voor een onbekend token), de OAuth-routes van de MCP-server lezen een formulier of JSON (404 zolang
    # OAuth niet is ingesteld, en `/oauth/revoke` geeft altijd 200, zoals RFC 7009 vraagt).
    BODY_EXEMPT: dict[str, int] = {
        "/api/backup/uploads/<token>": 404,
        "/api/backup/uploads/<token>/stage": 404,
        "/api/price-archive/uploads/<token>": 404,
        "/api/price-archive/uploads/<token>/stage": 404,
        "/api/auth/logout": 200,  # leest alleen de cookie (om de sessie in te trekken), geen body
        "/api/price-archive/export": 200,  # leest alleen queryparameters
        "/api/picnic/explore": 200,  # leest alleen queryparameters
        "/mcp": 404,  # JSON-RPC, en zonder OAuth-instelling niet beschikbaar
        "/oauth/authorize": 404,
        "/oauth/register": 404,
        "/oauth/token": 404,
        "/oauth/revoke": 200,
    }
    # Routes die de body niet lezen maar wel naar buiten gaan (de AH-loginproxy stuurt alles door, het prijsarchief haalt een site op):
    # in de tests stopt de netwerkwacht die met een fout, dus die slaan we over.
    BODY_SKIPPED: set[str] = {"/api/ah/auth/proxy", "/api/ah/auth/proxy/<path:proxy_path>", "/api/price-archive/fetch"}

    async def test_2026_11_07_a_non_object_json_body_never_gives_a_500(self):
        # Zonder inloggen (zoals een installatie zonder wachtwoord of passkey), anders stopt elke route al op een 401.
        patcher = mock.patch.dict(os.environ, {"PASSKEY_AUTH_ENABLED": "false"})
        patcher.start()
        self.addCleanup(patcher.stop)
        app = create_app()
        client = self.client_for(app)
        wrong: list[str] = []
        for rule in sorted(app.url_map.iter_rules(), key=lambda r: r.rule):
            methods = (rule.methods or set()) & {"POST", "PUT", "PATCH"}
            if not methods or rule.endpoint == "static" or rule.rule in self.BODY_SKIPPED:
                continue
            source = inspect.getsource(app.view_functions[rule.endpoint])
            if not re.search(r"get_json|json_object|form_or_json|get_data|request\.", source) and "body" not in source:
                continue  # leest de aanvraag niet
            path = re.sub(r"<[^>]+>", "1", rule.rule)
            for method in sorted(methods):
                response = await client.open(path, method=method, data='"x"', headers={"Content-Type": "application/json"})
                expected = self.BODY_EXEMPT.get(rule.rule, 400)
                if response.status_code != expected:
                    wrong.append(f"{method} {rule.rule} -> {response.status_code} (verwacht {expected})")
        self.assertEqual(wrong, [], "\n".join(wrong))


class SessionLifetimeTest(AuthPasswordTestCase):
    """Melding van 2026-11-09: "het systeem logt me regelmatig uit, zorg ervoor dat ik langdurig ingelogd kan blijven". De sessie
    (de cookie `ms_session`) liep na 24 uur af, ongeacht gebruik. Nu duurt hij 90 dagen (`MEALIESHOPPER_SESSION_DAYS`) en zet
    elke aanvraag met een sessie van meer dan een dag oud een verse cookie, zodat wie de app gebruikt ingelogd blijft."""

    NINETY_DAYS = 90 * 24 * 60 * 60

    def session_token(self, response):
        cookies = " ".join(response.headers.getlist("Set-Cookie"))
        found = re.search(r"ms_session=([^;]+)", cookies)
        return (found.group(1) if found else None), cookies

    async def test_2026_11_09_a_session_lasts_ninety_days_and_is_renewed_on_use(self):
        token, cookie = self.session_token(await self.setup_password())
        self.assertIn(f"Max-Age={self.NINETY_DAYS}", cookie)
        claims = jwt.decode(token, options={"verify_signature": False})
        self.assertEqual(claims["exp"] - claims["iat"], self.NINETY_DAYS)
        # Vandaag ingelogd: een gewone aanvraag zet geen nieuwe cookie.
        fresh = await self.client.get("/api/auth/status")
        self.assertTrue(fresh.get_json()["authenticated"])
        self.assertEqual(self.session_token(fresh), (None, ""))
        # Een sessie van twee dagen oud: de aanvraag krijgt een verse cookie met opnieuw 90 dagen, voor dezelfde gebruiker.
        old = jwt.encode(
            {**claims, "iat": claims["iat"] - 2 * 86400, "exp": claims["exp"] - 2 * 86400}, await auth.signing_secret(), algorithm="HS256"
        )
        client = self.new_client()
        renewed = await client.get("/api/auth/status", headers={"Cookie": f"ms_session={old}"})
        self.assertTrue(renewed.get_json()["authenticated"])
        new_token, new_cookie = self.session_token(renewed)
        self.assertIn(f"Max-Age={self.NINETY_DAYS}", new_cookie)
        self.assertIn("HttpOnly", new_cookie)
        new_claims = jwt.decode(new_token, options={"verify_signature": False})
        self.assertEqual((new_claims["sub"], new_claims["ep"]), (claims["sub"], claims["ep"]))
        self.assertGreaterEqual(new_claims["iat"], claims["iat"])
        # Een Bearer-token (AI-apps) krijgt geen cookie, en uitloggen wist de cookie zonder er een nieuwe overheen te zetten.
        bearer = await client.get("/api/auth/status", headers={"Authorization": f"Bearer {old}"})
        self.assertEqual(self.session_token(bearer), (None, ""))
        out = await client.post("/api/auth/logout", headers={"Cookie": f"ms_session={old}"})
        self.assertEqual(out.status_code, 200)
        cookies = out.headers.getlist("Set-Cookie")
        self.assertEqual(len(cookies), 1, cookies)
        self.assertIn("ms_session=;", cookies[0])

    async def test_2026_11_09_logout_revokes_the_session_and_renewal_keeps_what_was_checked(self):
        # Uit de beveiligingsreview van de fix: de verlenging neemt de sessieteller uit de gecontroleerde cookie mee (niet de
        # nieuwste uit de database), houdt dezelfde sessie-id en inlogtijd, stopt een jaar na de inlog, en uitloggen trekt de
        # sessie serverzijde in, ook de verlengde cookies van dezelfde sessie.
        token, _ = self.session_token(await self.setup_password())
        claims = jwt.decode(token, options={"verify_signature": False})
        self.assertEqual(claims["auth_time"], claims["iat"])
        self.assertEqual(len(claims["sid"]), 32)
        secret = await auth.signing_secret()
        old = jwt.encode({**claims, "iat": claims["iat"] - 2 * 86400, "exp": claims["exp"] - 2 * 86400}, secret, algorithm="HS256")
        client = self.new_client()
        with mock.patch.object(auth, "session_epoch", mock.AsyncMock(return_value=99)):
            renewed = await client.get("/api/auth/status", headers={"Cookie": f"ms_session={old}"})
        new_token, _ = self.session_token(renewed)
        new_claims = jwt.decode(new_token, options={"verify_signature": False})
        self.assertEqual((new_claims["ep"], new_claims["sid"], new_claims["auth_time"]), (claims["ep"], claims["sid"], claims["auth_time"]))
        # Een jaar na de inlog: de sessie geldt nog tot hij verloopt, maar wordt niet meer verlengd.
        aged_token = jwt.encode(
            {**claims, "iat": claims["iat"] - 2 * 86400, "exp": claims["exp"] - 2 * 86400, "auth_time": claims["iat"] - 400 * 86400},
            secret,
            algorithm="HS256",
        )
        aged = await client.get("/api/auth/status", headers={"Cookie": f"ms_session={aged_token}"})
        self.assertTrue(aged.get_json()["authenticated"])
        self.assertEqual(self.session_token(aged), (None, ""))
        # Uitloggen met de oude cookie: daarna zijn de oude en de verlengde cookie allebei ongeldig, een andere sessie niet.
        other_token, _ = self.session_token(
            await self.post("/api/auth/password/login", {"username": "admin", "password": PASSWORD}, self.new_client())
        )
        self.assertEqual((await client.post("/api/auth/logout", headers={"Cookie": f"ms_session={old}"})).status_code, 200)
        for name, cookie in (("oud", old), ("verlengd", new_token)):
            with self.subTest(name):
                self.assertEqual((await client.get("/api/mealplan?start=a&end=b", headers={"Cookie": f"ms_session={cookie}"})).status_code, 401)
                self.assertFalse((await client.get("/api/auth/status", headers={"Cookie": f"ms_session={cookie}"})).get_json()["authenticated"])
        self.assertTrue((await client.get("/api/auth/status", headers={"Cookie": f"ms_session={other_token}"})).get_json()["authenticated"])


class RecipeImportUrlTest(AppTestCase):
    """Melding van 2026-11-06: `POST /api/mealie/import` haalde elke opgegeven URL op, ook een adres op het eigen netwerk. Nu
    dezelfde hostnaamcontrole als de MCP-tool (urls.public_http_url)."""

    async def post(self, body: str):
        patcher = mock.patch.dict(os.environ, {"PASSKEY_AUTH_ENABLED": "false"})
        patcher.start()
        self.addCleanup(patcher.stop)
        client = self.client_for(create_app())
        return await client.post("/api/mealie/import", data=body, headers={"Content-Type": "application/json"})

    async def test_2026_11_06_importing_from_a_private_address_is_refused(self):
        from mealieshopper import mealie

        with mock.patch.object(mealie, "import_from_url", new_callable=mock.AsyncMock) as imp:
            response = await self.post('{"url": "http://mealie:9000/x"}')
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.get_json()["error"], "Alleen een openbaar http(s)-adres kan worden geimporteerd.")
            self.assertEqual((await self.post('{"url": 5}')).status_code, 400)
            self.assertEqual((await self.post('{"url": "ftp://ah.nl/x"}')).status_code, 400)
        imp.assert_not_called()


@unittest.skipUnless(shutil.which("node"), "Node is niet geinstalleerd")
class OffersFiltersTest(unittest.TestCase):
    """Meldingen over de filters op de aanbiedingenpagina."""

    def test_2026_11_09_filters_stack_and_count(self):
        # "ik wil veel meer filters hier: /offers" (4.2.0): merk Dr. Oetker plus minstens 15 procent laat van drie aanbiedingen precies
        # de twee pizza's door, en de actieve chips noemen beide filters.
        offers = [
            {"id": "1", "brand": "Dr. Oetker", "title": "Pizza salami", "badge": {"percent": 15}, "family": {"key": "a", "packs": []}},
            {"id": "2", "brand": "Dr. Oetker", "title": "Pizza hawaii", "badge": {"percent": 20}, "family": {"key": "b", "packs": []}},
            {"id": "3", "brand": "Unox", "title": "Rookworst", "badge": {"percent": 50}, "family": {"key": "c", "packs": []}},
            {"id": "4", "brand": "Dr. Oetker", "title": "Brownies", "badge": {"percent": 10}, "family": {"key": "d", "packs": []}},
        ]
        filters = {
            "brands": ["Dr. Oetker"],
            "brandsOut": [],
            "categories": [],
            "categoriesOut": [],
            "bought": False,
            "minDiscount": 15,
            "maxUnitPrice": {"value": None, "unit": "kg"},
            "yearLow": False,
            "rare": False,
        }
        script = f"""{test_offers_scan_js.PRELUDE}
offersState.filters = {json.dumps(filters)};
const shown = offersFilteredWithFamilies({json.dumps(offers)});
const through = {json.dumps(offers)}.filter((o) => offersFilterMatch(o, offersState.filters)).length;
console.log(JSON.stringify({{ ids: shown.map((o) => o.id), chips: offersActiveFilterChips().map((c) => c.key), count: `${{through}} van ${{{len(offers)}}}` }}));"""
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"ids": ["1", "2"], "chips": ["brand:Dr. Oetker", "minDiscount"], "count": "2 van 4"})

    def test_2026_11_09_hover_shows_the_full_history_and_purchases(self):
        # "met een hover wil ik direct de volledige prijshistorie en aankopen zien in een popup": de popup bij een aanbieding
        # bevat de kerncijfers, de grafiek en de aankopen, en de link naar de volledige pagina.
        offer = {"id": "514010", "title": "Dr. Oetker Casa di mama pizza salami", "brand": "Dr. Oetker", "unitSize": "390 g", "price": 3.65}
        product = {
            "store": "ah",
            "productId": "514010",
            "title": offer["title"],
            "purchases": [{"date": "2026-09-01", "price": 2.15}],
            "episodes": [],
        }
        script = f"""{test_offers_scan_js.PRELUDE}
console.log(JSON.stringify(offerPopupHtml({json.dumps(offer)}, {json.dumps(product)}, [])));"""
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        html = json.loads(result.stdout)
        for marker in ('class="ohc-stats"', 'class="ohc-block"', 'class="ohc-table"', "/offers/history?product=514010"):
            self.assertIn(marker, html)


@unittest.skipUnless(shutil.which("node"), "Node is not installed")
class OfferCardLinksTest(unittest.TestCase):
    """Verzoek van 2026-11-09: "ik wil overigens dat klikken naar de prijsgeschiedenis pagina gaat daar. maak een winkel (e.g. AH)
    icoontje waarmee je wel direct naar de winkel gaat". De foto en de titel van een aanbieding gingen naar de winkel."""

    STUBS: str = """
const mdiIcon = (path) => '<svg data-icon="' + path + '"></svg>';
const MDI_IMAGE_OUTLINE = 'image'; const MDI_COMPARE_HORIZONTAL = 'compare'; const MDI_CHART_LINE = 'chart'; const MDI_STORE = 'store';
const storeImage = (url) => url; const storeDisplayName = () => 'Albert Heijn'; const offersOnlyStore = () => false;
const offerPriceLabelHtml = () => '';
"""

    def render(self, offer: dict) -> dict:
        names = ("offerThumb", "offerCardFoot", "offerTitleLink", "offerStoreLink")
        script = f"""{test_offers_scan_js.PRELUDE}
{chr(10).join(test_offers_scan_js.function_source(name) for name in names)}
{self.STUBS}
const offer = {json.dumps(offer)};
console.log(JSON.stringify({{ thumb: offerThumb(offer), title: offerTitleLink(offer), foot: offerCardFoot(offer), store: offerStoreLink(offer) }}));"""
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_2026_11_09_photo_and_title_open_the_price_history_and_an_icon_opens_the_store(self):
        html = self.render(
            {"id": "234871", "title": "Mutti Pizzasaus aromatica", "image": "i.png", "url": "https://www.ah.nl/producten/product/wi234871"}
        )
        for part in ("thumb", "title"):
            self.assertIn('href="/offers/history?product=234871" data-route', html[part], part)
            self.assertNotIn("ah.nl", html[part], part)
            self.assertNotIn("_blank", html[part], part)
        store = html["store"]
        self.assertIn('href="https://www.ah.nl/producten/product/wi234871" target="_blank" rel="noopener noreferrer"', store)
        self.assertIn('aria-label="offers.history.open {"store":"Albert Heijn"}"', store, "het icoon heeft een naam voor een schermlezer")
        self.assertIn('data-icon="store"', store)
        self.assertIn(store, html["foot"], "het winkelicoon staat onderaan de kaart")
        row = test_offers_scan_js.function_source("offerProductRow")
        self.assertIn("offerTitleLink(offer)", row)
        self.assertIn("offerStoreLink(offer)", row, "ook in de compacte rij")
        self.assertIn("offerTitleLink(offer)", test_offers_scan_js.function_source("offerProductCard"))

    def test_2026_11_09_without_a_store_address_there_is_no_store_icon(self):
        html = self.render({"id": "folder:1", "title": "Alle Mutti", "image": "", "url": ""})
        self.assertEqual(html["store"], "")
        self.assertIn('href="/offers/history?product=folder:1" data-route', html["title"])


@unittest.skipUnless(shutil.which("node"), "Node is not installed")
class OfferPopupLegendTest(unittest.TestCase):
    """Melding van 2026-11-10: "in de popup mist de legenda van wat de dingen in de grafiek inhouden". De pagina Prijsgeschiedenis
    had de legenda onder de grafiek, de popup bij een aanbieding niet."""

    def test_2026_11_10_the_popup_has_the_legend_under_the_chart(self):
        offer = {"id": "234871", "title": "Mutti Pizzasaus aromatica", "price": 2.49}
        product = {
            "store": "ah",
            "productId": "234871",
            "title": offer["title"],
            "purchases": [{"date": "2026-03-07", "price": 1.87}],
            "episodes": [],
        }
        script = f"""{test_offers_scan_js.PRELUDE}
console.log(JSON.stringify(offerPopupHtml({json.dumps(offer)}, {json.dumps(product)}, [])));"""
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        html = json.loads(result.stdout)
        self.assertIn('class="ohc-legend"', html)
        self.assertLess(html.index('class="ohc-block"'), html.index('class="ohc-legend"'), "onder de grafiek")
        self.assertLess(html.index('class="ohc-legend"'), html.index('class="ohc-table"'), "boven de aankopen")


class OfferPopupPassThroughTest(unittest.TestCase):
    """Melding van 2026-11-10: "met de huidige popup is het heel lastig om door te gaan naar het volgende product omdat de popup daar
    overheen gaat". Gekozen: de muis gaat door de popup heen, zodat de kaart eronder reageert en de popup daarheen verspringt."""

    @unittest.skipUnless(shutil.which("node"), "Node is not installed")
    def test_2026_11_10_the_popup_has_no_buttons_or_links_and_the_dialog_keeps_them(self):
        # "haal die knoppen/links weg, die hebben geen nut nu": de popup is alleen om naar te kijken. Het venster op een aanraakscherm
        # houdt de verpakkingsknoppen en de link naar de volledige prijsgeschiedenis.
        offer = {"id": "234871", "title": "Mutti Pizzasaus aromatica", "unitSize": "388 ml", "price": 2.49}
        product = {"store": "ah", "productId": "234871", "title": offer["title"], "url": "https://www.ah.nl/x", "purchases": [], "episodes": []}
        packs = [{"productId": "509883", "unitSize": "210 g"}, {"productId": "234871", "unitSize": "388 ml"}]
        script = f"""{test_offers_scan_js.PRELUDE}
const args = [{json.dumps(offer)}, {json.dumps(product)}, {json.dumps(packs)}, "234871"];
console.log(JSON.stringify({{ popup: offerPopupHtml(...args, {{ preview: true }}), dialog: offerPopupHtml(...args) }}));"""
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        html = json.loads(result.stdout)
        for marker in ("<a ", "<button", "data-popup-pack", "offer-popup__foot"):
            self.assertNotIn(marker, html["popup"], marker)
        self.assertIn('data-links="false"', html["popup"], "de kop zonder de link naar de winkel")
        self.assertIn('data-buttons="false"', html["popup"], "de grafiek zonder periodeknoppen")
        for marker in ("data-popup-pack", "offer-popup__foot", 'data-links="true"', 'data-buttons="true"'):
            self.assertIn(marker, html["dialog"], marker)
        source = (ROOT / "static" / "offers_history.js").read_text(encoding="utf-8")
        self.assertIn("const link = links && product.url", source)
        self.assertIn("const buttons = withButtons && range.options.length", source)

    @unittest.skipUnless(shutil.which("node"), "Node is not installed")
    def test_2026_11_10_the_popup_comes_fast_and_its_history_is_fetched_at_once(self):
        # "kunnen we de popup sneller maken? hij lijkt een vertraging te hebben": 400 ms wachten en dan pas de geschiedenis ophalen.
        # Nu haalt de app de geschiedenis op zodra de muis op de kaart komt, staat de popup er na 100 ms en verspringt hij in 40 ms.
        case = test_offers_scan_js.OffersScanNodeTest()
        found = case.popup_js(
            "let fetched = 0; const jsonFetch = () => { fetched += 1; return new Promise(() => {}); };"
            f"offersState.offers = [{json.dumps(case.POPUP_OFFER)}, {{ ...{json.dumps(case.POPUP_OFFER)}, id: '777' }}];"
            "const other = element('card2'); other.dataset.offer = '777'; const states = [];"
            "scheduleOfferPopup(anchor); states.push({ fetched, hidden: box.hidden });"
            "setTimeout(() => { states.push({ hidden: box.hidden, shown: offerPopup.id }); scheduleOfferPopup(other);"
            "  setTimeout(() => { states.push({ shown: offerPopup.id, fetched }); console.log(JSON.stringify(states)); }, OFFER_POPUP_SWITCH + 30);"
            "}, OFFER_POPUP_DELAY + 30);"
        )
        self.assertEqual(found, [{"fetched": 1, "hidden": True}, {"hidden": False, "shown": "514010"}, {"shown": "777", "fetched": 2}])
        js = (ROOT / "static" / "offers.js").read_text(encoding="utf-8")
        self.assertIn("const OFFER_POPUP_DELAY = 100;", js)
        self.assertIn("const OFFER_POPUP_SWITCH = 40;", js)

    def test_2026_11_10_the_mouse_goes_through_the_popup(self):
        css = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
        self.assertIn(".offer-popup {\n  pointer-events: none;\n}", css, "de kaart onder de popup blijft aan te wijzen")
        self.assertNotIn(".offer-popup:focus-within", css, "er valt in de popup niets meer te klikken")
        self.assertNotIn(
            "pointer-events", css[css.index(".offer-popup__dialog") :].split("}")[0], "het venster op een aanraakscherm blijft gewoon klikbaar"
        )


class OfferCartQuantityTest(test_ah_orders.AhOrdersTestCase):
    """Verzoek van 2026-11-10: "is er ook een mogelijkheid om het product direct aan het winkelwagentje toe te voegen en/of direct te
    zien wat er al in de winkelwagen zit? (i.e. aantallen invulveld met +/- erbij)". Het AH-mandje is de boodschappenlijst: het
    aantal in de PATCH vervangt wat er stond en 0 haalt de regel weg (gemeten bij AH op 2026-11-10)."""

    LIST: dict = {
        "id": "x",
        "items": [test_ah_orders.shopping_entry("Halfvolle melk", 2), {"type": "FREE_TEXT", "description": "Bloemen", "quantity": 1}],
    }

    async def test_2026_11_10_the_cart_gives_the_quantity_per_product(self):
        with mock.patch.object(ah, "_get_json", return_value=self.LIST):
            cart = (await self.client.get("/api/ah/cart")).get_json()
        self.assertEqual([(item["id"], item["quantity"], item["title"]) for item in cart["items"]], [("111", 2, "Halfvolle melk")])
        self.assertEqual(cart["totalCount"], 3, "de vrije tekstregel telt mee in het totaal")
        self.assertEqual((await self.client.get("/api/ah/basket")).get_json()["itemCount"], 2, "het tabblad Mandje loopt mee")

    async def test_2026_11_10_a_quantity_is_set_exactly_and_zero_removes(self):
        for quantity, answer, expected in (
            (3, {"items": [test_ah_orders.shopping_entry("Halfvolle melk", 3)]}, [("111", 3)]),
            (0, {"items": []}, []),
        ):
            with mock.patch.object(ah, "get_user_token", return_value="tok"), served(httpx.Response(200, json=answer)) as patch:
                response = await self.client.put("/api/ah/cart/items/111", json={"quantity": quantity, "title": "Halfvolle melk"})
            self.assertEqual(response.status_code, 200, response.get_json())
            self.assertEqual(patch.calls.last.request.method, "PATCH")
            sent = sent_json(patch.calls.last)["items"]
            self.assertEqual(
                [(line["productId"], line["quantity"], line["description"]) for line in sent], [(111, quantity, "Halfvolle melk")], "0 blijft 0"
            )
            self.assertEqual([(item["id"], item["quantity"]) for item in response.get_json()["items"]], expected)
            self.assertEqual((await self.client.get("/api/ah/basket")).get_json()["itemCount"], len(expected))

    async def test_2026_11_10_a_wrong_quantity_never_reaches_ah(self):
        with mock.patch.object(ah, "get_user_token", return_value="tok"), served(httpx.Response(200, json={})) as patch:
            for quantity in (-1, 100, "veel", 1.5, True, None):
                response = await self.client.put("/api/ah/cart/items/111", json={"quantity": quantity})
                self.assertEqual(response.status_code, 400, quantity)
            self.assertEqual((await self.client.put("/api/ah/cart/items/geen-getal", json={"quantity": 1})).status_code, 400)
        self.assertEqual(patch.calls.call_count, 0)

    async def test_2026_11_10_an_open_order_at_ah_gives_a_readable_error(self):
        refused = httpx.Response(412, text='{"status":412,"message":"Server in order mode"}')
        with mock.patch.object(ah, "get_user_token", return_value="tok"), served(refused):
            response = await self.client.put("/api/ah/cart/items/111", json={"quantity": 1})
        self.assertEqual(response.status_code, 424)
        self.assertIn("order", response.get_json()["error"].lower())


@unittest.skipUnless(shutil.which("node"), "Node is not installed")
class OfferLabelsSayItOnceTest(unittest.TestCase):
    """Melding van 2026-11-10: "deze tags lijken redundant?" bij een kaart met "Laagste in 12 mnd" en "Laagste prijs in een jaar"."""

    def labels(self, offer: dict) -> list:
        script = f"""{test_offers_scan_js.PRELUDE}
const offer = {json.dumps(offer)};
console.log(JSON.stringify([offerPriceLabel(offer), offerYearLowLine(offer)].map((label) => label && label.text)));"""
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_2026_11_10_a_card_names_the_lowest_price_of_the_year_once(self):
        lowest: dict = {"status": "lowest", "month": "2026-09", "above": 0.0}
        above: dict = {"status": "above", "month": "2026-09", "above": 0.5, "price": 1.79}
        for kind in ("lowest_12m", "lowest_6m"):
            self.assertEqual(self.labels({"priceLabel": {"kind": kind}, "yearLow": lowest}), [None, "offers.label.year_low"], kind)
            found = self.labels({"priceLabel": {"kind": kind}, "yearLow": above})
            self.assertIsNone(found[0], "geen groen label naast 'boven de laagste van het jaar'")
            self.assertIn("offers.label.year_above", found[1])
            # Zonder een jaar aan prijzen uit alle bronnen blijft het label uit de geschiedenis het enige oordeel.
            self.assertEqual(self.labels({"priceLabel": {"kind": kind}, "yearLow": {"status": "unknown"}}), [f"offers.label.{kind}", None])
        self.assertEqual(self.labels({"priceLabel": {"kind": "lowest_ever"}, "yearLow": lowest}), ["offers.label.lowest_ever", None])
        self.assertIsNone(self.labels({"priceLabel": {"kind": "lowest_ever"}, "yearLow": above})[0])


@unittest.skipUnless(shutil.which("node"), "Node is not installed")
class OfferFilterListsTest(unittest.TestCase):
    """Verzoek van 2026-11-10: "de filters moeten veel beter dan ze nu zijn. voor merken wil ik een lijst met alle merken en een
    zoekveld erboven. merk filters moeten +/- hebben voor include/exclude. daarnaast wil ik een infinite scroll", met erbij: "ik wil
    de categorieen net als de merken ook links in het filtermenu" en "ik wil het aantal matchende producten er ook direct bij hebben
    in die lijst. daarop gesorteerd"."""

    OFFERS: list = [
        {"id": "1", "brand": "Mutti", "category": "sauzen_conserven", "title": "Pizzasaus"},
        {"id": "2", "brand": "Mutti", "category": "sauzen_conserven", "title": "Passata"},
        {"id": "3", "brand": "Lenor", "category": "huishouden", "title": "Geurbooster"},
        {"id": "4", "brand": "AH", "category": "vlees", "title": "Gehakt"},
        {"id": "5", "brand": "AH", "category": "vlees", "title": "Kipfilet"},
        {"id": "6", "brand": "AH", "category": "huishouden", "title": "Vaatwastabletten"},
    ]

    def run_js(self, body: str) -> dict:
        names = ("offersPersonalOnly", "offersBase", "offersCategoryCounts")
        facets = (ROOT / "static" / "offers_facets.js").read_text(encoding="utf-8")
        script = f"""{test_offers_scan_js.PRELUDE}
{chr(10).join(test_offers_scan_js.function_source(name) for name in names)}
const OFFERS_FILTERS_KEY = 'ms-offers-filters';
const saved = {{}};
const localStorage = {{ getItem: (key) => saved[key] || null, setItem: (key, value) => {{ saved[key] = value; }} }};
const categoryLabel = (id) => 'cat ' + id;
const offerSearchText = (offer) => foldText(offer.title + ' ' + offer.brand);
const mdiIcon = (path) => '<svg data-icon="' + path + '"></svg>'; const MDI_PLUS = 'plus'; const MDI_MINUS = 'minus';
const $ = () => ({{ addEventListener: () => {{}} }});
{test_order_views.FACETS}
{facets}
Object.assign(offersState, {{ offers: {json.dumps(self.OFFERS)}, search: '', goodOnly: false, personalOnly: false, personal: null }});
const fresh = (extra) => {{ offersState.filters = {{ ...offersEmptyFilters(), ...extra }}; return offersState.filters; }};
const ids = () => offersFilteredWithFamilies(offersBase()).map((offer) => offer.id);
{body}"""
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_2026_11_10_plus_shows_only_those_and_minus_always_hides(self):
        found = self.run_js("""
const out = {};
fresh({ brands: ['Mutti', 'Lenor'] }); out.plus = ids();
fresh({ brandsOut: ['AH'] }); out.minus = ids();
fresh({ brands: ['AH'], categoriesOut: ['huishouden'] }); out.both = ids();
fresh({ categories: ['vlees', 'huishouden'], brandsOut: ['Lenor'] }); out.categories = ids();
fresh({ brands: ['Lenor'], brandsOut: ['Lenor'] }); out.minusWins = ids();
out.chips = (fresh({ brands: ['Mutti'], brandsOut: ['Lenor'], categories: ['vlees'], categoriesOut: ['huishouden'] }), offersActiveFilterChips().map((chip) => [chip.key, chip.label]));
console.log(JSON.stringify(out));
""")
        self.assertEqual(found["plus"], ["1", "2", "3"], "meerdere plussen: een van deze")
        self.assertEqual(found["minus"], ["1", "2", "3"])
        self.assertEqual(found["both"], ["4", "5"])
        self.assertEqual(found["categories"], ["4", "5", "6"])
        self.assertEqual(found["minusWins"], [])
        self.assertEqual(
            found["chips"],
            [
                ["cat:vlees", "cat vlees"],
                ["catOut:huishouden", 'offers.filters.excluded {"name":"cat huishouden"}'],
                ["brand:Mutti", "Mutti"],
                ["brandOut:Lenor", 'offers.filters.excluded {"name":"Lenor"}'],
            ],
        )

    def test_2026_11_10_the_lists_show_the_count_sorted_by_it_with_the_chosen_ones_on_top(self):
        found = self.run_js("""
const rows = (facet, search) => offerFacetRows(facet, facet === 'brand' ? offersBrandCounts(offersBase()) : offersCategoryCounts(offersBase()), offersFilters(), search).map((row) => [row.value, row.count, row.mode]);
const out = {};
fresh({}); out.brands = rows('brand'); out.categories = rows('category');
fresh({ brandsOut: ['Lenor'], brands: ['Verdwenen'] }); out.active = rows('brand');
fresh({ categories: ['vlees'] }); out.withCategory = rows('brand'); out.categoryKeepsItsOwnCounts = rows('category');
fresh({}); out.search = rows('brand', 'mut'); out.none = rows('brand', 'zzz');
offersState.search = 'gehakt'; out.pageSearch = rows('brand'); offersState.search = '';
out.html = offerFacetRowHtml('category', { value: 'vlees', label: 'Vlees', count: 2, mode: 'out' });
console.log(JSON.stringify(out));
""")
        self.assertEqual(found["brands"], [["AH", 3, ""], ["Mutti", 2, ""], ["Lenor", 1, ""]], "op aantal, meeste eerst")
        self.assertEqual(found["categories"], [["huishouden", 2, ""], ["sauzen_conserven", 2, ""], ["vlees", 2, ""]])
        self.assertEqual(
            found["active"], [["Lenor", 1, "out"], ["Verdwenen", 0, "in"], ["AH", 3, ""], ["Mutti", 2, ""]], "gekozen bovenaan, ook met 0"
        )
        self.assertEqual(found["withCategory"], [["AH", 2, ""]], "het aantal is wat er met de andere filters over is")
        self.assertEqual(found["categoryKeepsItsOwnCounts"], [["vlees", 2, "in"], ["huishouden", 2, ""], ["sauzen_conserven", 2, ""]])
        self.assertEqual(found["search"], [["Mutti", 2, ""]], "het zoekveld boven de merken")
        self.assertEqual(found["none"], [])
        self.assertEqual(found["pageSearch"], [["AH", 1, ""]], "het zoekveld van de pagina telt mee")
        for marker in (
            'data-facet="category"',
            'data-value="vlees"',
            'data-mode="in" aria-pressed="false"',
            'data-mode="out" aria-pressed="true"',
            "is-out",
            "offers.filters.include",
            "offers.filters.exclude",
            ">2</span>",
        ):
            self.assertIn(marker, found["html"], marker)

    def test_2026_11_10_a_second_click_clears_and_saved_brand_filters_become_plus(self):
        found = self.run_js("""
const filters = fresh({});
const steps = [];
const state = () => [filters.brands.slice(), filters.brandsOut.slice()];
offerFacetToggle(filters, 'brand', 'Mutti', 'in'); steps.push(state());
offerFacetToggle(filters, 'brand', 'Mutti', 'out'); steps.push(state());
offerFacetToggle(filters, 'brand', 'Mutti', 'out'); steps.push(state());
saved['ms-offers-filters:ah'] = JSON.stringify({ brands: ['Unox'], minDiscount: 20 });
const old = offersLoadFilters('ah');
console.log(JSON.stringify({ steps, old: [old.brands, old.brandsOut, old.categories, old.categoriesOut, old.minDiscount] }));
""")
        self.assertEqual(found["steps"], [[["Mutti"], []], [[], ["Mutti"]], [[], []]])
        self.assertEqual(found["old"], [["Unox"], [], [], [], 20])

    def test_2026_11_10_the_list_loads_further_by_itself_and_only_adds_the_new_rows(self):
        names = ("renderOfferGrid", "offersShowMore", "offersAutoMore")
        script = f"""{test_offers_scan_js.PRELUDE}
{chr(10).join(test_offers_scan_js.function_source(name) for name in names)}
const OFFER_ROWS = 3; const OFFER_COMPACT_ROWS = 8; const OFFERS_MORE_AHEAD = 800;
const all = Array.from({{ length: 60 }}, (_, index) => ({{ id: String(index) }}));
const offersFiltered = () => all;
const offerProductRow = (offer) => '<r' + offer.id + '>'; const offerRowHead = () => '<head>'; const offerColumns = () => 4;
const writes = [];
const grid = {{ classList: {{ toggle: () => {{}} }}, set innerHTML(html) {{ writes.push(['all', (html.match(/<r/g) || []).length]); }}, insertAdjacentHTML: (where, html) => writes.push(['add', (html.match(/<r/g) || []).length]) }};
let top = 100; let visible = true;
const button = {{ hidden: false, get offsetParent() {{ return visible ? {{}} : null; }}, getBoundingClientRect: () => ({{ top }}) }};
const $ = (selector) => (selector === '#offers-more' ? button : grid);
const window = {{ innerHeight: 900 }};
let watched = 0; const offersWatchMore = () => {{ watched += 1; }};
Object.assign(offersState, {{ view: 'compact', rows: OFFER_ROWS, shown: 0 }});
renderOfferGrid();
offersShowMore();
const afterOne = {{ writes: writes.slice(), shown: offersState.shown, hidden: button.hidden, watched }};
visible = false; offersAutoMore(); const closedPage = offersState.shown;
visible = true; top = 5000; offersAutoMore(); const farAway = offersState.shown;
top = 100; offersAutoMore();
console.log(JSON.stringify({{ afterOne, closedPage, farAway, end: {{ shown: offersState.shown, hidden: button.hidden, writes: writes.length }} }}));"""
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        found = json.loads(result.stdout)
        self.assertEqual(
            found["afterOne"],
            {"writes": [["all", 24], ["add", 24]], "shown": 48, "hidden": False, "watched": 1},
            "de tweede lading komt erbij, de eerste blijft staan",
        )
        self.assertEqual(found["closedPage"], 48, "een pagina die niet open staat laadt niets")
        self.assertEqual(found["farAway"], 48, "het einde is nog ver: niets")
        self.assertEqual(found["end"], {"shown": 60, "hidden": True, "writes": 3}, "bij het einde in de buurt tot de lijst op is")
        template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="offers-filter-brand-search"', template)
        self.assertIn('<ul class="offer-facet offer-facet--scroll" id="offers-filter-brands"></ul>', template)
        css = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
        self.assertIn("body:has(.panel.is-active #offers-items.is-active) .shell {\n    max-width: none;", css, "het overzicht is schermbreed")


# 2026-11-10: "kijk over het hele systeem breed. ik wil dezelfde filters overal inzetten waar nuttig". Gekozen: een bestelling,
# bestellingen doorzoeken, de prijsgeschiedenis en Aanbiedingen > Recepten. Overal dezelfde lijst (static/facets.js): per regel
# een plus (alleen deze), een min (deze niet, dat wint) en het aantal, op dat aantal gesorteerd met de gekozen regels bovenaan.
FACET_PANELS: tuple[str, ...] = ("order-facets", "purchase-facets", "offers-history-facets", "offers-course-facets")


class FiltersEverywhereTest(unittest.TestCase):
    def test_2026_11_10_every_page_has_the_panel_and_the_shared_list_loads_first(self):
        html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        for panel in FACET_PANELS:
            self.assertEqual(len(re.findall(rf'<details class="facet-panel" id="{panel}" data-facet-panel="\w+"', html)), 1, panel)
        scripts = re.findall(r"filename='([\w/]+\.js)'", html)
        for later in ("offers_facets.js", "orders.js", "order_views.js", "offers.js", "offers_history.js"):
            self.assertLess(scripts.index("facets.js"), scripts.index(later), later)

    @unittest.skipUnless(shutil.which("node"), "Node is not installed")
    def test_2026_11_10_an_order_filters_on_category_and_brand_and_counts_with_the_other_list(self):
        found = test_order_views.OrderViewsNodeTest.run_js(
            self,
            """
const lines = [line("vis", 1, 2, 0, { brand: "AH", title: "Zalm" }), line("vis", 1, 3, 0, { brand: "Fish Tales", title: "Tonijn" }),
  line("zuivel_eieren", 1, 1, 0, { brand: "AH", title: "Melk" }), line("zuivel_eieren", 1, 1, 0, { brand: "", title: "Eieren" })];
const sel = (shown = [], hidden = []) => ({ shown, hidden });
const titles = (facets) => orderFilter(lines, [], facets).map((item) => item.title);
const rows = orderFacetRows(lines, { category: sel(["vis"]), brand: sel() }, "");
console.log(JSON.stringify({
  plus: titles({ category: sel(["vis"]), brand: sel() }),
  minus: titles({ category: sel(), brand: sel([], ["AH"]) }),
  both: titles({ category: sel(["vis"]), brand: sel([], ["AH"]) }),
  search: orderFilter(lines, ["zalm"], { category: sel([], ["vis"]), brand: sel() }).length,
  categories: rows.category.map((row) => [row.value, row.count, row.mode]),
  brands: rows.brand.map((row) => [row.value, row.count]),
  found: orderFacetRows(lines, orderFacetsEmpty(), "fish").brand.map((row) => row.value),
}));
""",
        )
        self.assertEqual(found["plus"], ["Zalm", "Tonijn"])
        self.assertEqual(found["minus"], ["Tonijn", "Eieren"], "een regel zonder merk valt niet weg met een min op een ander merk")
        self.assertEqual((found["both"], found["search"]), (["Tonijn"], 0))
        self.assertEqual(found["categories"], [["vis", 2, "in"], ["zuivel_eieren", 2, ""]], "de eigen keuze telt niet mee in de eigen lijst")
        self.assertEqual(found["brands"], [["AH", 1], ["Fish Tales", 1]], "de merken tellen binnen de gekozen categorie")
        self.assertEqual(found["found"], ["Fish Tales"], "het zoekveld boven de merken")

    @unittest.skipUnless(shutil.which("node"), "Node is not installed")
    def test_2026_11_10_the_purchase_search_narrows_to_the_found_products(self):
        orders = test_order_views.ORDERS
        script = f"""{test_order_views.FACETS}
const ordersState = {{ query: "cola", products: {{ shown: [], hidden: ["Pepsi"] }}, hits: {{
  products: [{{ product: "Coca-Cola", times: 5 }}, {{ product: "Pepsi", times: 2 }}],
  occurrences: [{{ product: "Coca-Cola", id: "1", source: "bestelling" }}, {{ product: "Pepsi", id: "2", source: "bestelling" }},
    {{ product: "Coca-Cola", id: "3", source: "kassabon" }}] }} }};
{test_order_views.function_source("purchaseProductRows", orders)}
{test_order_views.function_source("purchaseHits", orders)}
const out = {{ rows: purchaseProductRows().map((row) => [row.value, row.count, row.mode]), minus: [...purchaseHits().keys()] }};
ordersState.products = {{ shown: ["Pepsi"], hidden: [] }};
out.plus = [...purchaseHits().keys()];
console.log(JSON.stringify(out));"""
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        found = json.loads(result.stdout)
        self.assertEqual(found["rows"], [["Pepsi", 2, "out"], ["Coca-Cola", 5, ""]], "wat gekozen is staat bovenaan, daarna op aantal")
        self.assertEqual((found["minus"], found["plus"]), (["order:1", "receipt:3"], ["order:2"]))


class FiltersEverywhereHistoryTest(PricesCase):
    PRODUCTS: tuple[tuple[str, str, str], ...] = (
        ("1", "Unox Rookworst", "Unox"),
        ("2", "Unox Tomatensoep", "Unox"),
        ("3", "AH Halfvolle melk", "AH"),
    )

    async def filtered(self, **chosen):
        return await prices.history("ah", "", 50, **chosen)

    async def test_2026_11_10_the_price_history_filters_and_counts_brands_and_categories(self):
        await prices.record(
            "ah", [{"id": pid, "title": title, "price": 2.0, "unitSize": "500 g", "brand": brand} for pid, title, brand in self.PRODUCTS]
        )
        category = {pid: offer_categories.offer_category("ah", title) for pid, title, _ in self.PRODUCTS}
        ids = lambda data: sorted(product["productId"] for product in data["products"])  # noqa: E731
        plain = await self.filtered()
        self.assertEqual((plain["total"], plain["facets"]["brands"]), (3, [{"value": "Unox", "count": 2}, {"value": "AH", "count": 1}]))
        self.assertEqual(sum(row["count"] for row in plain["facets"]["categories"]), 3)
        unox = await self.filtered(brands=["Unox"])
        self.assertEqual((ids(unox), unox["total"]), (["1", "2"], 2))
        self.assertEqual(unox["facets"]["brands"], plain["facets"]["brands"], "de eigen keuze telt niet mee in de eigen lijst")
        self.assertEqual(
            {row["value"] for row in unox["facets"]["categories"]}, {category["1"], category["2"]}, "de categorieen tellen binnen het merk"
        )
        self.assertEqual(ids(await self.filtered(brands_out=["Unox"])), ["3"])
        self.assertEqual(ids(await self.filtered(brands=["Unox"], brands_out=["Unox"])), [], "de min wint")
        self.assertEqual(ids(await self.filtered(categories=[category["3"]])), sorted(pid for pid in category if category[pid] == category["3"]))
        self.assertEqual(ids(await self.filtered(categories_out=[category["3"]])), sorted(pid for pid in category if category[pid] != category["3"]))
        pinned = await prices.history("ah", "", 50, pin="3", brands=["Unox"])
        self.assertEqual(ids(pinned), ["1", "2", "3"], "het product uit het adres blijft staan")
        self.assertEqual((await prices.history("ah", "", 50, product="3"))["facets"], {}, "de hover vraagt een product, zonder lijsten")
        client = self.client_for(create_app())
        reply = (await client.get("/api/prices/history?store=ah&brand=Unox&brand=AH&brand_out=AH")).get_json()
        self.assertEqual(ids(reply), ["1", "2"])

    def test_2026_11_10_the_brand_list_is_capped_but_keeps_what_is_chosen(self):
        values = [f"merk{index:03d}" for index in range(prices.FACET_BRANDS + 5)]
        chosen = prices._Choice.of([values[-1]], None)
        rows = prices._facet_counts(values, chosen, prices.FACET_BRANDS)
        self.assertEqual(len(rows), prices.FACET_BRANDS + 1)
        self.assertEqual(rows[-1], {"value": values[-1], "count": 1})


class FiltersEverywhereCoursesTest(test_offers.TempDataDirTest):
    index = test_offers.SuggestionsTest.index
    course_setup = test_offers.SuggestionsTest.course_setup

    def test_2026_11_10_recipe_suggestions_take_courses_to_show_and_to_hide(self):
        self.course_setup()
        ids = lambda **chosen: sorted(recipe["id"] for recipe in run(offers.suggest("ah", **chosen))["recipes"])  # noqa: E731
        self.assertEqual(ids(courses=["ontbijt", "overig"]), ["b", "c"])
        self.assertEqual(ids(courses_out=["ontbijt"]), ["a", "c"])
        self.assertEqual(ids(courses=["ontbijt"], courses_out=["ontbijt"]), [], "de min wint")
        data = run(offers.suggest("ah", courses_out=["ontbijt"]))
        self.assertEqual((data["total"], len(data["courses"])), (2, 3), "de aantallen per soort blijven die van alle voorstellen")
        with self.assertRaises(ValueError):
            run(offers.suggest("ah", courses=["pannenkoek"]))


class TwoNumbersOneProductTest(PricesCase):
    """2026-11-10: "dit kan niet kloppen: ik koop die soort brood regelmatig dus er zouden meer bestellingen moeten zijn. en het is
    met regelmaat in de aanbieding" (AH Les pains triomphe heel, 1 keer gekocht in de popup).

    AH voert het brood onder twee nummers: 230786 (waar de kassabon "LP TRIOMPHE" naar wijst) en 446408 (de webwinkel en de bonus).
    Zelfde naam, merk en verpakking: een product. En de folder noemt de lijn "Alle AH Bakkersbrood Les Pains heel", met een woord
    dat niet in de productnaam staat maar wel de categorie noemt.
    """

    NAME: str = "AH Les pains triomphe heel"

    async def asyncSetUp(self) -> None:
        for number, back in enumerate((40, 30, 20)):
            self.ah_receipt(f"R{number}", f"{days(-back)}T12:00:00", (794881, "LP TRIOMPHE", 1, 2.39, 2.39))
        with closing(db(ah_orders)) as conn, conn:
            conn.execute(
                "INSERT INTO ah_receipt_products (receipt_product_id, product_id, title, checked_at, brand, unit_size) "
                "VALUES (794881, 230786, ?, 'x', 'AH', 'per stuk')",
                (self.NAME,),
            )
        await self.ah_order(101, "delivered", days(-10), order_item(446408, self.NAME, 2.39, "per stuk"))

    async def test_2026_11_10_purchases_under_the_other_number_count_for_the_product(self) -> None:
        found: dict = await prices.history("ah", "triomphe", 50, today=TODAY)
        self.assertEqual([product["productId"] for product in found["products"]], ["446408"], "een rij, onder het nummer met de nieuwste gegevens")
        self.assertEqual((found["total"], found["products"][0]["timesBought"]), (1, 4))
        self.assertEqual([purchase["date"] for purchase in found["products"][0]["purchases"]], [days(-40), days(-30), days(-20), days(-10)])
        for asked in ("446408", "230786"):  # de popup en een link vragen een nummer: dat nummer krijgen ze, met alle aankopen
            product: dict = (await prices.history("ah", "", 5, today=TODAY, product=asked))["products"][0]
            self.assertEqual((product["productId"], product["timesBought"]), (asked, 4))
        self.assertEqual(await prices.bought_ids("ah", TODAY) & {"446408", "230786"}, {"446408", "230786"})

    async def test_2026_11_10_an_old_order_with_the_name_in_singular_is_the_same_bread(self) -> None:
        # "ook dan kan het niet kloppen. ik heb dit zowel in de winkel met een bon gekocht als online via een bestelling": de
        # bevestigingsmails van 2022 noemen het "AH Les pain triomphe heel", een keer met "online" erachter.
        with closing(db(price_import)) as conn, conn:
            conn.executemany(
                "INSERT INTO imported_purchases (store, order_ref, position, day, title, quantity, paid, regular, imported_at) "
                "VALUES ('ah', ?, 1, ?, ?, 1, 2.39, 2.39, 'x')",
                (
                    ("M1", "2022-05-31", "AH Les pain triomphe heel"),
                    ("M2", "2022-12-14", "AH Les pain triomphe heel online"),
                    ("M3", "2023-02-11", "AH Les pain triomphe blanc heel"),
                ),
            )
        found: dict = await prices.history("ah", "triomphe", 50, today=TODAY)
        bought: dict[str, int] = {product["productId"]: product["timesBought"] for product in found["products"]}
        self.assertEqual(bought, {"446408": 6, "import:ah les pain triomphe blanc heel": 1}, "het witte brood is een ander product")

    async def test_2026_11_10_another_pack_with_the_same_name_stays_its_own_product(self) -> None:
        await self.ah_order(102, "delivered", days(-5), order_item(999, self.NAME, 1.29, "400 g"))
        found: dict = await prices.history("ah", "triomphe", 50, today=TODAY)
        self.assertEqual(sorted(product["productId"] for product in found["products"]), ["446408", "999"])

    def test_2026_11_10_a_leaflet_word_naming_the_category_may_be_missing_from_the_name(self) -> None:
        index = line_offers.LineIndex(
            [
                ("folder:1", "Folder: Alle AH Bakkersbrood Les Pains heel"),
                ("folder:2", "Folder: Alle AH Bakkersbrood"),
                ("folder:3", "Folder: AH Les Pains heel"),
                ("folder:4", "Folder: AH Bakkersbrood Les Pains half"),
                ("folder:5", "Folder: Alle AH Bakkersbrood tijgerbrood heel"),
                ("folder:6", "Folder: AH Tijgerbrood heel"),
            ]
        )
        bread: frozenset[str] = line_offers.category_words("brood_bakkerij")
        self.assertEqual(bread, {"brood", "bakkerij"})
        self.assertEqual(index.lines_for(["ah les pains triomphe heel ah"], (), bread), ["folder:1", "folder:3"])
        self.assertEqual(index.lines_for(["ah les pains triomphe heel ah"]), ["folder:3"], "zonder categorie blijft de regel streng")
        self.assertEqual(index.lines_for(["ah les pains triomphe heel ah"], (), line_offers.category_words("kaas_vleeswaren")), ["folder:3"])
        self.assertEqual(line_offers.category_words("vis"), frozenset(), "een kort woord zegt te weinig")


class OfferPerWeightTest(PricesCase):
    # 2026-11-10: "daar klopt niets van, het is geen 90% korting". AH Greenfields Runder riblap, ca. 755 g, "per 100 GRAM voor
    # 1.59": AH geeft 1,59 (de prijs per 100 gram) door als prijs van het product, naast 15,49 voor het hele pak.
    LABEL = "per 100 GRAM voor \u20ac1.59"

    def item(self, size):
        return {
            "webshopId": 3994,
            "title": "AH Greenfields Runder riblap",
            "salesUnitSize": size,
            "currentPrice": 1.59,
            "priceBeforeBonus": 15.49,
            "discountLabels": [{"defaultDescription": self.LABEL}],
        }

    def test_2026_11_10_a_price_per_100_gram_becomes_the_price_of_the_pack(self):
        product = ah._bonus_product(self.item("ca. 755 g"))
        self.assertEqual((product["price"], product["priceBefore"]), (12.0, 15.49), "1,59 per 100 gram op 755 gram")
        loose = ah._bonus_product(self.item("per stuk"))
        self.assertEqual((loose["price"], loose["priceBefore"]), (1.59, None), "zonder gewicht geen gewone prijs van het pak ernaast")
        plain = ah._bonus_product({**self.item("ca. 755 g"), "discountLabels": [{"defaultDescription": "voor 1.59"}]})
        self.assertEqual((plain["price"], plain["priceBefore"]), (1.59, 15.49), "een gewone prijsverlaging blijft zoals AH hem noemt")
        self.assertEqual(deals.parse_mechanic("2 voor 5,00")["type"], deals.X_FOR_Y)
        self.assertEqual(deals.parse_mechanic("Per 100 gram")["type"], deals.UNKNOWN, "zonder bedrag is het geen prijs per gewicht")
        self.assertEqual(deals.measure_priced("per kilo voor 9,99", "500 g", 9.99), 5.0)
        self.assertIsNone(deals.measure_priced(self.LABEL, "ca. 755 g", 12.0), "een prijs die al van het pak is blijft staan")

    async def test_2026_11_10_rows_stored_with_the_price_per_100_gram_are_repaired(self):
        async with offers._connect() as conn:
            await conn.execute(
                "INSERT INTO offers (store, product_id, position, title, unit_size, price, price_before, promo_label) VALUES ('ah', '3994', 1, 'Riblap', 'ca. 755 g', 1.59, 15.49, ?)",
                (self.LABEL,),
            )
            for product_id, size, price in (("3994", "ca. 755 g", 1.59), ("4002", "per stuk", 1.59), ("3996", "ca. 700 g", 11.13)):
                await conn.execute(
                    "INSERT INTO offer_history (store, product_id, title, unit_size, price, price_before, promo_label, first_seen, last_seen) "
                    "VALUES ('ah', ?, 'Vlees', ?, ?, 15.49, ?, '2026-11-10T04:04:41+00:00', '2026-11-10T04:04:41+00:00')",
                    (product_id, size, price, self.LABEL),
                )
            await conn.commit()
            await offers._repair_measure_prices(conn)
            current = [row["price"] for row in await conn.execute_fetchall("SELECT price FROM offers")]
            history = {row["product_id"]: row["price"] for row in await conn.execute_fetchall("SELECT product_id, price FROM offer_history")}
        self.assertEqual(current, [12.0])
        self.assertEqual(history, {"3994": 12.0, "4002": 1.59, "3996": 11.13})


@unittest.skipUnless(shutil.which("node"), "Node is not installed")
class OfferCartStepperTest(unittest.TestCase):
    """Hetzelfde verzoek van 2026-11-10, het scherm: op de kaart het aantal dat al in het mandje zit, met min, een veld en plus."""

    HARNESS: str = """
const escapeHtml = (value) => String(value);
const t = (key, params) => key + (params ? ' ' + JSON.stringify(params) : '');
const mdiIcon = (path) => '<svg data-icon="' + path + '"></svg>'; const MDI_MINUS = 'minus'; const MDI_PLUS = 'plus';
const state = { linkedStores: { picnic: false, crisp: true } };
const document = { activeElement: null };
const makeBox = (id) => { const input = { value: '', classList: { contains: () => true } }; const classes = new Set();
  return { dataset: { cart: id }, hidden: true, input, classes, classList: { toggle: (name, on) => (on ? classes.add(name) : classes.delete(name)) }, querySelector: () => input }; };
const boxes = [makeBox('111'), makeBox('222')];
const messages = [];
const message = (target, text) => messages.push(text);
const $ = () => ({ querySelectorAll: () => boxes, addEventListener: () => {} });
const offerById = (id) => ({ id, title: 'Product ' + id });
const calls = []; let answers = [];
const jsonFetch = (url, options) => { calls.push({ url, method: (options && options.method) || 'GET', body: options && options.body ? JSON.parse(options.body) : null });
  const answer = answers.shift(); return answer instanceof Error ? Promise.reject(answer) : Promise.resolve(answer); };
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const shown = () => boxes.map((box) => [box.dataset.cart, box.hidden, box.input.value, box.classes.has('offer-cart--empty')]);
"""

    def run_js(self, body: str) -> dict:
        source = (ROOT / "static" / "offers_cart.js").read_text(encoding="utf-8")
        script = f"{self.HARNESS}\n{source}\n(async () => {{ {body} }})();"
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_2026_11_10_the_card_shows_what_is_in_the_basket_and_three_clicks_are_one_request(self):
        found = self.run_js("""
const before = offerCartControl({ id: '111', title: 'Melk' });
answers = [{ items: [{ id: 111, quantity: 2 }] }, { items: [{ id: '111', quantity: 2 }, { id: '222', quantity: 3 }] }];
await loadOfferCart('ah');
const loaded = shown();
const html = offerCartControl({ id: '111', title: 'Melk' });
setOfferCartQuantity('222', 1); setOfferCartQuantity('222', 2); setOfferCartQuantity('222', 3);
const optimistic = shown();
await wait(OFFER_CART_PAUSE + 80);
console.log(JSON.stringify({ before, loaded, html, optimistic, calls, after: shown(), folder: offerCartControl({ id: 'folder:9', title: 'Alle Mutti' }) }));
""")
        self.assertIn(" hidden", found["before"], "geen knoppen tot het mandje gelezen is")
        self.assertEqual(found["loaded"], [["111", False, "2", False], ["222", False, "0", True]])
        self.assertIn('value="2"', found["html"])
        self.assertNotIn(" hidden", found["html"])
        for marker in (
            'data-cart="111"',
            'data-cart-step="-1"',
            'data-cart-step="1"',
            'min="0"',
            "offers.cart.quantity",
            "list.qty_minus",
            "offers.cart.plus",
        ):
            self.assertIn(marker, found["html"], marker)
        self.assertEqual(found["optimistic"][1], ["222", False, "3", False], "het aantal staat er meteen")
        self.assertEqual(
            found["calls"],
            [
                {"url": "/api/ah/cart", "method": "GET", "body": None},
                {"url": "/api/ah/cart/items/222", "method": "PUT", "body": {"quantity": 3, "title": "Product 222"}},
            ],
            "drie keer plus is een verzoek met het exacte aantal",
        )
        self.assertEqual(found["after"], [["111", False, "2", False], ["222", False, "3", False]])
        self.assertEqual(found["folder"], "", "een folderactie voor een hele lijn is geen product")

    def test_2026_11_10_zero_removes_a_refusal_shows_the_error_and_an_unlinked_store_has_no_buttons(self):
        found = self.run_js("""
answers = [{ items: [{ id: '111', quantity: 2 }] }, new Error('Er staat een bestelling open'), { items: [{ id: '111', quantity: 2 }] }];
await loadOfferCart('ah');
setOfferCartQuantity('111', 0);
const emptied = shown()[0];
await wait(OFFER_CART_PAUSE + 80);
const reverted = shown()[0];
const sent = calls.map((call) => [call.method, call.url, call.body && call.body.quantity]);
setOfferCartQuantity('111', 250); const clamped = offerCartQuantity('111');
await loadOfferCart('picnic');
console.log(JSON.stringify({ emptied, reverted, sent, messages, clamped, picnic: shown(), available: ['ah', 'picnic', 'crisp', 'jumbo', 'lidl'].map(offerCartAvailable) }));
""")
        self.assertEqual(found["emptied"], ["111", False, "0", True], "op nul staat alleen de plus er nog")
        self.assertEqual(found["sent"], [["GET", "/api/ah/cart", None], ["PUT", "/api/ah/cart/items/111", 0], ["GET", "/api/ah/cart", None]])
        self.assertEqual(found["reverted"], ["111", False, "2", False], "na een weigering staat het aantal van de winkel er weer")
        self.assertEqual(found["messages"], ["Er staat een bestelling open"])
        self.assertEqual(found["clamped"], 99)
        self.assertEqual([box[1] for box in found["picnic"]], [True, True], "een winkel zonder koppeling: geen knoppen")
        self.assertEqual(found["available"], [True, False, True, False, False])


class PackFamiliesTest(PricesCase):
    """Melding van 2026-11-09: "rundergehakt van 300gr, 500gr of 1000gr hebben vaak losse prijzen waarbij de 300gr in de aanbieding
    is, dat maakt het nog niet goedkoper dan de bulk 1000gr verpakking"."""

    async def test_2026_11_09_packs_of_one_product_share_a_card_with_the_cheapest_per_unit(self):
        await prices.record(
            "ah",
            [{"id": "1000", "title": "AH Rundergehakt 1000 g", "price": 7.49, "unitSize": "1000 g", "brand": "AH"}],
            now="2026-11-08T08:00:00+00:00",
        )
        found = {
            item["id"]: item
            for item in await prices.annotate(
                "ah", [offer("300", "AH Rundergehakt 300 g", 3.65, 4.29, "nu 3,65", unitSize="300 g", brand="AH")], today="2026-11-09"
            )
        }
        family = found["300"]["family"]
        self.assertEqual([(p["productId"], p["cheapest"]) for p in family["packs"]], [("300", False), ("1000", True)])
        self.assertEqual(found["300"]["verdict"]["cheaperPack"]["productId"], "1000", "de 300 g in de aanbieding is per kilo duurder dan de 1000 g")


class ConditionalOfferPriceTest(PricesCase):
    """Melding van 2026-11-09 bij Mutti Pizzasaus aromatica (2e halve prijs): "ik zie 2x een lagere prijs afgelopen jaar ook al
    heb ik 'laagste prijs in een jaar' aangeklikt. er staat vandaag 2,49 euro in de grafiek". De kaart en het filter rekenden met
    wat een stuk bij de actie kost (1,87), de prijsgeschiedenis in de popup met de gewone prijs die AH bij zo'n actie als prijs
    noemt (2,49): als actieprijs van vandaag, als laagste actieprijs en als balk in de grafiek."""

    def mutti(self) -> dict:
        return {
            "id": "234871",
            "title": "Mutti Pizzasaus aromatica",
            "price": 2.49,
            "priceBefore": None,
            "promoLabel": "2e halve prijs",
            "unitSize": "388 ml",
            "brand": "Mutti",
        }

    async def test_2026_11_09_the_history_gives_what_a_piece_costs_with_the_offer(self):
        await self.sync_offer(f"{TODAY}T05:00:00+00:00", self.mutti())
        product = await self.product("234871")
        episode = product["episodes"][0]
        self.assertEqual((episode["price"], episode["perPiece"]), (2.49, 1.87), "de prijs van de winkel blijft, de stukprijs komt erbij")
        self.assertEqual((product["lowestPrice"], product["highestPrice"]), (1.87, 1.87), "de laagste actieprijs is de stukprijs")
        annotated = (await prices.annotate("ah", [self.mutti()], today=TODAY))[0]
        self.assertEqual(annotated["unitPrice"], episode["perPiece"], "de kaart en de prijsgeschiedenis noemen dezelfde prijs")

    async def test_2026_11_09_a_plain_discount_keeps_its_price(self):
        await self.sync_offer(f"{TODAY}T05:00:00+00:00", offer("1", "AH Penne", 0.89, 1.19, "25% korting"))
        product = await self.product("1")
        self.assertEqual((product["episodes"][0]["perPiece"], product["lowestPrice"]), (0.89, 0.89))

    @unittest.skipUnless(shutil.which("node"), "Node is not installed")
    def test_2026_11_09_the_chart_and_the_popup_show_the_price_per_piece(self):
        product = {
            "store": "ah",
            "productId": "234871",
            "title": "Mutti Pizzasaus aromatica",
            "unitSize": "388 ml",
            "onOfferNow": True,
            "episodes": [
                {
                    "start": days(0),
                    "end": days(6),
                    "open": False,
                    "price": 2.49,
                    "perPiece": 1.87,
                    "priceBefore": None,
                    "promoLabel": "2e halve prijs",
                }
            ],
            "timesOnOffer": 1,
            "lowestPrice": 1.87,
            "regularPrice": 2.49,
            "regular": [{"from": days(-300), "to": days(0), "price": 2.49}],
            "purchases": [{"date": days(-247), "price": 1.87, "quantity": 1, "source": "import", "ref": "1"}],
            "observed": [],
            "lineOffers": [],
        }
        shown = {"price": 2.49, "unitPrice": 1.87, "promoLabel": "2e halve prijs", "verdict": {"lowestRegular": 2.49}}
        html = test_history_chart_js.HistoryChartTest().render(product, offer=shown)
        bar = re.search(r'<g class="ohc-bar[^"]*"[^>]*><title>(.*?)</title><rect x="[\d.]+" y="([\d.]+)"', html["chart"])
        paid = re.search(r'class="ohc-paid".*?<circle cx="[\d.]+" cy="([\d.]+)"', html["chart"])
        head = re.search(r'<p class="ohc-head__price">(.*?)</p>', html["headOffer"])
        assert bar and paid and head, html
        self.assertIn("1,87", bar.group(1), "het hoverlabel noemt de stukprijs")
        self.assertNotIn("2,49", bar.group(1))
        # De balk van vandaag staat op de hoogte van de aankoop van 1,87: `y` is de bovenkant van de balk, `cy` het midden van het rondje.
        self.assertLess(abs(float(bar.group(2)) - float(paid.group(1))), 12, "de balk van vandaag staat op 1,87, niet op 2,49")
        self.assertRegex(html["detail"], r"<td>[^<]*1,87[^<]*</td><td>[^<]*2e halve prijs", "de tabel met periodes noemt de stukprijs")
        self.assertRegex(head.group(1), r"<b>[^<]*1,87</b> <s>[^<]*2,49</s>", "de kop van de popup toont de prijs zoals de kaart")


class RegularPriceFromEverySourceTest(PricesCase):
    """2026-11-12, gemeld: bij veel producten staat dat de app geen gewone prijs van dat product kent, terwijl er veel data is.
    Het oordeel keek alleen naar de laatste 30 dagen, dus een aankoop van een half jaar terug telde niet en de badge viel terug
    op de claim van de winkel (`store`)."""

    async def test_2026_11_12_an_imported_purchase_of_200_days_ago_is_a_known_regular_price(self) -> None:
        await self.sync_offer(f"{TODAY}T05:00:00+00:00", offer("1", "AH Pindakaas", 1.99, 2.99, "nu 1,99", validFrom=TODAY, validUntil=days(6)))
        with closing(db(price_import)) as conn, conn:
            conn.execute(
                "INSERT INTO imported_purchases (store, order_ref, position, day, title, quantity, paid, regular, imported_at) "
                "VALUES ('ah', 'M1', 1, ?, 'AH Pindakaas', 1, 2.79, 2.79, 'x')",
                (days(-200),),
            )
        # Sinds het antwoord van de eigenaar (eindreview 2026-11-12) telt een aankoop alleen met een bevestigde koppeling, niet op naam.
        await product_links.save("ah", "AH Pindakaas", {"productId": "1", "title": "AH Pindakaas"}, now=f"{TODAY}T00:00:00+00:00")
        annotated: dict = (await prices.annotate("ah", [offer("1", "AH Pindakaas", 1.99, 2.99, "nu 1,99")], today=TODAY))[0]
        self.assertNotEqual(annotated["badge"]["source"], "store", "de app kent een gewone prijs van dit product")
        self.assertEqual(annotated["badge"]["regular"], {"price": 2.79, "day": days(-200), "source": "import", "judged": True, "fresh": False})

    async def test_2026_11_12_an_import_linked_by_name_only_counts_once_you_link_it(self) -> None:
        """Uitspraak van de eigenaar bij de eindreview van 2026-11-12: alleen bevestigde koppelingen tellen in het oordeel. Een
        ingelezen aankoop die alleen op naam bij het product hoort (de mail noemt geen verpakking) staat in de prijsgeschiedenis,
        maar is geen bron voor het oordeel: de kaart houdt de badge van de winkel. Koppel je hem zelf, dan telt hij wel. Dit keert
        de melding hierboven om voor aankopen op naam, met opzet."""
        await self.sync_offer(f"{TODAY}T05:00:00+00:00", offer("1", "AH Pindakaas", 1.99, 2.99, "nu 1,99", validFrom=TODAY, validUntil=days(6)))
        with closing(db(price_import)) as conn, conn:
            conn.execute(
                "INSERT INTO imported_purchases (store, order_ref, position, day, title, quantity, paid, regular, imported_at) "
                "VALUES ('ah', 'M1', 1, ?, 'AH Pindakaas', 1, 2.79, 2.79, 'x')",
                (days(-200),),
            )
        annotated: dict = (await prices.annotate("ah", [offer("1", "AH Pindakaas", 1.99, 2.99, "nu 1,99")], today=TODAY))[0]
        self.assertEqual(annotated["badge"]["source"], "store", "op naam is geen bron voor het oordeel")
        purchases: list = (await self.product("1"))["purchases"]
        self.assertEqual([(p["date"], p["source"]) for p in purchases], [(days(-200), "import")], "wel in de prijsgeschiedenis")
        await product_links.save("ah", "AH Pindakaas", {"productId": "1", "title": "AH Pindakaas"}, now=f"{TODAY}T00:00:00+00:00")
        linked: dict = (await prices.annotate("ah", [offer("1", "AH Pindakaas", 1.99, 2.99, "nu 1,99")], today=TODAY))[0]
        self.assertEqual((linked["badge"]["source"], linked["badge"]["regular"]["source"]), ("dated", "import"))


class SpeedTest(PricesCase):
    """Meldingen over traagheid: het dure werk gebeurt een keer, niet bij elke aanvraag."""

    async def test_2026_11_08_offers_page_computes_the_verdict_once_per_etag(self):
        # "het laden van deze pagina duurt redelijk lang: /offers" (4.1.3): drie aanvragen bij het openen, elk het hele oordeel.
        await self.sync_offer(
            f"{TODAY}T05:00:00+00:00",
            offer("1", "AH Penne", 0.89, 1.19, "25% korting", unitSize="500 g"),
        )
        client = self.client_for(create_app())
        with mock.patch.object(prices, "annotate", side_effect=prices.annotate) as annotate:
            for _ in range(3):
                self.assertEqual((await client.get("/api/offers?store=ah")).status_code, 200)
        self.assertEqual(annotate.call_count, 1)

    async def test_2026_11_08_price_history_builds_the_catalogue_once(self):
        # "deze pagina is veel trager dan dat hij redelijkerwijs zou moeten zijn: /offers/history?..." (4.1.5).
        await prices.record("ah", [{"id": "1", "title": "AH Pindakaas", "price": 2.99}])
        client = self.client_for(create_app())
        with mock.patch.object(prices, "_known_products", wraps=prices._known_products) as built:
            for query in ("pindakaas", "pinda", "kaas"):
                self.assertEqual(
                    (
                        await client.get(
                            "/api/prices/history",
                            query_string={"store": "ah", "q": query},
                        )
                    ).status_code,
                    200,
                )
        self.assertEqual(built.call_count, 1)

    async def test_2026_11_08_the_verdict_reads_only_the_offered_products(self):
        # Het oordeel las de hele geschiedenis van de winkel (138.000 regels in productie), ook van producten zonder aanbieding.
        await self.sync_offer(
            "2026-01-05T05:00:00+00:00",
            offer(
                "2",
                "AH Hagelslag",
                1.49,
                1.99,
                "nu 1,49",
                validFrom="2026-01-05",
                validUntil="2026-01-11",
            ),
        )
        await self.sync_offer("2026-01-11T23:00:00+00:00")
        with mock.patch.object(prices, "_episode_effective", wraps=prices._episode_effective) as read:
            await prices.annotate("ah", [offer("1", "AH Penne", 0.89, 1.19, "25% korting")], today=TODAY)
        self.assertEqual(read.call_count, 0, "AH Hagelslag is niet in de aanbieding")


class ArchiveTest(unittest.TestCase):
    """Meldingen over het prijsarchief."""

    def test_2026_11_08_an_uncertain_archive_link_does_not_stretch_a_chart_to_2009(
        self,
    ):
        # "waarom begint die bij 2009 als daar geen data voor is?" (4.1.6): "AH Goudse oude kaas 48" uit 2009 hing op woorden
        # aan "AH Oude kaas L".
        row = {
            "store": "ah",
            "product_id": "579426",
            "title": "AH Oude kaas L",
            "source_title": "AH Goudse oude kaas 48",
            "brand": "AH",
            "unit_size": "400 g",
            "price": None,
            "price_before": None,
            "match_kind": "approximate",
            "description": "Van de versafdeling",
            "promo_label": "Folder | Van de versafdeling | koppeling onzeker: AH Oude kaas L",
            "url": "https://www.supermarktaanbiedingen.com/folders/albert_heijn/2009/26/ah-goudse-oude-kaas-48",
        }
        with closing(__import__("sqlite3").connect(":memory:")) as conn:
            kept = price_archive_tidy.Tidy(conn, keep_unlinked=True).offer(dict(row))
            dropped = price_archive_tidy.Tidy(conn, keep_unlinked=False).offer(dict(row))
        self.assertIsNone(dropped)
        assert kept is not None
        self.assertTrue(kept["product_id"].startswith("folder:"))
        self.assertEqual(kept["title"], "Folder: AH Goudse oude kaas 48")

    def test_2026_11_08_picnic_and_crisp_observations_are_prices(self):
        # De gists van Picnic en Crisp werden geweigerd als beschadigd archief (4.1.6).
        from mealieshopper import price_archive

        self.assertEqual(
            price_archive.site_document("picnic/observations/2022-001.json"),
            ("price", "picnic"),
        )
        self.assertEqual(price_archive.site_document("observations/2021-001.json"), ("price", None))


@unittest.skipUnless(shutil.which("node"), "Node is niet geïnstalleerd")
class BrowserTest(unittest.TestCase):
    """Meldingen over wat de pagina doet, met de echte router, tabknoppen en winkelkeuze in Node (zie test_history_stores_js)."""

    run_script = test_history_stores_js.HistoryStoreNavigationTest.run_script  # dezelfde nagebootste pagina, zonder die tests nog eens te draaien

    def test_2026_11_08_unlinked_store_stays_reachable_in_price_history(self):
        # 3.13.1 in productie, overgezet in 4.1.2: prijzen uit een archief van een winkel zonder koppeling waren onbereikbaar.
        result: dict = self.run_script("""
state.linkedStores = {};
routerApply('http://localhost/offers/history?store=crisp'); await settle();
console.log(JSON.stringify({store: offersState.store, requests}));
""")
        self.assertEqual(result["store"], "crisp")
        self.assertTrue(any("/api/prices/history?store=crisp&" in path for path in result["requests"]))

    def test_2026_11_08_offers_page_asks_once_for_the_right_week(self):
        # Drie aanvragen bij het openen van /offers (4.1.3): twee voor dezelfde week en een voor de week uit Mealie.
        result: dict = self.run_script("""
state.weekStartKnown = false; activePanel = 'offers';
showOffers(); showOffers(); await settle();
const before = requests.filter(path => path.startsWith('/api/offers?')).length;
weekStartSettled(); showOffers(); showOffers(); await settle();
console.log(JSON.stringify({before, after: requests.filter(path => path.startsWith('/api/offers?')).length}));
""")
        self.assertEqual(result, {"before": 0, "after": 1})

    def test_2026_11_08_link_with_a_product_sends_it_as_pin_from_the_first_request(
        self,
    ):
        # De link noemt het product op id (4.1.4); in 4.1.2 zette het adres dat pas na het eerste laden.
        result: dict = self.run_script("""
routerApply('http://localhost/offers/history?q=Dr.+Oetker&product=233387'); await settle();
console.log(JSON.stringify({requests: requests.filter(path => path.startsWith('/api/prices/history'))}));
""")
        self.assertTrue(result["requests"])
        self.assertTrue(all("pin=233387" in path for path in result["requests"]), result["requests"])


@unittest.skipUnless(shutil.which("node"), "Node is niet geïnstalleerd")
class StaleTabTest(unittest.TestCase):
    """2026-11-09: een tabblad van 6 november draaide na drie deploys nog de scripts van 3.13.1, en zocht dus op naam."""

    def test_the_asset_version_follows_the_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            static = Path(tmp)
            (static / "app.js").write_text("eerst")
            first = assets.asset_version(static)
            (static / "app.js").write_text("daarna")
            self.assertNotEqual(assets.asset_version(static), first)

    def test_healthz_tells_the_asset_version_and_the_page_carries_it(self):
        with mock.patch.dict(os.environ, {"MEALIESHOPPER_ASSET_VERSION": "abc123"}):
            app = create_app()
        self.assertEqual(app.config["ASSET_VERSION"], "abc123")
        template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn(
            '<meta name="ms-asset-version" content="{{ config.ASSET_VERSION }}">',
            template,
        )
        app_py = (ROOT / "mealieshopper" / "app.py").read_text(encoding="utf-8")
        self.assertIn('"assetVersion": app.config["ASSET_VERSION"]', app_py)

    def test_a_stale_tab_loads_the_whole_page_on_the_next_click(self):
        router = (ROOT / "static" / "router.js").read_text(encoding="utf-8")
        block = router[router.index("// ---- een tabblad van voor een nieuwe versie") :]
        navigate = router[router.index("function routerNavigate(href) {") : router.index("// Een winkel die in het adres staat")]
        script = (
            r"""
const document = {querySelector: () => ({content: "oud"}), addEventListener: () => {}, visibilityState: "visible"};
const assigned = []; const applied = [];
const window = {location: {origin: "http://localhost", pathname: "/offers", search: "", assign: (href) => assigned.push(href)},
  addEventListener: () => {}};
const history = {pushState: () => {}};
function routerRememberScroll() {}
function routerApply(href) { applied.push(href); }
function setInterval() {}
"""
            + block
            + navigate
            + r"""
routerNavigate("/offers/history?product=1");
routerAssets.server = "nieuw";
routerNavigate("/offers/history?product=2");
console.log(JSON.stringify({assigned, applied}));
"""
        )
        result = subprocess.run(["node", "-e", script], check=False, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "assigned": ["/offers/history?product=2"],
                "applied": ["http://localhost/offers/history?product=1"],
            },
        )


class CookingIngredientsFitOnAPhoneTest(unittest.TestCase):
    """2026-11-10, issue #309: "zorg dat de huidige lijst met ingredienten ook leesbaar is op mobiele schermen (hij valt nu half
    buiten beeld)". Eén regel met een lang woord (een link, een lange samenstelling) maakte de kolom van de kookweergave breder
    dan het venster, en daarmee de hele lijst: op een telefoon viel de rechterhelft weg, ook in het receptvenster van het weekmenu."""

    CSS = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")

    def rule(self, selector: str) -> str:
        found = re.search(rf"^{re.escape(selector)} \{{(.*?)\}}", self.CSS, re.S | re.M)
        assert found, selector
        return found.group(1)

    def test_the_column_never_gets_wider_than_the_window(self) -> None:
        self.assertIn("grid-template-columns: minmax(0, 1fr)", self.rule(".cook__columns"))

    def test_long_words_wrap_anywhere(self) -> None:
        for selector in (".cook-ingredient__text", ".cook .cook-step__text", ".cook-mode .cook-mode__text"):
            with self.subTest(selector):
                self.assertIn("overflow-wrap: anywhere", self.rule(selector), "break-word laat de minimale breedte van de kolom staan")

    def test_a_long_step_scrolls_from_the_top(self) -> None:
        # Met justify-content: center viel de bovenkant van een lange stap (met zijn ingrediënten en timers) buiten de scroll.
        stage = self.rule(".cook-mode__stage")
        self.assertNotIn("justify-content: center", stage)
        self.assertIn("overflow-y: auto", stage)
        self.assertIn("margin-top: auto", self.rule(".cook-mode__stage > :first-child"))


class LooseNamesSharedListTest(test_shared_links.SharedListCase):
    async def test_2026_11_10_a_loose_name_in_the_shared_list_counts_for_its_product(self):
        """Rick, 2026-11-10: "hoe lossen we dit probleem fatsoenlijk op voor de rest van de aankoopregels? kijk of er een patroon
        is en of we dit goed automatisch kunnen regelen". Een oude naam uit een bestelmail die in het gedeelde bestand staat, hoort
        bij het product van nu."""
        await self.seed_catalogue()
        orders = (
            test_price_import.mail_order(number=f"N{n}", day=f"2026-09-1{n}", items=test_shared_links.mail_lines("AH Doosjevol kersen"))
            for n in range(3)
        )
        await self.run_import(*orders)
        products = (await prices.history("ah", "", 50, today="2026-10-20", product="1234"))["products"]
        self.assertEqual(sum(p["timesBought"] for p in products if p["productId"] == "1234"), 1, "zonder het bestand is het een losse naam")
        prices._CATALOGUES.clear()  # het bestand verandert alleen met een update van de app, dus na een herstart van het proces
        with test_shared_links.shared(test_shared_links.CHERRIES):
            product = (await prices.history("ah", "", 50, today="2026-10-20", product="1234"))["products"][0]
        self.assertEqual((product["productId"], product["timesBought"]), ("1234", 4))
        self.assertEqual(sum(1 for p in product["purchases"] if p["source"] == "import"), 3)


class RemovedExactLinkTest(test_loose_names.LooseNamesCase):
    async def test_2026_11_11_a_removed_exact_link_stays_removed(self) -> None:
        """Eindreview van 2026-11-11: "An exact link that the user removes leaves the name in limbo, and the round makes the same
        link again". Na Ontkoppelen is de naam een kaart, een latere ronde koppelt hem niet opnieuw en Koppel op die kaart werkt."""
        name: str = "Jumbo Pindakaas naturel"
        self.bought(name, 2)
        search = test_loose_names.FakeSearch({name: [test_loose_names.item("1", "Jumbo Pindakaas Naturel", "350 g")]})
        self.assertEqual((await loose_names.run_round("jumbo", test_loose_names.NOW, search=search))["exact"], 1)
        self.assertEqual([link["origin"] for link in (await product_links.list_links("jumbo"))["links"]], ["exact"])
        self.assertTrue(await product_links.remove("jumbo", "name", product_links.fold(name)))
        cards: list[dict] = (await loose_names.page("jumbo"))["cards"]
        self.assertEqual([(card["name"], [c["productId"] for c in card["candidates"]]) for card in cards], [(name, ["1"])])
        again: dict = await loose_names.run_round("jumbo", test_loose_names.later(days=91), search=search)
        self.assertEqual((again["searches"], again["exact"], again["candidates"]), (1, 0, 1), "opgezocht, niet opnieuw gekoppeld")
        self.assertEqual((await product_links.list_links("jumbo"))["links"], [])
        self.assertEqual(self.row("jumbo", name)["outcome"], "candidates")
        third: dict = await loose_names.run_round("jumbo", test_loose_names.later(days=182), search=search)
        self.assertEqual((third["searches"], third["exact"], third["candidates"]), (1, 0, 1), "ook een ronde later niet")
        self.assertEqual((await product_links.list_links("jumbo"))["links"], [])
        link: dict = await loose_names.confirm("jumbo", cards[0]["key"], "1")
        self.assertEqual((link["productId"], link["origin"]), ("1", "confirmed"))
        self.assertEqual((await loose_names.page("jumbo"))["cards"], [])


class SameDealAFewCentsApartTest(unittest.TestCase):
    """2026-11-11: "dit lijkt me een afrondingsfout, dat mag genegeerd worden", bij drie broden van AH met 1+1 gratis voor 1,20
    per stuk en het label "0,03 boven de laagste van het jaar (aug)". In augustus was het dezelfde actie: de folder gaf geen
    prijs, dus de app rekende met de gewone prijs van de weken eromheen (2,34: 1,17 per stuk), nu met 2,39 (1,195). Een verschil
    van een paar cent tussen twee berekende prijzen is geen duurdere aanbieding."""

    TODAY = "2026-11-11"
    POINTS = [("2020-01-06", 2.2, "offer"), ("2026-08-31", 1.17, "line"), ("2026-09-28", 1.75, "line"), ("2026-11-10", 2.39, "purchase")]

    def test_the_same_deal_a_few_cents_higher_is_the_lowest_of_the_year(self) -> None:
        for effective in (1.195, 1.2):
            with self.subTest(effective):
                found = offer_ranking.year_low(effective, self.POINTS, self.TODAY)
                self.assertEqual((found["status"], found["above"], found["price"], found["month"]), ("lowest", 0.0, 1.17, "2026-08"))

    def test_a_real_difference_still_counts(self) -> None:
        found = offer_ranking.year_low(1.25, self.POINTS, self.TODAY)
        self.assertEqual((found["status"], found["above"]), ("above", 0.08))

    def test_the_margin_is_a_few_cents_at_most(self) -> None:
        # 3 procent van 10 euro is 30 cent, en dat is geen afronding meer: de marge houdt op bij 5 cent.
        points = [("2020-01-06", 12.0, "offer"), ("2026-08-31", 10.0, "offer")]
        self.assertEqual(offer_ranking.year_low(10.05, points, self.TODAY)["status"], "lowest")
        self.assertEqual(offer_ranking.year_low(10.06, points, self.TODAY)["status"], "above")
        cheap = [("2020-01-06", 0.5, "offer"), ("2026-08-31", 0.39, "offer")]
        self.assertEqual(offer_ranking.year_low(0.4, cheap, self.TODAY)["status"], "lowest")
        self.assertEqual(offer_ranking.year_low(0.42, cheap, self.TODAY)["status"], "above")


if __name__ == "__main__":
    unittest.main()


class WeeklyPriceArchiveTest(test_price_archive.ArchiveCase):
    """Weekprijzen behouden hun bron en het latere verzameltijdstip."""

    async def test_2026_11_13_weekly_prices_survive_both_exports_and_import(self) -> None:
        # "Weekprijzen verdwijnen uit prijsarchief export": historische weken zijn later verzameld.
        suffix: str
        document: str
        for suffix, document in (
            ("", "prices/ah/2022-07.jsonl"),
            ("?format=site", "data/ah/prices/2022-001.json"),
        ):
            with self.subTest(format=suffix or "archive"):
                conn: sqlite3.Connection
                with closing(self.connect()) as conn, conn:
                    conn.execute("DELETE FROM price_observations")
                    conn.execute(
                        "INSERT INTO price_observations "
                        "(store, product_id, day, source, title, brand, unit_size, price, seen_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        ("ah", "8001", "2022-07-04", "ahprijsindex", "AH Halfvolle melk", "AH", "1 l", 1.39, "2026-11-13T10:00:00+00:00"),
                    )
                response: Any = await self.admin.post("/api/price-archive/export" + suffix)
                self.assertEqual(response.status_code, 200)
                payload: bytes = response.get_data()
                archive: zipfile.ZipFile
                with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                    self.assertIn(document, archive.namelist())
                    records: list[dict[str, Any]] = (
                        json.loads(archive.read(document)) if suffix else [json.loads(line) for line in archive.read(document).splitlines()]
                    )
                    self.assertEqual(len(records), 1)
                    self.assertEqual(
                        (records[0]["day"], records[0]["source"], records[0]["seen_at"], records[0]["price"]),
                        ("2022-07-04", "ahprijsindex", "2026-11-13T10:00:00+00:00", 1.39),
                    )
                    if suffix:
                        jsonschema.Draft202012Validator(json.loads(archive.read("schemas/ah.schema.json"))).validate(records)
                with closing(self.connect()) as conn, conn:
                    conn.execute("DELETE FROM price_observations")
                await self.run_import(payload)
                restored: list[dict[str, Any]] = self.rows("price_observations")
                self.assertEqual(
                    [(row["store"], row["day"], row["source"], row["seen_at"], row["price"]) for row in restored],
                    [("ah", "2022-07-04", "ahprijsindex", "2026-11-13T10:00:00+00:00", 1.39)],
                )


async def test_2026_11_13_shared_weekly_prices_default_without_accounts(data_dir: Path, monkeypatch: Any) -> None:
    """2026-11-13: shared historical AH prices were never fetched automatically."""
    from datetime import datetime, timezone

    from mealieshopper import price_archive_sync

    monkeypatch.delenv("MEALIESHOPPER_SHARED_PRICES", raising=False)
    assert price_archive_sync.enabled()
    with (
        mock.patch.object(price_archive_sync, "run_if_due", mock.AsyncMock(return_value={"documents": 1})) as shared,
        mock.patch.object(offers_schedule, "_claim", mock.AsyncMock(return_value=False)),
    ):
        await offers_schedule.run_if_due(datetime(2026, 11, 13, 6, tzinfo=timezone.utc))
    shared.assert_awaited_once()


class WeeklyCaptureBoundsTest(unittest.TestCase):
    def test_2026_11_13_weekly_capture_dates_keep_their_validation_bounds(self) -> None:
        # "Weekprijzen verdwijnen uit prijsarchief export": alleen de prijsindex mag later verzameld zijn.
        bounds: price_archive.Today = price_archive.Today()
        bounds.today = date(2026, 11, 13)
        bounds.later = date(2027, 11, 14)
        row: dict[str, Any] = {
            "store": "ah",
            "product_id": "8001",
            "day": "2022-07-04",
            "source": "ahprijsindex",
            "title": "AH Halfvolle melk",
            "price": 1.39,
            "seen_at": "2026-11-13T10:00:00+00:00",
        }
        self.assertEqual(price_archive.normalise_price(row, bounds)[3], "ahprijsindex")
        changes: dict[str, str]
        reason: str
        for changes, reason in (
            ({"seen_at": "2022-07-03T23:59:59+00:00"}, "seen_at"),
            ({"seen_at": "2027-11-15T00:00:00+00:00"}, "date_range"),
            ({"source": "search"}, "seen_at"),
            ({"source": "archive_search"}, "seen_at"),
            ({"source": "public_press_release"}, "seen_at"),
            ({"source": "public_observed_unknown"}, "seen_at"),
            ({"store": "jumbo", "seen_at": "2022-07-04T00:00:00+00:00"}, "source"),
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(price_archive.RowError) as error:
                    price_archive.normalise_price({**row, **changes}, bounds)
                self.assertEqual(error.exception.reason, reason)
