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
from datetime import timedelta
from typing import Any, Awaitable, Callable

from quart import Quart, jsonify, request

from . import auth, oauth, profile
from .i18n import tr

INVITE_TTL = timedelta(days=7)
MAX_USERS = 50
MAX_PENDING_INVITES = 50
OWNER_ID = auth.OWNER_ID


def _hash(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


async def username_taken(username: str, except_id: str | None = None) -> bool:
    await auth.ensure_db()
    async with auth.connect() as conn:
        async with conn.execute("SELECT id, username FROM users") as cursor:
            rows = await cursor.fetchall()
        async with conn.execute("SELECT user_id, username FROM invites WHERE kind = 'new' AND expires_at >= ?", (auth.utcnow().isoformat(),)) as cursor:
            pending = await cursor.fetchall()
    wanted = username.casefold()
    taken = any(row["username"].casefold() == wanted and row["id"] != except_id for row in rows)
    return taken or any(row["username"].casefold() == wanted and row["user_id"] != except_id for row in pending if row["username"])


async def list_users() -> list[dict[str, Any]]:
    await auth.ensure_db()
    async with auth.connect() as conn, conn.execute(
        "SELECT id, username, created_at FROM users ORDER BY (id = ?) DESC, LOWER(username)", (OWNER_ID,)
    ) as cursor:
        rows = await cursor.fetchall()
    profiles = await profile.info_map()
    return [
        {
            "id": row["id"],
            "username": row["username"],
            **profiles.get(row["id"], {"displayName": "", "hasAvatar": False, "avatarVersion": 0}),
            "role": "owner" if row["id"] == OWNER_ID else "user",
            "createdAt": row["created_at"],
            "passkeys": await auth.passkey_count(row["id"]),
            "password": await auth.password_configured(row["id"]),
            "mfa": await auth.mfa_enabled(row["id"]),
        }
        for row in rows
    ]


async def get_user(user_id: str) -> dict[str, Any] | None:
    return next((user for user in await list_users() if user["id"] == user_id), None)


async def create_user(user_id: str, username: str) -> None:
    async with auth.connect() as conn:
        await conn.execute(
            "INSERT INTO users (id, username, created_at) VALUES (?, ?, ?)",
            (user_id, username, auth.utcnow().isoformat()),
        )
        await conn.commit()


async def clear_credentials(user_id: str) -> None:
    """Verwijder alle manieren om in te loggen van een gebruiker en log die overal uit."""
    async with auth.connect() as conn:
        for table in ("passkey_credentials", "password_credentials", "mfa_totp", "mfa_recovery_codes"):
            await conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
        await conn.commit()
    await auth.bump_session_epoch(user_id)


async def delete_user(user_id: str) -> bool:
    """Verwijder een gewone gebruiker. De beheerder blijft altijd bestaan."""
    if user_id == OWNER_ID or not await get_user(user_id):
        return False
    async with auth.connect() as conn:
        for table in ("passkey_credentials", "password_credentials", "mfa_totp", "mfa_recovery_codes", "invites"):
            await conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
        await conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        await conn.commit()
    await profile.delete(user_id)
    await oauth.forget_user(user_id)  # ook de AI-apps van deze gebruiker
    return True


# --- uitnodigingen -------------------------------------------------------------------------------

async def create_invite(created_by: str, kind: str = "new", user_id: str | None = None, username: str = "") -> dict[str, Any]:
    await auth.ensure_db()
    now = auth.utcnow()
    token = secrets.token_urlsafe(32)
    invite_id = secrets.token_hex(4)
    user_id = user_id or "u_" + secrets.token_hex(8)
    async with auth.connect() as conn:
        await conn.execute("DELETE FROM invites WHERE expires_at < ?", (now.isoformat(),))
        if kind == "reset":
            await conn.execute("DELETE FROM invites WHERE user_id = ? AND kind = 'reset'", (user_id,))  # één tegelijk
        await conn.execute(
            "INSERT INTO invites (id, token_hash, user_id, username, kind, created_by, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (invite_id, _hash(token), user_id, username, kind, created_by, now.isoformat(), (now + INVITE_TTL).isoformat()),
        )
        await conn.commit()
    return {"id": invite_id, "token": token, "expiresAt": (now + INVITE_TTL).isoformat(), "kind": kind, "username": username}


async def find_invite(token: Any) -> dict[str, Any] | None:
    """De uitnodiging bij een token, als die bestaat en niet verlopen is."""
    if not token or not isinstance(token, str):
        return None
    await auth.ensure_db()
    async with auth.connect() as conn:
        row = await auth.fetchone(conn, "SELECT * FROM invites WHERE token_hash = ?", (_hash(token),))
        if not row or row["expires_at"] < auth.utcnow().isoformat():
            return None
        inviter = await auth.fetchone(conn, "SELECT username FROM users WHERE id = ?", (row["created_by"],))
        existing = await auth.fetchone(conn, "SELECT username FROM users WHERE id = ?", (row["user_id"],))
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


async def list_invites() -> list[dict[str, Any]]:
    await auth.ensure_db()
    async with auth.connect() as conn, conn.execute(
            """
            SELECT i.id, i.kind, i.username, i.user_id, i.created_at, i.expires_at, u.username AS target
            FROM invites i LEFT JOIN users u ON u.id = i.user_id
            WHERE i.expires_at >= ? ORDER BY i.created_at DESC
            """,
            (auth.utcnow().isoformat(),),
        ) as cursor:
        rows = await cursor.fetchall()
    return [
        {"id": r["id"], "kind": r["kind"], "username": (r["target"] if r["kind"] == "reset" else r["username"]) or "", "createdAt": r["created_at"], "expiresAt": r["expires_at"]}
        for r in rows
    ]


async def revoke_invite(invite_id: str) -> bool:
    async with auth.connect() as conn:
        cursor = await conn.execute("DELETE FROM invites WHERE id = ?", (str(invite_id or ""),))
        await conn.commit()
        return cursor.rowcount > 0


async def consume_invite(invite_id: str) -> None:
    await revoke_invite(invite_id)


class UsernameTaken(Exception):
    pass


async def register_account(invite: dict[str, Any], username: str) -> str:
    """Zet het account van een uitnodiging klaar vlak voor de eerste inloggegevens. Geeft het gebruikers-id."""
    user_id = invite["userId"]
    if invite["kind"] == "reset":
        await clear_credentials(user_id)
    else:
        try:
            await create_user(user_id, username)
        except sqlite3.IntegrityError:  # twee uitnodigingen met dezelfde naam tegelijk
            raise UsernameTaken(username) from None
    return user_id


async def check_new_username(username: str, invite: dict[str, Any]) -> str | None:
    """Een foutmelding als de naam niet kan, anders None."""
    from .auth_password import validate_username

    username, problem = validate_username(username)
    if problem:
        return problem
    if await username_taken(username, except_id=invite["userId"]):
        return tr("Deze naam is al in gebruik. Kies een andere.", "This name is already taken. Choose another one.")
    return None


# --- routes --------------------------------------------------------------------------------------

def register_routes(app: Quart, base_url: Callable[[], Awaitable[str]]) -> None:
    async def invite_payload(created: dict[str, Any]) -> dict[str, Any]:
        return {**created, "url": f"{await base_url()}/#invite={created['token']}"}

    @app.get("/api/users")
    async def users_list():
        return jsonify({"users": await list_users(), "invites": await list_invites(), "maxUsers": MAX_USERS})

    @app.post("/api/users/invites")
    async def users_invite():
        body = await request.get_json(silent=True) or {}
        username = str(body.get("username") or "").strip()
        if username:
            from .auth_password import validate_username

            username, problem = validate_username(username)
            if problem:
                return jsonify({"error": problem}), 400
            if await username_taken(username):
                return jsonify({"error": tr("Deze naam is al in gebruik.", "This name is already taken.")}), 409
        if len(await list_users()) + len([i for i in await list_invites() if i["kind"] == "new"]) >= MAX_USERS:
            return jsonify({"error": tr("Het maximum aantal gebruikers is bereikt.", "The maximum number of users has been reached.")}), 400
        if len(await list_invites()) >= MAX_PENDING_INVITES:
            return jsonify({"error": tr("Er staan te veel uitnodigingen open.", "Too many invitations are open.")}), 400
        created = await create_invite(await auth.acting_user_id(), "new", username=username)
        return jsonify(await invite_payload(created)), 201

    @app.post("/api/users/<user_id>/reset")
    async def users_reset(user_id: str):
        user = await get_user(user_id)
        if not user or user_id == OWNER_ID:
            return jsonify({"error": tr("Gebruiker niet gevonden.", "User not found.")}), 404
        created = await create_invite(await auth.acting_user_id(), "reset", user_id=user_id)
        return jsonify(await invite_payload(created)), 201

    @app.delete("/api/users/invites/<invite_id>")
    async def users_invite_revoke(invite_id: str):
        if not await revoke_invite(invite_id):
            return jsonify({"error": tr("Uitnodiging niet gevonden.", "Invitation not found.")}), 404
        return jsonify({"status": "ok"})

    @app.delete("/api/users/<user_id>")
    async def users_delete(user_id: str):
        if user_id == OWNER_ID:
            return jsonify({"error": tr("De beheerder kan niet worden verwijderd.", "The administrator cannot be removed.")}), 400
        if not await delete_user(user_id):
            return jsonify({"error": tr("Gebruiker niet gevonden.", "User not found.")}), 404
        return jsonify({"status": "ok"})

    # Publiek: wat iemand met een uitnodigingslink nodig heeft. Het token zelf is het geheim.
    @app.get("/api/invites/info")
    async def invite_info():
        invite = await find_invite(request.headers.get("X-Invite-Token") or request.args.get("token"))
        if not invite:
            return jsonify({"valid": False, "error": tr("Deze uitnodiging bestaat niet meer of is verlopen.", "This invitation no longer exists or has expired.")}), 404
        return jsonify({"valid": True, "kind": invite["kind"], "username": invite["username"], "invitedBy": invite["invitedBy"], "expiresAt": invite["expiresAt"]})

    @app.post("/api/invites/password")
    async def invite_password():
        from . import auth_password

        body = await request.get_json(silent=True) or {}
        invite = await find_invite(body.get("token"))
        if not invite:
            return jsonify({"error": tr("Deze uitnodiging bestaat niet meer of is verlopen.", "This invitation no longer exists or has expired.")}), 404
        username = invite["username"] if invite["kind"] == "reset" else str(body.get("username") or invite["username"]).strip()
        problem = None if invite["kind"] == "reset" else await check_new_username(username, invite)
        problem = problem or auth_password.validate_password(body.get("password"), username)
        if problem:
            return jsonify({"error": problem}), 400
        try:
            user_id = await register_account(invite, username)
        except UsernameTaken:
            return jsonify({"error": tr("Deze naam is al in gebruik. Kies een andere.", "This name is already taken. Choose another one.")}), 409
        await auth_password.store_password(user_id, username, str(body["password"]))
        await consume_invite(invite["id"])
        return await auth_password.session_response(user_id)
