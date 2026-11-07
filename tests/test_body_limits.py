"""Grenzen op het lichaam van een aanvraag (mealieshopper/body_limits.py): 16 MiB, ruimer alleen waar het moet.

Zonder inloggen bereikbare routes (inloggen, `/oauth/token`) weigeren een groot lichaam met 413. Het klaarzetten van een
back-up en de prijsimport van de beheerder krijgen meer ruimte.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING
from unittest import mock

import pytest
from quart import Quart

from mealieshopper import backup, body_limits, mcp, price_import

if TYPE_CHECKING:  # niet bij de import: pytest zou TestClientProtocol voor een testklasse aanzien
    from quart.typing import TestClientProtocol

MIB: int = 1024 * 1024


def test_the_default_limit_is_16_mib_and_only_two_routes_get_more(app: Quart) -> None:
    assert app.config["MAX_CONTENT_LENGTH"] == 16 * MIB
    assert app.request_class is body_limits.SizedRequest
    assert body_limits.LARGE_BODIES == {
        "/api/backup/stage": backup.MAX_STAGE_BYTES + MIB,
        "/api/prices/import": 32 * MIB,
    }


async def test_an_oversized_body_on_the_passkey_login_gets_413(client: TestClientProtocol, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PASSKEY_AUTH_ENABLED", "true")  # test_auth_password zet het bij de import uit
    response = await client.post(
        "/api/auth/login/verify",
        data=b"{" + b" " * (16 * MIB),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413


async def test_a_login_body_under_the_limit_is_read(client: TestClientProtocol, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PASSKEY_AUTH_ENABLED", "true")
    response = await client.post(
        "/api/auth/login/verify", json={"credential": {"id": "x" * MIB}}
    )
    assert response.status_code != 413


async def test_an_oversized_body_on_the_oauth_token_endpoint_gets_413(
    client: TestClientProtocol,
) -> None:
    with mock.patch.object(mcp, "enabled", mock.AsyncMock(return_value=(True, ""))):
        response = await client.post(
            "/oauth/token",
            data=b"grant_type=" + b"x" * (16 * MIB),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    assert response.status_code == 413


async def test_a_price_import_above_16_mib_is_not_refused_by_the_body_limit(
    client: TestClientProtocol, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tot 5 miljoen tekens tekst (`price_import.MAX_TEXT_CHARS`): met tekens van 4 bytes ruim boven 16 MiB."""
    monkeypatch.setenv("PASSKEY_AUTH_ENABLED", "false")
    text: str = "\U0001f96c" * 4_500_000  # sla, 4 bytes in UTF-8
    body: bytes = json.dumps({"store": "ah", "data": text, "dryRun": True}, ensure_ascii=False).encode()
    assert len(text) <= price_import.MAX_TEXT_CHARS < len(body) and len(body) > 16 * MIB
    response = await client.post("/api/prices/import", data=body, headers={"Content-Type": "application/json"})
    assert response.status_code == 400  # de eigen melding van de import (geen geldige JSON), geen 413
    assert "JSON" in (await response.get_json())["error"]


async def test_a_price_import_above_its_own_limit_gets_413(
    client: TestClientProtocol, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PASSKEY_AUTH_ENABLED", "false")
    response = await client.post(
        "/api/prices/import",
        data=b"{" + b" " * (32 * MIB),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413


async def test_a_large_backup_upload_still_passes_the_body_limit(
    client: TestClientProtocol, monkeypatch: pytest.MonkeyPatch, data_dir: Path
) -> None:
    monkeypatch.setenv("PASSKEY_AUTH_ENABLED", "false")
    response = await client.post(
        "/api/backup/stage",
        data=bytes(100 * MIB),
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code != 413
    assert response.status_code < 500
    assert (
        list(data_dir.glob(".restore-*")) == []
    )  # geen geldige back-up: niets blijft staan
