import base64
import hashlib
import json
import os
import tempfile
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlsplit

os.environ.setdefault("MEALIESHOPPER_DATA_DIR", tempfile.mkdtemp())

from mealieshopper import auth, jumbo, jumbo_explore  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402


def reply(data=None, *, status=200, text=None, content_type="application/json"):
    """Een nagebootst `requests`-antwoord."""
    response = mock.Mock()
    response.status_code = status
    response.ok = status < 400
    response.text = text if text is not None else json.dumps(data)
    response.json.side_effect = (lambda: data) if data is not None else ValueError("geen JSON")
    response.headers = {"content-type": content_type}
    return response


TOKENS = {"access_token": "acc", "refresh_token": "ref", "expires_in": 86400}


def product(sku, title, price=299, promo=None, **extra):
    return {"sku": sku, "title": title, "link": f"/producten/{sku}",
            "price": {"price": price, "promoPrice": promo, "pricePerUnit": {"price": 100}},
            "availability": {"isAvailable": True}, "promotions": [], **extra}


class TempDataDirTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"MEALIESHOPPER_DATA_DIR": tmp.name, "PASSKEY_AUTH_ENABLED": "false"})
        patcher.start()
        self.addCleanup(patcher.stop)
        auth.init_db()
        jumbo._access_cache.update(token="", expires_at=0.0)
        jumbo._search_variant = 0
        jumbo._promo_dates = True

    def link(self, refresh="ref"):
        auth.set_secret(jumbo.REFRESH_SECRET, refresh)


class LoginTest(TempDataDirTest):
    def test_authorize_url_uses_pkce_and_the_app_redirect(self):
        url = jumbo.start_login()
        parts = urlsplit(url)
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        self.assertEqual(f"{parts.scheme}://{parts.netloc}{parts.path}", "https://auth.jumbo.com/authorize")
        self.assertEqual(query["redirect_uri"], "jumbo://callbacks/login")
        self.assertEqual(query["response_type"], "code")
        self.assertEqual(query["code_challenge_method"], "S256")
        self.assertIn("offline_access", query["scope"])
        pending = json.loads(auth.get_secret(jumbo.PENDING_SECRET))
        digest = hashlib.sha256(pending["verifier"].encode()).digest()
        self.assertEqual(query["code_challenge"], base64.urlsafe_b64encode(digest).rstrip(b"=").decode())
        self.assertEqual(query["state"], pending["state"])
        self.assertGreaterEqual(len(pending["verifier"]), 43)  # PKCE: 43 tot 128 tekens

    def test_a_pasted_console_line_is_exchanged_with_the_verifier(self):
        url = jumbo.start_login()
        state = parse_qs(urlsplit(url).query)["state"][0]
        verifier = json.loads(auth.get_secret(jumbo.PENDING_SECRET))["verifier"]
        line = f"Failed to launch 'jumbo://callbacks/login?code=abc123&state={state}' because the protocol"
        with mock.patch.object(jumbo.requests, "post", return_value=reply(TOKENS)) as post:
            result = jumbo.link_account(line)
        sent = post.call_args.kwargs["json"]
        self.assertEqual(post.call_args.args[0], "https://auth.jumbo.com/oauth/token")
        self.assertEqual(sent["grant_type"], "authorization_code")
        self.assertEqual((sent["code"], sent["code_verifier"]), ("abc123", verifier))
        self.assertEqual((sent["audience"], sent["redirect_uri"]), (jumbo.AUDIENCE, jumbo.REDIRECT_URI))
        self.assertEqual(result, {"connected": True, "type": "code"})
        self.assertEqual(jumbo.configured_refresh_token(), "ref")
        self.assertEqual(auth.get_secret(jumbo.PENDING_SECRET), "")

    def test_a_code_from_another_login_attempt_is_refused(self):
        jumbo.start_login()
        with mock.patch.object(jumbo.requests, "post", side_effect=AssertionError("niet ruilen")):
            with self.assertRaises(jumbo.JumboAuthError):
                jumbo.link_account("jumbo://callbacks/login?code=abc&state=anders")

    def test_without_a_login_in_progress_nothing_is_exchanged(self):
        with mock.patch.object(jumbo.requests, "post", side_effect=AssertionError("niet ruilen")):
            with self.assertRaises(RuntimeError):
                jumbo.link_account("jumbo://callbacks/login?code=abc")

    def test_a_stale_login_expires(self):
        jumbo.start_login()
        later = mock.patch.object(jumbo.time, "time", return_value=jumbo.time.time() + jumbo.PENDING_TTL_SECONDS + 5)
        with later, self.assertRaises(RuntimeError):
            jumbo.link_account("jumbo://callbacks/login?code=abc")

    def test_a_bare_refresh_token_is_verified_and_stored(self):
        with mock.patch.object(jumbo.requests, "post", return_value=reply(TOKENS)) as post:
            result = jumbo.link_account("v1.MyRefreshToken")
        self.assertEqual(post.call_args.kwargs["json"]["grant_type"], "refresh_token")
        self.assertEqual(result["type"], "refresh")
        self.assertEqual(jumbo.configured_refresh_token(), "ref")  # de vernieuwde waarde gaat voor

    def test_an_expired_code_is_explained(self):
        jumbo.start_login()
        bad = reply({"error": "invalid_grant"}, status=403 - 3)
        bad.status_code = 400
        with mock.patch.object(jumbo.requests, "post", return_value=bad):
            with self.assertRaises(jumbo.JumboAuthError):
                jumbo.link_account("jumbo://callbacks/login?code=abc")
        self.assertEqual(jumbo.configured_refresh_token(), "")

    def test_a_blocked_server_is_reported_as_such(self):
        jumbo.start_login()
        with mock.patch.object(jumbo.requests, "post", return_value=reply(None, status=403, text="Access Denied")):
            with self.assertRaisesRegex(RuntimeError, "403"):
                jumbo.link_account("jumbo://callbacks/login?code=abc")

    def test_empty_input_is_rejected(self):
        with self.assertRaises(ValueError):
            jumbo.link_account("  ")

    def test_unlink_forgets_everything(self):
        self.link()
        jumbo.start_login()
        jumbo._access_cache["token"] = "x"
        self.assertEqual(jumbo.unlink(), {"connected": False})
        self.assertEqual((jumbo.configured_refresh_token(), auth.get_secret(jumbo.PENDING_SECRET), jumbo._access_cache["token"]), ("", "", ""))


