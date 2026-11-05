// Gedeelde categoriechips (aanbiedingen en bestellingen): een knop per categorie met het kleurbolletje van die categorie.

function categoryLabel(id) {
  return t(`offers.cat.${id}`);
}

function categoryChipLabel(category) {
  return t("offers.chip", { count: I18N.formatNumber(category.count), category: categoryLabel(category.id) });
}

// categories: [{ id, count }] (total is niet nodig: allLabel draagt het getal); active: het gekozen id of "all"; de chip "all" staat altijd vooraan.
function categoryChips({ categories, active, allLabel }) {
  const chips = [{ id: "all", label: allLabel }].concat(categories.map((category) => ({ id: category.id, label: categoryChipLabel(category) })));
  return chips
    .map(
      (chip) =>
        `<button class="offer-chip cat--${escapeHtml(chip.id)}${active === chip.id ? " is-active" : ""}" data-cat="${escapeHtml(chip.id)}" aria-pressed="${active === chip.id}" type="button"><i class="offer-chip__dot" aria-hidden="true"></i>${escapeHtml(chip.label)}</button>`
    )
    .join("");
}
