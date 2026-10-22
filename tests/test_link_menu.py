import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
NL = (ROOT / "static" / "i18n" / "nl.js").read_text(encoding="utf-8")
EN = (ROOT / "static" / "i18n" / "en.js").read_text(encoding="utf-8")
APP_JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")

PAGE = HTML[HTML.index('<div id="settings-link" class="subpanel'):HTML.index('<div id="settings-users" class="subpanel')]
STORES = ("mealie", "ah", "jumbo", "picnic")


def block(name):
    start = PAGE.index(f'<div class="box fold" id="{name}-login-box">')
    nxt = PAGE.find('<div class="box fold"', start + 10)
    return PAGE[start:nxt if nxt != -1 else len(PAGE)]


class LinkPageTest(unittest.TestCase):
    def test_koppelen_is_the_first_subtab_of_settings_and_the_dropdown_is_gone(self):
        nav = re.search(r'<nav class="subtabs" aria-label="Instellingen".*?</nav>', HTML, re.S).group(0)
        self.assertIn('<button class="subtab is-active" data-subtab="settings-link" data-admin-only data-i18n="settings.tab.link">Koppelen</button>', nav)
        self.assertEqual(re.findall(r'data-subtab="([\w-]+)"', nav), ["settings-link", "settings-users", "settings-audit", "settings-system"])
        self.assertNotIn("subtab-menu", nav)
        for gone in ("beheer", "beheer-link-menu", "beheer-stores"):
            self.assertNotIn(f'id="{gone}', HTML, gone)
            self.assertNotIn(f'data-subtab="{gone}', HTML, gone)

    def test_statuses_come_first_two_next_to_each_other(self):
        grid = PAGE[PAGE.index('<div class="link-statuses">'):PAGE.index('<div class="box fold"')]
        self.assertEqual(re.findall(r'id="(\w+)-status"', grid), list(STORES))
        rule = re.search(r"\.link-statuses\s*{(.*?)}", CSS, re.S).group(1)
        self.assertIn("repeat(2", rule)
        self.assertIn("max-width: 680px", rule, "even breed als .box")
        self.assertIn("max-width: 680px", re.search(r"^\.box\s*{(.*?)}", CSS, re.S | re.M).group(1))
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

    def test_blocks_are_titled_with_just_the_name(self):
        for key, title in (("ahlogin.title", "Albert Heijn"), ("picnic.title", "Picnic"), ("jumbo.title", "Jumbo"), ("link.mealie", "Mealie")):
            self.assertIn(f'"{key}": "{title}"', NL, key)
            self.assertIn(f'"{key}": "{title}"', EN, key)
        for store, key, title in (("mealie", "link.mealie", "Mealie"), ("ah", "ahlogin.title", "Albert Heijn"), ("jumbo", "jumbo.title", "Jumbo"), ("picnic", "picnic.title", "Picnic")):
            self.assertIn(f'<span data-i18n="{key}">{title}</span>', block(store))

    def test_status_banners_have_icons_and_colours_for_small_screens(self):
        self.assertIn('"link.status.linked": "gekoppeld"', NL)
        self.assertIn('"link.status.unlinked": "nog niet gekoppeld"', NL)
        self.assertIn('"link.status.working": "gekoppeld en werkt"', NL)
        self.assertIn('"link.status.linked": "linked"', EN)
        for needle in ("MDI_LINK", "MDI_LINK_OFF", "MDI_CHECK_CIRCLE", 'classes: "error"', "link-status--working"):
            self.assertIn(needle, APP_JS, needle)
        for store, script in (("picnic", "picnic.js"), ("jumbo", "jumbo.js")):
            self.assertIn(f'renderLinkStatus("#{store}-status"', (ROOT / "static" / script).read_text(encoding="utf-8"))
        icon = re.search(r"\.link-status__icon\s*{(.*?)}", CSS, re.S).group(1)
        self.assertIn("display: none", icon, "op een groot scherm de tekst")
        media = CSS[CSS.rindex("@media (max-width: 640px) {\n  .link-status__icon"):]
        self.assertIn("display: block", media)
        self.assertIn(".link-status__text {\n    position: absolute", media, "de tekst blijft voor schermlezers")
        grid = re.search(r"\.link-statuses\s*{(.*?)}", CSS, re.S).group(1)
        self.assertIn("repeat(2", grid)
        self.assertNotRegex(CSS, r"@media \(max-width: 640px\) {\s*\.link-statuses", "ook op een klein scherm twee naast elkaar")

    def test_folds_close_when_linked(self):
        self.assertIn("function applyStoreFold(store, linked)", APP_JS)
        self.assertIn('applyStoreFold("ah", Boolean(data.connected))', APP_JS)
        self.assertIn('applyStoreFold("mealie", Boolean(data.configured))', APP_JS)
        self.assertIn('applyStoreFold("picnic", picnicState.connected)', (ROOT / "static" / "picnic.js").read_text(encoding="utf-8"))
        self.assertIn('applyStoreFold("jumbo", jumboState.connected)', (ROOT / "static" / "jumbo.js").read_text(encoding="utf-8"))

    def test_texts_point_to_the_new_place(self):
        for path in ("mealieshopper/ah.py", "mealieshopper/jumbo.py", "mealieshopper/picnic.py", "mealieshopper/mealie.py", "static/i18n/nl.js", "README.md"):
            text = (ROOT / path).read_text(encoding="utf-8")
            self.assertNotIn("Beheer >", text, path)
            self.assertNotIn("Beheer, Beveiliging", text, path)
        self.assertNotIn("Settings > Stores", EN)
        self.assertIn("Instellingen > Koppelen > Picnic", (ROOT / "mealieshopper" / "picnic.py").read_text(encoding="utf-8"))

if __name__ == "__main__":
    unittest.main()
