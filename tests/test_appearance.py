"""Instellingen, Voorkeuren: de accentkleur en de donkere achtergrond (static/theme.js, static/appearance.js, styles.css).

De accentkleur mag iemand vrij kiezen, dus moet theme.js er altijd leesbare tinten van maken. Dat rekenen we hier na,
voor de keuzes in het menu en voor een rij lastige eigen kleuren (wit, zwart, citroen, felgroen ...).
"""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from test_brand import contrast, css_tokens

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
CSS = (STATIC / "styles.css").read_text(encoding="utf-8")
THEME = (STATIC / "theme.js").read_text(encoding="utf-8")
HTML = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")

# theme.js draait hier zonder browser: een kale window en document zijn genoeg om de rekenfuncties te gebruiken.
PRELUDE = """
global.window = { matchMedia: () => ({ matches: false, addEventListener() {} }),
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} }, dispatchEvent() {} };
const classes = new Set();
const root = { attrs: {}, props: {}, getAttribute(n) { return this.attrs[n] ?? null; }, setAttribute(n, v) { this.attrs[n] = String(v); },
  classList: { toggle(n, on) { if (on) classes.add(n); else classes.delete(n); }, add(n) { classes.add(n); }, contains: (n) => classes.has(n) },
  removeAttribute(n) { delete this.attrs[n]; }, toggleAttribute(n, on) { if (on) this.attrs[n] = ""; else delete this.attrs[n]; },
  style: { setProperty(n, v) { root.props[n] = v; }, removeProperty(n) { delete root.props[n]; } } };
global.document = { documentElement: root, addEventListener() {}, querySelectorAll: () => [] };
global.CustomEvent = class { constructor(type, init) { this.type = type; this.detail = init && init.detail; } };
"""

HARD_COLOURS = ["#ffffff", "#000000", "#f2cf45", "#00ff00", "#ff0000", "#00ffff", "#0000ff", "#ff00ff", "#808080",
                "#6c5ce7", "#ffd447", "#1a1a1a", "#e6e6e6", "#176b45", "#4cc38a", "#7f7f00"]


def block(selector: str) -> dict[str, str]:
    body = re.search(re.escape(selector) + r"\s*{(.*?)\n}", CSS, re.S).group(1)
    return {name: value.strip().lower() for name, value in re.findall(r"(--[\w-]+):\s*([^;]+);", body)}


def node(program: str):
    script = PRELUDE + THEME + "\n" + program
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30, check=False)
    if result.returncode:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


class GreyBackgroundTest(unittest.TestCase):
    def setUp(self):
        self.grey = block(':root[data-theme="dark"][data-dark-bg="grey"]')
        self.green = block(':root[data-theme="dark"]')

    def test_grey_only_swaps_the_neutral_surfaces_text_and_borders(self):
        self.assertEqual(
            sorted(self.grey),
            sorted(["--bg", "--surface", "--surface-muted", "--text", "--muted", "--border", "--border-strong", "--placeholder",
                    "--tabbar-rgb", "--tabbar-ink", "--tabbar-solid", "--topbar-bg", "--gate-bg", "--topbar-glass"]),
        )
        for name in self.grey:
            self.assertIn(name, self.green, f"{name} bestaat niet in het donkere thema")

    def test_grey_is_really_grey(self):
        for name in ("--bg", "--surface", "--surface-muted", "--border"):
            r, g, b = (int(self.grey[name][i:i + 2], 16) for i in (1, 3, 5))
            self.assertLessEqual(max(r, g, b) - min(r, g, b), 6, f"{name} {self.grey[name]} is getint")

    def test_grey_reads_as_well_as_green(self):
        for palette in (self.grey, self.green):
            for surface in ("--bg", "--surface", "--surface-muted"):
                self.assertGreaterEqual(contrast(palette["--text"], palette[surface]), 7, surface)
                self.assertGreaterEqual(contrast(palette["--muted"], palette[surface]), 4.5, surface)
                self.assertGreaterEqual(contrast(palette["--placeholder"], palette[surface]), 4.5, surface)

    def test_the_samples_in_the_menu_use_the_real_backgrounds(self):
        for name, palette in (("green", self.green), ("grey", self.grey)):
            sample = re.search(r"\.appearance-sample--" + name + r"\s*{\s*background:\s*(#[0-9a-f]{6});", CSS).group(1)
            self.assertEqual(sample, palette["--bg"], name)
            card = re.search(r"\.appearance-sample--" + name + r" i:first-child\s*{\s*background:\s*(#[0-9a-f]{6});", CSS).group(1)
            self.assertEqual(card, palette["--surface-muted"], name)


