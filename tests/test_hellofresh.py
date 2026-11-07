import asyncio
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

os.environ.setdefault("MEALIESHOPPER_DATA_DIR", tempfile.mkdtemp())

import httpx
import respx

from mealieshopper import audit, auth, hellofresh, mealie  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402
from helpers import AppTestCase, db, run, served  # noqa: E402

KEY = "pasta-met-pesto-" + "a" * 24
KEY_B = "salade-met-kip-" + "b" * 24


def course(key, name, **extra):
    recipe = {
        "name": name, "headline": "met verse basilicum", "websiteUrl": f"https://www.hellofresh.nl/recipes/{key}",
        "imagePath": "/image/pasta.jpg", "totalTime": "PT25M", "prepTime": "PT10M", "difficulty": 1,
        "cuisines": [{"name": "Italiaans"}], "label": {"text": "Herfstsmaken"},
    }
    recipe.update(extra)
    return {"recipe": recipe}


def menu_page(courses, week="2026-W44"):
    payload = {"props": {"pageProps": {"ssrPayload": {"courses": courses, "activeWeek": week, "currentWeek": "2026-W45"}}}}
    return f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script></html>'


def recipe_page(**extra):
    recipe = {
        "@context": "http://schema.org/", "@type": "Recipe", "name": "Pasta  met pesto", "description": "<p>Lekker <b>snel</b>.</p>",
        "image": "https://img.hellofresh.com/big.jpg", "totalTime": "PT1H5M", "recipeYield": 2,
        "recipeIngredient": ["200 gram Pasta", "1 stuk(s) Ui", "  naar smaak   Peper en zout "],
        "recipeInstructions": [
            {"@type": "HowToStep", "text": "<p><span>Kook de pasta.</span></p>\n<p><em>Tip: zout het water.</em></p>"},
            {"@type": "HowToStep", "text": "<p>Meng alles &amp; serveer.</p>"},
        ],
        "nutrition": {"@type": "NutritionInformation", "calories": "600 kcal", "fatContent": "20.5 g", "cholesterolContent": None, "servingSize": "757"},
    }
    recipe.update(extra)
    crumbs = {"@context": "http://schema.org/", "@type": "BreadcrumbList", "itemListElement": []}
    return "".join(f'<script type="application/ld+json">{json.dumps(block)}</script>' for block in (crumbs, recipe))


def page(text, status=200):
    return httpx.Response(status, text=text)


class TempDataDirTest(AppTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"MEALIESHOPPER_DATA_DIR": tmp.name, "PASSKEY_AUTH_ENABLED": "false"})
        patcher.start()
        self.addCleanup(patcher.stop)
        run(auth.init_db())
        # Geen echte pauze tussen verzoeken in de tests.
        gap = mock.patch.object(hellofresh, "MIN_GAP", 0)
        gap.start()
        self.addCleanup(gap.stop)


class HelperTest(AppTestCase):
    def test_durations_become_minutes(self):
        self.assertEqual([hellofresh.minutes(v) for v in ("PT25M", "PT1H5M", "PT2H", "PT0M", "", None, "25 min")], [25, 65, 120, None, None, None, None])

    def test_the_key_is_the_slug_and_the_id_of_a_recipe_page(self):
        self.assertEqual(hellofresh.recipe_key(f"https://www.hellofresh.nl/recipes/{KEY}"), KEY)
        for url in (f"https://evil.example/recipes/{KEY}", "https://www.hellofresh.nl/recipes/zonder-id", "https://www.hellofresh.nl/menus/2026-W44", None):
            self.assertIsNone(hellofresh.recipe_key(url), url)

    def test_weeks_are_checked_and_have_neighbours(self):
        self.assertEqual(hellofresh.neighbours("2026-W44"), ("2026-W43", "2026-W45"))
        self.assertEqual(hellofresh.neighbours("2026-W01"), ("2025-W52", "2026-W02"))
        self.assertEqual(hellofresh.neighbours("2020-W53")[1], "2021-W01")
        for week in ("2026-W00", "2026-W54", "2026-44", "../menus", "2026-W44/x", ""):
            with self.assertRaises(ValueError, msg=week):
                hellofresh.neighbours(week)

    def test_summary_needs_a_name_and_a_recipe_address(self):
        card = hellofresh.summary(course(KEY, "Pasta met pesto")["recipe"])
        self.assertEqual((card["key"], card["minutes"], card["cuisine"], card["label"]), (KEY, 25, "Italiaans", "Herfstsmaken"))
        self.assertEqual(card["url"], f"https://www.hellofresh.nl/recipes/{KEY}")
        self.assertTrue(card["image"].startswith("https://img.hellofresh.com/") and card["image"].endswith("/image/pasta.jpg"))
        self.assertIsNone(hellofresh.summary({"name": "Zonder adres"}))
        self.assertIsNone(hellofresh.summary({"websiteUrl": f"https://www.hellofresh.nl/recipes/{KEY}"}))

    def test_a_page_in_another_format_is_not_guessed_at(self):
        for text in ("<html></html>", '<script id="__NEXT_DATA__">{"props": 1}</script>', '<script id="__NEXT_DATA__">nope</script>'):
            with self.assertRaises(hellofresh.HelloFreshError):
                hellofresh.parse_menu(text)


