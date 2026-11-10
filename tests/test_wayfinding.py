"""Wegwijs (4.13.0), de vierde ronde uit docs/onderzoek-gebruikerservaring.md: de week in het adres, terug naar je plek, één
winkelkeuze, een aanbieding in je mandje, filters die blijven staan, de aankopenlijst in delen, Vernieuwen in de geïnstalleerde
app, snelkoppelingen, en Albert Heijn ontkoppelen.

De functies uit static/ draaien hier echt, in Node. Zonder Node slaan die tests over. De installatiehulp met de winkelkeuze staat
in tests/test_onboarding_ui.py, de volgorde van Koppelen in tests/test_link_menu.py.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("MEALIESHOPPER_DATA_DIR", tempfile.mkdtemp())

from mealieshopper import ah, auth  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402
from helpers import AppTestCase, run  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
ROUTER_JS = (STATIC / "router.js").read_text(encoding="utf-8")
OFFERS_JS = (STATIC / "offers.js").read_text(encoding="utf-8")
ORDERS_JS = (STATIC / "orders.js").read_text(encoding="utf-8")
VIEWS_JS = (STATIC / "purchase_views.js").read_text(encoding="utf-8")
PROFILE_JS = (STATIC / "profile.js").read_text(encoding="utf-8")
CSS = (STATIC / "styles.css").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")


def function_source(source, name):
    match = re.search(rf"^(?:async )?function {re.escape(name)}\(.*?^}}$", source, re.S | re.M)
    if not match:
        raise AssertionError(f"functie {name} niet gevonden")
    return match.group(0)


def run_node(test, parts, env=None):
    result = subprocess.run(["node", "-e", "\n".join(parts)], capture_output=True, text=True, timeout=30, check=False,
                            env={**os.environ, **(env or {})})
    test.assertEqual(result.returncode, 0, result.stderr)
    return json.loads(result.stdout.strip().splitlines()[-1])


# Een sessionStorage in het geheugen, voor de filters die blijven staan.
SESSION = r"""
const stored = {};
const window = { sessionStorage: {
  getItem: (key) => (key in stored ? stored[key] : null),
  setItem: (key, value) => { stored[key] = String(value); },
  removeItem: (key) => { delete stored[key]; },
} };
"""


@unittest.skipUnless(shutil.which("node"), "Node is niet geïnstalleerd")
class WeekInAddressTest(unittest.TestCase):
    def offsets(self, days, today, first_day=1):
        parts = [
            f"const state = {{ firstDayOfWeek: {first_day}, weekOffset: 0 }};",
            function_source(APP_JS, "weekStartOf"),
            function_source(APP_JS, "weekOffsetOf"),
            f"console.log(JSON.stringify({json.dumps(days)}.map((day) => weekOffsetOf(day, new Date('{today}T09:00:00')))));",
        ]
        return run_node(self, parts, env={"TZ": "Europe/Amsterdam"})

    def test_a_day_in_the_address_gives_the_week_it_is_in(self):
        # Woensdag 28 oktober 2026; de week begint op maandag de 26e.
        days = ["2026-10-26", "2026-11-01", "2026-11-02", "2026-11-08", "2026-10-19", "2026-10-25"]
        self.assertEqual(self.offsets(days, "2026-10-28"), [0, 0, 1, 1, -1, -1])

    def test_a_week_that_starts_on_sunday_counts_from_sunday(self):
        self.assertEqual(self.offsets(["2026-10-25", "2026-11-01", "2026-10-31"], "2026-10-28", first_day=0), [0, 1, 0])

    def test_the_clock_change_does_not_shift_a_week(self):
        # 29 maart 2026: de zomertijd begint, die week heeft een uur minder.
        self.assertEqual(self.offsets(["2026-03-30", "2026-03-23", "2026-04-06"], "2026-03-25"), [1, 0, 2])

    def test_nonsense_in_the_address_is_this_week_and_far_away_is_capped(self):
        self.assertEqual(self.offsets(["", "volgende", "2026-13-45", "2026-1-5", "2040-01-01", "1990-01-01"], "2026-10-28"),
                         [0, 0, 0, 0, 104, -104])

    def test_this_week_has_no_parameter_and_another_week_its_first_day(self):
        parts = [
            "const state = { weekOffset: 0 };",
            "const getWeekBounds = (offset) => ({ start: offset === 2 ? '2026-11-09' : 'x' });",
            function_source(APP_JS, "weekParam"),
            "const out = [weekParam()]; state.weekOffset = 2; out.push(weekParam()); console.log(JSON.stringify(out));",
        ]
        self.assertEqual(run_node(self, parts), ["", "2026-11-09"])

    def test_the_address_of_the_week_menu_and_offers_carries_the_week(self):
        path = function_source(ROUTER_JS, "routerCurrentPath")
        self.assertIn("return offersPath(sub, { store, week: weekParam() });", path)
        self.assertIn('${root === "recipes" && !sub ? routerQuery({ week: weekParam() }) : ""}', path)
        self.assertNotIn("week", path.split('sub !== "history"')[0], "de prijsgeschiedenis heeft geen week")
        # Een andere week vervangt het adres; hij wordt geen stap in de geschiedenis per week.
        self.assertIn('if (typeof routerSync === "function") routerSync({ replace: true });', function_source(APP_JS, "changePlanWeek"))
        self.assertIn("routerSync({ replace: true })", function_source(OFFERS_JS, "offersChangeWeek"))
        for button, call in (("this-week", "changePlanWeek(0)"), ("prev-week", "changePlanWeek(state.weekOffset - 1)"),
                             ("next-week", "changePlanWeek(state.weekOffset + 1)")):
            self.assertIn(f'$("#{button}").addEventListener("click", () => {call});', APP_JS)

    def test_opening_an_address_sets_the_week_once(self):
        parts = [
            "const state = { weekOffset: 0 }; const set = [];",
            "const weekOffsetOf = (day) => ({ '2026-11-09': 2, '': 0 })[day] ?? 0;",
            "const setPlanWeek = (offset) => { state.weekOffset = offset; set.push(offset); };",
            function_source(ROUTER_JS, "routerApplyWeek"),
            "routerApplyWeek(new URLSearchParams('week=2026-11-09'));",
            "routerApplyWeek(new URLSearchParams('week=2026-11-09'));",  # dezelfde week: niet opnieuw laden
            "routerApplyWeek(new URLSearchParams(''));",  # Terug naar het adres zonder week: deze week
            "console.log(JSON.stringify(set));",
        ]
        self.assertEqual(run_node(self, parts), [2, 0])
        self.assertIn('if (root === "recipes" && !parts[1]) routerApplyWeek(url.searchParams);', ROUTER_JS)
        self.assertIn('if (sub !== "history") routerApplyWeek(params);', function_source(ROUTER_JS, "routerApplyOffers"))

    def test_a_late_first_day_of_the_week_recounts_the_week_in_the_address(self):
        # De eerste dag van de week komt van Mealie, na het openen van het adres: de week wordt dan opnieuw geteld.
        body = function_source(APP_JS, "applyWeekStart")
        self.assertLess(body.index("state.firstDayOfWeek = day;"), body.index('state.weekOffset = weekOffsetOf(params.get("week"));'))
        self.assertLess(body.index('state.weekOffset = weekOffsetOf(params.get("week"));'), body.index("updateWeekLabel();"))


@unittest.skipUnless(shutil.which("node"), "Node is niet geïnstalleerd")
class BackToYourPlaceTest(unittest.TestCase):
    PAGE = r"""
