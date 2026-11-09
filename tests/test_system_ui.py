"""Fase 4 van de kritische blik op de interface (4.9.0): het systeem eronder.

- Eén segmentbediening (.segmented) voor een keuze binnen een scherm; .subtabs blijft navigatie, met aria-current op het actieve tabblad.
- Lange uitleg ingeklapt achter "Hoe werkt dit?" (details.help), met één zichtbare regel ervoor.

De typeschaal, de radius-tokens en de drie breekpunten staan in tests/test_brand.py.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
HTML = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
CSS = (STATIC / "styles.css").read_text(encoding="utf-8")
APP = (STATIC / "app.js").read_text(encoding="utf-8")
VIEWS = (STATIC / "purchase_views.js").read_text(encoding="utf-8")


class SegmentedTest(unittest.TestCase):
    def test_the_three_choices_share_one_control(self):
        self.assertIn('<div class="segmented" role="radiogroup" aria-label="Wat plan je?"', HTML)
        self.assertEqual(HTML.count('<label class="segmented__item"><input type="radio" name="plan-kind"'), 2)
        self.assertIn('<div id="offers-view" class="segmented" role="group"', HTML)
        self.assertEqual(HTML.count('<button class="segmented__item segmented__item--icon" type="button" data-view='), 2)
        self.assertIn('<button class="segmented__item" type="button" data-view="${escapeHtml(view.id)}" aria-pressed="false">', VIEWS)
        self.assertIn('<div class="segmented" id="${prefix}-view-switch" role="group"', VIEWS)
        for old in ("segment-control", "plan-kind__option", "purchase-view-switch"):
            self.assertNotIn(old, HTML + CSS + VIEWS, old)

    def test_every_segment_is_a_full_target_with_a_visible_focus(self):
        item = re.search(r"\n\.segmented__item \{(.*?)\n\}", CSS, re.S).group(1)
        self.assertIn("min-width: var(--control-height);", item)
        self.assertIn("min-height: var(--control-height);", item)
        track = re.search(r"\n\.segmented \{(.*?)\n\}", CSS, re.S).group(1)
        self.assertNotIn("overflow: hidden", track, "anders valt de focusring weg")
        self.assertIn(".segmented__item.is-active,\n.segmented__item[aria-pressed=\"true\"],\n.segmented__item:has(input:checked) {", CSS)
        self.assertIn(".segmented__item:focus-visible,\n.segmented__item:has(input:focus-visible) {\n  outline: 2px solid var(--primary);", CSS)

    def test_a_view_button_is_no_longer_a_subtab(self):
        # Een klik op een subtab haalde is-active weg bij elke .subtab in het paneel, ook bij de weergaveknoppen.
        self.assertNotIn('class="subtab" type="button" data-view', VIEWS)
        self.assertNotIn('classList.toggle("is-active", active)', VIEWS)


class SubtabsTest(unittest.TestCase):
    def test_the_open_tab_is_announced(self):
        for match in re.finditer(r'<a class="subtab[^"]*is-active[^"]*" data-subtab="[^"]*"[^>]*>', HTML):
            self.assertIn('aria-current="page"', match.group(0))
        handler = APP[APP.index('document.querySelectorAll(".subtab[data-subtab], .subtab-menu__item[data-subtab]").forEach'):]
        handler = handler[: handler.index("const target = panel.querySelector")]
        self.assertIn('item.removeAttribute("aria-current");', handler)
        self.assertIn('subtab.setAttribute("aria-current", "page");', handler)


if __name__ == "__main__":
    unittest.main()
