"""Het opstarten van de pagina: welke aanvragen tegelijk gaan, waarop het scherm uit het adres wacht, en de eerste dag van de week
die de pagina van de server meekrijgt of die het toestel onthield. De echte functies uit static/app.js, router.js, onboarding.js en
offers.js draaien hier in Node. Zonder Node slaan die tests over.

Gemeten op 17 november 2026: een koude /offers vroeg /api/auth/status, /api/onboarding/status, /api/auth/credentials en
/api/mealie/status na elkaar op, voor /api/offers de deur uit ging.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from helpers import AppTestCase

from mealieshopper import auth, mealie
from mealieshopper.app import create_app

ROOT: Path = Path(__file__).resolve().parents[1]
APP_JS: str = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
ROUTER_JS: str = (ROOT / "static" / "router.js").read_text(encoding="utf-8")
ONBOARDING_JS: str = (ROOT / "static" / "onboarding.js").read_text(encoding="utf-8")
OFFERS_JS: str = (ROOT / "static" / "offers.js").read_text(encoding="utf-8")
OFFERS_CART_JS: str = (ROOT / "static" / "offers_cart.js").read_text(encoding="utf-8")


def function_source(source: str, name: str) -> str:
    match: re.Match[str] | None = re.search(rf"^(?:async )?function {re.escape(name)}\(.*?^}}$", source, re.S | re.M)
    if not match:
        raise AssertionError(f"functie {name} niet gevonden")
    return match.group(0)


def run_node(test: unittest.TestCase, script: str, tz: str = "Europe/Amsterdam") -> object:
    env: dict[str, str] = {**os.environ, "TZ": tz}
    result: subprocess.CompletedProcess[str] = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30, check=False, env=env
    )
    test.assertEqual(result.returncode, 0, result.stderr)
    return json.loads(result.stdout.strip().splitlines()[-1])


# De opstart met nagebootste aanvragen: elke aanvraag wacht tot de test hem beantwoordt (answer) of laat mislukken (fail).
BOOT_PAGE: str = r"""
const state = { auth: null, credentials: [], onboarding: null };
const log = [];
const open = new Map();
function jsonFetch(url) {
  log.push(url);
  return new Promise((resolve, reject) => open.set(url, { resolve, reject }));
}
function answer(url, data) { open.get(url).resolve(data); open.delete(url); }
function fail(url, text) { open.get(url).reject(new Error(text)); open.delete(url); }
const settle = async () => { for (let i = 0; i < 20; i += 1) await new Promise((resolve) => setImmediate(resolve)); };
function appearanceFromProfile() { log.push("appearance"); }
function renderAuthState() { log.push("render"); }
function renderCredentials() { log.push(`credentials:${state.credentials.length}`); }
function refreshAhStatus() { log.push("status:ah"); }
function refreshMealieStatus() { log.push("status:mealie"); }
function refreshPicnicStatus() { log.push("status:picnic"); }
function refreshJumboStatus() { log.push("status:jumbo"); }
function refreshCrispStatus() { log.push("status:crisp"); }
function startUpdateCheck() { log.push("update"); }
function routerStart() { log.push("router"); }
let address = "http://localhost/offers";
const window = {
  get location() {
    const url = new URL(address);
    return { href: url.href, search: url.search, origin: url.origin, replace: (next) => log.push(`replace:${next}`) };
  },
};
let done = false;
let failure = "";
function boot() {
  refreshAuthStatus().then(() => { done = true; }, (error) => { failure = error.message; });
}
const SIGNED_IN = { enabled: true, authenticated: true, username: "spam" };
const SIGNED_OUT = { enabled: true, authenticated: false, configured: true };
const DONE = { needed: false, authDone: true };
"""


@unittest.skipUnless(shutil.which("node"), "Node is niet geïnstalleerd")
class BootChainTest(unittest.TestCase):
    def run_boot(self, scenario: str) -> dict:
        script: str = "\n".join(
            [
                BOOT_PAGE,
                function_source(APP_JS, "loadCredentials"),
                function_source(APP_JS, "continueAfterLogin"),
                function_source(APP_JS, "refreshLinkStatuses"),
                function_source(APP_JS, "refreshAuthStatus"),
                function_source(APP_JS, "startRouter"),
                function_source(ONBOARDING_JS, "loadOnboardingStatus"),
                function_source(ROUTER_JS, "routerNeedsCredentials"),
                f"(async () => {{\n{scenario}\n}})();",
            ]
        )
        return run_node(self, script)

    def test_auth_and_onboarding_go_out_together(self) -> None:
        result: dict = self.run_boot("""
