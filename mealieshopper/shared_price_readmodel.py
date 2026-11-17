"""Selected-product reads and a SQL union of public and private search data."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar, Token
from typing import Any, NamedTuple

import aiosqlite

from . import compute, price_history_index, shared_price_overlay

_PINNED: ContextVar[tuple[str, Any] | None] = ContextVar("prepared_response_generation", default=None)


@contextmanager
def response_lease(store: str) -> Iterator[Any]:
    from . import shared_prices

    current: tuple[str, Any] | None = _PINNED.get()
    if current is not None and current[0] == store:
        yield current[1]
        return
    with shared_prices.lease(store) as snapshot:
        token: Token[Any] = _PINNED.set((store, snapshot))
        try:
            yield snapshot
        finally:
            _PINNED.reset(token)


async def install(conn: aiosqlite.Connection, snapshot: Any, stores: list[str], today: str, receipts: dict[str, list[dict[str, Any]]]) -> None:
    """Keep the existing page queries against an ephemeral view, never a public copy."""
    await conn.execute("DROP VIEW IF EXISTS temp.price_history_products")
    from . import shared_price_transition

    if snapshot is not None:
        await attach(conn, snapshot)
    await shared_price_transition.install(conn, snapshot, stores, today, receipts)
    from . import shared_price_search

    await conn.create_function("ms_history_merged_key", 2, shared_price_search.merged_key, deterministic=True)
    pending: list[Any] = list(await conn.execute_fetchall("SELECT 1 FROM _private_history_context WHERE ready=0 LIMIT 1"))
    if not pending:
        await shared_price_search.install(conn, snapshot)
        return
    sql: str = """
    CREATE TEMP VIEW price_history_products AS
    WITH public AS (
      SELECT p.store,p.product_id,p.last_day AS last,
       COALESCE((SELECT json_group_array(search) FROM shared_public.search_names n
                 WHERE n.store=p.store AND n.product_id=p.product_id),'[]') AS names,
       COALESCE((SELECT json_group_array(json_array(amount,unit)) FROM
                  (SELECT DISTINCT amount,unit FROM shared_public.search_names n
                   WHERE n.store=p.store AND n.product_id=p.product_id AND amount IS NOT NULL)),'[]') AS sizes,
       COALESCE(p.brand,'') AS brand,ms_history_category(p.store,p.title) AS category,
       COALESCE((SELECT group_concat(search || char(10) || replace(search,' ',''),char(10))
                 FROM shared_public.search_names n WHERE n.store=p.store AND n.product_id=p.product_id),'') AS search,
       p.twin_key AS same_key
      FROM shared_public.products p
    ), combined AS (
      SELECT p.store,p.product_id,max(p.last,COALESCE(l.last,'')) AS last,
       CASE WHEN l.product_id IS NULL THEN p.names ELSE
         (SELECT json_group_array(value) FROM (SELECT value FROM json_each(p.names) UNION SELECT value FROM json_each(l.names))) END AS names,
       CASE WHEN l.product_id IS NULL THEN p.sizes ELSE
         (SELECT json_group_array(json(value)) FROM (SELECT value FROM json_each(p.sizes) UNION SELECT value FROM json_each(l.sizes))) END AS sizes,
       COALESCE(NULLIF(l.brand,''),p.brand) AS brand,COALESCE(NULLIF(l.category,''),p.category) AS category,
       p.search || char(10) || COALESCE(l.search,'') AS search,
       COALESCE(NULLIF(ms_history_merged_key(COALESCE(NULLIF(l.same_key,''),p.same_key),CASE WHEN l.product_id IS NULL THEN p.sizes ELSE (SELECT json_group_array(json(value)) FROM (SELECT value FROM json_each(p.sizes) UNION SELECT value FROM json_each(l.sizes))) END),''),'id:' || p.store || ':' || p.product_id) AS same_key
      FROM public p LEFT JOIN temp._private_history_products l USING(store,product_id)
      UNION ALL
      SELECT l.store,l.product_id,l.last,l.names,l.sizes,l.brand,l.category,l.search,
       COALESCE(NULLIF(l.same_key,''),'id:' || l.store || ':' || l.product_id)
      FROM temp._private_history_products l
      WHERE NOT EXISTS(SELECT 1 FROM public p WHERE p.store=l.store AND p.product_id=l.product_id)
    ), grouped AS (
      SELECT *,first_value(product_id) OVER(PARTITION BY store,same_key ORDER BY last DESC,product_id DESC) AS lead,
       max(last) OVER(PARTITION BY store,same_key) AS day,
       count(*) OVER(PARTITION BY store,same_key) AS members
      FROM combined
    )
    SELECT store,product_id,last,names,sizes,brand,category,search,lead,
      CASE WHEN members>1 THEN (SELECT json_group_array(product_id) FROM
        (SELECT product_id FROM combined c WHERE c.store=g.store AND c.same_key=g.same_key ORDER BY product_id)) END AS twins,day
    FROM grouped g
    """
    if snapshot is None:
        start: int = sql.index("      SELECT p.store")
        end: int = sql.index("    ), combined AS", start)
        sql = (
            sql[:start]
            + "SELECT NULL AS store,NULL AS product_id,NULL AS last,'[]' AS names,'[]' AS sizes,'' AS brand,'' AS category,'' AS search,'' AS same_key WHERE 0\n"
            + sql[end:]
        )
    await conn.execute(sql)


def _selected_rows(sql: str) -> str:
    if " GROUP BY " in sql:
        source, group = sql.rsplit(" GROUP BY ", 1)
        return source + " AND product_id IN (SELECT value FROM json_each(?)) GROUP BY " + group
    return f"SELECT * FROM ({sql}) WHERE product_id IN (SELECT value FROM json_each(?))"


async def catalogue(
    conn: aiosqlite.Connection,
    snapshot: Any,
    store: str,
    today: str,
    ids: list[str],
    observed: dict[str, list[Any]] | None = None,
    include_lines: bool = True,
    receipts: list[dict[str, Any]] | None = None,
) -> Any:
    """Only the selected products use legacy rows while migration is in progress."""
    from . import prices

    wanted: str = json.dumps(ids)
    receipts = await receipt_rows(conn, snapshot, store, today, ids) if receipts is None else await compute.run(_wanted_receipts, receipts, set(ids))
    observed = observed if observed is not None else await shared_price_overlay.observations(conn, snapshot, store, ids)
    observations: list[tuple[Any, ...]] = await compute.run(_known_observations, observed)
    tables: set[str] = await prices._tables(conn)
    rows: Any = prices._CatalogueRows(
        await prices._plain_rows(conn, _selected_rows(prices._KNOWN_OFFERS_SQL), (store, wanted)),
        observations,
        [
            await prices._plain_rows(conn, _selected_rows(prices._KNOWN_PURCHASES_SQL.format(sql=sql)), (today, wanted))
            for sql in prices._purchase_sources(tables, store)
        ],
        [],
        await prices._plain_rows(conn, _selected_rows(prices._KNOWN_UPCOMING_SQL), (today, store, today, today, store, wanted)),
        await prices._plain_rows(conn, _selected_rows(prices._CATEGORIES_SQL), (store, wanted)),
        [],
        None,
    )
    selected: Any = await compute.run(_catalogue, store, rows, receipts)
    if include_lines:
        from . import shared_price_lines

        folders: list[Any] = await shared_price_lines.candidates(conn, store, selected.products)
        selected = selected._replace(lines=await compute.run(prices._line_index, folders))
    return selected


def _wanted_receipts(receipts: list[dict[str, Any]], ids: set[str]) -> list[dict[str, Any]]:
    return [row for row in receipts if row["product_id"] in ids]


def _known_observations(observed: dict[str, list[Any]]) -> list[tuple[Any, ...]]:
    return [(pid, row["title"], row["brand"], row["unit_size"], row["day"]) for pid, rows in observed.items() for row in rows]


def _catalogue(store: str, rows: Any, receipts: list[dict[str, Any]]) -> Any:
    from . import prices

    known: dict[str, Any] = prices._known_products(store, rows, receipts)
    return prices._Catalogue(receipts, known, prices._line_index(rows.folder_products), prices._twins(known))


async def index_batch(conn: aiosqlite.Connection, snapshot: Any, store: str, today: str, ids: list[str]) -> None:
    """Reuse the existing private search index, one bounded product batch at a time."""
    from . import prices

    old_keys: list[str] = [
        row[0]
        for row in await conn.execute_fetchall(
            "SELECT same_key FROM shared_price_keys WHERE store=? AND product_id IN(SELECT value FROM json_each(?))", (store, json.dumps(ids))
        )
    ]
    private_observed: dict[str, list[Any]] = await private_metadata(conn, snapshot, store, ids)
    from . import shared_price_dirty

    generation: str = getattr(snapshot, "generation", "") if snapshot is not None else ""
    # The build and the drain bring the receipt lines up to date themselves (`shared_price_dirty`): read what is stored.
    linked: list[dict[str, Any]] | None = (
        await _stored_receipts(conn, store, today, ids) if await shared_price_dirty.receipts_linked(conn, store, generation) else None
    )
    selected: Any = await catalogue(conn, snapshot, store, today, ids, private_observed, False, receipts=linked)
    from . import shared_price_lines

    await shared_price_lines.prepare_products(conn, store, ids)
    private_ids: list[str] = list(private_observed)
    # Offers and purchases remain private even where all observations are equivalent.
    tables: set[str] = await prices._tables(conn)
    for sql, args in [
        ("SELECT product_id FROM offer_history WHERE store=?", (store,)),
        ("SELECT product_id FROM offers WHERE store=?", (store,)),
        *[(sql, ()) for sql in prices._purchase_sources(tables, store)],
    ]:
        private_ids += [
            row[0]
            for row in await conn.execute_fetchall(
                f"SELECT DISTINCT product_id FROM ({sql}) WHERE product_id IN (SELECT value FROM json_each(?))", (*args, json.dumps(ids))
            )
        ]
    private: set[str] = set(private_ids) | {row["product_id"] for row in selected.receipts}
    products: dict[str, Any] = {pid: known for pid, known in selected.products.items() if pid in private}
    rows: list[Any] = await compute.run(price_history_index.rows_of, products, prices._twins(products))
    await conn.executemany("DELETE FROM price_history_products WHERE store=? AND product_id=?", [(store, pid) for pid in ids])
    await conn.executemany(price_history_index._UPSERT_SQL, [(store, *row) for row in rows])
    # A product that is gone keeps no key: a full build would not write one either.
    await conn.executemany("DELETE FROM shared_price_keys WHERE store=? AND product_id=?", [(store, pid) for pid in ids])
    await conn.executemany(
        "INSERT OR REPLACE INTO shared_price_keys VALUES(?,?,?,?)",
        [(store, pid, known[prices._SAME], _family_key(store, known)) for pid, known in products.items()],
    )
    families: list[tuple[str, str, str]] = await compute.run(_family_memberships, store, products)
    await conn.executemany("DELETE FROM shared_price_families WHERE store=? AND product_id=?", [(store, pid) for pid in ids])
    await conn.executemany("INSERT OR IGNORE INTO shared_price_families VALUES(?,?,?)", families)

    from . import shared_price_search

    await shared_price_search.update_groups(conn, store, sorted(set(old_keys) | {known[prices._SAME] for known in products.values()}))


async def history(
    stores: list[str],
    wanted: Any,
    limit: int,
    today: str,
    bought: bool,
    only: str | None,
    pinned: str | None,
    by_brand: Any,
    by_category: Any,
    one_store: bool,
) -> dict[str, Any]:
    from . import offers, prices, shared_prices

    with ExitStack() as leases:
        snapshots: dict[str, Any] = {store: leases.enter_context(shared_prices.lease(store)) for store in stores}
        async with prices._connect() as conn:
            from . import shared_price_dirty

            for store in stores:
                await shared_price_dirty.refresh(conn, snapshots[store], store, today)
            receipts: dict[str, list[dict[str, Any]]] = (
                {store: await receipt_rows(conn, snapshots[store], store, today) for store in stores} if bought else {}
            )
            await install(conn, snapshots.get("ah"), stores, today, receipts)
            keeps: dict[str, set[str] | None] = {
                store: await prices._bought(conn, store, today, receipts[store]) if bought else None for store in stores
            }
            for store, keep in keeps.items():
                if keep:
                    rows: list[Any] = await price_history_index.rows_for(conn, store, [], sorted(keep), False)
                    _, twins = await compute.run(price_history_index.known_of, rows)
                    keeps[store] = prices._with_twins(keep, twins)
            ask: Any = prices._HistoryAsk(stores, one_store, wanted, only, pinned, keeps, by_brand, by_category, limit, today)
            if only is None:
                ranked, pinned_known = await (prices._indexed_page(conn, ask) if ask.indexed else prices._indexed_search(conn, ask))
            else:
                # A direct observation can precede the next background index batch.
                small: dict[str, Any] = {
                    store: await catalogue(conn, snapshots[store], store, today, [only], include_lines=False, receipts=receipts.get(store))
                    for store in stores
                }
                candidates: dict[tuple[str, str], str] = await compute.run(prices._history_candidates, small, wanted, only, pinned, keeps)
                ranked = await compute.run(prices._rank_history, small, candidates, pinned, only, by_brand, by_category, limit)
                pinned_known = {}
            chosen: list[tuple[str, str]] = ranked.chosen
            catalogues: dict[str, Any] = {}
            found: dict[str, Any] = {}
            lines: dict[str, Any] = {}
            upcoming: dict[str, Any] = {}
            for store in stores:
                ids: list[str] = [pid for name, pid in chosen if name == store]
                rows = await price_history_index.rows_for(conn, store, [], ids, False)
                _, twins = await compute.run(price_history_index.known_of, rows)
                ids = sorted(prices._with_twins(set(ids), twins))
                observed: dict[str, list[Any]] = await shared_price_overlay.observations(conn, snapshots[store], store, ids)
                selected: Any = await catalogue(conn, snapshots[store], store, today, ids, observed, receipts=receipts.get(store))
                catalogues[store] = selected._replace(twins={**selected.twins, **twins})
                found[store] = (
                    (observed, await prices._purchases(conn, store, ids, today, selected.receipts), await prices._saved_images(conn, store, ids))
                    if ids
                    else ({}, {}, {})
                )
                lines[store] = await prices._line_offers(store, selected, ids) if ids else {}
                upcoming[store] = await prices._upcoming(conn, store, ids, today)
            unsynced: dict[str, int] = {store: await prices._unsynced(conn, store) for store in stores}
        offered: dict[str, Any] = {store: await offers.history_of(store, [pid for name, pid in chosen if name == store]) for store in stores}
        sources: Any = prices._ProductSources(catalogues, offered, found, upcoming, pinned_known, lines)
        products: list[dict[str, Any]] = await shared_price_overlay.offload(prices._page_products, chosen, sources, pinned, today)
        return {"products": products, "total": ranked.total, "unsynced": unsynced, "facets": ranked.facets}


def _family_key(store: str, known: Any) -> str:
    from . import prices

    brand: str = prices._fold_name(known[prices._BRAND])
    name: str = known[prices._NAMES][0] if known[prices._NAMES] else ""
    if brand and name.endswith(" " + brand):
        name = name[: -len(brand) - 1]
    return prices.base_title(name)


def _family_memberships(store: str, products: dict[str, Any]) -> list[tuple[str, str, str]]:
    from . import prices

    found: set[tuple[str, str, str]] = set()
    for pid, known in products.items():
        suffixes: set[str] = {store, prices._fold_name(known[prices._BRAND])} - {""}
        for name in known[prices._NAMES]:
            names: list[str] = [name] + [name[: -len(suffix) - 1] for suffix in suffixes if name.endswith(" " + suffix)]
            found.update((store, key, pid) for candidate in names if (key := prices.base_title(candidate)))
    return sorted(found)


async def signature(store: str, today: str) -> str:
    from . import prices, shared_prices

    async with prices._connect() as conn:
        revision: str = await shared_price_overlay.revision(conn, store)
    pinned: tuple[str, Any] | None = _PINNED.get()
    generation: str | None = getattr(pinned[1], "generation", None) if pinned and pinned[0] == store else shared_prices.generation(store)
    return f"prepared-v3:{generation or 'private'}:{revision}:{today}"


async def verdict_facts(store: str, items: list[dict[str, Any]], today: str) -> Any:
    from . import prices

    ids: list[str] = sorted({str(item.get("id")) for item in items})
    with response_lease(store) as snapshot:
        async with prices._connect() as conn:
            from . import shared_price_dirty

            await shared_price_dirty.refresh(conn, snapshot, store, today)
            keys: list[str] = await compute.run(lambda values: sorted({prices.base_title(item.get("title")) for item in values}), items)
            family: dict[str, list[dict[str, Any]]] = {}
            if snapshot is not None:
                for start in range(0, len(keys), 32):
                    family.update(await shared_price_overlay.offload(snapshot.family_products, keys[start : start + 32]))
            family_ids: set[str] = {str(product["product_id"]) for products in family.values() for product in products}
            family_ids.update(
                row[0]
                for row in await conn.execute_fetchall(
                    "SELECT DISTINCT product_id FROM shared_price_families WHERE store=? AND family_key IN (SELECT value FROM json_each(?))",
                    (store, json.dumps(keys)),
                )
            )
            all_ids: list[str] = sorted(set(ids) | family_ids)
            observed: dict[str, list[Any]] = await shared_price_overlay.candidates(
                conn, snapshot, store, all_ids, prices._days_ago(today, prices.REGULAR_HISTORY_DAYS), today
            )
            selected: Any = await catalogue(conn, snapshot, store, today, all_ids, observed, False)
            historical_metadata: dict[str, Any] = await compute.run(_family_metadata, store, family)
            private_metadata: list[Any] = list(
                await conn.execute_fetchall(
                    "SELECT p.* FROM json_each(?) w CROSS JOIN main.price_history_products p ON p.store=? AND p.product_id=w.value",
                    (json.dumps(all_ids), store),
                )
            )
            private_known: dict[str, Any]
            private_known, _ = await compute.run(price_history_index.known_of, private_metadata)
            selected = selected._replace(products={**private_known, **historical_metadata, **selected.products})
            from . import shared_price_lines

            fallback: dict[str, Any] = await compute.run(prices._fallback_known, items)
            folders: list[Any] = await shared_price_lines.candidates(conn, store, {**fallback, **selected.products})
            selected = selected._replace(lines=await compute.run(prices._line_index, folders))
            rows: Any = await prices._regular_rows(
                conn, store, prices._days_ago(today, prices.REGULAR_HISTORY_DAYS), today, purchases_before_until=False, ids=ids, choosable=True
            )
            searched: list[tuple[str, str, float]] = await compute.run(_searched, observed, set(ids))
            rows = rows._replace(searched=searched)
            regulars: dict[str, Any] = await compute.run(
                prices._regular_from_rows, rows, selected.receipts, prices._days_ago(today, prices.REGULAR_HISTORY_DAYS), today, False, ids
            )
            ordering: dict[str, tuple[Any, ...]] = await pack_order(conn, snapshot, store, all_ids, prices._days_ago(today, prices.PACK_DAYS), today)
            recent: list[Any] = await compute.run(_recent, observed, prices._days_ago(today, prices.PACK_DAYS), today, ordering)
            episodes: list[Any] = list(
                await conn.execute_fetchall(
                    "SELECT * FROM offer_history WHERE store=? AND COALESCE(valid_until,substr(last_seen,1,10))>=? "
                    "AND product_id IN (SELECT value FROM json_each(?)) ORDER BY product_id,first_seen,id",
                    (store, prices._days_ago(today, prices.YEAR_DAYS), json.dumps(ids)),
                )
            )
            selected, points, bought = await prices._ladder(conn, store, today, selected, ids)
        lines: dict[str, list[dict[str, Any]]] = await prices._verdict_lines(store, selected, items, ids)
        return prices._VerdictFacts(selected, regulars, recent, episodes, points, bought, lines)


def _searched(observed: dict[str, list[Any]], ids: set[str]) -> list[tuple[str, str, float]]:
    from . import prices

    return [
        (pid, row["day"], row["price"])
        for pid, rows in observed.items()
        if pid in ids
        for row in rows
        if row["source"] != prices.PUBLIC_OBSERVED_UNKNOWN
    ]


def _recent(observed: dict[str, list[Any]], since: str, until: str, ordering: dict[str, tuple[Any, ...]]) -> list[Any]:
    from . import prices

    return sorted(
        (row for rows in observed.values() for row in rows if since <= row["day"] <= until and row["source"] != prices.PUBLIC_OBSERVED_UNKNOWN),
        key=lambda row: (ordering.get(row["product_id"], ("", 1, row["product_id"])), row["day"]),
    )


async def annotate(store: str, items: list[dict[str, Any]], today: str) -> None:
    from . import prices

    with response_lease(store):
        facts: Any = await verdict_facts(store, items, today)
        await shared_price_overlay.offload(prices._judge, store, items, today, facts)


async def attach(conn: aiosqlite.Connection, snapshot: Any) -> None:
    present: set[str] = {row[1] for row in await conn.execute_fetchall("PRAGMA database_list")}
    if "shared_public" not in present:
        await conn.execute("ATTACH DATABASE ? AS shared_public", (f"{snapshot.path.as_uri()}?mode=ro&immutable=1",))
        await conn.execute("PRAGMA shared_public.trusted_schema=OFF")
        await conn.execute("PRAGMA shared_public.cache_size=-2048")


async def public_candidates(
    conn: aiosqlite.Connection, snapshot: Any, store: str, ids: list[str], since: str, until: str
) -> dict[str, list[dict[str, Any]]]:
    await attach(conn, snapshot)
    rows: list[Any] = list(
        await conn.execute_fetchall(
            """
    WITH RECURSIVE candidates(product_id,source,first_day,step_days,version_id,values_json,day) AS (
      SELECT product_id,source,first_day,step_days,version_id,values_json,
       date(first_day,printf('+%d days',CAST((julianday(min(last_day,?))-julianday(first_day))/step_days AS INTEGER)*step_days))
      FROM shared_public.price_periods WHERE store=? AND product_id IN(SELECT value FROM json_each(?))
       AND first_day<=? AND last_day>=?
      UNION ALL
      SELECT c.product_id,c.source,c.first_day,c.step_days,c.version_id,c.values_json,date(c.day,printf('-%d days',c.step_days))
      FROM candidates c WHERE c.day>=max(c.first_day,?) AND EXISTS(
       SELECT 1 FROM main.price_observations p WHERE p.store=? AND p.product_id=c.product_id AND p.day=c.day
       AND NOT EXISTS(SELECT 1 FROM main.shared_price_equivalence e WHERE e.store=p.store AND e.product_id=p.product_id AND e.day=p.day))
    )
    SELECT c.product_id,c.day,c.source,c.values_json,v.metadata_json FROM candidates c
    JOIN shared_public.product_versions v ON v.version_id=c.version_id
    WHERE c.day>=max(c.first_day,?) AND NOT EXISTS(
      SELECT 1 FROM main.price_observations p WHERE p.store=? AND p.product_id=c.product_id AND p.day=c.day
      AND NOT EXISTS(SELECT 1 FROM main.shared_price_equivalence e WHERE e.store=p.store AND e.product_id=p.product_id AND e.day=p.day))
    """,
            (until, store, json.dumps(ids), until, since, since, store, since, store),
        )
    )
    return await compute.run(_decode_candidates, rows)


def _decode_candidates(rows: list[Any]) -> dict[str, list[dict[str, Any]]]:
    found: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        value: dict[str, Any] = {
            **json.loads(row["metadata_json"]),
            **json.loads(row["values_json"]),
            "product_id": row["product_id"],
            "day": row["day"],
            "source": row["source"],
        }
        for key in ("brand", "unit_size", "image", "url", "seen_at"):
            value.setdefault(key, None)
        found.setdefault(row["product_id"], []).append(value)
    return shared_price_overlay.normalise_public(found)


async def relink_receipts(conn: aiosqlite.Connection, snapshot: Any, store: str, today: str, generation: str, token: str, *, mark: bool) -> None:
    """Link every receipt line of `store` again and store the lines with what the linking read (`ReceiptState`), inside the
    caller's transaction. With `mark` the products whose lines changed are marked for the drain (`shared_price_dirty`)."""
    from . import prices, shared_price_dirty

    raw: list[Any] = await prices._receipt_source_rows(conn, store, today)
    receipts, state = await _link_with_state(conn, snapshot, store, raw)
    documents: dict[str, str] = await compute.run(_receipt_docs, receipts)
    old: dict[str, str] = {
        str(row[0]): str(row[1]) for row in await conn.execute_fetchall("SELECT product_id,doc FROM shared_price_receipts WHERE store=?", (store,))
    }
    changed: list[str] = await compute.run(_changed_docs, old, documents)
    await conn.executemany("DELETE FROM shared_price_receipts WHERE store=? AND product_id=?", [(store, pid) for pid in changed])
    await conn.executemany("INSERT INTO shared_price_receipts VALUES(?,?,?)", [(store, pid, documents[pid]) for pid in changed if pid in documents])
    for table in ("shared_price_receipt_keys", "shared_price_receipt_names", "shared_price_receipt_prices"):
        await conn.execute(f"DELETE FROM {table} WHERE store=?", (store,))
    await conn.executemany("INSERT OR IGNORE INTO shared_price_receipt_keys VALUES(?,?,?)", [(store, kind, value) for kind, value in state.keys])
    await conn.executemany("INSERT OR IGNORE INTO shared_price_receipt_names VALUES(?,?,?,?)", [(store, *name) for name in state.names])
    await conn.executemany("INSERT INTO shared_price_receipt_prices VALUES(?,?,?)", [(store, pid, known) for pid, known in state.prices])
    await conn.execute(
        "INSERT OR REPLACE INTO shared_price_receipt_meta VALUES(?,?)", (store, shared_price_dirty.receipt_revision(token, generation, today))
    )
    if mark:
        await shared_price_dirty.mark_products(conn, store, changed)


