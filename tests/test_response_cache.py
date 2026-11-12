"""De cache van een lijst per ETag (response_cache.py): bewaren, vernieuwen, gelijktijdige aanvragen samen, geen fouten bewaren."""

import asyncio
import unittest

from mealieshopper.response_cache import ResponseCache


class ResponseCacheTest(unittest.IsolatedAsyncioTestCase):
    async def test_the_same_etag_gives_the_kept_value_and_a_new_etag_loads_again(
        self,
    ) -> None:
        cache: ResponseCache = ResponseCache()
        calls: list[str] = []

        async def load(value: str) -> str:
            calls.append(value)
            return value

        self.assertEqual(await cache.get("ah", "e1", lambda: load("a")), "a")
        self.assertEqual(await cache.get("ah", "e1", lambda: load("b")), "a")
        self.assertEqual(await cache.get("ah", "e2", lambda: load("c")), "c")
        self.assertEqual(
            await cache.get("ah", "e1", lambda: load("d")),
            "d",
            "alleen de laatste ETag per sleutel blijft",
        )
        self.assertEqual(calls, ["a", "c", "d"])

    async def test_concurrent_requests_share_one_load(self) -> None:
        cache: ResponseCache = ResponseCache()
        started: asyncio.Event = asyncio.Event()
        release: asyncio.Event = asyncio.Event()
        calls: int = 0

        async def load() -> str:
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return "lijst"

        waiters: list[asyncio.Task[str]] = [asyncio.create_task(cache.get("ah", "e1", load)) for _ in range(3)]
        await started.wait()
        release.set()
        self.assertEqual(await asyncio.gather(*waiters), ["lijst"] * 3)
        self.assertEqual(calls, 1)

    async def test_a_waiter_that_goes_away_does_not_stop_the_load_for_the_others(
        self,
    ) -> None:
        cache: ResponseCache = ResponseCache()
        release: asyncio.Event = asyncio.Event()

        async def load() -> str:
            await release.wait()
            return "lijst"

        gone: asyncio.Task[str] = asyncio.create_task(cache.get("ah", "e1", load))
        stays: asyncio.Task[str] = asyncio.create_task(cache.get("ah", "e1", load))
        await asyncio.sleep(0)
        gone.cancel()
        release.set()
        self.assertEqual(await stays, "lijst")
        with self.assertRaises(asyncio.CancelledError):
            await gone
        self.assertEqual(await cache.get("ah", "e1", load), "lijst")

    async def test_an_error_is_not_kept(self) -> None:
        cache: ResponseCache = ResponseCache()
        calls: int = 0

        async def load() -> str:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("stuk")
            return "lijst"

        with self.assertRaises(RuntimeError):
            await cache.get("ah", "e1", load)
        self.assertEqual(await cache.get("ah", "e1", load), "lijst")
        self.assertEqual(calls, 2)

    async def test_the_oldest_key_goes_first_when_the_cache_is_full(self) -> None:
        cache: ResponseCache = ResponseCache(size=2)
        calls: list[str] = []

        async def load(key: str) -> str:
            calls.append(key)
            return key

        for key in (
            "a",
            "b",
            "a",
            "c",
        ):  # "a" opnieuw gebruikt, dus "b" is het oudst als "c" erbij komt
            await cache.get(key, "e", lambda key=key: load(key))
        await cache.get("a", "e", lambda: load("a"))
        await cache.get("b", "e", lambda: load("b"))
        self.assertEqual(calls, ["a", "b", "c", "b"])


if __name__ == "__main__":
    unittest.main()
