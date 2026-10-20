const state = {
  weekOffset: 0,
  entries: null,
  searchPage: 0,
  searchQuery: "",
  auth: null,
  credentials: [],
  ahListsLoaded: false,
  ahLists: [],
  ahListQuery: "",
  ahListHits: {},
  ahListsScroll: 0,
  ahOpenListId: null,
  ahRecipesLoaded: false,
  ahRecipes: [],
  ahRecipeQuery: "",
};

// Material Design Icons (zie static/icons/LICENSE-mdi.txt)
const MDI_SYNC =
  "M12,18A6,6 0 0,1 6,12C6,11 6.25,10.03 6.7,9.2L5.24,7.74C4.46,8.97 4,10.43 4,12A8,8 0 0,0 12,20V23L16,19L12,15M12,4V1L8,5L12,9V6A6,6 0 0,1 18,12C18,13 17.75,13.97 17.3,14.8L18.76,16.26C19.54,15.03 20,13.57 20,12A8,8 0 0,0 12,4Z";
const MDI_OPEN_IN_NEW =
  "M14,3V5H17.59L7.76,14.83L9.17,16.24L19,6.41V10H21V3M19,19H5V5H12V3H5C3.89,3 3,3.9 3,5V19A2,2 0 0,0 5,21H19A2,2 0 0,0 21,19V12H19V19Z";

function mdiIcon(path, className = "mdi") {
  return `<svg class="${className}" viewBox="0 0 24 24" aria-hidden="true"><path d="${path}"/></svg>`;
}

// Kleine letters zonder accenten, zodat "cafe" ook "café" vindt.
function foldText(value) {
  return String(value ?? "")
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase();
}

function matchesWords(text, query) {
  const words = foldText(query).split(/\s+/).filter(Boolean);
  const haystack = foldText(text);
  return words.length > 0 && words.every((word) => haystack.includes(word));
}

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
    const proxyHint =
      response.status === 502 || response.status === 504
        ? " Een reverse proxy kan het antwoord vervangen of te lang wachten; kijk in de container-logs voor de echte fout."
        : "";
    throw new Error(
      response.ok
        ? "Onverwacht antwoord van de server (geen geldige JSON)."
        : `Serverfout (${response.status}). Is de app bijgewerkt en ben je ingelogd?${proxyHint}`
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
    refreshMealieStatus();
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
                <div class="meal${entry.imageUrl ? " meal--with-image" : ""}">
                  ${
                    entry.imageUrl
                      ? `<img class="meal__image" src="${escapeHtml(entry.imageUrl)}" alt="" width="64" height="64" loading="lazy" data-hide-on-error>`
                      : ""
                  }
                  <div class="meal__text">
                    <span>${escapeHtml(mealLabels[entry.entryType] || entry.entryType)}</span>
                    <strong>${escapeHtml(entry.recipeDetail?.name || entry.recipe?.name || entry.title || "-")}</strong>
                    ${
                      entry.recipeDetail
                        ? `<small>${entry.recipeDetail.recipeIngredient.length} ingredienten</small>`
                        : ""
                    }
                  </div>
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
          <button class="button" id="add-cart">Voeg toe aan Albert Heijn mandje</button>
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

// Een lijniconenset met ronde uiteinden; nieuwe interface-iconen volgen deze vorm.
const ICON_EXTERNAL =
  '<svg class="icon" viewBox="0 0 16 16" aria-hidden="true"><path d="M6.5 3H4a1.5 1.5 0 0 0-1.5 1.5v7A1.5 1.5 0 0 0 4 13h7a1.5 1.5 0 0 0 1.5-1.5V9"/><path d="M9.5 2.5h4v4"/><path d="M13.5 2.5 7.5 8.5"/></svg>';

function formatPrice(value) {
  const price = Number(value || 0);
  return price ? `\u20ac${price.toFixed(2).replace(".", ",")}` : "";
}

function cartCandidateLabel(candidate) {
  return [candidate.title, candidate.unitSize, formatPrice(candidate.price)].filter(Boolean).join(" \u2013 ");
}

function openProductPopup(url) {
  // Een klein venster naast de lijst, zodat je het product kunt vergelijken zonder de lijst kwijt te raken.
  const popup = window.open(url, "ah-product", "popup=yes,width=1000,height=800,resizable=yes,scrollbars=yes");
  if (popup) {
    popup.opener = null;
    popup.focus();
  } else {
    window.open(url, "_blank", "noopener,noreferrer");
  }
}

function cartSelection(row, candidate, quantity) {
  return {
    query: row.query,
    productId: candidate.productId,
    quantity,
    title: candidate.title,
    price: candidate.price,
    unitSize: candidate.unitSize,
    image: candidate.image,
  };
}

function showCartResult(data, notFound) {
  const lines = (data.items || [])
    .map((item) => {
      const size = item.product.unitSize ? ` (${escapeHtml(item.product.unitSize)})` : "";
      return `<li>${escapeHtml(item.quantity)}x ${escapeHtml(item.product.title)}${size}</li>`;
    })
    .join("");
  const missing = notFound.length
    ? ` ${notFound.length} niet gevonden: ${escapeHtml(notFound.join(", "))}.`
    : "";
  $("#cart-result").innerHTML = `
    <div class="message success">Je boodschappen staan klaar: ${data.added} producten in je Albert Heijn mandje.${missing}</div>
    ${lines ? `<ul class="cart-lines">${lines}</ul>` : ""}
  `;
}

async function submitCart(selection, notFound) {
  const data = await jsonFetch("/api/ah/cart", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ items: selection }),
  });
  showCartResult(data, notFound);
}

