"""Het zijmenu als boom op een groot scherm (static/nav_tree.js): de subtabs onder hun onderdeel, de subtabbalk boven de inhoud weg."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
TREE = (ROOT / "static" / "nav_tree.js").read_text(encoding="utf-8")
ORDERS = (ROOT / "static" / "orders.js").read_text(encoding="utf-8")


def desktop_css() -> str:
    """Het blok met het zijmenu op een groot scherm."""
    start = CSS.index("@media (min-width: 1024px) {\n  .topbar__inner,")
    end = CSS.index("@media (min-width: 1024px) and (prefers-reduced-motion: reduce)")
    return CSS[start:end]


class NavTreeTest(unittest.TestCase):
    def test_the_tree_script_loads_after_the_app_and_before_the_router(self):
        scripts = re.findall(r"filename='([\w/]+\.js)'", HTML)
        self.assertLess(scripts.index("app.js"), scripts.index("nav_tree.js"))
        self.assertEqual(
            scripts[-2:],
            ["nav_tree.js", "router.js"],
            "de boom gebruikt de links van de router",
        )

    def test_tree_items_are_links_that_the_router_opens(self):
        self.assertIn('class="tab-tree__item', TREE)
        self.assertIn("data-route", TREE)
        self.assertIn('escapeHtml(link.getAttribute("href")', TREE)

    def test_the_tree_is_hidden_unless_the_menu_is_open_on_a_wide_screen_and_fits(self):
        self.assertRegex(CSS, r"\n\.tab-tree \{\n  display: none;")
        desktop = desktop_css()
        self.assertIn("html.nav-tree:not(.nav-collapsed) .tab-tree {", desktop)
        self.assertIn(
            "html.nav-tree:not(.nav-collapsed) .subtabs--tree > a.subtab,", desktop
        )
        self.assertIn(
            "html.nav-tree:not(.nav-collapsed) .subtabs--tree.subtabs--empty {", desktop
        )
        # Past de boom niet onder het menu, dan valt html.nav-tree weg en staan de subtabbalken er weer.
        self.assertIn(
            "NAV_TREE_TOP + tabs.scrollHeight + NAV_TREE_BOTTOM <= window.innerHeight",
            TREE,
        )
        self.assertIn('root.classList.toggle("nav-tree", fits)', TREE)

    def test_a_bar_with_a_store_menu_keeps_that_menu(self):
        self.assertIn(
            'bar.classList.toggle("subtabs--empty", !bar.querySelector(".subtab-menu:not([hidden])"))',
            TREE,
        )

    def test_the_tree_follows_the_bars(self):
        for needle in (
            "new MutationObserver(navTreeSchedule)",
            'attributeFilter: ["class", "hidden", "href"]',
            '"ms:languagechange"',
            '"resize"',
        ):
            self.assertIn(needle, TREE)


class ReceiptsTest(unittest.TestCase):
    def test_a_receipt_in_the_list_is_one_row_and_the_row_is_the_link(self):
        self.assertIn('<div id="ah-receipt-list" class="receipt-rows"></div>', HTML)
        self.assertIn(
            '<a class="receipt-row" data-receipt-id="${escapeHtml(receipt.id)}" href="${escapeHtml(storesPath("ah", "receipts", receipt.id))}" data-nav>',
            ORDERS,
        )
        self.assertNotIn('class="button button--secondary" data-receipt-id', ORDERS)
        # Op een groot scherm vult de lijst de rest van het scherm en scrolt hij in zijn eigen vak.
        self.assertRegex(
            CSS,
            r"@media \(min-width: 1024px\) \{\n  \.receipt-rows \{\n    max-height: calc\(100vh - \d+px\);\n    padding-right: 4px;\n    overflow-y: auto;",
        )

    def test_a_receipt_line_links_to_the_webshop_product_by_id_or_not_at_all(self):
        self.assertIn(
            'priceHistoryPath("ah", line.product.title, line.product.id)', ORDERS
        )
        self.assertNotIn(
            'priceHistoryPath("ah", line.name)',
            ORDERS,
            "zoeken op de bonnaam vindt niets",
        )
        self.assertIn("resolveReceiptProducts(receipt.id, words)", ORDERS)
        self.assertIn('/products`, { method: "POST" }', ORDERS)

    def test_the_receipt_paper_fills_its_frame_on_a_wide_screen(self):
        self.assertRegex(CSS, r"\.receipt \{\n(?:.*\n)*?  width: 100%;")
        self.assertRegex(
            CSS,
            r"@media \(min-width: 1024px\) \{\n  \.receipt \{\n    min-height: calc\(100vh - \d+px\);",
        )


if __name__ == "__main__":
    unittest.main()
