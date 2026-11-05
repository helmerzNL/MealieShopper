"""Je eigen koppelingen (#238): een regel op een kassabon of in een ingelezen bestelling die de app niet (of verkeerd) herkende, koppel
je zelf aan een product van de winkel. Daarna hoort elke regel met hetzelfde artikelnummer of dezelfde naam bij dat product: op de
bon, in de ingelezen bestellingen en in de Prijsgeschiedenis. Een eigen koppeling gaat voor de automatische; ontkoppelen zet die
terug. De schermen draaien in Node (zonder Node slaan die tests over)."""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

os.environ.setdefault("MEALIESHOPPER_DATA_DIR", tempfile.mkdtemp())

from mealieshopper import ah_orders, auth, backup, image_cache, price_import, prices, product_links  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
NOW = "2026-10-20T08:00:00+00:00"
TODAY = "2026-10-20"
COTTAGE = {"productId": "4711", "title": "AH Cottage cheese", "brand": "AH", "unitSize": "200 g", "image": "https://static.ah.nl/cottage.jpg"}
BLOEMKOOL = {"productId": "4183", "title": "AH Bloemkool", "brand": "AH", "unitSize": "per stuk", "image": "https://static.ah.nl/bloemkool.jpg"}


class LinksCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"MEALIESHOPPER_DATA_DIR": tmp.name, "PASSKEY_AUTH_ENABLED": "false"})
        patcher.start()
        self.addCleanup(patcher.stop)
        auth.init_db()

    def ah_receipt(self, receipt_id, when, *lines):
        with closing(ah_orders._connect()) as conn, conn:
            conn.execute("INSERT INTO ah_receipts (receipt_id, date_time, total, details_synced, first_seen) VALUES (?, ?, 0, 1, ?)",
                         (receipt_id, when, when))
            conn.executemany(
                "INSERT INTO ah_receipt_items (receipt_id, position, product_id, name, quantity, price, amount) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(receipt_id, position, *line) for position, line in enumerate(lines)],
            )

    def auto_link(self, article, product_id, title, image=None):
        with closing(ah_orders._connect()) as conn, conn:
            conn.execute("INSERT INTO ah_receipt_products (receipt_product_id, product_id, title, checked_at, image) VALUES (?, ?, ?, 'x', ?)",
                         (article, product_id, title, image))

    def ah_order(self, order_id, day, *items):
        with closing(ah_orders._connect()) as conn, conn:
            ah_orders._save_order(conn, {"id": order_id, "deliveryDate": day}, "delivered", list(items))

    def mail(self, store, *names, number="225301", day="2025-03-14"):
        items = [{"name": name, "quantity": 1, "price": 1.5, "total": 1.5, "discount": 0} for name in names]
        price_import.run(json.dumps({"store": store, "orders": [{"orderNumber": number, "date": day, "total": 1.5 * len(names), "items": items}]}),
                         store, now=NOW)

    def history(self, query="", store="ah"):
        return {p["productId"]: p for p in prices.history(store, query, 50, today=TODAY)["products"]}


