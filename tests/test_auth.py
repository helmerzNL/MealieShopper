"""Tests voor de async-tweelingen in `mealieshopper.auth`: `aconnect` en de helpers met een `a` ervoor.

Elke tweeling moet op dezelfde database hetzelfde geven als de synchrone versie, en wat de ene schrijft moet de
andere lezen. De synchrone versies blijven bestaan tot de hele app async is.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, closing
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from quart import Quart

from mealieshopper import auth

OTHER_ID: str = "u-2"


@pytest.fixture(autouse=True)
def auth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inloggen staat aan en het geheim komt uit `secret.key`, niet uit de omgeving."""
    monkeypatch.setenv("PASSKEY_AUTH_ENABLED", "true")
    monkeypatch.delenv("MEALIESHOPPER_AUTH_SECRET", raising=False)
    monkeypatch.delenv("JWT_SECRET", raising=False)


@pytest.fixture
def seeded(data_dir: Path) -> Path:
    """Een database met een eigenaar en een tweede gebruiker, elk met eigen inloggegevens, via de synchrone helpers."""
    auth.init_db()
    now: str = auth.utcnow().isoformat()
    with closing(auth.connect()) as conn, conn:
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
    auth.set_setting(auth.SETTING_AUTH_MODE, "passkey")
    auth.set_secret("MEALIE_API_TOKEN", "spam-token")
    return data_dir


@asynccontextmanager
async def request_as(
    user_id: str | None, *, bearer: bool = False
) -> AsyncIterator[None]:
    """Een verzoekcontext met (of zonder) een sessie van `user_id`, als cookie of als Bearer-token."""
    headers: dict[str, str] = {}
    if user_id:
        username: str = "spam" if user_id == auth.OWNER_ID else "eggs"
        token: str = auth.create_token(user_id, username)
        headers = (
            {"Authorization": f"Bearer {token}"}
            if bearer
            else {"Cookie": f"{auth.SESSION_COOKIE_NAME}={token}"}
        )
    async with Quart(__name__).test_request_context("/api/recipes", headers=headers):
        yield


# Lezende tweelingen: (naam, argumenten). De async versie heet "a" + naam.
READ_TWINS: list[tuple[str, tuple[Any, ...]]] = [
    ("get_setting", (auth.SETTING_AUTH_MODE,)),
    ("get_setting", ("ONBEKEND", "standaard")),
    ("auth_mode", ()),
    ("auth_required", ()),
    ("session_epoch", ()),
    ("session_epoch", (OTHER_ID,)),
    ("session_epoch", ("niemand",)),
    ("credential_rows", ()),
    ("credential_rows", (OTHER_ID,)),
    ("passkey_count", ()),
    ("passkey_count", (auth.OWNER_ID,)),
    ("password_configured", ()),
    ("password_configured", (auth.OWNER_ID,)),
    ("mfa_enabled", ()),
    ("mfa_enabled", (OTHER_ID,)),
    ("recovery_codes_left", ()),
    ("recovery_codes_left", (OTHER_ID,)),
    ("credentials_exist", ()),
    ("get_secret", ("MEALIE_API_TOKEN",)),
    ("get_secret", ("ONBEKEND",)),
    ("master_secret", ()),
    ("signing_secret", ()),
]


@pytest.mark.parametrize(
    ("name", "args"), READ_TWINS, ids=[f"{name}{args}" for name, args in READ_TWINS]
)
async def test_read_twin_matches_sync(
    seeded: Path, name: str, args: tuple[Any, ...]
) -> None:
    sync_fn: Callable[..., Any] = getattr(auth, name)
    async_fn: Callable[..., Awaitable[Any]] = getattr(auth, f"a{name}")
    assert await async_fn(*args) == sync_fn(*args)


# Tweelingen die het verzoek lezen: hetzelfde antwoord voor een eigenaar, een gewone gebruiker en een anoniem verzoek.
REQUEST_TWINS: list[str] = [
    "current_user",
    "acting_user_id",
    "is_owner",
    "require_auth_for_request",
]
REQUEST_CALLERS: list[tuple[str | None, bool]] = [
    (auth.OWNER_ID, False),
    (OTHER_ID, False),
    (OTHER_ID, True),
    (None, False),
]