const calls = []; const timers = [];
const history = { state: { ms: true, other: 1 }, replaceState(state) { this.state = state; calls.push(['replace', state]); } };
const listeners = {};
const window = { scrollY: 0, height: 0,
  scrollTo({ top }) { this.scrollY = Math.min(top, this.height); calls.push(['scroll', top]); },
  addEventListener: (name, callback) => { listeners[name] = callback; },
  removeEventListener: (name) => { delete listeners[name]; } };
const setTimeout = (callback, wait) => { timers.push({ callback, wait, on: true }); return timers.length - 1; };
const clearTimeout = (id) => { if (timers[id]) timers[id].on = false; };
const tick = () => { const next = timers.find((timer) => timer.on); if (!next) return false; next.on = false; next.callback(); return true; };
"""

    def scroll_source(self):
        start = ROUTER_JS.index("const ROUTER_SCROLL_TRIES")
        return ROUTER_JS[start:ROUTER_JS.index("function routerRestoreScroll")] + function_source(ROUTER_JS, "routerRestoreScroll")

    def test_leaving_a_screen_keeps_where_you_were(self):
        parts = [self.PAGE, function_source(ROUTER_JS, "routerRememberScroll"),
                 "window.scrollY = 1234.6; routerRememberScroll(); console.log(JSON.stringify(history.state));"]
        self.assertEqual(run_node(self, parts), {"ms": True, "other": 1, "scroll": 1235})
        sync = function_source(ROUTER_JS, "routerSync")
        self.assertIn("if (!replace) routerRememberScroll();", sync, "alleen bij een nieuwe stap, niet bij het vervangen van het adres")
        navigate = function_source(ROUTER_JS, "routerNavigate")
        self.assertLess(navigate.index("routerRememberScroll();"), navigate.index("history.pushState("))

    def test_back_returns_to_the_place_once_the_page_is_long_enough(self):
        # De lijst laadt nog: de pagina is eerst te kort. Na de tweede poging is ze lang genoeg en stopt het proberen.
        parts = [self.PAGE, self.scroll_source(), r"""
