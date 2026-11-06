// Crisp: koppelen (met een code per mail), het mandje aanpassen, recepten, favorieten en bestellingen onder Winkels, en de verkenner.
// Laadt na app.js en picnic.js en gebruikt daar de hulpfuncties van ($, message, jsonFetch, postJson, setBusy, t, ...).

const crispState = { connected: false, codePending: false, report: null };
// Je bewaarde recepten (Winkels > Crisp > Recepten); `run` negeert het antwoord van een eerdere, tragere aanvraag.
const crispRecipes = { recipes: [], query: "", loaded: false, run: 0 };
const crispFavorites = { products: [], query: "", loaded: false, run: 0 };
// `imported`: de ingelezen oude bestellingen (zie price_import.py), die bij de afgelopen bestellingen staan.
const crispOrders = { upcoming: [], past: [], imported: [], syncedAt: null, upcomingFailed: false, detail: null, loaded: false, run: 0 };

// ---- Status en koppelen (beheerder)
function renderCrispStatus() {
  $("#crisp-code-box").hidden = !crispState.codePending;
  $("#crisp-unlink").hidden = !crispState.connected;
  $("#crisp-check").hidden = !crispState.connected;
  renderLinkStatus("#crisp-status", STORES.crisp.name, crispState.connected ? "linked" : "unlinked");
  applyStoreFold("crisp", crispState.connected);
}

async function refreshCrispStatus() {
  try {
    const data = await jsonFetch("/api/crisp/auth/status");
    crispState.connected = Boolean(data.connected);
    crispState.codePending = Boolean(data.codePending) && !data.connected;
    state.linkedStores.crisp = crispState.connected;
    if (data.country) $("#crisp-country").value = data.country;
    renderCrispStatus();
  } catch (error) {
    message("#crisp-status", error.message);
  }
  renderStoreChoice();
}

async function checkCrispConnection() {
  message("#crisp-status", t("ah.checking"), "success");
  try {
    const data = await jsonFetch("/api/crisp/auth/status?verify=1");
    crispState.connected = Boolean(data.connected);
    if (data.verified) renderLinkStatus("#crisp-status", STORES.crisp.name, "working");
    else if (data.connected) message("#crisp-status", t("crisp.broken", { error: data.error || "" }));
    else renderLinkStatus("#crisp-status", STORES.crisp.name, "unlinked");
  } catch (error) {
    message("#crisp-status", error.message);
  }
}

function setCrispLinked(linked) {
  crispState.connected = linked;
  crispState.codePending = false;
  state.linkedStores.crisp = linked;
  // Een ander (of geen) account: recepten, favorieten en bestellingen opnieuw laden zodra je ze opent.
  crispRecipes.loaded = false;
  crispFavorites.loaded = false;
  crispOrders.loaded = false;
  renderCrispStatus();
  renderStoreChoice();
}

async function requestCrispCode(event) {
  event.preventDefault();
  const button = $("#crisp-login");
  setBusy(button, true, t("picnic.logging_in"));
  message("#crisp-message", "");
  message("#crisp-code-message", "");
  try {
    const data = await postJson("/api/crisp/auth", { email: $("#crisp-email").value.trim(), country: $("#crisp-country").value });
    if (data.connected) {
      setCrispLinked(true);
      message("#crisp-message", t("crisp.saved"), "success");
    } else {
      crispState.codePending = Boolean(data.codeRequired);
      renderCrispStatus();
      message("#crisp-code-message", t("crisp.code_sent"), "success");
      $("#crisp-code").focus();
    }
  } catch (error) {
    message("#crisp-message", error.message);
  } finally {
    setBusy(button, false, t("picnic.logging_in"));
  }
}

async function confirmCrispCode(event) {
  event.preventDefault();
  const button = $("#crisp-verify");
  setBusy(button, true, t("picnic.logging_in"));
  try {
    await postJson("/api/crisp/auth/verify", { code: $("#crisp-code").value.trim() });
    $("#crisp-code").value = "";
    $("#crisp-email").value = "";
    message("#crisp-code-message", "");
    setCrispLinked(true);
    message("#crisp-message", t("crisp.saved"), "success");
  } catch (error) {
    message("#crisp-code-message", error.message);
  } finally {
    setBusy(button, false, t("picnic.logging_in"));
  }
}

