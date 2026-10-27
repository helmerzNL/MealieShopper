// Recepten zoeken: tegelijk bij Albert Heijn, Jumbo en Picnic (de laatste twee zodra ze gekoppeld zijn) en in de menu's van
// de maaltijdboxen HelloFresh en Marley Spoon (zonder koppeling; de komende weken worden bij het zoeken opgehaald).
// Elke winkel heeft zijn eigen blok met resultaten; een winkel die faalt, laat de andere gewoon staan.
// Bij Jumbo, Picnic en de maaltijdboxen opent `Bekijken` het recept met ingrediënten en bereiding in dit tabblad.
// Laadt na app.js en gebruikt daar de hulpfuncties ($, message, jsonFetch, postJson, setBusy, t, tn, ...).

const SEARCH_STORES = ["ah", "jumbo", "picnic", "hellofresh", "marleyspoon"];
const MEALKIT_SEARCH = new Set(["hellofresh", "marleyspoon"]); // zoeken in menu's; elke aanbieder heeft eigen routes onder /api/<aanbieder>/
const MEALKIT_SEARCH_WEEKS = 4; // deze week en de 3 weken erna
const recipeSearch = {
  query: "",
  ah: { recipes: [], total: 0, page: 0, more: false },
  jumbo: { recipes: [], total: 0, more: false },
  picnic: { recipes: [], total: 0, more: false },
  hellofresh: { recipes: [], total: 0, more: false },
  marleyspoon: { recipes: [], total: 0, more: false },
  searched: false,
};

function searchStoreActive(store) {
  return store === "ah" || MEALKIT_SEARCH.has(store) || Boolean(state.linkedStores[store]);
}

// De elementen van een blok; AH behoudt zijn oorspronkelijke id's.
function searchParts(store) {
  const prefix = store === "ah" ? "#search-ah" : `#search-${store}`;
  return {
    section: $(prefix),
    count: $(store === "ah" ? "#search-count" : `${prefix}-count`),
    message: $(`${prefix}-message`),
    results: $(store === "ah" ? "#recipe-results" : `${prefix}-results`),
    more: $(`${prefix}-more`),
  };
}

function storeRecipeMeta(recipe) {
  return [
    recipe.minutes ? t("rsearch.minutes", { count: recipe.minutes }) : "",
    recipe.portions ? t("rsearch.portions", { count: recipe.portions }) : "",
    recipe.rating ? `★ ${I18N.formatNumber(recipe.rating)}` : "",
    ...(recipe.tags || []),
  ]
    .filter(Boolean)
    .join(" · ");
}

function renderStoreRecipeCard(store, recipe) {
  const meta = storeRecipeMeta(recipe);
  return `
    <article class="recipe-card" data-store-recipe="${store}:${escapeHtml(recipe.id)}">
      ${recipe.image ? `<img src="${escapeHtml(recipe.image)}" alt="${escapeHtml(recipe.name)}" loading="lazy" data-hide-on-error>` : ""}
      <h3>${escapeHtml(recipe.name)}</h3>
      ${meta ? `<p class="muted">${escapeHtml(meta)}</p>` : ""}
      <div class="recipe-card__actions">
        <button class="button button--secondary" type="button" data-store-open="${store}:${escapeHtml(recipe.id)}">${escapeHtml(t("rsearch.open"))}</button>
        <button class="button" type="button" data-store-import="${store}:${escapeHtml(recipe.id)}">${escapeHtml(t("recipe.import"))}</button>
      </div>
      <div class="recipe-import-result"></div>
    </article>`;
}