routerRestoreScroll(900);
tick(); window.height = 2000; tick();
const left = timers.filter((timer) => timer.on).length;
console.log(JSON.stringify({ scrolls: calls.filter(([kind]) => kind === 'scroll').length, at: window.scrollY, left, listening: Object.keys(listeners).length }));
"""]
        self.assertEqual(run_node(self, parts), {"scrolls": 2, "at": 900, "left": 0, "listening": 0})

    def test_scrolling_yourself_stops_the_return(self):
        parts = [self.PAGE, self.scroll_source(), r"""
routerRestoreScroll(900);
tick(); listeners.touchstart();
console.log(JSON.stringify({ left: timers.filter((timer) => timer.on).length, top: timers.length }));
"""]
        self.assertEqual(run_node(self, parts), {"left": 0, "top": 5})

    def test_the_top_of_a_screen_needs_no_return(self):
        parts = [self.PAGE, self.scroll_source(), "routerRestoreScroll(0); console.log(JSON.stringify(timers.length));"]
        self.assertEqual(run_node(self, parts), 0)

    def test_back_and_a_screen_change_put_the_focus_on_the_content(self):
        popstate = ROUTER_JS[ROUTER_JS.index('window.addEventListener("popstate"'):]
        self.assertLess(popstate.index("routerApply(window.location.href);"), popstate.index("routerRestoreScroll(Number(history.state?.scroll) || 0);"))
        self.assertIn('document.querySelector("#main")?.focus({ preventScroll: true });', popstate)
        panel = function_source(APP_JS, "showPanel")
        self.assertIn('if (typeof router !== "undefined" && router.started && !router.applying) document.querySelector("#main")?.focus({ preventScroll: true });',
                      panel, "niet bij het openen van de app en niet terwijl de router een adres toepast")
        self.assertRegex(INDEX_HTML, r'<main class="shell" id="main" tabindex="-1">')
        self.assertRegex(CSS, r"#main:focus\s*{\s*outline: none;", "de inhoud is geen bediening: geen focusrand om de hele pagina")


@unittest.skipUnless(shutil.which("node"), "Node is niet geïnstalleerd")
class OfferToBasketTest(unittest.TestCase):
    PAGE = r"""
