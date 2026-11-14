"""Bladeren door recepten: in een recept dat open staat naar het vorige of volgende recept van dezelfde lijst (static/recipe_nav.js).

De keuzes (welk recept na een stap, wanneer een veeg telt, welke toets, de knoppen van de balk) draaien hier echt, in Node. Zonder
Node slaan die tests over; de tekstcontroles op de aansluiting (de gastheren, het weekmenu-venster, de CSS, de teksten) draaien
altijd. In een echte browser is dit nagelopen met klikken, pijltjestoetsen en echte aanraakvegen.
"""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAV_JS = (ROOT / "static" / "recipe_nav.js").read_text(encoding="utf-8")
APP_JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
SEARCH_JS = (ROOT / "static" / "recipe_search.js").read_text(encoding="utf-8")
PLANNER_JS = (ROOT / "static" / "ah_planner.js").read_text(encoding="utf-8")
HTML = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
NL = (ROOT / "static" / "i18n" / "nl.js").read_text(encoding="utf-8")
EN = (ROOT / "static" / "i18n" / "en.js").read_text(encoding="utf-8")


def function_source(source, name):
    match = re.search(rf"^(?:async )?function {re.escape(name)}\(.*?^}}$", source, re.S | re.M)
    if not match:
        raise AssertionError(f"functie {name} niet gevonden")
    return match.group(0)


def nav_source():
    """De constanten en functies van recipe_nav.js, zonder de luisteraars onderaan (die hebben een pagina nodig)."""
    consts = re.findall(r"^const (?:MDI_CHEVRON_\w+|RECIPE_NAV_SWIPE_\w+) = [^;]*;", NAV_JS, re.M)
    functions = [
        function_source(NAV_JS, name) for name in ("recipeNavParse", "recipeNavTarget", "recipeNavSwipeStep", "recipeNavKeyStep", "recipeNavHtml")
    ]
    return "\n".join(consts + functions)


