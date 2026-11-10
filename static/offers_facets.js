// De keuzelijsten in de filterzijbalk van de aanbiedingen: de categorieën en de merken. Elke regel heeft een plus (alleen deze
// tonen), een min (deze verbergen) en het aantal aanbiedingen dat er met de andere filters is. De lijst staat op dat aantal, met
// wat je op plus of min hebt gezet bovenaan. Boven de merken een zoekveld, want een winkel heeft er honderden. De filters zelf
// (offersFilters, offersFilterMatch) en de aantallen (offersBrandCounts, offersCategoryCounts) wonen in offers.js.

// Per lijst: de velden van de filters (tonen en verbergen), waar de lijst staat, en de naam en de klasse van een waarde.
const OFFER_FACETS = {
  brand: { shown: "brands", hidden: "brandsOut", list: "#offers-filter-brands", label: (value) => value, cls: () => "", empty: "offers.filters.no_brands" },
  category: { shown: "categories", hidden: "categoriesOut", list: "#offers-categories", label: (value) => categoryLabel(value), cls: (value) => ` cat--${escapeHtml(value)}`, empty: "" },
};

const offerFacets = { brandSearch: "" };

// "in" (alleen deze), "out" (verbergen) of "" voor een waarde in een lijst.
function offerFacetMode(filters, facet, value) {
  if (filters[OFFER_FACETS[facet].shown].includes(value)) return "in";
  return filters[OFFER_FACETS[facet].hidden].includes(value) ? "out" : "";
}

// Zet een waarde op tonen of verbergen. Dezelfde knop nog een keer zet haar weer uit, de andere knop wisselt.
function offerFacetToggle(filters, facet, value, mode) {
  const { shown, hidden } = OFFER_FACETS[facet];
  const was = offerFacetMode(filters, facet, value);
  filters[shown] = filters[shown].filter((item) => item !== value);
  filters[hidden] = filters[hidden].filter((item) => item !== value);
  if (was !== mode) filters[mode === "out" ? hidden : shown].push(value);
}

// De regels van een lijst: [{ value, label, count, mode }]. Wat op plus of min staat eerst (ook als het deze week niet voorkomt,
// met 0: een filter verdwijnt nooit stil), daarna op aantal en dan op naam. `search` laat alleen de namen door waar elk woord in
// staat, zonder hoofdletters en accenten.
function offerFacetRows(facet, counts, filters, search = "") {
  const config = OFFER_FACETS[facet];
  const rows = counts.map((item) => ({ value: item.brand ?? item.id, count: item.count }));
  [...filters[config.shown], ...filters[config.hidden]].forEach((value) => {
    if (!rows.some((row) => row.value === value)) rows.push({ value, count: 0 });
  });
  const term = search.trim();
  return rows
    .map((row) => ({ ...row, label: config.label(row.value), mode: offerFacetMode(filters, facet, row.value) }))
    .filter((row) => !term || matchesWords(foldText(row.label), term))
    .sort((a, b) => Boolean(b.mode) - Boolean(a.mode) || b.count - a.count || a.label.localeCompare(b.label));
}

function offerFacetRowHtml(facet, row) {
  const step = (mode, icon, key) => {
    const label = escapeHtml(t(key, { name: row.label }));
    return `<button type="button" class="offer-facet__step offer-facet__step--${mode}" data-mode="${mode}" aria-pressed="${row.mode === mode}" aria-label="${label}" title="${label}">${mdiIcon(icon)}</button>`;
  };
  const dot = facet === "category" ? '<i class="offer-chip__dot" aria-hidden="true"></i>' : "";
  return `<li class="offer-facet__row${OFFER_FACETS[facet].cls(row.value)}${row.mode ? ` is-${row.mode}` : ""}" data-facet="${facet}" data-value="${escapeHtml(row.value)}">
      ${step("in", MDI_PLUS, "offers.filters.include")}${step("out", MDI_MINUS, "offers.filters.exclude")}
      <span class="offer-facet__name">${dot}${escapeHtml(row.label)}</span>
      <span class="offer-facet__count">${escapeHtml(I18N.formatNumber(row.count))}</span>
    </li>`;
}

// Tekent een lijst opnieuw. De aantallen gaan over wat het zoekveld en de schakelaars doorlaten (offersBase).
function renderOfferFacet(facet) {
  const config = OFFER_FACETS[facet];
  const list = $(config.list);
  if (!list) return;
  const base = offersBase();
  const counts = facet === "brand" ? offersBrandCounts(base) : offersCategoryCounts(base);
  const all = offerFacetRows(facet, counts, offersFilters());
  const rows = facet === "brand" ? offerFacetRows(facet, counts, offersFilters(), offerFacets.brandSearch) : all;
  list.closest(".offers-filters__section").hidden = !all.length;
  list.innerHTML = rows.length ? rows.map((row) => offerFacetRowHtml(facet, row)).join("") : config.empty ? `<li class="offer-facet__empty">${escapeHtml(t(config.empty))}</li>` : "";
}

// Een klik op plus of min, of op de naam (dat is de plus). Na het opnieuw tekenen staat de focus weer op dezelfde knop: de regel
// verhuist naar boven zodra ze actief is.
$("#offers-filters").addEventListener("click", (event) => {
  const row = event.target.closest("[data-facet]");
  if (!row) return;
  const button = event.target.closest("[data-mode]");
  const mode = button ? button.dataset.mode : "in";
  const { facet, value } = row.dataset;
  offerFacetToggle(offersFilters(), facet, value, mode);
  offersFilterChanged();
  if (!button) return;
  const again = [...$(OFFER_FACETS[facet].list).querySelectorAll("[data-facet]")].find((item) => item.dataset.value === value);
  if (again) again.querySelector(`[data-mode="${mode}"]`).focus();
});

$("#offers-filter-brand-search").addEventListener("input", (event) => {
  offerFacets.brandSearch = event.target.value;
  renderOfferFacet("brand");
});
