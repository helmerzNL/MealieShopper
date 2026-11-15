"""Het leesmodel van de prijsgeschiedenis (`mealieshopper/price_history_index.py`).

Het antwoord van `prices.history` moet hetzelfde blijven als dat van de berekening van voor het leesmodel, die over de hele
catalogus liep (`reference` hieronder, de code van `prices.history` tot 4.25.1). Op een kleine volumedatabase (`volume.build`
met schaal 0,01) bij de gewone tests, en op de volumedatabase zelf bij de volumetests. Daarna wat na het schrijven gebeurt: een
nieuwe prijs van een bekend product werkt alleen dat product bij (de trigger), een nieuwe aanbieding bouwt alles opnieuw op,
en een schrijver die het leesmodel niet kent (een oudere versie, een taak die direct in SQLite schrijft) laat het niet achter.
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import volume

from mealieshopper import compute, offers, price_history_index, prices

SMALL_SCALE: float = 0.01


async def reference(
    store: Any = None,
    query: Any = "",
    limit: Any = 50,
    today: str = volume.TODAY,
    bought: bool = False,
    product: Any = None,
    pin: Any = None,
    brands: Any = None,
    brands_out: Any = None,
    categories: Any = None,
    categories_out: Any = None,
) -> dict[str, Any]:
    """De oude berekening van `prices.history`, zonder `lookup`: elke aanvraag over alle producten van de catalogus, en de prijzen
    en lijnacties van de pagina uit de bronnen. Bewust gelijk aan de code van 4.25.1, zodat de test laat zien dat het leesmodel
    niets aan het antwoord verandert."""
    stores = [offers._valid_store(store)] if store else list(prices.STORES)
    wanted = prices._Query(query)
    limit = prices._limit(limit)
    only = str(product) if product not in (None, "") else None
    pinned = str(pin) if pin not in (None, "") else None
    async with prices._connect() as conn:
        catalogues = {name: await prices._catalogue(conn, name, today) for name in stores}
        receipts = {name: catalogue.receipts for name, catalogue in catalogues.items()}
        unsynced = {name: await prices._unsynced(conn, name) for name in stores}
        keeps: dict[str, set[str] | None] = {
            name: prices._with_twins(await prices._bought(conn, name, today, receipts[name]), catalogues[name].twins) if bought else None
            for name in stores
        }
        candidates: dict[tuple[str, str], str] = await compute.run(prices._history_candidates, catalogues, wanted, only, pinned, keeps)
        pinned_known: dict[str, dict[str, Any]] = {}
        for name in stores:
            if pinned is not None and store and only is None and (name, pinned) not in candidates:
                known = await prices._pinned(conn, name, pinned, await prices._tables(conn))
                if known is not None:
                    candidates[(name, pinned)] = today
                    pinned_known[name] = known
        ranked = await compute.run(
            prices._rank_history,
            catalogues,
            candidates,
            pinned,
            only,
            prices._Choice.of(brands, brands_out),
            prices._Choice.of(categories, categories_out),
            limit,
        )
        chosen: list[tuple[str, str]] = ranked.chosen
        found: dict[str, Any] = {}
        for name in stores:
            ids = sorted(prices._with_twins({product_id for store_name, product_id in chosen if store_name == name}, catalogues[name].twins))
            found[name] = (
                (
                    await prices._observations(conn, name, ids),
                    await prices._purchases(conn, name, ids, today, receipts[name]),
                    await prices._saved_images(conn, name, ids),
                )
                if ids
                else ({}, {}, {})
            )
        upcoming = {name: await prices._upcoming(conn, name, [pid for s, pid in chosen if s == name], today) for name in stores}
    offered = {name: await offers.history_of(name, [pid for s, pid in chosen if s == name]) for name in stores}
    lines = {name: await prices._line_offers(name, catalogues[name], [pid for s, pid in chosen if s == name]) for name in stores}
    sources = prices._ProductSources(catalogues, offered, found, upcoming, pinned_known, lines)
    products: list[dict[str, Any]] = await compute.run(prices._page_products, chosen, sources, pinned, today)
    return {"products": products, "total": ranked.total, "unsynced": unsynced, "facets": ranked.facets}


async def assert_same(**ask: Any) -> dict[str, Any]:
    """`prices.history` met het leesmodel en `reference` geven hetzelfde antwoord. Geeft het antwoord terug."""
    got: dict[str, Any] = await prices.history(today=volume.TODAY, **ask)
    expected: dict[str, Any] = await reference(**ask)
    assert [(item["store"], item["productId"]) for item in got["products"]] == [
        (item["store"], item["productId"]) for item in expected["products"]
    ], ask
    assert got == expected, ask
    return got


@pytest.fixture(scope="session")
def small_template(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """De volumedatabase op schaal 0,01 (een paar seconden), een keer per run. Een test werkt op een kopie (`small_dir`)."""
    folder: Path = tmp_path_factory.mktemp("small_volume")
    volume.build(folder, scale=SMALL_SCALE)
    yield folder
    shutil.rmtree(folder, ignore_errors=True)


@pytest.fixture
def small_dir(small_template: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Een kopie van de kleine volumedatabase als datamap, met de klok op `volume.TODAY` (zoals `volume_dir`)."""
    shutil.copyfile(small_template / volume.DB_NAME, tmp_path / volume.DB_NAME)
    monkeypatch.setenv("MEALIESHOPPER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MEALIESHOPPER_TESTING", "1")
    volume.freeze_clock(monkeypatch)
    prices._CATALOGUES.clear()
    yield tmp_path
    prices._CATALOGUES.clear()


