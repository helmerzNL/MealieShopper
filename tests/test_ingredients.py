import json
import os
import unittest
from unittest import mock

import httpx

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

from mealieshopper import ingredients, mealie  # noqa: E402
from helpers import routes, run, sent_json  # noqa: E402


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


def parsed(quantity, food, unit=None, note="", **food_extra):
    """Een regel die Mealie analyseerde, met eenheid en ingrediënt als object."""
    return {"quantity": quantity, "unit": unit, "food": {"name": food, **food_extra}, "note": note, "display": f"{quantity} {food}" if quantity else food}


GRAM = {"name": "gram", "abbreviation": "g", "useAbbreviation": True}


class ScaleTest(unittest.TestCase):
    """Porties uit de planner van Mealie (`recipeScale`): de hoeveelheden gaan maal de schaal."""

    def test_amount_unit_food_and_note(self):
        line = parsed(200, "bloem", GRAM, "gezeefd")
        scaled = ingredients.scale_ingredient(line, 2)
        self.assertEqual((scaled["quantity"], scaled["display"], scaled["unscaledQuantity"]), (400, "400 g bloem, gezeefd", 200))
        self.assertEqual(line["quantity"], 200, "de regel zelf verandert niet")
        self.assertEqual(scaled["food"], line["food"])

    def test_decimals_follow_the_language(self):
        line = parsed(0.75, "melk", {"name": "liter", "abbreviation": "l", "useAbbreviation": False})
        self.assertEqual(ingredients.scale_ingredient(line, 2)["display"], "1,5 liter melk")
        with mock.patch.object(ingredients, "current_lang", return_value="en"):
            self.assertEqual(ingredients.scale_ingredient(line, 2)["display"], "1.5 liter melk")

    def test_plurals_when_mealie_knows_them(self):
        self.assertEqual(ingredients.scale_ingredient(parsed(1, "ui", pluralName="uien"), 2)["display"], "2 uien")
        clove = {"name": "teen", "pluralName": "tenen", "abbreviation": ""}
        self.assertEqual(ingredients.scale_ingredient(parsed(1, "knoflook", clove, pluralName="knoflook"), 3)["display"], "3 tenen knoflook")
        self.assertEqual(ingredients.scale_ingredient(parsed(1, "ei"), 2)["display"], "2 ei", "zonder meervoud de naam zelf")

    def test_a_line_mealie_did_not_analyse(self):
        # Mealie bewaart zo'n regel met de tekst in de notitie; die is dan het ingrediënt, zonder komma.
        line = {"quantity": 2, "unit": None, "food": None, "note": "uien", "display": "2 uien"}
        self.assertEqual(ingredients.scale_ingredient(line, 2)["display"], "4 uien")
        normalized = ingredients.normalize(unparsed("200 ml melk"))
        self.assertEqual(ingredients.scale_ingredient(normalized, 1.5)["display"], "300 ml melk")

    def test_lines_without_an_amount_and_scale_one_stay_as_they_are(self):
        for quantity in (None, 0, -1, True, "2", float("nan")):
            line = parsed(quantity, "zout", note="naar smaak")
            self.assertIs(ingredients.scale_ingredient(line, 2), line, quantity)
        line = parsed(200, "bloem", GRAM)
        self.assertIs(ingredients.scale_ingredient(line, 1), line)
        self.assertEqual(ingredients.scale_ingredient("geen dict", 2), "geen dict")

    def test_recipe_is_copied(self):
        recipe = {"name": "Pasta", "recipeIngredient": [parsed(400, "spaghetti", GRAM), parsed(None, "peper")]}
        scaled = ingredients.scale_recipe(recipe, 1.5)
        self.assertEqual([i["display"] for i in scaled["recipeIngredient"]], ["600 g spaghetti", "peper"])
        self.assertEqual(recipe["recipeIngredient"][0]["quantity"], 400, "het recept zelf verandert niet")
        self.assertIs(ingredients.scale_recipe(recipe, 1), recipe)
        self.assertEqual(ingredients.scale_recipe({"name": "x"}, 2), {"name": "x"})
        self.assertIsNone(ingredients.scale_recipe(None, 2))

    def test_no_scaling_when_amounts_are_off_in_mealie(self):
        recipe = {"settings": {"disableAmount": True}, "recipeIngredient": [parsed(400, "spaghetti", GRAM)]}
        self.assertIs(ingredients.scale_recipe(recipe, 2), recipe)

    def test_normalize_display_is_unchanged(self):
        self.assertEqual(ingredients.normalize(unparsed("1 ui, fijngesneden"))["display"], "1 ui, fijngesneden")
        self.assertEqual(ingredients.normalize(unparsed("1,5 l water"))["display"], "1,5 l water")


class GetRecipeTest(unittest.TestCase):
    def test_get_recipe_normalises_the_ingredients(self):
        body = {"name": "Stroganoff", "recipeIngredient": [unparsed("1 ui"), unparsed("200 ml melk")]}
        with mock.patch.object(mealie, "_endpoint", new_callable=mock.AsyncMock, return_value=("https://m.example", "t")), routes() as api:
            api.get("https://m.example/api/recipes/stroganoff").mock(return_value=httpx.Response(200, json=body))
            recipe = run(mealie.get_recipe("stroganoff"))
        self.assertEqual([i["food"]["name"] for i in recipe["recipeIngredient"]], ["ui", "melk"])
        self.assertEqual(recipe["recipeIngredient"][1]["unit"]["name"], "ml")


