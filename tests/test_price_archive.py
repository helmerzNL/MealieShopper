"""Prijsarchief importeren (price_archive.py): de ZIP van de gist, het .tar.gz en het eigen formaat, met alles wat geweigerd wordt."""
import base64
import gzip
import hashlib
import io
import json
import os
import tarfile
import tempfile
import time
import unittest
import zipfile
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

from mealieshopper import auth, auth_password, backup, price_archive, prices  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402

PASSWORD = "koffie-met-melk-42"
ANNA = "een-heel-ander-wachtwoord"
TOP = "195c8964d4e63eb1774fc14ba35b4f09-058f0a157c46eab27e38f65b4b2d96af9bf679fe"
ROOT_DIR = "supermarket-archives/2026-11-06"
FOLDER = "https://www.supermarktaanbiedingen.com/folders"


def offer(n: int, store: str = "ah", **changes) -> dict:
    slug = {"ah": "albert_heijn", "jumbo": "jumbo", "picnic": "picnic", "crisp": "crisp"}[store]
    row = {
        "store": store, "product_id": f"folder:{store}{n:04d}", "title": f"Folder: Product {n}", "brand": "Folderarchief",
        "unit_size": "500 g", "category": None, "price": 1.0 + n / 100, "price_before": 2.0, "promo_label": "Folder | 2 voor",
        "image": "", "url": f"{FOLDER}/{slug}/2013/23/product-{n}", "valid_from": "2013-06-03", "valid_until": "2013-06-09",
        "first_seen": "2013-06-03T00:00:00+00:00", "last_seen": "2013-06-09T23:59:59+00:00", "ended_at": "2013-06-09T23:59:59+00:00",
        "syncs": 1, "continued": 0, "personal": 0, "source_title": f"Product {n}", "raw_old": "2.00", "raw_now": "1.00",
    }
    row.update(changes)
    return row


def price(n: int, store: str = "picnic", **changes) -> dict:
    row = {
        "store": store, "product_id": f"folder:{store}p{n:04d}", "day": "2022-11-30",
        "source": "public_observed_unknown" if store == "picnic" else "public_press_release",
        "title": f"Archief: Prijs {n}", "brand": f"{store.title()} (archief)", "unit_size": "1 l", "image": "",
        "url": "https://github.com/supermarkt/checkjebon/blob/2b1b6636d81c75cc63da1ac365371908f990e17c/data/supermarkets.json",
        "price": 1.25 + n / 100, "seen_at": "2022-11-30T19:56:18+01:00", "source_commit": "2b1b6636d81c75cc63da1ac365371908f990e17c",
    }
    row.update(changes)
    return row


def correction(url: str, old: tuple, new: tuple) -> dict:
    return {"category": "new_bundle", "url": url, "old_price": old[0], "old_before": old[1], "new_price": new[0], "new_before": new[1]}


def by_store(summary: dict) -> dict[str, dict]:
    return {entry["store"]: entry for entry in summary["stores"]}


def jsonl(rows: list[dict]) -> bytes:
    return "".join(json.dumps(row) + "\n" for row in rows).encode("utf-8")


def gist_files(**changes) -> dict[str, bytes]:
    """De zes gegevensbestanden van het pakket, met een paar regels per winkel."""
    files = {
        "ah-corrected/corrections.json": json.dumps([]).encode(),
        "ah-corrected/observations.jsonl": jsonl([offer(1), offer(2, product_id="4183", title="AH Bloemkool")]),
        "jumbo/reports/observations.jsonl": jsonl([offer(3, "jumbo"), offer(4, "jumbo", price=None, price_before=None)]),
        "picnic/reports/offers.jsonl": jsonl([offer(5, "picnic", url="https://www.voordeelmuis.nl/cgi-bin/v.cgi?a=d&d=3318619")]),
        "picnic/reports/observations.jsonl": jsonl([price(1), price(2), price(3, day="2023-04-12", seen_at="2023-04-12T08:00:00+02:00")]),
        "crisp/reports/observations.jsonl": jsonl([price(4, "crisp", url="https://crisp.nl/download/12/175/persbericht.pdf", day="2021-10-19", seen_at="2021-10-19T00:00:00+00:00")]),
    }
    files.update(changes)
    return files


