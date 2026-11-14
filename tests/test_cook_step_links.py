"""Ingrediënten bij een kookstap (issue #309): de koppelingen van Mealie (`ingredientReferences`) gaan mee naar de kookweergave,
en de kookmodus kan ze per stap wijzigen; dat gaat meteen naar Mealie (write-through, zoals het weekmenu)."""

import os
import tempfile
from unittest import mock

import httpx

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

from helpers import AppTestCase, routes, sent_json  # noqa: E402

from mealieshopper import auth, mealie  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402

URL = "https://mealie.example.nl"
TOKEN = "mealie-token"
RECIPE = f"{URL}/api/recipes/pasta-pesto"
PASTA = "aaaaaaaa-0000-4000-8000-000000000001"
OIL = "aaaaaaaa-0000-4000-8000-000000000002"
BASIL = "aaaaaaaa-0000-4000-8000-000000000003"
STEP_1 = "bbbbbbbb-0000-4000-8000-000000000001"
STEP_2 = "bbbbbbbb-0000-4000-8000-000000000002"
STEP_3 = "bbbbbbbb-0000-4000-8000-000000000003"


def response(status=200, body=None):
    return httpx.Response(status, json=body)


def recipe():
    """Een recept zoals Mealie het geeft: elk ingrediënt een `referenceId`, en een stap noemt de zijne in `ingredientReferences`."""
    return {
        "id": "cccccccc-0000-4000-8000-000000000000",
        "slug": "pasta-pesto",
        "name": "Pasta pesto",
        "recipeIngredient": [
            {"display": "200 g pasta", "referenceId": PASTA},
            {"display": "1 el olie", "referenceId": OIL.upper()},  # Mealie schrijft ze klein, maar een hoofdletter mag
            {"title": "Voor de pesto"},
            {"display": "1 bosje basilicum", "referenceId": BASIL},
        ],
        "recipeInstructions": [
            {
                "id": STEP_1,
                "title": "",
                "summary": "",
                "text": "Kook de pasta in water met olie.",
                "ingredientReferences": [{"referenceId": PASTA}, {"referenceId": OIL}, {"referenceId": PASTA}],
            },
            {"id": STEP_2, "title": "", "text": " "},
            {"id": STEP_3, "title": "Pesto", "text": "Maak de pesto.", "ingredientReferences": [{"referenceId": "kapot"}]},
        ],
    }


