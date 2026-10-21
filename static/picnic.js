// Picnic: koppelen (met een code per sms of e-mail) en de keuze van de supermarkt in de planner.
// Laadt na app.js en gebruikt daar de hulpfuncties van ($, message, jsonFetch, setBusy, t, ...).

const picnicState = { connected: false, codePending: false, report: null };

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

// ---- Bezorgmomenten onder het gevulde mandje (alleen lezen: kiezen doe je in de Picnic-app)
function formatSlot(slot) {
  const start = new Date(slot.windowStart);
  const end = new Date(slot.windowEnd);
  if (Number.isNaN(start.getTime())) return "";
  const day = start.toLocaleDateString(I18N.locale(), { weekday: "short", day: "numeric", month: "short" });
  const time = (date) => date.toLocaleTimeString(I18N.locale(), { hour: "2-digit", minute: "2-digit" });
  return Number.isNaN(end.getTime()) ? `${day} ${time(start)}` : `${day} ${time(start)}\u2013${time(end)}`;
}

async function showPicnicSlots() {
  let slots = [];
  try {
    slots = (await jsonFetch("/api/picnic/slots?limit=6")).slots || [];
  } catch {
    return; // De bezorgmomenten zijn een extraatje: het gevulde mandje blijft staan.
  }
  const target = $("#cart-result");
  if (!slots.length || !target || state.store !== "picnic") return;
  const items = slots
    .map((slot) => {
      const minimum = slot.minimumOrder ? ` <span class="muted">(${escapeHtml(t("picnic.slot_minimum", { amount: formatPrice(slot.minimumOrder) }))})</span>` : "";
      return `<li>${escapeHtml(formatSlot(slot))}${minimum}</li>`;
    })
    .join("");
  target.insertAdjacentHTML(
    "beforeend",
    `<div class="picnic-slots"><strong>${escapeHtml(t("picnic.slots_title"))}</strong><ul class="cart-lines">${items}</ul><p class="muted">${escapeHtml(t("picnic.slots_hint"))}</p></div>`
  );
}

// ---- Supermarkt kiezen in de planner: alleen zichtbaar als Picnic gekoppeld is
function renderStoreChoice() {
  const slot = $("#cart-store-slot");
  if (!slot) return;
  if (!picnicState.connected) {
    state.store = "ah";
    slot.innerHTML = "";
  } else {
    slot.innerHTML = `
      <select id="cart-store" class="store-select" aria-label="${escapeHtml(t("cart.store"))}">
        <option value="ah"${state.store === "ah" ? " selected" : ""}>Albert Heijn</option>
        <option value="picnic"${state.store === "picnic" ? " selected" : ""}>Picnic</option>
      </select>`;
    $("#cart-store").addEventListener("change", (event) => {
      state.store = event.target.value === "picnic" ? "picnic" : "ah";
      try {
        localStorage.setItem("ms-store", state.store);
      } catch {
        // Zonder opslag onthoudt de pagina de keuze alleen tot je hem herlaadt.
      }
      const button = $("#add-cart");
      if (button) button.textContent = cartButtonLabel();
    });
  }
  const button = $("#add-cart");
  if (button) button.textContent = cartButtonLabel();
}

try {
  if (localStorage.getItem("ms-store") === "picnic") state.store = "picnic";
} catch {
  // Geen opslag beschikbaar: AH blijft de standaard.
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
