"""De eerste dag van de week: standaard zoals in Mealie, of een vaste dag die de beheerder kiest.

Mealie geeft die per huishouden in `GET /api/households/preferences` als `firstDayOfWeek` (0 = zondag ... 6 = zaterdag).
Het weekmenu, de MCP-tool get_week_menu en de bonusweek bij Aanbiedingen volgen die dag. De weekberekening uit static/app.js
en static/offers.js draait hier echt, in Node; zonder Node slaat die test over.
"""

import datetime
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

from mealieshopper import auth, mcp, mealie  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402
from helpers import AppTestCase, routes, run, sent_timeout  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
OFFERS_JS = (ROOT / "static" / "offers.js").read_text(encoding="utf-8")
TEMPLATE = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")

TOKEN = "eyJhbGciOiJIUzI1NiJ9.geheime-mealie-token.handtekening"
URL = "https://mealie.example.nl"


PREFERENCES = f"{URL}/api/households/preferences"


def response(status=200, body=None, json_error=False):
    if json_error:
        return httpx.Response(status, text="geen json")
    return httpx.Response(status, json=body)


def preferences(first_day):
    return response(body={"privateHousehold": True, "firstDayOfWeek": first_day, "recipePublic": True, "id": "voorkeuren"})


class WeekStartTestCase(AppTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"MEALIESHOPPER_DATA_DIR": tmp.name, "MEALIESHOPPER_AUTH_SECRET": "test-secret"})
        patcher.start()
        self.addCleanup(patcher.stop)
        for key in ("MEALIE_URL", "MEALIE_API_TOKEN"):
            os.environ.pop(key, None)
        auth._INITIALIZED = False
        mealie._week_start_cache.clear()
        self.addCleanup(mealie._week_start_cache.clear)
        self.client = self.client_for(create_app())

    async def link(self):
        await auth.set_secret("MEALIE_URL", URL)
        await auth.set_secret("MEALIE_API_TOKEN", TOKEN)


