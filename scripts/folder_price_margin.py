"""Meting voor het koppelen van folderregels aan producten: hoe ver lopen de van-prijzen van opeenvolgende aanbiedingen van een
product uiteen, en hoeveel folderregels en gekochte producten zijn er. Alleen lezen, er wordt niets gekoppeld.

Gebruik:
    python scripts/folder_price_margin.py <pad naar auth.sqlite3>

De database gaat open met `mode=ro`. Het rapport (Markdown, alleen aantallen) gaat naar stdout."""

from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mealieshopper import deals, price_archive_tidy, price_drift

STORES: tuple[str, ...] = ("ah", "jumbo")

# "3 stapelen voor: 1.00 Los", "2 voor: 0.79": de foldertekst met een dubbele punt (`price_archive_tidy.short_label`), die
# `deals.parse_mechanic` met opzet niet als actie leest. De van-prijs is dan de prijs voor dat aantal.
MULTI_BUY_COLON: re.Pattern[str] = re.compile(r"\b(\d+)\s+(?:[a-z]+\s+)?voor\s*:")

# Per winkel de aankoopbronnen: (benodigde tabellen, SQL met de kolommen product_id, title en unit_size). Zoals `prices._PURCHASES`
# en `prices._RECEIPTS_SQL`, maar alleen wat de meting nodig heeft. De bron staat vooraan in de sleutel van een product.
PURCHASE_SQL: dict[str, tuple[tuple[str, tuple[str, ...], str], ...]] = {
    "ah": (
        (
            "bestelling",
            ("ah_order_items", "ah_orders"),
            ("SELECT CAST(i.product_id AS TEXT) AS product_id, i.title AS title, i.unit_size AS unit_size "
             "FROM ah_order_items i JOIN ah_orders o ON o.order_id = i.order_id "
             "WHERE o.kind = 'delivered' AND i.product_id IS NOT NULL AND i.product_id != 0"),
        ),
        (
            "kassabon",
            ("ah_receipt_items", "ah_receipts"),
            ("SELECT CASE WHEN l.product_id IS NULL OR l.product_id = 0 THEN NULL ELSE CAST(l.product_id AS TEXT) END AS product_id, "
             "l.name AS title, NULL AS unit_size "
             "FROM ah_receipt_items l JOIN ah_receipts r ON r.receipt_id = l.receipt_id WHERE COALESCE(l.name, '') != ''"),
        ),
        (
            "import",
            ("imported_purchases",),
            "SELECT NULL AS product_id, title, NULL AS unit_size FROM imported_purchases WHERE store = 'ah'",
        ),
    ),
    "jumbo": (
        (
            "bestelling",
            ("jumbo_order_items", "jumbo_orders"),
            ("SELECT CAST(i.product_id AS TEXT) AS product_id, i.title AS title, i.unit_size AS unit_size "
             "FROM jumbo_order_items i JOIN jumbo_orders o ON o.order_id = i.order_id "
             "WHERE o.kind = 'delivered' AND i.product_id IS NOT NULL"),
        ),
        (
            "import",
            ("imported_purchases",),
            "SELECT NULL AS product_id, title, NULL AS unit_size FROM imported_purchases WHERE store = 'jumbo'",
        ),
    ),
}


def open_readonly(path: str) -> sqlite3.Connection:
    """De database alleen-lezen: een schrijfpoging geeft een OperationalError."""
    conn: sqlite3.Connection = sqlite3.connect(
        Path(path).resolve().as_uri() + "?mode=ro", uri=True
    )
    conn.row_factory = sqlite3.Row
    return conn


def tables(conn: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }


def is_multi_buy(offer: dict[str, Any]) -> bool:
    """Heeft de aanbieding een minimumaantal boven 1? Volgens `deals.describe`, of in de vorm "N voor: ..." van een foldertekst."""
    for item in offer["rows"]:
        label: str | None = item["promo_label"]
        described: dict[str, Any] = deals.describe(
            label, price_now=item["price"], price_before=item["price_before"]
        )
        found: re.Match[str] | None = MULTI_BUY_COLON.search(str(label or "").lower())
        if (described["minQuantity"] or 1) > 1 or (
            found is not None and int(found.group(1)) > 1
        ):
            return True
    return False