class MenuTest(TempDataDirTest):
    def test_the_menu_is_read_and_kept_with_a_time(self):
        with served(return_value=page(menu_page([course(KEY, "Pasta met pesto"), course(KEY, "Dubbel"), course(KEY_B, "Salade")]))) as get:
            menu = run(hellofresh.get_menu("2026-W44"))
        self.assertEqual(str(get.calls.last.request.url), "https://www.hellofresh.nl/menus/2026-W44")
        self.assertIn("MealieShopper/", get.calls.last.request.headers["User-Agent"])
        self.assertEqual(get.calls.last.request.headers["Accept-Language"], "nl-NL,nl;q=0.9")
        self.assertEqual([r["key"] for r in menu["recipes"]], [KEY, KEY_B], "een recept staat één keer in het menu")
        self.assertEqual((menu["week"], menu["previous"], menu["next"]), ("2026-W44", "2026-W43", "2026-W45"))
        with served(side_effect=AssertionError("uit de kopie")):
            self.assertEqual(run(hellofresh.get_menu("2026-W44"))["recipes"], menu["recipes"])

    def test_a_stale_copy_or_a_refresh_asks_hellofresh_again(self):
        with served(return_value=page(menu_page([course(KEY, "Oud")]))):
            run(hellofresh.get_menu("2026-W44"))
        with served(return_value=page(menu_page([course(KEY, "Nieuw")]))) as get:
            self.assertEqual(run(hellofresh.get_menu("2026-W44", refresh=True))["recipes"][0]["name"], "Nieuw")
        self.assertEqual(get.call_count, 1)
        old = (datetime.now(timezone.utc) - hellofresh.MENU_TTL - timedelta(minutes=1)).isoformat(timespec="seconds")
        with db(hellofresh) as conn:
            conn.execute("UPDATE hellofresh_menus SET fetched_at = ?", (old,))
        with served(return_value=page(menu_page([course(KEY, "Nieuwst")]))) as get:
            self.assertEqual(run(hellofresh.get_menu("2026-W44"))["recipes"][0]["name"], "Nieuwst")
        self.assertEqual(get.call_count, 1)

    def test_an_older_copy_is_better_than_nothing_when_hellofresh_fails(self):
        with served(return_value=page(menu_page([course(KEY, "Pasta")]))):
            run(hellofresh.get_menu("2026-W44"))
        with served(side_effect=httpx.ConnectError("weg")):
            self.assertEqual(len(run(hellofresh.get_menu("2026-W44", refresh=True))["recipes"]), 1)
            with self.assertRaises(hellofresh.HelloFreshError):
                run(hellofresh.get_menu("2026-W45"))

    def test_a_week_without_a_menu_and_a_foreign_redirect_are_refused(self):
        with served(return_value=page("", status=404)):
            with self.assertRaises(ValueError):
                run(hellofresh.get_menu("2026-W44"))
        with respx.mock(assert_all_called=False) as router:
            router.get("https://www.hellofresh.nl/menus/2026-W44").respond(302, headers={"Location": "https://evil.example/menus"})
            router.get("https://evil.example/menus").respond(200, text="<html></html>")
            with self.assertRaises(hellofresh.HelloFreshError):
                run(hellofresh.get_menu("2026-W44"))
        with served(side_effect=AssertionError("geen verzoek")):
            with self.assertRaises(ValueError):
                run(hellofresh.get_menu("../etc/passwd"))

    def test_search_uses_only_the_menus_we_already_have(self):
        with served(return_value=page(menu_page([course(KEY, "Pasta met pesto"), course(KEY_B, "Salade met kip", cuisines=[{"name": "Frans"}])]))):
            run(hellofresh.get_menu("2026-W44"))
        with served(side_effect=AssertionError("geen verzoek")):
            found = run(hellofresh.search("PESTO pasta"))
            self.assertEqual((found["total"], found["weeks"], found["recipes"][0]["key"], found["recipes"][0]["week"]), (1, 1, KEY, "2026-W44"))
            self.assertEqual(run(hellofresh.search("frans"))["total"], 1)
            self.assertEqual(run(hellofresh.search("sushi"))["total"], 0)
            with self.assertRaises(ValueError):
                run(hellofresh.search("  "))


