"""data.mealieshopper.nl en het prijsarchief: de gist voor de site importeren, exporteren voor de site en Aanbiedingen ophalen.

De import leest de vorm van de site (price_archive.py: `site_document`, `_from_site`, `_from_site_gist`, `fetch_site`), de export
maakt hem (price_archive_export.py: `write_site_export`, `site_schema`). De regels van de site zelf (scripts/datastore.py in
helmerzNL/data.mealieshopper.nl) staan hieronder nagebouwd, zodat een export daar zonder fouten door de controle komt."""
import asyncio
import base64
import hashlib
import io
import json
import re
import zipfile
from contextlib import closing
from typing import Any, Callable

import httpx
import jsonschema

from helpers import routes  # noqa: E402
from mealieshopper import about, price_archive, price_archive_export, price_archive_tidy  # noqa: E402
from test_price_archive import TOP, ArchiveCase, by_store, offer, price  # noqa: E402

SITE = "https://data.mealieshopper.nl"
# De regels van data.mealieshopper.nl (scripts/datastore.py): namen, grootte en sleutels die op een geheim wijzen.
COLLECTION_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SITE_MAX_DOCUMENT = 10 * 1024 * 1024
SENSITIVE = ("password", "secret", "token", "apikey", "privatekey", "cookie", "session", "auth")


