const state = {
  weekOffset: 0,
  entries: null,
  searchPage: 0,
  searchQuery: "",
  auth: null,
  credentials: [],
  store: "ah",
};

const storeLabels = { ah: "Albert Heijn", jumbo: "Jumbo" };

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
  const response = await fetch(url, { credentials: "same-origin", ...options });
  let data;
  try {
    data = await response.json();
  } catch (error) {
    throw new Error(
      response.ok
        ? "Onverwacht antwoord van de server (geen geldige JSON)."
        : `Serverfout (${response.status}). Is de app bijgewerkt en ben je ingelogd?`
    );
  }
  if (!response.ok) {
    throw new Error(data.error || "Er is iets misgegaan");
  }
  return data;
}

function message(target, text, type = "error") {
  $(target).innerHTML = text ? `<div class="message ${type}">${escapeHtml(text)}</div>` : "";
}

function bufferToBase64url(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  bytes.forEach((byte) => {
    binary += String.fromCharCode(byte);
  });
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
}

function base64urlToBuffer(value) {
  const base64 = value.replaceAll("-", "+").replaceAll("_", "/");
  const padded = base64.padEnd(base64.length + ((4 - (base64.length % 4)) % 4), "=");
  const binary = atob(padded);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes.buffer;
}

function webauthnUnavailableReason() {
  if (!window.PublicKeyCredential || !navigator.credentials) {
    return "Deze browser ondersteunt geen passkeys.";
  }
  const secure = window.isSecureContext || ["localhost", "127.0.0.1", "::1"].includes(window.location.hostname);
  if (!secure) {
    return "Open MealieShopper via HTTPS om passkeys te gebruiken.";
  }
  return "";
}

function publicKeyCredentialToJSON(credential) {
  return {
    id: credential.id,
    rawId: bufferToBase64url(credential.rawId),
    type: credential.type,
    authenticatorAttachment: credential.authenticatorAttachment,
    response: {
      clientDataJSON: bufferToBase64url(credential.response.clientDataJSON),
      attestationObject: credential.response.attestationObject
        ? bufferToBase64url(credential.response.attestationObject)
        : undefined,
      authenticatorData: credential.response.authenticatorData
        ? bufferToBase64url(credential.response.authenticatorData)
        : undefined,
      signature: credential.response.signature ? bufferToBase64url(credential.response.signature) : undefined,
      userHandle: credential.response.userHandle ? bufferToBase64url(credential.response.userHandle) : undefined,
      transports: credential.response.getTransports ? credential.response.getTransports() : [],
    },
  };
}

function renderAuthState() {
  const gate = $("#auth-gate");
  const chip = $("#auth-chip");
  const title = $("#auth-title");
  const description = $("#auth-description");
  const primary = $("#auth-primary");
  const setupRequired = state.auth?.setupRequired;
  const authenticated = state.auth?.authenticated || !state.auth?.enabled;

  gate.hidden = authenticated;
  chip.hidden = !authenticated || !state.auth?.enabled;
  $("#auth-chip-text").textContent = authenticated && state.auth?.username ? `Ingelogd als ${state.auth.username}` : "";

  if (setupRequired) {
    title.textContent = "Eerste passkey";
    description.textContent = "Maak de owner passkey aan voor deze MealieShopper instance.";
    primary.textContent = "Owner passkey maken";
    $("#auth-username").hidden = false;
    $("#auth-passkey-name").hidden = false;
  } else {
    title.textContent = "Inloggen met passkey";
    description.textContent = "Bevestig met je passkey om MealieShopper te openen.";
    primary.textContent = "Inloggen";
    $("#auth-username").hidden = true;
    $("#auth-passkey-name").hidden = true;
  }

  const unavailable = webauthnUnavailableReason();
  if (unavailable && !authenticated) {
    message("#auth-message", unavailable);
    primary.disabled = true;
  } else {
    primary.disabled = false;
  }
}

function formatCredentialDate(value) {
  if (!value) return "-";
  return String(value).slice(0, 19).replace("T", " ");
}

