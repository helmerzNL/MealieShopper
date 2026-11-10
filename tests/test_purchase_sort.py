"""Het sorteren en het onthouden van de aankopenweergave (static/purchase_views.js) draaien hier echt, in Node: de volgorde
met lege waarden, open bestellingen bovenaan, en wat er gebeurt met rommel in localStorage. Zonder Node slaan de tests over."""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIEWS_JS = (ROOT / "static" / "purchase_views.js").read_text(encoding="utf-8")


APP_JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")


def function_source(name, source=VIEWS_JS):
    match = re.search(rf"^function {re.escape(name)}\(.*?^}}$", source, re.S | re.M)
    if not match:
        raise AssertionError(f"functie {name} niet gevonden")
    return match.group(0)


def const_source(name):
    match = re.search(rf"^const {re.escape(name)} = .*?;$", VIEWS_JS, re.M)
    if not match:
        raise AssertionError(f"constante {name} niet gevonden")
    return match.group(0)


PRELUDE = "\n".join(
    ["const t = (key) => key;"]
    + [function_source(name) for name in ("viewState", "viewStatusId", "viewBarMarkup", "viewSortMenu", "viewOptionsMenu")]
    + [const_source(name) for name in ("MDI_VIEW_TABLE", "MDI_VIEW_CARDS", "MDI_VIEW_LIST", "MDI_CHEVRON_DOWN")]
    + [const_source(name) for name in ("PURCHASE_VIEWS", "PURCHASE_SORT_KEYS", "PURCHASE_SIZES", "PURCHASE_OPTIONS", "purchaseView")]
    + [function_source(name) for name in ("purchaseViewDefault", "purchaseViewLoad", "purchaseSortRows")]
    + [function_source(name, APP_JS) for name in ("badge", "mdiIcon")]
    + ["const escapeHtml = (v) => String(v).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\"/g, '&quot;');"]
)


def row(kind, date, total, items, open=False):
    return {"kind": kind, "id": f"{kind}-{date}-{'null' if total is None else total}", "date": date, "total": total, "items": items, "open": open}


ROWS = [
    row("order", "2026-11-30", None, 0, open=True),
    row("order", "2026-11-03", 197.57, 21),
    row("receipt", "2026-10-30T17:08", 20.48, None),
    row("imported", "2020-02-29", 370.29, 113),
    row("order", "2026-10-29", 336.58, 38),
    row("receipt", "2026-10-28T19:22", 25.62, 8),
]