class AccessTokenTest(TempDataDirTest):
    def test_unlinked_account_is_refused_without_a_request(self):
        with mock.patch.object(jumbo.requests, "post", side_effect=AssertionError("geen verzoek")):
            with self.assertRaises(RuntimeError):
                jumbo.get_access_token()

    def test_refresh_sends_audience_and_scope_and_caches(self):
        self.link("old")
        with mock.patch.object(jumbo.requests, "post", return_value=reply({**TOKENS, "refresh_token": "rotated"})) as post:
            self.assertEqual(jumbo.get_access_token(), "acc")
            self.assertEqual(jumbo.get_access_token(), "acc")
        self.assertEqual(post.call_count, 1)
        sent = post.call_args.kwargs["json"]
        self.assertEqual((sent["grant_type"], sent["refresh_token"]), ("refresh_token", "old"))
        self.assertEqual((sent["audience"], sent["scope"]), (jumbo.AUDIENCE, jumbo.SCOPE))
        self.assertEqual(jumbo.configured_refresh_token(), "rotated")

    def test_status_verify_reports_a_dead_link_without_leaking_tokens(self):
        self.link("secret-token")
        bad = reply({"error": "invalid_grant"})
        bad.status_code, bad.ok = 400, False
        with mock.patch.object(jumbo.requests, "post", return_value=bad):
            result = jumbo.status(verify=True)
        self.assertEqual((result["connected"], result["verified"]), (True, False))
        self.assertEqual(result["error"], "JumboAuthError")
        self.assertNotIn("secret-token", json.dumps(result))

    def test_status_without_link(self):
        self.assertEqual(jumbo.status(), {"connected": False, "loginPending": False})


