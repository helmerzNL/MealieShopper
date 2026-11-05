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

// Bij een ander venster past het zichtbare vak zijn hoogte aan (de tabel doet dat zelf in orders.js).
function purchaseViewFit() {
  for (const id of ["ah-purchase-cards", "ah-purchase-overview"]) {
    const pane = $(`#${id}`);
    if (!pane || pane.hidden) continue;
    const height = pageFitHeight(pane);
    pane.style.maxHeight = height === null ? "" : `${height}px`;
  }
}

window.addEventListener("resize", () => {
  if (ordersState.loaded) purchaseViewFit();
});

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

// ---- Kaarten: een raster met per aankoop een collage van productfoto's en de belangrijkste gegevens eronder.

const MDI_CART = "M17,18C15.89,18 15,18.89 15,20A2,2 0 0,0 17,22A2,2 0 0,0 19,20C19,18.89 18.1,18 17,18M1,2V4H3L6.6,11.59L5.24,14.04C5.09,14.32 5,14.65 5,15A2,2 0 0,0 7,17H19V15H7.42A0.25,0.25 0 0,1 7.17,14.75C7.17,14.7 7.18,14.66 7.2,14.63L8.1,13H15.55C16.3,13 16.96,12.58 17.3,11.97L20.88,5.5C20.95,5.34 21,5.17 21,5A1,1 0 0,0 20,4H5.21L4.27,2M7,18C5.89,18 5,18.89 5,20A2,2 0 0,0 7,22A2,2 0 0,0 9,20C9,18.89 8.1,18 7,18Z";
const MDI_RECEIPT = "M3,22L4.5,20.5L6,22L7.5,20.5L9,22L10.5,20.5L12,22L13.5,20.5L15,22L16.5,20.5L18,22L19.5,20.5L21,22V2L19.5,3.5L18,2L16.5,3.5L15,2L13.5,3.5L12,2L10.5,3.5L9,2L7.5,3.5L6,2L4.5,3.5L3,2M18,9H6V7H18M18,13H6V11H18M18,17H6V15H18V17Z";

function purchaseKindIcon(kind) {
  return kind === "receipt" ? MDI_RECEIPT : kind === "imported" ? MDI_ARCHIVE_OUTLINE : MDI_CART;
}

// Een ingelezen bestelling is ook gewoon een Bestelling (#229); alleen het archief-icoon erachter (purchaseKindLabel), de grijze
// kleur van de soort en de prullenbak laten zien dat ze uit je bevestigingsmails komt.
function purchaseKindText(kind) {
  return t(kind === "receipt" ? "orders.kind.receipt" : "orders.kind.order");
}

// De soort als HTML, voor de tabel, de kaarten en het overzicht. Het archief-icoon zegt als tooltip en voor een schermlezer waar
// een ingelezen bestelling vandaan komt: de tekst ervoor is dezelfde als bij een bestelling uit je account. Het icoon staat achter
// het woord, zodat "Bestelling" in de kolom Soort onder elkaar uitlijnt.
function purchaseKindLabel(kind) {
  return escapeHtml(purchaseKindText(kind)) + purchaseOriginIcon(kind);
}

// Het archief-icoon van een ingelezen bestelling; voor de andere soorten niets.
function purchaseOriginIcon(kind) {
  if (kind !== "imported") return "";
  const label = escapeHtml(t("orders.imported.badge"));
  return `<span class="purchase-kind-badge" role="img" aria-label="${label}" title="${label}">${mdiIcon(MDI_ARCHIVE_OUTLINE)}</span>`;
}

// De statusbadge van een aankoop (afgeleverd, ingepland, ingelezen, kassabon), met het archief-icoon erachter bij een ingelezen
// bestelling. De tekst is die van AH voor een geplande bestelling (statusText), anders de vaste tekst van de status.
const PURCHASE_STATUS_TEXT = {
  delivered: "orders.status.delivered", planned: "orders.status.planned", imported: "orders.status.imported", receipt: "orders.status.receipt",
};

function purchaseStatusBadge(row) {
  const text = row.statusText || t(PURCHASE_STATUS_TEXT[row.statusKind] || "orders.status.receipt");
  return badge("status-" + row.statusKind, text) + purchaseOriginIcon(row.kind);
}

// De collage: hooguit `count` foto's (4 als 2x2, 6 als 3x2; minder foto's worden groter, zie de CSS per aantal). Zonder foto's
// (een ingelezen bestelling of een bon zonder gekoppelde producten, producten nog niet opgehaald) het pictogram van de soort en de
// eerste namen.
function purchaseCollage(row, count) {
  const urls = row.images.slice(0, count);
  if (!urls.length) {
    const names = row.names.slice(0, 2).join(", ");
    return `<div class="purchase-collage purchase-collage--empty">${mdiIcon(purchaseKindIcon(row.kind), "mdi purchase-collage__icon")}${names ? `<small>${escapeHtml(names)}</small>` : ""}</div>`;
  }
  return `<div class="purchase-collage purchase-collage--${urls.length}">${urls.map((url) => `<img src="${escapeHtml(storeImage(url))}" alt="" loading="lazy" data-hide-on-error>`).join("")}</div>`;
}

