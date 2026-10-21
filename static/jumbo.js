// Jumbo: koppelen (een regel uit de browserconsole plakken, zoals bij Albert Heijn) en verkennen.
// Laadt na app.js en gebruikt daar de hulpfuncties van ($, message, jsonFetch, setBusy, t, ...).
// De keuze van de supermarkt in de planner staat in app.js (renderStoreChoice).

const jumboState = { connected: false, report: null };

function renderJumboStatus() {
  $("#jumbo-unlink").hidden = !jumboState.connected;
  $("#jumbo-check").hidden = !jumboState.connected;
  message("#jumbo-status", jumboState.connected ? t("jumbo.linked") : t("jumbo.not_linked"), jumboState.connected ? "success" : "error");
}

async function refreshJumboStatus() {
  try {
    const data = await jsonFetch("/api/jumbo/auth/status");
    jumboState.connected = Boolean(data.connected);
    state.linkedStores.jumbo = jumboState.connected;
    renderJumboStatus();
  } catch (error) {
    message("#jumbo-status", error.message);
  }
  renderStoreChoice();
}

// ---- Het Jumbo-mandje in het tabblad Mandje (alleen lezen)
async function showJumboBasket() {
  const cart = await jsonFetch("/api/jumbo/cart");
  const items = cart.items || [];
  const lines = items
    .map((item) => {
      const price = item.price ? escapeHtml(formatPrice(item.price)) : "";
      return `<li class="basket-line"><span class="basket-title">${escapeHtml(item.title || "-")}</span><span class="basket-qty">${escapeHtml(item.quantity)}\u00d7</span><span class="basket-price">${price}</span><span></span></li>`;
    })
    .join("");
  const total = cart.totalPrice ? `<p><strong>${escapeHtml(t("picnic.basket_total", { amount: formatPrice(cart.totalPrice) }))}</strong></p>` : "";
  $("#basket-content").innerHTML = `
    ${items.length ? `<ul class="basket-lines">${lines}</ul>` : `<p class="muted">${escapeHtml(t("basket.jumbo_empty"))}</p>`}
    ${total}
    <p class="muted">${escapeHtml(t("basket.jumbo_hint"))}</p>`;
}

async function checkJumboConnection() {
  message("#jumbo-status", t("ah.checking"), "success");
  try {
    const data = await jsonFetch("/api/jumbo/auth/status?verify=1");
    if (data.verified) message("#jumbo-status", t("jumbo.works"), "success");
    else message("#jumbo-status", data.connected ? t("jumbo.broken", { error: data.error || "" }) : t("jumbo.not_linked"));
  } catch (error) {
    message("#jumbo-status", error.message);
  }
}

async function linkJumbo() {
  const button = $("#jumbo-link");
  const text = $("#jumbo-code").value.trim();
  if (!text) {
    message("#jumbo-message", t("jumbo.paste_first"));
    return;
  }
  setBusy(button, true, t("jumbo.linking"));
  message("#jumbo-message", "");
  try {
    await jsonFetch("/api/jumbo/auth", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code: text }),
    });
    $("#jumbo-code").value = "";
    message("#jumbo-message", t("jumbo.saved"), "success");
    await refreshJumboStatus();
  } catch (error) {
    message("#jumbo-message", error.message);
  } finally {
    setBusy(button, false, t("jumbo.linking"));
  }
}

async function unlinkJumbo() {
  try {
    await jsonFetch("/api/jumbo/auth/logout", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    jumboState.connected = false;
    state.linkedStores.jumbo = false;
    renderJumboStatus();
    renderStoreChoice();
  } catch (error) {
    message("#jumbo-status", error.message);
  }
}

// ---- Verkenner (alleen de beheerder)
function renderJumboExploreSummary() {
  const report = jumboState.report;
  if (report) $("#jumbo-explore-summary").textContent = t("explore.summary", { ok: report.ok, total: report.probes.length });
}

async function runJumboExplore() {
  const button = $("#jumbo-explore-run");
  setBusy(button, true);
  message("#jumbo-explore-message", "");
  try {
    jumboState.report = await jsonFetch("/api/jumbo/explore", { method: "POST" });
    renderJumboExploreSummary();
    $("#jumbo-explore-output").textContent = JSON.stringify(jumboState.report, null, 2);
    $("#jumbo-explore-report").hidden = false;
    if (!jumboState.report.linked) message("#jumbo-explore-message", t("jumbo.explore_not_linked"));
  } catch (error) {
    message("#jumbo-explore-message", error.message);
  } finally {
    setBusy(button, false);
  }
}

$("#jumbo-link").addEventListener("click", linkJumbo);
$("#jumbo-check").addEventListener("click", checkJumboConnection);
$("#jumbo-unlink").addEventListener("click", unlinkJumbo);
$("#jumbo-explore-run").addEventListener("click", runJumboExplore);
$("#jumbo-explore-copy").addEventListener("click", async (event) => {
  const text = $("#jumbo-explore-output").textContent;
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const range = document.createRange();
    range.selectNodeContents($("#jumbo-explore-output"));
    getSelection().removeAllRanges();
    getSelection().addRange(range);
    document.execCommand("copy");
  }
  event.currentTarget.textContent = t("users.copied");
  setTimeout(() => (event.currentTarget.textContent = t("users.copy")), 2000);
});

