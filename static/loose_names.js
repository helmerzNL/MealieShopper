// De pagina Losse aankopen (loose_names.py), alleen voor de beheerder: een kaart per oude productnaam uit een bevestigingsmail, met de
// kandidaten die de winkel bij die naam vond. Een mens kiest het product (de keuze van de AI is voorgeselecteerd, maar nooit
// gekoppeld), zoekt zelf in de koppeldialoog van product_links.js of wijst de naam af. Laadt na product_links.js en gebruikt uit
// app.js de hulpfuncties ($, message, jsonFetch, t, tn, escapeHtml, storeImage, formatPrice, formatSyncTime, ...).
//
// Bovenaan staan de functies die HTML maken (zuiver: een kaart of een pagina in, tekst uit), daaronder het ophalen en de knoppen.

const LOOSE_PAGE_SIZE = 30; // zoveel kaarten per keer, als de aanbiedingenpagina
const LOOSE_MORE_AHEAD = 800; // zoveel pixels voor het einde van de lijst komen de volgende kaarten er al bij (offers.js)
const LOOSE_POLL_MS = 5000; // zolang er een ronde loopt, ververst de voortgangsregel zo vaak

const looseState = {
  store: "",
  cards: [],
  missing: [],
  rejected: [],
  total: 0,
  toSearch: 0,
  pausedUntil: null,
  running: false,
  ai: { configured: false, nightly: false },
  lastRound: null,
  loaded: false,
  loading: false,
  run: 0, // een nieuwere lading (andere winkel) maakt een oudere antwoord waardeloos
  pending: "", // de sleutel van de naam die in de koppeldialoog openstaat
  timer: 0,
  stalled: false, // de poll stond stil omdat de pagina niet in beeld was: hij gaat verder zodra ze terug is
  pollFailed: false,
  headerHtml: "",
};

// ---- HTML: zuivere functies

// Het productnummer van het voorstel van de AI als het bij een kandidaat van de kaart hoort, anders null: dan staat er niets gekozen.
function looseSelected(card) {
  const proposed = card?.proposal?.productId;
  if (!proposed) return null;
  const found = (card.candidates || []).find((candidate) => String(candidate.productId) === String(proposed));
  return found ? String(found.productId) : null;
}

function looseYears(card) {
  return card.firstYear && card.lastYear && card.firstYear !== card.lastYear ? `${card.firstYear}-${card.lastYear}` : String(card.lastYear || card.firstYear || "");
}

function loosePaid(card) {
  const min = Number(card.paidMin || 0);
  const max = Number(card.paidMax || 0);
  if (!min && !max) return "";
  if (!max || formatPrice(min) === formatPrice(max)) return t("loose.paid_one", { price: formatPrice(min || max) });
  return t("loose.paid_range", { min: formatPrice(min), max: formatPrice(max) });
}

function looseMeta(card) {
  const bought = t("loose.bought", { count: card.bought, years: looseYears(card) });
  return [bought, loosePaid(card)].filter(Boolean).join(" · ");
}

