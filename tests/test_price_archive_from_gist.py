"""scripts/price_archive_from_gist.py: het pakket van de gist omzetten naar het formaat mealieshopper-price-archive.

Wat het script schrijft, moet de app precies zo importeren als het pakket zelf; daarom gebruiken deze tests de nep-pakketten
uit test_price_archive en importeren ze de uitvoer via de echte routes.
"""
import contextlib
import gzip
import hashlib
import importlib.util
import io
import json
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_price_archive as fixtures  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("price_archive_from_gist", ROOT / "scripts" / "price_archive_from_gist.py")
convert_script = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(convert_script)
convert_script.QUIET = True
URL = "https://gist.github.com/wolph/195c8964d4e63eb1774fc14ba35b4f09"


def read_lines(path: Path) -> list[dict]:
    data = path.read_bytes()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return [json.loads(line) for line in data.splitlines() if line.strip()]


class ConvertCase(fixtures.ArchiveCase):
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.work = Path(tmp.name)

    def write(self, name: str, data: bytes) -> Path:
        path = self.work / name
        path.write_bytes(data)
        return path

    def converted(self, source, **options) -> Path:
        output = self.work / f"out-{len(list(self.work.iterdir()))}.jsonl.gz"
        convert_script.convert(str(source), output, **options)
        return output


class ConvertTest(ConvertCase):
    def test_the_output_imports_exactly_like_the_package(self):
        package = fixtures.gist_zip()
        direct = self.stage(package).get_json()
        output = self.converted(self.write("gist.zip", package))
        summary, job = self.run_import(output.read_bytes())
        self.assertEqual(summary["format"], "mealieshopper-price-archive")
        self.assertEqual(summary["stores"], direct["stores"])
        self.assertEqual(summary["corrections"], direct["corrections"])
        self.assertEqual(summary["new"], direct["new"])
        self.assertEqual((job["summary"]["offers"], job["summary"]["prices"]), (5, 4))
        self.assertIn("supermarket-archives/2026-11-06", summary["source"])
        # En het pakket zelf voegt daarna niets meer toe: het zijn dezelfde regels.
        again = self.stage(package).get_json()
        self.assertEqual(again["new"], 0)

    def test_the_header_names_the_package_and_its_checksum(self):
        archive = fixtures.tar_gz(fixtures.gist_files())
        lines = read_lines(self.converted(self.write("gist.zip", fixtures.gist_zip(archive))))
        header = lines[0]
        self.assertEqual((header["format"], header["formatVersion"]), ("mealieshopper-price-archive", 1))
        self.assertEqual(header["sourceArchiveSha256"], hashlib.sha256(archive).hexdigest())
        self.assertIn("Checkjebon", header["license"])
        self.assertEqual(sorted({line["type"] for line in lines[1:]}), ["offer", "price"])
        # Zonder --compact blijven de herkomstvelden staan.
        self.assertIn("raw_old", lines[1])

    def test_every_kind_of_source_gives_the_same_lines(self):
        archive = fixtures.tar_gz(fixtures.gist_files())
        package = fixtures.gist_zip(archive)
        unzipped = self.work / "unzipped"
        with zipfile.ZipFile(io.BytesIO(package)) as bundle:
            bundle.extractall(unzipped)
        extracted = self.work / "extracted"
        for name, data in fixtures.gist_files().items():
            (extracted / name).parent.mkdir(parents=True, exist_ok=True)
            (extracted / name).write_bytes(data)
        (extracted / "data-manifest.json").write_text(json.dumps({n: hashlib.sha256(d).hexdigest() for n, d in fixtures.gist_files().items()}))
        sources = [
            self.write("gist.zip", package),
            self.write("archive.tar.gz", archive),
            unzipped,  # de uitgepakte ZIP, met de map van de gist erin
            unzipped / fixtures.TOP,  # of die map zelf
            extracted,  # supermarket-archives/<datum> na restore_other.py
        ]
        expected = read_lines(self.converted(sources[0]))[1:]
        for source in sources[1:]:
            with self.subTest(source=source.name):
                self.assertEqual(read_lines(self.converted(source))[1:], expected)

    def test_compact_keeps_only_the_columns_and_imports_the_same(self):
        package = fixtures.gist_zip()
        direct = self.stage(package).get_json()
        lines = read_lines(self.converted(self.write("gist.zip", package), compact=True))
        offer = next(line for line in lines if line.get("type") == "offer")
        self.assertNotIn("raw_old", offer)
        self.assertLessEqual(set(offer) - {"type"}, set(convert_script.OFFER_COLUMNS))
        summary = self.stage(gzip.compress(b"".join(json.dumps(line).encode() + b"\n" for line in lines))).get_json()
        self.assertEqual(summary["new"], direct["new"])

    def test_corrections_come_along(self):
        url = f"{fixtures.FOLDER}/albert_heijn/2013/23/eerder"
        changes = json.dumps([fixtures.correction(url, (1.0, 1.5), (0.5, 0.75))]).encode()
        archive = fixtures.tar_gz(fixtures.gist_files(**{"ah-corrected/corrections.json": changes}))
        lines = read_lines(self.converted(self.write("archive.tar.gz", archive)))
        corrections = [line for line in lines if line.get("type") == "correction"]
        self.assertEqual(len(corrections), 1)
        self.assertEqual((corrections[0]["store"], corrections[0]["url"], corrections[0]["new_price"]), ("ah", url, 0.5))

    def test_a_plain_jsonl_without_gzip(self):
        output = self.work / "plain.jsonl"
        convert_script.convert(str(self.write("gist.zip", fixtures.gist_zip())), output, compress=False)
        self.assertEqual(output.read_bytes()[:1], b"{")
        self.assertEqual(self.stage(output.read_bytes()).get_json()["new"], 9)