class SaveTest(LinksCase):
    def test_a_line_with_an_article_number_on_an_ah_receipt_is_linked_by_that_number(self):
        link = product_links.save("ah", "COTTAGE CH", COTTAGE, article=20960, now=NOW)
        self.assertEqual((link["kind"], link["key"], link["productId"], link["title"], link["name"]),
                         ("article", "20960", "4711", "AH Cottage cheese", "COTTAGE CH"))
        self.assertEqual((link["brand"], link["unitSize"], link["image"], link["createdAt"]), ("AH", "200 g", COTTAGE["image"], NOW))

    def test_without_an_article_number_or_at_another_store_the_name_is_the_key(self):
        for store, article in (("ah", None), ("ah", 0), ("ah", "geen"), ("jumbo", 20960)):
            link = product_links.save(store, "  Jumbo  Kipfilet  500g ", COTTAGE, article=article)
            self.assertEqual((link["kind"], link["key"]), ("name", "jumbo kipfilet 500g"), (store, article))

    def test_linking_the_same_line_again_replaces_the_link(self):
        product_links.save("ah", "COTTAGE CH", BLOEMKOOL, article=20960)
        product_links.save("ah", "COTTAGE CH", COTTAGE, article=20960)
        links = product_links.list_links("ah")["links"]
        self.assertEqual([(l["key"], l["productId"]) for l in links], [("20960", "4711")])

    def test_bad_input_is_refused_with_a_readable_message(self):
        with self.assertRaises(ValueError):
            product_links.save("lidl", "X", COTTAGE)
        with self.assertRaises(ValueError):
            product_links.save("ah", "X", {"title": "Zonder id"})
        with self.assertRaises(ValueError):
            product_links.save("ah", "X", {"productId": "1"})
        with self.assertRaises(ValueError):
            product_links.save("ah", "   ", COTTAGE)
        with self.assertRaises(ValueError):
            product_links.remove("ah", "iets", "x")

    def test_only_a_plain_https_photo_is_kept(self):
        for image in ("javascript:alert(1)", "http://static.ah.nl/x.jpg", "https://static.ah.nl/a b.jpg", "data:image/png;base64,AA"):
            self.assertIsNone(product_links.save("ah", "X", {**COTTAGE, "image": image})["image"], image)

    def test_remove_and_list(self):
        product_links.save("ah", "COTTAGE CH", COTTAGE, article=20960, now="2026-10-01T00:00:00+00:00")
        product_links.save("jumbo", "Jumbo Kipfilet", COTTAGE, now="2026-10-02T00:00:00+00:00")
        self.assertEqual([l["store"] for l in product_links.list_links()["links"]], ["jumbo", "ah"], "de nieuwste eerst")
        self.assertTrue(product_links.remove("ah", "article", "20960"))
        self.assertFalse(product_links.remove("ah", "article", "20960"))
        self.assertEqual([l["store"] for l in product_links.list_links()["links"]], ["jumbo"])

    def test_the_fold_is_the_same_as_the_name_matching_of_the_price_history(self):
        for name in ("AH Ah zachte spinazie", "Crème  fraîche", "COTTAGE CH"):
            self.assertEqual(product_links.fold(name), prices._fold_name(name))
        self.assertEqual(product_links.fold("AH Ah zachte spinazie"), "ah zachte spinazie")
        self.assertEqual(product_links.fold("Crème  fraîche"), "creme fraiche")

    def test_links_go_into_the_backup(self):
        self.assertIn("product_links", backup.DATA_TABLES)
        self.assertNotIn("product_links", backup.EXCLUDED_TABLES)
        self.assertIn(("product_links", "image"), image_cache.KNOWN_IMAGE_COLUMNS)


