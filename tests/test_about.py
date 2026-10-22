import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

from mealieshopper import about, auth, auth_password  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
APP_JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")


class VersionTest(unittest.TestCase):
    def setUp(self):
        about.build_info.cache_clear()
        self.addCleanup(about.build_info.cache_clear)

    def test_version_file_holds_a_plain_x_y_z(self):
        self.assertRegex((ROOT / "VERSION").read_text(encoding="utf-8").strip(), r"^\d+\.\d+\.\d+$")

    def test_label_is_v_version_with_the_commit_in_brackets(self):
        with mock.patch.dict(os.environ, {"MEALIESHOPPER_VERSION": "2.3.4", "MEALIESHOPPER_COMMIT": "abcdef0123456789"}):
            info = about.build_info()
        self.assertEqual(info, {"version": "2.3.4", "commit": "abcdef0", "label": "v2.3.4 (abcdef0)"})

    def test_a_leading_v_is_not_doubled(self):
        with mock.patch.dict(os.environ, {"MEALIESHOPPER_VERSION": "v1.2.3", "MEALIESHOPPER_COMMIT": "1234567"}):
            self.assertEqual(about.build_info()["label"], "v1.2.3 (1234567)")

    def test_version_comes_from_the_file_by_default(self):
        with mock.patch.dict(os.environ, {"MEALIESHOPPER_COMMIT": "1234567"}):
            os.environ.pop("MEALIESHOPPER_VERSION", None)
            expected = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
            self.assertEqual(about.build_info()["version"], expected)

    def test_unknown_commit_from_the_image_falls_back_to_git_or_nothing(self):
        with mock.patch.dict(os.environ, {"MEALIESHOPPER_COMMIT": "unknown"}), \
                mock.patch.object(about.subprocess, "run", side_effect=OSError("geen git")):
            self.assertEqual(about.read_commit(), "")
        with mock.patch.dict(os.environ, {"MEALIESHOPPER_COMMIT": ""}), \
                mock.patch.object(about, "ROOT", Path(tempfile.gettempdir()) / "geen-repo"):
            self.assertEqual(about.read_commit(), "", "zonder .git ook niets")

    def test_label_without_a_commit_has_no_brackets(self):
        with mock.patch.dict(os.environ, {"MEALIESHOPPER_VERSION": "1.0.0", "MEALIESHOPPER_COMMIT": "unknown"}), \
                mock.patch.object(about, "read_commit", return_value=""):
            self.assertEqual(about.build_info()["label"], "v1.0.0")

    def test_dockerfile_and_workflow_pass_the_commit_and_ship_the_version(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("ARG GIT_COMMIT", dockerfile)
        self.assertIn("MEALIESHOPPER_COMMIT=$GIT_COMMIT", dockerfile)
        self.assertIn("COPY VERSION", dockerfile)
        workflow = (ROOT / ".github" / "workflows" / "docker.yml").read_text(encoding="utf-8")
        self.assertIn("GIT_COMMIT=${{ github.sha }}", workflow)
        self.assertNotIn("VERSION", (ROOT / ".dockerignore").read_text(encoding="utf-8"))

    def test_workflow_builds_the_image_for_amd64_and_arm64(self):
        # Zonder arm64 start de container op een Synology/ARM-NAS niet ("exec format error").
        workflow = (ROOT / ".github" / "workflows" / "docker.yml").read_text(encoding="utf-8")
        self.assertIn("platforms: linux/amd64,linux/arm64", workflow)
        self.assertIn("docker/setup-qemu-action", workflow)
        self.assertIn("docker/setup-buildx-action", workflow)


class ComponentsTest(unittest.TestCase):
    def test_installed_python_packages_report_their_real_version(self):
        data = {c["id"]: c for c in about.about()["components"]}
        for ident in ("flask", "gunicorn", "pyjwt", "cryptography", "cbor2", "requests"):
            self.assertRegex(data[ident]["version"], r"^\d+(\.\d+)+", ident)
        self.assertRegex(data["python"]["version"], r"^3\.\d+\.\d+")
        self.assertRegex(data["sqlite"]["version"], r"^3\.\d+")

    def test_every_component_has_a_name_licence_and_https_link(self):
        for component in about.about()["components"]:
            self.assertTrue(component["name"])
            self.assertTrue(component["url"].startswith("https://"), component["id"])
            if component["group"] != "integrations":
                self.assertTrue(component["license"], component["id"])

    def test_requirements_are_all_listed(self):
        listed = {(c[3] or "").lower() for c in about.COMPONENTS}
        for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
            name = re.split(r"[=<>]", line.strip())[0].lower()
            if name:
                self.assertIn(name, listed, f"{name} staat in requirements.txt maar niet bij Over")

    def test_vendored_assets_match_the_files_in_the_repo(self):
        data = {c["id"]: c for c in about.about()["components"]}
        self.assertTrue((ROOT / "static" / "vendor" / f"qrcode-{data['qrcode']['version']}.js").exists())
        self.assertTrue((ROOT / "static" / "icons" / "LICENSE-mdi.txt").exists())

    def test_coffee_link(self):
        self.assertEqual(about.about()["coffeeUrl"], "https://buymeacoffee.com/flux76")
        self.assertIn('href="https://buymeacoffee.com/flux76"', HTML)
        link = re.search(r'<a[^>]*id="about-coffee"[^>]*>', HTML).group(0)
        self.assertIn('rel="noopener noreferrer"', link)
        self.assertIn('target="_blank"', link)


class RouteTest(unittest.TestCase):
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
        self.client = create_app().test_client()

    def test_needs_a_login_when_security_is_on(self):
        self.client.post("/api/auth/password", json={"username": "admin", "password": "koffie-met-melk-42"})
        self.assertEqual(self.app_client().get("/api/about").status_code, 401)
        res = self.client.get("/api/about")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertRegex(data["label"], r"^v\d+\.\d+\.\d+")
        self.assertGreaterEqual(len(data["components"]), 15)

    def app_client(self):
        return create_app().test_client()


class PageTest(unittest.TestCase):
    def test_about_is_the_sixth_tab_with_versions_components_and_coffee_in_that_order(self):
        section = HTML[HTML.index('<section id="about"'):HTML.index("</main>")]
        self.assertLess(section.index('id="about-version"'), section.index('id="about-components"'))
        self.assertLess(section.index('id="about-components"'), section.index('id="about-coffee"'))
        self.assertEqual(re.findall(r'<button class="tab[^"]*" data-tab="([\w-]+)"', HTML)[-1], "about")

    def test_script_loads_the_details_once_and_redraws_on_a_language_change(self):
        self.assertIn('panelId === "about" && !state.aboutLoaded', APP_JS)
        self.assertIn('jsonFetch("/api/about")', APP_JS)
        handler = APP_JS[APP_JS.index('addEventListener("ms:languagechange"'):]
        self.assertIn("renderAbout()", handler)
        self.assertIn('rel="noopener noreferrer"', APP_JS[APP_JS.index("function renderAbout"):])

    def test_every_component_has_a_description_in_both_languages(self):
        from test_i18n import EN, NL

        for ident in (c[0] for c in about.COMPONENTS):
            self.assertIn(f"about.desc.{ident}", NL, ident)
            self.assertIn(f"about.desc.{ident}", EN, ident)
        for group in ("integrations", "server", "interface"):
            self.assertIn(f"about.group.{group}", NL)


if __name__ == "__main__":
    unittest.main()
