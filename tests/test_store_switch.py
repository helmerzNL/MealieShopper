"""Winkels: wissel je van winkel, dan blijft het onderdeel open dat open stond, zoals bij Aanbiedingen.

Sta je bij Albert Heijn op Lijstjes en kies je Jumbo, dan opent Lijstjes van Jumbo; van Bestellingen bij AH naar Picnic opent
Bestellingen van Picnic. Heeft de nieuwe winkel het onderdeel niet (Lijstjes bij Picnic, Recepten bij Crisp), dan opent zijn
eerste tab. De tab opent één keer: zo laadt hij één keer en krijgt het adres één stap in de geschiedenis.

`switchItemsStore`, `renderItemsStore` en de hulpfuncties uit static/app.js draaien hier echt, in Node, met een nagebootste
tabbalk. Zonder Node slaan de tests over.
"""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
ROUTER_JS = (ROOT / "static" / "router.js").read_text(encoding="utf-8")

# De subtabs van Winkels in de volgorde van de balk (templates/index.html, zie test_ah_items_ui.StoreSubtabsTest).
SUBTABS = [
    "ah-items-recepten", "ah-items-jumbo-recepten", "ah-items-lijstjes", "ah-items-bestellingen",
    "ah-items-picnic", "ah-items-picnic-favorieten", "ah-items-picnic-bestellingen",
    "ah-items-crisp-favorieten", "ah-items-crisp-bestellingen",
]


def function_source(name):
    match = re.search(rf"^function {re.escape(name)}\(.*?^}}$", APP_JS, re.S | re.M)
    if not match:
        raise AssertionError(f"functie {name} niet gevonden")
    return match.group(0)


def const_source(name):
    match = re.search(rf"^const {re.escape(name)} = (?:\[.*?\];|\{{.*?^\}};)$", APP_JS, re.S | re.M)
    if not match:
        raise AssertionError(f"const {name} niet gevonden")
    return match.group(0)


# Een tabbalk met alleen wat de winkelwissel gebruikt. Een klik op een tab maakt hem actief, zoals de handler in app.js, en
# telt mee in `clicks`: elke klik laadt de tab en zet het adres.
PAGE = r"""
const SUBTABS = %s;
const clicks = [];
const tabs = SUBTABS.map((id) => ({
  dataset: { subtab: id },
  hidden: false,
  active: false,
  click() {
    clicks.push(id);
    tabs.forEach((tab) => { tab.active = false; });
    this.active = true;
  },
}));
const document = {
  querySelector(selector) {
    if (selector === "#ah-items .subtab.is-active[data-subtab]") return tabs.find((tab) => tab.active) || null;
    const one = selector.match(/^#ah-items \.subtab\[data-subtab="([\w-]+)"\]$/);
    if (one) return tabs.find((tab) => tab.dataset.subtab === one[1]) || null;
    throw new Error(`onbekende selector ${selector}`);
  },
  querySelectorAll(selector) {
    const all = selector.match(/^\[data-subtab="([\w-]+)"\]$/);
    if (all) return tabs.filter((tab) => tab.dataset.subtab === all[1]);
    throw new Error(`onbekende selector ${selector}`);
  },
};
const elements = {};
const $ = (selector) => (elements[selector] ||= { hidden: false, textContent: "", innerHTML: "", value: "" });
const escapeHtml = (value) => String(value);
function showAhListsOverview() {}
function renderAhLists() {}
function resetOrders() {}
const state = { itemsStore: "ah", linkedStores: { jumbo: true, picnic: true, crisp: true } };

function open(subtab) {
  tabs.forEach((tab) => { tab.active = tab.dataset.subtab === subtab; });
}
function result() {
  const active = tabs.find((tab) => tab.active);
  return { store: state.itemsStore, open: active ? active.dataset.subtab : "", hidden: active ? active.hidden : null, clicks: clicks.splice(0) };
}
"""