class ReceiptTest(LinksCase):
    def test_an_unknown_receipt_line_shows_the_product_you_chose_and_so_does_every_other_receipt(self):
        self.ah_receipt("R1", "2026-10-02T09:00:00", (20960, "COTTAGE CH", 1, 1.29, 1.29), (0, "STATIEGELD", 1, 0.15, 0.15))
        self.ah_receipt("R2", "2026-10-09T09:00:00", (20960, "COTTAGE CH", 2, 1.29, 2.58))
        before = ah_orders.get_receipt("R1")["items"]
        self.assertEqual([(line["product"], line["link"]) for line in before], [(None, None), (None, None)])
        product_links.save("ah", "COTTAGE CH", COTTAGE, article=20960)
        for receipt_id in ("R1", "R2"):
            line = ah_orders.get_receipt(receipt_id)["items"][0]
            self.assertEqual(line["product"], {"id": "4711", "title": "AH Cottage cheese", "image": COTTAGE["image"]}, receipt_id)
            self.assertEqual(line["link"], {"kind": "article", "key": "20960"})
            self.assertEqual(line["name"], "COTTAGE CH", "de naam op de bon blijft")

    def test_a_line_without_an_article_number_is_linked_by_its_name(self):
        self.ah_receipt("R1", "2026-10-02T09:00:00", (0, "COTTAGE CH", 1, 1.29, 1.29))
        self.ah_receipt("R2", "2026-10-09T09:00:00", (None, "Cottage  ch", 1, 1.29, 1.29))
        product_links.save("ah", "COTTAGE CH", COTTAGE, article=0)
        for receipt_id in ("R1", "R2"):
            line = ah_orders.get_receipt(receipt_id)["items"][0]
            self.assertEqual((line["product"]["id"], line["link"]), ("4711", {"kind": "name", "key": "cottage ch"}), receipt_id)

    def test_your_own_link_wins_from_the_automatic_one_and_unlinking_brings_that_back(self):
        self.ah_receipt("R1", "2026-10-02T09:00:00", (20960, "AH BLOEMK", 1, 1.99, 1.99))
        self.auto_link(20960, 999, "AH Broccoli", "https://static.ah.nl/broccoli.jpg")  # fout gekoppeld
        self.assertEqual(ah_orders.get_receipt("R1")["items"][0]["product"]["title"], "AH Broccoli")
        product_links.save("ah", "AH BLOEMK", BLOEMKOOL, article=20960)
        line = ah_orders.get_receipt("R1")["items"][0]
        self.assertEqual((line["product"]["id"], line["product"]["title"], line["link"]["kind"]), ("4183", "AH Bloemkool", "article"))
        product_links.remove("ah", "article", "20960")
        line = ah_orders.get_receipt("R1")["items"][0]
        self.assertEqual((line["product"]["id"], line["link"]), (999, None))

    def test_the_article_link_goes_before_a_name_link(self):
        self.ah_receipt("R1", "2026-10-02T09:00:00", (20960, "COTTAGE CH", 1, 1.29, 1.29))
        product_links.save("ah", "COTTAGE CH", BLOEMKOOL)  # op naam
        product_links.save("ah", "COTTAGE CH", COTTAGE, article=20960)  # op artikelnummer
        self.assertEqual(ah_orders.get_receipt("R1")["items"][0]["product"]["id"], "4711")

    def test_the_receipts_list_shows_the_photo_and_name_of_your_own_links(self):
        self.ah_receipt("R1", "2026-10-02T09:00:00", (20960, "COTTAGE CH", 1, 1.29, 1.29), (0, "AH BLOEMK", 1, 1.99, 1.99),
                        (777, "RB PICOLIENT", 1, 2.0, 2.0))
        self.auto_link(777, 88624, "Red Band Picolient", "https://static.ah.nl/picolient.jpg")
        product_links.save("ah", "COTTAGE CH", COTTAGE, article=20960)
        product_links.save("ah", "AH BLOEMK", BLOEMKOOL)
        receipt = ah_orders.list_receipts(summary=True)["receipts"][0]
        self.assertEqual(receipt["images"], [COTTAGE["image"], BLOEMKOOL["image"], "https://static.ah.nl/picolient.jpg"])
        self.assertEqual(receipt["productNames"][:3], ["AH Cottage cheese", "AH Bloemkool", "Red Band Picolient"])

    def test_a_linked_article_number_is_no_longer_looked_up_at_ah(self):
        self.ah_receipt("R1", "2026-10-02T09:00:00", (20960, "COTTAGE CH", 1, 1.29, 1.29), (30001, "RB PICOLIENT", 1, 2.0, 2.0))
        with closing(ah_orders._connect()) as conn:
            self.assertEqual(sorted(group["name"] for group in ah_orders._todo_by_name(conn, NOW)), ["COTTAGE CH", "RB PICOLIENT"])
        product_links.save("ah", "COTTAGE CH", COTTAGE, article=20960)
        with closing(ah_orders._connect()) as conn:
            self.assertEqual([group["name"] for group in ah_orders._todo_by_name(conn, NOW)], ["RB PICOLIENT"])


