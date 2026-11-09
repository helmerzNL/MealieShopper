const state = {
  weekOffset: 0,
  firstDayOfWeek: 1, // waar de week begint, zoals Mealie telt (0 = zondag, 1 = maandag); komt uit /api/mealie/status
  entries: null,
  planAutoLoaded: false, // het weekmenu is na het koppelen van Mealie eenmaal vanzelf geladen
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
  ahRecipeGroups: [], // de receptgroepen van AH: [{id, name, count, recipeIds}]
  ahRecipeGroup: "", // de gekozen groep; "" is alle bewaarde recepten
  about: null,
  aboutLoaded: false,
  store: "ah", // de keuze voor het weekmenu: "ah", "picnic", "jumbo" of "crisp"
  itemsStore: "ah", // de winkel in de tab Winkels: "ah", "jumbo", "picnic" of "crisp"
  itemsStoreChosen: false, // zelf een winkel gekozen in Winkels (menu of adres); tot dan volgt hij de voorkeurssupermarkt
  preferredStore: "ah", // de laatste keuze van de gebruiker in het weekmenu; geldt zodra die winkel als gekoppeld bekend is
  linkedStores: { picnic: false, jumbo: false, crisp: false, mealie: false }, // gezet door picnic.js, jumbo.js, crisp.js en renderMealieStatus; AH is altijd een keuze
  storeStatusKnown: { picnic: false, jumbo: false, crisp: false }, // de status van die koppeling is binnen (gekoppeld of niet)
};

// Material Design Icons (zie static/icons/LICENSE-mdi.txt)
const MDI_SYNC =
  "M12,18A6,6 0 0,1 6,12C6,11 6.25,10.03 6.7,9.2L5.24,7.74C4.46,8.97 4,10.43 4,12A8,8 0 0,0 12,20V23L16,19L12,15M12,4V1L8,5L12,9V6A6,6 0 0,1 18,12C18,13 17.75,13.97 17.3,14.8L18.76,16.26C19.54,15.03 20,13.57 20,12A8,8 0 0,0 12,4Z";
const MDI_PLUS = "M19,13H13V19H11V13H5V11H11V5H13V11H19V13Z";
const MDI_MINUS = "M19,13H5V11H19V13Z";
const MDI_LOADING = "M12,4V2A10,10 0 0,0 2,12H4A8,8 0 0,1 12,4Z";
const MDI_CHECK_CIRCLE_OUTLINE = "M12 2C6.5 2 2 6.5 2 12S6.5 22 12 22 22 17.5 22 12 17.5 2 12 2M12 20C7.59 20 4 16.41 4 12S7.59 4 12 4 20 7.59 20 12 16.41 20 12 20M16.59 7.58L10 14.17L7.41 11.59L6 13L10 17L18 9L16.59 7.58Z";
const MDI_IMAGE_OUTLINE = "M19,19H5V5H19M19,3H5A2,2 0 0,0 3,5V19A2,2 0 0,0 5,21H19A2,2 0 0,0 21,19V5A2,2 0 0,0 19,3M13.96,12.29L11.21,15.83L9.25,13.47L6.5,17H17.5L13.96,12.29Z";
const MDI_COMPARE_HORIZONTAL = "M9 14H2V16H9V19L13 15L9 11V14M15 13V10H22V8H15V5L11 9L15 13Z";
const MDI_CHART_LINE = "M16,11.78L20.24,4.45L21.97,5.45L16.74,14.5L10.23,10.75L5.46,19H22V21H2V3H4V17.54L9.5,8L16,11.78Z";
const MDI_FLAG_OUTLINE = "M12.36,6L12.76,8H18V14H14.64L14.24,12H7V6H12.36M14,4H5V21H7V14H12.6L13,16H20V6H14.4";
const MDI_PENCIL_OUTLINE =
  "M14.06,9L15,9.94L5.92,19H5V18.08L14.06,9M17.66,3C17.41,3 17.15,3.1 16.96,3.29L15.13,5.12L18.88,8.87L20.71,7.04C21.1,6.65 21.1,6 20.71,5.63L18.37,3.29C18.17,3.09 17.92,3 17.66,3M14.06,6.19L3,17.25V21H6.75L17.81,9.94L14.06,6.19Z";
const MDI_OPEN_IN_NEW =
  "M14,3V5H17.59L7.76,14.83L9.17,16.24L19,6.41V10H21V3M19,19H5V5H12V3H5C3.89,3 3,3.9 3,5V19A2,2 0 0,0 5,21H19A2,2 0 0,0 21,19V12H19V19Z";

function mdiIcon(path, className = "mdi") {
  return `<svg class="${className}" viewBox="0 0 24 24" aria-hidden="true"><path d="${path}"/></svg>`;
}

// Een badge: een gekleurd label met tekst (status, bespaard, bonus, eerder gekocht). De soort bepaalt de kleur in de CSS
// (.badge--<soort>); de tekst staat er altijd, dus kleur is nooit het enige signaal.
function badge(kind, text, title = "") {
  const hint = title ? ` title="${escapeHtml(title)}"` : "";
  return `<span class="badge badge--${kind}"${hint}>${escapeHtml(text)}</span>`;
}

const MDI_LINK = "M10.59,13.41C11,13.8 11,14.44 10.59,14.83C10.2,15.22 9.56,15.22 9.17,14.83C7.22,12.88 7.22,9.71 9.17,7.76V7.76L12.71,4.22C14.66,2.27 17.83,2.27 19.78,4.22C21.73,6.17 21.73,9.34 19.78,11.29L18.29,12.78C18.3,11.96 18.17,11.14 17.89,10.36L18.36,9.88C19.54,8.71 19.54,6.81 18.36,5.64C17.19,4.46 15.29,4.46 14.12,5.64L10.59,9.17C9.41,10.34 9.41,12.24 10.59,13.41M13.41,9.17C13.8,8.78 14.44,8.78 14.83,9.17C16.78,11.12 16.78,14.29 14.83,16.24V16.24L11.29,19.78C9.34,21.73 6.17,21.73 4.22,19.78C2.27,17.83 2.27,14.66 4.22,12.71L5.71,11.22C5.7,12.04 5.83,12.86 6.11,13.65L5.64,14.12C4.46,15.29 4.46,17.19 5.64,18.36C6.81,19.54 8.71,19.54 9.88,18.36L13.41,14.83C14.59,13.66 14.59,11.76 13.41,10.59C13,10.2 13,9.56 13.41,9.17Z";
const MDI_LINK_OFF = "M2,5.27L3.28,4L20,20.72L18.73,22L13.9,17.17L11.29,19.78C9.34,21.73 6.17,21.73 4.22,19.78C2.27,17.83 2.27,14.66 4.22,12.71L5.71,11.22C5.7,12.04 5.83,12.86 6.11,13.65L5.64,14.12C4.46,15.29 4.46,17.19 5.64,18.36C6.81,19.54 8.71,19.54 9.88,18.36L12.5,15.76L10.88,14.15C10.87,14.39 10.77,14.64 10.59,14.83C10.2,15.22 9.56,15.22 9.17,14.83C8.12,13.77 7.63,12.37 7.72,11L2,5.27M12.71,4.22C14.66,2.27 17.83,2.27 19.78,4.22C21.73,6.17 21.73,9.34 19.78,11.29L18.29,12.78C18.3,11.96 18.17,11.14 17.89,10.36L18.36,9.88C19.54,8.71 19.54,6.81 18.36,5.64C17.19,4.46 15.29,4.46 14.12,5.64L10.79,8.97L9.38,7.55L12.71,4.22M13.41,9.17C13.8,8.78 14.44,8.78 14.83,9.17C16.2,10.54 16.61,12.5 16.06,14.23L14.28,12.46C14.23,11.78 13.94,11.11 13.41,10.59C13,10.2 13,9.56 13.41,9.17Z";
const MDI_CHECK_CIRCLE = "M12 2C6.5 2 2 6.5 2 12S6.5 22 12 22 22 17.5 22 12 17.5 2 12 2M10 17L5 12L6.41 10.59L10 14.17L17.59 6.58L19 8L10 17Z";

// De meldingen op de koppelpagina: op een groot scherm "Albert Heijn gekoppeld", op een klein scherm alleen de naam met
// een pictogram en de kleur van de balk (groen: gekoppeld, donkergroen met vinkje: gekoppeld en werkt, rood: niet gekoppeld).
// De tekst blijft voor schermlezers staan.
const LINK_STATUS = {
  linked: { icon: MDI_LINK, classes: "success", text: "link.status.linked" },
  unlinked: { icon: MDI_LINK_OFF, classes: "error", text: "link.status.unlinked" },
  working: { icon: MDI_CHECK_CIRCLE, classes: "success link-status--working", text: "link.status.working" },
};

