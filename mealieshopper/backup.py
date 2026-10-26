"""Back-up en terugzetten: alle gegevens van deze installatie als één bestand dat de beheerder zelf bewaart.

Het volgt het patroon in `shared/guidelines/backup-restore.md` van Flux76HQ/App-Guidance (back-up en herstel in de app,
met zichtbare voortgang). Kort:

* Het bestand is één JSON-bestand: de koppelingen met Mealie en de supermarkten, de instellingen en alle tabellen met
  gegevens (lijstjes, bestellingen, kassabonnen, aanbiedingen en de hele historie daarvan, de beoordelingen van
  aanbiedingen bij recepten, de profielen).
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

Buiten de back-up blijven: het auditlog (een logboek dat na 30 dagen vanzelf wordt opgeruimd), de bewaarde
Mealie-afbeeldingen (een cache die vanzelf weer wordt gevuld) en het webadres dat de beheerder voor deze server instelde
(dat hoort bij de server, niet bij de gegevens).
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from flask import Flask, Response, jsonify, request

from . import about, ah_lists, ah_orders, ah_recipes, ai_review, auth, jumbo, jumbo_lists, jumbo_orders, mealie, offers, offers_schedule, picnic, picnic_orders, profile, server_settings
from .i18n import tr

log = logging.getLogger(__name__)

FORMAT = "mealieshopper-backup"
FORMAT_VERSION = 1

MAX_STAGE_BYTES = 200 * 1024 * 1024  # één bestand; een back-up van jaren aanbiedingen is enkele megabytes
CHUNK = 1024 * 1024
STAGE_TTL_SECONDS = 3600  # een bestand dat niet gebruikt wordt, ruimen we na een uur op
JOB_STALE_SECONDS = 15 * 60  # een klus die zo lang niet bewoog, is z'n thread kwijt (herstart)
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
    "ah_saved_recipes", "ah_saved_recipes_meta",
    "jumbo_lists", "jumbo_list_items",
    "jumbo_orders", "jumbo_order_items", "jumbo_orders_meta",
    "picnic_orders", "picnic_order_items", "picnic_orders_meta",
    "offers", "offer_history", "offers_meta",
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
})

# Opgeslagen koppelingen (versleuteld in app_settings) en bij welke koppeling ze horen.
SECRET_KEYS: dict[str, str] = {
    mealie.SECRET_URL: "mealie",
    mealie.SECRET_TOKEN: "mealie",
    "AH_REFRESH_TOKEN": "ah",
    picnic.AUTH_KEY_SECRET: "picnic",
    jumbo.REFRESH_SECRET: "jumbo",
    **{info["secret"]: f"ai_{name}" for name, info in ai_review.PROVIDERS.items()},
}
# Keuzes zonder geheim die mee teruggaan. Alles wat hier niet staat (AUTH_MODE, SESSION_EPOCH, ONBOARDING_*,
# PUBLIC_BASE_URL, halve inlogpogingen) blijft zoals het is, ook als een bewerkt bestand het probeert mee te geven.
PLAIN_SETTINGS = frozenset({
    ai_review.SETTING_PROVIDER,
    *(info["model_setting"] for info in ai_review.PROVIDERS.values()),
    server_settings.SETTING_MCP,
    server_settings.SETTING_TOOLS_OFF,
})
MAX_SETTING_CHARS = 20_000

# Wat de beheerder ziet: tabellen samengevoegd tot een paar regels.
COUNT_GROUPS: dict[str, tuple[str, ...]] = {
    "lists": ("ah_lists", "jumbo_lists"),
    "orders": ("ah_orders", "jumbo_orders", "picnic_orders"),
    "receipts": ("ah_receipts",),
    "offers": ("offers",),
    "offerHistory": ("offer_history",),
    "recipes": ("ah_saved_recipes", "offer_recipes"),
    "judgements": ("offer_rejections", "offer_verdicts", "ai_recipe_matches"),
    "profiles": ("user_profiles",),
}

SCHEMA_OWNERS = (ah_lists, ah_orders, ah_recipes, jumbo_lists, jumbo_orders, offers, offers_schedule, picnic_orders, profile)

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


def ensure_schemas() -> None:
    """Zorgt dat elke tabel van de gegevens bestaat (de modules maken hun tabellen pas bij het eerste gebruik)."""
    auth.ensure_db()
    for module in SCHEMA_OWNERS:
        with closing(module._connect()):
            pass
    with closing(_jobs_connect()):
        pass


def _jobs_connect() -> sqlite3.Connection:
    auth.ensure_db()
    conn = auth.connect()
    conn.executescript(JOB_SCHEMA)
    return conn


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


def _stored_secrets(conn: sqlite3.Connection) -> tuple[dict[str, str], int]:
    """De opgeslagen koppelingen, ontsleuteld; en hoeveel er onleesbaar waren (andere sleutel: opnieuw koppelen)."""
    found: dict[str, str] = {}
    unreadable = 0
    rows = conn.execute(
        f"SELECT key, value FROM app_settings WHERE key IN ({','.join('?' for _ in SECRET_KEYS)})", tuple(SECRET_KEYS)
    ).fetchall()
    for row in rows:
        value = str(row["value"] or "")
        try:
            value = auth.secret_box().decrypt(value.encode("ascii")).decode("utf-8")
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


def write_backup(target: Path, *, links: bool, passphrase: str = "") -> dict[str, Any]:
    """Schrijft de back-up naar `target` en geeft een samenvatting (aantallen en koppelingen).

    Alles komt uit één leestransactie, dus het bestand is een samenhangend beeld ook als de app ondertussen doorwerkt.
    """
    if links and len(passphrase) < MIN_PASSPHRASE_LENGTH:
        raise BackupError("passphrase_short", minimum=MIN_PASSPHRASE_LENGTH)
    ensure_schemas()
    counts: dict[str, int] = {}
    with closing(auth.connect()) as conn:
        conn.execute("BEGIN")
        stored, unreadable = _stored_secrets(conn) if links else ({}, 0)
        plain = {
            row["key"]: row["value"]
            for row in conn.execute("SELECT key, value FROM app_settings ORDER BY key")
            if row["key"] in PLAIN_SETTINGS
        }
        header = {
            "format": FORMAT,
            "format_version": FORMAT_VERSION,
            "app_version": about.read_version(),
            "created_at": _now(),
            "includes_links": bool(links),
        }
        with target.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(header, ensure_ascii=False)[:-1])
            handle.write(',"settings":' + json.dumps(plain, ensure_ascii=False))
            secrets_block = {"links": sorted({SECRET_KEYS[key] for key in stored}), "sealed": seal(stored, passphrase) if stored else None}
            handle.write(',"secrets":' + json.dumps(secrets_block, ensure_ascii=False))
            handle.write(',"tables":{')
            for number, table in enumerate(DATA_TABLES):
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
    return {"counts": _group_counts(counts), "links": secrets_block["links"], "unreadableLinks": unreadable}


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

    @property
    def needs_passphrase(self) -> bool:
        return self.sealed is not None

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

    tables = data.get("tables", {})
    if not isinstance(tables, dict):
        raise BackupError("bad_structure", detail="tables")
    for table, rows in tables.items():
        if table not in DATA_TABLES:
            raise BackupError("bad_table", table=str(table)[:60])
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
    except ValueError as exc:
        raise BackupError("bad_row", table=table, line=number, error=str(exc)[:200]) from exc
    return values


def check_rows(plan: Plan, progress: Callable[[int, int], None] | None = None) -> dict[str, list[tuple[int, dict[str, Any]]]]:
    """Legt elke rij tegen een lege kopie van het schema (types, verplichte kolommen, dubbele sleutels).

    Geeft de rijen terug zoals ze ingevoegd gaan worden (waarden ontcijferd). Niets in de echte database verandert.
    """
    ensure_schemas()
    with closing(auth.connect()) as real, closing(_schema_copy(real)) as scratch:
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
    }


# --- terugzetten ------------------------------------------------------------------------------------------------


def replace_everything(
    plan: Plan,
    prepared: dict[str, list[tuple[int, dict[str, Any]]]],
    on_commit: Callable[[sqlite3.Connection, dict[str, Any]], None],
) -> dict[str, Any]:
    """Zet alles in één transactie over de bestaande gegevens heen. `on_commit` loopt in dezelfde transactie."""
    ensure_schemas()
    skipped_profiles = 0
    conn = auth.connect()
    conn.isolation_level = None  # we sturen BEGIN en COMMIT zelf
    try:
        conn.execute("PRAGMA busy_timeout = 30000")  # een lopende synchronisatie mag even afmaken
        conn.execute("BEGIN IMMEDIATE")
        for table in reversed(DATA_TABLES):
            conn.execute(f"DELETE FROM {_quote(table)}")
        users = {row["id"] for row in conn.execute("SELECT id FROM users")}
        for table in DATA_TABLES:
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
            # Een back-up met koppelingen is de nieuwe stand: een koppeling die er niet in zit, verdwijnt hier.
            for key in SECRET_KEYS:
                conn.execute("DELETE FROM app_settings WHERE key = ?", (key,))
            for key, value in plan.secrets.items():
                token = auth.secret_box().encrypt(value.encode("utf-8")).decode("ascii")
                conn.execute("INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)", (key, token, now))
        summary = {
            "counts": plan.counts(),
            "links": sorted({SECRET_KEYS[key] for key in plan.secrets}) if plan.secrets is not None else [],
            "linksKept": plan.secrets is None,
            "skippedProfiles": skipped_profiles,
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


def stage(stream: Any, content_length: int | None) -> dict[str, Any]:
    """Slaat het geüploade bestand op, controleert het helemaal en geeft een samenvatting. De database verandert niet."""
    if content_length is not None and content_length > MAX_STAGE_BYTES:
        raise BackupError("too_large", 413, limit=MAX_STAGE_BYTES // (1024 * 1024))
    auth.data_dir().mkdir(parents=True, exist_ok=True)
    remove_old_staged_files()
    token = secrets.token_hex(16)
    path = _stage_path(token)
    received = 0
    try:
        with path.open("wb") as handle:
            os.chmod(path, 0o600)
            while True:
                chunk = stream.read(CHUNK)
                if not chunk:
                    break
                received += len(chunk)
                if received > MAX_STAGE_BYTES:
                    raise BackupError("too_large", 413, limit=MAX_STAGE_BYTES // (1024 * 1024))
                handle.write(chunk)
        plan = prepare(read_file(path))
        check_rows(plan)
        summary = summarize(plan)
    except BaseException:
        path.unlink(missing_ok=True)  # een geweigerd bestand blijft niet staan
        raise
    return {"token": token, **summary}


def discard(token: str) -> None:
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


def _give_up_stale_jobs(conn: sqlite3.Connection) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=JOB_STALE_SECONDS)).isoformat(timespec="seconds")
    conn.execute(
        "UPDATE backup_jobs SET status = 'failed', error_key = 'interrupted', finished_at = ? WHERE status = 'running' AND updated_at < ?",
        (_now(), cutoff),
    )


def _job_update(job_id: str, **fields: Any) -> None:
    fields["updated_at"] = _now()
    assignments = ", ".join(f"{name} = ?" for name in fields)
    with closing(_jobs_connect()) as conn, conn:
        conn.execute(f"UPDATE backup_jobs SET {assignments} WHERE id = ?", (*fields.values(), job_id))


def _fail_job(job_id: str, error: BackupError | None) -> None:
    key = error.key if error else "failed"
    params = error.params if error else {}
    _job_update(job_id, status="failed", error_key=key, error_params=json.dumps(params), finished_at=_now())


def latest_job() -> dict[str, Any] | None:
    """De klus die loopt, of de laatst afgeronde die de beheerder nog niet heeft weggeklikt."""
    with closing(_jobs_connect()) as conn, conn:
        _give_up_stale_jobs(conn)
        row = conn.execute(
            "SELECT * FROM backup_jobs WHERE status = 'running' OR dismissed = 0 ORDER BY started_at DESC, rowid DESC LIMIT 1"
        ).fetchone()
    return _job_view(row) if row else None


def dismiss_job(job_id: str) -> bool:
    with closing(_jobs_connect()) as conn, conn:
        cursor = conn.execute("UPDATE backup_jobs SET dismissed = 1 WHERE id = ? AND status != 'running'", (job_id,))
    return cursor.rowcount > 0


def start_job(token: str, passphrase: str) -> dict[str, Any]:
    """Controleert het wachtwoord, neemt het klaargezette bestand over en start het terugzetten op de achtergrond."""
    path = _stage_path(token)
    if not path.is_file():
        raise BackupError("stage_missing", 404)
    plan = prepare(read_file(path))  # het bestand dat nu staat, niet wat een uur geleden is gecontroleerd
    if plan.needs_passphrase:
        unseal(plan.sealed, passphrase)  # een verkeerd wachtwoord laat het bestand en het token staan
    running = path.with_name(path.name + ".job")
    job_id = secrets.token_hex(8)
    with closing(_jobs_connect()) as conn:
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        try:
            _give_up_stale_jobs(conn)
            if conn.execute("SELECT 1 FROM backup_jobs WHERE status = 'running'").fetchone():
                raise BackupError("busy", 409)
            try:
                os.replace(path, running)  # een token werkt één keer
            except FileNotFoundError:
                raise BackupError("stage_missing", 404) from None
            now = _now()
            conn.execute("UPDATE backup_jobs SET dismissed = 1 WHERE status != 'running'")
            conn.execute(
                "INSERT INTO backup_jobs (id, status, phase, step, steps, started_at, updated_at) VALUES (?, 'running', 'checking', 0, ?, ?, ?)",
                (job_id, len(DATA_TABLES), now, now),
            )
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    thread = threading.Thread(target=run_job, args=(job_id, running, passphrase), name=f"backup-restore-{job_id}", daemon=True)
    thread.start()
    return {"id": job_id}


def run_job(job_id: str, staged: Path, passphrase: str) -> None:
    started = time.monotonic()
    try:
        plan = prepare(read_file(staged))
        if plan.needs_passphrase:
            plan.secrets = unseal(plan.sealed, passphrase)
        prepared = check_rows(plan, lambda step, steps: _job_update(job_id, step=step, steps=steps))
        checking = time.monotonic() - started
        # Het terugzetten zelf is één transactie en dus niet te tellen: de voortgang blijft "bezig" zonder getal.
        _job_update(job_id, phase="replacing", step=0, steps=0, checking_seconds=round(checking, 2))
        replacing_started = time.monotonic()

        def done(conn: sqlite3.Connection, summary: dict[str, Any]) -> None:
            conn.execute(
                "UPDATE backup_jobs SET status = 'done', phase = 'replacing', summary = ?, replacing_seconds = ?, updated_at = ?, finished_at = ? WHERE id = ?",
                (json.dumps(summary), round(time.monotonic() - replacing_started, 2), _now(), _now(), job_id),
            )

        replace_everything(plan, prepared, done)
        log.info("Back-up teruggezet in %.1f s", time.monotonic() - started)
    except BackupError as exc:
        log.warning("Terugzetten geweigerd: %s %s", exc.key, exc.params)
        _fail_job(job_id, exc)
    except Exception:  # noqa: BLE001 - de beheerder krijgt een algemene zin, het logboek de hele fout
        log.exception("Terugzetten mislukt")
        try:
            _fail_job(job_id, None)
        except sqlite3.Error:
            log.exception("Kon de mislukte klus niet vastleggen")
    finally:
        staged.unlink(missing_ok=True)


# --- routes -----------------------------------------------------------------------------------------------------


def _error(exc: BackupError):
    return jsonify({"error": describe(exc.key, exc.params), "code": exc.key}), exc.status


def register_routes(app: Flask) -> None:
    # Alleen de beheerder: /api/backup staat in auth.ADMIN_PREFIXES.
    @app.get("/api/backup")
    def backup_info():
        return jsonify({"maxBytes": MAX_STAGE_BYTES, "minPassphraseLength": MIN_PASSPHRASE_LENGTH})

    @app.post("/api/backup")
    def backup_create():
        body = request.get_json(silent=True) or {}
        links = bool(body.get("links", True))
        passphrase = str(body.get("passphrase") or "")
        started = time.monotonic()
        auth.data_dir().mkdir(parents=True, exist_ok=True)
        path = auth.data_dir() / f".backup-{secrets.token_hex(8)}.json"  # op schijf: zo kent het scherm de grootte van de download
        try:
            write_backup(path, links=links, passphrase=passphrase)
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

        def stream() -> Iterator[bytes]:
            try:
                while chunk := handle.read(CHUNK):
                    yield chunk
            finally:
                handle.close()

        return Response(
            stream(),
            mimetype="application/json",
            headers={
                "Content-Length": str(size),
                "Content-Disposition": f'attachment; filename="{name}"',
                "Cache-Control": "no-store",
                "X-Backup-Seconds": f"{time.monotonic() - started:.2f}",
            },
        )

    @app.post("/api/backup/stage")
    def backup_stage():
        try:
            return jsonify(stage(request.stream, request.content_length))
        except BackupError as exc:
            return _error(exc)

    @app.delete("/api/backup/stage/<token>")
    def backup_unstage(token: str):
        try:
            discard(token)
        except BackupError as exc:
            return _error(exc)
        return jsonify({"status": "ok"})

    @app.post("/api/backup/jobs")
    def backup_start():
        body = request.get_json(silent=True) or {}
        try:
            job = start_job(str(body.get("token") or ""), str(body.get("passphrase") or ""))
        except BackupError as exc:
            return _error(exc)
        return jsonify(job), 202

    @app.get("/api/backup/jobs/latest")
    def backup_latest():
        return jsonify({"job": latest_job()})

    @app.delete("/api/backup/jobs/<job_id>")
    def backup_dismiss(job_id: str):
        if not dismiss_job(job_id):
            return _error(BackupError("unknown_job", 404))
        return jsonify({"status": "ok"})