async function unlinkCrisp() {
  try {
    await postJson("/api/crisp/auth/logout");
    message("#crisp-message", "");
    setCrispLinked(false);
  } catch (error) {
    message("#crisp-status", error.message);
  }
}

// ---- Verkenner (alleen de beheerder)
function renderCrispExploreSummary() {
  const report = crispState.report;
  if (report) $("#crisp-explore-summary").textContent = t("explore.summary", { ok: report.working ?? 0, total: (report.checks || []).length });
}

async function runCrispExplore() {
  const button = $("#crisp-explore-run");
  setBusy(button, true);
  message("#crisp-explore-message", "");
  try {
    crispState.report = await postJson("/api/crisp/explore");
    renderCrispExploreSummary();
    $("#crisp-explore-output").textContent = JSON.stringify(crispState.report, null, 2);
    $("#crisp-explore-report").hidden = false;
    if (!crispState.report.linked) message("#crisp-explore-message", t("crisp.explore_not_linked"));
  } catch (error) {
    message("#crisp-explore-message", error.message);
  } finally {
    setBusy(button, false);
  }
}

// ---- Recepten (Winkels > Crisp): de recepten die je in de Crisp-app hebt bewaard. Het detail, importeren en inplannen
// zijn dezelfde als bij Picnic (recipe_search.js); selecteren en in bulk importeren staat in recipe_bulk.js.
function renderCrispRecipes() {
  const query = crispRecipes.query.trim();
  const all = crispRecipes.recipes;
  const shown = query ? all.filter((recipe) => matchesWords(recipe.name, query)) : all;
  $("#crisp-recipes").innerHTML = shown.map((recipe) => renderStoreRecipeCard("crisp", recipe)).join("");
  $("#crisp-recipe-filter").disabled = !all.length;
  $("#crisp-recipes-status").textContent = all.length ? t("picnic.recipes.count", { count: I18N.formatNumber(all.length) }) : "";
  if (crispRecipes.loaded) message("#crisp-recipes-message", !all.length ? t("crisp.recipes.none") : shown.length ? "" : t("picnic.recipes.none_match", { query }), "success");
  if (typeof recipeBulkDecorate === "function") recipeBulkDecorate("crisp");
}

async function loadCrispRecipes() {
  const button = $("#load-crisp-recipes");
  const run = ++crispRecipes.run;
  setBusy(button, true);
  message("#crisp-recipes-message", "");
  try {
    const data = await jsonFetch("/api/crisp/recipes?source=saved");
    if (run !== crispRecipes.run) return;
    crispRecipes.recipes = data.recipes || [];
    crispRecipes.loaded = true;
    renderCrispRecipes();
  } catch (error) {
    if (run !== crispRecipes.run) return;
    crispRecipes.recipes = [];
    renderCrispRecipes();
    message("#crisp-recipes-message", error.message);
  } finally {
    if (run === crispRecipes.run) setBusy(button, false);
  }
}

// ---- Favorieten (Winkels > Crisp): de producten die je in de Crisp-app hebt bewaard
function renderCrispFavoriteCard(product) {
  const meta = [product.unitSize, product.price ? formatPrice(product.price) : ""].filter(Boolean).join(" · ");
  return `
    <article class="product-card" data-crisp-favorite="${escapeHtml(product.id)}">
      ${product.image ? `<img src="${escapeHtml(storeImage(product.image))}" alt="" loading="lazy" data-hide-on-error>` : ""}
      <div>
        <strong>${escapeHtml(product.title)}</strong>
        ${meta ? `<small>${escapeHtml(meta)}</small>` : ""}
        ${product.isBonus ? `<small class="bonus">${escapeHtml(t("list.bonus"))}</small>` : ""}
        <button class="button button--secondary" type="button" data-crisp-add="${escapeHtml(product.id)}">${escapeHtml(t("picnic.favorites.add"))}</button>
        <div class="favorite-result"></div>
      </div>
    </article>`;
}

