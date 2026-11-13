import asyncio
import os
import re
import shutil
import tempfile
import threading
import types
import unittest
from pathlib import Path
from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from unittest import mock

import pytest

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

from mealieshopper import auth, auth_password, factory_reset  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402
from test_users import FakeAuthenticator, b64  # noqa: E402
from helpers import AppTestCase  # noqa: E402

if TYPE_CHECKING:  # niet bij de import: pytest zou TestClientProtocol voor een testklasse aanzien
    from quart.typing import TestClientProtocol

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
JS = (ROOT / "static" / "system.js").read_text(encoding="utf-8")
PASSWORD = "koffie-met-melk-42"
ANNA = "een-heel-ander-wachtwoord"
URL = "/api/system/factory-reset"


class FactoryResetCase(AppTestCase):
    async def asyncSetUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name)
        env = mock.patch.dict(os.environ, {"PASSKEY_AUTH_ENABLED": "true", "MEALIESHOPPER_DATA_DIR": tmp.name})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("MEALIESHOPPER_AUTH_SECRET", None)
        os.environ.pop("JWT_SECRET", None)
        auth.forget_runtime_state()
        self.addCleanup(auth.forget_runtime_state)
        cost = mock.patch.object(auth_password, "SCRYPT_N", 2**4)
        cost.start()
        self.addCleanup(cost.stop)
        self.app = create_app()
        self.admin = self.client_for(self.app)
        await self.admin.post("/api/auth/password", json={"username": "admin", "password": PASSWORD})

    def files(self):
        return sorted(path.name for path in self.data.iterdir())

    async def reset(self, body=None, client=None):
        return await (client or self.admin).post(URL, json={"confirm": "RESET", **(body or {})})


