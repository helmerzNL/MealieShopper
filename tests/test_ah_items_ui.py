import os
import re
import unittest
from pathlib import Path

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")


def rule(selector: str, source: str = CSS) -> str:
    match = re.search(r"(?:^|\n)" + re.escape(selector) + r"\s*{(.*?)\n}", source, re.S)
    assert match, selector
    return match.group(1)


def small_screen_css() -> str:
    # Het blok dat de overzichten op een telefoon naast elkaar zet.
    start = CSS.index(".recipe-grid--compact,\n  .list-grid {")
    return CSS[start:start + 400]


class StoreSubtabsTest(unittest.TestCase):
    """De subtabs onder Winkels staan bij elke winkel in dezelfde volgorde: eerst de recepten."""

    ORDER = [
        "ah-items-recepten", "ah-items-weekplanner", "ah-items-jumbo-recepten", "ah-items-lijstjes", "ah-items-bestellingen",
        "ah-items-picnic", "ah-items-picnic-favorieten", "ah-items-picnic-bestellingen",
        "ah-items-crisp-recepten", "ah-items-crisp-favorieten", "ah-items-crisp-bestellingen",
    ]
    STORES = {
        "ah": ["ah-items-recepten", "ah-items-weekplanner", "ah-items-lijstjes", "ah-items-bestellingen"],
        "jumbo": ["ah-items-jumbo-recepten", "ah-items-lijstjes", "ah-items-bestellingen"],
        "picnic": ["ah-items-picnic", "ah-items-picnic-favorieten", "ah-items-picnic-bestellingen"],
        "crisp": ["ah-items-crisp-recepten", "ah-items-crisp-favorieten", "ah-items-crisp-bestellingen"],
    }

    def nav(self):
        nav = HTML[HTML.index('<nav class="subtabs" aria-label="Winkels"'):HTML.index('id="ah-items-recepten" class="subpanel')]
        return re.findall(r'<a class="subtab[^"]*" data-subtab="([\w-]+)"[^>]*>', nav)

    def test_the_buttons_are_in_the_order_recipes_first(self):
        self.assertEqual(self.nav(), self.ORDER)

    def test_each_store_shows_its_subtabs_in_the_wanted_order(self):
        script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        # De winkelkeuze verbergt per winkel wat er niet bij hoort; wat overblijft staat in de volgorde van de knoppen.
        self.assertIn('hide("ah-items-recepten", state.itemsStore !== "ah");', script)
        self.assertIn('hide("ah-items-weekplanner", state.itemsStore !== "ah");', script)
        # Picnic en Crisp hebben eigen bestellingen: de lijstjes en bestellingen van AH en Jumbo verdwijnen daar.
        self.assertIn("const ownTabs = picnic || crisp;", script)
        self.assertIn('hide("ah-items-lijstjes", ownTabs);', script)
        self.assertIn('hide("ah-items-bestellingen", ownTabs);', script)
        self.assertIn('CRISP_SUBTABS.forEach((subtab) => hide(subtab, !crisp));', script)
        self.assertIn('const CRISP_SUBTABS = ["ah-items-crisp-recepten", "ah-items-crisp-favorieten", "ah-items-crisp-bestellingen"];', script)
        self.assertIn('PICNIC_SUBTABS.forEach((subtab) => hide(subtab, !picnic));', script)
        self.assertIn('JUMBO_SUBTABS.forEach((subtab) => hide(subtab, state.itemsStore !== "jumbo"));', script)
        self.assertIn('const PICNIC_SUBTABS = ["ah-items-picnic", "ah-items-picnic-favorieten", "ah-items-picnic-bestellingen"];', script)
        self.assertIn('const JUMBO_SUBTABS = ["ah-items-jumbo-recepten"];', script)
        for store, wanted in self.STORES.items():
            self.assertEqual([tab for tab in self.ORDER if tab in wanted], wanted, store)

    def test_the_first_tab_is_called_recipes_for_every_store(self):
        nl = (ROOT / "static" / "i18n" / "nl.js").read_text(encoding="utf-8")
        en = (ROOT / "static" / "i18n" / "en.js").read_text(encoding="utf-8")
        self.assertIn('"ah.tab.recipes": "Recepten"', nl)
        self.assertIn('"ah.tab.recipes": "Recipes"', en)
        self.assertIn('data-i18n="ah.tab.recipes" href="/stores/ah/recipes" data-nav>Recepten</a>', HTML)

    def test_switching_store_opens_the_same_section_of_that_store(self):
        # Wat er per wissel open gaat, staat in test_store_switch.py; daar draait de wissel echt.
        script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        body = script[script.index("function switchItemsStore"):script.index('localStorage.getItem("ms-store")')]
        self.assertIn('document.querySelector(`#ah-items .subtab[data-subtab="${itemsSubtab(state.itemsStore, section)}"]`)?.click();', body)
        self.assertLess(body.index("?.click();"), body.index("renderItemsStore();"), "eerst de tab, dan de balk")
        self.assertNotIn("PICNIC_SUBTABS", body)
        self.assertNotIn("loadAhFavoriteLists", body)


