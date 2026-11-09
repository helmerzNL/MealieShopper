from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import secrets
import weakref
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiosqlite
import jwt
from cryptography.fernet import Fernet, InvalidToken
from quart import Quart, jsonify, make_response, request
from webauthn import verify_authentication_response, verify_registration_response
from webauthn.helpers.cose import COSEAlgorithmIdentifier

from .i18n import tr

OWNER_ID = "owner"  # de eerste gebruiker is de beheerder; alle anderen zijn gewone gebruikers
SESSION_COOKIE_NAME = "ms_session"
SESSION_MAX_AGE_SECONDS = 24 * 60 * 60
_INITIALIZED = False

# De schema's die dit proces al aanmaakte, per database-pad: elke module maakt haar tabellen, indexen en migraties een keer per
# proces, niet bij elke verbinding. `forget_runtime_state` wist het, zodat een nieuwe database na een fabrieksreset het schema
# weer krijgt. Twee coroutines die tegelijk de eerste zijn, draaien de opzet allebei: die is `IF NOT EXISTS` en de migraties
# controleren zelf nog eens achter een schrijfslot.
_SCHEMAS_READY: set[tuple[str, str]] = set()


def auth_enabled() -> bool:
    return os.environ.get("PASSKEY_AUTH_ENABLED", "true").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def data_dir() -> Path:
    return Path(os.environ.get("MEALIESHOPPER_DATA_DIR", "data")).resolve()


def db_path() -> Path:
    return data_dir() / "auth.sqlite3"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(value: str) -> bytes:
    value += "=" * ((4 - len(value) % 4) % 4)
    return base64.urlsafe_b64decode(value.encode("ascii"))


def make_challenge() -> bytes:
    return secrets.token_bytes(32)


_master_secrets: dict[str, str] = {}
SECRET_FILE = "secret.key"
SECRET_RETRY_SECONDS: float = 0.1  # zo lang wachten we tussen twee pogingen op een `secret.key` die een andere werker schrijft


async def master_secret() -> str:
    """Het geheim waaruit zowel de sessiesleutel als de sleutel voor opgeslagen gegevens volgt.

    Is MEALIESHOPPER_AUTH_SECRET (of JWT_SECRET) ingesteld, dan die: de sleutel staat dan niet bij de
    database op schijf. Anders maakt de app zelf een willekeurig geheim en bewaart dat eenmalig in de
    data-map (`secret.key`, alleen leesbaar voor de eigenaar). Dat bestand hoort bij een back-up van de
    data-map: zonder dat bestand zijn de opgeslagen koppelingen en sessies onleesbaar.
    """
    configured = os.environ.get("MEALIESHOPPER_AUTH_SECRET") or os.environ.get("JWT_SECRET")
    if configured and configured.strip():
        return configured.strip()
    path = data_dir() / SECRET_FILE
    cached = _master_secrets.get(str(path))
    if cached:
        return cached
    secret = await _read_secret_file(path)
    if not secret:
        data_dir().mkdir(parents=True, exist_ok=True)
        secret = secrets.token_hex(32)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:  # een andere werker was ons voor
            secret = await _read_secret_file(path, wait=True)
        else:
            with os.fdopen(descriptor, "w", encoding="ascii") as handle:
                handle.write(secret)
    if len(secret) < 32:
        raise RuntimeError(
            f"{path} is onleesbaar of onvolledig. Herstel het bestand uit een back-up; zonder dat bestand "
            "moet je Mealie en Albert Heijn opnieuw koppelen (verwijder het bestand dan en start opnieuw)."
        )
    _master_secrets[str(path)] = secret
    return secret


async def _read_secret_file(path: Path, wait: bool = False) -> str:
    """De inhoud van het geheim, of "". Een andere werker kan hem net aanmaken: wacht dan even op de inhoud, zonder de
    event loop vast te houden."""
    for _ in range(20 if wait else 1):
        try:
            text = path.read_text(encoding="ascii").strip()
        except FileNotFoundError:
            return ""
        if len(text) >= 32:
            return text
        await asyncio.sleep(SECRET_RETRY_SECONDS)
    return text


async def _derive(purpose: str) -> bytes:
    return hashlib.sha256(f"mealieshopper:{purpose}:{await master_secret()}".encode()).digest()


async def signing_secret() -> str:
    """Sleutel waarmee sessies worden ondertekend."""
    return (await _derive("sessions")).hex()


def rp_id() -> str:
    configured = os.environ.get("RP_ID", "").strip()
    if configured:
        return configured
    return request.host.split(":", 1)[0] if request else "localhost"


def rp_name() -> str:
    return os.environ.get("RP_NAME", "MealieShopper").strip() or "MealieShopper"