class PriceHistoryTest(LinksCase):
    def test_the_purchases_of_a_linked_line_count_at_the_product_you_chose(self):
        self.ah_order(101, "2026-09-01", {"productId": 4711, "title": "AH Cottage cheese", "brand": "AH", "unitSize": "200 g", "quantity": 1,
                                          "amount": 1.25, "price": 1.25, "priceBeforeBonus": None, "bonus": False, "taxonomy": "", "image": ""})
        self.ah_receipt("R1", "2026-10-02T09:00:00", (0, "COTTAGE CH", 2, 1.29, 2.58))
        before = self.history()
        self.assertIn("bon:cottage ch", before, "zonder koppeling: een los product")
        self.assertEqual(before["4711"]["timesBought"], 1)
        product_links.save("ah", "COTTAGE CH", COTTAGE)
        after = self.history()
        self.assertNotIn("bon:cottage ch", after)
        self.assertEqual((after["4711"]["timesBought"], [p["source"] for p in after["4711"]["purchases"]]), (2, ["order", "receipt"]))
        self.assertIn("4711", self.history("cottage ch"), "de naam op de bon vindt het product nog")
        self.assertIn("4711", {p for p in prices.bought_ids("ah", TODAY)})

    def test_an_article_link_wins_from_a_matching_id_and_name(self):
        # Zonder koppeling hoort artikelnummer 20960 bij zichzelf (een bekend id met dezelfde naam); met je koppeling bij 4183.
        self.ah_order(101, "2026-09-01", {"productId": 20960, "title": "AH Bloemk", "brand": "AH", "unitSize": "", "quantity": 1,
                                          "amount": 1.0, "price": 1.0, "priceBeforeBonus": None, "bonus": False, "taxonomy": "", "image": ""})
        self.ah_receipt("R1", "2026-10-02T09:00:00", (20960, "AH BLOEMK", 1, 1.99, 1.99))
        self.assertEqual(self.history()["20960"]["timesBought"], 2)
        product_links.save("ah", "AH BLOEMK", BLOEMKOOL, article=20960)
        found = self.history()
        self.assertEqual((found["20960"]["timesBought"], found["4183"]["timesBought"], found["4183"]["title"]), (1, 1, "AH Bloemkool"))

    def test_an_imported_line_is_linked_by_its_name_and_unlinking_brings_the_loose_product_back(self):
        self.mail("ah", "AH Hüttenkäse naturel")
        self.assertIn("import:ah huttenkase naturel", self.history())
        product_links.save("ah", "AH Hüttenkäse naturel", COTTAGE)
        found = self.history()
        self.assertNotIn("import:ah huttenkase naturel", found)
        self.assertEqual((found["4711"]["title"], found["4711"]["purchases"][0]["source"]), ("AH Cottage cheese", "import"))
        product_links.remove("ah", "name", "ah huttenkase naturel")
        self.assertIn("import:ah huttenkase naturel", self.history())


class ImportedOrderTest(LinksCase):
    def test_an_imported_order_shows_the_product_you_chose_at_every_store(self):
        for store, product in (("ah", COTTAGE), ("jumbo", {"productId": "123KIP", "title": "Jumbo Kipfilet 500g", "image": "https://jumbo.com/k.jpg"}),
                               ("picnic", {"productId": "s1001", "title": "Picnic kipfilet"}), ("crisp", {"productId": "77", "title": "Kipfilet"})):
            self.mail(store, "Kipfilet van de mail", "Iets anders")
            product_links.save(store, "Kipfilet van de mail", product)
            order = price_import.get_order(store, "225301")
            linked, other = order["items"]
            self.assertEqual((linked["productId"], linked["productTitle"], linked["link"]),
                             (product["productId"], product["title"], {"kind": "name", "key": "kipfilet van de mail"}), store)
            self.assertEqual(linked["image"], product.get("image"), store)
            self.assertEqual((other["productId"], other["link"]), (None, None), store)
            listed = price_import.list_orders(store, summary=True)["orders"][0]
            self.assertEqual(listed["images"], [product["image"]] if product.get("image") else [], store)

    def test_link_names_prefers_what_the_store_knows_about_the_product(self):
        prices.record("ah", [{"id": "4711", "title": "AH Cottage cheese naturel", "price": 1.29, "unitSize": "250 g",
                              "images": [{"url": "https://static.ah.nl/nieuw.jpg"}]}], now=NOW)
        product_links.save("ah", "Huttenkase", COTTAGE)
        found = prices.link_names("ah", ["Huttenkase"])["Huttenkase"]
        self.assertEqual((found["title"], found["unitSize"], found["image"], found["brand"]),
                         ("AH Cottage cheese naturel", "250 g", "https://static.ah.nl/nieuw.jpg", "AH"))


