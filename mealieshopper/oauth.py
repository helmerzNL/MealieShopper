"""OAuth 2.1 voor de MCP-server: AI-apps (Claude, ChatGPT, Copilot) loggen in via de eigenaar.

Een app registreert zichzelf (dynamic client registration, RFC 7591), stuurt de eigenaar naar
`/oauth/authorize` en krijgt na diens toestemming een code die ze met PKCE inwisselt voor een
toegangstoken (1 uur) en een verversingstoken (30 dagen, bij elk gebruik vervangen). Alleen
publieke clients: er is geen client secret, PKCE (S256) is verplicht.

Elke code en elk token hoort bij de gebruiker die toestemming gaf, en geeft alleen de rechten
van die gebruiker. Tokens staan alleen als hash in de database. Een token vervalt ook zodra de
sessieteller van die gebruiker verandert (nieuw wachtwoord, MFA uitgezet, reset) of de gebruiker
wordt verwijderd, en elke gebruiker kan zijn eigen apps intrekken.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
from contextlib import closing
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

from . import auth

SCOPE = "mealieshopper"
ACCESS_TTL = timedelta(hours=1)
REFRESH_TTL = timedelta(days=30)
CODE_TTL = timedelta(minutes=5)
MAX_CLIENTS = 100
MAX_REDIRECT_URIS = 10
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
FORBIDDEN_SCHEMES = {"javascript", "data", "file", "vbscript", "about", "blob", "ftp", "ws", "wss"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS oauth_clients (
  client_id TEXT PRIMARY KEY,
  client_name TEXT NOT NULL,
  redirect_uris TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS oauth_codes (
  code_hash TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  client_id TEXT NOT NULL,
  redirect_uri TEXT NOT NULL,
  code_challenge TEXT NOT NULL,
  resource TEXT NOT NULL,
  epoch INTEGER NOT NULL,
  expires_at TEXT NOT NULL,
  used INTEGER NOT NULL DEFAULT 0,
  FOREIGN KEY (client_id) REFERENCES oauth_clients(client_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS oauth_tokens (
  id TEXT PRIMARY KEY,
  family TEXT NOT NULL,
  user_id TEXT NOT NULL,
  client_id TEXT NOT NULL,
  access_hash TEXT NOT NULL UNIQUE,
  refresh_hash TEXT NOT NULL UNIQUE,
  resource TEXT NOT NULL,
  epoch INTEGER NOT NULL,
  access_expires TEXT NOT NULL,
  refresh_expires TEXT NOT NULL,
  rotated INTEGER NOT NULL DEFAULT 0,
  revoked INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  last_used_at TEXT,
  FOREIGN KEY (client_id) REFERENCES oauth_clients(client_id) ON DELETE CASCADE
);
"""


class OAuthError(Exception):
    """Een fout met een code uit RFC 6749 en een uitleg voor de ontwikkelaar van de client."""

    def __init__(self, code: str, description: str, status: int = 400):
        super().__init__(description)
        self.code = code
        self.description = description
        self.status = status


def _connect():
    auth.ensure_db()
    conn = auth.connect()
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def resource_ok(resource: str, resource_url: str) -> bool:
    """De resource (RFC 8707) is het MCP-adres zelf of de site waar het op staat."""
    origin = resource_url.rsplit("/", 1)[0]
    return resource.rstrip("/") in {resource_url.rstrip("/"), origin.rstrip("/")}


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# --- clients -------------------------------------------------------------------------------------

def valid_redirect_uri(uri: Any) -> bool:
    """https, http op de eigen computer (loopback) of een eigen schema van een app (`vscode://`)."""
    if not isinstance(uri, str) or not uri or len(uri) > 2000:
        return False
    try:
        parts = urlsplit(uri)
    except ValueError:
        return False
    if not parts.scheme or parts.fragment or any(ch.isspace() for ch in uri):
        return False
    scheme = parts.scheme.lower()
    if scheme == "https":
        return bool(parts.hostname)
    if scheme == "http":
        return parts.hostname in LOOPBACK_HOSTS
    if scheme in FORBIDDEN_SCHEMES:
        return False
    return bool(re.fullmatch(r"[a-z][a-z0-9+.-]*", scheme))


def _same_redirect(registered: str, requested: str) -> bool:
    if registered == requested:
        return True
    # RFC 8252: bij een loopback-adres kiest de app zelf een vrije poort.
    a, b = urlsplit(registered), urlsplit(requested)
    return (
        a.scheme == b.scheme == "http"
        and a.hostname == b.hostname
        and a.hostname in LOOPBACK_HOSTS
        and (a.path, a.query) == (b.path, b.query)
    )


