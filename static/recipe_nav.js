// ---- Bladeren door recepten: vanuit het recept dat open staat naar het vorige of volgende recept
// Open je een recept vanuit een lijst (Recepten zoeken, de winkels, de maaltijdboxen, de AH-weekplanner) of vanuit het weekmenu
// (het venster van een maaltijd), dan blader je daarna door diezelfde lijst. De reeks is wat er op het scherm stond toen je het
// recept opende, in dezelfde volgorde: met een zoekterm, een filter, een gekozen week of een gekozen lijst bladert het daar
// doorheen. Met de chevrons in de kop (en onderaan het recept), met de pijltjestoetsen, of met een veeg naar links (volgende)
// of naar rechts (vorige) op een scherm met aanraking, zoals in de kookmodus (recipe_view.js).
// Een recept uit de lijst heet hier een "scherm" (openStoreRecipe in recipe_search.js, een box met een kop en een inhoud) of
// het venster van een maaltijd (openMealRecipe in app.js). Elk heeft zijn eigen reeks (recipeNavStates, op het id van het
// element), zodat het detail bij Recepten zoeken en het weekmenu elkaar niet verstoren. Een `driver` zegt hoe het scherm een
// recept opent en wat het doet aan het eind van de lijst (Recepten zoeken zonder zoekterm en Jumbo laden dan de volgende pagina).
// Laadt na app.js en gebruikt daar $, t, escapeHtml, mdiIcon en state.

// Material Design Icons (zie static/icons/LICENSE-mdi.txt)
const MDI_CHEVRON_LEFT = "M15.41,16.58L10.83,12L15.41,7.41L14,6L8,12L14,18L15.41,16.58Z";
const MDI_CHEVRON_RIGHT = "M8.59,16.58L13.17,12L8.59,7.41L10,6L16,12L10,18L8.59,16.58Z";

const RECIPE_NAV_SWIPE_MIN = 60; // zo ver (px) moet een veeg zijn
const RECIPE_NAV_SWIPE_RATIO = 2; // en zoveel keer meer zijwaarts dan omhoog of omlaag, anders is het scrollen

const recipeNavStates = new Map(); // id van het scherm -> { items: [{ store, id }], index, driver, step, loading }
let recipeNavSwipe = null; // { surface, x, y } zolang een vinger op een scherm ligt

// ---- Zonder pagina: de keuzes zelf (tests/test_recipe_nav.py draait ze in Node)

// "store:id" van een kaart (data-store-open) als { store, id }; de id mag zelf een dubbele punt hebben. null zonder beide.
function recipeNavParse(value) {
  const text = String(value ?? "");
  const colon = text.indexOf(":");
  return colon > 0 && colon < text.length - 1 ? { store: text.slice(0, colon), id: text.slice(colon + 1) } : null;
}

// De plek na een stap (-1 vorige, 1 volgende) in een reeks van `length`; -1 als er geen is (de rand, of het recept staat er niet in).
function recipeNavTarget(index, length, step) {
  const target = index + step;
  return index >= 0 && index < length && (step === 1 || step === -1) && target >= 0 && target < length ? target : -1;
}

// Een veeg als stap: naar links is het volgende recept (1), naar rechts het vorige (-1), 0 als het geen duidelijke zijwaartse veeg is.
function recipeNavSwipeStep(dx, dy) {
  if (Math.abs(dx) < RECIPE_NAV_SWIPE_MIN || Math.abs(dx) < RECIPE_NAV_SWIPE_RATIO * Math.abs(dy)) return 0;
  return dx < 0 ? 1 : -1;
}

// De pijltjestoets als stap; met Ctrl, Alt, Meta of Shift (terug in de geschiedenis, tekst selecteren) niet.
function recipeNavKeyStep(event) {
  if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return 0;
  return { ArrowRight: 1, ArrowLeft: -1 }[event.key] || 0;
}

