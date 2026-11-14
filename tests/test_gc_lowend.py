"""Het terugzetten van een back-up en de rondes van de GC (taak C, trage apparaten).

Een volle ronde van de GC loopt langs elk object dat de GC volgt, met de GIL vast, dus ook de event loop wacht. Gemeten op de
volumedatabase (Python 3.15, 2026-11-14): de rijen van het bestand als `(nummer, (kolommen, waarden))` volgde de GC bij het
aanmaken, en een keer terugzetten startte zo 24 tot 26 volle rondes van 30 tot 56 ms, in een proces met alleen de app. Met de
rijen in `backup.Rows` (de waarden als tuple uit een uitgepakte reeks, die de GC niet volgt) geen een. Daarnaast liet de loop het
Plan van `start_job` aan het eind van de coroutine in een keer los: 45 ms in `list_dealloc`. Nu gaat dat in stukken, in een thread.
"""

from __future__ import annotations

import asyncio
import gc
import json
import sqlite3
import sys
import threading
from contextlib import closing
from pathlib import Path
from typing import Any
from unittest import mock

from test_backup import BackupCase

from mealieshopper import backup, gc_watch

HISTORY_ROWS: int = 3_000  # rijen in offer_history, genoeg om een verschil per rij te zien
ADDED_LIMIT: int = 500  # wat het lezen en controleren hooguit aan gevolgde objecten overhoudt, los van het aantal rijen


def _history(conn: sqlite3.Connection, count: int) -> None:
    columns: list[str] = [row[1] for row in conn.execute('PRAGMA table_info("offer_history")')]
    rows: list[tuple[Any, ...]] = []
    for number in range(count):
        values: dict[str, Any] = {name: (number if name == "id" else 1.25 if name.startswith("price") else f"{name} {number} é") for name in columns}
        rows.append(tuple(values[name] for name in columns))
    marks: str = ", ".join("?" for _ in columns)
    conn.executemany(f'INSERT INTO "offer_history" ({", ".join(chr(34) + name + chr(34) for name in columns)}) VALUES ({marks})', rows)


class _Pieces(list[Any]):
    """Een lijst die telt hoeveel rijen elke `del` loslaat."""

    def __init__(self, items: list[Any]) -> None:
        super().__init__(items)
        self.deleted: list[int] = []

    def __delitem__(self, index: Any) -> None:
        before: int = len(self)
        super().__delitem__(index)
        self.deleted.append(before - len(self))


class RowsTest(BackupCase):
    def _backup(self) -> Path:
        with closing(self.connect()) as conn, conn:
            _history(conn, HISTORY_ROWS)
        path: Path = self.data / "gc.json.gz"
        backup.write_backup(path, links=False, box=None)
        return path

    def test_reading_and_checking_a_backup_adds_nothing_per_row_that_the_gc_follows(self) -> None:
        # Met de GC uit ruimt geen ronde iets op en laat geen ronde een tuple los: het verschil is wat er gevolgd bleef.
        path: Path = self._backup()
        enabled: bool = gc.isenabled()
        gc.collect()
        gc.disable()
        try:
            before: int = len(gc.get_objects())
            plan: backup.Plan = backup.read_plan(path)
            prepared: dict[str, backup.Rows] = backup.check_rows(plan)
            added: int = len(gc.get_objects()) - before
        finally:
            if enabled:
                gc.enable()
        self.assertEqual(len(plan.tables["offer_history"]), HISTORY_ROWS)
        self.assertEqual(len(prepared["offer_history"]), HISTORY_ROWS)
        self.assertLess(added, ADDED_LIMIT)  # voorheen drie tuples per rij in het Plan en drie in `prepared`
        values: tuple[Any, ...] = prepared["offer_history"].values[0]
        self.assertFalse(gc.is_tracked(values))

    def test_release_lets_go_in_pieces(self) -> None:
        rows: backup.Rows = backup.Rows()
        for number in range(25):
            rows.append({"id": number, "title": f"rij {number}"})
        rows.values = _Pieces(rows.values)
        with mock.patch.object(backup, "RELEASE_PIECE", 7):
            rows.release()
        self.assertEqual(len(rows), 0)
        self.assertEqual(rows.names, [])
        self.assertEqual(rows.values.deleted, [7, 7, 7, 4])

    def test_a_plan_refuses_a_row_that_is_not_an_object(self) -> None:
        for rows in ([{"id": 1}, [2]], [3], "geen lijst"):
            with self.subTest(rows=rows), self.assertRaises(backup.BackupError) as caught:
                backup.prepare({"format": backup.FORMAT, "tables": {"offer_history": rows}})
            self.assertEqual(caught.exception.params, {"detail": "offer_history"})

    def test_the_rows_keep_their_order_numbers_and_shared_columns(self) -> None:
        plan: backup.Plan = backup.prepare(
            {"format": backup.FORMAT, "tables": {"offers_meta": [{"store": "ah", "synced_at": "nu"}, {"synced_at": "toen", "store": "jumbo"}]}}
        )
        rows: backup.Rows = plan.tables["offers_meta"]
        self.assertEqual(
            list(rows.numbered()),
            [(1, ("store", "synced_at"), ("ah", "nu")), (2, ("synced_at", "store"), ("toen", "jumbo"))],
        )
        again: backup.Plan = backup.prepare({"format": backup.FORMAT, "tables": {"offers_meta": [{"store": "x", "synced_at": "y"}] * 2}})
        first, second = again.tables["offers_meta"].names
        self.assertIs(first, second)  # een tuple van kolommen per vorm, niet per rij


