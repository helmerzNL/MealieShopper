// De gedeelde keuzelijst: een lijst waarden (merken, categorieën, gangen, producten) met per regel een plus (alleen deze tonen),
// een min (deze verbergen) en het aantal. De lijst staat op dat aantal, met wat op plus of min staat bovenaan. Gebruikt door de
// aanbiedingen (offers_facets.js), een bestelling en de aankopenlijst (order_facets.js), de prijsgeschiedenis en de recepten bij de
// aanbiedingen. Een keuze is `{ shown: [], hidden: [] }`: met iets in `shown` alleen die waarden (een van de gekozen), en een
// waarde in `hidden` valt altijd weg.

function facetSelection() {
  return { shown: [], hidden: [] };
}

function facetActive(selection) {
  return selection.shown.length + selection.hidden.length;
}

// "in" (alleen deze), "out" (verbergen) of "" voor een waarde.
function facetMode(selection, value) {
  if (selection.shown.includes(value)) return "in";
  return selection.hidden.includes(value) ? "out" : "";
}

// De keuze na een klik op plus ("in") of min ("out"). Dezelfde knop nog een keer zet de waarde weer uit, de andere knop wisselt.
function facetToggled(selection, value, mode) {
  const was = facetMode(selection, value);
  const next = { shown: selection.shown.filter((item) => item !== value), hidden: selection.hidden.filter((item) => item !== value) };
  if (was !== mode) next[mode === "out" ? "hidden" : "shown"].push(value);
  return next;
}

// Laat de keuze deze waarde door?
function facetPasses(selection, value) {
  if (selection.hidden.includes(value)) return false;
  return !selection.shown.length || selection.shown.includes(value);
}

// Telt waarden: [{ value, count }], op aantal en dan op waarde. Een lege waarde telt niet mee.
function facetCounts(values) {
  const counts = new Map();
  for (const value of values) {
    if (value) counts.set(value, (counts.get(value) || 0) + 1);
  }
  return [...counts.entries()].map(([value, count]) => ({ value, count })).sort((a, b) => b.count - a.count || a.value.localeCompare(b.value));
}

// De regels van een lijst: [{ value, label, count, mode }]. Wat op plus of min staat eerst (ook als het nu niet voorkomt, met 0:
// een filter verdwijnt nooit stil), daarna op aantal en dan op naam. `search` laat alleen de namen door waar elk woord in staat,
// zonder hoofdletters en accenten.
function facetRows(counts, selection, { label = (value) => value, search = "" } = {}) {
  const rows = counts.map((item) => ({ value: item.value, count: item.count }));
  [...selection.shown, ...selection.hidden].forEach((value) => {
    if (!rows.some((row) => row.value === value)) rows.push({ value, count: 0 });
  });
  const term = search.trim();
  return rows
    .map((row) => ({ ...row, label: label(row.value), mode: facetMode(selection, row.value) }))
    .filter((row) => !term || matchesWords(foldText(row.label), term))
    .sort((a, b) => Boolean(b.mode) - Boolean(a.mode) || b.count - a.count || a.label.localeCompare(b.label));
}

// Eén regel. `cls` is een extra klasse per waarde (de kleur van een categorie), `dot` zet het kleurbolletje voor de naam.
// `include` en `exclude` zijn de vertaalsleutels van de knoppen: bij de recepten moet een gekozen tag erbij (issue #322).
function facetRowHtml(name, row, { cls = () => "", dot = false, include = "offers.filters.include", exclude = "offers.filters.exclude" } = {}) {
  const step = (mode, icon, key) => {
    const label = escapeHtml(t(key, { name: row.label }));
    return `<button type="button" class="offer-facet__step offer-facet__step--${mode}" data-mode="${mode}" aria-pressed="${row.mode === mode}" aria-label="${label}" title="${label}">${mdiIcon(icon)}</button>`;
  };
  const bullet = dot ? '<i class="offer-chip__dot" aria-hidden="true"></i>' : "";
  return `<li class="offer-facet__row${cls(row.value)}${row.mode ? ` is-${row.mode}` : ""}" data-facet="${escapeHtml(name)}" data-value="${escapeHtml(row.value)}">
      ${step("in", MDI_PLUS, include)}${step("out", MDI_MINUS, exclude)}
      <span class="offer-facet__name" title="${escapeHtml(row.label)}">${bullet}${escapeHtml(row.label)}</span>
      <span class="offer-facet__count">${escapeHtml(I18N.formatNumber(row.count))}</span>
    </li>`;
}

// De regels van een lijst als html, of de melding `empty` (een vertaalsleutel) als er geen regel is.
function facetListHtml(name, rows, options = {}) {
  if (rows.length) return rows.map((row) => facetRowHtml(name, row, options)).join("");
  return options.empty ? `<li class="offer-facet__empty">${escapeHtml(t(options.empty))}</li>` : "";
}

// Wat een klik in een lijst betekent: { name, value, mode, button }, of null naast een regel. Een klik op de naam is de plus.
function facetClick(event) {
  const row = event.target.closest("[data-facet]");
  if (!row) return null;
  const button = event.target.closest("[data-mode]");
  return { name: row.dataset.facet, value: row.dataset.value, mode: button ? button.dataset.mode : "in", button };
}

// Na het opnieuw tekenen de focus terug op dezelfde knop: de regel verhuist naar boven zodra ze actief is.
function facetRefocus(list, click) {
  if (!list || !click.button) return;
  const again = [...list.querySelectorAll("[data-facet]")].find((item) => item.dataset.value === click.value);
  if (again) again.querySelector(`[data-mode="${click.mode}"]`).focus();
}

// De chips van een keuze voor de rij "actieve filters": [{ key, label }], met `prefix` in de sleutel ("brand:Unox",
// "brandOut:Unox") zodat de pagina weet welke lijst en welke kant.
function facetChips(selection, prefix, label = (value) => value) {
  return [
    ...selection.shown.map((value) => ({ key: `${prefix}:${value}`, label: label(value) })),
    ...selection.hidden.map((value) => ({ key: `${prefix}Out:${value}`, label: t("offers.filters.excluded", { name: label(value) }) })),
  ];
}

// Het uitklapvak met de lijsten op een pagina zonder zijbalk (`<details class="facet-panel" data-facet-panel="naam">`): achter
// "Filters" het aantal actieve keuzes, en "Alles wissen" alleen als er iets te wissen valt.
function facetPanelCount(panel, count) {
  if (!panel) return;
  panel.querySelector(".facet-panel__count").textContent = count ? ` (${I18N.formatNumber(count)})` : "";
  const clear = panel.querySelector("[data-facet-clear]");
  if (clear) clear.hidden = !count;
}

// Open of dicht onthoudt de browser per vak. Zonder keuze staat het open: de filters horen in beeld. Een vak met
// `data-facet-closed` staat zonder keuze dicht: bij Recepten zoeken komt eerst het zoekveld (issue #322).
const FACET_PANEL_KEY = "ms-facet-panel";

function facetPanelsInit() {
  document.querySelectorAll("[data-facet-panel]").forEach((panel) => {
    const key = `${FACET_PANEL_KEY}:${panel.dataset.facetPanel}`;
    let saved = null;
    try {
      saved = localStorage.getItem(key);
    } catch {
      // Dan staat het vak open.
    }
    panel.open = saved === null ? !panel.hasAttribute("data-facet-closed") : saved !== "0";
    panel.addEventListener("toggle", () => {
      try {
        localStorage.setItem(key, panel.open ? "1" : "0");
      } catch {
        // De keuze geldt dan tot je de pagina herlaadt.
      }
    });
  });
}

facetPanelsInit();