def request_origin() -> str:
    origin = (request.headers.get("Origin") or "").strip().rstrip("/")
    if origin:
        return origin
    scheme = request.headers.get("X-Forwarded-Proto") or request.scheme or "http"
    host = request.headers.get("X-Forwarded-Host") or request.host
    return f"{scheme}://{host}".rstrip("/")


def rp_origins() -> list[str]:
    configured = os.environ.get("RP_ORIGINS") or os.environ.get("RP_ORIGIN") or ""
    origins = [item.strip().rstrip("/") for item in configured.split(",") if item.strip()]
    return origins or [request_origin()]


def request_is_secure() -> bool:
    forwarded_proto = (request.headers.get("X-Forwarded-Proto") or "").split(",", 1)[0].strip().lower()
    if forwarded_proto:
        return forwarded_proto == "https"
    return request.is_secure or request_origin().startswith("https://")


@asynccontextmanager
async def connect() -> AsyncIterator[aiosqlite.Connection]:
    """Een kortlevende aiosqlite-verbinding, een per bewerking. Sluit bij het verlaten, maar commit niet.

    Wie schrijft, eindigt dus zelf met `await conn.commit()`. De rijen zijn `sqlite3.Row` (`aiosqlite.Row` is
    dezelfde klasse): `row["key"]` en `row[0]` werken allebei.
    """
    data_dir().mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(db_path()) as conn:
        conn.row_factory = aiosqlite.Row
        yield conn


async def ensure_schema(conn: aiosqlite.Connection, name: str, setup: str | Callable[[aiosqlite.Connection], Awaitable[None]]) -> None:
    """Draai `setup` (een SQL-script, of een coroutine met het schema en de migraties van `name`) een keer per proces en database.

    Voor de `_connect` van elke module: DDL per verzoek kost tijd (en op een server als Postgres neemt elke CREATE een slot), en
    het resultaat verandert nooit tot het proces of de database opnieuw begint. Mislukt de opzet, dan blijft hij open voor de
    volgende verbinding. Wat per verbinding moet (`PRAGMA foreign_keys`, `create_function`) hoort niet in `setup`."""
    key: tuple[str, str] = (str(db_path()), name)
    if key in _SCHEMAS_READY:
        return
    if isinstance(setup, str):
        await conn.executescript(setup)
    else:
        await setup(conn)
    _SCHEMAS_READY.add(key)


async def fetchone(conn: aiosqlite.Connection, sql: str, params: tuple[Any, ...] = ()) -> aiosqlite.Row | None:
    """De eerste rij van `sql` op `conn`, of None. Gedeeld door elke module met een eigen tabel."""
    async with conn.execute(sql, params) as cursor:
        return await cursor.fetchone()


# Het schema van auth.sqlite3, voor init_db. Alles met IF NOT EXISTS: veilig om opnieuw te draaien.
_SCHEMA: str = """
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  username TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS passkey_credentials (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  public_key BLOB NOT NULL,
  sign_count INTEGER NOT NULL DEFAULT 0,
  credential_name TEXT NOT NULL,
  created_at TEXT NOT NULL,
  last_used_at TEXT,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS auth_challenges (
  key TEXT PRIMARY KEY,
  challenge BLOB NOT NULL,
  expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS password_credentials (
  user_id TEXT PRIMARY KEY,
  password_hash TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS mfa_totp (
  user_id TEXT PRIMARY KEY,
  secret TEXT NOT NULL,
  confirmed INTEGER NOT NULL DEFAULT 0,
  last_counter INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS mfa_recovery_codes (
  code_hash TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS login_attempts (
  key TEXT PRIMARY KEY,
  failures INTEGER NOT NULL DEFAULT 0,
  window_start TEXT NOT NULL,
  locked_until TEXT
);

CREATE TABLE IF NOT EXISTS invites (
  id TEXT PRIMARY KEY,
  token_hash TEXT NOT NULL UNIQUE,
  user_id TEXT NOT NULL,
  username TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS passkey_credentials_user ON passkey_credentials (user_id);
CREATE INDEX IF NOT EXISTS mfa_recovery_codes_user ON mfa_recovery_codes (user_id);
"""




async def init_db() -> None:
    """Het schema en de migratie, met de vlag `_INITIALIZED`. Draait bij het starten (`before_serving`), anders een keer
    via `ensure_db`, nooit per verzoek."""
    global _INITIALIZED
    async with connect() as conn:
        await conn.executescript(_SCHEMA)
        await _migrate(conn)
    _INITIALIZED = True


