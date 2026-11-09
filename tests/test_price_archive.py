"""Prijsarchief importeren (price_archive.py): de ZIP van de gist, het .tar.gz en het eigen formaat, met alles wat geweigerd wordt."""
import asyncio
import base64
import gzip
import hashlib
import io
import json
import os
import sqlite3
import tarfile
import tempfile
import threading
import time
import unittest
import zipfile
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterator
from unittest import mock

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

from mealieshopper import about, auth, auth_password, backup, deals, price_archive, price_archive_tidy, prices, tasks, uploads  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402
from helpers import AppClient, run_aside  # noqa: E402

if TYPE_CHECKING:  # niet bij de import: pytest zou TestAppProtocol voor een testklasse aanzien
    from quart.typing import TestAppProtocol

PASSWORD = "koffie-met-melk-42"
ANNA = "een-heel-ander-wachtwoord"
TOP = "0123456789abcdef0123456789abcdef-fedcba9876543210fedcba9876543210fedcba98"  # zoals <gist-id>-<revisie>, verzonnen
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


class ArchiveCase(unittest.IsolatedAsyncioTestCase):
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

    async def asyncSetUp(self) -> None:
        # Wat `async with app.test_app()` (de fixture `client`) doet, verdeeld over het begin en het opruimen van de test:
        # de klus van een import loopt als task door tot de test klaar is.
        self.test_app: TestAppProtocol = self.app.test_app()
        await self.test_app.startup()
        self.addAsyncCleanup(self.test_app.shutdown)
        self.admin = self.new_client()
        await self.admin.post("/api/auth/password", json={"username": "admin", "password": PASSWORD})

    def new_client(self) -> AppClient:
        return AppClient(self.test_app.test_client())

    def connect(self) -> sqlite3.Connection:
        """Een synchrone verbinding om rijen klaar te zetten of na te kijken, met de tabellen van de aanbiedingen en prijzen."""
        run_aside(price_archive.ensure_schema())  # ook vanuit een async test: in een eigen loop
        conn = sqlite3.connect(auth.db_path())
        conn.row_factory = sqlite3.Row
        return conn

    # De meeste tests gebruiken folderregels als gewone archiefregels; standaard komen alleen regels binnen die bij een
    # product van jou horen (LinkedOnlyTest). Daarom uploaden deze tests met keep_unlinked, tenzij ze anders vragen.
    keep_unlinked = True

    async def stage(self, data: bytes, client=None):
        url = "/api/price-archive/stage" + ("?keep_unlinked=1" if self.keep_unlinked else "")
        return await (client or self.admin).post(url, data=data, content_type="application/octet-stream")

    async def wait(self, seconds: float = 20) -> dict:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            job = (await self.admin.get("/api/price-archive/jobs/latest")).get_json()["job"]
            if job and job["status"] != "running":
                return job
            await asyncio.sleep(0.05)
        self.fail("de klus is niet klaar gekomen")

    async def run_import(self, data: bytes) -> tuple[dict, dict]:
        staged = await self.stage(data)
        self.assertEqual(staged.status_code, 200, staged.get_json())
        summary = staged.get_json()
        started = await self.admin.post("/api/price-archive/jobs", json={"token": summary["token"]})
        self.assertEqual(started.status_code, 202, started.get_json())
        job = await self.wait()
        self.assertEqual(job["status"], "done", job)
        await self.admin.delete(f"/api/price-archive/jobs/{job['id']}")
        return summary, job

    def rows(self, table: str) -> list[dict]:
        with closing(self.connect()) as conn:
            return [dict(row) for row in conn.execute(f'SELECT * FROM "{table}" ORDER BY rowid')]

    def snapshot(self) -> tuple[list[dict], list[dict]]:
        return self.rows("offer_history"), self.rows("price_observations")

    def staged_files(self) -> list[str]:
        return sorted(path.name for path in self.data.glob(".price-archive-*"))

    async def refused(self, data: bytes, code: str, status: int = 400) -> dict:
        before = self.snapshot()
        response = await self.stage(data)
        body = response.get_json()
        self.assertEqual((response.status_code, body.get("code")), (status, code), body)
        self.assertTrue(body["error"])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.staged_files(), [])
        return body


class GistPackageTest(ArchiveCase):
    async def test_the_zip_from_the_gist_is_checked_shown_and_imported(self):
        summary, job = await self.run_import(gist_zip())
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
        product = (await prices.history("picnic", product="folder:picnicp0001"))["products"][0]
        self.assertEqual(product["observed"][0]["price"], 1.26)
        self.assertEqual(product["regular"], [])
        self.assertEqual(self.staged_files(), [])

    async def test_the_tar_gz_itself_works_too(self):
        summary, job = await self.run_import(tar_gz(gist_files()))
        self.assertEqual(summary["new"], 9)
        self.assertEqual(job["summary"]["offers"] + job["summary"]["prices"], 9)

    async def test_a_zip_without_the_folder_above_works_too(self):
        summary, _ = await self.run_import(gist_zip(top=""))
        self.assertEqual(summary["new"], 9)

    async def test_importing_again_finds_everything_already_there(self):
        await self.run_import(gist_zip())
        before = self.snapshot()
        summary, job = await self.run_import(gist_zip())
        self.assertEqual(summary["new"], 0)
        stores = by_store(summary)
        self.assertEqual(stores["ah"]["offers"], {"new": 0, "duplicate": 2, "covered": 0})
        self.assertEqual(stores["picnic"]["prices"], {"new": 0, "duplicate": 3, "kept": 0})
        self.assertEqual(stores["ah"]["earliest"], "2013-06-03")  # de periode van het archief blijft zichtbaar
        self.assertEqual((job["summary"]["offers"], job["summary"]["prices"]), (0, 0))
        self.assertEqual(self.snapshot(), before)

    async def test_what_the_app_already_has_always_wins(self):
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
        summary, job = await self.run_import(gist_zip())
        self.assertEqual(by_store(summary)["picnic"]["prices"], {"new": 2, "duplicate": 0, "kept": 1})
        self.assertEqual(by_store(summary)["jumbo"]["offers"], {"new": 1, "duplicate": 0, "covered": 1})
        own_price = [row for row in self.rows("price_observations") if row["product_id"] == "folder:picnicp0001"]
        self.assertEqual([(row["price"], row["source"]) for row in own_price], [(9.99, "search")])
        self.assertEqual(len([row for row in self.rows("offer_history") if row["product_id"] == "folder:jumbo0003"]), 1)

    async def test_corrections_change_only_an_earlier_archive_line_whose_old_value_matches(self):
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
        summary, job = await self.run_import(gist_zip(tar_gz(gist_files(**{"ah-corrected/corrections.json": changes}))))
        self.assertEqual(summary["corrections"], {"apply": 1, "already": 1, "missing": 1, "conflict": 1})
        self.assertEqual(job["summary"]["corrections"]["apply"], 1)
        values = {row["url"]: (row["price"], row["price_before"]) for row in self.rows("offer_history") if row["url"] in urls}
        self.assertEqual(values, {urls[0]: (0.5, 0.75), urls[1]: (1.2, 1.5), urls[2]: (0.5, 0.75)})

    async def test_a_correction_never_touches_a_line_from_a_store_of_its_own(self):
        url = f"{FOLDER}/albert_heijn/2013/23/live"
        with closing(self.connect()) as conn, conn:
            conn.execute(
                "INSERT INTO offer_history (store, product_id, title, price, price_before, url, first_seen, last_seen) "
                "VALUES ('ah', '4183', 'AH Bloemkool', 1.0, 1.5, ?, '2013-06-03', '2013-06-09')", (url,),
            )
        changes = json.dumps([correction(url, (1.0, 1.5), (0.5, 0.75))]).encode()
        summary, _ = await self.run_import(gist_zip(tar_gz(gist_files(**{"ah-corrected/corrections.json": changes}))))
        self.assertEqual(summary["corrections"]["conflict"], 1)
        self.assertEqual([row["price"] for row in self.rows("offer_history") if row["url"] == url], [1.0])

    async def test_a_control_character_in_free_text_becomes_a_space(self):
        rows = jsonl([offer(1, promo_label="Pak 250-450\x1b gram, 20 procent voordeel")])
        summary, _ = await self.run_import(gist_zip(tar_gz(gist_files(**{"ah-corrected/observations.jsonl": rows}))))
        self.assertEqual(by_store(summary)["ah"]["offers"]["new"], 1)
        labels = [row["promo_label"] for row in self.rows("offer_history") if row["store"] == "ah"]
        self.assertEqual(labels, ["Pak 250-450 gram, 20 procent voordeel"])

    async def test_an_image_address_from_an_archive_is_not_taken_over(self):
        rows = jsonl([offer(1, image="https://evil.example/pixel.png")])
        await self.run_import(gist_zip(tar_gz(gist_files(**{"ah-corrected/observations.jsonl": rows}))))
        self.assertEqual({row["image"] for row in self.rows("offer_history")}, {""})


