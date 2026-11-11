import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

from mealieshopper.app import create_app  # noqa: E402
from helpers import AppTestCase  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CSS = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")


def tokens() -> dict[str, str]:
    root = re.search(r":root\s*{(.*?)\n}", CSS, re.S).group(1)
    return {name: value.strip() for name, value in re.findall(r"(--[\w-]+):\s*([^;]+);", root)}


def block(selector: str) -> dict[str, str]:
    body = re.search(re.escape(selector) + r"\s*{(.*?)\n}", CSS, re.S).group(1)
    return {name: value.strip() for name, value in re.findall(r"(--[\w-]+):\s*([^;]+);", body)}


# De dekking van het glas: 1 - 0,0085 x de schuif (static/theme.js, NAV_ALPHA_STEP), zoals in 3dprint_pricing: 1 bij 0, 0,49 bij
# de standaard 60, 0,15 bij 100. Tot stand 29 (dekking 0,75) halen de labels boven elke foto 4,5 : 1; daarboven is het de keuze
# van wie de schuif verzet.
GLASS_STEP = 0.0085
READABLE_UP_TO = 29
GLASS_ALPHAS = (1.0, round(1 - 15 * GLASS_STEP, 4), round(1 - READABLE_UP_TO * GLASS_STEP, 4))


def rgba(value: str) -> tuple[float, float, float, float]:
    value = value.strip().lower()
    if value.startswith("#"):
        return int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16), 1.0
    r, g, b, *a = [float(x) for x in re.findall(r"[\d.]+", value)]
    return r, g, b, (a[0] if a else 1.0)


def over(top: str, bottom: tuple[float, float, float]) -> tuple[float, float, float]:
    r, g, b, a = rgba(top)
    return tuple(a * t + (1 - a) * u for t, u in zip((r, g, b), bottom))