@unittest.skipUnless(shutil.which("node"), "Node is niet geïnstalleerd")
class PurchaseSortTest(unittest.TestCase):
    def run_js(self, scenario):
        script = f"{PRELUDE}\nconst rows = {json.dumps(ROWS)};\n{scenario}"
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def sorted_ids(self, key, direction):
        return self.run_js(f'purchaseView.sort = {{ key: "{key}", dir: "{direction}" }};\nconsole.log(JSON.stringify(purchaseSortRows(rows).map((r) => r.id)));')

    def test_date_descending_puts_open_orders_first_and_ascending_does_not(self):
        self.assertEqual(self.sorted_ids("date", "desc")[:3], ["order-2026-11-30-null", "order-2026-11-03-197.57", "receipt-2026-10-30T17:08-20.48"])
        self.assertEqual(self.sorted_ids("date", "asc")[0], "imported-2020-02-29-370.29")
        self.assertEqual(self.sorted_ids("date", "asc")[-1], "order-2026-11-30-null", "oplopend staat de open bestelling gewoon op zijn datum, als laatste")

    def test_a_missing_amount_or_count_ends_up_last_in_both_directions(self):
        self.assertEqual(self.sorted_ids("amount", "desc")[-1], "order-2026-11-30-null")
        self.assertEqual(self.sorted_ids("amount", "asc")[-1], "order-2026-11-30-null")
        self.assertEqual(self.sorted_ids("amount", "asc")[0], "receipt-2026-10-30T17:08-20.48")
        self.assertEqual(self.sorted_ids("items", "desc")[-1], "receipt-2026-10-30T17:08-20.48", "producten nog niet opgehaald")
        self.assertEqual(self.sorted_ids("items", "asc")[-1], "receipt-2026-10-30T17:08-20.48")
        self.assertEqual(self.sorted_ids("items", "asc")[0], "order-2026-11-30-null", "0 producten is een echte waarde, geen lege")

    def test_the_badge_helper_escapes_and_names_the_kind(self):
        self.assertEqual(self.run_js('console.log(JSON.stringify(badge("saved", "<b>", "x")));'), '<span class="badge badge--saved" title="x">&lt;b&gt;</span>')
        self.assertEqual(self.run_js('console.log(JSON.stringify(badge("before", "2x")));'), '<span class="badge badge--before">2x</span>')

    def test_sorting_does_not_touch_the_original_rows(self):
        self.assertTrue(self.run_js('purchaseView.sort = { key: "amount", dir: "asc" };\nconst before = JSON.stringify(rows);\npurchaseSortRows(rows);\nconsole.log(JSON.stringify(JSON.stringify(rows) === before));'))

    def test_bad_values_in_local_storage_fall_back_to_the_defaults(self):
        for stored in ('{"ms-purchases-view": "x", "ms-purchases-sort": "\\"rommel\\"", "ms-purchases-options": "[1,2]"}',
                       '{"ms-purchases-sort": "{\\"key\\":\\"nope\\",\\"dir\\":\\"desc\\"}", "ms-purchases-options": "{\\"size\\":\\"huge\\",\\"status\\":\\"x\\"}"}',
                       '{"ms-purchases-sort": "{not json"}'):
            with self.subTest(stored=stored):
                state = self.run_js(f"const store = {stored};\nglobalThis.localStorage = {{ getItem: (k) => (k in store ? store[k] : null) }};\npurchaseViewLoad();\nconsole.log(JSON.stringify(purchaseView));")
                self.assertEqual(state, {"view": "table", "sort": {"key": "date", "dir": "desc"}, "options": {"size": "medium", "status": True, "savings": True, "names": True}})

    def test_valid_values_in_local_storage_are_applied(self):
        state = self.run_js('const store = {"ms-purchases-view": "cards", "ms-purchases-sort": "{\\"key\\":\\"amount\\",\\"dir\\":\\"asc\\"}", "ms-purchases-options": "{\\"size\\":\\"large\\",\\"names\\":false}"};\nglobalThis.localStorage = { getItem: (k) => (k in store ? store[k] : null) };\npurchaseViewLoad();\nconsole.log(JSON.stringify(purchaseView));')
        self.assertEqual(state, {"view": "cards", "sort": {"key": "amount", "dir": "asc"}, "options": {"size": "large", "status": True, "savings": True, "names": False}})

    def test_the_bar_markup_carries_the_prefix_in_every_id(self):
        html = self.run_js('console.log(JSON.stringify(viewBarMarkup("order", { views: [{ id: "cards", icon: "", label: "Kaarten" }], sorts: [{ key: "name", label: "Naam" }], sizes: true, options: [] })));')
        for needle in ('id="order-view-switch"', 'data-view="cards"', 'id="order-sort-menu"', 'id="order-sort-toggle"', 'id="order-sort-current"',
                       'id="order-sort-list"', 'data-sort-key="name"', 'id="order-options-menu"', 'id="order-options-toggle"', 'id="order-options-list"',
                       'data-size="large"', 'id="order-status"'):
            self.assertIn(needle, html)
        self.assertNotIn("purchase", html.replace("purchase-bar__tools", "").replace("purchase-status", "").replace("purchase-options", ""))

    def test_the_list_bar_keeps_its_status_line_id_and_draws_every_choice(self):
        html = self.run_js('console.log(JSON.stringify(viewBarMarkup("purchase", { views: [{ id: "table", icon: MDI_VIEW_TABLE, label: "<Tabel>" }], sorts: [], sizes: false, options: [{ key: "names", label: "Productnamen" }] })));')
        self.assertIn('id="ah-purchase-status"', html)
        self.assertIn('<svg class="mdi" viewBox="0 0 24 24" aria-hidden="true"><path d="M5,4H19', html)
        self.assertIn("&lt;Tabel&gt;", html, "labels worden ge-escaped")
        self.assertIn('data-option="names"', html)
        self.assertNotIn('id="purchase-sort-menu"', html, "zonder sorteersleutels geen sorteermenu")
        self.assertNotIn("data-size=", html, "zonder kaartgroottes geen groottes in het optiemenu")

    def test_the_list_keeps_its_storage_keys(self):
        stored = self.run_js('const store = {};\nglobalThis.localStorage = { setItem: (k, v) => { store[k] = v; } };\nconst state = viewState("purchase", { view: "table", sort: { key: "date", dir: "desc" }, options: { size: "medium", names: true } }, { views: PURCHASE_VIEWS, sorts: PURCHASE_SORT_KEYS, sizes: PURCHASE_SIZES, options: PURCHASE_OPTIONS });\nstate.view = "cards";\nstate.save();\nconsole.log(JSON.stringify(store));')
        self.assertEqual(stored, {"ms-purchases-view": "cards", "ms-purchases-sort": '{"key":"date","dir":"desc"}', "ms-purchases-options": '{"size":"medium","names":true}'})
        keys = self.run_js('const store = {};\nglobalThis.localStorage = { setItem: (k, v) => { store[k] = v; } };\nviewState("order", { view: "cards", sort: { key: "name", dir: "asc" }, options: {} }, { views: ["cards"], sorts: ["name"], sizes: [], options: [] }).save();\nconsole.log(JSON.stringify(Object.keys(store)));')
        self.assertEqual(keys, ["ms-orders-view", "ms-orders-sort", "ms-orders-options"])

    def test_two_states_do_not_share_their_defaults_or_their_choices(self):
        state = self.run_js('const defaults = { view: "cards", sort: { key: "name", dir: "asc" }, options: { size: "small" } };\nconst allowed = { views: ["cards", "table"], sorts: ["name"], sizes: ["small", "large"], options: [] };\nconst a = viewState("a", defaults, allowed);\nconst b = viewState("b", defaults, allowed);\na.view = "table";\na.options.size = "large";\na.sort.dir = "desc";\nconsole.log(JSON.stringify([b, defaults]));')
        self.assertEqual(state[0], {"view": "cards", "sort": {"key": "name", "dir": "asc"}, "options": {"size": "small"}})
        self.assertEqual(state[1], state[0])

    def test_sort_by_flips_the_direction_of_the_same_key_and_starts_a_new_key_descending(self):
        sorts = self.run_js('globalThis.localStorage = { setItem() {} };\nconst changes = [];\nconst state = viewState("x", { view: "a", sort: { key: "date", dir: "desc" }, options: {} }, { views: ["a"], sorts: ["date", "amount"], sizes: [], options: [] });\nstate.listen(() => changes.push(JSON.stringify(state.sort)));\nstate.sortBy("date");\nstate.sortBy("amount");\nstate.sortBy(null, "asc");\nstate.sortBy("date", "asc");\nconsole.log(JSON.stringify(changes));')
        self.assertEqual(sorts, ['{"key":"date","dir":"asc"}', '{"key":"amount","dir":"desc"}', '{"key":"amount","dir":"asc"}', '{"key":"date","dir":"asc"}'])

    def test_every_sort_key_of_the_list_starts_descending(self):
        # De lijst houdt het gedrag van voor 3.8: datum, bedrag en aantal beginnen aflopend, uit de spec van de balk.
        starts = self.run_js(f"{function_source('purchaseBarSpec')}\n{function_source('viewSortStart')}\nconsole.log(JSON.stringify(purchaseBarSpec().sorts.map((sort) => [sort.key, viewSortStart(purchaseBarSpec(), sort.key)])));")
        self.assertEqual(starts, [["date", "desc"], ["amount", "desc"], ["items", "desc"]])
        menu = self.run_js(f"{function_source('purchaseBarSpec')}\nconsole.log(JSON.stringify(viewSortMenu('purchase', purchaseBarSpec().sorts)));")
        self.assertIn('data-sort-key="amount" data-sort-start="desc"', menu)

    def test_without_local_storage_the_defaults_stay(self):
        state = self.run_js("globalThis.localStorage = { getItem() { throw new Error('geblokkeerd'); } };\npurchaseViewLoad();\nconsole.log(JSON.stringify(purchaseView.view));")
        self.assertEqual(state, "table")


if __name__ == "__main__":
    unittest.main()