// Een foto alleen met een https-adres: wat een winkel of de AI als adres meegeeft, is tekst van buiten.
function looseImage(candidate) {
  const image = String(candidate.image || "");
  if (/^https:\/\//i.test(image)) return `<img src="${escapeHtml(storeImage(image))}" alt="" loading="lazy" data-hide-on-error>`;
  return `<span class="link-item__photo" aria-hidden="true">${mdiIcon(MDI_IMAGE_OUTLINE)}</span>`;
}

function looseCandidateHtml(candidate, group, selected) {
  const meta = [candidate.brand, candidate.unitSize].filter(Boolean).join(" · ");
  const price = candidate.price ? formatPrice(candidate.price) : "";
  const checked = selected === String(candidate.productId) ? " checked" : "";
  return `
        <label class="loose-candidate">
          <input type="radio" name="${escapeHtml(group)}" value="${escapeHtml(candidate.productId)}"${checked}>
          ${looseImage(candidate)}
          <span class="loose-candidate__body">
            <strong>${escapeHtml(candidate.title)}</strong>
            ${meta ? `<small>${escapeHtml(meta)}</small>` : ""}
          </span>
          ${price ? `<span class="loose-candidate__price">${escapeHtml(price)}</span>` : ""}
        </label>`;
}

// Wat de AI over deze naam zei: zijn reden, en dat hij de verpakking niet zeker wist. Koos hij niets, dan staat dat erbij.
function looseProposalHtml(card) {
  const proposal = card.proposal;
  if (!proposal) return "";
  const chosen = Boolean(looseSelected(card));
  const parts = [];
  parts.push(`<strong>${escapeHtml(t(chosen ? "loose.ai_chose" : "loose.ai_none"))}</strong>`);
  if (proposal.reason) parts.push(escapeHtml(proposal.reason));
  if (chosen && proposal.surePack === false) parts.push(`<span class="loose-card__unsure">${escapeHtml(t("loose.unsure_pack"))}</span>`);
  return `<p class="loose-card__ai">${parts.join(" ")}</p>`;
}

function looseCardHtml(card, store) {
  const group = `loose-pick-${card.key}`;
  const selected = looseSelected(card);
  const candidates = (card.candidates || []).map((candidate) => looseCandidateHtml(candidate, group, selected)).join("");
  return `
    <article class="loose-card" data-loose-card="${escapeHtml(card.key)}">
      <header class="loose-card__head">
        <h3>${escapeHtml(card.name)}</h3>
        <p class="muted">${escapeHtml(looseMeta(card))}</p>
      </header>
      <div class="loose-card__candidates" role="radiogroup" aria-label="${escapeHtml(t("loose.candidates", { name: card.name }))}">${candidates}
      </div>
      ${looseProposalHtml(card)}
      <div class="loose-card__actions">
        <button type="button" class="button" data-loose-confirm${selected ? "" : " disabled"}>${escapeHtml(t("loose.confirm"))}</button>
        <button type="button" class="button button--secondary" data-product-link data-store="${escapeHtml(store)}" data-name="${escapeHtml(card.name)}">${escapeHtml(t("loose.search_own"))}</button>
        <button type="button" class="button button--secondary" data-loose-reject>${escapeHtml(t("loose.reject"))}</button>
      </div>
    </article>`;
}

function looseStatusLines(page) {
  const lines = [];
  if (page.pausedUntil) lines.push(t("loose.paused", { when: formatSyncTime(page.pausedUntil) }));
  if (page.running) lines.push(t("loose.running", { count: page.toSearch }));
  const round = page.lastRound;
  const own = round?.stores?.[page.store];
  if (round?.at && !page.running) lines.push(t("loose.last_round", { when: formatSyncTime(round.at) }));
  if (own?.error && !page.running) lines.push(t("loose.last_error", { error: own.error }));
  return lines;
}

// De kop van een winkel: de tellers, de knoppen en de regels over de pauze en een lopende ronde. "Vraag de AI" staat er alleen met
// een AI-sleutel en zolang de AI niet al 's nachts voorstelt.
function looseHeaderHtml(page, store) {
  const blocked = Boolean(page.pausedUntil) || Boolean(page.running);
  const ask = page.ai?.configured && !page.ai?.nightly;
  const lines = looseStatusLines({ ...page, store });
  return `
    <div class="loose-header">
      <div class="loose-header__info">
        <strong>${escapeHtml(storeDisplayName(store))}</strong>
        <span class="muted">${escapeHtml(tn("loose.proposals", page.total))} · ${escapeHtml(tn("loose.to_search", page.toSearch))}</span>
        ${lines.map((line) => `<span class="muted loose-header__line">${escapeHtml(line)}</span>`).join("")}
      </div>
      <div class="loose-header__actions">
        <button type="button" class="button" id="loose-run" data-loose-run${blocked ? " disabled" : ""}>${escapeHtml(t("loose.run"))}</button>
        <span id="loose-export-slot" class="loose-slot"></span>
        ${ask ? `<button type="button" class="button button--secondary" id="loose-ai-run" data-loose-ai>${escapeHtml(t("loose.ai_run"))}</button>` : ""}
      </div>
    </div>`;
}

function looseRowHtml(item, store, action) {
  const name = escapeHtml(item.name);
  const button =
    action === "restore"
      ? `<button type="button" class="button button--secondary" data-loose-restore>${escapeHtml(t("loose.restore"))}</button>`
      : `<button type="button" class="button button--secondary" data-product-link data-store="${escapeHtml(store)}" data-name="${name}">${escapeHtml(t("loose.search_own"))}</button>`;
  return `
        <li class="loose-row" data-loose-row="${escapeHtml(item.key)}">
          <span class="loose-row__name"><strong>${name}</strong><small class="muted">${escapeHtml(t("loose.bought_short", { count: item.bought }))}</small></span>
          ${button}
        </li>`;
}

// Twee ingeklapte lijsten onderaan: wat de winkel niet vond (met Zoek zelf) en wat je afwees (met Terugzetten).
function looseListsHtml(page, store) {
  const fold = (title, items, action) =>
    items.length
      ? `<details class="loose-fold">
      <summary>${escapeHtml(title)} <span class="loose-fold__count">${items.length}</span></summary>
      <ul class="loose-list">${items.map((item) => looseRowHtml(item, store, action)).join("")}
      </ul>
    </details>`
      : "";
  return fold(t("loose.missing_title"), page.missing || [], "search") + fold(t("loose.rejected_title"), page.rejected || [], "restore");
}

// Mag deze kaart of regel nu iets doen? Niet terwijl zijn eerste verzoek nog loopt (`data-busy`, zie looseLock).
function looseMayAct(holder) {
  return Boolean(holder) && holder.dataset?.busy !== "1";
}

// Moet de voortgang nu ververst worden? Alleen voor de lading die nu geldt, zolang er een ronde loopt en de pagina in beeld is.
function loosePollWanted({ running, run, current, panelActive, subtabActive, visible }) {
  return Boolean(running) && run === current && Boolean(panelActive) && Boolean(subtabActive) && visible !== false;
}

// ---- de pagina

function looseStore() {
  return state.itemsStore || "ah";
}

// De knop Exporteer voor GitHub is een vast element (`#loose-export` in de template): de kop krijgt alleen een plek (`#loose-export-slot`)
// en de knop verhuist erheen, zodat wie hem bindt niet bij elke verversing opnieuw hoeft. Een kop die niet veranderd is, blijft staan.
function looseRenderHeader({ force = false } = {}) {
  const html = looseHeaderHtml(looseState, looseState.store);
  if (!force && html === looseState.headerHtml) return;
  looseState.headerHtml = html;
  const button = $("#loose-export");
  $("#loose-export-home").append(button);
  $("#loose-header").innerHTML = html;
  $("#loose-export-slot")?.replaceWith(button);
}

function looseRenderLists() {
  $("#loose-lists").innerHTML = looseListsHtml(looseState, looseState.store);
}

function looseRenderMore() {
  $("#loose-more").hidden = looseState.cards.length >= looseState.total;
  $("#loose-empty").hidden = Boolean(looseState.cards.length) || looseState.loading;
}

function looseRenderCards({ append = false, from = 0 } = {}) {
  const box = $("#loose-cards");
  const html = looseState.cards.slice(append ? from : 0).map((card) => looseCardHtml(card, looseState.store)).join("");
  if (append) box.insertAdjacentHTML("beforeend", html);
  else box.innerHTML = html;
  looseRenderMore();
}

function looseRenderAll() {
  looseRenderHeader();
  looseRenderCards();
  looseRenderLists();
}

function looseApply(data) {
  Object.assign(looseState, {
    total: data.total, toSearch: data.toSearch, pausedUntil: data.pausedUntil || null, running: Boolean(data.running),
    ai: data.ai || looseState.ai, lastRound: data.lastRound || null,
  });
}

function looseQuery(offset, limit) {
  return new URLSearchParams({ store: looseState.store, offset: String(offset), limit: String(limit) });
}

async function loadLooseNames() {
  const run = ++looseState.run;
  clearTimeout(looseState.timer);
  looseState.pollFailed = false;
  Object.assign(looseState, { store: looseStore(), cards: [], missing: [], rejected: [], total: 0, toSearch: 0, loading: true, pending: "" });
  $("#loose-cards").innerHTML = "";
  $("#loose-lists").innerHTML = "";
  $("#loose-export-home").append($("#loose-export"));
  $("#loose-header").innerHTML = "";
  looseState.headerHtml = "";
  $("#loose-more").hidden = true;
  $("#loose-empty").hidden = true;
  looseMessage(t("loose.loading"), "info");
  try {
    const data = await jsonFetch(`/api/loose-names?${looseQuery(0, LOOSE_PAGE_SIZE)}`);
    if (run !== looseState.run) return;
    Object.assign(looseState, { cards: data.cards || [], missing: data.missing || [], rejected: data.rejected || [], loaded: true, loading: false });
    looseApply(data);
    looseMessage("");
    looseRenderAll();
    looseWatchMore();
    looseSchedulePoll();
    looseAnnounce("ms:loose-loaded");
  } catch (error) {
    if (run !== looseState.run) return;
    looseState.loading = false;
    looseMessage(error.message);
  }
}

async function looseLoadMore() {
  if (looseState.loading || looseState.cards.length >= looseState.total) return;
  const run = looseState.run;
  looseState.loading = true;
  const from = looseState.cards.length;
  try {
    const data = await jsonFetch(`/api/loose-names?${looseQuery(from, LOOSE_PAGE_SIZE)}`);
    if (run !== looseState.run) return;
    looseState.cards = looseState.cards.concat(data.cards || []);
    looseApply(data);
    looseState.loading = false;
    looseRenderHeader();
    looseRenderCards({ append: true, from });
  } catch (error) {
    if (run !== looseState.run) return;
    looseState.loading = false;
    looseMessage(error.message);
  }
}

// De lijst loopt vanzelf door (het patroon van offers.js): komt de knop Meer in de buurt van het scherm, dan komen de volgende kaarten.
let looseMoreObserver = null;

function looseAutoMore() {
  const button = $("#loose-more");
  if (!button.hidden && button.offsetParent && button.getBoundingClientRect().top < window.innerHeight + LOOSE_MORE_AHEAD) looseLoadMore();
}

function looseWatchMore() {
  if (typeof IntersectionObserver === "undefined") return;
  const button = $("#loose-more");
  if (!looseMoreObserver) {
    looseMoreObserver = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) looseAutoMore();
    }, { rootMargin: `0px 0px ${LOOSE_MORE_AHEAD}px 0px` });
  }
  looseMoreObserver.unobserve(button);
  looseMoreObserver.observe(button);
}

