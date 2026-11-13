import os
import tempfile
import unittest
from unittest import mock

import httpx

os.environ.setdefault("MEALIESHOPPER_DATA_DIR", tempfile.mkdtemp())

from helpers import routes, run, sent_json, sent_params, served  # noqa: E402

from mealieshopper import ah  # noqa: E402

GRAPHQL = f"{ah.AH_API_BASE}/graphql"
LISTS_REST = f"{ah.AH_API_BASE}/mobile-services/lists/v3/lists"


class ExtractOAuthCodeTest(unittest.TestCase):
    def test_full_url(self):
        self.assertEqual(ah.extract_oauth_code("appie://login-exit?code=abc123"), "abc123")

    def test_console_line_with_noise(self):
        line = "Failed to launch 'appie://login-exit?code=abc%2B123&state=x' because the protocol"
        self.assertEqual(ah.extract_oauth_code(line), "abc+123")

    def test_quoted_url(self):
        self.assertEqual(ah.extract_oauth_code("'appie://login-exit?code=abc123'"), "abc123")

    def test_bare_code(self):
        self.assertEqual(ah.extract_oauth_code("  abc123\n"), "abc123")


class ExchangeErrorTest(unittest.TestCase):
    def test_rejected_code(self):
        self.assertIn("verlopen of al gebruikt", ah._explain_exchange_error("400: bad code"))

    def test_blocked(self):
        self.assertIn("403", ah._explain_exchange_error("403: <H1>Access Denied</H1>"))

    def test_other_errors_pass_through(self):
        self.assertEqual(ah._explain_exchange_error("500: boom"), "500: boom")


class RotatedRefreshTokenTest(unittest.TestCase):
    def test_new_token_is_saved(self):
        with mock.patch.object(ah, "save_refresh_token") as save:
            run(ah._store_rotated_refresh_token("old", "new"))
        save.assert_called_once_with("new")

    def test_same_or_missing_token_is_ignored(self):
        with mock.patch.object(ah, "save_refresh_token") as save:
            run(ah._store_rotated_refresh_token("old", "old"))
            run(ah._store_rotated_refresh_token("old", None))
        save.assert_not_called()

    def test_get_user_token_persists_rotation(self):
        response = {"access_token": "acc", "refresh_token": "rotated", "expires_in": 3600}
        ah.user_token_cache = None
        with (
            mock.patch.object(ah, "configured_refresh_token", return_value="old"),
            mock.patch.object(ah, "_request_json", return_value=response),
            mock.patch.object(ah, "save_refresh_token") as save,
        ):
            self.assertEqual(run(ah.get_user_token()), "acc")
        save.assert_called_once_with("rotated")
        ah.user_token_cache = None

    def test_verify_token_returns_rotated_token(self):
        with served(httpx.Response(200, json={"refresh_token": "rotated"})) as route:
            result = run(ah.verify_token("old"))
        self.assertEqual(route.calls.last.request.method, "POST")
        self.assertEqual(result, {"ok": True, "type": "refresh", "refreshToken": "rotated", "accessToken": "", "expiresIn": None})


class FavoriteListsTest(unittest.TestCase):
    def test_product_web_url(self):
        self.assertEqual(ah.product_web_url(123), "https://www.ah.nl/producten/product/wi123")
        self.assertEqual(ah.product_web_url("456"), "https://www.ah.nl/producten/product/wi456")
        self.assertEqual(ah.product_web_url(None), "")

    def test_lists_come_from_graphql_with_empty_ids(self):
        graphql = httpx.Response(
            200,
            json={
                "data": {
                    "favoriteListV2": [
                        {"id": "L1", "description": "Favorieten", "totalSize": 3, "updatedAt": "2026-10-01"},
                    ]
                }
            },
        )
        with mock.patch.object(ah, "get_user_token", return_value="tok"), routes() as router:
            post = router.post(GRAPHQL).mock(return_value=graphql)
            get = router.get(LISTS_REST).mock(return_value=httpx.Response(200, json=[]))
            lists = run(ah.get_favorite_lists())
        self.assertEqual(
            lists,
            [{"id": "L1", "name": "Favorieten", "itemCount": 3, "updatedAt": "2026-10-01"}],
        )
        self.assertEqual(sent_json(post.calls.last)["variables"], {"ids": []})
        self.assertFalse(get.called)

    def test_lists_fall_back_to_rest_when_graphql_fails(self):
        graphql = httpx.Response(400, text="boom")
        rest = httpx.Response(200, json=[{"id": "L1", "description": "Favorieten", "itemCount": 3}])
        with mock.patch.object(ah, "get_user_token", return_value="tok"), routes() as router:
            router.post(GRAPHQL).mock(return_value=graphql)
            router.get(LISTS_REST).mock(return_value=rest)
            lists = run(ah.get_favorite_lists())
        self.assertEqual(lists, [{"id": "L1", "name": "Favorieten", "itemCount": 3}])

    def test_lists_report_both_errors_when_everything_fails(self):
        graphql = httpx.Response(200, json={"errors": [{"message": "Cannot query field"}]})
        gone = httpx.Response(404, text='{"error":"not_found"}')
        with mock.patch.object(ah, "get_user_token", return_value="tok"), routes() as router:
            router.post(GRAPHQL).mock(return_value=graphql)
            router.get(LISTS_REST).mock(return_value=gone)
            with self.assertRaises(RuntimeError) as ctx:
                run(ah.get_favorite_lists())
        self.assertIn("Cannot query field", str(ctx.exception))
        self.assertIn("404", str(ctx.exception))

    def test_rest_fallback_retries_with_product_id_zero(self):
        bad = httpx.Response(400, text="bad productId")
        good = httpx.Response(200, json=[])
        with served(side_effect=[bad, good]) as get:
            run(ah._get_favorite_lists_rest("tok", 1))
        self.assertEqual([(c.request.method, sent_params(c)["productId"]) for c in get.calls], [("GET", "1"), ("GET", "0")])

    def test_rest_fallback_does_not_retry_on_auth_error(self):
        denied = httpx.Response(401, text="nope")
        with served(denied) as get:
            with self.assertRaises(RuntimeError) as ctx:
                run(ah._get_favorite_lists_rest("tok", 1))
        self.assertEqual(get.call_count, 1)
        self.assertIn("401", str(ctx.exception))

    def test_items_always_carry_a_product_url(self):
        graphql = httpx.Response(
            200,
            json={
                "data": {
                    "favoriteListV2": [
                        {
                            "id": "L1",
                            "description": "Favorieten",
                            "totalSize": 2,
                            "items": [
                                {"id": "i1", "productId": 10, "quantity": 2},
                                {"id": "i2", "productId": 11, "quantity": 1},
                            ],
                        }
                    ]
                }
            },
        )
        products = [{"id": 10, "url": ah.product_web_url(10), "title": "Melk"}]
        with (
            mock.patch.object(ah, "get_user_token", return_value="tok"),
            served(graphql),
            mock.patch.object(ah, "get_products_by_ids", return_value=products),
        ):
            result = run(ah.get_favorite_list_items("l1"))
        self.assertEqual(result["items"][0]["url"], "https://www.ah.nl/producten/product/wi10")
        self.assertEqual(result["items"][1]["url"], "https://www.ah.nl/producten/product/wi11")
        self.assertIsNone(result["items"][1]["product"])


if __name__ == "__main__":
    unittest.main()
