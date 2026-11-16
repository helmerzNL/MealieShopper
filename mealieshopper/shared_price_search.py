"""A small SQL overlay over the already prepared private search rows."""

from __future__ import annotations

import json
from typing import Any

import aiosqlite


async def update_groups(conn: aiosqlite.Connection, store: str, keys: list[str]) -> None:
    keys = [key for key in keys if key]
    if not keys:
        return
    await conn.execute(
        """
    WITH members AS MATERIALIZED (
      SELECT p.product_id,p.last,k.same_key FROM shared_price_keys k INDEXED BY shared_price_keys_same
      CROSS JOIN price_history_products p ON p.store=k.store AND p.product_id=k.product_id
      WHERE k.store=? AND k.same_key IN(SELECT value FROM json_each(?)) AND k.same_key!=''
    ), ranked AS (
      SELECT product_id,same_key,first_value(product_id) OVER(PARTITION BY same_key ORDER BY last DESC,product_id DESC) AS lead,
        max(last) OVER(PARTITION BY same_key) AS day FROM members
    ), groups AS MATERIALIZED (
      SELECT same_key,json_group_array(product_id) AS twins,count(*) AS members
      FROM (SELECT * FROM members ORDER BY product_id) GROUP BY same_key
    ), changes AS MATERIALIZED (
      SELECT r.product_id,r.lead,r.day,CASE WHEN g.members>1 THEN g.twins END AS twins
      FROM ranked r JOIN groups g USING(same_key)
    )
    UPDATE price_history_products AS p SET lead=(SELECT lead FROM changes c WHERE c.product_id=p.product_id),
      day=(SELECT day FROM changes c WHERE c.product_id=p.product_id),twins=(SELECT twins FROM changes c WHERE c.product_id=p.product_id)
    WHERE p.store=? AND p.product_id IN(SELECT product_id FROM changes)
    """,
        (store, json.dumps(keys), store),
    )


def merged_key(base: str, sizes: str) -> str:
    if not base:
        return ""
    return base.rsplit("|", 1)[0] + "|" + str(sorted(tuple(size) for size in json.loads(sizes)))