def register_client(metadata: dict[str, Any]) -> dict[str, Any]:
    uris = metadata.get("redirect_uris")
    if not isinstance(uris, list) or not uris or len(uris) > MAX_REDIRECT_URIS:
        raise OAuthError("invalid_redirect_uri", "redirect_uris moet een lijst met 1 tot 10 adressen zijn")
    if not all(valid_redirect_uri(uri) for uri in uris):
        raise OAuthError("invalid_redirect_uri", "Een redirect-adres is niet toegestaan (gebruik https, http://localhost of een app-schema)")
    method = metadata.get("token_endpoint_auth_method", "none")
    if method != "none":
        raise OAuthError("invalid_client_metadata", "Alleen token_endpoint_auth_method 'none' (PKCE) wordt ondersteund")
    grants = metadata.get("grant_types") or ["authorization_code"]
    if not isinstance(grants, list) or not set(grants) <= {"authorization_code", "refresh_token"} or "authorization_code" not in grants:
        raise OAuthError("invalid_client_metadata", "grant_types moet authorization_code (en eventueel refresh_token) zijn")
    name = re.sub(r"\s+", " ", str(metadata.get("client_name") or "")).strip()[:80] or "Onbekende app"

    client_id = "msc_" + secrets.token_urlsafe(18)
    with closing(_connect()) as conn, conn:
        _purge_stale(conn)
        count = conn.execute("SELECT COUNT(*) FROM oauth_clients").fetchone()[0]
        if count >= MAX_CLIENTS:
            # Niemand mag de registratie blokkeren door het te vol te zetten: maak eerst plek door
            # apps weg te gooien die zich net registreerden maar nooit toestemming kregen.
            _purge_stale(conn, max_age=timedelta(minutes=15))
            count = conn.execute("SELECT COUNT(*) FROM oauth_clients").fetchone()[0]
        if count >= MAX_CLIENTS:
            raise OAuthError("temporarily_unavailable", "Te veel geregistreerde apps; probeer het later opnieuw", 503)
        conn.execute(
            "INSERT INTO oauth_clients (client_id, client_name, redirect_uris, created_at) VALUES (?, ?, ?, ?)",
            (client_id, name, json.dumps(uris), _iso(_now())),
        )
    return {
        "client_id": client_id,
        "client_name": name,
        "redirect_uris": uris,
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "scope": SCOPE,
    }


def _purge_stale(conn, max_age: timedelta = timedelta(days=1)) -> None:
    """Verwijder apps die zich registreerden maar nooit toestemming kregen (standaard na een dag)."""
    cutoff = _iso(_now() - max_age)
    conn.execute(
        """
        DELETE FROM oauth_clients
        WHERE created_at < ?
          AND client_id NOT IN (SELECT client_id FROM oauth_tokens)
        """,
        (cutoff,),
    )
    conn.execute("DELETE FROM oauth_codes WHERE expires_at < ?", (_iso(_now() - timedelta(hours=1)),))


def get_client(client_id: str) -> dict[str, Any] | None:
    with closing(_connect()) as conn:
        row = conn.execute("SELECT * FROM oauth_clients WHERE client_id = ?", (str(client_id or ""),)).fetchone()
    if not row:
        return None
    return {"client_id": row["client_id"], "name": row["client_name"], "redirect_uris": json.loads(row["redirect_uris"])}


def check_authorize_request(params: dict[str, str], resource_url: str) -> dict[str, Any]:
    """Controleer een autorisatieverzoek. Geeft de gegevens voor het toestemmingsscherm terug.

    Fouten voor client_id en redirect_uri mogen nooit naar het redirect-adres worden gestuurd
    (dat zou een open redirect zijn); die komen als gewone foutpagina terug.
    """
    client = get_client(params.get("client_id", ""))
    if not client:
        raise OAuthError("invalid_client", "Onbekende app. Voeg de connector opnieuw toe.")
    redirect_uri = params.get("redirect_uri", "")
    if not any(_same_redirect(registered, redirect_uri) for registered in client["redirect_uris"]):
        raise OAuthError("invalid_request", "Dit redirect-adres hoort niet bij deze app.")
    return {
        "client": client,
        "redirect_uri": redirect_uri,
        "state": params.get("state", ""),
        "error": _authorize_error(params, resource_url),
        "code_challenge": params.get("code_challenge", ""),
        "resource": params.get("resource") or resource_url,
    }


def _authorize_error(params: dict[str, str], resource_url: str) -> OAuthError | None:
    if params.get("response_type") != "code":
        return OAuthError("unsupported_response_type", "Alleen response_type=code wordt ondersteund")
    challenge = params.get("code_challenge", "")
    if params.get("code_challenge_method") != "S256" or not re.fullmatch(r"[A-Za-z0-9_-]{43}", challenge):
        return OAuthError("invalid_request", "PKCE met code_challenge_method=S256 is verplicht")
    resource = params.get("resource")
    if resource and not resource_ok(resource, resource_url):
        return OAuthError("invalid_target", "Onbekende resource")
    scope = params.get("scope", "").split()
    if any(item != SCOPE for item in scope):
        return OAuthError("invalid_scope", f"Alleen de scope '{SCOPE}' bestaat")
    return None


