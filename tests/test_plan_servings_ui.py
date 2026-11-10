"""Het weekmenu in de browser: porties tonen en gelijke ingrediënten optellen (issue #159).

`aggregateIngredients` uit static/app.js draait hier echt, in Node. Zonder Node slaat die test over; de tekstcontroles
draaien altijd.
"""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
NL = (ROOT / "static" / "i18n" / "nl.js").read_text(encoding="utf-8")
EN = (ROOT / "static" / "i18n" / "en.js").read_text(encoding="utf-8")


def function_source(name):
    """De broncode van een functie op het hoogste niveau van app.js, tot en met de sluitende accolade."""
    match = re.search(rf"^(?:async )?function {re.escape(name)}\(.*?^}}$", APP_JS, re.S | re.M)
    if not match:
        raise AssertionError(f"functie {name} niet gevonden in app.js")
    return match.group(0)


GRAM = {"name": "gram", "abbreviation": "g", "useAbbreviation": True}


def line(quantity, food=None, unit=None, note="", display=None, **food_extra):
    item = {"quantity": quantity, "unit": unit, "note": note}
    if food:
        item["food"] = {"name": food, **food_extra}
    item["display"] = display or " ".join(str(part) for part in (quantity, (unit or {}).get("abbreviation"), food) if part)
    return item


def meal(*ingredients):
    return {"recipeDetail": {"recipeIngredient": list(ingredients)}}


@unittest.skipUnless(shutil.which("node"), "Node is niet geïnstalleerd")
class AggregateIngredientsTest(unittest.TestCase):
    def aggregate(self, entries, locale="nl-NL"):
        script = "\n".join(
            [
                f"const I18N = {{ formatNumber: (value) => Number(value).toLocaleString({json.dumps(locale)}) }};",
                function_source("ingredientUnitLabel"),
                function_source("ingredientFoodLabel"),
                function_source("aggregateIngredients"),
                f"process.stdout.write(JSON.stringify(aggregateIngredients({json.dumps(entries)})));",
            ]
        )
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30, check=True)
        return json.loads(result.stdout)

    def displays(self, entries, **kwargs):
        return [item["display"] for item in self.aggregate(entries, **kwargs)]

    def test_the_same_ingredient_in_two_recipes_adds_up(self):
        rows = self.aggregate([meal(line(400, "spaghetti", GRAM)), meal(line(400, "Spaghetti", GRAM, note="volkoren"))])
        self.assertEqual([(r["quantity"], r["display"], r["note"]) for r in rows], [(800, "800 g spaghetti", "")])

    def test_a_single_line_keeps_its_own_text(self):
        self.assertEqual(self.displays([meal(line(400, "spaghetti", GRAM, note="volkoren", display="400 g spaghetti, volkoren"))]),
                         ["400 g spaghetti, volkoren"])

    def test_another_unit_stays_a_separate_line(self):
        self.assertEqual(self.displays([meal(line(1, "ui"), line(100, "ui", GRAM)), meal(line(2, "ui", pluralName="uien"))]),
                         ["3 ui", "100 g ui"])
        self.assertEqual(self.displays([meal(line(1, "ui", pluralName="uien")), meal(line(2, "ui", pluralName="uien"))]), ["3 uien"])

    def test_decimals_follow_the_language(self):
        liter = {"name": "liter", "abbreviation": "l", "useAbbreviation": False}
        entries = [meal(line(0.5, "melk", liter)), meal(line(0.25, "melk", liter))]
        self.assertEqual(self.displays(entries), ["0,75 liter melk"])
        self.assertEqual(self.displays(entries, locale="en-GB"), ["0.75 liter melk"])

    def test_lines_without_an_amount(self):
        # Zonder aantal één keer; komt het ingrediënt ook met een aantal voor, dan geldt dat.
        self.assertEqual(self.displays([meal(line(None, "peper", display="peper")), meal(line(None, "peper", display="peper naar smaak"))]), ["peper"])
        self.assertEqual(self.displays([meal(line(None, "peper", display="peper")), meal(line(1, "peper", {"name": "theelepel", "abbreviation": "tl", "useAbbreviation": True}))]),
                         ["1 tl peper"])
        self.assertEqual(self.displays([meal(line(2, "peper", GRAM)), meal(line(None, "peper", display="peper"))]), ["2 g peper"])

    def test_text_only_lines_are_deduplicated_as_before(self):
        # Zo staan ze ook in de screenshot-mocks: alleen een tekst, zonder ingrediënt.
        entries = [meal({"display": "400 g spaghetti"}, {"display": "1 ui"}), meal({"display": "400 g spaghetti"}, {})]
        self.assertEqual(self.displays(entries), ["400 g spaghetti", "1 ui"])
        self.assertEqual(self.aggregate([{"recipeDetail": None}, {}]), [])


class WeekMenuTextTest(unittest.TestCase):
    def test_a_meal_shows_its_servings(self):
        render = function_source("renderMeal")
        self.assertIn("mealServings(entry)", render)
        servings = function_source("mealServings")
        self.assertIn('tn("plan.servings", servings.planned)', servings)
        self.assertIn('t("plan.scale"', servings)

    def test_the_basket_uses_the_added_up_list(self):
        # Sinds 4.11.0 staat de boodschappenlijst in shopping.js: opgeteld over de dagen waarvoor je boodschappen doet.
        shopping = (ROOT / "static" / "shopping.js").read_text(encoding="utf-8")
        self.assertIn("items: shopItems(aggregateIngredients(chosen), shopping.pantry, shopping.picks)", shopping)
        self.assertIn("const items = shopNow().items.filter((item) => item.checked);", shopping)

    def test_both_languages_have_the_texts(self):
        for source in (NL, EN):
            for key in ("plan.servings.one", "plan.servings.other", "plan.scale"):
                self.assertIn(f'"{key}"', source)
        self.assertIn('"plan.servings.other": "{count} porties"', NL)
        self.assertIn('"plan.servings.other": "{count} servings"', EN)


if __name__ == "__main__":
    unittest.main()
