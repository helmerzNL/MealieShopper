"""Inloggen met gebruikersnaam en wachtwoord, met optionele tweestapsverificatie (TOTP).

Bedoeld voor installaties zonder domeinnaam, waar passkeys niet werken (WebAuthn vraagt
HTTPS en een domeinnaam). Er is één eigenaar (`owner`); de sessie is dezelfde cookie als bij
een passkey, dus de rest van de app merkt geen verschil.

Veiligheid in het kort:
- Wachtwoorden worden met scrypt gehasht (eigen zout per wachtwoord), nooit bewaard of gelogd.
- Een foute poging kost altijd evenveel rekentijd, ook bij een onbekende gebruikersnaam.
- Pogingen worden per IP-adres en in totaal begrensd, en de teller staat in de database.
- Het TOTP-geheim staat versleuteld in de database; een code werkt maar één keer.
- Herstelcodes (16 tekens, 80 bit) worden alleen als SHA-256 bewaard en werken één keer.
- Een nieuw wachtwoord of een uitgezette MFA maakt alle andere sessies ongeldig.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets
import struct
import time
from contextlib import closing
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote

import jwt
from flask import Flask, jsonify, request

from . import auth
from .i18n import tr

OWNER_ID = "owner"

SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_MAXMEM = 128 * 1024 * 1024

MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 256
MAX_USERNAME_LENGTH = 64

TOTP_DIGITS = 6
TOTP_PERIOD = 30
TOTP_WINDOW = 1  # een stap eerder of later, voor een klok die een beetje afwijkt
RECOVERY_CODE_COUNT = 8
_RECOVERY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # geen 0/O en 1/I

MFA_TICKET_SECONDS = 5 * 60

ATTEMPTS_PER_WINDOW = 5
ATTEMPTS_PER_WINDOW_TOTAL = 25
ATTEMPT_WINDOW = timedelta(minutes=15)
LOCK_DURATION = timedelta(minutes=15)


# ---------------------------------------------------------------- wachtwoord

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, maxmem=SCRYPT_MAXMEM, dklen=32
    )
    return "scrypt${}${}${}${}${}".format(
        SCRYPT_N, SCRYPT_R, SCRYPT_P, auth.b64url_encode(salt), auth.b64url_encode(digest)
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, expected = stored.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=auth.b64url_decode(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            maxmem=SCRYPT_MAXMEM,
            dklen=32,
        )
        return hmac.compare_digest(digest, auth.b64url_decode(expected))
    except (ValueError, TypeError, binascii.Error):
        return False


_dummy_hash: str | None = None


def _burn_time(password: str) -> None:
    """Even lang rekenen als bij een echt wachtwoord, zodat de duur niets verraadt."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = hash_password(secrets.token_urlsafe(8))
    verify_password(password, _dummy_hash)


def validate_username(value: Any) -> tuple[str, str | None]:
    username = str(value or "").strip()
    if not username:
        return username, tr("Vul een gebruikersnaam in.", "Enter a username.")
    if len(username) > MAX_USERNAME_LENGTH:
        return username, tr(
            f"Een gebruikersnaam is maximaal {MAX_USERNAME_LENGTH} tekens.",
            f"A username is at most {MAX_USERNAME_LENGTH} characters.",
        )
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in username):
        return username, tr(
            "Een gebruikersnaam mag geen stuurtekens bevatten.", "A username cannot contain control characters."
        )
    return username, None


def validate_password(password: Any, username: str = "") -> str | None:
    if not isinstance(password, str) or not password:
        return tr("Vul een wachtwoord in.", "Enter a password.")
    if len(password) < MIN_PASSWORD_LENGTH:
        return tr(
            f"Kies een wachtwoord van minstens {MIN_PASSWORD_LENGTH} tekens.",
            f"Choose a password of at least {MIN_PASSWORD_LENGTH} characters.",
        )
    if len(password) > MAX_PASSWORD_LENGTH:
        return tr(
            f"Een wachtwoord is maximaal {MAX_PASSWORD_LENGTH} tekens.",
            f"A password is at most {MAX_PASSWORD_LENGTH} characters.",
        )
    if len(set(password)) < 4:
        return tr(
            "Dit wachtwoord is te voorspelbaar. Gebruik meer verschillende tekens.",
            "This password is too predictable. Use more different characters.",
        )
    if username and len(username) >= 4 and username.casefold() in password.casefold():
        return tr("Zet je gebruikersnaam niet in je wachtwoord.", "Don't put your username in your password.")
    return None


