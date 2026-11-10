"""De kookweergave van een recept (static/recipe_view.js): ingrediënten afvinken, stappen als klaar markeren, de kookmodus en
"Scherm aan laten".

De functies draaien hier echt, in Node, met een nagemaakte localStorage, document en navigator.wakeLock. Zonder Node slaan
die tests over; de tekstcontroles (de pagina, de stijlen en de teksten) draaien altijd.
"""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from mealieshopper import ingredients

ROOT = Path(__file__).resolve().parents[1]
VIEW_JS = (ROOT / "static" / "recipe_view.js").read_text(encoding="utf-8")
SEARCH_JS = (ROOT / "static" / "recipe_search.js").read_text(encoding="utf-8")
APP_JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
HTML = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
NL = (ROOT / "static" / "i18n" / "nl.js").read_text(encoding="utf-8")
EN = (ROOT / "static" / "i18n" / "en.js").read_text(encoding="utf-8")

# Wat de functies van app.js en i18n.js nodig hebben, en een localStorage die in een Map bewaart.
PRELUDE = r"""
const I18N = { formatNumber: (value) => String(value) };
const t = (key, vars) => vars ? `${key}${JSON.stringify(vars)}` : key;
function mdiIcon(path, className = "mdi") { return `<svg class="${className}"><path d="${path}"/></svg>`; }
function escapeHtml(value) {
  return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");
}
function $() { return null; }
const store = new Map();
globalThis.localStorage = {
  getItem: (key) => (store.has(key) ? store.get(key) : null),
  setItem: (key, value) => store.set(key, String(value)),
  removeItem: (key) => store.delete(key),
  key: (index) => [...store.keys()][index] ?? null,
  get length() { return store.size; },
};
const out = (value) => process.stdout.write(JSON.stringify(value));
"""


def view_source():
    """De constanten en functies van recipe_view.js, zonder de luisteraars onderaan (die hebben een pagina nodig)."""
    consts = re.findall(r"^(?:const|let) \w+ = .*$", VIEW_JS, re.M)
    functions = re.findall(r"^(?:async )?function \w+\(.*?^}$", VIEW_JS, re.S | re.M)
    return "\n".join(consts) + "\n" + "\n".join(functions)


def recipe(**extra):
    model = {
        "key": "mealie:pasta-<pesto>",
        "title": "Pasta",
        "ingredients": [
            {"text": "400 g spaghetti", "amount": "400 g", "section": ""},
            {"text": "zout naar smaak", "amount": "", "section": ""},
            {"text": "1 bosje basilicum", "amount": "1 bosje", "section": "Voor de pesto"},
        ],
        "steps": [{"text": "Kook de pasta.", "title": ""}, {"text": "Maak de <pesto>.", "title": "Pesto"}, {"text": "Meng.", "title": ""}],
    }
    model.update(extra)
    return model


