import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

from helpers import AppTestCase  # noqa: E402

from mealieshopper.app import create_app  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
APP_JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
OB_JS = (ROOT / "static" / "onboarding.js").read_text(encoding="utf-8")


class MarkupTest(AppTestCase):
    def test_login_page_has_password_mfa_and_passkey_parts(self):
        gate = HTML[HTML.index('id="auth-gate"') : HTML.index('id="onboarding"')]
        for needle in (
            'id="login-form"',
            'id="login-username"',
            'autocomplete="username"',
            'id="login-password"',
            'autocomplete="current-password"',
            'id="mfa-form"',
            'autocomplete="one-time-code"',
            'id="mfa-recovery-toggle"',
            'id="login-passkey"',
        ):
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
        security = HTML[HTML.index('id="profile-security"') : HTML.index("</section>", HTML.index('id="profile-security"'))]
        for needle in ('id="password-form"', 'id="pw-current"', 'autocomplete="new-password"', 'id="mfa-panel"', 'id="credentials-list"'):
            self.assertIn(needle, security, needle)

    def test_scripts_load_in_order(self):
        self.assertLess(HTML.index("app.js"), HTML.index("onboarding.js"))

    async def test_page_is_served_with_both_scripts_and_the_vendored_qr_code(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with mock.patch.dict(os.environ, {"MEALIESHOPPER_DATA_DIR": tmp.name}):
            client = self.client_for(create_app())
            html = (await client.get("/")).get_data(as_text=True)
            self.assertIn("onboarding.js?v=", html)
            self.assertEqual((await client.get("/static/onboarding.js")).status_code, 200)
            qr = await client.get("/static/vendor/qrcode-2.0.4.js")
            self.assertEqual(qr.status_code, 200)
            self.assertIn("Kazuhiko Arase", qr.get_data(as_text=True)[:400])
            self.assertEqual((await client.get("/static/vendor/LICENSE-qrcode.txt")).status_code, 200)


def dictionary(lang: str) -> dict[str, str]:
    source = (ROOT / "static" / "i18n" / f"{lang}.js").read_text(encoding="utf-8")
    import json

    return {json.loads(f'"{k}"'): json.loads(f'"{v}"') for k, v in re.findall(r'^  "([^"]+)": "((?:[^"\\]|\\.)*)",$', source, re.M)}


NL = dictionary("nl")
EN = dictionary("en")


class ScriptTest(AppTestCase):
    def test_the_steps_follow_the_chosen_stores(self):
        # Sinds 4.13.0: beveiliging, Mealie, de winkelkeuze, een stap per gekozen winkel (in vaste volgorde) en Klaar.
        if not shutil.which("node"):
            self.skipTest("Node is niet geïnstalleerd")
        source = "\n".join(
            re.search(rf"^(?:const {name} = [^\n]*;|function {name}\(.*?^}})$", OB_JS, re.S | re.M).group(0)
            for name in ("OB_STORE_STEPS", "obSteps", "obNeighbour")
        )
        script = (
            source
            + """
        const ob = { stores: null };
        const ids = (stores) => obSteps(stores).map((step) => step.id);
        console.log(JSON.stringify({
          none: ids(null), empty: ids([]), picnic: ids(["picnic", "crisp"]), all: ids(["picnic", "ah", "jumbo", "crisp"]),
          next: [obNeighbour("mealie", 1, ["jumbo"]), obNeighbour("stores", 1, ["jumbo"]), obNeighbour("jumbo", 1, ["jumbo"]), obNeighbour("stores", 1, [])],
          back: [obNeighbour("jumbo", -1, ["jumbo"]), obNeighbour("done", -1, ["ah", "picnic"]), obNeighbour("auth", -1, [])],
        }));
        """
        )
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(out["none"], ["auth", "mealie", "stores", "done"])
        self.assertEqual(out["empty"], ["auth", "mealie", "stores", "done"])
        self.assertEqual(out["picnic"], ["auth", "mealie", "stores", "picnic", "done"], "Crisp heeft geen eigen stap")
        self.assertEqual(out["all"], ["auth", "mealie", "stores", "ah", "jumbo", "picnic", "done"])
        self.assertEqual(out["next"], ["stores", "jumbo", "done", "done"])
        self.assertEqual(out["back"], ["stores", "picnic", "auth"])
        for step in ("auth", "mealie", "stores", "ah", "jumbo", "picnic", "done"):
            self.assertIn(f"ob.step.{step}", NL)
            self.assertIn(f"ob.title.{step}", NL)

    def test_the_last_step_shows_the_chosen_stores_crisp_and_a_first_task(self):
        done = re.search(r"^async function renderObDone\(.*?^}$", OB_JS, re.S | re.M).group(0)
        self.assertIn("const chosen = ob.stores || OB_STORE_STEPS;", done, "een hulp van voor 4.13.0 toont de drie van voorheen")
        self.assertIn('data-ob-action="link-crisp"', done)
        self.assertIn('t(mealie.configured ? "ob.done.first_meal" : "ob.done.open")', done)
        self.assertIn('"link-crisp": () => finishOnboarding({ then: () => openLinkBlock("crisp") })', OB_JS)
        self.assertIn("Crisp", NL["ob.welcome"])

    def test_security_step_follows_the_domain_rule(self):
        for needle in ("status.passkeyAvailable", "go-passkey", "go-password", "go-open", "passkeyUnavailableReason"):
            self.assertIn(needle, OB_JS, needle)
        self.assertIn("Passkeys werken op dit adres niet", NL["ob.nopasskey.title"])
        self.assertIn("Zonder beveiliging verder", NL["ob.skip_security"])
        # Zonder domeinnaam is het wachtwoord de aanbevolen keuze en is overslaan mogelijk;
        # met een domeinnaam staat de passkey bovenaan en is er geen 'zonder beveiliging'.
        choose = OB_JS[OB_JS.index("body.innerHTML = status.passkeyAvailable") : OB_JS.index("function obBackButton")]
        domain_part, no_domain_part = choose.split(": `", 1)
        self.assertNotIn("go-open", domain_part)
        self.assertIn("go-open", no_domain_part)

    def test_mfa_is_optional_and_recovery_codes_must_be_acknowledged(self):
        for needle in ("skip-mfa", "data-recovery-saved", "disabled"):
            self.assertIn(needle, OB_JS, needle)
        self.assertIn("Overslaan", NL["ob.mfa.skip"])
        self.assertIn("niet meer getoond", NL["mfa.recovery.text"])

    def test_explanations_for_mealie_and_ah(self):
        mealie = " ".join(NL[k] for k in NL if k.startswith("ob.mealie."))
        ah = " ".join(NL[k] for k in NL if k.startswith("ob.ah."))
        for needle in ("API-tokens", "Token genereren", "http://mealie:9000"):
            self.assertIn(needle, mealie, needle)
        for needle in ("appie://login-exit?code=", "ontwikkelaarsconsole", "computer"):
            self.assertIn(needle, ah, needle)

    def test_mealie_and_ah_are_linked_inside_the_wizard(self):
        # Geen sprong naar Beheer: de formulieren staan in de stappen en je gaat daarna gewoon door.
        for needle in (
            'data-ob-form="mealie"',
            'data-ob-form="ah"',
            '"/api/mealie/auth"',
            '"/api/ah/auth"',
            "obIntegrationDone",
            'id="ob-mealie-next"',
            'id="ob-ah-next"',
            'id="ob-mealie-skip"',
            'id="ob-ah-skip"',
        ):
            self.assertIn(needle, OB_JS, needle)
        self.assertNotIn("finish-mealie", OB_JS)
        self.assertNotIn("openSettings", OB_JS)
        self.assertNotIn("openProfile", OB_JS)

    def test_jumbo_and_picnic_are_linked_inside_the_wizard_and_can_be_skipped(self):
        for needle in (
            'data-ob-form="jumbo"',
            '"/api/jumbo/auth"',
            '"/api/jumbo/auth/status"',
            'href="/api/jumbo/auth/start"',
            'id="ob-jumbo-next"',
            'id="ob-jumbo-skip"',
            'data-ob-form="picnic"',
            'data-ob-form="picnic-code"',
            '"/api/picnic/auth"',
            '"/api/picnic/auth/status"',
            '"/api/picnic/auth/2fa/request"',
            '"/api/picnic/auth/2fa/verify"',
            "twoFactorRequired",
            "twoFactorPending",
            'id="ob-picnic-next"',
            'id="ob-picnic-skip"',
            "refreshJumboStatus",
            "refreshPicnicStatus",
        ):
            self.assertIn(needle, OB_JS, needle)
        # Sinds 4.13.0 hangt de volgende stap af van de gekozen winkels: de knoppen heten next en back, en de samenvatting kan
        # naar elke gekozen stap terug.
        for target in ("mealie", "ah", "jumbo", "picnic"):
            self.assertIn(f'id="ob-{target}-next" data-ob-action="next"', OB_JS, target)
            self.assertIn(f'id="ob-{target}-skip" data-ob-action="next"', OB_JS, target)
        for action in ("go-jumbo", "go-picnic"):
            self.assertIn(f'data-ob-action="{action}">${{escapeHtml(t("ob.done.link_', OB_JS, action)

    def test_explanations_for_jumbo_and_picnic(self):
        jumbo = " ".join(NL[k] for k in NL if k.startswith(("ob.jumbo.", "jumbo.step")))
        picnic = " ".join(NL[k] for k in NL if k.startswith("ob.picnic."))
        for needle in ("jumbo://callbacks/login?code=", "ontwikkelaarsconsole", "computer"):
            self.assertIn(needle, jumbo, needle)
        for needle in ("e-mailadres", "wachtwoord", "sms"):
            self.assertIn(needle, picnic, needle)

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

    def test_security_blocks_fold_away_when_a_passkey_exists(self):
        self.assertIn("collapsible: hasPasskey", OB_JS)
        # Tweestapsverificatie is geen eigen blok meer: het zit in het wachtwoordblok.
        for name in ("password",):
            self.assertIn(f'id="{name}-toggle"', HTML)
            self.assertIn(f'id="{name}-body"', HTML)
            self.assertIn(f'id="{name}-badge"', HTML)
        self.assertNotIn('id="mfa-toggle"', HTML)
        body = HTML[HTML.index('id="password-body"') : HTML.index('id="credentials-list"')]
        self.assertIn('id="mfa-panel"', body)
        self.assertIn("Niet nodig", NL["sec.not_needed"])
        self.assertIn("Niet nodig", NL["sec.password.intro_passkey"])
        self.assertIn("passkey", NL["sec.password.intro_passkey"])
        self.assertIn("Niet nodig", NL["sec.mfa.optional_note"])
        self.assertIn("gebruikersnaam en wachtwoord", NL["sec.mfa.optional_note"])


class RestoreTest(AppTestCase):
    """Een back-up terugzetten in de installatiehulp: de stap Mealie biedt het aan; daarna gaat de hulp naar de homepage of verder
    met wat er nog niet gekoppeld is."""

    def node(self, body: str) -> object:
        if not shutil.which("node"):
            self.skipTest("Node is niet geïnstalleerd")
        source = "\n".join(
            re.search(rf"^(?:const {name} = [^\n]*;|function {name}\(.*?^}})$", OB_JS, re.S | re.M).group(0)
            for name in ("OB_STORE_STEPS", "OB_STORE_CHOICES", "obMissingLinks", "obStepForMissing")
        )
        result = subprocess.run(["node", "-e", source + body], capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_what_is_missing_and_where_the_wizard_goes_next(self):
        out = self.node(
            """
        const linked = { mealie: { configured: true }, ah: { connected: true }, jumbo: { connected: true }, picnic: { connected: true }, crisp: { connected: true } };
        const without = (...names) => Object.fromEntries(Object.entries(linked).map(([name, value]) => [name, names.includes(name) ? {} : value]));
        const all = ["ah", "jumbo", "picnic", "crisp"];
        const case_ = (status, stores) => { const missing = obMissingLinks(status, stores); return [missing, obStepForMissing(missing)]; };
        console.log(JSON.stringify({
          nothing: case_(linked, all),
          mealie: case_(without("mealie"), []),
          everything: case_({ mealie: {}, ah: {}, jumbo: {}, picnic: {}, crisp: {} }, all),
          second: case_(without("jumbo"), all),
          order: case_(without("picnic", "ah"), all),
          crispOnly: case_(without("crisp"), all),
          crispAndPicnic: case_(without("crisp", "picnic"), all),
          notChosen: case_({ mealie: { configured: true } }, []),
          chosenButNotLinked: case_({ mealie: { configured: true } }, ["jumbo"]),
          empty: case_({}, []),
        }));
        """
        )
        self.assertEqual(out["nothing"], [[], None], "alles gekoppeld: geen stap, naar de homepage")
        self.assertEqual(out["mealie"], [["mealie"], "mealie"])
        self.assertEqual(out["everything"], [["mealie", "ah", "jumbo", "picnic", "crisp"], "mealie"], "Mealie gaat voor")
        self.assertEqual(out["second"], [["jumbo"], "jumbo"])
        self.assertEqual(out["order"], [["ah", "picnic"], "ah"], "in de volgorde van de hulp")
        self.assertEqual(out["crispOnly"], [["crisp"], "done"], "Crisp heeft geen eigen stap: Klaar heeft zijn knop")
        self.assertEqual(out["crispAndPicnic"], [["picnic", "crisp"], "picnic"])
        self.assertEqual(out["notChosen"], [[], None], "een winkel die je niet koos, ontbreekt niet")
        self.assertEqual(out["chosenButNotLinked"], [["jumbo"], "jumbo"])
        self.assertEqual(out["empty"], [["mealie"], "mealie"])

    def test_the_mealie_step_offers_it_and_the_restore_has_its_own_screens(self):
        mealie = re.search(r"^function renderObMealie\(.*?^}$", OB_JS, re.S | re.M).group(0)
        self.assertIn("${obRestoreOffer()}", mealie)
        self.assertIn("renderObRestore", mealie)
        self.assertIn('data-ob-action="go-restore"', OB_JS)
        for sub in ("restore", "restoring", "restored"):
            self.assertIn(f"{sub}:", OB_JS.split("const OB_RESTORE_TITLES")[1].split("\n")[0], sub)
        self.assertIn('"go-restore": () => obGo("mealie", "restore")', OB_JS)
        # Het terugzetten staat na de beveiliging: de hulp biedt het pas aan als de inlog er is (de back-up bevat hem nooit).
        self.assertLess(OB_JS.index("function renderObAuth"), OB_JS.index("function renderObMealie"))

    def test_it_uses_the_routes_and_helpers_of_the_settings_screen(self):
        for needle in (
            'uploadInChunks(file, "/api/backup/uploads"',
            '"/api/backup/jobs"',
            '"/api/backup/jobs/latest"',
            "/api/backup/jobs/${job.id}",
            "/api/backup/stage/${token}",
            "backupRenderRestoreParts(summary",
            "backupChoices(",
            "backupRenderJob(job, obRestoreProgress)",
            "backupLoadInfo()",
        ):
            self.assertIn(needle, OB_JS, needle)
        self.assertNotIn("file.text()", OB_JS, "het bestand wordt op de server gecontroleerd, niet in de browser")

    def test_the_restore_belongs_to_the_server_and_the_page_reloads_only_when_it_can_pick_up_again(self):
        # Een klus die nog loopt of net klaar is (de pagina was dicht) pakt de hulp bij het openen weer op.
        self.assertIn("obResumeRestore()", OB_JS)
        # Herladen alleen als de klus is weggeklikt en de hulp na het herladen weet dat er is teruggezet; anders blijft de pagina.
        self.assertIn("if (!dismissed || !marked) {", OB_JS)
        self.assertIn('window.sessionStorage.setItem(OB_RESTORED_KEY, "1")', OB_JS)
        self.assertIn("window.location.reload()", OB_JS)
        self.assertEqual(OB_JS.count("window.location.reload()"), 1)
        # De stappen van wat teruggezet is komen bij de hulp (winkels met een koppeling of gegevens in het bestand).
        self.assertIn("job.summary?.stores", OB_JS)
        self.assertIn("obSaveStores(", OB_JS)

    def test_the_language_can_change_in_the_middle_of_a_restore(self):
        # Een bestandsveld laat zich niet vullen (alleen leegmaken), en de vinkjes van de onderdelen hebben geen id.
        self.assertIn("input[id]:not([type=file])", OB_JS)
        self.assertIn('backupChoices("#ob-restore-parts")', OB_JS.split('window.addEventListener("ms:languagechange"')[1])
        self.assertIn("backupApplyChoices(", OB_JS)

    def test_the_last_step_and_the_restore_share_the_checklist(self):
        for name in ("obLinkStatuses", "obChecklistHtml"):
            self.assertIn(f"function {name}(", OB_JS)
        self.assertIn("obChecklistHtml(status, chosen)", re.search(r"^async function renderObDone\(.*?^}$", OB_JS, re.S | re.M).group(0))
        self.assertIn("obChecklistHtml(status, chosen)", re.search(r"^async function renderObRestored\(.*?^}$", OB_JS, re.S | re.M).group(0))

    def test_every_text_exists_in_both_languages(self):
        keys = [key for key in NL if key.startswith("ob.restore.")] + ["ob.title.restore", "ob.title.restored"]
        self.assertGreaterEqual(len(keys), 12)
        for key in keys:
            self.assertIn(key, EN, key)
            self.assertEqual(re.findall(r"\{(\w+)\}", NL[key]), re.findall(r"\{(\w+)\}", EN[key]), key)
        for key in (
            "ob.restore.offer_title",
            "ob.restore.offer_text",
            "ob.restore.offer_button",
            "ob.restore.intro",
            "ob.restore.other_file",
            "ob.restore.restored",
            "ob.restore.all_linked",
            "ob.restore.missing",
            "ob.restore.continue",
            "ob.restore.home",
        ):
            self.assertIn(f'"{key}"', OB_JS, key)
        self.assertIn(".json.gz", NL["ob.restore.intro"])
        self.assertIn("homepage", EN["ob.restore.home"])

    def test_the_offer_has_a_style(self):
        self.assertIn(".ob-restore-offer {", CSS)
        self.assertIn(".onboarding .backup-parts__list .check {", CSS)


class StyleTest(AppTestCase):
    def test_overlay_covers_the_page_and_scrolls(self):
        rule = re.search(r"\n\.onboarding \{(.*?)\n\}", CSS, re.S).group(1)
        for declaration in ("position: fixed", "inset: 0", "overflow-y: auto", "z-index: 100"):
            self.assertIn(declaration, rule)
        self.assertIn("env(safe-area-inset-bottom)", rule)
        self.assertIn("body.has-onboarding", CSS)

    def test_qr_code_stays_black_on_white(self):
        self.assertRegex(re.search(r"\n\.qr \{(.*?)\n\}", CSS, re.S).group(1), r"background: #fff")

    def test_phone_layout_stacks_actions_and_single_qr_column(self):
        phone = CSS[CSS.index(".onboarding__card {\n    padding: 18px 16px;") :]
        phone = phone[: phone.index("\n}\n")]
        self.assertIn("flex-direction: column-reverse", phone)
        self.assertIn("grid-template-columns: 1fr;", phone)

    def test_checkbox_and_button_targets_are_large_enough(self):
        self.assertIn("width: 20px", re.search(r"\n\.check input \{(.*?)\n\}", CSS, re.S).group(1))
        self.assertIn("min-height: var(--control-height)", re.search(r"\n\.link-button \{(.*?)\n\}", CSS, re.S).group(1))


if __name__ == "__main__":
    unittest.main()
