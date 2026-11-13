// Recepten zoeken: in je eigen recepten in Mealie (zodra Mealie gekoppeld is), tegelijk bij Albert Heijn, Jumbo, Picnic en Crisp (de
// laatste drie zodra ze gekoppeld zijn) en in de menu's van de maaltijdboxen HelloFresh en Marley Spoon (zonder koppeling; de komende weken
// worden bij het zoeken opgehaald). Een recept uit Mealie importeer je niet, dat plan je meteen in het weekmenu.
// Elke winkel heeft zijn eigen blok met resultaten; een winkel die faalt, laat de andere gewoon staan.
// Elke kaart heeft Bekijken (het recept met ingrediënten en bereiding, in dit tabblad) en Inplannen (zie recipeCardActions in
// app.js). Importeren zonder plannen en de link naar de winkel of Mealie staan in het detail. Hetzelfde detail gebruiken
// Winkels (AH, Jumbo, Picnic) en Maaltijdboxen, elk met een eigen "gastheer" (zie hieronder).
// Met een leeg zoekveld blader je door je recepten in Mealie, met filters op tags, categorieën en bereidingstijd (issue #322,
// onderaan dit bestand).
// Laadt na app.js en facets.js en gebruikt daar de hulpfuncties ($, message, jsonFetch, postJson, setBusy, t, tn, facetRows, ...).

const SEARCH_STORES = ["mealie", "ah", "jumbo", "picnic", "crisp", "hellofresh", "marleyspoon"];
const MEALKIT_SEARCH = new Set(["hellofresh", "marleyspoon"]); // zoeken in menu's; elke aanbieder heeft eigen routes onder /api/<aanbieder>/
const MEALKIT_SEARCH_WEEKS = 4; // deze week en de 3 weken erna
const recipeSearch = {
  query: "",
  kcal: "", // de grenzen op de calorieën (kcalMin=..&kcalMax=..), of leeg; met grenzen zoekt alleen Mealie (issue #283)
  filters: "", // tags, categorieën, tijd en volgorde (tags=..&maxMinutes=..&sort=..), of leeg (issue #322)
  mealieOnly: false, // zonder zoekterm (bladeren), of met een filter dat alleen Mealie kent: dan zoeken de winkels niet mee
  browsing: false, // zonder zoekterm: je recepten in Mealie, met doorlopend scrollen
  loadingMore: false,
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
  const meta = factsWithKcal(
    [
      recipe.minutes ? t("rsearch.minutes", { count: recipe.minutes }) : "",
      recipe.portions ? t("rsearch.portions", { count: recipe.portions }) : "",
      recipe.rating ? `★ ${I18N.formatNumber(recipe.rating)}` : "",
      ...(recipe.tags || []),
    ],
    [["kcal.per_serving", recipeKcal(recipe)]],
  );
  return meta + nutritionBrief(recipe.nutrition, Boolean(meta)); // eiwit, suiker en zout, als de kaart ze heeft
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
  parts.section.hidden = !searchStoreActive(store) || !recipeSearch.searched || (recipeSearch.mealieOnly && store !== "mealie");
  if (store === "mealie") $("#search-mealie-title").textContent = recipeSearch.browsing ? t("rsearch.mealie_all") : "Mealie";
  parts.results.innerHTML = data.recipes.map((recipe) => (store === "ah" ? renderRecipeCard(recipe) : renderStoreRecipeCard(store, recipe))).join("");
  if (store === "ah") {
    parts.count.textContent = data.recipes.length ? tn("search.count", data.total, { limit: data.total > 2000 ? t("search.limit") : "" }) : "";
  } else {
    parts.count.textContent = data.recipes.length ? (data.more ? t("rsearch.count", { shown: I18N.formatNumber(data.recipes.length), total: I18N.formatNumber(data.total) }) : tn("search.count", data.total, { limit: "" })) : "";
  }
  if (parts.more) parts.more.hidden = !data.more; // Picnic, Crisp en de maaltijdboxen hebben maar één pagina
  if (recipeSearch.searched && !data.recipes.length && !parts.message.textContent) {
    message(`#${parts.message.id}`, searchNoneText(store), "success");
  }
}