function renderCartReview(rows) {
  const notFound = rows.filter((row) => !row.candidates.length).map((row) => row.display);
  const doubts = rows.filter((row) => row.candidates.length && row.candidates[0].doubt).length;
  $("#cart-result").innerHTML = `
    <div class="box cart-review">
      <div class="toolbar">
        <h2>Controleer je mandje</h2>
        <button class="button" id="confirm-cart"></button>
        <button class="button button--secondary" id="cancel-cart">Annuleren</button>
      </div>
      <p class="muted">${doubts} ${doubts === 1 ? "regel is" : "regels zijn"} gemarkeerd: daar ben ik niet zeker van. Kies het product en het aantal verpakkingen.</p>
      <div class="review-list">
        ${rows
          .map((row, index) => {
            if (!row.candidates.length) {
              return `
                <div class="review-row is-missing">
                  <input type="checkbox" disabled>
                  <div class="review-ingredient"><strong>${escapeHtml(row.display)}</strong></div>
                  <span class="muted">${row.error ? escapeHtml(row.error) : "Niet gevonden"}</span>
                </div>`;
            }
            const first = row.candidates[0];
            return `
              <div class="review-row${first.doubt ? " is-doubt" : ""}" data-row="${index}">
                <input type="checkbox" class="review-include" checked aria-label="Toevoegen">
                <div class="review-ingredient">
                  <strong>${escapeHtml(row.display)}</strong>
                  <small class="review-note">${escapeHtml(first.doubt || "")}</small>
                </div>
                <select class="review-product" aria-label="Product">
                  ${row.candidates
                    .map((candidate, at) => `<option value="${at}">${escapeHtml(cartCandidateLabel(candidate))}</option>`)
                    .join("")}
                </select>
                <a class="review-link" href="${escapeHtml(first.url)}" target="_blank" rel="noopener noreferrer" title="Bekijk dit product bij AH om te controleren of het klopt">Bekijk bij AH ${ICON_EXTERNAL}</a>
                <input class="review-qty" type="number" min="1" max="99" value="${first.packs}" aria-label="Aantal verpakkingen">
              </div>`;
          })
          .join("")}
      </div>
    </div>
  `;

  const rowElements = [...document.querySelectorAll(".review-row[data-row]")];
  const updateConfirm = () => {
    const count = rowElements.filter((el) => el.querySelector(".review-include").checked).length;
    const button = $("#confirm-cart");
    button.disabled = count === 0;
    button.textContent = `Voeg ${count} ${count === 1 ? "product" : "producten"} toe aan mandje`;
  };
  rowElements.forEach((el) => {
    const row = rows[Number(el.dataset.row)];
    el.querySelector(".review-product").addEventListener("change", (event) => {
      const candidate = row.candidates[Number(event.target.value)];
      el.querySelector(".review-qty").value = candidate.packs;
      el.querySelector(".review-link").href = candidate.url;
      el.querySelector(".review-note").textContent = candidate.doubt || "";
      el.classList.toggle("is-doubt", Boolean(candidate.doubt));
    });
    el.querySelector(".review-include").addEventListener("change", updateConfirm);
    el.querySelector(".review-link").addEventListener("click", (event) => {
      event.preventDefault();
      openProductPopup(event.currentTarget.href);
    });
  });
  updateConfirm();

  $("#cancel-cart").addEventListener("click", () => {
    $("#cart-result").innerHTML = "";
  });
  $("#confirm-cart").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    const selection = rowElements
      .filter((el) => el.querySelector(".review-include").checked)
      .map((el) => {
        const row = rows[Number(el.dataset.row)];
        const candidate = row.candidates[Number(el.querySelector(".review-product").value)];
        const quantity = Math.min(99, Math.max(1, parseInt(el.querySelector(".review-qty").value, 10) || 1));
        return cartSelection(row, candidate, quantity);
      });
    if (!selection.length) return;
    button.disabled = true;
    button.textContent = "Toevoegen...";
    try {
      await submitCart(selection, notFound);
    } catch (error) {
      $("#cart-result").insertAdjacentHTML(
        "afterbegin",
        `<div class="message error">${escapeHtml(error.message)}</div>`
      );
      updateConfirm();
    }
  });
}