class ApiTest(LinksCase):
    def setUp(self):
        super().setUp()
        self.client = create_app().test_client()

    def test_link_list_and_unlink_through_the_api(self):
        self.ah_receipt("R1", "2026-10-02T09:00:00", (20960, "COTTAGE CH", 1, 1.29, 1.29))
        saved = self.client.put("/api/product-links", json={"store": "ah", "name": "COTTAGE CH", "article": 20960, "product": COTTAGE})
        self.assertEqual(saved.status_code, 200, saved.get_json())
        self.assertEqual(saved.get_json()["link"]["key"], "20960")
        self.assertEqual(self.client.get("/api/ah/receipts/R1").get_json()["items"][0]["product"]["id"], "4711")
        self.assertEqual(len(self.client.get("/api/product-links?store=ah").get_json()["links"]), 1)
        removed = self.client.delete("/api/product-links/ah/article/20960")
        self.assertEqual(removed.get_json(), {"removed": True})
        self.assertIsNone(self.client.get("/api/ah/receipts/R1").get_json()["items"][0]["product"])

    def test_a_name_with_a_slash_or_spaces_can_be_unlinked(self):
        self.client.put("/api/product-links", json={"store": "jumbo", "name": "Melk 1/2 liter", "product": COTTAGE})
        response = self.client.delete("/api/product-links/jumbo/name/" + "melk%201%2F2%20liter")
        self.assertEqual(response.get_json(), {"removed": True})

    def test_bad_input_gives_400(self):
        self.assertEqual(self.client.put("/api/product-links", json={"store": "lidl", "name": "X", "product": COTTAGE}).status_code, 400)
        self.assertEqual(self.client.put("/api/product-links", json={"store": "ah", "name": "X"}).status_code, 400)
        self.assertEqual(self.client.delete("/api/product-links/ah/iets/x").status_code, 400)
        self.assertEqual(self.client.get("/api/product-links/search?store=lidl&q=melk").status_code, 400)

    def test_search_gives_the_same_shape_for_every_store_and_records_the_prices(self):
        ah_result = [{"webshopId": "4711", "title": "AH Cottage cheese", "price": {"now": 1.29}, "images": [{"url": "https://static.ah.nl/c.jpg"}],
                      "brand": "AH", "unitSize": "200 g"}]
        picnic_result = [{"id": "s1", "title": "Hüttenkäse", "price": 1.49, "image": "https://picnic.app/h.png", "unitSize": "200 gram"},
                         {"id": "", "title": "zonder id"}]
        with mock.patch.object(prices, "_search_function", side_effect=lambda store: (lambda q, limit: ah_result if store == "ah" else picnic_result)):
            ah = self.client.get("/api/product-links/search?store=ah&q=cottage").get_json()
            picnic = self.client.get("/api/product-links/search?store=picnic&q=huttenkase").get_json()
        self.assertEqual(ah["products"], [{"productId": "4711", "title": "AH Cottage cheese", "brand": "AH", "unitSize": "200 g",
                                           "image": "https://static.ah.nl/c.jpg", "price": 1.29}])
        self.assertEqual(picnic["products"], [{"productId": "s1", "title": "Hüttenkäse", "brand": None, "unitSize": "200 gram",
                                               "image": "https://picnic.app/h.png", "price": 1.49}])
        with closing(prices._connect()) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM price_observations").fetchone()[0], 2, "zoals elke zoekopdracht")

    def test_a_store_that_fails_gives_its_message(self):
        def broken(store):
            def search(query, limit):
                raise RuntimeError("Crisp is niet gekoppeld")
            return search

        with mock.patch.object(prices, "_search_function", side_effect=broken):
            response = self.client.get("/api/product-links/search?store=crisp&q=melk")
        self.assertEqual((response.status_code, response.get_json()["error"]), (424, "Crisp is niet gekoppeld"))

    def test_an_empty_search_asks_the_store_nothing(self):
        with mock.patch.object(prices, "_search_function") as search:
            found = self.client.get("/api/product-links/search?store=ah&q=%20").get_json()
        self.assertEqual(found["products"], [])
        search.assert_not_called()


