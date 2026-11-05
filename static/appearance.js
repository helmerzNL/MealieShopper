// Instellingen, Weergave: het thema, de achtergrond van het donkere thema, de accentkleur en de navigatiebalk, per gebruiker.
// Het toepassen en het rekenen met kleuren doet static/theme.js (window.THEME); dit bestand tekent de keuzes en bewaart ze
// bij het profiel (PUT /api/profile/preferences). Na het inloggen zet appearanceFromProfile() de keuze van het profiel.
// Laadt na app.js en profile.js en gebruikt daar de hulpfuncties van ($, message, jsonFetch, t, escapeHtml, state, ...).

const APPEARANCE_CHECK = "M21,7L9,19L3.5,13.5L4.91,12.09L9,16.17L19.59,5.59L21,7Z";
// Waar terwijl iemand in de kleurkiezer schuift: dan niet opnieuw tekenen, want dat zou de kiezer sluiten.
let appearancePicking = false;

// Mag de keuze naar het profiel? Alleen als iemand ingelogd is (of inloggen uit staat): op het inlogscherm blijft hij in de browser.
function appearanceSignedIn() {
  const auth = state.auth || {};
  return Boolean(auth.authenticated) || auth.enabled === false;
}

// De keuze van het profiel toepassen, na het laden van /api/auth/status. Een thema van null is nog niet gekozen: dan blijft
// wat deze browser had, zoals voor er een menu Weergave was.
function appearanceFromProfile() {
  const auth = state.auth || {};
  if (!appearanceSignedIn() || !("accent" in auth)) return;
  THEME.apply({
    theme: auth.theme,
    darkBackground: auth.darkBackground,
    accent: auth.accent,
    navbarStyle: auth.navbarStyle,
    navbarGlass: auth.navbarGlass,
  });
  renderAppearance();
}

// Bewaren gaat optimistisch: de keuze is al te zien. Weigert de server hem, dan komt de vorige keuze terug (previous) en staat
// de melding van de server bij het blok (target).
async function appearanceSave(change, { previous = null, target = "#appearance-message" } = {}) {
  if (!appearanceSignedIn()) return;
  message(target, "");
  try {
    const data = await jsonFetch("/api/profile/preferences", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(change),
    });
    state.auth = {
      ...state.auth,
      theme: data.theme,
      darkBackground: data.darkBackground,
      accent: data.accent,
      navbarStyle: data.navbarStyle,
      navbarGlass: data.navbarGlass,
    };
  } catch (error) {
    if (previous) {
      THEME.apply(previous);
      renderAppearance();
    }
    message(target, error.message);
  }
}

// De tint die je in dit thema krijgt: die tekent het rondje, zodat het toont wat je ziet.
function appearanceShown(accent) {
  const dark = THEME.current() === "dark";
  const tints = THEME.variants(accent);
  if (!tints) return { color: dark ? THEME.BRAND.dark : THEME.BRAND.light, on: dark ? "#0b1f15" : "#ffffff" };
  return dark ? { color: tints["--accent-dark"], on: tints["--accent-dark-on"] } : { color: tints["--accent-light"], on: tints["--accent-light-on"] };
}

function appearancePreset(accent) {
  return THEME.ACCENTS.find(([value]) => value === accent) || null;
}

function renderAppearanceAccent(look) {
  const list = $("#appearance-accent-list");
  if (!list) return;
  const preset = appearancePreset(look.accent);
  const custom = preset ? "" : look.accent;
  const swatches = THEME.ACCENTS.map(([value, key]) => {
    const label = t(key);
    return `<label class="appearance-swatch" title="${escapeHtml(label)}" data-accent-value="${escapeHtml(value)}">
        <input type="radio" name="appearance-accent" value="${escapeHtml(value)}" aria-label="${escapeHtml(label)}"${value === look.accent ? " checked" : ""}>
        ${mdiIcon(APPEARANCE_CHECK)}
      </label>`;
  });
  const customLabel = t("appearance.accent.custom");
  swatches.push(`<label class="appearance-custom${custom ? " is-chosen" : ""}" data-accent-value="${escapeHtml(custom)}">
      <span class="appearance-custom__dot">${mdiIcon(APPEARANCE_CHECK)}<input type="color" id="appearance-accent-custom" value="${escapeHtml(custom || THEME.BRAND.light)}" aria-label="${escapeHtml(customLabel)}"></span>
      <span>${escapeHtml(customLabel)}</span>
    </label>`);
  list.innerHTML = swatches.join("");
  list.querySelectorAll("[data-accent-value]").forEach((swatch) => {
    const value = swatch.dataset.accentValue;
    if (swatch.classList.contains("appearance-custom") && !value) return; // nog geen eigen kleur: de regenboog
    const shown = appearanceShown(value);
    swatch.style.setProperty("--swatch", shown.color);
    swatch.style.setProperty("--swatch-on", shown.on);
  });
  renderAppearanceCurrent(look);
}