class OwnFormatTest(ArchiveCase):
    ROWS = [
        {"type": "correction", "store": "ah", "url": f"{FOLDER}/albert_heijn/2013/23/eerder", "old_price": 1.0, "old_before": 1.5, "new_price": 0.5, "new_before": 0.75},
        {"type": "offer", **offer(7, "jumbo")},
        {"type": "price", **price(8, "crisp", url="https://crisp.nl/download/1/2/x.pdf", unit_size="3 stuks")},
        {"type": "price", **price(9, "picnic")},
    ]

    async def test_a_jsonl_file_with_all_three_kinds_is_imported(self):
        with closing(self.connect()) as conn, conn:
            conn.execute(
                "INSERT INTO offer_history (store, product_id, title, price, price_before, url, first_seen, last_seen) "
                "VALUES ('ah', 'folder:eerder', 'Eerder', 1.0, 1.5, ?, '2013-06-03', '2013-06-09')", (self.ROWS[0]["url"],),
            )
        summary, job = await self.run_import(own(self.ROWS))
        self.assertEqual((summary["format"], summary["source"], summary["license"]), ("mealieshopper-price-archive", "Testarchief", "CC0"))
        self.assertEqual(summary["new"], 4)
        self.assertEqual(job["summary"]["corrections"]["apply"], 1)
        self.assertEqual(len(self.rows("offer_history")), 2)
        self.assertEqual({row["source"] for row in self.rows("price_observations")}, {"public_press_release", "public_observed_unknown"})

    async def test_gzip_works_and_defaults_are_filled_in(self):
        minimal = {"type": "price", "store": "crisp", "product_id": "folder:x", "day": "2024-01-02", "source": "public_press_release", "title": "Avocado", "price": 4.99}
        bare_offer = {k: v for k, v in offer(1, "jumbo").items() if k not in ("ended_at", "syncs", "continued", "personal", "image")}
        await self.run_import(own([minimal, {"type": "offer", **bare_offer}], compress=True))
        observed = self.rows("price_observations")[0]
        self.assertEqual((observed["seen_at"], observed["image"], observed["url"]), ("2024-01-02T00:00:00+00:00", "", None))
        history = self.rows("offer_history")[0]
        self.assertEqual((history["ended_at"], history["syncs"], history["continued"], history["personal"]), (history["last_seen"], 1, 0, 0))