def _changed_docs(old: dict[str, str], new: dict[str, str]) -> list[str]:
    return sorted(pid for pid in set(old) | set(new) if old.get(pid) != new.get(pid))


def _receipt_docs(rows: list[dict[str, Any]]) -> dict[str, str]:
    found: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        found.setdefault(str(row["product_id"]), []).append(row)
    return {pid: json.dumps(lines, ensure_ascii=False, separators=(",", ":")) for pid, lines in found.items()}


async def _stored_receipts(conn: aiosqlite.Connection, store: str, today: str, ids: list[str] | None = None) -> list[dict[str, Any]]:
    wanted: str = " AND product_id IN(SELECT value FROM json_each(?))" if ids is not None else ""
    args: tuple[Any, ...] = (store, json.dumps(ids)) if ids is not None else (store,)
    rows: list[Any] = list(await conn.execute_fetchall("SELECT doc FROM shared_price_receipts WHERE store=?" + wanted, args))
    return await compute.run(_decode_receipts, rows, today)


def _decode_receipts(rows: list[Any], today: str) -> list[dict[str, Any]]:
    return [line for row in rows for line in json.loads(row[0]) if line["day"] <= today]


def _receipt_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(row.get(key) for key in ("source", "ref", "receipt_product_id", "day", "quantity", "paid")) + (
        row.get("receipt_name") or row.get("title"),
    )


