import asyncio
import base64
import hashlib
import io
import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from collections.abc import AsyncIterator
from contextlib import closing
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable
from unittest import mock

from PIL import Image
from werkzeug.exceptions import RequestTimeout

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

from mealieshopper import audit, auth, auth_password, backup, factory_reset, image_cache, mealie, mealie_images, oauth, profile, tasks  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402
from support import LiveClient, SyncResponse  # noqa: E402

if TYPE_CHECKING:  # niet bij de import: pytest zou TestAppProtocol voor een testklasse aanzien
    from quart.typing import TestAppProtocol

ROOT = Path(__file__).resolve().parent.parent
FIXTURE_3131 = ROOT / "tests" / "fixtures" / "backup-3.13.1.json"
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


async def chunks(data: bytes, size: int) -> AsyncIterator[bytes]:
    """Het lichaam van een upload in stukken, zoals `request.body` het aanlevert."""
    for start in range(0, len(data), size):
        yield data[start:start + size]


class BackupCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
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

    async def asyncSetUp(self) -> None:
        # Wat `async with app.test_app()` (de fixture `client`) doet, verdeeld over het begin en het opruimen van de test.
        self.test_app: TestAppProtocol = self.app.test_app()
        await self.test_app.startup()
        self.addAsyncCleanup(self.test_app.shutdown)
        self.admin = self.new_client()
        await self.admin.post("/api/auth/password", json={"username": "admin", "password": PASSWORD})

    def new_client(self) -> LiveClient:
        return LiveClient(self.test_app.test_client())

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

    def connect(self) -> sqlite3.Connection:
        backup.ensure_schemas()
        conn = sqlite3.connect(auth.db_path())
        conn.row_factory = sqlite3.Row
        return conn

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

    async def make(self, client: LiveClient | None = None, **body: Any) -> tuple[SyncResponse, bytes]:
        body.setdefault("links", False)
        response = await (client or self.admin).post("/api/backup", json=body)
        return response, response.get_data()

    async def stage(self, data: bytes, client: LiveClient | None = None) -> SyncResponse:
        # Quart zet zelf geen Content-Length bij een testverzoek, een browser wel: de route leest hem.
        headers = {"Content-Type": "application/octet-stream", **({"Content-Length": str(len(data))} if data else {})}
        return await (client or self.admin).post("/api/backup/stage", data=data, headers=headers)

    async def start(self, token: str, passphrase: str = "", client: LiveClient | None = None) -> SyncResponse:
        return await (client or self.admin).post("/api/backup/jobs", json={"token": token, "passphrase": passphrase})

    async def latest(self, client: LiveClient | None = None) -> dict[str, Any] | None:
        return (await (client or self.admin).get("/api/backup/jobs/latest")).get_json()["job"]

    async def current(self) -> dict[str, Any]:
        """De klus die `latest` noemt. De test faalt als er geen is."""
        job = await self.latest()
        if job is None:
            self.fail("er is geen klus")
        return job

    async def wait(self, client: LiveClient | None = None, seconds: float = 20) -> dict[str, Any]:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            job = await self.latest(client)
            if job and job["status"] != "running":
                return job
            await asyncio.sleep(0.05)
        self.fail("de klus is niet klaar gekomen")

    async def restore(self, data: bytes, passphrase: str = "", client: LiveClient | None = None) -> dict[str, Any]:
        staged = await self.stage(data, client)
        self.assertEqual(staged.status_code, 200, staged.get_json())
        started = await self.start(staged.get_json()["token"], passphrase, client)
        self.assertEqual(started.status_code, 202, started.get_json())
        return await self.wait(client)

    def tweak(self, data: bytes, change: Callable[[dict[str, Any]], Any]) -> bytes:
        document = json.loads(data)
        change(document)
        return json.dumps(document).encode("utf-8")

    def staged_files(self) -> list[str]:
        return sorted(path.name for path in self.data.glob(".restore-*"))

    def gate_after_checking(self) -> tuple[threading.Event, threading.Event]:
        """Houdt de klus vast na de controle van de rijen (in zijn thread), tot de test `release` zet.

        Alleen de klus geeft een voortgangsfunctie mee: het klaarzetten van een bestand loopt er ongehinderd langs.
        """
        entered, release = threading.Event(), threading.Event()
        original = backup.check_rows

        def gated(plan: backup.Plan, progress: Callable[[int, int], None] | None = None) -> Any:
            prepared = original(plan, progress)
            if progress is not None:
                entered.set()
                release.wait(10)
            return prepared

        patch = mock.patch.object(backup, "check_rows", gated)
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(release.set)  # loopt voor het sluiten van de app: de thread blijft nooit hangen
        return entered, release


class ClassificationTest(BackupCase):
    @staticmethod
    async def oauth_schema() -> None:
        async with oauth._connect():  # oauth en mealie_images maken hun tabellen bij de eerste verbinding
            pass
        async with mealie_images._aconnect():
            pass

    async def test_every_table_is_either_in_the_backup_or_left_out_on_purpose(self) -> None:
        # Een nieuwe tabel met gegevens hoort in de back-up (DATA_TABLES) en in de round-trip-test: dit is de herinnering.
        backup.ensure_schemas()
        with closing(audit._connect()):
            pass
        async with image_cache._aconnect():
            pass
        await self.oauth_schema()
        with closing(self.connect()) as conn:
            tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertEqual(tables - set(backup.DATA_TABLES) - backup.EXCLUDED_TABLES, set(), "een tabel is niet ingedeeld")
        self.assertEqual(set(backup.DATA_TABLES) & backup.EXCLUDED_TABLES, set())
        self.assertEqual((set(backup.DATA_TABLES) | backup.EXCLUDED_TABLES) - tables - {"backup_jobs"}, set(), "een ingedeelde tabel bestaat niet")

    def test_nothing_that_unlocks_the_app_is_in_the_list(self) -> None:
        for table in ("users", "passkey_credentials", "password_credentials", "mfa_totp", "mfa_recovery_codes", "invites", "oauth_tokens", "oauth_codes", "login_attempts"):
            self.assertNotIn(table, backup.DATA_TABLES)

    def test_every_stored_link_is_known(self) -> None:
        source = (ROOT / "mealieshopper" / "ah.py").read_text(encoding="utf-8")
        self.assertIn('"AH_REFRESH_TOKEN"', source)
        self.assertEqual(set(backup.SECRET_KEYS), set(SECRETS))
        self.assertTrue(set(backup.PLAIN_SETTINGS).isdisjoint(backup.SECRET_KEYS))
        for key in ("AUTH_MODE", "SESSION_EPOCH", "ONBOARDING_ACTIVE", "ONBOARDING_DONE", "PUBLIC_BASE_URL"):
            self.assertNotIn(key, backup.PLAIN_SETTINGS)


