// Het koppelen van bonregels aan AH-producten en het ophalen van hun foto's in beeld: een melding boven de aankopenlijst, een op
// een open kassabon en een vak bij Instellingen > Koppelen, alle drie uit GET /api/ah/linking (ah_orders.linking_status). Ligt het
// koppelen stil na een 403 of 429 van AH, is het uurbudget op of wacht er werk op de nacht, dan staat dat er met de knop
// "Nu ophalen" (POST /api/ah/linking/run, "Foto's aanvullen" stuurt images: true voor alleen foto's). Zolang een ronde loopt, vraagt de pagina elke 2 s de toestand opnieuw; na een ronde voor
// de open bon komt die bon opnieuw, met de foto's die erbij kwamen. Laadt na orders.js en popover.js.
//
// linkingNotice, linkingBox en de functies die ze gebruiken zijn puur (geen DOM), zodat tests/test_linking_ui.py ze in Node draait.

const LINKING_POLL_MS = 2000;

// De aanleidingen van een ronde (`trigger` in `receipt_products_last_round`, zie ah_orders.resolve_receipt_products) en hun naam.
const LINKING_TRIGGERS = {
  nightly: "settings.linking.trigger.nightly",
  manual: "settings.linking.trigger.manual",
  receipt: "settings.linking.trigger.receipt",
  sync: "settings.linking.trigger.sync",
  lookup: "settings.linking.trigger.lookup",
  open: "settings.linking.trigger.open",
  list: "settings.linking.trigger.list",
  images: "settings.linking.trigger.images",
};

// De laatste toestand van de route, de ene timer van het pollen, en de tellingen van de open bon (null als er geen open staat).
const linkingState = { status: null, timer: null, receipt: null };

// Een tijdstip in de melding: binnen een dag alleen de kloktijd ("14:02"), verder weg met de datum erbij.
function linkingClock(iso, now = new Date()) {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  if (Math.abs(date.getTime() - now.getTime()) >= 24 * 3600 * 1000) return formatSyncTime(iso);
  return date.toLocaleTimeString(I18N.locale(), { hour: "2-digit", minute: "2-digit" });
}

// De volgende nachtelijke ronde in woorden: "vannacht om 03:00", "om 20:00" (vandaag), of met de datum als hij verder weg ligt.
function linkingNext(iso, now = new Date()) {
  const date = iso ? new Date(iso) : null;
  if (!date || Number.isNaN(date.getTime())) return t("orders.linking.next_off");
  const hours = (date.getTime() - now.getTime()) / 3600000;
  if (hours < 0 || hours >= 24) return t("orders.linking.at_date", { when: formatSyncTime(iso) });
  const time = date.toLocaleTimeString(I18N.locale(), { hour: "2-digit", minute: "2-digit" });
  return t(date.getHours() < 7 ? "orders.linking.tonight" : "orders.linking.at_time", { time });
}

// De pauze in een zin. Zonder de reden (een pauze van vóór de reden werd bewaard) alleen tot wanneer.
function linkingPauseText(status, now = new Date()) {
  const until = linkingClock(status.pausedUntil, now);
  if (!status.pausedSince || !status.pausedStatus) return t("orders.linking.paused_until", { until });
  return t("orders.linking.paused", { since: linkingClock(status.pausedSince, now), status: status.pausedStatus, until });
}

function linkingBudgetText(budget, now = new Date()) {
  return t("orders.linking.budget", { used: budget.used, limit: budget.limit, reset: linkingClock(budget.resetAt, now) });
}

// Het uurbudget is op als er dit uur al `limit` zoekopdrachten waren.
function linkingSpent(budget) {
  return Number(budget?.limit) > 0 && Number(budget?.used) >= Number(budget.limit);
}

// De tellingen van wat er te doen is als woorden met hun meervoud ("1 artikel", "6 bonnen", "12 foto's"). `lines` telt de
// artikelnummers zonder product (ah_orders._todo), geen bonregels.
function linkingCounts(todo) {
  return {
    lines: tn("orders.linking.count.articles", Number(todo.lines) || 0),
    receipts: tn("orders.linking.count.receipts", Number(todo.receipts) || 0),
    images: tn("orders.linking.count.photos", Number(todo.images) || 0),
  };
}