async function addToCart() {
  const ingredients = aggregateIngredients(state.entries);
  if (!ingredients.length) return;
  const button = $("#add-cart");
  button.disabled = true;
  button.textContent = "Producten zoeken...";
  $("#cart-result").innerHTML = "";
  try {
    const preview = await jsonFetch("/api/ah/cart/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        items: ingredients.map((ingredient) => ({
          query: ingredient.food?.name || ingredient.display,
          display: ingredient.display || ingredient.food?.name || "",
          // Het aantal verpakkingen rekent de server uit met de verpakkingsgrootte van AH.
          quantity: ingredient.quantity ?? null,
          unit: ingredient.unit?.name || ingredient.unit?.abbreviation || null,
        })),
      }),
    });
    const rows = preview.items || [];
    const unsure = rows.some((row) => row.candidates.length && row.candidates[0].doubt);
    if (unsure) {
      renderCartReview(rows);
    } else {
      const selection = rows
        .filter((row) => row.candidates.length)
        .map((row) => cartSelection(row, row.candidates[0], row.candidates[0].packs));
      const notFound = rows.filter((row) => !row.candidates.length).map((row) => row.display);
      if (!selection.length) {
        $("#cart-result").innerHTML = `<div class="message error">Geen producten gevonden voor de ingredienten.</div>`;
      } else {
        await submitCart(selection, notFound);
      }
    }
  } catch (error) {
    $("#cart-result").innerHTML = `<div class="message error">${escapeHtml(error.message)}</div>`;
  } finally {
    button.disabled = false;
    button.textContent = "Voeg toe aan Albert Heijn mandje";
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
    const total = Number(data.total);
    $("#search-count").textContent = `${total.toLocaleString("nl-NL")} recepten gevonden${
      total > 2000 ? ", de eerste 2.000 zijn te bekijken" : ""
    }`;
    $("#recipe-results").innerHTML = (data.recipes || []).map(renderRecipeCard).join("");
    $("#prev-page").hidden = page === 0;
    $("#next-page").hidden = !(data.hasNextPage ?? total > (page + 1) * 12);
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
  const totalTime = Number(recipe.totalTime ?? (Number(recipe.cookTime || 0) + Number(recipe.preparationTime || 0)));
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

function renderMealieStatus(data, { verified = false } = {}) {
  const setup = $("#mealie-setup");
  if (setup) setup.hidden = Boolean(data.configured);
  $("#mealie-key-warning").hidden = !data.authKeyTiedToMealieEnv;
  $("#mealie-unlink").hidden = data.source !== "ui";
  const source = $("#mealie-source");
  source.hidden = data.source !== "env";
  source.textContent =
    data.source === "env"
      ? "Deze koppeling komt uit de omgevingsvariabelen MEALIE_URL en MEALIE_API_TOKEN. Een koppeling die je hier maakt gaat voor."
      : "";
  if (data.url && document.activeElement !== $("#mealie-url") && !$("#mealie-url").value) {
    $("#mealie-url").value = data.url;
  }
  if (!data.configured) {
    message("#mealie-status", "Mealie is nog niet gekoppeld.", "error");
  } else if (verified && data.connected) {
    const details = [data.version ? `versie ${data.version}` : "", data.username ? `gebruiker ${data.username}` : ""]
      .filter(Boolean)
      .join(", ");
    message("#mealie-status", `Mealie werkt: ${data.url}${details ? ` (${details})` : ""}.`, "success");
  } else if (verified) {
    message("#mealie-status", data.error || "Mealie reageert niet zoals verwacht.", "error");
  } else {
    message("#mealie-status", `Mealie gekoppeld: ${data.url}`, "success");
  }
}

async function refreshMealieStatus() {
  try {
    renderMealieStatus(await jsonFetch("/api/mealie/status"));
  } catch (error) {
    message("#mealie-status", error.message);
  }
}

async function checkMealieConnection() {
  message("#mealie-status", "Koppeling controleren...", "success");
  try {
    renderMealieStatus(await jsonFetch("/api/mealie/status?verify=1"), { verified: true });
  } catch (error) {
    message("#mealie-status", error.message);
  }
}

async function linkMealie(event) {
  event.preventDefault();
  const url = $("#mealie-url").value.trim();
  const token = $("#mealie-token").value.trim();
  if (!url || !token) {
    message("#mealie-status", "Vul het adres en de API-token in.");
    return;
  }
  const button = $("#mealie-link");
  button.disabled = true;
  button.textContent = "Controleren...";
  try {
    const data = await jsonFetch("/api/mealie/auth", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, token }),
    });
    $("#mealie-token").value = "";
    $("#mealie-url").value = data.url;
    renderMealieStatus(data, { verified: true });
  } catch (error) {
    message("#mealie-status", error.message);
  } finally {
    button.disabled = false;
    button.textContent = "Koppelen";
  }
}

