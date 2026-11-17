"""De eerste dag van de week voor de startpagina (`mealie.week_start_hint`), zonder op Mealie te wachten.

Een vers browserprofiel kent de eerste dag nog niet en vroeg /api/offers pas aan na /api/mealie/status: een ronde extra voordat
de aanbiedingen kwamen (17-11-2026). De startpagina geeft nu mee wat de server al weet, en vraagt Mealie daarvoor nooit zelf.
"""

import os
import tempfile
import time
import unittest
from unittest import mock

from helpers import routes, run  # noqa: E402

from mealieshopper import auth, mealie  # noqa: E402

URL = "https://mealie.example.nl"
TOKEN = "eyJhbGciOiJIUzI1NiJ9.geheime-mealie-token.handtekening"


class WeekStartHintTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp: tempfile.TemporaryDirectory[str] = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"MEALIESHOPPER_DATA_DIR": tmp.name, "MEALIESHOPPER_AUTH_SECRET": "test-secret"})
        patcher.start()
        self.addCleanup(patcher.stop)
        for key in ("MEALIE_URL", "MEALIE_API_TOKEN"):
            os.environ.pop(key, None)
        auth._INITIALIZED = False
        mealie._week_start_cache.clear()
        self.addCleanup(mealie._week_start_cache.clear)

    def link(self) -> None:
        async def linked() -> None:
            await auth.set_secret("MEALIE_URL", URL)
            await auth.set_secret("MEALIE_API_TOKEN", TOKEN)

        run(linked())

    def test_without_mealie_the_week_starts_on_monday(self) -> None:
        self.assertEqual(run(mealie.week_start_hint()), mealie.WEEK_START_DEFAULT)

    def test_the_admin_setting_goes_first(self) -> None:
        self.link()
        run(auth.set_setting(mealie.SETTING_WEEK_START, "0"))
        with routes():  # geen enkele route: een vraag aan Mealie zou hier falen
            self.assertEqual(run(mealie.week_start_hint()), 0)

    def test_unknown_until_mealie_answered_once_and_mealie_is_not_asked(self) -> None:
        self.link()
        with routes() as api:
            self.assertIsNone(run(mealie.week_start_hint()))
        self.assertEqual(len(api.calls), 0)

    def test_what_mealie_said_counts_also_after_ten_minutes(self) -> None:
        self.link()
        mealie._week_start_cache[URL] = (0.0, 6)  # verlopen: de client controleert het nog met /api/mealie/status
        with routes() as api:
            self.assertEqual(run(mealie.week_start_hint()), 6)
        self.assertEqual(len(api.calls), 0)

    def test_a_failed_answer_from_mealie_means_monday_like_week_start(self) -> None:
        self.link()
        mealie._week_start_cache[URL] = (time.time() + 600, None)  # Mealie gaf geen geldige dag
        self.assertEqual(run(mealie.week_start_hint()), mealie.WEEK_START_DEFAULT)
        self.assertEqual(run(mealie.week_start())["day"], mealie.WEEK_START_DEFAULT)
