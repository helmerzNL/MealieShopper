"""De calorieën van de recepten in Mealie (recipe_nutrition.py, issue #283): de cache, zoeken op kcal en het aanvullen van
oude AH-recepten."""

import asyncio
import json
import os
import tempfile
import time
import unittest
from unittest import mock

import httpx

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

from mealieshopper import ah, auth, mealie, recipe_nutrition, tasks  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402
from helpers import AppTestCase, routes, sent_params  # noqa: E402

URL = "https://mealie.example.nl"
LIST = f"{URL}/api/recipes"


def uuid(number):
    return f"00000000-0000-4000-8000-{number:012d}"


def summary(number, name, stamp="2026-11-01T10:00:00", **extra):
    return {"id": uuid(number), "slug": f"recept-{number}", "name": name, "updatedAt": stamp, "recipeServings": 4, **extra}


def recipe(number, name, kcal=None, stamp="2026-11-01T10:00:00", **extra):
    nutrition = {"calories": None if kcal is None else str(kcal), "fatContent": "10"}
    return {**summary(number, name, stamp), "nutrition": nutrition, "recipeIngredient": [], **extra}


def page(items):
    return httpx.Response(200, json={"page": 1, "per_page": 100, "total": len(items), "total_pages": 1, "items": items})


class NutritionCase(AppTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = {"MEALIESHOPPER_DATA_DIR": tmp.name, "MEALIESHOPPER_AUTH_SECRET": "test-secret", "MEALIE_URL": URL, "MEALIE_API_TOKEN": "t"}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        auth._INITIALIZED = False
        recipe_nutrition._refresh.update(done=0, total=0, error="", failedAt=0.0)
        recipe_nutrition._refresh_task = None
        recipe_nutrition._backfill.clear()
        recipe_nutrition._backfill["state"] = "idle"
        pause = mock.patch.object(recipe_nutrition, "PAUSE_SECONDS", 0)
        pause.start()
        self.addCleanup(pause.stop)
        # Mealie in het geheugen: per slug het volledige recept; de lijst geeft de samenvattingen.
        self.recipes = {}
        self.details = []

    async def asyncTearDown(self) -> None:
        await tasks.cancel_all()

    def add(self, *items):
        for item in items:
            self.recipes[item["slug"]] = item

    def mock_mealie(self, api, search_order=None):
        def listing(request):
            if "search" in request.url.params:
                return page([self.recipes[slug] for slug in search_order or []])
            return page(sorted(self.recipes.values(), key=lambda r: r["name"]))

        def detail(request, slug):
            self.details.append(slug)
            return httpx.Response(200, json=self.recipes[slug]) if slug in self.recipes else httpx.Response(404)

        api.get(LIST).mock(side_effect=listing)
        api.get(url__regex=rf"^{URL}/api/recipes/(?P<slug>[a-z0-9-]+)$").mock(side_effect=detail)

    async def refresh(self):
        await recipe_nutrition.start_refresh()


class CacheTest(NutritionCase):
    async def test_remember_and_lookup(self):
        await recipe_nutrition.remember(recipe(1, "Lasagne", 490))
        await recipe_nutrition.remember(recipe(2, "Zonder", None))
        await recipe_nutrition.remember({"slug": "zonder-id", "nutrition": {"calories": "100"}})
        self.assertEqual(await recipe_nutrition.lookup(["recept-1", "recept-2", "zonder-id", "onbekend"]), {"recept-1": 490, "recept-2": None})
        await recipe_nutrition.remember(recipe(1, "Lasagne", 510))
        self.assertEqual(await recipe_nutrition.lookup(["recept-1"]), {"recept-1": 510}, "een nieuwere versie vervangt de oude")
        self.assertEqual(await recipe_nutrition.lookup([]), {})

    async def test_every_recipe_fetched_from_mealie_goes_into_the_cache(self):
        self.add(recipe(1, "Lasagne", "490 kcal"))
        with routes() as api:
            self.mock_mealie(api)
            fetched = await mealie.get_recipe("recept-1")
        self.assertEqual(fetched["name"], "Lasagne")
        self.assertEqual(await recipe_nutrition.lookup(["recept-1"]), {"recept-1": 490})

    async def test_a_failing_cache_does_not_break_fetching_a_recipe(self):
        self.add(recipe(1, "Lasagne", 490))
        with routes() as api, mock.patch.object(recipe_nutrition, "_connect", side_effect=RuntimeError("schijf vol")):
            self.mock_mealie(api)
            self.assertEqual((await mealie.get_recipe("recept-1"))["name"], "Lasagne")

    async def test_a_round_fetches_only_new_or_changed_recipes_and_drops_removed_ones(self):
        self.add(recipe(1, "Lasagne", 490), recipe(2, "Curry", 600), recipe(3, "Weg", 300))
        with routes() as api:
            self.mock_mealie(api)
            await self.refresh()
            self.assertEqual(sorted(self.details), ["recept-1", "recept-2", "recept-3"])
            self.details.clear()
            del self.recipes["recept-3"]
            self.add(recipe(2, "Curry", 650, stamp="2026-11-07T09:00:00"), recipe(4, "Soep", 250))
            await self.refresh()
        self.assertEqual(sorted(self.details), ["recept-2", "recept-4"], "alleen het gewijzigde en het nieuwe recept")
        self.assertEqual(
            await recipe_nutrition.lookup(["recept-1", "recept-2", "recept-3", "recept-4"]),
            {"recept-1": 490, "recept-2": 650, "recept-4": 250},
        )
        self.assertEqual(recipe_nutrition.refresh_progress(), {"done": 2, "total": 2})

    async def test_another_mealie_starts_over(self):
        await recipe_nutrition.remember(recipe(9, "Van het oude Mealie", 400))
        async with recipe_nutrition._connect() as conn:
            await conn.execute("INSERT INTO mealie_recipe_nutrition_meta (id, base, synced_at) VALUES (1, 'https://oud.example', ?)", (time.time(),))
            await conn.commit()
        self.add(recipe(1, "Lasagne", 490))
        with routes() as api:
            self.mock_mealie(api)
            await self.refresh()
        self.assertEqual(await recipe_nutrition.lookup(["recept-9", "recept-1"]), {"recept-1": 490})

    async def test_a_failing_round_keeps_the_error_and_waits_before_trying_again(self):
        with routes() as api:
            listing = api.get(LIST).mock(return_value=httpx.Response(500, json={"detail": "kapot"}))
            with self.assertRaises(RuntimeError):
                await recipe_nutrition.search("", 0, 600)
            self.assertEqual(listing.call_count, 1)
            with self.assertRaises(RuntimeError):
                await recipe_nutrition.search("", 0, 600)
        self.assertEqual(listing.call_count, 1, "binnen RETRY_AFTER geen nieuwe ronde")


class SearchTest(NutritionCase):
    def setUp(self):
        super().setUp()
        self.add(
            recipe(1, "Lasagne", 490), recipe(2, "curry", 650), recipe(3, "Appeltaart", 380),
            recipe(4, "Zonder calorieën", None), recipe(5, "Bami", "2092 kJ"),
        )

    async def test_without_a_query_all_recipes_by_name_within_the_bounds(self):
        with routes() as api:
            self.mock_mealie(api)
            found = await recipe_nutrition.search(None, 400, 600)
        self.assertEqual([(r["name"], r["kcal"]) for r in found["recipes"]], [("Bami", 500), ("Lasagne", 490)])
        self.assertEqual((found["total"], found["page"], found["more"], found["withoutKcal"], found["indexing"]), (2, 1, False, 1, None))
        card = found["recipes"][1]
        self.assertEqual((card["slug"], card["portions"]), ("recept-1", 4))
        self.assertTrue(card["image"] is None or card["image"].startswith("/api/mealie/recipes/"))

    async def test_only_a_minimum_or_only_a_maximum(self):
        with routes() as api:
            self.mock_mealie(api)
            self.assertEqual([r["name"] for r in (await recipe_nutrition.search("", 600, None))["recipes"]], ["curry"])
            self.assertEqual([r["name"] for r in (await recipe_nutrition.search("", None, 400))["recipes"]], ["Appeltaart"])

    async def test_pages(self):
        with routes() as api:
            self.mock_mealie(api)
            first = await recipe_nutrition.search("", 0, 5000, page=1, per_page=2)
            second = await recipe_nutrition.search("", 0, 5000, page=2, per_page=2)
        self.assertEqual(([r["name"] for r in first["recipes"]], first["more"]), (["Appeltaart", "Bami"], True))
        self.assertEqual(([r["name"] for r in second["recipes"]], second["more"]), (["curry", "Lasagne"], False))

    async def test_with_a_query_mealie_decides_what_matches_and_the_order(self):
        with routes() as api:
            self.mock_mealie(api, search_order=["recept-2", "recept-4", "recept-1"])
            found = await recipe_nutrition.search("  pasta   saus ", None, 700)
            searched = [call for call in api.calls if "search" in call.request.url.params]
        self.assertEqual([r["name"] for r in found["recipes"]], ["curry", "Lasagne"])
        self.assertEqual(found["withoutKcal"], 1)
        self.assertEqual(sent_params(searched[-1])["search"], "pasta saus")

    async def test_a_fresh_cache_does_not_read_the_list_again(self):
        with routes() as api:
            self.mock_mealie(api)
            await recipe_nutrition.search("", 0, 600)
            calls = len(api.calls)
            await recipe_nutrition.search("", 0, 700)
            self.assertEqual(len(api.calls), calls, "binnen INDEX_TTL komt alles uit de cache")
            with mock.patch.object(recipe_nutrition, "INDEX_TTL", 0):
                await recipe_nutrition.search("", 0, 700)
            self.assertGreater(len(api.calls), calls)

    async def test_while_the_first_round_runs_the_progress_comes_along(self):
        gate = asyncio.Event()
        real = mealie.get_recipe

        async def slow(slug):
            if slug != "recept-3":
                await gate.wait()
            return await real(slug)

        with routes() as api, mock.patch.object(mealie, "get_recipe", side_effect=slow):
            self.mock_mealie(api)
            found = await recipe_nutrition.search("", 0, 5000, wait=0.2)
            self.assertEqual(found["indexing"], {"done": 1, "total": 5})
            self.assertEqual([r["name"] for r in found["recipes"]], ["Appeltaart"], "wat er al is")
            gate.set()
            await recipe_nutrition.refresh_running()
            found = await recipe_nutrition.search("", 0, 5000, wait=0.2)
        self.assertIsNone(found["indexing"])
        self.assertEqual(found["total"], 4)

    async def test_bounds_from_a_request(self):
        self.assertEqual(recipe_nutrition.kcal_range("400", "600"), (400, 600))
        self.assertEqual(recipe_nutrition.kcal_range("600", "400"), (400, 600), "omgedraaid")
        self.assertEqual(recipe_nutrition.kcal_range("", None), (None, None))
        self.assertEqual(recipe_nutrition.kcal_range("-5", "99999"), (0, recipe_nutrition.MAX_KCAL))
        self.assertEqual(recipe_nutrition.kcal_range("450,5", " "), (450, None))
        with self.assertRaises(ValueError):
            recipe_nutrition.kcal_range("veel", None)


class RouteTest(NutritionCase):
    def setUp(self):
        super().setUp()
        self.add(recipe(1, "Lasagne", 490), recipe(2, "Curry", 650))
        self.client = self.client_for(create_app())

    async def test_the_search_route_filters_on_kcal_when_a_bound_is_given(self):
        with routes() as api:
            self.mock_mealie(api, search_order=["recept-2", "recept-1"])
            answer = await self.client.get("/api/mealie/recipes?kcalMax=600")
            self.assertEqual(answer.status_code, 200)
            self.assertEqual([r["name"] for r in answer.get_json()["recipes"]], ["Lasagne"])
            # Zonder grens zoekt Mealie zelf, met de bekende kcal bij de kaarten.
            plain = (await self.client.get("/api/mealie/recipes?q=curry")).get_json()
        self.assertEqual({r["name"]: r["kcal"] for r in plain["recipes"]}, {"Curry": 650, "Lasagne": 490})
        self.assertNotIn("withoutKcal", plain)

    async def test_a_bound_that_is_not_a_number(self):
        with routes() as api:
            anything = api.route()
            answer = await self.client.get("/api/mealie/recipes?kcalMin=veel")
        self.assertEqual(answer.status_code, 400)
        self.assertFalse(anything.called)


class BackfillTest(NutritionCase):
    def setUp(self):
        super().setUp()
        ah_url = "https://www.ah.nl/allerhande/recept/R-R{}/naam"
        self.add(
            recipe(1, "Zonder", None, orgURL=ah_url.format(101), nutrition={"calories": None, "fiberContent": "4"}),
            recipe(2, "Heeft al", 480, orgURL=ah_url.format(102)),
            recipe(3, "AH zonder voedingswaarden", None, orgURL=ah_url.format(103)),
            recipe(4, "Van Jumbo", None, orgURL="https://www.jumbo.com/recepten/iets-1"),
            recipe(5, "Weg bij AH", None, orgURL=ah_url.format(105)),
        )
        self.asked = []
        self.patched = {}

    def ah_recipe(self, number):
        self.asked.append(number)
        if number == 105:
            raise ah.RecipeUnavailable("weg")
        ld = {"@type": "Recipe", "name": f"AH {number}"}
        if number == 101:
            ld["nutrition"] = {"@type": "NutritionInformation", "calories": "490 kcal", "fiberContent": "6 g", "sodiumContent": "200 mg"}
        return ld

    def mock_patch(self, api, status=200):
        def patched(request, slug):
            self.patched[slug] = json.loads(request.content)
            return httpx.Response(status, json={})

        api.patch(url__regex=rf"^{URL}/api/recipes/(?P<slug>[a-z0-9-]+)$").mock(side_effect=patched)

    async def finish(self):
        await asyncio.gather(*tasks.running().values())

    async def test_old_ah_recipes_get_their_nutrition_and_what_is_there_stays(self):
        with routes() as api, mock.patch.object(ah, "get_recipe_jsonld", side_effect=self.ah_recipe), \
                mock.patch.object(recipe_nutrition.audit, "record", new_callable=mock.AsyncMock) as record:
            self.mock_mealie(api)
            self.mock_patch(api)
            self.assertEqual(recipe_nutrition.start_backfill()["state"], "running")
            with self.assertRaises(recipe_nutrition.Busy):
                recipe_nutrition.start_backfill()
            await self.finish()
        # Op naam, zoals de lijst van Mealie; niet bij een recept dat al calorieën heeft of niet van AH komt.
        self.assertEqual(self.asked, [103, 105, 101])
        self.assertEqual(self.patched, {"recept-1": {"nutrition": {"calories": "490", "fiberContent": "4", "sodiumContent": "200"}}})
        status = recipe_nutrition.backfill_progress()
        self.assertEqual(
            {key: status[key] for key in ("state", "total", "checked", "updated", "skipped", "failed")},
            {"state": "done", "total": 4, "checked": 4, "updated": 1, "skipped": 3, "failed": 0},
        )
        self.assertEqual(await recipe_nutrition.lookup(["recept-1"]), {"recept-1": 490})
        self.assertEqual(record.call_args.kwargs["detail"]["updated"], 1)

    async def test_a_refusal_of_ah_stops(self):
        refused = ah.AhHttpError("te veel", 429)
        with routes() as api, mock.patch.object(ah, "get_recipe_jsonld", side_effect=refused), \
                mock.patch.object(recipe_nutrition.audit, "record", new_callable=mock.AsyncMock):
            self.mock_mealie(api)
            self.mock_patch(api)
            recipe_nutrition.start_backfill()
            await self.finish()
        status = recipe_nutrition.backfill_progress()
        self.assertEqual((status["state"], status["updated"], status["checked"]), ("stopped", 0, 1))
        self.assertIn("AH", status["error"])
        self.assertEqual(self.patched, {})

    async def test_too_many_failures_in_a_row_stop(self):
        with routes() as api, mock.patch.object(ah, "get_recipe_jsonld", side_effect=self.ah_recipe), \
                mock.patch.object(recipe_nutrition, "MAX_FAILURES_IN_A_ROW", 1), \
                mock.patch.object(recipe_nutrition.audit, "record", new_callable=mock.AsyncMock):
            self.mock_mealie(api)
            self.mock_patch(api, status=500)
            recipe_nutrition.start_backfill()
            await self.finish()
        status = recipe_nutrition.backfill_progress()
        self.assertEqual((status["state"], status["failed"]), ("error", 1))
        self.assertIn("(500)", status["error"])

    async def test_the_route_is_for_the_administrator(self):
        self.assertTrue(auth.admin_only("/api/mealie/nutrition/backfill"))
        client = self.client_for(create_app())
        self.assertEqual((await client.get("/api/mealie/nutrition/backfill")).get_json()["state"], "idle")
        with routes() as api, mock.patch.object(ah, "get_recipe_jsonld", side_effect=self.ah_recipe), \
                mock.patch.object(recipe_nutrition.audit, "record", new_callable=mock.AsyncMock):
            self.mock_mealie(api)
            self.mock_patch(api)
            gate = asyncio.Event()
            real = mealie.list_recipes

            async def waiting(*args, **kwargs):
                await gate.wait()
                return await real(*args, **kwargs)

            with mock.patch.object(mealie, "list_recipes", side_effect=waiting):
                self.assertEqual((await client.post("/api/mealie/nutrition/backfill")).status_code, 202)
                self.assertEqual((await client.post("/api/mealie/nutrition/backfill")).status_code, 409)
                gate.set()
                await self.finish()
        self.assertEqual((await client.get("/api/mealie/nutrition/backfill")).get_json()["updated"], 1)
        with mock.patch.dict(os.environ, {"MEALIE_URL": "", "MEALIE_API_TOKEN": ""}):
            self.assertEqual((await client.post("/api/mealie/nutrition/backfill")).status_code, 424)


if __name__ == "__main__":
    unittest.main()