async def _has_session_epoch(conn: aiosqlite.Connection) -> bool:
    rows = await conn.execute_fetchall("PRAGMA table_info(users)")
    return any(row["name"] == "session_epoch" for row in rows)


async def _migrate(conn: aiosqlite.Connection) -> None:
    """Elke gebruiker krijgt een eigen teller: een nieuw wachtwoord logt alleen die gebruiker overal uit.

    Twee werkers kunnen tegelijk de eerste zijn: de tweede controle staat daarom achter een schrijfslot (BEGIN
    IMMEDIATE). Wie als tweede komt, wacht op de eerste en ziet de kolom dan al staan."""
    if await _has_session_epoch(conn):
        return
    await conn.execute("BEGIN IMMEDIATE")
    try:
        if not await _has_session_epoch(conn):
            await conn.execute("ALTER TABLE users ADD COLUMN session_epoch INTEGER NOT NULL DEFAULT 0")
            row = await fetchone(conn, "SELECT value FROM app_settings WHERE key = ?", (SETTING_SESSION_EPOCH,))
            try:
                epoch = int(row["value"]) if row else 0
            except (TypeError, ValueError):
                epoch = 0
            await conn.execute("UPDATE users SET session_epoch = ?", (epoch,))
        await conn.commit()
    except BaseException:
        await conn.rollback()
        raise


# Een slot per event loop, zoals in http_client: elke test heeft een eigen loop.
_init_locks: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = weakref.WeakKeyDictionary()


def _init_lock() -> asyncio.Lock:
    loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
    lock: asyncio.Lock | None = _init_locks.get(loop)
    if lock is None:
        lock = _init_locks[loop] = asyncio.Lock()
    return lock


async def ensure_db() -> None:
    """`init_db` als dat in dit proces nog niet gebeurde. Gelijktijdige eerste verzoeken wachten op elkaar, zodat de
    migratie een keer loopt."""
    if _INITIALIZED:
        return
    async with _init_lock():
        if not _INITIALIZED:
            await init_db()


async def _count(table: str, user_id: str | None, extra: str = "") -> int:
    """Aantal rijen in `table`, voor één gebruiker of (zonder user_id) voor iedereen."""
    await ensure_db()
    where = [clause for clause in (extra, "user_id = ?" if user_id else "") if clause]
    sql = f"SELECT COUNT(*) AS count FROM {table}" + (" WHERE " + " AND ".join(where) if where else "")
    async with connect() as conn:
        row = await fetchone(conn, sql, (user_id,) if user_id else ())
    return int(row["count"] if row else 0)


async def passkey_count(user_id: str | None = None) -> int:
    return await _count("passkey_credentials", user_id)


async def password_configured(user_id: str | None = None) -> bool:
    return await _count("password_credentials", user_id) > 0


async def mfa_enabled(user_id: str | None = None) -> bool:
    return await _count("mfa_totp", user_id, "confirmed = 1") > 0


async def recovery_codes_left(user_id: str | None = None) -> int:
    return await _count("mfa_recovery_codes", user_id)


async def credentials_exist() -> bool:
    """Is er al een manier om in te loggen (passkey of wachtwoord)?"""
    return await passkey_count() > 0 or await password_configured()


# Instellingen zonder geheim (keuzes, vlaggen): onversleuteld in app_settings.
async def get_setting(key: str, default: str = "") -> str:
    await ensure_db()
    async with connect() as conn:
        row = await fetchone(conn, "SELECT value FROM app_settings WHERE key=?", (key,))
    return str(row["value"]) if row else default


_UPSERT_SETTING_SQL: str = """
    INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)
    ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
"""


async def set_setting(key: str, value: str) -> None:
    await ensure_db()
    async with connect() as conn:
        await conn.execute(_UPSERT_SETTING_SQL, (key, value, utcnow().isoformat()))
        await conn.commit()


SETTING_AUTH_MODE = "AUTH_MODE"  # "none" = bewust zonder inloggen; verder afgeleid uit de aanwezige gegevens
SETTING_SESSION_EPOCH = "SESSION_EPOCH"


async def auth_mode() -> str:
    return await get_setting(SETTING_AUTH_MODE)


async def auth_required() -> bool:
    """Moet je inloggen? Niet als de beheerder inloggen uitzette of er bewust voor koos het over te slaan."""
    if not auth_enabled():
        return False
    if await credentials_exist():
        return True
    return await auth_mode() != "none"


async def session_epoch(user_id: str = OWNER_ID) -> int:
    await ensure_db()
    async with connect() as conn:
        row = await fetchone(conn, "SELECT session_epoch FROM users WHERE id = ?", (user_id,))
    return int(row["session_epoch"]) if row else 0