function looseWantsPoll(run) {
  return loosePollWanted({
    running: looseState.running, run, current: looseState.run,
    panelActive: document.querySelector("#ah-items.is-active"), subtabActive: document.querySelector("#ah-items-losse-aankopen.is-active"),
    visible: !document.hidden,
  });
}

// Een ronde die loopt (ook na het herladen van de pagina): elke vijf seconden de stand, en aan het eind de lijst opnieuw. Staat de
// pagina niet in beeld, dan stopt de poll en looseResume zet hem weer aan. Na een fout gaat hij gewoon verder.
async function looseTick(run) {
  if (!looseWantsPoll(run)) {
    looseState.stalled = run === looseState.run && looseState.running;
    return;
  }
  looseState.stalled = false;
  try {
    const data = await jsonFetch(`/api/loose-names?${looseQuery(0, 1)}`);
    if (run !== looseState.run) return;
    if (!data.running) {
      loadLooseNames();
      return;
    }
    looseApply(data);
    looseRenderHeader();
    looseRenderMore();
    if (looseState.pollFailed) message("#loose-message", "");
    looseState.pollFailed = false;
  } catch (error) {
    if (run !== looseState.run) return;
    looseState.pollFailed = true;
    message("#loose-message", error.message);
  }
  looseSchedulePoll();
}

