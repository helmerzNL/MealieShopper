import json
import os
import tempfile
import time
import unittest
from unittest import mock

os.environ.setdefault("MEALIESHOPPER_DATA_DIR", tempfile.mkdtemp())

from mealieshopper import auth, crisp, mealie  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402


def response(data=None, *, status=200):
    """Een nagebootst `requests`-antwoord."""
    reply = mock.Mock()
    reply.status_code = status
    reply.ok = status < 400
    reply.content = b"" if data is None else json.dumps(data).encode()
    reply.text = "" if data is None else json.dumps(data)
    reply.json.return_value = data
    return reply


def product(product_id=101, title="Volle melk", price="1.89", **extra):
    """Een product zoals Crisp het in zoekresultaten en het mandje zet."""
    return {"itemType": "product", "id": product_id, "title": title, "price": price, "basePrice": price,
            "weightIndication": "1 liter", "images": ["prodBlob123"], "deliverable": True, **extra}


def recipe(recipe_id=55, **extra):
    return {
        "id": recipe_id,
        "title": "Curry met pompoen",
        "subTitle": "met zilvervliesrijst",
        "description": "Een **romige** curry.",
        "portions": 2,
        "images": ["recipeBlob456"],
        "cookingMethods": {"short": {"minutes": 30, "text": "30 min"}},
        "ingredientGroups": [
            {"title": None, "groupType": "default", "ingredients": [
                {"type": "default", "productId": 101, "formattedName": "zilvervliesrijst", "formattedQuantity": "150 g"},
                {"type": "default", "productId": 93, "formattedName": "flespompoen", "formattedQuantity": "400 g"},
            ]},
            {"title": "Zelf toe te voegen", "groupType": "addSelf", "ingredients": [
                {"type": "addSelf", "productId": 0, "formattedName": "olie om te bakken", "formattedQuantity": None},
            ]},
        ],
        "instructions": [{"instruction": "Zet **15 min** de rijst op."}, {"instruction": "Snijd de pompoen."}],
        "shareUrl": "https://crisp.nl/r/abc",
        **extra,
    }


class TempDataDirTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"MEALIESHOPPER_DATA_DIR": tmp.name, "PASSKEY_AUTH_ENABLED": "false"})
        patcher.start()
        self.addCleanup(patcher.stop)
        auth.init_db()

    def link(self, country="nl"):
        auth.set_secret(crisp.CLIENT_SECRET, "CLIENTabc1234567890x")
        auth.set_secret(crisp.COUNTRY_SECRET, country)


class ClientIdTest(unittest.TestCase):
    def test_client_id_looks_like_the_app_makes_it(self):
        first, second = crisp.generate_client_id(), crisp.generate_client_id()
        self.assertRegex(first, r"^[A-Za-z0-9]{20}$")
        self.assertNotEqual(first, second)


class CallTest(unittest.TestCase):
    def test_headers_follow_the_app_and_name_mealieshopper(self):
        with mock.patch.object(crisp.requests, "request", return_value=response({"ok": True})) as request:
            self.assertEqual(crisp._call("POST", "/search", "CLIENT", {"query": "melk"}), {"ok": True})
        method, url = request.call_args.args
        headers = request.call_args.kwargs["headers"]
        self.assertEqual((method, url), ("POST", "https://crispapp.nl/v1/search"))
        self.assertEqual(headers["Authorization"], "bearer CLIENT")
        self.assertEqual(headers["X-Crisp-Agent"], crisp.AGENT)
        self.assertEqual(headers["Origin"], "https://crispapp.nl")
        self.assertTrue(headers["User-Agent"].startswith("MealieShopper/"))
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(request.call_args.kwargs["json"], {"query": "melk"})

    def test_refused_session_is_an_auth_error_without_the_text_of_crisp(self):
        for status in (401, 403):
            with mock.patch.object(crisp.requests, "request", return_value=response({"error": "geheim"}, status=status)):
                with self.assertRaises(crisp.CrispAuthError) as raised:
                    crisp._call("GET", "/user", "CLIENT")
            self.assertNotIn("geheim", str(raised.exception))

    def test_other_errors_carry_the_status(self):
        with mock.patch.object(crisp.requests, "request", return_value=response({"x": 1}, status=500)):
            with self.assertRaisesRegex(RuntimeError, "^500"):
                crisp._call("GET", "/basket/main", "CLIENT")


