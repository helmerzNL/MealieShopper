// Bestellingen, bestelmandje en kassabonnen: lezen uit de lokale kopie, synchroniseren bij AH.
// Laadt na app.js en gebruikt daar de hulpfuncties ($, message, jsonFetch, t, tn, escapeHtml, formatPrice, ...).

// De winkel komt uit de keuze in de tab Winkels (state.itemsStore in app.js): "ah" of "jumbo".
function ordersApi() {
  return `/api/${state.itemsStore}`;
}

function ordersAreJumbo() {
  return state.itemsStore === "jumbo";
}

// `open`: de bestelling of kassabon die open staat ({ kind, id }), voor het adres (router.js).
// `imported`: de ingelezen oude bestellingen van deze winkel (zie price_import.py), die bij de afgelopen bestellingen staan.
const ordersState = { basket: null, orders: [], imported: [], receipts: [], ordersSyncedAt: null, receiptsSyncedAt: null, query: "", loaded: false, open: null };

function dayLabel(value) {
  const date = new Date(String(value || "").length === 10 ? `${value}T12:00:00` : value);
  if (!value || Number.isNaN(date.getTime())) return "";
  return date.toLocaleDateString(I18N.locale(), { weekday: "short", day: "numeric", month: "short", year: "numeric" });
}

function slotLabel(start, end) {
  const clock = (value) => {
    const text = String(value || "");
    // Met tijdzone (Jumbo: ...Z): omrekenen naar de lokale tijd; zonder tijdzone staat de tijd er al zoals AH hem geeft.
    if (/(Z|[+-]\d{2}:?\d{2})$/.test(text) && text.length > 10) {
      const date = new Date(text);
      if (!Number.isNaN(date.getTime())) return date.toLocaleTimeString(I18N.locale(), { hour: "2-digit", minute: "2-digit" });
    }
    return (/(\d{2}:\d{2})/.exec(text) || [])[1] || "";
  };
  const from = clock(start);
  const to = clock(end);
  return from && to ? `${from}-${to}` : from || to;
}

function money(value) {
  return value === null || value === undefined ? "" : formatPrice(value);
}

// Een product uit een bestelling, een bon of het mandje, als regel van orderLine (order_views.js): de hele kaart in de kleur van
// zijn categorie, de naam als link naar de prijsgeschiedenis, merk, inhoud en categorie, de badges en het bedrag. Het mandje
// heeft geen categorie (category null): dan een gewone kaart.
function productCard(line) {
  const cls = line.category ? `product-card cat--${escapeHtml(line.category)}` : "product-card";
  const name = line.href ? `<a href="${escapeHtml(line.href)}" data-route>${escapeHtml(line.title)}</a>` : escapeHtml(line.title);
  const meta = [line.brand, line.unitSize, line.category ? categoryLabel(line.category) : ""].filter(Boolean).join(" · ");
  const badges = orderLineBadges(line);
  return `
    <article class="${cls}">
      ${line.image ? `<img src="${escapeHtml(storeImage(line.image))}" alt="" loading="lazy" data-hide-on-error>` : ""}
      <div>
        <strong>${escapeHtml(line.quantity)}x ${name}</strong>
        ${meta ? `<small>${escapeHtml(meta)}</small>` : ""}
        ${badges ? `<span class="product-card__badges">${badges}</span>` : ""}
        ${line.amount ? `<small>${escapeHtml(money(line.amount))}</small>` : ""}
      </div>
    </article>`;
}

function renderBasket() {
  const basket = ordersState.basket;
  const target = $("#ah-basket");
  // Komt het AH-mandje binnen terwijl er al een andere winkel gekozen is, dan staat het niet meer in beeld: niets tekenen.
  if (!target || basketState.store !== "ah") return;
  $("#ah-basket-note").textContent = t("orders.basket_note");
  if (!basket?.syncedAt) {
    target.innerHTML = `<p class="muted">${escapeHtml(t("orders.never_synced"))}</p>`;
    return;
  }
  if (!basket.itemCount) {
    target.innerHTML = `<p class="muted">${escapeHtml(t("orders.basket_empty", { when: formatSyncTime(basket.syncedAt) }))}</p>`;
    return;
  }
  const delivery = [dayLabel(basket.deliveryDate), slotLabel(basket.slotStart, basket.slotEnd)].filter(Boolean).join(", ");
  const summary = [
    tn("list.items", basket.itemCount),
    t("orders.units", { count: I18N.formatNumber(basket.units) }),
    money(basket.total),
    delivery ? t("orders.delivery", { when: delivery }) : "",
    t("orders.updated", { when: formatSyncTime(basket.syncedAt) }),
  ].filter(Boolean);
  target.innerHTML = `<p>${escapeHtml(summary.join(" · "))}</p><div class="product-grid">${basket.items.map((item, index) => productCard({ ...orderLine(item, index, "order", "ah"), category: null })).join("")}</div>`;
}

// Het AH-mandje (de boodschappenlijst waar het weekmenu in schrijft) staat in het tabblad Mandje (basket.js):
// eerst de lokale kopie, daarna haalt het de actuele lijst op. Mislukt dat, dan blijft de kopie staan met de fout.
async function showAhBasket() {
  $("#basket-content").innerHTML = `
    <p class="muted" id="ah-basket-note"></p>
    <p><button class="button button--secondary" id="ah-basket-sync" type="button">${escapeHtml(t("common.sync"))}</button></p>
    <div id="ah-basket"></div>`;
  $("#ah-basket-sync").addEventListener("click", syncAhBasket);
  ordersState.basket = await jsonFetch("/api/ah/basket");
  renderBasket();
  syncAhBasket();
}