// ---- Recepten van Jumbo in de tab Winkels: zoeken, openen (ingrediënten en bereiding) en importeren in Mealie
const jumboRecipes = { query: "", recipes: [], total: 0, pageSize: 24, searched: false, open: null };

function jumboRecipeMeta(recipe) {
  return [
    recipe.minutes ? t("jumbo.recipes.minutes", { count: recipe.minutes }) : "",
    recipe.portions ? t("jumbo.recipes.portions", { count: recipe.portions }) : "",
    recipe.rating ? `\u2605 ${I18N.formatNumber(recipe.rating)}` : "",
  ]
    .filter(Boolean)
    .join(" \u00b7 ");
}

function renderJumboRecipeCard(recipe) {
  const meta = jumboRecipeMeta(recipe);
  return `
    <article class="recipe-card" data-jumbo-recipe="${escapeHtml(recipe.id)}">
      ${recipe.image ? `<img src="${escapeHtml(recipe.image)}" alt="${escapeHtml(recipe.name)}" loading="lazy" data-hide-on-error>` : ""}
      <h3>${escapeHtml(recipe.name)}</h3>
      ${meta ? `<p class="muted">${escapeHtml(meta)}</p>` : ""}
      <div class="recipe-card__actions">
        <button class="button button--secondary" type="button" data-jumbo-open="${escapeHtml(recipe.id)}">${escapeHtml(t("jumbo.recipes.open"))}</button>
        <button class="button" type="button" data-jumbo-import="${escapeHtml(recipe.id)}">${escapeHtml(t("recipe.import"))}</button>
      </div>
      <div class="recipe-import-result"></div>
    </article>`;
}

function renderJumboRecipes() {
  const all = jumboRecipes.recipes;
  $("#jumbo-recipes").innerHTML = all.map(renderJumboRecipeCard).join("");
  $("#jumbo-recipes-status").textContent = all.length
    ? t("jumbo.recipes.count", { shown: I18N.formatNumber(all.length), total: I18N.formatNumber(jumboRecipes.total) })
    : "";
  $("#jumbo-recipes-more").hidden = !(all.length && all.length < jumboRecipes.total);
  if (jumboRecipes.searched && !all.length) message("#jumbo-recipes-message", t("jumbo.recipes.none", { query: jumboRecipes.query }), "success");
}

async function searchJumboRecipes(append = false) {
  const query = append ? jumboRecipes.query : $("#jumbo-recipe-query").value.trim();
  if (!query) return;
  const button = append ? $("#jumbo-recipes-more") : $("#jumbo-recipe-search");
  setBusy(button, true);
  message("#jumbo-recipes-message", "");
  const offset = append ? jumboRecipes.recipes.length : 0;
  try {
    const data = await jsonFetch(`/api/jumbo/recipes?q=${encodeURIComponent(query)}&offset=${offset}`);
    const known = new Set(append ? jumboRecipes.recipes.map((recipe) => recipe.id) : []);
    const fresh = (data.recipes || []).filter((recipe) => !known.has(recipe.id));
    jumboRecipes.query = query;
    jumboRecipes.recipes = append ? [...jumboRecipes.recipes, ...fresh] : fresh;
    jumboRecipes.total = data.total ?? jumboRecipes.recipes.length;
    jumboRecipes.pageSize = data.pageSize || jumboRecipes.pageSize;
    jumboRecipes.searched = true;
    // Een pagina zonder nieuwe recepten: stop met "Meer recepten", anders blijft die knop eindeloos staan.
    if (append && !fresh.length) jumboRecipes.total = jumboRecipes.recipes.length;
    renderJumboRecipes();
  } catch (error) {
    message("#jumbo-recipes-message", error.message);
  } finally {
    setBusy(button, false);
  }
}

function focusJumboRecipeSearch() {
  if ($("#jumbo-recipe-view").hidden) $("#jumbo-recipe-query").focus({ preventScroll: true });
}

async function importJumboRecipe(id, button, target) {
  button.disabled = true;
  button.textContent = t("recipe.importing");
  try {
    const data = await postJson(`/api/jumbo/recipes/${encodeURIComponent(id)}/import`);
    const link = data.mealieUrl ? ` <a href="${escapeHtml(data.mealieUrl)}" target="_blank" rel="noreferrer noopener">${escapeHtml(t("recipe.view_mealie"))}</a>` : "";
    target.innerHTML = `<div class="message success">${escapeHtml(t("recipe.imported"))}${link}</div>`;
  } catch (error) {
    target.innerHTML = `<div class="message error">${escapeHtml(error.message)}</div>`;
  } finally {
    button.disabled = false;
    button.textContent = t("recipe.import");
  }
}

