// Winkels > Albert Heijn > Weekplanner: wat in de weekplanner van de AH-app staat (alleen lezen, plannen doe je in de AH-app),
// en *Naar weekmenu*, dat het per dag in het weekmenu van Mealie zet: de Allerhande-recepten (zo nodig eerst geïmporteerd)
// en de losse producten, als het recept met die naam in Mealie, als eigen recept of samen als menu (4.3.0). Eigen AH-recepten
// gaan niet mee, een notitie alleen als naam van een menu. Zie mealieshopper/ah_planner.py.
// Laadt na app.js en recipe_search.js en gebruikt daar de hulpfuncties van ($, message, jsonFetch, setBusy, t, tn, ...).

const AH_PLANNER_HOST = { prefix: "ah-planner-recipe", list: "#ah-planner-view", detail: "#ah-planner-recipe-view", known: () => ahPlannerRecipes() };

const ahPlanner = { data: null, loaded: false, run: 0, busy: false };

// De Allerhande-recepten uit de planner, voor de titel in het detail terwijl het recept laadt.
function ahPlannerRecipes() {
  return (ahPlanner.data?.days || []).flatMap((day) => day.items.filter((item) => item.kind === "recipe").map((item) => ({ id: item.recipeId, title: item.title })));
}

// De twee keuzes van de gebruiker (bewaard in het profiel, standaard aan): losse producten van een dag samen als één menu,
// en soep en pizza dan toch los.
function ahPlannerOptions() {
  return { combine: state.auth?.plannerCombine !== false, splitMeals: state.auth?.plannerSplitMeals !== false };
}

// Gaat dit product als eigen recept (of als het recept met die naam in Mealie), en niet in het menu van die dag? Dezelfde
// regel als `_split` in ah_planner.py; `standalone` (soep, pizza) zet de server bij elk product.
function ahPlannerGoesAlone(item, options = ahPlannerOptions()) {
  return !options.combine || (options.splitMeals && Boolean(item.standalone));
}

// Wat er per dag naar het weekmenu gaat: de recepten, de producten en de eerste notitie (de naam van een menu).
function ahPlannerDaysToSend() {
  const options = ahPlannerOptions();
  return (ahPlanner.data?.days || [])
    .map((day) => ({
      date: day.date,
      recipeIds: day.items.filter((item) => item.kind === "recipe").map((item) => item.recipeId),
      products: day.items.filter((item) => item.kind === "product").map((item) => ({ productId: item.productId, title: item.title || "" })),
      note: day.items.find((item) => item.kind === "note" && item.text)?.text || "",
      ...options,
    }))
    .filter((day) => day.recipeIds.length || day.products.length);
}

// Een Allerhande-recept: een tik opent het recept in MealieShopper (met Inplannen, Importeer en de link naar AH).
function renderAhPlannerRecipe(item) {
  const title = item.title || t("recipe.unknown");
  return `<div class="meal meal--planned${item.image ? " meal--with-image" : ""}">
      <button class="meal__open" type="button" data-store-open="ah:${escapeHtml(item.recipeId)}" title="${escapeHtml(t("plan.view"))}">
        ${item.image ? `<img class="meal__image" src="${escapeHtml(storeImage(item.image))}" alt="" width="64" height="64" loading="lazy" data-hide-on-error>` : ""}
        <span class="meal__text">
          <span class="meal__type">${escapeHtml(t("ah.planner.recipe"))}</span>
          <strong>${escapeHtml(title)}</strong>
        </span>
      </button>
    </div>`;
}

// Een product, notitie of eigen recept, met wat er bij Naar weekmenu mee gebeurt. `menuNote` is de notitie die de naam van
// het menu van die dag wordt.
function renderAhPlannerOther(item, { goes, menuNote }) {
  const label = { own_recipe: "ah.planner.own_recipe", product: "ah.planner.product", note: "ah.planner.note" }[item.kind] || "ah.planner.unknown";
  const text = item.kind === "note" ? item.text : item.title;
  const name = item.kind === "product" && item.url
    ? `<a href="${escapeHtml(item.url)}" target="_blank" rel="noreferrer noopener">${escapeHtml(text || t("recipe.unknown"))}</a>`
    : escapeHtml(text || t("ah.planner.unknown_item"));
  const fate =
    item.kind === "product" ? t(goes(item) ? "ah.planner.goes_own" : "ah.planner.goes_menu")
      : item === menuNote ? t("ah.planner.menu_name")
        : t("ah.planner.not_transferred");
  const along = item.kind === "product" || item === menuNote;
  return `<div class="meal meal--note${along ? "" : " ah-planner__other"}">
      <span class="meal__text">
        <span class="meal__type">${escapeHtml(t(label))}</span>
        <strong${item.kind === "note" ? ' class="meal__note"' : ""}>${name}</strong>
        <small>${escapeHtml(fate)}</small>
      </span>
    </div>`;
}

