"""De weekprijzen van AH-producten die je kocht, van ahprijsindex.nl (`ah_price_index.py`)."""

import asyncio
import json
import os
import unittest
from contextlib import closing
from pathlib import Path
from typing import Any
from unittest import mock

import httpx
from helpers import db, in_db, served  # helpers zet ook het pad naar de app
from test_prices import PricesCase, deal, offer, order_item

from mealieshopper import ah_orders, ah_price_index, image_cache, loose_names, offers, offers_schedule, prices, tasks

FIXTURE: Path = (
    Path(__file__).resolve().parent / "fixtures" / "ahprijsindex-product-446406.html"
)
TODAY: str = "2026-11-13"
NOW: str = f"{TODAY}T05:30:00+00:00"


def page(
    product_id: int = 446406,
    weeks: tuple[tuple[int, int, str, bool], ...] = ((2026, 46, "2.39", False),),
    **changes: Any,
) -> str:
    """Een productpagina in de vorm van de site, met `weeks` als (jaar, week, prijs, geschat)."""
    props: dict[str, Any] = {
        "productInfo": {
            "product_id": product_id,
            "product_title": "AH Les pains boulogne heel",
            "product_brand": "AH",
            "product_imgurl": "https://static.ah.nl/dam/product/x?rendition=200x200_WEBP",
            "product_size": "per stuk",
        },
        "prices": [
            {
                "price": price,
                "product_id": product_id,
                "isoweek": week,
                "isoyear": year,
                "is_interpolated": guessed,
            }
            for year, week, price, guessed in weeks
        ],
        "discounts": [],
        "sizeHistory": [],
        **changes,
    }
    data: str = json.dumps({"props": {"pageProps": props}, "page": "/product/[prodId]"})
    return f'<html><body><script id="__NEXT_DATA__" type="application/json">{data}</script></body></html>'


def html(text: str, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status, text=text, headers={"Content-Type": "text/html; charset=utf-8"}
    )


class ParseTest(unittest.TestCase):
    def test_a_recorded_page_gives_every_week_on_its_monday(self) -> None:
        series: ah_price_index.Series = ah_price_index.parse(
            FIXTURE.read_text(encoding="utf-8")
        )
        self.assertEqual(
            (series.product_id, series.title, series.brand, series.unit_size),
            ("446406", "AH Les pains boulogne heel", "AH", "per stuk"),
        )
        self.assertTrue(series.image.startswith("https://static.ah.nl/dam/product/"))
        self.assertEqual(len(series.weeks), 225)
        self.assertEqual(
            series.weeks[0], ("2022-07-25", 2.39)
        )  # week 30 van 2022, de oudste eerst
        self.assertEqual(series.weeks[-1], ("2026-11-09", 2.39))  # week 46 van 2026
        self.assertIn(
            ("2023-10-09", 1.99), series.weeks
        )  # week 41 van 2023: de prijs ging omlaag

    def test_a_guessed_week_and_a_week_without_a_price_are_left_out(self) -> None:
        series: ah_price_index.Series = ah_price_index.parse(
            page(
                weeks=(
                    (2026, 44, "2.39", False),
                    (2026, 45, "2.39", True),
                    (2026, 46, "0", False),
                    (2026, 47, "n/a", False),
                    (2026, 99, "1.00", False),
                )
            )
        )
        self.assertEqual(series.weeks, (("2026-10-26", 2.39),))

    def test_a_product_that_changed_size_has_no_size_on_its_prices(self) -> None:
        # De prijs van een oudere week hoort dan bij een andere inhoud dan die de pagina nu noemt.
        series: ah_price_index.Series = ah_price_index.parse(
            page(sizeHistory=[{"isoweek": 12, "isoyear": 2025}])
        )
        self.assertIsNone(series.unit_size)

    def test_a_page_in_another_shape_is_an_error(self) -> None:
        for text in (
            "<html><body>Onderhoud</body></html>",
            page(prices="geen lijst"),
            page(productInfo={}),
            '<script id="__NEXT_DATA__" type="application/json">{kapot</script>',
        ):
            with self.assertRaises(ah_price_index.PageError):
                ah_price_index.parse(text)