async def receipt_rows(conn: aiosqlite.Connection, snapshot: Any, store: str, today: str, ids: list[str] | None = None) -> list[dict[str, Any]]:
    from . import prices, shared_price_dirty

    if await shared_price_dirty.receipts_ready(conn, store, getattr(snapshot, "generation", "") if snapshot is not None else "", today):
        return await _stored_receipts(conn, store, today, ids)
    stored: list[dict[str, Any]] = []
    raw: list[Any] = await prices._receipt_source_rows(conn, store, today)
    missing, kept = await compute.run(_missing_receipts, raw, stored)
    linked: list[dict[str, Any]] = kept + (await link_receipts(conn, snapshot, store, missing) if missing else [])
    return await compute.run(_wanted_receipts, linked, set(ids)) if ids is not None else linked


class ReceiptState(NamedTuple):
    """What a linking of every receipt line read, for `shared_price_dirty` to prove that a change leaves each line where it
    was: the keys that select the names (`id`, `fold`, `near`) and the links (`article`, `name`, `shared`), the products of a
    name with more than one product (`several`), the selected names per source and the local prices of those products."""

    keys: list[tuple[str, str]]
    names: list[tuple[int, str, str]]
    prices: list[tuple[str, str]]


async def receipt_name_sources(conn: aiosqlite.Connection, snapshot: Any, store: str) -> list[tuple[str, tuple[Any, ...]]]:
    """The sources of product names for linking receipt lines (`prices._name_sources`), without the shadowed public rows."""
    from . import prices

    sources: list[tuple[str, tuple[Any, ...]]] = prices._name_sources(await prices._tables(conn), store)
    if snapshot is None:
        return sources
    await attach(conn, snapshot)
    return [
        (
            sql.replace(
                "FROM price_observations WHERE store = ?",
                "FROM price_observations WHERE store = ? AND NOT (" + shared_price_overlay.shadow_sql("price_observations") + ")",
            ),
            args,
        )
        for sql, args in sources
    ]


