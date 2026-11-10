// Installatiehulp voor de eerste start, en het beheer van wachtwoord en tweestapsverificatie (MFA).
// Wordt na app.js geladen en gebruikt de hulpfuncties daarvan ($, message, jsonFetch, escapeHtml, state).

// De stappen. Sinds 4.13.0 kies je na Mealie welke winkels je gebruikt (de stap "stores"); alleen die winkels krijgen een
// stap. Daarvoor liep de hulp altijd langs Albert Heijn, Jumbo en Picnic, die je een voor een oversloeg, en kwam Crisp niet voor.
// Crisp heeft geen eigen stap: het koppelen staat onder Instellingen, Koppelen, en de laatste stap brengt je erheen.
const OB_STORE_STEPS = ["ah", "jumbo", "picnic"];
const OB_STORE_CHOICES = ["ah", "jumbo", "picnic", "crisp"];
const OB_STORES_KEY = "ms-ob-stores";

const ob = { step: null, sub: "choose", rendered: "", started: false, mfaBusy: false, stores: obStoredStores() };

// De gekozen winkels, bewaard voor dit tabblad zodat herladen ze houdt. null: nog niet gekozen.
function obStoredStores() {
  try {
    const saved = JSON.parse(window.sessionStorage.getItem(OB_STORES_KEY) || "null");
    return Array.isArray(saved) ? saved.filter((store) => OB_STORE_CHOICES.includes(store)) : null;
  } catch {
    return null;
  }
}

function obSaveStores(stores) {
  ob.stores = stores;
  try {
    window.sessionStorage.setItem(OB_STORES_KEY, JSON.stringify(stores));
  } catch {
    // Zonder opslag geldt de keuze tot je de pagina herlaadt.
  }
}

// De stappen van deze installatie: beveiliging, Mealie, de winkelkeuze, een stap per gekozen winkel en Klaar. Zolang er niet
// gekozen is (`stores` null) staan er nog geen winkelstappen in.
function obSteps(stores = ob.stores) {
  const chosen = OB_STORE_STEPS.filter((store) => (stores || []).includes(store));
  return ["auth", "mealie", "stores", ...chosen, "done"].map((id) => ({ id }));
}

// De stap voor of na deze (`offset` -1 of 1), binnen de stappen van deze installatie.
function obNeighbour(step, offset, stores = ob.stores) {
  const steps = obSteps(stores).map((item) => item.id);
  const at = steps.indexOf(step);
  return steps[Math.min(steps.length - 1, Math.max(0, (at < 0 ? 0 : at) + offset))];
}

