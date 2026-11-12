"""Meting voor het koppelen van folderregels aan producten: hoe ver lopen de van-prijzen van opeenvolgende aanbiedingen van een
product uiteen, en hoeveel folderregels en gekochte producten zijn er. Alleen lezen, er wordt niets gekoppeld.

Gebruik:
    python scripts/folder_price_margin.py <pad naar auth.sqlite3>

De database gaat open met `mode=ro`. Het rapport (Markdown, alleen aantallen) gaat naar stdout."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mealieshopper import name_keys, price_archive_tidy, price_drift

STORES: tuple[str, ...] = ("ah", "jumbo")

# Per winkel de aankoopbronnen: (benodigde tabellen, SQL met de kolommen title en unit_size). Zoals `prices._PURCHASES`, maar alleen
# wat de meting nodig heeft.
PURCHASE_SQL: dict[str, tuple[tuple[tuple[str, ...], str], ...]] = {
    "ah": (
        (
            ("ah_order_items", "ah_orders"),
            (
                "SELECT i.title AS title, i.unit_size AS unit_size FROM ah_order_items i JOIN ah_orders o ON o.order_id = i.order_id "
                "WHERE o.kind = 'delivered' AND i.product_id IS NOT NULL AND i.product_id != 0"
            ),
        ),
        (
            ("ah_receipt_items",),
            "SELECT name AS title, NULL AS unit_size FROM ah_receipt_items WHERE COALESCE(name, '') != ''",
        ),
        (
            ("imported_purchases",),
            "SELECT title, NULL AS unit_size FROM imported_purchases WHERE store = 'ah'",
        ),
    ),
    "jumbo": (
        (
            ("jumbo_order_items", "jumbo_orders"),
            (
                "SELECT i.title AS title, i.unit_size AS unit_size FROM jumbo_order_items i JOIN jumbo_orders o ON o.order_id = i.order_id "
                "WHERE o.kind = 'delivered' AND i.product_id IS NOT NULL"
            ),
        ),
        (
            ("imported_purchases",),
            "SELECT title, NULL AS unit_size FROM imported_purchases WHERE store = 'jumbo'",
        ),
    ),
}


def open_readonly(path: str) -> sqlite3.Connection:
    """De database alleen-lezen: een schrijfpoging geeft een OperationalError."""
    conn: sqlite3.Connection = sqlite3.connect(
        f"file:{Path(path).resolve()}?mode=ro", uri=True
    )
    conn.row_factory = sqlite3.Row
    return conn


def tables(conn: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }


def drift_rows(conn: sqlite3.Connection, store: str) -> list[dict[str, Any]]:
    """Sectie 1: de van-prijzen van producten met een productnummer, per product op volgorde van dag, als `price_drift.summary`."""
    if "offer_history" not in tables(conn):
        return price_drift.summary([])
    series: dict[str, list[tuple[str, float]]] = {}
    for row in conn.execute(
        "SELECT product_id, substr(COALESCE(valid_from, first_seen), 1, 10) AS day, price_before FROM offer_history "
        "WHERE store = ? AND product_id NOT LIKE 'folder:%' AND price_before > 0 ORDER BY product_id, day, id",
        (store,),
    ):
        series.setdefault(row["product_id"], []).append(
            (row["day"], float(row["price_before"]))
        )
    return price_drift.summary(
        [pair for points in series.values() for pair in price_drift.pairs(points)]
    )


def folder_lines(conn: sqlite3.Connection, store: str) -> list[dict[str, Any]]:
    """De folderregels van een winkel (`product_id` begint met `folder:`), als dicts voor `price_archive_tidy`."""
    if "offer_history" not in tables(conn):
        return []
    sql: str = "SELECT product_id, title, unit_size, price_before, promo_label FROM offer_history WHERE store = ? AND product_id LIKE 'folder:%'"
    return [dict(row) for row in conn.execute(sql, (store,))]


def folder_counts(lines: list[dict[str, Any]]) -> dict[str, int]:
    """Sectie 2 en 4: aantallen folderproducten en -regels, en producten met regels van verschillende verpakkingen."""
    sizes: dict[str, set[str]] = {}
    for line in lines:
        sizes.setdefault(line["product_id"], set())
        if line["unit_size"]:
            sizes[line["product_id"]].add(str(line["unit_size"]))
    return {
        "products": len(sizes),
        "lines": len(lines),
        "groups": sum(price_archive_tidy.is_group(line) for line in lines),
        "with_before": sum(bool(line["price_before"]) for line in lines),
        "known_pack": sum(price_archive_tidy.known_pack(line) for line in lines),
        "multi_pack": sum(len(found) > 1 for found in sizes.values()),
    }


def purchases(conn: sqlite3.Connection, store: str) -> dict[str, list[str]]:
    """De gekochte producten van de eigenaar: naamsleutel -> de teksten (naam met verpakking) waaronder hij ze kocht."""
    present: set[str] = tables(conn)
    found: dict[str, list[str]] = {}
    for needed, sql in PURCHASE_SQL.get(store, ()):
        if not set(needed) <= present:
            continue
        for row in conn.execute(sql):
            if row["title"]:
                found.setdefault(name_keys.key(row["title"]), []).append(
                    f"{row['title']} {row['unit_size'] or ''}".strip()
                )
    return found


def folder_matches(
    lines: list[dict[str, Any]], bought: dict[str, list[str]]
) -> dict[str, int]:
    """Sectie 3: bij hoeveel gekochte producten een folderproduct dezelfde naamsleutel heeft, en daarvan met gelijke verpakking."""
    by_key: dict[str, list[str]] = {}
    for line in lines:
        title: str = price_archive_tidy.source_title(line)
        by_key.setdefault(name_keys.key(title), []).append(
            f"{title} {line['unit_size'] or ''}".strip()
        )
    same_name: int = 0
    same_pack: int = 0
    for key, texts in bought.items():
        if key not in by_key:
            continue
        same_name += 1
        wanted: set[Any] = {price_archive_tidy.size(text) for text in texts} - {None}
        if wanted & {price_archive_tidy.size(text) for text in by_key[key]}:
            same_pack += 1
    return {"bought": len(bought), "same_name": same_name, "same_pack": same_pack}


def percent(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.1f}%"


def drift_table(rows: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = [
        "| Afstand | Paren | p50 | p90 | p95 |",
        "|---|---|---|---|---|",
    ]
    lines += [
        f"| {r['gap']} | {r['count']} | {percent(r['p50'])} | {percent(r['p90'])} | {percent(r['p95'])} |"
        for r in rows
    ]
    return lines


def store_section(conn: sqlite3.Connection, store: str) -> str:
    lines: list[dict[str, Any]] = folder_lines(conn, store)
    counts: dict[str, int] = folder_counts(lines)
    match: dict[str, int] = folder_matches(lines, purchases(conn, store))
    out: list[str] = [
        f"## {store}",
        "",
        "### Verschil tussen opeenvolgende van-prijzen (producten met productnummer)",
        "",
    ]
    out += drift_table(drift_rows(conn, store))
    out += [
        "",
        "### Folderregels",
        "",
        f"- Folderproducten: {counts['products']}",
        f"- Folderregels: {counts['lines']}",
        f"- Assortimentsregels: {counts['groups']}",
        f"- Folderregels met van-prijs: {counts['with_before']}",
        f"- Folderregels met leesbare verpakking: {counts['known_pack']}",
        "",
        "### Gekocht door de eigenaar",
        "",
        f"- Gekochte producten: {match['bought']}",
        f"- Met folderproduct van dezelfde naam: {match['same_name']}",
        f"- Met gelijke verpakking: {match['same_pack']}",
        "",
        "### Eenheid van koppelen",
        "",
        f"- Folderproducten met meer dan een verpakking: {counts['multi_pack']}",
        "",
    ]
    return "\n".join(out)


def report(path: str) -> str:
    """Het hele rapport als Markdown."""
    conn: sqlite3.Connection = open_readonly(path)
    try:
        return "\n".join(
            ["# Meting prijsmarge folderregels", ""]
            + [store_section(conn, store) for store in STORES]
        )
    finally:
        conn.close()


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    print(report(argv[1]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