const STORES = { ah: { name: "Albert Heijn", api: "ah" }, picnic: { name: "Picnic", api: "picnic" }, jumbo: { name: "Jumbo", api: "jumbo" }, crisp: { name: "Crisp", api: "crisp" } };
const state = { ahConnected: true, linkedStores: { picnic: true, jumbo: false, crisp: true } };
"""

    def test_only_a_real_product_of_a_linked_store_with_a_basket(self):
        parts = [self.PAGE, function_source(OFFERS_JS, "offerStoreLinked"), function_source(OFFERS_JS, "offerCartId"),
                 function_source(OFFERS_JS, "offerCartable"), r"""
const cases = [
  ["ah", { id: "123456" }], ["ah", { id: "bonusbox-77" }], ["ah", { id: "" }], ["ah", { id: "folder-12" }], ["ah", {}],
  ["picnic", { id: "s1001" }], ["jumbo", { id: "200PAK" }], ["crisp", { id: "42" }], ["crisp", { id: "abc" }],
  ["lidl", { id: "5" }], ["aldi", { id: "5" }], ["toString", { id: "5" }],
];
const out = cases.map(([store, offer]) => offerCartable(store, offer));
state.ahConnected = false; out.push(offerCartable("ah", { id: "123456" }));
state.ahConnected = null; out.push(offerCartable("ah", { id: "123456" }));
console.log(JSON.stringify(out));
"""]
        self.assertEqual(run_node(self, parts),
                         [True, False, False, False, False, True, False, True, False, False, False, False, False, False])

    def test_the_basket_gets_the_number_the_store_knows(self):
        # Albert Heijn schrijft een product op de site als wi123456; het mandje wil alleen het getal.
        parts = [function_source(OFFERS_JS, "offerCartId"),
                 "console.log(JSON.stringify([['ah', 'wi123456'], ['ah', '123456'], ['ah', 'wi'], ['ah', 'bonusbox-1'], ['jumbo', '200PAK'], ['picnic', ' s1001 '], ['crisp', '42'], ['crisp', 'wi42']].map(([store, id]) => offerCartId(store, { id }))));"]
        self.assertEqual(run_node(self, parts), ["123456", "123456", "", "", "200PAK", "s1001", "42", ""])

    def test_the_quantity_is_what_the_deal_asks(self):
        parts = [function_source(OFFERS_JS, "offerCartQuantity"),
                 "console.log(JSON.stringify([{}, { minQuantity: 1 }, { minQuantity: 2 }, { minQuantity: 3 }, { minQuantity: 2.5 }, { minQuantity: 40 }, { minQuantity: '2' }, null].map(offerCartQuantity)));"]
        self.assertEqual(run_node(self, parts), [1, 1, 2, 3, 1, 1, 2, 1])

    def add(self, reply):
        parts = [self.PAGE, r"""