@pytest.mark.parametrize("name", REQUEST_TWINS)
@pytest.mark.parametrize(
    ("user_id", "bearer"),
    REQUEST_CALLERS,
    ids=["owner", "user", "user-bearer", "anonymous"],
)
async def test_request_twin_matches_sync(
    seeded: Path, name: str, user_id: str | None, bearer: bool
) -> None:
    async with request_as(user_id, bearer=bearer):
        expected: Any = getattr(auth, name)()
        assert await getattr(auth, f"a{name}")() == expected


async def test_acurrent_user_rejects_a_session_from_before_the_epoch_bump(
    seeded: Path,
) -> None:
    async with request_as(OTHER_ID):
        assert (await auth.acurrent_user() or {}).get("id") == OTHER_ID
        await auth.abump_session_epoch(OTHER_ID)
        assert await auth.acurrent_user() is None
        assert auth.current_user() is None


async def test_averify_token_matches_verify_token(seeded: Path) -> None:
    token: str = auth.create_token(auth.OWNER_ID, "spam")
    assert await auth.averify_token(token) == auth.verify_token(token)
    assert await auth.averify_token("geen-token") is None


async def test_acreate_token_carries_the_same_claims(seeded: Path) -> None:
    claims: dict[str, Any] = (
        auth.verify_token(await auth.acreate_token(auth.OWNER_ID, "spam")) or {}
    )
    assert (claims["sub"], claims["usr"], claims["ep"]) == (auth.OWNER_ID, "spam", 3)
    assert claims["exp"] - claims["iat"] == auth.SESSION_MAX_AGE_SECONDS


async def test_asecret_box_decrypts_what_secret_box_encrypted(seeded: Path) -> None:
    token: bytes = auth.secret_box().encrypt(b"spam")
    assert (await auth.asecret_box()).decrypt(token) == b"spam"


