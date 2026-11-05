// De weergaven van de aankopen (Winkels > Bestellingen): dezelfde gefilterde lijst als Tabel (orders.js), Kaarten of Overzicht,
// met sorteren en weergaveopties. De keuze staat in purchaseView en in localStorage, zodat terug uit een bestelling en een
// volgende keer dezelfde weergave geven. Laadt na orders.js en gebruikt daar purchaseRows(), renderOrders() en de rijvelden van
// (kind, label, date, open, status, total, items, images, names, savings, bonusCount, payment, href, attr).

// ---- De weergavebalk: één component voor de aankopenlijst (prefix "purchase") en de bestelpagina (prefix "order"). Links de
// statusregel, rechts de weergaveknoppen, het sorteermenu en het optiemenu. De container in index.html is
// <div class="purchase-bar" data-view-bar="<prefix>">; de ids in de balk dragen de prefix (<prefix>-view-switch,
// <prefix>-sort-list, <prefix>-options-list, ...).

const MDI_VIEW_TABLE = "M5,4H19A2,2 0 0,1 21,6V18A2,2 0 0,1 19,20H5A2,2 0 0,1 3,18V6A2,2 0 0,1 5,4M5,8V12H11V8H5M13,8V12H19V8H13M5,14V18H11V14H5M13,14V18H19V14H13Z";
const MDI_VIEW_CARDS = "M3,11H11V3H3M3,21H11V13H3M13,21H21V13H13M13,3V11H21V3";
const MDI_VIEW_LIST = "M9,5V9H21V5M9,19H21V15H9M9,14H21V10H9M4,9H8V5H4M4,19H8V15H4M4,14H8V10H4V14Z";
const MDI_CHEVRON_DOWN = "M7.41,8.58L12,13.17L16.59,8.58L18,10L12,16L6,10L7.41,8.58Z";

// De statusregel van de lijst heet ah-purchase-status: orders.js schrijft daar (renderOrders, het zoeken). Een andere balk krijgt
// <prefix>-status.
function viewStatusId(prefix) {
  return prefix === "purchase" ? "ah-purchase-status" : `${prefix}-status`;
}

function viewBarElement(prefix) {
  return document.querySelector(`[data-view-bar="${prefix}"]`);
}

// De keuze van één balk: weergave, sortering en opties, in localStorage onder ms-<prefix>s-view, -sort en -options (voor de lijst
// dus de bestaande ms-purchases-*). `allowed` ({ views, sorts, sizes, options }) zegt welke waarden gelden: wat in localStorage
// staat kan van een oudere versie zijn of met de hand aangepast. apply() bewaart en roept dan de functie aan die listen() kreeg
// (de pagina tekent opnieuw). Die functie staat in een closure, zodat JSON.stringify(state) alleen de keuze geeft.
//
// Met `scopes` (zoals ["order", "receipt"]) is de weergave er een per soort: defaults.view en allowed.views zijn dan objecten per
// scope, en ms-<prefix>s-view bewaart JSON ({ order: "table", receipt: "paper" }). `state.view` is de weergave van de scope die
// useScope() koos. ViewFor() en setView() lezen en zetten die van een andere. Sortering en opties blijven één keuze per balk.
function viewState(prefix, defaults, allowed, { scopes = null } = {}) {
  const keys = { view: `ms-${prefix}s-view`, sort: `ms-${prefix}s-sort`, options: `ms-${prefix}s-options` };
  const views = scopes ? Object.fromEntries(scopes.map((scope) => [scope, defaults.view[scope]])) : null;
  const fits = (scope, view) => (scopes ? (allowed.views[scope] || []) : allowed.views).includes(view);
  let scope = scopes ? scopes[0] : null;
  let onChange = () => {};
  return {
    view: scopes ? views[scope] : defaults.view,
    sort: { ...defaults.sort },
    options: { ...defaults.options },
    listen(callback) {
      onChange = callback;
    },
    viewFor(name) {
      if (name === scope) return this.view;
      return views ? views[name] : this.view;
    },
    setView(name, view) {
      if (!views || !fits(name, view)) return;
      views[name] = view;
      if (name === scope) this.view = view;
      this.apply();
    },
    useScope(name) {
      if (!views || !(name in views)) return;
      if (fits(scope, this.view)) views[scope] = this.view;
      scope = name;
      this.view = views[name];
    },
    load() {
      try {
        const view = localStorage.getItem(keys.view);
        if (scopes) {
          let stored = null;
          try {
            stored = JSON.parse(view || "null");
          } catch {
            // een waarde van voor de scopes (een kale weergave): de standaard per scope
          }
          if (stored && typeof stored === "object") for (const name of scopes) if (fits(name, stored[name])) views[name] = stored[name];
          this.view = views[scope];
        } else if (fits(null, view)) {
          this.view = view;
        }
        const sort = JSON.parse(localStorage.getItem(keys.sort) || "null");
        if (sort && allowed.sorts.includes(sort.key) && ["asc", "desc"].includes(sort.dir)) this.sort = { key: sort.key, dir: sort.dir };
        const options = JSON.parse(localStorage.getItem(keys.options) || "null");
        if (options && typeof options === "object") {
          if (allowed.sizes.includes(options.size)) this.options.size = options.size;
          for (const key of allowed.options) if (typeof options[key] === "boolean") this.options[key] = options[key];
        }
      } catch {
        // een browser zonder opslag (privévenster): de standaardwaarden
      }
    },
    save() {
      // Een weergave die niet bij de scope past (Bon bij een bestelling) wordt niet bewaard.
      if (views && fits(scope, this.view)) views[scope] = this.view;
      try {
        localStorage.setItem(keys.view, views ? JSON.stringify(views) : this.view);
        localStorage.setItem(keys.sort, JSON.stringify(this.sort));
        localStorage.setItem(keys.options, JSON.stringify(this.options));
      } catch {
        // niet erg: de keuze geldt tot je de pagina verlaat
      }
    },
    apply() {
      this.save();
      onChange();
    },
    // Zonder richting: dezelfde sleutel wisselt de richting, een andere sleutel begint in `start`, de beginrichting van die sleutel
    // uit de spec van de balk (viewSortStart). Met een richting en zonder sleutel blijft de sleutel staan (Oplopend of Aflopend in
    // het menu).
    sortBy(key, dir = null, start = "desc") {
      if (dir) this.sort = { key: key || this.sort.key, dir };
      else if (this.sort.key === key) this.sort = { key, dir: this.sort.dir === "asc" ? "desc" : "asc" };
      else this.sort = { key, dir: start === "asc" ? "asc" : "desc" };
      this.apply();
    },
  };
}

