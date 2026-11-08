// Winkels > Albert Heijn > Weekplanner: wat in de weekplanner van de AH-app staat (alleen lezen, plannen doe je in de AH-app),
// en *Naar weekmenu*, dat de Allerhande-recepten op dezelfde dag in het weekmenu van Mealie zet (en ze zo nodig eerst
// importeert). Eigen recepten, producten en notities zijn te zien maar gaan niet mee. Zie mealieshopper/ah_planner.py.
// Laadt na app.js en recipe_search.js en gebruikt daar de hulpfuncties van ($, message, jsonFetch, setBusy, t, tn, ...).

const AH_PLANNER_HOST = { prefix: "ah-planner-recipe", list: "#ah-planner-view", detail: "#ah-planner-recipe-view", known: () => ahPlannerRecipes() };

const ahPlanner = { data: null, loaded: false, run: 0, busy: false };

// De Allerhande-recepten uit de planner, voor de titel in het detail terwijl het recept laadt.
function ahPlannerRecipes() {
  return (ahPlanner.data?.days || []).flatMap((day) => day.items.filter((item) => item.kind === "recipe").map((item) => ({ id: item.recipeId, title: item.title })));
}

// Per dag de recepten die naar het weekmenu gaan, in de volgorde van de dagen.
function ahPlannerDaysWithRecipes() {
  return (ahPlanner.data?.days || [])
    .map((day) => ({ date: day.date, items: day.items.filter((item) => item.kind === "recipe").map((item) => ({ date: day.date, recipeId: item.recipeId, title: item.title })) }))
    .filter((day) => day.items.length);
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

// Een eigen recept, een product of een notitie: te zien, maar het gaat niet mee naar het weekmenu.
function renderAhPlannerOther(item) {
  const label = { own_recipe: "ah.planner.own_recipe", product: "ah.planner.product", note: "ah.planner.note" }[item.kind] || "ah.planner.unknown";
  const text = item.kind === "note" ? item.text : item.title;
  const name = item.kind === "product" && item.url
    ? `<a href="${escapeHtml(item.url)}" target="_blank" rel="noreferrer noopener">${escapeHtml(text || t("recipe.unknown"))}</a>`
    : escapeHtml(text || t("ah.planner.unknown_item"));
  return `<div class="meal meal--note ah-planner__other">
      <span class="meal__text">
        <span class="meal__type">${escapeHtml(t(label))}</span>
        <strong${item.kind === "note" ? ' class="meal__note"' : ""}>${name}</strong>
        <small>${escapeHtml(t("ah.planner.not_transferred"))}</small>
      </span>
    </div>`;
}

function renderAhPlanner() {
  const data = ahPlanner.data;
  const days = data?.days || [];
  const recipes = ahPlannerDaysWithRecipes().reduce((sum, day) => sum + day.items.length, 0);
  const today = localIsoDate(new Date());
  $("#ah-planner-days").innerHTML = days
    .map(
      (day) => `
        <article class="day-card${day.date === today ? " day-card--today" : ""}${day.items.length ? "" : " day-card--empty"}">
          <h3>${escapeHtml(formatDate(day.date))}${day.date === today ? `<span class="day-card__today">${escapeHtml(t("plan.today"))}</span>` : ""}</h3>
          ${day.items.length ? day.items.map((item) => (item.kind === "recipe" ? renderAhPlannerRecipe(item) : renderAhPlannerOther(item))).join("") : `<p class="muted ah-planner__nothing">${escapeHtml(t("ah.planner.nothing_planned"))}</p>`}
        </article>`
    )
    .join("");
  renderAhPlannerButton(recipes);
  if (!ahPlanner.loaded || !data) return;
  const planned = days.filter((day) => day.items.length).length;
  // Het aantal recepten dat met Naar weekmenu meegaat.
  $("#ah-planner-status").textContent = recipes ? tn("ah.planner.count", recipes) : "";
  if (!planned) message("#ah-planner-message", t("ah.planner.empty"), "success");
  else if (data.incomplete) message("#ah-planner-message", t("ah.planner.incomplete"), "warning");
}

// *Naar weekmenu* kan alleen met recepten in de planner en met Mealie gekoppeld; de titel zegt waarom niet.
function renderAhPlannerButton(recipes = ahPlannerDaysWithRecipes().reduce((sum, day) => sum + day.items.length, 0)) {
  const button = $("#ah-planner-to-meal-plan");
  const reason = !state.linkedStores.mealie ? t("ah.planner.needs_mealie") : !recipes ? t("ah.planner.no_recipes") : "";
  button.disabled = ahPlanner.busy || Boolean(reason);
  button.title = reason;
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
  if (!ahPlanner.loaded) loadAhPlanner();
  else renderAhPlannerButton(); // Mealie kan intussen gekoppeld zijn
}

// De uitkomst van *Naar weekmenu* in één zin, met daaronder de recepten die niet lukten.
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
  const lines = failed.map((result) => `${formatDate(result.date)}: ${titles.get(`${result.date}:${result.recipeId}`) || result.recipeId} (${result.error || t("err.generic")})`);
  return { text: [parts.join(" · "), ...lines].join("\n"), type: failed.length ? (planned || already ? "warning" : "error") : "success" };
}

// Een dag per verzoek: zo blijft elk verzoek kort (een import duurt een paar seconden) en ziet de gebruiker de voortgang.
async function ahPlannerToMealPlan() {
  const days = ahPlannerDaysWithRecipes();
  if (!days.length || ahPlanner.busy) return;
  const button = $("#ah-planner-to-meal-plan");
  const titles = new Map(days.flatMap((day) => day.items.map((item) => [`${item.date}:${item.recipeId}`, item.title])));
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
          body: JSON.stringify({ items: day.items.map(({ date, recipeId }) => ({ date, recipeId })) }),
        });
        results.push(...(data.results || []));
      } catch (error) {
        results.push(...day.items.map((item) => ({ date: item.date, recipeId: item.recipeId, status: "failed", error: error.message })));
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
$("#ah-planner-days").addEventListener("click", (event) => {
  const open = event.target.closest("[data-store-open]");
  if (open) openStoreRecipe("ah", open.dataset.storeOpen.split(":", 2)[1], AH_PLANNER_HOST);
});
$("#ah-planner-recipe-back").addEventListener("click", () => showRecipeList(AH_PLANNER_HOST));
window.addEventListener("ms:languagechange", () => {
  if (ahPlanner.loaded) renderAhPlanner();
});
