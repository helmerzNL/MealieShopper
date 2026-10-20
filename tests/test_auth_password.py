import os
import tempfile
import unittest
from contextlib import closing
from unittest import mock

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

from mealieshopper import auth, auth_password  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402

PASSWORD = "koffie-met-melk-42"
OTHER = "een-heel-ander-wachtwoord"


class AuthPasswordTestCase(unittest.TestCase):
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
        self.client = self.app.test_client()

    def post(self, path, body=None, client=None):
        return (client or self.client).post(path, json=body or {})

    def setup_password(self, client=None, username="admin", password=PASSWORD):
        return self.post("/api/auth/password", {"username": username, "password": password}, client)

    def new_client(self):
        return self.app.test_client()

    def enable_mfa(self, client=None):
        client = client or self.client
        started = self.post("/api/auth/mfa/start", client=client).get_json()
        secret = started["secret"].replace(" ", "")
        res = self.post("/api/auth/mfa/confirm", {"code": auth_password.totp_code(secret)}, client)
        return secret, res


class PasswordRulesTest(unittest.TestCase):
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


class TotpTest(unittest.TestCase):
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


class RecoveryCodeTest(unittest.TestCase):
    def test_codes_are_long_and_distinct(self):
        codes = auth_password.new_recovery_codes()
        self.assertEqual(len(codes), 8)
        self.assertEqual(len(set(codes)), 8)
        for code in codes:
            self.assertRegex(code, r"^[A-Z2-9]{4}(-[A-Z2-9]{4}){3}$")
            self.assertNotRegex(code, r"[01OI]")


class StatusTest(AuthPasswordTestCase):
    def test_fresh_install_requires_setup(self):
        status = self.client.get("/api/auth/status").get_json()
        self.assertTrue(status["enabled"])
        self.assertTrue(status["setupRequired"])
        self.assertFalse(status["authenticated"])
        self.assertEqual(status["methods"], {"passkey": False, "password": False})
        self.assertNotIn("mfaEnabled", status, "zonder sessie geen details")
        self.assertEqual(self.client.get("/api/mealplan?start=a&end=b").status_code, 401)


class SetupTest(AuthPasswordTestCase):
    def test_first_password_logs_you_in(self):
        res = self.setup_password()
        self.assertEqual(res.status_code, 200)
        self.assertIn("ms_session=", res.headers.get("Set-Cookie", ""))
        self.assertIn("HttpOnly", res.headers["Set-Cookie"])
        status = self.client.get("/api/auth/status").get_json()
        self.assertTrue(status["authenticated"])
        self.assertEqual(status["username"], "admin")
        self.assertFalse(status["setupRequired"])
        self.assertTrue(status["passwordConfigured"])
        self.assertFalse(status["mfaEnabled"])
        self.assertEqual(self.client.get("/healthz").status_code, 200)

    def test_weak_or_missing_password_is_refused(self):
        for body in ({"username": "admin", "password": "kort"}, {"username": "admin"}, {"username": "admin", "password": 12345678901}):
            self.assertEqual(self.post("/api/auth/password", body).status_code, 400, body)
        self.assertFalse(auth.password_configured())

    def test_empty_username_falls_back_to_admin(self):
        self.assertEqual(self.post("/api/auth/password", {"username": "", "password": PASSWORD}).status_code, 200)
        self.assertEqual(self.client.get("/api/auth/status").get_json()["username"], "admin")

    def test_password_is_not_stored_in_plain_text(self):
        self.setup_password()
        with closing(auth.connect()) as conn:
            stored = conn.execute("SELECT password_hash FROM password_credentials").fetchone()[0]
        self.assertTrue(stored.startswith("scrypt$"))
        self.assertNotIn(PASSWORD, stored)

    def test_a_stranger_cannot_replace_the_password(self):
        self.setup_password()
        stranger = self.new_client()
        self.assertEqual(self.setup_password(stranger, password=OTHER).status_code, 401)
        self.assertEqual(self.post("/api/auth/password/login", {"username": "admin", "password": OTHER}, stranger).status_code, 401)

    def test_changing_needs_the_current_password_and_logs_other_devices_out(self):
        self.setup_password()
        other = self.new_client()
        self.post("/api/auth/password/login", {"username": "admin", "password": PASSWORD}, other)
        self.assertEqual(other.get("/api/mealplan?start=a&end=b").status_code != 401, True)

        no_current = self.post("/api/auth/password", {"username": "admin", "password": OTHER})
        self.assertEqual(no_current.status_code, 403)
        res = self.post("/api/auth/password", {"username": "admin", "password": OTHER, "currentPassword": PASSWORD})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.get_json()["replaced"])
        self.assertTrue(self.client.get("/api/auth/status").get_json()["authenticated"], "dit apparaat blijft ingelogd")
        self.assertFalse(other.get("/api/auth/status").get_json()["authenticated"], "het andere apparaat niet")
        self.assertEqual(self.post("/api/auth/password/login", {"username": "admin", "password": PASSWORD}, self.new_client()).status_code, 401)
        self.assertEqual(self.post("/api/auth/password/login", {"username": "admin", "password": OTHER}, self.new_client()).status_code, 200)

    def test_disabled_via_environment(self):
        with mock.patch.dict(os.environ, {"PASSKEY_AUTH_ENABLED": "false"}):
            self.assertEqual(self.setup_password().status_code, 400)
            self.assertFalse(self.client.get("/api/auth/status").get_json()["enabled"])