class AccentCssTest(unittest.TestCase):
    def test_light_and_dark_accent_blocks_set_the_same_tokens_from_the_tints(self):
        light = block(":root[data-accent]")
        dark = block(':root[data-accent][data-theme="dark"]')
        self.assertEqual(sorted(light), sorted(dark), "het donkere blok moet alles van het lichte overschrijven")
        self.assertTrue(light["--primary"].startswith("var(--accent-light,"))
        self.assertTrue(dark["--primary"].startswith("var(--accent-dark,"))
        # Zonder tinten (dat kan niet, maar toch) valt de pagina terug op de huisstijl.
        tokens = css_tokens()
        self.assertIn(tokens["--primary"], light["--primary"])
        self.assertIn(block(':root[data-theme="dark"]')["--primary"], dark["--primary"])

    def test_statuses_never_follow_the_accent(self):
        for selector in (":root[data-accent]", ':root[data-accent][data-theme="dark"]'):
            for name in block(selector):
                self.assertFalse(re.match(r"--(success|danger|warning|info)", name), f"{selector} {name}")

    def test_the_surfaces_in_theme_js_are_the_surfaces_of_the_css(self):
        # Zo blijft de rekensom in theme.js gelijk met styles.css als iemand een vlak een andere kleur geeft.
        light = re.search(r"const LIGHT_SURFACES = \[([^\]]+)\]", THEME).group(1)
        dark = re.search(r"const DARK_SURFACES = \[([^\]]+)\]", THEME).group(1)
        tokens = css_tokens()
        for name in ("--surface", "--bg", "--surface-muted"):
            value = "#ffffff" if tokens[name] == "#fff" else tokens[name]
            self.assertIn(f'"{value}"', light, name)
        green, grey = block(':root[data-theme="dark"]'), block(':root[data-theme="dark"][data-dark-bg="grey"]')
        for palette in (green, grey):
            for name in ("--bg", "--surface", "--surface-muted"):
                self.assertIn(f'"{palette[name]}"', dark, name)
        bars = re.search(r"const DARK_BG = {([^}]+)}", THEME).group(1)
        self.assertIn(f'green: "{green["--bg"]}"', bars)
        self.assertIn(f'grey: "{grey["--bg"]}"', bars)

    def test_head_script_only_takes_valid_tints(self):
        head = HTML[:HTML.index("</head>")]
        self.assertIn('"ms-appearance"', head)
        self.assertIn("/^--accent-(light|dark)(-strong|-on|-ink)?$/", head)
        self.assertIn("names.length !== 8", head, "alle tinten of geen")
        self.assertIn("/^#[0-9a-f]{6}$/i", head)
        self.assertLess(head.index('"ms-appearance"'), HTML.index("styles.css"), "voor de stijlen, zodat er niets flitst")


