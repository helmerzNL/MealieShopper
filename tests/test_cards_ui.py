"""Fase 3 van de kritische blik op de interface (4.8.0): de receptkaart, de aanbiedingskaart, het mandje en de weeknavigatie.

- Receptkaart: de titel is de knop die het recept opent (met een ::after over de hele kaart), onderaan alleen Inplannen.
- Aanbiedingen: sorteren is een keuzelijst, de categorieën staan op een telefoon in één regel die opzij scrolt.
- Mandje: kerncijfers bovenaan (basketStats) en de uitleg in een <details>.
- Weeknavigatie: dezelfde pijlen bij Weekmenu, Aanbiedingen en Maaltijdboxen, en Deze week alleen als je een andere week bekijkt.
"""

import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
HTML = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
CSS = (STATIC / "styles.css").read_text(encoding="utf-8")
APP = (STATIC / "app.js").read_text(encoding="utf-8")
SEARCH = (STATIC / "recipe_search.js").read_text(encoding="utf-8")
MEALKITS = (STATIC / "mealkits.js").read_text(encoding="utf-8")
OFFERS = (STATIC / "offers.js").read_text(encoding="utf-8")
BASKET = (STATIC / "basket.js").read_text(encoding="utf-8")
ORDERS = (STATIC / "orders.js").read_text(encoding="utf-8")


def function_source(source, name):
    match = re.search(rf"^(?:async )?function {re.escape(name)}\(.*?^}}$", source, re.S | re.M)
    if not match:
        raise AssertionError(f"functie {name} niet gevonden")
    return match.group(0)


class RecipeCardTest(unittest.TestCase):
    def test_the_title_opens_the_recipe_and_only_plan_stays_below(self):
        title = function_source(APP, "recipeCardTitle")
        self.assertIn('<button class="recipe-card__open" type="button" data-store-open="${escapeHtml(open)}">', title)
        actions = function_source(APP, "recipeCardActions")
        self.assertNotIn("data-store-open", actions, "geen knop Bekijken meer: de kaart zelf opent het recept")
        self.assertNotIn('class="button" type="button"', actions, "Inplannen is niet meer primair")
        self.assertIn('class="button button--secondary" type="button" ${plan}', actions)
        self.assertIn('<span class="visually-hidden">: ${escapeHtml(title)}</span>', actions, "de naam voor een schermlezer")
        self.assertIn('<div class="recipe-import-result"></div>', actions, "daar komt Ingepland op ...")

    def test_every_source_uses_the_shared_title(self):
        for source, name in (
            (APP, "renderSavedRecipeCard"),
            (APP, "renderRecipeCard"),
            (SEARCH, "renderStoreRecipeCard"),
            (MEALKITS, "renderMealKitCard"),
        ):
            body = function_source(source, name)
            self.assertIn("recipeCardTitle(", body, name)
            self.assertNotIn("<h3>${escapeHtml(", body, name)
            self.assertIn('alt=""', body, f"{name}: de titel staat naast de foto, de foto herhaalt hem niet")

    def test_the_whole_card_is_the_target_except_in_selection_mode(self):
        self.assertIn('.recipe-card__open::after {\n  content: "";\n  position: absolute;\n  inset: 0;', CSS)
        self.assertIn(".recipe-card:has(.recipe-card__open) {\n  position: relative;", CSS)
        self.assertIn(".recipe-card .card-actions,\n.recipe-card .recipe-import-result {\n  position: relative;\n  z-index: 1;", CSS)
        self.assertIn(".recipe-card:has(.recipe-card__open:focus-visible) {\n  outline: 3px solid var(--primary);", CSS)
        self.assertIn(".recipe-card.is-selectable .recipe-card__open {\n  pointer-events: none;", CSS)
        self.assertIn(".recipe-card.is-selectable .recipe-card__open::after {\n  content: none;", CSS)

    def test_compact_titles_do_not_hyphenate_and_long_words_still_fit(self):
        rule = CSS[CSS.index(".recipe-grid--compact .recipe-card h3,\n.recipe-grid--compact .recipe-card__open {") :]
        rule = rule[: rule.index("}")]
        self.assertIn("hyphens: manual;", rule)
        self.assertIn("overflow-wrap: anywhere;", rule)
        self.assertIn(".recipe-grid--compact .recipe-card__open {\n  width: 100%;", CSS)


class OffersToolsTest(unittest.TestCase):
    def test_sorting_is_a_select_with_a_visible_label(self):
        block = HTML[HTML.index('<label class="offers-sort">') :]
        block = block[: block.index("</label>")]
        self.assertIn('<span data-i18n="offers.sort.label">Sorteren</span>', block)
        self.assertEqual(re.findall(r'<option value="(\w+)"', block), ["best", "store", "discount", "unit", "lowest"])
        self.assertNotIn('class="offer-cats offer-cats--sort"', HTML, "geen sorteerchips meer")
        self.assertIn('$("#offers-sort").addEventListener("change"', OFFERS)
        self.assertIn('$("#offers-sort").value = offersState.sort;', OFFERS)

    def test_categories_scroll_in_one_row_on_a_phone(self):
        # Sinds 2026-11-10 geen rij chips meer boven de producten: de categorieen staan als lijst in de filterzijbalk, op een
        # telefoon dus in de lade achter de knop Filters.
        self.assertIn('<ul class="offer-facet" id="offers-categories"></ul>', HTML)
        self.assertNotIn("offer-cats--scroll", HTML + CSS)

    def test_the_card_foot_has_two_icon_buttons_with_their_names(self):
        foot = function_source(OFFERS, "offerCardFoot")
        self.assertIn('class="icon-button offer-card__compare" type="button" data-compare=', foot)
        self.assertIn('class="icon-button offer-card__history"', foot)
        self.assertIn('aria-label="${compareLabel}"', foot)
        self.assertIn('aria-label="${historyLabel}"', foot)
        self.assertNotIn("link-button", foot)

    def test_the_price_label_sits_with_the_insights_not_on_the_photo(self):
        card = function_source(OFFERS, "offerProductCard")
        self.assertIn('offerThumb(offer, "", false, false)', card)
        self.assertIn('<div class="offer-insights">', card)
        self.assertIn("offerPriceLabelHtml(offer) + offerVerdictLines(offer)", card)