class UnitNamesTest(unittest.TestCase):
    def test_lookup_names_cover_the_spellings_mealie_may_use(self):
        self.assertEqual(ingredients.unit_lookup_names("gr"), ["gr", "g", "gram"])
        self.assertEqual(ingredients.unit_lookup_names("EL"), ["el", "eetlepel"])
        self.assertEqual(ingredients.unit_lookup_names("snufje"), ["snufje"])

    def test_definition_of_a_new_unit(self):
        self.assertEqual(ingredients.unit_definition("gr"), {"name": "gram", "abbreviation": "g"})
        self.assertEqual(ingredients.unit_definition("snufje"), {"name": "snufje", "abbreviation": ""})


def page(items):
    return httpx.Response(200, json={"items": items, "total_pages": 1})


class ImportParseTest(unittest.TestCase):
    """Na de import krijgen de losse tekstregels in Mealie een eenheid en een ingrediënt."""

    UNITS = [{"id": "u-g", "name": "gram", "abbreviation": "g", "aliases": []}]
    FOODS = [{"id": "f-melk", "name": "melk", "pluralName": "", "aliases": []}]

    def run_parse(self, lines, units=None, foods=None):
        created = []

        def post(request):
            kind = request.url.path.rsplit("/", 1)[1]
            body = json.loads(request.content)
            created.append((kind, body))
            return httpx.Response(201, json={"id": f"new-{len(created)}", **body})

        with mock.patch.object(mealie, "_endpoint", new_callable=mock.AsyncMock, return_value=("http://m", "t")), routes() as api:
            api.get("http://m/api/units").mock(return_value=page(self.UNITS if units is None else units))
            api.get("http://m/api/foods").mock(return_value=page(self.FOODS if foods is None else foods))
            api.get("http://m/api/recipes/stroganoff").mock(return_value=httpx.Response(200, json={"recipeIngredient": lines}))
            api.post(url__regex=r"^http://m/api/(units|foods)$").mock(side_effect=post)
            patch = api.patch("http://m/api/recipes/stroganoff").mock(return_value=httpx.Response(200, json={}))
            slug = run(mealie.parse_imported_ingredients("stroganoff"))
        return slug, patch, created

    def test_lines_are_linked_to_existing_units_and_foods(self):
        slug, patch, created = self.run_parse([unparsed("500 gr melk")])
        self.assertEqual(slug, "stroganoff")
        self.assertEqual(created, [])
        line = sent_json(patch.calls.last)["recipeIngredient"][0]
        self.assertEqual((line["quantity"], line["unit"]["id"], line["food"]["id"], line["isFood"]), (500.0, "u-g", "f-melk", True))
        self.assertNotIn("analyzedByMealieShopper", line)

    def test_missing_units_and_foods_are_created_once(self):
        _, patch, created = self.run_parse([unparsed("1 snufje zout"), unparsed("2 snufje zout")])
        self.assertEqual(created, [("units", {"name": "snufje", "abbreviation": ""}), ("foods", {"name": "zout"})])
        lines = sent_json(patch.calls.last)["recipeIngredient"]
        self.assertEqual([line["unit"]["name"] for line in lines], ["snufje", "snufje"])
        self.assertEqual([line["food"]["name"] for line in lines], ["zout", "zout"])

    def test_a_recipe_that_needs_nothing_is_not_touched(self):
        analysed = {"quantity": 200, "unit": {"name": "ml"}, "food": {"name": "melk"}, "note": ""}
        slug, patch, _ = self.run_parse([analysed, unparsed("peper naar smaak")])
        self.assertEqual(slug, "stroganoff")
        self.assertFalse(patch.called)

    def test_untouched_lines_stay_in_the_update(self):
        analysed = {"quantity": 200, "unit": {"name": "ml"}, "food": {"name": "melk"}, "note": ""}
        _, patch, _ = self.run_parse([analysed, unparsed("500 g melk")])
        lines = sent_json(patch.calls.last)["recipeIngredient"]
        self.assertEqual(lines[0], analysed)
        self.assertEqual(lines[1]["unit"]["id"], "u-g")

    def test_a_failure_never_breaks_the_import(self):
        with mock.patch.object(mealie, "_endpoint", new_callable=mock.AsyncMock, return_value=("http://m", "t")), routes() as api:
            api.get("http://m/api/recipes/stroganoff").mock(side_effect=httpx.ConnectError("weg"))
            self.assertEqual(run(mealie.parse_imported_ingredients("stroganoff")), "stroganoff")


class ImportHookTest(unittest.TestCase):
    def test_every_import_path_ends_in_the_parse(self):
        with mock.patch.object(mealie, "_endpoint", new_callable=mock.AsyncMock, return_value=("http://m", "t")), routes() as api, \
                mock.patch.object(mealie, "parse_imported_ingredients", new_callable=mock.AsyncMock, return_value="geparsed") as parse:
            api.post(url__regex=r"^http://m/api/recipes/create/(url|html-or-json)$").mock(return_value=httpx.Response(200, json="slug"))
            self.assertEqual(run(mealie.import_via_url("https://x.nl/r")), "geparsed")
            self.assertEqual(run(mealie.import_via_jsonld("https://x.nl/r", {"name": "x"})), "geparsed")
        self.assertEqual(parse.await_count, 2)


if __name__ == "__main__":
    unittest.main()
