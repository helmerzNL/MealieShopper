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


def function_source(name):
    match = re.search(
        rf"^function {re.escape(name)}\(.*?^}}$", OFFERS_JS, re.DOTALL | re.MULTILINE
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
    [const_source(name) for name in ("OFFER_UNIT_ORDER", "OFFER_LABEL_ORDER", "OFFERS_OFTEN_PER_YEAR", "offersDimmedIds")]
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
        )
    ]
    + [
        "const offersState = { sort: 'store', store: 'ah', filters: null };",
        "const t = (key, params) => key + (params ? ' ' + JSON.stringify(params) : '');",
        "const escapeHtml = (value) => String(value);",
        "const offerBadgeTitle = () => 'uitleg';",
        "const monthShort = (date) => date.toLocaleDateString('nl-NL', { month: 'short' }).replace(/\\.$/, '');",
        "const formatPrice = (value) => 'EUR ' + Number(value).toFixed(2);",
        "const offerById = (id) => ({ id: String(id) });",
        "const foldText = (value) => String(value).toLowerCase();",
        "const matchesWords = (text, query) => query.toLowerCase().split(/\\s+/).every((word) => text.includes(word));",
        "const I18N = { formatNumber: String };",
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
    def run_js(self, body):
        script = f"{PRELUDE}\nconst OFFERS = {json.dumps(OFFERS)};\n{body}"
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

    def test_brand_counts_ignore_the_brand_filter_itself(self):
        offers = [{**self.FILTER_OFFER, "id": "a"}, {**self.FILTER_OFFER, "id": "b", "brand": "Unox", "badge": {"percent": 5}}, {**self.FILTER_OFFER, "id": "c", "brand": "Unox"}]
        found = self.run_js(f"offersState.filters = {json.dumps({**self.EMPTY_FILTERS, 'brands': ['Unox'], 'minDiscount': 25})}; console.log(JSON.stringify(offersBrandCounts({json.dumps(offers)})));")
        self.assertEqual(found, [{"brand": "Dr. Oetker", "count": 1}, {"brand": "Unox", "count": 1}])

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
        self.assertNotIn(
            "offersFiltered()",
            body,
            "niet het zoekveld of de Bonus Box: het totaal ervoor telt die ook niet",
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

    def test_scrolling_with_the_zoom_closed_does_nothing(self):
        self.assertIn(
            "if (box.hidden && !offerZoom.timer) return;",
            function_source("hideOfferZoom"),
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