def tar_gz(files: dict[str, bytes], manifest: dict | None = None, extra: dict[str, bytes] | None = None) -> bytes:
    members = {f"{ROOT_DIR}/{name}": data for name, data in files.items()}
    data_manifest = manifest if manifest is not None else {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    members[f"{ROOT_DIR}/data-manifest.json"] = json.dumps(data_manifest).encode()
    members.update(extra or {})
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return out.getvalue()


def gist_zip(archive: bytes | None = None, part_size: int = 300, tamper=None, top: str = TOP) -> bytes:
    """De ZIP zoals Download ZIP van de gist hem geeft: een map erboven, het manifest en Base64-delen van het .tar.gz."""
    archive = archive if archive is not None else tar_gz(gist_files())
    encoded = base64.b64encode(archive)
    parts = {f"supermarket-archive.part-{i + 1:03d}.b64": encoded[at:at + part_size] + b"\n" for i, at in enumerate(range(0, len(encoded), part_size))}
    manifest = {
        "format": "supermarket-archive-base64-parts", "formatVersion": 1, "archiveFile": "supermarket-archives-20261106.tar.gz",
        "archiveBytes": len(archive), "archiveSha256": hashlib.sha256(archive).hexdigest(), "observationCount": 9,
        "parts": [{"file": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()} for name, data in parts.items()],
    }
    if tamper:
        tamper(manifest, parts)
    out = io.BytesIO()
    prefix = f"{top}/" if top else ""
    with zipfile.ZipFile(out, "w") as bundle:
        bundle.writestr(f"{prefix}00-README.md", "# Supermarktprijsgeschiedenis\n")
        bundle.writestr(f"{prefix}supermarket-manifest.json", json.dumps(manifest))
        for name, data in parts.items():
            bundle.writestr(f"{prefix}{name}", data)
        bundle.writestr(f"{prefix}restore_other.py", "raise SystemExit('dit draait nooit')\n")
    return out.getvalue()


def own(rows: list[dict], header: dict | None = None, compress: bool = False) -> bytes:
    head = header if header is not None else {
        "format": "mealieshopper-price-archive", "formatVersion": 1, "createdAt": "2026-11-06T09:00:00Z",
        "source": "Testarchief", "license": "CC0",
    }
    data = (json.dumps(head) + "\n").encode() + jsonl(rows)
    return gzip.compress(data) if compress else data


class ArchiveCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name)
        env = mock.patch.dict(os.environ, {"PASSKEY_AUTH_ENABLED": "true", "MEALIESHOPPER_DATA_DIR": tmp.name})
        env.start()
        self.addCleanup(env.stop)
        auth.forget_runtime_state()
        self.addCleanup(auth.forget_runtime_state)
        scrypt = mock.patch.object(auth_password, "SCRYPT_N", 2**4)
        scrypt.start()
        self.addCleanup(scrypt.stop)
        self.app = create_app()
        self.admin = self.app.test_client()
        self.admin.post("/api/auth/password", json={"username": "admin", "password": PASSWORD})

    def connect(self):
        price_archive._connect().close()
        return auth.connect()

    def stage(self, data: bytes, client=None):
        return (client or self.admin).post("/api/price-archive/stage", data=data, content_type="application/octet-stream")

    def wait(self, seconds: float = 20) -> dict:
        deadline = time.time() + seconds
        while time.time() < deadline:
            job = self.admin.get("/api/price-archive/jobs/latest").get_json()["job"]
            if job and job["status"] != "running":
                return job
            time.sleep(0.05)
        self.fail("de klus is niet klaar gekomen")

    def run_import(self, data: bytes) -> tuple[dict, dict]:
        staged = self.stage(data)
        self.assertEqual(staged.status_code, 200, staged.get_json())
        summary = staged.get_json()
        started = self.admin.post("/api/price-archive/jobs", json={"token": summary["token"]})
        self.assertEqual(started.status_code, 202, started.get_json())
        job = self.wait()
        self.assertEqual(job["status"], "done", job)
        self.admin.delete(f"/api/price-archive/jobs/{job['id']}")
        return summary, job

    def rows(self, table: str) -> list[dict]:
        with closing(self.connect()) as conn:
            return [dict(row) for row in conn.execute(f'SELECT * FROM "{table}" ORDER BY rowid')]

    def snapshot(self) -> tuple[list[dict], list[dict]]:
        return self.rows("offer_history"), self.rows("price_observations")

    def staged_files(self) -> list[str]:
        return sorted(path.name for path in self.data.glob(".price-archive-*"))

    def refused(self, data: bytes, code: str, status: int = 400) -> dict:
        before = self.snapshot()
        response = self.stage(data)
        body = response.get_json()
        self.assertEqual((response.status_code, body.get("code")), (status, code), body)
        self.assertTrue(body["error"])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.staged_files(), [])
        return body


