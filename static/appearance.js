// Instellingen, Voorkeuren, per gebruiker, in drie inklapbare blokken (zoals onder Koppelen): Supermarkt (de
// voorkeurssupermarkt waarmee Aanbiedingen en Winkels openen, en hoe Naar weekmenu losse producten uit de weekplanner van de
// AH-app in Mealie zet), Weergave (de kookmodus en de calorieën) en Thema (de accentkleur, het thema met de achtergrond van het
// donkere thema, en de navigatiebalk).
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
// wat deze browser had, zoals voor er een menu Weergave (nu Voorkeuren) was.
function appearanceFromProfile() {
  const auth = state.auth || {};
  renderAppearancePlanner(); // de keuzes voor de AH-weekplanner, los van de weergave
  if (!appearanceSignedIn() || !("accent" in auth)) return;
  THEME.apply({
    theme: auth.theme,
    darkBackground: auth.darkBackground,
    accent: auth.accent,
    navbarStyle: auth.navbarStyle,
    navbarGlass: auth.navbarGlass,
    cookingMode: auth.cookingMode,
    showCalories: auth.showCalories,
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
      cookingMode: data.cookingMode,
      showCalories: data.showCalories,
      preferredStore: data.preferredStore,
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

// De voorkeurssupermarkt: de gekozen winkel, en een regel als die nog niet gekoppeld is (dan openen de pagina's met AH).
function renderAppearanceStore() {
  const store = preferredSupermarket();
  document.querySelectorAll('input[name="appearance-store"]').forEach((input) => {
    input.checked = input.value === store;
  });
  const current = $("#appearance-store-current");
  if (!current) return;
  const unlinked = store !== "ah" && state.storeStatusKnown[store] && !state.linkedStores[store];
  current.textContent = unlinked ? t("appearance.store.unlinked", { store: STORES[store].name }) : "";
  current.hidden = !unlinked;
}

// Een andere voorkeurssupermarkt: meteen toepassen (Aanbiedingen en Winkels) en bij het profiel bewaren. Weigert de server hem,
// dan komt de vorige terug.
async function appearanceSaveStore(store) {
  const previous = preferredSupermarket();
  if (store === previous) return;
  state.auth = { ...state.auth, preferredStore: store };
  preferredSupermarketChanged();
  renderAppearanceStore();
  message("#appearance-store-message", "");
  if (!appearanceSignedIn()) return;
  try {
    const data = await jsonFetch("/api/profile/preferences", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ preferredStore: store }),
    });
    state.auth = { ...state.auth, preferredStore: data.preferredStore };
  } catch (error) {
    state.auth = { ...state.auth, preferredStore: previous };
    preferredSupermarketChanged();
    renderAppearanceStore();
    message("#appearance-store-message", error.message);
  }
}

// De weekplanner van de AH-app (4.3): losse producten van een dag samen als één menu, en soep en pizza dan toch los. Standaard
// aan; de tweede heeft alleen zin als de eerste aan staat. Winkels, Albert Heijn, Weekplanner (ah_planner.js) leest ze.
function renderAppearancePlanner() {
  const combine = $("#appearance-planner-combine");
  if (!combine) return;
  combine.checked = state.auth?.plannerCombine !== false;
  $("#appearance-planner-split-meals").checked = state.auth?.plannerSplitMeals !== false;
  $("#appearance-planner-split-row").hidden = !combine.checked;
}

// Een keuze voor de weekplanner: meteen toepassen (ook op de labels in de weekplanner) en bij het profiel bewaren. Weigert de
// server hem, dan komt de vorige stand terug met de melding van de server.
async function appearanceSavePlanner(key, value) {
  const previous = { plannerCombine: state.auth?.plannerCombine !== false, plannerSplitMeals: state.auth?.plannerSplitMeals !== false };
  const changed = () => {
    renderAppearancePlanner();
    if (typeof ahPlanner !== "undefined" && ahPlanner.loaded && typeof renderAhPlanner === "function") renderAhPlanner();
  };
  state.auth = { ...state.auth, [key]: value };
  changed();
  message("#appearance-planner-message", "");
  if (!appearanceSignedIn()) return;
  try {
    const data = await jsonFetch("/api/profile/preferences", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ [key]: value }),
    });
    state.auth = { ...state.auth, plannerCombine: data.plannerCombine !== false, plannerSplitMeals: data.plannerSplitMeals !== false };
  } catch (error) {
    state.auth = { ...state.auth, ...previous };
    message("#appearance-planner-message", error.message);
  }
  changed();
}

