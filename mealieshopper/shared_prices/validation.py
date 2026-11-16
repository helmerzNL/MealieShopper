"""Stream candidate contents and reject broken references and derived projections."""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Callable
from datetime import date
from typing import Any

from .pressure import Budget

Decoder = Callable[[str | bytes, int], Any]


def _check(valid: bool, message: str) -> None:
    if not valid:
        raise ValueError(message)


def _object(value: Any, decode: Decoder) -> dict[str, Any]:
    _check(isinstance(value, str), "Missing snapshot JSON object")
    parsed: Any = decode(value, 64 * 1024)
    _check(isinstance(parsed, dict), "Invalid snapshot JSON object")
    return parsed


def _day(value: Any) -> date:
    _check(isinstance(value, str) and len(value) == 10, "Noncanonical snapshot date")
    try:
        parsed: date = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid snapshot date") from exc
    _check(parsed.isoformat() == value, "Noncanonical snapshot date")
    return parsed


def _metadata(value: Any, decode: Decoder, descriptor: dict[str, Any], product_id: str | None = None) -> dict[str, Any]:
    metadata: dict[str, Any] = _object(value, decode)
    _check(metadata.get("store") == descriptor["store"] and metadata.get("source") == descriptor["source"], "Invalid metadata source")
    identifier: Any = metadata.get("product_id")
    _check(isinstance(identifier, str) and bool(identifier) and (product_id is None or identifier == product_id), "Invalid metadata product identity")
    _check(not {"day", "price", "seen_at"} & metadata.keys(), "Observation fields in product metadata")
    for key in ("title", "brand", "unit_size", "image", "url", "category"):
        _check(metadata.get(key) is None or isinstance(metadata[key], str), "Invalid product metadata type")
    return metadata


def _values(value: Any, price: Any, decode: Decoder) -> dict[str, Any]:
    values: dict[str, Any] = _object(value, decode)
    raw: Any = values.get("price")
    _check(type(raw) in (int, float) and math.isfinite(raw) and raw > 0, "Invalid original price")
    _check(type(price) in (int, float) and math.isfinite(price) and price == raw, "Price projection mismatch")
    _check(values.keys() <= {"price", "seen_at"}, "Unknown observation values")
    return values


def _checkpoint(budget: Budget | None) -> None:
    if budget is not None:
        budget.checkpoint()


def validate_contents(conn: sqlite3.Connection, descriptor: dict[str, Any], decode: Decoder, budget: Budget | None) -> None:
    for row in conn.execute("SELECT metadata_json FROM product_versions"):
        _checkpoint(budget)
        metadata: dict[str, Any] = _metadata(row[0], decode, descriptor)
        _check(
            conn.execute("SELECT 1 FROM products WHERE store=? AND product_id=?", (descriptor["store"], metadata["product_id"])).fetchone()
            is not None,
            "Orphaned metadata product",
        )
    _periods(conn, descriptor, decode, budget)
    _products(conn, descriptor, decode, budget)
    for table in ("search_names", "product_facts"):
        orphan: sqlite3.Row | None = conn.execute(
            f"SELECT 1 FROM {table} c LEFT JOIN products p ON p.store=c.store AND p.product_id=c.product_id WHERE p.product_id IS NULL LIMIT 1"
        ).fetchone()
        _check(orphan is None, "Orphaned product projection")


def _periods(conn: sqlite3.Connection, descriptor: dict[str, Any], decode: Decoder, budget: Budget | None) -> None:
    previous: tuple[str, str, str] | None = None
    previous_last: date | None = None
    cursor: sqlite3.Cursor = conn.execute(
        "SELECT p.*,v.metadata_json,r.product_id AS known_product FROM price_periods p "
        "LEFT JOIN product_versions v ON v.version_id=p.version_id "
        "LEFT JOIN products r ON r.store=p.store AND r.product_id=p.product_id "
        "ORDER BY p.store,p.product_id,p.source,p.first_day"
    )
    for row in cursor:
        _checkpoint(budget)
        _check(
            row["store"] == descriptor["store"] and row["source"] == descriptor["source"] and row["known_product"] is not None,
            "Invalid period product source",
        )
        _metadata(row["metadata_json"], decode, descriptor, row["product_id"])
        first: date = _day(row["first_day"])
        last: date = _day(row["last_day"])
        _check(
            type(row["step_days"]) is int
            and row["step_days"] == 7
            and type(row["observation_count"]) is int
            and row["observation_count"] > 0
            and (last - first).days == (row["observation_count"] - 1) * 7,
            "Invalid observation period",
        )
        identity: tuple[str, str, str] = (row["store"], row["product_id"], row["source"])
        _check(identity != previous or previous_last is not None and first > previous_last, "Overlapping observation periods")
        previous, previous_last = identity, last
        _values(row["values_json"], row["price"], decode)


