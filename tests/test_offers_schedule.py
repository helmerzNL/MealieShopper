import asyncio
import logging
import os
import unittest
from unittest import mock

from mealieshopper import ah_orders, image_cache, loose_names, offers, offers_schedule, prices, recipe_nutrition, tasks  # noqa: E402


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
            mock.patch.object(offers_schedule, "_seconds_to_day_change", return_value=24 * 60 * 60.0),  # ver van middernacht
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
            mock.patch.object(recipe_nutrition, "refresh_nightly", self.step("recipes", True)),
            mock.patch.object(offers_schedule, "_record", self.step("record")),
        ):
            await offers_schedule.run_all("manual", wait_for_images=wait_for_images)

    async def test_the_nightly_run_refreshes_the_recipe_cache_and_the_button_does_not(self) -> None:
        # Issue #322: de tijden, tags en voedingswaarden van de recepten zijn elke dag bij, ook als niemand overdag zoekt.
        with mock.patch.object(image_cache, "prefetch_nightly", self.step("nightly")):
            await self.run_all(wait_for_images=True)
        self.assertEqual(self.order.count("recipes"), 1)
        self.order.clear()
        await self.run_all()
        self.assertNotIn("recipes", self.order, "de knop Alles ophalen gaat over de aanbiedingen")

    async def test_a_failing_recipe_round_does_not_fail_the_nightly_run(self) -> None:
        async def broken() -> bool:
            raise RuntimeError("kapot")

        with (
            mock.patch.object(image_cache, "prefetch_nightly", self.step("nightly")),
            mock.patch.object(loose_names, "start", self.loose_start),
            mock.patch.object(offers, "sync_all", self.step("sync", {})),
            mock.patch.object(prices, "warm_catalogues", self.step("catalogues")),
            mock.patch.object(ah_orders, "resolve_receipt_products_nightly", self.step("receipts")),
            mock.patch.object(ah_orders, "backfill_receipt_images_nightly", self.step("images")),
            mock.patch.object(prices, "fill_regular_prices_nightly", self.step("regular")),
            mock.patch.object(recipe_nutrition, "refresh_nightly", broken),
            mock.patch.object(offers_schedule, "_record", self.step("record")),
            self.assertLogs(offers_schedule.log, level="WARNING"),
        ):
            await offers_schedule.run_all("nightly", wait_for_images=True)
        self.assertEqual(self.order[-1], "record")

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

    async def test_nightly_run_warms_again_when_the_round_finishes(self) -> None:
        # 17-11-2026: de ronde voor losse namen schreef om 05:04 tot 05:06 nog 17 koppelingen (product_links), na het opwarmen van
        # de nachtelijke run. Daarmee veranderde de ETag, en de eerste /api/offers van de dag rekende 6,3 s op de aanvraag.
        release = asyncio.Event()

        async def slow_round(trigger: str, now=None, only=None) -> dict:
            await release.wait()
            self.order.append("round")
            return {}

        offers_schedule.set_warmer(self.step("warmer"))
        self.addAsyncCleanup(tasks.cancel_all)
        with mock.patch.object(loose_names, "run_all_stores", slow_round), mock.patch.object(image_cache, "prefetch_nightly", self.step("nightly")):
            await asyncio.wait_for(self.run_all(wait_for_images=True, real_start=True), timeout=2)
            self.assertEqual(self.order.count("warmer"), 1, "de run warmt meteen op, de ronde loopt nog")
            release.set()
            await asyncio.wait_for(asyncio.gather(*tasks.running().values()), timeout=2)
        self.assertEqual(self.order[-2:], ["round", "warmer"], "na de ronde nog een keer opwarmen")

    async def test_a_round_that_finishes_during_the_warm_still_gets_a_second_warm(self) -> None:
        # De ronde schrijft en eindigt terwijl de opwarmer nog rekent: die zag haar schrijfactie dan misschien niet meer.
        release = asyncio.Event()

        async def round_that_ends_during_the_warm(trigger: str, now=None, only=None) -> dict:
            await release.wait()
            self.order.append("round")
            return {}

        async def warmer() -> None:
            self.order.append("warmer")
            if not release.is_set():
                release.set()
                await asyncio.sleep(0.01)  # de ronde eindigt nu, de opwarmer is nog bezig

        offers_schedule.set_warmer(warmer)
        self.addAsyncCleanup(tasks.cancel_all)
        with (
            mock.patch.object(loose_names, "run_all_stores", round_that_ends_during_the_warm),
            mock.patch.object(image_cache, "prefetch_nightly", self.step("nightly")),
        ):
            await asyncio.wait_for(self.run_all(wait_for_images=True, real_start=True), timeout=2)
            await asyncio.wait_for(asyncio.gather(*tasks.running().values()), timeout=2)
        self.assertEqual(self.order[-3:], ["warmer", "round", "warmer"])

    async def test_nightly_run_does_not_warm_twice_without_a_running_round(self) -> None:
        offers_schedule.set_warmer(self.step("warmer"))
        with mock.patch.object(image_cache, "prefetch_nightly", self.step("nightly")):
            await self.run_all(wait_for_images=True)
        await asyncio.gather(*tasks.running().values())
        self.assertEqual(self.order.count("warmer"), 1)

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