// De melding bij een lege uitkomst: met filters, zonder zoekterm (een leeg Mealie) of bij een zoekterm.
function searchNoneText(store) {
  if (recipeSearch.kcal) return t("kcal.none");
  if (store === "mealie" && recipeSearch.filters && recipeFiltersNarrow()) return t("rsearch.none_filtered");
  if (recipeSearch.browsing) return t("rsearch.none_mealie");
  return t("rsearch.none", { query: recipeSearch.query });
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
  const filters = recipeSearch.filters;
  const stale = () => query !== recipeSearch.query || kcal !== recipeSearch.kcal || filters !== recipeSearch.filters; // intussen opnieuw gezocht
  const q = encodeURIComponent(query);
  const parts = searchParts(store);
  message(`#${parts.message.id}`, "");
  let note = "";
  try {
    let found;
    if (store === "mealie") {
      const page = append ? data.page + 1 : 1;
      const answer = await jsonFetch(`/api/mealie/recipes?q=${q}&page=${page}${kcal ? `&${kcal}` : ""}${filters ? `&${filters}` : ""}`);
      found = { recipes: answer.recipes || [], total: Number(answer.total) || 0, page, more: Boolean(answer.more) };
      // Met een filter op voedingswaarden, tags, categorieën, tijd of volgorde zoekt de server in zijn receptencache: leest hij de
      // recepten nog in, dan zoeken we over twee seconden opnieuw.
      if (kcal || filters) {
        note = [kcalSearchNote(answer), answer.indexing || !answer.withoutTime ? "" : tn("rsearch.without_time", answer.withoutTime)].filter(Boolean).join(" ");
        clearTimeout(recipeSearch.retry);
        if (answer.indexing && !append) {
          recipeSearch.retry = setTimeout(() => {
            if (!stale()) fetchSearchStore("mealie", false);
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
    if (stale()) return;
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
    if (stale()) return;
    if (!append) data.recipes = [];
    data.more = false;
    message(`#${parts.message.id}`, error.message);
  }
  renderSearchStore(store);
}

// Een link in het zoekveld (sinds 4.10.4): die zoek je niet, die importeer je. Zo heeft *Recept importeren via URL* weer een
// ingang zonder een extra item in het menu; de pagina op /import blijft bestaan.
function recipeQueryIsUrl(query) {
  return /^https?:\/\/\S+$/i.test(query);
}

async function importFromSearch(url) {
  const button = $("#search-form button[type='submit']");
  setBusy(button, true);
  message("#search-message", t("recipe.importing"), "info");
  const result = await importUrl(url);
  $("#search-message").innerHTML = `<div class="message ${result.ok ? "success" : "error"}" role="${result.ok ? "status" : "alert"}">${result.html}</div>`;
  if (result.ok) $("#recipe-query").value = "";
  setBusy(button, false);
}

async function runRecipeSearch(event) {
  event?.preventDefault();
  const query = $("#recipe-query").value.trim();
  if (recipeQueryIsUrl(query)) {
    await importFromSearch(query);
    return;
  }
  // Zonder zoekterm blader je door je recepten in Mealie (issue #322). Met een grens op de voedingswaarden of een filter op
  // tags, categorieën of tijd zoekt alleen Mealie: de winkels kennen die niet in hun zoekresultaten.
  const kcal = nutritionQuery("search");
  const filters = recipeFilterQuery();
  const mealieOnly = !query || Boolean(kcal) || recipeFiltersNarrow();
  if (!query && !kcal && !searchStoreActive("mealie")) {
    recipeSearch.searched = false;
    recipeSearch.browsing = false;
    renderBrowseHint();
    return;
  }
  recipeSearch.query = query;
  recipeSearch.kcal = kcal;
  recipeSearch.filters = filters;
  recipeSearch.mealieOnly = mealieOnly;
  recipeSearch.browsing = !query;
  recipeSearch.searched = true;
  clearTimeout(recipeSearch.retry);
  message("#search-message", "");
  renderBrowseHint();
  const active = SEARCH_STORES.filter((store) => searchStoreActive(store) && (!mealieOnly || store === "mealie"));
  if (kcal && !active.length) message("#search-message", t("kcal.needs_mealie"));
  else if (query && active.length && recipeFiltersNarrow()) message("#search-message", t("rsearch.filters_mealie_only"), "info");
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
// Bladeren (recipe_nav.js) volgt de volgorde van de kaarten in `list`. Staat er na het laatste recept nog een pagina, dan laadt `loadMore`
// die (`more` zegt of het kan, `total` het aantal van de hele lijst voor "3 van 187"). Bij Recepten zoeken kan dat zonder zoekterm
// of met een filter dat alleen Mealie kent, want dan staat er alleen Mealie op het scherm en komt de volgende pagina onderaan.
const SEARCH_HOST = {
  prefix: "search-detail",
  list: "#search-main",
  detail: "#search-detail",
  known: (store) => recipeSearch[store].recipes,
  more: () => recipeSearch.mealieOnly && recipeSearch.mealie.more,
  loadMore: loadMoreMealieForNav,
  total: () => (recipeSearch.mealieOnly ? recipeSearch.mealie.total : 0),
};
const FAVORITES_HOST = {
  prefix: "jumbo-recipe",
  list: "#jumbo-favorites-view",
  detail: "#jumbo-recipe-view",
  known: () => jumboFavorites.recipes,
  more: () => jumboFavorites.hasNext,
  loadMore: () => loadJumboFavorites(true),
  total: () => jumboFavorites.total,
};
const PICNIC_HOST = { prefix: "picnic-recipe", list: "#picnic-recipes-view", detail: "#picnic-recipe-view", known: () => picnicRecipes.recipes };
const CRISP_HOST = { prefix: "crisp-recipe", list: "#crisp-recipes-view", detail: "#crisp-recipe-view", known: () => crispRecipes.recipes };
const AH_SAVED_HOST = { prefix: "ah-recipe", list: "#ah-recipes-view", detail: "#ah-recipe-view", known: () => state.ahRecipes };
const MEALKIT_HOST = { prefix: "mealkit-recipe", list: "#mealkits-view", detail: "#mealkit-recipe-view", known: () => mealKits.search?.recipes || mealKits.menu?.recipes || [] };

// De naam van de winkel bij de link in het detail ("Bekijk bij Albert Heijn").
const RECIPE_SOURCE_NAMES = { ah: "Albert Heijn", jumbo: "Jumbo", picnic: "Picnic", crisp: "Crisp", hellofresh: "HelloFresh", marleyspoon: "Marley Spoon" };

function showRecipeList(host) {
  $(host.detail).hidden = true;
  $(host.list).hidden = false;
  recipeNavEnd(host); // geen recept meer open: er wordt niet meer in bladerd (recipe_nav.js)
  cookWakeUpdate(); // het recept is uit beeld: "Scherm aan laten" hoeft niet meer (recipe_view.js)
}

// Een recept uit de lijst van `host`. Vanuit de lijst begint de reeks om doorheen te bladeren (recipe_nav.js); met `move` is dit een
// stap naar het vorige of volgende recept, binnen dezelfde reeks. Bij een snelle reeks stappen telt alleen het laatste antwoord.
async function openStoreRecipe(store, id, host = SEARCH_HOST, { move = false } = {}) {
  const token = (host.seq = (host.seq || 0) + 1);
  if (!move) recipeNavBegin(host, store, id);
  $(host.list).hidden = true;
  $(host.detail).hidden = false;
  const known = host.known(store).find((recipe) => String(recipe.id ?? recipe.key) === String(id));
  $(`#${host.prefix}-title`).textContent = known?.name || known?.title || "";
  $(`#${host.prefix}-body`).innerHTML = loadingHtml();
  message(`#${host.prefix}-message`, "");
  if (!move) $(`#${host.prefix}-title`).focus(); // bij een stap blijft de focus op de chevron
  try {
    const recipe = await jsonFetch(`/api/${store}/recipes/${encodeURIComponent(id)}`);
    if (token !== host.seq) return;
    renderStoreRecipeDetail(store, recipe, host);
  } catch (error) {
    if (token !== host.seq) return;
    $(`#${host.prefix}-body`).innerHTML = "";
    message(`#${host.prefix}-message`, error.message);
  } finally {
    cookWakeUpdate();
  }
  if (move) recipeNavArrived($(host.detail));
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
      ${nutritionTable(recipe.nutrition, { headingLevel: 3 })}
    </div>`;
  if (!fromMealie) $(`#${prefix}-import`).addEventListener("click", (event) => importStoreRecipe(store, recipe.id, event.currentTarget, $(`#${prefix}-import-result`)));
}

$("#search-form").addEventListener("submit", runRecipeSearch);
$("#search-detail-back").addEventListener("click", () => showRecipeList(SEARCH_HOST));
$("#ah-recipe-back").addEventListener("click", () => showRecipeList(AH_SAVED_HOST));
$("#picnic-recipe-back").addEventListener("click", () => showRecipeList(PICNIC_HOST));
$("#mealkit-recipe-back").addEventListener("click", () => showRecipeList(MEALKIT_HOST));
$("#search-mealie-more").addEventListener("click", (event) => loadMoreMealie(event.currentTarget));
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
  renderRecipeFacets();
});

// ---- Bladeren en filteren in je recepten in Mealie (issue #322) ------------------------------------------------------------
// Met een leeg zoekveld staan je recepten in Mealie er meteen, en laadt de volgende pagina vanzelf als je naar onderen scrolt.
// De filters: tags en categorieën met plus (moet erbij: alle gekozen) en min (verbergen), met het aantal recepten (de lijsten van
// facets.js), de bereidingstijd en de volgorde. De tijden, tags en categorieën komen uit de receptencache van de server
// (recipe_nutrition.py), die de dagelijkse run bijwerkt.

const RECIPE_FACETS = ["tags", "categories"];
const RECIPE_FACETS_EMPTY = { tags: "rsearch.no_tags", categories: "rsearch.no_categories" };
const RECIPE_FACETS_TTL = 10 * 60 * 1000; // zo lang gelden de lijsten; daarna haalt het openen van het scherm ze opnieuw op
const recipeFacets = { tags: facetSelection(), categories: facetSelection(), data: null, loadedAt: 0, loading: false, retry: 0 };

// De filters voor de zoekroute: `tags=a,b&tagsOut=c&categories=..&maxMinutes=30&sort=newest`, of "" zonder filter.
function recipeFilterQuery() {
  const params = [];
  const list = (values) => values.map(encodeURIComponent).join(",");
  for (const name of RECIPE_FACETS) {
    const selection = recipeFacets[name];
    if (selection.shown.length) params.push(`${name}=${list(selection.shown)}`);
    if (selection.hidden.length) params.push(`${name}Out=${list(selection.hidden)}`);
  }
  const minutes = $("#search-max-minutes").value;
  if (minutes) params.push(`maxMinutes=${encodeURIComponent(minutes)}`);
  const sort = $("#search-sort").value;
  if (sort) params.push(`sort=${encodeURIComponent(sort)}`);
  return params.join("&");
}

// Een filter dat alleen Mealie kent (tags, categorieën, tijd): dan zoeken de winkels niet mee. Alleen een volgorde mag wel.
function recipeFiltersNarrow() {
  return RECIPE_FACETS.some((name) => facetActive(recipeFacets[name])) || Boolean($("#search-max-minutes").value);
}

function recipeFilterCount() {
  return RECIPE_FACETS.reduce((sum, name) => sum + facetActive(recipeFacets[name]), 0) + ($("#search-max-minutes").value ? 1 : 0);
}

// Zonder zoekterm de uitleg dat zoeken ook bij de winkels zoekt; met een zoekterm de gewone uitleg.
function renderBrowseHint() {
  const browsing = recipeSearch.browsing && recipeSearch.searched;
  $("#search-intro").hidden = browsing;
  $("#search-browse-hint").hidden = !browsing;
}

function renderRecipeFacets(click) {
  const panel = $("#search-facets");
  panel.hidden = !searchStoreActive("mealie");
  const data = recipeFacets.data;
  for (const name of RECIPE_FACETS) {
    const list = $(`#search-${name}`);
    const items = data?.[name] || [];
    const labels = new Map(items.map((item) => [item.value, item.label]));
    const rows = facetRows(items, recipeFacets[name], { label: (value) => labels.get(value) || value, search: $(`#search-${name}-search`).value });
    list.innerHTML = facetListHtml(name, rows, {
      empty: data ? RECIPE_FACETS_EMPTY[name] : "rsearch.facets_loading",
      include: "rsearch.filter_include",
      exclude: "rsearch.filter_exclude",
    });
    if (click && click.name === name) facetRefocus(list, click);
  }
  facetPanelCount(panel, recipeFilterCount());
}

async function loadRecipeFacets() {
  if (!searchStoreActive("mealie") || recipeFacets.loading) return;
  recipeFacets.loading = true;
  clearTimeout(recipeFacets.retry);
  message("#search-facets-message", "");
  try {
    const answer = await jsonFetch("/api/mealie/recipes/facets");
    recipeFacets.data = answer;
    recipeFacets.loadedAt = Date.now();
    // Leest de server de recepten nog in, dan over een paar seconden opnieuw: de lijsten vullen zich aan.
    if (answer.indexing) recipeFacets.retry = setTimeout(loadRecipeFacets, 3000);
  } catch (error) {
    message("#search-facets-message", error.message);
  } finally {
    recipeFacets.loading = false;
  }
  renderRecipeFacets();
}

// Na een andere keuze opnieuw zoeken, met wat er in het zoekveld staat (een link importeert alleen de knop Zoeken).
function rerunRecipeSearch() {
  if (recipeQueryIsUrl($("#recipe-query").value.trim())) return;
  runRecipeSearch();
}

function recipeFacetClick(event) {
  const click = facetClick(event);
  if (!click || !RECIPE_FACETS.includes(click.name)) return;
  recipeFacets[click.name] = facetToggled(recipeFacets[click.name], click.value, click.mode);
  renderRecipeFacets(click);
  rerunRecipeSearch();
}

// Het scherm Recepten zoeken gaat open (app.js): de lijsten met tags en categorieën, en met een leeg zoekveld meteen je recepten.
function showRecipeSearch() {
  renderRecipeFacets();
  if (!searchStoreActive("mealie")) return;
  if (!recipeFacets.data || Date.now() - recipeFacets.loadedAt > RECIPE_FACETS_TTL) loadRecipeFacets();
  if (!recipeSearch.searched && !$("#recipe-query").value.trim()) runRecipeSearch();
}

// Mealie is (ont)koppeld of de status kwam binnen (app.js): staat Recepten zoeken open, dan meteen bijwerken.
function recipeSearchMealieChanged() {
  if ($("#search")?.classList.contains("is-active")) showRecipeSearch();
  else renderRecipeFacets();
}

// Doorlopend scrollen: komt de knop Meer recepten in beeld, dan laadt de volgende pagina vanzelf. Staat de knop daarna nog in
// beeld (een korte pagina), dan meteen de volgende; de knop zelf blijft werken zonder IntersectionObserver.
async function loadMoreMealie(button) {
  if (recipeSearch.loadingMore || button.hidden) return;
  recipeSearch.loadingMore = true;
  setBusy(button, true);
  try {
    await fetchSearchStore("mealie", true);
  } finally {
    setBusy(button, false);
    recipeSearch.loadingMore = false;
  }
  // Een recept dat intussen open ging verbergt de lijst; een verborgen knop meet 0 en zou anders pagina na pagina laden.
  if (recipeSearch.browsing && !button.hidden && button.getClientRects().length && button.getBoundingClientRect().top < window.innerHeight + 400) loadMoreMealie(button);
}

// Bladeren in een recept (recipe_nav.js) was bij het laatste recept van wat er geladen is: de volgende pagina erbij. De knop Meer
// recepten en het doorlopend scrollen laden niet tegelijk (loadingMore); dan is die pagina er zo, en bladert het door.
async function loadMoreMealieForNav() {
  if (recipeSearch.loadingMore) return;
  recipeSearch.loadingMore = true;
  try {
    await fetchSearchStore("mealie", true);
  } finally {
    recipeSearch.loadingMore = false;
  }
}

if (typeof IntersectionObserver === "function") {
  new IntersectionObserver(
    (entries) => {
      if (recipeSearch.browsing && entries.some((entry) => entry.isIntersecting)) loadMoreMealie($("#search-mealie-more"));
    },
    { rootMargin: "0px 0px 400px 0px" },
  ).observe($("#search-mealie-more"));
}

RECIPE_FACETS.forEach((name) => {
  $(`#search-${name}`).addEventListener("click", recipeFacetClick);
  $(`#search-${name}-search`).addEventListener("input", () => renderRecipeFacets());
});
$("#search-max-minutes").addEventListener("change", () => {
  renderRecipeFacets();
  rerunRecipeSearch();
});
$("#search-sort").addEventListener("change", rerunRecipeSearch);
$("#search-facets [data-facet-clear]").addEventListener("click", () => {
  RECIPE_FACETS.forEach((name) => {
    recipeFacets[name] = facetSelection();
  });
  $("#search-max-minutes").value = "";
  renderRecipeFacets();
  rerunRecipeSearch();
});
// Het zoekveld leeggemaakt (ook met het kruisje): terug naar je recepten in Mealie.
$("#recipe-query").addEventListener("input", (event) => {
  if (!event.currentTarget.value.trim() && recipeSearch.searched && recipeSearch.query) runRecipeSearch();
});