class SearchLoadTest(TempDataDirTest):
    def weeks_asked(self, get):
        return [str(call.request.url).rsplit("/", 1)[1] for call in get.calls]

    def test_upcoming_weeks_start_with_this_week_and_are_limited(self):
        with mock.patch.object(hellofresh, "date", wraps=hellofresh.date) as fake:
            fake.today.return_value = hellofresh.date(2026, 10, 27)
            self.assertEqual(hellofresh.upcoming_weeks(4), ["2026-W44", "2026-W45", "2026-W46", "2026-W47"])
            self.assertEqual(hellofresh.upcoming_weeks(99), ["2026-W44", "2026-W45", "2026-W46", "2026-W47"])
            self.assertEqual(hellofresh.upcoming_weeks(0), [])

    def test_search_with_load_fetches_the_coming_weeks_once_and_finds_in_them(self):
        pages = [page(menu_page([course(KEY, "Pasta met pesto")], week=f"2026-W{n}")) for n in (44, 45, 46, 47)]
        with mock.patch.object(hellofresh, "date", wraps=hellofresh.date) as fake:
            fake.today.return_value = hellofresh.date(2026, 10, 27)
            with served(side_effect=pages) as get:
                found = run(hellofresh.search("pesto", load=4))
            self.assertEqual(self.weeks_asked(get), ["2026-W44", "2026-W45", "2026-W46", "2026-W47"])
            self.assertEqual((found["weeks"], found["total"]), (4, 1), "hetzelfde recept in meer weken staat er één keer")
            with served(side_effect=AssertionError("uit de kopie")):
                self.assertEqual(run(hellofresh.search("pesto", load=4))["total"], 1)

    def test_search_without_load_asks_hellofresh_nothing(self):
        with served(side_effect=AssertionError("geen verzoek")):
            self.assertEqual(run(hellofresh.search("pesto"))["total"], 0)

    def test_a_week_that_fails_is_skipped_and_all_failing_is_an_error_only_without_a_copy(self):
        with mock.patch.object(hellofresh, "date", wraps=hellofresh.date) as fake:
            fake.today.return_value = hellofresh.date(2026, 10, 27)
            pages = [page("", status=404), page(menu_page([course(KEY, "Pasta met pesto")], week="2026-W45")), page("", status=500), page("", status=500)]
            with served(side_effect=pages):
                self.assertEqual(run(hellofresh.search("pesto", load=4))["total"], 1)
            old = (datetime.now(timezone.utc) - hellofresh.MENU_TTL - timedelta(minutes=1)).isoformat(timespec="seconds")
            with db(hellofresh) as conn:
                conn.execute("UPDATE hellofresh_menus SET fetched_at = ?", (old,))
            with served(return_value=page("", status=500)):
                self.assertEqual(run(hellofresh.search("pesto", load=2))["total"], 1, "een bewaarde kopie is genoeg")
            with db(hellofresh) as conn:
                conn.execute("DELETE FROM hellofresh_menus")
            with served(return_value=page("", status=500)):
                with self.assertRaises(hellofresh.HelloFreshError):
                    run(hellofresh.search("pesto", load=2))

    async def test_the_search_route_limits_load_and_the_detail_route_has_the_shape_of_the_other_stores(self):
        client = self.client_for(create_app())
        with mock.patch.object(hellofresh, "search", new_callable=mock.AsyncMock, return_value={"weeks": 0, "total": 0, "recipes": []}) as search:
            for raw, expected in (("4", 4), ("99", 4), ("-3", 0), ("x", 0), (None, 0)):
                await client.get("/api/hellofresh/search?q=a" + (f"&load={raw}" if raw is not None else ""))
                self.assertEqual(search.call_args.kwargs["load"], expected, raw)
        with served(return_value=page(recipe_page())):
            body = (await client.get(f"/api/hellofresh/recipes/{KEY}")).get_json()
        self.assertEqual((body["id"], body["name"], body["portions"], body["nutrition"]), (KEY, "Pasta met pesto", "2", []))
        self.assertEqual(body["ingredients"][0], {"line": "200 gram Pasta"})
        self.assertEqual(body["url"], f"https://www.hellofresh.nl/recipes/{KEY}")
        with served(side_effect=AssertionError("geen verzoek")):
            self.assertEqual((await client.get("/api/hellofresh/recipes/zonder-id")).status_code, 400)


