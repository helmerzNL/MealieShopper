"""Terugkoppeling en bediening in één stijl (4.12.0): het eigen bevestigingsvenster en de toast in plaats van de vragen van de
browser, laadtoestanden met vaste ruimte, de grafiek als één Tab-stop, de app buiten bereik achter een overlay, en in de
stylesheet: hover alleen met een muis, tikvlakken van 44 px en randen van invoervelden met 3 : 1.

De functies uit static/app.js en static/offers_history.js draaien hier echt, in Node. Zonder Node slaan die tests over.
"""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
HISTORY_JS = (STATIC / "offers_history.js").read_text(encoding="utf-8")
CSS = (STATIC / "styles.css").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
ALL_JS = {script.name: script.read_text(encoding="utf-8") for script in sorted(STATIC.glob("*.js"))}


def function_source(source, name):
    match = re.search(rf"^(?:async )?function {re.escape(name)}\(.*?^}}$", source, re.S | re.M)
    if not match:
        raise AssertionError(f"functie {name} niet gevonden")
    return match.group(0)


def run_node(test, parts):
    result = subprocess.run(["node", "-e", "\n".join(parts)], capture_output=True, text=True, timeout=30, check=False)
    test.assertEqual(result.returncode, 0, result.stderr)
    return json.loads(result.stdout.strip().splitlines()[-1])


# Een <dialog> en twee knoppen, genoeg voor confirmDialog: luisteraars, showModal en close.
DIALOG_PAGE = r"""
const t = (key) => key;
function node(id) {
  const listeners = {};
  return {
    id, textContent: "", focused: false, open: false, classes: new Set(),
    classList: { toggle(name, on) { on ? this.owner.classes.add(name) : this.owner.classes.delete(name); }, owner: null },
    addEventListener(type, fn, options) { (listeners[type] ||= []).push({ fn, once: Boolean(options?.once) }); },
    removeEventListener(type, fn) { listeners[type] = (listeners[type] || []).filter((entry) => entry.fn !== fn); },
    emit(type, event = {}) { for (const entry of [...(listeners[type] || [])]) { if (entry.once) this.removeEventListener(type, entry.fn); entry.fn({ target: this, ...event }); } },
    count() { return Object.values(listeners).reduce((sum, list) => sum + list.length, 0); },
    focus() { for (const other of Object.values(nodes)) other.focused = false; this.focused = true; },
    showModal() { this.open = true; },
    close() { if (!this.open) return; this.open = false; this.emit("close"); },
  };
}
const nodes = {};
for (const id of ["confirm-dialog", "confirm-dialog-yes", "confirm-dialog-no", "confirm-dialog-text"]) { nodes[id] = node(id); nodes[id].classList.owner = nodes[id]; }
const $ = (selector) => nodes[selector.slice(1)];
"""