function renderLinkStatus(target, name, status, detail = "") {
  const info = LINK_STATUS[status];
  $(target).innerHTML = `<div class="message ${info.classes} link-status" data-link-status="${status}" role="status">${mdiIcon(info.icon, "link-status__icon")}<span class="link-status__name">${escapeHtml(name)}</span> <span class="link-status__text">${escapeHtml(t(info.text))}${escapeHtml(detail)}.</span></div>`;
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

// Een productfoto van een winkel via de eigen cache (/api/images/<token>, zie image_cache.py): de server haalt hem een keer op
// en serveert hem daarna van schijf, zodat de browser niet bij de winkel hotlinkt. Een adres van een andere host (Mealie, een
// maaltijdbox) blijft zoals het is. Het token is het adres in URL-veilige base64 zonder "=".
const STORE_IMAGE_HOSTS = [/(^|\.)static\.ah\.nl$/, /(^|\.)jumbo\.com$/, /\.picnicinternational\.com$/, /(^|\.)crispapp\.nl$/];

function storeImage(url) {
  if (!url) return url;
  let host = "";
  try {
    const parsed = new URL(url, window.location.origin);
    if (parsed.protocol !== "https:" && parsed.protocol !== "http:") return url;
    host = parsed.hostname;
  } catch {
    return url;
  }
  if (!STORE_IMAGE_HOSTS.some((pattern) => pattern.test(host))) return url;
  const token = btoa(unescape(encodeURIComponent(String(url)))).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
  return `/api/images/${token}`;
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
  const auth = state.auth || {};
  const onboardingBusy = typeof onboardingVisible === "function" && onboardingVisible();
  const authenticated = auth.authenticated || !auth.enabled;
  // Een nieuwe installatie (nog niets ingesteld) gaat via de installatiehulp, niet via deze inlogpagina.
  const inviteOpen = Boolean(state.invite);
  const showGate = !authenticated && !auth.setupRequired && !onboardingBusy && !inviteOpen;

  gate.hidden = !showGate;
  if (typeof renderUserMenu === "function") renderUserMenu();

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
  if (typeof appearanceFromProfile === "function") appearanceFromProfile(); // de weergave van deze gebruiker
  if (typeof loadOnboardingStatus === "function") await loadOnboardingStatus();
  renderAuthState();
  if (continueAfterLogin()) return state.auth;
  await loadCredentials();
  if (state.auth?.authenticated || !state.auth?.enabled) {
    refreshAhStatus();
    refreshMealieStatus();
    if (typeof refreshPicnicStatus === "function") refreshPicnicStatus();
    if (typeof refreshJumboStatus === "function") refreshJumboStatus();
    if (typeof refreshCrispStatus === "function") refreshCrispStatus();
    if (typeof startUpdateCheck === "function") startUpdateCheck();
    if (typeof routerStart === "function") routerStart();
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

// JJJJ-MM-DD in de tijdzone van de gebruiker. toISOString() rekent in UTC en schoof tussen middernacht en 2 uur een dag op.
function localIsoDate(date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

// De eerste dag van de week waarin `date` valt. `firstDay` telt zoals Mealie en getDay(): 0 = zondag, 1 = maandag ... 6 = zaterdag.
function weekStartOf(date, firstDay = state.firstDayOfWeek) {
  const start = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  start.setDate(start.getDate() - ((start.getDay() - firstDay + 7) % 7));
  return start;
}

// De zeven dagen vanaf `start`, met het begin en eind (JJJJ-MM-DD) en een label als "7 nov - 13 nov".
function weekFrom(start) {
  const days = Array.from({ length: 7 }, (_, index) => new Date(start.getFullYear(), start.getMonth(), start.getDate() + index));
  const label = (d) => `${d.getDate()} ${monthShort(d)}`;
  return { start: localIsoDate(days[0]), end: localIsoDate(days[6]), days: days.map(localIsoDate), label: `${label(days[0])} - ${label(days[6])}` };
}

// De week van het weekmenu, `offset` weken van de huidige. Ze begint op de eerste dag uit Mealie of de instelling van de beheerder.
function getWeekBounds(offset = 0) {
  const today = new Date();
  today.setDate(today.getDate() + offset * 7);
  return weekFrom(weekStartOf(today));
}

// "Deze week", "Volgende week", "+2 weken": hoe ver een week van de huidige af ligt. Het weekmenu en Aanbiedingen gebruiken dezelfde tekst.
function weekOffsetText(offset) {
  if (offset === 0) return t("week.this");
  if (offset === 1) return t("week.next");
  if (offset === -1) return t("week.prev");
  return t("week.weeks", { n: `${offset > 0 ? "+" : ""}${offset}` });
}

function updateWeekLabel() {
  const week = getWeekBounds(state.weekOffset);
  $("#week-range").textContent = week.label;
  $("#week-offset").textContent = weekOffsetText(state.weekOffset);
}

// Een andere week kiezen. Het weekmenu en het tabblad Aanbiedingen delen de week: plan je volgende week, dan zie je de aanbiedingen
// van volgende week, en andersom. Het weekmenu laadt de week meteen (uit de cache als die er is), ook als je in Aanbiedingen wisselt,
// zodat er bij terugkomen geen menu van de vorige week staat.
function setPlanWeek(offset) {
  state.weekOffset = offset;
  updateWeekLabel();
  if (state.planAutoLoaded) loadMealPlan();
}

function formatDate(dateStr) {
  const date = new Date(`${dateStr}T00:00:00`);
  return `${weekdayShort(date)} ${date.getDate()} ${monthShort(date)}`;
}

// De eenheid en het ingrediënt zoals Mealie ze toont: de afkorting als die aanstaat, en het meervoud als Mealie het kent.
// Zelfde regels als `_unit_label` en `_food_label` in mealieshopper/ingredients.py.
function ingredientUnitLabel(unit, plural) {
  if (!unit || typeof unit !== "object") return String(unit || "").trim();
  const abbreviation = String(unit.abbreviation || "").trim();
  if (unit.useAbbreviation && abbreviation) return String((plural && unit.pluralAbbreviation) || abbreviation).trim();
  return String((plural && unit.pluralName) || unit.name || abbreviation).trim();
}

function ingredientFoodLabel(food, plural) {
  if (!food || typeof food !== "object") return String(food || "").trim();
  return String((plural && food.pluralName) || food.name || "").trim();
}

// Alle ingrediënten van de week, voor de lijst en het mandje. De hoeveelheden zijn al omgerekend naar de porties van
// elke maaltijd (mealie.py). Hetzelfde ingrediënt met dezelfde eenheid telt op (2x 400 g spaghetti = 800 g); met een
// andere eenheid blijft het een eigen regel. Een regel zonder aantal ("peper") staat er alleen in als het ingrediënt er
// nog niet met een aantal in staat. Een regel zonder ingrediënt (alleen tekst) staat er één keer in.
function aggregateIngredients(entries) {
  const rows = [];
  const byFood = new Map(); // ingrediënt -> zijn regels
  for (const entry of entries || []) {
    for (const ingredient of entry.recipeDetail?.recipeIngredient || []) {
      const name = String(ingredient.food?.name || ingredient.display || "").trim();
      if (!name) continue;
      const key = name.toLowerCase();
      const amount = ingredient.food?.name && typeof ingredient.quantity === "number" && Number.isFinite(ingredient.quantity) && ingredient.quantity > 0
        ? ingredient.quantity : null;
      const unit = String(ingredient.unit?.abbreviation || ingredient.unit?.name || "").trim().toLowerCase();
      const group = byFood.get(key) || [];
      if (!byFood.has(key)) byFood.set(key, group);
      if (amount === null) {
        if (!group.length) {
          const row = { ingredient, unit, total: null, count: 1 };
          group.push(row);
          rows.push(row);
        }
        continue;
      }
      const same = group.find((row) => row.total !== null && row.unit === unit);
      if (same) {
        same.total += amount;
        same.count += 1;
      } else if (group.length === 1 && group[0].total === null) {
        Object.assign(group[0], { ingredient, unit, total: amount, count: 1 }); // een aantal zegt meer dan geen aantal
      } else {
        const row = { ingredient, unit, total: amount, count: 1 };
        group.push(row);
        rows.push(row);
      }
    }
  }
  return rows.map(({ ingredient, total, count }) => {
    if (count < 2) return ingredient;
    const quantity = Math.round(total * 10000) / 10000;
    const unitLabel = ingredientUnitLabel(ingredient.unit, quantity > 1);
    const food = ingredientFoodLabel(ingredient.food, quantity > 1 && !unitLabel);
    const display = [I18N.formatNumber(Math.round(quantity * 100) / 100), unitLabel, food].filter(Boolean).join(" ");
    // De notitie ("fijngesneden") hoort bij één recept; bij het optellen valt ze weg.
    return { ...ingredient, quantity, note: "", display };
  });
}

// "4 porties" uit de planner van Mealie (recipeScale, zie mealie.py). Weet Mealie de porties van het recept niet, dan
// alleen de schaal ("×1,5"), en die alleen als hij niet 1 is.
function mealServings(entry) {
  const servings = entry.servings;
  if (!servings) return "";
  if (servings.planned) return tn("plan.servings", servings.planned);
  return servings.scale && servings.scale !== 1 ? t("plan.scale", { n: I18N.formatNumber(servings.scale) }) : "";
}

// ---- Calorieën (issue #283): per portie (per persoon), uit Mealie of de winkel, en voor de geplande porties samen. Elke
// waarde staat in een eigen <span class="kcal">, zodat Calorieën tonen (html.no-calories, zie theme.js) ze kan verbergen; de
// punt ervoor komt dan uit CSS (kcal--after), zodat er geen losse punt blijft staan.
function kcalSpan(key, kcal, after = false) {
  if (!kcal) return "";
  return `<span class="kcal${after ? " kcal--after" : ""}">${escapeHtml(t(key, { kcal: I18N.formatNumber(Math.round(kcal)) }))}</span>`;
}

// Een regel met feiten ("4 porties · 30 min") als HTML, met daarachter de calorieën (`[sleutel, kcal]`, lege vallen weg).
function factsWithKcal(parts, kcals = []) {
  const text = escapeHtml(parts.filter(Boolean).join(" \u00b7 "));
  const shown = kcals.filter(([, value]) => value);
  return text + shown.map(([key, value], index) => kcalSpan(key, value, Boolean(text) || index > 0)).join("");
}

// De kcal per portie van een recept: `kcal` uit Mealie, anders de energie uit de voedingswaarden van de winkel.
function recipeKcal(recipe) {
  const value = recipe?.kcal ?? (recipe?.nutrition || []).find((item) => item?.key === "calories")?.quantity;
  return Number(value) > 0 ? Number(value) : null;
}

// ---- Voedingswaarden (vervolg op #283): per portie uit Mealie of de winkel, als `[{key, quantity, unit}]` (zie
// mealieshopper/nutrition.py). Natrium (mg) tonen we als zout in gram, zoals op een verpakking: zout = natrium x 2,5.
const SODIUM_MG_PER_SALT_G = 400;

// De rijen van de tabel in de volgorde van een etiket: [veld, naam, ingesprongen ("waarvan ...")]. `salt` komt uit natrium.
const NUTRITION_ROWS = [
  ["calories", "nutrition.calories", false],
  ["proteinContent", "nutrition.proteinContent", false],
  ["fatContent", "nutrition.fatContent", false],
  ["saturatedFatContent", "nutrition.saturatedFatContent", true],
  ["unsaturatedFatContent", "nutrition.unsaturatedFatContent", true],
  ["transFatContent", "nutrition.transFatContent", true],
  ["carbohydrateContent", "nutrition.carbohydrateContent", false],
  ["sugarContent", "nutrition.sugarContent", true],
  ["fiberContent", "nutrition.fiberContent", false],
  ["salt", "nutrition.salt", false],
  ["cholesterolContent", "nutrition.cholesterolContent", false],
];

// De waarden als `{veld: getal}`, met `salt` (g) uit `sodiumContent` (mg).
function nutritionValues(items) {
  const values = {};
  for (const item of items || []) {
    if (item?.key && Number.isFinite(Number(item.quantity))) values[item.key] = Number(item.quantity);
  }
  if (values.sodiumContent != null) values.salt = values.sodiumContent / SODIUM_MG_PER_SALT_G;
  return values;
}

// Een hoeveelheid met eenheid: kcal heel, gram met een decimaal onder de 10, zout met twee decimalen onder de 1 g.
function nutrientAmount(key, value) {
  const amount = Number(value);
  if (value == null || !Number.isFinite(amount)) return "";
  if (key === "calories") return `${I18N.formatNumber(Math.round(amount))} kcal`;
  const digits = key === "salt" && amount < 1 ? 2 : amount < 10 ? 1 : 0;
  const factor = 10 ** digits;
  return `${I18N.formatNumber(Math.round(amount * factor) / factor)} ${key === "cholesterolContent" ? "mg" : "g"}`;
}

// De tabel "Voedingswaarden" onder een recept: per portie, en met `servings` (geplande porties, niet 1) ook het totaal.
function nutritionTable(items, { servings = null, headingLevel = 4 } = {}) {
  const values = nutritionValues(items);
  const rows = NUTRITION_ROWS.filter(([key]) => values[key] != null);
  if (!rows.length) return "";
  const count = Number(servings) > 0 && Number(servings) !== 1 ? Number(servings) : 0;
  const head = `<tr><th scope="col"><span class="visually-hidden">${escapeHtml(t("nutrition.title"))}</span></th><th scope="col">${escapeHtml(t("nutrition.per_serving"))}</th>${
    count ? `<th scope="col">${escapeHtml(tn("nutrition.total", count))}</th>` : ""
  }</tr>`;
  const body = rows
    .map(([key, label, sub]) => `<tr${sub ? ' class="nutrition-table__sub"' : ""}><th scope="row">${escapeHtml(t(label))}</th><td>${escapeHtml(nutrientAmount(key, values[key]))}</td>${
      count ? `<td>${escapeHtml(nutrientAmount(key, values[key] * count))}</td>` : ""
    }</tr>`)
    .join("");
  return `<section class="nutrition-facts"><h${headingLevel} class="nutrition-facts__title">${escapeHtml(t("nutrition.title"))}</h${headingLevel}>
    <table class="nutrition-table"><thead>${head}</thead><tbody>${body}</tbody></table></section>`;
}

// "27 g eiwit", "6 g suiker" en "0,5 g zout" uit de waarden (nutritionValues), voor zover bekend.
function nutritionBriefParts(values) {
  return [
    ["proteinContent", "nutrition.brief_protein"],
    ["sugarContent", "nutrition.brief_sugar"],
    ["salt", "nutrition.brief_salt"],
  ]
    .filter(([key]) => values[key] != null)
    .map(([key, label]) => t(label, { amount: nutrientAmount(key, values[key]) }));
}

// Kort, voor een kaart: "27 g eiwit · 6 g suiker · 0,5 g zout" achter de kcal (`after`: met de punt ervoor, zoals kcalSpan).
function nutritionBrief(items, after = true) {
  const parts = nutritionBriefParts(nutritionValues(items)).map((part) => `<span class="nutrition-brief__part">${escapeHtml(part)}</span>`);
  return parts.length ? `<span class="kcal nutrition-brief${after ? " kcal--after" : ""}">${parts.join(" \u00b7 ")}</span>` : "";
}

// Wat één persoon op een dag binnenkrijgt: de som per portie van de maaltijden met een recept. Een notitie telt niet mee; een
// recept zonder voedingswaarden telt in `without`.
function dayNutrition(entries) {
  const totals = {};
  let without = 0;
  for (const entry of entries || []) {
    if (!entry.recipe && !entry.recipeId) continue;
    const values = nutritionValues(entry.nutrition);
    if (!Object.keys(values).length) {
      without += 1;
      continue;
    }
    for (const [key, value] of Object.entries(values)) totals[key] = (totals[key] || 0) + value;
  }
  return { totals, without };
}

// De regel onder een dag in het weekmenu: "Per persoon: 1.850 kcal · 85 g eiwit · ...", of "" zonder voedingswaarden.
function dayNutritionLine(entries) {
  const { totals, without } = dayNutrition(entries);
  if (!Object.keys(totals).length) return "";
  const parts = [totals.calories != null ? nutrientAmount("calories", totals.calories) : "", ...nutritionBriefParts(totals)].filter(Boolean);
  const note = without ? ` ${tn("nutrition.day_without", without)}` : "";
  return `<p class="day-card__nutrition">${escapeHtml(t("nutrition.day_total", { values: parts.join(" \u00b7 ") }) + note)}</p>`;
}

// Of de calorieën en voedingswaarden te zien zijn (Instellingen, Voorkeuren, Weergave); zonder theme.js (in de tests) wel.
function caloriesShown() {
  return typeof THEME === "undefined" || THEME.appearance().showCalories !== false;
}

// De filters op voedingswaarden: [naam in het verzoek, decimalen]. De velden heten #<scope>-<naam>-min en -max.
const NUTRITION_FILTERS = [["kcal", 0], ["protein", 0], ["fat", 0], ["carbs", 0], ["sugar", 0], ["fiber", 0], ["salt", 1]];

// De grenzen uit de velden van een scherm (`plan` of `search`): `kcalMin=..&saltMax=..` voor de zoekroute, of "" zonder filter.
function nutritionQuery(scope) {
  if (!caloriesShown()) return "";
  const params = [];
  for (const [name, digits] of NUTRITION_FILTERS) {
    for (const side of ["min", "max"]) {
      const value = String(document.getElementById(`${scope}-${name}-${side}`)?.value ?? "").trim().replace(",", ".");
      if (value === "" || !Number.isFinite(Number(value))) continue;
      const factor = 10 ** digits;
      params.push(`${name}${side === "min" ? "Min" : "Max"}=${encodeURIComponent(Math.max(0, Math.round(Number(value) * factor) / factor))}`);
    }
  }
  return params.join("&");
}

// Wat er bij een zoekopdracht op voedingswaarden nog bij hoort: de cache vult zich nog, of recepten zonder die waarden vielen weg.
function kcalSearchNote(answer) {
  if (answer?.indexing) return t("kcal.indexing", { done: I18N.formatNumber(answer.indexing.done), total: I18N.formatNumber(answer.indexing.total) });
  const missing = answer?.withoutValues ?? answer?.withoutKcal;
  return missing ? tn("kcal.without", missing) : "";
}

// De volgorde van de maaltijden op een dag, zoals Mealie ze ook toont.
const MEAL_ORDER = ["breakfast", "lunch", "dinner", "side", "snack", "drink", "dessert"];

function mealName(entry) {
  return entry.recipeDetail?.name || entry.recipe?.name || entry.title || "-";
}

function renderMeal(entry) {
  const note = !entry.recipe && !entry.recipeId;
  const facts = factsWithKcal(
    [mealServings(entry), entry.recipeDetail ? tn("plan.ingredients_count", (entry.recipeDetail.recipeIngredient || []).length) : ""],
    [["kcal.per_serving", entry.kcal?.perServing]],
  );
  const detail = note
    ? entry.text ? `<small class="meal__note">${escapeHtml(entry.text)}</small>` : ""
    : facts ? `<small>${facts}</small>` : "";
  const content = `
      ${entry.imageUrl ? `<img class="meal__image" src="${escapeHtml(entry.imageUrl)}" alt="" width="64" height="64" loading="lazy" data-hide-on-error>` : ""}
      <span class="meal__text">
        <span class="meal__type">${escapeHtml(mealLabel(entry.entryType))}</span>
        <strong>${escapeHtml(mealName(entry))}</strong>
        ${detail}
      </span>`;
  const classes = `meal${entry.imageUrl ? " meal--with-image" : ""}${note ? " meal--note" : ""}`;
  if (entry.id == null) return `<div class="${classes}">${content}</div>`;
  // De hele maaltijd is een groot tikvlak dat het recept opent (openMealRecipe); het potlood rechtsboven wijzigt of
  // verwijdert haar. Een notitie heeft geen recept: een tik daarop wijzigt haar meteen.
  const id = escapeHtml(entry.id);
  const open = note
    ? `data-plan-edit="${id}" title="${escapeHtml(t("plan.edit"))}"`
    : `data-plan-view="${id}" title="${escapeHtml(t("plan.view"))}"`;
  return `<div class="${classes} meal--planned">
      <button class="meal__open" type="button" ${open}>${content}</button>
      <button class="meal__edit" type="button" data-plan-edit="${id}" title="${escapeHtml(t("plan.edit"))}" aria-label="${escapeHtml(t("plan.edit_meal", { name: mealName(entry) }))}">${mdiIcon(MDI_PENCIL_OUTLINE)}</button>
    </div>`;
}

// ---- Het recept van een maaltijd in een venster: wat de week al van Mealie kreeg (recipeDetail, met de hoeveelheden
// voor de geplande porties), zonder nieuw verzoek. Wijzigen opent vanuit het venster het plan-venster. De ingrediënten en
// de bereiding staan in de kookweergave (renderCookView in recipe_view.js); de sleutel mealie:<slug> is dezelfde als bij
// Recepten zoeken, zodat de voortgang van een recept op beide plekken dezelfde is.
const mealRecipe = { entry: null };

function renderMealRecipeBody(entry) {
  const detail = entry.recipeDetail;
  if (!detail) return `<p class="muted">${escapeHtml(t("plan.view_unavailable"))}</p>`;
  const description = String(detail.description || "").trim();
  const slug = entry.recipe?.slug || detail.slug;
  const recipe = cookRecipeFromMealie(detail, slug ? `mealie:${slug}` : `plan:${entry.id}`, mealName(entry));
  return `
    ${description ? `<p class="meal-recipe__description">${escapeHtml(description)}</p>` : ""}
    ${renderCookView(recipe, { headingLevel: 4, noIngredients: t("rsearch.no_ingredients_mealie"), noSteps: t("rsearch.no_steps_mealie") })}
    ${nutritionTable(entry.nutrition, { servings: entry.servings?.planned, headingLevel: 4 })}`;
}

function openMealRecipe(entry) {
  mealRecipe.entry = entry;
  const dialog = $("#meal-recipe-dialog");
  $("#meal-recipe-type").textContent = `${mealLabel(entry.entryType)} · ${formatDate(entry.date)}`;
  $("#meal-recipe-title").textContent = mealName(entry);
  // Het totaal alleen als het iets toevoegt: bij één portie is het gelijk aan de kcal per portie.
  const total = entry.kcal?.total && entry.kcal.total !== entry.kcal.perServing ? entry.kcal.total : null;
  const facts = factsWithKcal(
    [mealServings(entry), entry.recipeMinutes ? t("rsearch.minutes", { count: entry.recipeMinutes }) : ""],
    [["kcal.per_serving", entry.kcal?.perServing], ["kcal.total", total]],
  );
  $("#meal-recipe-facts").innerHTML = facts;
  $("#meal-recipe-facts").hidden = !facts;
  const image = $("#meal-recipe-image");
  image.hidden = !entry.imageUrl;
  if (entry.imageUrl) image.src = entry.imageUrl;
  else image.removeAttribute("src");
  const link = $("#meal-recipe-mealie");
  link.hidden = !entry.recipeUrl;
  if (entry.recipeUrl) link.href = entry.recipeUrl;
  else link.removeAttribute("href");
  $("#meal-recipe-body").innerHTML = renderMealRecipeBody(entry);
  if (!dialog.open) dialog.showModal();
  dialog.scrollTop = 0;
  if (typeof cookWakeUpdate === "function") cookWakeUpdate(); // "Scherm aan laten" (recipe_view.js)
}

function renderMealPlan(entries) {
  const byDay = {};
  for (const entry of entries) {
    byDay[entry.date] ||= [];
    byDay[entry.date].push(entry);
  }
  // Altijd de hele week, ook dagen zonder maaltijd: daar plan je er een in.
  const dates = [...new Set([...getWeekBounds(state.weekOffset).days, ...Object.keys(byDay)])].sort();
  const today = localIsoDate(new Date());
  const rank = (entry) => (MEAL_ORDER.indexOf(entry.entryType) + 1 || MEAL_ORDER.length + 1);
  // Toevoegen: op een lege dag de enige handeling, dus een volle knop onderaan; op een dag met maaltijden een kleine "+" in de
  // kop, zodat de dag niet twee keer zo hoog wordt. Het aria-label noemt de dag in beide gevallen.
  const addButton = (date, compact) => {
    const label = escapeHtml(t("plan.add_on", { day: formatDate(date) }));
    return compact
      ? `<button class="day-card__add day-card__add--compact" type="button" data-plan-add="${date}" aria-label="${label}" title="${escapeHtml(t("plan.add"))}">${mdiIcon(MDI_PLUS, "")}</button>`
      : `<button class="day-card__add" type="button" data-plan-add="${date}" aria-label="${label}">${mdiIcon(MDI_PLUS, "")}<span>${escapeHtml(t("plan.add"))}</span></button>`;
  };
  $("#meal-days").innerHTML = dates
    .map(
      (date) => `
        <article class="day-card${date === today ? " day-card--today" : ""}${byDay[date] ? "" : " day-card--empty"}">
          <div class="day-card__head">
            <h3>${escapeHtml(formatDate(date))}${date === today ? `<span class="day-card__today">${escapeHtml(t("plan.today"))}</span>` : ""}</h3>
            ${byDay[date] ? addButton(date, true) : ""}
          </div>
          ${(byDay[date] || []).sort((a, b) => rank(a) - rank(b)).map(renderMeal).join("")}
          ${dayNutritionLine(byDay[date])}
          ${byDay[date] ? "" : addButton(date, false)}
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
  message("#planner-message", "");
  $("#ingredients-panel").innerHTML = ingredients.length
    ? `
      <div class="box ingredients">
        <h2>${escapeHtml(t("plan.all_ingredients", { count: I18N.formatNumber(ingredients.length) }))}</h2>
        <ul>
          ${ingredients.map((item) => `<li>${escapeHtml(item.display || item.food?.name || "-")}</li>`).join("")}
        </ul>
        <div class="toolbar ingredients__actions">
          <span id="cart-store-slot"></span>
          <label class="switch-row cart-organic" title="${escapeHtml(t("cart.organic_toggle_title"))}">
            <input type="checkbox" id="cart-organic"${state.auth?.preferOrganic ? " checked" : ""}>
            <span>${escapeHtml(t("cart.organic_toggle"))}</span>
          </label>
          <button class="button" id="add-cart">${escapeHtml(cartButtonLabel())}</button>
        </div>
      </div>
    `
    : "";
  $("#add-cart")?.addEventListener("click", addToCart);
  $("#cart-organic")?.addEventListener("change", (event) => savePreferOrganic(event.target));
  renderStoreChoice();
}

// *Liever biologisch* (#218): per gebruiker bewaard in het profiel, zodat de schakelaar de volgende keer nog zo staat.
async function savePreferOrganic(input) {
  const wanted = input.checked;
  state.auth = { ...state.auth, preferOrganic: wanted };
  try {
    const data = await jsonFetch("/api/profile/preferences", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ preferOrganic: wanted }),
    });
    state.auth = { ...state.auth, preferOrganic: Boolean(data.preferOrganic) };
  } catch (error) {
    // Niet bewaard: de schakelaar geldt dan alleen nu, en de gebruiker ziet waarom.
    message("#planner-message", t("cart.organic_save_failed", { error: error.message }), "error");
  }
}

// Een weekmenu verandert zelden: elke week wordt na het ophalen bewaard, zodat bladeren door de weken direct gaat.
// Synchroniseren (de knop naast de week) haalt de week opnieuw op; na MEAL_PLAN_TTL_MS gebeurt dat vanzelf.
const MEAL_PLAN_TTL_MS = 30 * 60 * 1000;
const mealPlanCache = new Map(); // begin van de week -> { entries, at }
let mealPlanRequest = 0;

function cachedMealPlan(week) {
  const cached = mealPlanCache.get(week.start);
  return cached && Date.now() - cached.at < MEAL_PLAN_TTL_MS ? cached.entries : null;
}

const mealPlanInflight = new Map(); // begin van de week -> lopend verzoek, zodat een week niet dubbel wordt opgehaald

function fetchMealPlan(week) {
  if (!mealPlanInflight.has(week.start)) {
    // Is de week intussen vergeten (er is iets gepland), dan bewaart dit verzoek zijn (oudere) antwoord niet meer.
    const request = jsonFetch(`/api/mealplan?start=${week.start}&end=${week.end}`)
      .then((entries) => {
        if (mealPlanInflight.get(week.start) === request) mealPlanCache.set(week.start, { entries, at: Date.now() });
        return entries;
      })
      .finally(() => {
        if (mealPlanInflight.get(week.start) === request) mealPlanInflight.delete(week.start);
      });
    mealPlanInflight.set(week.start, request);
  }
  return mealPlanInflight.get(week.start);
}

// Na een wijziging in het weekmenu: de week van elke geraakte dag opnieuw ophalen, en de getoonde week zonder flikkeren vernieuwen.
async function refreshMealPlanAfterChange(dates) {
  for (const date of dates.filter(Boolean)) {
    const start = localIsoDate(weekStartOf(new Date(`${date}T00:00:00`)));
    mealPlanCache.delete(start);
    mealPlanInflight.delete(start);
  }
  if (!state.planAutoLoaded) return;
  const week = getWeekBounds(state.weekOffset);
  if (mealPlanCache.has(week.start)) return;
  const request = ++mealPlanRequest;
  try {
    const entries = await fetchMealPlan(week);
    if (request !== mealPlanRequest) return;
    state.entries = entries;
    renderMealPlan(entries);
  } catch (error) {
    if (request === mealPlanRequest) message("#planner-message", error.message);
  }
}

// De twee weken na de getoonde week alvast ophalen, een voor een en zonder foutmelding: ze zijn er pas straks nodig.
async function prefetchMealPlans(request) {
  for (const offset of [1, 2]) {
    const week = getWeekBounds(state.weekOffset + offset);
    if (cachedMealPlan(week)) continue;
    try {
      await fetchMealPlan(week);
    } catch {
      return;
    }
    if (request !== mealPlanRequest) return;
  }
}

// Terug in het weekmenu: een week die nog niet (of niet meer) binnen is, alsnog ophalen.
function ensureMealPlan() {
  if (state.planAutoLoaded && !cachedMealPlan(getWeekBounds(state.weekOffset))) loadMealPlan();
}

async function loadMealPlan({ force = false } = {}) {
  const button = $("#load-plan");
  const week = getWeekBounds(state.weekOffset);
  const request = ++mealPlanRequest;
  message("#planner-message", "");
  $("#cart-result").innerHTML = "";
  const cached = force ? null : cachedMealPlan(week);
  if (cached) {
    state.entries = cached;
    renderMealPlan(cached);
    setBusy(button, false);
    prefetchMealPlans(request);
    return;
  }
  state.entries = null;
  $("#meal-days").innerHTML = "";
  $("#ingredients-panel").innerHTML = "";
  message("#planner-message", t("plan.loading"), "success");
  setBusy(button, true);
  try {
    const entries = await fetchMealPlan(week);
    if (request !== mealPlanRequest) return;
    message("#planner-message", "");
    state.entries = entries;
    renderMealPlan(entries);
    prefetchMealPlans(request);
  } catch (error) {
    if (request === mealPlanRequest) message("#planner-message", error.message);
  } finally {
    if (request === mealPlanRequest) setBusy(button, false);
  }
}

// ---- Het weekmenu plannen: toevoegen, wijzigen, verwijderen en "Verras me". Elke wijziging gaat meteen naar Mealie
// (zie mealie.py); MealieShopper bewaart zelf geen planning. Daarna wordt de week opnieuw opgehaald.
const PLAN_BASE_TYPES = ["breakfast", "lunch", "dinner", "side"];
const MDI_DICE = "M19 5V19H5V5H19M19 3H5C3.9 3 3 3.9 3 5V19C3 20.1 3.9 21 5 21H19C20.1 21 21 20.1 21 19V5C21 3.9 20.1 3 19 3M7.5 6C6.7 6 6 6.7 6 7.5S6.7 9 7.5 9 9 8.3 9 7.5 8.3 6 7.5 6M16.5 15C15.7 15 15 15.7 15 16.5C15 17.3 15.7 18 16.5 18C17.3 18 18 17.3 18 16.5C18 15.7 17.3 15 16.5 15M16.5 6C15.7 6 15 6.7 15 7.5S15.7 9 16.5 9C17.3 9 18 8.3 18 7.5S17.3 6 16.5 6M12 10.5C11.2 10.5 10.5 11.2 10.5 12S11.2 13.5 12 13.5 13.5 12.8 13.5 12 12.8 10.5 12 10.5M7.5 15C6.7 15 6 15.7 6 16.5C6 17.3 6.7 18 7.5 18S9 17.3 9 16.5C9 15.7 8.3 15 7.5 15Z";
// `servings`: het aantal porties in het venster; `servingsTouched`: of iemand het veranderde (dan gaat het mee naar Mealie).
// `scaleSupported`: of de gekoppelde Mealie porties per maaltijd kent (recipeScale, vanaf 3.29).
const planDialog = {
  entry: null, recipe: null, results: [], types: null, searchTimer: 0, searchSeq: 0, target: null,
  servings: null, servingsTouched: false, scaleSupported: false,
};

// De maaltijden die de gekoppelde Mealie kent (snack, drank en dessert pas vanaf Mealie 3.7); bij een fout de vier vaste.
async function loadPlanTypes() {
  if (planDialog.types) return planDialog.types;
  try {
    const options = await jsonFetch("/api/mealplan/options");
    planDialog.types = options.entryTypes || PLAN_BASE_TYPES;
    planDialog.scaleSupported = Boolean(options.recipeScale);
    return planDialog.types;
  } catch {
    return PLAN_BASE_TYPES;
  }
}

function renderPlanTypes(types, current) {
  const options = types.includes(current) ? types : [...types, current];
  $("#plan-type").innerHTML = options
    .map((type) => `<option value="${escapeHtml(type)}"${type === current ? " selected" : ""}>${escapeHtml(mealLabel(type))}</option>`)
    .join("");
}

function planKind() {
  return document.querySelector('input[name="plan-kind"]:checked')?.value || "recipe";
}

function setPlanKind(kind) {
  document.querySelectorAll('input[name="plan-kind"]').forEach((input) => {
    input.checked = input.value === kind;
  });
  renderPlanKind();
}

function renderPlanKind() {
  const note = planKind() === "note";
  $("#plan-recipe-part").hidden = note;
  $("#plan-note-part").hidden = !note;
  if (!note && !planDialog.recipe && !planDialog.results.length) searchPlanRecipes();
}

function planRecipeRow(recipe) {
  const facts = factsWithKcal([recipe.minutes ? t("rsearch.minutes", { count: recipe.minutes }) : "", ...(recipe.tags || [])], [["kcal.per_serving", recipeKcal(recipe)]]);
  const meta = facts + nutritionBrief(recipe.nutrition, Boolean(facts));
  return `${recipe.image ? `<img src="${escapeHtml(recipe.image)}" alt="" width="48" height="48" loading="lazy" data-hide-on-error>` : `<span class="plan-recipe__placeholder" aria-hidden="true">${mdiIcon(MDI_PLUS, "")}</span>`}
    <span class="plan-recipe__text"><strong>${escapeHtml(recipe.name || recipe.slug)}</strong>${meta ? `<small>${meta}</small>` : ""}</span>`;
}

// De porties van het gekozen recept (de basis), of 0 als Mealie of de winkel die niet weet.
function planBase(recipe) {
  const base = Number(recipe?.portions);
  return Number.isFinite(base) && base > 0 ? base : 0;
}

// Porties kiezen (issue #283): alleen als Mealie het kent en de porties van het recept bekend zijn. Mealie bewaart de keuze
// als schaal van het recept (recipeScale); per persoon blijven de kcal gelijk, het totaal rekent mee.
function planServingsHtml(recipe) {
  const base = planBase(recipe);
  if (!planDialog.scaleSupported || !base) return "";
  const max = Math.min(99, Math.floor(base * 20));
  return `<div class="plan-servings">
      <span class="plan-servings__label" id="plan-servings-label">${escapeHtml(t("plan.servings_label"))}</span>
      <div class="qty">
        <button type="button" class="qty__step" data-servings-step="-1" aria-label="${escapeHtml(t("plan.servings_less"))}">${mdiIcon(MDI_MINUS)}</button>
        <input class="qty__input" id="plan-servings" type="number" inputmode="numeric" min="1" max="${max}" step="1" value="${escapeHtml(planDialog.servings || base)}" aria-labelledby="plan-servings-label">
        <button type="button" class="qty__step" data-servings-step="1" aria-label="${escapeHtml(t("plan.servings_more"))}">${mdiIcon(MDI_PLUS)}</button>
      </div>
      <span class="plan-servings__kcal" id="plan-servings-kcal">${planServingsKcal(recipe)}</span>
    </div>`;
}

function planServingsKcal(recipe) {
  const kcal = recipeKcal(recipe);
  const count = planDialog.servings || planBase(recipe);
  return kcal ? factsWithKcal([], [["kcal.per_serving", kcal], ["kcal.total", count > 1 ? kcal * count : null]]) : "";
}

// Een ander aantal porties: alleen het veld en de kcal ernaast bijwerken, zodat de focus in het veld blijft.
function setPlanServings(value) {
  const recipe = planDialog.recipe;
  const base = planBase(recipe);
  if (!base) return;
  const max = Math.min(99, Math.floor(base * 20));
  const count = Math.min(max, Math.max(1, Math.round(Number(value) || base)));
  planDialog.servings = count;
  planDialog.servingsTouched = true;
  const input = $("#plan-servings");
  if (input && Number(input.value) !== count) input.value = count;
  const kcal = $("#plan-servings-kcal");
  if (kcal) kcal.innerHTML = planServingsKcal(recipe);
}

function renderPlanRecipe() {
  const chosen = planDialog.recipe;
  $("#plan-recipe-chosen").hidden = !chosen;
  $("#plan-recipe-picker").hidden = Boolean(chosen);
  $("#plan-recipe-chosen").innerHTML = chosen
    ? `<div class="plan-recipe plan-recipe--chosen">${planRecipeRow(chosen)}</div>
       <button class="link-button" type="button" id="plan-recipe-change">${escapeHtml(t("plan.other_recipe"))}</button>
       ${planServingsHtml(chosen)}`
    : "";
  $("#plan-surprise").hidden = Boolean(planDialog.entry);
}

// Een (ander) recept gekozen: de porties beginnen bij die van het recept, of bij wat er voor deze maaltijd gepland stond.
function choosePlanRecipe(recipe, planned = null) {
  planDialog.recipe = recipe;
  planDialog.servings = Number(planned) > 0 ? Math.round(Number(planned)) : planBase(recipe) || null;
  planDialog.servingsTouched = false;
  renderPlanRecipe();
}

function renderPlanResults() {
  $("#plan-recipe-results").innerHTML = planDialog.results
    .map((recipe, index) => `<li><button class="plan-recipe" type="button" data-plan-pick="${index}">${planRecipeRow(recipe)}</button></li>`)
    .join("");
}

// Zoeken in de recepten van Mealie; zonder zoekterm de recepten op naam. Een ouder antwoord overschrijft een nieuwer niet.
// Met een grens op de calorieën (kcal per persoon, issue #283) zoekt de server in zijn cache; leest die de recepten nog in,
// dan zoekt het venster na twee seconden opnieuw.
async function searchPlanRecipes() {
  const query = $("#plan-recipe-query").value.trim();
  const kcal = nutritionQuery("plan");
  const seq = ++planDialog.searchSeq;
  clearTimeout(planDialog.searchTimer);
  message("#plan-message", "");
  try {
    const data = await jsonFetch(`/api/mealie/recipes?q=${encodeURIComponent(query)}${kcal ? `&${kcal}` : ""}`);
    if (seq !== planDialog.searchSeq) return;
    planDialog.results = data.recipes || [];
    renderPlanResults();
    const note = kcal ? kcalSearchNote(data) : "";
    if (!planDialog.results.length) {
      const none = kcal ? t("kcal.none") : query ? t("rsearch.none", { query }) : t("plan.no_recipes");
      message("#plan-message", [none, note].filter(Boolean).join(" "), "success");
    } else if (note) {
      message("#plan-message", note, "success");
    }
    if (kcal && data.indexing && $("#plan-dialog").open) planDialog.searchTimer = setTimeout(searchPlanRecipes, 2000);
  } catch (error) {
    if (seq === planDialog.searchSeq) message("#plan-message", error.message);
  }
}

// Een recept dat alleen als slug bekend is (na een import): naam en plaatje erbij halen.
async function completePlanRecipe(recipe) {
  try {
    const detail = await jsonFetch(`/api/mealie/recipes/${encodeURIComponent(recipe.slug)}`);
    if (planDialog.recipe !== recipe) return;
    choosePlanRecipe({ slug: detail.slug, recipeId: detail.recipeId, name: detail.name, image: detail.image, minutes: detail.minutes, tags: detail.tags, portions: detail.portions, kcal: detail.kcal });
  } catch (error) {
    if (planDialog.recipe === recipe) message("#plan-message", error.message);
  }
}

// `entry` wijzigt een maaltijd uit het weekmenu; `recipe` ({slug, ...}) kiest alvast een recept; `target` krijgt na het plannen de melding.
function openPlanDialog({ date = "", entry = null, recipe = null, target = null } = {}) {
  planDialog.entry = entry;
  planDialog.target = target;
  planDialog.results = [];
  planDialog.recipe = entry
    ? entry.recipe
      ? { slug: entry.recipe.slug, recipeId: entry.recipeId || entry.recipe.id, name: mealName(entry), image: entry.imageUrl, portions: entry.servings?.base, kcal: entry.kcal?.perServing }
      : null
    : recipe;
  planDialog.servings = entry?.servings?.planned ? Math.round(entry.servings.planned) : planBase(planDialog.recipe) || null;
  planDialog.servingsTouched = false;
  $("#plan-dialog-title").textContent = t(entry ? "plan.edit_title" : "plan.add_title");
  $("#plan-date").value = entry?.date || date || localIsoDate(new Date());
  $("#plan-title").value = entry && !entry.recipe ? entry.title || "" : "";
  $("#plan-text").value = entry?.text || "";
  $("#plan-recipe-query").value = "";
  $("#plan-recipe-results").innerHTML = "";
  $("#plan-delete").hidden = !entry;
  message("#plan-message", "");
  const current = entry?.entryType || "dinner";
  renderPlanTypes(planDialog.types || PLAN_BASE_TYPES, current);
  renderPlanRecipe();
  setPlanKind(entry && !entry.recipe && !entry.recipeId ? "note" : "recipe");
  if (!$("#plan-dialog").open) $("#plan-dialog").showModal();
  loadPlanTypes().then((types) => {
    if (!$("#plan-dialog").open || planDialog.entry !== entry) return;
    renderPlanTypes(types, $("#plan-type").value || current);
    if (planDialog.recipe && planDialog.scaleSupported && !$("#plan-servings")) renderPlanRecipe(); // nu weten we of porties kunnen
  });
  if (recipe?.slug && !recipe.name) completePlanRecipe(recipe);
}

// `imported` is het antwoord van de import bij een winkelrecept: dan staat er ook een link naar het recept in Mealie bij.
function planDone(dates, date, imported = null) {
  const target = planDialog.target;
  $("#plan-dialog").close();
  refreshMealPlanAfterChange(dates);
  if (!target || !date) return;
  const link = imported?.mealieUrl ? ` <a href="${escapeHtml(imported.mealieUrl)}" target="_blank" rel="noreferrer noopener">${escapeHtml(t("recipe.view_mealie"))}</a>` : "";
  target.innerHTML = `<div class="message success">${escapeHtml(t("plan.planned", { day: formatDate(date) }))}${link}</div>`;
}

function planRequest(url, method, body) {
  return jsonFetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
}

async function savePlanEntry(event) {
  event.preventDefault();
  const entry = planDialog.entry;
  const date = $("#plan-date").value;
  if (!date) {
    message("#plan-message", t("plan.need_date"));
    $("#plan-date").focus();
    return;
  }
  const body = { date, entryType: $("#plan-type").value };
  if (planKind() === "note") {
    body.title = $("#plan-title").value.trim();
    body.text = $("#plan-text").value.trim();
    body.recipeId = null;
    if (!body.title) {
      message("#plan-message", t("plan.need_title"));
      $("#plan-title").focus();
      return;
    }
  } else {
    const recipe = planDialog.recipe;
    if (!recipe) {
      message("#plan-message", t("plan.need_recipe"));
      $("#plan-recipe-query").focus();
      return;
    }
    if (recipe.recipeId) body.recipeId = recipe.recipeId;
    else if (recipe.slug) body.recipeSlug = recipe.slug;
    // De gekozen porties als schaal van het recept (Mealie 3.29+); niet aangeraakt, dan blijft wat Mealie had.
    const base = planBase(recipe);
    if (planDialog.servingsTouched && planDialog.scaleSupported && base && planDialog.servings) body.recipeScale = planDialog.servings / base;
    // Was het een notitie, dan gaan titel en tekst weg; anders blijft wat er in Mealie bij staat.
    if (entry && !entry.recipe && !entry.recipeId) Object.assign(body, { title: "", text: "" });
  }
  const button = $("#plan-save");
  // Een recept van een winkel staat nog niet in Mealie: eerst importeren (of het recept nemen dat er al staat).
  const source = planKind() === "recipe" && !body.recipeId && !body.recipeSlug ? planDialog.recipe?.source : null;
  const busyText = source ? t("plan.importing") : undefined;
  let imported = null;
  setBusy(button, true, busyText);
  try {
    if (source) {
      imported = await planRequest(`/api/${encodeURIComponent(source.store)}/recipes/${encodeURIComponent(source.id)}/import?reuse=1`, "POST");
      body.recipeSlug = imported.slug;
      // Mislukt het plannen hierna, dan importeert een tweede poging niet opnieuw.
      planDialog.recipe = { ...planDialog.recipe, slug: imported.slug, source: null };
    }
    if (entry) await planRequest(`/api/mealplan/${encodeURIComponent(entry.id)}`, "PATCH", body);
    else await planRequest("/api/mealplan", "POST", body);
    planDone([date, entry?.date], date, imported);
  } catch (error) {
    message("#plan-message", error.message);
  } finally {
    setBusy(button, false, busyText);
  }
}

async function deletePlanEntry() {
  const entry = planDialog.entry;
  if (!entry || !window.confirm(t("plan.delete_confirm", { name: mealName(entry) }))) return;
  const button = $("#plan-delete");
  setBusy(button, true);
  try {
    await planRequest(`/api/mealplan/${encodeURIComponent(entry.id)}`, "DELETE");
    planDone([entry.date]);
  } catch (error) {
    message("#plan-message", error.message);
  } finally {
    setBusy(button, false);
  }
}

// "Verras me": Mealie kiest zelf een recept volgens de planregels van het huishouden en zet het meteen in het weekmenu.
async function surprisePlanEntry() {
  const date = $("#plan-date").value;
  if (!date) {
    message("#plan-message", t("plan.need_date"));
    $("#plan-date").focus();
    return;
  }
  const button = $("#plan-surprise");
  setBusy(button, true);
  message("#plan-message", "");
  try {
    await planRequest("/api/mealplan/random", "POST", { date, entryType: $("#plan-type").value });
    planDone([date], date);
  } catch (error) {
    message("#plan-message", error.message);
  } finally {
    setBusy(button, false);
  }
}

// Een lijniconenset met ronde uiteinden; nieuwe interface-iconen volgen deze vorm.
const ICON_EXTERNAL =
  '<svg class="icon" viewBox="0 0 16 16" aria-hidden="true"><path d="M6.5 3H4a1.5 1.5 0 0 0-1.5 1.5v7A1.5 1.5 0 0 0 4 13h7a1.5 1.5 0 0 0 1.5-1.5V9"/><path d="M9.5 2.5h4v4"/><path d="M13.5 2.5 7.5 8.5"/></svg>';

function formatPrice(value) {
  const price = Number(value || 0);
  return price ? `\u20ac${price.toLocaleString(I18N.locale(), { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : "";
}

function dealText(deal) {
  // Wat de aanbieding inhoudt en tot wanneer ze geldt; een verlopen aanbieding zeggen we ronduit.
  if (deal.validity === "expired") return t("cart.deal_expired");
  const until = deal.validUntil ? new Date(deal.validUntil) : null;
  const day =
    until && !Number.isNaN(until.getTime())
      ? t("cart.deal_until", { date: until.toLocaleDateString(I18N.locale(), { day: "numeric", month: "short" }) })
      : "";
  return [deal.label || t("list.bonus"), day].filter(Boolean).join(" ");
}

function cartCandidateLabel(candidate) {
  const deal = candidate.deal ? dealText(candidate.deal) : "";
  // De prijs per kilo, liter of stuk en waarom een vergelijkingsoptie erbij staat (#178): zelfde product of goedkoper per eenheid.
  const perUnit = candidate.unitPrice ? offerPerUnitText(candidate.unitPrice) : "";
  const why = [candidate.same ? t("offers.compare.same") : "", candidate.cheaper ? t("offers.compare.cheaper") : ""].filter(Boolean).join(", ");
  const organic = candidate.organic ? t("cart.organic") : ""; // #218
  return [candidate.title, candidate.unitSize, formatPrice(candidate.price), perUnit, deal, organic, why].filter(Boolean).join(" \u2013 ");
}

// De goedkoopste optie per eenheid van een regel, als die duidelijk (2%) goedkoper is dan de gekozen; anders -1.
function reviewCheaperIndex(row, chosen) {
  const mine = row.candidates[chosen]?.unitPrice;
  if (!mine) return -1;
  // Met Liever biologisch (#218) en een biologische keuze telt alleen een goedkopere biologische: dat de gewone variant
  // goedkoper is, weet je al, en die staat er in de keuzelijst als "goedkoper per eenheid" bij.
  const organicOnly = Boolean(row.organic && row.candidates[chosen].organic);
  let best = -1;
  row.candidates.forEach((candidate, index) => {
    const unit = candidate.unitPrice;
    if (index === chosen || !unit || unit.unit !== mine.unit || unit.value >= mine.value * 0.98) return;
    if (organicOnly && !candidate.organic) return;
    if (best < 0 || unit.value < row.candidates[best].unitPrice.value) best = index;
  });
  return best;
}

function reviewCheaperHtml(row, chosen) {
  const best = reviewCheaperIndex(row, chosen);
  if (best < 0) return "";
  const candidate = row.candidates[best];
  const unit = t({ kg: "offers.unit.kg", l: "offers.unit.l" }[candidate.unitPrice.unit] || "offers.unit.stuk");
  const text = t("cart.cheaper_hint", {
    unit,
    title: [candidate.title, candidate.unitSize].filter(Boolean).join(" "),
    price: offerPerUnitText(candidate.unitPrice),
    current: offerPerUnitText(row.candidates[chosen].unitPrice),
  });
  return `${escapeHtml(text)} <button class="link-button review-cheaper-pick" type="button" data-pick="${best}">${escapeHtml(t("cart.cheaper_pick"))}</button>`;
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
  crisp: { name: "Crisp", api: "crisp", linked: "crisp", button: "plan.add_cart_crisp", ready: "cart.ready_crisp" },
};

function currentStore() {
  return STORES[state.store] || STORES.ah;
}

// De voorkeurssupermarkt van deze gebruiker (Instellingen, Voorkeuren; standaard Albert Heijn). Aanbiedingen en Winkels openen
// ermee, tot je daar zelf een andere winkel kiest. Niet te verwarren met state.preferredStore, de laatste keuze in het weekmenu.
function preferredSupermarket() {
  const store = state.auth?.preferredStore;
  return STORES[store] ? store : "ah";
}

// Weet de app al of de voorkeurssupermarkt gekoppeld is? Bij AH altijd; bij de andere zodra hun status binnen is.
function preferredSupermarketKnown() {
  const store = preferredSupermarket();
  return store === "ah" || Boolean(state.storeStatusKnown[store]);
}

// De winkel waarmee een pagina opent, uit de winkels die daar te kiezen zijn: de voorkeurssupermarkt, of AH als die (nog) niet
// gekoppeld is.
function defaultStoreOf(available) {
  const store = preferredSupermarket();
  return available.includes(store) ? store : "ah";
}

// De winkels die gekoppeld zijn; AH is altijd een keuze.
function linkedStoreKeys() {
  return Object.keys(STORES).filter((key) => key === "ah" || state.linkedStores[key]);
}

// De winkel in een adres van Aanbiedingen: weggelaten als het de winkel is waarmee de pagina toch al opent (zie routerApplyOffers).
function offersStoreParam(store) {
  return store === defaultStoreOf(linkedStoreKeys()) ? "" : store;
}

function cartButtonLabel() {
  return t(currentStore().button);
}

// Supermarkt kiezen in de planner: alleen zichtbaar zodra een tweede winkel gekoppeld is. Hetzelfde uitklapmenu
// als de winkelkeuze in Winkels.
function renderStoreChoice() {
  const available = Object.keys(STORES).filter((key) => key === "ah" || state.linkedStores[STORES[key].linked]);
  state.store = available.includes(state.preferredStore) ? state.preferredStore : "ah";
  const slot = $("#cart-store-slot");
  if (slot) {
    slot.innerHTML =
      available.length < 2
        ? ""
        : `<div class="subtab-menu" id="cart-store-menu">
            <button class="subtab subtab-menu__toggle subtab-menu__toggle--select" id="cart-store-toggle" type="button" aria-haspopup="true" aria-expanded="false" aria-controls="cart-store-list" aria-label="${escapeHtml(t("cart.store"))}"><span>${escapeHtml(STORES[state.store].name)}</span><svg class="subtab-menu__chevron" viewBox="0 0 24 24" aria-hidden="true"><path d="M7.41,8.58L12,13.17L16.59,8.58L18,10L12,16L6,10L7.41,8.58Z"/></svg></button>
            <div class="subtab-menu__list" id="cart-store-list" hidden>
              ${available.map((key) => `<button class="subtab-menu__item${state.store === key ? " is-active" : ""}" type="button" data-store="${key}">${escapeHtml(STORES[key].name)}</button>`).join("")}
            </div>
          </div>`;
    const menu = $("#cart-store-menu");
    if (menu) {
      bindSubtabMenu(menu);
      $("#cart-store-list").addEventListener("click", (event) => {
        const item = event.target.closest("[data-store]");
        if (!item) return;
        state.store = item.dataset.store;
        state.preferredStore = state.store;
        try {
          localStorage.setItem("ms-store", state.store);
        } catch {
          // Zonder opslag onthoudt de pagina de keuze alleen tot je hem herlaadt.
        }
        renderStoreChoice();
      });
    }
  }
  const button = $("#add-cart");
  if (button) button.textContent = cartButtonLabel();
  renderItemsStore();
  itemsFollowPreferred();
  if (typeof renderBasketStoreChoice === "function") renderBasketStoreChoice();
  if (typeof renderOffersStore === "function") renderOffersStore();
  if (typeof renderAppearanceStore === "function") renderAppearanceStore(); // gekoppeld of niet, bij Instellingen, Voorkeuren
}

// De winkel in de tab Winkels: AH, of Jumbo zodra die gekoppeld is.
function itemsApi() {
  return `/api/${state.itemsStore}`;
}

function itemsStoreName() {
  return STORES[state.itemsStore].name;
}

// De winkels die in de tab Winkels te kiezen zijn: AH altijd, Jumbo, Picnic en Crisp zodra ze gekoppeld zijn.
function itemsStores() {
  return ["ah", ...["jumbo", "picnic", "crisp"].filter((key) => state.linkedStores[key])];
}

const PICNIC_SUBTABS = ["ah-items-picnic", "ah-items-picnic-favorieten", "ah-items-picnic-bestellingen"];
const JUMBO_SUBTABS = ["ah-items-jumbo-recepten"];
const CRISP_SUBTABS = ["ah-items-crisp-recepten", "ah-items-crisp-favorieten", "ah-items-crisp-bestellingen"];

// Winkels: per winkel de onderdelen en hun subtab, in de volgorde van de tabs. Het onderdeel is ook het stuk in het adres
// (/stores/jumbo/lists, /stores/ah/planner); /stores/ah/receipts/<id> is een kassabon onder Bestellingen, /stores/ah/imported/<ref> een ingelezen
// oude bestelling (zie price_import.py en router.js). Elk subtab hoort bij één onderdeel, bij welke winkel ook.
const STORE_SECTIONS = {
  ah: { recipes: "ah-items-recepten", planner: "ah-items-weekplanner", lists: "ah-items-lijstjes", orders: "ah-items-bestellingen" },
  jumbo: { recipes: "ah-items-jumbo-recepten", lists: "ah-items-lijstjes", orders: "ah-items-bestellingen" },
  picnic: { recipes: "ah-items-picnic", favorites: "ah-items-picnic-favorieten", orders: "ah-items-picnic-bestellingen" },
  crisp: { recipes: "ah-items-crisp-recepten", favorites: "ah-items-crisp-favorieten", orders: "ah-items-crisp-bestellingen" },
};

function itemsOpenSubtab() {
  return document.querySelector("#ah-items .subtab.is-active[data-subtab]")?.dataset.subtab || "";
}

// Het onderdeel van een subtab: "lists" voor ah-items-lijstjes, "orders" voor ah-items-picnic-bestellingen.
function itemsSection(subtab) {
  for (const sections of Object.values(STORE_SECTIONS)) {
    const found = Object.entries(sections).find(([, id]) => id === subtab);
    if (found) return found[0];
  }
  return "";
}

// Het subtab van een onderdeel bij een winkel. Heeft de winkel dat onderdeel niet (Lijstjes bij Picnic, Favorieten bij
// AH), dan zijn eerste tab.
function itemsSubtab(store, section) {
  const sections = STORE_SECTIONS[store] || STORE_SECTIONS.ah;
  return sections[section] || Object.values(sections)[0];
}

// Waar de winkelkeuze heen gaat: hetzelfde onderdeel als nu open staat, zoals bij Aanbiedingen.
function itemsStorePath(store) {
  return storesPath(store, itemsSection(itemsSubtab(store, itemsSection(itemsOpenSubtab()))));
}

function renderItemsStore() {
  const stores = itemsStores();
  if (!stores.includes(state.itemsStore)) state.itemsStore = "ah";
  // De winkelkeuze is het eerste uitklapmenu in de subtabbalk; met maar één winkel valt er niets te kiezen.
  $("#items-store-menu").hidden = stores.length < 2;
  $("#items-store-name").textContent = itemsStoreName();
  $("#items-store-list").innerHTML = stores
    .map(
      (key) =>
        `<a class="subtab-menu__item${state.itemsStore === key ? " is-active" : ""}" data-store="${key}" href="${escapeHtml(itemsStorePath(key))}" data-nav>${escapeHtml(STORES[key].name)}</a>`
    )
    .join("");
  // AH heeft recepten, Jumbo zijn favoriete recepten (zoeken naar Jumbo-recepten staat bij Recepten zoeken); lijstjes en bestellingen zijn er voor AH en Jumbo; Picnic heeft recepten, favorieten en bestellingen; Crisp je bewaarde recepten, favorieten en bestellingen.
  const picnic = state.itemsStore === "picnic";
  const crisp = state.itemsStore === "crisp";
  // Picnic en Crisp hebben eigen tabs voor hun bestellingen; de lijstjes en bestellingen van AH en Jumbo horen daar niet bij.
  const ownTabs = picnic || crisp;
  const hide = (subtab, hidden) =>
    document.querySelectorAll(`[data-subtab="${subtab}"]`).forEach((button) => {
      button.hidden = hidden;
    });
  hide("ah-items-recepten", state.itemsStore !== "ah");
  hide("ah-items-weekplanner", state.itemsStore !== "ah"); // de weekplanner van de AH-app (ah_planner.js)
  hide("ah-items-lijstjes", ownTabs);
  hide("ah-items-bestellingen", ownTabs);
  PICNIC_SUBTABS.forEach((subtab) => hide(subtab, !picnic));
  JUMBO_SUBTABS.forEach((subtab) => hide(subtab, state.itemsStore !== "jumbo"));
  CRISP_SUBTABS.forEach((subtab) => hide(subtab, !crisp));
  // Staat de open tab niet meer bij de gekozen winkel (bijvoorbeeld na het ontkoppelen), ga dan naar hetzelfde onderdeel bij
  // deze winkel, of naar zijn eerste tab.
  const open = itemsOpenSubtab();
  if (open && document.querySelector(`#ah-items .subtab[data-subtab="${open}"]`).hidden) {
    document.querySelector(`#ah-items .subtab[data-subtab="${itemsSubtab(state.itemsStore, itemsSection(open))}"]`)?.click();
  }
  if (typeof routerRetry === "function") routerRetry();
}

// Een andere winkel kiezen. Het onderdeel dat open staat (Recepten, Lijstjes, Bestellingen of Favorieten) blijft open, zoals
// bij Aanbiedingen; heeft de nieuwe winkel het niet, dan opent zijn eerste tab. Een adres kan een ander onderdeel vragen.
// `chosen`: de gebruiker koos hem zelf (menu of adres); anders is het de voorkeurssupermarkt (itemsFollowPreferred).
function switchItemsStore(store, section = itemsSection(itemsOpenSubtab()), { chosen = true } = {}) {
  state.itemsStoreChosen = chosen;
  state.itemsStore = itemsStores().includes(store) ? store : "ah";
  // Een andere winkel heeft andere lijstjes en bestellingen: begin schoon. Ze laden opnieuw als hun tab hieronder opent.
  state.ahListsLoaded = false;
  state.ahLists = [];
  state.ahListHits = {};
  state.ahListQuery = "";
  $("#ah-list-filter").value = "";
  // Een open lijstje dicht zonder stap terug in de browser: die zou het adres van de vorige winkel weer openen.
  state.ahListFromOverview = false;
  showAhListsOverview({ restoreScroll: false });
  renderAhLists();
  if (typeof resetOrders === "function") resetOrders();
  // Eerst de tab openen en dan de balk tekenen: zo laadt alleen deze tab, een keer, en krijgt het adres een stap. Andersom
  // ging renderItemsStore eerst nog naar een andere tab, als de open tab bij deze winkel niet bestaat.
  document.querySelector(`#ah-items .subtab[data-subtab="${itemsSubtab(state.itemsStore, section)}"]`)?.click();
  renderItemsStore();
}

// Winkels opent met de voorkeurssupermarkt, tot je zelf een winkel kiest. Alleen terwijl Winkels open staat: een wissel opent
// een tab, en die laadt de recepten, lijstjes of bestellingen van die winkel. Wacht een adres nog op een koppeling
// (router.pending), dan beslist dat adres.
function itemsFollowPreferred() {
  if (state.itemsStoreChosen || (typeof router !== "undefined" && router.pending)) return;
  if (!document.querySelector("#ah-items.is-active")) return;
  const store = defaultStoreOf(itemsStores());
  if (store !== state.itemsStore) switchItemsStore(store, itemsSection(itemsOpenSubtab()), { chosen: false });
}

// Een andere voorkeurssupermarkt (Instellingen, Voorkeuren): Aanbiedingen en Winkels openen voortaan daarmee, ook als je er
// eerder zelf een andere winkel koos.
function preferredSupermarketChanged() {
  if (typeof routerRetry === "function") routerRetry(); // een adres dat nog op een koppeling wachtte, is niet meer open
  state.itemsStoreChosen = false;
  if (typeof offersState !== "undefined") offersState.chosen = false;
  itemsFollowPreferred();
  if (typeof offersFollowPreferred === "function") offersFollowPreferred();
  if (typeof routerRefreshLinks === "function") routerRefreshLinks();
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
  // Picnic, Jumbo en Crisp melden zelf ook welke producten ze niet in het mandje kregen (of al op het lijstje stonden).
  const names = state.store === "ah" ? notFound : [...notFound, ...(data.skippedItems || [])];
  const missing = names.length
    ? th("cart.missing", { count: names.length, names: names.join(", ") })
    : "";
  const ready = currentStore().ready;
  $("#cart-result").innerHTML = `
    <div class="message success">${th(ready, { added: data.added }).replace("{missing}", missing)}</div>
    ${lines ? `<ul class="cart-lines">${lines}</ul>` : ""}
  `;
  // Picnic, Jumbo en Crisp vullen het echte mandje: laat zien waar je het kunt bekijken en aanpassen.
  if (["picnic", "jumbo", "crisp"].includes(state.store)) {
    $("#cart-result").insertAdjacentHTML(
      "beforeend",
      `<p><button class="button button--secondary" id="view-basket" type="button">${escapeHtml(t("basket.view"))}</button></p>`
    );
    $("#view-basket").addEventListener("click", () => openBasket(state.store));
  }
}

async function submitCart(selection, notFound) {
  const data = await jsonFetch(`/api/${currentStore().api}/cart`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ items: selection }),
  });
  showCartResult(data, notFound);
}

function reviewSearchHtml(row, extra) {
  // Zoekveld bij een regel. Bij een niet gevonden regel staat het er direct; bij een gevonden regel klapt het uit
  // onder "Zoek een ander product", voor als het voorgestelde product niet is wat je zoekt.
  return `
    <form class="review-search${extra ? " review-search--extra" : ""}"${extra ? " hidden" : ""}>
      <input type="search" class="review-search-input" value="${escapeHtml(row.query)}" aria-label="${escapeHtml(t("cart.search_label"))}">
      <button class="button button--secondary" type="submit">${escapeHtml(t("cart.search_button"))}</button>
      <small class="review-search-status" role="status"></small>
    </form>`;
}

function reviewThumbHtml(candidate) {
  // De foto van het gekozen product, zodat je ziet wat er in het mandje komt; Picnic heeft geen productpagina, dus daar
  // is dit de enige manier om het product te zien. Tikken opent hem in het groot (openReviewPhoto). De knop blijft
  // altijd staan, zodat de kolommen gelijk blijven als er geen foto is of hij niet laadt; dan is hij uitgeschakeld.
  // De productnaam staat al in de keuzelijst, dus alt is leeg en de knop zegt wat hij doet.
  const label = escapeHtml(t("cart.photo_open", { title: candidate.title }));
  const src = candidate.image ? ` src="${escapeHtml(storeImage(candidate.image))}"` : " hidden";
  return `<button class="review-thumb" type="button" aria-label="${label}" title="${label}"${candidate.image ? "" : " disabled"}><img${src} alt="" width="56" height="56" loading="lazy"></button>`;
}

function showReviewThumb(button, candidate) {
  const img = button.querySelector("img");
  const label = t("cart.photo_open", { title: candidate.title });
  button.disabled = !candidate.image;
  button.setAttribute("aria-label", label);
  button.title = label;
  img.hidden = !candidate.image;
  if (candidate.image) img.src = storeImage(candidate.image);
  else img.removeAttribute("src");
}

function openReviewPhoto(candidate) {
  // De foto in het groot. Het grote formaat (imageLarge) geven AH en Picnic mee; laadt dat niet, of is er geen, dan
  // valt het venster terug op de gewone foto.
  const image = $("#photo-dialog-image");
  const large = candidate.imageLarge || candidate.image;
  if (!large) return;
  image.dataset.fallback = large !== candidate.image ? storeImage(candidate.image) : "";
  image.hidden = false;
  image.src = storeImage(large);
  $("#photo-dialog-title").textContent = candidate.title;
  $("#photo-dialog-meta").textContent = [candidate.unitSize, formatPrice(candidate.price)].filter(Boolean).join(" · ");
  $("#photo-dialog").showModal();
}

function reviewRowHtml(row, index) {
  if (!row.candidates.length) {
    // Niet gevonden: de gebruiker zoekt zelf verder, bijvoorbeeld onder een andere naam (paksoi heet bij AH pak choi).
    return `
      <div class="review-row is-missing" data-row="${index}">
        <input type="checkbox" disabled>
        <div class="review-ingredient">
          <strong>${escapeHtml(row.display)}</strong>
          <small class="review-note">${escapeHtml(row.error || t("cart.not_found"))}</small>
        </div>
        ${reviewSearchHtml(row, false)}
      </div>`;
  }
  const first = row.candidates[0];
  return `
    <div class="review-row${first.doubt ? " is-doubt" : ""}" data-row="${index}">
      <input type="checkbox" class="review-include" checked aria-label="${escapeHtml(t("cart.include"))}">
      <div class="review-ingredient">
        <strong>${escapeHtml(row.display)}</strong>
        <small class="review-note">${escapeHtml(first.doubt || "")}</small>
        ${row.organicMissing ? `<small class="review-organic-missing">${escapeHtml(t("cart.organic_missing"))}</small>` : ""}
        <small class="review-cheaper" role="status">${reviewCheaperHtml(row, 0)}</small>
        <button class="link-button review-search-toggle" type="button" aria-expanded="false">${escapeHtml(t("cart.search_other"))}</button>
      </div>
      ${reviewThumbHtml(first)}
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
      ${reviewSearchHtml(row, true)}
    </div>`;
}

function renderCartReview(rows) {
  const missingCount = rows.filter((row) => !row.candidates.length).length;
  const doubts = rows.filter((row) => row.candidates.length && row.candidates[0].doubt).length;
  $("#cart-result").innerHTML = `
    <div class="box cart-review">
      <h2>${escapeHtml(t("cart.review_title"))}</h2>
      <p class="muted">${escapeHtml(doubts ? tn("cart.doubts", doubts) : t("cart.review_intro"))}</p>
      ${missingCount ? `<p class="muted" id="review-missing-hint">${escapeHtml(tn("cart.missing_hint", missingCount))}</p>` : ""}
      <div class="review-list">${rows.map((row, index) => reviewRowHtml(row, index)).join("")}</div>
      <div class="cart-review__actions">
        <button class="button" id="confirm-cart"></button>
        <button class="button button--secondary" id="cancel-cart">${escapeHtml(t("common.cancel"))}</button>
      </div>
    </div>
  `;

  // De regels kunnen veranderen: een niet gevonden regel wordt na zoeken een gewone regel. Daarom steeds opnieuw zoeken.
  const includable = () => [...document.querySelectorAll(".review-row[data-row]")].filter((el) => el.querySelector(".review-include"));
  const updateConfirm = () => {
    const count = includable().filter((el) => el.querySelector(".review-include").checked).length;
    const button = $("#confirm-cart");
    button.disabled = count === 0;
    button.textContent = tn("cart.confirm", count);
  };
  const updateMissingHint = () => {
    const left = rows.filter((row) => !row.candidates.length).length;
    const hint = $("#review-missing-hint");
    if (!hint) return;
    hint.hidden = left === 0;
    if (left) hint.textContent = tn("cart.missing_hint", left);
  };

  const bindRow = (el) => {
    const row = rows[Number(el.dataset.row)];
    el.querySelector(".review-search").addEventListener("submit", (event) => {
      event.preventDefault();
      searchRow(el, row);
    });
    if (!row.candidates.length) return;
    const form = el.querySelector(".review-search");
    const toggle = el.querySelector(".review-search-toggle");
    toggle.addEventListener("click", () => {
      form.hidden = !form.hidden;
      toggle.setAttribute("aria-expanded", String(!form.hidden));
      if (!form.hidden) el.querySelector(".review-search-input").focus();
    });
    const thumb = el.querySelector(".review-thumb");
    const product = el.querySelector(".review-product");
    // Laadt de foto niet, dan verbergen we hem (en halen we hem niet weg): een ander product kiezen gebruikt hem opnieuw.
    thumb.querySelector("img").addEventListener("error", (event) => {
      event.target.hidden = true;
      thumb.disabled = true;
    });
    thumb.addEventListener("click", () => openReviewPhoto(row.candidates[Number(product.value)]));
    product.addEventListener("change", (event) => {
      const candidate = row.candidates[Number(event.target.value)];
      showReviewThumb(thumb, candidate);
      el.querySelector(".review-qty").value = candidate.packs;
      const link = el.querySelector("a.review-link");
      if (link && candidate.url) link.href = candidate.url;
      el.querySelector(".review-note").textContent = candidate.doubt || "";
      el.classList.toggle("is-doubt", Boolean(candidate.doubt));
      el.querySelector(".review-cheaper").innerHTML = reviewCheaperHtml(row, Number(event.target.value));
    });
    // "Kies": de goedkoopste optie per eenheid kiezen, alsof je hem in de keuzelijst kiest (ook het aantal verpakkingen rekent mee).
    el.querySelector(".review-cheaper").addEventListener("click", (event) => {
      const pick = event.target.closest("[data-pick]");
      if (!pick) return;
      product.value = pick.dataset.pick;
      product.dispatchEvent(new Event("change"));
      product.focus();
    });
    el.querySelector(".review-include").addEventListener("change", updateConfirm);
    el.querySelector("a.review-link")?.addEventListener("click", (event) => {
      event.preventDefault();
      openProductPopup(event.currentTarget.href);
    });
  };

  // Zoekt één regel opnieuw met de zoekterm uit het veld, voor een niet gevonden regel en voor een gevonden regel
  // waar je een ander product wilt. Alleen deze regel wordt vervangen, zodat wat je bij de andere regels hebt
  // gekozen blijft staan. Vindt de zoekopdracht niets, dan houdt de regel zijn huidige producten.
  async function searchRow(el, row) {
    const input = el.querySelector(".review-search-input");
    const button = el.querySelector(".review-search button");
    const status = el.querySelector(".review-search-status");
    const term = input.value.trim();
    if (!term) return;
    input.disabled = true;
    button.disabled = true;
    status.textContent = t("cart.searching");
    try {
      const preview = await jsonFetch(`/api/${currentStore().api}/cart/preview`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items: [{ query: term, display: row.display, quantity: row.quantity ?? null, unit: row.unit ?? null }] }),
      });
      const found = (preview.items || [])[0];
      if (found?.candidates?.length) {
        row.query = term;
        row.candidates = found.candidates;
        delete row.error;
        el.outerHTML = reviewRowHtml(row, Number(el.dataset.row));
        bindRow(document.querySelector(`.review-row[data-row="${el.dataset.row}"]`));
        updateMissingHint();
        updateConfirm();
        return;
      }
      status.textContent = found?.error || t("cart.search_none", { term });
    } catch (error) {
      status.textContent = error.message;
    }
    input.disabled = false;
    button.disabled = false;
    input.focus();
  }

  document.querySelectorAll(".review-row[data-row]").forEach(bindRow);
  updateConfirm();

  $("#cancel-cart").addEventListener("click", () => {
    $("#cart-result").innerHTML = "";
  });
  $("#confirm-cart").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    const selection = includable()
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
      await submitCart(
        selection,
        rows.filter((row) => !row.candidates.length).map((row) => row.display)
      );
    } catch (error) {
      $("#cart-result").insertAdjacentHTML(
        "afterbegin",
        `<div class="message error">${escapeHtml(error.message)}</div>`
      );
      // De knop staat in de balk onderaan; de melding staat boven de lijst en moet dus in beeld komen.
      $("#cart-result .message.error").scrollIntoView({ block: "center" });
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
        // Vergelijk de verpakkingen: na het voorgestelde product alleen hetzelfde product of wat per eenheid goedkoper is.
        compare: true,
        // Liever biologisch (#218): eerst een biologisch product van dezelfde soort, als de winkel het heeft.
        organic: Boolean(state.auth?.preferOrganic),
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
    // Het mandje gaat nooit zonder controle vol: ook zonder twijfel toont de lijst eerst wat erin komt.
    if (!rows.some((row) => row.candidates.length)) {
      $("#cart-result").innerHTML = `<div class="message error">${escapeHtml(t("cart.none_found"))}</div>`;
    } else {
      renderCartReview(rows);
    }
  } catch (error) {
    $("#cart-result").innerHTML = `<div class="message error">${escapeHtml(error.message)}</div>`;
  } finally {
    button.disabled = false;
    button.textContent = cartButtonLabel();
  }
}

// De knoppen onderaan elke receptkaart, overal gelijk: Bekijken (het recept in MealieShopper, zie openStoreRecipe) en
// Inplannen (de plan-dialoog). `open` is `winkel:id`; `plan` zijn de data-attributen van planButtonAttrs of planSourceAttrs.
// Importeren zonder plannen en de link naar de winkel staan in het detail.
function recipeCardActions(open, plan) {
  return `
      <div class="card-actions">
        <button class="button button--secondary" type="button" data-store-open="${escapeHtml(open)}">${escapeHtml(t("rsearch.open"))}</button>
        <button class="button" type="button" ${plan}>${escapeHtml(t("plan.schedule"))}</button>
      </div>
      <div class="recipe-import-result"></div>`;
}

// Inplannen bij een recept dat al in Mealie staat.
function planButtonAttrs(recipe) {
  return `data-plan-slug="${escapeHtml(recipe.slug || recipe.id)}" data-plan-id="${escapeHtml(recipe.recipeId || "")}" data-plan-name="${escapeHtml(recipe.name || "")}" data-plan-image="${escapeHtml(recipe.image || "")}"${planExtraAttrs(recipe)}`;
}

// De porties en de kcal per portie, voor het porties-veld in de plan-dialoog (issue #283); leeg als ze onbekend zijn.
function planExtraAttrs(recipe) {
  const portions = Number(recipe?.portions);
  const kcal = recipeKcal(recipe);
  return `${portions > 0 ? ` data-plan-portions="${escapeHtml(portions)}"` : ""}${kcal ? ` data-plan-kcal="${escapeHtml(Math.round(kcal))}"` : ""}`;
}

// Inplannen bij een recept van een winkel of maaltijdbox: de plan-dialoog zet het eerst in Mealie, of gebruikt het recept
// dat er al staat (zelfde bron).
function planSourceAttrs(store, id, name, image, recipe = null) {
  return `data-plan-source="${escapeHtml(`${store}:${id}`)}" data-plan-name="${escapeHtml(name || "")}" data-plan-image="${escapeHtml(image || "")}"${planExtraAttrs(recipe)}`;
}

// Het nummer van een Allerhande-recept: het id, of anders uit de link (R-R123).
function ahRecipeNumber(recipe) {
  const id = String(recipe.id ?? "");
  return /^\d+$/.test(id) ? id : /R-R(\d+)/i.exec(recipe.url || recipe.webPath || "")?.[1] || "";
}

function renderRecipeCard(recipe) {
  const imageUrl = recipe.images?.[0]?.url;
  const totalTime = Number(recipe.totalTime ?? (Number(recipe.cookTime || 0) + Number(recipe.preparationTime || 0)));
  const tags = [...(recipe.courses || []), ...(recipe.keywords || [])].slice(0, 3);
  const id = ahRecipeNumber(recipe);
  return `
    <article class="recipe-card">
      ${imageUrl ? `<img src="${escapeHtml(storeImage(imageUrl))}" alt="${escapeHtml(recipe.title)}" loading="lazy" data-hide-on-error>` : ""}
      <h3>${escapeHtml(recipe.title)}</h3>
      ${recipe.description ? `<p class="muted">${escapeHtml(recipe.description)}</p>` : ""}
      ${totalTime || recipe.servings ? `<p class="muted">${totalTime ? escapeHtml(t("recipe.minutes", { n: totalTime })) : ""}${totalTime && recipe.servings ? " | " : ""}${recipe.servings ? escapeHtml(t("recipe.servings", { n: recipe.servings })) : ""}</p>` : ""}
      ${tags.length ? `<div class="tags">${tags.map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join("")}</div>` : ""}
      ${id ? recipeCardActions(`ah:${id}`, planSourceAttrs("ah", id, recipe.title, imageUrl)) : ""}
    </article>
  `;
}

// De melding na een import in Mealie, overal hetzelfde: een link naar het recept en de knop Inplannen (zet het in het weekmenu).
function importedMessage(data) {
  const link = data.mealieUrl ? ` <a href="${escapeHtml(data.mealieUrl)}" target="_blank" rel="noreferrer noopener">${escapeHtml(t("recipe.view_mealie"))}</a>` : "";
  const plan = data.slug ? ` <button class="link-button link-button--inline" type="button" data-plan-slug="${escapeHtml(data.slug)}">${escapeHtml(t("plan.schedule"))}</button>` : "";
  return `${escapeHtml(t("recipe.imported"))}${link}${plan}`;
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
    return { ok: true, html: importedMessage(data) };
  } catch (error) {
    return { ok: false, html: error.message };
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = t("recipe.import");
    }
  }
}

