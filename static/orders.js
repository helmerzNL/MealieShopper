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

// Een product uit een bestelling of het mandje; de naam linkt naar zijn prijsgeschiedenis.
function purchaseItem(item, store = "ah") {
  const meta = [item.brand, item.unitSize].filter(Boolean).join(" · ");
  const title = item.title || item.name || "";
  const name = title ? `<a href="${escapeHtml(priceHistoryPath(store, title, item.productId || ""))}" data-route>${escapeHtml(title)}</a>` : "";
  return `
    <article class="product-card">
      ${item.image ? `<img src="${escapeHtml(item.image)}" alt="" loading="lazy">` : ""}
      <div>
        <strong>${escapeHtml(item.quantity)}x ${name}</strong>
        ${meta ? `<small>${escapeHtml(meta)}</small>` : ""}
        ${item.taxonomy ? `<small>${escapeHtml(item.taxonomy)}</small>` : ""}
        ${item.bonus ? `<small class="bonus">${escapeHtml(t("list.bonus"))}</small>` : ""}
        ${item.amount ? `<small>${escapeHtml(money(item.amount))}</small>` : ""}
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
  target.innerHTML = `<p>${escapeHtml(summary.join(" · "))}</p><div class="product-grid">${basket.items.map((item) => purchaseItem(item)).join("")}</div>`;
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

function orderCard(order) {
  if (order.imported) return importedOrderCard(order, ordersStore());
  const lines = [
    order.kind === "open" ? order.stateText || t("orders.kind.open") : t("orders.kind.delivered"),
    [slotLabel(order.slotStart, order.slotEnd), money(order.total)].filter(Boolean).join(" · "),
    order.itemsSynced ? tn("list.items", order.itemCount) : t("orders.items_pending"),
  ].filter(Boolean);
  return `
    <div class="list-card">
      <a class="list-card__open" data-order-id="${escapeHtml(order.id)}" href="${escapeHtml(storesPath(ordersStore(), "orders", order.id))}" data-nav>
        <strong>${escapeHtml(dayLabel(order.deliveryDate) || t("orders.order_title", { id: order.id }))}</strong>
        ${lines.map((line) => `<small>${escapeHtml(line)}</small>`).join("")}
      </a>
    </div>`;
}

// Aankomende bestellingen (nog open, nieuwste eerst) en afgelopen bestellingen (geleverd, nieuwste eerst), met de ingelezen oude
// bestellingen ertussen op hun datum
function renderOrderList() {
  const upcoming = ordersState.orders.filter((order) => order.kind === "open").sort((a, b) => String(a.deliveryDate || "").localeCompare(String(b.deliveryDate || "")));
  const past = importedOrdersMerge(ordersState.orders.filter((order) => order.kind !== "open"), ordersState.imported);
  const never = !ordersState.ordersSyncedAt && !ordersState.imported.length;
  const empty = (text) => `<p class="muted">${escapeHtml(never ? t("orders.never_synced") : text)}</p>`;
  $("#ah-order-upcoming").innerHTML = upcoming.length ? upcoming.map(orderCard).join("") : empty(t("orders.upcoming_none"));
  $("#ah-order-list").innerHTML = past.length ? past.map(orderCard).join("") : empty(ordersAreJumbo() ? t("orders.none_jumbo") : t("orders.none"));
}

// Zonder hoofdletters en accenten, zoals de server zoekt
function foldText(value) {
  return String(value || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
}

function matchesQuery(name, words) {
  const folded = foldText(name);
  return words.length > 0 && words.every((word) => folded.includes(word));
}

function receiptDay(receipt) {
  const date = new Date(receipt.dateTime);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleString(I18N.locale(), { dateStyle: "medium", timeStyle: "short" });
}

function renderReceiptList() {
  const target = $("#ah-receipt-list");
  if (!ordersState.receipts.length) {
    target.innerHTML = `<p class="muted">${escapeHtml(ordersState.receiptsSyncedAt ? t("orders.receipts_none") : t("orders.never_synced"))}</p>`;
    return;
  }
  // Een regel per bon, de hele regel is de link: zo passen er veel op een scherm en scrolt de lijst in zijn eigen vak.
  target.innerHTML = ordersState.receipts
    .map(
      (receipt) => `
        <a class="receipt-row" data-receipt-id="${escapeHtml(receipt.id)}" href="${escapeHtml(storesPath("ah", "receipts", receipt.id))}" data-nav>
          <strong>${escapeHtml(receiptDay(receipt) || receipt.id)}</strong>
          <span>${escapeHtml(money(receipt.total))}</span>
          <small>${escapeHtml(receipt.detailsSynced ? tn("list.items", receipt.itemCount) : t("orders.items_pending"))}</small>
        </a>`
    )
    .join("");
}

function renderOrders() {
  const jumbo = ordersAreJumbo();
  $("#ah-orders-intro").hidden = jumbo;
  $("#jumbo-orders-intro").hidden = !jumbo;
  $("#ah-receipts-block").hidden = jumbo; // winkelbonnen van Jumbo kan MealieShopper nog niet tonen
  renderOrderList();
  if (!jumbo) renderReceiptList();
  if (ordersState.query.trim()) renderPurchaseResults(ordersState.hits);
}

async function loadOrders() {
  message("#ah-orders-message", "");
  const store = state.itemsStore;
  try {
    const imported = importedOrdersFetch(store);
    if (ordersAreJumbo()) {
      const orders = await jsonFetch("/api/jumbo/orders");
      const importedOrders = await imported;
      if (store !== state.itemsStore) return; // de winkel is intussen gewisseld
      Object.assign(ordersState, { orders: orders.orders, imported: importedOrders, ordersSyncedAt: orders.syncedAt, loaded: true });
      renderOrders();
      return;
    }
    const receipts = await jsonFetch("/api/ah/receipts");
    Object.assign(ordersState, { receipts: receipts.receipts, receiptsSyncedAt: receipts.syncedAt, loaded: true });
    const orders = await jsonFetch("/api/ah/orders");
    const importedOrders = await imported;
    if (store !== state.itemsStore) return;
    Object.assign(ordersState, { orders: orders.orders, imported: importedOrders, ordersSyncedAt: orders.syncedAt });
    renderOrders();
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

// ---- zoeken in alles wat je kocht
let purchaseTimer = null;
let purchaseSeq = 0;

function renderPurchaseResults(data) {
  const box = $("#ah-purchase-results");
  const query = ordersState.query.trim();
  box.hidden = !query;
  $("#ah-orders-main").hidden = Boolean(query);
  if (!query) return;
  if (!data) {
    box.innerHTML = "";
    return;
  }
  if (!data.products.length && !data.receiptHits?.total) {
    box.innerHTML = `<p class="muted">${th("orders.search_none", { query })}</p>`;
    return;
  }
  const products = data.products
    .map(
      (product) => `<div class="credential-row"><div><strong>${escapeHtml(product.product)}</strong>
        <small>${escapeHtml([t("orders.last_bought", { date: dayLabel(product.lastDate) }), tn("orders.times", product.times), t("orders.quantity", { count: I18N.formatNumber(product.totalQuantity) })].join(" · "))}</small></div></div>`
    )
    .join("");
  const orderHits = data.occurrences.filter((entry) => entry.source === "bestelling");
  const occurrences = orderHits
    .map(
      (entry) => `<div class="credential-row"><div><strong>${escapeHtml(entry.product)}</strong>
        <small>${escapeHtml([dayLabel(entry.date), t("orders.source.order"), entry.quantity ? `${entry.quantity}x` : "", money(entry.amount)].filter(Boolean).join(" · "))}</small></div>
        <button class="button button--secondary" type="button" ${entry.imported ? "data-imported-order" : "data-order-id"}="${escapeHtml(entry.id)}">${escapeHtml(t("orders.view"))}</button></div>`
    )
    .join("");
  const found = data.receiptHits || { receipts: [], total: 0, unsearched: 0 };
  const receipts = found.receipts
    .map(
      (receipt) => `<div class="credential-row">
        <div>
          <strong>${escapeHtml(receiptDay(receipt) || receipt.id)}</strong>
          <small>${escapeHtml(money(receipt.total))}</small>
          <ul class="receipt-hits">${receipt.matches.map((line) => `<li>${escapeHtml(line.quantity)}x ${escapeHtml(line.name)}${line.amount === null ? "" : ` · ${escapeHtml(money(line.amount))}`}</li>`).join("")}</ul>
        </div>
        <button class="button button--secondary" type="button" data-receipt-id="${escapeHtml(receipt.id)}">${escapeHtml(t("orders.view"))}</button></div>`
    )
    .join("");
  box.innerHTML = `<p class="muted">${escapeHtml(t("orders.search_summary", { products: data.products.length, total: data.total }))}</p>
    <div class="credential-list">${products}</div>
    ${found.total ? `<h3>${escapeHtml(tn("orders.search_receipts", found.total))}</h3><div class="credential-list">${receipts}</div>` : ""}
    ${found.unsearched ? `<p class="muted">${escapeHtml(tn("orders.search_unsearched", found.unsearched))}</p>` : ""}
    ${occurrences ? `<h3>${escapeHtml(t("orders.occurrences"))}</h3><div class="credential-list">${occurrences}</div>` : ""}`;
}

async function searchPurchases() {
  const seq = ++purchaseSeq;
  const query = ordersState.query.trim();
  if (!query) {
    ordersState.hits = null;
    renderPurchaseResults(null);
    return;
  }
  try {
    const q = encodeURIComponent(query);
    const [data, receiptHits] = await Promise.all([
      jsonFetch(`${ordersApi()}/purchases/search?q=${q}`),
      ordersAreJumbo() ? { receipts: [], total: 0, unsearched: 0 } : jsonFetch("/api/ah/receipts/search?q=" + q),
    ]);
    if (seq !== purchaseSeq) return;
    ordersState.hits = { ...data, receiptHits };
    renderPurchaseResults(ordersState.hits);
  } catch (error) {
    if (seq === purchaseSeq) message("#ah-orders-message", error.message);
  }
}

$("#ah-purchase-filter").addEventListener("input", (event) => {
  ordersState.query = event.target.value;
  ordersState.hits = null;
  purchaseSeq++;
  renderPurchaseResults(null);
  clearTimeout(purchaseTimer);
  purchaseTimer = setTimeout(searchPurchases, 250);
});

// ---- een kassabon zoals op papier: kolommen, bonusregels, totaal en betaalwijze
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

// ---- één bestelling of kassabon openen
function showPurchaseOverview() {
  ordersState.open = null;
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
  try {
    if (kind === "order") {
      const order = await jsonFetch(`${ordersApi()}/orders/${encodeURIComponent(id)}`);
      $("#ah-order-title").textContent = dayLabel(order.deliveryDate) || t("orders.order_title", { id: order.id });
      $("#ah-order-meta").textContent = [
        t("orders.order_title", { id: order.id }),
        order.kind === "open" ? order.stateText || t("orders.kind.open") : t("orders.kind.delivered"),
        slotLabel(order.slotStart, order.slotEnd),
        money(order.total),
        t("orders.updated", { when: formatSyncTime(order.syncedAt) }),
      ].filter(Boolean).join(" · ");
      $("#ah-order-items").innerHTML = order.items.length
        ? `<div class="product-grid">${order.items.map((item) => purchaseItem(item, ordersAreJumbo() ? "jumbo" : "ah")).join("")}</div>`
        : `<p class="muted">${escapeHtml(t("orders.items_missing"))}</p>`;
    } else if (kind === "imported") {
      const store = ordersStore();
      const order = await jsonFetch(`/api/imported-orders/${store}/${encodeURIComponent(id)}`);
      $("#ah-order-title").textContent = importedOrderTitle(order);
      $("#ah-order-meta").textContent = importedOrderMeta(order);
      $("#ah-order-items").innerHTML = `<div class="product-grid">${importedOrderItems(order, store)}</div>`; // altijd met producten
    } else {
      const receipt = await jsonFetch(`/api/ah/receipts/${encodeURIComponent(id)}`);
      const words = foldText(ordersState.query).split(/\s+/).filter(Boolean);
      $("#ah-order-title").textContent = t("orders.receipt_title", { date: receiptDay(receipt) || receipt.id });
      $("#ah-order-items").innerHTML = receipt.items.length ? receiptPaper(receipt, words) : `<p class="muted">${escapeHtml(t("orders.items_missing"))}</p>`;
      if (receipt.items.some((line) => !line.product && line.productId)) resolveReceiptProducts(receipt.id, words);
    }
    $("#ah-order-title").focus({ preventScroll: true });
  } catch (error) {
    $("#ah-order-items").innerHTML = "";
    showPurchaseOverview();
    message("#ah-orders-message", error.message);
  }
}

// De regels van een open bon die nog bij geen webwinkelproduct horen, opzoeken bij AH (zuinig: een paar zoekopdrachten per keer)
// en de bon opnieuw tekenen met de links die erbij kwamen. Staat intussen iets anders open, dan blijft dat zo.
async function resolveReceiptProducts(id, words) {
  try {
    const receipt = await jsonFetch(`/api/ah/receipts/${encodeURIComponent(id)}/products`, { method: "POST" });
    const open = ordersState.open;
    if (!open || open.kind !== "receipt" || open.id !== String(id) || !receipt.items.length) return;
    $("#ah-order-items").innerHTML = receiptPaper(receipt, words);
  } catch {
    // De bon staat er al; een koppeling die niet lukt, krijgt een volgende keer een nieuwe kans.
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

// De ingelezen bestellingen tussen de bestellingen uit je account, de nieuwste eerst. `dateOf` geeft de dag van een bestelling uit
// je account; bij gelijke dagen blijft de volgorde van de server staan.
function importedOrdersMerge(orders, imported, dateOf = (order) => order.deliveryDate) {
  const day = (order) => String((order.imported ? order.date : dateOf(order)) || "").slice(0, 10);
  return [...orders, ...imported].sort((a, b) => day(b).localeCompare(day(a)));
}

function importedOrderTitle(order) {
  return dayLabel(order.date) || t("orders.order_title", { id: order.ref });
}

function importedOrderMeta(order) {
  return [
    order.number ? t("orders.order_title", { id: order.number }) : "",
    t("orders.imported.at", { when: formatSyncTime(order.importedAt) }),
    money(order.total),
    tn("list.items", order.itemCount),
  ].filter(Boolean).join(" · ");
}

function importedOrderItems(order, store) {
  return order.items.map((item) => purchaseItem(item, store)).join("");
}

// Een kaart zoals die van een bestelling uit je account. Bij AH en Jumbo heeft de bestelling een eigen adres
// (/stores/ah/imported/<ref>, zie router.js); Picnic en Crisp openen hem in hun eigen tabblad.
function importedOrderCard(order, store) {
  const title = importedOrderTitle(order);
  const badge = t("orders.imported.badge");
  const lines = [[t("orders.kind.imported"), money(order.total)].filter(Boolean).join(" · "), tn("list.items", order.itemCount)];
  const body = `
        <strong><span class="list-card__badge" role="img" aria-label="${escapeHtml(badge)}" title="${escapeHtml(badge)}">${mdiIcon(MDI_ARCHIVE_OUTLINE)}</span>${escapeHtml(title)}</strong>
        ${lines.map((line) => `<small>${escapeHtml(line)}</small>`).join("")}`;
  const open = store === "ah" || store === "jumbo"
    ? `<a class="list-card__open" data-imported-order="${escapeHtml(order.ref)}" href="${escapeHtml(storesPath(store, "imported", order.ref))}" data-nav>${body}</a>`
    : `<button type="button" class="list-card__open" data-imported-order="${escapeHtml(order.ref)}">${body}</button>`;
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

// Picnic en Crisp: dezelfde opbouw (#picnic-order-view, #crisp-order-view), elk met een eigen teller tegen een trage oude aanvraag.
async function openImportedStoreOrder(store, ref, current) {
  $(`#${store}-orders-list-view`).hidden = true;
  $(`#${store}-order-view`).hidden = false;
  $(`#${store}-order-title`).textContent = t("orders.order_title", { id: ref });
  $(`#${store}-order-meta`).textContent = "";
  $(`#${store}-order-items`).innerHTML = "";
  $(`#${store}-order-add-all`).hidden = true; // een regel uit een mail heeft geen product van de winkel
  message(`#${store}-order-message`, "");
  try {
    const order = await jsonFetch(`/api/imported-orders/${store}/${encodeURIComponent(ref)}`);
    if (!current()) return;
    $(`#${store}-order-title`).textContent = importedOrderTitle(order);
    $(`#${store}-order-meta`).textContent = importedOrderMeta(order);
    $(`#${store}-order-items`).innerHTML = importedOrderItems(order, store);
    $(`#${store}-order-title`).focus();
  } catch (error) {
    if (current()) message(`#${store}-order-message`, error.message);
  }
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
  $("#ah-purchase-results").hidden = true;
  $("#ah-orders-main").hidden = false;
  showPurchaseOverview();
  message("#ah-orders-message", "");
  if (document.querySelector("#ah-items-bestellingen.is-active")) loadOrders();
}

window.addEventListener("ms:languagechange", () => {
  if (ordersState.loaded) renderOrders();
  if (exploreReport) $("#explore-summary").textContent = t("explore.summary", { ok: exploreReport.ok, total: exploreReport.probes.length });
});