function renderCredentials() {
  const target = $("#credentials-list");
  if (!target) return;
  if (!state.auth?.authenticated || !state.auth?.enabled) {
    target.innerHTML = `<p class="muted">Log in om passkeys te beheren.</p>`;
    return;
  }
  target.innerHTML = state.credentials.length
    ? state.credentials
        .map(
          (credential) => `
            <div class="credential-row">
              <div>
                <strong>${escapeHtml(credential.credential_name || "Passkey")}</strong>
                <small>${escapeHtml(credential.username || "admin")} | aangemaakt ${escapeHtml(formatCredentialDate(credential.created_at))}${
                  credential.last_used_at ? ` | laatst gebruikt ${escapeHtml(formatCredentialDate(credential.last_used_at))}` : ""
                }</small>
              </div>
              <button class="button button--secondary" data-delete-passkey="${escapeHtml(credential.id)}" ${
                state.credentials.length <= 1 ? "disabled" : ""
              }>Verwijderen</button>
            </div>
          `
        )
        .join("")
    : `<p class="muted">Geen passkeys gevonden.</p>`;
  document.querySelectorAll("[data-delete-passkey]").forEach((button) => {
    button.addEventListener("click", () => deletePasskey(button.dataset.deletePasskey));
  });
}

async function loadCredentials() {
  if (!state.auth?.authenticated || !state.auth?.enabled) {
    state.credentials = [];
    renderCredentials();
    return;
  }
  const data = await jsonFetch("/api/auth/credentials");
  state.credentials = data.credentials || [];
  renderCredentials();
}

async function refreshAuthStatus() {
  state.auth = await jsonFetch("/api/auth/status");
  renderAuthState();
  await loadCredentials();
  if (state.auth?.authenticated || !state.auth?.enabled) {
    refreshAhStatus();
    refreshJumboStatus();
    refreshAhBrowserStatus();
  }
  return state.auth;
}

async function registerPasskey({ username, credentialName, messageTarget }) {
  message(messageTarget, "Wachten op je passkey prompt...", "success");
  const payload = await jsonFetch("/api/auth/register/options", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, credentialName }),
  });
  const options = payload.options;
  options.challenge = base64urlToBuffer(options.challenge);
  options.user.id = base64urlToBuffer(options.user.id);
  options.excludeCredentials = (options.excludeCredentials || []).map((credential) => ({
    ...credential,
    id: base64urlToBuffer(credential.id),
  }));
  const credential = await navigator.credentials.create({ publicKey: options });
  await jsonFetch("/api/auth/register/verify", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      userId: payload.userId,
      username,
      credentialName,
      credential: publicKeyCredentialToJSON(credential),
    }),
  });
}

async function createOwnerPasskey() {
  const username = $("#auth-username").value.trim() || "admin";
  const credentialName = $("#auth-passkey-name").value.trim() || "Owner passkey";
  await registerPasskey({ username, credentialName, messageTarget: "#auth-message" });
  message("#auth-message", "Passkey aangemaakt. Je bent ingelogd.", "success");
  await refreshAuthStatus();
}

async function addPasskey() {
  const button = $("#add-passkey");
  const credentialName = $("#new-passkey-name").value.trim() || "Extra passkey";
  const username = state.auth?.username || "admin";
  button.disabled = true;
  try {
    await registerPasskey({ username, credentialName, messageTarget: "#security-message" });
    message("#security-message", "Extra passkey toegevoegd.", "success");
    await refreshAuthStatus();
  } catch (error) {
    message("#security-message", error.name === "NotAllowedError" ? "Passkey prompt geannuleerd." : error.message);
  } finally {
    button.disabled = false;
  }
}

async function deletePasskey(credentialId) {
  if (!window.confirm("Deze passkey verwijderen?")) return;
  try {
    await jsonFetch(`/api/auth/credentials/${encodeURIComponent(credentialId)}`, { method: "DELETE" });
    message("#security-message", "Passkey verwijderd.", "success");
    await refreshAuthStatus();
  } catch (error) {
    message("#security-message", error.message);
  }
}

async function loginWithPasskey() {
  message("#auth-message", "Wachten op je passkey prompt...", "success");
  const payload = await jsonFetch("/api/auth/login/options", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  });
  const options = payload.options;
  options.challenge = base64urlToBuffer(options.challenge);
  options.allowCredentials = (options.allowCredentials || []).map((credential) => ({
    ...credential,
    id: base64urlToBuffer(credential.id),
  }));
  const credential = await navigator.credentials.get({ publicKey: options });
  await jsonFetch("/api/auth/login/verify", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ credential: publicKeyCredentialToJSON(credential) }),
  });
  message("#auth-message", "Ingelogd.", "success");
  await refreshAuthStatus();
}