// De blokken onder Instellingen > Koppelen zijn inklapbaar, in het compacte formaat van Weergave (.fold--compact). Mealie en de
// winkels klappen in zodra de koppeling er is; de AI-beoordeling staat dicht tot je hem opent, de Aanbiedingen open. Een keuze van
// de gebruiker blijft staan zolang de pagina open is. Per blok het begin van de id's van de knop en het inklapbare deel.
const LINK_FOLDS = {
  offers: "offers-sync",
  ai: "ai-login",
  mealie: "mealie-login",
  ah: "ah-login",
  jumbo: "jumbo-login",
  picnic: "picnic-login",
  crisp: "crisp-login",
};
state.storeFold = {};

function applyStoreFold(store, closed) {
  const toggle = $(`#${LINK_FOLDS[store]}-toggle`);
  if (!toggle) return;
  const open = state.storeFold[store] ?? !closed;
  toggle.setAttribute("aria-expanded", String(open));
  toggle.title = t(open ? "sec.collapse" : "sec.expand");
  $(`#${LINK_FOLDS[store]}-body`).hidden = !open;
}

for (const store of Object.keys(LINK_FOLDS)) {
  $(`#${LINK_FOLDS[store]}-toggle`).addEventListener("click", () => {
    state.storeFold[store] = $(`#${LINK_FOLDS[store]}-toggle`).getAttribute("aria-expanded") !== "true";
    applyStoreFold(store, false);
  });
}

