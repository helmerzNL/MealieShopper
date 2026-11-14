"""Tests voor de database- en verzoekhelpers in `mealieshopper.auth`, op aiosqlite (`auth.connect`)."""

from __future__ import annotations

import asyncio
import os
import sqlite3
import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, closing
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from quart import Quart

from mealieshopper import auth, readers, tasks
from mealieshopper.app import create_app
from helpers import db, run  # noqa: E402

OTHER_ID: str = "u-2"


@pytest.fixture(autouse=True)
def auth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inloggen staat aan en het geheim komt uit `secret.key`, niet uit de omgeving."""
    monkeypatch.setenv("PASSKEY_AUTH_ENABLED", "true")
    monkeypatch.delenv("MEALIESHOPPER_AUTH_SECRET", raising=False)
    monkeypatch.delenv("JWT_SECRET", raising=False)


@pytest.fixture
def seeded(data_dir: Path) -> Path:
    """Een database met een eigenaar en een tweede gebruiker, elk met eigen inloggegevens."""
    run(auth.init_db())
    now: str = auth.utcnow().isoformat()
    with closing(db()) as conn, conn:
        conn.execute(
            "INSERT INTO users (id, username, created_at, session_epoch) VALUES (?, ?, ?, ?)",
            (auth.OWNER_ID, "spam", now, 3),
        )
        conn.execute(
            "INSERT INTO users (id, username, created_at, session_epoch) VALUES (?, ?, ?, ?)",
            (OTHER_ID, "eggs", now, 0),
        )
        for credential_id, user_id in (
            ("pk-1", auth.OWNER_ID),
            ("pk-2", auth.OWNER_ID),
            ("pk-3", OTHER_ID),
        ):
            conn.execute(
                "INSERT INTO passkey_credentials (id, user_id, public_key, sign_count, credential_name, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (credential_id, user_id, b"key", 1, f"passkey {credential_id}", now),
            )
        conn.execute(
            "INSERT INTO password_credentials (user_id, password_hash, updated_at) VALUES (?, ?, ?)",
            (OTHER_ID, "hash", now),
        )
        conn.execute(
            "INSERT INTO mfa_totp (user_id, secret, confirmed, last_counter, created_at) VALUES (?, ?, 1, 0, ?)",
            (auth.OWNER_ID, "totp", now),
        )
        for code in ("c1", "c2"):
            conn.execute(
                "INSERT INTO mfa_recovery_codes (code_hash, user_id, created_at) VALUES (?, ?, ?)",
                (code, auth.OWNER_ID, now),
            )
    run(auth.set_setting(auth.SETTING_AUTH_MODE, "passkey"))
    run(auth.set_secret("MEALIE_API_TOKEN", "spam-token"))
    return data_dir


@asynccontextmanager
async def request_as(
    user_id: str | None, *, bearer: bool = False, path: str = "/api/recipes"
) -> AsyncIterator[None]:
    """Een verzoekcontext met (of zonder) een sessie van `user_id`, als cookie of als Bearer-token."""
    headers: dict[str, str] = {}
    if user_id:
        username: str = "spam" if user_id == auth.OWNER_ID else "eggs"
        token: str = await auth.create_token(user_id, username)
        headers = (
            {"Authorization": f"Bearer {token}"}
            if bearer
            else {"Cookie": f"{auth.SESSION_COOKIE_NAME}={token}"}
        )
    async with Quart(__name__).test_request_context(path, headers=headers):
        yield


# Lezende helpers op de database van `seeded`: (naam, argumenten, verwachte uitkomst).
READS: list[tuple[str, tuple[Any, ...], Any]] = [
    ("get_setting", (auth.SETTING_AUTH_MODE,), "passkey"),
    ("get_setting", ("ONBEKEND", "standaard"), "standaard"),
    ("auth_mode", (), "passkey"),
    ("auth_required", (), True),
    ("session_epoch", (), 3),
    ("session_epoch", (OTHER_ID,), 0),
    ("session_epoch", ("niemand",), 0),
    ("passkey_count", (), 3),
    ("passkey_count", (auth.OWNER_ID,), 2),
    ("password_configured", (), True),
    ("password_configured", (auth.OWNER_ID,), False),
    ("mfa_enabled", (), True),
    ("mfa_enabled", (OTHER_ID,), False),
    ("recovery_codes_left", (), 2),
    ("recovery_codes_left", (OTHER_ID,), 0),
    ("credentials_exist", (), True),
    ("get_secret", ("MEALIE_API_TOKEN",), "spam-token"),
    ("get_secret", ("ONBEKEND",), ""),
]


@pytest.mark.parametrize(("name", "args", "expected"), READS, ids=[f"{name}{args}" for name, args, _ in READS])
async def test_a_read_helper_gives_what_the_database_holds(seeded: Path, name: str, args: tuple[Any, ...], expected: Any) -> None:
    assert await getattr(auth, name)(*args) == expected


async def test_the_credential_rows_are_per_user_newest_first(seeded: Path) -> None:
    assert sorted(row["id"] for row in await auth.credential_rows()) == ["pk-1", "pk-2"]
    rows: list[dict[str, Any]] = await auth.credential_rows(OTHER_ID)
    assert [(row["id"], row["username"], row["credential_name"]) for row in rows] == [("pk-3", "eggs", "passkey pk-3")]


async def test_the_session_key_comes_from_the_stored_secret(seeded: Path) -> None:
    secret: str = (seeded / auth.SECRET_FILE).read_text(encoding="ascii")
    assert await auth.master_secret() == secret
    assert await auth.signing_secret() == auth.hashlib.sha256(f"mealieshopper:sessions:{secret}".encode()).hexdigest()


# Wie het verzoek doet: een eigenaar, een gewone gebruiker (cookie of Bearer-token) en een anoniem verzoek.
REQUEST_CALLERS: list[tuple[str | None, bool]] = [
    (auth.OWNER_ID, False),
    (OTHER_ID, False),
    (OTHER_ID, True),
    (None, False),
]
CALLER_IDS: list[str] = ["owner", "user", "user-bearer", "anonymous"]


@pytest.mark.parametrize(("user_id", "bearer"), REQUEST_CALLERS, ids=CALLER_IDS)
async def test_the_request_helpers_know_who_is_asking(seeded: Path, user_id: str | None, bearer: bool) -> None:
    async with request_as(user_id, bearer=bearer):
        user: dict[str, Any] | None = await auth.current_user()
        assert (user or {}).get("id") == user_id
        if user_id:
            assert user is not None
            assert (user["username"], user["role"]) == (("spam", "owner") if user_id == auth.OWNER_ID else ("eggs", "user"))
        assert await auth.acting_user_id() == (user_id or auth.OWNER_ID)
        assert await auth.is_owner() is (user_id == auth.OWNER_ID)


async def test_a_session_from_before_the_epoch_bump_is_refused(seeded: Path) -> None:
    async with request_as(OTHER_ID):
        assert (await auth.current_user() or {}).get("id") == OTHER_ID
        await auth.bump_session_epoch(OTHER_ID)
        assert await auth.current_user() is None


async def test_verify_token_reads_a_token_and_refuses_anything_else(seeded: Path) -> None:
    token: str = await auth.create_token(auth.OWNER_ID, "spam")
    assert (await auth.verify_token(token) or {})["sub"] == auth.OWNER_ID
    assert await auth.verify_token("geen-token") is None


async def test_verify_token_with_a_damaged_key_gives_none(data_dir: Path) -> None:
    """Een beschadigde `secret.key`: None in plaats van een fout."""
    (data_dir / auth.SECRET_FILE).write_text("kort", encoding="ascii")
    assert await auth.verify_token("een.token.hier") is None


# Paden voor de toegangscontrole, met de uitkomst per beller (eigenaar, gebruiker, gebruiker met Bearer, anoniem).
OPEN: None = None
UNAUTHORIZED: tuple[dict[str, str], int] = ({"error": "Je bent niet ingelogd. Log opnieuw in."}, 401)
OWNER_ONLY: tuple[dict[str, str], int] = ({"error": "Dit kan alleen de beheerder."}, 403)
ACCESS: list[tuple[str, tuple[Any, Any, Any, Any]]] = [
    ("/", (OPEN, OPEN, OPEN, OPEN)),
    ("/healthz", (OPEN, OPEN, OPEN, OPEN)),
    ("/mcp", (OPEN, OPEN, OPEN, OPEN)),
    ("/api/onboarding/status", (OPEN, OPEN, OPEN, OPEN)),
    ("/static/app.js", (OPEN, OPEN, OPEN, OPEN)),
    ("/api/auth/status", (OPEN, OPEN, OPEN, OPEN)),
    ("/api/invites/abc", (OPEN, OPEN, OPEN, OPEN)),
    ("/oauth/token", (OPEN, OPEN, OPEN, OPEN)),
    ("/.well-known/oauth-authorization-server", (OPEN, OPEN, OPEN, OPEN)),
    ("/recipes", (OPEN, OPEN, OPEN, OPEN)),
    ("/settings/backup", (OPEN, OPEN, OPEN, OPEN)),
    ("/api/users", (OPEN, OWNER_ONLY, OWNER_ONLY, UNAUTHORIZED)),
    ("/api/backup/export", (OPEN, OWNER_ONLY, OWNER_ONLY, UNAUTHORIZED)),
    ("/api/ah/auth/status", (OPEN, OPEN, OPEN, UNAUTHORIZED)),
    ("/api/recipes", (OPEN, OPEN, OPEN, UNAUTHORIZED)),
    ("/api/onboarding/finish", (OPEN, OPEN, OPEN, UNAUTHORIZED)),
]


@pytest.mark.parametrize(("path", "outcomes"), ACCESS, ids=[path for path, _ in ACCESS])
async def test_require_auth_for_request_on_every_path(seeded: Path, path: str, outcomes: tuple[Any, ...]) -> None:
    for (user_id, bearer), caller, expected in zip(REQUEST_CALLERS, CALLER_IDS, outcomes):
        async with request_as(user_id, bearer=bearer, path=path):
            assert await auth.require_auth_for_request() == expected, caller


async def test_the_allowlist_opens_a_path_and_a_prefix(seeded: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Een pad of voorvoegsel dat aan de lijst wordt toegevoegd, is openbaar."""
    async with request_as(None, path="/api/spam"):
        assert await auth.require_auth_for_request() == UNAUTHORIZED
        monkeypatch.setattr(auth, "_PUBLIC_PATHS", auth._PUBLIC_PATHS | {"/api/spam"})
        assert await auth.require_auth_for_request() is None
    async with request_as(None, path="/eggs/ham"):
        monkeypatch.setattr(auth, "_PUBLIC_PREFIXES", (*auth._PUBLIC_PREFIXES, "/eggs/"))
        assert await auth.require_auth_for_request() is None


