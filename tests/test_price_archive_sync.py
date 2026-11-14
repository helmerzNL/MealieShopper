"""Automatic shared weekly prices use bounded documents and durable checkpoints."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from mealieshopper import price_archive, prices, thread_db


def weekly(product: str = "123", value: float = 2.5, **changes: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "store": "ah",
        "product_id": product,
        "day": "2022-07-25",
        "source": "ahprijsindex",
        "title": "Product",
        "price": value,
        "seen_at": "2026-11-13T00:00:00+00:00",
    }
    return {**row, **changes}


class Site:
    def __init__(self) -> None:
        self.docs: dict[str, bytes] = {}
        self.calls: list[str] = []
        self.bad_hash: bool = False

    def put(self, name: str, rows: list[Any]) -> None:
        self.docs[f"ah/prices/ahprijsindex-{name}.json"] = json.dumps(rows).encode()

    async def get(self, path: str, limit: int) -> bytes:
        self.calls.append(path)
        if path != "index.json":
            return self.docs[path]
        entries: list[dict[str, Any]] = [
            {"path": name, "bytes": len(body), "sha256": "0" * 64 if self.bad_hash else hashlib.sha256(body).hexdigest()}
            for name, body in self.docs.items()
        ]
        return json.dumps({"collections": [{"name": "ah", "files": entries}]}).encode()


@pytest.fixture
async def synced(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    module: Any = importlib.import_module("mealieshopper.price_archive_sync")
    monkeypatch.setenv("MEALIESHOPPER_SHARED_PRICES", "true")
    site: Site = Site()
    site.put("2022-001", [weekly()])
    monkeypatch.setattr(price_archive, "_site_get", site.get)
    await module.ensure_schema()
    return module, site


def rows(table: str = "price_observations") -> list[sqlite3.Row]:
    with closing(thread_db.connect()) as conn:
        return conn.execute(f"SELECT * FROM {table}").fetchall()


async def test_persistent_unchanged_skip_and_only_changed_file(synced: Any) -> None:
    module, site = synced
    site.put("2023-001", [weekly("456")])
    async with prices._connect() as conn:
        await prices._catalogue_version(conn, "ah")  # de versie is per winkel
    result: dict[str, Any] = await module.sync_site()
    assert result["changed"] == 2
    assert len(rows()) == 2
    site.calls.clear()
    module = importlib.reload(module)
    with closing(thread_db.connect()) as conn:
        conn.execute("CREATE TRIGGER refuse_write BEFORE INSERT ON price_observations BEGIN SELECT RAISE(ABORT, 'write'); END")
        conn.commit()
    await module.sync_site()
    assert site.calls == ["index.json"]
    with closing(thread_db.connect()) as conn:
        conn.execute("DROP TRIGGER refuse_write")
        conn.commit()
    site.put("2023-001", [weekly("456", 3.0)])
    site.calls.clear()
    await module.sync_site()
    assert site.calls == ["index.json", "ah/prices/ahprijsindex-2023-001.json"]
    assert {row["product_id"]: row["price"] for row in rows()} == {"123": 2.5, "456": 3.0}


async def test_direct_observation_priority(synced: Any) -> None:
    module, site = synced
    await module.sync_site()
    with closing(thread_db.connect()) as conn:
        conn.execute("UPDATE price_observations SET source='search', price=9")
        conn.commit()
    site.put("2022-001", [weekly(value=4)])
    await module.sync_site()
    assert (rows()[0]["source"], rows()[0]["price"]) == ("search", 9)


@pytest.mark.parametrize("failure", ["checksum", "row", "duplicate", "sql", "source", "root"])
async def test_failed_document_rolls_back_and_retries(synced: Any, failure: str) -> None:
    module, site = synced
    if failure == "checksum":
        site.bad_hash = True
    elif failure == "row":
        site.put("2022-001", [weekly(), weekly("456", -1)])
    elif failure == "duplicate":
        site.put("2022-001", [weekly(), weekly(value=9)])
    elif failure == "source":
        site.put("2022-001", [weekly(source="search")])
    elif failure == "root":
        site.docs["ah/prices/ahprijsindex-2022-001.json"] = b"{}"
    else:
        with closing(thread_db.connect()) as conn:
            conn.execute("CREATE TRIGGER refuse_write BEFORE INSERT ON price_observations BEGIN SELECT RAISE(ABORT, 'write'); END")
            conn.commit()
    with pytest.raises(Exception):
        await module.sync_site()
    assert rows() == []
    assert rows("shared_price_documents") == []
    with closing(thread_db.connect()) as conn:
        conn.execute("DROP TRIGGER IF EXISTS refuse_write")
        conn.commit()
    site.bad_hash = False
    site.put("2022-001", [weekly()])
    await module.sync_site()
    assert len(rows()) == 1


async def test_concurrent_worker_cancel_and_expired_lease(synced: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    module, site = synced
    entered: asyncio.Event = asyncio.Event()
    release: asyncio.Event = asyncio.Event()

    async def slow(path: str, limit: int) -> bytes:
        entered.set()
        await release.wait()
        return await site.get(path, limit)

    monkeypatch.setattr(price_archive, "_site_get", slow)
    task: asyncio.Task[Any] = asyncio.create_task(module.sync_site())
    await entered.wait()
    assert (await module.sync_site())["busy"]
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    monkeypatch.setattr(price_archive, "_site_get", site.get)
    with closing(thread_db.connect()) as conn:
        conn.execute("UPDATE shared_price_sync SET owner='crashed', lease_until=0")
        conn.commit()
    await module.sync_site()
    assert len(rows()) == 1


async def test_interval_failure_retry_and_reset(synced: Any) -> None:
    module, site = synced
    site.bad_hash = True
    assert "error" in await module.run_if_due(now=1000)
    assert await module.run_if_due(now=1001) is None
    site.bad_hash = False
    await module.run_if_due(now=1900)
    assert len(rows()) == 1
    site.calls.clear()
    assert await module.run_if_due(now=1901) is None
    with closing(thread_db.connect()) as conn:
        module.reset(conn)
        conn.commit()
    await module.run_if_due(now=1901)
    assert site.calls == ["index.json", "ah/prices/ahprijsindex-2022-001.json"]


async def test_scheduler_checks_shared_data_without_repeating_store_sync(synced: Any) -> None:
    from datetime import datetime, timezone

    from mealieshopper import offers_schedule

    module, site = synced
    moment: datetime = datetime(2026, 11, 13, 6, tzinfo=timezone.utc)
    with mock.patch.object(offers_schedule, "_claim", mock.AsyncMock(return_value=False)), mock.patch.object(offers_schedule, "run_all") as stores:
        await offers_schedule.run_if_due(moment)
    assert len(rows()) == 1
    stores.assert_not_called()


async def test_destructive_archive_replace_invalidates_checkpoint(synced: Any, tmp_path: Path) -> None:
    module, site = synced
    await module.sync_site()
    staged: Path = tmp_path / "empty.rows"
    staged.write_text("")
    await price_archive.ensure_schema()
    await asyncio.to_thread(price_archive.apply, staged, True)
    assert rows("shared_price_documents") == []
    assert rows("shared_price_sync") == []


async def test_backup_restore_invalidates_checkpoint(synced: Any) -> None:
    from mealieshopper import backup

    module, site = synced
    await module.sync_site()
    await backup.ensure_schemas()
    plan: backup.Plan = backup.Plan()
    await asyncio.to_thread(backup.replace_everything, plan, {}, lambda conn, result: None, None)
    assert rows("shared_price_documents") == []
    assert rows("shared_price_sync") == []


async def test_cancelled_claim_releases_lease(synced: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    module, site = synced
    original: Any = module._claim
    entered: threading.Event = threading.Event()
    release: threading.Event = threading.Event()

    def slow_claim(*args: Any) -> bool:
        claimed: bool = original(*args)
        entered.set()
        release.wait(5)
        return claimed

    monkeypatch.setattr(module, "_claim", slow_claim)
    task: asyncio.Task[Any] = asyncio.create_task(module.sync_site())
    await asyncio.to_thread(entered.wait, 5)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert rows("shared_price_sync")[0]["owner"] is None


async def test_factory_reset_discards_derived_state(synced: Any) -> None:
    from mealieshopper import factory_reset

    module, site = synced
    await module.sync_site()
    await factory_reset.wipe_data()
    await module.ensure_schema()
    assert rows("shared_price_documents") == []
    assert rows("shared_price_sync") == []


async def test_later_direct_observation_replaces_weekly(synced: Any) -> None:
    module, site = synced
    await module.sync_site()
    assert await prices.record("ah", [{"id": "123", "title": "Product", "price": 4}], now="2022-07-25T09:00:00+00:00") == 1
    assert (rows()[0]["source"], rows()[0]["price"]) == ("search", 4)


async def test_index_filter_bounds_and_document_removal(synced: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    module, site = synced
    site.docs["ah/prices/other.json"] = b"[]"
    site.docs["ah/prices/../outside.json"] = b"[]"
    await module.sync_site()
    assert site.calls == ["index.json", "ah/prices/ahprijsindex-2022-001.json"]
    site.docs.pop("ah/prices/ahprijsindex-2022-001.json")
    await module.sync_site()
    assert len(rows()) == 1
    site.put("2023-001", [weekly("456")])
    monkeypatch.setattr(module, "MAX_DOCUMENT_BYTES", 1)
    with pytest.raises(ValueError, match="4 MiB"):
        await module.sync_site()
    assert len(rows("shared_price_documents")) == 1


async def test_same_price_metadata_correction_and_identical_noop(synced: Any) -> None:
    module, site = synced
    await module.sync_site()
    site.put("2022-001", [weekly(title="Corrected", brand="Brand", unit_size="2 l", seen_at="2026-11-13T10:00:00+00:00")])
    result: dict[str, Any] = await module.sync_site()
    assert result["changed"] == 1
    row: sqlite3.Row = rows()[0]
    assert (row["title"], row["brand"], row["unit_size"], row["seen_at"]) == ("Corrected", "Brand", "2 l", "2026-11-13T10:00:00+00:00")
    with closing(thread_db.connect()) as conn:
        conn.execute("CREATE TRIGGER refuse_update BEFORE UPDATE ON price_observations BEGIN SELECT RAISE(ABORT, 'write'); END")
        conn.commit()
    site.docs["ah/prices/ahprijsindex-2022-001.json"] += b"\n"
    result = await module.sync_site()
    assert result["changed"] == 0
    assert result["documents"] == 1


async def test_a_cancelled_sync_whose_thread_fails_is_not_reported_by_asyncio(synced: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """`_thread` wachtte met `asyncio.shield` op de thread. Werd de synchronisatie geannuleerd en faalde de thread daarna, dan
    meldde Python 3.15 de fout bij de loop ("exception in shielded future"), al haalt `_thread` hem zelf op. Nu wacht `_thread`
    met `tasks.share`, zoals uploads.append en price_archive.run_job."""
    import threading

    module, site = synced
    errors: list[dict[str, Any]] = []
    asyncio.get_running_loop().set_exception_handler(lambda loop, context: errors.append(context))
    entered: threading.Event = threading.Event()
    release: threading.Event = threading.Event()

    def failing(*args: Any) -> bool:
        entered.set()
        release.wait(5)
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(module, "_unchanged", failing)
    task: asyncio.Task[Any] = asyncio.create_task(module.sync_site())
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.sleep(0.05)
    finally:
        release.set()
    with pytest.raises(sqlite3.OperationalError):  # de fout van de thread gaat voor de annulering, zoals `_thread` het al deed
        await task
    await asyncio.sleep(0.05)
    assert errors == []
    assert rows("shared_price_sync")[0]["owner"] is None  # de lease is vrij, ook na de fout in de thread