@unittest.skipUnless(shutil.which("node"), "Node is niet geïnstalleerd")
class RecipeNavLogicTest(unittest.TestCase):
    def run_js(self, call):
        script = "\n".join(
            [
                "const t = (key) => key;",
                function_source(APP_JS, "escapeHtml"),
                function_source(APP_JS, "mdiIcon"),
                nav_source(),
                f"process.stdout.write(JSON.stringify({call}));",
            ]
        )
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30, check=True)
        return json.loads(result.stdout)

    def test_a_card_gives_its_store_and_id(self):
        self.assertEqual(self.run_js('recipeNavParse("mealie:pasta-pesto")'), {"store": "mealie", "id": "pasta-pesto"})
        self.assertEqual(self.run_js('recipeNavParse("jumbo:j1")'), {"store": "jumbo", "id": "j1"})
        # Een id met een dubbele punt blijft heel: alleen de eerste scheidt de winkel.
        self.assertEqual(self.run_js('recipeNavParse("hellofresh:wk:12")'), {"store": "hellofresh", "id": "wk:12"})

    def test_a_card_without_store_or_id_gives_nothing(self):
        for value in ('""', '"mealie"', '"mealie:"', '":abc"', "null", "undefined"):
            self.assertIsNone(self.run_js(f"recipeNavParse({value})"), value)

    def test_a_step_stays_inside_the_list(self):
        self.assertEqual(self.run_js("recipeNavTarget(1, 3, 1)"), 2)
        self.assertEqual(self.run_js("recipeNavTarget(1, 3, -1)"), 0)
        # Aan de rand is er niets: vorige bij het eerste, volgende bij het laatste.
        self.assertEqual(self.run_js("recipeNavTarget(0, 3, -1)"), -1)
        self.assertEqual(self.run_js("recipeNavTarget(2, 3, 1)"), -1)

    def test_a_step_needs_a_recipe_that_is_in_the_list(self):
        # Staat het open recept niet in de reeks (index -1, of buiten de lijst), dan is er geen vorige of volgende.
        self.assertEqual(self.run_js("recipeNavTarget(-1, 3, 1)"), -1)
        self.assertEqual(self.run_js("recipeNavTarget(3, 3, -1)"), -1)
        self.assertEqual(self.run_js("recipeNavTarget(0, 0, 1)"), -1)

    def test_a_step_is_one_back_or_one_forward(self):
        for step in (0, 2, -2):
            self.assertEqual(self.run_js(f"recipeNavTarget(1, 5, {step})"), -1, step)

    def test_a_swipe_to_the_left_is_next_and_to_the_right_is_previous(self):
        self.assertEqual(self.run_js("recipeNavSwipeStep(-80, 4)"), 1)
        self.assertEqual(self.run_js("recipeNavSwipeStep(80, -4)"), -1)

    def test_only_a_clear_sideways_swipe_counts(self):
        # Te kort, te schuin (dat is scrollen), en precies op de grens: 60 px en twee keer zoveel zijwaarts als verticaal telt wel.
        self.assertEqual(self.run_js("recipeNavSwipeStep(-59, 0)"), 0)
        self.assertEqual(self.run_js("recipeNavSwipeStep(-60, 0)"), 1)
        self.assertEqual(self.run_js("recipeNavSwipeStep(-100, 80)"), 0)
        self.assertEqual(self.run_js("recipeNavSwipeStep(-100, 50)"), 1)
        self.assertEqual(self.run_js("recipeNavSwipeStep(-100, 51)"), 0)
        self.assertEqual(self.run_js("recipeNavSwipeStep(0, 300)"), 0)

    def test_the_arrow_keys_step_and_modifiers_do_not(self):
        self.assertEqual(self.run_js('recipeNavKeyStep({ key: "ArrowRight" })'), 1)
        self.assertEqual(self.run_js('recipeNavKeyStep({ key: "ArrowLeft" })'), -1)
        for key in ("ArrowUp", "ArrowDown", "PageDown", "n", "Enter"):
            self.assertEqual(self.run_js(f'recipeNavKeyStep({{ key: "{key}" }})'), 0, key)
        # Alt + pijltje is terug in de browser, Shift selecteert tekst, Ctrl en Meta zijn sneltoetsen.
        for modifier in ("altKey", "ctrlKey", "metaKey", "shiftKey"):
            self.assertEqual(self.run_js(f'recipeNavKeyStep({{ key: "ArrowRight", {modifier}: true }})'), 0, modifier)

    def test_the_top_bar_has_two_chevrons_and_a_live_position(self):
        html = self.run_js('recipeNavHtml("top")')
        self.assertEqual(html.count("data-recipe-step="), 2)
        self.assertIn('data-recipe-step="-1" aria-label="recipenav.prev"', html)
        self.assertIn('data-recipe-step="1" aria-label="recipenav.next"', html)
        self.assertIn('class="icon-button recipe-nav__step"', html)
        # De plek wordt voorgelezen als hij verandert; de chevrons staan om de plek heen.
        self.assertIn('class="recipe-nav__count" role="status"', html)
        self.assertLess(html.index('data-recipe-step="-1"'), html.index("recipe-nav__count"))
        self.assertLess(html.index("recipe-nav__count"), html.index('data-recipe-step="1"'))
        self.assertNotIn("recipenav.prev_short", html)

    def test_the_bottom_bar_has_previous_and_next_with_text_and_no_position(self):
        html = self.run_js('recipeNavHtml("bottom")')
        self.assertEqual(html.count("data-recipe-step="), 2)
        self.assertIn("<span>recipenav.prev_short</span>", html)
        self.assertIn("<span>recipenav.next_short</span>", html)
        self.assertIn("button button--secondary recipe-nav__step", html)
        # Op een telefoon is er geen ruimte voor de plek; die staat bovenaan, en een tweede status zou dubbel worden voorgelezen.
        self.assertNotIn("recipe-nav__count", html)
        self.assertNotIn("role=", html)

    def test_the_chevron_points_the_way_of_its_step(self):
        top = self.run_js('recipeNavHtml("top")')
        bottom = self.run_js('recipeNavHtml("bottom")')
        left = re.search(r'^const MDI_CHEVRON_LEFT = "([^"]+)"', NAV_JS, re.M).group(1)
        right = re.search(r'^const MDI_CHEVRON_RIGHT = "([^"]+)"', NAV_JS, re.M).group(1)
        for html in (top, bottom):
            self.assertLess(html.index(left), html.index(right))
        # Onderaan staat bij Vorige het chevron voor de tekst, bij Volgende erna.
        self.assertLess(bottom.index(left), bottom.index("recipenav.prev_short"))
        self.assertLess(bottom.index("recipenav.next_short"), bottom.index(right))


