"""Wat de nachtelijke run aan gevolgde objecten overhoudt (de GC houdt bij een volle ronde de GIL vast: gc_watch.py).

Een `sqlite3.Row` en een NamedTuple volgen de GC zolang ze leven, een tuple van alleen tekst, getallen en None na zijn
literal niet. Bij AH zijn het tienduizenden regels van de bonkoppeling en punten van de gewone prijs, dus de lijsten ervan zijn
gewone tuples. De tests meten het op de volumedatabase (de schaal van productie) en zijn vastgelegd op wat een volle ronde erbij
ziet: het aantal gevolgde objecten, per soort.
"""

from __future__ import annotations

import collections
import gc
import json
import os
import sqlite3
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
import volume
from quart import Quart
from test_volume import _nightly, fake_stores, frozen_like_production  # noqa: F401  (fake_stores is een fixture)

from mealieshopper import prices, regular_price
from mealieshopper.app import create_app

# Hoeveel gevolgde objecten het vasthouden van de rijen hooguit toevoegt: de lijsten zelf, en wat de test eromheen bewaart.
HELD_LIMIT: int = 1_500


def _tracked_added(held: Any, before: int) -> int:
    """Het aantal gevolgde objecten erbij zodra `held` leeft, na een volle ronde zodat wat dood is weg is."""
    gc.collect()
    added: int = len(gc.get_objects()) - before
    assert held is not None
    return added


def _baseline() -> int:
    gc.collect()
    return len(gc.get_objects())


def test_a_regular_point_is_not_tracked() -> None:
    point: regular_price.RegularPoint = regular_price.regular_point("2026-11-13", float(len("2.49")), "search")
    assert not gc.is_tracked(point), "een NamedTuple of een tuple uit een iterator: de GC volgt hem"
    assert regular_price.choose([point], "2026-11-13") == regular_price.Chosen(4.0, "2026-11-13", "search", True, True)


def test_a_receipt_link_is_not_tracked() -> None:
    row: tuple[Any, ...] = (str(len("12")), str(len("345")), "Rundergehakt", None, "500 g")
    linked: dict[str, prices._Linked] = prices._receipt_product_map([row])
    (link,) = linked.values()
    assert link == ("3", "Rundergehakt", None, "500 g")
    assert not gc.is_tracked(link)
    assert all(not isinstance(value, sqlite3.Row) for value in linked.values())


def test_the_source_columns_follow_both_queries() -> None:
    """`_SOURCE_COLUMNS` is de volgorde van de tuples die `_receipt_source_rows` geeft: beide queries moeten hem delen."""
    conn: sqlite3.Connection = sqlite3.connect(":memory:")
    conn.executescript(
        "CREATE TABLE ah_receipt_items (product_id, name, quantity, price, amount, receipt_id);"
        "CREATE TABLE ah_receipts (receipt_id, date_time);"
        "CREATE TABLE imported_purchases (store, title, day, quantity, paid, regular, order_ref);"
    )
    imported: tuple[str, ...] = tuple(d[0] for d in conn.execute(prices._IMPORTED_SQL, ("ah", "2026-11-13")).description)
    dated: tuple[str, ...] = tuple(d[0] for d in conn.execute(prices._DATED_RECEIPTS_SQL, ("2026-11-13",)).description)
    assert imported == prices._SOURCE_COLUMNS
    assert dated == prices._SOURCE_COLUMNS


@pytest.mark.volume
async def test_the_receipt_lines_of_a_catalogue_build_are_not_tracked(volume_dir: Path) -> None:
    """De regels van de bonkoppeling en de koppelingen van AH die `_catalogue_rows` aan de bouw geeft: 30.000 rijen die als
    `sqlite3.Row` elk een gevolgd object zijn."""
    before: int = _baseline()
    async with prices._connect() as conn:
        rows: Any = await prices._catalogue_rows(conn, "ah", volume.TODAY)
    assert len(rows.receipts) > 10_000 and rows.links is not None and len(rows.links.receipt_products) > 1_000, "te weinig om te meten"
    receipts: list[Any] = rows.receipts
    products: list[Any] = rows.links.receipt_products
    del rows  # de namen van `_names` (dicts) horen er niet bij
    assert not any(isinstance(row, sqlite3.Row) for row in receipts + products)
    assert _tracked_added((receipts, products), before) < HELD_LIMIT


@pytest.mark.volume
async def test_the_regular_points_of_a_verdict_are_not_tracked(volume_dir: Path) -> None:
    """De rijen en de punten van `_regular_points` voor de aanbiedingen van AH: het oordeel maakt er tienduizenden."""
    async with prices._connect() as conn:
        catalogue: Any = await prices._catalogue(conn, "ah", volume.TODAY)
        ids: list[str] = sorted(catalogue.products)[:3_000]
        before: int = _baseline()
        found: dict[str, list[regular_price.RegularPoint]] = await prices._regular_points(
            conn, "ah", "2025-11-13", volume.TODAY, receipts=catalogue.receipts, ids=ids
        )
    points: int = sum(len(value) for value in found.values())
    assert points > 3_000, points
    added: int = _tracked_added(found, before)
    # Elk product heeft een lijst (gevolgd), de punten zelf niet: ruim onder het aantal punten.
    assert added < len(found) + HELD_LIMIT, (added, len(found), points)


def _histogram(objects: list[Any]) -> dict[str, int]:
    return dict(collections.Counter(type(item).__name__ for item in objects))


@pytest.mark.volume
async def test_a_nightly_run_tracks_no_named_tuples(volume_dir: Path, fake_stores: None, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    """Bij een volle ronde van de GC in de nachtelijke run (de app bevroren na haar start, zoals in productie) leeft er geen
    `RegularPoint` of `_Linked` meer als gevolgd object. Met `GC_NIGHTLY_OUT` gaat per volle ronde het aantal per soort en de duur
    naar dat bestand (json-regels)."""
    seen: list[dict[str, Any]] = []
    started: list[float] = []

    def at_full_collection(phase: str, info: dict[str, Any]) -> None:
        if info.get("generation") != 2:
            return
        if phase == "start":
            started.append(time.perf_counter())
            objects: list[Any] = gc.get_objects()
            seen.append({"tracked": len(objects), "types": _histogram(objects)})
            del objects
        elif started and seen:
            seen[-1]["ms"] = round((time.perf_counter() - started.pop()) * 1000, 1)

    nightly: Callable[[], Awaitable[Any]] = _nightly(volume_dir, monkeypatch)
    app: Quart = create_app()
    with frozen_like_production(monkeypatch):
        async with app.test_app():
            gc.collect()
            gc.callbacks.append(at_full_collection)
            try:
                await nightly()
            finally:
                gc.callbacks.remove(at_full_collection)
    assert seen, "geen volle ronde van de GC tijdens de run: dan meet deze test niets"
    out: str | None = os.environ.get("GC_NIGHTLY_OUT")
    if out:
        with open(out, "w", encoding="utf-8") as handle:
            handle.writelines(json.dumps(line) + "\n" for line in seen)
    for name in ("RegularPoint", "_Linked"):
        assert max(line["types"].get(name, 0) for line in seen) == 0, name
