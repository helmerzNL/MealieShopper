import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

from mealieshopper import about, auth_password  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402
from helpers import AppTestCase, run  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
APP_JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")


class VersionTest(AppTestCase):
    def setUp(self):
        about.reset()
        self.addCleanup(about.reset)

    def test_version_file_holds_a_plain_x_y_z(self):
        self.assertRegex((ROOT / "VERSION").read_text(encoding="utf-8").strip(), r"^\d+\.\d+\.\d+$")

    def test_label_is_v_version_with_the_commit_in_brackets(self):
        with mock.patch.dict(os.environ, {"MEALIESHOPPER_VERSION": "2.3.4", "MEALIESHOPPER_COMMIT": "abcdef0123456789"}):
            info = run(about.build_info())
        self.assertEqual(info, {"version": "2.3.4", "commit": "abcdef0", "label": "v2.3.4 (abcdef0)"})

    def test_a_leading_v_is_not_doubled(self):
        with mock.patch.dict(os.environ, {"MEALIESHOPPER_VERSION": "v1.2.3", "MEALIESHOPPER_COMMIT": "1234567"}):
            self.assertEqual(run(about.build_info())["label"], "v1.2.3 (1234567)")

    def test_version_comes_from_the_file_by_default(self):
        with mock.patch.dict(os.environ, {"MEALIESHOPPER_COMMIT": "1234567"}):
            os.environ.pop("MEALIESHOPPER_VERSION", None)
            expected = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
            self.assertEqual(run(about.build_info())["version"], expected)

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
            self.assertEqual(run(about.build_info())["label"], "v1.0.0")

    async def test_git_runs_in_a_thread_and_once(self):
        # `git rev-parse` mag de event loop niet vasthouden: build_info vraagt het via asyncio.to_thread, een keer.
        with mock.patch.dict(os.environ, {"MEALIESHOPPER_VERSION": "1.0.0", "MEALIESHOPPER_COMMIT": ""}), \
                mock.patch.object(about.asyncio, "to_thread", wraps=about.asyncio.to_thread) as to_thread, \
                mock.patch.object(about, "read_commit", return_value="abc1234") as read_commit:
            first = await about.build_info()
            second = await about.build_info()
        self.assertEqual(first, second)
        to_thread.assert_called_once_with(read_commit)
        read_commit.assert_called_once_with()

    def test_the_version_is_read_once_for_the_user_agent(self):
        # crisp, hellofresh, marleyspoon, lidl en aldi zetten de versie in hun User-Agent bij elk verzoek: het bestand niet elke keer lezen.
        from mealieshopper import aldi, crisp, hellofresh, lidl, marleyspoon

        with mock.patch.dict(os.environ, {"MEALIESHOPPER_VERSION": "7.8.9"}), mock.patch.object(about, "read_version", wraps=about.read_version) as read:
            agents = {crisp._user_agent(), hellofresh._user_agent(), marleyspoon._user_agent(), crisp._user_agent(), lidl._user_agent(), aldi._user_agent()}
        self.assertEqual(agents, {"MealieShopper/7.8.9 (+https://github.com/helmerzNL/MealieShopper)"})
        self.assertEqual(read.call_count, 1)

    def test_httpx_replaces_requests_in_the_components(self):
        ids = [ident for ident, *_ in about.COMPONENTS]
        self.assertIn("httpx", ids)
        self.assertNotIn("requests", ids)
        httpx_row = next(row for row in about.COMPONENTS if row[0] == "httpx")
        self.assertEqual(httpx_row[2:], ("HTTPX", "httpx", None, "BSD-3-Clause", "https://www.python-httpx.org"))

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


class ChannelTest(AppTestCase):
    def test_tag_decides_the_channel(self):
        for tag, channel in (("beta", "beta"), ("2.70.0-beta", "beta"), (" Beta ", "beta"), ("latest", "stable"), ("2.70.0", "stable"), ("", "stable")):
            with mock.patch.dict(os.environ, {"MEALIESHOPPER_TAG": tag}):
                self.assertEqual(about.build_channel(), channel, tag)

    def test_without_the_variable_the_channel_is_stable(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("MEALIESHOPPER_TAG", None)
            self.assertEqual(about.build_channel(), "stable")
            self.assertEqual(run(about.about())["channel"], "stable")

    def test_compose_uses_one_tag_for_the_image_and_the_app(self):
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("mealieshopper:${MEALIESHOPPER_TAG:-latest}", compose)
        self.assertIn("MEALIESHOPPER_TAG: ${MEALIESHOPPER_TAG:-latest}", compose)
        self.assertIn("MEALIESHOPPER_TAG=latest", (ROOT / ".env.example").read_text(encoding="utf-8"))

    async def _index(self, tag):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {"MEALIESHOPPER_TAG": tag, "MEALIESHOPPER_DATA_DIR": tmp, "PASSKEY_AUTH_ENABLED": "false"}
        ):
            return (await self.client_for(create_app()).get("/")).get_data(as_text=True)

    async def test_beta_badge_only_on_a_beta_build(self):
        self.assertIn('id="beta-badge"', await self._index("beta"))
        self.assertNotIn('id="beta-badge"', await self._index("latest"))


