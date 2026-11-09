"""De bestelpagina (static/order_views.js): een bestelling of bon per categorie, als kaarten, als tabel of als bon, met zoeken,
categoriechips en sorteren. Het groeperen, filteren, sorteren en onthouden draait hier echt, in Node, uit de bronnen van de functies
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
OFFERS = (ROOT / "static" / "offers.js").read_text(encoding="utf-8")
CATEGORIES = (ROOT / "static" / "categories.js").read_text(encoding="utf-8")
PURCHASE_VIEWS = (ROOT / "static" / "purchase_views.js").read_text(encoding="utf-8")
ORDER_VIEWS_PATH = ROOT / "static" / "order_views.js"
ORDER_VIEWS = (
    ORDER_VIEWS_PATH.read_text(encoding="utf-8") if ORDER_VIEWS_PATH.exists() else ""
)
NL = (ROOT / "static" / "i18n" / "nl.js").read_text(encoding="utf-8")
EN = (ROOT / "static" / "i18n" / "en.js").read_text(encoding="utf-8")


def function_source(name, source):
    match = re.search(
        rf"^(?:async )?function {re.escape(name)}\(.*?^}}$",
        source,
        re.DOTALL | re.MULTILINE,
    )
    if not match:
        raise AssertionError(f"functie {name} niet gevonden")
    return match.group(0)


def const_source(name, source):
    match = re.search(rf"^const {re.escape(name)} = .*?;$", source, re.MULTILINE)
    if not match:
        raise AssertionError(f"constante {name} niet gevonden")
    return match.group(0)


ORDER_FUNCTIONS = (
    "orderLines",
    "orderLine",
    "orderLineSaved",
    "orderLinePaid",
    "orderFilter",
    "orderSortLines",
    "orderGroups",
    "orderViewFor",
    "orderScope",
    "orderViewLoad",
    "orderCategorySection",
    "orderCategoryLine",
    "orderTable",
    "orderTableRow",
    "orderLineBadges",
    "orderGroupSummary",
    "orderLineName",
    "orderLineLink",
)
ORDER_CONSTS = (
    "ORDER_VIEWS",
    "ORDER_SORT_KEYS",
    "ORDER_SCOPE_VIEWS",
    "ORDER_VIEW_DEFAULTS",
    "orderView",
    "orderPage",
)


def prelude():
    return "\n".join(
        [
            "const t = (key, params = {}) => key + (Object.keys(params).length ? ' ' + JSON.stringify(params) : '');",
            "const tn = (key, count, params = {}) => t(key, { count, ...params });",
            "const escapeHtml = (v) => String(v).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\"/g, '&quot;');",
            "const money = (v) => (v === null || v === undefined ? '' : 'EUR ' + Number(v).toFixed(2));",
            "const storeImage = (url) => url;",
            "const priceHistoryPath = (store, title, id = '') => `/offers/history?store=${store}&q=${title}&product=${id}`;",
            "const I18N = { formatNumber: (n) => String(n) };",
            "const mdiIcon = () => '';",
            "const badge = (kind, text, title = '') => `<span class=\"badge badge--${kind}\">${escapeHtml(text)}</span>`;",
            "const offerBadge = (offer) => (offer.badge && typeof offer.badge.percent === 'number' ? `<span class=\"offer-badge\">-${offer.badge.percent}%</span>` : '');",
            const_source("PURCHASE_SIZES", PURCHASE_VIEWS),
            const_source("CATEGORY_ORDER", CATEGORIES),
            function_source("categoryLabel", CATEGORIES),
            function_source("viewState", PURCHASE_VIEWS),
            function_source("foldText", ORDERS),
            function_source("matchesQuery", ORDERS),
            # De knop Koppelen (#238) bij een regel van een bon of ingelezen bestelling: hier alleen een merkteken.
            function_source("receiptLinkLine", ORDERS),
            function_source("importedLinkLine", ORDERS),
            "const productLinkButton = (line) => `<button data-product-link data-name=\"${escapeHtml(line.name)}\"></button>`;",
        ]
        + [function_source(name, ORDER_VIEWS) for name in ORDER_FUNCTIONS]
        + [const_source(name, ORDER_VIEWS) for name in ORDER_CONSTS]
    )



@unittest.skipUnless(shutil.which("node"), "Node is niet geïnstalleerd")
class OrderViewsNodeTest(unittest.TestCase):
    def run_js(self, scenario):
        self.assertTrue(ORDER_VIEWS, "static/order_views.js bestaat niet")
        script = f"{prelude()}\nconst line = (category, quantity, amount, savedEach = 0, extra = {{}}) => ({{ category, quantity, amount, saved: Math.round(savedEach * quantity * 100) / 100, title: category, name: '', ...extra }});\n{scenario}"
        result = subprocess.run(
            ["node", "-e", script],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def run_json(self, expression):
        return self.run_js(f"console.log(JSON.stringify({expression}));")

    def test_lines_are_grouped_per_category_in_the_offer_order_with_subtotals(self):
        groups = self.run_json(
            'orderGroups([line("vis", 2, 3.0, 1.0), line("groente_fruit", 1, 0.85), line("vis", 1, 2.0)])'
        )
        self.assertEqual([g["id"] for g in groups], ["groente_fruit", "vis"])
        self.assertEqual(
            (
                groups[1]["units"],
                groups[1]["kinds"],
                groups[1]["amount"],
                groups[1]["saved"],
            ),
            (3, 2, 5.0, 2.0),
        )
        self.assertEqual(len(groups[1]["lines"]), 2)

    def test_receipt_subtotals_are_after_the_bonus_so_they_add_up_to_the_paid_total(self):
        # Een bon noemt de regelprijs vóór de bonus: het subtotaal per categorie trekt de bonus van de regel eraf. Een bestelling
        # noemt al het betaalde bedrag (bonus is daar alleen een vlag).
        groups = self.run_json(
            "orderGroups(orderLines({ items: ["
            "{ name: 'ROOMKAAS', category: 'kaas_vleeswaren', quantity: 4, price: 0.99, amount: 3.96, bonus: 3.96, paidEach: 0 },"
            "{ name: 'KAAS', category: 'kaas_vleeswaren', quantity: 1, price: 2.0, amount: 2.0, bonus: 0.5, paidEach: 1.5 },"
            "{ name: 'MELK', category: 'zuivel_eieren', quantity: 3, price: 1.0, amount: 3.0, bonus: 1.0, paidEach: 0.67 },"
            "{ name: 'BROOD', category: 'brood_bakkerij', quantity: 1, price: null, amount: null, bonus: 0.2, paidEach: 1.8 },"
            "] }, 'receipt', 'ah'))"
        )
        self.assertEqual({g["id"]: g["amount"] for g in groups}, {"kaas_vleeswaren": 1.5, "zuivel_eieren": 2.0, "brood_bakkerij": 1.8})
        order = self.run_json(
            "orderGroups(orderLines({ items: [{ title: 'AH Kaas', category: 'kaas_vleeswaren', quantity: 2, price: 1.5, amount: 3.0, bonus: true, paidEach: 1.5 }] }, 'order', 'ah'))"
        )
        self.assertEqual(order[0]["amount"], 3.0)

    def test_an_unknown_category_lands_in_overig_and_empty_categories_are_left_out(
        self,
    ):
        groups = self.run_json(
            'orderGroups([line("onzin", 1, 1.0), line(null, 1, 2.0), line("overig", 1, 3.0)])'
        )
        self.assertEqual([g["id"] for g in groups], ["overig"])
        self.assertEqual(groups[0]["kinds"], 3)

    def test_filtering_matches_the_webshop_title_and_the_receipt_name(self):
        lines = 'const lines = [line("snacks_snoep", 1, 1.5, 0, { name: "CHEETOS CHIP", title: "Cheetos Chipito kaas" }), line("snacks_snoep", 1, 1.0, 0, { name: "", title: "Chéetos Crunchy" }), line("zuivel_eieren", 1, 1.2, 0, { name: "HALFVOLLE M", title: "AH Halfvolle melk" }), line("groente_fruit", 1, 2.5, 0, { name: "AH BOERENK", title: "Stamppotpakket" })];\n'
        self.assertEqual(
            self.run_js(
                lines
                + 'console.log(JSON.stringify(orderFilter(lines, ["cheet"], "all").map((l) => l.title)));'
            ),
            ["Cheetos Chipito kaas", "Chéetos Crunchy"],
        )
        self.assertEqual(
            self.run_js(
                lines
                + 'console.log(JSON.stringify(orderFilter(lines, ["halfvolle", "m"], "all").length));'
            ),
            1,
            "alle woorden moeten passen, ook in de bonnaam",
        )
        self.assertEqual(
            self.run_js(
                lines
                + 'console.log(JSON.stringify(orderFilter(lines, ["boerenk"], "all").length));'
            ),
            1,
            "de bonnaam telt ook",
        )
        self.assertEqual(
            self.run_js(
                lines
                + 'console.log(JSON.stringify(orderFilter(lines, [], "all").length));'
            ),
            4,
            "zonder zoekwoorden alles",
        )
        self.assertEqual(
            self.run_js(
                lines
                + 'console.log(JSON.stringify(orderFilter(lines, [], "zuivel_eieren").map((l) => l.title)));'
            ),
            ["AH Halfvolle melk"],
        )
        self.assertEqual(
            self.run_js(
                lines
                + 'console.log(JSON.stringify(orderFilter(lines, ["cheet"], "zuivel_eieren").length));'
            ),
            0,
            "zoeken en de chip werken samen",
        )

    def test_sorting_by_discount_puts_lines_without_a_discount_last_in_both_directions(
        self,
    ):
        lines = 'const lines = [line("vis", 1, 1, 0, { id: 0, discount: null }), line("vis", 1, 1, 0, { id: 1, discount: 20 }), line("vis", 1, 1, 0, { id: 2, discount: 5 }), line("vis", 1, 1, 0, { id: 3 })];\n'
        desc = self.run_js(
            lines
            + 'console.log(JSON.stringify(orderSortLines(lines, { key: "discount", dir: "desc" }).map((l) => l.id)));'
        )
        asc = self.run_js(
            lines
            + 'console.log(JSON.stringify(orderSortLines(lines, { key: "discount", dir: "asc" }).map((l) => l.id)));'
        )
        self.assertEqual(desc, [1, 2, 0, 3])
        self.assertEqual(
            asc, [2, 1, 0, 3], "zonder korting onderaan, op hun plaats in de bestelling"
        )

    def test_sorting_by_category_follows_the_offer_order_then_the_line_order(self):
        lines = 'const lines = [line("vis", 1, 1, 0, { id: 0 }), line("groente_fruit", 1, 1, 0, { id: 1 }), line("vis", 1, 1, 0, { id: 2 }), line("bier_wijn", 1, 1, 0, { id: 3 })];\n'
        self.assertEqual(
            self.run_js(
                lines
                + 'console.log(JSON.stringify(orderSortLines(lines, { key: "category", dir: "asc" }).map((l) => l.id)));'
            ),
            [1, 0, 2, 3],
        )
        self.assertEqual(
            self.run_js(
                lines
                + 'console.log(JSON.stringify(orderSortLines(lines, { key: "category", dir: "desc" }).map((l) => l.id)));'
            ),
            [3, 0, 2, 1],
            "binnen een categorie blijft de volgorde van de bestelling",
        )

    def test_sorting_by_name_and_amount_and_the_original_stays(self):
        lines = 'const lines = [line("vis", 1, 3.5, 0, { id: 0, title: "Zalm" }), line("vis", 1, null, 0, { id: 1, title: "appel" }), line("vis", 1, 1.25, 0, { id: 2, title: "Brood" })];\nconst before = JSON.stringify(lines);\n'
        self.assertEqual(
            self.run_js(
                lines
                + 'console.log(JSON.stringify(orderSortLines(lines, { key: "name", dir: "asc" }).map((l) => l.id)));'
            ),
            [1, 2, 0],
        )
        self.assertEqual(
            self.run_js(
                lines
                + 'console.log(JSON.stringify(orderSortLines(lines, { key: "amount", dir: "desc" }).map((l) => l.id)));'
            ),
            [0, 2, 1],
        )
        self.assertEqual(
            self.run_js(
                lines
                + 'console.log(JSON.stringify(orderSortLines(lines, { key: "amount", dir: "asc" }).map((l) => l.id)));'
            ),
            [2, 0, 1],
        )
        self.assertTrue(
            self.run_js(
                lines
                + 'orderSortLines(lines, { key: "name", dir: "desc" });\nconsole.log(JSON.stringify(JSON.stringify(lines) === before));'
            )
        )

    def test_a_view_that_does_not_fit_the_kind_falls_back(self):
        def view_for(stored, scope):
            return self.run_js(
                f'const store = {{ "ms-orders-view": {json.dumps(stored)} }};\nglobalThis.localStorage = {{ getItem: (k) => (k in store ? store[k] : null) }};\norderViewLoad();\nconsole.log(JSON.stringify(orderViewFor({json.dumps(scope)})));'
            )

        self.assertEqual(view_for('{"order":"paper"}', "order"), "category")
        self.assertEqual(view_for('{"receipt":"paper"}', "receipt"), "paper")
        self.assertEqual(
            view_for('{"order":"table","receipt":"category"}', "order"), "table"
        )
        self.assertEqual(
            view_for('{"order":"table","receipt":"category"}', "receipt"), "category"
        )
        self.assertEqual(
            view_for("cards", "order"),
            "category",
            "een oude waarde zonder JSON valt terug",
        )
        self.assertEqual(view_for("{}", "receipt"), "paper", "een bon begint als bon")

    def test_the_view_is_stored_per_scope_and_sort_and_options_once(self):
        stored = self.run_js(
            'const store = {};\nglobalThis.localStorage = { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = v; } };\norderView.useScope("receipt");\norderView.view = "table";\norderView.apply();\norderView.useScope("order");\nconst order = orderView.view;\norderView.setView("order", "cards");\norderView.sortBy("discount", "desc");\nconsole.log(JSON.stringify({ store, order, view: orderView.view, receipt: orderView.viewFor("receipt") }));'
        )
        self.assertEqual(
            json.loads(stored["store"]["ms-orders-view"]),
            {"order": "cards", "receipt": "table"},
        )
        self.assertEqual(
            json.loads(stored["store"]["ms-orders-sort"]),
            {"key": "discount", "dir": "desc"},
        )
        self.assertEqual(stored["order"], "category")
        self.assertEqual(stored["view"], "cards")
        self.assertEqual(stored["receipt"], "table")

    def sort_scenario(self, body):
        """De sorteerkeuze via het echte menu (bindViewBar met de markup van orderBarSpec) en de tabelkop (orderSortBy)."""
        sources = "\n".join(
            [
                "const MDI_VIEW_CATEGORY = '', MDI_VIEW_CARDS = '', MDI_VIEW_TABLE = '', MDI_RECEIPT = '', MDI_CHEVRON_DOWN = '';",
                "const $ = () => null;",
                "const setSubtabMenu = () => {};",
                "const handlers = {};",
                "globalThis.document = { querySelector: () => ({ addEventListener: (type, fn) => { handlers[type] = fn; } }) };",
                "globalThis.localStorage = { getItem: () => null, setItem() {} };",
                function_source("viewBarElement", PURCHASE_VIEWS),
                function_source("bindViewBar", PURCHASE_VIEWS),
                function_source("viewSortMenu", PURCHASE_VIEWS),
                function_source("viewSortStart", PURCHASE_VIEWS),
                function_source("orderBarSpec", ORDER_VIEWS),
                function_source("orderSortBy", ORDER_VIEWS),
                "bindViewBar('order', orderView, () => {});",
                # Een klik op een sleutel in het menu: het element met de data-attributen zoals viewSortMenu ze schrijft.
                "const menu = viewSortMenu('order', orderBarSpec('order').sorts);",
                "const choose = (key) => {",
                "  const tag = menu.match(new RegExp(`<button[^>]*data-sort-key=\"${key}\"[^>]*>`))[0];",
                "  const dataset = Object.fromEntries([...tag.matchAll(/data-sort-([a-z]+)=\"([^\"]*)\"/g)].map(([, name, value]) => [name === 'key' ? 'sortKey' : 'sort' + name[0].toUpperCase() + name.slice(1), value]));",
                "  handlers.click({ target: { closest: (selector) => (selector.includes('data-sort-key') ? { dataset } : null) } });",
                "  return `${orderView.sort.key} ${orderView.sort.dir}`;",
                "};",
                body,
            ]
        )
        return self.run_js(sources)

    def test_a_new_sort_key_from_the_menu_starts_in_its_own_direction(self):
        # Na Korting aflopend opent Categorie oplopend (anders stond Overig bovenaan), Bedrag aflopend; dezelfde sleutel wisselt.
        steps = self.sort_scenario(
            "orderView.sort = { key: 'discount', dir: 'desc' };\nconsole.log(JSON.stringify([choose('category'), choose('amount'), choose('amount'), choose('name'), choose('discount')]));"
        )
        self.assertEqual(steps, ["category asc", "amount desc", "amount asc", "name asc", "discount desc"])

    def test_a_table_header_starts_a_new_key_in_its_own_direction_too(self):
        steps = self.sort_scenario(
            "orderPage.kind = 'order';\norderView.sort = { key: 'amount', dir: 'desc' };\nconst out = [];\nfor (const key of ['category', 'category', 'discount']) { orderSortBy(key); out.push(`${orderView.sort.key} ${orderView.sort.dir}`); }\nconsole.log(JSON.stringify(out));"
        )
        self.assertEqual(steps, ["category asc", "category desc", "discount desc"])

    def test_a_paper_view_is_not_stored_for_an_order(self):
        stored = self.run_js(
            'const store = {};\nglobalThis.localStorage = { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = v; } };\norderView.useScope("order");\norderView.view = "paper";\norderView.save();\nconsole.log(JSON.stringify(JSON.parse(store["ms-orders-view"])));'
        )
        self.assertEqual(stored, {"order": "category", "receipt": "paper"})

    def test_lines_come_out_of_every_kind_of_purchase_in_one_shape(self):
        order = {
            "items": [
                {
                    "productId": 155600,
                    "title": "AH Aardappelschijfjes",
                    "brand": "AH",
                    "unitSize": "600 g",
                    "quantity": 2,
                    "amount": 2.38,
                    "price": 1.19,
                    "bonus": False,
                    "image": "https://x/a.webp",
                    "regularPrice": 1.49,
                    "category": "groente_fruit",
                    "discountSource": "history",
                    "discount": 20,
                    "timesBefore": 3,
                    "paidEach": 1.19,
                }
            ]
        }
        receipt = {
            "items": [
                {
                    "productId": 740002,
                    "name": "RED VELVET",
                    "quantity": 1,
                    "price": None,
                    "amount": 7.99,
                    "product": {
                        "id": 485213,
                        "title": "AH Red velvet taart",
                        "image": "https://x/r.webp",
                    },
                    "regularPrice": 7.99,
                    "bonus": 2.8,
                    "category": "brood_bakkerij",
                    "discountSource": "store",
                    "discount": 35,
                    "timesBefore": 1,
                    "paidEach": 5.19,
                },
                {
                    "productId": 1,
                    "name": "COTTAGE CH",
                    "quantity": 2,
                    "price": 1.1,
                    "amount": 2.2,
                    "product": None,
                    "category": "zuivel_eieren",
                    "timesBefore": 0,
                    "paidEach": 1.1,
                },
            ]
        }
        imported = {
            "items": [
                {
                    "title": "Jumbo Kaas",
                    "quantity": 1,
                    "paid": 4.0,
                    "price": 5.0,
                    "amount": 4.0,
                    "bonus": True,
                    "productId": "J1",
                    "image": None,
                    "brand": "Jumbo",
                    "unitSize": "1 kg",
                    "category": "kaas_vleeswaren",
                    "regularPrice": 5.0,
                    "discount": 20,
                    "discountSource": "store",
                    "timesBefore": 0,
                    "paidEach": 4.0,
                }
            ]
        }
        result = self.run_js(
            f"console.log(JSON.stringify([orderLines({json.dumps(order)}, 'order', 'ah'), orderLines({json.dumps(receipt)}, 'receipt', 'ah'), orderLines({json.dumps(imported)}, 'imported', 'jumbo')]));"
        )
        (ah,), (cake, cottage), (cheese,) = result
        self.assertEqual(
            {
                k: ah[k]
                for k in (
                    "id",
                    "title",
                    "name",
                    "brand",
                    "unitSize",
                    "image",
                    "quantity",
                    "price",
                    "paidEach",
                    "amount",
                    "category",
                    "discount",
                    "discountSource",
                    "timesBefore",
                    "productId",
                )
            },
            {
                "id": 0,
                "title": "AH Aardappelschijfjes",
                "name": "",
                "brand": "AH",
                "unitSize": "600 g",
                "image": "https://x/a.webp",
                "quantity": 2,
                "price": 1.19,
                "paidEach": 1.19,
                "amount": 2.38,
                "category": "groente_fruit",
                "discount": 20,
                "discountSource": "history",
                "timesBefore": 3,
                "productId": 155600,
            },
        )
        self.assertEqual(ah["saved"], 0.6)
        self.assertEqual(
            ah["href"],
            "/offers/history?store=ah&q=AH Aardappelschijfjes&product=155600",
        )
        self.assertEqual(
            (cake["title"], cake["name"], cake["image"], cake["productId"], cake["id"]),
            ("AH Red velvet taart", "RED VELVET", "https://x/r.webp", 485213, 0),
        )
        self.assertEqual(
            cake["paidEach"],
            5.19,
            "wat de server rekent",
        )
        self.assertEqual(
            cake["price"], 7.99, "zonder prijs op de bon: het bedrag per stuk"
        )
        self.assertEqual(cake["saved"], 2.8, "de prijs op de bon min wat je betaalde")
        self.assertEqual(cake["bonus"], 2.8)
        self.assertEqual(
            (cottage["title"], cottage["href"], cottage["image"], cottage["id"]),
            ("COTTAGE CH", None, None, 1),
            "zonder koppeling geen link en geen foto",
        )
        self.assertEqual(cottage["saved"], 0)
        self.assertEqual(
            (
                cheese["price"],
                cheese["paidEach"],
                cheese["saved"],
                cheese["discountSource"],
            ),
            (4.0, 4.0, 1.0, "store"),
        )
        self.assertTrue(cheese["href"].startswith("/offers/history?store=jumbo"))

    def test_a_missing_category_becomes_overig(self):
        result = self.run_json(
            "orderLines({ items: [{ title: 'x', quantity: 1, amount: 1 }] }, 'order', 'ah')"
        )
        self.assertEqual(result[0]["category"], "overig")

    def test_the_category_section_has_a_coloured_head_with_the_counts_and_the_subtotal(
        self,
    ):
        html = self.run_js(
            'const group = orderGroups([line("vis", 2, 3.0, 1.0, { title: "<Zalm>", href: "/x", image: "https://x/z.webp", timesBefore: 2, discount: 20, discountSource: "history" }), line("vis", 1, 2.0)])[0];\nconsole.log(JSON.stringify(orderCategorySection(group, ["zalm"], "ah")));'
        )
        for needle in (
            'class="cat-section cat--vis"',
            'class="cat-section__head"',
            "offers.cat.vis",
            "orders.group.units",
            "EUR 5.00",
            "EUR 2.00",
            'class="cat-line',
            "&lt;Zalm&gt;",
            'href="/x"',
            'loading="lazy"',
            "badge--before",
            "-20%",
        ):
            self.assertIn(needle, html, needle)
        self.assertNotIn("<Zalm>", html)

    def test_the_table_has_sortable_headers_and_marks_the_active_one(self):
        html = self.run_js(
            'console.log(JSON.stringify(orderTable([line("vis", 2, 3.0, 0, { title: "Zalm & co", unitSize: "250 g", price: 1.5, discount: 10, timesBefore: 1 })], { key: "discount", dir: "asc" })));'
        )
        for key in ("category", "name", "amount", "discount"):
            self.assertIn(f'data-sort="{key}"', html)
        self.assertIn('class="purchase-table order-table"', html)
        self.assertIn('aria-sort="ascending"', html)
        self.assertEqual(html.count("aria-sort="), 1)
        for needle in (
            "orders.col.category",
            "orders.col.unit",
            "orders.col.price",
            "orders.col.discount",
            "orders.col.before",
            "Zalm &amp; co",
            "250 g",
            "offer-chip__dot",
            "cat--vis",
        ):
            self.assertIn(needle, html, needle)


@unittest.skipUnless(shutil.which("node"), "Node is niet geïnstalleerd")
class OfferBadgeForALineTest(unittest.TestCase):
    def run_js(self, scenario):
        script = "\n".join(
            [
                "const t = (key, params = {}) => key + ' ' + JSON.stringify(params);",
                "const formatPrice = (v) => 'EUR ' + v;",
                "const escapeHtml = (v) => String(v).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\"/g, '&quot;');",
                function_source("offerBadgeTitle", OFFERS),
                function_source("offerBadge", OFFERS),
                scenario,
            ]
        )
        result = subprocess.run(
            ["node", "-e", script],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_a_purchase_line_without_a_verdict_gets_the_badge_and_a_title(self):
        history = self.run_js(
            'console.log(JSON.stringify(offerBadge({ badge: { percent: 25, source: "history" } })));'
        )
        self.assertIn("offer-badge--good", history, "zonder oordeel volgt de soort uit het getal (20% of meer)")
        self.assertIn("-25%", history)
        self.assertIn("offers.verdict.good", history)
        self.assertNotIn("offers.verdict.reference", history)
        small = self.run_js(
            'console.log(JSON.stringify(offerBadge({ badge: { percent: 5, source: "history" } })));'
        )
        self.assertIn("offers.verdict.small", small)
        self.assertIn("offer-badge--small", small)
        store = self.run_js(
            'console.log(JSON.stringify(offerBadge({ badge: { percent: 12, source: "store" } })));'
        )
        self.assertIn("offer-badge--store", store)
        self.assertIn("offers.badge.title_store", store)
        self.assertEqual(
            self.run_js(
                "console.log(JSON.stringify(offerBadge({ badge: { percent: null, source: null } })));"
            ),
            "",
        )


class OrderPageMarkupTest(unittest.TestCase):
    def test_the_category_order_in_the_browser_is_the_one_of_the_server(self):
        from mealieshopper import offer_categories

        match = re.search(r"^const CATEGORY_ORDER = (\[.*?\]);$", CATEGORIES, re.M)
        self.assertIsNotNone(match)
        ids = [category[0] if isinstance(category, (tuple, list)) else getattr(category, "id", category) for category in offer_categories.CATEGORIES]
        self.assertEqual(json.loads(match.group(1)), ids)

    def test_the_order_page_has_the_bar_the_search_field_and_the_chips(self):
        for needle in (
            'data-view-bar="order"',
            'id="order-search"',
            'id="order-categories"',
        ):
            self.assertIn(needle, HTML)
        self.assertNotIn("function purchaseItem(", ORDERS)
        self.assertIn("function productCard(", ORDERS)

    def test_the_bar_the_search_and_the_chips_sit_between_the_meta_line_and_the_items(
        self,
    ):
        detail = HTML[HTML.index('id="ah-order-detail"') :]
        order = [
            detail.index(needle)
            for needle in (
                'id="ah-order-badges"',
                'id="ah-order-meta"',
                'data-view-bar="order"',
                'id="order-search"',
                'id="order-categories"',
                'id="ah-order-items"',
            )
        ]
        self.assertEqual(order, sorted(order))
        self.assertNotIn(
            'id="receipt-view"',
            HTML,
            "de bon heeft geen eigen schakelaar meer: de balk heeft de weergave Bon",
        )
        self.assertIn(
            'data-i18n-attr="placeholder:orders.search_in"',
            detail[: detail.index('id="ah-order-items"')],
        )

    def test_order_views_loads_right_after_purchase_views(self):
        scripts = re.findall(r"filename='([\w/]+\.js)'", HTML)
        self.assertEqual(
            scripts[scripts.index("purchase_views.js") + 1], "order_views.js"
        )

    def test_every_kind_of_purchase_is_drawn_by_the_order_page(self):
        open_purchase = function_source("openPurchase", ORDERS)
        self.assertEqual(
            open_purchase.count("renderOrderPage("),
            3,
            "bestelling (AH en Jumbo), ingelezen bestelling en bon",
        )
        self.assertIn(
            'renderOrderPage(order, "order", ordersAreJumbo() ? "jumbo" : "ah")',
            open_purchase,
        )
        self.assertIn('renderOrderPage(order, "imported", store)', open_purchase)
        self.assertIn('renderOrderPage(receipt, "receipt", "ah")', open_purchase)
        self.assertIn(
            'renderOrderPage(receipt, "receipt", "ah")',
            function_source("resolveReceiptProducts", ORDERS),
        )
        self.assertNotIn("receiptState", ORDERS)
        self.assertNotIn("function receiptProducts(", ORDERS)
        self.assertIn(
            "receiptPaper(", ORDER_VIEWS, "de weergave Bon tekent het bonnetje"
        )

    def test_the_meta_line_no_longer_repeats_the_status_of_the_badge(self):
        open_purchase = function_source("openPurchase", ORDERS)
        self.assertNotIn("orders.kind.delivered", open_purchase)
        self.assertNotIn("importedOrderStatus", open_purchase)
        page = function_source("renderOrderPage", ORDER_VIEWS)
        self.assertIn("purchaseStatusBadge(", page)
        self.assertIn('badge("saved", text)', page)
        self.assertIn('t("orders.saved_percent", { amount, percent:', page)

    def test_the_product_card_carries_the_category_colour_and_the_badges(self):
        card = function_source("productCard", ORDERS)
        for needle in (
            "product-card cat--",
            "orderLineBadges(line)",
            "categoryLabel(",
            "escapeHtml(",
        ):
            self.assertIn(needle, card, needle)
        badges = function_source("orderLineBadges", ORDER_VIEWS)
        for needle in (
            'offerBadge({ badge: { percent: line.discount, source: line.discountSource } }, "offer-badge--inline")',
            'badge("before", t("orders.before"',
            'badge("bonus"',
        ):
            self.assertIn(needle, badges, needle)
        self.assertIn(
            "productCard(",
            function_source("renderBasket", ORDERS),
            "het mandje toont dezelfde kaart",
        )

    def test_the_css_colours_the_card_and_the_section_head_and_keeps_the_offer_badge_inline(
        self,
    ):
        self.assertRegex(
            CSS,
            r"\.product-card\[class\*=\"cat--\"\] \{\n  border-color: var\(--cat-line\);\n  background: var\(--cat-bg\);",
        )
        self.assertRegex(
            CSS, r"\.cat-section__head \{\n(?:.*\n)*?  background: var\(--cat-bg\);"
        )
        self.assertRegex(CSS, r"\.offer-badge\.offer-badge--inline \{\n  position: static;")
        for selector in (
            ".cat-section {",
            ".cat-line {",
            ".order-table {",
            ".order-table-wrap {",
        ):
            self.assertIn(selector, CSS)
        self.assertRegex(
            CSS,
            r"\.offer-badge \{\n  position: absolute;",
            "de aanbiedingen houden hun badge op het plaatje",
        )

    def test_the_line_badge_is_inline(self):
        self.assertIn("offer-badge--inline", ORDER_VIEWS + ORDERS)

    def test_the_inline_store_badge_keeps_its_outline(self):
        # .offer-badge.offer-badge--inline zet box-shadow op none. Sinds 4.8.0 zijn de randen van de badge een border (de
        # stippelrand van een claim van de winkel ook), dus in een regel blijft de rand staan zonder een eigen regel.
        self.assertRegex(CSS, r"\.offer-badge\.offer-badge--inline \{\n  position: static;[^}]*box-shadow: none;")
        self.assertIn(".offer-badge--store {\n  border: 2px dashed currentColor;", CSS)
        self.assertNotIn(".offer-badge.offer-badge--inline.offer-badge--store", CSS)

    def test_both_languages_have_the_texts(self):
        for key in (
            "orders.view.category",
            "orders.view.paper",
            "orders.sort.category",
            "orders.sort.name",
            "orders.sort.discount",
            "orders.search_in",
            "orders.group.units.one",
            "orders.group.units.other",
            "orders.group.kinds.one",
            "orders.group.kinds.other",
            "orders.group.summary",
            "orders.col.category",
            "orders.col.unit",
            "orders.col.price",
            "orders.col.discount",
            "orders.col.before",
            "orders.saved_percent",
            "orders.before",
        ):
            self.assertIn(f'"{key}":', NL, key)
            self.assertIn(f'"{key}":', EN, key)
        self.assertIn('"orders.saved_percent": "{amount} bespaard ({percent}%)"', NL)
        self.assertIn('"orders.before": "{count}x eerder"', NL)
        self.assertIn('"orders.view.category": "Per categorie"', NL)
        self.assertIn('"orders.view.category": "By category"', EN)


if __name__ == "__main__":
    unittest.main()
