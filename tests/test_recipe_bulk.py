"""Recepten van AH, Jumbo en Crisp in bulk in Mealie zetten (recipe_bulk.py), en de hulpfuncties in Mealie, AH en Jumbo daarvoor."""
import asyncio
import json
import os
import re
import tempfile
import unittest
from collections.abc import Iterable
from typing import Any
from pathlib import Path
from unittest import mock

import httpx

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

from mealieshopper import ah, ah_recipes, audit, auth, auth_password, crisp, jumbo, jumbo_recipes, mealie, recipe_bulk, tasks  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402
from helpers import AppClient, routes, run, sent_json, sent_params, served  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PASSWORD = "koffie-met-melk-42"
ANNA = "een-heel-ander-wachtwoord"
TAG = {"id": "t-ah", "name": "Albert Heijn", "slug": "albert-heijn"}


def ah_url(number):
    return f"https://www.ah.nl/allerhande/recept/R-R{number}"


class FakeMealie:
    """Mealie in het geheugen: wat er staat (op bron), wat er geïmporteerd en getagd is."""

    def __init__(self):
        self.recipes = {}
        self.imported = []
        self.organized = []

    def add(self, url, slug):
        self.recipes[url] = {"slug": slug, "tags": [], "recipeCategory": []}

    def find(self, url, strict=False):
        return self.recipes.get(url)

    def import_(self, url, recipe, catalog=None):
        assert isinstance(catalog, mealie.Catalog), "de bulkimport deelt de eenheden en ingrediënten"
        slug = "r-" + re.sub(r"\D", "", url)[-6:]
        self.imported.append(url)
        self.add(url, slug)
        return slug

    def organize(self, slug, tags, categories, current=None):
        self.organized.append((slug, [tag["name"] for tag in tags], [category["name"] for category in categories], current is not None))
        return True


async def finish_tasks() -> None:
    """Wacht tot elke achtergrondtask van de app klaar is (de import loopt als task)."""
    await asyncio.gather(*tasks.running().values())