async def link_receipts(conn: aiosqlite.Connection, snapshot: Any, store: str, raw: list[Any]) -> list[dict[str, Any]]:
    return (await _link_with_state(conn, snapshot, store, raw))[0]


async def _link_with_state(conn: aiosqlite.Connection, snapshot: Any, store: str, raw: list[Any]) -> tuple[list[dict[str, Any]], ReceiptState]:
    from . import name_keys, prices, product_links, shared_links

    if not raw:
        return [], ReceiptState([], [], [])
    ids, folded, near = await compute.run(_receipt_candidates, raw, store)
    await conn.create_function("ms_receipt_near", 1, name_keys.key, deterministic=True)
    sources: list[tuple[str, tuple[Any, ...]]] = await receipt_name_sources(conn, snapshot, store)
    names: list[tuple[Any, ...]] = []
    by_source: list[list[tuple[Any, ...]]] = []
    params: tuple[str, str, str] = (json.dumps(ids), json.dumps(folded), json.dumps(near))
    for sql, args in sources:
        found: list[tuple[Any, ...]] = await prices._plain_rows(
            conn,
            "WITH names AS MATERIALIZED (SELECT product_id,title FROM (" + sql + ") GROUP BY product_id,title) "
            "SELECT product_id,title FROM names WHERE product_id IN(SELECT value FROM json_each(?)) "
            "OR ms_fold(title) IN(SELECT value FROM json_each(?)) OR ms_receipt_near(title) IN(SELECT value FROM json_each(?))",
            (*args, *params),
        )
        names += found
        by_source.append(found)
    public_prices: list[Any] = []
    if snapshot is not None:
        await attach(conn, snapshot)
        columns: set[str] = {row[1] for row in await conn.execute_fetchall("PRAGMA shared_public.table_info(search_names)")}
        fold_column: str = "folded_name" if "folded_name" in columns else "ms_fold(name)"
        near_column: str = "near_name" if "near_name" in columns else "ms_receipt_near(name)"
        public: list[Any] = await prices._plain_rows(
            conn,
            f"SELECT product_id,name FROM shared_public.search_names WHERE store=? AND "
            f"(product_id IN(SELECT value FROM json_each(?)) OR {fold_column} IN(SELECT value FROM json_each(?)) "
            f"OR {near_column} IN(SELECT value FROM json_each(?)))",
            (store, *params),
        )
        names += public
    index, several = await compute.run(prices._name_groups, names)
    ambiguous: list[str] = sorted(set().union(*several.values())) if several else []
    if snapshot is not None and ambiguous:
        public_prices = await prices._plain_rows(
            conn,
            "SELECT DISTINCT product_id,price FROM shared_public.price_periods WHERE store=? "
            "AND product_id IN(SELECT value FROM json_each(?)) AND price>0",
            (store, json.dumps(ambiguous)),
        )
    known: dict[str, set[float]] = await prices._known_prices(conn, sources, ambiguous) if several else {}
    state: ReceiptState = await compute.run(_receipt_state, raw, store, ids, folded, near, by_source, ambiguous, known)
    for pid, price in public_prices:
        known.setdefault(str(pid), set()).add(float(price))
    shared: dict[str, Any] = await compute.run(prices._name_prices, several, known) if several else {}
    tables: set[str] = await prices._tables(conn)
    article: list[Any] = await prices._receipt_product_rows(conn) if store == "ah" and "ah_receipt_products" in tables else []
    links: Any = prices._LinkRows(
        prices._Names(index, shared),
        article,
        await product_links.load(conn, store),
        shared_links.active(store, await shared_links.rejected(conn, store)),
    )
    return await compute.run(prices._linked_receipts, store, raw, links), state