async def install(conn: aiosqlite.Connection, snapshot: Any) -> None:
    await conn.create_function("ms_history_merged_key", 2, merged_key, deterministic=True)
    if snapshot is None:
        await conn.execute(
            "CREATE TEMP VIEW price_history_products AS SELECT p.* FROM main.price_history_products p "
            "JOIN _private_history_context c ON c.store=p.store"
        )
        return
    await conn.execute("""
    CREATE TEMP TABLE _shared_history_rows AS
    WITH public AS MATERIALIZED (
      SELECT p.store,p.product_id,p.last_day AS last,
       COALESCE((SELECT json_group_array(search) FROM shared_public.search_names n WHERE n.store=p.store AND n.product_id=p.product_id),'[]') AS names,
       COALESCE((SELECT json_group_array(json_array(amount,unit)) FROM (SELECT DISTINCT amount,unit FROM shared_public.search_names n
                 WHERE n.store=p.store AND n.product_id=p.product_id AND amount IS NOT NULL)),'[]') AS sizes,
       COALESCE(p.brand,'') AS brand,ms_history_category(p.store,p.title) AS category,
       COALESCE((SELECT group_concat(search || char(10) || replace(search,' ',''),char(10)) FROM shared_public.search_names n
                 WHERE n.store=p.store AND n.product_id=p.product_id),'') AS search,p.twin_key AS same_key
      FROM shared_public.products p
    ), merged AS (
      SELECT p.store,p.product_id,max(p.last,COALESCE(l.last,'')) AS last,
       CASE WHEN l.product_id IS NULL THEN p.names ELSE
        (SELECT json_group_array(value) FROM (SELECT value FROM json_each(p.names) UNION SELECT value FROM json_each(l.names))) END AS names,
       CASE WHEN l.product_id IS NULL THEN p.sizes ELSE
        (SELECT json_group_array(json(value)) FROM (SELECT value FROM json_each(p.sizes) UNION SELECT value FROM json_each(l.sizes))) END AS sizes,
       COALESCE(NULLIF(l.brand,''),p.brand) AS brand,COALESCE(NULLIF(l.category,''),p.category) AS category,
       p.search || char(10) || COALESCE(l.search,'') AS search,COALESCE(NULLIF(k.same_key,''),p.same_key) AS base
      FROM public p LEFT JOIN main.price_history_products l USING(store,product_id) LEFT JOIN main.shared_price_keys k USING(store,product_id)
    ) SELECT store,product_id,last,names,sizes,brand,category,search,
      COALESCE(NULLIF(ms_history_merged_key(base,sizes),''),'id:' || store || ':' || product_id) AS same_key FROM merged
    """)
    await conn.execute("CREATE UNIQUE INDEX _shared_history_rows_id ON _shared_history_rows(store,product_id)")
    await conn.execute("CREATE TEMP TABLE _shared_history_keys(store TEXT,same_key TEXT,PRIMARY KEY(store,same_key)) WITHOUT ROWID")
    await conn.execute("INSERT OR IGNORE INTO _shared_history_keys SELECT store,same_key FROM _shared_history_rows WHERE same_key!=''")
    await conn.execute("INSERT OR IGNORE INTO _shared_history_keys SELECT store,twin_key FROM shared_public.products WHERE twin_key!=''")
    await conn.execute(
        "INSERT OR IGNORE INTO _shared_history_keys SELECT k.store,k.same_key FROM shared_public.products p "
        "CROSS JOIN main.shared_price_keys k ON k.store=p.store AND k.product_id=p.product_id WHERE k.same_key!=''"
    )
    await conn.execute("""
    INSERT OR IGNORE INTO _shared_history_rows
    SELECT p.store,p.product_id,p.last,p.names,p.sizes,p.brand,p.category,p.search,k.same_key
    FROM _shared_history_keys w CROSS JOIN main.shared_price_keys k INDEXED BY shared_price_keys_same
      ON k.store=w.store AND k.same_key=w.same_key
    CROSS JOIN main.price_history_products p ON p.store=k.store AND p.product_id=k.product_id
    """)
    await conn.execute("""
    CREATE TEMP TABLE _shared_history_grouped AS
    WITH ranked AS (
      SELECT *,first_value(product_id) OVER(PARTITION BY store,same_key ORDER BY last DESC,product_id DESC) AS lead,
       max(last) OVER(PARTITION BY store,same_key) AS day,count(*) OVER(PARTITION BY store,same_key) AS members FROM _shared_history_rows
    ) SELECT store,product_id,last,names,sizes,brand,category,search,lead,
      CASE WHEN members>1 THEN (SELECT json_group_array(product_id) FROM
       (SELECT product_id FROM _shared_history_rows c WHERE c.store=g.store AND c.same_key=g.same_key ORDER BY product_id)) END AS twins,day
    FROM ranked g
    """)
    await conn.execute("CREATE INDEX _shared_history_grouped_id ON _shared_history_grouped(store,product_id)")
    await conn.execute(
        "CREATE TEMP VIEW price_history_products AS SELECT p.* FROM main.price_history_products p "
        "JOIN _private_history_context c ON c.store=p.store WHERE NOT EXISTS(SELECT 1 FROM _shared_history_rows r WHERE r.store=p.store AND r.product_id=p.product_id) "
        "UNION ALL SELECT * FROM _shared_history_grouped"
    )


async def page(
    conn: aiosqlite.Connection, stores: list[str], pinned: str | None, keeps: dict[str, set[str] | None], by_brand: Any, by_category: Any, limit: int
) -> Any:
    from . import compute, price_history_index

    private: Any = await price_history_index.page(
        conn, stores, pinned, keeps, by_brand, by_category, limit, table="main.price_history_products", shadowed=True
    )
    public: Any = await price_history_index.page(conn, stores, pinned, keeps, by_brand, by_category, limit, table="temp._shared_history_grouped")
    days: dict[tuple[str, str], str] = {}
    for table, selected in (("main.price_history_products", private.rest), ("temp._shared_history_grouped", public.rest)):
        if selected:
            rows: list[Any] = list(
                await conn.execute_fetchall(
                    f"SELECT p.store,p.product_id,p.day FROM json_each(?) w CROSS JOIN {table} p "
                    "ON p.store=json_extract(w.value,'$[0]') AND p.product_id=json_extract(w.value,'$[1]')",
                    (json.dumps(selected),),
                )
            )
            days.update({(row[0], row[1]): row[2] for row in rows})
    return await compute.run(_page_union, private, public, days, limit)


def _page_union(private: Any, public: Any, days: dict[tuple[str, str], str], limit: int) -> Any:
    from . import price_history_index

    rest: list[tuple[str, str]] = sorted(private.rest + public.rest, key=lambda key: (days[key], *key), reverse=True)[:limit]
    brands: dict[str, int] = dict(private.brands)
    categories: dict[str, int] = dict(private.categories)
    for value, count in public.brands.items():
        brands[value] = brands.get(value, 0) + count
    for value, count in public.categories.items():
        categories[value] = categories.get(value, 0) + count
    return price_history_index.Page(private.pinned + public.pinned, rest, private.total + public.total, brands, categories)
