// Het tabblad Aanbiedingen: de aanbiedingen van een winkel (AH Bonus, Picnic en Jumbo) per categorie, en receptvoorstellen
// waarvan ingredienten in de aanbieding zijn. Alles komt uit een lokale kopie; die wordt elke dag ververst en kan de
// beheerder onder Instellingen > Koppelen meteen synchroniseren.
// Laadt na basket.js en gebruikt STORES, state, formatPrice en formatSyncTime uit app.js.

const OFFERS_STORES = ["ah", "picnic", "jumbo"];
const OFFER_ROWS = 3; // zoveel rijen aanbiedingen staan er eerst; de knop Meer voegt er steeds zoveel toe
const offersState = { store: "ah", supported: true, offers: [], categories: [], category: "all", course: "all", recipes: [], rows: OFFER_ROWS };

function offersStores() {
  return OFFERS_STORES.filter((key) => key === "ah" || state.linkedStores[key]);
}

function offersTabActive() {
  return Boolean(document.querySelector("#offers.is-active"));
}

// De winkelkeuze is het eerste uitklapmenu in de subtabbalk, zoals bij Winkels; met maar een winkel valt er niets te kiezen.
function renderOffersStore() {
  const menu = $("#offers-store-menu");
  if (!menu) return;
  const available = offersStores();
  if (!available.includes(offersState.store)) {
    let remembered = "";
    try {
      remembered = localStorage.getItem("ms-offers-store") || "";
    } catch {
      // Zonder opslag begint de pagina bij de winkel van de planner.
    }
    offersState.store = [remembered, state.preferredStore].find((key) => available.includes(key)) || "ah";
  }
  menu.hidden = available.length < 2;
  $("#offers-store-name").textContent = STORES[offersState.store].name;
  $("#offers-store-list").innerHTML = available
    .map(
      (key) =>
        `<button class="subtab-menu__item${offersState.store === key ? " is-active" : ""}" type="button" data-store="${key}">${escapeHtml(STORES[key].name)}</button>`
    )
    .join("");
}

function offersSwitchStore(store) {
  offersState.store = offersStores().includes(store) ? store : "ah";
  offersState.category = "all";
  offersState.course = "all";
  try {
    localStorage.setItem("ms-offers-store", offersState.store);
  } catch {
    // De keuze geldt dan alleen tot je de pagina herlaadt.
  }
  showOffers();
}

// De voorwaarden van een aanbieding: vanaf hoeveel stuks, wat een stuk dan kost en tot wanneer ze geldt.
function offerConditionsText(offer) {
  const parts = [];
  if (offer.minQuantity > 1) parts.push(t("offers.min_quantity", { count: offer.minQuantity }));
  if (offer.unitPrice && offer.mechanic !== "fixed" && offer.unitPrice !== offer.price) {
    parts.push(t("offers.unit_price", { price: formatPrice(offer.unitPrice) }));
  }
  const until = offer.validUntil ? new Date(`${String(offer.validUntil).slice(0, 10)}T12:00:00`) : null;
  if (until && !Number.isNaN(until.getTime())) {
    parts.push(t("offers.valid_until", { date: until.toLocaleDateString(I18N.locale(), { day: "numeric", month: "short" }) }));
  }
  return parts.join(" \u00b7 ");
}

function offerProductCard(offer) {
  const meta = [offer.brand, offer.unitSize].filter(Boolean).join(" · ");
  const was = offer.priceBefore ? ` <s>${escapeHtml(formatPrice(offer.priceBefore))}</s>` : "";
  const tag = offer.url ? "a" : "div";
  const link = offer.url ? ` href="${escapeHtml(offer.url)}" target="_blank" rel="noopener noreferrer"` : "";
  return `
    <${tag} class="product-card"${link}>
      ${offer.image ? `<img src="${escapeHtml(offer.image)}" alt="" loading="lazy" data-hide-on-error>` : ""}
      <span>
        <strong>${escapeHtml(offer.title)}</strong>
        ${meta ? `<small>${escapeHtml(meta)}</small>` : ""}
        ${offer.promoLabel ? `<small class="bonus">${escapeHtml(offer.promoLabel)}</small>` : ""}
        ${offerConditionsText(offer) ? `<small>${escapeHtml(offerConditionsText(offer))}</small>` : ""}
        ${offer.price ? `<small>${escapeHtml(formatPrice(offer.price))}${was}</small>` : ""}
      </span>
    </${tag}>`;
}