function renderAhPlannerDay(day, today, options) {
  const goes = (item) => ahPlannerGoesAlone(item, options);
  // De eerste notitie wordt de naam van het menu, als er die dag een menu ontstaat (een product dat niet apart gaat).
  const menu = day.items.some((item) => item.kind === "product" && !goes(item));
  const menuNote = menu ? day.items.find((item) => item.kind === "note" && item.text) : null;
  const items = day.items.length
    ? day.items.map((item) => (item.kind === "recipe" ? renderAhPlannerRecipe(item) : renderAhPlannerOther(item, { goes, menuNote }))).join("")
    : `<p class="muted ah-planner__nothing">${escapeHtml(t("ah.planner.nothing_planned"))}</p>`;
  return `
        <article class="day-card${day.date === today ? " day-card--today" : ""}${day.items.length ? "" : " day-card--empty"}">
          <h3>${escapeHtml(formatDate(day.date))}${day.date === today ? `<span class="day-card__today">${escapeHtml(t("plan.today"))}</span>` : ""}</h3>
          ${items}
        </article>`;
}

function renderAhPlannerOptions(options = ahPlannerOptions()) {
  $("#ah-planner-combine").checked = options.combine;
  $("#ah-planner-split-meals").checked = options.splitMeals;
  $("#ah-planner-split-row").hidden = !options.combine; // soep en pizza los heeft alleen zin als producten samen een menu worden
}

function renderAhPlanner() {
  const data = ahPlanner.data;
  const days = data?.days || [];
  const options = ahPlannerOptions();
  const today = localIsoDate(new Date());
  renderAhPlannerOptions(options);
  $("#ah-planner-days").innerHTML = days.map((day) => renderAhPlannerDay(day, today, options)).join("");
  renderAhPlannerButton();
  if (!ahPlanner.loaded || !data) return;
  const items = days.flatMap((day) => day.items);
  const recipes = items.filter((item) => item.kind === "recipe").length;
  const products = items.filter((item) => item.kind === "product").length;
  // Wat met Naar weekmenu meegaat.
  $("#ah-planner-status").textContent = [recipes ? tn("ah.planner.count_recipes", recipes) : "", products ? tn("ah.planner.count_products", products) : ""].filter(Boolean).join(" · ");
  if (!items.length) message("#ah-planner-message", t("ah.planner.empty"), "success");
  else if (data.incomplete) message("#ah-planner-message", t("ah.planner.incomplete"), "warning");
}

// *Naar weekmenu* kan alleen met recepten of producten in de planner en met Mealie gekoppeld; de titel zegt waarom niet.
function renderAhPlannerButton() {
  const button = $("#ah-planner-to-meal-plan");
  const reason = !state.linkedStores.mealie ? t("ah.planner.needs_mealie") : !ahPlannerDaysToSend().length ? t("ah.planner.nothing_to_copy") : "";
  button.disabled = ahPlanner.busy || Boolean(reason);
  button.title = reason;
}

// Een keuze bewaren in het profiel, zoals *Liever biologisch* (savePreferOrganic in app.js). Lukt dat niet, dan geldt de keuze
// alleen nu en zegt een melding waarom.
async function saveAhPlannerOption(key, value) {
  state.auth = { ...state.auth, [key]: value };
  renderAhPlanner();
  try {
    const data = await jsonFetch("/api/profile/preferences", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ [key]: value }),
    });
    state.auth = { ...state.auth, plannerCombine: data.plannerCombine !== false, plannerSplitMeals: data.plannerSplitMeals !== false };
  } catch (error) {
    message("#ah-planner-message", t("ah.planner.options_save_failed", { error: error.message }), "error");
  }
  renderAhPlannerOptions();
}

async function loadAhPlanner() {
  const run = ++ahPlanner.run;
  const button = $("#load-ah-planner");
  setBusy(button, true);
  message("#ah-planner-message", "");
  $("#ah-planner-status").textContent = t("ah.planner.loading");
  try {
    const data = await jsonFetch("/api/ah/planner");
    if (run !== ahPlanner.run) return;
    ahPlanner.data = data;
    ahPlanner.loaded = true;
    renderAhPlanner();
  } catch (error) {
    if (run !== ahPlanner.run) return;
    ahPlanner.loaded = true;
    $("#ah-planner-status").textContent = "";
    renderAhPlanner();
    message("#ah-planner-message", error.message);
  } finally {
    if (run === ahPlanner.run) setBusy(button, false);
  }
}

