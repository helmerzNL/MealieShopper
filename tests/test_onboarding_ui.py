import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

from mealieshopper.app import create_app  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
APP_JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
OB_JS = (ROOT / "static" / "onboarding.js").read_text(encoding="utf-8")


class MarkupTest(unittest.TestCase):
    def test_login_page_has_password_mfa_and_passkey_parts(self):
        gate = HTML[HTML.index('id="auth-gate"'):HTML.index('id="onboarding"')]
        for needle in ('id="login-form"', 'id="login-username"', 'autocomplete="username"',
                       'id="login-password"', 'autocomplete="current-password"', 'id="mfa-form"',
                       'autocomplete="one-time-code"', 'id="mfa-recovery-toggle"', 'id="login-passkey"'):
            self.assertIn(needle, gate, needle)
        self.assertRegex(gate, r'<input[^>]*id="login-password"[^>]*type="password"|<input[^>]*type="password"[^>]*id="login-password"')
        self.assertNotIn("auth-primary", HTML, "het oude passkey-aanmaakformulier is naar de installatiehulp verhuisd")

    def test_onboarding_shell_is_an_accessible_dialog_and_starts_hidden(self):
        shell = re.search(r'<section id="onboarding"[^>]*>', HTML).group(0)
        for needle in ('role="dialog"', 'aria-modal="true"', 'aria-labelledby="ob-title"', "hidden"):
            self.assertIn(needle, shell, needle)
        for needle in ('id="ob-progress"', 'id="ob-body"', 'id="ob-message"', 'role="alert"'):
            self.assertIn(needle, HTML, needle)

    def test_security_tab_manages_password_and_mfa(self):
        security = HTML[HTML.index('id="beheer-security"'):HTML.index("</section>", HTML.index('id="beheer-security"'))]
        for needle in ('id="password-form"', 'id="pw-current"', 'autocomplete="new-password"', 'id="mfa-panel"', 'id="credentials-list"'):
            self.assertIn(needle, security, needle)

    def test_scripts_load_in_order(self):
        self.assertLess(HTML.index("app.js"), HTML.index("onboarding.js"))

    def test_page_is_served_with_both_scripts_and_the_vendored_qr_code(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with mock.patch.dict(os.environ, {"MEALIESHOPPER_DATA_DIR": tmp.name}):
            client = create_app().test_client()
            html = client.get("/").get_data(as_text=True)
            self.assertIn("onboarding.js?v=", html)
            self.assertEqual(client.get("/static/onboarding.js").status_code, 200)
            qr = client.get("/static/vendor/qrcode-2.0.4.js")
            self.assertEqual(qr.status_code, 200)
            self.assertIn("Kazuhiko Arase", qr.get_data(as_text=True)[:400])
            self.assertEqual(client.get("/static/vendor/LICENSE-qrcode.txt").status_code, 200)


class ScriptTest(unittest.TestCase):
    def test_four_steps_in_the_asked_order(self):
        ids = re.findall(r'\{ id: "(\w+)", label', OB_JS)
        self.assertEqual(ids, ["auth", "mealie", "ah", "done"])

    def test_security_step_follows_the_domain_rule(self):
        for needle in ("status.passkeyAvailable", "go-passkey", "go-password", "go-open", "passkeyUnavailableReason",
                       "Passkeys werken op dit adres niet", "Zonder beveiliging verder"):
            self.assertIn(needle, OB_JS, needle)
        # Zonder domeinnaam is het wachtwoord de aanbevolen keuze en is overslaan mogelijk;
        # met een domeinnaam staat de passkey bovenaan en is er geen 'zonder beveiliging'.
        with_domain = OB_JS[OB_JS.index("body.innerHTML = status.passkeyAvailable"):OB_JS.index("function obBackButton")]
        domain_part, no_domain_part = with_domain.split(": `", 1)
        self.assertNotIn("go-open", domain_part)
        self.assertIn("go-open", no_domain_part)

    def test_mfa_is_optional_and_recovery_codes_must_be_acknowledged(self):
        for needle in ("skip-mfa", "Overslaan", "data-recovery-saved", "disabled", "niet meer getoond"):
            self.assertIn(needle, OB_JS, needle)

    def test_explanations_for_mealie_and_ah(self):
        for needle in ("API-tokens", "Token genereren", "Beheer &gt; Mealie", "http://mealie:9000",
                       "appie://login-exit?code=", "ontwikkelaarsconsole", "computer"):
            self.assertIn(needle, OB_JS, needle)

    def test_opt_out_needs_an_explicit_confirmation(self):
        self.assertIn("ob-open-confirm", OB_JS)
        self.assertIn('postJson("/api/auth/open", { confirm: true })', OB_JS)

    def test_user_values_are_escaped(self):
        self.assertIn("escapeHtml(setup.otpauthUri)", OB_JS)
        self.assertIn("escapeHtml(code)", OB_JS)
        self.assertNotIn("innerHTML = error", OB_JS)

    def test_app_waits_for_the_last_script_before_starting(self):
        self.assertIn('document.addEventListener("DOMContentLoaded"', APP_JS)

    def test_login_page_hides_when_the_wizard_takes_over(self):
        self.assertIn("onboardingVisible()", APP_JS)
        self.assertIn("!auth.setupRequired", APP_JS)


class StyleTest(unittest.TestCase):
    def test_overlay_covers_the_page_and_scrolls(self):
        rule = re.search(r"\n\.onboarding \{(.*?)\n\}", CSS, re.S).group(1)
        for declaration in ("position: fixed", "inset: 0", "overflow-y: auto", "z-index: 100"):
            self.assertIn(declaration, rule)
        self.assertIn("env(safe-area-inset-bottom)", rule)
        self.assertIn("body.has-onboarding", CSS)

    def test_qr_code_stays_black_on_white(self):
        self.assertRegex(re.search(r"\n\.qr \{(.*?)\n\}", CSS, re.S).group(1), r"background: #fff")

    def test_phone_layout_stacks_actions_and_single_qr_column(self):
        phone = CSS[CSS.index(".onboarding__card {\n    padding: 18px 16px;"):]
        phone = phone[:phone.index("\n}\n")]
        self.assertIn("flex-direction: column-reverse", phone)
        self.assertIn("grid-template-columns: 1fr;", phone)

    def test_checkbox_and_button_targets_are_large_enough(self):
        self.assertIn("width: 20px", re.search(r"\n\.check input \{(.*?)\n\}", CSS, re.S).group(1))
        self.assertIn("min-height: var(--control-height)", re.search(r"\n\.link-button \{(.*?)\n\}", CSS, re.S).group(1))


if __name__ == "__main__":
    unittest.main()
