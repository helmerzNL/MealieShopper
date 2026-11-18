"""De dagelijkse taak die de aanbiedingen van alle supermarkten ophaalt.

Een achtergrondtask kijkt elk kwartier of het tijd is: een keer per dag, op `MEALIESHOPPER_OFFERS_SYNC_HOUR`
(lokale tijd van de container, standaard 5 uur). Is de app dan uit, dan volgt de vernieuwing bij de eerstvolgende
start. Meerdere workers zijn geen probleem: wie het eerst de run van dit tijdstip in SQLite vastlegt, voert hem uit.
`MEALIESHOPPER_OFFERS_SCHEDULE=false` zet de taak uit; de knop onder Instellingen > Koppelen blijft werken.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from os import environ
from typing import Any, Awaitable, Callable

import aiosqlite

from . import (
    ah_orders,
    ah_price_index,
    auth,
    image_cache,
    loose_names,
    offers,
    price_archive_sync,
    prices,
    purchase_sync,
    recipe_nutrition,
    shared_price_overlay,
    shared_prices,
    tasks,
    watched_products,
)

log = logging.getLogger(__name__)

DEFAULT_HOUR = 5
CHECK_SECONDS = 15 * 60
FIRST_CHECK_SECONDS = 60

SCHEMA = """
CREATE TABLE IF NOT EXISTS offers_schedule (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  last_run TEXT NOT NULL,
  trigger TEXT NOT NULL,
  summary TEXT NOT NULL
);
"""

_task: asyncio.Task[None] | None = None
# De opwarmer van /api/offers (create_app registreert hem): rekent na een synchronisatie het oordeel alvast uit, zodat de
# eerste bezoeker niet wacht. Zonder registratie (tests, scripts) doet de synchronisatie niets extra's.
_warmer: Callable[[], Awaitable[None]] | None = None
# De rondes die de nachtelijke run start zonder erop te wachten en die in de catalogustabellen schrijven (prices.CATALOGUE_TABLES):
# de koppelingen van de losse namen (product_links) en de weekprijzen van de prijsindex (price_observations).
CATALOGUE_WRITERS: tuple[str, ...] = (loose_names.TASK_NAME, ah_price_index.TASK_NAME)
DAY_CHANGE_DELAY: float = 5.0  # zoveel seconden na middernacht warmt de lus op (`warm_if_new_day`)
_warmed_days: tuple[str, str] = ("", "")  # de lokale dag en de UTC-dag van het laatste opwarmen (`_days`)


def set_warmer(warmer: Callable[[], Awaitable[None]] | None) -> None:
    global _warmer
    _warmer = warmer


def enabled() -> bool:
    return environ.get("MEALIESHOPPER_OFFERS_SCHEDULE", "true").strip().lower() not in {"0", "false", "no", "off"}


def sync_hour() -> int:
    try:
        hour = int(environ.get("MEALIESHOPPER_OFFERS_SYNC_HOUR", DEFAULT_HOUR))
    except ValueError:
        return DEFAULT_HOUR
    return hour if 0 <= hour <= 23 else DEFAULT_HOUR


def _utc_iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _local_now() -> datetime:
    return datetime.now().astimezone()


def latest_slot(now: datetime) -> datetime:
    """Het laatste tijdstip van de dagelijkse run dat al voorbij is."""
    slot = now.replace(hour=sync_hour(), minute=0, second=0, microsecond=0)
    return slot if now >= slot else slot - timedelta(days=1)


def next_slot(now: datetime) -> datetime:
    return latest_slot(now) + timedelta(days=1)


@asynccontextmanager
async def _connect() -> AsyncIterator[aiosqlite.Connection]:
    async with auth.connect() as conn:
        await auth.ensure_schema(conn, "offers_schedule", SCHEMA)
        yield conn


async def _record(trigger: str, result: dict[str, Any], when: datetime | None = None) -> None:
    async with _connect() as conn:
        await conn.execute(
            """
            INSERT INTO offers_schedule (id, last_run, trigger, summary) VALUES (1, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET last_run = excluded.last_run, trigger = excluded.trigger, summary = excluded.summary
            """,
            (_utc_iso(when or _local_now()), trigger, json.dumps(result)),
        )
        await conn.commit()


async def run_all(trigger: str, now: datetime | None = None, *, wait_for_images: bool = True) -> dict[str, Any]:
    """De dagelijkse run: alle aanbiedingen, je bestellingen en kassabonnen (purchase_sync.sync_nightly), daarna de extraatjes die
    in hetzelfde rustige tempo bij de winkels langsgaan: een deel van de artikelnummers op de kassabonnen koppelen
    (ah_orders.resolve_receipt_products), de foto's bij oude koppelingen en de afbeeldingencache aanvullen (image_cache.prefetch),
    en de gewone prijs per aankoopregel bijwerken (prices.fill_regular_prices_nightly) en de gevolgde producten
    (watched_products.refresh_nightly). Elk extraatje staat alleen in de uitkomst als het iets te doen had. De weekprijzen van je
    gekochte AH-producten (ah_price_index.start) gaan in een eigen task: die ronde wacht tien seconden tussen twee verzoeken."""
    result = await offers.sync_all()
    # Alleen de dagelijkse run (die wacht ook op de foto's): de knop geeft meteen antwoord, en de bestelpagina heeft een eigen
    # knop. Voor de catalogus en de extraatjes: die rekenen met de nieuwe aankopen.
    if wait_for_images:
        purchases = await purchase_sync.sync_nightly()
        if purchases is not None:
            result = {**result, "purchases": purchases}
    shared: dict[str, Any] | None = await shared_prices.refresh_if_due() if shared_price_overlay.enabled() else None
    if shared is not None:
        result = {**result, "priceArchive": shared}
    await prices.warm_catalogues()  # de catalogus is na de synchronisatie anders: nu opbouwen, niet bij de eerste aanvraag
    # Het koppelen en de foto's van de bonnen krijgen hetzelfde tijdstip mee: zo staan ze samen in een laatste ronde
    # (ah_orders.SETTING_LAST_ROUND), net als bij een ronde op afroep.
    linking_now = _utc_iso(_local_now())
    extras = (
        ("receiptProducts", lambda: ah_orders.resolve_receipt_products_nightly(linking_now)),
        ("receiptImages", lambda: ah_orders.backfill_receipt_images_nightly(linking_now)),
        ("regularPrices", prices.fill_regular_prices_nightly),
        # De gevolgde producten: van elk de prijs, de maat, de verpakking en de afbeelding van vandaag (watched_products.py).
        ("watchedProducts", watched_products.refresh_nightly),
    )
    for key, task in extras:
        outcome = await task()
        if outcome is not None:
            result = {**result, key: outcome}
    if wait_for_images:
        _start_loose_names()
        _start_price_index()
        await _start_recipe_index()
        images = await image_cache.prefetch_nightly()
        if images is not None:
            result = {**result, "images": images}
    else:  # de knop: een gewone aanvraag mag niet tien minuten duren, de foto's gaan in een eigen task
        await image_cache.prefetch_async(limit=image_cache.PREFETCH_LIMIT)
    await _record(trigger, result, now)
    # Als laatste: de extraatjes hierboven schrijven in catalogustabellen en veranderen de ETag, dus pas nu heeft opwarmen zin.
    # De nachtelijke run wacht erop (niets heeft haast), de knop niet: die geeft antwoord en warmt op in een eigen task.
    if _warmer is not None:
        if wait_for_images:
            # De rondes die nu lopen, voor het opwarmen: een ronde die tijdens het opwarmen schrijft en klaar is, telt ook mee.
            writers: list[asyncio.Task[Any]] = _running_writers()
            await _warm()
            _warm_after_writers(writers)
        else:
            tasks.spawn("warm-offers", _warm())
    return result


def _start_loose_names() -> None:
    """Start de nachtelijke ronde voor losse aankoopnamen in een eigen task: de run wacht er niet op."""
    try:
        loose_names.start(None, "nightly")
    except Exception:  # noqa: BLE001 - een extraatje: een fout bij het starten mag de nachtelijke run niet vertragen of laten falen
        log.warning("Ronde voor losse namen starten mislukt", exc_info=True)


def _start_price_index() -> None:
    """Start de ronde langs de prijsindex van AH in een eigen task: de run wacht er niet op."""
    try:
        ah_price_index.start()
    except Exception:  # noqa: BLE001 - een extraatje: een fout bij het starten mag de nachtelijke run niet laten falen
        log.warning("Ronde langs de prijsindex starten mislukt", exc_info=True)


async def _start_recipe_index() -> None:
    """Werk de receptencache van Recepten zoeken bij (tijden, tags en voedingswaarden, issue #322), in een eigen task: de run
    wacht er niet op."""
    try:
        await recipe_nutrition.refresh_nightly()
    except Exception:  # noqa: BLE001 - een extraatje: een fout bij het starten mag de nachtelijke run niet laten falen
        log.warning("Receptencache bijwerken starten mislukt", exc_info=True)


def _running_writers() -> list[asyncio.Task[Any]]:
    """De rondes van `CATALOGUE_WRITERS` die nu nog lopen."""
    running: dict[str, asyncio.Task[Any]] = tasks.running()
    return [task for name in CATALOGUE_WRITERS if (task := running.get(name)) is not None and not task.done()]


def _warm_after_writers(writers: list[asyncio.Task[Any]]) -> None:
    """Liep er bij het opwarmen een ronde van `CATALOGUE_WRITERS` (`writers`), dan warmt een eigen task nog een keer op zodra die
    allemaal klaar zijn. Op 17-11-2026 schreef de ronde voor losse namen nog twee minuten koppelingen na het opwarmen: de ETag
    veranderde, en de eerste bezoeker van die dag wachtte 6,3 s op een lijst die de app op de aanvraag uitrekende."""
    if writers:
        tasks.spawn("warm-offers-after-rounds", _warm_when_done(writers))


async def _warm_when_done(writers: list[asyncio.Task[Any]]) -> None:
    # `asyncio.wait` geeft geen fout van een ronde door: een mislukte of geannuleerde ronde telt ook als klaar.
    await asyncio.wait(writers)
    await _warm()


def _days(moment: datetime) -> tuple[str, str]:
    """De lokale dag en de UTC-dag van `moment`: de ETag van /api/offers bevat ze allebei (`offers.signature` rekent met de lokale
    dag, `prices.offers_signature` met de UTC-dag)."""
    return moment.date().isoformat(), moment.astimezone(timezone.utc).date().isoformat()


def _remember_day() -> None:
    """Onthoud de dagen van nu als opgewarmd (`warm_if_new_day`)."""
    global _warmed_days
    _warmed_days = _days(_local_now())


def _seconds_to_day_change() -> float:
    """Seconden tot de eerstvolgende middernacht, lokaal of in UTC, wat het eerst komt, plus `DAY_CHANGE_DELAY`."""
    now: datetime = _local_now()
    waits: list[float] = []
    for moment in (now, now.astimezone(timezone.utc)):
        midnight: datetime = (moment + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        waits.append((midnight - moment).total_seconds())
    return min(waits) + DAY_CHANGE_DELAY


async def warm_if_new_day() -> bool:
    """Warmt de catalogi en de aanbiedingen op als de lokale dag of de UTC-dag veranderde sinds het vorige opwarmen. Na middernacht
    is elke lijst van /api/offers ongeldig, en tot de nachtelijke run rekende de eerste bezoeker hem op de aanvraag uit (18-11-2026).
    Geeft terug of het opwarmde."""
    if _warmer is None or _days(_local_now()) == _warmed_days:
        return False
    _remember_day()
    try:
        await prices.warm_catalogues()
    except Exception:  # noqa: BLE001 - opwarmen is een extraatje
        log.warning("Catalogi opwarmen na de dagwissel mislukt", exc_info=True)
    await _warm()
    return True


async def _warm() -> None:
    if _warmer is None:
        return
    _remember_day()
    try:
        await _warmer()
    except Exception:  # noqa: BLE001 - opwarmen is een extraatje
        log.warning("Aanbiedingen opwarmen na de synchronisatie mislukt", exc_info=True)


async def sync_now(trigger: str = "manual") -> dict[str, Any]:
    """Haal alle aanbiedingen op (de knop onder Koppelen) en onthoud de uitkomst voor de statusregel."""
    return await run_all(trigger, wait_for_images=False)


async def status(now: datetime | None = None) -> dict[str, Any]:
    now = now or _local_now()
    async with _connect() as conn:
        async with conn.execute("SELECT last_run, trigger, summary FROM offers_schedule WHERE id = 1") as cursor:
            row = await cursor.fetchone()
    on = enabled()
    return {
        "enabled": on,
        "hour": sync_hour(),
        "nextRun": _utc_iso(next_slot(now)) if on else None,
        "lastRun": row["last_run"] if row else None,
        "lastTrigger": row["trigger"] if row else None,
        "lastResult": json.loads(row["summary"]) if row else None,
        "stores": await offers.overview(),
    }


async def _claim(now: datetime) -> bool:
    """Leg vast dat deze worker de run van het laatste tijdstip uitvoert. Alleen de eerste die dat doet krijgt True."""
    slot = _utc_iso(latest_slot(now))
    async with _connect() as conn:
        await conn.execute(
            "INSERT INTO offers_schedule (id, last_run, trigger, summary) VALUES (1, '', 'scheduled', '{}') ON CONFLICT(id) DO NOTHING"
        )
        claimed = await conn.execute(
            "UPDATE offers_schedule SET last_run = ?, trigger = 'scheduled' WHERE id = 1 AND last_run < ?",
            (_utc_iso(now), slot),
        )
        await conn.commit()
        return claimed.rowcount == 1


async def run_if_due(now: datetime | None = None) -> bool:
    """Voer de dagelijkse run uit als het tijd is en niemand anders hem al deed. Geeft terug of hij liep."""
    now = now or _local_now()
    if shared_price_overlay.enabled():
        await shared_prices.refresh_if_due()
        await shared_price_overlay.reconcile_if_due()
    if not enabled():
        return False
    if not await _claim(now):
        return False
    await run_all("scheduled", now)
    return True


async def _loop() -> None:
    # Bij de start warmt create_app de aanbiedingen al op (warm_at_start): de dag van nu telt als opgewarmd.
    _remember_day()
    wait: float = FIRST_CHECK_SECONDS
    while True:
        await asyncio.sleep(wait)
        # Elk kwartier, en ook vlak na elke middernacht (`warm_if_new_day`).
        wait = min(CHECK_SECONDS, _seconds_to_day_change())
        try:
            if await run_if_due():
                log.info("Aanbiedingen van alle winkels vernieuwd")
            await warm_if_new_day()
        except Exception:  # noqa: BLE001 - de taak moet blijven draaien, ook na een fout
            log.exception("Dagelijkse aanbiedingen-run mislukt")


def start() -> None:
    """Start de achtergrondtask (een keer per proces). Moet binnen een lopende event loop worden aangeroepen."""
    global _task
    if (_task is not None and not _task.done()) or not (enabled() or price_archive_sync.enabled()):
        return
    # Elk kwartier een controle. De run zelf (`run_all`) heeft een volumetest: test_the_nightly_tasks_do_not_stall (taak 10).
    _task = tasks.spawn("offers-schedule", _loop())