function looseSchedulePoll() {
  clearTimeout(looseState.timer);
  if (!looseState.running) return;
  const run = looseState.run;
  looseState.timer = setTimeout(() => looseTick(run), LOOSE_POLL_MS);
}

// De pagina is weer in beeld terwijl de poll stilstond: eerst een keer verversen, dan verder.
function looseResume() {
  if (!looseState.stalled || !looseWantsPoll(looseState.run)) return;
  clearTimeout(looseState.timer);
  looseTick(looseState.run);
}

// ---- acties

// Elke melding van de pagina, behalve die van de poll zelf, haalt de melding van de poll weg: herstelt de poll later, dan wist hij
// alleen zijn eigen melding (zie looseTick).
function looseMessage(text, type = "error") {
  looseState.pollFailed = false;
  message("#loose-message", text, type);
}

// Vergrendelt een kaart of regel voor de duur van een verzoek: alle knoppen en keuzerondjes uit, en `data-busy` zodat een klik of
// wijziging op de kaart niets meer doet. Ontgrendelen zet het kiezen terug zoals het was (zonder keuze blijft Koppel uit).
function looseLock(holder, locked) {
  if (locked) holder.dataset.busy = "1";
  else delete holder.dataset.busy;
  holder.querySelectorAll("button, input").forEach((element) => {
    element.disabled = locked;
  });
  const confirm = holder.querySelector("[data-loose-confirm]");
  if (!locked && confirm) confirm.disabled = !holder.querySelector("input[type=radio]:checked");
}

