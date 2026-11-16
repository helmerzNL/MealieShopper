"""Immutable generation lifecycle and exact bounded reader behaviour."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from shared_prices_fixture import build_snapshot

from mealieshopper import shared_prices


def rows() -> list[dict[str, Any]]:
    return [
        {
            "store": "ah",
            "product_id": "p1",
            "source": "ahprijsindex",
            "day": day,
            "title": "Red Band winegums",
            "brand": "Red Band",
            "unit_size": "500 g",
            "price": 1.234,
            "seen_at": None,
            "unknown": {"a": 1},
        }
        for day in ("2026-01-01", "2026-01-08", "2026-01-22")
    ]


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("MEALIESHOPPER_SHARED_PRICES", "true")
    return shared_prices.GenerationStore(tmp_path / "shared")


def publish(store: Any, tmp_path: Path, data: list[dict[str, Any]]) -> dict[str, Any]:
    path: Path = tmp_path / f"{len(list(tmp_path.glob('*fixture*')))}fixture.sqlite"
    descriptor: dict[str, Any] = build_snapshot(path, data)
    store.install(path, descriptor)
    return descriptor


def test_exact_history_and_pinned_generations(store: Any, tmp_path: Path) -> None:
    original: list[dict[str, Any]] = rows()
    first: dict[str, Any] = publish(store, tmp_path, original)
    with store.lease() as snapshot:
        assert snapshot is not None
        assert snapshot.observations(["p1"]) == {"p1": original}
        assert snapshot.observations(["p1"], since="2026-01-02", until="2026-01-21") == {"p1": [original[1]]}
        assert snapshot.page("redband 0,5 kg")["total"] == 1
        assert snapshot.page("winegums 300g")["total"] == 0
        assert snapshot.products(["p1"])["p1"]["unknown"] == {"a": 1}
        second: dict[str, Any] = publish(store, tmp_path, [{**row, "price": 9} for row in original])
        publish(store, tmp_path, [{**row, "price": 10} for row in original])
        store.cleanup()
        assert snapshot.path.exists()
        assert snapshot.generation == first["artifact"]["sha256"]
        assert snapshot.observations(["p1"])["p1"][0]["price"] == 1.234
    store.cleanup()
    assert not (store.root / f"{first['artifact']['sha256']}.sqlite").exists()
    assert (store.root / f"{second['artifact']['sha256']}.sqlite").exists()


def test_cold_start_does_not_hash_trusted_file(store: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    descriptor: dict[str, Any] = publish(store, tmp_path, rows())
    monkeypatch.setattr(hashlib, "sha256", lambda *args: pytest.fail("cold request hashed database"))
    cold: Any = shared_prices.GenerationStore(store.root)
    assert cold.generation() == descriptor["artifact"]["sha256"]
    with cold.lease() as snapshot:
        assert snapshot.products(["p1"])["p1"]["title"] == "Red Band winegums"


@pytest.mark.parametrize("damage", ["checksum", "schema", "integrity", "size"])
def test_invalid_candidate_preserves_active(store: Any, tmp_path: Path, damage: str) -> None:
    first: dict[str, Any] = publish(store, tmp_path, rows())
    candidate: Path = tmp_path / "bad.sqlite"
    descriptor: dict[str, Any] = build_snapshot(candidate, [{**rows()[0], "price": 3}])
    if damage == "schema":
        with sqlite3.connect(candidate) as conn:
            conn.execute("PRAGMA user_version=2")
    elif damage == "integrity":
        with sqlite3.connect(candidate) as conn:
            page_size: int = conn.execute("PRAGMA page_size").fetchone()[0]
            root_page: int = conn.execute("SELECT rootpage FROM sqlite_schema WHERE name='periods_product'").fetchone()[0]
        with candidate.open("r+b") as handle:
            handle.seek((root_page - 1) * page_size)
            handle.write(b"\xff")
    elif damage == "size":
        descriptor["artifact"]["bytes"] += 1
    else:
        descriptor["artifact"]["sha256"] = "b" * 64
        descriptor["artifact"]["path"] = f"shared-prices/v1/ah-ahprijsindex/{descriptor['artifact']['sha256']}.sqlite"
    if damage in {"schema", "integrity"}:
        descriptor["artifact"]["sha256"] = hashlib.sha256(candidate.read_bytes()).hexdigest()
        descriptor["artifact"]["path"] = f"shared-prices/v1/ah-ahprijsindex/{descriptor['artifact']['sha256']}.sqlite"
    with pytest.raises(ValueError):
        store.install(candidate, descriptor)
    assert store.generation() == first["artifact"]["sha256"]


@pytest.mark.parametrize(
    "change",
    [
        lambda x: x.update(format_version=2),
        lambda x: x["datasets"][0]["artifact"].update(path="../evil.sqlite"),
        lambda x: x["datasets"][0]["artifact"].update(bytes=33 * 1024 * 1024),
        lambda x: x["datasets"][0].update(schema_version=True),
    ],
)
def test_manifest_rejected(store: Any, tmp_path: Path, change: Any) -> None:
    descriptor: dict[str, Any] = build_snapshot(tmp_path / "manifest.sqlite", rows())
    manifest: dict[str, Any] = {"format_version": 1, "datasets": [descriptor]}
    change(manifest)
    with pytest.raises(ValueError):
        shared_prices.validate_manifest(json.dumps(manifest).encode())


async def test_refresh_schedule_unchanged_and_offline(store: Any, tmp_path: Path) -> None:
    candidate: Path = tmp_path / "download.sqlite"
    descriptor: dict[str, Any] = build_snapshot(candidate, rows())
    manifest: dict[str, Any] = {"format_version": 1, "datasets": [descriptor]}
    with respx.mock() as mock:
        request: Any = mock.get("https://data.mealieshopper.nl/shared-prices/v1/manifest.json").respond(200, json=manifest)
        download: Any = mock.get(f"https://data.mealieshopper.nl/{descriptor['artifact']['path']}").respond(200, content=candidate.read_bytes())
        assert (await store.refresh_if_due())["changed"]
        assert (await store.refresh_if_due())["state"] == "not_due"
        assert not (await store.refresh_if_due(force=True))["changed"]
        schedule: dict[str, Any] = json.loads((store.root / "schedule.json").read_text())
        import time

        assert 86390 < schedule["next_check"] - time.time() <= 86400
        assert request.call_count == 2
        assert download.call_count == 1
        request.mock(side_effect=httpx.ConnectError("offline"))
        assert (await store.refresh_if_due(force=True))["state"] == "error"
        assert store.generation() == descriptor["artifact"]["sha256"]
        schedule = json.loads((store.root / "schedule.json").read_text())
        assert 890 < schedule["next_check"] - time.time() <= 900
        assert (await store.refresh_if_due())["state"] == "not_due"


def _hold_generation(root: str, ready: Any, release: Any) -> None:
    worker: Any = shared_prices.GenerationStore(Path(root))
    with worker.lease() as snapshot:
        ready.set()
        release.wait(15)
        assert snapshot.observations(["p1"])["p1"][0]["price"] == 1.234


def _hold_updater(root: str, ready: Any) -> None:
    lock: Any = shared_prices.GenerationStore(Path(root))._claim()
    assert lock is not None
    ready.set()
    import time

    time.sleep(30)


def test_multiprocess_leases_and_crashed_updater(store: Any, tmp_path: Path) -> None:
    import multiprocessing

    context: Any = multiprocessing.get_context("spawn")
    ready: Any = context.Event()
    release: Any = context.Event()
    first: dict[str, Any] = publish(store, tmp_path, rows())
    reader: Any = context.Process(target=_hold_generation, args=(str(store.root), ready, release))
    reader.start()
    try:
        assert ready.wait(10)
        publish(store, tmp_path, [{**row, "price": 2} for row in rows()])
        publish(store, tmp_path, [{**row, "price": 3} for row in rows()])
        store.cleanup()
        assert (store.root / f"{first['artifact']['sha256']}.sqlite").exists()
        release.set()
        reader.join(10)
        assert reader.exitcode == 0
        store.cleanup()
        assert not (store.root / f"{first['artifact']['sha256']}.sqlite").exists()
    finally:
        if reader.is_alive():
            reader.terminate()
            reader.join()
    ready.clear()
    updater: Any = context.Process(target=_hold_updater, args=(str(store.root), ready))
    updater.start()
    try:
        assert ready.wait(10)
        assert store._claim() is None
        updater.terminate()
        updater.join(10)
        lock: Any = store._claim()
        assert lock is not None
        lock.close()
    finally:
        if updater.is_alive():
            updater.terminate()
            updater.join()


def test_low_disk_preserves_last_good(store: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from mealieshopper.shared_prices import store as store_module

    first: dict[str, Any] = publish(store, tmp_path, rows())
    candidate: Path = tmp_path / "low-space.sqlite"
    descriptor: dict[str, Any] = build_snapshot(candidate, [{**row, "price": 3} for row in rows()])
    monkeypatch.setattr(store_module.shutil, "disk_usage", lambda path: type("Disk", (), {"free": 0})())
    with pytest.raises(ValueError, match="space"):
        store.install(candidate, descriptor)
    assert store.generation() == first["artifact"]["sha256"]
    assert not (store.root / "candidate.install").exists()


async def test_interrupted_and_oversize_download_preserve_active(store: Any, tmp_path: Path) -> None:
    first: dict[str, Any] = publish(store, tmp_path, rows())
    candidate: Path = tmp_path / "interrupt.sqlite"
    descriptor: dict[str, Any] = build_snapshot(candidate, [{**row, "price": 3} for row in rows()])
    with respx.mock() as mock:
        mock.get("https://data.mealieshopper.nl/shared-prices/v1/manifest.json").respond(200, json={"format_version": 1, "datasets": [descriptor]})
        request: Any = mock.get(f"https://data.mealieshopper.nl/{descriptor['artifact']['path']}").respond(200, content=b"broken")
        assert (await store.refresh_if_due(force=True))["state"] == "error"
        assert store.generation() == first["artifact"]["sha256"]
        request.respond(200, content=b"x" * (descriptor["artifact"]["bytes"] + 1))
        assert (await store.refresh_if_due(force=True))["state"] == "error"
        assert store.generation() == first["artifact"]["sha256"]
        assert not (store.root / "candidate.download").exists()


async def test_cancelled_download_releases_lock(store: Any, tmp_path: Path) -> None:
    import asyncio

    first: dict[str, Any] = publish(store, tmp_path, rows())
    candidate: Path = tmp_path / "cancel.sqlite"
    descriptor: dict[str, Any] = build_snapshot(candidate, [{**row, "price": 3} for row in rows()])
    started: asyncio.Event = asyncio.Event()

    class Waiting(httpx.AsyncByteStream):
        async def __aiter__(self) -> Any:
            started.set()
            await asyncio.Event().wait()
            yield b""

    with respx.mock() as mock:
        mock.get("https://data.mealieshopper.nl/shared-prices/v1/manifest.json").respond(200, json={"format_version": 1, "datasets": [descriptor]})
        mock.get(f"https://data.mealieshopper.nl/{descriptor['artifact']['path']}").mock(return_value=httpx.Response(200, stream=Waiting()))
        update: asyncio.Task[Any] = asyncio.create_task(store.refresh_if_due(force=True))
        await asyncio.wait_for(started.wait(), 5)
        assert (await store.refresh_if_due(force=True))["state"] == "busy"
        update.cancel()
        with pytest.raises(asyncio.CancelledError):
            await update
        assert store.generation() == first["artifact"]["sha256"]
        assert not (store.root / "candidate.download").exists()
        lock: Any = store._claim()
        assert lock is not None
        lock.close()


def test_selected_history_compressed_periods_metadata_presence_and_clipping(store: Any, tmp_path: Path) -> None:
    original: list[dict[str, Any]] = rows()
    original[1]["title"] = "New name"
    del original[1]["seen_at"]
    original.append({**original[-1], "day": "2026-01-29"})
    candidate: Path = tmp_path / "compressed.sqlite"
    descriptor: dict[str, Any] = build_snapshot(candidate, original)
    with sqlite3.connect(candidate) as conn:
        conn.execute("UPDATE price_periods SET last_day='2026-01-29',observation_count=2 WHERE first_day='2026-01-22'")
        conn.execute("DELETE FROM price_periods WHERE first_day='2026-01-29'")
    descriptor["artifact"]["sha256"] = hashlib.sha256(candidate.read_bytes()).hexdigest()
    descriptor["artifact"]["path"] = f"shared-prices/v1/ah-ahprijsindex/{descriptor['artifact']['sha256']}.sqlite"
    store.install(candidate, descriptor)
    with store.lease() as snapshot:
        assert snapshot.observations(["p1"])["p1"] == original
        assert snapshot.observations(["p1"], since="2026-01-23", until="2026-01-30")["p1"] == [original[-1]]
        assert snapshot.observations(["p1"], since="2026-01-09", until="2026-01-21")["p1"] == []
        assert snapshot.family_products(["red band winegums"])["red band winegums"][0]["product_id"] == "p1"
        with pytest.raises(ValueError):
            snapshot.products(["p1"] * 501)


def test_nonfinite_json_and_missing_indexes_rejected(store: Any, tmp_path: Path) -> None:
    from mealieshopper.shared_prices.protocol import strict_json

    with pytest.raises(ValueError):
        strict_json('{"number":1e999}')
    candidate: Path = tmp_path / "unindexed.sqlite"
    descriptor: dict[str, Any] = build_snapshot(candidate, rows())
    with sqlite3.connect(candidate) as conn:
        conn.execute("DROP INDEX products_family")
    descriptor["artifact"]["sha256"] = hashlib.sha256(candidate.read_bytes()).hexdigest()
    descriptor["artifact"]["path"] = f"shared-prices/v1/ah-ahprijsindex/{descriptor['artifact']['sha256']}.sqlite"
    with pytest.raises(ValueError, match="index"):
        store.install(candidate, descriptor)


async def test_cancelled_lock_acquisition_does_not_leak_updater(store: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio
    import threading

    acquired: threading.Event = threading.Event()
    release: threading.Event = threading.Event()
    claim: Any = store._claim

    def slow_claim() -> Any:
        lock: Any = claim()
        acquired.set()
        release.wait(5)
        return lock

    monkeypatch.setattr(store, "_claim", slow_claim)
    update: asyncio.Task[Any] = asyncio.create_task(store.refresh_if_due(force=True))
    assert await asyncio.to_thread(acquired.wait, 5)
    update.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await update
    lock: Any = claim()
    assert lock is not None
    lock.close()


def test_restart_recovers_readonly_staging_and_atomic_pointer_failure(store: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from mealieshopper.shared_prices import store as store_module

    first: dict[str, Any] = publish(store, tmp_path, rows())
    candidate: Path = tmp_path / "restart.sqlite"
    descriptor: dict[str, Any] = build_snapshot(candidate, [{**row, "price": 7} for row in rows()])
    staging: Path = store.root / "candidate.install"
    staging.write_bytes(b"abandoned")
    staging.chmod(0o400)
    store.install(candidate, descriptor)
    assert store.generation() == descriptor["artifact"]["sha256"]
    replace: Any = store_module.os.replace

    def fail_pointer(source: Any, destination: Any) -> None:
        if Path(destination).name == "active.json":
            raise OSError("simulated interrupted pointer activation")
        replace(source, destination)

    monkeypatch.setattr(store_module.os, "replace", fail_pointer)
    with pytest.raises(OSError):
        third: Path = tmp_path / "third.sqlite"
        store.install(third, build_snapshot(third, [{**row, "price": 8} for row in rows()]))
    cold: Any = shared_prices.GenerationStore(store.root)
    assert cold.generation() == descriptor["artifact"]["sha256"]
    assert (store.root / f"{first['artifact']['sha256']}.sqlite").exists()


async def test_missing_download_refreshes_manifest_once(store: Any, tmp_path: Path) -> None:
    candidate: Path = tmp_path / "retry.sqlite"
    descriptor: dict[str, Any] = build_snapshot(candidate, rows())
    with respx.mock() as mock:
        manifest: Any = mock.get("https://data.mealieshopper.nl/shared-prices/v1/manifest.json").respond(
            200, json={"format_version": 1, "datasets": [descriptor]}
        )
        download: Any = mock.get(f"https://data.mealieshopper.nl/{descriptor['artifact']['path']}").respond(404)
        assert (await store.refresh_if_due(force=True))["state"] == "error"
        assert manifest.call_count == 2
        assert download.call_count == 2
        assert store.generation() is None
        with store.lease() as snapshot:
            assert snapshot is None


def test_latest_period_candidates_mask_only_actual_private_days(store: Any, tmp_path: Path) -> None:
    original: list[dict[str, Any]] = rows()
    original.append({**original[-1], "day": "2026-01-29"})
    candidate: Path = tmp_path / "candidates.sqlite"
    descriptor: dict[str, Any] = build_snapshot(candidate, original)
    with sqlite3.connect(candidate) as conn:
        conn.execute("UPDATE price_periods SET last_day='2026-01-29',observation_count=2 WHERE first_day='2026-01-22'")
        conn.execute("DELETE FROM price_periods WHERE first_day='2026-01-29'")
    descriptor["artifact"]["sha256"] = hashlib.sha256(candidate.read_bytes()).hexdigest()
    descriptor["artifact"]["path"] = f"shared-prices/v1/ah-ahprijsindex/{descriptor['artifact']['sha256']}.sqlite"
    store.install(candidate, descriptor)
    with store.lease() as snapshot:
        assert snapshot.candidate_observations(["p1"], "2026-01-01", "2026-01-30", {"p1": ["2026-01-29"]})["p1"] == original[:3]
        assert snapshot.candidate_observations(["p1"], "2026-01-02", "2026-01-25", {"p1": ["2026-01-08"]})["p1"] == [original[2]]
        assert snapshot.candidate_observations(["p1"], "2026-01-09", "2026-01-21", {})["p1"] == []


def test_history_output_and_validator_read_buffers_are_bounded(store: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from mealieshopper.shared_prices import reader
    from mealieshopper.shared_prices.protocol import CHUNK_BYTES, validate_file

    candidate: Path = tmp_path / "bounded.sqlite"
    descriptor: dict[str, Any] = build_snapshot(candidate, rows())
    store.install(candidate, descriptor)
    monkeypatch.setattr(reader, "MAX_OBSERVATIONS", 2)
    with store.lease() as snapshot:
        with pytest.raises(ValueError, match="limit"):
            snapshot.observations(["p1"])
    original_open: Any = Path.open
    reads: list[int] = []

    class Bounded:
        def __init__(self, handle: Any) -> None:
            self.handle: Any = handle

        def __enter__(self) -> Any:
            return self

        def __exit__(self, *args: Any) -> None:
            self.handle.close()

        def read(self, size: int = -1) -> bytes:
            assert 0 < size <= CHUNK_BYTES
            reads.append(size)
            return self.handle.read(size)

    def bounded_open(path: Path, *args: Any, **kwargs: Any) -> Any:
        handle: Any = original_open(path, *args, **kwargs)
        return Bounded(handle) if path == candidate and args == ("rb",) else handle

    monkeypatch.setattr(Path, "open", bounded_open)
    validate_file(candidate, descriptor)
    assert reads


def test_cold_start_uses_supported_last_good_when_active_is_incompatible(store: Any, tmp_path: Path) -> None:
    first: dict[str, Any] = publish(store, tmp_path, rows())
    publish(store, tmp_path, [{**row, "price": 3} for row in rows()])
    pointer: dict[str, Any] = json.loads((store.root / "active.json").read_text())
    pointer["active"]["schema_version"] = 2
    (store.root / "active.json").write_text(json.dumps(pointer))
    assert shared_prices.GenerationStore(store.root).generation() == first["artifact"]["sha256"]


async def test_unwritable_storage_is_isolated_from_scheduled_caller(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEALIESHOPPER_SHARED_PRICES", "true")
    obstacle: Path = tmp_path / "file"
    obstacle.write_bytes(b"not a directory")
    store: Any = shared_prices.GenerationStore(obstacle)
    assert (await store.refresh_if_due(force=True))["state"] == "error"


def test_manifest_limits_duplicate_keys_and_capability_negotiation(tmp_path: Path) -> None:
    from mealieshopper.shared_prices.protocol import MANIFEST_BYTES

    with pytest.raises(ValueError, match="size limit"):
        shared_prices.validate_manifest(b" " * (MANIFEST_BYTES + 1))
    with pytest.raises(ValueError, match="Duplicate"):
        shared_prices.validate_manifest(b'{"format_version":1,"format_version":1,"datasets":[]}')
    descriptor: dict[str, Any] = build_snapshot(tmp_path / "features.sqlite", rows())
    descriptor["required_sqlite"]["features"] = ["fts5"]
    with pytest.raises(ValueError, match="capabilities"):
        shared_prices.validate_manifest(json.dumps({"format_version": 1, "datasets": [descriptor]}).encode())


def test_abandoned_generation_is_cleaned_before_disk_budget_check(store: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from mealieshopper.shared_prices import store as store_module

    first: dict[str, Any] = publish(store, tmp_path, rows())
    publish(store, tmp_path, [{**row, "price": 2} for row in rows()])
    orphan: Path = tmp_path / "orphan.sqlite"
    abandoned: dict[str, Any] = build_snapshot(orphan, [{**row, "price": 3} for row in rows()])
    (store.root / f"{abandoned['artifact']['sha256']}.sqlite").write_bytes(orphan.read_bytes())
    monkeypatch.setattr(store_module, "RETAINED_BYTES", 3 * first["artifact"]["bytes"])
    fourth: dict[str, Any] = publish(store, tmp_path, [{**row, "price": 4} for row in rows()])
    assert store.generation() == fourth["artifact"]["sha256"]
    assert not (store.root / f"{abandoned['artifact']['sha256']}.sqlite").exists()