async function refreshAhStatus() {
  if (!$("#ah-status")) return;
  try {
    const data = await jsonFetch("/api/ah/auth/status");
    renderLinkStatus("#ah-status", STORES.ah.name, data.connected ? "linked" : "unlinked");
    applyStoreFold("ah", Boolean(data.connected));
  } catch (error) {
    message("#ah-status", error.message);
  }
}

// De volledige naam van een dag (0 = zondag ... 6 = zaterdag) in de gekozen taal. 4 januari 2026 is een zondag.
function weekdayName(day) {
  return new Date(2026, 0, 4 + day).toLocaleDateString(I18N.locale(), { weekday: "long" });
}

// De keuzelijst bij Mealie: zoals in Mealie (met de dag die Mealie nu zegt) of een vaste dag, maandag eerst.
function renderWeekStartChoice(weekStart) {
  const select = $("#mealie-week-start");
  if (!select || !weekStart) return;
  const follow =
    weekStart.source === "mealie"
      ? t("mealie.week_start_mealie", { day: weekdayName(weekStart.day) })
      : weekStart.source === "default"
        ? t("mealie.week_start_unknown", { day: weekdayName(weekStart.day) })
        : t("mealie.week_start_follow");
  select.replaceChildren(new Option(follow, ""), ...[1, 2, 3, 4, 5, 6, 0].map((day) => new Option(weekdayName(day), String(day))));
  select.value = weekStart.setting || "";
}

