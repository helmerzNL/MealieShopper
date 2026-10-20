// Vertalingen (nl en en). De teksten staan in i18n/nl.js en i18n/en.js; dit bestand regelt de taalkeuze.
//
//   t("sleutel", { naam })      gewone tekst; {naam} wordt vervangen. Voor textContent of message().
//   th("sleutel", { naam })     tekst met opmaak uit het woordenboek; de waarden worden ge-escaped. Voor innerHTML.
//   tn("sleutel", aantal)       meervoud: zoekt "sleutel.one" (bij 1) of "sleutel.other", met {count}.
//
// In de HTML staat de Nederlandse tekst gewoon in de pagina; data-i18n="sleutel" (tekst),
// data-i18n-html="sleutel" (met opmaak) en data-i18n-attr="placeholder:sleutel;aria-label:sleutel"
// laten hem bij een taalwissel vervangen.
(function () {
  "use strict";

  const SUPPORTED = ["nl", "en"];
  const LOCALES = { nl: "nl-NL", en: "en-GB" };
  const STORAGE_KEY = "ms-lang";
  const COOKIE_NAME = "ms_lang"; // ook de server leest hem, zodat meldingen in dezelfde taal komen
  const messages = { nl: {}, en: {} };
  const warned = new Set();

  function stored() {
    try {
      return window.localStorage.getItem(STORAGE_KEY);
    } catch {
      return null;
    }
  }

  function detect() {
    const saved = stored();
    if (SUPPORTED.includes(saved)) return saved;
    for (const candidate of navigator.languages || [navigator.language]) {
      const short = String(candidate || "").slice(0, 2).toLowerCase();
      if (SUPPORTED.includes(short)) return short;
    }
    return "nl";
  }

  let current = detect();

  function escapeValue(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function lookup(key) {
    const text = messages[current][key] ?? messages.nl[key];
    if (text === undefined && !warned.has(key)) {
      warned.add(key);
      console.warn(`i18n: ontbrekende tekst "${key}"`);
    }
    return text ?? key;
  }

  function fill(template, params, escape) {
    return template.replace(/\{(\w+)\}/g, (match, name) =>
      name in params ? (escape ? escapeValue(params[name]) : String(params[name])) : match
    );
  }

  function t(key, params = {}) {
    return fill(lookup(key), params, false);
  }

  function th(key, params = {}) {
    return fill(lookup(key), params, true);
  }

  function formatNumber(value) {
    return Number(value).toLocaleString(LOCALES[current]);
  }

  function tn(key, count, params = {}) {
    return t(`${key}.${Number(count) === 1 ? "one" : "other"}`, { count: formatNumber(count), ...params });
  }

  function thn(key, count, params = {}) {
    return th(`${key}.${Number(count) === 1 ? "one" : "other"}`, { count: formatNumber(count), ...params });
  }

  function apply(root = document) {
    root.querySelectorAll("[data-i18n]").forEach((element) => {
      element.textContent = t(element.dataset.i18n);
    });
    root.querySelectorAll("[data-i18n-html]").forEach((element) => {
      element.innerHTML = lookup(element.dataset.i18nHtml);
    });
    root.querySelectorAll("[data-i18n-attr]").forEach((element) => {
      for (const pair of element.dataset.i18nAttr.split(";")) {
        const [attribute, key] = pair.split(":").map((part) => part.trim());
        if (attribute && key) element.setAttribute(attribute, t(key));
      }
    });
  }

  function remember(lang) {
    try {
      window.localStorage.setItem(STORAGE_KEY, lang);
    } catch {
      /* opslag geblokkeerd: de taal geldt dan voor deze pagina */
    }
    const secure = window.location.protocol === "https:" ? "; Secure" : "";
    document.cookie = `${COOKIE_NAME}=${lang}; Path=/; Max-Age=31536000; SameSite=Lax${secure}`;
  }

  function markSwitches() {
    document.querySelectorAll("[data-lang]").forEach((button) => {
      const active = button.dataset.lang === current;
      button.setAttribute("aria-pressed", String(active));
      button.classList.toggle("is-active", active);
    });
  }

  function setLang(next, { announce = true } = {}) {
    if (!SUPPORTED.includes(next)) return;
    current = next;
    remember(next);
    document.documentElement.lang = next;
    apply();
    markSwitches();
    if (announce) window.dispatchEvent(new CustomEvent("ms:languagechange", { detail: { lang: next } }));
  }

  window.I18N = {
    add(lang, entries) {
      Object.assign(messages[lang], entries);
    },
    lang: () => current,
    locale: () => LOCALES[current],
    supported: SUPPORTED,
    messages,
    apply,
    setLang,
    formatNumber,
  };
  window.t = t;
  window.th = th;
  window.tn = tn;
  window.thn = thn;

  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-lang]");
    if (button) setLang(button.dataset.lang);
  });

  // Alle scripts (ook de woordenboeken) zijn dan geladen.
  document.addEventListener("DOMContentLoaded", () => {
    setLang(current, { announce: false });
  });
})();
