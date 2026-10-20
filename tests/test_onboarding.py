import os
import tempfile
import unittest
from contextlib import closing
from unittest import mock

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

from mealieshopper import ah, auth, auth_password, mealie, onboarding  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402

PASSWORD = "koffie-met-melk-42"


class OnboardingTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(
            os.environ,
            {"PASSKEY_AUTH_ENABLED": "true", "MEALIESHOPPER_DATA_DIR": tmp.name, "MEALIESHOPPER_AUTH_SECRET": "test-secret"},
        )
        env.start()
        self.addCleanup(env.stop)
        for target, name, value in ((auth_password, "SCRYPT_N", 2**4),):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        # Geen echte Mealie of AH-koppeling in de omgeving van de testmachine.
        for target, name in ((mealie, "mealie_url"), (mealie, "mealie_token")):
            patcher = mock.patch.object(target, name, return_value="")
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(ah, "configured_refresh_token", return_value="")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.app = create_app()
        self.client = self.app.test_client()

    def status(self, client=None, **kwargs):
        return (client or self.client).get("/api/onboarding/status", **kwargs).get_json()

    def add_passkey_row(self):
        auth.ensure_db()
        with closing(auth.connect()) as conn, conn:
            conn.execute("INSERT OR IGNORE INTO users (id, username, created_at) VALUES ('owner', 'admin', 'x')")
            conn.execute(
                "INSERT INTO passkey_credentials (id, user_id, public_key, credential_name, created_at) VALUES ('c1', 'owner', x'00', 'Telefoon', 'x')"
            )


class HostnameTest(unittest.TestCase):
    def test_real_domains(self):
        for host in ("mealie.example.com", "shop.jouwdomein.nl", "a.b.c.example.org", "EXAMPLE.com.", "xn--bcher-kva.example"):
            self.assertTrue(onboarding.hostname_info(host)["isDomain"], host)

    def test_addresses_without_a_domain(self):
        cases = {
            "192.168.1.20": "een IP-adres", "10.0.0.5": "een IP-adres", "::1": "een IP-adres", "[fe80::1]": "een IP-adres",
            "localhost": "localhost", "app.localhost": "localhost", "nas": "een naam zonder domein",
            "mealie.local": "alleen in je eigen netwerk", "box.lan": "alleen in je eigen netwerk",
            "x.home.arpa": "alleen in je eigen netwerk", "host.internal": "alleen in je eigen netwerk",
        }
        for host, reason in cases.items():
            info = onboarding.hostname_info(host)
            self.assertFalse(info["isDomain"], host)
            self.assertIn(reason, info["reason"], host)

    def test_empty(self):
        self.assertFalse(onboarding.hostname_info("")["isDomain"])


class EnvironmentTest(OnboardingTestCase):
    def test_ip_address_over_http_means_no_passkey(self):
        status = self.status(base_url="http://192.168.1.20:8080")
        self.assertFalse(status["hasDomain"])
        self.assertFalse(status["passkeyAvailable"])
        self.assertIn("192.168.1.20", status["passkeyUnavailableReason"])
        self.assertIn("domeinnaam", status["passkeyUnavailableReason"])

    def test_domain_over_https_allows_passkeys(self):
        status = self.status(base_url="https://shop.example.com", headers={"X-Forwarded-Proto": "https"})
        self.assertTrue(status["hasDomain"])
        self.assertTrue(status["secure"])
        self.assertTrue(status["passkeyAvailable"])
        self.assertEqual(status["passkeyUnavailableReason"], "")

    def test_domain_without_https_explains_the_https_requirement(self):
        status = self.status(base_url="http://shop.example.com")
        self.assertTrue(status["hasDomain"])
        self.assertFalse(status["passkeyAvailable"])
        self.assertIn("HTTPS", status["passkeyUnavailableReason"])

    def test_configured_rp_id_wins_over_the_request_host(self):
        with mock.patch.dict(os.environ, {"RP_ID": "shop.example.com"}):
            status = self.status(base_url="https://10.0.0.5", headers={"X-Forwarded-Proto": "https"})
        self.assertTrue(status["hasDomain"])
        self.assertEqual(status["host"], "shop.example.com")