class TidyTest(ConvertCase):
    """Opschonen: combi-aanbiedingen weg, geen prijs zonder bekende verpakking, en koppelen aan je eigen producten."""

    def rows(self, *rows: dict) -> Path:
        files = fixtures.gist_files(**{"ah-corrected/observations.jsonl": fixtures.jsonl(list(rows))})
        return self.write("archive.tar.gz", fixtures.tar_gz(files))

    def offers(self, source, **options) -> dict[str, dict]:
        lines = read_lines(self.converted(source, **options))
        return {line["url"].rsplit("/", 1)[1]: line for line in lines if line.get("type") == "offer" and line["store"] == "ah"}

    def test_a_combination_offer_is_left_out_and_can_be_kept(self):
        source = self.rows(
            fixtures.offer(1, source_title="AH Runderhamburger 2 stuks of gehaktschnitzel Hongaarse stijl 4 stuks"),
            fixtures.offer(2, source_title="Alle Ajax", description="2e halve prijs, diverse soorten"),
            fixtures.offer(3, source_title="AH Runderhamburger 4 stuks"),
        )
        self.assertEqual(sorted(self.offers(source)), ["product-3"])
        self.assertEqual(sorted(self.offers(source, keep_groups=True)), ["product-1", "product-2", "product-3"])

    def test_without_a_known_pack_the_price_is_left_out(self):
        source = self.rows(
            fixtures.offer(1, source_title="AH Runderhamburgers", description="2e gratis, 2 schalen", unit_size=None),
            fixtures.offer(2, source_title="AH Runderhamburger 4 stuks", description="voor 3.99", unit_size=None),
            fixtures.offer(3, source_title="AH Rundergehakt", description="500 gram", unit_size="500 g"),
        )
        offers = self.offers(source)
        self.assertEqual((offers["product-1"]["price"], offers["product-1"]["price_before"]), (None, None))
        self.assertIn("verpakking onbekend", offers["product-1"]["promo_label"])
        self.assertIsNotNone(offers["product-2"]["price"])  # een aantal stuks telt als verpakking
        self.assertIsNotNone(offers["product-3"]["price"])
        self.assertIsNotNone(self.offers(source, keep_unknown_prices=True)["product-1"]["price"])

    def test_a_line_linked_by_wolph_is_kept_as_it_is(self):
        source = self.rows(fixtures.offer(1, product_id="169238", title="AH Runderhamburger 2st", source_title="AH Runderhamburgers of schnitzels", unit_size=None, price=None, price_before=None))
        line = self.offers(source)["product-1"]
        self.assertEqual((line["product_id"], line["title"]), ("169238", "AH Runderhamburger 2st"))

    def database(self) -> Path:
        path = self.work / "auth.sqlite3"
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE offer_history (store TEXT, product_id TEXT, title TEXT, unit_size TEXT, url TEXT)")
            conn.execute("CREATE TABLE price_observations (store TEXT, product_id TEXT, title TEXT, unit_size TEXT, url TEXT, source TEXT)")
            conn.execute("CREATE TABLE ah_order_items (order_id INTEGER, product_id INTEGER, title TEXT, unit_size TEXT)")
            conn.execute("INSERT INTO ah_order_items VALUES (1, 8001, 'AH Biologisch Halfvolle melk', '1000 ml')")
            conn.execute("INSERT INTO offer_history VALUES ('ah', '8002', 'AH Halfvolle melk', '1500 ml', 'https://www.ah.nl/p/8002')")
            # Wat uit een archief kwam, hoort niet bij jouw producten: anders zou de exacte koppeling hier twee producten vinden.
            conn.execute("INSERT INTO offer_history VALUES ('ah', 'folder:oud', 'AH Biologisch Halfvolle melk', '1000 ml', 'https://www.supermarktaanbiedingen.com/folders/albert_heijn/2013/1/x')")
            conn.execute("INSERT INTO offer_history VALUES ('ah', '9999', 'AH Biologisch Halfvolle melk', '1000 ml', 'https://www.supermarktaanbiedingen.com/folders/albert_heijn/2013/1/y')")
            conn.execute("INSERT INTO price_observations VALUES ('ah', '9998', 'AH Biologisch Halfvolle melk', '1000 ml', '', 'public_press_release')")
        return path

    def test_lines_are_linked_to_your_own_products(self):
        source = self.rows(
            fixtures.offer(1, source_title="AH Biologisch halfvolle melk", description="1 liter", unit_size="1000 ml", price=1.29),
            fixtures.offer(2, source_title="AH Biologisch halfvolle melk pak", description="per pak", unit_size=None, price=1.29),
            fixtures.offer(3, source_title="Jumbo-only product", description="per stuk", unit_size="per stuk"),
        )
        output = self.work / "linked.jsonl.gz"
        result = convert_script.convert(str(source), output, database=self.database())
        self.assertEqual((result["tidied"]["exact"], result["tidied"]["approximate"]), (1, 1))
        offers = {line["url"].rsplit("/", 1)[1]: line for line in read_lines(output) if line.get("type") == "offer" and line["store"] == "ah"}
        exact, approximate = offers["product-1"], offers["product-2"]
        self.assertEqual((exact["product_id"], exact["title"], exact["match_kind"], exact["price"]), ("8001", "AH Biologisch halfvolle melk", "exact", 1.29))
        self.assertEqual((approximate["product_id"], approximate["match_kind"], approximate["price"]), ("8001", "approximate", None))
        self.assertIn("koppeling onzeker", approximate["promo_label"])
        self.assertTrue(offers["product-3"]["product_id"].startswith("folder:"))
        # De kop zegt wat het opschonen deed.
        self.assertEqual(read_lines(output)[0]["tidied"]["approximate"], 1)
        # En de app importeert het zo: de folderregels horen nu bij product 8001.
        summary, _ = self.run_import(output.read_bytes())
        self.assertEqual(sum(1 for row in self.rows_in("offer_history") if row["product_id"] == "8001"), 2)

    def rows_in(self, table: str) -> list[dict]:
        return fixtures.ArchiveCase.rows(self, table)

    def test_the_database_is_only_read(self):
        database = self.database()
        before = database.read_bytes()
        convert_script.convert(str(self.rows(fixtures.offer(1))), self.work / "x.jsonl.gz", database=database)
        self.assertEqual(database.read_bytes(), before)
        with self.assertRaises(convert_script.ConversionError):
            convert_script.convert(str(self.rows(fixtures.offer(1))), self.work / "y.jsonl.gz", database=self.work / "bestaat-niet.sqlite3")


