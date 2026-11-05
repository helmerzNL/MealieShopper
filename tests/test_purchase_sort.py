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
    [const_source(name) for name in ("PURCHASE_VIEWS", "PURCHASE_SORT_KEYS", "PURCHASE_SIZES", "PURCHASE_OPTIONS", "purchaseView")]
    + [function_source(name) for name in ("purchaseViewLoad", "purchaseSortRows")]
    + [function_source("badge", APP_JS), "const escapeHtml = (v) => String(v).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\"/g, '&quot;');"]
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

    def test_without_local_storage_the_defaults_stay(self):
        state = self.run_js("globalThis.localStorage = { getItem() { throw new Error('geblokkeerd'); } };\npurchaseViewLoad();\nconsole.log(JSON.stringify(purchaseView.view));")
        self.assertEqual(state, "table")


if __name__ == "__main__":
    unittest.main()