class GistPackageTest(ArchiveCase):
    def test_the_zip_from_the_gist_is_checked_shown_and_imported(self):
        summary, job = self.run_import(gist_zip())
        self.assertEqual(summary["format"], "supermarket-archive-base64-parts")
        self.assertEqual(summary["source"], ROOT_DIR)
        self.assertEqual(summary["lines"], 9)
        self.assertEqual(summary["new"], 9)
        self.assertEqual([entry["store"] for entry in summary["stores"]], ["ah", "picnic", "jumbo", "crisp"])  # zoals in de app
        stores = by_store(summary)
        self.assertEqual(stores["ah"]["offers"], {"new": 2, "duplicate": 0, "covered": 0})
        self.assertEqual(stores["picnic"]["prices"], {"new": 3, "duplicate": 0, "kept": 0})
        self.assertEqual((stores["picnic"]["earliest"], stores["picnic"]["latest"]), ("2013-06-03", "2023-04-12"))
        self.assertEqual(job["summary"]["offers"], 5)
        self.assertEqual(job["summary"]["prices"], 4)

        history = self.rows("offer_history")
        self.assertEqual(len(history), 5)
        first = history[0]
        self.assertEqual((first["store"], first["product_id"], first["price"], first["price_before"]), ("ah", "folder:ah0001", 1.01, 2.0))
        self.assertEqual((first["personal"], first["syncs"], first["image"]), (0, 1, ""))
        self.assertEqual(history[1]["product_id"], "4183")  # een gekoppeld productnummer blijft staan
        observed = self.rows("price_observations")
        self.assertEqual(sorted(row["source"] for row in observed), ["public_observed_unknown"] * 3 + ["public_press_release"])
        # De herkomstvelden (source_commit, raw_old, ...) komen niet in de database; de kolommen blijven die van de app.
        self.assertNotIn("source_commit", observed[0])
        # En de app laat ze zien zoals bedoeld: waargenomen, niet als gewone prijs.
        product = prices.history("picnic", product="folder:picnicp0001")["products"][0]
        self.assertEqual(product["observed"][0]["price"], 1.26)
        self.assertEqual(product["regular"], [])
        self.assertEqual(self.staged_files(), [])

    def test_the_tar_gz_itself_works_too(self):
        summary, job = self.run_import(tar_gz(gist_files()))
        self.assertEqual(summary["new"], 9)
        self.assertEqual(job["summary"]["offers"] + job["summary"]["prices"], 9)

    def test_a_zip_without_the_folder_above_works_too(self):
        summary, _ = self.run_import(gist_zip(top=""))
        self.assertEqual(summary["new"], 9)

    def test_importing_again_finds_everything_already_there(self):
        self.run_import(gist_zip())
        before = self.snapshot()
        summary, job = self.run_import(gist_zip())
        self.assertEqual(summary["new"], 0)
        stores = by_store(summary)
        self.assertEqual(stores["ah"]["offers"], {"new": 0, "duplicate": 2, "covered": 0})
        self.assertEqual(stores["picnic"]["prices"], {"new": 0, "duplicate": 3, "kept": 0})
        self.assertEqual(stores["ah"]["earliest"], "2013-06-03")  # de periode van het archief blijft zichtbaar
        self.assertEqual((job["summary"]["offers"], job["summary"]["prices"]), (0, 0))
        self.assertEqual(self.snapshot(), before)

    def test_what_the_app_already_has_always_wins(self):
        with closing(self.connect()) as conn, conn:
            # Een eigen meting van dezelfde dag met een andere prijs, en een aanbieding uit een synchronisatie van hetzelfde product.
            conn.execute(
                "INSERT INTO price_observations (store, product_id, day, source, title, price, seen_at) "
                "VALUES ('picnic', 'folder:picnicp0001', '2022-11-30', 'search', 'Eigen meting', 9.99, '2022-11-30T12:00:00+00:00')"
            )
            conn.execute(
                "INSERT INTO offer_history (store, product_id, title, price, url, valid_from, valid_until, first_seen, last_seen) "
                "VALUES ('jumbo', 'folder:jumbo0003', 'Live', 0.5, 'https://www.jumbo.com/x', '2013-06-03', '2013-06-09', '2013-06-03', '2013-06-09')"
            )
        summary, job = self.run_import(gist_zip())
        self.assertEqual(by_store(summary)["picnic"]["prices"], {"new": 2, "duplicate": 0, "kept": 1})
        self.assertEqual(by_store(summary)["jumbo"]["offers"], {"new": 1, "duplicate": 0, "covered": 1})
        own_price = [row for row in self.rows("price_observations") if row["product_id"] == "folder:picnicp0001"]
        self.assertEqual([(row["price"], row["source"]) for row in own_price], [(9.99, "search")])
        self.assertEqual(len([row for row in self.rows("offer_history") if row["product_id"] == "folder:jumbo0003"]), 1)

    def test_corrections_change_only_an_earlier_archive_line_whose_old_value_matches(self):
        urls = [f"{FOLDER}/albert_heijn/2013/23/correctie-{n}" for n in range(4)]
        with closing(self.connect()) as conn, conn:
            for n, (url, value) in enumerate(zip(urls, [(1.0, 1.5), (1.2, 1.5), (0.5, 0.75)])):
                conn.execute(
                    "INSERT INTO offer_history (store, product_id, title, price, price_before, url, first_seen, last_seen) "
                    "VALUES ('ah', ?, 'AH Muffins', ?, ?, ?, '2013-06-03', '2013-06-09')",
                    (f"folder:c{n}", value[0], value[1], url),
                )
        changes = json.dumps([
            correction(urls[0], (1.0, 1.5), (0.5, 0.75)),  # klopt: wordt verbeterd
            correction(urls[1], (1.0, 1.5), (0.5, 0.75)),  # de huidige waarde wijkt af: blijft staan
            correction(urls[2], (1.0, 1.5), (0.5, 0.75)),  # staat al goed
            correction(urls[3], (1.0, 1.5), (0.5, 0.75)),  # die regel is er niet
        ]).encode()
        summary, job = self.run_import(gist_zip(tar_gz(gist_files(**{"ah-corrected/corrections.json": changes}))))
        self.assertEqual(summary["corrections"], {"apply": 1, "already": 1, "missing": 1, "conflict": 1})
        self.assertEqual(job["summary"]["corrections"]["apply"], 1)
        values = {row["url"]: (row["price"], row["price_before"]) for row in self.rows("offer_history") if row["url"] in urls}
        self.assertEqual(values, {urls[0]: (0.5, 0.75), urls[1]: (1.2, 1.5), urls[2]: (0.5, 0.75)})

    def test_a_correction_never_touches_a_line_from_a_store_of_its_own(self):
        url = f"{FOLDER}/albert_heijn/2013/23/live"
        with closing(self.connect()) as conn, conn:
            conn.execute(
                "INSERT INTO offer_history (store, product_id, title, price, price_before, url, first_seen, last_seen) "
                "VALUES ('ah', '4183', 'AH Bloemkool', 1.0, 1.5, ?, '2013-06-03', '2013-06-09')", (url,),
            )
        changes = json.dumps([correction(url, (1.0, 1.5), (0.5, 0.75))]).encode()
        summary, _ = self.run_import(gist_zip(tar_gz(gist_files(**{"ah-corrected/corrections.json": changes}))))
        self.assertEqual(summary["corrections"]["conflict"], 1)
        self.assertEqual([row["price"] for row in self.rows("offer_history") if row["url"] == url], [1.0])

    def test_a_control_character_in_free_text_becomes_a_space(self):
        rows = jsonl([offer(1, promo_label="Pak 250-450\x1b gram, 20 procent voordeel")])
        summary, _ = self.run_import(gist_zip(tar_gz(gist_files(**{"ah-corrected/observations.jsonl": rows}))))
        self.assertEqual(by_store(summary)["ah"]["offers"]["new"], 1)
        labels = [row["promo_label"] for row in self.rows("offer_history") if row["store"] == "ah"]
        self.assertEqual(labels, ["Pak 250-450 gram, 20 procent voordeel"])

    def test_an_image_address_from_an_archive_is_not_taken_over(self):
        rows = jsonl([offer(1, image="https://evil.example/pixel.png")])
        self.run_import(gist_zip(tar_gz(gist_files(**{"ah-corrected/observations.jsonl": rows}))))
        self.assertEqual({row["image"] for row in self.rows("offer_history")}, {""})