async function unlinkMealie() {
  try {
    const data = await jsonFetch("/api/mealie/auth/logout", { method: "POST", body: "{}" });
    $("#mealie-token").value = "";
    $("#mealie-url").value = "";
    renderMealieStatus(data);
  } catch (error) {
    message("#mealie-status", error.message);
  }
}

async function checkAhConnection() {
  message("#ah-status", "Koppeling controleren...", "success");
  try {
    const data = await jsonFetch("/api/ah/auth/status?verify=1");
    if (data.connected) {
      message("#ah-status", "AH account gekoppeld en werkt.", "success");
    } else {
      message(
        "#ah-status",
        data.error || "AH account nog niet gekoppeld, of de koppeling is verlopen. Log opnieuw in.",
        "error"
      );
    }
  } catch (error) {
    message("#ah-status", error.message);
  }
}

function formatSyncTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString("nl-NL", { dateStyle: "medium", timeStyle: "short" });
}

function ahListStatus(list) {
  const total = Number(list.itemCount || 0);
  const count = `${total.toLocaleString("nl-NL")} ${total === 1 ? "item" : "items"}`;
  if (!list.synced) return `${count} · niet gesynchroniseerd`;
  const when = formatSyncTime(list.syncedAt);
  const stale =
    list.source === "both" && list.syncedCount !== null && list.syncedCount !== list.itemCount
      ? " · gewijzigd bij AH"
      : "";
  const gone = list.source === "local" ? " · alleen lokale kopie" : "";
  return `${count} · gesynchroniseerd${when ? ` op ${when}` : ""}${stale}${gone}`;
}