const offersState = { store: "picnic", offers: [{ id: "s1001", title: "Halfvolle melk", price: 1.19, unitSize: "1 l", image: "melk.png", minQuantity: 2 }] };
const requests = []; const toasts = []; const busy = [];
const storeDisplayName = (key) => STORES[key].name;
const t = (key, values = {}) => `${key}:${values.store || ""}`;
const tn = (key, count, values = {}) => `${key}:${count}:${values.title}:${values.store}`;
const toast = (text, type = "success") => toasts.push([text, type]);
const setBusy = (button, on) => busy.push(on);
""",
                 f"async function jsonFetch(path, options) {{ requests.push([path, JSON.parse(options.body)]); {reply} }}",
                 function_source(OFFERS_JS, "offerById"), function_source(OFFERS_JS, "offerStoreLinked"),
                 function_source(OFFERS_JS, "offerCartId"), function_source(OFFERS_JS, "offerCartable"), function_source(OFFERS_JS, "offerCartQuantity"),
                 function_source(OFFERS_JS, "addOfferToCart"),
                 "addOfferToCart({ dataset: { offerCart: 's1001' } }).then(() => console.log(JSON.stringify({ requests, toasts, busy })));"]
        return run_node(self, parts)

    def test_the_offer_goes_into_the_basket_by_its_product_id(self):
        out = self.add("return { added: 1, skipped: 0 };")
        self.assertEqual(out["requests"], [["/api/picnic/cart", {"items": [{
            "query": "Halfvolle melk", "productId": "s1001", "quantity": 2, "title": "Halfvolle melk", "price": 1.19, "unitSize": "1 l"}]}]])
        self.assertNotIn("remember", out["requests"][0][1]["items"][0], "geen keuze bij een ingrediënt: de app onthoudt hem niet")
        self.assertEqual(out["toasts"], [["offers.cart.added:2:Halfvolle melk:Picnic", "success"]])
        self.assertEqual(out["busy"], [True, False])

    def test_a_refusal_of_the_store_is_an_error_toast(self):
        self.assertEqual(self.add("return { added: 0, skipped: 1 };")["toasts"], [["offers.cart.failed:Picnic", "error"]])
        out = self.add("throw new Error('Picnic is niet gekoppeld');")
        self.assertEqual(out["toasts"], [["Picnic is niet gekoppeld", "error"]])
        self.assertEqual(out["busy"], [True, False], "de knop komt terug na een fout")

    def test_the_button_sits_on_the_card_and_in_the_compact_row(self):
        button = function_source(OFFERS_JS, "offerCartButton")
        self.assertIn('if (!offerCartable(offersState.store, offer)) return "";', button)
        self.assertIn('data-offer-cart="${escapeHtml(offer.id)}" title="${label}" aria-label="${label}"', button)
        self.assertIn('${offerCartButton(offer, "offer-card__cart")}${compare}${history}', function_source(OFFERS_JS, "offerCardFoot"))
        self.assertIn('<div class="offer-row__links">${offerCartButton(offer)}${compare}${chart}</div>', function_source(OFFERS_JS, "offerProductRow"))
        self.assertIn('const cart = event.target.closest("[data-offer-cart]");\n  if (cart) addOfferToCart(cart);', OFFERS_JS)
        self.assertEqual(len(re.findall(r"grid-template-columns: [^;]*106px;", CSS)), 2, "ruimte voor drie iconen in de compacte regel")


@unittest.skipUnless(shutil.which("node"), "Node is niet geïnstalleerd")
class FiltersStayTest(unittest.TestCase):
    def test_the_search_and_category_of_offers_survive_a_reload(self):
        find = OFFERS_JS[OFFERS_JS.index('const OFFERS_FIND_KEY'):OFFERS_JS.index("offersState.search = typeof offersSavedFind()")]
        parts = [SESSION, 'const offersState = { store: "ah", search: "", category: "all" };', find, r"""
const out = [];
offersState.search = "kaas"; offersState.category = "zuivel"; offersSaveFind(); out.push(JSON.parse(stored["ms-offers-find"]));
// Na het herladen: dezelfde winkel krijgt de categorie terug, één keer.
offersState.category = "all"; offersRestoreCategory(); out.push(offersState.category);
offersState.category = "all"; offersRestoreCategory(); out.push(offersState.category);
// Een andere winkel niet.
offersState.findRestored = false; offersState.store = "jumbo"; offersRestoreCategory(); out.push(offersState.category);
// Niets meer te bewaren: de sleutel is weg.
offersState.search = ""; offersState.category = "all"; offersSaveFind(); out.push(Object.keys(stored));
// Kapotte opslag is geen fout.
stored["ms-offers-find"] = "{kapot"; out.push(offersSavedFind());
console.log(JSON.stringify(out));
"""]
        self.assertEqual(run_node(self, parts),
                         [{"search": "kaas", "store": "ah", "category": "zuivel"}, "zuivel", "all", "all", [], {}])
        self.assertIn("if (data.supported) offersRestoreCategory();", function_source(OFFERS_JS, "renderOffers"))
        for name in ("offersChooseCategory", "offersShowAll"):
            self.assertIn("offersSaveFind();", function_source(OFFERS_JS, name))
        self.assertIn("offersState.search = event.target.value;\n  offersSaveFind();", OFFERS_JS)

    def test_the_filters_of_purchases_survive_a_reload(self):
        block = ORDERS_JS[ORDERS_JS.index('const PURCHASE_FILTERS_KEY'):ORDERS_JS.index("// De bewaarde zoektekst")]
        parts = [SESSION, r"""
