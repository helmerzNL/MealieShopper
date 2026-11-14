"""De weergaven van een kassabon: het bonnetje zoals op papier (receiptPaper in static/orders.js), als smalle strook, en de
producten met de namen en foto's van de webwinkel. De keuze zit in de weergavebalk van de bestelpagina (static/order_views.js,
weergave Bon) en blijft per soort aankoop bewaard."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
ORDERS = (ROOT / "static" / "orders.js").read_text(encoding="utf-8")
ORDER_VIEWS = (ROOT / "static" / "order_views.js").read_text(encoding="utf-8")
NL = (ROOT / "static" / "i18n" / "nl.js").read_text(encoding="utf-8")
EN = (ROOT / "static" / "i18n" / "en.js").read_text(encoding="utf-8")


def function_source(name, source=ORDERS):
    match = re.search(
        rf"^(?:async )?function {re.escape(name)}\(.*?^}}$",
        source,
        re.DOTALL | re.MULTILINE,
    )
    if not match:
        raise AssertionError(f"functie {name} niet gevonden")
    return match.group(0)


class ReceiptPaperTest(unittest.TestCase):
    def test_the_paper_is_a_narrow_strip_in_the_middle_again(self):
        # Zo breed als een bon uit de kassa, niet zo breed als de pagina: anders is het een tabel in een monospace-lettertype.
        self.assertRegex(
            CSS,
            r"\.receipt \{\n(?:.*\n)*?  width: min\(100%, 400px\);\n  margin: 12px auto 28px;",
        )
        self.assertNotRegex(CSS, r"\.receipt \{\n[^}]*  width: 100%;")

    def test_a_matching_line_is_highlighted_on_paper(self):
        # De andere weergaven laten alleen de gevonden regels zien. Op het bonnetje blijft de hele bon staan, met de treffers opgelicht.
        self.assertRegex(CSS, r"\.receipt__hit \{\n  background: ")
        self.assertIn("receiptPaper(purchase, words)", function_source("renderOrderItems", ORDER_VIEWS))

    def test_the_box_scrolls_inside_on_a_wide_screen_instead_of_the_page(self):
        box = function_source("fitReceiptBox")
        self.assertIn("pageFitHeight(box, { min: PURCHASE_MIN_HEIGHT })", box)
        self.assertIn("orderPage.purchase", box, "voor elke weergave van een geopende aankoop")
        self.assertIn('box.classList.toggle("order-items--fit", height !== null)', box)
        self.assertIn("if (ordersState.loaded) fitPurchaseTable();\n  fitReceiptBox();", ORDERS)
        self.assertNotIn("fitReceiptPaper", ORDERS)
        self.assertRegex(CSS, r"\.order-items--fit \{\n  overflow-y: auto;")


class ReceiptViewSwitchTest(unittest.TestCase):
    def test_the_bar_of_the_order_page_has_the_paper_view_for_a_receipt_only(self):
        self.assertNotIn('id="receipt-view"', HTML, "geen eigen schakelaar meer in de kop van de bon")
        self.assertIn(
            'const ORDER_SCOPE_VIEWS = { order: ["category", "cards", "table"], receipt: ["category", "cards", "table", "paper"] };', ORDER_VIEWS
        )
        spec = function_source("orderBarSpec", ORDER_VIEWS)
        self.assertIn('if (kind === "receipt") views.push({ id: "paper", icon: MDI_RECEIPT, label: t("orders.view.paper") });', spec)

    def test_the_choice_is_remembered_per_kind_and_a_receipt_starts_on_paper(self):
        self.assertNotIn("ms-receipt-view", ORDERS)
        self.assertNotIn("receiptState", ORDERS)
        self.assertIn('const ORDER_VIEW_DEFAULTS = { order: "category", receipt: "paper" };', ORDER_VIEWS)
        self.assertIn('{ scopes: ["order", "receipt"] }', ORDER_VIEWS, "ms-orders-view bewaart de weergave per soort")
        self.assertIn("orderView.useScope(orderScope(kind));", function_source("renderOrderPage", ORDER_VIEWS))
        self.assertIn("orderViewLoad();", ORDER_VIEWS)

    def test_every_view_draws_from_the_same_receipt_and_relinking_redraws_it(self):
        render = function_source("renderOrderItems", ORDER_VIEWS)
        self.assertIn("else if (paper) box.innerHTML = receiptPaper(purchase, words);", render)
        self.assertIn('const summary = kind === "receipt" ? receiptBonusSummary(purchase) : "";', render)
        self.assertIn('renderOrderPage(receipt, "receipt", "ah");', function_source("openPurchase"))
        self.assertIn('renderOrderPage(receipt, "receipt", "ah");', function_source("resolveReceiptProducts"))
        self.assertIn("orderPageClose();", function_source("showPurchaseOverview"))

    def test_the_products_show_webshop_names_and_photos_and_the_bonus_lines(self):
        line = function_source("orderLine", ORDER_VIEWS)
        self.assertIn("const product = receipt ? item.product || null : null;", line)
        self.assertIn("(product && product.title) || name", line, "de titel van de webwinkel, anders de naam op de bon")
        self.assertIn("href: title && (!receipt || product) ?", line, "zonder koppeling geen link")
        summary = function_source("receiptBonusSummary")
        self.assertIn('class="receipt-summary"', summary)
        self.assertIn("receipt.discounts", summary)
        self.assertIn('t("orders.receipt.summary.bonus"', summary)
        meta = function_source("receiptMeta")
        for needle in ('tn("list.items", units)', "money(receipt.total)", "receiptMethod("):
            self.assertIn(needle, meta)
        self.assertNotIn('t("orders.saved"', meta, "het bespaarde bedrag staat in de badge in de kop")

    def test_both_languages_have_the_texts(self):
        for key in ("orders.view.paper", "orders.receipt.summary.bonus"):
            self.assertIn(f'"{key}":', NL, key)
            self.assertIn(f'"{key}":', EN, key)
        self.assertNotIn('"orders.receipt.view.label":', NL)


if __name__ == "__main__":
    unittest.main()
