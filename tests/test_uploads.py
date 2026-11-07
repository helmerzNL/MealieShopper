"""Uploaden in stukken (mealieshopper/uploads.py) onder de server: wat er gebeurt als de client halverwege een stuk weggaat.

De rest van het gedrag (aansluiten, grenzen, stilte, opruimen) staat in test_backup.py (ChunkedUploadTest) en
test_price_archive.py, via de routes van de back-up en het prijsarchief.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from quart import Quart

from mealieshopper import backup, uploads
from mealieshopper.app import create_app

Message = dict[str, Any]


def put_scope(token: str, length: int) -> dict[str, Any]:
    path: str = f"/api/backup/uploads/{token}"
    return {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "PUT", "scheme": "http",
        "path": path, "raw_path": path.encode(), "query_string": b"offset=0", "root_path": "",
        "headers": [(b"host", b"localhost"), (b"content-type", b"application/octet-stream"), (b"content-length", str(length).encode())],
        "client": ("127.0.0.1", 50000), "server": ("127.0.0.1", 8000), "state": {}, "extensions": {},
    }


def half_then_gone(part: bytes) -> Callable[[], Awaitable[Message]]:
    """`receive` dat een deel van het stuk geeft en daarna meldt dat de client weg is."""
    messages: list[Message] = [{"type": "http.request", "body": part, "more_body": True}, {"type": "http.disconnect"}]

    async def receive() -> Message:
        if messages:
            return messages.pop(0)
        await asyncio.Event().wait()  # daarna komt er niets meer
        raise AssertionError("onbereikbaar")

    return receive


async def test_a_client_that_goes_away_halfway_a_part_leaves_nothing_written(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PASSKEY_AUTH_ENABLED", "false")
    app: Quart = create_app()
    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    async with app.test_app():
        token: str = (await uploads.begin(backup.STAGE_PREFIX, 100, backup.MAX_STAGE_BYTES))["token"]
        await asyncio.wait_for(app(put_scope(token, 20), half_then_gone(b"{" * 10), send), 5)  # de route wacht niet op de rest
        part: Path = uploads.part_path(backup.STAGE_PREFIX, token)
        assert part.read_bytes() == b"", "een half stuk wordt nooit geschreven"
        assert not any(message.get("status") == 200 for message in sent)
        # Het scherm stuurt het stuk opnieuw, vanaf het begin: dat sluit aan.
        assert await uploads.append(backup.STAGE_PREFIX, token, 0, _once(b"{" * 20), 20, backup.MAX_STAGE_BYTES) == {"received": 20}


async def _once(data: bytes) -> AsyncIterator[bytes]:
    """Een lichaam dat in een keer binnenkomt."""
    yield data
