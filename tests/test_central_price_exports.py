from __future__ import annotations

import asyncio
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest
from shared_prices_fixture import build_snapshot

from mealieshopper import price_archive, price_archive_export, prices, shared_price_overlay, shared_prices


def observation(pid: str, day: str = "2026-10-01", price: float = 2.0) -> dict[str, Any]:
    return dict(
        store="ah",
        product_id=pid,
        day=day,
        source="ahprijsindex",
        title=f"Product {pid}",
        brand=None,
        unit_size=None,
        image=None,
        url=None,
        price=price,
        seen_at=f"{day}T12:00:00Z",
    )


def install(folder: Path, rows: list[dict[str, Any]], name: str) -> None:
    path: Path = folder / name
    descriptor: dict[str, Any] = build_snapshot(path, rows)
    shared_prices.GenerationStore(folder / "shared-prices").install(path, descriptor)


@pytest.mark.asyncio
async def test_exports_stream_public_history_and_private_precedence(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    await price_archive.ensure_schema()
    original: dict[str, Any] = observation("1")
    async with prices._connect() as conn:
        await conn.execute(
            "INSERT INTO price_observations VALUES(?,?,?,?,?,?,?,?,?,?,?)", price_archive.normalise_price(original, price_archive.Today())
        )
        await conn.execute("DELETE FROM shared_price_origins")
        await conn.commit()
    await asyncio.to_thread(install, data_dir, [original], "first.sqlite")
    monkeypatch.setenv("MEALIESHOPPER_SHARED_PRICES", "true")
    await shared_price_overlay.prepare()
    await asyncio.to_thread(
        install, data_dir, [{**original, "price": 3.0}, observation("1", "2026-10-08", 2.5), observation("2", price=5.0)], "second.sqlite"
    )
    await prices.record(
        "ah", [{"id": "2", "title": "Private 2", "price": 9.0}, {"id": "3", "title": "Private 3", "price": 7.0}], now="2026-10-01T12:00:00Z"
    )
    for site in (False, True):
        path: Path = data_dir / f"export-{site}.zip"
        header: dict[str, Any] = await asyncio.to_thread(price_archive_export.write_site_export if site else price_archive_export.write_export, path)
        assert header["counts"]["prices"]["ah"] == 4
        exported: list[dict[str, Any]] = []
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                if site and "/prices/" in name and name.endswith(".json"):
                    exported += json.loads(archive.read(name))
                elif not site and name.startswith("prices/") and name.endswith(".jsonl"):
                    exported += [json.loads(line) for line in archive.read(name).splitlines()]
        assert {(row["product_id"], row["day"]): row["price"] for row in exported} == {
            ("1", "2026-10-01"): 3.0,
            ("1", "2026-10-08"): 2.5,
            ("2", "2026-10-01"): 9.0,
            ("3", "2026-10-01"): 7.0,
        }
    async with prices._connect() as conn:
        assert (await conn.execute_fetchall("SELECT price FROM price_observations WHERE product_id='1'"))[0][0] == 2.0


@pytest.mark.asyncio
async def test_export_keeps_legacy_days_and_products_removed_from_publication(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original: list[dict[str, Any]] = [observation("1", "2026-09-24"), observation("1", "2026-10-01"), observation("2")]
    await price_archive.ensure_schema()
    async with prices._connect() as conn:
        await conn.executemany(
            "INSERT INTO price_observations VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            [price_archive.normalise_price(row, price_archive.Today()) for row in original],
        )
        await conn.execute("DELETE FROM shared_price_origins")
        await conn.commit()
    await asyncio.to_thread(install, data_dir, original, "old.sqlite")
    monkeypatch.setenv("MEALIESHOPPER_SHARED_PRICES", "true")
    await shared_price_overlay.prepare()
    await asyncio.to_thread(install, data_dir, [observation("1", price=3.0)], "missing.sqlite")
    for site in (False, True):
        path: Path = data_dir / f"retained-{site}.zip"
        header: dict[str, Any] = await asyncio.to_thread(price_archive_export.write_site_export if site else price_archive_export.write_export, path)
        assert header["counts"]["prices"]["ah"] == 3
        exported: list[dict[str, Any]] = []
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                if site and "/prices/" in name and name.endswith(".json"):
                    exported += json.loads(archive.read(name))
                elif not site and name.startswith("prices/") and name.endswith(".jsonl"):
                    exported += [json.loads(line) for line in archive.read(name).splitlines()]
        assert {(row["product_id"], row["day"]): row["price"] for row in exported} == {
            ("1", "2026-09-24"): 2.0,
            ("1", "2026-10-01"): 3.0,
            ("2", "2026-10-01"): 2.0,
        }