class GraphqlTest(TempDataDirTest):
    def setUp(self):
        super().setUp()
        self.link()
        jumbo._access_cache.update(token="acc", expires_at=jumbo.time.time() + 3600)

    def test_headers_and_payload(self):
        with mock.patch.object(jumbo.requests, "post", return_value=reply({"data": {"x": 1}})) as post:
            data = jumbo._graphql("query Q { x }", {"a": 1}, "Q")
        headers = post.call_args.kwargs["headers"]
        self.assertEqual(data, {"x": 1})
        self.assertEqual(headers["Authorization"], "Bearer acc")
        self.assertEqual(headers["apollographql-client-name"], jumbo.BASKET_CLIENT)
        self.assertEqual(post.call_args.kwargs["json"], {"operationName": "Q", "query": "query Q { x }", "variables": {"a": 1}})

    def test_a_401_refreshes_the_token_once(self):
        replies = [reply(None, status=401, text="no"), reply({"data": {"ok": True}})]
        with mock.patch.object(jumbo.requests, "post", side_effect=[replies[0], reply(TOKENS), replies[1]]) as post:
            self.assertEqual(jumbo._graphql("query Q { x }", None, "Q"), {"ok": True})
        self.assertEqual(post.call_count, 3)

    def test_a_403_is_reported_as_a_block(self):
        with mock.patch.object(jumbo.requests, "post", return_value=reply(None, status=403, text="Access Denied")):
            with self.assertRaisesRegex(RuntimeError, "403"):
                jumbo._graphql("query Q { x }", None, "Q")

    def test_an_html_page_instead_of_json_is_reported_as_a_block(self):
        with mock.patch.object(jumbo.requests, "post", return_value=reply(None, text="<html>")):
            with self.assertRaises(RuntimeError):
                jumbo._graphql("query Q { x }", None, "Q")

    def test_unauthorized_errors_ask_to_link_again(self):
        errors = {"errors": [{"message": "401 UNAUTHORIZED"}]}
        with mock.patch.object(jumbo.requests, "post", side_effect=[reply(errors), reply(TOKENS), reply(errors)]):
            with self.assertRaises(jumbo.JumboAuthError):
                jumbo._graphql("query Q { x }", None, "Q")

    def test_unknown_fields_are_a_schema_error(self):
        errors = {"errors": [{"message": "Cannot query field \"subtitle\" on type \"Product\"."}]}
        with mock.patch.object(jumbo.requests, "post", return_value=reply(errors)):
            with self.assertRaises(jumbo.JumboSchemaError):
                jumbo._graphql("query Q { x }", None, "Q")

    def test_other_graphql_errors_pass_through(self):
        with mock.patch.object(jumbo.requests, "post", return_value=reply({"errors": [{"message": "boem"}]})):
            with self.assertRaisesRegex(RuntimeError, "boem"):
                jumbo._graphql("query Q { x }", None, "Q")


