"""Ingelezen bestellingen bij Bestellingen (#229), echt gedraaid in Node: een bestelling uit je account zonder producten valt weg
naast haar ingelezen versie (op nummer of dag, met de datum van elke winkel), de status en het tijdvak komen uit de mail of anders
uit die weggevallen bestelling, de soort heet gewoon Bestelling met het archief-icoon erachter, en bij Picnic en Crisp krijgt een
ingelezen bestelling de vorm van een eigen bestelling (met de producten voor Alles in mandje). Zonder Node slaan de tests over."""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = {name: (ROOT / "static" / name).read_text(encoding="utf-8") for name in ("orders.js", "purchase_views.js", "picnic.js", "crisp.js")}


def function_source(file, name):
    match = re.search(rf"^function {re.escape(name)}\(.*?^}}$", SOURCES[file], re.S | re.M)
    if not match:
        raise AssertionError(f"functie {name} niet gevonden in {file}")
    return match.group(0)


STRINGS = {
    "orders.kind.order": "Bestelling", "orders.kind.receipt": "Kassabon", "orders.kind.delivered": "Afgeleverd",
    "orders.imported.badge": "Geïmporteerd uit je bevestigingsmails",
}

PRELUDE = "\n".join([
    'const I18N = { locale: () => "nl-NL" };',
    f"const STRINGS = {json.dumps(STRINGS, ensure_ascii=False)};",
    "const t = (key) => STRINGS[key] || key;",
    'const tn = (key, count) => `${count} ${key}`;',
    'const escapeHtml = (value) => String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");',
    'const mdiIcon = (path) => `<svg aria-hidden="true"><path d="${path}"/></svg>`;',
    'const MDI_ARCHIVE_OUTLINE = "ARCHIEF";',
    *(function_source("orders.js", name) for name in ("slotLabel", "importedOrdersCover", "importedOrdersMerge", "importedOrderStatus",
                                                        "importedStoreOrder", "importedUnlinkedNote")),
    *(function_source("purchase_views.js", name) for name in ("purchaseKindText", "purchaseKindLabel")),
    function_source("picnic.js", "picnicOrderDate"),
    function_source("crisp.js", "crispOrderDate"),
])

# Bestellingen uit je Jumbo-account: twee oude zonder producten (12 en 5 jan), een met producten (19 jan), een open bestelling.
JUMBO = [
    {"id": "6005201234", "kind": "delivered", "deliveryDate": "2022-01-12", "itemsSynced": False, "itemCount": 0},
    {"id": "6005209999", "kind": "delivered", "deliveryDate": "2022-01-05", "itemsSynced": False, "itemCount": 0,
     "slotStart": "2022-01-05T18:00:00", "slotEnd": "2022-01-05T21:00:00"},
    {"id": "6005204444", "kind": "delivered", "deliveryDate": "2022-01-19", "itemsSynced": True, "itemCount": 38},
    {"id": "6005205555", "kind": "open", "deliveryDate": "2022-01-26", "itemsSynced": False, "itemCount": 0},
]
IMPORTED = [
    {"ref": "6005201234", "number": "6005201234", "date": "2022-01-12", "imported": True, "status": "delivered",
     "slotStart": "2022-01-12T18:00", "slotEnd": "2022-01-12T20:00"},
    {"ref": "dag:2022-01-05", "number": None, "date": "2022-01-05", "imported": True, "status": None, "slotStart": None, "slotEnd": None},
    {"ref": "777", "number": "777", "date": "2022-01-19", "imported": True},
    {"ref": "888", "number": "888", "date": "2022-01-26", "imported": True},
]


