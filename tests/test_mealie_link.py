import asyncio
import datetime
import os
import ssl
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

from mealieshopper import auth, http_client, mealie  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402
from helpers import AppTestCase, routes, run, sent_url  # noqa: E402

TOKEN = "eyJhbGciOiJIUzI1NiJ9.geheime-mealie-token.handtekening"
URL = "https://mealie.example.nl"
SELF = f"{URL}/api/users/self"
ABOUT = f"{URL}/api/app/about"
PREFERENCES = f"{URL}/api/households/preferences"


def response(status=200, body=None, text="", headers=None, json_error=False):
    if json_error:
        return httpx.Response(status, text="geen json", headers=headers)
    if body is None and text:
        return httpx.Response(status, text=text, headers=headers)
    return httpx.Response(status, json=body, headers=headers)


def preferences(first_day=1):
    """Het antwoord van /api/households/preferences: de voorkeuren van het huishouden in Mealie (0 = zondag, 1 = maandag)."""
    return response(body={"privateHousehold": True, "firstDayOfWeek": first_day, "recipePublic": True, "id": "voorkeuren"})


def mealie_ok(api, version="v2.4.1", username="helmer"):
    """Routes voor /api/users/self, /api/app/about en /api/households/preferences (de eerste dag van de week, die de status
    meegeeft). Geeft de routes terug, in die volgorde."""
    return (
        api.get(SELF).mock(return_value=response(body={"username": username, "email": "x@y.nl"})),
        api.get(ABOUT).mock(return_value=response(body={"version": version})),
        api.get(PREFERENCES).mock(return_value=preferences()),
    )


