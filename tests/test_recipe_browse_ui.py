"""Bladeren door je recepten in Mealie met filters, in de browser (issue #322): static/recipe_search.js, facets.js en de markup.

De pure functies draaien hier echt, in Node; zonder Node slaan die tests over. De controles op de tekst draaien altijd.
"""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
SEARCH_JS = (STATIC / "recipe_search.js").read_text(encoding="utf-8")
FACETS_JS = (STATIC / "facets.js").read_text(encoding="utf-8")
HTML = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")


def function_source(name, source):
    match = re.search(rf"^(?:async )?function {re.escape(name)}\(.*?^}}$", source, re.S | re.M)
    if not match:
        raise AssertionError(f"functie {name} niet gevonden")
    return match.group(0)


def constant(name, source):
    match = re.search(rf"^const {re.escape(name)} = .*?;$", source, re.S | re.M)
    if not match:
        raise AssertionError(f"constante {name} niet gevonden")
    return match.group(0)


@unittest.skipUnless(shutil.which("node"), "Node is niet geïnstalleerd")
class FilterQueryTest(unittest.TestCase):
    def run_js(self, setup, call):
        script = "\n".join(
            [
                # Een document met alleen de twee keuzelijsten: {selector: waarde}.
                "const fields = {'#search-max-minutes': '', '#search-sort': ''};",
                "const $ = (selector) => ({ value: fields[selector] ?? '' });",
                function_source("facetSelection", FACETS_JS),
                function_source("facetActive", FACETS_JS),
                function_source("facetMode", FACETS_JS),
                function_source("facetToggled", FACETS_JS),
                constant("RECIPE_FACETS", SEARCH_JS),
                "const recipeFacets = { tags: facetSelection(), categories: facetSelection() };",
                function_source("recipeFilterQuery", SEARCH_JS),
                function_source("recipeFiltersNarrow", SEARCH_JS),
                function_source("recipeFilterCount", SEARCH_JS),
                setup,
                f"process.stdout.write(JSON.stringify({call}));",
            ]
        )
        result = subprocess.run(["node", "-"], input=script, capture_output=True, text=True, timeout=30, check=True)
        return json.loads(result.stdout)

    def test_without_a_choice_there_is_no_filter(self):
        self.assertEqual(self.run_js("", "[recipeFilterQuery(), recipeFiltersNarrow(), recipeFilterCount()]"), ["", False, 0])

    def test_tags_categories_time_and_order_travel_to_the_route(self):
        setup = """
            recipeFacets.tags = facetToggled(recipeFacets.tags, "pasta", "in");
            recipeFacets.tags = facetToggled(recipeFacets.tags, "vega", "in");
            recipeFacets.tags = facetToggled(recipeFacets.tags, "vis & schaal", "out");
            recipeFacets.categories = facetToggled(recipeFacets.categories, "diner", "in");
            fields['#search-max-minutes'] = '30';
            fields['#search-sort'] = 'newest';
        """
        query, narrow, count = self.run_js(setup, "[recipeFilterQuery(), recipeFiltersNarrow(), recipeFilterCount()]")
        self.assertEqual(query, "tags=pasta,vega&tagsOut=vis%20%26%20schaal&categories=diner&maxMinutes=30&sort=newest")
        self.assertTrue(narrow)
        self.assertEqual(count, 5, "drie tags, een categorie en de tijd; de volgorde is geen filter")

    def test_only_an_order_keeps_the_stores_in_the_search(self):
        self.assertEqual(self.run_js("fields['#search-sort'] = 'rating';", "[recipeFilterQuery(), recipeFiltersNarrow()]"), ["sort=rating", False])
        self.assertTrue(self.run_js("fields['#search-max-minutes'] = '15';", "recipeFiltersNarrow()"), "een grens op de tijd kent alleen Mealie")