// Korte regels voor op een kaart; de volledige tekst (ahListStatus) staat in de tooltip.
function ahListCardLines(list) {
  const total = Number(list.itemCount || 0);
  const lines = [`${total.toLocaleString("nl-NL")} ${total === 1 ? "item" : "items"}`];
  if (!list.synced) {
    lines.push("Niet gesynchroniseerd");
    return lines;
  }
  const date = list.syncedAt ? new Date(list.syncedAt) : null;
  const day =
    date && !Number.isNaN(date.getTime()) ? date.toLocaleDateString("nl-NL", { day: "numeric", month: "short" }) : "";
  lines.push(day ? `Gesynchroniseerd ${day}` : "Gesynchroniseerd");
  if (list.source === "local") lines.push("Alleen lokale kopie");
  else if (list.syncedCount !== null && list.syncedCount !== list.itemCount) lines.push("Gewijzigd bij AH");
  return lines;
}

function visibleAhLists() {
  const query = state.ahListQuery.trim();
  if (!query) return state.ahLists;
  return state.ahLists.filter((list) => matchesWords(list.name, query) || state.ahListHits[list.id]);
}

function renderAhLists() {
  const lists = visibleAhLists();
  const query = state.ahListQuery.trim();
  let empty = `<p class="muted">Geen AH lijstjes gevonden.</p>`;
  if (query && state.ahLists.length) {
    empty = `<p class="muted">Geen lijstjes gevonden met &ldquo;${escapeHtml(query)}&rdquo;.</p>`;
  }
  $("#ah-lists").innerHTML = lists.length
    ? lists
        .map((list) => {
          const hits = query ? state.ahListHits[list.id] || [] : [];
          const syncLabel = list.synced ? "Opnieuw synchroniseren" : "Synchroniseren";
          return `
            <div class="list-card">
              <button type="button" class="list-card__open" data-ah-list-id="${escapeHtml(list.id)}" title="${escapeHtml(ahListStatus(list))}">
                <strong>${escapeHtml(list.name || "-")}</strong>
                ${ahListCardLines(list).map((line) => `<small>${escapeHtml(line)}</small>`).join("")}
                ${hits.length ? `<small class="list-card__hit">Bevat: ${escapeHtml(hits.join(", "))}</small>` : ""}
              </button>
              ${
                list.source === "local"
                  ? ""
                  : `<button type="button" class="icon-button list-card__sync" data-ah-sync-id="${escapeHtml(list.id)}" aria-label="${syncLabel}: ${escapeHtml(list.name || "")}" title="${syncLabel}">${mdiIcon(MDI_SYNC)}</button>`
              }
            </div>
          `;
        })
        .join("")
    : empty;
}

let ahListSearchTimer = null;
let ahListSearchSeq = 0;

async function searchAhListContents() {
  const query = state.ahListQuery.trim();
  const seq = ++ahListSearchSeq;
  if (!query) {
    state.ahListHits = {};
    renderAhLists();
    return;
  }
  try {
    const data = await jsonFetch(`/api/ah/lists/search?q=${encodeURIComponent(query)}`);
    if (seq !== ahListSearchSeq) return; // een nieuwere toetsaanslag is al onderweg
    state.ahListHits = data.items || {};
  } catch {
    if (seq !== ahListSearchSeq) return;
    state.ahListHits = {}; // zoeken op naam blijft werken
  }
  renderAhLists();
}

function onAhListFilter(event) {
  state.ahListQuery = event.target.value;
  // Treffers horen bij de vorige zoektekst; weg ermee tot de nieuwe binnen zijn.
  state.ahListHits = {};
  ahListSearchSeq++;
  renderAhLists(); // meteen op naam filteren
  clearTimeout(ahListSearchTimer);
  ahListSearchTimer = setTimeout(searchAhListContents, 200);
}

async function loadAhFavoriteLists() {
  const button = $("#load-ah-lists");
  button.disabled = true;
  button.textContent = "Laden...";
  try {
    const data = await jsonFetch("/api/ah/lists");
    state.ahLists = data.lists || [];
    renderAhLists();
    if (state.ahListQuery.trim()) searchAhListContents();
    // Lokale kopieen blijven zichtbaar; meld wel waarom de live lijst ontbreekt.
    const hasLocal = state.ahLists.some((list) => list.synced);
    message(
      "#ah-lists-warning",
      data.ahError
        ? `AH is niet bereikbaar${hasLocal ? ", je ziet de gesynchroniseerde kopieen" : ""}: ${data.ahError}`
        : ""
    );
  } catch (error) {
    message("#ah-lists", error.message);
  } finally {
    button.disabled = false;
    button.textContent = "Laden";
  }
}

