import os
import re
import tempfile
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest import mock

import httpx
import respx

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

from mealieshopper import http_client, mealie, mealie_images  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402
from support import SyncClient, run  # noqa: E402

RECIPE_ID = "1b4e28ba-2fa1-11d2-883f-0016d3cca427"
OTHER_ID = "2b4e28ba-2fa1-11d2-883f-0016d3cca427"
WEBP = b"RIFF....WEBPVP8 "


IMAGE_URL = re.compile(r"^https://mealie\.example/api/media/recipes/[^/]+/images/[^/]+\.webp$")


def response(status: int = 200, body: bytes = WEBP, content_type: str = "image/webp") -> httpx.Response:
    return httpx.Response(status, content=body, headers={"Content-Type": content_type})


@contextmanager
def mealie_media(side_effect: Any = None, *, return_value: httpx.Response | None = None) -> Iterator[respx.Route]:
    """Een respx-route op de afbeeldingen van Mealie. Elke aanroep krijgt `return_value` of het volgende uit `side_effect`."""
    with respx.mock(assert_all_called=False) as router:
        route = router.get(url__regex=IMAGE_URL.pattern)
        route.mock(return_value=return_value, side_effect=side_effect)
        yield route


class MealieImagesTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(os.environ, {"MEALIESHOPPER_DATA_DIR": tmp.name})
        env.start()
        self.addCleanup(env.stop)
        for name, value in (
            ("ensure_configured", None),
            ("mealie_url", "https://mealie.example"),
            ("mealie_token", "secret-token"),
            ("group_slug", None),
        ):
            patcher = mock.patch.object(mealie, name, new_callable=mock.AsyncMock, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = SyncClient(create_app())


class ImagePathTest(unittest.TestCase):
    def test_path_for_a_recipe_with_an_image(self):
        self.assertEqual(
            mealie_images.image_path({"id": RECIPE_ID, "image": "abc123"}),
            f"/api/mealie/recipes/{RECIPE_ID}/image?v=abc123",
        )

    def test_no_path_without_image_or_with_a_bad_id(self):
        self.assertIsNone(mealie_images.image_path({"id": RECIPE_ID, "image": None}))
        self.assertIsNone(mealie_images.image_path({"id": RECIPE_ID, "image": False}))
        self.assertIsNone(mealie_images.image_path({"id": "../../etc/passwd", "image": "x"}))
        self.assertIsNone(mealie_images.image_path(None))


class MealPlanImageTest(MealieImagesTestCase):
    def test_entries_carry_the_image_address(self):
        entries = [
            {"date": "2026-10-19", "entryType": "dinner", "recipe": {"id": RECIPE_ID, "slug": "soep", "image": "v1", "name": "Soep"}},
            {"date": "2026-10-20", "entryType": "dinner", "recipe": {"id": OTHER_ID, "slug": "pasta", "image": None, "name": "Pasta"}},
            {"date": "2026-10-21", "entryType": "lunch", "title": "Restjes"},
        ]
        details = {
            "soep": {"id": RECIPE_ID, "image": "v2", "name": "Soep", "recipeIngredient": []},
            "pasta": {"id": OTHER_ID, "image": None, "name": "Pasta", "recipeIngredient": []},
        }
        with mock.patch.object(mealie, "get_meal_plan", new_callable=mock.AsyncMock, return_value=entries), \
                mock.patch.object(mealie, "get_recipe", new_callable=mock.AsyncMock, side_effect=lambda slug: details[slug]):
            result = run(mealie.get_meal_plan_with_recipes("2026-10-19", "2026-10-25"))
        self.assertEqual(result[0]["imageUrl"], f"/api/mealie/recipes/{RECIPE_ID}/image?v=v2")
        self.assertIsNone(result[1]["imageUrl"])
        self.assertIsNone(result[2]["imageUrl"])

    def test_summary_image_is_used_when_the_detail_fails(self):
        entries = [{"date": "2026-10-19", "recipe": {"id": RECIPE_ID, "slug": "soep", "image": "v1"}}]
        with mock.patch.object(mealie, "get_meal_plan", new_callable=mock.AsyncMock, return_value=entries), \
                mock.patch.object(mealie, "get_recipe", new_callable=mock.AsyncMock, side_effect=RuntimeError("boem")):
            result = run(mealie.get_meal_plan_with_recipes("2026-10-19", "2026-10-25"))
        self.assertEqual(result[0]["imageUrl"], f"/api/mealie/recipes/{RECIPE_ID}/image?v=v1")


class FetchAndCacheTest(MealieImagesTestCase):
    def test_fetches_with_the_token_and_then_serves_from_the_local_copy(self):
        with mealie_media(return_value=response()) as get:
            first = run(mealie_images.get_image(RECIPE_ID, version="v1"))
            second = run(mealie_images.get_image(RECIPE_ID, version="v1"))
        self.assertEqual(first, ("image/webp", WEBP))
        self.assertEqual(second, first)
        self.assertEqual(get.call_count, 1, "tweede keer uit SQLite")
        request = get.calls.last.request
        self.assertEqual(str(request.url), f"https://mealie.example/api/media/recipes/{RECIPE_ID}/images/min-original.webp")
        self.assertEqual(request.headers["Authorization"], "Bearer secret-token")
        self.assertEqual(request.headers["Accept"], "image/*")
        self.assertEqual(request.headers["User-Agent"], http_client.DEFAULT_USER_AGENT, "Mealie krijgt de vaste User-Agent, niet die van httpx")

    def test_a_redirect_is_not_followed(self):
        # Zoals requests met allow_redirects=False: een doorverwijzing is geen afbeelding, en het token gaat niet mee naar een ander adres.
        with respx.mock(assert_all_called=False) as router:
            elsewhere = router.get("https://elders.example/plaatje.webp").mock(return_value=response())
            router.get(url__regex=IMAGE_URL.pattern).mock(return_value=httpx.Response(302, headers={"Location": "https://elders.example/plaatje.webp"}))
            with self.assertRaises(RuntimeError):
                run(mealie_images.get_image(RECIPE_ID))
        self.assertFalse(elsewhere.called)

    def test_an_image_over_the_limit_is_refused(self):
        with mealie_media(return_value=response(body=b"x" * (mealie_images.MAX_BYTES + 1))):
            with self.assertRaisesRegex(RuntimeError, "te groot"):
                run(mealie_images.get_image(RECIPE_ID))

    def test_new_image_version_refetches(self):
        with mealie_media([response(body=b"one"), response(body=b"two")]) as get:
            self.assertEqual(run(mealie_images.get_image(RECIPE_ID, version="v1"))[1], b"one")
            self.assertEqual(run(mealie_images.get_image(RECIPE_ID, version="v2"))[1], b"two")
            self.assertEqual(run(mealie_images.get_image(RECIPE_ID, version="v2"))[1], b"two")
        self.assertEqual(get.call_count, 2)

    def test_unknown_size_falls_back_to_the_default(self):
        with mealie_media(return_value=response()) as get:
            run(mealie_images.get_image(RECIPE_ID, size="../../secret"))
        self.assertIn("/images/min-original.webp", str(get.calls.last.request.url))

    def test_old_copy_survives_an_unreachable_mealie(self):
        with mealie_media(return_value=response(body=b"old")):
            run(mealie_images.get_image(RECIPE_ID, version="v1"))
        with mealie_media(httpx.ConnectError("down")):
            self.assertEqual(run(mealie_images.get_image(RECIPE_ID, version="v2"))[1], b"old")

    def test_unreachable_mealie_without_a_copy_is_an_error(self):
        with mealie_media(httpx.ConnectError("down")):
            with self.assertRaises(httpx.ConnectError):
                run(mealie_images.get_image(RECIPE_ID))

    def test_missing_image_and_bad_content(self):
        with mealie_media(return_value=response(404)):
            with self.assertRaises(mealie_images.ImageNotFound):
                run(mealie_images.get_image(RECIPE_ID))
        for content_type in ("text/html", "image/svg+xml"):
            with self.subTest(content_type=content_type), \
                    mealie_media(return_value=response(content_type=content_type)):
                with self.assertRaises(RuntimeError):
                    run(mealie_images.get_image(OTHER_ID))


class ImageRouteTest(MealieImagesTestCase):
    def test_serves_the_image_with_long_cache_when_versioned(self):
        with mealie_media(return_value=response()):
            res = self.client.get(f"/api/mealie/recipes/{RECIPE_ID}/image?v=v1")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data, WEBP)
        self.assertEqual(res.mimetype, "image/webp")
        self.assertIn("immutable", res.headers["Cache-Control"])
        self.assertEqual(res.headers["X-Content-Type-Options"], "nosniff")

    def test_without_version_the_browser_cache_is_short(self):
        with mealie_media(return_value=response()):
            res = self.client.get(f"/api/mealie/recipes/{RECIPE_ID}/image")
        self.assertNotIn("immutable", res.headers["Cache-Control"])

    def test_rejects_ids_that_are_not_recipe_ids(self):
        with mealie_media(return_value=response()) as get:
            for bad in ("abc", "..%2f..%2fapi%2fusers", "1b4e28ba-2fa1-11d2-883f-0016d3cca42"):
                # Een gecodeerde slash haalt de route al niet meer binnen (404); de rest geeft 400.
                self.assertIn(self.client.get(f"/api/mealie/recipes/{bad}/image").status_code, (400, 404), bad)
        self.assertFalse(get.called)

    def test_no_image_is_404_and_errors_are_424_json(self):
        with mealie_media(return_value=response(404)):
            self.assertEqual(self.client.get(f"/api/mealie/recipes/{RECIPE_ID}/image").status_code, 404)
        with mealie_media(httpx.ConnectError("down")):
            res = self.client.get(f"/api/mealie/recipes/{OTHER_ID}/image")
        self.assertEqual(res.status_code, 424)
        self.assertEqual(res.get_json()["error"], "down")


if __name__ == "__main__":
    unittest.main()