class DownloadTest(ConvertCase):
    def test_the_parts_are_fetched_from_the_gist_by_name(self):
        package = fixtures.gist_zip()
        with zipfile.ZipFile(io.BytesIO(package)) as bundle:
            files = {Path(name).name: bundle.read(name) for name in bundle.namelist()}
        asked = []

        def download(url: str) -> bytes:
            asked.append(url)
            return files[url.rsplit("/", 1)[1]]

        output = self.work / "url.jsonl.gz"
        convert_script.convert(URL, output, download=download)
        base = "https://gist.githubusercontent.com/wolph/195c8964d4e63eb1774fc14ba35b4f09/raw/"
        self.assertEqual(asked[0], base + "supermarket-manifest.json")
        self.assertTrue(all(url.startswith(base + "supermarket-archive.part-") for url in asked[1:]))
        self.assertNotIn(base + "restore_other.py", asked)  # alleen het manifest en de delen
        self.assertEqual(read_lines(output)[1:], read_lines(self.converted(self.write("gist.zip", package)))[1:])

    def test_an_address_without_the_user_is_refused(self):
        with self.assertRaises(convert_script.ConversionError):
            convert_script.convert("https://gist.github.com/195c8964d4e63eb1774fc14ba35b4f09", self.work / "x.jsonl.gz", download=lambda url: b"")
        with self.assertRaises(convert_script.ConversionError):
            convert_script.convert("https://example.com/wolph/195c8964d4e63eb1774fc14ba35b4f09", self.work / "x.jsonl.gz", download=lambda url: b"")