// Een verzoek voor een kaart of regel, een keer tegelijk: de kaart blijft op slot tot het antwoord er is. Een knop in de kop heeft geen
// kaart, die is tegen een tweede klik beschermd doordat setBusy hem uitzet. `after(antwoord)` is wat er na een gelukt verzoek gebeurt
// (de kaart weghalen, de lijst laden). Staat de kaart daarna nog in de pagina, of faalt `after`, dan komt de kaart weer vrij.
async function loosePost(path, body, button, after) {
  const holder = button?.closest("[data-loose-card], [data-loose-row]") || null;
  if (holder ? !looseMayAct(holder) : button?.disabled) return null;
  looseMessage("");
  if (holder) looseLock(holder, true);
  if (button) setBusy(button, true);
  let result;
  try {
    result = await jsonFetch(`/api/loose-names/${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (error) {
    looseMessage(error.message);
    if (holder) looseLock(holder, false);
    return null;
  } finally {
    if (button) setBusy(button, false);
  }
  let failed = null;
  try {
    if (after) after(result);
  } catch (error) {
    failed = error;
    looseMessage(error.message);
  }
  if (holder?.isConnected) looseLock(holder, false);
  return failed ? null : result;
}

// De kaart verdwijnt en de tellers lopen bij, zonder de lijst opnieuw te laden.
function looseDropCard(key, { rejected = false } = {}) {
  for (const element of document.querySelectorAll("[data-loose-card]")) {
    if (element.dataset.looseCard === key) element.remove();
  }
  const at = looseState.cards.findIndex((card) => card.key === key);
  if (at < 0) return;
  const [card] = looseState.cards.splice(at, 1);
  looseState.total = Math.max(0, looseState.total - 1);
  if (rejected) looseState.rejected.push({ key: card.key, name: card.name, bought: card.bought });
  looseRenderHeader();
  looseRenderMore();
  if (rejected) looseRenderLists();
}

function looseDropRow(key) {
  looseState.missing = looseState.missing.filter((item) => item.key !== key);
  looseRenderLists();
}

async function looseConfirm(button) {
  const card = button.closest("[data-loose-card]");
  const picked = card?.querySelector("input[type=radio]:checked");
  if (!card || !picked) return;
  const key = card.dataset.looseCard;
  // Alleen de sleutel en het gekozen productnummer: de server weet welk product dat is.
  await loosePost("confirm", { store: looseState.store, key, productId: picked.value }, button, () => {
    looseDropCard(key);
    looseAnnounce("ms:loose-confirmed"); // een eigen koppeling erbij: de teller van de export (shared_links_export.js) ververst
  });
}

async function looseReject(button) {
  const key = button.closest("[data-loose-card]")?.dataset.looseCard;
  if (key) await loosePost("reject", { store: looseState.store, key }, button, () => looseDropCard(key, { rejected: true }));
}

async function looseRestore(button) {
  const key = button.closest("[data-loose-row]")?.dataset.looseRow;
  if (key) await loosePost("restore", { store: looseState.store, key }, button, () => loadLooseNames());
}

async function looseRun(button) {
  await loosePost("run", { store: looseState.store }, button, () => loadLooseNames());
}

async function looseAskAi(button) {
  setBusy(button, true, t("loose.ai_busy"));
  const result = await loosePost("propose", { store: looseState.store });
  setBusy(button, false, t("loose.ai_busy"));
  if (!result) return;
  await loadLooseNames();
  looseMessage(t("loose.ai_done", { asked: result.asked || 0, proposed: result.proposed || 0, none: result.none || 0 }), "success");
}

// Meldt de pagina's stand aan wie meeluistert (shared_links_export.js), met de winkel erbij.
function looseAnnounce(name) {
  window.dispatchEvent(new CustomEvent(name, { detail: { store: looseState.store } }));
}

// De koppeldialoog van product_links.js meldt een gelukte koppeling met ms:product-links. De naam die we daar openden, is dan
// geen losse naam meer: kaart of regel weg.
function looseLinked(store) {
  if (store !== looseState.store || !looseState.loaded) return;
  const key = looseState.pending;
  looseState.pending = "";
  if (!key) return;
  if (looseState.cards.some((card) => card.key === key)) looseDropCard(key);
  else looseDropRow(key);
}

function looseOnClick(event) {
  const target = event.target;
  const pick = (selector) => target.closest(selector);
  const link = pick("[data-product-link]");
  if (link && looseMayAct(link.closest("[data-loose-card], [data-loose-row]"))) {
    const holder = link.closest("[data-loose-card], [data-loose-row]");
    looseState.pending = holder?.dataset.looseCard ?? holder?.dataset.looseRow ?? "";
  }
  const actions = [
    ["[data-loose-confirm]", looseConfirm], ["[data-loose-reject]", looseReject], ["[data-loose-restore]", looseRestore],
    ["[data-loose-run]", looseRun], ["[data-loose-ai]", looseAskAi],
  ];
  for (const [selector, action] of actions) {
    const button = pick(selector);
    if (button) {
      action(button);
      return;
    }
  }
  if (pick("#loose-more")) looseLoadMore();
}

function looseOnChange(event) {
  const card = event.target.closest("[data-loose-card]");
  if (card && looseMayAct(card) && event.target.matches("input[type=radio]")) card.querySelector("[data-loose-confirm]").disabled = false;
}

function looseStart() {
  const view = $("#loose-view");
  if (!view) return;
  view.addEventListener("click", looseOnClick);
  view.addEventListener("change", looseOnChange);
  window.addEventListener("ms:product-links", (event) => looseLinked(event.detail?.store));
  document.addEventListener("visibilitychange", looseResume);
  // Het hoofdtabblad Winkels dicht en weer open: de klasse van het paneel wijzigt, en de poll gaat verder.
  const panel = $("#ah-items");
  if (panel && typeof MutationObserver !== "undefined") new MutationObserver(looseResume).observe(panel, { attributes: true, attributeFilter: ["class"] });
  window.addEventListener("ms:languagechange", () => {
    if (looseState.loaded) looseRenderAll();
  });
}

looseStart();
