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
                re.search(r"^const NUTRITION_FILTERS = \[.*?\];$", APP_JS, re.M).group(0),
                function_source("nutritionQuery"),
                function_source("kcalSearchNote"),
                "const SODIUM_MG_PER_SALT_G = 400;",
                function_source("nutritionValues"),
                function_source("nutrientAmount"),
                function_source("nutritionBriefParts"),
                function_source("nutritionBrief"),
                function_source("dayNutrition"),
                function_source("dayNutritionLine"),
                # Een document met alleen de velden van de filters: {id: waarde}.
                "const fields = {}; const document = { getElementById: (id) => (id in fields ? { value: fields[id] } : null) };",
                f"process.stdout.write(JSON.stringify({call}));",
            ]
        )
        result = subprocess.run(["node", "-"], input=script, capture_output=True, text=True, timeout=30, check=True)
        return json.loads(result.stdout)

    def query(self, values, scope="plan", shown=True):
        theme = "" if shown else "globalThis.THEME = { appearance: () => ({ showCalories: false }) };"
        return self.run_js(f"(() => {{ {theme} Object.assign(fields, {json.dumps(values)}); return nutritionQuery({json.dumps(scope)}); }})()")

    def test_the_bounds_of_a_filter_become_query_parameters(self):
        self.assertEqual(self.query({"plan-kcal-min": "400", "plan-kcal-max": " 650.4 "}), "kcalMin=400&kcalMax=650")
        self.assertEqual(self.query({"plan-kcal-max": "600"}), "kcalMax=600")
        self.assertEqual(self.query({"plan-kcal-max": "veel"}), "", "geen getal is geen filter")
        self.assertEqual(self.query({}), "")
        self.assertEqual(self.query({"plan-kcal-min": "-20"}), "kcalMin=0")
        # Meer filters: elke voedingswaarde, zout met één decimaal (ook met een komma), per scherm.
        self.assertEqual(
            self.query({"search-protein-min": "30", "search-sugar-max": "10.6", "search-salt-max": "1,55", "plan-fat-max": "20"}, scope="search"),
            "proteinMin=30&sugarMax=11&saltMax=1.6",
        )

    def test_without_calories_shown_there_is_no_filter(self):
        self.assertEqual(self.query({"plan-kcal-min": "400", "plan-protein-min": "30"}, shown=False), "")

    def test_the_kcal_of_a_recipe_comes_from_mealie_or_from_the_nutrition_of_a_store(self):
        self.assertEqual(self.run_js("recipeKcal({ kcal: 490 })"), 490)
        self.assertEqual(self.run_js('recipeKcal({ nutrition: [{ key: "fatContent", quantity: 3 }, { key: "calories", quantity: 699 }] })'), 699)
        self.assertIsNone(self.run_js("recipeKcal({ kcal: 0, nutrition: [] })"))
        self.assertIsNone(self.run_js("recipeKcal(null)"))

    def test_the_note_of_a_search_on_nutrition(self):
        self.assertEqual(self.run_js("kcalSearchNote({ indexing: { done: 12, total: 80 }, withoutValues: 3 })"), 'kcal.indexing{"done":"12","total":"80"}')
        self.assertEqual(self.run_js("kcalSearchNote({ indexing: null, withoutValues: 3, withoutKcal: 3 })"), "kcal.without:3")
        self.assertEqual(self.run_js("kcalSearchNote({ withoutKcal: 2 })"), "kcal.without:2", "een antwoord van 4.4")
        self.assertEqual(self.run_js("kcalSearchNote({ withoutValues: 0 })"), "")

    def test_brief_on_a_card(self):
        items = [{"key": "proteinContent", "quantity": 27}, {"key": "sugarContent", "quantity": 6.04}, {"key": "sodiumContent", "quantity": 180}]
        html = self.run_js(f"nutritionBrief({json.dumps(items)})")
        part = lambda text: f'<span class="nutrition-brief__part">{text}</span>'  # noqa: E731
        self.assertEqual(html, '<span class="kcal nutrition-brief kcal--after">' + " · ".join([
            part("nutrition.brief_protein{&quot;amount&quot;:&quot;27 g&quot;}"), part("nutrition.brief_sugar{&quot;amount&quot;:&quot;6 g&quot;}"),
            part("nutrition.brief_salt{&quot;amount&quot;:&quot;0.45 g&quot;}"),
        ]) + "</span>")
        self.assertNotIn("kcal--after", self.run_js(f"nutritionBrief({json.dumps(items)}, false)"))
        self.assertEqual(self.run_js("nutritionBrief([])"), "")

    def test_the_day_total_is_per_person(self):
        n = lambda **values: [{"key": key, "quantity": amount} for key, amount in values.items()]  # noqa: E731
        entries = [
            {"recipe": {"name": "Pasta"}, "nutrition": n(calories=490, proteinContent=27, sodiumContent=200)},
            {"recipe": {"name": "Soep"}, "nutrition": n(calories=310.6, proteinContent=12.5)},
            {"recipeId": "r3", "nutrition": []},
            {"title": "Uit eten", "recipe": None},
        ]
        totals = self.run_js(f"dayNutrition({json.dumps(entries)})")
        self.assertEqual(totals["without"], 1, "een notitie telt niet mee, een recept zonder waarden wel")
        self.assertAlmostEqual(totals["totals"]["calories"], 800.6)
        self.assertEqual(totals["totals"]["salt"], 0.5)
        line = self.run_js(f"dayNutritionLine({json.dumps(entries)})")
        self.assertIn('class="day-card__nutrition"', line)
        self.assertIn("801 kcal", line)
        self.assertIn("nutrition.day_without:1", line)
        self.assertEqual(self.run_js(f"dayNutritionLine({json.dumps(entries[2:])})"), "", "zonder voedingswaarden geen regel")


class WiringTest(unittest.TestCase):
    def test_the_filter_fields_exist_in_the_picker_and_in_recipe_search(self):
        for field in ("plan-kcal-min", "plan-kcal-max", "search-kcal-min", "search-kcal-max"):
            self.assertIn(f'id="{field}"', HTML, field)
        self.assertEqual(HTML.count('form="search-form"'), 14, "Enter in een veld van een filter zoekt")
        self.assertIn('nutritionQuery("plan")', function_source("searchPlanRecipes"))
        self.assertIn('nutritionQuery("search")', function_source("runRecipeSearch", SEARCH_JS))
        # Meer filters: elke voedingswaarde met min en max op beide plekken; bij Recepten zoeken met Enter in het formulier.
        for scope in ("plan", "search"):
            self.assertIn(f'id="{scope}-nutrition-filter"', HTML)
            for name in ("protein", "fat", "carbs", "sugar", "fiber", "salt"):
                for side in ("min", "max"):
                    self.assertIn(f'id="{scope}-{name}-{side}"', HTML, f"{scope}-{name}-{side}")
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
        for selector in (".kcal", ".day-card__nutrition", ".nutrition-facts", ".kcal-filter", ".nutrition-filter", ".plan-servings__kcal"):
            self.assertIn(f"html.no-calories {selector}", hidden.group(1), selector)

    def test_the_administrator_can_fill_in_old_ah_recipes(self):
        for element in ('id="mealie-nutrition-start"', 'id="mealie-nutrition-stop"', 'id="mealie-nutrition-message"'):
            self.assertIn(element, HTML)
        self.assertIn('"/api/mealie/nutrition/backfill"', function_source("startNutritionBackfill"))
        self.assertIn('{ method: "DELETE" }', function_source("stopNutritionBackfill"))


if __name__ == "__main__":
    unittest.main()