class SearchTest(TempDataDirTest):
    def setUp(self):
        super().setUp()
        self.link()
        jumbo._access_cache.update(token="acc", expires_at=jumbo.time.time() + 3600)

    def data(self, *products):
        return {"searchProducts": {"count": len(products), "products": list(products)}}

    def test_products_are_normalized(self):
        promo = product("1", "Jumbo Eieren 10 stuks", 299, 249, subtitle="10 stuks", image="https://img/1.jpg")
        found = jumbo.products_from_search(self.data(promo))[0]
        self.assertEqual((found["id"], found["price"], found["isBonus"]), ("1", 2.49, True))
        self.assertEqual((found["unitSize"], found["image"]), ("10 stuks", "https://img/1.jpg"))
        self.assertEqual(found["url"], "https://www.jumbo.com/producten/1")

    def test_pack_size_falls_back_to_the_title(self):
        found = jumbo.products_from_search(self.data(product("2", "Jumbo Halfvolle Melk 1 l")))[0]
        self.assertEqual(found["unitSize"], "Jumbo Halfvolle Melk 1 l")
        self.assertEqual(jumbo.candidate(found, 500, "ml")["packs"], 1)

    def test_promotion_tags_count_as_a_bonus(self):
        raw = product("3", "Kaas", promotions=[{"tags": [{"text": "2 voor 5"}]}])
        self.assertTrue(jumbo.products_from_search(self.data(raw))[0]["isBonus"])

    def test_promotion_text_and_regular_price_are_kept(self):
        raw = product("4", "Kaas", 299, 249, promotions=[{"tags": [{"text": "2 voor 5"}, {"text": "Nu extra"}]}])
        found = jumbo.products_from_search(self.data(raw))[0]
        self.assertEqual(found["promo"]["label"], "2 voor 5 | Nu extra")
        self.assertEqual(found["promo"]["priceBefore"], 2.99)

    def test_a_product_without_promotion_has_no_promo(self):
        found = jumbo.products_from_search(self.data(product("5", "Melk")))[0]
        self.assertFalse(found["isBonus"])
        self.assertNotIn("promo", found)

    def test_odd_responses_give_no_products(self):
        for data in (None, {}, {"searchProducts": None}, {"searchProducts": {"products": [None, {"sku": "1"}]}}):
            self.assertEqual(jumbo.products_from_search(data), [])

    def test_unavailable_products_are_skipped_and_the_limit_applies(self):
        gone = product("9", "Weg")
        gone["availability"] = {"isAvailable": False}
        data = self.data(gone, product("1", "A"), product("2", "B"), product("3", "C"))
        with mock.patch.object(jumbo, "_graphql", return_value=data):
            self.assertEqual([p["id"] for p in jumbo.search_products("x", 2)], ["1", "2"])

    def test_the_query_falls_back_when_jumbo_does_not_know_a_field(self):
        calls = []

        def fake(query, variables, operation, client=jumbo.BASKET_CLIENT):
            calls.append(query)
            if "subtitle" in query:
                raise jumbo.JumboSchemaError('Cannot query field "subtitle"')
            return self.data(product("1", "Melk"))

        with mock.patch.object(jumbo, "_graphql", side_effect=fake):
            self.assertEqual(len(jumbo.search_products("melk")), 1)
            self.assertEqual(len(jumbo.search_products("melk")), 1)
        self.assertEqual(len(calls), 4)  # eerst twee mislukte varianten, daarna direct de werkende
        self.assertEqual(jumbo._search_variant, 2)
        self.assertEqual(calls[-1], calls[2])
        self.assertNotIn("subtitle", calls[2])

    def test_promotion_period_comes_from_start_and_end(self):
        week = {"tags": [{"text": "2 voor 3,99"}], "start": {"iso": "2026-10-21T00:01:00+02:00"}, "end": {"iso": "2026-10-27T23:59:59+01:00"}}
        found = jumbo.products_from_search(self.data(product("5", "Kaas", promotions=[week])))[0]
        self.assertEqual((found["promo"]["validFrom"], found["promo"]["validUntil"]), ("2026-10-21", "2026-10-27"))

    def test_the_period_is_the_running_promotion_that_ends_first(self):
        today = jumbo.datetime.date(2026, 10, 26)
        season = {"start": {"iso": "2026-01-07T00:00:00+01:00"}, "end": {"iso": "2026-11-17T23:59:59+01:00"}}
        week = {"start": {"iso": "2026-10-21T00:01:00+02:00"}, "end": {"iso": "2026-10-27T23:59:59+01:00"}}
        later = {"start": {"iso": "2026-11-01T00:00:00+01:00"}, "end": {"iso": "2026-11-07T23:59:59+01:00"}}
        expired = {"start": {"iso": "2026-09-01T00:00:00+02:00"}, "end": {"iso": "2026-09-07T23:59:59+02:00"}}
        self.assertEqual(jumbo._promotion_period({"promotions": [season, week]}, today), ("2026-10-21", "2026-10-27"))
        self.assertEqual(jumbo._promotion_period({"promotions": [expired, later]}, today), ("2026-11-01", "2026-11-07"))
        self.assertEqual(jumbo._promotion_period({"promotions": [expired]}, today), ("2026-09-01", "2026-09-07"))

    def test_a_promotion_without_dates_has_no_period(self):
        self.assertEqual(jumbo._promotion_period({"promotions": [{"tags": [{"text": "x"}]}, {"start": None, "end": None}, "?"]}), (None, None))
        self.assertEqual(jumbo._promotion_period({}), (None, None))

    def test_the_query_asks_for_the_period_and_drops_it_when_jumbo_does_not_know_it(self):
        calls = []

        def fake(query, variables, operation, client=jumbo.BASKET_CLIENT):
            calls.append(query)
            if "start { iso }" in query:
                raise jumbo.JumboSchemaError('Cannot query field "start" on type "Promotion".')
            return self.data(product("1", "Melk"))

        with mock.patch.object(jumbo, "_graphql", side_effect=fake):
            self.assertEqual(len(jumbo.search_products("melk")), 1)
            self.assertEqual(len(jumbo.search_products("melk")), 1)
        self.assertEqual(len(calls), 3)  # met looptijd mislukt, zonder gelukt, daarna direct zonder
        self.assertIn("start { iso } end { iso }", calls[0])
        self.assertNotIn("start", calls[1])
        self.assertEqual((jumbo._search_variant, jumbo._promo_dates), (0, False))

    def test_search_sends_a_keyword_search(self):
        with mock.patch.object(jumbo, "_graphql", return_value=self.data()) as graphql:
            jumbo.search_products("melk")
        self.assertEqual(graphql.call_args.args[1], {"input": {"searchType": "KEYWORD", "searchTerms": "melk"}})

    def test_blank_queries_do_not_call_jumbo(self):
        with mock.patch.object(jumbo, "_graphql", side_effect=AssertionError("niet zoeken")):
            self.assertEqual(jumbo.search_products("  "), [])

    def test_candidate_has_the_same_shape_as_the_other_stores(self):
        found = jumbo.products_from_search(self.data(product("1", "Eieren", subtitle="10 stuks")))[0]
        candidate = jumbo.candidate(found, 12, "stuks")
        self.assertEqual((candidate["productId"], candidate["packs"]), ("1", 2))
        for key in ("title", "brand", "unitSize", "price", "image", "isBonus", "url", "doubt"):
            self.assertIn(key, candidate)