function renderCrispFavorites() {
  const query = crispFavorites.query.trim();
  const all = crispFavorites.products;
  const shown = query ? all.filter((product) => matchesWords(product.title, query)) : all;
  $("#crisp-favorites").innerHTML = shown.map(renderCrispFavoriteCard).join("");
  $("#crisp-favorite-filter").disabled = !all.length;
  $("#crisp-favorites-status").textContent = all.length ? t("picnic.favorites.count", { count: I18N.formatNumber(all.length) }) : "";
  if (!crispFavorites.loaded) return;
  message("#crisp-favorites-message", !all.length ? t("crisp.favorites.none") : shown.length ? "" : t("picnic.favorites.none_match", { query }), "success");
}

async function loadCrispFavorites() {
  const button = $("#load-crisp-favorites");
  const run = ++crispFavorites.run;
  setBusy(button, true);
  message("#crisp-favorites-message", "");
  try {
    const data = await jsonFetch("/api/crisp/favorites");
    if (run !== crispFavorites.run) return;
    crispFavorites.products = data.products || [];
    crispFavorites.loaded = true;
    renderCrispFavorites();
  } catch (error) {
    if (run !== crispFavorites.run) return;
    crispFavorites.products = [];
    renderCrispFavorites();
    message("#crisp-favorites-message", error.message);
  } finally {
    if (run === crispFavorites.run) setBusy(button, false);
  }
}

async function addCrispFavorite(productId, button, target) {
  button.disabled = true;
  try {
    const title = crispFavorites.products.find((product) => product.id === productId)?.title || "";
    const data = await postJson("/api/crisp/cart", { items: [{ query: title, productId, quantity: 1 }] });
    target.innerHTML = data.added
      ? `<div class="message success">${escapeHtml(t("crisp.favorites.added"))}</div>`
      : `<div class="message error">${escapeHtml(t("cart.not_found"))}</div>`;
  } catch (error) {
    target.innerHTML = `<div class="message error">${escapeHtml(error.message)}</div>`;
  } finally {
    button.disabled = false;
  }
}

// ---- Bestellingen (Winkels > Crisp): aankomende live, bezorgde uit de lokale kopie (zie crisp_orders.py)
function crispOrderStatus(status) {
  const known = {
    ready: t("crisp.orders.status.planned"),
    picked: t("crisp.orders.status.planned"),
    delivery: t("crisp.orders.status.delivery"),
    done: t("crisp.orders.status.done"),
    manual: t("crisp.orders.status.done"),
    cancel: t("picnic.orders.status.cancelled"),
  };
  return known[String(status || "").toLowerCase()] || String(status || "");
}

// De dag van een bestelling uit je account; een ingelezen bestelling heeft haar dag uit de mail.
function crispOrderDate(order) {
  return order.imported ? order.date : order.windowStart || order.deliveryDate;
}

function crispOrderDay(order) {
  return dayLabel(crispOrderDate(order));
}

// Een ingelezen bestelling (zie importedStoreOrder in orders.js) in de vorm van een bezorgde bestelling van Crisp.
function crispImportedOrder(order, from) {
  return importedStoreOrder(order, from, "done");
}

// `source`: "live" (aankomend), "local" (uit de lokale kopie) of "imported" (ingelezen uit je mails: dezelfde kaart, met het
// archief-icoon en de prullenbak, zie importedOrderCard in orders.js).
function crispOrderCard(order, source) {
  const lines = [
    crispOrderStatus(order.status),
    [slotLabel(order.windowStart, order.windowEnd), money(order.total)].filter(Boolean).join(" · "),
    order.itemsSynced ? tn("list.items", order.itemCount || 0) : source === "local" ? t("orders.items_pending") : "",
  ].filter(Boolean);
  if (source === "imported") return importedOrderCard(order, "crisp", lines);
  return `
    <div class="list-card">
      <button type="button" class="list-card__open" data-crisp-order="${escapeHtml(order.id)}">
        <strong>${escapeHtml(crispOrderDay(order) || t("picnic.orders.order_title", { id: order.id }))}</strong>
        ${lines.map((line) => `<small>${escapeHtml(line)}</small>`).join("")}
      </button>
    </div>`;
}