@unittest.skipUnless(shutil.which("node"), "Node is niet geïnstalleerd")
class RecipeViewJsTest(unittest.TestCase):
    def run_js(self, body):
        script = "\n".join([PRELUDE, view_source(), body])
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
        if result.returncode:
            self.fail(result.stderr[-2000:])
        return json.loads(result.stdout)

    # ---- De hoeveelheid vooraan een regel
    def test_the_amount_at_the_start_of_a_line(self):
        cases = {
            "400 g spaghetti": "400 g",
            "200g spaghetti": "200g",
            "1½ el olijfolie": "1½ el",
            "½ ui": "½",
            "2-3 teentjes knoflook": "2-3 teentjes",
            "2 tot 3 teentjes knoflook": "2 tot 3 teentjes",
            "snufje zout": "snufje",
            "een snufje zout": "een snufje",
            "2 x 400 g tomaten": "2 x 400 g",
            "1,5 l melk": "1,5 l",
            "3 eieren": "3",
            "ca. 200 ml water": "ca. 200 ml",
            "1 1/2 cups flour": "1 1/2 cups",
            "2 tbsp. olive oil": "2 tbsp.",
            "a pinch of salt": "a pinch",
            "3/4 cup sugar": "3/4 cup",
            "2 tomatoes": "2",
        }
        found = self.run_js(f"out(Object.fromEntries({json.dumps(list(cases))}.map((line) => [line, cookSplitAmount(line)])));")
        self.assertEqual(found, cases)

    def test_lines_that_stay_plain(self):
        lines = ["zout naar smaak", "7-up", "00-meel", "100% sinaasappelsap", "5-kruidenpoeder", "400 g", "4personen", "peper", "", "snufje"]
        found = self.run_js(f"out({json.dumps(lines)}.map((line) => cookSplitAmount(line)));")
        self.assertEqual(found, [""] * len(lines))

    def test_a_unit_from_mealie_counts_too(self):
        found = self.run_js('out([cookSplitAmount("2 pondjes gehakt", ["pondjes"]), cookSplitAmount("1 fl oz rum", ["fl oz"]), cookSplitAmount("2 gehakt", ["g"])]);')
        self.assertEqual(found, ["2 pondjes", "1 fl oz", "2"])

    def test_the_units_include_those_of_the_shopping_list(self):
        # Wat ingredients.py als eenheid kent, maakt de kookweergave ook vet.
        line = re.search(r'^const COOK_UNITS = new Set\("([^"]+)"', VIEW_JS, re.M).group(1)
        self.assertLessEqual(set(ingredients.UNITS), set(line.split()))

    # ---- De twee bronnen
    def test_a_recipe_from_the_detail_screen(self):
        data = {
            "id": 42, "name": "Pasta",
            "ingredients": [{"line": "200 g pasta", "section": "Pasta"}, {"line": "  "}, {"line": " 1   el olie "}, {"line": "", "section": "Saus"}, {"line": "1 ui"}],
            "steps": ["Kook.", "  ", "Bak.\nRoer."],
            "stepTitles": ["Koken", "Leeg", ""],
        }
        model = self.run_js(f"out(cookRecipeFromStore('jumbo', {json.dumps(data)}));")
        self.assertEqual(model["key"], "jumbo:42")
        self.assertEqual([(i["text"], i["amount"], i["section"]) for i in model["ingredients"]],
                         [("200 g pasta", "200 g", "Pasta"), ("1 el olie", "1 el", ""), ("1 ui", "1", "Saus")])
        self.assertEqual([(step["text"], step["title"]) for step in model["steps"]], [("Kook.", "Koken"), ("Bak.\nRoer.", "")])
        self.assertEqual((model["slug"], model["linkable"]), ("", False), "een recept van een winkel kan niet gekoppeld worden")

    def test_a_recipe_from_mealie(self):
        detail = {
            "name": "Pasta",
            "recipeIngredient": [
                {"title": "Pasta"},
                {"display": "800 g  spaghetti", "unit": {"name": "gram", "abbreviation": "g"}},
                {"note": "zout naar smaak"},
                {},
                "kapot",
                {"display": "2 pond gehakt", "title": "Saus", "unit": {"name": "pond"}},
            ],
            "recipeInstructions": [{"text": "Kook de pasta.", "title": "Koken"}, {"text": "  "}, {"text": "Roer."}, None],
        }
        model = self.run_js(f"out(cookRecipeFromMealie({json.dumps(detail)}, 'mealie:pasta', 'Pasta pesto'));")
        self.assertEqual((model["key"], model["title"]), ("mealie:pasta", "Pasta pesto"))
        self.assertEqual([(i["text"], i["amount"], i["section"]) for i in model["ingredients"]],
                         [("800 g spaghetti", "800 g", "Pasta"), ("zout naar smaak", "", ""), ("2 pond gehakt", "2 pond", "Saus")])
        self.assertEqual([(step["text"], step["title"]) for step in model["steps"]], [("Kook de pasta.", "Koken"), ("Roer.", "")])

    # ---- De weergave
    def test_the_view(self):
        html = self.run_js(f"out(renderCookView({json.dumps(recipe())}, {{ headingLevel: 3 }}));")
        self.assertIn('data-cook-key="mealie:pasta-&lt;pesto&gt;"', html)
        self.assertIn('<span class="cook-ingredient__amount">400 g</span> spaghetti', html)
        self.assertEqual(html.count('<input type="checkbox" data-cook-tick='), 3)
        self.assertIn('<h4 class="cook__section">Voor de pesto</h4>', html)
        self.assertEqual(html.count('<ul class="cook-ingredients" role="list">'), 2, "een lijst per sectie")
        self.assertIn('<ol class="cook-steps" role="list">', html)
        self.assertIn('<h4 class="cook-step__title">Pesto</h4>', html)
        self.assertIn('<p class="cook-step__text">Maak de &lt;pesto&gt;.</p>', html)
        self.assertIn('data-cook-done="0" aria-pressed="false" aria-label="cook.step_toggle{&quot;n&quot;:&quot;1&quot;}"', html)
        self.assertEqual(len(re.findall(r'<h3 class="cook__heading">', html)), 2)
        self.assertIn("data-cook-start", html)
        self.assertIn("cook.progress{&quot;done&quot;:&quot;0&quot;,&quot;total&quot;:&quot;3&quot;}", html)
        # Nog niet begonnen: geen stap is "Nu", Opnieuw beginnen staat er niet.
        self.assertNotIn("is-current", html)
        self.assertRegex(html, r"data-cook-reset hidden>")

    def test_empty_parts_say_so_and_without_steps_there_is_no_cooking_mode(self):
        html = self.run_js(f"out(renderCookView({json.dumps(recipe(steps=[]))}, {{ headingLevel: 4, noIngredients: 'geen', noSteps: 'Geen <stappen>.', stepsNote: 'Voor 2.' }}));")
        self.assertIn('<p class="muted">Geen &lt;stappen&gt;.</p>', html)
        self.assertIn("Voor 2.", html)
        self.assertNotIn("data-cook-start", html)
        self.assertNotIn("data-cook-status", html)
        self.assertIn('<h4 class="cook__heading">', html)
        empty = self.run_js(f"out(renderCookView({json.dumps(recipe(steps=[], ingredients=[]))}, {{ noIngredients: 'Geen ingrediënten.' }}));")
        self.assertIn("Geen ingrediënten.", empty)
        self.assertNotIn("cook__bar", empty, "zonder ingrediënten en stappen valt er niets te koken")

    def test_the_view_shows_the_stored_progress(self):
        key = json.dumps("ms-cook-progress:" + recipe()["key"])
        html = self.run_js(
            f"store.set({key}, JSON.stringify({{ at: Date.now() - 1000, n: [3, 3], i: [0], s: [0] }}));"
            f"out(renderCookView({json.dumps(recipe())}));"
        )
        self.assertIn('data-cook-tick="0" checked', html)
        self.assertNotIn('data-cook-tick="1" checked', html)
        self.assertIn('class="cook-step is-done" data-cook-step="0"', html)
        self.assertIn('class="cook-step is-current" data-cook-step="1" aria-current="step"', html)
        self.assertIn('data-cook-done="0" aria-pressed="true"', html)
        self.assertIn("cook.progress{&quot;done&quot;:&quot;1&quot;,&quot;total&quot;:&quot;3&quot;}", html)
        self.assertIn('style="width: 33%"', html)
        self.assertRegex(html, r"data-cook-reset>")

    def test_the_screen_switch_only_where_the_browser_can(self):
        without = self.run_js(f"out(renderCookView({json.dumps(recipe())}));")
        self.assertRegex(without, r'data-cook-wake-row title="[^"]*" data-i18n-attr="title:cook.wake_hint" hidden>')
        self.assertNotIn("data-cook-wake checked", without)
        on = self.run_js(
            'Object.defineProperty(globalThis, "navigator", { value: { wakeLock: {} }, configurable: true });'
            'store.set("ms-cook-wake", "1");'
            f"out(renderCookView({json.dumps(recipe())}));"
        )
        self.assertRegex(on, r'data-i18n-attr="title:cook.wake_hint">')
        self.assertIn("data-cook-wake checked", on)

    # ---- De voortgang
    def test_progress_that_no_longer_counts(self):
        prefix = "ms-cook-progress:" + recipe()["key"]
        cases = {
            "verlopen": json.dumps({"at": "NOW - 13 * 3600000", "n": [3, 3], "i": [0], "s": [0]}),
            "ander recept": json.dumps({"at": "NOW", "n": [4, 3], "i": [0], "s": [0]}),
            "kapot": "{kapot",
            "leeg": "null",
        }
        body = []
        for name, raw in cases.items():
            raw = raw.replace('"NOW - 13 * 3600000"', "${Date.now() - 13 * 3600000}").replace('"NOW"', "${Date.now()}")
            body.append(f"store.set({json.dumps(prefix)}, `{raw}`); result[{json.dumps(name)}] = cookLoad(model).ingredients.size;")
        found = self.run_js(f"const model = {json.dumps(recipe())}; const result = {{}};" + "".join(body) + "out(result);")
        self.assertEqual(found, {name: 0 for name in cases})

    def test_progress_without_storage_stays_in_this_screen(self):
        found = self.run_js(
            'globalThis.localStorage = { getItem() { throw new Error("geen opslag"); }, setItem() { throw new Error("vol"); }, removeItem() { throw new Error("nee"); } };'
            f"const model = {json.dumps(recipe())};"
            "const progress = cookProgress(model); progress.steps.add(1); progress.at = Date.now(); cookSave(model, progress); cookPrune();"
            "out([...cookProgress(model).steps]);"
        )
        self.assertEqual(found, [1])

    def test_saving_and_clearing(self):
        found = self.run_js(
            f"const model = {json.dumps(recipe())};"
            "const progress = cookProgress(model); progress.ingredients.add(2); progress.ingredients.add(0); progress.at = 1234; cookSave(model, progress);"
            "const saved = JSON.parse(store.get('ms-cook-progress:' + model.key));"
            "progress.ingredients.clear(); cookSave(model, progress);"
            "out([saved, store.has('ms-cook-progress:' + model.key)]);"
        )
        self.assertEqual(found, [{"at": 1234, "n": [3, 3], "i": [0, 2], "s": []}, False])

    def test_pruning_removes_only_old_and_broken_progress(self):
        found = self.run_js(
            "store.set('ms-cook-progress:oud', JSON.stringify({ at: Date.now() - 13 * 3600000 }));"
            "store.set('ms-cook-progress:kapot', '{');"
            "store.set('ms-cook-progress:nieuw', JSON.stringify({ at: Date.now() }));"
            "store.set('ms-store', 'ah');"
            "cookPrune(); out([...store.keys()].sort());"
        )
        self.assertEqual(found, ["ms-cook-progress:nieuw", "ms-store"])

    def test_the_current_step(self):
        found = self.run_js(
            f"const model = {json.dumps(recipe())};"
            "const p = (i, s) => ({ ingredients: new Set(i), steps: new Set(s) });"
            "out([cookCurrentStep(model, p([], [])), cookCurrentStep(model, p([0], [])), cookCurrentStep(model, p([], [0, 2])), cookCurrentStep(model, p([], [0, 1, 2])),"
            "cookProgressText(model, p([], [0, 1, 2]))]);"
        )
        self.assertEqual(found, [-1, 0, 1, -1, "cook.progress_all"])

    # ---- De kookmodus
    def test_moving_through_the_steps(self):
        found = self.run_js(
            "out([cookModeMove(0, 3, 'next'), cookModeMove(2, 3, 'next'), cookModeMove(2, 3, 'forward'), cookModeMove(1, 3, 'prev'),"
            "cookModeMove(0, 3, 'prev'), cookModeMove(9, 3, 'show')]);"
        )
        self.assertEqual(found, [
            {"index": 1, "markDone": True, "close": False},
            {"index": 2, "markDone": True, "close": True},
            {"index": 2, "markDone": True, "close": False},
            {"index": 0, "markDone": False, "close": False},
            {"index": 0, "markDone": False, "close": False},
            {"index": 2, "markDone": False, "close": False},
        ])

    # ---- Scherm aan laten
    def test_the_screen_lock_follows_the_switch_the_page_and_the_recipe(self):
        found = self.run_js(r"""
const log = [];
let current = null;
Object.defineProperty(globalThis, "navigator", { configurable: true, value: { wakeLock: { request: async (type) => {
  log.push(`request:${type}`);
  const listeners = [];
  current = { released: false, addEventListener: (name, fn) => listeners.push(fn), release: async () => { log.push("release"); current.released = true; listeners.forEach((fn) => fn()); } };
  return current;
} } } });
const root = { shown: true, getClientRects() { return this.shown ? [1] : []; } };
globalThis.document = { visibilityState: "visible", querySelectorAll: () => [root] };
(async () => {
  await cookWakeUpdate();                       // de schakelaar staat uit
  cookWakeRemember(true); await cookWakeUpdate(); // aan: aanvragen
  await cookWakeUpdate();                       // al aan: niets
  root.shown = false; await cookWakeUpdate();   // het recept is uit beeld: vrijgeven
  root.shown = true; await cookWakeUpdate();
  current.release();                            // de browser laat los (tabblad verborgen)
  document.visibilityState = "hidden"; await cookWakeUpdate();
  document.visibilityState = "visible"; await cookWakeUpdate(); // terug: opnieuw aanvragen
  cookWakeRemember(false); await cookWakeUpdate();
  out([log, store.get("ms-cook-wake")]);
})();
""")
        self.assertEqual(found, [["request:screen", "release", "request:screen", "release", "request:screen", "release"], "0"])

    def test_a_refused_lock_is_no_error(self):
        found = self.run_js(r"""
Object.defineProperty(globalThis, "navigator", { configurable: true, value: { wakeLock: { request: async () => { throw new Error("NotAllowedError"); } } } });
globalThis.document = { visibilityState: "visible", querySelectorAll: () => [{ getClientRects: () => [1] }] };
cookWakeRemember(true);
cookWakeUpdate().then(() => out([cookWake.lock, cookWake.busy]));
""")
        self.assertEqual(found, [None, False])

    def test_leaving_during_a_request_releases_the_lock(self):
        found = self.run_js(r"""
const log = [];
const root = { shown: true, getClientRects() { return this.shown ? [1] : []; } };
Object.defineProperty(globalThis, "navigator", { configurable: true, value: { wakeLock: { request: async () => {
  root.shown = false; // terwijl de browser de lock geeft, ga je terug naar de lijst
  cookWakeUpdate();
  return { addEventListener() {}, release: async () => log.push("release") };
} } } });
globalThis.document = { visibilityState: "visible", querySelectorAll: () => [root] };
cookWakeRemember(true);
cookWakeUpdate().then(() => out([log, cookWake.lock]));
""")
        self.assertEqual(found, [["release"], None])

    def test_without_cooking_mode_the_ingredients_cannot_be_ticked(self):
        # Kookmodus uit (Instellingen, Weergave): de regels zijn gewone tekst, de balk verbergt styles.css.
        html = self.run_js(
            "globalThis.THEME = { appearance: () => ({ cookingMode: false }) };"
            f"out(renderCookView({json.dumps(recipe())}));"
        )
        ticks = re.findall(r'<input type="checkbox" data-cook-tick="\d+"[^>]*>', html)
        self.assertEqual(len(ticks), 3)
        self.assertTrue(all(" disabled" in tick for tick in ticks), ticks)
        self.assertFalse(self.run_js("globalThis.THEME = { appearance: () => ({ cookingMode: false }) }; out(cookEnabled());"))
        self.assertTrue(self.run_js("out(cookEnabled());"), "zonder theme.js: aan")
        on = self.run_js("globalThis.THEME = { appearance: () => ({ cookingMode: true }) };" f"out(renderCookView({json.dumps(recipe())}));")
        self.assertNotIn(" disabled", on)

