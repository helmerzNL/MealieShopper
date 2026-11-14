"""Grenzen op het lichaam van een aanvraag (mealieshopper/body_limits.py): 16 MiB, ruimer alleen waar het moet.

Zonder inloggen bereikbare routes (inloggen, `/oauth/token`) weigeren een groot lichaam met 413. Het klaarzetten van een
back-up en de prijsimport van de beheerder krijgen meer ruimte.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import TYPE_CHECKING
from unittest import mock

import pytest
from quart import Quart

from mealieshopper import backup, body_limits, mcp, price_archive, price_import, uploads

if TYPE_CHECKING:  # niet bij de import: pytest zou TestClientProtocol voor een testklasse aanzien
    from quart.typing import TestClientProtocol

MIB: int = 1024 * 1024


async def _until_staged(data_dir: Path, size: int, timeout: float = 5) -> None:
    """Wacht tot er een bestand van een upload naar /api/backup/stage staat met minstens `size` bytes op schijf."""
    deadline: float = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if any(path.stat().st_size >= size for path in data_dir.glob(".restore-*")):
            return
        await asyncio.sleep(0.001)
    pytest.fail(f"na {timeout} s staan er nog geen {size} bytes van de upload op schijf")


def test_the_default_limit_is_16_mib_and_only_three_routes_get_more(app: Quart) -> None:
    assert app.config["MAX_CONTENT_LENGTH"] == 16 * MIB
    assert app.request_class is body_limits.SizedRequest
    assert body_limits.LARGE_BODIES == {
        "/api/backup/stage": backup.MAX_STAGE_BYTES + MIB,
        "/api/price-archive/stage": price_archive.MAX_UPLOAD_BYTES + MIB,
        "/api/prices/import": 32 * MIB,
    }


def test_a_part_of_a_chunked_upload_fits_in_the_default_limit_with_headroom() -> None:
    # Een stuk (uploads.py) gaat per verzoek: met 1 MiB marge onder de 16 MiB, zodat de grens van uploads.py zelf (413 met
    # `upload_chunk_too_large`) eerder geldt dan die van Quart.
    assert uploads.CHUNK_BYTES + MIB <= body_limits.DEFAULT_MAX_BODY


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
    response = await client.post("/api/auth/login/verify", json={"credential": {"id": "x" * MIB}})
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


async def test_a_price_import_above_16_mib_is_not_refused_by_the_body_limit(client: TestClientProtocol, monkeypatch: pytest.MonkeyPatch) -> None:
    """Tot 5 miljoen tekens tekst (`price_import.MAX_TEXT_CHARS`): met tekens van 4 bytes ruim boven 16 MiB."""
    monkeypatch.setenv("PASSKEY_AUTH_ENABLED", "false")
    text: str = "\U0001f96c" * 4_500_000  # sla, 4 bytes in UTF-8
    body: bytes = json.dumps({"store": "ah", "data": text, "dryRun": True}, ensure_ascii=False).encode()
    assert len(text) <= price_import.MAX_TEXT_CHARS < len(body) and len(body) > 16 * MIB
    response = await client.post("/api/prices/import", data=body, headers={"Content-Type": "application/json"})
    assert response.status_code == 400  # de eigen melding van de import (geen geldige JSON), geen 413
    assert "JSON" in (await response.get_json())["error"]


async def test_a_price_import_above_its_own_limit_gets_413(client: TestClientProtocol, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PASSKEY_AUTH_ENABLED", "false")
    response = await client.post(
        "/api/prices/import",
        data=b"{" + b" " * (32 * MIB),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413


async def test_a_large_backup_upload_still_passes_the_body_limit(client: TestClientProtocol, monkeypatch: pytest.MonkeyPatch, data_dir: Path) -> None:
    monkeypatch.setenv("PASSKEY_AUTH_ENABLED", "false")
    # In stukken van 1 MiB, elk op schijf voor het volgende komt, zoals een server het aflevert. In een keer kopieert Quart de
    # hele 100 MiB op de event loop (`bytes(self._data)` in `Body.__anext__`), en dat kan op een trage runner 100 ms duren.
    piece: bytes = bytes(MIB)
    async with client.request("/api/backup/stage", method="POST", headers={"Content-Type": "application/octet-stream"}) as connection:
        for sent in range(MIB, 100 * MIB + 1, MIB):
            await connection.send(piece)
            await _until_staged(data_dir, sent)
        await connection.send_complete()
    response = await connection.as_response()
    assert response.status_code != 413
    assert response.status_code < 500
    assert list(data_dir.glob(".restore-*")) == []  # geen geldige back-up: niets blijft staan
