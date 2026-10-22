from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import struct
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import cbor2
import jwt
from cryptography.hazmat.primitives.asymmetric.ec import (
    ECDSA,
    SECP256R1,
    EllipticCurvePublicKey,
    EllipticCurvePublicNumbers,
)
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.hashes import SHA256
from flask import Flask, jsonify, make_response, request

from .i18n import tr

OWNER_ID = "owner"  # de eerste gebruiker is de beheerder; alle anderen zijn gewone gebruikers
SESSION_COOKIE_NAME = "ms_session"
SESSION_MAX_AGE_SECONDS = 24 * 60 * 60
_INITIALIZED = False


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


def master_secret() -> str:
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
    secret = _read_secret_file(path)
    if not secret:
        data_dir().mkdir(parents=True, exist_ok=True)
        secret = secrets.token_hex(32)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:  # een andere werker was ons voor
            secret = _read_secret_file(path, wait=True)
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


def _read_secret_file(path: Path, wait: bool = False) -> str:
    """De inhoud van het geheim, of "". Een andere werker kan hem net aanmaken: wacht dan even op de inhoud."""
    for _ in range(20 if wait else 1):
        try:
            text = path.read_text(encoding="ascii").strip()
        except FileNotFoundError:
            return ""
        if len(text) >= 32:
            return text
        time.sleep(0.1)
    return text


def _derive(purpose: str) -> bytes:
    return hashlib.sha256(f"mealieshopper:{purpose}:{master_secret()}".encode("utf-8")).digest()


def signing_secret() -> str:
    """Sleutel waarmee sessies worden ondertekend."""
    return _derive("sessions").hex()


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


def connect() -> sqlite3.Connection:
    data_dir().mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    global _INITIALIZED
    with connect() as conn:
        conn.executescript(
            """
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
            """
        )
        _migrate(conn)
    _INITIALIZED = True


def _migrate(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
    if "session_epoch" not in columns:
        # Elke gebruiker heeft een eigen teller: een nieuw wachtwoord logt alleen die gebruiker overal uit.
        conn.execute("ALTER TABLE users ADD COLUMN session_epoch INTEGER NOT NULL DEFAULT 0")
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (SETTING_SESSION_EPOCH,)).fetchone()
        try:
            epoch = int(row["value"]) if row else 0
        except (TypeError, ValueError):
            epoch = 0
        conn.execute("UPDATE users SET session_epoch = ?", (epoch,))


def ensure_db() -> None:
    if not _INITIALIZED:
        init_db()


def _count(table: str, user_id: str | None, extra: str = "") -> int:
    """Aantal rijen in `table`, voor één gebruiker of (zonder user_id) voor iedereen."""
    ensure_db()
    where = [clause for clause in (extra, "user_id = ?" if user_id else "") if clause]
    sql = f"SELECT COUNT(*) AS count FROM {table}" + (" WHERE " + " AND ".join(where) if where else "")
    with closing(connect()) as conn:
        row = conn.execute(sql, (user_id,) if user_id else ()).fetchone()
    return int(row["count"] if row else 0)


def passkey_count(user_id: str | None = None) -> int:
    return _count("passkey_credentials", user_id)


def password_configured(user_id: str | None = None) -> bool:
    return _count("password_credentials", user_id) > 0


def mfa_enabled(user_id: str | None = None) -> bool:
    return _count("mfa_totp", user_id, "confirmed = 1") > 0


def recovery_codes_left(user_id: str | None = None) -> int:
    return _count("mfa_recovery_codes", user_id)


def credentials_exist() -> bool:
    """Is er al een manier om in te loggen (passkey of wachtwoord)?"""
    return passkey_count() > 0 or password_configured()


# Instellingen zonder geheim (keuzes, vlaggen): onversleuteld in app_settings.
def get_setting(key: str, default: str = "") -> str:
    ensure_db()
    with closing(connect()) as conn:
        row = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    return str(row["value"]) if row else default


def set_setting(key: str, value: str) -> None:
    ensure_db()
    with closing(connect()) as conn, conn:
        conn.execute(
            """
            INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
            """,
            (key, value, utcnow().isoformat()),
        )


