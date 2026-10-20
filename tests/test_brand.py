import json
import os
import re
import struct
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from importlib.util import find_spec
from pathlib import Path
from unittest import mock

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

from mealieshopper.app import create_app  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
BRAND = STATIC / "brand"

SVGS = [
    "icon.svg", "mark.svg", "mark-white.svg", "mark-mono.svg",
    "wordmark.svg", "wordmark-white.svg", "wordmark-mono.svg", "favicon.svg",
]
PNG_SIZES = {
    "favicon-16.png": (16, 16), "favicon-32.png": (32, 32), "favicon-48.png": (48, 48),
    "icon-192.png": (192, 192), "icon-512.png": (512, 512), "icon-maskable-512.png": (512, 512),
    "apple-touch-icon.png": (180, 180), "social-card.png": (1200, 630), "brand-preview.png": (1600, 1180),
}


def png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", f"{path.name} is geen PNG"
    return struct.unpack(">II", data[16:24])


def ico_sizes(path: Path) -> set[tuple[int, int]]:
    data = path.read_bytes()
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    assert (reserved, kind) == (0, 1), "geen ICO"
    sizes = set()
    for i in range(count):
        width, height = data[6 + 16 * i], data[7 + 16 * i]
        sizes.add((width or 256, height or 256))
    return sizes


def css_tokens() -> dict[str, str]:
    css = (STATIC / "styles.css").read_text(encoding="utf-8")
    root = re.search(r":root\s*{(.*?)}", css, re.S).group(1)
    return {name: value.strip().lower() for name, value in re.findall(r"(--[\w-]+):\s*([^;]+);", root)}


def luminance(hex_color: str) -> float:
    channels = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


class BrandFilesTest(unittest.TestCase):
    def test_svgs_parse_and_have_a_viewbox(self):
        for name in SVGS:
            root = ET.parse(BRAND / name).getroot()
            self.assertTrue(root.tag.endswith("svg"), name)
            self.assertTrue(root.get("viewBox"), name)

    def test_marks_are_pure_shapes_and_wordmarks_use_manrope_text(self):
        for name in ("mark.svg", "mark-white.svg", "mark-mono.svg", "icon.svg", "favicon.svg"):
            self.assertNotIn("<text", (BRAND / name).read_text(), name)
        for name in ("wordmark.svg", "wordmark-white.svg", "wordmark-mono.svg"):
            svg = (BRAND / name).read_text()
            self.assertIn("<text", svg, name)
            self.assertIn("Manrope", svg, name)
            self.assertIn("MealieShopper", re.sub(r"<[^>]+>", "", svg), name)

    def test_png_sizes(self):
        for name, size in PNG_SIZES.items():
            self.assertEqual(png_size(BRAND / name), size, name)

    def test_ico_has_the_three_favicon_sizes(self):
        self.assertEqual(ico_sizes(BRAND / "favicon.ico"), {(16, 16), (32, 32), (48, 48)})

    def test_clear_colour_variants(self):
        self.assertIn("#FFFFFF", (BRAND / "mark-white.svg").read_text().upper())
        self.assertNotIn("#176B45", (BRAND / "mark-white.svg").read_text().upper())
        mono = (BRAND / "mark-mono.svg").read_text().upper()
        self.assertIn("#18251E", mono)
        self.assertNotIn("#F2CF45", mono)

    def test_fonts_and_licences_sit_together(self):
        for name in ("Manrope-Variable.woff2", "Manrope-Variable.ttf", "Inter-Variable.woff2",
                     "OFL-Manrope.txt", "OFL-Inter.txt"):
            self.assertTrue((STATIC / "fonts" / name).is_file(), name)
        for name in ("OFL-Manrope.txt", "OFL-Inter.txt"):
            self.assertIn("SIL Open Font License", (STATIC / "fonts" / name).read_text(), name)

    def test_css_font_files_exist_and_load_locally(self):
        css = (STATIC / "styles.css").read_text()
        self.assertNotIn("fonts.googleapis", css)
        self.assertNotIn("http", css.replace("http-equiv", ""))
        for path in re.findall(r'url\("([^"]+)"\)', css):
            self.assertTrue((STATIC / path).is_file(), path)