# --- de schermen, in Node ---------------------------------------------------------------------------------------------

SOURCES = {name: (ROOT / "static" / name).read_text(encoding="utf-8")
           for name in ("orders.js", "order_views.js", "product_links.js", "picnic.js", "crisp.js")}
HTML = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
NL = (ROOT / "static" / "i18n" / "nl.js").read_text(encoding="utf-8")
EN = (ROOT / "static" / "i18n" / "en.js").read_text(encoding="utf-8")


def function_source(file, name):
    match = re.search(rf"^(?:async )?function {re.escape(name)}\(.*?^}}$", SOURCES[file], re.S | re.M)
    if not match:
        raise AssertionError(f"functie {name} niet gevonden in {file}")
    return match.group(0)


PRELUDE = "\n".join([
    'const I18N = { locale: () => "nl-NL" };',
    "const t = (key, values = {}) => [key, ...Object.values(values)].join('|');",
    "const tn = (key, count) => `${count} ${key}`;",
    'const escapeHtml = (value) => String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");',
    'const mdiIcon = (path) => `<svg aria-hidden="true"><path d="${path}"/></svg>`;',
    'const MDI_LINK = "LINK";',
    'const MDI_IMAGE_OUTLINE = "FOTO";',
    "const matchesQuery = () => false;",
    "const priceHistoryPath = (store, title, id) => `/history/${store}/${id}`;",
    "const storeImage = (url) => url;",
    "const formatPrice = (value) => `€${value}`;",
    "const money = (value) => (value === null || value === undefined ? '' : formatPrice(value));",
    "const receiptDay = (receipt) => receipt.id;",
    "const receiptMethod = (method) => method;",
    # De weergaven van een aankoop (3.9): een regel van orderLine, als kaart, als regel in een categorie en als rij in de tabel.
    'const CATEGORY_ORDER = ["overig"];',
    "const categoryLabel = (id) => id;",
    "const badge = (kind, text) => `<span class=\"badge badge--${kind}\">${escapeHtml(text)}</span>`;",
    "const offerBadge = () => '';",
    *(function_source("product_links.js", name) for name in ("productLinkButton", "productLinkItem")),
    *(function_source("orders.js", name) for name in ("productCard", "receiptLinkLine", "receiptPaper", "importedLinkLine")),
    *(function_source("order_views.js", name) for name in ("orderLineSaved", "orderLine", "orderLinePaid", "orderLines", "orderLineBadges",
                                                             "orderLineName", "orderLineLink", "orderCategoryLine", "orderTableRow")),
    "const views = (purchase, kind, store) => { const lines = orderLines(purchase, kind, store); return {"
    " cards: lines.map(productCard).join(''), category: lines.map(orderCategoryLine).join(''), table: lines.map(orderTableRow).join('') }; };",
    function_source("picnic.js", "renderPicnicOrderItem"),
    function_source("crisp.js", "renderCrispOrderItem"),
])

RECEIPT = {
    "id": "R1", "discounts": [], "payments": [], "total": 3.28,
    "items": [
        {"productId": 20960, "name": "COTTAGE CH", "quantity": 1, "price": 1.29, "amount": 1.29, "product": None, "link": None},
        {"productId": 30001, "name": "AH BLOEMK", "quantity": 1, "price": 1.99, "amount": 1.99,
         "product": {"id": "4183", "title": "AH Bloemkool", "image": ""}, "link": {"kind": "article", "key": "30001"}},
    ],
}


