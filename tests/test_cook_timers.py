"""De timers bij het koken (static/cook_timers.js, issue #309): tijden uit een stap halen, meer timers tegelijk, pauzeren,
afgaan, bewaren in localStorage, en de melding via de service worker (static/cook_sw.js).

De functies draaien hier echt, in Node, met een nagemaakte localStorage. Zonder Node slaan die tests over; de controles van
de pagina en de bestanden draaien altijd.
"""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TIMERS_JS = (ROOT / "static" / "cook_timers.js").read_text(encoding="utf-8")
WORKER_JS = (ROOT / "static" / "cook_sw.js").read_text(encoding="utf-8")
VIEW_JS = (ROOT / "static" / "recipe_view.js").read_text(encoding="utf-8")
HTML = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")

PRELUDE = r"""
const t = (key, vars) => vars ? `${key}${JSON.stringify(vars)}` : key;
const cookFormat = (value) => String(value);
function mdiIcon(path, className = "mdi") { return `<svg class="${className}"><path d="${path}"/></svg>`; }
function escapeHtml(value) {
  return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");
}
function $() { return null; }
const store = new Map();
globalThis.localStorage = {
  getItem: (key) => (store.has(key) ? store.get(key) : null),
  setItem: (key, value) => store.set(key, String(value)),
  removeItem: (key) => store.delete(key),
};
const out = (value) => process.stdout.write(JSON.stringify(value));
const NOW = 1700000000000;
"""


def timers_source():
    """De constanten en functies van cook_timers.js, zonder de luisteraars en het starten onderaan (die hebben een pagina nodig)."""
    consts = re.findall(r"^(?:const|let) \w+ = .*$", TIMERS_JS, re.M)
    functions = re.findall(r"^(?:async )?function \w+\(.*?^}$", TIMERS_JS, re.S | re.M)
    return "\n".join(consts) + "\n" + "\n".join(functions)