class FirstDayFromMealieTest(WeekStartTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.link()

    def test_read_from_the_household_preferences_with_the_token(self):
        with routes() as api:
            get = api.get(PREFERENCES).mock(return_value=preferences(6))
            self.assertEqual(run(mealie.first_day_of_week()), 6)
        self.assertEqual(get.calls.last.request.headers["Authorization"], f"Bearer {TOKEN}")
        self.assertEqual(sent_timeout(get.calls.last), 10)

    def test_sunday_is_zero_like_in_mealie(self):
        with routes() as api:
            api.get(PREFERENCES).mock(return_value=preferences(0))
            self.assertEqual(run(mealie.first_day_of_week()), 0)

    def test_remembered_so_every_page_load_does_not_ask_mealie(self):
        with routes() as api:
            get = api.get(PREFERENCES).mock(return_value=preferences(1))
            for _ in range(20):
                self.assertEqual(run(mealie.first_day_of_week()), 1)
        self.assertEqual(get.call_count, 1)

    def test_anything_but_a_day_from_0_to_6_is_unknown(self):
        for value in (True, False, 7, -1, "1", 1.0, None):
            mealie._week_start_cache.clear()
            with routes() as api:
                api.get(PREFERENCES).mock(return_value=preferences(value))
                self.assertIsNone(run(mealie.first_day_of_week()), repr(value))
        for answer in (response(body={"privateHousehold": True}), response(body=None), response(body=[1]), response(json_error=True),
                       response(status=401), response(status=404)):
            mealie._week_start_cache.clear()
            with routes() as api:
                api.get(PREFERENCES).mock(return_value=answer)
                self.assertIsNone(run(mealie.first_day_of_week()))

    def test_a_failure_never_raises_and_is_retried_after_a_minute(self):
        with routes() as api:
            get = api.get(PREFERENCES).mock(side_effect=httpx.ConnectError("weg"))
            self.assertIsNone(run(mealie.first_day_of_week()))
            self.assertIsNone(run(mealie.first_day_of_week()))
        self.assertEqual(get.call_count, 1, "binnen een minuut niet opnieuw")
        mealie._week_start_cache[URL] = (0, None)  # de wachttijd is voorbij
        with routes() as api:
            api.get(PREFERENCES).mock(return_value=preferences(6))
            self.assertEqual(run(mealie.first_day_of_week()), 6)

    def test_without_a_link_there_is_no_request(self):
        run(auth.delete_secret("MEALIE_URL"))
        with routes() as api:
            anything = api.route()
            self.assertIsNone(run(mealie.first_day_of_week()))
        self.assertFalse(anything.called)


class WeekStartChoiceTest(WeekStartTestCase):
    async def test_follows_mealie_by_default(self):
        await self.link()
        with routes() as api:
            api.get(PREFERENCES).mock(return_value=preferences(6))
            self.assertEqual(await mealie.week_start(), {"day": 6, "source": "mealie", "setting": "", "mealie": 6})

    async def test_a_fixed_day_wins_without_asking_mealie(self):
        await self.link()
        await auth.set_setting(mealie.SETTING_WEEK_START, "1")
        with routes() as api:
            anything = api.route()
            self.assertEqual(await mealie.week_start(), {"day": 1, "source": "setting", "setting": "1", "mealie": None})
            await auth.set_setting(mealie.SETTING_WEEK_START, "0")
            self.assertEqual((await mealie.week_start())["day"], 0, "zondag is ook een vaste dag")
        self.assertFalse(anything.called)

    async def test_monday_when_mealie_does_not_say(self):
        with routes() as api:
            anything = api.route()
            self.assertEqual(await mealie.week_start(), {"day": 1, "source": "default", "setting": "", "mealie": None})
        self.assertFalse(anything.called)
        await self.link()
        with routes() as api:
            api.get(PREFERENCES).mock(side_effect=httpx.ReadTimeout("traag"))
            self.assertEqual((await mealie.week_start())["source"], "default")

    async def test_a_broken_stored_value_counts_as_following_mealie(self):
        await self.link()
        for value in ("7", "ma", " ", "1.0"):
            await auth.set_setting(mealie.SETTING_WEEK_START, value)
            mealie._week_start_cache.clear()
            with routes() as api:
                api.get(PREFERENCES).mock(return_value=preferences(6))
                self.assertEqual((await mealie.week_start())["source"], "mealie", repr(value))


class StatusTest(WeekStartTestCase):
    async def test_the_status_says_where_the_week_starts(self):
        data = (await self.client.get("/api/mealie/status")).get_json()
        self.assertEqual(data["weekStart"], {"day": 1, "source": "default", "setting": "", "mealie": None})
        await self.link()
        with routes() as api:
            api.get(PREFERENCES).mock(return_value=preferences(6))
            data = (await self.client.get("/api/mealie/status")).get_json()
        self.assertEqual(data["weekStart"]["day"], 6)
        self.assertNotIn(TOKEN, json.dumps(data))

    async def test_checking_the_link_picks_up_a_change_in_mealie_at_once(self):
        await self.link()
        with routes() as api:
            api.get(PREFERENCES).mock(return_value=preferences(1))
            self.assertEqual((await self.client.get("/api/mealie/status")).get_json()["weekStart"]["day"], 1)
        with routes() as api:
            api.get(PREFERENCES).mock(return_value=preferences(6))
            self.assertEqual((await self.client.get("/api/mealie/status")).get_json()["weekStart"]["day"], 1, "nog onthouden")
        with routes() as api:
            api.get(f"{URL}/api/users/self").mock(return_value=response(body={"username": "helmer"}))
            api.get(f"{URL}/api/app/about").mock(return_value=response(body={"version": "v3.30.0"}))
            api.get(PREFERENCES).mock(return_value=preferences(6))
            self.assertEqual((await self.client.get("/api/mealie/status?verify=1")).get_json()["weekStart"]["day"], 6)


class WeekMenuToolTest(WeekStartTestCase):
    def test_the_week_menu_starts_on_the_first_day_of_the_week(self):
        for first, isoweekday in ((6, 6), (0, 7), (1, 1), (3, 3)):
            with mock.patch.object(mealie, "week_start", new_callable=mock.AsyncMock, return_value={"day": first}), \
                    mock.patch.object(mealie, "get_meal_plan_with_recipes", new_callable=mock.AsyncMock, return_value=[]) as plan:
                data = run(mcp.tool_week_menu({}))
            start, end = (datetime.date.fromisoformat(day) for day in plan.call_args.args)
            self.assertEqual(start.isoweekday(), isoweekday, first)
            self.assertEqual((end - start).days, 6)
            self.assertLessEqual(start, datetime.date.today())
            self.assertGreater(start + datetime.timedelta(days=7), datetime.date.today(), "de huidige week")
            self.assertEqual(data["from"], start.isoformat())

    def test_an_explicit_start_date_does_not_ask_for_the_week_start(self):
        with mock.patch.object(mealie, "week_start", new_callable=mock.AsyncMock, side_effect=AssertionError("niet nodig")), \
                mock.patch.object(mealie, "get_meal_plan_with_recipes", new_callable=mock.AsyncMock, return_value=[]) as plan:
            run(mcp.tool_week_menu({"start_date": "2026-11-07", "days": 7}))
        plan.assert_awaited_once_with("2026-11-07", "2026-11-13")

    def test_week_start_counts_like_mealie(self):
        wednesday = datetime.date(2026, 11, 4)
        self.assertEqual(mcp._week_start(wednesday, 0), datetime.date(2026, 11, 1))
        self.assertEqual(mcp._week_start(wednesday, 1), datetime.date(2026, 11, 2))
        self.assertEqual(mcp._week_start(wednesday, 3), wednesday)
        self.assertEqual(mcp._week_start(wednesday, 6), datetime.date(2026, 10, 31))
        self.assertEqual(mcp._week_start(datetime.date(2026, 11, 7), 6), datetime.date(2026, 11, 7))

    def test_the_tool_description_no_longer_promises_monday(self):
        description = json.dumps(mcp.TOOLS["get_week_menu"]["description"]) + json.dumps(mcp.TOOLS["get_week_menu"]["inputSchema"])
        self.assertNotIn("maandag t/m zondag", description)
        self.assertIn("eerste dag van de week", description)


def function_source(source, name):
    match = re.search(rf"^(?:async )?function {re.escape(name)}\(.*?^}}$", source, re.S | re.M)
    if not match:
        raise AssertionError(f"functie {name} niet gevonden")
    return match.group(0)


@unittest.skipUnless(shutil.which("node"), "Node is niet geïnstalleerd")
class BrowserWeekTest(AppTestCase):
    """De weekberekening in de browser, in de tijdzone van Nederland (ook over de wintertijd heen)."""

    def run_js(self, expression):
        script = "\n".join(
            [
                "const state = { firstDayOfWeek: 1 };",
                'const I18N = { locale: () => "nl-NL" };',
                function_source(APP_JS, "localIsoDate"),
                function_source(APP_JS, "monthShort"),
                function_source(APP_JS, "weekStartOf"),
                function_source(APP_JS, "weekFrom"),
                function_source(OFFERS_JS, "bonusWeekOf"),
                f"process.stdout.write(JSON.stringify({expression}));",
            ]
        )
        env = {**os.environ, "TZ": "Europe/Amsterdam"}
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30, check=True, env=env)
        return json.loads(result.stdout)

    def test_the_week_starts_on_the_chosen_day(self):
        starts = self.run_js("[0, 1, 3, 6].map((day) => localIsoDate(weekStartOf(new Date(2026, 10, 4), day)))")
        self.assertEqual(starts, ["2026-11-01", "2026-11-02", "2026-11-04", "2026-10-31"])
        self.assertEqual(self.run_js("localIsoDate(weekStartOf(new Date(2026, 10, 7, 23, 30), 6))"), "2026-11-07", "op de dag zelf")

    def test_by_default_monday_as_before(self):
        self.assertEqual(self.run_js("localIsoDate(weekStartOf(new Date(2026, 10, 8)))"), "2026-11-02", "zondag hoort bij de week ervoor")

    def test_seven_days_also_across_the_change_to_winter_time(self):
        week = self.run_js("weekFrom(weekStartOf(new Date(2026, 9, 28), 6))")  # de klok gaat op zondag 25 oktober een uur terug
        self.assertEqual(week["days"], [f"2026-10-{day}" for day in range(24, 31)])
        self.assertEqual((week["start"], week["end"]), ("2026-10-24", "2026-10-30"))
        self.assertEqual(week["label"], "24 okt - 30 okt")

    def test_offers_show_the_bonus_week_that_overlaps_most(self):
        saturday = self.run_js("bonusWeekOf(weekFrom(new Date(2026, 10, 7)))")  # za 7 t/m vr 13 nov
        self.assertEqual((saturday["start"], saturday["end"]), ("2026-11-09", "2026-11-15"))
        sunday = self.run_js("bonusWeekOf(weekFrom(new Date(2026, 10, 8)))")  # zo 8 t/m za 14 nov
        self.assertEqual(sunday["start"], "2026-11-09")
        monday = self.run_js("bonusWeekOf(weekFrom(new Date(2026, 10, 2)))")  # ma 2 t/m zo 8 nov: dezelfde week, zoals altijd
        self.assertEqual((monday["start"], monday["end"]), ("2026-11-02", "2026-11-08"))
        friday = self.run_js("bonusWeekOf(weekFrom(new Date(2026, 10, 6)))")  # vr 6 t/m do 12 nov
        self.assertEqual(friday["start"], "2026-11-09")


