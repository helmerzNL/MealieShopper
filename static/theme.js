// Donker en licht thema. De keuze staat in localStorage ("ms-theme"); zonder keuze volgt de pagina het apparaat.
// De eerste waarde zet een klein script in de kop van de pagina (templates/index.html), zodat er niets flitst.
(function () {
  "use strict";

  const STORAGE_KEY = "ms-theme";
  const THEME_COLORS = { light: "#176B45", dark: "#0F1613" };
  const query = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;

  function stored() {
    try {
      const value = window.localStorage.getItem(STORAGE_KEY);
      return value === "dark" || value === "light" ? value : null;
    } catch {
      return null;
    }
  }

  function current() {
    return document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
  }

  function apply(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    document.querySelectorAll('meta[name="theme-color"]').forEach((meta) => meta.setAttribute("content", THEME_COLORS[theme]));
    document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
      button.setAttribute("aria-pressed", String(theme === "dark"));
    });
  }

  function choose(theme) {
    try {
      window.localStorage.setItem(STORAGE_KEY, theme);
    } catch {
      /* opslag geblokkeerd: de keuze geldt dan voor deze pagina */
    }
    apply(theme);
  }

  document.addEventListener("click", (event) => {
    if (event.target.closest("[data-theme-toggle]")) choose(current() === "dark" ? "light" : "dark");
  });

  // Zonder eigen keuze volgt de pagina het apparaat ook als dat tijdens het gebruik wisselt.
  if (query) {
    query.addEventListener("change", (event) => {
      if (!stored()) apply(event.matches ? "dark" : "light");
    });
  }

  window.THEME = { current, set: choose };
  document.addEventListener("DOMContentLoaded", () => apply(current()));
})();
