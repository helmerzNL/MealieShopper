"""Het leesmodel van de aanbiedingsgeschiedenis (offer_history_index.py): `offers.history` geeft hetzelfde antwoord als de oude
berekening, na elke schrijver, en de pagina zonder zoekterm is een index.

De oude berekening staat hieronder als referentie (`old_history`, zoals `offers.history` tot 4.26.0 was): een window-functie over
alle rijen van `offer_history` bij elke aanvraag. In de app staat hij niet meer.
"""

from __future__ import annotations

import contextlib
import random
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
import volume
from helpers import db, returned

from mealieshopper import auth, compute, offer_history_index, offers, product_images

OLD_PAGE_SQL: str = """
WITH matched AS (
    SELECT store, product_id, last_seen,
           ROW_NUMBER() OVER (PARTITION BY store, product_id ORDER BY first_seen DESC, id DESC) AS rank,
           SUM(CASE WHEN continued THEN 0 ELSE 1 END) OVER (PARTITION BY store, product_id) AS times
    FROM offer_history WHERE store IN ({marks}) AND title LIKE ? ESCAPE '\\'
)
SELECT store, product_id, COUNT(*) OVER () AS total FROM matched WHERE rank = 1
ORDER BY last_seen DESC, times DESC, store, product_id LIMIT ?
"""
OLD_ROWS_SQL: str = """
SELECT * FROM offer_history WHERE store = ? AND product_id IN ({marks}) AND title LIKE ? ESCAPE '\\'
ORDER BY store, product_id, first_seen, id
"""


async def old_history(store: str | None = None, query: str = "", limit: int = 50) -> dict[str, Any]:
    """`offers.history` zoals tot 4.26.0: de window-functie kiest de pagina uit alle rijen."""
    stores: list[str] = [store] if store else list(offers.STORES)
    pattern: str = "%" + query.strip().replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_") + "%"
    limit = max(1, min(200, limit))
    rows: list[Any] = []
    async with offers._connect() as conn:
        page: list[Any] = list(await conn.execute_fetchall(OLD_PAGE_SQL.format(marks=",".join("?" * len(stores))), (*stores, pattern, limit)))
        wanted: dict[str, list[str]] = {}
        for row in page:
            wanted.setdefault(row["store"], []).append(row["product_id"])
        for name, ids in wanted.items():
            rows += await conn.execute_fetchall(OLD_ROWS_SQL.format(marks=",".join("?" * len(ids))), (name, *ids, pattern))
    products: dict[tuple[str, str], dict[str, Any]] = await compute.run(offers._history_products, rows)
    return {"products": [products[(row["store"], row["product_id"])] for row in page], "total": page[0]["total"] if page else 0}


HISTORY_COLUMNS: str = "store, product_id, title, price, price_before, promo_label, first_seen, last_seen, ended_at, syncs, continued"
INSERT_SQL: str = f"INSERT INTO offer_history ({HISTORY_COLUMNS}) VALUES ({', '.join('?' * (HISTORY_COLUMNS.count(',') + 1))})"
# Titels met wat LIKE bijzonder maakt: hoofdletters (LIKE vouwt alleen ASCII), %, _ en \ als gewone tekens, en een naam die de
# winkel later wijzigt (een product met meer titels).
TITLES: tuple[str, ...] = (
    "Kaas jong",
    "Kaas belegen",
    "KAAS extra belegen",
    "Crème fraîche",
    "CRÈME FRAÎCHE",
    "Melk 50% vet",
    "Melk_halfvol",
    "Brood a\\b",
    "Appels",
    "appelsap",
)
QUERIES: tuple[str, ...] = ("", "kaas", "KAAS", "belegen", "crème", "CRÈME", "50%", "%", "_", "a\\b", "\\", "ap", "e", "bestaat niet", "  kaas  ")


