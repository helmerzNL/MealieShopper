"""Profiel van een gebruiker: displaynaam, avatar en eigen voorkeuren (*Liever biologisch*, #218, en de weergave).

Inloggen blijft met de gebruikersnaam; de displaynaam is alleen wat er in het menu en in de gebruikerslijst staat.
De voorkeurssupermarkt (Instellingen, Voorkeuren) is per gebruiker: Albert Heijn, Jumbo, Picnic of Crisp, waarmee Aanbiedingen
en Winkels beginnen zodra die winkel gekoppeld is. De weergave (Instellingen, Voorkeuren) is ook per gebruiker: licht, donker of het apparaat volgen, de achtergrond van het
donkere thema (groen of donkergrijs), de accentkleur en de navigatiebalk onderaan (iOS- of Android-achtig, en hoe
doorzichtig het glas is), en of de kookmodus bij een recept aan staat. De browser leidt uit de accentkleur de tinten af die de
contrastregels halen (static/theme.js); hier bewaren we alleen de keuze, en een waarde die niet klopt telt als geen keuze.
De avatar is een kleine afbeelding (PNG, JPEG of WebP) die in de database bewaard wordt. De browser verkleint hem
vooraf; hier controleren we nog het type aan de inhoud en de grootte. SVG wordt nooit geaccepteerd.
"""
from __future__ import annotations

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import aiosqlite

from quart import Quart, Response, jsonify, request

from . import auth
from .i18n import tr

MAX_NAME_LENGTH = 40
MAX_AVATAR_BYTES = 256 * 1024
AVATAR_TYPES = {"image/png", "image/jpeg", "image/webp"}
THEMES = ("system", "light", "dark")
DARK_BACKGROUNDS = ("green", "grey")
ACCENT = re.compile(r"^#[0-9a-f]{6}$")
NAVBAR_STYLES = ("auto", "ios", "android")  # auto: de balk die bij het toestel hoort, ook op een volgend toestel
NAVBAR_GLASS_DEFAULT = 60  # 0 is dicht, 100 het doorzichtigst (static/theme.js rekent het om)
PREFERRED_STORES = ("ah", "jumbo", "picnic", "crisp")  # de eerste is de standaard: Albert Heijn

SCHEMA = """
CREATE TABLE IF NOT EXISTS user_profiles (
  user_id TEXT PRIMARY KEY,
  display_name TEXT NOT NULL DEFAULT '',
  avatar BLOB,
  avatar_type TEXT,
  avatar_version INTEGER NOT NULL DEFAULT 0,
  prefer_organic INTEGER NOT NULL DEFAULT 0,
  theme TEXT,
  dark_background TEXT NOT NULL DEFAULT 'green',
  accent TEXT NOT NULL DEFAULT '',
  navbar_style TEXT NOT NULL DEFAULT 'auto',
  navbar_glass INTEGER NOT NULL DEFAULT 60,
  cooking_mode INTEGER NOT NULL DEFAULT 1,
  preferred_store TEXT NOT NULL DEFAULT 'ah',
  planner_combine INTEGER NOT NULL DEFAULT 1,
  planner_split_meals INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL
);
"""

# Kolommen die later bij user_profiles kwamen: een oudere database krijgt ze bij het openen.
_ADDED_COLUMNS = {
    "prefer_organic": "INTEGER NOT NULL DEFAULT 0",  # *Liever biologisch* (#218)
    "theme": "TEXT",  # weergave: NULL is "nog niet gekozen", dan beslist de browser zoals voorheen
    "dark_background": "TEXT NOT NULL DEFAULT 'green'",
    "accent": "TEXT NOT NULL DEFAULT ''",  # leeg is het basilicumgroen van de huisstijl
    "navbar_style": "TEXT NOT NULL DEFAULT 'auto'",  # de navigatiebalk onderaan
    "navbar_glass": "INTEGER NOT NULL DEFAULT 60",
    "cooking_mode": "INTEGER NOT NULL DEFAULT 1",  # Start met koken en het afvinken bij een recept (3.14.0); standaard aan
    "preferred_store": "TEXT NOT NULL DEFAULT 'ah'",  # de voorkeurssupermarkt (3.20.0); standaard Albert Heijn
    # De weekplanner van de AH-app (4.3.0): losse producten van een dag samen als één menu, en soep en pizza dan toch los.
    "planner_combine": "INTEGER NOT NULL DEFAULT 1",
    "planner_split_meals": "INTEGER NOT NULL DEFAULT 1",
}


