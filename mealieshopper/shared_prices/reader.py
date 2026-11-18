"""Bounded read-only operations on one pinned immutable generation."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .protocol import connect, strict_json

_CONNECTIONS: threading.BoundedSemaphore = threading.BoundedSemaphore(4)
MAX_IDS: int = 500
MAX_OBSERVATIONS: int = 20_000
MAX_RESULT_BYTES: int = 8 * 1024 * 1024


def _ids(values: Sequence[str]) -> tuple[str, ...]:
    if isinstance(values, str) or len(values) > MAX_IDS:
        raise ValueError("Snapshot selection exceeds limit")
    return tuple(dict.fromkeys(str(value) for value in values))


def _marks(values: Sequence[str]) -> str:
    return ",".join("?" for _ in values)


class Snapshot:
    """A leased file. Call its SQLite methods outside the event loop."""

    def __init__(self, path: Path, generation: str, store: str = "ah") -> None:
        self.path: Path = path
        self.generation: str = generation
        self.store: str = store

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        with _CONNECTIONS:
            conn: sqlite3.Connection = connect(self.path)
            try:
                yield conn
            finally:
                conn.close()

    def products(self, ids: Sequence[str]) -> dict[str, dict[str, Any]]:
        selected: tuple[str, ...] = _ids(ids)
        if not selected:
            return {}
        with self._connection() as conn:
            result: dict[str, dict[str, Any]] = {}
            for row in conn.execute(
                "SELECT p.product_id,v.metadata_json,f.facts_json,p.family_key,p.first_day,p.last_day "
                "FROM products p JOIN product_versions v ON v.version_id=p.version_id "
                "LEFT JOIN product_facts f ON f.store=p.store AND f.product_id=p.product_id "
                f"WHERE p.store=? AND p.product_id IN ({_marks(selected)})",
                (self.store, *selected),
            ):
                result[row["product_id"]] = {
                    **strict_json(row["metadata_json"]),
                    "facts": strict_json(row["facts_json"] or "{}"),
                    "family_key": row["family_key"],
                    "first_day": row["first_day"],
                    "last_day": row["last_day"],
                }
            return result

    def observations(self, ids: Sequence[str], since: str | None = None, until: str | None = None) -> dict[str, list[dict[str, Any]]]:
        selected: tuple[str, ...] = _ids(ids)
        start: date | None = date.fromisoformat(since) if since else None
        end: date | None = date.fromisoformat(until) if until else None
        result: dict[str, list[dict[str, Any]]] = {key: [] for key in selected}
        if not selected:
            return result
        count: int = 0
        payload: int = 0
        with self._connection() as conn:
            cursor: sqlite3.Cursor = conn.execute(
                "SELECT p.*,v.metadata_json FROM price_periods p JOIN product_versions v ON v.version_id=p.version_id "
                f"WHERE p.store=? AND p.product_id IN ({_marks(selected)}) "
                "AND (? IS NULL OR p.last_day>=?) AND (? IS NULL OR p.first_day<=?) ORDER BY p.product_id,p.first_day",
                (self.store, *selected, since, since, until, until),
            )
            for row in cursor:
                first: date = date.fromisoformat(row["first_day"])
                first_index: int = max(0, ((start - first).days + 6) // 7) if start else 0
                last_index: int = min(row["observation_count"] - 1, (end - first).days // 7) if end else row["observation_count"] - 1
                length: int = max(0, last_index - first_index + 1)
                count += length
                payload += length * (len(row["metadata_json"]) + len(row["values_json"]))
                if count > MAX_OBSERVATIONS or payload > MAX_RESULT_BYTES:
                    raise ValueError("Snapshot observation result exceeds limit. Request fewer products or a narrower date range")
                other: dict[str, Any] = {**strict_json(row["metadata_json"]), **strict_json(row["values_json"])}
                for index in range(first_index, last_index + 1):
                    result[row["product_id"]].append({**other, "day": (first + timedelta(days=index * 7)).isoformat()})
        return result

    def candidate_observations(
        self, ids: Sequence[str], since: str, until: str, excluded: dict[str, Sequence[str]]
    ) -> dict[str, list[dict[str, Any]]]:
        """Return the newest unmasked actual observation per constant-value period."""
        selected: tuple[str, ...] = _ids(ids)
        start: date = date.fromisoformat(since)
        end: date = date.fromisoformat(until)
        result: dict[str, list[dict[str, Any]]] = {key: [] for key in selected}
        if not selected:
            return result
        if sum(len(excluded.get(key, ())) for key in selected) > MAX_OBSERVATIONS:
            raise ValueError("Snapshot exclusion selection exceeds limit")
        masks: dict[str, set[str]] = {key: set(excluded.get(key, ())) for key in selected}
        count: int = 0
        payload: int = 0
        with self._connection() as conn:
            cursor: sqlite3.Cursor = conn.execute(
                "SELECT p.*,v.metadata_json FROM price_periods p JOIN product_versions v ON v.version_id=p.version_id "
                f"WHERE p.store=? AND p.product_id IN ({_marks(selected)}) "
                "AND p.last_day>=? AND p.first_day<=? ORDER BY p.product_id,p.first_day",
                (self.store, *selected, since, until),
            )
            for row in cursor:
                first: date = date.fromisoformat(row["first_day"])
                index: int = min(row["observation_count"] - 1, (end - first).days // 7)
                observed: date = first + timedelta(days=index * 7)
                while index >= 0 and observed.isoformat() in masks[row["product_id"]]:
                    index -= 1
                    observed = first + timedelta(days=index * 7)
                if index < 0 or observed < start:
                    continue
                count += 1
                payload += len(row["metadata_json"]) + len(row["values_json"])
                if count > MAX_OBSERVATIONS or payload > MAX_RESULT_BYTES:
                    raise ValueError("Snapshot candidate result exceeds limit")
                result[row["product_id"]].append(
                    {**strict_json(row["metadata_json"]), **strict_json(row["values_json"]), "day": observed.isoformat()}
                )
        return result

    def family_products(self, keys: Sequence[str]) -> dict[str, list[dict[str, Any]]]:
        selected: tuple[str, ...] = _ids(keys)
        result: dict[str, list[dict[str, Any]]] = {key: [] for key in selected}
        if not selected:
            return result
        with self._connection() as conn:
            for row in conn.execute(
                "SELECT p.family_key,v.metadata_json,f.facts_json FROM products p "
                "JOIN product_versions v ON v.version_id=p.version_id "
                "LEFT JOIN product_facts f ON f.store=p.store AND f.product_id=p.product_id "
                f"WHERE p.store=? AND p.family_key IN ({_marks(selected)}) ORDER BY p.product_id",
                (self.store, *selected),
            ):
                result[row["family_key"]].append({**strict_json(row["metadata_json"]), "facts": strict_json(row["facts_json"] or "{}")})
        return result

    def page(self, query: str = "", limit: int = 50, offset: int = 0, brands: Sequence[str] = (), categories: Sequence[str] = ()) -> dict[str, Any]:
        from mealieshopper.prices import _Query

        if not 1 <= limit <= 500 or offset < 0:
            raise ValueError("Invalid snapshot page")
        wanted: _Query = _Query(query)
        conditions: list[str] = ["p.store=?"]
        params: list[Any] = [self.store]
        terms: list[str] = []
        for word in wanted.words:
            terms.append("(instr(n.search,?)>0 OR instr(replace(n.search,' ',''),?)>0)")
            params.extend((word, word))
        if terms:
            conditions.append(
                "EXISTS(SELECT 1 FROM search_names n WHERE n.store=p.store AND n.product_id=p.product_id AND " + " AND ".join(terms) + ")"
            )
        if wanted.size:
            amount, unit = wanted.size
            conditions.append(
                "NOT EXISTS(SELECT 1 FROM search_names n WHERE n.store=p.store AND n.product_id=p.product_id AND n.amount IS NOT NULL) "
                "OR EXISTS(SELECT 1 FROM search_names n WHERE n.store=p.store AND n.product_id=p.product_id "
                "AND n.unit=? AND abs(n.amount-?)<=0.005*max(n.amount,?))"
            )
            conditions[-1] = f"({conditions[-1]})"
            params.extend((unit, amount, amount))
        for column, values in (("brand", brands), ("category", categories)):
            selected: tuple[str, ...] = _ids(values)
            if selected:
                conditions.append(f"p.{column} IN ({_marks(selected)})")
                params.extend(selected)
        where: str = " AND ".join(conditions)
        with self._connection() as conn:
            total: int = conn.execute(f"SELECT COUNT(*) FROM products p WHERE {where}", params).fetchone()[0]
            ids: list[str] = [
                row[0]
                for row in conn.execute(
                    f"SELECT p.product_id FROM products p WHERE {where} ORDER BY p.last_day DESC,p.product_id LIMIT ? OFFSET ?",
                    (*params, limit, offset),
                )
            ]
            facets: dict[str, list[str]] = {
                plural: [
                    row[0]
                    for row in conn.execute(
                        f"SELECT DISTINCT p.{column} FROM products p WHERE {where} AND coalesce(p.{column},'')!='' ORDER BY p.{column}", params
                    )
                ]
                for column, plural in (("brand", "brands"), ("category", "categories"))
            }
        products: dict[str, dict[str, Any]] = self.products(ids)
        return {"items": [products[key] for key in ids], "total": total, "brands": facets["brands"], "categories": facets["categories"]}
