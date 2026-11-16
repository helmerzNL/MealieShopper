"""Small deterministic snapshots for shared reader and overlay tests."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any


def build_snapshot(path: Path, rows: list[dict[str, Any]]) -> dict[str, Any]:
    from mealieshopper import prices

    path.parent.mkdir(parents=True, exist_ok=True)
    conn: sqlite3.Connection = sqlite3.connect(path)
    conn.executescript("""
PRAGMA application_id=1297305649;
PRAGMA user_version=1;
CREATE TABLE dataset_meta(key TEXT PRIMARY KEY,value TEXT);
CREATE TABLE source_documents(path TEXT PRIMARY KEY,sha256 TEXT,bytes INTEGER,items INTEGER);
CREATE TABLE product_versions(version_id INTEGER PRIMARY KEY,metadata_json TEXT);
CREATE TABLE products(store TEXT,product_id TEXT,title TEXT,brand TEXT,unit_size TEXT,image TEXT,url TEXT,
first_day TEXT,last_day TEXT,version_id INTEGER,family_key TEXT,category TEXT,twin_key TEXT,PRIMARY KEY(store,product_id));
CREATE INDEX products_recent ON products(store,last_day,product_id);
CREATE INDEX products_family ON products(store,family_key,product_id);
CREATE TABLE search_names(store TEXT,product_id TEXT,name TEXT,search TEXT,amount REAL,unit TEXT);
CREATE INDEX search_product ON search_names(store,product_id);
CREATE TABLE price_periods(store TEXT,product_id TEXT,source TEXT,first_day TEXT,last_day TEXT,step_days INTEGER,
observation_count INTEGER,version_id INTEGER,price REAL,values_json TEXT,PRIMARY KEY(store,product_id,source,first_day));
CREATE INDEX periods_product ON price_periods(store,product_id,first_day,last_day);
CREATE TABLE product_facts(store TEXT,product_id TEXT,facts_json TEXT,PRIMARY KEY(store,product_id));
""")
    meta: dict[str, str] = {
        "schema_version": "1",
        "dataset_id": "ah-ahprijsindex",
        "store": "ah",
        "source": "ahprijsindex",
        "source_set_sha256": "a" * 64,
        "builder_revision": "fixture-v1",
    }
    conn.executemany("INSERT INTO dataset_meta VALUES (?,?)", meta.items())
    grouped: dict[str, list[dict[str, Any]]] = {}
    for index, row in enumerate(sorted(rows, key=lambda item: (item["product_id"], item["day"])), 1):
        metadata: dict[str, Any] = {key: value for key, value in row.items() if key not in {"day", "price", "seen_at"}}
        values: dict[str, Any] = {key: row[key] for key in ("price", "seen_at") if key in row}
        conn.execute("INSERT INTO product_versions VALUES (?,?)", (index, json.dumps(metadata)))
        conn.execute(
            "INSERT INTO price_periods VALUES (?,?,?,?,?,?,?,?,?,?)",
            (row["store"], row["product_id"], row["source"], row["day"], row["day"], 7, 1, index, row.get("price"), json.dumps(values)),
        )
        size: tuple[float, str] | None = prices._size_of(row.get("unit_size"), row.get("title"))
        conn.execute(
            "INSERT INTO search_names VALUES (?,?,?,?,?,?)",
            (
                row["store"],
                row["product_id"],
                row.get("title", ""),
                prices._fold_name(f"{row.get('title') or ''} {row.get('brand') or ''}"),
                *(size or (None, None)),
            ),
        )
        grouped.setdefault(row["product_id"], []).append({**row, "_version": index})
    for product_id, history in grouped.items():
        latest: dict[str, Any] = history[-1]
        sizes: set[tuple[float, str]] = {size for row in history if (size := prices._size_of(row.get("unit_size"), row.get("title")))}
        conn.execute(
            "INSERT INTO products VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "ah",
                product_id,
                latest.get("title"),
                latest.get("brand"),
                latest.get("unit_size"),
                latest.get("image"),
                latest.get("url"),
                history[0]["day"],
                latest["day"],
                latest["_version"],
                prices.base_title(latest.get("title")),
                latest.get("category", ""),
                prices._same_key(product_id, latest.get("title"), latest.get("brand"), sizes),
            ),
        )
        facts: dict[str, Any] = {
            "rule_version": 1,
            "observation_count": len(history),
            "first_day": history[0]["day"],
            "last_day": latest["day"],
            "promotional_coverage": False,
        }
        conn.execute("INSERT INTO product_facts VALUES (?,?,?)", ("ah", product_id, json.dumps(facts)))
    conn.commit()
    conn.close()
    body: bytes = path.read_bytes()
    digest: str = hashlib.sha256(body).hexdigest()
    return {
        "id": "ah-ahprijsindex",
        "store": "ah",
        "source": "ahprijsindex",
        "schema_version": 1,
        "source_commit": "fixture",
        "source_set_sha256": "a" * 64,
        "builder_revision": "fixture-v1",
        "coverage": {},
        "required_sqlite": {"minimum_version": "3.8.0", "features": []},
        "artifact": {"path": f"shared-prices/v1/ah-ahprijsindex/{digest}.sqlite", "bytes": len(body), "sha256": digest, "compression": "identity"},
    }