@unittest.skipUnless(shutil.which("node"), "Node is niet geïnstalleerd")
class ImportedOrdersUiTest(unittest.TestCase):
    def run_js(self, scenario):
        script = f"{PRELUDE}\nconst jumbo = {json.dumps(JUMBO)};\nconst imported = {json.dumps(IMPORTED)};\n{scenario}"
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_an_empty_account_order_drops_out_next_to_its_imported_version(self):
        found = self.run_js("const { kept, covered } = importedOrdersCover(jumbo, imported);\n"
                            "console.log(JSON.stringify({ kept: kept.map((o) => o.id), covered: Object.fromEntries([...covered].map(([ref, o]) => [ref, o.id])) }));")
        # Op nummer (12 jan) en op dag (5 jan) valt de lege bestelling weg; die met producten en de open bestelling blijven.
        self.assertEqual(found["kept"], ["6005204444", "6005205555"])
        self.assertEqual(found["covered"], {"6005201234": "6005201234", "dag:2022-01-05": "6005209999"})

    def test_without_imported_orders_nothing_drops_out(self):
        self.assertEqual(self.run_js("console.log(JSON.stringify(importedOrdersCover(jumbo, []).kept.length));"), 4)

    def test_every_store_compares_on_its_own_date(self):
        picnic = [{"id": "p1", "status": "COMPLETED", "deliveredStart": "2022-01-12T17:31:00+01:00", "itemsSynced": False},
                  {"id": "p2", "status": "COMPLETED", "deliveredStart": "2022-01-13T17:31:00+01:00", "itemsSynced": True, "itemCount": 4}]
        crisp = [{"id": "c1", "status": "done", "windowStart": "2022-01-12T08:00:00+01:00", "itemsSynced": True, "itemCount": 0}]
        found = self.run_js(f"const picnic = {json.dumps(picnic)}; const crisp = {json.dumps(crisp)};\n"
                            "const one = [{ ref: \"x\", number: null, date: \"2022-01-12\", imported: true }];\n"
                            "console.log(JSON.stringify([importedOrdersCover(picnic, one, picnicOrderDate).kept.map((o) => o.id),"
                            " importedOrdersCover(crisp, one, crispOrderDate).kept.map((o) => o.id)]));")
        self.assertEqual(found, [["p2"], []])

    def test_the_merged_list_is_newest_first_with_the_imported_orders_shaped_like_the_store(self):
        found = self.run_js("const past = importedOrdersMerge(jumbo, imported, (o) => o.deliveryDate, (o, from) => ({ ...o, from: from ? from.id : null }));\n"
                            "console.log(JSON.stringify(past.map((o) => [o.imported ? o.ref : o.id, o.from ?? null])));")
        self.assertEqual(found, [["6005205555", None], ["888", None], ["6005204444", None], ["777", None], ["6005201234", "6005201234"],
                                 ["dag:2022-01-05", "6005209999"]])

    def test_the_status_comes_from_the_mail_or_else_from_the_order_that_dropped_out(self):
        found = self.run_js("const { covered } = importedOrdersCover(jumbo, imported);\n"
                            "console.log(JSON.stringify(imported.map((o) => importedOrderStatus(o, covered.get(o.ref)))));")
        self.assertEqual(found, ["Afgeleverd · 18:00-20:00", "Afgeleverd · 18:00-21:00", "", ""])

    def test_the_kind_is_order_with_the_archive_icon_behind_it(self):
        found = self.run_js('console.log(JSON.stringify(["imported", "order", "receipt"].map(purchaseKindLabel)));')
        imported, order, receipt = found
        self.assertTrue(imported.startswith("Bestelling<span"), "het icoon staat achter het woord, zodat Bestelling uitlijnt")
        self.assertIn('role="img" aria-label="Geïmporteerd uit je bevestigingsmails" title="Geïmporteerd uit je bevestigingsmails"', imported)
        self.assertIn("ARCHIEF", imported)
        self.assertEqual((order, receipt), ("Bestelling", "Kassabon"))
        self.assertNotIn("orders.kind.imported", SOURCES["orders.js"] + SOURCES["purchase_views.js"])

    def test_picnic_and_crisp_get_an_imported_order_in_their_own_shape(self):
        order = {"ref": "r1", "imported": True, "date": "2022-01-12", "status": "delivered", "slotStart": "2022-01-12T17:20",
                 "slotEnd": "2022-01-12T17:40", "itemCount": 2, "units": 5,
                 "items": [{"title": "Kipfilet", "productId": "s1001", "amount": 10.98, "price": 5.49, "quantity": 2},
                           {"title": "Iets uit de mail", "productId": None, "amount": 3.0, "price": 1.0, "quantity": 3}]}
        found = self.run_js(f"const order = {json.dumps(order)};\n"
                            "const shaped = importedStoreOrder(order, null, \"COMPLETED\");\n"
                            "const bare = importedStoreOrder({ ...order, status: null, slotStart: null, slotEnd: null },"
                            " { status: \"done\", windowStart: \"2022-01-12T08:00:00+01:00\", windowEnd: \"2022-01-12T10:00:00+01:00\" }, \"done\");\n"
                            "console.log(JSON.stringify({ shaped, bare: [bare.status, bare.windowStart, bare.windowEnd],"
                            " note: importedUnlinkedNote(shaped), account: importedUnlinkedNote({ items: [{ id: null }] }) }));")
        shaped = found["shaped"]
        self.assertEqual((shaped["status"], shaped["windowStart"], shaped["windowEnd"], shaped["itemCount"], shaped["itemsSynced"]),
                         ("COMPLETED", "2022-01-12T17:20", "2022-01-12T17:40", 5, True))
        self.assertEqual([(i["id"], i["price"]) for i in shaped["items"]], [("s1001", 10.98), (None, 3.0)],
                         "het product voor Alles in mandje, en het bedrag van de hele regel zoals Picnic en Crisp het bewaren")
        self.assertEqual(found["bare"], ["done", "2022-01-12T08:00:00+01:00", "2022-01-12T10:00:00+01:00"])
        self.assertEqual((found["note"], found["account"]), ("1 orders.imported.unlinked", ""))


if __name__ == "__main__":
    unittest.main()