def _receipt_state(
    raw: list[Any],
    store: str,
    ids: list[str],
    folded: list[str],
    near: list[str],
    by_source: list[list[tuple[Any, ...]]],
    ambiguous: list[str],
    known: dict[str, set[float]],
) -> ReceiptState:
    from . import name_keys, prices, product_links

    at: dict[str, int] = {column: prices._SOURCE_COLUMNS.index(column) for column in ("receipt_product_id", "title", "source")}
    keys: set[tuple[str, str]] = {("id", value) for value in ids} | {("fold", value) for value in folded} | {("near", value) for value in near}
    for row in raw:
        title: Any = row[at["title"]]
        keys.add(("name", product_links.fold(title)))
        if row[at["source"]] == "receipt" and (article := product_links.article_key(row[at["receipt_product_id"]])):
            keys.add(("article", article))
        if row[at["source"]] == "import":
            keys.add(("shared", name_keys.key(title)))
    keys.update(("several", pid) for pid in ambiguous)
    names: set[tuple[int, str, str]] = {(index, str(pid), str(title)) for index, rows in enumerate(by_source) for pid, title in rows if pid and title}
    return ReceiptState(sorted(keys), sorted(names), [(pid, json.dumps(sorted(known.get(pid, ())))) for pid in ambiguous])