class ManifestTest(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((STATIC / "manifest.webmanifest").read_text())

    def test_installable_basics(self):
        m = self.manifest
        self.assertEqual(m["name"], "MealieShopper")
        self.assertEqual(m["display"], "standalone")
        self.assertEqual(m["start_url"], "/")
        self.assertEqual(m["scope"], "/")
        self.assertEqual(m["id"], "/")
        self.assertEqual(m["lang"], "nl")
        self.assertEqual(m["description"], "Van recept naar geregeld.")
        self.assertFalse(m["prefer_related_applications"])

    def test_colours_follow_the_tokens(self):
        tokens = css_tokens()
        self.assertEqual(self.manifest["theme_color"].lower(), tokens["--primary"])
        self.assertEqual(self.manifest["background_color"].lower(), tokens["--bg"])

    def test_icons_exist_with_the_declared_sizes(self):
        by_purpose = {}
        for icon in self.manifest["icons"]:
            path = (STATIC / icon["src"]).resolve()
            self.assertTrue(path.is_file(), icon["src"])
            self.assertEqual(path.parent, BRAND.resolve())
            if icon["type"] == "image/png":
                w, h = icon["sizes"].split("x")
                self.assertEqual(png_size(path), (int(w), int(h)), icon["src"])
            by_purpose.setdefault(icon["purpose"], []).append(icon["sizes"])
        # Android en Windows: 192 en 512 voor "any"; een apart maskable icoon (niet "any maskable")
        self.assertTrue({"192x192", "512x512"} <= set(by_purpose["any"]))
        self.assertEqual(by_purpose["maskable"], ["512x512"])
        self.assertNotIn("any maskable", by_purpose)

    def test_icons_are_opaque_where_the_platform_needs_it(self):
        # iOS maakt van een doorzichtig Apple-icoon een zwart vlak; maskable moet vol zijn.
        for name in ("icon-192.png", "icon-512.png", "icon-maskable-512.png", "apple-touch-icon.png"):
            data = (BRAND / name).read_bytes()
            color_type = data[25]
            self.assertEqual(color_type, 2, f"{name} moet RGB zijn, zonder alfakanaal")


class ServedAssetsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {
            "MEALIESHOPPER_DATA_DIR": tmp.name,
            "MEALIESHOPPER_PUBLIC_BASE_URL": "https://mealieshopper.example.nl",
        })
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = create_app().test_client()

    def test_manifest_content_type(self):
        response = self.client.get("/static/manifest.webmanifest")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/manifest+json")
        self.assertEqual(response.get_json(force=True)["short_name"], "MealieShopper")

    def test_favicon_ico_at_the_root(self):
        response = self.client.get("/favicon.ico")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data[:4], b"\x00\x00\x01\x00")

    def test_assets_work_before_login(self):
        # Browsers halen manifest, iconen en lettertypen op voordat iemand ingelogd is.
        with mock.patch.dict(os.environ, {"PASSKEY_AUTH_ENABLED": "true"}):
            for path in (
                "/static/manifest.webmanifest", "/static/brand/icon-192.png", "/static/brand/icon-512.png",
                "/static/brand/icon-maskable-512.png", "/static/brand/apple-touch-icon.png",
                "/static/brand/favicon.svg", "/static/brand/social-card.png",
                "/static/fonts/Manrope-Variable.woff2", "/static/fonts/Inter-Variable.woff2", "/favicon.ico",
            ):
                self.assertEqual(self.client.get(path).status_code, 200, path)
            self.assertEqual(self.client.get("/api/ah/lists").status_code, 401)

    def test_head_covers_ios_android_and_windows(self):
        html = self.client.get("/").get_data(as_text=True)
        expected = [
            '<meta name="theme-color" content="#176B45">',
            'rel="manifest" href="/static/manifest.webmanifest"',
            'rel="icon" href="/static/brand/favicon.svg" type="image/svg+xml"',
            'rel="icon" href="/static/brand/favicon.ico" sizes="48x48"',
            'sizes="32x32"', 'sizes="16x16"',
            'rel="apple-touch-icon" href="/static/brand/apple-touch-icon.png"',
            '<meta name="apple-mobile-web-app-capable" content="yes">',
            '<meta name="mobile-web-app-capable" content="yes">',
            '<meta name="apple-mobile-web-app-title" content="MealieShopper">',
            '<meta name="apple-mobile-web-app-status-bar-style" content="default">',
            '<meta name="msapplication-TileColor" content="#176B45">',
            'name="msapplication-TileImage" content="/static/brand/icon-192.png"',
            '<meta name="twitter:card" content="summary_large_image">',
            'rel="preload" href="/static/fonts/Inter-Variable.woff2" as="font" type="font/woff2" crossorigin',
            'rel="preload" href="/static/fonts/Manrope-Variable.woff2" as="font" type="font/woff2" crossorigin',
        ]
        for fragment in expected:
            self.assertIn(fragment, html, fragment)
        # Deelafbeelding: een absolute URL op het publieke adres
        self.assertIn(
            'property="og:image" content="https://mealieshopper.example.nl/static/brand/social-card.png"', html
        )
        self.assertIn('content="https://mealieshopper.example.nl/static/brand/social-card.png"', html)

    def test_brand_mark_replaces_the_ms_placeholder(self):
        html = self.client.get("/").get_data(as_text=True)
        self.assertNotIn(">MS<", html)
        self.assertEqual(html.count("brand/mark.svg"), 3)  # kop, inlogscherm en installatiehulp
        self.assertIn('<h1 class="wordmark">Mealie<span>Shopper</span></h1>', html)
        self.assertIn("Van recept naar geregeld.", html)


