"""Back-up en terugzetten: alle gegevens van deze installatie als één bestand dat de beheerder zelf bewaart.

Het volgt het patroon in `shared/guidelines/backup-restore.md` van Flux76HQ/App-Guidance (back-up en herstel in de app,
met zichtbare voortgang). Kort:

* Het bestand is één JSON-bestand: de koppelingen met Mealie en de supermarkten, de instellingen en alle tabellen met
  gegevens (lijstjes, bestellingen, kassabonnen, aanbiedingen en de hele historie daarvan, de bewaarde afbeeldingen van
  aanbiedingen die je kocht, de beoordelingen van aanbiedingen bij recepten, de profielen). Afbeeldingen staan er als
  base64 in en worden bij het terugzetten opnieuw gelezen.
* **De koppelingen zijn de uitzondering op "een back-up opent nooit de app".** Mealie-token, AH-, Picnic- en
  Jumbo-sessie en AI-sleutels staan alleen in het bestand als je er bij het maken een wachtwoord voor kiest; ze zijn
  daarmee versleuteld (scrypt + Fernet). Zonder dat wachtwoord is het bestand zonder koppelingen terug te zetten niet
  mogelijk. Wat de app zelf ontgrendelt staat er nooit in: passkeys, wachtwoorden, tweestapsverificatie, herstelcodes,
  uitnodigingen, sessies en de tokens van AI-assistenten (MCP/OAuth). Ook niet het geheim `secret.key` zelf.
* Terugzetten is alles of niets. Het bestand wordt niet vertrouwd: elke tabel, kolom en waarde wordt eerst tegen een
  lege kopie van het schema gelegd, onbekende onderdelen worden geweigerd en daarna gaat alles in één transactie
  over de bestaande gegevens heen. Een geweigerd bestand verandert niets.
* Het terugzetten is werk van de server: de browser start het en kijkt mee. De staat van de klus staat in de database
  (`backup_jobs`), dus een andere werker, een herladen pagina of een gesloten browser maakt niets uit.
* De klus is een achtergrondtask (`tasks.spawn`). Het zware werk (het bestand maken, lezen en controleren, en het
  vervangen in een transactie) loopt per fase als geheel in `asyncio.to_thread`, op een eigen sqlite3-verbinding. Alles
  wat de event loop zelf doet (de klus starten, de stand lezen en bijwerken, wegklikken) gaat via aiosqlite.

Buiten de back-up blijven: het auditlog (een logboek dat na 30 dagen vanzelf wordt opgeruimd), de bewaarde
Mealie-afbeeldingen (een cache die vanzelf weer wordt gevuld) en het webadres dat de beheerder voor deze server instelde
(dat hoort bij de server, niet bij de gegevens).
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import os
import re
import secrets
import sqlite3
import time
from collections.abc import AsyncIterable, AsyncIterator, Callable, Iterator
from contextlib import aclosing, asynccontextmanager, closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiosqlite
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from quart import Quart, Response, jsonify, request

from . import about, ah_lists, ah_orders, ah_recipes, ai_review, auth, crisp, crisp_orders, hellofresh, jumbo, jumbo_lists, jumbo_orders, marleyspoon, mealie, offers, offers_schedule, picnic, picnic_orders, price_import, prices, product_images, product_links, profile, server_settings, tasks, uploads
from .i18n import tr

log = logging.getLogger(__name__)

FORMAT = "mealieshopper-backup"
FORMAT_VERSION = 1

MAX_STAGE_BYTES = 200 * 1024 * 1024  # één bestand; een back-up van jaren aanbiedingen is enkele megabytes
CHUNK = 1024 * 1024
STAGE_TTL_SECONDS = 3600  # een bestand dat niet gebruikt wordt, ruimen we na een uur op
JOB_STALE_SECONDS = 15 * 60  # een klus die zo lang niet bewoog, is z'n task kwijt (herstart)
MIN_PASSPHRASE_LENGTH = 8
SCRYPT_N = 2**15  # ~32 MB geheugen en ~0,1 s; in de tests lager gezet
SCRYPT_R, SCRYPT_P = 8, 1
SCRYPT_MAX_N = 2**16  # wat een bestand ons hoogstens laat doen: de kosten staan in het (niet vertrouwde) bestand

# --- wat er in de back-up zit -----------------------------------------------------------------------------------

# Tabellen met gegevens, ouders vóór kinderen. Een nieuwe tabel met gegevens hoort hier én in de round-trip-test.
DATA_TABLES = (
    "ah_lists", "ah_list_items",
    "ah_basket", "ah_basket_items",
    "ah_orders", "ah_order_items",
    "ah_receipts", "ah_receipt_items",
    "ah_orders_meta",
    "ah_saved_recipes", "ah_saved_recipes_meta", "ah_saved_recipe_groups", "ah_saved_recipe_group_items",
    "jumbo_lists", "jumbo_list_items",
    "jumbo_orders", "jumbo_order_items", "jumbo_orders_meta",
    "picnic_orders", "picnic_order_items", "picnic_orders_meta",
    "crisp_orders", "crisp_order_items", "crisp_orders_meta",
    "offers", "offer_history", "offers_meta",
    "price_observations", "product_images", "imported_purchases",
    "product_links",  # je eigen koppelingen van bonregels en ingelezen aankopen aan een product (#238): werk van de gebruiker
    "offer_recipes", "offer_recipes_meta",
    "offer_rejections", "offer_verdicts",
    "ai_recipe_matches", "ai_recipe_runs",
    "offers_schedule",
    "user_profiles",
)
# Tabellen die er bewust niet in zitten: de inloggegevens van de app, tijdelijke staat, caches en logboeken.
EXCLUDED_TABLES = frozenset({
    "users", "passkey_credentials", "auth_challenges", "password_credentials", "mfa_totp", "mfa_recovery_codes",
    "login_attempts", "invites", "oauth_clients", "oauth_codes", "oauth_tokens",
    "app_settings",  # staat er wel in, maar apart: zie SECRET_KEYS en PLAIN_SETTINGS
    "audit_log", "mealie_images", "backup_jobs", "sqlite_sequence",
    "hellofresh_menus",  # kopie van openbare menu's van HelloFresh: herlaadbaar, dus niet in de back-up
    "marleyspoon_menus",  # idem voor Marley Spoon
    "ah_receipt_products",  # koppeling artikelnummer -> product, zoekt de volgende synchronisatie van de bonnen opnieuw op
    "image_cache",  # cache van winkelfoto's op schijf (image_cache.py), opnieuw op te bouwen
    "price_archive_jobs",  # de import van een prijsarchief die loopt (price_archive.py): tijdelijke staat
})

# Opgeslagen koppelingen (versleuteld in app_settings) en bij welke koppeling ze horen.
SECRET_KEYS: dict[str, str] = {
    mealie.SECRET_URL: "mealie",
    mealie.SECRET_TOKEN: "mealie",
    "AH_REFRESH_TOKEN": "ah",
    picnic.AUTH_KEY_SECRET: "picnic",
    jumbo.REFRESH_SECRET: "jumbo",
    crisp.CLIENT_SECRET: "crisp",
    crisp.COUNTRY_SECRET: "crisp",
    **{info["secret"]: f"ai_{name}" for name, info in ai_review.PROVIDERS.items()},
}
# Keuzes zonder geheim die mee teruggaan. Alles wat hier niet staat (AUTH_MODE, SESSION_EPOCH, ONBOARDING_*,
# PUBLIC_BASE_URL, halve inlogpogingen) blijft zoals het is, ook als een bewerkt bestand het probeert mee te geven.
PLAIN_SETTINGS = frozenset({
    ai_review.SETTING_PROVIDER,
    *(info["model_setting"] for info in ai_review.PROVIDERS.values()),
    server_settings.SETTING_MCP,
    server_settings.SETTING_TOOLS_OFF,
    mealie.SETTING_WEEK_START,
})
MAX_SETTING_CHARS = 20_000

# Onderdelen die de beheerder bij het maken kan weglaten en bij het terugzetten kan overslaan (naast de koppelingen). Een
# onderdeel dat niet in het bestand zit of niet wordt teruggezet, blijft op deze installatie zoals het is, net als de
# koppelingen. De aanbiedingen van nu haalt de app bij de volgende synchronisatie ook weer op; de historie niet.
OPTIONAL_PARTS: dict[str, tuple[str, ...]] = {
    "offers": ("offers", "offers_meta"),  # de aanbiedingen van nu en wanneer ze per winkel zijn opgehaald
    "offerHistory": ("offer_history",),
}

# Wat de beheerder ziet: tabellen samengevoegd tot een paar regels.
COUNT_GROUPS: dict[str, tuple[str, ...]] = {
    "lists": ("ah_lists", "jumbo_lists"),
    "orders": ("ah_orders", "jumbo_orders", "picnic_orders", "crisp_orders"),
    "receipts": ("ah_receipts",),
    "offers": ("offers",),
    "offerHistory": ("offer_history",),
    "prices": ("price_observations",),
    "productImages": ("product_images",),
    "importedPurchases": ("imported_purchases",),
    "productLinks": ("product_links",),
    "recipes": ("ah_saved_recipes", "offer_recipes"),
    "judgements": ("offer_rejections", "offer_verdicts", "ai_recipe_matches"),
    "profiles": ("user_profiles",),
}

SCHEMA_OWNERS = (ah_lists, ah_orders, ah_recipes, crisp_orders, hellofresh, jumbo_lists, jumbo_orders, marleyspoon, offers, offers_schedule, picnic_orders, price_import, prices, product_links, profile)

JOB_SCHEMA = """
CREATE TABLE IF NOT EXISTS backup_jobs (
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  phase TEXT NOT NULL,
  step INTEGER NOT NULL DEFAULT 0,
  steps INTEGER NOT NULL DEFAULT 0,
  error_key TEXT,
  error_params TEXT,
  summary TEXT,
  checking_seconds REAL,
  replacing_seconds REAL,
  dismissed INTEGER NOT NULL DEFAULT 0,
  started_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  finished_at TEXT
);
"""

# De SQL van de klussen, gedeeld door de synchrone helper in de thread en zijn async tweeling op de loop.
JOB_UPDATE_SQL = "UPDATE backup_jobs SET {assignments} WHERE id = ?"
# Een klus die niet meer verder komt (task kwijt na een herstart, of geannuleerd bij het afsluiten): mislukt, er is niets gewijzigd.
JOB_INTERRUPTED = "status = 'failed', error_key = 'interrupted', finished_at = ?"
JOB_GIVE_UP_STALE_SQL = f"UPDATE backup_jobs SET {JOB_INTERRUPTED} WHERE status = 'running' AND updated_at < ?"
JOB_INTERRUPT_SQL = f"UPDATE backup_jobs SET {JOB_INTERRUPTED}, updated_at = ? WHERE id = ? AND status = 'running'"
JOB_LATEST_SQL = "SELECT * FROM backup_jobs WHERE status = 'running' OR dismissed = 0 ORDER BY started_at DESC, rowid DESC LIMIT 1"
JOB_DISMISS_SQL = "UPDATE backup_jobs SET dismissed = 1 WHERE id = ? AND status != 'running'"
# Ook de foutvelden leeg: een klus die bij het afsluiten al onderbroken heette, kan in zijn thread toch nog klaar komen.
JOB_DONE_SQL = (
    "UPDATE backup_jobs SET status = 'done', phase = 'replacing', error_key = NULL, error_params = NULL, summary = ?,"
    " replacing_seconds = ?, updated_at = ?, finished_at = ? WHERE id = ?"
)

TOKEN_PATTERN = re.compile(r"^[0-9a-f]{32}$")
STAGE_PREFIX = ".restore-"
FERNET_PREFIX = "gAAAA"

# --- meldingen --------------------------------------------------------------------------------------------------

MESSAGES: dict[str, tuple[str, str]] = {
    "not_a_backup": (
        "Dit is geen MealieShopper-back-up. Kies een bestand dat je met Back-up maken hebt gemaakt.",
        "This is not a MealieShopper backup. Choose a file you made with Make backup.",
    ),
    "too_large": ("Het bestand is groter dan {limit} MB.", "The file is larger than {limit} MB."),
    "newer_version": (
        "Deze back-up is gemaakt met een nieuwere versie van MealieShopper ({app_version}, back-upformaat {found}; deze versie kent formaat {supported}). Werk MealieShopper eerst bij.",
        "This backup was made by a newer version of MealieShopper ({app_version}, backup format {found}; this version knows format {supported}). Update MealieShopper first.",
    ),
    "bad_table": ("De back-up bevat een onderdeel dat hier niet hoort: {table}.", "The backup contains a part that does not belong here: {table}."),
    "bad_structure": ("De back-up is beschadigd of bewerkt: {detail}.", "The backup is damaged or edited: {detail}."),
    "bad_row": ("{table}, regel {line}: {error}", "{table}, row {line}: {error}"),
    "bad_part": ("Dit onderdeel bestaat niet in een back-up: {part}.", "This part does not exist in a backup: {part}."),
    "bad_setting": ("De back-up bevat een instelling die hier niet hoort: {key}.", "The backup contains a setting that does not belong here: {key}."),
    "needs_passphrase": (
        "Deze back-up bevat koppelingen en is versleuteld. Vul het wachtwoord in dat je bij het maken koos.",
        "This backup contains links and is encrypted. Enter the password you chose when you made it.",
    ),
    "wrong_passphrase": ("Dit wachtwoord klopt niet voor deze back-up.", "This password is not right for this backup."),
    "passphrase_short": (
        "Kies een wachtwoord van minstens {minimum} tekens om de koppelingen in het bestand te versleutelen.",
        "Choose a password of at least {minimum} characters to encrypt the links in the file.",
    ),
    "stage_missing": (
        "De gekozen back-up is niet meer beschikbaar. Kies het bestand opnieuw.",
        "The chosen backup is no longer available. Choose the file again.",
    ),
    "busy": ("Er loopt al een back-up of herstel. Wacht tot die klaar is.", "A backup or restore is already running. Wait until it has finished."),
    "interrupted": (
        "Het terugzetten is onderbroken (bijvoorbeeld door een herstart van de app). Er is niets gewijzigd; kies de back-up opnieuw.",
        "The restore was interrupted (for example by a restart of the app). Nothing was changed; choose the backup again.",
    ),
    "failed": (
        "Het terugzetten is mislukt. Er is niets gewijzigd. Kijk in het logboek van de container voor de reden.",
        "The restore failed. Nothing was changed. See the container log for the reason.",
    ),
    "unknown_job": ("Deze klus bestaat niet (meer).", "This job no longer exists."),
}


class BackupError(Exception):
    """Een melding voor de beheerder, als sleutel plus waarden; de tekst volgt pas bij het lezen, in de taal van de lezer."""

    def __init__(self, code: str, status: int = 400, **params: Any):
        super().__init__(code)
        self.key = code
        self.status = status
        self.params = params


def describe(key: str, params: dict[str, Any] | None = None) -> str:
    nl, en = MESSAGES.get(key, MESSAGES["failed"])
    text = tr(nl, en)
    try:
        return text.format(**(params or {}))
    except (KeyError, IndexError):
        return text


# --- hulpjes ----------------------------------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _thread_connect() -> sqlite3.Connection:
    """Een eigen synchrone verbinding voor het werk in een thread (`asyncio.to_thread`), niet voor de event loop.

    De tabellen bestaan dan al: wie de thread start, roept eerst `ensure_schemas` aan."""
    auth.data_dir().mkdir(parents=True, exist_ok=True)
    conn: sqlite3.Connection = sqlite3.connect(auth.db_path())
    conn.row_factory = sqlite3.Row
    return conn


async def ensure_schemas() -> None:
    """Zorgt dat elke tabel van de gegevens bestaat (de modules maken hun tabellen pas bij het eerste gebruik).

    Op de event loop, voordat een fase naar zijn thread gaat: elke module maakt haar tabellen en migraties met haar eigen
    `_connect`, dus het schema staat op een plek. De thread zelf werkt daarna alleen met `_thread_connect`.
    """
    await auth.ensure_db()
    for module in SCHEMA_OWNERS:
        async with module._connect():
            pass
    async with _ajobs_connect():
        pass


def _jobs_connect() -> sqlite3.Connection:
    """De tabel van de klussen op een verbinding voor een thread (de voortgang tijdens het controleren)."""
    conn: sqlite3.Connection = _thread_connect()
    conn.executescript(JOB_SCHEMA)
    return conn


@asynccontextmanager
async def _ajobs_connect() -> AsyncIterator[aiosqlite.Connection]:
    """Async tweeling van `_jobs_connect`, voor de event loop. Sluit zonder commit, zoals `auth.connect`."""
    await auth.ensure_db()
    async with auth.connect() as conn:
        await conn.executescript(JOB_SCHEMA)
        yield conn


def _columns(conn: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    return conn.execute(f"PRAGMA table_info({_quote(table)})").fetchall()


def _encode(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"base64": base64.b64encode(bytes(value)).decode("ascii")}
    return value


def _decode(value: Any) -> Any:
    if isinstance(value, bool):
        return int(value)
    if value is None or isinstance(value, (str, int, float)):
        return value
    if isinstance(value, dict) and set(value) == {"base64"} and isinstance(value["base64"], str):
        try:
            return base64.b64decode(value["base64"], validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("invalid base64") from None
    raise ValueError("unsupported value")


def _derive_key(passphrase: str, salt: bytes, n: int) -> bytes:
    raw = Scrypt(salt=salt, length=32, n=n, r=SCRYPT_R, p=SCRYPT_P).derive(passphrase.encode("utf-8"))
    return base64.urlsafe_b64encode(raw)


def seal(payload: dict[str, str], passphrase: str) -> dict[str, Any]:
    salt = os.urandom(16)
    token = Fernet(_derive_key(passphrase, salt, SCRYPT_N)).encrypt(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    return {
        "cipher": "fernet", "kdf": "scrypt", "n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P,
        "salt": base64.b64encode(salt).decode("ascii"), "token": token.decode("ascii"),
    }


def unseal(sealed: Any, passphrase: str) -> dict[str, str]:
    """De koppelingen uit het bestand, of een BackupError als het wachtwoord niet klopt of het blok niet te vertrouwen is."""
    try:
        if not isinstance(sealed, dict) or sealed.get("cipher") != "fernet" or sealed.get("kdf") != "scrypt":
            raise ValueError("unknown cipher")
        n, r, p = sealed.get("n"), sealed.get("r"), sealed.get("p")
        if not (isinstance(n, int) and 2**14 <= n <= SCRYPT_MAX_N and n & (n - 1) == 0 and r == SCRYPT_R and p == SCRYPT_P):
            raise ValueError("unsupported cost")
        salt = base64.b64decode(str(sealed.get("salt") or ""), validate=True)
        token = str(sealed.get("token") or "").encode("ascii")
    except (ValueError, binascii.Error, UnicodeEncodeError) as exc:
        raise BackupError("bad_structure", detail="secrets") from exc
    if not passphrase:
        raise BackupError("needs_passphrase")
    try:
        plain = Fernet(_derive_key(passphrase, salt, n)).decrypt(token)
    except InvalidToken:
        raise BackupError("wrong_passphrase", 403) from None
    try:
        data = json.loads(plain.decode("utf-8"))
    except ValueError as exc:
        raise BackupError("bad_structure", detail="secrets") from exc
    if not isinstance(data, dict):
        raise BackupError("bad_structure", detail="secrets")
    for key, value in data.items():
        if key not in SECRET_KEYS or not isinstance(value, str) or not value or len(value) > MAX_SETTING_CHARS:
            raise BackupError("bad_setting", key=str(key)[:60])
    return data


# --- de back-up maken -------------------------------------------------------------------------------------------


def _stored_secrets(conn: sqlite3.Connection, box: Fernet) -> tuple[dict[str, str], int]:
    """De opgeslagen koppelingen, ontsleuteld; en hoeveel er onleesbaar waren (andere sleutel: opnieuw koppelen)."""
    found: dict[str, str] = {}
    unreadable = 0
    rows = conn.execute(
        f"SELECT key, value FROM app_settings WHERE key IN ({','.join('?' for _ in SECRET_KEYS)})", tuple(SECRET_KEYS)
    ).fetchall()
    for row in rows:
        value = str(row["value"] or "")
        try:
            value = box.decrypt(value.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError):
            if value.startswith(FERNET_PREFIX):
                unreadable += 1
                continue
        if value.strip():
            found[row["key"]] = value.strip()
    return found, unreadable


def _rows(conn: sqlite3.Connection, table: str, names: list[str]) -> Iterator[str]:
    cursor = conn.execute(f"SELECT {', '.join(_quote(n) for n in names)} FROM {_quote(table)} ORDER BY rowid")
    for row in cursor:
        yield json.dumps({name: _encode(row[index]) for index, name in enumerate(names)}, ensure_ascii=False, separators=(",", ":"))


def write_backup(target: Path, *, links: bool, box: Fernet | None, passphrase: str = "", leave_out: Any = ()) -> dict[str, Any]:
    """Schrijft de back-up naar `target` en geeft een samenvatting (aantallen en koppelingen).

    `leave_out`: onderdelen uit OPTIONAL_PARTS die niet meegaan; het bestand zegt welke (`left_out`), zodat het terugzetten
    die onderdelen laat zoals ze zijn. Alles komt uit één leestransactie, dus het bestand is een samenhangend beeld ook als
    de app ondertussen doorwerkt. `box` ontsleutelt de opgeslagen koppelingen (`auth.secret_box`). Eerst `ensure_schemas`.
    """
    if links and len(passphrase) < MIN_PASSPHRASE_LENGTH:
        raise BackupError("passphrase_short", minimum=MIN_PASSPHRASE_LENGTH)
    left_out = sorted(set(leave_out))
    if any(part not in OPTIONAL_PARTS for part in left_out):
        raise BackupError("bad_part", part=str(next(part for part in left_out if part not in OPTIONAL_PARTS))[:40])
    skipped_tables = {table for part in left_out for table in OPTIONAL_PARTS[part]}
    counts: dict[str, int] = {}
    with closing(_thread_connect()) as conn:
        conn.execute("BEGIN")
        stored, unreadable = _stored_secrets(conn, box) if links and box else ({}, 0)
        plain = {
            row["key"]: row["value"]
            for row in conn.execute("SELECT key, value FROM app_settings ORDER BY key")
            if row["key"] in PLAIN_SETTINGS
        }
        header = {
            "format": FORMAT,
            "format_version": FORMAT_VERSION,
            "app_version": about.version(),
            "created_at": _now(),
            "includes_links": bool(links),
            "left_out": left_out,
        }
        with target.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(header, ensure_ascii=False)[:-1])
            handle.write(',"settings":' + json.dumps(plain, ensure_ascii=False))
            secrets_block = {"links": sorted({SECRET_KEYS[key] for key in stored}), "sealed": seal(stored, passphrase) if stored else None}
            handle.write(',"secrets":' + json.dumps(secrets_block, ensure_ascii=False))
            handle.write(',"tables":{')
            for number, table in enumerate(table for table in DATA_TABLES if table not in skipped_tables):
                names = [column["name"] for column in _columns(conn, table)]
                handle.write(("," if number else "") + json.dumps(table) + ":[")
                count = 0
                for line in _rows(conn, table, names):
                    handle.write(("," if count else "") + line)
                    count += 1
                handle.write("]")
                counts[table] = count
            handle.write("}}")
        conn.rollback()
    return {"counts": _group_counts(counts), "links": secrets_block["links"], "unreadableLinks": unreadable, "leftOut": left_out}


def _group_counts(per_table: dict[str, int]) -> dict[str, int]:
    return {group: sum(per_table.get(table, 0) for table in tables) for group, tables in COUNT_GROUPS.items()}


# --- het bestand lezen en controleren ---------------------------------------------------------------------------


def _reject_constant(name: str) -> Any:
    raise ValueError(f"unsupported number {name}")


def read_file(path: Path) -> dict[str, Any]:
    """Het bestand als JSON, na controle van formaat en versie. Beslist op de eerste bytes, niet op de naam."""
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            head = handle.read(4)
            handle.seek(0)
            if size > MAX_STAGE_BYTES:
                raise BackupError("too_large", 413, limit=MAX_STAGE_BYTES // (1024 * 1024))
            if head[:2] == b"PK" or not head.lstrip().startswith(b"{"):
                raise BackupError("not_a_backup")
            data = json.loads(handle.read().decode("utf-8-sig"), parse_constant=_reject_constant)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise BackupError("not_a_backup") from None
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise BackupError("not_a_backup")
    version = data.get("format_version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise BackupError("bad_structure", detail="format_version")
    if version > FORMAT_VERSION:
        raise BackupError("newer_version", found=version, supported=FORMAT_VERSION, app_version=str(data.get("app_version") or "?")[:20])
    return data


def read_plan(path: Path) -> Plan:
    """`read_file` en `prepare` in een keer: de fase die het bestand leest, als geheel voor `asyncio.to_thread`."""
    return prepare(read_file(path))


class Plan:
    """Het gecontroleerde bestand: wat er teruggezet gaat worden, klaar om in te voegen."""

    def __init__(self) -> None:
        self.created_at = ""
        self.app_version = ""
        self.links: list[str] = []
        self.sealed: Any = None
        self.plain: dict[str, str] = {}
        self.secrets: dict[str, str] | None = None  # None zolang het wachtwoord niet is gebruikt
        self.tables: dict[str, list[tuple[int, dict[str, Any]]]] = {}
        self.left_out: list[str] = []  # onderdelen die niet in het bestand zitten
        self.skipped: list[str] = []  # onderdelen die de beheerder niet terugzet

    @property
    def needs_passphrase(self) -> bool:
        return self.sealed is not None

    def skip(self, *, links: bool = False, parts: Any = ()) -> None:
        """Wat de beheerder niet wil terugzetten: dat blijft op deze installatie zoals het is.

        Zonder de koppelingen is er geen wachtwoord nodig (handig als je het kwijt bent): de koppelingen van nu blijven staan.
        """
        if links:
            self.links, self.sealed = [], None
        for part in parts:
            if part not in OPTIONAL_PARTS:
                raise BackupError("bad_part", part=str(part)[:40])
            if part not in self.left_out and part not in self.skipped:
                self.skipped.append(part)
                for table in OPTIONAL_PARTS[part]:
                    self.tables.pop(table, None)
        self.skipped.sort()

    def kept_parts(self) -> list[str]:
        """De onderdelen die het terugzetten niet aanraakt: niet in het bestand, of overgeslagen."""
        return sorted(set(self.left_out) | set(self.skipped))

    def kept_tables(self) -> set[str]:
        return {table for part in self.kept_parts() for table in OPTIONAL_PARTS[part]}

    def counts(self) -> dict[str, int]:
        return _group_counts({table: len(rows) for table, rows in self.tables.items()})


def prepare(data: dict[str, Any]) -> Plan:
    """Controleert de vorm van het bestand (niet de inhoud van de rijen; dat doet `check_rows`) en maakt er een Plan van."""
    plan = Plan()
    plan.created_at = str(data.get("created_at") or "")[:40]
    plan.app_version = str(data.get("app_version") or "")[:40]

    settings = data.get("settings", {})
    if not isinstance(settings, dict):
        raise BackupError("bad_structure", detail="settings")
    for key, value in settings.items():
        if key not in PLAIN_SETTINGS:
            raise BackupError("bad_setting", key=str(key)[:60])
        if not isinstance(value, str) or len(value) > MAX_SETTING_CHARS:
            raise BackupError("bad_structure", detail=f"settings.{str(key)[:40]}")
    plan.plain = dict(settings)

    block = data.get("secrets") or {}
    if not isinstance(block, dict):
        raise BackupError("bad_structure", detail="secrets")
    links = block.get("links") or []
    if not isinstance(links, list) or not all(isinstance(item, str) for item in links):
        raise BackupError("bad_structure", detail="secrets.links")
    plan.links = sorted(set(links) & set(SECRET_KEYS.values()))
    plan.sealed = block.get("sealed") if plan.links else None
    if plan.links and not isinstance(plan.sealed, dict):
        raise BackupError("bad_structure", detail="secrets")

    left_out = data.get("left_out") or []
    if not isinstance(left_out, list) or not all(isinstance(part, str) and part in OPTIONAL_PARTS for part in left_out):
        raise BackupError("bad_structure", detail="left_out")
    plan.left_out = sorted(set(left_out))
    absent = {table for part in plan.left_out for table in OPTIONAL_PARTS[part]}

    tables = data.get("tables", {})
    if not isinstance(tables, dict):
        raise BackupError("bad_structure", detail="tables")
    for table, rows in tables.items():
        if table not in DATA_TABLES:
            raise BackupError("bad_table", table=str(table)[:60])
        if table in absent:  # het bestand zegt dat dit onderdeel er niet in zit, maar het staat er wel: bewerkt
            raise BackupError("bad_structure", detail="left_out")
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise BackupError("bad_structure", detail=str(table))
        plan.tables[table] = list(enumerate(rows, 1))
    return plan


def _schema_copy(conn: sqlite3.Connection) -> sqlite3.Connection:
    """Een lege kopie van het schema van de gegevenstabellen, in het geheugen."""
    scratch = sqlite3.connect(":memory:")
    scratch.row_factory = sqlite3.Row
    for table in DATA_TABLES:
        row = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone()
        scratch.execute(row["sql"])
    return scratch


def _column_rules(conn: sqlite3.Connection, table: str) -> dict[str, bool]:
    columns = _columns(conn, table)
    single_integer_key = sum(1 for column in columns if column["pk"]) == 1
    return {
        column["name"]: bool(column["pk"]) and not (single_integer_key and column["type"].upper() == "INTEGER")  # een gehele sleutel telt SQLite zelf
        for column in columns
    }


def _check_profile(row: dict[str, Any]) -> None:
    avatar = row.get("avatar")
    if avatar is None:
        return
    if not isinstance(avatar, bytes) or len(avatar) > profile.MAX_AVATAR_BYTES:
        raise ValueError("avatar too large")
    if profile.sniff(avatar) != row.get("avatar_type"):  # het type volgt uit de inhoud, niet uit wat het bestand zegt
        raise ValueError("avatar is not the image it says it is")


def _decoded(table: str, number: int, row: dict[str, Any], known: dict[str, bool]) -> dict[str, Any]:
    """`known`: de kolommen van de tabel; waar is "hoort bij de sleutel en mag niet leeg zijn"."""
    unknown = sorted(set(row) - set(known))
    if unknown:
        raise BackupError("bad_row", table=table, line=number, error=f"unknown column {unknown[0][:40]}")
    for name, required in known.items():
        if required and row.get(name) is None:  # SQLite laat in een tekstsleutel stilletjes NULL toe
            raise BackupError("bad_row", table=table, line=number, error=f"{name} is empty")
    try:
        values = {name: _decode(value) for name, value in row.items()}
        if table == "user_profiles":
            _check_profile(values)
        if table == "product_images":
            values.update(product_images.checked(values.get("data")))  # type, maat en digest volgen uit de afbeelding zelf
    except ValueError as exc:
        raise BackupError("bad_row", table=table, line=number, error=str(exc)[:200]) from exc
    return values


def check_rows(plan: Plan, progress: Callable[[int, int], None] | None = None) -> dict[str, list[tuple[int, dict[str, Any]]]]:
    """Legt elke rij tegen een lege kopie van het schema (types, verplichte kolommen, dubbele sleutels).

    Geeft de rijen terug zoals ze ingevoegd gaan worden (waarden ontcijferd). Niets in de echte database verandert.
    Eerst `ensure_schemas`.
    """
    with closing(_thread_connect()) as real, closing(_schema_copy(real)) as scratch:
        known = {table: _column_rules(real, table) for table in DATA_TABLES}
        prepared: dict[str, list[tuple[int, dict[str, Any]]]] = {}
        for step, table in enumerate(DATA_TABLES, 1):
            rows = []
            for number, row in plan.tables.get(table, []):
                values = _decoded(table, number, row, known[table])
                names = list(values)
                if names:
                    sql = f"INSERT INTO {_quote(table)} ({', '.join(_quote(n) for n in names)}) VALUES ({', '.join('?' for _ in names)})"
                else:
                    sql = f"INSERT INTO {_quote(table)} DEFAULT VALUES"
                try:
                    scratch.execute(sql, [values[n] for n in names])
                except (sqlite3.Error, OverflowError) as exc:
                    raise BackupError("bad_row", table=table, line=number, error=str(exc)[:200]) from exc
                rows.append((number, values))
            prepared[table] = rows
            if progress:
                progress(step, len(DATA_TABLES))
    return prepared


def summarize(plan: Plan) -> dict[str, Any]:
    return {
        "createdAt": plan.created_at,
        "appVersion": plan.app_version,
        "counts": plan.counts(),
        "links": plan.links,
        "needsPassphrase": plan.needs_passphrase,
        "leftOut": plan.left_out,
    }


# --- terugzetten ------------------------------------------------------------------------------------------------


def replace_everything(
    plan: Plan,
    prepared: dict[str, list[tuple[int, dict[str, Any]]]],
    on_commit: Callable[[sqlite3.Connection, dict[str, Any]], None],
    box: Fernet | None,
) -> dict[str, Any]:
    """Zet alles in één transactie over de bestaande gegevens heen. `on_commit` loopt in dezelfde transactie.

    `box` versleutelt de koppelingen opnieuw met de sleutel van deze installatie (`auth.secret_box`), nodig als het plan
    koppelingen heeft. Eerst `ensure_schemas`.
    """
    skipped_profiles = 0
    conn = _thread_connect()
    conn.isolation_level = None  # we sturen BEGIN en COMMIT zelf
    try:
        conn.execute("PRAGMA busy_timeout = 30000")  # een lopende synchronisatie mag even afmaken
        conn.execute("BEGIN IMMEDIATE")
        kept = plan.kept_tables()  # niet in het bestand of overgeslagen: blijft zoals het is
        for table in reversed(DATA_TABLES):
            if table not in kept:
                conn.execute(f"DELETE FROM {_quote(table)}")
        # De eigenaar bestaat op elke installatie: zonder inloggen (of voor de eerste gebruiker) doet de app alles als OWNER_ID,
        # ook als die niet in users staat. Zonder deze regel verdween bij terugzetten zonder inlog het eigen profiel.
        users = {row["id"] for row in conn.execute("SELECT id FROM users")} | {auth.OWNER_ID}
        for table in DATA_TABLES:
            if table in kept:
                continue
            groups: dict[tuple[str, ...], list[list[Any]]] = {}
            for _, values in prepared.get(table, []):
                if table == "user_profiles" and values.get("user_id") not in users:
                    skipped_profiles += 1  # een profiel van iemand die op deze installatie niet bestaat
                    continue
                names = tuple(values)
                groups.setdefault(names, []).append([values[n] for n in names])
            for names, batch in groups.items():
                if names:
                    sql = f"INSERT INTO {_quote(table)} ({', '.join(_quote(n) for n in names)}) VALUES ({', '.join('?' for _ in names)})"
                    conn.executemany(sql, batch)
                else:
                    for _ in batch:
                        conn.execute(f"INSERT INTO {_quote(table)} DEFAULT VALUES")
        now = _now()
        for key in PLAIN_SETTINGS:
            conn.execute("DELETE FROM app_settings WHERE key = ?", (key,))
        for key, value in plan.plain.items():
            conn.execute("INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)", (key, value, now))
        if plan.secrets is not None:
            if box is None:
                raise ValueError("replace_everything: koppelingen terugzetten vraagt om `box`")
            # Een back-up met koppelingen is de nieuwe stand: een koppeling die er niet in zit, verdwijnt hier.
            for key in SECRET_KEYS:
                conn.execute("DELETE FROM app_settings WHERE key = ?", (key,))
            for key, value in plan.secrets.items():
                token = box.encrypt(value.encode("utf-8")).decode("ascii")
                conn.execute("INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)", (key, token, now))
        summary = {
            "counts": plan.counts(),
            "links": sorted({SECRET_KEYS[key] for key in plan.secrets}) if plan.secrets is not None else [],
            "linksKept": plan.secrets is None,
            "skippedProfiles": skipped_profiles,
            "kept": plan.kept_parts(),
        }
        on_commit(conn, summary)
        conn.execute("COMMIT")
        return summary
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        conn.close()


# --- klaargezet bestand (stage) ---------------------------------------------------------------------------------


def _stage_path(token: str) -> Path:
    if not TOKEN_PATTERN.match(str(token or "")):
        raise BackupError("stage_missing", 404)
    return auth.data_dir() / f"{STAGE_PREFIX}{token}"


def remove_old_staged_files() -> None:
    cutoff = time.time() - STAGE_TTL_SECONDS
    for path in auth.data_dir().glob(f"{STAGE_PREFIX}*"):
        try:
            if not path.name.endswith(".job") and path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            continue


async def stage(body: AsyncIterable[bytes], content_length: int | None) -> dict[str, Any]:
    """Slaat het geüploade bestand op, controleert het helemaal en geeft een samenvatting. De database verandert niet.

    Het lichaam komt in stukken binnen (`request.body`) en gaat zo naar schijf, nooit helemaal in het geheugen. Een
    Content-Length boven de grens wordt geweigerd voor er iets gelezen is.
    """
    if content_length is not None and content_length > MAX_STAGE_BYTES:
        raise BackupError("too_large", 413, limit=MAX_STAGE_BYTES // (1024 * 1024))
    auth.data_dir().mkdir(parents=True, exist_ok=True)
    remove_old_staged_files()
    token: str = secrets.token_hex(16)
    path: Path = _stage_path(token)
    received: int = 0
    summary: dict[str, Any]
    try:
        with path.open("wb") as handle:
            os.chmod(path, 0o600)
            async for chunk in body:
                received += len(chunk)
                if received > MAX_STAGE_BYTES:
                    raise BackupError("too_large", 413, limit=MAX_STAGE_BYTES // (1024 * 1024))
                handle.write(chunk)  # lokale schijf en stukken van hoogstens een paar MB: dat mag op de loop
        await ensure_schemas()
        summary = await asyncio.to_thread(_check_staged, path)
    except BaseException:
        path.unlink(missing_ok=True)  # een geweigerd bestand blijft niet staan
        raise
    return {"token": token, **summary}


async def stage_upload(token: str, size: Any) -> dict[str, Any]:
    """Als `stage`, voor een bestand dat in stukken binnenkwam (uploads.py): het krijgt hetzelfde token."""
    remove_old_staged_files()
    path: Path = _stage_path(token)
    (await uploads.take(STAGE_PREFIX, token, size)).replace(path)
    summary: dict[str, Any]
    try:
        await ensure_schemas()
        summary = await asyncio.to_thread(_check_staged, path)
    except BaseException:
        path.unlink(missing_ok=True)  # een geweigerd bestand blijft niet staan
        raise
    return {"token": token, **summary}


def _check_staged(path: Path) -> dict[str, Any]:
    """Lezen en controleren van een klaargezet bestand, als geheel voor `asyncio.to_thread`."""
    plan: Plan = read_plan(path)
    check_rows(plan)
    return summarize(plan)


async def discard(token: str) -> None:
    _stage_path(token).unlink(missing_ok=True)


# --- klussen ----------------------------------------------------------------------------------------------------


def _job_view(row: sqlite3.Row) -> dict[str, Any]:
    params = json.loads(row["error_params"]) if row["error_params"] else {}
    return {
        "id": row["id"],
        "status": row["status"],
        "phase": row["phase"],
        "step": row["step"],
        "steps": row["steps"],
        "error": describe(row["error_key"], params) if row["error_key"] else None,
        "summary": json.loads(row["summary"]) if row["summary"] else None,
        "checkingSeconds": row["checking_seconds"],
        "replacingSeconds": row["replacing_seconds"],
    }


def _stale_cutoff() -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=JOB_STALE_SECONDS)).isoformat(timespec="seconds")


async def _give_up_stale_jobs(conn: aiosqlite.Connection) -> None:
    await conn.execute(JOB_GIVE_UP_STALE_SQL, (_now(), _stale_cutoff()))


def _job_update_statement(job_id: str, fields: dict[str, Any]) -> tuple[str, tuple[Any, ...]]:
    """De UPDATE voor `_job_update` en `_ajob_update`: dezelfde velden, plus `updated_at`."""
    values: dict[str, Any] = {**fields, "updated_at": _now()}
    assignments: str = ", ".join(f"{name} = ?" for name in values)
    return JOB_UPDATE_SQL.format(assignments=assignments), (*values.values(), job_id)


def _job_update(job_id: str, **fields: Any) -> None:
    """Synchroon, voor de thread van een fase (de voortgang tijdens het controleren)."""
    sql, params = _job_update_statement(job_id, fields)
    with closing(_jobs_connect()) as conn, conn:
        conn.execute(sql, params)


async def _ajob_update(job_id: str, **fields: Any) -> None:
    """Async tweeling van `_job_update`, voor de event loop (tussen de fases)."""
    sql, params = _job_update_statement(job_id, fields)
    async with _ajobs_connect() as conn:
        await conn.execute(sql, params)
        await conn.commit()


async def _fail_job(job_id: str, error: BackupError | None) -> None:
    key: str = error.key if error else "failed"
    params: dict[str, Any] = error.params if error else {}
    await _ajob_update(job_id, status="failed", error_key=key, error_params=json.dumps(params), finished_at=_now())


async def _interrupt_job(job_id: str) -> None:
    """Een geannuleerde klus (de app sluit af) krijgt de stand die `_give_up_stale_jobs` hem na een herstart zou geven.

    Alleen zolang hij nog loopt: heeft het vervangen in zijn thread toch nog vastgelegd dat hij klaar is, dan klopt dat.
    """
    now: str = _now()
    async with _ajobs_connect() as conn:
        await conn.execute(JOB_INTERRUPT_SQL, (now, now, job_id))
        await conn.commit()


async def latest_job() -> dict[str, Any] | None:
    """De klus die loopt, of de laatst afgeronde die de beheerder nog niet heeft weggeklikt."""
    async with _ajobs_connect() as conn:
        await _give_up_stale_jobs(conn)
        await conn.commit()
        async with conn.execute(JOB_LATEST_SQL) as cursor:
            row: sqlite3.Row | None = await cursor.fetchone()
    return _job_view(row) if row else None


async def dismiss_job(job_id: str) -> bool:
    async with _ajobs_connect() as conn:
        cursor: aiosqlite.Cursor = await conn.execute(JOB_DISMISS_SQL, (job_id,))
        await conn.commit()
    return cursor.rowcount > 0


async def start_job(token: str, passphrase: str, *, skip_links: bool = False, skip_parts: Any = ()) -> dict[str, Any]:
    """Controleert het wachtwoord, neemt het klaargezette bestand over en start het terugzetten op de achtergrond.

    `skip_links` en `skip_parts` (uit OPTIONAL_PARTS): wat de beheerder niet terugzet; dat blijft zoals het is.
    """
    path: Path = _stage_path(token)
    if not path.is_file():
        raise BackupError("stage_missing", 404)
    skip_parts = tuple(skip_parts)
    plan: Plan = await asyncio.to_thread(read_plan, path)  # het bestand dat nu staat, niet wat een uur geleden is gecontroleerd
    plan.skip(links=skip_links, parts=skip_parts)
    if plan.needs_passphrase:
        await asyncio.to_thread(unseal, plan.sealed, passphrase)  # een verkeerd wachtwoord laat het bestand en het token staan
    running: Path = path.with_name(path.name + ".job")
    job_id: str = secrets.token_hex(8)
    async with _ajobs_connect() as conn:
        await conn.execute("BEGIN IMMEDIATE")
        try:
            await _give_up_stale_jobs(conn)
            async with conn.execute("SELECT 1 FROM backup_jobs WHERE status = 'running'") as cursor:
                if await cursor.fetchone():
                    raise BackupError("busy", 409)
            try:
                os.replace(path, running)  # een token werkt één keer
            except FileNotFoundError:
                raise BackupError("stage_missing", 404) from None
            now: str = _now()
            await conn.execute("UPDATE backup_jobs SET dismissed = 1 WHERE status != 'running'")
            await conn.execute(
                "INSERT INTO backup_jobs (id, status, phase, step, steps, started_at, updated_at) VALUES (?, 'running', 'checking', 0, ?, ?, ?)",
                (job_id, len(DATA_TABLES), now, now),
            )
            await conn.commit()
        except BaseException:
            await conn.rollback()
            raise
    tasks.spawn(
        f"backup-restore-{job_id}", run_job(job_id, running, passphrase, skip_links=skip_links, skip_parts=skip_parts)
    )
    return {"id": job_id}


async def run_job(job_id: str, staged: Path, passphrase: str, *, skip_links: bool = False, skip_parts: Any = ()) -> None:
    """De klus zelf, als achtergrondtask (zie `_run_phases`).

    Een geweigerd bestand of een fout geeft een mislukte klus (de database is dan niet veranderd). Bij het afsluiten van
    de app wordt de task geannuleerd, ook als dat midden in het vastleggen van een fout gebeurt: de klus heet dan
    onderbroken, net als na een herstart.
    """
    try:
        try:
            await _run_phases(job_id, staged, passphrase, skip_links=skip_links, skip_parts=skip_parts)
        except BackupError as exc:
            log.warning("Terugzetten geweigerd: %s %s", exc.key, exc.params)
            await _fail_job(job_id, exc)
        except Exception:  # noqa: BLE001 - de beheerder krijgt een algemene zin, het logboek de hele fout
            log.exception("Terugzetten mislukt")
            try:
                await _fail_job(job_id, None)
            except sqlite3.Error:
                log.exception("Kon de mislukte klus niet vastleggen")
    except asyncio.CancelledError:
        log.warning("Terugzetten onderbroken: de app sluit af")
        try:
            await _interrupt_job(job_id)
        except sqlite3.Error:
            log.exception("Kon de onderbroken klus niet vastleggen")
        raise
    finally:
        staged.unlink(missing_ok=True)


async def _run_phases(job_id: str, staged: Path, passphrase: str, *, skip_links: bool = False, skip_parts: Any = ()) -> None:
    """Elke fase loopt als geheel in een thread, de stand van de klus wordt ertussen bijgewerkt."""
    started: float = time.monotonic()
    plan: Plan = await asyncio.to_thread(read_plan, staged)
    plan.skip(links=skip_links, parts=skip_parts)
    if plan.needs_passphrase:
        plan.secrets = await asyncio.to_thread(unseal, plan.sealed, passphrase)
    await ensure_schemas()
    prepared: dict[str, list[tuple[int, dict[str, Any]]]] = await asyncio.to_thread(
        check_rows, plan, lambda step, steps: _job_update(job_id, step=step, steps=steps)
    )
    checking: float = time.monotonic() - started
    # Het terugzetten zelf is één transactie en dus niet te tellen: de voortgang blijft "bezig" zonder getal.
    await _ajob_update(job_id, phase="replacing", step=0, steps=0, checking_seconds=round(checking, 2))
    replacing_started: float = time.monotonic()

    def done(conn: sqlite3.Connection, summary: dict[str, Any]) -> None:
        now: str = _now()
        conn.execute(JOB_DONE_SQL, (json.dumps(summary), round(time.monotonic() - replacing_started, 2), now, now, job_id))

    box: Fernet | None = await auth.secret_box() if plan.secrets is not None else None  # alleen voor de koppelingen
    await ensure_schemas()
    await asyncio.to_thread(replace_everything, plan, prepared, done, box)
    log.info("Back-up teruggezet in %.1f s", time.monotonic() - started)


# --- routes -----------------------------------------------------------------------------------------------------


def _error(exc: BackupError) -> tuple[Response, int]:
    return jsonify({"error": describe(exc.key, exc.params), "code": exc.key}), exc.status


def register_routes(app: Quart) -> None:
    # Alleen de beheerder: /api/backup staat in auth.ADMIN_PREFIXES.
    @app.get("/api/backup")
    async def backup_info() -> Response:
        return jsonify({"maxBytes": MAX_STAGE_BYTES, "minPassphraseLength": MIN_PASSPHRASE_LENGTH})

    @app.post("/api/backup")
    async def backup_create() -> Response | tuple[Response, int]:
        body = await request.get_json(silent=True) or {}
        links = bool(body.get("links", True))
        passphrase = str(body.get("passphrase") or "")
        leave_out = [part for part in OPTIONAL_PARTS if body.get(part, True) is False]  # standaard gaat alles mee
        started = time.monotonic()
        auth.data_dir().mkdir(parents=True, exist_ok=True)
        path = auth.data_dir() / f".backup-{secrets.token_hex(8)}.json"  # op schijf: zo kent het scherm de grootte van de download
        try:
            await ensure_schemas()
            box = await auth.secret_box() if links else None  # zonder koppelingen hoeft er niets ontsleuteld
            await asyncio.to_thread(write_backup, path, links=links, box=box, passphrase=passphrase, leave_out=leave_out)
        except BackupError as exc:
            path.unlink(missing_ok=True)
            return _error(exc)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        name = "mealieshopper-backup-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + ".json"
        # Het bestand is al van het bestandssysteem af zodra het open is: er blijft niets staan, ook niet als de download wordt afgebroken.
        handle = path.open("rb")
        size = os.fstat(handle.fileno()).st_size
        path.unlink(missing_ok=True)

        async def stream() -> AsyncIterator[bytes]:
            # Async, zodat Quart niet elk stuk via een thread leest (dat doet hij bij een gewone generator). Lokale schijf.
            try:
                while chunk := handle.read(CHUNK):
                    yield chunk
            finally:
                handle.close()

        response = Response(
            stream(),
            mimetype="application/json",
            headers={
                "Content-Length": str(size),
                "Content-Disposition": f'attachment; filename="{name}"',
                "Cache-Control": "no-store",
                "X-Backup-Seconds": f"{time.monotonic() - started:.2f}",
            },
        )
        # Geen RESPONSE_TIMEOUT: Quart breekt een gestreamd antwoord daarna stil af, en een grote back-up over een trage
        # verbinding mag gewoon lang duren. Gaat de browser weg, dan stopt het streamen (keep_running.py).
        response.timeout = None
        return response

    @app.post("/api/backup/stage")
    async def backup_stage() -> Response | tuple[Response, int]:
        try:
            async with aclosing(uploads.read_while_arriving(request.body, request.body_timeout)) as body:
                return jsonify(await stage(body, request.content_length))
        except BackupError as exc:
            return _error(exc)

    # Hetzelfde in stukken (uploads.py): zo komt ook een back-up van 100 MB door een proxy die grote verzoeken weigert.
    uploads.register_routes(app, "/api/backup/uploads", STAGE_PREFIX, MAX_STAGE_BYTES)

    @app.post("/api/backup/uploads/<token>/stage")
    async def backup_stage_upload(token: str) -> Response | tuple[Response, int]:
        body = await request.get_json(silent=True) or {}
        try:
            return jsonify(await stage_upload(token, body.get("size")))
        except uploads.UploadError as exc:
            return uploads.error_response(exc)
        except BackupError as exc:
            return _error(exc)

    @app.delete("/api/backup/stage/<token>")
    async def backup_unstage(token: str) -> Response | tuple[Response, int]:
        try:
            await discard(token)
        except BackupError as exc:
            return _error(exc)
        return jsonify({"status": "ok"})

    @app.post("/api/backup/jobs")
    async def backup_start() -> tuple[Response, int]:
        body = await request.get_json(silent=True) or {}
        try:
            job = await start_job(
                str(body.get("token") or ""), str(body.get("passphrase") or ""),
                skip_links=body.get("links", True) is False,  # standaard zet je alles terug wat in het bestand zit
                skip_parts=[part for part in OPTIONAL_PARTS if body.get(part, True) is False],
            )
        except BackupError as exc:
            return _error(exc)
        return jsonify(job), 202

    @app.get("/api/backup/jobs/latest")
    async def backup_latest() -> Response:
        return jsonify({"job": await latest_job()})

    @app.delete("/api/backup/jobs/<job_id>")
    async def backup_dismiss(job_id: str) -> Response | tuple[Response, int]:
        if not await dismiss_job(job_id):
            return _error(BackupError("unknown_job", 404))
        return jsonify({"status": "ok"})
