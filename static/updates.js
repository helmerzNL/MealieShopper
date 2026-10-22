// Updatecheck: kijkt of er een nieuwere versie is en meldt dat bij de beheerder (bolletje, melding en Beheer > Updates).
// Laadt na app.js en gebruikt daar de hulpfuncties van ($, message, jsonFetch, t, th, escapeHtml, ...).

const updateState = { data: null, loading: false, dismissedKey: "ms_update_dismissed" };

function updateIsOwner() {
  const auth = state.auth || {};
  const signedIn = Boolean(auth.authenticated) || !auth.enabled;
  return signedIn && (!auth.enabled || auth.isOwner !== false);
}

// Na het inloggen één keer kijken; de server onthoudt het antwoord, dus dit raakt GitHub niet bij elke paginaweergave.
function startUpdateCheck() {
  if (updateIsOwner() && !updateState.data) loadUpdate();
  else renderUpdate();
}

function updateDismissedVersion() {
  try {
    return localStorage.getItem(updateState.dismissedKey) || "";
  } catch (error) {
    return "";
  }
}

function rememberUpdateDismissed(version) {
  try {
    localStorage.setItem(updateState.dismissedKey, version);
  } catch (error) {
    // zonder opslag komt de melding bij de volgende keer terug; dat is prima
  }
}

async function loadUpdate({ force = false } = {}) {
  if (updateState.loading) return;
  updateState.loading = true;
  renderUpdate();
  try {
    updateState.data = await jsonFetch(force ? "/api/update/check" : "/api/update", force ? { method: "POST" } : {});
  } catch (error) {
    updateState.data = { error: true };
  } finally {
    updateState.loading = false;
    renderUpdate();
  }
}

function renderUpdate() {
  const data = updateState.data;
  const owner = updateIsOwner();
  const available = Boolean(owner && data?.available && data.latest);
  const dismissed = available && updateDismissedVersion() === data.latest;

  $("#update-dot-tab").hidden = !available;
  $("#update-dot-subtab").hidden = !available;
  $("#update-dot-user").hidden = !available;
  $("#update-dot-settings").hidden = !available;
  const banner = $("#update-banner");
  banner.hidden = !available || dismissed;
  if (available) {
    $("#update-banner-text").textContent = t("update.banner", { latest: `v${data.latest}`, current: `v${data.current}` });
  }

  const status = $("#update-status");
  $("#update-check").disabled = updateState.loading;
  $("#update-check").textContent = t(updateState.loading ? "update.checking" : "update.check");
  if (!data) {
    $("#update-howto").hidden = true;
    $("#update-notes").hidden = true;
    return;
  }
  $("#update-current").textContent = data.current ? `v${data.current}` : "-";
  $("#update-latest").textContent = data.latest ? `v${data.latest}` : "-";
  $("#update-notes").hidden = !(data.latest && data.url);
  if (data.url) $("#update-notes").href = data.url;
  $("#update-howto").hidden = !data.available;

  if (data.enabled === false) {
    message("#update-status", t("update.disabled"), "warning");
  } else if (data.error && !data.latest) {
    message("#update-status", t("update.failed", { detail: data.errorDetail || "?" }), "error");
  } else if (data.available) {
    message("#update-status", t("update.available", { version: `v${data.latest}` }), "warning");
  } else if (!data.latest) {
    message("#update-status", t("update.no_release"), "success");
  } else {
    message("#update-status", t("update.up_to_date"), "success");
  }
  if (data.error && data.latest) status.insertAdjacentHTML("beforeend", `<div class="message error">${escapeHtml(t("update.failed", { detail: data.errorDetail || "?" }))}</div>`);
  const checked = data.checkedAt ? new Date(data.checkedAt) : null;
  $("#update-checked").textContent =
    checked && !Number.isNaN(checked.getTime())
      ? t("update.checked", { time: checked.toLocaleString(document.documentElement.lang || undefined) })
      : "";
}

$("#update-check").addEventListener("click", () => loadUpdate({ force: true }));
$("#update-banner-open").addEventListener("click", () => openSettings("settings-updates"));
$("#update-banner-dismiss").addEventListener("click", () => {
  if (updateState.data?.latest) rememberUpdateDismissed(updateState.data.latest);
  renderUpdate();
});