class WeekPlannerMarkupTest(unittest.TestCase):
    """Winkels > Albert Heijn > Weekplanner: de weekplanner van de AH-app, met Naar weekmenu (ah_planner.js)."""

    def test_the_planner_has_its_own_address_tab_and_panel(self):
        script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn('ah: { recipes: "ah-items-recepten", planner: "ah-items-weekplanner",', script)
        self.assertIn('data-subtab="ah-items-weekplanner" data-i18n="ah.tab.planner" href="/stores/ah/planner" data-nav>Weekplanner</a>', HTML)
        panel = HTML[HTML.index('id="ah-items-weekplanner"'):HTML.index('id="ah-items-lijstjes"')]
        for element in ('id="load-ah-planner"', 'id="ah-planner-to-meal-plan"', 'id="ah-planner-days"', 'id="ah-planner-recipe-view"', 'id="ah-planner-recipe-back"'):
            self.assertIn(element, panel)
        # Pas laden als de tab opengaat, en het script laadt na recipe_search.js (openStoreRecipe, showRecipeList).
        self.assertIn('if (subtab.dataset.subtab === "ah-items-weekplanner" && typeof showAhPlanner === "function") showAhPlanner();', script)
        self.assertLess(HTML.index("filename='recipe_search.js'"), HTML.index("filename='ah_planner.js'"))

    def test_recipes_and_products_go_to_the_week_menu_one_day_at_a_time(self):
        planner = (ROOT / "static" / "ah_planner.js").read_text(encoding="utf-8")
        self.assertIn('day.items.filter((item) => item.kind === "recipe")', planner)
        self.assertIn('day.items.filter((item) => item.kind === "product")', planner)
        self.assertIn('"/api/ah/planner/to-meal-plan"', planner)
        # Een dag per verzoek, met de keuzes van de gebruiker, en daarna het weekmenu vernieuwen.
        self.assertIn("for (const [index, day] of days.entries())", planner)
        self.assertIn("body: JSON.stringify(day)", planner)
        self.assertIn("...options,", planner)
        self.assertIn("refreshMealPlanAfterChange(", planner)

    def test_the_choices_for_loose_products_live_in_the_preferences(self):
        # Samen als één menu, en soep en pizza dan toch los (4.3.0) staan sinds 4.3.1 bij Instellingen, Voorkeuren, Supermarkt
        # (test_appearance.py); het paneel leest ze alleen en wijst ernaar.
        panel = HTML[HTML.index('id="ah-items-weekplanner"'):HTML.index('id="ah-items-lijstjes"')]
        self.assertNotIn("ah-planner-combine", panel)
        self.assertNotIn("ah-planner-split-meals", panel)
        self.assertIn('<a href="/settings/preferences" data-route data-i18n="ah.planner.settings_link">', panel)
        planner = (ROOT / "static" / "ah_planner.js").read_text(encoding="utf-8")
        self.assertIn("return { combine: state.auth?.plannerCombine !== false, splitMeals: state.auth?.plannerSplitMeals !== false };", planner)
        self.assertNotIn('"/api/profile/preferences"', planner, "bewaren doet appearance.js")


