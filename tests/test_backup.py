import base64
import hashlib
import io
import json
import os
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from PIL import Image

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

from mealieshopper import audit, auth, auth_password, backup, factory_reset, image_cache, mealie, mealie_images, oauth, price_archive, prices, profile, uploads  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PASSWORD = "koffie-met-melk-42"
ANNA = "een-heel-ander-wachtwoord"
PASSPHRASE = "een lange zin voor het bestand"
PNG = b"\x89PNG\r\n\x1a\n" + b"avatar" * 5


def product_image(n: int) -> dict:
    """Een echte, kleine afbeelding voor `product_images`, met de waarden die eruit volgen (die rekent het terugzetten opnieuw uit)."""
    out = io.BytesIO()
    Image.new("RGB", (4 + n, 3), (n * 40, 120, 200)).save(out, "PNG")
    data = out.getvalue()
    return {"data": data, "digest": hashlib.sha256(data).hexdigest(), "content_type": "image/png", "width": 4 + n, "height": 3}


SINGLETONS = {"ah_basket", "ah_saved_recipes_meta", "offer_recipes_meta", "offers_schedule"}  # CHECK (id = 1)
SECRETS = {
    mealie.SECRET_URL: "http://mealie.test:9000",
    mealie.SECRET_TOKEN: "MEALIE-TOKEN-12345678",
    "AH_REFRESH_TOKEN": "AH-REFRESH-98765432",
    "PICNIC_AUTH_KEY": "PICNIC-KEY-abcdefgh",
    "JUMBO_REFRESH_TOKEN": "JUMBO-REFRESH-zyxwvuts",
    "CRISP_CLIENT_ID": "CRISPclientID1234567",
    "CRISP_COUNTRY": "nl",
    "AI_ANTHROPIC_KEY": "sk-ant-0000000000000000000000",
    "AI_OPENAI_KEY": "sk-openai-111111111111111111",
}