class RecipeNavWiringTest(unittest.TestCase):
    def test_the_script_loads_after_app_and_before_the_screens_that_use_it(self):
        scripts = re.findall(r"filename='([\w./]+\.js)'", HTML)
        self.assertLess(scripts.index("app.js"), scripts.index("recipe_nav.js"))
        self.assertLess(scripts.index("recipe_view.js"), scripts.index("recipe_nav.js"))
        self.assertLess(scripts.index("recipe_nav.js"), scripts.index("recipe_search.js"))

    def test_every_host_has_a_list_and_a_detail_box_with_a_header(self):
        hosts = re.findall(r'prefix: "([\w-]+)",?\s*list: "#([\w-]+)",\s*detail: "#([\w-]+)"', SEARCH_JS + PLANNER_JS)
        self.assertGreaterEqual(len(hosts), 7, hosts)  # Recepten zoeken, Jumbo, Picnic, Crisp, AH bewaard, maaltijdboxen, AH-weekplanner
        for prefix, list_id, detail_id in hosts:
            self.assertIn(f'id="{list_id}"', HTML, list_id)
            detail = HTML[HTML.index(f'id="{detail_id}"') :]
            detail = detail[: detail.index(f'id="{prefix}-body"')]
            self.assertIn('class="list-header"', detail, f"{detail_id} heeft geen kop om de balk in te zetten")
            self.assertIn(f'id="{prefix}-title"', detail, detail_id)
            self.assertIn(f'id="{prefix}-body"', HTML, prefix)

    def test_opening_a_recipe_from_a_list_starts_the_sequence_and_a_step_keeps_it(self):
        opened = function_source(SEARCH_JS, "openStoreRecipe")
        self.assertIn("{ move = false } = {}", opened)
        self.assertIn("if (!move) recipeNavBegin(host, store, id);", opened)
        self.assertIn("if (!move)", opened[opened.index("recipeNavBegin") :])
        self.assertIn("recipeNavArrived($(host.detail))", opened)

    def test_a_quick_series_of_steps_only_shows_the_last_answer(self):
        opened = function_source(SEARCH_JS, "openStoreRecipe")
        self.assertIn("const token = (host.seq = (host.seq || 0) + 1);", opened)
        # Zowel het antwoord als een fout van een eerdere stap wordt weggegooid.
        self.assertEqual(opened.count("if (token !== host.seq) return;"), 2)
        self.assertLess(opened.index("renderStoreRecipeDetail"), opened.index("recipeNavArrived"))

    def test_going_back_to_the_list_ends_the_sequence(self):
        self.assertIn("recipeNavEnd(host);", function_source(SEARCH_JS, "showRecipeList"))

    def test_the_week_menu_window_has_a_sequence_of_its_own(self):
        opened = function_source(APP_JS, "openMealRecipe")
        self.assertIn("{ move = false } = {}", opened)
        # De reeks staat er voordat het venster opent; na een stap schuift het recept in en gaat de focus goed.
        self.assertLess(opened.index("recipeNavMeal(entry)"), opened.index("dialog.showModal()"))
        self.assertLess(opened.index("dialog.showModal()"), opened.index("recipeNavArrived(dialog)"))
        self.assertIn("if (!move && typeof recipeNavMeal", opened)
        self.assertIn('id="meal-recipe-dialog"', HTML)
        dialog = re.search(r'<dialog id="meal-recipe-dialog"[^>]*>', HTML).group(0)
        self.assertIn("data-recipe-surface", dialog)
        self.assertIn('<div class="recipe-nav recipe-nav--top" data-recipe-nav="top" hidden></div>', HTML)

    def test_a_note_has_no_recipe_and_is_not_in_the_week_sequence(self):
        items = function_source(NAV_JS, "recipeNavMealItems")
        self.assertIn("[data-plan-view]", items)
        self.assertNotIn("data-plan-edit", items)

    def test_the_lists_that_page_on_load_the_next_page_at_the_end(self):
        # Recepten zoeken zonder zoekterm (alleen Mealie op het scherm) en de favorieten van Jumbo; bij een zoekterm staat Mealie vooraan
        # en zou een volgende pagina midden in de reeks komen.
        self.assertIn("more: () => recipeSearch.mealieOnly && recipeSearch.mealie.more,", SEARCH_JS)
        self.assertIn("total: () => (recipeSearch.mealieOnly ? recipeSearch.mealie.total : 0),", SEARCH_JS)
        self.assertIn("more: () => jumboFavorites.hasNext,", SEARCH_JS)
        self.assertIn("loadMore: () => loadJumboFavorites(true),", SEARCH_JS)
        loader = function_source(SEARCH_JS, "loadMoreMealieForNav")
        self.assertIn("if (recipeSearch.loadingMore) return;", loader)  # nooit tegelijk met het doorlopend scrollen
        self.assertIn("recipeSearch.loadingMore = false;", loader)

    def test_a_hidden_load_more_button_does_not_keep_loading_pages(self):
        # Een recept dat opent terwijl een pagina laadt verbergt de lijst; een verborgen knop meet 0 en zou pagina na pagina laden.
        more = function_source(SEARCH_JS, "loadMoreMealie")
        self.assertIn("button.getClientRects().length", more)

    def test_arrow_keys_leave_fields_tabs_and_the_cook_mode_alone(self):
        keys = NAV_JS[NAV_JS.index('document.addEventListener("keydown"') :]
        keys = keys[: keys.index("\n});")]
        self.assertIn("event.defaultPrevented", keys)
        self.assertIn("input, select, textarea, [contenteditable]", keys)
        self.assertIn("[role='tab']", keys)
        # De kookmodus staat boven het recept van een maaltijd (twee vensters open): die heeft zijn eigen pijltjes.
        self.assertIn("dialogs.length > 1", keys)

    def test_a_swipe_needs_a_finger_not_the_mouse_and_ignores_fields_and_sideways_scrolling(self):
        down = NAV_JS[NAV_JS.index('"pointerdown"') :]
        down = down[: down.index("{ passive: true }")]
        self.assertIn('event.pointerType === "mouse"', down)
        self.assertIn("!event.isPrimary", down)  # een tweede vinger (knijpen) is geen veeg
        self.assertIn("input, select, textarea, [contenteditable], [data-no-swipe]", down)
        self.assertIn("recipeNavScrollsSideways(event.target, surface)", down)
        self.assertIn('document.addEventListener("pointercancel"', NAV_JS)

    def test_the_surface_hands_sideways_swipes_to_the_script_and_keeps_scrolling_and_zooming(self):
        match = re.search(r"^\[data-recipe-surface\] \{(.*?)^\}", CSS, re.S | re.M)
        self.assertIsNotNone(match)
        self.assertIn("touch-action: pan-y pinch-zoom;", match.group(1))

    def test_the_slide_only_runs_with_motion_allowed(self):
        media = "@media (prefers-reduced-motion: no-preference) {"
        block = CSS[CSS.index(media) : CSS.index("\n}\n", CSS.index(media)) + 3]
        for direction in ("next", "prev"):
            rule = f".recipe-slide-{direction} {{"
            # De klasse staat maar op één plek, en dat is binnen het blok voor wie beweging toestaat.
            self.assertEqual(CSS.count(rule), 1, rule)
            self.assertIn(rule, block, rule)
            self.assertIn(f"animation: recipe-slide-{direction} ", block)
            self.assertIn(f"@keyframes recipe-slide-{direction} {{", CSS)

    def test_the_counter_has_no_muted_class_that_would_push_the_next_button_down(self):
        # `.box .muted + .button` geeft een knop na een grijze tekst een bovenmarge: dat zette Volgende onderaan te laag.
        self.assertIn(".box .muted + .button", CSS)
        self.assertNotIn("muted", function_source(NAV_JS, "recipeNavHtml"))
        counter = re.search(r"^\.recipe-nav__count \{(.*?)^\}", CSS, re.S | re.M).group(1)
        self.assertIn("color: var(--muted);", counter)

    def test_the_bar_is_built_once_so_focus_stays_on_the_chevron(self):
        render = function_source(NAV_JS, "recipeNavRender")
        self.assertIn("if (rebuild || !holder.firstElementChild) holder.innerHTML", render)
        self.assertIn("button.disabled = !recipeNavCan(", render)
        # Valt de chevron uit (het eerste of laatste recept), dan gaat de focus naar de titel.
        arrived = function_source(NAV_JS, "recipeNavArrived")
        self.assertIn("active.disabled", arrived)
        self.assertIn("focus({ preventScroll: true })", arrived)

    def test_both_languages_have_the_texts(self):
        for key in ("group", "prev", "next", "prev_short", "next_short", "position"):
            self.assertIn(f'"recipenav.{key}"', NL)
            self.assertIn(f'"recipenav.{key}"', EN)
        self.assertIn('"recipenav.position": "{current} van {total}"', NL)
        self.assertIn('"recipenav.position": "{current} of {total}"', EN)


if __name__ == "__main__":
    unittest.main()