def history_rows(seed: int, products: int) -> list[tuple[Any, ...]]:
    """Rijen voor `offer_history` met veel gelijke standen: weinig verschillende dagen, dus dezelfde `first_seen` (de id beslist),
    dezelfde `last_seen` (dan `times`, dan winkel en product), en een op de vijf producten wisselt van titel."""
    rng: random.Random = random.Random(seed)
    rows: list[tuple[Any, ...]] = []
    for number in range(products):
        store: str = ("ah", "jumbo", "lidl")[number % 3]
        title: str = rng.choice(TITLES)
        for period in range(rng.randint(1, 5)):
            if rng.random() < 0.2:
                title = rng.choice(TITLES)
            first: int = rng.randint(1, 9)
            last: int = first + rng.randint(0, 3)
            ended: str | None = None if period and rng.random() < 0.3 else f"2026-10-{last:02d}T05:00:00+00:00"
            rows.append(
                (
                    store,
                    f"p{number:03d}",
                    title,
                    round(rng.uniform(0.5, 5), 2),
                    None,
                    "",
                    f"2026-10-{first:02d}T05:00:00+00:00",
                    f"2026-10-{last:02d}T05:00:00+00:00",
                    ended,
                    1,
                    int(period > 0 and rng.random() < 0.4),
                )
            )
    rng.shuffle(rows)  # de ids staan dan niet op volgorde van `first_seen`
    return rows


@pytest.fixture
def filled(data_dir: Path) -> Iterator[Path]:
    """Een database met de rijen van `history_rows`, geschreven buiten de app om (zoals een import): de triggers markeren ze."""
    with contextlib.closing(db(offers)) as conn, conn:
        conn.executemany(INSERT_SQL, history_rows(7, 240))
    yield data_dir


async def assert_same(stores: tuple[str | None, ...] = (None, "ah", "jumbo", "lidl", "picnic")) -> None:
    for store in stores:
        for query in QUERIES:
            for limit in (1, 7, 50, 200):
                assert await offers.history(store, query, limit) == await old_history(store, query, limit), (store, query, limit)


def stale(path: Path) -> list[tuple[str, str]]:
    with contextlib.closing(sqlite3.connect(path / "auth.sqlite3")) as conn:
        return sorted(conn.execute("SELECT store, product_id FROM offer_history_stale").fetchall())


async def test_the_page_is_the_same_as_the_old_query(filled: Path) -> None:
    await assert_same()


async def test_a_page_with_a_title_change_counts_only_the_matching_periods(data_dir: Path) -> None:
    # Product 1 heette eerst "Kaas jong" (twee keer in de aanbieding) en daarna "Belegen" (een keer, en later gezien): met "kaas"
    # telt de pagina alleen de periodes met die titel, en geeft ze `lastSeen` van de nieuwste passende periode.
    rows: list[tuple[Any, ...]] = [
        ("ah", "1", "Kaas jong", 1.0, None, "", "2026-10-01T05:00:00+00:00", "2026-10-02T05:00:00+00:00", "x", 1, 0),
        ("ah", "1", "Kaas jong", 1.0, None, "", "2026-10-05T05:00:00+00:00", "2026-10-06T05:00:00+00:00", "x", 1, 0),
        ("ah", "1", "Belegen", 1.0, None, "", "2026-10-09T05:00:00+00:00", "2026-10-20T05:00:00+00:00", None, 1, 0),
        ("ah", "2", "Kaas oud", 1.0, None, "", "2026-10-03T05:00:00+00:00", "2026-10-10T05:00:00+00:00", None, 1, 0),
    ]
    with contextlib.closing(db(offers)) as conn, conn:
        conn.executemany(INSERT_SQL, rows)
    found: dict[str, Any] = await offers.history("ah", "kaas")
    assert [(p["productId"], p["timesOnOffer"], p["lastSeen"]) for p in found["products"]] == [
        ("2", 1, "2026-10-10T05:00:00+00:00"),
        ("1", 2, "2026-10-06T05:00:00+00:00"),
    ]
    assert [p["productId"] for p in (await offers.history("ah"))["products"]] == ["1", "2"], "zonder zoekterm telt de nieuwste rij"
    assert found == await old_history("ah", "kaas")


async def test_writers_outside_the_app_are_seen_by_the_next_page(filled: Path) -> None:
    # Een import, het prijsarchief of een oudere versie na een rollback schrijft zelf in `offer_history`: de triggers markeren,
    # en de leesroute werkt de markeringen bij voor ze antwoordt.
    await offers.history()
    assert stale(filled) == []
    with contextlib.closing(db()) as conn, conn:
        conn.execute("UPDATE offer_history SET last_seen = '2026-10-28T05:00:00+00:00' WHERE id = 5")
        conn.execute("UPDATE offer_history SET title = 'Kaas jong' WHERE id IN (7, 8, 9)")
        conn.execute("UPDATE offer_history SET continued = 1 - continued WHERE id % 11 = 0")
        conn.execute("DELETE FROM offer_history WHERE id % 13 = 0")
        conn.execute("DELETE FROM offer_history WHERE store = 'lidl' AND product_id = 'p002'")  # een product zonder rijen
        # nieuwe producten naast de bestaande, en bij de bestaande nieuwe periodes
        conn.executemany(INSERT_SQL, [(store, f"n{product_id}", *rest) for store, product_id, *rest in history_rows(8, 30)])
        conn.executemany(INSERT_SQL, [row for row in history_rows(10, 30) if row[1] != "p002"])
    assert stale(filled), "de triggers markeerden de producten"
    await assert_same()
    assert stale(filled) == []
    assert "p002" not in {p["productId"] for p in (await offers.history("lidl", "", 200))["products"]}