// Waar de week begint, uit /api/mealie/status: een vaste dag van de beheerder, anders die uit Mealie, anders maandag.
// Geeft true als de week nu op een andere dag begint.
function applyWeekStart(weekStart) {
  if (!weekStart) return false;
  renderWeekStartChoice(weekStart);
  const day = Number.isInteger(weekStart.day) && weekStart.day >= 0 && weekStart.day <= 6 ? weekStart.day : 1;
  if (day === state.firstDayOfWeek) return false;
  state.firstDayOfWeek = day;
  updateWeekLabel();
  if (typeof offersWeekStartChanged === "function") offersWeekStartChanged();
  return true;
}

async function saveWeekStart(event) {
  const select = event.currentTarget;
  message("#mealie-week-start-message", "");
  select.disabled = true;
  try {
    await jsonFetch("/api/server-settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ weekStart: select.value === "" ? null : Number(select.value) }),
    });
    message("#mealie-week-start-message", t("mealie.week_start_saved"), "success");
  } catch (error) {
    message("#mealie-week-start-message", error.message);
  } finally {
    select.disabled = false;
  }
  await refreshMealieStatus();
}

// ---- Voedingswaarden van oude AH-recepten aanvullen (Instellingen, Koppelen, Mealie; alleen de beheerder, issue #283). De
// server werkt de recepten een voor een af (recipe_nutrition.py); zolang dat loopt, vraagt de pagina elke twee seconden de stand.
let nutritionBackfillTimer = 0;