class LoginTest(AuthPasswordTestCase):
    def setUp(self):
        super().setUp()
        self.setup_password()
        self.guest = self.new_client()

    def login(self, username="admin", password=PASSWORD, client=None):
        return self.post("/api/auth/password/login", {"username": username, "password": password}, client or self.guest)

    def test_success_sets_the_session_cookie(self):
        res = self.login()
        self.assertEqual(res.status_code, 200)
        self.assertIn("ms_session=", res.headers["Set-Cookie"])
        self.assertTrue(self.guest.get("/api/auth/status").get_json()["authenticated"])

    def test_username_is_case_insensitive_but_the_password_is_not(self):
        self.assertEqual(self.login(username="ADMIN").status_code, 200)
        self.assertEqual(self.login(password=PASSWORD.upper(), client=self.new_client()).status_code, 401)

    def test_wrong_user_and_wrong_password_look_the_same(self):
        wrong_pw = self.login(password=OTHER)
        wrong_user = self.login(username="iemand")
        self.assertEqual(wrong_pw.status_code, wrong_user.status_code, 401)
        self.assertEqual(wrong_pw.get_json(), wrong_user.get_json())
        self.assertNotIn("Set-Cookie", wrong_pw.headers)

    def test_unknown_user_still_costs_a_hash(self):
        with mock.patch.object(auth_password, "_burn_time") as burn:
            self.login(username="iemand")
            burn.assert_called_once()
            burn.reset_mock()
            self.login(password=OTHER)
            burn.assert_not_called()

    def test_lockout_after_repeated_failures(self):
        for _ in range(auth_password.ATTEMPTS_PER_WINDOW):
            self.assertEqual(self.login(password=OTHER).status_code, 401)
        locked = self.login(password=OTHER)
        self.assertEqual(locked.status_code, 429)
        self.assertGreater(int(locked.headers["Retry-After"]), 0)
        self.assertIn("Probeer het over", locked.get_json()["error"])
        self.assertEqual(self.login().status_code, 429, "ook het juiste wachtwoord wacht even")

    def test_lockout_expires(self):
        for _ in range(auth_password.ATTEMPTS_PER_WINDOW):
            self.login(password=OTHER)
        with closing(auth.connect()) as conn, conn:
            conn.execute("UPDATE login_attempts SET locked_until = '2000-01-01T00:00:00+00:00'")
        self.assertEqual(self.login().status_code, 200)

    def test_success_resets_the_counter(self):
        for _ in range(auth_password.ATTEMPTS_PER_WINDOW - 1):
            self.login(password=OTHER)
        self.assertEqual(self.login().status_code, 200)
        for _ in range(auth_password.ATTEMPTS_PER_WINDOW - 1):
            self.assertEqual(self.login(password=OTHER).status_code, 401)

    def test_overlong_password_is_refused_without_hashing(self):
        with mock.patch.object(auth_password, "verify_password") as verify:
            res = self.login(password="x" * 5000)
        verify.assert_not_called()
        self.assertEqual(res.status_code, 401)

    def test_login_before_setup(self):
        fresh = create_app().test_client()
        with closing(auth.connect()) as conn, conn:
            conn.execute("DELETE FROM password_credentials")
        self.assertEqual(self.login(client=fresh).status_code, 400)

    def test_logout(self):
        self.login()
        self.post("/api/auth/logout", client=self.guest)
        self.assertFalse(self.guest.get("/api/auth/status").get_json()["authenticated"])


