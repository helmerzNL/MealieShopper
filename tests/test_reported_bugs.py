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
import unittest
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from helpers import db  # helpers zet ook het pad naar de app
import test_history_stores_js  # als module: anders draait pytest die tests hier nog een keer
from test_prices import PricesCase, offer

from mealieshopper import ah_orders, assets, offers, price_archive_tidy, prices
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