async function syncAhBasket() {
  const button = $("#ah-basket-sync");
  setBusy(button, true);
  message("#basket-message", "");
  try {
    await jsonFetch("/api/ah/basket/sync", { method: "POST" });
    ordersState.basket = await jsonFetch("/api/ah/basket");
    renderBasket();
  } catch (error) {
    // Een mislukte synchronisatie hoort bij het AH-mandje, niet bij de winkel die nu gekozen is.
    if (basketState.store === "ah") message("#basket-message", error.message);
  } finally {
    setBusy(button, false);
  }
}

function ordersStore() {
  return ordersAreJumbo() ? "jumbo" : "ah";
}

// Zonder hoofdletters en accenten, zoals de server zoekt
function foldText(value) {
  return String(value || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
}

function matchesQuery(name, words) {
  const folded = foldText(name);
  return words.length > 0 && words.every((word) => folded.includes(word));
}

function receiptDay(receipt) {
  const date = new Date(receipt.dateTime);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleString(I18N.locale(), { dateStyle: "medium", timeStyle: "short" });
}

// ---- de aankopentabel: je bestellingen (uit je account en ingelezen uit je mails) en je kassabonnen samen, de nieuwste eerst.
// Erboven de filters: welke soorten, een bedrag van en tot, en het zoekveld dat op de producten in een bestelling of bon zoekt
// (op de server, zie searchPurchases); de tabel laat dan alleen de aankopen met dat product zien, met de gevonden regels erin.
// De filters werken in de browser op de lijst die er al staat: een paar honderd rijen, in een keer getekend, zonder herladen.
const purchaseFilters = { orders: true, receipts: true, min: "", max: "" };

function purchaseRows() {
  const store = ordersStore();
  // Een bestelling uit je account zonder producten waarvan de ingelezen versie er is, staat er niet nog een keer (#229).
  const { kept, covered } = importedOrdersCover(ordersState.orders, ordersState.imported);
  const rows = kept.map((order) => ({
    kind: "order", id: String(order.id), store, date: order.deliveryDate || "", open: order.kind === "open",
    label: dayLabel(order.deliveryDate) || t("orders.order_title", { id: order.id }),
    // De statusbadge noemt de status al (purchaseStatusBadge): de regel houdt alleen het bezorgmoment.
    status: slotLabel(order.slotStart, order.slotEnd),
    total: order.total, items: order.itemsSynced ? order.itemCount : null,
    href: storesPath(store, "orders", order.id), attr: `data-order-id="${escapeHtml(order.id)}"`,
    images: order.images || [], names: order.productNames || [], savings: order.savings, bonusCount: order.bonusCount || 0, payment: null,
    statusKind: order.statusKind, statusText: order.statusText,
  }));
  // Een ingelezen bestelling heeft dezelfde gegevens als een bestelling uit je account: status en tijdvak uit de mail, en de foto's,
  // het voordeel en de bonus van de producten waar de regels bij horen (price_import.list_orders).
  for (const order of ordersState.imported) {
    rows.push({
      kind: "imported", id: String(order.ref), store, date: order.date || "", open: false, label: importedOrderTitle(order),
      status: importedOrderSlot(order, covered.get(order.ref)), ref: order.ref,
      total: order.total, items: order.itemCount, href: storesPath(store, "imported", order.ref), attr: `data-imported-order="${escapeHtml(order.ref)}"`,
      images: order.images || [], names: order.productNames || [], savings: order.savings ?? null, bonusCount: order.bonusCount || 0, payment: null,
      statusKind: order.statusKind, statusText: order.statusText,
    });
  }
  if (!ordersAreJumbo()) {
    for (const receipt of ordersState.receipts) {
      rows.push({
        kind: "receipt", id: String(receipt.id), store: "ah", date: receipt.dateTime || "", open: false, label: receiptDay(receipt) || receipt.id, status: "",
        total: receipt.total, items: receipt.detailsSynced ? receipt.itemCount : null,
        href: storesPath("ah", "receipts", receipt.id), attr: `data-receipt-id="${escapeHtml(receipt.id)}"`,
        images: receipt.images || [], names: receipt.productNames || [], savings: receipt.savings, bonusCount: 0, payment: receipt.payment || null,
        statusKind: receipt.statusKind, statusText: receipt.statusText,
      });
    }
  }
  return rows; // de volgorde bepaalt purchaseSortRows (purchase_views.js)
}

function purchaseAmount(value) {
  const number = Number.parseFloat(String(value).replace(",", "."));
  return Number.isFinite(number) ? number : null;
}

function purchaseVisible(row, hits) {
  if (row.kind === "receipt" ? !purchaseFilters.receipts : !purchaseFilters.orders) return false;
  const min = purchaseAmount(purchaseFilters.min);
  const max = purchaseAmount(purchaseFilters.max);
  if (min !== null && !(row.total >= min)) return false;
  if (max !== null && !(row.total <= max)) return false;
  return !hits || hits.has(`${row.kind}:${row.id}`);
}

// De gevonden regels per aankoop (kind:id), of null zonder zoekopdracht: dan tellen alleen de andere filters.
function purchaseHits() {
  const hits = ordersState.hits;
  if (!ordersState.query.trim() || !hits) return null;
  const map = new Map();
  for (const entry of hits.occurrences) {
    const key = `${entry.source === "kassabon" ? "receipt" : entry.imported ? "imported" : "order"}:${entry.id}`;
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(entry);
  }
  return map;
}

function purchaseProductsCell(row, matches) {
  if (matches) {
    const shown = matches.slice(0, 3).map((entry) => `${entry.quantity ? `${entry.quantity}x ` : ""}${entry.product}`);
    if (matches.length > 3) shown.push(t("orders.more", { count: matches.length - 3 }));
    return `<span class="purchase-table__hits">${escapeHtml(shown.join(", "))}</span>`;
  }
  return row.items === null ? `<span class="muted">${escapeHtml(t("orders.items_pending"))}</span>` : escapeHtml(tn("list.items", row.items));
}

function purchaseRow(row, matches) {
  const kind = purchaseKindMarkup(row.kind, "dot") + purchaseStatusBadge(row);
  const remove = t("orders.imported.delete");
  const action = row.kind === "imported" && priceImportIsOwner()
    ? `<button type="button" class="icon-button purchase-table__action" data-imported-delete="${escapeHtml(row.ref)}" data-title="${escapeHtml(row.label)}" aria-label="${escapeHtml(`${remove}: ${row.label}`)}" title="${escapeHtml(remove)}">${mdiIcon(MDI_DELETE_OUTLINE)}</button>`
    : "";
  return `<tr class="purchase-table__row${row.open ? " is-open" : ""}" ${row.attr} data-popover="purchase" ${purchasePopoverKey(row)}>
      <td><a href="${escapeHtml(row.href)}" data-nav>${escapeHtml(row.label)}</a></td>
      <td>${kind}</td>
      <td>${escapeHtml(row.status)}</td>
      <td>${purchaseProductsCell(row, matches)}</td>
      <td class="purchase-table__amount">${escapeHtml(money(row.total))}</td>
      <td class="purchase-table__actions">${action}</td>
    </tr>`;
}

function renderOrders() {
  const jumbo = ordersAreJumbo();
  $("#ah-orders-intro").hidden = jumbo;
  $("#jumbo-orders-intro").hidden = !jumbo;
  $("#ah-filter-receipts-label").hidden = jumbo; // winkelbonnen van Jumbo kan MealieShopper nog niet tonen
  const rows = purchaseRows();
  const hits = purchaseHits();
  const visible = purchaseSortRows(rows.filter((row) => purchaseVisible(row, hits)));
  const query = ordersState.query.trim();
  const never = !ordersState.ordersSyncedAt && !ordersState.receiptsSyncedAt && !ordersState.imported.length;
  let empty = "";
  if (!rows.length) empty = escapeHtml(never ? t("orders.never_synced") : t(jumbo ? "orders.none_jumbo" : "orders.none"));
  else if (query && !hits) empty = escapeHtml(t("orders.searching"));
  else if (!visible.length) empty = query ? th("orders.search_none", { query }) : escapeHtml(t("orders.filter_none"));
  ordersState.empty = empty;
  renderPurchaseView(visible, hits);
  const notes = [rows.length ? t("orders.shown", { shown: I18N.formatNumber(visible.length), total: I18N.formatNumber(rows.length) }) : ""];
  if (hits) notes.push(t("orders.search_summary", { products: I18N.formatNumber(ordersState.hits.products.length), total: I18N.formatNumber(hits.size) }));
  if (hits && ordersState.hits.unsearched) notes.push(tn("orders.search_unsearched", ordersState.hits.unsearched));
  $("#ah-purchase-status").textContent = notes.filter(Boolean).join(" · ");
}

// De tabel: een rij per aankoop, of de melding waarom er niets staat (ordersState.empty, gezet door renderOrders).
function renderPurchaseTable(visible, hits) {
  $("#ah-purchase-rows").innerHTML = ordersState.empty
    ? `<tr><td colspan="6" class="muted">${ordersState.empty}</td></tr>`
    : visible.map((row) => purchaseRow(row, hits ? hits.get(`${row.kind}:${row.id}`) : null)).join("");
  window.requestAnimationFrame(fitPurchaseTable);
}

// De tabel vult het scherm tot onderaan en scrolt in zijn eigen vak (pageFitHeight in app.js); op een smal scherm scrolt de pagina.
function fitPurchaseTable() {
  const wrap = $("#ah-purchase-table-wrap");
  const height = pageFitHeight(wrap);
  wrap.style.maxHeight = height === null ? "" : `${height}px`;
}

// Het vak met de producten of de bon van een geopende aankoop zo hoog als er onder de kop past, en het scrolt zelf (op een breed
// scherm, gemeten door pageFitHeight), zodat de pagina niet schuift. Op een smal scherm doet de pagina het.
function fitReceiptBox() {
  const box = $("#ah-order-items");
  if (!orderPage.purchase || $("#ah-order-detail").hidden) return;
  const height = pageFitHeight(box);
  box.classList.toggle("order-items--fit", height !== null);
  box.style.maxHeight = height === null ? "" : `${height}px`;
}

window.addEventListener("resize", () => {
  if (ordersState.loaded) fitPurchaseTable();
  fitReceiptBox();
});

async function loadOrders() {
  message("#ah-orders-message", "");
  purchaseDetailCache.clear(); // de hovervensters (popover.js) halen de details opnieuw op
  const store = state.itemsStore;
  try {
    const imported = importedOrdersFetch(store);
    if (ordersAreJumbo()) {
      const orders = await jsonFetch("/api/jumbo/orders");
      const importedOrders = await imported;
      if (store !== state.itemsStore) return; // de winkel is intussen gewisseld
      Object.assign(ordersState, { orders: orders.orders, imported: importedOrders, ordersSyncedAt: orders.syncedAt, loaded: true });
      renderOrders();
      linkingNoticeRefresh(); // bij Jumbo blijft de melding van AH leeg (linkingRender)
      return;
    }
    const receipts = await jsonFetch("/api/ah/receipts");
    Object.assign(ordersState, { receipts: receipts.receipts, receiptsSyncedAt: receipts.syncedAt, loaded: true });
    const orders = await jsonFetch("/api/ah/orders");
    const importedOrders = await imported;
    if (store !== state.itemsStore) return;
    Object.assign(ordersState, { orders: orders.orders, imported: importedOrders, ordersSyncedAt: orders.syncedAt });
    renderOrders();
    linkingNoticeRefresh(); // de pauze van AH, het uurbudget en wat er nog te koppelen is (linking.js), boven de lijst
  } catch (error) {
    message("#ah-orders-message", `${t("orders.load_failed")} ${error.message}`);
  }
}

// ---- synchroniseren: eerst het mandje, dan de bestellingen, dan de kassabonnen; een deel dat mislukt stopt de rest niet
async function syncOrders() {
  const button = $("#sync-orders");
  setBusy(button, true);
  message("#ah-orders-message", "");
  const done = [];
  const failed = [];
  const run = async (what, path, report) => {
    try {
      report(await jsonFetch(path, { method: "POST" }));
    } catch (error) {
      failed.push(t("orders.sync_failed", { what, error: error.message }));
    }
  };
  if (ordersAreJumbo()) {
    await run(t("orders.part.orders"), "/api/jumbo/orders/sync", (data) => {
      done.push(tn("orders.sync_jumbo", data.orders));
      if (data.new) done.push(tn("orders.sync_jumbo_new", data.new));
      if (data.remaining && !data.detailError) done.push(tn("orders.sync_order_items", data.remaining));
      if (data.storeReceipts) done.push(tn("orders.jumbo_receipts", data.storeReceipts));
      if (data.detailError) failed.push(t("orders.jumbo_detail_error", { error: data.detailError }));
    });
  } else {
    await run(t("orders.part.basket"), "/api/ah/basket/sync", () => done.push(t("orders.sync_basket")));
    await run(t("orders.part.orders"), "/api/ah/orders/sync", (data) => {
      done.push(tn("orders.sync_orders", data.open));
      if (data.archived) done.push(tn("orders.sync_archived", data.archived));
      if (data.remaining) done.push(tn("orders.sync_order_items", data.remaining));
    });
  }
  if (!ordersAreJumbo()) {
    await run(t("orders.part.receipts"), "/api/ah/receipts/sync", (data) => {
      done.push(tn("orders.sync_receipts", data.new));
      if (data.remaining) done.push(tn("orders.sync_remaining", data.remaining));
    });
  }
  await loadOrders();
  const text = [done.length ? `${done.join(", ")}.` : "", ...failed].filter(Boolean).join(" ");
  message("#ah-orders-message", text, failed.length ? "error" : "success");
  setBusy(button, false);
}

// ---- zoeken op product: de server zoekt in de regels van alle bestellingen en bonnen (alle woorden moeten erin staan) en
// geeft elke gevonden regel met de aankoop waar hij op staat; de tabel filtert daarop. Een kwart seconde na de laatste toets,
// en een antwoord op een oudere zoekopdracht wordt genegeerd.
let purchaseTimer = null;
let purchaseSeq = 0;

async function searchPurchases() {
  const seq = ++purchaseSeq;
  const query = ordersState.query.trim();
  if (!query) {
    ordersState.hits = null;
    renderOrders();
    return;
  }
  try {
    const data = await jsonFetch(`${ordersApi()}/purchases/search?${new URLSearchParams({ q: query, limit: "all" })}`);
    if (seq !== purchaseSeq) return;
    ordersState.hits = data;
    renderOrders();
  } catch (error) {
    if (seq === purchaseSeq) message("#ah-orders-message", error.message);
  }
}

$("#ah-purchase-filter").addEventListener("input", (event) => {
  ordersState.query = event.target.value;
  ordersState.hits = null;
  purchaseSeq++;
  clearTimeout(purchaseTimer);
  if (!ordersState.query.trim()) {
    renderOrders();
    return;
  }
  $("#ah-purchase-status").textContent = t("orders.searching");
  purchaseTimer = setTimeout(searchPurchases, 250);
});

for (const [id, key] of [["ah-filter-orders", "orders"], ["ah-filter-receipts", "receipts"]]) {
  $(`#${id}`).addEventListener("change", (event) => {
    purchaseFilters[key] = event.target.checked;
    if (ordersState.loaded) renderOrders();
  });
}
for (const [id, key] of [["ah-filter-min", "min"], ["ah-filter-max", "max"]]) {
  $(`#${id}`).addEventListener("input", (event) => {
    purchaseFilters[key] = event.target.value;
    if (ordersState.loaded) renderOrders();
  });
}

// ---- een kassabon: als bonnetje zoals op papier (receiptPaper, de weergave Bon van de bestelpagina in order_views.js). De andere
// weergaven tonen de regels met de namen en foto's van de webwinkel, met de bonusregels eronder (receiptBonusSummary).
function receiptMethod(method) {
  const name = String(method || "").toUpperCase();
  return name === "PIN" ? t("orders.receipt.pin") : name === "CASH" ? t("orders.receipt.cash") : name;
}

function receiptPaper(receipt, words = []) {
  const money = (value) => (value === null || value === undefined ? "" : Number(value).toLocaleString(I18N.locale(), { minimumFractionDigits: 2, maximumFractionDigits: 2 })); // op papier staat geen euroteken
  const row = (cells, className = "") => `<div class="receipt__row ${className}">${cells.map((cell, index) => `<span class="receipt__c${index}">${escapeHtml(cell)}</span>`).join("")}</div>`;
  // De omschrijving linkt naar de prijsgeschiedenis van het webwinkelproduct, zodra het artikelnummer op de bon daaraan gekoppeld
  // is (ah_receipt_products; bij het openen zoekt resolveReceiptProducts de rest op). Zonder koppeling is het gewone tekst: zoeken
  // op een afgekorte bonnaam als "COTTAGE CH" vindt toch niets.
  const lines = receipt.items.map((line) =>
    row([line.quantity, line.name, line.quantity > 1 ? money(line.price) : "", money(line.amount)], `receipt__line${matchesQuery(line.name, words) ? " receipt__hit" : ""}`).replace(
      `<span class="receipt__c1">${escapeHtml(line.name)}</span>`,
      line.product
        ? `<span class="receipt__c1"><a href="${escapeHtml(priceHistoryPath("ah", line.product.title, line.product.id))}" data-route title="${escapeHtml(line.product.title)}">${escapeHtml(line.name)}</a></span>`
        : `<span class="receipt__c1">${escapeHtml(line.name)}</span>`
    )
  );
  const bonus = receipt.discounts.map((entry) => row(["", entry.name, "", entry.amount === null ? "" : `-${money(Math.abs(entry.amount))}`], "receipt__line receipt__bonus"));
  const units = receipt.items.reduce((sum, line) => sum + (line.quantity || 0), 0);
  const payments = receipt.payments.map((entry) => row([receiptMethod(entry.method), money(entry.amount)], "receipt__pay"));
  return `
    <div class="receipt" role="group" aria-label="${escapeHtml(t("orders.receipt_title", { date: receiptDay(receipt) || receipt.id }))}">
      <div class="receipt__head">
        <strong class="receipt__store">ALBERT HEIJN</strong>
        <span>${escapeHtml(receiptDay(receipt))}</span>
      </div>
      ${row([t("orders.receipt.qty"), t("orders.receipt.description"), t("orders.receipt.price"), t("orders.receipt.amount")], "receipt__cols")}
      ${lines.join("")}
      ${bonus.length ? `<div class="receipt__section">${escapeHtml(t("orders.receipt.bonus"))}</div>${bonus.join("")}` : ""}
      <div class="receipt__rule"></div>
      ${row([tn("orders.receipt.articles", units), money(receipt.total)], "receipt__total")}
      ${payments.length ? `<div class="receipt__rule"></div>${payments.join("")}` : ""}
    </div>`;
}

// De regel onder de titel van een bon: het aantal artikelen, het totaal en de betaalwijze (het bespaarde bedrag staat in de badge).
function receiptMeta(receipt) {
  const units = receipt.items.reduce((sum, line) => sum + (line.quantity || 0), 0);
  const methods = receipt.payments.map((entry) => receiptMethod(entry.method)).filter(Boolean).join(", ");
  return [tn("list.items", units), money(receipt.total), methods].filter(Boolean).join(" · ");
}

// Onder de producten van een bon (per categorie, kaarten, tabel): de bonusregels met hun bedrag, zoals op de bon.
function receiptBonusSummary(receipt) {
  const savings = receipt.discounts.reduce((sum, entry) => sum + Math.abs(entry.amount || 0), 0);
  const bonus = receipt.discounts
    .map((entry) => `<li>${escapeHtml(entry.name)}${entry.amount === null ? "" : ` <span>-${escapeHtml(money(Math.abs(entry.amount)))}</span>`}</li>`)
    .join("");
  return bonus ? `<ul class="receipt-summary"><li class="receipt-summary__title">${escapeHtml(t("orders.receipt.summary.bonus", { amount: money(savings) }))}</li>${bonus}</ul>` : "";
}

// ---- één bestelling of kassabon openen: de kop (titel en metaregel) hier, de badges, de balk en de producten op de bestelpagina
// (renderOrderPage in order_views.js), voor alle vier de soorten.
function showPurchaseOverview() {
  ordersState.open = null;
  // order_views.js en linking.js laden na dit bestand. Een winkelwissel tijdens het laden komt hier al langs.
  if (typeof orderPageClose === "function") orderPageClose();
  if (typeof linkingForget === "function") linkingForget();
  $("#ah-order-items").classList.remove("order-items--fit");
  $("#ah-order-items").style.maxHeight = "";
  $("#ah-order-detail").hidden = true;
  $("#ah-orders-view").hidden = false;
  if (typeof routerSync === "function") routerSync();
}

async function openPurchase(kind, id) {
  ordersState.open = { kind, id: String(id) };
  if (typeof routerSync === "function") routerSync();
  $("#ah-orders-view").hidden = true;
  $("#ah-order-detail").hidden = false;
  $("#ah-order-title").textContent = "";
  $("#ah-order-meta").textContent = "";
  $("#ah-order-items").innerHTML = "";
  $("#ah-order-items").classList.remove("order-items--fit");
  $("#ah-order-items").style.maxHeight = "";
  orderPageClose();
  linkingForget(); // de melding hoort bij de vorige bon
  try {
    if (kind === "order") {
      // De status (afgeleverd, of de tekst van AH bij een open bestelling) staat in de badge, niet nog eens in de metaregel.
      const order = await jsonFetch(`${ordersApi()}/orders/${encodeURIComponent(id)}`);
      $("#ah-order-title").textContent = dayLabel(order.deliveryDate) || t("orders.order_title", { id: order.id });
      $("#ah-order-meta").textContent = [
        t("orders.order_title", { id: order.id }),
        slotLabel(order.slotStart, order.slotEnd),
        money(order.total),
        t("orders.updated", { when: formatSyncTime(order.syncedAt) }),
      ].filter(Boolean).join(" · ");
      renderOrderPage(order, "order", ordersAreJumbo() ? "jumbo" : "ah");
    } else if (kind === "imported") {
      // Zoals een bestelling uit je account: tijdvak en de producten met foto, merk en inhoud (price_import.get_order).
      const store = ordersStore();
      const order = await jsonFetch(`/api/imported-orders/${store}/${encodeURIComponent(id)}`);
      const from = importedOrdersCover(ordersState.orders, [order]).covered.get(order.ref);
      $("#ah-order-title").textContent = importedOrderTitle(order);
      $("#ah-order-meta").textContent = importedOrderMeta(order, importedOrderSlot(order, from));
      renderOrderPage(order, "imported", store);
    } else {
      const receipt = await jsonFetch(`/api/ah/receipts/${encodeURIComponent(id)}`);
      $("#ah-order-title").textContent = t("orders.receipt_title", { date: receiptDay(receipt) || receipt.id });
      $("#ah-order-meta").textContent = receipt.items.length ? receiptMeta(receipt) : "";
      renderOrderPage(receipt, "receipt", "ah");
      linkingNoticeRefresh(receiptLinkCounts(receipt)); // hoeveel regels nog geen product hebben, met de knop Nu ophalen
      // De regels die nog bij geen webwinkelproduct horen opzoeken, en de foto's die nog ontbreken erbij halen.
      if (receipt.items.some((line) => (!line.product && line.productId) || (line.product && !line.product.image))) resolveReceiptProducts(receipt.id);
    }
    $("#ah-order-title").focus({ preventScroll: true });
  } catch (error) {
    $("#ah-order-items").innerHTML = "";
    showPurchaseOverview();
    message("#ah-orders-message", error.message);
  }
}

// De regels van een open bon die nog bij geen webwinkelproduct horen, opzoeken bij AH (zuinig: een paar zoekopdrachten per keer),
// de foto's van de gekoppelde regels die er nog geen hebben erbij halen, en de bon opnieuw tekenen met de links en foto's die
// erbij kwamen. Staat intussen iets anders open, dan blijft dat zo.
async function resolveReceiptProducts(id) {
  try {
    const receipt = await jsonFetch(`/api/ah/receipts/${encodeURIComponent(id)}/products`, { method: "POST" });
    const open = ordersState.open;
    if (!open || open.kind !== "receipt" || open.id !== String(id) || !receipt.items.length) return;
    renderOrderPage(receipt, "receipt", "ah");
    linkingNoticeRefresh(receiptLinkCounts(receipt));
  } catch {
    // De bon staat er al; een koppeling die niet lukt, krijgt een volgende keer een nieuwe kans.
  }
}

// Na een ronde op afroep voor deze bon (linking.js): de bon opnieuw ophalen en tekenen met de producten en foto's die erbij kwamen.
// Zonder de pagina eerst leeg te maken (zoals openPurchase doet) en zonder nieuwe zoekopdrachten bij AH: die deed de ronde al.
async function refreshReceipt(id) {
  try {
    const receipt = await jsonFetch(`/api/ah/receipts/${encodeURIComponent(id)}`);
    const open = ordersState.open;
    if (!open || open.kind !== "receipt" || open.id !== String(id)) return;
    $("#ah-order-meta").textContent = receipt.items.length ? receiptMeta(receipt) : "";
    renderOrderPage(receipt, "receipt", "ah");
    linkingNoticeRefresh(receiptLinkCounts(receipt));
  } catch {
    // De bon staat er al zoals hij was; de volgende keer openen haalt hem opnieuw op.
  }
}

$("#sync-orders").addEventListener("click", syncOrders);
$("#ah-order-back").addEventListener("click", showPurchaseOverview);
$("#ah-items-bestellingen").addEventListener("click", (event) => {
  const order = event.target.closest("[data-order-id]");
  const receipt = event.target.closest("[data-receipt-id]");
  const imported = event.target.closest("[data-imported-order]");
  const remove = event.target.closest("[data-imported-delete]");
  if (remove) deleteImportedOrder(ordersStore(), remove.dataset.importedDelete, remove);
  else if (order) openPurchase("order", order.dataset.orderId);
  else if (receipt) openPurchase("receipt", receipt.dataset.receiptId);
  else if (imported) openPurchase("imported", imported.dataset.importedOrder);
});
$("#orders-import").addEventListener("click", () => openPriceImport(ordersStore()));
// Na importeren of verwijderen (hier of in de Prijsgeschiedenis): de lijst van deze winkel opnieuw, als hij er al stond.
window.addEventListener("ms:imported-orders", (event) => {
  if (ordersState.loaded && event.detail?.store === ordersStore()) loadOrders();
});

// ---- Ingelezen oude bestellingen (zie price_import.py): bij elke winkel tussen de afgelopen bestellingen, ook bij Picnic en Crisp.
// Ze openen en doorzoeken als elke andere bestelling. De archiefdoos laat zien dat een bestelling uit je bevestigingsmails is
// ingelezen; met de prullenbak haalt de beheerder hem in zijn geheel weg. Importeren gaat met dezelfde dialoog als in de
// Prijsgeschiedenis (openPriceImport in offers_history.js).
const MDI_ARCHIVE_OUTLINE = "M20 21H4V10H6V19H18V10H20V21M3 3H21V9H3V3M9.5 11H14.5C14.78 11 15 11.22 15 11.5V13H9V11.5C9 11.22 9.22 11 9.5 11M5 5V7H19V5H5Z";
const MDI_DELETE_OUTLINE = "M6,19A2,2 0 0,0 8,21H16A2,2 0 0,0 18,19V7H6V19M8,9H16V19H8V9M15.5,4L14.5,3H9.5L8.5,4H5V6H19V4H15.5Z";

// Importeren en verwijderen kan alleen de beheerder; de server laat ook alleen de beheerder toe (/api/prices/import).
function priceImportIsOwner() {
  const auth = state.auth || {};
  return !auth.enabled || (Boolean(auth.authenticated) && auth.isOwner !== false);
}

// Mislukt dit, dan blijven de bestellingen uit je account gewoon staan, zonder de ingelezen.
async function importedOrdersFetch(store) {
  try {
    return (await jsonFetch(`/api/imported-orders/${encodeURIComponent(store)}`)).orders || [];
  } catch {
    return [];
  }
}

// #229: een bestelling uit je account zonder producten (Jumbo geeft de producten van oude bestellingen niet) en de ingelezen
// versie uit je mails zijn dezelfde bestelling. Die uit je account valt dan weg uit de lijst, zodat de bestelling er één keer staat,
// met de producten uit de mail. Zelfde regel als price_import.account_orders, maar omgekeerd: zelfde bestelnummer of zelfde dag
// (`dateOf` geeft de dag van een bestelling uit je account). Komen de producten later toch uit je account, dan verbergt de server
// de ingelezen versie en staat die uit je account er weer. Alleen in de lijst: de bestelling blijft bewaard, want de volgende
// synchronisatie zou haar toch weer neerzetten.
// Geeft { kept, covered }: de bestellingen die blijven, en per ingelezen bestelling (ref) de bestelling uit je account die erdoor
// wegviel, zodat haar status en tijdvak kunnen invullen wat de mail niet noemde.
function importedOrdersCover(orders, imported, dateOf = (order) => order.deliveryDate) {
  const byNumber = new Map();
  const byDay = new Map();
  for (const order of imported) {
    if (order.number) byNumber.set(String(order.number), order);
    const day = String(order.date || "").slice(0, 10);
    if (day && !byDay.has(day)) byDay.set(day, order);
  }
  const kept = [];
  const covered = new Map();
  for (const order of orders) {
    const empty = order.kind !== "open" && (order.itemsSynced === false || order.itemCount === 0);
    const match = empty ? byNumber.get(String(order.id)) || byDay.get(String(dateOf(order) || "").slice(0, 10)) : null;
    if (!match) kept.push(order);
    else if (!covered.has(match.ref)) covered.set(match.ref, order);
  }
  return { kept, covered };
}

// De ingelezen bestellingen tussen de bestellingen uit je account, de nieuwste eerst (Picnic en Crisp; AH en Jumbo: purchaseRows).
// `shape(order, from)` maakt van een ingelezen bestelling er een in de vorm van die winkel, met `from` de bestelling uit je account
// die erdoor wegviel (importedOrdersCover). Bij gelijke dagen blijft de volgorde van de server staan.
function importedOrdersMerge(orders, imported, dateOf = (order) => order.deliveryDate, shape = (order) => order) {
  const { kept, covered } = importedOrdersCover(orders, imported, dateOf);
  const day = (order) => String((order.imported ? order.date : dateOf(order)) || "").slice(0, 10);
  return [...kept, ...imported.map((order) => shape(order, covered.get(order.ref)))].sort((a, b) => day(b).localeCompare(day(a)));
}

// Status en tijdvak van een ingelezen bestelling zoals AH en Jumbo ze bij een eigen bestelling tonen ("Afgeleverd · 18:00-20:00").
// Een ingelezen bestelling is altijd afgeleverd: ze telt als gekocht, en een geannuleerde wordt niet ingelezen (de server geeft
// dan ook altijd "delivered", ook bij een import van vóór 3.5.0). Noemde de mail geen tijdvak, dan dat van de bestelling uit je
// account die erdoor wegviel (`from`).
function importedOrderSlot(order, from = null) {
  return slotLabel(order.slotStart, order.slotEnd) || (from ? slotLabel(from.slotStart, from.slotEnd) : "");
}

function importedOrderStatus(order, from = null) {
  return [t("orders.kind.delivered"), importedOrderSlot(order, from)].filter(Boolean).join(" · ");
}

function importedOrderTitle(order) {
  return dayLabel(order.date) || t("orders.order_title", { id: order.ref });
}

// De regel onder de titel van een geopende ingelezen bestelling, zoals bij een bestelling uit je account, met wanneer ze is ingelezen.
function importedOrderMeta(order, status = importedOrderStatus(order)) {
  return [
    order.number ? t("orders.order_title", { id: order.number }) : "",
    status,
    money(order.total),
    t("orders.imported.at", { when: formatSyncTime(order.importedAt) }),
  ].filter(Boolean).join(" · ");
}

// Een ingelezen bestelling bij Picnic en Crisp, met de regels van de kaart van die winkel (`lines`: status, tijdvak en bedrag,
// aantal), het archief-icoon voor de datum en de prullenbak. Bij AH en Jumbo staat ze als rij in de aankopentabel (purchaseRow).
function importedOrderCard(order, store, lines) {
  const title = importedOrderTitle(order);
  const badge = t("orders.imported.badge");
  const body = `
        <strong><span class="list-card__badge" role="img" aria-label="${escapeHtml(badge)}" title="${escapeHtml(badge)}">${mdiIcon(MDI_ARCHIVE_OUTLINE)}</span>${escapeHtml(title)}</strong>
        ${lines.map((line) => `<small>${escapeHtml(line)}</small>`).join("")}`;
  const open = `<button type="button" class="list-card__open" data-imported-order="${escapeHtml(order.ref)}">${body}</button>`;
  const remove = t("orders.imported.delete");
  return `
    <div class="list-card list-card--imported">
      ${open}
      ${priceImportIsOwner() ? `<button type="button" class="icon-button list-card__action" data-imported-delete="${escapeHtml(order.ref)}" data-title="${escapeHtml(title)}" aria-label="${escapeHtml(`${remove}: ${title}`)}" title="${escapeHtml(remove)}">${mdiIcon(MDI_DELETE_OUTLINE)}</button>` : ""}
    </div>`;
}

// Iedereen die de lijst van een winkel toont, luistert naar ms:imported-orders en laadt hem opnieuw (orders.js, picnic.js,
// crisp.js, en de Prijsgeschiedenis in offers_history.js).
function importedOrdersChanged(store) {
  window.dispatchEvent(new CustomEvent("ms:imported-orders", { detail: { store } }));
}

// De hele ingelezen bestelling weg, na een bevestiging. De melding komt pas na het opnieuw laden: dat wist eerst de oude melding.
async function deleteImportedOrder(store, ref, button) {
  if (!window.confirm(t("orders.imported.delete_confirm", { date: button.dataset.title || ref }))) return;
  const box = store === "picnic" || store === "crisp" ? `#${store}-orders-message` : "#ah-orders-message";
  button.disabled = true;
  try {
    await jsonFetch(`/api/prices/import?${new URLSearchParams({ store, ref })}`, { method: "DELETE" });
    importedOrdersChanged(store);
    message(box, t("orders.imported.deleted"), "success");
  } catch (error) {
    message(box, error.message);
    button.disabled = false;
  }
}

// Een ingelezen bestelling in de vorm van een bestelling uit je account van Picnic of Crisp (#229), zodat ze door dezelfde code
// gaat: de lijstkaart, het detail en Alles in mandje. `status` is de status van die winkel voor een afgeleverde bestelling. De regels
// krijgen het product waar ze bij horen als `id` (Alles in mandje) en het bedrag van de hele regel als `price`, zoals Picnic en
// Crisp het bewaren. Noemde de mail geen tijdvak, dan dat van de bestelling uit je account die erdoor wegviel (`from`).
function importedStoreOrder(order, from, status) {
  const items = (order.items || []).map((item) => ({ ...item, id: item.productId || null, price: item.amount }));
  return {
    ...order,
    items,
    status, // altijd afgeleverd, zie importedOrderStatus
    windowStart: order.slotStart || from?.windowStart || null,
    windowEnd: order.slotEnd || (order.slotStart ? null : from?.windowEnd) || null,
    itemCount: order.units ?? order.itemCount, // Picnic en Crisp tellen stuks, geen regels
    itemsSynced: true,
  };
}

// Bij Alles in mandje van een ingelezen bestelling: de regels uit de mail die bij geen product van de winkel horen, gaan er niet in.
function importedUnlinkedNote(order) {
  const unlinked = order.imported ? order.items.filter((item) => !item.id).length : 0;
  return unlinked ? tn("orders.imported.unlinked", unlinked) : "";
}

// ---- verkenner (alleen de beheerder): welke AH-routes werken, en met welke velden?
let exploreReport = null;

async function runExplore() {
  const button = $("#explore-run");
  setBusy(button, true);
  message("#explore-message", "");
  try {
    exploreReport = await jsonFetch("/api/ah/explore", { method: "POST" });
    $("#explore-summary").textContent = t("explore.summary", { ok: exploreReport.ok, total: exploreReport.probes.length });
    $("#explore-output").textContent = JSON.stringify(exploreReport, null, 2);
    $("#explore-report").hidden = false;
    if (!exploreReport.linked) message("#explore-message", t("explore.not_linked"));
  } catch (error) {
    message("#explore-message", error.message);
  } finally {
    setBusy(button, false);
  }
}

$("#explore-run").addEventListener("click", runExplore);
$("#explore-copy").addEventListener("click", async (event) => {
  const text = $("#explore-output").textContent;
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const range = document.createRange();
    range.selectNodeContents($("#explore-output"));
    getSelection().removeAllRanges();
    getSelection().addRange(range);
    document.execCommand("copy");
  }
  event.currentTarget.textContent = t("users.copied");
  setTimeout(() => (event.currentTarget.textContent = t("users.copy")), 2000);
});

// Bij een andere winkel in de tab Winkels: schone lijst, en opnieuw laden als dit tabblad openstaat.
function resetOrders() {
  Object.assign(ordersState, { basket: null, orders: [], imported: [], receipts: [], ordersSyncedAt: null, receiptsSyncedAt: null, query: "", hits: null, loaded: false });
  purchaseSeq++;
  $("#ah-purchase-filter").value = "";
  showPurchaseOverview();
  message("#ah-orders-message", "");
  if (document.querySelector("#ah-items-bestellingen.is-active")) loadOrders();
}

window.addEventListener("ms:languagechange", () => {
  if (ordersState.loaded) renderOrders();
  if (exploreReport) $("#explore-summary").textContent = t("explore.summary", { ok: exploreReport.ok, total: exploreReport.probes.length });
});
