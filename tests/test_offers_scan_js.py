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
    [const_source(name) for name in ("OFFER_UNIT_ORDER", "OFFER_LABEL_ORDER")]
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
        )
    ]
    + [
        "const offersState = { sort: 'store' };",
        "const t = (key, params) => key + (params ? ' ' + JSON.stringify(params) : '');",
        "const escapeHtml = (value) => String(value);",
        "const offerBadgeTitle = () => 'uitleg';",
        "const monthShort = (date) => date.toLocaleDateString('nl-NL', { month: 'short' }).replace(/\\.$/, '');",
        "const formatPrice = (value) => 'EUR ' + Number(value).toFixed(2);",
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