SETTING_AUTH_MODE = "AUTH_MODE"  # "none" = bewust zonder inloggen; verder afgeleid uit de aanwezige gegevens
SETTING_SESSION_EPOCH = "SESSION_EPOCH"


def auth_mode() -> str:
    return get_setting(SETTING_AUTH_MODE)


def auth_required() -> bool:
    """Moet je inloggen? Niet als de beheerder inloggen uitzette of er bewust voor koos het over te slaan."""
    if not auth_enabled():
        return False
    if credentials_exist():
        return True
    return auth_mode() != "none"


def session_epoch(user_id: str = OWNER_ID) -> int:
    ensure_db()
    with closing(connect()) as conn:
        row = conn.execute("SELECT session_epoch FROM users WHERE id = ?", (user_id,)).fetchone()
    return int(row["session_epoch"]) if row else 0


def bump_session_epoch(user_id: str = OWNER_ID) -> int:
    """Maak alle bestaande sessies van een gebruiker ongeldig (bij een nieuw wachtwoord of uitgezette MFA)."""
    ensure_db()
    with closing(connect()) as conn, conn:
        conn.execute("UPDATE users SET session_epoch = session_epoch + 1 WHERE id = ?", (user_id,))
    return session_epoch(user_id)


def credential_rows(user_id: str = OWNER_ID) -> list[dict[str, Any]]:
    ensure_db()
    with closing(connect()) as conn:
        rows = conn.execute(
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
        ).fetchall()
    return [dict(row) for row in rows]


def secret_box() -> Fernet:
    """Versleuteling voor opgeslagen koppelingen (Mealie-token, AH-token) en het MFA-geheim."""
    return Fernet(base64.urlsafe_b64encode(_derive("stored-secrets")))


