"""De boodschappenlijst en de controlelijst van het weekmenu (static/shopping.js, 4.11.0): de dagen waarvoor je boodschappen doet,
wat in huis is, het totaal, wat er naar het mandje gaat en wat de server moet onthouden, en het terugzetten na herladen.

De functies draaien hier echt, in Node. Zonder Node slaan de tests over.
"""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
SHOPPING_JS = (ROOT / "static" / "shopping.js").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")


def function_source(source, name):
    match = re.search(rf"^(?:async )?function {re.escape(name)}\(.*?^}}$", source, re.S | re.M)
    if not match:
        raise AssertionError(f"functie {name} niet gevonden")
    return match.group(0)


def run_node(test, parts):
    result = subprocess.run(["node", "-e", "\n".join(parts)], capture_output=True, text=True, timeout=30, check=False)
    test.assertEqual(result.returncode, 0, result.stderr)
    return json.loads(result.stdout.strip().splitlines()[-1])


WEEK = ["2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30", "2026-10-31", "2026-11-01"]


@unittest.skipUnless(shutil.which("node"), "Node is niet geïnstalleerd")
class ShoppingDaysTest(unittest.TestCase):
    def run_js(self, scenario):
        names = ("shopDates", "shopDefaultDays", "shopActiveDays")
        return run_node(self, [f"const WEEK = {json.dumps(WEEK)};", *(function_source(SHOPPING_JS, name) for name in names), scenario])

    def test_only_days_with_ingredients_can_be_chosen(self):
        out = self.run_js(
            """
            const meal = (date, count) => ({ date, recipeDetail: { recipeIngredient: Array(count).fill({}) } });
            console.log(JSON.stringify(shopDates([meal("2026-10-28", 2), meal("2026-10-26", 1), meal("2026-10-28", 3), { date: "2026-10-27" }, meal("2026-10-29", 0)])));
            """
        )
        self.assertEqual(out, ["2026-10-26", "2026-10-28"], "een notitie of een recept zonder ingrediënten telt niet")

    def test_days_that_are_over_do_not_count_in_the_current_week(self):
        out = self.run_js(
            """
            const dates = ["2026-10-26", "2026-10-27", "2026-10-28", "2026-10-30"];
            console.log(JSON.stringify({
              wednesday: shopDefaultDays(dates, "2026-10-28", WEEK),
              sunday: shopDefaultDays(dates, "2026-11-01", WEEK),        // alles is voorbij: dan toch alles
              otherWeek: shopDefaultDays(dates, "2026-10-21", WEEK),     // volgende week plannen: alles
            }));
            """
        )
        self.assertEqual(out["wednesday"], ["2026-10-28", "2026-10-30"])
        self.assertEqual(out["sunday"], ["2026-10-26", "2026-10-27", "2026-10-28", "2026-10-30"])
        self.assertEqual(out["otherWeek"], ["2026-10-26", "2026-10-27", "2026-10-28", "2026-10-30"])

    def test_your_own_choice_wins_and_a_new_day_follows_the_default(self):
        out = self.run_js(
            """
            const dates = ["2026-10-26", "2026-10-28", "2026-10-30", "2026-10-31"];
            const picks = { "2026-10-26": true, "2026-10-30": false };   // maandag toch mee, vrijdag niet
            console.log(JSON.stringify(shopActiveDays(dates, "2026-10-28", WEEK, picks)));
            """
        )
        self.assertEqual(out, ["2026-10-26", "2026-10-28", "2026-10-31"], "zaterdag is later gepland en volgt de standaard")


