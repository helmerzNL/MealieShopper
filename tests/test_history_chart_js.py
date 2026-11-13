"""De grafiek van de Prijsgeschiedenis in Node, met een product zoals Dr. Oetker Casa di mama pizza salami (514010).

Gemeld op 2026-11-09: "ik zie nogsteeds geen andere prijzen behalve de huidige hier ... ik weet zeker dat ik deze ook meermaals
besteld heb dus ook dat mist hierbij. de prijsgrafiek is ook stuk nu." De folderacties sinds 2009 rekten de grafiek op tot
zeventien jaar: zeven aankopen, de gewone prijs en de aanbieding van nu stonden samen in de laatste pixels, en de folderacties
waren streepjes zonder prijs.
"""

import json
import re
import shutil
import subprocess
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT: Path = Path(__file__).resolve().parents[1]


def days(n: int) -> str:
    return (date.today() + timedelta(days=n)).isoformat()


def salami() -> dict:
    """De pizza zoals /api/prices/history hem geeft: een eigen aanbieding nu, zeven aankopen sinds ruim twee jaar, de gewone prijs
    uit die aankopen, en folderacties voor de hele lijn sinds 2009 (per stuk, line_offers.per_piece)."""
    purchases: list[dict] = [
        {
            "date": days(back),
            "price": price,
            "quantity": 2,
            "regularPrice": 4.29,
            "source": "import",
            "ref": str(back),
        }
        for back, price in (
            (-858, 3.0),
            (-767, 3.0),
            (-578, 3.0),
            (-424, 4.29),
            (-69, 2.15),
            (-65, 2.15),
            (-11, 4.29),
        )
    ]
    lines: list[dict] = [
        {
            "start": "2009-10-26",
            "end": "2009-11-01",
            "price": 1.75,
            "priceBefore": 2.69,
            "title": "Folder: Alle Dr. Oetker Casa di Mama pizza's",
            "promoLabel": "2 dozen, actie 3.50, was 2 stuks 5.38",
        },
        {
            "start": "2016-11-14",
            "end": "2016-11-20",
            "price": 2.0,
            "priceBefore": 2.89,
            "title": "Folder: Dr. Oetker Casa di Mama",
            "promoLabel": "2 voor: 4.00",
        },
        {
            "start": days(-861),
            "end": days(-855),
            "price": 3.0,
            "priceBefore": 4.29,
            "title": "Folder: Alle Dr. Oetker Casa di Mama en Tradizionale",
            "promoLabel": "2 voor: 6.00, was 8.58",
        },
        {
            "start": days(-70),
            "end": days(-64),
            "price": 2.15,
            "priceBefore": 4.29,
            "title": "Folder: Alle Dr. Oetker Casa di Mama",
            "promoLabel": "1+1 gratis",
        },
        {
            "start": days(-14),
            "end": days(-8),
            "price": None,
            "priceBefore": None,
            "title": "Folder: Alle Dr. Oetker Casa di Mama",
            "promoLabel": "tekst zonder prijs",
        },
    ]
    return {
        "store": "ah",
        "productId": "514010",
        "title": "Dr. Oetker Casa di mama pizza salami",
        "brand": "Dr. Oetker",
        "unitSize": "390 g",
        "episodes": [
            {
                "start": days(0),
                "end": days(6),
                "open": False,
                "price": 3.65,
                "priceBefore": 4.29,
                "promoLabel": "Bonus Box 15% korting",
                "personal": True,
            }
        ],
        "timesOnOffer": 1,
        "lowestPrice": 3.65,
        "regularPrice": 4.29,
        "regular": [{"from": days(-858), "to": days(0), "price": 4.29}],
        "purchases": purchases,
        "timesBought": len(purchases),
        "observed": [],
        "lineOffers": lines,
        "timesInLineOffer": len(lines),
    }


