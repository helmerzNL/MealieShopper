import json
import os
import tempfile
import unittest
from hashlib import md5
from unittest import mock

os.environ.setdefault("MEALIESHOPPER_DATA_DIR", tempfile.mkdtemp())

from mealieshopper import auth, picnic  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402


def response(data=None, *, status=200, token=None):
    """Een nagebootst `requests`-antwoord."""
    reply = mock.Mock()
    reply.status_code = status
    reply.ok = status < 400
    reply.content = b"" if data is None else json.dumps(data).encode()
    reply.text = "" if data is None else json.dumps(data)
    reply.json.return_value = data
    reply.headers = {picnic.AUTH_HEADER: token} if token else {}
    return reply


def tile(unit_id, name, unit_quantity, cents=199, **extra):
    """Een zoektegel zoals Picnic die in de widgetboom zet."""
    unit = {"id": unit_id, "name": name, "display_price": cents, "image_id": f"img{unit_id}",
            "unit_quantity": unit_quantity, "decorators": [], **extra}
    return {"type": "SELLING_UNIT_TILE", "sellingUnit": unit}


def search_page(*tiles):
    return {"body": {"child": {"children": [{"content": list(tiles)}]}}}


class TempDataDirTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"MEALIESHOPPER_DATA_DIR": tmp.name, "PASSKEY_AUTH_ENABLED": "false"})
        patcher.start()
        self.addCleanup(patcher.stop)
        auth.init_db()


class SearchParsingTest(unittest.TestCase):
    def test_products_come_out_of_the_widget_tree(self):
        page = search_page(tile("s1", "Halfvolle melk", "1 l", 129), tile("s2", "Eieren", "10 stuks", 249))
        products = picnic.products_from_search_page(page)
        self.assertEqual([p["id"] for p in products], ["s1", "s2"])
        self.assertEqual(products[0]["price"], 1.29)
        self.assertEqual(products[0]["unitSize"], "1 l")
        self.assertIn("img", products[0]["image"])

    def test_duplicates_and_nameless_tiles_are_dropped(self):
        page = search_page(tile("s1", "Melk", "1 l"), tile("s1", "Melk", "1 l"), tile("s3", None, "1 l"))
        self.assertEqual([p["id"] for p in picnic.products_from_search_page(page)], ["s1"])

    def test_promotions_are_flagged(self):
        promo = tile("s1", "Kaas", "400 g", decorators=[{"type": "PROMOTION_LABEL"}])
        plain = tile("s2", "Brood", "800 g")
        products = picnic.products_from_search_page(search_page(promo, plain))
        self.assertEqual([p["isBonus"] for p in products], [True, False])

    def test_unexpected_pages_give_no_products(self):
        for page in (None, {}, {"body": None}, [], "x"):
            self.assertEqual(picnic.products_from_search_page(page), [])


class CandidateTest(unittest.TestCase):
    def test_same_shape_as_the_ah_candidates(self):
        product = {"id": "s1", "title": "Eieren", "unitSize": "10 stuks", "price": 2.49,
                   "image": "https://img", "isBonus": False, "maxCount": None}
        found = picnic.candidate(product, 12, "stuks")
        self.assertEqual(found["productId"], "s1")
        self.assertEqual(found["packs"], 2)
        self.assertIsNone(found["doubt"])
        self.assertIsNone(found["url"])
        for key in ("title", "brand", "unitSize", "price", "image", "isBonus", "doubt"):
            self.assertIn(key, found)

    def test_unknown_pack_size_is_a_doubt(self):
        found = picnic.candidate({"id": "s1", "title": "Zout", "unitSize": None, "price": 1.0}, 500, "g")
        self.assertEqual(found["packs"], 1)
        self.assertTrue(found["doubt"])

    def test_max_count_caps_the_packs(self):
        product = {"id": "s1", "title": "Melk", "unitSize": "1 l", "price": 1.0, "maxCount": 3}
        self.assertEqual(picnic.candidate(product, 10, "liter")["packs"], 3)


