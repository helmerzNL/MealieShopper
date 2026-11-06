"""Het anonieme AH-token: hooguit een uur bewaren en een geweigerd token één keer vervangen ("member not active")."""
import asyncio
import json
import os
import tempfile
import unittest
from datetime import date, timedelta
from unittest import mock

import httpx

os.environ.setdefault("MEALIESHOPPER_DATA_DIR", tempfile.mkdtemp())

from mealieshopper import ah  # noqa: E402
from support import routes, run  # noqa: E402

REJECTED = '{"error_description":"member not active","error":"invalid_grant"}'
ANONYMOUS = f"{ah.AH_API_BASE}/mobile-auth/v1/auth/token/anonymous"
REFRESH = f"{ah.AH_API_BASE}/mobile-auth/v1/auth/token/refresh"
GRAPHQL = f"{ah.AH_API_BASE}/graphql"
SEARCH = f"{ah.AH_API_BASE}/mobile-services/product/search/v2"


def Reply(status=200, data=None, text=None):
    """Een antwoord van AH, voor respx: JSON met `data`, of de ruwe `text`."""
    if text is not None:
        return httpx.Response(status, text=text)
    return httpx.Response(status, json=data if data is not None else {})


async def handed_out() -> None:
    """Geeft de event loop een paar keer vrij, zoals een echt verzoek: een tweede aanroep kan dan tussendoor beginnen."""
    for _ in range(3):
        await asyncio.sleep(0)


class AnonymousTokenTest(unittest.TestCase):
    def setUp(self):
        ah.anon_token_cache = None
        ah.user_token_cache = None
        self.addCleanup(setattr, ah, "anon_token_cache", None)
        self.addCleanup(setattr, ah, "user_token_cache", None)
        self.issued = 0

        async def issue(request):
            self.issued += 1
            issued = self.issued
            await handed_out()
            return Reply(data={"access_token": f"anon-{issued}", "refresh_token": "r", "expires_in": 604798})

        self.router = self.enterContext(routes())
        self.router.post(ANONYMOUS).mock(side_effect=issue)

    def test_the_token_is_kept_for_at_most_an_hour_although_ah_says_seven_days(self):
        with mock.patch.object(ah.time, "time", return_value=1000.0):
            self.assertEqual(run(ah.get_token()), "anon-1")
            self.assertEqual(run(ah.get_token()), "anon-1")
        self.assertEqual(ah.anon_token_cache.expires_at, 1000.0 + ah.ANON_TOKEN_MAX_AGE)
        with mock.patch.object(ah.time, "time", return_value=1000.0 + ah.ANON_TOKEN_MAX_AGE + 1):
            self.assertEqual(run(ah.get_token()), "anon-2")

    def test_the_bonus_retries_once_with_a_new_token_when_ah_rejects_the_old_one(self):
        run(ah.get_token())  # een bewaard token dat AH inmiddels heeft ingetrokken
        seen = []

        def graphql(request):
            seen.append(request.headers["Authorization"])
            if request.headers["Authorization"] == "Bearer anon-1":
                return Reply(400, text=REJECTED)
            return Reply(data={"data": {"bonusPeriods": [], "bonusPromotions": []}})

        self.router.post(GRAPHQL).mock(side_effect=graphql)
        self.assertEqual(run(ah.get_bonus_promotions()), [])
        self.assertEqual(seen, ["Bearer anon-1", "Bearer anon-2"])
        self.assertEqual(ah.anon_token_cache.token, "anon-2")

    def test_a_second_rejection_is_reported_and_the_token_is_not_kept(self):
        self.router.post(GRAPHQL).mock(return_value=Reply(400, text=REJECTED))
        with self.assertRaises(ah.TokenRejected) as caught:
            run(ah.get_bonus_promotions())
        self.assertIn("member not active", str(caught.exception))
        self.assertIsInstance(caught.exception, RuntimeError)  # bestaande `except RuntimeError` vangen hem nog
        self.assertEqual(self.issued, 2)
        self.assertIsNone(ah.anon_token_cache)

    def test_another_error_is_not_retried(self):
        post = self.router.post(GRAPHQL).mock(return_value=Reply(400, text='{"errors":"bad query"}'))
        with self.assertRaises(RuntimeError) as caught:
            run(ah.get_bonus_promotions())
        self.assertNotIsInstance(caught.exception, ah.TokenRejected)
        self.assertEqual((post.call_count, self.issued), (1, 1))
        self.assertIsNotNone(ah.anon_token_cache)

    def test_the_product_search_retries_on_a_401(self):
        replies = [Reply(401, text="Unauthorized"), Reply(data={"products": [{"webshopId": 1, "title": "AH Penne", "currentPrice": 1.19}]})]
        get = self.router.get(SEARCH).mock(side_effect=replies)
        found = run(ah.search_products("penne", 1))
        self.assertEqual([p["title"] for p in found], ["AH Penne"])
        self.assertEqual([c.request.headers["Authorization"] for c in get.calls], ["Bearer anon-1", "Bearer anon-2"])

    def test_the_whole_bonus_comes_back_after_a_rejected_token(self):
        run(ah.get_token())
        today = date.today()
        start, end = (today - timedelta(days=1)).isoformat(), (today + timedelta(days=5)).isoformat()
        promotions = {"data": {"bonusPeriods": [{"startDate": start, "endDate": end}], "bonusPromotions": [
            {"id": "P1", "title": "Actie", "periodStart": start, "periodEnd": end, "promotionType": "NATIONAL",
             "promotionLabels": [{"topText": "25%", "bottomText": "korting"}],
             "products": [{"id": 1, "title": "AH Penne", "price": {"now": {"amount": 1.19}, "was": {"amount": 1.19}}}]}]}}

        def graphql(request):
            return Reply(400, text=REJECTED) if request.headers["Authorization"] == "Bearer anon-1" else Reply(data=promotions)

        self.router.post(GRAPHQL).mock(side_effect=graphql)
        products = run(ah.get_bonus_products())
        self.assertEqual([p["title"] for p in products], ["AH Penne"])

    def test_a_rejected_user_token_is_forgotten_too(self):
        ah.user_token_cache = ah.TokenCache(token="user-1", expires_at=10**12)
        ah.forget_token("user-1")
        self.assertIsNone(ah.user_token_cache)
        ah.forget_token(None)  # niets te vergeten: geen fout

    def test_graphql_variants_stop_at_a_rejected_token(self):
        post = self.router.post(GRAPHQL).mock(return_value=Reply(400, text=REJECTED))
        with self.assertRaises(ah.TokenRejected):
            run(ah._graphql_variants("anon-x", ("query A", "query B"), {}, "AH test"))
        self.assertEqual(post.call_count, 1)

    def test_two_callers_at_once_fetch_the_anonymous_token_once(self):
        # Het tweede verzoek wacht op het slot terwijl het eerste het token haalt, en neemt daarna dat token.
        async def both() -> list[str]:
            return list(await asyncio.gather(ah.get_token(), ah.get_token(), ah.get_token()))

        self.assertEqual(run(both()), ["anon-1"] * 3)
        self.assertEqual(self.issued, 1)


