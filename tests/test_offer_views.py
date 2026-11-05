"""De gedeelde categoriechips (static/categories.js) draaien hier echt, in Node. Zonder Node slaan de tests over."""

import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATEGORIES_JS = (ROOT / "static" / "categories.js").read_text(encoding="utf-8") if (ROOT / "static" / "categories.js").exists() else ""
OFFERS_JS = (ROOT / "static" / "offers.js").read_text(encoding="utf-8")


def function_source(source, name):
    match = re.search(rf"^function {re.escape(name)}\(.*?^}}$", source, re.S | re.M)
    if not match:
        raise AssertionError(f"functie {name} niet gevonden")
    return match.group(0)


STUBS = """
const t = (key, vars) => key + JSON.stringify(vars || {});
const escapeHtml = (s) => String(s);
const I18N = { formatNumber: (n) => String(n) };
"""


@unittest.skipUnless(shutil.which("node"), "Node is niet geïnstalleerd")
class CategoryChipsTest(unittest.TestCase):
    def run_js(self, expression):
        functions = "\n".join(function_source(CATEGORIES_JS, name) for name in ("categoryLabel", "categoryChipLabel", "categoryChips"))
        script = f"{STUBS}\n{functions}\nconsole.log({expression});"
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_the_chips_carry_the_category_class_the_dot_and_the_active_state(self):
        html = self.run_js('categoryChips({ categories: [{ id: "vlees", count: 2 }, { id: "vis", count: 1 }], active: "vis", total: 3, allLabel: "Alle (3)" })')
        self.assertIn('class="offer-chip cat--all', html)
        self.assertIn('class="offer-chip cat--vlees" data-cat="vlees" aria-pressed="false"', html)
        self.assertIn('class="offer-chip cat--vis is-active" data-cat="vis" aria-pressed="true"', html)
        self.assertEqual(html.count('<i class="offer-chip__dot"'), 3)

    def test_the_all_chip_is_first_and_active_by_default(self):
        html = self.run_js('categoryChips({ categories: [{ id: "vis", count: 1 }], active: "all", total: 1, allLabel: "Alle (1)" })')
        self.assertLess(html.index('data-cat="all"'), html.index('data-cat="vis"'))
        self.assertIn('class="offer-chip cat--all is-active" data-cat="all" aria-pressed="true"', html)
        self.assertIn("Alle (1)", html)

    def test_offers_render_their_chips_through_the_shared_function(self):
        self.assertIn("categoryChips(", OFFERS_JS)
        # De chips per gang (data-course) zijn geen categorieen en houden hun eigen opbouw.
        self.assertNotIn("data-cat=", OFFERS_JS)
