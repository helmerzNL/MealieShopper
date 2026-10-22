"""Profiel van een gebruiker: displaynaam en avatar.

Inloggen blijft met de gebruikersnaam; de displaynaam is alleen wat er in het menu en in de gebruikerslijst staat.
De avatar is een kleine afbeelding (PNG, JPEG of WebP) die in de database bewaard wordt. De browser verkleint hem
vooraf; hier controleren we nog het type aan de inhoud en de grootte. SVG wordt nooit geaccepteerd.
"""
from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from typing import Any

from flask import Flask, Response, jsonify, request

from . import auth
from .i18n import tr

MAX_NAME_LENGTH = 40
MAX_AVATAR_BYTES = 256 * 1024
AVATAR_TYPES = {"image/png", "image/jpeg", "image/webp"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS user_profiles (
  user_id TEXT PRIMARY KEY,
  display_name TEXT NOT NULL DEFAULT '',
  avatar BLOB,
  avatar_type TEXT,
  avatar_version INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
);
"""


def _connect() -> sqlite3.Connection:
    conn = auth.connect()
    conn.executescript(SCHEMA)
    return conn


def clean_name(value: Any) -> str:
    """De displaynaam zonder stuurtekens en dubbele spaties, hoogstens `MAX_NAME_LENGTH` tekens."""
    text = re.sub(r"[\x00-\x1f\x7f]", " ", str(value or ""))
    return " ".join(text.split())[:MAX_NAME_LENGTH]


def sniff(data: bytes) -> str | None:
    """Het afbeeldingstype aan de inhoud; None als het geen PNG, JPEG of WebP is."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def get(user_id: str) -> dict[str, Any]:
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT display_name, avatar IS NOT NULL AS has_avatar, avatar_version FROM user_profiles WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if not row:
        return {"displayName": "", "hasAvatar": False, "avatarVersion": 0}
    return {"displayName": row["display_name"], "hasAvatar": bool(row["has_avatar"]), "avatarVersion": int(row["avatar_version"])}


def info_map() -> dict[str, dict[str, Any]]:
    """Het profiel van alle gebruikers, voor de gebruikerslijst."""
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT user_id, display_name, avatar IS NOT NULL AS has_avatar, avatar_version FROM user_profiles").fetchall()
    return {
        row["user_id"]: {"displayName": row["display_name"], "hasAvatar": bool(row["has_avatar"]), "avatarVersion": int(row["avatar_version"])}
        for row in rows
    }


def _upsert(user_id: str, **fields: Any) -> None:
    with closing(_connect()) as conn, conn:
        conn.execute(
            "INSERT INTO user_profiles (user_id, updated_at) VALUES (?, ?) ON CONFLICT(user_id) DO NOTHING",
            (user_id, auth.utcnow().isoformat()),
        )
        for column, value in fields.items():
            conn.execute(f"UPDATE user_profiles SET {column} = ?, updated_at = ? WHERE user_id = ?", (value, auth.utcnow().isoformat(), user_id))


def set_display_name(user_id: str, name: Any) -> str:
    name = clean_name(name)
    _upsert(user_id, display_name=name)
    return name


def set_avatar(user_id: str, data: bytes, content_type: str) -> None:
    if not data:
        raise ValueError(tr("Geen afbeelding ontvangen.", "No image received."))
    if len(data) > MAX_AVATAR_BYTES:
        raise ValueError(tr("De afbeelding is te groot (hoogstens 256 kB).", "The image is too large (256 kB at most)."))
    kind = sniff(data)
    if content_type not in AVATAR_TYPES or kind != content_type:
        raise ValueError(tr("Kies een PNG-, JPEG- of WebP-afbeelding.", "Choose a PNG, JPEG or WebP image."))
    _upsert(user_id, avatar=data, avatar_type=kind)
    with closing(_connect()) as conn, conn:
        conn.execute("UPDATE user_profiles SET avatar_version = avatar_version + 1 WHERE user_id = ?", (user_id,))


def clear_avatar(user_id: str) -> None:
    with closing(_connect()) as conn, conn:
        conn.execute(
            "UPDATE user_profiles SET avatar = NULL, avatar_type = NULL, avatar_version = avatar_version + 1, updated_at = ? WHERE user_id = ?",
            (auth.utcnow().isoformat(), user_id),
        )


def avatar(user_id: str) -> tuple[bytes, str] | None:
    with closing(_connect()) as conn:
        row = conn.execute("SELECT avatar, avatar_type FROM user_profiles WHERE user_id = ?", (user_id,)).fetchone()
    if not row or row["avatar"] is None or row["avatar_type"] not in AVATAR_TYPES:
        return None
    return bytes(row["avatar"]), row["avatar_type"]


def delete(user_id: str) -> None:
    with closing(_connect()) as conn, conn:
        conn.execute("DELETE FROM user_profiles WHERE user_id = ?", (user_id,))


def register_routes(app: Flask) -> None:
    def payload(user_id: str) -> dict[str, Any]:
        user = auth.current_user()
        return {"username": user["username"] if user else None, **get(user_id)}

    @app.get("/api/profile")
    def profile_get():
        return jsonify(payload(auth.acting_user_id()))

    @app.put("/api/profile")
    def profile_put():
        body = request.get_json(silent=True) or {}
        user_id = auth.acting_user_id()
        set_display_name(user_id, body.get("displayName"))
        return jsonify(payload(user_id))

    @app.post("/api/profile/avatar")
    def profile_avatar_put():
        # De afbeelding komt als gewone body met zijn eigen Content-Type: een andere website kan dat niet zonder
        # toestemming van de browser (preflight) namens jou sturen.
        if (request.content_length or 0) > MAX_AVATAR_BYTES:
            return jsonify({"error": tr("De afbeelding is te groot (hoogstens 256 kB).", "The image is too large (256 kB at most).")}), 413
        user_id = auth.acting_user_id()
        try:
            set_avatar(user_id, request.get_data(), (request.mimetype or "").lower())
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(payload(user_id))

    @app.delete("/api/profile/avatar")
    def profile_avatar_delete():
        user_id = auth.acting_user_id()
        clear_avatar(user_id)
        return jsonify(payload(user_id))

    @app.get("/api/profile/avatar/<user_id>")
    def profile_avatar_get(user_id: str):
        found = avatar(user_id)
        if not found:
            return jsonify({"error": "Not found"}), 404
        data, content_type = found
        return Response(
            data,
            mimetype=content_type,
            headers={
                "Cache-Control": "private, max-age=86400",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'",
            },
        )