@unittest.skipUnless(shutil.which("node"), "Node is niet geïnstalleerd")
class ShoppingItemsTest(unittest.TestCase):
    def test_the_pantry_is_off_by_default_and_a_tick_of_this_week_wins(self):
        parts = [
            function_source(APP_JS, "foldText"),
            *(function_source(SHOPPING_JS, name) for name in ("shopQuery", "shopFold", "shopItems")),
            """
            const ingredients = [
              { display: "2 el Olijfolie", food: { name: "Olijfolie" } },
              { display: "1 tl zout", food: { name: "Zout" } },
              { display: "400 g spaghetti" },                           // niet ontleed door Mealie: de hele regel is de naam
              { display: "250 ml crème fraîche", food: { name: "Crème  Fraîche" } },
            ];
            const pantry = new Map([["olijfolie", "Olijfolie"], ["zout", "zout"], ["creme fraiche", "crème fraîche"]]);
            const items = shopItems(ingredients, pantry, { zout: true, "400 g spaghetti": false });
            console.log(JSON.stringify(items.map(({ key, name, label, pantry, checked }) => ({ key, name, label, pantry, checked }))));
            """,
        ]
        out = run_node(self, parts)
        self.assertEqual(out[0], {"key": "olijfolie", "name": "Olijfolie", "label": "2 el Olijfolie", "pantry": True, "checked": False})
        self.assertEqual((out[1]["pantry"], out[1]["checked"]), (True, True), "in huis, maar deze week toch kopen")
        self.assertEqual((out[2]["key"], out[2]["pantry"], out[2]["checked"]), ("400 g spaghetti", False, False))
        self.assertEqual((out[3]["key"], out[3]["pantry"]), ("creme fraiche", True), "accenten en dubbele spaties tellen niet")

    def test_only_ticked_ingredients_are_searched(self):
        add = function_source(SHOPPING_JS, "addToCart")
        self.assertIn("shopNow().items.filter((item) => item.checked)", add)
        self.assertIn("requests.slice(at, at + CART_CHUNK)", add, "in groepen, met de stand ertussen")
        self.assertIn("\nconst CART_CHUNK = 6;", SHOPPING_JS, "gelijk aan CART_PREVIEW_WORKERS")
        self.assertIn("CART_PREVIEW_WORKERS: int = 6", (ROOT / "mealieshopper" / "app.py").read_text(encoding="utf-8"))


# Twee gevonden regels, een niet gevonden regel en een regel die nog gezocht wordt.
ROWS = """
const candidate = (productId, price, packs, extra = {}) => ({ productId, title: `Product ${productId}`, unitSize: "500 g", image: "https://img/x.jpg", price, packs, ...extra });
const item = (query) => ({ query, display: `1 ${query}`, quantity: 1, unit: null });
const rows = [
  reviewFill(reviewNewRow(item("spaghetti")), { candidates: [candidate("a", 1.49, 1, { suggested: true }), candidate("b", 2.59, 1)] }),
  reviewFill(reviewNewRow(item("pesto")), { candidates: [candidate("c", 2.29, 2, { doubt: "Verpakkingsgrootte onbekend" }), candidate("d", 1.0, 1)] }),
  reviewFill(reviewNewRow(item("paksoi")), { candidates: [] }),
  reviewNewRow(item("ui")),
];
"""


