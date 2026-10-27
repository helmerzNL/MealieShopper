import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

os.environ.setdefault("MEALIESHOPPER_DATA_DIR", tempfile.mkdtemp())

from mealieshopper import auth, marleyspoon  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402

SLUG = "710152-bulgurbowl-met-kikkererwten-en-bloemkool-met-tahin-granaatappel-en-amandelen"
SLUG_B = "710142-butter-chicken-gehaktballen-met-jasmijnrijst-en-komkommersalade"


def recipe(slug, title, **extra):
    data = {
        "id": 1, "slug": slug, "title": title, "subtitle": "met tahin en amandelen",
        "recipeServingTime": "30-40 minuten", "category": {"displayText": "vegetarisch"},
        "image": {"url": "https://marleyspoon.com/media/recipes/383922/main_photos/medium/bowl.jpeg"},
    }
    data.update(extra)
    return data


def answer(recipes, start="2026-10-26", status=200, url=marleyspoon.API, body=None):
    reply = mock.Mock(status_code=status, url=url)
    reply.json.return_value = body if body is not None else {"data": {"menu": [{"startOfWeek": start, "recipes": recipes}]}}
    return reply


class TempDataDirTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"MEALIESHOPPER_DATA_DIR": tmp.name, "PASSKEY_AUTH_ENABLED": "false"})
        patcher.start()
        self.addCleanup(patcher.stop)
        auth.init_db()
        gap = mock.patch.object(marleyspoon, "MIN_GAP", 0)
        gap.start()
        self.addCleanup(gap.stop)


class HelperTest(unittest.TestCase):
    def test_a_range_of_minutes_becomes_its_upper_bound(self):
        self.assertEqual([marleyspoon.minutes(v) for v in ("30-40 minuten", "20-30 minuten", "45 minuten", "", None, "snel")], [40, 30, 45, None, None, None])

    def test_the_key_is_the_slug_with_its_id(self):
        self.assertEqual(marleyspoon.recipe_key(SLUG), SLUG)
        for key in ("zonder-id", "../710152-x", "710152-x/y", "710152-X", "", None):
            self.assertIsNone(marleyspoon.recipe_key(key), key)

    def test_summary_needs_a_title_and_a_slug(self):
        card = marleyspoon.summary(recipe(SLUG, "Bulgurbowl"))
        self.assertEqual((card["key"], card["name"], card["headline"], card["minutes"], card["label"]), (SLUG, "Bulgurbowl", "met tahin en amandelen", 40, "vegetarisch"))
        self.assertEqual(card["url"], f"https://marleyspoon.nl/menu/{SLUG}")
        self.assertTrue(card["image"].startswith("https://marleyspoon.com/"))
        self.assertIsNone(marleyspoon.summary(recipe(SLUG, "")))
        self.assertIsNone(marleyspoon.summary(recipe("zonder-id", "Naam")))

    def test_an_image_that_is_not_https_is_left_out(self):
        self.assertEqual(marleyspoon.summary(recipe(SLUG, "A", image={"url": "http://x.example/a.jpg"}))["image"], "")
        self.assertEqual(marleyspoon.summary(recipe(SLUG, "A", image=None))["image"], "")