class BulkCase(unittest.IsolatedAsyncioTestCase):
    AUTH = "false"

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(os.environ, {
            "PASSKEY_AUTH_ENABLED": self.AUTH, "MEALIESHOPPER_DATA_DIR": tmp.name, "MEALIESHOPPER_AUTH_SECRET": "test-secret",
            "MEALIE_URL": "http://mealie", "MEALIE_API_TOKEN": "t",
        })
        env.start()
        self.addCleanup(env.stop)
        run(auth.init_db())
        recipe_bulk._job.clear()
        recipe_bulk._job["state"] = "idle"
        recipe_bulk._cancel.clear()
        self.mealie = FakeMealie()
        self.fetched = []
        patches = [
            mock.patch.object(recipe_bulk, "PAUSE_SECONDS", 0),
            mock.patch.object(mealie, "group_slug", new_callable=mock.AsyncMock, return_value="thuis"),
            mock.patch.object(mealie, "find_recipe_summary_by_source", new_callable=mock.AsyncMock, side_effect=self.mealie.find),
            mock.patch.object(mealie, "import_via_jsonld", new_callable=mock.AsyncMock, side_effect=self.mealie.import_),
            mock.patch.object(mealie, "ensure_tag", new_callable=mock.AsyncMock, side_effect=lambda name: {"id": f"t-{name}", "name": name, "slug": name.lower()}),
            mock.patch.object(mealie, "ensure_category", new_callable=mock.AsyncMock, side_effect=lambda name: {"id": f"c-{name}", "name": name, "slug": name.lower()}),
            mock.patch.object(mealie, "add_organizers", new_callable=mock.AsyncMock, side_effect=self.mealie.organize),
            mock.patch.object(ah, "get_recipe_jsonld", side_effect=self.ah_recipe),
            mock.patch.object(ah, "get_saved_recipes", side_effect=AssertionError("de bulkimport leest de lokale kopie")),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    async def asyncTearDown(self) -> None:
        await tasks.cancel_all()

    def ah_recipe(self, number: int) -> dict[str, Any]:
        self.fetched.append(number)
        return {"@type": "Recipe", "name": f"AH {number}", "recipeIngredient": ["1 ui"]}

    async def seed_ah(self, count: int = 5, groups: Iterable[dict[str, Any]] = ()) -> None:
        recipes = [{"id": str(100 + i), "title": f"Recept {100 + i}", "image": "", "url": ah_url(100 + i)} for i in range(count)]
        with mock.patch.object(ah_recipes, "_fetch_all", new_callable=mock.AsyncMock, return_value=recipes), \
                mock.patch.object(ah_recipes, "_fetch_groups", new_callable=mock.AsyncMock, return_value=list(groups)):
            await ah_recipes.refresh()

    def statuses(self, job):
        return [(item["id"], item["status"], item["error"]) for item in job["items"]]


class AhSelectionTest(BulkCase):
    async def test_only_the_chosen_recipes_in_their_order_without_duplicates(self):
        await self.seed_ah()
        job = await recipe_bulk.start("ah", ["102", "100", "102"], background=False)
        self.assertEqual(job["state"], "done")
        self.assertEqual(self.fetched, [102, 100])
        self.assertEqual(self.mealie.imported, [ah_url(102), ah_url(100)])
        self.assertEqual([item["title"] for item in job["items"]], ["AH 102", "AH 100"], "de titel uit het recept zelf")
        self.assertEqual((job["imported"], job["existing"], job["failed"], job["total"], job["done"]), (2, 0, 0, 2, 2))
        self.assertEqual(job["items"][0]["mealieUrl"], "http://mealie/g/thuis/r/r-102")
        self.assertEqual(self.mealie.organized, [("r-102", ["Albert Heijn"], [], False), ("r-100", ["Albert Heijn"], [], False)])
        self.assertNotIn("_source", json.dumps(job))

    async def test_a_selection_in_a_group_gets_the_category(self):
        await self.seed_ah(groups=[{"id": "g1", "name": "Kerst", "isDefault": False, "recipeIds": ["100", "101"]}])
        job = await recipe_bulk.start("ah", ["101"], "g1", background=False)
        self.assertEqual(job["category"], "Kerst")
        self.assertEqual(self.mealie.organized, [("r-101", ["Albert Heijn"], ["Kerst"], False)])

    async def test_a_recipe_that_is_not_saved_or_too_many_are_refused(self):
        await self.seed_ah()
        with self.assertRaises(ValueError):
            await recipe_bulk.start("ah", ["999"], background=False)
        with self.assertRaises(ValueError):
            await recipe_bulk.start("ah", [str(n) for n in range(recipe_bulk.MAX_SELECTION + 1)], background=False)
        with self.assertRaises(ValueError):
            await recipe_bulk.start("ah", [], background=False)
        with self.assertRaises(ValueError):
            await recipe_bulk.start("lidl", ["100"], background=False)
        self.assertEqual(recipe_bulk.progress()["state"], "idle")
        self.assertEqual(self.fetched, [])

    async def test_without_a_copy_of_the_saved_recipes(self):
        with self.assertRaises(ValueError):
            await recipe_bulk.start("ah", ["100"], background=False)

    async def test_mealie_must_be_linked(self):
        await self.seed_ah()
        with mock.patch.dict(os.environ, {"MEALIE_URL": "", "MEALIE_API_TOKEN": ""}), self.assertRaises(ValueError):
            await recipe_bulk.start("ah", ["100"], background=False)


class ExistingRecipeTest(BulkCase):
    async def test_a_recipe_already_in_mealie_is_not_fetched_but_gets_the_tag(self):
        await self.seed_ah(groups=[{"id": "g1", "name": "Kerst", "isDefault": False, "recipeIds": ["100", "101"]}])
        self.mealie.add(ah_url(101), "stamppot")
        job = await recipe_bulk.start("ah", None, "g1", whole=True, background=False)
        self.assertEqual(self.fetched, [100])
        self.assertEqual(self.statuses(job), [("100", "imported", ""), ("101", "existing", "")])
        self.assertIn(("stamppot", ["Albert Heijn"], ["Kerst"], True), self.mealie.organized, "met het recept uit de lijst: geen extra GET")


class WholeGroupTest(BulkCase):
    async def test_a_group_imports_exactly_its_recipes_with_its_name(self):
        await self.seed_ah(groups=[{"id": "g1", "name": "Doordeweeks", "isDefault": False, "recipeIds": ["103", "100"]}])
        job = await recipe_bulk.start("ah", None, "g1", whole=True, background=False)
        self.assertEqual(self.fetched, [103, 100])
        self.assertEqual((job["group"], job["category"], job["whole"]), ({"id": "g1", "name": "Doordeweeks"}, "Doordeweeks", True))

    async def test_all_saved_recipes_get_only_the_tag(self):
        await self.seed_ah(count=3)
        job = await recipe_bulk.start("ah", None, "", whole=True, background=False)
        self.assertEqual(self.fetched, [100, 101, 102])
        self.assertIsNone(job["category"])
        self.assertTrue(all(categories == [] for _, _, categories, _ in self.mealie.organized))

    async def test_an_unknown_group(self):
        await self.seed_ah()
        with self.assertRaises(ValueError):
            await recipe_bulk.start("ah", None, "weg", whole=True, background=False)


class LimitTest(BulkCase):
    async def test_only_new_imports_count_and_a_next_run_continues(self):
        await self.seed_ah(count=5)
        with mock.patch.object(recipe_bulk, "MAX_IMPORTS", 2):
            first = await recipe_bulk.start("ah", None, "", whole=True, background=False)
            self.assertEqual((first["state"], first["imported"], first["skipped"], first["remaining"]), ("done", 2, 3, 3))
            self.assertEqual(first["items"][2]["error"], "limit")
            second = await recipe_bulk.start("ah", None, "", whole=True, background=False)
        self.assertEqual((second["existing"], second["imported"], second["remaining"]), (2, 2, 1))
        self.assertEqual(self.fetched, [100, 101, 102, 103])


class StopTest(BulkCase):
    async def test_ah_refusing_stops_the_import(self):
        await self.seed_ah(count=4)
        replies = iter([{"name": "eerste"}, ah.AhHttpError("AH zoeken mislukt (429): te veel", 429)])

        def fetch(number):
            self.fetched.append(number)
            reply = next(replies)
            if isinstance(reply, Exception):
                raise reply
            return reply

        with mock.patch.object(ah, "get_recipe_jsonld", side_effect=fetch):
            job = await recipe_bulk.start("ah", None, "", whole=True, background=False)
        self.assertEqual((job["state"], job["error"]), ("error", "ah_blocked"))
        self.assertEqual(self.fetched, [100, 101], "na de weigering niets meer bij AH")
        self.assertEqual(self.statuses(job), [("100", "imported", ""), ("101", "failed", "store_failed"), ("102", "skipped", "stopped"), ("103", "skipped", "stopped")])

    async def test_a_recipe_without_content_is_not_a_failure_of_the_store(self):
        await self.seed_ah(count=4)
        with mock.patch.object(recipe_bulk, "MAX_FAILURES_IN_A_ROW", 2), \
                mock.patch.object(ah, "get_recipe_jsonld", side_effect=ah.RecipeUnavailable("leeg")):
            job = await recipe_bulk.start("ah", None, "", whole=True, background=False)
        self.assertEqual((job["state"], job["failed"]), ("done", 4))
        self.assertEqual({item["error"] for item in job["items"]}, {"no_content"})

    async def test_too_many_failures_in_a_row(self):
        await self.seed_ah(count=4)
        with mock.patch.object(recipe_bulk, "MAX_FAILURES_IN_A_ROW", 2), \
                mock.patch.object(ah, "get_recipe_jsonld", side_effect=RuntimeError("antwoord van AH: GEHEIM")):
            job = await recipe_bulk.start("ah", None, "", whole=True, background=False)
        self.assertEqual((job["state"], job["error"]), ("error", "too_many_failures"))
        self.assertEqual([item["status"] for item in job["items"]], ["failed", "failed", "skipped", "skipped"])
        self.assertNotIn("GEHEIM", json.dumps(job), "de tekst van de winkel blijft in het logboek")

    async def test_without_a_lookup_in_mealie_nothing_is_imported(self):
        await self.seed_ah(count=2)
        with mock.patch.object(mealie, "find_recipe_summary_by_source", new_callable=mock.AsyncMock, side_effect=mealie.MealieLookupError("400")):
            job = await recipe_bulk.start("ah", None, "", whole=True, background=False)
        self.assertEqual((job["state"], job["error"]), ("error", "mealie_lookup"))
        self.assertEqual((self.fetched, self.mealie.imported), ([], []))

    async def test_mealie_refusing_the_token_stops(self):
        await self.seed_ah(count=3)
        with mock.patch.object(mealie, "import_via_jsonld", new_callable=mock.AsyncMock, side_effect=mealie.MealieImportError("geweigerd", 401)):
            job = await recipe_bulk.start("ah", None, "", whole=True, background=False)
        self.assertEqual((job["state"], job["error"]), ("error", "mealie_denied"))
        self.assertEqual(self.fetched, [100])

    async def test_a_tag_mealie_refuses_is_a_warning(self):
        await self.seed_ah(count=2)
        with mock.patch.object(mealie, "ensure_tag", new_callable=mock.AsyncMock, side_effect=mealie.OrganizerError("tags aanmaken mislukt (403)", 403)):
            job = await recipe_bulk.start("ah", ["100", "101"], background=False)
        self.assertEqual((job["state"], job["imported"], job["warnings"]), ("done", 2, ["organizers_denied"]))
        self.assertEqual(self.mealie.organized, [])


class CancelTest(BulkCase):
    async def test_stopping_skips_the_rest(self):
        await self.seed_ah(count=4)

        def fetch(number):
            self.fetched.append(number)
            recipe_bulk.cancel(owner=True)
            return {"name": "x"}

        with mock.patch.object(ah, "get_recipe_jsonld", side_effect=fetch):
            job = await recipe_bulk.start("ah", None, "", whole=True, background=False)
        self.assertEqual(job["state"], "cancelled")
        self.assertEqual(self.fetched, [100])
        self.assertEqual([item["error"] for item in job["items"][1:]], ["cancelled"] * 3)

    async def test_stopping_interrupts_the_pause_between_two_recipes(self):
        await self.seed_ah(count=3)
        with mock.patch.object(recipe_bulk, "PAUSE_SECONDS", 30):
            await recipe_bulk.start("ah", None, "", whole=True)
            await asyncio.sleep(0.05)
            self.assertEqual(self.fetched, [100], "het tweede recept wacht op de pauze")
            recipe_bulk.cancel(owner=True)
            await asyncio.wait_for(finish_tasks(), 5)
        job = recipe_bulk.progress()
        self.assertEqual((job["state"], job["imported"], self.fetched), ("cancelled", 1, [100]))
        self.assertEqual([item["error"] for item in job["items"][1:]], ["cancelled"] * 2)

    async def test_an_app_shutdown_does_not_leave_the_import_running(self) -> None:
        await self.seed_ah(count=3)
        with mock.patch.object(recipe_bulk, "PAUSE_SECONDS", 30):
            await recipe_bulk.start("ah", None, "", whole=True)
            await asyncio.sleep(0.05)
            await tasks.cancel_all()
        job = recipe_bulk.progress()
        self.assertEqual(job["state"], "cancelled")
        self.assertEqual([item["error"] for item in job["items"][1:]], ["cancelled"] * 2)
        again = await recipe_bulk.start("ah", ["100"], background=False)
        self.assertEqual(again["state"], "done")

    def test_only_who_started_it_or_the_administrator(self):
        recipe_bulk._job.update(state="running", startedById="anna", items=[])
        with self.assertRaises(PermissionError):
            recipe_bulk.cancel({"id": "bob"}, owner=False)
        recipe_bulk.cancel({"id": "anna"}, owner=False)
        self.assertTrue(recipe_bulk._cancel.is_set())
        recipe_bulk._job.update(state="done")
        with self.assertRaises(ValueError):
            recipe_bulk.cancel(owner=True)


class BackgroundTest(BulkCase):
    async def test_one_import_at_a_time(self):
        await self.seed_ah()
        recipe_bulk._job.update(state="running", items=[])
        with self.assertRaises(recipe_bulk.Busy):
            await recipe_bulk.start("ah", ["100"], background=False)

    async def test_the_progress_is_a_copy(self):
        await self.seed_ah(count=1)
        job = await recipe_bulk.start("ah", ["100"], background=False)
        job["items"][0]["status"] = "veranderd"
        self.assertEqual(recipe_bulk.progress()["items"][0]["status"], "imported")

    async def test_in_a_task_with_one_audit_line(self):
        await self.seed_ah(count=2)
        with mock.patch.object(audit, "record") as record:
            await recipe_bulk.start("ah", ["100", "101"])
            await finish_tasks()
        self.assertEqual(recipe_bulk.progress()["state"], "done")
        record.assert_called_once()
        kwargs = record.call_args.kwargs
        self.assertEqual((kwargs["service"], kwargs["action"], kwargs["ok"]), ("ah", "Mealie bulk import", True))
        self.assertEqual((kwargs["detail"]["imported"], kwargs["detail"]["scope"]), (2, "selection"))


class JumboTest(BulkCase):
    LISTS = [{"id": "fav", "name": "Favorieten", "kind": "favorites", "count": 3}, {"id": "kerst", "name": "Kerst", "kind": "own", "count": 3}]

    def setUp(self):
        super().setUp()
        self.jumbo_fetched = []
        patches = [
            mock.patch.object(jumbo, "configured_refresh_token", return_value="v1.token"),
            mock.patch.object(jumbo_recipes, "get_lists", new_callable=mock.AsyncMock, return_value=self.LISTS),
            mock.patch.object(jumbo_recipes, "get_recipe", new_callable=mock.AsyncMock, side_effect=self.recipe),
            mock.patch.object(jumbo_recipes, "jsonld", side_effect=lambda recipe: {"@type": "Recipe", "name": recipe["name"]}),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def recipe(self, recipe_id):
        self.jumbo_fetched.append(recipe_id)
        return {"id": recipe_id, "name": f"Jumbo {recipe_id}", "url": f"https://www.jumbo.com/recepten/iets-{recipe_id}"}

    @staticmethod
    def card(recipe_id):
        return {"id": recipe_id, "name": f"Kaart {recipe_id}", "url": f"https://www.jumbo.com/recepten/kaart-{recipe_id}"}

    async def test_a_whole_list_is_read_page_by_page_with_its_name(self):
        pages = {1: {"recipes": [self.card("1"), self.card("2")], "total": 3, "hasNext": True},
                 2: {"recipes": [self.card("2"), self.card("3")], "total": 3, "hasNext": True},
                 3: {"recipes": [self.card("3")], "total": 3, "hasNext": True}}
        with mock.patch.object(jumbo_recipes, "get_recipes_of", new_callable=mock.AsyncMock, side_effect=lambda info, page: pages[page]) as pager:
            job = await recipe_bulk.start("jumbo", None, "kerst", whole=True, background=False)
        self.assertEqual(pager.call_count, 3, "een pagina zonder nieuwe recepten stopt het bladeren")
        self.assertEqual(self.jumbo_fetched, ["1", "2", "3"])
        self.assertEqual((job["state"], job["category"], job["group"]["name"]), ("done", "Kerst", "Kerst"))
        self.assertEqual(self.mealie.organized[0][1:3], (["Jumbo"], ["Kerst"]))
        self.assertEqual(self.mealie.imported[0], "https://www.jumbo.com/recepten/iets-1")

    async def test_favourites_get_only_the_tag(self):
        with mock.patch.object(jumbo_recipes, "get_recipes_of", new_callable=mock.AsyncMock, return_value={"recipes": [self.card("1")], "total": 1, "hasNext": False}):
            job = await recipe_bulk.start("jumbo", None, "fav", whole=True, background=False)
        self.assertIsNone(job["category"])
        self.assertEqual(self.mealie.organized[0][1:3], (["Jumbo"], []))

    async def test_a_selection_looks_in_mealie_on_the_number_first(self):
        self.mealie.add("https://www.jumbo.com/recepten/504898", "al-in-mealie")
        job = await recipe_bulk.start("jumbo", ["504898", "700001"], titles={"504898": "Romige pasta"}, background=False)
        self.assertEqual(self.jumbo_fetched, ["700001"], "wat al in Mealie staat haalt de import niet bij Jumbo op")
        self.assertEqual(self.statuses(job), [("504898", "existing", ""), ("700001", "imported", "")])
        self.assertEqual(job["items"][0]["title"], "Romige pasta")
        self.assertEqual(jumbo_recipes.get_lists.call_count, 0, "zonder groep ook geen lijsten bij Jumbo")

    async def test_jumbo_refusing_stops(self):
        with mock.patch.object(jumbo_recipes, "get_recipe", new_callable=mock.AsyncMock, side_effect=jumbo._blocked("403")):
            job = await recipe_bulk.start("jumbo", ["1", "2"], background=False)
        self.assertEqual((job["state"], job["error"]), ("error", "jumbo_blocked"))
        self.assertEqual([item["status"] for item in job["items"]], ["failed", "skipped"])

    async def test_an_unknown_list_and_no_link(self):
        job = await recipe_bulk.start("jumbo", None, "weg", whole=True, background=False)
        self.assertEqual((job["state"], job["error"]), ("error", "list_unknown"))
        with mock.patch.object(jumbo, "configured_refresh_token", return_value=""), self.assertRaises(ValueError):
            await recipe_bulk.start("jumbo", ["1"], background=False)
        with self.assertRaises(ValueError):
            await recipe_bulk.start("jumbo", ["1/2"], background=False)


class CrispTest(BulkCase):
    def setUp(self):
        super().setUp()
        self.crisp_fetched = []
        patches = [
            mock.patch.object(crisp, "configured_client_id", return_value="apparaat-id"),
            mock.patch.object(crisp, "get_recipe", side_effect=self.recipe),
            mock.patch.object(crisp, "jsonld", side_effect=lambda recipe: {"@type": "Recipe", "name": recipe["name"]}),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def recipe(self, recipe_id):
        self.crisp_fetched.append(recipe_id)
        return {"id": recipe_id, "name": f"Crisp {recipe_id}", "ingredients": [], "steps": []}

    @staticmethod
    def saved(*ids):
        recipes = [{"id": recipe_id, "name": f"Bewaard {recipe_id}"} for recipe_id in ids]
        return {"recipes": recipes, "total": len(recipes)}

    async def test_a_selection_uses_the_titles_and_looks_in_mealie_first(self):
        self.mealie.add("https://crisp.nl/#recipe-55", "al-in-mealie")
        with mock.patch.object(crisp, "get_saved_recipes", side_effect=AssertionError("een selectie haalt de lijst niet op")):
            job = await recipe_bulk.start("crisp", ["55", "56"], titles={"55": "Curry"}, background=False)
        self.assertEqual(self.crisp_fetched, ["56"], "wat al in Mealie staat haalt de import niet bij Crisp op")
        self.assertEqual(self.statuses(job), [("55", "existing", ""), ("56", "imported", "")])
        self.assertEqual((job["storeName"], job["items"][0]["title"], job["items"][1]["title"]), ("Crisp", "Curry", "Crisp 56"))
        self.assertEqual(self.mealie.imported, ["https://crisp.nl/#recipe-56"])
        self.assertEqual([entry[1:3] for entry in self.mealie.organized], [(["Crisp"], []), (["Crisp"], [])])

    async def test_all_saved_recipes_are_fetched_by_the_import_itself(self):
        with mock.patch.object(crisp, "get_saved_recipes", return_value=self.saved("1", "2", "3")) as saved:
            await recipe_bulk.start("crisp", None, whole=True)
            await finish_tasks()
        job = recipe_bulk.progress()
        saved.assert_called_once()
        self.assertEqual(self.crisp_fetched, ["1", "2", "3"])
        self.assertEqual((job["state"], job["whole"], job["groupSize"], job["category"]), ("done", True, 3, None))
        self.assertEqual([item["title"] for item in job["items"]], ["Crisp 1", "Crisp 2", "Crisp 3"])
        self.assertEqual(self.mealie.organized[0][1:3], (["Crisp"], []))

    async def test_start_itself_asks_crisp_nothing(self):
        with mock.patch.object(crisp, "get_saved_recipes", side_effect=AssertionError("niet in start")):
            job = await recipe_bulk.start("crisp", None, whole=True)
            self.assertEqual((job["state"], job["phase"], job["total"]), ("running", "preparing", 0))
            self.assertEqual(list(tasks.running()), ["recipe-bulk"])
            await tasks.cancel_all()
        recipe_bulk._job["state"] = "idle"

    async def test_a_recipe_without_content_is_not_a_store_failure(self):
        empty = crisp.RecipeUnavailable("leeg")
        with mock.patch.object(crisp, "jsonld", side_effect=[empty] * 6 + [{"@type": "Recipe", "name": "Crisp 7"}]):
            job = await recipe_bulk.start("crisp", [str(i) for i in range(1, 8)], background=False)
        self.assertEqual(job["state"], "done", "zes lege recepten na elkaar stoppen de import niet")
        self.assertEqual([item["error"] for item in job["items"]], ["no_content"] * 6 + [""])

    async def test_crisp_refusing_or_a_broken_link_stops(self):
        for error, code in ((RuntimeError("429: te veel"), "crisp_blocked"), (crisp.CrispAuthError("verlopen"), "crisp_auth")):
            with self.subTest(code), mock.patch.object(crisp, "get_recipe", side_effect=error):
                job = await recipe_bulk.start("crisp", ["1", "2"], background=False)
                self.assertEqual((job["state"], job["error"]), ("error", code))
                self.assertEqual([item["status"] for item in job["items"]], ["failed", "skipped"])
        with mock.patch.object(crisp, "get_recipe", side_effect=RuntimeError("500: storing")):
            job = await recipe_bulk.start("crisp", ["1", "2"], background=False)
        self.assertEqual((job["state"], [item["error"] for item in job["items"]]), ("done", ["store_failed", "store_failed"]))

    async def test_the_saved_list_failing_stops_with_its_own_code(self):
        for error, code in ((RuntimeError("500: storing"), "crisp_saved_failed"), (crisp.CrispAuthError("verlopen"), "crisp_auth")):
            with self.subTest(code), mock.patch.object(crisp, "get_saved_recipes", side_effect=error):
                job = await recipe_bulk.start("crisp", None, whole=True, background=False)
                self.assertEqual((job["state"], job["error"], job["total"]), ("error", code, 0))

    async def test_no_link_a_bad_id_or_a_group(self):
        with mock.patch.object(crisp, "configured_client_id", return_value=""), self.assertRaises(ValueError):
            await recipe_bulk.start("crisp", ["1"], background=False)
        for bad in (["1/2"], ["abc"]):
            with self.assertRaises(ValueError):
                await recipe_bulk.start("crisp", bad, background=False)
        with self.assertRaises(ValueError):
            await recipe_bulk.start("crisp", None, "kerst", whole=True, background=False)
        self.assertEqual(self.crisp_fetched, [])


class RoutesTest(BulkCase):
    AUTH = "true"

    def setUp(self):
        super().setUp()
        cost = mock.patch.object(auth_password, "SCRYPT_N", 2**4)
        cost.start()
        self.addCleanup(cost.stop)
        self.app = create_app()

    async def asyncSetUp(self) -> None:
        await self.seed_ah(groups=[{"id": "g1", "name": "Kerst", "isDefault": False, "recipeIds": ["100", "101"]}])
        # De import loopt als task in de app: een app die de hele test openblijft (zoals de fixture `client`).
        self.test_app = self.app.test_app()
        await self.test_app.startup()
        self.addAsyncCleanup(self.test_app.shutdown)
        self.admin = self.new_client()
        await self.admin.post("/api/auth/password", json={"username": "admin", "password": PASSWORD})

    def new_client(self) -> AppClient:
        return AppClient(self.test_app.test_client())

    async def join(self) -> AppClient:
        invite = await self.admin.post("/api/users/invites", json={"username": "anna"})
        token = invite.get_json()["token"]
        client = self.new_client()
        await client.post("/api/invites/password", json={"token": token, "password": ANNA})
        return client

    async def test_everyone_may_import_a_selection(self):
        anna = await self.join()
        answer = await anna.post("/api/mealie/bulk-import", json={"store": "ah", "ids": ["100"], "group": "g1"})
        self.assertEqual(answer.status_code, 202, answer.get_data(as_text=True))
        await finish_tasks()
        status = (await anna.get("/api/mealie/bulk-import")).get_json()
        self.assertEqual((status["state"], status["imported"], status["startedBy"]), ("done", 1, "anna"))
        self.assertNotIn("startedById", status)

    async def test_a_whole_group_is_for_the_administrator(self):
        anna, anonymous = await self.join(), self.new_client()
        self.assertEqual((await anna.post("/api/mealie/bulk-import/group", json={"store": "ah", "group": "g1"})).status_code, 403)
        for path in ("/api/mealie/bulk-import", "/api/mealie/bulk-import/group", "/api/mealie/bulk-import/cancel"):
            self.assertEqual((await anonymous.post(path, json={"store": "ah"})).status_code, 401)
        self.assertEqual((await anonymous.get("/api/mealie/bulk-import")).status_code, 401)
        self.assertEqual(self.fetched, [])
        answer = await self.admin.post("/api/mealie/bulk-import/group", json={"store": "ah", "group": "g1"})
        self.assertEqual(answer.status_code, 202)
        await finish_tasks()
        self.assertEqual((await self.admin.get("/api/mealie/bulk-import")).get_json()["imported"], 2)

    async def test_errors(self):
        self.assertEqual((await self.admin.post("/api/mealie/bulk-import", json={"store": "ah", "ids": ["999"]})).status_code, 400)
        with mock.patch.dict(os.environ, {"MEALIE_URL": "", "MEALIE_API_TOKEN": ""}):
            self.assertEqual((await self.admin.post("/api/mealie/bulk-import", json={"store": "ah", "ids": ["100"]})).status_code, 400)
        self.assertEqual((await self.admin.post("/api/mealie/bulk-import/cancel")).status_code, 400, "er loopt niets")
        recipe_bulk._job.update(state="running", startedById="iemand-anders", items=[])
        busy = await self.admin.post("/api/mealie/bulk-import", json={"store": "ah", "ids": ["100"]})
        self.assertEqual(busy.status_code, 409)
        self.assertEqual(busy.get_json()["job"]["state"], "running")
        anna = await self.join()
        self.assertEqual((await anna.post("/api/mealie/bulk-import/cancel")).status_code, 403, "niet de import van een ander")
        self.assertEqual((await self.admin.post("/api/mealie/bulk-import/cancel")).status_code, 200, "de beheerder mag wel")


class MealieHelpersTest(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.object(mealie, "_endpoint", new_callable=mock.AsyncMock, return_value=("http://m", "t"))
        patch.start()
        self.addCleanup(patch.stop)

    @staticmethod
    def reply(status=200, body=None):
        return httpx.Response(status, json=body)

    def test_a_tag_is_found_by_its_name_without_creating_it(self):
        found = {"items": [{"id": "1", "name": "Albert Heijn bonus", "slug": "x"}, {"id": "2", "name": "albert heijn", "slug": "albert-heijn"}]}
        with routes() as api:
            get = api.get("http://m/api/organizers/tags").mock(return_value=self.reply(body=found))
            post = api.post()
            self.assertEqual(run(mealie.ensure_tag("Albert  Heijn")), {"id": "2", "name": "albert heijn", "slug": "albert-heijn"})
        self.assertFalse(post.called, "niet aanmaken")
        self.assertEqual(sent_params(get.calls.last)["search"], "Albert Heijn")
        self.assertEqual(get.calls.last.request.headers["Authorization"], "Bearer t")

    def test_a_missing_category_is_created(self):
        with routes() as api:
            api.get("http://m/api/organizers/categories/slug/kerst").mock(return_value=self.reply(404, {"detail": "weg"}))
            api.get("http://m/api/organizers/categories").mock(return_value=self.reply(body={"items": []}))
            post = api.post("http://m/api/organizers/categories").mock(return_value=self.reply(201, {"id": "c1", "name": "Kerst", "slug": "kerst"}))
            self.assertEqual(run(mealie.ensure_category("Kerst"))["id"], "c1")
        self.assertEqual(sent_json(post.calls.last), {"name": "Kerst"})

    def test_no_right_to_organize(self):
        with routes() as api:
            api.get(url__startswith="http://m/api/organizers/tags").mock(return_value=self.reply(body={"items": []}))
            api.post("http://m/api/organizers/tags").mock(return_value=self.reply(403, {"detail": "nee"}))
            with self.assertRaises(mealie.OrganizerError) as caught:
                run(mealie.ensure_tag("Jumbo"))
        self.assertTrue(caught.exception.forbidden)

    def test_organizers_are_added_without_duplicates(self):
        current = {"slug": "pasta", "tags": [{"id": "t0", "name": "Snel", "slug": "snel"}, {"id": "t9", "name": "albert heijn", "slug": "ah"}],
                   "recipeCategory": []}
        with routes() as api:
            get = api.get()
            patch = api.patch("http://m/api/recipes/pasta").mock(return_value=self.reply(body={}))
            changed = run(mealie.add_organizers("pasta", [TAG], [{"id": "c1", "name": "Kerst", "slug": "kerst"}], current))
        self.assertTrue(changed)
        self.assertFalse(get.called, "het recept uit de lijst is genoeg")
        self.assertEqual(sent_json(patch.calls.last), {"recipeCategory": [{"id": "c1", "name": "Kerst", "slug": "kerst"}]}, "de tag stond er al (op naam)")
        with routes() as api:
            get = api.get("http://m/api/recipes/pasta").mock(return_value=self.reply(body={"tags": [TAG], "recipeCategory": []}))
            patch = api.patch()
            self.assertFalse(run(mealie.add_organizers("pasta", [TAG], [])))
        self.assertEqual(get.call_count, 1)
        self.assertFalse(patch.called)

    def test_the_strict_lookup_stops_a_bulk_import(self):
        with routes() as api:
            api.get("http://m/api/recipes").mock(return_value=self.reply(body={"items": [{"slug": "pasta", "tags": []}]}))
            self.assertEqual(run(mealie.find_recipe_summary_by_source(ah_url(1), strict=True)), {"slug": "pasta", "tags": []})
            self.assertEqual(run(mealie.find_recipe_by_source(ah_url(1))), "pasta")
        with routes() as api:
            api.get("http://m/api/recipes").mock(return_value=self.reply(400, {"detail": "invalid query"}))
            self.assertIsNone(run(mealie.find_recipe_summary_by_source(ah_url(1))))
            with self.assertRaises(mealie.MealieLookupError):
                run(mealie.find_recipe_summary_by_source(ah_url(1), strict=True))
        with routes() as api, self.assertRaises(mealie.MealieLookupError):
            api.get("http://m/api/recipes").mock(side_effect=httpx.ConnectError("weg"))
            run(mealie.find_recipe_summary_by_source(ah_url(1), strict=True))

    def test_units_and_foods_once_for_a_series_of_imports(self):
        line = {"note": "500 g melk", "originalText": "500 g melk"}
        catalogs = {"units": [{"id": "u-g", "name": "gram", "abbreviation": "g", "aliases": []}], "foods": [{"id": "f-melk", "name": "melk", "aliases": []}]}
        calls = []

        def get(request):
            kind = request.url.path.rsplit("/", 1)[1]
            if kind in catalogs:
                calls.append(kind)
                return self.reply(body={"items": catalogs[kind], "total_pages": 1})
            return self.reply(body={"recipeIngredient": [line]})

        catalog = mealie.Catalog()
        with routes() as api:
            api.get(url__startswith="http://m/api/").mock(side_effect=get)
            api.patch(url__startswith="http://m/api/recipes/").mock(return_value=self.reply(body={}))
            run(mealie.parse_imported_ingredients("een", catalog))
            run(mealie.parse_imported_ingredients("twee", catalog))
            self.assertEqual(calls, ["units", "foods"])
            run(mealie.parse_imported_ingredients("drie"))
        self.assertEqual(calls, ["units", "foods", "units", "foods"], "zonder catalogus zoals altijd")


class StoreRefusalTest(unittest.TestCase):
    def test_ah_graphql_gives_the_status(self):
        response = httpx.Response(429, text="te veel verzoeken")
        with served(response), self.assertRaises(ah.AhHttpError) as caught:
            run(ah._graphql("token", "query", {}, "AH recept ophalen"))
        self.assertEqual(caught.exception.status, 429)

    def test_ah_does_not_try_other_variants_when_refused(self):
        with mock.patch.object(ah, "_graphql", side_effect=ah.AhHttpError("geweigerd", 403)) as graphql, self.assertRaises(ah.AhHttpError):
            run(ah._graphql_variants("token", ("a", "b", "c"), {}, "AH"))
        self.assertEqual(graphql.call_count, 1)
        with mock.patch.object(ah, "_graphql", side_effect=[ah.AhHttpError("fout veld", 400), {"recipe": {"id": 1}}]) as graphql:
            self.assertEqual(run(ah._graphql_variants("token", ("a", "b"), {}, "AH")), {"recipe": {"id": 1}})
        self.assertEqual(graphql.call_count, 2)

    def test_jumbo_stops_the_ladder_when_refused(self):
        jumbo._persisted_route.clear()
        with mock.patch.object(jumbo, "_graphql", side_effect=jumbo._blocked("429")) as graphql, self.assertRaises(jumbo.JumboBlocked):
            run(jumbo._persisted_query("getRecipe", {"id": "1"}))
        self.assertEqual(graphql.call_count, 1)
        with mock.patch.object(jumbo, "_graphql", side_effect=jumbo._blocked("geen JSON terug / no JSON back")) as graphql, self.assertRaises(RuntimeError):
            run(jumbo._persisted_query("getRecipe", {"id": "1"}))
        self.assertGreater(graphql.call_count, 1, "geen JSON kan aan de clientnaam liggen: dan wel de andere proberen")


class MarkupTest(unittest.TestCase):
    def test_page_script_and_routes(self):
        html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "static" / "recipe_bulk.js").read_text(encoding="utf-8")
        app_js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        jumbo_js = (ROOT / "static" / "jumbo.js").read_text(encoding="utf-8")
        crisp_js = (ROOT / "static" / "crisp.js").read_text(encoding="utf-8")
        self.assertRegex(html, r'<dialog id="bulk-dialog" class="product-dialog')
        order = [html.index(f"filename='{name}'") for name in ("jumbo.js", "recipe_bulk.js", "router.js")]
        self.assertEqual(order, sorted(order))
        for store in recipe_bulk.STORES:
            for kind in ("tools", "select", "group", "status", "bar", "count", "all", "clear", "import", "note"):
                self.assertIn(f'data-bulk-{kind}="{store}"', html)
        for route in ("/api/mealie/bulk-import", "/api/mealie/bulk-import/group", "/api/mealie/bulk-import/cancel"):
            self.assertIn(route, script)
        self.assertIn('data-store-recipe="ah:', app_js)
        self.assertIn('recipeBulkDecorate("ah")', app_js)
        self.assertIn('recipeBulkDecorate("jumbo")', jumbo_js)
        self.assertIn('recipeBulkDecorate("crisp")', crisp_js)
        self.assertIn('crisp: "#crisp-recipes"', script)
        self.assertIn("/api/mealie/bulk-import/group", auth.ADMIN_PATHS)
        self.assertNotIn("/api/mealie/bulk-import", auth.ADMIN_PATHS, "een selectie mag iedereen")
        for code in ("ah_blocked", "jumbo_blocked", "jumbo_auth", "crisp_blocked", "crisp_auth", "crisp_saved_failed", "mealie_denied", "mealie_lookup", "too_many_failures", "list_failed", "list_unknown", "internal"):
            self.assertIn(f'{code}: "bulk.error.{code}"', script, "elke foutcode van de server heeft een tekst")
        for code in ("no_content", "store_failed", "mealie_failed", "limit", "cancelled", "stopped"):
            self.assertIn(f'{code}: "bulk.item.{code}"', script)


if __name__ == "__main__":
    unittest.main()