# --- codes en tokens -----------------------------------------------------------------------------

def issue_code(user_id: str, client_id: str, redirect_uri: str, code_challenge: str, resource: str) -> str:
    code = secrets.token_urlsafe(32)
    with closing(_connect()) as conn, conn:
        conn.execute(
            """
            INSERT INTO oauth_codes (code_hash, user_id, client_id, redirect_uri, code_challenge, resource, epoch, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (_hash(code), user_id, client_id, redirect_uri, code_challenge, resource, auth.session_epoch(user_id), _iso(_now() + CODE_TTL)),
        )
    return code


def _new_token_row(conn, *, family: str, user_id: str, client_id: str, resource: str, epoch: int) -> dict[str, Any]:
    access, refresh = "msa_" + secrets.token_urlsafe(32), "msr_" + secrets.token_urlsafe(32)
    now = _now()
    conn.execute(
        """
        INSERT INTO oauth_tokens
          (id, family, user_id, client_id, access_hash, refresh_hash, resource, epoch, access_expires, refresh_expires, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            secrets.token_hex(8), family, user_id, client_id, _hash(access), _hash(refresh), resource, epoch,
            _iso(now + ACCESS_TTL), _iso(now + REFRESH_TTL), _iso(now),
        ),
    )
    return {
        "access_token": access,
        "token_type": "Bearer",
        "expires_in": int(ACCESS_TTL.total_seconds()),
        "refresh_token": refresh,
        "scope": SCOPE,
    }


def _user_exists(conn, user_id: str) -> bool:
    return conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is not None


def _pkce_ok(verifier: str, challenge: str) -> bool:
    if not re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", verifier or ""):
        return False
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    expected = auth.b64url_encode(digest)
    return secrets.compare_digest(expected, challenge)


def exchange_code(form: dict[str, str], resource_url: str) -> dict[str, Any]:
    client_id = form.get("client_id", "")
    with closing(_connect()) as conn, conn:
        row = conn.execute("SELECT * FROM oauth_codes WHERE code_hash = ?", (_hash(form.get("code", "")),)).fetchone()
        if not row or row["client_id"] != client_id:
            raise OAuthError("invalid_grant", "Onbekende of verlopen code")
        used = row["used"]
        conn.execute("UPDATE oauth_codes SET used = 1 WHERE code_hash = ?", (row["code_hash"],))
        if used:
            # Een code die twee keer wordt ingewisseld is gestolen of gelekt: trek de tokens van de app in.
            conn.execute("UPDATE oauth_tokens SET revoked = 1 WHERE client_id = ? AND user_id = ?", (client_id, row["user_id"]))
            conn.commit()  # vóór de fout: anders draait de context manager het intrekken terug
            raise OAuthError("invalid_grant", "Deze code is al gebruikt")
        if row["expires_at"] < _iso(_now()):
            raise OAuthError("invalid_grant", "Deze code is verlopen")
        if row["redirect_uri"] != form.get("redirect_uri", ""):
            raise OAuthError("invalid_grant", "redirect_uri komt niet overeen met het verzoek")
        if not _pkce_ok(form.get("code_verifier", ""), row["code_challenge"]):
            raise OAuthError("invalid_grant", "PKCE-controle mislukt")
        resource = form.get("resource")
        if resource and resource.rstrip("/") != row["resource"].rstrip("/"):
            raise OAuthError("invalid_target", "resource komt niet overeen met het verzoek")
        if not _user_exists(conn, row["user_id"]) or row["epoch"] != auth.session_epoch(row["user_id"]):
            raise OAuthError("invalid_grant", "De inlog is sinds de toestemming gewijzigd; probeer opnieuw")
        return _new_token_row(conn, family=secrets.token_hex(8), user_id=row["user_id"], client_id=client_id, resource=row["resource"], epoch=row["epoch"])