@unittest.skipUnless(shutil.which("node"), "Node is niet geïnstalleerd")
class ConfirmDialogTest(unittest.TestCase):
    def run_js(self, scenario):
        return run_node(self, [DIALOG_PAGE, function_source(APP_JS, "confirmDialog"), "(async () => {", scenario, "})();"])

    def test_the_answer_follows_the_button_escape_and_the_backdrop(self):
        out = self.run_js(
            """
            const dialog = nodes["confirm-dialog"], yes = nodes["confirm-dialog-yes"], no = nodes["confirm-dialog-no"];
            const answers = [];
            let asked = confirmDialog("Weet je het zeker?"); yes.emit("click"); answers.push(await asked);
            asked = confirmDialog("Weet je het zeker?"); no.emit("click"); answers.push(await asked);
            asked = confirmDialog("Weet je het zeker?"); dialog.close(); answers.push(await asked);                 // Escape
            asked = confirmDialog("Weet je het zeker?"); dialog.emit("click"); answers.push(await asked);           // naast het venster
            asked = confirmDialog("Weet je het zeker?"); dialog.emit("click", { target: yes }); const still = dialog.open; yes.emit("click"); answers.push(await asked);
            console.log(JSON.stringify({ answers, still, listeners: [dialog.count(), yes.count(), no.count()], text: nodes["confirm-dialog-text"].textContent }));
            """
        )
        self.assertEqual(out["answers"], [True, False, False, False, True])
        self.assertTrue(out["still"], "een tik in het venster sluit het niet")
        self.assertEqual(out["listeners"], [0, 0, 0], "na het antwoord blijft er geen luisteraar hangen")
        self.assertEqual(out["text"], "Weet je het zeker?")

    def test_a_dangerous_question_puts_the_focus_on_cancel(self):
        out = self.run_js(
            """
            const yes = nodes["confirm-dialog-yes"], no = nodes["confirm-dialog-no"];
            let asked = confirmDialog("Alles wissen?", { danger: true, confirm: "Wissen" });
            const danger = { focus: no.focused, red: yes.classes.has("button--danger"), label: yes.textContent, cancel: no.textContent };
            no.emit("click"); await asked;
            asked = confirmDialog("Doorgaan?");
            const plain = { focus: yes.focused, red: yes.classes.has("button--danger"), label: yes.textContent };
            yes.emit("click"); await asked;
            console.log(JSON.stringify({ danger, plain }));
            """
        )
        self.assertEqual(out["danger"], {"focus": True, "red": True, "label": "Wissen", "cancel": "common.cancel"})
        self.assertEqual(out["plain"], {"focus": True, "red": False, "label": "common.confirm"})

    def test_no_script_asks_with_the_dialog_of_the_browser(self):
        for name, source in ALL_JS.items():
            code = re.sub(r"//[^\n]*", "", source)
            self.assertNotRegex(code, r"window\.(confirm|alert)\(|(?<![\w.])(confirm|alert)\(", name)
        self.assertEqual(sum(source.count("confirmDialog(") for source in ALL_JS.values()), 15, "veertien vragen en de functie zelf")
        self.assertIn(
            '<dialog id="confirm-dialog" class="product-dialog confirm-dialog" role="alertdialog" aria-labelledby="confirm-dialog-text">', INDEX_HTML
        )


TOAST_PAGE = r"""
const t = (key) => key;
const timers = [];
const setTimeout = (fn, ms) => timers.push({ fn, ms });
function element(tag) {
  return {
    tag, className: "", textContent: "", attrs: {}, children: [], parent: null, listeners: {},
    setAttribute(name, value) { this.attrs[name] = value; },
    append(...kids) { for (const kid of kids) { kid.parent = this; this.children.push(kid); } if (this.tag === "div" || this.tag === "box") this.sync(); },
    sync() { if (this.tag === "div") this.textContent = this.children.map((kid) => kid.textContent).join(""); },
    remove() { this.parent.children = this.parent.children.filter((kid) => kid !== this); },
    addEventListener(type, fn) { this.listeners[type] = fn; },
    querySelectorAll(selector) { const cls = selector.slice(1); return this.children.filter((kid) => kid.className.split(" ").includes(cls)); },
  };
}
const box = element("box");
const document = { createElement: element };
const $ = (selector) => (selector === "#toasts" ? box : null);
"""