class LoginTest(TempDataDirTest):
    def test_password_is_sent_as_md5_and_never_stored(self):
        with mock.patch.object(picnic.requests, "request", return_value=response({}, token="key1")) as call:
            result = picnic.login("jan@example.com", "geheim")
        body = call.call_args.kwargs["json"]
        self.assertEqual(body, {"key": "jan@example.com", "secret": md5(b"geheim").hexdigest(), "client_id": 30100})
        self.assertEqual(result, {"connected": True, "twoFactorRequired": False})
        self.assertEqual(picnic.configured_auth_key(), "key1")

    def test_two_factor_flow(self):
        login = response({"second_factor_authentication_required": True}, token="pending")
        with mock.patch.object(picnic.requests, "request", return_value=login):
            result = picnic.login("jan@example.com", "geheim")
        self.assertEqual(result, {"connected": False, "twoFactorRequired": True})
        self.assertEqual(picnic.configured_auth_key(), "")
        self.assertTrue(picnic.status()["twoFactorPending"])

        with mock.patch.object(picnic.requests, "request", return_value=response(None, status=204)) as call:
            picnic.request_2fa_code("sms")
        self.assertEqual(call.call_args.kwargs["json"], {"channel": "SMS"})
        self.assertEqual(call.call_args.kwargs["headers"][picnic.AUTH_HEADER], "pending")

        with mock.patch.object(picnic.requests, "request", return_value=response(None, status=204, token="final")) as call:
            self.assertEqual(picnic.verify_2fa(" 123456 "), {"connected": True})
        self.assertEqual(call.call_args.kwargs["json"], {"otp": "123456"})
        self.assertEqual(picnic.configured_auth_key(), "final")
        self.assertFalse(picnic.status()["twoFactorPending"])

    def test_verify_keeps_the_pending_key_when_picnic_sends_no_new_one(self):
        login = response({"second_factor_authentication_required": True}, token="pending")
        with mock.patch.object(picnic.requests, "request", return_value=login):
            picnic.login("jan@example.com", "geheim")
        with mock.patch.object(picnic.requests, "request", return_value=response(None, status=204)):
            picnic.verify_2fa("123456")
        self.assertEqual(picnic.configured_auth_key(), "pending")

    def test_wrong_code_is_reported(self):
        login = response({"second_factor_authentication_required": True}, token="pending")
        with mock.patch.object(picnic.requests, "request", return_value=login):
            picnic.login("jan@example.com", "geheim")
        bad = response({"error": {"code": "TWO_FA_INVALID", "message": "Invalid code"}})
        with mock.patch.object(picnic.requests, "request", return_value=bad):
            with self.assertRaises(picnic.Picnic2FAError):
                picnic.verify_2fa("000000")
        self.assertEqual(picnic.configured_auth_key(), "")

    def test_verify_without_a_login_in_progress_fails(self):
        with self.assertRaises(RuntimeError):
            picnic.verify_2fa("123456")

    def test_a_stale_pending_login_expires(self):
        login = response({"second_factor_authentication_required": True}, token="pending")
        with mock.patch.object(picnic.requests, "request", return_value=login):
            picnic.login("jan@example.com", "geheim")
        later = mock.patch.object(picnic.time, "time", return_value=picnic.time.time() + picnic.PENDING_TTL_SECONDS + 5)
        with later, self.assertRaises(RuntimeError):
            picnic.verify_2fa("123456")

    def test_refused_credentials_are_explained(self):
        bad = response({"error": {"code": "AUTH_INVALID_CRED", "message": "nope"}}, status=401)
        with mock.patch.object(picnic.requests, "request", return_value=bad):
            with self.assertRaises(picnic.PicnicAuthError):
                picnic.login("jan@example.com", "fout")
        self.assertEqual(picnic.configured_auth_key(), "")

    def test_missing_fields_are_rejected(self):
        for args in (("", "x"), ("a@b.nl", "")):
            with self.assertRaises(ValueError):
                picnic.login(*args)

    def test_unknown_2fa_channel_is_rejected(self):
        with self.assertRaises(ValueError):
            picnic.request_2fa_code("pigeon")

    def test_unlink_forgets_everything(self):
        picnic.save_auth_key("key")
        self.assertEqual(picnic.unlink(), {"connected": False})
        self.assertEqual(picnic.configured_auth_key(), "")


class AuthenticatedCallTest(TempDataDirTest):
    def test_unlinked_account_is_refused_without_a_request(self):
        with mock.patch.object(picnic.requests, "request", side_effect=AssertionError("geen verzoek")):
            with self.assertRaises(RuntimeError):
                picnic.get_cart()

    def test_rotated_key_is_saved(self):
        picnic.save_auth_key("old")
        with mock.patch.object(picnic.requests, "request", return_value=response({"items": []}, token="new")):
            picnic.get_cart()
        self.assertEqual(picnic.configured_auth_key(), "new")

    def test_expired_key_asks_to_link_again(self):
        picnic.save_auth_key("old")
        bad = response({"error": {"code": "AUTH_ERROR", "message": "expired"}}, status=401)
        with mock.patch.object(picnic.requests, "request", return_value=bad):
            with self.assertRaises(picnic.PicnicAuthError):
                picnic.get_cart()

    def test_http_errors_carry_status_and_body(self):
        picnic.save_auth_key("key")
        with mock.patch.object(picnic.requests, "request", return_value=response({"x": 1}, status=500)):
            with self.assertRaisesRegex(RuntimeError, "^500:"):
                picnic.get_cart()

    def test_status_verify_reports_a_dead_link(self):
        picnic.save_auth_key("key")
        bad = response({"error": {"code": "AUTH_ERROR"}}, status=401)
        with mock.patch.object(picnic.requests, "request", return_value=bad):
            result = picnic.status(verify=True)
        self.assertEqual((result["connected"], result["verified"]), (True, False))
        self.assertNotIn("key", json.dumps(result))

    def test_country_code_falls_back_to_nl(self):
        with mock.patch.dict(os.environ, {"PICNIC_COUNTRY_CODE": "xx"}):
            self.assertIn(".nl.", picnic.base_url())
        with mock.patch.dict(os.environ, {"PICNIC_COUNTRY_CODE": "de"}):
            self.assertIn(".de.", picnic.base_url())


