"""Winkelfoto's in de interface gaan via de eigen cache: storeImage(url) in static/app.js (zie image_cache.py)."""

import re
import unittest
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "static"


def source(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


class StoreImageTest(unittest.TestCase):
    def test_store_image_turns_a_store_address_into_the_cache_route(self):
        app = source("app.js")
        self.assertIn("function storeImage(url)", app)
        self.assertIn("return `/api/images/${token}`", app)
        for host in (r"static\.ah\.nl", r"jumbo\.com", r"picnicinternational\.com", r"crispapp\.nl", r"www\.lidl\.nl",
                     r"imgproxy-retcat\.assets\.schwarz", r"s7g10\.scene7\.com"):
            self.assertIn(host, app)

    def test_every_store_image_goes_through_the_cache(self):
        # Elke <img src> met een product- of aanbiedingsfoto van een winkel gebruikt storeImage(...).
        wanted = {
            "orders.js": ['src="${escapeHtml(storeImage(line.image))}"'],
            "order_views.js": ['src="${escapeHtml(storeImage(line.image))}"'],
            "offers.js": [
                'src="${escapeHtml(storeImage(offer.image))}"',
                'src="${escapeHtml(storeImage(recipe.image))}"',
                "image.src = storeImage(offer.image)",
            ],
            "offers_history.js": ['src="${escapeHtml(storeImage(image))}"'],
            "picnic.js": [
                'src="${escapeHtml(storeImage(product.image))}"',
                'src="${escapeHtml(storeImage(item.image))}"',
            ],
            "crisp.js": [
                'src="${escapeHtml(storeImage(product.image))}"',
                'src="${escapeHtml(storeImage(item.image))}"',
            ],
            "recipe_search.js": ['src="${escapeHtml(storeImage(recipe.image))}"'],
            "app.js": [
                'src="${escapeHtml(storeImage(product.image))}"',
                'src="${escapeHtml(storeImage(image))}"',
            ],
            # De controlelijst van het weekmenu (sinds 4.11.0 een eigen script): de foto van het gekozen product, de foto's bij de
            # opties en de foto in het groot.
            "shopping.js": [
                'src="${escapeHtml(storeImage(candidate.image))}"',
                "image.src = storeImage(large)",
            ],
        }
        for script, needles in wanted.items():
            text = source(script)
            for needle in needles:
                self.assertIn(needle, text, f"{script}: {needle}")

    def test_no_store_image_is_hotlinked_any_more(self):
        # Een <img src="${escapeHtml(x.image)}"> zonder storeImage is een foto die rechtstreeks van de winkel komt.
        # (De bewaarde afbeeldingen van de prijsgeschiedenis, image.url, komen al van de eigen server: /api/prices/images.)
        pattern = re.compile(r'<img[^>]*src="\$\{escapeHtml\((?!storeImage\()[^)]*\.image\)\}"')
        for script in (
            "orders.js",
            "offers.js",
            "offers_history.js",
            "picnic.js",
            "crisp.js",
            "recipe_search.js",
            "jumbo.js",
            "order_views.js",
        ):
            self.assertEqual(pattern.findall(source(script)), [], script)

    def test_the_system_page_shows_the_cache_line(self):
        system = source("system.js")
        self.assertIn('jsonFetch("/api/system/images")', system)
        self.assertIn('t("system.images_line"', system)
        self.assertIn("loadImageCacheStatus()", source("app.js"))
        self.assertIn('id="system-images"', (STATIC.parent / "templates" / "index.html").read_text(encoding="utf-8"))

    def test_the_system_page_shows_a_paused_store(self):
        # #236: een winkel in pauze gaf geen enkele foto, zonder dat je kon zien waarom.
        system = source("system.js")
        self.assertIn('t("system.images_paused"', system)
        self.assertIn("stats.paused", system)
        self.assertIn('id="system-images-paused"', (STATIC.parent / "templates" / "index.html").read_text(encoding="utf-8"))

    def test_every_store_image_hides_itself_when_it_fails(self):
        for script in (
            "orders.js",
            "app.js",
            "offers.js",
            "offers_history.js",
            "picnic.js",
            "crisp.js",
            "recipe_search.js",
            "order_views.js",
        ):
            for line in source(script).splitlines():
                if "storeImage(" in line and "<img" in line:
                    self.assertIn(
                        "data-hide-on-error", line, f"{script}: {line.strip()[:90]}"
                    )


if __name__ == "__main__":
    unittest.main()