class BackupCase(unittest.TestCase):
    def setUp(self):
        self.data = self.new_data_dir()
        auth.forget_runtime_state()
        self.addCleanup(auth.forget_runtime_state)
        for patch in (
            mock.patch.object(auth_password, "SCRYPT_N", 2**4),
            mock.patch.object(backup, "SCRYPT_N", 2**14),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        self.app = create_app()
        self.admin = self.app.test_client()
        self.admin.post("/api/auth/password", json={"username": "admin", "password": PASSWORD})

    def new_data_dir(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(os.environ, {"PASSKEY_AUTH_ENABLED": "true", "MEALIESHOPPER_DATA_DIR": tmp.name})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("MEALIESHOPPER_AUTH_SECRET", None)
        os.environ.pop("JWT_SECRET", None)
        return Path(tmp.name)

    # --- gegevens ---

    def connect(self):
        backup.ensure_schemas()
        return auth.connect()

    def seed(self):
        """Twee rijen in elke tabel met gegevens, met waarden naar het type van de kolom."""
        with closing(self.connect()) as conn, conn:
            for table in backup.DATA_TABLES:
                columns = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
                for n in (1, 2):
                    if table in SINGLETONS and n == 2:
                        continue
                    values = {}
                    for index, column in enumerate(columns):
                        name, kind = column["name"], column["type"].upper()
                        if table in SINGLETONS and name == "id":
                            values[name] = 1
                        elif table == "user_profiles" and name == "user_id":
                            values[name] = "owner" if n == 1 else "ghost"
                        elif table == "user_profiles" and name == "avatar":
                            values[name] = PNG
                        elif table == "user_profiles" and name == "avatar_type":
                            values[name] = "image/png"
                        elif table == "product_images" and name in product_image(n):
                            values[name] = product_image(n)[name]
                        elif kind == "INTEGER":
                            values[name] = n * 10 + index
                        elif kind == "REAL":
                            values[name] = n + 0.25
                        elif kind == "BLOB":
                            values[name] = bytes([n, index, 255])
                        else:
                            values[name] = f"{table}.{name}.{n} é€"
                    names = list(values)
                    conn.execute(
                        f'INSERT INTO "{table}" ({", ".join(chr(34) + x + chr(34) for x in names)}) VALUES ({", ".join("?" for _ in names)})',
                        [values[x] for x in names],
                    )

    def snapshot(self) -> dict[str, list[tuple]]:
        with closing(self.connect()) as conn:
            return {
                table: [tuple(row) for row in conn.execute(f'SELECT * FROM "{table}" ORDER BY rowid')]
                for table in backup.DATA_TABLES
            }

    def settings(self) -> dict[str, str]:
        with closing(self.connect()) as conn:
            return {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM app_settings")}

    def wipe(self):
        with closing(self.connect()) as conn, conn:
            for table in backup.DATA_TABLES:
                conn.execute(f'DELETE FROM "{table}"')

    def junk(self):
        with closing(self.connect()) as conn, conn:
            conn.execute("INSERT INTO offers_meta (store, synced_at) VALUES ('junk', 'nu')")
            conn.execute("INSERT INTO ah_lists (id, name, synced_at) VALUES ('junk', 'Rommel', 'nu')")

    # --- aanroepen ---

    def make(self, client=None, **body):
        body.setdefault("links", False)
        response = (client or self.admin).post("/api/backup", json=body)
        data = response.get_data()
        response.close()
        return response, data

    def stage(self, data: bytes, client=None):
        return (client or self.admin).post("/api/backup/stage", data=data, content_type="application/octet-stream")

    def start(self, token, passphrase="", client=None):
        return (client or self.admin).post("/api/backup/jobs", json={"token": token, "passphrase": passphrase})

    def upload(self, data: bytes, chunk: int = 1000, client=None):
        """Zoals het scherm (static/upload.js): beginnen, stukken achter elkaar en dan laten controleren."""
        client = client or self.admin
        with mock.patch.object(uploads, "CHUNK_BYTES", chunk):
            begun = client.post("/api/backup/uploads", json={"size": len(data)})
            self.assertEqual(begun.status_code, 200, begun.get_json())
            token = begun.get_json()["token"]
            self.assertEqual(begun.get_json()["chunkBytes"], chunk)
            for offset in range(0, len(data), chunk):
                sent = client.put(f"/api/backup/uploads/{token}?offset={offset}", data=data[offset:offset + chunk], content_type="application/octet-stream")
                self.assertEqual(sent.get_json(), {"received": min(offset + chunk, len(data))})
        return token, client.post(f"/api/backup/uploads/{token}/stage", json={"size": len(data)})

    def wait(self, client=None, seconds=20):
        client = client or self.admin
        deadline = time.time() + seconds
        while time.time() < deadline:
            job = client.get("/api/backup/jobs/latest").get_json()["job"]
            if job and job["status"] != "running":
                return job
            time.sleep(0.05)
        self.fail("de klus is niet klaar gekomen")

    def restore(self, data: bytes, passphrase="", client=None):
        staged = self.stage(data, client)
        self.assertEqual(staged.status_code, 200, staged.get_json())
        started = self.start(staged.get_json()["token"], passphrase, client)
        self.assertEqual(started.status_code, 202, started.get_json())
        return self.wait(client)

    def tweak(self, data: bytes, change) -> bytes:
        document = json.loads(data)
        change(document)
        return json.dumps(document).encode("utf-8")

    def staged_files(self):
        return sorted(path.name for path in self.data.glob(".restore-*"))


class ClassificationTest(BackupCase):
    def test_every_table_is_either_in_the_backup_or_left_out_on_purpose(self):
        # Een nieuwe tabel met gegevens hoort in de back-up (DATA_TABLES) en in de round-trip-test: dit is de herinnering.
        backup.ensure_schemas()
        for module in (audit, mealie_images, oauth, image_cache):
            with closing(module._connect()):
                pass
        with closing(price_archive._jobs_connect()):
            pass
        with closing(self.connect()) as conn:
            tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertEqual(tables - set(backup.DATA_TABLES) - backup.EXCLUDED_TABLES, set(), "een tabel is niet ingedeeld")
        self.assertEqual(set(backup.DATA_TABLES) & backup.EXCLUDED_TABLES, set())
        self.assertEqual((set(backup.DATA_TABLES) | backup.EXCLUDED_TABLES) - tables - {"backup_jobs"}, set(), "een ingedeelde tabel bestaat niet")

    def test_nothing_that_unlocks_the_app_is_in_the_list(self):
        for table in ("users", "passkey_credentials", "password_credentials", "mfa_totp", "mfa_recovery_codes", "invites", "oauth_tokens", "oauth_codes", "login_attempts"):
            self.assertNotIn(table, backup.DATA_TABLES)

    def test_every_stored_link_is_known(self):
        source = (ROOT / "mealieshopper" / "ah.py").read_text(encoding="utf-8")
        self.assertIn('"AH_REFRESH_TOKEN"', source)
        self.assertEqual(set(backup.SECRET_KEYS), set(SECRETS))
        self.assertTrue(set(backup.PLAIN_SETTINGS).isdisjoint(backup.SECRET_KEYS))
        for key in ("AUTH_MODE", "SESSION_EPOCH", "ONBOARDING_ACTIVE", "ONBOARDING_DONE", "PUBLIC_BASE_URL"):
            self.assertNotIn(key, backup.PLAIN_SETTINGS)


class RoundTripTest(BackupCase):
    def test_public_observed_prices_keep_their_classification_and_provenance(self) -> None:
        with closing(self.connect()) as conn, conn:
            conn.execute(
                "INSERT INTO price_observations (store, product_id, day, source, title, unit_size, url, price, seen_at) "
                "VALUES ('picnic', 'milk', '2024-01-02', 'public_observed_unknown', 'Milk', '1 l', 'https://example.org/archive', 1.29, '2024-01-02')"
            )
        response, data = self.make()
        self.assertEqual(response.status_code, 200)
        self.wipe()
        self.assertEqual(self.restore(data)["status"], "done")
        product: dict = prices.history("picnic", product="milk")["products"][0]
        self.assertEqual(product["observed"], [{
            "date": "2024-01-02", "price": 1.29, "unitSize": "1 l", "sourceUrl": "https://example.org/archive",
        }])
        self.assertEqual(product["regular"], [])
        self.assertIsNone(product["regularPrice"])

    def test_everything_comes_back_with_its_links(self):
        self.seed()
        for key, value in SECRETS.items():
            auth.set_secret(key, value)
        auth.set_setting("AI_PROVIDER", "anthropic")
        auth.set_setting("MCP_ENABLED", "0")
        auth.set_setting("WEEK_START", "6")
        before = self.snapshot()
        self.assertTrue(all(before[table] for table in backup.DATA_TABLES))
        response, data = self.make(links=True, passphrase=PASSPHRASE)
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response.headers["Content-Disposition"])
        self.assertEqual(int(response.headers["Content-Length"]), len(data))
        self.assertIn("X-Backup-Seconds", response.headers)

        self.wipe()
        self.junk()
        for key in SECRETS:
            auth.delete_secret(key)
        auth.set_setting("AI_PROVIDER", "openai")
        auth.set_setting("WEEK_START", "")

        job = self.restore(data, PASSPHRASE)
        self.assertEqual(job["status"], "done", job)
        self.assertIsNone(job["error"])
        expected = dict(before)
        expected["user_profiles"] = [row for row in before["user_profiles"] if row[0] == "owner"]  # "ghost" bestaat hier niet
        self.assertEqual(self.snapshot(), expected)
        self.assertEqual(job["summary"]["skippedProfiles"], 1)
        self.assertEqual(sorted(job["summary"]["links"]), ["ah", "ai_anthropic", "ai_openai", "crisp", "jumbo", "mealie", "picnic"])
        for key, value in SECRETS.items():
            self.assertEqual(auth.get_secret(key), value, key)
        self.assertEqual(auth.get_setting("AI_PROVIDER"), "anthropic")
        self.assertEqual(auth.get_setting("MCP_ENABLED"), "0")
        self.assertEqual(auth.get_setting("WEEK_START"), "6", "de eerste dag van de week gaat mee terug")
        self.assertEqual(self.admin.get("/api/backup/jobs/latest").get_json()["job"]["status"], "done")  # nog niet weggeklikt

    def test_the_links_work_on_another_server_with_another_secret_key(self):
        for key, value in SECRETS.items():
            auth.set_secret(key, value)
        _, data = self.make(links=True, passphrase=PASSPHRASE)
        old_secret = (self.data / "secret.key").read_text()

        self.new_data_dir()
        auth.forget_runtime_state()
        newcomer = self.app.test_client()
        newcomer.post("/api/auth/password", json={"username": "nieuw", "password": PASSWORD})
        job = self.restore(data, PASSPHRASE, newcomer)
        self.assertEqual(job["status"], "done", job)
        self.assertNotEqual((self.data_dir() / "secret.key").read_text(), old_secret)
        for key, value in SECRETS.items():
            self.assertEqual(auth.get_secret(key), value, key)
        self.assertEqual(mealie.mealie_token(), SECRETS[mealie.SECRET_TOKEN])
        self.assertNotIn(SECRETS[mealie.SECRET_TOKEN], self.settings()[mealie.SECRET_TOKEN])  # opgeslagen met de sleutel van deze server

    def data_dir(self) -> Path:
        return auth.data_dir()

    def test_a_backup_without_links_leaves_the_links_as_they_are(self):
        self.seed()
        _, data = self.make(links=False)
        for key, value in SECRETS.items():
            auth.set_secret(key, value)
        self.wipe()
        job = self.restore(data)
        self.assertEqual(job["status"], "done", job)
        self.assertTrue(job["summary"]["linksKept"])
        self.assertEqual(auth.get_secret(mealie.SECRET_TOKEN), SECRETS[mealie.SECRET_TOKEN])

    def test_a_backup_with_links_replaces_the_links_that_are_there(self):
        auth.set_secret("PICNIC_AUTH_KEY", "PICNIC-KEY-abcdefgh")
        _, data = self.make(links=True, passphrase=PASSPHRASE)  # alleen Picnic zit erin
        auth.set_secret("JUMBO_REFRESH_TOKEN", "JUMBO-REFRESH-zyxwvuts")
        self.assertEqual(self.restore(data, PASSPHRASE)["status"], "done")
        self.assertEqual(auth.get_secret("PICNIC_AUTH_KEY"), "PICNIC-KEY-abcdefgh")
        self.assertEqual(auth.get_secret("JUMBO_REFRESH_TOKEN"), "")

    def test_numbers_carry_on_after_a_restore(self):
        self.seed()
        _, data = self.make()
        self.restore(data)
        with closing(self.connect()) as conn, conn:
            top = conn.execute("SELECT MAX(id) FROM offer_history").fetchone()[0]
            conn.execute("INSERT INTO offer_history (store, product_id, title, first_seen, last_seen) VALUES ('ah', 'x', 'y', 'a', 'b')")
            self.assertGreater(conn.execute("SELECT MAX(id) FROM offer_history").fetchone()[0], top)

    def test_a_section_that_is_missing_comes_back_empty(self):
        # Een bestand van vóór een tabel bestond, heeft die sectie niet: de tabel is na het terugzetten leeg.
        self.seed()
        _, data = self.make()
        older = self.tweak(data, lambda doc: doc["tables"].pop("offer_history"))
        self.assertEqual(self.restore(older)["status"], "done")
        after = self.snapshot()
        self.assertEqual(after["offer_history"], [])
        self.assertTrue(after["offers"] and after["ah_orders"])

    def test_a_restore_replaces_what_is_there(self):
        self.seed()
        _, data = self.make()
        self.junk()
        self.assertEqual(self.restore(data)["status"], "done")
        with closing(self.connect()) as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM ah_lists WHERE id = 'junk'").fetchone())
            self.assertIsNone(conn.execute("SELECT 1 FROM offers_meta WHERE store = 'junk'").fetchone())

    def test_the_login_of_the_app_is_not_touched(self):
        self.seed()
        _, data = self.make()
        with closing(self.connect()) as conn:
            before = [tuple(r) for r in conn.execute("SELECT id, username FROM users")], conn.execute("SELECT password_hash FROM password_credentials").fetchall()
        self.assertEqual(self.restore(data)["status"], "done")
        with closing(self.connect()) as conn:
            after = [tuple(r) for r in conn.execute("SELECT id, username FROM users")], conn.execute("SELECT password_hash FROM password_credentials").fetchall()
        self.assertEqual([tuple(r) for r in after[1]], [tuple(r) for r in before[1]])
        self.assertEqual(after[0], before[0])
        self.assertEqual(self.admin.get("/api/backup").status_code, 200)  # de sessie werkt nog


class FileContentTest(BackupCase):
    def test_the_file_holds_nothing_that_unlocks_the_app(self):
        self.seed()
        for key, value in SECRETS.items():
            auth.set_secret(key, value)
        with closing(self.connect()) as conn, conn:
            conn.execute("INSERT INTO passkey_credentials (id, user_id, public_key, credential_name, created_at) VALUES ('CRED-ID-777', 'owner', ?, 'telefoon', 'nu')", (b"PUBLIC-KEY-777",))
            conn.execute("INSERT INTO mfa_recovery_codes (code_hash, user_id, created_at) VALUES ('RECOVERY-HASH-777', 'owner', 'nu')")
            conn.execute("INSERT INTO mfa_totp (user_id, secret, confirmed, created_at) VALUES ('owner', 'TOTP-SECRET-777', 1, 'nu')")
            conn.execute("INSERT INTO invites (id, token_hash, user_id, kind, created_by, created_at, expires_at) VALUES ('i', 'INVITE-HASH-777', 'u', 'new', 'owner', 'nu', 'nu')")
            password_hash = conn.execute("SELECT password_hash FROM password_credentials").fetchone()[0]
            conn.execute("INSERT OR REPLACE INTO app_settings (key, value, updated_at) VALUES ('AUTH_MODE', 'passkey', 'nu')")
        oauth_present = (self.admin.get("/api/backup").status_code == 200)
        self.assertTrue(oauth_present)
        for links in (True, False):
            _, data = self.make(links=links, passphrase=PASSPHRASE)
            text = data.decode("utf-8")
            for needle in (
                password_hash, "CRED-ID-777", "PUBLIC-KEY-777", "RECOVERY-HASH-777", "TOTP-SECRET-777", "INVITE-HASH-777",
                "passkey_credentials", "password_credentials", "mfa_totp", "mfa_recovery_codes", "invites", "oauth_", "login_attempts",
                "AUTH_MODE", "SESSION_EPOCH", "ONBOARDING", "PUBLIC_BASE_URL", (self.data / "secret.key").read_text(),
            ):
                self.assertNotIn(needle, text, needle)

    def test_the_links_are_encrypted_and_the_password_is_not_in_the_file(self):
        for key, value in SECRETS.items():
            auth.set_secret(key, value)
        _, data = self.make(links=True, passphrase=PASSPHRASE)
        text = data.decode("utf-8")
        document = json.loads(text)
        sealed = document["secrets"]["sealed"]
        # Salt en token zijn willekeurige base64: een korte waarde als "nl" (CRISP_COUNTRY) staat daar vaak toevallig in.
        # Elke waarde mag dus nergens buiten die twee staan, een lange waarde nergens in het bestand, en wat in het blok
        # zit is echt versleuteld: alleen met het wachtwoord geeft het precies de koppelingen terug.
        outside = text.replace(sealed["token"], "").replace(sealed["salt"], "")
        for value in SECRETS.values():
            self.assertNotIn(value, outside)
            if len(value) >= 8:
                self.assertNotIn(value, text)
        self.assertEqual(backup.unseal(sealed, PASSPHRASE), SECRETS)
        self.assertNotIn(PASSPHRASE, text)
        self.assertEqual(sorted(document["secrets"]["links"]), ["ah", "ai_anthropic", "ai_openai", "crisp", "jumbo", "mealie", "picnic"])
        self.assertEqual(sealed["kdf"], "scrypt")

    def test_a_backup_without_links_has_none(self):
        for key, value in SECRETS.items():
            auth.set_secret(key, value)
        _, data = self.make(links=False)
        document = json.loads(data)
        self.assertEqual(document["secrets"], {"links": [], "sealed": None})
        self.assertFalse(document["includes_links"])
        for value in SECRETS.values():
            self.assertNotIn(value, data.decode("utf-8"))

    def test_the_header_names_the_format_and_versions(self):
        _, data = self.make()
        document = json.loads(data)
        self.assertEqual((document["format"], document["format_version"]), ("mealieshopper-backup", 1))
        self.assertRegex(document["app_version"], r"^\d+\.\d+\.\d+")
        self.assertTrue(document["created_at"])
        self.assertEqual(list(document["tables"]), list(backup.DATA_TABLES))

    def test_links_need_a_password_of_some_length(self):
        for passphrase in ("", "kort"):
            response = self.admin.post("/api/backup", json={"links": True, "passphrase": passphrase})
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.get_json()["code"], "passphrase_short")
        self.assertEqual(list(self.data.glob(".backup-*")), [])  # ook een geweigerde aanvraag laat niets achter

    def test_the_temporary_file_is_removed_after_the_download(self):
        self.make(links=False)
        self.assertEqual(list(self.data.glob(".backup-*")), [])

    def test_a_link_that_cannot_be_read_is_left_out(self):
        auth.set_secret(mealie.SECRET_TOKEN, "MEALIE-TOKEN-12345678")
        with closing(self.connect()) as conn, conn:
            conn.execute("UPDATE app_settings SET value = 'gAAAAB-een-andere-sleutel' WHERE key = ?", (mealie.SECRET_TOKEN,))
        _, data = self.make(links=True, passphrase=PASSPHRASE)
        self.assertEqual(json.loads(data)["secrets"]["links"], [])


class OlderBackupTest(BackupCase):
    def test_a_backup_from_before_prefer_organic_still_restores(self):
        # #218 gaf user_profiles de kolom prefer_organic; een back-up van daarvoor heeft hem niet en zet de schakelaar uit.
        self.seed()
        with closing(self.connect()) as conn, conn:
            conn.execute("UPDATE user_profiles SET prefer_organic = 1")
        _, data = self.make()

        def older(doc):
            for row in doc["tables"]["user_profiles"]:
                del row["prefer_organic"]

        job = self.restore(self.tweak(data, older))
        self.assertEqual(job["status"], "done", job)
        self.assertFalse(profile.get("owner")["preferOrganic"])

    def test_a_backup_from_before_the_appearance_settings_still_restores(self):
        # De weergave gaf user_profiles de kolommen theme, dark_background en accent; een oudere back-up zet de standaard terug.
        self.seed()
        profile.set_appearance(
            "owner", theme="dark", dark_background="grey", accent="#0a84ff", navbar_style="android", navbar_glass=0, cooking_mode=False,
            preferred_store="crisp",
        )
        _, data = self.make()
        row = json.loads(data)["tables"]["user_profiles"][0]
        self.assertEqual(
            (row["accent"], row["navbar_style"], row["navbar_glass"], row["cooking_mode"], row["preferred_store"]), ("#0a84ff", "android", 0, 0, "crisp"),
            "de weergave en de voorkeurssupermarkt zitten in de back-up",
        )

        def older(doc):
            for row in doc["tables"]["user_profiles"]:
                for column in ("theme", "dark_background", "accent", "navbar_style", "navbar_glass", "cooking_mode", "preferred_store"):
                    del row[column]

        job = self.restore(self.tweak(data, older))
        self.assertEqual(job["status"], "done", job)
        self.assertEqual(
            {key: profile.get("owner")[key] for key in ("theme", "darkBackground", "accent", "navbarStyle", "navbarGlass", "cookingMode", "preferredStore")},
            {"theme": None, "darkBackground": "green", "accent": "", "navbarStyle": "auto", "navbarGlass": 60, "cookingMode": True, "preferredStore": "ah"},
        )


class RefusalTest(BackupCase):
    def setUp(self):
        super().setUp()
        self.seed()
        self.before = self.snapshot()
        _, self.data_bytes = self.make()

    def refused(self, data: bytes, code: str, status=400):
        response = self.stage(data)
        self.assertEqual(response.status_code, status, response.get_json())
        self.assertEqual(response.get_json()["code"], code)
        self.assertEqual(self.staged_files(), [], "een geweigerd bestand blijft niet staan")
        self.assertEqual(self.snapshot(), self.before)
        return response.get_json()

    def test_files_that_are_not_a_backup(self):
        for data in (b"", b"gewoon tekst", b"{kapot", b"[1, 2]", b'{"format": "iets-anders"}', b"PK\x03\x04" + b"0" * 100, b'{"format": "mealieshopper-backup", "format_version": NaN}'):
            self.refused(data, "not_a_backup")

    def test_a_newer_version_names_the_version_that_wrote_it(self):
        newer = self.tweak(self.data_bytes, lambda doc: doc.update(format_version=2, app_version="9.9.9"))
        body = self.refused(newer, "newer_version")
        self.assertIn("9.9.9", body["error"])
        self.assertIn("bij", body["error"])  # "Werk MealieShopper eerst bij"

    def test_parts_that_do_not_belong_are_refused(self):
        for table in ("passkey_credentials", "users", "oauth_tokens", "audit_log", "sqlite_master", "niet_bestaand"):
            data = self.tweak(self.data_bytes, lambda doc, t=table: doc["tables"].update({t: [{"id": "x"}]}))
            self.assertEqual(self.refused(data, "bad_table")["error"].count(table), 1)

    def test_a_file_cannot_switch_off_the_login_or_plant_a_setting(self):
        for key in ("AUTH_MODE", "SESSION_EPOCH", "ONBOARDING_DONE", "PUBLIC_BASE_URL", "iets"):
            data = self.tweak(self.data_bytes, lambda doc, k=key: doc["settings"].update({k: "none"}))
            self.refused(data, "bad_setting")
        self.assertTrue(auth.auth_required())

    def test_a_row_that_does_not_fit_names_the_table_and_the_line(self):
        def spoil(doc):
            doc["tables"]["jumbo_orders"][1]["state"] = {"niet": "toegestaan"}

        body = self.refused(self.tweak(self.data_bytes, spoil), "bad_row")
        self.assertIn("jumbo_orders", body["error"])

    def test_duplicates_and_missing_required_values_are_found(self):
        def duplicate(doc):
            doc["tables"]["ah_orders"].append(dict(doc["tables"]["ah_orders"][0]))

        def missing(doc):
            del doc["tables"]["offers"][0]["title"]

        body = self.refused(self.tweak(self.data_bytes, duplicate), "bad_row")
        self.assertRegex(body["error"], r"ah_orders, regel 3")
        body = self.refused(self.tweak(self.data_bytes, missing), "bad_row")
        self.assertRegex(body["error"], r"offers, regel 1")

    def test_an_unknown_column_is_refused(self):
        def extra(doc):
            doc["tables"]["ah_lists"][0]["geheim"] = "x"

        self.assertIn("geheim", self.refused(self.tweak(self.data_bytes, extra), "bad_row")["error"])

    def test_an_avatar_is_read_again_and_not_taken_on_the_files_word(self):
        def spoil(doc):
            doc["tables"]["user_profiles"][0]["avatar"] = {"base64": "PHN2Zz48L3N2Zz4="}  # <svg></svg>, maar het bestand zegt image/png

        self.assertIn("user_profiles", self.refused(self.tweak(self.data_bytes, spoil), "bad_row")["error"])

        def broken(doc):
            doc["tables"]["user_profiles"][0]["avatar"] = {"base64": "***"}

        self.refused(self.tweak(self.data_bytes, broken), "bad_row")

    def test_a_product_image_is_read_again_and_not_taken_on_the_files_word(self):
        def spoil(doc):
            doc["tables"]["product_images"][0]["data"] = {"base64": "PHN2Zz48L3N2Zz4="}  # <svg></svg>

        self.assertIn("product_images", self.refused(self.tweak(self.data_bytes, spoil), "bad_row")["error"])

        def too_wide(doc):
            out = io.BytesIO()
            Image.new("RGB", (1500, 10)).save(out, "PNG")
            doc["tables"]["product_images"][0]["data"] = {"base64": base64.b64encode(out.getvalue()).decode("ascii")}

        self.assertIn("1024", self.refused(self.tweak(self.data_bytes, too_wide), "bad_row")["error"])

    def test_what_follows_from_a_product_image_is_worked_out_again(self):
        def lie(doc):
            row = doc["tables"]["product_images"][0]
            row.update(content_type="image/webp", width=999, height=999, digest="niet-waar")

        self.assertEqual(self.restore(self.tweak(self.data_bytes, lie))["status"], "done")
        with closing(self.connect()) as conn:
            row = conn.execute("SELECT * FROM product_images ORDER BY id LIMIT 1").fetchone()
        expected = product_image(1)
        self.assertEqual((row["content_type"], row["width"], row["height"], row["digest"]),
                         (expected["content_type"], expected["width"], expected["height"], expected["digest"]))

    def test_a_file_over_the_limit_is_refused_and_not_kept(self):
        with mock.patch.object(backup, "MAX_STAGE_BYTES", 1000):
            self.refused(self.data_bytes, "too_large", 413)

    def test_one_bad_line_stops_the_whole_restore(self):
        def spoil(doc):
            doc["tables"]["picnic_orders"][0]["order_id"] = None

        data = self.tweak(self.data_bytes, spoil)
        self.junk()
        junk_state = self.snapshot()
        response = self.stage(data)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.snapshot(), junk_state)

    def test_a_tampered_encrypted_block_is_refused(self):
        auth.set_secret(mealie.SECRET_TOKEN, "MEALIE-TOKEN-12345678")
        _, data = self.make(links=True, passphrase=PASSPHRASE)
        auth.delete_secret(mealie.SECRET_TOKEN)
        # De inhoud wordt pas met het wachtwoord gelezen, dus deze bestanden komen wel klaar te staan en falen bij het starten.
        for change in (
            lambda doc: doc["secrets"]["sealed"].update(token="x" + doc["secrets"]["sealed"]["token"][1:]),
            lambda doc: doc["secrets"]["sealed"].update(n=2**30),  # de kosten staan in het bestand: een te hoge waarde mag de server niet vastzetten
            lambda doc: doc["secrets"]["sealed"].update(kdf="md5"),
        ):
            staged = self.stage(self.tweak(data, change))
            self.assertEqual(staged.status_code, 200)
            started = self.start(staged.get_json()["token"], PASSPHRASE)
            self.assertIn(started.status_code, (400, 403))
            self.admin.delete(f"/api/backup/stage/{staged.get_json()['token']}")
        # Koppelingen aangekondigd zonder versleuteld blok: dat is een beschadigd bestand.
        self.assertEqual(self.stage(self.tweak(data, lambda doc: doc["secrets"].update(sealed=None))).status_code, 400)
        self.assertEqual(auth.get_secret(mealie.SECRET_TOKEN), "")
        self.assertEqual(self.staged_files(), [])

    def test_secrets_in_the_block_must_be_known_links(self):
        sealed = backup.seal({"AUTH_MODE": "none"}, PASSPHRASE)
        auth.set_secret(mealie.SECRET_TOKEN, "MEALIE-TOKEN-12345678")
        _, data = self.make(links=True, passphrase=PASSPHRASE)
        planted = self.tweak(data, lambda doc: doc["secrets"].update(sealed=sealed))
        staged = self.stage(planted)
        self.assertEqual(staged.status_code, 200)
        started = self.start(staged.get_json()["token"], PASSPHRASE)
        self.assertEqual(started.status_code, 400)
        self.assertEqual(started.get_json()["code"], "bad_setting")