function renderCrispOrders() {
  const { upcoming, syncedAt, loaded } = crispOrders;
  const past = importedOrdersMerge(crispOrders.past, crispOrders.imported, crispOrderDate, crispImportedOrder);
  const empty = (text) => (loaded ? `<p class="muted">${escapeHtml(text)}</p>` : "");
  $("#crisp-orders-upcoming").innerHTML = upcoming.length
    ? upcoming.map((order) => crispOrderCard(order, "live")).join("")
    : empty(crispOrders.upcomingFailed ? t("crisp.orders.upcoming_offline") : t("picnic.orders.upcoming_none"));
  $("#crisp-orders-past").innerHTML = past.length
    ? past.map((order) => crispOrderCard(order, order.imported ? "imported" : "local")).join("")
    : empty(syncedAt ? t("crisp.orders.none") : t("crisp.orders.never_synced"));
  $("#crisp-orders-synced").textContent = loaded && syncedAt ? t("picnic.orders.synced_at", { when: new Date(syncedAt).toLocaleString(I18N.locale(), { dateStyle: "medium", timeStyle: "short" }) }) : "";
}

// De bewaarde bestellingen komen uit de lokale kopie en werken zonder Crisp; de aankomende halen we live op en
// ontbreken (met een melding) als Crisp niet bereikbaar is.
async function loadCrispOrders() {
  const run = ++crispOrders.run;
  message("#crisp-orders-message", "");
  try {
    const [local, live, imported] = await Promise.all([
      jsonFetch("/api/crisp/orders"),
      jsonFetch("/api/crisp/orders/open").then((data) => data.orders || [], () => null),
      importedOrdersFetch("crisp"),
    ]);
    if (run !== crispOrders.run) return;
    Object.assign(crispOrders, { past: local.orders || [], imported, syncedAt: local.syncedAt, upcoming: live || [], upcomingFailed: live === null, loaded: true });
    renderCrispOrders();
  } catch (error) {
    if (run === crispOrders.run) message("#crisp-orders-message", error.message);
  }
}

async function syncCrispOrders() {
  const button = $("#sync-crisp-orders");
  setBusy(button, true);
  message("#crisp-orders-message", "");
  let text = "";
  let failed = false;
  try {
    const data = await postJson("/api/crisp/orders/sync");
    const done = [tn("picnic.orders.sync_done", data.orders)];
    if (data.new) done.push(tn("picnic.orders.sync_new", data.new));
    if (data.remaining && !data.detailError) done.push(tn("picnic.orders.sync_remaining", data.remaining));
    text = `${done.join(", ")}.`;
    if (data.detailError) {
      failed = true;
      text += ` ${t("crisp.orders.sync_detail_error", { error: data.detailError })}`;
    }
  } catch (error) {
    failed = true;
    text = t("orders.sync_failed", { what: t("orders.part.orders"), error: error.message });
  }
  await loadCrispOrders();
  message("#crisp-orders-message", text, failed ? "error" : "success");
  setBusy(button, false);
}

function showCrispOrderList() {
  crispOrders.detail = null;
  $("#crisp-order-view").hidden = true;
  $("#crisp-orders-list-view").hidden = false;
}

// Met `imported` (een ingelezen bestelling) staat rechts de knop Koppelen of Ander product kiezen (#238, product_links.js).
function renderCrispOrderItem(item, imported = false) {
  const meta = [item.quantity > 1 ? `${item.quantity}×` : "", item.unitSize, item.price ? formatPrice(item.price) : ""].filter(Boolean).join(" · ");
  return `
    <article class="product-card">
      ${item.image ? `<img src="${escapeHtml(storeImage(item.image))}" alt="" loading="lazy" data-hide-on-error>` : ""}
      <div>
        <strong>${escapeHtml(item.title || "")}</strong>
        ${meta ? `<small>${escapeHtml(meta)}</small>` : ""}
      </div>
      ${imported ? productLinkButton(importedLinkLine(item, "crisp")) : ""}
    </article>`;
}