class CartTest(TempDataDirTest):
    CART = {
        "total_count": 3, "total_price": 450,
        "items": [
            {"id": "l1", "display_price": 300, "items": [
                {"id": "s1", "name": "Melk", "unit_quantity": "1 l", "image_ids": ["i1"],
                 "decorators": [{"type": "QUANTITY", "quantity": 2}]}]},
            {"id": "l2", "price": 150, "items": [{"id": "s2", "name": "Brood", "unit_quantity": "800 g"}]},
        ],
    }

    def test_cart_summary(self):
        summary = picnic.cart_summary(self.CART)
        self.assertEqual(summary["totalPrice"], 4.5)
        self.assertEqual([(i["id"], i["quantity"], i["price"]) for i in summary["items"]],
                         [("s1", 2, 3.0), ("s2", 1, 1.5)])

    def test_cart_summary_has_checkout_total_and_savings(self):
        summary = picnic.cart_summary({**self.CART, "checkout_total_price": 695, "membership_savings": 120})
        self.assertEqual((summary["checkoutTotal"], summary["savings"]), (6.95, 1.2))
        self.assertIsNone(picnic.cart_summary(self.CART)["checkoutTotal"])

    def test_odd_carts_do_not_crash(self):
        for cart in (None, {}, {"items": [None, {"items": None}]}):
            self.assertIn("items", picnic.cart_summary(cart))


def cart_with(**quantities):
    """Een mandje met de gegeven producten (id -> aantal), zoals Picnic het teruggeeft."""
    return {"total_count": sum(quantities.values()), "items": [
        {"id": f"l{pid}", "items": [{"id": pid, "name": pid.upper(),
                                     "decorators": [{"type": "QUANTITY", "quantity": qty}]}]}
        for pid, qty in quantities.items()]}


def paths_and_bodies(call):
    return [(c.args[1].rsplit("/api/15", 1)[1], c.kwargs.get("json")) for c in call.call_args_list]


class RealCartShapeTest(unittest.TestCase):
    """De vorm van een gevuld mandje, zoals de verkenner hem meldde (veldnamen, geen echte waarden)."""

    def line(self, pid, display_price, price, quantity=1, max_count=10):
        return {"id": f"l{pid}", "display_price": display_price, "price": price, "type": "ORDER_LINE",
                "decorators": [{"type": "PRICE", "display_price": display_price, "text": "x"}],
                "items": [{"id": pid, "name": pid.upper(), "price": 100, "unit_quantity": "1 st", "max_count": max_count,
                           "image_ids": ["i"], "decorators": [{"type": "QUANTITY", "quantity": quantity}]}]}

    def cart(self):
        return {"total_count": 3, "total_price": 480, "checkout_total_price": 480, "membership_savings": 70,
                "analytics_context_data": {"items_list": [
                    {"product_id": "s1", "is_available": True, "is_in_recipe": False, "quantity": 2},
                    {"product_id": "s2", "is_available": False, "is_in_recipe": False, "quantity": 1}]},
                "items": [self.line("s1", 300, 300, quantity=2, max_count=8), self.line("s2", 250, 180)]}

    def test_the_paid_price_is_the_lower_one_and_the_other_is_the_list_price(self):
        first, second = picnic.cart_summary(self.cart())["items"]
        self.assertEqual((first["price"], first["listPrice"]), (3.0, None))
        self.assertEqual((second["price"], second["listPrice"]), (1.8, 2.5))

    def test_it_does_not_matter_which_field_holds_the_discount(self):
        cart = self.cart()
        cart["items"][1]["display_price"], cart["items"][1]["price"] = 180, 250
        second = picnic.cart_summary(cart)["items"][1]
        self.assertEqual((second["price"], second["listPrice"]), (1.8, 2.5))

    def test_lines_add_up_to_the_total(self):
        summary = picnic.cart_summary(self.cart())
        self.assertAlmostEqual(sum(i["price"] for i in summary["items"]), summary["totalPrice"], places=2)

    def test_availability_and_max_count_are_read(self):
        first, second = picnic.cart_summary(self.cart())["items"]
        self.assertEqual((first["available"], second["available"]), (True, False))
        self.assertEqual((first["maxCount"], second["maxCount"]), (8, 10))

    def test_unknown_availability_is_none_not_unavailable(self):
        cart = self.cart()
        del cart["analytics_context_data"]
        self.assertEqual([i["available"] for i in picnic.cart_summary(cart)["items"]], [None, None])

    def test_odd_values_do_not_crash(self):
        cart = self.cart()
        cart["items"][0]["price"] = "x"
        cart["items"][1]["display_price"] = None
        cart["items"][1]["price"] = None
        cart["items"][0]["items"][0]["max_count"] = "veel"
        cart["analytics_context_data"] = {"items_list": [None, {"product_id": "s1"}, "x"]}
        items = picnic.cart_summary(cart)["items"]
        self.assertEqual((items[0]["price"], items[1]["price"], items[0]["maxCount"]), (3.0, None, None))