class StageTest(BackupCase):
    def test_the_stage_answers_with_a_summary_and_changes_nothing(self):
        self.seed()
        before, settings = self.snapshot(), self.settings()
        _, data = self.make(links=False)
        self.wipe()
        wiped = self.snapshot()
        response = self.stage(data)
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertRegex(body["token"], r"^[0-9a-f]{32}$")
        self.assertEqual(body["counts"]["lists"], 4)  # twee rijen in elke van de twee tabellen met lijstjes (AH en Jumbo)
        self.assertEqual(body["counts"]["offerHistory"], 2)
        self.assertEqual(body["counts"]["prices"], 2)
        self.assertEqual(body["links"], [])
        self.assertFalse(body["needsPassphrase"])
        self.assertTrue(body["createdAt"] and body["appVersion"])
        self.assertEqual(self.snapshot(), wiped)
        self.assertNotEqual(before, wiped)
        self.assertEqual(self.settings(), settings)
        self.assertEqual(len(self.staged_files()), 1)

    def test_changing_your_mind_removes_the_file(self):
        token = self.stage(self.make()[1]).get_json()["token"]
        self.assertEqual(self.admin.delete(f"/api/backup/stage/{token}").status_code, 200)
        self.assertEqual(self.staged_files(), [])
        self.assertEqual(self.start(token).status_code, 404)

    def test_a_token_must_look_like_a_token(self):
        for token in ("../x", "..%2Fsecret", "x" * 32, "A" * 32, "0" * 31, ""):
            self.assertEqual(self.start(token).status_code, 404, token)
        self.assertEqual(self.admin.delete("/api/backup/stage/..").status_code, 404)

    def test_a_token_works_once(self):
        token = self.stage(self.make()[1]).get_json()["token"]
        self.assertEqual(self.start(token).status_code, 202)
        self.wait()
        self.assertEqual(self.start(token).status_code, 404)
        self.assertEqual(self.staged_files(), [])

    def test_staged_files_that_were_never_used_are_cleared_the_next_time(self):
        old = self.data / (".restore-" + "a" * 32)
        old.write_bytes(b"oud")
        os.utime(old, (time.time() - 2 * 3600, time.time() - 2 * 3600))
        young = self.data / (".restore-" + "b" * 32)
        young.write_bytes(b"jong")
        self.assertEqual(self.stage(self.make()[1]).status_code, 200)
        self.assertFalse(old.exists())
        self.assertTrue(young.exists())

    def test_a_factory_reset_takes_a_staged_file_along(self):
        self.stage(self.make()[1])
        removed = factory_reset.wipe_data()
        self.assertEqual(self.staged_files(), [])
        self.assertTrue(any(name.startswith(".restore-") for name in removed))

    def test_the_staged_file_is_not_readable_by_others(self):
        self.stage(self.make()[1])
        path = next(self.data.glob(".restore-*"))
        self.assertEqual(path.stat().st_mode & 0o077, 0)