def _receipt_candidates(raw: list[Any], store: str) -> tuple[list[str], list[str], list[str]]:
    from . import name_keys, prices

    ids: list[str] = sorted(
        {str(row[prices._SOURCE_COLUMNS.index("receipt_product_id")]) for row in raw if row[prices._SOURCE_COLUMNS.index("receipt_product_id")]}
    )
    folded: set[str] = set()
    for row in raw:
        name: str = prices._fold_name(row[prices._SOURCE_COLUMNS.index("title")])
        folded.add(name)
        if store == "ah":
            folded.add(name[3:] if name.startswith("ah ") else f"ah {name}")
    return ids, sorted(folded), sorted({name_keys.key(name) for name in folded})


def _missing_receipts(raw: list[Any], stored: list[dict[str, Any]]) -> tuple[list[Any], list[dict[str, Any]]]:
    from collections import defaultdict, deque

    from . import prices

    remaining: dict[tuple[Any, ...], Any] = defaultdict(deque)
    for index, row in enumerate(raw):
        remaining[_receipt_key(dict(zip(prices._SOURCE_COLUMNS, row, strict=True)))].append((index, row))
    kept: list[dict[str, Any]] = []
    for row in stored:
        key: tuple[Any, ...] = _receipt_key(row)
        if remaining[key]:
            remaining[key].popleft()
            kept.append(row)
    return [row for _, row in sorted(item for rows in remaining.values() for item in rows)], kept