@unittest.skipUnless(shutil.which("node"), "Node is niet geïnstalleerd")
class ScreensTest(unittest.TestCase):
    def run_js(self, scenario):
        script = f"{PRELUDE}\nconst receipt = {json.dumps(RECEIPT)};\n{scenario}"
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_the_button_carries_the_line_and_says_what_it_does(self):
        todo, change, nothing = self.run_js(
            "console.log(JSON.stringify([productLinkButton({ store: 'ah', name: 'COTTAGE \"CH\"', article: 20960 }),"
            " productLinkButton({ store: 'jumbo', name: 'Kip', product: 'Jumbo Kip', productId: 'k1', link: { kind: 'name', key: 'kip' } }),"
            " productLinkButton({ store: 'ah', name: '  ' })]));"
        )
        self.assertIn('class="icon-button product-link product-link--todo" data-product-link', todo)
        for needle in ('data-store="ah"', 'data-name="COTTAGE &quot;CH&quot;"', 'data-article="20960"', 'data-product=""', 'data-kind=""',
                       'aria-label="links.link_line|COTTAGE &quot;CH&quot;"', 'title="links.link"', "LINK"):
            self.assertIn(needle, todo, needle)
        self.assertIn('class="icon-button product-link" data-product-link', change)
        for needle in ('data-product="Jumbo Kip"', 'data-product-id="k1"', 'data-kind="name"', 'data-key="kip"', 'title="links.change"',
                       'aria-label="links.change_line|Kip"'):
            self.assertIn(needle, change, needle)
        self.assertEqual(nothing, "", "zonder naam valt er niets te koppelen")

    def test_on_paper_only_an_unlinked_line_has_the_button_and_in_the_other_views_every_line_has_one(self):
        paper, views = self.run_js("console.log(JSON.stringify([receiptPaper(receipt), views(receipt, 'receipt', 'ah')]));")
        self.assertEqual(paper.count("data-product-link"), 1)
        self.assertIn('COTTAGE CH<button type="button" class="icon-button product-link product-link--todo"', paper)
        self.assertIn('<a href="/history/ah/4183" data-route title="AH Bloemkool">AH BLOEMK</a>', paper)
        for view, html in views.items():
            self.assertEqual(html.count("data-product-link"), 2, view)
            self.assertIn('data-article="30001" data-product="AH Bloemkool" data-product-id="4183" data-kind="article" data-key="30001"', html, view)
        self.assertIn('<a href="/history/ah/4183" data-route>AH Bloemkool</a><button type="button" class="icon-button product-link"', views["category"])
        self.assertIn('<small>€1.29</small>\n      </div>\n      <button type="button"', views["cards"], "rechts op de kaart, na de tekst")

    def test_an_order_from_your_account_has_no_link_button(self):
        order = {"items": [{"productId": 4183, "title": "AH Bloemkool", "quantity": 1, "amount": 1.99}]}
        views = self.run_js(f"console.log(JSON.stringify(views({json.dumps(order)}, 'order', 'ah')));")
        self.assertEqual({view: html.count("data-product-link") for view, html in views.items()}, {"cards": 0, "category": 0, "table": 0})

    def test_an_imported_line_offers_to_change_the_product_it_belongs_to(self):
        items = [{"title": "Kipfilet uit de mail", "productId": "s1", "productTitle": "Picnic kipfilet", "quantity": 1,
                  "link": {"kind": "name", "key": "kipfilet uit de mail"}},
                 {"title": "Iets anders", "productId": None, "quantity": 2, "link": None}]
        found = self.run_js(f"const items = {json.dumps(items)};\n"
                            "console.log(JSON.stringify({ lines: items.map((item) => importedLinkLine(item, 'picnic')),"
                            " ah: views({ items }, 'imported', 'ah'), picnic: [renderPicnicOrderItem(items[0], true), renderPicnicOrderItem(items[0])],"
                            " crisp: [renderCrispOrderItem(items[1], true), renderCrispOrderItem(items[1], false)] }));")
        self.assertEqual(found["lines"], [
            {"store": "picnic", "name": "Kipfilet uit de mail", "article": None, "product": "Picnic kipfilet", "productId": "s1",
             "link": {"kind": "name", "key": "kipfilet uit de mail"}},
            {"store": "picnic", "name": "Iets anders", "article": None, "product": None, "productId": None, "link": None},
        ])
        self.assertEqual({view: html.count("data-product-link") for view, html in found["ah"].items()}, {"cards": 2, "category": 2, "table": 2})
        self.assertIn('data-store="ah" data-name="Kipfilet uit de mail" data-article="" data-product="Picnic kipfilet" data-product-id="s1"', found["ah"]["cards"])
        self.assertEqual([html.count("data-product-link") for html in found["picnic"]], [1, 0], "alleen bij een ingelezen bestelling")
        self.assertEqual([html.count("data-product-link") for html in found["crisp"]], [1, 0])
        self.assertIn('data-store="crisp"', found["crisp"][0])

    def test_the_product_you_have_now_stands_out_in_the_results(self):
        found = self.run_js("console.log(JSON.stringify([productLinkItem({ productId: 4183, title: 'AH Bloemkool', brand: 'AH', unitSize: 'per stuk', price: 1.99 }, 0, '4183'),"
                            " productLinkItem({ productId: '9', title: 'AH Broccoli', image: 'https://x/b.jpg' }, 1, '4183')]));")
        self.assertIn('class="link-item is-self" data-link-pick="0" aria-current="true"', found[0])
        self.assertIn("AH · per stuk · €1.99", found[0])
        self.assertIn('<span class="link-item__photo" aria-hidden="true"><svg aria-hidden="true"><path d="FOTO"/></svg></span>', found[0],
                      "zonder foto een pictogram op de plek van de foto")
        self.assertIn('class="link-item" data-link-pick="1"', found[1])
        self.assertIn('<img src="https://x/b.jpg"', found[1])


