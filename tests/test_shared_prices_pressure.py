"""Foreground work takes priority over cooperative snapshot updates."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from quart import Quart, g
from shared_prices_fixture import build_snapshot

from mealieshopper import shared_prices
from mealieshopper.shared_prices import pressure


def observations(count: int = 150) -> list[dict[str, Any]]:
    return [
        {
            "store": "ah",
            "source": "ahprijsindex",
            "product_id": f"p{number}",
            "day": "2026-01-01",
            "title": f"Product {number}",
            "unit_size": "500 g",
            "price": 1.5,
        }
        for number in range(count)
    ]


@pytest.fixture
def policy(monkeypatch: pytest.MonkeyPatch) -> Any:
    instance: Any = pressure.Pressure(cooldown_seconds=0.02)
    monkeypatch.setattr(pressure, "_pressure", instance)
    monkeypatch.setenv("MEALIESHOPPER_SHARED_PRICES", "true")
    monkeypatch.setenv("MEALIESHOPPER_TELEMETRY", "false")
    return instance


def request_app(store: Any, entered: asyncio.Event, release: asyncio.Event) -> Quart:
    app: Quart = Quart(__name__)

    @app.before_request
    async def start() -> None:
        g.pressure = shared_prices.foreground_started()

    @app.after_request
    async def finish(response: Any) -> Any:
        shared_prices.foreground_finished(g.pressure)
        return response

    @app.teardown_request
    async def cleanup(error: BaseException | None) -> None:
        shared_prices.foreground_finished(g.pressure)

    @app.get("/product")
    async def product() -> Any:
        def read() -> Any:
            with store.lease() as snapshot:
                return snapshot.products(["p0"]) if snapshot else {}

        result: Any = await asyncio.to_thread(read)
        entered.set()
        await release.wait()
        return result

    return app


def test_policy_active_requests_cooldown_and_idempotent_finish() -> None:
    now: list[float] = [1.0]
    policy: Any = pressure.Pressure(clock=lambda: now[0], cooldown_seconds=1.0)
    first: Any = policy.started()
    second: Any = policy.started()
    assert policy.delay() is None
    assert policy.delay(excluded=second) is None
    policy.finished(first)
    assert policy.delay(excluded=second) == 0
    now[0] += 0.2
    policy.finished(second)
    assert policy.delay() == pytest.approx(1.0)
    policy.finished(second)
    now[0] += 1.1
    assert policy.delay() == 0


async def test_paused_background_cancellation_and_self_invocation(policy: Any) -> None:
    token: Any = shared_prices.foreground_started()
    # An explicit request may run an updater without waiting on itself.
    await asyncio.wait_for(shared_prices.yield_background(), 1)
    context: Any = __import__("contextvars").Context()
    waiting: asyncio.Task[Any] = context.run(asyncio.create_task, shared_prices.yield_background())
    await asyncio.sleep(0.02)
    assert not waiting.done()
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(waiting, 1)
    shared_prices.foreground_finished(token)
    await asyncio.wait_for(shared_prices.yield_background(), 1)


async def test_real_request_pauses_download_and_can_read_pinned_snapshot(tmp_path: Path, policy: Any) -> None:
    store: Any = shared_prices.GenerationStore(tmp_path / "store")
    old: Path = tmp_path / "old.sqlite"
    store.install(old, build_snapshot(old, observations()))
    new: Path = tmp_path / "new.sqlite"
    descriptor: dict[str, Any] = build_snapshot(new, [{**row, "price": 2.5} for row in observations()])
    body: bytes = new.read_bytes()
    entered: asyncio.Event = asyncio.Event()
    release: asyncio.Event = asyncio.Event()
    stream_started: asyncio.Event = asyncio.Event()
    resume_stream: asyncio.Event = asyncio.Event()
    sent: list[int] = []

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self) -> Any:
            stream_started.set()
            await resume_stream.wait()
            for offset in range(0, len(body), 65536):
                sent.append(offset)
                yield body[offset : offset + 65536]

    app: Quart = request_app(store, entered, release)
    with respx.mock() as mock:
        mock.get("https://data.mealieshopper.nl/shared-prices/v1/manifest.json").respond(200, json={"format_version": 1, "datasets": [descriptor]})
        mock.get(f"https://data.mealieshopper.nl/{descriptor['artifact']['path']}").mock(return_value=httpx.Response(200, stream=Stream()))
        update: asyncio.Task[Any] = asyncio.create_task(store.refresh_if_due(force=True))
        await asyncio.wait_for(stream_started.wait(), 2)
        request: asyncio.Task[Any] = asyncio.create_task(app.test_client().get("/product"))
        await asyncio.wait_for(entered.wait(), 2)
        resume_stream.set()
        await asyncio.sleep(0.03)
        assert not update.done()
        assert len(sent) <= 1
        release.set()
        response: Any = await asyncio.wait_for(request, 2)
        assert (await response.get_json())["p0"]["title"] == "Product 0"
        assert (await asyncio.wait_for(update, 3))["changed"]


@pytest.mark.parametrize("validation_connection", [1, 3])
async def test_real_request_during_sqlite_validation_does_not_deadlock_selection(
    tmp_path: Path, policy: Any, monkeypatch: pytest.MonkeyPatch, validation_connection: int
) -> None:
    from mealieshopper.shared_prices import protocol

    store: Any = shared_prices.GenerationStore(tmp_path / "store")
    candidate: Path = tmp_path / "same.sqlite"
    descriptor: dict[str, Any] = build_snapshot(candidate, observations())
    store.install(candidate, descriptor)
    validating: threading.Event = threading.Event()
    request_read: threading.Event = threading.Event()
    entered: asyncio.Event = asyncio.Event()
    release: asyncio.Event = asyncio.Event()
    original: Any = protocol.connect
    connects: list[int] = [0]

    def instrumented(path: Path) -> Any:
        conn: Any = original(path)
        connects[0] += 1
        selected: bool = connects[0] == validation_connection

        def traced(sql: str) -> None:
            if selected and sql == "PRAGMA integrity_check":
                validating.set()
                assert request_read.wait(3)

        conn.set_trace_callback(traced)
        return conn

    monkeypatch.setattr(protocol, "connect", instrumented)
    app: Quart = request_app(store, entered, release)
    update: asyncio.Task[Any] = asyncio.create_task(asyncio.to_thread(store.install, candidate, descriptor))
    assert await asyncio.to_thread(validating.wait, 3)
    request: asyncio.Task[Any] = asyncio.create_task(app.test_client().get("/product"))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        request_read.set()
        await asyncio.sleep(0.03)
        assert not update.done()
        release.set()
        assert (await asyncio.wait_for(request, 2)).status_code == 200
        await asyncio.wait_for(update, 3)
    finally:
        request_read.set()
        release.set()
        await asyncio.gather(request, update, return_exceptions=True)


async def test_cancelled_validation_releases_updater_while_foreground_remains_active(
    tmp_path: Path, policy: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mealieshopper.shared_prices import protocol

    store: Any = shared_prices.GenerationStore(tmp_path / "store")
    old: Path = tmp_path / "active.sqlite"
    original: dict[str, Any] = build_snapshot(old, observations())
    store.install(old, original)
    candidate: Path = tmp_path / "cancel-validation.sqlite"
    descriptor: dict[str, Any] = build_snapshot(candidate, [{**row, "price": 5} for row in observations()])
    validating: threading.Event = threading.Event()
    permit: threading.Event = threading.Event()
    entered: asyncio.Event = asyncio.Event()
    release: asyncio.Event = asyncio.Event()
    connect: Any = protocol.connect
    paused_sqlite: threading.Event = threading.Event()
    progress: Any = pressure.Budget.progress

    def observed_progress(budget: Any) -> int:
        if policy.delay() is None:
            paused_sqlite.set()
        return progress(budget)

    monkeypatch.setattr(pressure.Budget, "progress", observed_progress)

    def traced_connect(path: Path) -> Any:
        conn: Any = connect(path)

        def trace(sql: str) -> None:
            if sql == "PRAGMA integrity_check":
                validating.set()
                assert permit.wait(3)

        conn.set_trace_callback(trace)
        return conn

    monkeypatch.setattr(protocol, "connect", traced_connect)
    app: Quart = request_app(store, entered, release)
    with respx.mock() as mock:
        mock.get("https://data.mealieshopper.nl/shared-prices/v1/manifest.json").respond(200, json={"format_version": 1, "datasets": [descriptor]})
        mock.get(f"https://data.mealieshopper.nl/{descriptor['artifact']['path']}").respond(200, content=candidate.read_bytes())
        update: asyncio.Task[Any] = asyncio.create_task(store.refresh_if_due(force=True))
        assert await asyncio.to_thread(validating.wait, 3)
        request: asyncio.Task[Any] = asyncio.create_task(app.test_client().get("/product"))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            permit.set()
            assert await asyncio.to_thread(paused_sqlite.wait, 1)
            assert not update.done()
            update.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(update, 1)
            assert not request.done()
            assert store.generation() == original["artifact"]["sha256"]
            lock: Any = store._claim()
            assert lock is not None
            lock.close()
        finally:
            permit.set()
            release.set()
            await asyncio.gather(request, update, return_exceptions=True)


async def test_real_request_pauses_bounded_hash_chunks(tmp_path: Path, policy: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    store: Any = shared_prices.GenerationStore(tmp_path / "store")
    old: Path = tmp_path / "old-hash.sqlite"
    store.install(old, build_snapshot(old, observations()))
    candidate: Path = tmp_path / "hash.sqlite"
    descriptor: dict[str, Any] = build_snapshot(candidate, [{**row, "price": 4} for row in observations()])
    hashing: threading.Event = threading.Event()
    permit: threading.Event = threading.Event()
    entered: asyncio.Event = asyncio.Event()
    release: asyncio.Event = asyncio.Event()
    reads: list[int] = []
    original_open: Any = Path.open

    class HashReader:
        def __init__(self, handle: Any) -> None:
            self.handle: Any = handle

        def __enter__(self) -> Any:
            return self

        def __exit__(self, *args: Any) -> None:
            self.handle.close()

        def read(self, size: int) -> bytes:
            reads.append(size)
            if len(reads) == 1:
                hashing.set()
                assert permit.wait(3)
            return self.handle.read(size)

    def instrumented_open(path: Path, *args: Any, **kwargs: Any) -> Any:
        handle: Any = original_open(path, *args, **kwargs)
        return HashReader(handle) if path == candidate and args == ("rb",) else handle

    monkeypatch.setattr(Path, "open", instrumented_open)
    app: Quart = request_app(store, entered, release)
    update: asyncio.Task[Any] = asyncio.create_task(asyncio.to_thread(store.install, candidate, descriptor))
    assert await asyncio.to_thread(hashing.wait, 3)
    request: asyncio.Task[Any] = asyncio.create_task(app.test_client().get("/product"))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        permit.set()
        await asyncio.sleep(0.03)
        assert reads == [65536]
        assert not update.done()
        release.set()
        assert (await asyncio.wait_for(request, 2)).status_code == 200
        await asyncio.wait_for(update, 3)
        assert all(size == 65536 for size in reads)
    finally:
        permit.set()
        release.set()
        await asyncio.gather(request, update, return_exceptions=True)