class BasketTest(TempDataDirTest):
    BASKET = {"id": "b1", "totalProductCount": 3, "lines": [
        {"sku": "1", "id": "l1", "quantity": 2, "details": {"title": "Melk", "price": {"price": 150, "promoPrice": 100}}},
        {"sku": "2", "id": "l2", "quantity": 1, "details": {"title": "Brood", "price": {"price": 250}}},
    ]}

    def setUp(self):
        super().setUp()
        self.link()
        jumbo._access_cache.update(token="acc", expires_at=jumbo.time.time() + 3600)

    def test_summary_uses_the_promotion_price(self):
        summary = jumbo.basket_summary(self.BASKET, 450)
        self.assertEqual([(i["id"], i["quantity"], i["price"]) for i in summary["items"]], [("1", 2, 2.0), ("2", 1, 2.5)])
        self.assertEqual((summary["totalCount"], summary["totalPrice"]), (3, 4.5))

    def test_odd_baskets_do_not_crash(self):
        for basket in (None, {}, {"lines": [None, {"details": None}]}):
            self.assertIn("items", jumbo.basket_summary(basket))

    def test_get_cart(self):
        data = {"activeBasket": {"__typename": "ActiveBasketResult", "basket": {
            **self.BASKET, "calculation": {"totals": {"totalToPay": {"amount": 450, "currency": "EUR"}}}}}}
        with mock.patch.object(jumbo, "_graphql", return_value=data):
            cart = jumbo.get_cart()
        self.assertEqual((cart["totalPrice"], len(cart["items"])), (4.5, 2))

    def test_no_active_basket_is_an_empty_basket(self):
        data = {"activeBasket": {"__typename": "BasketError", "reason": "NO_ACTIVE_BASKET", "errorMessage": ""}}
        with mock.patch.object(jumbo, "_graphql", return_value=data):
            self.assertEqual(jumbo.get_cart()["items"], [])

    def test_other_basket_errors_are_raised(self):
        data = {"activeBasket": {"__typename": "BasketError", "reason": "SESSION_BROKEN", "errorMessage": "kapot"}}
        with mock.patch.object(jumbo, "_graphql", return_value=data):
            with self.assertRaisesRegex(RuntimeError, "SESSION_BROKEN"):
                jumbo.get_cart()

    def add(self, items, effects):
        with mock.patch.object(jumbo, "_graphql", side_effect=effects) as graphql:
            return jumbo.add_to_cart(items), graphql

    def ok(self):
        return {"addBasketLines": {"__typename": "Basket", **self.BASKET}}

    def test_all_products_go_in_one_request_with_clamped_quantities(self):
        result, graphql = self.add(
            [{"productId": "1", "quantity": 500}, {"productId": "2", "quantity": 0}, {"productId": "", "quantity": 1}], [self.ok()])
        self.assertEqual(graphql.call_count, 1)
        sent = graphql.call_args.args[1]["input"]
        self.assertEqual(sent, {"lines": [{"sku": "1", "quantity": 99}, {"sku": "2", "quantity": 1}], "type": "ECOMMERCE"})
        self.assertEqual(len(result["added"]), 2)
        self.assertEqual(result["failed"], [])

    def test_a_refused_batch_is_retried_per_product(self):
        error = {"addBasketLines": {"__typename": "AddBasketLinesError"}}
        result, graphql = self.add(
            [{"productId": "1", "quantity": 1}, {"productId": "2", "quantity": 1}], [error, error, self.ok()])
        self.assertEqual(graphql.call_count, 3)
        self.assertEqual([f["productId"] for f in result["failed"]], ["1"])
        self.assertEqual([a["productId"] for a in result["added"]], ["2"])

    def test_a_single_refused_product_is_reported(self):
        error = {"addBasketLines": {"__typename": "AddBasketLinesError"}}
        result, _ = self.add([{"productId": "1", "quantity": 1}], [error])
        self.assertEqual((result["added"], len(result["failed"])), ([], 1))

    def test_the_add_mutation_does_not_name_an_error_type_jumbo_does_not_have(self):
        # Jumbo weigerde `... on BasketError` bij addBasketLines met een validatiefout (echte server).
        self.assertNotIn("BasketError", jumbo.ADD_LINES_MUTATION)
        self.assertIn("... on Basket {", jumbo.ADD_LINES_MUTATION)
        self.assertIn("BasketError", jumbo.ACTIVE_BASKET_QUERY)  # activeBasket kent hem wel

    def test_a_refusal_names_the_type_jumbo_answered_with(self):
        result, _ = self.add([{"productId": "1", "quantity": 1}], [{"addBasketLines": {"__typename": "SomethingNew"}}])
        self.assertIn("SomethingNew", result["failed"][0]["error"])

    def test_an_empty_answer_is_a_refusal_too(self):
        result, _ = self.add([{"productId": "1", "quantity": 1}], [{}])
        self.assertEqual((result["added"], len(result["failed"])), ([], 1))

    def test_a_dead_link_aborts_the_whole_add(self):
        with self.assertRaises(jumbo.JumboAuthError):
            self.add([{"productId": "1", "quantity": 1}, {"productId": "2", "quantity": 1}], jumbo.JumboAuthError("weg"))

    def test_nothing_to_add_calls_nothing(self):
        result, graphql = self.add([{"productId": "", "quantity": 1}], [])
        graphql.assert_not_called()
        self.assertEqual(result, {"added": [], "failed": [], "cart": None})