class PasswordResetTest(FactoryResetCase):
    async def test_wipes_the_database_and_the_secret_and_logs_out(self):
        self.assertIn("auth.sqlite3", self.files())
        self.assertIn("secret.key", self.files())
        response = await self.reset({"password": PASSWORD})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json(), {"status": "ok", "restarting": False})
        self.assertEqual(self.files(), [])
        self.assertIn("ms_session=;", response.headers.get("Set-Cookie", ""))

    async def test_a_rejected_shared_link_goes_with_the_database(self):
        from mealieshopper import shared_links

        cherries = {shared_links.name_keys.key("AH Doosjevol kersen"): shared_links.SharedLink("ah doosjevol kersen", "1234", "AH Doosje met kersen", "250 g")}
        with mock.patch.object(shared_links, "load", return_value=cherries):
            self.assertTrue(await shared_links.reject("ah", "AH Doosjevol kersen", "1234"))
            response = await self.reset({"password": PASSWORD})
            self.assertEqual(response.status_code, 200, response.get_json())
            auth.forget_runtime_state()
            await auth.init_db()
            self.assertTrue(await shared_links.reject("ah", "AH Doosjevol kersen", "1234"), "na de reset is de afwijzing weg")

    async def test_the_loose_names_go_with_the_database(self):
        from contextlib import closing

        from helpers import db
        from mealieshopper import loose_names

        with closing(db(loose_names)) as conn, conn:
            conn.execute(
                "INSERT INTO loose_names (store, key, name, checked_at, outcome, candidates, proposal) "
                "VALUES ('jumbo', '~kersen', 'Jumbo Kersen', '2026-11-11T01:00:00+00:00', 'candidates', '[]', NULL)"
            )
        self.assertTrue(await loose_names.reject("jumbo", "~kersen"))
        response = await self.reset({"password": PASSWORD})
        self.assertEqual(response.status_code, 200, response.get_json())
        auth.forget_runtime_state()
        await auth.init_db()
        self.assertFalse(await loose_names.reject("jumbo", "~kersen"), "na de reset is de stand weg")
        with closing(db(loose_names)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM loose_names").fetchone()[0], 0)

    async def test_a_factory_reset_drops_the_cached_catalogue_and_list(self):
        # Review Focus 4 van taak 9: na het wissen staat er niets van de oude database meer in het geheugen.
        from mealieshopper import prices

        self.assertEqual((await self.admin.get("/api/offers?store=ah")).status_code, 200)
        await prices.warm_catalogues(["ah"])
        cache = self.app.extensions["mealieshopper_offers_cache"]
        self.assertTrue(prices._CATALOGUES)
        self.assertTrue(cache._entries)
        response = await self.reset({"password": PASSWORD})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(prices._CATALOGUES, {})
        self.assertEqual(dict(cache._entries), {})

    async def test_the_image_cache_folder_goes_too(self):
        cached = self.data / "images" / "ab" / "abcd.webp"
        cached.parent.mkdir(parents=True)
        cached.write_bytes(b"x")
        removed = await factory_reset.wipe_data()
        self.assertIn("images", removed)
        self.assertFalse((self.data / "images").exists())
        self.assertNotIn("images", await factory_reset.wipe_data(), "een tweede keer is er niets meer te verwijderen")

    async def test_staged_backups_and_price_archives_go_too(self):
        # #276: de reset haalde alleen `.restore-*` weg. Een klaargezet prijsarchief (`.price-archive-*`) bleef staan, met de
        # gegevens van de oude installatie erin, tot iemand een nieuw archief klaarzette.
        from mealieshopper import backup, price_archive

        staged = []
        for prefix in (backup.STAGE_PREFIX, price_archive.STAGE_PREFIX):
            for suffix in (".part", ".upload", ".rows", ".rows.job"):  # een upload in stukken, klaargezet, en na een harde stop
                name = f"{prefix}tok3n{suffix}"
                (self.data / name).write_bytes(b"gegevens")
                staged.append(name)
        elsewhere = self.data / "export" / f"{price_archive.STAGE_PREFIX}tok3n.rows"  # alleen direct in de data-map
        elsewhere.parent.mkdir()
        elsewhere.write_bytes(b"van iemand anders")
        removed = await factory_reset.wipe_data()
        self.assertTrue(set(staged) <= set(removed), sorted(set(staged) - set(removed)))
        self.assertEqual([name for name in self.files() if name.startswith(".")], [])
        self.assertTrue(elsewhere.exists())

    def test_every_staging_prefix_of_the_app_is_known_to_the_reset(self):
        # Een nieuw soort klaargezette upload (een eigen STAGE_PREFIX) moet de reset ook opruimen: anders blijft hij staan.
        prefixes = set()
        for path in (ROOT / "mealieshopper").glob("*.py"):
            prefixes.update(re.findall(r'^STAGE_PREFIX\s*=\s*"([^"]+)"', path.read_text(encoding="utf-8"), re.M))
        self.assertTrue(prefixes, "geen enkel STAGE_PREFIX gevonden: is de naam veranderd?")
        self.assertEqual(prefixes, set(factory_reset.STAGED_PREFIXES))

    async def test_the_image_cache_is_removed_off_the_event_loop(self):
        """Duizenden bestanden wissen houdt de event loop niet vast: shutil.rmtree loopt in een thread."""
        (self.data / "images").mkdir()
        loop_thread: int = threading.get_ident()
        threads: list[int] = []

        def rmtree(path: Any, ignore_errors: bool = False) -> None:
            threads.append(threading.get_ident())
            shutil.rmtree(path, ignore_errors=ignore_errors)

        with mock.patch.object(factory_reset, "shutil", types.SimpleNamespace(rmtree=rmtree)):
            self.assertIn("images", await factory_reset.wipe_data())
        self.assertEqual(len(threads), 1)
        self.assertNotEqual(threads[0], loop_thread)

    async def test_the_app_starts_over_as_a_new_installation_without_a_restart(self):
        await self.reset({"password": PASSWORD})
        client = self.client_for(self.app)
        status = (await client.get("/api/auth/status")).get_json()
        self.assertFalse(status["configured"])
        self.assertFalse(status["authenticated"])
        self.assertTrue((await client.get("/api/onboarding/status")).get_json()["needed"])
        self.assertFalse(await auth.credentials_exist())

    async def test_the_old_session_and_password_no_longer_work(self):
        old_cookie = self.admin.get_cookie("ms_session").value
        await self.reset({"password": PASSWORD})
        client = self.client_for(self.app)
        client.set_cookie("ms_session", old_cookie)
        self.assertFalse((await client.get("/api/auth/status")).get_json()["authenticated"])
        login = await client.post("/api/auth/password/login", json={"username": "admin", "password": PASSWORD})
        self.assertGreaterEqual(login.status_code, 400)

    async def test_a_new_owner_can_be_set_up_afterwards(self):
        await self.reset({"password": PASSWORD})
        client = self.client_for(self.app)
        self.assertEqual((await client.post("/api/auth/password", json={"username": "beheer", "password": ANNA})).status_code, 200)
        self.assertEqual((await client.get("/api/auth/status")).get_json()["username"], "beheer")

    async def test_other_users_are_removed_too(self):
        token = (await self.admin.post("/api/users/invites", json={"username": "anna"})).get_json()["token"]
        anna = self.client_for(self.app)
        await anna.post("/api/invites/password", json={"token": token, "password": ANNA})
        await self.reset({"password": PASSWORD})
        login = await self.client_for(self.app).post("/api/auth/password/login", json={"username": "anna", "password": ANNA})
        self.assertGreaterEqual(login.status_code, 400)

    async def test_nothing_is_wiped_without_the_typed_word(self):
        for body in ({}, {"confirm": "reset"}, {"confirm": "ja"}):
            response = await self.admin.post(URL, json={"password": PASSWORD, **body})
            self.assertEqual(response.status_code, 400)
        self.assertIn("auth.sqlite3", self.files())

    async def test_a_session_alone_is_not_enough(self):
        for body in ({}, {"password": ""}, {"password": "verkeerd-wachtwoord"}):
            self.assertIn((await self.reset(body)).status_code, (400, 403))
        self.assertIn("auth.sqlite3", self.files())
        self.assertTrue((await self.admin.get("/api/auth/status")).get_json()["authenticated"])

    async def test_wrong_attempts_are_limited_like_logging_in(self):
        for _ in range(auth_password.ATTEMPTS_PER_WINDOW):
            await self.reset({"password": "verkeerd-wachtwoord"})
        locked = await self.reset({"password": PASSWORD})
        self.assertEqual(locked.status_code, 429)
        self.assertIn("auth.sqlite3", self.files())

    async def test_with_two_step_verification_the_code_is_needed_too(self):
        secret = await self.enable_mfa()
        self.assertEqual((await self.reset({"password": PASSWORD})).status_code, 400)
        self.assertEqual((await self.reset({"password": PASSWORD, "code": "000000"})).status_code, 403)
        self.assertIn("auth.sqlite3", self.files())
        code = auth_password.totp_code(secret, at=__import__("time").time() + auth_password.TOTP_PERIOD)
        response = await self.reset({"password": PASSWORD, "code": code})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self.files(), [])

    async def enable_mfa(self):
        start = (await self.admin.post("/api/auth/mfa/start", json={})).get_json()
        secret = start["secret"].replace(" ", "")
        code = auth_password.totp_code(secret)
        done = await self.admin.post("/api/auth/mfa/confirm", json={"code": code})
        self.assertEqual(done.status_code, 200, done.get_json())
        return secret