class LoginTest(TempDataDirTest):
    def test_requesting_a_code_keeps_a_pending_login(self):
        with mock.patch.object(crisp, "_call", return_value={"sent": True}) as call:
            result = crisp.start_login(" iemand@example.com ", "BE")
        self.assertEqual(result, {"connected": False, "codeRequired": True})
        method, path, client_id, body = call.call_args.args
        self.assertEqual((method, path, body), ("POST", "/user/login", {"email": "iemand@example.com", "country": "be"}))
        self.assertRegex(client_id, r"^[A-Za-z0-9]{20}$")
        self.assertEqual(crisp.configured_client_id(), "")
        self.assertTrue(crisp.status()["codePending"])

    def test_the_code_links_the_client_id_of_the_request(self):
        with mock.patch.object(crisp, "_call", return_value={"sent": True}) as call:
            crisp.start_login("iemand@example.com", "nl")
        client_id = call.call_args.args[2]
        with mock.patch.object(crisp, "_call", return_value={"id": 42}) as call:
            self.assertEqual(crisp.verify_login(" 123456 "), {"connected": True})
        self.assertEqual(call.call_args.args, ("POST", "/user/login", client_id, {"email": "iemand@example.com", "code": "123456", "country": "nl"}))
        self.assertEqual(crisp.configured_client_id(), client_id)
        self.assertEqual(crisp.country(), "nl")
        self.assertEqual(crisp.status(), {"connected": True, "codePending": False, "country": "nl"})

    def test_an_already_logged_in_client_is_linked_at_once(self):
        with mock.patch.object(crisp, "_call", return_value={"id": 42}):
            self.assertEqual(crisp.start_login("iemand@example.com"), {"connected": True, "codeRequired": False})
        self.assertTrue(crisp.configured_client_id())

    def test_unknown_email_gets_our_own_text(self):
        with mock.patch.object(crisp, "_call", return_value={"error": "webLogin"}):
            with self.assertRaisesRegex(crisp.CrispLoginError, "account"):
                crisp.start_login("nieuw@example.com")
        with mock.patch.object(crisp, "_call", return_value={"error": "ruwe tekst van crisp"}):
            with self.assertRaises(crisp.CrispLoginError) as raised:
                crisp.start_login("iemand@example.com")
        self.assertNotIn("ruwe tekst", str(raised.exception))
        self.assertFalse(crisp.status()["codePending"])

    def test_wrong_code_and_missing_login(self):
        with self.assertRaises(crisp.CrispLoginError):
            crisp.verify_login("123456")  # er loopt geen login
        with mock.patch.object(crisp, "_call", return_value={"sent": True}):
            crisp.start_login("iemand@example.com")
        with mock.patch.object(crisp, "_call", return_value={"error": "invalidCode"}):
            with self.assertRaises(crisp.CrispLoginError):
                crisp.verify_login("000000")
        self.assertEqual(crisp.configured_client_id(), "")
        with self.assertRaises(ValueError):
            crisp.verify_login(" ")

    def test_a_pending_login_expires(self):
        with mock.patch.object(crisp, "_call", return_value={"sent": True}):
            crisp.start_login("iemand@example.com")
        with mock.patch.object(crisp.time, "time", return_value=time.time() + crisp.PENDING_TTL_SECONDS + 5):
            self.assertFalse(crisp.status()["codePending"])
            with self.assertRaises(crisp.CrispLoginError):
                crisp.verify_login("123456")

    def test_input_is_checked_before_calling_crisp(self):
        with mock.patch.object(crisp, "_call", side_effect=AssertionError("niet bellen")):
            with self.assertRaises(ValueError):
                crisp.start_login("geen-adres")
            with self.assertRaises(ValueError):
                crisp.start_login("iemand@example.com", "de")

    def test_unlink_logs_out_and_forgets_even_when_crisp_fails(self):
        self.link()
        with mock.patch.object(crisp, "_call", side_effect=RuntimeError("offline")) as call:
            self.assertEqual(crisp.unlink(), {"connected": False})
        self.assertEqual(call.call_args.args[3], {"logout": True})
        self.assertEqual(crisp.configured_client_id(), "")
        self.assertEqual(auth.get_secret(crisp.COUNTRY_SECRET), "")

    def test_verify_checks_that_crisp_still_knows_the_user(self):
        self.link("be")
        with mock.patch.object(crisp, "_call", return_value={"id": 42, "email": "iemand@example.com"}):
            result = crisp.status(verify=True)
        self.assertEqual((result["verified"], result["country"]), (True, "be"))
        self.assertNotIn("email", json.dumps(result))
        with mock.patch.object(crisp, "_call", return_value={"country": "nl"}):
            result = crisp.status(verify=True)
        self.assertFalse(result["verified"])
        self.assertIn("Koppel opnieuw", result["error"])
        with mock.patch.object(crisp, "_call", side_effect=RuntimeError("500: interne tekst")):
            self.assertEqual(crisp.status(verify=True)["error"], "RuntimeError")

    def test_calls_without_a_link_say_how_to_link(self):
        with self.assertRaisesRegex(RuntimeError, "Instellingen"):
            crisp.get_cart()

    def test_a_refused_session_asks_to_link_again(self):
        self.link()
        with mock.patch.object(crisp, "_call", side_effect=crisp.CrispAuthError("x")):
            with self.assertRaisesRegex(crisp.CrispAuthError, "Koppel opnieuw"):
                crisp.get_cart()


