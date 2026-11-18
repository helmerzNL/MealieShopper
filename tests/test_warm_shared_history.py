"""Ready history reads keep receipt decoding within the selected twin groups."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from shared_prices_fixture import build_snapshot

from mealieshopper import offer_categories, price_import, prices, shared_price_overlay, shared_price_readmodel, shared_price_transition, shared_prices

TODAY: str = "2026-10-03"


async def fixture_history(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(prices, "_now", lambda: TODAY + "T12:00:00Z")
    rows: list[dict[str, Any]] = [
        dict(
            store="ah",
            product_id=pid,
            day=day,
            source="ahprijsindex",
            title=title,
            brand="AH",
            unit_size=unit,
            image=None,
            url=None,
            price=price,
            seen_at=day + "T12:00:00Z",
            category=offer_categories.offer_category("ah", title),
        )
        for pid, day, title, unit, price in [
            ("1", "2026-09-24", "AH Melk", "1 l", 2.0),
            ("2", "2026-10-01", "AH Melk", "1 l", 2.0),
            ("3", "2026-10-01", "AH Brood", "500 g", 3.0),
        ]
    ]
    async with prices._connect() as conn:
        await conn.executemany(
            "INSERT INTO price_observations (store,product_id,day,source,title,brand,unit_size,image,url,price,seen_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            [tuple(item[key] for key in shared_price_overlay.COLUMNS) for item in rows],
        )
        await conn.execute("DELETE FROM shared_price_origins")
        await conn.commit()
    async with price_import._connect() as conn:
        await conn.executemany(
            "INSERT INTO imported_purchases(store,order_ref,position,day,title,quantity,paid,regular,imported_at) VALUES('ah','own',?,'2026-10-01',?,1,?,2.0,'now')",
            [(0, "AH Melk", 1.8), (1, "AH Brood", 2.8), (2, "Receipt only", 4.0)],
        )
        await conn.commit()
    path: Path = data_dir / "warm.sqlite"
    descriptor: dict[str, Any] = build_snapshot(path, rows)
    shared_prices.GenerationStore(data_dir / "shared-prices").install(path, descriptor)


@pytest.mark.asyncio
@pytest.mark.parametrize("question", [{}, {"product": "1"}, {"query": "brood", "pin": "1"}, {"query": "receipt"}])
async def test_ready_receipts_are_selected_and_never_staged(data_dir: Path, monkeypatch: pytest.MonkeyPatch, question: dict[str, Any]) -> None:
    await fixture_history(data_dir, monkeypatch)
    expected: dict[str, Any] = await prices.history("ah", today=TODAY, **question)
    monkeypatch.setenv("MEALIESHOPPER_SHARED_PRICES", "true")
    await shared_price_overlay.prepare()
    original_stored: Any = shared_price_readmodel._stored_receipts
    original_metadata: Any = shared_price_transition._receipt_metadata
    original_purchases: Any = prices._purchases
    requested: list[list[str] | None] = []
    staged: list[int] = []

    async def stored(conn: Any, store: str, today: str, ids: list[str] | None = None) -> list[dict[str, Any]]:
        requested.append(ids)
        return await original_stored(conn, store, today, ids)

    def metadata(receipts: dict[str, list[dict[str, Any]]]) -> list[tuple[Any, ...]]:
        staged.append(sum(len(lines) for lines in receipts.values()))
        return original_metadata(receipts)

    async def purchases(conn: Any, store: str, ids: list[str], today: str, receipts: list[dict[str, Any]]) -> dict[str, Any]:
        assert {row["product_id"] for row in receipts} <= set(ids)
        return await original_purchases(conn, store, ids, today, receipts)

    monkeypatch.setattr(prices, "_purchases", purchases)
    monkeypatch.setattr(shared_price_readmodel, "_stored_receipts", stored)
    monkeypatch.setattr(shared_price_transition, "_receipt_metadata", metadata)
    actual: dict[str, Any] = await prices.history("ah", today=TODAY, **question)
    assert actual == expected
    assert requested and all(ids is not None for ids in requested), requested
    assert not any(staged), staged
    if question.get("product") == "1":
        assert any(set(ids or []) == {"1", "2"} for ids in requested), requested


@pytest.mark.asyncio
async def test_ready_bought_and_facets_match_legacy(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    await fixture_history(data_dir, monkeypatch)
    questions: list[dict[str, Any]] = [
        {"bought": True},
        {"bought": True, "product": "1"},
        {"brands": ["AH"]},
        {"brands_out": ["AH"]},
        {"categories": [offer_categories.offer_category("ah", "AH Melk")]},
        {"query": "absent", "pin": "1"},
    ]
    expected: list[dict[str, Any]] = [await prices.history("ah", today=TODAY, **question) for question in questions]
    monkeypatch.setenv("MEALIESHOPPER_SHARED_PRICES", "true")
    await shared_price_overlay.prepare()
    for question, golden in zip(questions, expected, strict=True):
        assert await prices.history("ah", today=TODAY, **question) == golden, question


@pytest.mark.asyncio
@pytest.mark.parametrize("stale", [False, True])
async def test_unready_receipts_are_resolved_once_and_keep_receipt_only_metadata(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch, stale: bool
) -> None:
    await fixture_history(data_dir, monkeypatch)
    monkeypatch.setenv("MEALIESHOPPER_SHARED_PRICES", "true")
    if stale:
        await shared_price_overlay.prepare()
    today: str = "2026-10-04" if stale else TODAY
    expected: dict[str, Any] = await prices.history("ah", query="receipt", today=today)
    original_rows: Any = shared_price_readmodel.receipt_rows
    requests: list[list[str] | None] = []

    async def receipt_rows(conn: Any, snapshot: Any, store: str, day: str, ids: list[str] | None = None) -> list[dict[str, Any]]:
        requests.append(ids)
        return await original_rows(conn, snapshot, store, day, ids)

    monkeypatch.setattr(shared_price_readmodel, "receipt_rows", receipt_rows)
    assert await prices.history("ah", query="receipt", today=today) == expected
    assert expected["total"] == 1
    assert requests == [None], requests


@pytest.mark.asyncio
async def test_ready_bought_reads_all_receipts_once_without_staging(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    await fixture_history(data_dir, monkeypatch)
    expected: dict[str, Any] = await prices.history("ah", bought=True, today=TODAY)
    monkeypatch.setenv("MEALIESHOPPER_SHARED_PRICES", "true")
    await shared_price_overlay.prepare()
    original: Any = shared_price_readmodel.receipt_rows
    requested: list[list[str] | None] = []

    async def receipts(conn: Any, snapshot: Any, store: str, today: str, ids: list[str] | None = None) -> list[dict[str, Any]]:
        requested.append(ids)
        return await original(conn, snapshot, store, today, ids)

    def no_staging(*args: Any) -> Any:
        raise AssertionError("ready receipt metadata staged")

    monkeypatch.setattr(shared_price_readmodel, "receipt_rows", receipts)
    monkeypatch.setattr(shared_price_transition, "_receipt_metadata", no_staging)
    assert await prices.history("ah", bought=True, today=TODAY) == expected
    assert requested == [None]


@pytest.mark.asyncio
async def test_mixed_ready_and_cold_stores_stage_only_cold_receipts(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    await fixture_history(data_dir, monkeypatch)
    async with price_import._connect() as conn:
        await conn.execute(
            "INSERT INTO imported_purchases(store,order_ref,position,day,title,quantity,paid,regular,imported_at) "
            "VALUES('jumbo','own',0,'2026-10-01','Jumbo receipt only',1,2.0,2.0,'now')"
        )
        await conn.commit()
    expected: dict[str, Any] = await prices.history(query="receipt", today=TODAY)
    monkeypatch.setenv("MEALIESHOPPER_SHARED_PRICES", "true")
    await shared_price_overlay.prepare("ah")
    original: Any = shared_price_transition._receipt_metadata
    staged: dict[str, list[dict[str, Any]]] = {}

    def metadata(receipts: dict[str, list[dict[str, Any]]]) -> list[tuple[Any, ...]]:
        staged.update(receipts)
        return original(receipts)

    monkeypatch.setattr(shared_price_transition, "_receipt_metadata", metadata)
    assert await prices.history(query="receipt", today=TODAY) == expected
    assert "ah" not in staged
    assert staged["jumbo"][0]["title"] == "Jumbo receipt only"