function renderSearchStore(store) {
  const data = recipeSearch[store];
  const parts = searchParts(store);
  parts.section.hidden = !searchStoreActive(store) || !recipeSearch.searched;
  parts.results.innerHTML = data.recipes.map((recipe) => (store === "ah" ? renderRecipeCard(recipe) : renderStoreRecipeCard(store, recipe))).join("");
  if (store === "ah") {
    parts.count.textContent = data.recipes.length ? tn("search.count", data.total, { limit: data.total > 2000 ? t("search.limit") : "" }) : "";
    parts.results.querySelectorAll("[data-import-url]").forEach((button) => {
      button.addEventListener("click", async () => {
        const result = await importUrl(button.dataset.importUrl, button);
        button.closest(".recipe-card").querySelector(".recipe-import-result").innerHTML = `<div class="message ${result.ok ? "success" : "error"}">${result.html}</div>`;
      });
    });
  } else {
    parts.count.textContent = data.recipes.length ? (data.more ? t("rsearch.count", { shown: I18N.formatNumber(data.recipes.length), total: I18N.formatNumber(data.total) }) : tn("search.count", data.total, { limit: "" })) : "";
  }
  if (parts.more) parts.more.hidden = !data.more; // Picnic heeft maar één pagina
  if (recipeSearch.searched && !data.recipes.length && !parts.message.textContent) {
    message(`#${parts.message.id}`, t("rsearch.none", { query: recipeSearch.query }), "success");
  }
}

// Een recept uit een weekmenu in de vorm van de andere winkelkaarten: de sleutel is het id, label, keuken en week zijn de etiketten.
function mealKitSearchCard(recipe) {
  const week = /^(\d{4})-W(\d{2})$/.exec(recipe.week || "");
  return {
    id: recipe.key,
    name: recipe.name,
    image: recipe.image,
    minutes: recipe.minutes,
    tags: [recipe.label, recipe.cuisine, week ? t("mealkits.week", { week: Number(week[2]), year: week[1] }) : ""],
  };
}

// Eén pagina van één winkel; `append` voegt de volgende pagina toe aan wat er staat.
async function fetchSearchStore(store, append) {
  const data = recipeSearch[store];
  const query = recipeSearch.query;
  const q = encodeURIComponent(query);
  const parts = searchParts(store);
  message(`#${parts.message.id}`, "");
  try {
    let found;
    if (store === "ah") {
      const page = append ? data.page + 1 : 0;
      const answer = await jsonFetch(`/api/ah/search?q=${q}&page=${page}`);
      found = { recipes: answer.recipes || [], total: Number(answer.total) || 0, page, more: Boolean(answer.hasNextPage ?? Number(answer.total) > (page + 1) * 12) };
    } else if (store === "jumbo") {
      const answer = await jsonFetch(`/api/jumbo/recipes?q=${q}&offset=${append ? data.recipes.length : 0}`);
      found = { recipes: answer.recipes || [], total: answer.total ?? (answer.recipes || []).length, more: false };
    } else if (MEALKIT_SEARCH.has(store)) {
      const answer = await jsonFetch(`/api/${store}/search?q=${q}&load=${MEALKIT_SEARCH_WEEKS}`);
      // Er komen er hooguit 60 terug: bij meer treffers staat er "60 van 101 recepten" (verfijn dan het zoekwoord); er is geen "Meer recepten".
      const recipes = (answer.recipes || []).map(mealKitSearchCard);
      found = { recipes, total: answer.total ?? recipes.length, more: Number(answer.total) > recipes.length };
    } else {
      const answer = await jsonFetch(`/api/picnic/recipes?q=${q}`);
      found = { recipes: answer.recipes || [], total: answer.total ?? (answer.recipes || []).length, more: false };
    }
    if (query !== recipeSearch.query) return; // intussen is er opnieuw gezocht
    const known = new Set(append ? data.recipes.map((recipe) => String(recipe.id)) : []);
    const fresh = found.recipes.filter((recipe) => !known.has(String(recipe.id)));
    data.recipes = append ? [...data.recipes, ...fresh] : fresh;
    data.total = found.total;
    data.page = found.page ?? 0;
    // Jumbo geeft per pagina 24 recepten; een pagina zonder nieuwe recepten of het totaal bereikt: geen "Meer recepten".
    data.more = store === "jumbo" ? Boolean(fresh.length) && data.recipes.length < data.total : found.more && (store !== "ah" || Boolean(fresh.length));
  } catch (error) {
    if (query !== recipeSearch.query) return;
    if (!append) data.recipes = [];
    data.more = false;
    message(`#${parts.message.id}`, error.message);
  }
  renderSearchStore(store);
}

