"""Indexed candidates for private folder lines, selected by product word pairs."""

from __future__ import annotations

import json
from itertools import combinations
from typing import Any

import aiosqlite

from . import compute, line_offers

SCHEMA: str = """
CREATE TABLE IF NOT EXISTS shared_price_lines (
 store TEXT NOT NULL,product_id TEXT NOT NULL,first_word TEXT NOT NULL,second_word TEXT NOT NULL,
 kind TEXT NOT NULL,title TEXT NOT NULL,PRIMARY KEY(store,product_id,first_word,second_word,kind)
);
CREATE INDEX IF NOT EXISTS shared_price_lines_pair ON shared_price_lines(store,first_word,second_word,kind,product_id);
"""


def entries(store: str, rows: list[Any]) -> list[tuple[str, str, str, str, str, str]]:
    found: set[tuple[str, str, str, str, str, str]] = set()
    for pid, title in rows:
        for needed, _ in line_offers._parts(title):
            first, second = line_offers._top_two(needed)
            found.add((store, pid, first, second, "", title or ""))
            if len(needed) - 1 >= line_offers.NAMED_WORDS:
                for outside in needed:
                    first, second = line_offers._top_two(word for word in needed if word != outside)
                    for start in range(len(outside) - line_offers.CATEGORY_WORD_LENGTH + 1):
                        found.add((store, pid, first, second, outside[start:], title or ""))
    return sorted(found)


async def prepare_products(conn: aiosqlite.Connection, store: str, ids: list[str]) -> None:
    selected: list[str] = [pid for pid in ids if pid.startswith("folder:")]
    if not selected:
        return
    rows: list[Any] = list(
        await conn.execute_fetchall(
            "SELECT product_id,MIN(title) FROM offer_history WHERE store=? AND product_id IN(SELECT value FROM json_each(?)) GROUP BY product_id",
            (store, json.dumps(selected)),
        )
    )
    values: list[tuple[str, str, str, str, str, str]] = await compute.run(entries, store, rows)
    await conn.executemany("DELETE FROM shared_price_lines WHERE store=? AND product_id=?", [(store, pid) for pid in selected])
    await conn.executemany("INSERT INTO shared_price_lines VALUES(?,?,?,?,?,?)", values)


def wanted(products: dict[str, Any]) -> tuple[list[list[str]], list[str]]:
    from . import prices

    pairs: set[tuple[str, str]] = set()
    kinds: set[str] = {""}
    for pid, known in products.items():
        if pid.startswith("folder:"):
            continue
        kinds.update(line_offers.category_words(known[prices._CATEGORY]))
        for name in known[prices._NAMES]:
            words: list[str] = sorted(line_offers.words(name), key=line_offers._rank, reverse=True)
            pairs.update(combinations(words, 2))
    return [list(pair) for pair in sorted(pairs)], sorted(kinds)


async def candidates(conn: aiosqlite.Connection, store: str, products: dict[str, Any]) -> list[Any]:
    from . import auth, shared_price_overlay

    pairs, kinds = await compute.run(wanted, products)
    if not pairs:
        return []
    current: str = await shared_price_overlay.revision(conn, store)
    state: Any = await auth.fetchone(conn, "SELECT ready_revision FROM shared_price_build WHERE store=?", (store,))
    if state and state[0] == current:
        return list(
            await conn.execute_fetchall(
                "SELECT DISTINCT l.product_id,l.title FROM shared_price_lines l "
                "JOIN json_each(?) p ON l.first_word=json_extract(p.value,'$[0]') AND l.second_word=json_extract(p.value,'$[1]') "
                "WHERE l.store=? AND l.kind IN(SELECT value FROM json_each(?)) ORDER BY l.product_id",
                (json.dumps(pairs), store, json.dumps(kinds)),
            )
        )
    pair_set: set[tuple[str, str]] = {tuple(pair) for pair in pairs}
    kind_set: set[str] = set(kinds)

    def possible(title: str) -> int:
        return int(any((first, second) in pair_set and kind in kind_set for _, _, first, second, kind, _ in entries("", [("", title)])))

    await conn.create_function("ms_folder_candidate", 1, possible, deterministic=True)
    return list(
        await conn.execute_fetchall(
            "WITH names AS MATERIALIZED (SELECT product_id,MIN(title) AS title FROM offer_history "
            "WHERE store=? AND product_id LIKE 'folder:%' GROUP BY product_id) "
            "SELECT product_id,title FROM names WHERE ms_folder_candidate(title) ORDER BY product_id",
            (store,),
        )
    )