class ChunkedUploadTest(BackupCase):
    """Het scherm stuurt een back-up in stukken (uploads.py): één verzoek van 100 MB komt niet door elke proxy."""

    def parts(self):
        return sorted(path.name for path in self.data.glob(".restore-*.part"))

    def put(self, token, offset, data: bytes):
        return self.admin.put(f"/api/backup/uploads/{token}?offset={offset}", data=data, content_type="application/octet-stream")

    def begin(self, size):
        return self.admin.post("/api/backup/uploads", json={"size": size})

    def test_a_backup_in_pieces_gives_the_same_summary_and_restores(self):
        self.seed()
        before, settings = self.snapshot(), self.settings()
        _, data = self.make(links=False)
        self.assertGreater(len(data), 3000)  # dus echt meerdere stukken
        whole = self.stage(data).get_json()
        self.wipe()
        token, staged = self.upload(data)
        self.assertEqual(staged.status_code, 200, staged.get_json())
        body = staged.get_json()
        self.assertEqual(body["token"], token)
        self.assertEqual({k: v for k, v in body.items() if k != "token"}, {k: v for k, v in whole.items() if k != "token"})
        self.assertEqual(self.parts(), [])
        self.assertIn(".restore-" + token, self.staged_files())
        self.assertEqual(self.start(token).status_code, 202)
        self.assertEqual(self.wait()["status"], "done")
        expected = dict(before)
        expected["user_profiles"] = [row for row in before["user_profiles"] if row[0] == "owner"]  # "ghost" bestaat hier niet
        self.assertEqual(self.snapshot(), expected)
        self.assertEqual(self.settings(), settings)

    def test_a_part_that_does_not_follow_on_gets_what_the_server_has(self):
        _, data = self.make()
        token = self.begin(len(data)).get_json()["token"]
        self.assertEqual(self.put(token, 0, data[:100]).get_json(), {"received": 100})
        for offset in (0, 50, 200, "x", ""):  # het antwoord ging verloren en het stuk komt nog eens, of het sluit niet aan
            response = self.admin.put(f"/api/backup/uploads/{token}?offset={offset}", data=data[100:200], content_type="application/octet-stream")
            self.assertEqual(response.status_code, 409, offset)
            self.assertEqual(response.get_json()["received"], 100)
            self.assertEqual(response.get_json()["code"], "upload_out_of_order")
        self.assertEqual((self.data / f".restore-{token}.part").read_bytes(), data[:100])  # niets dubbel geplakt
        self.assertEqual(self.put(token, 100, data[100:]).get_json(), {"received": len(data)})
        self.assertEqual(self.admin.post(f"/api/backup/uploads/{token}/stage", json={"size": len(data)}).status_code, 200)

    def test_a_file_that_is_too_large_is_refused_before_the_first_byte(self):
        response = self.begin(backup.MAX_STAGE_BYTES + 1)
        self.assertEqual((response.status_code, response.get_json()["code"]), (413, "upload_too_large"))
        self.assertIn("200 MB", response.get_json()["error"])
        for size in (0, -1, "10", None, True, 1.5):
            self.assertEqual(self.begin(size).status_code, 400, size)
        self.assertEqual(self.parts(), [])

    def test_a_part_larger_than_agreed_is_refused_and_leaves_nothing_behind(self):
        token = self.begin(5000).get_json()["token"]
        self.put(token, 0, b"{" * 10)
        with mock.patch.object(uploads, "CHUNK_BYTES", 100):
            response = self.put(token, 10, b"x" * 101)
        self.assertEqual((response.status_code, response.get_json()["code"]), (413, "upload_chunk_too_large"))
        self.assertEqual((self.data / f".restore-{token}.part").read_bytes(), b"{" * 10)

    def test_the_upload_never_grows_past_the_limit(self):
        token = uploads.begin(backup.STAGE_PREFIX, 10, 10)["token"]
        with self.assertRaises(uploads.UploadError) as caught:
            uploads.append(backup.STAGE_PREFIX, token, 0, io.BytesIO(b"x" * 11), None, 10)
        self.assertEqual((caught.exception.key, caught.exception.status), ("too_large", 413))
        self.assertEqual((self.data / f".restore-{token}.part").read_bytes(), b"")  # een half stuk blijft niet staan

    def test_an_incomplete_upload_is_not_checked_and_is_removed(self):
        _, data = self.make()
        token = self.begin(len(data)).get_json()["token"]
        self.put(token, 0, data[:100])
        response = self.admin.post(f"/api/backup/uploads/{token}/stage", json={"size": len(data)})
        self.assertEqual((response.status_code, response.get_json()["code"]), (409, "upload_incomplete"))
        self.assertEqual(self.staged_files(), [])
        self.assertEqual(self.put(token, 100, data[100:]).status_code, 404)

    def test_a_refused_file_in_pieces_leaves_nothing_behind(self):
        token, response = self.upload(b'{"format": "iets anders", "tables": {}}', chunk=7)
        self.assertEqual((response.status_code, response.get_json()["code"]), (400, "not_a_backup"))
        self.assertEqual(self.staged_files(), [])
        self.assertEqual(self.start(token).status_code, 404)

    def test_tokens_must_look_like_tokens(self):
        for token in ("..", "x" * 32, "0" * 31, "0" * 32):
            self.assertEqual(self.put(token, 0, b"{").status_code, 404, token)
            self.assertEqual(self.admin.post(f"/api/backup/uploads/{token}/stage", json={"size": 1}).status_code, 404, token)
        self.assertEqual(self.admin.delete("/api/backup/uploads/..").status_code, 404)

    def test_changing_your_mind_halfway_removes_the_part(self):
        token = self.begin(100).get_json()["token"]
        self.put(token, 0, b"{" * 10)
        self.assertEqual(self.admin.delete(f"/api/backup/uploads/{token}").status_code, 200)
        self.assertEqual(self.parts(), [])

    def test_an_abandoned_upload_is_cleared_when_a_new_one_begins(self):
        old = self.data / (".restore-" + "a" * 32 + ".part")
        old.write_bytes(b"oud")
        os.utime(old, (time.time() - 2 * 3600, time.time() - 2 * 3600))
        young = self.data / (".restore-" + "b" * 32 + ".part")
        young.write_bytes(b"jong")
        self.assertEqual(self.begin(10).status_code, 200)
        self.assertFalse(old.exists())
        self.assertTrue(young.exists())

    def test_a_part_is_private_and_goes_with_a_factory_reset(self):
        token = self.begin(100).get_json()["token"]
        self.put(token, 0, b"{" * 10)
        path = self.data / f".restore-{token}.part"
        self.assertEqual(path.stat().st_mode & 0o077, 0)
        self.assertIn(path.name, factory_reset.wipe_data())
        self.assertFalse(path.exists())