def test_a_full_collection_says_how_many_objects_it_walked() -> None:
    # Wat een volle ronde kost, volgt uit het aantal objecten: in de melding van een stilstand is zo te zien of de app groeide.
    seen: list[gc_watch.Collection] = []
    watch: gc_watch.GcWatch = gc_watch.GcWatch(keep=0.0, on_collection=seen.append)
    watch.start()
    try:
        gc.collect()
    finally:
        watch.stop()
    full: gc_watch.Collection = [found for found in seen if found.generation == 2][-1]
    if sys.version_info < (3, 15):  # pragma: no cover - Python geeft het aantal pas vanaf 3.15
        assert full.candidates == 0
        return
    assert full.candidates > 1000
    assert full.describe().startswith("GC generatie 2 ")
    assert full.describe().endswith(" ms in MainThread, " + f"{full.candidates:,}".replace(",", ".") + " objecten")
    assert gc_watch.Collection(2, 0.05, "MainThread", 0, 166_518).describe() == "GC generatie 2 50 ms in MainThread, 166.518 objecten"
    assert gc_watch.Collection(2, 0.05, "MainThread", 0).describe() == "GC generatie 2 50 ms in MainThread"


class ReleaseTest(BackupCase):
    async def test_every_phase_releases_its_rows_in_a_thread(self) -> None:
        # Het klaarzetten (Plan en `prepared`), `start_job` (Plan) en de klus (Plan en `prepared`): vijf keer elke rij, en nooit op
        # de loop. Liet de loop het Plan van `start_job` los, dan was dat een lange aanroep in C op de loop zelf.
        with closing(self.connect()) as conn, conn:
            _history(conn, 200)
        loop_thread: int = threading.get_ident()
        released: list[tuple[int, int]] = []
        original: Any = backup.Rows.release

        def spy(rows: backup.Rows) -> None:
            released.append((threading.get_ident(), len(rows)))
            original(rows)

        response, data = await self.make()
        self.assertEqual(response.status_code, 200)
        total: int = sum(len(rows) for rows in json.loads(self.unpacked(data))["tables"].values())
        self.assertGreaterEqual(total, 200)
        with mock.patch.object(backup.Rows, "release", spy):
            job: dict[str, Any] = await self.restore(data)
            await asyncio.sleep(0)
        self.assertEqual(job["status"], "done", job)
        self.assertEqual(sum(count for _, count in released), 5 * total)
        self.assertNotIn(loop_thread, {ident for ident, count in released if count})
