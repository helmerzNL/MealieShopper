// De keuzelijsten in de filterzijbalk van de aanbiedingen: de categorieën en de merken, met de gedeelde lijst uit facets.js.
// Boven de merken een zoekveld, want een winkel heeft er honderden. De filters zelf (offersFilters, offersFilterMatch) en de
// aantallen (offersBrandCounts, offersCategoryCounts) wonen in offers.js.

// Per lijst: de velden van de filters (tonen en verbergen), waar de lijst staat, en de naam en de klasse van een waarde.
const OFFER_FACETS = {
  brand: { shown: "brands", hidden: "brandsOut", list: "#offers-filter-brands", label: (value) => value, cls: () => "", dot: false, empty: "offers.filters.no_brands" },
  category: { shown: "categories", hidden: "categoriesOut", list: "#offers-categories", label: (value) => categoryLabel(value), cls: (value) => ` cat--${escapeHtml(value)}`, dot: true, empty: "" },
};

const offerFacets = { brandSearch: "" };

// De keuze van een lijst uit de filters van de aanbiedingen, in de vorm van facets.js.
function offerFacetSelection(filters, facet) {
  return { shown: filters[OFFER_FACETS[facet].shown], hidden: filters[OFFER_FACETS[facet].hidden] };
}

// Zet een waarde op tonen of verbergen in de filters. Dezelfde knop nog een keer zet haar weer uit, de andere knop wisselt.
function offerFacetToggle(filters, facet, value, mode) {
  const next = facetToggled(offerFacetSelection(filters, facet), value, mode);
  filters[OFFER_FACETS[facet].shown] = next.shown;
  filters[OFFER_FACETS[facet].hidden] = next.hidden;
}

// De regels van een lijst uit de aantallen van offers.js ([{ brand | id, count }]).
function offerFacetRows(facet, counts, filters, search = "") {
  const values = counts.map((item) => ({ value: item.brand ?? item.id, count: item.count }));
  return facetRows(values, offerFacetSelection(filters, facet), { label: OFFER_FACETS[facet].label, search });
}

function offerFacetRowHtml(facet, row) {
  return facetRowHtml(facet, row, OFFER_FACETS[facet]);
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
  list.innerHTML = facetListHtml(facet, rows, config);
}

$("#offers-filters").addEventListener("click", (event) => {
  const click = facetClick(event);
  if (!click) return;
  offerFacetToggle(offersFilters(), click.name, click.value, click.mode);
  offersFilterChanged();
  facetRefocus($(OFFER_FACETS[click.name].list), click);
});

$("#offers-filter-brand-search").addEventListener("input", (event) => {
  offerFacets.brandSearch = event.target.value;
  renderOfferFacet("brand");
});