class AccessTest(FactoryResetCase):
    async def test_not_logged_in_gets_nothing(self):
        client = self.client_for(self.app)
        self.assertEqual((await client.get(URL)).status_code, 401)
        self.assertEqual((await client.post(URL, json={"confirm": "RESET", "password": PASSWORD})).status_code, 401)
        self.assertEqual((await client.post(URL + "/options", json={})).status_code, 401)
        self.assertIn("auth.sqlite3", self.files())

    async def test_a_regular_user_is_refused(self):
        token = (await self.admin.post("/api/users/invites", json={"username": "anna"})).get_json()["token"]
        anna = self.client_for(self.app)
        await anna.post("/api/invites/password", json={"token": token, "password": ANNA})
        for response in (await anna.get(URL), await self.reset({"password": ANNA}, anna), await anna.post(URL + "/options", json={})):
            self.assertEqual(response.status_code, 403)
        self.assertIn("auth.sqlite3", self.files())

    async def test_info_tells_which_ways_of_confirming_exist(self):
        info = (await self.admin.get(URL)).get_json()
        self.assertEqual(info["phrase"], "RESET")
        self.assertTrue(info["authRequired"])
        self.assertEqual(info["methods"], {"password": True, "mfa": False, "passkey": False})
        self.assertFalse(info["restart"])


