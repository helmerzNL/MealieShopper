"""Terugkoppeling in de frontend (4.10.3): een verlopen sessie, geen verbinding, de soort van een melding, de controlelijst die bij
zijn winkel hoort, een lege aanbiedingenlijst en de vertraging van het zoekveld.

De functies uit static/app.js, static/offers.js en static/recipe_search.js draaien hier echt, in Node, met een kleine
nagebootste pagina. Zonder Node slaan de tests over.
"""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
OFFERS_JS = (ROOT / "static" / "offers.js").read_text(encoding="utf-8")
SEARCH_JS = (ROOT / "static" / "recipe_search.js").read_text(encoding="utf-8")
ROUTER_JS = (ROOT / "static" / "router.js").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")


def function_source(source, name):
    match = re.search(rf"^(?:async )?function {re.escape(name)}\(.*?^}}$", source, re.S | re.M)
    if not match:
        raise AssertionError(f"functie {name} niet gevonden")
    return match.group(0)


def run_node(test, script):
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30, check=False)
    test.assertEqual(result.returncode, 0, result.stderr)
    return json.loads(result.stdout.strip().splitlines()[-1])


# Wat jsonFetch om zich heen nodig heeft: de vertaling geeft de sleutel terug, `fetch` doet wat de test zegt.
FETCH_PAGE = r"""
const t = (key) => key;
const state = { auth: { enabled: true, authenticated: true } };
const messages = [];
const elements = {};
const $ = (selector) => (elements[selector] ||= { innerHTML: "" });
const escapeHtml = (value) => String(value);
let statusCalls = 0;
async function refreshAuthStatus() {
  statusCalls += 1;
  await Promise.resolve();
  state.auth = { enabled: true, authenticated: false };
}
let reply = null;
async function fetch(url) {
  if (reply instanceof Error) throw reply;
  return reply;
}
const answer = (status, data) => ({ status, ok: status >= 200 && status < 300, json: async () => data });
async function attempt(url) {
  try {
    return { data: await jsonFetch(url) };
  } catch (error) {
    return { error: error.message, name: error.name };
  }
}
"""


