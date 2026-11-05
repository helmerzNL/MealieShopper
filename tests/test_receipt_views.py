"""De twee weergaven van een kassabon (static/orders.js): het bonnetje zoals op papier, als smalle strook, en de producten met de
namen en foto's van de webwinkel, met een schakelaar in de kop die bewaard blijft."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
ORDERS = (ROOT / "static" / "orders.js").read_text(encoding="utf-8")
NL = (ROOT / "static" / "i18n" / "nl.js").read_text(encoding="utf-8")
EN = (ROOT / "static" / "i18n" / "en.js").read_text(encoding="utf-8")


def function_source(name):
    match = re.search(
        rf"^(?:async )?function {re.escape(name)}\(.*?^}}$",
        ORDERS,
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

    def test_a_matching_line_is_highlighted_on_paper_and_on_a_card(self):
        self.assertRegex(CSS, r"\.receipt__hit \{\n  background: ")
        self.assertRegex(CSS, r"\.product-card\.receipt__hit \{\n")

    def test_the_box_scrolls_inside_on_a_wide_screen_instead_of_the_page(self):
        box = function_source("fitReceiptBox")
        self.assertIn("pageFitHeight(box)", box)
        self.assertIn('box.classList.toggle("order-items--fit", height !== null)', box)
        self.assertIn(
            "if (ordersState.loaded) fitPurchaseTable();\n  fitReceiptBox();", ORDERS
        )
        self.assertNotIn("fitReceiptPaper", ORDERS)
        self.assertRegex(CSS, r"\.order-items--fit \{\n  overflow-y: auto;")


class ReceiptViewSwitchTest(unittest.TestCase):
    def test_the_header_of_the_receipt_has_a_switch_between_paper_and_products(self):
        detail = HTML[
            HTML.index('id="ah-order-detail"') : HTML.index('id="ah-order-items"')
        ]
        for needle in (
            'id="receipt-view"',
            'class="segment-control"',
            'data-view="paper"',
            'data-view="products"',
            "hidden",
            'data-i18n-attr="aria-label:orders.receipt.view.label"',
            'data-i18n-attr="title:orders.receipt.view.paper;aria-label:orders.receipt.view.paper"',
            'data-i18n-attr="title:orders.receipt.view.products;aria-label:orders.receipt.view.products"',
        ):
            self.assertIn(needle, detail, needle)
        self.assertLess(
            detail.index('id="ah-order-title"'), detail.index('id="receipt-view"')
        )
        self.assertLess(
            detail.index('id="receipt-view"'), detail.index('id="ah-order-meta"')
        )

    def test_the_choice_is_remembered_and_bad_values_fall_back_to_paper(self):
        self.assertIn('const RECEIPT_VIEWS = ["paper", "products"];', ORDERS)
        self.assertIn('const RECEIPT_VIEW_KEY = "ms-receipt-view";', ORDERS)
        self.assertIn(
            'const receiptState = { receipt: null, words: [], view: "paper" };', ORDERS
        )
        load = function_source("receiptViewLoad")
        self.assertIn("localStorage.getItem(RECEIPT_VIEW_KEY)", load)
        self.assertIn(
            "if (RECEIPT_VIEWS.includes(view)) receiptState.view = view;", load
        )
        self.assertIn(
            "localStorage.setItem(RECEIPT_VIEW_KEY, receiptState.view)", ORDERS
        )
        self.assertIn('$("#receipt-view").addEventListener("click"', ORDERS)

    def test_the_switch_only_shows_for_a_receipt_and_both_views_draw_from_the_same_receipt(
        self,
    ):
        open_purchase = function_source("openPurchase")
        self.assertIn('$("#receipt-view").hidden = kind !== "receipt";', open_purchase)
        self.assertIn("receiptState.receipt = receipt;", open_purchase)
        self.assertIn("renderReceipt();", open_purchase)
        self.assertNotIn(
            "receiptPaper(",
            open_purchase,
            "het tekenen zit in renderReceipt, voor beide weergaven",
        )
        render = function_source("renderReceipt")
        self.assertIn(
            'receiptState.view === "products" ? receiptProducts(receipt, receiptState.words) : receiptPaper(receipt, receiptState.words)',
            render,
        )
        self.assertIn(
            'button.setAttribute("aria-pressed", String(button.dataset.view === receiptState.view))',
            render,
        )
        resolve = function_source("resolveReceiptProducts")
        self.assertIn("receiptState.receipt = receipt;\n    renderReceipt();", resolve)
        self.assertIn(
            '$("#receipt-view").hidden = true;', function_source("showPurchaseOverview")
        )

    def test_the_products_view_shows_webshop_names_and_photos_and_the_bonus_lines(self):
        products = function_source("receiptProducts")
        self.assertIn(
            "purchaseItem(",
            products,
            "dezelfde kaart als een product in een bestelling",
        )
        self.assertIn("line.product", products)
        self.assertIn('<div class="product-grid">', products)
        self.assertIn('class="receipt-summary"', products)
        self.assertIn("receipt.discounts", products)
        self.assertIn('t("orders.receipt.summary.bonus"', products)
        self.assertIn(
            "receipt__hit",
            products,
            "bij zoeken lichten de gevonden producten op, zoals op papier",
        )
        meta = function_source("receiptMeta")
        for needle in (
            'tn("list.items", units)',
            "money(receipt.total)",
            "receiptMethod(",
            't("orders.saved"',
        ):
            self.assertIn(needle, meta)

    def test_both_languages_have_the_texts(self):
        for key in (
            "orders.receipt.view.label",
            "orders.receipt.view.paper",
            "orders.receipt.view.products",
            "orders.receipt.summary.bonus",
        ):
            self.assertIn(f'"{key}":', NL, key)
            self.assertIn(f'"{key}":', EN, key)


if __name__ == "__main__":
    unittest.main()