PASTA = "aaaaaaaa-0000-4000-8000-000000000001"
OIL = "aaaaaaaa-0000-4000-8000-000000000002"
BASIL = "aaaaaaaa-0000-4000-8000-000000000003"
STEP_1 = "bbbbbbbb-0000-4000-8000-000000000001"
STEP_2 = "bbbbbbbb-0000-4000-8000-000000000002"


@unittest.skipUnless(shutil.which("node"), "Node is niet geïnstalleerd")
class StepIngredientsJsTest(RecipeViewJsTest):
    """Issue #309: bij een kookstap de ingrediënten die erbij horen; uit Mealie (ingredientReferences) of gevonden in de tekst."""

    MEALIE = {
        "name": "Pasta pesto",
        "recipeIngredient": [
            {"display": "400 g spaghetti", "referenceId": PASTA},
            {"display": "2 el olijfolie", "referenceId": OIL.upper()},
            {"display": "1 bosje basilicum", "referenceId": BASIL, "title": "Pesto"},
        ],
        "recipeInstructions": [
            {"id": STEP_1, "text": "Kook de pasta.", "ingredientReferences": [{"referenceId": PASTA}, {"referenceId": OIL}, {"referenceId": "kapot"}]},
            {"id": STEP_2, "text": "Pureer het basilicum met de olie.", "ingredientReferences": []},
            {"text": "Eet smakelijk."},
        ],
    }

    def test_the_links_of_mealie_and_the_rest_found_in_the_text(self):
        model = self.run_js(f"out(cookRecipeFromMealie({json.dumps(self.MEALIE)}, 'mealie:pasta-pesto', 'Pasta'));")
        self.assertEqual((model["slug"], model["linkable"]), ("pasta-pesto", True))
        self.assertEqual([item["ref"] for item in model["ingredients"]], [PASTA, OIL, BASIL])
        first, second, third = model["steps"]
        self.assertEqual((first["id"], first["refs"], first["uses"], first["auto"]), (STEP_1, [PASTA, OIL], [0, 1], False),
                         "gekoppeld in Mealie: ook als de tekst ze niet noemt")
        self.assertEqual((second["uses"], second["auto"]), ([1, 2], True), "niet gekoppeld: gevonden in de tekst (olie in olijfolie)")
        self.assertEqual((third["id"], third["uses"], third["auto"]), ("", [], False))

    def test_the_detail_screen_of_mealie_gives_the_same(self):
        data = {
            "id": "pasta-pesto", "slug": "pasta-pesto", "name": "Pasta",
            "ingredients": [{"line": "400 g spaghetti", "ref": PASTA}, {"line": "2 el olijfolie", "ref": OIL}, {"line": "1 bosje basilicum", "ref": BASIL}],
            "steps": ["Kook de pasta.", "Pureer het basilicum met de olie."],
            "stepIds": [STEP_1, STEP_2], "stepRefs": [[PASTA, OIL], []],
        }
        mealie = self.run_js(f"out(cookRecipeFromStore('mealie', {json.dumps(data)}));")
        self.assertEqual((mealie["slug"], mealie["linkable"]), ("pasta-pesto", True))
        self.assertEqual([step["uses"] for step in mealie["steps"]], [[0, 1], [1, 2]])
        # Dezelfde gegevens van een winkel: geen slug, dus niets te koppelen, wel gevonden in de tekst.
        store = self.run_js(f"out(cookRecipeFromStore('ah', {json.dumps(data)}));")
        self.assertEqual((store["slug"], store["linkable"]), ("", False))
        self.assertEqual([step["uses"] for step in store["steps"]], [[0, 1], [1, 2]])

    def test_finding_the_ingredients_in_the_text(self):
        lines = ["1 rode ui, gesnipperd", "2 tenen knoflook, fijngehakt", "400 g kipfiletblokjes", "200 ml kookroom", "zout en peper naar smaak",
                 "2 el olijfolie", "300 g verse spinazie", "2 tomaten", "3 eieren", "1 blik tomatenblokjes (400 g)", "250 g rundergehakt",
                 "300 g penne", "2 cloves garlic, minced", "1 cup chopped onions"]
        cases = {
            "Fruit de ui en de knoflook in de olie.": [0, 1, 5],
            "Bak de kipfiletblokjes rondom bruin.": [2],
            "Klop de eieren los en voeg de room toe.": [3, 8],
            "Snijd de tomaat in stukjes.": [7],
            "Voeg de tomatenblokjes toe.": [9],
            "Breng op smaak met zout en peper.": [4],
            "Laat de spinazie slinken.": [6],
            "Rul het gehakt.": [10],
            "Bewaar wat kookvocht van de pasta en giet de penne af.": [11],
            "Saute the garlic and the onion.": [12, 13],
            "Verwarm de oven voor op 200 °C.": [],
            "Dek af en laat rusten.": [],
        }
        script = f"""
          const recipe = cookRecipeFromStore('ah', {{ id: 1, name: 'x', ingredients: {json.dumps(lines)}, steps: {json.dumps(list(cases))} }});
          out(recipe.steps.map((step) => [step.text, step.uses, step.auto]));
        """
        for text, uses, auto in self.run_js(script):
            with self.subTest(text):
                self.assertEqual(uses, cases[text])
                self.assertEqual(auto, bool(uses))

    def test_stems(self):
        cases = {"uien": "ui", "ui": "ui", "tomaten": "tomat", "tomaat": "tomat", "eieren": "ei", "kippen": "kip", "boontjes": "bon", "bonen": "bon",
                 "paprika's": "paprika", "Onions": "onion", "olives": "oliv", "olive": "oliv", "tomatoes": "tomato", "Crème": "crem", "olie": "oli"}
        found = self.run_js(f"out(Object.fromEntries({json.dumps(list(cases))}.map((word) => [word, cookStem(word)])));")
        self.assertEqual(found, cases)

    def test_a_saved_link_counts_for_every_view_of_the_recipe(self):
        found = self.run_js(f"""
          const calls = [];
          globalThis.jsonFetch = async (url, options) => {{ calls.push([url, options.method, JSON.parse(options.body)]); return {{ refs: [{json.dumps(BASIL)}] }}; }};
          const week = cookRecipeFromMealie({json.dumps(self.MEALIE)}, 'mealie:pasta-pesto', 'Pasta');
          const search = cookRecipeFromMealie({json.dumps(self.MEALIE)}, 'mealie:pasta-pesto', 'Pasta');
          const other = cookRecipeFromMealie({json.dumps(self.MEALIE)}, 'mealie:iets-anders', 'Iets');
          cookViews.set(1, week); cookViews.set(2, search); cookViews.set(3, other);
          (async () => {{
            const saved = await cookSaveLinks(week, 1, [{json.dumps(BASIL)}]);
            const later = cookRecipeFromMealie({json.dumps(self.MEALIE)}, 'mealie:pasta-pesto', 'Pasta');
            out({{ calls, saved, week: week.steps[1], search: search.steps[1].uses, other: other.steps[1].uses, later: later.steps[1].uses }});
          }})();
        """)
        self.assertEqual(found["calls"], [[f"/api/mealie/recipes/pasta-pesto/steps/{STEP_2}/ingredients", "PUT", {"refs": [BASIL]}]])
        self.assertEqual(found["saved"], [BASIL])
        self.assertEqual((found["week"]["uses"], found["week"]["auto"]), ([2], False))
        self.assertEqual(found["search"], [2], "hetzelfde recept elders in de pagina")
        self.assertEqual(found["other"], [1, 2], "een ander recept blijft zoals het was")
        self.assertEqual(found["later"], [2], "een recept dat later opnieuw wordt opgebouwd (het weekmenu) heeft de nieuwe koppeling")

    def test_the_view_shows_the_ingredients_under_a_step(self):
        html = self.run_js(f"out(renderCookView(cookRecipeFromMealie({json.dumps(self.MEALIE)}, 'mealie:pasta-pesto', 'Pasta')));")
        uses = re.findall(r'<p class="cook-step__uses" data-cook-uses="(\d)"( hidden)?>(.*?)</p>', html, re.S)
        self.assertEqual([(index, bool(hidden)) for index, hidden, _ in uses], [("0", False), ("1", False), ("2", True)])
        self.assertIn("cook.uses: </span>", uses[0][2])
        self.assertIn("<b>400 g</b> spaghetti", uses[0][2])
        self.assertIn("cook.uses_found: </span>", uses[1][2], "automatisch gevonden staat er voor een schermlezer bij")

    def test_the_link_form_has_every_linkable_ingredient_with_the_current_ones_ticked(self):
        html = self.run_js(f"""
          const recipe = cookRecipeFromMealie({json.dumps(self.MEALIE)}, 'mealie:pasta-pesto', 'Pasta');
          recipe.ingredients.push(cookIngredient('zout', '', [], ''));
          out(cookLinkFormHtml(recipe, recipe.steps[0]));
        """)
        boxes = re.findall(r'<input type="checkbox" value="([^"]+)" data-cook-link-ref( checked)?>', html)
        self.assertEqual(boxes, [(PASTA, " checked"), (OIL, " checked"), (BASIL, "")], "zonder referenceId (zout) valt een regel weg")
        self.assertIn('<p class="cook-link__section">Pesto</p>', html)


