import os
import tempfile
import unittest
from unittest import mock

import requests

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

from mealieshopper import auth, mealie  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402

TOKEN = "eyJhbGciOiJIUzI1NiJ9.geheime-mealie-token.handtekening"
URL = "https://mealie.example.nl"


def response(status=200, body=None, text="", headers=None, json_error=False):
    r = mock.Mock(status_code=status, ok=200 <= status < 300, text=text, headers=headers or {})
    if json_error:
        r.json.side_effect = ValueError("geen json")
    else:
        r.json.return_value = body
    return r


def mealie_ok(version="v2.4.1", username="helmer"):
    """Antwoorden van /api/users/self en /api/app/about, in die volgorde."""
    return [response(body={"username": username, "email": "x@y.nl"}), response(body={"version": version})]


class MealieTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = {"MEALIESHOPPER_DATA_DIR": tmp.name, "MEALIESHOPPER_AUTH_SECRET": "test-secret"}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        for key in ("MEALIE_URL", "MEALIE_API_TOKEN", "JWT_SECRET"):
            os.environ.pop(key, None)
        auth._INITIALIZED = False
        self.client = create_app().test_client()


class NormalizeUrlTest(unittest.TestCase):
    def test_accepted_forms(self):
        cases = {
            "mealie.example.nl": "https://mealie.example.nl",
            "https://mealie.example.nl/": "https://mealie.example.nl",
            "  HTTPS://mealie.example.nl  ": "https://mealie.example.nl",
            "http://192.168.1.5:9000/api": "http://192.168.1.5:9000",
            "https://example.nl/mealie/api/": "https://example.nl/mealie",
            "https://mealie.example.nl/?x=1#y": "https://mealie.example.nl",
        }
        for text, expected in cases.items():
            self.assertEqual(mealie.normalize_url(text), expected, text)

    def test_rejected_forms(self):
        for text in ("", "   ", None, "ftp://mealie.example.nl", "https://", "https://gebruiker:wachtwoord@mealie.nl"):
            with self.assertRaises(ValueError, msg=repr(text)):
                mealie.normalize_url(text)


class ConfigSourceTest(MealieTestCase):
    def test_nothing_configured(self):
        self.assertEqual(mealie._config(), ("", "", None))
        with self.assertRaises(RuntimeError) as ctx:
            mealie.ensure_configured()
        self.assertIn("Beheer > Mealie", str(ctx.exception))

    def test_environment_only(self):
        with mock.patch.dict(os.environ, {"MEALIE_URL": "https://env.example.nl/", "MEALIE_API_TOKEN": "env-token"}):
            self.assertEqual(mealie._config(), ("https://env.example.nl", "env-token", "env"))

    def test_ui_configuration_beats_the_environment(self):
        auth.set_secret("MEALIE_URL", URL)
        auth.set_secret("MEALIE_API_TOKEN", TOKEN)
        with mock.patch.dict(os.environ, {"MEALIE_URL": "https://env.example.nl", "MEALIE_API_TOKEN": "env-token"}):
            self.assertEqual(mealie._config(), (URL, TOKEN, "ui"))
            self.assertEqual(mealie.mealie_url(), URL)
            self.assertEqual(mealie.auth_headers()["Authorization"], f"Bearer {TOKEN}")
            self.assertEqual(mealie.recipe_page_url("stamppot"), f"{URL}/recipe/stamppot")

    def test_url_and_token_are_never_mixed_between_sources(self):
        # Alleen een adres in de UI + een token in de omgeving: het env-token mag nooit naar het UI-adres.
        auth.set_secret("MEALIE_URL", URL)
        with mock.patch.dict(os.environ, {"MEALIE_URL": "https://env.example.nl", "MEALIE_API_TOKEN": "env-token"}):
            self.assertEqual(mealie._config(), ("https://env.example.nl", "env-token", "env"))
        self.assertEqual(mealie._config(), ("", "", None))


