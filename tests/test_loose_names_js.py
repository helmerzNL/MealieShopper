"""De pagina Losse aankopen (static/loose_names.js): de functies die de kaart en de kop maken draaien hier echt, in Node (zoals
tests/test_order_views.py). Zonder Node slaan die tests over. De markup-tests lezen de template, de scripts en de CSS.

Ook de gedeelde koppelingen in de bestaande knoppen: `importedLinkLine` en `productLinkButton` dragen `data-shared`, en de dialoog van
product_links.js toont de bron en "Klopt niet" bij zo'n regel."""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
LOOSE = (STATIC / "loose_names.js").read_text(encoding="utf-8")
PRODUCT_LINKS = (STATIC / "product_links.js").read_text(encoding="utf-8")
ORDERS = (STATIC / "orders.js").read_text(encoding="utf-8")
HTML = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
CSS = (STATIC / "styles.css").read_text(encoding="utf-8")
AI_REVIEW = (STATIC / "ai_review.js").read_text(encoding="utf-8")


def function_source(name: str, source: str) -> str:
    match = re.search(
        rf"^(?:async )?function {re.escape(name)}\(.*?^}}$",
        source,
        re.DOTALL | re.MULTILINE,
    )
    if not match:
        raise AssertionError(f"functie {name} niet gevonden")
    return match.group(0)


LOOSE_FUNCTIONS: tuple[str, ...] = (
    "looseSelected",
    "looseYears",
    "loosePaid",
    "looseMeta",
    "looseImage",
    "looseCandidateHtml",
    "looseProposalHtml",
    "looseCardHtml",
    "looseStatusLines",
    "looseHeaderHtml",
    "looseRowHtml",
    "looseListsHtml",
    "looseMayAct",
    "loosePollWanted",
    "looseLock",
)

PRELUDE: str = "\n".join(
    [
        "const t = (key, params = {}) => key + (Object.keys(params).length ? ' ' + JSON.stringify(params) : '');",
        "const tn = (key, count, params = {}) => t(key + (count === 1 ? '.one' : '.other'), { count, ...params });",
        "const escapeHtml = (v) => String(v ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\"/g, '&quot;');",
        "const formatPrice = (v) => (Number(v || 0) ? 'EUR ' + Number(v).toFixed(2) : '');",
        "const formatSyncTime = (v) => 'T:' + v;",
        "const storeImage = (url) => url;",
        "const storeDisplayName = (store) => 'Winkel ' + store;",
        "const mdiIcon = () => '<svg></svg>';",
        "const MDI_IMAGE_OUTLINE = 'x';",
        *[function_source(name, LOOSE) for name in LOOSE_FUNCTIONS],
    ]
)

CARD: str = """{
  key: 'halfvolle melk', name: 'Halfvolle melk', bought: 12, firstYear: 2019, lastYear: 2023, paidMin: 0.99, paidMax: 1.19,
  candidates: [
    { productId: '1', title: 'AH Halfvolle melk', brand: 'AH', unitSize: '1 l', price: 1.19, image: 'https://img.example/1.jpg' },
    { productId: '2', title: 'Campina Halfvolle melk', brand: 'Campina', unitSize: '1,5 l', price: 1.89, image: null },
    { productId: '3', title: 'Biologische melk', brand: null, unitSize: '1 l', price: 1.59, image: 'https://img.example/3.jpg' },
  ],
  proposal: null,
}"""