function renderNutritionBackfill(status) {
  const running = status?.state === "running";
  $("#mealie-nutrition-start").hidden = running;
  $("#mealie-nutrition-stop").hidden = !running;
  const counts = {};
  for (const key of ["checked", "total", "updated", "skipped", "failed"]) counts[key] = I18N.formatNumber(Number(status?.[key]) || 0);
  const texts = {
    running: t("mealie.nutrition.running", counts),
    done: t("mealie.nutrition.done", counts),
    stopped: [t("mealie.nutrition.stopped", counts), status?.error || ""].filter(Boolean).join(" "),
    error: t("mealie.nutrition.error", { ...counts, error: status?.error || "" }),
  };
  message("#mealie-nutrition-message", texts[status?.state] || "", status?.state === "error" ? "error" : "success");
  clearTimeout(nutritionBackfillTimer);
  if (running) nutritionBackfillTimer = setTimeout(pollNutritionBackfill, 2000);
}

async function pollNutritionBackfill() {
  try {
    renderNutritionBackfill(await jsonFetch("/api/mealie/nutrition/backfill"));
  } catch (error) {
    message("#mealie-nutrition-message", error.message);
  }
}

async function startNutritionBackfill(event) {
  const button = event.currentTarget;
  setBusy(button, true);
  try {
    // Loopt het al (bijvoorbeeld gestart voor het herladen van de pagina), dan alleen de stand tonen.
    const current = await jsonFetch("/api/mealie/nutrition/backfill");
    renderNutritionBackfill(current.state === "running" ? current : await jsonFetch("/api/mealie/nutrition/backfill", { method: "POST" }));
  } catch (error) {
    message("#mealie-nutrition-message", error.message);
  } finally {
    setBusy(button, false);
  }
}

