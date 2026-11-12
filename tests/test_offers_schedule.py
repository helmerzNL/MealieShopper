import asyncio
import logging
import os
import unittest
from unittest import mock

from mealieshopper import ah_orders, image_cache, loose_names, offers, offers_schedule, prices, tasks  # noqa: E402


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
        with (
            mock.patch.object(offers_schedule, "run_if_due", due),
            mock.patch.object(offers_schedule.asyncio, "sleep", fake_sleep),
            self.assertLogs(offers_schedule.log, logging.INFO) as logs,
        ):
            with self.assertRaises(asyncio.CancelledError):
                await offers_schedule._loop()
        self.assertEqual(waits, [offers_schedule.FIRST_CHECK_SECONDS] + [offers_schedule.CHECK_SECONDS] * 3)
        self.assertEqual(due.await_count, 3, "een fout in een ronde stopt de lus niet")
        messages: list[str] = [record.getMessage() for record in logs.records]
        self.assertIn("Aanbiedingen van alle winkels vernieuwd", messages)
        self.assertIn("Dagelijkse aanbiedingen-run mislukt", messages)
        self.assertEqual([r.levelno for r in logs.records if r.exc_info], [logging.ERROR])


class WarmerTest(unittest.IsolatedAsyncioTestCase):
    """Na de synchronisatie rekent de app het oordeel van /api/offers alvast uit, via een opwarmer die create_app registreert."""

    def setUp(self) -> None:
        self.order: list[str] = []
        self.addCleanup(offers_schedule.set_warmer, None)
        self.loose_start: mock.MagicMock = mock.MagicMock(return_value=True)

    def step(self, name: str, value=None):
        async def run(*args, **kwargs):
            self.order.append(name)
            return value

        return run

    async def run_all(self, wait_for_images: bool = False, real_start: bool = False) -> None:
        with (
            mock.patch.object(loose_names, "start", loose_names.start if real_start else self.loose_start),
            mock.patch.object(offers, "sync_all", self.step("sync", {})),
            mock.patch.object(prices, "warm_catalogues", self.step("catalogues")),
            mock.patch.object(ah_orders, "resolve_receipt_products_nightly", self.step("receipts")),
            mock.patch.object(ah_orders, "backfill_receipt_images_nightly", self.step("images")),
            mock.patch.object(prices, "fill_regular_prices_nightly", self.step("regular")),
            mock.patch.object(image_cache, "prefetch_async", self.step("prefetch")),
            mock.patch.object(offers_schedule, "_record", self.step("record")),
        ):
            await offers_schedule.run_all("manual", wait_for_images=wait_for_images)

    async def test_run_all_calls_the_registered_warmer_last(self) -> None:
        offers_schedule.set_warmer(self.step("warmer"))
        with mock.patch.object(image_cache, "prefetch_nightly", self.step("nightly")):
            await self.run_all(wait_for_images=True)
        self.assertEqual(self.order[-1], "warmer")
        self.assertEqual(self.order.count("warmer"), 1)
        self.assertLess(self.order.index("regular"), self.order.index("warmer"), "na de extraatjes die de ETag veranderen")

    async def test_run_all_without_a_warmer_runs_as_before(self) -> None:
        offers_schedule.set_warmer(None)
        await self.run_all()
        self.assertNotIn("warmer", self.order)
        self.assertEqual(self.order[-1], "record")

    async def test_the_button_does_not_wait_for_the_warmer(self) -> None:
        # De knop Alles ophalen (wait_for_images=False) geeft antwoord zodra de uitkomst vastligt: de opwarmer draait in een eigen task.
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_warmer() -> None:
            started.set()
            await release.wait()
            self.order.append("warmer")

        offers_schedule.set_warmer(slow_warmer)
        await asyncio.wait_for(self.run_all(), timeout=2)
        self.assertEqual(self.order[-1], "record", "run_all is klaar terwijl de opwarmer nog loopt")
        await asyncio.wait_for(started.wait(), timeout=2)
        release.set()
        await asyncio.sleep(0)
        self.assertEqual(self.order[-1], "warmer")

    async def test_a_failing_warmer_does_not_fail_the_run(self) -> None:
        async def broken() -> None:
            raise RuntimeError("kapot")

        offers_schedule.set_warmer(broken)
        with self.assertLogs(offers_schedule.log, level="WARNING"), mock.patch.object(image_cache, "prefetch_nightly", self.step("nightly")):
            await self.run_all(wait_for_images=True)
        self.assertEqual(self.order[-1], "record")

    async def test_nightly_run_starts_the_round_without_waiting_for_it(self) -> None:
        release = asyncio.Event()
        seen: list[tuple[str | None, str]] = []

        async def slow_round(trigger: str, now=None, only=None) -> dict:
            seen.append((only, trigger))
            await release.wait()
            return {}

        self.addAsyncCleanup(tasks.cancel_all)
        with mock.patch.object(loose_names, "run_all_stores", slow_round), mock.patch.object(image_cache, "prefetch_nightly", self.step("nightly")):
            await asyncio.wait_for(self.run_all(wait_for_images=True, real_start=True), timeout=2)
            self.assertTrue(loose_names.running(), "de ronde loopt nog, de nachtelijke run is al klaar")
            self.assertEqual(self.order[-1], "record")
            await asyncio.sleep(0)
            self.assertEqual(seen, [(None, "nightly")])
            release.set()
            await asyncio.gather(*tasks.running().values())

    async def test_nightly_run_starts_the_round_after_the_extras(self) -> None:
        self.loose_start.side_effect = lambda *args: self.order.append("loose") or True
        with mock.patch.object(image_cache, "prefetch_nightly", self.step("nightly")):
            await self.run_all(wait_for_images=True)
        self.loose_start.assert_called_once_with(None, "nightly")
        self.assertGreater(self.order.index("loose"), self.order.index("regular"))

    async def test_button_sync_does_not_start_the_round(self) -> None:
        await self.run_all(wait_for_images=False)
        self.loose_start.assert_not_called()

    async def test_a_failing_start_does_not_fail_the_nightly_run(self) -> None:
        self.loose_start.side_effect = RuntimeError("kapot")
        with self.assertLogs(offers_schedule.log, level="WARNING") as logs, mock.patch.object(image_cache, "prefetch_nightly", self.step("nightly")):
            await self.run_all(wait_for_images=True)
        self.assertEqual(self.order[-1], "record")
        self.assertIn("nightly", self.order, "de foto's na de start gingen door")
        self.assertTrue(any("losse namen" in record.getMessage() for record in logs.records))
