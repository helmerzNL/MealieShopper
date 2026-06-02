const state = {
  weekOffset: 0,
  entries: null,
  searchPage: 0,
  searchQuery: "",
};

const dayNames = ["zo", "ma", "di", "wo", "do", "vr", "za"];
const monthNames = ["jan", "feb", "mrt", "apr", "mei", "jun", "jul", "aug", "sep", "okt", "nov", "dec"];
const mealLabels = { breakfast: "Ontbijt", lunch: "Lunch", dinner: "Diner", side: "Bijgerecht" };

function $(selector) {
  return document.querySelector(selector);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function jsonFetch(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || "Er is iets misgegaan");
  }
  return data;
}

function message(target, text, type = "error") {
  $(target).innerHTML = text ? `<div class="message ${type}">${escapeHtml(text)}</div>` : "";
}

function getWeekBounds(offset = 0) {
  const today = new Date();
  today.setDate(today.getDate() + offset * 7);
  const dow = today.getDay();
  const monday = new Date(today);
  monday.setDate(today.getDate() - (dow === 0 ? 6 : dow - 1));
  const sunday = new Date(monday);
  sunday.setDate(monday.getDate() + 6);
  const iso = (d) => d.toISOString().slice(0, 10);
  const label = (d) => `${d.getDate()} ${monthNames[d.getMonth()]}`;
  return { start: iso(monday), end: iso(sunday), label: `${label(monday)} - ${label(sunday)}` };
}

function updateWeekLabel() {
  const week = getWeekBounds(state.weekOffset);
  $("#week-range").textContent = week.label;
  $("#week-offset").textContent =
    state.weekOffset === 0
      ? "Deze week"
      : state.weekOffset === 1
        ? "Volgende week"
        : state.weekOffset === -1
          ? "Vorige week"
          : `${state.weekOffset > 0 ? "+" : ""}${state.weekOffset} weken`;
}

function formatDate(dateStr) {
  const date = new Date(`${dateStr}T00:00:00`);
  return `${dayNames[date.getDay()]} ${date.getDate()} ${monthNames[date.getMonth()]}`;
}

function aggregateIngredients(entries) {
  const seen = new Set();
  const result = [];
  for (const entry of entries || []) {
    for (const ingredient of entry.recipeDetail?.recipeIngredient || []) {
      const key = ingredient.food?.name || ingredient.display;
      if (key && !seen.has(key.toLowerCase())) {
        seen.add(key.toLowerCase());
        result.push(ingredient);
      }
    }
  }
  return result;
}

function renderMealPlan(entries) {
  const byDay = {};
  for (const entry of entries) {
    byDay[entry.date] ||= [];
    byDay[entry.date].push(entry);
  }
  const dates = Object.keys(byDay).sort();
  $("#meal-days").innerHTML = dates
    .map(
      (date) => `
        <article class="day-card">
          <h3>${escapeHtml(formatDate(date))}</h3>
          ${byDay[date]
            .map(
              (entry) => `
                <div class="meal">
                  <span>${escapeHtml(mealLabels[entry.entryType] || entry.entryType)}</span>
                  <strong>${escapeHtml(entry.recipeDetail?.name || entry.recipe?.name || entry.title || "-")}</strong>
                  ${
                    entry.recipeDetail
                      ? `<small>${entry.recipeDetail.recipeIngredient.length} ingredienten</small>`
                      : ""
                  }
                </div>
              `
            )
            .join("")}
        </article>
      `
    )
    .join("");

  const ingredients = aggregateIngredients(entries);
  if (!entries.length) {
    $("#ingredients-panel").innerHTML = "";
    message("#planner-message", "Geen maaltijden gepland voor deze week.", "success");
    return;
  }
  $("#ingredients-panel").innerHTML = ingredients.length
    ? `
      <div class="box ingredients">
        <div class="toolbar">
          <h2>Alle ingredienten (${ingredients.length})</h2>
          <button class="button" id="add-cart">Voeg toe aan AH winkelmandje</button>
        </div>
        <ul>
          ${ingredients.map((item) => `<li>${escapeHtml(item.display || item.food?.name || "-")}</li>`).join("")}
        </ul>
      </div>
    `
    : "";
  $("#add-cart")?.addEventListener("click", addToCart);
}

async function loadMealPlan() {
  const button = $("#load-plan");
  const week = getWeekBounds(state.weekOffset);
  button.disabled = true;
  button.textContent = "Laden...";
  message("#planner-message", "");
  $("#cart-result").innerHTML = "";
  try {
    state.entries = await jsonFetch(`/api/mealplan?start=${week.start}&end=${week.end}`);
    renderMealPlan(state.entries);
  } catch (error) {
    message("#planner-message", error.message);
  } finally {
    button.disabled = false;
    button.textContent = "Weekmenu laden";
  }
}

async function addToCart() {
  const ingredients = aggregateIngredients(state.entries);
  if (!ingredients.length) return;
  const button = $("#add-cart");
  button.disabled = true;
  button.textContent = "Toevoegen...";
  try {
    const items = ingredients.map((ingredient) => ({
      query: ingredient.food?.name || ingredient.display,
      quantity: Math.max(1, Math.round(ingredient.quantity || 1)),
    }));
    const data = await jsonFetch("/api/ah/cart", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items }),
    });
    $("#cart-result").innerHTML = `
      <div class="message success">${data.added} producten toegevoegd aan je AH winkelmandje${
        data.skipped ? ` (${data.skipped} niet gevonden)` : ""
      }.</div>
    `;
  } catch (error) {
    $("#cart-result").innerHTML = `<div class="message error">${escapeHtml(error.message)}</div>`;
  } finally {
    button.disabled = false;
    button.textContent = "Voeg toe aan AH winkelmandje";
  }
}