class CartEditingTest(TempDataDirTest):
    def setUp(self):
        super().setUp()
        picnic.save_auth_key("key")

    def test_raising_a_quantity_adds_the_difference(self):
        replies = [response(cart_with(s1=2)), response(cart_with(s1=5))]
        with mock.patch.object(picnic.requests, "request", side_effect=replies) as call:
            cart = picnic.set_quantity("s1", 5)
        self.assertEqual(paths_and_bodies(call), [("/cart", None), ("/cart/add_product", {"product_id": "s1", "count": 3})])
        self.assertEqual(cart["items"][0]["quantity"], 5)

    def test_lowering_a_quantity_removes_the_difference(self):
        replies = [response(cart_with(s1=4)), response(cart_with(s1=1))]
        with mock.patch.object(picnic.requests, "request", side_effect=replies) as call:
            picnic.set_quantity("s1", 1)
        self.assertEqual(paths_and_bodies(call)[1], ("/cart/remove_product", {"product_id": "s1", "count": 3}))

    def test_zero_removes_the_whole_line(self):
        replies = [response(cart_with(s1=3)), response(cart_with())]
        with mock.patch.object(picnic.requests, "request", side_effect=replies) as call:
            cart = picnic.set_quantity("s1", 0)
        self.assertEqual(paths_and_bodies(call)[1], ("/cart/remove_product", {"product_id": "s1", "count": 3}))
        self.assertEqual(cart["items"], [])

    def test_more_than_picnic_allows_is_refused_before_anything_is_added(self):
        cart = cart_with(s1=2)
        cart["items"][0]["items"][0]["max_count"] = 5
        with mock.patch.object(picnic.requests, "request", return_value=response(cart)) as call:
            with self.assertRaisesRegex(ValueError, "maximaal 5"):
                picnic.set_quantity("s1", 6)
            self.assertEqual(picnic.set_quantity("s1", 5)["items"][0]["id"], "s1")
        self.assertNotIn("/cart/add_product", [p for p, _ in paths_and_bodies(call)][:1])

    def test_an_unchanged_quantity_changes_nothing(self):
        with mock.patch.object(picnic.requests, "request", return_value=response(cart_with(s1=2))) as call:
            picnic.set_quantity("s1", 2)
        self.assertEqual(call.call_count, 1)  # alleen het mandje lezen

    def test_a_product_that_is_not_in_the_cart_counts_as_zero(self):
        replies = [response(cart_with(s2=1)), response(cart_with(s2=1, s1=2))]
        with mock.patch.object(picnic.requests, "request", side_effect=replies) as call:
            picnic.set_quantity("s1", 2)
        self.assertEqual(paths_and_bodies(call)[1], ("/cart/add_product", {"product_id": "s1", "count": 2}))

    def test_bad_input_is_rejected_before_any_request(self):
        with mock.patch.object(picnic.requests, "request", side_effect=AssertionError("geen verzoek")):
            for product, quantity in (("", 1), ("s1", -1), ("s1", 100), ("s1", "veel"), ("s1", None),
                                      ("s1", 1.5), ("s1", True)):
                with self.assertRaises(ValueError, msg=repr((product, quantity))):
                    picnic.set_quantity(product, quantity)

    def test_clear_uses_picnics_route_and_counts_what_was_removed(self):
        replies = [response(cart_with(s1=2, s2=1)), response(cart_with())]
        with mock.patch.object(picnic.requests, "request", side_effect=replies) as call:
            result = picnic.clear_cart()
        self.assertEqual([p for p, _ in paths_and_bodies(call)], ["/cart", "/cart/clear"])
        self.assertEqual((result["removed"], result["cart"]["items"]), (3, []))

    def test_clear_falls_back_to_removing_each_product(self):
        replies = [response(cart_with(s1=2, s2=1)), response({"x": 1}, status=404),
                   response(cart_with(s2=1)), response(cart_with())]
        with mock.patch.object(picnic.requests, "request", side_effect=replies) as call:
            result = picnic.clear_cart()
        self.assertEqual(paths_and_bodies(call)[2:], [
            ("/cart/remove_product", {"product_id": "s1", "count": 2}),
            ("/cart/remove_product", {"product_id": "s2", "count": 1})])
        self.assertEqual((result["removed"], result["cart"]["items"]), (3, []))

    def test_clearing_an_empty_cart_is_a_no_op(self):
        with mock.patch.object(picnic.requests, "request", return_value=response(cart_with())) as call:
            self.assertEqual(picnic.clear_cart()["removed"], 0)
        self.assertEqual(call.call_count, 1)

    def test_a_dead_link_is_not_hidden_by_the_fallback(self):
        bad = response({"error": {"code": "AUTH_ERROR"}}, status=401)
        with mock.patch.object(picnic.requests, "request", side_effect=[response(cart_with(s1=1)), bad]):
            with self.assertRaises(picnic.PicnicAuthError):
                picnic.clear_cart()

    def test_nothing_here_places_an_order(self):
        # Alleen de mandje-routes; geen checkout of bestelroute.
        with mock.patch.object(picnic.requests, "request", side_effect=[response(cart_with(s1=1)), response(cart_with())]) as call:
            picnic.set_quantity("s1", 0)
        with mock.patch.object(picnic.requests, "request", side_effect=[response(cart_with(s1=1)), response(cart_with())]) as other:
            picnic.clear_cart()
        for path, _ in paths_and_bodies(call) + paths_and_bodies(other):
            self.assertTrue(path.startswith("/cart"), path)
            self.assertNotIn("checkout", path)