const purchaseFilters = { orders: true, receipts: true, min: "", max: "" };
const ordersState = { query: "frisdrank" }; const state = { itemsStore: "ah" };
""", block, r"""
purchaseFilters.receipts = false; purchaseFilters.min = "10"; purchaseFiltersSave();
const fresh = purchaseFiltersApply(purchaseFiltersSaved(), { orders: true, receipts: true, min: "", max: "" });
const odd = purchaseFiltersApply({ orders: "ja", min: 5, max: "1".repeat(40), receipts: false }, { orders: true, receipts: true, min: "", max: "" });
console.log(JSON.stringify({ saved: JSON.parse(stored["ms-purchase-filters"]), fresh, odd }));
"""]
        out = run_node(self, parts)
        self.assertEqual(out["saved"], {"orders": True, "receipts": False, "min": "10", "max": "", "query": "frisdrank", "store": "ah"})
        self.assertEqual(out["fresh"], {"orders": True, "receipts": False, "min": "10", "max": ""})
        self.assertEqual(out["odd"], {"orders": True, "receipts": False, "min": "", "max": ""}, "alleen waarden van het juiste soort")

    def test_the_search_of_purchases_runs_again_for_the_same_store(self):
        parts = [r"""
let purchaseQueryRestored = false; const searches = []; const field = { value: "" };
const ordersState = { query: "" }; const state = { itemsStore: "ah" };
const $ = () => field; const searchPurchases = () => searches.push(ordersState.query);
""", function_source(ORDERS_JS, "purchaseQueryRestore"), r"""
purchaseQueryRestore({ query: "frisdrank", store: "jumbo" });   // een andere winkel: niet
purchaseQueryRestore({ query: "frisdrank", store: "ah" });      // al een keer geprobeerd: niet nog eens
purchaseQueryRestored = false;
purchaseQueryRestore({ query: "frisdrank", store: "ah" });
console.log(JSON.stringify({ searches, value: field.value }));
"""]
        self.assertEqual(run_node(self, parts), {"searches": ["frisdrank"], "value": "frisdrank"})
        self.assertEqual(function_source(ORDERS_JS, "loadOrders").count("purchaseQueryRestore();"), 2, "bij AH en bij Jumbo")


@unittest.skipUnless(shutil.which("node"), "Node is niet geïnstalleerd")
class PurchasesInPartsTest(unittest.TestCase):
    def test_the_first_hundred_and_the_rest_behind_more(self):
        parts = ["let purchaseLimit = 100;", function_source(ORDERS_JS, "purchasePage"), r"""
const rows = Array.from({ length: 250 }, (_, index) => index);
const pages = [purchasePage(rows), purchasePage(rows, 200), purchasePage(rows, 300), purchasePage(rows.slice(0, 40))];
console.log(JSON.stringify(pages.map((page) => [page.shown.length, page.rest, page.shown[0]])));
"""]
        self.assertEqual(run_node(self, parts), [[100, 150, 0], [200, 50, 0], [250, 0, 0], [40, 0, 0]])
        self.assertIn("const PURCHASE_PAGE = 100;", ORDERS_JS)
        render = function_source(ORDERS_JS, "renderOrders")
        self.assertIn("renderPurchaseView(page.shown, hits);", render)
        self.assertIn("if (more) more.hidden = !page.rest;", render)
        self.assertIn("shown: I18N.formatNumber(visible.length)", render, "de telling gaat over de hele gefilterde lijst")
        self.assertRegex(INDEX_HTML, r'<button class="button button--secondary" id="ah-purchase-more" type="button" hidden data-i18n="orders.more_rows">Meer aankopen</button>')
        self.assertIn('$("#ah-purchase-more").addEventListener("click", () => {\n  purchaseLimit += PURCHASE_PAGE;\n  renderOrders();\n});', ORDERS_JS)
        # Een ander filter, een andere zoektekst of een andere winkel begint weer bij de eerste honderd.
        self.assertEqual(ORDERS_JS.count("purchaseLimit = PURCHASE_PAGE;"), 5)

    def test_cards_are_the_default_on_a_phone_until_you_choose(self):
        parts = [function_source(VIEWS_JS, "purchaseViewDefault"),
                 "console.log(JSON.stringify([purchaseViewDefault(null, true), purchaseViewDefault(null, false), purchaseViewDefault('table', true), purchaseViewDefault('overview', true)]));"]
        self.assertEqual(run_node(self, parts), ["cards", None, None, None])
        load = function_source(VIEWS_JS, "purchaseViewLoad")
        self.assertIn('stored = localStorage.getItem("ms-purchases-view");', load)
        self.assertIn('globalThis.matchMedia?.("(max-width: 640px)").matches', load)


@unittest.skipUnless(shutil.which("node"), "Node is niet geïnstalleerd")
class SubtabsEdgeTest(unittest.TestCase):
    def test_the_bar_fades_on_the_side_where_more_is(self):
        parts = [function_source(APP_JS, "subtabsEdges"), r"""
