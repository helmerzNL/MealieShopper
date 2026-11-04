"""De weergaven van de aankopen (static/purchase_views.js): tabel, kaarten en overzicht van dezelfde gefilterde lijst."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
ORDERS = (ROOT / "static" / "orders.js").read_text(encoding="utf-8")
VIEWS_PATH = ROOT / "static" / "purchase_views.js"
VIEWS = VIEWS_PATH.read_text(encoding="utf-8") if VIEWS_PATH.exists() else ""


class PurchaseViewsMarkupTest(unittest.TestCase):
    def test_the_script_loads_after_orders_and_before_the_tree(self):
        scripts = re.findall(r"filename='([\w/]+\.js)'", HTML)
        self.assertIn("purchase_views.js", scripts)
        self.assertLess(scripts.index("orders.js"), scripts.index("purchase_views.js"))
        self.assertLess(scripts.index("purchase_views.js"), scripts.index("nav_tree.js"))

    def test_the_bar_has_the_switch_the_sort_menu_and_the_options_menu(self):
        for needle in ('id="purchase-view-switch"', 'data-view="table"', 'data-view="cards"', 'data-view="overview"',
                       'id="purchase-sort-menu"', 'data-sort-key="date"', 'data-sort-key="amount"', 'data-sort-key="items"', 'data-sort-dir="asc"',
                       'id="purchase-options-menu"', 'data-size="small"', 'data-size="large"', 'data-option="status"', 'data-option="savings"', 'data-option="names"'):
            self.assertIn(needle, HTML)
        self.assertLess(HTML.index('class="purchase-filters"'), HTML.index('class="purchase-bar"'))
        self.assertLess(HTML.index('class="purchase-bar"'), HTML.index('id="ah-purchase-table-wrap"'))
        self.assertLess(HTML.index('class="purchase-bar"'), HTML.index('id="ah-purchase-status"'), "de statusregel staat in de balk")

    def test_every_kind_has_a_colour_dot_in_the_table_and_at_the_checkboxes(self):
        self.assertIn("purchase-kind--order", HTML.split('id="ah-filter-orders"')[1][:200])
        self.assertIn("purchase-kind--receipt", HTML.split('id="ah-filter-receipts"')[1][:200])
        self.assertIn('purchaseKindMarkup(row.kind, "dot")', ORDERS)
        for kind, colour in (("order", "--primary"), ("receipt", "--lemon"), ("imported", "--muted")):
            self.assertRegex(CSS, rf"\.purchase-kind--{kind} \{{\n  --purchase-kind: var\({colour}\);")

    def test_the_table_headers_sort(self):
        for key in ("date", "items", "amount"):
            self.assertIn(f'class="purchase-table__sort" data-sort="{key}"', HTML)

    def test_the_state_is_remembered_and_bad_values_fall_back(self):
        for needle in ('localStorage.getItem("ms-purchases-view")', 'localStorage.getItem("ms-purchases-sort")', 'localStorage.getItem("ms-purchases-options")',
                       'const PURCHASE_VIEWS = ["table", "cards", "overview"]', 'const PURCHASE_SORT_KEYS = ["date", "amount", "items"]',
                       "function purchaseViewLoad()", "function purchaseViewSave()"):
            self.assertIn(needle, VIEWS)
        self.assertIn("} catch {", VIEWS)

    def test_the_rows_are_sorted_in_the_script_with_nulls_last(self):
        self.assertIn("function purchaseSortRows(rows)", VIEWS)
        self.assertIn("const visible = purchaseSortRows(rows.filter((row) => purchaseVisible(row, hits)));", ORDERS)
        self.assertIn("renderPurchaseView(visible, hits)", ORDERS)
        self.assertIn("function renderPurchaseTable(visible, hits)", ORDERS)
        self.assertIn("if (va === null || va === undefined) return vb === null || vb === undefined ? String(b.date).localeCompare(String(a.date)) : 1;", VIEWS)
        self.assertIn("if (vb === null || vb === undefined) return -1;", VIEWS, "nulls onderaan, in beide richtingen")

    def test_the_rows_carry_what_the_cards_and_the_overview_need(self):
        for needle in ("images: order.images || []", "names: order.productNames || []", "savings: order.savings", "bonusCount: order.bonusCount",
                       "names: receipt.productNames || []", "payment: receipt.payment"):
            self.assertIn(needle, ORDERS)


if __name__ == "__main__":
    unittest.main()