// De knop van de melding. Loopt er een ronde, dan een uitgeschakelde "Bezig met ophalen". Op een bon waarvan geen regel aan de beurt
// is (`due` 0) een uitgeschakelde "Nu ophalen": een ronde zou niets opzoeken. Bij een pauze alleen de beheerder, met force (de route
// weigert force van een ander met 403). Bij een leeg uurbudget uitgeschakeld tot het weer kan. Het getal op de knop van een bon is
// `due`, de regels die een ronde opzoekt.
function linkingButton(status, { receiptId = null, due = 0, admin = false, now = new Date() } = {}) {
  if (status.running) return `<button class="button inline-button" type="button" disabled>${escapeHtml(t("orders.linking.running"))}</button>`;
  if (receiptId && !(due > 0)) return `<button class="button inline-button" type="button" disabled>${escapeHtml(t("orders.linking.run"))}</button>`;
  const receipt = receiptId ? ` data-receipt-id="${escapeHtml(receiptId)}"` : "";
  if (status.pausedUntil) {
    if (!admin) return "";
    return `<button class="button inline-button" type="button" data-linking-run${receipt} data-force="1">${escapeHtml(t("orders.linking.try_anyway"))}</button>`;
  }
  const wait = linkingSpent(status.budget)
    ? ` disabled title="${escapeHtml(t("orders.linking.wait", { reset: linkingClock(status.budget.resetAt, now) }))}"`
    : "";
  const label = receiptId ? tn("orders.linking.run_receipt", due) : t("orders.linking.run");
  return `<button class="button inline-button" type="button" data-linking-run${receipt} data-force="0"${wait}>${escapeHtml(label)}</button>`;
}

// De melding boven de lijst (zonder receiptId) of op een bon. Leeg als er niets te melden is: op de lijst geen pauze, budget
// over en geen werk; op een bon geen regels zonder product. Een pauze of een leeg budget is een waarschuwing, werk dat wacht een
// succesmelding. Op een bon is `unlinked` het aantal regels zonder product en `due` hoeveel daarvan een ronde nu opzoekt (zonder
// `due` allemaal). Is dat nul, dan zocht AH ze de laatste 30 dagen al op: de melding zegt dat de volgende poging vanzelf komt, en
// de pauze en het budget doen voor deze bon niet ter zake.
function linkingNotice(status, { receiptId = null, unlinked = 0, due = unlinked, total = 0, admin = false, now = new Date() } = {}) {
  if (!status) return "";
  const todo = status.todo || { lines: 0, receipts: 0, images: 0 };
  if (receiptId && !(unlinked > 0)) return "";
  const waiting = Boolean(receiptId) && !(due > 0);
  const paused = !waiting && Boolean(status.pausedUntil);
  const spent = !waiting && !paused && linkingSpent(status.budget);
  if (!receiptId && !paused && !spent && !(todo.lines > 0)) return "";
  const parts = [];
  if (receiptId) parts.push(tn("orders.linking.receipt_todo", unlinked, { total }));
  if (waiting) parts.push(t("orders.linking.receipt_waiting"));
  if (paused) parts.push(linkingPauseText(status, now));
  else if (spent) parts.push(linkingBudgetText(status.budget, now));
  if (!receiptId && (paused || spent) && todo.lines > 0) parts.push(t("orders.linking.todo_short", linkingCounts(todo)));
  if (!receiptId && !paused && !spent) {
    parts.push(t("orders.linking.todo", { ...linkingCounts(todo), next: linkingNext(status.nextRound, now) }));
  }
  const kind = paused || spent ? "warning" : "success";
  const button = linkingButton(status, { receiptId, due, admin, now });
  return `<div class="message ${kind} message--action linking-notice"><span>${escapeHtml(parts.join(" "))}</span>${button}</div>`;
}

// De laatste ronde in een regel, met de naam van de aanleiding. Een ronde op afroep of bij het openen zonder enige telling had
// niets te doen; een nachtelijke ronde met nullen laat zijn nullen zien (dan lag hij bijvoorbeeld stil).
function linkingLastRound(last) {
  if (!last || !last.at) return t("settings.linking.never");
  const name = Object.hasOwn(LINKING_TRIGGERS, last.trigger) ? LINKING_TRIGGERS[last.trigger] : null;
  const at = name ? `${formatSyncTime(last.at)} (${t(name)})` : formatSyncTime(last.at);
  const counts = ["searches", "found", "missing", "failed", "images"].map((key) => Number(last[key]) || 0);
  if (last.trigger !== "nightly" && counts.every((count) => count === 0)) return t("settings.linking.nothing", { at });
  const [searches, found, missing, , images] = counts;
  return t("settings.linking.last_round_text", { at, searches, found, missing, images });
}