@unittest.skipUnless(shutil.which("node"), "Node is niet geïnstalleerd")
class ToastTest(unittest.TestCase):
    def test_a_success_goes_away_and_an_error_stays_until_closed(self):
        out = run_node(
            self,
            [
                TOAST_PAGE,
                function_source(APP_JS, "toast"),
                """
            toast("Opgeslagen");
            toast("Opgeslagen");                                   // dezelfde melding stapelt niet
            toast("Uitloggen is niet gelukt", "error");
            const shown = box.children.map((item) => ({ cls: item.className, role: item.attrs.role, text: item.children[0].textContent, close: item.children.length > 1 }));
            for (const timer of timers) timer.fn();                // vier seconden later
            const after = box.children.map((item) => item.className);
            box.children[0].children[1].listeners.click();         // Sluiten
            toast("");                                             // zonder tekst geen melding
            console.log(JSON.stringify({ shown, ms: timers.map((timer) => timer.ms), after, left: box.children.length }));
            """,
            ],
        )
        self.assertEqual(
            out["shown"],
            [
                {"cls": "toast toast--success", "role": "status", "text": "Opgeslagen", "close": False},
                {"cls": "toast toast--error", "role": "alert", "text": "Uitloggen is niet gelukt", "close": True},
            ],
        )
        self.assertEqual(set(out["ms"]), {4000})
        self.assertEqual(out["after"], ["toast toast--error"], "een fout verdwijnt niet vanzelf")
        self.assertEqual(out["left"], 0)

    def test_saving_a_preference_says_so(self):
        save = function_source(ALL_JS["appearance.js"], "appearanceSave")
        self.assertLess(save.index("state.auth = {"), save.index('toast(t("common.saved"))'))
        self.assertLess(save.index('toast(t("common.saved"))'), save.index("catch (error)"), "alleen als het bewaren lukte")
        self.assertIn('toast(t("common.saved"));', function_source(APP_JS, "savePreferOrganic"))
        self.assertIn('<div class="toasts" id="toasts"></div>', INDEX_HTML)


@unittest.skipUnless(shutil.which("node"), "Node is niet geïnstalleerd")
class LoadingAndChartTest(unittest.TestCase):
    def test_loading_reserves_room_and_is_announced(self):
        out = run_node(
            self,
            [
                "const t = (key) => key; const escapeHtml = (value) => String(value).replace('<', '&lt;'); const mdiIcon = (path, cls) => `<svg class=\"${cls}\"></svg>`; const MDI_LOADING = '';",
                function_source(APP_JS, "loadingHtml"),
                function_source(APP_JS, "mealPlanSkeletonHtml"),
                "console.log(JSON.stringify({ plain: loadingHtml(), named: loadingHtml('Mandje <laden>'), week: mealPlanSkeletonHtml([1, 2, 3, 4, 5, 6, 7]) }));",
            ],
        )
        self.assertEqual(
            out["plain"], '<div class="loading-box" role="status"><svg class="loading-box__icon"></svg><span>common.loading</span></div>'
        )
        self.assertIn("Mandje &lt;laden>", out["named"])
        self.assertEqual(out["week"].count('class="day-card day-card--skeleton" aria-hidden="true"'), 7)
        self.assertRegex(CSS, r"\.loading-box \{[^}]*min-height: 160px;")
        self.assertRegex(CSS, r"\.day-card--skeleton \{[^}]*min-height: 150px;")
        self.assertIn('$("#meal-days").innerHTML = mealPlanSkeletonHtml(week.days);', function_source(APP_JS, "loadMealPlan"))
        self.assertIn('content.innerHTML = loadingHtml(t("basket.loading"));', ALL_JS["basket.js"])
        self.assertEqual(sum(source.count('t("basket.loading")') for source in ALL_JS.values()), 1, "Mandje laden staat alleen nog bij het mandje")
        self.assertIn('message("#ah-orders-message", ordersState.loaded ? "" : t("common.loading"), "info");', ALL_JS["orders.js"])

    def test_the_chart_is_one_tab_stop_and_the_arrow_keys_walk_the_points(self):
        out = run_node(
            self,
            [
                function_source(HISTORY_JS, "offerChartNext"),
                """
            const moves = {};
            for (const [name, key, index, count] of [["first", "ArrowRight", -1, 5], ["next", "ArrowRight", 1, 5], ["last", "ArrowRight", 4, 5], ["prev", "ArrowLeft", 2, 5],
                ["start", "ArrowLeft", 0, 5], ["fromChart", "ArrowLeft", -1, 5], ["home", "Home", 3, 5], ["end", "End", 0, 5], ["other", "a", 2, 5], ["empty", "ArrowRight", -1, 0]]) {
              moves[name] = offerChartNext(key, index, count);
            }
            console.log(JSON.stringify(moves));
            """,
            ],
        )
        self.assertEqual(
            out, {"first": 0, "next": 2, "last": 4, "prev": 1, "start": 0, "fromChart": 0, "home": 0, "end": 4, "other": None, "empty": None}
        )
        chart = function_source(HISTORY_JS, "offerHistoryChart") if "function offerHistoryChart(" in HISTORY_JS else HISTORY_JS
        self.assertNotIn('tabindex="0"><title>', HISTORY_JS, "geen Tab-stop per balk of stip")
        self.assertEqual(HISTORY_JS.count('tabindex="-1" role="img" aria-label="${escapeHtml('), 5)
        self.assertIn('role="group" tabindex="0" aria-label=', chart)
        self.assertIn('document.addEventListener("keydown", offerChartKeys);', HISTORY_JS)

    def test_the_app_behind_an_overlay_is_out_of_reach(self):
        out = run_node(
            self,
            [
                """
            const layers = { "#auth-gate": { hidden: true }, "#invite-gate": { hidden: true }, "#onboarding": { hidden: true }, ".topbar": {}, "main.shell": {} };
            const document = { querySelector: (selector) => layers[selector] || null };
            """,
                function_source(APP_JS, "syncAppInert"),
                """
            const states = [];
            const read = () => [Boolean(layers[".topbar"].inert), Boolean(layers["main.shell"].inert)];
            syncAppInert(); states.push(read());
            layers["#onboarding"].hidden = false; syncAppInert(); states.push(read());
            layers["#onboarding"].hidden = true; layers["#auth-gate"].hidden = false; syncAppInert(); states.push(read());
            layers["#auth-gate"].hidden = true; syncAppInert(); states.push(read());
            delete layers["#invite-gate"]; syncAppInert(); states.push(read());
            console.log(JSON.stringify(states));
            """,
            ],
        )
        self.assertEqual(out, [[False, False], [True, True], [True, True], [False, False], [False, False]])
        self.assertIn("syncAppInert();", function_source(APP_JS, "renderAuthState"))
        self.assertEqual(ALL_JS["onboarding.js"].count('if (typeof syncAppInert === "function") syncAppInert();'), 2)


