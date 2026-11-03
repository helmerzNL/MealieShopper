"""Het tabblad Mandje: wissel je van winkel terwijl het AH-mandje nog laadt, dan blijft het mandje van de nieuwe winkel staan.

Het AH-mandje opent als eerste. Koos je daarna Picnic, Jumbo of Crisp voordat AH antwoordde, dan tekende het AH-mandje
alsnog in een scherm dat er niet meer was: "Cannot set properties of null (setting 'textContent')" stond dan in een rood
vak en het mandje van de gekozen winkel was weg. `showBasket` (static/basket.js) en `showAhBasket`, `syncAhBasket` en
`renderBasket` (static/orders.js) draaien hier echt, in Node, met een kleine nagebootste pagina. Zonder Node slaan de
tests over.
"""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASKET_JS = (ROOT / "static" / "basket.js").read_text(encoding="utf-8")
ORDERS_JS = (ROOT / "static" / "orders.js").read_text(encoding="utf-8")


def function_source(source, name):
    match = re.search(rf"^(?:async )?function {re.escape(name)}\(.*?^}}$", source, re.S | re.M)
    if not match:
        raise AssertionError(f"functie {name} niet gevonden")
    return match.group(0)


# Een pagina met alleen wat het mandje gebruikt: een element per id. Zet je innerHTML van #basket-content, dan bestaan
# daarna alleen de ids uit die nieuwe HTML, net als in de browser.
PAGE = r"""
const elements = {};
function element(id) {
  const el = { id, textContent: "", disabled: false, classList: { toggle() {} }, addEventListener() {}, dataset: {} };
  let html = "";
  Object.defineProperty(el, "innerHTML", {
    get: () => html,
    set: (value) => {
      for (const child of el.children || []) delete elements[child];
      html = String(value);
      el.children = [...html.matchAll(/id="([^"]+)"/g)].map((m) => m[1]);
      for (const child of el.children) elements[child] = element(child);
    },
  });
  return el;
}
for (const id of ["basket-content", "basket-message"]) elements[id] = element(id);
const $ = (selector) => elements[selector.replace(/^#/, "")] || null;
const messages = [];
function message(target, text) { if (text) messages.push(`${target}: ${text}`); }
const t = (key) => key;
const tn = (key, count) => `${key}:${count}`;
const escapeHtml = (value) => String(value);
function setBusy() {}
function renderBasketStoreChoice() {}
const ordersState = { basket: null };
const basketState = { store: "ah" };

// jsonFetch geeft een belofte die de test zelf inlost, zodat het AH-mandje "nog laadt" terwijl je van winkel wisselt.
const pending = [];
function jsonFetch(url, options = {}) {
  return new Promise((resolve, reject) => pending.push({ url, method: options.method || "GET", resolve, reject }));
}
async function answer(url, value, fail = false) {
  for (let tries = 0; tries < 50; tries += 1) {
    const at = pending.findIndex((call) => call.url === url);
    if (at >= 0) {
      const [call] = pending.splice(at, 1);
      if (fail) call.reject(new Error(value));
      else call.resolve(value);
      for (let i = 0; i < 5; i += 1) await Promise.resolve();
      return true;
    }
    await new Promise((done) => setTimeout(done, 0));
  }
  return false; // geen aanvraag: de app vroeg het niet (meer)
}
async function showPicnicBasket() { $("#basket-content").innerHTML = '<ul id="picnic-lines"></ul>'; }
async function showJumboBasket() {}
async function showCrispBasket() {}
"""


@unittest.skipUnless(shutil.which("node"), "Node is niet geïnstalleerd")
class BasketSwitchTest(unittest.TestCase):
    def run_js(self, scenario):
        functions = [function_source(BASKET_JS, "showBasket")] + [
            function_source(ORDERS_JS, name) for name in ("renderBasket", "showAhBasket", "syncAhBasket")
        ]
        script = "\n".join([PAGE, *functions, "(async () => {", scenario, "})().catch((error) => { console.log(JSON.stringify({ crash: String(error) })); });"])
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_switching_to_picnic_while_ah_loads_keeps_the_picnic_basket(self):
        out = self.run_js(
            """
            const first = showBasket();                       // het AH-mandje opent en vraagt /api/ah/basket
            basketState.store = "picnic";                      // je kiest Picnic voordat AH antwoordt
            const second = showBasket();
            await second;
            await answer("/api/ah/basket", { syncedAt: null }); // nu pas komt het AH-mandje binnen
            await answer("/api/ah/basket/sync", {});
            await answer("/api/ah/basket", { syncedAt: null });
            await first;
            console.log(JSON.stringify({ messages, picnic: Boolean($("#picnic-lines")), ah: Boolean($("#ah-basket")) }));
            """
        )
        self.assertNotIn("crash", out)
        self.assertEqual(out["messages"], [])
        self.assertTrue(out["picnic"])
        self.assertFalse(out["ah"])

    def test_a_failed_ah_sync_after_switching_shows_no_error_at_the_other_store(self):
        out = self.run_js(
            """
            const first = showBasket();
            await answer("/api/ah/basket", { syncedAt: null }); // de kopie staat er; de synchronisatie loopt nog
            basketState.store = "picnic";
            await showBasket();
            await answer("/api/ah/basket/sync", "AH antwoordt niet", true);
            await first;
            console.log(JSON.stringify({ messages, picnic: Boolean($("#picnic-lines")) }));
            """
        )
        self.assertNotIn("crash", out)
        self.assertEqual(out["messages"], [])
        self.assertTrue(out["picnic"])

    def test_the_ah_basket_still_renders_and_reports_errors_when_it_stays_chosen(self):
        out = self.run_js(
            """
            const first = showBasket();
            await answer("/api/ah/basket", { syncedAt: null });
            await answer("/api/ah/basket/sync", "AH antwoordt niet", true);
            await first;
            console.log(JSON.stringify({ messages, note: $("#ah-basket-note").textContent, basket: $("#ah-basket").innerHTML }));
            """
        )
        self.assertNotIn("crash", out)
        self.assertEqual(out["note"], "orders.basket_note")
        self.assertIn("orders.never_synced", out["basket"])
        self.assertEqual(out["messages"], ["#basket-message: AH antwoordt niet"])


if __name__ == "__main__":
    unittest.main()