class RefusedTest(ConvertCase):
    def assert_refused(self, source) -> str:
        output = self.work / "refused.jsonl.gz"
        with self.assertRaises(convert_script.ConversionError) as caught:
            convert_script.convert(str(source), output)
        self.assertEqual([p.name for p in self.work.iterdir() if p.name.startswith("refused")], [])  # ook geen .tmp
        return str(caught.exception)

    def test_damaged_packages(self):
        def bad_part(manifest, parts):
            name = next(iter(parts))
            parts[name] = b"A" + parts[name][1:]
        self.assertIn("controlesom", self.assert_refused(self.write("a.zip", fixtures.gist_zip(tamper=bad_part))))

        def path_in_name(manifest, parts):
            manifest["parts"][0]["file"] = "../x.b64"
        self.assert_refused(self.write("b.zip", fixtures.gist_zip(tamper=path_in_name)))

        files = fixtures.gist_files()
        honest = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
        edited = dict(files, **{"jumbo/reports/observations.jsonl": fixtures.jsonl([fixtures.offer(3, "jumbo", price=0.01)])})
        message = self.assert_refused(self.write("c.tar.gz", fixtures.tar_gz(edited, manifest=honest)))
        self.assertIn("jumbo/reports/observations.jsonl", message)

        incomplete = dict(files)
        incomplete.pop("crisp/reports/observations.jsonl")
        self.assertIn("mist", self.assert_refused(self.write("d.tar.gz", fixtures.tar_gz(incomplete))))

    def test_files_that_are_not_a_package(self):
        self.assert_refused(self.write("e.txt", b"geen pakket"))
        self.assert_refused(self.work / "bestaat-niet.zip")
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as bundle:
            bundle.writestr(f"{fixtures.TOP}/manifest.json", json.dumps({"format": "ah-bonus-archive-base64-parts"}))
        self.assertIn("supermarket-manifest.json", self.assert_refused(self.write("old.zip", out.getvalue())))

    def test_the_command_line_reports_the_error_and_the_result(self):
        stderr, stdout = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.assertEqual(convert_script.main([str(self.write("f.txt", b"nee")), "-o", str(self.work / "f.jsonl.gz"), "-q"]), 1)
        self.assertIn("Fout:", stderr.getvalue())
        with contextlib.redirect_stdout(stdout):
            code = convert_script.main([str(self.write("gist.zip", fixtures.gist_zip())), "-o", str(self.work / "g.jsonl.gz"), "-q"])
        self.assertEqual(code, 0)
        self.assertIn("5 aanbiedingen, 4 prijzen en 0 correcties", stdout.getvalue())


if __name__ == "__main__":
    import unittest

    unittest.main()
