import base64
import os
import tempfile
import unittest
from contextlib import closing
from unittest import mock

os.environ.setdefault("MEALIESHOPER_DATA_DIR", tempfile.mkdtemp())

from mealieshopper import auth, profile, users  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402
from test_users import ANNA, UsersTestCase  # noqa: E402
from helpers import AppTestCase, db, run  # noqa: E402

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 20
WEBP = b"RIFF\x04\x00\x00\x00WEBPVP8 "


class ProfileTest(AppTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"MEALIESHOPPER_DATA_DIR": tmp.name, "PASSKEY_AUTH_ENABLED": "false"})
        patcher.start()
        self.addCleanup(patcher.stop)
        run(auth.init_db())
        self.client = self.client_for(create_app())

    async def test_display_name_is_cleaned_and_shown_in_the_status(self):
        response = await self.client.put("/api/profile", json={"displayName": "  Helmer \n  Z\x00  "})
        self.assertEqual(response.get_json()["displayName"], "Helmer Z")
        self.assertEqual((await self.client.get("/api/auth/status")).get_json()["displayName"], "Helmer Z")
        self.assertEqual(len(profile.clean_name("x" * 100)), profile.MAX_NAME_LENGTH)
        self.assertEqual((await self.client.put("/api/profile", json={"displayName": ""})).get_json()["displayName"], "")

    async def test_avatar_roundtrip_with_version_and_removal(self):
        first = (await self.client.post("/api/profile/avatar", data=PNG, content_type="image/png")).get_json()
        self.assertEqual((first["hasAvatar"], first["avatarVersion"]), (True, 1))
        served = await self.client.get(f"/api/profile/avatar/{auth.OWNER_ID}")
        self.assertEqual((served.status_code, served.mimetype, served.data), (200, "image/png", PNG))
        self.assertEqual(served.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual((await self.client.post("/api/profile/avatar", data=JPEG, content_type="image/jpeg")).get_json()["avatarVersion"], 2)
        gone = (await self.client.delete("/api/profile/avatar")).get_json()
        self.assertEqual((gone["hasAvatar"], gone["avatarVersion"]), (False, 3))
        self.assertEqual((await self.client.get(f"/api/profile/avatar/{auth.OWNER_ID}")).status_code, 404)

    async def test_only_real_png_jpeg_or_webp_under_the_limit_is_accepted(self):
        for data, content_type in (
            (b"<svg xmlns='http://www.w3.org/2000/svg'/>", "image/svg+xml"),
            (b"<svg xmlns='http://www.w3.org/2000/svg'/>", "image/png"),  # SVG die zich als PNG voordoet
            (PNG, "image/jpeg"),  # type klopt niet met de inhoud
            (b"", "image/png"),
            (PNG, "text/html"),
        ):
            response = await self.client.post("/api/profile/avatar", data=data, content_type=content_type)
            self.assertEqual(response.status_code, 400, content_type)
        self.assertEqual((await self.client.post("/api/profile/avatar", data=PNG + b"0" * profile.MAX_AVATAR_BYTES, content_type="image/png")).status_code, 413)
        self.assertEqual((await self.client.post("/api/profile/avatar", data=WEBP, content_type="image/webp")).status_code, 200)

    async def test_prefer_organic_is_kept_without_touching_the_display_name(self):
        await self.client.put("/api/profile", json={"displayName": "Helmer"})
        self.assertFalse((await self.client.get("/api/auth/status")).get_json()["preferOrganic"])  # standaard uit
        reply = await self.client.put("/api/profile/preferences", json={"preferOrganic": True})
        self.assertEqual((reply.status_code, reply.get_json()["preferOrganic"], reply.get_json()["displayName"]), (200, True, "Helmer"))
        status = (await self.client.get("/api/auth/status")).get_json()
        self.assertEqual((status["preferOrganic"], status["displayName"]), (True, "Helmer"))
        for bad in ({}, {"preferOrganic": "ja"}, {"preferOrganic": 1}):
            self.assertEqual((await self.client.put("/api/profile/preferences", json=bad)).status_code, 400, bad)
        self.assertFalse((await self.client.put("/api/profile/preferences", json={"preferOrganic": False})).get_json()["preferOrganic"])

    async def test_appearance_is_stored_per_field_and_shown_in_the_status(self):
        await self.client.put("/api/profile", json={"displayName": "Helmer"})
        status = (await self.client.get("/api/auth/status")).get_json()
        self.assertEqual((status["theme"], status["darkBackground"], status["accent"]), (None, "green", ""), "standaard: niets gekozen")
        reply = await self.client.put("/api/profile/preferences", json={"theme": "dark", "darkBackground": "grey", "accent": "#0A84FF"})
        self.assertEqual(reply.status_code, 200)
        data = reply.get_json()
        self.assertEqual((data["theme"], data["darkBackground"], data["accent"], data["displayName"]), ("dark", "grey", "#0a84ff", "Helmer"))
        # Alleen wat in het verzoek staat verandert.
        await self.client.put("/api/profile/preferences", json={"preferOrganic": True})
        await self.client.put("/api/profile/preferences", json={"theme": "system"})
        status = (await self.client.get("/api/auth/status")).get_json()
        self.assertEqual(
            (status["theme"], status["darkBackground"], status["accent"], status["preferOrganic"]), ("system", "grey", "#0a84ff", True)
        )
        self.assertEqual((await self.client.put("/api/profile/preferences", json={"accent": ""})).get_json()["accent"], "", "terug naar de huisstijl")

    async def test_appearance_refuses_values_it_does_not_know(self):
        for bad in (
            {"theme": "sepia"}, {"theme": None}, {"darkBackground": "black"}, {"accent": "red"}, {"accent": "#12345"},
            {"accent": "#1234567"}, {"accent": 123}, {"accent": "#12345g"}, {"accent": "url(x)"},
            {"theme": "dark", "accent": "nope"},  # alles of niets
        ):
            self.assertEqual((await self.client.put("/api/profile/preferences", json=bad)).status_code, 400, bad)
        self.assertIsNone((await self.client.get("/api/auth/status")).get_json()["theme"], "een geweigerd verzoek verandert niets")
        self.assertEqual((await self.client.put("/api/profile/preferences", data="[]", content_type="application/json")).status_code, 400)

    async def test_navigation_bar_style_and_glass(self):
        # Zoals de richtlijn navigation-bar.md van App-Guidance: elke stijl en het glas 0 en 100 mogen, de rest geeft 400.
        status = (await self.client.get("/api/auth/status")).get_json()
        self.assertEqual((status["navbarStyle"], status["navbarGlass"]), ("auto", 60), "standaard: de balk van het toestel")
        for style in ("ios", "android", "auto"):
            self.assertEqual((await self.client.put("/api/profile/preferences", json={"navbarStyle": style})).get_json()["navbarStyle"], style)
        for glass in (0, 100, 35):
            self.assertEqual((await self.client.put("/api/profile/preferences", json={"navbarGlass": glass})).get_json()["navbarGlass"], glass)
        await self.client.put("/api/profile/preferences", json={"navbarStyle": "android"})
        for bad in ({"navbarStyle": "windows"}, {"navbarStyle": None}, {"navbarGlass": -1}, {"navbarGlass": 101},
                    {"navbarGlass": 50.5}, {"navbarGlass": "50"}, {"navbarGlass": True}, {"navbarGlass": None}):
            self.assertEqual((await self.client.put("/api/profile/preferences", json=bad)).status_code, 400, bad)
        status = (await self.client.get("/api/auth/status")).get_json()
        self.assertEqual((status["navbarStyle"], status["navbarGlass"]), ("android", 35), "een weigering verandert niets")

    async def test_cooking_mode_is_on_by_default_and_can_be_switched_off(self):
        self.assertTrue((await self.client.get("/api/auth/status")).get_json()["cookingMode"], "standaard aan")
        reply = await self.client.put("/api/profile/preferences", json={"cookingMode": False})
        self.assertEqual((reply.status_code, reply.get_json()["cookingMode"]), (200, False))
        await self.client.put("/api/profile/preferences", json={"accent": "#0b62c4"})
        self.assertFalse((await self.client.get("/api/auth/status")).get_json()["cookingMode"], "een andere voorkeur laat hem staan")
        for bad in ({"cookingMode": 0}, {"cookingMode": "nee"}, {"cookingMode": None}):
            self.assertEqual((await self.client.put("/api/profile/preferences", json=bad)).status_code, 400, bad)
        self.assertFalse((await self.client.get("/api/auth/status")).get_json()["cookingMode"], "een weigering verandert niets")
        self.assertTrue((await self.client.put("/api/profile/preferences", json={"cookingMode": True})).get_json()["cookingMode"])

    async def test_calories_are_shown_by_default_and_can_be_hidden(self):
        # Issue #283: iedere gebruiker kiest zelf of hij de calorieën bij een recept (en het filter erop) ziet.
        self.assertTrue((await self.client.get("/api/auth/status")).get_json()["showCalories"], "standaard aan")
        reply = await self.client.put("/api/profile/preferences", json={"showCalories": False})
        self.assertEqual((reply.status_code, reply.get_json()["showCalories"]), (200, False))
        await self.client.put("/api/profile/preferences", json={"cookingMode": False})
        self.assertFalse((await self.client.get("/api/auth/status")).get_json()["showCalories"], "een andere voorkeur laat hem staan")
        for bad in ({"showCalories": 0}, {"showCalories": "nee"}, {"showCalories": None}):
            self.assertEqual((await self.client.put("/api/profile/preferences", json=bad)).status_code, 400, bad)
        self.assertFalse((await self.client.get("/api/profile")).get_json()["showCalories"], "een weigering verandert niets")
        self.assertTrue((await self.client.put("/api/profile/preferences", json={"showCalories": True})).get_json()["showCalories"])

    async def test_preferred_store_is_albert_heijn_until_another_is_chosen(self):
        self.assertEqual((await self.client.get("/api/auth/status")).get_json()["preferredStore"], "ah", "standaard Albert Heijn")
        for store in ("jumbo", "picnic", "crisp", "ah", "jumbo"):
            reply = await self.client.put("/api/profile/preferences", json={"preferredStore": store})
            self.assertEqual((reply.status_code, reply.get_json()["preferredStore"]), (200, store))
        await self.client.put("/api/profile/preferences", json={"cookingMode": False})
        self.assertEqual((await self.client.get("/api/auth/status")).get_json()["preferredStore"], "jumbo", "een andere voorkeur laat hem staan")
        for bad in ({"preferredStore": "lidl"}, {"preferredStore": "AH"}, {"preferredStore": ""}, {"preferredStore": None},
                    {"preferredStore": 1}, {"preferredStore": "picnic", "theme": "sepia"}):
            self.assertEqual((await self.client.put("/api/profile/preferences", json=bad)).status_code, 400, bad)
        self.assertEqual((await self.client.get("/api/auth/status")).get_json()["preferredStore"], "jumbo", "een weigering verandert niets")
        self.assertEqual((await self.client.get("/api/profile")).get_json()["preferredStore"], "jumbo")

    def test_a_bad_stored_value_counts_as_no_choice(self):
        run(profile.set_display_name("owner", "x"))
        with closing(db()) as conn, conn:  # zoals een handmatig gewijzigde back-up
            conn.execute(
                "UPDATE user_profiles SET theme = 'sepia', dark_background = 'black', accent = 'javascript:x', "
                "navbar_style = 'windows', navbar_glass = 500, preferred_store = 'lidl'"
            )
        self.assertEqual(
            {key: run(profile.get("owner"))[key] for key in ("theme", "darkBackground", "accent", "navbarStyle", "navbarGlass", "preferredStore")},
            {"theme": None, "darkBackground": "green", "accent": "", "navbarStyle": "auto", "navbarGlass": 60, "preferredStore": "ah"},
        )

    def test_an_older_database_gets_the_prefer_organic_column(self):
        with closing(db()) as conn, conn:  # zoals de tabel was voor #218
            conn.execute("DROP TABLE IF EXISTS user_profiles")
            conn.execute(
                "CREATE TABLE user_profiles (user_id TEXT PRIMARY KEY, display_name TEXT NOT NULL DEFAULT '', avatar BLOB, "
                "avatar_type TEXT, avatar_version INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL)"
            )
            conn.execute("INSERT INTO user_profiles (user_id, display_name, updated_at) VALUES ('owner', 'Oud', '2026-01-01')")
        self.assertEqual(run(profile.get("owner"))["preferOrganic"], False)
        run(profile.set_prefer_organic("owner", True))
        self.assertEqual((run(profile.get("owner"))["preferOrganic"], run(profile.get("owner"))["displayName"]), (True, "Oud"))
        self.assertEqual((run(profile.get("owner"))["theme"], run(profile.get("owner"))["accent"]), (None, ""), "en de kolommen van de weergave")
        self.assertEqual(run(profile.get("owner"))["preferredStore"], "ah", "en de voorkeurssupermarkt")
        run(profile.set_appearance("owner", accent="#b0213f"))
        self.assertEqual((run(profile.get("owner"))["accent"], run(profile.get("owner"))["darkBackground"]), ("#b0213f", "green"))

    def test_users_list_carries_the_profile_and_deleting_a_user_removes_it(self):
        run(users.create_user("u1", "anna"))
        run(profile.set_display_name("u1", "Anna de Vries"))
        run(profile.set_avatar("u1", PNG, "image/png"))
        listed = {user["id"]: user for user in run(users.list_users())}
        self.assertEqual((listed["u1"]["displayName"], listed["u1"]["hasAvatar"]), ("Anna de Vries", True))
        self.assertTrue(run(users.delete_user("u1")))
        self.assertIsNone(run(profile.avatar("u1")))
        self.assertEqual(run(profile.get("u1"))["displayName"], "")


class ProfileWithLoginTest(UsersTestCase):
    async def test_everything_needs_a_login(self):
        anonymous = self.client_for(self.app)
        self.assertEqual((await anonymous.get("/api/profile")).status_code, 401)
        self.assertEqual((await anonymous.put("/api/profile", json={"displayName": "x"})).status_code, 401)
        self.assertEqual((await anonymous.post("/api/profile/avatar", data=PNG, content_type="image/png")).status_code, 401)
        self.assertEqual((await anonymous.get(f"/api/profile/avatar/{auth.OWNER_ID}")).status_code, 401)

    async def test_each_user_only_changes_their_own_profile(self):
        anna = await self.join("anna", ANNA)
        self.assertEqual((await anna.put("/api/profile", json={"displayName": "Anna de Vries"})).get_json()["displayName"], "Anna de Vries")
        self.assertEqual((await anna.post("/api/profile/avatar", data=PNG, content_type="image/png")).status_code, 200)
        self.assertEqual((await self.admin.get("/api/profile")).get_json()["displayName"], "", "de beheerder houdt zijn eigen profiel")
        status = (await anna.get("/api/auth/status")).get_json()
        self.assertEqual((status["displayName"], status["hasAvatar"], status["username"]), ("Anna de Vries", True, "anna"))
        listed = {u["username"]: u for u in (await self.admin.get("/api/users")).get_json()["users"]}
        self.assertEqual((listed["anna"]["displayName"], listed["anna"]["hasAvatar"]), ("Anna de Vries", True))
        self.assertEqual((await self.admin.get(f"/api/profile/avatar/{listed['anna']['id']}")).data, PNG)
        self.assertEqual((await self.admin.get("/api/profile/avatar/owner")).status_code, 404)

    async def test_each_user_has_their_own_appearance(self):
        anna = await self.join("anna", ANNA)
        self.assertEqual((await anna.put("/api/profile/preferences", json={"accent": "#e0457b", "darkBackground": "grey"})).status_code, 200)
        self.assertEqual((await self.admin.put("/api/profile/preferences", json={"accent": "#0c9aa6", "theme": "dark"})).status_code, 200)
        mine = (await anna.get("/api/auth/status")).get_json()
        theirs = (await self.admin.get("/api/auth/status")).get_json()
        self.assertEqual((mine["accent"], mine["darkBackground"], mine["theme"]), ("#e0457b", "grey", None))
        self.assertEqual((await anna.put("/api/profile/preferences", json={"navbarStyle": "android"})).status_code, 200)
        self.assertEqual((await self.admin.get("/api/auth/status")).get_json()["navbarStyle"], "auto", "de beheerder houdt de eigen balk")
        self.assertEqual((theirs["accent"], theirs["darkBackground"], theirs["theme"]), ("#0c9aa6", "green", "dark"))
        self.assertEqual((await anna.put("/api/profile/preferences", json={"preferredStore": "picnic"})).status_code, 200)
        self.assertEqual((await anna.get("/api/auth/status")).get_json()["preferredStore"], "picnic")
        self.assertEqual((await self.admin.get("/api/auth/status")).get_json()["preferredStore"], "ah", "de beheerder houdt de eigen supermarkt")
        self.assertEqual((await self.client_for(self.app).put("/api/profile/preferences", json={"accent": "#000000"})).status_code, 401)


class ProfileMarkupTest(AppTestCase):
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
        self.assertEqual(re.findall(r'data-user-action="(\w+)"', menu), ["basket", "profile", "settings", "about"])
        settings = re.search(r'<a[^>]*data-user-action="settings"[^>]*>', menu).group(0)
        self.assertNotIn("data-admin-only", settings, "Instellingen is voor iedereen: een gewone gebruiker ziet daar alleen Weergave")
        profile_item = re.search(r'<a[^>]*data-user-action="profile"[^>]*>', menu).group(0)
        self.assertNotIn("data-admin-only", profile_item)
        self.assertIn('id="auth-logout"', menu)
        self.assertNotIn("auth-chip", self.html)

    def test_menu_order_separators_and_an_icon_per_item(self):
        # Avatar en naam, een lijn, Mandje (alleen op een klein scherm), Profiel, Instellingen, Over, een lijn, Uitloggen.
        import re

        start = self.html.index('id="user-menu-list"')
        menu = self.html[start:self.html.index("</div>\n\n    <main", start)]
        parts = re.findall(r'class="(user-menu__who|user-menu__separator)"|data-user-action="(\w+)"|id="(auth-logout)"', menu)
        self.assertEqual([next(p for p in part if p) for part in parts],
                         ["user-menu__who", "basket", "profile", "settings", "about", "user-menu__separator", "auth-logout"])
        self.assertIn("border-bottom: 1px solid var(--border)", re.search(r"\.user-menu__who\s*{(.*?)}", self.css, re.S).group(1),
                      "de lijn onder avatar en naam")
        self.assertIn('role="separator"', menu)
        items = re.findall(r'<(?:a|button)[^>]*class="user-menu__item[^"]*".*?</(?:a|button)>', menu, re.S)
        self.assertEqual(len(items), 5)
        icons = [re.search(r'<svg class="user-menu__icon" viewBox="0 0 24 24" aria-hidden="true" data-mdi="(\w+)"><path d="[^"]+"/></svg>', item)
                 for item in items]
        self.assertTrue(all(icons), "elk item heeft een icoon")
        self.assertEqual([icon.group(1) for icon in icons],
                         ["mdiCart", "mdiAccountCircleOutline", "mdiCogOutline", "mdiInformationOutline", "mdiLogout"])
        for item in items:
            self.assertNotRegex(item.split(">")[0], r"data-i18n=", "de vertaling staat op de tekst, niet op het item (anders verdwijnt het icoon)")
        basket = re.search(r'<a[^>]*data-user-action="basket"[^>]*>', menu).group(0)
        self.assertIn("user-menu__item--small", basket)
        self.assertIn('href="/cart"', basket)
        icon = re.search(r"\.user-menu__icon\s*{(.*?)}", self.css, re.S).group(1)
        self.assertIn("fill: currentColor", icon)

    def test_menu_script_opens_the_basket_and_hides_the_logout_line_with_logout(self):
        self.assertIn('item.dataset.userAction === "basket"', self.js)
        self.assertIn('showPanel("basket"', self.js)
        self.assertIn('$("#auth-logout-separator").hidden = !auth.enabled;', self.js)
        self.assertIn('$("#tab-more").hidden = !visible;', self.js)
        self.assertNotIn('list.querySelector("button:not([hidden])")?.focus()', self.js, "niet de focus op Uitloggen")
        from pathlib import Path

        app = (Path(__file__).resolve().parent.parent / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn('const MORE_MENU_PANELS = ["profile", "settings", "about", "basket"];', app)
        self.assertIn('$("#tab-more")', app)

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

    def test_settings_holds_link_users_audit_and_updates_and_appearance_for_everyone(self):
        import re

        nav = re.search(r'<nav class="subtabs".*?</nav>', self.section("settings"), re.S).group(0)
        self.assertEqual(
            re.findall(r'data-subtab="([\w-]+)"', nav),
            ["settings-link", "settings-users", "settings-audit", "settings-system", "settings-appearance"],
        )
        self.assertEqual(nav.count("data-admin-only"), 4, "alleen Weergave is voor iedereen")
        appearance = re.search(r'<a[^>]*data-subtab="settings-appearance"[^>]*>', nav).group(0)
        self.assertNotIn("data-admin-only", appearance)

    def test_avatar_is_resized_in_the_browser_and_sent_with_its_own_type(self):
        self.assertIn("AVATAR_SIZE = 256", self.js)
        self.assertIn('"image/jpeg"', self.js)
        self.assertIn('"Content-Type": blob.type', self.js)
        self.assertIn("avatar__initials", self.js, "zonder afbeelding de initialen")


if __name__ == "__main__":
    unittest.main()