function showJumboRecipeList() {
  $("#jumbo-recipe-view").hidden = true;
  $("#jumbo-recipes-view").hidden = false;
}

async function openJumboRecipe(id) {
  $("#jumbo-recipes-view").hidden = true;
  $("#jumbo-recipe-view").hidden = false;
  $("#jumbo-recipe-title").textContent = jumboRecipes.recipes.find((recipe) => recipe.id === id)?.name || "";
  $("#jumbo-recipe-body").innerHTML = `<p class="muted">${escapeHtml(t("basket.loading"))}</p>`;
  message("#jumbo-recipe-message", "");
  $("#jumbo-recipe-title").focus();
  try {
    renderJumboRecipeDetail(await jsonFetch(`/api/jumbo/recipes/${encodeURIComponent(id)}`));
  } catch (error) {
    $("#jumbo-recipe-body").innerHTML = "";
    message("#jumbo-recipe-message", error.message);
  }
}

function renderJumboRecipeDetail(recipe) {
  $("#jumbo-recipe-title").textContent = recipe.name;
  const facts = [
    recipe.portions ? t("jumbo.recipes.portions", { count: recipe.portions }) : "",
    recipe.minutes ? t("jumbo.recipes.minutes", { count: recipe.minutes }) : "",
    recipe.rating ? `\u2605 ${I18N.formatNumber(recipe.rating)}${recipe.ratingCount ? ` (${I18N.formatNumber(recipe.ratingCount)})` : ""}` : "",
    recipe.author || "",
  ].filter(Boolean);
  const ingredients = (recipe.ingredients || []).map((item) => `<li>${escapeHtml(item.line)}</li>`).join("");
  const steps = (recipe.steps || []).map((text) => `<li>${escapeHtml(text)}</li>`).join("");
  const nutrition = (recipe.nutrition || []).map((item) => `${item.name} ${I18N.formatNumber(item.quantity)} ${item.unit}`.trim()).join(" \u00b7 ");
  $("#jumbo-recipe-body").innerHTML = `
    <div class="picnic-recipe">
      ${recipe.image ? `<img class="picnic-recipe__image" src="${escapeHtml(recipe.image)}" alt="" data-hide-on-error>` : ""}
      ${facts.length ? `<p class="muted">${escapeHtml(facts.join(" \u00b7 "))}</p>` : ""}
      ${recipe.description ? `<p>${escapeHtml(recipe.description)}</p>` : ""}
      <p>
        <button class="button" type="button" id="jumbo-recipe-import">${escapeHtml(t("recipe.import"))}</button>
        ${recipe.url ? `<a class="button button--secondary" href="${escapeHtml(recipe.url)}" target="_blank" rel="noreferrer noopener">${escapeHtml(t("jumbo.recipes.on_site"))}</a>` : ""}
      </p>
      <div id="jumbo-recipe-import-result"></div>
      <h3>${escapeHtml(t("jumbo.recipes.ingredients"))}</h3>
      ${ingredients ? `<ul class="picnic-recipe__list">${ingredients}</ul>` : `<p class="muted">${escapeHtml(t("jumbo.recipes.no_ingredients"))}</p>`}
      <h3>${escapeHtml(t("jumbo.recipes.steps"))}</h3>
      ${steps ? `<ol class="picnic-recipe__list">${steps}</ol>` : `<p class="muted">${escapeHtml(t("jumbo.recipes.no_steps"))}</p>`}
      ${recipe.tip ? `<h3>${escapeHtml(t("jumbo.recipes.tip"))}</h3><p>${escapeHtml(recipe.tip)}</p>` : ""}
      ${nutrition ? `<p class="muted">${escapeHtml(t("jumbo.recipes.nutrition", { values: nutrition }))}</p>` : ""}
    </div>`;
  $("#jumbo-recipe-import").addEventListener("click", (event) => importJumboRecipe(recipe.id, event.currentTarget, $("#jumbo-recipe-import-result")));
}

$("#jumbo-recipe-form").addEventListener("submit", (event) => {
  event.preventDefault();
  searchJumboRecipes(false);
});
$("#jumbo-recipes-more").addEventListener("click", () => searchJumboRecipes(true));
$("#jumbo-recipe-back").addEventListener("click", showJumboRecipeList);
$("#jumbo-recipes").addEventListener("click", (event) => {
  const open = event.target.closest("[data-jumbo-open]");
  const importButton = event.target.closest("[data-jumbo-import]");
  if (open) openJumboRecipe(open.dataset.jumboOpen);
  if (importButton) importJumboRecipe(importButton.dataset.jumboImport, importButton, importButton.closest(".recipe-card").querySelector(".recipe-import-result"));
});

window.addEventListener("ms:languagechange", () => {
  renderJumboStatus();
  renderStoreChoice();
  renderJumboExploreSummary();
  if (jumboRecipes.recipes.length) renderJumboRecipes();
});