async function stopNutritionBackfill() {
  try {
    renderNutritionBackfill(await jsonFetch("/api/mealie/nutrition/backfill", { method: "DELETE" }));
  } catch (error) {
    message("#mealie-nutrition-message", error.message);
  }
}

function renderMealieStatus(data, { verified = false } = {}) {
  const setup = $("#mealie-setup");
  if (setup) setup.hidden = Boolean(data.configured);
  state.linkedStores.mealie = Boolean(data.configured); // Recepten zoeken zoekt dan ook in Mealie
  if (typeof renderAhPlannerButton === "function") renderAhPlannerButton(); // Naar weekmenu (ah_planner.js) vraagt Mealie
  // Eerst de eerste dag van de week, zodat het weekmenu meteen de goede week ophaalt.
  const weekChanged = applyWeekStart(data.weekStart);
  // Het weekmenu laadt vanzelf zodra Mealie gekoppeld is; zonder koppeling vervalt wat we onthouden hadden.
  if (!data.configured) {
    mealPlanCache.clear();
    state.planAutoLoaded = false;
  } else if (!state.planAutoLoaded) {
    state.planAutoLoaded = true;
    loadMealPlan();
  } else if (weekChanged) {
    loadMealPlan();
  }
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
  applyStoreFold("mealie", Boolean(data.configured));
  if (!data.configured) {
    renderLinkStatus("#mealie-status", "Mealie", "unlinked");
  } else if (verified && data.connected) {
    const details = [
      data.version ? t("mealie.version", { version: data.version }) : "",
      data.username ? t("mealie.user", { user: data.username }) : "",
    ]
      .filter(Boolean)
      .join(", ");
    renderLinkStatus("#mealie-status", "Mealie", "working", `: ${data.url}${details ? ` (${details})` : ""}`);
  } else if (verified) {
    message("#mealie-status", data.error || t("mealie.unexpected"), "error");
  } else {
    renderLinkStatus("#mealie-status", "Mealie", "linked");
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
      renderLinkStatus("#ah-status", STORES.ah.name, "working");
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

// De hoogte waarmee een element precies tot de onderkant van de pagina reikt, zodat het daarbinnen scrolt in plaats van de
// pagina: van waar het begint tot waar de pagina ophoudt, met wat er onder het element nog komt (de rand van het kader, de
// marge van de pagina) meegerekend. Op een scherm smaller dan `minWidth` of voor een verborgen element null: dan scrolt de
// pagina gewoon. De aankopentabel (orders.js) en de prijsgeschiedenis (offers_history.js) gebruiken dit.
function pageFitHeight(element, { minWidth = 1024, min = 240 } = {}) {
  if (!window.matchMedia(`(min-width: ${minWidth}px)`).matches || !element.offsetParent) return null;
  const rect = element.getBoundingClientRect();
  const shell = document.querySelector("main.shell").getBoundingClientRect();
  return Math.max(min, window.innerHeight - (rect.top + window.scrollY) - (shell.bottom - rect.bottom));
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
  state.ahListFromOverview = push; // geopend met een klik in het overzicht: terug is dan een stap terug in de browser
  if (push && typeof routerSync === "function") routerSync();
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
  if (state.ahListFromOverview) {
    history.back(); // router.js opent dan het overzicht weer
    return;
  }
  showAhListsOverview();
  if (typeof routerSync === "function") routerSync();
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
    ${product.image ? `<img src="${escapeHtml(storeImage(product.image))}" alt="" loading="lazy" data-hide-on-error>` : ""}
    <div>
      <strong>${editable ? "" : `${escapeHtml(item.quantity)}x `}${escapeHtml(title)}</strong>
      ${meta ? `<small>${escapeHtml(meta)}</small>` : ""}
      ${product.isBonus ? `<small class="bonus">${escapeHtml(t("list.bonus"))}</small>` : ""}
      ${price ? `<small class="product-card__price">${escapeHtml(formatPrice(price))}</small>` : ""}
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
  const image = recipe.image || "";
  const id = ahRecipeNumber(recipe);
  // De voedingswaarden per portie, zodra MealieShopper ze bij AH heeft opgehaald (ah_recipes.start_fill na Synchroniseren).
  const kcal = factsWithKcal([], [["kcal.per_serving", recipeKcal(recipe)]]);
  const meta = kcal + nutritionBrief(recipe.nutrition, Boolean(kcal));
  return `
    <article class="recipe-card"${id ? ` data-store-recipe="ah:${escapeHtml(id)}"` : ""}>
      ${image ? `<img src="${escapeHtml(storeImage(image))}" alt="${escapeHtml(recipe.title || "")}" loading="lazy" data-hide-on-error>` : ""}
      <h3>${escapeHtml(recipe.title || t("recipe.unknown"))}</h3>
      ${meta ? `<p class="muted">${meta}</p>` : ""}
      ${id ? recipeCardActions(`ah:${id}`, planSourceAttrs("ah", id, recipe.title, image, recipe)) : ""}
    </article>
  `;
}

// De keuzelijst met de receptgroepen van AH, zoals de receptenlijsten bij Jumbo: eerst alle recepten, dan elke groep.
function renderAhRecipeGroups() {
  const select = $("#ah-recipe-group");
  const groups = state.ahRecipeGroups;
  if (!groups.some((group) => group.id === state.ahRecipeGroup)) state.ahRecipeGroup = "";
  const all = `<option value="">${escapeHtml(`${t("ah.recipes.all_groups")} (${I18N.formatNumber(state.ahRecipes.length)})`)}</option>`;
  select.innerHTML =
    all +
    groups
      .map((group) => `<option value="${escapeHtml(group.id)}">${escapeHtml(`${group.name} (${I18N.formatNumber(group.count)})`)}</option>`)
      .join("");
  select.value = state.ahRecipeGroup;
  select.hidden = !groups.length; // zonder groepen valt er niets te kiezen
}

// De recepten van de gekozen groep, in de volgorde van die groep; zonder keuze alle bewaarde recepten.
function savedRecipesOfGroup() {
  const group = state.ahRecipeGroups.find((item) => item.id === state.ahRecipeGroup);
  if (!group) return state.ahRecipes;
  const byId = new Map(state.ahRecipes.map((recipe) => [String(recipe.id), recipe]));
  return (group.recipeIds || []).map((id) => byId.get(String(id))).filter(Boolean);
}

function renderSavedRecipes() {
  const query = state.ahRecipeQuery.trim();
  const all = state.ahRecipes;
  const inGroup = savedRecipesOfGroup();
  const recipes = query ? inGroup.filter((recipe) => matchesWords(recipe.title, query)) : inGroup;
  $("#ah-recipes").innerHTML = recipes.length
    ? recipes.map(renderSavedRecipeCard).join("")
    : "";
  if (typeof recipeBulkDecorate === "function") recipeBulkDecorate("ah"); // selecteren en in bulk importeren (recipe_bulk.js)
  if (!all.length) return;
  let text = "";
  if (!inGroup.length) text = t("ah.recipes.group_empty");
  else if (!recipes.length) text = t("saved.none_match", { query });
  message("#ah-recipes-message", text, "success");
}

function updateSavedRecipesStatus(data) {
  const total = Number(data.total ?? state.ahRecipes.length);
  const when = formatSyncTime(data.syncedAt);
  state.ahRecipesMeta = data;
  // Voedingswaarden die nog bij AH opgehaald moeten worden (telkens een deel na Synchroniseren, zie ah_recipes.start_fill).
  const pending = Number(data.nutritionPending) || 0;
  $("#ah-recipes-status").textContent =
    tn("saved.status", total) + (when ? t("saved.status_when", { when }) : "") + t("saved.status_hint") +
    (pending && caloriesShown() ? ` ${tn("saved.nutrition_pending", pending)}` : "");
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
    state.ahRecipeGroups = data.groups || [];
    renderAhRecipeGroups();
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

// Een scherm tonen. Een hoofdtab geeft zijn knop mee (die lichtt op); het profiel, de instellingen en Over horen bij het
// avatar-menu, en op een klein scherm ook het mandje: dan lichtt de knop Meer in de tabbalk op. Die knop is er op een groot
// scherm niet, en de tab Mandje niet op een klein scherm, dus er is steeds een zichtbare tab met aria-current.
const MORE_MENU_PANELS = ["profile", "settings", "about", "basket"];

function showPanel(panelId, tab = null) {
  document.querySelectorAll(".tab, .panel").forEach((item) => item.classList.remove("is-active"));
  document.querySelectorAll(".tab").forEach((item) => item.removeAttribute("aria-current"));
  const highlighted = [tab, MORE_MENU_PANELS.includes(panelId) ? $("#tab-more") : null].filter(Boolean);
  highlighted.forEach((item) => {
    item.classList.add("is-active");
    item.setAttribute("aria-current", "page");
  });
  $(`#${panelId}`).classList.add("is-active");
  // Zoals een native tabbalk: een andere tab begint bovenaan.
  window.scrollTo({ top: 0 });
  if (panelId === "ah-items") itemsFollowPreferred();
  if (panelId === "ah-items" && !state.ahRecipesLoaded) loadSavedRecipes({ cachedOnly: true });
  if (panelId === "about" && !state.aboutLoaded) loadAbout();
  if (panelId === "basket") showBasket();
  if (panelId === "meal-kits" && typeof showMealKits === "function") showMealKits();
  if (panelId === "offers" && typeof showOffers === "function") showOffers();
  if (panelId === "recipes") ensureMealPlan();
  if (panelId === "settings" && !isRegularUser() && typeof loadOffersSyncStatus === "function") loadOffersSyncStatus();
  if (panelId === "settings" && !isRegularUser() && typeof loadLinkingBox === "function") loadLinkingBox();
  if (typeof cookWakeUpdate === "function") cookWakeUpdate(); // een ander scherm: is er nog een recept in beeld?
  if (typeof routerSync === "function") routerSync();
}

// Het navigatiemenu links (groot scherm) inklappen tot alleen de pictogrammen; de keuze blijft bewaard.
function updateNavToggle() {
  const collapsed = document.documentElement.classList.contains("nav-collapsed");
  const toggle = $("#nav-toggle");
  toggle.setAttribute("aria-expanded", String(!collapsed));
  const label = t(collapsed ? "nav.expand" : "nav.collapse");
  toggle.setAttribute("aria-label", label);
  toggle.title = collapsed ? label : "";
  // Ingeklapt is van de tabs alleen het pictogram te zien: de naam komt dan als tooltip.
  document.querySelectorAll(".tab[data-tab]").forEach((tab) => {
    const name = tab.querySelector(".tab-text")?.textContent || "";
    if (collapsed && name) tab.title = name;
    else tab.removeAttribute("title");
  });
}

$("#nav-toggle").addEventListener("click", () => {
  const collapsed = document.documentElement.classList.toggle("nav-collapsed");
  try {
    localStorage.setItem("ms-nav-collapsed", collapsed ? "1" : "0");
  } catch {
    // Zonder opslag geldt de keuze alleen tot je de pagina herlaadt.
  }
  updateNavToggle();
});
window.addEventListener("ms:languagechange", updateNavToggle);
document.addEventListener("DOMContentLoaded", updateNavToggle);

document.querySelectorAll(".tab[data-tab]").forEach((tab) => {
  tab.addEventListener("click", () => showPanel(tab.dataset.tab, tab));
});

// Het logo brengt je naar het startscherm (de eerste tab); met Ctrl, Cmd of de middelste knop opent het gewoon een nieuw tabblad.
$("#brand-home").addEventListener("click", (event) => {
  if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  document.querySelector('.tab[data-tab="recipes"]').click();
});

// Uitklapmenu's in een subtabbalk (Beheer > Winkels en de winkelkeuze in Winkels): de knop opent de lijst; die sluit
// bij een keuze, een klik elders en Esc.
function setSubtabMenu(menu, open) {
  menu.querySelector(".subtab-menu__list").hidden = !open;
  menu.querySelector(".subtab-menu__toggle").setAttribute("aria-expanded", String(open));
}

function bindSubtabMenu(menu) {
  const toggle = menu.querySelector(".subtab-menu__toggle");
  toggle.addEventListener("click", () => setSubtabMenu(menu, menu.querySelector(".subtab-menu__list").hidden));
  menu.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || menu.querySelector(".subtab-menu__list").hidden) return;
    setSubtabMenu(menu, false);
    toggle.focus();
  });
}