class MenuTest(TempDataDirTest):
    def test_the_menu_is_asked_for_one_week_and_kept_with_a_time(self):
        with mock.patch.object(marleyspoon.requests, "post", return_value=answer([recipe(SLUG, "Bulgurbowl"), recipe(SLUG, "Dubbel"), recipe(SLUG_B, "Butter chicken")])) as post:
            menu = marleyspoon.get_menu("2026-W44")
        sent = post.call_args.kwargs["json"]["variables"]
        self.assertEqual((sent["country"], sent["brand"], sent["startDate"]), ("NL", "MS", "2026-10-26"))
        self.assertEqual(post.call_args.args[0], "https://api.marleyspoon.com/public/graphql")
        self.assertNotIn("Authorization", post.call_args.kwargs["headers"], "het menu vraagt geen token")
        self.assertIn("MealieShopper/", post.call_args.kwargs["headers"]["User-Agent"])
        self.assertEqual([r["key"] for r in menu["recipes"]], [SLUG, SLUG_B], "een recept staat één keer in het menu")
        self.assertEqual((menu["week"], menu["previous"], menu["next"]), ("2026-W44", "2026-W43", "2026-W45"))
        with mock.patch.object(marleyspoon.requests, "post", side_effect=AssertionError("uit de kopie")):
            self.assertEqual(marleyspoon.get_menu("2026-W44")["recipes"], menu["recipes"])

    def test_a_stale_copy_or_a_refresh_asks_marley_spoon_again(self):
        with mock.patch.object(marleyspoon.requests, "post", return_value=answer([recipe(SLUG, "Oud")])):
            marleyspoon.get_menu("2026-W44")
        with mock.patch.object(marleyspoon.requests, "post", return_value=answer([recipe(SLUG, "Nieuw")])) as post:
            self.assertEqual(marleyspoon.get_menu("2026-W44", refresh=True)["recipes"][0]["name"], "Nieuw")
        self.assertEqual(post.call_count, 1)
        old = (datetime.now(timezone.utc) - marleyspoon.MENU_TTL - timedelta(minutes=1)).isoformat(timespec="seconds")
        with marleyspoon._connect() as conn:
            conn.execute("UPDATE marleyspoon_menus SET fetched_at = ?", (old,))
        with mock.patch.object(marleyspoon.requests, "post", return_value=answer([recipe(SLUG, "Nieuwst")])) as post:
            self.assertEqual(marleyspoon.get_menu("2026-W44")["recipes"][0]["name"], "Nieuwst")
        self.assertEqual(post.call_count, 1)

    def test_an_older_copy_is_better_than_nothing_when_marley_spoon_fails(self):
        with mock.patch.object(marleyspoon.requests, "post", return_value=answer([recipe(SLUG, "Oud")])):
            marleyspoon.get_menu("2026-W44")
        with mock.patch.object(marleyspoon.requests, "post", return_value=answer([], status=500)):
            self.assertEqual(marleyspoon.get_menu("2026-W44", refresh=True)["recipes"][0]["name"], "Oud")
        with mock.patch.object(marleyspoon.requests, "post", return_value=answer([], status=500)):
            with self.assertRaises(marleyspoon.MarleySpoonError):
                marleyspoon.get_menu("2026-W45")

    def test_a_week_without_a_menu_is_empty_and_not_kept(self):
        with mock.patch.object(marleyspoon.requests, "post", return_value=answer([], body={"data": {"menu": []}})):
            self.assertEqual(marleyspoon.get_menu("2027-W20")["recipes"], [])
        self.assertIsNone(marleyspoon._stored("2027-W20"))

    def test_the_menu_of_another_week_is_never_shown_under_this_one(self):
        with mock.patch.object(marleyspoon.requests, "post", return_value=answer([recipe(SLUG, "Andere week")], start="2026-11-02")):
            self.assertEqual(marleyspoon.get_menu("2026-W44")["recipes"], [])

    def test_a_foreign_redirect_an_error_and_another_format_are_refused(self):
        for reply in (answer([], url="https://evil.example/graphql"), answer([], status=403),
                      answer([], body={"errors": [{"message": "nope"}]}), answer([], body={"data": {"menu": {}}}), answer([], body=["x"])):
            with mock.patch.object(marleyspoon.requests, "post", return_value=reply):
                with self.assertRaises(marleyspoon.MarleySpoonError):
                    marleyspoon.get_menu("2026-W44", refresh=True)

    def test_an_unreachable_marley_spoon_says_so_in_our_words(self):
        with mock.patch.object(marleyspoon.requests, "post", side_effect=marleyspoon.requests.ConnectionError("secret detail")):
            with self.assertRaises(marleyspoon.MarleySpoonError) as caught:
                marleyspoon.get_menu("2026-W44")
        self.assertNotIn("secret", str(caught.exception))

    def test_an_invalid_week_is_refused_before_any_request(self):
        with mock.patch.object(marleyspoon.requests, "post", side_effect=AssertionError("geen verzoek")):
            for week in ("2026-W00", "2026-44", "../x"):
                with self.assertRaises(ValueError, msg=week):
                    marleyspoon.get_menu(week)

    def test_search_uses_only_the_menus_we_already_have(self):
        with mock.patch.object(marleyspoon.requests, "post", return_value=answer([recipe(SLUG, "Bulgurbowl"), recipe(SLUG_B, "Butter chicken", subtitle="met rijst")])):
            marleyspoon.get_menu("2026-W44")
        with mock.patch.object(marleyspoon.requests, "post", side_effect=AssertionError("geen verzoek")):
            found = marleyspoon.search("BUTTER rijst")
            self.assertEqual((found["total"], found["weeks"], found["recipes"][0]["week"]), (1, 1, "2026-W44"))
            self.assertEqual(marleyspoon.search("pizza")["total"], 0)
            with self.assertRaises(ValueError):
                marleyspoon.search("  ")


class RoutesTest(TempDataDirTest):
    def setUp(self):
        super().setUp()
        self.client = create_app().test_client()

    def test_menu_and_search_routes(self):
        with mock.patch.object(marleyspoon.requests, "post", return_value=answer([recipe(SLUG, "Bulgurbowl")])):
            body = self.client.get("/api/marleyspoon/menu?week=2026-W44").get_json()
        self.assertEqual(body["recipes"][0]["key"], SLUG)
        self.assertEqual(self.client.get("/api/marleyspoon/search?q=bulgur").get_json()["total"], 1)
        self.assertEqual(self.client.get("/api/marleyspoon/menu?week=nope").status_code, 400)
        self.assertEqual(self.client.get("/api/marleyspoon/search").status_code, 400)

    def test_a_menu_we_cannot_read_says_so_in_our_words(self):
        with mock.patch.object(marleyspoon.requests, "post", return_value=answer([], status=503)):
            reply = self.client.get("/api/marleyspoon/menu?week=2026-W44")
        self.assertEqual(reply.status_code, 424)
        self.assertIn("503", reply.get_json()["error"])

    def test_there_is_no_import_route(self):
        self.assertEqual(self.client.post(f"/api/marleyspoon/recipes/{SLUG}/import").status_code, 404)


if __name__ == "__main__":
    unittest.main()