async def test_a_sync_brings_the_read_model_up_to_date_in_its_own_transaction(data_dir: Path) -> None:
    clock: Iterator[str] = iter(f"2026-10-{day:02d}T05:00:00+00:00" for day in range(1, 29))
    weeks: list[list[dict[str, Any]]] = [
        [{"id": "1", "title": "Kipfilet", "price": 3.0}, {"id": "2", "title": "Kaas jong", "price": 2.0}],
        [{"id": "1", "title": "Kipfilet", "price": 2.5}, {"id": "2", "title": "Kaas belegen", "price": 2.0}, {"id": "3", "title": "Melk"}],
        [{"id": "3", "title": "Melk"}],  # 1 en 2 zijn weg: hun periodes sluiten
        [{"id": "1", "title": "Kipfilet", "price": 3.0}, {"id": "3", "title": "Melk", "price": 1.0}],
    ]
    with (
        mock.patch.object(offers, "_now", side_effect=lambda: next(clock)),
        mock.patch.object(product_images, "save_bought", new=lambda *args: returned(0)),
    ):
        for week in weeks:
            with mock.patch.dict(offers.FETCHERS, {"ah": lambda week=week: returned([dict(item) for item in week])}):
                await offers.sync("ah")
            assert stale(data_dir) == [], "de synchronisatie werkte het leesmodel zelf bij"
            for query in ("", "kaas", "jong", "melk", "kip"):
                assert await offers.history("ah", query) == await old_history("ah", query), query
    assert (await offers.history("ah", "kaas"))["products"][0]["timesOnOffer"] == 1, "een andere titel in dezelfde periode"


async def test_the_triggers_mark_only_what_the_read_model_uses(filled: Path) -> None:
    await offers.history()
    with contextlib.closing(db()) as conn, conn:
        # Een periode sluiten, een prijs repareren, een foto omzetten: niets aan de pagina verandert.
        conn.execute("UPDATE offer_history SET ended_at = last_seen, price = 1.5, image = 'x', url = 'y', syncs = syncs + 1")
    assert stale(filled) == []
    with contextlib.closing(db()) as conn, conn:
        moved: tuple[str, str] = tuple(conn.execute("SELECT store, product_id FROM offer_history WHERE id = 3").fetchone())
        conn.execute("UPDATE offer_history SET store = 'jumbo', product_id = 'nieuw' WHERE id = 3")
    assert stale(filled) == sorted({moved, ("jumbo", "nieuw")}), "het oude en het nieuwe product"


def test_a_marking_never_conflicts_with_the_writer(data_dir: Path) -> None:
    # Een trigger neemt de conflictregel over van de opdracht die hem afvuurt. Met INSERT OR IGNORE in de trigger draaide een
    # tweede rij van hetzelfde product met INSERT OR ROLLBACK hier alles terug.
    with contextlib.closing(db(offers)) as conn, conn:
        for _ in range(2):
            conn.execute(INSERT_SQL.replace("INSERT INTO", "INSERT OR ROLLBACK INTO"), history_rows(1, 1)[0])
        assert conn.execute("SELECT COUNT(*) FROM offer_history").fetchone()[0] == 2


async def test_a_database_from_before_the_read_model_is_built_at_the_start(data_dir: Path) -> None:
    # Een database van 4.25.1 (of een rollback die het leesmodel weghaalde): geen tabellen en geen triggers, wel rijen.
    with contextlib.closing(db(offers)) as conn, conn:
        for name in offer_history_index.TRIGGERS:
            conn.execute(f"DROP TRIGGER {name}")
        for table in offer_history_index.TABLES:
            conn.execute(f"DROP TABLE {table}")
        conn.executemany(INSERT_SQL, history_rows(9, 120))
    auth.forget_runtime_state()  # een nieuwe start van de app
    with mock.patch.object(offer_history_index, "REFRESH", ()):  # alleen de herbouw bij de start mag het doen
        await assert_same((None, "ah"))
    assert stale(data_dir) == []


