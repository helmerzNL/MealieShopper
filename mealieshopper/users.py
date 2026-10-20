"""Gebruikers en uitnodigingen.

De eerste gebruiker (`owner`) is de beheerder: die koppelt Mealie en Albert Heijn en beheert de
gebruikers. Anderen komen erbij via een uitnodigingslink van de beheerder en kunnen de
basisfuncties gebruiken (weekmenu, recepten, lijstjes, mandje), maar niets koppelen of beheren.

Een uitnodiging is een geheime link die één keer werkt en na 7 dagen verloopt. De gebruiker
kiest zelf een wachtwoord of passkey, dus de beheerder kent nooit een wachtwoord. Een
"reset"-uitnodiging doet hetzelfde voor een bestaande gebruiker die niet meer kan inloggen:
de oude inloggegevens vervangen we pas als de nieuwe zijn ingesteld.
"""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
from contextlib import closing
from datetime import timedelta
from typing import Any, Callable

from flask import Flask, jsonify, request

from . import auth, oauth
from .i18n import tr

INVITE_TTL = timedelta(days=7)
MAX_USERS = 50
MAX_PENDING_INVITES = 50
OWNER_ID = auth.OWNER_ID


def _hash(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def username_taken(username: str, except_id: str | None = None) -> bool:
    auth.ensure_db()
    with closing(auth.connect()) as conn:
        rows = conn.execute("SELECT id, username FROM users").fetchall()
        pending = conn.execute("SELECT user_id, username FROM invites WHERE kind = 'new' AND expires_at >= ?", (auth.utcnow().isoformat(),)).fetchall()
    wanted = username.casefold()
    taken = any(row["username"].casefold() == wanted and row["id"] != except_id for row in rows)
    return taken or any(row["username"].casefold() == wanted and row["user_id"] != except_id for row in pending if row["username"])


def list_users() -> list[dict[str, Any]]:
    auth.ensure_db()
    with closing(auth.connect()) as conn:
        rows = conn.execute("SELECT id, username, created_at FROM users ORDER BY (id = ?) DESC, LOWER(username)", (OWNER_ID,)).fetchall()
    return [
        {
            "id": row["id"],
            "username": row["username"],
            "role": "owner" if row["id"] == OWNER_ID else "user",
            "createdAt": row["created_at"],
            "passkeys": auth.passkey_count(row["id"]),
            "password": auth.password_configured(row["id"]),
            "mfa": auth.mfa_enabled(row["id"]),
        }
        for row in rows
    ]


def get_user(user_id: str) -> dict[str, Any] | None:
    return next((user for user in list_users() if user["id"] == user_id), None)


def create_user(user_id: str, username: str) -> None:
    with closing(auth.connect()) as conn, conn:
        conn.execute(
            "INSERT INTO users (id, username, created_at) VALUES (?, ?, ?)",
            (user_id, username, auth.utcnow().isoformat()),
        )


def clear_credentials(user_id: str) -> None:
    """Verwijder alle manieren om in te loggen van een gebruiker en log die overal uit."""
    with closing(auth.connect()) as conn, conn:
        for table in ("passkey_credentials", "password_credentials", "mfa_totp", "mfa_recovery_codes"):
            conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
    auth.bump_session_epoch(user_id)


def delete_user(user_id: str) -> bool:
    """Verwijder een gewone gebruiker. De beheerder blijft altijd bestaan."""
    if user_id == OWNER_ID or not get_user(user_id):
        return False
    with closing(auth.connect()) as conn, conn:
        for table in ("passkey_credentials", "password_credentials", "mfa_totp", "mfa_recovery_codes", "invites"):
            conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    oauth.forget_user(user_id)  # ook de AI-apps van deze gebruiker
    return True


# --- uitnodigingen -------------------------------------------------------------------------------

def create_invite(created_by: str, kind: str = "new", user_id: str | None = None, username: str = "") -> dict[str, Any]:
    auth.ensure_db()
    now = auth.utcnow()
    token = secrets.token_urlsafe(32)
    invite_id = secrets.token_hex(4)
    user_id = user_id or "u_" + secrets.token_hex(8)
    with closing(auth.connect()) as conn, conn:
        conn.execute("DELETE FROM invites WHERE expires_at < ?", (now.isoformat(),))
        if kind == "reset":
            conn.execute("DELETE FROM invites WHERE user_id = ? AND kind = 'reset'", (user_id,))  # één tegelijk
        conn.execute(
            "INSERT INTO invites (id, token_hash, user_id, username, kind, created_by, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (invite_id, _hash(token), user_id, username, kind, created_by, now.isoformat(), (now + INVITE_TTL).isoformat()),
        )
    return {"id": invite_id, "token": token, "expiresAt": (now + INVITE_TTL).isoformat(), "kind": kind, "username": username}


def find_invite(token: Any) -> dict[str, Any] | None:
    """De uitnodiging bij een token, als die bestaat en niet verlopen is."""
    if not token or not isinstance(token, str):
        return None
    auth.ensure_db()
    with closing(auth.connect()) as conn:
        row = conn.execute("SELECT * FROM invites WHERE token_hash = ?", (_hash(token),)).fetchone()
        if not row or row["expires_at"] < auth.utcnow().isoformat():
            return None
        inviter = conn.execute("SELECT username FROM users WHERE id = ?", (row["created_by"],)).fetchone()
        existing = conn.execute("SELECT username FROM users WHERE id = ?", (row["user_id"],)).fetchone()
    username = existing["username"] if row["kind"] == "reset" and existing else row["username"]
    if row["kind"] == "reset" and not existing:
        return None
    return {
        "id": row["id"],
        "kind": row["kind"],
        "userId": row["user_id"],
        "username": username,
        "invitedBy": inviter["username"] if inviter else "",
        "expiresAt": row["expires_at"],
    }


def list_invites() -> list[dict[str, Any]]:
    auth.ensure_db()
    with closing(auth.connect()) as conn:
        rows = conn.execute(
            """
            SELECT i.id, i.kind, i.username, i.user_id, i.created_at, i.expires_at, u.username AS target
            FROM invites i LEFT JOIN users u ON u.id = i.user_id
            WHERE i.expires_at >= ? ORDER BY i.created_at DESC
            """,
            (auth.utcnow().isoformat(),),
        ).fetchall()
    return [
        {"id": r["id"], "kind": r["kind"], "username": (r["target"] if r["kind"] == "reset" else r["username"]) or "", "createdAt": r["created_at"], "expiresAt": r["expires_at"]}
        for r in rows
    ]


def revoke_invite(invite_id: str) -> bool:
    with closing(auth.connect()) as conn, conn:
        return conn.execute("DELETE FROM invites WHERE id = ?", (str(invite_id or ""),)).rowcount > 0


def consume_invite(invite_id: str) -> None:
    revoke_invite(invite_id)


class UsernameTaken(Exception):
    pass


def register_account(invite: dict[str, Any], username: str) -> str:
    """Zet het account van een uitnodiging klaar vlak voor de eerste inloggegevens. Geeft het gebruikers-id."""
    user_id = invite["userId"]
    if invite["kind"] == "reset":
        clear_credentials(user_id)
    else:
        try:
            create_user(user_id, username)
        except sqlite3.IntegrityError:  # twee uitnodigingen met dezelfde naam tegelijk
            raise UsernameTaken(username) from None
    return user_id


def check_new_username(username: str, invite: dict[str, Any]) -> str | None:
    """Een foutmelding als de naam niet kan, anders None."""
    from .auth_password import validate_username

    username, problem = validate_username(username)
    if problem:
        return problem
    if username_taken(username, except_id=invite["userId"]):
        return tr("Deze naam is al in gebruik. Kies een andere.", "This name is already taken. Choose another one.")
    return None


# --- routes --------------------------------------------------------------------------------------

def register_routes(app: Flask, base_url: Callable[[], str]) -> None:
    def invite_payload(created: dict[str, Any]) -> dict[str, Any]:
        return {**created, "url": f"{base_url()}/#invite={created['token']}"}

    @app.get("/api/users")
    def users_list():
        return jsonify({"users": list_users(), "invites": list_invites(), "maxUsers": MAX_USERS})

    @app.post("/api/users/invites")
    def users_invite():
        body = request.get_json(silent=True) or {}
        username = str(body.get("username") or "").strip()
        if username:
            from .auth_password import validate_username

            username, problem = validate_username(username)
            if problem:
                return jsonify({"error": problem}), 400
            if username_taken(username):
                return jsonify({"error": tr("Deze naam is al in gebruik.", "This name is already taken.")}), 409
        if len(list_users()) + len([i for i in list_invites() if i["kind"] == "new"]) >= MAX_USERS:
            return jsonify({"error": tr("Het maximum aantal gebruikers is bereikt.", "The maximum number of users has been reached.")}), 400
        if len(list_invites()) >= MAX_PENDING_INVITES:
            return jsonify({"error": tr("Er staan te veel uitnodigingen open.", "Too many invitations are open.")}), 400
        created = create_invite(auth.acting_user_id(), "new", username=username)
        return jsonify(invite_payload(created)), 201

    @app.post("/api/users/<user_id>/reset")
    def users_reset(user_id: str):
        user = get_user(user_id)
        if not user or user_id == OWNER_ID:
            return jsonify({"error": tr("Gebruiker niet gevonden.", "User not found.")}), 404
        created = create_invite(auth.acting_user_id(), "reset", user_id=user_id)
        return jsonify(invite_payload(created)), 201

    @app.delete("/api/users/invites/<invite_id>")
    def users_invite_revoke(invite_id: str):
        if not revoke_invite(invite_id):
            return jsonify({"error": tr("Uitnodiging niet gevonden.", "Invitation not found.")}), 404
        return jsonify({"status": "ok"})

    @app.delete("/api/users/<user_id>")
    def users_delete(user_id: str):
        if user_id == OWNER_ID:
            return jsonify({"error": tr("De beheerder kan niet worden verwijderd.", "The administrator cannot be removed.")}), 400
        if not delete_user(user_id):
            return jsonify({"error": tr("Gebruiker niet gevonden.", "User not found.")}), 404
        return jsonify({"status": "ok"})

    # Publiek: wat iemand met een uitnodigingslink nodig heeft. Het token zelf is het geheim.
    @app.get("/api/invites/info")
    def invite_info():
        invite = find_invite(request.headers.get("X-Invite-Token") or request.args.get("token"))
        if not invite:
            return jsonify({"valid": False, "error": tr("Deze uitnodiging bestaat niet meer of is verlopen.", "This invitation no longer exists or has expired.")}), 404
        return jsonify({"valid": True, "kind": invite["kind"], "username": invite["username"], "invitedBy": invite["invitedBy"], "expiresAt": invite["expiresAt"]})

    @app.post("/api/invites/password")
    def invite_password():
        from . import auth_password

        body = request.get_json(silent=True) or {}
        invite = find_invite(body.get("token"))
        if not invite:
            return jsonify({"error": tr("Deze uitnodiging bestaat niet meer of is verlopen.", "This invitation no longer exists or has expired.")}), 404
        username = invite["username"] if invite["kind"] == "reset" else str(body.get("username") or invite["username"]).strip()
        problem = None if invite["kind"] == "reset" else check_new_username(username, invite)
        problem = problem or auth_password.validate_password(body.get("password"), username)
        if problem:
            return jsonify({"error": problem}), 400
        try:
            user_id = register_account(invite, username)
        except UsernameTaken:
            return jsonify({"error": tr("Deze naam is al in gebruik. Kies een andere.", "This name is already taken. Choose another one.")}), 409
        auth_password.store_password(user_id, username, str(body["password"]))
        consume_invite(invite["id"])
        return auth_password.session_response(user_id)