class RoundCase(PricesCase):
    def setUp(self) -> None:
        super().setUp()
        patcher = mock.patch.dict(os.environ, {"MEALIESHOPPER_PRICE_INDEX": "true"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.waits: list[float] = []

        async def no_wait(seconds: float) -> None:
            self.waits.append(seconds)

        sleeper = mock.patch.object(ah_price_index, "_sleep", no_wait)
        sleeper.start()
        self.addCleanup(sleeper.stop)

    async def buy(self, *product_ids: int, day: str = "2026-11-02") -> None:
        for product_id in product_ids:
            await self.ah_order(
                1000 + product_id,
                "delivered",
                day,
                order_item(product_id, f"AH Product {product_id}", 2.39),
            )

    def observations(self, product_id: str = "446406") -> list[tuple[str, float, str]]:
        with closing(db(prices)) as conn:
            return [
                tuple(row)
                for row in conn.execute(
                    "SELECT day, price, source FROM price_observations WHERE store = 'ah' AND product_id = ? ORDER BY day",
                    (product_id,),
                )
            ]

    def state(self) -> dict[str, tuple[str, str]]:
        with closing(db(ah_price_index)) as conn:
            return {
                row[0]: (row[1], row[2])
                for row in conn.execute(
                    "SELECT product_id, status, checked_at FROM ah_price_index"
                )
            }


class RoundTest(RoundCase):
    async def test_a_bought_product_gets_its_weekly_prices(self) -> None:
        await self.buy(446406)
        with served(html(FIXTURE.read_text(encoding="utf-8"))) as route:
            result: dict[str, Any] | None = await ah_price_index.run_round(now=NOW)
        self.assertEqual(
            result,
            {
                "checked": 1,
                "prices": 225,
                "missing": 0,
                "failed": 0,
                "stopped": None,
                "left": 0,
            },
        )
        self.assertEqual(route.call_count, 1)
        request: httpx.Request = route.calls[0].request
        self.assertEqual(str(request.url), "https://www.ahprijsindex.nl/product/446406")
        self.assertTrue(
            request.headers["User-Agent"].startswith("MealieShopper/"),
            request.headers["User-Agent"],
        )
        found: list[tuple[str, float, str]] = self.observations()
        self.assertEqual(len(found), 225)
        self.assertEqual(found[-1], ("2026-11-09", 2.39, "ahprijsindex"))
        self.assertEqual(self.state(), {"446406": ("ok", NOW)})

    async def test_only_products_with_a_number_of_the_webshop_are_asked(self) -> None:
        self.ah_receipt(
            "r1", "2026-11-02T10:00:00", (0, "AH LOSSE NAAM", 1, 1.0, 1.0)
        )  # een bonregel zonder product
        with served(html(page())) as route:
            self.assertIsNone(await ah_price_index.run_round(now=NOW))
        self.assertEqual(route.call_count, 0)

    async def test_a_product_is_not_asked_again_within_two_weeks(self) -> None:
        await self.buy(446406)
        with served(html(page())) as route:
            await ah_price_index.run_round(now=NOW)
            self.assertIsNone(
                await ah_price_index.run_round(now="2026-11-26T05:30:00+00:00")
            )  # 13 dagen later
            self.assertEqual(route.call_count, 1)
            again: dict[str, Any] | None = await ah_price_index.run_round(
                now="2026-11-27T05:30:00+00:00"
            )
        self.assertEqual(route.call_count, 2)
        self.assertEqual(again["checked"], 1)

    async def test_the_site_gets_ten_seconds_between_two_requests(self) -> None:
        await self.buy(1, 2, 3)
        with served(side_effect=[html(page(1)), html(page(2)), html(page(3))]):
            await ah_price_index.run_round(now=NOW)
        self.assertEqual(
            self.waits, [10.0, 10.0]
        )  # tussen de verzoeken, niet voor het eerste of na het laatste

    async def test_a_price_the_app_measured_itself_stays(self) -> None:
        await self.buy(446406)
        await prices.record(
            "ah",
            [{"id": "446406", "title": "AH Les pains boulogne heel", "price": 2.49}],
            now="2026-11-09T08:00:00+00:00",
        )
        with served(
            html(page(weeks=((2026, 45, "2.39", False), (2026, 46, "2.39", False))))
        ):
            await ah_price_index.run_round(now=NOW)
        self.assertEqual(
            self.observations(),
            [("2026-11-02", 2.39, "ahprijsindex"), ("2026-11-09", 2.49, "search")],
        )

    async def test_a_corrected_week_replaces_the_earlier_price_of_the_index(
        self,
    ) -> None:
        await self.buy(446406)
        with served(
            side_effect=[
                html(page(weeks=((2026, 46, "2.39", False),))),
                html(page(weeks=((2026, 46, "2.29", False),))),
            ]
        ):
            await ah_price_index.run_round(now=NOW)
            await ah_price_index.run_round(now="2026-11-28T05:30:00+00:00")
        self.assertEqual(self.observations(), [("2026-11-09", 2.29, "ahprijsindex")])

    async def test_a_week_that_has_not_started_is_not_saved(self) -> None:
        await self.buy(446406)
        with served(
            html(page(weeks=((2026, 46, "2.39", False), (2026, 47, "2.59", False))))
        ):
            result: dict[str, Any] | None = await ah_price_index.run_round(now=NOW)
        self.assertEqual(result["prices"], 1)
        self.assertEqual(self.observations(), [("2026-11-09", 2.39, "ahprijsindex")])

    async def test_a_product_the_site_does_not_know_waits_a_month(self) -> None:
        await self.buy(7)
        with served(html("niet gevonden", 404)) as route:
            first: dict[str, Any] | None = await ah_price_index.run_round(now=NOW)
            self.assertIsNone(
                await ah_price_index.run_round(now="2026-12-12T05:30:00+00:00")
            )  # 29 dagen later
            await ah_price_index.run_round(now="2026-12-13T05:30:00+00:00")
        self.assertEqual(
            (first["checked"], first["missing"], first["prices"]), (1, 1, 0)
        )
        self.assertEqual(route.call_count, 2)
        self.assertEqual(self.state()["7"][0], "missing")
        self.assertEqual(self.observations("7"), [])

    async def test_a_refusal_stops_the_round_and_keeps_the_rest_for_later(self) -> None:
        await self.buy(1, 2, 3)
        for status in (429, 403, 503):
            with (
                self.subTest(status=status),
                served(
                    side_effect=[html(page(1)), html("te druk", status), html(page(3))]
                ) as route,
            ):
                with closing(db(ah_price_index)) as conn, conn:
                    conn.execute("DELETE FROM ah_price_index")
                with self.assertLogs(ah_price_index.log, "WARNING"):
                    result: dict[str, Any] | None = await ah_price_index.run_round(
                        now=NOW
                    )
                self.assertEqual(
                    route.call_count, 2, "na de weigering geen verzoek meer"
                )
                self.assertEqual(
                    (result["checked"], result["stopped"], result["left"]),
                    (1, f"HTTP {status}", 2),
                )
                self.assertEqual(sorted(self.state()), ["1"])

    async def test_a_network_error_stops_the_round(self) -> None:
        await self.buy(1, 2)
        with (
            served(side_effect=httpx.ConnectError("geen verbinding")) as route,
            self.assertLogs(ah_price_index.log, "WARNING"),
        ):
            result: dict[str, Any] | None = await ah_price_index.run_round(now=NOW)
        self.assertEqual(route.call_count, 1)
        self.assertEqual(
            (result["checked"], result["stopped"], result["left"]),
            (0, "ConnectError", 2),
        )
        self.assertEqual(self.state(), {})

    async def test_a_page_in_another_shape_is_skipped_and_three_in_a_row_stop_the_round(
        self,
    ) -> None:
        await self.buy(1, 2, 3, 4, 5)
        with (
            served(html("<html>nieuw ontwerp</html>")) as route,
            self.assertLogs(ah_price_index.log, "WARNING"),
        ):
            result: dict[str, Any] | None = await ah_price_index.run_round(now=NOW)
        self.assertEqual(route.call_count, 3)
        self.assertEqual(
            (result["checked"], result["failed"], result["stopped"], result["left"]),
            (3, 3, "page", 2),
        )
        self.assertEqual({status for status, _ in self.state().values()}, {"error"})
        # De volgende nacht: eerst wat nog nooit is gevraagd, de mislukte daarna opnieuw.
        with served(
            side_effect=lambda request: html(
                page(int(request.url.path.rsplit("/", 1)[1]))
            )
        ) as route:
            await ah_price_index.run_round(now="2026-11-14T05:30:00+00:00")
        self.assertEqual(route.call_count, 5)

    async def test_a_round_asks_no_more_than_its_limit_and_the_oldest_first(
        self,
    ) -> None:
        await self.buy(1, 2, 3)
        with served(
            side_effect=lambda request: html(
                page(int(request.url.path.rsplit("/", 1)[1]))
            )
        ) as route:
            first: dict[str, Any] | None = await ah_price_index.run_round(
                limit=2, now=NOW
            )
            second: dict[str, Any] | None = await ah_price_index.run_round(
                limit=2, now=NOW
            )
        self.assertEqual((first["checked"], first["left"]), (2, 1))
        self.assertEqual((second["checked"], second["left"]), (1, 0))
        self.assertEqual(
            [call.request.url.path for call in route.calls],
            ["/product/1", "/product/2", "/product/3"],
        )

    async def test_a_product_in_this_weeks_offers_goes_first(self) -> None:
        await self.buy(1, 2, 3)
        await self.sync_offer(NOW, offer("3", "AH Product 3", 1.20, 2.39, "1+1 gratis"))
        with served(
            side_effect=lambda request: html(
                page(int(request.url.path.rsplit("/", 1)[1]))
            )
        ) as route:
            await ah_price_index.run_round(limit=1, now=NOW)
        self.assertEqual(
            [call.request.url.path for call in route.calls], ["/product/3"]
        )

    async def test_switched_off_it_asks_nothing(self) -> None:
        await self.buy(446406)
        with (
            mock.patch.dict(os.environ, {"MEALIESHOPPER_PRICE_INDEX": "false"}),
            served(html(page())) as route,
        ):
            self.assertIsNone(await ah_price_index.run_round(now=NOW))
            self.assertFalse(ah_price_index.start())
        self.assertEqual(route.call_count, 0)


class VerdictTest(RoundCase):
    async def test_the_offer_of_a_bought_product_is_judged_on_the_price_of_the_index(
        self,
    ) -> None:
        await self.buy(
            446406, day="2025-06-02"
        )  # langer dan een jaar geleden: te oud voor een oordeel
        before: dict[str, Any] = (
            await prices.annotate(
                "ah",
                [
                    deal(
                        "446406", "AH Les pains boulogne heel", 1.20, 2.99, "nu 1,20"
                    )
                ],
                today=TODAY,
            )
        )[0]
        self.assertEqual(before["badge"]["source"], "store")
        with served(html(FIXTURE.read_text(encoding="utf-8"))):
            await ah_price_index.run_round(now=NOW)
        after: dict[str, Any] = (
            await prices.annotate(
                "ah",
                [
                    deal(
                        "446406", "AH Les pains boulogne heel", 1.20, 2.99, "nu 1,20"
                    )
                ],
                today=TODAY,
            )
        )[0]
        self.assertEqual(after["badge"]["source"], "history")
        self.assertEqual(
            after["badge"]["regular"],
            {
                "price": 2.39,
                "day": "2026-11-09",
                "source": "search",
                "judged": True,
                "fresh": True,
            },
        )
        self.assertTrue(
            after["badge"]["inflated"], "de winkel noemt 2,99, de gewone prijs is 2,39"
        )
        self.assertEqual(after["verdict"]["percent"], 50)

    async def test_the_history_shows_the_regular_price_through_the_years(self) -> None:
        await self.buy(446406)
        with served(html(FIXTURE.read_text(encoding="utf-8"))):
            await ah_price_index.run_round(now=NOW)
        product: dict[str, Any] = (
            await prices.history("ah", "", 50, today=TODAY, product="446406")
        )["products"][0]
        self.assertEqual(product["regular"][0]["from"], "2022-07-25")
        self.assertEqual(
            (
                product["regularPrice"],
                product["lowestRegular"],
                product["highestRegular"],
            ),
            (2.39, 1.99, 2.79),
        )

    async def test_the_ladder_of_lowest_prices_does_not_count_the_index(self) -> None:
        # De index kent alleen gewone prijzen. Telden die mee als dekking, dan werd een actieprijs "laagste ooit" zonder dat de
        # app de acties van die jaren kent.
        await self.buy(446406)
        with served(html(FIXTURE.read_text(encoding="utf-8"))):
            await ah_price_index.run_round(now=NOW)

        async def points(conn: Any) -> dict[str, list[tuple[str, float, str]]]:
            receipts: list[dict[str, Any]] = await prices._receipt_rows(conn, "ah", TODAY)
            return await prices._history_points(conn, "ah", TODAY, receipts, ("446406",))

        found: dict[str, list[tuple[str, float, str]]] = (
            await in_db(prices._connect, points)
        )[0]
        # Alleen de aankoop.
        self.assertEqual(found["446406"], [("2026-11-02", 2.39, "purchase")])


class StartTest(RoundCase):
    async def asyncTearDown(self) -> None:
        await tasks.cancel_all()

    async def test_start_runs_one_round_at_a_time(self) -> None:
        release: asyncio.Event = asyncio.Event()

        async def waiting(**_kwargs: Any) -> None:
            await release.wait()

        with mock.patch.object(ah_price_index, "run_round", waiting):
            self.assertTrue(ah_price_index.start())
            self.assertFalse(ah_price_index.start(), "er loopt er al een")
            self.assertEqual(list(tasks.running()), [ah_price_index.TASK_NAME])
            release.set()
            await asyncio.gather(*tasks.running().values())
            self.assertTrue(ah_price_index.start(), "na afloop start hij opnieuw")
            await asyncio.gather(*tasks.running().values())


class NightlyTest(unittest.IsolatedAsyncioTestCase):
    """De dagelijkse run start de ronde in een eigen task, de knop onder Koppelen niet."""

    async def run_all(self, started: mock.MagicMock, wait_for_images: bool) -> None:
        async def nothing(*_args: Any, **_kwargs: Any) -> None:
            return None

        async def synced() -> dict[str, Any]:
            return {}

        with (
            mock.patch.object(ah_price_index, "start", started),
            mock.patch.object(loose_names, "start", mock.MagicMock(return_value=True)),
            mock.patch.object(offers, "sync_all", synced),
            mock.patch.object(offers_schedule.purchase_sync, "sync_nightly", nothing),
            mock.patch.object(prices, "warm_catalogues", nothing),
            mock.patch.object(ah_orders, "resolve_receipt_products_nightly", nothing),
            mock.patch.object(ah_orders, "backfill_receipt_images_nightly", nothing),
            mock.patch.object(prices, "fill_regular_prices_nightly", nothing),
            mock.patch.object(image_cache, "prefetch_nightly", nothing),
            mock.patch.object(image_cache, "prefetch_async", nothing),
            mock.patch.object(offers_schedule, "_record", nothing),
        ):
            await offers_schedule.run_all("scheduled", wait_for_images=wait_for_images)

    async def test_the_nightly_run_starts_the_round(self) -> None:
        started: mock.MagicMock = mock.MagicMock(return_value=True)
        await self.run_all(started, wait_for_images=True)
        started.assert_called_once_with()

    async def test_the_button_does_not_start_the_round(self) -> None:
        started: mock.MagicMock = mock.MagicMock(return_value=True)
        await self.run_all(started, wait_for_images=False)
        started.assert_not_called()

    async def test_a_round_that_does_not_start_leaves_the_nightly_run_alone(self) -> None:
        started: mock.MagicMock = mock.MagicMock(side_effect=RuntimeError("kapot"))
        with self.assertLogs(offers_schedule.log, "WARNING") as logs:
            await self.run_all(started, wait_for_images=True)
        self.assertIn("Ronde langs de prijsindex starten mislukt", logs.output[0])