// Hoeveel producten er in een rij van het raster passen; een verborgen raster geeft de ruwe waarde, dan gokken we.
function offerColumns() {
  const columns = getComputedStyle($("#offers-products")).gridTemplateColumns;
  return columns.includes("(") ? 3 : Math.max(1, columns.split(" ").filter(Boolean).length);
}

function offerCategoryLabel(id) {
  return t(`offers.cat.${id}`);
}

function renderOfferCategories() {
  const total = offersState.offers.length;
  const chips = [{ id: "all", label: t("offers.all", { count: I18N.formatNumber(total) }) }].concat(
    offersState.categories.map((category) => ({
      id: category.id,
      label: t("offers.chip", { count: I18N.formatNumber(category.count), category: offerCategoryLabel(category.id) }),
    }))
  );
  $("#offers-categories").hidden = !total;
  $("#offers-categories").innerHTML = chips
    .map(
      (chip) =>
        `<button class="offer-chip${offersState.category === chip.id ? " is-active" : ""}" type="button" data-cat="${escapeHtml(chip.id)}" aria-pressed="${offersState.category === chip.id}">${escapeHtml(chip.label)}</button>`
    )
    .join("");
}

function renderOfferGrid() {
  const shown = offersState.category === "all" ? offersState.offers : offersState.offers.filter((offer) => offer.category === offersState.category);
  const limit = offersState.rows * offerColumns();
  $("#offers-products").innerHTML = shown.slice(0, limit).map(offerProductCard).join("");
  $("#offers-more").hidden = shown.length <= limit;
}

function renderOffers(data) {
  offersState.supported = data.supported;
  offersState.offers = data.offers || [];
  offersState.categories = data.categories || [];
  if (offersState.category !== "all" && !offersState.categories.some((category) => category.id === offersState.category)) offersState.category = "all";
  offersState.rows = OFFER_ROWS;
  const status = $("#offers-status");
  if (!data.supported) {
    status.textContent = t("offers.unsupported", { store: STORES[offersState.store].name });
    offersState.offers = [];
    offersState.categories = [];
  } else {
    status.textContent = data.syncedAt ? tn("offers.status", data.total, { when: formatSyncTime(data.syncedAt) }) : t("offers.never");
  }
  renderOfferCategories();
  renderOfferGrid();
}

function offersChooseCategory(category) {
  offersState.category = category;
  offersState.rows = OFFER_ROWS;
  renderOfferCategories();
  renderOfferGrid();
}

function offersShowMore() {
  offersState.rows += OFFER_ROWS;
  renderOfferGrid();
}

// De lijst van de vorige winkel maakt plaats voor een draaiend icoon zolang de aanbiedingen van de gekozen winkel binnenkomen.
function renderOffersLoading() {
  $("#offers-status").textContent = "";
  $("#offers-categories").hidden = true;
  $("#offers-products").innerHTML = "";
  $("#offers-products").setAttribute("aria-busy", "true");
  $("#offers-more").hidden = true;
  const box = $("#offers-loading");
  box.innerHTML = `${mdiIcon(MDI_LOADING, "offers-loading__icon")}<span>${escapeHtml(t("offers.loading", { store: STORES[offersState.store].name }))}</span>`;
  box.hidden = false;
}

function endOffersLoading() {
  $("#offers-loading").hidden = true;
  $("#offers-products").removeAttribute("aria-busy");
}

// Wisselt iemand tijdens het laden van winkel, dan telt alleen de laatste aanvraag; een traag antwoord van de vorige winkel wordt genegeerd.
let offersLoadId = 0;
// De laatst geladen lijst per winkel. Wissel je terug naar een winkel die je al zag, dan staat die lijst er meteen en controleren we op de
// achtergrond of er iets veranderd is; alleen bij een andere lijst wordt het scherm vernieuwd. De browser doet hetzelfde met een ETag (304).
const offersCache = {};

async function showOffers() {
  const loadId = ++offersLoadId;
  const store = offersState.store;
  renderOffersStore();
  message("#offers-message", "");
  const cached = offersCache[store];
  if (cached) renderOffers(cached);
  else renderOffersLoading();
  try {
    const data = await jsonFetch(`/api/offers?store=${encodeURIComponent(store)}`);
    if (loadId !== offersLoadId) return;
    endOffersLoading();
    if (!cached || JSON.stringify(cached) !== JSON.stringify(data)) renderOffers(data);
    offersCache[store] = data;
  } catch (error) {
    if (loadId !== offersLoadId) return;
    endOffersLoading();
    message("#offers-message", error.message);
  }
  showOfferRecipes();
}