function showAhPlanner() {
  renderAhPlannerOptions(); // de keuzes uit het profiel, ook terwijl de planner nog laadt
  if (!ahPlanner.loaded) loadAhPlanner();
  else renderAhPlanner(); // Mealie kan intussen gekoppeld zijn, of de keuzes zijn op een ander scherm veranderd
}

// De uitkomst van *Naar weekmenu* in één zin, met daaronder wat niet lukte.
function ahPlannerSummary(results, titles) {
  const count = (status) => results.filter((result) => result.status === status).length;
  const planned = count("planned");
  const imported = results.filter((result) => result.status === "planned" && result.imported).length;
  const already = count("already_planned");
  const failed = results.filter((result) => result.status === "failed");
  const parts = [
    planned ? `${tn("ah.planner.result.planned", planned)}${imported ? ` (${tn("ah.planner.result.imported", imported)})` : ""}` : "",
    already ? tn("ah.planner.result.already", already) : "",
    failed.length ? tn("ah.planner.result.failed", failed.length) : "",
  ].filter(Boolean);
  const name = (result) => result.title || titles.get(`${result.date}:${result.recipeId}`) || result.recipeId || t("recipe.unknown");
  const lines = failed.map((result) => `${formatDate(result.date)}: ${name(result)} (${result.error || t("err.generic")})`);
  return { text: [parts.join(" · "), ...lines].join("\n"), type: failed.length ? (planned || already ? "warning" : "error") : "success" };
}

// Een dag per verzoek: zo blijft elk verzoek kort (een import duurt een paar seconden) en ziet de gebruiker de voortgang.
async function ahPlannerToMealPlan() {
  const days = ahPlannerDaysToSend();
  if (!days.length || ahPlanner.busy) return;
  const button = $("#ah-planner-to-meal-plan");
  const titles = new Map(
    (ahPlanner.data?.days || []).flatMap((day) => day.items.filter((item) => item.kind === "recipe").map((item) => [`${day.date}:${item.recipeId}`, item.title]))
  );
  const results = [];
  ahPlanner.busy = true;
  setBusy(button, true);
  message("#ah-planner-message", "");
  try {
    for (const [index, day] of days.entries()) {
      $("#ah-planner-status").textContent = t("ah.planner.progress", { day: formatDate(day.date), current: I18N.formatNumber(index + 1), total: I18N.formatNumber(days.length) });
      try {
        const data = await jsonFetch("/api/ah/planner/to-meal-plan", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(day),
        });
        results.push(...(data.results || []));
      } catch (error) {
        // De hele dag mislukte (Mealie niet bereikbaar, een ongeldige dag): elk recept en de producten samen als mislukt.
        results.push(...day.recipeIds.map((recipeId) => ({ date: day.date, recipeId, status: "failed", error: error.message })));
        if (day.products.length) results.push({ date: day.date, title: day.products.map((product) => product.title).join(", "), status: "failed", error: error.message });
      }
    }
  } finally {
    ahPlanner.busy = false;
    setBusy(button, false);
    renderAhPlanner();
  }
  const summary = ahPlannerSummary(results, titles);
  message("#ah-planner-message", summary.text, summary.type);
  const changed = results.filter((result) => result.status === "planned").map((result) => result.date);
  if (changed.length && typeof refreshMealPlanAfterChange === "function") refreshMealPlanAfterChange([...new Set(changed)]);
}

$("#load-ah-planner").addEventListener("click", () => loadAhPlanner());
$("#ah-planner-to-meal-plan").addEventListener("click", ahPlannerToMealPlan);
$("#ah-planner-combine").addEventListener("change", (event) => saveAhPlannerOption("plannerCombine", event.target.checked));
$("#ah-planner-split-meals").addEventListener("change", (event) => saveAhPlannerOption("plannerSplitMeals", event.target.checked));
$("#ah-planner-days").addEventListener("click", (event) => {
  const open = event.target.closest("[data-store-open]");
  if (open) openStoreRecipe("ah", open.dataset.storeOpen.split(":", 2)[1], AH_PLANNER_HOST);
});
$("#ah-planner-recipe-back").addEventListener("click", () => showRecipeList(AH_PLANNER_HOST));
window.addEventListener("ms:languagechange", () => {
  if (ahPlanner.loaded) renderAhPlanner();
});