class JumboRoutesTest(TempDataDirTest):
    def setUp(self):
        super().setUp()
        self.client = create_app().test_client()

    def test_start_redirects_to_the_jumbo_login(self):
        response = self.client.get("/api/jumbo/auth/start")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].startswith("https://auth.jumbo.com/authorize?"))

    def test_status_route(self):
        self.assertEqual(self.client.get("/api/jumbo/auth/status").get_json()["connected"], False)

    def test_link_route_delegates_and_maps_errors(self):
        with mock.patch.object(jumbo, "link_account", return_value={"connected": True, "type": "code"}) as link:
            body = self.client.post("/api/jumbo/auth", json={"code": "jumbo://x?code=1"}).get_json()
        link.assert_called_once_with("jumbo://x?code=1")
        self.assertTrue(body["connected"])
        with mock.patch.object(jumbo, "link_account", side_effect=jumbo.JumboAuthError("verlopen")):
            self.assertEqual(self.client.post("/api/jumbo/auth", json={}).status_code, 400)
        with mock.patch.object(jumbo, "get_cart", side_effect=RuntimeError("500: stuk")):
            response = self.client.get("/api/jumbo/cart")
        self.assertEqual(response.status_code, 424)
        self.assertEqual(response.get_json()["error"], "Jumbo fout")

    def test_explorer_reports_error_type_without_exception_details(self):
        report, _ = jumbo_explore._probe("test", mock.Mock(side_effect=RuntimeError("private response body")))
        self.assertEqual(report["error"], "RuntimeError")

    def test_preview_uses_jumbo_candidates(self):
        products = [{"id": "1", "title": "Eieren", "unitSize": "10 stuks", "price": 2.49, "image": None, "url": "https://www.jumbo.com/p/1"}]
        with mock.patch.object(jumbo, "search_products", return_value=products):
            body = self.client.post("/api/jumbo/cart/preview", json={"items": [{"query": "eieren", "quantity": 12, "unit": "stuks"}]}).get_json()
        candidate = body["items"][0]["candidates"][0]
        self.assertEqual((candidate["productId"], candidate["packs"], candidate["url"]), ("1", 2, "https://www.jumbo.com/p/1"))

    def test_cart_searches_and_adds(self):
        found = {"id": "1", "title": "Eieren", "unitSize": "10 stuks", "price": 2.49}
        result = {"added": [{"productId": "1", "quantity": 2}], "failed": [], "cart": {"items": []}}
        with mock.patch.object(jumbo, "search_product", return_value=found), \
                mock.patch.object(jumbo, "add_to_cart", return_value=result) as add:
            body = self.client.post("/api/jumbo/cart", json={"items": [{"query": "eieren", "quantity": 12, "unit": "stuks"}]}).get_json()
        self.assertEqual(add.call_args.args[0][0]["quantity"], 2)
        self.assertEqual((body["added"], body["skipped"]), (1, 0))

    def test_cart_reports_failed_and_missing_products(self):
        result = {"added": [], "failed": [{"productId": "9", "error": "x"}], "cart": None}
        with mock.patch.object(jumbo, "search_product", return_value=None), \
                mock.patch.object(jumbo, "add_to_cart", return_value=result):
            body = self.client.post("/api/jumbo/cart", json={"items": [
                {"query": "iets", "quantity": 1}, {"query": "zout", "productId": "9", "quantity": 1}]}).get_json()
        self.assertEqual(body["skippedItems"], ["iets", "zout"])

    def test_cart_with_nothing_found_is_404(self):
        with mock.patch.object(jumbo, "search_product", return_value=None), \
                mock.patch.object(jumbo, "add_to_cart", side_effect=AssertionError("niets toevoegen")):
            self.assertEqual(self.client.post("/api/jumbo/cart", json={"items": [{"query": "iets"}]}).status_code, 404)

    def test_empty_requests_are_400(self):
        for path in ("/api/jumbo/cart", "/api/jumbo/cart/preview"):
            self.assertEqual(self.client.post(path, json={"items": []}).status_code, 400, path)