class CookLinksCase(AppTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = {"MEALIESHOPPER_DATA_DIR": tmp.name, "MEALIESHOPPER_AUTH_SECRET": "test-secret", "MEALIE_URL": URL, "MEALIE_API_TOKEN": TOKEN}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        auth._INITIALIZED = False
        mealie._version_cache.clear()
        self.addCleanup(mealie._version_cache.clear)
        mealie._group_slug_cache.clear()
        self.addCleanup(mealie._group_slug_cache.clear)
        self.client = self.client_for(create_app())


class DetailTest(CookLinksCase):
    async def test_the_detail_has_the_links_of_mealie(self):
        with routes() as api, mock.patch.object(mealie, "group_slug", new_callable=mock.AsyncMock, return_value="thuis"):
            api.get(RECIPE).mock(return_value=response(body=recipe()))
            data = (await self.client.get("/api/mealie/recipes/pasta-pesto")).get_json()
        self.assertEqual(
            data["ingredients"],
            [
                {"line": "200 g pasta", "ref": PASTA},
                {"line": "1 el olie", "ref": OIL},
                {"line": "1 bosje basilicum", "section": "Voor de pesto", "ref": BASIL},
            ],
        )
        self.assertEqual(data["steps"], ["Kook de pasta in water met olie.", "Maak de pesto."])
        # Even lang als de stappen; een dubbele of kapotte verwijzing valt weg.
        self.assertEqual(data["stepIds"], [STEP_1, STEP_3])
        self.assertEqual(data["stepRefs"], [[PASTA, OIL], []])

    async def test_without_ids_there_is_nothing_to_link(self):
        bare = {
            "id": "x",
            "slug": "pasta-pesto",
            "name": "Pasta",
            "recipeIngredient": [{"display": "200 g pasta"}],
            "recipeInstructions": [{"text": "Kook."}],
        }
        with routes() as api, mock.patch.object(mealie, "group_slug", new_callable=mock.AsyncMock, return_value="thuis"):
            api.get(RECIPE).mock(return_value=response(body=bare))
            data = (await self.client.get("/api/mealie/recipes/pasta-pesto")).get_json()
        self.assertEqual(data["ingredients"], [{"line": "200 g pasta"}])
        self.assertNotIn("stepIds", data)
        self.assertNotIn("stepRefs", data)

    def test_the_week_menu_keeps_the_links_when_it_scales(self):
        # Het weekmenu rekent een recept om naar de porties (ingredients.scale_recipe); de verwijzingen blijven staan.
        from mealieshopper import ingredients

        scaled = ingredients.scale_recipe(
            {
                **recipe(),
                "recipeIngredient": [
                    {"display": "200 g pasta", "quantity": 200, "unit": {"name": "g"}, "food": {"name": "pasta"}, "referenceId": PASTA}
                ],
            },
            2,
        )
        self.assertEqual(scaled["recipeIngredient"][0]["referenceId"], PASTA)
        self.assertEqual(scaled["recipeIngredient"][0]["display"], "400 g pasta")
        self.assertEqual(scaled["recipeInstructions"][0]["ingredientReferences"][0], {"referenceId": PASTA})


class LinkTest(CookLinksCase):
    async def test_linking_sends_all_steps_with_new_links_for_one(self):
        with routes() as api:
            api.get(RECIPE).mock(return_value=response(body=recipe()))
            patch = api.patch(RECIPE).mock(return_value=response(body={"slug": "pasta-pesto"}))
            answer = await self.client.put(f"/api/mealie/recipes/pasta-pesto/steps/{STEP_3}/ingredients", json={"refs": [BASIL, OIL.upper(), BASIL]})
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer.get_json(), {"slug": "pasta-pesto", "stepId": STEP_3, "refs": [BASIL, OIL]})
        sent = sent_json(patch.calls.last)
        self.assertEqual(list(sent), ["recipeInstructions"], "alleen de stappen; de rest van het recept blijft in Mealie zoals het is")
        steps = sent["recipeInstructions"]
        self.assertEqual([step["id"] for step in steps], [STEP_1, STEP_2, STEP_3], "alle stappen, ook een lege, in dezelfde volgorde")
        self.assertEqual(steps[0], recipe()["recipeInstructions"][0], "een andere stap gaat ongewijzigd terug")
        self.assertEqual(steps[2]["ingredientReferences"], [{"referenceId": BASIL}, {"referenceId": OIL}])
        self.assertEqual((steps[2]["title"], steps[2]["text"]), ("Pesto", "Maak de pesto."))
        self.assertEqual(patch.calls.last.request.headers["Authorization"], f"Bearer {TOKEN}")

    async def test_an_empty_list_unlinks_the_step(self):
        with routes() as api:
            api.get(RECIPE).mock(return_value=response(body=recipe()))
            patch = api.patch(RECIPE).mock(return_value=response(body={}))
            answer = await self.client.put(f"/api/mealie/recipes/pasta-pesto/steps/{STEP_1}/ingredients", json={"refs": []})
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(sent_json(patch.calls.last)["recipeInstructions"][0]["ingredientReferences"], [])

    async def test_a_step_or_ingredient_that_is_gone_changes_nothing(self):
        cases = {
            "onbekende stap": (f"/api/mealie/recipes/pasta-pesto/steps/{'dddddddd-0000-4000-8000-000000000000'}/ingredients", {"refs": [PASTA]}),
            "onbekend ingrediënt": (
                f"/api/mealie/recipes/pasta-pesto/steps/{STEP_1}/ingredients",
                {"refs": ["eeeeeeee-0000-4000-8000-000000000000"]},
            ),
        }
        for label, (path, body) in cases.items():
            with self.subTest(label), routes() as api:
                api.get(RECIPE).mock(return_value=response(body=recipe()))
                patch = api.patch(RECIPE).mock(return_value=response(body={}))
                answer = await self.client.put(path, json=body)
                self.assertEqual(answer.status_code, 400)
                self.assertIn("opnieuw", answer.get_json()["error"])
                self.assertFalse(patch.called)

    async def test_odd_input_is_refused_before_mealie(self):
        cases = {
            "geen uuid als stap": ("/api/mealie/recipes/pasta-pesto/steps/stap-1/ingredients", {"refs": []}),
            "geen lijst": (f"/api/mealie/recipes/pasta-pesto/steps/{STEP_1}/ingredients", {"refs": PASTA}),
            "geen uuid als ingrediënt": (f"/api/mealie/recipes/pasta-pesto/steps/{STEP_1}/ingredients", {"refs": ["pasta"]}),
            "een getal": (f"/api/mealie/recipes/pasta-pesto/steps/{STEP_1}/ingredients", {"refs": [12]}),
            "te veel": (f"/api/mealie/recipes/pasta-pesto/steps/{STEP_1}/ingredients", {"refs": [PASTA] * (mealie.MAX_STEP_REFERENCES + 1)}),
            "rare slug": (f"/api/mealie/recipes/..%2Fadmin/steps/{STEP_1}/ingredients", {"refs": []}),
        }
        for label, (path, body) in cases.items():
            with self.subTest(label), routes() as api:
                anything = api.route()
                answer = await self.client.put(path, json=body)
                self.assertIn(answer.status_code, (400, 404))
                self.assertFalse(anything.called, "niets naar Mealie")

    async def test_a_refusal_of_mealie_is_readable(self):
        with routes() as api:
            api.get(RECIPE).mock(return_value=response(body=recipe()))
            api.patch(RECIPE).mock(return_value=response(403, {"detail": "Forbidden"}))
            answer = await self.client.put(f"/api/mealie/recipes/pasta-pesto/steps/{STEP_1}/ingredients", json={"refs": [PASTA]})
        self.assertEqual(answer.status_code, 424)
        self.assertIn("Mealie", answer.get_json()["error"])