class RoundTripTest(BackupCase):
    async def test_everything_comes_back_with_its_links(self) -> None:
        self.seed()
        for key, value in SECRETS.items():
            auth.set_secret(key, value)
        auth.set_setting("AI_PROVIDER", "anthropic")
        auth.set_setting("MCP_ENABLED", "0")
        auth.set_setting("WEEK_START", "6")
        before = self.snapshot()
        self.assertTrue(all(before[table] for table in backup.DATA_TABLES))
        response, data = await self.make(links=True, passphrase=PASSPHRASE)
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

        job = await self.restore(data, PASSPHRASE)
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
        self.assertEqual((await self.admin.get("/api/backup/jobs/latest")).get_json()["job"]["status"], "done")  # nog niet weggeklikt

    async def test_the_links_work_on_another_server_with_another_secret_key(self) -> None:
        for key, value in SECRETS.items():
            auth.set_secret(key, value)
        _, data = await self.make(links=True, passphrase=PASSPHRASE)
        old_secret = (self.data / "secret.key").read_text()

        self.new_data_dir()
        auth.forget_runtime_state()
        newcomer = self.new_client()
        await newcomer.post("/api/auth/password", json={"username": "nieuw", "password": PASSWORD})
        job = await self.restore(data, PASSPHRASE, newcomer)
        self.assertEqual(job["status"], "done", job)
        self.assertNotEqual((self.data_dir() / "secret.key").read_text(), old_secret)
        for key, value in SECRETS.items():
            self.assertEqual(auth.get_secret(key), value, key)
        self.assertEqual(await mealie.mealie_token(), SECRETS[mealie.SECRET_TOKEN])
        self.assertNotIn(SECRETS[mealie.SECRET_TOKEN], self.settings()[mealie.SECRET_TOKEN])  # opgeslagen met de sleutel van deze server

    def data_dir(self) -> Path:
        return auth.data_dir()

    async def test_a_backup_without_links_leaves_the_links_as_they_are(self) -> None:
        self.seed()
        _, data = await self.make(links=False)
        for key, value in SECRETS.items():
            auth.set_secret(key, value)
        self.wipe()
        job = await self.restore(data)
        self.assertEqual(job["status"], "done", job)
        self.assertTrue(job["summary"]["linksKept"])
        self.assertEqual(auth.get_secret(mealie.SECRET_TOKEN), SECRETS[mealie.SECRET_TOKEN])

    async def test_a_backup_with_links_replaces_the_links_that_are_there(self) -> None:
        auth.set_secret("PICNIC_AUTH_KEY", "PICNIC-KEY-abcdefgh")
        _, data = await self.make(links=True, passphrase=PASSPHRASE)  # alleen Picnic zit erin
        auth.set_secret("JUMBO_REFRESH_TOKEN", "JUMBO-REFRESH-zyxwvuts")
        self.assertEqual((await self.restore(data, PASSPHRASE))["status"], "done")
        self.assertEqual(auth.get_secret("PICNIC_AUTH_KEY"), "PICNIC-KEY-abcdefgh")
        self.assertEqual(auth.get_secret("JUMBO_REFRESH_TOKEN"), "")

    async def test_numbers_carry_on_after_a_restore(self) -> None:
        self.seed()
        _, data = await self.make()
        await self.restore(data)
        with closing(self.connect()) as conn, conn:
            top = conn.execute("SELECT MAX(id) FROM offer_history").fetchone()[0]
            conn.execute("INSERT INTO offer_history (store, product_id, title, first_seen, last_seen) VALUES ('ah', 'x', 'y', 'a', 'b')")
            self.assertGreater(conn.execute("SELECT MAX(id) FROM offer_history").fetchone()[0], top)

    async def test_a_section_that_is_missing_comes_back_empty(self) -> None:
        # Een bestand van vóór een tabel bestond, heeft die sectie niet: de tabel is na het terugzetten leeg.
        self.seed()
        _, data = await self.make()
        older = self.tweak(data, lambda doc: doc["tables"].pop("offer_history"))
        self.assertEqual((await self.restore(older))["status"], "done")
        after = self.snapshot()
        self.assertEqual(after["offer_history"], [])
        self.assertTrue(after["offers"] and after["ah_orders"])

    async def test_a_restore_replaces_what_is_there(self) -> None:
        self.seed()
        _, data = await self.make()
        self.junk()
        self.assertEqual((await self.restore(data))["status"], "done")
        with closing(self.connect()) as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM ah_lists WHERE id = 'junk'").fetchone())
            self.assertIsNone(conn.execute("SELECT 1 FROM offers_meta WHERE store = 'junk'").fetchone())

    async def test_the_login_of_the_app_is_not_touched(self) -> None:
        self.seed()
        _, data = await self.make()
        with closing(self.connect()) as conn:
            before = [tuple(r) for r in conn.execute("SELECT id, username FROM users")], conn.execute("SELECT password_hash FROM password_credentials").fetchall()
        self.assertEqual((await self.restore(data))["status"], "done")
        with closing(self.connect()) as conn:
            after = [tuple(r) for r in conn.execute("SELECT id, username FROM users")], conn.execute("SELECT password_hash FROM password_credentials").fetchall()
        self.assertEqual([tuple(r) for r in after[1]], [tuple(r) for r in before[1]])
        self.assertEqual(after[0], before[0])
        self.assertEqual((await self.admin.get("/api/backup")).status_code, 200)  # de sessie werkt nog


