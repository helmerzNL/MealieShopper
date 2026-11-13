import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

from helpers import AppTestCase, run  # noqa: E402

from mealieshopper import auth_password, i18n  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
HTML = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
SOURCES = {
    "app.js": (STATIC / "app.js").read_text(encoding="utf-8"),
    "recipe_view.js": (STATIC / "recipe_view.js").read_text(encoding="utf-8"),
    "recipe_nav.js": (STATIC / "recipe_nav.js").read_text(encoding="utf-8"),
    "cook_timers.js": (STATIC / "cook_timers.js").read_text(encoding="utf-8"),
    "categories.js": (STATIC / "categories.js").read_text(encoding="utf-8"),
    "onboarding.js": (STATIC / "onboarding.js").read_text(encoding="utf-8"),
    "users.js": (STATIC / "users.js").read_text(encoding="utf-8"),
    "ai.js": (STATIC / "ai.js").read_text(encoding="utf-8"),
    "orders.js": (STATIC / "orders.js").read_text(encoding="utf-8"),
    "purchase_views.js": (STATIC / "purchase_views.js").read_text(encoding="utf-8"),
    "order_views.js": (STATIC / "order_views.js").read_text(encoding="utf-8"),
    "popover.js": (STATIC / "popover.js").read_text(encoding="utf-8"),
    "linking.js": (STATIC / "linking.js").read_text(encoding="utf-8"),
    "picnic.js": (STATIC / "picnic.js").read_text(encoding="utf-8"),
    "recipe_search.js": (STATIC / "recipe_search.js").read_text(encoding="utf-8"),
    "jumbo.js": (STATIC / "jumbo.js").read_text(encoding="utf-8"),
    "recipe_bulk.js": (STATIC / "recipe_bulk.js").read_text(encoding="utf-8"),
    "crisp.js": (STATIC / "crisp.js").read_text(encoding="utf-8"),
    "product_links.js": (STATIC / "product_links.js").read_text(encoding="utf-8"),
    "mealkits.js": (STATIC / "mealkits.js").read_text(encoding="utf-8"),
    "ah_planner.js": (STATIC / "ah_planner.js").read_text(encoding="utf-8"),
    "basket.js": (STATIC / "basket.js").read_text(encoding="utf-8"),
    "updates.js": (STATIC / "updates.js").read_text(encoding="utf-8"),
    "system.js": (STATIC / "system.js").read_text(encoding="utf-8"),
    "upload.js": (STATIC / "upload.js").read_text(encoding="utf-8"),
    "backup.js": (STATIC / "backup.js").read_text(encoding="utf-8"),
    "price_archive.js": (STATIC / "price_archive.js").read_text(encoding="utf-8"),
    "audit.js": (STATIC / "audit.js").read_text(encoding="utf-8"),
    "offers.js": (STATIC / "offers.js").read_text(encoding="utf-8"),
    "offers_cart.js": (STATIC / "offers_cart.js").read_text(encoding="utf-8"),
    "facets.js": (STATIC / "facets.js").read_text(encoding="utf-8"),
    "offers_facets.js": (STATIC / "offers_facets.js").read_text(encoding="utf-8"),
    "ai_review.js": (STATIC / "ai_review.js").read_text(encoding="utf-8"),
    "offers_history.js": (STATIC / "offers_history.js").read_text(encoding="utf-8"),
    "profile.js": (STATIC / "profile.js").read_text(encoding="utf-8"),
    "theme.js": (STATIC / "theme.js").read_text(encoding="utf-8"),
    "appearance.js": (STATIC / "appearance.js").read_text(encoding="utf-8"),
    "shopping.js": (STATIC / "shopping.js").read_text(encoding="utf-8"),
    "loose_names.js": (STATIC / "loose_names.js").read_text(encoding="utf-8"),
    "shared_links_export.js": (STATIC / "shared_links_export.js").read_text(encoding="utf-8"),
}
JS = "\n".join(SOURCES.values())
ENTRY = re.compile(r'^  "([^"]+)": "((?:[^"\\]|\\.)*)",$', re.M)


def load(lang: str) -> dict[str, str]:
    source = (STATIC / "i18n" / f"{lang}.js").read_text(encoding="utf-8")
    return {json.loads(f'"{k}"'): json.loads(f'"{v}"') for k, v in ENTRY.findall(source)}