// De knoppen van een balk: bovenaan chevrons met de plek ertussen, onderaan Vorige en Volgende met een chevron ervoor of erna
// (zonder de plek: die staat bovenaan, en op een telefoon is er geen ruimte voor).
function recipeNavHtml(variant) {
  const full = variant === "bottom";
  const button = (step, key, shortKey, icon) => {
    const label = escapeHtml(t(key));
    const text = full ? `<span>${escapeHtml(t(shortKey))}</span>` : "";
    const inner = step < 0 ? `${mdiIcon(icon)}${text}` : `${text}${mdiIcon(icon)}`;
    return `<button class="${full ? "button button--secondary" : "icon-button"} recipe-nav__step" type="button" data-recipe-step="${step}" aria-label="${label}"${full ? "" : ` title="${label}"`}>${inner}</button>`;
  };
  const count = full ? "" : '<span class="recipe-nav__count" role="status"></span>';
  return `${button(-1, "recipenav.prev", "recipenav.prev_short", MDI_CHEVRON_LEFT)}${count}${button(1, "recipenav.next", "recipenav.next_short", MDI_CHEVRON_RIGHT)}`;
}

// ---- De reeks lezen en de balk bijhouden

// De recepten in de lijst van een gastheer (recipe_search.js), zoals de kaarten op het scherm staan. Een kaart in een verborgen
// onderdeel (een winkel zonder resultaten) telt niet mee; de lijst zelf mag verborgen zijn (het recept staat er al open).
function recipeNavItems(host) {
  const list = $(host.list);
  const seen = new Set();
  const items = [];
  for (const button of list ? list.querySelectorAll("[data-store-open]") : []) {
    const hidden = button.closest("[hidden]");
    if (hidden && hidden !== list && list.contains(hidden)) continue;
    const item = recipeNavParse(button.dataset.storeOpen);
    if (!item || seen.has(`${item.store}:${item.id}`)) continue;
    seen.add(`${item.store}:${item.id}`);
    items.push(item);
  }
  return items;
}

// De maaltijden met een recept in het weekmenu, in de volgorde van het scherm (dagen, en per dag de maaltijdsoort). Een notitie heeft geen recept.
function recipeNavMealItems() {
  return [...document.querySelectorAll("#meal-days [data-plan-view]")].map((button) => ({ store: "meal", id: button.dataset.planView }));
}

function recipeNavHostDriver(host) {
  return {
    open: (item) => openStoreRecipe(item.store, item.id, host, { move: true }),
    items: () => recipeNavItems(host),
    more: () => Boolean(host.more?.()),
    loadMore: () => host.loadMore?.(),
    total: () => Number(host.total?.()) || 0,
    title: `#${host.prefix}-title`,
    body: `#${host.prefix}-body`,
  };
}

const RECIPE_NAV_MEAL_DRIVER = {
  open: (item) => {
    const entry = (state.entries || []).find((candidate) => String(candidate.id) === item.id);
    if (entry) openMealRecipe(entry, { move: true });
  },
  items: recipeNavMealItems,
  more: () => false,
  loadMore: () => undefined,
  total: () => 0,
  title: "#meal-recipe-title",
  body: "#meal-recipe-body",
};

// Het scherm van een gastheer wordt een bladerscherm: een balk in de kop en een onderaan, en het scherm weet het zelf (de tik, de veeg).
function recipeNavMount(host) {
  const surface = $(host.detail);
  if (surface.dataset.recipeSurface !== undefined) return surface;
  surface.dataset.recipeSurface = "";
  const holder = (variant) => {
    const element = document.createElement("div");
    element.className = `recipe-nav recipe-nav--${variant}`;
    element.dataset.recipeNav = variant;
    element.hidden = true;
    return element;
  };
  surface.querySelector(".list-header")?.append(holder("top"));
  surface.append(holder("bottom"));
  return surface;
}

// Een recept uit een lijst is open: de reeks is de lijst zoals hij nu op het scherm staat.
function recipeNavBegin(host, store, id) {
  const surface = recipeNavMount(host);
  const items = recipeNavItems(host);
  const index = items.findIndex((item) => item.store === store && item.id === String(id));
  if (index < 0) recipeNavStates.delete(surface.id);
  else recipeNavStates.set(surface.id, { items, index, driver: recipeNavHostDriver(host), step: 0, loading: false });
  recipeNavRender(surface);
}