def execute(folder: Path, sql: str, args: tuple[Any, ...] = ()) -> None:
    """Schrijft zoals een taak of een oudere versie van de app: met een eigen verbinding, buiten de app om."""
    with contextlib.closing(sqlite3.connect(folder / volume.DB_NAME)) as conn:
        conn.execute(sql, args)
        conn.commit()


def query(folder: Path, sql: str, args: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    with contextlib.closing(sqlite3.connect(folder / volume.DB_NAME)) as conn:
        return conn.execute(sql, args).fetchall()


def meta(folder: Path, store: str) -> tuple[Any, ...]:
    return query(folder, "SELECT version, day, prices, generation FROM price_history_meta WHERE store = ?", (store,))[0]


async def catalogue(store: str) -> prices._Catalogue:
    async with prices._connect() as conn:
        return await prices._catalogue(conn, store, volume.TODAY)


async def asks(store: str) -> list[dict[str, Any]]:
    """De aanvragen die de test vergelijkt: zonder en met zoekterm, met een maat, Eerder gekocht, de keuzelijsten, het product uit
    een link (een ander nummer van een product met meer nummers, en een onbekend nummer) en het ene product van de hover."""
    found: prices._Catalogue = await catalogue(store)
    twin: str = sorted(found.twins)[0]
    lead: str = max(found.twins[twin], key=lambda other: (found.products[other][prices._LAST], other))
    other: str = next(member for member in found.twins[twin] if member != lead)
    answer: dict[str, Any] = await prices.history(store, today=volume.TODAY)
    brand: str = answer["facets"]["brands"][0]["value"]
    category: str = answer["facets"]["categories"][0]["value"]
    return [
        {"store": store},
        {"store": store, "limit": 200},
        {"store": store, "query": "melk"},
        {"store": store, "query": "halfvolle melk"},
        {"store": store, "query": "500 g"},
        {"store": store, "query": "kaas 500 g"},
        {"store": store, "bought": True},
        {"store": store, "bought": True, "query": "kaas"},
        {"store": store, "brands": [brand]},
        {"store": store, "brands_out": [brand], "categories": [category]},
        {"store": store, "categories_out": [category], "bought": True},
        {"store": store, "pin": other},
        {"store": store, "pin": other, "query": "melk"},
        {"store": store, "pin": other, "brands_out": [found.products[other][prices._BRAND]]},
        {"store": store, "pin": other, "bought": True},
        {"store": store, "pin": "nergens-bekend"},
        {"store": store, "product": other},
        {"store": store, "product": lead},
        {"query": "melk"},
        {},
        {"pin": other},
    ]


def test_the_fields_of_a_known_product_are_those_of_the_catalogue() -> None:
    # Het leesmodel leest `prices._Known` op plaats: die moeten gelijk blijven.
    assert (price_history_index.LAST, price_history_index.NAMES, price_history_index.SIZES) == (prices._LAST, prices._NAMES, prices._SIZES)
    assert (price_history_index.BRAND, price_history_index.CATEGORY) == (prices._BRAND, prices._CATEGORY)


async def test_the_answer_is_that_of_the_whole_catalogue(small_dir: Path) -> None:
    for store in ("ah", "jumbo"):
        for ask in await asks(store):
            await assert_same(**ask)


async def test_the_page_without_a_query_comes_from_the_index(small_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Zonder zoekterm loopt niets meer over de hele catalogus: dat was bij AH op de volumedatabase 106 van de 130 ms.
    await prices.history("ah", today=volume.TODAY)

    def whole_catalogue(*args: Any) -> None:
        raise AssertionError("over de hele catalogus")

    monkeypatch.setattr(prices, "_history_candidates", whole_catalogue)
    monkeypatch.setattr(prices, "_rank_history", whole_catalogue)
    answer: dict[str, Any] = await prices.history("ah", today=volume.TODAY)
    assert answer["products"]
    assert answer["total"] > len(answer["products"])


async def test_the_prices_and_lines_of_a_page_come_from_their_documents(small_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first: dict[str, Any] = await prices.history("ah", today=volume.TODAY)
    assert query(small_dir, "SELECT COUNT(*) FROM price_history_docs WHERE store = 'ah'")[0][0] >= len(first["products"])

    async def from_the_sources(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("uit de bronnen")

    monkeypatch.setattr(prices, "_observations", from_the_sources)
    monkeypatch.setattr(prices, "_line_offers", from_the_sources)
    assert await prices.history("ah", today=volume.TODAY) == first


def _known_observation(folder: Path, store: str, ids: Any = None) -> tuple[Any, ...]:
    """Een bewaarde prijs van een product dat vandaag nog geen prijs heeft (`ids`: alleen een van die producten)."""
    rows: list[tuple[Any, ...]] = query(
        folder,
        "SELECT product_id, title, brand, unit_size, image, url, price FROM price_observations AS p WHERE store = ? AND day < ? "
        "AND source = 'search' AND NOT EXISTS (SELECT 1 FROM price_observations AS q WHERE q.store = p.store "
        "AND q.product_id = p.product_id AND q.day >= ?) ORDER BY product_id, day",
        (store, volume.TODAY, volume.TODAY),
    )
    return next(row for row in rows if ids is None or row[0] in ids)


def _observe_today(folder: Path, store: str, row: tuple[Any, ...]) -> None:
    """Een nieuwe prijs van vandaag, met dezelfde naam, hetzelfde merk en dezelfde verpakking: alleen `prices` gaat omhoog."""
    product_id, title, brand, unit_size, image, url, price = row
    execute(
        folder,
        "INSERT INTO price_observations (store, product_id, day, source, title, brand, unit_size, image, url, price, seen_at) "
        "VALUES (?, ?, ?, 'search', ?, ?, ?, ?, ?, ?, ?)",
        (store, product_id, volume.TODAY, title, brand, unit_size, image, url, round(price + 0.1, 2), f"{volume.TODAY}T08:00:00+00:00"),
    )


async def test_a_new_price_of_a_known_product_updates_only_that_product(small_dir: Path) -> None:
    await prices.history("ah", today=volume.TODAY)
    before: tuple[Any, ...] = meta(small_dir, "ah")
    row: tuple[Any, ...] = _known_observation(small_dir, "ah")
    _observe_today(small_dir, "ah", row)
    assert query(small_dir, "SELECT n FROM price_history_stale WHERE store = 'ah' AND product_id = ?", (row[0],)) == [(1,)]
    answer: dict[str, Any] = await assert_same(store="ah")
    after: tuple[Any, ...] = meta(small_dir, "ah")
    assert after[:2] == before[:2] and after[3] == before[3], "geen nieuwe opbouw voor een prijs van een bekend product"
    assert after[2] > before[2]
    assert query(small_dir, "SELECT COUNT(*) FROM price_history_stale") == [(0,)]
    group: set[str] = {row[0], *(await catalogue("ah")).twins.get(row[0], ())}
    shown: dict[str, Any] = next(item for item in answer["products"] if item["productId"] in group)
    assert shown["lastActivity"] == volume.TODAY
    assert shown["regularPrice"] == round(row[6] + 0.1, 2)


async def test_a_new_price_of_another_number_moves_the_group_to_that_number(small_dir: Path) -> None:
    # Een groep van nummers van hetzelfde product staat in de lijst onder het nummer met de nieuwste gegevens. Een prijs van
    # vandaag voor een ander nummer van de groep zet de groep daarna onder dat nummer, zonder nieuwe opbouw.
    found: prices._Catalogue = await catalogue("ah")
    group: tuple[str, ...] = next(group for group in found.twins.values() if all(found.products[pid][prices._LAST] < volume.TODAY for pid in group))
    lead: str = max(group, key=lambda other: (found.products[other][prices._LAST], other))
    other: str = next(pid for pid in group if pid != lead)
    title, brand, unit_size = query(small_dir, "SELECT title, brand, unit_size FROM offer_history WHERE store = 'ah' AND product_id = ?", (other,))[0]
    row: tuple[Any, ...] = (other, title, brand, unit_size, None, None, 1.0)
    execute(
        small_dir,
        "INSERT INTO price_observations (store, product_id, day, source, title, brand, unit_size, price, seen_at) "
        "VALUES ('ah', ?, '2026-11-01', 'search', ?, ?, ?, 1.0, '2026-11-01T08:00:00+00:00')",
        (other, title, brand, unit_size),
    )
    await assert_same(store="ah", pin=lead)
    before: tuple[Any, ...] = meta(small_dir, "ah")
    _observe_today(small_dir, "ah", row)
    answer: dict[str, Any] = await assert_same(store="ah")
    assert meta(small_dir, "ah")[3] == before[3], "geen nieuwe opbouw"
    shown: list[str] = [item["productId"] for item in answer["products"]]
    assert other in shown and lead not in shown
    await assert_same(store="ah", query=found.products[other][prices._NAMES][0])
    await assert_same(store="ah", pin=lead)


async def test_a_new_offer_rebuilds_the_rows(small_dir: Path) -> None:
    await prices.history("ah", today=volume.TODAY)
    before: tuple[Any, ...] = meta(small_dir, "ah")
    execute(
        small_dir,
        "INSERT INTO offer_history (store, product_id, title, brand, unit_size, category, price, price_before, promo_label, valid_from, "
        "valid_until, first_seen, last_seen) VALUES ('ah', 'nieuw-1', 'Nieuwe Halfvolle Melk', 'Nieuwmerk', '1 l', NULL, 0.99, 1.29, "
        "'1 + 1 gratis', ?, ?, ?, ?)",
        (volume.TODAY, volume.TODAY, f"{volume.TODAY}T06:00:00+00:00", f"{volume.TODAY}T06:00:00+00:00"),
    )
    answer: dict[str, Any] = await assert_same(store="ah")
    assert "nieuw-1" in [item["productId"] for item in answer["products"]]
    assert meta(small_dir, "ah")[0] != before[0]
    assert meta(small_dir, "ah")[3] == before[3] + 1, "de documenten van voor de aanbieding gelden niet meer"
    await assert_same(store="ah", query="nieuwe melk")
    await assert_same(store="ah", brands=["Nieuwmerk"])


async def test_a_writer_without_the_read_model_leaves_it_behind_nothing(small_dir: Path) -> None:
    # Een oudere versie (4.25.1) kent de tabellen van het leesmodel niet, maar haar schrijven gaat door de triggers: zonder
    # rij in `price_history_meta` (die haalt alleen de nieuwe versie weg) en met de prijzen ertussen moet het antwoord kloppen.
    await prices.history("ah", today=volume.TODAY)
    execute(small_dir, "DELETE FROM price_history_meta")
    row: tuple[Any, ...] = _known_observation(small_dir, "ah")
    _observe_today(small_dir, "ah", row)
    execute(small_dir, "UPDATE price_observations SET price = price + 1 WHERE store = 'ah' AND product_id = ? AND day = ?", (row[0], volume.TODAY))
    execute(small_dir, "DELETE FROM price_observations WHERE store = 'ah' AND product_id = ? AND day < ?", (row[0], volume.TODAY))
    await assert_same(store="ah")
    await assert_same(store="ah", product=row[0])


async def test_missing_triggers_come_back_and_rebuild(small_dir: Path) -> None:
    # Zonder trigger markeert niemand een nieuwe prijs. De volgende aanvraag (in hetzelfde proces) zet ze terug en bouwt opnieuw op.
    await prices.history("ah", today=volume.TODAY)
    for name in price_history_index.TRIGGERS:
        execute(small_dir, f"DROP TRIGGER {name}")
    _observe_today(small_dir, "ah", _known_observation(small_dir, "ah"))
    assert query(small_dir, "SELECT COUNT(*) FROM price_history_stale") == [(0,)]
    await assert_same(store="ah")
    names: set[str] = {row[0] for row in query(small_dir, "SELECT name FROM sqlite_master WHERE type = 'trigger'")}
    assert set(price_history_index.TRIGGERS) <= names


async def test_a_new_day_and_a_restored_backup_rebuild(small_dir: Path) -> None:
    await prices.history("ah", today=volume.TODAY)
    generation: int = meta(small_dir, "ah")[3]
    await prices.history("ah", today="2026-11-14")
    assert meta(small_dir, "ah")[1:] == ("2026-11-14", meta(small_dir, "ah")[2], generation + 1)
    execute(small_dir, "DELETE FROM catalogue_versions")  # zoals het terugzetten van een back-up (backup.py)
    prices.forget_catalogues()
    await assert_same(store="ah")
    assert meta(small_dir, "ah")[3] == generation + 2


async def test_two_requests_at_once_rebuild_once(small_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    writes: list[str] = []
    original: Any = price_history_index.changes

    def counted(store: str, *args: Any) -> Any:
        writes.append(store)
        return original(store, *args)

    monkeypatch.setattr(price_history_index, "changes", counted)
    # De versie van de catalogus staat vast voordat de twee aanvragen haar lezen, zoals in test_volume.py: op een lege database
    # maakt de eerste aanvraag de triggers van de catalogus aan en hoogt daarmee de teller van de producten op, zodat de tweede
    # een andere versie zag en zelf opbouwde (een op de vijf runs, 2026-11-15).
    async with prices._connect() as conn:
        await prices._catalogue_version(conn, "ah")
    first, second = await asyncio.gather(prices.history("ah", today=volume.TODAY), prices.history("ah", today=volume.TODAY))
    assert first == second
    assert writes == ["ah"]


async def test_a_rebuild_that_waited_behind_a_newer_one_writes_nothing(small_dir: Path) -> None:
    # Twee aanvragen met een andere stand van `prices` bouwen allebei op. Die met de oudere stand die als laatste het schrijfslot
    # krijgt, schrijft niets: met haar oudere catalogus kon ze een prijs terugdraaien die de nieuwere al verwerkte.
    await prices.history("ah", today=volume.TODAY)
    version, day, counted, generation = meta(small_dir, "ah")
    found: prices._Catalogue = await catalogue("ah")
    older: price_history_index.Before = price_history_index.Before(version, counted - 1, None, ())
    fresh: price_history_index.Fresh = await price_history_index._rebuild("ah", day, older, {}, {}, [])
    assert fresh == price_history_index.Fresh(generation, counted)
    assert query(small_dir, "SELECT COUNT(*) FROM price_history_products WHERE store = 'ah'") == [(len(found.products),)]
    newer: price_history_index.Before = price_history_index.Before(version, counted + 1, None, ())
    assert (await price_history_index._rebuild("ah", day, newer, found.products, found.twins, found.receipts)).generation == generation + 1


# --- op de volumedatabase -------------------------------------------------------------------------------------------


@pytest.mark.volume
@pytest.mark.may_block(
    "na de nieuwe aanbieding bouwt de catalogus koud opnieuw op (prices._new_catalogue), met een ronde van de GC erin: dat meten "
    "de koude tests van tests/test_volume.py, deze test vergelijkt alleen de antwoorden"
)
async def test_the_answer_on_the_volume_database_is_that_of_the_whole_catalogue(volume_dir: Path) -> None:
    for ask in await asks("ah"):
        await assert_same(**ask)
    row: tuple[Any, ...] = _known_observation(volume_dir, "ah")
    _observe_today(volume_dir, "ah", row)
    await assert_same(store="ah")
    await assert_same(store="ah", pin=row[0])
    execute(
        volume_dir,
        "INSERT INTO offer_history (store, product_id, title, brand, unit_size, category, price, price_before, promo_label, valid_from, "
        "valid_until, first_seen, last_seen) SELECT store, product_id, title, brand, unit_size, category, price * 0.8, price_before, "
        "'25% korting', ?, ?, ?, ? FROM offer_history WHERE store = 'ah' AND product_id NOT LIKE 'folder:%' ORDER BY id LIMIT 1",
        (volume.TODAY, volume.TODAY, f"{volume.TODAY}T06:00:00+00:00", f"{volume.TODAY}T06:00:00+00:00"),
    )
    await assert_same(store="ah")
    await assert_same(store="ah", query="melk")


def _observe_many_today(folder: Path, count: int) -> int:
    """Een prijs van vandaag voor `count` bekende producten van AH, zoals een ronde langs de prijsindex: dezelfde prijs als hun
    laatste, dus alleen `prices` gaat omhoog en geen bonregel krijgt een ander product. Elk product komt in
    `price_history_stale`."""
    with contextlib.closing(sqlite3.connect(folder / volume.DB_NAME)) as conn:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO price_observations (store, product_id, day, source, title, brand, unit_size, image, url, price, seen_at) "
            "SELECT store, product_id, ?, source, title, brand, unit_size, image, url, price, ? FROM price_observations AS p "
            "WHERE store = 'ah' AND day = (SELECT MAX(day) FROM price_observations AS q WHERE q.store = 'ah' AND q.product_id = p.product_id) "
            "AND day < ? LIMIT ?",
            (volume.TODAY, f"{volume.TODAY}T09:00:00+00:00", volume.TODAY, count),
        )
        conn.commit()
        return cursor.rowcount


def _raise_today(folder: Path) -> int:
    """Een hogere prijs voor elke prijs van vandaag (een UPDATE: alleen `prices` gaat omhoog)."""
    with contextlib.closing(sqlite3.connect(folder / volume.DB_NAME)) as conn:
        cursor = conn.execute("UPDATE price_observations SET price = price + 0.05 WHERE store = 'ah' AND day = ?", (volume.TODAY,))
        conn.commit()
        return cursor.rowcount


def _receipt_products(found: prices._Catalogue) -> list[Any]:
    return [row.get("product_id") for row in found.receipts]


@pytest.mark.volume
async def test_many_new_prices_do_not_stall(volume_dir: Path) -> None:
    # Een ronde die duizenden prijzen van bekende producten schrijft: de volgende aanvraag werkt die producten bij zonder
    # nieuwe opbouw, en zonder de event loop vast te houden.
    from test_volume import quiet

    await prices.history("ah", today=volume.TODAY)
    generation: int = meta(volume_dir, "ah")[3]
    written: list[int] = []

    async def prepare() -> None:
        written.append(await asyncio.to_thread(_observe_many_today, volume_dir, 5_000))

    answer: dict[str, Any] = await quiet(lambda: prices.history("ah", today=volume.TODAY), prepare=prepare)
    assert written[0] > 1_000
    assert answer["products"]
    assert meta(volume_dir, "ah")[3] == generation, "alleen bijgewerkt, niet opnieuw opgebouwd"
    assert query(volume_dir, "SELECT COUNT(*) FROM price_history_stale") == [(0,)]
    await assert_same(store="ah")


@pytest.mark.volume
async def test_a_price_that_moves_a_receipt_line_rebuilds(volume_dir: Path) -> None:
    # Een andere prijs kan bepalen welk product met dezelfde naam een bonregel krijgt (`prices._nearest_priced`). Dan veranderen
    # de namen en de laatste dag van twee producten zonder dat de versie van de catalogus omhoog gaat: het leesmodel ziet het aan
    # de bonregels (`receipts_digest`) en bouwt opnieuw op.
    await asyncio.to_thread(_observe_many_today, volume_dir, 5_000)
    await prices.history("ah", today=volume.TODAY)
    linked: list[Any] = _receipt_products(await catalogue("ah"))
    generation: int = meta(volume_dir, "ah")[3]
    assert await asyncio.to_thread(_raise_today, volume_dir) > 1_000
    await assert_same(store="ah")
    moved: bool = _receipt_products(await catalogue("ah")) != linked
    assert meta(volume_dir, "ah")[3] == generation + (1 if moved else 0)
    await assert_same(store="ah", query="melk")


@pytest.mark.volume
async def test_the_price_history_is_fast_enough(volume_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # Doel van het leesmodel: /api/prices/history?store=ah onder 50 ms warm op de volumedatabase (was 130 ms, in productie 260
    # tot 400). Het beste van vijf, na een eerste aanvraag die de catalogus en het leesmodel opbouwt.
    await prices.history("ah", today=volume.TODAY)
    best: float = float("inf")
    for _ in range(5):
        start: float = time.perf_counter()
        await prices.history("ah", today=volume.TODAY)
        best = min(best, time.perf_counter() - start)
    from test_volume import _judge_speed  # de regel van de snelheidstests: op CI alleen melden, een runner is trager

    _judge_speed("prices.history(store=ah) warm", best * 1000, 50, capsys)
