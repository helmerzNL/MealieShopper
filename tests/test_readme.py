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
IMAGE = re.compile(r"(?:src|href)=\"([^\"]+\.(?:png|svg))\"|!\[[^\]]*\]\(([^)]+)\)")
LOCAL_LINK = re.compile(r"\]\(((?!https?:|#|mailto:)[^)]+)\)")


def text(lang):
    return READMES[lang].read_text(encoding="utf-8")


def headings(lang):
    return [line for line in text(lang).splitlines() if line.startswith("## ")]


class ReadmeFilesTest(unittest.TestCase):
    def test_both_readmes_exist_and_link_to_each_other(self):
        self.assertIn("README.en.md", text("nl"))
        self.assertIn("README.md", text("en"))

    def test_every_local_image_and_link_exists(self):
        for lang in READMES:
            for match in IMAGE.finditer(text(lang)):
                path = match.group(1) or match.group(2)
                if path.startswith("http"):
                    continue
                self.assertTrue((ROOT / path).is_file(), f"{lang}: {path}")
            for path in LOCAL_LINK.findall(text(lang)):
                self.assertTrue((ROOT / path.split("#")[0]).exists(), f"{lang}: {path}")

    def test_each_language_uses_its_own_screenshots(self):
        for lang, other in (("nl", "en"), ("en", "nl")):
            self.assertIn(f"docs/screenshots/{lang}/", text(lang))
            self.assertNotIn(f"docs/screenshots/{other}/", text(lang))

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
        self.assertEqual(len(headings("nl")), len(headings("en")))
        self.assertEqual(len(IMAGE.findall(text("nl"))), len(IMAGE.findall(text("en"))))

    def test_coffee_message_closes_the_document(self):
        for lang in READMES:
            tail = text(lang).strip().splitlines()[-1]
            self.assertIn("buymeacoffee.com/flux76", tail, lang)
            self.assertIn("Integrat", headings(lang)[-2], lang)

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
            self.assertNotIn("AH koppelen", text(lang))
            self.assertNotRegex(text(lang), r"(?i)MEALIE_URL.{0,40}(derive|afgeleid)")


class ResetLoginTest(unittest.TestCase):
    """De noodgreep uit de README zet de inlog terug zonder de koppelingen te wissen."""

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

                for lang in READMES:
                    command = re.search(r"python -c \"(import sqlite3;.*?)\"\n", text(lang), re.S)
                    self.assertIsNotNone(command, lang)
                    code = command.group(1).replace('\\"', '"').replace("/data/auth.sqlite3", str(Path(tmp) / "auth.sqlite3"))
                    with closing(sqlite3.connect(Path(tmp) / "auth.sqlite3")) as conn, conn:
                        conn.execute("INSERT OR IGNORE INTO passkey_credentials (id, user_id, public_key, credential_name, created_at) VALUES ('c1', 'owner', x'00', 'Telefoon', 'x')")
                    for key, value in (("ONBOARDING_DONE", "1"), ("AUTH_MODE", "passkey")):
                        auth.set_setting(key, value)
                    exec(compile(code, "readme", "exec"), {})

                    status = client.get("/api/onboarding/status").get_json()
                    self.assertTrue(status["needed"], lang)
                    self.assertFalse(status["authDone"], lang)
                    with closing(auth.connect()) as conn:
                        kept = conn.execute("SELECT value FROM app_settings WHERE key = 'KEEP_ME'").fetchone()
                    self.assertEqual(kept[0], "bewaard", lang)


if __name__ == "__main__":
    unittest.main()
