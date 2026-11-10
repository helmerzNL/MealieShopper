// Maaltijdboxen: het weekmenu van een aanbieder (HelloFresh, Marley Spoon; openbare gegevens, geen account) bekijken, doorzoeken,
// een recept bekijken en inplannen (en zo in Mealie zetten). De aanbieder kies je in het uitklapmenu van de subtabbalk, zoals de winkel bij Winkels.
// Laadt na app.js en gebruikt daar de hulpfuncties van ($, message, jsonFetch, postJson, setBusy, t, matchesWords, ...).

// Per aanbieder de naam; de routes staan onder /api/<aanbieder>/.
const MEALKIT_PROVIDERS = {
  hellofresh: { name: "HelloFresh" },
  marleyspoon: { name: "Marley Spoon" },
};

const mealKits = { provider: "hellofresh", menu: null, search: null, query: "", loaded: false, run: 0 };

function mealKitProvider() {
  return MEALKIT_PROVIDERS[mealKits.provider];
}

function mealKitWeekLabel(code) {
  const found = /^(\d{4})-W(\d{2})$/.exec(code || "");
  return found ? t("mealkits.week", { week: Number(found[2]), year: found[1] }) : "";
}

function mealKitMeta(recipe) {
  return [
    recipe.minutes ? t("mealkits.minutes", { count: recipe.minutes }) : "",
    recipe.cuisine,
    recipe.label,
    recipe.week ? mealKitWeekLabel(recipe.week) : "",
  ]
    .filter(Boolean)
    .join(" · ");
}

// Dezelfde knoppen als elke receptkaart: Bekijken (in MealieShopper, met de link naar de aanbieder) en Inplannen.
function renderMealKitCard(recipe) {
  const meta = mealKitMeta(recipe);
  const store = mealKits.provider;
  return `
    <article class="recipe-card" data-mealkit-recipe="${escapeHtml(recipe.key)}">
      ${recipe.image ? `<img src="${escapeHtml(recipe.image)}" alt="" loading="lazy" referrerpolicy="no-referrer" data-hide-on-error>` : ""}
      ${recipeCardTitle(recipe.name, `${store}:${recipe.key}`)}
      ${recipe.headline ? `<p class="recipe-card__headline">${escapeHtml(recipe.headline)}</p>` : ""}
      ${meta ? `<p class="muted">${escapeHtml(meta)}</p>` : ""}
      ${recipeCardActions(planSourceAttrs(store, recipe.key, recipe.name, recipe.image), recipe.name)}
    </article>`;
}

// De teksten die de naam van de aanbieder dragen; ze staan niet in de HTML omdat de aanbieder wisselt.
function renderMealKitTexts() {
  const provider = mealKitProvider();
  $("#mealkits-intro").textContent = t("mealkits.intro", { provider: provider.name });
  $("#mealkits-source").textContent = t("mealkits.source", { provider: provider.name });
  $("#mealkits-provider-name").textContent = provider.name;
  document.querySelectorAll("#mealkits-provider-list [data-provider]").forEach((item) => item.classList.toggle("is-active", item.dataset.provider === mealKits.provider));
}

function renderMealKits() {
  const menu = mealKits.menu;
  const query = mealKits.query.trim();
  const searching = Boolean(mealKits.search);
  const all = searching ? mealKits.search.recipes : menu ? menu.recipes : [];
  const shown = !searching && query ? all.filter((recipe) => matchesWords(`${recipe.name} ${recipe.headline} ${recipe.cuisine} ${recipe.label}`, query)) : all;

  renderMealKitTexts();
  $("#mealkits-week").textContent = menu ? mealKitWeekLabel(menu.week) : "";
  $("#mealkits-previous").disabled = !menu;
  $("#mealkits-next").disabled = !menu;
  // Deze week alleen als je een andere week bekijkt (verborgen in plaats van uitgeschakeld, sinds 4.8.0).
  $("#mealkits-this-week").hidden = !menu || menu.week === menu.currentWeek;
  $("#mealkits-filter").disabled = !menu;
  $("#mealkits-search-all").disabled = !menu || !query;
  $("#mealkits-recipes").innerHTML = shown.map(renderMealKitCard).join("");

  if (searching) $("#mealkits-status").textContent = t("mealkits.search_count", { count: I18N.formatNumber(mealKits.search.total), weeks: I18N.formatNumber(mealKits.search.weeks) });
  else $("#mealkits-status").textContent = menu && all.length ? t("mealkits.count", { count: I18N.formatNumber(all.length) }) : "";
  if (!mealKits.loaded) return;
  const empty = searching ? t("mealkits.search_none", { query }) : !all.length ? t("mealkits.none") : t("mealkits.none_match", { query });
  message("#mealkits-message", shown.length ? "" : empty, "success");
}

