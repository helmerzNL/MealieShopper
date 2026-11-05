"""De AH-pauze van het koppelen in beeld (static/linking.js): de melding boven de aankopenlijst en op een kassabon, en het vak bij
Instellingen > Koppelen. De renderfuncties draaien hier echt, in Node, uit de bronnen van de functies (zoals
tests/test_purchase_sort.py), met `t`, `escapeHtml` en de tijdnotatie als stub; zonder Node slaan die tests over. De markup-tests
lezen de template, de scripts en de CSS."""

import json
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
APP = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
ORDERS = (ROOT / "static" / "orders.js").read_text(encoding="utf-8")
LINKING_PATH = ROOT / "static" / "linking.js"
LINKING = LINKING_PATH.read_text(encoding="utf-8") if LINKING_PATH.exists() else ""
NL_JS = (ROOT / "static" / "i18n" / "nl.js").read_text(encoding="utf-8")
EN_JS = (ROOT / "static" / "i18n" / "en.js").read_text(encoding="utf-8")


def function_source(source, name):
    match = re.search(
        rf"^(?:async )?function {re.escape(name)}\(.*?^}}$",
        source,
        re.DOTALL | re.MULTILINE,
    )
    if not match:
        raise AssertionError(f"functie {name} niet gevonden")
    return match.group(0)


def const_source(source, name):
    match = re.search(
        rf"^const {re.escape(name)} = .*?;$", source, re.DOTALL | re.MULTILINE
    )
    if not match:
        raise AssertionError(f"constante {name} niet gevonden")
    return match.group(0)


# De pure functies; de tijdnotatie (linkingClock, linkingNext) heeft zijn eigen tests en is in de rest een stub.
PURE = (
    "linkingSpent",
    "linkingPauseText",
    "linkingBudgetText",
    "linkingNotice",
    "linkingButton",
    "linkingBox",
    "linkingLastRound",
    "receiptLinkCounts",
)


def tag(pattern: str, html: str) -> str:
    """Het eerste stuk markup dat op het patroon past; ontbreekt het, dan faalt de test met de markup erbij."""
    match = re.search(pattern, html)
    if not match:
        raise AssertionError(f"{pattern} niet gevonden in {html}")
    return match.group(0)


def prelude(clock_stub=True):
    lines = [
        "const t = (key, params = {}) => key + (Object.keys(params).length ? ' ' + Object.entries(params).map(([k, v]) => `${k}=${v}`).join(',') : '');",
        "const escapeHtml = (v) => String(v ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\"/g, '&quot;');",
        'const badge = (kind, text) => `<span class="badge badge--${kind}">${escapeHtml(text)}</span>`;',
        "const formatSyncTime = (v) => (v ? 'date(' + v + ')' : '');",
        "const I18N = { locale: () => 'nl-NL' };",
        const_source(LINKING, "LINKING_TRIGGERS"),
    ]
    if clock_stub:
        lines += [
            "const linkingClock = (v) => (v ? 'clock(' + v + ')' : '');",
            "const linkingNext = (v) => 'next(' + v + ')';",
        ]
    else:
        lines += [
            function_source(LINKING, "linkingClock"),
            function_source(LINKING, "linkingNext"),
        ]
    return "\n".join(lines + [function_source(LINKING, name) for name in PURE])


PAUSED = (
    '{ pausedUntil: "2026-11-06T02:02:00+00:00", pausedSince: "2026-11-05T14:02:00+00:00", pausedStatus: 429, '
    "budget: { used: 3, limit: 120, resetAt: null }, todo: { lines: 19, receipts: 6, images: 12 }, lastRound: null, "
    'nextRound: "2026-11-06T02:00:00+00:00", running: null }'
)
SPENT = (
    '{ pausedUntil: null, pausedSince: null, pausedStatus: null, budget: { used: 120, limit: 120, resetAt: "2026-11-05T15:10:00+00:00" }, '
    'todo: { lines: 19, receipts: 6, images: 12 }, lastRound: null, nextRound: "2026-11-06T02:00:00+00:00", running: null }'
)
TODO = (
    '{ pausedUntil: null, pausedSince: null, pausedStatus: null, budget: { used: 37, limit: 120, resetAt: "2026-11-05T15:10:00+00:00" }, '
    'todo: { lines: 19, receipts: 6, images: 12 }, lastRound: null, nextRound: "2026-11-06T02:00:00+00:00", running: null }'
)