class MfaTest(AuthPasswordTestCase):
    def setUp(self):
        super().setUp()
        self.setup_password()

    def test_needs_a_session_and_a_password(self):
        self.assertEqual(self.post("/api/auth/mfa/start", client=self.new_client()).status_code, 401)
        self.assertEqual(self.post("/api/auth/mfa/confirm", {"code": "123456"}, self.new_client()).status_code, 401)

    def test_start_gives_a_secret_and_an_otpauth_link(self):
        started = self.post("/api/auth/mfa/start").get_json()
        self.assertRegex(started["secret"], r"^([A-Z2-7]{4} ){7}[A-Z2-7]{4}$")
        self.assertTrue(started["otpauthUri"].startswith("otpauth://totp/"))
        self.assertIn(started["secret"].replace(" ", ""), started["otpauthUri"])
        self.assertFalse(self.client.get("/api/auth/status").get_json()["mfaEnabled"], "pas aan na bevestigen")

    def test_secret_is_encrypted_at_rest(self):
        started = self.post("/api/auth/mfa/start").get_json()
        with closing(auth.connect()) as conn:
            stored = conn.execute("SELECT secret FROM mfa_totp").fetchone()[0]
        self.assertNotIn(started["secret"].replace(" ", ""), stored)

    def test_wrong_code_does_not_enable_it(self):
        self.post("/api/auth/mfa/start")
        res = self.post("/api/auth/mfa/confirm", {"code": "000000"})
        self.assertEqual(res.status_code, 400)
        self.assertFalse(auth.mfa_enabled())

    def test_confirm_enables_it_and_returns_recovery_codes_once(self):
        _, res = self.enable_mfa()
        self.assertEqual(res.status_code, 200)
        codes = res.get_json()["recoveryCodes"]
        self.assertEqual(len(codes), 8)
        status = self.client.get("/api/auth/status").get_json()
        self.assertTrue(status["mfaEnabled"])
        self.assertEqual(status["recoveryCodesLeft"], 8)
        self.assertTrue(status["authenticated"], "je eigen sessie blijft geldig")
        with closing(auth.connect()) as conn:
            hashes = [r[0] for r in conn.execute("SELECT code_hash FROM mfa_recovery_codes")]
        self.assertTrue(all(len(h) == 64 and h not in codes for h in hashes), "alleen hashes in de database")
        self.assertEqual(self.post("/api/auth/mfa/start").status_code, 409)

    def login_step_one(self, client):
        return self.post("/api/auth/password/login", {"username": "admin", "password": PASSWORD}, client)

    def test_login_needs_the_code_after_the_password(self):
        secret, _ = self.enable_mfa()
        guest = self.new_client()
        step = self.login_step_one(guest)
        self.assertEqual(step.status_code, 200)
        self.assertTrue(step.get_json()["mfaRequired"])
        self.assertNotIn("Set-Cookie", step.headers, "nog geen sessie na alleen het wachtwoord")
        self.assertFalse(guest.get("/api/auth/status").get_json()["authenticated"])
        ticket = step.get_json()["ticket"]

        wrong = self.post("/api/auth/password/mfa", {"ticket": ticket, "code": "000000"}, guest)
        self.assertEqual(wrong.status_code, 401)
        good = self.post("/api/auth/password/mfa", {"ticket": ticket, "code": auth_password.totp_code(secret, at=auth_password.time.time() + 30)}, guest)
        self.assertEqual(good.status_code, 200, good.get_json())
        self.assertTrue(guest.get("/api/auth/status").get_json()["authenticated"])

    def test_a_code_works_only_once(self):
        secret, _ = self.enable_mfa()  # de bevestigingscode van zojuist is al gebruikt
        code = auth_password.totp_code(secret)
        guest = self.new_client()
        ticket = self.login_step_one(guest).get_json()["ticket"]
        self.assertEqual(self.post("/api/auth/password/mfa", {"ticket": ticket, "code": code}, guest).status_code, 401)
        later = auth_password.totp_code(secret, at=auth_password.time.time() + 30)
        self.assertEqual(self.post("/api/auth/password/mfa", {"ticket": ticket, "code": later}, guest).status_code, 200)
        again = self.new_client()
        ticket2 = self.login_step_one(again).get_json()["ticket"]
        self.assertEqual(self.post("/api/auth/password/mfa", {"ticket": ticket2, "code": later}, again).status_code, 401, "hergebruik")

    def test_recovery_code_works_once(self):
        _, res = self.enable_mfa()
        code = res.get_json()["recoveryCodes"][0]
        guest = self.new_client()
        ticket = self.login_step_one(guest).get_json()["ticket"]
        ok = self.post("/api/auth/password/mfa", {"ticket": ticket, "code": code.lower().replace("-", " ")}, guest)
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.get_json()["recoveryCodesLeft"], 7)
        again = self.new_client()
        ticket2 = self.login_step_one(again).get_json()["ticket"]
        self.assertEqual(self.post("/api/auth/password/mfa", {"ticket": ticket2, "code": code}, again).status_code, 401)

    def test_the_mfa_ticket_is_not_a_session(self):
        self.enable_mfa()
        ticket = self.login_step_one(self.new_client()).get_json()["ticket"]
        bearer = self.new_client().get("/api/auth/status", headers={"Authorization": f"Bearer {ticket}"})
        self.assertFalse(bearer.get_json()["authenticated"])
        guest = self.new_client()
        guest.set_cookie(auth.SESSION_COOKIE_NAME, ticket)
        self.assertFalse(guest.get("/api/auth/status").get_json()["authenticated"])
        self.assertEqual(guest.get("/api/mealplan?start=a&end=b").status_code, 401)

    def test_bad_or_expired_ticket(self):
        self.enable_mfa()
        guest = self.new_client()
        self.assertEqual(self.post("/api/auth/password/mfa", {"ticket": "nep", "code": "123456"}, guest).status_code, 401)
        ticket = self.login_step_one(guest).get_json()["ticket"]
        with mock.patch.object(auth_password, "MFA_TICKET_SECONDS", -10):
            expired = self.login_step_one(guest).get_json()["ticket"]
        self.assertEqual(self.post("/api/auth/password/mfa", {"ticket": expired, "code": "123456"}, guest).status_code, 401)
        self.assertTrue(ticket)

    def test_wrong_codes_lock_out(self):
        self.enable_mfa()
        guest = self.new_client()
        ticket = self.login_step_one(guest).get_json()["ticket"]
        for _ in range(auth_password.ATTEMPTS_PER_WINDOW):
            self.post("/api/auth/password/mfa", {"ticket": ticket, "code": "000000"}, guest)
        self.assertEqual(self.post("/api/auth/password/mfa", {"ticket": ticket, "code": "000000"}, guest).status_code, 429)

    def test_disable_needs_the_password_and_logs_others_out(self):
        self.enable_mfa()
        other = self.new_client()
        self.assertEqual(self.post("/api/auth/mfa/disable", {"password": OTHER}).status_code, 403)
        self.assertTrue(auth.mfa_enabled())
        self.assertEqual(self.post("/api/auth/mfa/disable", {"password": PASSWORD}).status_code, 200)
        self.assertFalse(auth.mfa_enabled())
        self.assertEqual(auth.recovery_codes_left(), 0)
        self.assertEqual(self.login_step_one(other).get_json().get("mfaRequired"), None)

    def test_new_recovery_codes_replace_the_old_ones(self):
        _, res = self.enable_mfa()
        old = res.get_json()["recoveryCodes"]
        self.assertEqual(self.post("/api/auth/mfa/recovery", {"password": OTHER}).status_code, 403)
        new = self.post("/api/auth/mfa/recovery", {"password": PASSWORD}).get_json()["recoveryCodes"]
        self.assertEqual(len(new), 8)
        self.assertFalse(set(old) & set(new))
        guest = self.new_client()
        ticket = self.login_step_one(guest).get_json()["ticket"]
        self.assertEqual(self.post("/api/auth/password/mfa", {"ticket": ticket, "code": old[0]}, guest).status_code, 401)

    def test_unreadable_secret_leaves_recovery_codes(self):
        _, res = self.enable_mfa()
        code = res.get_json()["recoveryCodes"][0]
        with mock.patch.dict(os.environ, {"MEALIESHOPPER_AUTH_SECRET": "andere-sleutel"}):
            guest = self.new_client()
            ticket = self.login_step_one(guest)
            # Met een andere sleutel zijn ook de sessies en kaartjes ongeldig; het wachtwoord blijft werken
            # omdat de hash zelf geen sleutel nodig heeft, en een herstelcode logt nog in.
            self.assertEqual(ticket.status_code, 200)
            token = ticket.get_json()["ticket"]
            self.assertEqual(self.post("/api/auth/password/mfa", {"ticket": token, "code": "123456"}, guest).status_code, 401)
            self.assertEqual(self.post("/api/auth/password/mfa", {"ticket": token, "code": code}, guest).status_code, 200)


