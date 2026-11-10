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


def function_source(source, name):
    match = re.search(rf"^function {re.escape(name)}\(.*?^}}$", source, re.S | re.M)
    if not match:
        raise AssertionError(f"functie {name} niet gevonden")
    return match.group(0)


class PurchaseViewsMarkupTest(unittest.TestCase):
    def test_every_purchase_view_shows_the_status_badge(self):
        self.assertIn("purchaseStatusBadge(row)", function_source(ORDERS, "purchaseRow"))
        for name in ("purchaseCard", "purchaseOverviewRow"):
            self.assertIn("purchaseStatusBadge(row)", function_source(VIEWS, name))
        for kind, colour in (("delivered", "--primary"), ("planned", "--info"), ("imported", "--muted"), ("receipt", "--lemon")):
            self.assertRegex(CSS, rf"\.badge--status-{kind} \{{\n  --badge: var\({colour}\);")

    def test_the_status_line_does_not_repeat_the_word_the_badge_shows(self):
        rows = function_source(ORDERS, "purchaseRows")
        self.assertNotIn("orders.kind.delivered", rows)
        self.assertNotIn("orders.kind.open", rows)
        self.assertNotIn("importedOrderStatus", rows)
        self.assertIn("status: slotLabel(order.slotStart, order.slotEnd),", rows)
        self.assertIn("status: importedOrderSlot(", rows)

    def test_the_rows_carry_the_status_kind_and_text_of_the_route(self):
        rows = function_source(ORDERS, "purchaseRows")
        self.assertEqual(rows.count("statusKind: "), 3)
        self.assertEqual(rows.count("statusText: "), 3)
        badge = function_source(VIEWS, "purchaseStatusBadge")
        self.assertIn('"status-" + row.statusKind', badge)
        self.assertIn("PURCHASE_STATUS_TEXT[row.statusKind]", badge)

    def test_the_script_loads_after_orders_and_before_the_tree(self):
        scripts = re.findall(r"filename='([\w/]+\.js)'", HTML)
        self.assertIn("purchase_views.js", scripts)
        self.assertLess(scripts.index("orders.js"), scripts.index("purchase_views.js"))
        self.assertLess(scripts.index("purchase_views.js"), scripts.index("nav_tree.js"))

    def test_the_bar_is_rendered_once_from_javascript_for_both_pages(self):
        self.assertIn('data-view-bar="purchase"', HTML)
        self.assertIn('data-view-bar="order"', HTML)
        self.assertNotIn('id="purchase-view-switch"', HTML)
        self.assertEqual(VIEWS.count("subtab-menu__toggle--select"), 2, "het sorteer- en het optiemenu staan één keer in de code")
        self.assertLess(HTML.index('class="purchase-filters"'), HTML.index('data-view-bar="purchase"'))
        self.assertLess(HTML.index('data-view-bar="purchase"'), HTML.index('id="ah-purchase-table-wrap"'))
        self.assertLess(HTML.index('id="ah-order-meta"'), HTML.index('data-view-bar="order"'))
        self.assertIn('<div class="purchase-bar" data-view-bar="order" hidden></div>', HTML)
        self.assertIn('renderViewBar("purchase", purchaseView, purchaseBarSpec());', VIEWS)

    def test_the_bar_labels_come_from_the_dictionary_in_the_script(self):
        for key in ("orders.view.label", "orders.view.table", "orders.view.cards", "orders.view.overview",
                    "orders.sort.label", "orders.sort.date", "orders.sort.amount", "orders.sort.items", "orders.sort.asc", "orders.sort.desc",
                    "orders.options.label", "orders.options.size", "orders.options.small", "orders.options.medium", "orders.options.large",
                    "orders.options.status", "orders.options.savings", "orders.options.names"):
            self.assertIn(f't("{key}")', VIEWS, key)
            self.assertNotIn(f'data-i18n="{key}"', HTML, key)
        self.assertIn('window.addEventListener("ms:languagechange"', VIEWS)

    def test_the_bar_binds_its_menus_the_way_the_other_menus_do(self):
        render = function_source(VIEWS, "renderViewBar")
        self.assertIn('querySelectorAll(".subtab-menu").forEach(bindSubtabMenu)', render)
        bind = function_source(VIEWS, "bindViewBar")
        self.assertIn("setSubtabMenu(", bind)
        self.assertIn('[data-view-bar="${prefix}"]', VIEWS)

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
        for needle in ("`ms-${prefix}s-view`", "`ms-${prefix}s-sort`", "`ms-${prefix}s-options`",
                       'const PURCHASE_VIEWS = ["table", "cards", "overview"]', 'const PURCHASE_SORT_KEYS = ["date", "amount", "items"]',
                       "function purchaseViewLoad()", "function viewState(prefix, defaults, allowed, { scopes = null } = {})"):
            self.assertIn(needle, VIEWS)
        self.assertIn("} catch {", VIEWS)

    def test_the_rows_are_sorted_in_the_script_with_nulls_last(self):
        self.assertIn("function purchaseSortRows(rows, sort = purchaseView.sort)", VIEWS)
        self.assertIn("const visible = purchaseSortRows(rows.filter((row) => purchaseVisible(row, hits)));", ORDERS)
        self.assertIn("renderPurchaseView(page.shown, hits)", ORDERS)  # de eerste honderd, daarna Meer aankopen (sinds 4.13.0)
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

    def test_the_photo_strip_stands_right_so_every_row_starts_its_text_at_the_same_place(self):
        # Een bon zonder gekoppelde foto's krijgt een icoon, een bestelling zes foto's: links zou de tekst per rij ergens anders
        # beginnen. De strook staat daarom rechts, op een vaste breedte, en de tekst links.
        row = VIEWS.split("function purchaseOverviewRow")[1].split("function renderPurchaseOverview")[0]
        self.assertLess(row.index('<span class="purchase-overview__body">'), row.index('<span class="purchase-overview__strip">'))
        self.assertRegex(CSS, r"\.purchase-overview__strip \{\n(?:.*\n)*?  justify-content: flex-end;")
        self.assertRegex(CSS, r"\.purchase-overview__strip \{\n(?:.*\n)*?  width: 356px;", "zes foto's van 56 met vijf keer 4 ertussen")
        self.assertRegex(CSS, r"@media \(max-width: 640px\) \{\n(?:.*\n)*?  \.purchase-overview__strip \{\n    width: auto;\n    justify-content: flex-start;")

    def test_the_keyboard_focus_ring_is_drawn_around_the_card_and_the_row_not_clipped_inside(self):
        # De kaart en de rij knippen hun inhoud af (overflow hidden): de ring van de link erin zou wegvallen. Daarom tekent de
        # kaart zelf de ring (focus-within) en laat de link de zijne weg.
        self.assertRegex(CSS, r"\.purchase-card:focus-within,\n\.purchase-overview__row:focus-within \{\n  outline: 3px solid var\(--primary\);")
        self.assertRegex(CSS, r"\.purchase-card__link:focus-visible,\n\.purchase-overview__link:focus-visible \{\n  outline: none;")

    def test_a_receipt_shows_its_payment_method_the_way_the_paper_does(self):
        self.assertIn("receiptMethod(row.payment)", VIEWS)
        self.assertNotIn('row.kind === "receipt" ? row.payment : row.status', VIEWS)

    def test_every_view_draws_from_the_same_rows(self):
        self.assertIn('const panes = { table: $("#ah-purchase-table-wrap"), cards: $("#ah-purchase-cards"), overview: $("#ah-purchase-overview") };', VIEWS)
        self.assertIn("renderPurchaseOverview(visible, hits)", VIEWS.split("function renderPurchaseView")[1].split("\n}\n")[0])


if __name__ == "__main__":
    unittest.main()


class CategoryColoursTest(unittest.TestCase):
    def test_every_category_has_a_light_and_a_dark_colour_token(self):
        from mealieshopper import offer_categories

        for category in offer_categories.CATEGORIES:
            self.assertRegex(CSS, rf"\.cat--{category} \{{\n  --cat-bg: #[0-9a-f]{{6}};\n  --cat-line: #[0-9a-f]{{6}};")
            self.assertRegex(CSS, rf':root\[data-theme="dark"\] \.cat--{category} \{{')
