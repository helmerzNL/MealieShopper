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
    def test_orders_and_receipts_share_one_table_with_filters(self):
        # Vinkjes voor de soorten, een bedrag van en tot, en de tabel met de kolommen.
        for needle in ('id="ah-filter-orders"', 'id="ah-filter-receipts"', 'id="ah-filter-min"', 'id="ah-filter-max"',
                       '<table class="purchase-table">', 'id="ah-purchase-rows"', 'id="ah-purchase-table-wrap"'):
            self.assertIn(needle, HTML)
        self.assertNotIn('id="ah-receipt-list"', HTML)
        self.assertNotIn('id="ah-order-upcoming"', HTML)
        # Elke rij is een bestelling, een ingelezen bestelling of een kassabon, met de link die het detail opent.
        for needle in ('attr: `data-order-id="${escapeHtml(order.id)}"`', 'attr: `data-imported-order="${escapeHtml(order.ref)}"`',
                       'attr: `data-receipt-id="${escapeHtml(receipt.id)}"`', "function purchaseVisible(row, hits)",
                       'row.kind === "receipt" ? !purchaseFilters.receipts : !purchaseFilters.orders'):
            self.assertIn(needle, ORDERS)
        # Zoeken op product: een verzoek met alles erin, een kwart seconde na de laatste toets, en de tabel filtert op de hits.
        self.assertIn('limit: "all"', ORDERS)
        self.assertIn("purchaseTimer = setTimeout(searchPurchases, 250);", ORDERS)
        self.assertIn("return !hits || hits.has(`${row.kind}:${row.id}`);", ORDERS)
        # De tabel vult het scherm tot onderaan en scrolt in zijn eigen vak.
        self.assertIn("const height = pageFitHeight(wrap);", ORDERS)
        self.assertRegex(CSS, r"\.purchase-table-wrap \{\n  overflow: auto;")
        self.assertRegex(CSS, r"\.purchase-table thead th \{\n  position: sticky;")

    def test_a_receipt_line_links_to_the_webshop_product_by_id_or_not_at_all(self):
        self.assertIn(
            'priceHistoryPath("ah", line.product.title, line.product.id)', ORDERS
        )
        self.assertNotIn(
            'priceHistoryPath("ah", line.name)',
            ORDERS,
            "zoeken op de bonnaam vindt niets",
        )
        self.assertIn("resolveReceiptProducts(receipt.id)", ORDERS)
        self.assertIn('/products`, { method: "POST" }', ORDERS)

    def test_the_price_history_fits_the_screen_on_a_wide_screen(self):
        # De lijst en het product krijgen allebei de hoogte tot de onderkant van de pagina en scrollen in hun eigen vak.
        self.assertRegex(CSS, r"\.ohc-list \{\n  display: grid;(?:.*\n)*?  max-height: 420px;(?:.*\n)*?@media \(min-width: 900px\) \{\n  \.ohc-detail \{\n    overflow-y: auto;")
        history = (ROOT / "static" / "offers_history.js").read_text(encoding="utf-8")
        self.assertIn("window.requestAnimationFrame(() => offerHistoryFitList(list));", history)
        self.assertIn('const height = pageFitHeight(list.parentElement, { minWidth: 900 });', history)
        self.assertIn('$("#offers-history-detail").style.maxHeight = value;', history)
        app = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("window.innerHeight - (rect.top + window.scrollY) - (shell.bottom - rect.bottom)", app)

    def test_the_receipt_box_scrolls_inside_on_a_wide_screen(self):
        # De bon zelf is een smalle strook (test_receipt_views); het vak eromheen krijgt op een breed scherm precies de hoogte die er
        # onder de kop past, gemeten (pageFitHeight) en niet geraden met 100vh, en scrolt zelf, zodat de pagina niet schuift.
        self.assertNotRegex(CSS, r"\.receipt \{\n(?:.*\n)*?    min-height: calc\(100vh")
        orders = (ROOT / "static" / "orders.js").read_text(encoding="utf-8")
        self.assertIn("function fitReceiptBox()", orders)
        self.assertIn("pageFitHeight(box)", orders)
        self.assertIn("if (ordersState.loaded) fitPurchaseTable();\n  fitReceiptBox();", orders)


if __name__ == "__main__":
    unittest.main()