@unittest.skipUnless(shutil.which("node"), "Node is not installed")
class HistoryChartTest(unittest.TestCase):
    def render(self, product: dict, range_: str | None = None, offer: dict | None = None) -> dict:
        shown: dict = offer or {
            "price": 3.65,
            "priceBefore": 4.29,
            "promoLabel": "Bonus Box 15% korting",
        }
        source: str = (ROOT / "static/offers_history.js").read_text().split('$("#offers-history-search").addEventListener')[0]
        prelude: str = """
const escapeHtml = (value) => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('"', '&quot;');
const t = (key, values = {}) => key + ' ' + JSON.stringify(values);
const tn = (key, count) => key + ' ' + count;
const I18N = {locale: () => 'nl-NL', formatNumber: String};
const STORES = {ah: {name: 'Albert Heijn'}};
const storeDisplayName = (key) => STORES[key].name;
const offersOnlyStore = () => false;
const $ = () => ({clientWidth: 640});
const storeImage = (url) => url;
const offerPerUnitText = () => '';
const purchasePath = () => '';
const facetSelection = () => ({shown: [], hidden: []});
"""
        script: str = (
            prelude
            + source
            + "\nconst product = "
            + json.dumps(product)
            + ";\n"
            + f"""
console.log(JSON.stringify({{
    chart: offerHistoryChart(product, 640, {json.dumps(range_)} ?? undefined),
    block: offerHistoryChartBlock(product),
    detail: offerHistoryDetail(product),
    head: offerHistoryHead(product),
    headCompact: offerHistoryHead(product, {{ compact: true }}),
    stats: offerHistoryStats(product),
    purchases8: offerHistoryPurchasesTable(product, {{ limit: 8 }}),
    purchasesAll: offerHistoryPurchasesTable(product, {{}}),
    blockPopup: offerHistoryChartBlock(product, {{ width: 600, ranges: {{ "ah:514010": "1y" }} }}),
    headOffer: offerHistoryHead(product, {{ compact: true, offer: {json.dumps(shown)} }}),
}}));
"""
        )
        result: subprocess.CompletedProcess = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
        return json.loads(result.stdout)

    @staticmethod
    def xs(svg: str, kind: str) -> list[float]:
        if kind == "paid":
            return [float(value) for value in re.findall(r'class="ohc-paid".*?<circle cx="([\d.]+)"', svg)]
        return [float(value) for value in re.findall(rf'class="{kind}[^"]*".*?<rect x="([\d.]+)"', svg)]

    def test_2026_11_09_the_chart_shows_the_period_of_the_product_itself(self) -> None:
        chart: str = self.render(salami())["chart"]
        self.assertNotIn("2009", chart, "standaard niet zeventien jaar folderarchief")
        self.assertEqual(len(self.xs(chart, "paid")), 7, "alle aankopen staan erin")
        paid: list[float] = self.xs(chart, "paid")
        self.assertLess(
            min(paid),
            640 / 3,
            "de oudste aankoop staat links, niet in de laatste pixels",
        )
        self.assertGreater(max(paid) - min(paid), 640 * 0.6)

    def test_2026_11_09_line_offers_with_a_price_are_bars_on_that_price(self) -> None:
        chart: str = self.render(salami())["chart"]
        self.assertEqual(
            len(re.findall(r'class="ohc-line ohc-line--priced"', chart)),
            2,
            "de twee met een prijs in de laatste drie jaar",
        )
        self.assertEqual(
            len(re.findall(r'class="ohc-line"', chart)),
            1,
            "zonder prijs: een streepje onderaan",
        )
        self.assertIn("offers.history.line_price", chart)

    def test_2026_11_09_all_shows_the_whole_archive(self) -> None:
        chart: str = self.render(salami(), "all")["chart"]
        self.assertIn("2009", chart)
        self.assertEqual(len(re.findall(r'class="ohc-line', chart)), 5)

    def test_2026_11_09_range_buttons_and_the_default_choice(self) -> None:
        block: str = self.render(salami())["block"]
        self.assertEqual(
            re.findall(r'data-range="(\w+)" aria-pressed="(\w+)"', block),
            [("1y", "false"), ("3y", "true"), ("all", "false")],
        )

    def test_2026_11_09_times_on_offer_counts_the_leaflet_offers(self) -> None:
        detail: str = self.render(salami())["detail"]
        self.assertIn("<dt>offers.history.stat.times {}</dt><dd>6</dd>", detail)
        self.assertIn("offers.history.stat.line_lowest", detail)
        self.assertIn("offers.history.table.price_each", detail)

    def test_the_regular_price_stat_names_its_source_and_date(self) -> None:
        product: dict = {**salami(), "regularSince": "2026-10-03", "regularSource": "import"}
        stats: str = self.render(product)["stats"]
        self.assertIn("<dt>offers.history.stat.regular {}</dt>", stats)
        self.assertIn("offers.history.stat.regular_from", stats)
        self.assertIn("offers.regular.source.import", stats)
        self.assertIn("2026", stats.split("regular_from")[1])
        # Dezelfde prijs uit twee bronnen op twee dagen: de bron is die van de nieuwste dag, en de regel noemt ook die dag.
        run: dict = {
            **salami(),
            "regularSince": "2026-09-01",
            "regularSource": "receipt",
            "regular": [{"from": "2026-09-01", "to": "2026-09-20", "price": 4.29}],
        }
        line: str = self.render(run)["stats"].split("regular_from")[1]
        self.assertIn("20", line.split("</small>")[0])
        self.assertNotIn("1 sep", line.split("</small>")[0])
        bare: str = self.render({**salami(), "regularSource": None})["stats"]
        self.assertNotIn("regular_from", bare)

    def test_the_detail_is_built_from_head_stats_chart_and_purchases(self) -> None:
        found: dict = self.render(salami())
        for part in ("head", "stats", "block", "purchasesAll"):
            self.assertIn(found[part].strip(), found["detail"], part)
        self.assertIn('class="ohc-head ohc-head--compact"', found["headCompact"])
        self.assertNotIn("data-compare", found["headCompact"], "de popup heeft geen vergelijkknop")
        self.assertTrue(found["stats"].startswith('<dl class="ohc-stats">'))

    def test_the_purchases_table_takes_a_limit_and_says_how_many_more(self) -> None:
        product: dict = salami()
        product["purchases"] = [{**product["purchases"][0], "ref": str(n), "date": days(-n)} for n in range(1, 12)]
        product["timesBought"] = 11
        found: dict = self.render(product)
        self.assertEqual(found["purchases8"].count("<tr><td>"), 8)
        self.assertIn("offers.history.purchases_more", found["purchases8"])
        self.assertEqual(found["purchasesAll"].count("<tr><td>"), 11)
        self.assertNotIn("offers.history.purchases_more", found["purchasesAll"])

    def test_the_chart_block_reads_its_choice_from_the_given_map(self) -> None:
        found: dict = self.render(salami())
        self.assertIn('data-range="1y" aria-pressed="true"', found["blockPopup"])
        self.assertIn('data-ranges="popup"', found["blockPopup"])
        self.assertNotIn(
            'data-ranges="popup"',
            found["block"],
            "de pagina zelf leest offerHistory.ranges",
        )

    def test_the_compact_head_shows_the_offer_price_line(self) -> None:
        found: dict = self.render(salami())
        self.assertIn('class="ohc-head__price"', found["headOffer"])
        for part in ("€3,65", "<s>€4,29</s>", "Bonus Box 15% korting"):
            self.assertIn(part, found["headOffer"])
        self.assertNotIn("ohc-head__price", found["headCompact"], "zonder aanbieding geen prijsregel")

    def test_the_compact_head_strikes_through_an_old_price_only_when_it_is_fresh(self) -> None:
        # Eindreview 2026-11-12: de kop van de popup streepte de gewone prijs door, ook een bon van 300 dagen terug. Alleen bij een
        # verse meting of aankoop (`history`), anders de prijs ervoor van de winkel.
        offer: dict = {"price": 3.65, "priceBefore": 4.29, "promoLabel": "Bonus Box 15% korting"}
        dated: dict = self.render(salami(), offer={**offer, "badge": {"source": "dated"}, "verdict": {"lowestRegular": 4.99}})
        fresh: dict = self.render(salami(), offer={**offer, "badge": {"source": "history"}, "verdict": {"lowestRegular": 3.99}})
        self.assertIn("<s>€4,29</s>", dated["headOffer"])
        self.assertNotIn("4,99", dated["headOffer"])
        self.assertIn("<s>€3,99</s>", fresh["headOffer"])

    def test_without_older_data_there_are_no_buttons(self) -> None:
        product: dict = salami()
        product.update(purchases=[], lineOffers=[], timesInLineOffer=0, regular=[])
        self.assertNotIn("data-range", self.render(product)["block"])


if __name__ == "__main__":
    unittest.main()