@unittest.skipUnless(shutil.which("node"), "Node is niet geïnstalleerd")
class StoreSwitchTest(unittest.TestCase):
    def run_js(self, scenario):
        consts = [const_source(name) for name in ("STORES", "PICNIC_SUBTABS", "JUMBO_SUBTABS", "CRISP_SUBTABS", "STORE_SECTIONS")]
        functions = [function_source(name) for name in (
            "itemsStores", "itemsStoreName", "itemsOpenSubtab", "itemsSection", "itemsSubtab", "itemsStorePath",
            "storesPath", "renderItemsStore", "switchItemsStore",
        )]
        script = "\n".join([PAGE % json.dumps(SUBTABS), *consts, *functions, scenario])
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout.strip().splitlines()[-1])

    def switch(self, store, start, start_open):
        return self.run_js(
            f"""
            state.itemsStore = {json.dumps(start)};
            renderItemsStore();
            open({json.dumps(start_open)});
            clicks.splice(0);
            switchItemsStore({json.dumps(store)});
            console.log(JSON.stringify(result()));
            """
        )

    def test_the_open_section_stays_open_at_the_new_store(self):
        cases = [
            # van, open tab, naar, wat open moet staan
            ("ah", "ah-items-lijstjes", "jumbo", "ah-items-lijstjes"),
            ("ah", "ah-items-bestellingen", "jumbo", "ah-items-bestellingen"),
            ("ah", "ah-items-recepten", "jumbo", "ah-items-jumbo-recepten"),
            ("ah", "ah-items-bestellingen", "picnic", "ah-items-picnic-bestellingen"),
            ("ah", "ah-items-bestellingen", "crisp", "ah-items-crisp-bestellingen"),
            ("jumbo", "ah-items-lijstjes", "ah", "ah-items-lijstjes"),
            ("jumbo", "ah-items-jumbo-recepten", "picnic", "ah-items-picnic"),
            ("picnic", "ah-items-picnic", "ah", "ah-items-recepten"),
            ("picnic", "ah-items-picnic-favorieten", "crisp", "ah-items-crisp-favorieten"),
            ("picnic", "ah-items-picnic-bestellingen", "jumbo", "ah-items-bestellingen"),
            ("crisp", "ah-items-crisp-favorieten", "picnic", "ah-items-picnic-favorieten"),
            ("crisp", "ah-items-crisp-bestellingen", "ah", "ah-items-bestellingen"),
        ]
        for start, start_open, store, wanted in cases:
            with self.subTest(f"{start} {start_open} -> {store}"):
                out = self.switch(store, start, start_open)
                self.assertEqual(out["store"], store)
                self.assertEqual(out["open"], wanted)
                self.assertFalse(out["hidden"], "de open tab hoort bij de gekozen winkel")
                self.assertEqual(out["clicks"], [wanted], "de tab opent een keer: een keer laden en een stap in het adres")

    def test_a_section_the_new_store_does_not_have_opens_its_first_tab(self):
        cases = [
            ("ah", "ah-items-lijstjes", "picnic", "ah-items-picnic"),
            ("jumbo", "ah-items-lijstjes", "crisp", "ah-items-crisp-favorieten"),
            ("crisp", "ah-items-crisp-favorieten", "ah", "ah-items-recepten"),
            ("picnic", "ah-items-picnic-favorieten", "jumbo", "ah-items-jumbo-recepten"),
            ("crisp", "ah-items-crisp-favorieten", "ah", "ah-items-recepten"),
        ]
        for start, start_open, store, wanted in cases:
            with self.subTest(f"{start} {start_open} -> {store}"):
                out = self.switch(store, start, start_open)
                self.assertEqual(out["open"], wanted)
                self.assertEqual(out["clicks"], [wanted])

    def test_an_address_chooses_the_section(self):
        # router.js geeft het onderdeel uit het adres mee; zonder onderdeel (/stores/jumbo) opent de eerste tab.
        out = self.run_js(
            """
            open("ah-items-recepten");
            switchItemsStore("jumbo", "orders");
            const orders = result();
            switchItemsStore("picnic", "");
            console.log(JSON.stringify([orders, result()]));
            """
        )
        self.assertEqual(out[0]["open"], "ah-items-bestellingen")
        self.assertEqual(out[0]["clicks"], ["ah-items-bestellingen"])
        self.assertEqual(out[1]["open"], "ah-items-picnic")
        self.assertIn("switchItemsStore(store, section);", ROUTER_JS)

    def test_the_store_menu_links_to_what_a_click_opens(self):
        out = self.run_js(
            """
            open("ah-items-lijstjes");
            const lists = Object.fromEntries(["ah", "jumbo", "picnic", "crisp"].map((store) => [store, itemsStorePath(store)]));
            open("ah-items-picnic-favorieten");
            const favorites = Object.fromEntries(["ah", "jumbo", "picnic", "crisp"].map((store) => [store, itemsStorePath(store)]));
            console.log(JSON.stringify({ lists, favorites, menu: $("#items-store-list").innerHTML }));
            """
        )
        self.assertEqual(out["lists"], {"ah": "/stores/ah/lists", "jumbo": "/stores/jumbo/lists", "picnic": "/stores/picnic/recipes", "crisp": "/stores/crisp/favorites"})
        self.assertEqual(out["favorites"], {"ah": "/stores/ah/recipes", "jumbo": "/stores/jumbo/recipes", "picnic": "/stores/picnic/favorites", "crisp": "/stores/crisp/favorites"})
        # router.js houdt de links in het menu daarna gelijk met dezelfde functie.
        self.assertIn('link.setAttribute("href", itemsStorePath(link.dataset.store))', ROUTER_JS)

    def test_unlinking_the_open_store_keeps_the_section_at_albert_heijn(self):
        out = self.run_js(
            """
            state.itemsStore = "picnic";
            renderItemsStore();
            open("ah-items-picnic-bestellingen");
            clicks.splice(0);
            state.linkedStores.picnic = false; // ontkoppeld: de winkelkeuze valt terug op Albert Heijn
            renderItemsStore();
            console.log(JSON.stringify(result()));
            """
        )
        self.assertEqual(out["store"], "ah")
        self.assertEqual(out["open"], "ah-items-bestellingen")
        self.assertEqual(out["clicks"], ["ah-items-bestellingen"])

    def test_an_open_list_closes_without_a_step_back_in_the_browser(self):
        # closeAhList gaat met history.back() terug als het lijstje uit het overzicht kwam; dat adres hoort bij de vorige winkel.
        body = function_source("switchItemsStore")
        self.assertNotIn("closeAhList", body)
        self.assertIn("state.ahListFromOverview = false;", body)
        self.assertIn("showAhListsOverview({ restoreScroll: false });", body)


if __name__ == "__main__":
    unittest.main()
