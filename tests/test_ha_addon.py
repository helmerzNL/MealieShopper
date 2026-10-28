import importlib.util
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import ha_addon  # noqa: E402

spec = importlib.util.spec_from_file_location("docker_entrypoint", ROOT / "docker-entrypoint.py")
entrypoint = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entrypoint)

VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()


def top_level(text, key):
    match = re.search(rf"^{key}: *\"?([^\"\n]*)\"?\s*$", text, re.M)
    return match.group(1) if match else None


def block(text, key):
    """De regels `naam: waarde` onder `key:` (twee spaties ingesprongen) als dict."""
    match = re.search(rf"^{key}:\s*\n((?:  .*\n?)+)", text, re.M)
    pairs = re.findall(r"^  ([^\s:]+): *(.*)$", match.group(1), re.M) if match else []
    return dict(pairs)


class AddonFilesTest(unittest.TestCase):
    def config(self, folder):
        return (folder / "config.yaml").read_text(encoding="utf-8")

    def test_repository_file_names_the_project(self):
        text = (ROOT / "repository.yaml").read_text(encoding="utf-8")
        for key in ("name", "url", "maintainer"):
            self.assertTrue(top_level(text, key), key)
        self.assertEqual(top_level(text, "url"), "https://github.com/helmerznl/mealieshopper")

    def test_stable_addon_follows_the_version_file_and_the_stable_image_tag(self):
        text = self.config(ha_addon.STABLE)
        self.assertEqual(top_level(text, "version"), VERSION)
        self.assertEqual(top_level(text, "slug"), "mealieshopper")
        self.assertEqual(top_level(text, "image"), "ghcr.io/helmerznl/mealieshopper")

    def test_beta_addon_follows_the_beta_image_tag(self):
        text = self.config(ha_addon.BETA)
        self.assertEqual(top_level(text, "version"), f"{VERSION}-beta")
        self.assertEqual(top_level(text, "slug"), "mealieshopper_beta")
        self.assertEqual(top_level(text, "image"), "ghcr.io/helmerznl/mealieshopper")
        self.assertNotEqual(block(text, "ports"), block(self.config(ha_addon.STABLE), "ports"))

    def test_beta_addon_is_derived_from_the_stable_one(self):
        self.assertEqual(ha_addon.differences(VERSION), [], "draai: python scripts/ha_addon.py")

    def test_the_app_is_reachable_and_watched(self):
        text = self.config(ha_addon.STABLE)
        self.assertEqual(block(text, "ports"), {"8000/tcp": "5959"})
        self.assertIn("[PORT:8000]", top_level(text, "webui"))
        self.assertTrue(top_level(text, "watchdog").endswith("/healthz"))
        self.assertIn("amd64", text)
        self.assertIn("aarch64", text)

    def test_every_option_has_a_translation_in_both_languages(self):
        options = set(block(self.config(ha_addon.STABLE), "schema"))
        self.assertTrue(options)
        for lang in ("nl", "en"):
            translation = (ha_addon.STABLE / "translations" / f"{lang}.yaml").read_text(encoding="utf-8")
            named = set(re.findall(r"^  ([a-z_]+):\s*$", translation, re.M))
            self.assertEqual(options, named, lang)
            self.assertEqual(translation.count("    name: "), len(options), lang)
            self.assertEqual(translation.count("    description: "), len(options), lang)

    def test_every_option_reaches_the_app_and_nothing_else_does(self):
        self.assertEqual(set(block(self.config(ha_addon.STABLE), "schema")), set(entrypoint.OPTION_ENV))

    def test_options_become_variables_the_app_documents(self):
        readme = (ROOT / "README.en.md").read_text(encoding="utf-8")
        for name in entrypoint.OPTION_ENV.values():
            self.assertIn(name, readme, name)

    def test_the_store_files_exist(self):
        for folder in (ha_addon.STABLE, ha_addon.BETA):
            for name in ("config.yaml", "DOCS.md", "README.md", "CHANGELOG.md", "icon.png", "logo.png"):
                self.assertTrue((folder / name).is_file(), f"{folder.name}/{name}")
            self.assertEqual((folder / "icon.png").read_bytes()[:8], b"\x89PNG\r\n\x1a\n")

    def test_no_other_config_file_looks_like_an_addon(self):
        # De Supervisor zoekt in de hele repository naar config.yaml/yml/json: elk ander bestand zou een add-on worden.
        found = [
            p.relative_to(ROOT).as_posix()
            for p in ROOT.rglob("config.*")
            if p.name in {"config.yaml", "config.yml", "config.json"} and ".git" not in p.relative_to(ROOT).parts
            and not any(part.startswith(".") for part in p.relative_to(ROOT).parts)
        ]
        self.assertEqual(sorted(found), ["homeassistant/mealieshopper-beta/config.yaml", "homeassistant/mealieshopper/config.yaml"])