// Een bestelling uit de lokale kopie, of (`source` "imported") een ingelezen bestelling uit je mails in de vorm van een
// Crisp-bestelling, met de producten waar de regels bij horen.
async function fetchCrispOrder(id, source) {
  if (source !== "imported") return jsonFetch(`/api/crisp/orders/${encodeURIComponent(id)}`);
  const order = await jsonFetch(`/api/imported-orders/crisp/${encodeURIComponent(id)}`);
  return crispImportedOrder(order, importedOrdersCover(crispOrders.past, [order], crispOrderDate).covered.get(order.ref));
}

async function openCrispOrder(id, source = "local") {
  $("#crisp-orders-list-view").hidden = true;
  $("#crisp-order-view").hidden = false;
  $("#crisp-order-title").textContent = t("picnic.orders.order_title", { id });
  $("#crisp-order-meta").textContent = "";
  $("#crisp-order-items").innerHTML = "";
  $("#crisp-order-add-all").hidden = true;
  message("#crisp-order-message", "");
  crispOrders.detail = null;
  const open = ++crispOrders.run;
  try {
    const order = await fetchCrispOrder(id, source);
    if (open !== crispOrders.run) return;
    const items = order.items || [];
    crispOrders.detail = order;
    $("#crisp-order-title").textContent = crispOrderDay(order) || t("picnic.orders.order_title", { id });
    $("#crisp-order-meta").textContent = [crispOrderStatus(order.status), slotLabel(order.windowStart, order.windowEnd), order.total ? t("picnic.orders.total", { amount: money(order.total) }) : "", order.itemsSynced === false && !items.length ? t("orders.items_pending") : tn("list.items", order.itemCount || 0), order.imported ? t("orders.imported.at", { when: formatSyncTime(order.importedAt) }) : ""].filter(Boolean).join(" · ");
    $("#crisp-order-items").innerHTML = items.map((item) => renderCrispOrderItem(item, Boolean(order.imported))).join("");
    $("#crisp-order-add-all").hidden = !items.some((item) => item.id);
    if (!items.length) message("#crisp-order-message", order.itemsSynced === false ? t("crisp.orders.items_not_synced") : t("crisp.orders.no_items"), "success");
    $("#crisp-order-title").focus();
  } catch (error) {
    message("#crisp-order-message", error.message);
  }
}

// Een ingelezen oude bestelling opent als elke andere (#229): met de producten waar de regels uit de mail bij horen, en Alles in
// mandje voor die producten.
function openCrispImportedOrder(ref) {
  openCrispOrder(ref, "imported");
}

// De producten van een bestelling opnieuw in je Crisp-mandje; er wordt nooit een bestelling geplaatst.
async function addCrispOrderToBasket() {
  const order = crispOrders.detail;
  if (!order) return;
  const button = $("#crisp-order-add-all");
  setBusy(button, true);
  message("#crisp-order-message", "");
  try {
    const items = order.items.filter((item) => item.id).map((item) => ({ query: item.title || "", productId: item.id, quantity: item.quantity }));
    const data = await postJson("/api/crisp/cart", { items });
    const failed = (data.failed || []).length;
    message("#crisp-order-message", [t("crisp.orders.added", { count: data.added || 0 }), failed ? t("picnic.orders.added_failed", { count: failed }) : "", importedUnlinkedNote(order)].filter(Boolean).join(" "), failed ? "error" : "success");
  } catch (error) {
    message("#crisp-order-message", error.message);
  } finally {
    setBusy(button, false);
  }
}

// ---- Het mandje (tabblad Mandje): aantallen aanpassen, weghalen en legen gaat direct naar het echte Crisp-mandje.
// Bestellen en een bezorgmoment kiezen doe je in de Crisp-app.
async function showCrispBasket() {
  renderCrispBasket(await jsonFetch("/api/crisp/cart"));
}