class FileContentTest(BackupCase):
    async def test_the_file_holds_nothing_that_unlocks_the_app(self) -> None:
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
        oauth_present = ((await self.admin.get("/api/backup")).status_code == 200)
        self.assertTrue(oauth_present)
        for links in (True, False):
            _, data = await self.make(links=links, passphrase=PASSPHRASE)
            text = data.decode("utf-8")
            for needle in (
                password_hash, "CRED-ID-777", "PUBLIC-KEY-777", "RECOVERY-HASH-777", "TOTP-SECRET-777", "INVITE-HASH-777",
                "passkey_credentials", "password_credentials", "mfa_totp", "mfa_recovery_codes", "invites", "oauth_", "login_attempts",
                "AUTH_MODE", "SESSION_EPOCH", "ONBOARDING", "PUBLIC_BASE_URL", (self.data / "secret.key").read_text(),
            ):
                self.assertNotIn(needle, text, needle)

    async def test_the_links_are_encrypted_and_the_password_is_not_in_the_file(self) -> None:
        for key, value in SECRETS.items():
            auth.set_secret(key, value)
        _, data = await self.make(links=True, passphrase=PASSPHRASE)
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

    async def test_a_backup_without_links_has_none(self) -> None:
        for key, value in SECRETS.items():
            auth.set_secret(key, value)
        _, data = await self.make(links=False)
        document = json.loads(data)
        self.assertEqual(document["secrets"], {"links": [], "sealed": None})
        self.assertFalse(document["includes_links"])
        for value in SECRETS.values():
            self.assertNotIn(value, data.decode("utf-8"))

    async def test_the_header_names_the_format_and_versions(self) -> None:
        _, data = await self.make()
        document = json.loads(data)
        self.assertEqual((document["format"], document["format_version"]), ("mealieshopper-backup", 1))
        self.assertRegex(document["app_version"], r"^\d+\.\d+\.\d+")
        self.assertTrue(document["created_at"])
        self.assertEqual(list(document["tables"]), list(backup.DATA_TABLES))

    async def test_links_need_a_password_of_some_length(self) -> None:
        for passphrase in ("", "kort"):
            response = await self.admin.post("/api/backup", json={"links": True, "passphrase": passphrase})
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.get_json()["code"], "passphrase_short")
        self.assertEqual(list(self.data.glob(".backup-*")), [])  # ook een geweigerde aanvraag laat niets achter

    async def test_the_temporary_file_is_removed_after_the_download(self) -> None:
        await self.make(links=False)
        self.assertEqual(list(self.data.glob(".backup-*")), [])

    async def test_a_link_that_cannot_be_read_is_left_out(self) -> None:
        auth.set_secret(mealie.SECRET_TOKEN, "MEALIE-TOKEN-12345678")
        with closing(self.connect()) as conn, conn:
            conn.execute("UPDATE app_settings SET value = 'gAAAAB-een-andere-sleutel' WHERE key = ?", (mealie.SECRET_TOKEN,))
        _, data = await self.make(links=True, passphrase=PASSPHRASE)
        self.assertEqual(json.loads(data)["secrets"]["links"], [])


class OlderBackupTest(BackupCase):
    async def test_a_backup_from_before_prefer_organic_still_restores(self) -> None:
        # #218 gaf user_profiles de kolom prefer_organic; een back-up van daarvoor heeft hem niet en zet de schakelaar uit.
        self.seed()
        with closing(self.connect()) as conn, conn:
            conn.execute("UPDATE user_profiles SET prefer_organic = 1")
        _, data = await self.make()

        def older(doc):
            for row in doc["tables"]["user_profiles"]:
                del row["prefer_organic"]

        job = await self.restore(self.tweak(data, older))
        self.assertEqual(job["status"], "done", job)
        self.assertFalse(profile.get("owner")["preferOrganic"])

    async def test_a_backup_from_before_the_appearance_settings_still_restores(self) -> None:
        # De weergave gaf user_profiles de kolommen theme, dark_background en accent; een oudere back-up zet de standaard terug.
        self.seed()
        profile.set_appearance("owner", theme="dark", dark_background="grey", accent="#0a84ff", navbar_style="android", navbar_glass=0)
        _, data = await self.make()
        row = json.loads(data)["tables"]["user_profiles"][0]
        self.assertEqual((row["accent"], row["navbar_style"], row["navbar_glass"]), ("#0a84ff", "android", 0), "de weergave zit in de back-up")

        def older(doc):
            for row in doc["tables"]["user_profiles"]:
                for column in ("theme", "dark_background", "accent", "navbar_style", "navbar_glass"):
                    del row[column]

        job = await self.restore(self.tweak(data, older))
        self.assertEqual(job["status"], "done", job)
        self.assertEqual(
            {key: profile.get("owner")[key] for key in ("theme", "darkBackground", "accent", "navbarStyle", "navbarGlass")},
            {"theme": None, "darkBackground": "green", "accent": "", "navbarStyle": "auto", "navbarGlass": 60},
        )

    async def test_restore_of_a_3_x_backup_file_succeeds(self) -> None:
        # tests/fixtures/backup-3.13.1.json is gemaakt met de code van release 3.13.1 (commit 135bd86) op een lege
        # installatie: een AH-lijstje met twee artikelen, twee aanbiedingen van AH met hun geschiedenis en de eerste dag
        # van de week. Hoe je het opnieuw maakt staat in tests/fixtures/make_backup_3131.py.
        self.seed()
        data = FIXTURE_3131.read_bytes()
        self.assertEqual(json.loads(data)["app_version"], "3.13.1")
        job = await self.restore(data)
        self.assertEqual(job["status"], "done", job)
        self.assertEqual((job["summary"]["counts"]["lists"], job["summary"]["counts"]["offers"]), (1, 2))
        after = self.snapshot()
        self.assertEqual([row[:2] for row in after["ah_lists"]], [("LIJST-1", "Ontbijt")])
        self.assertEqual(len(after["ah_list_items"]), 2)
        with closing(self.connect()) as conn:
            titles = [row["title"] for row in conn.execute("SELECT title FROM offers ORDER BY position")]
            history = conn.execute("SELECT COUNT(*) FROM offer_history WHERE store = 'ah'").fetchone()[0]
            meta = [row["store"] for row in conn.execute("SELECT store FROM offers_meta")]
        self.assertEqual(titles, ["Spam in blik", "Scharreleieren"])
        self.assertEqual((history, meta), (2, ["ah"]))
        for table in ("jumbo_lists", "ah_orders", "user_profiles"):
            self.assertEqual(after[table], [], f"{table} is vervangen door de lege sectie uit het bestand")
        self.assertEqual(auth.get_setting(mealie.SETTING_WEEK_START), "1")