class ImageTest(unittest.TestCase):
    def test_dockerfile_starts_through_the_entrypoint_and_keeps_gunicorn(self):
        text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn('ENTRYPOINT ["python", "/app/docker-entrypoint.py"]', text)
        self.assertIn('CMD ["gunicorn"', text)
        self.assertRegex(text, r"COPY .*docker-entrypoint\.py")
        self.assertIn("MEALIESHOPPER_DATA_DIR=/data", text)

    def test_the_build_tells_the_image_which_line_it_is(self):
        self.assertIn("ENV MEALIESHOPPER_TAG=$BUILD_TAG", (ROOT / "Dockerfile").read_text(encoding="utf-8"))
        workflow = (ROOT / ".github" / "workflows" / "docker.yml").read_text(encoding="utf-8")
        self.assertIn("BUILD_TAG=${{ github.ref == 'refs/heads/beta' && 'beta' || 'latest' }}", workflow)


class EntrypointTest(unittest.TestCase):
    def test_options_become_environment_variables(self):
        env = entrypoint.env_from_options(
            {
                "mealie_url": " http://mealie:9000 ",
                "mealie_api_token": "tok",
                "offers_schedule": False,
                "mcp": True,
                "offers_sync_hour": 0,
                "picnic_country_code": "DE",
                "audit_retention_days": 14,
            }
        )
        self.assertEqual(
            env,
            {
                "MEALIE_URL": "http://mealie:9000",
                "MEALIE_API_TOKEN": "tok",
                "MEALIESHOPPER_OFFERS_SCHEDULE": "false",
                "MEALIESHOPPER_MCP": "true",
                "MEALIESHOPPER_OFFERS_SYNC_HOUR": "0",
                "PICNIC_COUNTRY_CODE": "DE",
                "MEALIESHOPPER_AUDIT_RETENTION_DAYS": "14",
            },
        )

    def test_empty_missing_and_unknown_options_set_nothing(self):
        self.assertEqual(entrypoint.env_from_options({}), {})
        self.assertEqual(entrypoint.env_from_options({"mealie_url": "", "rp_id": None, "auth_secret": "  ", "onbekend": "x"}), {})

    def test_missing_or_broken_options_file_gives_no_options(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(entrypoint.load_options(str(Path(tmp) / "options.json")), {})
            broken = Path(tmp) / "broken.json"
            broken.write_text("{niet json", encoding="utf-8")
            self.assertEqual(entrypoint.load_options(str(broken)), {})
            listing = Path(tmp) / "list.json"
            listing.write_text("[1]", encoding="utf-8")
            self.assertEqual(entrypoint.load_options(str(listing)), {})

    def test_main_sets_the_environment_and_hands_over_to_the_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            options = Path(tmp) / "options.json"
            options.write_text(json.dumps({"mealie_url": "http://mealie:9000"}), encoding="utf-8")
            with mock.patch.dict(os.environ, {"MEALIESHOPPER_OPTIONS_FILE": str(options)}), \
                    mock.patch.object(entrypoint.os, "execvp") as execvp:
                entrypoint.main(["docker-entrypoint.py", "gunicorn", "run:app"])
                self.assertEqual(os.environ["MEALIE_URL"], "http://mealie:9000")
            os.environ.pop("MEALIE_URL", None)
        execvp.assert_called_once_with("gunicorn", ["gunicorn", "run:app"])

    def test_without_options_the_command_starts_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            before = dict(os.environ)
            with mock.patch.dict(os.environ, {"MEALIESHOPPER_OPTIONS_FILE": str(Path(tmp) / "none.json")}), \
                    mock.patch.object(entrypoint.os, "execvp") as execvp:
                entrypoint.main(["docker-entrypoint.py", "gunicorn"])
                self.assertEqual({k: v for k, v in os.environ.items() if k != "MEALIESHOPPER_OPTIONS_FILE"}, {k: v for k, v in before.items() if k != "MEALIESHOPPER_OPTIONS_FILE"})
        execvp.assert_called_once_with("gunicorn", ["gunicorn"])

    def test_no_command_is_an_error(self):
        with self.assertRaises(SystemExit):
            entrypoint.main(["docker-entrypoint.py"])


if __name__ == "__main__":
    unittest.main()
