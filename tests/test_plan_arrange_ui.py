"""Het weekmenu bewerken (static/plan_drag.js): met de knop naast Synchroniseren sleep je een maaltijd naar een andere dag, en
blijft ze even op de pijl naar de vorige of volgende week staan, dan schuift het weekmenu een week op.

De pure functie draait hier echt, in Node. Zonder Node slaat die test over.
"""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
DRAG_JS = (STATIC / "plan_drag.js").read_text(encoding="utf-8")
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
CSS = (STATIC / "styles.css").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")


def function_source(source, name):
    match = re.search(rf"^(?:async )?function {re.escape(name)}\(.*?^}}$", source, re.S | re.M)
    if not match:
        raise AssertionError(f"functie {name} niet gevonden")
    return match.group(0)


class ToolbarTest(unittest.TestCase):
    def test_the_edit_button_sits_next_to_sync_and_is_a_toggle(self):
        toolbar = INDEX_HTML[INDEX_HTML.index('id="load-plan"') :]
        toolbar = toolbar[: toolbar.index("</div>")]
        button = re.search(r'<button[^>]*id="plan-edit-mode"[^>]*>', toolbar)
        self.assertIsNotNone(button, "de bewerkknop staat direct na de knop Synchroniseren")
        self.assertIn('aria-pressed="false"', button.group(0))
        self.assertIn("aria-label:plan.arrange", button.group(0))

    def test_the_hint_is_hidden_until_editing(self):
        hint = re.search(r'<p[^>]*id="plan-arrange-hint"[^>]*>', INDEX_HTML)
        self.assertIsNotNone(hint)
        self.assertIn("hidden", hint.group(0))

    def test_the_script_loads_after_app_js(self):
        scripts = re.findall(r"filename='([\w/]+\.js)'", INDEX_HTML)
        self.assertLess(scripts.index("app.js"), scripts.index("plan_drag.js"))


class MarkupTest(unittest.TestCase):
    def test_meals_and_days_carry_what_dragging_needs(self):
        self.assertIn('data-plan-entry="${id}"', function_source(APP_JS, "renderMeal"))
        self.assertIn('data-plan-date="${date}"', function_source(APP_JS, "renderMealPlan"))

    def test_a_tap_on_a_meal_opens_nothing_while_editing(self):
        handler = APP_JS[APP_JS.index('$("#meal-days").addEventListener("click"') :]
        handler = handler[: handler.index("\n});")]
        self.assertLess(handler.index(".is-arranging .meal--planned"), handler.index("openMealRecipe"))

    def test_only_the_meals_block_scrolling_on_a_phone(self):
        # Op een telefoon scroll je via de rest van de dag; alleen de maaltijd zelf vangt de vinger.
        block = re.search(r"\.is-arranging \.meal--planned \{(.*?)\}", CSS, re.S)
        self.assertIsNotNone(block)
        self.assertIn("touch-action: none", block.group(1))
        self.assertNotRegex(CSS, r"\.is-arranging\s*\{[^}]*touch-action")


class DragTest(unittest.TestCase):
    def test_dragging_uses_pointer_events_for_mouse_and_touch(self):
        for event in ("pointerdown", "pointermove", "pointerup", "pointercancel"):
            self.assertIn(f'"{event}"', DRAG_JS)
        self.assertNotIn("draggable", DRAG_JS)

    def test_dropping_only_sends_the_new_day(self):
        move = function_source(DRAG_JS, "movePlanEntry")
        self.assertIn('"PATCH", { date }', move)
        self.assertIn("refreshMealPlanAfterChange([from, date])", move)

    def test_holding_on_an_arrow_moves_a_week_both_ways(self):
        hover = function_source(DRAG_JS, "planDragWeekHover")
        self.assertIn('button.id === "next-week" ? 1 : -1', hover)
        self.assertIn("changePlanWeek(", hover)
        self.assertIn('"#prev-week, #next-week"', function_source(DRAG_JS, "planDragUpdate"))


@unittest.skipUnless(shutil.which("node"), "Node ontbreekt")
class EntriesAfterMoveTest(unittest.TestCase):
    def run_move(self, entries, entry_id, date, days):
        script = "\n".join(
            [
                function_source(DRAG_JS, "planEntriesAfterMove"),
                f"console.log(JSON.stringify(planEntriesAfterMove({json.dumps(entries)}, {json.dumps(entry_id)}, {json.dumps(date)}, {json.dumps(days)})));",
            ]
        )
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    WEEK = ["2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30", "2026-10-31", "2026-11-01"]
    ENTRIES = [{"id": 1, "date": "2026-10-26", "title": "Pasta"}, {"id": 2, "date": "2026-10-27", "title": "Stamppot"}]

    def test_a_day_in_the_same_week(self):
        moved = self.run_move(self.ENTRIES, "1", "2026-10-28", self.WEEK)
        self.assertEqual(moved, [{"id": 1, "date": "2026-10-28", "title": "Pasta"}, self.ENTRIES[1]])

    def test_another_week_takes_it_out_of_this_week(self):
        self.assertEqual(self.run_move(self.ENTRIES, 1, "2026-11-04", self.WEEK), [self.ENTRIES[1]])


if __name__ == "__main__":
    unittest.main()
