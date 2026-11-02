"""Het anonieme AH-token: hooguit een uur bewaren en een geweigerd token één keer vervangen ("member not active")."""
import os
import tempfile
import unittest
from datetime import date, timedelta
from unittest import mock

os.environ.setdefault("MEALIESHOPPER_DATA_DIR", tempfile.mkdtemp())

from mealieshopper import ah  # noqa: E402

REJECTED = '{"error_description":"member not active","error":"invalid_grant"}'


class Reply:
    def __init__(self, status=200, data=None, text=None):
        self.status_code = status
        self.ok = 200 <= status < 300
        self._data = data if data is not None else {}
        self.text = text if text is not None else str(self._data)

    def json(self):
        return self._data


class AnonymousTokenTest(unittest.TestCase):
    def setUp(self):
        ah.anon_token_cache = None
        ah.user_token_cache = None
        self.addCleanup(setattr, ah, "anon_token_cache", None)
        self.addCleanup(setattr, ah, "user_token_cache", None)
        self.issued = 0

        def issue(method, url, **kwargs):
            self.assertIn("/auth/token/anonymous", url)
            self.issued += 1
            return Reply(data={"access_token": f"anon-{self.issued}", "refresh_token": "r", "expires_in": 604798})

        patcher = mock.patch.object(ah.requests, "request", side_effect=issue)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_token_is_kept_for_at_most_an_hour_although_ah_says_seven_days(self):
        with mock.patch.object(ah.time, "time", return_value=1000.0):
            self.assertEqual(ah.get_token(), "anon-1")
            self.assertEqual(ah.get_token(), "anon-1")
        self.assertEqual(ah.anon_token_cache.expires_at, 1000.0 + ah.ANON_TOKEN_MAX_AGE)
        with mock.patch.object(ah.time, "time", return_value=1000.0 + ah.ANON_TOKEN_MAX_AGE + 1):
            self.assertEqual(ah.get_token(), "anon-2")

    def test_the_bonus_retries_once_with_a_new_token_when_ah_rejects_the_old_one(self):
        ah.get_token()  # een bewaard token dat AH inmiddels heeft ingetrokken
        seen = []

        def graphql(url, headers, json, timeout):
            seen.append(headers["Authorization"])
            if headers["Authorization"] == "Bearer anon-1":
                return Reply(400, text=REJECTED)
            return Reply(data={"data": {"bonusPeriods": [], "bonusPromotions": []}})

        with mock.patch.object(ah.requests, "post", side_effect=graphql):
            self.assertEqual(ah.get_bonus_promotions(), [])
        self.assertEqual(seen, ["Bearer anon-1", "Bearer anon-2"])
        self.assertEqual(ah.anon_token_cache.token, "anon-2")

    def test_a_second_rejection_is_reported_and_the_token_is_not_kept(self):
        with mock.patch.object(ah.requests, "post", return_value=Reply(400, text=REJECTED)):
            with self.assertRaises(ah.TokenRejected) as caught:
                ah.get_bonus_promotions()
        self.assertIn("member not active", str(caught.exception))
        self.assertIsInstance(caught.exception, RuntimeError)  # bestaande `except RuntimeError` vangen hem nog
        self.assertEqual(self.issued, 2)
        self.assertIsNone(ah.anon_token_cache)

    def test_another_error_is_not_retried(self):
        with mock.patch.object(ah.requests, "post", return_value=Reply(400, text='{"errors":"bad query"}')) as post:
            with self.assertRaises(RuntimeError) as caught:
                ah.get_bonus_promotions()
        self.assertNotIsInstance(caught.exception, ah.TokenRejected)
        self.assertEqual((post.call_count, self.issued), (1, 1))
        self.assertIsNotNone(ah.anon_token_cache)

    def test_the_product_search_retries_on_a_401(self):
        replies = [Reply(401, text="Unauthorized"), Reply(data={"products": [{"webshopId": 1, "title": "AH Penne", "currentPrice": 1.19}]})]
        with mock.patch.object(ah.requests, "get", side_effect=replies) as get:
            found = ah.search_products("penne", 1)
        self.assertEqual([p["title"] for p in found], ["AH Penne"])
        self.assertEqual([c.kwargs["headers"]["Authorization"] for c in get.call_args_list], ["Bearer anon-1", "Bearer anon-2"])

    def test_the_whole_bonus_comes_back_after_a_rejected_token(self):
        ah.get_token()
        today = date.today()
        start, end = (today - timedelta(days=1)).isoformat(), (today + timedelta(days=5)).isoformat()
        promotions = {"data": {"bonusPeriods": [{"startDate": start, "endDate": end}], "bonusPromotions": [
            {"id": "P1", "title": "Actie", "periodStart": start, "periodEnd": end, "promotionType": "NATIONAL",
             "promotionLabels": [{"topText": "25%", "bottomText": "korting"}],
             "products": [{"id": 1, "title": "AH Penne", "price": {"now": {"amount": 1.19}, "was": {"amount": 1.19}}}]}]}}

        def graphql(url, headers, json, timeout):
            return Reply(400, text=REJECTED) if headers["Authorization"] == "Bearer anon-1" else Reply(data=promotions)

        with mock.patch.object(ah.requests, "post", side_effect=graphql):
            products = ah.get_bonus_products()
        self.assertEqual([p["title"] for p in products], ["AH Penne"])

    def test_a_rejected_user_token_is_forgotten_too(self):
        ah.user_token_cache = ah.TokenCache(token="user-1", expires_at=10**12)
        ah.forget_token("user-1")
        self.assertIsNone(ah.user_token_cache)
        ah.forget_token(None)  # niets te vergeten: geen fout

    def test_graphql_variants_stop_at_a_rejected_token(self):
        with mock.patch.object(ah.requests, "post", return_value=Reply(400, text=REJECTED)) as post:
            with self.assertRaises(ah.TokenRejected):
                ah._graphql_variants("anon-x", ("query A", "query B"), {}, "AH test")
        self.assertEqual(post.call_count, 1)


if __name__ == "__main__":
    unittest.main()
