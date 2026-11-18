from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from shared_prices_fixture import build_snapshot

from mealieshopper import prices, shared_price_overlay, shared_prices


def row(pid: str = "1", day: str = "2026-10-01", price: float = 2.0, title: str = "AH Melk", unit: str = "1 l") -> dict[str, Any]:
    return dict(
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
        seen_at=f"{day}T12:00:00Z",
    )


def install(data_dir: Path, rows: list[dict[str, Any]], name: str = "fixture.sqlite") -> None:
    path: Path = data_dir / name
    descriptor: dict[str, Any] = build_snapshot(path, rows)
    shared_prices.GenerationStore(data_dir / "shared-prices").install(path, descriptor)


async def raising_catalogue(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("full catalogue called")


@pytest.mark.asyncio
async def test_cold_history_page_and_hover_never_build_catalogue(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install(data_dir, [row(), row("2", title="AH Brood", unit="500 g")])
    monkeypatch.setenv("MEALIESHOPPER_SHARED_PRICES", "true")
    monkeypatch.setattr(prices, "_catalogue_rows", raising_catalogue)
    result: dict[str, Any] = await prices.history("ah", today="2026-10-02")
    assert result["total"] == 2
    assert {product["productId"] for product in result["products"]} == {"1", "2"}
    hover: dict[str, Any] = await prices.history("ah", product="1", today="2026-10-02")
    assert hover["products"][0]["regularPrice"] == 2.0
    assert hover["products"][0]["title"] == "AH Melk"


@pytest.mark.asyncio
async def test_cold_missing_snapshot_keeps_selected_local_price(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEALIESHOPPER_SHARED_PRICES", "true")
    await prices.record("ah", [{"id": "private", "title": "Private milk", "price": 4.0}], now="2026-10-01T12:00:00Z")
    monkeypatch.setattr(prices, "_catalogue_rows", raising_catalogue)
    hover: dict[str, Any] = await prices.history("ah", product="private", today="2026-10-02")
    assert hover["products"][0]["regularPrice"] == 4.0


@pytest.mark.asyncio
async def test_prepare_builds_private_index_and_public_correction_reaches_history(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original: dict[str, Any] = row()
    await prices.record(
        "ah",
        [{"id": "1", "title": original["title"], "price": 2.0, "brand": "AH", "unitSize": "1 l"}],
        source="ahprijsindex",
        now=original["seen_at"],
    )
    async with prices._connect() as conn:
        await conn.execute("UPDATE price_observations SET image=''")
        await conn.execute("DELETE FROM shared_price_origins")
        await conn.commit()
    install(data_dir, [original])
    monkeypatch.setenv("MEALIESHOPPER_SHARED_PRICES", "true")
    await shared_price_overlay.prepare()
    install(data_dir, [{**original, "price": 3.0}], "corrected.sqlite")
    await shared_price_overlay.prepare()
    monkeypatch.setattr(prices, "_catalogue_rows", raising_catalogue)
    hover: dict[str, Any] = await prices.history("ah", product="1", today="2026-10-02")
    assert hover["products"][0]["regularPrice"] == 3.0
    async with prices._connect() as conn:
        assert (await conn.execute_fetchall("SELECT price FROM price_observations"))[0][0] == 2.0
        assert await conn.execute_fetchall("SELECT * FROM price_history_products") == []


@pytest.mark.asyncio
async def test_cold_offer_facts_and_signature_bypass_catalogue(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install(data_dir, [row(day="2026-10-01", price=2.0), row("2", day="2026-10-01", price=3.0, unit="2 l")])
    monkeypatch.setenv("MEALIESHOPPER_SHARED_PRICES", "true")
    monkeypatch.setattr(prices, "_catalogue_rows", raising_catalogue)
    monkeypatch.setattr(prices, "_now", lambda: "2026-10-02T12:00:00Z")
    before: str = await prices.offers_signature("ah")
    assert not before.startswith("fout:")
    items: list[dict[str, Any]] = [{"id": 1, "title": "AH Melk", "brand": "AH", "unitSize": "1 l", "price": 1.0}]
    await prices.annotate("ah", items, today="2026-10-02")
    assert items[0]["verdict"]["regular"]["price"] == 2.0
    assert items[0]["verdict"]["cheaperPack"] is None
    assert await prices.offers_signature("ah") == before
    await prices.record("ah", [{"id": "1", "title": "AH Melk", "price": 4.0}], now="2026-10-02T12:00:00Z")
    assert await prices.offers_signature("ah") != before


@pytest.mark.asyncio
async def test_compact_offer_candidates_keep_private_mask_and_do_not_expand_history(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install(data_dir, [row(day="2026-09-24", price=2.0), row(day="2026-10-01", price=2.0)])
    monkeypatch.setenv("MEALIESHOPPER_SHARED_PRICES", "true")
    await prices.record("ah", [{"id": "1", "title": "AH Melk", "price": 4.0}], now="2026-10-01T12:00:00Z")

    def no_expansion(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("offer facts expanded chart history")

    monkeypatch.setattr(shared_prices.Snapshot, "observations", no_expansion)
    monkeypatch.setattr(prices, "_catalogue_rows", raising_catalogue)
    items: list[dict[str, Any]] = [{"id": 1, "title": "AH Melk", "brand": "AH", "unitSize": "1 l", "price": 1.0}]
    await prices.annotate("ah", items, today="2026-10-02")
    assert items[0]["verdict"]["regular"]["price"] == 2.0
    assert items[0]["verdict"]["regular"]["day"] == "2026-09-24"


@pytest.mark.asyncio
async def test_maximum_200_product_page_keeps_all_225_week_observations(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    observations: list[dict[str, Any]] = [
        row(str(pid), day=(date(2020, 1, 1) + timedelta(days=week * 7)).isoformat(), price=1.0 + (week % 3) / 10, title=f"AH Product {pid}")
        for pid in range(200)
        for week in range(225)
    ]
    await asyncio.to_thread(install, data_dir, observations)
    monkeypatch.setenv("MEALIESHOPPER_SHARED_PRICES", "true")
    monkeypatch.setattr(prices, "_catalogue_rows", raising_catalogue)
    original: Any = shared_prices.Snapshot.observations

    def bounded(snapshot: Any, ids: Any, *args: Any, **kwargs: Any) -> Any:
        assert len(ids) <= 32
        return original(snapshot, ids, *args, **kwargs)

    monkeypatch.setattr(shared_prices.Snapshot, "observations", bounded)
    result: dict[str, Any] = await prices.history("ah", limit=200, today="2026-10-02")
    assert result["total"] == 200
    assert len(result["products"]) == 200
    assert all(len(product["regular"]) == 225 for product in result["products"])


@pytest.mark.asyncio
async def test_direct_write_immediately_joins_the_prepared_page(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install(data_dir, [row()])
    monkeypatch.setenv("MEALIESHOPPER_SHARED_PRICES", "true")
    await prices.record("ah", [{"id": "private", "title": "Private yoghurt", "price": 4.0}], now="2026-10-02T12:00:00Z")
    result: dict[str, Any] = await prices.history("ah", today="2026-10-03")
    assert {product["productId"] for product in result["products"]} == {"1", "private"}


@pytest.mark.asyncio
async def test_differential_history_filters_twins_pins_receipts_and_offers(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(prices, "_now", lambda: "2026-10-03T12:00:00Z")
    from copy import deepcopy

    from mealieshopper import offer_categories, price_import

    public: list[dict[str, Any]] = [
        row("1", day="2026-09-24"),
        row("2", day="2026-10-01"),
        row("3", day="2026-10-01", price=3.0, unit="2 l"),
        {**row("4", title="Red Band Winegums original", unit="300 g"), "brand": "Red Band"},
    ]
    public = [{**item, "category": offer_categories.offer_category("ah", item["title"])} for item in public]
    async with prices._connect() as conn:
        await conn.executemany(
            "INSERT INTO price_observations (store,product_id,day,source,title,brand,unit_size,image,url,price,seen_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            [tuple(item[key] for key in shared_price_overlay.COLUMNS) for item in public],
        )
        await conn.execute("DELETE FROM shared_price_origins")
        await conn.commit()
    await prices.record("ah", [{"id": "1", "title": "AH Melk", "brand": "AH", "unitSize": "1 l", "price": 2.1}], now="2026-10-02T12:00:00Z")
    async with price_import._connect() as conn:
        await conn.execute(
            "INSERT INTO imported_purchases(store,order_ref,position,day,title,quantity,paid,regular,imported_at) VALUES('ah','own',0,'2026-10-01','AH Melk',1,1.8,2.0,'now')"
        )
        await conn.commit()
    questions: list[dict[str, Any]] = [
        {},
        {"query": "melk 1l"},
        {"query": "redband300g"},
        {"query": "winegums", "pin": "1"},
        {"product": "1"},
        {"bought": True},
        {"brands": ["AH"]},
        {"brands_out": ["AH"]},
    ]
    golden: list[dict[str, Any]] = [await prices.history("ah", today="2026-10-03", **question) for question in questions]
    items: list[dict[str, Any]] = [{"id": 1, "title": "AH Melk", "brand": "AH", "unitSize": "1 l", "price": 1.0}]
    old_items: list[dict[str, Any]] = await prices.annotate("ah", deepcopy(items), today="2026-10-03")
    install(data_dir, public)
    monkeypatch.setenv("MEALIESHOPPER_SHARED_PRICES", "true")
    for question, expected in zip(questions, golden, strict=True):
        assert await prices.history("ah", today="2026-10-03", **question) == expected, question
    await shared_price_overlay.prepare()
    monkeypatch.setattr(prices, "_catalogue_rows", raising_catalogue)
    monkeypatch.setattr(prices, "_names", raising_catalogue)
    for question, expected in zip(questions, golden, strict=True):
        actual: dict[str, Any] = await prices.history("ah", today="2026-10-03", **question)
        assert actual == expected, question
    assert await prices.annotate("ah", deepcopy(items), today="2026-10-03") == old_items


@pytest.mark.asyncio
async def test_cold_http_offers_304_never_loads_chart_or_catalogue(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from mealieshopper.app import create_app

    today: str = date.today().isoformat()
    await asyncio.to_thread(install, data_dir, [row(day=today)])
    monkeypatch.setenv("MEALIESHOPPER_SHARED_PRICES", "true")
    monkeypatch.setenv("PASSKEY_AUTH_ENABLED", "false")
    async with prices._connect() as conn:
        await conn.execute("INSERT INTO offers(store,product_id,position,title,brand,unit_size,price) VALUES('ah','1',0,'AH Melk','AH','1 l',1.0)")
        await conn.commit()
    monkeypatch.setattr(prices, "_catalogue_rows", raising_catalogue)

    def no_expansion(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("offer facts expanded chart history")

    monkeypatch.setattr(shared_prices.Snapshot, "observations", no_expansion)
    app: Any = create_app()
    async with app.test_app() as running:
        client: Any = running.test_client()
        first: Any = await client.get("/api/offers?store=ah")
        assert first.status_code == 200
        body: dict[str, Any] = await first.get_json()
        assert body["offers"][0]["verdict"]["regular"]["price"] == 2.0
        monkeypatch.setattr(prices, "annotate", raising_catalogue)
        second: Any = await client.get("/api/offers?store=ah", headers={"If-None-Match": first.headers["ETag"]})
        assert second.status_code == 304


@pytest.mark.asyncio
async def test_offer_response_keeps_generation_through_etag_and_build(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from mealieshopper import offers
    from mealieshopper.app import create_app

    today: str = date.today().isoformat()
    await asyncio.to_thread(install, data_dir, [row(day=today)], "first.sqlite")
    monkeypatch.setenv("MEALIESHOPPER_SHARED_PRICES", "true")
    monkeypatch.setenv("PASSKEY_AUTH_ENABLED", "false")
    async with prices._connect() as conn:
        await conn.execute("INSERT INTO offers(store,product_id,position,title,brand,unit_size,price) VALUES('ah','1',0,'AH Melk','AH','1 l',1.0)")
        await conn.commit()
    original: Any = offers.load
    changed: bool = False

    async def update_during_load(*args: Any, **kwargs: Any) -> Any:
        nonlocal changed
        data: Any = await original(*args, **kwargs)
        if not changed:
            changed = True
            await asyncio.to_thread(install, data_dir, [row(day=today, price=3.0)], "second.sqlite")
        return data

    monkeypatch.setattr(offers, "load", update_during_load)
    app: Any = create_app()
    async with app.test_app() as running:
        client: Any = running.test_client()
        first: Any = await client.get("/api/offers?store=ah")
        assert (await first.get_json())["offers"][0]["verdict"]["regular"]["price"] == 2.0
        second: Any = await client.get("/api/offers?store=ah", headers={"If-None-Match": first.headers["ETag"]})
        assert second.status_code == 200
        assert (await second.get_json())["offers"][0]["verdict"]["regular"]["price"] == 3.0


@pytest.mark.asyncio
async def test_foreground_hooks_finish_once_with_telemetry_disabled(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from mealieshopper.app import create_app

    monkeypatch.delenv("MEALIESHOPPER_TELEMETRY", raising=False)
    monkeypatch.setenv("PASSKEY_AUTH_ENABLED", "false")
    started: list[Any] = []
    finished: list[Any] = []

    def start() -> object:
        token: object = object()
        started.append(token)
        return token

    def finish(token: Any) -> None:
        finished.append(token)

    monkeypatch.setattr(shared_prices, "foreground_started", start)
    monkeypatch.setattr(shared_prices, "foreground_finished", finish)
    app: Any = create_app()
    async with app.test_app() as running:
        client: Any = running.test_client()
        assert (await client.get("/api/offers?store=ah")).status_code == 200
        assert (await client.get("/missing-pressure-probe")).status_code == 404
    assert len(started) == 2
    assert finished == started


@pytest.mark.asyncio
async def test_day_rollover_changes_signature_and_compact_candidates(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install(data_dir, [row(day="2026-10-01", price=2.5), row(day="2026-10-02", price=1.5)])
    monkeypatch.setenv("MEALIESHOPPER_SHARED_PRICES", "true")
    monkeypatch.setattr(prices, "_catalogue_rows", raising_catalogue)
    monkeypatch.setattr(prices, "_now", lambda: "2026-10-01T12:00:00Z")
    first: str = await prices.offers_signature("ah")
    items: list[dict[str, Any]] = [{"id": 1, "title": "AH Melk", "price": 1.0, "unitSize": "1 l"}]
    await prices.annotate("ah", items, today="2026-10-01")
    assert items[0]["verdict"]["regular"]["price"] == 2.5
    monkeypatch.setattr(prices, "_now", lambda: "2026-10-02T12:00:00Z")
    assert await prices.offers_signature("ah") != first
    await prices.annotate("ah", items, today="2026-10-02")
    assert items[0]["verdict"]["regular"]["price"] == 1.5


@pytest.mark.asyncio
async def test_new_snapshot_receipt_names_and_own_link_change_apply_immediately(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from mealieshopper import price_import, product_links, shared_price_readmodel

    install(data_dir, [row()])
    monkeypatch.setenv("MEALIESHOPPER_SHARED_PRICES", "true")
    async with price_import._connect() as conn:
        await conn.execute(
            "INSERT INTO imported_purchases(store,order_ref,position,day,title,quantity,paid,regular,imported_at) VALUES('ah','own',0,'2026-10-01','AH Melk',1,1.8,2.0,'now')"
        )
        await conn.commit()
    await shared_price_overlay.prepare()
    async with prices._connect() as conn:
        with shared_prices.lease("ah") as snapshot:
            linked: list[dict[str, Any]] = await shared_price_readmodel.receipt_rows(conn, snapshot, "ah", "2026-10-03")
    assert linked[0]["product_id"] == "1"
    assert linked[0]["linked_by"] == "name"
    await product_links.save("ah", "AH Melk", {"id": "corrected", "title": "Corrected milk"})
    async with prices._connect() as conn:
        with shared_prices.lease("ah") as snapshot:
            corrected: list[dict[str, Any]] = await shared_price_readmodel.receipt_rows(conn, snapshot, "ah", "2026-10-03")
    assert corrected[0]["product_id"] == "corrected"
    assert corrected[0]["linked_by"] == "own"


@pytest.mark.asyncio
async def test_central_removal_preserves_retained_legacy_weeks_and_product(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from mealieshopper import price_archive

    original: list[dict[str, Any]] = [row("1", day="2026-09-24"), row("1", day="2026-10-01"), row("2", title="AH Brood", unit="500 g")]
    async with prices._connect() as conn:
        await conn.executemany(
            "INSERT INTO price_observations VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            [price_archive.normalise_price(item, price_archive.Today()) for item in original],
        )
        await conn.execute("DELETE FROM shared_price_origins")
        await conn.commit()
    install(data_dir, original, "old.sqlite")
    monkeypatch.setenv("MEALIESHOPPER_SHARED_PRICES", "true")
    await shared_price_overlay.prepare()
    install(data_dir, [{**row("1", day="2026-10-01"), "price": 3.0}], "removed.sqlite")
    await shared_price_overlay.prepare()
    first: dict[str, Any] = await prices.history("ah", product="1", today="2026-10-02")
    assert [(segment["from"], segment["price"]) for segment in first["products"][0]["regular"]] == [("2026-09-24", 2.0), ("2026-10-01", 3.0)]
    removed: dict[str, Any] = await prices.history("ah", product="2", today="2026-10-02")
    assert removed["total"] == 1
    page: dict[str, Any] = await prices.history("ah", today="2026-10-02")
    assert {product["productId"] for product in page["products"]} == {"1", "2"}


@pytest.mark.asyncio
async def test_cold_legacy_private_page_remains_visible_before_background_prepare(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    await prices.record("ah", [{"id": "private", "title": "Private yoghurt", "price": 4.0}], now="2026-10-02T12:00:00Z")
    monkeypatch.setenv("MEALIESHOPPER_SHARED_PRICES", "true")
    monkeypatch.setattr(prices, "_catalogue_rows", raising_catalogue)
    missing: dict[str, Any] = await prices.history("ah", today="2026-10-03")
    assert missing["total"] == 1
    assert missing["products"][0]["productId"] == "private"
    install(data_dir, [row()])
    available: dict[str, Any] = await prices.history("ah", today="2026-10-03")
    assert {product["productId"] for product in available["products"]} == {"private", "1"}


@pytest.mark.asyncio
async def test_folder_line_lookup_only_builds_selected_candidates(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install(data_dir, [row()])
    async with prices._connect() as conn:
        await conn.executemany(
            "INSERT INTO offer_history(store,product_id,title,first_seen,last_seen,ended_at,price,price_before,valid_from,valid_until) "
            "VALUES('ah',?,?,'2026-09-01T00:00:00Z','2026-09-07T00:00:00Z','2026-09-07T00:00:00Z',1,2,'2026-09-01','2026-09-07')",
            [("folder:matching", "Alle AH Melk")] + [(f"folder:other{number}", f"Alle Merk unrelated{number}") for number in range(2000)],
        )
        await conn.commit()
    monkeypatch.setenv("MEALIESHOPPER_SHARED_PRICES", "true")
    original: Any = prices._line_index
    seen: list[int] = []

    def selected(rows: list[Any]) -> Any:
        seen.append(len(rows))
        assert len(rows) <= 1
        return original(rows)

    monkeypatch.setattr(prices, "_line_index", selected)
    for prepared in (False, True):
        if prepared:
            await shared_price_overlay.prepare()
        result: dict[str, Any] = await prices.history("ah", product="1", today="2026-10-03")
        assert len(result["products"][0]["lineOffers"]) == 1
    assert max(seen) == 1


@pytest.mark.asyncio
async def test_selected_catalogue_decodes_only_selected_cached_receipt_documents(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from mealieshopper import shared_price_dirty, shared_price_readmodel

    today: str = "2026-10-03"
    async with prices._connect() as conn:
        await shared_price_dirty.ensure_triggers(conn)
        token: str = await shared_price_dirty.token(conn, "ah")
        await conn.executemany(
            "INSERT INTO shared_price_receipts VALUES(?,?,?)",
            [
                (
                    "ah",
                    str(number),
                    json.dumps([{"product_id": str(number), "title": "AH Melk", "brand": None, "unit_size": None, "day": "2026-10-01"}]),
                )
                for number in range(8000)
            ],
        )
        await conn.execute("INSERT INTO shared_price_receipt_meta VALUES(?,?)", ("ah", shared_price_dirty.receipt_revision(token, "", today)))
        await conn.commit()
    original: Any = shared_price_readmodel._decode_receipts

    def selected(rows: list[Any], day: str) -> Any:
        assert len(rows) <= 1
        return original(rows, day)

    monkeypatch.setattr(shared_price_readmodel, "_decode_receipts", selected)
    async with prices._connect() as conn:
        catalogue: Any = await shared_price_readmodel.catalogue(conn, None, "ah", today, ["1"])
    assert len(catalogue.receipts) == 1


@pytest.mark.asyncio
async def test_cancelled_annotation_keeps_generation_lease_until_worker_finishes(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import threading
    from contextlib import contextmanager

    install(data_dir, [row()])
    monkeypatch.setenv("MEALIESHOPPER_SHARED_PRICES", "true")
    active: list[int] = [0]
    original_lease: Any = shared_prices.lease

    @contextmanager
    def tracked(store: str = "ah") -> Any:
        with original_lease(store) as snapshot:
            active[0] += 1
            try:
                yield snapshot
            finally:
                active[0] -= 1

    entered: threading.Event = threading.Event()
    release: threading.Event = threading.Event()
    original_judge: Any = prices._judge

    def blocked(*args: Any) -> None:
        assert active[0] == 1
        entered.set()
        assert release.wait(3)
        assert active[0] == 1
        original_judge(*args)

    monkeypatch.setattr(shared_prices, "lease", tracked)
    monkeypatch.setattr(prices, "_judge", blocked)
    task: asyncio.Task[Any] = asyncio.create_task(prices.annotate("ah", [{"id": 1, "title": "AH Melk", "price": 1.0}], today="2026-10-03"))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        await asyncio.sleep(0.02)
        assert not task.done()
        assert active[0] == 1
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert active[0] == 0


@pytest.mark.asyncio
async def test_folder_pair_lookup_uses_bounded_index_probes(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:

    from mealieshopper import shared_price_dirty, shared_price_lines

    async with prices._connect() as conn:
        await shared_price_dirty.ensure_triggers(conn)
        await conn.executemany(
            "INSERT INTO shared_price_lines VALUES(?,?,?,?,?,?)",
            [("ah", f"folder:{number}", f"other{number}", "missing", "", "Unrelated") for number in range(5000)]
            + [("ah", "folder:match", "melk", "ah", "", "Alle AH Melk")],
        )
        ready: str = shared_price_dirty.build_revision(await shared_price_dirty.token(conn, "ah"), "2026-10-01")
        await conn.execute("INSERT INTO shared_price_build VALUES('ah','','',?,?)", (ready, ready))
        await conn.commit()
        work: list[int] = [0]

        def progress() -> int:
            work[0] += 100
            return 0

        await conn.set_progress_handler(progress, 100)
        found: list[Any] = await shared_price_lines.candidates(conn, "ah", {"1": prices._known("2026-10-01", ["ah melk"], [])})
        assert [row[0] for row in found] == ["folder:match"]
        assert work[0] < 5000, work[0]


@pytest.mark.asyncio
async def test_public_category_infers_like_legacy_without_raw_category(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(prices, "_now", lambda: "2026-10-03T12:00:00Z")
    from mealieshopper import offer_categories

    install(data_dir, [{**row(), "category": None}])
    monkeypatch.setenv("MEALIESHOPPER_SHARED_PRICES", "true")
    category: str = offer_categories.offer_category("ah", "AH Melk")
    for prepared in (False, True):
        if prepared:
            await shared_price_overlay.prepare()
        result: dict[str, Any] = await prices.history("ah", categories=[category], today="2026-10-03")
        assert result["total"] == 1
        assert result["facets"]["categories"] == [{"value": category, "count": 1}]


@pytest.mark.asyncio
async def test_duplicate_import_lines_keep_multiplicity_and_order_cold_and_warm(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(prices, "_now", lambda: "2026-10-03T12:00:00Z")
    from mealieshopper import price_import

    install(data_dir, [row()])
    monkeypatch.setenv("MEALIESHOPPER_SHARED_PRICES", "true")
    async with price_import._connect() as conn:
        await conn.executemany(
            "INSERT INTO imported_purchases(store,order_ref,position,day,title,quantity,paid,regular,imported_at) "
            "VALUES('ah','own',?,'2026-10-01','AH Melk',1,?,2.0,'now')",
            [(0, 1.8), (1, 1.9), (2, 1.8)],
        )
        await conn.commit()
    for prepared in (False, True):
        if prepared:
            await shared_price_overlay.prepare()
        result: dict[str, Any] = await prices.history("ah", product="1", today="2026-10-03")
        assert result["products"][0]["timesBought"] == 3
        assert [purchase["price"] for purchase in result["products"][0]["purchases"]] == [1.8, 1.9, 1.8]


@pytest.mark.asyncio
async def test_public_private_twins_use_merged_historical_sizes(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(prices, "_now", lambda: "2026-10-03T12:00:00Z")
    from mealieshopper import price_archive

    public: list[dict[str, Any]] = [row(), row("2", unit="2 l")]
    install(data_dir, public)
    async with prices._connect() as conn:
        await conn.executemany(
            "INSERT INTO price_observations VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            [price_archive.normalise_price(item, price_archive.Today()) for item in public],
        )
        await conn.execute("DELETE FROM shared_price_origins")
        await conn.commit()
    await prices.record("ah", [{"id": "1", "title": "AH Melk", "brand": "AH", "unitSize": "2 l", "price": 3.0}], now="2026-10-02T12:00:00Z")
    expected: dict[str, Any] = await prices.history("ah", today="2026-10-03")
    assert expected["total"] == 2
    monkeypatch.setenv("MEALIESHOPPER_SHARED_PRICES", "true")
    for prepared in (False, True):
        if prepared:
            await shared_price_overlay.prepare()
        assert await prices.history("ah", today="2026-10-03") == expected


@pytest.mark.asyncio
async def test_family_metadata_outlives_regular_price_candidate_window(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install(data_dir, [row(), row("2", day="2014-01-01", unit="2 l", price=3.0)])
    monkeypatch.setenv("MEALIESHOPPER_SHARED_PRICES", "true")
    items: list[dict[str, Any]] = [{"id": 1, "title": "AH Melk", "brand": "AH", "unitSize": "1 l", "price": 1.0}]
    await prices.annotate("ah", items, today="2026-10-03")
    assert items[0]["family"]["unknownPacks"] is True
    assert [pack["productId"] for pack in items[0]["family"]["packs"]] == ["1"]


@pytest.mark.asyncio
async def test_private_readiness_rolls_over_for_future_receipt_days(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from mealieshopper import price_import

    monkeypatch.setenv("MEALIESHOPPER_SHARED_PRICES", "true")
    monkeypatch.setattr(prices, "_now", lambda: "2026-10-02T12:00:00Z")
    async with price_import._connect() as conn:
        await conn.execute(
            "INSERT INTO imported_purchases(store,order_ref,position,day,title,quantity,paid,imported_at) VALUES('ah','future',0,'2026-10-03','Private milk',1,2.0,'now')"
        )
        await conn.commit()
    await shared_price_overlay.prepare()
    assert (await prices.history("ah", today="2026-10-02"))["total"] == 0
    monkeypatch.setattr(prices, "_now", lambda: "2026-10-03T12:00:00Z")
    assert (await prices.history("ah", today="2026-10-03"))["total"] == 1
    await shared_price_overlay.prepare()
    assert (await prices.history("ah", today="2026-10-03"))["total"] == 1


@pytest.mark.asyncio
async def test_selected_catalogue_bounds_sql_grouping_before_materialisation(data_dir: Path) -> None:
    from contextlib import closing

    from mealieshopper import shared_price_readmodel, thread_db

    async with prices._connect():
        pass

    def populate() -> None:
        with closing(thread_db.connect()) as conn:
            conn.executemany(
                "INSERT INTO offer_history(store,product_id,title,first_seen,last_seen) VALUES('ah',?,?,'2026-10-01','2026-10-01')",
                [(str(number), f"Product {number}") for number in range(20000)],
            )
            conn.commit()

    await asyncio.to_thread(populate)
    async with prices._connect() as conn:
        await shared_price_overlay.revision(conn, "ah")
        work: list[int] = [0]

        def progress() -> int:
            work[0] += 100
            return 0

        await conn.set_progress_handler(progress, 100)
        selected: Any = await shared_price_readmodel.catalogue(conn, None, "ah", "2026-10-03", ["1"], include_lines=False)
        assert set(selected.products) == {"1"}
        assert work[0] < 5000, work[0]


@pytest.mark.asyncio
async def test_private_twins_across_background_batches_match_legacy(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(prices, "_now", lambda: "2026-10-03T12:00:00Z")
    await prices.record("ah", [{"id": "000first", "title": "AH Melk", "brand": "AH", "unitSize": "1 l", "price": 2.0}], now="2026-10-01T12:00:00Z")
    await prices.record(
        "ah",
        [{"id": f"p{number:03d}", "title": f"Unique product {number}", "brand": "AH", "unitSize": "500 g", "price": 1.0} for number in range(63)],
        now="2026-10-01T12:00:00Z",
    )
    await prices.record("ah", [{"id": "zzlast", "title": "AH Melk", "brand": "AH", "unitSize": "1 l", "price": 2.1}], now="2026-10-02T12:00:00Z")
    expected: dict[str, Any] = await prices.history("ah", today="2026-10-03", limit=200)
    assert expected["total"] == 64
    monkeypatch.setenv("MEALIESHOPPER_SHARED_PRICES", "true")
    await shared_price_overlay.prepare()
    assert await prices.history("ah", today="2026-10-03", limit=200) == expected


@pytest.mark.asyncio
async def test_cold_http_search_lookup_uses_prepared_receipts_without_catalogue(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import AsyncMock

    install(data_dir, [row()])
    monkeypatch.setenv("MEALIESHOPPER_SHARED_PRICES", "true")
    monkeypatch.setattr(prices, "_catalogue_rows", raising_catalogue)
    lookup: AsyncMock = AsyncMock()
    result: dict[str, Any] = await prices.history("ah", "melk", today="2026-10-03", lookup=lookup)
    assert result["total"] == 1
    lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_receipt_ambiguity_uses_public_prices_even_when_public_name_differs(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from mealieshopper import price_archive, price_import, shared_price_readmodel

    public: dict[str, Any] = row("1", title="Public Other Item", price=2.0)
    install(data_dir, [public])
    async with prices._connect() as conn:
        await conn.execute(
            "INSERT INTO price_observations VALUES(?,?,?,?,?,?,?,?,?,?,?)", price_archive.normalise_price(public, price_archive.Today())
        )
        await conn.execute("DELETE FROM shared_price_origins")
        await conn.commit()
    await prices.record(
        "ah", [{"id": "1", "title": "Receipt Label", "price": 1.0}, {"id": "2", "title": "Receipt Label", "price": 2.1}], now="2026-10-02T12:00:00Z"
    )
    async with price_import._connect() as conn:
        await conn.execute(
            "INSERT INTO imported_purchases(store,order_ref,position,day,title,quantity,paid,regular,imported_at) VALUES('ah','ambiguous',0,'2026-10-02','Receipt Label',1,2.0,2.0,'now')"
        )
        await conn.commit()
    async with prices._connect() as conn:
        expected: list[dict[str, Any]] = await prices._receipt_rows(conn, "ah", "2026-10-03")
    assert expected[0]["product_id"] == "1"
    monkeypatch.setenv("MEALIESHOPPER_SHARED_PRICES", "true")
    await shared_price_overlay.prepare()
    async with prices._connect() as conn:
        with shared_prices.lease("ah") as snapshot:
            actual: list[dict[str, Any]] = await shared_price_readmodel.receipt_rows(conn, snapshot, "ah", "2026-10-03")
    assert actual == expected


@pytest.mark.asyncio
async def test_family_pack_size_ties_preserve_retained_observation_order(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from copy import deepcopy

    from mealieshopper import price_archive

    monkeypatch.setattr(prices, "_now", lambda: "2026-10-03T12:00:00Z")
    public: dict[str, Any] = row("10")
    install(data_dir, [public])
    await prices.record("ah", [{"id": "20", "title": "AH Melk", "brand": "AH", "unitSize": "1 l", "price": 2.0}], now="2026-10-01T12:00:00Z")
    async with prices._connect() as conn:
        await conn.execute(
            "INSERT INTO price_observations VALUES(?,?,?,?,?,?,?,?,?,?,?)", price_archive.normalise_price(public, price_archive.Today())
        )
        await conn.execute("DELETE FROM shared_price_origins WHERE product_id='10'")
        await conn.commit()
    items: list[dict[str, Any]] = [{"id": pid, "title": "AH Melk", "brand": "AH", "unitSize": "1 l", "price": 1.5} for pid in ("10", "20")]
    expected: list[dict[str, Any]] = await prices.annotate("ah", deepcopy(items), today="2026-10-03")
    assert expected[0]["family"]["packs"][0]["productId"] == "20"
    monkeypatch.setenv("MEALIESHOPPER_SHARED_PRICES", "true")
    await shared_price_overlay.prepare()
    actual: list[dict[str, Any]] = await prices.annotate("ah", deepcopy(items), today="2026-10-03")
    assert actual == expected


@pytest.mark.asyncio
async def test_private_family_metadata_outlives_regular_price_window(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from copy import deepcopy

    monkeypatch.setattr(prices, "_now", lambda: "2026-10-03T12:00:00Z")
    install(data_dir, [row()])
    await prices.record("ah", [{"id": "1", "title": "AH Melk", "brand": "AH", "unitSize": "1 l", "price": 2.0}], now="2026-10-01T12:00:00Z")
    await prices.record("ah", [{"id": "2", "title": "AH Melk", "brand": "AH", "unitSize": "2 l", "price": 3.0}], now="2014-10-01T12:00:00Z")
    items: list[dict[str, Any]] = [{"id": "1", "title": "AH Melk", "brand": "AH", "unitSize": "1 l", "price": 1.0}]
    expected: list[dict[str, Any]] = await prices.annotate("ah", deepcopy(items), today="2026-10-03")
    assert expected[0]["family"]["unknownPacks"] is True
    monkeypatch.setenv("MEALIESHOPPER_SHARED_PRICES", "true")
    await shared_price_overlay.prepare()
    actual: list[dict[str, Any]] = await prices.annotate("ah", deepcopy(items), today="2026-10-03")
    assert actual == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "title,old_title,brand,size",
    [
        ("Polderpracht Kaarsen mini", "Polderpra Kaarsen", "Polderpracht", "10 stuks"),
        ("Twentse Hoeve Croissants light", "Twentse Hoeve Croissant light", "Twentse Hoeve", "6 stuks"),
    ],
)
async def test_family_lookup_preserves_every_historical_name(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch, title: str, old_title: str, brand: str, size: str
) -> None:
    from copy import deepcopy

    monkeypatch.setattr(prices, "_now", lambda: "2026-10-03T12:00:00Z")
    install(data_dir, [{**row(), "title": title, "brand": brand, "unit_size": size}])
    await prices.record("ah", [{"id": "1", "title": title, "brand": brand, "unitSize": size, "price": 2.0}], now="2026-10-01T12:00:00Z")
    for day, name in (("2014-01-01", old_title), ("2014-01-02", title)):
        await prices.record("ah", [{"id": "2", "title": name, "brand": brand, "unitSize": size, "price": 2.0}], now=day + "T12:00:00Z")
    items: list[dict[str, Any]] = [{"id": "1", "title": title, "brand": brand, "unitSize": size, "price": 1.0, "unitPrice": 1.0}]
    expected: list[dict[str, Any]] = await prices.annotate("ah", deepcopy(items), today="2026-10-03")
    assert expected[0]["family"]["unknownPacks"] is True
    monkeypatch.setenv("MEALIESHOPPER_SHARED_PRICES", "true")
    await shared_price_overlay.prepare()
    actual: list[dict[str, Any]] = await prices.annotate("ah", deepcopy(items), today="2026-10-03")
    assert actual == expected

    async with prices._connect() as conn:
        await conn.execute("DROP TABLE shared_price_families")
        await shared_price_overlay._setup(conn)
        assert not await conn.execute_fetchall("SELECT 1 FROM shared_price_build")
    await shared_price_overlay.prepare()
    assert await prices.annotate("ah", deepcopy(items), today="2026-10-03") == expected