async def test_setup_is_required_without_credentials(data_dir: Path) -> None:
    await auth.ensure_db()
    async with request_as(None):
        assert await auth.require_auth_for_request() == ({"error": "Stel eerst de beveiliging in."}, 401)


async def test_create_token_carries_the_claims(seeded: Path) -> None:
    claims: dict[str, Any] = (
        await auth.verify_token(await auth.create_token(auth.OWNER_ID, "spam")) or {}
    )
    assert (claims["sub"], claims["usr"], claims["ep"]) == (auth.OWNER_ID, "spam", 3)
    assert claims["exp"] - claims["iat"] == auth.session_max_age_seconds()


def test_a_session_lasts_ninety_days_unless_the_environment_says_otherwise(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MEALIESHOPPER_SESSION_DAYS", raising=False)
    assert auth.session_max_age_seconds() == 90 * 24 * 60 * 60
    monkeypatch.setenv("MEALIESHOPPER_SESSION_DAYS", "7")
    assert auth.session_max_age_seconds() == 7 * 24 * 60 * 60
    for wrong in ("0", "-3", "rommel", "1.5", ""):
        monkeypatch.setenv("MEALIESHOPPER_SESSION_DAYS", wrong)
        assert auth.session_max_age_seconds() == 90 * 24 * 60 * 60, wrong


async def test_secret_box_decrypts_what_it_encrypted(seeded: Path) -> None:
    token: bytes = (await auth.secret_box()).encrypt(b"spam")
    assert (await auth.secret_box()).decrypt(token) == b"spam"


async def test_auth_required_without_credentials(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert await auth.auth_required() is True
    await auth.set_setting(auth.SETTING_AUTH_MODE, "none")
    assert await auth.auth_required() is False
    monkeypatch.setenv("PASSKEY_AUTH_ENABLED", "false")
    assert await auth.auth_required() is False
    async with request_as(None):
        assert await auth.is_owner() is True
        assert await auth.require_auth_for_request() is None


async def test_set_setting_is_seen_by_get_setting(data_dir: Path) -> None:
    await auth.set_setting("WEEK_START", "monday")
    assert await auth.get_setting("WEEK_START") == "monday"
    await auth.set_setting("WEEK_START", "sunday")
    assert await auth.get_setting("WEEK_START") == "sunday"


async def test_set_secret_round_trip(data_dir: Path) -> None:
    await auth.set_secret("AH_TOKEN", "spam")
    assert await auth.get_secret("AH_TOKEN") == "spam"
    with closing(db()) as conn:
        stored: str = conn.execute(
            "SELECT value FROM app_settings WHERE key = 'AH_TOKEN'"
        ).fetchone()["value"]
    assert stored.startswith("gAAAA")  # versleuteld opgeslagen
    await auth.delete_secret("AH_TOKEN")
    assert await auth.get_secret("AH_TOKEN") == ""


async def test_purge_obsolete_secrets(data_dir: Path) -> None:
    await auth.set_secret("AH_PASSWORD", "spam")
    await auth.set_secret("JUMBO_COOKIES", "eggs")
    await auth.set_secret("MEALIE_API_TOKEN", "blijft")
    assert await auth.purge_obsolete_secrets() == 2
    assert await auth.get_secret("AH_PASSWORD") == ""
    assert await auth.get_secret("MEALIE_API_TOKEN") == "blijft"
    assert await auth.purge_obsolete_secrets() == 0


async def test_bump_session_epoch(seeded: Path) -> None:
    assert await auth.bump_session_epoch(auth.OWNER_ID) == 4
    assert await auth.session_epoch(auth.OWNER_ID) == 4
    assert await auth.session_epoch(OTHER_ID) == 0  # alleen die ene gebruiker


async def test_store_challenge_and_pop_challenge(data_dir: Path) -> None:
    await auth.store_challenge("login", b"spam")
    assert await auth.pop_challenge("login") == b"spam"
    assert await auth.pop_challenge("login") is None  # eenmalig
    await auth.store_challenge("register:owner", b"eggs")
    assert await auth.pop_challenge("register:owner") == b"eggs"
    await auth.store_challenge("register:owner", b"ham")
    assert await auth.pop_challenge("register:owner") == b"ham"


async def test_pop_challenge_ignores_an_expired_challenge(data_dir: Path) -> None:
    await auth.store_challenge("login", b"spam")
    later: Any = auth.utcnow() + auth.timedelta(minutes=6)
    with mock.patch.object(auth, "utcnow", return_value=later):
        assert await auth.pop_challenge("login") is None
    with closing(db()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM auth_challenges").fetchone()[0] == 0


async def test_ensure_db_creates_the_schema_once(data_dir: Path) -> None:
    assert not auth.db_path().exists()
    with mock.patch.object(auth, "init_db", wraps=auth.init_db) as init:
        await auth.ensure_db()
        await auth.ensure_db()
    assert init.call_count == 1
    assert auth._INITIALIZED is True
    with closing(db()) as conn:
        tables: set[str] = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert {
        "users",
        "passkey_credentials",
        "auth_challenges",
        "app_settings",
        "invites",
    } <= tables


async def test_ensure_db_after_init_db_does_nothing(data_dir: Path) -> None:
    await auth.init_db()  # zoals before_serving
    with mock.patch.object(auth, "init_db") as init:
        await auth.ensure_db()
    init.assert_not_called()


async def test_concurrent_first_requests_initialise_the_database_once(data_dir: Path) -> None:
    """Vijf eerste verzoeken tegelijk: `ensure_db` draait schema en migratie een keer, de rest wacht op het slot."""
    old_database()
    with mock.patch.object(auth, "init_db", wraps=auth.init_db) as init:
        await asyncio.gather(*(auth.ensure_db() for _ in range(5)))
    assert init.call_count == 1
    assert await auth.session_epoch(auth.OWNER_ID) == 7


def old_database() -> None:
    """Een database van voor de kolom `users.session_epoch`, met de oude teller in app_settings."""
    auth.data_dir().mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(auth.db_path())) as conn, conn:
        conn.execute(
            "CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        conn.execute("INSERT INTO users VALUES ('owner', 'spam', '2026-01-01')")
        conn.execute(
            "INSERT INTO app_settings VALUES (?, '7', '2026-01-01')",
            (auth.SETTING_SESSION_EPOCH,),
        )


async def test_init_db_migrates_an_old_database(data_dir: Path) -> None:
    old_database()
    await auth.init_db()
    assert await auth.session_epoch(auth.OWNER_ID) == 7


async def test_concurrent_init_db_migrates_once(data_dir: Path) -> None:
    """Vijf verzoeken zien tegelijk het oude schema. Alleen de eerste voegt de kolom toe, de rest wacht en ziet hem staan."""
    old_database()
    workers: int = 5
    barrier: asyncio.Barrier = asyncio.Barrier(workers)
    checked: set[asyncio.Task[Any]] = set()
    real_check: Callable[..., Awaitable[bool]] = auth._has_session_epoch

    async def check_together(conn: Any) -> bool:
        found: bool = await real_check(conn)
        task: asyncio.Task[Any] | None = asyncio.current_task()
        if task is not None and task not in checked:
            checked.add(task)
            await barrier.wait()  # pas verder als iedereen de eerste controle heeft gedaan
        return found

    with mock.patch.object(auth, "_has_session_epoch", side_effect=check_together):
        await asyncio.gather(*(auth.init_db() for _ in range(workers)))
    with closing(db()) as conn:
        columns: list[str] = [
            row["name"] for row in conn.execute("PRAGMA table_info(users)")
        ]
    assert columns.count("session_epoch") == 1
    assert await auth.session_epoch(auth.OWNER_ID) == 7


async def test_connect_rows_behave_like_sqlite3_rows(data_dir: Path) -> None:
    await auth.set_setting("WEEK_START", "monday")
    async with auth.connect() as conn:
        cursor: Any = await conn.execute("SELECT key, value FROM app_settings WHERE key = 'WEEK_START'")
        row: Any = await cursor.fetchone()
    assert isinstance(row, sqlite3.Row)
    assert (row["key"], row[0], row["value"], row[1]) == (
        "WEEK_START",
        "WEEK_START",
        "monday",
        "monday",
    )
    assert dict(row) == {"key": "WEEK_START", "value": "monday"}


async def test_connect_creates_the_data_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target: Path = tmp_path / "nieuw" / "data"
    monkeypatch.setenv("MEALIESHOPPER_DATA_DIR", str(target))
    async with auth.connect() as conn:
        await conn.execute("SELECT 1")
    assert (target / "auth.sqlite3").exists()


async def test_connect_does_not_commit_implicitly(data_dir: Path) -> None:
    await auth.ensure_db()
    async with auth.connect() as conn:
        await conn.execute(
            "INSERT INTO app_settings (key, value, updated_at) VALUES ('SPAM', 'eggs', 'nu')"
        )
    assert await auth.get_setting("SPAM") == ""
    async with auth.connect() as conn:
        await conn.execute(
            "INSERT INTO app_settings (key, value, updated_at) VALUES ('SPAM', 'eggs', 'nu')"
        )
        await conn.commit()
    assert await auth.get_setting("SPAM") == "eggs"


async def test_connect_closes_the_connection(data_dir: Path) -> None:
    async with auth.connect() as conn:
        pass
    with pytest.raises(ValueError):
        await conn.execute("SELECT 1")


async def test_master_secret_is_cached(data_dir: Path) -> None:
    secret: str = await auth.master_secret()
    assert len(secret) == 64
    assert (data_dir / auth.SECRET_FILE).read_text(encoding="ascii") == secret
    assert await auth.master_secret() == secret
    (data_dir / auth.SECRET_FILE).unlink()
    assert (
        await auth.master_secret() == secret
    )  # uit de cache, het bestand wordt niet opnieuw gelezen


async def test_master_secret_prefers_the_environment(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEALIESHOPPER_AUTH_SECRET", "  spam  ")
    assert await auth.master_secret() == "spam"
    assert not (data_dir / auth.SECRET_FILE).exists()


async def test_master_secret_waits_for_another_worker_without_blocking(
    data_dir: Path,
) -> None:
    """Een andere werker maakt `secret.key` net aan: wacht met asyncio.sleep tot de inhoud er staat. Een ticker loopt
    ondertussen door, de event loop staat dus niet stil."""
    path: Path = data_dir / auth.SECRET_FILE
    full: str = "f" * 64
    real_open: Callable[..., int] = os.open

    def other_worker_wins(target: Any, flags: int, mode: int = 0o777) -> int:
        if Path(target) == path and flags & os.O_EXCL:
            path.write_text("half", encoding="ascii")
            asyncio.get_running_loop().call_later(0.15, path.write_text, full, "ascii")
            raise FileExistsError(target)
        return real_open(target, flags, mode)

    ticks: list[int] = []

    async def ticker() -> None:
        while True:
            ticks.append(1)
            await asyncio.sleep(0.01)

    ticking: asyncio.Task[None] = asyncio.create_task(ticker())
    try:
        with mock.patch.object(auth.os, "open", side_effect=other_worker_wins):
            assert await auth.master_secret() == full
    finally:
        ticking.cancel()
    assert len(ticks) > 5, "de loop liep door tijdens het wachten"
    assert await auth.master_secret() == full


async def test_master_secret_refuses_a_damaged_file(data_dir: Path) -> None:
    (data_dir / auth.SECRET_FILE).write_text("kort", encoding="ascii")
    with pytest.raises(RuntimeError, match="onleesbaar of onvolledig"):
        await auth.master_secret()


def test_every_database_helper_is_a_coroutine_function() -> None:
    """De synchrone kant is weg: elke helper die de database of het geheim raakt, is async."""
    import inspect

    names: list[str] = sorted({name for name, _, _ in READS}) + [
        "current_user", "acting_user_id", "is_owner", "require_auth_for_request", "credential_rows", "master_secret",
        "signing_secret", "init_db", "ensure_db", "set_setting", "bump_session_epoch", "set_secret", "delete_secret",
        "purge_obsolete_secrets", "store_challenge", "pop_challenge", "create_token", "verify_token", "secret_box",
        "check_registration", "check_assertion",
    ]
    for name in names:
        assert inspect.iscoroutinefunction(getattr(auth, name)), name
    assert not hasattr(auth, "aconnect"), "geen tweelingen met een a ervoor"


async def test_two_requests_never_get_the_same_challenge(data_dir: Path) -> None:
    """`pop_challenge` leest en wist in een opdracht: van vijf gelijktijdige pogingen krijgt er een de challenge."""
    await auth.store_challenge("login", b"spam")
    found: list[bytes | None] = list(await asyncio.gather(*(auth.pop_challenge("login") for _ in range(5))))
    assert found.count(b"spam") == 1
    assert found.count(None) == 4


@pytest.mark.parametrize("check", ["check_registration", "check_assertion"])
async def test_the_passkey_check_runs_off_the_event_loop(data_dir: Path, check: str) -> None:
    """py_webauthn controleert een handtekening: dat is rekenwerk, dus in een thread, niet in de thread van de loop."""
    loop_thread: int = threading.get_ident()
    seen: list[int] = []

    def verify(*args: Any) -> Any:
        seen.append(threading.get_ident())
        return (b"id", b"key", 0) if check == "check_registration" else 1

    target: str = "_verify_registration" if check == "check_registration" else "_verify_assertion"
    arguments: tuple[Any, ...] = ({}, b"c") if check == "check_registration" else ({}, b"c", b"k")
    async with request_as(None):
        with mock.patch.object(auth, target, side_effect=verify):
            await getattr(auth, check)(*arguments)
    assert seen and seen[0] != loop_thread


def test_the_data_dir_follows_the_environment_and_is_resolved_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Taak 9c: `data_dir` zocht bij elke verbinding het pad op (een lstat per deel, op de event loop). Nu een keer per waarde van
    MEALIESHOPPER_DATA_DIR, en een andere waarde geldt meteen."""
    first: Path = tmp_path / "eerste"
    second: Path = tmp_path / "tweede"
    first.mkdir()
    second.mkdir()
    (tmp_path / "link").symlink_to(first)
    resolved: list[Path] = []
    original: Any = Path.resolve

    def counted(self: Path, strict: bool = False) -> Path:
        resolved.append(self)
        return original(self, strict)

    monkeypatch.setattr(Path, "resolve", counted)
    monkeypatch.setenv("MEALIESHOPPER_DATA_DIR", str(first))
    assert [auth.data_dir() for _ in range(3)] == [original(first)] * 3
    assert auth.db_path() == original(first) / "auth.sqlite3"
    monkeypatch.setenv("MEALIESHOPPER_DATA_DIR", str(second))
    assert auth.data_dir() == original(second)
    monkeypatch.setenv("MEALIESHOPPER_DATA_DIR", str(tmp_path / "link"))
    assert auth.data_dir() == original(first)  # zonder symlinks, zoals `resolve`
    monkeypatch.setenv("MEALIESHOPPER_DATA_DIR", str(first))
    assert auth.data_dir() == original(first)
    assert resolved == [first, second, tmp_path / "link"]


def test_connect_makes_the_data_dir_once_per_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review van taak 9c: elke `auth.connect` deed `mkdir(exist_ok=True)`, een mkdir en een stat op de event loop. Nu een keer per
    pad, en opnieuw na `forget_runtime_state` (de map kan dan weg zijn)."""
    made: list[Path] = []
    original: Any = Path.mkdir

    def counted(self: Path, *args: Any, **kwargs: Any) -> None:
        made.append(self)
        original(self, *args, **kwargs)

    async def three() -> None:
        for _ in range(3):
            async with auth.connect() as conn:
                await conn.execute("SELECT 1")

    monkeypatch.setattr(Path, "mkdir", counted)
    monkeypatch.setenv("MEALIESHOPPER_DATA_DIR", str(tmp_path / "data"))
    run(three())
    assert made == [(tmp_path / "data").resolve()]
    auth.forget_runtime_state()
    run(three())
    assert len(made) == 2


@asynccontextmanager
async def reader_threads() -> AsyncIterator[list[threading.Thread]]:
    """De threads van aiosqlite die binnen het blok starten (een per `aiosqlite.connect`)."""
    started: list[threading.Thread] = []
    original: Callable[[threading.Thread], None] = threading.Thread.start

    def counted(thread: threading.Thread) -> None:
        if "_connection_worker_thread" in thread.name:
            started.append(thread)
        original(thread)

    with mock.patch.object(threading.Thread, "start", counted):
        yield started


async def test_requests_start_at_most_a_few_reader_threads(seeded: Path) -> None:
    """Taak 10b: elke lezing van de sessie en de instellingen opende een eigen `aiosqlite.connect`, dus een thread, en op de loop
    wacht `Thread.start` tot die draait (met een drukke rekenthread 1,5 tot 6 ms per verbinding, en de verbinding met een SELECT
    32 ms). `/api/auth/status` las er zo zeven per aanvraag. Nu lenen ze een open verbinding (`auth.read`, readers.py): twintig
    aanvragen, tegelijk en na elkaar, starten hooguit `readers.READERS_KEPT` threads."""
    app: Quart = create_app()
    async with app.test_app() as test_app:
        client: Any = test_app.test_client()
        async with reader_threads() as started:
            first: list[Any] = await asyncio.gather(*(client.get("/api/auth/status") for _ in range(10)))
            later: list[Any] = [await client.get("/api/auth/status") for _ in range(10)]
    assert [response.status_code for response in first + later] == [200] * 20
    assert len(started) <= readers.READERS_KEPT, len(started)


async def test_a_pooled_read_sees_what_another_connection_committed(data_dir: Path) -> None:
    """Een verbinding gaat terug in de pool zonder open transactie: anders zag de volgende lezing de oude stand van de WAL. Een
    lener die er toch een liet openstaan, krijgt zijn verbinding dicht in plaats van terug."""
    assert await auth.get_setting("WEEK_START") == ""
    async with auth.read() as conn:
        await conn.execute("BEGIN")
        await auth.fetchone(conn, "SELECT value FROM app_settings WHERE key = ?", ("WEEK_START",))
        stale: Any = conn
    await auth.set_setting("WEEK_START", "monday")  # via `auth.connect`: een andere verbinding
    assert await auth.get_setting("WEEK_START") == "monday"
    async with auth.read() as conn:
        assert conn is not stale
        assert conn.in_transaction is False


async def test_a_pooled_read_connection_refuses_to_write(data_dir: Path) -> None:
    """`auth.read` is alleen voor lezen (`PRAGMA query_only`): een schrijfopdracht faalt luid, en die verbinding gaat dicht."""
    await auth.ensure_db()
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        async with auth.read() as conn:
            await conn.execute("INSERT INTO app_settings (key, value, updated_at) VALUES ('X', 'Y', 'Z')")
    assert await auth.get_setting("X") == ""


async def test_shutting_down_closes_the_reader_threads(data_dir: Path) -> None:
    """`tasks.cancel_all` (het afsluiten) sluit de open leesverbindingen en wacht tot hun threads stoppen."""
    await auth.ensure_db()  # het schema opent een eigen verbinding (`auth.connect`)
    async with reader_threads() as started:
        await asyncio.gather(*(auth.get_setting("WEEK_START") for _ in range(6)))
    assert 1 <= len(started) <= readers.READERS_KEPT
    await tasks.cancel_all(timeout=5)
    for thread in started:
        thread.join(5)
    assert not [thread for thread in started if thread.is_alive()]


async def test_forgetting_the_data_closes_the_reader_threads(data_dir: Path) -> None:
    """Na het wissen van de data-map (`forget_runtime_state`) gaan de open leesverbindingen dicht: ze hebben het oude bestand nog
    open. De volgende lezing opent een nieuwe."""
    await auth.ensure_db()  # het schema opent een eigen verbinding (`auth.connect`)
    async with reader_threads() as started:
        await auth.get_setting("WEEK_START")
    auth.forget_runtime_state()
    for _ in range(50):
        await asyncio.sleep(0.01)
        if not started[0].is_alive():
            break
    assert not started[0].is_alive()
    await auth.ensure_db()
    async with reader_threads() as again:
        assert await auth.get_setting("WEEK_START") == ""
    assert len(again) == 1
