import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
NL = (ROOT / "static" / "i18n" / "nl.js").read_text(encoding="utf-8")
EN = (ROOT / "static" / "i18n" / "en.js").read_text(encoding="utf-8")
APP_JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")


def panel(name):
    start = HTML.index(f'<div id="beheer-{name}" class="subpanel')
    return HTML[start:HTML.index('<div id="beheer-', start + 10)]


class LinkMenuTest(unittest.TestCase):
    def setUp(self):
        self.nav = re.search(r'<nav class="subtabs" aria-label="Beheer".*?</nav>', HTML, re.S).group(0)

    def test_link_menu_holds_the_stores_and_mealie(self):
        menu = re.search(r'<div class="subtab-menu" id="beheer-link-menu".*?</div>\s*</div>', self.nav, re.S).group(0)
        self.assertIn('data-admin-only', menu)
        self.assertIn('data-i18n="settings.tab.link">Koppelen<', menu)
        self.assertEqual(re.findall(r'data-subtab="([\w-]+)"', menu), ["beheer-ah", "beheer-picnic", "beheer-jumbo", "beheer-mealie"])
        self.assertNotIn("beheer-stores", HTML)

    def test_mealie_has_no_tab_of_its_own_any_more(self):
        outside = self.nav.replace(re.search(r'<div class="subtab-menu".*?</div>\s*</div>', self.nav, re.S).group(0), "")
        self.assertNotIn("beheer-mealie", outside)

    def test_every_store_page_has_status_on_top_and_one_collapsible_login_block(self):
        for store in ("ah", "picnic", "jumbo"):
            page = panel(store)
            self.assertLess(page.index(f'id="{store}-status"'), page.index(f'id="{store}-login-box"'), store)
            self.assertEqual(page.count('class="box fold"'), 1, store)
            self.assertIn(f'id="{store}-login-toggle" aria-expanded="true" aria-controls="{store}-login-body"', page)
            body = page[page.index(f'id="{store}-login-body"'):]
            self.assertIn("ahfinish.title" if store != "picnic" else "picnic.code_title", body, "afronden zit in het blok")
            self.assertRegex(body, rf'id="{"explore" if store == "ah" else store + "-explore"}-run"', "verkennen zit in het blok")
            self.assertNotIn(f'id="{store}-status"', body)

    def test_titles_all_say_log_in_to(self):
        self.assertIn('"ahlogin.title": "Inloggen bij Albert Heijn"', NL)
        self.assertIn('"picnic.title": "Inloggen bij Picnic"', NL)
        self.assertIn('"jumbo.title": "Inloggen bij Jumbo"', NL)
        self.assertIn('"ahlogin.title": "Log in to Albert Heijn"', EN)
        for store, title in (("ah", "Inloggen bij Albert Heijn"), ("picnic", "Inloggen bij Picnic"), ("jumbo", "Inloggen bij Jumbo")):
            self.assertIn(f'<span data-i18n="{"ahlogin" if store == "ah" else store}.title">{title}</span>', panel(store))

    def test_folds_close_when_the_store_is_linked(self):
        self.assertIn("function applyStoreFold(store, linked)", APP_JS)
        self.assertIn('applyStoreFold("ah", Boolean(data.connected))', APP_JS)
        self.assertIn('applyStoreFold("picnic", picnicState.connected)', (ROOT / "static" / "picnic.js").read_text(encoding="utf-8"))
        self.assertIn('applyStoreFold("jumbo", jumboState.connected)', (ROOT / "static" / "jumbo.js").read_text(encoding="utf-8"))

    def test_texts_point_to_the_new_place(self):
        for path in ("mealieshopper/ah.py", "mealieshopper/jumbo.py", "mealieshopper/picnic.py", "mealieshopper/mealie.py", "static/i18n/nl.js"):
            self.assertNotIn("Beheer > Winkels >", (ROOT / path).read_text(encoding="utf-8"), path)
        self.assertNotIn("Settings > Stores >", EN)


if __name__ == "__main__":
    unittest.main()
