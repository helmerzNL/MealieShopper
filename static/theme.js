// De weergave: licht of donker, de achtergrond van het donkere thema en de accentkleur.
//
// Thema. De keuze staat in localStorage ("ms-theme": light of dark); zonder keuze volgt de pagina het apparaat. Een klein
// script in de kop van de pagina (templates/index.html) zet de eerste waarde, zodat er niets flitst.
//
// Per gebruiker. Instellingen, Weergave (appearance.js) bewaart het thema, de donkere achtergrond (groen of donkergrijs) en
// de accentkleur bij het profiel op de server; na het inloggen zet apply() die keuze. Een kopie staat in localStorage
// ("ms-appearance"), zodat het script in de kop ook de kleur meteen goed zet.
//
// De accentkleur. Eén kleur kleurt de knoppen, links, de actieve navigatie, de focusring en de gloed bovenaan (styles.css
// leidt de rest af met color-mix). Een kleur die iemand kiest is niet vanzelf leesbaar, dus maakt variants() er twee tinten
// van: een voor het lichte thema (donkerder tot hij 4,5 : 1 haalt op wit, de achtergrond en het selectievlak) en een voor het
// donkere (lichter tot hij dat haalt op de vlakken van beide donkere achtergronden). Statussen (fout, waarschuwing, gelukt)
// volgen de accentkleur nooit. Zonder eigen keuze blijft het basilicumgroen van de huisstijl, met de waarden uit styles.css.
(function () {
  "use strict";

  const THEME_KEY = "ms-theme";
  const LOOK_KEY = "ms-appearance";
  const HEX = /^#[0-9a-f]{6}$/i;
  const BRAND = { light: "#176b45", dark: "#4cc38a" };
  // De balk van de browser (en de statusbalk op Android): in het lichte thema de accentkleur, in het donkere de achtergrond.
  const DARK_BG = { green: "#0f1613", grey: "#121314" };
  const DARK_BACKGROUNDS = ["green", "grey"];

  // De keuzes in Instellingen, Weergave: [waarde, naam in de vertaling]. Leeg is de huisstijl. Elke kleur haalt in het lichte
  // thema zelf al het contrast, dus het rondje toont wat je krijgt; tests/test_appearance.py controleert dat.
  const ACCENTS = [
    ["", "appearance.accent.basil"],
    ["#0b62c4", "appearance.accent.blue"],
    ["#4f46e5", "appearance.accent.indigo"],
    ["#7c3aed", "appearance.accent.violet"],
    ["#a21caf", "appearance.accent.purple"],
    ["#c2185b", "appearance.accent.pink"],
    ["#c0400c", "appearance.accent.orange"],
    ["#0f766e", "appearance.accent.teal"],
    ["#9b1c3a", "appearance.accent.bordeaux"],
    ["#475569", "appearance.accent.graphite"],
  ];

  // De vlakken waar de accentkleur als tekst op staat (styles.css): wit, --bg en --surface-muted; donker voor groen en grijs.
  const LIGHT_SURFACES = ["#ffffff", "#f5f7f6", "#eef3f0"];
  const DARK_SURFACES = ["#0f1613", "#17211c", "#1e2b24", "#121314", "#1b1c1e", "#232527"];
  const TARGET = 4.6; // net boven 4,5 : 1, zodat afronden in de browser het niet onder de grens brengt
  // Het glas van de onderste menubalk op zijn slechtst (styles.css, --tabbar-glass): wit glas boven een zwarte foto, en donker
  // glas (groen en grijs) boven een witte. Het label van de actieve tab moet daar ook leesbaar zijn, met de tint erover.
  const LIGHT_GLASS = "#cccccc";
  const DARK_GLASS = ["#454d49", "#494a4b"];

  const query = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;

  // ---- kleur rekenen
  function rgb(hex) {
    return [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
  }

  function toHex(channels) {
    return `#${channels.map((c) => Math.round(Math.min(255, Math.max(0, c))).toString(16).padStart(2, "0")).join("")}`;
  }

  function luminance(hex) {
    const lin = rgb(hex).map((c) => {
      const v = c / 255;
      return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2];
  }

  function contrast(a, b) {
    const [high, low] = [luminance(a), luminance(b)].sort((x, y) => y - x);
    return (high + 0.05) / (low + 0.05);
  }

  // Zoals color-mix(in srgb, a share%, b) in styles.css.
  function mix(a, b, share) {
    const [x, y] = [rgb(a), rgb(b)];
    return toHex(x.map((c, i) => c * share + y[i] * (1 - share)));
  }

  function toHsl(hex) {
    const [r, g, b] = rgb(hex).map((c) => c / 255);
    const max = Math.max(r, g, b);
    const min = Math.min(r, g, b);
    const l = (max + min) / 2;
    if (max === min) return [0, 0, l];
    const d = max - min;
    const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    const h = max === r ? (g - b) / d + (g < b ? 6 : 0) : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
    return [h / 6, s, l];
  }

  function fromHsl([h, s, l]) {
    if (!s) return toHex([l * 255, l * 255, l * 255]);
    const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
    const p = 2 * l - q;
    const channel = (t) => {
      const u = t < 0 ? t + 1 : t > 1 ? t - 1 : t;
      if (u < 1 / 6) return p + (q - p) * 6 * u;
      if (u < 1 / 2) return q;
      if (u < 2 / 3) return p + (q - p) * (2 / 3 - u) * 6;
      return p;
    };
    return toHex([channel(h + 1 / 3), channel(h), channel(h - 1 / 3)].map((c) => c * 255));
  }

  function withLightness(hex, lightness) {
    const [h, s] = toHsl(hex);
    return fromHsl([h, s, Math.min(1, Math.max(0, lightness))]);
  }

  // Het slechtste contrast van een kleur op de vlakken en op zijn eigen selectievlak (zoals --selection in styles.css).
  function worst(color, surfaces, selection) {
    return Math.min(...surfaces.map((surface) => contrast(color, surface)), contrast(color, selection(color)));
  }

  // De tint houden en alleen de lichtheid verschuiven tot het contrast er is: donkerder (step < 0) of lichter (step > 0).
  function readable(hex, surfaces, selection, step) {
    const [, , start] = toHsl(hex);
    let color = hex;
    for (let lightness = start; worst(color, surfaces, selection) < TARGET; lightness += step) {
      if (lightness < 0 || lightness > 1) return step < 0 ? "#000000" : "#ffffff";
      color = withLightness(hex, lightness);
    }
    return color;
  }

  const ACCENT_VARS = ["light", "dark"].flatMap((theme) => ["", "-strong", "-on", "-ink"].map((part) => `--accent-${theme}${part}`));
  const variantCache = new Map();

  // De tinten voor beide thema's, of null voor de huisstijl (dan gelden de waarden uit styles.css).
  function variants(accent) {
    const hex = String(accent || "").toLowerCase();
    if (!HEX.test(hex) || hex === BRAND.light) return null; // het merkgroen zelf: de afgewogen waarden uit styles.css
    if (variantCache.has(hex)) return variantCache.get(hex);
    const light = readable(hex, LIGHT_SURFACES, (c) => mix(c, "#ffffff", 0.09), -0.005);
    const dark = readable(hex, DARK_SURFACES, (c) => mix(c, "#0f1613", 0.2), 0.005);
    const [, , lightL] = toHsl(light);
    const [h, s, darkL] = toHsl(dark);
    const darkOn = fromHsl([h, Math.min(s, 0.5), 0.08]);
    const lightStrong = withLightness(light, lightL * 0.76);
    const darkStrong = withLightness(dark, Math.min(0.9, darkL + 0.13));
    const lightTint = mix(light, LIGHT_GLASS, 0.14);
    const darkTints = DARK_GLASS.map((glass) => mix(dark, glass, 0.18));
    const result = {
      "--accent-light": light,
      "--accent-light-strong": lightStrong,
      "--accent-light-on": contrast("#ffffff", light) >= 4.5 ? "#ffffff" : "#0b0f0d",
      "--accent-light-ink": readable(lightStrong, [lightTint], () => lightTint, -0.005),
      "--accent-dark": dark,
      "--accent-dark-strong": darkStrong,
      "--accent-dark-on": contrast(darkOn, dark) >= 4.5 ? darkOn : "#000000",
      "--accent-dark-ink": readable(darkStrong, darkTints, () => darkTints[0], 0.005),
    };
    variantCache.set(hex, result);
    return result;
  }

  // ---- de keuze
  const look = { theme: null, darkBackground: "green", accent: "" };

  function storedTheme() {
    try {
      const value = window.localStorage.getItem(THEME_KEY);
      return value === "dark" || value === "light" ? value : null;
    } catch {
      return null;
    }
  }

  function storedLook() {
    try {
      const value = JSON.parse(window.localStorage.getItem(LOOK_KEY) || "null");
      return value && typeof value === "object" ? value : {};
    } catch {
      return {};
    }
  }

  function current() {
    return document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
  }

  // Wat er gekozen is: system (het apparaat), light of dark.
  function choice() {
    return storedTheme() || "system";
  }

  function remember() {
    try {
      const vars = variants(look.accent);
      window.localStorage.setItem(LOOK_KEY, JSON.stringify({ darkBackground: look.darkBackground, accent: look.accent, vars: vars || {} }));
    } catch {
      /* opslag geblokkeerd: de keuze geldt dan voor deze pagina */
    }
  }

  function paint() {
    const root = document.documentElement;
    const theme = current();
    if (look.darkBackground === "grey") root.setAttribute("data-dark-bg", "grey");
    else root.removeAttribute("data-dark-bg");
    const vars = variants(look.accent);
    ACCENT_VARS.forEach((name) => {
      if (vars) root.style.setProperty(name, vars[name]);
      else root.style.removeProperty(name);
    });
    root.toggleAttribute("data-accent", Boolean(vars));
    const bar = theme === "dark" ? DARK_BG[look.darkBackground] || DARK_BG.green : vars ? vars["--accent-light"] : BRAND.light;
    document.querySelectorAll('meta[name="theme-color"]').forEach((meta) => meta.setAttribute("content", bar));
    document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
      button.setAttribute("aria-pressed", String(theme === "dark"));
    });
    window.dispatchEvent(new CustomEvent("ms:appearancepaint", { detail: appearance() }));
  }

  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    paint();
  }

  function systemTheme() {
    return query && query.matches ? "dark" : "light";
  }

  // system, light of dark kiezen en in deze browser bewaren. announce: laat appearance.js het bij het profiel bewaren.
  function choose(theme, { announce = true } = {}) {
    const wanted = theme === "light" || theme === "dark" ? theme : "system";
    try {
      if (wanted === "system") window.localStorage.removeItem(THEME_KEY);
      else window.localStorage.setItem(THEME_KEY, wanted);
    } catch {
      /* opslag geblokkeerd: de keuze geldt dan voor deze pagina */
    }
    look.theme = wanted;
    applyTheme(wanted === "system" ? systemTheme() : wanted);
    if (announce) window.dispatchEvent(new CustomEvent("ms:appearancechange", { detail: { theme: wanted } }));
  }

  // De keuze van het profiel (of een deel ervan) toepassen. Een thema van null is "nog niet gekozen": dan blijft wat deze
  // browser had. Waarden die niet kloppen tellen als de standaard, zodat een slechte waarde de pagina nooit breekt.
  function apply(prefs = {}) {
    if ("darkBackground" in prefs) look.darkBackground = DARK_BACKGROUNDS.includes(prefs.darkBackground) ? prefs.darkBackground : "green";
    if ("accent" in prefs) look.accent = variants(prefs.accent) ? prefs.accent.toLowerCase() : "";
    remember();
    if (prefs.theme === "system" || prefs.theme === "light" || prefs.theme === "dark") choose(prefs.theme, { announce: false });
    else paint();
  }

  function appearance() {
    return { theme: choice(), darkBackground: look.darkBackground, accent: look.accent };
  }

  document.addEventListener("click", (event) => {
    if (event.target.closest("[data-theme-toggle]")) choose(current() === "dark" ? "light" : "dark");
  });

  // Zonder eigen keuze volgt de pagina het apparaat ook als dat tijdens het gebruik wisselt.
  if (query) {
    query.addEventListener("change", (event) => {
      if (!storedTheme()) applyTheme(event.matches ? "dark" : "light");
    });
  }

  const cached = storedLook();
  look.darkBackground = DARK_BACKGROUNDS.includes(cached.darkBackground) ? cached.darkBackground : "green";
  look.accent = variants(cached.accent) ? cached.accent.toLowerCase() : "";
  look.theme = choice();

  window.THEME = { current, set: choose, choose, choice, apply, appearance, variants, contrast, mix, ACCENTS, BRAND };
  document.addEventListener("DOMContentLoaded", () => paint());
})();
