// Picnic: koppelen (met een code per sms of e-mail) en de keuze van de supermarkt in de planner.
// Laadt na app.js en gebruikt daar de hulpfuncties van ($, message, jsonFetch, setBusy, t, ...).

const picnicState = { connected: false, codePending: false, report: null, slots: [] };
const picnicRecipes = { source: "saved", recipes: [], query: "", loaded: false };
// `imported`: de ingelezen oude bestellingen (zie price_import.py), die bij de afgelopen bestellingen staan.
const picnicOrders = { upcoming: [], past: [], imported: [], syncedAt: null, upcomingFailed: false, detail: null, loaded: false, run: 0 };
const picnicFavorites = { products: [], total: 0, filtered: true, pending: 0, run: 0, query: "", loaded: false };

function postJson(url, body = {}) {
  return jsonFetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

// ---- Status en koppelen (beheerder)
function renderPicnicStatus() {
  $("#picnic-code-box").hidden = !picnicState.codePending;
  $("#picnic-unlink").hidden = !picnicState.connected;
  $("#picnic-check").hidden = !picnicState.connected;
  renderLinkStatus("#picnic-status", STORES.picnic.name, picnicState.connected ? "linked" : "unlinked");
  applyStoreFold("picnic", picnicState.connected);
}

async function refreshPicnicStatus() {
  try {
    const data = await jsonFetch("/api/picnic/auth/status");
    picnicState.connected = Boolean(data.connected);
    state.linkedStores.picnic = picnicState.connected;
    picnicState.codePending = Boolean(data.twoFactorPending) && !data.connected;
    renderPicnicStatus();
  } catch (error) {
    message("#picnic-status", error.message);
  }
  renderStoreChoice();
}

async function checkPicnicConnection() {
  message("#picnic-status", t("ah.checking"), "success");
  try {
    const data = await jsonFetch("/api/picnic/auth/status?verify=1");
    picnicState.connected = Boolean(data.connected);
    if (data.verified) renderLinkStatus("#picnic-status", STORES.picnic.name, "working");
    else if (data.connected) message("#picnic-status", t("picnic.broken", { error: data.error || "" }));
    else renderLinkStatus("#picnic-status", STORES.picnic.name, "unlinked");
  } catch (error) {
    message("#picnic-status", error.message);
  }
}

async function requestPicnicCode(channel) {
  message("#picnic-code-message", "");
  try {
    await postJson("/api/picnic/auth/2fa/request", { channel });
    message("#picnic-code-message", t("picnic.code_sent"), "success");
  } catch (error) {
    message("#picnic-code-message", error.message);
  }
}

async function loginToPicnic(event) {
  event.preventDefault();
  const button = $("#picnic-login");
  setBusy(button, true, t("picnic.logging_in"));
  message("#picnic-message", "");
  try {
    const data = await postJson("/api/picnic/auth", {
      email: $("#picnic-email").value.trim(),
      password: $("#picnic-password").value,
    });
    $("#picnic-password").value = "";
    picnicState.connected = Boolean(data.connected);
    state.linkedStores.picnic = picnicState.connected;
    picnicState.codePending = Boolean(data.twoFactorRequired);
    renderPicnicStatus();
    renderStoreChoice();
    if (picnicState.codePending) await requestPicnicCode("SMS");
  } catch (error) {
    message("#picnic-message", error.message);
  } finally {
    setBusy(button, false, t("picnic.logging_in"));
  }
}

async function confirmPicnicCode(event) {
  event.preventDefault();
  const button = $("#picnic-verify");
  setBusy(button, true, t("picnic.logging_in"));
  try {
    await postJson("/api/picnic/auth/2fa/verify", { code: $("#picnic-code").value.trim() });
    $("#picnic-code").value = "";
    picnicState.connected = true;
    state.linkedStores.picnic = true;
    picnicState.codePending = false;
    message("#picnic-code-message", "");
    renderPicnicStatus();
    renderStoreChoice();
  } catch (error) {
    message("#picnic-code-message", error.message);
  } finally {
    setBusy(button, false, t("picnic.logging_in"));
  }
}

async function unlinkPicnic() {
  try {
    await postJson("/api/picnic/auth/logout");
    picnicState.connected = false;
    state.linkedStores.picnic = false;
    picnicState.codePending = false;
    renderPicnicStatus();
    renderStoreChoice();
  } catch (error) {
    message("#picnic-status", error.message);
  }
}

// ---- Verkenner (alleen de beheerder)
async function runPicnicExplore() {
  const button = $("#picnic-explore-run");
  setBusy(button, true);
  message("#picnic-explore-message", "");
  try {
    picnicState.report = await postJson("/api/picnic/explore");
    renderPicnicExploreSummary();
    $("#picnic-explore-output").textContent = JSON.stringify(picnicState.report, null, 2);
    $("#picnic-explore-report").hidden = false;
    if (!picnicState.report.linked) message("#picnic-explore-message", t("picnic.explore_not_linked"));
  } catch (error) {
    message("#picnic-explore-message", error.message);
  } finally {
    setBusy(button, false);
  }
}

function renderPicnicExploreSummary() {
  const report = picnicState.report;
  if (report) $("#picnic-explore-summary").textContent = t("explore.summary", { ok: report.ok, total: report.probes.length });
}

// ---- Bezorgmomenten onder het mandje (alleen lezen: kiezen doe je in de Picnic-app)
function formatSlot(slot) {
  const start = new Date(slot.windowStart);
  const end = new Date(slot.windowEnd);
  if (Number.isNaN(start.getTime())) return "";
  const day = start.toLocaleDateString(I18N.locale(), { weekday: "short", day: "numeric", month: "short" });
  const time = (date) => date.toLocaleTimeString(I18N.locale(), { hour: "2-digit", minute: "2-digit" });
  return Number.isNaN(end.getTime()) ? `${day} ${time(start)}` : `${day} ${time(start)}\u2013${time(end)}`;
}

function picnicSlotsHtml() {
  if (!picnicState.slots.length) return "";
  const items = picnicState.slots
    .map((slot) => {
      const minimum = slot.minimumOrder ? ` <span class="muted">(${escapeHtml(t("picnic.slot_minimum", { amount: formatPrice(slot.minimumOrder) }))})</span>` : "";
      return `<li>${escapeHtml(formatSlot(slot))}${minimum}</li>`;
    })
    .join("");
  return `<div class="picnic-slots"><strong>${escapeHtml(t("picnic.slots_title"))}</strong><ul class="cart-lines">${items}</ul><p class="muted">${escapeHtml(t("picnic.slots_hint"))}</p></div>`;
}

// ---- Picnic in de tab Winkels (de keuzelijst staat in app.js): Recepten en Favorieten
// Recepten: bewaard, opnieuw bestellen en het kookboek
function renderPicnicRecipes() {
  const query = picnicRecipes.query.trim();
  const all = picnicRecipes.recipes;
  const shown = query ? all.filter((recipe) => matchesWords(recipe.name, query)) : all;
  // Dezelfde kaart en hetzelfde detail als bij Recepten zoeken (recipe_search.js), met Bekijken en Inplannen.
  $("#picnic-recipes").innerHTML = shown.map((recipe) => renderStoreRecipeCard("picnic", recipe)).join("");
  $("#picnic-recipe-filter").disabled = !all.length;
  $("#picnic-recipes-status").textContent = all.length ? t("picnic.recipes.count", { count: I18N.formatNumber(all.length) }) : "";
  if (!picnicRecipes.loaded) return;
  message("#picnic-recipes-message", !all.length ? t("picnic.recipes.none") : shown.length ? "" : t("picnic.recipes.none_match", { query }), "success");
}

async function loadPicnicRecipes() {
  const button = $("#load-picnic-recipes");
  setBusy(button, true);
  message("#picnic-recipes-message", "");
  picnicRecipes.source = $("#picnic-recipe-source").value;
  try {
    const data = await jsonFetch(`/api/picnic/recipes?source=${encodeURIComponent(picnicRecipes.source)}`);
    picnicRecipes.recipes = data.recipes || [];
    picnicRecipes.loaded = true;
    renderPicnicRecipes();
  } catch (error) {
    picnicRecipes.recipes = [];
    renderPicnicRecipes();
    message("#picnic-recipes-message", error.message);
  } finally {
    setBusy(button, false);
  }
}

$("#load-picnic-recipes").addEventListener("click", loadPicnicRecipes);
$("#picnic-recipe-source").addEventListener("change", loadPicnicRecipes);
$("#picnic-recipe-filter").addEventListener("input", (event) => {
  picnicRecipes.query = event.target.value;
  renderPicnicRecipes();
});
$("#picnic-recipes").addEventListener("click", (event) => {
  const open = event.target.closest("[data-store-open]");
  if (open) openStoreRecipe("picnic", open.dataset.storeOpen.split(":", 2)[1], PICNIC_HOST);
});

// Favorieten: de producten uit de tab Favorieten van de Picnic-app
function renderPicnicFavoriteCard(product) {
  const meta = [product.unitSize, product.price ? formatPrice(product.price) : ""].filter(Boolean).join(" \u00b7 ");
  return `
    <article class="product-card" data-picnic-favorite="${escapeHtml(product.id)}">
      ${product.image ? `<img src="${escapeHtml(storeImage(product.image))}" alt="" loading="lazy" data-hide-on-error>` : ""}
      <div>
        <strong>${escapeHtml(product.title)}</strong>
        ${meta ? `<small>${escapeHtml(meta)}</small>` : ""}
        ${product.isBonus ? `<small class="bonus">${escapeHtml(t("list.bonus"))}</small>` : ""}
        <button class="button button--secondary" type="button" data-picnic-add="${escapeHtml(product.id)}">${escapeHtml(t("picnic.favorites.add"))}</button>
        <div class="favorite-result"></div>
      </div>
    </article>`;
}

function renderPicnicFavorites() {
  const query = picnicFavorites.query.trim();
  const all = picnicFavorites.products;
  const shown = query ? all.filter((product) => matchesWords(product.title, query)) : all;
  $("#picnic-favorites").innerHTML = shown.map(renderPicnicFavoriteCard).join("");
  $("#picnic-favorite-filter").disabled = !all.length;
  const counted = all.length ? t("picnic.favorites.count", { count: I18N.formatNumber(all.length) }) : "";
  const checking = picnicFavorites.pending ? t("picnic.favorites.checking", { count: I18N.formatNumber(picnicFavorites.pending) }) : "";
  $("#picnic-favorites-status").textContent = [counted, checking].filter(Boolean).join(" ");
  if (!picnicFavorites.loaded) return;
  if (picnicFavorites.pending && !all.length) return; // we zijn nog aan het controleren: zeg nog niet dat er niets is
  // Markeert Picnic niet welke producten je favoriet zijn, dan tonen we er geen: de producten van de pagina zijn niet de jouwe.
  const empty = picnicFavorites.filtered ? t("picnic.favorites.none") : t("picnic.favorites.unknown", { count: I18N.formatNumber(picnicFavorites.total) });
  message("#picnic-favorites-message", !all.length ? empty : shown.length ? "" : t("picnic.favorites.none_match", { query }), "success");
}

// De producten van de Favorieten-tab worden lui gecontroleerd: de server doet een paar per aanroep en onthoudt wat hij weet.
// We vragen door tot er niets meer te controleren valt en tonen onderweg wat er al is.
const PICNIC_FAVORITES_STEP = 4;

async function loadPicnicFavorites({ refresh = true } = {}) {
  const button = $("#load-picnic-favorites");
  const run = ++picnicFavorites.run;
  setBusy(button, true);
  message("#picnic-favorites-message", "");
  try {
    let first = true;
    for (;;) {
      const query = `budget=${PICNIC_FAVORITES_STEP}${refresh && first ? "&refresh=1" : ""}`;
      const data = await jsonFetch(`/api/picnic/favorites?${query}`);
      first = false;
      if (run !== picnicFavorites.run) return; // een nieuwere aanvraag heeft het overgenomen
      picnicFavorites.products = data.products || [];
      picnicFavorites.total = data.total ?? picnicFavorites.products.length;
      picnicFavorites.filtered = data.filtered !== false;
      picnicFavorites.pending = data.pending || 0;
      picnicFavorites.loaded = true;
      renderPicnicFavorites();
      if (!picnicFavorites.pending) break;
    }
  } catch (error) {
    if (run !== picnicFavorites.run) return;
    picnicFavorites.products = [];
    picnicFavorites.pending = 0;
    renderPicnicFavorites();
    message("#picnic-favorites-message", error.message);
  } finally {
    if (run === picnicFavorites.run) setBusy(button, false);
  }
}

async function addPicnicFavorite(productId, button, target) {
  button.disabled = true;
  try {
    const title = picnicFavorites.products.find((product) => product.id === productId)?.title || "";
    const data = await postJson("/api/picnic/cart", { items: [{ query: title, productId, quantity: 1 }] });
    target.innerHTML = data.added
      ? `<div class="message success">${escapeHtml(t("picnic.favorites.added"))}</div>`
      : `<div class="message error">${escapeHtml(t("cart.not_found"))}</div>`;
  } catch (error) {
    target.innerHTML = `<div class="message error">${escapeHtml(error.message)}</div>`;
  } finally {
    button.disabled = false;
  }
}

$("#load-picnic-favorites").addEventListener("click", () => loadPicnicFavorites());
$("#picnic-favorite-filter").addEventListener("input", (event) => {
  picnicFavorites.query = event.target.value;
  renderPicnicFavorites();
});
$("#picnic-favorites").addEventListener("click", (event) => {
  const add = event.target.closest("[data-picnic-add]");
  if (add) addPicnicFavorite(add.dataset.picnicAdd, add, add.closest(".product-card").querySelector(".favorite-result"));
});

// ---- Bestellingen: aankomende leveringen live, afgeleverde bestellingen uit de lokale kopie (zie picnic_orders.py)
function picnicOrderStatus(status) {
  const known = {
    CURRENT: t("picnic.orders.status.current"),
    COMPLETED: t("picnic.orders.status.completed"),
    CANCELLED: t("picnic.orders.status.cancelled"),
  };
  return known[String(status || "").toUpperCase()] || String(status || "");
}

// De dag van een bestelling uit je account; een ingelezen bestelling heeft haar dag uit de mail.
function picnicOrderDate(order) {
  return order.imported ? order.date : order.deliveredStart || order.windowStart || order.etaStart;
}

function picnicOrderDay(order) {
  return dayLabel(picnicOrderDate(order));
}

// Een ingelezen bestelling (zie importedStoreOrder in orders.js) in de vorm van een afgeleverde bestelling van Picnic.
function picnicImportedOrder(order, from) {
  return importedStoreOrder(order, from, "COMPLETED");
}

// `source`: "live" (aankomend), "local" (uit de lokale kopie) of "imported" (ingelezen uit je mails: dezelfde kaart, met het
// archief-icoon en de prullenbak, zie importedOrderCard in orders.js).
function picnicOrderCard(order, source) {
  const lines = [
    picnicOrderStatus(order.status),
    [slotLabel(order.windowStart, order.windowEnd), money(order.total)].filter(Boolean).join(" \u00b7 "),
    source === "live" ? "" : order.itemsSynced ? tn("list.items", order.itemCount || 0) : t("orders.items_pending"),
  ].filter(Boolean);
  if (source === "imported") return importedOrderCard(order, "picnic", lines);
  return `
    <div class="list-card">
      <button type="button" class="list-card__open" data-picnic-order="${escapeHtml(order.id)}" data-source="${source}">
        <strong>${escapeHtml(picnicOrderDay(order) || t("picnic.orders.order_title", { id: order.id }))}</strong>
        ${lines.map((line) => `<small>${escapeHtml(line)}</small>`).join("")}
      </button>
    </div>`;
}

function renderPicnicOrders() {
  const { upcoming, syncedAt, loaded } = picnicOrders;
  const past = importedOrdersMerge(picnicOrders.past, picnicOrders.imported, picnicOrderDate, picnicImportedOrder);
  const empty = (text) => (loaded ? `<p class="muted">${escapeHtml(text)}</p>` : "");
  $("#picnic-orders-upcoming").innerHTML = upcoming.length
    ? upcoming.map((order) => picnicOrderCard(order, "live")).join("")
    : empty(picnicOrders.upcomingFailed ? t("picnic.orders.upcoming_offline") : t("picnic.orders.upcoming_none"));
  $("#picnic-orders-past").innerHTML = past.length
    ? past.map((order) => picnicOrderCard(order, order.imported ? "imported" : "local")).join("")
    : empty(syncedAt ? t("picnic.orders.none") : t("picnic.orders.never_synced"));
  $("#picnic-orders-synced").textContent = loaded && syncedAt ? t("picnic.orders.synced_at", { when: new Date(syncedAt).toLocaleString(I18N.locale(), { dateStyle: "medium", timeStyle: "short" }) }) : "";
}

// De bewaarde bestellingen komen uit de lokale kopie en werken zonder Picnic; de aankomende halen we live op en
// ontbreken (met een melding) als Picnic niet bereikbaar is.
async function loadPicnicOrders() {
  const run = ++picnicOrders.run;
  message("#picnic-orders-message", "");
  try {
    const [local, live, imported] = await Promise.all([
      jsonFetch("/api/picnic/orders"),
      jsonFetch("/api/picnic/deliveries?current=1").then((data) => data.deliveries || null, () => null),
      importedOrdersFetch("picnic"),
    ]);
    if (run !== picnicOrders.run) return;
    Object.assign(picnicOrders, {
      past: local.orders || [],
      imported,
      syncedAt: local.syncedAt,
      upcoming: (live || []).filter((order) => String(order.status).toUpperCase() === "CURRENT"),
      upcomingFailed: live === null,
      loaded: true,
    });
    renderPicnicOrders();
  } catch (error) {
    if (run === picnicOrders.run) message("#picnic-orders-message", error.message);
  }
}

// Synchroniseren haalt de afgeleverde bestellingen bij Picnic op en bewaart ze; een deel van de producten per keer.
async function syncPicnicOrders() {
  const button = $("#sync-picnic-orders");
  setBusy(button, true);
  message("#picnic-orders-message", "");
  let text = "";
  let failed = false;
  try {
    const data = await postJson("/api/picnic/orders/sync");
    const done = [tn("picnic.orders.sync_done", data.orders)];
    if (data.new) done.push(tn("picnic.orders.sync_new", data.new));
    if (data.remaining && !data.detailError) done.push(tn("picnic.orders.sync_remaining", data.remaining));
    text = `${done.join(", ")}.`;
    if (data.detailError) {
      failed = true;
      text += ` ${t("picnic.orders.sync_detail_error", { error: data.detailError })}`;
    }
  } catch (error) {
    failed = true;
    text = t("orders.sync_failed", { what: t("orders.part.orders"), error: error.message });
  }
  await loadPicnicOrders();
  message("#picnic-orders-message", text, failed ? "error" : "success");
  setBusy(button, false);
}

function showPicnicOrderList() {
  picnicOrders.detail = null;
  $("#picnic-order-view").hidden = true;
  $("#picnic-orders-list-view").hidden = false;
}

// Met `imported` (een ingelezen bestelling) staat rechts de knop Koppelen of Ander product kiezen (#238, product_links.js).
function renderPicnicOrderItem(item, imported = false) {
  const meta = [item.quantity > 1 ? `${item.quantity}\u00d7` : "", item.unitSize, item.price ? formatPrice(item.price) : ""].filter(Boolean).join(" \u00b7 ");
  return `
    <article class="product-card">
      ${item.image ? `<img src="${escapeHtml(storeImage(item.image))}" alt="" loading="lazy" data-hide-on-error>` : ""}
      <div>
        <strong>${escapeHtml(item.title || "")}</strong>
        ${meta ? `<small>${escapeHtml(meta)}</small>` : ""}
      </div>
      ${imported ? productLinkButton(importedLinkLine(item, "picnic")) : ""}
    </article>`;
}

// Een bewaarde bestelling lezen we uit de lokale kopie; staan de producten daar nog niet, dan proberen we Picnic zelf. Een ingelezen
// bestelling komt uit je mails, in de vorm van een Picnic-bestelling (met de producten waar de regels bij horen).
async function fetchPicnicOrder(id, source) {
  if (source === "imported") {
    const order = await jsonFetch(`/api/imported-orders/picnic/${encodeURIComponent(id)}`);
    return picnicImportedOrder(order, importedOrdersCover(picnicOrders.past, [order], picnicOrderDate).covered.get(order.ref));
  }
  if (source === "local") {
    const order = await jsonFetch(`/api/picnic/orders/${encodeURIComponent(id)}`);
    if (order.itemsSynced) return order;
    try {
      return { ...order, ...(await jsonFetch(`/api/picnic/deliveries/${encodeURIComponent(id)}`)) };
    } catch {
      return order;
    }
  }
  return jsonFetch(`/api/picnic/deliveries/${encodeURIComponent(id)}`);
}

async function openPicnicOrder(id, source) {
  $("#picnic-orders-list-view").hidden = true;
  $("#picnic-order-view").hidden = false;
  $("#picnic-order-title").textContent = t("picnic.orders.order_title", { id });
  $("#picnic-order-meta").textContent = "";
  $("#picnic-order-items").innerHTML = "";
  $("#picnic-order-add-all").hidden = true;
  message("#picnic-order-message", "");
  picnicOrders.detail = null;
  const open = ++picnicOrders.run;
  try {
    const order = await fetchPicnicOrder(id, source);
    if (open !== picnicOrders.run) return;
    const items = order.items || [];
    picnicOrders.detail = order;
    $("#picnic-order-title").textContent = picnicOrderDay(order) || t("picnic.orders.order_title", { id });
    $("#picnic-order-meta").textContent = [picnicOrderStatus(order.status), slotLabel(order.windowStart, order.windowEnd), order.total ? t("picnic.orders.total", { amount: money(order.total) }) : "", order.itemsSynced === false && !items.length ? t("orders.items_pending") : tn("list.items", order.itemCount || 0), order.imported ? t("orders.imported.at", { when: formatSyncTime(order.importedAt) }) : ""].filter(Boolean).join(" \u00b7 ");
    $("#picnic-order-items").innerHTML = items.map((item) => renderPicnicOrderItem(item, Boolean(order.imported))).join("");
    $("#picnic-order-add-all").hidden = !items.some((item) => item.id);
    if (!items.length) message("#picnic-order-message", order.itemsSynced === false ? t("picnic.orders.items_not_synced") : t("picnic.orders.no_items"), "success");
    $("#picnic-order-title").focus();
  } catch (error) {
    message("#picnic-order-message", error.message);
  }
}

// Een ingelezen oude bestelling opent als elke andere (#229): met de producten waar de regels uit de mail bij horen, en Alles in
// mandje voor die producten.
function openPicnicImportedOrder(ref) {
  openPicnicOrder(ref, "imported");
}

// De producten van een bestelling opnieuw in je Picnic-mandje; er wordt nooit een bestelling geplaatst.
async function addPicnicOrderToBasket() {
  const order = picnicOrders.detail;
  if (!order) return;
  const button = $("#picnic-order-add-all");
  setBusy(button, true);
  message("#picnic-order-message", "");
  try {
    const items = order.items.filter((item) => item.id).map((item) => ({ query: item.title || "", productId: item.id, quantity: item.quantity }));
    const data = await postJson("/api/picnic/cart", { items });
    const failed = (data.failed || []).length;
    message("#picnic-order-message", [t("picnic.orders.added", { count: data.added || 0 }), failed ? t("picnic.orders.added_failed", { count: failed }) : "", importedUnlinkedNote(order)].filter(Boolean).join(" "), failed ? "error" : "success");
  } catch (error) {
    message("#picnic-order-message", error.message);
  } finally {
    setBusy(button, false);
  }
}

$("#sync-picnic-orders").addEventListener("click", syncPicnicOrders);
$("#picnic-order-back").addEventListener("click", showPicnicOrderList);
$("#picnic-order-add-all").addEventListener("click", addPicnicOrderToBasket);
$("#picnic-orders-list-view").addEventListener("click", (event) => {
  const open = event.target.closest("[data-picnic-order]");
  const imported = event.target.closest("[data-imported-order]");
  const remove = event.target.closest("[data-imported-delete]");
  if (remove) deleteImportedOrder("picnic", remove.dataset.importedDelete, remove);
  else if (open) openPicnicOrder(open.dataset.picnicOrder, open.dataset.source);
  else if (imported) openPicnicImportedOrder(imported.dataset.importedOrder);
});
$("#picnic-orders-import").addEventListener("click", () => openPriceImport("picnic"));
window.addEventListener("ms:imported-orders", (event) => {
  if (picnicOrders.loaded && event.detail?.store === "picnic") loadPicnicOrders();
});
// Na een eigen koppeling (#238, product_links.js): de lijst opnieuw, en een open ingelezen bestelling opnieuw met het gekozen product.
window.addEventListener("ms:product-links", (event) => {
  if (event.detail?.store !== "picnic") return;
  if (picnicOrders.loaded) loadPicnicOrders();
  const open = picnicOrders.detail;
  if (open?.imported && !$("#picnic-order-view").hidden) openPicnicOrder(open.ref, "imported");
});

// ---- Het Picnic-mandje bekijken en aanpassen (tabblad Mandje: aantallen, product weg, mandje legen)
// Alles gaat direct naar je echte Picnic-mandje. Er wordt nooit een bestelling geplaatst.
async function showPicnicBasket() {
  const [cart, slots] = await Promise.all([
    jsonFetch("/api/picnic/cart"),
    // De bezorgmomenten zijn een extraatje: het mandje blijft staan als ze niet te laden zijn.
    jsonFetch("/api/picnic/slots?limit=6").then((data) => data.slots || [], () => []),
  ]);
  picnicState.slots = slots;
  renderPicnicBasket(cart);
}

function renderPicnicBasket(cart, note = "") {
  const box = $("#basket-content");
  if (!box || basketState.store !== "picnic") return;
  const items = cart.items || [];
  const lines = items
    .map((item) => {
      const size = item.unitSize ? ` <small class="muted">${escapeHtml(item.unitSize)}</small>` : "";
      // Bij een aanbieding of korting: de prijs zonder korting doorgestreept, dan wat je betaalt.
      const listed = item.listPrice ? `<s class="muted">${escapeHtml(formatPrice(item.listPrice))}</s> ` : "";
      const price = item.price ? `<span class="basket-price">${listed}${escapeHtml(formatPrice(item.price))}</span>` : '<span class="basket-price"></span>';
      const unavailable = item.available === false ? ` <small class="basket-unavailable">${escapeHtml(t("picnic.basket_unavailable"))}</small>` : "";
      const title = escapeHtml(item.title || "-") + unavailable;
      const maxAllowed = Math.min(99, item.maxCount || 99);
      if (!item.id) return `<li class="basket-line"><span class="basket-title">${title}${size}</span><span class="basket-qty">${escapeHtml(item.quantity)}\u00d7</span>${price}<span></span></li>`;
      return `
        <li class="basket-line${item.available === false ? " is-unavailable" : ""}" data-id="${escapeHtml(item.id)}">
          <span class="basket-title">${title}${size}</span>
          <span class="basket-qty">
            <button class="icon-button" type="button" data-act="less" aria-label="${escapeHtml(t("picnic.basket_less"))}">\u2212</button>
            <input class="review-qty" type="number" min="0" max="${maxAllowed}" value="${escapeHtml(item.quantity)}" data-act="set" data-max="${maxAllowed}" aria-label="${escapeHtml(t("picnic.basket_qty"))}">
            <button class="icon-button" type="button" data-act="more" aria-label="${escapeHtml(t("picnic.basket_more"))}"${item.quantity >= maxAllowed ? " disabled" : ""}>+</button>
          </span>
          ${price}
          <button class="icon-button" type="button" data-act="remove" aria-label="${escapeHtml(t("picnic.basket_remove"))}" title="${escapeHtml(t("picnic.basket_remove"))}">\u00d7</button>
        </li>`;
    })
    .join("");
  const totals = [
    cart.checkoutTotal ? t("picnic.basket_checkout", { amount: formatPrice(cart.checkoutTotal) }) : cart.totalPrice ? t("picnic.basket_total", { amount: formatPrice(cart.totalPrice) }) : "",
    cart.savings ? t("picnic.basket_savings", { amount: formatPrice(cart.savings) }) : "",
  ].filter(Boolean);
  const unavailableCount = items.filter((item) => item.available === false).length;
  box.innerHTML = `
    ${items.length ? `<div class="toolbar"><button class="button button--secondary" id="picnic-basket-clear" type="button">${escapeHtml(t("picnic.basket_clear"))}</button></div>` : ""}
    ${note ? `<div class="message success">${escapeHtml(note)}</div>` : ""}
    ${unavailableCount ? `<div class="message error">${escapeHtml(t("picnic.basket_unavailable_note", { count: unavailableCount }))}</div>` : ""}
    ${items.length ? `<ul class="basket-lines">${lines}</ul>` : `<p class="muted">${escapeHtml(t("picnic.basket_empty"))}</p>`}
    ${totals.length ? `<p><strong>${escapeHtml(totals.join(" \u00b7 "))}</strong></p>` : ""}
    <p class="muted">${escapeHtml(t("picnic.basket_hint"))}</p>
    ${picnicSlotsHtml()}`;
  $("#picnic-basket-clear")?.addEventListener("click", () => clearPicnicBasket(items));
  box.querySelectorAll(".basket-line[data-id]").forEach((line) => {
    const id = line.dataset.id;
    const input = line.querySelector('[data-act="set"]');
    const current = Number(input.value);
    const maxAllowed = Number(input.dataset.max) || 99;
    line.querySelector('[data-act="less"]').addEventListener("click", () => setPicnicQuantity(id, Math.max(0, current - 1)));
    line.querySelector('[data-act="more"]').addEventListener("click", () => setPicnicQuantity(id, Math.min(maxAllowed, current + 1)));
    line.querySelector('[data-act="remove"]').addEventListener("click", () => setPicnicQuantity(id, 0));
    input.addEventListener("change", () => {
      const value = Number(input.value);
      if (Number.isInteger(value) && value >= 0 && value <= maxAllowed) setPicnicQuantity(id, value);
      else {
        input.value = current;
        if (Number.isInteger(value) && value > maxAllowed) message("#basket-message", t("picnic.basket_max", { max: maxAllowed }));
      }
    });
  });
}

function setPicnicBasketBusy(busy) {
  document.querySelectorAll("#basket-content button, #basket-content input").forEach((el) => {
    el.disabled = busy;
  });
}

async function setPicnicQuantity(productId, quantity) {
  setPicnicBasketBusy(true);
  try {
    const cart = await jsonFetch(`/api/picnic/cart/items/${encodeURIComponent(productId)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ quantity }),
    });
    renderPicnicBasket(cart);
  } catch (error) {
    setPicnicBasketBusy(false);
    message("#basket-message", error.message);
  }
}

async function clearPicnicBasket(items) {
  const count = items.reduce((sum, item) => sum + Number(item.quantity || 0), 0);
  if (!window.confirm(t("picnic.basket_clear_confirm", { count }))) return;
  setPicnicBasketBusy(true);
  try {
    const data = await postJson("/api/picnic/cart/clear", { confirm: true });
    renderPicnicBasket(data.cart || { items: [] }, t("picnic.basket_cleared", { count: data.removed }));
  } catch (error) {
    setPicnicBasketBusy(false);
    message("#basket-message", error.message);
  }
}

$("#picnic-form").addEventListener("submit", loginToPicnic);
$("#picnic-code-form").addEventListener("submit", confirmPicnicCode);
$("#picnic-send-sms").addEventListener("click", () => requestPicnicCode("SMS"));
$("#picnic-send-email").addEventListener("click", () => requestPicnicCode("EMAIL"));
$("#picnic-check").addEventListener("click", checkPicnicConnection);
$("#picnic-unlink").addEventListener("click", unlinkPicnic);
$("#picnic-explore-run").addEventListener("click", runPicnicExplore);
$("#picnic-explore-copy").addEventListener("click", async (event) => {
  const text = $("#picnic-explore-output").textContent;
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const range = document.createRange();
    range.selectNodeContents($("#picnic-explore-output"));
    getSelection().removeAllRanges();
    getSelection().addRange(range);
    document.execCommand("copy");
  }
  event.currentTarget.textContent = t("users.copied");
  setTimeout(() => (event.currentTarget.textContent = t("users.copy")), 2000);
});

window.addEventListener("ms:languagechange", () => {
  if (picnicRecipes.loaded) renderPicnicRecipes();
  if (picnicFavorites.loaded) renderPicnicFavorites();
  renderPicnicStatus();
  renderStoreChoice();
  renderPicnicExploreSummary();
});