class RecipeTest(TempDataDirTest):
    def test_a_recipe_comes_from_the_schema_org_data_of_its_page(self):
        with served(return_value=page(recipe_page())) as get:
            recipe = run(hellofresh.get_recipe(KEY))
        self.assertEqual(str(get.calls.last.request.url), f"https://www.hellofresh.nl/recipes/{KEY}")
        self.assertEqual((recipe["name"], recipe["minutes"], recipe["portions"]), ("Pasta met pesto", 65, "2"))
        self.assertEqual(recipe["ingredients"], ["200 gram Pasta", "1 stuk(s) Ui", "naar smaak Peper en zout"])
        self.assertEqual(recipe["steps"], ["Kook de pasta.\nTip: zout het water.", "Meng alles & serveer."])
        self.assertEqual(recipe["description"], "Lekker snel.")

    def test_a_description_without_words_is_left_out(self):
        with served(return_value=page(recipe_page(description="."))):
            self.assertEqual(run(hellofresh.get_recipe(KEY))["description"], "")

    def test_the_recipe_becomes_a_schema_org_recipe_for_mealie(self):
        with served(return_value=page(recipe_page())):
            data = hellofresh.jsonld(run(hellofresh.get_recipe(KEY)))
        self.assertEqual((data["@type"], data["name"], data["totalTime"], data["recipeYield"]), ("Recipe", "Pasta met pesto", "PT65M", "2"))
        self.assertEqual(data["url"], hellofresh.source_url(KEY))
        self.assertEqual(data["recipeInstructions"][0], {"@type": "HowToStep", "text": "Kook de pasta.\nTip: zout het water."})
        self.assertEqual(data["nutrition"]["calories"], "600 kcal")

    def test_the_payload_for_mealie_is_clean(self):
        with served(return_value=page(recipe_page(image="https://img.hellofresh.com/f_auto,fl_lossy,h_640,q_auto,w_1200/hellofresh_s3/image/pasta-1.jpg"))):
            recipe = run(hellofresh.get_recipe(KEY))
        data = hellofresh.jsonld(recipe)
        self.assertEqual(data["nutrition"], {"@type": "NutritionInformation", "calories": "600 kcal", "fatContent": "20.5 g"}, "geen null en geen servingSize")
        self.assertEqual(data["image"], ["https://img.hellofresh.com/f_jpg,q_auto,w_1200/hellofresh_s3/image/pasta-1.jpg"], "een vaste JPG, geen f_auto")
        minimal = hellofresh.jsonld(recipe, minimal=True)
        self.assertNotIn("image", minimal)
        self.assertNotIn("nutrition", minimal)
        self.assertEqual(minimal["recipeIngredient"], data["recipeIngredient"])
        json.dumps(data)

    def test_an_image_address_of_another_shape_is_left_alone(self):
        for url in ("https://example.com/a.jpg", "", None, "https://img.hellofresh.com/x/other/a.jpg"):
            self.assertEqual(hellofresh.image_url(url), url or "")

    def test_only_a_key_of_our_own_shape_is_ever_requested(self):
        with served(side_effect=AssertionError("geen verzoek")):
            for key in ("", "6a0472a6b5c58db9dbcf6138", "../../my-account", f"{KEY}/../x", f"{KEY}?page=2", KEY.upper(), None):
                with self.assertRaises(ValueError, msg=key):
                    run(hellofresh.get_recipe(key))

    def test_a_recipe_without_content_or_in_another_format_is_not_imported(self):
        with served(return_value=page(recipe_page(recipeIngredient=[], recipeInstructions=[]))):
            with self.assertRaises(hellofresh.HelloFreshError):
                hellofresh.jsonld(run(hellofresh.get_recipe(KEY)))
        with served(return_value=page("<html>geen recept</html>")):
            with self.assertRaises(hellofresh.HelloFreshError):
                run(hellofresh.get_recipe(KEY))