function renderAppearance() {
  renderAppearanceStore();
  renderAppearancePlanner();
  // De taal is een keuze van deze browser (static/i18n.js), niet van het profiel; de rondjes volgen wat er nu aan staat.
  document.querySelectorAll('input[name="appearance-lang"]').forEach((input) => {
    input.checked = input.value === I18N.lang();
  });
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
  const cooking = $("#appearance-cooking");
  if (cooking) cooking.checked = look.cookingMode !== false;
  const calories = $("#appearance-calories");
  if (calories) calories.checked = look.showCalories !== false;
}

// ---- De blokken klappen in en uit. De keuze blijft in deze browser (ms-fold-appearance-<naam>); zonder keuze staan ze
// dicht, zodat de pagina kort blijft en je meteen ziet wat er is.
const APPEARANCE_FOLDS = ["store", "display", "theme"];

function appearanceFoldStored(name) {
  try {
    const value = localStorage.getItem(`ms-fold-appearance-${name}`);
    return value === "1" ? true : value === "0" ? false : null;
  } catch {
    return null;
  }
}

function applyAppearanceFold(name, open) {
  const toggle = $(`#appearance-${name}-toggle`);
  const body = $(`#appearance-${name}-body`);
  if (!toggle || !body) return;
  toggle.setAttribute("aria-expanded", String(open));
  toggle.title = t(open ? "sec.collapse" : "sec.expand");
  body.hidden = !open;
}

function applyAppearanceFolds() {
  APPEARANCE_FOLDS.forEach((name) => applyAppearanceFold(name, appearanceFoldStored(name) ?? false));
}

APPEARANCE_FOLDS.forEach((name) => {
  $(`#appearance-${name}-toggle`).addEventListener("click", () => {
    const open = $(`#appearance-${name}-toggle`).getAttribute("aria-expanded") !== "true";
    try {
      localStorage.setItem(`ms-fold-appearance-${name}`, open ? "1" : "0");
    } catch {
      // Geen opslag: het blok blijft zo tot je de pagina herlaadt.
    }
    applyAppearanceFold(name, open);
  });
});

// De kookmodus: uit verbergt bij een recept Start met koken, de voortgang, Scherm aan laten en de vinkjes (recipe_view.js).
$("#appearance-cooking").addEventListener("change", (event) => {
  const previous = { cookingMode: THEME.appearance().cookingMode };
  THEME.apply({ cookingMode: event.target.checked });
  appearanceSave({ cookingMode: event.target.checked }, { previous, target: "#appearance-display-message" });
});

// Calorieën tonen (issue #283): uit verbergt de kcal bij recepten en maaltijden, de voedingswaarden en het filter erop.
$("#appearance-calories").addEventListener("change", (event) => {
  const previous = { showCalories: THEME.appearance().showCalories };
  THEME.apply({ showCalories: event.target.checked });
  appearanceSave({ showCalories: event.target.checked }, { previous, target: "#appearance-display-message" });
});

$("#appearance-store").addEventListener("change", (event) => {
  if (event.target.name === "appearance-store") appearanceSaveStore(event.target.value);
});

$("#appearance-planner-combine").addEventListener("change", (event) => appearanceSavePlanner("plannerCombine", event.target.checked));
$("#appearance-planner-split-meals").addEventListener("change", (event) => appearanceSavePlanner("plannerSplitMeals", event.target.checked));

$("#appearance-theme").addEventListener("change", (event) => {
  if (event.target.name === "appearance-theme") THEME.choose(event.target.value); // het bewaren volgt via ms:appearancechange
});

// Geen data-lang op de rondjes: die zou i18n.js als knop behandelen (aria-pressed). setLang vuurt ms:languagechange, en dan
// tekent renderAppearance de keuze opnieuw.
$("#appearance-lang").addEventListener("change", (event) => {
  if (event.target.name === "appearance-lang") I18N.setLang(event.target.value);
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

// Het thema (ook via het menu onder je naam) bij het profiel bewaren.
window.addEventListener("ms:appearancechange", (event) => {
  renderAppearance();
  if (event.detail?.theme) appearanceSave({ theme: event.detail.theme });
});

// Na elke wissel van thema tonen de rondjes de tint van dat thema (niet tijdens het schuiven met de kleurkiezer).
window.addEventListener("ms:appearancepaint", () => {
  if (!appearancePicking) renderAppearance();
});

window.addEventListener("ms:languagechange", () => {
  renderAppearance();
  applyAppearanceFolds();
});
applyAppearanceFolds();
renderAppearance();
