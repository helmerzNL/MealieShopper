// Prijsarchief importeren (Instellingen > Systeem). De beheerder uploadt een archief met historische prijzen; de server
// controleert het en toont eerst wat erbij komt. Pas na de bevestiging voegt een klus op de server de prijzen toe. Er wordt
// niets vervangen of verwijderd. Zelfde opzet als backup.js (en gebruikt daar backupMegabytes); laadt na app.js en backup.js.

const priceArchiveState = { token: "", summary: null, busy: false, poller: null, finishing: false };
const PRICE_ARCHIVE_POLL_MS = 1000;

function priceArchiveMessage(text, type = "error") {
  message("#price-archive-message", text, type);
}

function priceArchiveShowProgress({ label, value = null, max = null, note = "" }) {
  const bar = $("#price-archive-progress-bar");
  if (value !== null && max) {
    bar.max = max;
    bar.value = value;
  } else {
    bar.removeAttribute("value");
  }
  $("#price-archive-progress-label").textContent = label;
  $("#price-archive-progress-note").textContent = note;
  $("#price-archive-progress").hidden = false;
}

function priceArchiveHideProgress() {
  $("#price-archive-progress").hidden = true;
}

function priceArchiveSetBusy(busy) {
  priceArchiveState.busy = busy;
  ["#price-archive-file", "#price-archive-import", "#price-archive-discard"].forEach((selector) => {
    $(selector).disabled = busy;
  });
}

// XMLHttpRequest meldt hoeveel bytes er zijn verstuurd (fetch niet); daarna controleert de server het archief.
function priceArchiveUpload(file) {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", "/api/price-archive/stage");
    request.setRequestHeader("Content-Type", "application/octet-stream");
    request.upload.onprogress = (event) => {
      if (!event.lengthComputable) return;
      priceArchiveShowProgress({
        label: t("price_archive.progress.uploading"),
        value: event.loaded,
        max: event.total,
        note: t("backup.note.bytes", { done: backupMegabytes(event.loaded), total: backupMegabytes(event.total) }),
      });
    };
    request.upload.onload = () => priceArchiveShowProgress({ label: t("price_archive.progress.reading"), note: t("price_archive.note.reading") });
    request.onerror = () => reject(new Error(t("err.generic")));
    request.onload = () => {
      let data = {};
      try {
        data = JSON.parse(request.responseText);
      } catch (error) {
        data = {};
      }
      if (request.status >= 200 && request.status < 300) resolve(data);
      else reject(new Error(data.error || t("err.server", { status: request.status, hint: request.status === 413 || request.status === 502 ? t("err.proxy_hint") : "" })));
    };
    request.send(file);
  });
}

async function priceArchiveChoose() {
  const file = $("#price-archive-file").files[0];
  await priceArchiveDiscard({ quiet: true });
  if (!file || priceArchiveState.busy) return;
  priceArchiveMessage("");
  priceArchiveSetBusy(true);
  try {
    const summary = await priceArchiveUpload(file);
    priceArchiveState.token = summary.token;
    priceArchiveState.summary = summary;
    priceArchiveRenderSummary(summary);
  } catch (error) {
    $("#price-archive-file").value = "";
    priceArchiveMessage(error.message);
  } finally {
    priceArchiveHideProgress();
    priceArchiveSetBusy(false);
  }
}

function priceArchiveDate(day) {
  const when = new Date(`${day}T00:00:00`);
  return Number.isNaN(when.getTime()) ? String(day) : when.toLocaleDateString(I18N.locale(), { day: "numeric", month: "short", year: "numeric" });
}

// Per winkel één regel: wat er nieuw is, wat er al staat en wat overgeslagen wordt, met de periode van het archief.
function priceArchiveStoreLine(store, stats) {
  const offers = stats.offers || {};
  const prices = stats.prices || {};
  const parts = [];
  if (offers.new) parts.push(tn("price_archive.part.offers_new", offers.new));
  if (prices.new) parts.push(tn("price_archive.part.prices_new", prices.new));
  const duplicate = (offers.duplicate || 0) + (prices.duplicate || 0);
  if (duplicate) parts.push(tn("price_archive.part.duplicate", duplicate));
  if (offers.covered) parts.push(tn("price_archive.part.covered", offers.covered));
  if (prices.kept) parts.push(tn("price_archive.part.kept", prices.kept));
  const period = stats.earliest && stats.latest ? t("price_archive.period", { from: priceArchiveDate(stats.earliest), to: priceArchiveDate(stats.latest) }) : "";
  const name = STORES[store]?.name || store;
  return `<li><strong>${escapeHtml(name)}</strong>${period ? ` <span class="muted">${escapeHtml(period)}</span>` : ""}<br>${escapeHtml(parts.join(", "))}</li>`;
}