class UnreadableDatabaseTest(MealieTestCase):
    def setUp(self):
        super().setUp()
        mealie._db_warned = False
        self.addCleanup(setattr, mealie, "_db_warned", False)

    def test_environment_configuration_survives_a_broken_database(self):
        env = {"MEALIE_URL": "https://env.example.nl", "MEALIE_API_TOKEN": "env-token"}
        with mock.patch.dict(os.environ, env), \
                mock.patch.object(mealie.auth, "get_secret", side_effect=OSError("read-only")):
            self.assertEqual(mealie._config(), ("https://env.example.nl", "env-token", "env"))
            self.assertEqual(mealie.mealie_url(), "https://env.example.nl")
            mealie.ensure_configured()

    def test_sqlite_errors_are_treated_the_same(self):
        import sqlite3
        env = {"MEALIE_URL": "https://env.example.nl", "MEALIE_API_TOKEN": "env-token"}
        with mock.patch.dict(os.environ, env), \
                mock.patch.object(mealie.auth, "get_secret", side_effect=sqlite3.OperationalError("no such table")):
            self.assertEqual(mealie._config()[2], "env")

    def test_without_environment_it_is_simply_not_linked(self):
        with mock.patch.object(mealie.auth, "get_secret", side_effect=OSError("read-only")):
            self.assertEqual(mealie._config(), ("", "", None))
            self.assertFalse(mealie.status()["configured"])

    def test_warns_once_not_on_every_call(self):
        with mock.patch.object(mealie.auth, "get_secret", side_effect=OSError("read-only")), \
                self.assertLogs("mealieshopper.mealie", level="WARNING") as logs:
            for _ in range(5):
                mealie._config()
        self.assertEqual(len(logs.records), 1)
        self.assertIn("omgevingsvariabelen", logs.output[0])


class CheckConnectionTest(MealieTestCase):
    def check(self, *responses):
        with mock.patch.object(mealie.requests, "get", side_effect=list(responses)) as get:
            return mealie.check_connection(URL, TOKEN), get

    def error_from(self, side_effect):
        with mock.patch.object(mealie.requests, "get", side_effect=side_effect):
            with self.assertRaises(RuntimeError) as ctx:
                mealie.check_connection(URL, TOKEN)
        return str(ctx.exception)

    def test_success_returns_user_and_version(self):
        result, get = self.check(*mealie_ok())
        self.assertEqual(result, {"username": "helmer", "version": "v2.4.1"})
        first = get.call_args_list[0]
        self.assertEqual(first.args[0], f"{URL}/api/users/self")
        self.assertEqual(first.kwargs["headers"]["Authorization"], f"Bearer {TOKEN}")
        self.assertIs(first.kwargs["allow_redirects"], False)

    def test_version_is_optional(self):
        result, _ = self.check(response(body={"username": "helmer"}), requests.ConnectionError("weg"))
        self.assertEqual(result, {"username": "helmer", "version": None})

    def test_token_is_only_sent_to_the_users_endpoint(self):
        _, get = self.check(*mealie_ok())
        self.assertNotIn("Authorization", get.call_args_list[1].kwargs.get("headers", {}))

    def test_rejected_token(self):
        for status in (401, 403):
            self.assertIn("weigert de token", self.error_from([response(status)]))

    def test_wrong_address(self):
        self.assertIn("geen Mealie-API", self.error_from([response(404)]))

    def test_redirect_names_the_target(self):
        message = self.error_from([response(301, headers={"Location": "https://mealie.example.nl/"})])
        self.assertIn("stuurt door naar https://mealie.example.nl/", message)
        self.assertIn("stuurt door", self.error_from([response(302)]))

    def test_server_error(self):
        self.assertIn("(500)", self.error_from([response(500, text="kapot")]))

    def test_not_json(self):
        self.assertIn("geen JSON", self.error_from([response(json_error=True)]))

    def test_json_that_is_not_mealie(self):
        self.assertIn("lijkt geen Mealie", self.error_from([response(body={"hello": "world"})]))
        self.assertIn("lijkt geen Mealie", self.error_from([response(body=["lijst"])]))

    def test_network_problems_have_clear_messages(self):
        self.assertIn("certificaat", self.error_from(requests.exceptions.SSLError("bad cert")))
        self.assertIn("time-out", self.error_from(requests.exceptions.Timeout()))
        self.assertIn("niet bereikbaar", self.error_from(requests.exceptions.ConnectionError("refused")))
        self.assertIn("mislukt", self.error_from(requests.exceptions.TooManyRedirects("loop")))

    def test_error_messages_never_contain_the_token(self):
        for side_effect in ([response(401, text=TOKEN)], requests.exceptions.ConnectionError(TOKEN)):
            self.assertNotIn(TOKEN, self.error_from(side_effect))