// Het vak bij Instellingen > Koppelen (alleen de beheerder ziet dat tabblad): toestand, de meter van het uurbudget, wat er te
// doen is, de laatste en de volgende ronde, en de twee knoppen.
function linkingBox(status, now = new Date()) {
  if (!status) return "";
  const budget = status.budget || { used: 0, limit: 0 };
  const todo = status.todo || { lines: 0, receipts: 0, images: 0 };
  const used = Number(budget.used) || 0;
  const limit = Number(budget.limit) || 0;
  const percent = limit > 0 ? Math.min(100, Math.max(0, Math.round((used / limit) * 100))) : 0;
  const paused = Boolean(status.pausedUntil);
  const spent = linkingSpent(budget);
  let state = t("settings.linking.state_ok");
  if (status.running) state = t("orders.linking.running");
  else if (paused) state = linkingPauseText(status, now);
  else if (spent) state = linkingBudgetText(budget, now);
  const fact = (key, value) => `<dt>${escapeHtml(t(key))}</dt><dd>${value}</dd>`;
  const meter = `<span class="linking-meter" aria-hidden="true"><i style="width: ${percent}%"></i></span>${escapeHtml(t("settings.linking.meter", { used, limit }))}`;
  const blocked = status.running || (!paused && spent);
  const off = blocked
    ? ` disabled${spent && !status.running ? ` title="${escapeHtml(t("orders.linking.wait", { reset: linkingClock(budget.resetAt, now) }))}"` : ""}`
    : "";
  const runLabel = status.running ? t("orders.linking.running") : t(paused ? "settings.linking.run_anyway" : "settings.linking.run");
  return `<dl class="linking-facts">${[
    fact("settings.linking.state", escapeHtml(state)),
    fact("settings.linking.budget", meter),
    fact("settings.linking.todo", escapeHtml(t("settings.linking.todo_text", linkingCounts(todo)))),
    fact("settings.linking.last_round", escapeHtml(linkingLastRound(status.lastRound))),
    fact("settings.linking.next_round", escapeHtml(linkingNext(status.nextRound, now))),
  ].join("")}</dl>
  <div class="toolbar">
    <button class="button" type="button" data-linking-run data-force="${paused ? "1" : "0"}"${off}>${escapeHtml(runLabel)}</button>
    <button class="button button--secondary" type="button" data-linking-images data-force="${paused ? "1" : "0"}"${off}>${escapeHtml(t("settings.linking.images"))}</button>
  </div>`;
}

// De tellingen van een bon voor zijn melding: regels met een artikelnummer maar zonder webwinkelproduct (`unlinked`), hoeveel
// daarvan een ronde nu opzoekt (`due`, uit `linkingTodo` van de route, ah_orders.receipt_linking_todo), van het totaal. Zonder
// `linkingTodo` geldt elke regel zonder product als aan de beurt.
function receiptLinkCounts(receipt) {
  const items = receipt.items || [];
  const unlinked = items.filter((line) => line.productId && !line.product).length;
  const todo = receipt.linkingTodo || {};
  const due = Number.isFinite(todo.due) ? Math.min(todo.due, unlinked) : unlinked;
  return { receiptId: receipt.id, unlinked, due, total: items.length };
}

// ---- de pagina

// Dezelfde regel als applyRole (users.js): zonder inloggen of als beheerder ben je de eigenaar. Bij het tekenen bepaald, want de
// melding komt na applyRole op de pagina.
function linkingAdmin() {
  const auth = state.auth || {};
  return !auth.enabled || auth.isOwner !== false;
}

// Of een van de drie plekken op het scherm staat (een verborgen tab, subpanel of bonpagina heeft geen offsetParent).
function linkingVisible() {
  return ["#ah-linking-notice", "#order-linking-notice", "#linking-box-body"].some((selector) => $(selector)?.offsetParent);
}

