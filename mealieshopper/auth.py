from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import struct
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


def auth_secret() -> str:
    configured = os.environ.get("MEALIESHOPPER_AUTH_SECRET") or os.environ.get("JWT_SECRET")
    if configured:
        return configured.strip()
    seed = f"{db_path()}:{os.environ.get('MEALIE_URL', 'mealieshopper')}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


_signing_keys: dict[str, str] = {}


def signing_secret() -> str:
    """Sleutel waarmee sessies worden ondertekend.

    Is MEALIESHOPPER_AUTH_SECRET (of JWT_SECRET) ingesteld, dan die. Anders een willekeurige sleutel
    die één keer in de data-map wordt bewaard (`session.key`). De vroegere afgeleide sleutel hing
    alleen van het pad en MEALIE_URL af en was dus te raden: wie dat wist kon een sessie namaken.
    """
    configured = os.environ.get("MEALIESHOPPER_AUTH_SECRET") or os.environ.get("JWT_SECRET")
    if configured and configured.strip():
        return configured.strip()
    path = data_dir() / "session.key"
    cached = _signing_keys.get(str(path))
    if cached:
        return cached
    try:
        key = path.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        key = ""
    if len(key) < 32:
        data_dir().mkdir(parents=True, exist_ok=True)
        key = secrets.token_hex(32)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:  # een andere werker was ons voor
            key = path.read_text(encoding="ascii").strip()
        else:
            with os.fdopen(descriptor, "w", encoding="ascii") as handle:
                handle.write(key)
    _signing_keys[str(path)] = key
    return key


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
            """
        )
    _INITIALIZED = True


def ensure_db() -> None:
    if not _INITIALIZED:
        init_db()


def passkey_count() -> int:
    ensure_db()
    with connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS count FROM passkey_credentials").fetchone()
        return int(row["count"] if row else 0)


def password_configured() -> bool:
    ensure_db()
    with connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS count FROM password_credentials").fetchone()
        return bool(row and row["count"])


def mfa_enabled() -> bool:
    ensure_db()
    with closing(connect()) as conn:
        row = conn.execute("SELECT COUNT(*) AS count FROM mfa_totp WHERE confirmed = 1").fetchone()
    return bool(row and row["count"])


def recovery_codes_left() -> int:
    ensure_db()
    with closing(connect()) as conn:
        row = conn.execute("SELECT COUNT(*) AS count FROM mfa_recovery_codes").fetchone()
    return int(row["count"] if row else 0)


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


def session_epoch() -> int:
    try:
        return int(get_setting(SETTING_SESSION_EPOCH, "0") or 0)
    except ValueError:
        return 0


def bump_session_epoch() -> int:
    """Maak alle bestaande sessies ongeldig (bij een nieuw wachtwoord of uitgezette MFA)."""
    epoch = session_epoch() + 1
    set_setting(SETTING_SESSION_EPOCH, str(epoch))
    return epoch


def credential_rows() -> list[dict[str, Any]]:
    ensure_db()
    with connect() as conn:
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
            ORDER BY c.created_at DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def secret_box() -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(auth_secret().encode("utf-8")).digest())
    return Fernet(key)


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


def auth_secret_depends_on_mealie_env() -> bool:
    """True als de sleutel voor opgeslagen gegevens van de omgevingsvariabele MEALIE_URL afhangt.

    Zonder MEALIESHOPPER_AUTH_SECRET (of JWT_SECRET) wordt de sleutel daarvan afgeleid; wie
    MEALIE_URL dan uit de omgeving haalt, kan opgeslagen koppelingen niet meer ontsleutelen.
    """
    if os.environ.get("MEALIESHOPPER_AUTH_SECRET") or os.environ.get("JWT_SECRET"):
        return False
    return bool(os.environ.get("MEALIE_URL"))


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
        return value


def create_token(user_id: str, username: str) -> str:
    return jwt.encode(
        {
            "sub": user_id,
            "usr": username,
            "ep": session_epoch(),
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
    if int(payload.get("ep") or 0) != session_epoch():
        return None
    with connect() as conn:
        row = conn.execute(
            "SELECT id, username, created_at FROM users WHERE id=?",
            (payload.get("sub"),),
        ).fetchone()
    return dict(row) if row else None


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
            "rpId": rp_id(),
            "rpOrigins": rp_origins(),
        }
        if user or not required:
            payload["passwordConfigured"] = password_configured()
            payload["mfaEnabled"] = mfa_enabled()
            payload["recoveryCodesLeft"] = recovery_codes_left()
        return jsonify(payload)

    @app.get("/api/auth/credentials")
    def credentials_list():
        if auth_required() and credentials_exist() and not current_user():
            return jsonify({"error": "Unauthorized"}), 401
        return jsonify({"credentials": credential_rows()})

    @app.delete("/api/auth/credentials/<credential_id>")
    def credentials_delete(credential_id: str):
        if auth_required() and not current_user():
            return jsonify({"error": "Unauthorized"}), 401
        with connect() as conn:
            row = conn.execute(
                "SELECT id FROM passkey_credentials WHERE id=?",
                (credential_id,),
            ).fetchone()
            if not row:
                return jsonify({"error": "Not found"}), 404
            count = conn.execute("SELECT COUNT(*) AS count FROM passkey_credentials").fetchone()["count"]
            if int(count) <= 1 and auth_required() and not password_configured():
                return jsonify({"error": tr("Je kunt de laatste passkey niet verwijderen.", "You cannot delete the last passkey.")}), 400
            conn.execute("DELETE FROM passkey_credentials WHERE id=?", (credential_id,))
        return jsonify({"status": "deleted", "remaining": passkey_count()})

    @app.post("/api/auth/register/options")
    def register_options():
        if not auth_enabled():
            return jsonify({"error": "Passkey auth is disabled"}), 400
        if credentials_exist() and auth_required() and not current_user():
            return jsonify({"error": "Login required to add another passkey"}), 401

        body = request.get_json(silent=True) or {}
        username = str(body.get("username") or "admin").strip() or "admin"
        credential_name = str(body.get("credentialName") or body.get("credential_name") or "Owner passkey").strip()
        user_id = "owner"
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
        if credentials_exist() and auth_required() and not current_user():
            return jsonify({"error": "Login required to add another passkey"}), 401

        body = request.get_json(silent=True) or {}
        user_id = str(body.get("userId") or body.get("user_id") or "owner")
        username = str(body.get("username") or "admin").strip() or "admin"
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


def require_auth_for_request() -> tuple[dict[str, Any], int] | None:
    if not auth_required():
        return None
    path = request.path
    public_prefixes = ("/static/", "/api/auth/")
    public_paths = {
        "/",
        "/healthz",
        "/.well-known/webauthn",
        "/favicon.ico",
        # De onboarding moet ook voor de eerste inlog bereikbaar zijn.
        "/api/onboarding/status",
        "/api/onboarding/start",
    }
    if path in public_paths or any(path.startswith(prefix) for prefix in public_prefixes):
        return None
    if current_user():
        return None
    if not credentials_exist():
        return {"error": "Setup required"}, 401
    return {"error": "Unauthorized"}, 401
