// De weergaven van de aankopen (Winkels > Bestellingen): dezelfde gefilterde lijst als Tabel (orders.js), Kaarten of Overzicht,
// met sorteren en weergaveopties. De keuze staat in purchaseView en in localStorage, zodat terug uit een bestelling en een
// volgende keer dezelfde weergave geven. Laadt na orders.js en gebruikt daar purchaseRows(), renderOrders() en de rijvelden van
// (kind, label, date, open, status, total, items, images, names, savings, bonusCount, payment, href, attr).

const PURCHASE_VIEWS = ["table", "cards", "overview"];
const PURCHASE_SORT_KEYS = ["date", "amount", "items"];
const PURCHASE_SIZES = ["small", "medium", "large"];
const PURCHASE_OPTIONS = ["status", "savings", "names"];

const purchaseView = { view: "table", sort: { key: "date", dir: "desc" }, options: { size: "medium", status: true, savings: true, names: true } };

// Wat in localStorage staat kan van een oudere versie zijn of met de hand aangepast: alleen bekende waarden tellen.
function purchaseViewLoad() {
  try {
    const view = localStorage.getItem("ms-purchases-view");
    if (PURCHASE_VIEWS.includes(view)) purchaseView.view = view;
    const sort = JSON.parse(localStorage.getItem("ms-purchases-sort") || "null");
    if (sort && PURCHASE_SORT_KEYS.includes(sort.key) && ["asc", "desc"].includes(sort.dir)) purchaseView.sort = { key: sort.key, dir: sort.dir };
    const options = JSON.parse(localStorage.getItem("ms-purchases-options") || "null");
    if (options && typeof options === "object") {
      if (PURCHASE_SIZES.includes(options.size)) purchaseView.options.size = options.size;
      for (const key of PURCHASE_OPTIONS) if (typeof options[key] === "boolean") purchaseView.options[key] = options[key];
    }
  } catch {
    // een browser zonder opslag (privévenster): de standaardwaarden
  }
}

function purchaseViewSave() {
  try {
    localStorage.setItem("ms-purchases-view", purchaseView.view);
    localStorage.setItem("ms-purchases-sort", JSON.stringify(purchaseView.sort));
    localStorage.setItem("ms-purchases-options", JSON.stringify(purchaseView.options));
  } catch {
    // niet erg: de keuze geldt tot je de pagina verlaat
  }
}

// Sorteren: datum (open bestellingen bovenaan bij aflopend, hun bezorgdag ligt in de toekomst), bedrag of aantal producten.
// Een aankoop zonder waarde (producten nog niet opgehaald) staat onderaan, in beide richtingen; gelijke waarden op datum aflopend.
function purchaseSortRows(rows) {
  const { key, dir } = purchaseView.sort;
  const sign = dir === "asc" ? 1 : -1;
  const value = (row) => (key === "amount" ? row.total : key === "items" ? row.items : row.date || null);
  return [...rows].sort((a, b) => {
    if (key === "date" && dir === "desc" && a.open !== b.open) return Number(b.open) - Number(a.open);
    const va = value(a);
    const vb = value(b);
    if (va === null || va === undefined) return vb === null || vb === undefined ? String(b.date).localeCompare(String(a.date)) : 1;
    if (vb === null || vb === undefined) return -1;
    const order = typeof va === "number" ? va - vb : String(va).localeCompare(String(vb));
    return order ? order * sign : String(b.date).localeCompare(String(a.date));
  });
}

// De kleurindicator per soort: een bolletje (tabel, filters) of een streep (kaart, overzicht). Nooit alleen kleur: de tekst
// van de soort staat er altijd naast.
function purchaseKindMarkup(kind, shape) {
  return `<span class="purchase-kind purchase-kind--${shape} purchase-kind--${kind}" aria-hidden="true"></span>`;
}