async def private_metadata(conn: aiosqlite.Connection, snapshot: Any, store: str, ids: list[str]) -> dict[str, list[Any]]:
    if snapshot is not None:
        await attach(conn, snapshot)
    shadow: str = " AND NOT (" + shared_price_overlay.shadow_sql("p") + ")" if snapshot is not None else ""
    rows: list[Any] = list(
        await conn.execute_fetchall(
            "SELECT product_id,title,brand,unit_size,MAX(day) AS day FROM price_observations p "
            "WHERE store=? AND product_id IN(SELECT value FROM json_each(?))" + shadow + " GROUP BY product_id,title,brand,unit_size",
            (store, json.dumps(ids)),
        )
    )
    return await compute.run(_metadata_by_product, rows)


def _metadata_by_product(rows: list[Any]) -> dict[str, list[Any]]:
    found: dict[str, list[Any]] = {}
    for row in rows:
        found.setdefault(row["product_id"], []).append(row)
    return found


def _family_metadata(store: str, families: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    from . import offer_categories, prices

    known: dict[str, Any] = {}
    for members in families.values():
        for row in members:
            pid: str = str(row["product_id"])
            title: str = str(row.get("title") or "")
            brand: str = str(row.get("brand") or "")
            size: tuple[float, str] | None = prices._size_of(row.get("unit_size"), title)
            sizes: list[tuple[float, str]] = [size] if size else []
            known[pid] = prices._known(
                str(row.get("last_day") or ""),
                [prices._fold_name(f"{title} {brand}")],
                sizes,
                brand,
                offer_categories.offer_category(store, title),
                prices._same_key(pid, title, brand, sizes),
            )
    return known


async def pack_order(conn: aiosqlite.Connection, snapshot: Any, store: str, ids: list[str], since: str, until: str) -> dict[str, tuple[Any, ...]]:
    from . import prices

    selected: str = json.dumps(ids)
    rows: list[Any] = list(
        await conn.execute_fetchall(
            """
    SELECT w.value,p.day,p.rowid FROM json_each(?) w CROSS JOIN price_observations p ON p.rowid=(
      SELECT o.rowid FROM price_observations o WHERE o.store=? AND o.product_id=w.value AND o.day>=? AND o.day<=?
       AND o.source!=? ORDER BY o.day LIMIT 1)
    """,
            (selected, store, since, until, prices.PUBLIC_OBSERVED_UNKNOWN),
        )
    )
    order: dict[str, tuple[Any, ...]] = {row[0]: (row[1], 0, int(row[2])) for row in rows}
    if snapshot is not None:
        public: list[Any] = list(
            await conn.execute_fetchall(
                """
        SELECT product_id,min(date(first_day,printf('+%d days',max(0,CAST((julianday(?)-julianday(first_day)+step_days-1)/step_days AS INTEGER))*step_days))) AS day
        FROM shared_public.price_periods WHERE store=? AND product_id IN(SELECT value FROM json_each(?)) AND last_day>=? AND first_day<=? GROUP BY product_id
        """,
                (since, store, selected, since, until),
            )
        )
        for row in public:
            order.setdefault(row[0], (row[1], 1, row[0]))
    return order
