"""De gedeelde import van een maaltijdboxrecept in Mealie: de pogingen, de foutteksten en de auditregel.

`mealkit_import` doet zelf geen http: het ophalen zit in hellofresh en marleyspoon, het zetten in Mealie in mealie (zie
hun tests met respx). Hier gaat het om wat dit module zelf beslist."""
import os
import tempfile
import unittest
from unittest import mock

import httpx

os.environ.setdefault("MEALIESHOPPER_DATA_DIR", tempfile.mkdtemp())

from mealieshopper import audit, auth, mealie, mealkit_import  # noqa: E402
from support import returned, run  # noqa: E402


class BoxError(RuntimeError):
    """De fout van een aanbieder, met een tekst voor de gebruiker."""


def valid(key: str) -> bool:
    return key.startswith("ok")


def source_url(key: str) -> str:
    return f"https://box.example/recipes/{key}"


class Case(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"MEALIESHOPPER_DATA_DIR": tmp.name, "PASSKEY_AUTH_ENABLED": "false"})
        patcher.start()
        self.addCleanup(patcher.stop)
        auth.init_db()


class ImportIntoMealieTest(Case):
    def test_the_first_attempt_that_works_gives_the_slug_and_is_logged(self):
        log = []
        slug = run(mealkit_import.import_into_mealie(
            "Box", "ok1", (("volledig", lambda: returned("pasta")), ("tweede", lambda: returned("nooit"))), BoxError, log
        ))
        self.assertEqual((slug, log), ("pasta", [{"attempt": "volledig", "ok": True}]))

    def test_a_refusal_goes_on_with_the_next_attempt(self):
        refused = mealie.MealieImportError("Mealie import mislukt (400): geheim", 400)

        async def first() -> str:
            raise refused

        log = []
        slug = run(mealkit_import.import_into_mealie("Box", "ok1", (("volledig", first), ("klein", lambda: returned("slim"))), BoxError, log))
        self.assertEqual(slug, "slim")
        self.assertEqual([entry["attempt"] for entry in log], ["volledig", "klein"])
        self.assertEqual(log[0]["status"], 400)

    def test_a_server_error_stops_because_the_recipe_may_exist_already(self):
        broken = mealie.MealieImportError("Mealie import mislukt (500): geheim", 500)
        second = mock.AsyncMock(return_value="nooit")
        with self.assertRaises(BoxError) as caught:
            run(mealkit_import.import_into_mealie("Box", "ok1", (("volledig", mock.AsyncMock(side_effect=broken)), ("klein", second)), BoxError))
        second.assert_not_called()
        self.assertIn("status 500", str(caught.exception))
        self.assertNotIn("geheim", str(caught.exception))

    def test_another_error_is_logged_and_raised_as_it_is(self):
        log = []
        with self.assertRaises(KeyError):
            run(mealkit_import.import_into_mealie("Box", "ok1", (("volledig", mock.AsyncMock(side_effect=KeyError("x"))),), BoxError, log))
        self.assertEqual(log[0]["attempt"], "volledig")
        self.assertIn("KeyError", log[0]["error"])


class FailureTest(unittest.TestCase):
    def test_our_own_errors_and_value_errors_pass_through(self):
        for exc in (ValueError("nee"), BoxError("onze tekst")):
            self.assertIs(mealkit_import.failure(exc, BoxError), exc)

    def test_a_timeout_says_the_recipe_may_exist_anyway(self):
        for exc in (httpx.ReadTimeout("x"), httpx.ConnectTimeout("x"), httpx.PoolTimeout("x")):
            fail = mealkit_import.failure(exc, BoxError)
            self.assertIsInstance(fail, BoxError)
            self.assertIn("niet binnen 60 seconden", str(fail))

    def test_an_unreachable_mealie_is_said_in_our_words(self):
        # Wat requests een ConnectionError noemde: geen verbinding, of een verbinding die wegviel voor het antwoord.
        for exc in (httpx.ConnectError("10.0.0.5 geheim"), httpx.RemoteProtocolError("10.0.0.5 geheim"), httpx.ReadError("10.0.0.5 geheim")):
            self.assertEqual(str(mealkit_import.failure(exc, BoxError)), "Mealie is niet bereikbaar", type(exc).__name__)

    def test_anything_else_shows_its_kind_but_not_its_text(self):
        fail = mealkit_import.failure(KeyError("geheim"), BoxError)
        self.assertEqual(str(fail), "Importeren in Mealie mislukt (KeyError)")


class RunImportTest(Case):
    def entries(self):
        return audit.list_entries(service="box", query="Mealie import")["entries"]

    def test_a_working_import_gives_slug_and_url_and_an_audit_line(self):
        async def work(key, attempts):
            attempts.append({"attempt": "volledig", "ok": True})
            return "pasta"

        with mock.patch.object(mealie, "recipe_page_url", new_callable=mock.AsyncMock, return_value="https://mealie.example/g/home/r/pasta"):
            result = run(mealkit_import.run_import("box", "Box", " ok1 ", valid, source_url, work, BoxError))
        self.assertEqual(result, {"slug": "pasta", "mealieUrl": "https://mealie.example/g/home/r/pasta"})
        (entry,) = self.entries()
        self.assertEqual((entry["ok"], entry["method"], entry["url"]), (True, "POST", "https://box.example/recipes/ok1"))

    def test_a_failing_import_raises_our_error_and_is_logged_with_the_status(self):
        async def work(key, attempts):
            attempts.append({"attempt": "volledig", "status": 400, "error": "geheim"})
            raise mealie.MealieImportError("Mealie import mislukt (400): geheim", 400)

        with self.assertRaises(BoxError):
            run(mealkit_import.run_import("box", "Box", "ok1", valid, source_url, work, BoxError))
        (entry,) = self.entries()
        self.assertEqual((entry["ok"], entry["status"]), (False, 400))

    def test_an_invalid_key_leaves_the_url_of_the_audit_line_empty(self):
        async def work(key, attempts):
            raise ValueError("Geen geldig recept")

        with self.assertRaises(ValueError):
            run(mealkit_import.run_import("box", "Box", "nee", valid, source_url, work, BoxError))
        (entry,) = self.entries()
        self.assertEqual((entry["ok"], entry["url"]), (False, ""))


if __name__ == "__main__":
    unittest.main()