class UserTokenTest(unittest.TestCase):
    def setUp(self):
        ah.user_token_cache = None
        self.addCleanup(setattr, ah, "user_token_cache", None)
        self.router = self.enterContext(routes())
        self.used: list[str] = []

        async def refresh(request):
            self.used.append(json.loads(request.content)["refreshToken"])
            number = len(self.used)
            await handed_out()
            return Reply(data={"access_token": f"user-{number}", "refresh_token": f"rotated-{number}", "expires_in": 3600})

        self.route = self.router.post(REFRESH).mock(side_effect=refresh)

    def test_two_callers_at_once_refresh_the_user_token_once(self):
        # AH kan het refresh-token bij elke vernieuwing vervangen: twee vernieuwingen met hetzelfde oude token zouden de
        # koppeling kunnen breken. Met het slot vernieuwt alleen de eerste, de anderen krijgen zijn token.
        async def both() -> list[str]:
            return list(await asyncio.gather(ah.get_user_token(), ah.get_user_token(), ah.get_user_token()))

        with mock.patch.object(ah, "configured_refresh_token", return_value="old"), \
                mock.patch.object(ah, "save_refresh_token", new_callable=mock.AsyncMock) as save:
            self.assertEqual(run(both()), ["user-1"] * 3)
        self.assertEqual(self.used, ["old"])
        save.assert_awaited_once_with("rotated-1")

    def test_an_expired_user_token_is_refreshed_again(self):
        with mock.patch.object(ah, "configured_refresh_token", return_value="old"), \
                mock.patch.object(ah, "save_refresh_token", new_callable=mock.AsyncMock):
            self.assertEqual(run(ah.get_user_token()), "user-1")
            ah.user_token_cache = ah.TokenCache(token="user-1", expires_at=0.0)
            self.assertEqual(run(ah.get_user_token()), "user-2")
        self.assertEqual(self.route.call_count, 2)


if __name__ == "__main__":
    unittest.main()