class BrowserWiringTest(AppTestCase):
    def test_monday_is_no_longer_hardcoded(self):
        self.assertNotIn("mondayOf", APP_JS + OFFERS_JS)
        self.assertIn("return weekFrom(weekStartOf(today));", function_source(APP_JS, "getWeekBounds"))
        self.assertIn("weekStartOf(new Date(`${date}T00:00:00`))", function_source(APP_JS, "refreshMealPlanAfterChange"))

    def test_the_week_start_is_applied_before_the_meal_plan_loads(self):
        render = function_source(APP_JS, "renderMealieStatus")
        self.assertLess(render.index("applyWeekStart(data.weekStart)"), render.index("loadMealPlan()"))
        self.assertIn("} else if (weekChanged) {\n    loadMealPlan();", render)

    def test_offers_use_the_bonus_week(self):
        self.assertIn("return bonusWeekOf(getWeekBounds(offersWeekOffset()));", function_source(OFFERS_JS, "offersWeek"))
        self.assertIn("const week = offersWeek();", function_source(OFFERS_JS, "renderOffersWeek"))
        self.assertIn("offersWeekStartChanged()", function_source(APP_JS, "applyWeekStart"))

    def test_the_owner_chooses_the_day_next_to_the_mealie_link(self):
        body = TEMPLATE[TEMPLATE.index('id="mealie-login-body"'):TEMPLATE.index('id="ah-login-box"')]
        self.assertIn('id="mealie-week-start"', body)
        self.assertIn('$("#mealie-week-start").addEventListener("change", saveWeekStart);', APP_JS)
        save = function_source(APP_JS, "saveWeekStart")
        self.assertIn('"/api/server-settings"', save)
        self.assertIn('weekStart: select.value === "" ? null : Number(select.value)', save)


if __name__ == "__main__":
    unittest.main()
