"""Een open popup houdt het vegen voor zich: de pagina erachter scrolt nooit mee.

In de browser nagelopen met echte aanraakgebeurtenissen (vegen op de popup bovenaan en onderaan, op de donkere rand
eromheen, en het muiswiel). Deze test bewaakt de CSS die dat regelt, ook voor popups die er later bij komen.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
SCRIPTS = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "static").glob("*.js"))


def rule(selector):
    match = re.search(rf"^{re.escape(selector)} \{{(.*?)^\}}", CSS, re.S | re.M)
    if not match:
        raise AssertionError(f"regel {selector} niet gevonden in styles.css")
    return match.group(1)


class DialogScrollTest(unittest.TestCase):
    def test_the_page_behind_an_open_popup_does_not_scroll(self):
        # Op <html>, niet op <body>: zonder scrollbar-gutter krijgt html zelf overflow-y en scrolt dan de pagina.
        lock = rule("html:has(dialog[open])")
        self.assertIn("overflow: hidden;", lock)
        self.assertIn("overscroll-behavior: none;", lock)  # geen pull-to-refresh achter de popup

    def test_a_popup_keeps_its_own_scrolling(self):
        popup = rule(".product-dialog")
        self.assertIn("overflow: auto;", popup)
        self.assertIn("overscroll-behavior: contain;", popup)

    def test_every_popup_is_a_product_dialog(self):
        dialogs = re.findall(r"<dialog\b[^>]*>", HTML)
        self.assertGreaterEqual(len(dialogs), 5)
        for tag in dialogs:
            self.assertRegex(tag, r'class="product-dialog\b', tag)

    def test_popups_open_as_modal(self):
        # Alleen een modale popup legt de pagina erachter stil en vangt de tikken; show() zou de pagina laten scrollen.
        self.assertIn(".showModal()", SCRIPTS)
        self.assertNotRegex(SCRIPTS, r"dialog\w*\)?\.show\(\)")


if __name__ == "__main__":
    unittest.main()