class RefusedTest(ArchiveCase):
    async def test_damaged_or_edited_packages(self):
        def bad_part(manifest, parts):
            name = next(iter(parts))
            parts[name] = b"A" + parts[name][1:]
        await self.refused(gist_zip(tamper=bad_part), "checksum")

        def bad_total(manifest, parts):
            manifest["archiveSha256"] = "0" * 64
        await self.refused(gist_zip(tamper=bad_total), "checksum")

        def path_in_name(manifest, parts):
            manifest["parts"][0]["file"] = "../supermarket-archive.part-001.b64"
        await self.refused(gist_zip(tamper=path_in_name), "bad_structure")

        def missing_part(manifest, parts):
            parts.pop(next(iter(parts)))
        await self.refused(gist_zip(tamper=missing_part), "missing_file")

        files = gist_files()
        honest = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
        edited = dict(files, **{"jumbo/reports/observations.jsonl": jsonl([offer(3, "jumbo", price=0.01)])})
        await self.refused(gist_zip(tar_gz(edited, manifest=honest)), "checksum")

        incomplete = dict(files)
        incomplete.pop("crisp/reports/observations.jsonl")
        await self.refused(gist_zip(tar_gz(incomplete)), "missing_file")

    async def test_the_older_ah_package_and_other_files(self):
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as bundle:
            bundle.writestr(f"{TOP}/manifest.json", json.dumps({"format": "ah-bonus-archive-base64-parts"}))
        await self.refused(out.getvalue(), "old_package")
        await self.refused(b"\x00\x01 dit is geen archief", "not_an_archive")
        await self.refused(b"", "not_an_archive")
        await self.refused(json.dumps({"format": "mealieshopper-backup"}).encode() + b"\n", "not_an_archive")
        await self.refused(gzip.compress(b"geen json"), "not_an_archive")

    async def test_format_versions(self):
        header = {"format": "mealieshopper-price-archive", "formatVersion": 2}
        body = await self.refused(own(OwnFormatTest.ROWS, header=header), "newer_version")
        self.assertIn("2", body["error"])
        await self.refused(own(OwnFormatTest.ROWS, header={"format": "mealieshopper-price-archive"}), "bad_structure")

    async def test_every_line_is_checked(self):
        today = date.today()
        cases = [
            ({"type": "visit"}, "type"),
            ({"type": "offer", **offer(1), "store": "nope"}, "store"),
            ({"type": "price", **price(1, source="receipt")}, "source"),
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
                body = await self.refused(own([OwnFormatTest.ROWS[1], row]), "bad_row")
                self.assertIn("mealieshopper-price-archive", body["error"])
                self.assertIn("3", body["error"])  # het regelnummer: kop, een goede regel, dan deze
        nan = (json.dumps({"format": "mealieshopper-price-archive", "formatVersion": 1}) + "\n").encode() + b'{"type": "price", "price": NaN}\n'
        await self.refused(nan, "bad_row")
        await self.refused(own([]), "empty")

    async def test_a_bad_line_in_the_gist_names_the_file_and_line(self):
        rows = jsonl([offer(3, "jumbo"), {**offer(4, "jumbo"), "store": "nope"}])
        body = await self.refused(gist_zip(tar_gz(gist_files(**{"jumbo/reports/observations.jsonl": rows}))), "bad_row")
        self.assertIn("jumbo/reports/observations.jsonl, regel 2", body["error"])

    async def test_the_message_follows_the_language_of_the_reader(self):
        self.admin.set_cookie("ms_lang", "en")
        body = await self.refused(b"nonsense", "not_an_archive")
        self.assertIn("This is not a price archive", body["error"])

    async def test_size_and_line_limits(self):
        with mock.patch.object(price_archive, "MAX_UPLOAD_BYTES", 1024):
            await self.refused(gist_zip(), "too_large", 413)
        with mock.patch.object(price_archive, "MAX_RECORDS", 3):
            await self.refused(gist_zip(), "too_many")


class ReplaceTest(ArchiveCase):
    """Vervang wat eerder uit een prijsarchief kwam: eerst de oude archiefregels eruit, in dezelfde transactie."""

    def add_live_data(self):
        with closing(self.connect()) as conn, conn:
            conn.execute(
                "INSERT INTO offer_history (store, product_id, title, price, url, valid_from, valid_until, first_seen, last_seen) "
                "VALUES ('ah', '4183', 'AH Bloemkool', 0.99, 'https://www.ah.nl/producten/product/wi4183', '2026-10-26', '2026-11-01', '2026-10-26', '2026-11-01')"
            )
            conn.execute(
                "INSERT INTO offer_history (store, product_id, title, price, url, first_seen, last_seen) "
                "VALUES ('jumbo', '123456PAK', 'Jumbo Koffie', 4.99, NULL, '2026-10-26', '2026-11-01')"
            )
            conn.execute(
                "INSERT INTO price_observations (store, product_id, day, source, title, price, seen_at) "
                "VALUES ('picnic', 's100', '2026-11-01', 'search', 'Melk', 1.19, '2026-11-01T10:00:00+00:00')"
            )

    async def start(self, token: str, replace) -> dict:
        response = await self.admin.post("/api/price-archive/jobs", json={"token": token, "replace": replace})
        self.assertEqual(response.status_code, 202, response.get_json())
        job = await self.wait()
        self.assertEqual(job["status"], "done", job)
        return job

    def smaller(self) -> bytes:
        return own([{"type": "offer", **offer(9, "jumbo")}, {"type": "price", **price(9, "crisp", url="https://crisp.nl/download/9/9/x.pdf")}])

    async def test_only_what_came_from_an_archive_is_replaced(self):
        await self.run_import(gist_zip())
        self.add_live_data()
        staged = (await self.stage(self.smaller())).get_json()
        self.assertEqual(staged["archived"], {"offers": 5, "prices": 4})
        self.assertEqual(staged["new"], 2)
        self.assertEqual(staged["replace"]["new"], 2)
        job = await self.start(staged["token"], True)
        self.assertEqual(job["summary"]["removed"], {"offers": 5, "prices": 4})
        self.assertTrue(job["summary"]["replaced"])
        offers = sorted((row["store"], row["product_id"]) for row in self.rows("offer_history"))
        self.assertEqual(offers, [("ah", "4183"), ("jumbo", "123456PAK"), ("jumbo", "folder:jumbo0009")])
        live = [row for row in self.rows("offer_history") if row["product_id"] == "4183"]
        self.assertEqual(live[0]["url"], "https://www.ah.nl/producten/product/wi4183")  # de eigen aanbieding, niet die uit het archief
        observed = sorted((row["store"], row["source"]) for row in self.rows("price_observations"))
        self.assertEqual(observed, [("crisp", "public_press_release"), ("picnic", "search")])

    async def test_without_the_box_nothing_is_removed(self):
        await self.run_import(gist_zip())
        before = len(self.rows("offer_history")), len(self.rows("price_observations"))
        job = await self.start((await self.stage(self.smaller())).get_json()["token"], False)
        self.assertEqual(job["summary"]["removed"], {"offers": 0, "prices": 0})
        self.assertEqual((len(self.rows("offer_history")), len(self.rows("price_observations"))), (before[0] + 1, before[1] + 1))

    async def test_only_true_replaces(self):
        await self.run_import(gist_zip())
        for value in ("yes", 1, "true"):
            with self.subTest(value=value):
                job = await self.start((await self.stage(self.smaller())).get_json()["token"], value)
                self.assertEqual(job["summary"]["removed"], {"offers": 0, "prices": 0})
                await self.admin.delete(f"/api/price-archive/jobs/{job['id']}")

    async def test_replacing_with_the_same_package_gives_the_same_rows(self):
        await self.run_import(gist_zip())
        def content():
            columns = ("store", "product_id", "title", "price", "price_before", "url", "valid_from", "valid_until")
            return sorted(tuple(row[c] for c in columns) for row in self.rows("offer_history")), sorted(
                (row["store"], row["product_id"], row["day"], row["price"]) for row in self.rows("price_observations"))
        before = content()
        staged = (await self.stage(gist_zip())).get_json()
        self.assertEqual((staged["new"], staged["replace"]["new"]), (0, 9))
        self.assertEqual(by_store(staged["replace"])["ah"]["offers"], {"new": 2, "duplicate": 0, "covered": 0})
        job = await self.start(staged["token"], True)
        self.assertEqual((job["summary"]["offers"], job["summary"]["prices"]), (5, 4))
        self.assertEqual(content(), before)

    async def test_corrections_find_nothing_once_the_archive_is_replaced(self):
        url = f"{FOLDER}/albert_heijn/2013/23/eerder"
        with closing(self.connect()) as conn, conn:
            conn.execute(
                "INSERT INTO offer_history (store, product_id, title, price, price_before, url, first_seen, last_seen) "
                "VALUES ('ah', 'folder:eerder', 'Eerder', 1.0, 1.5, ?, '2013-06-03', '2013-06-09')", (url,),
            )
        changes = json.dumps([correction(url, (1.0, 1.5), (0.5, 0.75))]).encode()
        staged = (await self.stage(gist_zip(tar_gz(gist_files(**{"ah-corrected/corrections.json": changes}))))).get_json()
        self.assertEqual(staged["corrections"]["apply"], 1)
        self.assertEqual(staged["replace"]["corrections"]["missing"], 1)
        job = await self.start(staged["token"], True)
        self.assertEqual(job["summary"]["corrections"]["apply"], 0)
        self.assertEqual([row for row in self.rows("offer_history") if row["url"] == url], [])

    async def test_a_failed_replace_changes_nothing(self):
        await self.run_import(gist_zip())
        self.add_live_data()
        token = (await self.stage(self.smaller())).get_json()["token"]
        before = self.snapshot()
        with mock.patch.object(price_archive.Plan, "resolve_corrections", side_effect=RuntimeError("stuk")):
            self.assertEqual((await self.admin.post("/api/price-archive/jobs", json={"token": token, "replace": True})).status_code, 202)
            job = await self.wait()
        self.assertEqual(job["status"], "failed")
        self.assertEqual(self.snapshot(), before)

    async def test_a_fresh_installation_has_nothing_to_replace(self):
        staged = (await self.stage(gist_zip())).get_json()
        self.assertEqual(staged["archived"], {"offers": 0, "prices": 0})
        self.assertEqual(staged["replace"]["new"], staged["new"])


class TidyTest(ArchiveCase):
    """Folderregels worden bij het importeren opgeschoond (price_archive_tidy.py), hier met keep_unlinked."""

    async def import_ah(self, *rows: dict) -> tuple[dict, list[dict]]:
        files = gist_files(**{"ah-corrected/observations.jsonl": jsonl(list(rows))})
        summary, _ = await self.run_import(gist_zip(tar_gz(files)))
        return summary, [row for row in self.rows("offer_history") if row["store"] == "ah"]

    async def test_a_combination_offer_is_left_out(self):
        summary, rows = (await self.import_ah(
            offer(1, source_title="AH Runderhamburger 2 stuks of gehaktschnitzel Hongaarse stijl 4 stuks"),
            offer(2, source_title="Alle Ajax", description="2e halve prijs, diverse soorten"),
            offer(3, source_title="AH Runderhamburger 4 stuks"),
        ))
        self.assertEqual([row["product_id"] for row in rows], ["folder:ah0003"])
        self.assertEqual(summary["tidied"]["groups"], 2)
        self.assertEqual(by_store(summary)["ah"]["offers"]["new"], 1)

    async def test_without_a_known_pack_the_price_is_left_out(self):
        summary, rows = (await self.import_ah(
            offer(1, source_title="AH Runderhamburgers", description="2e gratis, 2 schalen", unit_size=None),
            offer(2, source_title="AH Runderhamburger 4 stuks", description="voor 3.99", unit_size=None),
        ))
        by_id = {row["product_id"]: row for row in rows}
        self.assertEqual((by_id["folder:ah0001"]["price"], by_id["folder:ah0001"]["price_before"]), (None, None))
        self.assertIsNotNone(by_id["folder:ah0002"]["price"])  # een aantal stuks telt als verpakking
        self.assertEqual(summary["tidied"]["unknownPack"], 1)

    async def test_own_format_lines_are_tidied_by_their_title(self):
        summary, _ = (await self.run_import(own([
            {"type": "offer", **{k: v for k, v in offer(1, "jumbo", title="Folder: Alle Ajax").items() if k != "source_title"}},
            {"type": "offer", **offer(2, "jumbo")},
        ])))
        self.assertEqual(summary["tidied"]["groups"], 1)
        self.assertEqual([row["product_id"] for row in self.rows("offer_history")], ["folder:jumbo0002"])

    def add_own_products(self):
        with closing(self.connect()) as conn, conn:
            conn.execute(
                "INSERT INTO offer_history (store, product_id, title, unit_size, price, url, valid_from, valid_until, first_seen, last_seen) "
                "VALUES ('ah', '8001', 'AH Biologisch Halfvolle melk', '1000 ml', 1.39, 'https://www.ah.nl/producten/product/wi8001', '2026-10-26', '2026-11-01', '2026-10-26', '2026-11-01')"
            )
            # Wat uit een archief kwam, telt niet als jouw product: anders vond de exacte koppeling hier twee producten.
            conn.execute(
                "INSERT INTO offer_history (store, product_id, title, unit_size, price, url, first_seen, last_seen) "
                "VALUES ('ah', '9999', 'AH Biologisch Halfvolle melk', '1000 ml', 1.0, 'https://www.supermarktaanbiedingen.com/folders/albert_heijn/2013/1/x', '2013-01-01', '2013-01-07')"
            )

    async def test_lines_are_linked_to_your_own_products(self):
        self.add_own_products()
        summary, rows = (await self.import_ah(
            offer(1, source_title="AH Biologisch halfvolle melk", description="1 liter", unit_size="1000 ml", price=1.29),
            offer(2, source_title="AH Biologisch halfvolle melk pak", description="per pak", unit_size=None, price=1.29),
            offer(3, source_title="AH Biologisch halfvolle melk of karnemelk", description="2 pakken", unit_size=None),
            offer(4, source_title="Alle Ajax", description="diverse soorten"),
        ))
        # Alleen exact koppelen: dezelfde naam en verpakking. Op woorden ("… pak", geen verpakking) koppelt de app niet meer.
        self.assertEqual(summary["tidied"]["exact"], 1)
        linked = [row for row in rows if row["product_id"] == "8001" and row["url"].startswith(FOLDER)]
        self.assertEqual([(row["url"].rsplit("/", 1)[1], row["title"], row["price"]) for row in linked], [("product-1", "AH Biologisch halfvolle melk", 1.29)])
        # Combi's komen er nooit in, ook niet als ze een product van jou noemen.
        self.assertEqual(summary["tidied"]["groups"], 2)
        self.assertEqual({row["product_id"] for row in rows} & {"folder:ah0003", "folder:ah0004"}, set())
        # Met keep_unlinked blijft de regel zonder koppeling als eigen product, zonder prijs (geen verpakking).
        unlinked = [row for row in rows if row["product_id"] == "folder:ah0002"]
        self.assertEqual([(row["price"], row["price_before"]) for row in unlinked], [(None, None)])
        # In de Prijsgeschiedenis staat de folderregel nu bij het product (plus de eigen aanbieding).
        product = (await prices.history("ah", product="8001"))["products"][0]
        self.assertEqual(product["timesOnOffer"], 2)

    async def test_a_line_already_linked_in_the_archive_keeps_its_product(self):
        summary, rows = (await self.import_ah(
            offer(1, product_id="4183", title="AH Bloemkool", source_title="AH Bloemkool", unit_size="per stuk", match_kind="exact",
                  promo_label="Folder | VOOR: 1.39Los | bron: oud=1.79, actie=1.39"),
            offer(2, product_id="169238", title="AH Runderhamburger 2st", source_title="AH Runderhamburgers of schnitzels", price=None, price_before=None),
        ))
        self.assertEqual([(row["product_id"], row["title"], row["promo_label"]) for row in rows], [("4183", "AH Bloemkool", "voor: 1.39 Los")])
        self.assertEqual(summary["tidied"]["groups"], 1)  # ook een gekoppelde combi komt er niet in
        self.assertEqual((summary["tidied"]["exact"], summary["tidied"]["linkedByArchive"]), (0, 1))

    async def test_a_line_it_cannot_read_is_left_to_the_checks(self):
        body = await self.refused(own([{"type": "offer", **offer(1, unit_size=500)}]), "bad_row")
        self.assertIn("unit_size", body["error"])
        await self.refused(own([{"type": "offer", **offer(1), "store": "nope"}]), "bad_row")


class LinkedOnlyTest(ArchiveCase):
    """Standaard komt een folderregel alleen binnen als hij bij een product van jou hoort: geen "Folder: …"-producten."""

    keep_unlinked = False

    def add_own_products(self):
        with closing(self.connect()) as conn, conn:
            conn.execute(
                "INSERT INTO offer_history (store, product_id, title, unit_size, price, url, valid_from, valid_until, first_seen, last_seen) "
                "VALUES ('ah', '8001', 'AH Biologisch Halfvolle melk', '1000 ml', 1.39, 'https://www.ah.nl/producten/product/wi8001', '2026-10-26', '2026-11-01', '2026-10-26', '2026-11-01')"
            )
            conn.execute(
                "INSERT INTO offer_history (store, product_id, title, unit_size, price, url, valid_from, valid_until, first_seen, last_seen) "
                "VALUES ('picnic', 's1002', 'Prijs 2', '1000 ml', 1.49, NULL, '2026-10-26', '2026-11-01', '2026-10-26', '2026-11-01')"
            )

    async def test_only_lines_that_belong_to_your_products_come_in(self):
        self.add_own_products()
        files = gist_files(**{"ah-corrected/observations.jsonl": jsonl([
            offer(1, source_title="AH Biologisch halfvolle melk", description="1 liter", unit_size="1000 ml", price=1.29),
            offer(2, source_title="AH Excellent tafelversiering", unit_size=None, price=None, price_before=None),
            offer(3, source_title="A-merk speelgoed"),
            offer(4, product_id="4183", title="AH Bloemkool", source_title="AH Bloemkool", unit_size="per stuk"),
        ])})
        summary, job = await self.run_import(gist_zip(tar_gz(files)))
        self.assertFalse(summary["keepUnlinked"])
        self.assertEqual(summary["tidied"]["exact"], 1)
        # Twee AH-regels en de Jumbo- en Picnic-aanbiedingen uit het pakket horen bij geen product van jou.
        self.assertEqual(summary["tidied"]["unlinked"], 2 + 2 + 1)
        offers = self.rows("offer_history")
        self.assertEqual(sorted(row["product_id"] for row in offers if row["url"] and row["url"].startswith("https://www.supermarktaanbiedingen")), ["4183", "8001"])
        self.assertEqual([row for row in offers if row["product_id"].startswith("folder:")], [])
        # Van de prijzen (Picnic en Crisp) hoort er één exact bij een product van jou: die staat bij dat product. De rest niet.
        self.assertEqual((summary["tidied"]["pricesExact"], summary["tidied"]["pricesUnlinked"]), (1, 3))
        prices_in = self.rows("price_observations")
        self.assertEqual([(row["store"], row["product_id"], row["title"], row["brand"], row["source"]) for row in prices_in],
                         [("picnic", "s1002", "Prijs 2", None, "public_observed_unknown")])
        self.assertEqual((job["summary"]["offers"], job["summary"]["prices"]), (2, 1))

    async def test_an_archive_price_needs_the_same_name_and_pack(self):
        self.add_own_products()
        files = gist_files(**{"picnic/reports/observations.jsonl": jsonl([
            price(2, unit_size="500 ml"),  # dezelfde naam, een andere verpakking
            price(9, unit_size="1 l", title="Archief: Prijs 2 met kers"),  # dezelfde verpakking, een andere naam
        ])})
        summary, _ = await self.run_import(gist_zip(tar_gz(files)))
        self.assertEqual((summary["tidied"]["pricesExact"], summary["tidied"]["pricesUnlinked"]), (0, 3))
        self.assertEqual(self.rows("price_observations"), [])

    async def test_a_multipack_with_the_same_text_is_the_same_pack(self):
        with closing(self.connect()) as conn, conn:
            conn.execute(
                "INSERT INTO offer_history (store, product_id, title, unit_size, price, url, valid_from, valid_until, first_seen, last_seen) "
                "VALUES ('picnic', 's1003', 'Affligem blond', '6 x 300 ml', 7.49, NULL, '2026-10-26', '2026-11-01', '2026-10-26', '2026-11-01')"
            )
        files = gist_files(**{"picnic/reports/observations.jsonl": jsonl([
            price(1, title="Archief: Affligem blond", unit_size="6 x 300 ml"),
            price(2, title="Archief: Affligem blond", unit_size="4 x 300 ml"),
        ])})
        summary, _ = await self.run_import(gist_zip(tar_gz(files)))
        self.assertEqual((summary["tidied"]["pricesExact"], summary["tidied"]["pricesUnlinked"]), (1, 2))
        self.assertEqual([(row["product_id"], row["unit_size"]) for row in self.rows("price_observations")], [("s1003", "6 x 300 ml")])

    async def test_an_uncertain_link_from_the_archive_is_left_out(self):
        files = gist_files(**{"ah-corrected/observations.jsonl": jsonl([
            offer(1, product_id="169238", title="AH Runderhamburger 2st", source_title="AH Runderhamburgers", unit_size=None,
                  price=None, price_before=None, match_kind="approximate", promo_label="Folder | per schaal | bron: x | koppeling onzeker: AH Runderhamburgers"),
            offer(2, product_id="4183", title="AH Bloemkool", source_title="AH Bloemkool", unit_size="per stuk", match_kind="exact",
                  promo_label="Folder | VOOR: 1.39Los | bron: oud=1.79, actie=1.39"),
        ])})
        summary, _ = await self.run_import(gist_zip(tar_gz(files)))
        self.assertEqual((summary["tidied"]["uncertain"], summary["tidied"]["linkedByArchive"]), (1, 1))
        archive = [row for row in self.rows("offer_history") if row["url"].startswith(FOLDER)]
        self.assertEqual([(row["product_id"], row["promo_label"]) for row in archive], [("4183", "voor: 1.39 Los")])

    async def test_a_broken_line_still_refuses_the_file_even_if_it_would_be_skipped(self):
        files = gist_files(**{"jumbo/reports/observations.jsonl": jsonl([offer(3, "jumbo", url="http://example.com/x")])})
        await self.refused(gist_zip(tar_gz(files)), "bad_row")

    async def test_an_archive_in_pieces_also_keeps_only_your_products(self):
        # Het scherm uploadt in stukken en zonder keep_unlinked: dan blijven alleen gekoppelde regels over.
        data = gist_zip()
        token = (await self.admin.post("/api/price-archive/uploads", json={"size": len(data)})).get_json()["token"]
        await self.admin.put(f"/api/price-archive/uploads/{token}?offset=0", data=data, content_type="application/octet-stream")
        summary = (await self.admin.post(f"/api/price-archive/uploads/{token}/stage", json={"size": len(data)})).get_json()
        self.assertFalse(summary["keepUnlinked"])
        self.assertEqual(summary["tidied"]["unlinked"], 4)  # folder:ah0001, twee Jumbo-regels en de Picnic-aanbieding
        self.assertEqual(by_store(summary)["ah"]["offers"]["new"], 1)  # 4183 hoorde al bij een product

    async def test_the_route_reads_keep_unlinked(self):
        response = await self.admin.post("/api/price-archive/stage?keep_unlinked=1", data=gist_zip(), content_type="application/octet-stream")
        self.assertTrue(response.get_json()["keepUnlinked"])
        self.assertEqual(response.get_json()["tidied"]["unlinked"], 0)


class ShortLabelTest(unittest.TestCase):
    """De actietekst van een folderregel: alleen de actie, en nooit iets wat de app als actie leest."""

    def test_only_the_action_is_kept(self):
        cases = {
            "Folder | 2 voor: 5.00, 2 schalen | bron: oud=2 stuks 7.50, actie=5.00 | koppeling onzeker: AH Mager rundergehakt": "2 voor: 5.00, 2 schalen",
            "Folder | 2 VOOR: 1.00Los | bron: oud=2 stuks 1.50, actie=1.00": "2 voor: 1.00 Los",
            "Folder | 1 EURO voordeelFles 0.75 liter | bron: x": "1 euro voordeel Fles 0.75 liter",
            "Folder | FrankrijkVOOR: 12.49Fles 0.75 liter* | bron: x | prijs onzeker": "Frankrijk voor: 12.49 Fles 0.75 liter*",
            "2 voor 5 euro": "2 voor 5 euro",  # geen foldertekst: blijft zoals hij is
            None: None,
        }
        for label, expected in cases.items():
            with self.subTest(label=label):
                self.assertEqual(price_archive_tidy.short_label(label), expected)

    def test_the_app_reads_no_action_in_it(self):
        # De prijs uit het archief is al per stuk; als de app "2 voor 5.00" las, zou hij nog een keer delen.
        for label in ("Folder | 2 voor: 5.00, 2 schalen | bron: x", "Folder | 2 VOOR: 1.00Los | bron: x", "Folder | 2e halve-prijs, 2 schalen | bron: x"):
            with self.subTest(label=label):
                self.assertEqual(deals.parse_mechanic(price_archive_tidy.short_label(label))["type"], "unknown")


class ExportTest(ArchiveCase):
    """Prijsarchief exporteren (price_archive_export.py): een ZIP met een bestand per winkel en maand, die de import weer leest."""

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
                ("ah", "4183", "AH Bloemkool", "per stuk", 1.39, 1.79, "voor: 1.39 Los", None,
                 f"{FOLDER}/albert_heijn/2013/23/product-2", "2013-06-03", "2013-06-09", "2013-06-03T00:00:00+00:00", "2013-06-09T23:59:59+00:00", 0),
            ])
            conn.executemany(price_sql, [
                ("ah", "8001", "2026-10-05", "search", "AH Halfvolle melk", "AH", "1 l", None, "https://www.ah.nl/producten/product/wi8001", 1.39, "2026-10-05T10:00:00+00:00"),
                ("picnic", "s1001", "2022-11-30", "public_observed_unknown", "Picnic eieren", None, "10 stuks", None, None, 2.79, "2022-11-30T19:56:18+01:00"),
            ])

    async def export(self, client=None) -> tuple[zipfile.ZipFile, dict]:
        response = await (client or self.admin).post("/api/price-archive/export")
        self.assertEqual(response.status_code, 200, response.get_data()[:300])
        self.assertEqual(response.mimetype, "application/zip")
        self.assertRegex(response.headers["Content-Disposition"], r'attachment; filename="mealieshopper-prijsarchief-\d{8}-\d{6}\.zip"')
        archive = zipfile.ZipFile(io.BytesIO(response.get_data()))
        return archive, json.loads(archive.read("mealieshopper-price-archive.json"))

    @staticmethod
    def lines(archive: zipfile.ZipFile, name: str) -> list[dict]:
        return [json.loads(line) for line in archive.read(name).decode("utf-8").splitlines()]

    async def test_a_file_per_store_and_month_without_personal_offers(self):
        self.fill()
        archive, header = await self.export()
        files = ["offers/ah/2013-06.jsonl", "offers/ah/2026-10.jsonl", "offers/picnic/2026-11.jsonl",
                 "prices/ah/2026-10.jsonl", "prices/picnic/2022-11.jsonl"]
        self.assertEqual(header["files"], files)
        self.assertEqual(sorted(archive.namelist()), sorted(files + ["README.md", "mealieshopper-price-archive.json"]))
        self.assertEqual((header["format"], header["formatVersion"]), ("mealieshopper-price-archive", 1))
        self.assertEqual(header["counts"], {"offers": {"ah": 2, "picnic": 1}, "prices": {"ah": 1, "picnic": 1}})
        # De persoonlijke aanbieding (Bonus Box) staat nergens, ook niet in de README.
        self.assertFalse(any(b"Persoonlijke kaas" in archive.read(name) for name in archive.namelist()))
        melk = self.lines(archive, "offers/ah/2026-10.jsonl")
        self.assertEqual(melk[0]["type"], "offer")
        self.assertEqual((melk[0]["product_id"], melk[0]["image"], melk[0]["price"]), ("8001", "https://static.ah.nl/melk.jpg", 1.19))
        self.assertNotIn("personal", melk[0])
        self.assertIsNone(self.lines(archive, "offers/picnic/2026-11.jsonl")[0]["url"])  # Picnic heeft geen webwinkel
        self.assertEqual(self.lines(archive, "prices/ah/2026-10.jsonl")[0]["source"], "search")
        self.assertIn("| Albert Heijn | 2 | 1 | 2013-06 | 2026-10 |", archive.read("README.md").decode("utf-8"))
        with closing(self.connect()) as conn:
            detail = conn.execute("SELECT detail FROM audit_log WHERE service = 'price-archive' AND action = 'export'").fetchone()
        self.assertEqual(json.loads(detail[0])["offers"], 3)

    async def test_the_same_data_gives_the_same_files(self):
        self.fill()
        first, _ = await self.export()
        second, _ = await self.export()
        for name in first.namelist():
            if name.endswith(".jsonl"):
                self.assertEqual(first.read(name), second.read(name), name)

    async def test_an_export_comes_back_with_the_import(self):
        self.fill()
        before = self.snapshot()
        archive, _ = await self.export()
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as out:
            for name in archive.namelist():
                out.writestr(name, archive.read(name))
        with closing(self.connect()) as conn, conn:
            conn.execute("DELETE FROM offer_history")
            conn.execute("DELETE FROM price_observations")
        summary, job = await self.run_import(data.getvalue())
        self.assertEqual(summary["source"], f"MealieShopper {about.read_version()}")
        self.assertEqual((job["summary"]["offers"], job["summary"]["prices"]), (3, 2))
        offers_after, prices_after = self.snapshot()
        columns = ("store", "product_id", "title", "unit_size", "price", "price_before", "url", "valid_from", "valid_until", "first_seen")
        def plain(rows):
            return sorted(([row[key] for key in columns] for row in rows), key=lambda values: (values[0], values[1]))

        self.assertEqual(plain(offers_after), plain(row for row in before[0] if not row["personal"]))
        # De schapprijs komt terug als archiefregel (archive_search): een gewone prijs, die Vervangen herkent.
        self.assertEqual(sorted((row["store"], row["source"], row["price"]) for row in prices_after),
                         [("ah", "archive_search", 1.39), ("picnic", "public_observed_unknown", 2.79)])
        # Nog een keer: alles staat er al, ook de aanbieding zonder adres.
        again = (await self.stage(data.getvalue())).get_json()
        self.assertEqual(again["new"], 0)
        self.assertEqual(by_store(again)["picnic"]["offers"], {"new": 0, "duplicate": 1, "covered": 0})
        self.assertEqual(again["archived"]["prices"], 2)

    async def test_the_zip_of_a_github_repository_imports(self):
        self.fill()
        archive, _ = await self.export()
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as out:
            out.writestr("prijzen-main/LICENSE", "CC0")
            out.writestr("prijzen-main/.github/notes.md", "x")
            out.writestr("prijzen-main/offers/ah/1999-01.jsonl", "niet in de lijst, dus niet gelezen")
            for name in archive.namelist():
                out.writestr(f"prijzen-main/{name}", archive.read(name))
        with closing(self.connect()) as conn, conn:
            conn.execute("DELETE FROM offer_history")
            conn.execute("DELETE FROM price_observations")
        staged = await self.stage(data.getvalue())
        self.assertEqual(staged.status_code, 200, staged.get_json())
        self.assertEqual(staged.get_json()["new"], 5)

    async def test_a_missing_file_or_a_broken_line_refuses_it(self):
        self.fill()
        archive, header = await self.export()

        def rebuilt(change) -> bytes:
            data = io.BytesIO()
            with zipfile.ZipFile(data, "w") as out:
                for name in archive.namelist():
                    content = change(name, archive.read(name))
                    if content is not None:
                        out.writestr(name, content)
            return data.getvalue()

        missing = await self.stage(rebuilt(lambda name, content: None if name == "prices/ah/2026-10.jsonl" else content))
        self.assertEqual((missing.status_code, missing.get_json()["code"]), (400, "missing_file"))
        self.assertIn("prices/ah/2026-10.jsonl", missing.get_json()["error"])
        broken = await self.stage(rebuilt(lambda name, content: content.replace(b'"store": "ah"', b'"store": "nope"') if name == "offers/ah/2026-10.jsonl" else content))
        self.assertEqual((broken.status_code, broken.get_json()["code"]), (400, "bad_row"))
        self.assertIn("offers/ah/2026-10.jsonl", broken.get_json()["error"])
        outside = dict(header, files=header["files"] + ["../geheim.jsonl"])
        escaped = await self.stage(rebuilt(lambda name, content: json.dumps(outside).encode() if name == "mealieshopper-price-archive.json" else content))
        self.assertEqual((escaped.status_code, escaped.get_json()["code"]), (400, "bad_structure"))
        self.assertEqual(self.staged_files(), [])

    async def test_a_line_the_import_would_refuse_is_left_out(self):
        with closing(self.connect()) as conn, conn:
            conn.execute(
                "INSERT INTO offer_history (store, product_id, title, url, valid_from, valid_until, first_seen, last_seen) "
                "VALUES ('ah', '9001', 'Kapot', 'http://niet-https.example', '2026-10-26', '2026-11-01', '2026-10-26', '2026-11-01')"
            )
        response = await self.admin.post("/api/price-archive/export")
        self.assertEqual(response.headers["X-Export-Skipped"], "1")
        header = json.loads(zipfile.ZipFile(io.BytesIO(response.get_data())).read("mealieshopper-price-archive.json"))
        self.assertEqual((header["files"], header["skipped"]), ([], {"offers": 1, "prices": 0}))


