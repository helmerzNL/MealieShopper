"""Het weekmenu: een tik op een maaltijd opent het recept in een venster, het potlood rechtsboven het plan-venster.

`renderMeal` en `renderMealRecipeBody` uit static/app.js draaien hier echt, in Node, met de kookweergave uit
static/recipe_view.js (zie ook tests/test_recipe_view.py). Zonder Node slaan die tests over; de tekstcontroles draaien altijd.
"""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
VIEW_JS = (ROOT / "static" / "recipe_view.js").read_text(encoding="utf-8")
HTML = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
NL = (ROOT / "static" / "i18n" / "nl.js").read_text(encoding="utf-8")
EN = (ROOT / "static" / "i18n" / "en.js").read_text(encoding="utf-8")


def function_source(name):
    match = re.search(rf"^(?:async )?function {re.escape(name)}\(.*?^}}$", APP_JS, re.S | re.M)
    if not match:
        raise AssertionError(f"functie {name} niet gevonden in app.js")
    return match.group(0)


def recipe_view_source():
    """De constanten en functies van recipe_view.js, zonder de luisteraars onderaan (die hebben een pagina nodig)."""
    consts = re.findall(r"^(?:const|let) \w+ = .*$", VIEW_JS, re.M)
    functions = re.findall(r"^(?:async )?function \w+\(.*?^}$", VIEW_JS, re.S | re.M)
    return "\n".join(line for line in consts if not line.startswith("const MDI_")) + "\n" + "\n".join(functions)


RECIPE_ID = "6f1c8d1e-2b3a-4c5d-8e9f-0a1b2c3d4e5f"


def recipe_entry(**extra):
    entry = {
        "id": 7,
        "date": "2026-11-03",
        "entryType": "dinner",
        "recipeId": RECIPE_ID,
        "recipe": {"id": RECIPE_ID, "slug": "pasta", "name": "Pasta"},
        "recipeDetail": {
            "name": "Pasta <pesto>",
            "description": "Snel en groen.",
            "recipeIngredient": [{"display": "800 g  spaghetti"}, {"note": "zout naar smaak"}, {}],
            "recipeInstructions": [{"text": "Kook de pasta."}, {"text": "  "}, {"text": "Roer de pesto erdoor."}],
        },
        "servings": {"scale": 2, "base": 2, "planned": 4},
        "imageUrl": f"/api/mealie/recipes/{RECIPE_ID}/image?v=1",
    }
    entry.update(extra)
    return entry


NOTE = {"id": 8, "date": "2026-11-04", "entryType": "dinner", "title": "Uit eten", "text": "Bij oma", "recipeId": None, "recipe": None}