class LinkTest(MealieTestCase):
    def link(self, url=URL, token=TOKEN, responses=None):
        with mock.patch.object(mealie.requests, "get", side_effect=responses or mealie_ok()):
            return mealie.link(url, token)

    def test_saves_encrypted_and_reports_connected(self):
        result = self.link("mealie.example.nl/", f"  {TOKEN}  ")
        self.assertEqual((result["connected"], result["verified"], result["source"]), (True, True, "ui"))
        self.assertEqual((result["url"], result["username"], result["version"]), (URL, "helmer", "v2.4.1"))
        self.assertEqual(auth.get_secret("MEALIE_URL"), URL)
        self.assertEqual(auth.get_secret("MEALIE_API_TOKEN"), TOKEN)
        self.assertNotIn(TOKEN, repr(result))
        import sqlite3
        raw = sqlite3.connect(auth.db_path()).execute(
            "SELECT value FROM app_settings WHERE key='MEALIE_API_TOKEN'"
        ).fetchone()[0]
        self.assertNotEqual(raw, TOKEN, "token staat leesbaar in de database")
        self.assertNotIn(TOKEN, raw)

    def test_nothing_is_saved_when_the_check_fails(self):
        with self.assertRaises(RuntimeError):
            self.link(responses=[response(401)])
        self.assertEqual(auth.get_secret("MEALIE_URL"), "")
        self.assertEqual(auth.get_secret("MEALIE_API_TOKEN"), "")

    def test_a_failed_relink_keeps_the_working_link(self):
        self.link()
        with self.assertRaises(RuntimeError):
            self.link("https://ander.example.nl", "nieuw", responses=[response(404)])
        self.assertEqual(mealie._config(), (URL, TOKEN, "ui"))

    def test_input_validation_before_any_request(self):
        with mock.patch.object(mealie.requests, "get", side_effect=AssertionError("geen verzoek")):
            for url, token in (("", TOKEN), (URL, ""), (URL, None), (URL, "twee woorden"), (URL, "regel\neinde"),
                               ("ftp://x.nl", TOKEN)):
                with self.assertRaises(ValueError, msg=(url, token)):
                    mealie.link(url, token)


class StatusTest(MealieTestCase):
    def test_not_configured(self):
        data = mealie.status()
        self.assertEqual((data["configured"], data["url"], data["source"]), (False, "", None))
        self.assertNotIn("connected", data)

    def test_never_contains_the_token(self):
        auth.set_secret("MEALIE_URL", URL)
        auth.set_secret("MEALIE_API_TOKEN", TOKEN)
        self.assertNotIn(TOKEN, repr(mealie.status()))
        with mock.patch.object(mealie.requests, "get", side_effect=mealie_ok()):
            self.assertNotIn(TOKEN, repr(mealie.status(verify=True)))

    def test_verify_success_and_failure(self):
        auth.set_secret("MEALIE_URL", URL)
        auth.set_secret("MEALIE_API_TOKEN", TOKEN)
        with mock.patch.object(mealie.requests, "get", side_effect=mealie_ok()):
            data = mealie.status(verify=True)
        self.assertEqual((data["connected"], data["username"], data["version"]), (True, "helmer", "v2.4.1"))
        with mock.patch.object(mealie.requests, "get", side_effect=[response(401)]):
            data = mealie.status(verify=True)
        self.assertFalse(data["connected"])
        self.assertIn("weigert de token", data["error"])

    def test_verify_without_configuration_makes_no_request(self):
        with mock.patch.object(mealie.requests, "get", side_effect=AssertionError("geen verzoek")):
            self.assertFalse(mealie.status(verify=True)["configured"])

    def test_clear_falls_back_to_the_environment(self):
        auth.set_secret("MEALIE_URL", URL)
        auth.set_secret("MEALIE_API_TOKEN", TOKEN)
        with mock.patch.dict(os.environ, {"MEALIE_URL": "https://env.example.nl", "MEALIE_API_TOKEN": "env-token"}):
            self.assertEqual(mealie.status()["source"], "ui")
            data = mealie.clear()
            self.assertEqual((data["source"], data["url"]), ("env", "https://env.example.nl"))
        self.assertEqual(mealie.clear()["configured"], False)


