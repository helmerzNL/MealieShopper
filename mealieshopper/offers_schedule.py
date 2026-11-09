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

from . import ah_orders, auth, image_cache, offers, prices, tasks

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
    """De dagelijkse run: alle aanbiedingen, daarna de extraatjes die in hetzelfde rustige tempo bij de winkels langsgaan: een deel
    van de artikelnummers op de kassabonnen koppelen (ah_orders.resolve_receipt_products), de foto's bij oude koppelingen en de
    afbeeldingencache aanvullen (image_cache.prefetch), en de gewone prijs per aankoopregel bijwerken
    (prices.fill_regular_prices_nightly). Elk extraatje staat alleen in de uitkomst als het iets te doen had."""
    result = await offers.sync_all()
    await prices.warm_catalogues()  # de catalogus is na de synchronisatie anders: nu opbouwen, niet bij de eerste aanvraag
    # Het koppelen en de foto's van de bonnen krijgen hetzelfde tijdstip mee: zo staan ze samen in een laatste ronde
    # (ah_orders.SETTING_LAST_ROUND), net als bij een ronde op afroep.
    linking_now = _utc_iso(_local_now())
    extras = (
        ("receiptProducts", lambda: ah_orders.resolve_receipt_products_nightly(linking_now)),
        ("receiptImages", lambda: ah_orders.backfill_receipt_images_nightly(linking_now)),
        ("regularPrices", prices.fill_regular_prices_nightly),
    )
    for key, task in extras:
        outcome = await task()
        if outcome is not None:
            result = {**result, key: outcome}
    if wait_for_images:
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
            await _warm()
        else:
            tasks.spawn("warm-offers", _warm())
    return result


async def _warm() -> None:
    if _warmer is None:
        return
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
        await conn.execute("INSERT INTO offers_schedule (id, last_run, trigger, summary) VALUES (1, '', 'scheduled', '{}') ON CONFLICT(id) DO NOTHING")
        claimed = await conn.execute(
            "UPDATE offers_schedule SET last_run = ?, trigger = 'scheduled' WHERE id = 1 AND last_run < ?",
            (_utc_iso(now), slot),
        )
        await conn.commit()
        return claimed.rowcount == 1


async def run_if_due(now: datetime | None = None) -> bool:
    """Voer de dagelijkse run uit als het tijd is en niemand anders hem al deed. Geeft terug of hij liep."""
    if not enabled():
        return False
    now = now or _local_now()
    if not await _claim(now):
        return False
    await run_all("scheduled", now)
    return True


async def _loop() -> None:
    wait: float = FIRST_CHECK_SECONDS
    while True:
        await asyncio.sleep(wait)
        wait = CHECK_SECONDS
        try:
            if await run_if_due():
                log.info("Aanbiedingen van alle winkels vernieuwd")
        except Exception:  # noqa: BLE001 - de taak moet blijven draaien, ook na een fout
            log.exception("Dagelijkse aanbiedingen-run mislukt")


def start() -> None:
    """Start de achtergrondtask (een keer per proces). Moet binnen een lopende event loop worden aangeroepen."""
    global _task
    if (_task is not None and not _task.done()) or not enabled():
        return
    _task = tasks.spawn("offers-schedule", _loop())