@unittest.skipUnless(shutil.which("node"), "Node is niet geïnstalleerd")
class JsonFetchTest(unittest.TestCase):
    def run_js(self, scenario):
        parts = [
            FETCH_PAGE,
            "let sessionExpiredCheck = null;",
            *(function_source(APP_JS, name) for name in ("sessionExpired", "jsonFetch", "message")),
            "(async () => {",
            scenario,
            "})();",
        ]
        return run_node(self, "\n".join(parts))

    def test_no_connection_gives_a_translated_message(self):
        out = self.run_js(
            """
            reply = new TypeError("Failed to fetch");
            const offline = await attempt("/api/recipes");
            reply = Object.assign(new Error("gestopt"), { name: "AbortError" });
            const aborted = await attempt("/api/recipes");
            console.log(JSON.stringify({ offline, aborted, statusCalls }));
            """
        )
        self.assertEqual(out["offline"]["error"], "err.offline", "niet de Engelse tekst van de browser")
        self.assertEqual(out["aborted"]["name"], "AbortError", "wie een verzoek afbreekt, krijgt zijn eigen fout terug")
        self.assertEqual(out["statusCalls"], 0)

    def test_an_expired_session_opens_the_login_gate_once(self):
        out = self.run_js(
            """
            reply = answer(401, { error: "Je bent niet ingelogd. Log opnieuw in." });
            const both = await Promise.all([attempt("/api/recipes"), attempt("/api/ah/lists")]);
            await sessionExpiredCheck;
            for (let i = 0; i < 5; i += 1) await Promise.resolve();
            const later = await attempt("/api/recipes");
            console.log(JSON.stringify({ both, later, statusCalls, gate: elements["#auth-message"]?.innerHTML || "" }));
            """
        )
        self.assertEqual([call["error"] for call in out["both"]], ["err.session_expired"] * 2)
        self.assertEqual(out["statusCalls"], 1, "twee verzoeken tegelijk vragen de status een keer op")
        self.assertIn("err.session_expired", out["gate"])
        self.assertIn('class="message info"', out["gate"], "een mededeling, geen fout")
        self.assertEqual(out["later"]["error"], "Je bent niet ingelogd. Log opnieuw in.", "de poort staat er al: gewoon de tekst van de server")
        self.assertEqual(out["statusCalls"], 1)

    def test_a_401_on_a_login_route_is_not_an_expired_session(self):
        out = self.run_js(
            """
            reply = answer(401, { error: "Verkeerd wachtwoord" });
            const login = await attempt("/api/auth/password/login");
            state.auth = { enabled: true, authenticated: false };
            const anonymous = await attempt("/api/recipes");
            console.log(JSON.stringify({ login, anonymous, statusCalls }));
            """
        )
        self.assertEqual(out["login"]["error"], "Verkeerd wachtwoord")
        self.assertEqual(out["anonymous"]["error"], "Verkeerd wachtwoord")
        self.assertEqual(out["statusCalls"], 0)

    def test_a_message_has_a_role_that_fits_its_kind(self):
        out = self.run_js(
            """
            const html = {};
            for (const kind of ["error", "warning", "success", "info"]) {
              message("#box", "tekst", kind);
              html[kind] = elements["#box"].innerHTML;
            }
            message("#box", "standaard");
            html.default = elements["#box"].innerHTML;
            message("#box", "");
            html.empty = elements["#box"].innerHTML;
            console.log(JSON.stringify(html));
            """
        )
        for kind, role in (("error", "alert"), ("warning", "alert"), ("success", "status"), ("info", "status")):
            self.assertEqual(out[kind], f'<div class="message {kind}" role="{role}">tekst</div>')
        self.assertIn('class="message error" role="alert"', out["default"])
        self.assertEqual(out["empty"], "")

    def test_busy_texts_are_not_shown_as_a_success(self):
        # "Laden..." en "Wachten op de passkey" zijn geen succes: ze hebben de neutrale soort, en die heeft een eigen stijl.
        for key in ("plan.loading", "passkey.waiting", "mealie.checking", "ah.checking"):
            self.assertNotRegex(APP_JS, rf'message\([^;]*t\("{re.escape(key)}"\), "success"\)', key)
            self.assertRegex(APP_JS, rf'message\([^;]*t\("{re.escape(key)}"\), "info"\)', key)
        self.assertRegex(CSS, r"\.message\.info \{[^}]*background: var\(--surface-muted\)")


# De controlelijst hoort bij een winkel (data-store op .cart-review); de winkelkeuze erboven kan intussen iets anders zeggen.
REVIEW_PAGE = r"""
const STORES = {
  ah: { name: "Albert Heijn", api: "ah", ready: "cart.ready" },
  picnic: { name: "Picnic", api: "picnic", ready: "cart.ready_picnic" },
};
const state = { store: "ah" };
let review = null; // de open controlelijst, of null
const document = { querySelector: (selector) => (selector === "#cart-result .cart-review" ? review : null) };
const posts = [];
async function jsonFetch(url, options) {
  posts.push({ url, items: JSON.parse(options.body).items });
  return { added: 1, items: [] };
}
const shown = [];
function showCartResult(data, notFound, store) { shown.push(store); }
"""