// De regels onder de collage, naar de opties: status en bezorgmoment (of de betaalwijze van een bon), producten en bedrag,
// het bespaarde bedrag, en de productnamen (bij zoeken de gevonden regels).
function purchaseDetailLines(row, matches, { names = true, amount = true } = {}) {
  const lines = [];
  if (purchaseView.options.status) {
    const status = row.kind === "receipt" ? receiptMethod(row.payment) : row.status; // zoals op de bon zelf: PINNEN, niet PIN
    if (status) lines.push(escapeHtml(status));
  }
  const count = row.items === null ? t("orders.items_pending") : tn("list.items", row.items);
  lines.push(escapeHtml([count, amount ? money(row.total) : ""].filter(Boolean).join(" · ")));
  if (purchaseView.options.savings && row.savings > 0) lines.push(`<span class="purchase-card__savings">${escapeHtml(t("orders.saved", { amount: money(row.savings) }))}</span>`);
  if (names && matches) {
    const shown = matches.slice(0, 3).map((entry) => `${entry.quantity ? `${entry.quantity}x ` : ""}${entry.product}`);
    if (matches.length > 3) shown.push(t("orders.more", { count: matches.length - 3 }));
    lines.push(`<span class="purchase-card__hits">${escapeHtml(shown.join(", "))}</span>`);
  } else if (names && purchaseView.options.names && row.names.length) {
    lines.push(`<span class="purchase-card__names">${escapeHtml(row.names.join(", "))}</span>`);
  }
  return lines;
}

function purchaseDeleteButton(row, className) {
  if (row.kind !== "imported" || !priceImportIsOwner()) return "";
  const remove = t("orders.imported.delete");
  return `<button type="button" class="icon-button ${className}" data-imported-delete="${escapeHtml(row.ref)}" data-title="${escapeHtml(row.label)}" aria-label="${escapeHtml(`${remove}: ${row.label}`)}" title="${escapeHtml(remove)}">${mdiIcon(MDI_DELETE_OUTLINE)}</button>`;
}

// De kaart: de link ligt over de hele kaart (purchase-card__link), de prullenbak van een ingelezen bestelling staat erbuiten,
// zodat een klik erop niet ook de bestelling opent.
function purchaseCard(row, matches) {
  const count = purchaseView.options.size === "large" ? 6 : 4;
  return `<article class="purchase-card${row.open ? " is-open" : ""}" ${row.attr}>
      ${purchaseKindMarkup(row.kind, "stripe")}
      <a class="purchase-card__link" href="${escapeHtml(row.href)}" data-nav>
        ${purchaseCollage(row, count)}
        <strong class="purchase-card__date">${escapeHtml(row.label)}</strong>
        <span class="purchase-card__kind">${purchaseStatusBadge(row)}</span>
        ${purchaseDetailLines(row, matches).map((line) => `<span class="purchase-card__line">${line}</span>`).join("")}
      </a>
      ${purchaseDeleteButton(row, "purchase-card__action")}
    </article>`;
}

function renderPurchaseCards(visible, hits) {
  const grid = $("#ah-purchase-cards");
  grid.className = `purchase-cards purchase-cards--${purchaseView.options.size}`;
  grid.innerHTML = ordersState.empty
    ? `<p class="muted purchase-cards__empty">${ordersState.empty}</p>`
    : visible.map((row) => purchaseCard(row, hits ? hits.get(`${row.kind}:${row.id}`) : null)).join("");
  window.requestAnimationFrame(purchaseViewFit);
}

// ---- Overzicht: een rij per aankoop met links de details op een paar regels en rechts een strook foto's. De strook staat rechts
// op een vaste breedte: een bon zonder gekoppelde foto's (een icoon) en een bestelling met zes foto's beginnen hun tekst zo op
// dezelfde plek.

function purchaseOverviewRow(row, matches) {
  const urls = row.images.slice(0, 6);
  const strip = urls.length
    ? urls.map((url) => `<img src="${escapeHtml(storeImage(url))}" alt="" loading="lazy" data-hide-on-error>`).join("")
    : mdiIcon(purchaseKindIcon(row.kind), "mdi purchase-overview__icon");
  const lines = purchaseDetailLines(row, matches, { names: false, amount: false }); // het bedrag staat al in de kop
  const meta = [purchaseStatusBadge(row), ...lines].join(" · ");
  let names = "";
  if (matches) {
    const shown = matches.slice(0, 3).map((entry) => `${entry.quantity ? `${entry.quantity}x ` : ""}${entry.product}`);
    if (matches.length > 3) shown.push(t("orders.more", { count: matches.length - 3 }));
    names = `<span class="purchase-overview__names purchase-overview__names--hits">${escapeHtml(shown.join(", "))}</span>`;
  } else if (purchaseView.options.names && row.names.length) {
    names = `<span class="purchase-overview__names">${escapeHtml(row.names.join(", "))}</span>`;
  }
  return `<article class="purchase-overview__row${row.open ? " is-open" : ""}" ${row.attr}>
      ${purchaseKindMarkup(row.kind, "stripe")}
      <a class="purchase-overview__link" href="${escapeHtml(row.href)}" data-nav>
        <span class="purchase-overview__body">
          <span class="purchase-overview__head"><strong>${escapeHtml(row.label)}</strong><span class="purchase-overview__amount">${escapeHtml(money(row.total))}</span></span>
          <span class="purchase-overview__meta">${meta}</span>
          ${names}
        </span>
        <span class="purchase-overview__strip">${strip}</span>
      </a>
      ${purchaseDeleteButton(row, "purchase-overview__action")}
    </article>`;
}

function renderPurchaseOverview(visible, hits) {
  const list = $("#ah-purchase-overview");
  list.innerHTML = ordersState.empty
    ? `<p class="muted">${ordersState.empty}</p>`
    : visible.map((row) => purchaseOverviewRow(row, hits ? hits.get(`${row.kind}:${row.id}`) : null)).join("");
  window.requestAnimationFrame(purchaseViewFit);
}