async function loadMealKits(week, { refresh = false } = {}) {
  const run = ++mealKits.run;
  const button = $("#mealkits-refresh");
  setBusy(button, true);
  message("#mealkits-message", "");
  $("#mealkits-status").textContent = t("common.loading");
  try {
    const params = new URLSearchParams();
    if (week) params.set("week", week);
    if (refresh) params.set("refresh", "1");
    const data = await jsonFetch(`/api/${mealKits.provider}/menu?${params}`);
    if (run !== mealKits.run) return;
    mealKits.menu = data;
    mealKits.search = null;
    mealKits.loaded = true;
    renderMealKits();
  } catch (error) {
    if (run !== mealKits.run) return;
    mealKits.loaded = true;
    renderMealKits();
    message("#mealkits-message", error.message);
  } finally {
    if (run === mealKits.run) setBusy(button, false);
  }
}

function showMealKits() {
  if (!mealKits.loaded) loadMealKits();
}

function changeMealKitProvider(provider) {
  if (!MEALKIT_PROVIDERS[provider] || provider === mealKits.provider) return;
  Object.assign(mealKits, { provider, menu: null, search: null, query: "", loaded: false });
  showRecipeList(MEALKIT_HOST);
  $("#mealkits-filter").value = "";
  $("#mealkits-recipes").innerHTML = "";
  renderMealKits();
  loadMealKits();
}

async function searchMealKits() {
  const query = mealKits.query.trim();
  if (!query) return;
  const run = mealKits.run;
  const button = $("#mealkits-search-all");
  setBusy(button, true);
  message("#mealkits-message", "");
  try {
    const found = await jsonFetch(`/api/${mealKits.provider}/search?q=${encodeURIComponent(query)}`);
    if (run !== mealKits.run) return;
    mealKits.search = found;
    renderMealKits();
  } catch (error) {
    if (run === mealKits.run) message("#mealkits-message", error.message);
  } finally {
    setBusy(button, false);
    $("#mealkits-search-all").disabled = !mealKits.query.trim();
  }
}

$("#mealkits-provider-list").addEventListener("click", (event) => {
  const item = event.target.closest("[data-provider]");
  if (!item) return;
  setSubtabMenu($("#mealkits-provider-menu"), false);
  changeMealKitProvider(item.dataset.provider);
});
$("#mealkits-refresh").addEventListener("click", () => loadMealKits(mealKits.menu?.week, { refresh: true }));
$("#mealkits-previous").addEventListener("click", () => mealKits.menu && loadMealKits(mealKits.menu.previous));
$("#mealkits-next").addEventListener("click", () => mealKits.menu && loadMealKits(mealKits.menu.next));
$("#mealkits-this-week").addEventListener("click", () => mealKits.menu && loadMealKits(mealKits.menu.currentWeek));
$("#mealkits-filter").addEventListener("input", (event) => {
  mealKits.query = event.target.value;
  mealKits.search = null;
  renderMealKits();
});
$("#mealkits-search-form").addEventListener("submit", (event) => {
  event.preventDefault();
  searchMealKits();
});
$("#mealkits-recipes").addEventListener("click", (event) => {
  const open = event.target.closest("[data-store-open]");
  if (open) openStoreRecipe(mealKits.provider, open.dataset.storeOpen.split(":", 2)[1], MEALKIT_HOST);
});
window.addEventListener("ms:languagechange", renderMealKits);