class OwnFormatTest(ArchiveCase):
    ROWS = [
        {"type": "correction", "store": "ah", "url": f"{FOLDER}/albert_heijn/2013/23/eerder", "old_price": 1.0, "old_before": 1.5, "new_price": 0.5, "new_before": 0.75},
        {"type": "offer", **offer(7, "jumbo")},
        {"type": "price", **price(8, "crisp", url="https://crisp.nl/download/1/2/x.pdf", unit_size="3 stuks")},
        {"type": "price", **price(9, "picnic")},
    ]

    def test_a_jsonl_file_with_all_three_kinds_is_imported(self):
        with closing(self.connect()) as conn, conn:
            conn.execute(
                "INSERT INTO offer_history (store, product_id, title, price, price_before, url, first_seen, last_seen) "
                "VALUES ('ah', 'folder:eerder', 'Eerder', 1.0, 1.5, ?, '2013-06-03', '2013-06-09')", (self.ROWS[0]["url"],),
            )
        summary, job = self.run_import(own(self.ROWS))
        self.assertEqual((summary["format"], summary["source"], summary["license"]), ("mealieshopper-price-archive", "Testarchief", "CC0"))
        self.assertEqual(summary["new"], 4)
        self.assertEqual(job["summary"]["corrections"]["apply"], 1)
        self.assertEqual(len(self.rows("offer_history")), 2)
        self.assertEqual({row["source"] for row in self.rows("price_observations")}, {"public_press_release", "public_observed_unknown"})

    def test_gzip_works_and_defaults_are_filled_in(self):
        minimal = {"type": "price", "store": "crisp", "product_id": "folder:x", "day": "2024-01-02", "source": "public_press_release", "title": "Avocado", "price": 4.99}
        bare_offer = {k: v for k, v in offer(1, "jumbo").items() if k not in ("ended_at", "syncs", "continued", "personal", "image")}
        self.run_import(own([minimal, {"type": "offer", **bare_offer}], compress=True))
        observed = self.rows("price_observations")[0]
        self.assertEqual((observed["seen_at"], observed["image"], observed["url"]), ("2024-01-02T00:00:00+00:00", "", None))
        history = self.rows("offer_history")[0]
        self.assertEqual((history["ended_at"], history["syncs"], history["continued"], history["personal"]), (history["last_seen"], 1, 0, 0))