function setBusy(button, busy, busyText) {
  if (!button) return;
  button.disabled = busy;
  button.classList.toggle("is-busy", busy);
  if (busyText && !button.querySelector("svg")) {
    if (busy) button.dataset.label = button.textContent;
    button.textContent = busy ? busyText : button.dataset.label || button.textContent;
  }
}

async function syncAhList(listId, button) {
  setBusy(button, true, "Synchroniseren...");
  try {
    const data = await jsonFetch(`/api/ah/lists/${encodeURIComponent(listId)}/sync`, { method: "POST" });
    return { ok: true, data };
  } catch (error) {
    return { ok: false, error: error.message };
  } finally {
    setBusy(button, false, "Synchroniseren...");
  }
}

async function syncOneAndReport(listId, button) {
  const result = await syncAhList(listId, button);
  await loadAhFavoriteLists();
  const outcome = result.ok
    ? [`"${result.data.name}" gesynchroniseerd (${result.data.totalSize} items).`, "success"]
    : [`Synchroniseren mislukt: ${result.error}`, "error"];
  if (state.ahOpenListId === listId) {
    // De lijst staat open: ververs hem op dezelfde plek en meld het resultaat daar.
    await loadAhFavoriteListItems(listId, { keepScroll: true });
    message("#ah-list-items-message", ...outcome);
  } else {
    message("#ah-lists-warning", ...outcome);
  }
}

async function syncAllAhLists() {
  const button = $("#sync-all-ah-lists");
  button.disabled = true;
  button.textContent = "Synchroniseren...";
  try {
    const data = await jsonFetch("/api/ah/lists");
    const lists = (data.lists || []).filter((list) => list.source !== "local");
    if (!lists.length) {
      message(
        "#ah-lists-warning",
        data.ahError ? `AH is niet bereikbaar: ${data.ahError}` : "Geen AH lijstjes om te synchroniseren."
      );
      return;
    }
    const failures = [];
    for (const list of lists) {
      const result = await syncAhList(list.id, null);
      if (!result.ok) failures.push(`${list.name}: ${result.error}`);
    }
    await loadAhFavoriteLists();
    message(
      "#ah-lists-warning",
      failures.length
        ? `${lists.length - failures.length} van ${lists.length} gesynchroniseerd. Mislukt: ${failures.join("; ")}`
        : `${lists.length} lijstjes gesynchroniseerd.`,
      failures.length ? "error" : "success"
    );
  } catch (error) {
    message("#ah-lists-warning", error.message);
  } finally {
    button.disabled = false;
    button.textContent = "Alles synchroniseren";
  }
}

// Een lijstje opent op dezelfde plek als het overzicht; de pijl (of de terugknop van de telefoon) gaat terug.
function showAhListsOverview({ restoreScroll = true } = {}) {
  state.ahOpenListId = null;
  $("#ah-list-view").hidden = true;
  $("#ah-lists-view").hidden = false;
  if (restoreScroll) window.scrollTo({ top: state.ahListsScroll });
}

async function openAhList(listId, { push = true } = {}) {
  if (!state.ahOpenListId) state.ahListsScroll = window.scrollY;
  state.ahOpenListId = listId;
  if (push) history.pushState({ ahList: listId }, "");
  const known = state.ahLists.find((list) => list.id === listId);
  $("#ah-list-title").textContent = known?.name || "AH lijst";
  $("#ah-list-meta").textContent = "";
  $("#ah-list-items").innerHTML = "";
  $("#ah-lists-view").hidden = true;
  $("#ah-list-view").hidden = false;
  window.scrollTo({ top: 0 });
  await loadAhFavoriteListItems(listId);
  $("#ah-list-title").focus({ preventScroll: true });
}

function closeAhList() {
  if (history.state?.ahList) history.back();
  else showAhListsOverview();
}

