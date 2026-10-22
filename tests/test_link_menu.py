import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
NL = (ROOT / "static" / "i18n" / "nl.js").read_text(encoding="utf-8")
EN = (ROOT / "static" / "i18n" / "en.js").read_text(encoding="utf-8")
APP_JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")

PAGE = HTML[HTML.index('<div id="beheer-link" class="subpanel'):HTML.index('<div id="beheer-security" class="subpanel">')]
STORES = ("mealie", "ah", "picnic", "jumbo")


def block(name):
    start = PAGE.index(f'<div class="box fold" id="{name}-login-box">')
    nxt = PAGE.find('<div class="box fold"', start + 10)
    return PAGE[start:nxt if nxt != -1 else len(PAGE)]


class LinkPageTest(unittest.TestCase):
    def test_koppelen_is_a_plain_subtab_and_the_dropdown_is_gone(self):
        nav = re.search(r'<nav class="subtabs" aria-label="Beheer".*?</nav>', HTML, re.S).group(0)
        self.assertIn('<button class="subtab is-active" data-subtab="beheer-link" data-admin-only data-i18n="settings.tab.link">Koppelen</button>', nav)
        self.assertNotIn("subtab-menu", nav)
        for gone in ("beheer-ah", "beheer-picnic", "beheer-jumbo", "beheer-mealie", "beheer-link-menu", "beheer-stores"):
            self.assertNotIn(gone, HTML, gone)

    def test_statuses_come_first_two_next_to_each_other(self):
        grid = PAGE[PAGE.index('<div class="link-statuses">'):PAGE.index('<div class="box fold"')]
        self.assertEqual(re.findall(r'id="(\w+)-status"', grid), list(STORES))
        rule = re.search(r"\.link-statuses\s*{(.*?)}", CSS, re.S).group(1)
        self.assertIn("repeat(2", rule)
        for store in STORES:
            self.assertEqual(PAGE.count(f'id="{store}-status"'), 1, store)

    def test_blocks_are_collapsible_with_mealie_first_and_the_stores_below(self):
        positions = [PAGE.index(f'id="{store}-login-box"') for store in STORES]
        self.assertEqual(positions, sorted(positions))
        for store in STORES:
            self.assertIn(f'id="{store}-login-toggle" aria-expanded="true" aria-controls="{store}-login-body"', block(store))
            self.assertIn(f'id="{store}-login-body"', block(store))
        self.assertIn('for (const store of ["mealie", "ah", "picnic", "jumbo"])', APP_JS)

    def test_unlink_sits_next_to_the_link_button_in_the_store_block(self):
        for store, link in (("picnic", "picnic-login"), ("jumbo", "jumbo-link"), ("mealie", "mealie-link")):
            row = re.search(r'<div class="toolbar">\s*<button[^>]*id="%s".*?</div>' % link, block(store), re.S).group(0)
            self.assertIn(f'id="{store}-unlink"', row, store)
            self.assertIn("common.check_link", row, store)
        row = re.search(r'<div class="toolbar">\s*<button[^>]*id="verify-token".*?</div>', block("ah"), re.S).group(0)
        self.assertIn('id="check-ah"', row)

    def test_titles_all_say_log_in_to(self):
        self.assertIn('"ahlogin.title": "Inloggen bij Albert Heijn"', NL)
        self.assertIn('"picnic.title": "Inloggen bij Picnic"', NL)
        self.assertIn('"jumbo.title": "Inloggen bij Jumbo"', NL)
        self.assertIn('"ahlogin.title": "Log in to Albert Heijn"', EN)
        for store, title in (("ah", "Inloggen bij Albert Heijn"), ("picnic", "Inloggen bij Picnic"), ("jumbo", "Inloggen bij Jumbo")):
            self.assertIn(f'<span data-i18n="{"ahlogin" if store == "ah" else store}.title">{title}</span>', block(store))

    def test_folds_close_when_linked(self):
        self.assertIn("function applyStoreFold(store, linked)", APP_JS)
        self.assertIn('applyStoreFold("ah", Boolean(data.connected))', APP_JS)
        self.assertIn('applyStoreFold("mealie", Boolean(data.configured))', APP_JS)
        self.assertIn('applyStoreFold("picnic", picnicState.connected)', (ROOT / "static" / "picnic.js").read_text(encoding="utf-8"))
        self.assertIn('applyStoreFold("jumbo", jumboState.connected)', (ROOT / "static" / "jumbo.js").read_text(encoding="utf-8"))

    def test_texts_point_to_the_new_place(self):
        for path in ("mealieshopper/ah.py", "mealieshopper/jumbo.py", "mealieshopper/picnic.py", "mealieshopper/mealie.py", "static/i18n/nl.js"):
            self.assertNotIn("Beheer > Winkels >", (ROOT / path).read_text(encoding="utf-8"), path)
        self.assertNotIn("Settings > Stores >", EN)


if __name__ == "__main__":
    unittest.main()