@unittest.skipUnless(shutil.which("node"), "Node is niet geïnstalleerd")
class AccentTintsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        colours = json.dumps(HARD_COLOURS)
        cls.data = node(f"""
const T = window.THEME;
const out = {{ presets: T.ACCENTS, brand: T.variants(T.BRAND.light), bad: ["", "red", "#12345", null, "#12345g"].map(T.variants), tints: {{}} }};
for (const [value] of T.ACCENTS) if (value) out.tints[value] = T.variants(value);
for (const value of {colours}) out.tints[value] = T.variants(value);
T.apply({{ accent: "#0B62C4", darkBackground: "grey" }});
out.applied = {{ attrs: {{ ...root.attrs }}, props: {{ ...root.props }}, look: T.appearance() }};
T.apply({{ accent: "javascript:alert(1)", darkBackground: "black" }});
out.refused = {{ attrs: {{ ...root.attrs }}, props: {{ ...root.props }}, look: T.appearance() }};
// De navigatiebalk: Automatisch volgt het toestel, een eigen keuze niet; het glas wordt gewone CSS-waarden.
out.nav = [];
for (const [platform, style] of [["android", "auto"], ["ios", "auto"], ["other", "auto"], ["android", "ios"], ["ios", "android"]]) {{
  root.attrs["data-platform"] = platform;
  T.apply({{ navbarStyle: style }});
  out.nav.push([platform, style, root.attrs["data-nav"], T.appearance().navbarStyle]);
}}
out.glass = [];
for (const glass of [0, 60, 100, 150, -5, 50.5, "60", null]) {{
  T.apply({{ navbarGlass: glass }});
  out.glass.push([glass, T.appearance().navbarGlass, root.props["--nav-alpha"], root.props["--nav-blur"]]);
}}
T.apply({{ navbarStyle: "windows" }});
out.badStyle = [T.appearance().navbarStyle, root.attrs["data-nav"]];
// De kookmodus: standaard aan, alleen een echte false zet hem uit (en de klasse no-cook-mode op <html>).
out.cooking = [[T.appearance().cookingMode, classes.has("no-cook-mode")]];
for (const value of [false, "nee", true, 0, false, null]) {{
  T.apply({{ cookingMode: value }});
  out.cooking.push([value, T.appearance().cookingMode, classes.has("no-cook-mode")]);
}}
console.log(JSON.stringify(out));
""")
        cls.light_surfaces = [css_tokens()[name] for name in ("--surface", "--bg", "--surface-muted")]
        green, grey = block(':root[data-theme="dark"]'), block(':root[data-theme="dark"][data-dark-bg="grey"]')
        cls.dark_surfaces = [palette[name] for palette in (green, grey) for name in ("--bg", "--surface", "--surface-muted")]
        cls.dark_bgs = [green["--bg"], grey["--bg"]]

    def mix(self, a, b, share):
        x = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
        y = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
        return "#" + "".join(f"{round(p * share + q * (1 - share)):02x}" for p, q in zip(x, y))

    def test_every_tint_is_readable_on_every_surface(self):
        surfaces_light = [("#ffffff" if s == "#fff" else s) for s in self.light_surfaces]
        for colour, tints in self.data["tints"].items():
            if tints is None:
                continue
            light, dark = tints["--accent-light"], tints["--accent-dark"]
            for surface in surfaces_light + [self.mix(light, "#ffffff", 0.09)]:
                self.assertGreaterEqual(contrast(light, surface), 4.5, f"{colour}: {light} op {surface}")
                self.assertGreaterEqual(contrast(tints["--accent-light-strong"], surface), 4.5, f"{colour}: sterk op {surface}")
            for surface in self.dark_surfaces + [self.mix(dark, bg, 0.2) for bg in self.dark_bgs]:
                self.assertGreaterEqual(contrast(dark, surface), 4.5, f"{colour}: {dark} op {surface}")
            self.assertGreaterEqual(contrast(tints["--accent-light-on"], light), 4.5, f"{colour}: tekst op de knop (licht)")
            self.assertGreaterEqual(contrast(tints["--accent-light-on"], tints["--accent-light-strong"]), 4.5, f"{colour}: hover (licht)")
            self.assertGreaterEqual(contrast(tints["--accent-dark-on"], dark), 4.5, f"{colour}: tekst op de knop (donker)")
            self.assertGreaterEqual(contrast(tints["--accent-dark-on"], tints["--accent-dark-strong"]), 4.5, f"{colour}: hover (donker)")

    def test_the_active_tab_reads_on_the_glass_of_the_bottom_bar(self):
        # Zoals TabbarContrastTest in test_tabbar.py, voor elke accentkleur: glas boven een zwarte, witte en grijze foto.
        from test_tabbar import GLASS_ALPHAS, over, rgba

        light, dark = css_tokens(), block(':root[data-theme="dark"]')
        grey = block(':root[data-theme="dark"][data-dark-bg="grey"]')
        hexrgb = lambda value: rgba(value)[:3]
        ratio = lambda a, b: contrast("#%02x%02x%02x" % tuple(round(c) for c in a), "#%02x%02x%02x" % tuple(round(c) for c in b))
        for colour, tints in self.data["tints"].items():
            if tints is None:
                continue
            for alpha in GLASS_ALPHAS:  # de schuif van 0 tot 29, waar de labels boven elke foto leesbaar blijven
                for base in ((0, 0, 0), (255, 255, 255), (128, 128, 128)):
                    glass = over(f"rgba({light['--tabbar-rgb']}, {alpha})", base)
                    tinted = over(f"rgba({','.join(str(c) for c in hexrgb(tints['--accent-light']))}, 0.14)", glass)
                    self.assertGreaterEqual(ratio(hexrgb(tints["--accent-light-ink"]), tinted), 4.5, f"{colour} licht, {alpha}, boven {base}")
                    for palette in (dark, grey):
                        glass = over(f"rgba({palette['--tabbar-rgb']}, {alpha})", base)
                        tinted = over(f"rgba({','.join(str(c) for c in hexrgb(tints['--accent-dark']))}, 0.18)", glass)
                        self.assertGreaterEqual(ratio(hexrgb(tints["--accent-dark-ink"]), tinted), 4.5, f"{colour} donker, {alpha}, boven {base}")

    def test_the_android_like_bar_reads_with_every_accent(self):
        # Geen glas: een dicht vlak, 10 % naar de accentkleur. Labels 4,5 : 1, het icoon op de pil 3 : 1 (geen tekst).
        light, dark = css_tokens(), block(':root[data-theme="dark"]')
        grey = {**dark, **block(':root[data-theme="dark"][data-dark-bg="grey"]')}
        brand = {"--accent-light": light["--primary"], "--accent-light-strong": light["--primary-strong"],
                 "--accent-dark": dark["--primary"], "--accent-dark-strong": dark["--primary-strong"]}
        for colour, tints in {**self.data["tints"], "huisstijl": brand}.items():
            if tints is None:
                continue
            for name, palette, primary, strong in (("licht", light, tints["--accent-light"], tints["--accent-light-strong"]),
                                                   ("donker", dark, tints["--accent-dark"], tints["--accent-dark-strong"]),
                                                   ("donkergrijs", grey, tints["--accent-dark"], tints["--accent-dark-strong"])):
                surface = "#ffffff" if palette["--surface"] == "#fff" else palette["--surface"]
                bar = self.mix(surface, primary, 0.9)
                self.assertGreaterEqual(contrast(palette["--muted"], bar), 4.5, f"{colour}, {name}: label")
                self.assertGreaterEqual(contrast(palette["--text"], bar), 4.5, f"{colour}, {name}: actief label")
                pill = self.mix(primary, bar, 0.26)
                self.assertGreaterEqual(contrast(strong, pill), 3, f"{colour}, {name}: icoon op de pil")

    def test_automatic_follows_the_device_and_a_choice_does_not(self):
        self.assertEqual(self.data["nav"], [
            ["android", "auto", "android", "auto"],  # bewaard blijft "auto", niet wat het nu is
            ["ios", "auto", "ios", "auto"],
            ["other", "auto", "ios", "auto"],  # een computer of onbekend toestel: de iOS-achtige
            ["android", "ios", "ios", "ios"],
            ["ios", "android", "android", "android"],
        ])
        self.assertEqual(self.data["badStyle"], ["auto", "ios"])

    def test_cooking_mode_is_on_unless_it_is_switched_off(self):
        self.assertEqual(self.data["cooking"], [
            [True, False],
            [False, False, True],
            ["nee", True, False],  # geen echte false: aan
            [True, True, False],
            [0, True, False],
            [False, False, True],
            [None, True, False],
        ])

    def test_the_glass_slider_becomes_plain_values(self):
        self.assertEqual(self.data["glass"], [
            [0, 0, "1.000", "10.0px"],  # zoals applyNavGlass in 3dprint_pricing
            [60, 60, "0.490", "28.0px"],
            [100, 100, "0.150", "40.0px"],
            [150, 100, "0.150", "40.0px"],
            [-5, 0, "1.000", "10.0px"],
            [50.5, 60, "0.490", "28.0px"],  # geen geheel getal: de standaard
            ["60", 60, "0.490", "28.0px"],
            [None, 60, "0.490", "28.0px"],
        ])

    def test_the_menu_colours_need_no_adjustment_in_the_light_theme(self):
        # Het rondje in het menu is de kleur die je krijgt.
        presets = [value for value, _ in self.data["presets"] if value]
        self.assertGreaterEqual(len(presets), 8)
        self.assertEqual(len(set(presets)), len(presets))
        for value in presets:
            self.assertEqual(self.data["tints"][value]["--accent-light"], value, value)

    def test_the_first_choice_is_the_house_style(self):
        self.assertEqual(self.data["presets"][0], ["", "appearance.accent.basil"])
        self.assertIsNone(self.data["brand"], "het merkgroen zelf houdt de waarden uit styles.css")
        self.assertEqual(self.data["bad"], [None] * 5)

    def test_apply_sets_the_tints_on_html_and_refuses_bad_values(self):
        applied = self.data["applied"]
        self.assertEqual(applied["look"]["accent"], "#0b62c4")
        self.assertEqual(applied["attrs"].get("data-dark-bg"), "grey")
        self.assertIn("data-accent", applied["attrs"])
        accent = [f"--accent-{t}{s}" for t in ("light", "dark") for s in ("", "-strong", "-on", "-ink")]
        self.assertEqual(sorted(applied["props"]), sorted(accent + ["--nav-alpha", "--nav-blur"]))
        refused = self.data["refused"]
        self.assertEqual((refused["look"]["accent"], refused["look"]["darkBackground"]), ("", "green"))
        self.assertNotIn("data-accent", refused["attrs"])
        self.assertNotIn("data-dark-bg", refused["attrs"])
        self.assertEqual(sorted(refused["props"]), ["--nav-alpha", "--nav-blur"], "alleen het glas van de balk")


