"""Regressietests voor de bugs die de eigenaar van de installatie meldde, een test per melding, met de melding erbij.

Elke melding hier is een keer gerepareerd en daarna weer stukgegaan, of had dat gekund zonder test. De tests lopen waar het
kan door de echte routes (`/api/offers`, `/api/prices/history`, `/healthz`) en door de echte scripts in Node, zodat een
nieuwe versie die het gedrag terugdraait hier rood wordt. Een nieuwe melding krijgt hier een nieuwe test.
"""

import json
import os
import shutil
import subprocess
import tempfile
import inspect
import re
import unittest
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from helpers import AppTestCase, db  # helpers zet ook het pad naar de app
import test_history_chart_js  # als module, voor de Node-opbouw van offers_history.js
import test_history_stores_js  # als module: anders draait pytest die tests hier nog een keer
import test_offers_scan_js  # idem: de Node-opbouw van offers.js
from test_prices import PricesCase, offer

from mealieshopper import ah_orders, assets, offers, price_archive_tidy, price_import, prices
from mealieshopper.app import create_app

ROOT: Path = Path(__file__).resolve().parents[1]
TODAY: str = date.today().isoformat()


def days(n: int) -> str:
    return (date.today() + timedelta(days=n)).isoformat()


def order_item(
    product_id: int, title: str, price: float, unit_size: str = "500 g"
) -> dict:
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
            (await self.linked("een heel andere naam", "233387", bought="1"))[0][
                "productId"
            ],
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
            (await self.linked("AH Gekoelde sinaasappeldrank", "100845"))[0][
                "productId"
            ],
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
        found = (
            await prices.history("ah", "bospeen", 30, today=days(1), pin="188141")
        )["products"]
        self.assertEqual(found[0]["productId"], "188141")  # hoe hij dan telt: zie de test hieronder

    async def test_2026_11_09_offer_that_started_at_midnight_counts_and_has_a_chart(self):
        # "werkt nogsteeds niet zo te zien. ik zie hier staan 0 keer in de aanbieding en ik zie geen prijs grafiek" (4.1.8): de
        # bonusweek begon om middernacht, offer_history krijgt hem pas bij de synchronisatie van die nacht.
        await self.sync_offer(f"{TODAY}T05:00:00+00:00", offer("233387", "Dr. Oetker Casa di mama pizza salame extra piccante", 3.65, 4.29,
                                                              "Bonus Box 15% korting", validFrom=days(1), validUntil=days(7)))

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
        await self.sync_offer(f"{TODAY}T05:00:00+00:00", offer("233387", "Dr. Oetker Casa di mama pizza salame extra piccante", 3.65, 4.29,
                                                              "Bonus Box 15% korting", validFrom=TODAY, validUntil=days(6)))
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
        self.assertEqual([(line["start"], line["title"], line["promoLabel"]) for line in product["lineOffers"]], [
            ("2009-10-26", "Folder: Alle Dr. Oetker Casa di Mama pizza's", "2 dozen, actie 3.50, was 2 stuks 5.38"),
            ("2016-11-14", "Folder: Dr. Oetker Casa di Mama", "2 voor 4.00"),
        ])
        self.assertEqual((product["timesInLineOffer"], product["timesOnOffer"]), (2, 1))

    async def test_2026_11_09_line_offers_have_a_price_per_piece_and_purchases_give_the_regular_price(self):
        # "ik zie nogsteeds geen andere prijzen behalve de huidige hier ... ik weet zeker dat ik deze ook meermaals besteld heb dus
        # ook dat mist hierbij. de prijsgrafiek is ook stuk nu." (4.1.10, Dr. Oetker Casa di mama pizza salami, 514010). Het archief
        # gaf "2 voor 6.00" de ene week als 3.00 per stuk en de andere als 6.00 voor twee, de import gooide de prijs zonder
        # verpakking weg, en de gewone prijs kwam alleen uit de bestelling van de laatste tien dagen.
        await self.sync_offer(f"{TODAY}T05:00:00+00:00", offer("514010", "Dr. Oetker Casa di mama pizza salami", 3.65, 4.29, "Bonus Box 15% korting",
                                                              unitSize="390 g", validFrom=TODAY, validUntil=days(6)))
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
                    (product_id, title, price, before, label, start, end, f"{start}T00:00:00+00:00", f"{end}T23:59:59+00:00", f"{end}T23:59:59+00:00"),
                )
        with closing(db(price_import)) as conn, conn:
            conn.execute(
                "INSERT INTO imported_purchases (store, order_ref, position, day, title, quantity, paid, regular, imported_at) "
                "VALUES ('ah', '232668613', 1, '2024-07-04', 'Dr. Oetker Casa di mama pizza salami', 1, 3.0, 4.29, ?)", (f"{TODAY}T00:00:00+00:00",),
            )
        product = (await self.linked("Dr. Oetker Casa di mama pizza salami", "514010"))[0]
        self.assertEqual(product["productId"], "514010")
        self.assertEqual([(line["start"], line["price"], line["priceBefore"]) for line in product["lineOffers"]],
                         [("2024-07-01", 3.0, 4.29), ("2024-09-30", 3.0, 4.29), ("2026-08-31", 2.15, 4.29)], "per stuk, zonder de brownies")
        self.assertEqual([(purchase["date"], purchase["price"]) for purchase in product["purchases"]], [("2024-07-04", 3.0)])
        self.assertEqual(product["regular"][0]["from"], "2024-07-04", "de gewone prijs uit de ingelezen aankoop")
        self.assertEqual(product["unitSize"], "390 g")

    def test_2026_11_09_line_offers_from_the_archive_are_kept_on_import(self):
        # Dezelfde melding: met "Ook regels bewaren die bij geen product van jou horen" blijft een combi als eigen folderproduct.
        line = {"store": "ah", "product_id": "folder:x", "title": "Folder: Alle Dr. Oetker Casa di Mama pizza's", "price": None,
                "price_before": None, "unit_size": None, "description": "2 dozen", "source_title": "Alle Dr. Oetker Casa di Mama pizza's",
                "promo_label": "Folder | 2 dozen | bron: oud=2 stuks 5.38, actie=3.50 | prijs onzeker",
                "url": "https://www.supermarktaanbiedingen.com/folders/albert_heijn/2009/44/x"}
        with closing(db(prices)) as conn:
            kept = price_archive_tidy.Tidy(conn, keep_unlinked=True).offer(dict(line))
            dropped = price_archive_tidy.Tidy(conn).offer(dict(line))
        self.assertEqual((kept["product_id"], kept["promo_label"]), ("folder:x", "2 dozen, actie 3.50, was 2 stuks 5.38"))
        self.assertIsNone(dropped, "zonder keep_unlinked komt een combi er nog steeds niet in")

    async def test_an_unknown_id_changes_nothing(self):
        await prices.record("ah", [{"id": "1", "title": "AH Pindakaas", "price": 2.99}])
        self.assertEqual(
            [p["productId"] for p in await self.linked("pindakaas", "99999999")], ["1"]
        )


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
                    (days(start), days(start + 6), f"{days(start)}T00:00:00+00:00", f"{days(start + 6)}T23:59:59+00:00", f"{days(start + 6)}T23:59:59+00:00"),
                )
        await self.sync_offer(
            f"{TODAY}T05:00:00+00:00",
            offer("514010", "Dr. Oetker Casa di mama pizza salami", 2.15, 4.29, "nu 2,15", unitSize="390 g", brand="Dr. Oetker", validFrom=TODAY, validUntil=days(6)),
            offer("233387", "Dr. Oetker Casa di mama pizza salame extra piccante", 3.65, 4.29, "nu 3,65", unitSize="415 g", brand="Dr. Oetker", validFrom=TODAY, validUntil=days(6)),
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
        filters = {"brands": ["Dr. Oetker"], "bought": False, "minDiscount": 15, "maxUnitPrice": {"value": None, "unit": "kg"}, "yearLow": False, "rare": False}
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
        product = {"store": "ah", "productId": "514010", "title": offer["title"], "purchases": [{"date": "2026-09-01", "price": 2.15}], "episodes": []}
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
        html = self.render({"id": "234871", "title": "Mutti Pizzasaus aromatica", "image": "i.png", "url": "https://www.ah.nl/producten/product/wi234871"})
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
        product = {"store": "ah", "productId": "234871", "title": offer["title"], "purchases": [{"date": "2026-03-07", "price": 1.87}], "episodes": []}
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

    def test_2026_11_10_the_mouse_goes_through_the_popup(self):
        css = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
        self.assertIn(".offer-popup {\n  pointer-events: none;\n}", css, "de kaart onder de popup blijft aan te wijzen")
        self.assertNotIn(".offer-popup:focus-within", css, "er valt in de popup niets meer te klikken")
        self.assertNotIn("pointer-events", css[css.index(".offer-popup__dialog") :].split("}")[0], "het venster op een aanraakscherm blijft gewoon klikbaar")


class PackFamiliesTest(PricesCase):
    """Melding van 2026-11-09: "rundergehakt van 300gr, 500gr of 1000gr hebben vaak losse prijzen waarbij de 300gr in de aanbieding
    is, dat maakt het nog niet goedkoper dan de bulk 1000gr verpakking"."""

    async def test_2026_11_09_packs_of_one_product_share_a_card_with_the_cheapest_per_unit(self):
        await prices.record("ah", [{"id": "1000", "title": "AH Rundergehakt 1000 g", "price": 7.49, "unitSize": "1000 g", "brand": "AH"}], now="2026-11-08T08:00:00+00:00")
        found = {item["id"]: item for item in await prices.annotate("ah", [offer("300", "AH Rundergehakt 300 g", 3.65, 4.29, "nu 3,65", unitSize="300 g", brand="AH")], today="2026-11-09")}
        family = found["300"]["family"]
        self.assertEqual([(p["productId"], p["cheapest"]) for p in family["packs"]], [("300", False), ("1000", True)])
        self.assertEqual(found["300"]["verdict"]["cheaperPack"]["productId"], "1000", "de 300 g in de aanbieding is per kilo duurder dan de 1000 g")


class ConditionalOfferPriceTest(PricesCase):
    """Melding van 2026-11-09 bij Mutti Pizzasaus aromatica (2e halve prijs): "ik zie 2x een lagere prijs afgelopen jaar ook al
    heb ik 'laagste prijs in een jaar' aangeklikt. er staat vandaag 2,49 euro in de grafiek". De kaart en het filter rekenden met
    wat een stuk bij de actie kost (1,87), de prijsgeschiedenis in de popup met de gewone prijs die AH bij zo'n actie als prijs
    noemt (2,49): als actieprijs van vandaag, als laagste actieprijs en als balk in de grafiek."""

    def mutti(self) -> dict:
        return {"id": "234871", "title": "Mutti Pizzasaus aromatica", "price": 2.49, "priceBefore": None, "promoLabel": "2e halve prijs",
                "unitSize": "388 ml", "brand": "Mutti"}

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
            "store": "ah", "productId": "234871", "title": "Mutti Pizzasaus aromatica", "unitSize": "388 ml", "onOfferNow": True,
            "episodes": [{"start": days(0), "end": days(6), "open": False, "price": 2.49, "perPiece": 1.87, "priceBefore": None, "promoLabel": "2e halve prijs"}],
            "timesOnOffer": 1, "lowestPrice": 1.87, "regularPrice": 2.49, "regular": [{"from": days(-300), "to": days(0), "price": 2.49}],
            "purchases": [{"date": days(-247), "price": 1.87, "quantity": 1, "source": "import", "ref": "1"}], "observed": [], "lineOffers": [],
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


class SpeedTest(PricesCase):
    """Meldingen over traagheid: het dure werk gebeurt een keer, niet bij elke aanvraag."""

    async def test_2026_11_08_offers_page_computes_the_verdict_once_per_etag(self):
        # "het laden van deze pagina duurt redelijk lang: /offers" (4.1.3): drie aanvragen bij het openen, elk het hele oordeel.
        await self.sync_offer(
            f"{TODAY}T05:00:00+00:00",
            offer("1", "AH Penne", 0.89, 1.19, "25% korting", unitSize="500 g"),
        )
        client = self.client_for(create_app())
        with mock.patch.object(
            prices, "annotate", side_effect=prices.annotate
        ) as annotate:
            for _ in range(3):
                self.assertEqual(
                    (await client.get("/api/offers?store=ah")).status_code, 200
                )
        self.assertEqual(annotate.call_count, 1)

    async def test_2026_11_08_price_history_builds_the_catalogue_once(self):
        # "deze pagina is veel trager dan dat hij redelijkerwijs zou moeten zijn: /offers/history?..." (4.1.5).
        await prices.record("ah", [{"id": "1", "title": "AH Pindakaas", "price": 2.99}])
        client = self.client_for(create_app())
        with mock.patch.object(
            prices, "_known_products", wraps=prices._known_products
        ) as built:
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
        with mock.patch.object(
            prices, "_episode_effective", wraps=prices._episode_effective
        ) as read:
            await prices.annotate(
                "ah", [offer("1", "AH Penne", 0.89, 1.19, "25% korting")], today=TODAY
            )
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
            dropped = price_archive_tidy.Tidy(conn, keep_unlinked=False).offer(
                dict(row)
            )
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
        self.assertEqual(
            price_archive.site_document("observations/2021-001.json"), ("price", None)
        )


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
        self.assertTrue(
            any(
                "/api/prices/history?store=crisp&" in path
                for path in result["requests"]
            )
        )

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
        self.assertTrue(
            all("pin=233387" in path for path in result["requests"]), result["requests"]
        )


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
        navigate = router[
            router.index("function routerNavigate(href) {") : router.index(
                "// Een winkel die in het adres staat"
            )
        ]
        script = (
            r"""
const document = {querySelector: () => ({content: "oud"}), addEventListener: () => {}, visibilityState: "visible"};
const assigned = []; const applied = [];
const window = {location: {origin: "http://localhost", pathname: "/offers", search: "", assign: (href) => assigned.push(href)},
  addEventListener: () => {}};
const history = {pushState: () => {}};
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
        result = subprocess.run(
            ["node", "-e", script], check=False, capture_output=True, text=True, timeout=30
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "assigned": ["/offers/history?product=2"],
                "applied": ["http://localhost/offers/history?product=1"],
            },
        )


if __name__ == "__main__":
    unittest.main()