class ProductTest(unittest.TestCase):
    def test_product_in_the_shape_of_the_other_stores(self):
        summary = crisp.product_summary(product(supplier={"name": "Weidemelk"}))
        self.assertEqual(summary, {
            "id": "101", "title": "Volle melk", "brand": "Weidemelk", "unitSize": "1 liter", "price": 1.89,
            "image": "https://crispapp.nl/blob/prodBlob123?thumb=512", "isBonus": False, "url": None, "available": True,
        })

    def test_without_weight_it_is_per_piece_and_paths_become_addresses(self):
        summary = crisp.product_summary(product(weightIndication=None, images=["/asset/xyz"]))
        self.assertEqual((summary["unitSize"], summary["image"]), ("per stuk", "https://crispapp.nl/asset/xyz"))
        self.assertIsNone(crisp.product_summary(product(images=["javascript:x"]))["image"])

    def test_blob_ids_become_addresses_like_in_the_app(self):
        self.assertEqual(crisp._image(["abc-DEF_1.2"]), "https://crispapp.nl/blob/abc-DEF_1.2?thumb=512")
        self.assertEqual(crisp._image("abc", crisp.THUMB_LARGE), "https://crispapp.nl/blob/abc?thumb=1024")
        self.assertEqual(crisp._image([{"legacyParse": "oud123"}]), "https://crispapp.nl/blob/oud123?thumb=512")
        self.assertEqual(crisp._image(["", None, "tweede"]), "https://crispapp.nl/blob/tweede?thumb=512")
        self.assertEqual(crisp._image("https://cdn.example/x.jpg"), "https://cdn.example/x.jpg")
        for bad in ("javascript:x", "a b", "x?y", "x#y", "", "  ", None, [], {}, 5):
            self.assertIsNone(crisp._image(bad), bad)

    def test_the_large_photo_is_a_bigger_thumb(self):
        result = crisp.candidate(crisp.product_summary(product()), 1, None)
        self.assertEqual(result["image"], "https://crispapp.nl/blob/prodBlob123?thumb=512")
        self.assertEqual(result["imageLarge"], "https://crispapp.nl/blob/prodBlob123?thumb=1024")
        outside = crisp.candidate(crisp.product_summary(product(images=["https://cdn.example/x.jpg"])), 1, None)
        self.assertEqual(outside["imageLarge"], "https://cdn.example/x.jpg")

    def test_a_lower_price_or_a_promotion_is_a_deal(self):
        summary = crisp.product_summary(product(price="1.49", basePrice="1.89", promotion={"label": "Tijdelijk goedkoper"}))
        self.assertTrue(summary["isBonus"])
        self.assertEqual(summary["promo"], {"label": "Tijdelijk goedkoper", "priceBefore": 1.89, "validFrom": None, "validUntil": None})
        self.assertFalse(crisp.product_summary(product(promotion={"isEmptyPromo": True, "label": "x"}))["isBonus"])

    def test_undeliverable_products_are_unavailable(self):
        self.assertFalse(crisp.product_summary(product(deliverable=False))["available"])

    def test_candidate_counts_packs(self):
        found = crisp.product_summary(product(weightIndication="500 gram", price="2.10"))
        result = crisp.candidate(found, 1, "kg")
        self.assertEqual((result["productId"], result["packs"], result["url"]), ("101", 2, None))
        self.assertIsNone(result["deal"])


