"""Tests voor oauth.py zelf: elke publieke functie die de database raakt, rechtstreeks aangeroepen."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from mealieshopper import auth, oauth

REDIRECT: str = "https://claude.ai/api/mcp/auth_callback"
MCP_URL: str = "http://localhost/mcp"
OWNER: str = auth.OWNER_ID


def pkce() -> tuple[str, str]:
    verifier: str = secrets.token_urlsafe(48)
    challenge: str = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


async def execute(sql: str, params: tuple[Any, ...] = ()) -> None:
    """Een losse schrijfactie, om een tijdstip of vlag in de tests klaar te zetten."""
    async with auth.connect() as conn:
        await conn.execute(sql, params)
        await conn.commit()


async def scalar(sql: str, params: tuple[Any, ...] = ()) -> Any:
    async with auth.connect() as conn, conn.execute(sql, params) as cursor:
        row = await cursor.fetchone()
    return row[0] if row else None


@pytest.fixture
async def owner(data_dir: Path) -> str:
    """Een database met alleen de eigenaar (`admin`)."""
    await auth.ensure_db()
    await execute(
        "INSERT INTO users (id, username, created_at) VALUES (?, ?, ?)",
        (OWNER, "admin", auth.utcnow().isoformat()),
    )
    return OWNER


async def register(name: str = "Claude") -> str:
    return (await oauth.register_client({"client_name": name, "redirect_uris": [REDIRECT]}))["client_id"]


async def code_for(client_id: str, user_id: str = OWNER) -> tuple[str, str]:
    verifier, challenge = pkce()
    code: str = await oauth.issue_code(user_id, client_id, REDIRECT, challenge, MCP_URL)
    return code, verifier


def exchange_form(client_id: str, code: str, verifier: str) -> dict[str, str]:
    return {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "code": code,
        "redirect_uri": REDIRECT,
        "code_verifier": verifier,
    }


async def tokens_for(client_id: str, user_id: str = OWNER) -> dict[str, Any]:
    code, verifier = await code_for(client_id, user_id)
    return await oauth.token_request(exchange_form(client_id, code, verifier), MCP_URL)


async def test_register_client_stores_a_public_client(owner: str) -> None:
    data: dict[str, Any] = await oauth.register_client({"client_name": "  Claude\n Desktop ", "redirect_uris": [REDIRECT]})
    assert data["client_id"].startswith("msc_")
    assert data["token_endpoint_auth_method"] == "none"
    assert await oauth.get_client(data["client_id"]) == {
        "client_id": data["client_id"],
        "name": "Claude Desktop",
        "redirect_uris": [REDIRECT],
    }


async def test_register_client_refuses_a_bad_redirect_without_writing(
    owner: str,
) -> None:
    await register()
    with pytest.raises(oauth.OAuthError) as raised:
        await oauth.register_client({"redirect_uris": ["http://evil.example.com/"]})
    assert raised.value.code == "invalid_redirect_uri"
    assert await scalar("SELECT COUNT(*) FROM oauth_clients") == 1


async def test_a_full_registry_drops_unused_clients_but_never_used_ones(owner: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(oauth, "MAX_CLIENTS", 2)
    used: str = await register()
    await tokens_for(used)
    unused: str = await register()
    old: str = (oauth._now() - timedelta(minutes=20)).isoformat()
    await execute("UPDATE oauth_clients SET created_at = ?", (old,))
    await register()  # er is plek gemaakt
    assert await oauth.get_client(unused) is None
    assert await oauth.get_client(used) is not None
    with pytest.raises(oauth.OAuthError) as raised:
        await register()
    assert raised.value.status == 503


async def test_registering_cleans_up_codes_that_expired_an_hour_ago(owner: str) -> None:
    client_id: str = await register()
    await code_for(client_id)
    await code_for(client_id)
    await execute(
        "UPDATE oauth_codes SET expires_at = ?",
        ((oauth._now() - timedelta(hours=2)).isoformat(),),
    )
    await code_for(client_id)  # deze is nog geldig
    await register()
    assert await scalar("SELECT COUNT(*) FROM oauth_codes") == 1


async def test_get_client_of_an_unknown_id_is_none(owner: str) -> None:
    assert await oauth.get_client("msc_bestaat-niet") is None
    assert await oauth.get_client("") is None


async def test_check_authorize_request_finds_the_client_and_checks_the_redirect(
    owner: str,
) -> None:
    client_id: str = await register()
    _, challenge = pkce()
    params: dict[str, str] = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": "xyz",
    }
    info: dict[str, Any] = await oauth.check_authorize_request(params, MCP_URL)
    assert (info["client"]["client_id"], info["error"], info["resource"]) == (
        client_id,
        None,
        MCP_URL,
    )
    with pytest.raises(oauth.OAuthError):
        await oauth.check_authorize_request({**params, "redirect_uri": "https://elders.example/cb"}, MCP_URL)
    with pytest.raises(oauth.OAuthError):
        await oauth.check_authorize_request({**params, "client_id": "msc_onbekend"}, MCP_URL)


async def test_issue_code_and_exchange_it_for_tokens(owner: str) -> None:
    client_id: str = await register()
    code, verifier = await code_for(client_id)
    assert await scalar("SELECT epoch FROM oauth_codes") == await auth.session_epoch(OWNER)
    tokens: dict[str, Any] = await oauth.token_request(exchange_form(client_id, code, verifier), MCP_URL)
    assert (tokens["token_type"], tokens["expires_in"], tokens["scope"]) == (
        "Bearer",
        3600,
        "mealieshopper",
    )
    assert tokens["access_token"].startswith("msa_") and tokens["refresh_token"].startswith("msr_")
    assert await scalar("SELECT used FROM oauth_codes") == 1


async def test_a_code_used_twice_revokes_the_tokens_of_the_app(owner: str) -> None:
    client_id: str = await register()
    code, verifier = await code_for(client_id)
    tokens: dict[str, Any] = await oauth.exchange_code(exchange_form(client_id, code, verifier), MCP_URL)
    with pytest.raises(oauth.OAuthError, match="al gebruikt"):
        await oauth.exchange_code(exchange_form(client_id, code, verifier), MCP_URL)
    assert await scalar("SELECT revoked FROM oauth_tokens") == 1, "het intrekken blijft staan na de fout"
    assert await oauth.verify_access_token(tokens["access_token"], MCP_URL) is None


async def test_a_refused_exchange_leaves_the_code_unused(owner: str) -> None:
    client_id: str = await register()
    code, _ = await code_for(client_id)
    with pytest.raises(oauth.OAuthError, match="PKCE"):
        await oauth.exchange_code(exchange_form(client_id, code, "x" * 43), MCP_URL)
    assert await scalar("SELECT used FROM oauth_codes") == 0, "een fout draait het markeren terug"


async def test_an_expired_code_is_refused(owner: str) -> None:
    client_id: str = await register()
    code, verifier = await code_for(client_id)
    await execute(
        "UPDATE oauth_codes SET expires_at = ?",
        ((oauth._now() - timedelta(seconds=1)).isoformat(),),
    )
    with pytest.raises(oauth.OAuthError, match="verlopen"):
        await oauth.exchange_code(exchange_form(client_id, code, verifier), MCP_URL)


async def test_a_session_epoch_bump_invalidates_the_code_and_the_token(
    owner: str,
) -> None:
    client_id: str = await register()
    tokens: dict[str, Any] = await tokens_for(client_id)
    code, verifier = await code_for(client_id)
    await auth.bump_session_epoch(OWNER)
    assert await oauth.verify_access_token(tokens["access_token"], MCP_URL) is None
    with pytest.raises(oauth.OAuthError, match="gewijzigd"):
        await oauth.exchange_code(exchange_form(client_id, code, verifier), MCP_URL)


async def test_refresh_rotates_and_a_reused_refresh_token_revokes_the_family(
    owner: str,
) -> None:
    client_id: str = await register()
    first: dict[str, Any] = await tokens_for(client_id)
    form: dict[str, str] = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "refresh_token": first["refresh_token"],
    }
    second: dict[str, Any] = await oauth.refresh(form)
    assert second["access_token"] != first["access_token"]
    assert await oauth.verify_access_token(second["access_token"], MCP_URL) is not None
    with pytest.raises(oauth.OAuthError, match="al gebruikt"):
        await oauth.token_request(form, MCP_URL)
    assert await scalar("SELECT COUNT(*) FROM oauth_tokens WHERE revoked = 0") == 0
    assert await oauth.verify_access_token(second["access_token"], MCP_URL) is None


async def test_token_request_refuses_an_unknown_grant(owner: str) -> None:
    with pytest.raises(oauth.OAuthError) as raised:
        await oauth.token_request({"grant_type": "password"}, MCP_URL)
    assert raised.value.code == "unsupported_grant_type"


async def test_verify_access_token_gives_the_user_and_records_the_use(
    owner: str,
) -> None:
    client_id: str = await register("ChatGPT")
    tokens: dict[str, Any] = await tokens_for(client_id)
    assert await scalar("SELECT last_used_at FROM oauth_tokens") is None
    identity: dict[str, Any] | None = await oauth.verify_access_token(tokens["access_token"], MCP_URL)
    assert identity == {
        "client_id": client_id,
        "name": "ChatGPT",
        "user_id": OWNER,
        "username": "admin",
    }
    assert await scalar("SELECT last_used_at FROM oauth_tokens") is not None
    assert await oauth.verify_access_token(tokens["access_token"], "https://elders.example/mcp") is None
    assert await oauth.verify_access_token("msa_onbekend", MCP_URL) is None
    assert await oauth.verify_access_token("geen-token", MCP_URL) is None


async def test_revoke_token_revokes_the_whole_family_and_never_fails(
    owner: str,
) -> None:
    client_id: str = await register()
    tokens: dict[str, Any] = await tokens_for(client_id)
    await oauth.revoke_token("msr_onbekend")
    await oauth.revoke_token("")
    assert await oauth.verify_access_token(tokens["access_token"], MCP_URL) is not None
    await oauth.revoke_token(tokens["refresh_token"])
    assert await oauth.verify_access_token(tokens["access_token"], MCP_URL) is None


async def test_connected_apps_lists_active_apps_and_revoke_app_removes_them(
    owner: str,
) -> None:
    client_id: str = await register("Claude")
    await tokens_for(client_id)
    await code_for(client_id)
    apps: list[dict[str, Any]] = await oauth.connected_apps(OWNER)
    assert [(app["clientId"], app["name"]) for app in apps] == [(client_id, "Claude")]
    assert await oauth.revoke_app(OWNER, "msc_onbekend") is False
    assert await oauth.revoke_app(OWNER, client_id) is True
    assert await oauth.connected_apps(OWNER) == []
    assert await scalar("SELECT COUNT(*) FROM oauth_codes") == 0
    assert await oauth.revoke_app(OWNER, client_id) is False


async def test_forget_user_removes_codes_and_tokens_of_that_user_only(
    owner: str,
) -> None:
    await execute(
        "INSERT INTO users (id, username, created_at) VALUES ('u_anna', 'anna', ?)",
        (auth.utcnow().isoformat(),),
    )
    client_id: str = await register()
    await tokens_for(client_id, OWNER)
    await tokens_for(client_id, "u_anna")
    await code_for(client_id, "u_anna")
    await oauth.forget_user("u_anna")
    assert await scalar("SELECT COUNT(*) FROM oauth_tokens WHERE user_id = 'u_anna'") == 0
    assert await scalar("SELECT COUNT(*) FROM oauth_codes WHERE user_id = 'u_anna'") == 0
    assert await scalar("SELECT COUNT(*) FROM oauth_tokens WHERE user_id = ?", (OWNER,)) == 1


async def test_two_concurrent_exchanges_of_one_code_issue_one_token_and_revoke_it(owner: str) -> None:
    client_id: str = await register()
    code, verifier = await code_for(client_id)
    form: dict[str, str] = exchange_form(client_id, code, verifier)
    results: list[Any] = await asyncio.gather(oauth.exchange_code(form, MCP_URL), oauth.exchange_code(form, MCP_URL), return_exceptions=True)
    tokens: list[dict[str, Any]] = [result for result in results if isinstance(result, dict)]
    errors: list[BaseException] = [result for result in results if isinstance(result, BaseException)]
    assert len(tokens) == 1, results
    assert len(errors) == 1 and isinstance(errors[0], oauth.OAuthError), results
    assert "al gebruikt" in errors[0].description
    assert await scalar("SELECT COUNT(*) FROM oauth_tokens") == 1
    assert await oauth.verify_access_token(tokens[0]["access_token"], MCP_URL) is None, "het hergebruik trekt in"


async def test_two_concurrent_refreshes_of_one_token_rotate_once_and_revoke_the_family(owner: str) -> None:
    client_id: str = await register()
    first: dict[str, Any] = await tokens_for(client_id)
    form: dict[str, str] = {"grant_type": "refresh_token", "client_id": client_id, "refresh_token": first["refresh_token"]}
    results: list[Any] = await asyncio.gather(oauth.refresh(form), oauth.refresh(form), return_exceptions=True)
    rotated: list[dict[str, Any]] = [result for result in results if isinstance(result, dict)]
    errors: list[BaseException] = [result for result in results if isinstance(result, BaseException)]
    assert len(rotated) == 1, results
    assert len(errors) == 1 and isinstance(errors[0], oauth.OAuthError), results
    assert "al gebruikt" in errors[0].description
    assert await scalar("SELECT COUNT(*) FROM oauth_tokens") == 2
    assert await scalar("SELECT COUNT(*) FROM oauth_tokens WHERE revoked = 0") == 0