class ExploreTest(TempDataDirTest):
    def test_the_report_starts_with_reachability_and_contains_no_data(self):
        self.link()
        jumbo._access_cache.update(token="acc", expires_at=jumbo.time.time() + 3600)
        blocked = mock.Mock(status_code=403, headers={"content-type": "text/html"})
        found = [{"id": "SECRET-SKU", "title": "Geheime Melk", "price": 1.23, "unitSize": "1 l", "image": None, "isBonus": False, "url": None}]
        with mock.patch.object(jumbo_explore.requests, "request", return_value=blocked), \
                mock.patch.object(jumbo, "get_access_token", return_value="acc"), \
                mock.patch.object(jumbo, "search_products", return_value=found), \
                mock.patch.object(jumbo, "get_cart", return_value={"items": [], "totalCount": 0, "totalPrice": None}), \
                mock.patch.object(jumbo, "_graphql", return_value={"addBasketLines": {"__typename": "AddBasketLinesError"}}):
            report = jumbo_explore.explore()
        names = [p["name"] for p in report["probes"]]
        self.assertTrue(names[0].startswith("bereikbaar"))
        self.assertTrue(report["probes"][0]["info"]["looksLikeBotBlock"])
        self.assertTrue(report["linked"])
        dumped = json.dumps(report)
        for secret in ("SECRET-SKU", "Geheime Melk", "1.23"):
            self.assertNotIn(secret, dumped)
        self.assertIn("AddBasketLinesError", dumped)  # een typenaam, geen persoonlijk gegeven

    def test_the_empty_add_probe_never_adds_anything(self):
        with mock.patch.object(jumbo, "_graphql", return_value={"addBasketLines": {"__typename": "Basket"}}) as graphql:
            result = jumbo_explore._empty_add_probe()
        self.assertEqual(graphql.call_args.args[1], {"input": {"lines": [], "type": "ECOMMERCE"}})
        self.assertEqual(result, {"typename": "Basket"})

    def test_the_result_types_are_listed_when_introspection_allows_it(self):
        data = {"__type": {"possibleTypes": [{"name": "Basket"}, {"name": "AddBasketLinesError"}]}}
        with mock.patch.object(jumbo, "_graphql", return_value=data):
            self.assertEqual(jumbo_explore._possible_result_types(), ["Basket", "AddBasketLinesError"])
        with mock.patch.object(jumbo, "_graphql", return_value={"__type": None}):
            self.assertEqual(jumbo_explore._possible_result_types(), [])


if __name__ == "__main__":
    unittest.main()