class PassphraseTest(BackupCase):
    def prepare(self):
        auth.set_secret(mealie.SECRET_TOKEN, "MEALIE-TOKEN-12345678")
        _, data = self.make(links=True, passphrase=PASSPHRASE)
        auth.delete_secret(mealie.SECRET_TOKEN)
        return data

    def test_the_summary_says_a_password_is_needed(self):
        body = self.stage(self.prepare()).get_json()
        self.assertTrue(body["needsPassphrase"])
        self.assertEqual(body["links"], ["mealie"])

    def test_a_missing_or_wrong_password_changes_nothing_and_keeps_the_file(self):
        token = self.stage(self.prepare()).get_json()["token"]
        for passphrase, code, status in (("", "needs_passphrase", 400), ("verkeerd wachtwoord", "wrong_passphrase", 403)):
            response = self.start(token, passphrase)
            self.assertEqual((response.status_code, response.get_json()["code"]), (status, code))
            self.assertEqual(self.staged_files(), [".restore-" + token])
        self.assertEqual(auth.get_secret(mealie.SECRET_TOKEN), "")
        self.assertEqual(self.admin.get("/api/backup/jobs/latest").get_json()["job"], None)
        self.assertEqual(self.start(token, PASSPHRASE).status_code, 202)  # en meteen opnieuw proberen kan
        self.assertEqual(self.wait()["status"], "done")
        self.assertEqual(auth.get_secret(mealie.SECRET_TOKEN), "MEALIE-TOKEN-12345678")

    def test_the_password_is_never_stored(self):
        data = self.prepare()
        self.restore(data, PASSPHRASE)
        for path in self.data.iterdir():
            if path.is_file():
                self.assertNotIn(PASSPHRASE.encode(), path.read_bytes(), path.name)


