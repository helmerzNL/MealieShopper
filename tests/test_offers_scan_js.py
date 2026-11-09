"""De koopjes-scanner van de aanbiedingen (static/offers.js) draait hier echt, in Node: het sorteren, de badge, de maand in de tooltip
en wat de status telt. Zonder Node slaan de Node-tests over."""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OFFERS_JS = (ROOT / "static" / "offers.js").read_text(encoding="utf-8")
APP_JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
POPOVER_JS = (ROOT / "static" / "popover.js").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")


def popover_source(name):
    match = re.search(rf"^(?:const {re.escape(name)} = .*?;|function {re.escape(name)}\(.*?^}})$", POPOVER_JS, re.DOTALL | re.MULTILINE)
    if not match:
        raise AssertionError(f"{name} niet gevonden in popover.js")
    return match.group(0)


def function_source(name):
    match = re.search(
        rf"^(?:async )?function {re.escape(name)}\(.*?^}}$", OFFERS_JS, re.DOTALL | re.MULTILINE
    )
    if not match:
        raise AssertionError(f"functie {name} niet gevonden")
    return match.group(0)


def const_source(name):
    match = re.search(rf"^const {re.escape(name)} = .*?;$", OFFERS_JS, re.MULTILINE)
    if not match:
        raise AssertionError(f"constante {name} niet gevonden")
    return match.group(0)


PRELUDE = "\n".join(
    [const_source(name) for name in ("OFFER_UNIT_ORDER", "OFFER_LABEL_ORDER", "OFFERS_OFTEN_PER_YEAR", "offersDimmedIds", "OFFER_POPUP_WIDTH", "OFFER_POPUP_PURCHASES", "offerPopup")]
    + [
        function_source(name)
        for name in (
            "offerDiscountKey",
            "offerSortKey",
            "offersSorted",
            "offerBadge",
            "offerMonthText",
            "offerBestKey",
            "offerYearLowLine",
            "offerPriceLabel",
            "offerFamilies",
            "offerPackRows",
            "offerPackPricier",
            "offersUnitsInList",
            "offersUnitChoice",
            "offersCardCount",
            "offersFiltersOpen",
            "offerPackWarning",
            "offerPerUnitText",
            "offersFilterMatch",
            "offersEmptyFilters",
            "offersActiveFilterChips",
            "offersFilteredWithFamilies",
            "offersBrandCounts",
            "offersDimmed",
            "offersFilters",
            "offersLoadFilters",
            "offersUnitLabel",
            "offerPopupHtml",
            "offerAsProduct",
            "offerForPack",
            "offerPopupPacks",
            "loadOfferZoomHistory",
        )
    ]
    + [popover_source(name) for name in ("POPOVER_DELAY", "POPOVER_GRACE", "POPOVER_GAP", "POPOVER_EDGE", "POPOVER_SIDE_MIN", "offerPopupPlace")]
    + [
        "const offersState = { sort: 'store', store: 'ah', filters: null, filtersStore: 'ah' };",
        "const t = (key, params) => key + (params ? ' ' + JSON.stringify(params) : '');",
        "const escapeHtml = (value) => String(value);",
        "const offerBadgeTitle = () => 'uitleg';",
        "const monthShort = (date) => date.toLocaleDateString('nl-NL', { month: 'short' }).replace(/\\.$/, '');",
        "const formatPrice = (value) => 'EUR ' + Number(value).toFixed(2);",
        "const offerById = (id) => Array.isArray(offersState.offers) ? offersState.offers.find((o) => String(o.id) === String(id)) || null : { id: String(id) };",
        "const foldText = (value) => String(value).toLowerCase();",
        "const matchesWords = (text, query) => query.toLowerCase().split(/\\s+/).every((word) => text.includes(word));",
        "const I18N = { formatNumber: String };",
        "const offerHistoryHead = (product, options) => '<div class=\"ohc-head\" data-offer-price=\"' + (options && options.offer ? options.offer.price : '') + '\">' + product.title + '</div>';",
        "const offerHistoryStats = () => '<dl class=\"ohc-stats\"></dl>';",
        "const offerHistoryChartBlock = (product, options) => '<div class=\"ohc-block\" data-ranges=\"' + (options && options.ranges && options.ranges === offerPopup.ranges ? 'popup' : '') + '\"></div>';",
        "const offerHistoryPurchasesTable = (product) => (product.purchases || []).length ? '<table class=\"ohc-table\"></table>' : '';",
        "const priceHistoryPath = (store, title, id) => '/offers/history?product=' + id;",
        "const offerDateText = (day) => String(day);",
    ]
)


def offer(id, percent=None, source="history", unit=None, value=None, label=None):
    badge = None if percent is None else {"percent": percent, "source": source}
    per = None if unit is None else {"unit": unit, "value": value}
    return {
        "id": id,
        "badge": badge,
        "unitPricePer": per,
        "priceLabel": {"kind": label},
    }


OFFERS = [
    offer("a", 25, "store", "kg", 9.0, "lowest_6m"),
    offer("b", 40, "history", "l", 1.5, None),
    offer("c", unit="stuk", value=0.5, label="lowest_ever"),
    offer("d", 40, "store", None, None, "lowest_12m"),
    offer("e", 0, "history", "kg", 3.0, "lowest_seen"),
]