NL, EN = load("nl"), load("en")


def placeholders(text: str) -> set[str]:
    return set(re.findall(r"\{(\w+)\}", text))


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


class DictionaryTest(AppTestCase):
    def test_every_line_of_both_files_is_read(self):
        for lang in ("nl", "en"):
            source = (STATIC / "i18n" / f"{lang}.js").read_text(encoding="utf-8")
            body = [line for line in source.splitlines() if line.startswith('  "')]
            self.assertEqual(len(body), len(load(lang)), f"{lang}.js: een regel volgt het formaat niet")

    def test_both_languages_have_the_same_keys(self):
        self.assertEqual(sorted(set(NL) - set(EN)), [], "ontbreekt in en.js")
        self.assertEqual(sorted(set(EN) - set(NL)), [], "ontbreekt in nl.js")

    def test_placeholders_match(self):
        for key in NL:
            self.assertEqual(placeholders(NL[key]), placeholders(EN[key]), key)

    def test_no_empty_texts_and_english_is_translated(self):
        for key, text in EN.items():
            self.assertTrue(text.strip(), key)
        identical = {k for k in NL if NL[k] == EN[k]}
        # Merknamen en vaste woorden mogen gelijk zijn; een lange zin die gelijk is, is vergeten te vertalen.
        for key in identical:
            self.assertLess(len(NL[key]), 40, f"{key} lijkt niet vertaald: {NL[key]}")

    def test_plural_forms_come_in_pairs(self):
        for lang, dictionary in (("nl", NL), ("en", EN)):
            for key in dictionary:
                if key.endswith(".one"):
                    self.assertIn(key[:-4] + ".other", dictionary, f"{lang}: {key}")
                if key.endswith(".other"):
                    self.assertIn(key[:-6] + ".one", dictionary, f"{lang}: {key}")

    def test_markup_in_texts_is_balanced_and_limited(self):
        allowed = {"strong", "em", "code"}
        for key, text in {**{f"nl:{k}": v for k, v in NL.items()}, **{f"en:{k}": v for k, v in EN.items()}}.items():
            tags = re.findall(r"</?(\w+)", text)
            self.assertTrue(set(tags) <= allowed, f"{key}: {tags}")
            for tag in allowed:
                self.assertEqual(text.count(f"<{tag}>"), text.count(f"</{tag}>"), key)
            self.assertNotRegex(text, r"<script|javascript:|on\w+=", key)


