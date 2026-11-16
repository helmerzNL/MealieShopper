"""Candidates must be ordinary, internally consistent and immediately readable."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from shared_prices_fixture import build_snapshot

from mealieshopper.shared_prices import GenerationStore
from mealieshopper.shared_prices.protocol import validate_file


def row(price: float = 1.0) -> dict[str, Any]:
    return {"store": "ah", "source": "ahprijsindex", "product_id": "p1", "day": "2026-01-01", "title": "Test", "price": price}


def descriptor_after_sql(path: Path, sql: str) -> dict[str, Any]:
    descriptor: dict[str, Any] = build_snapshot(path, [row()])
    with sqlite3.connect(path) as conn:
        conn.executescript(sql)
    body: bytes = path.read_bytes()
    digest: str = hashlib.sha256(body).hexdigest()
    descriptor["artifact"].update(bytes=len(body), sha256=digest, path=f"shared-prices/v1/ah-ahprijsindex/{digest}.sqlite")
    return descriptor


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE VIRTUAL/* comment */TABLE extra USING fts5(value)",
        "CREATE VIRTUAL TABLE extra USING fts5(value); PRAGMA writable_schema=ON; UPDATE sqlite_schema SET sql=replace(sql,'VIRTUAL TABLE','VIRTUAL/**/TABLE') WHERE name='extra';",
        "UPDATE price_periods SET observation_count=NULL",
        "DELETE FROM product_versions",
        "UPDATE price_periods SET first_day='2026-01-01 12:00:00',last_day='2026-01-01 12:00:00'",
        "UPDATE product_versions SET metadata_json='{}'",
        "UPDATE products SET version_id=999",
        "UPDATE price_periods SET version_id=999",
        "UPDATE price_periods SET product_id='unknown'",
        "UPDATE products SET title='Wrong projection'",
        "UPDATE price_periods SET values_json='{\"price\":99}'",
        "DELETE FROM product_facts",
        "DELETE FROM search_names",
    ],
)
def test_unreadable_or_inconsistent_candidates_are_rejected(tmp_path: Path, sql: str) -> None:
    path: Path = tmp_path / "invalid.sqlite"
    descriptor: dict[str, Any] = descriptor_after_sql(path, sql)
    with pytest.raises(ValueError):
        validate_file(path, descriptor)
    store: GenerationStore = GenerationStore(tmp_path / "store")
    with pytest.raises(ValueError):
        store.install(path, descriptor)
    assert store.generation() is None


def test_activation_retains_chosen_last_good_when_old_active_is_missing(tmp_path: Path) -> None:
    store: GenerationStore = GenerationStore(tmp_path / "store")
    descriptors: list[dict[str, Any]] = []
    for number in range(2):
        path: Path = tmp_path / f"{number}.sqlite"
        descriptor: dict[str, Any] = build_snapshot(path, [row(float(number + 1))])
        store.install(path, descriptor)
        descriptors.append(descriptor)
    (store.root / f"{descriptors[1]['artifact']['sha256']}.sqlite").unlink()
    assert store.generation() == descriptors[0]["artifact"]["sha256"]
    candidate: Path = tmp_path / "third.sqlite"
    descriptor = build_snapshot(candidate, [row(3.0)])
    store.install(candidate, descriptor)
    pointer: dict[str, Any] = json.loads((store.root / "active.json").read_text())
    assert pointer["previous"]["artifact"]["sha256"] == descriptors[0]["artifact"]["sha256"]
    assert (store.root / f"{descriptors[0]['artifact']['sha256']}.sqlite").exists()


def test_fixture_receipt_name_lookups_have_exact_indexed_keys(tmp_path: Path) -> None:
    from mealieshopper import name_keys, prices

    path: Path = tmp_path / "names.sqlite"
    build_snapshot(path, [{**row(), "title": "AH Volle melk 1 liter"}])
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT folded_name,near_name FROM search_names").fetchone() == (
            prices._fold_name("AH Volle melk 1 liter"),
            name_keys.key("AH Volle melk 1 liter"),
        )
        for column in ("folded_name", "near_name"):
            plan: str = str(
                conn.execute(f"EXPLAIN QUERY PLAN SELECT product_id FROM search_names WHERE store=? AND {column}=?", ("ah", "value")).fetchall()
            )
            assert "USING" in plan and "INDEX" in plan