def set_secret(key: str, value: str) -> None:
    ensure_db()
    encrypted = secret_box().encrypt(value.encode("utf-8")).decode("ascii")
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO app_settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
            """,
            (key, encrypted, utcnow().isoformat()),
        )


# Inloggegevens en cookies van verwijderde functies (AH website-koppeling, Jumbo).
OBSOLETE_SECRET_KEYS = (
    "AH_USERNAME",
    "AH_PASSWORD",
    "AH_COOKIES",
    "JUMBO_USERNAME",
    "JUMBO_PASSWORD",
    "JUMBO_COOKIES",
)


def purge_obsolete_secrets() -> int:
    """Verwijder opgeslagen gegevens van functies die niet meer bestaan; geeft het aantal rijen."""
    init_db()  # idempotent (CREATE TABLE IF NOT EXISTS); werkt ook voor een nieuwe data-map
    placeholders = ",".join("?" for _ in OBSOLETE_SECRET_KEYS)
    with closing(connect()) as conn, conn:
        cursor = conn.execute(
            f"DELETE FROM app_settings WHERE key IN ({placeholders})", OBSOLETE_SECRET_KEYS
        )
        return cursor.rowcount


def delete_secret(key: str) -> None:
    ensure_db()
    with closing(connect()) as conn, conn:
        conn.execute("DELETE FROM app_settings WHERE key=?", (key,))


def get_secret(key: str) -> str:
    ensure_db()
    with connect() as conn:
        row = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    if not row:
        return ""
    value = str(row["value"] or "")
    try:
        return secret_box().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        # Een versleutelde waarde met een andere sleutel is onleesbaar (opnieuw koppelen), geen tekst om terug te geven.
        return "" if value.startswith("gAAAA") else value


def create_token(user_id: str, username: str) -> str:
    return jwt.encode(
        {
            "sub": user_id,
            "usr": username,
            "ep": session_epoch(user_id),
            "iat": utcnow(),
            "exp": utcnow() + timedelta(seconds=SESSION_MAX_AGE_SECONDS),
        },
        signing_secret(),
        algorithm="HS256",
    )


def verify_token(token: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(token, signing_secret(), algorithms=["HS256"])
    except Exception:
        return None


def current_user() -> dict[str, Any] | None:
    ensure_db()
    token = str(request.cookies.get(SESSION_COOKIE_NAME) or "").strip()
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
    payload = verify_token(token) if token else None
    if not payload:
        return None
    if payload.get("purpose"):
        return None  # een kaartje voor een tussenstap (bijvoorbeeld MFA) is geen sessie
    with closing(connect()) as conn:
        row = conn.execute(
            "SELECT id, username, created_at, session_epoch FROM users WHERE id=?",
            (payload.get("sub"),),
        ).fetchone()
    if not row or int(payload.get("ep") or 0) != int(row["session_epoch"]):
        return None
    user = {"id": row["id"], "username": row["username"], "created_at": row["created_at"]}
    return {**user, "role": "owner" if user["id"] == OWNER_ID else "user"}


def acting_user_id() -> str:
    """Wie de aanvraag doet: de ingelogde gebruiker, of de eigenaar zolang er nog niemand is ingelogd (eerste start)."""
    user = current_user()
    return user["id"] if user else OWNER_ID


def is_owner() -> bool:
    """De eigenaar (beheerder) mag koppelen en gebruikers beheren. Zonder inlog is iedereen de beheerder."""
    if not auth_required():
        return True
    user = current_user()
    return bool(user and user["id"] == OWNER_ID)


def store_challenge(key: str, challenge: bytes) -> None:
    ensure_db()
    expires_at = (utcnow() + timedelta(minutes=5)).isoformat()
    with connect() as conn:
        conn.execute("DELETE FROM auth_challenges WHERE expires_at < ?", (utcnow().isoformat(),))
        conn.execute(
            """
            INSERT INTO auth_challenges (key, challenge, expires_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET challenge=excluded.challenge, expires_at=excluded.expires_at
            """,
            (key, challenge, expires_at),
        )


def pop_challenge(key: str) -> bytes | None:
    ensure_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT challenge FROM auth_challenges WHERE key=? AND expires_at >= ?",
            (key, utcnow().isoformat()),
        ).fetchone()
        conn.execute("DELETE FROM auth_challenges WHERE key=? OR expires_at < ?", (key, utcnow().isoformat()))
    return bytes(row["challenge"]) if row else None


def parse_cose_key(cose_map: dict[Any, Any]) -> EllipticCurvePublicKey:
    numbers = EllipticCurvePublicNumbers(
        x=int.from_bytes(cose_map[-2], "big"),
        y=int.from_bytes(cose_map[-3], "big"),
        curve=SECP256R1(),
    )
    return numbers.public_key()


def parse_attestation_object(attestation_object_b64: str) -> tuple[bytes, bytes, bytes, int]:
    attestation = cbor2.loads(b64url_decode(attestation_object_b64))
    auth_data = attestation["authData"]
    sign_count = struct.unpack(">I", auth_data[33:37])[0]
    credential_id_len = struct.unpack(">H", auth_data[53:55])[0]
    credential_id = auth_data[55 : 55 + credential_id_len]
    cose_key_bytes = auth_data[55 + credential_id_len :]
    cbor2.loads(cose_key_bytes)
    return credential_id, cose_key_bytes, auth_data, sign_count


def parse_auth_data(auth_data: bytes) -> tuple[bytes, int, int]:
    return auth_data[:32], auth_data[32], struct.unpack(">I", auth_data[33:37])[0]


def verify_signature(public_key_bytes: bytes, auth_data: bytes, client_data_hash: bytes, signature: bytes) -> None:
    public_key = parse_cose_key(cbor2.loads(public_key_bytes))
    public_key.verify(signature, auth_data + client_data_hash, ECDSA(SHA256()))


def validate_client_data(
    *,
    client_data_json_b64: str,
    expected_type: str,
    expected_challenge: bytes,
) -> bytes:
    client_data_raw = b64url_decode(client_data_json_b64)
    client_data = json.loads(client_data_raw)
    if client_data.get("type") != expected_type:
        raise ValueError("Wrong type in clientDataJSON")
    if not hmac.compare_digest(b64url_decode(client_data["challenge"]), expected_challenge):
        raise ValueError("Challenge mismatch")
    incoming_origin = str(client_data.get("origin") or "").rstrip("/")
    if incoming_origin not in rp_origins():
        raise ValueError(f"Origin not allowed: {incoming_origin}")
    return client_data_raw


def assert_rp_hash(auth_data: bytes) -> None:
    expected_rp_hash = hashlib.sha256(rp_id().encode("utf-8")).digest()
    if auth_data[:32] != expected_rp_hash:
        raise ValueError("RP ID hash mismatch")


def cookie_response(payload: dict[str, Any], token: str | None = None, status: int = 200):
    result = make_response(jsonify(payload), status)
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


def clear_cookie_response(payload: dict[str, Any]):
    result = make_response(jsonify(payload))
    result.delete_cookie(SESSION_COOKIE_NAME, path="/", secure=request_is_secure(), samesite="Lax")
    return result


def register_routes(app: Flask) -> None:
    @app.get("/.well-known/webauthn")
    def webauthn_well_known():
        return jsonify({"origins": rp_origins()})

    @app.get("/api/auth/status")
    def auth_status():
        user = current_user()
        configured = credentials_exist()
        required = auth_required()
        payload = {
            "enabled": required,
            "envEnabled": auth_enabled(),
            "configured": configured,
            "credentialCount": passkey_count(),
            "methods": {"passkey": passkey_count() > 0, "password": password_configured()},
            "setupRequired": required and not configured,
            "authenticated": bool(user),
            "username": user["username"] if user else None,
            "userId": user["id"] if user else None,
            "role": user["role"] if user else ("owner" if not required else None),
            "isOwner": is_owner(),
            "rpId": rp_id(),
            "rpOrigins": rp_origins(),
        }
        if user or not required:
            uid = user["id"] if user else OWNER_ID
            payload["credentialCount"] = passkey_count(uid)
            payload["passwordConfigured"] = password_configured(uid)
            payload["mfaEnabled"] = mfa_enabled(uid)
            payload["recoveryCodesLeft"] = recovery_codes_left(uid)
            from . import profile  # late import: profile gebruikt dit module

            payload.update(profile.get(uid))
        return jsonify(payload)

    @app.get("/api/auth/credentials")
    def credentials_list():
        if auth_required() and credentials_exist() and not current_user():
            return jsonify({"error": "Unauthorized"}), 401
        return jsonify({"credentials": credential_rows(acting_user_id())})

    @app.delete("/api/auth/credentials/<credential_id>")
    def credentials_delete(credential_id: str):
        if auth_required() and not current_user():
            return jsonify({"error": "Unauthorized"}), 401
        uid = acting_user_id()  # alleen je eigen passkeys
        with connect() as conn:
            row = conn.execute(
                "SELECT id FROM passkey_credentials WHERE id=? AND user_id=?",
                (credential_id, uid),
            ).fetchone()
            if not row:
                return jsonify({"error": "Not found"}), 404
            count = conn.execute("SELECT COUNT(*) AS count FROM passkey_credentials WHERE user_id=?", (uid,)).fetchone()["count"]
            if int(count) <= 1 and auth_required() and not password_configured(uid):
                return jsonify({"error": tr("Je kunt de laatste passkey niet verwijderen.", "You cannot delete the last passkey.")}), 400
            conn.execute("DELETE FROM passkey_credentials WHERE id=?", (credential_id,))
        return jsonify({"status": "deleted", "remaining": passkey_count(uid)})

    @app.post("/api/auth/register/options")
    def register_options():
        if not auth_enabled():
            return jsonify({"error": "Passkey auth is disabled"}), 400
        body = request.get_json(silent=True) or {}
        from . import users  # lokaal: users importeert dit module

        invite = None
        if body.get("invite"):
            invite = users.find_invite(body.get("invite"))
            if not invite:
                return jsonify({"error": tr("Deze uitnodiging bestaat niet meer of is verlopen.", "This invitation no longer exists or has expired.")}), 404
        elif credentials_exist() and auth_required() and not current_user():
            return jsonify({"error": "Login required to add another passkey"}), 401

        credential_name = str(body.get("credentialName") or body.get("credential_name") or "Owner passkey").strip()
        if invite:
            user_id = invite["userId"]
            username = invite["username"] if invite["kind"] == "reset" else str(body.get("username") or invite["username"]).strip()
            problem = None if invite["kind"] == "reset" else users.check_new_username(username, invite)
            if problem:
                return jsonify({"error": problem}), 400
        else:
            user_id = acting_user_id()
            current = current_user()
            # Alleen de eigenaar kan zijn naam wijzigen; een gewone gebruiker houdt zijn eigen naam.
            username = (str(body.get("username") or "admin").strip() or "admin") if user_id == OWNER_ID or not current else current["username"]
        challenge = make_challenge()
        store_challenge(f"register:{user_id}", challenge)
        with connect() as conn:
            rows = conn.execute("SELECT id FROM passkey_credentials WHERE user_id=? ORDER BY created_at", (user_id,)).fetchall()

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
    def register_verify():
        ensure_db()
        if not auth_enabled():
            return jsonify({"error": "Passkey auth is disabled"}), 400
        body = request.get_json(silent=True) or {}
        from . import users  # lokaal: users importeert dit module

        invite = None
        if body.get("invite"):
            invite = users.find_invite(body.get("invite"))
            if not invite:
                return jsonify({"error": tr("Deze uitnodiging bestaat niet meer of is verlopen.", "This invitation no longer exists or has expired.")}), 404
            user_id = invite["userId"]
            username = invite["username"] if invite["kind"] == "reset" else str(body.get("username") or invite["username"]).strip()
        else:
            if credentials_exist() and auth_required() and not current_user():
                return jsonify({"error": "Login required to add another passkey"}), 401
            user_id = acting_user_id()  # een passkey kun je alleen voor jezelf toevoegen
            current = current_user()
            username = (str(body.get("username") or "admin").strip() or "admin") if user_id == OWNER_ID or not current else current["username"]
        credential_name = str(body.get("credentialName") or body.get("credential_name") or "Owner passkey").strip()
        credential = body.get("credential") or {}
        challenge = pop_challenge(f"register:{user_id}")
        if not challenge:
            return jsonify({"error": "No pending challenge"}), 400

        try:
            client_data_raw = validate_client_data(
                client_data_json_b64=credential["response"]["clientDataJSON"],
                expected_type="webauthn.create",
                expected_challenge=challenge,
            )
            credential_id, cose_key_bytes, auth_data, sign_count = parse_attestation_object(
                credential["response"]["attestationObject"]
            )
            assert_rp_hash(auth_data)
            credential_id_b64 = b64url_encode(credential_id)
        except Exception as exc:
            return jsonify({"error": f"Verification failed: {exc}"}), 400

        from . import onboarding  # lokaal: onboarding importeert dit module

        if invite:
            if invite["kind"] == "new" and users.check_new_username(username, invite):
                return jsonify({"error": users.check_new_username(username, invite)}), 409
            try:
                users.register_account(invite, username)  # nieuw account, of de oude inloggegevens wissen bij een reset
            except users.UsernameTaken:
                return jsonify({"error": tr("Deze naam is al in gebruik. Kies een andere.", "This name is already taken. Choose another one.")}), 409
        elif user_id == OWNER_ID:
            onboarding.begin()
        now = utcnow().isoformat()
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO users (id, username, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET username=excluded.username
                """,
                (user_id, username, now),
            )
            conn.execute(
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
        if invite:
            users.consume_invite(invite["id"])
        elif user_id == OWNER_ID:
            set_setting(SETTING_AUTH_MODE, "passkey")
        token = create_token(user_id, username)
        return cookie_response({"status": "ok", "username": username}, token)

    @app.post("/api/auth/login/options")
    def login_options():
        ensure_db()
        if not auth_enabled():
            return jsonify({"error": "Passkey auth is disabled"}), 400
        with connect() as conn:
            rows = conn.execute("SELECT id FROM passkey_credentials ORDER BY created_at").fetchall()
        if not rows:
            return jsonify({"error": "No passkey registered yet"}), 400
        challenge = make_challenge()
        store_challenge("login", challenge)
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
    def login_verify():
        if not auth_enabled():
            return jsonify({"error": "Passkey auth is disabled"}), 400
        body = request.get_json(silent=True) or {}
        credential = body.get("credential") or {}
        credential_id = str(credential.get("id") or "")
        challenge = pop_challenge("login")
        if not credential_id:
            return jsonify({"error": "Credential id is required"}), 400
        if not challenge:
            return jsonify({"error": "No pending challenge"}), 400

        with connect() as conn:
            stored = conn.execute(
                """
                SELECT c.*, u.username
                FROM passkey_credentials c
                JOIN users u ON u.id = c.user_id
                WHERE c.id=?
                """,
                (credential_id,),
            ).fetchone()
        if not stored:
            return jsonify({"error": "Unknown credential"}), 400

        try:
            client_data_raw = validate_client_data(
                client_data_json_b64=credential["response"]["clientDataJSON"],
                expected_type="webauthn.get",
                expected_challenge=challenge,
            )
            auth_data = b64url_decode(credential["response"]["authenticatorData"])
            signature = b64url_decode(credential["response"]["signature"])
            client_data_hash = hashlib.sha256(client_data_raw).digest()
            assert_rp_hash(auth_data)
            verify_signature(bytes(stored["public_key"]), auth_data, client_data_hash, signature)
            _, _, new_sign_count = parse_auth_data(auth_data)
        except Exception as exc:
            return jsonify({"error": f"Verification failed: {exc}"}), 400

        with connect() as conn:
            conn.execute(
                "UPDATE passkey_credentials SET sign_count=?, last_used_at=? WHERE id=?",
                (new_sign_count, utcnow().isoformat(), credential_id),
            )
        token = create_token(str(stored["user_id"]), str(stored["username"]))
        return cookie_response({"status": "ok", "username": stored["username"]}, token)

    @app.post("/api/auth/logout")
    def logout():
        return clear_cookie_response({"status": "ok"})


# Alleen de beheerder: Mealie en Albert Heijn koppelen, gebruikers beheren en de serverinstellingen. Gewone gebruikers
# zien de status ervan wel (/api/mealie/status, /api/ah/auth/status), maar kunnen niets wijzigen.
ADMIN_PREFIXES = ("/api/users", "/api/server-settings", "/api/audit", "/api/update", "/api/mealie/auth", "/api/ah/auth/", "/api/picnic/auth/", "/api/jumbo/auth/")
ADMIN_PATHS = {"/api/ah/auth", "/api/ah/explore", "/api/picnic/auth", "/api/picnic/explore", "/api/jumbo/auth", "/api/jumbo/explore"}
ADMIN_EXCEPTIONS = {"/api/ah/auth/status", "/api/picnic/auth/status", "/api/jumbo/auth/status"}


def admin_only(path: str) -> bool:
    if path in ADMIN_EXCEPTIONS:
        return False
    return path in ADMIN_PATHS or any(path == prefix or path.startswith(prefix + "/") or (prefix.endswith("/") and path.startswith(prefix)) for prefix in ADMIN_PREFIXES)


def require_auth_for_request() -> tuple[dict[str, Any], int] | None:
    if not auth_required():
        return None
    path = request.path
    # De MCP-ingang en OAuth controleren zelf hun toegangstoken (of, voor /oauth/authorize, de sessie).
    public_prefixes = ("/static/", "/api/auth/", "/api/invites/", "/oauth/", "/.well-known/oauth-")
    public_paths = {
        "/",
        "/healthz",
        "/.well-known/webauthn",
        "/favicon.ico",
        "/mcp",
        # De onboarding moet ook voor de eerste inlog bereikbaar zijn.
        "/api/onboarding/status",
        "/api/onboarding/start",
    }
    if path in public_paths or any(path.startswith(prefix) for prefix in public_prefixes):
        return None
    user = current_user()
    if user:
        if admin_only(path) and user["id"] != OWNER_ID:
            return {"error": tr("Dit kan alleen de beheerder.", "Only the administrator can do this.")}, 403
        return None
    if not credentials_exist():
        return {"error": "Setup required"}, 401
    return {"error": "Unauthorized"}, 401
