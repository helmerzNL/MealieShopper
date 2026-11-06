import asyncio
import logging
import os
import unittest
from unittest import mock

from mealieshopper import offers_schedule, tasks  # noqa: E402


class StartTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        patcher = mock.patch.dict(os.environ, {"MEALIESHOPPER_OFFERS_SCHEDULE": "true"})
        patcher.start()
        self.addCleanup(patcher.stop)
        offers_schedule._task = None
        self.addCleanup(setattr, offers_schedule, "_task", None)

    async def asyncTearDown(self) -> None:
        await tasks.cancel_all()

    async def test_start_spawns_one_task_only_once(self) -> None:
        async def idle() -> None:
            await asyncio.Event().wait()

        with mock.patch.object(offers_schedule, "_loop", idle):
            offers_schedule.start()
            first: asyncio.Task[None] | None = offers_schedule._task
            offers_schedule.start()
        self.assertIsNotNone(first)
        self.assertIs(offers_schedule._task, first)
        self.assertEqual(list(tasks.running()), ["offers-schedule"])
        await tasks.cancel_all()
        with mock.patch.object(offers_schedule, "_loop", idle):
            offers_schedule.start()
        self.assertIsNot(offers_schedule._task, first, "na het annuleren start hij opnieuw")
        self.assertEqual(list(tasks.running()), ["offers-schedule"])

    async def test_disabled_by_environment_does_not_start(self) -> None:
        with mock.patch.dict(os.environ, {"MEALIESHOPPER_OFFERS_SCHEDULE": "false"}):
            offers_schedule.start()
        self.assertIsNone(offers_schedule._task)
        self.assertEqual(tasks.running(), {})

    async def test_loop_runs_when_due_and_logs_failures(self) -> None:
        waits: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            waits.append(seconds)
            if len(waits) == 4:
                raise asyncio.CancelledError

        due = mock.AsyncMock(side_effect=[True, RuntimeError("stuk"), False])
        with mock.patch.object(offers_schedule, "run_if_due", due), \
             mock.patch.object(offers_schedule.asyncio, "sleep", fake_sleep), \
             self.assertLogs(offers_schedule.log, logging.INFO) as logs:
            with self.assertRaises(asyncio.CancelledError):
                await offers_schedule._loop()
        self.assertEqual(waits, [offers_schedule.FIRST_CHECK_SECONDS] + [offers_schedule.CHECK_SECONDS] * 3)
        self.assertEqual(due.await_count, 3, "een fout in een ronde stopt de lus niet")
        messages: list[str] = [record.getMessage() for record in logs.records]
        self.assertIn("Aanbiedingen van alle winkels vernieuwd", messages)
        self.assertIn("Dagelijkse aanbiedingen-run mislukt", messages)
        self.assertEqual([r.levelno for r in logs.records if r.exc_info], [logging.ERROR])