@unittest.skipUnless(shutil.which("node"), "Node is niet geïnstalleerd")
class FacetRowTest(unittest.TestCase):
    def test_recipe_rows_have_their_own_button_labels(self):
        script = "\n".join(
            [
                "const I18N = { formatNumber: (value) => String(value) };",
                "const t = (key, vars) => vars ? `${key}${JSON.stringify(vars)}` : key;",
                function_source("escapeHtml", APP_JS),
                function_source("mdiIcon", APP_JS),
                constant("MDI_PLUS", APP_JS),
                constant("MDI_MINUS", APP_JS),
                function_source("facetRowHtml", FACETS_JS),
                "const row = { value: 'pasta', label: 'Pasta', count: 3, mode: 'in' };",
                "process.stdout.write(JSON.stringify([facetRowHtml('tags', row, { include: 'rsearch.filter_include', exclude: 'rsearch.filter_exclude' }), facetRowHtml('brand', row)]));",
            ]
        )
        recipe, offer = json.loads(subprocess.run(["node", "-"], input=script, capture_output=True, text=True, timeout=30, check=True).stdout)
        self.assertIn("rsearch.filter_include", recipe)
        self.assertIn("rsearch.filter_exclude", recipe)
        self.assertIn('aria-pressed="true"', recipe)
        self.assertIn("offers.filters.include", offer, "de aanbiedingen houden hun eigen teksten")


class WiringTest(unittest.TestCase):
    def test_the_filters_are_on_the_search_screen_and_start_closed(self):
        panel = re.search(r'<details class="facet-panel recipe-filters" id="search-facets".*?</details>', HTML, re.S).group(0)
        self.assertIn("data-facet-closed", panel, "eerst het zoekveld; open of dicht onthoudt de browser")
        for element in ('id="search-max-minutes"', 'id="search-sort"', 'id="search-tags"', 'id="search-categories"', "data-facet-clear"):
            self.assertIn(element, panel, element)
        for value in ("15", "30", "45", "60", "90"):
            self.assertIn(f'<option value="{value}"', panel)
        for value in ("newest", "rating"):
            self.assertIn(f'<option value="{value}"', panel)
        self.assertIn('saved === null ? !panel.hasAttribute("data-facet-closed")', function_source("facetPanelsInit", FACETS_JS))

    def test_without_a_search_term_the_screen_says_that_searching_includes_the_stores(self):
        self.assertIn('id="search-browse-hint"', HTML)
        self.assertIn('id="search-mealie-title"', HTML)
        hint = function_source("renderBrowseHint", SEARCH_JS)
        self.assertIn('$("#search-browse-hint").hidden = !browsing', hint)
        self.assertIn('t("rsearch.mealie_all")', function_source("renderSearchStore", SEARCH_JS))
        self.assertIn('t("rsearch.filters_mealie_only")', function_source("runRecipeSearch", SEARCH_JS))

    def test_opening_the_screen_browses_mealie_and_loads_the_lists(self):
        self.assertIn('subtab.dataset.subtab === "search" && typeof showRecipeSearch === "function"', APP_JS)
        self.assertIn('typeof recipeSearchMealieChanged === "function"', function_source("renderMealieStatus", APP_JS))
        show = function_source("showRecipeSearch", SEARCH_JS)
        self.assertIn("loadRecipeFacets()", show)
        self.assertIn('if (!recipeSearch.searched && !$("#recipe-query").value.trim()) runRecipeSearch();', show)
        self.assertIn('jsonFetch("/api/mealie/recipes/facets")', function_source("loadRecipeFacets", SEARCH_JS))

    def test_the_filters_go_along_with_the_mealie_search_and_are_part_of_its_freshness(self):
        fetch = function_source("fetchSearchStore", SEARCH_JS)
        self.assertIn('${filters ? `&${filters}` : ""}', fetch)
        self.assertIn("filters !== recipeSearch.filters", fetch)
        self.assertIn('tn("rsearch.without_time", answer.withoutTime)', fetch)

    def test_scrolling_down_loads_the_next_page(self):
        self.assertIn("new IntersectionObserver(", SEARCH_JS)
        self.assertIn('.observe($("#search-mealie-more"))', SEARCH_JS)
        more = function_source("loadMoreMealie", SEARCH_JS)
        self.assertIn("if (recipeSearch.loadingMore || button.hidden) return;", more, "nooit twee pagina's tegelijk")
        self.assertIn('await fetchSearchStore("mealie", true);', more)

    def test_clearing_the_search_field_goes_back_to_browsing(self):
        self.assertIn("recipeSearch.searched && recipeSearch.query) runRecipeSearch();", SEARCH_JS)


if __name__ == "__main__":
    unittest.main()
