// Maaltijdboxen: het weekmenu van een aanbieder (HelloFresh, Marley Spoon; openbare gegevens, geen account) bekijken, doorzoeken
// en, waar de aanbieder het toelaat, een recept in Mealie importeren.
// Laadt na app.js en gebruikt daar de hulpfuncties van ($, message, jsonFetch, postJson, setBusy, t, matchesWords, ...).

// Per aanbieder de naam en of we het recept kunnen importeren; de routes staan onder /api/<aanbieder>/.
const MEALKIT_PROVIDERS = {
  hellofresh: { name: "HelloFresh", canImport: true },
  marleyspoon: { name: "Marley Spoon", canImport: false },
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

function renderMealKitCard(recipe) {
  const meta = mealKitMeta(recipe);
  const provider = mealKitProvider();
  return `
    <article class="recipe-card" data-mealkit-recipe="${escapeHtml(recipe.key)}">
      ${recipe.image ? `<img src="${escapeHtml(recipe.image)}" alt="${escapeHtml(recipe.name)}" loading="lazy" referrerpolicy="no-referrer" data-hide-on-error>` : ""}
      <h3>${escapeHtml(recipe.name)}</h3>
      ${recipe.headline ? `<p class="recipe-card__headline">${escapeHtml(recipe.headline)}</p>` : ""}
      ${meta ? `<p class="muted">${escapeHtml(meta)}</p>` : ""}
      <div class="card-actions">
        <a class="button button--secondary" href="${escapeHtml(recipe.url)}" target="_blank" rel="noreferrer noopener">${escapeHtml(t("mealkits.open", { provider: provider.name }))}</a>
        ${provider.canImport ? `<button class="button" type="button" data-mealkit-import="${escapeHtml(recipe.key)}">${escapeHtml(t("recipe.import"))}</button>` : ""}
      </div>
      <div class="recipe-import-result"></div>
    </article>`;
}

// De teksten die de naam van de aanbieder dragen; ze staan niet in de HTML omdat de aanbieder wisselt.
function renderMealKitTexts() {
  const provider = mealKitProvider();
  $("#mealkits-title").textContent = t("mealkits.title", { provider: provider.name });
  $("#mealkits-intro").textContent = t(provider.canImport ? "mealkits.intro" : "mealkits.intro_open_only", { provider: provider.name });
  $("#mealkits-source").textContent = t("mealkits.source", { provider: provider.name });
  $("#mealkits-provider").value = mealKits.provider;
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
  $("#mealkits-this-week").disabled = !menu || menu.week === menu.currentWeek;
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
  $("#mealkits-status").textContent = t("basket.loading");
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

async function importMealKit(key, button, target) {
  button.disabled = true;
  button.textContent = t("recipe.importing");
  try {
    const data = await postJson(`/api/${mealKits.provider}/recipes/${encodeURIComponent(key)}/import`);
    const link = data.mealieUrl ? ` <a href="${escapeHtml(data.mealieUrl)}" target="_blank" rel="noreferrer noopener">${escapeHtml(t("recipe.view_mealie"))}</a>` : "";
    target.innerHTML = `<div class="message success">${escapeHtml(t("recipe.imported"))}${link}</div>`;
  } catch (error) {
    target.innerHTML = `<div class="message error">${escapeHtml(error.message)}</div>`;
  } finally {
    button.disabled = false;
    button.textContent = t("recipe.import");
  }
}

$("#mealkits-provider").addEventListener("change", (event) => changeMealKitProvider(event.target.value));
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
  const button = event.target.closest("[data-mealkit-import]");
  if (button) importMealKit(button.dataset.mealkitImport, button, button.closest(".recipe-card").querySelector(".recipe-import-result"));
});
window.addEventListener("ms:languagechange", renderMealKits);