class PageTest(unittest.TestCase):
    def test_the_dialog_is_on_the_page_and_the_script_loads_after_the_store_scripts(self):
        for needle in ('id="link-dialog"', 'aria-labelledby="link-dialog-title"', 'id="link-dialog-form" role="search"', 'id="link-dialog-query"',
                       'id="link-dialog-list"', 'id="link-dialog-message" role="status" aria-live="polite"', 'id="link-dialog-unlink" hidden',
                       'id="link-dialog-close"'):
            self.assertIn(needle, HTML, needle)
        self.assertLess(HTML.index("filename='crisp.js'"), HTML.index("filename='product_links.js'"))
        self.assertLess(HTML.index("filename='orders.js'"), HTML.index("filename='product_links.js'"))

    def test_every_view_redraws_after_a_link_changes(self):
        self.assertIn('window.addEventListener("ms:product-links", (event) => refreshLinkedPurchase(event.detail?.store));', SOURCES["orders.js"])
        for file in ("picnic.js", "crisp.js"):
            self.assertIn('window.addEventListener("ms:product-links"', SOURCES[file], file)
        self.assertIn('window.addEventListener("ms:product-links"', (ROOT / "static" / "offers_history.js").read_text(encoding="utf-8"))
        self.assertIn('new CustomEvent("ms:product-links", { detail: { store } })', SOURCES["product_links.js"])

    def test_both_languages_have_the_texts(self):
        for key in ("links.link", "links.change", "links.link_line", "links.change_line", "links.intro", "links.current", "links.current_own",
                    "links.search", "links.search_button", "links.searching", "links.none", "links.saving", "links.unlink", "links.note",
                    "backup.count.productLinks"):
            self.assertIn(f'"{key}":', NL, key)
            self.assertIn(f'"{key}":', EN, key)


if __name__ == "__main__":
    unittest.main()