// De balk als HTML. spec = { views: [{ id, icon, label }], sorts: [{ key, label, dir }], sizes: bool, options: [{ key, label }] },
// met de labels al vertaald en per sorteersleutel de richting waarin hij begint (`dir`, "asc" of "desc"). Zonder sorteersleutels geen sorteermenu, zonder groottes en opties geen optiemenu. Wat er gekozen is
// (aria-pressed, is-active, de vinkjes) zet syncViewBar.
function viewBarMarkup(prefix, spec) {
  const views = spec.views
    .map((view) => `<button class="subtab" type="button" data-view="${escapeHtml(view.id)}" aria-pressed="false">${view.icon ? mdiIcon(view.icon) : ""}<span>${escapeHtml(view.label)}</span></button>`)
    .join("");
  const sortMenu = spec.sorts.length ? viewSortMenu(prefix, spec.sorts) : "";
  const optionsMenu = spec.sizes || spec.options.length ? viewOptionsMenu(prefix, spec) : "";
  return `<p class="muted purchase-status" id="${viewStatusId(prefix)}"></p><div class="purchase-bar__tools"><div class="purchase-view-switch" id="${prefix}-view-switch" role="group" aria-label="${escapeHtml(t("orders.view.label"))}">${views}</div>${sortMenu}${optionsMenu}</div>`;
}

// De richting waarin een sorteersleutel begint, uit de spec van de balk. Zonder richting aflopend.
function viewSortStart(spec, key) {
  const sort = spec.sorts.find((item) => item.key === key);
  return sort && sort.dir === "asc" ? "asc" : "desc";
}

function viewSortMenu(prefix, sorts) {
  const keys = sorts
    .map((sort) => `<button class="subtab-menu__item" type="button" data-sort-key="${escapeHtml(sort.key)}" data-sort-start="${sort.dir === "asc" ? "asc" : "desc"}">${escapeHtml(sort.label)}</button>`)
    .join("");
  return `<div class="subtab-menu" id="${prefix}-sort-menu"><button class="subtab subtab-menu__toggle subtab-menu__toggle--select" id="${prefix}-sort-toggle" type="button" aria-haspopup="true" aria-expanded="false" aria-controls="${prefix}-sort-list"><span>${escapeHtml(t("orders.sort.label"))}</span>: <span id="${prefix}-sort-current"></span>${mdiIcon(MDI_CHEVRON_DOWN, "subtab-menu__chevron")}</button>`
    + `<div class="subtab-menu__list" id="${prefix}-sort-list" hidden>${keys}<hr class="subtab-menu__rule">`
    + `<button class="subtab-menu__item" type="button" data-sort-dir="desc">${escapeHtml(t("orders.sort.desc"))}</button>`
    + `<button class="subtab-menu__item" type="button" data-sort-dir="asc">${escapeHtml(t("orders.sort.asc"))}</button></div></div>`;
}