boot(); await settle();
const first = [...log];
answer("/api/auth/status", SIGNED_IN); await settle();
const afterAuth = [...log];
answer("/api/onboarding/status", DONE); await settle();
console.log(JSON.stringify({ first, afterAuth, log }));
""")
        self.assertEqual(result["first"], ["/api/onboarding/status", "/api/auth/status"])
        # De weergave van de gebruiker meteen, het scherm pas als ook de installatiehulp bekend is (die kan het inlogscherm vervangen).
        self.assertEqual(result["afterAuth"][2:], ["appearance"])
        self.assertLess(result["log"].index("/api/onboarding/status"), result["log"].index("render"))
        self.assertEqual(result["log"].index("render"), 3)

    def test_the_offers_page_does_not_wait_for_the_passkeys(self) -> None:
        result: dict = self.run_boot("""
boot(); await settle();
answer("/api/onboarding/status", DONE); answer("/api/auth/status", SIGNED_IN); await settle();
const before = { log: [...log], done };
answer("/api/auth/credentials", { credentials: [{ id: "eggs" }] }); await settle();
console.log(JSON.stringify({ before, log, done }));
""")
        before: dict = result["before"]
        self.assertFalse(before["done"], "wie op refreshAuthStatus wacht, wacht nog op de passkeys")
        self.assertIn("router", before["log"])
        self.assertIn("/api/auth/credentials", before["log"])
        # Het scherm eerst: over HTTP/1.1 namen de passkeys en de vijf koppelingen anders alle zes verbindingen, voor /api/offers.
        self.assertLess(before["log"].index("router"), before["log"].index("/api/auth/credentials"))
        self.assertLess(before["log"].index("router"), before["log"].index("status:ah"))
        for status in ("status:ah", "status:mealie", "status:picnic", "status:jumbo", "status:crisp", "update"):
            self.assertIn(status, before["log"])
        self.assertTrue(result["done"])
        self.assertEqual(result["log"][-1], "credentials:1")

    def test_the_security_page_opens_with_its_passkeys(self) -> None:
        result: dict = self.run_boot("""
address = "http://localhost/profile/security";
boot(); await settle();
answer("/api/onboarding/status", DONE); answer("/api/auth/status", SIGNED_IN); await settle();
const before = [...log];
answer("/api/auth/credentials", { credentials: [{ id: "eggs" }, { id: "ham" }] }); await settle();
console.log(JSON.stringify({ before, log, done }));
""")
        self.assertNotIn("router", result["before"])
        self.assertIn("status:ah", result["before"], "de koppelingen wachten niet op de passkeys")
        self.assertLess(result["log"].index("credentials:2"), result["log"].index("router"))
        self.assertTrue(result["done"])

    def test_signed_out_asks_only_for_the_status_and_the_onboarding(self) -> None:
        result: dict = self.run_boot("""
boot(); await settle();
answer("/api/onboarding/status", DONE); answer("/api/auth/status", SIGNED_OUT); await settle();
console.log(JSON.stringify({ log, done, failure }));
""")
        self.assertEqual(
            result["log"],
            ["/api/onboarding/status", "/api/auth/status", "appearance", "render", "credentials:0"],
        )
        self.assertTrue(result["done"])

    def test_an_app_sent_you_to_log_in_and_goes_back_without_more_requests(self) -> None:
        result: dict = self.run_boot("""
address = "http://localhost/?next=%2Foauth%2Fauthorize%3Fclient_id%3Dspam";
boot(); await settle();
answer("/api/onboarding/status", DONE); answer("/api/auth/status", SIGNED_IN); await settle();
console.log(JSON.stringify({ log, done }));
""")
        self.assertEqual(
            result["log"],
            ["/api/onboarding/status", "/api/auth/status", "appearance", "render", "replace:/oauth/authorize?client_id=spam"],
        )
        self.assertTrue(result["done"])

    def test_a_failed_passkey_list_still_reaches_the_caller(self) -> None:
        result: dict = self.run_boot("""
boot(); await settle();
answer("/api/onboarding/status", DONE); answer("/api/auth/status", SIGNED_IN); await settle();
fail("/api/auth/credentials", "kapot"); await settle();
console.log(JSON.stringify({ done, failure }));
""")
        self.assertEqual(result, {"done": False, "failure": "kapot"})

    def test_a_failed_onboarding_status_does_not_stop_the_page(self) -> None:
        result: dict = self.run_boot("""