@unittest.skipUnless(shutil.which("node"), "Node is niet geïnstalleerd")
class BasketStatsNodeTest(unittest.TestCase):
    def run_js(self, expression):
        script = "const escapeHtml = (s) => String(s);\n" + function_source(BASKET, "basketStats") + f"\nconsole.log({expression});"
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def test_the_figures_are_a_definition_list_and_empty_values_drop_out(self):
        html = self.run_js('basketStats([["Totaal", "€15,65"], ["Stuks", ""], ["Bezorging", "vr 30 okt"]])')
        self.assertEqual(html, '<dl class="stats"><div><dt>Totaal</dt><dd>€15,65</dd></div><div><dt>Bezorging</dt><dd>vr 30 okt</dd></div></dl>')
        self.assertEqual(self.run_js('JSON.stringify(basketStats([["Totaal", ""]]))'), '""')


class BasketMarkupTest(unittest.TestCase):
    def test_the_explanation_folds_away(self):
        block = HTML[HTML.index('<details class="help" id="basket-help">') :]
        block = block[: block.index("</details>")]
        self.assertIn('<summary data-i18n="common.help">Hoe werkt dit?</summary>', block)
        self.assertIn('id="basket-intro"', block)

    def test_every_store_shows_its_figures(self):
        self.assertIn("basketStats([", function_source(ORDERS, "renderBasket"))
        for name in ("picnic.js", "jumbo.js", "crisp.js"):
            self.assertIn("basketStats([", (STATIC / name).read_text(encoding="utf-8"), name)
        self.assertIn(".stats dd {\n  margin: 2px 0 0;", CSS)
        self.assertIn("font-variant-numeric: tabular-nums;", CSS[CSS.index(".stats dd {") : CSS.index(".stats dd {") + 200])

    def test_ah_refreshes_with_the_button_next_to_the_title(self):
        # De knop Synchroniseren deed hetzelfde als Vernieuwen naast de titel (die opent het mandje opnieuw en synchroniseert).
        self.assertNotIn("ah-basket-sync", ORDERS)
        self.assertIn('const button = $("#basket-refresh");', function_source(ORDERS, "syncAhBasket"))


class WeekNavigationTest(unittest.TestCase):
    CHEVRON_LEFT = '<path d="M15.41,16.58L10.83,12L15.41,7.41L14,6L8,12L14,18L15.41,16.58Z"/>'

    def test_the_three_week_bars_use_the_same_arrows(self):
        self.assertNotIn("&lsaquo;", HTML)
        self.assertNotIn("&rsaquo;", HTML)
        previous = [
            re.search(r'<button class="icon-button" id="prev-week" type="button"[^>]*>.*?</button>', HTML).group(0),
            re.search(r'<button class="icon-button" id="mealkits-previous" type="button"[^>]*>.*?</button>', HTML).group(0),
            *re.findall(r'<button class="icon-button" type="button" data-offers-week="-1"[^>]*>.*?</button>', HTML),
        ]
        self.assertEqual(len(previous), 4, "weekmenu, maaltijdboxen en twee keer aanbiedingen")
        for button in previous:
            self.assertIn(self.CHEVRON_LEFT, button)
        self.assertIn('<div class="week-label"><strong id="mealkits-week" aria-live="polite"></strong></div>', HTML)

    def test_this_week_only_shows_when_another_week_is_open(self):
        self.assertIn('id="this-week" type="button" data-i18n="week.this" hidden>', HTML)
        self.assertEqual(HTML.count("data-offers-week-today"), 2)
        self.assertIn('id="mealkits-this-week" type="button" data-i18n="mealkits.this_week" hidden>', HTML)
        self.assertIn('$("#this-week").hidden = state.weekOffset === 0;', APP)
        self.assertIn('bar.querySelector("[data-offers-week-today]").hidden = offset === 0;', OFFERS)
        self.assertIn("offersChangeWeek(-offersWeekOffset())", OFFERS)
        self.assertIn('$("#mealkits-this-week").hidden = !menu || menu.week === menu.currentWeek;', MEALKITS)

    def test_meal_kits_refresh_is_an_icon_button_like_the_week_menu(self):
        self.assertRegex(
            HTML,
            r'<button class="icon-button" id="mealkits-refresh" type="button" aria-label="Vernieuwen" title="Vernieuwen" data-i18n-attr="aria-label:mealkits.refresh;title:mealkits.refresh"><svg[^>]*data-mdi="mdiSync">',
        )


if __name__ == "__main__":
    unittest.main()