// Het recept van een maaltijd is open: de reeks zijn de maaltijden met een recept in het weekmenu.
function recipeNavMeal(entry) {
  const surface = $("#meal-recipe-dialog");
  const items = recipeNavMealItems();
  const index = items.findIndex((item) => item.id === String(entry.id));
  if (index < 0) recipeNavStates.delete(surface.id);
  else recipeNavStates.set(surface.id, { items, index, driver: RECIPE_NAV_MEAL_DRIVER, step: 0, loading: false });
  recipeNavRender(surface);
}

// De lijst is weer in beeld: er is geen recept meer open.
function recipeNavEnd(host) {
  const surface = $(host.detail);
  recipeNavStates.delete(surface.id);
  recipeNavRender(surface);
}

// Is er vanaf hier een recept in die richting? Aan het eind van een lijst met een volgende pagina is dat ook zo.
function recipeNavCan(state, step) {
  return recipeNavTarget(state.index, state.items.length, step) >= 0 || (step > 0 && !state.loading && state.driver.more());
}

// De balken van een scherm: de plek ("3 van 24"), en een chevron valt uit waar het niet verder kan. Eén recept of niets: de balk is weg.
// De knoppen worden één keer gebouwd en daarna bijgewerkt, zodat de focus op een knop blijft staan.
function recipeNavRender(surface, { rebuild = false } = {}) {
  const state = recipeNavStates.get(surface.id);
  const total = state ? Math.max(state.items.length, state.driver.total()) : 0;
  for (const holder of surface.querySelectorAll("[data-recipe-nav]")) {
    holder.hidden = !state || total < 2;
    if (holder.hidden) continue;
    if (rebuild || !holder.firstElementChild) holder.innerHTML = recipeNavHtml(holder.dataset.recipeNav);
    holder.setAttribute("role", "group");
    holder.setAttribute("aria-label", t("recipenav.group"));
    const count = holder.querySelector(".recipe-nav__count");
    if (count) count.textContent = t("recipenav.position", { current: I18N.formatNumber(state.index + 1), total: I18N.formatNumber(total) });
    for (const button of holder.querySelectorAll("[data-recipe-step]")) button.disabled = !recipeNavCan(state, Number(button.dataset.recipeStep));
  }
}

// Het nieuwe recept staat er (na een stap): het schuift van de kant waar het vandaan komt naar binnen, en de focus blijft op de
// chevron of gaat naar de titel als die chevron nu uitgevallen is (de eerste of laatste).
function recipeNavArrived(surface) {
  const state = recipeNavStates.get(surface.id);
  if (!state) return;
  const step = state.step;
  state.step = 0;
  const body = $(state.driver.body);
  if (step && body) {
    body.classList.remove("recipe-slide-next", "recipe-slide-prev");
    void body.offsetWidth; // zonder dit begint dezelfde animatie niet opnieuw
    body.classList.add(step > 0 ? "recipe-slide-next" : "recipe-slide-prev");
  }
  const active = document.activeElement;
  if (!active || active === document.body || (active instanceof HTMLButtonElement && active.disabled)) $(state.driver.title)?.focus({ preventScroll: true });
}

// Een stap naar het vorige of volgende recept. Aan het eind van een lijst met een volgende pagina laadt die eerst.
async function recipeNavGo(surface, step) {
  const state = recipeNavStates.get(surface.id);
  if (!state || state.loading) return;
  let target = recipeNavTarget(state.index, state.items.length, step);
  if (target < 0 && step > 0 && state.driver.more()) {
    state.loading = true;
    try {
      await state.driver.loadMore();
    } catch {
      // De lijst toont zelf wat er misging; hier blijft het recept staan.
    }
    state.loading = false;
    if (recipeNavStates.get(surface.id) !== state) return; // intussen een ander recept geopend, of terug naar de lijst
    const current = state.items[state.index];
    state.items = state.driver.items();
    state.index = state.items.findIndex((item) => item.store === current.store && item.id === current.id);
    if (state.index < 0) recipeNavStates.delete(surface.id); // de lijst is intussen een andere geworden
    target = recipeNavTarget(state.index, state.items.length, step);
    recipeNavRender(surface);
  }
  if (target < 0) return;
  state.index = target;
  state.step = step;
  recipeNavRender(surface);
  if (!(surface instanceof HTMLDialogElement)) window.scrollTo({ top: 0 }); // het venster van een maaltijd zet zijn eigen scrollstand terug
  await state.driver.open(state.items[target]);
}