function postJson(url, body = {}) {
  return jsonFetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

async function loadOnboardingStatus() {
  try {
    state.onboarding = await jsonFetch("/api/onboarding/status");
  } catch {
    state.onboarding = null;
  }
}

// De installatiehulp toont zich zolang de onboarding niet is afgerond, en alleen als je er ook echt
// iets mee kunt: op een nieuwe installatie, of nadat je bent ingelogd.
function onboardingVisible() {
  const status = state.onboarding;
  const auth = state.auth;
  if (!status?.needed || !auth) return false;
  const authenticated = auth.authenticated || !auth.enabled;
  return authenticated || Boolean(auth.setupRequired);
}

function stepIndex(id) {
  return obSteps().findIndex((step) => step.id === id);
}

function renderOnboarding() {
  const root = $("#onboarding");
  if (!root) return;
  if (!onboardingVisible()) {
    root.hidden = true;
    document.body.classList.remove("has-onboarding");
    if (typeof syncAppInert === "function") syncAppInert();
    return;
  }
  const status = state.onboarding;
  if (root.hidden) {
    root.hidden = false;
    document.body.classList.add("has-onboarding");
    if (typeof syncAppInert === "function") syncAppInert();
    if (!ob.started) {
      ob.started = true;
      postJson("/api/onboarding/start").catch(() => {});
    }
  }
  if (ob.step === null) ob.step = status.authDone ? "mealie" : "auth";
  // Is de beveiliging al geregeld terwijl we nog op de keuzepagina staan (bijvoorbeeld na verversen)? Door.
  if (ob.step === "auth" && ob.sub === "choose" && status.authDone) ob.step = "mealie";
  renderObStep();
}

function obGo(step, sub = "choose") {
  ob.step = step;
  ob.sub = sub;
  message("#ob-message", "");
  renderObStep(true);
}

function obProgressHtml() {
  const current = stepIndex(ob.step);
  return obSteps().map((step, index) => {
    const state_ = index < current ? "is-done" : index === current ? "is-current" : "";
    return `<li class="${state_}"${index === current ? ' aria-current="step"' : ""}><span class="ob-progress__dot" aria-hidden="true">${
      index < current ? "&#10003;" : index + 1
    }</span><span class="ob-progress__label">${escapeHtml(t(`ob.step.${step.id}`))}</span></li>`;
  }).join("");
}

function renderObStep(force = false) {
  const key = `${ob.step}/${ob.sub}`;
  if (!force && ob.rendered === key) return;
  ob.rendered = key;
  // Een winkelstap die niet (meer) gekozen is, bijvoorbeeld na herladen zonder opslag: terug naar de winkelkeuze.
  if (stepIndex(ob.step) < 0) ob.step = "stores";
  const index = stepIndex(ob.step);
  $("#ob-eyebrow").textContent = t("ob.step_of", { n: index + 1, total: obSteps().length });
  $("#ob-title").textContent = t(`ob.title.${ob.step}`);
  $("#ob-progress").innerHTML = obProgressHtml();
  const body = $("#ob-body");
  const renderers = { auth: renderObAuth, mealie: renderObMealie, stores: renderObStores, ah: renderObAh, jumbo: renderObJumbo, picnic: renderObPicnic, done: renderObDone };
  renderers[ob.step](body);
  $("#onboarding").scrollTo?.({ top: 0 });
  $("#ob-title").focus({ preventScroll: true });
}

// ------------------------------------------------------------------ stap 1: beveiliging

function obEnvironmentNote() {
  const env = state.onboarding || {};
  return `<p class="ob-env">${th("ob.env", {
    host: env.host || window.location.hostname,
    secure: t(env.secure ? "ob.env.secure" : "ob.env.insecure"),
  }).replace(/&lt;(\/?)strong&gt;/g, "<$1strong>")}</p>`;
}

function renderObAuth(body) {
  const status = state.onboarding || {};
  const subs = {
    choose: obAuthChoose,
    passkey: obAuthPasskey,
    password: obAuthPassword,
    "mfa-offer": obAuthMfaOffer,
    "mfa-setup": obAuthMfaSetup,
    open: obAuthOpen,
  };
  (subs[ob.sub] || obAuthChoose)(body, status);
}

function obAuthChoose(body, status) {
  const passkey = `
    <article class="ob-option ob-option--recommended">
      <span class="ob-badge">${escapeHtml(t("ob.recommended"))}</span>
      <h3>${escapeHtml(t("ob.passkey.title"))}</h3>
      <p>${escapeHtml(t("ob.passkey.text"))}</p>
      <button class="button" type="button" data-ob-action="go-passkey">${escapeHtml(t("ob.passkey.button"))}</button>
    </article>`;
  const password = (primary) => `
    <article class="ob-option${primary ? " ob-option--recommended" : ""}">
      ${primary ? `<span class="ob-badge">${escapeHtml(t("ob.recommended"))}</span>` : ""}
      <h3>${escapeHtml(t("ob.password.title"))}</h3>
      <p>${escapeHtml(t("ob.password.text"))}</p>
      <button class="button${primary ? "" : " button--secondary"}" type="button" data-ob-action="go-password">${escapeHtml(t("ob.password.button"))}</button>
    </article>`;
  const welcome = `<p>${escapeHtml(t("ob.welcome"))}</p>`;
  body.innerHTML = status.passkeyAvailable
    ? `
      ${welcome}
      ${obEnvironmentNote()}
      <p>${escapeHtml(t("ob.choose.domain"))}</p>
      <div class="ob-options">${passkey}${password(false)}</div>`
    : `
      ${welcome}
      ${obEnvironmentNote()}
      <div class="message warning ob-note">
        <strong>${escapeHtml(t("ob.nopasskey.title"))}</strong>
        ${escapeHtml(status.passkeyUnavailableReason || "")}
        ${escapeHtml(t("ob.nopasskey.hint"))}
      </div>
      <p>${escapeHtml(t("ob.choose.nodomain"))}</p>
      <div class="ob-options">${password(true)}</div>
      <p class="ob-skip"><button class="link-button" type="button" data-ob-action="go-open">${escapeHtml(t("ob.skip_security"))}</button></p>`;
}

function obBackButton(action = "go-choose", label = t("common.back")) {
  return `<button class="button button--secondary" type="button" data-ob-action="${action}">${escapeHtml(label)}</button>`;
}

function obAuthPasskey(body, status) {
  const unavailable = webauthnUnavailableReason();
  body.innerHTML = `
    <p>${escapeHtml(t("ob.passkey.intro"))}</p>
    ${unavailable ? `<div class="message error">${escapeHtml(unavailable)}</div>` : ""}
    <form class="auth-form" data-ob-form="passkey" autocomplete="off">
      <label class="field"><span>${escapeHtml(t("field.username"))}</span><input class="input" id="ob-username" value="admin" autocomplete="username" autocapitalize="none" spellcheck="false"></label>
      <label class="field"><span>${escapeHtml(t("ob.passkey.name_label"))}</span><input class="input" id="ob-passkey-name" value="${escapeHtml(t("ob.passkey.default_name"))}" autocomplete="off"></label>
      <div class="ob-actions">${obBackButton()}<button class="button" type="submit" ${unavailable ? "disabled" : ""}>${escapeHtml(t("ob.passkey.make"))}</button></div>
    </form>`;
}

function obAuthPassword(body, status) {
  const min = status.minPasswordLength || 10;
  body.innerHTML = `
    <p>${escapeHtml(t("ob.password.intro", { min }))}</p>
    <form class="auth-form" data-ob-form="password" autocomplete="on">
      <label class="field"><span>${escapeHtml(t("field.username"))}</span><input class="input" id="ob-username" value="admin" autocomplete="username" autocapitalize="none" spellcheck="false" required></label>
      <label class="field"><span>${escapeHtml(t("field.password"))}</span><input class="input" id="ob-password" type="password" autocomplete="new-password" minlength="${min}" required></label>
      <label class="field"><span>${escapeHtml(t("field.repeat_password"))}</span><input class="input" id="ob-password2" type="password" autocomplete="new-password" required></label>
      <label class="check"><input type="checkbox" id="ob-show-password"> ${escapeHtml(t("ob.password.show"))}</label>
      <div class="ob-actions">${obBackButton()}<button class="button" type="submit">${escapeHtml(t("ob.password.save"))}</button></div>
    </form>`;
}

function obAuthMfaOffer(body) {
  body.innerHTML = `
    <div class="message success">${escapeHtml(t("ob.password.saved"))}</div>
    <h3 class="ob-subtitle">${escapeHtml(t("ob.mfa.offer_title"))}</h3>
    <p>${escapeHtml(t("ob.mfa.offer_text"))}</p>
    <div class="ob-actions">
      <button class="button button--secondary" type="button" data-ob-action="skip-mfa">${escapeHtml(t("ob.mfa.skip"))}</button>
      <button class="button" type="button" data-ob-action="go-mfa">${escapeHtml(t("ob.mfa.setup"))}</button>
    </div>`;
}

function obAuthMfaSetup(body) {
  body.innerHTML = `<div id="ob-mfa"></div>`;
  mfaEnroll($("#ob-mfa"), {
    messageTarget: "#ob-message",
    cancelLabel: "common.cancel_now",
    onCancel: () => obGo("mealie"),
    onDone: async () => {
      await refreshAuthStatus();
      obGo("mealie");
    },
  });
}

function obAuthOpen(body) {
  body.innerHTML = `
    <div class="message warning ob-note">
      <strong>${escapeHtml(t("ob.open.warning_title"))}</strong>
      ${escapeHtml(t("ob.open.warning_text"))}
    </div>
    <p>${escapeHtml(t("ob.open.later"))}</p>
    <form class="auth-form" data-ob-form="open">
      <label class="check"><input type="checkbox" id="ob-open-confirm"> ${escapeHtml(t("ob.open.confirm"))}</label>
      <div class="ob-actions">${obBackButton()}<button class="button" type="submit" id="ob-open-submit" disabled>${escapeHtml(t("ob.open.continue"))}</button></div>
    </form>`;
}

// ------------------------------------------------------------------ stap 2 t/m 6

function obNavigation({ back, next }) {
  return `<div class="ob-actions">${back ? obBackButton(`go-${back}`) : "<span></span>"}<button class="button" type="button" data-ob-action="go-${next}">${escapeHtml(t("ob.next"))}</button></div>`;
}

function renderObMealie(body) {
  body.innerHTML = `
    <p>${escapeHtml(t("ob.mealie.intro"))}</p>
    <div id="ob-mealie-status"></div>
    <div id="ob-mealie-form-area">
      <h3 class="ob-subtitle">${escapeHtml(t("ob.mealie.how"))}</h3>
      <ol class="steps">
        <li>${th("ob.mealie.s1")}</li>
        <li>${th("ob.mealie.s2")}</li>
        <li>${th("ob.mealie.s3")}</li>
      </ol>
      <form class="auth-form" id="ob-mealie-form" data-ob-form="mealie" autocomplete="off">
        <label class="field"><span>${escapeHtml(t("mealie.url_label"))}</span><input class="input" id="ob-mealie-url" type="url" inputmode="url" placeholder="${escapeHtml(t("mealie.url_ph"))}" autocomplete="off" spellcheck="false" required></label>
        <label class="field"><span>${escapeHtml(t("mealie.token_label"))}</span><input class="input" id="ob-mealie-token" type="password" placeholder="${escapeHtml(t("mealie.token_ph"))}" autocomplete="off" spellcheck="false" required></label>
        <button class="button" type="submit">${escapeHtml(t("mealie.link"))}</button>
      </form>
      <p class="muted">${th("ob.mealie.note")}</p>
    </div>
    <div class="ob-actions">
      <button class="button button--secondary" type="button" id="ob-mealie-skip" data-ob-action="next">${escapeHtml(t("ob.skip"))}</button>
      <button class="button" type="button" id="ob-mealie-next" data-ob-action="next" hidden>${escapeHtml(t("ob.next"))}</button>
    </div>`;
  loadObIntegrationStatus("mealie");
}

function renderObAh(body) {
  body.innerHTML = `
    <p>${escapeHtml(t("ob.ah.intro"))}</p>
    <div id="ob-ah-status"></div>
    <div id="ob-ah-form-area">
      <h3 class="ob-subtitle">${escapeHtml(t("ob.ah.how"))}</h3>
      <div class="message warning ob-note">${th("ob.ah.computer")}</div>
      <ol class="steps">
        <li>${th("ob.ah.s1")}</li>
        <li>${th("ob.ah.s2")}</li>
        <li>${th("ob.ah.s3")}</li>
        <li>${th("ob.ah.s4")}</li>
        <li>${th("ob.ah.s5")}</li>
      </ol>
      <p><a class="button button--secondary" href="/api/ah/auth/start" target="_blank" rel="noopener">${escapeHtml(t("ahlogin.button"))}</a></p>
      <form class="auth-form" id="ob-ah-form" data-ob-form="ah" autocomplete="off">
        <label class="field"><span>${escapeHtml(t("ob.ah.paste_label"))}</span><textarea class="textarea" id="ob-ah-token" rows="3" placeholder="appie://login-exit?code=..." spellcheck="false" required></textarea></label>
        <button class="button" type="submit">${escapeHtml(t("common.link"))}</button>
      </form>
      <p class="muted">${escapeHtml(t("ob.ah.skip_note"))}</p>
    </div>
    <div class="ob-actions">
      ${obBackButton("back")}
      <button class="button button--secondary" type="button" id="ob-ah-skip" data-ob-action="next">${escapeHtml(t("ob.skip"))}</button>
      <button class="button" type="button" id="ob-ah-next" data-ob-action="next" hidden>${escapeHtml(t("ob.next"))}</button>
    </div>`;
  loadObIntegrationStatus("ah");
}

function renderObJumbo(body) {
  body.innerHTML = `
    <p>${escapeHtml(t("ob.jumbo.intro"))}</p>
    <div id="ob-jumbo-status"></div>
    <div id="ob-jumbo-form-area">
      <h3 class="ob-subtitle">${escapeHtml(t("ob.jumbo.how"))}</h3>
      <div class="message warning ob-note">${th("ob.jumbo.computer")}</div>
      <ol class="steps">
        <li>${th("jumbo.step1")}</li>
        <li>${th("jumbo.step2")}</li>
        <li>${th("jumbo.step3")}</li>
        <li>${th("jumbo.step4")}</li>
        <li>${th("jumbo.step5")}</li>
      </ol>
      <p><a class="button button--secondary" href="/api/jumbo/auth/start" target="_blank" rel="noopener">${escapeHtml(t("jumbo.login"))}</a></p>
      <form class="auth-form" id="ob-jumbo-form" data-ob-form="jumbo" autocomplete="off">
        <label class="field"><span>${escapeHtml(t("ob.ah.paste_label"))}</span><textarea class="textarea" id="ob-jumbo-token" rows="3" placeholder="jumbo://callbacks/login?code=..." spellcheck="false" required></textarea></label>
        <button class="button" type="submit">${escapeHtml(t("common.link"))}</button>
      </form>
      <p class="muted">${escapeHtml(t("ob.jumbo.skip_note"))}</p>
    </div>
    <div class="ob-actions">
      ${obBackButton("back")}
      <button class="button button--secondary" type="button" id="ob-jumbo-skip" data-ob-action="next">${escapeHtml(t("ob.skip"))}</button>
      <button class="button" type="button" id="ob-jumbo-next" data-ob-action="next" hidden>${escapeHtml(t("ob.next"))}</button>
    </div>`;
  loadObIntegrationStatus("jumbo");
}

function renderObPicnic(body) {
  body.innerHTML = `
    <p>${escapeHtml(t("ob.picnic.intro"))}</p>
    <div id="ob-picnic-status"></div>
    <div id="ob-picnic-form-area">
      <form class="auth-form" id="ob-picnic-form" data-ob-form="picnic" autocomplete="off">
        <label class="field"><span>${escapeHtml(t("picnic.email"))}</span><input class="input" id="ob-picnic-email" type="email" autocomplete="off" required></label>
        <label class="field"><span>${escapeHtml(t("picnic.password"))}</span><input class="input" id="ob-picnic-password" type="password" autocomplete="off" required></label>
        <button class="button" id="ob-picnic-login" type="submit">${escapeHtml(t("picnic.login"))}</button>
      </form>
      <div id="ob-picnic-code-area" hidden>
        <h3 class="ob-subtitle">${escapeHtml(t("picnic.code_title"))}</h3>
        <p>${escapeHtml(t("picnic.code_intro"))}</p>
        <form class="auth-form" id="ob-picnic-code-form" data-ob-form="picnic-code" autocomplete="off">
          <label class="field"><span>${escapeHtml(t("picnic.code_label"))}</span><input class="input" id="ob-picnic-code" type="text" inputmode="numeric" autocomplete="one-time-code" required></label>
          <button class="button" type="submit">${escapeHtml(t("picnic.confirm"))}</button>
        </form>
        <div class="ob-actions ob-actions--wrap">
          <button class="button button--secondary" type="button" data-ob-action="picnic-sms">${escapeHtml(t("picnic.send_sms"))}</button>
          <button class="button button--secondary" type="button" data-ob-action="picnic-email">${escapeHtml(t("picnic.send_email"))}</button>
        </div>
        <div id="ob-picnic-code-message"></div>
      </div>
      <p class="muted">${th("ob.picnic.note")}</p>
    </div>
    <div class="ob-actions">
      ${obBackButton("back")}
      <button class="button button--secondary" type="button" id="ob-picnic-skip" data-ob-action="next">${escapeHtml(t("ob.skip"))}</button>
      <button class="button" type="button" id="ob-picnic-next" data-ob-action="next" hidden>${escapeHtml(t("ob.next"))}</button>
    </div>`;
  loadObIntegrationStatus("picnic");
}

// Gekoppeld: toon dat, verberg het formulier en maak "volgende" de hoofdknop.
function obIntegrationDone(which, text) {
  message(`#ob-${which}-status`, text, "success");
  $(`#ob-${which}-form-area`).hidden = true;
  $(`#ob-${which}-skip`).hidden = true;
  const next = $(`#ob-${which}-next`);
  next.hidden = false;
  next.focus({ preventScroll: true });
}

async function loadObIntegrationStatus(which) {
  const target = `#ob-${which}-status`;
  try {
    if (which === "mealie") {
      const data = await jsonFetch("/api/mealie/status");
      if (data.configured) obIntegrationDone("mealie", t("ob.mealie.linked", { url: data.url }));
      else message(target, t("ob.mealie.not_linked"), "warning");
    } else if (which === "jumbo") {
      const data = await jsonFetch("/api/jumbo/auth/status");
      if (data.connected) obIntegrationDone("jumbo", t("ob.jumbo.linked"));
      else message(target, t("ob.jumbo.not_linked"), "warning");
    } else if (which === "picnic") {
      const data = await jsonFetch("/api/picnic/auth/status");
      if (data.connected) obIntegrationDone("picnic", t("ob.picnic.linked"));
      else {
        message(target, t("ob.picnic.not_linked"), "warning");
        if (data.twoFactorPending) obPicnicShowCode(); // de login is half af (bijvoorbeeld na verversen): alleen de code ontbreekt
      }
    } else {
      const data = await jsonFetch("/api/ah/auth/status");
      if (data.connected) obIntegrationDone("ah", t("ob.ah.linked"));
      else message(target, t("ob.ah.not_linked"), "warning");
    }
  } catch {
    $(target).innerHTML = "";
  }
}

function authModeLabel() {
  const status = state.onboarding || {};
  const auth = state.auth || {};
  if (status.authMode === "off") return t("ob.mode.off");
  if (status.authMode === "none" || !auth.enabled) return t("ob.mode.none");
  const parts = [];
  if (auth.methods?.passkey) parts.push(t("ob.mode.passkey"));
  if (auth.methods?.password) parts.push(t("ob.mode.password"));
  if (auth.mfaEnabled) parts.push(t("ob.mode.mfa"));
  return t("ob.mode.login_with", { methods: parts.join(t("ob.mode.and")) || t("ob.mode.password") });
}

// Welke winkels gebruik je? Alleen de aangevinkte winkels krijgen daarna een stap. Niets aanvinken kan: dan ga je door naar Klaar.
function renderObStores(body) {
  const chosen = ob.stores || [];
  const names = { ah: "Albert Heijn", jumbo: "Jumbo", picnic: "Picnic", crisp: "Crisp" };
  const notes = { ah: "ob.stores.ah", jumbo: "ob.stores.jumbo", picnic: "ob.stores.picnic", crisp: "ob.stores.crisp" };
  body.innerHTML = `
    <p>${escapeHtml(t("ob.stores.intro"))}</p>
    <fieldset class="ob-stores" id="ob-stores">
      <legend class="visually-hidden">${escapeHtml(t("ob.title.stores"))}</legend>
      ${OB_STORE_CHOICES.map(
        (store) => `<label class="ob-store"><input type="checkbox" value="${store}"${chosen.includes(store) ? " checked" : ""}><span><strong>${names[store]}</strong><small>${escapeHtml(t(notes[store]))}</small></span></label>`
      ).join("")}
    </fieldset>
    <p class="muted">${escapeHtml(t("ob.stores.later"))}</p>
    <div class="ob-actions">
      ${obBackButton("back")}
      <button class="button" type="button" data-ob-action="next">${escapeHtml(t("ob.next"))}</button>
    </div>`;
  if (!ob.stores) obSaveStores([]);
}

async function renderObDone(body) {
  body.innerHTML = `
    <p>${escapeHtml(t("ob.done.intro"))}</p>
    <ul class="ob-checklist" id="ob-checklist"></ul>
    <div class="ob-actions ob-actions--wrap" id="ob-done-actions"></div>`;
  // Alleen de winkels die je koos (sinds 4.13.0); zonder keuze, bij een hulp die voor 4.13.0 begon, de drie van voorheen.
  const chosen = ob.stores || OB_STORE_STEPS;
  const wanted = (store) => chosen.includes(store);
  const [mealie, ah, jumbo, picnic, crisp] = await Promise.all([
    jsonFetch("/api/mealie/status").catch(() => ({})),
    wanted("ah") ? jsonFetch("/api/ah/auth/status").catch(() => ({})) : {},
    wanted("jumbo") ? jsonFetch("/api/jumbo/auth/status").catch(() => ({})) : {},
    wanted("picnic") ? jsonFetch("/api/picnic/auth/status").catch(() => ({})) : {},
    wanted("crisp") ? jsonFetch("/api/crisp/auth/status").catch(() => ({})) : {},
  ]);
  const item = (ok, text) => `<li class="${ok ? "is-ok" : "is-todo"}"><span class="ob-check" aria-hidden="true">${ok ? "&#10003;" : "&#9675;"}</span><span>${escapeHtml(text)}</span></li>`;
  $("#ob-checklist").innerHTML =
    item(true, t("ob.done.security", { mode: authModeLabel() })) +
    item(Boolean(mealie.configured), mealie.configured ? t("ob.done.mealie_ok", { url: mealie.url || "" }) : t("ob.done.mealie_todo")) +
    (wanted("ah") ? item(Boolean(ah.connected), ah.connected ? t("ob.done.ah_ok") : t("ob.done.ah_todo")) : "") +
    (wanted("jumbo") ? item(Boolean(jumbo.connected), jumbo.connected ? t("ob.done.jumbo_ok") : t("ob.done.jumbo_todo")) : "") +
    (wanted("picnic") ? item(Boolean(picnic.connected), picnic.connected ? t("ob.done.picnic_ok") : t("ob.done.picnic_todo")) : "") +
    (wanted("crisp") ? item(Boolean(crisp.connected), crisp.connected ? t("ob.done.crisp_ok") : t("ob.done.crisp_todo")) : "");
  const actions = [];
  if (!mealie.configured) actions.push(`<button class="button" type="button" data-ob-action="go-mealie">${escapeHtml(t("ob.done.link_mealie"))}</button>`);
  if (wanted("ah") && !ah.connected) actions.push(`<button class="button button--secondary" type="button" data-ob-action="go-ah">${escapeHtml(t("ob.done.link_ah"))}</button>`);
  if (wanted("jumbo") && !jumbo.connected) actions.push(`<button class="button button--secondary" type="button" data-ob-action="go-jumbo">${escapeHtml(t("ob.done.link_jumbo"))}</button>`);
  if (wanted("picnic") && !picnic.connected) actions.push(`<button class="button button--secondary" type="button" data-ob-action="go-picnic">${escapeHtml(t("ob.done.link_picnic"))}</button>`);
  // Crisp heeft geen stap in de hulp: de knop rondt de hulp af en opent het blok onder Instellingen, Koppelen.
  if (wanted("crisp") && !crisp.connected) actions.push(`<button class="button button--secondary" type="button" data-ob-action="link-crisp">${escapeHtml(t("ob.done.link_crisp"))}</button>`);
  // Met Mealie gekoppeld is de eerste taak het weekmenu: de knop zegt dat, in plaats van alleen "openen".
  actions.push(
    `<button class="button${actions.length ? " button--secondary" : ""}" type="button" data-ob-action="finish">${escapeHtml(t(mealie.configured ? "ob.done.first_meal" : "ob.done.open"))}</button>`
  );
  $("#ob-done-actions").innerHTML = actions.join("");
}

async function finishOnboarding({ then = null } = {}) {
  try {
    await postJson("/api/onboarding/complete");
  } catch (error) {
    message("#ob-message", error.message);
    return;
  }
  state.onboarding = { ...state.onboarding, needed: false };
  ob.step = null;
  ob.rendered = "";
  renderAuthState();
  window.scrollTo({ top: 0 });
  refreshMealieStatus(); // de melding op het weekmenu en de status onder Beheer volgen wat er gekoppeld is
  refreshAhStatus();
  if (typeof refreshJumboStatus === "function") refreshJumboStatus(); // ook de winkelkeuze in de planner
  if (typeof refreshPicnicStatus === "function") refreshPicnicStatus();
  if (typeof refreshCrispStatus === "function") refreshCrispStatus();
  try {
    window.sessionStorage.removeItem(OB_STORES_KEY); // de keuze hoorde bij de hulp
  } catch {
    // Zonder opslag viel er niets te wissen.
  }
  if (then) then();
}

// ---------------------------------------------------------------- acties en formulieren

async function obSubmitPasskey() {
  const username = $("#ob-username").value.trim() || "admin";
  const credentialName = $("#ob-passkey-name").value.trim() || t("ob.passkey.default_name");
  await registerPasskey({ username, credentialName, messageTarget: "#ob-message" });
  await refreshAuthStatus();
  obGo("mealie");
}

async function obSubmitPassword() {
  const password = $("#ob-password").value;
  if (password !== $("#ob-password2").value) throw new Error(t("ob.password.mismatch"));
  await postJson("/api/auth/password", { username: $("#ob-username").value, password });
  await refreshAuthStatus();
  obGo("auth", "mfa-offer");
}

async function obSubmitMealie() {
  const data = await postJson("/api/mealie/auth", {
    url: $("#ob-mealie-url").value.trim(),
    token: $("#ob-mealie-token").value.trim(),
  });
  $("#ob-mealie-token").value = "";
  const details = [
    data.version ? t("mealie.version", { version: data.version }) : "",
    data.username ? t("mealie.user", { user: data.username }) : "",
  ]
    .filter(Boolean)
    .join(", ");
  obIntegrationDone("mealie", t("mealie.works", { url: data.url, details: details ? ` (${details})` : "" }));
}

async function obSubmitAh() {
  await postJson("/api/ah/auth", { code: $("#ob-ah-token").value.trim() });
  $("#ob-ah-token").value = "";
  obIntegrationDone("ah", t("ah.saved"));
}

async function obSubmitJumbo() {
  await postJson("/api/jumbo/auth", { code: $("#ob-jumbo-token").value.trim() });
  $("#ob-jumbo-token").value = "";
  obIntegrationDone("jumbo", t("jumbo.saved"));
}

function obPicnicShowCode() {
  const area = $("#ob-picnic-code-area");
  if (!area) return;
  area.hidden = false;
  $("#ob-picnic-code").focus({ preventScroll: true });
}

async function obPicnicRequestCode(channel) {
  message("#ob-picnic-code-message", "");
  try {
    await postJson("/api/picnic/auth/2fa/request", { channel });
    message("#ob-picnic-code-message", t("picnic.code_sent"), "success");
  } catch (error) {
    message("#ob-picnic-code-message", error.message);
  }
}

async function obSubmitPicnic() {
  const data = await postJson("/api/picnic/auth", {
    email: $("#ob-picnic-email").value.trim(),
    password: $("#ob-picnic-password").value,
  });
  $("#ob-picnic-password").value = "";
  if (data.twoFactorRequired) {
    // Picnic wil een code: de gebruiker kan opnieuw proberen met een ander wachtwoord, dus de knop blijft bruikbaar.
    $("#ob-picnic-login").disabled = false;
    obPicnicShowCode();
    await obPicnicRequestCode("SMS");
    return;
  }
  obIntegrationDone("picnic", t("picnic.linked"));
}

async function obSubmitPicnicCode() {
  await postJson("/api/picnic/auth/2fa/verify", { code: $("#ob-picnic-code").value.trim() });
  $("#ob-picnic-code").value = "";
  obIntegrationDone("picnic", t("picnic.linked"));
}

async function obSubmitOpen() {
  await postJson("/api/auth/open", { confirm: true });
  await refreshAuthStatus();
  obGo("mealie");
}

const OB_ACTIONS = {
  "go-choose": () => obGo("auth", "choose"),
  "go-passkey": () => obGo("auth", "passkey"),
  "go-password": () => obGo("auth", "password"),
  "go-open": () => obGo("auth", "open"),
  "go-mfa": () => obGo("auth", "mfa-setup"),
  "skip-mfa": () => obGo("mealie"),
  "go-mealie": () => obGo("mealie"),
  "go-stores": () => obGo("stores"),
  next: () => obGo(obNeighbour(ob.step, 1)),
  back: () => obGo(obNeighbour(ob.step, -1)),
  "link-crisp": () => finishOnboarding({ then: () => openLinkBlock("crisp") }),
  "go-ah": () => obGo("ah"),
  "go-jumbo": () => obGo("jumbo"),
  "go-picnic": () => obGo("picnic"),
  "picnic-sms": () => obPicnicRequestCode("SMS"),
  "picnic-email": () => obPicnicRequestCode("EMAIL"),
  "go-done": () => obGo("done"),
  finish: () => finishOnboarding(),
};

$("#ob-body").addEventListener("click", (event) => {
  const button = event.target.closest("[data-ob-action]");
  if (button && OB_ACTIONS[button.dataset.obAction]) OB_ACTIONS[button.dataset.obAction]();
});

$("#ob-body").addEventListener("change", (event) => {
  if (event.target.id === "ob-show-password") {
    for (const id of ["ob-password", "ob-password2"]) $(`#${id}`).type = event.target.checked ? "text" : "password";
  }
  if (event.target.id === "ob-open-confirm") $("#ob-open-submit").disabled = !event.target.checked;
  if (event.target.closest("#ob-stores")) {
    obSaveStores([...document.querySelectorAll("#ob-stores input:checked")].map((input) => input.value));
    // De stappenbalk en de teller volgen de keuze meteen.
    $("#ob-progress").innerHTML = obProgressHtml();
    $("#ob-eyebrow").textContent = t("ob.step_of", { n: stepIndex(ob.step) + 1, total: obSteps().length });
  }
});

$("#ob-body").addEventListener("submit", async (event) => {
  const form = event.target.closest("[data-ob-form]");
  if (!form) return;
  event.preventDefault();
  const submit = form.querySelector('[type="submit"]');
  const handlers = {
    passkey: obSubmitPasskey,
    password: obSubmitPassword,
    open: obSubmitOpen,
    mealie: obSubmitMealie,
    ah: obSubmitAh,
    jumbo: obSubmitJumbo,
    picnic: obSubmitPicnic,
    "picnic-code": obSubmitPicnicCode,
  };
  if (submit) submit.disabled = true;
  message("#ob-message", "");
  try {
    await handlers[form.dataset.obForm]();
  } catch (error) {
    message("#ob-message", error.name === "NotAllowedError" ? t("passkey.cancelled") : error.message);
    if (submit && form.dataset.obForm !== "open") submit.disabled = false;
  }
});

// ------------------------------------------------------------- tweestapsverificatie (MFA)

let qrLibrary = null;

function loadQrLibrary() {
  qrLibrary ||= new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = "/static/vendor/qrcode-2.0.4.js";
    script.onload = () => resolve(window.qrcode);
    script.onerror = () => reject(new Error(t("mfa.qr_load_failed")));
    document.head.appendChild(script);
  });
  return qrLibrary;
}

