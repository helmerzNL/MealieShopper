"""Gevolgde producten (`watched_products`): zoeken of een link plakken, volgen, elke nacht bijwerken en wat er veranderde.

Geen test spreekt een echte winkel aan: `ah.get_products_by_ids`, `jumbo.get_product` en het zoeken per winkel zijn vervangen, en
de afbeeldingen worden niet opgehaald (`product_images._fetch_all`).
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from collections import deque
from contextlib import closing
from pathlib import Path
from typing import Any
from unittest import mock

from helpers import AppTestCase, db, run

from mealieshopper import ah, ah_orders, auth, backup, jumbo, offers_schedule, prices, product_images, watched_products
from mealieshopper.app import create_app

ROOT = Path(__file__).resolve().parent.parent


def ah_product(
    product_id: int, price: float, unit_size: str = "1 l", title: str = "AH Halfvolle melk", bonus: bool = False, before: float | None = None
) -> dict[str, Any]:
    """Een product zoals `ah.get_products_by_ids` het geeft."""
    return {
        "id": product_id,
        "url": ah.product_web_url(product_id),
        "title": title,
        "brand": "AH",
        "unitSize": unit_size,
        "price": price,
        "image": f"https://static.ah.nl/{product_id}.jpg",
        "isBonus": bonus,
        "priceBefore": before,
        "promoLabel": "2e halve prijs" if bonus else None,
    }


class WatchedCase(AppTestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"MEALIESHOPPER_DATA_DIR": tmp.name, "PASSKEY_AUTH_ENABLED": "false"})
        patcher.start()
        self.addCleanup(patcher.stop)
        run(auth.init_db())
        self.ah_answer: dict[int, dict[str, Any]] = {}
        self.ah_calls: list[list[int]] = []

        async def by_ids(ids: list[int]) -> list[dict[str, Any]]:
            self.ah_calls.append(list(ids))
            return [self.ah_answer[number] for number in ids if number in self.ah_answer]

        async def no_wait(seconds: float) -> None:
            return None

        async def no_images(store: str, wanted: list[Any]) -> tuple[list[Any], int]:
            return [], 0

        for patch in (
            mock.patch.object(ah, "get_products_by_ids", by_ids),
            mock.patch.object(ah_orders, "_wait", no_wait),
            mock.patch.dict(ah_orders._PACE, {"last": float("-inf"), "recent": deque()}),
            mock.patch.object(watched_products, "_pause", mock.AsyncMock()),
            mock.patch.object(product_images, "_fetch_all", no_images),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def follow(self, product_id: int = 4183, price: float = 1.09, **extra: Any) -> dict[str, Any]:
        self.ah_answer[product_id] = ah_product(product_id, price, **extra)
        with mock.patch.object(watched_products, "_now", return_value=self.day(1)):
            return run(watched_products.add("ah", str(product_id)))

    def day(self, number: int) -> str:
        return f"2026-11-{number:02d}T03:00:00+00:00"


class PackagingAndLinksTest(WatchedCase):
    def test_packaging_follows_from_the_name_and_the_size(self):
        cases = {
            ("Coca-Cola Zero", "6 x 330 ml"): "multipack",
            ("Hak Bruine bonen pot", "720 g"): "jar",
            ("AH Halfvolle melk", "1 l"): None,
            ("Heinz Tomato ketchup knijpfles", "500 ml"): "bottle",
            ("AH Bananen", "per stuk"): "piece",
            ("AH Tomaten tros", "500 g"): "bunch",
            ("Grolsch Pilsner blikjes", "330 ml"): "can",
            ("AH Kipfilet", "per kilo"): "weighed",
        }
        for (title, size), kind in cases.items():
            with self.subTest(title=title):
                self.assertEqual(watched_products.packaging_type(title, size), kind)

    def test_a_pasted_link_of_ah_or_jumbo_gives_the_product(self):
        cases = {
            "https://www.ah.nl/producten/product/wi4183/ah-halfvolle-melk": ("ah", "4183"),
            "https://ah.nl/producten/product/wi4183": ("ah", "4183"),
            " https://www.jumbo.com/producten/jumbo-halfvolle-melk-1l-123456PAK ": ("jumbo", "123456PAK"),
            "https://www.jumbo.com/producten/jumbo-pindakaas-67649pot?ref=x": ("jumbo", "67649POT"),
            "https://www.ah.nl/bonus": None,
            "https://picnic.app/nl/product/s1001": None,
            "halfvolle melk": None,
        }
        for link, expected in cases.items():
            with self.subTest(link=link):
                self.assertEqual(watched_products.parse_url(link), expected)

    def test_searching_with_a_link_fetches_that_product_at_the_store_of_the_link(self):
        self.ah_answer[4183] = ah_product(4183, 1.09)
        found = run(watched_products.search("jumbo", "https://www.ah.nl/producten/product/wi4183/ah-halfvolle-melk"))
        self.assertEqual(found["store"], "ah", "de link wint van de gekozen winkel")
        self.assertEqual([(p["productId"], p["price"], p["watched"]) for p in found["products"]], [("4183", 1.09, False)])
        self.assertEqual(self.ah_calls, [[4183]])

    def test_a_jumbo_link_asks_jumbo_for_that_article(self):
        answer = {"id": "123456PAK", "title": "Jumbo Halfvolle melk", "unitSize": "1 l", "price": 1.15, "isBonus": False, "image": None}
        with mock.patch.object(jumbo, "get_product", mock.AsyncMock(return_value=answer)) as get:
            found = run(watched_products.search("ah", "https://www.jumbo.com/producten/jumbo-halfvolle-melk-1l-123456PAK"))
        get.assert_awaited_once_with("123456PAK")
        self.assertEqual(found["products"][0]["store"], "jumbo")

    def test_an_unknown_link_says_what_works(self):
        with self.assertRaisesRegex(ValueError, "ah.nl of jumbo.com"):
            run(watched_products.search("ah", "https://www.example.com/melk"))

    def test_a_store_without_search_cannot_be_followed(self):
        with self.assertRaises(ValueError):
            run(watched_products.add("lidl", "123"))


class FollowTest(WatchedCase):
    def test_following_saves_the_first_point_and_the_price_history_gets_it_too(self):
        added = self.follow(4183, 1.09)
        self.assertEqual((added["title"], added["unitSize"], added["regular"]), ("AH Halfvolle melk", "1 l", 1.09))
        self.assertEqual(len(added["points"]), 1)
        self.assertEqual(run(watched_products.keys()), ["ah:4183"])
        with closing(db(prices)) as conn:
            rows = conn.execute("SELECT product_id, price FROM price_observations").fetchall()
        self.assertEqual([tuple(row) for row in rows], [("4183", 1.09)])

    def test_each_night_adds_a_point_and_the_changes_show_price_and_size(self):
        self.follow(4183, 1.09)
        steps = [(2, 1.09, "1 l", False, None), (3, 0.99, "1 l", True, 1.19), (4, 1.19, "1 l", False, None), (5, 1.19, "900 ml", False, None)]
        for number, price, size, bonus, before in steps:
            self.ah_answer[4183] = ah_product(4183, price, size, bonus=bonus, before=before)
            self.assertEqual(run(watched_products.refresh(now=self.day(number))), {"updated": 1, "missing": 0})
        product = run(watched_products.listing())["products"][0]
        self.assertEqual(len(product["points"]), 5)
        offer_day = product["points"][2]
        self.assertEqual(
            (offer_day["price"], offer_day["regular"], offer_day["onOffer"], offer_day["offerLabel"]), (0.99, 1.19, True, "2e halve prijs")
        )
        fields = [(change["field"], change["from"], change["to"]) for change in product["changes"]]
        self.assertEqual(fields, [("regular", 1.09, 1.19), ("unitSize", "1 l", "900 ml")], "de actieprijs is geen prijsverandering")
        self.assertEqual((product["change"], product["changePercent"]), (0.1, 9.2))
        self.assertEqual((product["lowest"], product["highest"]), (1.09, 1.19))

    def test_an_offer_without_the_price_before_does_not_count_as_a_regular_price(self):
        self.follow(4183, 1.09)
        self.ah_answer[4183] = ah_product(4183, 0.5, bonus=True, before=None)
        run(watched_products.refresh(now=self.day(2)))
        product = run(watched_products.listing())["products"][0]
        self.assertIsNone(product["points"][-1]["regular"])
        self.assertEqual(product["changes"], [])
        self.assertEqual(product["lastRegular"]["price"], 1.09)

    def test_a_product_the_store_no_longer_gives_is_marked_missing(self):
        self.follow(4183, 1.09)
        del self.ah_answer[4183]
        self.assertEqual(run(watched_products.refresh(now=self.day(2))), {"updated": 0, "missing": 1})
        self.assertEqual(run(watched_products.listing())["products"][0]["missingSince"], self.day(2))
        self.ah_answer[4183] = ah_product(4183, 1.09)
        run(watched_products.refresh(now=self.day(3)))
        self.assertIsNone(run(watched_products.listing())["products"][0]["missingSince"])

    def test_unfollowing_keeps_the_prices_for_when_you_follow_again(self):
        self.follow(4183, 1.09)
        self.assertTrue(run(watched_products.remove("ah", "4183")))
        self.assertEqual(run(watched_products.listing())["products"], [])
        with closing(db(prices)) as conn, conn:
            conn.execute("UPDATE watched_prices SET day = '2026-10-01'")
        again = self.follow(4183, 1.29)
        self.assertEqual([point["regular"] for point in again["points"]], [1.09, 1.29])

    def test_when_the_store_does_not_answer_the_page_knowledge_is_used(self):
        added = run(watched_products.add("ah", "999", {"title": "AH Pindakaas", "unitSize": "350 g", "price": 2.19}))
        self.assertEqual((added["title"], added["unitSize"], added["packaging"]), ("AH Pindakaas", "350 g", None))
        self.assertEqual(added["points"], [], "geen prijs van de winkel: de eerste komt 's nachts")

    def test_ah_is_asked_in_batches(self):
        for number in range(1, 36):
            self.follow(number, 1.0)
        self.ah_calls.clear()
        run(watched_products.refresh(now=self.day(2)))
        self.assertEqual([len(call) for call in self.ah_calls], [30, 5])

    def test_the_nightly_run_updates_the_followed_products(self):
        source = (ROOT / "mealieshopper" / "offers_schedule.py").read_text(encoding="utf-8")
        self.assertIn('("watchedProducts", watched_products.refresh_nightly)', source)
        self.assertIsNone(run(watched_products.refresh_nightly()), "niets te volgen: niets in de uitkomst")
        self.follow(4183, 1.09)
        self.assertEqual(run(watched_products.refresh_nightly()), {"updated": 1, "missing": 0})

    def test_a_failing_store_does_not_fail_the_night(self):
        self.follow(4183, 1.09)
        with mock.patch.object(ah, "get_products_by_ids", mock.AsyncMock(side_effect=RuntimeError("503"))):
            self.assertEqual(run(watched_products.refresh_nightly()), {"updated": 0, "missing": 1})

    def test_the_tables_are_in_the_backup(self):
        self.assertEqual(backup.PARTS["watchedProducts"], ("watched_products", "watched_prices"))
        self.assertIn(watched_products, backup.SCHEMA_OWNERS)
        _ = offers_schedule  # de nachtelijke run importeert de module


class ApiTest(WatchedCase):
    def setUp(self) -> None:
        super().setUp()
        self.client = self.client_for(create_app())

    async def test_follow_list_and_unfollow_through_the_api(self):
        self.ah_answer[4183] = ah_product(4183, 1.09)
        added = await self.client.post("/api/watched", json={"store": "ah", "productId": "4183"})
        self.assertEqual(added.status_code, 200, added.get_json())
        self.assertEqual((await self.client.get("/api/watched/keys")).get_json(), {"keys": ["ah:4183"]})
        listed = (await self.client.get("/api/watched")).get_json()
        self.assertEqual([p["productId"] for p in listed["products"]], ["4183"])
        removed = await self.client.delete("/api/watched/ah/4183")
        self.assertEqual(removed.get_json(), {"removed": True})
        bad = await self.client.post("/api/watched", json={"store": "aldi", "productId": "1"})
        self.assertEqual(bad.status_code, 400)


class PageTest(WatchedCase):
    def test_the_screen_sits_next_to_the_price_history_and_marks_followed_products(self):
        html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn('data-subtab="offers-watched"', html)
        self.assertIn('id="offers-watched"', html)
        self.assertLess(html.index("offers_history.js"), html.index("watched.js"))
        router = (ROOT / "static" / "router.js").read_text(encoding="utf-8")
        self.assertIn('watched: "offers-watched"', router)
        history = (ROOT / "static" / "offers_history.js").read_text(encoding="utf-8")
        self.assertIn("watchedBadge()", history)
        self.assertIn("data-history-watch", history)
        loose = (ROOT / "static" / "loose_names.js").read_text(encoding="utf-8")
        self.assertIn("data-loose-watch", loose)
        self.assertIn("watchedFollow(", loose)

    def test_every_packaging_kind_has_a_text(self):
        script = (ROOT / "static" / "watched.js").read_text(encoding="utf-8")
        for kind, _ in watched_products.PACKAGING_WORDS:
            self.assertRegex(script, rf'\b{kind}: "watched\.pack\.{kind}"')
        self.assertTrue(re.search(r"regular: \"watched.change.regular\"", script))


class FollowableIdTest(WatchedCase):
    def test_only_a_real_product_of_a_web_shop_can_be_followed(self):
        # Een folderactie voor een hele lijn (prijsarchief), de Bonus Box en een AH-id met letters zijn geen product dat de winkel
        # kent; wi4183 is hoe AH 4183 op de site schrijft (4.28.0).
        for store, product_id in (("ah", "folder:ah0001"), ("jumbo", "folder:jumbo0009"), ("ah", "bonusbox-12"), ("ah", "abc"), ("crisp", "x1")):
            with self.subTest(store=store, product_id=product_id), self.assertRaisesRegex(ValueError, "niet gevolgd"):
                run(watched_products.add(store, product_id, {"title": "Iets"}))
        self.ah_answer[4183] = ah_product(4183, 1.09)
        self.assertEqual(run(watched_products.add("ah", "wi4183"))["productId"], "4183")
        self.assertEqual(run(watched_products.keys()), ["ah:4183"])
        self.assertTrue(run(watched_products.remove("ah", "wi4183")))


@unittest.skipUnless(shutil.which("node"), "Node is niet geinstalleerd")
class OfferWatchButtonTest(unittest.TestCase):
    """De knop Prijs volgen op een aanbieding (offers.js), met de echte functies uit offers.js en watched.js in Node."""

    @staticmethod
    def source(script: str, name: str) -> str:
        match = re.search(rf"^(?:async )?function {re.escape(name)}\(.*?^}}$", script, re.DOTALL | re.MULTILINE)
        assert match, name
        return match.group(0)

    def render(self, store: str, offers: list[dict[str, Any]], watched_keys: list[str]) -> list[dict[str, str]]:
        offers_js = (ROOT / "static" / "offers.js").read_text(encoding="utf-8")
        watched_js = (ROOT / "static" / "watched.js").read_text(encoding="utf-8")
        functions = [self.source(offers_js, name) for name in ("offerCartId", "offerWatchButton")]
        functions += [self.source(watched_js, name) for name in ("watchedKey", "watchedIs", "watchedFollowableId", "watchedBadge")]
        script = f"""