class UsageTest(AppTestCase):
    CALL = re.compile(r'\b(?:t|th|tn|thn)\(\s*["`]([\w.]+)["`]')

    def test_every_key_used_in_the_scripts_exists(self):
        used = set(self.CALL.findall(JS))
        self.assertTrue(used, "geen t()-aanroepen gevonden")
        for key in used:
            self.assertTrue(key in NL or key + ".one" in NL, f"{key} staat niet in het woordenboek")

    def test_dynamic_key_families_are_complete(self):
        for step in ("auth", "mealie", "ah", "jumbo", "picnic", "done"):
            self.assertIn(f"ob.step.{step}", NL)
            self.assertIn(f"ob.title.{step}", NL)
        for meal in ("breakfast", "lunch", "dinner", "side"):
            self.assertIn(f"meal.{meal}", NL)
        for key in ("sec.mfa.regen_action", "sec.mfa.disable_action", "common.cancel_now", "common.cancel"):
            self.assertIn(key, NL)

    def test_every_html_key_exists_and_the_page_text_is_the_dutch_text(self):
        pairs = re.findall(r"<(\w+)\b([^>]*\bdata-i18n(?:-html)?=\"([\w.]+)\"[^>]*)>(.*?)</\1>", HTML, re.S)
        self.assertGreater(len(pairs), 60)
        for tag, attrs, key, inner in pairs:
            self.assertIn(key, NL, key)
            if "data-i18n-html=" in attrs:
                self.assertEqual(norm(inner), norm(NL[key]), f"{key}: de HTML wijkt af van nl.js")
            elif "<" not in inner:
                self.assertEqual(norm(inner), norm(NL[key]).replace("&gt;", ">"), f"{key}: de HTML wijkt af van nl.js")

    def test_attribute_keys_exist_and_match(self):
        found = 0
        for tag_attrs in re.findall(r"<\w+\b([^>]*data-i18n-attr=\"[^\"]+\"[^>]*)>", HTML):
            spec = re.search(r'data-i18n-attr="([^"]+)"', tag_attrs).group(1)
            for pair in spec.split(";"):
                attribute, key = pair.split(":")
                self.assertIn(key, NL, key)
                value = re.search(rf'\b{attribute}="([^"]*)"', tag_attrs)
                self.assertIsNotNone(value, f"{attribute} ontbreekt bij {key}")
                self.assertEqual(value.group(1), NL[key], key)
                found += 1
        self.assertGreater(found, 8)

    def test_no_key_is_left_unused(self):
        html_keys = set(re.findall(r'data-i18n(?:-html)?="([\w.]+)"', HTML))
        for spec in re.findall(r'data-i18n-attr="([^"]+)"', HTML):
            html_keys |= {pair.split(":")[1] for pair in spec.split(";")}
        prefixes = {
            "ob.step.",
            "ob.title.",
            "meal.",
            "about.group.",
            "about.desc.",
            "offers.source.",
            "offers.cat.",
            "offers.course.",
            "offers.regular.source.",
            "backup.count.",
            "backup.link.",
            "backup.part.",
            "backup.restore_part.",
        }
        for key in NL:
            base = re.sub(r"\.(one|other)$", "", key)
            used = (
                key in html_keys
                or re.search(r"(?<![\w.])" + re.escape(base) + r"(?![\w.])", JS)
                or any(key.startswith(prefix) for prefix in prefixes)
            )
            self.assertTrue(used, f"{key} wordt nergens gebruikt")

    def test_no_dutch_literals_are_left_in_the_scripts(self):
        # Zichtbare tekst hoort in het woordenboek; zo'n zin in de code is een vergeten vertaling.
        pattern = re.compile(
            r'(?:message\([^,]+,\s*|textContent\s*=\s*|<p[^>]*>|<h3[^>]*>|<button[^>]*>|<span[^>]*>)\s*["`]?([A-Z][a-z]+ [a-z]+ [a-z]+)'
        )
        offenders = []
        for name, source in SOURCES.items():
            for line_number, line in enumerate(source.splitlines(), 1):
                if line.strip().startswith("//") or "t(" in line or "th(" in line or "tn(" in line:
                    continue
                if pattern.search(line):
                    offenders.append(f"{name}:{line_number}: {line.strip()[:80]}")
        self.assertEqual(offenders, [])


