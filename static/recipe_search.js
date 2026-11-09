// Recepten zoeken: in je eigen recepten in Mealie (zodra Mealie gekoppeld is), tegelijk bij Albert Heijn, Jumbo, Picnic en Crisp (de
// laatste drie zodra ze gekoppeld zijn) en in de menu's van de maaltijdboxen HelloFresh en Marley Spoon (zonder koppeling; de komende weken
// worden bij het zoeken opgehaald). Een recept uit Mealie importeer je niet, dat plan je meteen in het weekmenu.
// Elke winkel heeft zijn eigen blok met resultaten; een winkel die faalt, laat de andere gewoon staan.
// Elke kaart heeft Bekijken (het recept met ingrediënten en bereiding, in dit tabblad) en Inplannen (zie recipeCardActions in
// app.js). Importeren zonder plannen en de link naar de winkel of Mealie staan in het detail. Hetzelfde detail gebruiken
// Winkels (AH, Jumbo, Picnic) en Maaltijdboxen, elk met een eigen "gastheer" (zie hieronder).
// Laadt na app.js en gebruikt daar de hulpfuncties ($, message, jsonFetch, postJson, setBusy, t, tn, ...).

const SEARCH_STORES = ["mealie", "ah", "jumbo", "picnic", "crisp", "hellofresh", "marleyspoon"];
const MEALKIT_SEARCH = new Set(["hellofresh", "marleyspoon"]); // zoeken in menu's; elke aanbieder heeft eigen routes onder /api/<aanbieder>/
const MEALKIT_SEARCH_WEEKS = 4; // deze week en de 3 weken erna
const recipeSearch = {
  query: "",
  kcal: "", // de grenzen op de calorieën (kcalMin=..&kcalMax=..), of leeg; met grenzen zoekt alleen Mealie (issue #283)
  retry: 0,
  mealie: { recipes: [], total: 0, page: 1, more: false },
  ah: { recipes: [], total: 0, page: 0, more: false },
  jumbo: { recipes: [], total: 0, more: false },
  picnic: { recipes: [], total: 0, more: false },
  crisp: { recipes: [], total: 0, more: false },
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

// De regel onder de naam op een kaart, als HTML: tijd, personen, waardering en labels, met de kcal per persoon erachter.
function storeRecipeMeta(recipe) {
  return factsWithKcal(
    [
      recipe.minutes ? t("rsearch.minutes", { count: recipe.minutes }) : "",
      recipe.portions ? t("rsearch.portions", { count: recipe.portions }) : "",
      recipe.rating ? `★ ${I18N.formatNumber(recipe.rating)}` : "",
      ...(recipe.tags || []),
    ],
    [["kcal.per_serving", recipeKcal(recipe)]],
  );
}

// Inplannen: een recept uit Mealie direct, een recept van een winkel of maaltijdbox eerst in Mealie (of hergebruikt).
function recipePlanAttrs(store, recipe) {
  return store === "mealie" ? planButtonAttrs(recipe) : planSourceAttrs(store, recipe.id, recipe.name, recipe.image, recipe);
}

function renderStoreRecipeCard(store, recipe) {
  const meta = storeRecipeMeta(recipe);
  return `
    <article class="recipe-card" data-store-recipe="${store}:${escapeHtml(recipe.id)}">
      ${recipe.image ? `<img src="${escapeHtml(storeImage(recipe.image))}" alt="" loading="lazy" data-hide-on-error>` : ""}
      ${recipeCardTitle(recipe.name, `${store}:${recipe.id}`)}
      ${meta ? `<p class="muted">${meta}</p>` : ""}
      ${recipeCardActions(recipePlanAttrs(store, recipe), recipe.name)}
    </article>`;
}

function renderSearchStore(store) {
  const data = recipeSearch[store];
  const parts = searchParts(store);
  parts.section.hidden = !searchStoreActive(store) || !recipeSearch.searched || Boolean(recipeSearch.kcal && store !== "mealie");
  parts.results.innerHTML = data.recipes.map((recipe) => (store === "ah" ? renderRecipeCard(recipe) : renderStoreRecipeCard(store, recipe))).join("");
  if (store === "ah") {
    parts.count.textContent = data.recipes.length ? tn("search.count", data.total, { limit: data.total > 2000 ? t("search.limit") : "" }) : "";
  } else {
    parts.count.textContent = data.recipes.length ? (data.more ? t("rsearch.count", { shown: I18N.formatNumber(data.recipes.length), total: I18N.formatNumber(data.total) }) : tn("search.count", data.total, { limit: "" })) : "";
  }
  if (parts.more) parts.more.hidden = !data.more; // Picnic, Crisp en de maaltijdboxen hebben maar één pagina
  if (recipeSearch.searched && !data.recipes.length && !parts.message.textContent) {
    message(`#${parts.message.id}`, recipeSearch.kcal ? t("kcal.none") : t("rsearch.none", { query: recipeSearch.query }), "success");
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
  const kcal = recipeSearch.kcal;
  const q = encodeURIComponent(query);
  const parts = searchParts(store);
  message(`#${parts.message.id}`, "");
  let note = "";
  try {
    let found;
    if (store === "mealie") {
      const page = append ? data.page + 1 : 1;
      const answer = await jsonFetch(`/api/mealie/recipes?q=${q}&page=${page}${kcal ? `&${kcal}` : ""}`);
      found = { recipes: answer.recipes || [], total: Number(answer.total) || 0, page, more: Boolean(answer.more) };
      // Met een filter op calorieën: leest de server de recepten nog in, dan zoeken we over twee seconden opnieuw.
      if (kcal) {
        note = kcalSearchNote(answer);
        clearTimeout(recipeSearch.retry);
        if (answer.indexing && !append) {
          recipeSearch.retry = setTimeout(() => {
            if (recipeSearch.query === query && recipeSearch.kcal === kcal) fetchSearchStore("mealie", false);
          }, 2000);
        }
      }
    } else if (store === "ah") {
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
      // Picnic en Crisp: één pagina met de treffers.
      const answer = await jsonFetch(`/api/${store}/recipes?q=${q}`);
      found = { recipes: answer.recipes || [], total: answer.total ?? (answer.recipes || []).length, more: false };
    }
    if (query !== recipeSearch.query || kcal !== recipeSearch.kcal) return; // intussen is er opnieuw gezocht
    if (note) message(`#${parts.message.id}`, note, "success");
    const known = new Set(append ? data.recipes.map((recipe) => String(recipe.id)) : []);
    const fresh = found.recipes.filter((recipe) => !known.has(String(recipe.id)));
    data.recipes = append ? [...data.recipes, ...fresh] : fresh;
    data.total = found.total;
    data.page = found.page ?? 0;
    // Bij Mealie en AH geldt "Meer recepten" alleen als de volgende pagina ook echt iets nieuws bracht.
    // Jumbo geeft per pagina 24 recepten; een pagina zonder nieuwe recepten of het totaal bereikt: geen "Meer recepten".
    data.more = store === "jumbo" ? Boolean(fresh.length) && data.recipes.length < data.total : found.more && (!["ah", "mealie"].includes(store) || Boolean(fresh.length));
  } catch (error) {
    if (query !== recipeSearch.query || kcal !== recipeSearch.kcal) return;
    if (!append) data.recipes = [];
    data.more = false;
    message(`#${parts.message.id}`, error.message);
  }
  renderSearchStore(store);
}

async function runRecipeSearch(event) {
  event.preventDefault();
  const query = $("#recipe-query").value.trim();
  // Met een grens op de calorieën mag de zoekterm leeg zijn; dan zoekt alleen Mealie (de winkels geven geen kcal in hun lijst).
  const kcal = kcalQuery($("#search-kcal-min"), $("#search-kcal-max"));
  if (!query && !kcal) return;
  recipeSearch.query = query;
  recipeSearch.kcal = kcal;
  recipeSearch.searched = true;
  clearTimeout(recipeSearch.retry);
  message("#search-message", "");
  const active = SEARCH_STORES.filter((store) => searchStoreActive(store) && (!kcal || store === "mealie"));
  if (kcal && !active.length) message("#search-message", t("kcal.needs_mealie"));
  SEARCH_STORES.forEach((store) => {
    Object.assign(recipeSearch[store], { recipes: [], total: 0, page: store === "mealie" ? 1 : 0, more: false });
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

// ---- Een recept van Jumbo, Picnic of Crisp openen: ingrediënten en bereiding (de kookweergave, recipe_view.js), en importeren in Mealie
async function importStoreRecipe(store, id, button, target) {
  button.disabled = true;
  button.textContent = t("recipe.importing");
  try {
    const data = await postJson(`/api/${store}/recipes/${encodeURIComponent(id)}/import`);
    target.innerHTML = `<div class="message success">${importedMessage(data)}</div>`;
  } catch (error) {
    target.innerHTML = `<div class="message error">${escapeHtml(error.message)}</div>`;
  } finally {
    button.disabled = false;
    button.textContent = t("recipe.import");
  }
}

// Het detailscherm hoort bij een "gastheer": de lijst waar het recept vandaan kwam en het blok dat het detail toont.
// De elementen heten `#<voorvoegsel>-title`, `-body`, `-message`, `-import` en `-import-result`. `known` geeft de recepten
// van de lijst, zodat de titel er al staat terwijl het recept laadt.
const SEARCH_HOST = { prefix: "search-detail", list: "#search-main", detail: "#search-detail", known: (store) => recipeSearch[store].recipes };
const FAVORITES_HOST = { prefix: "jumbo-recipe", list: "#jumbo-favorites-view", detail: "#jumbo-recipe-view", known: () => jumboFavorites.recipes };
const PICNIC_HOST = { prefix: "picnic-recipe", list: "#picnic-recipes-view", detail: "#picnic-recipe-view", known: () => picnicRecipes.recipes };
const CRISP_HOST = { prefix: "crisp-recipe", list: "#crisp-recipes-view", detail: "#crisp-recipe-view", known: () => crispRecipes.recipes };
const AH_SAVED_HOST = { prefix: "ah-recipe", list: "#ah-recipes-view", detail: "#ah-recipe-view", known: () => state.ahRecipes };
const MEALKIT_HOST = { prefix: "mealkit-recipe", list: "#mealkits-view", detail: "#mealkit-recipe-view", known: () => mealKits.search?.recipes || mealKits.menu?.recipes || [] };

// De naam van de winkel bij de link in het detail ("Bekijk bij Albert Heijn").
const RECIPE_SOURCE_NAMES = { ah: "Albert Heijn", jumbo: "Jumbo", picnic: "Picnic", crisp: "Crisp", hellofresh: "HelloFresh", marleyspoon: "Marley Spoon" };

function showRecipeList(host) {
  $(host.detail).hidden = true;
  $(host.list).hidden = false;
  cookWakeUpdate(); // het recept is uit beeld: "Scherm aan laten" hoeft niet meer (recipe_view.js)
}

async function openStoreRecipe(store, id, host = SEARCH_HOST) {
  $(host.list).hidden = true;
  $(host.detail).hidden = false;
  const known = host.known(store).find((recipe) => String(recipe.id ?? recipe.key) === String(id));
  $(`#${host.prefix}-title`).textContent = known?.name || known?.title || "";
  $(`#${host.prefix}-body`).innerHTML = `<p class="muted">${escapeHtml(t("basket.loading"))}</p>`;
  message(`#${host.prefix}-message`, "");
  $(`#${host.prefix}-title`).focus();
  try {
    renderStoreRecipeDetail(store, await jsonFetch(`/api/${store}/recipes/${encodeURIComponent(id)}`), host);
  } catch (error) {
    $(`#${host.prefix}-body`).innerHTML = "";
    message(`#${host.prefix}-message`, error.message);
  } finally {
    cookWakeUpdate();
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
  ];
  const factsHtml = factsWithKcal(facts, [["kcal.per_serving", recipeKcal(recipe)]]);
  const fromMealie = store === "mealie";
  // Inplannen, Importeer (een recept uit Mealie staat er al) en de link naar de bron: Mealie of de winkel.
  const source = fromMealie ? t("rsearch.in_mealie") : t("rsearch.on_store", { store: RECIPE_SOURCE_NAMES[store] || store });
  const actions = [
    `<button class="button" type="button" ${recipePlanAttrs(store, recipe)} data-plan-target="${prefix}-import-result">${escapeHtml(t("plan.schedule"))}</button>`,
    fromMealie ? "" : `<button class="button button--secondary" type="button" id="${prefix}-import">${escapeHtml(t("recipe.import"))}</button>`,
    recipe.url ? `<a class="button button--secondary" href="${escapeHtml(recipe.url)}" target="_blank" rel="noreferrer noopener">${mdiIcon(MDI_OPEN_IN_NEW)}<span>${escapeHtml(source)}</span></a>` : "",
  ].join("");
  // De ingrediënten en de bereiding in de kookweergave (renderCookView in recipe_view.js): afvinken, de stap waar je bent,
  // de kookmodus en "Scherm aan laten".
  const cookView = renderCookView(cookRecipeFromStore(store, recipe), {
    headingLevel: 3,
    noIngredients: t(fromMealie ? "rsearch.no_ingredients_mealie" : "rsearch.no_ingredients"),
    noSteps: t(fromMealie ? "rsearch.no_steps_mealie" : "rsearch.no_steps"),
    stepsNote: recipe.stepsPortions && recipe.stepsPortions !== recipe.portions ? t("picnic.recipes.steps_for", { count: recipe.stepsPortions }) : "",
  });
  $(`#${prefix}-body`).innerHTML = `
    <div class="picnic-recipe">
      ${recipe.image ? `<img class="picnic-recipe__image" src="${escapeHtml(storeImage(recipe.image))}" alt="" data-hide-on-error>` : ""}
      ${factsHtml ? `<p class="muted">${factsHtml}</p>` : ""}
      ${recipe.description ? `<p>${escapeHtml(recipe.description)}</p>` : ""}
      <div class="recipe-detail__actions">${actions}</div>
      <div id="${prefix}-import-result"></div>
      ${cookView}
      ${recipe.tip ? `<h3>${escapeHtml(t("rsearch.tip"))}</h3><p>${escapeHtml(recipe.tip)}</p>` : ""}
      ${nutritionLine(recipe.nutrition)}
    </div>`;
  if (!fromMealie) $(`#${prefix}-import`).addEventListener("click", (event) => importStoreRecipe(store, recipe.id, event.currentTarget, $(`#${prefix}-import-result`)));
}

$("#search-form").addEventListener("submit", runRecipeSearch);
$("#search-detail-back").addEventListener("click", () => showRecipeList(SEARCH_HOST));
$("#ah-recipe-back").addEventListener("click", () => showRecipeList(AH_SAVED_HOST));
$("#picnic-recipe-back").addEventListener("click", () => showRecipeList(PICNIC_HOST));
$("#mealkit-recipe-back").addEventListener("click", () => showRecipeList(MEALKIT_HOST));
$("#search-mealie-more").addEventListener("click", async (event) => {
  setBusy(event.currentTarget, true);
  await fetchSearchStore("mealie", true);
  setBusy(event.currentTarget, false);
});
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
  if (open) openStoreRecipe(...open.dataset.storeOpen.split(":", 2));
});

window.addEventListener("ms:languagechange", () => {
  if (recipeSearch.searched) SEARCH_STORES.forEach(renderSearchStore);
});
