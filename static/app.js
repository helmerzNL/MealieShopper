const state = {
  weekOffset: 0,
  entries: null,
  searchPage: 0,
  searchQuery: "",
  auth: null,
  credentials: [],
  ahListsLoaded: false,
  loginTicket: null,
  onboarding: null,
  ahLists: [],
  ahListQuery: "",
  ahListHits: {},
  ahListsScroll: 0,
  ahOpenListId: null,
  ahOpenList: null,
  ahRecipesLoaded: false,
  ahRecipes: [],
  ahRecipeQuery: "",
  about: null,
  aboutLoaded: false,
  store: "ah", // de keuze voor het weekmenu: "ah", "picnic", "jumbo" (mandje) of "jumbo-list" (lijstje)
  itemsStore: "ah", // de winkel in de tab Winkels: "ah" of "jumbo"
  preferredStore: "ah", // de laatste keuze van de gebruiker; geldt zodra die winkel als gekoppeld bekend is
  linkedStores: { picnic: false, jumbo: false }, // gezet door picnic.js en jumbo.js; AH is altijd een keuze
};

// Material Design Icons (zie static/icons/LICENSE-mdi.txt)
const MDI_SYNC =
  "M12,18A6,6 0 0,1 6,12C6,11 6.25,10.03 6.7,9.2L5.24,7.74C4.46,8.97 4,10.43 4,12A8,8 0 0,0 12,20V23L16,19L12,15M12,4V1L8,5L12,9V6A6,6 0 0,1 18,12C18,13 17.75,13.97 17.3,14.8L18.76,16.26C19.54,15.03 20,13.57 20,12A8,8 0 0,0 12,4Z";
const MDI_PLUS = "M19,13H13V19H11V13H5V11H11V5H13V11H19V13Z";
const MDI_MINUS = "M19,13H5V11H19V13Z";
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

// Korte dag- en maandnamen in de gekozen taal ("ma", "okt" / "Mon", "Oct").
function weekdayShort(date) {
  return date.toLocaleDateString(I18N.locale(), { weekday: "short" }).replace(/\.$/, "");
}

function monthShort(date) {
  return date.toLocaleDateString(I18N.locale(), { month: "short" }).replace(/\.$/, "");
}

function mealLabel(type) {
  const key = `meal.${type}`;
  return I18N.messages.nl[key] ? t(key) : type;
}

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
        ? t("err.proxy_hint")
        : "";
    throw new Error(
      response.ok
        ? t("err.bad_json")
        : t("err.server", { status: response.status, hint: proxyHint })
    );
  }
  if (!response.ok) {
    throw new Error(data.error || t("err.generic"));
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
    return t("passkey.unsupported");
  }
  const secure = window.isSecureContext || ["localhost", "127.0.0.1", "::1"].includes(window.location.hostname);
  if (!secure) {
    return t("passkey.needs_https");
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
  const auth = state.auth || {};
  const onboardingBusy = typeof onboardingVisible === "function" && onboardingVisible();
  const authenticated = auth.authenticated || !auth.enabled;
  // Een nieuwe installatie (nog niets ingesteld) gaat via de installatiehulp, niet via deze inlogpagina.
  const inviteOpen = Boolean(state.invite);
  const showGate = !authenticated && !auth.setupRequired && !onboardingBusy && !inviteOpen;

  gate.hidden = !showGate;
  chip.hidden = !authenticated || !auth.enabled;
  $("#auth-chip-text").textContent = authenticated && auth.username ? t("auth.chip", { name: auth.username }) : "";

  if (showGate) {
    const methods = auth.methods || {};
    const mfaStep = Boolean(state.loginTicket);
    $("#login-form").hidden = !methods.password || mfaStep;
    $("#mfa-form").hidden = !mfaStep;
    $("#login-passkey-box").hidden = !methods.passkey || mfaStep;
    $("#login-or").hidden = !(methods.passkey && methods.password);
    $("#auth-title").textContent = mfaStep ? t("gate.title.mfa") : t("gate.title.login");
    $("#auth-description").textContent = mfaStep
      ? t("gate.desc.mfa")
      : methods.password && methods.passkey
        ? t("gate.desc.both")
        : methods.password
          ? t("gate.desc.password")
          : t("gate.desc.passkey");
    const unavailable = webauthnUnavailableReason();
    $("#login-passkey").disabled = Boolean(unavailable);
    if (unavailable && methods.passkey && !methods.password) message("#auth-message", unavailable);
  }
  if (typeof renderOnboarding === "function") renderOnboarding();
  if (typeof renderSecurityPanel === "function") renderSecurityPanel();
  if (typeof renderInviteGate === "function") renderInviteGate();
  if (typeof applyRole === "function") applyRole();
  if (typeof renderUpdate === "function") renderUpdate();
}

function formatCredentialDate(value) {
  if (!value) return "-";
  return String(value).slice(0, 19).replace("T", " ");
}

function renderCredentials() {
  const target = $("#credentials-list");
  if (!target) return;
  if (!state.auth?.authenticated || !state.auth?.enabled) {
    target.innerHTML = `<p class="muted">${
      state.auth?.enabled ? t("sec.passkeys.login_required") : t("sec.passkeys.enable_login")
    }</p>`;
    return;
  }
  target.innerHTML = state.credentials.length
    ? state.credentials
        .map(
          (credential) => `
            <div class="credential-row">
              <div>
                <strong>${escapeHtml(credential.credential_name || t("passkey.default_name"))}</strong>
                <small>${th("passkey.created", { user: credential.username || "admin", date: formatCredentialDate(credential.created_at) })}${
                  credential.last_used_at ? th("passkey.last_used", { date: formatCredentialDate(credential.last_used_at) }) : ""
                }</small>
              </div>
              <button class="button button--secondary" data-delete-passkey="${escapeHtml(credential.id)}" ${
                state.credentials.length <= 1 ? "disabled" : ""
              }>${escapeHtml(t("passkey.delete"))}</button>
            </div>
          `
        )
        .join("")
    : `<p class="muted">${escapeHtml(t("passkey.none"))}</p>`;
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

// Een AI-app stuurde je naar de inlog (/?next=/oauth/authorize?...): na het inloggen ga je terug naar het
// toestemmingsscherm. Alleen een adres van die ene pagina is toegestaan, dus nooit een andere site of pagina.
function continueAfterLogin() {
  if (!state.auth?.authenticated) return false;
  const next = new URLSearchParams(window.location.search).get("next") || "";
  if (!/^\/oauth\/authorize\?/.test(next)) return false;
  window.location.replace(next);
  return true;
}

async function refreshAuthStatus() {
  state.auth = await jsonFetch("/api/auth/status");
  if (typeof loadOnboardingStatus === "function") await loadOnboardingStatus();
  renderAuthState();
  if (continueAfterLogin()) return state.auth;
  await loadCredentials();
  if (state.auth?.authenticated || !state.auth?.enabled) {
    refreshAhStatus();
    refreshMealieStatus();
    if (typeof refreshPicnicStatus === "function") refreshPicnicStatus();
    if (typeof refreshJumboStatus === "function") refreshJumboStatus();
    if (typeof startUpdateCheck === "function") startUpdateCheck();
  }
  return state.auth;
}

async function registerPasskey({ username, credentialName, messageTarget, invite }) {
  message(messageTarget, t("passkey.waiting"), "success");
  const payload = await jsonFetch("/api/auth/register/options", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, credentialName, invite }),
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
      invite,
      credential: publicKeyCredentialToJSON(credential),
    }),
  });
}

