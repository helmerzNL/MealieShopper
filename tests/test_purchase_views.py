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


class PurchaseCardsTest(unittest.TestCase):
    def test_the_cards_are_links_with_a_collage_of_cached_photos(self):
        self.assertIn('id="ah-purchase-cards"', HTML)
        for needle in ("function purchaseCollage(row, count)", "function purchaseCard(row, matches)", "function renderPurchaseCards(visible, hits)",
                       'src="${escapeHtml(storeImage(url))}" alt="" loading="lazy" data-hide-on-error',
                       '<a class="purchase-card__link" href="${escapeHtml(row.href)}" data-nav', 'purchaseKindMarkup(row.kind, "stripe")', "purchase-collage--empty"):
            self.assertIn(needle, VIEWS)
        self.assertIn('purchaseView.options.size === "large" ? 6 : 4', VIEWS)

    def test_the_grid_sizes_and_the_phone_layout(self):
        for size, width in (("small", 150), ("medium", 190), ("large", 240)):
            self.assertRegex(CSS, rf"\.purchase-cards--{size} \{{\n  --purchase-card-width: {width}px;")
        self.assertRegex(CSS, r"\.purchase-card \{\n(?:.*\n)*?  content-visibility: auto;")
        self.assertRegex(CSS, r"@media \(max-width: 640px\) \{\n(?:.*\n)*?  \.purchase-cards \{\n    grid-template-columns: repeat\(2, 1fr\);")

    def test_the_options_decide_what_a_card_shows_and_search_shows_the_hits(self):
        for needle in ("purchaseView.options.status", "purchaseView.options.savings", "purchaseView.options.names", "matches.slice(0, 3)", "row.savings > 0"):
            self.assertIn(needle, VIEWS)

    def test_a_purchase_without_photos_shows_the_icon_of_its_kind(self):
        self.assertIn("purchaseKindIcon(row.kind)", VIEWS.split("function purchaseCollage")[1].split("function purchaseDetailLines")[0])
        mapping = VIEWS.split("function purchaseKindIcon")[1].split("}")[0]
        for needle in ("MDI_CART", "MDI_RECEIPT", "MDI_ARCHIVE_OUTLINE"):
            self.assertIn(needle, mapping, needle)


class PurchaseOverviewTest(unittest.TestCase):
    def test_the_overview_is_a_list_of_rows_with_a_photo_strip(self):
        self.assertIn('id="ah-purchase-overview"', HTML)
        for needle in ("function purchaseOverviewRow(row, matches)", "function renderPurchaseOverview(visible, hits)", "row.images.slice(0, 6)",
                       "purchase-overview__strip", '<a class="purchase-overview__link" href="${escapeHtml(row.href)}" data-nav'):
            self.assertIn(needle, VIEWS)
        self.assertIn("purchaseDetailLines(row, matches, { names: false, amount: false })", VIEWS, "het bedrag staat in de kop van de rij, niet nog een keer in de regel eronder")
        self.assertRegex(CSS, r"\.purchase-overview__strip img \{\n(?:.*\n)*?  width: 56px;")
        self.assertRegex(CSS, r"\.purchase-overview__row \{\n(?:.*\n)*?  content-visibility: auto;")
        self.assertRegex(CSS, r"\.purchase-overview__row \{\n(?:.*\n)*?  flex: none;", "een rij mag in de kolom niet krimpen")
        self.assertRegex(CSS, r"@media \(max-width: 640px\) \{\n(?:.*\n)*?  \.purchase-overview__strip img:nth-child\(n \+ 5\) \{\n    display: none;")

    def test_every_view_draws_from_the_same_rows(self):
        self.assertIn('const panes = { table: $("#ah-purchase-table-wrap"), cards: $("#ah-purchase-cards"), overview: $("#ah-purchase-overview") };', VIEWS)
        self.assertIn("renderPurchaseOverview(visible, hits)", VIEWS.split("function renderPurchaseView")[1].split("\n}\n")[0])


if __name__ == "__main__":
    unittest.main()