function linkingRender() {
  const status = linkingState.status;
  const admin = linkingAdmin();
  const list = $("#ah-linking-notice");
  if (list) list.innerHTML = status && !ordersAreJumbo() ? linkingNotice(status, { admin }) : "";
  const receipt = $("#order-linking-notice");
  if (receipt) receipt.innerHTML = status && linkingState.receipt ? linkingNotice(status, { ...linkingState.receipt, admin }) : "";
  const box = $("#linking-box-body");
  if (box && status) box.innerHTML = linkingBox(status);
}

// Eén timer: elke keer eerst de vorige weg, en alleen een nieuwe zolang er een ronde loopt en er een plek te zien is.
function linkingSchedule() {
  clearTimeout(linkingState.timer);
  linkingState.timer = null;
  if (!linkingState.status?.running || !linkingVisible()) return;
  linkingState.timer = setTimeout(() => linkingNoticeRefresh(), LINKING_POLL_MS);
}

// Of de aankopenlijst met zijn melding op het scherm staat.
function linkingListVisible() {
  return Boolean($("#ah-linking-notice")?.offsetParent);
}

// Een ronde is klaar: de open bon opnieuw (refreshReceipt in orders.js) als de ronde voor die bon of voor alle bonnen was, anders
// na een ronde voor alle bonnen de lijst, als die op het scherm staat (niet als je intussen elders bent).
function linkingRoundDone(round) {
  const open = ordersState.open;
  if (open && open.kind === "receipt") {
    if (!round.receiptId || round.receiptId === open.id) refreshReceipt(open.id);
  } else if (!round.receiptId && !open && ordersState.loaded && linkingListVisible()) {
    loadOrders();
  }
}

// De toestand ophalen en de drie plekken tekenen. Met `receipt` ({ receiptId, unlinked, total }, of null) wisselt de open bon.
// Een mislukte vraag laat de plekken zoals ze waren en stopt het pollen: de melding is een toegift, de lijst en de bon melden
// hun eigen fouten.
async function linkingNoticeRefresh(receipt) {
  if (receipt !== undefined) linkingState.receipt = receipt;
  let status;
  try {
    status = await jsonFetch("/api/ah/linking");
  } catch {
    clearTimeout(linkingState.timer);
    linkingState.timer = null;
    return;
  }
  const before = linkingState.status?.running || null;
  linkingState.status = status;
  linkingRender();
  if (before && !status.running) linkingRoundDone(before);
  linkingSchedule();
}

// Een andere aankoop of de lijst gaat open: de melding van de vorige bon weg.
function linkingForget() {
  linkingState.receipt = null;
  const receipt = $("#order-linking-notice");
  if (receipt) receipt.innerHTML = "";
  const box = $("#order-linking-message");
  if (box) box.innerHTML = "";
}

// Instellingen > Koppelen opent (app.js).
function loadLinkingBox() {
  return linkingNoticeRefresh();
}

// De knop: een ronde starten. 202 en 409 geven de toestand terug (409: er loopt er al een); 403 is force van een gewone
// gebruiker. Een fout komt in het meldingsvak van de plek (data-linking-message). "Foto's aanvullen" (data-linking-images)
// vraagt met images: true alleen de foto's van alle bonnen, zonder zoekopdrachten uit het uurbudget.
async function linkingRun(button) {
  const target = button.closest("[data-linking-message]")?.dataset.linkingMessage || "#ah-orders-message";
  const body = { force: button.dataset.force === "1" };
  if (button.hasAttribute("data-linking-images")) body.images = true;
  else if (button.dataset.receiptId) body.receiptId = button.dataset.receiptId;
  message(target, "");
  button.disabled = true;
  try {
    const response = await fetch("/api/ah/linking/run", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await response.json().catch(() => null);
    if (response.status === 409) message(target, t("orders.linking.busy"), "warning");
    else if (response.status === 403 || !response.ok) message(target, data?.error || t("err.generic"));
    if ((response.status === 202 || response.status === 409) && data?.budget) {
      linkingState.status = data;
      linkingRender();
      // Was de ronde al klaar voordat de route antwoordde (niets te doen), dan meteen de bon of de lijst opnieuw.
      if (response.status === 202 && !data.running) linkingRoundDone({ receiptId: body.receiptId || null });
      linkingSchedule();
      return;
    }
    button.disabled = false;
  } catch (error) {
    message(target, error.message);
    button.disabled = false;
  }
}

document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-linking-run], [data-linking-images]");
  if (button && !button.disabled) linkingRun(button);
});
window.addEventListener("ms:languagechange", linkingRender);
