"""De voorkeurssupermarkt (Instellingen, Voorkeuren): Aanbiedingen en Winkels openen ermee, tot je zelf een winkel kiest.

Standaard is het Albert Heijn. Kies je Jumbo, Picnic of Crisp, dan openen beide pagina's daarmee zodra die winkel gekoppeld
is; is hij (nog) niet gekoppeld, dan met Albert Heijn. Een adres zonder winkel (/offers) betekent "de voorkeurssupermarkt";
een adres met een winkel (?store=ah, /stores/jumbo/lists) is een keuze en blijft staan.

De functies uit static/app.js, static/offers.js en static/router.js draaien hier echt, in Node, met nagebootste pagina's.
Zonder Node slaan de tests over.
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
OFFERS_JS = (STATIC / "offers.js").read_text(encoding="utf-8")
ROUTER_JS = (STATIC / "router.js").read_text(encoding="utf-8")
APPEARANCE_JS = (STATIC / "appearance.js").read_text(encoding="utf-8")
HTML = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")


def function_source(source, name):
    match = re.search(rf"^(?:async )?function {re.escape(name)}\(.*?^}}$", source, re.S | re.M)
    if not match:
        raise AssertionError(f"functie {name} niet gevonden")
    return match.group(0)


def const_source(source, name):
    match = re.search(rf"^const {re.escape(name)} = (?:\[.*?\];|\{{.*?^\}};)$", source, re.S | re.M)
    if not match:
        raise AssertionError(f"const {name} niet gevonden")
    return match.group(0)


APP_HELPERS = ["preferredSupermarket", "preferredSupermarketKnown", "defaultStoreOf", "linkedStoreKeys", "offersStoreParam",
               "offersOnlyStore", "storeWithoutLink"]

# Aanbiedingen: wat offersFollowPreferred, offersSwitchStore en routerApplyOffers aanraken, nagebootst. `loads` telt elke
# keer dat de aanbiedingen laden; `active` zegt of de pagina Aanbiedingen open staat.
OFFERS_PAGE = r"""
const state = { auth: { preferredStore: "ah" }, linkedStores: { jumbo: false, picnic: false, crisp: false },
  storeStatusKnown: { jumbo: false, picnic: false, crisp: false } };