@unittest.skipUnless(shutil.which("node"), "Node is niet geïnstalleerd")
class CookTimersJsTest(unittest.TestCase):
    def run_js(self, body):
        script = "\n".join([PRELUDE, timers_source(), body])
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
        if result.returncode:
            self.fail(result.stderr[-2000:])
        return json.loads(result.stdout)

    # ---- Tijden uit een stap
    def test_the_times_in_a_step(self):
        cases = {
            "Kook de pasta in 10-12 minuten beetgaar.": [600],
            "Bak 5 min. en daarna nog 5 minuten.": [300],
            "Laat 1 uur en 15 minuten sudderen.": [4500],
            "Laat een kwartier rusten.": [900],
            "Stoof anderhalf uur.": [5400],
            "Laat een half uur marineren.": [1800],
            "Rooster 30 sec.": [30],
            "Laat het deeg 2u rijzen.": [7200],
            "Bak 1,5 uur.": [5400],
            "Kook 5 tot 7 minuten.": [300],
            "Bak 3 minuten, keer om, bak 10 minuten en laat 20 min rusten. Serveer na 1 uur.": [180, 600, 1200],
            "Simmer for 20 minutes, then rest for ten minutes.": [1200, 600],
            "Verwarm de oven voor op 200 °C.": [],
            "Snijd 2 uien en 400 g kip.": [],
            "Voor 4 personen.": [],
        }
        found = self.run_js(f"out({json.dumps(list(cases))}.map((text) => cookDurations(text).map((item) => item.seconds)));")
        for text, seconds in zip(cases, found):
            with self.subTest(text):
                self.assertEqual(seconds, cases[text])

    def test_labels_and_the_clock(self):
        found = self.run_js("""out({
          labels: [600, 45, 4500, 7200].map(cookDurationLabel),
          clocks: [0, 999, 61000, 600000, 3600000, 3725000].map(cookClock),
        });""")
        self.assertEqual(found["labels"], [
            'cook.timer_minutes_short{"n":10}', 'cook.timer_seconds_short{"n":45}', 'cook.timer_hours{"n":1} cook.timer_minutes_short{"n":15}', 'cook.timer_hours{"n":2}',
        ])
        self.assertEqual(found["clocks"], ["0:00", "0:01", "1:01", "10:00", "1:00:00", "1:02:05"], "naar boven afgerond: bij 0:01 is er nog iets over")

    # ---- Meer timers tegelijk
    def test_adding_pausing_resuming_and_ringing(self):
        found = self.run_js("""
          const a = cookTimerAdd(600, { key: 'mealie:pasta', title: 'Pasta', step: 1 }, NOW);
          const again = cookTimerAdd(600, { key: 'mealie:pasta', title: 'Pasta', step: 1 }, NOW + 1000);
          const b = cookTimerAdd(60, { key: 'mealie:pasta', title: 'Pasta', step: 2 }, NOW);
          cookTimerPause(a, NOW + 100000);
          const paused = { state: a.state, left: cookTimerLeft(a, NOW + 500000) };
          const due = cookTimersDue(NOW + 61000).map((timer) => timer.id);
          cookTimerResume(a, NOW + 200000);
          const resumed = { state: a.state, end: a.end };
          const later = cookTimersDue(NOW + 700000).map((timer) => timer.id);
          cookTimerRemove(b.id);
          out({ same: again === a, count: cookTimers.list.length, paused, due, bId: b.id, resumed, later, aId: a.id, left: cookTimerLeft(a, NOW) });
        """)
        self.assertTrue(found["same"], "dezelfde tijd bij dezelfde stap geeft geen tweede timer")
        self.assertEqual(found["paused"], {"state": "pause", "left": 500000}, "een gepauzeerde timer houdt zijn resttijd")
        self.assertEqual(found["due"], [found["bId"]])
        self.assertEqual(found["resumed"], {"state": "run", "end": 1700000000000 + 200000 + 500000})
        self.assertEqual(found["later"], [found["aId"]])
        self.assertEqual((found["count"], found["left"]), (1, 0), "een timer die afgaat heeft niets meer over")

    def test_no_more_than_the_maximum(self):
        found = self.run_js("""
          const made = Array.from({ length: COOK_TIMERS_MAX + 2 }, (_, index) => cookTimerAdd(60 + index, { step: index }, NOW));
          out({ made: made.filter(Boolean).length, refused: made.filter((timer) => !timer).length, odd: [0, -5, NaN, COOK_TIMER_LIMIT + 1].map((value) => cookTimerAdd(value, {}, NOW)) });
        """)
        self.assertEqual((found["made"], found["refused"]), (8, 2))
        self.assertEqual(found["odd"], [None, None, None, None])

    # ---- Bewaren
    def test_timers_survive_a_reload(self):
        stored = [
            {"id": "run", "total": 600, "label": "", "title": "Pasta", "key": "k", "step": 1, "state": "run", "end": 1700000000000 + 300000, "left": 0},
            {"id": "pause", "total": 600, "state": "pause", "left": 120000, "end": 0},
            {"id": "just-done", "total": 60, "state": "run", "end": 1700000000000 - 60000},
            {"id": "long-done", "total": 60, "state": "run", "end": 1700000000000 - 3600000},
            {"id": "broken", "total": "veel", "state": "run", "end": 1700000000000 + 1000},
            {"total": 60, "state": "run", "end": 1700000000000 + 1000},
            "kapot",
        ]
        found = self.run_js(f"""
          localStorage.setItem(COOK_TIMERS_KEY, {json.dumps(json.dumps(stored))});
          const loaded = cookTimersLoad(NOW);
          cookTimers.list = loaded; cookTimersSave();
          localStorage.setItem('kapot', '{{');
          out({{ loaded: loaded.map((timer) => [timer.id, timer.state]), saved: JSON.parse(localStorage.getItem(COOK_TIMERS_KEY)).length }});
        """)
        self.assertEqual(found["loaded"], [["run", "run"], ["pause", "pause"], ["just-done", "ring"]],
                         "een timer die net afliep gaat alsnog af; een oude, kapotte of onbekende valt weg")
        self.assertEqual(found["saved"], 3)

    def test_without_storage_the_timers_still_run(self):
        found = self.run_js("""
          globalThis.localStorage = { getItem() { throw new Error('geen opslag'); }, setItem() { throw new Error('geen opslag'); }, removeItem() { throw new Error('geen opslag'); } };
          const loaded = cookTimersLoad(NOW);
          cookTimerAdd(60, {}, NOW); cookTimersSave();
          out({ loaded: loaded.length, running: cookTimers.list.length });
        """)
        self.assertEqual(found, {"loaded": 0, "running": 1})

    # ---- Weergave
    def test_the_floating_timers(self):
        found = self.run_js("""
          const a = cookTimerAdd(600, { title: 'Pasta', step: 2 }, NOW);
          const b = cookTimerAdd(30, { step: 3 }, NOW);
          cookTimersDue(NOW + 31000);
          out({ running: cookTimerHtml(a, NOW + 60000), ringing: cookTimerHtml(b, NOW + 60000), tray: cookTimersHtml(NOW), collapsed: (cookTimers.collapsed = true, cookTimersHtml(NOW)) });
        """)
        self.assertIn('data-cook-timer-clock>9:00<', found["running"])
        self.assertIn("data-cook-timer-toggle", found["running"])
        self.assertIn("cook.timer_step{&quot;n&quot;:&quot;2&quot;} · cook.timer_minutes_short{&quot;n&quot;:10}", found["running"])
        self.assertIn("is-ringing", found["ringing"])
        self.assertIn("cook.timer_done", found["ringing"])
        self.assertNotIn("data-cook-timer-toggle", found["ringing"], "een timer die afgaat kun je alleen stoppen")
        self.assertIn("data-cook-timer-stop", found["ringing"])
        self.assertIn('aria-expanded="true"', found["tray"])
        self.assertNotIn(" hidden>", found["collapsed"], "met een timer die afgaat klapt de lijst niet in")

    def test_the_buttons_under_a_step(self):
        found = self.run_js("""
          const step = { title: '', text: 'Kook 10-12 minuten en laat 5 min rusten.' };
          const plain = cookTimerRowHtml(step);
          cookTimers.custom = true;
          out({ plain, custom: cookTimerRowHtml(step), none: (cookTimers.custom = false, cookTimerRowHtml({ text: 'Proef.' })) });
        """)
        self.assertEqual(re.findall(r'data-cook-timer-start="(\d+)"', found["plain"]), ["600", "300"])
        self.assertIn("data-cook-timer-custom>", found["plain"])
        self.assertIn("data-cook-timer-form", found["custom"])
        self.assertIn('type="number" inputmode="numeric" min="1" max="600"', found["custom"])
        self.assertEqual(re.findall(r"data-cook-timer-start", found["none"]), [])
        self.assertIn("data-cook-timer-custom>", found["none"], "zelf een timer zetten kan altijd")


