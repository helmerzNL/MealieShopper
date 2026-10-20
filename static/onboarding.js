// Installatiehulp voor de eerste start, en het beheer van wachtwoord en tweestapsverificatie (MFA).
// Wordt na app.js geladen en gebruikt de hulpfuncties daarvan ($, message, jsonFetch, escapeHtml, state).

const OB_STEPS = [
  { id: "auth", label: "Beveiliging", title: "Beveilig MealieShopper" },
  { id: "mealie", label: "Mealie", title: "Koppel je Mealie" },
  { id: "ah", label: "Albert Heijn", title: "Koppel Albert Heijn" },
  { id: "done", label: "Klaar", title: "Alles staat klaar" },
];

const ob = { step: null, sub: "choose", rendered: "", started: false, mfaBusy: false };

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
  return OB_STEPS.findIndex((step) => step.id === id);
}

function renderOnboarding() {
  const root = $("#onboarding");
  if (!root) return;
  if (!onboardingVisible()) {
    root.hidden = true;
    document.body.classList.remove("has-onboarding");
    return;
  }
  const status = state.onboarding;
  if (root.hidden) {
    root.hidden = false;
    document.body.classList.add("has-onboarding");
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
  return OB_STEPS.map((step, index) => {
    const state_ = index < current ? "is-done" : index === current ? "is-current" : "";
    return `<li class="${state_}"${index === current ? ' aria-current="step"' : ""}><span class="ob-progress__dot" aria-hidden="true">${
      index < current ? "&#10003;" : index + 1
    }</span><span class="ob-progress__label">${escapeHtml(step.label)}</span></li>`;
  }).join("");
}

function renderObStep(force = false) {
  const key = `${ob.step}/${ob.sub}`;
  if (!force && ob.rendered === key) return;
  ob.rendered = key;
  const index = stepIndex(ob.step);
  $("#ob-eyebrow").textContent = `Stap ${index + 1} van ${OB_STEPS.length}`;
  $("#ob-title").textContent = OB_STEPS[index].title;
  $("#ob-progress").innerHTML = obProgressHtml();
  const body = $("#ob-body");
  const renderers = { auth: renderObAuth, mealie: renderObMealie, ah: renderObAh, done: renderObDone };
  renderers[ob.step](body);
  $("#onboarding").scrollTo?.({ top: 0 });
  $("#ob-title").focus({ preventScroll: true });
}

// ------------------------------------------------------------------ stap 1: beveiliging

function obEnvironmentNote() {
  const env = state.onboarding || {};
  const where = escapeHtml(env.host || window.location.hostname);
  return `<p class="ob-env">Je opent MealieShopper via <strong>${where}</strong> ${
    env.secure ? "(beveiligd met HTTPS)" : "(zonder HTTPS)"
  }.</p>`;
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
      <span class="ob-badge">Aanbevolen</span>
      <h3>Passkey</h3>
      <p>Inloggen met je vingerafdruk, gezicht of schermvergrendeling. Er is geen wachtwoord om te onthouden, te raden of te lekken.</p>
      <button class="button" type="button" data-ob-action="go-passkey">Passkey instellen</button>
    </article>`;
  const password = (primary) => `
    <article class="ob-option${primary ? " ob-option--recommended" : ""}">
      ${primary ? '<span class="ob-badge">Aanbevolen</span>' : ""}
      <h3>Gebruikersnaam en wachtwoord</h3>
      <p>Een gewoon wachtwoord. Je kunt er daarna tweestapsverificatie bij zetten met een authenticator-app, zodat een gelekt wachtwoord niet genoeg is.</p>
      <button class="button${primary ? "" : " button--secondary"}" type="button" data-ob-action="go-password">Wachtwoord instellen</button>
    </article>`;
  body.innerHTML = status.passkeyAvailable
    ? `
      <p>Welkom bij MealieShopper. In een paar stappen is alles klaar: eerst beveilig je de app, daarna koppel je Mealie en Albert Heijn.</p>
      ${obEnvironmentNote()}
      <p>Je hebt een domeinnaam met HTTPS, dus je kunt kiezen hoe je wilt inloggen:</p>
      <div class="ob-options">${passkey}${password(false)}</div>`
    : `
      <p>Welkom bij MealieShopper. In een paar stappen is alles klaar: eerst beveilig je de app, daarna koppel je Mealie en Albert Heijn.</p>
      ${obEnvironmentNote()}
      <div class="message warning ob-note">
        <strong>Passkeys werken op dit adres niet.</strong>
        ${escapeHtml(status.passkeyUnavailableReason || "")}
        Heb je een domeinnaam met HTTPS (bijvoorbeeld via een reverse proxy), open MealieShopper dan via dat adres als je een passkey wilt.
      </div>
      <p>Zonder domeinnaam kun je inloggen met een gebruikersnaam en wachtwoord. Dat is optioneel, net als tweestapsverificatie.</p>
      <div class="ob-options">${password(true)}</div>
      <p class="ob-skip"><button class="link-button" type="button" data-ob-action="go-open">Zonder beveiliging verder (niet aanbevolen)</button></p>`;
}

function obBackButton(action = "go-choose", label = "Terug") {
  return `<button class="button button--secondary" type="button" data-ob-action="${action}">${label}</button>`;
}

function obAuthPasskey(body, status) {
  const unavailable = webauthnUnavailableReason();
  body.innerHTML = `
    <p>Je telefoon, laptop of beveiligingssleutel vraagt je zo om je vingerafdruk, gezicht of schermvergrendeling. Je kunt later extra passkeys toevoegen onder Beheer &gt; Beveiliging.</p>
    ${unavailable ? `<div class="message error">${escapeHtml(unavailable)}</div>` : ""}
    <form class="auth-form" data-ob-form="passkey" autocomplete="off">
      <label class="field"><span>Gebruikersnaam</span><input class="input" id="ob-username" value="admin" autocomplete="username" autocapitalize="none" spellcheck="false"></label>
      <label class="field"><span>Naam van deze passkey</span><input class="input" id="ob-passkey-name" value="Mijn passkey" autocomplete="off"></label>
      <div class="ob-actions">${obBackButton()}<button class="button" type="submit" ${unavailable ? "disabled" : ""}>Passkey maken</button></div>
    </form>`;
}

function obAuthPassword(body, status) {
  const min = status.minPasswordLength || 10;
  body.innerHTML = `
    <p>Kies een wachtwoord van minstens ${min} tekens. Een zin van een paar woorden is sterker dan één woord met een cijfer, en makkelijker te onthouden. Een wachtwoordmanager mag.</p>
    <form class="auth-form" data-ob-form="password" autocomplete="on">
      <label class="field"><span>Gebruikersnaam</span><input class="input" id="ob-username" value="admin" autocomplete="username" autocapitalize="none" spellcheck="false" required></label>
      <label class="field"><span>Wachtwoord</span><input class="input" id="ob-password" type="password" autocomplete="new-password" minlength="${min}" required></label>
      <label class="field"><span>Herhaal het wachtwoord</span><input class="input" id="ob-password2" type="password" autocomplete="new-password" required></label>
      <label class="check"><input type="checkbox" id="ob-show-password"> Wachtwoord tonen</label>
      <div class="ob-actions">${obBackButton()}<button class="button" type="submit">Wachtwoord opslaan</button></div>
    </form>`;
}

function obAuthMfaOffer(body) {
  body.innerHTML = `
    <div class="message success">Je wachtwoord is ingesteld en je bent ingelogd.</div>
    <h3 class="ob-subtitle">Tweestapsverificatie (optioneel)</h3>
    <p>Met tweestapsverificatie vraagt MealieShopper bij het inloggen ook om een code van 6 cijfers uit een authenticator-app, zoals Google Authenticator, Microsoft Authenticator, 1Password, Bitwarden of Aegis. Zo komt niemand binnen met alleen je wachtwoord.</p>
    <div class="ob-actions">
      <button class="button button--secondary" type="button" data-ob-action="skip-mfa">Overslaan, ik doe dit later</button>
      <button class="button" type="button" data-ob-action="go-mfa">Tweestapsverificatie instellen</button>
    </div>`;
}

function obAuthMfaSetup(body) {
  body.innerHTML = `<div id="ob-mfa"></div>`;
  mfaEnroll($("#ob-mfa"), {
    messageTarget: "#ob-message",
    cancelLabel: "Nu niet",
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
      <strong>Zonder beveiliging kan iedereen die dit adres kent MealieShopper gebruiken.</strong>
      Dat geldt ook voor je gekoppelde Mealie en je Albert Heijn-account: een ander kan je recepten importeren, je lijstjes lezen en je winkelmandje vullen. Doe dit alleen op een netwerk dat je vertrouwt, niet op internet.
    </div>
    <p>Je kunt later altijd een wachtwoord instellen onder Beheer &gt; Beveiliging.</p>
    <form class="auth-form" data-ob-form="open">
      <label class="check"><input type="checkbox" id="ob-open-confirm"> Ik begrijp het risico en wil zonder beveiliging verder.</label>
      <div class="ob-actions">${obBackButton()}<button class="button" type="submit" id="ob-open-submit" disabled>Verder zonder beveiliging</button></div>
    </form>`;
}

// ------------------------------------------------------------------ stap 2 t/m 4

function obNavigation({ back, next, nextLabel = "Volgende" }) {
  return `<div class="ob-actions">${back ? obBackButton(`go-${back}`) : "<span></span>"}<button class="button" type="button" data-ob-action="go-${next}">${nextLabel}</button></div>`;
}

function renderObMealie(body) {
  body.innerHTML = `
    <p>MealieShopper haalt je weekmenu en recepten uit Mealie en zet geïmporteerde recepten daar neer. Daarvoor heeft de app het adres van je Mealie en een API-token nodig.</p>
    <div id="ob-mealie-status"></div>
    <h3 class="ob-subtitle">Zo koppel je Mealie</h3>
    <ol class="steps">
      <li>Open Mealie en ga naar je profiel, onderdeel <strong>API-tokens</strong>.</li>
      <li>Geef de token een naam, bijvoorbeeld <em>MealieShopper</em>, en kies <strong>Token genereren</strong>.</li>
      <li>Kopieer de token meteen: Mealie toont hem maar één keer.</li>
      <li>Open in MealieShopper <strong>Beheer &gt; Mealie</strong>, vul het adres van je Mealie in (bijvoorbeeld <code>https://mealie.jouwdomein.nl</code>) en plak de token.</li>
      <li>Kies <strong>Koppelen</strong>. MealieShopper controleert het adres en de token bij Mealie voordat er iets wordt bewaard.</li>
    </ol>
    <p class="muted">Het adres moet bereikbaar zijn vanaf de plek waar MealieShopper draait, niet per se vanaf je telefoon. Draaien ze in dezelfde Docker-omgeving, dan kan dat ook een interne naam zijn zoals <code>http://mealie:9000</code>. De token wordt versleuteld opgeslagen en nooit meer getoond.</p>
    ${obNavigation({ next: "ah" })}`;
  loadObIntegrationStatus("mealie");
}

function renderObAh(body) {
  body.innerHTML = `
    <p>Met je Albert Heijn-account kan MealieShopper je favoriete lijstjes en bewaarde recepten ophalen en je boodschappen in het AH-mandje zetten. Zoeken naar recepten werkt ook zonder koppeling.</p>
    <div id="ob-ah-status"></div>
    <h3 class="ob-subtitle">Zo koppel je Albert Heijn</h3>
    <div class="message warning ob-note">Dit gaat op een <strong>computer</strong> (niet op een telefoon of tablet), omdat je de browserconsole nodig hebt. AH biedt geen officiële koppeling; daarom loopt het zo.</div>
    <ol class="steps">
      <li>Open <strong>Beheer &gt; Albert Heijn</strong> en kies <strong>Inloggen bij Albert Heijn</strong>. De AH-loginpagina opent in een nieuw tabblad.</li>
      <li>Open in dat tabblad de ontwikkelaarsconsole (<code>F12</code>, of <code>Ctrl</code>+<code>Shift</code>+<code>J</code>, op een Mac <code>⌘</code>+<code>⌥</code>+<code>J</code>) <em>voordat</em> je inlogt.</li>
      <li>Log in met je passkey of wachtwoord. De pagina lijkt daarna vast te lopen: AH probeert de AH-app te openen. Dat is normaal.</li>
      <li>In de console staat nu een rode regel met <code>appie://login-exit?code=...</code>. Kopieer die regel.</li>
      <li>Plak hem in MealieShopper onder <strong>Koppeling afronden</strong> en kies direct <strong>Koppelen</strong>. De code werkt maar één keer en verloopt snel.</li>
    </ol>
    <p class="muted">Dit kan ook later: je kunt MealieShopper eerst gebruiken en AH daarna koppelen.</p>
    ${obNavigation({ back: "mealie", next: "done" })}`;
  loadObIntegrationStatus("ah");
}

async function loadObIntegrationStatus(which) {
  const target = which === "mealie" ? "#ob-mealie-status" : "#ob-ah-status";
  try {
    if (which === "mealie") {
      const data = await jsonFetch("/api/mealie/status");
      message(
        target,
        data.configured ? `Mealie is al gekoppeld: ${data.url}` : "Mealie is nog niet gekoppeld.",
        data.configured ? "success" : "warning"
      );
    } else {
      const data = await jsonFetch("/api/ah/auth/status");
      message(
        target,
        data.connected ? "Je AH-account is al gekoppeld." : "Je AH-account is nog niet gekoppeld.",
        data.connected ? "success" : "warning"
      );
    }
  } catch {
    $(target).innerHTML = "";
  }
}

function authModeLabel() {
  const status = state.onboarding || {};
  const auth = state.auth || {};
  if (status.authMode === "off") return "Inloggen staat uit via de omgevingsvariabelen";
  if (status.authMode === "none" || !auth.enabled) return "Zonder inloggen";
  const parts = [];
  if (auth.methods?.passkey) parts.push("passkey");
  if (auth.methods?.password) parts.push("wachtwoord");
  if (auth.mfaEnabled) parts.push("tweestapsverificatie");
  return `Inloggen met ${parts.join(" en ") || "wachtwoord"}`;
}

async function renderObDone(body) {
  body.innerHTML = `
    <p>MealieShopper is klaar voor gebruik. Dit staat er nu:</p>
    <ul class="ob-checklist" id="ob-checklist"></ul>
    <div class="ob-actions ob-actions--wrap" id="ob-done-actions"></div>`;
  const [mealie, ah] = await Promise.all([
    jsonFetch("/api/mealie/status").catch(() => ({})),
    jsonFetch("/api/ah/auth/status").catch(() => ({})),
  ]);
  const item = (ok, text) => `<li class="${ok ? "is-ok" : "is-todo"}"><span class="ob-check" aria-hidden="true">${ok ? "&#10003;" : "&#9675;"}</span><span>${text}</span></li>`;
  $("#ob-checklist").innerHTML =
    item(true, `Beveiliging: ${escapeHtml(authModeLabel())}`) +
    item(Boolean(mealie.configured), mealie.configured ? `Mealie gekoppeld: ${escapeHtml(mealie.url || "")}` : "Mealie nog niet gekoppeld") +
    item(Boolean(ah.connected), ah.connected ? "Albert Heijn gekoppeld" : "Albert Heijn nog niet gekoppeld (optioneel)");
  const actions = [];
  if (!mealie.configured) actions.push(`<button class="button" type="button" data-ob-action="finish-mealie">Mealie nu koppelen</button>`);
  if (!ah.connected) actions.push(`<button class="button button--secondary" type="button" data-ob-action="finish-ah">Albert Heijn nu koppelen</button>`);
  actions.push(
    `<button class="button${actions.length ? " button--secondary" : ""}" type="button" data-ob-action="finish">MealieShopper openen</button>`
  );
  $("#ob-done-actions").innerHTML = actions.join("");
}

async function finishOnboarding(openSubtab) {
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
  if (openSubtab) openBeheer(openSubtab);
  else window.scrollTo({ top: 0 });
}

// ---------------------------------------------------------------- acties en formulieren

async function obSubmitPasskey() {
  const username = $("#ob-username").value.trim() || "admin";
  const credentialName = $("#ob-passkey-name").value.trim() || "Mijn passkey";
  await registerPasskey({ username, credentialName, messageTarget: "#ob-message" });
  await refreshAuthStatus();
  obGo("mealie");
}

async function obSubmitPassword() {
  const password = $("#ob-password").value;
  if (password !== $("#ob-password2").value) throw new Error("De twee wachtwoorden zijn niet hetzelfde.");
  await postJson("/api/auth/password", { username: $("#ob-username").value, password });
  await refreshAuthStatus();
  obGo("auth", "mfa-offer");
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
  "go-ah": () => obGo("ah"),
  "go-done": () => obGo("done"),
  finish: () => finishOnboarding(),
  "finish-mealie": () => finishOnboarding("beheer-mealie"),
  "finish-ah": () => finishOnboarding("beheer-ah"),
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
});