boot(); await settle();
fail("/api/onboarding/status", "weg"); answer("/api/auth/status", SIGNED_IN); await settle();
answer("/api/auth/credentials", { credentials: [] }); await settle();
console.log(JSON.stringify({ log, done, onboarding: state.onboarding }));
""")
        self.assertIn("router", result["log"])
        self.assertTrue(result["done"])
        self.assertIsNone(result["onboarding"])

    def test_only_the_security_page_needs_the_passkeys(self) -> None:
        result: object = self.run_boot("""
const paths = ["/profile/security", "/profile/security/", "/profile", "/offers", "/settings/security", "/"];
console.log(JSON.stringify(paths.map((path) => routerNeedsCredentials(`http://localhost${path}`))));
""")
        self.assertEqual(result, [True, True, False, False, False, False])


# De opslag van het toestel: `stored` is wat getItem teruggeeft, of "throw" voor een browser die de opslag weigert.
STORAGE_PAGE: str = r"""
const written = [];
function storage(stored) {
  return {
    getItem: () => { if (stored === "throw") throw new Error("SecurityError"); return stored; },
    setItem: (key, value) => { if (stored === "throw") throw new Error("QuotaExceededError"); written.push([key, value]); },
  };
}
"""


@unittest.skipUnless(shutil.which("node"), "Node is niet geïnstalleerd")
class RememberedWeekStartTest(unittest.TestCase):
    """Aanbiedingen wachtte bij elk openen een ronde op /api/mealie/status voor de eerste dag van de week. Nu onthoudt het toestel die dag."""

    def initial_state(self, stored: object, page: str | None = None) -> dict:
        # `page`: data-week-start op <html>, zoals de server hem meegaf (None: geen attribuut).
        top: str = APP_JS[: APP_JS.index("// Material Design Icons")]
        dataset: str = json.dumps({} if page is None else {"weekStart": page})
        script: str = "\n".join(
            [
                STORAGE_PAGE,
                f"const window = {{ localStorage: storage({json.dumps(stored)}) }};",
                f"const document = {{ documentElement: {{ dataset: {dataset} }} }};",
                *(function_source(APP_JS, name) for name in ("parseWeekStart", "pageWeekStart", "storedWeekStart", "initialWeekStart")),
                top,
                "console.log(JSON.stringify({ first: state.firstDayOfWeek, known: state.weekStartKnown }));",
            ]
        )
        return run_node(self, script)

    def test_a_remembered_day_counts_as_known(self) -> None:
        self.assertEqual(self.initial_state("0"), {"first": 0, "known": True}, "zondag")
        self.assertEqual(self.initial_state("6"), {"first": 6, "known": True})
        self.assertEqual(self.initial_state("1"), {"first": 1, "known": True})

    def test_anything_else_is_unknown_and_the_offers_wait_as_before(self) -> None:
        for stored in (None, "", "7", "-1", "1.0", " 1", "01", "zondag", "throw"):
            self.assertEqual(self.initial_state(stored), {"first": 1, "known": False}, repr(stored))
        self.assertEqual(self.initial_state(None, page=""), {"first": 1, "known": False}, "een leeg attribuut")

    def test_a_fresh_profile_starts_with_the_day_from_the_server(self) -> None:
        self.assertEqual(self.initial_state(None, page="0"), {"first": 0, "known": True})
        self.assertEqual(self.initial_state("throw", page="6"), {"first": 6, "known": True}, "ook zonder opslag")

    def test_the_server_goes_before_the_remembered_day(self) -> None:
        # De server weet wat de beheerder nu instelde, het toestel wat het de vorige keer hoorde.
        self.assertEqual(self.initial_state("6", page="1"), {"first": 1, "known": True})
        self.assertEqual(self.initial_state("6", page="7"), {"first": 6, "known": True}, "een onzinnig attribuut telt niet")

    def apply(self, stored: object, first: int, days: list[int]) -> dict:
        script: str = "\n".join(
            [
                STORAGE_PAGE,
                f"const window = {{ localStorage: storage({json.dumps(stored)}), location: {{ search: '' }} }};",
                'const WEEK_START_KEY = "ms-week-start";',
                f"const state = {{ firstDayOfWeek: {first}, weekOffset: 0 }};",
                "const changed = [];",
                "function renderWeekStartChoice() {}",
                "function updateWeekLabel() {}",
                "function offersWeekStartChanged() { changed.push(state.firstDayOfWeek); }",
                function_source(APP_JS, "rememberWeekStart"),
                function_source(APP_JS, "applyWeekStart"),
                f"const results = {json.dumps(days)}.map((day) => applyWeekStart({{ day, source: 'mealie' }}));",
                "console.log(JSON.stringify({ results, written, changed, first: state.firstDayOfWeek }));",
            ]
        )
        return run_node(self, script)

    def test_the_answer_is_remembered_also_when_it_did_not_change(self) -> None:
        # Een installatie die op maandag begint, krijgt elke keer dezelfde dag: ook die moet het toestel onthouden.
        result: dict = self.apply(None, 1, [1])
        self.assertEqual(result, {"results": [False], "written": [["ms-week-start", "1"]], "changed": [], "first": 1})

    def test_a_different_answer_corrects_the_remembered_day(self) -> None:
        result: dict = self.apply("6", 6, [0, 0])
        self.assertEqual(result["results"], [True, False])
        self.assertEqual(result["written"], [["ms-week-start", "0"], ["ms-week-start", "0"]])
        self.assertEqual(result["changed"], [0])

    def test_without_storage_the_week_start_still_applies(self) -> None:
        result: dict = self.apply("throw", 1, [0])
        self.assertEqual(result, {"results": [True], "written": [], "changed": [0], "first": 0})

    def offer_weeks(self, today: str, remembered: int, answer: int) -> list[str]:
        # De echte weekberekening en de correctie van Aanbiedingen, op een vaste dag. showOffers noteert welke week hij opvraagt.
        year, month, day = (int(part) for part in today.split("-"))
        script: str = "\n".join(
            [
                "const RealDate = Date;",
                f"const NOW = new RealDate({year}, {month - 1}, {day}, 12, 0).getTime();",
                "Date = class extends RealDate { constructor(...args) { super(...(args.length ? args : [NOW])); } static now() { return NOW; } };",
                STORAGE_PAGE,
                "const window = { localStorage: storage(null), location: { search: '' } };",
                'const WEEK_START_KEY = "ms-week-start";',
                'const I18N = { locale: () => "nl-NL" };',
                f"const state = {{ firstDayOfWeek: {remembered}, weekOffset: 0, weekStartKnown: true }};",
                "const offersState = { weekStart: '' };",
                "const asked = [];",
                "function renderWeekStartChoice() {}",
                "function updateWeekLabel() {}",
                "function renderOffersWeek() {}",
                "function showOffers() { offersState.weekStart = offersWeek().start; asked.push(offersState.weekStart); }",
                *(
                    function_source(APP_JS, name)
                    for name in ("localIsoDate", "monthShort", "weekStartOf", "weekFrom", "getWeekBounds", "rememberWeekStart", "applyWeekStart")
                ),
                *(function_source(OFFERS_JS, name) for name in ("offersWeekOffset", "bonusWeekOf", "offersWeek", "offersWeekStartChanged")),
                "showOffers();",
                f"applyWeekStart({{ day: {answer}, source: 'mealie' }});",
                f"applyWeekStart({{ day: {answer}, source: 'mealie' }});",
                "console.log(JSON.stringify(asked));",
            ]
        )
        result: object = run_node(self, script)
        assert isinstance(result, list)
        return result

    def test_a_wrong_remembered_day_loads_the_right_bonus_week_after_the_status(self) -> None:
        # Zondag 15 november: met maandag als eerste dag hoort hij bij de bonusweek van 9 november, met zondag bij die van 16 november.
        self.assertEqual(self.offer_weeks("2026-11-15", remembered=1, answer=0), ["2026-11-09", "2026-11-16"])

    def test_a_day_that_does_not_change_the_bonus_week_asks_once(self) -> None:
        # Dinsdag 17 november: zondag of maandag als eerste dag, het is dezelfde bonusweek.
        self.assertEqual(self.offer_weeks("2026-11-17", remembered=1, answer=0), ["2026-11-16"])
        self.assertEqual(self.offer_weeks("2026-11-15", remembered=0, answer=0), ["2026-11-16"])


@unittest.skipUnless(shutil.which("node"), "Node is niet geïnstalleerd")
class OfferCartOnOpenTest(unittest.TestCase):
    """Zonder het wachten op de week vragen het scherm en het subtab Aanbiedingen het mandje meteen na elkaar op: dat is een aanvraag."""

    def run_cart(self, scenario: str) -> dict:
        top: str = OFFERS_CART_JS[: OFFERS_CART_JS.index("// AH is altijd een keuze")]
        script: str = "\n".join(
            [
                "const state = { linkedStores: { picnic: true } };",
                "const asked = [];",
                "const open = [];",
                "function jsonFetch(url) { asked.push(url); return new Promise((resolve, reject) => open.push({ resolve, reject })); }",
                "function paintOfferCart() {}",
                "const settle = async () => { for (let i = 0; i < 10; i += 1) await new Promise((resolve) => setImmediate(resolve)); };",
                top,
                function_source(OFFERS_CART_JS, "offerCartAvailable"),
                function_source(OFFERS_CART_JS, "offerCartCounts"),
                function_source(OFFERS_CART_JS, "loadOfferCart"),
                f"(async () => {{\n{scenario}\n}})();",
            ]
        )
        result: object = run_node(self, script)
        assert isinstance(result, dict)
        return result

    def test_two_loads_at_once_ask_the_store_once(self) -> None:
        result: dict = self.run_cart("""