async def bump_session_epoch(user_id: str = OWNER_ID) -> int:
    """Maak alle bestaande sessies van een gebruiker ongeldig (bij een nieuw wachtwoord of uitgezette MFA)."""
    await ensure_db()
    async with connect() as conn:
        await conn.execute("UPDATE users SET session_epoch = session_epoch + 1 WHERE id = ?", (user_id,))
        await conn.commit()
    return await session_epoch(user_id)


async def credential_rows(user_id: str = OWNER_ID) -> list[dict[str, Any]]:
    await ensure_db()
    async with connect() as conn:
        rows = await conn.execute_fetchall(
            """
            SELECT
              c.id,
              c.credential_name,
              c.created_at,
              c.last_used_at,
              c.sign_count,
              u.username
            FROM passkey_credentials c
            JOIN users u ON u.id = c.user_id
            WHERE c.user_id = ?
            ORDER BY c.created_at DESC
            """,
            (user_id,),
        )
    return [dict(row) for row in rows]


async def secret_box() -> Fernet:
    """Versleuteling voor opgeslagen koppelingen (Mealie-token, AH-token) en het MFA-geheim."""
    return Fernet(base64.urlsafe_b64encode(await _derive("stored-secrets")))


async def set_secret(key: str, value: str) -> None:
    await ensure_db()
    encrypted = (await secret_box()).encrypt(value.encode("utf-8")).decode("ascii")
    async with connect() as conn:
        await conn.execute(_UPSERT_SETTING_SQL, (key, encrypted, utcnow().isoformat()))
        await conn.commit()


# Inloggegevens en cookies van verwijderde functies (AH website-koppeling, Jumbo).
OBSOLETE_SECRET_KEYS = (
    "AH_USERNAME",
    "AH_PASSWORD",
    "AH_COOKIES",
    "JUMBO_USERNAME",
    "JUMBO_PASSWORD",
    "JUMBO_COOKIES",
)


async def purge_obsolete_secrets() -> int:
    """Verwijder opgeslagen gegevens van functies die niet meer bestaan; geeft het aantal rijen."""
    await init_db()  # idempotent (CREATE TABLE IF NOT EXISTS), werkt ook voor een nieuwe data-map
    placeholders = ",".join("?" for _ in OBSOLETE_SECRET_KEYS)
    async with connect() as conn:
        cursor = await conn.execute(f"DELETE FROM app_settings WHERE key IN ({placeholders})", OBSOLETE_SECRET_KEYS)
        await conn.commit()
        return cursor.rowcount


async def delete_secret(key: str) -> None:
    await ensure_db()
    async with connect() as conn:
        await conn.execute("DELETE FROM app_settings WHERE key=?", (key,))
        await conn.commit()


async def get_secret(key: str) -> str:
    await ensure_db()
    async with connect() as conn:
        row = await fetchone(conn, "SELECT value FROM app_settings WHERE key=?", (key,))
    if not row:
        return ""
    value = str(row["value"] or "")
    try:
        return (await secret_box()).decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        # Een versleutelde waarde met een andere sleutel is onleesbaar (opnieuw koppelen), geen tekst om terug te geven.
        return "" if value.startswith("gAAAA") else value


async def create_token(user_id: str, username: str) -> str:
    return jwt.encode(
        {
            "sub": user_id,
            "usr": username,
            "ep": await session_epoch(user_id),
            "iat": utcnow(),
            "exp": utcnow() + timedelta(seconds=SESSION_MAX_AGE_SECONDS),
        },
        await signing_secret(),
        algorithm="HS256",
    )