function viewOptionsMenu(prefix, spec) {
  const sizes = [["small", t("orders.options.small")], ["medium", t("orders.options.medium")], ["large", t("orders.options.large")]];
  const sizeItems = spec.sizes
    ? `<span class="purchase-options__title">${escapeHtml(t("orders.options.size"))}</span>`
      + sizes.map(([size, label]) => `<button class="subtab-menu__item" type="button" data-size="${size}">${escapeHtml(label)}</button>`).join("")
    : "";
  const rule = spec.sizes && spec.options.length ? '<hr class="subtab-menu__rule">' : "";
  const checks = spec.options
    .map((option) => `<label class="check purchase-options__check"><input type="checkbox" data-option="${escapeHtml(option.key)}"> <span>${escapeHtml(option.label)}</span></label>`)
    .join("");
  return `<div class="subtab-menu" id="${prefix}-options-menu"><button class="subtab subtab-menu__toggle subtab-menu__toggle--select" id="${prefix}-options-toggle" type="button" aria-haspopup="true" aria-expanded="false" aria-controls="${prefix}-options-list"><span>${escapeHtml(t("orders.options.label"))}</span>${mdiIcon(MDI_CHEVRON_DOWN, "subtab-menu__chevron")}</button>`
    + `<div class="subtab-menu__list purchase-options" id="${prefix}-options-list" hidden>${sizeItems}${rule}${checks}</div></div>`;
}

// Tekent de balk in zijn container en bindt de uitklapmenu's zoals app.js de andere bindt (bindSubtabMenu: openen, Esc; een
// klik elders sluit ze via de documentbrede handler daar). De statusregel houdt zijn tekst: die schrijft de pagina zelf, en
// een andere taal tekent de balk opnieuw.
function renderViewBar(prefix, state, spec) {
  const bar = viewBarElement(prefix);
  const status = document.getElementById(viewStatusId(prefix));
  const text = status ? status.textContent : "";
  bar.innerHTML = viewBarMarkup(prefix, spec);
  document.getElementById(viewStatusId(prefix)).textContent = text;
  bar.querySelectorAll(".subtab-menu").forEach(bindSubtabMenu);
  syncViewBar(prefix, state, spec);
}

// De handlers van een balk, één keer per pagina. Ze luisteren op de container, dus een opnieuw getekende balk werkt meteen.
// Een keuze komt in de staat, die bewaart hem en roept onChange aan (de pagina tekent opnieuw).
function bindViewBar(prefix, state, onChange) {
  state.listen(onChange);
  const bar = viewBarElement(prefix);
  bar.addEventListener("click", (event) => {
    const view = event.target.closest(`#${prefix}-view-switch [data-view]`);
    if (view) {
      state.view = view.dataset.view;
      state.apply();
      return;
    }
    const sort = event.target.closest(`#${prefix}-sort-list [data-sort-key], #${prefix}-sort-list [data-sort-dir]`);
    if (sort) {
      setSubtabMenu($(`#${prefix}-sort-menu`), false);
      // Een sleutel: dezelfde wisselt, een andere begint in zijn eigen richting (data-sort-start uit de spec).
      if (sort.dataset.sortKey) state.sortBy(sort.dataset.sortKey, null, sort.dataset.sortStart);
      else state.sortBy(null, sort.dataset.sortDir);
      return;
    }
    const size = event.target.closest(`#${prefix}-options-list [data-size]`);
    if (size) {
      state.options.size = size.dataset.size;
      state.apply();
    }
  });
  bar.addEventListener("change", (event) => {
    const input = event.target.closest(`#${prefix}-options-list [data-option]`);
    if (!input) return;
    state.options[input.dataset.option] = input.checked;
    state.apply();
  });
}

// De balk laat zien wat er gekozen is: de actieve weergave, de sortering (met het label uit de spec) en de opties.
function syncViewBar(prefix, state, spec) {
  for (const button of document.querySelectorAll(`#${prefix}-view-switch [data-view]`)) {
    const active = button.dataset.view === state.view;
    button.setAttribute("aria-pressed", String(active));
    button.classList.toggle("is-active", active);
  }
  const { key, dir } = state.sort;
  const current = $(`#${prefix}-sort-current`);
  if (current) {
    const sort = spec.sorts.find((item) => item.key === key);
    current.textContent = `${sort ? sort.label : key} ${dir === "asc" ? "↑" : "↓"}`;
  }
  for (const item of document.querySelectorAll(`#${prefix}-sort-list [data-sort-key]`)) item.classList.toggle("is-active", item.dataset.sortKey === key);
  for (const item of document.querySelectorAll(`#${prefix}-sort-list [data-sort-dir]`)) item.classList.toggle("is-active", item.dataset.sortDir === dir);
  for (const item of document.querySelectorAll(`#${prefix}-options-list [data-size]`)) item.classList.toggle("is-active", item.dataset.size === state.options.size);
  for (const input of document.querySelectorAll(`#${prefix}-options-list [data-option]`)) input.checked = Boolean(state.options[input.dataset.option]);
}

