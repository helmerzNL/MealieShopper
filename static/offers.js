// Het tabblad Aanbiedingen: de aanbiedingen van een winkel (voor nu AH Bonus) en receptvoorstellen waarvan
// ingredienten in de aanbieding zijn. Alles komt uit een lokale kopie; vernieuwen gaat op verzoek.
// Laadt na basket.js en gebruikt STORES, state, formatPrice en formatSyncTime uit app.js.

const OFFERS_STORES = ["ah", "picnic", "jumbo"];
const offersState = { store: "ah", supported: true };

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
  try {
    localStorage.setItem("ms-offers-store", offersState.store);
  } catch {
    // De keuze geldt dan alleen tot je de pagina herlaadt.
  }
  showOffers();
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
        ${offer.price ? `<small>${escapeHtml(formatPrice(offer.price))}${was}</small>` : ""}
      </span>
    </${tag}>`;
}

function renderOffers(data) {
  offersState.supported = data.supported;
  const status = $("#offers-status");
  const list = $("#offers-products");
  $("#offers-refresh").hidden = !data.supported;
  if (!data.supported) {
    status.textContent = t("offers.unsupported", { store: STORES[offersState.store].name });
    list.innerHTML = "";
    return;
  }
  status.textContent = data.syncedAt
    ? tn("offers.status", data.total, { when: formatSyncTime(data.syncedAt) })
    : t("offers.never");
  list.innerHTML = data.offers.map(offerProductCard).join("");
}

async function showOffers() {
  renderOffersStore();
  message("#offers-message", "");
  try {
    renderOffers(await jsonFetch(`/api/offers?store=${encodeURIComponent(offersState.store)}`));
  } catch (error) {
    message("#offers-message", error.message);
  }
  showOfferRecipes();
}

async function offersRefresh() {
  const button = $("#offers-refresh");
  message("#offers-message", "");
  setBusy(button, true, t("offers.fetching"));
  try {
    renderOffers(
      await jsonFetch("/api/offers/sync", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ store: offersState.store }),
      })
    );
    await showOfferRecipes();
  } catch (error) {
    message("#offers-message", error.message);
  } finally {
    setBusy(button, false, t("offers.fetching"));
  }
}

function offerRecipeCard(recipe) {
  const matched = recipe.matched
    .map((item) => `<li>${escapeHtml(item.ingredient)} <span class="muted">→ ${escapeHtml(item.offer.title)}</span></li>`)
    .join("");
  const saving = recipe.saving ? `<p class="bonus">${escapeHtml(t("offers.saving", { amount: formatPrice(recipe.saving) }))}</p>` : "";
  return `
    <article class="recipe-card">
      ${recipe.image ? `<img src="${escapeHtml(recipe.image)}" alt="${escapeHtml(recipe.title)}" loading="lazy" data-hide-on-error>` : ""}
      <h3>${escapeHtml(recipe.title)}</h3>
      <p class="muted">${escapeHtml(t("offers.match", { matched: recipe.matchedCount, total: recipe.ingredientCount }))} · ${escapeHtml(t(`offers.source.${recipe.source}`))}</p>
      <ul class="offer-matches">${matched}</ul>
      ${saving}
      ${recipe.url ? `<div class="recipe-card__actions"><a class="button button--secondary" href="${escapeHtml(recipe.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(t("rsearch.open"))}</a></div>` : ""}
    </article>`;
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
  const status = $("#offers-recipes-status");
  const list = $("#offers-recipes");
  message("#offers-recipes-message", "");
  if (!offersState.supported) {
    status.textContent = "";
    list.innerHTML = "";
    return;
  }
  try {
    const data = await jsonFetch(`/api/offers/recipes?store=${encodeURIComponent(offersState.store)}`);
    if (!data.indexedAt) {
      status.textContent = t("offers.recipes.never");
    } else if (!data.offersSyncedAt) {
      status.textContent = t("offers.recipes.no_offers");
    } else {
      status.textContent =
        (data.recipes.length ? tn("offers.recipes.count", data.total) : t("offers.recipes.none")) +
        ` · ${t("offers.recipes.indexed", { when: formatSyncTime(data.indexedAt) })} (${offerIndexSummary(data.sources)})`;
    }
    list.innerHTML = data.recipes.map(offerRecipeCard).join("");
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

$("#offers-refresh").addEventListener("click", offersRefresh);
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