function renderCrispBasket(cart, note = "") {
  const box = $("#basket-content");
  if (!box || basketState.store !== "crisp") return;
  const items = cart.items || [];
  const lines = items
    .map((item) => {
      const size = item.unitSize ? ` <small class="muted">${escapeHtml(item.unitSize)}</small>` : "";
      const listed = item.listPrice ? `<s class="muted">${escapeHtml(formatPrice(item.listPrice))}</s> ` : "";
      const price = item.price != null ? `<span class="basket-price">${listed}${escapeHtml(formatPrice(item.price))}</span>` : '<span class="basket-price"></span>';
      const maxAllowed = Math.min(99, item.maxCount || 99);
      return `
        <li class="basket-line" data-id="${escapeHtml(item.id)}">
          <span class="basket-title">${escapeHtml(item.title || "-")}${size}</span>
          <span class="basket-qty">
            <button class="icon-button" type="button" data-act="less" aria-label="${escapeHtml(t("picnic.basket_less"))}">−</button>
            <input class="review-qty" type="number" min="0" max="${maxAllowed}" value="${escapeHtml(item.quantity)}" data-act="set" data-max="${maxAllowed}" aria-label="${escapeHtml(t("picnic.basket_qty"))}">
            <button class="icon-button" type="button" data-act="more" aria-label="${escapeHtml(t("picnic.basket_more"))}"${item.quantity >= maxAllowed ? " disabled" : ""}>+</button>
          </span>
          ${price}
          <button class="icon-button" type="button" data-act="remove" aria-label="${escapeHtml(t("picnic.basket_remove"))}" title="${escapeHtml(t("picnic.basket_remove"))}">×</button>
        </li>`;
    })
    .join("");
  const totals = [
    cart.totalPrice != null ? t("picnic.basket_total", { amount: formatPrice(cart.totalPrice) }) : "",
    cart.checkoutTotal ? t("picnic.basket_checkout", { amount: formatPrice(cart.checkoutTotal) }) : "",
  ].filter(Boolean);
  const minimum = items.length && cart.stillNeeded ? t("crisp.basket_minimum", { amount: formatPrice(cart.stillNeeded), minimum: formatPrice(cart.minimumOrder || 0) }) : "";
  box.innerHTML = `
    ${items.length ? `<div class="toolbar"><button class="button button--secondary" id="crisp-basket-clear" type="button">${escapeHtml(t("picnic.basket_clear"))}</button></div>` : ""}
    ${note ? `<div class="message success">${escapeHtml(note)}</div>` : ""}
    ${items.length ? `<ul class="basket-lines">${lines}</ul>` : `<p class="muted">${escapeHtml(t("basket.crisp_empty"))}</p>`}
    ${totals.length ? `<p><strong>${escapeHtml(totals.join(" · "))}</strong></p>` : ""}
    ${minimum ? `<p class="muted">${escapeHtml(minimum)}</p>` : ""}
    <p class="muted">${escapeHtml(t("basket.crisp_hint"))}</p>`;
  $("#crisp-basket-clear")?.addEventListener("click", () => clearCrispBasket(items));
  box.querySelectorAll(".basket-line[data-id]").forEach((line) => {
    const id = line.dataset.id;
    const input = line.querySelector('[data-act="set"]');
    const current = Number(input.value);
    const maxAllowed = Number(input.dataset.max) || 99;
    line.querySelector('[data-act="less"]').addEventListener("click", () => setCrispQuantity(id, Math.max(0, current - 1)));
    line.querySelector('[data-act="more"]').addEventListener("click", () => setCrispQuantity(id, Math.min(maxAllowed, current + 1)));
    line.querySelector('[data-act="remove"]').addEventListener("click", () => setCrispQuantity(id, 0));
    input.addEventListener("change", () => {
      const value = Number(input.value);
      if (Number.isInteger(value) && value >= 0 && value <= maxAllowed) setCrispQuantity(id, value);
      else {
        input.value = current;
        if (Number.isInteger(value) && value > maxAllowed) message("#basket-message", t("crisp.basket_max", { max: maxAllowed }));
      }
    });
  });
}

function setCrispBasketBusy(busy) {
  document.querySelectorAll("#basket-content button, #basket-content input").forEach((el) => {
    el.disabled = busy;
  });
}