@unittest.skipUnless(shutil.which("node"), "Node is niet geïnstalleerd")
class ReviewModelTest(unittest.TestCase):
    NAMES = ("cartSelection", "reviewNewRow", "reviewFill", "reviewGroup", "reviewTotal", "reviewSelection", "reviewCounts")

    def run_js(self, scenario):
        return run_node(self, [*(function_source(SHOPPING_JS, name) for name in self.NAMES), ROWS, scenario])

    def test_rows_are_grouped_by_what_needs_a_look(self):
        out = self.run_js(
            """
            console.log(JSON.stringify({ groups: rows.map(reviewGroup), open: rows.map((row) => Boolean(row.open)), counts: reviewCounts(rows) }));
            """
        )
        self.assertEqual(out["groups"], ["fine", "attention", "attention", "pending"])
        self.assertEqual(out["open"], [False, True, False, False], "bij twijfel staan de opties er meteen")
        self.assertEqual(out["counts"], {"total": 4, "pending": 1, "skipped": 0, "missing": 1, "included": 2, "doubts": 1, "attention": 2, "fine": 1})

    def test_a_row_stays_in_its_group_when_you_choose_or_search(self):
        out = self.run_js(
            """
            rows[1].pick = 1;                                             // de optie zonder twijfel
            const afterPick = reviewGroup(rows[1]);
            reviewFill(rows[2], { candidates: [candidate("e", 0.99, 1)] }, { searched: true });
            console.log(JSON.stringify({ afterPick, afterSearch: reviewGroup(rows[2]), counts: reviewCounts(rows) }));
            """
        )
        self.assertEqual((out["afterPick"], out["afterSearch"]), ("attention", "attention"), "een regel verspringt niet onder je vinger")
        self.assertEqual((out["counts"]["doubts"], out["counts"]["missing"], out["counts"]["included"]), (0, 0, 3))

    def test_the_total_follows_the_choice_the_packs_and_the_ticks(self):
        out = self.run_js(
            """
            const first = reviewTotal(rows);                              // 1,49 + 2 x 2,29
            rows[0].pick = 1; rows[0].packs = 3;                          // 3 x 2,59
            const changed = reviewTotal(rows);
            rows[1].include = false;
            const unticked = reviewTotal(rows);
            rows[0].candidates[1].price = null;                           // een winkel zonder prijs telt niet mee
            console.log(JSON.stringify({ first, changed, unticked, unknown: reviewTotal(rows) }));
            """
        )
        self.assertEqual(out, {"first": 6.07, "changed": 12.35, "unticked": 7.77, "unknown": 0})

    def test_only_your_own_choice_is_remembered_under_the_ingredient(self):
        out = self.run_js(
            """
            rows[0].pick = 1; rows[0].packs = "250";                      // een andere optie, en een aantal buiten 1 tot 99
            reviewFill(rows[2], { candidates: [candidate("e", 0.99, 1)] }, { searched: true });
            rows[2].term = "pak choi";                                    // zelf gezocht onder een andere naam
            const selection = reviewSelection(rows);
            console.log(JSON.stringify(selection.map(({ query, productId, quantity, remember }) => ({ query, productId, quantity, remember: remember === true }))));
            """
        )
        self.assertEqual(out, [
            {"query": "spaghetti", "productId": "b", "quantity": 99, "remember": True},
            {"query": "pesto", "productId": "c", "quantity": 2, "remember": False},
            {"query": "paksoi", "productId": "e", "quantity": 1, "remember": True},
        ], "het voorstel onthouden we niet; de zoekterm pak choi verandert het ingrediënt paksoi niet")

    def test_what_was_not_searched_is_counted_apart(self):
        out = self.run_js(
            """
            Object.assign(rows[3], { state: "missing", attention: true, skipped: true, error: "Nog niet gezocht." });
            const stopped = reviewCounts(rows);
            reviewFill(rows[3], { candidates: [] }, { searched: true });   // alsnog gezocht, niets gevonden
            console.log(JSON.stringify({ stopped: [stopped.pending, stopped.skipped, stopped.missing], after: reviewCounts(rows).skipped }));
            """
        )
        self.assertEqual(out, {"stopped": [0, 1, 2], "after": 0})


# De opslag en de pagina rond het bewaren en terugzetten van de controlelijst.
STORAGE = r"""
const store = {};
const window = { sessionStorage: { getItem: (key) => (key in store ? store[key] : null), setItem: (key, value) => { store[key] = String(value); }, removeItem: (key) => { delete store[key]; } } };
const STORES = { ah: { api: "ah" } };
const state = { weekOffset: 0 };
let week = "2026-10-26";
const getWeekBounds = () => ({ start: week });
const t = (key) => key;
let drawn = 0;
function renderCartReview() { drawn += 1; }
const REVIEW_STORAGE_KEY = "ms-cart-review";
const REVIEW_MAX_AGE_MS = 12 * 60 * 60 * 1000;
const shopping = { review: null };
"""