class RefusalTest(BackupCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.seed()
        self.before = self.snapshot()
        _, self.data_bytes = await self.make()

    async def refused(self, data: bytes, code: str, status: int = 400) -> dict[str, Any]:
        response = await self.stage(data)
        self.assertEqual(response.status_code, status, response.get_json())
        self.assertEqual(response.get_json()["code"], code)
        self.assertEqual(self.staged_files(), [], "een geweigerd bestand blijft niet staan")
        self.assertEqual(self.snapshot(), self.before)
        return response.get_json()

    async def test_files_that_are_not_a_backup(self) -> None:
        for data in (b"", b"gewoon tekst", b"{kapot", b"[1, 2]", b'{"format": "iets-anders"}', b"PK\x03\x04" + b"0" * 100, b'{"format": "mealieshopper-backup", "format_version": NaN}'):
            await self.refused(data, "not_a_backup")

    async def test_a_newer_version_names_the_version_that_wrote_it(self) -> None:
        newer = self.tweak(self.data_bytes, lambda doc: doc.update(format_version=2, app_version="9.9.9"))
        body = await self.refused(newer, "newer_version")
        self.assertIn("9.9.9", body["error"])
        self.assertIn("bij", body["error"])  # "Werk MealieShopper eerst bij"

    async def test_parts_that_do_not_belong_are_refused(self) -> None:
        for table in ("passkey_credentials", "users", "oauth_tokens", "audit_log", "sqlite_master", "niet_bestaand"):
            data = self.tweak(self.data_bytes, lambda doc, t=table: doc["tables"].update({t: [{"id": "x"}]}))
            self.assertEqual((await self.refused(data, "bad_table"))["error"].count(table), 1)

    async def test_a_file_cannot_switch_off_the_login_or_plant_a_setting(self) -> None:
        for key in ("AUTH_MODE", "SESSION_EPOCH", "ONBOARDING_DONE", "PUBLIC_BASE_URL", "iets"):
            data = self.tweak(self.data_bytes, lambda doc, k=key: doc["settings"].update({k: "none"}))
            await self.refused(data, "bad_setting")
        self.assertTrue(auth.auth_required())

    async def test_a_row_that_does_not_fit_names_the_table_and_the_line(self) -> None:
        def spoil(doc):
            doc["tables"]["jumbo_orders"][1]["state"] = {"niet": "toegestaan"}

        body = await self.refused(self.tweak(self.data_bytes, spoil), "bad_row")
        self.assertIn("jumbo_orders", body["error"])

    async def test_duplicates_and_missing_required_values_are_found(self) -> None:
        def duplicate(doc):
            doc["tables"]["ah_orders"].append(dict(doc["tables"]["ah_orders"][0]))

        def missing(doc):
            del doc["tables"]["offers"][0]["title"]

        body = await self.refused(self.tweak(self.data_bytes, duplicate), "bad_row")
        self.assertRegex(body["error"], r"ah_orders, regel 3")
        body = await self.refused(self.tweak(self.data_bytes, missing), "bad_row")
        self.assertRegex(body["error"], r"offers, regel 1")

    async def test_an_unknown_column_is_refused(self) -> None:
        def extra(doc):
            doc["tables"]["ah_lists"][0]["geheim"] = "x"

        self.assertIn("geheim", (await self.refused(self.tweak(self.data_bytes, extra), "bad_row"))["error"])

    async def test_an_avatar_is_read_again_and_not_taken_on_the_files_word(self) -> None:
        def spoil(doc):
            doc["tables"]["user_profiles"][0]["avatar"] = {"base64": "PHN2Zz48L3N2Zz4="}  # <svg></svg>, maar het bestand zegt image/png

        self.assertIn("user_profiles", (await self.refused(self.tweak(self.data_bytes, spoil), "bad_row"))["error"])

        def broken(doc):
            doc["tables"]["user_profiles"][0]["avatar"] = {"base64": "***"}

        await self.refused(self.tweak(self.data_bytes, broken), "bad_row")

    async def test_a_product_image_is_read_again_and_not_taken_on_the_files_word(self) -> None:
        def spoil(doc):
            doc["tables"]["product_images"][0]["data"] = {"base64": "PHN2Zz48L3N2Zz4="}  # <svg></svg>

        self.assertIn("product_images", (await self.refused(self.tweak(self.data_bytes, spoil), "bad_row"))["error"])

        def too_wide(doc):
            out = io.BytesIO()
            Image.new("RGB", (1500, 10)).save(out, "PNG")
            doc["tables"]["product_images"][0]["data"] = {"base64": base64.b64encode(out.getvalue()).decode("ascii")}

        self.assertIn("1024", (await self.refused(self.tweak(self.data_bytes, too_wide), "bad_row"))["error"])

    async def test_what_follows_from_a_product_image_is_worked_out_again(self) -> None:
        def lie(doc):
            row = doc["tables"]["product_images"][0]
            row.update(content_type="image/webp", width=999, height=999, digest="niet-waar")

        self.assertEqual((await self.restore(self.tweak(self.data_bytes, lie)))["status"], "done")
        with closing(self.connect()) as conn:
            row = conn.execute("SELECT * FROM product_images ORDER BY id LIMIT 1").fetchone()
        expected = product_image(1)
        self.assertEqual((row["content_type"], row["width"], row["height"], row["digest"]),
                         (expected["content_type"], expected["width"], expected["height"], expected["digest"]))

    async def test_a_file_over_the_limit_is_refused_and_not_kept(self) -> None:
        with mock.patch.object(backup, "MAX_STAGE_BYTES", 1000):
            await self.refused(self.data_bytes, "too_large", 413)

    async def test_one_bad_line_stops_the_whole_restore(self) -> None:
        def spoil(doc):
            doc["tables"]["picnic_orders"][0]["order_id"] = None

        data = self.tweak(self.data_bytes, spoil)
        self.junk()
        junk_state = self.snapshot()
        response = await self.stage(data)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.snapshot(), junk_state)

    async def test_a_tampered_encrypted_block_is_refused(self) -> None:
        auth.set_secret(mealie.SECRET_TOKEN, "MEALIE-TOKEN-12345678")
        _, data = await self.make(links=True, passphrase=PASSPHRASE)
        auth.delete_secret(mealie.SECRET_TOKEN)
        # De inhoud wordt pas met het wachtwoord gelezen, dus deze bestanden komen wel klaar te staan en falen bij het starten.
        for change in (
            lambda doc: doc["secrets"]["sealed"].update(token="x" + doc["secrets"]["sealed"]["token"][1:]),
            lambda doc: doc["secrets"]["sealed"].update(n=2**30),  # de kosten staan in het bestand: een te hoge waarde mag de server niet vastzetten
            lambda doc: doc["secrets"]["sealed"].update(kdf="md5"),
        ):
            staged = await self.stage(self.tweak(data, change))
            self.assertEqual(staged.status_code, 200)
            started = await self.start(staged.get_json()["token"], PASSPHRASE)
            self.assertIn(started.status_code, (400, 403))
            await self.admin.delete(f"/api/backup/stage/{staged.get_json()['token']}")
        # Koppelingen aangekondigd zonder versleuteld blok: dat is een beschadigd bestand.
        self.assertEqual((await self.stage(self.tweak(data, lambda doc: doc["secrets"].update(sealed=None)))).status_code, 400)
        self.assertEqual(auth.get_secret(mealie.SECRET_TOKEN), "")
        self.assertEqual(self.staged_files(), [])

    async def test_secrets_in_the_block_must_be_known_links(self) -> None:
        sealed = backup.seal({"AUTH_MODE": "none"}, PASSPHRASE)
        auth.set_secret(mealie.SECRET_TOKEN, "MEALIE-TOKEN-12345678")
        _, data = await self.make(links=True, passphrase=PASSPHRASE)
        planted = self.tweak(data, lambda doc: doc["secrets"].update(sealed=sealed))
        staged = await self.stage(planted)
        self.assertEqual(staged.status_code, 200)
        started = await self.start(staged.get_json()["token"], PASSPHRASE)
        self.assertEqual(started.status_code, 400)
        self.assertEqual(started.get_json()["code"], "bad_setting")


