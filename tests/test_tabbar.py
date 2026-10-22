import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

from mealieshopper.app import create_app  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CSS = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")


def tokens() -> dict[str, str]:
    root = re.search(r":root\s*{(.*?)\n}", CSS, re.S).group(1)
    return {name: value.strip() for name, value in re.findall(r"(--[\w-]+):\s*([^;]+);", root)}


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


class TabbarMarkupTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"MEALIESHOPPER_DATA_DIR": tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.html = create_app().test_client().get("/").get_data(as_text=True)
        self.nav = re.search(r'<nav class="tabs".*?</nav>', self.html, re.S).group(0)

    def test_viewport_reaches_under_the_home_indicator(self):
        self.assertIn("viewport-fit=cover", self.html)

    def test_seven_tabs_with_icon_long_and_short_label(self):
        buttons = re.findall(r'<button class="tab[^"]*" data-tab="([\w-]+)".*?</button>', self.nav, re.S)
        self.assertEqual(buttons, ["planner", "search", "url", "ah-items", "basket", "beheer", "about"])
        self.assertEqual(self.nav.count('class="tab-icon"'), 7)
        self.assertEqual(self.nav.count('aria-hidden="true"'), 14, "icoon en kort label worden niet voorgelezen")
        short = re.findall(r'<span class="tab-short"[^>]*>([^<]+)</span>', self.nav)
        self.assertEqual(short, ["Menu", "Zoeken", "Import", "Winkels", "Mandje", "Beheer", "Over"])
        long = re.findall(r'<span class="tab-text"[^>]*>([^<]+)</span>', self.nav)
        self.assertEqual(long, ["Weekmenu naar winkel", "Recepten zoeken", "URL importeren", "Winkels", "Mandje", "Beheer", "Over"])

    def test_icons_are_material_design_icons_with_the_text_colour(self):
        svgs = re.findall(r'<svg class="tab-icon".*?</svg>', self.nav, re.S)
        self.assertEqual(
            [re.search(r'data-mdi="(\w+)"', s).group(1) for s in svgs],
            ["mdiCalendarWeek", "mdiMagnify", "mdiLinkVariant", "mdiBasket", "mdiCart", "mdiCog", "mdiInformationOutline"],
        )
        for svg in svgs:
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

    def test_current_page_is_announced(self):
        self.assertEqual(self.nav.count('aria-current="page"'), 1)
        self.assertRegex(self.nav, r'data-tab="planner" aria-current="page"')

    def test_head_script_decides_when_the_bar_shows(self):
        script = re.search(r"<script>(.*?)</script>", self.html, re.S).group(1)
        for needle in ("has-tabbar", "navigator.standalone", "(display-mode: standalone)", "tabbar", "sessionStorage"):
            self.assertIn(needle, script, needle)
        self.assertIn("try {", script, "geblokkeerde opslag mag de pagina niet breken")

    def test_app_js_keeps_aria_current_in_sync_and_scrolls_to_top(self):
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        handler = js[js.index('document.querySelectorAll(".tab").forEach'):js.index('document.querySelectorAll(".subtab[data-subtab]')]
        self.assertIn('removeAttribute("aria-current")', handler)
        self.assertIn('setAttribute("aria-current", "page")', handler)
        self.assertIn("window.scrollTo({ top: 0 })", handler)


class TabbarStyleTest(unittest.TestCase):
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
        for declaration in ("position: fixed", "grid-template-columns: repeat(7", "border-radius: 32px",
                            "bottom: max(12px, env(safe-area-inset-bottom))",
                            "left: max(12px, env(safe-area-inset-left))",
                            "right: max(12px, env(safe-area-inset-right))"):
            self.assertIn(declaration, bar, declaration)
        self.assertIn("backdrop-filter: saturate(180%) blur(24px)", self.media)
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
        padding = int(re.search(r"calc\((\d+)px", shell).group(1))
        self.assertGreaterEqual(padding, 56 + 12 + 12 + 12, "ruimte voor balk, marge en adem")

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


class TabbarContrastTest(unittest.TestCase):
    """Labels moeten leesbaar blijven, ook als er een donkere foto onder het glas ligt."""

    def setUp(self):
        self.t = tokens()
        self.glass_over = lambda base: over(self.t["--tabbar-glass"], base)
        self.black, self.white = (0, 0, 0), (255, 255, 255)

    def test_inactive_ink_on_worst_case_glass(self):
        ink = rgba(self.t["--tabbar-ink"])[:3]
        for base in (self.black, self.white, (128, 128, 128)):
            self.assertGreaterEqual(contrast(ink, self.glass_over(base)), 4.5, f"onder {base}")

    def test_active_ink_on_tinted_worst_case_glass(self):
        ink = rgba(self.t["--tabbar-ink-active"])[:3]
        for base in (self.black, self.white, (128, 128, 128)):
            tinted = over(self.t["--tabbar-tint"], self.glass_over(base))
            self.assertGreaterEqual(contrast(ink, tinted), 4.5, f"onder {base}")

    def test_the_old_colours_would_not_have_been_enough(self):
        # Waarom de balk eigen kleuren heeft: --muted en --primary zakken boven donkere inhoud onder 4,5 : 1.
        glass = self.glass_over(self.black)
        self.assertLess(contrast(rgba(self.t["--muted"])[:3], glass), 4.5)
        self.assertLess(contrast(rgba(self.t["--primary"])[:3], over(self.t["--tabbar-tint"], glass)), 4.5)

    def test_selected_pill_is_visible_against_the_glass(self):
        glass = self.glass_over(self.white)
        tinted = over(self.t["--tabbar-tint"], glass)
        self.assertGreater(contrast(tinted, glass), 1.1, "het geselecteerde vlak valt op")


class TabbarDocsTest(unittest.TestCase):
    def test_style_guide_explains_the_bar_and_how_to_preview_it(self):
        guide = (ROOT / "docs" / "huisstijl.md").read_text(encoding="utf-8")
        for needle in ("onderste menubalk", "?tabbar=1", "?tabbar=0", "viewport-fit=cover"):
            self.assertIn(needle, guide, needle)


if __name__ == "__main__":
    unittest.main()