class NodeCase(unittest.TestCase):
    """Een expressie uit linking.js in Node, met de prelude erboven; de uitkomst komt als JSON terug."""

    def render(self, expression: str, clock_stub: bool = True):
        return self.js(
            f"console.log(JSON.stringify({expression}));", clock_stub=clock_stub
        )

    def js(self, scenario: str, clock_stub: bool = True):
        script = f"{prelude(clock_stub)}\n{scenario}"
        env = {**os.environ, "TZ": "Europe/Amsterdam"}
        done = subprocess.run(
            ["node", "-e", script],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env=env,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)


@unittest.skipUnless(shutil.which("node"), "Node is niet geïnstalleerd")
class LinkingNoticeTest(NodeCase):

    def test_nothing_to_report_gives_no_notice(self):
        self.assertEqual(
            self.render(
                "linkingNotice({ pausedUntil: null, budget: { used: 3, limit: 120 }, todo: { lines: 0, receipts: 0, images: 0 }, running: null }, {})"
            ),
            "",
        )

    def test_no_status_yet_gives_no_notice(self):
        self.assertEqual(self.render("linkingNotice(null, {})"), "")

    def test_a_pause_shows_try_anyway_to_the_administrator_only(self):
        paused = '{ pausedUntil: "2026-11-06T02:02:00+00:00", pausedSince: "2026-11-05T14:02:00+00:00", pausedStatus: 429, budget: { used: 3, limit: 120 }, todo: { lines: 19, receipts: 6, images: 12 }, running: null }'
        regular = self.render(f"linkingNotice({paused}, {{ admin: false }})")
        self.assertNotIn("data-linking-run", regular)
        self.assertIn("orders.linking.paused", regular)
        self.assertIn(
            'data-force="1"', self.render(f"linkingNotice({paused}, {{ admin: true }})")
        )

    def test_a_pause_is_a_warning_with_the_times_the_status_and_what_waits(self):
        html = self.render(f"linkingNotice({PAUSED}, {{ admin: true }})")
        self.assertIn('class="message warning message--action', html)
        self.assertIn(
            "orders.linking.paused since=clock(2026-11-05T14:02:00+00:00),status=429,until=clock(2026-11-06T02:02:00+00:00)",
            html,
        )
        self.assertIn("orders.linking.todo_short lines=19,receipts=6", html)
        self.assertIn("orders.linking.try_anyway", html)
        self.assertNotIn("data-receipt-id", html)

    def test_a_pause_without_its_reason_still_names_the_end(self):
        html = self.render(
            'linkingNotice({ pausedUntil: "2026-11-06T02:02:00+00:00", pausedSince: null, pausedStatus: null, budget: { used: 0, limit: 120 }, todo: { lines: 0, receipts: 0, images: 0 }, running: null }, { admin: true })'
        )
        self.assertIn(
            "orders.linking.paused_until until=clock(2026-11-06T02:02:00+00:00)", html
        )
        self.assertNotIn("todo_short", html, "zonder werk geen regel over wat wacht")

    def test_a_spent_budget_disables_the_button(self):
        html = self.render(f"linkingNotice({SPENT}, {{ admin: true }})")
        self.assertIn('class="message warning message--action', html)
        self.assertIn(
            "orders.linking.budget used=120,limit=120,reset=clock(2026-11-05T15:10:00+00:00)",
            html,
        )
        button = tag(r"<button[^>]*>", html)
        self.assertIn("data-linking-run", button)
        self.assertIn("disabled", button)
        self.assertIn('data-force="0"', button)
        self.assertIn(
            'title="orders.linking.wait reset=clock(2026-11-05T15:10:00+00:00)"', button
        )
        self.assertIn("orders.linking.run<", html)

    def test_work_waiting_for_the_night_is_a_success_with_the_run_button(self):
        html = self.render(f"linkingNotice({TODO}, {{ admin: false }})")
        self.assertIn('class="message success message--action', html)
        self.assertIn(
            "orders.linking.todo lines=19,receipts=6,images=12,next=next(2026-11-06T02:00:00+00:00)",
            html,
        )
        button = tag(r"<button[^>]*>", html)
        self.assertIn("data-linking-run", button)
        self.assertNotIn("disabled", button)
        self.assertIn('data-force="0"', button, "Nu ophalen mag elke gebruiker")

    def test_a_receipt_notice_counts_its_unlinked_lines(self):
        html = self.render(
            f'linkingNotice({TODO}, {{ receiptId: "R1", unlinked: 19, total: 40 }})'
        )
        self.assertIn("orders.linking.receipt_todo unlinked=19,total=40", html)
        self.assertIn("orders.linking.run_receipt count=19", html)
        self.assertIn('data-receipt-id="R1"', html)
        self.assertNotIn(
            "orders.linking.todo ", html, "op een bon niet het werk van alle bonnen"
        )

    def test_a_receipt_without_unlinked_lines_has_no_notice_even_with_work_elsewhere(
        self,
    ):
        self.assertEqual(
            self.render(
                f'linkingNotice({TODO}, {{ receiptId: "R1", unlinked: 0, total: 40 }})'
            ),
            "",
        )
        self.assertEqual(
            self.render(
                f'linkingNotice({PAUSED}, {{ receiptId: "R1", unlinked: 0, total: 40, admin: true }})'
            ),
            "",
        )

    def test_a_receipt_during_a_pause_adds_the_pause_and_the_force_button_for_the_administrator(
        self,
    ):
        html = self.render(
            f'linkingNotice({PAUSED}, {{ receiptId: "R1", unlinked: 19, total: 40, admin: true }})'
        )
        self.assertIn("orders.linking.receipt_todo unlinked=19,total=40", html)
        self.assertIn("orders.linking.paused since=", html)
        self.assertIn('data-receipt-id="R1"', html)
        self.assertIn('data-force="1"', html)
        self.assertNotIn(
            "data-linking-run",
            self.render(
                f'linkingNotice({PAUSED}, {{ receiptId: "R1", unlinked: 19, total: 40, admin: false }})'
            ),
        )

    def test_a_receipt_with_a_spent_budget_disables_its_button(self):
        html = self.render(
            f'linkingNotice({SPENT}, {{ receiptId: "R1", unlinked: 4, total: 9 }})'
        )
        self.assertIn("orders.linking.budget used=120", html)
        self.assertIn("disabled", tag(r"<button[^>]*>", html))

    def test_a_running_round_shows_a_disabled_busy_button(self):
        running = TODO.replace("running: null", "running: { receiptId: null }")
        html = self.render(f"linkingNotice({running}, {{ admin: true }})")
        self.assertIn("orders.linking.running", html)
        self.assertNotIn(
            "data-linking-run", html, "een tweede ronde kan niet zolang er een loopt"
        )
        self.assertIn("disabled", tag(r"<button[^>]*>", html))

    def test_every_value_in_the_notice_is_escaped(self):
        html = self.render(
            f'linkingNotice({TODO}, {{ receiptId: "\\"><img src=x>", unlinked: 1, total: 2 }})'
        )
        self.assertNotIn("<img", html)
        self.assertIn('data-receipt-id="&quot;&gt;&lt;img src=x&gt;"', html)

    def test_the_counts_of_a_receipt_are_its_lines_with_an_article_number_and_no_product(
        self,
    ):
        counts = self.render(
            'receiptLinkCounts({ id: "R1", items: [{ productId: 1, product: null }, { productId: 2, product: { id: 9 } }, { productId: null, product: null }, { productId: 3, product: null }] })'
        )
        self.assertEqual(counts, {"receiptId": "R1", "unlinked": 2, "total": 4})