class ComponentsTest(AppTestCase):
    def test_installed_python_packages_report_their_real_version(self):
        data = {c["id"]: c for c in run(about.about())["components"]}
        for ident in ("quart", "hypercorn", "pyjwt", "cryptography", "cbor2", "httpx", "aiosqlite"):
            self.assertRegex(data[ident]["version"], r"^\d+(\.\d+)+", ident)
        self.assertRegex(data["python"]["version"], r"^3\.\d+\.\d+")
        self.assertRegex(data["sqlite"]["version"], r"^3\.\d+")

    def test_every_component_has_a_name_licence_and_https_link(self):
        for component in run(about.about())["components"]:
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

    def test_flask_and_werkzeug_are_pinned_like_the_rest(self):
        """Quart vraagt `flask>=3.0` en `werkzeug>=3.0` zonder bovengrens: een nieuwe major mag niet ongemerkt in de image."""
        lines = [line.strip() for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines() if line.strip()]
        for line in lines:
            self.assertRegex(line, r"^[A-Za-z0-9_.-]+==\d+(\.\d+)+(\.post\d+)?$", line)
        pinned = {line.split("==")[0].lower() for line in lines}
        self.assertLessEqual({"quart", "flask", "werkzeug", "hypercorn"}, pinned)

    def test_vendored_assets_match_the_files_in_the_repo(self):
        data = {c["id"]: c for c in run(about.about())["components"]}
        self.assertTrue((ROOT / "static" / "vendor" / f"qrcode-{data['qrcode']['version']}.js").exists())
        self.assertTrue((ROOT / "static" / "icons" / "LICENSE-mdi.txt").exists())

    def test_coffee_link(self):
        self.assertEqual(run(about.about())["coffeeUrl"], "https://buymeacoffee.com/flux76")
        self.assertIn('href="https://buymeacoffee.com/flux76"', HTML)
        link = re.search(r'<a[^>]*id="about-coffee"[^>]*>', HTML).group(0)
        self.assertIn('rel="noopener noreferrer"', link)
        self.assertIn('target="_blank"', link)

    def test_repo_link(self):
        """Over linkt naar de broncode op GitHub, in een nieuw tabblad en met een vertaalde tekst."""
        self.assertEqual(run(about.about())["repoUrl"], "https://github.com/helmerzNL/MealieShopper")
        link = re.search(r'<a[^>]*id="about-repo"[^>]*>', HTML).group(0)
        self.assertIn('href="https://github.com/helmerzNL/MealieShopper"', link)
        self.assertIn('rel="noopener noreferrer"', link)
        self.assertIn('target="_blank"', link)
        self.assertIn('data-i18n="about.repo"', HTML[HTML.index('id="about-repo"'):])
        for lang in ("nl", "en"):
            self.assertIn('"about.repo": "MealieShopper o', (ROOT / "static" / "i18n" / f"{lang}.js").read_text(encoding="utf-8"), lang)


class RouteTest(AppTestCase):
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
        self.client = self.client_for(create_app())

    async def test_needs_a_login_when_security_is_on(self):
        await self.client.post("/api/auth/password", json={"username": "admin", "password": "koffie-met-melk-42"})
        self.assertEqual((await self.app_client().get("/api/about")).status_code, 401)
        res = await self.client.get("/api/about")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertRegex(data["label"], r"^v\d+\.\d+\.\d+")
        self.assertGreaterEqual(len(data["components"]), 15)

    def app_client(self):
        return self.client_for(create_app())


class PageTest(AppTestCase):
    def test_about_page_has_versions_components_and_coffee_in_that_order_and_sits_in_the_avatar_menu(self):
        section = HTML[HTML.index('<section id="about"'):HTML.index("</main>")]
        self.assertLess(section.index('id="about-version"'), section.index('id="about-repo"'))
        self.assertLess(section.index('id="about-repo"'), section.index('id="about-components"'))
        self.assertLess(section.index('id="about-components"'), section.index('id="about-coffee"'))
        self.assertNotIn('data-tab="about"', HTML)
        menu = HTML[HTML.index('id="user-menu-list"'):HTML.index("</div>\n\n    <main")]
        self.assertEqual(re.findall(r'data-user-action="(\w+)"', menu), ["basket", "profile", "settings", "about", "theme", "lang", "reload"],
                         "Over staat na Instellingen, en voor thema, taal, Vernieuwen (alleen in de app) en Uitloggen")
        self.assertLess(menu.index('data-user-action="about"'), menu.index('id="auth-logout"'))

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