const offersState = { store: "ah", category: "x", courses: { shown: ["x"], hidden: [] }, chosen: false, waiting: false };
function facetSelection() { return { shown: [], hidden: [] }; }
const router = { pending: "", applying: 0 };
const window = { location: { href: "https://shop.example/offers" } };
const loads = [];
let active = true;
let historyChanged = 0;
function offersTabActive() { return active; }
function offersHistoryActive() { return false; }
function showOffers() { loads.push(offersState.store); }
function offerHistoryStoreChanged() { historyChanged++; }
function routerSync() {}
function showPanel() {}
function routerTab() {}
function routerOpenSubtab() {}
const offerHistory = {};
const $ = () => ({ value: "" });
function link(store, picked = true) { state.linkedStores[store] = picked; state.storeStatusKnown[store] = true; }
function result() { return { store: offersState.store, chosen: offersState.chosen, pending: router.pending, loads: loads.splice(0), historyChanged }; }
"""


@unittest.skipUnless(shutil.which("node"), "Node is niet geïnstalleerd")
class PreferredStoreTest(unittest.TestCase):
    def run_js(self, scenario):
        parts = [
            OFFERS_PAGE,
            const_source(APP_JS, "STORES"),
            const_source(APP_JS, "OFFER_ONLY_STORES"),
            const_source(OFFERS_JS, "OFFERS_STORES"),
            *(function_source(APP_JS, name) for name in APP_HELPERS),
            function_source(OFFERS_JS, "offersStores"),
            function_source(OFFERS_JS, "offersFollowPreferred"),
            function_source(OFFERS_JS, "offersSwitchStore"),
            const_source(ROUTER_JS, "ROUTE_SUBTABS"),
            function_source(ROUTER_JS, "routerApplyOffers"),
            scenario,
        ]
        result = subprocess.run(["node", "-e", "\n".join(parts)], capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_albert_heijn_is_the_default_and_an_unlinked_choice_falls_back_to_it(self):
        out = self.run_js(
            """
            const rows = [];
            const row = () => rows.push([preferredSupermarket(), preferredSupermarketKnown(), defaultStoreOf(linkedStoreKeys())]);
            state.auth = null; row();                                   // nog niet ingelogd
            state.auth = { preferredStore: "ah" }; row();
            state.auth = { preferredStore: "jumbo" }; row();            // status van Jumbo nog niet binnen
            link("jumbo", false); row();                                // binnen: niet gekoppeld
            link("jumbo"); row();                                       // gekoppeld
            state.auth = { preferredStore: "lidl" }; row();             // geen voorkeurssupermarkt (alleen aanbiedingen): Albert Heijn
            state.auth = { preferredStore: "crisp" }; link("crisp"); row();
            console.log(JSON.stringify(rows));
            """
        )
        self.assertEqual(out, [
            ["ah", True, "ah"],
            ["ah", True, "ah"],
            ["jumbo", False, "ah"],
            ["jumbo", True, "ah"],
            ["jumbo", True, "jumbo"],
            ["ah", True, "ah"],
            ["crisp", True, "crisp"],
        ])

    def test_the_address_leaves_out_only_the_store_the_page_opens_with(self):
        out = self.run_js(
            """
            const before = ["ah", "jumbo"].map(offersStoreParam);
            state.auth = { preferredStore: "jumbo" }; link("jumbo");
            console.log(JSON.stringify([before, ["ah", "jumbo", "picnic"].map(offersStoreParam)]));
            """
        )
        self.assertEqual(out[0], ["", "jumbo"])
        self.assertEqual(out[1], ["ah", "", "picnic"], "met Jumbo als voorkeur heeft Albert Heijn zijn eigen adres")
        self.assertIn('store: offersStoreParam(store)', APP_JS, "ook de links naar de prijsgeschiedenis (een kassabon van AH)")

    def test_offers_follow_the_preferred_store_until_you_choose_one(self):
        out = self.run_js(
            """
            state.auth = { preferredStore: "picnic" };
            offersFollowPreferred();
            const unknown = result();                 // Picnic nog niet bekend: blijft AH, laadt niets
            link("picnic");
            offersFollowPreferred();
            const followed = result();                // open: wisselt en laadt, maar is geen keuze
            offersSwitchStore("ah");                  // zelf gekozen in het menu
            link("jumbo");
            offersFollowPreferred();
            const chosen = result();
            console.log(JSON.stringify([unknown, followed, chosen]));
            """
        )
        unknown, followed, chosen = out
        self.assertEqual((unknown["store"], unknown["loads"]), ("ah", []))
        self.assertEqual((followed["store"], followed["chosen"], followed["loads"]), ("picnic", False, ["picnic"]))
        self.assertEqual((chosen["store"], chosen["chosen"], chosen["loads"]), ("ah", True, ["ah"]), "een eigen keuze blijft staan")

    def test_a_closed_offers_page_only_remembers_the_store(self):
        out = self.run_js(
            """
            active = false;
            state.auth = { preferredStore: "crisp" }; link("crisp");
            offersFollowPreferred();
            console.log(JSON.stringify({ ...result(), course: offersState.courses }));
            """
        )
        self.assertEqual((out["store"], out["loads"]), ("crisp", []), "laden doet showOffers bij het openen")
        self.assertEqual((out["course"], out["historyChanged"]), ({"shown": [], "hidden": []}, 1))  # de categorieen zijn filters per winkel (offersFilters)

    def test_an_address_waiting_for_a_link_decides_first(self):
        out = self.run_js(
            """
            state.auth = { preferredStore: "jumbo" }; link("jumbo"); link("picnic");
            router.pending = "https://shop.example/offers?store=picnic";
            offersFollowPreferred();
            console.log(JSON.stringify(result()));
            """
        )
        self.assertEqual((out["store"], out["loads"]), ("ah", []))

    def test_an_address_without_a_store_opens_the_preferred_one(self):
        out = self.run_js(
            """
            const rows = [];
            state.auth = { preferredStore: "jumbo" };
            routerApplyOffers("", new URLSearchParams(""));
            rows.push(result());                                     // Jumbo nog niet bekend: het adres wacht
            router.pending = ""; link("jumbo");
            routerApplyOffers("", new URLSearchParams(""));
            rows.push(result());                                     // nu wel: Jumbo, geen keuze
            routerApplyOffers("", new URLSearchParams("store=ah"));
            rows.push(result());                                     // ?store=ah is een keuze
            routerApplyOffers("", new URLSearchParams("store=ah"));
            offersFollowPreferred();
            rows.push(result());                                     // en blijft staan
            console.log(JSON.stringify(rows));
            """
        )
        self.assertEqual((out[0]["store"], out[0]["pending"]), ("ah", "https://shop.example/offers"))
        self.assertEqual((out[1]["store"], out[1]["chosen"], out[1]["loads"]), ("jumbo", False, ["jumbo"]))
        self.assertEqual((out[2]["store"], out[2]["chosen"]), ("ah", True))
        self.assertEqual((out[3]["store"], out[3]["loads"]), ("ah", []))

    def test_offers_wait_for_the_link_of_the_preferred_store_instead_of_showing_albert_heijn_first(self):
        body = function_source(OFFERS_JS, "showOffers")
        self.assertIn("offersState.waiting = !offersState.chosen && !preferredSupermarketKnown();", body)
        self.assertLess(body.index("if (offersState.waiting) {"), body.index("await offersRequest"))
        render = function_source(OFFERS_JS, "renderOffersStore")
        self.assertIn("offersFollowPreferred();", render)
        self.assertIn("if (offersState.waiting && preferredSupermarketKnown() && offersTabActive()", render, "daarna alsnog laden")
        for name in ("jumbo", "picnic", "crisp"):
            script = (STATIC / f"{name}.js").read_text(encoding="utf-8")
            status = script[script.index("Status() {"):]
            self.assertIn(f"state.storeStatusKnown.{name} = true;", status[:status.index("renderStoreChoice();")], name)

    def test_the_old_remembered_offers_store_is_gone(self):
        # Die gold nooit (AH stond er altijd al) en zou de voorkeurssupermarkt overschrijven.
        self.assertNotIn("ms-offers-store", OFFERS_JS)

    def test_lidl_and_aldi_are_offer_stores_without_a_link(self):
        out = self.run_js(
            """
            const before = offersStores();
            const address = [];
            routerApplyOffers("", new URLSearchParams("store=lidl"));
            address.push(result());
            offersSwitchStore("aldi");
            address.push(result());
            console.log(JSON.stringify([before, address, linkedStoreKeys(), storeWithoutLink("lidl"), storeWithoutLink("jumbo")]));
            """
        )
        before, address, linked, lidl, jumbo = out
        self.assertEqual(before, ["ah", "lidl", "aldi"], "zonder koppelingen: AH, Lidl en Aldi")
        self.assertEqual((address[0]["store"], address[0]["chosen"], address[0]["pending"]), ("lidl", True, ""), "een adres met Lidl wacht nergens op")
        self.assertEqual((address[1]["store"], address[1]["loads"]), ("aldi", ["aldi"]))
        self.assertEqual(linked, ["ah"], "Lidl en Aldi horen niet bij het mandje, Winkels of de voorkeurssupermarkt")
        self.assertEqual((lidl, jumbo), (True, False))


@unittest.skipUnless(shutil.which("node"), "Node is niet geïnstalleerd")
class StoresFollowPreferredTest(unittest.TestCase):
    """Winkels: itemsFollowPreferred wisselt alleen terwijl de pagina open staat, en niet na een eigen keuze."""

    def run_js(self, scenario):
        page = r"""
        const state = { auth: { preferredStore: "ah" }, itemsStore: "ah", itemsStoreChosen: false,
          linkedStores: { jumbo: false, picnic: false, crisp: false }, storeStatusKnown: { jumbo: false, picnic: false, crisp: false } };
        let active = true;
        const switches = [];
        const document = { querySelector: (selector) => (selector === "#ah-items.is-active" && active ? {} : null) };
        function itemsStores() { return ["ah", ...["jumbo", "picnic", "crisp"].filter((key) => state.linkedStores[key])]; }
        function itemsOpenSubtab() { return "ah-items-lijstjes"; }
        function itemsSection() { return "lists"; }
        function switchItemsStore(store, section, { chosen = true } = {}) {
          state.itemsStoreChosen = chosen; state.itemsStore = store; switches.push([store, section, chosen]);
        }
        const offersState = { chosen: true };
        function link(store) { state.linkedStores[store] = true; state.storeStatusKnown[store] = true; }
        """
        parts = [page, const_source(APP_JS, "STORES"), *(function_source(APP_JS, name) for name in APP_HELPERS),
                 function_source(APP_JS, "itemsFollowPreferred"), function_source(APP_JS, "preferredSupermarketChanged"), scenario]
        result = subprocess.run(["node", "-e", "\n".join(parts)], capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_stores_open_with_the_preferred_store_and_keep_the_open_section(self):
        out = self.run_js(
            """
            state.auth = { preferredStore: "jumbo" };
            itemsFollowPreferred();
            const before = switches.splice(0);       // Jumbo nog niet gekoppeld: Albert Heijn
            link("jumbo");
            itemsFollowPreferred();
            console.log(JSON.stringify([before, switches.splice(0), state.itemsStoreChosen]));
            """
        )
        self.assertEqual(out[0], [])
        self.assertEqual(out[1], [["jumbo", "lists", False]], "hetzelfde onderdeel, en het telt niet als eigen keuze")
        self.assertFalse(out[2])

    def test_nothing_switches_while_stores_is_closed_or_after_a_choice(self):
        out = self.run_js(
            """
            state.auth = { preferredStore: "picnic" }; link("picnic");
            active = false;
            itemsFollowPreferred();
            const closed = switches.splice(0);       // anders laadt een tab op de achtergrond
            active = true;
            state.itemsStoreChosen = true;
            itemsFollowPreferred();
            const chosen = switches.splice(0);
            console.log(JSON.stringify([closed, chosen]));
            """
        )
        self.assertEqual(out, [[], []])

    def test_a_new_preference_applies_again_after_an_own_choice(self):
        out = self.run_js(
            """
            link("crisp");
            state.itemsStoreChosen = true;
            state.auth = { preferredStore: "crisp" };
            preferredSupermarketChanged();
            console.log(JSON.stringify([switches.splice(0), offersState.chosen]));
            """
        )
        self.assertEqual(out, [[["crisp", "lists", False]], False])

    def test_an_address_with_a_store_is_a_choice_and_without_one_is_not(self):
        body = function_source(ROUTER_JS, "routerApplyStores")
        self.assertIn("if (STORE_SECTIONS[store]) state.itemsStoreChosen = true;", body)
        self.assertIn("else if (!store) state.itemsStoreChosen = false;", body)
        self.assertLess(body.index("state.itemsStoreChosen = true"), body.index("showPanel("), "eerst kiezen, dan tonen")
        self.assertIn("switchItemsStore(store, itemsSection(itemsOpenSubtab()), { chosen: false })", APP_JS)
        self.assertIn('if (panelId === "ah-items") itemsFollowPreferred();', APP_JS)


class PreferencesMarkupTest(unittest.TestCase):
    def test_the_settings_tab_is_called_preferences_and_its_display_block_appearance(self):
        tab = re.search(r'<a class="subtab" data-subtab="settings-appearance"[^>]*>([^<]+)</a>', HTML)
        self.assertEqual(tab.group(1), "Voorkeuren")
        self.assertIn('href="/settings/preferences"', tab.group(0))
        for lang, tab_name, block in (("nl", "Voorkeuren", "Weergave"), ("en", "Preferences", "Appearance")):
            strings = (STATIC / "i18n" / f"{lang}.js").read_text(encoding="utf-8")
            self.assertIn(f'"settings.tab.appearance": "{tab_name}"', strings, lang)
            self.assertIn(f'"appearance.display.title": "{block}"', strings, lang)
            self.assertNotIn('"appearance.prefs.title"', strings, lang)

    def test_the_choice_is_saved_with_the_profile_and_undone_when_refused(self):
        body = function_source(APPEARANCE_JS, "appearanceSaveStore")
        self.assertIn("JSON.stringify({ preferredStore: store })", body)
        self.assertIn("preferredSupermarketChanged();", body)
        self.assertIn("state.auth = { ...state.auth, preferredStore: previous };", body, "geweigerd: de vorige terug")



if __name__ == "__main__":
    unittest.main()