class PasskeyResetTest(FactoryResetCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.phone = FakeAuthenticator()
        options = (await self.admin.post("/api/auth/register/options", json={"credentialName": "telefoon"})).get_json()
        verify = await self.admin.post(
            "/api/auth/register/verify",
            json={"credentialName": "telefoon", "credential": self.phone.create(options["options"]["challenge"])},
        )
        self.assertEqual(verify.status_code, 200, verify.get_json())

    async def challenge(self, client=None):
        response = await (client or self.admin).post(URL + "/options", json={})
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()["options"]

    async def test_info_lists_the_passkey(self):
        self.assertTrue((await self.admin.get(URL)).get_json()["methods"]["passkey"])

    async def test_a_passkey_confirms_the_reset(self):
        options = await self.challenge()
        self.assertEqual([c["id"] for c in options["allowCredentials"]], [b64(self.phone.credential_id)])
        response = await self.reset({"credential": self.phone.get(options["challenge"])})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self.files(), [])

    async def test_the_challenge_works_once(self):
        options = await self.challenge()
        credential = self.phone.get(options["challenge"])
        self.assertEqual((await self.reset({"credential": {**credential, "response": {**credential["response"], "signature": "AAAA"}}})).status_code, 403)
        self.assertEqual((await self.reset({"credential": credential})).status_code, 400)  # de uitdaging is al gebruikt
        self.assertIn("auth.sqlite3", self.files())

    async def test_a_passkey_without_a_fresh_challenge_is_refused(self):
        self.assertEqual((await self.reset({"credential": self.phone.get("AAAA")})).status_code, 400)
        self.assertIn("auth.sqlite3", self.files())

    async def test_someone_elses_passkey_is_refused(self):
        stranger = FakeAuthenticator()
        options = await self.challenge()
        self.assertEqual((await self.reset({"credential": stranger.get(options["challenge"])})).status_code, 403)
        self.assertIn("auth.sqlite3", self.files())

    async def test_the_signature_is_checked_off_the_event_loop(self):
        threads: list[int] = []
        real: Callable[..., int] = auth._verify_assertion

        def spy(*args: Any) -> int:
            threads.append(threading.get_ident())
            return real(*args)

        options = await self.challenge()
        with mock.patch.object(auth, "_verify_assertion", side_effect=spy):
            response = await self.reset({"credential": self.phone.get(options["challenge"])})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(len(threads), 1)
        self.assertNotEqual(threads[0], threading.get_ident(), "de test draait in de thread van de event loop")

    async def test_the_word_is_still_needed(self):
        options = await self.challenge()
        response = await self.admin.post(URL, json={"credential": self.phone.get(options["challenge"])})
        self.assertEqual(response.status_code, 400)
        self.assertIn("auth.sqlite3", self.files())


class WithoutLoginTest(AppTestCase):
    async def test_without_login_only_the_word_is_needed(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {"PASSKEY_AUTH_ENABLED": "false", "MEALIESHOPPER_DATA_DIR": tmp}
        ):
            auth.forget_runtime_state()
            self.addCleanup(auth.forget_runtime_state)
            client = self.client_for(create_app())
            self.assertFalse((await client.get(URL)).get_json()["authRequired"])
            self.assertEqual((await client.post(URL, json={})).status_code, 400)
            await auth.ensure_db()
            self.assertTrue((Path(tmp) / "auth.sqlite3").exists())
            self.assertEqual((await client.post(URL, json={"confirm": "RESET"})).status_code, 200)
            self.assertEqual(sorted(path.name for path in Path(tmp).iterdir()), [])


@pytest.fixture
async def admin(data_dir: Path, monkeypatch: pytest.MonkeyPatch, client: "TestClientProtocol") -> "TestClientProtocol":
    """De async testclient, ingelogd als beheerder met een wachtwoord."""
    monkeypatch.setenv("PASSKEY_AUTH_ENABLED", "true")
    monkeypatch.setattr(auth_password, "SCRYPT_N", 2**4)
    response = await client.post("/api/auth/password", json={"username": "admin", "password": PASSWORD})
    assert response.status_code == 200, await response.get_json()
    return client


@pytest.fixture
def in_a_container(monkeypatch: pytest.MonkeyPatch) -> mock.Mock:
    """Een container (herstart mogelijk) met een nep-`restart`, zodat de test zichzelf geen SIGTERM stuurt."""
    restart = mock.Mock()
    monkeypatch.setattr(factory_reset, "restart_supported", lambda: True)
    monkeypatch.setattr(factory_reset, "restart", restart)
    return restart