// Het scherm dat nu in beeld is en een reeks heeft (niet het venster van een maaltijd; dat heeft zijn eigen toets).
function recipeNavVisible() {
  for (const id of recipeNavStates.keys()) {
    const element = document.getElementById(id);
    if (element && !(element instanceof HTMLDialogElement) && !element.hidden && element.getClientRects().length) return element;
  }
  return null;
}

// Loopt er binnen deze plek zijwaarts scrollende inhoud (een brede tabel)? Een veeg daarop scrolt dat, en bladert niet.
function recipeNavScrollsSideways(element, root) {
  for (let node = element; node && node !== root; node = node.parentElement) {
    if (node.scrollWidth > node.clientWidth + 1 && /auto|scroll/.test(getComputedStyle(node).overflowX)) return true;
  }
  return false;
}

// ---- Bediening. De balken worden met innerHTML opgebouwd; daarom luisteren de handlers op het document.
document.addEventListener("click", (event) => {
  const button = event.target instanceof Element ? event.target.closest("[data-recipe-step]") : null;
  const surface = button?.closest("[data-recipe-surface]");
  if (surface) recipeNavGo(surface, Number(button.dataset.recipeStep));
});

// Pijltje rechts en links, als er geen veld, tabblad of ander venster is dat die toets zelf gebruikt.
document.addEventListener("keydown", (event) => {
  const step = recipeNavKeyStep(event);
  if (!step || event.defaultPrevented) return;
  if (event.target instanceof Element && event.target.closest("input, select, textarea, [contenteditable], [role='tab'], [role='menuitem'], [role='slider']")) return;
  const dialogs = document.querySelectorAll("dialog[open]");
  if (dialogs.length > 1) return; // de kookmodus staat boven het recept van een maaltijd: die heeft zijn eigen pijltjes
  const surface = dialogs.length ? (dialogs[0].matches("[data-recipe-surface]") ? dialogs[0] : null) : recipeNavVisible();
  const state = surface && recipeNavStates.get(surface.id);
  if (!state || !recipeNavCan(state, step)) return;
  event.preventDefault();
  recipeNavGo(surface, step);
});

// Vegen op het recept: alleen met een vinger of pen, niet met de muis (die selecteert tekst), en niet op een veld of op inhoud die
// zelf zijwaarts scrolt. Een tweede vinger (knijpen) maakt er geen veeg van.
document.addEventListener(
  "pointerdown",
  (event) => {
    recipeNavSwipe = null;
    if (event.pointerType === "mouse" || !event.isPrimary || !(event.target instanceof Element)) return;
    const surface = event.target.closest("[data-recipe-surface]");
    if (!surface || !recipeNavStates.has(surface.id)) return;
    if (event.target.closest("input, select, textarea, [contenteditable], [data-no-swipe]") || recipeNavScrollsSideways(event.target, surface)) return;
    recipeNavSwipe = { surface, x: event.clientX, y: event.clientY };
  },
  { passive: true },
);
document.addEventListener("pointerup", (event) => {
  const swipe = recipeNavSwipe;
  recipeNavSwipe = null;
  if (!swipe) return;
  const step = recipeNavSwipeStep(event.clientX - swipe.x, event.clientY - swipe.y);
  if (step) recipeNavGo(swipe.surface, step);
});
document.addEventListener("pointercancel", () => {
  recipeNavSwipe = null;
});

// Een andere taal: de knoppen en het label van de balken opnieuw.
window.addEventListener("ms:languagechange", () => {
  for (const id of recipeNavStates.keys()) {
    const surface = document.getElementById(id);
    if (surface) recipeNavRender(surface, { rebuild: true });
  }
});