async function handleAuthPrimary() {
  const button = $("#auth-primary");
  button.disabled = true;
  try {
    if (state.auth?.setupRequired) {
      await createOwnerPasskey();
    } else {
      await loginWithPasskey();
    }
  } catch (error) {
    message("#auth-message", error.name === "NotAllowedError" ? "Passkey prompt geannuleerd." : error.message);
  } finally {
    button.disabled = false;
  }
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
          <div class="store-select" role="group" aria-label="Kies winkel">
            <label class="store-option">
              <input type="radio" name="store" value="ah"${state.store === "ah" ? " checked" : ""}>
              <span>Albert Heijn</span>
            </label>
            <label class="store-option">
              <input type="radio" name="store" value="jumbo"${state.store === "jumbo" ? " checked" : ""}>
              <span>Jumbo</span>
            </label>
          </div>
          <button class="button" id="add-cart">Voeg toe aan ${escapeHtml(storeLabels[state.store])} mandje</button>
        </div>
        <ul>
          ${ingredients.map((item) => `<li>${escapeHtml(item.display || item.food?.name || "-")}</li>`).join("")}
        </ul>
      </div>
    `
    : "";
  document.querySelectorAll('input[name="store"]').forEach((radio) => {
    radio.addEventListener("change", () => {
      state.store = radio.value;
      const button = $("#add-cart");
      if (button) button.textContent = `Voeg toe aan ${storeLabels[state.store]} mandje`;
    });
  });
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
  const store = state.store === "jumbo" ? "jumbo" : "ah";
  const storeLabel = storeLabels[store];
  const endpoint = store === "jumbo" ? "/api/jumbo/cart" : "/api/ah/cart";
  const button = $("#add-cart");
  button.disabled = true;
  button.textContent = "Toevoegen...";
  try {
    const items = ingredients.map((ingredient) => ({
      query: ingredient.food?.name || ingredient.display,
      quantity: Math.max(1, Math.round(ingredient.quantity || 1)),
    }));
    const data = await jsonFetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items }),
    });
    $("#cart-result").innerHTML = `
      <div class="message success">${data.added} producten toegevoegd aan je ${escapeHtml(storeLabel)} mandje${
        data.skipped ? ` (${data.skipped} niet gevonden)` : ""
      }.</div>
    `;
  } catch (error) {
    $("#cart-result").innerHTML = `<div class="message error">${escapeHtml(error.message)}</div>`;
  } finally {
    button.disabled = false;
    button.textContent = `Voeg toe aan ${storeLabel} mandje`;
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

async function refreshAhStatus() {
  if (!$("#ah-status")) return;
  try {
    const data = await jsonFetch("/api/ah/auth/status");
    message(
      "#ah-status",
      data.connected ? "AH account gekoppeld." : "AH account nog niet gekoppeld.",
      data.connected ? "success" : "error"
    );
  } catch (error) {
    message("#ah-status", error.message);
  }
}

async function refreshJumboStatus() {
  if (!$("#jumbo-status")) return;
  try {
    const data = await jsonFetch("/api/jumbo/auth/status");
    const connected = Boolean(data.connected);
    message(
      "#jumbo-status",
      connected
        ? `Jumbo account gekoppeld${data.username ? ` (${data.username})` : ""}.`
        : "Jumbo account nog niet gekoppeld.",
      connected ? "success" : "error"
    );
    const logout = $("#jumbo-logout");
    if (logout) logout.hidden = !connected;
  } catch (error) {
    message("#jumbo-status", error.message);
  }
}

async function refreshAhBrowserStatus() {
  if (!$("#ah-browser-status")) return;
  try {
    const data = await jsonFetch("/api/ah/browser/status");
    const connected = Boolean(data.connected);
    message(
      "#ah-browser-status",
      connected
        ? `AH website-koppeling actief${data.username ? ` (${data.username})` : ""}.`
        : "AH website-koppeling nog niet gemaakt (alleen nodig voor bewaarde recepten).",
      connected ? "success" : "error"
    );
    const logout = $("#ah-browser-logout");
    if (logout) logout.hidden = !connected;
  } catch (error) {
    message("#ah-browser-status", error.message);
  }
}

function renderAhLists(lists) {
  $("#ah-lists").innerHTML = lists.length
    ? `
      <div class="credential-list">
        ${lists
          .map(
            (list) => `
              <div class="credential-row">
                <div>
                  <strong>${escapeHtml(list.name || "-")}</strong>
                  <small>${Number(list.itemCount || 0).toLocaleString("nl-NL")} items</small>
                </div>
                <button class="button button--secondary" data-ah-list-id="${escapeHtml(list.id)}">Openen</button>
              </div>
            `
          )
          .join("")}
      </div>
    `
    : `<p class="muted">Geen AH lijstjes gevonden.</p>`;
  document.querySelectorAll("[data-ah-list-id]").forEach((button) => {
    button.addEventListener("click", () => loadAhFavoriteListItems(button.dataset.ahListId));
  });
}

async function loadAhFavoriteLists() {
  const button = $("#load-ah-lists");
  button.disabled = true;
  button.textContent = "Laden...";
  $("#ah-list-items").innerHTML = "";
  try {
    const data = await jsonFetch("/api/ah/favorite-lists");
    renderAhLists(data.lists || []);
  } catch (error) {
    message("#ah-lists", error.message);
  } finally {
    button.disabled = false;
    button.textContent = "Laden";
  }
}

async function loadAhFavoriteListItems(listId) {
  $("#ah-list-items").innerHTML = `<div class="message success">Lijst laden...</div>`;
  try {
    const data = await jsonFetch(`/api/ah/favorite-lists/${encodeURIComponent(listId)}/items`);
    $("#ah-list-items").innerHTML = `
      <div class="ingredients">
        <h2>${escapeHtml(data.name || "AH lijst")}</h2>
        <ul>
          ${(data.items || [])
            .map((item) => {
              const product = item.product || {};
              const price = Number(product.price || 0);
              return `<li>${escapeHtml(item.quantity)}x ${escapeHtml(product.title || `Product ${item.productId}`)}${
                price ? ` - &euro;${price.toFixed(2).replace(".", ",")}` : ""
              }</li>`;
            })
            .join("")}
        </ul>
      </div>
    `;
  } catch (error) {
    message("#ah-list-items", error.message);
  }
}

function renderSavedRecipeCard(recipe) {
  const url = recipe.url || "";
  const image = recipe.image || "";
  return `
    <article class="recipe-card">
      ${image ? `<img src="${escapeHtml(image)}" alt="${escapeHtml(recipe.title || "")}" loading="lazy">` : ""}
      <h3>${escapeHtml(recipe.title || "Onbekend recept")}</h3>
      <div class="card-actions">
        <button class="button" data-import-url="${escapeHtml(url)}">Importeer</button>
        ${url ? `<a href="${escapeHtml(url)}" target="_blank" rel="noreferrer">Open recept</a>` : ""}
      </div>
      <div class="recipe-import-result"></div>
    </article>
  `;
}

async function loadSavedRecipes(endpoint, containerId, messageId, button) {
  if (button) {
    button.disabled = true;
    button.textContent = "Laden...";
  }
  message(messageId, "");
  $(containerId).innerHTML = "";
  try {
    const data = await jsonFetch(endpoint);
    const recipes = data.recipes || [];
    if (!recipes.length) {
      message(messageId, "Geen bewaarde recepten gevonden.", "success");
      return;
    }
    $(containerId).innerHTML = recipes.map(renderSavedRecipeCard).join("");
    $(containerId)
      .querySelectorAll("[data-import-url]")
      .forEach((btn) => {
        btn.addEventListener("click", async () => {
          const result = await importUrl(btn.dataset.importUrl, btn);
          const target = btn.closest(".recipe-card")?.querySelector(".recipe-import-result");
          if (target) {
            target.innerHTML = `<div class="message ${result.ok ? "success" : "error"}">${result.html}</div>`;
          }
        });
      });
  } catch (error) {
    message(messageId, error.message);
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = "Laden";
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

document.querySelectorAll(".subtab").forEach((subtab) => {
  subtab.addEventListener("click", () => {
    const panel = subtab.closest(".panel");
    if (!panel) return;
    panel
      .querySelectorAll(".subtab, .subpanel")
      .forEach((item) => item.classList.remove("is-active"));
    subtab.classList.add("is-active");
    const target = panel.querySelector(`#${subtab.dataset.subtab}`);
    if (target) target.classList.add("is-active");
  });
});