class PauseTest(TempDataDirTest):
    def test_two_concurrent_requests_run_one_after_the_other_with_the_pause_between_them(self):
        events: list[str] = []
        waits: list[float] = []

        async def wait(seconds: float) -> None:
            waits.append(seconds)
            events.append("pauze")

        async def answer(request: httpx.Request) -> httpx.Response:
            events.append(f"start {request.url.path}")
            await asyncio.sleep(0)
            events.append(f"end {request.url.path}")
            return page("<html></html>")

        async def both() -> list[str]:
            return await asyncio.gather(hellofresh._get("/menus/a"), hellofresh._get("/menus/b"))

        with mock.patch.object(hellofresh, "MIN_GAP", 0.5), mock.patch.object(hellofresh, "_last_request", 0.0), \
                mock.patch.object(hellofresh, "_wait", wait), served(side_effect=answer):
            run(both())
        self.assertEqual(events, ["start /menus/a", "end /menus/a", "pauze", "start /menus/b", "end /menus/b"])
        self.assertEqual(len(waits), 1)
        self.assertGreater(waits[0], 0.4)
        self.assertLessEqual(waits[0], 0.5)

    def test_a_redirect_to_the_same_host_is_followed(self):
        with respx.mock(assert_all_called=False) as router:
            router.get("https://www.hellofresh.nl/menus/2026-W44").respond(301, headers={"Location": "https://www.hellofresh.nl/menus/2026-W44/"})
            router.get("https://www.hellofresh.nl/menus/2026-W44/").respond(200, text=menu_page([course(KEY, "Pasta")]))
            menu = run(hellofresh.get_menu("2026-W44"))
        self.assertEqual([r["key"] for r in menu["recipes"]], [KEY])