def offers_per_product(
    conn: sqlite3.Connection, store: str
) -> tuple[int, dict[str, list[dict[str, Any]]]]:
    """Het aantal gelezen rijen en per product de aanbiedingen (`price_drift.chain_offers`).

    Rijen met een productnummer zonder `folder:`, ook die het prijsarchief al aan een echt product koppelde (zoals
    `prices._offer_points`). De dag is `valid_from`, of `first_seen` als die leeg is."""
    if "offer_history" not in tables(conn):
        return 0, {}
    grouped: dict[str, list[dict[str, Any]]] = {}
    read: int = 0
    for found in conn.execute(
        "SELECT product_id, substr(COALESCE(NULLIF(valid_from, ''), first_seen), 1, 10) AS day, price_before, price, promo_label, "
        "continued FROM offer_history WHERE store = ? AND product_id NOT LIKE 'folder:%' ORDER BY product_id, first_seen, id",
        (store,),
    ):
        read += 1
        grouped.setdefault(found["product_id"], []).append(dict(found))
    return read, {pid: price_drift.chain_offers(rows) for pid, rows in grouped.items()}


def drift_stats(offers: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """De van-prijzen van opeenvolgende aanbiedingen als `price_drift.summary`, met de aantallen erachter."""
    found: list[tuple[int, float]] = []
    points: int = 0
    priced: int = 0
    for product_offers in offers.values():
        series: list[tuple[str, float]] = price_drift.daily_points(product_offers)
        priced += sum(offer["price_before"] is not None for offer in product_offers)
        points += len(series)
        found += price_drift.pairs(series)
    nonzero: list[tuple[int, float]] = [pair for pair in found if pair[0] > 0]
    return {
        "summary": price_drift.summary(nonzero),
        "offers": sum(len(v) for v in offers.values()),
        "products": sum(bool(v) for v in offers.values()),
        "merged_days": priced - points,
        "pairs": len(nonzero),
        "zero_dropped": len(found) - len(nonzero),
    }


def percent(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.1f}%"


def drift_section(title: str, stats: dict[str, Any]) -> list[str]:
    lines: list[str] = [
        f"### {title}",
        "",
        "| Afstand | Paren | p50 | p90 | p95 |",
        "|---|---|---|---|---|",
    ]
    lines += [
        f"| {r['gap']} | {r['count']} | {percent(r['p50'])} | {percent(r['p90'])} | {percent(r['p95'])} |"
        for r in stats["summary"]
    ]
    return lines + [
        "",
        f"- Aanbiedingen na samenvoegen van ketens: {stats['offers']}",
        f"- Producten: {stats['products']}",
        f"- Aanbiedingen op een dag met een andere aanbieding van hetzelfde product (een punt per dag): {stats['merged_days']}",
        f"- Paren: {stats['pairs']}",
        f"- Paren van 0 dagen weggelaten: {stats['zero_dropped']}",
        "",
    ]


def folder_lines(conn: sqlite3.Connection, store: str) -> list[dict[str, Any]]:
    """De folderregels van een winkel (`product_id` begint met `folder:`), als dicts voor `price_archive_tidy`."""
    if "offer_history" not in tables(conn):
        return []
    sql: str = (
        "SELECT product_id, title, unit_size, price_before, promo_label FROM offer_history "
        "WHERE store = ? AND product_id LIKE 'folder:%'"
    )
    return [dict(found) for found in conn.execute(sql, (store,))]


def line_pack(line: dict[str, Any]) -> Any:
    """De verpakking van een regel (`price_archive_tidy.size`): uit `unit_size`, anders uit de titel. None als ze niet leesbaar is."""
    return price_archive_tidy.size(line["unit_size"] or "") or price_archive_tidy.size(
        price_archive_tidy.source_title(line)
    )


def folder_counts(lines: list[dict[str, Any]]) -> dict[str, int]:
    """Sectie 2 en 4: aantallen folderproducten en -regels, en producten met regels van verschillende verpakkingen."""
    units: dict[str, set[str]] = {}
    packs: dict[str, set[Any]] = {}
    for line in lines:
        units.setdefault(line["product_id"], set())
        packs.setdefault(line["product_id"], set())
        if line["unit_size"]:
            units[line["product_id"]].add(str(line["unit_size"]))
        if line_pack(line) is not None:
            packs[line["product_id"]].add(line_pack(line))
    return {
        "products": len(units),
        "lines": len(lines),
        "groups": sum(price_archive_tidy.is_group(line) for line in lines),
        "with_before": sum(bool(line["price_before"]) for line in lines),
        "known_pack": sum(price_archive_tidy.known_pack(line) for line in lines),
        "multi_unit": sum(len(found) > 1 for found in units.values()),
        "multi_pack": sum(len(found) > 1 for found in packs.values()),
    }


def purchases(
    conn: sqlite3.Connection, store: str
) -> tuple[dict[str, set[tuple[str, Any]]], dict[str, set[tuple[str, Any]]]]:
    """De gekochte producten van de eigenaar, als (met productnummer, zonder productnummer).

    Beide: sleutel -> de (gevouwen naam, verpakking) waaronder het gekocht is. Met productnummer is de sleutel de bron met het
    nummer (een artikelnummer van een kassabon is niet hetzelfde als een nummer van de webwinkel), zonder is het de gevouwen naam.
    De verpakking is `size` van `unit_size`, of van de titel (kassabon en import hebben geen `unit_size`); None als ze niet leesbaar is."""
    present: set[str] = tables(conn)
    numbered: dict[str, set[tuple[str, Any]]] = {}
    unnumbered: dict[str, set[tuple[str, Any]]] = {}
    for source, needed, sql in PURCHASE_SQL.get(store, ()):
        if not set(needed) <= present:
            continue
        for found in conn.execute(sql):
            if not found["title"]:
                continue
            name: str = price_archive_tidy.fold(found["title"])
            pack: Any = price_archive_tidy.size(
                found["unit_size"] or ""
            ) or price_archive_tidy.size(found["title"])
            key: str = (
                f"{source}:{found['product_id']}" if found["product_id"] else name
            )
            (numbered if found["product_id"] else unnumbered).setdefault(
                key, set()
            ).add((name, pack))
    return numbered, unnumbered


def match_counts(
    lines: list[dict[str, Any]], bought: dict[str, set[tuple[str, Any]]]
) -> dict[str, int]:
    """Bij hoeveel aankopen een folderregel dezelfde gevouwen naam heeft, en daarvan met gelijke verpakking.

    De gelijke verpakking telt alleen over aankopen met een leesbare verpakking (`readable`): een ondergrens."""
    folder_packs: dict[str, set[Any]] = {}
    for line in lines:
        folder_packs.setdefault(
            price_archive_tidy.fold(price_archive_tidy.source_title(line)), set()
        ).add(line_pack(line))
    same_name: int = 0
    same_pack: int = 0
    readable: int = 0
    for variants in bought.values():
        named: list[tuple[str, Any]] = [
            (name, pack) for name, pack in variants if name in folder_packs
        ]
        same_name += bool(named)
        known: list[tuple[str, Any]] = [
            (name, pack) for name, pack in named if pack is not None
        ]
        readable += bool(known)
        same_pack += any(pack in folder_packs[name] for name, pack in known)
    return {
        "bought": len(bought),
        "same_name": same_name,
        "readable": readable,
        "same_pack": same_pack,
    }


def match_section(title: str, counts: dict[str, int]) -> list[str]:
    return [
        f"- {title}: {counts['bought']}",
        f"- Daarvan met folderregel van dezelfde naam: {counts['same_name']}",
        f"- Daarvan met dezelfde naam en een leesbare verpakking: {counts['readable']}",
        f"- Daarvan met gelijke verpakking (ondergrens, over de vorige regel): {counts['same_pack']}",
    ]


def store_section(conn: sqlite3.Connection, store: str) -> str:
    read, offers = offers_per_product(conn, store)
    single: dict[str, list[dict[str, Any]]] = {
        pid: [o for o in found if not is_multi_buy(o)] for pid, found in offers.items()
    }
    lines: list[dict[str, Any]] = folder_lines(conn, store)
    counts: dict[str, int] = folder_counts(lines)
    numbered, unnumbered = purchases(conn, store)
    out: list[str] = [
        f"## {store}",
        "",
        f"- Gelezen rijen van producten met productnummer (ook door het archief gekoppelde): {read}",
        "",
    ]
    out += drift_section(
        "Verschil tussen van-prijzen van opeenvolgende aanbiedingen: alle aanbiedingen",
        drift_stats(offers),
    )
    out += drift_section(
        "Verschil tussen van-prijzen van opeenvolgende aanbiedingen: alleen aanbiedingen zonder minimumaantal",
        drift_stats(single),
    )
    out += [
        "### Folderregels",
        "",
        f"- Folderproducten: {counts['products']}",
        f"- Folderregels: {counts['lines']}",
    ]
    out += [
        f"- Assortimentsregels: {counts['groups']}",
        f"- Folderregels met van-prijs: {counts['with_before']}",
    ]
    out += [
        f"- Folderregels met leesbare verpakking: {counts['known_pack']}",
        "",
        "### Gekocht door de eigenaar",
        "",
    ]
    out += match_section(
        "Gekochte producten met productnummer", match_counts(lines, numbered)
    )
    out += match_section(
        "Gekochte namen zonder productnummer (kassabonregels zonder id en ingelezen mails, verpakking uit de titel)",
        match_counts(lines, unnumbered),
    )
    out += [
        "",
        "### Eenheid van koppelen",
        "",
        f"- Folderproducten met meer dan een unit_size: {counts['multi_unit']}",
    ]
    out += [
        f"- Folderproducten met meer dan een verpakking uit de tekst: {counts['multi_pack']}",
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
