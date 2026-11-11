"""De AH Bonus Box: je persoonlijke aanbiedingen staan tussen de aanbiedingen, met een schakelaar om alleen die te zien.

AH geeft de Bonus Box alleen met je AH-koppeling. `ah.get_bonus_box` haalt hem op (GraphQL, anders de REST-route), `offers.sync` zet hem
bovenaan de aanbiedingen van AH met `personal` en `activated`, en de schakelaar "Alleen Bonus Box" in static/offers.js laat alleen die zien.
"""

import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

os.environ.setdefault("MEALIESHOPPER_DATA_DIR", tempfile.mkdtemp())

from mealieshopper import ah, auth, offers  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402
from helpers import AppTestCase, returned, run  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


class TempDataDirTest(AppTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"MEALIESHOPPER_DATA_DIR": tmp.name, "PASSKEY_AUTH_ENABLED": "false"})
        patcher.start()
        self.addCleanup(patcher.stop)
        run(auth.init_db())


def _box_promotion(pid, label, products, status="ACTIVATED", start="2026-11-02", end="2026-11-08", kind="PERSONAL", price=None):
    top, _, bottom = label.partition(" ")
    return {"id": pid, "title": f"Bonus Box {pid}", "periodStart": start, "periodEnd": end, "promotionType": kind, "category": "Zuivel",
            "activationStatus": status, "promotionLabels": [{"topText": top, "bottomText": bottom}], "price": price, "products": products}


def _product(pid, title, price):
    return {"id": pid, "title": title, "brand": "AH", "salesUnitSize": "500 g", "price": {"now": {"amount": price}, "was": {"amount": price}},
            "imagePack": [{"small": {"url": f"https://img/{pid}.webp"}}]}


PERIODS = {"bonusPeriods": [{"startDate": "2026-10-26", "endDate": "2026-11-01", "weekNumber": 44},
                            {"startDate": "2026-11-02", "endDate": "2026-11-08", "weekNumber": 45}]}
BOX = {"bonusPromotions": [
    _box_promotion("b1", "25% korting", [_product(101, "AH Griekse yoghurt", 2.0)]),
    _box_promotion("b2", "2 voor 5", [_product(102, "Perla koffiebonen", 6.49)], status="NOT_ACTIVATED"),
    # Zonder producten: dan is de actie zelf de aanbieding, met haar eigen titel en prijs.
    _box_promotion("b3", "voor 1.99", [], price={"now": {"amount": 1.99}, "was": {"amount": 2.79}}),
    # De gewone bonus hoort er niet bij, ook niet als AH het filter zou negeren.
    _box_promotion("n1", "1+1 gratis", [_product(103, "Gewone bonus", 3.0)], status=None, kind="NATIONAL"),
]}


def fake_graphql(box=BOX, periods=PERIODS, calls=None):
    def graphql(token, query, variables, what):
        if calls is not None:
            calls.append((token, query, dict(variables)))
        if "bonusPeriods" in query:
            return periods
        if "APP_BONUS_BOX" in query:
            if isinstance(box, Exception):
                raise box
            return box if variables.get("periodStart") == "2026-11-02" else {"bonusPromotions": []}
        raise AssertionError(query)
    return graphql