async function addPasskey() {
  const button = $("#add-passkey");
  const credentialName = $("#new-passkey-name").value.trim() || t("passkey.extra_name");
  const username = state.auth?.username || "admin";
  button.disabled = true;
  try {
    await registerPasskey({ username, credentialName, messageTarget: "#security-message" });
    message("#security-message", t("passkey.added"), "success");
    await refreshAuthStatus();
  } catch (error) {
    message("#security-message", error.name === "NotAllowedError" ? t("passkey.cancelled") : error.message);
  } finally {
    button.disabled = false;
  }
}

async function deletePasskey(credentialId) {
  if (!window.confirm(t("passkey.confirm_delete"))) return;
  try {
    await jsonFetch(`/api/auth/credentials/${encodeURIComponent(credentialId)}`, { method: "DELETE" });
    message("#security-message", t("passkey.deleted"), "success");
    await refreshAuthStatus();
  } catch (error) {
    message("#security-message", error.message);
  }
}

async function loginWithPasskey() {
  message("#auth-message", t("passkey.waiting"), "success");
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

async function loginWithPassword(event) {
  event.preventDefault();
  const button = $("#login-submit");
  button.disabled = true;
  message("#auth-message", "");
  try {
    const data = await jsonFetch("/api/auth/password/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: $("#login-username").value, password: $("#login-password").value }),
    });
    $("#login-password").value = "";
    if (data.mfaRequired) {
      state.loginTicket = data.ticket;
      renderAuthState();
      $("#mfa-code").value = "";
      $("#mfa-code").focus();
      return;
    }
    await refreshAuthStatus();
  } catch (error) {
    message("#auth-message", error.message);
  } finally {
    button.disabled = false;
  }
}

async function submitMfaCode(event) {
  event.preventDefault();
  const button = $("#mfa-submit");
  button.disabled = true;
  message("#auth-message", "");
  try {
    await jsonFetch("/api/auth/password/mfa", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ticket: state.loginTicket, code: $("#mfa-code").value }),
    });
    state.loginTicket = null;
    $("#mfa-code").value = "";
    await refreshAuthStatus();
  } catch (error) {
    message("#auth-message", error.message);
    if (/verlopen/i.test(error.message)) cancelMfa();
  } finally {
    button.disabled = false;
  }
}

function cancelMfa() {
  state.loginTicket = null;
  $("#mfa-code").value = "";
  renderAuthState();
}

function toggleRecoveryInput() {
  const input = $("#mfa-code");
  const recovery = input.dataset.mode !== "recovery";
  input.dataset.mode = recovery ? "recovery" : "code";
  input.inputMode = recovery ? "text" : "numeric";
  input.autocomplete = recovery ? "off" : "one-time-code";
  input.placeholder = recovery ? "XXXX-XXXX-XXXX-XXXX" : "123456";
  $("#mfa-code-label").textContent = recovery ? t("mfa.recovery_label") : t("mfa.code_label");
  $("#mfa-recovery-toggle").textContent = recovery ? t("mfa.use_app") : t("mfa.use_recovery");
  input.value = "";
  input.focus();
}