async def test_auth_required_twins_without_credentials(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert await auth.aauth_required() is True
    auth.set_setting(auth.SETTING_AUTH_MODE, "none")
    assert await auth.aauth_required() is False
    monkeypatch.setenv("PASSKEY_AUTH_ENABLED", "false")
    assert await auth.aauth_required() is False
    async with request_as(None):
        assert await auth.ais_owner() is True
        assert await auth.arequire_auth_for_request() is None


async def test_aset_setting_is_seen_by_get_setting(data_dir: Path) -> None:
    await auth.aset_setting("WEEK_START", "monday")
    assert auth.get_setting("WEEK_START") == "monday"
    await auth.aset_setting("WEEK_START", "sunday")
    assert auth.get_setting("WEEK_START") == "sunday"


async def test_aset_secret_round_trip(data_dir: Path) -> None:
    await auth.aset_secret("AH_TOKEN", "spam")
    assert auth.get_secret("AH_TOKEN") == "spam"
    assert await auth.aget_secret("AH_TOKEN") == "spam"
    with closing(auth.connect()) as conn:
        stored: str = conn.execute(
            "SELECT value FROM app_settings WHERE key = 'AH_TOKEN'"
        ).fetchone()["value"]
    assert stored.startswith("gAAAA")  # versleuteld opgeslagen
    await auth.adelete_secret("AH_TOKEN")
    assert await auth.aget_secret("AH_TOKEN") == ""
    assert auth.get_secret("AH_TOKEN") == ""


async def test_aget_secret_reads_what_set_secret_wrote(data_dir: Path) -> None:
    auth.set_secret("PICNIC_TOKEN", "eggs")
    assert await auth.aget_secret("PICNIC_TOKEN") == "eggs"


async def test_apurge_obsolete_secrets(data_dir: Path) -> None:
    auth.set_secret("AH_PASSWORD", "spam")
    auth.set_secret("JUMBO_COOKIES", "eggs")
    auth.set_secret("MEALIE_API_TOKEN", "blijft")
    assert await auth.apurge_obsolete_secrets() == 2
    assert auth.get_secret("AH_PASSWORD") == ""
    assert auth.get_secret("MEALIE_API_TOKEN") == "blijft"
    assert await auth.apurge_obsolete_secrets() == 0


async def test_abump_session_epoch(seeded: Path) -> None:
    assert await auth.abump_session_epoch(auth.OWNER_ID) == 4
    assert auth.session_epoch(auth.OWNER_ID) == 4
    assert auth.session_epoch(OTHER_ID) == 0  # alleen die ene gebruiker


async def test_astore_challenge_and_apop_challenge(data_dir: Path) -> None:
    await auth.astore_challenge("login", b"spam")
    assert await auth.apop_challenge("login") == b"spam"
    assert await auth.apop_challenge("login") is None  # eenmalig
    await auth.astore_challenge("register:owner", b"eggs")
    assert auth.pop_challenge("register:owner") == b"eggs"
    auth.store_challenge("register:owner", b"ham")
    assert await auth.apop_challenge("register:owner") == b"ham"


async def test_apop_challenge_ignores_an_expired_challenge(data_dir: Path) -> None:
    await auth.astore_challenge("login", b"spam")
    later: Any = auth.utcnow() + auth.timedelta(minutes=6)
    with mock.patch.object(auth, "utcnow", return_value=later):
        assert await auth.apop_challenge("login") is None
    with closing(auth.connect()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM auth_challenges").fetchone()[0] == 0


async def test_aensure_db_creates_the_schema_once(data_dir: Path) -> None:
    assert not auth.db_path().exists()
    with mock.patch.object(auth, "ainit_db", wraps=auth.ainit_db) as init:
        await auth.aensure_db()
        await auth.aensure_db()
    assert init.call_count == 1
    assert auth._INITIALIZED is True
    with mock.patch.object(auth, "init_db") as sync_init:
        auth.ensure_db()  # dezelfde vlag: de synchrone versie doet het niet nog eens
    sync_init.assert_not_called()
    with closing(auth.connect()) as conn:
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


async def test_aensure_db_respects_a_sync_init(data_dir: Path) -> None:
    auth.ensure_db()
    with mock.patch.object(auth, "ainit_db") as init:
        await auth.aensure_db()
    init.assert_not_called()


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


async def test_ainit_db_migrates_an_old_database(data_dir: Path) -> None:
    old_database()
    await auth.ainit_db()
    assert auth.session_epoch(auth.OWNER_ID) == 7
    assert await auth.asession_epoch(auth.OWNER_ID) == 7


async def test_concurrent_ainit_db_migrates_once(data_dir: Path) -> None:
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
        await asyncio.gather(*(auth.ainit_db() for _ in range(workers)))
    with closing(auth.connect()) as conn:
        columns: list[str] = [
            row["name"] for row in conn.execute("PRAGMA table_info(users)")
        ]
    assert columns.count("session_epoch") == 1
    assert auth.session_epoch(auth.OWNER_ID) == 7


async def test_aconnect_rows_behave_like_sqlite3_rows(data_dir: Path) -> None:
    auth.set_setting("WEEK_START", "monday")
    async with auth.aconnect() as conn:
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


async def test_aconnect_creates_the_data_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target: Path = tmp_path / "nieuw" / "data"
    monkeypatch.setenv("MEALIESHOPPER_DATA_DIR", str(target))
    async with auth.aconnect() as conn:
        await conn.execute("SELECT 1")
    assert (target / "auth.sqlite3").exists()


async def test_aconnect_does_not_commit_implicitly(data_dir: Path) -> None:
    auth.ensure_db()
    async with auth.aconnect() as conn:
        await conn.execute(
            "INSERT INTO app_settings (key, value, updated_at) VALUES ('SPAM', 'eggs', 'nu')"
        )
    assert auth.get_setting("SPAM") == ""
    async with auth.aconnect() as conn:
        await conn.execute(
            "INSERT INTO app_settings (key, value, updated_at) VALUES ('SPAM', 'eggs', 'nu')"
        )
        await conn.commit()
    assert auth.get_setting("SPAM") == "eggs"


async def test_aconnect_closes_the_connection(data_dir: Path) -> None:
    async with auth.aconnect() as conn:
        pass
    with pytest.raises(ValueError):
        await conn.execute("SELECT 1")


async def test_amaster_secret_shares_the_cache(data_dir: Path) -> None:
    secret: str = await auth.amaster_secret()
    assert len(secret) == 64
    assert (data_dir / auth.SECRET_FILE).read_text(encoding="ascii") == secret
    assert auth.master_secret() == secret
    (data_dir / auth.SECRET_FILE).unlink()
    assert (
        await auth.amaster_secret() == secret
    )  # uit de cache, het bestand wordt niet opnieuw gelezen


async def test_amaster_secret_prefers_the_environment(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEALIESHOPPER_AUTH_SECRET", "  spam  ")
    assert await auth.amaster_secret() == "spam"
    assert not (data_dir / auth.SECRET_FILE).exists()


async def test_amaster_secret_waits_for_another_worker_without_blocking(
    data_dir: Path,
) -> None:
    """Een andere werker maakt `secret.key` net aan: wacht met asyncio.sleep tot de inhoud er staat."""
    path: Path = data_dir / auth.SECRET_FILE
    full: str = "f" * 64
    real_open: Callable[..., int] = os.open

    def other_worker_wins(target: Any, flags: int, mode: int = 0o777) -> int:
        if Path(target) == path and flags & os.O_EXCL:
            path.write_text("half", encoding="ascii")
            asyncio.get_running_loop().call_later(0.15, path.write_text, full, "ascii")
            raise FileExistsError(target)
        return real_open(target, flags, mode)

    with (
        mock.patch.object(auth.os, "open", side_effect=other_worker_wins),
        mock.patch.object(
            auth.time,
            "sleep",
            side_effect=AssertionError("time.sleep blokkeert de loop"),
        ),
    ):
        assert await auth.amaster_secret() == full
    assert auth.master_secret() == full


async def test_amaster_secret_refuses_a_damaged_file(data_dir: Path) -> None:
    (data_dir / auth.SECRET_FILE).write_text("kort", encoding="ascii")
    with pytest.raises(RuntimeError, match="onleesbaar of onvolledig"):
        await auth.amaster_secret()


def test_every_db_helper_has_an_async_twin() -> None:
    """Elke publieke helper die de database of `master_secret` raakt, heeft een tweeling met dezelfde parameters."""
    import inspect

    names: list[str] = (
        [name for name, _ in READ_TWINS]
        + REQUEST_TWINS
        + [
            "init_db",
            "ensure_db",
            "set_setting",
            "bump_session_epoch",
            "set_secret",
            "delete_secret",
            "purge_obsolete_secrets",
            "store_challenge",
            "pop_challenge",
            "create_token",
            "verify_token",
            "secret_box",
        ]
    )
    for name in names:
        twin: Any = getattr(auth, f"a{name}")
        assert inspect.iscoroutinefunction(twin), name
        sync_signature: inspect.Signature = inspect.signature(getattr(auth, name))
        async_signature: inspect.Signature = inspect.signature(twin)
        assert async_signature.parameters == sync_signature.parameters, name
        assert async_signature.return_annotation == sync_signature.return_annotation, (
            name
        )


async def test_twins_do_not_reach_the_sync_connection(seeded: Path) -> None:
    """De tweelingen lezen en schrijven via aconnect, nooit via de synchrone connect()."""
    auth.forget_runtime_state()
    with mock.patch.object(
        auth, "connect", side_effect=AssertionError("sync connect() in een tweeling")
    ):
        await auth.aensure_db()
        await auth.aget_setting(auth.SETTING_AUTH_MODE)
        await auth.aset_setting("SPAM", "eggs")
        await auth.aset_secret("HAM", "spam")
        await auth.aget_secret("HAM")
        await auth.adelete_secret("HAM")
        await auth.apurge_obsolete_secrets()
        await auth.acredentials_exist()
        await auth.amfa_enabled(auth.OWNER_ID)
        await auth.arecovery_codes_left(auth.OWNER_ID)
        await auth.acredential_rows()
        await auth.abump_session_epoch()
        await auth.astore_challenge("login", b"spam")
        await auth.apop_challenge("login")
        token: str = await auth.acreate_token(auth.OWNER_ID, "spam")
        async with Quart(__name__).test_request_context(
            "/api/recipes", headers={"Cookie": f"{auth.SESSION_COOKIE_NAME}={token}"}
        ):
            assert (await auth.acurrent_user() or {}).get("id") == auth.OWNER_ID
            assert await auth.aacting_user_id() == auth.OWNER_ID
            assert await auth.ais_owner() is True
            assert await auth.arequire_auth_for_request() is None