class StageTest(BackupCase):
    async def test_the_stage_answers_with_a_summary_and_changes_nothing(self) -> None:
        self.seed()
        before, settings = self.snapshot(), self.settings()
        _, data = await self.make(links=False)
        self.wipe()
        wiped = self.snapshot()
        response = await self.stage(data)
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

    async def test_changing_your_mind_removes_the_file(self) -> None:
        token = (await self.stage((await self.make())[1])).get_json()["token"]
        self.assertEqual((await self.admin.delete(f"/api/backup/stage/{token}")).status_code, 200)
        self.assertEqual(self.staged_files(), [])
        self.assertEqual((await self.start(token)).status_code, 404)

    async def test_a_token_must_look_like_a_token(self) -> None:
        for token in ("../x", "..%2Fsecret", "x" * 32, "A" * 32, "0" * 31, ""):
            self.assertEqual((await self.start(token)).status_code, 404, token)
        self.assertEqual((await self.admin.delete("/api/backup/stage/..")).status_code, 404)

    async def test_a_token_works_once(self) -> None:
        token = (await self.stage((await self.make())[1])).get_json()["token"]
        self.assertEqual((await self.start(token)).status_code, 202)
        await self.wait()
        self.assertEqual((await self.start(token)).status_code, 404)
        self.assertEqual(self.staged_files(), [])

    async def test_staged_files_that_were_never_used_are_cleared_the_next_time(self) -> None:
        old = self.data / (".restore-" + "a" * 32)
        old.write_bytes(b"oud")
        os.utime(old, (time.time() - 2 * 3600, time.time() - 2 * 3600))
        young = self.data / (".restore-" + "b" * 32)
        young.write_bytes(b"jong")
        self.assertEqual((await self.stage((await self.make())[1])).status_code, 200)
        self.assertFalse(old.exists())
        self.assertTrue(young.exists())

    async def test_a_factory_reset_takes_a_staged_file_along(self) -> None:
        await self.stage((await self.make())[1])
        removed = factory_reset.wipe_data()
        self.assertEqual(self.staged_files(), [])
        self.assertTrue(any(name.startswith(".restore-") for name in removed))

    async def test_the_staged_file_is_not_readable_by_others(self) -> None:
        await self.stage((await self.make())[1])
        path = next(self.data.glob(".restore-*"))
        self.assertEqual(path.stat().st_mode & 0o077, 0)


class PassphraseTest(BackupCase):
    async def prepare(self) -> bytes:
        auth.set_secret(mealie.SECRET_TOKEN, "MEALIE-TOKEN-12345678")
        _, data = await self.make(links=True, passphrase=PASSPHRASE)
        auth.delete_secret(mealie.SECRET_TOKEN)
        return data

    async def test_the_summary_says_a_password_is_needed(self) -> None:
        body = (await self.stage(await self.prepare())).get_json()
        self.assertTrue(body["needsPassphrase"])
        self.assertEqual(body["links"], ["mealie"])

    async def test_a_missing_or_wrong_password_changes_nothing_and_keeps_the_file(self) -> None:
        token = (await self.stage(await self.prepare())).get_json()["token"]
        for passphrase, code, status in (("", "needs_passphrase", 400), ("verkeerd wachtwoord", "wrong_passphrase", 403)):
            response = await self.start(token, passphrase)
            self.assertEqual((response.status_code, response.get_json()["code"]), (status, code))
            self.assertEqual(self.staged_files(), [".restore-" + token])
        self.assertEqual(auth.get_secret(mealie.SECRET_TOKEN), "")
        self.assertEqual((await self.admin.get("/api/backup/jobs/latest")).get_json()["job"], None)
        self.assertEqual((await self.start(token, PASSPHRASE)).status_code, 202)  # en meteen opnieuw proberen kan
        self.assertEqual((await self.wait())["status"], "done")
        self.assertEqual(auth.get_secret(mealie.SECRET_TOKEN), "MEALIE-TOKEN-12345678")

    async def test_the_password_is_never_stored(self) -> None:
        data = await self.prepare()
        await self.restore(data, PASSPHRASE)
        for path in self.data.iterdir():
            if path.is_file():
                self.assertNotIn(PASSPHRASE.encode(), path.read_bytes(), path.name)


class AccessTest(BackupCase):
    ROUTES = (
        ("get", "/api/backup"), ("post", "/api/backup"), ("post", "/api/backup/stage"), ("delete", "/api/backup/stage/" + "0" * 32),
        ("post", "/api/backup/jobs"), ("get", "/api/backup/jobs/latest"), ("delete", "/api/backup/jobs/abc"),
    )

    async def test_every_route_needs_a_session(self) -> None:
        stranger = self.new_client()
        for method, path in self.ROUTES:
            self.assertEqual((await getattr(stranger, method)(path)).status_code, 401, path)

    async def test_only_the_administrator_may_use_them(self) -> None:
        token = (await self.admin.post("/api/users/invites", json={"username": "anna"})).get_json()["token"]
        anna = self.new_client()
        await anna.post("/api/invites/password", json={"token": token, "password": ANNA})
        for method, path in self.ROUTES:
            self.assertEqual((await getattr(anna, method)(path)).status_code, 403, path)
        self.assertTrue(auth.admin_only("/api/backup/stage"))
        self.assertEqual(list(self.data.glob(".restore-*")), [])