function openBeheer(subtab = "beheer-ah") {
  document.querySelector('[data-tab="beheer"]').click();
  const button = document.querySelector(`[data-subtab="${subtab}"]`);
  if (button) button.click();
}

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
    await jsonFetch("/api/ah/auth", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code: token }),
    });
    $("#token-input").value = "";
    message("#token-message", "AH account gekoppeld en opgeslagen.", "success");
    await refreshAhStatus();
  } catch (error) {
    message("#token-message", error.message);
  }
});
$("#load-ah-lists").addEventListener("click", loadAhFavoriteLists);
$("#load-ah-recipes").addEventListener("click", (event) =>
  loadSavedRecipes("/api/ah/recipes/saved", "#ah-recipes", "#ah-recipes-message", event.currentTarget)
);
$("#load-jumbo-recipes").addEventListener("click", (event) =>
  loadSavedRecipes("/api/jumbo/recipes/saved", "#jumbo-recipes", "#jumbo-recipes-message", event.currentTarget)
);
$("#jumbo-login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const username = $("#jumbo-username").value.trim();
  const password = $("#jumbo-password").value;
  if (!username || !password) {
    message("#jumbo-status", "Vul je e-mailadres en wachtwoord in.");
    return;
  }
  const button = $("#jumbo-login");
  button.disabled = true;
  button.textContent = "Koppelen...";
  try {
    await jsonFetch("/api/jumbo/auth", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    $("#jumbo-password").value = "";
    await refreshJumboStatus();
  } catch (error) {
    message("#jumbo-status", error.message);
  } finally {
    button.disabled = false;
    button.textContent = "Koppelen";
  }
});
$("#jumbo-logout").addEventListener("click", async () => {
  try {
    await jsonFetch("/api/jumbo/auth/logout", { method: "POST", body: "{}" });
    $("#jumbo-username").value = "";
    $("#jumbo-password").value = "";
    await refreshJumboStatus();
  } catch (error) {
    message("#jumbo-status", error.message);
  }
});