def _stored_hash() -> str:
    with closing(auth.connect()) as conn:
        row = conn.execute(
            "SELECT password_hash FROM password_credentials WHERE user_id = ?", (OWNER_ID,)
        ).fetchone()
    return str(row["password_hash"]) if row else ""


def _owner_username() -> str:
    with closing(auth.connect()) as conn:
        row = conn.execute("SELECT username FROM users WHERE id = ?", (OWNER_ID,)).fetchone()
    return str(row["username"]) if row else ""


def _store_password(username: str, password: str) -> None:
    now = auth.utcnow().isoformat()
    password_hash = hash_password(password)
    with closing(auth.connect()) as conn, conn:
        conn.execute(
            """
            INSERT INTO users (id, username, created_at) VALUES (?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET username = excluded.username
            """,
            (OWNER_ID, username, now),
        )
        conn.execute(
            """
            INSERT INTO password_credentials (user_id, password_hash, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET password_hash = excluded.password_hash, updated_at = excluded.updated_at
            """,
            (OWNER_ID, password_hash, now),
        )


# --------------------------------------------------------------- begrenzing

def _client_keys() -> list[str]:
    return [f"ip:{request.remote_addr or 'onbekend'}", "alle"]


def locked_seconds() -> int:
    """Hoeveel seconden je nog moet wachten (0 = je mag proberen)."""
    now = auth.utcnow()
    remaining = 0
    with closing(auth.connect()) as conn:
        for key in _client_keys():
            row = conn.execute("SELECT locked_until FROM login_attempts WHERE key = ?", (key,)).fetchone()
            if row and row["locked_until"]:
                until = datetime.fromisoformat(row["locked_until"])
                remaining = max(remaining, int((until - now).total_seconds()) + 1)
    return max(0, remaining)


def register_failure() -> None:
    now = auth.utcnow()
    limits = {"alle": ATTEMPTS_PER_WINDOW_TOTAL}
    with closing(auth.connect()) as conn, conn:
        for key in _client_keys():
            limit = limits.get(key, ATTEMPTS_PER_WINDOW)
            row = conn.execute(
                "SELECT failures, window_start FROM login_attempts WHERE key = ?", (key,)
            ).fetchone()
            failures, start = 0, now
            if row:
                start = datetime.fromisoformat(row["window_start"])
                failures = int(row["failures"])
                if now - start > ATTEMPT_WINDOW:
                    failures, start = 0, now
            failures += 1
            locked_until = (now + LOCK_DURATION).isoformat() if failures >= limit else None
            conn.execute(
                """
                INSERT INTO login_attempts (key, failures, window_start, locked_until) VALUES (?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET failures = excluded.failures,
                  window_start = excluded.window_start, locked_until = excluded.locked_until
                """,
                (key, failures, start.isoformat(), locked_until),
            )


def reset_failures() -> None:
    """Na een geslaagde login telt alleen het eigen IP-adres weer vanaf nul."""
    with closing(auth.connect()) as conn, conn:
        conn.execute("DELETE FROM login_attempts WHERE key = ?", (_client_keys()[0],))