class JobAndAccessTest(ArchiveCase):
    ROUTES = (
        ("get", "/api/price-archive"), ("post", "/api/price-archive/stage"), ("delete", "/api/price-archive/stage/" + "0" * 32),
        ("post", "/api/price-archive/jobs"), ("get", "/api/price-archive/jobs/latest"), ("delete", "/api/price-archive/jobs/abc"),
        ("post", "/api/price-archive/uploads"), ("put", "/api/price-archive/uploads/" + "0" * 32),
        ("delete", "/api/price-archive/uploads/" + "0" * 32), ("post", "/api/price-archive/uploads/" + "0" * 32 + "/stage"),
        ("post", "/api/price-archive/export"), ("post", "/api/price-archive/fetch"),
    )

    async def test_every_route_needs_a_session(self):
        stranger = self.new_client()
        for method, path in self.ROUTES:
            self.assertEqual((await getattr(stranger, method)(path)).status_code, 401, path)

    async def test_only_the_administrator_may_use_them(self):
        token = (await self.admin.post("/api/users/invites", json={"username": "anna"})).get_json()["token"]
        anna = self.new_client()
        await anna.post("/api/invites/password", json={"token": token, "password": ANNA})
        for method, path in self.ROUTES:
            self.assertEqual((await getattr(anna, method)(path)).status_code, 403, path)
        self.assertTrue(auth.admin_only("/api/price-archive/stage"))
        self.assertEqual(self.staged_files(), [])

    async def test_a_token_works_once_and_can_be_discarded(self):
        token = (await self.stage(gist_zip())).get_json()["token"]
        self.assertEqual(len(self.staged_files()), 1)  # alleen de klaargezette regels; de upload zelf is weg
        self.assertEqual((await self.admin.post("/api/price-archive/jobs", json={"token": token})).status_code, 202)
        await self.wait()
        self.assertEqual((await self.admin.post("/api/price-archive/jobs", json={"token": token})).status_code, 404)
        other = (await self.stage(gist_zip())).get_json()["token"]
        self.assertEqual((await self.admin.delete(f"/api/price-archive/stage/{other}")).status_code, 200)
        self.assertEqual((await self.admin.post("/api/price-archive/jobs", json={"token": other})).status_code, 404)
        self.assertEqual((await self.admin.post("/api/price-archive/jobs", json={"token": "../../auth.sqlite3"})).status_code, 404)
        self.assertEqual(self.staged_files(), [])

    async def test_an_archive_in_pieces_gives_the_same_summary_and_imports(self):
        # Het scherm stuurt het archief in stukken (uploads.py): één verzoek van 100 MB komt niet door elke proxy.
        data = gist_zip()
        whole = (await self.stage(data)).get_json()
        await self.admin.delete(f"/api/price-archive/stage/{whole['token']}")
        with mock.patch.object(uploads, "CHUNK_BYTES", 512):
            begun = (await self.admin.post("/api/price-archive/uploads", json={"size": len(data)})).get_json()
            token = begun["token"]
            for offset in range(0, len(data), 512):
                sent = await self.admin.put(f"/api/price-archive/uploads/{token}?offset={offset}", data=data[offset:offset + 512], content_type="application/octet-stream")
                self.assertEqual(sent.status_code, 200, sent.get_json())
        response = await self.admin.post(f"/api/price-archive/uploads/{token}/stage?keep_unlinked=1", json={"size": len(data)})
        self.assertEqual(response.status_code, 200, response.get_json())
        summary = response.get_json()
        self.assertEqual(summary["token"], token)
        self.assertEqual({k: v for k, v in summary.items() if k != "token"}, {k: v for k, v in whole.items() if k != "token"})
        self.assertEqual(self.staged_files(), [f".price-archive-{token}.rows"])  # de upload zelf is weg
        self.assertEqual((await self.admin.post("/api/price-archive/jobs", json={"token": token})).status_code, 202)
        self.assertEqual((await self.wait())["status"], "done")

    async def test_a_refused_archive_in_pieces_leaves_nothing_behind(self):
        token = (await self.admin.post("/api/price-archive/uploads", json={"size": 9})).get_json()["token"]
        await self.admin.put(f"/api/price-archive/uploads/{token}?offset=0", data=b"geen zip!", content_type="application/octet-stream")
        response = await self.admin.post(f"/api/price-archive/uploads/{token}/stage", json={"size": 9})
        self.assertEqual(response.status_code, 400, response.get_json())
        self.assertEqual(self.staged_files(), [])
        too_large = await self.admin.post("/api/price-archive/uploads", json={"size": price_archive.MAX_UPLOAD_BYTES + 1})
        self.assertEqual((too_large.status_code, too_large.get_json()["code"]), (413, "upload_too_large"))

    async def test_a_body_that_is_not_an_object_is_refused_without_a_server_error(self):
        # 3.22 gaf hier een 500 (AttributeError op body.get). Nu telt het als een lichaam zonder size, zoals `{}`.
        for body in ([1], "x", 5):
            begun = await self.admin.post("/api/price-archive/uploads", json=body)
            self.assertEqual((begun.status_code, begun.get_json()["code"]), (400, "upload_empty"), body)
            token = (await self.admin.post("/api/price-archive/uploads", json={"size": 9})).get_json()["token"]
            await self.admin.put(f"/api/price-archive/uploads/{token}?offset=0", data=b"geen zip!", content_type="application/octet-stream")
            response = await self.admin.post(f"/api/price-archive/uploads/{token}/stage", json=body)
            self.assertEqual((response.status_code, response.get_json()["code"]), (409, "upload_incomplete"), body)
        self.assertEqual(self.staged_files(), [])

    async def test_a_finished_job_is_shown_once(self):
        _, job = await self.run_import(gist_zip())
        self.assertIsNone((await self.admin.get("/api/price-archive/jobs/latest")).get_json()["job"])
        self.assertEqual((await self.admin.delete(f"/api/price-archive/jobs/{job['id']}")).status_code, 200)  # al weg mag ook

    async def test_a_failed_import_changes_nothing(self):
        token = (await self.stage(gist_zip())).get_json()["token"]
        before = self.snapshot()
        with mock.patch.object(price_archive.Plan, "resolve_corrections", side_effect=RuntimeError("stuk")):
            self.assertEqual((await self.admin.post("/api/price-archive/jobs", json={"token": token})).status_code, 202)
            job = await self.wait()
        self.assertEqual(job["status"], "failed")
        self.assertIn("Er is niets gewijzigd", job["error"])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.staged_files(), [])

    async def test_the_job_table_stays_out_of_the_backup(self):
        self.assertIn("price_archive_jobs", backup.EXCLUDED_TABLES)
        self.assertNotIn("price_archive_jobs", backup.DATA_TABLES)


