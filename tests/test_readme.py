import os
import re
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

from mealieshopper import ah, auth, auth_password, mealie  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
READMES = {"nl": ROOT / "README.md", "en": ROOT / "README.en.md"}
# Wat in de README geen plek heeft (shared/templates/readme van Flux76HQ/App-Guidance), staat in de handleiding.
GUIDES = {"nl": ROOT / "docs" / "handleiding.md", "en": ROOT / "docs" / "handleiding.en.md"}
DOCS = {**{("readme", lang): path for lang, path in READMES.items()}, **{("guide", lang): path for lang, path in GUIDES.items()}}
# De kopjes van de README-template, in deze volgorde; een app voegt er geen tussen en hernoemt ze niet.
TEMPLATE_HEADINGS = {
    "nl": ["Inhoud", "Waarom MealieShopper?", "Wat kan het?", "Zo ziet het eruit", "Snel starten", "Configuratie", "Beheer",
           "Data en back-ups", "Architectuur", "Stabiel en beta", "Meewerken", "Privacy en beveiliging", "Steun MealieShopper", "Licentie"],
    "en": ["Contents", "Why MealieShopper?", "Features", "Screenshots", "Getting started", "Configuration", "Running it",
           "Data and backups", "Architecture", "Stable and beta", "Contributing", "Privacy and security", "Support MealieShopper", "License"],
}
IMAGE = re.compile(r"(?:src|srcset|href)=\"([^\"]+\.(?:png|svg))\"|!\[[^\]]*\]\(([^)]+)\)")
LOCAL_LINK = re.compile(r"\]\(((?!https?:|#|mailto:)[^)]+)\)")
ANCHOR_LINK = re.compile(r"\]\(((?!https?:|mailto:)[^)\s]*#[^)\s]+)\)")


def text(lang):
    return READMES[lang].read_text(encoding="utf-8")


def guide(lang):
    return GUIDES[lang].read_text(encoding="utf-8")


def headings(lang, files=READMES):
    return [line for line in files[lang].read_text(encoding="utf-8").splitlines() if line.startswith("## ")]


def anchors(path):
    """De ankers die GitHub voor de kopjes maakt: kleine letters, leestekens weg, spaties als streepje, -1 bij een dubbel."""
    seen, found, fenced = {}, set(), False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        heading = None if fenced else re.match(r"#{1,6} (.+?)\s*$", line)
        if heading:
            base = re.sub(r"[^\w\- ]", "", heading.group(1).lower()).replace(" ", "-")
            count = seen.get(base, 0)
            found.add(base if count == 0 else f"{base}-{count}")
            seen[base] = count + 1
    return found


