"""De dagelijkse taak die de aanbiedingen van alle supermarkten ophaalt.

Een achtergrondthread kijkt elk kwartier of het tijd is: een keer per dag, op `MEALIESHOPPER_OFFERS_SYNC_HOUR`
(lokale tijd van de container, standaard 5 uur). Is de app dan uit, dan volgt de vernieuwing bij de eerstvolgende
start. Meerdere workers zijn geen probleem: wie het eerst de run van dit tijdstip in SQLite vastlegt, voert hem uit.
`MEALIESHOPPER_OFFERS_SCHEDULE=false` zet de taak uit; de knop onder Instellingen > Koppelen blijft werken.
"""
from __future__ import annotations

import json
import logging
import threading
from contextlib import closing
from datetime import datetime, timedelta, timezone
from os import environ
from typing import Any

from . import auth, offers

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

_lock = threading.Lock()
_thread: threading.Thread | None = None


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


def _connect():
    conn = auth.connect()
    conn.executescript(SCHEMA)
    return conn


def _record(trigger: str, result: dict[str, Any], when: datetime | None = None) -> None:
    with closing(_connect()) as conn, conn:
        conn.execute(
            """
            INSERT INTO offers_schedule (id, last_run, trigger, summary) VALUES (1, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET last_run = excluded.last_run, trigger = excluded.trigger, summary = excluded.summary
            """,
            (_utc_iso(when or _local_now()), trigger, json.dumps(result)),
        )


def sync_now(trigger: str = "manual") -> dict[str, Any]:
    """Haal alle aanbiedingen op en onthoud de uitkomst (voor de statusregel onder Koppelen)."""
    result = offers.sync_all()
    _record(trigger, result)
    return result


def status(now: datetime | None = None) -> dict[str, Any]:
    now = now or _local_now()
    with closing(_connect()) as conn:
        row = conn.execute("SELECT last_run, trigger, summary FROM offers_schedule WHERE id = 1").fetchone()
    on = enabled()
    return {
        "enabled": on,
        "hour": sync_hour(),
        "nextRun": _utc_iso(next_slot(now)) if on else None,
        "lastRun": row["last_run"] if row else None,
        "lastTrigger": row["trigger"] if row else None,
        "lastResult": json.loads(row["summary"]) if row else None,
        "stores": offers.overview(),
    }


def _claim(now: datetime) -> bool:
    """Leg vast dat deze worker de run van het laatste tijdstip uitvoert. Alleen de eerste die dat doet krijgt True."""
    slot = _utc_iso(latest_slot(now))
    with closing(_connect()) as conn, conn:
        conn.execute("INSERT OR IGNORE INTO offers_schedule (id, last_run, trigger, summary) VALUES (1, '', 'scheduled', '{}')")
        claimed = conn.execute(
            "UPDATE offers_schedule SET last_run = ?, trigger = 'scheduled' WHERE id = 1 AND last_run < ?",
            (_utc_iso(now), slot),
        )
        return claimed.rowcount == 1


def run_if_due(now: datetime | None = None) -> bool:
    """Voer de dagelijkse run uit als het tijd is en niemand anders hem al deed. Geeft terug of hij liep."""
    if not enabled():
        return False
    now = now or _local_now()
    if not _claim(now):
        return False
    _record("scheduled", offers.sync_all(), now)
    return True


def _loop() -> None:
    wait = FIRST_CHECK_SECONDS
    while True:
        threading.Event().wait(wait)
        wait = CHECK_SECONDS
        try:
            if run_if_due():
                log.info("Aanbiedingen van alle winkels vernieuwd")
        except Exception:  # noqa: BLE001 - de taak moet blijven draaien, ook na een fout
            log.exception("Dagelijkse aanbiedingen-run mislukt")


def start() -> None:
    """Start de achtergrondthread (een keer per proces)."""
    global _thread
    with _lock:
        if _thread is not None or not enabled():
            return
        _thread = threading.Thread(target=_loop, name="offers-schedule", daemon=True)
        _thread.start()
