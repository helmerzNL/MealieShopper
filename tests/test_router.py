"""Een adres per scherm (static/router.js): /recipes, /offers/history, /stores/ah/orders/123, /cart, ... en navigatie als echte links."""
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("MEALIESHOPPER_DATA_DIR", tempfile.mkdtemp())

from mealieshopper import auth  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ROUTER = (ROOT / "static" / "router.js").read_text(encoding="utf-8")
HTML = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")

PAGES = ["/recipes", "/recipes/search", "/offers", "/offers/history", "/import", "/stores", "/stores/ah/orders/225301",
         "/stores/ah/receipts/AH-123", "/stores/jumbo/lists/7", "/meal-kits", "/cart", "/profile/security", "/settings/system", "/about"]


class PageRoutesTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"MEALIESHOPPER_DATA_DIR": tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = create_app().test_client()

    def test_every_page_address_serves_the_app(self):
        home = self.client.get("/").get_data(as_text=True)
        for path in PAGES:
            reply = self.client.get(path)
            self.assertEqual(reply.status_code, 200, path)
            self.assertEqual(reply.get_data(as_text=True), home, path)

    def test_page_addresses_are_as_public_as_the_home_page_and_the_api_is_not(self):
        with mock.patch.object(auth, "auth_required", return_value=True), mock.patch.object(auth, "current_user", return_value=None):
            for path in ["/", *PAGES]:
                self.assertEqual(self.client.get(path).status_code, 200, f"{path}: het inlogscherm staat op de pagina zelf")
            self.assertEqual(self.client.get("/api/prices/history").status_code, 401)
        self.assertTrue(auth.is_page_path("/cart"))
        self.assertFalse(auth.is_page_path("/cartoon"), "alleen de wortel zelf of een pad eronder")
        self.assertFalse(auth.is_page_path("/api/cart"))

    def test_unknown_addresses_stay_unknown(self):
        with mock.patch.object(auth, "auth_required", return_value=False):
            self.assertEqual(self.client.get("/nope").status_code, 404)
            self.assertEqual(self.client.get("/static/router.js").status_code, 200)

    def test_settings_are_still_for_the_administrator_only(self):
        self.assertIn('panel === "settings" && !routerIsOwner()', ROUTER)


class LinkMarkupTest(unittest.TestCase):
    def test_tabs_subtabs_and_the_user_menu_are_links_with_an_address(self):
        tabs = dict(re.findall(r'<a class="tab[^"]*" data-tab="([\w-]+)"[^>]* href="([^"]+)" data-nav>', HTML))
        self.assertEqual(tabs, {"recipes": "/recipes", "offers": "/offers", "url": "/import", "ah-items": "/stores", "meal-kits": "/meal-kits", "basket": "/cart"})
        subtabs = dict(re.findall(r'<a class="subtab[^"]*" data-subtab="([\w-]+)"[^>]* href="([^"]+)" data-nav>', HTML))
        for subtab, path in {"search": "/recipes/search", "offers-history": "/offers/history", "ah-items-bestellingen": "/stores/ah/orders",
                             "profile-security": "/profile/security", "settings-system": "/settings/system"}.items():
            self.assertEqual(subtabs.get(subtab), path, subtab)
        users = dict(re.findall(r'<a[^>]*data-user-action="(\w+)"[^>]* href="([^"]+)" data-nav>', HTML))
        self.assertEqual(users, {"profile": "/profile", "settings": "/settings", "about": "/about"})
        self.assertNotRegex(HTML, r'<button[^>]*data-(?:tab|subtab)="', "geen navigatie meer als knop")

    def test_router_loads_last(self):
        scripts = re.findall(r"filename='([\w/]+\.js)'", HTML)
        self.assertEqual(scripts[-1], "router.js")


class RouterScriptTest(unittest.TestCase):
    def test_a_click_with_a_modifier_or_another_button_is_left_to_the_browser(self):
        for needle in ("event.button !== 0", "event.metaKey", "event.ctrlKey", "event.shiftKey", "event.altKey"):
            self.assertIn(needle, ROUTER)
        # In de vangfase: de eigen handler van de link ziet de klik dan niet en het scherm hier wisselt niet.
        self.assertIn("event.stopImmediatePropagation();\n      return;", ROUTER)
        self.assertRegex(ROUTER, r'document\.addEventListener\(\s*"click",[\s\S]*?,\s*true\s*\);')

    def test_back_and_forward_open_the_screen_of_the_address(self):
        self.assertIn('window.addEventListener("popstate"', ROUTER)
        self.assertIn("routerApply(window.location.href)", ROUTER)

    def test_every_page_root_has_a_screen(self):
        roots = re.search(r"const ROUTE_PANELS = \{(.*?)\};", ROUTER, re.S).group(1)
        names = set(re.findall(r'^\s*"?([\w-]+)"?:', roots, re.M))
        self.assertEqual(names, set(auth.PAGE_ROOTS))

    def test_typing_in_a_search_box_replaces_the_address(self):
        history = (ROOT / "static" / "offers_history.js").read_text(encoding="utf-8")
        self.assertIn("routerSync({ replace: true })", history)


if __name__ == "__main__":
    unittest.main()