class ReadmeFilesTest(unittest.TestCase):
    def test_both_readmes_exist_and_link_to_each_other(self):
        self.assertIn("README.en.md", text("nl"))
        self.assertIn("README.md", text("en"))
        self.assertIn("(handleiding.en.md)", guide("nl"))
        self.assertIn("(handleiding.md)", guide("en"))

    def test_every_local_image_and_link_exists(self):
        for (kind, lang), file in DOCS.items():
            content = file.read_text(encoding="utf-8")
            for match in IMAGE.finditer(content):
                path = match.group(1) or match.group(2)
                if path.startswith("http"):
                    continue
                self.assertTrue((file.parent / path).is_file(), f"{kind} {lang}: {path}")
            for path in LOCAL_LINK.findall(content):
                self.assertTrue((file.parent / path.split("#")[0]).exists(), f"{kind} {lang}: {path}")

    def test_every_anchor_exists(self):
        for (kind, lang), file in DOCS.items():
            for link in ANCHOR_LINK.findall(file.read_text(encoding="utf-8")):
                path, anchor = link.split("#", 1)
                target = (file.parent / path).resolve() if path else file
                self.assertIn(anchor, anchors(target), f"{kind} {lang}: {link}")

    def test_no_template_placeholder_is_left(self):
        # Een <PLACEHOLDER> toont op GitHub als niets; zo controleert de README-template het ook.
        for (kind, lang), file in DOCS.items():
            self.assertIsNone(re.search(r"<[A-Z]", file.read_text(encoding="utf-8")), f"{kind} {lang}")

    def test_each_language_uses_its_own_screenshots(self):
        for lang, other in (("nl", "en"), ("en", "nl")):
            self.assertIn(f"docs/screenshots/{lang}/", text(lang))
            self.assertNotIn(f"docs/screenshots/{other}/", text(lang))
            self.assertIn(f'src="screenshots/{lang}/', guide(lang))
            self.assertNotIn(f"screenshots/{other}/", guide(lang))

    def test_screenshots_are_present_in_both_languages_and_small(self):
        for lang in ("nl", "en"):
            files = sorted((ROOT / "docs" / "screenshots" / lang).glob("*.png"))
            self.assertGreaterEqual(len(files), 12)
            for file in files:
                self.assertLess(file.stat().st_size, 300_000, file.name)
        self.assertEqual(
            sorted(p.name for p in (ROOT / "docs/screenshots/nl").glob("*.png")),
            sorted(p.name for p in (ROOT / "docs/screenshots/en").glob("*.png")),
        )

    def test_same_structure_in_both_languages(self):
        for files in (READMES, GUIDES):
            self.assertEqual(len(headings("nl", files)), len(headings("en", files)))
            nl, en = (files[lang].read_text(encoding="utf-8") for lang in ("nl", "en"))
            self.assertEqual(len(IMAGE.findall(nl)), len(IMAGE.findall(en)))
            self.assertEqual(len(re.findall(r"^#{3,} ", nl, re.M)), len(re.findall(r"^#{3,} ", en, re.M)))

    def test_sections_follow_the_readme_template(self):
        for lang in READMES:
            self.assertEqual(headings(lang), [f"## {name}" for name in TEMPLATE_HEADINGS[lang]], lang)

    def test_support_section_funding_and_footer(self):
        funding = (ROOT / ".github" / "FUNDING.yml").read_text(encoding="utf-8")
        self.assertIn("buy_me_a_coffee: flux76", funding)
        self.assertIn("ko_fi: flux76", funding)
        for lang in READMES:
            support = text(lang).split(headings(lang)[-2], 1)[1].split(headings(lang)[-1], 1)[0]
            self.assertIn('href="https://buymeacoffee.com/flux76"', support, lang)
            self.assertIn('href="https://ko-fi.com/flux76"', support, lang)
            header = text(lang).split("\n---\n", 1)[0]
            self.assertIn("](https://buymeacoffee.com/flux76)", header, lang)
            self.assertIn("](https://ko-fi.com/flux76)", header, lang)
            self.assertIn("flux76.com", text(lang).strip().splitlines()[-2], lang)

    def test_licence(self):
        # Een LICENSE-bestand, met de badge in de kop en de licentie onder het laatste kopje (README-template).
        licence = (ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertTrue(licence.startswith("MIT License\n\nCopyright (c) "))
        for lang in READMES:
            self.assertIn("](LICENSE)\n", text(lang).split("\n---\n", 1)[0], lang)
            self.assertIn("[MIT](LICENSE)", text(lang).split(headings(lang)[-1], 1)[1], lang)

    def test_security_policy(self):
        # SECURITY.md volgt shared/templates/github/SECURITY.md: eerst Nederlands, dan Engels, zonder open plekken.
        file = ROOT / "SECURITY.md"
        content = file.read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"<[A-Z]", content))
        self.assertNotIn("FILL IN", content)
        self.assertNotIn("<!--", content)
        self.assertLess(content.index("## Nederlands"), content.index("## English"))
        nl, en = content.split("## English", 1)
        self.assertEqual(len(re.findall(r"^### ", nl, re.M)), len(re.findall(r"^### ", en, re.M)))
        self.assertEqual(content.count("/security/advisories/new"), 2)
        for path in LOCAL_LINK.findall(content):
            self.assertTrue((ROOT / path.split("#")[0]).exists(), path)
        for link in ANCHOR_LINK.findall(content):
            path, anchor = link.split("#", 1)
            self.assertIn(anchor, anchors(ROOT / path if path else file), link)
        for lang in READMES:
            self.assertIn("](SECURITY.md)", text(lang), lang)

    def test_environment_variables_used_by_the_code_are_documented(self):
        used = set()
        for file in (ROOT / "mealieshopper").glob("*.py"):
            used |= set(re.findall(r"environ(?:\.get)?[\[(]\s*\"([A-Z][A-Z0-9_]+)\"", file.read_text(encoding="utf-8")))
        used -= {"MEALIESHOPPER_ASSET_VERSION", "MEALIESHOPPER_DATA_DIR_UNUSED"}
        self.assertIn("MEALIE_URL", used)
        for lang in READMES:
            for name in sorted(used):
                self.assertIn(name, text(lang), f"{lang}: {name}")

    def test_no_stale_instructions(self):
        for lang in READMES:
            for content in (text(lang), guide(lang)):
                self.assertNotIn("AH koppelen", content)
                self.assertNotRegex(content, r"(?i)MEALIE_URL.{0,40}(derive|afgeleid)")