class DesignTokensTest(unittest.TestCase):
    def setUp(self):
        self.tokens = css_tokens()
        self.css = (STATIC / "styles.css").read_text()

    def test_tokens_match_the_style_guide(self):
        expected = {
            "--primary": "#176b45", "--primary-strong": "#105235", "--selection": "#eaf5ee",
            "--lemon": "#f2cf45", "--bg": "#f5f7f6", "--surface": "#ffffff", "--text": "#18251e",
            "--muted": "#59665e", "--border": "#d6dfd9", "--radius-card": "12px", "--radius-control": "8px",
        }
        for name, value in expected.items():
            self.assertEqual(self.tokens[name], value, name)

    def test_fonts_and_control_size(self):
        self.assertTrue(self.tokens["--font-body"].startswith('"inter"'))
        self.assertTrue(self.tokens["--font-heading"].startswith('"manrope"'))
        self.assertEqual(self.tokens["--control-height"], "44px")
        body = re.search(r"body\s*{(.*?)}", self.css, re.S).group(1)
        self.assertIn("font-size: 16px", body)
        rules = {
            re.sub(r"\s+", "", selector): body
            for selector, body in re.findall(r"([^{}@]+){([^{}]*)}", re.sub(r"/\*.*?\*/", "", self.css, flags=re.S))
        }
        self.assertIn("font-weight: 600", rules[".tab,.subtab,.button,.icon-button"])
        self.assertIn("min-height: var(--control-height)", rules[".button,.icon-button"])
        self.assertIn("min-height: var(--control-height)", rules[".tab,.subtab"])
        self.assertIn("font-size: 16px", rules[".input,.textarea"])

    def test_no_leftover_blue_palette(self):
        for old in ("#2563eb", "#1d4ed8", "#e8f0ff", "#1e4fbf", "#f6f7f9", "#162033", "#667085", "#d9dee8",
                    "#eef3fb", "#f4f7ff", "#526070", "#f1f4f8", "#eef1f6", "#eef1f5"):
            self.assertNotIn(old, self.css.lower(), old)

    def test_lemon_is_never_text(self):
        self.assertNotRegex(self.css, r"(?<![-\w])color:\s*(var\(--lemon\)|#f2cf45)")
        self.assertIn("decoratief", self.css)

    def test_visible_green_focus_ring(self):
        focus = re.search(r":focus-visible\s*{(.*?)}", self.css, re.S).group(1)
        self.assertIn("var(--primary)", focus)
        self.assertRegex(focus, r"outline:\s*[3-9]px solid")

    def test_contrast_meets_wcag_aa(self):
        t = self.tokens
        pairs = {
            "wit op primair (knoppen)": ("#ffffff", t["--primary"], 4.5),
            "wit op hover": ("#ffffff", t["--primary-strong"], 4.5),
            "tekst op achtergrond": (t["--text"], t["--bg"], 4.5),
            "tekst op oppervlak": (t["--text"], t["--surface"], 4.5),
            "ondersteunend op achtergrond": (t["--muted"], t["--bg"], 4.5),
            "ondersteunend op oppervlak": (t["--muted"], t["--surface"], 4.5),
            "ondersteunend op rand-/codevlak": (t["--muted"], t["--surface-muted"], 4.5),
            "link op oppervlak": (t["--primary"], t["--surface"], 4.5),
            "primair op selectievlak": (t["--primary"], t["--selection"], 4.5),
            "sterk accent op selectievlak": (t["--primary-strong"], t["--selection"], 4.5),
            "succesmelding": (t["--success-text"], t["--success-bg"], 4.5),
            "foutmelding": (t["--danger"], t["--danger-bg"], 4.5),
            "fout op oppervlak": (t["--danger"], t["--surface"], 4.5),
            "waarschuwing": (t["--warning-text"], t["--warning-bg"], 4.5),
            "groene M op citroen (beeldmerk)": (t["--primary"], t["--lemon"], 3.0),
            "wit op groen (deelafbeelding)": ("#ffffff", t["--primary"], 4.5),
            "selectietint op groen (tagline)": (t["--selection"], t["--primary"], 4.5),
        }
        for name, (fg, bg, minimum) in pairs.items():
            ratio = contrast(fg, bg)
            self.assertGreaterEqual(ratio, minimum, f"{name}: {ratio:.2f} < {minimum}")

    def test_style_guide_lists_the_same_tokens(self):
        guide = (ROOT / "docs" / "huisstijl.md").read_text().lower()
        for name in ("--primary", "--primary-strong", "--selection", "--lemon", "--bg", "--surface",
                     "--text", "--muted", "--border"):
            self.assertIn(self.tokens[name], guide, f"{name} ontbreekt of wijkt af in docs/huisstijl.md")

    def test_lemon_would_fail_as_text(self):
        # Waarom citroen decoratief blijft: als tekst op wit haalt het het minimum niet.
        self.assertLess(contrast(self.tokens["--lemon"], "#ffffff"), 3.0)