class PageTest(AppTestCase):
    def test_loose_names_texts_exist_in_both_languages_with_the_same_placeholders(self):
        keys = [key for key in NL if key.startswith("loose.") or key.startswith("aireview.loose_")]
        self.assertGreater(len(keys), 30)
        for key in keys:
            self.assertIn(key, EN, key)
            self.assertEqual(placeholders(NL[key]), placeholders(EN[key]), key)
        self.assertIn("'s nachts", NL["aireview.loose_nightly"])

    def test_scripts_load_in_the_right_order(self):
        order = [HTML.index(name) for name in ("i18n.js", "i18n/nl.js", "i18n/en.js", "app.js", "onboarding.js")]
        self.assertEqual(order, sorted(order))

    def test_language_switch_on_login_invitation_and_onboarding_not_in_the_header(self):
        # Sinds 4.7.0 staan taal en thema in de app in het menu onder je naam en bij Voorkeuren, niet meer in de kop. De taalkiezer
        # is tekst (NL, EN), geen vlag: een vlag staat voor een land, niet voor een taal.
        self.assertEqual(HTML.count('class="lang-switch"'), 3)
        self.assertEqual(HTML.count('data-lang="nl"'), 3)
        self.assertEqual(HTML.count('data-lang="en"'), 3)
        for gate in ('id="auth-gate"', 'id="invite-gate"', 'id="onboarding"'):
            start = HTML.index(gate)
            self.assertLess(HTML.index('class="lang-switch"', start), HTML.index("</section>", start), gate)
        header = HTML[HTML.index('<header class="topbar">') : HTML.index("</header>")]
        self.assertNotIn("lang-switch", header)
        self.assertNotIn("data-theme-toggle", header)
        self.assertNotIn("brand.tagline", header, "de slogan staat alleen nog op de inlogkaart")
        self.assertNotIn('id="flag-nl"', HTML)
        self.assertNotIn('id="flag-gb"', HTML)
        self.assertEqual(re.findall(r'<span class="lang-switch__code" aria-hidden="true">(\w+)</span>', HTML), ["NL", "EN"] * 3)
        for button in re.findall(r"<button[^>]*data-lang=[^>]*>", HTML):
            self.assertIn("aria-label=", button)
            self.assertIn("aria-pressed=", button)
            self.assertIn("lang=", button)

    def test_theme_toggle_sits_next_to_every_language_switch(self):
        self.assertEqual(HTML.count('class="switches"'), 3)
        self.assertEqual(HTML.count("data-theme-toggle"), 3)
        for button in re.findall(r"<button[^>]*data-theme-toggle[^>]*>", HTML):
            self.assertIn("aria-label=", button)
            self.assertIn("aria-pressed=", button)
            self.assertIn("theme.dark", button)
        # theme.light is het label van het menu-item als donker aan staat.
        self.assertEqual(sorted({k for k in NL if k.startswith("theme.")}), ["theme.dark", "theme.light"])
        self.assertIn("theme.dark", EN)
        self.assertIn("theme.light", EN)

    def test_theme_is_set_in_the_head_before_the_styles_and_the_script_loads_after_the_dictionaries(self):
        self.assertLess(HTML.index('setAttribute("data-theme"'), HTML.index("styles.css"))
        self.assertLess(HTML.index("i18n/en.js"), HTML.index("filename='theme.js'"))
        self.assertIn('setAttribute("data-theme"', (ROOT / "templates" / "oauth.html").read_text(encoding="utf-8"))
        engine = (STATIC / "theme.js").read_text(encoding="utf-8")
        for needle in ('"ms-theme"', "prefers-color-scheme: dark", 'meta[name="theme-color"]', "aria-pressed"):
            self.assertIn(needle, engine)

    def test_dark_theme_overrides_every_token_that_is_a_colour(self):
        css = (STATIC / "styles.css").read_text(encoding="utf-8")
        light = re.search(r":root\s*{(.*?)\n}", css, re.S).group(1)
        dark = re.search(r':root\[data-theme="dark"\]\s*{(.*?)\n}', css, re.S).group(1)
        names = lambda block: set(re.findall(r"(--[\w-]+):", block))
        unchanged = {"--lemon", "--control-height", "--font-body", "--font-heading", "--ease"}
        unchanged |= {name for name in names(light) if name.startswith(("--radius-", "--text-", "--scroll-"))}  # maten, geen kleuren
        self.assertEqual(sorted(names(light) - names(dark) - unchanged), [])

    def test_engine_keeps_the_language_and_tells_the_server(self):
        engine = (STATIC / "i18n.js").read_text(encoding="utf-8")
        for needle in ('"ms-lang"', "ms_lang", "SameSite=Lax", "document.documentElement.lang", "ms:languagechange", "navigator.languages", "try {"):
            self.assertIn(needle, engine, needle)

    def test_app_redraws_data_driven_parts_on_a_language_change(self):
        handler = SOURCES["app.js"][SOURCES["app.js"].index('addEventListener("ms:languagechange"') :]
        for call in (
            "updateWeekLabel()",
            "renderMealPlan(",
            "renderAhLists()",
            "renderSavedRecipes()",
            "renderAuthState()",
            "refreshSavedRecipesStatus()",
        ):
            self.assertIn(call, handler, call)
        # Recepten zoeken tekent zijn eigen resultaten opnieuw.
        search = SOURCES["recipe_search.js"]
        self.assertIn("SEARCH_STORES.forEach(renderSearchStore)", search[search.index('addEventListener("ms:languagechange"') :])

    def test_the_language_handler_only_calls_functions_that_exist(self):
        # Een aanroep van een functie die niet (meer) bestaat, breekt de rest van de handler af: zo bleven na een taalwissel
        # de inlogstatus, de lijstjes en de bewaarde recepten in de oude taal staan (renderSearchResults, weg sinds 2.8.0).
        source = SOURCES["app.js"]
        start = source.index('window.addEventListener("ms:languagechange", () => {')
        handler = source[start : source.index("\n});", start)]
        defined = set(re.findall(r"^(?:async\s+)?function\s+(\w+)\s*\(", JS, re.M))
        called = set(re.findall(r"^\s*(?:if \(.*?\) )?(\w+)\(", handler, re.M)) - {"if", "window"}
        self.assertTrue(called)
        self.assertEqual(sorted(called - defined), [])

    def test_changing_language_mid_mfa_setup_does_not_restart_it(self):
        handler = SOURCES["onboarding.js"][SOURCES["onboarding.js"].index('addEventListener("ms:languagechange"') :]
        self.assertIn("if (ob.mfaBusy)", handler)
        self.assertLess(handler.index("if (ob.mfaBusy)"), handler.index("renderOnboarding()"))

    def test_dates_and_prices_follow_the_language(self):
        self.assertNotIn('"nl-NL"', JS)
        self.assertIn("I18N.locale()", JS)