def luminance(rgb) -> float:
    lin = [(c / 255 / 12.92) if c / 255 <= 0.03928 else (((c / 255) + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a, b) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


class TabbarMarkupTest(AppTestCase):
    async def asyncSetUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"MEALIESHOPPER_DATA_DIR": tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.html = (await self.client_for(create_app()).get("/")).get_data(as_text=True)
        self.nav = re.search(r'<nav class="tabs".*?</nav>', self.html, re.S).group(0)

    def test_viewport_reaches_under_the_home_indicator(self):
        self.assertIn("viewport-fit=cover", self.html)

    def test_five_tabs_with_icon_long_and_short_label(self):
        buttons = re.findall(r'<a class="tab[^"]*" data-tab="([\w-]+)".*?</a>', self.nav, re.S)
        self.assertEqual(buttons, ["recipes", "offers", "ah-items", "meal-kits", "basket"])
        self.assertEqual(self.nav.count('class="tab-icon"'), 6, "vijf tabs en de knop Meer")
        self.assertEqual(self.nav.count('aria-hidden="true"'), 17, "iconen en kort label worden niet voorgelezen")
        short = re.findall(r'<span class="tab-short"[^>]*>([^<]+)</span>', self.nav)
        self.assertEqual(short, ["Recepten", "Aanbod", "Winkels", "Boxen", "Mandje", "Meer"])
        long = re.findall(r'<span class="tab-text"[^>]*>([^<]+)</span>', self.nav)
        self.assertEqual(long, ["Recepten", "Aanbiedingen", "Winkels", "Maaltijdboxen", "Mandje", "Meer"])

    def test_url_import_is_no_longer_in_the_menu_but_its_address_still_works(self):
        self.assertNotIn('data-tab="url"', self.nav)
        self.assertNotIn('href="/import"', self.html)
        self.assertIn('<section id="url" class="panel">', self.html, "/import opent het scherm nog")
        router = (ROOT / "static" / "router.js").read_text(encoding="utf-8")
        self.assertIn('import: "url"', router)

    def test_more_is_the_last_tab_and_shows_on_small_screens_only(self):
        self.assertNotIn('data-tab="beheer"', self.html)
        self.assertNotIn("tab--avatar", self.html + CSS, "de avatar staat niet meer in de tabbalk")
        more = re.search(r'<button class="tab tab--more" id="tab-more".*?</button>', self.nav, re.S).group(0)
        self.assertIn("hidden", more.split(">")[0], "zonder inlog is er geen menu")
        self.assertIn('data-user-menu-trigger', more)
        self.assertIn('aria-controls="user-menu-list"', more)
        self.assertIn('data-mdi="mdiMenu"', more, "een hamburger-icoon")
        self.assertIn('data-i18n="nav.more">Meer<', more)
        self.assertNotIn("data-avatar", more)
        self.assertLess(self.nav.index('data-tab="basket"'), self.nav.index('id="tab-more"'))
        self.assertNotIn('data-tab="about"', self.nav, "Over zit in het menu Meer")
        self.assertRegex(CSS, r"\.tab--more\s*{\s*display: none;")
        media = CSS[CSS.rindex("@media (max-width: 640px) {\n  .tab--more"):]
        media = media[:media.index("\n}\n")]
        self.assertIn(".tab--more:not([hidden])", media)
        self.assertIn(".user-menu {\n    display: none;", media, "op een klein scherm staat de avatar niet in de kop")

    def test_basket_moves_into_the_more_menu_on_small_screens(self):
        media = CSS[CSS.rindex("@media (max-width: 640px) {\n  .tab--more"):]
        media = media[:media.index("\n}\n")]
        self.assertIn('.tabs .tab[data-tab="basket"] {\n    display: none;', media)
        self.assertIn(".user-menu__item--small:not([hidden]) {\n    display: flex;", media)
        self.assertRegex(CSS, r"\.user-menu__item\[hidden\],\s*\.user-menu__item--small,\s*\.user-menu__item--standalone\s*{\s*display: none;",
                         "op een groot scherm staat Mandje niet in het menu")

    def test_icons_are_material_design_icons_with_the_text_colour(self):
        svgs = re.findall(r'<svg class="tab-icon".*?</svg>', self.nav, re.S)
        self.assertEqual(
            [re.search(r'data-mdi="(\w+)"', s).group(1) for s in svgs],
            ["mdiCalendarWeek", "mdiSale", "mdiBasket", "mdiPackageVariantClosed", "mdiCart", "mdiMenu"],
        )
        # Een tab die niet actief is toont in de balk het omlijnde icoon, als MDI dat heeft (Boxen en Meer hebben er geen).
        outlines = re.findall(r'<svg class="tab-icon tab-icon--outline".*?</svg>', self.nav, re.S)
        self.assertEqual(
            [re.search(r'data-mdi="(\w+)"', s).group(1) for s in outlines],
            ["mdiCalendarWeekOutline", "mdiSaleOutline", "mdiBasketOutline", "mdiCartOutline"],
        )
        for name in ("mdiCalendarWeek", "mdiSale", "mdiBasket", "mdiCart"):
            self.assertRegex(self.nav, f'data-mdi="{name}"><path d="[^"]+"/></svg><svg class="tab-icon tab-icon--outline"', name)
        for svg in svgs + outlines:
            self.assertIn('viewBox="0 0 24 24"', svg)
            self.assertEqual(svg.count("<path"), 1, "een MDI-icoon is een enkel pad")
            self.assertNotRegex(svg, r'(fill|stroke)="', "kleur komt uit de stylesheet")
        icon = re.search(r"\.tab-icon\s*{(.*?)}", CSS, re.S).group(1)
        self.assertIn("fill: currentColor", icon)
        self.assertNotIn("stroke", icon)

    def test_icon_licence_ships_with_the_icons(self):
        licence = (ROOT / "static" / "icons" / "LICENSE-mdi.txt").read_text(encoding="utf-8")
        self.assertIn("Pictogrammers", licence)
        self.assertIn("Apache 2.0", licence)

    def test_icons_show_in_the_top_menu_too(self):
        outside = CSS[:CSS.index("/* Onderste menubalk: een zwevende")]
        self.assertNotRegex(outside, r"\.tab-icon\s*{\s*display: none;")
        tab = re.search(r"^\.tab\s*{(.*?)}", outside, re.S | re.M).group(1)
        self.assertIn("display: inline-flex", tab)
        self.assertIn("align-items: center", tab)

    def test_recipes_tab_holds_the_week_menu_and_recipe_search_as_subtabs(self):
        section = self.html[self.html.index('<section id="recipes"'):self.html.index('<section id="basket"')]
        self.assertEqual(re.findall(r'<a class="subtab[^"]*" data-subtab="([\w-]+)"', section), ["planner", "search"])
        self.assertIn('<div id="planner" class="subpanel is-active">', section)
        self.assertIn('<div id="search" class="subpanel">', section)

    def test_current_page_is_announced(self):
        self.assertEqual(self.nav.count('aria-current="page"'), 1)
        self.assertRegex(self.nav, r'data-tab="recipes" aria-current="page"')

    def test_head_script_decides_when_the_bar_shows(self):
        script = re.search(r"<script>(.*?)</script>", self.html, re.S).group(1)
        for needle in ("has-tabbar", "navigator.standalone", "(display-mode: standalone)", "tabbar", "sessionStorage"):
            self.assertIn(needle, script, needle)
        self.assertIn("try {", script, "geblokkeerde opslag mag de pagina niet breken")

    def test_only_the_installed_app_stops_zooming_and_the_bounce(self):
        # In de browser blijft zoomen werken (toegankelijkheid); alleen de geinstalleerde app gedraagt zich als een native app.
        self.assertIn('<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">', self.html)
        script = re.search(r"<script>(.*?)</script>", self.html, re.S).group(1)
        branch = script[script.index("if (standalone) {"):]
        for needle in ('classList.add("is-standalone")', "maximum-scale=1, user-scalable=no, viewport-fit=cover", '"gesturestart"'):
            self.assertIn(needle, branch, needle)
        self.assertEqual(script.count("user-scalable=no"), 1, "nergens anders")
        rule = re.search(r"html\.is-standalone,\s*html\.is-standalone body\s*{(.*?)}", CSS, re.S).group(1)
        self.assertIn("overscroll-behavior: none", rule, "geen rubberband: de titelbalk blijft bovenaan staan")
        self.assertIn("touch-action: pan-x pan-y", rule, "scrollen wel, knijpzoom niet")
        topbar = re.search(r"\.topbar\s*{(.*?)}", CSS, re.S).group(1)
        self.assertIn("position: sticky", topbar)
        self.assertIn("top: 0", topbar)

    def test_app_js_keeps_aria_current_in_sync_and_scrolls_to_top(self):
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        handler = js[js.index('document.querySelectorAll(".tab").forEach'):js.index('document.querySelectorAll(".subtab[data-subtab]')]
        self.assertIn('removeAttribute("aria-current")', handler)
        self.assertIn('setAttribute("aria-current", "page")', handler)
        self.assertIn("window.scrollTo({ top: 0 })", handler)


class TabbarStyleTest(AppTestCase):
    def setUp(self):
        self.media = CSS[CSS.index("/* Onderste menubalk: een zwevende"):]

    def test_only_on_small_screens_in_the_installed_app(self):
        first_media = re.search(r"^@media[^{]+", self.media, re.M).group(0).strip()
        self.assertEqual(first_media, "@media (max-width: 640px)")
        self.assertNotIn("has-tabbar", CSS[:CSS.index("/* Onderste menubalk: een zwevende")], "geen balkregels buiten de mediaquery")
        outside = CSS[:CSS.index("/* Onderste menubalk: een zwevende")]
        self.assertRegex(outside, r"\.tab-short\s*{\s*display: none;")

    def test_floating_glass_bar(self):
        bar = re.search(r"html\.has-tabbar \.tabs\s*{(.*?)\n  }", self.media, re.S).group(1)
        for declaration in ("position: fixed", "grid-template-columns: repeat(5", "border-radius: 32px",
                            "bottom: max(12px, env(safe-area-inset-bottom))",
                            "left: max(12px, env(safe-area-inset-left))",
                            "right: max(12px, env(safe-area-inset-right))"):
            self.assertIn(declaration, bar, declaration)
        # Het glas volgt de schuif, met gewone waarden in rgba() en blur(): Safari laat het glas weg bij een berekening erin.
        self.assertIn("background: rgba(var(--tabbar-rgb), var(--nav-alpha))", self.media)
        self.assertIn("backdrop-filter: saturate(180%) blur(var(--nav-blur))", self.media)
        self.assertNotRegex(self.media, r"blur\(calc|rgba\([^)]*calc", "geen berekening in het glas")
        self.assertIn("-webkit-backdrop-filter", self.media, "Safari heeft de voorvoegsel nodig")
        self.assertIn("@supports (backdrop-filter: blur(1px)) or (-webkit-backdrop-filter: blur(1px))", self.media)
        self.assertRegex(self.media, r"(?s)html\.has-tabbar \.tabs\s*{.*?background: var\(--tabbar-solid\)")
        for theme in (r":root\s*{", r':root\[data-theme="dark"\]\s*{'):
            tokens = re.search(theme + r"(.*?)\n}", CSS, re.S).group(1)
            solid = re.search(r"--tabbar-solid: rgba\([\d, ]+, ([\d.]+)\)", tokens)
            self.assertGreaterEqual(float(solid.group(1)), 0.95, "zonder glas een bijna dichte balk")

    def test_content_never_hides_behind_the_bar(self):
        shell = re.search(r"html\.has-tabbar \.shell\s*{(.*?)}", self.media, re.S).group(1)
        self.assertIn("env(safe-area-inset-bottom)", shell)
        self.assertIn("var(--tabbar-h)", shell, "de hoogte van de balk die er staat, iOS- of Android-achtig")
        for bar in ("bulk-bar", "cart-review__actions"):  # de vaste balken onderaan (In bulk importeren, de controlelijst)
            self.assertIn("var(--tabbar-h)", re.search(r"html\.has-tabbar \." + bar + r"\s*{(.*?)}", self.media, re.S).group(1), bar)
        extra = int(re.search(r"var\(--tabbar-h\) \+ (\d+)px", shell).group(1))
        ios = int(re.search(r"--tabbar-h: (\d+)px", self.media).group(1))
        android = int(re.search(r'html\.has-tabbar\[data-nav="android"\]\s*{\s*--tabbar-h: (\d+)px', self.media).group(1))
        self.assertGreaterEqual(ios, 56 + 12 + 12, "de tab, de rand van de balk en de marge")
        self.assertGreaterEqual(android, 12 + 56 + 16, "de Android-balk is hoger")
        self.assertGreaterEqual(extra, 24, "adem tussen de inhoud en de balk")

    def test_android_like_bar_is_material(self):
        # Shared/guidelines/navigation-bar.md: over de volle breedte op de rand, geen glas, een pil van 64 x 32.
        bar = re.search(r'html\.has-tabbar\[data-nav="android"\] \.tabs\s*{(.*?)\n  }', self.media, re.S).group(1)
        for declaration in ("left: 0", "right: 0", "bottom: 0", "border-radius: 0", "backdrop-filter: none",
                            "background: color-mix(in srgb, var(--surface) 90%, var(--primary))", "env(safe-area-inset-bottom)"):
            self.assertIn(declaration, bar, declaration)
        icon = re.search(r'html\.has-tabbar\[data-nav="android"\] \.tab-icon\s*{(.*?)\n  }', self.media, re.S).group(1)
        self.assertIn("padding: 4px 20px", icon, "24 + 2 x 20 = 64 breed, 24 + 2 x 4 = 32 hoog")
        self.assertIn("box-sizing: content-box", icon)
        active = re.search(r'html\.has-tabbar\[data-nav="android"\] \.tab\.is-active\s*{(.*?)\n  }', self.media, re.S).group(1)
        self.assertIn("font-weight: 700", active, "het actieve item is niet alleen een kleur")

    def test_glass_defaults_match_the_formula_and_the_head_script(self):
        # De standaard in de stylesheet is schuifstand 60, anders verspringt het glas na het laden.
        defaults = re.search(r":root\s*{\s*--tabbar-h: \d+px;\s*--nav-alpha: ([\d.]+);\s*--nav-blur: (\d+)px;", self.media)
        self.assertEqual((float(defaults.group(1)), int(defaults.group(2))), (round(1 - 60 * GLASS_STEP, 2), 28))
        theme = (ROOT / "static" / "theme.js").read_text(encoding="utf-8")
        self.assertIn("const NAV_ALPHA_STEP = 0.0085;", theme)
        self.assertIn("const NAV_GLASS = 60;", theme)
        self.assertIn("(10 + g * 0.3).toFixed(1)", theme)
        html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        head = html[:html.index("</head>")]
        self.assertIn("(1 - glass * 0.0085).toFixed(3)", head)
        self.assertIn("(10 + glass * 0.3).toFixed(1)", head)
        self.assertLess(head.index('setAttribute("data-nav"'), head.index("styles.css"), "voor de eerste weergave")
        # Het toestel buiten de try: geblokkeerde opslag mag een Android-toestel niet de iOS-balk geven.
        self.assertLess(head.index('setAttribute("data-platform"'), head.index('localStorage.getItem("ms-theme")'))
        self.assertIn('navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1', head, "een iPad meldt zich als Mac")

    def test_reduced_transparency_gives_a_solid_bar(self):
        reduced = self.media[self.media.index("prefers-reduced-transparency: reduce"):]
        reduced = reduced[:reduced.index("\n}\n")]
        self.assertIn("background: var(--tabbar-solid)", reduced)
        self.assertIn("backdrop-filter: none", reduced)

    def test_touch_targets_and_type(self):
        tab = re.search(r"html\.has-tabbar \.tab\s*{(.*?)\n  }", self.media, re.S).group(1)
        self.assertGreaterEqual(int(re.search(r"min-height: (\d+)px", tab).group(1)), 44)
        self.assertIn("flex-direction: column", tab)
        self.assertIn("font-weight: 600", tab)
        self.assertRegex(tab, r"font-size: (10|11)(\.5)?px")

    def test_motion_respects_the_reduced_motion_setting(self):
        reduced = self.media[self.media.index("prefers-reduced-motion"):]
        self.assertIn("transition: none", reduced)
        self.assertIn("transform: none", reduced)

    def test_focus_ring_stays_inside_the_pill(self):
        self.assertRegex(self.media, r"(?s)html\.has-tabbar \.tab:focus-visible\s*{\s*outline-offset: -3px")

    def test_code_no_longer_breaks_inside_words(self):
        code = re.search(r"^code\s*{(.*?)}", CSS, re.S | re.M).group(1)
        self.assertNotIn("break-all", code)
        self.assertIn("overflow-wrap: anywhere", code)


class TabbarContrastTest(AppTestCase):
    """Labels moeten leesbaar blijven, ook als er een donkere of witte foto onder het glas ligt, bij elke stand van de schuif."""

    def setUp(self):
        self.t = tokens()
        dark = block(':root[data-theme="dark"]')
        grey = {**dark, **block(':root[data-theme="dark"][data-dark-bg="grey"]')}
        self.palettes = {"licht": self.t, "donker": dark, "donkergrijs": grey}
        self.glass_over = lambda base, t=None, alpha=GLASS_ALPHAS[-1]: over(f"rgba({(t or self.t)['--tabbar-rgb']}, {alpha})", base)
        self.black, self.white = (0, 0, 0), (255, 255, 255)

    def test_inactive_ink_on_worst_case_glass(self):
        for name, t in self.palettes.items():
            ink = rgba(t["--tabbar-ink"])[:3]
            for alpha in GLASS_ALPHAS:
                for base in (self.black, self.white, (128, 128, 128)):
                    self.assertGreaterEqual(contrast(ink, self.glass_over(base, t, alpha)), 4.5, f"{name}, dekking {alpha}, onder {base}")

    def test_active_ink_on_tinted_worst_case_glass(self):
        for name, t in self.palettes.items():
            ink = rgba(t["--tabbar-ink-active"])[:3]
            for alpha in GLASS_ALPHAS:
                for base in (self.black, self.white, (128, 128, 128)):
                    tinted = over(t["--tabbar-tint"], self.glass_over(base, t, alpha))
                    self.assertGreaterEqual(contrast(ink, tinted), 4.5, f"{name}, dekking {alpha}, onder {base}")

    def test_glass_tokens_are_plain_triplets(self):
        for name, t in self.palettes.items():
            self.assertRegex(t["--tabbar-rgb"], r"^\d{1,3}, \d{1,3}, \d{1,3}$", name)

    def test_the_old_colours_would_not_have_been_enough(self):
        # Waarom de balk eigen kleuren heeft: --muted en --primary zakken boven donkere inhoud onder 4,5 : 1.
        glass = self.glass_over(self.black)
        self.assertLess(contrast(rgba(self.t["--muted"])[:3], glass), 4.5)
        self.assertLess(contrast(rgba(self.t["--primary"])[:3], over(self.t["--tabbar-tint"], glass)), 4.5)

    def test_selected_pill_is_visible_against_the_glass(self):
        glass = self.glass_over(self.white)
        tinted = over(self.t["--tabbar-tint"], glass)
        self.assertGreater(contrast(tinted, glass), 1.1, "het geselecteerde vlak valt op")


class TabbarDocsTest(AppTestCase):
    def test_style_guide_explains_the_bar_and_how_to_preview_it(self):
        guide = (ROOT / "docs" / "huisstijl.md").read_text(encoding="utf-8")
        for needle in ("onderste menubalk", "?tabbar=1", "?tabbar=0", "viewport-fit=cover"):
            self.assertIn(needle, guide, needle)


if __name__ == "__main__":
    unittest.main()
