import os
import unittest
from unittest import mock

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

from mealieshopper import ingredients, mealie  # noqa: E402


def unparsed(note, quantity=1):
    """Een regel zoals Mealie hem bewaart als hij de tekst niet analyseerde."""
    return {"quantity": quantity, "unit": None, "food": None, "note": note, "display": f"{quantity} {note}", "originalText": note}


class ParseLineTest(unittest.TestCase):
    def test_amount_unit_name(self):
        self.assertEqual(
            ingredients.parse_line("200 ml melk"),
            {"quantity": 200.0, "unit": "ml", "name": "melk", "extra": ""},
        )
        self.assertEqual(ingredients.parse_line("500 g champignons")["name"], "champignons")

    def test_no_unit(self):
        self.assertEqual(ingredients.parse_line("1 ui"), {"quantity": 1.0, "unit": "", "name": "ui", "extra": ""})
        self.assertEqual(ingredients.parse_line("2x tomaten")["quantity"], 2.0)

    def test_fractions_decimals_and_ranges(self):
        self.assertEqual(ingredients.parse_line("1 1/2 el olie")["quantity"], 1.5)
        self.assertEqual(ingredients.parse_line("½ tl zout")["quantity"], 0.5)
        self.assertEqual(ingredients.parse_line("1,5 l water")["quantity"], 1.5)
        self.assertEqual(ingredients.parse_line("2-3 el olie")["quantity"], 3.0)

    def test_extra_after_comma_and_parentheses(self):
        parsed = ingredients.parse_line("1 ui, fijngesneden")
        self.assertEqual((parsed["name"], parsed["extra"]), ("ui", "fijngesneden"))
        self.assertEqual(ingredients.parse_line("20 g peterselie (vers)")["name"], "peterselie")

    def test_unit_word_that_is_part_of_the_name(self):
        self.assertEqual(ingredients.parse_line("1 stokbrood")["name"], "stokbrood")
        self.assertEqual(ingredients.parse_line("1 stokbrood")["unit"], "")

    def test_no_amount_or_no_name(self):
        self.assertIsNone(ingredients.parse_line("peper naar smaak"))
        self.assertIsNone(ingredients.parse_line("200 ml"))
        self.assertIsNone(ingredients.parse_line("2 x 400 g tomaten"))
        self.assertIsNone(ingredients.parse_line(""))


class NormalizeTest(unittest.TestCase):
    def test_unparsed_line_gets_the_shape_of_an_analysed_one(self):
        item = ingredients.normalize(unparsed("200 ml melk"))
        self.assertEqual(item["display"], "200 ml melk")
        self.assertEqual(item["quantity"], 200.0)
        self.assertEqual(item["unit"]["name"], "ml")
        self.assertEqual(item["food"]["name"], "melk")
        self.assertEqual(item["note"], "")

    def test_the_screenshot_lines(self):
        shown = {
            "1 ui": "1 ui",
            "1 snufje zwarte peper": "1 snufje zwarte peper",
            "1 el maïzena": "1 el maïzena",
            "500 g lintnoedels": "500 g lintnoedels",
        }
        for note, display in shown.items():
            self.assertEqual(ingredients.normalize(unparsed(note))["display"], display)

    def test_unit_without_amount_means_one(self):
        item = ingredients.normalize(unparsed("snufje zout"))
        self.assertEqual((item["quantity"], item["unit"]["name"], item["food"]["name"]), (1.0, "snufje", "zout"))

    def test_text_comes_from_original_text_when_note_is_empty(self):
        line = unparsed("")
        line["originalText"] = "200 g slagroom"
        self.assertEqual(ingredients.normalize(line)["food"]["name"], "slagroom")

    def test_lines_mealie_analysed_stay_as_they_are(self):
        analysed = {"quantity": 200, "unit": {"name": "ml"}, "food": {"name": "melk"}, "note": "", "display": "200 ml melk"}
        self.assertIs(ingredients.normalize(analysed), analysed)
        food_only = {"quantity": 1, "unit": None, "food": {"name": "ui"}, "note": "1 ui", "display": "1 ui"}
        self.assertIs(ingredients.normalize(food_only), food_only)

    def test_a_real_quantity_is_not_overruled(self):
        line = unparsed("400 g tomaten", quantity=2)
        self.assertIs(ingredients.normalize(line), line)

    def test_unusable_text_stays_as_it_is(self):
        line = unparsed("peper naar smaak")
        self.assertIs(ingredients.normalize(line), line)
        self.assertEqual(ingredients.normalize("geen dict"), "geen dict")

    def test_recipe(self):
        recipe = {"recipeIngredient": [unparsed("200 ml melk"), unparsed("peper")]}
        result = ingredients.normalize_recipe(recipe)
        self.assertEqual([i["display"] for i in result["recipeIngredient"]], ["200 ml melk", "1 peper"])
        self.assertEqual(ingredients.normalize_recipe({"name": "x"}), {"name": "x"})


class GetRecipeTest(unittest.TestCase):
    def test_get_recipe_normalises_the_ingredients(self):
        body = {"name": "Stroganoff", "recipeIngredient": [unparsed("1 ui"), unparsed("200 ml melk")]}
        reply = mock.Mock(ok=True, status_code=200)
        reply.json.return_value = body
        with mock.patch.object(mealie, "mealie_url", return_value="https://m.example"), \
                mock.patch.object(mealie, "auth_headers", return_value={}), \
                mock.patch.object(mealie.requests, "get", return_value=reply):
            recipe = mealie.get_recipe("stroganoff")
        self.assertEqual([i["food"]["name"] for i in recipe["recipeIngredient"]], ["ui", "melk"])
        self.assertEqual(recipe["recipeIngredient"][1]["unit"]["name"], "ml")


if __name__ == "__main__":
    unittest.main()