async function loadAhFavoriteListItems(listId, { keepScroll = false } = {}) {
  const scroll = window.scrollY;
  message("#ah-list-items-message", "");
  $("#ah-list-items").innerHTML = `<div class="message success">Lijst laden...</div>`;
  try {
    const data = await jsonFetch(`/api/ah/lists/${encodeURIComponent(listId)}`);
    if (state.ahOpenListId !== listId) return; // inmiddels teruggegaan of een ander lijstje geopend
    const items = data.items || [];
    const synced = data.source === "local";
    $("#ah-list-title").textContent = data.name || "AH lijst";
    $("#sync-open-ah-list").textContent = synced ? "Opnieuw synchroniseren" : "Synchroniseren";
    $("#ah-list-meta").textContent = `${items.length.toLocaleString("nl-NL")} producten · ${
      synced ? `lokale kopie van ${formatSyncTime(data.syncedAt)}` : "live van AH, nog niet gesynchroniseerd"
    }`;
    $("#ah-list-items").innerHTML = `<div class="product-grid">${items.map(renderAhListItem).join("")}</div>`;
    if (keepScroll) window.scrollTo({ top: scroll });
  } catch (error) {
    if (state.ahOpenListId !== listId) return;
    $("#ah-list-items").innerHTML = "";
    message("#ah-list-items-message", error.message);
  }
}