def _too_many():
    seconds = locked_seconds()
    minutes = max(1, (seconds + 59) // 60)
    response = jsonify(
        {
            "error": tr(
                f"Te veel mislukte pogingen. Probeer het over {minutes} {'minuut' if minutes == 1 else 'minuten'} opnieuw.",
                f"Too many failed attempts. Try again in {minutes} {'minute' if minutes == 1 else 'minutes'}.",
            ),
            "retryAfter": seconds,
        }
    )
    response.status_code = 429
    response.headers["Retry-After"] = str(seconds)
    return response


# -------------------------------------------------------------------- TOTP

def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii")  # 32 tekens, geen opvulling nodig


def _hotp(secret: bytes, counter: int) -> str:
    digest = hmac.new(secret, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % (10**TOTP_DIGITS)
    return str(value).zfill(TOTP_DIGITS)


def totp_code(secret_b32: str, at: float | None = None) -> str:
    counter = int((time.time() if at is None else at) // TOTP_PERIOD)
    return _hotp(base64.b32decode(secret_b32), counter)


def check_totp(secret_b32: str, code: str, last_counter: int = 0, at: float | None = None) -> int | None:
    """De stap van de geldige code, of None. Een stap die al gebruikt is (of ouder) telt niet meer."""
    code = "".join(str(code or "").split())
    if len(code) != TOTP_DIGITS or not code.isdigit():
        return None
    try:
        secret = base64.b32decode(secret_b32)
    except (binascii.Error, ValueError):
        return None
    now_counter = int((time.time() if at is None else at) // TOTP_PERIOD)
    for counter in range(now_counter - TOTP_WINDOW, now_counter + TOTP_WINDOW + 1):
        if counter > last_counter and hmac.compare_digest(_hotp(secret, counter), code):
            return counter
    return None


def otpauth_uri(secret_b32: str, account: str) -> str:
    issuer = auth.rp_name()
    label = quote(f"{issuer}:{account}", safe="")
    return (
        f"otpauth://totp/{label}?secret={secret_b32}&issuer={quote(issuer, safe='')}"
        f"&algorithm=SHA1&digits={TOTP_DIGITS}&period={TOTP_PERIOD}"
    )


def _read_totp(confirmed: bool | None = None) -> dict[str, Any] | None:
    with closing(auth.connect()) as conn:
        row = conn.execute("SELECT * FROM mfa_totp WHERE user_id = ?", (OWNER_ID,)).fetchone()
    if not row:
        return None
    if confirmed is not None and bool(row["confirmed"]) != confirmed:
        return None
    try:
        secret = auth.secret_box().decrypt(str(row["secret"]).encode("ascii")).decode("ascii")
    except Exception:  # noqa: BLE001 - sleutel veranderd: alleen herstelcodes werken nog
        secret = ""
    return {"secret": secret, "confirmed": bool(row["confirmed"]), "lastCounter": int(row["last_counter"])}


def _save_last_counter(counter: int) -> None:
    with closing(auth.connect()) as conn, conn:
        conn.execute("UPDATE mfa_totp SET last_counter = ? WHERE user_id = ?", (counter, OWNER_ID))


# -------------------------------------------------------------- herstelcodes

def _normalize_recovery(code: str) -> str:
    return "".join(ch for ch in str(code or "").upper() if ch.isalnum())


def _hash_recovery(code: str) -> str:
    return hashlib.sha256(_normalize_recovery(code).encode("ascii", "ignore")).hexdigest()


def new_recovery_codes() -> list[str]:
    codes = []
    for _ in range(RECOVERY_CODE_COUNT):
        raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(16))
        codes.append("-".join(raw[i:i + 4] for i in range(0, 16, 4)))
    return codes


def _store_recovery_codes(codes: list[str]) -> None:
    now = auth.utcnow().isoformat()
    with closing(auth.connect()) as conn, conn:
        conn.execute("DELETE FROM mfa_recovery_codes WHERE user_id = ?", (OWNER_ID,))
        conn.executemany(
            "INSERT INTO mfa_recovery_codes (code_hash, user_id, created_at) VALUES (?, ?, ?)",
            [(_hash_recovery(code), OWNER_ID, now) for code in codes],
        )


def _use_recovery_code(code: str) -> bool:
    if len(_normalize_recovery(code)) != 16:
        return False
    with closing(auth.connect()) as conn, conn:
        cursor = conn.execute(
            "DELETE FROM mfa_recovery_codes WHERE user_id = ? AND code_hash = ?",
            (OWNER_ID, _hash_recovery(code)),
        )
        return cursor.rowcount == 1


# ------------------------------------------------------------ MFA-kaartje

def _make_ticket() -> str:
    return jwt.encode(
        {
            "purpose": "mfa",
            "sub": OWNER_ID,
            "ep": auth.session_epoch(),
            "exp": auth.utcnow() + timedelta(seconds=MFA_TICKET_SECONDS),
        },
        auth.signing_secret(),
        algorithm="HS256",
    )


def _read_ticket(ticket: Any) -> bool:
    try:
        payload = jwt.decode(str(ticket or ""), auth.signing_secret(), algorithms=["HS256"])
    except Exception:  # noqa: BLE001
        return False
    return payload.get("purpose") == "mfa" and payload.get("sub") == OWNER_ID and int(payload.get("ep") or 0) == auth.session_epoch()


# --------------------------------------------------------------------- routes

def _json_error(message: str, status: int):
    return jsonify({"error": message}), status


def _session_response(payload: dict[str, Any] | None = None):
    username = _owner_username() or "admin"
    return auth.cookie_response({"status": "ok", "username": username, **(payload or {})}, auth.create_token(OWNER_ID, username))


def _needs_session():
    """None als het mag; anders het 401-antwoord."""
    # Op een nieuwe installatie (nog geen inlogmethode) is er geen sessie om te vragen.
    if auth.auth_required() and auth.credentials_exist() and not auth.current_user():
        return _json_error(tr("Log eerst in.", "Log in first."), 401)
    return None


def register_routes(app: Flask) -> None:
    @app.post("/api/auth/open")
    def auth_open():
        """Bewust zonder inloggen verder; alleen op een nieuwe installatie, en alleen met bevestiging."""
        from . import onboarding

        body = request.get_json(silent=True) or {}
        if not auth.auth_enabled():
            return _json_error(
                tr("Inloggen staat al uit via de omgevingsvariabelen.", "Login is already turned off through the environment variables."), 400
            )
        if auth.credentials_exist():
            return _json_error(tr("Er is al een manier om in te loggen ingesteld.", "A way to log in is already set up."), 409)
        if body.get("confirm") is not True:
            return _json_error(
                tr("Bevestig dat je zonder beveiliging verder wilt.", "Confirm that you want to continue without security."), 400
            )
        onboarding.begin()
        auth.set_setting(auth.SETTING_AUTH_MODE, "none")
        return jsonify({"status": "ok"})

    @app.post("/api/auth/password")
    def password_set():
        """Eerste wachtwoord (nieuwe installatie), of wachtwoord wijzigen/toevoegen als je bent ingelogd."""
        from . import onboarding

        if not auth.auth_enabled():
            return _json_error(
                tr("Inloggen staat uit via de omgevingsvariabelen.", "Login is turned off through the environment variables."), 400
            )
        denied = _needs_session()
        if denied:
            return denied
        body = request.get_json(silent=True) or {}
        username, problem = validate_username(body.get("username") or _owner_username() or "admin")
        password = body.get("password")
        problem = problem or validate_password(password, username)
        if problem:
            return _json_error(problem, 400)

        replacing = auth.password_configured()
        if replacing:
            if locked_seconds():
                return _too_many()
            if not verify_password(str(body.get("currentPassword") or ""), _stored_hash()):
                register_failure()
                return _json_error(tr("Het huidige wachtwoord klopt niet.", "The current password is incorrect."), 403)
            reset_failures()

        onboarding.begin()
        _store_password(username, password)
        auth.set_setting(auth.SETTING_AUTH_MODE, "password" if auth.passkey_count() == 0 else "")
        if replacing:
            auth.bump_session_epoch()  # andere apparaten moeten opnieuw inloggen
        return _session_response({"replaced": replacing})

    @app.post("/api/auth/password/login")
    def password_login():
        if not auth.auth_required() or not auth.password_configured():
            return _json_error(tr("Inloggen met een wachtwoord is niet ingesteld.", "Password login is not set up."), 400)
        if locked_seconds():
            return _too_many()
        body = request.get_json(silent=True) or {}
        username = str(body.get("username") or "").strip()
        password = str(body.get("password") or "")
        owner = _owner_username()
        valid_user = bool(username) and hmac.compare_digest(username.casefold().encode(), owner.casefold().encode())
        if len(password) <= MAX_PASSWORD_LENGTH:  # geen scrypt over een absurd lang wachtwoord
            if valid_user:
                matches = verify_password(password, _stored_hash())
            else:
                _burn_time(password)
                matches = False
            if matches:
                if auth.mfa_enabled():
                    return jsonify({"status": "mfa", "mfaRequired": True, "ticket": _make_ticket()})
                reset_failures()
                return _session_response()
        register_failure()
        return _json_error(tr("Gebruikersnaam of wachtwoord klopt niet.", "Username or password is incorrect."), 401)

    @app.post("/api/auth/password/mfa")
    def password_mfa():
        if not auth.mfa_enabled():
            return _json_error(tr("Tweestapsverificatie is niet ingesteld.", "Two-step verification is not set up."), 400)
        if locked_seconds():
            return _too_many()
        body = request.get_json(silent=True) or {}
        if not _read_ticket(body.get("ticket")):
            return _json_error(
                tr(
                    "Deze inlogpoging is verlopen. Log opnieuw in met je wachtwoord.",
                    "This login attempt has expired. Log in again with your password.",
                ),
                401,
            )
        code = str(body.get("code") or "")
        record = _read_totp(confirmed=True)
        counter = check_totp(record["secret"], code, record["lastCounter"]) if record and record["secret"] else None
        if counter is not None:
            _save_last_counter(counter)
        elif not _use_recovery_code(code):
            register_failure()
            return _json_error(tr("Deze code klopt niet.", "This code is incorrect."), 401)
        reset_failures()
        return _session_response({"recoveryCodesLeft": auth.recovery_codes_left()})

    @app.post("/api/auth/mfa/start")
    def mfa_start():
        denied = _needs_session()
        if denied:
            return denied
        if not auth.password_configured():
            return _json_error(tr("Stel eerst een wachtwoord in.", "Set a password first."), 400)
        if auth.mfa_enabled():
            return _json_error(tr("Tweestapsverificatie staat al aan.", "Two-step verification is already on."), 409)
        secret = new_totp_secret()
        encrypted = auth.secret_box().encrypt(secret.encode("ascii")).decode("ascii")
        with closing(auth.connect()) as conn, conn:
            conn.execute(
                """
                INSERT INTO mfa_totp (user_id, secret, confirmed, last_counter, created_at) VALUES (?, ?, 0, 0, ?)
                ON CONFLICT(user_id) DO UPDATE SET secret = excluded.secret, confirmed = 0, last_counter = 0,
                  created_at = excluded.created_at
                """,
                (OWNER_ID, encrypted, auth.utcnow().isoformat()),
            )
        account = _owner_username() or "admin"
        return jsonify(
            {
                "secret": " ".join(secret[i:i + 4] for i in range(0, len(secret), 4)),
                "otpauthUri": otpauth_uri(secret, account),
                "issuer": auth.rp_name(),
                "account": account,
                "digits": TOTP_DIGITS,
                "period": TOTP_PERIOD,
            }
        )

    @app.post("/api/auth/mfa/confirm")
    def mfa_confirm():
        denied = _needs_session()
        if denied:
            return denied
        if locked_seconds():
            return _too_many()
        pending = _read_totp(confirmed=False)
        if not pending or not pending["secret"]:
            return _json_error(
                tr(
                    "Begin opnieuw met het instellen van tweestapsverificatie.",
                    "Start setting up two-step verification again.",
                ),
                400,
            )
        body = request.get_json(silent=True) or {}
        counter = check_totp(pending["secret"], str(body.get("code") or ""))
        if counter is None:
            register_failure()
            return _json_error(
                tr(
                    "Deze code klopt niet. Controleer de tijd op je telefoon en probeer het opnieuw.",
                    "This code is incorrect. Check the time on your phone and try again.",
                ),
                400,
            )
        reset_failures()
        codes = new_recovery_codes()
        _store_recovery_codes(codes)
        with closing(auth.connect()) as conn, conn:
            conn.execute(
                "UPDATE mfa_totp SET confirmed = 1, last_counter = ? WHERE user_id = ?", (counter, OWNER_ID)
            )
        auth.bump_session_epoch()
        return _session_response({"recoveryCodes": codes})

    def _check_current_password(body: dict[str, Any]):
        if not auth.password_configured():
            return _json_error(tr("Er is geen wachtwoord ingesteld.", "No password is set."), 400)
        if locked_seconds():
            return _too_many()
        if not verify_password(str(body.get("password") or ""), _stored_hash()):
            register_failure()
            return _json_error(tr("Het wachtwoord klopt niet.", "The password is incorrect."), 403)
        reset_failures()
        return None

    @app.post("/api/auth/mfa/disable")
    def mfa_disable():
        denied = _needs_session()
        if denied:
            return denied
        wrong = _check_current_password(request.get_json(silent=True) or {})
        if wrong:
            return wrong
        with closing(auth.connect()) as conn, conn:
            conn.execute("DELETE FROM mfa_totp WHERE user_id = ?", (OWNER_ID,))
            conn.execute("DELETE FROM mfa_recovery_codes WHERE user_id = ?", (OWNER_ID,))
        auth.bump_session_epoch()
        return _session_response()

    @app.post("/api/auth/mfa/recovery")
    def mfa_recovery():
        denied = _needs_session()
        if denied:
            return denied
        if not auth.mfa_enabled():
            return _json_error(tr("Tweestapsverificatie staat niet aan.", "Two-step verification is not on."), 400)
        wrong = _check_current_password(request.get_json(silent=True) or {})
        if wrong:
            return wrong
        codes = new_recovery_codes()
        _store_recovery_codes(codes)
        return jsonify({"recoveryCodes": codes})
