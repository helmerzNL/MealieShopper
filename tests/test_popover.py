"""De hovervensters op de aankopenlijst (static/popover.js): de productfoto groot en een bestelling of bon per categorie. Het
plaatsen, de inhoud van beide vensters en het ophalen van de details draaien hier echt, in Node, uit de bronnen van de functies
(zoals tests/test_purchase_sort.py); zonder Node slaan die tests over. De markup-tests lezen de template, de scripts en de CSS."""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
ORDERS = (ROOT / "static" / "orders.js").read_text(encoding="utf-8")
VIEWS = (ROOT / "static" / "purchase_views.js").read_text(encoding="utf-8")
ORDER_VIEWS = (ROOT / "static" / "order_views.js").read_text(encoding="utf-8")
CATEGORIES = (ROOT / "static" / "categories.js").read_text(encoding="utf-8")
POPOVER_PATH = ROOT / "static" / "popover.js"
POPOVER = POPOVER_PATH.read_text(encoding="utf-8") if POPOVER_PATH.exists() else ""


def function_source(source, name):
    match = re.search(
        rf"^(?:async )?function {re.escape(name)}\(.*?^}}$",
        source,
        re.DOTALL | re.MULTILINE,
    )
    if not match:
        raise AssertionError(f"functie {name} niet gevonden")
    return match.group(0)


def const_source(source, name):
    match = re.search(rf"^const {re.escape(name)} = .*?;$", source, re.MULTILINE)
    if not match:
        raise AssertionError(f"constante {name} niet gevonden")
    return match.group(0)


POPOVER_FUNCTIONS = (
    "popoverAnchorLink",
    "popoverAllowed",
    "popoverPlace",
    "popoverProduct",
    "popoverPurchase",
    "popoverThumbs",
    "popoverGroup",
    "purchaseDetail",
    "purchaseDetailKey",
    "popoverForget",
    "purchaseDetailUrl",
)


def prelude():
    return "\n".join(
        [
            "const t = (key, params = {}) => key + (Object.keys(params).length ? ' ' + Object.entries(params).map(([k, v]) => `${k}=${v}`).join(',') : '');",
            "const tn = (key, count, params = {}) => t(key, { count, ...params });",
            "const escapeHtml = (v) => String(v ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\"/g, '&quot;');",
            "const money = (v) => (v === null || v === undefined ? '' : 'EUR ' + Number(v).toFixed(2));",
            "const storeImage = (url) => '/img/' + url;",
            "const priceHistoryPath = (store, title, id = '') => `/offers/history?store=${store}&q=${title}&product=${id}`;",
            "const I18N = { formatNumber: (n) => String(n) };",
            "const badge = (kind, text, title = '') => `<span class=\"badge badge--${kind}\">${escapeHtml(text)}</span>`;",
            "const offerBadge = (offer) => (offer.badge && typeof offer.badge.percent === 'number' ? `<span class=\"offer-badge\">-${offer.badge.percent}%</span>` : '');",
            "const purchaseStatusBadge = (row) => badge('status-' + row.statusKind, row.statusText || row.statusKind);",
            const_source(CATEGORIES, "CATEGORY_ORDER"),
            function_source(CATEGORIES, "categoryLabel"),
            function_source(CATEGORIES, "categoryChip"),
        ]
        + [
            function_source(ORDER_VIEWS, name)
            for name in (
                "orderLines",
                "orderLine",
                "orderLineSaved",
                "orderGroups",
                "orderGroupSummary",
                "orderLineBadges",
            )
        ]
        + [
            const_source(POPOVER, "POPOVER_GAP"),
            const_source(POPOVER, "POPOVER_EDGE"),
            const_source(POPOVER, "POPOVER_THUMBS"),
            const_source(POPOVER, "POPOVER_SMALL_ABOVE"),
            const_source(POPOVER, "purchaseDetailCache"),
        ]
        + [function_source(POPOVER, name) for name in POPOVER_FUNCTIONS]
    )


ROW = {
    "kind": "order",
    "id": "473954549",
    "label": "zo 21 sep 2026",
    "date": "2026-09-21",
    "total": 197.57,
    "statusKind": "delivered",
    "statusText": "",
    "store": "ah",
}