async function drawQr(slot, text) {
  try {
    const qrcode = await loadQrLibrary();
    const code = qrcode(0, "M");
    code.addData(text);
    code.make();
    slot.innerHTML = code.createSvgTag({ cellSize: 4, margin: 4, scalable: true });
  } catch {
    slot.innerHTML = `<p class="muted">${escapeHtml(t("mfa.qr_failed"))}</p>`;
  }
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    let ok = false;
    try {
      ok = document.execCommand("copy");
    } catch {
      ok = false;
    }
    area.remove();
    return ok;
  }
}

async function mfaEnroll(container, { onDone, onCancel, cancelLabel = "common.cancel", messageTarget }) {
  ob.mfaBusy = true;
  container.innerHTML = `<p class="muted" data-i18n="mfa.wait"></p>`;
  I18N.apply(container);
  let setup;
  try {
    setup = await postJson("/api/auth/mfa/start");
  } catch (error) {
    ob.mfaBusy = false;
    container.innerHTML = "";
    message(messageTarget, error.message);
    return;
  }
  // Alle vaste teksten dragen data-i18n: bij een taalwissel midden in het instellen blijft de sleutel
  // (en dus je al gescande code) gewoon staan en wisselt alleen de tekst.
  container.innerHTML = `
    <ol class="steps">
      <li data-i18n="mfa.setup.s1"></li>
      <li data-i18n="mfa.setup.s2"></li>
      <li data-i18n="mfa.setup.s3"></li>
    </ol>
    <div class="qr-box">
      <div class="qr" role="img" data-i18n-attr="aria-label:mfa.qr_label"></div>
      <div class="qr-help">
        <p class="muted" data-i18n="mfa.help"></p>
        <a class="button button--secondary" href="${escapeHtml(setup.otpauthUri)}" data-i18n="mfa.open_app"></a>
        <p class="muted" data-i18n="mfa.type_key"></p>
        <p><code class="qr-secret" data-mfa-secret>${escapeHtml(setup.secret)}</code></p>
      </div>
    </div>
    <form class="auth-form" data-mfa-confirm autocomplete="off">
      <label class="field"><span data-i18n="mfa.code_from_app"></span><input class="input" data-mfa-code inputmode="numeric" autocomplete="one-time-code" placeholder="123456" maxlength="7" required></label>
      <div class="ob-actions"><button class="button button--secondary" type="button" data-mfa-cancel data-i18n="${escapeHtml(cancelLabel)}"></button><button class="button" type="submit" data-i18n="common.confirm"></button></div>
    </form>`;
  I18N.apply(container);
  drawQr(container.querySelector(".qr"), setup.otpauthUri);
  const form = container.querySelector("[data-mfa-confirm]");
  container.querySelector("[data-mfa-cancel]").addEventListener("click", () => {
    ob.mfaBusy = false;
    onCancel?.();
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const submit = form.querySelector('[type="submit"]');
    submit.disabled = true;
    message(messageTarget, "");
    try {
      const data = await postJson("/api/auth/mfa/confirm", { code: form.querySelector("[data-mfa-code]").value });
      showRecoveryCodes(container, data.recoveryCodes, async () => {
        ob.mfaBusy = false;
        await onDone?.();
      });
    } catch (error) {
      message(messageTarget, error.message);
      submit.disabled = false;
    }
  });
  form.querySelector("[data-mfa-code]").focus();
}