class AccessTest(BackupCase):
    ROUTES = (
        ("get", "/api/backup"), ("post", "/api/backup"), ("post", "/api/backup/stage"), ("delete", "/api/backup/stage/" + "0" * 32),
        ("post", "/api/backup/jobs"), ("get", "/api/backup/jobs/latest"), ("delete", "/api/backup/jobs/abc"),
        ("post", "/api/backup/uploads"), ("put", "/api/backup/uploads/" + "0" * 32), ("delete", "/api/backup/uploads/" + "0" * 32),
        ("post", "/api/backup/uploads/" + "0" * 32 + "/stage"),
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
        self.assertTrue(auth.admin_only("/api/backup/stage"))
        self.assertEqual(list(self.data.glob(".restore-*")), [])


class JobTest(BackupCase):
    def test_a_job_reports_steps_and_timings(self):
        self.seed()
        _, data = self.make()
        job = self.restore(data)
        self.assertEqual(job["status"], "done")
        self.assertEqual(job["phase"], "replacing")
        self.assertIsNotNone(job["checkingSeconds"])
        self.assertIsNotNone(job["replacingSeconds"])
        self.assertEqual(job["summary"]["counts"]["offers"], 2)
        with closing(self.connect()) as conn:
            row = conn.execute("SELECT * FROM backup_jobs").fetchone()
        self.assertEqual(row["status"], "done")

    def test_progress_counts_the_tables_while_checking(self):
        self.seed()
        _, data = self.make()
        plan = backup.prepare(backup.read_file(self._write(data)))
        seen = []
        backup.check_rows(plan, lambda step, steps: seen.append((step, steps)))
        self.assertEqual(seen[0], (1, len(backup.DATA_TABLES)))
        self.assertEqual(seen[-1], (len(backup.DATA_TABLES), len(backup.DATA_TABLES)))

    def _write(self, data: bytes) -> Path:
        path = self.data / "voorbeeld.json"
        path.write_bytes(data)
        return path

    def test_a_failure_halfway_changes_nothing_and_says_so(self):
        self.seed()
        auth.set_secret(mealie.SECRET_TOKEN, "MEALIE-TOKEN-12345678")
        _, data = self.make(links=True, passphrase=PASSPHRASE)
        self.junk()
        before, settings = self.snapshot(), self.settings()
        staged = self.stage(data).get_json()["token"]
        # Pas na het wissen en invoegen van alle tabellen gaat het mis: de transactie moet alles terugdraaien.
        with mock.patch.object(auth, "secret_box", side_effect=RuntimeError("stuk")), self.assertLogs("mealieshopper.backup", "ERROR"):
            self.assertEqual(self.start(staged, PASSPHRASE).status_code, 202)
            job = self.wait()
        self.assertEqual(job["status"], "failed")
        self.assertIn("niets gewijzigd", job["error"])
        self.assertNotIn("stuk", job["error"])  # de beheerder krijgt een zin, het logboek de fout
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.settings(), settings)
        self.assertEqual(self.staged_files(), [])

    def test_done_is_part_of_the_swap_so_the_two_cannot_disagree(self):
        self.seed()
        _, data = self.make()
        plan = backup.prepare(backup.read_file(self._write(data)))
        prepared = backup.check_rows(plan)
        self.junk()
        before = self.snapshot()

        def refuse(conn, summary):
            raise RuntimeError("het markeren als klaar mislukt")

        with self.assertRaises(RuntimeError):
            backup.replace_everything(plan, prepared, refuse)
        self.assertEqual(self.snapshot(), before)  # de gegevens zijn pas vervangen als de klus ook als klaar is gemarkeerd

    def test_a_refused_file_in_the_job_fails_with_its_reason(self):
        _, data = self.make()
        staged = self.stage(data).get_json()["token"]
        path = self.data / (".restore-" + staged)
        path.write_bytes(b"niet meer wat het was")  # het bestand wordt in de klus opnieuw gecontroleerd
        started = self.start(staged)
        self.assertEqual(started.status_code, 400)
        self.assertEqual(started.get_json()["code"], "not_a_backup")

    def test_only_one_job_runs_at_a_time(self):
        now = backup._now()
        with closing(self.connect()) as conn, conn:
            conn.execute("INSERT INTO backup_jobs (id, status, phase, started_at, updated_at) VALUES ('lopend', 'running', 'checking', ?, ?)", (now, now))
        staged = self.stage(self.make()[1]).get_json()["token"]
        response = self.start(staged)
        self.assertEqual((response.status_code, response.get_json()["code"]), (409, "busy"))
        self.assertEqual(self.staged_files(), [".restore-" + staged])  # het bestand blijft voor een nieuwe poging
        self.assertEqual(self.admin.get("/api/backup/jobs/latest").get_json()["job"]["status"], "running")

    def test_a_job_that_lost_its_thread_is_reported_as_interrupted(self):
        old = backup._now()
        with closing(self.connect()) as conn, conn:
            conn.execute(
                "INSERT INTO backup_jobs (id, status, phase, started_at, updated_at) VALUES ('weg', 'running', 'replacing', '2020-01-01T00:00:00+00:00', '2020-01-01T00:00:00+00:00')"
            )
        job = self.admin.get("/api/backup/jobs/latest").get_json()["job"]
        self.assertEqual(job["status"], "failed")
        self.assertIn("onderbroken", job["error"])
        self.assertIn("niets gewijzigd", job["error"])
        self.assertTrue(old)
        # en daarna kan er weer een starten
        self.assertEqual(self.restore(self.make()[1])["status"], "done")

    def test_a_finished_job_is_shown_until_it_is_dismissed(self):
        job = self.restore(self.make()[1])
        again = self.admin.get("/api/backup/jobs/latest").get_json()["job"]
        self.assertEqual(again["id"], job["id"])
        self.assertEqual(self.admin.delete(f"/api/backup/jobs/{job['id']}").status_code, 200)
        self.assertIsNone(self.admin.get("/api/backup/jobs/latest").get_json()["job"])
        self.assertEqual(self.admin.delete("/api/backup/jobs/onbekend").status_code, 404)

    def test_a_running_job_cannot_be_dismissed(self):
        now = backup._now()
        with closing(self.connect()) as conn, conn:
            conn.execute("INSERT INTO backup_jobs (id, status, phase, started_at, updated_at) VALUES ('lopend', 'running', 'checking', ?, ?)", (now, now))
        self.assertEqual(self.admin.delete("/api/backup/jobs/lopend").status_code, 404)
        self.assertEqual(self.admin.get("/api/backup/jobs/latest").get_json()["job"]["status"], "running")

    def test_the_messages_follow_the_language_of_the_reader(self):
        _, data = self.make()
        self.admin.set_cookie("ms_lang", "en")
        response = self.admin.post("/api/backup/stage", data=b"kapot")
        self.assertIn("not a MealieShopper backup", response.get_json()["error"])
        self.admin.set_cookie("ms_lang", "nl")
        response = self.admin.post("/api/backup/stage", data=b"kapot")
        self.assertIn("geen MealieShopper-back-up", response.get_json()["error"])

    def test_the_job_table_is_not_part_of_the_backup_and_survives_a_restore(self):
        self.assertNotIn("backup_jobs", backup.DATA_TABLES)
        job = self.restore(self.make()[1])
        with closing(self.connect()) as conn:
            self.assertEqual(conn.execute("SELECT id FROM backup_jobs").fetchone()["id"], job["id"])