def site_doc(rows: list[Any]) -> bytes:
    return (json.dumps(rows, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def plain_zip(files: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name, data in files.items():
            bundle.writestr(name, data)
    return out.getvalue()


def gist_offer(n: int, store: str = "ah", **changes) -> dict:
    """Een regel zoals in de gist voor de site: de kolommen van de app plus de velden die de app niet bewaart."""
    return offer(n, store, **{"description": "voor 1.00", "reason": "", **changes})


def default_packages() -> dict[str, dict[str, bytes]]:
    return {
        "ah": {"offers/2013-001.json": site_doc([gist_offer(1), gist_offer(2, product_id="4183", title="AH Bloemkool", match_kind="exact")])},
        "jumbo": {"offers/2013-001.json": site_doc([gist_offer(3, "jumbo")])},
    }


def site_gist_zip(
    packages: dict[str, dict[str, bytes]] | None = None, part_size: int = 400, top: str = TOP,
    tamper: Callable[[dict, dict[str, bytes]], None] | None = None,
) -> bytes:
    """De gist voor data.mealieshopper.nl zoals Download ZIP hem geeft: data-site-manifest.json en per winkel Base64-delen van de
    ZIP die je op de site instuurt, met het schema en restore_data.py erbij."""
    packages = packages if packages is not None else default_packages()
    parts: dict[str, bytes] = {}
    listed = []
    for store, documents in packages.items():
        inner = plain_zip(documents)
        encoded = base64.b64encode(inner)
        names = []
        for index, at in enumerate(range(0, len(encoded), part_size), 1):
            name = f"data-site-{store}.part-{index:03d}.b64"
            parts[name] = encoded[at:at + part_size] + b"\n"
            names.append(name)
        listed.append({
            "store": store, "title": store.upper(), "uploadPath": f"data/{store}/{store}-history-20261107.zip",
            "zipBytes": len(inner), "sha256": hashlib.sha256(inner).hexdigest(), "counts": {"offers": 0},
            "documents": [
                {"path": f"{store}/{name}", "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "items": len(json.loads(data))}
                for name, data in documents.items()
            ],
            "schema": f"schemas/{store}.schema.json", "schemaSha256": "0" * 64, "gistSchemaFile": f"data-site-{store}.schema.json",
            "parts": [{"file": name, "bytes": len(parts[name]), "sha256": hashlib.sha256(parts[name]).hexdigest()} for name in names],
        })
    manifest = {"specification": f"{SITE}/", "created": "2026-11-07", "currency": "EUR", "packages": listed}
    if tamper:
        tamper(manifest, parts)
    prefix = f"{top}/" if top else ""
    files = {f"{prefix}00-README.md": b"# Albert Heijn prijsgeschiedenis\n", f"{prefix}data-site-manifest.json": json.dumps(manifest).encode()}
    files.update({f"{prefix}{name}": data for name, data in parts.items()})
    files[f"{prefix}restore_data.py"] = b"raise SystemExit('dit draait nooit')\n"
    for store in packages:
        files[f"{prefix}data-site-{store}.schema.json"] = b"{}"
    return plain_zip(files)


class SiteGistTest(ArchiveCase):
    """De gist van wolph voor data.mealieshopper.nl (data-site-manifest.json)."""

    async def test_the_gist_is_checked_shown_and_imported(self):
        summary, job = await self.run_import(site_gist_zip())
        self.assertEqual((summary["format"], summary["createdAt"]), ("data.mealieshopper.nl", "2026-11-07"))
        self.assertIn("data/ah/ah-history-20261107.zip", summary["source"])
        self.assertEqual(summary["lines"], 3)
        self.assertEqual(by_store(summary)["ah"]["offers"]["new"], 2)
        self.assertEqual(by_store(summary)["jumbo"]["offers"]["new"], 1)
        self.assertEqual(job["summary"]["offers"], 3)
        rows = {row["product_id"]: row for row in self.rows("offer_history")}
        self.assertEqual(set(rows), {"folder:ah0001", "4183", "folder:jumbo0003"})
        self.assertEqual(rows["4183"]["title"], "AH Bloemkool")
        self.assertEqual(rows["folder:ah0001"]["promo_label"], "2 voor")  # opgeschoond, zoals elke folderregel

    async def test_without_the_folder_above_and_a_second_time(self):
        await self.run_import(site_gist_zip(top=""))
        again = (await self.stage(site_gist_zip())).get_json()
        self.assertEqual(again["new"], 0)
        self.assertEqual(by_store(again)["ah"]["offers"]["duplicate"], 2)

    async def test_without_the_box_only_lines_that_belong_to_a_product(self):
        self.keep_unlinked = False
        summary, _ = await self.run_import(site_gist_zip())
        self.assertEqual([row["product_id"] for row in self.rows("offer_history")], ["4183"])  # door het archief al gekoppeld
        self.assertEqual(summary["tidied"]["unlinked"], 2)

    async def test_damaged_or_edited_packages_are_refused(self):
        def change_part(manifest, parts):
            name = sorted(parts)[0]
            parts[name] = parts[name].replace(parts[name][:4], b"AAAA", 1)

        def part(field, value):
            def tamper(manifest, parts):
                manifest["packages"][0]["parts"][0][field] = value
            return tamper

        def package(field, value):
            def tamper(manifest, parts):
                manifest["packages"][0][field] = value
            return tamper

        def document(field, value):
            def tamper(manifest, parts):
                manifest["packages"][0]["documents"][0][field] = value
            return tamper

        def extra_document(manifest, parts):
            manifest["packages"][0]["documents"].append({"path": "ah/offers/2014-001.json", "bytes": 3, "sha256": "0" * 64, "items": 0})

        def unlisted_document(manifest, parts):
            manifest["packages"][0]["documents"] = []

        def missing_part(manifest, parts):
            del parts[sorted(parts)[0]]

        cases = [
            ("a part was edited", change_part, "checksum"),
            ("the length of a part", part("bytes", 1), "checksum"),
            ("the zip", package("sha256", "1" * 64), "checksum"),
            ("the size of the zip", package("zipBytes", 10), "checksum"),
            ("a document", document("sha256", "2" * 64), "checksum"),
            ("the number of lines", document("items", 7), "checksum"),
            ("a listed document is missing", extra_document, "missing_file"),
            ("a document that is not listed", unlisted_document, "bad_structure"),
            ("a missing part", missing_part, "missing_file"),
            ("another store's folder", package("uploadPath", "data/jumbo/ah.zip"), "bad_structure"),
            ("an unknown store", package("store", "aldi"), "bad_structure"),
            ("a part outside the folder", part("file", "../geheim.b64"), "bad_structure"),
            ("a document outside the store", document("path", "jumbo/offers/2013-001.json"), "bad_structure"),
        ]
        for label, tamper, code in cases:
            with self.subTest(label):
                await self.refused(site_gist_zip(tamper=tamper), code)

    async def test_a_document_that_is_not_listed_is_named(self):
        def drop(manifest, parts):
            manifest["packages"][0]["documents"] = manifest["packages"][0]["documents"][:0] + [
                {"path": "ah/offers/2014-001.json", "bytes": 3, "sha256": "0" * 64, "items": 0}
            ]

        body = await self.refused(site_gist_zip(tamper=drop), "missing_file")
        self.assertIn("ah/offers/2014-001.json", body["error"])
        packages = default_packages()
        packages["ah"]["offers/2014-001.json"] = site_doc([gist_offer(9)])

        def forget(manifest, parts):
            manifest["packages"][0]["documents"] = [d for d in manifest["packages"][0]["documents"] if d["path"] != "ah/offers/2014-001.json"]

        body = await self.refused(site_gist_zip(packages, tamper=forget), "unlisted_file")
        self.assertIn("ah/offers/2014-001.json", body["error"])

    async def test_every_document_and_line_is_checked(self):
        cases = [
            ("not a list", b'{"store": "ah"}\n', "list"),
            ("not an object", site_doc(["tekst"]), "object"),
            ("a key twice", b'[{"store": "ah", "store": "jumbo"}]\n', "duplicate_key"),
            ("NaN", b'[{"store": "ah", "price": NaN}]\n', "json"),
            ("another store", site_doc([gist_offer(1, "jumbo")]), "store_folder"),
        ]
        for label, data, reason in cases:
            with self.subTest(label):
                body = await self.refused(site_gist_zip({"ah": {"offers/2013-001.json": data}}), "bad_row")
                self.assertIn("ah/offers/2013-001.json", body["error"])
                nl, en = (text.split("{")[0].strip() for text in price_archive.REASONS[reason])
                self.assertTrue(nl in body["error"] or en in body["error"], body["error"])
        # Een regel die de import niet aanneemt, noemt het document en de plek in de lijst.
        body = await self.refused(site_gist_zip({"ah": {"offers/2013-001.json": site_doc([gist_offer(1), gist_offer(2, url="http://x")])}}), "bad_row")
        self.assertIn("ah/offers/2013-001.json", body["error"])
        self.assertIn(", regel 2:", body["error"])


class SiteZipTest(ArchiveCase):
    """Een ZIP met documenten in de vorm van de site: uit de gist hersteld, de export ervoor of de datarepo."""

    async def test_the_restored_zip_from_the_gist_imports(self):
        summary, job = await self.run_import(plain_zip({"offers/2013-001.json": site_doc([gist_offer(1), gist_offer(2)])}))
        self.assertEqual((summary["format"], summary["source"]), ("data.mealieshopper.nl", ""))
        self.assertEqual(job["summary"]["offers"], 2)

    async def test_the_zip_of_the_data_repository_imports(self):
        top = "data.mealieshopper.nl-main"
        data = plain_zip({
            f"{top}/README.md": b"# MealieShopper data\n",
            f"{top}/schemas/ah.schema.json": b"{}",
            f"{top}/data/README.md": b"# data/\n",
            f"{top}/data/ah/offers/2013-001.json": site_doc([gist_offer(1)]),
            f"{top}/data/picnic/prices/2022-001.json": site_doc([price(1), price(2)]),
            f"{top}/data/recepten/soepen.json": site_doc([{"naam": "Erwtensoep"}]),
        })
        summary, job = await self.run_import(data)
        self.assertEqual((job["summary"]["offers"], job["summary"]["prices"]), (1, 2))
        self.assertEqual(by_store(summary)["picnic"]["prices"]["new"], 2)

    async def test_the_published_site_with_its_catalogue_names_the_source(self):
        catalog = {"site": SITE, "generated_at": "2026-11-08T10:00:00Z", "commit": "abc1234def", "license": {"name": "MIT"}, "collections": []}
        summary = (await self.stage(plain_zip({"index.json": json.dumps(catalog).encode(), "ah/offers/2013-001.json": site_doc([gist_offer(1)])}))).get_json()
        self.assertEqual((summary["source"], summary["createdAt"], summary["license"]), ("data.mealieshopper.nl (abc1234)", "2026-11-08T10:00:00Z", "MIT"))

    async def test_a_line_in_the_folder_of_another_store_is_refused(self):
        body = await self.refused(plain_zip({"data/ah/offers/2013-001.json": site_doc([gist_offer(1, "jumbo")])}), "bad_row")
        self.assertIn("data/ah/offers/2013-001.json", body["error"])

    async def test_other_zips_stay_what_they_were(self):
        await self.refused(plain_zip({"foo/bar.json": site_doc([gist_offer(1)])}), "not_an_archive")
        await self.refused(plain_zip({"offers/../x.json": site_doc([gist_offer(1)])}), "not_an_archive")
        await self.refused(plain_zip({"manifest.json": b"{}", "offers/2013-001.json": site_doc([gist_offer(1)])}), "old_package")


class SiteExportTest(ArchiveCase):
    """Exporteren voor data.mealieshopper.nl (price_archive_export.write_site_export)."""

    def fill(self):
        offer_sql = ("INSERT INTO offer_history (store, product_id, title, unit_size, price, price_before, promo_label, image, url, "
                     "valid_from, valid_until, first_seen, last_seen, personal) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)")
        price_sql = ("INSERT INTO price_observations (store, product_id, day, source, title, brand, unit_size, image, url, price, seen_at) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)")
        with closing(self.connect()) as conn, conn:
            conn.executemany(offer_sql, [
                ("ah", "8001", "AH Halfvolle melk", "1 l", 1.19, 1.39, "voor 1.19", "https://static.ah.nl/melk.jpg",
                 "https://www.ah.nl/producten/product/wi8001", "2026-10-26", "2026-11-01", "2026-10-26T06:00:00+00:00", "2026-11-01T06:00:00+00:00", 0),
                ("ah", "8002", "AH Persoonlijke kaas", "500 g", 3.0, 4.0, "Bonus Box", None,
                 "https://www.ah.nl/producten/product/wi8002", "2026-10-26", "2026-11-01", "2026-10-26T06:00:00+00:00", "2026-11-01T06:00:00+00:00", 1),
                ("picnic", "s1001", "Picnic eieren", "10 stuks", 2.49, 2.99, "1+1 gratis", None,
                 None, "2026-11-02", "2026-11-08", "2026-11-02T06:00:00+00:00", "2026-11-03T06:00:00+00:00", 0),
                ("ah", "folder:ah0002", "Folder: Product 2", "500 g", 1.39, 1.79, "voor: 1.39 Los", "",
                 "https://www.supermarktaanbiedingen.com/folders/albert_heijn/2013/23/product-2", "2013-06-03", "2013-06-09",
                 "2013-06-03T00:00:00+00:00", "2013-06-09T23:59:59+00:00", 0),
            ])
            conn.executemany(price_sql, [
                ("ah", "8001", "2026-10-05", "search", "AH Halfvolle melk", "AH", "1 l", None, "https://www.ah.nl/producten/product/wi8001", 1.39, "2026-10-05T10:00:00+00:00"),
                ("picnic", "s1001", "2022-11-30", "public_observed_unknown", "Picnic eieren", None, "10 stuks", None, None, 2.79, "2022-11-30T19:56:18+01:00"),
            ])

    async def export(self) -> zipfile.ZipFile:
        response = await self.admin.post("/api/price-archive/export?format=site")
        self.assertEqual(response.status_code, 200, response.get_data()[:300])
        self.assertRegex(response.headers["Content-Disposition"], r'attachment; filename="mealieshopper-data-\d{8}-\d{6}\.zip"')
        return zipfile.ZipFile(io.BytesIO(response.get_data()))

    @staticmethod
    def documents(archive: zipfile.ZipFile) -> dict[str, list[dict]]:
        return {name: json.loads(archive.read(name)) for name in archive.namelist() if name.startswith("data/")}

    async def test_a_document_per_store_kind_and_year_with_a_schema(self):
        self.fill()
        archive = await self.export()
        self.assertEqual(sorted(archive.namelist()), [
            "MEALIESHOPPER-EXPORT.md",
            "data/ah/offers/2013-001.json", "data/ah/offers/2026-001.json", "data/ah/prices/2026-001.json",
            "data/picnic/offers/2026-001.json", "data/picnic/prices/2022-001.json",
            "schemas/ah.schema.json", "schemas/picnic.schema.json",
        ])
        documents = self.documents(archive)
        self.assertFalse(any(b"Persoonlijke kaas" in archive.read(name) for name in archive.namelist()))
        melk = documents["data/ah/offers/2026-001.json"][0]
        self.assertEqual((melk["product_id"], melk["image"], melk["price"]), ("8001", "https://static.ah.nl/melk.jpg", 1.19))
        self.assertNotIn("type", melk)
        self.assertNotIn("personal", melk)
        folder = documents["data/ah/offers/2013-001.json"][0]
        self.assertEqual((folder["brand"], folder["image"], folder["ended_at"]), (None, None, "2013-06-09T23:59:59+00:00"))  # zoals de import
        # Het adres van het folderarchief gaat niet mee naar de site; de regel zelf wel, en een adres van de winkel ook.
        self.assertEqual((folder["product_id"], folder["url"], folder["price"]), ("folder:ah0002", None, 1.39))
        self.assertEqual(melk["url"], "https://www.ah.nl/producten/product/wi8001")
        self.assertFalse([name for name in documents if b"supermarktaanbiedingen" in archive.read(name)])  # het schema noemt hem, om hem te weigeren
        self.assertIsNone(documents["data/picnic/offers/2026-001.json"][0]["url"])
        self.assertEqual(documents["data/ah/prices/2026-001.json"][0]["source"], "search")
        readme = archive.read("MEALIESHOPPER-EXPORT.md").decode("utf-8")
        self.assertIn("| Albert Heijn (`ah`) | 2 | 1 | 2013 | 2026 |", readme)
        self.assertIn("helmerzNL/data.mealieshopper.nl", readme)
        with closing(self.connect()) as conn:
            detail = json.loads(conn.execute("SELECT detail FROM audit_log WHERE service = 'price-archive' AND action = 'export'").fetchone()[0])
        self.assertEqual((detail["format"], detail["offers"], detail["prices"], detail["files"]), ("site", 3, 2, 5))

    async def test_every_document_follows_the_rules_of_the_site(self):
        self.fill()
        archive = await self.export()
        schemas = {name.split("/")[1].removesuffix(".schema.json"): json.loads(archive.read(name)) for name in archive.namelist() if name.startswith("schemas/")}
        for schema in schemas.values():
            jsonschema.Draft202012Validator.check_schema(schema)
        for name, data in self.documents(archive).items():
            with self.subTest(name):
                raw = archive.read(name)
                parts = name.split("/")
                self.assertTrue(COLLECTION_RE.match(parts[1]))
                self.assertTrue(all(SEGMENT_RE.match(part) for part in parts[2:]))
                self.assertEqual(raw, price_archive_export.site_text(data).encode("utf-8"))  # zoals de site hem publiceert
                self.assertLessEqual(len(raw), SITE_MAX_DOCUMENT)
                jsonschema.Draft202012Validator(schemas[parts[1]]).validate(data)
                keys = {key.replace("_", "").lower() for row in data for key in row}
                self.assertFalse([key for key in keys if any(word in key for word in SENSITIVE)])

    def test_the_schema_takes_the_gist_without_its_source_address_and_refuses_what_does_not_belong(self):
        schema = jsonschema.Draft202012Validator(price_archive_export.site_schema("ah"))
        gist = gist_offer(1, url=None, match_kind="approximate", match_score=0.9, matched_title="AH Bloemkool")
        self.assertTrue(schema.is_valid([gist, gist_offer(2, url="https://www.ah.nl/producten/product/wi4183", brand=None)]))
        self.assertTrue(schema.is_valid([price(1, "ah", source="search")]))
        for label, row in [
            ("an address of supermarktaanbiedingen.com", gist_offer(1)),
            ("an address of voordeelmuis.nl", gist_offer(1, url="https://www.voordeelmuis.nl/cgi-bin/v.cgi?a=d&d=3318619")),
            ("a price from a folder archive", price(1, "ah", url="https://www.supermarktaanbiedingen.com/folders/albert_heijn/2013/1/x")),
            ("another store", gist_offer(1, "jumbo", url=None)),
            ("an unknown field", gist_offer(1, url=None, type="offer")),
            ("a personal offer", gist_offer(1, url=None, personal=1)),
            ("no https", gist_offer(1, url="http://x")),
            ("a price of nothing", price(1, "ah", price=0)),
            ("an unknown source", price(1, "ah", source="order")),
            ("half an offer and half a price", {**gist_offer(1, url=None), "day": "2013-06-03"}),
        ]:
            with self.subTest(label):
                self.assertFalse(schema.is_valid([row]))

    def test_the_pattern_for_folder_archives_covers_every_archive_address(self):
        pattern = re.compile(price_archive_export.ARCHIVE_URL_PATTERN)
        for prefix in price_archive_tidy.ARCHIVE_URLS:
            self.assertTrue(pattern.match(prefix + "x"), prefix)
        for address in ("https://www.ah.nl/producten/product/wi4183", "https://crisp.nl/download/12/175/persbericht.pdf",
                        "https://www.supermarktaanbiedingen.nl/folders/x", "https://wwwXsupermarktaanbiedingen.com/folders/x"):
            self.assertIsNone(pattern.match(address), address)
        # Alleen een escape voor . en ?: zo werkt de regex ook als ECMAScript-regex (JSON Schema).
        self.assertEqual(set(re.findall(r"\\(.)", price_archive_export.ARCHIVE_URL_PATTERN)), {".", "?"})

    async def test_documents_are_split_before_the_limit(self):
        with closing(self.connect()) as conn, conn:
            conn.executemany(
                "INSERT INTO offer_history (store, product_id, title, url, valid_from, valid_until, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [("ah", f"{8000 + n}", f"AH Product {n}", f"https://www.ah.nl/producten/product/wi{8000 + n}", "2025-01-06", "2025-01-12",
                  "2025-01-06T00:00:00+00:00", "2025-01-12T00:00:00+00:00") for n in range(40)],
            )
        await price_archive.ensure_schema()
        path = self.data / "export.zip"
        header = await asyncio.to_thread(price_archive_export.write_site_export, path, 4000)
        archive = zipfile.ZipFile(path)
        names = [name for name in header["files"]]
        self.assertGreater(len(names), 2)
        self.assertEqual(names, [f"data/ah/offers/2025-{n:03d}.json" for n in range(1, len(names) + 1)])
        product_ids = []
        for name in names:
            raw = archive.read(name)
            self.assertLessEqual(len(raw), 4000)
            data = json.loads(raw)
            self.assertEqual(raw, price_archive_export.site_text(data).encode("utf-8"))
            product_ids += [row["product_id"] for row in data]
        self.assertEqual(product_ids, [f"{8000 + n}" for n in range(40)])

    async def test_the_same_data_gives_the_same_documents_and_comes_back_with_the_import(self):
        self.fill()
        first, second = await self.export(), await self.export()
        self.assertEqual(first.namelist(), second.namelist())
        for name in first.namelist():
            if name.endswith(".json"):
                self.assertEqual(first.read(name), second.read(name), name)
        before = self.snapshot()
        data = plain_zip({name: first.read(name) for name in first.namelist()})
        with closing(self.connect()) as conn, conn:
            conn.execute("DELETE FROM offer_history")
            conn.execute("DELETE FROM price_observations")
        summary, job = await self.run_import(data)
        self.assertEqual(summary["format"], "data.mealieshopper.nl")
        self.assertEqual((job["summary"]["offers"], job["summary"]["prices"]), (3, 2))
        columns = ("store", "product_id", "title", "price", "price_before", "url", "valid_from", "valid_until", "first_seen")

        def plain(rows):
            return sorted([row[key] for key in columns] for row in rows)

        # Alles komt terug, behalve het adres van het folderarchief: dat ging niet mee naar de site.
        expected = [{**row, "url": None if str(row["url"] or "").startswith(price_archive_tidy.ARCHIVE_URLS) else row["url"]}
                    for row in before[0] if not row["personal"]]
        self.assertEqual(plain(self.rows("offer_history")), plain(expected))
        self.assertEqual(sorted((row["store"], row["source"], row["price"]) for row in self.rows("price_observations")),
                         [("ah", "archive_search", 1.39), ("picnic", "public_observed_unknown", 2.79)])

    async def test_lines_that_only_differed_in_the_archive_address_go_once(self):
        # Het archief noemde dezelfde aanbieding twee keer (twee folderpagina's); een variant (andere actietekst) blijft.
        twice = [gist_offer(1, url=f"https://www.supermarktaanbiedingen.com/folders/albert_heijn/2013/23/p{page}") for page in (1, 2)]
        variant = gist_offer(1, url="https://www.supermarktaanbiedingen.com/folders/albert_heijn/2013/23/p3", promo_label="Folder | Stuk 100 gram")
        await self.run_import(plain_zip({"offers/2013-001.json": site_doc([*twice, variant])}))
        self.assertEqual(len(self.rows("offer_history")), 3)
        await price_archive.ensure_schema()
        header = await asyncio.to_thread(price_archive_export.write_site_export, self.data / "export.zip")
        rows = json.loads(zipfile.ZipFile(self.data / "export.zip").read("data/ah/offers/2013-001.json"))
        self.assertEqual(sorted(row["promo_label"] for row in rows), ["2 voor", "Stuk 100 gram"])
        self.assertEqual((header["merged"], header["counts"]["offers"]), ({"offers": 1, "prices": 0}, {"ah": 2}))
        self.assertIn("1 regels die alleen in het adres van het archief verschilden",
                      zipfile.ZipFile(self.data / "export.zip").read("MEALIESHOPPER-EXPORT.md").decode("utf-8"))

    async def test_the_gist_comes_out_under_the_same_names(self):
        await self.run_import(site_gist_zip())
        archive = await self.export()
        self.assertEqual(sorted(name for name in archive.namelist() if name.startswith("data/")),
                         ["data/ah/offers/2013-001.json", "data/jumbo/offers/2013-001.json"])
        self.assertIn(f"MealieShopper {about.read_version()}", archive.read("MEALIESHOPPER-EXPORT.md").decode("utf-8"))


    async def test_a_pack_only_in_the_leaflet_text_keeps_its_price_on_the_way_back(self):
        # De verpakking staat alleen in `description`, die de app niet bewaart; in de export staat ze nog in de actietekst.
        row = gist_offer(1, unit_size=None, description="Bakje 40 gram", promo_label="Folder | Bakje 40 gram | bron: oud=1.69, actie=1.27",
                         price=1.27, price_before=1.69)
        await self.run_import(plain_zip({"offers/2013-001.json": site_doc([row])}))
        archive = await self.export()
        exported = self.documents(archive)["data/ah/offers/2013-001.json"][0]
        self.assertEqual((exported["promo_label"], exported["unit_size"], exported["price"]), ("Bakje 40 gram", None, 1.27))
        with closing(self.connect()) as conn, conn:
            conn.execute("DELETE FROM offer_history")
        summary, _ = await self.run_import(plain_zip({name: archive.read(name) for name in archive.namelist()}))
        self.assertEqual(summary["tidied"]["unknownPack"], 0)
        self.assertEqual([(r["price"], r["price_before"]) for r in self.rows("offer_history")], [(1.27, 1.69)])


class FetchTest(ArchiveCase):
    """Aanbiedingen ophalen van data.mealieshopper.nl (price_archive.fetch_site)."""

    def catalog(self, documents: dict[str, bytes], **changes) -> dict:
        collections: dict[str, list[dict]] = {}
        for path, data in documents.items():
            collections.setdefault(path.split("/")[0], []).append({
                "path": path, "url": f"https://elders.example/{path}", "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                "items": len(json.loads(data)),
            })
        catalog = {
            "site": SITE, "generated_at": "2026-11-08T10:00:00Z", "commit": "abc1234def5678", "license": {"name": "MIT"},
            "collections": [{"name": name, "title": name, "files": files} for name, files in collections.items()],
        }
        catalog.update(changes)
        return catalog

    def serve(self, router, catalog: Any, documents: dict[str, bytes]) -> None:
        router.get(f"{SITE}/index.json").respond(200, content=catalog if isinstance(catalog, bytes) else json.dumps(catalog).encode())
        for path, data in documents.items():
            router.get(f"{SITE}/{path}").respond(200, content=data)

    async def fetch(self, keep_unlinked: bool = True):
        return await self.admin.post("/api/price-archive/fetch" + ("?keep_unlinked=1" if keep_unlinked else ""))

    async def test_fetching_shows_the_summary_and_imports(self):
        documents = {
            "ah/offers/2013-001.json": site_doc([gist_offer(1), gist_offer(2)]),
            "picnic/prices/2022-001.json": site_doc([price(1)]),
            "recepten/soepen.json": site_doc([{"naam": "Erwtensoep"}]),
            "ah/schemas/x.json": site_doc([]),
        }
        with routes() as router:
            self.serve(router, self.catalog(documents), documents)
            response = await self.fetch()
            summary = response.get_json()
            self.assertEqual(response.status_code, 200, summary)
            called = sorted(str(call.request.url) for call in router.calls)
        self.assertEqual(called, [f"{SITE}/ah/offers/2013-001.json", f"{SITE}/index.json", f"{SITE}/picnic/prices/2022-001.json"])
        self.assertEqual((summary["format"], summary["source"], summary["license"]), ("data.mealieshopper.nl", "data.mealieshopper.nl (abc1234)", "MIT"))
        self.assertEqual(by_store(summary)["ah"]["offers"]["new"], 2)
        self.assertEqual(by_store(summary)["picnic"]["prices"]["new"], 1)
        started = await self.admin.post("/api/price-archive/jobs", json={"token": summary["token"]})
        self.assertEqual(started.status_code, 202)
        job = await self.wait()
        self.assertEqual((job["status"], job["summary"]["offers"], job["summary"]["prices"]), ("done", 2, 1))

    async def test_without_the_box_only_lines_that_belong_to_a_product(self):
        documents = {"ah/offers/2013-001.json": site_doc([gist_offer(1), gist_offer(2, product_id="4183")])}
        with routes() as router:
            self.serve(router, self.catalog(documents), documents)
            summary = (await self.fetch(keep_unlinked=False)).get_json()
        self.assertEqual(by_store(summary)["ah"]["offers"]["new"], 1)
        self.assertEqual(summary["keepUnlinked"], False)

    async def refused_fetch(self, catalog: Any, documents: dict[str, bytes], code: str, status: int) -> dict:
        before = self.snapshot()
        with routes() as router:
            self.serve(router, catalog, documents)
            response = await self.fetch()
        body = response.get_json()
        self.assertEqual((response.status_code, body.get("code")), (status, code), body)
        self.assertTrue(body["error"])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.staged_files(), [])
        return body

    async def test_a_document_that_does_not_match_the_catalogue_is_refused(self):
        documents = {"ah/offers/2013-001.json": site_doc([gist_offer(1)]), "ah/offers/2014-001.json": site_doc([gist_offer(2)])}
        catalog = self.catalog(documents)
        served = dict(documents, **{"ah/offers/2014-001.json": site_doc([gist_offer(3)])})
        body = await self.refused_fetch(catalog, served, "site_checksum", 502)
        self.assertIn("ah/offers/2014-001.json", body["error"])
        longer = dict(documents, **{"ah/offers/2014-001.json": documents["ah/offers/2014-001.json"] + b" "})
        await self.refused_fetch(catalog, longer, "site_checksum", 502)

    async def test_a_broken_catalogue_or_an_empty_site(self):
        documents = {"ah/offers/2013-001.json": site_doc([gist_offer(1)])}
        await self.refused_fetch(b"<html>", documents, "site_catalog", 502)
        await self.refused_fetch({"collections": "ah"}, documents, "site_catalog", 502)
        bad = self.catalog(documents)
        bad["collections"][0]["files"][0]["sha256"] = "geen hash"
        await self.refused_fetch(bad, documents, "site_catalog", 502)
        await self.refused_fetch(self.catalog({}), {}, "site_empty", 404)
        outside = self.catalog(documents)
        outside["collections"][0]["files"][0]["path"] = "ah/../../geheim.json"
        await self.refused_fetch(outside, {}, "site_empty", 404)

    async def test_an_unreachable_site(self):
        with routes() as router:
            router.get(f"{SITE}/index.json").respond(503)
            response = await self.fetch()
        self.assertEqual((response.status_code, response.get_json()["code"]), (502, "site_unreachable"))
        self.assertIn("HTTP 503", response.get_json()["error"])
        with routes() as router:
            router.get(f"{SITE}/index.json").mock(side_effect=httpx.ConnectError("geen verbinding"))
            response = await self.fetch()
        self.assertEqual((response.status_code, response.get_json()["code"]), (502, "site_unreachable"))
        with routes() as router:  # een doorverwijzing volgt de app niet
            router.get(f"{SITE}/index.json").respond(302, headers={"Location": "https://elders.example/index.json"})
            response = await self.fetch()
        self.assertEqual((response.status_code, response.get_json()["code"]), (502, "site_unreachable"))
        self.assertEqual(self.staged_files(), [])