// ---- De balk van de aankopenlijst.

const PURCHASE_VIEWS = ["table", "cards", "overview"];
const PURCHASE_SORT_KEYS = ["date", "amount", "items"];
const PURCHASE_SIZES = ["small", "medium", "large"];
const PURCHASE_OPTIONS = ["status", "savings", "names"];

const purchaseView = viewState("purchase", { view: "table", sort: { key: "date", dir: "desc" }, options: { size: "medium", status: true, savings: true, names: true } }, { views: PURCHASE_VIEWS, sorts: PURCHASE_SORT_KEYS, sizes: PURCHASE_SIZES, options: PURCHASE_OPTIONS });

// De weergaven, sorteersleutels en opties van de lijst, met de labels in de taal van nu.
function purchaseBarSpec() {
  return {
    views: [
      { id: "table", icon: MDI_VIEW_TABLE, label: t("orders.view.table") },
      { id: "cards", icon: MDI_VIEW_CARDS, label: t("orders.view.cards") },
      { id: "overview", icon: MDI_VIEW_LIST, label: t("orders.view.overview") },
    ],
    sorts: [
      { key: "date", label: t("orders.sort.date"), dir: "desc" },
      { key: "amount", label: t("orders.sort.amount"), dir: "desc" },
      { key: "items", label: t("orders.sort.items"), dir: "desc" },
    ],
    sizes: true,
    options: [
      { key: "status", label: t("orders.options.status") },
      { key: "savings", label: t("orders.options.savings") },
      { key: "names", label: t("orders.options.names") },
    ],
  };
}

function purchaseViewLoad() {
  purchaseView.load();
}

// Sorteren: datum (open bestellingen bovenaan bij aflopend, hun bezorgdag ligt in de toekomst), bedrag of aantal producten.
// Een aankoop zonder waarde (producten nog niet opgehaald) staat onderaan, in beide richtingen; gelijke waarden op datum aflopend.
function purchaseSortRows(rows, sort = purchaseView.sort) {
  const { key, dir } = sort;
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

// De balk en de tabelkoppen laten zien wat er gekozen is.
function purchaseViewSyncBar() {
  syncViewBar("purchase", purchaseView, purchaseBarSpec());
  const { key, dir } = purchaseView.sort;
  for (const button of document.querySelectorAll(".purchase-table__sort")) {
    const th = button.closest("th");
    if (button.dataset.sort === key) th.setAttribute("aria-sort", dir === "asc" ? "ascending" : "descending");
    else th.removeAttribute("aria-sort");
  }
}

// Een klik op een tabelkop: dezelfde sleutel wisselt de richting, een andere begint in zijn eigen richting (zoals het menu).
function purchaseSortBy(key, dir = null) {
  purchaseView.sortBy(key, dir, viewSortStart(purchaseBarSpec(), key));
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
renderViewBar("purchase", purchaseView, purchaseBarSpec());
bindViewBar("purchase", purchaseView, () => {
  if (ordersState.loaded) renderOrders();
  else purchaseViewSyncBar();
});
document.querySelector(".purchase-table thead").addEventListener("click", (event) => {
  const button = event.target.closest(".purchase-table__sort");
  if (button) purchaseSortBy(button.dataset.sort);
});
purchaseViewSyncBar();
// Een andere taal: de balk opnieuw, met de labels in die taal (orders.js tekent de lijst en de statusregel al opnieuw).
window.addEventListener("ms:languagechange", () => {
  renderViewBar("purchase", purchaseView, purchaseBarSpec());
  purchaseViewSyncBar();
});

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
  return `<div class="purchase-collage purchase-collage--${urls.length}">${urls.map((url) => `<img src="${escapeHtml(storeImage(url))}" alt="" loading="lazy" data-hide-on-error data-popover="product" data-image="${escapeHtml(url)}">`).join("")}</div>`;
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

// Welke aankoop een rij, kaart of foto is, voor het hovervenster (popover.js): de soort en het id, zoals purchaseRows ze kent.
function purchasePopoverKey(row) {
  return `data-purchase-kind="${escapeHtml(row.kind)}" data-purchase-id="${escapeHtml(row.id)}"`;
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
  return `<article class="purchase-card${row.open ? " is-open" : ""}" ${row.attr} data-popover="purchase" ${purchasePopoverKey(row)}>
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
    ? urls.map((url) => `<img src="${escapeHtml(storeImage(url))}" alt="" loading="lazy" data-hide-on-error data-popover="product" data-image="${escapeHtml(url)}">`).join("")
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
  return `<article class="purchase-overview__row${row.open ? " is-open" : ""}" ${row.attr} data-popover="purchase" ${purchasePopoverKey(row)}>
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