@unittest.skipUnless(shutil.which("node"), "Node is niet geïnstalleerd")
class LinkingBoxTest(NodeCase):
    def test_the_box_shows_state_meter_todo_last_and_next_round_and_both_buttons(self):
        html = self.render(f"linkingBox({TODO})")
        for needle in (
            "<dl",
            "settings.linking.state",
            "settings.linking.state_ok",
            "settings.linking.budget",
            "settings.linking.todo",
            "settings.linking.last_round",
            "settings.linking.next_round",
            "data-linking-images",
            "settings.linking.images",
            "settings.linking.meter used=37,limit=120",
            "settings.linking.todo_text lines=19,receipts=6,images=12",
            "next(2026-11-06T02:00:00+00:00)",
            "settings.linking.never",
        ):
            self.assertIn(needle, html)
        self.assertIn('<span class="linking-meter"', html)
        self.assertIn('<i style="width: 31%">', html, "37 van 120 is 31%")
        run = tag(r"<button[^>]*data-linking-run[^>]*>", html)
        self.assertIn('data-force="0"', run)
        self.assertIn("settings.linking.run<", html)

    def test_a_pause_in_the_box_offers_the_round_anyway(self):
        html = self.render(f"linkingBox({PAUSED})")
        self.assertIn("orders.linking.paused since=", html)
        self.assertIn(
            'data-force="1"',
            tag(r"<button[^>]*data-linking-run[^>]*>", html),
        )
        self.assertIn("settings.linking.run_anyway", html)

    def test_the_meter_never_runs_past_full(self):
        self.assertIn(
            '<i style="width: 100%">',
            self.render(f"linkingBox({SPENT.replace('used: 120', 'used: 130')})"),
        )
        self.assertIn(
            '<i style="width: 0%">',
            self.render(f"linkingBox({TODO.replace('used: 37', 'used: 0')})"),
        )

    def test_a_running_round_disables_both_buttons_in_the_box(self):
        html = self.render(
            f"linkingBox({TODO.replace('running: null', 'running: { receiptId: null }')})"
        )
        self.assertIn("orders.linking.running", html)
        self.assertEqual(len(re.findall(r"<button[^>]*disabled", html)), 2)

    def test_the_last_round_names_its_trigger_and_its_counts(self):
        last = '{ at: "2026-11-05T03:00:00+00:00", trigger: "nightly", searches: 40, found: 12, missing: 28, failed: 0, images: 9 }'
        html = self.render(f"linkingLastRound({last})")
        self.assertEqual(
            html,
            "settings.linking.last_round_text at=date(2026-11-05T03:00:00+00:00) (settings.linking.trigger.nightly),searches=40,found=12,missing=28,images=9",
        )

    def test_an_on_demand_round_without_work_says_there_was_nothing_to_do(self):
        for trigger in ("receipt", "manual", "open", "lookup", "list", "sync", "images"):
            with self.subTest(trigger=trigger):
                last = f'{{ at: "2026-11-05T14:00:00+00:00", trigger: "{trigger}", searches: 0, found: 0, missing: 0, failed: 0, images: 0 }}'
                self.assertEqual(
                    self.render(f"linkingLastRound({last})"),
                    f"settings.linking.nothing at=date(2026-11-05T14:00:00+00:00) (settings.linking.trigger.{trigger})",
                )

    def test_an_empty_nightly_round_still_shows_its_zero_counts(self):
        last = '{ at: "2026-11-05T03:00:00+00:00", trigger: "nightly", searches: 0, found: 0, missing: 0, failed: 0, images: 0 }'
        self.assertIn(
            "settings.linking.last_round_text", self.render(f"linkingLastRound({last})")
        )

    def test_an_unknown_trigger_keeps_the_time_without_a_name(self):
        last = '{ at: "2026-11-05T03:00:00+00:00", trigger: "<x>", searches: 1, found: 1, missing: 0, failed: 0, images: 0 }'
        self.assertTrue(
            self.render(f"linkingLastRound({last})").startswith(
                "settings.linking.last_round_text at=date(2026-11-05T03:00:00+00:00),"
            )
        )

    def test_every_trigger_of_the_backend_has_a_name(self):
        self.assertEqual(
            sorted(self.render("Object.keys(LINKING_TRIGGERS)")),
            sorted(["nightly", "manual", "receipt", "sync", "lookup", "open", "list", "images"]),
        )

    def test_a_box_without_a_next_round_says_so(self):
        self.assertIn(
            "next(null)",
            self.render(
                f"linkingBox({TODO.replace('nextRound: "2026-11-06T02:00:00+00:00"', 'nextRound: null')})"
            ),
        )