@unittest.skipUnless(shutil.which("node"), "Node is niet geinstalleerd")
class LooseNamesNodeTest(unittest.TestCase):
    def run_json(self, scenario: str) -> object:
        script: str = f"{PRELUDE}\nconst card = {CARD};\n{scenario}"
        result = subprocess.run(
            ["node", "-e", script],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_card_preselects_the_ai_choice_and_shows_reason_and_unsure_pack(self):
        out = self.run_json(
            "card.proposal = { productId: '2', surePack: false, reason: 'zelfde merk en inhoud' };"
            "console.log(JSON.stringify({ html: looseCardHtml(card, 'ah'), selected: looseSelected(card) }));"
        )
        html: str = out["html"]
        self.assertEqual(out["selected"], "2")
        self.assertEqual(
            re.findall(r'<input type="radio"[^>]*value="(\d)"[^>]*checked', html), ["2"]
        )
        self.assertEqual(html.count(" checked"), 1)
        self.assertIn("zelfde merk en inhoud", html)
        self.assertIn("loose.unsure_pack", html)
        self.assertRegex(
            html,
            r"<button[^>]*data-loose-confirm>",
            "met een keuze is Koppel gekozen product niet uitgeschakeld",
        )
        self.assertIn("loose.bought", html)
        self.assertIn("&quot;years&quot;:&quot;2019-2023&quot;", html)
        self.assertIn("loose.paid_range", html)

    def test_a_sure_pack_does_not_say_unsure(self):
        html = self.run_json(
            "card.proposal = { productId: '1', surePack: true, reason: 'dezelfde inhoud' };"
            "console.log(JSON.stringify(looseCardHtml(card, 'ah')));"
        )
        self.assertNotIn("loose.unsure_pack", html)
        self.assertIn("dezelfde inhoud", html)

    def test_card_without_proposal_or_with_choice_zero_selects_nothing_and_disables_link_button(
        self,
    ):
        out = self.run_json(
            "const none = looseCardHtml(card, 'ah');"
            "card.proposal = { productId: null, surePack: true, reason: 'geen goede match' };"
            "const zero = looseCardHtml(card, 'ah');"
            "card.proposal = { productId: '99', surePack: true, reason: 'onbekend nummer' };"
            "const unknown = looseCardHtml(card, 'ah');"
            "console.log(JSON.stringify({ none, zero, unknown, selected: looseSelected(card) }));"
        )
        for name in ("none", "zero", "unknown"):
            self.assertNotIn(" checked", out[name], name)
            self.assertRegex(
                out[name], r"<button[^>]*data-loose-confirm disabled>", name
            )
        self.assertIn("loose.ai_none", out["zero"])
        self.assertIn("geen goede match", out["zero"])
        self.assertIsNone(out["selected"])

    def test_card_escapes_name_title_and_reason(self):
        evil: str = '<img src=x onerror=1> " onfocus="x'
        out = self.run_json(
            f"card.name = {json.dumps(evil)}; card.key = {json.dumps(evil)}; card.candidates[0].title = {json.dumps(evil)};"
            f"card.candidates[0].brand = {json.dumps(evil)};"
            f"card.proposal = {{ productId: '1', surePack: true, reason: {json.dumps(evil)} }};"
            "console.log(JSON.stringify(looseCardHtml(card, 'ah') + looseListsHtml({ missing: [{ key: card.key, name: card.name, bought: 1 }],"
            " rejected: [{ key: card.key, name: card.name, bought: 1 }] }, 'ah')));"
        )
        self.assertNotIn("<img src=x", out)
        self.assertIn("&lt;img src=x onerror=1&gt;", out)
        self.assertIn("&quot; onfocus=&quot;x", out)
        self.assertNotIn('" onfocus="', out, "een aanhalingsteken uit een naam breekt niet uit zijn attribuut")

    def test_image_only_for_https(self):
        html = self.run_json(
            "card.candidates = ['http://img.example/a.jpg', 'javascript:alert(1)', 'data:image/png;base64,AAAA', '//img.example/b.jpg', 'https://img.example/c.jpg']"
            ".map((image, i) => ({ productId: String(i), title: 't' + i, image }));"
            "console.log(JSON.stringify(looseCardHtml(card, 'ah')));"
        )
        self.assertEqual(
            re.findall(r'<img src="([^"]+)"', html), ["https://img.example/c.jpg"]
        )
        self.assertEqual(
            html.count("link-item__photo"),
            4,
            "de andere vier krijgen de plaatsvervanger",
        )

    def test_header_shows_counts_pause_and_ai_button_only_when_allowed(self):
        out = self.run_json(
            "const base = { total: 7, toSearch: 3, pausedUntil: null, running: false, ai: { configured: false, nightly: false }, lastRound: null };"
            "console.log(JSON.stringify({"
            " plain: looseHeaderHtml(base, 'ah'),"
            " ai: looseHeaderHtml({ ...base, ai: { configured: true, nightly: false } }, 'ah'),"
            " nightly: looseHeaderHtml({ ...base, ai: { configured: true, nightly: true } }, 'ah'),"
            " paused: looseHeaderHtml({ ...base, pausedUntil: '2026-11-12T08:00:00' }, 'ah'),"
            " running: looseHeaderHtml({ ...base, running: true }, 'ah'),"
            " one: looseHeaderHtml({ ...base, total: 1, toSearch: 1 }, 'ah'),"
            "}));"
        )
        plain: str = out["plain"]
        self.assertIn("loose.proposals.other", plain)
        self.assertIn("&quot;count&quot;:7", plain)
        self.assertIn("&quot;count&quot;:3", plain)
        self.assertIn("Winkel ah", plain)
        self.assertIn('id="loose-export-slot"', plain)
        self.assertNotIn('id="loose-export"', plain, "de knop zelf is een vast element, buiten de kop")
        self.assertRegex(
            plain,
            r'<button[^>]*id="loose-run"[^>]*data-loose-run>',
            "Nu ophalen staat aan",
        )
        self.assertNotIn("loose-ai-run", plain, "zonder AI-sleutel geen knop")
        self.assertIn("loose-ai-run", out["ai"])
        self.assertNotIn(
            "loose-ai-run", out["nightly"], "de AI stelt al 's nachts voor: geen knop"
        )
        self.assertIn("loose.paused", out["paused"])
        self.assertIn("T:2026-11-12T08:00:00", out["paused"])
        self.assertRegex(out["paused"], r'id="loose-run"[^>]* disabled>')
        self.assertIn("loose.running", out["running"])
        self.assertRegex(out["running"], r'id="loose-run"[^>]* disabled>')
        self.assertIn("loose.proposals.one", out["one"])

    def test_lists_show_missing_with_search_and_rejected_with_restore(self):
        html = self.run_json(
            "console.log(JSON.stringify(looseListsHtml({ missing: [{ key: 'a', name: 'Aap', bought: 2 }],"
            " rejected: [{ key: 'b', name: 'Beer', bought: 1 }] }, 'jumbo')));"
        )
        self.assertEqual(html.count("<details"), 2)
        self.assertNotIn("<details open", html, "ingeklapt")
        self.assertRegex(
            html,
            r'(?s)data-loose-row="a".*?data-product-link data-store="jumbo" data-name="Aap"',
        )
        self.assertRegex(html, r'(?s)data-loose-row="b".*?data-loose-restore')
        empty = self.run_json(
            "console.log(JSON.stringify(looseListsHtml({ missing: [], rejected: [] }, 'ah')));"
        )
        self.assertEqual(empty.strip(), "")

    def test_search_own_button_carries_what_the_link_dialog_reads(self):
        html = self.run_json(
            "console.log(JSON.stringify(looseCardHtml(card, 'picnic')));"
        )
        self.assertIn(
            'data-product-link data-store="picnic" data-name="Halfvolle melk"', html
        )


@unittest.skipUnless(shutil.which("node"), "Node is niet geinstalleerd")
class SharedLinkButtonsNodeTest(unittest.TestCase):
    """Wat taak 2 aan de bestaande knoppen en de dialoog toevoegde, zonder dat een test het las."""

    PRELUDE: str = "\n".join(
        [
            "const t = (key, params = {}) => key;",
            "const escapeHtml = (v) => String(v ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\"/g, '&quot;');",
            "const mdiIcon = () => '';",
            "const MDI_LINK = 'x';",
            "const productLinks = { line: null, results: [], run: 0 };",
            "const elements = {};",
            "const $ = (selector) => (elements[selector] ??= { hidden: false, textContent: '', value: '', innerHTML: '', open: false, focus() {}, showModal() { this.open = true; } });",
            "const message = () => {};",
            "const searchProductLink = () => {};",
            "const setProductLinkBusy = () => {};",
            function_source("importedLinkLine", ORDERS),
            function_source("productLinkStoreName", PRODUCT_LINKS),
            function_source("productLinkButton", PRODUCT_LINKS),
            function_source("openProductLink", PRODUCT_LINKS),
        ]
    )

    def run_json(self, scenario: str) -> object:
        result = subprocess.run(
            ["node", "-e", f"{self.PRELUDE}\n{scenario}"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_imported_link_line_and_button_carry_data_shared_only_for_a_shared_line(
        self,
    ):
        out = self.run_json(
            "const shared = importedLinkLine({ title: 'Melk', productId: '1', productTitle: 'AH Melk', shared: true }, 'ah');"
            "const plain = importedLinkLine({ title: 'Melk', productId: '1', productTitle: 'AH Melk' }, 'ah');"
            "console.log(JSON.stringify({ shared, plain, sharedButton: productLinkButton(shared), plainButton: productLinkButton(plain) }));"
        )
        self.assertIs(out["shared"]["shared"], True)
        self.assertIs(out["plain"]["shared"], False)
        self.assertIn('data-shared="1"', out["sharedButton"])
        self.assertIn('data-shared=""', out["plainButton"])
        self.assertNotIn('data-shared="1"', out["plainButton"])

    def test_dialog_shows_the_shared_source_and_not_right_action_for_a_shared_line_only(
        self,
    ):
        out = self.run_json(
            "const open = (data) => { openProductLink({ dataset: { store: 'ah', name: 'Melk', product: 'AH Melk', productId: '1', ...data } });"
            " return { source: $('#link-dialog-shared').hidden, reject: $('#link-dialog-reject').hidden, unlink: $('#link-dialog-unlink').hidden }; };"
            "console.log(JSON.stringify({ shared: open({ shared: '1' }), plain: open({ shared: '' }),"
            " own: open({ shared: '1', kind: 'name', key: 'melk' }) }));"
        )
        self.assertEqual(
            out["shared"], {"source": False, "reject": False, "unlink": True}
        )
        self.assertEqual(out["plain"], {"source": True, "reject": True, "unlink": True})
        self.assertEqual(
            out["own"]["source"],
            True,
            "een eigen koppeling gaat voor het gedeelde bestand",
        )
        self.assertEqual(out["own"]["reject"], True)
        self.assertEqual(out["own"]["unlink"], False)
        self.assertIn('id="link-dialog-shared"', HTML)
        self.assertIn('id="link-dialog-reject"', HTML)


class LooseNamesMarkupTest(unittest.TestCase):
    def test_the_subtab_is_admin_only_and_the_panel_has_what_the_script_fills(self):
        subtab = re.search(
            r'<a class="subtab"[^>]*data-subtab="ah-items-losse-aankopen"[^>]*>', HTML
        )
        self.assertIsNotNone(subtab)
        self.assertIn("data-admin-only", subtab.group(0))
        self.assertIn('href="/stores/ah/loose"', subtab.group(0))
        for element in (
            "loose-view",
            "loose-header",
            "loose-message",
            "loose-cards",
            "loose-empty",
            "loose-more",
            "loose-lists",
        ):
            self.assertIn(f'id="{element}"', HTML, element)
        self.assertIn('id="ah-items-losse-aankopen" class="subpanel"', HTML)

    def test_the_script_loads_after_the_link_dialog_and_before_the_router(self):
        scripts = re.findall(r"filename='([\w/]+\.js)'", HTML)
        self.assertLess(
            scripts.index("product_links.js"), scripts.index("loose_names.js")
        )
        self.assertLess(scripts.index("loose_names.js"), scripts.index("router.js"))

    def test_the_subtab_loads_the_page_and_every_store_has_the_section(self):
        app = (STATIC / "app.js").read_text(encoding="utf-8")
        self.assertRegex(app, r'"ah-items-losse-aankopen"[^\n]*loadLooseNames\(\)')
        sections = app[
            app.index("const STORE_SECTIONS = {") : app.index(
                "function itemsOpenSubtab"
            )
        ]
        self.assertEqual(sections.count('loose: "ah-items-losse-aankopen"'), 4)

    def test_nothing_is_sent_from_the_page_but_the_key_and_the_chosen_product(self):
        confirm = function_source("looseConfirm", LOOSE)
        self.assertIn(
            "{ store: looseState.store, key, productId: picked.value }", confirm
        )
        self.assertNotIn("price", confirm)
        self.assertNotIn("title", confirm)

    def test_the_link_dialog_hook_and_the_error_message_pattern_are_used(self):
        self.assertIn('"ms:product-links"', LOOSE)
        self.assertNotIn("catch {", LOOSE, "geen stille catch")
        self.assertEqual(
            LOOSE.count("catch (error)"),
            LOOSE.count('message("#loose-message", error.message)'),
        )

    def test_the_nightly_checkbox_sits_in_the_ai_login_block_and_is_saved(self):
        block = HTML[
            HTML.index('id="ai-login-body"') : HTML.index('id="ai-review-message"')
        ]
        self.assertIn('id="ai-loose-nightly"', block)
        self.assertIn('data-i18n="aireview.loose_nightly"', block)
        self.assertIn("looseNightly", AI_REVIEW)

    def test_css_uses_tokens_and_no_new_breakpoint(self):
        block = CSS[CSS.index(".loose-header {") :]
        widths = re.findall(r"@media \(\s*(?:min|max)-width:\s*(\d+)px", block)
        self.assertEqual(set(widths), {"640"}, "alleen het bestaande breekpunt van een telefoon")
        self.assertNotRegex(block, r"border-radius: \d")


LOOSE_ASYNC: tuple[str, ...] = ("loosePost", "looseWantsPoll", "looseTick")


@unittest.skipUnless(shutil.which("node"), "Node is niet geinstalleerd")
class LooseNamesBehaviourNodeTest(unittest.TestCase):
    """Wanneer een kaart iets mag doen en wanneer de poll draait, met nagemaakte knoppen en een nagemaakt netwerk."""

    SETUP: str = "\n".join(
        [
            PRELUDE,
            *[function_source(name, LOOSE) for name in LOOSE_ASYNC],
            "const LOOSE_POLL_MS = 5000;",
            "const looseState = { run: 1, running: true, stalled: false, pollFailed: false, timer: 0 };",
            "const log = { fetches: [], messages: [], scheduled: 0, loaded: 0, busy: [] };",
            "let reply = () => Promise.resolve({ ok: true });",
            "const jsonFetch = (url, options) => { log.fetches.push(url); return reply(url, options); };",
            "const message = (target, text) => log.messages.push(text);",
            "const setBusy = (button, busy) => log.busy.push(busy);",
            "const looseQuery = () => 'q';",
            "const looseApply = () => {};",
            "const looseRenderHeader = () => {};",
            "const looseRenderMore = () => {};",
            "const looseSchedulePoll = () => { log.scheduled += 1; };",
            "const loadLooseNames = () => { log.loaded += 1; };",
            "let panel = true, subtab = true;",
            "const document = { hidden: false, querySelector: (selector) => (selector.startsWith('#ah-items.') ? panel : subtab) };",
            """const element = (extra = {}) => ({ disabled: false, ...extra });
const makeCard = (checked) => {
  const confirm = element({ matches: () => false });
  const reject = element();
  const radio = element();
  const holder = {
    dataset: {},
    confirm, reject, radio,
    querySelectorAll: () => [confirm, reject, radio],
    querySelector: (selector) => (selector === '[data-loose-confirm]' ? confirm : checked ? radio : null),
  };
  confirm.closest = () => holder; reject.closest = () => holder;
  return holder;
};""",
        ]
    )

    def run_json(self, scenario: str) -> object:
        script: str = f"{self.SETUP}\n(async () => {{ {scenario} }})();"
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_a_card_may_act_only_when_it_exists_and_is_not_busy(self):
        out = self.run_json(
            "console.log(JSON.stringify([looseMayAct(null), looseMayAct({ dataset: {} }), looseMayAct({ dataset: { busy: '1' } })]));"
        )
        self.assertEqual(out, [False, True, False])

    def test_the_poll_runs_only_for_a_running_round_of_the_current_load_on_a_visible_page(self):
        out = self.run_json(
            "const base = { running: true, run: 3, current: 3, panelActive: {}, subtabActive: {}, visible: true };"
            "console.log(JSON.stringify([loosePollWanted(base), loosePollWanted({ ...base, running: false }),"
            " loosePollWanted({ ...base, run: 2 }), loosePollWanted({ ...base, panelActive: null }),"
            " loosePollWanted({ ...base, subtabActive: null }), loosePollWanted({ ...base, visible: false })]));"
        )
        self.assertEqual(out, [True, False, False, False, False, False])

    def test_locking_a_card_disables_everything_and_unlocking_restores_the_confirm_rule(self):
        out = self.run_json(
            "const card = makeCard(false); looseLock(card, true);"
            "const locked = [card.dataset.busy, card.confirm.disabled, card.reject.disabled, card.radio.disabled];"
            "looseLock(card, false);"
            "const none = [card.dataset.busy ?? null, card.confirm.disabled, card.reject.disabled, card.radio.disabled];"
            "const chosen = makeCard(true); looseLock(chosen, true); looseLock(chosen, false);"
            "console.log(JSON.stringify({ locked, none, chosen: chosen.confirm.disabled }));"
        )
        self.assertEqual(out["locked"], ["1", True, True, True])
        self.assertEqual(out["none"], [None, True, False, False], "zonder keuze blijft Koppel uit")
        self.assertIs(out["chosen"], False)

    def test_a_card_ignores_a_second_action_while_the_first_request_is_in_flight(self):
        out = self.run_json(
            "let done; reply = () => new Promise((resolve) => { done = resolve; });"
            "const card = makeCard(true);"
            "const first = loosePost('confirm', {}, card.confirm);"
            "const second = await loosePost('reject', {}, card.reject);"
            "const during = { fetches: log.fetches.length, second, busy: card.dataset.busy, disabled: card.reject.disabled };"
            "done({ ok: true }); const result = await first;"
            "console.log(JSON.stringify({ during, result, after: card.dataset.busy, fetches: log.fetches.length }));"
        )
        self.assertEqual(out["during"], {"fetches": 1, "second": None, "busy": "1", "disabled": True})
        self.assertEqual(out["result"], {"ok": True})
        self.assertEqual(out["after"], "1", "na een gelukt verzoek blijft de kaart op slot: hij verdwijnt")
        self.assertEqual(out["fetches"], 1)

    def test_a_failed_request_unlocks_the_card_and_shows_the_error(self):
        out = self.run_json(
            "reply = () => Promise.reject(new Error('Dit product staat niet meer bij de voorstellen'));"
            "const card = makeCard(true);"
            "const result = await loosePost('confirm', {}, card.confirm);"
            "console.log(JSON.stringify({ result, busy: card.dataset.busy ?? null, confirm: card.confirm.disabled, messages: log.messages }));"
        )
        self.assertIsNone(out["result"])
        self.assertIsNone(out["busy"])
        self.assertIs(out["confirm"], False)
        self.assertIn("Dit product staat niet meer bij de voorstellen", out["messages"])

    def test_the_poll_stops_when_the_page_is_not_in_view_and_remembers_that(self):
        out = self.run_json(
            "panel = null; await looseTick(1);"
            "console.log(JSON.stringify({ fetches: log.fetches.length, stalled: looseState.stalled, scheduled: log.scheduled }));"
        )
        self.assertEqual(out, {"fetches": 0, "stalled": True, "scheduled": 0})

    def test_the_poll_goes_on_after_a_failed_request_and_keeps_the_message(self):
        out = self.run_json(
            "reply = () => Promise.reject(new Error('geen verbinding')); await looseTick(1);"
            "console.log(JSON.stringify({ scheduled: log.scheduled, failed: looseState.pollFailed, messages: log.messages }));"
        )
        self.assertEqual(out, {"scheduled": 1, "failed": True, "messages": ["geen verbinding"]})

    def test_the_poll_reloads_the_page_when_the_round_has_finished_and_clears_an_old_error(self):
        out = self.run_json(
            "reply = () => Promise.resolve({ running: false }); await looseTick(1);"
            "const finished = { loaded: log.loaded, scheduled: log.scheduled };"
            "looseState.pollFailed = true; reply = () => Promise.resolve({ running: true }); await looseTick(1);"
            "console.log(JSON.stringify({ finished, failed: looseState.pollFailed, messages: log.messages, scheduled: log.scheduled }));"
        )
        self.assertEqual(out["finished"], {"loaded": 1, "scheduled": 0})
        self.assertIs(out["failed"], False)
        self.assertEqual(out["messages"], [""])
        self.assertEqual(out["scheduled"], 1)
