// Bestellingen, bestelmandje en kassabonnen: lezen uit de lokale kopie, synchroniseren bij AH.
// Laadt na app.js en gebruikt daar de hulpfuncties ($, message, jsonFetch, t, tn, escapeHtml, formatPrice, ...).

// Bestelmandje en bestellingen wachten tot AH ze weer levert; de code staat klaar. Op false worden ze weer getoond en gesynchroniseerd.
// Geldt alleen voor AH: Jumbo-bestellingen werken wel.
const ORDERS_COMING_SOON = true;

// De winkel komt uit de keuze in de tab Winkels (state.itemsStore in app.js): "ah" of "jumbo".
function ordersApi() {
  return `/api/${state.itemsStore}`;
}

function ordersAreJumbo() {
  return state.itemsStore === "jumbo";
}

function ordersSoon() {
  return !ordersAreJumbo() && ORDERS_COMING_SOON;
}

const ordersState = { basket: null, orders: [], receipts: [], ordersSyncedAt: null, receiptsSyncedAt: null, query: "", loaded: false };

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

function purchaseItem(item) {
  const meta = [item.brand, item.unitSize].filter(Boolean).join(" · ");
  return `
    <article class="product-card">
      ${item.image ? `<img src="${escapeHtml(item.image)}" alt="" loading="lazy">` : ""}
      <div>
        <strong>${escapeHtml(item.quantity)}x ${escapeHtml(item.title || item.name || "")}</strong>
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
  target.innerHTML = `<p>${escapeHtml(summary.join(" · "))}</p><div class="product-grid">${basket.items.map(purchaseItem).join("")}</div>`;
}

function renderOrderList() {
  const target = $("#ah-order-list");
  if (!ordersState.orders.length) {
    const none = ordersAreJumbo() ? t("orders.none_jumbo") : t("orders.none");
    target.innerHTML = `<p class="muted">${escapeHtml(ordersState.ordersSyncedAt ? none : t("orders.never_synced"))}</p>`;
    return;
  }
  target.innerHTML = ordersState.orders
    .map((order) => {
      const lines = [
        order.kind === "open" ? order.stateText || t("orders.kind.open") : t("orders.kind.delivered"),
        [slotLabel(order.slotStart, order.slotEnd), money(order.total)].filter(Boolean).join(" · "),
        order.itemsSynced ? tn("list.items", order.itemCount) : t("orders.items_pending"),
      ].filter(Boolean);
      return `
        <div class="list-card">
          <button type="button" class="list-card__open" data-order-id="${escapeHtml(order.id)}">
            <strong>${escapeHtml(dayLabel(order.deliveryDate) || t("orders.order_title", { id: order.id }))}</strong>
            ${lines.map((line) => `<small>${escapeHtml(line)}</small>`).join("")}
          </button>
        </div>`;
    })
    .join("");
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
  target.innerHTML = ordersState.receipts
    .map(
      (receipt) => `
        <div class="credential-row">
          <div>
            <strong>${escapeHtml(receiptDay(receipt) || receipt.id)}</strong>
            <small>${escapeHtml([money(receipt.total), receipt.detailsSynced ? tn("list.items", receipt.itemCount) : t("orders.items_pending")].filter(Boolean).join(" · "))}</small>
          </div>
          <button class="button button--secondary" type="button" data-receipt-id="${escapeHtml(receipt.id)}">${escapeHtml(t("orders.view"))}</button>
        </div>`
    )
    .join("");
}

function renderOrders() {
  const jumbo = ordersAreJumbo();
  const soon = ordersSoon();
  $("#ah-orders-intro").hidden = jumbo;
  $("#jumbo-orders-intro").hidden = !jumbo;
  $("#ah-basket-block").hidden = jumbo; // Jumbo's mandje staat bij het plannen; hier alleen bestellingen
  $("#ah-receipts-block").hidden = jumbo; // winkelbonnen van Jumbo kan MealieShopper nog niet tonen
  $("#ah-orders-section").hidden = soon;
  $("#ah-orders-soon").hidden = !soon;
  if (!soon) {
    if (!jumbo) renderBasket();
    renderOrderList();
  }
  if (!jumbo) renderReceiptList();
  if (ordersState.query.trim()) renderPurchaseResults(ordersState.hits);
}

async function loadOrders() {
  message("#ah-orders-message", "");
  const store = state.itemsStore;
  try {
    if (ordersAreJumbo()) {
      const orders = await jsonFetch("/api/jumbo/orders");
      if (store !== state.itemsStore) return; // de winkel is intussen gewisseld
      Object.assign(ordersState, { orders: orders.orders, ordersSyncedAt: orders.syncedAt, loaded: true });
      renderOrders();
      return;
    }
    const receipts = await jsonFetch("/api/ah/receipts");
    Object.assign(ordersState, { receipts: receipts.receipts, receiptsSyncedAt: receipts.syncedAt, loaded: true });
    if (!ORDERS_COMING_SOON) {
      const [basket, orders] = await Promise.all([jsonFetch("/api/ah/basket"), jsonFetch("/api/ah/orders")]);
      Object.assign(ordersState, { basket, orders: orders.orders, ordersSyncedAt: orders.syncedAt });
    }
    if (store !== state.itemsStore) return;
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
  } else if (!ORDERS_COMING_SOON) {
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
  const orderHits = ordersSoon() ? [] : data.occurrences.filter((entry) => entry.source === "bestelling");
  const occurrences = orderHits
    .map(
      (entry) => `<div class="credential-row"><div><strong>${escapeHtml(entry.product)}</strong>
        <small>${escapeHtml([dayLabel(entry.date), t("orders.source.order"), entry.quantity ? `${entry.quantity}x` : "", money(entry.amount)].filter(Boolean).join(" · "))}</small></div>
        <button class="button button--secondary" type="button" data-order-id="${escapeHtml(entry.id)}">${escapeHtml(t("orders.view"))}</button></div>`
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
  const lines = receipt.items.map((line) => row([line.quantity, line.name, line.quantity > 1 ? money(line.price) : "", money(line.amount)], `receipt__line${matchesQuery(line.name, words) ? " receipt__hit" : ""}`));
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
  $("#ah-order-detail").hidden = true;
  $("#ah-orders-view").hidden = false;
}

async function openPurchase(kind, id) {
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
        ? `<div class="product-grid">${order.items.map(purchaseItem).join("")}</div>`
        : `<p class="muted">${escapeHtml(t("orders.items_missing"))}</p>`;
    } else {
      const receipt = await jsonFetch(`/api/ah/receipts/${encodeURIComponent(id)}`);
      $("#ah-order-title").textContent = t("orders.receipt_title", { date: receiptDay(receipt) || receipt.id });
      $("#ah-order-items").innerHTML = receipt.items.length
        ? receiptPaper(receipt, foldText(ordersState.query).split(/\s+/).filter(Boolean))
        : `<p class="muted">${escapeHtml(t("orders.items_missing"))}</p>`;
    }
    $("#ah-order-title").focus({ preventScroll: true });
  } catch (error) {
    $("#ah-order-items").innerHTML = "";
    showPurchaseOverview();
    message("#ah-orders-message", error.message);
  }
}

$("#sync-orders").addEventListener("click", syncOrders);
$("#ah-order-back").addEventListener("click", showPurchaseOverview);
$("#ah-items-bestellingen").addEventListener("click", (event) => {
  const order = event.target.closest("[data-order-id]");
  const receipt = event.target.closest("[data-receipt-id]");
  if (order) openPurchase("order", order.dataset.orderId);
  else if (receipt) openPurchase("receipt", receipt.dataset.receiptId);
});

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
  Object.assign(ordersState, { basket: null, orders: [], receipts: [], ordersSyncedAt: null, receiptsSyncedAt: null, query: "", hits: null, loaded: false });
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
