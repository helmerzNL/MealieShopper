// Jumbo: koppelen (een regel uit de browserconsole plakken, zoals bij Albert Heijn) en verkennen.
// Laadt na app.js en gebruikt daar de hulpfuncties van ($, message, jsonFetch, setBusy, t, ...).
// De keuze van de supermarkt in de planner staat in app.js (renderStoreChoice).

const jumboState = { connected: false, report: null };

function renderJumboStatus() {
  $("#jumbo-unlink").hidden = !jumboState.connected;
  $("#jumbo-check").hidden = !jumboState.connected;
  message("#jumbo-status", jumboState.connected ? t("jumbo.linked") : t("jumbo.not_linked"), jumboState.connected ? "success" : "error");
}

async function refreshJumboStatus() {
  try {
    const data = await jsonFetch("/api/jumbo/auth/status");
    jumboState.connected = Boolean(data.connected);
    state.linkedStores.jumbo = jumboState.connected;
    renderJumboStatus();
  } catch (error) {
    message("#jumbo-status", error.message);
  }
  renderStoreChoice();
}

// ---- Het Jumbo-mandje in het tabblad Mandje (alleen lezen)
async function showJumboBasket() {
  const cart = await jsonFetch("/api/jumbo/cart");
  const items = cart.items || [];
  const lines = items
    .map((item) => {
      const price = item.price ? escapeHtml(formatPrice(item.price)) : "";
      return `<li class="basket-line"><span class="basket-title">${escapeHtml(item.title || "-")}</span><span class="basket-qty">${escapeHtml(item.quantity)}\u00d7</span><span class="basket-price">${price}</span><span></span></li>`;
    })
    .join("");
  const total = cart.totalPrice ? `<p><strong>${escapeHtml(t("picnic.basket_total", { amount: formatPrice(cart.totalPrice) }))}</strong></p>` : "";
  $("#basket-content").innerHTML = `
    ${items.length ? `<ul class="basket-lines">${lines}</ul>` : `<p class="muted">${escapeHtml(t("basket.jumbo_empty"))}</p>`}
    ${total}
    <p class="muted">${escapeHtml(t("basket.jumbo_hint"))}</p>`;
}

async function checkJumboConnection() {
  message("#jumbo-status", t("ah.checking"), "success");
  try {
    const data = await jsonFetch("/api/jumbo/auth/status?verify=1");
    if (data.verified) message("#jumbo-status", t("jumbo.works"), "success");
    else message("#jumbo-status", data.connected ? t("jumbo.broken", { error: data.error || "" }) : t("jumbo.not_linked"));
  } catch (error) {
    message("#jumbo-status", error.message);
  }
}

async function linkJumbo() {
  const button = $("#jumbo-link");
  const text = $("#jumbo-code").value.trim();
  if (!text) {
    message("#jumbo-message", t("jumbo.paste_first"));
    return;
  }
  setBusy(button, true, t("jumbo.linking"));
  message("#jumbo-message", "");
  try {
    await jsonFetch("/api/jumbo/auth", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code: text }),
    });
    $("#jumbo-code").value = "";
    message("#jumbo-message", t("jumbo.saved"), "success");
    await refreshJumboStatus();
  } catch (error) {
    message("#jumbo-message", error.message);
  } finally {
    setBusy(button, false, t("jumbo.linking"));
  }
}

async function unlinkJumbo() {
  try {
    await jsonFetch("/api/jumbo/auth/logout", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    jumboState.connected = false;
    state.linkedStores.jumbo = false;
    renderJumboStatus();
    renderStoreChoice();
  } catch (error) {
    message("#jumbo-status", error.message);
  }
}

// ---- Verkenner (alleen de beheerder)
function renderJumboExploreSummary() {
  const report = jumboState.report;
  if (report) $("#jumbo-explore-summary").textContent = t("explore.summary", { ok: report.ok, total: report.probes.length });
}

async function runJumboExplore() {
  const button = $("#jumbo-explore-run");
  setBusy(button, true);
  message("#jumbo-explore-message", "");
  try {
    jumboState.report = await jsonFetch("/api/jumbo/explore", { method: "POST" });
    renderJumboExploreSummary();
    $("#jumbo-explore-output").textContent = JSON.stringify(jumboState.report, null, 2);
    $("#jumbo-explore-report").hidden = false;
    if (!jumboState.report.linked) message("#jumbo-explore-message", t("jumbo.explore_not_linked"));
  } catch (error) {
    message("#jumbo-explore-message", error.message);
  } finally {
    setBusy(button, false);
  }
}

$("#jumbo-link").addEventListener("click", linkJumbo);
$("#jumbo-check").addEventListener("click", checkJumboConnection);
$("#jumbo-unlink").addEventListener("click", unlinkJumbo);
$("#jumbo-explore-run").addEventListener("click", runJumboExplore);
$("#jumbo-explore-copy").addEventListener("click", async (event) => {
  const text = $("#jumbo-explore-output").textContent;
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const range = document.createRange();
    range.selectNodeContents($("#jumbo-explore-output"));
    getSelection().removeAllRanges();
    getSelection().addRange(range);
    document.execCommand("copy");
  }
  event.currentTarget.textContent = t("users.copied");
  setTimeout(() => (event.currentTarget.textContent = t("users.copy")), 2000);
});

window.addEventListener("ms:languagechange", () => {
  renderJumboStatus();
  renderStoreChoice();
  renderJumboExploreSummary();
});