class CookTimersPageTest(unittest.TestCase):
    def test_the_script_loads_after_the_cooking_view(self):
        scripts = re.findall(r"filename='([\w/]+\.js)'", HTML)
        self.assertLess(scripts.index("recipe_view.js"), scripts.index("cook_timers.js"))
        self.assertLess(scripts.index("cook_timers.js"), scripts.index("recipe_search.js"))

    def test_the_cooking_mode_has_a_place_for_the_timers(self):
        dialog = HTML[HTML.index('<dialog id="cook-mode"'):]
        dialog = dialog[:dialog.index("</dialog>")]
        self.assertIn('id="cook-mode-timer-row"', dialog)
        tray = re.search(r'<section class="cook-timers" id="cook-timers"[^>]*>', HTML).group(0)
        self.assertIn("data-worker=\"{{ url_for('static', filename='cook_sw.js'", tray)
        self.assertIn(" hidden", tray)
        self.assertNotIn('id="cook-timers"', dialog, "de timers verhuizen mee naar een open venster, maar staan in de pagina")
        self.assertIn('window.dispatchEvent(new CustomEvent("ms:cookstep"', VIEW_JS)

    def test_storage_is_guarded_and_the_alarm_needs_a_tap(self):
        for function in re.findall(r"^(?:async )?function \w+\(.*?^}$", TIMERS_JS, re.S | re.M):
            if "localStorage." in function or "new Notification" in function or "navigator.vibrate" in function:
                self.assertIn("try {", function, function.split("(")[0])
        start = TIMERS_JS[TIMERS_JS.index("function cookTimerStartFromStep"):]
        start = start[:start.index("\n}")]
        self.assertIn("cookAudioReady()", start, "het geluid wordt bij de tik op Start klaargezet")
        self.assertIn("cookNotifyAsk()", start, "toestemming voor meldingen vragen we na een tik")

    def test_the_service_worker_only_shows_notifications(self):
        self.assertIn('addEventListener("notificationclick"', WORKER_JS)
        self.assertNotIn('"fetch"', WORKER_JS, "geen fetch-handler: de app werkt precies zoals zonder")
        self.assertNotIn("caches.", WORKER_JS)

    def test_the_styles(self):
        tray = re.search(r"\.cook-timers \{(.*?)\}", CSS, re.S).group(1)
        self.assertIn("position: fixed", tray)
        self.assertIn("pointer-events: none", tray, "alleen de timers zelf vangen een tik, niet de lege ruimte eromheen")
        self.assertIn("env(safe-area-inset-bottom)", tray)
        self.assertIn("var(--cook-timers-room, 0px)", re.search(r"\.cook-mode__stage \{(.*?)\}", CSS, re.S).group(1))
        motion = CSS[CSS.index(".cook-mode__nav .button"):]
        motion = motion[motion.index("@media (prefers-reduced-motion: reduce)"):]
        self.assertIn(".cook-timer.is-ringing", motion[:motion.index("}\n}")])


if __name__ == "__main__":
    unittest.main()