class OpenModeTest(AuthPasswordTestCase):
    def test_skipping_security_needs_confirmation(self):
        self.assertEqual(self.post("/api/auth/open").status_code, 400)
        self.assertEqual(self.post("/api/auth/open", {"confirm": "ja"}).status_code, 400)
        self.assertTrue(self.client.get("/api/auth/status").get_json()["setupRequired"])

    def test_open_mode_lets_everything_through(self):
        self.assertEqual(self.post("/api/auth/open", {"confirm": True}).status_code, 200)
        status = self.client.get("/api/auth/status").get_json()
        self.assertFalse(status["enabled"])
        self.assertFalse(status["setupRequired"])
        self.assertEqual(self.new_client().get("/api/ah/lists/search?q=x").status_code, 200)

    def test_open_mode_is_only_for_a_new_install(self):
        self.setup_password()
        self.assertEqual(self.post("/api/auth/open", {"confirm": True}, self.new_client()).status_code, 409)

    def test_a_password_can_be_added_later_and_turns_security_on(self):
        self.post("/api/auth/open", {"confirm": True})
        guest = self.new_client()
        self.assertEqual(self.setup_password(guest).status_code, 200)
        stranger = self.new_client()
        self.assertEqual(stranger.get("/api/ah/lists/search?q=x").status_code, 401)
        self.assertEqual(guest.get("/api/ah/lists/search?q=x").status_code, 200)

    def test_not_possible_when_disabled_by_environment(self):
        with mock.patch.dict(os.environ, {"PASSKEY_AUTH_ENABLED": "false"}):
            self.assertEqual(self.post("/api/auth/open", {"confirm": True}).status_code, 400)