class RecipeViewPageTest(unittest.TestCase):
    def test_the_script_loads_after_app_and_before_the_screens_that_use_it(self):
        scripts = re.findall(r"filename='([\w/]+\.js)'", HTML)
        self.assertLess(scripts.index("app.js"), scripts.index("recipe_view.js"))
        self.assertLess(scripts.index("recipe_view.js"), scripts.index("recipe_search.js"))
        self.assertEqual(scripts[-1], "router.js")

    def test_both_recipe_screens_use_the_cooking_view(self):
        self.assertIn("renderCookView(cookRecipeFromStore(store, recipe)", SEARCH_JS)
        self.assertIn("renderCookView(recipe, { headingLevel: 4", APP_JS)
        self.assertIn("cookRecipeFromMealie(detail, slug ? `mealie:${slug}`", APP_JS)
        for old in ("picnic-recipe__list", "mealRecipeLines"):
            self.assertNotIn(old, SEARCH_JS + APP_JS + CSS, old)
        # Terug naar de lijst en een recept openen kijken of het scherm nog aan moet.
        back = SEARCH_JS[SEARCH_JS.index("function showRecipeList"):]
        self.assertIn("cookWakeUpdate()", back[:back.index("\n}")])

    def test_the_cooking_mode_window(self):
        dialog = HTML[HTML.index('<dialog id="cook-mode"'):]
        dialog = dialog[:dialog.index("</dialog>")]
        self.assertIn('class="product-dialog cook-mode" aria-labelledby="cook-mode-title"', dialog)
        for element in ("cook-mode-title", "cook-mode-close", "cook-mode-ingredients", "cook-mode-meter", "cook-mode-stage", "cook-mode-step",
                        "cook-mode-counter", "cook-mode-step-title", "cook-mode-text", "cook-mode-done", "cook-mode-panel",
                        "cook-mode-ingredients-list", "cook-mode-prev", "cook-mode-next", "cook-mode-next-label"):
            self.assertIn(f'id="{element}"', dialog, element)
        self.assertIn('aria-live="polite"', dialog)
        self.assertIn('aria-controls="cook-mode-panel"', dialog)
        self.assertIn("data-cook-wake", dialog)
        # Niet in het venster van een maaltijd: dan kan de kookmodus er bovenop openen.
        meal = HTML[HTML.index('<dialog id="meal-recipe-dialog"'):]
        self.assertNotIn('id="cook-mode"', meal[:meal.index("</dialog>")])

    def test_storage_and_the_screen_lock_are_guarded(self):
        for function in re.findall(r"^(?:async )?function \w+\(.*?^}$", VIEW_JS, re.S | re.M):
            if "localStorage." in function:
                self.assertIn("try {", function, function.split("(")[0])
        self.assertIn('"wakeLock" in navigator', VIEW_JS)
        self.assertIn('document.addEventListener("visibilitychange", cookWakeUpdate)', VIEW_JS)
        self.assertIn('document.addEventListener("close", () => setTimeout(cookWakeUpdate), true)', VIEW_JS)
        self.assertIn('window.addEventListener("popstate"', VIEW_JS)

    def test_the_styles(self):
        toggle = re.search(r"\.cook-step__toggle \{(.*?)\}", CSS, re.S).group(1)
        self.assertIn("width: 44px", toggle)
        self.assertIn("height: 44px", toggle)
        self.assertIn("@container cook (min-width: 640px)", CSS)
        motion = CSS[CSS.index(".cook-mode__nav .button"):]
        motion = motion[motion.index("@media (prefers-reduced-motion: reduce)"):]
        self.assertIn(".cook-step,", motion[:motion.index("}\n}")])
        # Schermvullend zoals de achtergrond: ook over de ruimte die de pagina voor de scrollbalk vrijhoudt.
        self.assertIn("inset: 0", re.search(r"\.cook-mode \{(.*?)\}", CSS, re.S).group(1))

    def test_both_languages_have_the_texts(self):
        keys = sorted(set(re.findall(r'"(cook\.\w+)"', VIEW_JS + HTML)) | set(re.findall(r'data-i18n(?:-attr="[\w-]+:|=")(cook\.\w+)', VIEW_JS + HTML)))
        self.assertIn("cook.start", keys)
        for source in (NL, EN):
            for key in keys:
                self.assertIn(f'"{key}":', source, key)
        self.assertIn('"cook.wake": "Scherm aan laten"', NL)
        self.assertIn('"cook.wake": "Keep screen on"', EN)


if __name__ == "__main__":
    unittest.main()