class RoutesTest(TempDataDirTest):
    def setUp(self):
        super().setUp()
        self.client = self.client_for(create_app())

    async def test_menu_and_search_routes(self):
        with served(return_value=page(menu_page([course(KEY, "Pasta met pesto")]))):
            body = (await self.client.get("/api/hellofresh/menu?week=2026-W44")).get_json()
        self.assertEqual((body["week"], len(body["recipes"])), ("2026-W44", 1))
        self.assertEqual((await self.client.get("/api/hellofresh/search?q=pesto")).get_json()["total"], 1)
        bad = await self.client.get("/api/hellofresh/menu?week=nope")
        self.assertEqual((bad.status_code, bad.get_json()), (400, {"error": "Geen geldige week"}))
        self.assertEqual((await self.client.get("/api/hellofresh/search")).status_code, 400)

    async def test_a_menu_we_cannot_read_says_so_in_our_words(self):
        with served(side_effect=httpx.ConnectError("intern adres 10.0.0.1")):
            reply = await self.client.get("/api/hellofresh/menu?week=2026-W44")
        self.assertEqual((reply.status_code, reply.get_json()), (424, {"error": "HelloFresh is niet bereikbaar"}))

    async def test_import_sends_a_schema_org_recipe_to_mealie(self):
        with mock.patch.object(mealie, "ensure_configured", new_callable=mock.AsyncMock), \
                mock.patch.object(mealie, "mealie_url", new_callable=mock.AsyncMock, return_value="https://mealie.example"), \
                mock.patch.object(mealie, "group_slug", new_callable=mock.AsyncMock, return_value="home"), \
                served(return_value=page(recipe_page())), \
                mock.patch.object(mealie, "import_via_jsonld", new_callable=mock.AsyncMock, return_value="pasta-met-pesto") as send:
            body = (await self.client.post(f"/api/hellofresh/recipes/{KEY}/import")).get_json()
        self.assertEqual(body, {"slug": "pasta-met-pesto", "mealieUrl": "https://mealie.example/g/home/r/pasta-met-pesto"})
        url, data = send.call_args.args
        self.assertEqual(url, f"https://www.hellofresh.nl/recipes/{KEY}")
        self.assertEqual(data["name"], "Pasta met pesto")

    async def test_import_without_mealie_says_so_and_asks_hellofresh_nothing(self):
        with mock.patch.object(mealie, "ensure_configured", new_callable=mock.AsyncMock, side_effect=RuntimeError("Mealie is niet gekoppeld.")), \
                served(side_effect=AssertionError("niet ophalen")):
            reply = await self.client.post(f"/api/hellofresh/recipes/{KEY}/import")
        self.assertEqual((reply.status_code, reply.get_json()), (400, {"error": "Mealie is niet gekoppeld."}))

    async def test_an_invalid_key_is_refused_before_any_request(self):
        with mock.patch.object(mealie, "ensure_configured", new_callable=mock.AsyncMock), served(side_effect=AssertionError("geen verzoek")):
            reply = await self.client.post("/api/hellofresh/recipes/6a0472a6b5c58db9dbcf6138/import")
        self.assertEqual((reply.status_code, reply.get_json()), (400, {"error": "Geen geldig recept"}))

    async def test_a_failed_import_does_not_leak_the_answer_of_mealie(self):
        with mock.patch.object(mealie, "ensure_configured", new_callable=mock.AsyncMock), \
                served(return_value=page(recipe_page())), \
                mock.patch.object(mealie, "import_via_jsonld", new_callable=mock.AsyncMock, side_effect=RuntimeError("Mealie import mislukt (500): geheim")):
            reply = await self.client.post(f"/api/hellofresh/recipes/{KEY}/import")
        self.assertEqual(reply.status_code, 424)
        self.assertNotIn("geheim", reply.get_data(as_text=True))

    async def test_when_mealie_refuses_the_recipe_we_try_again_smaller_and_then_by_page(self):
        refused = mealie.MealieImportError("Mealie import mislukt (400): BAD_RECIPE_DATA", 400)
        with mock.patch.object(mealie, "ensure_configured", new_callable=mock.AsyncMock), mock.patch.object(mealie, "recipe_page_url", new_callable=mock.AsyncMock, return_value="u"), \
                served(return_value=page(recipe_page())), \
                mock.patch.object(mealie, "import_via_jsonld", new_callable=mock.AsyncMock, side_effect=[refused, "slim"]) as send, \
                mock.patch.object(mealie, "import_via_url", new_callable=mock.AsyncMock) as by_page:
            body = (await self.client.post(f"/api/hellofresh/recipes/{KEY}/import")).get_json()
        self.assertEqual(body["slug"], "slim")
        self.assertIn("image", send.call_args_list[0].args[1])
        self.assertNotIn("image", send.call_args_list[1].args[1])
        by_page.assert_not_called()
        with mock.patch.object(mealie, "ensure_configured", new_callable=mock.AsyncMock), mock.patch.object(mealie, "recipe_page_url", new_callable=mock.AsyncMock, return_value="u"), \
                served(return_value=page(recipe_page())), \
                mock.patch.object(mealie, "import_via_jsonld", new_callable=mock.AsyncMock, side_effect=[refused, refused]), \
                mock.patch.object(mealie, "import_via_url", new_callable=mock.AsyncMock, return_value="viapagina"):
            self.assertEqual((await self.client.post(f"/api/hellofresh/recipes/{KEY}/import")).get_json()["slug"], "viapagina")

    async def test_a_refusal_shows_the_status_of_mealie_but_not_its_answer(self):
        refused = mealie.MealieImportError("Mealie import mislukt (400): geheim", 400)
        with mock.patch.object(mealie, "ensure_configured", new_callable=mock.AsyncMock), served(return_value=page(recipe_page())), \
                mock.patch.object(mealie, "import_via_jsonld", new_callable=mock.AsyncMock, side_effect=refused), mock.patch.object(mealie, "import_via_url", new_callable=mock.AsyncMock, side_effect=refused):
            reply = await self.client.post(f"/api/hellofresh/recipes/{KEY}/import")
        self.assertEqual((reply.status_code, reply.get_json()), (424, {"error": "Importeren in Mealie mislukt (Mealie antwoordde met status 400)"}))
        self.assertNotIn("geheim", reply.get_data(as_text=True))

    async def test_a_server_error_of_mealie_is_not_retried_because_the_recipe_may_exist_already(self):
        broken = mealie.MealieImportError("Mealie import mislukt (500): geheim", 500)
        with mock.patch.object(mealie, "ensure_configured", new_callable=mock.AsyncMock), served(return_value=page(recipe_page())), \
                mock.patch.object(mealie, "import_via_jsonld", new_callable=mock.AsyncMock, side_effect=broken) as send, mock.patch.object(mealie, "import_via_url", new_callable=mock.AsyncMock) as by_page:
            reply = await self.client.post(f"/api/hellofresh/recipes/{KEY}/import")
        self.assertEqual(reply.get_json(), {"error": "Importeren in Mealie mislukt (Mealie antwoordde met status 500)"})
        self.assertEqual(send.call_count, 1)
        by_page.assert_not_called()

    async def audit_rows(self):
        return (await audit.list_entries(service="hellofresh", query="Mealie import"))["entries"]

    async def post_import(self, **patches):
        with mock.patch.object(mealie, "ensure_configured", new_callable=mock.AsyncMock), mock.patch.object(mealie, "recipe_page_url", new_callable=mock.AsyncMock, return_value="u"), \
                served(return_value=page(recipe_page())):
            for name, value in patches.items():
                patcher = mock.patch.object(mealie, name, new_callable=mock.AsyncMock, **value)
                patcher.start()
                self.addCleanup(patcher.stop)
            return await self.client.post(f"/api/hellofresh/recipes/{KEY}/import")

    async def test_a_successful_import_is_in_the_audit_log(self):
        reply = await self.post_import(import_via_jsonld={"return_value": "pasta"})
        self.assertEqual(reply.status_code, 200)
        (entry,) = await self.audit_rows()
        self.assertEqual((entry["service"], entry["action"], entry["method"], entry["ok"], entry["url"]), ("hellofresh", "Mealie import", "POST", True, f"https://www.hellofresh.nl/recipes/{KEY}"))

    async def test_a_refused_import_is_in_the_audit_log_with_status_and_attempts(self):
        refused = mealie.MealieImportError("Mealie import mislukt (400): BAD_RECIPE_DATA", 400)
        reply = await self.post_import(import_via_jsonld={"side_effect": refused}, import_via_url={"side_effect": refused})
        self.assertEqual(reply.status_code, 424)
        (entry,) = await self.audit_rows()
        self.assertEqual((entry["ok"], entry["status"]), (False, 400))
        self.assertIn("HelloFreshError", entry["error"])
        full = await audit.get_entry(entry["id"])
        self.assertEqual([a["attempt"] for a in full["detail"]["attempts"]], ["volledig", "zonder foto en voedingswaarden", "via de pagina"])
        self.assertTrue(all(a["status"] == 400 for a in full["detail"]["attempts"]))

    async def test_a_timeout_of_mealie_says_so_and_is_in_the_audit_log(self):
        reply = await self.post_import(import_via_jsonld={"side_effect": httpx.ReadTimeout("Read timed out")})
        self.assertEqual(reply.status_code, 424)
        self.assertIn("niet binnen 60 seconden", reply.get_json()["error"])
        (entry,) = await self.audit_rows()
        self.assertEqual((entry["ok"], entry["status"]), (False, None))
        self.assertIn("ReadTimeout", entry["error"])

    async def test_mealie_not_reachable_is_said_in_our_words(self):
        reply = await self.post_import(import_via_jsonld={"side_effect": httpx.ConnectError("10.0.0.5 geheim")})
        self.assertEqual(reply.get_json(), {"error": "Mealie is niet bereikbaar"})

    async def test_an_unexpected_error_shows_its_kind_but_not_its_text(self):
        reply = await self.post_import(import_via_jsonld={"side_effect": KeyError("geheim")})
        self.assertEqual((reply.status_code, reply.get_json()), (424, {"error": "Importeren in Mealie mislukt (KeyError)"}))
        (entry,) = await self.audit_rows()
        self.assertIn("KeyError", entry["error"])

    async def test_nothing_is_logged_when_the_audit_log_is_off(self):
        with mock.patch.dict(os.environ, {"MEALIESHOPPER_AUDIT_LOG": "false"}):
            reply = await self.post_import(import_via_jsonld={"return_value": "pasta"})
        self.assertEqual(reply.status_code, 200)
        self.assertEqual(await self.audit_rows(), [])

    def test_nothing_here_writes_to_hellofresh(self):
        with respx.mock(assert_all_called=False) as router:  # een ander verzoek dan GET heeft geen route en faalt
            router.get(f"https://www.hellofresh.nl/recipes/{KEY}").respond(200, text=recipe_page())
            run(hellofresh.get_recipe(KEY))
            self.assertEqual([call.request.method for call in router.calls], ["GET"])


if __name__ == "__main__":
    unittest.main()