async function searchRecipes(page = 0) {
  const query = $("#recipe-query").value.trim();
  if (!query) return;
  state.searchQuery = query;
  state.searchPage = page;
  message("#search-message", "");
  $("#recipe-results").innerHTML = "";
  try {
    const data = await jsonFetch(`/api/ah/search?q=${encodeURIComponent(query)}&page=${page}`);
    $("#search-count").textContent = `${Number(data.total).toLocaleString("nl-NL")} recepten gevonden`;
    $("#recipe-results").innerHTML = (data.recipes || []).map(renderRecipeCard).join("");
    $("#prev-page").hidden = page === 0;
    $("#next-page").hidden = Number(data.total) <= (page + 1) * 12;
    document.querySelectorAll("[data-import-url]").forEach((button) => {
      button.addEventListener("click", () => importUrl(button.dataset.importUrl, button));
    });
  } catch (error) {
    message("#search-message", error.message);
  }
}

function renderRecipeCard(recipe) {
  const ahUrl = `https://www.ah.nl${recipe.webPath}`;
  const imageUrl = recipe.images?.[0]?.url;
  const totalTime = Number(recipe.cookTime || 0) + Number(recipe.preparationTime || 0);
  const tags = [...(recipe.courses || []), ...(recipe.keywords || [])].slice(0, 3);
  return `
    <article class="recipe-card">
      ${imageUrl ? `<img src="${escapeHtml(imageUrl)}" alt="${escapeHtml(recipe.title)}" loading="lazy">` : ""}
      <h3>${escapeHtml(recipe.title)}</h3>
      ${recipe.description ? `<p class="muted">${escapeHtml(recipe.description)}</p>` : ""}
      ${totalTime || recipe.servings ? `<p class="muted">${totalTime ? `${totalTime} min` : ""}${totalTime && recipe.servings ? " | " : ""}${recipe.servings ? `${recipe.servings} pers.` : ""}</p>` : ""}
      <div class="tags">${tags.map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join("")}</div>
      <div class="card-actions">
        <button class="button" data-import-url="${escapeHtml(ahUrl)}">Importeer</button>
        <a href="${escapeHtml(ahUrl)}" target="_blank" rel="noreferrer">Open bij AH</a>
      </div>
    </article>
  `;
}

async function importUrl(url, button = null) {
  if (button) {
    button.disabled = true;
    button.textContent = "Importeren...";
  }
  try {
    const data = await jsonFetch("/api/mealie/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    const link = data.mealieUrl ? ` <a href="${escapeHtml(data.mealieUrl)}" target="_blank" rel="noreferrer">Bekijk in Mealie</a>` : "";
    return { ok: true, html: `Recept geimporteerd.${link}` };
  } catch (error) {
    return { ok: false, html: error.message };
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = "Importeer";
    }
  }
}

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab, .panel").forEach((item) => item.classList.remove("is-active"));
    tab.classList.add("is-active");
    $(`#${tab.dataset.tab}`).classList.add("is-active");
  });
});

$("#prev-week").addEventListener("click", () => {
  state.weekOffset -= 1;
  updateWeekLabel();
});
$("#next-week").addEventListener("click", () => {
  state.weekOffset += 1;
  updateWeekLabel();
});
$("#load-plan").addEventListener("click", loadMealPlan);
$("#search-form").addEventListener("submit", (event) => {
  event.preventDefault();
  searchRecipes(0);
});
$("#prev-page").addEventListener("click", () => searchRecipes(state.searchPage - 1));
$("#next-page").addEventListener("click", () => searchRecipes(state.searchPage + 1));
$("#import-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const result = await importUrl($("#import-url").value.trim());
  $("#import-message").innerHTML = `<div class="message ${result.ok ? "success" : "error"}">${result.html}</div>`;
});
$("#verify-token").addEventListener("click", async () => {
  const token = $("#token-input").value.trim();
  if (!token) return;
  try {
    const isShortCode = token.length < 100 && !token.startsWith("eyJ");
    if (isShortCode) {
      const data = await jsonFetch("/api/ah/auth", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code: token }),
      });
      $("#token-input").value = data.refreshToken;
      message("#token-message", "Refresh token ontvangen. Voeg AH_REFRESH_TOKEN toe aan de containeromgeving.", "success");
      return;
    }
    await jsonFetch("/api/ah/auth/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refreshToken: token }),
    });
    message("#token-message", "Token werkt. Voeg AH_REFRESH_TOKEN toe aan de containeromgeving.", "success");
  } catch (error) {
    message("#token-message", error.message);
  }
});

const params = new URLSearchParams(window.location.search);
if (params.get("ah_refresh")) {
  document.querySelector('[data-tab="link"]').click();
  $("#token-input").value = params.get("ah_refresh");
  message("#token-message", "Refresh token ontvangen. Voeg AH_REFRESH_TOKEN toe aan de containeromgeving.", "success");
  history.replaceState({}, "", "/");
}
if (params.get("ah_error")) {
  document.querySelector('[data-tab="link"]').click();
  message("#token-message", `AH redirect mislukt: ${params.get("ah_error")}`);
  history.replaceState({}, "", "/");
}

updateWeekLabel();