class DeliverySlotsTest(TempDataDirTest):
    CART = {
        "selected_slot": {"slot_id": "s2", "state": "IMPLICIT"},
        "delivery_slots": [
            {"slot_id": "s2", "window_start": "2026-10-23T17:00:00", "window_end": "2026-10-23T18:00:00",
             "cut_off_time": "2026-10-22T23:00:00", "is_available": True, "reserved": False,
             "selected": False, "minimum_order_value": 3500, "hub_id": "h", "fc_id": "f"},
            {"slot_id": "s1", "window_start": "2026-10-22T17:00:00", "window_end": "2026-10-22T18:00:00",
             "is_available": False, "reserved": False, "selected": False, "minimum_order_value": 0},
            {"slot_id": "s3", "window_start": "2026-10-24T09:00:00", "window_end": "2026-10-24T10:00:00",
             "is_available": True, "reserved": True, "selected": True, "minimum_order_value": 3500},
            {"slot_id": None, "window_start": "x"},
            None,
        ],
    }

    def test_slots_are_sorted_and_mapped(self):
        slots = picnic.slots_from_cart(self.CART)
        self.assertEqual([s["id"] for s in slots], ["s1", "s2", "s3"])
        self.assertEqual(slots[1]["minimumOrder"], 35.0)
        self.assertEqual(slots[1]["cutOff"], "2026-10-22T23:00:00")
        self.assertTrue(slots[1]["selected"])  # gekozen via selected_slot
        self.assertTrue(slots[2]["selected"] and slots[2]["reserved"])
        self.assertNotIn("h", json.dumps(slots))  # hub- en magazijn-id's blijven eruit

    def test_odd_carts_give_no_slots(self):
        for cart in (None, {}, {"delivery_slots": None}, {"delivery_slots": "x"}, []):
            self.assertEqual(picnic.slots_from_cart(cart), [])

    def test_only_available_slots_are_listed_by_default(self):
        picnic.save_auth_key("key")
        with mock.patch.object(picnic.requests, "request", return_value=response(self.CART)) as call:
            slots = picnic.get_delivery_slots(limit=1)
        self.assertEqual(call.call_args.args[:2], ("GET", picnic.base_url() + "/cart"))
        self.assertEqual([s["id"] for s in slots], ["s2"])
        with mock.patch.object(picnic.requests, "request", return_value=response(self.CART)):
            self.assertEqual(len(picnic.get_delivery_slots(limit=10, available_only=False)), 3)

    def test_add_to_cart_clamps_quantities_and_never_orders(self):
        picnic.save_auth_key("key")
        with mock.patch.object(picnic.requests, "request", return_value=response(self.CART)) as call:
            result = picnic.add_to_cart([{"productId": "s1", "quantity": 500}, {"productId": "s2", "quantity": 0}])
        paths = {c.args[1].rsplit("/api/15", 1)[1] for c in call.call_args_list}
        self.assertEqual(paths, {"/cart/add_product"})
        self.assertEqual([c.kwargs["json"] for c in call.call_args_list],
                         [{"product_id": "s1", "count": 99}, {"product_id": "s2", "count": 1}])
        self.assertEqual(len(result["added"]), 2)
        self.assertEqual(result["failed"], [])

    def test_one_failing_product_does_not_stop_the_rest(self):
        picnic.save_auth_key("key")
        replies = [response({"x": 1}, status=500), response(self.CART)]
        with mock.patch.object(picnic.requests, "request", side_effect=replies):
            result = picnic.add_to_cart([{"productId": "s1", "quantity": 1}, {"productId": "s2", "quantity": 1}])
        self.assertEqual([a["productId"] for a in result["added"]], ["s2"])
        self.assertEqual([f["productId"] for f in result["failed"]], ["s1"])

    def test_a_dead_link_aborts_the_whole_add(self):
        picnic.save_auth_key("key")
        bad = response({"error": {"code": "AUTH_ERROR"}}, status=401)
        with mock.patch.object(picnic.requests, "request", return_value=bad):
            with self.assertRaises(picnic.PicnicAuthError):
                picnic.add_to_cart([{"productId": "s1", "quantity": 1}])

    def test_deliveries(self):
        picnic.save_auth_key("key")
        data = [{"delivery_id": "d1", "status": "CURRENT", "slot": {"window_start": "a", "window_end": "b"},
                 "eta2": {"start": "c", "end": "d"}, "orders": [{"checkout_total_price": 2500}]}]
        with mock.patch.object(picnic.requests, "request", return_value=response(data)) as call:
            deliveries = picnic.get_deliveries(current_only=True)
        self.assertEqual(call.call_args.kwargs["json"], ["CURRENT"])
        self.assertEqual(deliveries[0]["id"], "d1")
        self.assertEqual(deliveries[0]["total"], 25.0)
        self.assertNotIn("address", json.dumps(deliveries))


