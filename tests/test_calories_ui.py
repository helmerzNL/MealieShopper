"""Calorieën in de browser (issue #283): de kcal bij recepten en maaltijden, het filter, de porties en de schakelaar.

De pure functies uit static/app.js (kcalQuery, kcalSearchNote, recipeKcal, factsWithKcal) draaien hier echt, in Node; zonder
Node slaan die tests over. De tekstcontroles draaien altijd.
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
THEME_JS = (STATIC / "theme.js").read_text(encoding="utf-8")
APPEARANCE_JS = (STATIC / "appearance.js").read_text(encoding="utf-8")
CSS = (STATIC / "styles.css").read_text(encoding="utf-8")
HTML = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")


def function_source(name, source=APP_JS):
    match = re.search(rf"^(?:async )?function {re.escape(name)}\(.*?^}}$", source, re.S | re.M)
    if not match:
        raise AssertionError(f"functie {name} niet gevonden")
    return match.group(0)


@unittest.skipUnless(shutil.which("node"), "Node is niet geïnstalleerd")
class PureFunctionsTest(unittest.TestCase):
    def run_js(self, call):
        script = "\n".join(
            [
                "const I18N = { formatNumber: (value) => String(value) };",
                "const t = (key, vars) => vars ? `${key}${JSON.stringify(vars)}` : key;",
                "const tn = (key, count) => `${key}:${count}`;",
                function_source("escapeHtml"),
                function_source("kcalSpan"),
                function_source("factsWithKcal"),
                function_source("recipeKcal"),
                function_source("caloriesShown"),
                function_source("kcalQuery"),
                function_source("kcalSearchNote"),
                f"process.stdout.write(JSON.stringify({call}));",
            ]
        )
        result = subprocess.run(["node", "-"], input=script, capture_output=True, text=True, timeout=30, check=True)
        return json.loads(result.stdout)

    def test_the_bounds_of_a_filter_become_query_parameters(self):
        self.assertEqual(self.run_js('kcalQuery({ value: "400" }, { value: " 650.4 " })'), "kcalMin=400&kcalMax=650")
        self.assertEqual(self.run_js('kcalQuery({ value: "" }, { value: "600" })'), "kcalMax=600")
        self.assertEqual(self.run_js('kcalQuery({ value: "" }, { value: "veel" })'), "", "geen getal is geen filter")
        self.assertEqual(self.run_js("kcalQuery(null, undefined)"), "")
        self.assertEqual(self.run_js('kcalQuery({ value: "-20" }, null)'), "kcalMin=0")

    def test_without_calories_shown_there_is_no_filter(self):
        call = '(() => { globalThis.THEME = { appearance: () => ({ showCalories: false }) }; return kcalQuery({ value: "400" }, { value: "600" }); })()'
        self.assertEqual(self.run_js(call), "")

    def test_the_kcal_of_a_recipe_comes_from_mealie_or_from_the_nutrition_of_a_store(self):
        self.assertEqual(self.run_js("recipeKcal({ kcal: 490 })"), 490)
        self.assertEqual(self.run_js('recipeKcal({ nutrition: [{ key: "fatContent", quantity: 3 }, { key: "calories", quantity: 699 }] })'), 699)
        self.assertIsNone(self.run_js("recipeKcal({ kcal: 0, nutrition: [] })"))
        self.assertIsNone(self.run_js("recipeKcal(null)"))

    def test_the_note_of_a_search_on_calories(self):
        self.assertEqual(self.run_js("kcalSearchNote({ indexing: { done: 12, total: 80 }, withoutKcal: 3 })"), 'kcal.indexing{"done":"12","total":"80"}')
        self.assertEqual(self.run_js("kcalSearchNote({ indexing: null, withoutKcal: 3 })"), "kcal.without:3")
        self.assertEqual(self.run_js("kcalSearchNote({ withoutKcal: 0 })"), "")


class WiringTest(unittest.TestCase):
    def test_the_filter_fields_exist_in_the_picker_and_in_recipe_search(self):
        for field in ("plan-kcal-min", "plan-kcal-max", "search-kcal-min", "search-kcal-max"):
            self.assertIn(f'id="{field}"', HTML, field)
        self.assertEqual(HTML.count('form="search-form"'), 2, "Enter in een kcal-veld zoekt")
        self.assertIn('kcalQuery($("#plan-kcal-min"), $("#plan-kcal-max"))', function_source("searchPlanRecipes"))
        self.assertIn('kcalQuery($("#search-kcal-min"), $("#search-kcal-max"))', function_source("runRecipeSearch", SEARCH_JS))
        # Met een grens zoekt alleen Mealie; de andere winkels gaan uit beeld.
        self.assertIn('(!kcal || store === "mealie")', function_source("runRecipeSearch", SEARCH_JS))
        self.assertIn('recipeSearch.kcal && store !== "mealie"', function_source("renderSearchStore", SEARCH_JS))

    def test_servings_go_to_mealie_only_when_changed(self):
        save = function_source("savePlanEntry")
        self.assertIn("planDialog.servingsTouched && planDialog.scaleSupported && base && planDialog.servings", save)
        self.assertIn("body.recipeScale = planDialog.servings / base", save)
        self.assertIn("planServingsHtml(chosen)", function_source("renderPlanRecipe"))
        self.assertIn("planDialog.scaleSupported = Boolean(options.recipeScale)", function_source("loadPlanTypes"))

    def test_the_switch_hides_every_calorie(self):
        self.assertIn('id="appearance-calories"', HTML)
        self.assertIn('root.classList.toggle("no-calories", !look.showCalories)', THEME_JS)
        self.assertIn("showCalories: auth.showCalories", APPEARANCE_JS)
        self.assertIn('$("#appearance-calories").addEventListener("change"', APPEARANCE_JS)
        hidden = re.search(r"((?:html\.no-calories [^,{]+,\s*)*html\.no-calories [^,{]+)\{\s*display: none;", CSS)
        self.assertIsNotNone(hidden)
        for selector in (".kcal", ".nutrition-facts", ".kcal-filter", ".plan-servings__kcal"):
            self.assertIn(f"html.no-calories {selector}", hidden.group(1), selector)

    def test_the_administrator_can_fill_in_old_ah_recipes(self):
        for element in ('id="mealie-nutrition-start"', 'id="mealie-nutrition-stop"', 'id="mealie-nutrition-message"'):
            self.assertIn(element, HTML)
        self.assertIn('"/api/mealie/nutrition/backfill"', function_source("startNutritionBackfill"))
        self.assertIn('{ method: "DELETE" }', function_source("stopNutritionBackfill"))


if __name__ == "__main__":
    unittest.main()