class PasskeyRuleTest(AuthPasswordTestCase):
    def add_passkey_row(self):
        auth.ensure_db()
        with closing(auth.connect()) as conn, conn:
            conn.execute("INSERT OR IGNORE INTO users (id, username, created_at) VALUES ('owner', 'admin', 'x')")
            conn.execute(
                "INSERT INTO passkey_credentials (id, user_id, public_key, credential_name, created_at) VALUES ('c1', 'owner', x'00', 'Telefoon', 'x')"
            )

    def test_last_passkey_stays_when_it_is_the_only_way_in(self):
        self.add_passkey_row()
        client = self.new_client()
        client.set_cookie(auth.SESSION_COOKIE_NAME, auth.create_token("owner", "admin"))
        res = client.delete("/api/auth/credentials/c1")
        self.assertEqual(res.status_code, 400)

    def test_last_passkey_can_go_when_there_is_a_password(self):
        self.add_passkey_row()
        client = self.new_client()
        client.set_cookie(auth.SESSION_COOKIE_NAME, auth.create_token("owner", "admin"))
        self.assertEqual(self.setup_password(client).status_code, 200)
        self.assertEqual(client.delete("/api/auth/credentials/c1").status_code, 200)
        self.assertTrue(client.get("/api/auth/status").get_json()["authenticated"])

    def test_adding_a_passkey_needs_a_session_once_a_password_exists(self):
        self.setup_password()
        res = self.post("/api/auth/register/options", {}, self.new_client())
        self.assertEqual(res.status_code, 401)
        self.assertEqual(self.post("/api/auth/register/options", {}).status_code, 200)


if __name__ == "__main__":
    unittest.main()