class SearchTest(TempDataDirTest):
    def setUp(self):
        super().setUp()
        self.link()

    def test_search_asks_like_the_app_and_keeps_deliverable_products(self):
        answer = {"data": [
            {"itemType": "text", "id": 1, "title": "Zoek ook in recepten"},
            product(1, "Melk"), product(2, "Karnemelk", deliverable=False), product(3, "Halfvolle melk"),
            {"itemType": "recipe", "id": 9, "title": "Melkrijst"},
        ], "cursor": "abc"}
        with mock.patch.object(crisp, "_call", return_value=answer) as call:
            found = crisp.search_products(" melk ", 5)
        self.assertEqual(call.call_args.args[1:], ("/search", "CLIENTabc1234567890x", {"query": "melk", "cursor": False, "limit": 10}))
        self.assertEqual([p["id"] for p in found], ["1", "3"])
        with mock.patch.object(crisp, "_call", return_value=answer):
            self.assertEqual(crisp.search_product("melk")["id"], "1")

    def test_empty_query_does_not_call_crisp(self):
        with mock.patch.object(crisp, "_call", side_effect=AssertionError("niet bellen")):
            self.assertEqual(crisp.search_products("  "), [])


class CartTest(TempDataDirTest):
    def setUp(self):
        super().setUp()
        self.link()

    def test_cart_summary_reads_the_products_by_id(self):
        basket = {"products": {
            "101": {**product(101, "Melk"), "count": 2, "totalPrice": "3.78"},
            "93": {**product(93, "Pompoen", price="2.50"), "count": 1},
            "7": {**product(7, "Weg"), "count": 0},
        }, "payPrice": "9.23"}
        summary = crisp.cart_summary(basket)
        self.assertEqual([(i["id"], i["quantity"], i["price"]) for i in summary["items"]], [("101", 2, 3.78), ("93", 1, 2.5)])
        self.assertEqual((summary["totalCount"], summary["totalPrice"], summary["checkoutTotal"]), (3, 6.28, 9.23))
        self.assertEqual(crisp.cart_summary(None)["items"], [])

    def test_add_to_cart_sets_the_new_count_in_one_request(self):
        before = {"products": {"101": {**product(101), "count": 1}}}
        after = {"products": {"101": {**product(101), "count": 4}, "93": {**product(93), "count": 2}}}
        calls = []

        def fake(method, path, client_id, body=None):
            calls.append((method, path, body))
            return before if method == "GET" else after

        with mock.patch.object(crisp, "_call", side_effect=fake):
            result = crisp.add_to_cart([
                {"productId": "101", "quantity": 2}, {"productId": "93", "quantity": 2},
                {"productId": "101", "quantity": 1}, {"productId": "../x", "quantity": 1},
            ])
        self.assertEqual(calls[0][:2], ("GET", "/basket/main"))
        self.assertEqual(calls[1], ("PUT", "/basket/main", {"products": {
            "101": {"count": 4, "from": crisp.FROM}, "93": {"count": 2, "from": crisp.FROM}}}))
        self.assertEqual(result["added"], [{"productId": "101", "quantity": 3}, {"productId": "93", "quantity": 2}])
        self.assertEqual((result["failed"], result["cart"]["totalCount"]), ([], 6))

    def test_a_product_that_did_not_grow_is_reported(self):
        basket = {"products": {}}
        with mock.patch.object(crisp, "_call", return_value=basket):
            result = crisp.add_to_cart([{"productId": "5", "quantity": 1}])
        self.assertEqual((result["added"], [f["productId"] for f in result["failed"]]), ([], ["5"]))

    def test_nothing_to_add_does_not_call_crisp(self):
        with mock.patch.object(crisp, "_call", side_effect=AssertionError("niet bellen")):
            self.assertEqual(crisp.add_to_cart([{"productId": ""}]), {"added": [], "failed": [], "cart": None})