class AuthKeyTest(MealieTestCase):
    """De sleutel voor opgeslagen gegevens hangt niet van MEALIE_URL of de koppeling af."""

    def test_linking_in_the_ui_never_changes_the_encryption_key(self):
        # De sleutel voor opgeslagen gegevens (ook het AH-token) mag niet van de UI-koppeling afhangen.
        with mock.patch.dict(os.environ, {"MEALIE_URL": "https://env.example.nl"}):
            os.environ.pop("MEALIESHOPPER_AUTH_SECRET", None)
            auth._master_secrets.clear()
            before = auth.master_secret()
            auth.set_secret("AH_REFRESH_TOKEN", "ah-token")
            with mock.patch.object(mealie.requests, "get", side_effect=mealie_ok()):
                mealie.link(URL, TOKEN)
            self.assertEqual(auth.master_secret(), before)
            self.assertEqual(auth.get_secret("AH_REFRESH_TOKEN"), "ah-token")
            mealie.clear()
            self.assertEqual(auth.master_secret(), before)

    def test_delete_secret(self):
        auth.set_secret("IETS", "waarde")
        auth.delete_secret("IETS")
        auth.delete_secret("IETS")  # herhaling is geen fout
        self.assertEqual(auth.get_secret("IETS"), "")

    def test_mealie_secrets_are_not_purged_at_startup(self):
        self.assertNotIn("MEALIE_URL", auth.OBSOLETE_SECRET_KEYS)
        self.assertNotIn("MEALIE_API_TOKEN", auth.OBSOLETE_SECRET_KEYS)
        auth.set_secret("MEALIE_URL", URL)
        auth.set_secret("MEALIE_API_TOKEN", TOKEN)
        create_app()
        self.assertEqual(auth.get_secret("MEALIE_API_TOKEN"), TOKEN)


class RoutesTest(MealieTestCase):
    def test_status_route(self):
        data = self.client.get("/api/mealie/status").get_json()
        self.assertFalse(data["configured"])

    def test_link_unlink_roundtrip(self):
        with mock.patch.object(mealie.requests, "get", side_effect=mealie_ok()):
            ok = self.client.post("/api/mealie/auth", json={"url": "mealie.example.nl", "token": TOKEN})
        self.assertEqual(ok.status_code, 200)
        self.assertNotIn(TOKEN, ok.get_data(as_text=True))
        self.assertEqual(ok.get_json()["url"], URL)
        status = self.client.get("/api/mealie/status")
        self.assertEqual((status.get_json()["configured"], status.get_json()["source"]), (True, "ui"))
        self.assertNotIn(TOKEN, status.get_data(as_text=True))
        gone = self.client.post("/api/mealie/auth/logout")
        self.assertEqual(gone.status_code, 200)
        self.assertFalse(gone.get_json()["configured"])

    def test_errors_are_400_with_a_message(self):
        with mock.patch.object(mealie.requests, "get", side_effect=[response(401)]):
            bad = self.client.post("/api/mealie/auth", json={"url": URL, "token": TOKEN})
        self.assertEqual(bad.status_code, 400)
        self.assertIn("weigert de token", bad.get_json()["error"])
        self.assertNotIn(TOKEN, bad.get_data(as_text=True))
        for payload in ({}, {"url": URL}, {"token": TOKEN}, {"url": "ftp://x", "token": TOKEN}):
            self.assertEqual(self.client.post("/api/mealie/auth", json=payload).status_code, 400, payload)

    def test_routes_need_a_session_when_auth_is_on(self):
        with mock.patch.dict(os.environ, {"PASSKEY_AUTH_ENABLED": "true"}):
            for method, path in (("get", "/api/mealie/status"), ("post", "/api/mealie/auth"),
                                 ("post", "/api/mealie/auth/logout")):
                self.assertEqual(getattr(self.client, method)(path).status_code, 401, path)

    def test_ui_configuration_drives_imports_and_the_planner(self):
        auth.set_secret("MEALIE_URL", URL)
        auth.set_secret("MEALIE_API_TOKEN", TOKEN)
        post = mock.Mock(ok=True, status_code=201)
        post.json.return_value = "stamppot"
        with mock.patch.object(mealie.requests, "post", return_value=post) as sent:
            slug = mealie.import_from_url("https://example.com/recept")
        self.assertEqual(slug, "stamppot")
        self.assertEqual(sent.call_args.args[0], f"{URL}/api/recipes/create/url")
        self.assertEqual(sent.call_args.kwargs["headers"]["Authorization"], f"Bearer {TOKEN}")

    def test_page_shows_the_tab_and_the_notice(self):
        html = self.client.get("/").get_data(as_text=True)
        for fragment in ('data-subtab="beheer-mealie"', 'id="mealie-form"', 'id="mealie-token" type="password"',
                         'class="message warning message--action" id="mealie-setup"', 'id="check-mealie"',
                         'id="mealie-unlink"'):
            self.assertIn(fragment, html, fragment)


if __name__ == "__main__":
    unittest.main()