class MasterSecretTest(unittest.TestCase):
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
        first = auth.master_secret()
        self.assertGreaterEqual(len(first), 64)
        auth._master_secrets.clear()
        self.assertEqual(auth.master_secret(), first, "blijft gelijk na een herstart")
        self.assertEqual(oct(os.stat(self.path).st_mode & 0o777), "0o600")
        os.remove(self.path)
        auth._master_secrets.clear()
        self.assertNotEqual(auth.master_secret(), first)

    def test_sessions_and_stored_data_use_different_keys(self):
        self.assertNotEqual(auth.signing_secret(), auth.master_secret())
        box_key = auth._derive("stored-secrets")
        self.assertNotEqual(box_key.hex(), auth.signing_secret())

    def test_a_session_signed_with_the_old_guessable_secret_is_rejected(self):
        app = create_app()
        client = app.test_client()
        client.post("/api/auth/password", json={"username": "admin", "password": PASSWORD})
        guessable = auth.hashlib.sha256(f"{auth.db_path()}:mealieshopper".encode()).hexdigest()  # de vroegere sleutel
        forged = auth.jwt.encode({"sub": "owner", "usr": "admin", "ep": 0, "exp": auth.utcnow() + auth.timedelta(hours=1)}, guessable, algorithm="HS256")
        attacker = app.test_client()
        attacker.set_cookie(auth.SESSION_COOKIE_NAME, forged)
        self.assertFalse(attacker.get("/api/auth/status").get_json()["authenticated"])
        self.assertEqual(attacker.get("/api/ah/lists/search?q=x").status_code, 401)
        self.assertTrue(client.get("/api/auth/status").get_json()["authenticated"], "de echte sessie werkt")

    def test_configured_secret_wins_and_nothing_is_written(self):
        with mock.patch.dict(os.environ, {"MEALIESHOPPER_AUTH_SECRET": " zelf-gekozen "}):
            self.assertEqual(auth.master_secret(), "zelf-gekozen")
            auth.init_db()
            auth.set_secret("X", "waarde")
            self.assertEqual(auth.get_secret("X"), "waarde")
        self.assertFalse(os.path.exists(self.path), "de sleutel staat dan niet op schijf")

    def test_jwt_secret_is_still_accepted(self):
        with mock.patch.dict(os.environ, {"JWT_SECRET": "oude-naam"}):
            self.assertEqual(auth.master_secret(), "oude-naam")

    def test_stored_secrets_round_trip_and_are_not_plain_text(self):
        auth.init_db()
        auth.set_secret("MEALIE_API_TOKEN", "geheime-token")
        self.assertEqual(auth.get_secret("MEALIE_API_TOKEN"), "geheime-token")
        with auth.closing(auth.connect()) as conn:
            stored = conn.execute("SELECT value FROM app_settings WHERE key = 'MEALIE_API_TOKEN'").fetchone()[0]
        self.assertNotIn("geheime-token", stored)
        self.assertTrue(stored.startswith("gAAAA"))

    def test_data_encrypted_with_another_key_is_unreadable_not_garbage(self):
        auth.init_db()
        auth.set_secret("MEALIE_API_TOKEN", "geheime-token")
        os.remove(self.path)
        auth._master_secrets.clear()  # nieuwe sleutel: bijvoorbeeld een data-map zonder secret.key teruggezet
        self.assertEqual(auth.get_secret("MEALIE_API_TOKEN"), "", "geen versleutelde tekst teruggeven als token")

    def test_plain_settings_are_still_returned_as_they_are(self):
        auth.init_db()
        auth.set_setting("AUTH_MODE", "none")
        self.assertEqual(auth.get_secret("AUTH_MODE"), "none")

    def test_the_key_does_not_depend_on_mealie_url(self):
        before = auth.master_secret()
        with mock.patch.dict(os.environ, {"MEALIE_URL": "https://ergens.example"}):
            auth._master_secrets.clear()
            self.assertEqual(auth.master_secret(), before)

    def test_an_incomplete_file_is_waited_for_then_reported(self):
        with open(self.path, "w") as handle:
            handle.write("kort")
        with mock.patch.object(auth.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "onleesbaar of onvolledig"):
                auth.master_secret()
        with open(self.path) as handle:
            self.assertEqual(handle.read(), "kort", "een bestaand bestand wordt nooit overschreven")

    def test_a_file_that_is_still_being_written_is_read_once_complete(self):
        with open(self.path, "w") as handle:
            handle.write("")
        full = "ab" * 32
        calls = {"n": 0}

        def finish(_):
            calls["n"] += 1
            if calls["n"] == 2:
                with open(self.path, "w") as handle:
                    handle.write(full)

        with mock.patch.object(auth.time, "sleep", side_effect=finish):
            self.assertEqual(auth._read_secret_file(auth.Path(self.path), wait=True), full)