class PlannerPreferencesTest(unittest.TestCase):
    """Voorkeuren, Supermarkt: hoe Naar weekmenu losse producten uit de AH-weekplanner in Mealie zet (4.3.1, eerst in de planner)."""

    def test_two_real_switches_in_the_supermarket_block(self):
        store = HTML[HTML.index('id="appearance-store-box"'):HTML.index('id="appearance-display-box"')]
        self.assertIn('<section class="appearance-section fold__section" aria-labelledby="appearance-planner-title">', store)
        for name in ("combine", "split-meals"):
            switch = re.search(rf'<input type="checkbox" role="switch" class="switch" id="appearance-planner-{name}"[^>]*>', store).group(0)
            self.assertIn(" checked", switch, "standaard aan")
            self.assertIn(f'aria-labelledby="appearance-planner-{name}-label"', switch)
            self.assertIn(f'aria-describedby="appearance-planner-{name}-help"', switch)
        self.assertIn('<div class="appearance-setting" id="appearance-planner-split-row">', store)
        self.assertLess(store.index("appearance-planner-combine"), store.index("appearance-planner-split-row"))
        self.assertIn('<div id="appearance-planner-message"></div>', store)

    def test_servings_per_recipe_is_a_small_number_next_to_its_explanation(self):
        # Porties per recept (4.9.0): de voedingswaarden in een recept van producten staan per persoon.
        store = HTML[HTML.index('id="appearance-store-box"'):HTML.index('id="appearance-display-box"')]
        field = re.search(r'<input type="number"[^>]*id="appearance-planner-servings"[^>]*>', store).group(0)
        for part in ('min="1"', 'max="8"', 'step="1"', 'value="2"', 'aria-labelledby="appearance-planner-servings-label"', 'aria-describedby="appearance-planner-servings-help"'):
            self.assertIn(part, field)
        script = (STATIC / "appearance.js").read_text(encoding="utf-8")
        self.assertIn('appearanceSavePlanner("plannerServings", value)', script)
        self.assertIn("const APPEARANCE_SERVINGS = { min: 1, max: 8, fallback: 2 };", script)
        self.assertIn("plannerServings: data.plannerServings", script, "de server heeft het laatste woord")

    def test_the_switches_are_saved_in_the_profile(self):
        script = (STATIC / "appearance.js").read_text(encoding="utf-8")
        self.assertIn('appearanceSavePlanner("plannerCombine", event.target.checked)', script)
        self.assertIn('appearanceSavePlanner("plannerSplitMeals", event.target.checked)', script)
        save = script[script.index("async function appearanceSavePlanner"):script.index("function renderAppearance()")]
        self.assertIn('"/api/profile/preferences"', save)
        self.assertIn("state.auth = { ...state.auth, ...previous };", save, "een weigering zet de vorige stand terug")
        self.assertIn("renderAhPlanner()", save, "de labels in de weekplanner volgen meteen")
        self.assertIn('$("#appearance-planner-split-row").hidden = !combine.checked;', script, "soep en pizza los alleen met de eerste aan")