async def test_rebuild_now_restores_the_read_model_on_a_thread_connection(filled: Path) -> None:
    await offers.history()
    with contextlib.closing(db()) as conn, conn:
        conn.execute("DELETE FROM offer_history_products")
        conn.execute("UPDATE offer_history_titles SET times = 99")
        offer_history_index.rebuild_now(conn)
    with mock.patch.object(offer_history_index, "REFRESH", ()):
        await assert_same((None, "jumbo"))


@pytest.mark.parametrize(("stores", "index"), [(offers.STORES, "offer_history_products_recent"), (("ah",), "offer_history_products_store")])
def test_the_page_without_a_query_is_a_walk_over_an_index(data_dir: Path, stores: tuple[str, ...], index: str) -> None:
    sql: str = offer_history_index.PAGE_SQL.format(plus="+" if len(stores) > 1 else "", marks=",".join("?" * len(stores)))
    with contextlib.closing(db(offers)) as conn:
        plan: str = "\n".join(row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + sql, (*stores, 50)))
    assert f"COVERING INDEX {index}" in plan, plan
    assert "TEMP B-TREE" not in plan, "geen sortering: de index heeft de volgorde van de pagina"


def test_the_refresh_looks_up_each_marked_product_by_its_index(data_dir: Path) -> None:
    with contextlib.closing(db(offers)) as conn:
        plan: str = "\n".join(row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + offer_history_index.REFRESH[2]))
    assert "SEARCH h USING INDEX offer_history_product (store=? AND product_id=?)" in plan, plan


# --- de volumedatabase ---------------------------------------------------------------------------------------------

VOLUME_CASES: tuple[tuple[str | None, str, int], ...] = (
    (None, "", 200),
    (None, "", 50),
    ("ah", "", 200),
    ("jumbo", "", 50),
    ("lidl", "", 50),
    (None, "kaas", 50),
    (None, "a", 200),
    ("ah", "melk", 50),
    ("jumbo", "kip", 200),
    (None, "bestaat niet", 50),
)


async def _same_on_volume() -> None:
    for store, query, limit in VOLUME_CASES:
        assert await offers.history(store, query, limit) == await old_history(store, query, limit), (store, query, limit)


def _volume_offers(path: Path, store: str) -> list[dict[str, Any]]:
    """De aanbiedingen van `store` uit de volumedatabase in de vorm van `offers.FETCHERS`."""
    with contextlib.closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        rows: list[sqlite3.Row] = conn.execute("SELECT * FROM offers WHERE store = ? ORDER BY position", (store,)).fetchall()
    return [
        {"id": row["product_id"], "title": row["title"], "price": row["price"], "priceBefore": row["price_before"],
         "promoLabel": row["promo_label"], "validFrom": row["valid_from"], "validUntil": row["valid_until"]}
        for row in rows
    ]  # fmt: skip


@pytest.mark.volume
async def test_the_page_is_the_same_as_the_old_query_on_the_volume_database(volume_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    await _same_on_volume()
    # De winkel wijzigt namen: een op de zeven producten van AH heeft daarna twee titels.
    with contextlib.closing(sqlite3.connect(volume_dir / volume.DB_NAME)) as conn, conn:
        conn.execute("UPDATE offer_history SET title = title || ' (nieuw)' WHERE store = 'ah' AND id % 7 = 0")
    await _same_on_volume()
    # Een synchronisatie die rijen beeindigt (een op de vijf aanbiedingen is weg), prijzen wijzigt en nieuwe producten toevoegt.
    found: list[dict[str, Any]] = _volume_offers(volume_dir / volume.DB_NAME, "ah")
    week: list[dict[str, Any]] = [
        {**offer, "price": round(float(offer["price"] or 1) + 0.1, 2)} if number % 4 == 0 else offer
        for number, offer in enumerate(found)
        if number % 5
    ] + [{"id": f"nieuw-{number}", "title": f"Nieuwe kaas {number}", "price": 1.0} for number in range(40)]
    monkeypatch.setattr(product_images, "save_bought", lambda *args: returned(0))
    with mock.patch.dict(offers.FETCHERS, {"ah": lambda: returned([dict(offer) for offer in week])}):
        await offers.sync("ah")
    with contextlib.closing(sqlite3.connect(volume_dir / volume.DB_NAME)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM offer_history_stale").fetchone()[0] == 0
    await _same_on_volume()