class BonusBoxFetchTest(AppTestCase):
    def linked(self, token="u"):
        return mock.patch.multiple(ah, configured_refresh_token=mock.AsyncMock(return_value="refresh"), get_user_token=mock.AsyncMock(return_value=token))

    def test_the_box_comes_from_graphql_per_bonus_week_with_its_activation(self):
        calls = []
        with self.linked(), mock.patch.object(ah, "_graphql", side_effect=fake_graphql(calls=calls)):
            found = {item["id"]: item for item in run(ah.get_bonus_box(date(2026, 11, 1)))}
        self.assertEqual(sorted(found), ["101", "102", "bonusbox-b3"], "de gewone bonus hoort niet in de Bonus Box")
        yoghurt = found["101"]
        self.assertEqual((yoghurt["title"], yoghurt["price"], yoghurt["priceBefore"], yoghurt["promoLabel"]), ("AH Griekse yoghurt", 1.5, 2.0, "25% korting"))
        self.assertEqual((yoghurt["validFrom"], yoghurt["validUntil"], yoghurt["activated"]), ("2026-11-02", "2026-11-08", True))
        self.assertEqual(yoghurt["url"], "https://www.ah.nl/producten/product/wi101")
        self.assertIs(found["102"]["activated"], False)
        own = found["bonusbox-b3"]
        self.assertEqual((own["title"], own["price"], own["priceBefore"], own["url"]), ("Bonus Box b3", 1.99, 2.79, ""))
        box_calls = [variables for token, query, variables in calls if "APP_BONUS_BOX" in query]
        self.assertEqual(box_calls, [{"periodStart": "2026-10-26", "periodEnd": "2026-11-01", "weekNumber": 44},
                                     {"periodStart": "2026-11-02", "periodEnd": "2026-11-08", "weekNumber": 45}])
        self.assertTrue(all(token == "u" for token, _, _ in calls), "de Bonus Box is persoonlijk: altijd met het gebruikerstoken")

    def test_weeks_that_are_over_are_skipped_and_without_weeks_this_week_is_asked(self):
        calls = []
        with self.linked(), mock.patch.object(ah, "_graphql", side_effect=fake_graphql(calls=calls)):
            run(ah.get_bonus_box(date(2026, 11, 3)))
        self.assertEqual([v["periodStart"] for _, q, v in calls if "APP_BONUS_BOX" in q], ["2026-11-02"])
        calls.clear()
        with self.linked(), mock.patch.object(ah, "_graphql", side_effect=fake_graphql(periods={}, calls=calls)):
            run(ah.get_bonus_box(date(2026, 11, 4)))
        self.assertEqual([v for _, q, v in calls if "APP_BONUS_BOX" in q], [{"periodStart": "2026-11-02", "periodEnd": "2026-11-08", "weekNumber": 45}])

    def test_without_an_ah_link_there_is_no_box_and_nothing_is_asked(self):
        with mock.patch.object(ah, "configured_refresh_token", return_value=""), mock.patch.object(ah, "_graphql") as graphql, \
                mock.patch.object(ah, "get_user_token") as token:
            with self.assertRaises(LookupError):
                run(ah.get_bonus_box())
        graphql.assert_not_called()
        token.assert_not_called()

    def test_the_rest_route_is_the_fallback(self):
        page = {"sectionType": "PERSONAL", "bonusGroupOrProducts": [
            {"product": {"webshopId": 111, "title": "Bio yoghurt", "currentPrice": 1.49, "priceBeforeBonus": 1.99, "isBonus": True,
                         "bonusMechanism": "25% KORTING", "activationStatus": "ACTIVATED", "promotionType": "PERSONAL", "segmentType": "BBOX"}},
            {"bonusGroup": {"discountDescription": "1 + 1 GRATIS", "activationStatus": "NOT_ACTIVATED", "bonusStartDate": "2026-11-02",
                            "bonusEndDate": "2026-11-08", "products": [{"webshopId": 222, "title": "Koffiebonen", "currentPrice": 6.49}]}},
        ]}
        requests_made = []

        def rest(method, url, **kwargs):
            requests_made.append((url, kwargs["params"], kwargs["headers"].get("Authorization")))
            return page

        with self.linked(), mock.patch.object(ah, "_graphql", side_effect=fake_graphql(box=RuntimeError("Unknown argument filterSet"))), \
                mock.patch.object(ah, "_request_json", side_effect=rest):
            found = {item["id"]: item for item in run(ah.get_bonus_box(date(2026, 11, 3)))}
        self.assertEqual(requests_made, [(f"{ah.AH_API_BASE}/mobile-services/bonuspage/v1/personal", {"bonusStartDate": "2026-11-02"}, "Bearer u")])
        self.assertEqual((found["111"]["promoLabel"], found["111"]["activated"]), ("25% KORTING", True))
        self.assertEqual((found["111"]["validFrom"], found["111"]["validUntil"]), ("2026-11-02", "2026-11-08"), "zonder eigen looptijd die van de week")
        self.assertEqual((found["222"]["promoLabel"], found["222"]["activated"]), ("1 + 1 GRATIS", False))

    def test_when_both_routes_fail_the_error_says_why(self):
        with self.linked(), mock.patch.object(ah, "_graphql", side_effect=fake_graphql(box=RuntimeError("graphql stuk"))), \
                mock.patch.object(ah, "_request_json", side_effect=RuntimeError("404: niet gevonden")):
            with self.assertRaisesRegex(RuntimeError, "graphql stuk.*404"):
                run(ah.get_bonus_box(date(2026, 11, 3)))

    def test_a_refused_token_is_retried_once_with_a_fresh_one(self):
        tokens = iter(["oud", "nieuw"])
        used = []

        def graphql(token, query, variables, what):
            used.append(token)
            if token == "oud":
                raise ah.TokenRejected("401")
            return fake_graphql()(token, query, variables, what)

        with mock.patch.object(ah, "configured_refresh_token", return_value="r"), mock.patch.object(ah, "get_user_token", side_effect=lambda: next(tokens)), \
                mock.patch.object(ah, "_graphql", side_effect=graphql):
            found = run(ah.get_bonus_box(date(2026, 11, 3)))
        self.assertEqual(len(found), 3)
        self.assertEqual(used[0], "oud")
        self.assertTrue(all(token == "nieuw" for token in used[1:]))