class ExploreTest(TempDataDirTest):
    def test_report_lists_more_routes_and_contains_no_values(self):
        from mealieshopper import picnic_explore
        page = search_page(tile("s1", "Geheime melk van Jan", "1 l", 129))
        replies = {"/user": {"firstname": "Jan", "address": {"street": "Hoofdstraat"}}}

        def fake(method, path, body=None, text=False):
            if path.startswith("/pages/search-page-results"):
                return page
            if path == "/lists":
                raise RuntimeError("404: niet gevonden")
            return replies.get(path, {})

        picnic.save_auth_key("key")
        with mock.patch.object(picnic, "_authed", side_effect=fake):
            report = picnic_explore.explore()
        names = [probe["name"] for probe in report["probes"]]
        self.assertTrue(any("product-details-page-root" in name for name in names))
        self.assertFalse(next(p for p in report["probes"] if "lists" in p["name"])["ok"])
        for secret in ("Jan", "Hoofdstraat", "Geheime", "s1"):
            self.assertNotIn(secret, json.dumps(report), secret)


class RecipeExploreTest(TempDataDirTest):
    TILE = {"type": "TILE", "analytics": {"contexts": [{"schema": "iglu:x/recipe/1", "data": {"selling_group_id": "abc123def456", "rank": 1}}]},
            "pml": {"component": {"text": "Geheime pasta van Jan"}}}

    def page(self):
        return {"body": {"children": [self.TILE, {"type": "TEXT", "markdown": "Geheim"}], "link": "picnic://page?page_id=favorites-page-root&user=jan"}}

    def test_recipe_tiles_are_found_by_their_analytics_context(self):
        from mealieshopper import picnic_explore
        self.assertEqual(picnic_explore.recipe_tiles(self.page()), [self.TILE])
        self.assertEqual(picnic_explore.recipe_tiles({"body": []}), [])
        self.assertEqual(picnic_explore.recipe_tiles("tekst"), [])

    def test_links_are_reduced_to_page_names_and_parameter_names(self):
        from mealieshopper import picnic_explore
        links = picnic_explore.page_links({"a": "open /pages/recipe-details?selling_group_id=abc123def456&portions=4 nu",
                                           "b": "picnic://deeplink/saved?token=geheim"})
        self.assertEqual(links, ["/pages/recipe-details?portions&selling_group_id", "picnic://deeplink/saved?token"])
        self.assertEqual(picnic_explore.page_links("x /pages/item-9876543210"), ["/pages/item-<id>"])

    def test_the_report_finds_recipes_favorites_and_leaks_nothing(self):
        from mealieshopper import picnic_explore
        tile_page, calls = self.page(), []

        def fake(method, path, body=None, text=False):
            calls.append(path)
            if path.startswith("/pages/search-page-results"):
                return search_page(tile("s1", "Melk", "1 l"))
            if path == "/pages/saved-deep-dive-page-content":
                return tile_page
            if path.startswith("/pages/selling-group-details-page"):
                return "RSC-tekst met SELLING_GROUP en Geheime pasta"
            if path == "/pages/favorites-deep-dive-page-content":
                return {"body": {"children": []}}
            if path.startswith("/pages/") or path in ("/lists", "/my_store"):
                raise RuntimeError("404: {\"error\": \"geheim\"}")
            return {}

        picnic.save_auth_key("key")
        with mock.patch.object(picnic, "_authed", side_effect=fake):
            report = picnic_explore.explore()
        names = {probe["name"]: probe for probe in report["probes"]}
        self.assertTrue(names["opgeslagen recepten: GET pages/saved-deep-dive-page-content"]["ok"])
        self.assertEqual(names["opgeslagen recepten: GET pages/saved-deep-dive-page-content"]["info"]["receptonderdelen"], 1)
        self.assertTrue(any("receptonderdeel" in name for name in names))
        self.assertIn("/pages/selling-group-details-page?selling_group_id=abc123def456", calls)
        details = names["receptdetails: GET pages/selling-group-details-page"]["info"]
        self.assertEqual((details["formaat"], details["noemt SELLING_GROUP"]), ("tekst (geen JSON)", True))
        self.assertTrue(names["favorieten? GET pages/favorites-deep-dive-page-content"]["ok"])
        self.assertEqual(names["favorieten? GET pages/favorites-page-root"]["error"], "404")
        self.assertEqual(picnic_explore.short_error('404: {"error": {"code": "NOT_FOUND", "message": "x"}}'), "404 NOT_FOUND")
        self.assertEqual(picnic_explore.short_error("netwerkfout: Timeout"), "netwerkfout: Timeout")
        for secret in ("Geheime", "geheim", "abc123def456", "jan"):
            self.assertNotIn(secret, json.dumps(report, ensure_ascii=False), secret)


