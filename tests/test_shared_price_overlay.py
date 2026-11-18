from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from mealieshopper import price_archive, prices, shared_price_overlay


class Snapshot:
    generation: str = "first"

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows: list[dict[str, Any]] = rows

    def observations(self, ids: list[str], since: str | None = None, until: str | None = None) -> dict[str, list[dict[str, Any]]]:
        return {pid: [dict(row) for row in self.rows if row["product_id"] == pid] for pid in ids}


def observation(pid: str = "1", day: str = "2026-10-01", price: float = 2.0) -> dict[str, Any]:
    return dict(
        store="ah",
        product_id=pid,
        day=day,
        source="ahprijsindex",
        title="AH Melk",
        brand="AH",
        unit_size="1 l",
        image=None,
        url=None,
        price=price,
        seen_at=f"{day}T12:00:00Z",
    )


async def seed(rows: list[dict[str, Any]]) -> None:
    async with prices._connect() as conn:
        await conn.executemany(
            "INSERT INTO price_observations (store,product_id,day,source,title,brand,unit_size,image,url,price,seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [price_archive.normalise_price(row, price_archive.Today()) for row in rows],
        )
        await conn.execute("DELETE FROM shared_price_origins")
        await conn.commit()


@pytest.mark.asyncio
async def test_migration_retains_rows_and_only_marks_full_equivalence(data_dir: Path) -> None:
    public: dict[str, Any] = observation()
    changed: dict[str, Any] = observation("2", price=3.0)
    await seed([public, changed])
    snapshot: Snapshot = Snapshot([public, observation("2")])
    await shared_price_overlay.reconcile(snapshot, batch_size=1)
    async with prices._connect() as conn:
        assert (await conn.execute_fetchall("SELECT COUNT(*) FROM price_observations"))[0][0] == 2
        rows: list[Any] = await conn.execute_fetchall("SELECT product_id,generation FROM shared_price_equivalence")
        assert [tuple(row) for row in rows] == [("1", "first")]
    snapshot.generation = "second"
    snapshot.rows[0] = {**public, "seen_at": "2026-10-01T13:00:00Z"}
    await shared_price_overlay.reconcile(snapshot, batch_size=1)
    async with prices._connect() as conn:
        assert await conn.execute_fetchall("SELECT * FROM shared_price_equivalence WHERE generation='second'") == []
        assert (await conn.execute_fetchall("SELECT projection_generation FROM shared_price_equivalence"))[0][0] == "second"


@pytest.mark.asyncio
async def test_local_write_invalidates_equivalence_in_same_transaction(data_dir: Path) -> None:
    row: dict[str, Any] = observation()
    await seed([row])
    snapshot: Snapshot = Snapshot([row])
    await shared_price_overlay.reconcile(snapshot)
    async with prices._connect() as conn:
        await conn.execute("UPDATE price_observations SET price=9 WHERE store='ah' AND product_id='1'")
        assert await conn.execute_fetchall("SELECT * FROM shared_price_equivalence") == []
        await conn.rollback()
        assert len(await conn.execute_fetchall("SELECT * FROM shared_price_equivalence")) == 1
    await prices.record("ah", [{"id": "1", "title": "AH Melk", "price": 2}], source="ahprijsindex", now="2026-10-01T12:00:00Z")
    await shared_price_overlay.reconcile(snapshot)
    async with prices._connect() as conn:
        assert await conn.execute_fetchall("SELECT * FROM shared_price_equivalence") == []
        assert (await conn.execute_fetchall("SELECT origin FROM shared_price_origins"))[0][0] == "local"


@pytest.mark.asyncio
async def test_reconcile_resumes_and_preserves_unknown_corrections(data_dir: Path) -> None:
    rows: list[dict[str, Any]] = [observation(str(number)) for number in range(7)]
    await seed(rows)
    snapshot: Snapshot = Snapshot(rows)
    assert await shared_price_overlay.reconcile_batch(snapshot, batch_size=2) is False
    async with prices._connect() as conn:
        assert len(await conn.execute_fetchall("SELECT * FROM shared_price_equivalence")) == 2
    await shared_price_overlay.reconcile(snapshot, batch_size=2)
    async with prices._connect() as conn:
        assert len(await conn.execute_fetchall("SELECT * FROM shared_price_equivalence")) == 7
    snapshot.rows = [{**row, "price": 3.0} for row in rows]
    snapshot.generation = "corrected"
    await shared_price_overlay.reconcile(snapshot, batch_size=2)
    merged: dict[str, list[Any]] = shared_price_overlay.merge_observations(snapshot, [dict(rows[0])], {"0": snapshot.rows[:1]}, set())
    assert merged["0"][0]["price"] == 2.0


@pytest.mark.asyncio
async def test_proven_legacy_shadow_allows_central_correction(data_dir: Path) -> None:
    original: dict[str, Any] = observation()
    await seed([original])
    snapshot: Snapshot = Snapshot([original])
    await shared_price_overlay.reconcile(snapshot)
    snapshot.generation = "corrected"
    snapshot.rows = [{**original, "price": 3.5, "seen_at": "2026-10-02T12:00:00Z"}]
    await shared_price_overlay.reconcile(snapshot)
    async with prices._connect() as conn:
        found: dict[str, list[Any]] = await shared_price_overlay.observations(conn, snapshot, "ah", ["1"])
        assert found["1"][0]["price"] == 3.5
        assert (await conn.execute_fetchall("SELECT price FROM price_observations"))[0][0] == 2.0
        assert tuple((await conn.execute_fetchall("SELECT generation,projection_generation FROM shared_price_equivalence"))[0]) == (
            "first",
            "corrected",
        )
    await prices.record("ah", [{"id": "1", "title": "AH Melk", "price": 4}], now="2026-10-01T12:00:00Z")
    async with prices._connect() as conn:
        found = await shared_price_overlay.observations(conn, snapshot, "ah", ["1"])
        assert found["1"][0]["price"] == 4.0