@unittest.skipUnless(shutil.which("node"), "Node is niet geïnstalleerd")
class PopoverNodeTest(unittest.TestCase):
    def run_expr(self, expression: str) -> str:
        """De waarde van een expressie als tekst (String() in Node)."""
        return self.run_js(f"console.log(String({expression}));", raw=True)

    def run_js(self, scenario, raw=False):
        self.assertTrue(POPOVER, "static/popover.js bestaat niet")
        items = [
            {
                "title": f"Product {name}",
                "category": name,
                "quantity": 2,
                "amount": 3.0,
                "image": f"https://static.ah.nl/{name}.jpg",
            }
            for name in json.loads(
                const_source(CATEGORIES, "CATEGORY_ORDER")
                .split(" = ", 1)[1]
                .rstrip(";")
            )
        ]
        script = f"{prelude()}\nconst row = {json.dumps(ROW)};\nconst lines19Categories = {json.dumps(items)};\n{scenario}"
        result = subprocess.run(
            ["node", "-e", script],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip() if raw else json.loads(result.stdout)

    def run_json(self, expression):
        return self.run_js(f"console.log(JSON.stringify({expression}));")

    def test_the_popover_is_for_a_mouse_only(self):
        self.assertEqual(
            self.run_expr(
                'popoverAllowed((q) => ({ matches: q.includes("hover: hover") }))'
            ),
            "true",
        )
        self.assertEqual(
            self.run_expr("popoverAllowed(() => ({ matches: false }))"), "false"
        )

    def test_the_popover_asks_for_hover_and_a_fine_pointer_together(self):
        self.assertEqual(
            self.run_expr(
                'popoverAllowed((q) => ({ matches: q === "(hover: hover) and (pointer: fine)" }))'
            ),
            "true",
        )

    def test_the_popover_flips_above_when_it_does_not_fit_below(self):
        place = self.run_json(
            "popoverPlace({ left: 100, top: 700, bottom: 756, width: 56 }, { width: 420, height: 300 }, { width: 1280, height: 800 })"
        )
        self.assertEqual(
            place, {"left": 100, "top": 400 - 8}
        )  # boven het anker, 8 px ertussen

    def test_the_popover_sits_below_when_it_fits(self):
        place = self.run_json(
            "popoverPlace({ left: 100, top: 100, bottom: 156, width: 56 }, { width: 420, height: 300 }, { width: 1280, height: 800 })"
        )
        self.assertEqual(place, {"left": 100, "top": 164})

    def test_the_popover_shifts_left_when_it_does_not_fit_on_the_right(self):
        place = self.run_json(
            "popoverPlace({ left: 1100, top: 100, bottom: 156, width: 56 }, { width: 420, height: 300 }, { width: 1280, height: 800 })"
        )
        self.assertEqual(place, {"left": 1280 - 16 - 420, "top": 164})

    def test_a_popover_that_fits_neither_below_nor_above_stays_within_the_viewport(
        self,
    ):
        place = self.run_json(
            "popoverPlace({ left: 10, top: 300, bottom: 360, width: 900 }, { width: 420, height: 600 }, { width: 1280, height: 800 })"
        )
        self.assertEqual(place, {"left": 16, "top": 800 - 16 - 600})

    def test_a_big_order_uses_small_thumbs_and_scrolls_inside(self):
        html = self.run_expr("popoverPurchase(row, { items: lines19Categories })")
        self.assertIn("popover__thumbs--small", html)
        self.assertRegex(CSS, r"\.popover \{[^}]*max-height: calc\(100vh - 32px\)")

    def test_an_order_of_fourteen_categories_keeps_the_normal_thumbs(self):
        html = self.run_expr(
            "popoverPurchase(row, { items: lines19Categories.slice(0, 14) })"
        )
        self.assertNotIn("popover__thumbs--small", html)
        self.assertEqual(html.count('class="popover__cat '), 14)

    def test_the_purchase_popover_shows_a_line_per_category_with_six_thumbs_at_most(
        self,
    ):
        scenario = (
            "const items = Array.from({ length: 8 }, (_, i) => ({ title: 'Vis ' + i, category: 'vis', quantity: 1, amount: 2, image: 'https://static.ah.nl/vis' + i + '.jpg' }))"
            ".concat([{ title: 'Peen', category: 'groente_fruit', quantity: 3, amount: 1.5, image: null }]);\n"
            "console.log(JSON.stringify(popoverPurchase(row, { items, total: 17.5, saved: 2.4, savedPercent: 12 })));"
        )
        html = self.run_js(scenario)
        self.assertIn("zo 21 sep 2026", html)
        self.assertIn("badge--status-delivered", html)
        self.assertIn('orders.page.products count=9', html)
        self.assertIn('orders.page.categories count=2', html)
        self.assertLess(
            html.index("cat--groente_fruit"),
            html.index("cat--vis"),
            "de volgorde van de categorieën",
        )
        self.assertIn('popover.group units=8,kinds=8', html)
        self.assertEqual(html.count("/img/https://static.ah.nl/vis"), 6)
        self.assertIn('popover.more count=2', html)
        self.assertIn("EUR 16.00", html, "het subtotaal van de vis")
        self.assertIn("badge--saved", html)
        self.assertIn('orders.saved_percent amount=EUR 2.40,percent=12', html)
        self.assertIn('popover.total amount=EUR 17.50', html)

    def test_a_purchase_without_lines_says_the_products_are_not_fetched_yet(self):
        html = self.run_expr("popoverPurchase(row, { items: [] })")
        self.assertIn("orders.items_pending", html)
        self.assertNotIn("popover__cat ", html)

    def test_the_product_popover_shows_the_big_photo_the_data_the_chip_the_discount_and_times_before(
        self,
    ):
        scenario = (
            "const [line] = orderLines({ items: [{ title: 'AH Knijpfruit <appel>', brand: 'AH', unitSize: '4 x 90 g', category: 'groente_fruit', quantity: 2, price: 1.39, paidEach: 1.19, amount: 2.38,"
            " image: 'https://static.ah.nl/knijp.jpg', discount: 14, discountSource: 'history', timesBefore: 3 }] }, 'order', 'ah');\n"
            "console.log(JSON.stringify(popoverProduct(line)));"
        )
        html = self.run_js(scenario)
        self.assertIn("popover--product cat--groente_fruit", html)
        self.assertIn('src="/img/https://static.ah.nl/knijp.jpg"', html)
        self.assertIn("AH Knijpfruit &lt;appel&gt;", html)
        self.assertIn("AH · 4 x 90 g", html)
        self.assertIn('class="offer-chip cat--groente_fruit', html)
        self.assertIn("offers.cat.groente_fruit", html)
        self.assertIn(
            'popover.amount quantity=2,price=EUR 1.19,amount=EUR 2.38', html
        )
        self.assertIn('<span class="offer-badge">-14%</span>', html)
        self.assertIn('popover.before count=3', html)
        self.assertNotIn("popover.last", html, "geen prijs en dag van een eerdere aankoop: die zou een extra verzoek kosten")
        self.assertNotIn("<button", html, "een tooltip heeft geen knoppen")

    def test_a_receipt_line_adds_up_what_was_paid_after_the_bonus(self):
        # Op een bon is het bedrag van de regel de prijs vóór de bonus; het venster rekent met wat je per stuk betaalde.
        scenario = (
            "const [line] = orderLines({ items: [{ name: 'AH RED VELV', product: { title: 'AH Red velvet aardbei taart', image: 'https://static.ah.nl/taart.jpg' }, category: 'brood_bakkerij',"
            " quantity: 1, price: 7.99, amount: 7.99, paidEach: 5.19, bonus: 2.8, discount: 35 }] }, 'receipt', 'ah');\n"
            "console.log(JSON.stringify(popoverProduct(line)));"
        )
        html = self.run_js(scenario)
        self.assertIn("popover.amount quantity=1,price=EUR 5.19,amount=EUR 5.19", html)

    def test_a_product_bought_for_the_first_time_has_no_history_line(self):
        scenario = (
            "const [line] = orderLines({ items: [{ title: 'Peen', category: 'groente_fruit', quantity: 1, price: 0.89, amount: 0.89, image: 'https://static.ah.nl/peen.jpg' }] }, 'order', 'ah');\n"
            "console.log(JSON.stringify(popoverProduct(line)));"
        )
        html = self.run_js(scenario)
        self.assertNotIn("popover.before", html)
        self.assertNotIn("popover__history", html)

    def test_a_click_on_the_popover_goes_to_the_link_of_its_element(self):
        # Een foto ligt binnen de link (kaart, overzicht), een tabelrij of rij heeft de link erin.
        scenario = (
            "const link = { href: '/stores/ah/orders/1' };\n"
            "const photo = { closest: (s) => (s === 'a[data-nav]' ? link : null), querySelector: () => null };\n"
            "const tr = { closest: () => null, querySelector: (s) => (s === 'a[data-nav]' ? link : null) };\n"
            "const bare = { closest: () => null, querySelector: () => null };\n"
            "console.log(JSON.stringify([popoverAnchorLink(photo) === link, popoverAnchorLink(tr) === link, popoverAnchorLink(bare)]));"
        )
        self.assertEqual(self.run_js(scenario), [True, True, None])

    def test_details_come_from_the_route_of_each_kind_once_per_purchase(self):
        scenario = (
            "const urls = []; const jsonFetch = async (url) => { urls.push(url); return { url }; };\n"
            "(async () => {\n"
            "  await purchaseDetail('order', '473954549', 'ah'); await purchaseDetail('order', '473954549', 'ah');\n"
            "  await purchaseDetail('order', '77', 'jumbo'); await purchaseDetail('receipt', 'AH 1/2', 'ah'); await purchaseDetail('imported', 'r-1', 'crisp');\n"
            "  console.log(JSON.stringify(urls));\n"
            "})();"
        )
        self.assertEqual(
            self.run_js(scenario),
            [
                "/api/ah/orders/473954549",
                "/api/jumbo/orders/77",
                "/api/ah/receipts/AH%201%2F2",
                "/api/imported-orders/crisp/r-1",
            ],
        )

    def test_a_failed_detail_is_not_kept_so_the_next_hover_tries_again(self):
        scenario = (
            "let calls = 0; const jsonFetch = async () => { calls += 1; if (calls === 1) throw new Error('boom'); return { ok: true }; };\n"
            "(async () => {\n"
            "  try { await purchaseDetail('order', '1', 'ah'); } catch {}\n"
            "  const detail = await purchaseDetail('order', '1', 'ah');\n"
            "  console.log(JSON.stringify([calls, detail]));\n"
            "})();"
        )
        self.assertEqual(self.run_js(scenario), [2, {"ok": True}])

    def test_forgetting_one_purchase_fetches_only_that_one_again(self):
        scenario = (
            "const urls = []; const jsonFetch = async (url) => { urls.push(url); return { url }; };\n"
            "(async () => {\n"
            "  await purchaseDetail('receipt', 'R1', 'ah'); await purchaseDetail('receipt', 'R2', 'ah');\n"
            "  popoverForget(purchaseDetailKey('receipt', 'R1', 'ah'));\n"
            "  await purchaseDetail('receipt', 'R1', 'ah'); await purchaseDetail('receipt', 'R2', 'ah');\n"
            "  console.log(JSON.stringify(urls));\n"
            "})();"
        )
        self.assertEqual(
            self.run_js(scenario),
            ["/api/ah/receipts/R1", "/api/ah/receipts/R2", "/api/ah/receipts/R1"],
        )


class PopoverMarkupTest(unittest.TestCase):
    def test_rows_cards_and_photos_announce_their_popover(self):
        self.assertIn(
            'data-popover="purchase"', function_source(VIEWS, "purchaseOverviewRow")
        )
        self.assertIn(
            'data-popover="product"', function_source(VIEWS, "purchaseCollage")
        )
        self.assertIn('data-popover="purchase"', function_source(ORDERS, "purchaseRow"))

    def test_the_card_and_the_strip_photos_announce_theirs_too(self):
        self.assertIn('data-popover="purchase"', function_source(VIEWS, "purchaseCard"))
        self.assertIn(
            'data-popover="product"', function_source(VIEWS, "purchaseOverviewRow")
        )
        self.assertIn("data-image=", function_source(VIEWS, "purchaseCollage"))

    def test_the_popover_script_loads_right_after_the_order_views(self):
        scripts = re.findall(r"filename='([\w/]+\.js)'", HTML)
        self.assertEqual(scripts[scripts.index("order_views.js") + 1], "popover.js")

    def test_the_hover_waits_400_ms_closes_on_escape_and_skips_buttons(self):
        source = function_source(POPOVER, "hoverPopover")
        self.assertIn("POPOVER_DELAY", source)
        self.assertIn("const POPOVER_DELAY = 400;", POPOVER)
        self.assertIn('"Escape"', source)
        self.assertIn('closest("button")', source)
        self.assertIn("popoverAllowed()", source)
        self.assertIn('role", "tooltip"', source)
        self.assertIn("document.body.append", source)

    def test_a_scroll_hides_the_popover_and_waits_again_for_the_element_under_the_mouse(self):
        source = function_source(POPOVER, "hoverPopover")
        scroll = source[source.index('addEventListener("scroll"'):]
        self.assertIn("timer = window.setTimeout(() => show(target), POPOVER_DELAY)", scroll)
        self.assertIn('target.matches(":hover")', source, "na het schuiven alleen als de muis er nog op staat")

    def test_a_click_on_the_popover_is_forwarded_to_the_anchor_link_and_hides_it(self):
        source = function_source(POPOVER, "hoverPopover")
        click = source[source.index('popover.addEventListener("click"'):]
        click = click[: click.index("});") + 3]
        self.assertIn('event.target.closest("a, button")', click, "een link of knop in het venster doet zijn eigen werk")
        self.assertIn("popoverAnchorLink(", click)
        self.assertIn("hide()", click)
        self.assertIn("link.click()", click, "via de router, zoals een klik op de link zelf")
        self.assertIn("routerModified(event)", click, "met een toets erbij een nieuw tabblad, zoals de link zelf")

    def test_a_route_change_or_any_click_outside_hides_the_popover_even_with_a_modifier(self):
        # De router stopt een Cmd/Ctrl-klik in de capture-fase op document; een eigen capture-handler die eerder geregistreerd is
        # (popover.js laadt voor router.js) ziet hem nog. Alt+Links en de knop Terug geven popstate.
        source = function_source(POPOVER, "hoverPopover")
        handler = next(line for line in source.splitlines() if 'document.addEventListener("click"' in line)
        self.assertIn("!popover.contains(event.target)", handler)
        self.assertIn("hide()", handler)
        self.assertIn("{ capture: true }", handler)
        self.assertIn('window.addEventListener("popstate", hide)', source)
        self.assertNotIn('root.addEventListener("click", hide)', source, "de capture-handler op document vervangt die op de lijst")
        scripts = re.findall(r"filename='([\w/]+\.js)'", HTML)
        self.assertLess(scripts.index("popover.js"), scripts.index("router.js"), "de capture-handler moet voor die van de router staan")

    def test_the_detail_cache_is_cleared_when_the_list_loads(self):
        self.assertIn(
            "purchaseDetailCache.clear()", function_source(ORDERS, "loadOrders")
        )

    def test_the_product_popover_has_the_category_border_and_the_grid(self):
        self.assertRegex(
            CSS, r"\.popover--product \{[^}]*border-left: 5px solid var\(--cat-line\)"
        )
        self.assertRegex(
            CSS,
            r"\.popover__cat \{[^}]*grid-template-columns: 14px 150px 90px 1fr 70px",
        )
        self.assertRegex(CSS, r"\.popover__thumbs img \{[^}]*width: 28px")
        self.assertRegex(CSS, r"\.popover__thumbs--small img \{[^}]*width: 22px")
        self.assertRegex(CSS, r"\.popover \{[^}]*overflow: auto")


if __name__ == "__main__":
    unittest.main()