function showRecoveryCodes(container, codes, onContinue, { heading = true } = {}) {
  const text = codes.join("\n");
  container.innerHTML = `
    ${heading ? '<div class="message success" data-i18n="mfa.enabled_msg"></div>' : ""}
    <h3 class="ob-subtitle" data-i18n="mfa.recovery.title"></h3>
    <p data-i18n-html="mfa.recovery.text"></p>
    <ul class="recovery-codes" data-recovery-codes>${codes.map((code) => `<li><code>${escapeHtml(code)}</code></li>`).join("")}</ul>
    <div class="ob-actions ob-actions--wrap">
      <button class="button button--secondary" type="button" data-recovery-copy data-i18n="mfa.recovery.copy"></button>
      <button class="button button--secondary" type="button" data-recovery-download data-i18n="mfa.recovery.download"></button>
    </div>
    <label class="check"><input type="checkbox" data-recovery-saved> <span data-i18n="mfa.recovery.saved"></span></label>
    <div class="ob-actions"><span></span><button class="button" type="button" data-recovery-continue data-i18n="mfa.recovery.continue" disabled></button></div>`;
  I18N.apply(container);
  const status = container.querySelector("[data-recovery-copy]");
  status.addEventListener("click", async () => {
    status.textContent = (await copyText(text)) ? t("mfa.recovery.copied") : t("mfa.recovery.copy_failed");
  });
  container.querySelector("[data-recovery-download]").addEventListener("click", () => {
    const blob = new Blob([`${t("mfa.recovery.file")}\n\n${text}\n`], { type: "text/plain" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "mealieshopper-herstelcodes.txt";
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  });
  const saved = container.querySelector("[data-recovery-saved]");
  const next = container.querySelector("[data-recovery-continue]");
  saved.addEventListener("change", () => {
    next.disabled = !saved.checked;
  });
  next.addEventListener("click", () => onContinue?.());
}

// ------------------------------------------------- Beheer > Beveiliging: wachtwoord en MFA

// Inklapbare blokken in Beheer > Beveiliging. De keuze van de gebruiker blijft bij het opnieuw tekenen staan.
state.secFold = {};

function applyFold(name, { collapsible, defaultOpen, notNeeded }) {
  const toggle = $(`#${name}-toggle`);
  const open = collapsible ? state.secFold[name] ?? defaultOpen : true;
  toggle.disabled = !collapsible;
  toggle.classList.toggle("is-static", !collapsible);
  toggle.setAttribute("aria-expanded", String(open));
  toggle.title = collapsible ? t(open ? "sec.collapse" : "sec.expand") : "";
  $(`#${name}-body`).hidden = !open;
  $(`#${name}-badge`).hidden = !notNeeded;
}

for (const name of ["password"]) {
  $(`#${name}-toggle`).addEventListener("click", () => {
    const toggle = $(`#${name}-toggle`);
    const open = toggle.getAttribute("aria-expanded") !== "true";
    state.secFold[name] = open;
    toggle.setAttribute("aria-expanded", String(open));
    toggle.title = t(open ? "sec.collapse" : "sec.expand");
    $(`#${name}-body`).hidden = !open;
  });
}

function renderSecurityPanel() {
  const passwordBox = $("#security-password-box");
  if (!passwordBox) return;
  const auth = state.auth;
  const envOff = state.onboarding && state.onboarding.authEnabled === false;
  const canManage = Boolean(auth) && (auth.authenticated || !auth.enabled) && !envOff;
  passwordBox.hidden = !canManage;
  if (!canManage) return;

  const configured = Boolean(auth.passwordConfigured);
  const hasPasskey = Boolean(auth.methods?.passkey);
  // Met een passkey is een wachtwoord (en dus ook tweestapsverificatie) niet nodig: dat blok is dan
  // inklapbaar, en dicht zolang je het niet gebruikt. Zonder passkey blijft het open.
  applyFold("password", { collapsible: hasPasskey, defaultOpen: configured, notNeeded: hasPasskey && !configured });
  $("#password-intro").textContent = configured
    ? t("sec.password.intro_set")
    : !auth.enabled
      ? t("sec.password.intro_open")
      : t("sec.password.intro_passkey");
  $("#pw-current-field").hidden = !configured;
  $("#pw-new-label").textContent = configured ? t("sec.password.new") : t("field.password");
  $("#pw-submit").textContent = configured ? t("sec.password.change") : t("sec.password.set");
  const username = $("#pw-username");
  if (!username.value && document.activeElement !== username) username.value = auth.username || "admin";

  const note = $("#mfa-intro");
  const panel = $("#mfa-panel");
  if (ob.mfaBusy) {
    I18N.apply(panel); // midden in het instellen: de vaste teksten wisselen mee, de inhoud blijft
    return;
  }
  if (!configured) {
    note.textContent = hasPasskey ? t("sec.mfa.optional_note") : t("sec.mfa.needs_password");
    panel.innerHTML = "";
  } else if (auth.mfaEnabled) {
    note.textContent = t("sec.mfa.on", { count: I18N.formatNumber(auth.recoveryCodesLeft ?? 0) });
    panel.innerHTML = `<div class="ob-actions ob-actions--wrap">
      <button class="button button--secondary" type="button" data-mfa-action="regen">${escapeHtml(t("sec.mfa.regen"))}</button>
      <button class="button button--secondary" type="button" data-mfa-action="disable">${escapeHtml(t("sec.mfa.disable"))}</button></div>`;
  } else {
    note.textContent = t("sec.mfa.off");
    panel.innerHTML = `<button class="button" type="button" data-mfa-action="start">${escapeHtml(t("sec.mfa.start"))}</button>`;
  }
}

async function submitPasswordForm(event) {
  event.preventDefault();
  const button = $("#pw-submit");
  const password = $("#pw-new").value;
  message("#password-message", "");
  if (password !== $("#pw-new2").value) {
    message("#password-message", t("ob.password.mismatch"));
    return;
  }
  button.disabled = true;
  try {
    const data = await postJson("/api/auth/password", {
      username: $("#pw-username").value,
      password,
      currentPassword: $("#pw-current").value,
    });
    for (const id of ["pw-current", "pw-new", "pw-new2"]) $(`#${id}`).value = "";
    message("#password-message", data.replaced ? t("sec.password.changed") : t("sec.password.was_set"), "success");
    await refreshAuthStatus();
  } catch (error) {
    message("#password-message", error.message);
  } finally {
    button.disabled = false;
  }
}

function askPassword(kind) {
  const labels = { regen: "sec.mfa.regen_action", disable: "sec.mfa.disable_action" };
  $("#mfa-panel").innerHTML = `
    <form class="auth-form" data-mfa-password="${kind}" autocomplete="off">
      <label class="field"><span>${escapeHtml(t("sec.mfa.confirm_password"))}</span><input class="input" type="password" id="mfa-password" autocomplete="current-password" required></label>
      <div class="ob-actions"><button class="button button--secondary" type="button" data-mfa-action="cancel">${escapeHtml(t("common.cancel"))}</button><button class="button" type="submit">${escapeHtml(t(labels[kind]))}</button></div>
    </form>`;
  $("#mfa-password").focus();
}

$("#password-form").addEventListener("submit", submitPasswordForm);

$("#mfa-panel").addEventListener("click", (event) => {
  const button = event.target.closest("[data-mfa-action]");
  if (!button) return;
  const action = button.dataset.mfaAction;
  message("#mfa-message", "");
  if (action === "start") {
    mfaEnroll($("#mfa-panel"), {
      messageTarget: "#mfa-message",
      onCancel: renderSecurityPanel,
      onDone: () => refreshAuthStatus(),
    });
  } else if (action === "regen" || action === "disable") {
    askPassword(action);
  } else if (action === "cancel") {
    renderSecurityPanel();
  }
});

$("#mfa-panel").addEventListener("submit", async (event) => {
  const form = event.target.closest("[data-mfa-password]");
  if (!form) return;
  event.preventDefault();
  const kind = form.dataset.mfaPassword;
  const password = $("#mfa-password").value;
  message("#mfa-message", "");
  try {
    if (kind === "disable") {
      await postJson("/api/auth/mfa/disable", { password });
      message("#mfa-message", t("sec.mfa.turned_off"), "success");
      await refreshAuthStatus();
    } else {
      const data = await postJson("/api/auth/mfa/recovery", { password });
      ob.mfaBusy = true;
      showRecoveryCodes(
        $("#mfa-panel"),
        data.recoveryCodes,
        async () => {
          ob.mfaBusy = false;
          await refreshAuthStatus();
        },
        { heading: false }
      );
    }
  } catch (error) {
    message("#mfa-message", error.message);
  }
});

// Een andere taal: de installatiehulp opnieuw tekenen en bewaren wat je al had ingevuld. Midden in het
// instellen van tweestapsverificatie niet opnieuw tekenen (dat zou een nieuwe sleutel maken); de vaste
// teksten daar wisselen mee via data-i18n.
window.addEventListener("ms:languagechange", () => {
  if (!onboardingVisible()) return;
  if (ob.mfaBusy) {
    $("#ob-eyebrow").textContent = t("ob.step_of", { n: stepIndex(ob.step) + 1, total: obSteps().length });
    $("#ob-title").textContent = t(`ob.title.${ob.step}`);
    $("#ob-progress").innerHTML = obProgressHtml();
    I18N.apply($("#ob-body"));
    return;
  }
  const body = $("#ob-body");
  const saved = [...body.querySelectorAll("input[id]")].map((input) => [input.id, input.type === "checkbox" ? input.checked : input.value]);
  ob.rendered = "";
  renderOnboarding();
  for (const [id, value] of saved) {
    const input = document.getElementById(id);
    if (!input) continue;
    if (input.type === "checkbox") {
      input.checked = value;
      input.dispatchEvent(new Event("change", { bubbles: true }));
    } else if (!(id === "ob-passkey-name" && Object.values(I18N.messages).some((m) => m["ob.passkey.default_name"] === value))) {
      input.value = value; // een nog onaangeraakte standaardnaam wisselt gewoon mee van taal
    }
  }
});