async function runRecipeSearch(event) {
  event.preventDefault();
  const query = $("#recipe-query").value.trim();
  if (!query) return;
  recipeSearch.query = query;
  recipeSearch.searched = true;
  message("#search-message", "");
  const active = SEARCH_STORES.filter(searchStoreActive);
  SEARCH_STORES.forEach((store) => {
    Object.assign(recipeSearch[store], { recipes: [], total: 0, page: 0, more: false });
    const parts = searchParts(store);
    message(`#${parts.message.id}`, "");
    parts.section.hidden = !active.includes(store);
    parts.results.innerHTML = "";
    parts.count.textContent = "";
    if (parts.more) parts.more.hidden = true;
  });
  const button = $("#search-form button[type='submit']");
  setBusy(button, true);
  await Promise.all(active.map((store) => fetchSearchStore(store, false)));
  setBusy(button, false);
}

// ---- Een recept van Jumbo of Picnic openen: ingrediënten en bereiding, en importeren in Mealie
async function importStoreRecipe(store, id, button, target) {
  button.disabled = true;
  button.textContent = t("recipe.importing");
  try {
    const data = await postJson(`/api/${store}/recipes/${encodeURIComponent(id)}/import`);
    const link = data.mealieUrl ? ` <a href="${escapeHtml(data.mealieUrl)}" target="_blank" rel="noreferrer noopener">${escapeHtml(t("recipe.view_mealie"))}</a>` : "";
    target.innerHTML = `<div class="message success">${escapeHtml(t("recipe.imported"))}${link}</div>`;
  } catch (error) {
    target.innerHTML = `<div class="message error">${escapeHtml(error.message)}</div>`;
  } finally {
    button.disabled = false;
    button.textContent = t("recipe.import");
  }
}

// Het detailscherm hoort bij een "gastheer": het zoekresultaat (`search-detail`) of de favorieten van Jumbo (`jumbo-recipe`).
// De elementen heten `#<voorvoegsel>-title`, `-body`, `-message`, `-import` en `-import-result`.
const SEARCH_HOST = { prefix: "search-detail", list: "#search-main", detail: "#search-detail", known: (store) => recipeSearch[store].recipes };
const FAVORITES_HOST = { prefix: "jumbo-recipe", list: "#jumbo-favorites-view", detail: "#jumbo-recipe-view", known: () => jumboFavorites.recipes };

function showRecipeList(host) {
  $(host.detail).hidden = true;
  $(host.list).hidden = false;
}

async function openStoreRecipe(store, id, host = SEARCH_HOST) {
  $(host.list).hidden = true;
  $(host.detail).hidden = false;
  $(`#${host.prefix}-title`).textContent = host.known(store).find((recipe) => String(recipe.id) === String(id))?.name || "";
  $(`#${host.prefix}-body`).innerHTML = `<p class="muted">${escapeHtml(t("basket.loading"))}</p>`;
  message(`#${host.prefix}-message`, "");
  $(`#${host.prefix}-title`).focus();
  try {
    renderStoreRecipeDetail(store, await jsonFetch(`/api/${store}/recipes/${encodeURIComponent(id)}`), host);
  } catch (error) {
    $(`#${host.prefix}-body`).innerHTML = "";
    message(`#${host.prefix}-message`, error.message);
  }
}