// Wat er gekozen is, in woorden; bij een eigen kleur ook de tinten die MealieShopper ervan maakt als die afwijken.
function renderAppearanceCurrent(look) {
  const target = $("#appearance-accent-current");
  if (!target) return;
  const preset = appearancePreset(look.accent);
  const name = preset ? t(preset[1]) : t("appearance.accent.custom_named", { color: look.accent });
  let text = t("appearance.accent.current", { name });
  const tints = !preset ? THEME.variants(look.accent) : null;
  if (tints && (tints["--accent-light"] !== look.accent || tints["--accent-dark"] !== look.accent)) {
    text += ` ${t("appearance.accent.adjusted", { light: tints["--accent-light"], dark: tints["--accent-dark"] })}`;
  }
  target.textContent = text;
}

// De navigatiebalk: de stijl, wat Automatisch nu is, en de schuif voor het glas (alleen bij de iOS-achtige balk: de
// Android-achtige heeft geen glas).
function renderAppearanceNav(look) {
  document.querySelectorAll('input[name="appearance-nav"]').forEach((input) => {
    input.checked = input.value === look.navbarStyle;
  });
  const shown = t(look.navShown === "android" ? "appearance.nav.android" : "appearance.nav.ios");
  const current = $("#appearance-nav-current");
  if (current) {
    current.textContent =
      look.navbarStyle === "auto"
        ? t("appearance.nav.auto_hint", { style: shown })
        : t(look.navShown === "android" ? "appearance.nav.android_hint" : "appearance.nav.ios_hint");
  }
  const row = $("#appearance-glass-row");
  if (row) row.hidden = look.navShown !== "ios";
  const slider = $("#appearance-glass");
  if (slider && document.activeElement !== slider) slider.value = String(look.navbarGlass);
  const value = $("#appearance-glass-value");
  if (value) value.textContent = t("appearance.glass.value", { n: slider ? slider.value : look.navbarGlass });
}

function renderAppearance() {
  if (!$("#settings-appearance") || typeof THEME === "undefined") return;
  const look = THEME.appearance();
  document.querySelectorAll('input[name="appearance-theme"]').forEach((input) => {
    input.checked = input.value === look.theme;
  });
  document.querySelectorAll('input[name="appearance-dark-bg"]').forEach((input) => {
    input.checked = input.value === look.darkBackground;
  });
  renderAppearanceAccent(look);
  renderAppearanceNav(look);
}

$("#appearance-theme").addEventListener("change", (event) => {
  if (event.target.name === "appearance-theme") THEME.choose(event.target.value); // het bewaren volgt via ms:appearancechange
});

$("#appearance-dark-bg").addEventListener("change", (event) => {
  if (event.target.name !== "appearance-dark-bg") return;
  THEME.apply({ darkBackground: event.target.value });
  appearanceSave({ darkBackground: THEME.appearance().darkBackground });
});

// Een rondje kiezen, of een eigen kleur: tijdens het schuiven in de kleurkiezer verandert de pagina mee (input), bewaard
// wordt pas als de kiezer dicht gaat (change).
$("#appearance-accent").addEventListener("change", (event) => {
  const input = event.target;
  if (input.name !== "appearance-accent" && input.id !== "appearance-accent-custom") return;
  appearancePicking = false;
  THEME.apply({ accent: input.value });
  renderAppearance();
  appearanceSave({ accent: THEME.appearance().accent });
});

$("#appearance-accent").addEventListener("input", (event) => {
  if (event.target.id !== "appearance-accent-custom") return;
  appearancePicking = true;
  THEME.apply({ accent: event.target.value });
  renderAppearanceCurrent(THEME.appearance());
});

$("#appearance-nav").addEventListener("change", (event) => {
  if (event.target.name !== "appearance-nav") return;
  const previous = { navbarStyle: THEME.appearance().navbarStyle };
  THEME.apply({ navbarStyle: event.target.value });
  appearanceSave({ navbarStyle: THEME.appearance().navbarStyle }, { previous, target: "#appearance-nav-message" });
});

// De schuif: tijdens het schuiven verandert alleen het glas (de balk onderaan toont het meteen), bewaard wordt pas bij loslaten.
$("#appearance-glass").addEventListener("input", (event) => {
  const glass = Number(event.target.value);
  THEME.applyNavGlass(glass);
  $("#appearance-glass-value").textContent = t("appearance.glass.value", { n: glass });
});

$("#appearance-glass").addEventListener("change", (event) => {
  const previous = { navbarGlass: THEME.appearance().navbarGlass };
  THEME.apply({ navbarGlass: Number(event.target.value) });
  appearanceSave({ navbarGlass: THEME.appearance().navbarGlass }, { previous, target: "#appearance-nav-message" });
});

// Het thema (ook met de knop bovenaan) bij het profiel bewaren.
window.addEventListener("ms:appearancechange", (event) => {
  renderAppearance();
  if (event.detail?.theme) appearanceSave({ theme: event.detail.theme });
});

// Na elke wissel van thema tonen de rondjes de tint van dat thema (niet tijdens het schuiven met de kleurkiezer).
window.addEventListener("ms:appearancepaint", () => {
  if (!appearancePicking) renderAppearance();
});

window.addEventListener("ms:languagechange", renderAppearance);
renderAppearance();