async function handlePasskeyLogin() {
  const button = $("#login-passkey");
  button.disabled = true;
  try {
    await loginWithPasskey();
  } catch (error) {
    message("#auth-message", error.name === "NotAllowedError" ? t("passkey.cancelled") : error.message);
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
  const label = (d) => `${d.getDate()} ${monthShort(d)}`;
  return { start: iso(monday), end: iso(sunday), label: `${label(monday)} - ${label(sunday)}` };
}

function updateWeekLabel() {
  const week = getWeekBounds(state.weekOffset);
  $("#week-range").textContent = week.label;
  $("#week-offset").textContent =
    state.weekOffset === 0
      ? t("week.this")
      : state.weekOffset === 1
        ? t("week.next")
        : state.weekOffset === -1
          ? t("week.prev")
          : t("week.weeks", { n: `${state.weekOffset > 0 ? "+" : ""}${state.weekOffset}` });
}

function formatDate(dateStr) {
  const date = new Date(`${dateStr}T00:00:00`);
  return `${weekdayShort(date)} ${date.getDate()} ${monthShort(date)}`;
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
                    <span>${escapeHtml(mealLabel(entry.entryType))}</span>
                    <strong>${escapeHtml(entry.recipeDetail?.name || entry.recipe?.name || entry.title || "-")}</strong>
                    ${
                      entry.recipeDetail
                        ? `<small>${escapeHtml(tn("plan.ingredients_count", entry.recipeDetail.recipeIngredient.length))}</small>`
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
    message("#planner-message", t("plan.none_planned"), "success");
    return;
  }
  $("#ingredients-panel").innerHTML = ingredients.length
    ? `
      <div class="box ingredients">
        <div class="toolbar">
          <h2>${escapeHtml(t("plan.all_ingredients", { count: I18N.formatNumber(ingredients.length) }))}</h2>
          <span id="cart-store-slot"></span>
          <button class="button" id="add-cart">${escapeHtml(cartButtonLabel())}</button>
        </div>
        <ul>
          ${ingredients.map((item) => `<li>${escapeHtml(item.display || item.food?.name || "-")}</li>`).join("")}
        </ul>
      </div>
    `
    : "";
  $("#add-cart")?.addEventListener("click", addToCart);
  renderStoreChoice();
}

async function loadMealPlan() {
  const button = $("#load-plan");
  const week = getWeekBounds(state.weekOffset);
  button.disabled = true;
  button.textContent = t("plan.loading");
  message("#planner-message", "");
  $("#cart-result").innerHTML = "";
  try {
    state.entries = await jsonFetch(`/api/mealplan?start=${week.start}&end=${week.end}`);
    renderMealPlan(state.entries);
  } catch (error) {
    message("#planner-message", error.message);
  } finally {
    button.disabled = false;
    button.textContent = t("plan.load");
  }
}

// Een lijniconenset met ronde uiteinden; nieuwe interface-iconen volgen deze vorm.
const ICON_EXTERNAL =
  '<svg class="icon" viewBox="0 0 16 16" aria-hidden="true"><path d="M6.5 3H4a1.5 1.5 0 0 0-1.5 1.5v7A1.5 1.5 0 0 0 4 13h7a1.5 1.5 0 0 0 1.5-1.5V9"/><path d="M9.5 2.5h4v4"/><path d="M13.5 2.5 7.5 8.5"/></svg>';

function formatPrice(value) {
  const price = Number(value || 0);
  return price ? `\u20ac${price.toLocaleString(I18N.locale(), { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : "";
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

const STORES = {
  ah: { name: "Albert Heijn", api: "ah", linked: "ah", button: "plan.add_cart", ready: "cart.ready" },
  picnic: { name: "Picnic", api: "picnic", linked: "picnic", button: "plan.add_cart_picnic", ready: "cart.ready_picnic" },
  jumbo: { name: "Jumbo", api: "jumbo", linked: "jumbo", button: "plan.add_cart_jumbo", ready: "cart.ready_jumbo" },
  // Hetzelfde Jumbo-account, maar de ingrediënten gaan op het boodschappenlijstje in plaats van in het mandje.
  "jumbo-list": { name: "Jumbo (boodschappenlijstje)", api: "jumbo", linked: "jumbo", cart: "list", button: "plan.add_cart_jumbo_list", ready: "cart.ready_jumbo_list" },
};

function currentStore() {
  return STORES[state.store] || STORES.ah;
}

function cartButtonLabel() {
  return t(currentStore().button);
}

// Supermarkt kiezen in de planner: alleen zichtbaar zodra een tweede winkel gekoppeld is.
function renderStoreChoice() {
  const available = Object.keys(STORES).filter((key) => key === "ah" || state.linkedStores[STORES[key].linked]);
  state.store = available.includes(state.preferredStore) ? state.preferredStore : "ah";
  const slot = $("#cart-store-slot");
  if (slot) {
    slot.innerHTML =
      available.length < 2
        ? ""
        : `<select id="cart-store" class="store-select" aria-label="${escapeHtml(t("cart.store"))}">
            ${available.map((key) => `<option value="${key}"${state.store === key ? " selected" : ""}>${STORES[key].name}</option>`).join("")}
          </select>`;
    $("#cart-store")?.addEventListener("change", (event) => {
      state.store = event.target.value;
      state.preferredStore = state.store;
      try {
        localStorage.setItem("ms-store", state.store);
      } catch {
        // Zonder opslag onthoudt de pagina de keuze alleen tot je hem herlaadt.
      }
      const button = $("#add-cart");
      if (button) button.textContent = cartButtonLabel();
    });
  }
  const button = $("#add-cart");
  if (button) button.textContent = cartButtonLabel();
  renderItemsStore();
  if (typeof renderBasketStoreChoice === "function") renderBasketStoreChoice();
}

// De winkel in de tab Winkels: AH, of Jumbo zodra die gekoppeld is. Jumbo heeft geen recepten.
function itemsApi() {
  return `/api/${state.itemsStore}`;
}

function itemsStoreName() {
  return STORES[state.itemsStore].name;
}

// De winkels die in de tab Winkels te kiezen zijn: AH altijd, Jumbo en Picnic zodra ze gekoppeld zijn.
function itemsStores() {
  return ["ah", ...["jumbo", "picnic"].filter((key) => state.linkedStores[key])];
}

const PICNIC_SUBTABS = ["ah-items-picnic", "ah-items-picnic-favorieten"];

function renderItemsStore() {
  const stores = itemsStores();
  if (!stores.includes(state.itemsStore)) state.itemsStore = "ah";
  const slot = $("#items-store-slot");
  if (slot) {
    slot.innerHTML =
      stores.length > 1
        ? `<select id="items-store" class="store-select" aria-label="${escapeHtml(t("cart.store"))}">
            ${stores.map((key) => `<option value="${key}"${state.itemsStore === key ? " selected" : ""}>${STORES[key].name}</option>`).join("")}
          </select>`
        : "";
    $("#items-store")?.addEventListener("change", (event) => switchItemsStore(event.target.value));
  }
  // AH heeft recepten, Jumbo niet; lijstjes en bestellingen zijn er voor AH en Jumbo; Picnic heeft recepten en favorieten.
  const picnic = state.itemsStore === "picnic";
  const hide = (subtab, hidden) =>
    document.querySelectorAll(`[data-subtab="${subtab}"]`).forEach((button) => {
      button.hidden = hidden;
    });
  hide("ah-items-recepten", state.itemsStore !== "ah");
  hide("ah-items-lijstjes", picnic);
  hide("ah-items-bestellingen", picnic);
  PICNIC_SUBTABS.forEach((subtab) => hide(subtab, !picnic));
  // Staat de open tab niet meer bij de gekozen winkel (bijvoorbeeld na het ontkoppelen), ga dan naar de eerste die er wel is.
  const open = document.querySelector("#ah-items .subtab.is-active");
  if (open?.hidden) document.querySelector("#ah-items .subtab:not([hidden])")?.click();
}

function switchItemsStore(store) {
  state.itemsStore = itemsStores().includes(store) ? store : "ah";
  // Een andere winkel heeft andere lijstjes: begin met een schone lijst en laad opnieuw.
  state.ahListsLoaded = false;
  state.ahLists = [];
  state.ahListHits = {};
  state.ahListQuery = "";
  $("#ah-list-filter").value = "";
  closeAhList(false);
  renderAhLists();
  renderItemsStore();
  if (typeof resetOrders === "function") resetOrders();
  // Een tab die bij de gekozen winkel niet bestaat, maakt plaats: Picnic begint bij de recepten, AH en Jumbo bij de
  // lijstjes (Jumbo heeft geen recepten). Anders blijft de tab waar hij was.
  const active = document.querySelector("#ah-items .subtab.is-active");
  const activeId = active?.dataset.subtab || "";
  if (state.itemsStore === "picnic") {
    if (!PICNIC_SUBTABS.includes(activeId)) document.querySelector('[data-subtab="ah-items-picnic"]').click();
  } else if (PICNIC_SUBTABS.includes(activeId) || (state.itemsStore === "jumbo" && activeId === "ah-items-recepten")) {
    document.querySelector('[data-subtab="ah-items-lijstjes"]').click();
  } else if (document.querySelector("#ah-items-lijstjes.is-active")) {
    state.ahListsLoaded = true;
    loadAhFavoriteLists();
  }
}

try {
  const remembered = localStorage.getItem("ms-store");
  if (remembered && STORES[remembered]) state.preferredStore = remembered;
} catch {
  // Geen opslag beschikbaar: AH blijft de standaard.
}

function showCartResult(data, notFound) {
  const lines = (data.items || [])
    .map((item) => {
      const size = item.product.unitSize ? ` (${escapeHtml(item.product.unitSize)})` : "";
      return `<li>${escapeHtml(item.quantity)}x ${escapeHtml(item.product.title)}${size}</li>`;
    })
    .join("");
  // Picnic en Jumbo melden zelf ook welke producten ze niet in het mandje kregen (of al op het lijstje stonden).
  const names = state.store === "ah" ? notFound : [...notFound, ...(data.skippedItems || [])];
  const missing = names.length
    ? th(state.store === "jumbo-list" ? "cart.on_list" : "cart.missing", { count: names.length, names: names.join(", ") })
    : "";
  const ready = currentStore().ready;
  $("#cart-result").innerHTML = `
    <div class="message success">${th(ready, { added: data.added }).replace("{missing}", missing)}</div>
    ${lines ? `<ul class="cart-lines">${lines}</ul>` : ""}
  `;
  // Picnic en Jumbo vullen het echte mandje: laat zien waar je het kunt bekijken en aanpassen.
  if (state.store === "picnic" || state.store === "jumbo") {
    $("#cart-result").insertAdjacentHTML(
      "beforeend",
      `<p><button class="button button--secondary" id="view-basket" type="button">${escapeHtml(t("basket.view"))}</button></p>`
    );
    $("#view-basket").addEventListener("click", () => openBasket(state.store));
  }
}

async function submitCart(selection, notFound) {
  const data = await jsonFetch(`/api/${currentStore().api}/${currentStore().cart || "cart"}`, {
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
        <h2>${escapeHtml(t("cart.review_title"))}</h2>
        <button class="button" id="confirm-cart"></button>
        <button class="button button--secondary" id="cancel-cart">${escapeHtml(t("common.cancel"))}</button>
      </div>
      <p class="muted">${escapeHtml(tn("cart.doubts", doubts))}</p>
      <div class="review-list">
        ${rows
          .map((row, index) => {
            if (!row.candidates.length) {
              return `
                <div class="review-row is-missing">
                  <input type="checkbox" disabled>
                  <div class="review-ingredient"><strong>${escapeHtml(row.display)}</strong></div>
                  <span class="muted">${row.error ? escapeHtml(row.error) : escapeHtml(t("cart.not_found"))}</span>
                </div>`;
            }
            const first = row.candidates[0];
            return `
              <div class="review-row${first.doubt ? " is-doubt" : ""}" data-row="${index}">
                <input type="checkbox" class="review-include" checked aria-label="${escapeHtml(t("cart.include"))}">
                <div class="review-ingredient">
                  <strong>${escapeHtml(row.display)}</strong>
                  <small class="review-note">${escapeHtml(first.doubt || "")}</small>
                </div>
                <select class="review-product" aria-label="${escapeHtml(t("cart.product"))}">
                  ${row.candidates
                    .map((candidate, at) => `<option value="${at}">${escapeHtml(cartCandidateLabel(candidate))}</option>`)
                    .join("")}
                </select>
                ${
                  first.url
                    ? `<a class="review-link" href="${escapeHtml(first.url)}" target="_blank" rel="noopener noreferrer" title="${escapeHtml(t("cart.view_title"))}">${escapeHtml(t("cart.view"))} ${ICON_EXTERNAL}</a>`
                    : '<span class="review-link"></span>'
                }
                <input class="review-qty" type="number" min="1" max="99" value="${first.packs}" aria-label="${escapeHtml(t("cart.packs"))}">
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
    button.textContent = tn("cart.confirm", count);
  };
  rowElements.forEach((el) => {
    const row = rows[Number(el.dataset.row)];
    el.querySelector(".review-product").addEventListener("change", (event) => {
      const candidate = row.candidates[Number(event.target.value)];
      el.querySelector(".review-qty").value = candidate.packs;
      const link = el.querySelector("a.review-link");
      if (link && candidate.url) link.href = candidate.url;
      el.querySelector(".review-note").textContent = candidate.doubt || "";
      el.classList.toggle("is-doubt", Boolean(candidate.doubt));
    });
    el.querySelector(".review-include").addEventListener("change", updateConfirm);
    el.querySelector("a.review-link")?.addEventListener("click", (event) => {
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
    button.textContent = t("cart.adding");
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
  button.textContent = t("cart.searching");
  $("#cart-result").innerHTML = "";
  try {
    const preview = await jsonFetch(`/api/${currentStore().api}/cart/preview`, {
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
        $("#cart-result").innerHTML = `<div class="message error">${escapeHtml(t("cart.none_found"))}</div>`;
      } else {
        await submitCart(selection, notFound);
      }
    }
  } catch (error) {
    $("#cart-result").innerHTML = `<div class="message error">${escapeHtml(error.message)}</div>`;
  } finally {
    button.disabled = false;
    button.textContent = cartButtonLabel();
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
    state.searchData = await jsonFetch(`/api/ah/search?q=${encodeURIComponent(query)}&page=${page}`);
    renderSearchResults();
  } catch (error) {
    message("#search-message", error.message);
  }
}

function renderSearchResults() {
  const data = state.searchData;
  if (!data) return;
  const total = Number(data.total);
  $("#search-count").textContent = tn("search.count", total, { limit: total > 2000 ? t("search.limit") : "" });
  $("#recipe-results").innerHTML = (data.recipes || []).map(renderRecipeCard).join("");
  $("#prev-page").hidden = state.searchPage === 0;
  $("#next-page").hidden = !(data.hasNextPage ?? total > (state.searchPage + 1) * 12);
  document.querySelectorAll("#recipe-results [data-import-url]").forEach((button) => {
    button.addEventListener("click", () => importUrl(button.dataset.importUrl, button));
  });
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
      ${totalTime || recipe.servings ? `<p class="muted">${totalTime ? escapeHtml(t("recipe.minutes", { n: totalTime })) : ""}${totalTime && recipe.servings ? " | " : ""}${recipe.servings ? escapeHtml(t("recipe.servings", { n: recipe.servings })) : ""}</p>` : ""}
      <div class="tags">${tags.map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join("")}</div>
      <div class="card-actions">
        <button class="button" data-import-url="${escapeHtml(ahUrl)}">${escapeHtml(t("recipe.import"))}</button>
        <a href="${escapeHtml(ahUrl)}" target="_blank" rel="noreferrer">${escapeHtml(t("recipe.open_ah"))}</a>
      </div>
    </article>
  `;
}

async function importUrl(url, button = null) {
  if (button) {
    button.disabled = true;
    button.textContent = t("recipe.importing");
  }
  try {
    const data = await jsonFetch("/api/mealie/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    const link = data.mealieUrl ? ` <a href="${escapeHtml(data.mealieUrl)}" target="_blank" rel="noreferrer">${escapeHtml(t("recipe.view_mealie"))}</a>` : "";
    return { ok: true, html: `${escapeHtml(t("recipe.imported"))}${link}` };
  } catch (error) {
    return { ok: false, html: error.message };
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = t("recipe.import");
    }
  }
}

async function refreshAhStatus() {
  if (!$("#ah-status")) return;
  try {
    const data = await jsonFetch("/api/ah/auth/status");
    message(
      "#ah-status",
      data.connected ? t("ah.linked") : t("ah.not_linked"),
      data.connected ? "success" : "error"
    );
  } catch (error) {
    message("#ah-status", error.message);
  }
}

function renderMealieStatus(data, { verified = false } = {}) {
  const setup = $("#mealie-setup");
  if (setup) setup.hidden = Boolean(data.configured);
  $("#mealie-unlink").hidden = data.source !== "ui";
  const source = $("#mealie-source");
  source.hidden = data.source !== "env";
  source.textContent =
    data.source === "env"
      ? t("mealie.source_env")
      : "";
  if (data.url && document.activeElement !== $("#mealie-url") && !$("#mealie-url").value) {
    $("#mealie-url").value = data.url;
  }
  if (!data.configured) {
    message("#mealie-status", t("mealie.not_linked"), "error");
  } else if (verified && data.connected) {
    const details = [
      data.version ? t("mealie.version", { version: data.version }) : "",
      data.username ? t("mealie.user", { user: data.username }) : "",
    ]
      .filter(Boolean)
      .join(", ");
    message("#mealie-status", t("mealie.works", { url: data.url, details: details ? ` (${details})` : "" }), "success");
  } else if (verified) {
    message("#mealie-status", data.error || t("mealie.unexpected"), "error");
  } else {
    message("#mealie-status", t("mealie.linked", { url: data.url }), "success");
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
  message("#mealie-status", t("mealie.checking"), "success");
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
    message("#mealie-status", t("mealie.fill_both"));
    return;
  }
  const button = $("#mealie-link");
  button.disabled = true;
  button.textContent = t("mealie.checking_btn");
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
    button.textContent = t("common.link");
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
  message("#ah-status", t("ah.checking"), "success");
  try {
    const data = await jsonFetch("/api/ah/auth/status?verify=1");
    if (data.connected) {
      message("#ah-status", t("ah.works"), "success");
    } else {
      message(
        "#ah-status",
        data.error || t("ah.expired"),
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
  return date.toLocaleString(I18N.locale(), { dateStyle: "medium", timeStyle: "short" });
}

function ahListStatus(list) {
  const total = Number(list.itemCount || 0);
  const count = tn("list.items", total);
  if (!list.synced) return `${count} · ${t("list.not_synced")}`;
  const when = formatSyncTime(list.syncedAt);
  const stale =
    list.source === "both" && list.syncedCount !== null && list.syncedCount !== list.itemCount
      ? ` · ${t("list.changed_at_store", { store: itemsStoreName() })}`
      : "";
  const gone = list.source === "local" ? ` · ${t("list.local_only")}` : "";
  return `${count} · ${when ? t("list.synced_on", { when }) : t("list.synced")}${stale}${gone}`;
}

// Korte regels voor op een kaart; de volledige tekst (ahListStatus) staat in de tooltip.
function ahListCardLines(list) {
  const total = Number(list.itemCount || 0);
  const lines = [tn("list.items", total)];
  if (!list.synced) {
    lines.push(t("list.card.not_synced"));
    return lines;
  }
  const date = list.syncedAt ? new Date(list.syncedAt) : null;
  const day =
    date && !Number.isNaN(date.getTime()) ? date.toLocaleDateString(I18N.locale(), { day: "numeric", month: "short" }) : "";
  lines.push(day ? t("list.card.synced_day", { day }) : t("list.card.synced"));
  if (list.source === "local") lines.push(t("list.card.local_only"));
  else if (list.syncedCount !== null && list.syncedCount !== list.itemCount) lines.push(t("list.card.changed", { store: itemsStoreName() }));
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
  let empty = `<p class="muted">${escapeHtml(t("list.none"))}</p>`;
  if (query && state.ahLists.length) {
    empty = `<p class="muted">${th("list.no_match", { query })}</p>`;
  }
  $("#ah-lists").innerHTML = lists.length
    ? lists
        .map((list) => {
          const hits = query ? state.ahListHits[list.id] || [] : [];
          const syncLabel = list.synced ? t("list.sync_again") : t("common.sync");
          return `
            <div class="list-card">
              <button type="button" class="list-card__open" data-ah-list-id="${escapeHtml(list.id)}" title="${escapeHtml(ahListStatus(list))}">
                <strong>${escapeHtml(list.name || "-")}</strong>
                ${ahListCardLines(list).map((line) => `<small>${escapeHtml(line)}</small>`).join("")}
                ${hits.length ? `<small class="list-card__hit">${th("list.contains", { titles: hits.join(", ") })}</small>` : ""}
              </button>
              ${
                list.source === "local"
                  ? ""
                  : `<button type="button" class="icon-button list-card__sync" data-ah-sync-id="${escapeHtml(list.id)}" aria-label="${escapeHtml(syncLabel)}: ${escapeHtml(list.name || "")}" title="${escapeHtml(syncLabel)}">${mdiIcon(MDI_SYNC)}</button>`
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
    const data = await jsonFetch(`${itemsApi()}/lists/search?q=${encodeURIComponent(query)}`);
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
  button.textContent = t("saved.loading");
  try {
    const data = await jsonFetch(`${itemsApi()}/lists`);
    state.ahLists = data.lists || [];
    renderAhLists();
    if (state.ahListQuery.trim()) searchAhListContents();
    // Lokale kopieen blijven zichtbaar; meld wel waarom de live lijst ontbreekt.
    const hasLocal = state.ahLists.some((list) => list.synced);
    message(
      "#ah-lists-warning",
      data.storeError || data.ahError
        ? t("list.store_down", { store: itemsStoreName(), copies: hasLocal ? t("list.store_down_copies") : "", error: data.storeError || data.ahError })
        : ""
    );
  } catch (error) {
    message("#ah-lists", error.message);
  } finally {
    button.disabled = false;
    button.textContent = t("common.load");
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
  setBusy(button, true, t("list.syncing"));
  try {
    const data = await jsonFetch(`${itemsApi()}/lists/${encodeURIComponent(listId)}/sync`, { method: "POST" });
    return { ok: true, data };
  } catch (error) {
    return { ok: false, error: error.message };
  } finally {
    setBusy(button, false, t("list.syncing"));
  }
}

// "X gesynchroniseerd (8 items). 2 nieuw, 1 aangepast aantal behouden." Zo zie je dat je aanpassingen blijven staan.
function syncSummary(data) {
  const info = data.sync || {};
  const parts = [
    info.new ? tn("list.sync_new", info.new) : "",
    info.removed ? tn("list.sync_removed", info.removed) : "",
    info.keptQuantities ? tn("list.sync_kept", info.keptQuantities) : "",
  ].filter(Boolean);
  return t("list.sync_done", { name: data.name, count: data.totalSize }) + (parts.length ? ` ${parts.join(", ")}.` : "");
}

async function syncOneAndReport(listId, button) {
  const result = await syncAhList(listId, button);
  await loadAhFavoriteLists();
  const outcome = result.ok
    ? [syncSummary(result.data), "success"]
    : [t("list.sync_failed", { error: result.error }), "error"];
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
  setBusy(button, true);
  try {
    const data = await jsonFetch(`${itemsApi()}/lists`);
    const lists = (data.lists || []).filter((list) => list.source !== "local");
    if (!lists.length) {
      message(
        "#ah-lists-warning",
        data.storeError || data.ahError ? t("list.store_down_short", { store: itemsStoreName(), error: data.storeError || data.ahError }) : t("list.none_to_sync")
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
        ? t("list.sync_partial", { done: lists.length - failures.length, total: lists.length, failures: failures.join("; ") })
        : t("list.sync_all_done", { count: lists.length }),
      failures.length ? "error" : "success"
    );
  } catch (error) {
    message("#ah-lists-warning", error.message);
  } finally {
    setBusy(button, false);
  }
}

// Een lijstje opent op dezelfde plek als het overzicht; de pijl (of de terugknop van de telefoon) gaat terug.
function showAhListsOverview({ restoreScroll = true } = {}) {
  state.ahOpenListId = null;
  state.ahOpenList = null;
  $("#ah-list-view").hidden = true;
  $("#ah-lists-view").hidden = false;
  if (restoreScroll) window.scrollTo({ top: state.ahListsScroll });
}

async function openAhList(listId, { push = true } = {}) {
  if (!state.ahOpenListId) state.ahListsScroll = window.scrollY;
  state.ahOpenListId = listId;
  state.ahOpenList = null;
  if (push) history.pushState({ ahList: listId }, "");
  const known = state.ahLists.find((list) => list.id === listId);
  $("#ah-list-title").textContent = known?.name || t("list.default_title");
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
  $("#ah-list-items").innerHTML = `<div class="message success">${escapeHtml(t("list.loading"))}</div>`;
  try {
    const data = await jsonFetch(`${itemsApi()}/lists/${encodeURIComponent(listId)}`);
    if (state.ahOpenListId !== listId) return; // inmiddels teruggegaan of een ander lijstje geopend
    const items = data.items || [];
    state.ahOpenList = data;
    renderOpenAhList();
    if (keepScroll) window.scrollTo({ top: scroll });
  } catch (error) {
    if (state.ahOpenListId !== listId) return;
    $("#ah-list-items").innerHTML = "";
    message("#ah-list-items-message", error.message);
  }
}

function renderOpenAhList() {
  const data = state.ahOpenList;
  if (!data) return;
  const items = data.items || [];
  const synced = data.source === "local";
  const syncLabel = synced ? t("list.sync_again") : t("common.sync");
  $("#ah-list-title").textContent = data.name || t("list.default_title");
  for (const attribute of ["title", "aria-label"]) $("#sync-open-ah-list").setAttribute(attribute, syncLabel);
  $("#ah-list-items").innerHTML = `<div class="product-grid">${items.map((item) => renderAhListItem(item, synced)).join("")}</div>`;
  $("#ah-list-to-cart").disabled = !items.length;
  updateAhListControls();
  if (!synced && items.length) message("#ah-list-items-message", t("list.qty_sync_first"), "success");
}

// Reset-knop en "aangepast"-markering volgen de aantallen die nu in state.ahOpenList staan.
function updateAhListControls() {
  const items = state.ahOpenList?.items || [];
  const edited = items.filter((item) => item.edited).length;
  $("#reset-ah-list").disabled = !edited;
  const synced = state.ahOpenList?.source === "local";
  $("#ah-list-meta").textContent =
    (synced
      ? t("list.products_local", { count: I18N.formatNumber(items.length), when: formatSyncTime(state.ahOpenList.syncedAt) })
      : t("list.products_live", { count: I18N.formatNumber(items.length), store: itemsStoreName() })) +
    (edited ? t("list.products_edited", { count: I18N.formatNumber(edited) }) : "");
  for (const item of items) {
    const card = $(`#ah-list-items [data-position="${item.position}"]`);
    if (!card) continue;
    card.classList.toggle("is-edited", Boolean(item.edited));
    const note = card.querySelector(".qty__note");
    if (note) note.textContent = item.edited ? t("list.qty_default", { count: item.defaultQuantity }) : "";
  }
}

function renderQuantityControl(item, title) {
  const label = (key) => escapeHtml(t(key, { title }));
  return `
    <div class="qty">
      <span class="qty__note">${item.edited ? escapeHtml(t("list.qty_default", { count: item.defaultQuantity })) : ""}</span>
      <button type="button" class="qty__step" data-qty-step="-1" aria-label="${label("list.qty_minus")}">${mdiIcon(MDI_MINUS)}</button>
      <input class="qty__input" type="number" inputmode="numeric" min="1" max="99" step="1" value="${escapeHtml(item.quantity)}" aria-label="${label("list.qty_input")}">
      <button type="button" class="qty__step" data-qty-step="1" aria-label="${label("list.qty_plus")}">${mdiIcon(MDI_PLUS)}</button>
    </div>
  `;
}

function renderAhListItem(item, editable = false) {
  const product = item.product || {};
  const title = product.title || t("list.product_fallback", { id: item.productId });
  const price = Number(product.price || 0);
  const url = item.url || product.url || "";
  const meta = [product.brand, product.unitSize].filter(Boolean).join(" · ");
  const body = `
    ${product.image ? `<img src="${escapeHtml(product.image)}" alt="" loading="lazy">` : ""}
    <div>
      <strong>${editable ? "" : `${escapeHtml(item.quantity)}x `}${escapeHtml(title)}</strong>
      ${meta ? `<small>${escapeHtml(meta)}</small>` : ""}
      ${product.isBonus ? `<small class="bonus">${escapeHtml(t("list.bonus"))}</small>` : ""}
      ${price ? `<small>${escapeHtml(formatPrice(price))}</small>` : ""}
    </div>
  `;
  const main = url
    ? `<a class="product-card__main" href="${escapeHtml(url)}" target="_blank" rel="noreferrer noopener">${body}</a>`
    : `<div class="product-card__main">${body}</div>`;
  return `<article class="product-card product-card--list${item.edited ? " is-edited" : ""}" data-position="${escapeHtml(item.position)}">${main}${editable ? renderQuantityControl(item, title) : ""}</article>`;
}

// Aantallen: meteen zichtbaar, kort daarna (en voor het mandje altijd) bewaard op de server.
const pendingQuantities = new Map();
const quantitySaves = new Set();

function queueQuantity(position, value) {
  const item = state.ahOpenList?.items.find((entry) => entry.position === position);
  if (!item) return;
  item.quantity = value;
  item.edited = value !== item.defaultQuantity;
  updateAhListControls();
  clearTimeout(pendingQuantities.get(position)?.timer);
  const timer = setTimeout(() => saveQuantity(position), 300);
  pendingQuantities.set(position, { listId: state.ahOpenListId, value, timer });
}

function saveQuantity(position) {
  const pending = pendingQuantities.get(position);
  if (!pending) return Promise.resolve();
  clearTimeout(pending.timer);
  pendingQuantities.delete(position);
  const save = jsonFetch(`${itemsApi()}/lists/${encodeURIComponent(pending.listId)}/items/${position}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ quantity: pending.value }),
  })
    .catch(async (error) => {
      // Opnieuw laden zet de getoonde aantallen terug naar wat is opgeslagen; de melding komt daarna, anders wist het laden haar.
      if (state.ahOpenListId === pending.listId) await loadAhFavoriteListItems(pending.listId, { keepScroll: true });
      message("#ah-list-items-message", t("list.qty_failed", { error: error.message }));
    })
    .finally(() => quantitySaves.delete(save));
  quantitySaves.add(save);
  return save;
}

async function flushQuantities() {
  for (const position of [...pendingQuantities.keys()]) saveQuantity(position);
  await Promise.all([...quantitySaves]);
}

function clampQuantity(value) {
  const number = Math.round(Number(value));
  return Number.isFinite(number) ? Math.min(99, Math.max(1, number)) : 1;
}

function onQuantityStep(button) {
  const card = button.closest("[data-position]");
  const input = card.querySelector(".qty__input");
  input.value = clampQuantity(Number(input.value) + Number(button.dataset.qtyStep));
  queueQuantity(Number(card.dataset.position), Number(input.value));
}

function onQuantityInput(input) {
  if (input.value === "") return; // nog aan het typen
  input.value = clampQuantity(input.value);
  queueQuantity(Number(input.closest("[data-position]").dataset.position), Number(input.value));
}

async function resetOpenAhList() {
  const listId = state.ahOpenListId;
  const button = $("#reset-ah-list");
  const edited = (state.ahOpenList?.items || []).filter((item) => item.edited).length;
  // Terugzetten gooit je aanpassingen weg; eerst bevestigen.
  if (!window.confirm(tn("list.reset_confirm", edited))) return;
  setBusy(button, true);
  message("#ah-list-items-message", "");
  try {
    await flushQuantities();
    await jsonFetch(`${itemsApi()}/lists/${encodeURIComponent(listId)}/reset`, { method: "POST" });
    if (state.ahOpenListId !== listId) return;
    await loadAhFavoriteListItems(listId, { keepScroll: true });
    message("#ah-list-items-message", t("list.reset_done"), "success");
  } catch (error) {
    message("#ah-list-items-message", error.message);
  } finally {
    setBusy(button, false);
    updateAhListControls();
  }
}

async function addOpenAhListToCart() {
  const listId = state.ahOpenListId;
  const button = $("#ah-list-to-cart");
  const label = button.querySelector("span");
  button.disabled = true;
  label.textContent = t("list.cart_busy");
  message("#ah-list-items-message", "");
  try {
    await flushQuantities();
    const data = await jsonFetch(`${itemsApi()}/lists/${encodeURIComponent(listId)}/cart`, { method: "POST" });
    message(
      "#ah-list-items-message",
      t("list.cart_done", { name: data.name || t("list.default_title"), count: data.added, units: data.units, store: itemsStoreName() }),
      "success"
    );
  } catch (error) {
    message("#ah-list-items-message", t("list.cart_failed", { error: error.message }));
  } finally {
    label.textContent = t("list.to_cart");
    button.disabled = !state.ahOpenList?.items?.length;
  }
}

function renderSavedRecipeCard(recipe) {
  const url = recipe.url || "";
  const image = recipe.image || "";
  return `
    <article class="recipe-card">
      ${image ? `<img src="${escapeHtml(image)}" alt="${escapeHtml(recipe.title || "")}" loading="lazy">` : ""}
      <h3>${escapeHtml(recipe.title || t("recipe.unknown"))}</h3>
      <div class="card-actions">
        <button class="button" data-import-url="${escapeHtml(url)}">${escapeHtml(t("recipe.import"))}</button>
        ${
          url
            ? `<a class="card-link" href="${escapeHtml(url)}" target="_blank" rel="noreferrer noopener" aria-label="${escapeHtml(t("recipe.open_label", { title: recipe.title || "" }))}">${mdiIcon(MDI_OPEN_IN_NEW)}<span>${escapeHtml(t("recipe.open_ah"))}</span></a>`
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
    recipes.length ? "" : t("saved.none_match", { query }),
    "success"
  );
}

function updateSavedRecipesStatus(data) {
  const total = Number(data.total ?? state.ahRecipes.length);
  const when = formatSyncTime(data.syncedAt);
  state.ahRecipesMeta = data;
  $("#ah-recipes-status").textContent =
    tn("saved.status", total) + (when ? t("saved.status_when", { when }) : "") + t("saved.status_hint");
}

// `cachedOnly`: alleen de lokale kopie tonen, nooit AH vragen (zo opent het tabblad zonder AH-verkeer).
function refreshSavedRecipesStatus() {
  const meta = state.ahRecipesMeta;
  if (state.ahRecipes.length && meta) updateSavedRecipesStatus(meta);
  else $("#ah-recipes-status").textContent = state.ahRecipesLoaded ? t("saved.not_fetched") : t("saved.intro");
  const button = $("#load-ah-recipes");
  button.dataset.label = state.ahRecipesFetched ? t("saved.refresh") : t("saved.fetch");
  if (!button.disabled) button.textContent = button.dataset.label;
}

async function loadSavedRecipes({ refresh = false, cachedOnly = false } = {}) {
  const button = $("#load-ah-recipes");
  state.ahRecipesLoaded = true;
  setBusy(button, true, t("saved.loading"));
  message("#ah-recipes-message", "");
  try {
    const query = refresh ? "?refresh=1" : cachedOnly ? "?cached=1" : "";
    const data = await jsonFetch(`/api/ah/recipes/saved${query}`);
    state.ahRecipes = data.recipes || [];
    $("#ah-recipe-filter").disabled = !state.ahRecipes.length;
    button.dataset.label = data.syncedAt ? t("saved.refresh") : t("saved.fetch");
    state.ahRecipesFetched = Boolean(data.syncedAt);
    if (!state.ahRecipes.length) {
      $("#ah-recipes").innerHTML = "";
      if (data.source === "none") {
        $("#ah-recipes-status").textContent = t("saved.not_fetched");
      } else {
        message("#ah-recipes-message", t("saved.none"), "success");
      }
      return;
    }
    updateSavedRecipesStatus(data);
    renderSavedRecipes();
    if (data.ahError) {
      message(
        "#ah-recipes-message",
        t("saved.ah_down", { error: data.ahError }),
        "error"
      );
    }
  } catch (error) {
    message("#ah-recipes-message", error.message);
  } finally {
    setBusy(button, false, t("saved.loading"));
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
    if (tab.dataset.tab === "about" && !state.aboutLoaded) loadAbout();
    if (tab.dataset.tab === "basket") showBasket();
  });
});

// Het logo brengt je naar het startscherm (de eerste tab); met Ctrl, Cmd of de middelste knop opent het gewoon een nieuw tabblad.
$("#brand-home").addEventListener("click", (event) => {
  if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  document.querySelector('.tab[data-tab="planner"]').click();
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
    if (subtab.dataset.subtab === "beheer-users" && typeof loadUsers === "function") loadUsers();
    if (subtab.dataset.subtab === "beheer-ai" && typeof loadAi === "function") loadAi();
    if (subtab.dataset.subtab === "ah-items-bestellingen" && typeof loadOrders === "function") loadOrders();
    if (subtab.dataset.subtab === "ah-items-picnic" && typeof loadPicnicRecipes === "function" && !picnicRecipes.loaded) loadPicnicRecipes();
    if (subtab.dataset.subtab === "ah-items-picnic-favorieten" && typeof loadPicnicFavorites === "function" && !picnicFavorites.loaded) loadPicnicFavorites();
    if (subtab.dataset.subtab === "ah-items-lijstjes" && !state.ahListsLoaded) {
      state.ahListsLoaded = true;
      loadAhFavoriteLists();
    }
  });
});

// Over: versie en commit van deze installatie, de gebruikte componenten en integraties.
async function loadAbout() {
  state.aboutLoaded = true;
  message("#about-message", "");
  try {
    state.about = await jsonFetch("/api/about");
    renderAbout();
  } catch (error) {
    state.aboutLoaded = false;
    message("#about-message", `${t("about.failed")} ${error.message}`);
  }
}

function renderAbout() {
  const about = state.about;
  if (!about) return;
  $("#about-version").textContent = about.label;
  const groups = ["integrations", "server", "interface"];
  $("#about-components").innerHTML = groups
    .map((group) => {
      const items = about.components.filter((component) => component.group === group);
      return `
        <h3 class="about-group">${escapeHtml(t(`about.group.${group}`))}</h3>
        <ul class="about-list">
          ${items
            .map(
              (component) => `
                <li>
                  <div class="about-list__head">
                    <a href="${escapeHtml(component.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(component.name)}</a>
                    ${component.version ? `<span class="about-list__version">${escapeHtml(component.version)}</span>` : ""}
                  </div>
                  <small>${escapeHtml(t(`about.desc.${component.id}`))}</small>
                  ${component.license ? `<small class="about-list__license">${escapeHtml(t("about.license", { license: component.license }))}</small>` : ""}
                </li>`
            )
            .join("")}
        </ul>`;
    })
    .join("");
}

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
    message("#token-message", t("ah.saved"), "success");
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
$("#sync-open-ah-list").addEventListener("click", async (event) => {
  await flushQuantities();
  syncOneAndReport(state.ahOpenListId, event.currentTarget);
});
$("#reset-ah-list").addEventListener("click", resetOpenAhList);
$("#ah-list-to-cart").addEventListener("click", addOpenAhListToCart);
$("#ah-list-items").addEventListener("click", (event) => {
  const step = event.target.closest("[data-qty-step]");
  if (step) onQuantityStep(step);
});
$("#ah-list-items").addEventListener("change", (event) => {
  if (event.target.matches(".qty__input")) onQuantityInput(event.target);
});
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
$("#login-form").addEventListener("submit", loginWithPassword);
$("#mfa-form").addEventListener("submit", submitMfaCode);
$("#mfa-cancel").addEventListener("click", cancelMfa);
$("#mfa-recovery-toggle").addEventListener("click", toggleRecoveryInput);
$("#login-passkey").addEventListener("click", handlePasskeyLogin);
$("#add-passkey").addEventListener("click", addPasskey);
$("#auth-logout").addEventListener("click", async () => {
  await jsonFetch("/api/auth/logout", { method: "POST", body: "{}" });
  await refreshAuthStatus();
});

const params = new URLSearchParams(window.location.search);
if (params.get("ah_refresh")) {
  openBeheer("beheer-ah");
  $("#token-input").value = params.get("ah_refresh");
  message("#token-message", t("ah.refresh_received"), "success");
  history.replaceState({}, "", "/");
}
if (params.get("ah_connected")) {
  openBeheer("beheer-ah");
  message("#token-message", t("ah.saved"), "success");
  history.replaceState({}, "", "/");
}
if (params.get("ah_error")) {
  openBeheer("beheer-ah");
  message("#token-message", t("ah.redirect_failed", { error: params.get("ah_error") }));
  history.replaceState({}, "", "/");
}

// Een andere taal: alles wat uit gegevens is opgebouwd opnieuw tekenen (de vaste teksten doet i18n.js).
window.addEventListener("ms:languagechange", () => {
  updateWeekLabel();
  if (state.entries) renderMealPlan(state.entries);
  renderSearchResults();
  renderAuthState();
  renderCredentials();
  renderAhLists();
  if (state.ahOpenListId) loadAhFavoriteListItems(state.ahOpenListId, { keepScroll: true });
  renderSavedRecipes();
  refreshSavedRecipesStatus();
  renderAbout();
  if (typeof renderUpdate === "function") renderUpdate();
  if (typeof renderUsers === "function") renderUsers();
  if (typeof renderInviteGate === "function") renderInviteGate();
  if (state.auth?.authenticated || !state.auth?.enabled) {
    refreshAhStatus();
    refreshMealieStatus();
  }
});

updateWeekLabel();
// Pas starten als ook onboarding.js (het laatste script) is geladen.
document.addEventListener("DOMContentLoaded", async () => {
  if (typeof loadInvite === "function") await loadInvite();
  refreshAuthStatus().catch((error) => {
    message("#auth-message", error.message);
    $("#auth-gate").hidden = false;
  });
});