function renderStoreRecipeDetail(store, recipe, host) {
  const prefix = host.prefix;
  $(`#${prefix}-title`).textContent = recipe.name;
  const facts = [
    recipe.portions ? t("rsearch.portions", { count: recipe.portions }) : "",
    recipe.minutes ? t("rsearch.minutes", { count: recipe.minutes }) : "",
    recipe.rating ? `\u2605 ${I18N.formatNumber(recipe.rating)}${recipe.ratingCount ? ` (${I18N.formatNumber(recipe.ratingCount)})` : ""}` : "",
    recipe.author || "",
  ].filter(Boolean);
  const ingredients = (recipe.ingredients || []).map((item) => `<li>${escapeHtml(item.line)}</li>`).join("");
  const steps = (recipe.steps || []).map((text) => `<li>${escapeHtml(text)}</li>`).join("");
  const nutrition = (recipe.nutrition || []).map((item) => `${item.name} ${I18N.formatNumber(item.quantity)} ${item.unit}`.trim()).join(" \u00b7 ");
  $(`#${prefix}-body`).innerHTML = `
    <div class="picnic-recipe">
      ${recipe.image ? `<img class="picnic-recipe__image" src="${escapeHtml(recipe.image)}" alt="" data-hide-on-error>` : ""}
      ${facts.length ? `<p class="muted">${escapeHtml(facts.join(" \u00b7 "))}</p>` : ""}
      ${recipe.description ? `<p>${escapeHtml(recipe.description)}</p>` : ""}
      <p>
        <button class="button" type="button" id="${prefix}-import">${escapeHtml(t("recipe.import"))}</button>
        ${recipe.url ? `<a class="button button--secondary" href="${escapeHtml(recipe.url)}" target="_blank" rel="noreferrer noopener">${escapeHtml(t("rsearch.on_site"))}</a>` : ""}
      </p>
      <div id="${prefix}-import-result"></div>
      <h3>${escapeHtml(t("rsearch.ingredients"))}</h3>
      ${ingredients ? `<ul class="picnic-recipe__list">${ingredients}</ul>` : `<p class="muted">${escapeHtml(t("rsearch.no_ingredients"))}</p>`}
      <h3>${escapeHtml(t("rsearch.steps"))}</h3>
      ${steps ? `<ol class="picnic-recipe__list">${steps}</ol>` : `<p class="muted">${escapeHtml(t("rsearch.no_steps"))}</p>`}
      ${recipe.stepsPortions && recipe.stepsPortions !== recipe.portions ? `<p class="muted">${escapeHtml(t("picnic.recipes.steps_for", { count: recipe.stepsPortions }))}</p>` : ""}
      ${recipe.tip ? `<h3>${escapeHtml(t("rsearch.tip"))}</h3><p>${escapeHtml(recipe.tip)}</p>` : ""}
      ${nutrition ? `<p class="muted">${escapeHtml(t("rsearch.nutrition", { values: nutrition }))}</p>` : ""}
    </div>`;
  $(`#${prefix}-import`).addEventListener("click", (event) => importStoreRecipe(store, recipe.id, event.currentTarget, $(`#${prefix}-import-result`)));
}

$("#search-form").addEventListener("submit", runRecipeSearch);
$("#search-detail-back").addEventListener("click", () => showRecipeList(SEARCH_HOST));
$("#search-ah-more").addEventListener("click", async (event) => {
  setBusy(event.currentTarget, true);
  await fetchSearchStore("ah", true);
  setBusy(event.currentTarget, false);
});
$("#search-jumbo-more").addEventListener("click", async (event) => {
  setBusy(event.currentTarget, true);
  await fetchSearchStore("jumbo", true);
  setBusy(event.currentTarget, false);
});
$("#search-sections").addEventListener("click", (event) => {
  const open = event.target.closest("[data-store-open]");
  const importButton = event.target.closest("[data-store-import]");
  if (open) openStoreRecipe(...open.dataset.storeOpen.split(":", 2));
  if (importButton) {
    const [store, id] = importButton.dataset.storeImport.split(":", 2);
    importStoreRecipe(store, id, importButton, importButton.closest(".recipe-card").querySelector(".recipe-import-result"));
  }
});

window.addEventListener("ms:languagechange", () => {
  if (recipeSearch.searched) SEARCH_STORES.forEach(renderSearchStore);
});
