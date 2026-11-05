// Gedeelde categoriechips (aanbiedingen en bestellingen): een knop per categorie met het kleurbolletje van die categorie.

// De volgorde van de categorieën, zoals offer_categories.CATEGORIES op de server (de chips, de secties van de bestelpagina).
const CATEGORY_ORDER = ["groente_fruit", "vlees", "vis", "kaas_vleeswaren", "zuivel_eieren", "brood_bakkerij", "maaltijden", "sauzen_conserven", "ontbijt_beleg", "snacks_snoep", "frisdrank", "koffie_thee", "bier_wijn", "diepvries", "huishouden", "drogisterie", "baby", "huisdier", "overig"];

function categoryLabel(id) {
  return t(`offers.cat.${id}`);
}

function categoryChipLabel(category) {
  return t("offers.chip", { count: I18N.formatNumber(category.count), category: categoryLabel(category.id) });
}

// Eén chip: een knop die een categorie kiest, of met `tag` "span" alleen het label (in een hovervenster, waar niets te klikken valt).
function categoryChip({ id, label }, { active = false, tag = "button" } = {}) {
  const cls = `offer-chip cat--${escapeHtml(id)}${active ? " is-active" : ""}`;
  const dot = `<i class="offer-chip__dot" aria-hidden="true"></i>${escapeHtml(label)}`;
  if (tag === "span") return `<span class="${cls}">${dot}</span>`;
  return `<button class="${cls}" data-cat="${escapeHtml(id)}" aria-pressed="${active}" type="button">${dot}</button>`;
}

// categories: [{ id, count }] (total is niet nodig: allLabel draagt het getal); active: het gekozen id of "all"; de chip "all" staat altijd vooraan.
function categoryChips({ categories, active, allLabel }) {
  const chips = [{ id: "all", label: allLabel }].concat(categories.map((category) => ({ id: category.id, label: categoryChipLabel(category) })));
  return chips.map((chip) => categoryChip(chip, { active: active === chip.id })).join("");
}