@unittest.skipUnless(shutil.which("node"), "Node is niet geïnstalleerd")
class OffersScanNodeTest(unittest.TestCase):
    def run_js(self, body, extra=()):
        # `extra`: functies uit offers.js die alleen deze test nodig heeft (met stubs voor `$` en de DOM in `body`).
        script = f"{PRELUDE}\n{chr(10).join(function_source(name) for name in extra)}\nconst OFFERS = {json.dumps(OFFERS)};\n{body}"
        result = subprocess.run(
            ["node", "-e", script],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def sorted_ids(self, sort):
        return self.run_js(
            f"offersState.sort = '{sort}'; console.log(JSON.stringify(offersSorted(OFFERS).map((o) => o.id)));"
        )

    def test_sorting_by_discount_puts_the_biggest_first_measured_before_claimed_and_unknown_last(
        self,
    ):
        self.assertEqual(self.sorted_ids("discount"), ["b", "d", "a", "e", "c"])

    def test_sorting_by_unit_price_goes_kilo_litre_piece_and_without_a_unit_price_last(
        self,
    ):
        self.assertEqual(self.sorted_ids("unit"), ["e", "a", "b", "c", "d"])

    def test_sorting_by_lowest_goes_down_the_ladder_and_then_by_discount(self):
        self.assertEqual(self.sorted_ids("lowest"), ["c", "d", "a", "e", "b"])

    def test_store_order_is_left_alone(self):
        self.assertEqual(self.sorted_ids("store"), ["a", "b", "c", "d", "e"])

    def render(self, name, offer):
        return self.run_js(f"console.log(JSON.stringify({name}({json.dumps(offer)})));")

    def test_best_first_puts_lowest_of_the_year_without_a_cheaper_pack_on_top(self):
        offers = [
            {"id": "a", "yearLow": {"status": "lowest"}, "badge": {"percent": 15}, "verdict": {"cheaperPack": None}, "unitPricePer": {"value": 9.0}},
            {"id": "b", "yearLow": {"status": "lowest"}, "badge": {"percent": 40}, "verdict": {"cheaperPack": {"productId": "x"}}},
            {"id": "c", "yearLow": {"status": "above", "above": 0.5}, "badge": {"percent": 30}, "verdict": {"cheaperPack": None}},
            {"id": "d", "yearLow": {"status": "lowest"}, "badge": {"percent": 15}, "verdict": {"cheaperPack": None}, "unitPricePer": {"value": 7.0}},
            {"id": "e", "yearLow": {"status": "unknown"}, "badge": None, "verdict": {"cheaperPack": None}},
        ]
        found = self.run_js(f"offersState.sort = 'best'; console.log(JSON.stringify(offersSorted({json.dumps(offers)}).map((o) => o.id)));")
        # b: laagste van het jaar, maar per eenheid duurder dan een andere verpakking: bij de rest, daar op korting.
        self.assertEqual(found, ["d", "a", "b", "c", "e"])

    def test_the_year_low_line_on_the_card(self):
        self.assertIn("offers.label.year_low", json.dumps(self.render("offerYearLowLine", {"yearLow": {"status": "lowest", "month": "2026-09"}})))
        above = self.render("offerYearLowLine", {"yearLow": {"status": "above", "above": 0.65, "month": "2026-09", "price": 3.0}})
        self.assertIn("offers.label.year_above", above["text"])
        self.assertIn("EUR 0.65", above["text"])
        self.assertIsNone(self.render("offerYearLowLine", {"yearLow": {"status": "unknown"}}))
        self.assertIsNone(self.render("offerYearLowLine", {}))

    FAMILY = {
        "key": "ah|rundergehakt|ah",
        "unknownPacks": False,
        "packs": [
            {"productId": "300", "unitSize": "300 g", "price": 2.49, "unitPrice": {"value": 8.3, "unit": "kg"}, "onOffer": True, "cheapest": False},
            {"productId": "500", "unitSize": "500 g", "price": 4.19, "unitPrice": {"value": 8.38, "unit": "kg"}, "onOffer": True, "cheapest": False},
            {"productId": "1000", "unitSize": "1000 g", "price": 7.49, "unitPrice": {"value": 7.49, "unit": "kg"}, "onOffer": False, "cheapest": True},
        ],
    }
    FAMILY_OFFERS = [
        {"id": "300", "title": "AH Rundergehakt 300 g", "family": FAMILY, "yearLow": {"status": "lowest"}, "badge": {"percent": 30}, "verdict": {"cheaperPack": {"productId": "1000", "title": "AH Rundergehakt", "unitSize": "1000 g", "unitPrice": {"value": 7.49, "unit": "kg"}}}},
        {"id": "x", "title": "AH Pindakaas", "family": {"key": "ah|pindakaas|ah", "packs": [], "unknownPacks": False}, "yearLow": {"status": "lowest"}, "badge": {"percent": 10}, "verdict": {"cheaperPack": None}},
        {"id": "500", "title": "AH Rundergehakt 500 g", "family": FAMILY, "yearLow": {"status": "above", "above": 0.2}, "badge": {"percent": 20}, "verdict": {"cheaperPack": None}},
    ]

    def families(self, offers, sort="best"):
        return self.run_js(f"offersState.sort = '{sort}'; console.log(JSON.stringify(offerFamilies(offersSorted({json.dumps(offers)}))));")

    def test_families_group_the_offers_and_take_the_place_of_their_best_offer(self):
        groups = self.families(self.FAMILY_OFFERS)
        # De familie staat op de plek van de 500 g: de 300 g is laagste van het jaar maar per kilo duurder dan de 1000 g, dus niet beste.
        self.assertEqual([group["head"]["id"] for group in groups], ["x", "500"])
        self.assertEqual([offer["id"] for offer in groups[1]["offers"]], ["500", "300"])
        self.assertEqual([group["head"]["id"] for group in self.families(self.FAMILY_OFFERS, "store")], ["300", "x"], "in winkelvolgorde staat de familie bij haar eerste aanbieding")

    def test_the_pack_rows_list_every_pack_and_mark_the_cheapest(self):
        html = self.run_js(f"console.log(JSON.stringify(offerPackRows({json.dumps(self.FAMILY_OFFERS[0])})));")
        self.assertEqual(html.count('<li class="offer-pack'), 3)
        self.assertEqual(html.count("offer-pack--cheapest"), 1)
        self.assertEqual(html.count("offer-pack--offer"), 2)
        self.assertIn("offers.pack.pricier_than", html)  # op de regel van de 300 g en de 500 g
        self.assertIn('data-zoom="1000"', html)
        self.assertIn("EUR 7.49", html)

    def test_a_dimmed_pack_row_is_marked(self):
        family = {**self.FAMILY, "packs": [{**self.FAMILY["packs"][0], "productId": "dim"}, self.FAMILY["packs"][2]]}
        html = self.run_js(f"offersDimmedIds.add('dim'); console.log(JSON.stringify(offerPackRows({json.dumps({'id': 'dim', 'family': family})})));")
        self.assertEqual(html.count("offer-pack--dimmed"), 1)

    def test_a_single_pack_has_no_rows_and_unknown_packs_get_a_note(self):
        self.assertEqual(self.run_js(f"console.log(JSON.stringify(offerPackRows({json.dumps(self.FAMILY_OFFERS[1])})));"), "")
        lonely = {"id": "300", "family": {**self.FAMILY, "packs": self.FAMILY["packs"][:1], "unknownPacks": True}}
        self.assertIn("offers.pack.unknown", self.run_js(f"console.log(JSON.stringify(offerPackRows({json.dumps(lonely)})));"))

    def test_the_pack_warning_names_the_cheaper_pack(self):
        self.assertIn("1000 g", self.run_js(f"console.log(JSON.stringify(offerPackWarning({json.dumps(self.FAMILY_OFFERS[0])})));"))
        self.assertEqual(self.run_js(f"console.log(JSON.stringify(offerPackWarning({json.dumps(self.FAMILY_OFFERS[2])})));"), "")

    FILTER_OFFER = {"id": "1", "brand": "Dr. Oetker", "title": "Pizza", "bought": True, "badge": {"percent": 25}, "unitPricePer": {"value": 9.36, "unit": "kg"},
                    "yearLow": {"status": "lowest"}, "verdict": {"timesThisYear": 3}, "personal": False, "family": {"key": "f1", "packs": []}}
    EMPTY_FILTERS = {"brands": [], "bought": False, "minDiscount": 0, "maxUnitPrice": {"value": None, "unit": "kg"}, "yearLow": False, "rare": False}

    def match(self, offer, filters):
        return self.run_js(f"console.log(JSON.stringify(offersFilterMatch({json.dumps(offer)}, {json.dumps(filters)})));")

    def test_filters_stack(self):
        offer, empty = self.FILTER_OFFER, self.EMPTY_FILTERS
        self.assertTrue(self.match(offer, empty))
        self.assertTrue(self.match(offer, {**empty, "brands": ["Dr. Oetker"], "bought": True, "minDiscount": 25, "yearLow": True, "rare": True}))
        self.assertFalse(self.match(offer, {**empty, "brands": ["Unox"]}))
        self.assertFalse(self.match(offer, {**empty, "minDiscount": 30}))
        self.assertFalse(self.match(offer, {**empty, "maxUnitPrice": {"value": 9.0, "unit": "kg"}}))
        self.assertTrue(self.match(offer, {**empty, "maxUnitPrice": {"value": 9.0, "unit": "l"}}), "een grens in een andere eenheid raakt dit product niet")
        self.assertFalse(self.match({**offer, "unitPricePer": None}, {**empty, "maxUnitPrice": {"value": 9.0, "unit": "kg"}}))
        self.assertFalse(self.match({**offer, "badge": None}, {**empty, "minDiscount": 5}))
        self.assertFalse(self.match({**offer, "verdict": {"timesThisYear": 6}}, {**empty, "rare": True}))
        self.assertFalse(self.match({**offer, "bought": False}, {**empty, "bought": True}))
        self.assertFalse(self.match({**offer, "yearLow": {"status": "above"}}, {**empty, "yearLow": True}))
        self.assertEqual(self.run_js("console.log(JSON.stringify(offersEmptyFilters()));"), empty)

    def test_a_brand_filter_that_matches_nothing_stays_active(self):
        chips = self.run_js(f"offersState.filters = {json.dumps({**self.EMPTY_FILTERS, 'brands': ['Verdwenen'], 'minDiscount': 25})}; console.log(JSON.stringify(offersActiveFilterChips()));")
        self.assertEqual([chip["key"] for chip in chips], ["brand:Verdwenen", "minDiscount"])
        self.assertIn("Verdwenen", chips[0]["label"])

    def test_the_family_stays_when_one_pack_matches_and_the_other_is_dimmed(self):
        fam = {"key": "ah|rundergehakt|ah", "packs": []}
        offers = [{**self.FILTER_OFFER, "id": "a", "family": fam, "badge": {"percent": 30}}, {**self.FILTER_OFFER, "id": "b", "family": fam, "badge": {"percent": 5}},
                  {**self.FILTER_OFFER, "id": "c", "badge": {"percent": 5}}]
        found = self.run_js(f"offersState.filters = {json.dumps({**self.EMPTY_FILTERS, 'minDiscount': 25})}; const shown = offersFilteredWithFamilies({json.dumps(offers)}); console.log(JSON.stringify({{ ids: shown.map((o) => o.id), dimmed: shown.filter((o) => offersDimmed(o)).map((o) => o.id) }}));")
        self.assertEqual(found, {"ids": ["a", "b"], "dimmed": ["b"]})

    def test_a_dimmed_offer_does_not_head_the_card(self):
        fam = {"key": "ah|rundergehakt|ah", "packs": []}
        offers = [{"id": "b", "family": fam, "verdict": {}}, {"id": "a", "family": fam, "verdict": {}}]
        found = self.run_js(f"offersState.sort = 'best'; offersDimmedIds.add('b'); console.log(JSON.stringify(offerFamilies({json.dumps(offers)}).map((g) => g.head.id)));")
        self.assertEqual(found, ["a"])

    def test_bought_counts_for_the_whole_family(self):
        offer = {**self.FILTER_OFFER, "bought": False, "family": {"key": "f", "packs": [], "bought": True}}
        self.assertTrue(self.match(offer, {**self.EMPTY_FILTERS, "bought": True}))

    def test_filters_follow_the_store(self):
        found = self.run_js("offersState.store = 'ah'; offersFilters().brands.push('Unox'); offersState.store = 'jumbo'; const jumbo = offersFilters().brands.slice(); offersState.store = 'ah'; console.log(JSON.stringify({ jumbo, ah: offersFilters().brands }));")
        self.assertEqual(found, {"jumbo": [], "ah": []})

    def test_a_failed_history_fetch_is_not_cached_and_shows_a_line(self):
        found = self.run_js(
            "const jsonFetch = () => Promise.reject(new Error('down')); const offerPopupProducts = new Map(); const offerZoomCache = new Map();"
            f"loadOfferZoomHistory('ah', '1').then((r) => console.log(JSON.stringify({{ result: r, cached: offerZoomCache.has('ah|1'), html: offerPopupHtml({json.dumps(self.POPUP_OFFER)}, r, []) }})));"
        )
        self.assertEqual((found["result"], found["cached"]), (False, False))
        self.assertIn("offers.popup.none", found["html"])
        self.assertNotIn("offers.popup.loading", found["html"])

    def test_the_popup_keeps_its_pack_while_the_mouse_crosses_the_card(self):
        self.assertIn("clearTimeout(offerPopup.timer);", OFFERS_JS.split('$("#offer-popup").addEventListener("mouseenter"')[1].split("});")[0])

    def test_a_tap_opens_the_window_whenever_the_hover_popup_is_off(self):
        self.assertIn("if (!thumb || offerPopupAllowed()) return;", OFFERS_JS)

    def test_brand_counts_ignore_the_brand_filter_itself(self):
        offers = [{**self.FILTER_OFFER, "id": "a"}, {**self.FILTER_OFFER, "id": "b", "brand": "Unox", "badge": {"percent": 5}}, {**self.FILTER_OFFER, "id": "c", "brand": "Unox"}]
        found = self.run_js(f"offersState.filters = {json.dumps({**self.EMPTY_FILTERS, 'brands': ['Unox'], 'minDiscount': 25})}; console.log(JSON.stringify(offersBrandCounts({json.dumps(offers)})));")
        self.assertEqual(found, [{"brand": "Dr. Oetker", "count": 1}, {"brand": "Unox", "count": 1}])

    POPUP_OFFER = {"id": "514010", "title": "Dr. Oetker Casa di mama pizza salami", "brand": "Dr. Oetker", "unitSize": "390 g", "image": "x.png", "url": "https://ah.nl/x", "price": 3.65}
    POPUP_PRODUCT = {"store": "ah", "productId": "514010", "title": "Dr. Oetker Casa di mama pizza salami", "purchases": [{"date": "2026-09-01", "price": 2.15}], "episodes": []}

    def place(self, anchor, size, viewport):
        return self.run_js(f"console.log(JSON.stringify(offerPopupPlace({json.dumps(anchor)}, {json.dumps(size)}, {json.dumps(viewport)})));")

    def popup(self, offer, product, packs, active=None):
        return self.run_js(f"console.log(JSON.stringify(offerPopupHtml({json.dumps(offer)}, {json.dumps(product)}, {json.dumps(packs)}, {json.dumps(active)})));")

    def test_popup_placement_prefers_right_then_left_then_below_then_above(self):
        size = {"width": 640, "height": 500}
        self.assertEqual(self.place({"left": 100, "right": 400, "top": 100, "bottom": 400}, size, {"width": 1280, "height": 900}), {"left": 408, "top": 100, "width": 640})
        self.assertEqual(self.place({"left": 800, "right": 1100, "top": 100, "bottom": 400}, size, {"width": 1280, "height": 900})["left"], 152)
        self.assertEqual(self.place({"left": 300, "right": 600, "top": 100, "bottom": 300}, size, {"width": 900, "height": 900})["top"], 308)
        self.assertEqual(self.place({"left": 300, "right": 600, "top": 700, "bottom": 880}, size, {"width": 900, "height": 900})["top"], 192)
        cramped = self.place({"left": 300, "right": 600, "top": 20, "bottom": 880}, size, {"width": 900, "height": 600})
        self.assertGreaterEqual(cramped["top"], 16)
        self.assertLessEqual(cramped["top"] + 500, 600 - 16)
        # Naast de kaart blijft de popup binnen de onderrand: hij schuift omhoog in plaats van eraf.
        self.assertEqual(self.place({"left": 100, "right": 400, "top": 600, "bottom": 800}, size, {"width": 1280, "height": 900})["top"], 384)

    def test_popup_beside_a_card_shrinks_to_the_room_there_down_to_a_minimum(self):
        # Een kaart in het midden van een scherm van 1280 px: rechts is 481 px over, dus de popup wordt zo breed en staat ernaast
        # in plaats van over de kaart heen. Onder 420 px gaat hij alsnog eronder, op volle breedte.
        size = {"width": 640, "height": 500}
        self.assertEqual(self.place({"left": 561, "right": 775, "top": 443, "bottom": 899}, size, {"width": 1280, "height": 900}), {"left": 783, "top": 384, "width": 481})
        self.assertEqual(self.place({"left": 400, "right": 460, "top": 100, "bottom": 300}, size, {"width": 1100, "height": 900}), {"left": 468, "top": 100, "width": 616})
        self.assertEqual(self.place({"left": 300, "right": 600, "top": 100, "bottom": 300}, size, {"width": 900, "height": 900}), {"left": 244, "top": 308, "width": 640})

    def test_popup_html_has_head_stats_chart_purchases_and_the_link(self):
        html = self.popup(self.POPUP_OFFER, self.POPUP_PRODUCT, [])
        for marker in ('class="ohc-head"', 'class="ohc-stats"', 'class="ohc-block"', 'class="ohc-table"', "offers.popup.full_history", "/offers/history?product=514010"):
            self.assertIn(marker, html)
        self.assertNotIn("offer-popup__packs", html)

    def test_popup_without_history_shows_the_head_and_a_loading_line(self):
        html = self.popup(self.POPUP_OFFER, None, [])
        self.assertIn('class="ohc-head"', html)
        self.assertIn("offers.popup.loading", html)
        self.assertNotIn("ohc-block", html)

    def test_popup_of_a_family_has_pack_buttons_with_the_shown_pack_pressed(self):
        packs = [{"productId": "300", "unitSize": "300 g"}, {"productId": "514010", "unitSize": "390 g"}, {"productId": "1000", "unitSize": "1000 g"}]
        html = self.popup(self.POPUP_OFFER, self.POPUP_PRODUCT, packs, "514010")
        self.assertEqual(html.count("data-popup-pack="), 3)
        self.assertIn('data-popup-pack="514010" aria-pressed="true"', html)

    def pack(self, pid, size, price, per_kg, on_offer, cheapest=False):
        return {"productId": pid, "unitSize": size, "price": price, "unitPrice": {"value": per_kg, "unit": "kg"}, "onOffer": on_offer, "cheapest": cheapest}

    def test_a_pack_within_the_margin_is_not_pricier(self):
        # 500 g 4,19 (8,38/kg) naast 1000 g 8,30 (8,30/kg): binnen twee procent, dus geen "duurder". 300 g 3,67/kg naast 500 g 3,04/kg wel.
        within = {"id": "a", "family": {"key": "f", "packs": [self.pack("500", "500 g", 4.19, 8.38, True), self.pack("1000", "1000 g", 8.30, 8.30, True, cheapest=True)]}}
        html = self.run_js(f"offersState.packMargin = 0.02; console.log(JSON.stringify(offerPackRows({json.dumps(within)})));")
        self.assertNotIn("offers.pack.pricier_than", html)
        beyond = {"id": "b", "family": {"key": "g", "packs": [self.pack("300", "300 g", 1.10, 3.67, True), self.pack("500", "500 g", 1.52, 3.04, False, cheapest=True)]}}
        html = self.run_js(f"offersState.packMargin = 0.02; console.log(JSON.stringify(offerPackRows({json.dumps(beyond)})));")
        self.assertEqual(html.count("offers.pack.pricier_than"), 1)

    def test_without_pack_margin_the_client_uses_two_percent(self):
        within = {"id": "a", "family": {"key": "f", "packs": [self.pack("500", "500 g", 4.19, 8.38, True), self.pack("1000", "1000 g", 8.30, 8.30, True, cheapest=True)]}}
        html = self.run_js(f"delete offersState.packMargin; console.log(JSON.stringify(offerPackRows({json.dumps(within)})));")
        self.assertNotIn("offers.pack.pricier_than", html)

    def test_pack_buttons_show_the_price_when_sizes_repeat(self):
        packs = [{"productId": "1", "unitSize": "3 stuks", "price": 14.52}, {"productId": "2", "unitSize": "3 stuks", "price": 26.16}]
        html = self.popup(self.POPUP_OFFER, self.POPUP_PRODUCT, packs, "1")
        self.assertIn(">3 stuks · EUR 14.52<", html)
        self.assertIn("3 stuks · EUR 26.16", html)
        plain = self.popup(self.POPUP_OFFER, self.POPUP_PRODUCT, [{"productId": "1", "unitSize": "300 g", "price": 2.49}, {"productId": "2", "unitSize": "500 g", "price": 4.19}], "1")
        self.assertNotIn("EUR", plain)

    def test_the_popup_passes_the_offer_and_its_own_ranges(self):
        html = self.popup(self.POPUP_OFFER, self.POPUP_PRODUCT, [])
        self.assertIn('data-offer-price="3.65"', html, "de kop krijgt de aanbieding voor de prijsregel")
        self.assertIn('data-ranges="popup"', html, "de grafiek leest de periodekeuze van de popup, niet van de pagina")
        self.assertIn("offerPopup.ranges = {}", function_source("showOfferPopup"), "een nieuwe popup begint zonder periodekeuze")

    def test_the_unit_choice_only_lists_units_in_the_offers(self):
        offers = [{"unitPricePer": {"unit": "stuk", "value": 1}}, {"unitPricePer": {"unit": "kg", "value": 2}}, {"unitPricePer": None}]
        self.assertEqual(self.run_js(f"console.log(JSON.stringify(offersUnitsInList({json.dumps(offers)})));"), ["kg", "stuk"])

    def test_an_unknown_remembered_unit_falls_back_to_the_first(self):
        self.assertEqual(self.run_js("console.log(JSON.stringify([offersUnitChoice({ maxUnitPrice: { unit: 'l' } }, ['kg', 'stuk']), offersUnitChoice({ maxUnitPrice: { unit: 'stuk' } }, ['kg', 'stuk']), offersUnitChoice({ maxUnitPrice: { unit: 'l' } }, [])]));"), ["kg", "stuk", "kg"])

    def test_the_counter_counts_cards_after_switches(self):
        fam = {"key": "f", "packs": []}
        offers = [{"id": "a", "family": fam, "verdict": {}}, {"id": "b", "family": fam, "verdict": {}}, {"id": "c", "family": {"key": "g", "packs": []}, "verdict": {}}]
        self.assertEqual(self.run_js(f"console.log(offersCardCount({json.dumps(offers)}));"), 2)
        body = function_source("renderOffersStatusGood")
        self.assertIn("offersCardCount(offersFiltered({ search: false }))", body)
        self.assertIn("offersCardCount(offersState.offers)", body)

    # Een minimale DOM voor de popup, het tikvenster en de lade: net genoeg voor de functies die de listeners aanroepen.
    POPUP_DOM = """
        const element = (name) => ({ name, hidden: false, innerHTML: "", dataset: {}, attrs: {}, children: new Set(), focused: 0, nextElementSibling: null,
          contains(other) { return other === this || this.children.has(other); }, focus() { this.focused += 1; document.activeElement = this; },
          querySelectorAll() { return Array.from(this.children); },
          setAttribute(key, value) { this.attrs[key] = value; }, classList: { names: new Set(), toggle(name, on) { on ? this.names.add(name) : this.names.delete(name); }, contains(name) { return this.names.has(name); } } });
        const box = element("popup"); box.hidden = true;
        const dialog = Object.assign(element("dialog"), { open: false, showModal() { this.open = true; }, close() { this.open = false; } });
        const dialogBody = element("dialogBody");
        const aside = element("aside"), backdrop = element("backdrop"), toggle = element("toggle"), closeButton = element("close");
        aside.children.add(closeButton);
        const body = element("body");
        const document = { body, activeElement: body };
        const window = { innerWidth: 1280, innerHeight: 900, matchMedia: () => ({ matches: window.innerWidth < 1100 }) };
        const $ = (selector) => ({ "#offer-popup": box, "#offer-zoom-dialog": dialog, "#offer-zoom-dialog-body": dialogBody, "#offers-filters": aside,
          "#offers-filters-backdrop": backdrop, "#offers-filters-toggle": toggle, "#offers-filters-close": closeButton })[selector];
        function placeOfferPopup() {}
        const offerZoomCache = new Map(); const offerPopupProducts = new Map();
        const anchor = element("card"); anchor.dataset.offer = "514010";
    """
    POPUP_FUNCTIONS = ("openOfferZoomDialog", "showOfferPopup", "scheduleOfferPopup", "hideOfferPopup", "delayHideOfferPopup", "hideOfferPopupFromKeyboard", "offerPopupTabTarget", "offerFocusables", "offerPopupKeepsFocus", "offerPopupScrolled")

    def popup_js(self, body):
        return self.run_js(f"{self.POPUP_DOM}\nofferPopup.ranges = {{}};\nofferPopup.anchor = null;\nofferPopup.id = '';\n{body}", extra=self.POPUP_FUNCTIONS)

    def test_the_window_starts_a_new_offer_without_a_period_choice_and_keeps_it_per_pack(self):
        found = self.popup_js(
            f"const jsonFetch = () => new Promise(() => {{}}); offersState.offers = [{json.dumps(self.POPUP_OFFER)}, {{ ...{json.dumps(self.POPUP_OFFER)}, id: '777' }}];"
            "const states = [];"
            "offerPopup.ranges = { stale: '1y' }; openOfferZoomDialog(offerById('514010')); states.push({ ...offerPopup.ranges });"
            "offerPopup.ranges.chosen = '6m'; openOfferZoomDialog(offerById('514010'), '1000'); states.push({ ...offerPopup.ranges });"
            "openOfferZoomDialog(offerById('777')); states.push({ ...offerPopup.ranges, open: dialog.open });"
            "console.log(JSON.stringify(states));"
        )
        self.assertEqual(found, [{}, {"chosen": "6m"}, {"open": True}])

    def test_a_closing_drawer_gives_the_focus_back_to_the_filters_button(self):
        found = self.popup_js(
            "const states = [];"
            "window.innerWidth = 375; offersFiltersOpen(true); document.activeElement = closeButton; offersFiltersOpen(false);"
            "states.push({ focused: toggle.focused, inert: aside.inert, hidden: aside.attrs['aria-hidden'] });"
            "document.activeElement = body; offersFiltersOpen(false); states.push({ focused: toggle.focused });"
            "window.innerWidth = 1280; offersFiltersOpen(false); states.push({ focused: toggle.focused, inert: aside.inert, hidden: aside.attrs['aria-hidden'] });"
            "console.log(JSON.stringify(states));"
        )
        self.assertEqual(found, [{"focused": 1, "inert": True, "hidden": "true"}, {"focused": 1}, {"focused": 1, "inert": False, "hidden": "false"}])

    def test_the_popup_keeps_the_focus_within_the_card_and_itself(self):
        found = self.popup_js(
            "const inside = element('link'); box.children.add(inside); const elsewhere = element('other');"
            "console.log(JSON.stringify([offerPopupKeepsFocus(anchor, inside), offerPopupKeepsFocus(anchor, anchor), offerPopupKeepsFocus(anchor, elsewhere), offerPopupKeepsFocus(anchor, null)]));"
        )
        self.assertEqual(found, [True, True, False, False])
        for listener in ('$("#offers-products").addEventListener("focusout"', '$("#offer-popup").addEventListener("focusout"', '$("#offers-products").addEventListener("mouseout"'):
            self.assertIn("offerPopupKeepsFocus(", OFFERS_JS.split(listener)[1].split("});")[0], listener)

    def test_tab_goes_from_the_card_into_the_popup_and_shift_tab_back(self):
        found = self.popup_js(
            "const jsonFetch = () => new Promise(() => {}); offersState.offers = [" + json.dumps(self.POPUP_OFFER) + "];"
            "showOfferPopup(anchor, offerById('514010'));"
            "const first = element('first'), last = element('last'); box.children.add(first); box.children.add(last); anchor.nextElementSibling = element('nextcard');"
            "const tab = (target, shiftKey) => { const found = offerPopupTabTarget({ key: 'Tab', shiftKey, target }, box); return found ? found.name : null; };"
            # Een kale kaart: Tab vanaf de kaart de popup in, Shift+Tab vanaf de popup of zijn eerste link terug, Tab voorbij de laatste link naar de volgende kaart.
            "const states = [tab(anchor, false), tab(box, true), tab(first, true), tab(last, false), tab(first, false), tab(anchor, true), tab(element('other'), false), offerPopupTabTarget({ key: 'a', shiftKey: false, target: anchor }, box) ? 'x' : null];"
            # Een kaart met eigen knoppen (de link naar het product, Vergelijken): die blijven in de tabvolgorde, de popup komt na de laatste.
            "const cardLink = element('cardlink'), compare = element('compare'); anchor.children.add(cardLink); anchor.children.add(compare);"
            "states.push(tab(anchor, false), tab(cardLink, false), tab(compare, false), tab(box, true), tab(first, true));"
            "anchor.nextElementSibling = null; states.push(tab(last, false)); box.hidden = true; states.push(tab(compare, false));"
            "console.log(JSON.stringify(states));"
        )
        self.assertEqual(found, ["popup", "card", "card", "nextcard", None, None, None, None, None, None, "popup", "compare", "compare", None, None])
        self.assertIn('id="offer-popup" class="offer-popup" tabindex="-1"', INDEX_HTML, "de popup zelf is te focussen")

    def test_the_scroll_of_a_focus_does_not_cancel_the_waiting_popup(self):
        # Tab naar een kaart onder de vouw scrolt die in beeld: die scroll mag de popup die nog wacht niet annuleren. Scrollen met
        # een open popup, of met een popup die na een muisbeweging wacht, haalt hem wel weg.
        found = self.popup_js(
            "const jsonFetch = () => new Promise(() => {}); offersState.offers = [" + json.dumps(self.POPUP_OFFER) + "];"
            "const states = [];"
            "scheduleOfferPopup(anchor, null, { byKeyboard: true }); offerPopupScrolled(box, false); states.push({ waiting: offerPopup.timer !== 0 });"
            "offerPopupScrolled(box, true); states.push({ waiting: offerPopup.timer !== 0 });"
            "clearTimeout(offerPopup.timer); offerPopup.timer = 0; scheduleOfferPopup(anchor); offerPopupScrolled(box, false); states.push({ waiting: offerPopup.timer !== 0 });"
            "showOfferPopup(anchor, offerById('514010')); offerPopupScrolled(box, true); states.push({ hidden: box.hidden });"
            "offerPopupScrolled(box, false); states.push({ hidden: box.hidden });"
            "console.log(JSON.stringify(states));"
        )
        self.assertEqual(found, [{"waiting": True}, {"waiting": True}, {"waiting": False}, {"hidden": False}, {"hidden": True}])
        self.assertIn("{ byKeyboard: true }", OFFERS_JS.split('$("#offers-products").addEventListener("focusin"')[1].split("\n});")[0])

    def test_keyboard_opens_the_popup_a_failed_load_shows_a_line_and_escape_closes_it(self):
        # Review Focus 2: focus op een kaart opent de popup na de wachttijd, de geschiedenis mislukt (de kop blijft, met een regel
        # erbij), Escape sluit en zet de focus terug op de kaart zonder de popup opnieuw te openen.
        found = self.popup_js(
            "const jsonFetch = () => Promise.reject(new Error('down')); offersState.offers = [" + json.dumps(self.POPUP_OFFER) + "];"
            "const states = []; scheduleOfferPopup(anchor, null, { byKeyboard: true }); states.push({ hidden: box.hidden, timer: offerPopup.timer !== 0 });"
            "setTimeout(() => {"
            "  states.push({ hidden: box.hidden, none: box.innerHTML.includes('offers.popup.none'), loading: box.innerHTML.includes('offers.popup.loading'), head: box.innerHTML.includes('ohc-head') });"
            "  document.activeElement = box; hideOfferPopupFromKeyboard();"
            "  states.push({ hidden: box.hidden, focused: anchor.focused, quiet: offerPopup.quiet === anchor });"
            "  console.log(JSON.stringify(states));"
            "}, POPOVER_DELAY + 50);"
        )
        self.assertEqual(found, [
            {"hidden": True, "timer": True},
            {"hidden": False, "none": True, "loading": False, "head": True},
            {"hidden": True, "focused": 1, "quiet": True},
        ])
        focusin = OFFERS_JS.split('$("#offers-products").addEventListener("focusin"')[1].split("\n});")[0]
        self.assertIn("offerPopup.quiet", focusin, "de focus die Escape terugzet opent de popup niet opnieuw")

    def test_a_pack_without_its_own_offer_gets_no_deal_in_the_popup(self):
        # De 1000 g tegen gewone prijs naast de 500 g in de aanbieding: geen 15% korting of Bonus Box van de 500 g op haar regel.
        offer = {**self.POPUP_OFFER, "priceBefore": 4.29, "promoLabel": "15% korting", "personal": True,
                 "family": {"key": "f", "packs": [{"productId": "514010", "unitSize": "390 g", "price": 3.65}, {"productId": "1000", "unitSize": "1000 g", "price": 8.30}]}}
        found = self.run_js(f"offersState.offers = []; console.log(JSON.stringify(offerForPack({json.dumps(offer)}, '1000')));")
        self.assertEqual((found["id"], found["price"], found["priceBefore"], found["promoLabel"], found["personal"]), ("1000", 8.30, None, "", False))

    def test_the_unit_choice_keeps_a_remembered_unit_with_a_limit(self):
        self.assertEqual(self.run_js("console.log(JSON.stringify([offersUnitChoice({ maxUnitPrice: { unit: 'l', value: 5 } }, ['kg', 'stuk']), offersUnitChoice({ maxUnitPrice: { unit: 'l', value: null } }, ['kg', 'stuk'])]));"), ["l", "kg"])
        self.assertNotIn("filters.maxUnitPrice.unit = ", function_source("renderOffersFilters"), "de keuzelijst verandert de bewaarde eenheid niet")

    def test_an_offer_becomes_a_product_for_the_head(self):
        product = self.run_js(f"console.log(JSON.stringify(offerAsProduct('ah', {json.dumps(self.POPUP_OFFER)})));")
        self.assertEqual((product["store"], product["productId"], product["title"], product["onOfferNow"]), ("ah", "514010", "Dr. Oetker Casa di mama pizza salami", True))

    def test_lower_before_disappears_when_year_low_is_known(self):
        label = {"kind": "lower_before", "price": 1.99, "month": "2010-03", "coveredSince": "2009-01-01"}
        self.assertIsNone(self.render("offerPriceLabel", {"priceLabel": label, "yearLow": {"status": "above", "above": 1.0}}))
        self.assertIn("lower_before", json.dumps(self.render("offerPriceLabel", {"priceLabel": label, "yearLow": {"status": "unknown"}})))
        self.assertIn("lower_before", json.dumps(self.render("offerPriceLabel", {"priceLabel": label})))

    def test_the_badge_takes_the_step_of_the_userscript_and_is_outlined_for_a_store_claim(
        self,
    ):
        badges = self.run_js(
            "const steps = [55, 50, 35, 20, 10, 1, 0].map((p) => offerBadge({ badge: { percent: p, source: 'history' } }));"
            "steps.push(offerBadge({ badge: { percent: 12, source: 'store' } }), offerBadge({}), offerBadge({ badge: { percent: 'x' } }));"
            "console.log(JSON.stringify(steps));"
        )
        for html, step in zip(badges[:7], ("50", "50", "35", "20", "10", "1", "0")):
            self.assertIn(f'class="offer-badge offer-badge--{step}"', html)
        self.assertIn(">-55%<", badges[0])
        self.assertIn(">0%<", badges[6])
        self.assertIn(
            'class="offer-badge offer-badge--10 offer-badge--store"', badges[7]
        )
        self.assertEqual(badges[8:], ["", ""])

    def test_the_month_text_adds_the_year_outside_this_year_or_when_asked(self):
        texts = self.run_js(
            "const now = new Date().getFullYear();"
            "console.log(JSON.stringify([offerMonthText(now + '-05'), offerMonthText((now - 1) + '-05'), offerMonthText(now + '-05', true), offerMonthText('rommel')]));"
        )
        self.assertEqual(texts[0], "mei")
        self.assertRegex(texts[1], r"^mei \d{4}$")
        self.assertRegex(texts[2], r"^mei \d{4}$")
        self.assertEqual(texts[3], "rommel")


class OffersScanMarkupTest(unittest.TestCase):
    def test_the_good_count_in_the_status_counts_the_same_offers_as_the_total(self):
        body = function_source("renderOffersStatusGood")
        self.assertIn(
            'offersState.offers.filter((offer) => offer.verdict && offer.verdict.kind === "good").length',
            body,
        )
        self.assertIn(
            "offersCardCount(offersFiltered({ search: false }))",
            body,
            "de teller telt kaarten na alle filters en schakelaars, zonder het zoekveld",
        )

    def test_the_lower_before_tooltip_always_names_the_year(self):
        self.assertIn(
            'title: t("offers.label.lower_before_title", { ...params, month: offerMonthText(label.month, true) })',
            OFFERS_JS,
        )

    def test_the_bonus_box_label_stands_under_the_deal(self):
        card = function_source("offerProductCard")
        self.assertLess(
            card.index("offer.promoLabel"), card.index("offerPersonalBadge(offer)")
        )

    def test_an_empty_thumb_shows_an_icon(self):
        self.assertIn(
            'mdiIcon(MDI_IMAGE_OUTLINE, "mdi offer-card__thumb-icon")',
            function_source("offerThumb"),
        )
        self.assertIn("const MDI_IMAGE_OUTLINE = ", APP_JS)
        self.assertNotIn(
            "MDI_VIEW_GRID_OUTLINE",
            APP_JS,
            "ongebruikt: de paden staan in het template",
        )
        self.assertNotIn("MDI_VIEW_LIST_OUTLINE", APP_JS)
        self.assertRegex(CSS, r"\.offer-card__thumb-icon \{")

    def test_scrolling_with_the_popup_closed_does_nothing(self):
        self.assertIn(
            "if (box.hidden && !offerPopup.timer) return;",
            function_source("hideOfferPopup"),
        )

    def test_the_outlined_orange_and_the_grey_badge_have_hard_colours_with_contrast(
        self,
    ):
        self.assertRegex(
            CSS, r"\.offer-badge--store\.offer-badge--10 \{\n  color: #c25e00;"
        )
        self.assertRegex(
            CSS, r"\.offer-badge--0 \{\n  background: #e5e7eb;\n  color: #374151;"
        )

    def test_the_compact_row_on_a_phone_stacks_name_price_and_label_beside_the_photo(self):
        # Zo'n 300 pixels voor een regel: de badge onder het plaatje, de naam op twee regels, de prijs met de eenheid rechts, het label.
        phone = CSS[CSS.index("/* Twee kaarten naast elkaar op een telefoon") :]
        phone = phone[: phone.index("@media", 10)]
        self.assertIn("  .offer-row {\n    grid-template-columns: 48px minmax(0, 1fr) auto;", phone)
        self.assertIn("  .offer-row__badge {\n    grid-column: 1;\n    grid-row: 2 / span 2;", phone, "de badge onder het plaatje")
        self.assertIn("  .offer-row__product {\n    grid-column: 2 / span 2;\n    grid-row: 1;", phone)
        self.assertIn("  .offer-row__price {\n    grid-column: 2;\n    grid-row: 2;", phone)
        self.assertIn("  .offer-row__unit {\n    grid-column: 3;\n    grid-row: 2;\n    justify-self: end;", phone)
        self.assertIn("  .offer-row__history {\n    grid-column: 2 / span 2;\n    grid-row: 3;", phone)
        self.assertIn("    -webkit-line-clamp: 2;", phone, "de naam mag op een telefoon twee regels zijn")


if __name__ == "__main__":
    unittest.main()