class BonusBoxOffersTest(TempDataDirTest):
    def setUp(self):
        super().setUp()
        today = date.today()
        self.monday = today - timedelta(days=today.weekday())
        sunday = self.monday + timedelta(days=6)
        week = {"validFrom": self.monday.isoformat(), "validUntil": sunday.isoformat()}
        self.national = [
            {"id": "1", "title": "AH Kipfilet", "price": 5.0, "priceBefore": 7.0, "promoLabel": "30% korting", **week},
            {"id": "2", "title": "AH Halfvolle melk", "price": 1.0, "priceBefore": 1.2, "promoLabel": "voor 1.00", **week},
        ]
        self.box = [
            {"id": "9", "title": "Calvé crunchy", "categoryHint": "Ontbijtgranen en beleg", "price": 1.5, "priceBefore": 2.0, "promoLabel": "25% korting", "activated": True, **week},
            {"id": "2", "title": "AH Halfvolle melk", "price": 0.8, "priceBefore": 1.2, "promoLabel": "Bonus Box", "activated": False, **week},
        ]

    async def sync(self, box=None, linked=True, national=None):
        fetch_box = box if callable(box) else (lambda: returned(self.box if box is None else box))
        with mock.patch.dict(offers.FETCHERS, {"ah": lambda: returned(self.national if national is None else national)}), \
                mock.patch.dict(offers.PERSONAL_FETCHERS, {"ah": fetch_box}), \
                mock.patch.object(ah, "configured_refresh_token", return_value="r" if linked else ""):
            return await offers.sync("ah")

    async def test_the_box_is_stored_on_top_of_the_offers_with_its_activation(self):
        data = await self.sync()
        listed = [(o["id"], o["personal"], o["activated"]) for o in data["offers"]]
        self.assertEqual(listed, [("9", True, True), ("2", True, False), ("1", False, None), ("2", False, None)],
                         "de Bonus Box bovenaan; staat een product ook in de gewone bonus, dan blijven beide acties staan")
        self.assertEqual(data["personal"], {"supported": True, "linked": True, "count": 2, "activated": 1, "error": None})
        with mock.patch.object(ah, "configured_refresh_token", return_value="r"):
            week = await offers.load("ah", self.monday.isoformat())
        self.assertEqual([(o["id"], o["personal"]) for o in week["offers"]], [("9", True), ("2", True), ("1", False), ("2", False)])
        self.assertEqual(week["personal"]["count"], 2)

    async def test_the_history_records_the_box_and_prefers_the_national_bonus(self):
        await self.sync()
        history = {p["productId"]: p for p in (await offers.history("ah"))["products"]}
        self.assertTrue(history["9"]["episodes"][0]["personal"], "een product alleen in je Bonus Box staat er als persoonlijke aanbieding in")
        self.assertFalse(history["2"]["episodes"][0]["personal"], "staat het ook in de gewone bonus, dan telt die")
        self.assertEqual(history["2"]["episodes"][0]["price"], 1.0)

    async def test_a_failing_box_keeps_the_previous_box_and_reports_the_error(self):
        await self.sync()
        self.national[0]["price"] = 4.5

        def broken():
            raise RuntimeError("AH Bonus Box ophalen mislukt (500)")

        before = {o["id"]: o["category"] for o in (await offers.load("ah"))["offers"] if o["personal"]}
        data = await self.sync(box=broken)
        self.assertEqual(data["personal"]["error"], "AH Bonus Box ophalen mislukt (500)")
        self.assertEqual({o["id"]: o["category"] for o in data["offers"] if o["personal"]}, before, "ook met hun categorie")
        self.assertEqual([(o["id"], o["activated"]) for o in data["offers"] if o["personal"]], [("9", True), ("2", False)])
        self.assertEqual(next(o for o in data["offers"] if o["id"] == "1")["price"], 4.5, "de gewone bonus is wel bijgewerkt")
        data = await self.sync()
        self.assertIsNone(data["personal"]["error"], "de volgende keer dat het lukt is de fout weg")

    async def test_without_a_link_there_is_no_box_and_the_list_says_so(self):
        called = mock.AsyncMock(return_value=self.box)
        data = await self.sync(box=called, linked=False)
        called.assert_not_called()
        self.assertFalse(any(o["personal"] for o in data["offers"]))
        self.assertEqual(data["personal"], {"supported": True, "linked": False, "count": 0, "activated": 0, "error": None})
        self.assertNotEqual(await offers.signature("ah"), await self.signature_when_linked(), "koppelen verandert de lijst, dus ook de ETag")

    async def signature_when_linked(self):
        with mock.patch.object(ah, "configured_refresh_token", return_value="r"):
            return await offers.signature("ah")

    def test_other_stores_have_no_box(self):
        with mock.patch.dict(offers.FETCHERS, {"jumbo": lambda: returned(self.national)}):
            data = run(offers.sync("jumbo"))
        self.assertEqual(data["personal"]["supported"], False)
        self.assertFalse(any(o["personal"] for o in data["offers"]))

    def test_sync_all_and_the_overview_count_the_box(self):
        with mock.patch.dict(offers.FETCHERS, {"ah": lambda: returned(self.national)}), mock.patch.dict(offers.PERSONAL_FETCHERS, {"ah": lambda: returned(self.box)}), \
                mock.patch.object(ah, "configured_refresh_token", return_value="r"), mock.patch.object(offers, "linked", side_effect=lambda store: store == "ah"):
            result = run(offers.sync_all())
            overview = run(offers.overview())
        self.assertEqual(result["stores"]["ah"]["personal"]["count"], 2)
        self.assertEqual(result["stores"]["ah"]["count"], 4)
        self.assertEqual(overview["ah"]["personal"], {"linked": True, "count": 2, "error": None})
        self.assertNotIn("personal", overview["jumbo"])

    async def test_the_offers_route_gives_the_box(self):
        await self.sync()
        client = self.client_for(create_app())
        with mock.patch.object(ah, "configured_refresh_token", return_value="r"):
            data = (await client.get(f"/api/offers?store=ah&week={self.monday.isoformat()}")).get_json()
        self.assertEqual(data["personal"]["count"], 2)
        self.assertEqual([o["id"] for o in data["offers"] if o["personal"]], ["9", "2"])

    async def test_a_database_from_before_the_box_gets_the_new_columns(self):
        path = auth.db_path()
        with closing(sqlite3.connect(path)) as conn, conn:
            for table in ("offers", "offer_history", "offers_meta"):
                conn.execute(f"DROP TABLE IF EXISTS {table}")
            conn.execute(
                "CREATE TABLE offers (store TEXT NOT NULL, product_id TEXT NOT NULL, position INTEGER NOT NULL, title TEXT NOT NULL, brand TEXT, "
                "unit_size TEXT, price REAL, price_before REAL, promo_label TEXT, image TEXT, url TEXT, valid_from TEXT, valid_until TEXT, category TEXT)"
            )
            conn.execute("INSERT INTO offers (store, product_id, position, title, price) VALUES ('ah', '1', 0, 'AH Kipfilet', 5.0)")
            conn.execute(
                "CREATE TABLE offer_history (id INTEGER PRIMARY KEY AUTOINCREMENT, store TEXT NOT NULL, product_id TEXT NOT NULL, title TEXT NOT NULL, "
                "brand TEXT, unit_size TEXT, category TEXT, price REAL, price_before REAL, promo_label TEXT, image TEXT, url TEXT, valid_from TEXT, "
                "valid_until TEXT, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, ended_at TEXT, syncs INTEGER NOT NULL DEFAULT 1, "
                "continued INTEGER NOT NULL DEFAULT 0)"
            )
            conn.execute("CREATE TABLE offers_meta (store TEXT PRIMARY KEY, synced_at TEXT NOT NULL)")
            conn.execute("INSERT INTO offers_meta VALUES ('ah', '2026-11-01T06:00:00+00:00')")
        data = await offers.load("ah")
        self.assertEqual([(o["id"], o["personal"], o["activated"]) for o in data["offers"]], [("1", False, None)])
        self.assertIsNone(data["personal"]["error"])
        await self.sync()
        self.assertEqual(sum(1 for o in (await offers.load("ah"))["offers"] if o["personal"]), 2)