loadOfferCart("ah"); loadOfferCart("ah"); await settle();
const during = asked.length;
open[0].resolve({ items: [{ id: "spam", quantity: 2 }] }); await settle();
loadOfferCart("ah"); await settle();
console.log(JSON.stringify({ during, asked, ready: offerCart.ready, spam: offerCart.counts.get("spam"), loading: offerCart.loading }));
""")
        self.assertEqual(result, {"during": 1, "asked": ["/api/ah/cart"], "ready": True, "spam": 2, "loading": ""})

    def test_a_failed_read_can_be_retried(self) -> None:
        result: dict = self.run_cart("""
loadOfferCart("ah"); await settle();
open[0].reject(new Error("niet gekoppeld")); await settle();
loadOfferCart("ah"); await settle();
console.log(JSON.stringify({ asked, ready: offerCart.ready }));
""")
        self.assertEqual(result, {"asked": ["/api/ah/cart", "/api/ah/cart"], "ready": False})

    def test_forcing_and_switching_stores_still_ask(self) -> None:
        result: dict = self.run_cart("""
loadOfferCart("ah"); loadOfferCart("ah", { force: true }); await settle();
loadOfferCart("jumbo"); await settle(); // Jumbo heeft geen mandje hier: geen aanvraag, en de lopende van AH telt niet meer
loadOfferCart("ah"); await settle();
loadOfferCart("picnic"); await settle();
console.log(JSON.stringify({ asked }));
""")
        self.assertEqual(result["asked"], ["/api/ah/cart", "/api/ah/cart", "/api/ah/cart", "/api/picnic/cart"])


class WeekStartHintPageTest(AppTestCase):
    """De pagina draagt de eerste dag van de week die de server al weet (mealie.week_start_hint) als data-week-start op <html>,
    zodat een vers browserprofiel bij Aanbiedingen niet op /api/mealie/status hoeft te wachten."""

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
        self.client = self.client_for(create_app())

    async def week_start_attribute(self, path: str = "/offers") -> str | None:
        reply = await self.client.get(path)
        self.assertEqual(reply.status_code, 200)
        tag: re.Match[str] | None = re.search(r"<html\b[^>]*>", reply.get_data(as_text=True))
        assert tag is not None
        found: re.Match[str] | None = re.search(r'\sdata-week-start="([^"]*)"', tag.group(0))
        return found.group(1) if found else None

    async def link_mealie(self) -> None:
        await auth.set_secret("MEALIE_URL", "https://mealie.example.nl")
        await auth.set_secret("MEALIE_API_TOKEN", "eyJhbGciOiJIUzI1NiJ9.geheime-mealie-token.handtekening")

    async def test_without_mealie_the_page_says_monday(self) -> None:
        self.assertEqual(await self.week_start_attribute(), "1")
        self.assertEqual(await self.week_start_attribute("/"), "1", "elke pagina van de app")

    async def test_the_fixed_day_of_the_owner(self) -> None:
        await auth.set_setting(mealie.SETTING_WEEK_START, "0")
        self.assertEqual(await self.week_start_attribute(), "0")

    async def test_what_mealie_said_earlier(self) -> None:
        await self.link_mealie()
        mealie._week_start_cache["https://mealie.example.nl"] = (0.0, 6)
        self.assertEqual(await self.week_start_attribute(), "6")

    async def test_no_attribute_while_mealie_has_not_answered(self) -> None:
        # Dan wacht Aanbiedingen op /api/mealie/status, zoals voorheen (of gebruikt de dag die het toestel onthield).
        await self.link_mealie()
        self.assertIsNone(await self.week_start_attribute())


if __name__ == "__main__":
    unittest.main()