class InterruptTest(ArchiveCase):
    """Het afsluiten annuleert de klus (tasks.cancel_all). Het invoegen in zijn thread loopt dan door of niet: de stand van de
    klus moet zeggen wat er met de database gebeurd is, zoals bij het terugzetten van een back-up."""

    async def staged_token(self) -> str:
        staged = await self.stage(gist_zip())
        self.assertEqual(staged.status_code, 200, staged.get_json())
        return staged.get_json()["token"]

    async def begin(self, token: str) -> str:
        started = await self.admin.post("/api/price-archive/jobs", json={"token": token})
        self.assertEqual(started.status_code, 202, started.get_json())
        return started.get_json()["id"]

    async def until_finished(self, seconds: float = 15) -> dict:
        deadline = time.monotonic() + seconds
        job = (await self.admin.get("/api/price-archive/jobs/latest")).get_json()["job"]
        while (job is None or job["status"] != "done") and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
            job = (await self.admin.get("/api/price-archive/jobs/latest")).get_json()["job"]
        return job

    def job_row(self, job_id: str) -> sqlite3.Row:
        with closing(self.connect()) as conn:
            return conn.execute("SELECT * FROM price_archive_jobs WHERE id = ?", (job_id,)).fetchone()

    def import_audits(self) -> list[sqlite3.Row]:
        with closing(self.connect()) as conn:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'audit_log'").fetchone():
                return []  # nog nooit een auditregel geschreven
            return conn.execute("SELECT detail FROM audit_log WHERE service = 'price-archive' AND action = 'import'").fetchall()

    async def audited(self, seconds: float = 5) -> list[sqlite3.Row]:
        deadline = time.monotonic() + seconds
        while not self.import_audits() and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        return self.import_audits()

    def hold_in_transaction(self) -> tuple[threading.Event, threading.Event]:
        """Houdt het invoegen vast midden in zijn transactie (na BEGIN IMMEDIATE, met het schrijfslot), tot de test `release` zet."""
        entered, release = threading.Event(), threading.Event()
        original: Callable[[Path], Iterator[tuple[str, tuple[Any, ...]]]] = price_archive._staged_rows

        def held(path: Path) -> Iterator[tuple[str, tuple[Any, ...]]]:
            rows = original(path)
            first = next(rows)
            entered.set()
            release.wait(15)
            yield first
            yield from rows

        patch = mock.patch.object(price_archive, "_staged_rows", held)
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(release.set)  # de thread blijft nooit hangen
        return entered, release

    async def test_a_cancelled_import_marks_the_job_failed(self) -> None:
        token = await self.staged_token()
        before = self.snapshot()
        waiting = asyncio.Event()

        async def never() -> None:
            waiting.set()
            await asyncio.Event().wait()

        with mock.patch.object(price_archive, "ensure_schema", never):  # de klus komt niet verder dan de voorbereiding
            job_id = await self.begin(token)
            await asyncio.wait_for(waiting.wait(), 5)
            task: asyncio.Task[Any] = tasks.running()[f"price-archive-{job_id}"]
            await tasks.cancel_all()  # wat de app bij het afsluiten doet (after_serving)
        self.assertTrue(task.cancelled())
        job = (await self.admin.get("/api/price-archive/jobs/latest")).get_json()["job"]
        self.assertEqual((job["id"], job["status"]), (job_id, "failed"))
        self.assertIn("onderbroken", job["error"])
        self.assertIn("niets gewijzigd", job["error"])
        row = self.job_row(job_id)
        self.assertEqual(row["error_key"], "interrupted")
        self.assertTrue(row["finished_at"])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.staged_files(), [])
        self.assertEqual(self.import_audits(), [])
        # en daarna kan er weer een starten
        _, again = await self.run_import(gist_zip())
        self.assertEqual(again["summary"]["offers"], 5)

    async def test_an_interrupted_import_whose_insert_still_commits_ends_done_without_an_error(self) -> None:
        # Het afsluiten annuleert de klus terwijl de thread van het invoegen nog niet aan zijn transactie begon. De thread
        # maakt het daarna af: de klus heet dan klaar, zonder de melding "onderbroken, niets gewijzigd".
        token = await self.staged_token()
        entered, release = threading.Event(), threading.Event()
        original: Callable[..., dict[str, Any]] = price_archive.apply

        def gated(*args: Any, **kwargs: Any) -> dict[str, Any]:
            entered.set()
            release.wait(15)
            return original(*args, **kwargs)

        patch = mock.patch.object(price_archive, "apply", gated)
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(release.set)
        job_id = await self.begin(token)
        self.assertTrue(await asyncio.to_thread(entered.wait, 10), "het invoegen is niet begonnen")
        await tasks.cancel_all(timeout=0.5)
        job = (await self.admin.get("/api/price-archive/jobs/latest")).get_json()["job"]
        self.assertEqual(job["status"], "failed")
        self.assertIn("onderbroken", job["error"])
        release.set()
        job = await self.until_finished()
        self.assertEqual((job["id"], job["status"], job["error"]), (job_id, "done", None))
        self.assertEqual((job["summary"]["offers"], job["summary"]["prices"]), (5, 4))
        row = self.job_row(job_id)
        self.assertEqual((row["error_key"], row["error_params"]), (None, None))
        self.assertEqual(len(self.rows("offer_history")), 5)
        self.assertEqual(len(await self.audited()), 1, "het auditlog krijgt de import ook als de klus geannuleerd was")

    async def interrupted_inside_the_transaction(self, commit_after: float) -> tuple[str, dict]:
        """Het afsluiten begint terwijl het invoegen in zijn transactie zit, en die commit `commit_after` seconden later."""
        token = await self.staged_token()
        entered, release = self.hold_in_transaction()
        job_id = await self.begin(token)
        self.assertTrue(await asyncio.to_thread(entered.wait, 10), "het invoegen zit niet in zijn transactie")
        asyncio.get_running_loop().call_later(commit_after, release.set)
        await tasks.cancel_all(timeout=tasks.SHUTDOWN_TIMEOUT)
        return job_id, await self.until_finished()

    async def check_done_and_free(self, job_id: str, job: dict) -> None:
        self.assertEqual((job["id"], job["status"], job["error"]), (job_id, "done", None))
        self.assertEqual(len(self.rows("offer_history")), 5)
        self.assertEqual(len(self.rows("price_observations")), 4)
        self.assertEqual(len(await self.audited()), 1)
        # Geen klus die blijft "lopen": een volgende import kan meteen.
        _, again = await self.run_import(gist_zip())
        self.assertEqual(again["summary"]["offers"], 0)

    async def test_a_commit_one_second_into_the_shutdown_ends_done(self) -> None:
        job_id, job = await self.interrupted_inside_the_transaction(1.0)
        await self.check_done_and_free(job_id, job)

    async def test_a_commit_after_the_lock_wait_of_the_interruption_ends_done(self) -> None:
        # 6,5 s: langer dan de 5 s die aiosqlite op het schrijfslot wacht, dus het vastleggen van "onderbroken" mislukt.
        with self.assertLogs("mealieshopper.price_archive", "WARNING"):
            job_id, job = await self.interrupted_inside_the_transaction(6.5)
        await self.check_done_and_free(job_id, job)


if __name__ == "__main__":
    unittest.main()