function priceArchiveRenderSummary(summary) {
  const meta = [
    summary.source ? t("price_archive.source", { source: summary.source }) : "",
    summary.license ? t("price_archive.license", { license: summary.license }) : "",
  ].filter(Boolean);
  $("#price-archive-source").textContent = meta.join(" · ");
  $("#price-archive-summary").innerHTML = (summary.stores || []).map((stats) => priceArchiveStoreLine(stats.store, stats)).join("");
  const corrections = summary.corrections || {};
  const counted = ["apply", "already", "missing", "conflict"].reduce((total, key) => total + Number(corrections[key] || 0), 0);
  const line = $("#price-archive-corrections");
  line.textContent = counted
    ? t("price_archive.corrections", {
        apply: I18N.formatNumber(corrections.apply || 0),
        already: I18N.formatNumber(corrections.already || 0),
        missing: I18N.formatNumber(corrections.missing || 0),
        conflict: I18N.formatNumber(corrections.conflict || 0),
      })
    : "";
  line.hidden = !counted;
  const nothing = !summary.new;
  $("#price-archive-total").textContent = nothing ? t("price_archive.nothing_new") : tn("price_archive.total", summary.new);
  $("#price-archive-import").hidden = nothing;
  $("#price-archive-confirm").hidden = false;
}

async function priceArchiveDiscard({ quiet = false } = {}) {
  const token = priceArchiveState.token;
  priceArchiveState.token = "";
  priceArchiveState.summary = null;
  $("#price-archive-confirm").hidden = true;
  if (!quiet) $("#price-archive-file").value = "";
  if (!token) return;
  try {
    await fetch(`/api/price-archive/stage/${token}`, { method: "DELETE", credentials: "same-origin" });
  } catch (error) {
    // het klaargezette bestand op de server wordt na een uur vanzelf opgeruimd
  }
}

async function priceArchiveImport() {
  if (!priceArchiveState.token || priceArchiveState.busy) return;
  priceArchiveMessage("");
  priceArchiveSetBusy(true);
  try {
    await jsonFetch("/api/price-archive/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: priceArchiveState.token }),
    });
  } catch (error) {
    priceArchiveMessage(error.message);
    priceArchiveSetBusy(false);
    return;
  }
  priceArchiveState.token = "";
  $("#price-archive-confirm").hidden = true;
  $("#price-archive-file").value = "";
  priceArchiveFollow();
}

function priceArchiveStopFollowing() {
  clearTimeout(priceArchiveState.poller);
  priceArchiveState.poller = null;
}

// Volgt de klus op de server. Een weggevallen verbinding stopt de import niet, dus dan vragen we gewoon opnieuw.
function priceArchiveFollow() {
  priceArchiveStopFollowing();
  const tick = async () => {
    priceArchiveState.poller = null;
    if (!$("#settings-system").classList.contains("is-active")) return; // het scherm is weg: de klus loopt zelf door
    let job;
    try {
      job = (await jsonFetch("/api/price-archive/jobs/latest")).job;
    } catch (error) {
      priceArchiveState.poller = setTimeout(tick, PRICE_ARCHIVE_POLL_MS);
      return;
    }
    if (job && job.status === "running") {
      priceArchiveSetBusy(true);
      priceArchiveShowProgress({ label: t("price_archive.progress.importing"), note: t("price_archive.note.leave") });
      priceArchiveState.poller = setTimeout(tick, PRICE_ARCHIVE_POLL_MS);
      return;
    }
    priceArchiveHideProgress();
    priceArchiveSetBusy(false);
    if (job && !priceArchiveState.finishing) {
      priceArchiveState.finishing = true;
      try {
        await priceArchiveFinish(job);
      } finally {
        priceArchiveState.finishing = false;
      }
    }
  };
  tick();
}

// De uitkomst melden we één keer: tonen, dan wegklikken.
async function priceArchiveFinish(job) {
  try {
    await jsonFetch(`/api/price-archive/jobs/${job.id}`, { method: "DELETE" });
  } catch (error) {
    // dan zie je dezelfde uitkomst de volgende keer nog een keer
  }
  if (job.status === "failed") {
    priceArchiveMessage(job.error || t("err.generic"));
    return;
  }
  const summary = job.summary || {};
  const notes = [
    t("price_archive.done", {
      offers: I18N.formatNumber(summary.offers || 0),
      prices: I18N.formatNumber(summary.prices || 0),
      seconds: backupSeconds(job.seconds),
    }),
  ];
  const applied = summary.corrections?.apply || 0;
  if (applied) notes.push(tn("price_archive.done_corrections", applied));
  priceArchiveMessage(notes.join(" "), "success");
}

// Bij het openen van het scherm: weer aansluiten bij een import die nog loopt (of net klaar is).
function loadPriceArchive() {
  priceArchiveFollow();
}

$("#price-archive-file").addEventListener("change", priceArchiveChoose);
$("#price-archive-import").addEventListener("click", priceArchiveImport);
$("#price-archive-discard").addEventListener("click", () => priceArchiveDiscard());