class ResetLoginTest(unittest.TestCase):
    """De noodgreep uit de handleiding (Problemen oplossen) zet de inlog terug zonder de koppelingen te wissen."""

    def test_documented_reset_brings_the_wizard_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"PASSKEY_AUTH_ENABLED": "true", "MEALIESHOPPER_DATA_DIR": tmp, "MEALIESHOPPER_AUTH_SECRET": "test-secret"}
            with mock.patch.dict(os.environ, env), \
                    mock.patch.object(auth_password, "SCRYPT_N", 2**4), \
                    mock.patch.object(mealie, "mealie_url", return_value=""), \
                    mock.patch.object(mealie, "mealie_token", return_value=""), \
                    mock.patch.object(ah, "configured_refresh_token", return_value=""):
                app = create_app()
                client = app.test_client()
                auth.ensure_db()
                with closing(auth.connect()) as conn, conn:
                    conn.execute("INSERT OR IGNORE INTO users (id, username, created_at) VALUES ('owner', 'admin', 'x')")
                    conn.execute("INSERT INTO passkey_credentials (id, user_id, public_key, credential_name, created_at) VALUES ('c1', 'owner', x'00', 'Telefoon', 'x')")
                for key, value in (("ONBOARDING_DONE", "1"), ("AUTH_MODE", "passkey"), ("KEEP_ME", "bewaard")):
                    auth.set_setting(key, value)
                self.assertFalse(client.get("/api/onboarding/status").get_json()["needed"])

                for lang in GUIDES:
                    command = re.search(r"python -c \"(import sqlite3;.*?)\"\n", guide(lang), re.S)
                    self.assertIsNotNone(command, lang)
                    code = command.group(1).replace('\\"', '"').replace("/data/auth.sqlite3", str(Path(tmp) / "auth.sqlite3"))
                    with closing(sqlite3.connect(Path(tmp) / "auth.sqlite3")) as conn, conn:
                        conn.execute("INSERT OR IGNORE INTO passkey_credentials (id, user_id, public_key, credential_name, created_at) VALUES ('c1', 'owner', x'00', 'Telefoon', 'x')")
                    for key, value in (("ONBOARDING_DONE", "1"), ("AUTH_MODE", "passkey")):
                        auth.set_setting(key, value)
                    with closing(sqlite3.connect(Path(tmp) / "auth.sqlite3")) as conn, conn:
                        conn.execute("INSERT OR IGNORE INTO users (id, username, created_at) VALUES ('u_anna', 'anna', 'x')")
                        conn.execute("INSERT OR IGNORE INTO invites (id, token_hash, user_id, username, kind, created_by, created_at, expires_at) VALUES ('i1', 'h', 'u_x', 'bob', 'new', 'owner', 'x', '2999')")
                    with closing(sqlite3.connect(Path(tmp) / "auth.sqlite3")) as conn:
                        before = conn.execute("SELECT session_epoch FROM users WHERE id = 'owner'").fetchone()[0]
                    exec(compile(code, "readme", "exec"), {})
                    with closing(sqlite3.connect(Path(tmp) / "auth.sqlite3")) as conn:
                        after = conn.execute("SELECT session_epoch FROM users WHERE id = 'owner'").fetchone()[0]
                    self.assertEqual(after, before + 1, lang)  # alle sessies en AI-apps zijn ongeldig
                    with closing(sqlite3.connect(Path(tmp) / "auth.sqlite3")) as conn:
                        self.assertEqual([row[0] for row in conn.execute("SELECT id FROM users")], ["owner"], lang)
                        self.assertEqual(conn.execute("SELECT COUNT(*) FROM invites").fetchone()[0], 0, lang)

                    status = client.get("/api/onboarding/status").get_json()
                    self.assertTrue(status["needed"], lang)
                    self.assertFalse(status["authDone"], lang)
                    with closing(auth.connect()) as conn:
                        kept = conn.execute("SELECT value FROM app_settings WHERE key = 'KEEP_ME'").fetchone()
                    self.assertEqual(kept[0], "bewaard", lang)


if __name__ == "__main__":
    unittest.main()