class JobTest(BackupCase):
    async def test_a_job_reports_steps_and_timings(self) -> None:
        self.seed()
        _, data = await self.make()
        job = await self.restore(data)
        self.assertEqual(job["status"], "done")
        self.assertEqual(job["phase"], "replacing")
        self.assertIsNotNone(job["checkingSeconds"])
        self.assertIsNotNone(job["replacingSeconds"])
        self.assertEqual(job["summary"]["counts"]["offers"], 2)
        with closing(self.connect()) as conn:
            row = conn.execute("SELECT * FROM backup_jobs").fetchone()
        self.assertEqual(row["status"], "done")

    async def test_progress_counts_the_tables_while_checking(self) -> None:
        self.seed()
        _, data = await self.make()
        plan = backup.prepare(backup.read_file(self._write(data)))
        seen = []
        backup.check_rows(plan, lambda step, steps: seen.append((step, steps)))
        self.assertEqual(seen[0], (1, len(backup.DATA_TABLES)))
        self.assertEqual(seen[-1], (len(backup.DATA_TABLES), len(backup.DATA_TABLES)))

    def _write(self, data: bytes) -> Path:
        path = self.data / "voorbeeld.json"
        path.write_bytes(data)
        return path

    async def test_a_failure_halfway_changes_nothing_and_says_so(self) -> None:
        self.seed()
        auth.set_secret(mealie.SECRET_TOKEN, "MEALIE-TOKEN-12345678")
        _, data = await self.make(links=True, passphrase=PASSPHRASE)
        self.junk()
        before, settings = self.snapshot(), self.settings()
        staged = (await self.stage(data)).get_json()["token"]
        # Pas na het wissen en invoegen van alle tabellen gaat het mis: de transactie moet alles terugdraaien.
        with mock.patch.object(auth, "secret_box", side_effect=RuntimeError("stuk")), self.assertLogs("mealieshopper.backup", "ERROR"):
            self.assertEqual((await self.start(staged, PASSPHRASE)).status_code, 202)
            job = await self.wait()
        self.assertEqual(job["status"], "failed")
        self.assertIn("niets gewijzigd", job["error"])
        self.assertNotIn("stuk", job["error"])  # de beheerder krijgt een zin, het logboek de fout
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.settings(), settings)
        self.assertEqual(self.staged_files(), [])

    async def test_done_is_part_of_the_swap_so_the_two_cannot_disagree(self) -> None:
        self.seed()
        _, data = await self.make()
        plan = backup.prepare(backup.read_file(self._write(data)))
        prepared = backup.check_rows(plan)
        self.junk()
        before = self.snapshot()

        def refuse(conn, summary):
            raise RuntimeError("het markeren als klaar mislukt")

        with self.assertRaises(RuntimeError):
            backup.replace_everything(plan, prepared, refuse)
        self.assertEqual(self.snapshot(), before)  # de gegevens zijn pas vervangen als de klus ook als klaar is gemarkeerd

    async def test_a_refused_file_in_the_job_fails_with_its_reason(self) -> None:
        _, data = await self.make()
        staged = (await self.stage(data)).get_json()["token"]
        path = self.data / (".restore-" + staged)
        path.write_bytes(b"niet meer wat het was")  # het bestand wordt in de klus opnieuw gecontroleerd
        started = await self.start(staged)
        self.assertEqual(started.status_code, 400)
        self.assertEqual(started.get_json()["code"], "not_a_backup")

    async def test_only_one_job_runs_at_a_time(self) -> None:
        now = backup._now()
        with closing(self.connect()) as conn, conn:
            conn.execute("INSERT INTO backup_jobs (id, status, phase, started_at, updated_at) VALUES ('lopend', 'running', 'checking', ?, ?)", (now, now))
        staged = (await self.stage((await self.make())[1])).get_json()["token"]
        response = await self.start(staged)
        self.assertEqual((response.status_code, response.get_json()["code"]), (409, "busy"))
        self.assertEqual(self.staged_files(), [".restore-" + staged])  # het bestand blijft voor een nieuwe poging
        self.assertEqual((await self.admin.get("/api/backup/jobs/latest")).get_json()["job"]["status"], "running")

    async def test_a_job_that_lost_its_task_is_reported_as_interrupted(self) -> None:
        old = backup._now()
        with closing(self.connect()) as conn, conn:
            conn.execute(
                "INSERT INTO backup_jobs (id, status, phase, started_at, updated_at) VALUES ('weg', 'running', 'replacing', '2020-01-01T00:00:00+00:00', '2020-01-01T00:00:00+00:00')"
            )
        job = (await self.admin.get("/api/backup/jobs/latest")).get_json()["job"]
        self.assertEqual(job["status"], "failed")
        self.assertIn("onderbroken", job["error"])
        self.assertIn("niets gewijzigd", job["error"])
        self.assertTrue(old)
        # en daarna kan er weer een starten
        self.assertEqual((await self.restore((await self.make())[1]))["status"], "done")

    async def test_a_finished_job_is_shown_until_it_is_dismissed(self) -> None:
        job = await self.restore((await self.make())[1])
        again = (await self.admin.get("/api/backup/jobs/latest")).get_json()["job"]
        self.assertEqual(again["id"], job["id"])
        self.assertEqual((await self.admin.delete(f"/api/backup/jobs/{job['id']}")).status_code, 200)
        self.assertIsNone((await self.admin.get("/api/backup/jobs/latest")).get_json()["job"])
        self.assertEqual((await self.admin.delete("/api/backup/jobs/onbekend")).status_code, 404)

    async def test_a_running_job_cannot_be_dismissed(self) -> None:
        now = backup._now()
        with closing(self.connect()) as conn, conn:
            conn.execute("INSERT INTO backup_jobs (id, status, phase, started_at, updated_at) VALUES ('lopend', 'running', 'checking', ?, ?)", (now, now))
        self.assertEqual((await self.admin.delete("/api/backup/jobs/lopend")).status_code, 404)
        self.assertEqual((await self.admin.get("/api/backup/jobs/latest")).get_json()["job"]["status"], "running")

    async def test_the_messages_follow_the_language_of_the_reader(self) -> None:
        _, data = await self.make()
        self.admin.set_cookie("ms_lang", "en")
        response = await self.admin.post("/api/backup/stage", data=b"kapot")
        self.assertIn("not a MealieShopper backup", response.get_json()["error"])
        self.admin.set_cookie("ms_lang", "nl")
        response = await self.admin.post("/api/backup/stage", data=b"kapot")
        self.assertIn("geen MealieShopper-back-up", response.get_json()["error"])

    async def test_the_job_table_is_not_part_of_the_backup_and_survives_a_restore(self) -> None:
        self.assertNotIn("backup_jobs", backup.DATA_TABLES)
        job = await self.restore((await self.make())[1])
        with closing(self.connect()) as conn:
            self.assertEqual(conn.execute("SELECT id FROM backup_jobs").fetchone()["id"], job["id"])