async def test_in_a_container_the_restart_is_scheduled_on_the_loop_after_answering(
    admin: "TestClientProtocol", in_a_container: mock.Mock
) -> None:
    loop = asyncio.get_running_loop()
    with mock.patch.object(loop, "call_later", wraps=loop.call_later) as call_later:
        response = await admin.post(URL, json={"confirm": "RESET", "password": PASSWORD})
    assert await response.get_json() == {"status": "ok", "restarting": True}
    restarts = [call for call in call_later.call_args_list if in_a_container in call.args]
    assert restarts == [mock.call(factory_reset.RESTART_DELAY_SECONDS, in_a_container)]
    assert factory_reset.RESTART_DELAY_SECONDS == 1.5
    in_a_container.assert_not_called()  # eerst het antwoord, de herstart pas na de pauze


async def test_in_a_container_the_app_stops_itself_after_the_delay(
    admin: "TestClientProtocol", in_a_container: mock.Mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(factory_reset, "RESTART_DELAY_SECONDS", 0.05)
    response = await admin.post(URL, json={"confirm": "RESET", "password": PASSWORD})
    assert response.status_code == 200
    in_a_container.assert_not_called()
    await asyncio.sleep(0.2)
    in_a_container.assert_called_once_with()


async def test_outside_a_container_nothing_is_scheduled(admin: "TestClientProtocol", monkeypatch: pytest.MonkeyPatch) -> None:
    restart = mock.Mock()
    monkeypatch.setattr(factory_reset, "restart_supported", lambda: False)
    monkeypatch.setattr(factory_reset, "restart", restart)
    monkeypatch.setattr(factory_reset, "RESTART_DELAY_SECONDS", 0.01)
    response = await admin.post(URL, json={"confirm": "RESET", "password": PASSWORD})
    assert await response.get_json() == {"status": "ok", "restarting": False}
    await asyncio.sleep(0.05)
    restart.assert_not_called()


class RestartTest(FactoryResetCase):
    def test_restart_sends_sigterm_to_itself(self):
        with mock.patch.object(factory_reset.os, "kill") as kill:
            factory_reset.restart()
        kill.assert_called_once_with(os.getpid(), factory_reset.signal.SIGTERM)

    def test_restart_is_supported_with_dockerenv_or_ha_options(self):
        for present in ("/.dockerenv", "/data/options.json"):
            with self.subTest(present=present), mock.patch.object(
                factory_reset.Path, "exists", autospec=True, side_effect=lambda path, present=present: str(path) == present
            ):
                self.assertTrue(factory_reset.restart_supported())

    def test_outside_a_container_there_is_no_restart(self):
        with mock.patch.object(factory_reset.Path, "exists", autospec=True, return_value=False):
            self.assertFalse(factory_reset.restart_supported())


class PageTest(AppTestCase):
    def test_the_tab_is_called_system_and_holds_updates_and_clean_up(self):
        tab = re.search(r'<a[^>]*data-subtab="settings-system"[^>]*>(.*?)</a>', HTML, re.S).group(1)
        self.assertIn('data-i18n="settings.tab.system"', tab)
        panel = HTML[HTML.index('id="settings-system"'):HTML.index('<section id="about"')]
        self.assertLess(panel.index("update.title"), panel.index("system.cleanup_title"))
        self.assertLess(panel.index("system.cleanup_title"), panel.index("system.reset_title"))
        self.assertNotIn("settings-updates", HTML)

    def test_the_form_warns_about_the_consequences_and_asks_for_the_word_and_authentication(self):
        form = re.search(r'<form[^>]*id="reset-form"[^>]*>(.*?)</form>', HTML, re.S)
        self.assertIsNotNone(form)
        self.assertIn("hidden", re.search(r'<form[^>]*id="reset-form"[^>]*>', HTML).group(0))
        for ident in ("reset-phrase", "reset-password", "reset-code", "reset-submit", "reset-cancel", "reset-message"):
            self.assertIn(f'id="{ident}"', HTML)
        for key in ("system.reset_warning_title", "system.reset_loses_users", "system.reset_loses_data", "system.reset_after"):
            self.assertIn(key, form.group(1))

    def test_the_script_asks_for_a_last_confirmation_and_for_authentication(self):
        self.assertIn('await confirmDialog(t("system.reset_confirm"), { danger: true })', JS)
        self.assertIn("body.password", JS)
        self.assertIn("factoryResetPasskey()", JS)
        self.assertIn("system.js", HTML)


if __name__ == "__main__":
    unittest.main()
