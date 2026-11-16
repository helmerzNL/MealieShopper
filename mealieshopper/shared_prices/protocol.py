"""Validate the versioned public snapshot boundary before activation."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from .pressure import Budget
from .validation import validate_contents

MANIFEST_BYTES: int = 1024 * 1024
DATABASE_BYTES: int = 32 * 1024 * 1024
RETAINED_BYTES: int = 256 * 1024 * 1024
CHUNK_BYTES: int = 64 * 1024
APPLICATION_ID: int = 1297305649
SHA256: re.Pattern[str] = re.compile(r"[a-f0-9]{64}\Z")
COLUMNS: dict[str, set[str]] = {
    "dataset_meta": {"key", "value"},
    "source_documents": {"path", "sha256", "bytes", "items"},
    "product_versions": {"version_id", "metadata_json"},
    "products": {
        "store",
        "product_id",
        "title",
        "brand",
        "unit_size",
        "image",
        "url",
        "first_day",
        "last_day",
        "version_id",
        "family_key",
        "category",
        "twin_key",
    },
    "search_names": {"store", "product_id", "name", "search", "amount", "unit"},
    "price_periods": {
        "store",
        "product_id",
        "source",
        "first_day",
        "last_day",
        "step_days",
        "observation_count",
        "version_id",
        "price",
        "values_json",
    },
    "product_facts": {"store", "product_id", "facts_json"},
}


def _constant(value: str) -> None:
    raise ValueError(f"Non-finite JSON value: {value}")


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _float(value: str) -> float:
    number: float = float(value)
    if not math.isfinite(number):
        raise ValueError("Non-finite JSON number")
    return number


def strict_json(body: bytes | str, limit: int = MANIFEST_BYTES) -> Any:
    if len(body) > limit:
        raise ValueError("JSON exceeds size limit")
    try:
        return json.loads(body, parse_constant=_constant, parse_float=_float, object_pairs_hook=_pairs)
    except (UnicodeError, RecursionError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid snapshot JSON") from exc


def validate_dataset(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("id") != "ah-ahprijsindex" or value.get("store") != "ah" or value.get("source") != "ahprijsindex":
        raise ValueError("Unsupported snapshot dataset")
    if type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise ValueError("Unsupported snapshot schema version")
    for key in ("source_commit", "builder_revision"):
        if not isinstance(value.get(key), str) or not 0 < len(value[key]) <= 256:
            raise ValueError(f"Invalid {key}")
    if not isinstance(value.get("source_set_sha256"), str) or not SHA256.fullmatch(value["source_set_sha256"]):
        raise ValueError("Invalid source inventory hash")
    if not isinstance(value.get("coverage"), dict):
        raise ValueError("Invalid coverage")
    artifact: Any = value.get("artifact")
    if not isinstance(artifact, dict) or not isinstance(artifact.get("sha256"), str) or not SHA256.fullmatch(artifact["sha256"]):
        raise ValueError("Invalid artifact hash")
    if artifact.get("path") != f"shared-prices/v1/ah-ahprijsindex/{artifact['sha256']}.sqlite":
        raise ValueError("Invalid artifact path")
    if type(artifact.get("bytes")) is not int or not 0 < artifact["bytes"] <= DATABASE_BYTES or artifact.get("compression") != "identity":
        raise ValueError("Invalid artifact size or compression")
    required: Any = value.get("required_sqlite", {"minimum_version": "3.8.0", "features": []})
    if not isinstance(required, dict) or required.get("features") != []:
        raise ValueError("Unsupported SQLite capabilities")
    version: Any = required.get("minimum_version")
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Invalid SQLite version")
    if tuple(map(int, version.split("."))) > sqlite3.sqlite_version_info:
        raise ValueError("SQLite version too old")
    return value


def validate_manifest(body: bytes) -> dict[str, Any]:
    value: Any = strict_json(body)
    if not isinstance(value, dict) or type(value.get("format_version")) is not int or value["format_version"] != 1:
        raise ValueError("Unsupported manifest version")
    datasets: Any = value.get("datasets")
    if not isinstance(datasets, list) or not 1 <= len(datasets) <= 16:
        raise ValueError("Invalid dataset inventory")
    seen: set[str] = set()
    for dataset in datasets:
        validate_dataset(dataset)
        if dataset["id"] in seen:
            raise ValueError("Duplicate dataset")
        seen.add(dataset["id"])
    return value


def connect(path: Path) -> sqlite3.Connection:
    conn: sqlite3.Connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(False)
    conn.execute("PRAGMA trusted_schema=OFF")
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA cache_size=-512")
    conn.execute("PRAGMA mmap_size=0")
    return conn


def validate_file(path: Path, descriptor: dict[str, Any], budget: Budget | None = None) -> None:
    validate_dataset(descriptor)
    if path.stat().st_size != descriptor["artifact"]["bytes"]:
        raise ValueError("Snapshot size mismatch")
    digest: Any = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            if budget is not None:
                budget.checkpoint()
            chunk: bytes = handle.read(CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
    if digest.hexdigest() != descriptor["artifact"]["sha256"]:
        raise ValueError("Snapshot checksum mismatch")
    try:
        with closing(connect(path)) as conn:
            if budget is not None:
                budget.checkpoint()
                conn.set_progress_handler(budget.progress, 1000)
            _validate_database(conn, descriptor, budget)
    except sqlite3.Error as exc:
        raise ValueError("Invalid snapshot database") from exc


def _validate_database(conn: sqlite3.Connection, descriptor: dict[str, Any], budget: Budget | None = None) -> None:
    if conn.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID or conn.execute("PRAGMA user_version").fetchone()[0] != 1:
        raise ValueError("Unsupported SQLite snapshot")
    schema: list[sqlite3.Row] = conn.execute("SELECT type,name,sql FROM sqlite_schema").fetchall()
    if any(
        row["type"] not in {"table", "index"}
        or re.search(r"\bCREATE\s+VIRTUAL\s+TABLE\b", re.sub(r"/\*.*?\*/|--[^\n]*", " ", row["sql"] or "", flags=re.DOTALL), flags=re.IGNORECASE)
        for row in schema
    ):
        raise ValueError("Only ordinary snapshot tables and indexes are allowed")
    expected_tables: set[str] = set(COLUMNS) | {"sqlite_stat1", "sqlite_stat4"}
    if any(row["type"] == "table" and row["name"] not in expected_tables for row in schema):
        raise ValueError("Unknown snapshot table")
    for table, expected in COLUMNS.items():
        columns: set[str] = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if not expected <= columns:
            raise ValueError(f"Missing snapshot columns: {table}")
    for table, prefix in (
        ("products", ["store", "last_day", "product_id"]),
        ("products", ["store", "family_key", "product_id"]),
        ("search_names", ["store", "product_id"]),
        ("price_periods", ["store", "product_id", "first_day", "last_day"]),
    ):
        indexes: list[list[str]] = [
            [item["name"] for item in conn.execute("SELECT name FROM pragma_index_info(?)", (row["name"],))]
            for row in conn.execute(f"PRAGMA index_list({table})")
        ]
        if not any(columns[: len(prefix)] == prefix for columns in indexes):
            raise ValueError(f"Missing snapshot index: {table}")
    for key in ("schema_version", "dataset_id", "store", "source", "source_set_sha256", "builder_revision"):
        expected_value: str = str(descriptor["id" if key == "dataset_id" else key])
        found: sqlite3.Row | None = conn.execute("SELECT value FROM dataset_meta WHERE key=?", (key,)).fetchone()
        if found is None or found[0] != expected_value:
            raise ValueError(f"Snapshot identity mismatch: {key}")
    if [row[0] for row in conn.execute("PRAGMA integrity_check")] != ["ok"]:
        raise ValueError("Snapshot integrity failed")
    validate_contents(conn, descriptor, strict_json, budget)
