"""De meting per aanvraag achter `MEALIESHOPPER_TELEMETRY` (mealieshopper/telemetry.py).

Aanleiding (2026-11-13): /offers laadde in productie geregeld tien seconden, en niets in de app zei welke route dat was of waar
de tijd bleef. De tests hier bewaken dat de vlag standaard niets doet, en dat ze aan de duur, de database, het verpakken en
een stilstaande event loop meet.
"""

from __future__ import annotations

import asyncio
import gc
import logging
import os
import threading
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from quart import Quart
from quart.typing import TestClientProtocol

from mealieshopper import auth, gc_watch, loop_guard, telemetry
from mealieshopper.app import create_app


@pytest.fixture
def measured_app(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Quart:
    monkeypatch.setenv("MEALIESHOPPER_TELEMETRY", "true")
    monkeypatch.setenv(
        "MEALIESHOPPER_TELEMETRY_SLOW_MS", "0"
    )  # elke aanvraag telt als traag, zodat de tests ze terugzien
    app: Quart = create_app()

    @app.get("/api/auth/test-query")  # onder /api/auth/: zonder inloggen bereikbaar
    async def query() -> dict[str, Any]:
        async with auth.connect() as conn:
            rows: Any = await conn.execute_fetchall("SELECT ? AS secret", ("hunter2",))
            async with conn.execute("SELECT 2") as cursor:
                await cursor.fetchone()
        return {"rows": len(rows), "filler": "x" * 5000}

    @app.get("/api/auth/test-block")
    async def block() -> str:
        time.sleep(0.4)  # noqa: ASYNC251 - de test wil juist een route die de event loop vasthoudt
        return "ok"

    return app


@pytest.fixture
async def measured(measured_app: Quart) -> AsyncIterator[TestClientProtocol]:
    async with measured_app.test_app() as test_app:
        yield test_app.test_client()


def stats(app: Quart) -> telemetry.Telemetry:
    return app.extensions["mealieshopper_telemetry"]


async def test_off_by_default(client: TestClientProtocol, app: Quart) -> None:
    response: Any = await client.get("/healthz")
    assert response.status_code == 200
    assert "Server-Timing" not in response.headers
    assert "mealieshopper_telemetry" not in app.extensions
    assert (await client.get("/api/telemetry")).status_code in {401, 403, 404}
    assert telemetry.current() is None


async def test_watch_does_nothing_without_a_request(data_dir: Path) -> None:
    async with auth.connect() as conn:
        assert "_execute" not in vars(conn)  # de verbinding is niet aangeraakt
        assert next(iter(await conn.execute_fetchall("SELECT 1")))[0] == 1


async def test_request_is_timed_with_its_route(
    measured: TestClientProtocol, measured_app: Quart
) -> None:
    response: Any = await measured.get("/healthz")
    assert response.status_code == 200
    assert response.headers["Server-Timing"].startswith("total;dur=")
    snapshot: dict[str, Any] = stats(measured_app).snapshot()
    route: dict[str, Any] = next(
        item for item in snapshot["routes"] if item["route"] == "GET /healthz"
    )
    assert route["count"] == 1
    assert route["maxMs"] >= route["p50Ms"] > 0
    assert snapshot["slow"][0]["target"] == "/healthz"
    assert snapshot["slow"][0]["status"] == 200


async def test_database_time_and_slowest_query(
    measured: TestClientProtocol, measured_app: Quart
) -> None:
    """Elke bewerking van een aiosqlite-verbinding telt mee. Faalt dit na een upgrade van aiosqlite, dan loopt een bewerking
    niet meer door `Connection._execute` en moet `telemetry.watch` mee veranderen."""
    response: Any = await measured.get("/api/auth/test-query")
    assert response.status_code == 200
    assert "db;dur=" in response.headers["Server-Timing"]
    recorded: dict[str, Any] = stats(measured_app).snapshot()["slow"][0]
    assert recorded["route"] == "/api/auth/test-query"
    assert recorded["connections"] >= 1  # die van de route, en die van de controle op inloggen ervoor
    assert (
        recorded["phases"]["db"]["count"] >= 4
    )  # connect, de query met fetchall, de cursor met fetchone, close
    assert recorded["phases"]["db"]["ms"] > 0
    assert recorded["slowestQuery"]["sql"]
    assert "hunter2" not in str(
        recorded
    )  # de waarden van een query worden niet bewaard


async def test_gzip_is_a_phase(
    measured: TestClientProtocol, measured_app: Quart
) -> None:
    response: Any = await measured.get(
        "/api/auth/test-query", headers={"Accept-Encoding": "gzip"}
    )
    assert response.headers["Content-Encoding"] == "gzip"
    recorded: dict[str, Any] = stats(measured_app).snapshot()["slow"][0]
    assert recorded["phases"]["gzip"]["count"] == 1
    assert 0 < recorded["bytes"] < 5000  # de bytes die de deur uitgingen, verpakt


async def test_only_safe_parameters_are_kept(
    measured: TestClientProtocol, measured_app: Quart
) -> None:
    await measured.get("/healthz?store=ah&q=geheim&code=abc123")
    assert stats(measured_app).snapshot()["slow"][0]["target"] == "/healthz?store=ah"


async def test_slow_request_is_logged(
    measured: TestClientProtocol, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="mealieshopper.telemetry"):
        await measured.get("/api/auth/test-query")
    lines: list[str] = [
        record.getMessage()
        for record in caplog.records
        if record.name == "mealieshopper.telemetry"
    ]
    assert any(
        line.startswith("Trage aanvraag: GET /api/auth/test-query 200 in ") and "db " in line
        for line in lines
    )


async def test_fast_request_is_not_logged(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("MEALIESHOPPER_TELEMETRY", "1")
    monkeypatch.setenv("MEALIESHOPPER_TELEMETRY_SLOW_MS", "60000")
    app: Quart = create_app()
    with caplog.at_level(logging.WARNING, logger="mealieshopper.telemetry"):
        async with app.test_app() as test_app:
            await test_app.test_client().get("/healthz")
    assert not [
        record for record in caplog.records if record.name == "mealieshopper.telemetry"
    ]
    snapshot: dict[str, Any] = stats(app).snapshot()
    assert snapshot["slow"] == []
    assert (
        snapshot["routes"][0]["count"] == 1
    )  # de cijfers per route tellen wel elke aanvraag


@pytest.mark.may_block("de test blokkeert de event loop met opzet")
async def test_blocked_event_loop_is_reported(
    measured: TestClientProtocol, measured_app: Quart, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="mealieshopper.telemetry"):
        await measured.get("/api/auth/test-block")
        await asyncio.sleep(0.1)  # de bewaker meldt pas als de loop weer aan de beurt is
    stalls: list[dict[str, Any]] = stats(measured_app).snapshot()["stalls"]
    assert stalls
    stall: dict[str, Any] = stalls[0]
    assert stall["ms"] >= telemetry.STALL_SECONDS * 1000
    assert stall["active"] == ["GET /api/auth/test-block"]
    assert any("test_telemetry.py" in line and "block" in line for line in stall["stack"])
    assert any(
        "De event loop stond" in record.getMessage()
        and "/api/auth/test-block" in record.getMessage()
        and "test_telemetry.py" in record.getMessage()
        for record in caplog.records
    )


def test_a_stall_names_the_thread_that_held_the_gil(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """De loop wachtte in de selector terwijl een rekenthread bezig was: de melding en `snapshot` noemen die thread."""
    with open(threading.__file__, encoding="utf-8") as source:  # een regel die wacht, zoals `Condition.wait` doet
        waiting: int = next(number for number, line in enumerate(source, 1) if "waiter.acquire()" in line)
    stall: loop_guard.Stall = loop_guard.Stall(
        0.177,
        (
            loop_guard.Frame(
                "/usr/lib/python3.12/asyncio/selector_events.py", 132, "_read_from_self"
            ),
        ),
        (
            loop_guard.ThreadStack(
                "compute-0", (loop_guard.Frame("/app/mealieshopper/prices.py", 1234, "_rows"),)
            ),
            loop_guard.ThreadStack(
                "compute-1",
                (loop_guard.Frame(threading.__file__, waiting, "wait"),),
            ),
        ),
    )
    measured: telemetry.Telemetry = telemetry.Telemetry(slow_seconds=60)
    with caplog.at_level(logging.WARNING, logger="mealieshopper.telemetry"):
        measured.stalled(stall)
    recorded: dict[str, Any] = measured.snapshot()["stalls"][0]
    assert recorded["threads"] == ["compute-0 prices.py:1234 _rows"]
    assert recorded["stack"] == ["selector_events.py:132 _read_from_self"]
    assert any(
        "Andere threads: compute-0 prices.py:1234 _rows (1 wachtend)"
        in record.getMessage()
        for record in caplog.records
    )


def test_a_stall_in_the_app_has_no_threads() -> None:
    measured: telemetry.Telemetry = telemetry.Telemetry(slow_seconds=60)
    measured.stalled(
        loop_guard.Stall(
            0.3,
            (loop_guard.Frame(os.path.join(loop_guard.APP_DIR, "app.py"), 9, "route"),),
        )
    )
    assert measured.snapshot()["stalls"][0]["threads"] == []


def test_a_long_collection_is_kept_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    measured: telemetry.Telemetry = telemetry.Telemetry(slow_seconds=60)
    with caplog.at_level(logging.WARNING, logger="mealieshopper.telemetry"):
        measured.collected(gc_watch.Collection(2, 0.15, "compute-0", 0))
        measured.collected(gc_watch.Collection(1, 0.03, "MainThread", 12))
    assert [entry["ms"] for entry in measured.snapshot()["gc"]] == [30.0, 150.0]
    assert measured.snapshot()["gc"][1]["thread"] == "compute-0"
    logged: list[str] = [record.getMessage() for record in caplog.records if record.name == "mealieshopper.telemetry"]
    assert logged == ["Een ronde van de garbage collector hield de GIL vast: GC generatie 2 150 ms in compute-0"]


def _watches() -> int:
    """Hoeveel metingen van de GC er hangen: de tests zelf hebben er al een (`loop_watch.GC`)."""
    return sum(isinstance(getattr(callback, "__self__", None), gc_watch.GcWatch) for callback in gc.callbacks)


async def test_the_gc_is_watched_only_when_asked(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEALIESHOPPER_TELEMETRY", "true")
    monkeypatch.setenv("PASSKEY_AUTH_ENABLED", "false")  # zonder inloggen is iedereen beheerder
    before: int = _watches()
    async with create_app().test_app():
        assert _watches() == before  # standaard uit: de meting kost een aanroep per ronde
    monkeypatch.setenv("MEALIESHOPPER_TELEMETRY_GC", "true")
    app: Quart = create_app()
    async with app.test_app() as test_app:
        assert _watches() == before + 1
        body: dict[str, Any] = await (await test_app.test_client().get("/api/telemetry")).get_json()
        assert "gc" in body
    assert _watches() == before


def test_the_stall_limit_is_a_hundred_milliseconds() -> None:
    assert telemetry.STALL_SECONDS == 0.1


async def test_snapshot_route_is_for_the_admin(measured: TestClientProtocol, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PASSKEY_AUTH_ENABLED", "true")  # ook als een eerdere test het inloggen uit liet staan
    assert auth.admin_only("/api/telemetry")
    assert (await measured.get("/api/telemetry")).status_code == 401  # niet ingelogd


async def test_snapshot_route(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEALIESHOPPER_TELEMETRY", "true")
    monkeypatch.setenv("PASSKEY_AUTH_ENABLED", "false")  # zonder inloggen is iedereen beheerder
    app: Quart = create_app()
    async with app.test_app() as test_app:
        response: Any = await test_app.test_client().get("/api/telemetry")
        assert response.status_code == 200
        body: dict[str, Any] = await response.get_json()
    assert {"since", "slowMs", "stallMs", "routes", "slow", "stalls"} <= set(body)
    assert body["slowMs"] == telemetry.DEFAULT_SLOW_MS


def test_routes_are_capped() -> None:
    measured: telemetry.Telemetry = telemetry.Telemetry(slow_seconds=60)
    for number in range(telemetry.MAX_ROUTES + 5):
        recording: telemetry.Recording = telemetry.Recording(
            method="GET", target=f"/{number}"
        )
        recording.route = f"/{number}"
        measured.finish(recording)
    assert len(measured.routes) == telemetry.MAX_ROUTES + 1
    assert measured.routes[telemetry.OTHER_ROUTES].count == 5


@pytest.mark.parametrize(
    ("value", "expected"), [("", 500), ("250", 250), ("0", 0), ("-5", 0), ("snel", 500)]
)
def test_slow_threshold(
    monkeypatch: pytest.MonkeyPatch, value: str, expected: int
) -> None:
    monkeypatch.setenv("MEALIESHOPPER_TELEMETRY_SLOW_MS", value)
    assert telemetry.slow_ms() == expected