@unittest.skipUnless(shutil.which("node"), "Node is niet geïnstalleerd")
class MealRenderTest(unittest.TestCase):
    def run_js(self, call):
        script = "\n".join(
            [
                "const I18N = { formatNumber: (value) => String(value), messages: { nl: {} } };",
                "const t = (key, vars) => vars ? `${key}${JSON.stringify(vars)}` : key;",
                "const tn = (key, count) => `${key}:${count}`;",
                'const MDI_PENCIL_OUTLINE = "M0";',
                function_source("mdiIcon"),
                function_source("mealLabel"),
                function_source("escapeHtml"),
                function_source("mealServings"),
                # De calorieën (issue #283): de hulpfuncties en de namen van de voedingswaarden.
                function_source("kcalSpan"),
                function_source("factsWithKcal"),
                "const SODIUM_MG_PER_SALT_G = 400;",
                re.search(r"^const NUTRITION_ROWS = \[.*?^\];$", APP_JS, re.S | re.M).group(0),
                function_source("nutritionValues"),
                function_source("nutrientAmount"),
                function_source("nutritionTable"),
                'const MDI_CHEF_HAT = "M1"; const MDI_RESTART = "M2"; const MDI_CHECK = "M3";',
                recipe_view_source(),
                function_source("mealName"),
                function_source("renderMeal"),
                function_source("renderMealRecipeBody"),
                f"process.stdout.write(JSON.stringify({call}));",
            ]
        )
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30, check=True)
        return json.loads(result.stdout)

    def test_a_meal_opens_the_recipe_and_the_pencil_edits(self):
        html = self.run_js(f"renderMeal({json.dumps(recipe_entry())})")
        self.assertIn('class="meal__open" type="button" data-plan-view="7"', html)
        self.assertIn('class="meal__edit" type="button" data-plan-edit="7"', html)
        self.assertIn('aria-label="plan.edit_meal{&quot;name&quot;:&quot;Pasta &lt;pesto&gt;&quot;}"', html)
        # Twee knoppen naast elkaar: een knop in een knop is ongeldige HTML en geeft één tikvlak.
        opened = html.index('class="meal__open"')
        self.assertLess(html.index("</button>", opened), html.index('class="meal__edit"'))
        self.assertEqual(html.count("data-plan-edit"), 1)

    def test_a_note_has_no_recipe_so_a_tap_edits_it(self):
        html = self.run_js(f"renderMeal({json.dumps(NOTE)})")
        self.assertNotIn("data-plan-view", html)
        self.assertIn('class="meal__open" type="button" data-plan-edit="8"', html)
        self.assertIn('class="meal__edit" type="button" data-plan-edit="8"', html)

    def test_a_meal_without_id_is_no_button(self):
        entry = recipe_entry()
        del entry["id"]
        html = self.run_js(f"renderMeal({json.dumps(entry)})")
        self.assertNotIn("<button", html)

    def test_the_window_shows_the_planned_ingredients_and_the_steps(self):
        html = self.run_js(f"renderMealRecipeBody({json.dumps(recipe_entry())})")
        self.assertIn("Snel en groen.", html)
        # De kookweergave: elke regel een vinkje, het aantal vet; de stappen genummerd.
        texts = [re.sub(r"<[^>]+>", "", line) for line in re.findall(r'<span class="cook-ingredient__text">(.*?)</span></label>', html)]
        self.assertEqual(texts, ["800 g spaghetti", "zout naar smaak"])
        self.assertEqual(re.findall(r'<span class="cook-ingredient__amount">(.*?)</span>', html), ["800 g"])
        self.assertEqual(html.count('type="checkbox" data-cook-tick='), 2)
        self.assertEqual(re.findall(r'<p class="cook-step__text">(.*?)</p>', html), ["Kook de pasta.", "Roer de pesto erdoor."])
        self.assertIn('<ol class="cook-steps" role="list">', html)
        # Dezelfde sleutel als bij Recepten zoeken (mealie:<slug>): dezelfde voortgang op beide plekken.
        self.assertIn('data-cook-key="mealie:pasta"', html)
        self.assertIn('<h4 class="cook__heading">', html)

    def test_a_meal_without_slug_has_its_own_key(self):
        entry = recipe_entry(recipe=None)
        html = self.run_js(f"renderMealRecipeBody({json.dumps(entry)})")
        self.assertIn('data-cook-key="plan:7"', html)

    def test_empty_parts_say_so(self):
        entry = recipe_entry(recipeDetail={"recipeIngredient": [], "recipeInstructions": []})
        html = self.run_js(f"renderMealRecipeBody({json.dumps(entry)})")
        self.assertIn("rsearch.no_ingredients_mealie", html)
        self.assertIn("rsearch.no_steps_mealie", html)

    def test_a_meal_shows_its_kcal_per_serving_in_its_own_span(self):
        html = self.run_js(f"renderMeal({json.dumps(recipe_entry(kcal={'perServing': 490, 'total': 1960}))})")
        self.assertIn(
            '<small>plan.servings:4 · plan.ingredients_count:3<span class="kcal kcal--after">kcal.per_serving{&quot;kcal&quot;:&quot;490&quot;}</span></small>',
            html,
        )
        self.assertNotIn("kcal", self.run_js(f"renderMeal({json.dumps(recipe_entry(kcal=None))})"))
        # Zonder andere feiten staat er geen punt voor de kcal (kcal--after zet die erbij, alleen na tekst).
        alone = self.run_js("factsWithKcal(['', null], [['kcal.per_serving', 499.6], ['kcal.total', 0]])")
        self.assertEqual(alone, '<span class="kcal">kcal.per_serving{&quot;kcal&quot;:&quot;500&quot;}</span>')

    def test_the_window_shows_the_nutrition_per_serving_and_for_the_planned_servings(self):
        nutrition = [
            {"key": "calories", "quantity": 490, "unit": "kcal"},
            {"key": "fatContent", "quantity": 23, "unit": "g"},
            {"key": "saturatedFatContent", "quantity": 8, "unit": "g"},
            {"key": "sodiumContent", "quantity": 200, "unit": "mg"},
        ]
        html = self.run_js(f"renderMealRecipeBody({json.dumps(recipe_entry(nutrition=nutrition))})")
        rows = re.findall(r'<tr(?: class="([^"]+)")?><th scope="row">(.*?)</th><td>(.*?)</td><td>(.*?)</td></tr>', html)
        # Per portie en voor de 4 geplande porties; verzadigd vet ingesprongen; zout in gram uit natrium (200 mg = 0,5 g).
        self.assertEqual(
            rows,
            [
                ("", "nutrition.calories", "490 kcal", "1960 kcal"),
                ("", "nutrition.fatContent", "23 g", "92 g"),
                ("nutrition-table__sub", "nutrition.saturatedFatContent", "8 g", "32 g"),
                ("", "nutrition.salt", "0.5 g", "2 g"),
            ],
        )
        self.assertIn("nutrition.total:4", html)
        self.assertIn('<h4 class="nutrition-facts__title">nutrition.title</h4>', html)
        single = self.run_js(f"renderMealRecipeBody({json.dumps(recipe_entry(nutrition=nutrition, servings={'planned': 1}))})")
        self.assertNotIn("nutrition.total", single, "bij één portie geen kolom Totaal")
        self.assertNotIn("nutrition-facts", self.run_js(f"renderMealRecipeBody({json.dumps(recipe_entry(nutrition=[]))})"))

    def test_a_recipe_mealie_could_not_give(self):
        html = self.run_js(f"renderMealRecipeBody({json.dumps(recipe_entry(recipeDetail=None))})")
        self.assertIn("plan.view_unavailable", html)