function offerRecipeCard(recipe, index) {
  const matched = recipe.matched
    .map(
      (item, at) =>
        `<li>${escapeHtml(item.ingredient)} <button class="offer-match" type="button" data-recipe="${index}" data-match="${at}" aria-label="${escapeHtml(t("offers.product.open", { product: item.offer.title }))}">→ ${escapeHtml(item.offer.title)}</button><button class="offer-flag" type="button" data-recipe="${index}" data-match="${at}" title="${escapeHtml(t("offers.report.label"))}" aria-label="${escapeHtml(t("offers.report.aria", { product: item.offer.title }))}">${mdiIcon(MDI_FLAG_OUTLINE)}</button></li>`
    )
    .join("");
  const saving = recipe.saving ? `<p class="bonus">${escapeHtml(t("offers.saving", { amount: formatPrice(recipe.saving) }))}</p>` : "";
  // Staat hetzelfde recept ook in een andere bron, dan is het één kaart en staat daar de andere bron bij.
  const alsoIn = recipe.alsoIn && recipe.alsoIn.length
    ? ` \u00b7 ${t("offers.also_in", { sources: recipe.alsoIn.map((source) => t(`offers.source.${source}`)).join(", ") })}`
    : "";
  const missing = recipe.missing && recipe.missing.length ? `<p class="muted">${escapeHtml(t("offers.recipe.missing", { list: recipe.missing.join(", ") }))}</p>` : "";
  return `
    <article class="recipe-card">
      ${recipe.image ? `<img src="${escapeHtml(recipe.image)}" alt="${escapeHtml(recipe.title)}" loading="lazy" data-hide-on-error>` : ""}
      <h3>${escapeHtml(recipe.title)}</h3>
      <p class="muted">${escapeHtml(t("offers.match", { matched: recipe.matchedCount, total: recipe.ingredientCount }))}</p>
      <p class="muted">${escapeHtml(t("offers.recipe_from", { source: t(`offers.source.${recipe.source}`) }))}${alsoIn}</p>
      <ul class="offer-matches">${matched}</ul>
      ${missing}
      ${saving}
      ${recipe.url ? `<div class="recipe-card__actions"><a class="button button--secondary" href="${escapeHtml(recipe.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(t("rsearch.open"))}</a></div>` : ""}
    </article>`;
}

// Het product dat aan een ingrediënt is gekoppeld, in een venster: zo zie je meteen of de koppeling klopt.
let offerDialogMatch = null;

function openOfferProduct(recipe, item) {
  const offer = item.offer;
  offerDialogMatch = item;
  $("#offer-product-title").textContent = offer.title;
  $("#offer-product-meta").textContent = [offer.brand, offer.unitSize].filter(Boolean).join(" \u00b7 ");
  $("#offer-product-label").textContent = offer.promoLabel || "";
  const was = offer.priceBefore ? ` <s>${escapeHtml(formatPrice(offer.priceBefore))}</s>` : "";
  $("#offer-product-price").innerHTML = offer.price ? `<strong>${escapeHtml(formatPrice(offer.price))}</strong>${was}` : "";
  $("#offer-product-conditions").textContent = offerConditionsText(offer);
  const image = $("#offer-product-image");
  image.hidden = !offer.image;
  if (offer.image) image.src = offer.image;
  else image.removeAttribute("src");
  $("#offer-product-match").textContent = t("offers.product.match", { ingredient: item.ingredient, recipe: recipe.title });
  const open = $("#offer-product-open");
  open.hidden = !offer.url;
  open.textContent = t("offers.product.store", { store: STORES[offersState.store].name });
  open.dataset.url = offer.url || "";
  $("#offer-product-nopage").hidden = Boolean(offer.url);
  $("#offer-product-dialog").showModal();
}

function renderOfferCourses(courses) {
  const total = courses.reduce((sum, course) => sum + course.count, 0);
  const chips = [{ id: "all", label: t("offers.all", { count: I18N.formatNumber(total) }) }].concat(
    courses.map((course) => ({
      id: course.id,
      label: t("offers.chip", { count: I18N.formatNumber(course.count), category: t(`offers.course.${course.id}`) }),
    }))
  );
  $("#offers-courses").hidden = !total;
  $("#offers-courses").innerHTML = chips
    .map(
      (chip) =>
        `<button class="offer-chip${offersState.course === chip.id ? " is-active" : ""}" type="button" data-course="${escapeHtml(chip.id)}" aria-pressed="${offersState.course === chip.id}">${escapeHtml(chip.label)}</button>`
    )
    .join("");
}

function offersChooseCourse(course) {
  offersState.course = course;
  showOfferRecipes();
}

function offerIndexSummary(sources) {
  return Object.entries(sources || {})
    .map(([name, info]) => {
      const label = t(`offers.source.${name}`);
      if (info.error) return `${label}: ${info.error}`;
      if (info.skipped) return `${label}: ${info.skipped}`;
      return `${label}: ${I18N.formatNumber(info.count)}`;
    })
    .join(" · ");
}

async function showOfferRecipes() {
  // Het subtabblad Recepten staat standaard verborgen (MEALIESHOPPER_OFFERS_RECIPES); dan is er niets te laden.
  if (document.querySelector('.subtab[data-subtab="offers-suggestions"]').hidden) return;
  const status = $("#offers-recipes-status");
  const list = $("#offers-recipes");
  message("#offers-recipes-message", "");
  $("#offers-recipes-heading").textContent = t("offers.recipes.heading", { store: STORES[offersState.store].name });
  if (!offersState.supported) {
    status.textContent = "";
    list.innerHTML = "";
    $("#offers-courses").hidden = true;
    return;
  }
  try {
    const course = offersState.course === "all" ? "" : `&course=${encodeURIComponent(offersState.course)}`;
    const data = await jsonFetch(`/api/offers/recipes?store=${encodeURIComponent(offersState.store)}${course}`);
    // Staat de gekozen soort er na het opnieuw inlezen niet meer bij, dan tonen we weer alles.
    if (offersState.course !== "all" && !(data.courses || []).some((item) => item.id === offersState.course)) {
      offersState.course = "all";
      return showOfferRecipes();
    }
    renderOfferCourses(data.courses || []);
    if (!data.indexedAt) {
      status.textContent = t("offers.recipes.never");
    } else if (!data.offersSyncedAt) {
      status.textContent = t("offers.recipes.no_offers");
    } else {
      status.textContent =
        (data.recipes.length ? tn("offers.recipes.count", data.total) : t("offers.recipes.none")) +
        ` · ${t("offers.recipes.indexed", { when: formatSyncTime(data.indexedAt) })} (${offerIndexSummary(data.sources)})`;
    }
    offersState.recipes = data.recipes;
    list.innerHTML = data.recipes.map(offerRecipeCard).join("");
  } catch (error) {
    message("#offers-recipes-message", error.message);
  }
}

// Een koppeling als onjuist melden: ze komt niet meer terug, ook niet bij andere recepten met hetzelfde ingrediënt. Ongedaan maken kan direct.
async function reportOfferMismatch(item, undo = false) {
  const message_ = $("#offers-recipes-message");
  try {
    await jsonFetch("/api/offers/rejections", {
      method: undo ? "DELETE" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ store: offersState.store, ingredient: item.ingredient, offerId: item.offer.id }),
    });
    await showOfferRecipes();
    if (undo) return;
    message_.innerHTML = `<div class="message success message--action" role="status"><span>${escapeHtml(
      t("offers.report.done", { product: item.offer.title, ingredient: item.ingredient })
    )}</span><button class="inline-button" type="button" id="offers-report-undo">${escapeHtml(t("offers.report.undo"))}</button></div>`;
    $("#offers-report-undo").addEventListener("click", () => reportOfferMismatch(item, true));
  } catch (error) {
    message("#offers-recipes-message", error.message);
  }
}

async function offersIndexRecipes() {
  const button = $("#offers-index");
  message("#offers-recipes-message", "");
  setBusy(button, true, t("offers.recipes.indexing"));
  try {
    await jsonFetch("/api/offers/recipes/sync", { method: "POST" });
    await showOfferRecipes();
  } catch (error) {
    message("#offers-recipes-message", error.message);
  } finally {
    setBusy(button, false, t("offers.recipes.indexing"));
  }
}

$("#offers-more").addEventListener("click", offersShowMore);
$("#offers-recipes").addEventListener("click", (event) => {
  const button = event.target.closest(".offer-match, .offer-flag");
  if (!button) return;
  const recipe = offersState.recipes[Number(button.dataset.recipe)];
  const item = recipe && recipe.matched[Number(button.dataset.match)];
  if (!item) return;
  if (button.classList.contains("offer-flag")) reportOfferMismatch(item);
  else openOfferProduct(recipe, item);
});
$("#offer-product-report").addEventListener("click", () => {
  const item = offerDialogMatch;
  $("#offer-product-dialog").close();
  if (item) reportOfferMismatch(item);
});
$("#offer-product-open").addEventListener("click", (event) => {
  if (event.currentTarget.dataset.url) openProductPopup(event.currentTarget.dataset.url);
});
$("#offer-product-close").addEventListener("click", () => $("#offer-product-dialog").close());
// Een plaatje dat niet laadt verbergen we, en halen we niet weg (zoals data-hide-on-error doet): het venster gebruikt hetzelfde plaatje opnieuw.
$("#offer-product-image").addEventListener("error", (event) => {
  event.target.hidden = true;
});
// Een klik naast het venster (op de achtergrond) sluit het ook.
$("#offer-product-dialog").addEventListener("click", (event) => {
  if (event.target === event.currentTarget) event.currentTarget.close();
});
$("#offers-courses").addEventListener("click", (event) => {
  const chip = event.target.closest("[data-course]");
  if (chip) offersChooseCourse(chip.dataset.course);
});
$("#offers-categories").addEventListener("click", (event) => {
  const chip = event.target.closest("[data-cat]");
  if (chip) offersChooseCategory(chip.dataset.cat);
});
let offersResizeFrame = 0;
window.addEventListener("resize", () => {
  if (!offersTabActive()) return;
  cancelAnimationFrame(offersResizeFrame);
  offersResizeFrame = requestAnimationFrame(renderOfferGrid);
});
$("#offers-index").addEventListener("click", offersIndexRecipes);
$("#offers-store-list").addEventListener("click", (event) => {
  const item = event.target.closest("[data-store]");
  if (!item) return;
  setSubtabMenu($("#offers-store-menu"), false);
  offersSwitchStore(item.dataset.store);
});
window.addEventListener("ms:languagechange", () => {
  renderOffersStore();
  if (offersTabActive()) showOffers();
});
renderOffersStore();

// ---- Instellingen > Koppelen: de aanbiedingen van alle winkels synchroniseren, en wat de dagelijkse taak deed.
const offersSync = { status: null, result: null };

function offersSyncStoreLine(store, info, failure) {
  const name = STORES[store].name;
  if (failure) return t("offers.sync.store.error", { store: name, error: failure });
  if (!info.linked) return t("offers.sync.store.skipped", { store: name });
  if (!info.syncedAt) return t("offers.sync.store.never", { store: name });
  return tn("offers.sync.store.ok", info.total, { store: name, when: formatSyncTime(info.syncedAt) });
}

function renderOffersSync() {
  const status = offersSync.status;
  if (!status) return;
  const failures = {};
  for (const [store, info] of Object.entries((offersSync.result || status.lastResult || {}).stores || {})) {
    if (info.error) failures[store] = info.error;
  }
  $("#offers-sync-stores").innerHTML = OFFERS_STORES.map(
    (store) => `<li class="${failures[store] ? "offers-sync-list__failed" : ""}">${escapeHtml(offersSyncStoreLine(store, status.stores[store], failures[store]))}</li>`
  ).join("");
  const lines = [];
  if (status.enabled) {
    lines.push(t("offers.sync.schedule", { hour: String(status.hour).padStart(2, "0"), next: formatSyncTime(status.nextRun) }));
  } else {
    lines.push(t("offers.sync.off"));
  }
  if (status.lastRun) {
    lines.push(t("offers.sync.last", { when: formatSyncTime(status.lastRun), trigger: t(status.lastTrigger === "scheduled" ? "offers.sync.trigger.scheduled" : "offers.sync.trigger.manual") }));
  }
  $("#offers-sync-schedule").textContent = lines.join(" ");
}

async function loadOffersSyncStatus() {
  try {
    offersSync.status = await jsonFetch("/api/offers/status");
    renderOffersSync();
  } catch (error) {
    message("#offers-sync-message", error.message);
  }
}

async function offersSyncAll() {
  const button = $("#offers-sync-all");
  message("#offers-sync-message", "");
  setBusy(button, true, t("offers.sync.running"));
  try {
    offersSync.result = await jsonFetch("/api/offers/sync-all", { method: "POST" });
    await loadOffersSyncStatus();
    const failed = Object.values(offersSync.result.stores).some((info) => info.error);
    message("#offers-sync-message", failed ? t("offers.sync.partial") : t("offers.sync.done"), failed ? "error" : "success");
    if (offersTabActive()) showOffers();
  } catch (error) {
    message("#offers-sync-message", error.message);
  } finally {
    setBusy(button, false, t("offers.sync.running"));
  }
}

$("#offers-sync-all").addEventListener("click", offersSyncAll);
window.addEventListener("ms:languagechange", renderOffersSync);
