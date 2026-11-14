"""Uploaden in stukken (mealieshopper/uploads.py) onder de server: wat er gebeurt als de client halverwege een stuk weggaat.

De rest van het gedrag (aansluiten, grenzen, stilte, opruimen) staat in test_backup.py (ChunkedUploadTest) en
test_price_archive.py, via de routes van de back-up en het prijsarchief.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from quart import Quart

from mealieshopper import backup, price_archive, uploads
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


# Elke plek die een token van een upload of een klaargezet bestand tot een pad maakt, met de fout die hij geeft.
TOKEN_SITES: dict[str, tuple[Callable[[str], Path], type[Exception]]] = {
    "uploads.part_path": (lambda token: uploads.part_path(".upload-", token), uploads.UploadError),
    "backup._stage_path": (backup._stage_path, backup.BackupError),
    "price_archive._stage_path": (price_archive._stage_path, price_archive.ArchiveError),
}


@pytest.mark.parametrize("site", sorted(TOKEN_SITES))
def test_a_token_with_a_trailing_newline_is_not_a_token(data_dir: Path, site: str) -> None:
    """Eindreview: `TOKEN_PATTERN.match` met `$` liet een token met een regeleinde erachter door ("$" past ook voor een laatste
    "\\n"). Nu `fullmatch`: alleen 32 tekens 0-9a-f, verder niets."""
    path_of: Callable[[str], Path]
    error: type[Exception]
    path_of, error = TOKEN_SITES[site]
    token: str = "0" * 32
    assert token in path_of(token).name
    for wrong in (f"{token}\n", f"{token}\r\n", f"\n{token}", f"{token} "):
        with pytest.raises(error):
            path_of(wrong)


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


async def test_a_cancelled_write_that_fails_is_not_reported_by_asyncio(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Taak 10b: `append` wachtte met `asyncio.shield` op de thread. Werd het verzoek geannuleerd en faalde het schrijven daarna,
    dan meldde Python 3.15 de fout bij de loop ("exception in shielded future"), al haalt `append` hem zelf op: het verzoek is
    weg. Nu wacht `append` met `tasks.share`."""
    errors: list[dict[str, Any]] = []
    asyncio.get_running_loop().set_exception_handler(lambda loop, context: errors.append(context))
    token: str = (await uploads.begin(backup.STAGE_PREFIX, 100, 100))["token"]
    entered: threading.Event = threading.Event()
    release: threading.Event = threading.Event()

    def refused(path: Path, offset: Any, data: bytes, limit: int) -> int:
        entered.set()
        release.wait(10)
        raise uploads.UploadError("out_of_order", 409, received=0)

    monkeypatch.setattr(uploads, "_write_chunk", refused)
    try:
        first: asyncio.Task[dict[str, int]] = asyncio.create_task(uploads.append(backup.STAGE_PREFIX, token, 0, _once(b"{" * 10), 10, 100))
        assert await asyncio.to_thread(entered.wait, 5)
        first.cancel()
        await asyncio.sleep(0.05)
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await first
    await asyncio.sleep(0.05)
    assert errors == []


async def test_a_cancelled_write_keeps_the_lock_until_its_thread_is_done(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Een annulering (het afsluiten) tijdens het schrijven: de thread schrijft door, dus het slot blijft tot hij klaar is.
    Anders kon een volgend stuk tegelijk in hetzelfde bestand schrijven."""
    token: str = (await uploads.begin(backup.STAGE_PREFIX, 100, 100))["token"]
    entered: threading.Event = threading.Event()
    release: threading.Event = threading.Event()
    writers: list[int] = []
    original: Callable[..., int] = uploads._write_chunk

    def gated(path: Path, offset: Any, data: bytes, limit: int) -> int:
        writers.append(offset)
        if len(writers) == 1:
            entered.set()
            release.wait(10)
        return original(path, offset, data, limit)

    monkeypatch.setattr(uploads, "_write_chunk", gated)
    try:
        first: asyncio.Task[dict[str, int]] = asyncio.create_task(uploads.append(backup.STAGE_PREFIX, token, 0, _once(b"{" * 10), 10, 100))
        assert await asyncio.to_thread(entered.wait, 5)
        first.cancel()
        await asyncio.sleep(0.05)
        second: asyncio.Task[dict[str, int]] = asyncio.create_task(uploads.append(backup.STAGE_PREFIX, token, 10, _once(b"}" * 5), 5, 100))
        await asyncio.sleep(0.2)
        assert writers == [0], "het volgende stuk wacht op het slot zolang de geannuleerde thread schrijft"
        assert not second.done()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert await asyncio.wait_for(second, 5) == {"received": 15}
    assert uploads.part_path(backup.STAGE_PREFIX, token).read_bytes() == b"{" * 10 + b"}" * 5