def self_signed(folder: str) -> ssl.SSLContext:
    """Een TLS-server met een eigen certificaat voor 127.0.0.1, dat geen enkele client vertrouwt."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now).not_valid_after(now + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    cert_file, key_file = Path(folder, "cert.pem"), Path(folder, "key.pem")
    cert_file.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_file, key_file)
    return context


class MealieTestCase(AppTestCase):
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
        mealie._week_start_cache.clear()
        self.addCleanup(mealie._week_start_cache.clear)
        self.client = self.client_for(create_app())


class NormalizeUrlTest(AppTestCase):
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
        self.assertEqual(run(mealie._config()), ("", "", None))
        with self.assertRaises(RuntimeError) as ctx:
            run(mealie.ensure_configured())
        self.assertIn("Instellingen > Koppelen > Mealie", str(ctx.exception))

    def test_environment_only(self):
        with mock.patch.dict(os.environ, {"MEALIE_URL": "https://env.example.nl/", "MEALIE_API_TOKEN": "env-token"}):
            self.assertEqual(run(mealie._config()), ("https://env.example.nl", "env-token", "env"))

    def test_ui_configuration_beats_the_environment(self):
        run(auth.set_secret("MEALIE_URL", URL))
        run(auth.set_secret("MEALIE_API_TOKEN", TOKEN))
        with mock.patch.dict(os.environ, {"MEALIE_URL": "https://env.example.nl", "MEALIE_API_TOKEN": "env-token"}):
            self.assertEqual(run(mealie._config()), (URL, TOKEN, "ui"))
            self.assertEqual(run(mealie.mealie_url()), URL)
            self.assertEqual(run(mealie.auth_headers())["Authorization"], f"Bearer {TOKEN}")
            with mock.patch.object(mealie, "group_slug", new_callable=mock.AsyncMock, return_value=None):
                self.assertEqual(run(mealie.recipe_page_url("stamppot")), f"{URL}/recipe/stamppot", "zonder groep het oude adres")
            with mock.patch.object(mealie, "group_slug", new_callable=mock.AsyncMock, return_value="zb"):
                self.assertEqual(run(mealie.recipe_page_url("stamppot")), f"{URL}/g/zb/r/stamppot")

    def test_url_and_token_are_never_mixed_between_sources(self):
        # Alleen een adres in de UI + een token in de omgeving: het env-token mag nooit naar het UI-adres.
        run(auth.set_secret("MEALIE_URL", URL))
        with mock.patch.dict(os.environ, {"MEALIE_URL": "https://env.example.nl", "MEALIE_API_TOKEN": "env-token"}):
            self.assertEqual(run(mealie._config()), ("https://env.example.nl", "env-token", "env"))
        self.assertEqual(run(mealie._config()), ("", "", None))


class ClientTest(MealieTestCase):
    """Een gedeelde httpx-client voor Mealie, met het token per verzoek: een nieuw token telt meteen."""

    def test_the_token_goes_with_each_request_and_a_new_token_counts_at_once(self):
        run(auth.set_secret("MEALIE_URL", URL))
        run(auth.set_secret("MEALIE_API_TOKEN", TOKEN))

        async def two_weeks() -> tuple[list[httpx.Request], httpx.AsyncClient, httpx.AsyncClient]:
            with routes() as api:
                plan = api.get(f"{URL}/api/households/mealplans").mock(return_value=response(body={"items": []}))
                await mealie.get_meal_plan("2026-11-02", "2026-11-08")
                first = http_client.client("mealie")
                await auth.set_secret("MEALIE_API_TOKEN", "nieuw-token")
                await mealie.get_meal_plan("2026-11-09", "2026-11-15")
                second = http_client.client("mealie")
            return [call.request for call in plan.calls], first, second

        requests, first, second = run(two_weeks())
        self.assertEqual([request.headers["Authorization"] for request in requests], [f"Bearer {TOKEN}", "Bearer nieuw-token"])
        self.assertIs(first, second, "geen nieuwe client voor een nieuw token")
        self.assertNotIn("Authorization", first.headers, "het token zit niet in de client")
        self.assertEqual(requests[0].headers["User-Agent"], http_client.DEFAULT_USER_AGENT, "Mealie krijgt de vaste User-Agent")

    def test_each_request_reads_address_and_token_once(self):
        run(auth.set_secret("MEALIE_URL", URL))
        run(auth.set_secret("MEALIE_API_TOKEN", TOKEN))
        real = mealie._config
        calls = (
            ("weekmenu", lambda: mealie.get_meal_plan("2026-11-02", "2026-11-08")),
            ("recept", lambda: mealie.get_recipe("pasta")),
            ("eerste dag", mealie.first_day_of_week),
            ("zoeken op bron", lambda: mealie.find_recipe_by_source("https://example.com/r")),
        )
        for label, call in calls:
            mealie._week_start_cache.clear()
            with self.subTest(label), routes() as api, \
                    mock.patch.object(mealie, "_config", new_callable=mock.AsyncMock, side_effect=real) as config:
                api.get(f"{URL}/api/households/mealplans").mock(return_value=response(body={"items": []}))
                api.get(f"{URL}/api/recipes/pasta").mock(return_value=response(body={"recipeIngredient": []}))
                api.get(PREFERENCES).mock(return_value=preferences())
                api.get(f"{URL}/api/recipes").mock(return_value=response(body={"items": []}))
                run(call())
                self.assertEqual(config.await_count, 1, "adres en token een keer per verzoek")
                self.assertEqual(api.calls.last.request.headers["Authorization"], f"Bearer {TOKEN}")


class RecipePageUrlTest(MealieTestCase):
    """Mealie zet de groep in het adres van een recept: /g/<groep>/r/<recept>, niet /recipe/<recept>."""

    def setUp(self):
        super().setUp()
        run(auth.set_secret("MEALIE_URL", URL))
        run(auth.set_secret("MEALIE_API_TOKEN", TOKEN))
        mealie._group_slug_cache.clear()
        self.addCleanup(mealie._group_slug_cache.clear)

    def test_the_recipe_link_has_the_group_of_the_user(self):
        with routes() as api:
            get = api.get(SELF).mock(return_value=response(body={"username": "helmer", "groupSlug": "zb"}))
            self.assertEqual(
                run(mealie.recipe_page_url("witlof-met-ham-en-kaas-uit-de-oven")), f"{URL}/g/zb/r/witlof-met-ham-en-kaas-uit-de-oven"
            )
        self.assertEqual(get.calls.last.request.headers["Authorization"], f"Bearer {TOKEN}", "met het token van de koppeling")

    def test_the_group_is_remembered_so_a_list_of_recipes_costs_one_request(self):
        async def links() -> list[str]:
            return [await mealie.recipe_page_url(f"recept-{n}") for n in range(50)]

        with routes() as api:
            get = api.get(SELF).mock(return_value=response(body={"groupSlug": "zb"}))
            found = run(links())
        self.assertEqual(get.call_count, 1)
        self.assertEqual(found[0], f"{URL}/g/zb/r/recept-0")

    def test_an_older_mealie_without_a_group_keeps_the_old_address(self):
        with routes() as api:
            api.get(SELF).mock(return_value=response(body={"username": "helmer"}))
            self.assertEqual(run(mealie.recipe_page_url("pasta")), f"{URL}/recipe/pasta")

    def test_a_failing_lookup_falls_back_and_is_retried_later(self):
        with routes() as api:
            get = api.get(SELF).mock(side_effect=httpx.ConnectError("weg"))
            self.assertEqual(run(mealie.recipe_page_url("pasta")), f"{URL}/recipe/pasta")
            self.assertEqual(run(mealie.recipe_page_url("soep")), f"{URL}/recipe/soep")
            self.assertEqual(get.call_count, 1, "binnen een minuut niet opnieuw proberen")
        for status in (401, 500):
            mealie._group_slug_cache.clear()
            with routes() as api:
                api.get(SELF).mock(return_value=response(status=status))
                self.assertEqual(run(mealie.recipe_page_url("pasta")), f"{URL}/recipe/pasta", status)
        mealie._group_slug_cache.clear()
        with routes() as api:
            api.get(SELF).mock(return_value=response(json_error=True))
            self.assertEqual(run(mealie.recipe_page_url("pasta")), f"{URL}/recipe/pasta")
        # Na de wachttijd lukt het wel.
        mealie._group_slug_cache[URL] = (0, None)
        with routes() as api:
            api.get(SELF).mock(return_value=response(body={"groupSlug": "zb"}))
            self.assertEqual(run(mealie.recipe_page_url("pasta")), f"{URL}/g/zb/r/pasta")

    def test_without_a_link_there_is_no_request(self):
        with mock.patch.object(mealie, "_endpoint", new_callable=mock.AsyncMock, return_value=("", TOKEN)), routes() as api:
            get = api.get(SELF)
            self.assertIsNone(run(mealie.group_slug()))
        self.assertFalse(get.called)

    def test_the_group_is_per_mealie_address(self):
        with routes() as api:
            api.get(SELF).mock(return_value=response(body={"groupSlug": "zb"}))
            self.assertEqual(run(mealie.group_slug()), "zb")
        mealie._group_slug_cache[URL] = (mealie._group_slug_cache[URL][0], "zb")
        with mock.patch.object(mealie, "_endpoint", new_callable=mock.AsyncMock, return_value=("https://andere.example", TOKEN)), routes() as api:
            api.get("https://andere.example/api/users/self").mock(return_value=response(body={"groupSlug": "home"}))
            self.assertEqual(run(mealie.group_slug()), "home")


class UnreadableDatabaseTest(MealieTestCase):
    def setUp(self):
        super().setUp()
        mealie._db_warned = False
        self.addCleanup(setattr, mealie, "_db_warned", False)

    def test_environment_configuration_survives_a_broken_database(self):
        env = {"MEALIE_URL": "https://env.example.nl", "MEALIE_API_TOKEN": "env-token"}
        with mock.patch.dict(os.environ, env), \
                mock.patch.object(mealie.auth, "get_secret", new_callable=mock.AsyncMock, side_effect=OSError("read-only")):
            self.assertEqual(run(mealie._config()), ("https://env.example.nl", "env-token", "env"))
            self.assertEqual(run(mealie.mealie_url()), "https://env.example.nl")
            run(mealie.ensure_configured())

    def test_sqlite_errors_are_treated_the_same(self):
        import sqlite3
        env = {"MEALIE_URL": "https://env.example.nl", "MEALIE_API_TOKEN": "env-token"}
        with mock.patch.dict(os.environ, env), \
                mock.patch.object(mealie.auth, "get_secret", new_callable=mock.AsyncMock, side_effect=sqlite3.OperationalError("no such table")):
            self.assertEqual(run(mealie._config())[2], "env")

    def test_without_environment_it_is_simply_not_linked(self):
        with mock.patch.object(mealie.auth, "get_secret", new_callable=mock.AsyncMock, side_effect=OSError("read-only")):
            self.assertEqual(run(mealie._config()), ("", "", None))
            self.assertFalse(run(mealie.status())["configured"])

    def test_warns_once_not_on_every_call(self):
        async def five_times() -> None:
            for _ in range(5):
                await mealie._config()

        with mock.patch.object(mealie.auth, "get_secret", new_callable=mock.AsyncMock, side_effect=OSError("read-only")), \
                self.assertLogs("mealieshopper.mealie", level="WARNING") as logs:
            run(five_times())
        self.assertEqual(len(logs.records), 1)
        self.assertIn("omgevingsvariabelen", logs.output[0])


class CheckConnectionTest(MealieTestCase):
    def check(self, users, about=None):
        """`users` en `about` zijn het antwoord (of de fout) van /api/users/self en /api/app/about."""
        with routes() as api:
            first = api.get(SELF).mock(side_effect=[users])
            second = api.get(ABOUT).mock(side_effect=[about if about is not None else response(body={"version": "v2.4.1"})])
            return run(mealie.check_connection(URL, TOKEN)), first, second

    def error_from(self, users):
        with routes() as api:
            api.get(SELF).mock(side_effect=users)
            with self.assertRaises(RuntimeError) as ctx:
                run(mealie.check_connection(URL, TOKEN))
        return str(ctx.exception)

    def test_success_returns_user_and_version(self):
        result, first, _ = self.check(response(body={"username": "helmer", "email": "x@y.nl"}))
        self.assertEqual(result, {"username": "helmer", "version": "v2.4.1"})
        self.assertEqual(first.calls.last.request.headers["Authorization"], f"Bearer {TOKEN}")
        self.assertEqual(first.calls.last.request.headers["Accept"], "application/json")

    def test_version_is_optional(self):
        result, _, _ = self.check(response(body={"username": "helmer"}), httpx.ConnectError("weg"))
        self.assertEqual(result, {"username": "helmer", "version": None})

    def test_token_is_only_sent_to_the_users_endpoint(self):
        _, _, about = self.check(response(body={"username": "helmer"}))
        self.assertNotIn("Authorization", about.calls.last.request.headers)

    def test_rejected_token(self):
        for status in (401, 403):
            self.assertIn("weigert de token", self.error_from([response(status)]))

    def test_wrong_address(self):
        self.assertIn("geen Mealie-API", self.error_from([response(404)]))

    def test_redirect_names_the_target_and_is_not_followed(self):
        with routes() as api:
            api.get(SELF).mock(return_value=response(301, headers={"Location": "https://elders.example.nl/api/users/self"}))
            elsewhere = api.get("https://elders.example.nl/api/users/self").mock(return_value=response(body={"username": "x"}))
            with self.assertRaises(RuntimeError) as ctx:
                run(mealie.check_connection(URL, TOKEN))
        self.assertIn("stuurt door naar https://elders.example.nl/api/users/self", str(ctx.exception))
        self.assertFalse(elsewhere.called, "een doorverwijzing wordt niet gevolgd: het token gaat niet naar een ander adres")
        self.assertIn("stuurt door", self.error_from([response(302)]))

    def test_server_error(self):
        self.assertIn("(500)", self.error_from([response(500, text="kapot")]))

    def test_not_json(self):
        self.assertIn("geen JSON", self.error_from([response(json_error=True)]))

    def test_json_that_is_not_mealie(self):
        self.assertIn("lijkt geen Mealie", self.error_from([response(body={"hello": "world"})]))
        self.assertIn("lijkt geen Mealie", self.error_from([response(body=["lijst"])]))

    def test_an_untrusted_certificate_is_named(self):
        # Een echte TLS-handdruk (respx vervangt de oorzaak van een fout, en juist daaraan herkennen we ssl).
        async def connect() -> str:
            async def answer(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
                writer.close()

            with tempfile.TemporaryDirectory() as folder:
                server = await asyncio.start_server(answer, "127.0.0.1", 0, ssl=self_signed(folder))
            port = server.sockets[0].getsockname()[1]
            try:
                with self.assertRaises(RuntimeError) as ctx:
                    await mealie.check_connection(f"https://127.0.0.1:{port}", TOKEN)
            finally:
                server.close()
                await server.wait_closed()
            return str(ctx.exception)

        self.assertIn("certificaat", run(connect()))

    def test_network_problems_have_clear_messages(self):
        self.assertIn("time-out", self.error_from(httpx.ReadTimeout("traag")))
        self.assertIn("time-out", self.error_from(httpx.ConnectTimeout("traag")))
        for dropped in (httpx.ConnectError("refused"), httpx.RemoteProtocolError("weg"), httpx.ReadError("reset")):
            self.assertIn("niet bereikbaar", self.error_from(dropped), type(dropped).__name__)
        self.assertIn("mislukt", self.error_from(httpx.UnsupportedProtocol("gopher")))

    def test_error_messages_never_contain_the_token(self):
        for side_effect in ([response(401, text=TOKEN)], httpx.ConnectError(TOKEN)):
            self.assertNotIn(TOKEN, self.error_from(side_effect))


class LinkTest(MealieTestCase):
    def link(self, url=URL, token=TOKEN, users=None):
        """Koppel met de routes van een werkende Mealie, of met alleen `users` als antwoord van /api/users/self."""
        with routes() as api:
            if users is None:
                mealie_ok(api)
            else:
                api.get(f"{mealie.normalize_url(url)}/api/users/self").mock(return_value=users)
            return run(mealie.link(url, token))

    def test_saves_encrypted_and_reports_connected(self):
        result = self.link("mealie.example.nl/", f"  {TOKEN}  ")
        self.assertEqual((result["connected"], result["verified"], result["source"]), (True, True, "ui"))
        self.assertEqual((result["url"], result["username"], result["version"]), (URL, "helmer", "v2.4.1"))
        self.assertEqual(run(auth.get_secret("MEALIE_URL")), URL)
        self.assertEqual(run(auth.get_secret("MEALIE_API_TOKEN")), TOKEN)
        self.assertNotIn(TOKEN, repr(result))
        import sqlite3
        raw = sqlite3.connect(auth.db_path()).execute(
            "SELECT value FROM app_settings WHERE key='MEALIE_API_TOKEN'"
        ).fetchone()[0]
        self.assertNotEqual(raw, TOKEN, "token staat leesbaar in de database")
        self.assertNotIn(TOKEN, raw)

    def test_nothing_is_saved_when_the_check_fails(self):
        with self.assertRaises(RuntimeError):
            self.link(users=response(401))
        self.assertEqual(run(auth.get_secret("MEALIE_URL")), "")
        self.assertEqual(run(auth.get_secret("MEALIE_API_TOKEN")), "")

    def test_a_failed_relink_keeps_the_working_link(self):
        self.link()
        with self.assertRaises(RuntimeError):
            self.link("https://ander.example.nl", "nieuw", users=response(404))
        self.assertEqual(run(mealie._config()), (URL, TOKEN, "ui"))

    def test_input_validation_before_any_request(self):
        with routes() as api:
            anything = api.route()
            for url, token in (("", TOKEN), (URL, ""), (URL, None), (URL, "twee woorden"), (URL, "regel\neinde"),
                               ("ftp://x.nl", TOKEN)):
                with self.assertRaises(ValueError, msg=(url, token)):
                    run(mealie.link(url, token))
        self.assertFalse(anything.called)


class StatusTest(MealieTestCase):
    def test_not_configured(self):
        data = run(mealie.status())
        self.assertEqual((data["configured"], data["url"], data["source"]), (False, "", None))
        self.assertNotIn("connected", data)

    def test_never_contains_the_token(self):
        run(auth.set_secret("MEALIE_URL", URL))
        run(auth.set_secret("MEALIE_API_TOKEN", TOKEN))
        with routes() as api:
            api.get(PREFERENCES).mock(return_value=preferences())
            self.assertNotIn(TOKEN, repr(run(mealie.status())))
        mealie._week_start_cache.clear()
        with routes() as api:
            mealie_ok(api)
            self.assertNotIn(TOKEN, repr(run(mealie.status(verify=True))))

    def test_verify_success_and_failure(self):
        run(auth.set_secret("MEALIE_URL", URL))
        run(auth.set_secret("MEALIE_API_TOKEN", TOKEN))
        with routes() as api:
            mealie_ok(api)
            data = run(mealie.status(verify=True))
        self.assertEqual((data["connected"], data["username"], data["version"]), (True, "helmer", "v2.4.1"))
        with routes() as api:
            api.get(SELF).mock(return_value=response(401))
            api.get(PREFERENCES).mock(return_value=response(401))
            data = run(mealie.status(verify=True))
        self.assertFalse(data["connected"])
        self.assertEqual(data["weekStart"]["source"], "default", "zonder antwoord van Mealie begint de week op maandag")
        self.assertIn("weigert de token", data["error"])

    def test_verify_without_configuration_makes_no_request(self):
        with routes() as api:
            anything = api.route()
            self.assertFalse(run(mealie.status(verify=True))["configured"])
        self.assertFalse(anything.called)

    def test_clear_falls_back_to_the_environment(self):
        run(auth.set_secret("MEALIE_URL", URL))
        run(auth.set_secret("MEALIE_API_TOKEN", TOKEN))
        with mock.patch.dict(os.environ, {"MEALIE_URL": "https://env.example.nl", "MEALIE_API_TOKEN": "env-token"}), routes() as api:
            api.get(PREFERENCES).mock(return_value=preferences())
            api.get("https://env.example.nl/api/households/preferences").mock(return_value=preferences())
            self.assertEqual(run(mealie.status())["source"], "ui")
            data = run(mealie.clear())
            self.assertEqual((data["source"], data["url"]), ("env", "https://env.example.nl"))
        self.assertEqual(run(mealie.clear())["configured"], False)


class AuthKeyTest(MealieTestCase):
    """De sleutel voor opgeslagen gegevens hangt niet van MEALIE_URL of de koppeling af."""

    def test_linking_in_the_ui_never_changes_the_encryption_key(self):
        # De sleutel voor opgeslagen gegevens (ook het AH-token) mag niet van de UI-koppeling afhangen.
        with mock.patch.dict(os.environ, {"MEALIE_URL": "https://env.example.nl"}):
            os.environ.pop("MEALIESHOPPER_AUTH_SECRET", None)
            auth._master_secrets.clear()
            before = run(auth.master_secret())
            run(auth.set_secret("AH_REFRESH_TOKEN", "ah-token"))
            with routes() as api:
                mealie_ok(api)
                run(mealie.link(URL, TOKEN))
            self.assertEqual(run(auth.master_secret()), before)
            self.assertEqual(run(auth.get_secret("AH_REFRESH_TOKEN")), "ah-token")
            run(mealie.clear())
            self.assertEqual(run(auth.master_secret()), before)

    def test_delete_secret(self):
        run(auth.set_secret("IETS", "waarde"))
        run(auth.delete_secret("IETS"))
        run(auth.delete_secret("IETS"))  # herhaling is geen fout
        self.assertEqual(run(auth.get_secret("IETS")), "")

    def test_mealie_secrets_are_not_purged_at_startup(self):
        self.assertNotIn("MEALIE_URL", auth.OBSOLETE_SECRET_KEYS)
        self.assertNotIn("MEALIE_API_TOKEN", auth.OBSOLETE_SECRET_KEYS)
        run(auth.set_secret("MEALIE_URL", URL))
        run(auth.set_secret("MEALIE_API_TOKEN", TOKEN))
        create_app()
        self.assertEqual(run(auth.get_secret("MEALIE_API_TOKEN")), TOKEN)


class RoutesTest(MealieTestCase):
    async def test_status_route(self):
        data = (await self.client.get("/api/mealie/status")).get_json()
        self.assertFalse(data["configured"])

    async def test_link_unlink_roundtrip(self):
        with routes() as api:
            mealie_ok(api)
            ok = await self.client.post("/api/mealie/auth", json={"url": "mealie.example.nl", "token": TOKEN})
            self.assertEqual(ok.status_code, 200)
            self.assertNotIn(TOKEN, ok.get_data(as_text=True))
            self.assertEqual(ok.get_json()["url"], URL)
            status = await self.client.get("/api/mealie/status")
        self.assertEqual((status.get_json()["configured"], status.get_json()["source"]), (True, "ui"))
        self.assertNotIn(TOKEN, status.get_data(as_text=True))
        gone = await self.client.post("/api/mealie/auth/logout")
        self.assertEqual(gone.status_code, 200)
        self.assertFalse(gone.get_json()["configured"])

    async def test_errors_are_400_with_a_message(self):
        with routes() as api:
            api.get(SELF).mock(return_value=response(401))
            bad = await self.client.post("/api/mealie/auth", json={"url": URL, "token": TOKEN})
        self.assertEqual(bad.status_code, 400)
        self.assertIn("weigert de token", bad.get_json()["error"])
        self.assertNotIn(TOKEN, bad.get_data(as_text=True))
        for payload in ({}, {"url": URL}, {"token": TOKEN}, {"url": "ftp://x", "token": TOKEN}):
            self.assertEqual((await self.client.post("/api/mealie/auth", json=payload)).status_code, 400, payload)

    async def test_routes_need_a_session_when_auth_is_on(self):
        with mock.patch.dict(os.environ, {"PASSKEY_AUTH_ENABLED": "true"}):
            for method, path in (("get", "/api/mealie/status"), ("post", "/api/mealie/auth"),
                                 ("post", "/api/mealie/auth/logout")):
                self.assertEqual((await getattr(self.client, method)(path)).status_code, 401, path)

    def test_ui_configuration_drives_imports_and_the_planner(self):
        run(auth.set_secret("MEALIE_URL", URL))
        run(auth.set_secret("MEALIE_API_TOKEN", TOKEN))
        with routes() as api:
            sent = api.post(f"{URL}/api/recipes/create/url").mock(return_value=response(201, "stamppot"))
            api.get(f"{URL}/api/recipes/stamppot").mock(return_value=response(body={"recipeIngredient": []}))
            slug = run(mealie.import_from_url("https://example.com/recept"))
        self.assertEqual(slug, "stamppot")
        self.assertEqual(sent_url(sent.calls.last), f"{URL}/api/recipes/create/url")
        self.assertEqual(sent.calls.last.request.headers["Authorization"], f"Bearer {TOKEN}")

    async def test_page_shows_the_tab_and_the_notice(self):
        html = (await self.client.get("/")).get_data(as_text=True)
        for fragment in ('data-subtab="settings-link"', 'id="mealie-login-box"', 'id="mealie-form"', 'id="mealie-token" type="password"',
                         'class="message warning message--action" id="mealie-setup"', 'id="check-mealie"',
                         'id="mealie-unlink"'):
            self.assertIn(fragment, html, fragment)


if __name__ == "__main__":
    unittest.main()