function edges(scrollWidth, clientWidth, scrollLeft) {
  const set = new Set();
  const bar = { scrollWidth, clientWidth, scrollLeft, classList: { toggle: (name, on) => (on ? set.add(name) : set.delete(name)) } };
  subtabsEdges(bar);
  return [...set].sort();
}
console.log(JSON.stringify([edges(600, 320, 0), edges(600, 320, 140), edges(600, 320, 280), edges(320, 320, 0), edges(321, 320, 0)]));
"""]
        self.assertEqual(run_node(self, parts), [["has-more-end"], ["has-more-end", "has-more-start"], ["has-more-start"], [], []])
        phone = CSS[CSS.index("@media (max-width: 640px) {\n  .subtabs {"):]
        phone = phone[:phone.index("\n}\n")]
        for cls in (".subtabs.has-more-end", ".subtabs.has-more-start", ".subtabs.has-more-start.has-more-end"):
            self.assertIn(f'{cls}:not(:has(.subtab-menu__toggle[aria-expanded="true"])) {{', phone, "niet met de winkelkeuze open")
        self.assertEqual(phone.count("mask-image: linear-gradient("), 6, "met en zonder -webkit-")
        self.assertIn('bar.addEventListener("scroll", () => subtabsEdges(bar), { passive: true });', APP_JS)
        self.assertIn("new ResizeObserver(() => subtabsEdges(bar)).observe(bar);", APP_JS)


class AppShellTest(unittest.TestCase):
    def test_one_store_choice_for_the_week_menu_and_the_basket(self):
        choice = function_source(APP_JS, "renderStoreChoice")
        self.assertIn('if (typeof basketState !== "undefined") basketState.store = state.store;', choice)
        self.assertIn('localStorage.setItem("ms-basket-store", state.store);', choice)

    def test_refresh_is_in_the_menu_of_the_installed_app_only(self):
        item = re.search(r'<button type="button" role="menuitem" class="user-menu__item user-menu__item--standalone" data-user-action="reload">.*?</button>', INDEX_HTML, re.S)
        self.assertIsNotNone(item)
        self.assertIn('<span data-i18n="user.reload">Vernieuwen</span>', item.group(0))
        self.assertRegex(CSS, r"\.user-menu__item\[hidden\],\s*\.user-menu__item--small,\s*\.user-menu__item--standalone\s*{\s*display: none;")
        self.assertRegex(CSS, r"html\.is-standalone \.user-menu__item--standalone:not\(\[hidden\]\)\s*{\s*display: flex;")
        self.assertIn('if (item.dataset.userAction === "reload") window.location.reload();', PROFILE_JS)

    def test_the_installed_app_has_shortcuts_to_three_screens(self):
        manifest = json.loads((STATIC / "manifest.webmanifest").read_text(encoding="utf-8"))
        self.assertEqual([(entry["name"], entry["url"]) for entry in manifest["shortcuts"]],
                         [("Weekmenu", "/recipes"), ("Aanbiedingen", "/offers"), ("Mandje", "/cart")])
        for entry in manifest["shortcuts"]:
            self.assertTrue(entry["url"].startswith(manifest["scope"]))
            for icon in entry["icons"]:
                self.assertTrue((STATIC / icon["src"]).is_file(), icon["src"])

    def test_prefer_organic_is_also_under_preferences(self):
        self.assertRegex(INDEX_HTML, r'<input[^>]*id="appearance-organic"')
        self.assertIn('id="appearance-organic-message"', INDEX_HTML)
        appearance = (STATIC / "appearance.js").read_text(encoding="utf-8")
        self.assertIn('savePreferOrganic(event.target, "#appearance-organic-message")', appearance)
        save = function_source(APP_JS, "savePreferOrganic")
        for field in ("#cart-organic", "#appearance-organic"):
            self.assertIn(field, save, "de twee schakelaars lopen gelijk")


class AhUnlinkTest(AppTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"MEALIESHOPPER_DATA_DIR": tmp.name, "PASSKEY_AUTH_ENABLED": "false"})
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("AH_REFRESH_TOKEN", None)
        run(auth.init_db())
        self.addCleanup(setattr, ah, "user_token_cache", None)
        self.client = self.client_for(create_app())

    async def test_unlinking_forgets_the_stored_token_and_the_one_in_memory(self):
        await ah.save_refresh_token("bewaard-token")
        ah.user_token_cache = ah.TokenCache(token="in-geheugen", expires_at=10**12)
        self.assertEqual(await ah.configured_refresh_token(), "bewaard-token")
        reply = await self.client.post("/api/ah/auth/logout")
        self.assertEqual((reply.status_code, reply.get_json()), (200, {"connected": False, "fromEnvironment": False}))
        self.assertEqual(await ah.configured_refresh_token(), "")
        self.assertIsNone(ah.user_token_cache)
        self.assertEqual((await self.client.get("/api/ah/auth/status")).get_json()["connected"], False)

    async def test_a_token_in_the_environment_keeps_the_link_and_the_answer_says_so(self):
        await ah.save_refresh_token("bewaard-token")
        with mock.patch.dict(os.environ, {"AH_REFRESH_TOKEN": "uit-de-omgeving"}):
            body = (await self.client.post("/api/ah/auth/logout")).get_json()
            self.assertEqual(body, {"connected": True, "fromEnvironment": True})
            self.assertEqual(await ah.configured_refresh_token(), "uit-de-omgeving", "het bewaarde token is weg, de startwaarde blijft")

    async def test_unlinking_twice_is_fine(self):
        for _ in range(2):
            self.assertEqual((await self.client.post("/api/ah/auth/logout")).get_json(), {"connected": False, "fromEnvironment": False})

    def test_only_the_admin_may_unlink(self):
        self.assertTrue(auth.admin_only("/api/ah/auth/logout"))
        self.assertFalse(auth.admin_only("/api/ah/auth/status"))

    def test_the_button_asks_first_and_says_what_the_environment_does(self):
        self.assertRegex(INDEX_HTML, r'<button[^>]*id="ah-unlink"[^>]*hidden')
        unlink = function_source(APP_JS, "unlinkAh")
        self.assertIn('jsonFetch("/api/ah/auth/logout", { method: "POST", body: "{}" })', unlink)
        self.assertIn("ah.unlink_environment", unlink)
        self.assertIn("ah.unlinked", unlink)
        self.assertRegex(APP_JS, r'\$\("#ah-unlink"\)\.addEventListener\("click", \(event\) => unlinkWithConfirm\(event\.currentTarget, STORES\.ah\.name, unlinkAh\)\);')
        self.assertIn('if ($("#ah-unlink")) $("#ah-unlink").hidden = !data.connected;', function_source(APP_JS, "refreshAhStatus"))


if __name__ == "__main__":
    unittest.main()
