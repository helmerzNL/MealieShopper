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
  ["#price-archive-file", "#price-archive-import", "#price-archive-discard", "#price-archive-replace"].forEach((selector) => {
    $(selector).disabled = busy;
  });
}

// In stukken (upload.js): een archief van 100 MB in één verzoek komt niet door elke proxy. Daarna controleert de server
// het archief.
function priceArchiveUpload(file) {
  return uploadInChunks(file, "/api/price-archive/uploads", {
    onProgress: (done, total) =>
      priceArchiveShowProgress({
        label: t("price_archive.progress.uploading"),
        value: done,
        max: total,
        note: t("backup.note.bytes", { done: backupMegabytes(done), total: backupMegabytes(total) }),
      }),
    onChecking: () => priceArchiveShowProgress({ label: t("price_archive.progress.reading"), note: t("price_archive.note.reading") }),
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

// Met het vinkje Vervangen gaan de oude archiefregels er eerst uit; de server rekende beide uitkomsten al uit.
function priceArchiveRenderSummary(summary) {
  const existing = summary.archived || {};
  const archived = Number(existing.offers || 0) + Number(existing.prices || 0);
  const replace = Boolean(archived && $("#price-archive-replace").checked);
  $("#price-archive-replace-field").hidden = !archived;
  $("#price-archive-replace-note").hidden = !archived;
  $("#price-archive-replace-note").textContent = archived
    ? t("price_archive.replace_note", { offers: I18N.formatNumber(existing.offers || 0), prices: I18N.formatNumber(existing.prices || 0) })
    : "";
  const outcome = replace && summary.replace ? summary.replace : summary;
  $("#price-archive-keeps").hidden = replace;
  $("#price-archive-keeps-replace").hidden = !replace;
  const meta = [
    summary.source ? t("price_archive.source", { source: summary.source }) : "",
    summary.license ? t("price_archive.license", { license: summary.license }) : "",
  ].filter(Boolean);
  $("#price-archive-source").textContent = meta.join(" · ");
  $("#price-archive-summary").innerHTML = (outcome.stores || []).map((stats) => priceArchiveStoreLine(stats.store, stats)).join("");
  const tidied = summary.tidied || {};
  const linked = Number(tidied.exact || 0) + Number(tidied.approximate || 0);
  const tidiedLine = $("#price-archive-tidied");
  const anyTidied = linked + Number(tidied.groups || 0) + Number(tidied.unknownPack || 0);
  tidiedLine.textContent = anyTidied
    ? t("price_archive.tidied", {
        linked: I18N.formatNumber(linked),
        groups: I18N.formatNumber(tidied.groups || 0),
        prices: I18N.formatNumber(tidied.unknownPack || 0),
      })
    : "";
  tidiedLine.hidden = !anyTidied;
  const corrections = outcome.corrections || {};
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
  const nothing = !outcome.new && !replace;
  $("#price-archive-total").textContent = nothing ? t("price_archive.nothing_new") : tn("price_archive.total", outcome.new);
  $("#price-archive-import").hidden = nothing;
  $("#price-archive-confirm").hidden = false;
}

async function priceArchiveDiscard({ quiet = false } = {}) {
  const token = priceArchiveState.token;
  priceArchiveState.token = "";
  priceArchiveState.summary = null;
  $("#price-archive-confirm").hidden = true;
  $("#price-archive-replace").checked = false;
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
      body: JSON.stringify({ token: priceArchiveState.token, replace: $("#price-archive-replace").checked && !$("#price-archive-replace-field").hidden }),
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
  const removed = Number(summary.removed?.offers || 0) + Number(summary.removed?.prices || 0);
  if (summary.replaced && removed) notes.push(tn("price_archive.done_removed", removed));
  priceArchiveMessage(notes.join(" "), "success");
}

// Bij het openen van het scherm: weer aansluiten bij een import die nog loopt (of net klaar is).
function loadPriceArchive() {
  priceArchiveFollow();
}

$("#price-archive-file").addEventListener("change", priceArchiveChoose);
$("#price-archive-import").addEventListener("click", priceArchiveImport);
$("#price-archive-discard").addEventListener("click", () => priceArchiveDiscard());
$("#price-archive-replace").addEventListener("change", () => {
  if (priceArchiveState.summary) priceArchiveRenderSummary(priceArchiveState.summary);
});