class RecipeTest(TempDataDirTest):
    def setUp(self):
        super().setUp()
        self.link()

    def test_recipe_search_leaves_out_bundles(self):
        answer = {"data": [
            {**recipe(1), "itemType": "recipe", "type": ""},
            {**recipe(2), "itemType": "recipe", "type": "bundle"},
            {**product(3), "itemType": "product"},
        ]}
        with mock.patch.object(crisp, "_call", return_value=answer) as call:
            result = crisp.search_recipes("curry")
        self.assertEqual(call.call_args.args[1], "/search/recipe")
        self.assertEqual(call.call_args.args[3]["query"], "curry")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["recipes"][0], {
            "id": "1", "name": "Curry met pompoen", "image": "https://crispapp.nl/blob/recipeBlob456?thumb=512", "minutes": 30,
            "portions": 2, "tags": ["met zilvervliesrijst"],
        })
        with self.assertRaises(ValueError):
            crisp.search_recipes(" ")

    def test_recipe_detail_with_ingredients_and_plain_steps(self):
        with mock.patch.object(crisp, "_call", return_value=recipe()) as call:
            detail = crisp.get_recipe("55")
        self.assertEqual(call.call_args.args[1], "/recipe/55")
        self.assertEqual([i["line"] for i in detail["ingredients"]], ["150 g zilvervliesrijst", "400 g flespompoen", "olie om te bakken"])
        self.assertEqual([i["addSelf"] for i in detail["ingredients"]], [False, False, True])
        self.assertEqual(detail["steps"], ["Zet 15 min de rijst op.", "Snijd de pompoen."])
        self.assertEqual((detail["description"], detail["minutes"], detail["url"]), ("Een romige curry.", 30, "https://crisp.nl/r/abc"))
        self.assertEqual(detail["image"], "https://crispapp.nl/blob/recipeBlob456?thumb=1024")

    def test_invalid_ids_never_reach_crisp(self):
        with mock.patch.object(crisp, "_call", side_effect=AssertionError("niet bellen")):
            for bad in ("", "abc", "1/2", "../user"):
                with self.assertRaises(ValueError):
                    crisp.get_recipe(bad)
                with self.assertRaises(ValueError):
                    crisp.source_url(bad)

    def test_jsonld_for_mealie(self):
        data = crisp.jsonld(crisp.recipe_details(recipe()))
        self.assertEqual(data["@type"], "Recipe")
        self.assertEqual(data["recipeIngredient"][0], "150 g zilvervliesrijst")
        self.assertEqual(data["recipeInstructions"][1], {"@type": "HowToStep", "text": "Snijd de pompoen."})
        self.assertEqual((data["recipeYield"], data["totalTime"]), ("2", "PT30M"))
        self.assertEqual(crisp.source_url(55), "https://crisp.nl/#recipe-55")
        with self.assertRaises(RuntimeError):
            crisp.jsonld(crisp.recipe_details(recipe(ingredientGroups=[], instructions=[])))


class ExploreTest(TempDataDirTest):
    def test_without_a_link_nothing_is_called(self):
        with mock.patch.object(crisp, "_call", side_effect=AssertionError("niet bellen")):
            self.assertEqual(crisp.explore()["linked"], False)

    def test_report_has_field_names_but_no_ids_or_values(self):
        self.link()
        answers = {
            "/user": {"id": 4242, "email": "iemand@example.com"},
            "/search": {"data": [product(98765, "Geheime melk")]},
            "/product/98765": product(98765, "Geheime melk"),
            "/basket/main": {"products": {"98765": {**product(98765), "count": 1}}},
            "/search/recipe": {"data": [{**recipe(31337), "itemType": "recipe"}]},
            "/recipe/31337": recipe(31337),
        }
        with mock.patch.object(crisp, "_call", side_effect=lambda method, path, client_id, body=None: answers[path]):
            report = crisp.explore()
        self.assertEqual((report["working"], len(report["checks"])), (6, 6))
        dumped = json.dumps(report)
        for secret in ("98765", "31337", "4242", "Geheime", "iemand@example.com", "1.89"):
            self.assertNotIn(secret, dumped)
        self.assertIn("weightIndication", dumped)
        self.assertIn("formattedQuantity", dumped)

    def test_failures_show_our_text_or_the_status_only(self):
        self.link()
        with mock.patch.object(crisp, "_call", side_effect=RuntimeError("500: interne tekst van crisp")):
            report = crisp.explore()
        self.assertEqual(report["working"], 0)
        self.assertEqual({entry["error"] for entry in report["checks"]}, {"HTTP 500"})