def function_source(source, name):
    match = re.search(rf"^(?:async )?function {re.escape(name)}\(.*?^}}$", source, re.S | re.M)
    if not match:
        raise AssertionError(f"functie {name} niet gevonden")
    return match.group(0)


class BonusBoxSwitchTest(AppTestCase):
    script = (ROOT / "static" / "offers.js").read_text(encoding="utf-8")
    html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")

    def test_the_switch_sits_above_the_categories_and_is_remembered(self):
        section = self.html[self.html.index('<div id="offers-items"'):self.html.index('<div id="offers-suggestions"')]
        self.assertIn('<input type="checkbox" id="offers-personal-only" role="switch">', section)
        self.assertLess(section.index('id="offers-categories"'), section.index('id="offers-personal"'), "beide in de filterzijbalk")
        self.assertIn('data-i18n="offers.personal.switch"', section)
        self.assertIn('localStorage.setItem(OFFERS_PERSONAL_KEY, on ? "1" : "0")', self.script)
        self.assertIn('$("#offers-personal-only").addEventListener("change"', self.script)
        self.assertIn("renderOffersPersonal();\n  renderOfferCategories();\n  renderOfferGrid();", self.script)

    @unittest.skipUnless(shutil.which("node"), "Node is niet geïnstalleerd")
    def test_the_switch_filters_the_offers_and_their_categories(self):
        functions = "\n".join(function_source(self.script, name) for name in ("offersPersonalOnly", "offersBase", "offersFiltered", "offersCategoryCounts", "offerPersonalBadge"))
        program = f"""
const t = (key) => key;
const escapeHtml = (value) => String(value);
const offersFilteredWithFamilies = (list) => list; // de zijbalkfilters hebben hun eigen tests (test_offers_scan_js.py)
const offersFilters = () => ({{}}); const offersFilterMatch = () => true;
const offersShownCategories = () => offersCategoryCounts(offersBase());
const offersState = {{
  personalOnly: false, personal: {{ supported: true }}, search: "", goodOnly: false,
  offers: [
    {{ id: "9", category: "zuivel_eieren", personal: true, activated: true }},
    {{ id: "8", category: "dranken", personal: true, activated: false }},
    {{ id: "1", category: "vlees", personal: false }},
    {{ id: "2", category: "zuivel_eieren", personal: false }},
  ],
  categories: [{{ id: "zuivel_eieren", count: 2 }}, {{ id: "vlees", count: 1 }}, {{ id: "dranken", count: 1 }}],
}};
{functions}
const result = {{ all: offersFiltered().map((o) => o.id), allCats: offersShownCategories() }};
offersState.personalOnly = true;
result.box = offersFiltered().map((o) => o.id);
result.boxCats = offersShownCategories();
result.badges = offersState.offers.map(offerPersonalBadge);
offersState.personal = {{ supported: false }};
result.otherStore = offersFiltered().map((o) => o.id);
console.log(JSON.stringify(result));
"""
        output = subprocess.run(["node", "-e", program], capture_output=True, text=True, check=True).stdout
        result = json.loads(output)
        self.assertEqual(result["all"], ["9", "8", "1", "2"])
        self.assertEqual(result["allCats"], [{"id": "zuivel_eieren", "count": 2}, {"id": "dranken", "count": 1}, {"id": "vlees", "count": 1}])
        self.assertEqual(result["box"], ["9", "8"])
        self.assertEqual(result["boxCats"], [{"id": "dranken", "count": 1}, {"id": "zuivel_eieren", "count": 1}], "op aantal, dan op naam")
        self.assertIn("offer-card__personal--on", result["badges"][0])
        self.assertIn("offers.personal.badge_on", result["badges"][0])
        self.assertIn("offer-card__personal--off", result["badges"][1])
        self.assertEqual(result["badges"][2:], ["", ""], "een gewone aanbieding heeft geen Bonus Box-label")
        self.assertEqual(result["otherStore"], ["9", "8", "1", "2"], "bij een winkel zonder Bonus Box doet de schakelaar niets")


if __name__ == "__main__":
    unittest.main()