const ahBrowserForm = $("#ah-browser-login-form");
if (ahBrowserForm) {
  ahBrowserForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const username = $("#ah-browser-username").value.trim();
    const password = $("#ah-browser-password").value;
    if (!username || !password) {
      message("#ah-browser-status", "E-mail en wachtwoord zijn verplicht");
      return;
    }
    const button = $("#ah-browser-login");
    button.disabled = true;
    const originalLabel = button.textContent;
    button.textContent = "Koppelen...";
    try {
      await jsonFetch("/api/ah/browser/auth", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      $("#ah-browser-password").value = "";
      await refreshAhBrowserStatus();
    } catch (error) {
      message("#ah-browser-status", error.message);
    } finally {
      button.disabled = false;
      button.textContent = originalLabel;
    }
  });
}
const ahBrowserLogout = $("#ah-browser-logout");
if (ahBrowserLogout) {
  ahBrowserLogout.addEventListener("click", async () => {
    try {
      await jsonFetch("/api/ah/browser/auth/logout", { method: "POST", body: "{}" });
      $("#ah-browser-username").value = "";
      $("#ah-browser-password").value = "";
      await refreshAhBrowserStatus();
    } catch (error) {
      message("#ah-browser-status", error.message);
    }
  });
}
$("#auth-primary").addEventListener("click", handleAuthPrimary);
$("#add-passkey").addEventListener("click", addPasskey);
$("#auth-logout").addEventListener("click", async () => {
  await jsonFetch("/api/auth/logout", { method: "POST", body: "{}" });
  await refreshAuthStatus();
});

const params = new URLSearchParams(window.location.search);
if (params.get("ah_refresh")) {
  openBeheer("beheer-ah");
  $("#token-input").value = params.get("ah_refresh");
  message("#token-message", "Refresh token ontvangen.", "success");
  history.replaceState({}, "", "/");
}
if (params.get("ah_connected")) {
  openBeheer("beheer-ah");
  message("#token-message", "AH account gekoppeld en opgeslagen.", "success");
  history.replaceState({}, "", "/");
}
if (params.get("ah_error")) {
  openBeheer("beheer-ah");
  message("#token-message", `AH redirect mislukt: ${params.get("ah_error")}`);
  history.replaceState({}, "", "/");
}

updateWeekLabel();
refreshAuthStatus().catch((error) => {
  message("#auth-message", error.message);
  $("#auth-gate").hidden = false;
});
