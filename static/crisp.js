// Crisp: koppelen (met een code per mail), het mandje tonen en de verkenner.
// Laadt na app.js en picnic.js en gebruikt daar de hulpfuncties van ($, message, jsonFetch, postJson, setBusy, t, ...).

const crispState = { connected: false, codePending: false, report: null };

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

// ---- Het mandje (tabblad Mandje): alleen bekijken, aanpassen en bestellen doe je in de Crisp-app
async function showCrispBasket() {
  const cart = await jsonFetch("/api/crisp/cart");
  const items = cart.items || [];
  const lines = items
    .map((item) => {
      const price = item.price != null ? escapeHtml(formatPrice(item.price)) : "";
      const size = item.unitSize ? ` <span class="muted">${escapeHtml(item.unitSize)}</span>` : "";
      return `<li class="basket-line"><span class="basket-title">${escapeHtml(item.title || "-")}${size}</span><span class="basket-qty">${escapeHtml(item.quantity)}×</span><span class="basket-price">${price}</span><span></span></li>`;
    })
    .join("");
  const total = cart.totalPrice != null ? `<p><strong>${escapeHtml(t("picnic.basket_total", { amount: formatPrice(cart.totalPrice) }))}</strong></p>` : "";
  $("#basket-content").innerHTML = `
    ${items.length ? `<ul class="basket-lines">${lines}</ul>` : `<p class="muted">${escapeHtml(t("basket.crisp_empty"))}</p>`}
    ${total}
    <p class="muted">${escapeHtml(t("basket.crisp_hint"))}</p>`;
}

$("#crisp-form").addEventListener("submit", requestCrispCode);
$("#crisp-code-form").addEventListener("submit", confirmCrispCode);
$("#crisp-check").addEventListener("click", checkCrispConnection);
$("#crisp-unlink").addEventListener("click", unlinkCrisp);
$("#crisp-explore-run").addEventListener("click", runCrispExplore);
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
});