class LifecycleTest(OnboardingTestCase):
    def test_new_install_needs_onboarding(self):
        status = self.status()
        self.assertTrue(status["needed"])
        self.assertFalse(status["authDone"])
        self.assertFalse(status["authenticated"])
        self.assertEqual(status["authMode"], "")

    def test_existing_install_with_a_passkey_is_left_alone(self):
        self.add_passkey_row()
        status = self.status()
        self.assertFalse(status["needed"])
        self.assertEqual(status["authMode"], "passkey")

    def test_existing_install_with_a_password_is_left_alone(self):
        auth.ensure_db()
        auth_password._store_password("admin", PASSWORD)
        self.assertFalse(self.status()["needed"])
        self.assertEqual(self.status()["authMode"], "password")

    def test_existing_install_with_login_off_and_mealie_linked_is_left_alone(self):
        with mock.patch.dict(os.environ, {"PASSKEY_AUTH_ENABLED": "false"}), \
                mock.patch.object(mealie, "mealie_url", return_value="https://m"), \
                mock.patch.object(mealie, "mealie_token", return_value="t"):
            status = self.status()
        self.assertFalse(status["needed"])
        self.assertEqual(status["authMode"], "off")

    def test_new_install_with_login_off_still_gets_the_explanation(self):
        with mock.patch.dict(os.environ, {"PASSKEY_AUTH_ENABLED": "false"}):
            status = self.status()
        self.assertTrue(status["needed"])
        self.assertTrue(status["authDone"], "de beveiligingsstap valt weg")
        self.assertTrue(status["authenticated"])

    def test_mealie_in_the_environment_does_not_skip_security_on_a_new_install(self):
        with mock.patch.object(mealie, "mealie_url", return_value="https://m"), \
                mock.patch.object(mealie, "mealie_token", return_value="t"):
            self.assertTrue(self.status()["needed"])

    def test_it_stays_needed_after_the_first_login_method_until_completed(self):
        self.assertEqual(self.client.post("/api/auth/password", json={"username": "admin", "password": PASSWORD}).status_code, 200)
        status = self.status()
        self.assertTrue(status["needed"], "de wizard verdwijnt niet halverwege")
        self.assertTrue(status["authDone"])
        self.assertTrue(status["authenticated"])
        self.assertEqual(status["authMode"], "password")
        self.assertEqual(self.client.post("/api/onboarding/complete").status_code, 200)
        self.assertFalse(self.status()["needed"])
        self.assertFalse(self.status(self.app.test_client())["needed"], "ook voor een ander apparaat")

    def test_skipping_security_also_stays_needed_until_completed(self):
        self.client.post("/api/auth/open", json={"confirm": True})
        status = self.status()
        self.assertTrue(status["needed"])
        self.assertTrue(status["authDone"])
        self.assertEqual(status["authMode"], "none")
        self.assertFalse(status["authRequired"])
        self.client.post("/api/onboarding/complete")
        self.assertFalse(self.status()["needed"])

    def test_start_marks_a_new_install_as_busy(self):
        self.client.post("/api/onboarding/start")
        self.assertEqual(auth.get_setting(onboarding.SETTING_ACTIVE), "1")
        # Nu inloggen verderop in de wizard wijzigt niets meer aan de status.
        self.client.post("/api/auth/open", json={"confirm": True})
        self.assertTrue(self.status()["needed"])

    def test_start_does_nothing_on_an_existing_install(self):
        self.add_passkey_row()
        self.client.post("/api/onboarding/start")
        self.assertEqual(auth.get_setting(onboarding.SETTING_ACTIVE), "")
        self.assertFalse(self.status()["needed"])

    def test_completing_is_remembered_for_good(self):
        self.client.post("/api/auth/open", json={"confirm": True})
        self.client.post("/api/onboarding/complete")
        self.client.post("/api/onboarding/start")
        self.assertFalse(self.status()["needed"])


class AccessTest(OnboardingTestCase):
    def test_status_and_start_are_public_but_complete_is_not(self):
        self.client.post("/api/auth/password", json={"username": "admin", "password": PASSWORD})
        stranger = self.app.test_client()
        self.assertEqual(stranger.get("/api/onboarding/status").status_code, 200)
        self.assertEqual(stranger.post("/api/onboarding/start").status_code, 200)
        self.assertEqual(stranger.post("/api/onboarding/complete").status_code, 401)
        self.assertTrue(self.status()["needed"], "een vreemde kan de onboarding niet afsluiten")

    def test_status_reveals_nothing_secret(self):
        self.client.post("/api/auth/password", json={"username": "admin", "password": PASSWORD})
        text = str(self.status(self.app.test_client()))
        for secret in (PASSWORD, "test-secret", "admin"):
            self.assertNotIn(secret, text)

    def test_other_endpoints_stay_closed_on_a_new_install(self):
        for path in ("/api/mealie/status", "/api/ah/lists", "/api/mealplan?start=a&end=b"):
            self.assertEqual(self.client.get(path).status_code, 401, path)


if __name__ == "__main__":
    unittest.main()