class RefusedTest(ArchiveCase):
    def test_damaged_or_edited_packages(self):
        def bad_part(manifest, parts):
            name = next(iter(parts))
            parts[name] = b"A" + parts[name][1:]
        self.refused(gist_zip(tamper=bad_part), "checksum")

        def bad_total(manifest, parts):
            manifest["archiveSha256"] = "0" * 64
        self.refused(gist_zip(tamper=bad_total), "checksum")

        def path_in_name(manifest, parts):
            manifest["parts"][0]["file"] = "../supermarket-archive.part-001.b64"
        self.refused(gist_zip(tamper=path_in_name), "bad_structure")

        def missing_part(manifest, parts):
            parts.pop(next(iter(parts)))
        self.refused(gist_zip(tamper=missing_part), "missing_file")

        files = gist_files()
        honest = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
        edited = dict(files, **{"jumbo/reports/observations.jsonl": jsonl([offer(3, "jumbo", price=0.01)])})
        self.refused(gist_zip(tar_gz(edited, manifest=honest)), "checksum")

        incomplete = dict(files)
        incomplete.pop("crisp/reports/observations.jsonl")
        self.refused(gist_zip(tar_gz(incomplete)), "missing_file")

    def test_the_older_ah_package_and_other_files(self):
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as bundle:
            bundle.writestr(f"{TOP}/manifest.json", json.dumps({"format": "ah-bonus-archive-base64-parts"}))
        self.refused(out.getvalue(), "old_package")
        self.refused(b"\x00\x01 dit is geen archief", "not_an_archive")
        self.refused(b"", "not_an_archive")
        self.refused(json.dumps({"format": "mealieshopper-backup"}).encode() + b"\n", "not_an_archive")
        self.refused(gzip.compress(b"geen json"), "not_an_archive")

    def test_format_versions(self):
        header = {"format": "mealieshopper-price-archive", "formatVersion": 2}
        body = self.refused(own(OwnFormatTest.ROWS, header=header), "newer_version")
        self.assertIn("2", body["error"])
        self.refused(own(OwnFormatTest.ROWS, header={"format": "mealieshopper-price-archive"}), "bad_structure")

    def test_every_line_is_checked(self):
        today = date.today()
        cases = [
            ({"type": "visit"}, "type"),
            ({"type": "offer", **offer(1), "store": "lidl"}, "store"),
            ({"type": "price", **price(1, source="search")}, "source"),
            ({"type": "price", **price(1, source="order")}, "source"),
            ({"type": "offer", **offer(1, url="http://www.supermarktaanbiedingen.com/x")}, "url"),
            ({"type": "offer", **offer(1, url="javascript:alert(1)")}, "url"),
            ({"type": "price", **price(1, day=(today + timedelta(days=1)).isoformat(), seen_at=None)}, "date_range"),
            ({"type": "price", **price(1, day="1999-12-31", seen_at=None)}, "date_range"),
            ({"type": "price", **price(1, day="30-11-2022")}, "date"),
            ({"type": "price", **price(1, seen_at="2022-12-01T10:00:00+00:00")}, "seen_at"),
            ({"type": "offer", **offer(1, title="x" * 1001)}, "too_long"),
            ({"type": "offer", **offer(1, product_id="folder:\x00x")}, "control"),
            ({"type": "offer", **offer(1, title="   ")}, "missing"),
            ({"type": "offer", **offer(1, price=-1)}, "price"),
            ({"type": "offer", **offer(1, price=True)}, "price"),
            ({"type": "offer", **offer(1, price="1,00")}, "price"),
            ({"type": "price", **price(1, price=0)}, "price"),
            ({"type": "offer", **offer(1, personal=1)}, "personal"),
            ({"type": "offer", **offer(1, syncs=0)}, "number"),
            ({"type": "correction", "store": "ah", "url": f"{FOLDER}/x", "old_price": 1.0}, "missing"),
        ]
        for row, reason in cases:
            with self.subTest(reason=reason, row=str(row)[:80]):
                body = self.refused(own([OwnFormatTest.ROWS[1], row]), "bad_row")
                self.assertIn("mealieshopper-price-archive", body["error"])
                self.assertIn("3", body["error"])  # het regelnummer: kop, een goede regel, dan deze
        nan = (json.dumps({"format": "mealieshopper-price-archive", "formatVersion": 1}) + "\n").encode() + b'{"type": "price", "price": NaN}\n'
        self.refused(nan, "bad_row")
        self.refused(own([]), "empty")

    def test_a_bad_line_in_the_gist_names_the_file_and_line(self):
        rows = jsonl([offer(3, "jumbo"), {**offer(4, "jumbo"), "store": "aldi"}])
        body = self.refused(gist_zip(tar_gz(gist_files(**{"jumbo/reports/observations.jsonl": rows}))), "bad_row")
        self.assertIn("jumbo/reports/observations.jsonl, regel 2", body["error"])

    def test_the_message_follows_the_language_of_the_reader(self):
        self.admin.set_cookie("ms_lang", "en")
        body = self.refused(b"nonsense", "not_an_archive")
        self.assertIn("This is not a price archive", body["error"])

    def test_size_and_line_limits(self):
        with mock.patch.object(price_archive, "MAX_UPLOAD_BYTES", 1024):
            self.refused(gist_zip(), "too_large", 413)
        with mock.patch.object(price_archive, "MAX_RECORDS", 3):
            self.refused(gist_zip(), "too_many")