class ServerLanguageTest(AppTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(
            os.environ,
            {"PASSKEY_AUTH_ENABLED": "true", "MEALIESHOPPER_DATA_DIR": tmp.name, "MEALIESHOPPER_AUTH_SECRET": "x"},
        )
        env.start()
        self.addCleanup(env.stop)
        cost = mock.patch.object(auth_password, "SCRYPT_N", 2**4)
        cost.start()
        self.addCleanup(cost.stop)
        self.app = create_app()

    def client(self, lang=None):
        client = self.client_for(self.app)
        if lang:
            client.set_cookie("ms_lang", lang)
        return client

    def test_default_and_unknown_languages_are_dutch(self):
        self.assertEqual(i18n.tr("nl", "en"), "nl", "zonder verzoek")

        async def lang(**kwargs):
            async with self.app.test_request_context("/", **kwargs):
                return i18n.current_lang()

        self.assertEqual(run(lang()), "nl")
        self.assertEqual(run(lang(headers={"Cookie": "ms_lang=fr"})), "nl")
        self.assertEqual(run(lang(headers={"Cookie": "ms_lang=en"})), "en")

    async def test_login_errors_follow_the_cookie(self):
        await self.client().post("/api/auth/password", json={"username": "admin", "password": "koffie-met-melk-42"})
        wrong = {"username": "admin", "password": "verkeerd-wachtwoord"}
        dutch = (await self.client("nl").post("/api/auth/password/login", json=wrong)).get_json()["error"]
        english = (await self.client("en").post("/api/auth/password/login", json=wrong)).get_json()["error"]
        self.assertEqual(dutch, "Gebruikersnaam of wachtwoord klopt niet.")
        self.assertEqual(english, "Username or password is incorrect.")

    async def test_password_rules_and_lockout_in_english(self):
        weak = (await self.client("en").post("/api/auth/password", json={"username": "admin", "password": "short"})).get_json()["error"]
        self.assertEqual(weak, "Choose a password of at least 10 characters.")
        await self.client().post("/api/auth/password", json={"username": "admin", "password": "koffie-met-melk-42"})
        guest = self.client("en")
        for _ in range(auth_password.ATTEMPTS_PER_WINDOW):
            await guest.post("/api/auth/password/login", json={"username": "admin", "password": "nope-nope-nope"})
        locked = await guest.post("/api/auth/password/login", json={"username": "admin", "password": "nope-nope-nope"})
        self.assertEqual(locked.status_code, 429)
        self.assertRegex(locked.get_json()["error"], r"^Too many failed attempts\. Try again in \d+ minutes?\.$")

    async def test_onboarding_explains_the_missing_passkey_in_the_chosen_language(self):
        url = "/api/onboarding/status"

        async def fetch(lang):
            client = self.client_for(self.app)
            client.set_cookie("ms_lang", lang, domain="192.168.1.20")
            return (await client.get(url, base_url="http://192.168.1.20:8080")).get_json()

        english, dutch = await fetch("en"), await fetch("nl")
        self.assertIn("an IP address", english["passkeyUnavailableReason"])
        self.assertIn("A passkey needs a real domain name", english["passkeyUnavailableReason"])
        self.assertIn("een IP-adres", dutch["passkeyUnavailableReason"])

    async def test_open_mode_messages(self):
        res = await self.client("en").post("/api/auth/open", json={})
        self.assertEqual(res.get_json()["error"], "Confirm that you want to continue without security.")


if __name__ == "__main__":
    unittest.main()