class AppearanceMarkupTest(unittest.TestCase):
    def panel(self):
        start = HTML.index('<div id="settings-appearance"')
        return HTML[start:HTML.index("\n      </section>", start)]

    def test_three_folding_blocks_like_link_with_the_parts_in_order(self):
        panel = self.panel()
        boxes = re.findall(r'<div class="box fold fold--compact" id="appearance-(\w+)-box">', panel)
        self.assertEqual(boxes, ["store", "display", "theme"], "Supermarkt, Weergave, dan Thema")
        titles = re.findall(r'<span data-i18n="appearance\.(\w+)\.title">([^<]+)</span>', panel)
        self.assertEqual(titles[:3], [("store", "Supermarkt"), ("display", "Weergave"), ("theme", "Thema")])
        script = (STATIC / "appearance.js").read_text(encoding="utf-8")
        self.assertIn('const APPEARANCE_FOLDS = ["store", "display", "theme"];', script)
        for name in boxes:
            toggle = re.search(rf'<button type="button" class="fold__toggle" id="appearance-{name}-toggle"[^>]*>', panel).group(0)
            self.assertIn(f'aria-controls="appearance-{name}-body"', toggle)
            self.assertIn('aria-expanded="false"', toggle, "dicht tot je hem opent")
            self.assertIn(f'<div class="fold__body" id="appearance-{name}-body" hidden>', panel)
        theme = panel[panel.index('id="appearance-theme-box"'):]
        titles = re.findall(r'<h3 id="appearance-(\w+)-title"', theme)
        self.assertEqual(titles, ["accent", "themebg", "nav"], "Accentkleur, Thema & achtergrond, Navigatiebalk")
        sections = re.findall(r'<section class="appearance-section( fold__section)?"', theme)
        self.assertEqual(sections, ["", " fold__section", " fold__section"], "een lijn tussen de onderdelen")
        prefs = panel[panel.index('id="appearance-display-box"'):panel.index('id="appearance-theme-box"')]
        switch = re.search(r'<input type="checkbox" role="switch" class="switch" id="appearance-cooking"[^>]*>', prefs).group(0)
        self.assertIn(" checked", switch, "standaard aan")
        self.assertIn('aria-labelledby="appearance-cooking-label"', switch)
        script = (STATIC / "appearance.js").read_text(encoding="utf-8")
        self.assertIn("ms-fold-appearance-", script, "open of dicht blijft per browser")
        self.assertIn("appearanceFoldStored(name) ?? false", script, "zonder keuze dicht")

    def test_compact_folds_and_a_real_switch(self):
        compact = re.search(r"\.box\.fold--compact\s*{(.*?)}", CSS, re.S).group(1)
        self.assertIn("padding: 8px 20px", compact, "dicht is het blok niet hoger dan zijn kop")
        self.assertIn('.box.fold--compact:has(> .fold__title > .fold__toggle[aria-expanded="true"])', CSS)
        switch = re.search(r"^\.switch\s*{(.*?)}", CSS, re.S | re.M).group(1)
        self.assertIn("appearance: none", switch)
        self.assertIn("var(--placeholder)", switch, "de rand haalt 3 : 1")
        self.assertRegex(CSS, r"\.switch:focus-visible\s*{\s*outline: 3px solid var\(--primary\)")

    def test_cooking_mode_off_hides_the_bar_and_the_ticks(self):
        rule = re.search(r"html\.no-cook-mode \.cook__bar,\s*html\.no-cook-mode \.cook-ingredient input,\s*html\.no-cook-mode \.cook \[data-cook-ticked\]\s*{(.*?)}", CSS, re.S)
        self.assertIsNotNone(rule)
        self.assertIn("display: none", rule.group(1))
        head = HTML[:HTML.index("</head>")]
        self.assertIn('if (look.cookingMode === false) root.classList.add("no-cook-mode")', head, "voor de eerste weergave")
        view = (STATIC / "recipe_view.js").read_text(encoding="utf-8")
        self.assertIn("return cookEnabled() && cookWakeSupported()", view, "uit: het scherm blijft niet aan")
        self.assertIn("input.disabled = !enabled;", view, "uit: een tik op een regel vinkt niets af")

    def test_the_choices_are_real_radio_buttons(self):
        panel = HTML[HTML.index('<div id="settings-appearance"'):HTML.index("\n      </section>", HTML.index('<div id="settings-appearance"'))]
        self.assertEqual(re.findall(r'name="appearance-theme" value="(\w+)"', panel), ["system", "light", "dark"])
        self.assertEqual(re.findall(r'name="appearance-dark-bg" value="(\w+)"', panel), ["green", "grey"])
        self.assertEqual(re.findall(r'name="appearance-nav" value="(\w+)"', panel), ["auto", "ios", "android"])
        self.assertEqual(re.findall(r'name="appearance-store" value="(\w+)"', panel), ["ah", "jumbo", "picnic", "crisp"])
        self.assertRegex(panel, r'name="appearance-store" value="ah" checked>', "standaard Albert Heijn")
        self.assertIn('<input type="range" id="appearance-glass" min="0" max="100" step="1" value="60"', panel)
        # Taal (sinds 4.7.0, in het blok Weergave): elke taal in die taal zelf, en geen data-lang op de rondjes, want dan zou
        # i18n.js ze als knop behandelen (aria-pressed).
        self.assertEqual(re.findall(r'name="appearance-lang" value="(\w+)"><span lang="(\w+)">', panel), [("nl", "nl"), ("en", "en")])
        self.assertNotRegex(panel, r'name="appearance-lang"[^>]*data-lang')
        display = panel[panel.index('id="appearance-display-box"'):panel.index('id="appearance-theme-box"')]
        self.assertIn('id="appearance-lang"', display)
        self.assertEqual(panel.count("<fieldset"), 6)
        self.assertEqual(panel.count("<legend"), 6)
        script = (STATIC / "appearance.js").read_text(encoding="utf-8")
        self.assertIn('I18N.setLang(event.target.value)', script)
        self.assertIn("input.checked = input.value === I18N.lang();", script)
        self.assertIn('type="radio" name="appearance-accent"', script)
        self.assertIn('type="color"', script)
        self.assertIn('"/api/profile/preferences"', script)

    def test_appearance_loads_after_the_profile_and_before_the_router(self):
        order = [HTML.index(f"filename='{name}'") for name in ("theme.js", "app.js", "profile.js", "appearance.js", "router.js")]
        self.assertEqual(order, sorted(order))


if __name__ == "__main__":
    unittest.main()