@unittest.skipUnless(shutil.which("node"), "Node is niet geïnstalleerd")
class ReviewStorageTest(unittest.TestCase):
    def run_js(self, scenario):
        names = ("shopRead", "shopWrite", "reviewSave", "restoreCartReview")
        return run_node(self, [STORAGE, *(function_source(SHOPPING_JS, name) for name in names), scenario])

    def test_the_list_comes_back_after_a_reload(self):
        out = self.run_js(
            """
            shopping.review = { week, store: "ah", searching: true, stopped: false, rows: [
              { query: "spaghetti", state: "found", candidates: [{ productId: "a" }], pick: 0, packs: 2, include: true, open: true },
              { query: "ui", state: "pending", candidates: [] },
            ] };
            reviewSave();
            const saved = JSON.parse(store[REVIEW_STORAGE_KEY]);
            shopping.review = null;                                        // herladen: het geheugen is leeg
            restoreCartReview();
            console.log(JSON.stringify({ saved: Object.keys(saved).sort(), open: "open" in saved.rows[0], review: shopping.review, drawn }));
            """
        )
        self.assertEqual(out["saved"], ["rows", "savedAt", "store", "week"])
        self.assertFalse(out["open"], "of een regel openstond, hoort bij het scherm")
        self.assertEqual(out["drawn"], 1)
        rows = out["review"]["rows"]
        self.assertEqual((rows[0]["packs"], rows[0]["state"]), (2, "found"))
        self.assertEqual((rows[1]["state"], rows[1]["skipped"], rows[1]["error"]), ("missing", True, "cart.not_searched"), "wat nog gezocht werd, is niet gezocht")

    def test_another_week_an_old_list_or_an_unknown_store_is_not_shown(self):
        out = self.run_js(
            """
            const rows = [{ query: "ui", state: "found", candidates: [{ productId: "a" }] }];
            const tries = {};
            const attempt = (name, saved, currentWeek = "2026-10-26") => {
              store[REVIEW_STORAGE_KEY] = JSON.stringify(saved); shopping.review = null; week = currentWeek; drawn = 0;
              restoreCartReview();
              tries[name] = drawn;
            };
            attempt("fresh", { week: "2026-10-26", store: "ah", rows, savedAt: Date.now() });
            attempt("otherWeek", { week: "2026-10-26", store: "ah", rows, savedAt: Date.now() }, "2026-11-02");
            const kept = Boolean(shopping.review);                         // bewaard voor als je terugbladert
            week = "2026-10-26"; restoreCartReview(); const back = drawn;
            attempt("old", { week: "2026-10-26", store: "ah", rows, savedAt: Date.now() - 13 * 60 * 60 * 1000 });
            attempt("unknownStore", { week: "2026-10-26", store: "lidl", rows, savedAt: Date.now() });
            attempt("broken", { week: "2026-10-26", store: "ah", rows: "x", savedAt: Date.now() });
            console.log(JSON.stringify({ tries, kept, back }));
            """
        )
        self.assertEqual(out["tries"], {"fresh": 1, "otherWeek": 0, "old": 0, "unknownStore": 0, "broken": 0})
        self.assertTrue(out["kept"])
        self.assertEqual(out["back"], 1, "terugbladeren naar de week zet de lijst terug")

    def test_adding_or_discarding_clears_the_saved_copy(self):
        out = self.run_js(
            """
            shopping.review = { week, store: "ah", rows: [] };
            reviewSave();
            const before = REVIEW_STORAGE_KEY in store;
            shopping.review = null;
            reviewSave();
            console.log(JSON.stringify({ before, after: REVIEW_STORAGE_KEY in store }));
            """
        )
        self.assertEqual(out, {"before": True, "after": False})
        self.assertIn("shopping.review = null;\n  reviewSave();\n  showCartResult(data, notFound, store);", function_source(SHOPPING_JS, "submitCart"))
        discard = function_source(SHOPPING_JS, "discardCartReview")
        self.assertIn("reviewAbort?.abort();", discard, "een lopende zoekronde stopt mee")
        self.assertIn("reviewSave();", discard)