@unittest.skipUnless(shutil.which("node"), "Node is niet geïnstalleerd")
class CartReviewStoreTest(unittest.TestCase):
    def run_js(self, scenario):
        parts = [
            REVIEW_PAGE,
            *(function_source(APP_JS, name) for name in ("currentStore", "cartReviewStore", "cartReviewApi", "submitCart")),
            "(async () => {",
            scenario,
            "})();",
        ]
        return run_node(self, "\n".join(parts))

    def test_the_list_goes_to_the_store_it_was_searched_at(self):
        out = self.run_js(
            """
            review = { dataset: { store: "ah" } };   // gezocht bij Albert Heijn
            state.store = "picnic";                   // de winkelkeuze erboven zegt intussen Picnic
            await submitCart([{ productId: "wi123", quantity: 1 }], []);
            console.log(JSON.stringify({ posts, shown, api: cartReviewApi() }));
            """
        )
        self.assertEqual(out["posts"][0]["url"], "/api/ah/cart", "AH-producten gaan nooit naar Picnic")
        self.assertEqual(out["shown"], ["ah"])
        self.assertEqual(out["api"], "ah")

    def test_without_an_open_list_the_chosen_store_counts(self):
        out = self.run_js(
            """
            state.store = "picnic";
            const none = cartReviewStore();
            review = { dataset: { store: "onbekend" } };
            const unknown = cartReviewStore();
            console.log(JSON.stringify({ none, unknown, api: cartReviewApi() }));
            """
        )
        self.assertEqual(out, {"none": "", "unknown": "", "api": "picnic"})

    def test_switching_store_asks_before_it_drops_the_list(self):
        handler = function_source(APP_JS, "renderStoreChoice")
        asked = handler.index('t("cart.switch_confirm"')
        self.assertLess(handler.index("cartReviewStore()"), asked)
        self.assertLess(asked, handler.index("state.store = item.dataset.store"), "eerst vragen, dan pas wisselen")
        self.assertIn('data-store="${escapeHtml(state.store)}"', function_source(APP_JS, "renderCartReview"))

    def test_cancel_asks_and_every_store_gets_the_basket_button(self):
        review = function_source(APP_JS, "renderCartReview")
        self.assertLess(review.index('t("cart.cancel_confirm")'), review.index('$("#cart-result").innerHTML = "";'))
        result = function_source(APP_JS, "showCartResult")
        self.assertIn('id="view-basket"', result)
        self.assertNotIn('["picnic", "jumbo", "crisp"].includes', result, "ook bij Albert Heijn")

    def test_an_unlinked_albert_heijn_is_said_before_the_search(self):
        add = function_source(APP_JS, "addToCart")
        self.assertLess(add.index("cartStoreUnlinkedHtml()"), add.index("/cart/preview"), "voor het zoeken, niet na het nalopen")
        parts = [
            "const t = (key) => key; const escapeHtml = (value) => String(value);",
            "let regular = false; const isRegularUser = () => regular;",
            "const state = { store: 'ah', ahConnected: undefined };",
            function_source(APP_JS, "cartStoreUnlinkedHtml"),
            """
            const unknown = cartStoreUnlinkedHtml();          // de status is nog niet binnen: niets tegenhouden
            state.ahConnected = true; const linked = cartStoreUnlinkedHtml();
            state.ahConnected = false; const owner = cartStoreUnlinkedHtml();
            regular = true; const user = cartStoreUnlinkedHtml();
            state.store = "picnic"; const other = cartStoreUnlinkedHtml();
            console.log(JSON.stringify({ unknown, linked, owner, user, other }));
            """,
        ]
        out = run_node(self, "\n".join(parts))
        self.assertEqual((out["unknown"], out["linked"], out["other"]), ("", "", ""))
        self.assertIn('id="cart-link-store"', out["owner"])
        self.assertIn("cart.ah_unlinked", out["owner"])
        self.assertNotIn("<button", out["user"], "een gewone gebruiker kan niet koppelen")
        self.assertIn("cart.ah_ask_admin", out["user"])


