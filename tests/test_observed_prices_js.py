"""Execute the observed-price chart and table renderers in Node."""

import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT: Path = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("node"), "Node is not installed")
class ObservedPricesJavascriptTest(unittest.TestCase):
    def render(self, product: dict) -> dict:
        source: str = (ROOT / "static/offers_history.js").read_text().split('$("#offers-history-search").addEventListener')[0]
        prelude: str = """
const escapeHtml = (value) => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('"', '&quot;');
const t = (key, values = {}) => key + ' ' + JSON.stringify(values);
const tn = (key, count) => key + ' ' + count;
const I18N = {locale: () => 'en-GB', formatNumber: String};
const STORES = {picnic: {name: 'Picnic'}};
const $ = () => ({clientWidth: 640});
const storeImage = (url) => url;
const offerPerUnitText = () => '';
const purchasePath = () => '';
"""
        script: str = prelude + source + "\nconst product = " + json.dumps(product) + ";\n" + """
console.log(JSON.stringify({
    chart: offerHistoryChart(product, 375),
    legend: offerHistoryLegend(product),
    detail: offerHistoryDetail(product)
}));
"""
        result: subprocess.CompletedProcess = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
        return json.loads(result.stdout)

    def test_unknown_only_prices_render_as_distinct_points_and_a_sourced_table(self) -> None:
        product: dict = {
            "store": "picnic", "productId": "milk", "title": "Milk", "episodes": [], "regular": [], "purchases": [],
            "observed": [{"date": "2024-01-01", "price": 1.29, "unitSize": "1 l", "sourceUrl": "https://example.org/archive?x=1&y=2"}],
        }
        result: dict = self.render(product)
        self.assertIn('class="ohc-observed"', result["chart"])
        self.assertNotIn('class="ohc-paid"', result["chart"])
        self.assertNotIn('class="ohc-regular"', result["chart"])
        self.assertIn("2024", result["chart"])
        self.assertIn("ohc-key--observed", result["legend"])
        self.assertIn("offers.history.table.observed", result["detail"])
        self.assertIn("offers.history.observed_note", result["detail"])
        self.assertIn("offers.history.no_confirmed_offer", result["detail"])
        self.assertNotIn("offers.history.not_now", result["detail"])
        self.assertIn('href="https://example.org/archive?x=1&amp;y=2"', result["detail"])
        self.assertIn("1 l", result["detail"])
        self.assertNotIn("offers.history.table.purchases", result["detail"])
        self.assertNotIn("offers.history.table.offers", result["detail"])

    def test_source_links_allow_only_http_and_https(self) -> None:
        product: dict = {"store": "picnic", "productId": "milk", "title": "Milk", "observed": [
            {"date": "2024-01-01", "price": 1.29, "unitSize": "<pack>", "sourceUrl": 'javascript:alert(1)'},
        ]}
        result: dict = self.render(product)
        self.assertNotIn('href="javascript:', result["detail"])
        self.assertIn("&lt;pack>", result["detail"])
