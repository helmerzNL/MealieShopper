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
        sync = rule(".list-card__sync")
        self.assertRegex(sync, r"min-height: (\d+)px")
        self.assertGreaterEqual(int(re.search(r"min-height: (\d+)px", sync).group(1)), 40)

    def test_spinner_respects_reduced_motion(self):
        self.assertRegex(CSS, r"@media \(prefers-reduced-motion: reduce\) {\s*\.icon-button\.is-busy svg {\s*animation: none;")


class OverviewScriptTest(unittest.TestCase):
    def test_lists_filter_by_name_and_by_product(self):
        self.assertIn("matchesWords(list.name, query)", JS)
        self.assertIn("/lists/search?q=${encodeURIComponent(query)}", JS)
        self.assertIn("function itemsApi()", JS)  # het adres hangt af van de gekozen winkel (AH of Jumbo)
        self.assertIn("state.ahListHits = {};\n  ahListSearchSeq++;", JS, "oude treffers horen niet bij een nieuwe zoektekst")

    def test_list_opens_in_place_and_back_uses_history(self):
        for needle in ("history.pushState({ ahList: listId }", "history.back()", 'addEventListener("popstate"',
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