@asynccontextmanager
async def _connect() -> AsyncIterator[aiosqlite.Connection]:
    """Een verbinding met het schema en de kolommen die later kwamen. Sluit zonder commit, zoals `auth.connect`."""
    async with auth.connect() as conn:
        await conn.executescript(SCHEMA)
        columns = {row["name"] for row in await conn.execute_fetchall("PRAGMA table_info(user_profiles)")}
        for name, definition in _ADDED_COLUMNS.items():
            if name not in columns:
                try:
                    await conn.execute(f"ALTER TABLE user_profiles ADD COLUMN {name} {definition}")
                except aiosqlite.OperationalError as exc:  # een andere verbinding was ons net voor
                    if "duplicate column" not in str(exc):
                        raise
        await conn.commit()
        yield conn


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


def clean_theme(value: Any) -> str | None:
    return value if value in THEMES else None


def clean_dark_background(value: Any) -> str:
    return value if value in DARK_BACKGROUNDS else DARK_BACKGROUNDS[0]


def clean_accent(value: Any) -> str:
    """'#rrggbb' in kleine letters, of '' (de huisstijl). Een waarde die niet klopt (een oude of handmatig gewijzigde
    back-up) mag de pagina nooit breken, dus die telt als geen keuze."""
    text = str(value or "").strip().lower()
    return text if ACCENT.match(text) else ""


def clean_navbar_style(value: Any) -> str:
    return value if value in NAVBAR_STYLES else NAVBAR_STYLES[0]