class TaskTest(BackupCase):
    async def test_restore_job_runs_as_a_task_and_reports_progress(self) -> None:
        self.seed()
        _, data = await self.make()
        token: str = (await self.stage(data)).get_json()["token"]
        entered, release = self.gate_after_checking()
        started: SyncResponse = await self.start(token)
        self.assertEqual(started.status_code, 202, started.get_json())
        job_id: str = started.get_json()["id"]
        self.assertTrue(await asyncio.to_thread(entered.wait, 10), "de controle van de rijen is niet begonnen")
        name: str = f"backup-restore-{job_id}"
        self.assertIn(name, tasks.running())
        task: asyncio.Task[Any] = tasks.running()[name]
        self.assertFalse(any(thread.name.startswith("backup-restore-") for thread in threading.enumerate()), "geen eigen thread meer")
        steps: int = len(backup.DATA_TABLES)
        job: dict[str, Any] = await self.current()
        self.assertEqual((job["id"], job["status"], job["phase"], job["step"], job["steps"]), (job_id, "running", "checking", steps, steps))
        release.set()
        job = await self.wait()
        self.assertEqual((job["status"], job["phase"], job["error"]), ("done", "replacing", None))
        self.assertIsNotNone(job["checkingSeconds"])
        self.assertIsNotNone(job["replacingSeconds"])
        await task
        self.assertNotIn(name, tasks.running())
        self.assertEqual(self.staged_files(), [])

    async def test_a_cancelled_restore_marks_the_job_failed(self) -> None:
        self.seed()
        _, data = await self.make()
        self.junk()
        before: dict[str, list[tuple]] = self.snapshot()
        token: str = (await self.stage(data)).get_json()["token"]
        entered, release = self.gate_after_checking()
        job_id: str = (await self.start(token)).get_json()["id"]
        self.assertTrue(await asyncio.to_thread(entered.wait, 10), "de controle van de rijen is niet begonnen")
        task: asyncio.Task[Any] = tasks.running()[f"backup-restore-{job_id}"]
        await tasks.cancel_all()  # wat de app bij het afsluiten doet (after_serving)
        release.set()
        self.assertTrue(task.cancelled())
        job: dict[str, Any] = await self.current()
        self.assertEqual((job["id"], job["status"]), (job_id, "failed"))
        self.assertIn("onderbroken", job["error"])
        self.assertIn("niets gewijzigd", job["error"])
        with closing(self.connect()) as conn:
            row = conn.execute("SELECT error_key, finished_at FROM backup_jobs WHERE id = ?", (job_id,)).fetchone()
        self.assertEqual(row["error_key"], "interrupted")
        self.assertTrue(row["finished_at"])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.staged_files(), [])
        # en daarna kan er weer een starten (de poort staat nog open)
        self.assertEqual((await self.restore(data))["status"], "done")

    async def test_an_interrupted_job_whose_swap_still_commits_ends_done_without_an_error(self) -> None:
        # Het afsluiten annuleert de task terwijl de thread van het vervangen nog op zijn schrijfslot wacht. De thread
        # maakt het daarna af: de klus heet dan klaar, zonder de melding "onderbroken, niets gewijzigd".
        self.seed()
        _, data = await self.make()
        self.junk()
        token: str = (await self.stage(data)).get_json()["token"]
        entered, release = threading.Event(), threading.Event()
        original: Callable[..., dict[str, Any]] = backup.replace_everything

        def gated(plan: backup.Plan, prepared: dict[str, Any], on_commit: Callable[..., None]) -> dict[str, Any]:
            entered.set()
            release.wait(10)
            return original(plan, prepared, on_commit)

        patch = mock.patch.object(backup, "replace_everything", gated)
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(release.set)
        job_id: str = (await self.start(token)).get_json()["id"]
        self.assertTrue(await asyncio.to_thread(entered.wait, 10), "het vervangen is niet begonnen")
        await tasks.cancel_all()
        job: dict[str, Any] = await self.current()
        self.assertEqual(job["status"], "failed")
        self.assertIn("onderbroken", job["error"])
        release.set()
        deadline: float = time.monotonic() + 10
        while job["status"] != "done" and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
            job = await self.current()
        self.assertEqual((job["id"], job["status"], job["error"]), (job_id, "done", None))
        with closing(self.connect()) as conn:
            row = conn.execute("SELECT error_key, error_params FROM backup_jobs WHERE id = ?", (job_id,)).fetchone()
            self.assertIsNone(conn.execute("SELECT 1 FROM ah_lists WHERE id = 'junk'").fetchone(), "de gegevens zijn vervangen")
        self.assertEqual((row["error_key"], row["error_params"]), (None, None))

    async def test_a_cancel_while_a_refusal_is_recorded_still_marks_the_job_interrupted(self) -> None:
        _, data = await self.make()
        token: str = (await self.stage(data)).get_json()["token"]
        recording = asyncio.Event()

        async def stuck(job_id: str, error: backup.BackupError | None) -> None:
            recording.set()
            await asyncio.Event().wait()

        refusal = backup.BackupError("bad_row", table="offers", line=1, error="kapot")
        with mock.patch.object(backup, "check_rows", side_effect=refusal), mock.patch.object(backup, "_fail_job", stuck):
            job_id: str = (await self.start(token)).get_json()["id"]
            await asyncio.wait_for(recording.wait(), 10)
            task: asyncio.Task[Any] = tasks.running()[f"backup-restore-{job_id}"]
            await tasks.cancel_all()
        self.assertTrue(task.cancelled())
        job: dict[str, Any] = await self.current()
        self.assertEqual((job["id"], job["status"]), (job_id, "failed"))
        self.assertIn("onderbroken", job["error"])
        self.assertEqual(self.staged_files(), [])


