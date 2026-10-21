// Picnic: koppelen (met een code per sms of e-mail) en de keuze van de supermarkt in de planner.
// Laadt na app.js en gebruikt daar de hulpfuncties van ($, message, jsonFetch, setBusy, t, ...).

const picnicState = { connected: false, codePending: false, report: null, slots: [] };

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
  message("#picnic-status", picnicState.connected ? t("picnic.linked") : t("picnic.not_linked"), picnicState.connected ? "success" : "error");
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
    if (data.verified) message("#picnic-status", t("picnic.works"), "success");
    else message("#picnic-status", data.connected ? t("picnic.broken", { error: data.error || "" }) : t("picnic.not_linked"));
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
  renderPicnicStatus();
  renderStoreChoice();
  renderPicnicExploreSummary();
});