async function setCrispQuantity(productId, quantity) {
  setCrispBasketBusy(true);
  message("#basket-message", "");
  try {
    const cart = await jsonFetch(`/api/crisp/cart/items/${encodeURIComponent(productId)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ quantity }),
    });
    renderCrispBasket(cart);
  } catch (error) {
    setCrispBasketBusy(false);
    message("#basket-message", error.message);
  }
}

async function clearCrispBasket(items) {
  const count = items.reduce((sum, item) => sum + Number(item.quantity || 0), 0);
  if (!window.confirm(t("crisp.basket_clear_confirm", { count }))) return;
  setCrispBasketBusy(true);
  message("#basket-message", "");
  try {
    const data = await postJson("/api/crisp/cart/clear", { confirm: true });
    renderCrispBasket(data.cart || { items: [] }, t("crisp.basket_cleared", { count: data.removed }));
  } catch (error) {
    setCrispBasketBusy(false);
    message("#basket-message", error.message);
  }
}

$("#crisp-form").addEventListener("submit", requestCrispCode);
$("#crisp-code-form").addEventListener("submit", confirmCrispCode);
$("#crisp-check").addEventListener("click", checkCrispConnection);
$("#crisp-unlink").addEventListener("click", unlinkCrisp);
$("#crisp-explore-run").addEventListener("click", runCrispExplore);
$("#load-crisp-recipes").addEventListener("click", loadCrispRecipes);
$("#crisp-recipe-filter").addEventListener("input", (event) => {
  crispRecipes.query = event.target.value;
  renderCrispRecipes();
});
$("#crisp-recipes").addEventListener("click", (event) => {
  const open = event.target.closest("[data-store-open]");
  if (open) openStoreRecipe("crisp", open.dataset.storeOpen.split(":", 2)[1], CRISP_HOST);
});
$("#crisp-recipe-back").addEventListener("click", () => showRecipeList(CRISP_HOST));
$("#load-crisp-favorites").addEventListener("click", loadCrispFavorites);
$("#crisp-favorite-filter").addEventListener("input", (event) => {
  crispFavorites.query = event.target.value;
  renderCrispFavorites();
});
$("#crisp-favorites").addEventListener("click", (event) => {
  const add = event.target.closest("[data-crisp-add]");
  if (add) addCrispFavorite(add.dataset.crispAdd, add, add.closest(".product-card").querySelector(".favorite-result"));
});
$("#sync-crisp-orders").addEventListener("click", syncCrispOrders);
$("#crisp-order-back").addEventListener("click", showCrispOrderList);
$("#crisp-order-add-all").addEventListener("click", addCrispOrderToBasket);
$("#crisp-orders-list-view").addEventListener("click", (event) => {
  const open = event.target.closest("[data-crisp-order]");
  const imported = event.target.closest("[data-imported-order]");
  const remove = event.target.closest("[data-imported-delete]");
  if (remove) deleteImportedOrder("crisp", remove.dataset.importedDelete, remove);
  else if (open) openCrispOrder(open.dataset.crispOrder);
  else if (imported) openCrispImportedOrder(imported.dataset.importedOrder);
});
$("#crisp-orders-import").addEventListener("click", () => openPriceImport("crisp"));
window.addEventListener("ms:imported-orders", (event) => {
  if (crispOrders.loaded && event.detail?.store === "crisp") loadCrispOrders();
});
// Na een eigen koppeling (#238, product_links.js): de lijst opnieuw, en een open ingelezen bestelling opnieuw met het gekozen product.
window.addEventListener("ms:product-links", (event) => {
  if (event.detail?.store !== "crisp") return;
  if (crispOrders.loaded) loadCrispOrders();
  const open = crispOrders.detail;
  if (open?.imported && !$("#crisp-order-view").hidden) openCrispOrder(open.ref, "imported");
});
$("#crisp-explore-copy").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  try {
    await navigator.clipboard.writeText($("#crisp-explore-output").textContent);
  } catch {
    const range = document.createRange();
    range.selectNodeContents($("#crisp-explore-output"));
    getSelection().removeAllRanges();
    getSelection().addRange(range);
    document.execCommand("copy");
  }
  button.textContent = t("users.copied");
  setTimeout(() => (button.textContent = t("users.copy")), 2000);
});

window.addEventListener("ms:languagechange", () => {
  renderCrispStatus();
  renderCrispExploreSummary();
  if (crispRecipes.loaded) renderCrispRecipes();
  if (crispFavorites.loaded) renderCrispFavorites();
  if (crispOrders.loaded) renderCrispOrders();
});
