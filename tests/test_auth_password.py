import asyncio
import hashlib
import os
import tempfile
import threading
import unittest
from collections.abc import Callable
from contextlib import closing
from typing import TYPE_CHECKING, Any
from unittest import mock

import pytest

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

from helpers import AppTestCase, db, run  # noqa: E402

from mealieshopper import auth, auth_password  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402

if TYPE_CHECKING:  # niet bij de import: pytest zou TestClientProtocol voor een testklasse aanzien
    from quart.typing import TestClientProtocol

PASSWORD = "koffie-met-melk-42"
OTHER = "een-heel-ander-wachtwoord"


class AuthPasswordTestCase(AppTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(
            os.environ,
            {"PASSKEY_AUTH_ENABLED": "true", "MEALIESHOPPER_DATA_DIR": tmp.name, "MEALIESHOPPER_AUTH_SECRET": "test-secret"},
        )
        env.start()
        self.addCleanup(env.stop)
        # Een lichte scrypt houdt de tests snel; verify leest de parameters uit de opgeslagen hash.
        cost = mock.patch.object(auth_password, "SCRYPT_N", 2**4)
        cost.start()
        self.addCleanup(cost.stop)
        self.app = create_app()
        self.client = self.client_for(self.app)

    async def post(self, path, body=None, client=None):
        return await (client or self.client).post(path, json=body or {})

    async def setup_password(self, client=None, username="admin", password=PASSWORD):
        return await self.post("/api/auth/password", {"username": username, "password": password}, client)

    def new_client(self):
        return self.client_for(self.app)

    async def enable_mfa(self, client=None):
        client = client or self.client
        started = (await self.post("/api/auth/mfa/start", client=client)).get_json()
        secret = started["secret"].replace(" ", "")
        res = await self.post("/api/auth/mfa/confirm", {"code": auth_password.totp_code(secret)}, client)
        return secret, res


class PasswordRulesTest(AppTestCase):
    def test_hash_is_salted_and_verifies(self):
        with mock.patch.object(auth_password, "SCRYPT_N", 2**4):
            first, second = auth_password.hash_password("geheim-wachtwoord"), auth_password.hash_password("geheim-wachtwoord")
            self.assertNotEqual(first, second)
            self.assertTrue(auth_password.verify_password("geheim-wachtwoord", first))
            self.assertFalse(auth_password.verify_password("geheim-wachtwoorD", first))
        self.assertNotIn("geheim", first)

    def test_garbage_hashes_never_verify(self):
        for stored in ("", "x", "scrypt$a$b$c$d$e", "md5$1$2$3$4$5", "scrypt$16$8$1$!!$!!"):
            self.assertFalse(auth_password.verify_password("x", stored), stored)

    def test_policy(self):
        v = auth_password.validate_password
        self.assertIsNone(v("koffie-met-melk-42", "admin"))
        self.assertIn("minstens 10", v("kort", "admin"))
        self.assertIn("voorspelbaar", v("aaaaaaaaaaaa", "admin"))
        self.assertIn("gebruikersnaam", v("mijn-ADMIN-wachtwoord", "admin"))
        self.assertIsNone(v("ab-cd-ef-gh-ij", "ab"), "korte gebruikersnamen tellen niet mee")
        self.assertIsNotNone(v("", ""))
        self.assertIsNotNone(v(None, ""))
        self.assertIn("maximaal", v("x1" * 200, ""))

    def test_username(self):
        self.assertEqual(auth_password.validate_username("  piet ")[0], "piet")
        self.assertIsNotNone(auth_password.validate_username("")[1])
        self.assertIsNotNone(auth_password.validate_username("a" * 65)[1])
        self.assertIsNotNone(auth_password.validate_username("a\nb")[1])


class TotpTest(AppTestCase):
    SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # RFC 6238: "12345678901234567890"

    def test_rfc_6238_vector(self):
        # De RFC geeft 94287082 (8 cijfers) op t=59; de laatste 6 cijfers zijn onze code.
        self.assertEqual(auth_password.totp_code(self.SECRET, at=59), "287082")
        self.assertEqual(auth_password.totp_code(self.SECRET, at=1111111109), "081804")

    def test_window_and_replay(self):
        code = auth_password.totp_code(self.SECRET, at=1000)
        counter = auth_password.check_totp(self.SECRET, code, 0, at=1000)
        self.assertEqual(counter, 1000 // 30)
        self.assertIsNotNone(auth_password.check_totp(self.SECRET, code, 0, at=1000 + 30), "een stap later mag nog")
        self.assertIsNone(auth_password.check_totp(self.SECRET, code, 0, at=1000 + 90), "te oud")
        self.assertIsNone(auth_password.check_totp(self.SECRET, code, counter, at=1000), "dezelfde stap werkt maar een keer")

    def test_bad_input(self):
        for code in ("", "12345", "1234567", "abcdef", None):
            self.assertIsNone(auth_password.check_totp(self.SECRET, code, 0), repr(code))
        self.assertIsNone(auth_password.check_totp("niet-base32!", "123456", 0))

    def test_spaces_in_the_code_are_fine(self):
        code = auth_password.totp_code(self.SECRET, at=5000)
        spaced = f"{code[:3]} {code[3:]}"
        self.assertIsNotNone(auth_password.check_totp(self.SECRET, spaced, 0, at=5000))

    def test_otpauth_uri(self):
        with mock.patch.dict(os.environ, {"RP_NAME": "Mealie Shopper"}):
            uri = auth_password.otpauth_uri(self.SECRET, "piet@thuis")
        self.assertTrue(uri.startswith("otpauth://totp/Mealie%20Shopper%3Apiet%40thuis?secret=" + self.SECRET))
        self.assertIn("issuer=Mealie%20Shopper", uri)
        self.assertIn("digits=6&period=30", uri)


class RecoveryCodeTest(AppTestCase):
    def test_codes_are_long_and_distinct(self):
        codes = auth_password.new_recovery_codes()
        self.assertEqual(len(codes), 8)
        self.assertEqual(len(set(codes)), 8)
        for code in codes:
            self.assertRegex(code, r"^[A-Z2-9]{4}(-[A-Z2-9]{4}){3}$")
            self.assertNotRegex(code, r"[01OI]")


class StatusTest(AuthPasswordTestCase):
    async def test_fresh_install_requires_setup(self):
        status = (await self.client.get("/api/auth/status")).get_json()
        self.assertTrue(status["enabled"])
        self.assertTrue(status["setupRequired"])
        self.assertFalse(status["authenticated"])
        self.assertEqual(status["methods"], {"passkey": False, "password": False})
        self.assertNotIn("mfaEnabled", status, "zonder sessie geen details")
        self.assertEqual((await self.client.get("/api/mealplan?start=a&end=b")).status_code, 401)


class SetupTest(AuthPasswordTestCase):
    async def test_first_password_logs_you_in(self):
        res = await self.setup_password()
        self.assertEqual(res.status_code, 200)
        self.assertIn("ms_session=", res.headers.get("Set-Cookie", ""))
        self.assertIn("HttpOnly", res.headers["Set-Cookie"])
        status = (await self.client.get("/api/auth/status")).get_json()
        self.assertTrue(status["authenticated"])
        self.assertEqual(status["username"], "admin")
        self.assertFalse(status["setupRequired"])
        self.assertTrue(status["passwordConfigured"])
        self.assertFalse(status["mfaEnabled"])
        self.assertEqual((await self.client.get("/healthz")).status_code, 200)

    async def test_weak_or_missing_password_is_refused(self):
        for body in ({"username": "admin", "password": "kort"}, {"username": "admin"}, {"username": "admin", "password": 12345678901}):
            self.assertEqual((await self.post("/api/auth/password", body)).status_code, 400, body)
        self.assertFalse(await auth.password_configured())

    async def test_empty_username_falls_back_to_admin(self):
        self.assertEqual((await self.post("/api/auth/password", {"username": "", "password": PASSWORD})).status_code, 200)
        self.assertEqual((await self.client.get("/api/auth/status")).get_json()["username"], "admin")

    async def test_password_is_not_stored_in_plain_text(self):
        await self.setup_password()
        with closing(db()) as conn:
            stored = conn.execute("SELECT password_hash FROM password_credentials").fetchone()[0]
        self.assertTrue(stored.startswith("scrypt$"))
        self.assertNotIn(PASSWORD, stored)

    async def test_a_stranger_cannot_replace_the_password(self):
        await self.setup_password()
        stranger = self.new_client()
        self.assertEqual((await self.setup_password(stranger, password=OTHER)).status_code, 401)
        self.assertEqual((await self.post("/api/auth/password/login", {"username": "admin", "password": OTHER}, stranger)).status_code, 401)

    async def test_changing_needs_the_current_password_and_logs_other_devices_out(self):
        await self.setup_password()
        other = self.new_client()
        await self.post("/api/auth/password/login", {"username": "admin", "password": PASSWORD}, other)
        self.assertEqual((await other.get("/api/mealplan?start=a&end=b")).status_code != 401, True)

        no_current = await self.post("/api/auth/password", {"username": "admin", "password": OTHER})
        self.assertEqual(no_current.status_code, 403)
        res = await self.post("/api/auth/password", {"username": "admin", "password": OTHER, "currentPassword": PASSWORD})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.get_json()["replaced"])
        self.assertTrue((await self.client.get("/api/auth/status")).get_json()["authenticated"], "dit apparaat blijft ingelogd")
        self.assertFalse((await other.get("/api/auth/status")).get_json()["authenticated"], "het andere apparaat niet")
        self.assertEqual(
            (await self.post("/api/auth/password/login", {"username": "admin", "password": PASSWORD}, self.new_client())).status_code, 401
        )
        self.assertEqual((await self.post("/api/auth/password/login", {"username": "admin", "password": OTHER}, self.new_client())).status_code, 200)

    async def test_disabled_via_environment(self):
        with mock.patch.dict(os.environ, {"PASSKEY_AUTH_ENABLED": "false"}):
            self.assertEqual((await self.setup_password()).status_code, 400)
            self.assertFalse((await self.client.get("/api/auth/status")).get_json()["enabled"])


class LoginTest(AuthPasswordTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.setup_password()
        self.guest = self.new_client()

    async def login(self, username="admin", password=PASSWORD, client=None):
        return await self.post("/api/auth/password/login", {"username": username, "password": password}, client or self.guest)

    async def test_success_sets_the_session_cookie(self):
        res = await self.login()
        self.assertEqual(res.status_code, 200)
        self.assertIn("ms_session=", res.headers["Set-Cookie"])
        self.assertTrue((await self.guest.get("/api/auth/status")).get_json()["authenticated"])

    async def test_username_is_case_insensitive_but_the_password_is_not(self):
        self.assertEqual((await self.login(username="ADMIN")).status_code, 200)
        self.assertEqual((await self.login(password=PASSWORD.upper(), client=self.new_client())).status_code, 401)

    async def test_wrong_user_and_wrong_password_look_the_same(self):
        wrong_pw = await self.login(password=OTHER)
        wrong_user = await self.login(username="iemand")
        self.assertEqual(wrong_pw.status_code, wrong_user.status_code, 401)
        self.assertEqual(wrong_pw.get_json(), wrong_user.get_json())
        self.assertNotIn("Set-Cookie", wrong_pw.headers)

    async def test_unknown_user_still_costs_a_hash(self):
        with mock.patch.object(auth_password, "_burn_time", new_callable=mock.AsyncMock) as burn:
            await self.login(username="iemand")
            burn.assert_called_once()
            burn.reset_mock()
            await self.login(password=OTHER)
            burn.assert_not_called()

    async def test_lockout_after_repeated_failures(self):
        for _ in range(auth_password.ATTEMPTS_PER_WINDOW):
            self.assertEqual((await self.login(password=OTHER)).status_code, 401)
        locked = await self.login(password=OTHER)
        self.assertEqual(locked.status_code, 429)
        self.assertGreater(int(locked.headers["Retry-After"]), 0)
        self.assertIn("Probeer het over", locked.get_json()["error"])
        self.assertEqual((await self.login()).status_code, 429, "ook het juiste wachtwoord wacht even")

    async def test_lockout_expires(self):
        for _ in range(auth_password.ATTEMPTS_PER_WINDOW):
            await self.login(password=OTHER)
        with closing(db()) as conn, conn:
            conn.execute("UPDATE login_attempts SET locked_until = '2000-01-01T00:00:00+00:00'")
        self.assertEqual((await self.login()).status_code, 200)

    async def test_success_resets_the_counter(self):
        for _ in range(auth_password.ATTEMPTS_PER_WINDOW - 1):
            await self.login(password=OTHER)
        self.assertEqual((await self.login()).status_code, 200)
        for _ in range(auth_password.ATTEMPTS_PER_WINDOW - 1):
            self.assertEqual((await self.login(password=OTHER)).status_code, 401)

    async def test_scrypt_runs_off_the_event_loop(self):
        """Hashen en controleren blokkeren de event loop niet: scrypt draait in een thread (`asyncio.to_thread`)."""
        threads: list[int] = []
        real: Callable[..., bytes] = hashlib.scrypt

        def spy(*args: Any, **kwargs: Any) -> bytes:
            threads.append(threading.get_ident())
            return real(*args, **kwargs)

        with mock.patch.object(auth_password.hashlib, "scrypt", side_effect=spy):
            self.assertEqual((await self.login()).status_code, 200)  # controleren
            self.assertEqual((await self.login(username="iemand")).status_code, 401)  # de nep-hash bij een onbekende naam
            self.assertEqual((await self.setup_password(password=OTHER)).status_code, 403)  # zonder huidig wachtwoord geweigerd
            self.assertEqual((await self.post("/api/auth/password", {"password": OTHER, "currentPassword": PASSWORD})).status_code, 200)  # hashen
        self.assertGreaterEqual(len(threads), 4)
        # De test draait in de thread van de event loop: scrypt hoort daarbuiten.
        self.assertNotIn(threading.get_ident(), threads)

    async def test_overlong_password_is_refused_without_hashing(self):
        with mock.patch.object(auth_password, "verify_password") as verify:
            res = await self.login(password="x" * 5000)
        verify.assert_not_called()
        self.assertEqual(res.status_code, 401)

    async def test_login_before_setup(self):
        fresh = self.client_for(create_app())
        with closing(db()) as conn, conn:
            conn.execute("DELETE FROM password_credentials")
        self.assertEqual((await self.login(client=fresh)).status_code, 400)

    async def test_logout(self):
        await self.login()
        await self.post("/api/auth/logout", client=self.guest)
        self.assertFalse((await self.guest.get("/api/auth/status")).get_json()["authenticated"])


class MfaTest(AuthPasswordTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.setup_password()

    async def test_needs_a_session_and_a_password(self):
        self.assertEqual((await self.post("/api/auth/mfa/start", client=self.new_client())).status_code, 401)
        self.assertEqual((await self.post("/api/auth/mfa/confirm", {"code": "123456"}, self.new_client())).status_code, 401)

    async def test_start_gives_a_secret_and_an_otpauth_link(self):
        started = (await self.post("/api/auth/mfa/start")).get_json()
        self.assertRegex(started["secret"], r"^([A-Z2-7]{4} ){7}[A-Z2-7]{4}$")
        self.assertTrue(started["otpauthUri"].startswith("otpauth://totp/"))
        self.assertIn(started["secret"].replace(" ", ""), started["otpauthUri"])
        self.assertFalse((await self.client.get("/api/auth/status")).get_json()["mfaEnabled"], "pas aan na bevestigen")

    async def test_secret_is_encrypted_at_rest(self):
        started = (await self.post("/api/auth/mfa/start")).get_json()
        with closing(db()) as conn:
            stored = conn.execute("SELECT secret FROM mfa_totp").fetchone()[0]
        self.assertNotIn(started["secret"].replace(" ", ""), stored)

    async def test_wrong_code_does_not_enable_it(self):
        await self.post("/api/auth/mfa/start")
        res = await self.post("/api/auth/mfa/confirm", {"code": "000000"})
        self.assertEqual(res.status_code, 400)
        self.assertFalse(await auth.mfa_enabled())

    async def test_confirm_enables_it_and_returns_recovery_codes_once(self):
        _, res = await self.enable_mfa()
        self.assertEqual(res.status_code, 200)
        codes = res.get_json()["recoveryCodes"]
        self.assertEqual(len(codes), 8)
        status = (await self.client.get("/api/auth/status")).get_json()
        self.assertTrue(status["mfaEnabled"])
        self.assertEqual(status["recoveryCodesLeft"], 8)
        self.assertTrue(status["authenticated"], "je eigen sessie blijft geldig")
        with closing(db()) as conn:
            hashes = [r[0] for r in conn.execute("SELECT code_hash FROM mfa_recovery_codes")]
        self.assertTrue(all(len(h) == 64 and h not in codes for h in hashes), "alleen hashes in de database")
        self.assertEqual((await self.post("/api/auth/mfa/start")).status_code, 409)

    async def login_step_one(self, client):
        return await self.post("/api/auth/password/login", {"username": "admin", "password": PASSWORD}, client)

    async def test_login_needs_the_code_after_the_password(self):
        secret, _ = await self.enable_mfa()
        guest = self.new_client()
        step = await self.login_step_one(guest)
        self.assertEqual(step.status_code, 200)
        self.assertTrue(step.get_json()["mfaRequired"])
        self.assertNotIn("Set-Cookie", step.headers, "nog geen sessie na alleen het wachtwoord")
        self.assertFalse((await guest.get("/api/auth/status")).get_json()["authenticated"])
        ticket = step.get_json()["ticket"]

        wrong = await self.post("/api/auth/password/mfa", {"ticket": ticket, "code": "000000"}, guest)
        self.assertEqual(wrong.status_code, 401)
        good = await self.post(
            "/api/auth/password/mfa", {"ticket": ticket, "code": auth_password.totp_code(secret, at=auth_password.time.time() + 30)}, guest
        )
        self.assertEqual(good.status_code, 200, good.get_json())
        self.assertTrue((await guest.get("/api/auth/status")).get_json()["authenticated"])

    async def test_a_code_works_only_once(self):
        secret, _ = await self.enable_mfa()  # de bevestigingscode van zojuist is al gebruikt
        code = auth_password.totp_code(secret)
        guest = self.new_client()
        ticket = (await self.login_step_one(guest)).get_json()["ticket"]
        self.assertEqual((await self.post("/api/auth/password/mfa", {"ticket": ticket, "code": code}, guest)).status_code, 401)
        later = auth_password.totp_code(secret, at=auth_password.time.time() + 30)
        self.assertEqual((await self.post("/api/auth/password/mfa", {"ticket": ticket, "code": later}, guest)).status_code, 200)
        again = self.new_client()
        ticket2 = (await self.login_step_one(again)).get_json()["ticket"]
        self.assertEqual((await self.post("/api/auth/password/mfa", {"ticket": ticket2, "code": later}, again)).status_code, 401, "hergebruik")

    async def test_recovery_code_works_once(self):
        _, res = await self.enable_mfa()
        code = res.get_json()["recoveryCodes"][0]
        guest = self.new_client()
        ticket = (await self.login_step_one(guest)).get_json()["ticket"]
        ok = await self.post("/api/auth/password/mfa", {"ticket": ticket, "code": code.lower().replace("-", " ")}, guest)
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.get_json()["recoveryCodesLeft"], 7)
        again = self.new_client()
        ticket2 = (await self.login_step_one(again)).get_json()["ticket"]
        self.assertEqual((await self.post("/api/auth/password/mfa", {"ticket": ticket2, "code": code}, again)).status_code, 401)

    async def test_the_mfa_ticket_is_not_a_session(self):
        await self.enable_mfa()
        ticket = (await self.login_step_one(self.new_client())).get_json()["ticket"]
        bearer = await self.new_client().get("/api/auth/status", headers={"Authorization": f"Bearer {ticket}"})
        self.assertFalse(bearer.get_json()["authenticated"])
        guest = self.new_client()
        guest.set_cookie(auth.SESSION_COOKIE_NAME, ticket)
        self.assertFalse((await guest.get("/api/auth/status")).get_json()["authenticated"])
        self.assertEqual((await guest.get("/api/mealplan?start=a&end=b")).status_code, 401)

    async def test_bad_or_expired_ticket(self):
        await self.enable_mfa()
        guest = self.new_client()
        self.assertEqual((await self.post("/api/auth/password/mfa", {"ticket": "nep", "code": "123456"}, guest)).status_code, 401)
        ticket = (await self.login_step_one(guest)).get_json()["ticket"]
        with mock.patch.object(auth_password, "MFA_TICKET_SECONDS", -10):
            expired = (await self.login_step_one(guest)).get_json()["ticket"]
        self.assertEqual((await self.post("/api/auth/password/mfa", {"ticket": expired, "code": "123456"}, guest)).status_code, 401)
        self.assertTrue(ticket)

    async def test_wrong_codes_lock_out(self):
        await self.enable_mfa()
        guest = self.new_client()
        ticket = (await self.login_step_one(guest)).get_json()["ticket"]
        for _ in range(auth_password.ATTEMPTS_PER_WINDOW):
            await self.post("/api/auth/password/mfa", {"ticket": ticket, "code": "000000"}, guest)
        self.assertEqual((await self.post("/api/auth/password/mfa", {"ticket": ticket, "code": "000000"}, guest)).status_code, 429)

    async def test_disable_needs_the_password_and_logs_others_out(self):
        await self.enable_mfa()
        other = self.new_client()
        self.assertEqual((await self.post("/api/auth/mfa/disable", {"password": OTHER})).status_code, 403)
        self.assertTrue(await auth.mfa_enabled())
        self.assertEqual((await self.post("/api/auth/mfa/disable", {"password": PASSWORD})).status_code, 200)
        self.assertFalse(await auth.mfa_enabled())
        self.assertEqual(await auth.recovery_codes_left(), 0)
        self.assertEqual((await self.login_step_one(other)).get_json().get("mfaRequired"), None)

    async def test_new_recovery_codes_replace_the_old_ones(self):
        _, res = await self.enable_mfa()
        old = res.get_json()["recoveryCodes"]
        self.assertEqual((await self.post("/api/auth/mfa/recovery", {"password": OTHER})).status_code, 403)
        new = (await self.post("/api/auth/mfa/recovery", {"password": PASSWORD})).get_json()["recoveryCodes"]
        self.assertEqual(len(new), 8)
        self.assertFalse(set(old) & set(new))
        guest = self.new_client()
        ticket = (await self.login_step_one(guest)).get_json()["ticket"]
        self.assertEqual((await self.post("/api/auth/password/mfa", {"ticket": ticket, "code": old[0]}, guest)).status_code, 401)

    async def test_unreadable_secret_leaves_recovery_codes(self):
        _, res = await self.enable_mfa()
        code = res.get_json()["recoveryCodes"][0]
        with mock.patch.dict(os.environ, {"MEALIESHOPPER_AUTH_SECRET": "andere-sleutel"}):
            guest = self.new_client()
            ticket = await self.login_step_one(guest)
            # Met een andere sleutel zijn ook de sessies en kaartjes ongeldig; het wachtwoord blijft werken
            # omdat de hash zelf geen sleutel nodig heeft, en een herstelcode logt nog in.
            self.assertEqual(ticket.status_code, 200)
            token = ticket.get_json()["ticket"]
            self.assertEqual((await self.post("/api/auth/password/mfa", {"ticket": token, "code": "123456"}, guest)).status_code, 401)
            self.assertEqual((await self.post("/api/auth/password/mfa", {"ticket": token, "code": code}, guest)).status_code, 200)


class OpenModeTest(AuthPasswordTestCase):
    async def test_skipping_security_needs_confirmation(self):
        self.assertEqual((await self.post("/api/auth/open")).status_code, 400)
        self.assertEqual((await self.post("/api/auth/open", {"confirm": "ja"})).status_code, 400)
        self.assertTrue((await self.client.get("/api/auth/status")).get_json()["setupRequired"])

    async def test_open_mode_lets_everything_through(self):
        self.assertEqual((await self.post("/api/auth/open", {"confirm": True})).status_code, 200)
        status = (await self.client.get("/api/auth/status")).get_json()
        self.assertFalse(status["enabled"])
        self.assertFalse(status["setupRequired"])
        self.assertEqual((await self.new_client().get("/api/ah/lists/search?q=x")).status_code, 200)

    async def test_open_mode_is_only_for_a_new_install(self):
        await self.setup_password()
        self.assertEqual((await self.post("/api/auth/open", {"confirm": True}, self.new_client())).status_code, 409)

    async def test_a_password_can_be_added_later_and_turns_security_on(self):
        await self.post("/api/auth/open", {"confirm": True})
        guest = self.new_client()
        self.assertEqual((await self.setup_password(guest)).status_code, 200)
        stranger = self.new_client()
        self.assertEqual((await stranger.get("/api/ah/lists/search?q=x")).status_code, 401)
        self.assertEqual((await guest.get("/api/ah/lists/search?q=x")).status_code, 200)

    async def test_not_possible_when_disabled_by_environment(self):
        with mock.patch.dict(os.environ, {"PASSKEY_AUTH_ENABLED": "false"}):
            self.assertEqual((await self.post("/api/auth/open", {"confirm": True})).status_code, 400)


class PasskeyRuleTest(AuthPasswordTestCase):
    async def add_passkey_row(self):
        await auth.ensure_db()
        with closing(db()) as conn, conn:
            conn.execute("INSERT OR IGNORE INTO users (id, username, created_at) VALUES ('owner', 'admin', 'x')")
            conn.execute(
                "INSERT INTO passkey_credentials (id, user_id, public_key, credential_name, created_at) VALUES ('c1', 'owner', x'00', 'Telefoon', 'x')"
            )

    async def test_last_passkey_stays_when_it_is_the_only_way_in(self):
        await self.add_passkey_row()
        client = self.new_client()
        client.set_cookie(auth.SESSION_COOKIE_NAME, await auth.create_token("owner", "admin"))
        res = await client.delete("/api/auth/credentials/c1")
        self.assertEqual(res.status_code, 400)

    async def test_last_passkey_can_go_when_there_is_a_password(self):
        await self.add_passkey_row()
        client = self.new_client()
        client.set_cookie(auth.SESSION_COOKIE_NAME, await auth.create_token("owner", "admin"))
        self.assertEqual((await self.setup_password(client)).status_code, 200)
        self.assertEqual((await client.delete("/api/auth/credentials/c1")).status_code, 200)
        self.assertTrue((await client.get("/api/auth/status")).get_json()["authenticated"])

    async def test_adding_a_passkey_needs_a_session_once_a_password_exists(self):
        await self.setup_password()
        res = await self.post("/api/auth/register/options", {}, self.new_client())
        self.assertEqual(res.status_code, 401)
        self.assertEqual((await self.post("/api/auth/register/options", {})).status_code, 200)


if __name__ == "__main__":
    unittest.main()


class MasterSecretTest(AppTestCase):
    """Zonder MEALIESHOPPER_AUTH_SECRET maakt de app zelf een willekeurig geheim; het is niet te raden."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        env = mock.patch.dict(os.environ, {"PASSKEY_AUTH_ENABLED": "true", "MEALIESHOPPER_DATA_DIR": tmp.name})
        env.start()
        self.addCleanup(env.stop)
        for name in ("MEALIESHOPPER_AUTH_SECRET", "JWT_SECRET", "MEALIE_URL"):
            os.environ.pop(name, None)
        auth._master_secrets.clear()
        self.addCleanup(auth._master_secrets.clear)
        cost = mock.patch.object(auth_password, "SCRYPT_N", 2**4)
        cost.start()
        self.addCleanup(cost.stop)
        self.path = os.path.join(self.tmp, "secret.key")

    def test_secret_is_random_stored_once_and_private(self):
        first = run(auth.master_secret())
        self.assertGreaterEqual(len(first), 64)
        auth._master_secrets.clear()
        self.assertEqual(run(auth.master_secret()), first, "blijft gelijk na een herstart")
        self.assertEqual(oct(os.stat(self.path).st_mode & 0o777), "0o600")
        os.remove(self.path)
        auth._master_secrets.clear()
        self.assertNotEqual(run(auth.master_secret()), first)

    def test_sessions_and_stored_data_use_different_keys(self):
        self.assertNotEqual(run(auth.signing_secret()), run(auth.master_secret()))
        box_key = run(auth._derive("stored-secrets"))
        self.assertNotEqual(box_key.hex(), run(auth.signing_secret()))

    async def test_a_session_signed_with_the_old_guessable_secret_is_rejected(self):
        app = create_app()
        client = self.client_for(app)
        await client.post("/api/auth/password", json={"username": "admin", "password": PASSWORD})
        guessable = auth.hashlib.sha256(f"{auth.db_path()}:mealieshopper".encode()).hexdigest()  # de vroegere sleutel
        forged = auth.jwt.encode(
            {"sub": "owner", "usr": "admin", "ep": 0, "exp": auth.utcnow() + auth.timedelta(hours=1)}, guessable, algorithm="HS256"
        )
        attacker = self.client_for(app)
        attacker.set_cookie(auth.SESSION_COOKIE_NAME, forged)
        self.assertFalse((await attacker.get("/api/auth/status")).get_json()["authenticated"])
        self.assertEqual((await attacker.get("/api/ah/lists/search?q=x")).status_code, 401)
        self.assertTrue((await client.get("/api/auth/status")).get_json()["authenticated"], "de echte sessie werkt")

    def test_configured_secret_wins_and_nothing_is_written(self):
        with mock.patch.dict(os.environ, {"MEALIESHOPPER_AUTH_SECRET": " zelf-gekozen "}):
            self.assertEqual(run(auth.master_secret()), "zelf-gekozen")
            run(auth.init_db())
            run(auth.set_secret("X", "waarde"))
            self.assertEqual(run(auth.get_secret("X")), "waarde")
        self.assertFalse(os.path.exists(self.path), "de sleutel staat dan niet op schijf")

    def test_jwt_secret_is_still_accepted(self):
        with mock.patch.dict(os.environ, {"JWT_SECRET": "oude-naam"}):
            self.assertEqual(run(auth.master_secret()), "oude-naam")

    def test_stored_secrets_round_trip_and_are_not_plain_text(self):
        run(auth.init_db())
        run(auth.set_secret("MEALIE_API_TOKEN", "geheime-token"))
        self.assertEqual(run(auth.get_secret("MEALIE_API_TOKEN")), "geheime-token")
        with closing(db()) as conn:
            stored = conn.execute("SELECT value FROM app_settings WHERE key = 'MEALIE_API_TOKEN'").fetchone()[0]
        self.assertNotIn("geheime-token", stored)
        self.assertTrue(stored.startswith("gAAAA"))

    def test_data_encrypted_with_another_key_is_unreadable_not_garbage(self):
        run(auth.init_db())
        run(auth.set_secret("MEALIE_API_TOKEN", "geheime-token"))
        os.remove(self.path)
        auth._master_secrets.clear()  # nieuwe sleutel: bijvoorbeeld een data-map zonder secret.key teruggezet
        self.assertEqual(run(auth.get_secret("MEALIE_API_TOKEN")), "", "geen versleutelde tekst teruggeven als token")

    def test_plain_settings_are_still_returned_as_they_are(self):
        run(auth.init_db())
        run(auth.set_setting("AUTH_MODE", "none"))
        self.assertEqual(run(auth.get_secret("AUTH_MODE")), "none")

    def test_the_key_does_not_depend_on_mealie_url(self):
        before = run(auth.master_secret())
        with mock.patch.dict(os.environ, {"MEALIE_URL": "https://ergens.example"}):
            auth._master_secrets.clear()
            self.assertEqual(run(auth.master_secret()), before)

    def test_an_incomplete_file_is_waited_for_then_reported(self):
        with open(self.path, "w") as handle:
            handle.write("kort")
        with mock.patch.object(auth, "SECRET_RETRY_SECONDS", 0):
            with self.assertRaisesRegex(RuntimeError, "onleesbaar of onvolledig"):
                run(auth.master_secret())
        with open(self.path) as handle:
            self.assertEqual(handle.read(), "kort", "een bestaand bestand wordt nooit overschreven")

    async def test_a_file_that_is_still_being_written_is_read_once_complete(self):
        with open(self.path, "w") as handle:
            handle.write("")
        full = "ab" * 32

        def finish():
            with open(self.path, "w") as handle:
                handle.write(full)

        asyncio.get_running_loop().call_later(0.05, finish)  # de andere werker is na een paar pogingen klaar
        with mock.patch.object(auth, "SECRET_RETRY_SECONDS", 0.02):
            self.assertEqual(await auth._read_secret_file(auth.Path(self.path), wait=True), full)


# --- gelijktijdige pogingen (een event loop, een slot) -----------------------------------------------


@pytest.fixture
async def owner(data_dir: "object", monkeypatch: pytest.MonkeyPatch, client: "TestClientProtocol") -> "TestClientProtocol":
    """De async testclient, ingelogd als beheerder met een wachtwoord."""
    monkeypatch.setenv("PASSKEY_AUTH_ENABLED", "true")
    monkeypatch.setattr(auth_password, "SCRYPT_N", 2**4)
    response = await client.post("/api/auth/password", json={"username": "admin", "password": PASSWORD})
    assert response.status_code == 200, await response.get_json()
    return client


async def test_concurrent_wrong_logins_are_throttled_like_serial_ones(owner: "TestClientProtocol") -> None:
    burst: int = 10
    replies = await asyncio.gather(*(owner.post("/api/auth/password/login", json={"username": "admin", "password": OTHER}) for _ in range(burst)))
    statuses: list[int] = sorted(reply.status_code for reply in replies)
    limit: int = auth_password.ATTEMPTS_PER_WINDOW
    assert statuses == [401] * limit + [429] * (burst - limit)


async def test_one_totp_code_works_once_even_at_the_same_moment(owner: "TestClientProtocol") -> None:
    started = await (await owner.post("/api/auth/mfa/start", json={})).get_json()
    secret: str = started["secret"].replace(" ", "")
    confirmed = await owner.post("/api/auth/mfa/confirm", json={"code": auth_password.totp_code(secret)})
    assert confirmed.status_code == 200
    step_one = await (await owner.post("/api/auth/password/login", json={"username": "admin", "password": PASSWORD})).get_json()
    code: str = auth_password.totp_code(secret, at=auth_password.time.time() + auth_password.TOTP_PERIOD)
    replies = await asyncio.gather(*(owner.post("/api/auth/password/mfa", json={"ticket": step_one["ticket"], "code": code}) for _ in range(2)))
    assert sorted(reply.status_code for reply in replies) == [200, 401]