class DayChangeTest(unittest.IsolatedAsyncioTestCase):
    """De ETag van /api/offers bevat de dag (lokaal voor de aanbiedingen, UTC voor het oordeel): na middernacht is elke lijst
    ongeldig. De lus warmt daarom op na elke dagwissel, anders rekende de eerste bezoeker tussen middernacht en de nachtelijke
    run (05:00) de lijst op de aanvraag uit (18-11-2026)."""

    def setUp(self) -> None:
        self.warmed: list[str] = []

        async def warmer() -> None:
            self.warmed.append("warmer")

        offers_schedule.set_warmer(warmer)
        self.addCleanup(offers_schedule.set_warmer, None)
        self.catalogues: mock.AsyncMock = mock.AsyncMock()
        patcher = mock.patch.object(prices, "warm_catalogues", self.catalogues)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_next_day_change_is_the_earliest_midnight(self) -> None:
        cet: offers_schedule.timezone = offers_schedule.timezone(offers_schedule.timedelta(hours=1))
        late: offers_schedule.datetime = offers_schedule.datetime(2026, 11, 17, 23, 30, tzinfo=cet)  # 22:30 UTC
        with mock.patch.object(offers_schedule, "_local_now", return_value=late):
            seconds: float = offers_schedule._seconds_to_day_change()
        self.assertAlmostEqual(seconds, 30 * 60 + offers_schedule.DAY_CHANGE_DELAY, delta=1)
        after: offers_schedule.datetime = offers_schedule.datetime(2026, 11, 18, 0, 30, tzinfo=cet)  # 23:30 UTC
        with mock.patch.object(offers_schedule, "_local_now", return_value=after):
            seconds = offers_schedule._seconds_to_day_change()
        self.assertAlmostEqual(seconds, 30 * 60 + offers_schedule.DAY_CHANGE_DELAY, delta=1, msg="nu komt middernacht in UTC eerst")

    async def test_a_new_day_warms_the_catalogues_and_the_offers_once(self) -> None:
        cet: offers_schedule.timezone = offers_schedule.timezone(offers_schedule.timedelta(hours=1))
        before: offers_schedule.datetime = offers_schedule.datetime(2026, 11, 17, 23, 59, tzinfo=cet)
        with mock.patch.object(offers_schedule, "_local_now", return_value=before):
            offers_schedule._remember_day()
            self.assertFalse(await offers_schedule.warm_if_new_day())
        self.assertEqual(self.warmed, [])
        local_midnight: offers_schedule.datetime = offers_schedule.datetime(2026, 11, 18, 0, 0, 10, tzinfo=cet)
        with mock.patch.object(offers_schedule, "_local_now", return_value=local_midnight):
            self.assertTrue(await offers_schedule.warm_if_new_day())
            self.assertFalse(await offers_schedule.warm_if_new_day(), "dezelfde dag: niet nog een keer")
        utc_midnight: offers_schedule.datetime = offers_schedule.datetime(2026, 11, 18, 1, 0, 10, tzinfo=cet)
        with mock.patch.object(offers_schedule, "_local_now", return_value=utc_midnight):
            self.assertTrue(await offers_schedule.warm_if_new_day(), "ook de UTC-dag telt")
        self.assertEqual(self.warmed, ["warmer", "warmer"])
        self.assertEqual(self.catalogues.await_count, 2)

    async def test_the_loop_wakes_at_the_day_change(self) -> None:
        waits: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            waits.append(seconds)
            if len(waits) == 2:
                raise asyncio.CancelledError

        with (
            mock.patch.object(offers_schedule, "run_if_due", mock.AsyncMock(return_value=False)),
            mock.patch.object(offers_schedule, "_seconds_to_day_change", return_value=120.0),
            mock.patch.object(offers_schedule.asyncio, "sleep", fake_sleep),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await offers_schedule._loop()
        self.assertEqual(waits, [offers_schedule.FIRST_CHECK_SECONDS, 120.0])