async def verify_token(token: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(token, await signing_secret(), algorithms=["HS256"])
    except Exception:
        return None


async def current_user() -> dict[str, Any] | None:
    await ensure_db()
    token = str(request.cookies.get(SESSION_COOKIE_NAME) or "").strip()
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
    payload = await verify_token(token) if token else None
    if not payload:
        return None
    if payload.get("purpose"):
        return None  # een kaartje voor een tussenstap (bijvoorbeeld MFA) is geen sessie
    async with connect() as conn:
        row = await fetchone(conn, "SELECT id, username, created_at, session_epoch FROM users WHERE id=?", (payload.get("sub"),))
    if not row or int(payload.get("ep") or 0) != int(row["session_epoch"]):
        return None
    user = {"id": row["id"], "username": row["username"], "created_at": row["created_at"]}
    return {**user, "role": "owner" if user["id"] == OWNER_ID else "user"}


async def acting_user_id() -> str:
    """Wie de aanvraag doet: de ingelogde gebruiker, of de eigenaar zolang er nog niemand is ingelogd (eerste start)."""
    user = await current_user()
    return user["id"] if user else OWNER_ID


async def is_owner() -> bool:
    """De eigenaar (beheerder) mag koppelen en gebruikers beheren. Zonder inlog is iedereen de beheerder."""
    if not await auth_required():
        return True
    user = await current_user()
    return bool(user and user["id"] == OWNER_ID)


async def store_challenge(key: str, challenge: bytes) -> None:
    await ensure_db()
    expires_at = (utcnow() + timedelta(minutes=5)).isoformat()
    async with connect() as conn:
        await conn.execute("DELETE FROM auth_challenges WHERE expires_at < ?", (utcnow().isoformat(),))
        await conn.execute(
            """
            INSERT INTO auth_challenges (key, challenge, expires_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET challenge=excluded.challenge, expires_at=excluded.expires_at
            """,
            (key, challenge, expires_at),
        )
        await conn.commit()


async def pop_challenge(key: str) -> bytes | None:
    """De challenge onder `key`, een keer. Lezen en wissen zijn een opdracht (`DELETE ... RETURNING`), dus twee verzoeken
    tegelijk krijgen nooit allebei dezelfde challenge. Een verlopen challenge geeft None en verdwijnt ook."""
    await ensure_db()
    now = utcnow().isoformat()
    async with connect() as conn:
        row = await fetchone(conn, "DELETE FROM auth_challenges WHERE key=? RETURNING challenge, expires_at", (key,))
        await conn.execute("DELETE FROM auth_challenges WHERE expires_at < ?", (now,))
        await conn.commit()
    return bytes(row["challenge"]) if row and str(row["expires_at"]) >= now else None


def _verify_registration(credential: dict[str, Any], challenge: bytes, rp: str, origins: list[str]) -> tuple[bytes, bytes, int]:
    result = verify_registration_response(
        credential=credential,
        expected_challenge=challenge,
        expected_rp_id=rp,
        expected_origin=origins,
        require_user_verification=False,
        supported_pub_key_algs=[COSEAlgorithmIdentifier.ECDSA_SHA_256],
    )
    return result.credential_id, result.credential_public_key, result.sign_count


async def check_registration(credential: dict[str, Any], challenge: bytes) -> tuple[bytes, bytes, int]:
    """Controleert een nieuwe passkey met py_webauthn. Geeft (credential-id, COSE-sleutel, teller) of gooit een fout.

    Alleen ES256 (dat is wat de opties aanbieden). Aanwezigheid is verplicht, verificatie niet: een wachtwoordmanager
    als Enpass ontgrendelt met zijn eigen hoofdwachtwoord en meldt de verificatievlag niet. De controle loopt in een
    thread, net als bij `check_assertion`.
    """
    return await asyncio.to_thread(_verify_registration, credential, challenge, rp_id(), rp_origins())


def _verify_assertion(credential: dict[str, Any], challenge: bytes, public_key: bytes, rp: str, origins: list[str]) -> int:
    result = verify_authentication_response(
        credential=credential,
        expected_challenge=challenge,
        expected_rp_id=rp,
        expected_origin=origins,
        credential_public_key=public_key,
        credential_current_sign_count=0,
        require_user_verification=False,
    )
    return result.new_sign_count


async def check_assertion(credential: dict[str, Any], challenge: bytes, public_key: bytes) -> int:
    """Controleert de ondertekening van een passkey-aanmelding met py_webauthn. Geeft de nieuwe teller of gooit een fout.

    Het nakijken van de handtekening loopt in een thread, buiten de event loop. De verwachte host en origins komen uit
    het verzoek, die lezen we vooraf. De tellercontrole staat uit (huidige teller 0): synced passkeys melden 0 en deze
    app bewaarde de teller nooit om op te weigeren.
    """
    return await asyncio.to_thread(_verify_assertion, credential, challenge, public_key, rp_id(), rp_origins())


def forget_runtime_state() -> None:
    """Vergeet wat er in het geheugen staat over de database en het geheim (na het wissen van de data-map)."""
    global _INITIALIZED
    _INITIALIZED = False
    _SCHEMAS_READY.clear()
    _master_secrets.clear()


async def cookie_response(payload: dict[str, Any], token: str | None = None, status: int = 200):
    result = await make_response(jsonify(payload), status)
    if token:
        result.set_cookie(
            SESSION_COOKIE_NAME,
            token,
            max_age=SESSION_MAX_AGE_SECONDS,
            httponly=True,
            secure=request_is_secure(),
            samesite="Lax",
            path="/",
        )
    return result


async def clear_cookie_response(payload: dict[str, Any]):
    result = await make_response(jsonify(payload))
    result.delete_cookie(SESSION_COOKIE_NAME, path="/", secure=request_is_secure(), samesite="Lax")
    return result


def register_routes(app: Quart) -> None:
    @app.get("/.well-known/webauthn")
    async def webauthn_well_known():
        return jsonify({"origins": rp_origins()})

    @app.get("/api/auth/status")
    async def auth_status():
        user = await current_user()
        configured = await credentials_exist()
        required = await auth_required()
        payload = {
            "enabled": required,
            "envEnabled": auth_enabled(),
            "configured": configured,
            "credentialCount": await passkey_count(),
            "methods": {"passkey": await passkey_count() > 0, "password": await password_configured()},
            "setupRequired": required and not configured,
            "authenticated": bool(user),
            "username": user["username"] if user else None,
            "userId": user["id"] if user else None,
            "role": user["role"] if user else ("owner" if not required else None),
            "isOwner": await is_owner(),
            "rpId": rp_id(),
            "rpOrigins": rp_origins(),
        }
        if user or not required:
            uid = user["id"] if user else OWNER_ID
            payload["credentialCount"] = await passkey_count(uid)
            payload["passwordConfigured"] = await password_configured(uid)
            payload["mfaEnabled"] = await mfa_enabled(uid)
            payload["recoveryCodesLeft"] = await recovery_codes_left(uid)
            from . import profile  # late import: profile gebruikt dit module

            payload.update(await profile.get(uid))
        return jsonify(payload)

    @app.get("/api/auth/credentials")
    async def credentials_list():
        if await auth_required() and await credentials_exist() and not await current_user():
            return jsonify({"error": "Unauthorized"}), 401
        return jsonify({"credentials": await credential_rows(await acting_user_id())})

    @app.delete("/api/auth/credentials/<credential_id>")
    async def credentials_delete(credential_id: str):
        if await auth_required() and not await current_user():
            return jsonify({"error": "Unauthorized"}), 401
        uid = await acting_user_id()  # alleen je eigen passkeys
        last_allowed = not await auth_required() or await password_configured(uid)  # buiten de verbinding hieronder
        async with connect() as conn:
            row = await fetchone(conn, "SELECT id FROM passkey_credentials WHERE id=? AND user_id=?", (credential_id, uid))
            if not row:
                return jsonify({"error": "Not found"}), 404
            count = await fetchone(conn, "SELECT COUNT(*) AS count FROM passkey_credentials WHERE user_id=?", (uid,))
            if int(count["count"] if count else 0) <= 1 and not last_allowed:
                return jsonify({"error": tr("Je kunt de laatste passkey niet verwijderen.", "You cannot delete the last passkey.")}), 400
            await conn.execute("DELETE FROM passkey_credentials WHERE id=?", (credential_id,))
            await conn.commit()
        return jsonify({"status": "deleted", "remaining": await passkey_count(uid)})

    @app.post("/api/auth/register/options")
    async def register_options():
        if not auth_enabled():
            return jsonify({"error": "Passkey auth is disabled"}), 400
        body = await request.get_json(silent=True) or {}
        from . import users  # lokaal: users importeert dit module

        invite = None
        if body.get("invite"):
            invite = await users.find_invite(body.get("invite"))
            if not invite:
                return jsonify({"error": tr("Deze uitnodiging bestaat niet meer of is verlopen.", "This invitation no longer exists or has expired.")}), 404
        elif await credentials_exist() and await auth_required() and not await current_user():
            return jsonify({"error": "Login required to add another passkey"}), 401

        credential_name = str(body.get("credentialName") or body.get("credential_name") or "Owner passkey").strip()
        if invite:
            user_id = invite["userId"]
            username = invite["username"] if invite["kind"] == "reset" else str(body.get("username") or invite["username"]).strip()
            problem = None if invite["kind"] == "reset" else await users.check_new_username(username, invite)
            if problem:
                return jsonify({"error": problem}), 400
        else:
            user_id = await acting_user_id()
            current = await current_user()
            # Alleen de eigenaar kan zijn naam wijzigen; een gewone gebruiker houdt zijn eigen naam.
            username = (str(body.get("username") or "admin").strip() or "admin") if user_id == OWNER_ID or not current else current["username"]
        challenge = make_challenge()
        await store_challenge(f"register:{user_id}", challenge)
        async with connect() as conn:
            rows = await conn.execute_fetchall("SELECT id FROM passkey_credentials WHERE user_id=? ORDER BY created_at", (user_id,))

        options = {
            "rp": {"name": rp_name(), "id": rp_id()},
            "user": {"id": b64url_encode(user_id.encode("utf-8")), "name": username, "displayName": username},
            "challenge": b64url_encode(challenge),
            "pubKeyCredParams": [{"type": "public-key", "alg": -7}],
            "timeout": 60000,
            "authenticatorSelection": {"residentKey": "preferred", "userVerification": "preferred"},
            "excludeCredentials": [{"type": "public-key", "id": row["id"]} for row in rows],
            "attestation": "none",
        }
        return jsonify({"status": "ok", "userId": user_id, "username": username, "credentialName": credential_name, "options": options})

    @app.post("/api/auth/register/verify")
    async def register_verify():
        await ensure_db()
        if not auth_enabled():
            return jsonify({"error": "Passkey auth is disabled"}), 400
        body = await request.get_json(silent=True) or {}
        from . import users  # lokaal: users importeert dit module

        invite = None
        if body.get("invite"):
            invite = await users.find_invite(body.get("invite"))
            if not invite:
                return jsonify({"error": tr("Deze uitnodiging bestaat niet meer of is verlopen.", "This invitation no longer exists or has expired.")}), 404
            user_id = invite["userId"]
            username = invite["username"] if invite["kind"] == "reset" else str(body.get("username") or invite["username"]).strip()
        else:
            if await credentials_exist() and await auth_required() and not await current_user():
                return jsonify({"error": "Login required to add another passkey"}), 401
            user_id = await acting_user_id()  # een passkey kun je alleen voor jezelf toevoegen
            current = await current_user()
            username = (str(body.get("username") or "admin").strip() or "admin") if user_id == OWNER_ID or not current else current["username"]
        credential_name = str(body.get("credentialName") or body.get("credential_name") or "Owner passkey").strip()
        credential = body.get("credential") or {}
        challenge = await pop_challenge(f"register:{user_id}")
        if not challenge:
            return jsonify({"error": "No pending challenge"}), 400

        try:
            credential_id, cose_key_bytes, sign_count = await check_registration(credential, challenge)
            credential_id_b64 = b64url_encode(credential_id)
        except Exception as exc:
            return jsonify({"error": f"Verification failed: {exc}"}), 400

        from . import onboarding  # lokaal: onboarding importeert dit module

        if invite:
            if invite["kind"] == "new" and (taken := await users.check_new_username(username, invite)):
                return jsonify({"error": taken}), 409
            try:
                await users.register_account(invite, username)  # nieuw account, of de oude inloggegevens wissen bij een reset
            except users.UsernameTaken:
                return jsonify({"error": tr("Deze naam is al in gebruik. Kies een andere.", "This name is already taken. Choose another one.")}), 409
        elif user_id == OWNER_ID:
            await onboarding.begin()
        now = utcnow().isoformat()
        async with connect() as conn:
            await conn.execute(
                """
                INSERT INTO users (id, username, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET username=excluded.username
                """,
                (user_id, username, now),
            )
            await conn.execute(
                """
                INSERT INTO passkey_credentials (id, user_id, public_key, sign_count, credential_name, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  public_key=excluded.public_key,
                  sign_count=excluded.sign_count,
                  credential_name=excluded.credential_name
                """,
                (credential_id_b64, user_id, cose_key_bytes, sign_count, credential_name, now),
            )
            await conn.commit()
        if invite:
            await users.consume_invite(invite["id"])
        elif user_id == OWNER_ID:
            await set_setting(SETTING_AUTH_MODE, "passkey")
        token = await create_token(user_id, username)
        return await cookie_response({"status": "ok", "username": username}, token)

    @app.post("/api/auth/login/options")
    async def login_options():
        await ensure_db()
        if not auth_enabled():
            return jsonify({"error": "Passkey auth is disabled"}), 400
        async with connect() as conn:
            rows = await conn.execute_fetchall("SELECT id FROM passkey_credentials ORDER BY created_at")
        if not rows:
            return jsonify({"error": "No passkey registered yet"}), 400
        challenge = make_challenge()
        await store_challenge("login", challenge)
        return jsonify(
            {
                "status": "ok",
                "options": {
                    "challenge": b64url_encode(challenge),
                    "timeout": 60000,
                    "rpId": rp_id(),
                    "allowCredentials": [{"type": "public-key", "id": row["id"]} for row in rows],
                    "userVerification": "preferred",
                },
            }
        )

    @app.post("/api/auth/login/verify")
    async def login_verify():
        if not auth_enabled():
            return jsonify({"error": "Passkey auth is disabled"}), 400
        body = await request.get_json(silent=True) or {}
        credential = body.get("credential") or {}
        credential_id = str(credential.get("id") or "")
        challenge = await pop_challenge("login")
        if not credential_id:
            return jsonify({"error": "Credential id is required"}), 400
        if not challenge:
            return jsonify({"error": "No pending challenge"}), 400

        async with connect() as conn:
            stored = await fetchone(
                conn,
                """
                SELECT c.*, u.username
                FROM passkey_credentials c
                JOIN users u ON u.id = c.user_id
                WHERE c.id=?
                """,
                (credential_id,),
            )
        if not stored:
            return jsonify({"error": "Unknown credential"}), 400

        try:
            new_sign_count = await check_assertion(credential, challenge, bytes(stored["public_key"]))
        except Exception as exc:
            return jsonify({"error": f"Verification failed: {exc}"}), 400

        async with connect() as conn:
            await conn.execute(
                "UPDATE passkey_credentials SET sign_count=?, last_used_at=? WHERE id=?",
                (new_sign_count, utcnow().isoformat(), credential_id),
            )
            await conn.commit()
        token = await create_token(str(stored["user_id"]), str(stored["username"]))
        return await cookie_response({"status": "ok", "username": stored["username"]}, token)

    @app.post("/api/auth/logout")
    async def logout():
        return await clear_cookie_response({"status": "ok"})


# Alleen de beheerder: Mealie en Albert Heijn koppelen, gebruikers beheren en de serverinstellingen. Gewone gebruikers
# zien de status ervan wel (/api/mealie/status, /api/ah/auth/status), maar kunnen niets wijzigen.
ADMIN_PREFIXES = ("/api/users", "/api/server-settings", "/api/audit", "/api/update", "/api/system", "/api/backup", "/api/price-archive", "/api/mealie/auth", "/api/prices/import", "/api/ah/auth/", "/api/picnic/auth/", "/api/jumbo/auth/", "/api/crisp/auth/")
ADMIN_PATHS = {"/api/mealie/bulk-import/group", "/api/mealie/nutrition/backfill", "/api/offers/sync-all", "/api/offers/ai", "/api/offers/ai/test", "/api/offers/ai/review", "/api/offers/ai/search", "/api/offers/ai/search/plan", "/api/offers/ai/search/status", "/api/ah/auth", "/api/ah/explore", "/api/picnic/auth", "/api/picnic/explore", "/api/jumbo/auth", "/api/jumbo/explore", "/api/crisp/auth", "/api/crisp/explore"}
ADMIN_EXCEPTIONS = {"/api/ah/auth/status", "/api/picnic/auth/status", "/api/jumbo/auth/status", "/api/crisp/auth/status"}


def admin_only(path: str) -> bool:
    if path in ADMIN_EXCEPTIONS:
        return False
    return path in ADMIN_PATHS or any(path == prefix or path.startswith(prefix + "/") or (prefix.endswith("/") and path.startswith(prefix)) for prefix in ADMIN_PREFIXES)


# De adressen van de pagina's (zie static/router.js). Ze geven dezelfde pagina als "/", met het inlogscherm als je niet bent
# ingelogd, dus ze zijn net zo openbaar als "/". De gegevens zelf komen via /api en daar geldt de gewone controle.
PAGE_ROOTS = ("recipes", "offers", "import", "stores", "meal-kits", "cart", "profile", "settings", "about")


def is_page_path(path: str) -> bool:
    return any(path == f"/{root}" or path.startswith(f"/{root}/") for root in PAGE_ROOTS)


# Zonder inlog bereikbaar. De MCP-ingang en OAuth controleren zelf hun toegangstoken (of, voor /oauth/authorize, de
# sessie).
_PUBLIC_PREFIXES: tuple[str, ...] = ("/static/", "/api/auth/", "/api/invites/", "/oauth/", "/.well-known/oauth-")
_PUBLIC_PATHS: frozenset[str] = frozenset(
    {
        "/",
        "/healthz",
        "/.well-known/webauthn",
        "/favicon.ico",
        "/mcp",
        # De onboarding moet ook voor de eerste inlog bereikbaar zijn.
        "/api/onboarding/status",
        "/api/onboarding/start",
    }
)


def _is_public_path(path: str) -> bool:
    return path in _PUBLIC_PATHS or is_page_path(path) or any(path.startswith(prefix) for prefix in _PUBLIC_PREFIXES)


async def require_auth_for_request() -> tuple[dict[str, Any], int] | None:
    """De before_request-hook van app.py: None als het verzoek door mag, anders de fout en de status."""
    if not await auth_required():
        return None
    path = request.path
    if _is_public_path(path):
        return None
    user = await current_user()
    if user:
        if admin_only(path) and user["id"] != OWNER_ID:
            return {"error": tr("Dit kan alleen de beheerder.", "Only the administrator can do this.")}, 403
        return None
    if not await credentials_exist():
        return {"error": "Setup required"}, 401
    return {"error": "Unauthorized"}, 401