const STORES = {{ ah: {{}}, jumbo: {{}}, picnic: {{}}, crisp: {{}} }};
const offersState = {{ store: {json.dumps(store)} }};
const watched = {{ keys: new Set({json.dumps(watched_keys)}) }};
const t = (key) => key;
const escapeHtml = (value) => String(value);
const mdiIcon = () => "<svg></svg>";
const MDI_EYE = "";
{chr(10).join(functions)}
const offers = {json.dumps(offers)};
console.log(JSON.stringify(offers.map((offer) => ({{ icon: offerWatchButton(offer), text: offerWatchButton(offer, {{ text: true }}) }}))));"""
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_the_button_is_on_real_products_and_says_when_it_is_followed(self):
        ah = self.render("ah", [{"id": "4183"}, {"id": "wi5000"}, {"id": "bonusbox-1"}, {"id": "folder:ah0001"}], ["ah:5000"])
        self.assertIn('data-offer-watch="4183" aria-pressed="false"', ah[0]["icon"])
        self.assertIn('aria-pressed="true"', ah[1]["icon"], "wi5000 is 5000, en dat volg je al")
        self.assertIn("is-watched", ah[1]["icon"])
        self.assertIn("watched.card.unfollow", ah[1]["text"])
        self.assertEqual((ah[2]["icon"], ah[3]["icon"], ah[3]["text"]), ("", "", ""), "Bonus Box en folderactie: geen knop")
        self.assertEqual(self.render("jumbo", [{"id": "folder:jumbo1"}, {"id": "123PAK"}], [])[0]["icon"], "")
        self.assertIn('data-offer-watch="123PAK"', self.render("jumbo", [{"id": "123PAK"}], [])[0]["icon"])
        self.assertEqual(self.render("lidl", [{"id": "123"}], [])[0]["icon"], "", "Lidl heeft geen webwinkel")

    def test_the_card_the_row_and_the_window_carry_the_button(self):
        offers_js = (ROOT / "static" / "offers.js").read_text(encoding="utf-8")
        self.assertIn("${offerWatchButton(offer)}${history}", self.source(offers_js, "offerCardFoot"))
        self.assertIn("${offerWatchButton(offer)}${chart}", self.source(offers_js, "offerProductRow"))
        self.assertIn('${preview ? "" : offerWatchButton(offer, { text: true })}', self.source(offers_js, "offerPopupHtml"))
        history_js = (ROOT / "static" / "offers_history.js").read_text(encoding="utf-8")
        self.assertIn("watchedFollowableId(product.store, product.productId)", self.source(history_js, "offerHistoryWatchHtml"))