@unittest.skipUnless(shutil.which("node"), "Node is niet geïnstalleerd")
class ReviewRowHtmlTest(unittest.TestCase):
    PAGE = r"""
    const t = (key, params = {}) => key + (Object.keys(params).length ? JSON.stringify(params) : "");
    const escapeHtml = (value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll('"', "&quot;");
    const mdiIcon = () => "<svg></svg>";
    const storeImage = (url) => url;
    const MDI_LOADING = "", MDI_MINUS = "", MDI_PLUS = "", ICON_EXTERNAL = "";
    const cartCandidateFacts = (candidate) => [candidate.unitSize, `€${candidate.price}`];
    const reviewCheaperHtml = () => "";
    """

    def run_js(self, scenario):
        names = ("reviewSearchHtml", "reviewThumbHtml", "reviewOptionsHtml", "reviewRowHtml")
        return run_node(self, [self.PAGE, *(function_source(SHOPPING_JS, name) for name in names), scenario])

    def test_a_found_row_shows_the_product_as_a_button_with_a_stepper(self):
        out = self.run_js(
            """
            const row = { query: "spaghetti", display: "400 g <spaghetti>", state: "found", pick: 1, packs: 2, include: true, open: false, candidates: [
              { productId: "a", title: "Spaghetti 500 g", unitSize: "500 g", price: 1.49, suggested: true },
              { productId: "b", title: "Spaghetti 1 kg", unitSize: "1 kg", price: 2.59, remembered: true, url: "https://winkel/b" },
            ] };
            const closed = reviewRowHtml(row, 3);
            row.open = true;
            console.log(JSON.stringify({ closed, open: reviewRowHtml(row, 3) }));
            """
        )
        closed, opened = out["closed"], out["open"]
        self.assertIn('data-row="3"', closed)
        self.assertNotIn("<select", closed, "geen keuzelijst van het systeem meer")
        self.assertIn('class="review-product review-chosen" type="button" aria-expanded="false"', closed)
        self.assertIn("<strong>Spaghetti 1 kg</strong>", closed)
        self.assertIn("400 g &lt;spaghetti>", closed, "de naam van het ingrediënt gaat nooit als HTML de pagina in")
        self.assertIn('class="qty__input" type="number" inputmode="numeric" min="1" max="99" step="1" value="2"', closed)
        self.assertEqual(closed.count("data-review-step="), 2)
        self.assertIn("cart.remembered", closed)
        self.assertIn('class="link-button review-forget"', closed)
        self.assertNotIn("review-options", closed)
        self.assertIn('aria-expanded="true"', opened)
        self.assertEqual(opened.count('type="radio" name="review-pick-3"'), 2)
        self.assertRegex(opened, r'name="review-pick-3" value="1" checked')
        self.assertIn("<fieldset", opened)
        self.assertIn("cart.suggested", opened)
        self.assertIn('class="review-search"', opened, "zelf zoeken staat onder de opties")

    def test_pending_and_missing_rows(self):
        out = self.run_js(
            """
            const pending = reviewRowHtml({ display: "1 ui", state: "pending", candidates: [] }, 0);
            const missing = reviewRowHtml({ query: "paksoi", term: "pak choi", display: "1 paksoi", state: "missing", candidates: [], error: "" }, 1);
            console.log(JSON.stringify({ pending, missing }));
            """
        )
        self.assertIn("is-pending", out["pending"])
        self.assertIn("cart.pending", out["pending"])
        self.assertIn("is-missing", out["missing"])
        self.assertIn("cart.not_found", out["missing"])
        self.assertIn('value="pak choi"', out["missing"], "het zoekveld houdt de term waar je zelf op zocht")


class ShoppingPageTest(unittest.TestCase):
    def test_the_script_is_loaded_after_app_js(self):
        scripts = re.findall(r"filename='([\w/]+\.js)'", INDEX_HTML)
        self.assertEqual(scripts.index("shopping.js"), scripts.index("app.js") + 1)

    def test_the_week_menu_has_the_jump_button_and_no_plain_list(self):
        self.assertIn('<button class="button button--secondary shop-jump" id="shop-jump" type="button" hidden></button>', INDEX_HTML)
        plan = function_source(APP_JS, "renderMealPlan")
        self.assertIn("renderShoppingList();", plan)
        self.assertIn("restoreCartReview();", plan)
        self.assertNotIn("<li>${escapeHtml(item.display", plan, "geen platte lijst meer")

    def test_the_list_is_a_checklist_with_a_pantry_button_and_day_chips(self):
        item = function_source(SHOPPING_JS, "shopItemHtml")
        self.assertIn('<input type="checkbox" data-shop-item=', item)
        self.assertIn("aria-pressed=", item)
        self.assertIn("aria-label=", item, "het huisje is alleen een icoon: de naam staat in het label")
        render = function_source(SHOPPING_JS, "renderShoppingList")
        self.assertIn('role="group" aria-labelledby="shop-days-label"', render)
        self.assertIn('data-shop-day="${escapeHtml(date)}" aria-pressed="${on}"', render)

    def test_touch_targets_in_the_list_and_the_review_are_44_px(self):
        for selector in (r"\.shop-item__pantry", r"\.review-qty \.qty__step,\n\.review-qty \.qty__input"):
            rule = re.search(selector + r" \{([^}]*)\}", CSS).group(1)
            self.assertIn("width: 44px;", rule, selector)
            self.assertIn("height: 44px;", rule, selector)
        self.assertRegex(CSS, r"\.shop-item__label \{[^}]*min-height: 44px;")
        self.assertRegex(CSS, r"@media \(prefers-reduced-motion: reduce\) \{\s*\.review-pending__icon \{\s*animation: none;")

    def test_not_shopping_this_week_is_not_only_a_tick(self):
        self.assertRegex(CSS, r"\.shop-item\.is-off \.shop-item__label span \{[^}]*text-decoration: line-through;")


if __name__ == "__main__":
    unittest.main()