$("#ob-body").addEventListener("submit", async (event) => {
  const form = event.target.closest("[data-ob-form]");
  if (!form) return;
  event.preventDefault();
  const submit = form.querySelector('[type="submit"]');
  const handlers = { passkey: obSubmitPasskey, password: obSubmitPassword, open: obSubmitOpen };
  if (submit) submit.disabled = true;
  message("#ob-message", "");
  try {
    await handlers[form.dataset.obForm]();
  } catch (error) {
    message("#ob-message", error.name === "NotAllowedError" ? "Passkey prompt geannuleerd." : error.message);
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
    script.onerror = () => reject(new Error("QR-code kon niet worden getoond."));
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
    slot.innerHTML = `<p class="muted">De QR-code kon niet worden getoond. Gebruik de sleutel hiernaast.</p>`;
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

async function mfaEnroll(container, { onDone, onCancel, cancelLabel = "Annuleren", messageTarget }) {
  ob.mfaBusy = true;
  container.innerHTML = `<p class="muted">Even geduld...</p>`;
  let setup;
  try {
    setup = await postJson("/api/auth/mfa/start");
  } catch (error) {
    ob.mfaBusy = false;
    container.innerHTML = "";
    message(messageTarget, error.message);
    return;
  }
  const secret = escapeHtml(setup.secret);
  container.innerHTML = `
    <ol class="steps">
      <li>Installeer een authenticator-app, zoals Google Authenticator, Microsoft Authenticator, 1Password, Bitwarden of Aegis.</li>
      <li>Scan de QR-code met die app.</li>
      <li>Vul hieronder de code van 6 cijfers in die de app laat zien.</li>
    </ol>
    <div class="qr-box">
      <div class="qr" role="img" aria-label="QR-code voor tweestapsverificatie"></div>
      <div class="qr-help">
        <p class="muted">Lukt scannen niet, of zit je op de telefoon waarop de app staat?</p>
        <a class="button button--secondary" href="${escapeHtml(setup.otpauthUri)}">Open in je authenticator-app</a>
        <p class="muted">Of typ deze sleutel over (tijdgebaseerd, 6 cijfers):</p>
        <p><code class="qr-secret" data-mfa-secret>${secret}</code></p>
      </div>
    </div>
    <form class="auth-form" data-mfa-confirm autocomplete="off">
      <label class="field"><span>Code uit de app</span><input class="input" data-mfa-code inputmode="numeric" autocomplete="one-time-code" placeholder="123456" maxlength="7" required></label>
      <div class="ob-actions"><button class="button button--secondary" type="button" data-mfa-cancel>${escapeHtml(cancelLabel)}</button><button class="button" type="submit">Bevestigen</button></div>
    </form>`;
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
    ${heading ? '<div class="message success">Tweestapsverificatie staat aan.</div>' : ""}
    <h3 class="ob-subtitle">Bewaar je herstelcodes</h3>
    <p>Raak je telefoon kwijt, dan log je met een van deze codes in. Elke code werkt één keer. Ze worden hierna <strong>niet meer getoond</strong>: bewaar ze in een wachtwoordmanager of print ze.</p>
    <ul class="recovery-codes" data-recovery-codes>${codes.map((code) => `<li><code>${escapeHtml(code)}</code></li>`).join("")}</ul>
    <div class="ob-actions ob-actions--wrap">
      <button class="button button--secondary" type="button" data-recovery-copy>Kopiëren</button>
      <button class="button button--secondary" type="button" data-recovery-download>Downloaden (.txt)</button>
    </div>
    <label class="check"><input type="checkbox" data-recovery-saved> Ik heb mijn herstelcodes veilig bewaard.</label>
    <div class="ob-actions"><span></span><button class="button" type="button" data-recovery-continue disabled>Doorgaan</button></div>`;
  const status = container.querySelector("[data-recovery-copy]");
  status.addEventListener("click", async () => {
    status.textContent = (await copyText(text)) ? "Gekopieerd" : "Kopiëren lukt niet, selecteer de codes zelf";
  });
  container.querySelector("[data-recovery-download]").addEventListener("click", () => {
    const blob = new Blob([`MealieShopper herstelcodes\n\n${text}\n`], { type: "text/plain" });
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

function renderSecurityPanel() {
  const passwordBox = $("#security-password-box");
  const mfaBox = $("#security-mfa-box");
  if (!passwordBox || !mfaBox) return;
  const auth = state.auth;
  const envOff = state.onboarding && state.onboarding.authEnabled === false;
  const canManage = Boolean(auth) && (auth.authenticated || !auth.enabled) && !envOff;
  passwordBox.hidden = !canManage;
  mfaBox.hidden = !canManage;
  if (!canManage) return;

  const configured = Boolean(auth.passwordConfigured);
  $("#password-intro").textContent = configured
    ? "Je kunt hier je wachtwoord of gebruikersnaam wijzigen. Andere apparaten moeten daarna opnieuw inloggen."
    : !auth.enabled
      ? "MealieShopper is nu open voor iedereen die het adres kent. Stel een wachtwoord in om inloggen aan te zetten."
      : "Je logt nu in met een passkey. Je kunt er ook een wachtwoord naast zetten, bijvoorbeeld voor een apparaat zonder passkey.";
  $("#pw-current-field").hidden = !configured;
  $("#pw-new-label").textContent = configured ? "Nieuw wachtwoord" : "Wachtwoord";
  $("#pw-submit").textContent = configured ? "Wachtwoord wijzigen" : "Wachtwoord instellen";
  const username = $("#pw-username");
  if (!username.value && document.activeElement !== username) username.value = auth.username || "admin";

  const note = $("#mfa-intro");
  const panel = $("#mfa-panel");
  if (ob.mfaBusy) return; // niet midden in het instellen opnieuw tekenen
  if (!configured) {
    note.textContent = "Tweestapsverificatie hoort bij inloggen met een wachtwoord. Stel eerst een wachtwoord in.";
    panel.innerHTML = "";
  } else if (auth.mfaEnabled) {
    note.textContent = `Staat aan. Bij het inloggen vraagt MealieShopper ook om een code uit je authenticator-app. Je hebt nog ${auth.recoveryCodesLeft ?? 0} herstelcodes.`;
    panel.innerHTML = `<div class="ob-actions ob-actions--wrap">
      <button class="button button--secondary" type="button" data-mfa-action="regen">Nieuwe herstelcodes maken</button>
      <button class="button button--secondary" type="button" data-mfa-action="disable">Uitzetten</button></div>`;
  } else {
    note.textContent = "Staat uit. Met een authenticator-app voeg je bij het inloggen een code van 6 cijfers toe, zodat een gelekt wachtwoord niet genoeg is.";
    panel.innerHTML = `<button class="button" type="button" data-mfa-action="start">Tweestapsverificatie instellen</button>`;
  }
}

async function submitPasswordForm(event) {
  event.preventDefault();
  const button = $("#pw-submit");
  const password = $("#pw-new").value;
  message("#password-message", "");
  if (password !== $("#pw-new2").value) {
    message("#password-message", "De twee wachtwoorden zijn niet hetzelfde.");
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
    message("#password-message", data.replaced ? "Wachtwoord gewijzigd." : "Wachtwoord ingesteld. Je bent ingelogd.", "success");
    await refreshAuthStatus();
  } catch (error) {
    message("#password-message", error.message);
  } finally {
    button.disabled = false;
  }
}

function askPassword(kind) {
  const labels = {
    regen: "Maak nieuwe herstelcodes",
    disable: "Tweestapsverificatie uitzetten",
  };
  $("#mfa-panel").innerHTML = `
    <form class="auth-form" data-mfa-password="${kind}" autocomplete="off">
      <label class="field"><span>Bevestig met je wachtwoord</span><input class="input" type="password" id="mfa-password" autocomplete="current-password" required></label>
      <div class="ob-actions"><button class="button button--secondary" type="button" data-mfa-action="cancel">Annuleren</button><button class="button" type="submit">${labels[kind]}</button></div>
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
      message("#mfa-message", "Tweestapsverificatie staat uit.", "success");
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