class CrispRoutesTest(TempDataDirTest):
    def setUp(self):
        super().setUp()
        self.client = create_app().test_client()

    def test_login_routes(self):
        with mock.patch.object(crisp, "start_login", return_value={"connected": False, "codeRequired": True}) as start:
            body = self.client.post("/api/crisp/auth", json={"email": "iemand@example.com", "country": "be"}).get_json()
        self.assertEqual(start.call_args.args, ("iemand@example.com", "be"))
        self.assertTrue(body["codeRequired"])
        with mock.patch.object(crisp, "verify_login", side_effect=crisp.CrispLoginError("Crisp weigerde de code")):
            reply = self.client.post("/api/crisp/auth/verify", json={"code": "1"})
        self.assertEqual((reply.status_code, reply.get_json()["error"]), (400, "Crisp weigerde de code"))
        with mock.patch.object(crisp, "_call", return_value={}):
            self.assertEqual(self.client.post("/api/crisp/auth/logout").get_json(), {"connected": False})
        self.assertEqual(self.client.get("/api/crisp/auth/status").get_json()["connected"], False)

    def test_store_errors_hide_the_answer_of_crisp(self):
        with mock.patch.object(crisp, "get_cart", side_effect=RuntimeError("500: ruwe tekst")):
            reply = self.client.get("/api/crisp/cart")
        self.assertEqual((reply.status_code, reply.get_json()["error"]), (424, "Crisp fout"))

    def test_preview_and_cart_use_the_shared_flow(self):
        found = [crisp.product_summary(product(1, "Eieren", weightIndication="6 stuks", price="2.49"))]
        with mock.patch.object(crisp, "search_products", return_value=found):
            candidate = self.client.post("/api/crisp/cart/preview", json={"items": [
                {"query": "eieren", "quantity": 12, "unit": "stuks"}]}).get_json()["items"][0]["candidates"][0]
        self.assertEqual((candidate["productId"], candidate["packs"]), ("1", 2))
        result = {"added": [{"productId": "1", "quantity": 2}], "failed": [], "cart": None}
        with mock.patch.object(crisp, "search_product", return_value=found[0]), \
                mock.patch.object(crisp, "add_to_cart", return_value=result) as add:
            body = self.client.post("/api/crisp/cart", json={"items": [{"query": "eieren", "quantity": 12, "unit": "stuks"}]}).get_json()
        self.assertEqual(add.call_args.args[0][0]["quantity"], 2)
        self.assertEqual(body["added"], 1)

    def test_recipe_routes_and_import(self):
        with mock.patch.object(crisp, "search_recipes", return_value={"recipes": [], "total": 0}):
            self.assertEqual(self.client.get("/api/crisp/recipes?q=curry").get_json(), {"recipes": [], "total": 0})
        detail = crisp.recipe_details(recipe())
        with mock.patch.object(crisp, "get_recipe", return_value=detail):
            self.assertEqual(self.client.get("/api/crisp/recipes/55").get_json()["name"], "Curry met pompoen")
        with mock.patch.object(mealie, "ensure_configured"), \
                mock.patch.object(crisp, "get_recipe", return_value=detail), \
                mock.patch.object(mealie, "import_via_jsonld", return_value="curry") as imported, \
                mock.patch.object(mealie, "recipe_page_url", return_value="http://mealie/curry"):
            body = self.client.post("/api/crisp/recipes/55/import").get_json()
        self.assertEqual(body, {"slug": "curry", "mealieUrl": "http://mealie/curry"})
        self.assertEqual(imported.call_args.args[0], "https://crisp.nl/#recipe-55")
        with mock.patch.object(mealie, "ensure_configured"):
            self.assertEqual(self.client.post("/api/crisp/recipes/abc/import").status_code, 400)

    def test_planning_reuses_a_recipe_that_is_already_in_mealie(self):
        with mock.patch.object(mealie, "ensure_configured"), \
                mock.patch.object(mealie, "find_recipe_by_source", return_value="curry") as find, \
                mock.patch.object(mealie, "recipe_page_url", return_value="http://mealie/curry"), \
                mock.patch.object(crisp, "get_recipe", side_effect=AssertionError("niet ophalen")):
            body = self.client.post("/api/crisp/recipes/55/import?reuse=1").get_json()
        self.assertTrue(body["reused"])
        self.assertEqual(find.call_args.args[0], "https://crisp.nl/#recipe-55")

    def test_linking_is_for_the_admin_only(self):
        for path in ("/api/crisp/auth", "/api/crisp/auth/verify", "/api/crisp/auth/logout", "/api/crisp/explore"):
            self.assertTrue(auth.admin_only(path), path)
        self.assertFalse(auth.admin_only("/api/crisp/auth/status"))
        self.assertFalse(auth.admin_only("/api/crisp/cart"))


if __name__ == "__main__":
    unittest.main()