@unittest.skipUnless(shutil.which("node"), "Node is niet geïnstalleerd")
class LinkingTimeTest(unittest.TestCase):
    NOW = 'new Date("2026-11-05T14:30:00+00:00")'

    def run_js(self, expression):
        script = (
            f"{prelude(clock_stub=False)}\nconsole.log(JSON.stringify({expression}));"
        )
        env = {**os.environ, "TZ": "Europe/Amsterdam"}
        done = subprocess.run(
            ["node", "-e", script],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env=env,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def test_a_time_within_a_day_is_the_clock_time_only(self):
        self.assertEqual(
            self.run_js(f'linkingClock("2026-11-05T14:02:00+00:00", {self.NOW})'),
            "15:02",
        )
        self.assertEqual(
            self.run_js(f'linkingClock("2026-11-06T02:02:00+00:00", {self.NOW})'),
            "03:02",
        )

    def test_a_time_further_away_gets_the_date_too(self):
        self.assertEqual(
            self.run_js(f'linkingClock("2026-11-01T14:02:00+00:00", {self.NOW})'),
            "date(2026-11-01T14:02:00+00:00)",
        )
        self.assertEqual(self.run_js(f"linkingClock(null, {self.NOW})"), "")

    def test_the_next_round_tonight_says_tonight(self):
        self.assertEqual(
            self.run_js(f'linkingNext("2026-11-06T02:00:00+00:00", {self.NOW})'),
            "orders.linking.tonight time=03:00",
        )

    def test_a_next_round_later_today_or_another_day(self):
        self.assertEqual(
            self.run_js(f'linkingNext("2026-11-05T19:00:00+00:00", {self.NOW})'),
            "orders.linking.at_time time=20:00",
        )
        self.assertEqual(
            self.run_js(f'linkingNext("2026-11-08T02:00:00+00:00", {self.NOW})'),
            "orders.linking.at_date when=date(2026-11-08T02:00:00+00:00)",
        )
        self.assertEqual(
            self.run_js(f"linkingNext(null, {self.NOW})"), "orders.linking.next_off"
        )


@unittest.skipUnless(shutil.which("node"), "Node is niet geïnstalleerd")
class LinkingPollTest(unittest.TestCase):
    """Het pollen: één timer zolang er een ronde loopt, en na een ronde voor de open bon die bon opnieuw."""

    def scenario(self, body):
        stubs = """
const timers = [];
let cleared = 0;
globalThis.setTimeout = (fn, ms) => { timers.push({ fn, ms }); return timers.length; };
globalThis.clearTimeout = (id) => { if (id) cleared += 1; };
const calls = [];
let visible = true;
const linkingVisible = () => visible;
const linkingListVisible = () => visible;
const linkingRender = () => calls.push('render');
const refreshReceipt = (id) => calls.push('receipt:' + id);
const loadOrders = () => calls.push('orders');
const ordersState = { open: null, loaded: true };
let next = null;
let fail = false;
const jsonFetch = async (url) => { calls.push(url); if (fail) throw new Error('down'); return next; };
"""
        script = "\n".join(
            [
                const_source(LINKING, "LINKING_POLL_MS"),
            const_source(LINKING, "linkingState"),
                function_source(LINKING, "linkingSchedule"),
                function_source(LINKING, "linkingRoundDone"),
                function_source(LINKING, "linkingNoticeRefresh"),
            ]
        )
        full = f"{stubs}\n{script}\n(async () => {{\n{body}\n}})().catch((e) => {{ console.error(e); process.exit(1); }});"
        done = subprocess.run(
            ["node", "-e", full],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def test_a_running_round_polls_every_two_seconds_with_one_timer(self):
        result = self.scenario("""
next = { running: { receiptId: null } };
await linkingNoticeRefresh();
await linkingNoticeRefresh();
console.log(JSON.stringify({ timers: timers.map((x) => x.ms), cleared, timer: linkingState.timer }));
""")
        self.assertEqual(result["timers"], [2000, 2000])
        self.assertGreaterEqual(
            result["cleared"], 1, "de tweede verversing ruimt de eerste timer op"
        )
        self.assertEqual(result["timer"], 2)

    def test_no_poll_when_nothing_runs_or_the_places_are_hidden(self):
        result = self.scenario("""
next = { running: null };
await linkingNoticeRefresh();
const idle = timers.length;
next = { running: { receiptId: null } };
visible = false;
await linkingNoticeRefresh();
console.log(JSON.stringify({ idle, hidden: timers.length, timer: linkingState.timer }));
""")
        self.assertEqual(result, {"idle": 0, "hidden": 0, "timer": None})

    def test_a_finished_round_for_the_open_receipt_reloads_that_receipt(self):
        result = self.scenario("""
ordersState.open = { kind: "receipt", id: "R1" };
next = { running: { receiptId: "R1" } };
await linkingNoticeRefresh({ receiptId: "R1", unlinked: 3, total: 9 });
next = { running: null };
await linkingNoticeRefresh();
console.log(JSON.stringify(calls));
""")
        self.assertIn("receipt:R1", result)
        self.assertNotIn("orders", result)

    def test_a_finished_round_for_all_receipts_reloads_the_list_when_it_shows(self):
        result = self.scenario("""
next = { running: { receiptId: null } };
await linkingNoticeRefresh();
next = { running: null };
await linkingNoticeRefresh();
console.log(JSON.stringify(calls));
""")
        self.assertIn("orders", result)

    def test_a_failed_status_request_stops_the_poll_quietly(self):
        result = self.scenario("""
next = { running: { receiptId: null } };
await linkingNoticeRefresh();
const before = timers.length;
fail = true;
let threw = false;
try { await linkingNoticeRefresh(); } catch { threw = true; }
console.log(JSON.stringify({ before, after: timers.length, threw, timer: linkingState.timer }));
""")
        self.assertEqual(result, {"before": 1, "after": 1, "threw": False, "timer": None})


@unittest.skipUnless(shutil.which("node"), "Node is niet geïnstalleerd")
class LinkingRunTest(unittest.TestCase):
    """De knoppen: wat linkingRun naar /api/ah/linking/run stuurt. "Foto's aanvullen" vraagt alleen foto's, zonder zoekopdrachten."""

    def posted(self, attributes):
        stubs = """
const posted = [];
globalThis.fetch = async (url, init) => { posted.push({ url, body: JSON.parse(init.body) }); return { status: 202, ok: true, json: async () => ({ budget: {}, running: { receiptId: null } }) }; };
const message = () => {};
const t = (key) => key;
const linkingState = { status: null, timer: null, receipt: null };
const linkingRender = () => {};
const linkingRoundDone = () => {};
const linkingSchedule = () => {};
"""
        button = (
            "const button = { disabled: false, dataset: "
            + json.dumps(attributes["dataset"])
            + ", hasAttribute: (name) => "
            + json.dumps(attributes["attributes"])
            + ".includes(name), closest: () => null };"
        )
        full = "\n".join(
            [
                stubs,
                function_source(LINKING, "linkingRun"),
                button,
                "linkingRun(button).then(() => console.log(JSON.stringify(posted)));",
            ]
        )
        done = subprocess.run(
            ["node", "-e", full], capture_output=True, text=True, timeout=30, check=False
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def test_the_images_button_asks_for_photos_only(self):
        posted = self.posted(
            {"dataset": {"force": "0"}, "attributes": ["data-linking-images"]}
        )
        self.assertEqual(
            posted,
            [{"url": "/api/ah/linking/run", "body": {"force": False, "images": True}}],
        )

    def test_the_images_button_during_a_pause_forces_and_asks_for_photos(self):
        posted = self.posted(
            {"dataset": {"force": "1"}, "attributes": ["data-linking-images"]}
        )
        self.assertEqual(posted[0]["body"], {"force": True, "images": True})

    def test_the_run_button_does_not_ask_for_photos_only(self):
        posted = self.posted(
            {"dataset": {"force": "0"}, "attributes": ["data-linking-run"]}
        )
        self.assertEqual(posted[0]["body"], {"force": False})


class LinkingMarkupTest(unittest.TestCase):
    def test_the_three_places_exist_and_the_script_loads(self):
        for needle in (
            'id="ah-linking-notice"',
            'id="order-linking-notice"',
            'id="linking-box"',
        ):
            self.assertIn(needle, HTML)
        scripts = re.findall(r"filename='([\w/]+\.js)'", HTML)
        self.assertEqual(scripts[scripts.index("popover.js") + 1], "linking.js")

    def test_the_list_notice_sits_right_above_the_view_bar(self):
        self.assertRegex(
            HTML,
            r'<div id="ah-linking-notice"[^>]*></div>\s*<div class="purchase-bar" data-view-bar="purchase">',
        )

    def test_the_receipt_notice_sits_right_under_the_meta_line(self):
        self.assertRegex(
            HTML,
            r'<p class="muted" id="ah-order-meta"></p>\s*<div id="order-linking-notice"[^>]*></div>',
        )

    def test_the_settings_box_comes_after_the_link_statuses(self):
        section = HTML[HTML.index('id="settings-link"') :]
        self.assertLess(
            section.index('class="link-statuses"'), section.index('id="linking-box"')
        )
        self.assertLess(
            section.index('id="linking-box"'), section.index('id="offers-sync-box"')
        )
        self.assertIn(
            'data-i18n="settings.linking.title"',
            section[: section.index('id="offers-sync-box"')],
        )

    def test_every_place_names_the_message_box_for_its_errors(self):
        for place in ("ah-linking-notice", "order-linking-notice", "linking-box"):
            element = tag(rf'<div[^>]*id="{place}"[^>]*>', HTML)
            target = tag(r'data-linking-message="#[\w-]+"', element).split("#")[1].rstrip('"')
            self.assertIn(f'id="{target}"', HTML, place)

    def test_the_orders_page_refreshes_the_notice_after_the_list_and_on_a_receipt(self):
        load = function_source(ORDERS, "loadOrders")
        self.assertIn("linkingNoticeRefresh", load)
        opened = function_source(ORDERS, "openPurchase")
        self.assertIn("linkingNoticeRefresh(receiptLinkCounts(receipt))", opened)
        self.assertIn(
            "linkingForget",
            opened,
            "een andere aankoop haalt de melding van de vorige bon weg",
        )
        self.assertIn("refreshReceipt", ORDERS)

    def test_the_settings_tab_loads_the_box(self):
        self.assertIn(
            'subtab.dataset.subtab === "settings-link" && typeof loadLinkingBox === "function") loadLinkingBox();',
            APP,
        )

    def test_one_delegated_click_handler_posts_the_run(self):
        self.assertEqual(LINKING.count('document.addEventListener("click"'), 1)
        self.assertIn('"/api/ah/linking/run"', LINKING)
        self.assertIn("[data-linking-run]", LINKING)
        self.assertIn("[data-linking-images]", LINKING)
        self.assertIn("409", LINKING)
        self.assertIn("403", LINKING)

    def test_the_administrator_flag_follows_apply_role(self):
        self.assertIn(
            "!auth.enabled || auth.isOwner !== false",
            function_source(LINKING, "linkingAdmin"),
        )

    def test_the_meter_has_its_style(self):
        self.assertIn(".linking-meter {", CSS)
        self.assertIn(".linking-meter i {", CSS)

    def test_the_keys_of_the_brief_are_in_both_languages(self):
        keys = [
            "orders.linking." + k
            for k in (
                "paused",
                "budget",
                "todo",
                "receipt_todo",
                "run",
                "run_receipt",
                "try_anyway",
                "running",
                "busy",
            )
        ]
        keys += [
            "settings.linking." + k
            for k in (
                "title",
                "state",
                "budget",
                "todo",
                "last_round",
                "next_round",
                "last_round_text",
                "run",
                "run_anyway",
                "images",
            )
        ]
        keys += ["settings.linking.trigger." + k for k in re.findall(r"^  (\w+): ", const_source(LINKING, "LINKING_TRIGGERS"), re.MULTILINE)]
        self.assertIn("settings.linking.trigger.images", keys)
        for key in keys:
            self.assertTrue(f'  "{key}": ' in NL_JS, key)
            self.assertTrue(f'  "{key}": ' in EN_JS, key)
        self.assertTrue('"orders.linking.paused": "AH weigerde onze verzoeken om {since} ({status}). Koppelen en foto\'s ophalen wachten tot {until}."' in NL_JS)
        self.assertTrue('"orders.linking.run": "Nu ophalen"' in NL_JS)


if __name__ == "__main__":
    unittest.main()