@pytest.mark.asyncio
async def test_real_legacy_import_normalisation_is_equivalent(data_dir: Path) -> None:
    public: dict[str, Any] = {**observation(), "image": "https://example.com/public.jpg", "brand": "", "seen_at": None}
    await seed([public])
    snapshot: Snapshot = Snapshot([public])
    await shared_price_overlay.reconcile(snapshot)
    async with prices._connect() as conn:
        assert len(await conn.execute_fetchall("SELECT * FROM shared_price_equivalence")) == 1
        assert tuple((await conn.execute_fetchall("SELECT image,brand,seen_at FROM price_observations"))[0]) == (
            "",
            None,
            "2026-10-01T00:00:00+00:00",
        )


@pytest.mark.asyncio
async def test_local_change_behind_cursor_does_not_reinsert_stale_proof(data_dir: Path) -> None:
    rows: list[dict[str, Any]] = [observation(str(number)) for number in range(5)]
    await seed(rows)
    snapshot: Snapshot = Snapshot(rows)
    await shared_price_overlay.reconcile_batch(snapshot, batch_size=2)
    async with prices._connect() as conn:
        await conn.execute("UPDATE price_observations SET price=9 WHERE product_id='0'")
        await conn.commit()
    await shared_price_overlay.reconcile(snapshot, batch_size=2)
    async with prices._connect() as conn:
        assert len(await conn.execute_fetchall("SELECT * FROM shared_price_equivalence")) == 4
        assert await conn.execute_fetchall("SELECT * FROM shared_price_equivalence WHERE product_id='0'") == []


@pytest.mark.asyncio
async def test_background_migration_yields_to_foreground_before_batch(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio
    from unittest.mock import AsyncMock

    from mealieshopper import shared_prices

    gate: asyncio.Event = asyncio.Event()
    batch: AsyncMock = AsyncMock(return_value=True)
    monkeypatch.setattr(shared_price_overlay, "reconcile_batch", batch)

    async def background() -> None:
        await gate.wait()
        await shared_price_overlay.reconcile(Snapshot([]))

    work: asyncio.Task[None] = asyncio.create_task(background())
    token: Any = shared_prices.foreground_started()
    try:
        gate.set()
        await asyncio.sleep(0.03)
        batch.assert_not_awaited()
    finally:
        shared_prices.foreground_finished(token)
    await asyncio.wait_for(work, 3)
    batch.assert_awaited_once()


def test_classification_reads_only_selected_product_date_windows() -> None:
    calls: list[tuple[list[str], str | None, str | None]] = []

    class WindowedSnapshot(Snapshot):
        def observations(self, ids: list[str], since: str | None = None, until: str | None = None) -> dict[str, list[dict[str, Any]]]:
            calls.append((ids, since, until))
            assert len(ids) <= 8
            assert since == until == "2026-10-01"
            return super().observations(ids, since, until)

    rows: list[dict[str, Any]] = [observation(str(number)) for number in range(64)]
    from mealieshopper import price_archive

    normalised: list[dict[str, Any]] = [
        dict(zip(shared_price_overlay.COLUMNS, price_archive.normalise_price(row, price_archive.Today()), strict=True)) for row in rows
    ]
    assert len(shared_price_overlay.equivalents(WindowedSnapshot(rows), normalised)) == 64
    assert len(calls) == 8


@pytest.mark.asyncio
async def test_one_migration_batch_has_bounded_sql_work_with_large_legacy_store(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio
    from contextlib import asynccontextmanager, closing

    from mealieshopper import thread_db

    async with prices._connect() as conn:
        await prices._catalogue_version(conn, "ah")

    def legacy() -> None:
        with closing(thread_db.connect()) as conn:
            conn.executemany(
                "INSERT INTO price_observations VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                [
                    ("ah", str(number), "2026-10-01", "ahprijsindex", "AH Melk", "AH", "1 l", "", None, 2.0, "2026-10-01T12:00:00Z")
                    for number in range(20000)
                ],
            )
            conn.execute("DELETE FROM shared_price_origins")
            conn.commit()

    await asyncio.to_thread(legacy)
    work: list[int] = [0]
    original: Any = prices._connect

    def progress() -> int:
        work[0] += 1000
        return 0

    @asynccontextmanager
    async def measured() -> Any:
        async with original() as conn:
            await conn.set_progress_handler(progress, 1000)
            yield conn

    monkeypatch.setattr(prices, "_connect", measured)
    snapshot: Snapshot = Snapshot([observation(str(number)) for number in range(64)])
    assert await shared_price_overlay.reconcile_batch(snapshot, batch_size=64) is False
    assert work[0] < 100000, work[0]


@pytest.mark.asyncio
async def test_persistent_origin_rebuilds_historical_proof_after_derived_reset(data_dir: Path) -> None:
    original: dict[str, Any] = observation()
    await seed([original])
    snapshot: Snapshot = Snapshot([original])
    await shared_price_overlay.reconcile(snapshot)
    async with prices._connect() as conn:
        await conn.execute("DELETE FROM shared_price_equivalence")
        await conn.execute("DELETE FROM shared_price_migration")
        await conn.commit()
    snapshot.generation = "corrected"
    snapshot.rows = [{**original, "price": 3.0}]
    await shared_price_overlay.reconcile(snapshot)
    async with prices._connect() as conn:
        assert tuple((await conn.execute_fetchall("SELECT generation,projection_generation FROM shared_price_equivalence"))[0]) == (
            "first",
            "corrected",
        )