class PicnicRoutesTest(TempDataDirTest):
    def setUp(self):
        super().setUp()
        self.client = create_app().test_client()

    def test_search_route(self):
        found = [{"id": "s1", "title": "Melk"}]
        with mock.patch.object(picnic, "search_products", return_value=found):
            body = self.client.get("/api/picnic/search?q=melk").get_json()
        self.assertEqual(body, {"products": found})
        self.assertEqual(self.client.get("/api/picnic/search").status_code, 400)

    def test_preview_uses_picnic_candidates(self):
        products = [{"id": "s1", "title": "Eieren", "unitSize": "10 stuks", "price": 2.49, "image": None}]
        with mock.patch.object(picnic, "search_products", return_value=products):
            response_ = self.client.post("/api/picnic/cart/preview", json={"items": [
                {"query": "eieren", "quantity": 12, "unit": "stuks"}]})
        candidate = response_.get_json()["items"][0]["candidates"][0]
        self.assertEqual((candidate["productId"], candidate["packs"]), ("s1", 2))

    def test_preview_reports_a_failed_search_per_line(self):
        with mock.patch.object(picnic, "search_products", side_effect=RuntimeError("stuk")):
            body = self.client.post("/api/picnic/cart/preview", json={"items": [{"query": "melk"}]}).get_json()
        self.assertEqual(body["items"][0]["error"], "stuk")
        self.assertEqual(body["items"][0]["candidates"], [])

    def test_cart_searches_and_adds(self):
        product = {"id": "s1", "title": "Eieren", "unitSize": "10 stuks", "price": 2.49}
        result = {"added": [{"productId": "s1", "quantity": 2}], "failed": [], "cart": {"items": []}}
        with mock.patch.object(picnic, "search_product", return_value=product), \
                mock.patch.object(picnic, "add_to_cart", return_value=result) as add:
            body = self.client.post("/api/picnic/cart", json={"items": [
                {"query": "eieren", "quantity": 12, "unit": "stuks"}]}).get_json()
        self.assertEqual(add.call_args.args[0][0]["quantity"], 2)
        self.assertEqual((body["added"], body["skipped"]), (1, 0))

    def test_cart_skips_products_that_are_not_found_or_fail(self):
        result = {"added": [], "failed": [{"productId": "s9", "error": "x"}], "cart": None}
        with mock.patch.object(picnic, "search_product", return_value=None), \
                mock.patch.object(picnic, "add_to_cart", return_value=result):
            body = self.client.post("/api/picnic/cart", json={"items": [
                {"query": "iets", "quantity": 1}, {"query": "zout", "productId": "s9", "quantity": 1}]}).get_json()
        self.assertEqual(body["skippedItems"], ["iets", "zout"])

    def test_cart_with_nothing_found_is_404_and_adds_nothing(self):
        with mock.patch.object(picnic, "search_product", return_value=None), \
                mock.patch.object(picnic, "add_to_cart", side_effect=AssertionError("niets toevoegen")):
            response_ = self.client.post("/api/picnic/cart", json={"items": [{"query": "iets"}]})
        self.assertEqual(response_.status_code, 404)

    def test_confirmed_products_skip_the_search(self):
        result = {"added": [{"productId": "s7", "quantity": 4}], "failed": [], "cart": None}
        with mock.patch.object(picnic, "search_product", side_effect=AssertionError("niet zoeken")), \
                mock.patch.object(picnic, "add_to_cart", return_value=result) as add:
            self.client.post("/api/picnic/cart", json={"items": [{"query": "melk", "productId": "s7", "quantity": 4}]})
        self.assertEqual(add.call_args.args[0], [{"productId": "s7", "quantity": 4, "query": "melk"}])

    def test_empty_requests_are_400(self):
        for path in ("/api/picnic/cart", "/api/picnic/cart/preview"):
            self.assertEqual(self.client.post(path, json={"items": []}).status_code, 400, path)

    def test_quantity_route(self):
        cart = {"items": [], "totalCount": 0}
        with mock.patch.object(picnic, "set_quantity", return_value=cart) as setq:
            body = self.client.put("/api/picnic/cart/items/s1", json={"quantity": 3}).get_json()
        setq.assert_called_once_with("s1", 3)
        self.assertEqual(body, cart)
        with mock.patch.object(picnic, "set_quantity", side_effect=ValueError("Het aantal moet een heel getal zijn")):
            reply = self.client.put("/api/picnic/cart/items/s1", json={"quantity": "x"})
        self.assertEqual((reply.status_code, reply.get_json()["error"]), (400, "Het aantal moet een heel getal zijn"))
        with mock.patch.object(picnic, "set_quantity", side_effect=RuntimeError("500: stuk")):
            self.assertEqual(self.client.put("/api/picnic/cart/items/s1", json={"quantity": 1}).status_code, 424)

    def test_clear_route_needs_confirmation(self):
        with mock.patch.object(picnic, "clear_cart", side_effect=AssertionError("niet zonder bevestiging")):
            for body in ({}, {"confirm": "yes"}, {"confirm": 1}, {"confirm": False}):
                self.assertEqual(self.client.post("/api/picnic/cart/clear", json=body).status_code, 400, body)
        with mock.patch.object(picnic, "clear_cart", return_value={"removed": 2, "cart": {"items": []}}) as clear:
            body = self.client.post("/api/picnic/cart/clear", json={"confirm": True}).get_json()
        clear.assert_called_once_with()
        self.assertEqual(body["removed"], 2)

    def test_slots_route(self):
        slots = [{"id": "s1", "available": True}]
        with mock.patch.object(picnic, "get_delivery_slots", return_value=slots) as get:
            body = self.client.get("/api/picnic/slots?limit=3").get_json()
        self.assertEqual(body, {"slots": slots})
        get.assert_called_once_with(3)
        with mock.patch.object(picnic, "get_delivery_slots", side_effect=RuntimeError("500: stuk")):
            self.assertEqual(self.client.get("/api/picnic/slots").status_code, 424)

    def test_user_errors_are_400_and_picnic_errors_are_424(self):
        with mock.patch.object(picnic, "login", side_effect=picnic.PicnicAuthError("fout wachtwoord")):
            self.assertEqual(self.client.post("/api/picnic/auth", json={}).status_code, 400)
        with mock.patch.object(picnic, "get_cart", side_effect=RuntimeError("500: stuk")):
            self.assertEqual(self.client.get("/api/picnic/cart").status_code, 424)

    def test_user_errors_show_our_own_explanation(self):
        bad = response({"error": {"code": "AUTH_INVALID_CRED", "message": "geheim detail van Picnic"}}, status=401)
        with mock.patch.object(picnic.requests, "request", return_value=bad):
            reply = self.client.post("/api/picnic/auth", json={"email": "jan@example.com", "password": "fout"})
        self.assertEqual(reply.status_code, 400)
        self.assertIn("e-mailadres en wachtwoord", reply.get_json()["error"])
        self.assertNotIn("geheim", reply.get_data(as_text=True))

    def test_wrong_code_shows_our_text_not_picnics(self):
        login = response({"second_factor_authentication_required": True}, token="pending")
        with mock.patch.object(picnic.requests, "request", return_value=login):
            picnic.login("jan@example.com", "geheim")
        bad = response({"error": {"code": "TWO_FA_INVALID", "message": "geheim detail van Picnic"}})
        with mock.patch.object(picnic.requests, "request", return_value=bad):
            reply = self.client.post("/api/picnic/auth/2fa/verify", json={"code": "000000"})
        self.assertEqual(reply.status_code, 400)
        self.assertIn("code", reply.get_json()["error"])
        self.assertNotIn("geheim", reply.get_data(as_text=True))

    def test_picnic_failures_stay_generic_for_the_user(self):
        with mock.patch.object(picnic, "get_cart", side_effect=RuntimeError("500: antwoord met geheim")):
            reply = self.client.get("/api/picnic/cart")
        self.assertEqual((reply.status_code, reply.get_json()), (424, {"error": "Picnic fout"}))

    def test_status_check_does_not_echo_picnic_responses(self):
        picnic.save_auth_key("key")
        with mock.patch.object(picnic.requests, "request", return_value=response({"x": "geheim"}, status=500)):
            result = picnic.status(verify=True)
        self.assertEqual(result["error"], "RuntimeError")
        bad = response({"error": {"code": "AUTH_ERROR", "message": "geheim"}}, status=401)
        with mock.patch.object(picnic.requests, "request", return_value=bad):
            result = picnic.status(verify=True)
        self.assertIn("Koppel opnieuw", result["error"])
        self.assertNotIn("geheim", json.dumps(result))

    def test_login_routes_delegate(self):
        with mock.patch.object(picnic, "login", return_value={"connected": False, "twoFactorRequired": True}) as login:
            body = self.client.post("/api/picnic/auth", json={"email": "a@b.nl", "password": "pw"}).get_json()
        login.assert_called_once_with("a@b.nl", "pw")
        self.assertTrue(body["twoFactorRequired"])
        with mock.patch.object(picnic, "request_2fa_code") as request_code:
            self.client.post("/api/picnic/auth/2fa/request", json={"channel": "EMAIL"})
        request_code.assert_called_once_with("EMAIL")
        with mock.patch.object(picnic, "verify_2fa", return_value={"connected": True}) as verify:
            self.client.post("/api/picnic/auth/2fa/verify", json={"code": "123456"})
        verify.assert_called_once_with("123456")


if __name__ == "__main__":
    unittest.main()