class StreamTest(BackupCase):
    async def test_stage_rejects_an_oversized_upload_before_reading(self) -> None:
        consumed: list[bytes] = []

        async def body() -> AsyncIterator[bytes]:
            consumed.append(b"{}")
            yield b"{}"

        with self.assertRaises(backup.BackupError) as caught:
            await backup.stage(body(), backup.MAX_STAGE_BYTES + 1)
        self.assertEqual((caught.exception.key, caught.exception.status), ("too_large", 413))
        self.assertEqual(consumed, [], "het lichaam is niet gelezen")
        self.assertEqual(self.staged_files(), [])
        # Via de route: dezelfde melding als JSON, op de Content-Length alleen.
        response: SyncResponse = await self.admin.post(
            "/api/backup/stage", data=b"{}", headers={"Content-Type": "application/octet-stream", "Content-Length": str(backup.MAX_STAGE_BYTES + 1)},
        )
        self.assertEqual((response.status_code, response.get_json()["code"]), (413, "too_large"))
        self.assertEqual(self.staged_files(), [])

    async def test_stage_streams_without_buffering(self) -> None:
        self.seed()
        _, data = await self.make()
        size: int = 64 * 1024  # groter dan de buffer van een bestand: elk stuk gaat meteen naar schijf
        upload: bytes = data + b" " * (2 * 1024 * 1024)  # witruimte achter de JSON mag, zo zijn het tientallen stukken
        behind: list[int] = []

        async def body() -> AsyncIterator[bytes]:
            sent: int = 0
            async for chunk in chunks(upload, size):
                staged: list[Path] = list(self.data.glob(".restore-*"))
                if sent:
                    self.assertEqual(len(staged), 1)
                    behind.append(sent - staged[0].stat().st_size)  # wat al gestuurd is maar nog niet op schijf staat
                sent += len(chunk)
                yield chunk

        summary: dict[str, Any] = await backup.stage(body(), None)
        self.assertGreater(len(behind), 30)
        self.assertEqual(max(behind), 0, "elk stuk staat op schijf voor het volgende komt")
        path: Path = self.data / (".restore-" + summary["token"])
        self.assertEqual(path.read_bytes(), upload)
        self.assertEqual(summary["counts"]["offers"], 2)

    async def test_a_chunked_upload_through_the_route_is_written_while_it_arrives(self) -> None:
        self.seed()
        _, data = await self.make()
        upload: bytes = data + b" " * (256 * 1024)
        first: bytes = upload[:128 * 1024]  # groter dan de buffer van een bestand: dit stuk staat meteen op schijf
        async with self.admin.stream("/api/backup/stage", {"Content-Type": "application/octet-stream"}) as connection:
            await connection.send(first)
            deadline: float = time.monotonic() + 5
            arrived: list[Path] = []
            while time.monotonic() < deadline:
                arrived = [path for path in self.data.glob(".restore-*") if path.stat().st_size == len(first)]
                if arrived:
                    break
                await asyncio.sleep(0.01)
            self.assertEqual(len(arrived), 1, "het eerste stuk staat op schijf voor de rest binnen is")
            await connection.send(upload[len(first):])
            await connection.send_complete()
        response = await connection.as_response()
        self.assertEqual(response.status_code, 200, await response.get_data())
        summary: dict[str, Any] = await response.get_json()
        self.assertEqual((self.data / (".restore-" + summary["token"])).read_bytes(), upload)
        self.assertEqual(summary["counts"]["offers"], 2)

    async def test_a_stalled_upload_through_the_route_answers_408(self) -> None:
        self.app.config["BODY_TIMEOUT"] = 0.2
        async with self.admin.stream("/api/backup/stage", {"Content-Type": "application/octet-stream"}) as connection:
            await connection.send(b'{"format": "mealieshopper-backup"')  # en daarna niets meer
        response = await connection.as_response()
        self.assertEqual(response.status_code, 408)
        self.assertEqual(self.staged_files(), [])

    async def test_a_stalled_upload_gives_up_after_the_body_timeout(self) -> None:
        async def stalled() -> AsyncIterator[bytes]:
            yield b'{"format"'
            await asyncio.Event().wait()

        with self.assertRaises(RequestTimeout):
            await backup.stage(backup._read_within(stalled(), 0.05), None)
        self.assertEqual(self.staged_files(), [])


class BodyLimitTest(BackupCase):
    async def test_restore_upload_up_to_the_backup_limit_is_accepted(self) -> None:
        # Quart weigert standaard alles boven 16 MiB met 413. Een bestand van precies de back-upgrens moet erdoor: het is geen
        # geldige back-up, dus de validator mag 400 geven, maar de aanvraag mag niet al bij de lichaamsgrens stranden.
        body = bytes(backup.MAX_STAGE_BYTES)
        response = await self.stage(body)
        self.assertNotEqual(response.status_code, 413, response.get_data()[:200])
        self.assertLess(response.status_code, 500)


class PageTest(unittest.TestCase):
    HTML = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    JS = (ROOT / "static" / "backup.js").read_text(encoding="utf-8")

    def test_the_card_has_a_live_progress_block_and_loads_after_app_js(self) -> None:
        self.assertRegex(self.HTML, r'<div class="backup-progress" id="backup-progress" role="status" hidden>')
        self.assertIn("<progress", self.HTML)
        self.assertLess(self.HTML.index("filename='app.js'"), self.HTML.index("filename='backup.js'"))
        for element in ("backup-make", "backup-file", "backup-restore", "backup-discard", "backup-confirm", "backup-links"):
            self.assertIn(f'id="{element}"', self.HTML)

    def test_the_screen_follows_the_contract(self) -> None:
        # Alleen een balk vullen die zijn totaal kent, de klus op de server volgen en niet herladen als wegklikken mislukt.
        for needle in ("/api/backup/stage", "/api/backup/jobs/latest", "XMLHttpRequest", "upload.onprogress", "Content-Length", "dismissed", "BACKUP_POLL_MS"):
            self.assertIn(needle, self.JS)
        self.assertNotIn("setInterval", self.JS)
        self.assertNotIn("file.text()", self.JS)  # het bestand wordt op de server gecontroleerd, niet in de browser

    def test_the_system_tab_opens_the_card(self) -> None:
        self.assertIn('"settings-system" && typeof loadBackup === "function"', (ROOT / "static" / "app.js").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