class MealRecipeWiringTest(unittest.TestCase):
    def test_the_week_sends_a_tap_to_the_recipe_and_the_pencil_to_the_plan_window(self):
        start = APP_JS.index('$("#meal-days").addEventListener("click"')
        handler = APP_JS[start : APP_JS.index("\n});", start)]
        self.assertIn('closest("[data-plan-view], [data-plan-edit]")', handler)
        self.assertIn("openMealRecipe(entry)", handler)
        self.assertIn("openPlanDialog({ entry })", handler)

    def test_the_window_exists_with_edit_mealie_link_and_close(self):
        for element in (
            'id="meal-recipe-dialog"',
            'id="meal-recipe-title"',
            'id="meal-recipe-body"',
            'id="meal-recipe-image"',
            'id="meal-recipe-mealie"',
            'id="meal-recipe-edit"',
            'id="meal-recipe-close"',
        ):
            self.assertIn(element, HTML, element)
        for wiring in (
            '$("#meal-recipe-edit").addEventListener',
            '$("#meal-recipe-close").addEventListener',
            '$("#meal-recipe-dialog").addEventListener',
        ):
            self.assertIn(wiring, APP_JS, wiring)
        # Wijzigen vanuit het venster sluit het eerst en opent dan het plan-venster met dezelfde maaltijd.
        edit = APP_JS[APP_JS.index('$("#meal-recipe-edit").addEventListener') :]
        edit = edit[: edit.index("\n});")]
        self.assertLess(edit.index('$("#meal-recipe-dialog").close()'), edit.index("openPlanDialog({ entry })"))

    def test_opening_the_window_checks_the_screen_lock(self):
        opened = function_source("openMealRecipe")
        self.assertLess(opened.index("dialog.showModal()"), opened.index("cookWakeUpdate()"))
        self.assertNotIn("mealRecipeLines", APP_JS)

    def test_the_old_single_button_is_gone(self):
        for old in ("meal--button", "meal__pencil"):
            self.assertNotIn(old, APP_JS, old)
            self.assertNotIn(old, CSS, old)

    def test_both_languages_have_the_texts(self):
        for source in (NL, EN):
            for key in ("plan.view", "plan.edit_meal", "plan.view_unavailable"):
                self.assertIn(f'"{key}"', source)
        self.assertIn('"plan.edit_meal": "{name} wijzigen"', NL)
        self.assertIn('"plan.edit_meal": "Edit {name}"', EN)


if __name__ == "__main__":
    unittest.main()