@unittest.skipUnless(shutil.which("node"), "Node is niet geïnstalleerd")
class OffersFeedbackTest(unittest.TestCase):
    def test_typing_redraws_once_after_a_pause(self):
        parts = [
            re.search(r"^const OFFERS_INPUT_WAIT = \d+;$", OFFERS_JS, re.M).group(0),
            "let offersInputTimer = 0; let drawn = 0; function offersRedraw() { drawn += 1; }",
            function_source(OFFERS_JS, "offersRedrawSoon"),
            """
            (async () => {
              for (let i = 0; i < 5; i += 1) offersRedrawSoon();
              const during = drawn;
              await new Promise((done) => setTimeout(done, OFFERS_INPUT_WAIT + 80));
              let other = 0;
              offersRedrawSoon(() => { other += 1; });
              await new Promise((done) => setTimeout(done, OFFERS_INPUT_WAIT + 80));
              console.log(JSON.stringify({ during, drawn, other, wait: OFFERS_INPUT_WAIT }));
            })();
            """,
        ]
        out = run_node(self, "\n".join(parts))
        self.assertEqual(out, {"during": 0, "drawn": 1, "other": 1, "wait": 250})
        for field in ("offers-search", "offers-filter-unit-price", "offers-filter-discount"):
            handler = re.search(rf'\$\("#{field}"\)\.addEventListener\("input".*?^\}}\);$', OFFERS_JS, re.S | re.M).group(0)
            self.assertIn("offersRedrawSoon(", handler, field)

    def test_an_empty_grid_says_so_and_offers_the_way_back(self):
        grid = function_source(OFFERS_JS, "renderOfferGrid")
        self.assertIn('empty.hidden = Boolean(units.length) || !offersState.offers.length;', grid)
        self.assertRegex(INDEX_HTML, r'<div class="message info message--action" id="offers-empty" role="status" hidden>')
        self.assertIn('id="offers-empty-clear"', INDEX_HTML)
        self.assertIn('$("#offers-empty-clear").addEventListener("click", offersShowAll);', OFFERS_JS)
        parts = [
            "const OFFERS_GOOD_KEY = 'good';",
            "const offersState = { search: 'kaas', category: 'vlees', filters: { bought: true }, goodOnly: true, personalOnly: true };",
            "const offersEmptyFilters = () => ({ bought: false }); const calls = [];",
            "const offersSaveFilters = () => calls.push('save'); const offersRemember = (key, value) => calls.push(`${key}=${value}`);",
            "const offersTogglePersonal = (on) => { offersState.personalOnly = on; calls.push(`personal=${on}`); };",
            "const offersRedraw = () => calls.push('redraw');",
            "const field = { checked: true, focus: () => calls.push('focus') }; const $ = () => field;",
            function_source(OFFERS_JS, "offersShowAll"),
            "offersShowAll(); console.log(JSON.stringify({ offersState, calls, checked: field.checked }));",
        ]
        out = run_node(self, "\n".join(parts))
        self.assertEqual(out["offersState"], {"search": "", "category": "all", "filters": {"bought": False}, "goodOnly": False, "personalOnly": False})
        self.assertEqual(out["calls"], ["save", "good=0", "personal=false", "redraw", "focus"])
        self.assertFalse(out["checked"])


@unittest.skipUnless(shutil.which("node"), "Node is niet geïnstalleerd")
class SearchImportTest(unittest.TestCase):
    def test_a_link_in_the_search_field_is_imported(self):
        parts = [
            function_source(SEARCH_JS, "recipeQueryIsUrl"),
            """
            const cases = ["https://www.ah.nl/allerhande/recept/R-R1/soep", "HTTP://example.org/x", "kip https://example.org", "https://", "kipsoep", ""];
            console.log(JSON.stringify(cases.map(recipeQueryIsUrl)));
            """,
        ]
        self.assertEqual(run_node(self, "\n".join(parts)), [True, True, False, False, False, False])
        search = function_source(SEARCH_JS, "runRecipeSearch")
        self.assertLess(search.index("recipeQueryIsUrl(query)"), search.index("recipeSearch.query = query"), "een link wordt niet gezocht")

    def test_an_import_error_never_goes_in_as_html(self):
        self.assertIn("return { ok: false, html: escapeHtml(error.message) };", function_source(APP_JS, "importUrl"))