class JobAndAccessTest(ArchiveCase):
    ROUTES = (
        ("get", "/api/price-archive"), ("post", "/api/price-archive/stage"), ("delete", "/api/price-archive/stage/" + "0" * 32),
        ("post", "/api/price-archive/jobs"), ("get", "/api/price-archive/jobs/latest"), ("delete", "/api/price-archive/jobs/abc"),
    )

    def test_every_route_needs_a_session(self):
        stranger = self.app.test_client()
        for method, path in self.ROUTES:
            self.assertEqual(getattr(stranger, method)(path).status_code, 401, path)

    def test_only_the_administrator_may_use_them(self):
        token = self.admin.post("/api/users/invites", json={"username": "anna"}).get_json()["token"]
        anna = self.app.test_client()
        anna.post("/api/invites/password", json={"token": token, "password": ANNA})
        for method, path in self.ROUTES:
            self.assertEqual(getattr(anna, method)(path).status_code, 403, path)
        self.assertTrue(auth.admin_only("/api/price-archive/stage"))
        self.assertEqual(self.staged_files(), [])

    def test_a_token_works_once_and_can_be_discarded(self):
        token = self.stage(gist_zip()).get_json()["token"]
        self.assertEqual(len(self.staged_files()), 1)  # alleen de klaargezette regels; de upload zelf is weg
        self.assertEqual(self.admin.post("/api/price-archive/jobs", json={"token": token}).status_code, 202)
        self.wait()
        self.assertEqual(self.admin.post("/api/price-archive/jobs", json={"token": token}).status_code, 404)
        other = self.stage(gist_zip()).get_json()["token"]
        self.assertEqual(self.admin.delete(f"/api/price-archive/stage/{other}").status_code, 200)
        self.assertEqual(self.admin.post("/api/price-archive/jobs", json={"token": other}).status_code, 404)
        self.assertEqual(self.admin.post("/api/price-archive/jobs", json={"token": "../../auth.sqlite3"}).status_code, 404)
        self.assertEqual(self.staged_files(), [])

    def test_a_finished_job_is_shown_once(self):
        _, job = self.run_import(gist_zip())
        self.assertIsNone(self.admin.get("/api/price-archive/jobs/latest").get_json()["job"])
        self.assertEqual(self.admin.delete(f"/api/price-archive/jobs/{job['id']}").status_code, 200)  # al weg mag ook

    def test_a_failed_import_changes_nothing(self):
        token = self.stage(gist_zip()).get_json()["token"]
        before = self.snapshot()
        with mock.patch.object(price_archive.Plan, "resolve_corrections", side_effect=RuntimeError("stuk")):
            self.assertEqual(self.admin.post("/api/price-archive/jobs", json={"token": token}).status_code, 202)
            job = self.wait()
        self.assertEqual(job["status"], "failed")
        self.assertIn("Er is niets gewijzigd", job["error"])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.staged_files(), [])

    def test_the_job_table_stays_out_of_the_backup(self):
        self.assertIn("price_archive_jobs", backup.EXCLUDED_TABLES)
        self.assertNotIn("price_archive_jobs", backup.DATA_TABLES)


if __name__ == "__main__":
    unittest.main()
