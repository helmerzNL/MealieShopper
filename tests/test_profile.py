import base64
import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("MEALIESHOPER_DATA_DIR", tempfile.mkdtemp())

from mealieshopper import auth, profile, users  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402
from test_users import ANNA, UsersTestCase  # noqa: E402

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 20
WEBP = b"RIFF\x04\x00\x00\x00WEBPVP8 "


class ProfileTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"MEALIESHOPPER_DATA_DIR": tmp.name, "PASSKEY_AUTH_ENABLED": "false"})
        patcher.start()
        self.addCleanup(patcher.stop)
        auth.init_db()
        self.client = create_app().test_client()

    def test_display_name_is_cleaned_and_shown_in_the_status(self):
        response = self.client.put("/api/profile", json={"displayName": "  Helmer \n  Z\x00  "})
        self.assertEqual(response.get_json()["displayName"], "Helmer Z")
        self.assertEqual(self.client.get("/api/auth/status").get_json()["displayName"], "Helmer Z")
        self.assertEqual(len(profile.clean_name("x" * 100)), profile.MAX_NAME_LENGTH)
        self.assertEqual(self.client.put("/api/profile", json={"displayName": ""}).get_json()["displayName"], "")

    def test_avatar_roundtrip_with_version_and_removal(self):
        first = self.client.post("/api/profile/avatar", data=PNG, content_type="image/png").get_json()
        self.assertEqual((first["hasAvatar"], first["avatarVersion"]), (True, 1))
        served = self.client.get(f"/api/profile/avatar/{auth.OWNER_ID}")
        self.assertEqual((served.status_code, served.mimetype, served.data), (200, "image/png", PNG))
        self.assertEqual(served.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(self.client.post("/api/profile/avatar", data=JPEG, content_type="image/jpeg").get_json()["avatarVersion"], 2)
        gone = self.client.delete("/api/profile/avatar").get_json()
        self.assertEqual((gone["hasAvatar"], gone["avatarVersion"]), (False, 3))
        self.assertEqual(self.client.get(f"/api/profile/avatar/{auth.OWNER_ID}").status_code, 404)

    def test_only_real_png_jpeg_or_webp_under_the_limit_is_accepted(self):
        for data, content_type in (
            (b"<svg xmlns='http://www.w3.org/2000/svg'/>", "image/svg+xml"),
            (b"<svg xmlns='http://www.w3.org/2000/svg'/>", "image/png"),  # SVG die zich als PNG voordoet
            (PNG, "image/jpeg"),  # type klopt niet met de inhoud
            (b"", "image/png"),
            (PNG, "text/html"),
        ):
            response = self.client.post("/api/profile/avatar", data=data, content_type=content_type)
            self.assertEqual(response.status_code, 400, content_type)
        self.assertEqual(self.client.post("/api/profile/avatar", data=PNG + b"0" * profile.MAX_AVATAR_BYTES, content_type="image/png").status_code, 413)
        self.assertEqual(self.client.post("/api/profile/avatar", data=WEBP, content_type="image/webp").status_code, 200)

    def test_users_list_carries_the_profile_and_deleting_a_user_removes_it(self):
        users.create_user("u1", "anna")
        profile.set_display_name("u1", "Anna de Vries")
        profile.set_avatar("u1", PNG, "image/png")
        listed = {user["id"]: user for user in users.list_users()}
        self.assertEqual((listed["u1"]["displayName"], listed["u1"]["hasAvatar"]), ("Anna de Vries", True))
        self.assertTrue(users.delete_user("u1"))
        self.assertIsNone(profile.avatar("u1"))
        self.assertEqual(profile.get("u1")["displayName"], "")


class ProfileWithLoginTest(UsersTestCase):
    def test_everything_needs_a_login(self):
        anonymous = self.app.test_client()
        self.assertEqual(anonymous.get("/api/profile").status_code, 401)
        self.assertEqual(anonymous.put("/api/profile", json={"displayName": "x"}).status_code, 401)
        self.assertEqual(anonymous.post("/api/profile/avatar", data=PNG, content_type="image/png").status_code, 401)
        self.assertEqual(anonymous.get(f"/api/profile/avatar/{auth.OWNER_ID}").status_code, 401)

    def test_each_user_only_changes_their_own_profile(self):
        anna = self.join("anna", ANNA)
        self.assertEqual(anna.put("/api/profile", json={"displayName": "Anna de Vries"}).get_json()["displayName"], "Anna de Vries")
        self.assertEqual(anna.post("/api/profile/avatar", data=PNG, content_type="image/png").status_code, 200)
        self.assertEqual(self.admin.get("/api/profile").get_json()["displayName"], "", "de beheerder houdt zijn eigen profiel")
        status = anna.get("/api/auth/status").get_json()
        self.assertEqual((status["displayName"], status["hasAvatar"], status["username"]), ("Anna de Vries", True, "anna"))
        listed = {u["username"]: u for u in self.admin.get("/api/users").get_json()["users"]}
        self.assertEqual((listed["anna"]["displayName"], listed["anna"]["hasAvatar"]), ("Anna de Vries", True))
        self.assertEqual(self.admin.get(f"/api/profile/avatar/{listed['anna']['id']}").data, PNG)
        self.assertEqual(self.admin.get("/api/profile/avatar/owner").status_code, 404)


class ProfileMarkupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        cls.html = (root / "templates" / "index.html").read_text(encoding="utf-8")
        cls.js = (root / "static" / "profile.js").read_text(encoding="utf-8")
        cls.css = (root / "static" / "styles.css").read_text(encoding="utf-8")

    def section(self, name):
        start = self.html.index(f'<section id="{name}"')
        return self.html[start:self.html.index("</section>", start)]

    def test_menu_has_profile_settings_for_the_admin_and_logout(self):
        import re

        start = self.html.index('id="user-menu-list"')
        menu = self.html[start:self.html.index("</div>\n\n    <main", start)]
        self.assertEqual(re.findall(r'data-user-action="(\w+)"', menu), ["profile", "settings", "about"])
        settings = re.search(r'<button[^>]*data-user-action="settings"[^>]*>', menu).group(0)
        self.assertIn("data-admin-only", settings, "Instellingen is alleen voor de beheerder")
        profile_item = re.search(r'<button[^>]*data-user-action="profile"[^>]*>', menu).group(0)
        self.assertNotIn("data-admin-only", profile_item)
        self.assertIn('id="auth-logout"', menu)
        self.assertNotIn("auth-chip", self.html)

    def test_profile_has_general_security_and_ai_apps_for_everyone(self):
        import re

        profile_section = self.section("profile")
        nav = re.search(r'<nav class="subtabs".*?</nav>', profile_section, re.S).group(0)
        self.assertEqual(re.findall(r'data-subtab="([\w-]+)"', nav), ["profile-general", "profile-security", "profile-ai"])
        self.assertNotIn("data-admin-only", nav, "AI-apps en de rest van het profiel zijn voor iedereen")
        for needle in ('id="profile-avatar-file" accept="image/png,image/jpeg,image/webp"', 'id="profile-name"', 'id="profile-form"'):
            self.assertIn(needle, profile_section, needle)

    def test_about_opens_from_the_menu(self):
        self.assertIn('item.dataset.userAction === "about"', self.js)
        self.assertIn('showPanel("about")', self.js)

    def test_security_has_one_password_block_with_two_step_inside_and_a_passkey_block(self):
        security = self.html[self.html.index('<div id="profile-security"'):self.html.index('<div id="profile-ai"')]
        self.assertEqual(security.count('class="box'), 2, "wachtwoord (met tweestapsverificatie) en passkeys")
        password = security[security.index('id="security-password-box"'):security.index('id="credentials-list"')]
        self.assertIn('id="mfa-panel"', password)
        self.assertIn('id="credentials-list"', security)

    def test_settings_holds_link_users_audit_and_updates(self):
        import re

        nav = re.search(r'<nav class="subtabs".*?</nav>', self.section("settings"), re.S).group(0)
        self.assertEqual(re.findall(r'data-subtab="([\w-]+)"', nav), ["settings-link", "settings-users", "settings-audit", "settings-updates"])
        self.assertEqual(nav.count("data-admin-only"), 4)

    def test_avatar_is_resized_in_the_browser_and_sent_with_its_own_type(self):
        self.assertIn("AVATAR_SIZE = 256", self.js)
        self.assertIn('"image/jpeg"', self.js)
        self.assertIn('"Content-Type": blob.type', self.js)
        self.assertIn("avatar__initials", self.js, "zonder afbeelding de initialen")


if __name__ == "__main__":
    unittest.main()