@unittest.skipUnless(find_spec("PIL"), "Pillow ontbreekt; alleen nodig voor het exporteren")
class BuildScriptTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ROOT / "scripts"))
        import build_brand

        cls.build_brand = build_brand
        cls.tmp = tempfile.TemporaryDirectory()
        cls.written = {path.name for path in build_brand.build(Path(cls.tmp.name))}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_exports_every_documented_file(self):
        expected = set(SVGS) | set(PNG_SIZES) | {"favicon.ico"}
        self.assertEqual(self.written, expected)
        self.assertEqual({p.name for p in BRAND.iterdir()}, expected, "static/brand wijkt af van het script")

    def test_committed_svgs_are_up_to_date(self):
        for name in SVGS:
            self.assertEqual(
                (Path(self.tmp.name) / name).read_text(), (BRAND / name).read_text(),
                f"{name} is verouderd; draai python scripts/build_brand.py",
            )

    def test_exported_sizes_match_the_committed_files(self):
        for name, size in PNG_SIZES.items():
            self.assertEqual(png_size(Path(self.tmp.name) / name), size, name)
        self.assertEqual(ico_sizes(Path(self.tmp.name) / "favicon.ico"), {(16, 16), (32, 32), (48, 48)})

    def test_maskable_mark_stays_inside_the_safe_circle(self):
        b = self.build_brand
        scale = 0.62  # zoals in build(): icon-maskable-512
        points = b.BODY_PTS + b.HANDLE_PTS
        reach = max(((x - 48) ** 2 + (y - 48) ** 2) ** 0.5 for x, y in points) + b.HANDLE_W / 2
        self.assertLessEqual(reach * scale, 96 * 0.40, "tasje valt buiten de veilige cirkel (80% diameter)")

    def test_wordmark_leaves_a_quarter_of_the_bag_width_free(self):
        b = self.build_brand
        bag_width = 84 - 12
        clearspace = bag_width / 4
        left = b.WORDMARK_TX + 12
        top = b.WORDMARK_TY + (12.5 - b.HANDLE_W / 2)
        bottom = b.WORDMARK_HEIGHT - (b.WORDMARK_TY + 86.5)
        right = b.wordmark_width() - (b.WORDMARK_TEXT_X + b.wordmark_text_width())
        for side, value in (("links", left), ("boven", top), ("onder", bottom), ("rechts", right)):
            self.assertGreaterEqual(value, clearspace - 0.5, f"{side}: {value:.1f} < {clearspace}")


if __name__ == "__main__":
    unittest.main()