class StaticFeedbackTest(unittest.TestCase):
    def test_the_bar_keeps_a_name_for_a_screen_reader(self):
        # Het korte label onder het icoon heeft aria-hidden; het lange is dus de naam van de tab en mag niet met display: none weg.
        rule = re.search(r"html\.has-tabbar \.tab-text \{([^}]*)\}", CSS).group(1)
        self.assertNotIn("display: none", rule)
        self.assertIn("clip: rect(0 0 0 0)", rule)
        self.assertIn("position: absolute", rule)

    def test_dialogs_use_the_visible_height_with_a_fallback(self):
        lines = CSS.split("\n")
        with_vh = [at for at, line in enumerate(lines) if re.match(r"\s*max-height: .*100vh", line)]
        self.assertEqual(len(with_vh), 5)
        for at in with_vh:
            self.assertEqual(lines[at + 1].split(";")[0], lines[at].split(";")[0].replace("100vh", "100dvh"), lines[at])

    def test_the_page_title_follows_the_screen(self):
        self.assertIn("document.title = routerTitle();", function_source(ROUTER_JS, "routerRefreshLinks"))

    @unittest.skipUnless(shutil.which("node"), "Node is niet geïnstalleerd")
    def test_the_title_names_the_open_subtab_and_the_section(self):
        parts = [
            "let panel = null; let tab = null;",
            "const document = { querySelector: (selector) => (selector === '.panel.is-active' ? panel : tab) };",
            function_source(ROUTER_JS, "routerTitle"),
            """
            const node = (textContent) => ({ textContent });
            const titles = [routerTitle()];                                     // nog geen scherm
            tab = node("Recepten");
            panel = { querySelector: (selector) => (selector.startsWith(".subtabs") ? node(" Weekmenu ") : node("Weekmenu")) };
            titles.push(routerTitle());                                         // subtab en de tab van het hoofdmenu
            tab = node("Mandje");
            panel = { querySelector: (selector) => (selector === "h2" ? node("Mandje") : null) };
            titles.push(routerTitle());                                         // geen subtab: de tab een keer
            tab = null;
            panel = { querySelector: (selector) => (selector === "h2" ? node("Instellingen") : node("Koppelen")) };
            titles.push(routerTitle());                                         // uit het menu Meer: de kop van het scherm
            panel = { querySelector: (selector) => (selector === "h2" ? node("Over MealieShopper") : null) };
            titles.push(routerTitle());
            console.log(JSON.stringify(titles));
            """,
        ]
        self.assertEqual(
            run_node(self, "\n".join(parts)),
            ["MealieShopper", "Weekmenu \u00b7 Recepten \u00b7 MealieShopper", "Mandje \u00b7 MealieShopper", "Koppelen \u00b7 Instellingen \u00b7 MealieShopper", "Over MealieShopper"],
        )

    def test_unlinking_and_signing_out_cannot_fail_silently(self):
        for script, handler in (("app.js", "unlinkMealie"), ("jumbo.js", "unlinkJumbo"), ("picnic.js", "unlinkPicnic"), ("crisp.js", "unlinkCrisp")):
            source = (ROOT / "static" / script).read_text(encoding="utf-8")
            self.assertRegex(source, rf"unlinkWithConfirm\(event\.currentTarget, [^,]+, {handler}\)", script)
        confirm = function_source(APP_JS, "unlinkWithConfirm")
        self.assertLess(confirm.index('t("links.unlink_confirm"'), confirm.index("await unlink()"))
        logout = re.search(r'\$\("#auth-logout"\)\.addEventListener\("click".*?^\}\);$', APP_JS, re.S | re.M).group(0)
        self.assertIn("catch (error)", logout)
        self.assertIn('t("auth.logout_failed"', logout)


if __name__ == "__main__":
    unittest.main()