function renderAhListItem(item) {
  const product = item.product || {};
  const title = product.title || `Product ${item.productId}`;
  const price = Number(product.price || 0);
  const url = item.url || product.url || "";
  const meta = [product.brand, product.unitSize].filter(Boolean).join(" · ");
  const body = `
    ${product.image ? `<img src="${escapeHtml(product.image)}" alt="" loading="lazy">` : ""}
    <div>
      <strong>${escapeHtml(item.quantity)}x ${escapeHtml(title)}</strong>
      ${meta ? `<small>${escapeHtml(meta)}</small>` : ""}
      ${product.isBonus ? `<small class="bonus">Bonus</small>` : ""}
      ${price ? `<small>&euro;${price.toFixed(2).replace(".", ",")}</small>` : ""}
    </div>
  `;
  return url
    ? `<a class="product-card" href="${escapeHtml(url)}" target="_blank" rel="noreferrer noopener">${body}</a>`
    : `<article class="product-card">${body}</article>`;
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
        ${
          url
            ? `<a class="card-link" href="${escapeHtml(url)}" target="_blank" rel="noreferrer noopener" aria-label="Open recept bij AH: ${escapeHtml(recipe.title || "")}">${mdiIcon(MDI_OPEN_IN_NEW)}<span>Open bij AH</span></a>`
            : ""
        }
      </div>
      <div class="recipe-import-result"></div>
    </article>
  `;
}

function renderSavedRecipes() {
  const query = state.ahRecipeQuery.trim();
  const all = state.ahRecipes;
  const recipes = query ? all.filter((recipe) => matchesWords(recipe.title, query)) : all;
  $("#ah-recipes").innerHTML = recipes.length
    ? recipes.map(renderSavedRecipeCard).join("")
    : "";
  if (!all.length) return;
  message(
    "#ah-recipes-message",
    recipes.length ? "" : `Geen recepten gevonden met "${query}".`,
    "success"
  );
}

function updateSavedRecipesStatus(data) {
  const total = Number(data.total ?? state.ahRecipes.length);
  const when = formatSyncTime(data.syncedAt);
  $("#ah-recipes-status").textContent =
    `${total.toLocaleString("nl-NL")} ${total === 1 ? "recept" : "recepten"}` +
    (when ? ` · bewaard in MealieShopper op ${when}` : "") +
    ` · Vernieuwen haalt ze opnieuw bij AH op.`;
}

// `cachedOnly`: alleen de lokale kopie tonen, nooit AH vragen (zo opent het tabblad zonder AH-verkeer).
async function loadSavedRecipes({ refresh = false, cachedOnly = false } = {}) {
  const button = $("#load-ah-recipes");
  state.ahRecipesLoaded = true;
  setBusy(button, true, "Laden...");
  message("#ah-recipes-message", "");
  try {
    const query = refresh ? "?refresh=1" : cachedOnly ? "?cached=1" : "";
    const data = await jsonFetch(`/api/ah/recipes/saved${query}`);
    state.ahRecipes = data.recipes || [];
    $("#ah-recipe-filter").disabled = !state.ahRecipes.length;
    button.dataset.label = data.syncedAt ? "Vernieuwen" : "Ophalen bij AH";
    if (!state.ahRecipes.length) {
      $("#ah-recipes").innerHTML = "";
      if (data.source === "none") {
        $("#ah-recipes-status").textContent =
          "Je bewaarde recepten zijn nog niet opgehaald. Kies Ophalen bij AH; ze worden daarna in MealieShopper bewaard.";
      } else {
        message("#ah-recipes-message", "Geen bewaarde recepten gevonden.", "success");
      }
      return;
    }
    updateSavedRecipesStatus(data);
    renderSavedRecipes();
    if (data.ahError) {
      message(
        "#ah-recipes-message",
        `AH is niet bereikbaar, je ziet de bewaarde kopie: ${data.ahError}`,
        "error"
      );
    }
  } catch (error) {
    message("#ah-recipes-message", error.message);
  } finally {
    setBusy(button, false, "Laden...");
  }
}

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab, .panel").forEach((item) => item.classList.remove("is-active"));
    document.querySelectorAll(".tab").forEach((item) => item.removeAttribute("aria-current"));
    tab.classList.add("is-active");
    tab.setAttribute("aria-current", "page");
    $(`#${tab.dataset.tab}`).classList.add("is-active");
    // Zoals een native tabbalk: een andere tab begint bovenaan.
    window.scrollTo({ top: 0 });
    if (tab.dataset.tab === "ah-items" && !state.ahRecipesLoaded) loadSavedRecipes({ cachedOnly: true });
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
    if (subtab.dataset.subtab === "ah-items-lijstjes" && !state.ahListsLoaded) {
      state.ahListsLoaded = true;
      loadAhFavoriteLists();
    }
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
$("#check-ah").addEventListener("click", checkAhConnection);
$("#mealie-form").addEventListener("submit", linkMealie);
$("#check-mealie").addEventListener("click", checkMealieConnection);
$("#mealie-unlink").addEventListener("click", unlinkMealie);
$("#open-mealie-setup").addEventListener("click", () => openBeheer("beheer-mealie"));
$("#load-ah-lists").addEventListener("click", loadAhFavoriteLists);
$("#sync-all-ah-lists").addEventListener("click", syncAllAhLists);
$("#load-ah-recipes").addEventListener("click", () => loadSavedRecipes({ refresh: true }));
$("#ah-recipe-filter").addEventListener("input", (event) => {
  state.ahRecipeQuery = event.target.value;
  renderSavedRecipes();
});
$("#ah-list-filter").addEventListener("input", onAhListFilter);
$("#ah-list-back").addEventListener("click", closeAhList);
$("#sync-open-ah-list").addEventListener("click", (event) =>
  syncOneAndReport(state.ahOpenListId, event.currentTarget)
);
$("#ah-lists").addEventListener("click", (event) => {
  const sync = event.target.closest("[data-ah-sync-id]");
  if (sync) return syncOneAndReport(sync.dataset.ahSyncId, sync);
  const open = event.target.closest("[data-ah-list-id]");
  if (open) openAhList(open.dataset.ahListId);
});
$("#ah-recipes").addEventListener("click", async (event) => {
  const btn = event.target.closest("[data-import-url]");
  if (!btn) return;
  const result = await importUrl(btn.dataset.importUrl, btn);
  const target = btn.closest(".recipe-card")?.querySelector(".recipe-import-result");
  if (target) target.innerHTML = `<div class="message ${result.ok ? "success" : "error"}">${result.html}</div>`;
});
// De browser zou bij terug zelf een scrollstand kiezen; wij zetten hem terug op de plek in het overzicht.
if ("scrollRestoration" in history) history.scrollRestoration = "manual";
window.addEventListener("popstate", (event) => {
  if (event.state?.ahList) openAhList(event.state.ahList, { push: false });
  else if (state.ahOpenListId) showAhListsOverview();
});
// Een plaatje dat niet laadt (bijvoorbeeld een recept zonder afbeelding in Mealie) verdwijnt netjes.
document.addEventListener(
  "error",
  (event) => {
    if (event.target instanceof HTMLImageElement && event.target.hasAttribute("data-hide-on-error")) {
      event.target.closest(".meal")?.classList.remove("meal--with-image");
      event.target.remove();
    }
  },
  true
);
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