def luminance(colour):
    values = [int(colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4 for value in values]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(first, second):
    high, low = sorted((luminance(first), luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


class StylesheetTest(unittest.TestCase):
    def test_hover_styles_only_apply_with_a_mouse(self):
        # Op een aanraakscherm blijft een :hover-stijl na een tik hangen: elke regel staat dus in @media (hover: hover).
        bare = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)
        depth, media, outside = 0, [], []
        for match in re.finditer(r"([^{}]*)([{}])", bare):
            prelude, brace = match.group(1).strip(), match.group(2)
            if brace == "}":
                depth -= 1
                if media and media[-1][0] == depth:
                    media.pop()
                continue
            if prelude.startswith("@media"):
                media.append((depth, prelude))
            elif ":hover" in prelude and not any("hover" in query for _, query in media):
                outside.append(prelude)
            depth += 1
        self.assertEqual(outside, [])
        self.assertGreaterEqual(CSS.count("@media (hover: hover)"), 40)

    def test_small_controls_get_a_44_px_touch_area(self):
        block = re.search(r"@media \(pointer: coarse\) \{(.*?)\n\}\n", CSS, re.S).group(1)
        for selector in (
            ".qty__step::after",
            ".meal__edit::after",
            ".day-card__add--compact::after",
            ".purchase-table__action::after",
            ".offer-row__links .icon-button::after",
            ".list-card__action::after",
        ):
            self.assertIn(selector, block)
        self.assertRegex(block, r"width: 44px;\s*height: 44px;")
        self.assertRegex(block, r"\.ohc-range-button,\s*details\.help > summary,\s*\.user-menu__item \{\s*min-height: 44px;")

    def test_the_border_of_a_field_reaches_three_to_one_in_every_theme(self):
        blocks = re.findall(r"^:root[^{]*\{[^}]*--surface: (#[0-9a-fA-F]{6});[^}]*--control-border: (#[0-9a-fA-F]{6});", CSS, re.M)
        self.assertEqual(len(blocks), 3, "licht, donker en donkergrijs")
        for surface, border in blocks:
            self.assertGreaterEqual(contrast(surface, border), 3.0, f"{border} op {surface}")
        for selector in (r"\.input,\n\.textarea", r"\.store-select", r"\.review-search-input", r"\.qty__input"):
            self.assertRegex(CSS, selector + r" \{[^}]*border: 1px solid var\(--control-border\);")

    def test_the_first_tab_stop_skips_to_the_content(self):
        self.assertLess(
            INDEX_HTML.index('<a class="skip-link" href="#main" data-i18n="nav.skip">Naar de inhoud</a>'), INDEX_HTML.index('<header class="topbar">')
        )
        self.assertIn('<main class="shell" id="main" tabindex="-1">', INDEX_HTML)
        self.assertRegex(CSS, r"\.skip-link:focus \{\s*transform: none;")


class FormsAndTextsTest(unittest.TestCase):
    def test_every_field_has_a_name(self):
        for field in ("recipe-query", "import-url", "token-input", "jumbo-code", "invite-name", "audit-search"):
            tag = re.search(rf'<(?:input|textarea)[^>]*id="{field}"[^>]*>', INDEX_HTML).group(0)
            self.assertIn("aria-label=", tag, field)
            self.assertRegex(tag, r'data-i18n-attr="[^"]*aria-label:', field)
        self.assertRegex(
            INDEX_HTML,
            r'<label class="field field--inline"><span data-i18n="sec\.passkeys\.name">[^<]+</span><input class="input" id="new-passkey-name"',
        )

    def test_the_password_rule_stands_at_the_field_and_matches_the_server(self):
        from mealieshopper import auth_password, backup

        for field in ("invite-password", "pw-new"):
            self.assertRegex(
                INDEX_HTML,
                rf'id="{field}" type="password" autocomplete="new-password" minlength="{auth_password.MIN_PASSWORD_LENGTH}"[^>]*>\s*<small class="muted field__hint" data-i18n="field\.password_rule">Minstens {auth_password.MIN_PASSWORD_LENGTH} tekens',
            )
        self.assertRegex(
            INDEX_HTML,
            rf'id="backup-passphrase" type="password" autocomplete="new-password" minlength="{backup.MIN_PASSPHRASE_LENGTH}">\s*<small class="muted field__hint" data-i18n="backup\.passphrase_rule">Minstens {backup.MIN_PASSPHRASE_LENGTH} tekens',
        )

    def test_the_server_answers_in_the_language_of_the_user(self):
        for module in ("auth.py", "profile.py"):
            source = (ROOT / "mealieshopper" / module).read_text(encoding="utf-8")
            self.assertNotRegex(source, r'jsonify\(\{"error": "[A-Z]', module)

    def test_crisp_is_named_with_the_other_stores(self):
        nl = (STATIC / "i18n" / "nl.js").read_text(encoding="utf-8")
        for key in ("system.reset_loses_links", "backup.intro_more", "backup.links_label", "audit.intro"):
            line = re.search(rf'^  "{re.escape(key)}": "(.*)",$', nl, re.M).group(1)
            self.assertIn("Crisp", line, key)
        self.assertNotIn("Verversen", nl)
        self.assertNotIn("…", nl, "drie punten, geen beletselteken")


if __name__ == "__main__":
    unittest.main()