// Tekent de gekozen weergave uit de gesorteerde, gefilterde rijen; de andere twee vakken gaan dicht.
function renderPurchaseView(visible, hits) {
  const panes = { table: $("#ah-purchase-table-wrap"), cards: $("#ah-purchase-cards"), overview: $("#ah-purchase-overview") };
  for (const [name, pane] of Object.entries(panes)) if (pane) pane.hidden = name !== purchaseView.view;
  if (purchaseView.view === "cards" && typeof renderPurchaseCards === "function") renderPurchaseCards(visible, hits);
  else if (purchaseView.view === "overview" && typeof renderPurchaseOverview === "function") renderPurchaseOverview(visible, hits);
  else renderPurchaseTable(visible, hits);
  purchaseViewSyncBar();
}

// De balk laat zien wat er gekozen is: de actieve weergave, de sortering (ook in de tabelkoppen) en de opties.
function purchaseViewSyncBar() {
  for (const button of document.querySelectorAll("#purchase-view-switch [data-view]")) {
    const active = button.dataset.view === purchaseView.view;
    button.setAttribute("aria-pressed", String(active));
    button.classList.toggle("is-active", active);
  }
  const { key, dir } = purchaseView.sort;
  $("#purchase-sort-current").textContent = `${t(`orders.sort.${key}`)} ${dir === "asc" ? "↑" : "↓"}`;
  for (const item of document.querySelectorAll("#purchase-sort-list [data-sort-key]")) item.classList.toggle("is-active", item.dataset.sortKey === key);
  for (const item of document.querySelectorAll("#purchase-sort-list [data-sort-dir]")) item.classList.toggle("is-active", item.dataset.sortDir === dir);
  for (const button of document.querySelectorAll(".purchase-table__sort")) {
    const th = button.closest("th");
    if (button.dataset.sort === key) th.setAttribute("aria-sort", dir === "asc" ? "ascending" : "descending");
    else th.removeAttribute("aria-sort");
  }
  for (const item of document.querySelectorAll("#purchase-options-list [data-size]")) item.classList.toggle("is-active", item.dataset.size === purchaseView.options.size);
  for (const input of document.querySelectorAll("#purchase-options-list [data-option]")) input.checked = Boolean(purchaseView.options[input.dataset.option]);
}

function purchaseViewApply() {
  purchaseViewSave();
  if (ordersState.loaded) renderOrders();
  else purchaseViewSyncBar();
}

// Een klik op een tabelkop: dezelfde sleutel wisselt de richting, een andere sleutel wordt aflopend (zoals het menu).
function purchaseSortBy(key, dir = null) {
  if (dir) purchaseView.sort = { key: purchaseView.sort.key, dir };
  else if (purchaseView.sort.key === key) purchaseView.sort = { key, dir: purchaseView.sort.dir === "asc" ? "desc" : "asc" };
  else purchaseView.sort = { key, dir: "desc" };
  purchaseViewApply();
}

purchaseViewLoad();
$("#purchase-view-switch").addEventListener("click", (event) => {
  const button = event.target.closest("[data-view]");
  if (!button) return;
  purchaseView.view = button.dataset.view;
  purchaseViewApply();
});
$("#purchase-sort-list").addEventListener("click", (event) => {
  const item = event.target.closest("[data-sort-key], [data-sort-dir]");
  if (!item) return;
  if (item.dataset.sortKey) purchaseView.sort = { key: item.dataset.sortKey, dir: purchaseView.sort.dir };
  else purchaseView.sort = { key: purchaseView.sort.key, dir: item.dataset.sortDir };
  setSubtabMenu($("#purchase-sort-menu"), false);
  purchaseViewApply();
});
$("#purchase-options-list").addEventListener("click", (event) => {
  const item = event.target.closest("[data-size]");
  if (!item) return;
  purchaseView.options.size = item.dataset.size;
  purchaseViewApply();
});
$("#purchase-options-list").addEventListener("change", (event) => {
  const input = event.target.closest("[data-option]");
  if (!input) return;
  purchaseView.options[input.dataset.option] = input.checked;
  purchaseViewApply();
});
document.querySelector(".purchase-table thead").addEventListener("click", (event) => {
  const button = event.target.closest(".purchase-table__sort");
  if (button) purchaseSortBy(button.dataset.sort);
});
purchaseViewSyncBar();