def valid_glass(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 100  # True is in Python ook een int


def clean_navbar_glass(value: Any) -> int:
    return value if valid_glass(value) else NAVBAR_GLASS_DEFAULT


def clean_preferred_store(value: Any) -> str:
    return value if value in PREFERRED_STORES else PREFERRED_STORES[0]


async def get(user_id: str) -> dict[str, Any]:
    async with _connect() as conn:
        row = await auth.fetchone(
            conn,
            "SELECT display_name, avatar IS NOT NULL AS has_avatar, avatar_version, prefer_organic, theme, dark_background, accent, "
            "navbar_style, navbar_glass, cooking_mode, preferred_store, planner_combine, planner_split_meals "
            "FROM user_profiles WHERE user_id = ?",
            (user_id,),
        )
    if not row:
        return {
            "displayName": "", "hasAvatar": False, "avatarVersion": 0, "preferOrganic": False,
            "theme": None, "darkBackground": DARK_BACKGROUNDS[0], "accent": "",
            "navbarStyle": NAVBAR_STYLES[0], "navbarGlass": NAVBAR_GLASS_DEFAULT, "cookingMode": True,
            "preferredStore": PREFERRED_STORES[0], "plannerCombine": True, "plannerSplitMeals": True,
        }
    return {
        "displayName": row["display_name"],
        "hasAvatar": bool(row["has_avatar"]),
        "avatarVersion": int(row["avatar_version"]),
        "preferOrganic": bool(row["prefer_organic"]),
        "theme": clean_theme(row["theme"]),
        "darkBackground": clean_dark_background(row["dark_background"]),
        "accent": clean_accent(row["accent"]),
        "navbarStyle": clean_navbar_style(row["navbar_style"]),
        "navbarGlass": clean_navbar_glass(row["navbar_glass"]),
        "cookingMode": row["cooking_mode"] != 0,  # alleen een echte 0 zet hem uit
        "preferredStore": clean_preferred_store(row["preferred_store"]),
        "plannerCombine": row["planner_combine"] != 0,  # alleen een echte 0 zet hem uit
        "plannerSplitMeals": row["planner_split_meals"] != 0,
    }


async def info_map() -> dict[str, dict[str, Any]]:
    """Het profiel van alle gebruikers, voor de gebruikerslijst."""
    async with _connect() as conn:
        rows = await conn.execute_fetchall("SELECT user_id, display_name, avatar IS NOT NULL AS has_avatar, avatar_version FROM user_profiles")
    return {
        row["user_id"]: {"displayName": row["display_name"], "hasAvatar": bool(row["has_avatar"]), "avatarVersion": int(row["avatar_version"])}
        for row in rows
    }


async def _upsert(user_id: str, **fields: Any) -> None:
    async with _connect() as conn:
        await conn.execute(
            "INSERT INTO user_profiles (user_id, updated_at) VALUES (?, ?) ON CONFLICT(user_id) DO NOTHING",
            (user_id, auth.utcnow().isoformat()),
        )
        for column, value in fields.items():
            await conn.execute(f"UPDATE user_profiles SET {column} = ?, updated_at = ? WHERE user_id = ?", (value, auth.utcnow().isoformat(), user_id))
        await conn.commit()


async def set_display_name(user_id: str, name: Any) -> str:
    name = clean_name(name)
    await _upsert(user_id, display_name=name)
    return name


async def set_prefer_organic(user_id: str, value: bool) -> None:
    """*Liever biologisch* (#218): stelt de controlelijst van het weekmenu eerst een biologisch product voor."""
    await _upsert(user_id, prefer_organic=1 if value else 0)


async def set_appearance(
    user_id: str,
    *,
    theme: str | None = None,
    dark_background: str | None = None,
    accent: str | None = None,
    navbar_style: str | None = None,
    navbar_glass: int | None = None,
    cooking_mode: bool | None = None,
    preferred_store: str | None = None,
    planner_combine: bool | None = None,
    planner_split_meals: bool | None = None,
) -> None:
    """De weergave en de voorkeurssupermarkt van één gebruiker; alleen wat is meegegeven verandert. De waarden zijn al
    gecontroleerd (zie de route)."""
    given = {
        "theme": theme,
        "dark_background": dark_background,
        "accent": accent,
        "navbar_style": navbar_style,
        "navbar_glass": navbar_glass,
        "cooking_mode": None if cooking_mode is None else int(bool(cooking_mode)),
        "preferred_store": preferred_store,
        "planner_combine": None if planner_combine is None else int(bool(planner_combine)),
        "planner_split_meals": None if planner_split_meals is None else int(bool(planner_split_meals)),
    }
    fields = {column: value for column, value in given.items() if value is not None}
    if fields:
        await _upsert(user_id, **fields)


def parse_preferences(body: dict[str, Any]) -> dict[str, Any]:
    """De voorkeuren uit een verzoek, gecontroleerd. Geeft ValueError met een uitleg als er iets niet klopt."""
    wanted: dict[str, Any] = {}
    if "preferOrganic" in body:
        if not isinstance(body["preferOrganic"], bool):
            raise ValueError(tr("preferOrganic moet waar of onwaar zijn.", "preferOrganic must be true or false."))
        wanted["prefer_organic"] = body["preferOrganic"]
    if "theme" in body:
        if body["theme"] not in THEMES:
            raise ValueError(tr("theme moet system, light of dark zijn.", "theme must be system, light or dark."))
        wanted["theme"] = body["theme"]
    if "darkBackground" in body:
        if body["darkBackground"] not in DARK_BACKGROUNDS:
            raise ValueError(tr("darkBackground moet green of grey zijn.", "darkBackground must be green or grey."))
        wanted["dark_background"] = body["darkBackground"]
    if "accent" in body:
        accent = body["accent"]
        if not isinstance(accent, str) or (accent and not ACCENT.match(accent.lower())):
            raise ValueError(tr("accent moet een kleur als #1a2b3c zijn, of leeg.", "accent must be a colour like #1a2b3c, or empty."))
        wanted["accent"] = accent.lower()
    if "navbarStyle" in body:
        if body["navbarStyle"] not in NAVBAR_STYLES:
            raise ValueError(tr("navbarStyle moet auto, ios of android zijn.", "navbarStyle must be auto, ios or android."))
        wanted["navbar_style"] = body["navbarStyle"]
    if "navbarGlass" in body:
        if not valid_glass(body["navbarGlass"]):
            raise ValueError(tr("navbarGlass moet een geheel getal van 0 tot en met 100 zijn.", "navbarGlass must be a whole number from 0 to 100."))
        wanted["navbar_glass"] = body["navbarGlass"]
    if "cookingMode" in body:
        if not isinstance(body["cookingMode"], bool):
            raise ValueError(tr("cookingMode moet waar of onwaar zijn.", "cookingMode must be true or false."))
        wanted["cooking_mode"] = body["cookingMode"]
    if "preferredStore" in body:
        if body["preferredStore"] not in PREFERRED_STORES:
            raise ValueError(tr("preferredStore moet ah, jumbo, picnic of crisp zijn.", "preferredStore must be ah, jumbo, picnic or crisp."))
        wanted["preferred_store"] = body["preferredStore"]
    # De weekplanner van de AH-app: hoe Naar weekmenu losse producten in Mealie zet (zie ah_planner.py).
    for key, column in (("plannerCombine", "planner_combine"), ("plannerSplitMeals", "planner_split_meals")):
        if key in body:
            if not isinstance(body[key], bool):
                raise ValueError(tr(f"{key} moet waar of onwaar zijn.", f"{key} must be true or false."))
            wanted[column] = body[key]
    if not wanted:
        raise ValueError(tr("Geen voorkeur ontvangen.", "No preference received."))
    return wanted


async def set_avatar(user_id: str, data: bytes, content_type: str) -> None:
    if not data:
        raise ValueError(tr("Geen afbeelding ontvangen.", "No image received."))
    if len(data) > MAX_AVATAR_BYTES:
        raise ValueError(tr("De afbeelding is te groot (hoogstens 256 kB).", "The image is too large (256 kB at most)."))
    kind = sniff(data)
    if content_type not in AVATAR_TYPES or kind != content_type:
        raise ValueError(tr("Kies een PNG-, JPEG- of WebP-afbeelding.", "Choose a PNG, JPEG or WebP image."))
    await _upsert(user_id, avatar=data, avatar_type=kind)
    async with _connect() as conn:
        await conn.execute("UPDATE user_profiles SET avatar_version = avatar_version + 1 WHERE user_id = ?", (user_id,))
        await conn.commit()


async def clear_avatar(user_id: str) -> None:
    async with _connect() as conn:
        await conn.execute(
            "UPDATE user_profiles SET avatar = NULL, avatar_type = NULL, avatar_version = avatar_version + 1, updated_at = ? WHERE user_id = ?",
            (auth.utcnow().isoformat(), user_id),
        )
        await conn.commit()


async def avatar(user_id: str) -> tuple[bytes, str] | None:
    async with _connect() as conn:
        row = await auth.fetchone(conn, "SELECT avatar, avatar_type FROM user_profiles WHERE user_id = ?", (user_id,))
    if not row or row["avatar"] is None or row["avatar_type"] not in AVATAR_TYPES:
        return None
    return bytes(row["avatar"]), row["avatar_type"]


async def delete(user_id: str) -> None:
    async with _connect() as conn:
        await conn.execute("DELETE FROM user_profiles WHERE user_id = ?", (user_id,))
        await conn.commit()


def register_routes(app: Quart) -> None:
    async def payload(user_id: str) -> dict[str, Any]:
        user = await auth.current_user()
        return {"username": user["username"] if user else None, **await get(user_id)}

    @app.get("/api/profile")
    async def profile_get():
        return jsonify(await payload(await auth.acting_user_id()))

    @app.put("/api/profile")
    async def profile_put():
        body = await request.get_json(silent=True) or {}
        user_id = await auth.acting_user_id()
        await set_display_name(user_id, body.get("displayName"))
        return jsonify(await payload(user_id))

    @app.put("/api/profile/preferences")
    async def profile_preferences_put():
        # Los van PUT /api/profile, zodat een schakelaar de displaynaam niet meestuurt (en dus niet leegmaakt).
        # Alleen wat in het verzoek staat verandert: *Liever biologisch*, de weergave (theme, darkBackground, accent,
        # navbarStyle, navbarGlass, cookingMode), de voorkeurssupermarkt (preferredStore) en de AH-weekplanner
        # (plannerCombine, plannerSplitMeals).
        body = await request.get_json(silent=True)
        if not isinstance(body, dict):
            body = {}
        try:
            wanted = parse_preferences(body)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        user_id = await auth.acting_user_id()
        if "prefer_organic" in wanted:
            await set_prefer_organic(user_id, wanted.pop("prefer_organic"))
        await set_appearance(user_id, **wanted)
        return jsonify(await payload(user_id))

    @app.post("/api/profile/avatar")
    async def profile_avatar_put():
        # De afbeelding komt als gewone body met zijn eigen Content-Type: een andere website kan dat niet zonder
        # toestemming van de browser (preflight) namens jou sturen.
        if (request.content_length or 0) > MAX_AVATAR_BYTES:
            return jsonify({"error": tr("De afbeelding is te groot (hoogstens 256 kB).", "The image is too large (256 kB at most).")}), 413
        user_id = await auth.acting_user_id()
        try:
            await set_avatar(user_id, await request.get_data(), (request.mimetype or "").lower())
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(await payload(user_id))

    @app.delete("/api/profile/avatar")
    async def profile_avatar_delete():
        user_id = await auth.acting_user_id()
        await clear_avatar(user_id)
        return jsonify(await payload(user_id))

    @app.get("/api/profile/avatar/<user_id>")
    async def profile_avatar_get(user_id: str):
        found = await avatar(user_id)
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