document.querySelectorAll(".subtab-menu").forEach(bindSubtabMenu);
document.addEventListener("click", (event) => {
  document.querySelectorAll(".subtab-menu").forEach((menu) => {
    if (!menu.contains(event.target)) setSubtabMenu(menu, false);
  });
});

$("#items-store-list").addEventListener("click", (event) => {
  const item = event.target.closest("[data-store]");
  if (!item) return;
  setSubtabMenu($("#items-store-menu"), false);
  switchItemsStore(item.dataset.store);
});

document.querySelectorAll(".subtab[data-subtab], .subtab-menu__item[data-subtab]").forEach((subtab) => {
  subtab.addEventListener("click", () => {
    const panel = subtab.closest(".panel");
    if (!panel) return;
    panel
      .querySelectorAll(".subtab, .subtab-menu__item[data-subtab], .subpanel")
      .forEach((item) => item.classList.remove("is-active"));
    subtab.classList.add("is-active");
    const menu = subtab.closest(".subtab-menu");
    if (menu) {
      menu.querySelector(".subtab-menu__toggle").classList.add("is-active");
      setSubtabMenu(menu, false);
    }
    const target = panel.querySelector(`#${subtab.dataset.subtab}`);
    if (target) target.classList.add("is-active");
    if (subtab.dataset.subtab === "planner") ensureMealPlan();
    if (subtab.dataset.subtab === "settings-link" && typeof loadOffersSyncStatus === "function") loadOffersSyncStatus();
    if (subtab.dataset.subtab === "settings-link" && typeof loadLinkingBox === "function") loadLinkingBox();
    if (subtab.dataset.subtab === "settings-link" && typeof loadAiReviewSettings === "function") loadAiReviewSettings();
    if (subtab.dataset.subtab === "settings-users" && typeof loadUsers === "function") loadUsers();
    if (subtab.dataset.subtab === "profile-ai" && typeof loadAi === "function") loadAi();
    if (subtab.dataset.subtab === "settings-audit" && typeof loadAudit === "function") loadAudit();
    if (subtab.dataset.subtab === "settings-system" && typeof loadBackup === "function") loadBackup();
    if (subtab.dataset.subtab === "settings-system" && typeof loadPriceArchive === "function") loadPriceArchive();
    if (subtab.dataset.subtab === "settings-system" && typeof loadImageCacheStatus === "function") loadImageCacheStatus();
    if (subtab.dataset.subtab === "ah-items-bestellingen" && typeof loadOrders === "function") loadOrders();
    if (subtab.dataset.subtab === "ah-items-weekplanner" && typeof showAhPlanner === "function") showAhPlanner();
    if (subtab.dataset.subtab === "ah-items-picnic" && typeof loadPicnicRecipes === "function" && !picnicRecipes.loaded) loadPicnicRecipes();
    if (subtab.dataset.subtab === "ah-items-picnic-favorieten" && typeof loadPicnicFavorites === "function" && !picnicFavorites.loaded) loadPicnicFavorites({ refresh: false });
    if (subtab.dataset.subtab === "ah-items-picnic-bestellingen" && typeof loadPicnicOrders === "function" && !picnicOrders.loaded) loadPicnicOrders();
    if (subtab.dataset.subtab === "ah-items-jumbo-recepten" && typeof loadJumboFavorites === "function" && !jumboFavorites.loaded) loadJumboFavorites();
    if (subtab.dataset.subtab === "ah-items-crisp-recepten" && typeof loadCrispRecipes === "function" && !crispRecipes.loaded) loadCrispRecipes();
    if (subtab.dataset.subtab === "ah-items-crisp-favorieten" && typeof loadCrispFavorites === "function" && !crispFavorites.loaded) loadCrispFavorites();
    if (subtab.dataset.subtab === "ah-items-crisp-bestellingen" && typeof loadCrispOrders === "function" && !crispOrders.loaded) loadCrispOrders();
    if (subtab.dataset.subtab === "ah-items-lijstjes" && !state.ahListsLoaded) {
      state.ahListsLoaded = true;
      loadAhFavoriteLists();
    }
    if (typeof routerSync === "function") routerSync();
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
  $("#about-version").textContent = about.channel === "beta" ? `${about.label} beta` : about.label;
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

// Het profiel en de instellingen openen, desgewenst op een bepaald subtab.
function openSubtab(panelId, subtab) {
  showPanel(panelId);
  const button = document.querySelector(`[data-subtab="${subtab}"]`);
  if (button) button.click();
}

function openProfile(subtab = "profile-general") {
  openSubtab("profile", subtab);
}

function isRegularUser() {
  return document.documentElement.classList.contains("is-regular-user");
}

// Een gewone gebruiker ziet onder Instellingen alleen Voorkeuren.
function openSettings(subtab = "settings-link") {
  openSubtab("settings", isRegularUser() ? "settings-appearance" : subtab);
}

$("#prev-week").addEventListener("click", () => {
  state.weekOffset -= 1;
  updateWeekLabel();
  loadMealPlan();
});
$("#next-week").addEventListener("click", () => {
  state.weekOffset += 1;
  updateWeekLabel();
  loadMealPlan();
});
$("#load-plan").addEventListener("click", () => loadMealPlan({ force: true }));
$("#meal-days").addEventListener("click", (event) => {
  const add = event.target.closest("[data-plan-add]");
  if (add) return openPlanDialog({ date: add.dataset.planAdd });
  const button = event.target.closest("[data-plan-view], [data-plan-edit]");
  if (!button) return;
  const id = button.dataset.planView ?? button.dataset.planEdit;
  const entry = (state.entries || []).find((item) => String(item.id) === id);
  if (!entry) return;
  if (button.dataset.planView != null) openMealRecipe(entry);
  else openPlanDialog({ entry });
});
// Het recept van een maaltijd (openMealRecipe): Wijzigen gaat naar het plan-venster; Sluiten, Esc of een tik naast het venster sluit.
// "Start met koken" in het venster opent de kookmodus (#cook-mode, recipe_view.js) erbovenop; dat is een eigen venster ernaast.
$("#meal-recipe-edit").addEventListener("click", () => {
  const entry = mealRecipe.entry;
  $("#meal-recipe-dialog").close();
  if (entry) openPlanDialog({ entry });
});
$("#meal-recipe-close").addEventListener("click", () => $("#meal-recipe-dialog").close());
$("#meal-recipe-dialog").addEventListener("click", (event) => {
  if (event.target === event.currentTarget) event.currentTarget.close();
});
$("#meal-recipe-image").addEventListener("error", (event) => {
  event.target.hidden = true;
});
// "Inplannen" bij een recept uit Mealie of na een import (`data-plan-slug`), of bij een recept van een winkel of maaltijdbox
// (`data-plan-source`, dat bij het opslaan eerst in Mealie komt): de plan-dialoog met het recept al gekozen.
document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-plan-slug], [data-plan-source]");
  if (!button) return;
  const data = button.dataset;
  const [store, ...rest] = (data.planSource || "").split(":");
  const extra = { portions: Number(data.planPortions) || null, kcal: Number(data.planKcal) || null };
  const recipe = data.planSource
    ? { source: { store, id: rest.join(":") }, name: data.planName || "", image: data.planImage || "", ...extra }
    : { slug: data.planSlug, recipeId: data.planId || "", name: data.planName || "", image: data.planImage || "", ...extra };
  const target =
    button.closest(".message")?.parentElement ||
    button.closest(".recipe-card")?.querySelector(".recipe-import-result") ||
    (data.planTarget ? document.getElementById(data.planTarget) : null);
  openPlanDialog({ recipe, target });
});
$("#plan-form").addEventListener("submit", savePlanEntry);
$("#plan-cancel").addEventListener("click", () => $("#plan-dialog").close());
// Het venster met de productfoto in het groot (openReviewPhoto): Sluiten, Esc (van de browser) of een tik naast het venster.
$("#photo-dialog-close").addEventListener("click", () => $("#photo-dialog").close());
$("#photo-dialog").addEventListener("click", (event) => {
  if (event.target === event.currentTarget) event.currentTarget.close();
});
$("#photo-dialog-image").addEventListener("error", (event) => {
  const image = event.target;
  if (image.dataset.fallback) {
    image.src = image.dataset.fallback;
    image.dataset.fallback = "";
  } else {
    image.hidden = true;
  }
});
$("#plan-delete").addEventListener("click", deletePlanEntry);
$("#plan-surprise").addEventListener("click", surprisePlanEntry);
document.querySelectorAll('input[name="plan-kind"]').forEach((input) => input.addEventListener("change", renderPlanKind));
$("#plan-recipe-query").addEventListener("input", () => {
  clearTimeout(planDialog.searchTimer);
  planDialog.searchTimer = setTimeout(searchPlanRecipes, 250);
});
$("#plan-recipe-query").addEventListener("keydown", (event) => {
  // Enter zoekt meteen in plaats van het formulier te versturen.
  if (event.key !== "Enter") return;
  event.preventDefault();
  clearTimeout(planDialog.searchTimer);
  searchPlanRecipes();
});
$("#plan-recipe-results").addEventListener("click", (event) => {
  const pick = event.target.closest("[data-plan-pick]");
  if (!pick) return;
  message("#plan-message", "");
  choosePlanRecipe(planDialog.results[Number(pick.dataset.planPick)] || null);
});
$("#plan-recipe-chosen").addEventListener("click", (event) => {
  const step = event.target.closest("[data-servings-step]");
  if (step) return setPlanServings((planDialog.servings || planBase(planDialog.recipe)) + Number(step.dataset.servingsStep));
  if (!event.target.closest("#plan-recipe-change")) return;
  planDialog.recipe = null;
  renderPlanRecipe();
  $("#plan-recipe-query").focus();
  if (!planDialog.results.length) searchPlanRecipes();
});
$("#plan-recipe-chosen").addEventListener("change", (event) => {
  if (event.target.id === "plan-servings") setPlanServings(event.target.value);
});
// De filters op voedingswaarden (kcal per persoon en Meer filters) in de receptkiezer: zoeken zodra je stopt met typen.
["#plan-kcal-min", "#plan-kcal-max", ...NUTRITION_FILTERS.slice(1).flatMap(([name]) => [`#plan-${name}-min`, `#plan-${name}-max`])].forEach((selector) => {
  $(selector).addEventListener("input", () => {
    clearTimeout(planDialog.searchTimer);
    planDialog.searchTimer = setTimeout(searchPlanRecipes, 400);
  });
  $(selector).addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    searchPlanRecipes();
  });
});
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
$("#mealie-week-start").addEventListener("change", saveWeekStart);
$("#mealie-nutrition-start").addEventListener("click", startNutritionBackfill);
$("#mealie-nutrition-stop").addEventListener("click", stopNutritionBackfill);
$("#open-mealie-setup").addEventListener("click", () => {
  openSettings();
  state.storeFold.mealie = true;
  applyStoreFold("mealie", false);
});
$("#load-ah-lists").addEventListener("click", loadAhFavoriteLists);
$("#sync-all-ah-lists").addEventListener("click", syncAllAhLists);
$("#load-ah-recipes").addEventListener("click", () => loadSavedRecipes({ refresh: true }));
$("#ah-recipe-filter").addEventListener("input", (event) => {
  state.ahRecipeQuery = event.target.value;
  renderSavedRecipes();
});
$("#ah-recipe-group").addEventListener("change", (event) => {
  state.ahRecipeGroup = event.target.value;
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
$("#ah-recipes").addEventListener("click", (event) => {
  const open = event.target.closest("[data-store-open]");
  if (open) openStoreRecipe("ah", open.dataset.storeOpen.split(":", 2)[1], AH_SAVED_HOST);
});
// De browser zou bij terug zelf een scrollstand kiezen; wij zetten hem terug op de plek in het overzicht.
if ("scrollRestoration" in history) history.scrollRestoration = "manual";
// Terug en vooruit (ook naar een lijstje) regelt router.js.
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
  openSettings();
  $("#token-input").value = params.get("ah_refresh");
  message("#token-message", t("ah.refresh_received"), "success");
  history.replaceState({}, "", "/settings");
}
if (params.get("ah_connected")) {
  openSettings();
  message("#token-message", t("ah.saved"), "success");
  history.replaceState({}, "", "/settings");
}
if (params.get("ah_error")) {
  openSettings();
  message("#token-message", t("ah.redirect_failed", { error: params.get("ah_error") }));
  history.replaceState({}, "", "/settings");
}

// Een andere taal: alles wat uit gegevens is opgebouwd opnieuw tekenen (de vaste teksten doet i18n.js).
window.addEventListener("ms:languagechange", () => {
  updateWeekLabel();
  if (state.entries) renderMealPlan(state.entries);
  renderAuthState();
  renderCredentials();
  renderAhLists();
  if (state.ahOpenListId) loadAhFavoriteListItems(state.ahOpenListId, { keepScroll: true });
  renderAhRecipeGroups();
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

// ---- adressen van schermen (router.js gebruikt ze, en de links elders in de app ook). Hier, omdat app.js als eerste laadt.

function routerQuery(params) {
  const text = new URLSearchParams(Object.entries(params).filter(([, value]) => value)).toString();
  return text ? `?${text}` : "";
}

function storesPath(store, section = "", id = "") {
  return ["/stores", store, section, id].filter(Boolean).map((part, index) => (index ? encodeURIComponent(part) : part)).join("/");
}

function offersPath(sub = "", params = {}) {
  return `/offers${sub ? `/${sub}` : ""}${routerQuery(params)}`;
}

// De prijsgeschiedenis van een product: de naam zoekt hem op, het id kiest hem in de lijst.
function priceHistoryPath(store, title, productId = "") {
  return offersPath("history", { store: offersStoreParam(store), q: title || "", product: productId ? String(productId) : "" });
}

// Waar een aankoop vandaan komt: een bestelling of kassabon heeft een eigen adres, als de app hem kan tonen.
function purchasePath(store, source, ref) {
  if (!ref) return "";
  if (source === "receipt" && store === "ah") return storesPath("ah", "receipts", ref);
  if (source === "order" && (store === "ah" || store === "jumbo")) return storesPath(store, "orders", ref);
  return "";
}