class OverviewMarkupTest(unittest.TestCase):
    def test_recipes_have_a_search_bar_above_the_cards(self):
        recipes = HTML[HTML.index('id="ah-items-recepten"'):HTML.index('id="ah-items-lijstjes"')]
        self.assertIn('id="ah-recipe-filter"', recipes)
        self.assertIn('type="search"', recipes)
        self.assertLess(recipes.index("ah-recipe-filter"), recipes.index('id="ah-recipes"'))
        self.assertIn("recipe-grid--compact", recipes)

    def test_lists_have_a_search_bar_and_a_detail_view_with_a_back_button(self):
        lists = HTML[HTML.index('id="ah-items-lijstjes"'):HTML.index('<section id="profile"')]
        self.assertIn('id="ah-list-filter"', lists)
        self.assertLess(lists.index("ah-list-filter"), lists.index('id="ah-lists"'))
        self.assertIn('id="ah-lists-view"', lists)
        self.assertRegex(lists, r'id="ah-list-view" hidden')
        back = re.search(r'<button[^>]*id="ah-list-back"[^>]*>', lists).group(0)
        self.assertIn('aria-label="Terug naar de lijstjes"', back)
        self.assertLess(lists.index("ah-list-back"), lists.index('id="ah-list-items"'))

    def test_search_fields_have_a_label(self):
        for field in ("ah-recipe-filter", "ah-list-filter"):
            block = re.search(r'<label class="searchbar">(.*?)</label>', HTML[HTML.index(field) - 600:], re.S)
            self.assertIn("visually-hidden", block.group(1), field)

    def test_search_results_use_the_same_compact_grid(self):
        self.assertIn('id="recipe-results" class="recipe-grid recipe-grid--compact"', HTML)

    def test_message_container_is_not_re_rendered(self):
        # De melding hoort buiten #ah-list-items, dat bij elke keer laden leeggemaakt wordt.
        self.assertLess(HTML.index('id="ah-list-items-message"'), HTML.index('id="ah-list-items"'))


class OverviewStyleTest(unittest.TestCase):
    def test_two_columns_on_a_phone_for_recipes_and_lists(self):
        css = small_screen_css()
        self.assertIn("repeat(2, minmax(0, 1fr))", css)
        self.assertIn("@media (max-width: 640px)", CSS[CSS.index(".recipe-grid--compact,\n  .list-grid {") - 200:])

    def test_more_columns_on_larger_screens(self):
        self.assertRegex(rule(".recipe-grid--compact"), r"repeat\(auto-fill, minmax\(\d+px, 1fr\)\)")
        self.assertRegex(rule(".list-grid"), r"repeat\(auto-fill, minmax\(\d+px, 1fr\)\)")

    def test_overviews_use_the_full_width(self):
        self.assertIn("max-width: none", rule(".box--wide"))
        for ident in ('id="ah-lists-view"', 'id="ah-list-view" hidden'):
            self.assertRegex(HTML, r'class="box box--wide" ' + ident)

    def test_meal_thumbnail(self):
        image = rule(".meal__image")
        self.assertIn("object-fit: cover", image)
        self.assertIn("width: 64px", image)

    def test_sync_button_keeps_a_big_enough_target(self):
        # Ook de prullenbak op een ingelezen bestelling (list-card__action), die op dezelfde plek staat.
        for selector in (".list-card__sync", ".list-card__action"):
            button = rule(selector)
            self.assertRegex(button, r"min-height: (\d+)px")
            self.assertGreaterEqual(int(re.search(r"min-height: (\d+)px", button).group(1)), 40, selector)

    def test_spinner_respects_reduced_motion(self):
        self.assertRegex(CSS, r"@media \(prefers-reduced-motion: reduce\) {\s*\.icon-button\.is-busy svg {\s*animation: none;")


class OverviewScriptTest(unittest.TestCase):
    def test_lists_filter_by_name_and_by_product(self):
        self.assertIn("matchesWords(list.name, query)", JS)
        self.assertIn("/lists/search?q=${encodeURIComponent(query)}", JS)
        self.assertIn("function itemsApi()", JS)  # het adres hangt af van de gekozen winkel (AH of Jumbo)
        self.assertIn("state.ahListHits = {};\n  ahListSearchSeq++;", JS, "oude treffers horen niet bij een nieuwe zoektekst")

    def test_list_opens_in_place_and_back_uses_history(self):
        for needle in ("state.ahListFromOverview = push", "history.back()", "routerSync()",
                       'history.scrollRestoration = "manual"', "state.ahListsScroll"):
            self.assertIn(needle, JS, needle)

    def test_recipes_are_filtered_on_the_device_and_cached_on_the_server(self):
        self.assertIn("matchesWords(recipe.title, query)", JS)
        self.assertIn("?cached=1", JS)
        self.assertIn("?refresh=1", JS)
        self.assertIn("loadSavedRecipes({ cachedOnly: true })", JS)

    def test_meal_images_come_from_the_entry_and_fail_quietly(self):
        self.assertIn("entry.imageUrl", JS)
        self.assertIn("data-hide-on-error", JS)

    def test_user_text_is_never_put_in_html_unescaped(self):
        # De zoektekst komt via th() (dat de waarden escaped) of via message() (dat alles escaped) op het scherm.
        self.assertIn('th("list.no_match", { query })', JS)
        self.assertIn('t("saved.none_match", { query })', JS)


if __name__ == "__main__":
    unittest.main()
