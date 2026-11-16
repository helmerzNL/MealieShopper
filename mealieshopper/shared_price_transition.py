"""SQL metadata fallback while the private read model is still being prepared."""

from __future__ import annotations

import json
from typing import Any

import aiosqlite

from . import compute, shared_price_overlay


async def install(conn: aiosqlite.Connection, snapshot: Any, stores: list[str], today: str, receipts: dict[str, list[dict[str, Any]]]) -> None:
    from . import prices

    await conn.execute("CREATE TEMP TABLE _private_history_context(store TEXT PRIMARY KEY,today TEXT,ready INTEGER)")
    for store in stores:
        revision: str = await shared_price_overlay.revision(conn, store)
        state: Any = await conn.execute_fetchall("SELECT generation,ready_revision FROM shared_price_build WHERE store=?", (store,))
        generation: str = snapshot.generation if snapshot is not None and store == "ah" else ""
        ready: bool = bool(state and state[0][0] == generation and state[0][1] == revision)
        await conn.execute("INSERT INTO _private_history_context VALUES(?,?,?)", (store, today, int(ready)))
    await conn.execute(
        "CREATE TEMP TABLE _private_receipt_metadata(store TEXT,product_id TEXT,title TEXT,brand TEXT,unit_size TEXT,last TEXT,priority INTEGER)"
    )
    await conn.executemany("INSERT INTO _private_receipt_metadata VALUES(?,?,?,?,?,?,?)", await compute.run(_receipt_metadata, receipts))
    await conn.create_function("ms_history_size", 2, _size, deterministic=True)
    await conn.create_function("ms_history_key", 4, _key, deterministic=True)
    await conn.create_function("ms_history_category", 2, _category, deterministic=True)
    sources: list[str] = [
        "SELECT p.store,p.product_id,p.title,p.brand,p.unit_size,MAX(p.last_seen) AS last,MAX(COALESCE(p.category,'')) AS category,0 AS priority "
        "FROM main.offer_history p JOIN _private_history_context c ON c.store=p.store AND c.ready=0 GROUP BY p.store,p.product_id,p.title,p.brand,p.unit_size",
        "SELECT p.store,p.product_id,p.title,p.brand,p.unit_size,MAX(p.day) AS last,'' AS category,1 AS priority "
        "FROM main.price_observations p JOIN _private_history_context c ON c.store=p.store AND c.ready=0 "
        + ("WHERE NOT (" + shared_price_overlay.shadow_sql("p") + ") " if snapshot is not None else "")
        + "GROUP BY p.store,p.product_id,p.title,p.brand,p.unit_size",
        "SELECT p.store,p.product_id,p.title,p.brand,p.unit_size,MAX(COALESCE(p.valid_from,c.today)) AS last,'' AS category,4 AS priority "
        "FROM main.offers p JOIN _private_history_context c ON c.store=p.store AND c.ready=0 "
        "WHERE p.valid_from>c.today OR (COALESCE(p.valid_until,'9999-12-31')>=c.today AND NOT EXISTS("
        "SELECT 1 FROM main.offer_history h WHERE h.store=p.store AND h.product_id=p.product_id AND h.ended_at IS NULL)) "
        "GROUP BY p.store,p.product_id,p.title,p.brand,p.unit_size",
        "SELECT r.store,r.product_id,r.title,r.brand,r.unit_size,r.last,'' AS category,r.priority "
        "FROM _private_receipt_metadata r JOIN _private_history_context c ON c.store=r.store AND c.ready=0",
    ]
    tables: set[str] = await prices._tables(conn)
    for store in stores:
        for sql in prices._purchase_sources(tables, store):
            sources.append(
                f"SELECT '{store}',p.product_id,p.title,p.brand,p.unit_size,MAX(p.day) AS last,'' AS category,2 AS priority "
                f"FROM ({sql}) p JOIN _private_history_context c ON c.store='{store}' AND c.ready=0 WHERE p.day<=c.today "
                "GROUP BY p.product_id,p.title,p.brand,p.unit_size"
            )
    await conn.execute(
        """
    CREATE TEMP VIEW _private_history_products AS
    WITH metadata AS MATERIALIZED ("""
        + " UNION ALL ".join(sources)
        + """),
    named AS (
      SELECT *,ms_fold(COALESCE(title,'') || ' ' || COALESCE(brand,'')) AS name,CASE WHEN priority=5 THEN NULL ELSE ms_history_size(unit_size,title) END AS size,
       first_value(title) OVER(PARTITION BY store,product_id ORDER BY CASE WHEN COALESCE(trim(title),'')='' THEN 1 ELSE 0 END,priority,title,brand,unit_size) AS kept_title,
       first_value(brand) OVER(PARTITION BY store,product_id ORDER BY CASE WHEN COALESCE(trim(brand),'')='' THEN 1 ELSE 0 END,priority,title,brand,unit_size) AS kept_brand
      FROM metadata WHERE COALESCE(product_id,'')!=''
    ), collected AS (
      SELECT store,product_id,MAX(substr(last,1,10)) AS last,json_group_array(DISTINCT name) AS names,
       json_group_array(DISTINCT json(size)) FILTER(WHERE size IS NOT NULL) AS sizes,
       COALESCE(MAX(kept_title),'') AS title,COALESCE(MAX(kept_brand),'') AS brand,
       MAX(category) AS stored_category,group_concat(DISTINCT name || char(10) || replace(name,' ','')) AS search
      FROM named GROUP BY store,product_id
    )
    SELECT store,product_id,last,names,sizes,brand,
      COALESCE(NULLIF(stored_category,''),ms_history_category(store,title)) AS category,search,
      product_id AS lead,NULL AS twins,last AS day,ms_history_key(product_id,title,brand,sizes) AS same_key
    FROM collected
    UNION ALL
    SELECT p.store,p.product_id,p.last,p.names,p.sizes,p.brand,p.category,p.search,p.lead,p.twins,p.day,COALESCE(k.same_key,'') AS same_key
    FROM main.price_history_products p JOIN _private_history_context c ON c.store=p.store AND c.ready=1
    LEFT JOIN main.shared_price_keys k ON k.store=p.store AND k.product_id=p.product_id
    """
    )


def _receipt_metadata(receipts: dict[str, list[dict[str, Any]]]) -> list[tuple[Any, ...]]:
    rows: list[tuple[Any, ...]] = []
    for store, lines in receipts.items():
        for row in lines:
            rows.append((store, row["product_id"], row["title"], row.get("brand"), row.get("unit_size"), row["day"], 3))
            if row.get("receipt_name"):
                rows.append((store, row["product_id"], row["receipt_name"], None, None, row["day"], 5))
    return rows


def _size(unit: Any, title: Any) -> str | None:
    from . import prices

    size: tuple[float, str] | None = prices._size_of(unit, title)
    return json.dumps(size) if size else None


def _key(pid: str, title: str, brand: str, sizes: str) -> str:
    from . import prices

    return prices._same_key(pid, title, brand, [tuple(size) for size in json.loads(sizes)])


def _category(store: str, title: str) -> str:
    from . import offer_categories

    return offer_categories.offer_category(store, title) if title else ""