def refresh(form: dict[str, str]) -> dict[str, Any]:
    client_id = form.get("client_id", "")
    with closing(_connect()) as conn, conn:
        row = conn.execute("SELECT * FROM oauth_tokens WHERE refresh_hash = ?", (_hash(form.get("refresh_token", "")),)).fetchone()
        if not row or row["client_id"] != client_id or row["revoked"]:
            raise OAuthError("invalid_grant", "Onbekend of ingetrokken verversingstoken")
        if row["rotated"]:
            # Een oud verversingstoken opnieuw gebruikt: iemand heeft een kopie. Trek de hele reeks in.
            conn.execute("UPDATE oauth_tokens SET revoked = 1 WHERE family = ?", (row["family"],))
            conn.commit()  # vóór de fout: anders draait de context manager het intrekken terug
            raise OAuthError("invalid_grant", "Dit verversingstoken is al gebruikt")
        if row["refresh_expires"] < _iso(_now()):
            raise OAuthError("invalid_grant", "Het verversingstoken is verlopen")
        if not _user_exists(conn, row["user_id"]) or row["epoch"] != auth.session_epoch(row["user_id"]):
            raise OAuthError("invalid_grant", "De inlog is gewijzigd; geef de app opnieuw toestemming")
        conn.execute("UPDATE oauth_tokens SET rotated = 1 WHERE id = ?", (row["id"],))
        return _new_token_row(conn, family=row["family"], user_id=row["user_id"], client_id=client_id, resource=row["resource"], epoch=row["epoch"])


def token_request(form: dict[str, str], resource_url: str) -> dict[str, Any]:
    grant = form.get("grant_type")
    if grant == "authorization_code":
        return exchange_code(form, resource_url)
    if grant == "refresh_token":
        return refresh(form)
    raise OAuthError("unsupported_grant_type", "Gebruik authorization_code of refresh_token")


def verify_access_token(token: str, resource_url: str) -> dict[str, Any] | None:
    """De app en gebruiker achter een geldig toegangstoken, of None."""
    if not token.startswith("msa_"):
        return None
    now = _iso(_now())
    with closing(_connect()) as conn, conn:
        row = conn.execute(
            """
            SELECT t.id, t.user_id, t.client_id, t.resource, t.epoch, t.access_expires, t.revoked, t.last_used_at,
                   c.client_name, u.username, u.session_epoch AS user_epoch
            FROM oauth_tokens t
            JOIN oauth_clients c ON c.client_id = t.client_id
            JOIN users u ON u.id = t.user_id
            WHERE t.access_hash = ?
            """,
            (_hash(token),),
        ).fetchone()
        if not row or row["revoked"] or row["access_expires"] < now or row["epoch"] != row["user_epoch"]:
            return None
        if not resource_ok(row["resource"], resource_url):
            return None
        if not row["last_used_at"] or row["last_used_at"][:16] != now[:16]:
            conn.execute("UPDATE oauth_tokens SET last_used_at = ? WHERE id = ?", (now, row["id"]))
    return {"client_id": row["client_id"], "name": row["client_name"], "user_id": row["user_id"], "username": row["username"]}


def revoke_token(token: str) -> None:
    """RFC 7009: geeft nooit een fout, ook niet voor een onbekend token."""
    digest = _hash(token or "")
    with closing(_connect()) as conn, conn:
        row = conn.execute("SELECT family FROM oauth_tokens WHERE access_hash = ? OR refresh_hash = ?", (digest, digest)).fetchone()
        if row:
            conn.execute("UPDATE oauth_tokens SET revoked = 1 WHERE family = ?", (row["family"],))


# --- beheer door de eigenaar ---------------------------------------------------------------------

def connected_apps(user_id: str) -> list[dict[str, Any]]:
    """Apps van deze gebruiker met een actief (niet ingetrokken, niet verlopen) verversingstoken."""
    now = _iso(_now())
    with closing(_connect()) as conn:
        rows = conn.execute(
            """
            SELECT c.client_id, c.client_name, MIN(t.created_at) AS connected_at, MAX(t.last_used_at) AS last_used_at
            FROM oauth_clients c JOIN oauth_tokens t ON t.client_id = c.client_id
            WHERE t.user_id = ? AND t.revoked = 0 AND t.rotated = 0 AND t.refresh_expires > ? AND t.epoch = ?
            GROUP BY c.client_id ORDER BY MAX(t.created_at) DESC
            """,
            (user_id, now, auth.session_epoch(user_id)),
        ).fetchall()
    return [
        {"clientId": r["client_id"], "name": r["client_name"], "connectedAt": r["connected_at"], "lastUsedAt": r["last_used_at"]}
        for r in rows
    ]


def revoke_app(user_id: str, client_id: str) -> bool:
    """Trek de toegang van een app voor deze gebruiker in. False als de gebruiker die app niet heeft."""
    with closing(_connect()) as conn, conn:
        count = conn.execute("DELETE FROM oauth_tokens WHERE user_id = ? AND client_id = ?", (user_id, str(client_id or ""))).rowcount
        conn.execute("DELETE FROM oauth_codes WHERE user_id = ? AND client_id = ?", (user_id, str(client_id or "")))
        return count > 0


def forget_user(user_id: str) -> None:
    """Wis alle codes en tokens van een gebruiker (bij het verwijderen van het account)."""
    with closing(_connect()) as conn, conn:
        conn.execute("DELETE FROM oauth_tokens WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM oauth_codes WHERE user_id = ?", (user_id,))