def _products(conn: sqlite3.Connection, descriptor: dict[str, Any], decode: Decoder, budget: Budget | None) -> None:
    from mealieshopper import prices

    name_columns: set[str] = {item["name"] for item in conn.execute("PRAGMA table_info(search_names)")}
    cursor: sqlite3.Cursor = conn.execute(
        "SELECT p.*,v.metadata_json,f.facts_json,a.first_day AS history_first,a.last_day AS history_last,a.observations "
        "FROM products p LEFT JOIN product_versions v ON v.version_id=p.version_id "
        "LEFT JOIN product_facts f ON f.store=p.store AND f.product_id=p.product_id "
        "LEFT JOIN (SELECT store,product_id,min(first_day) first_day,max(last_day) last_day,sum(observation_count) observations "
        "FROM price_periods GROUP BY store,product_id) a ON a.store=p.store AND a.product_id=p.product_id"
    )
    for row in cursor:
        _checkpoint(budget)
        _check(row["store"] == descriptor["store"], "Invalid product source")
        metadata: dict[str, Any] = _metadata(row["metadata_json"], decode, descriptor, row["product_id"])
        _day(row["first_day"])
        _day(row["last_day"])
        _check(row["first_day"] == row["history_first"] and row["last_day"] == row["history_last"], "Product history bounds mismatch")
        for key in ("title", "brand", "unit_size", "image", "url", "category"):
            _check((row[key] or "") == (metadata.get(key) or ""), "Product metadata projection mismatch")
        latest: sqlite3.Row | None = conn.execute(
            "SELECT v.metadata_json FROM price_periods p JOIN product_versions v ON v.version_id=p.version_id "
            "WHERE p.store=? AND p.product_id=? AND p.last_day=?",
            (row["store"], row["product_id"], row["last_day"]),
        ).fetchone()
        _check(latest is not None and _metadata(latest[0], decode, descriptor, row["product_id"]) == metadata, "Latest metadata mismatch")
        sizes: set[tuple[float, str]] = _names(conn, row, descriptor, decode, budget, name_columns)
        _check(row["family_key"] == prices.base_title(metadata.get("title")), "Product family projection mismatch")
        _check(
            row["twin_key"] == prices._same_key(row["product_id"], metadata.get("title"), metadata.get("brand"), sizes),
            "Product twin projection mismatch",
        )
        facts: dict[str, Any] = _object(row["facts_json"], decode)
        _check(
            type(facts.get("rule_version")) is int and facts["rule_version"] == 1 and facts.get("promotional_coverage") is False,
            "Invalid product facts version or coverage",
        )
        _check(
            facts.get("observation_count") == row["observations"]
            and facts.get("first_day") == row["first_day"]
            and facts.get("last_day") == row["last_day"],
            "Product facts history mismatch",
        )


def _names(
    conn: sqlite3.Connection, product: sqlite3.Row, descriptor: dict[str, Any], decode: Decoder, budget: Budget | None, columns: set[str]
) -> set[tuple[float, str]]:
    from mealieshopper import name_keys, prices

    expected: set[tuple[str, str, float | None, str | None]] = set()
    sizes: set[tuple[float, str]] = set()
    for row in conn.execute(
        "SELECT DISTINCT v.metadata_json FROM price_periods p JOIN product_versions v ON v.version_id=p.version_id "
        "WHERE p.store=? AND p.product_id=?",
        (product["store"], product["product_id"]),
    ):
        _checkpoint(budget)
        metadata: dict[str, Any] = _metadata(row[0], decode, descriptor, product["product_id"])
        size: tuple[float, str] | None = prices._size_of(metadata.get("unit_size"), metadata.get("title"))
        if size:
            sizes.add(size)
        title: str = metadata.get("title") or ""
        expected.add((title, prices._fold_name(f"{title} {metadata.get('brand') or ''}"), *(size or (None, None))))
    actual: set[tuple[Any, ...]] = {
        tuple(row)
        for row in conn.execute(
            "SELECT name,search,amount,unit FROM search_names WHERE store=? AND product_id=?", (product["store"], product["product_id"])
        )
    }
    _check(actual == expected and bool(actual), "Search name projection mismatch")
    for column, function in (("folded_name", prices._fold_name), ("near_name", name_keys.key)):
        if column in columns:
            for row in conn.execute(
                f"SELECT name,{column} FROM search_names WHERE store=? AND product_id=?", (product["store"], product["product_id"])
            ):
                _check(row[column] == function(row["name"]), "Receipt name projection mismatch")
    return sizes