class PageTest(unittest.TestCase):
    HTML = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    JS = (ROOT / "static" / "backup.js").read_text(encoding="utf-8")
    UPLOAD_JS = (ROOT / "static" / "upload.js").read_text(encoding="utf-8")

    def test_the_card_has_a_live_progress_block_and_loads_after_app_js(self):
        self.assertRegex(self.HTML, r'<div class="backup-progress" id="backup-progress" role="status" hidden>')
        self.assertIn("<progress", self.HTML)
        self.assertLess(self.HTML.index("filename='app.js'"), self.HTML.index("filename='backup.js'"))
        for element in ("backup-make", "backup-file", "backup-restore", "backup-discard", "backup-confirm", "backup-links"):
            self.assertIn(f'id="{element}"', self.HTML)

    def test_the_screen_follows_the_contract(self):
        # Alleen een balk vullen die zijn totaal kent, de klus op de server volgen en niet herladen als wegklikken mislukt.
        for needle in ("/api/backup/stage", "/api/backup/jobs/latest", "Content-Length", "dismissed", "BACKUP_POLL_MS"):
            self.assertIn(needle, self.JS)
        # Het bestand gaat in stukken (upload.js), met een balk per verstuurde byte en een tijdslimiet per stuk.
        self.assertIn('uploadInChunks(file, "/api/backup/uploads"', self.JS)
        for needle in ("XMLHttpRequest", "upload.onprogress", "request.timeout", "status === 409", "/stage`"):
            self.assertIn(needle, self.UPLOAD_JS)
        self.assertLess(self.HTML.index("filename='app.js'"), self.HTML.index("filename='upload.js'"))
        self.assertLess(self.HTML.index("filename='upload.js'"), self.HTML.index("filename='backup.js'"))
        self.assertLess(self.HTML.index("filename='upload.js'"), self.HTML.index("filename='price_archive.js'"))
        self.assertNotIn("setInterval", self.JS)
        self.assertNotIn("file.text()", self.JS)  # het bestand wordt op de server gecontroleerd, niet in de browser

    def test_the_system_tab_opens_the_card(self):
        self.assertIn('"settings-system" && typeof loadBackup === "function"', (ROOT / "static" / "app.js").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
