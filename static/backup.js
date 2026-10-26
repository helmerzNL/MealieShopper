// Back-up en terugzetten (Instellingen > Systeem). De beheerder maakt hier één bestand met alle gegevens en zet het terug.
// Volgt shared/guidelines/backup-restore.md van Flux76HQ/App-Guidance: niets traags is onzichtbaar (elke fase heeft een
// naam en, waar te tellen, een getal en een balk), en het terugzetten is werk van de server: de browser start het en kijkt
// mee, dus de pagina verlaten of sluiten stopt niets. Laadt na app.js en gebruikt daar de hulpfuncties ($, message, t, ...).

const backupState = { info: null, token: "", summary: null, busy: false, poller: null, finishing: false, timing: {} };
const BACKUP_POLL_MS = 1000;
const BACKUP_TIMING_KEY = "ms_backup_timing";

function backupMessage(text, type = "error") {
  message("#backup-message", text, type);
}

function backupMegabytes(bytes) {
  return `${(bytes / (1024 * 1024)).toLocaleString(I18N.locale(), { maximumFractionDigits: 1, minimumFractionDigits: 1 })} MB`;
}

function backupSeconds(seconds) {
  return Number(seconds || 0).toLocaleString(I18N.locale(), { maximumFractionDigits: 1, minimumFractionDigits: 1 });
}

// Een balk met getal alleen als het totaal bekend is; zonder totaal beweegt hij en zegt hij niet meer dan hij weet.
function backupShowProgress({ label, value = null, max = null, note = "" }) {
  const bar = $("#backup-progress-bar");
  if (value !== null && max) {
    bar.max = max;
    bar.value = value;
  } else {
    bar.removeAttribute("value");
  }
  $("#backup-progress-label").textContent = label;
  $("#backup-progress-note").textContent = note;
  $("#backup-progress").hidden = false;
}

function backupHideProgress() {
  $("#backup-progress").hidden = true;
}

function backupSetBusy(busy) {
  backupState.busy = busy;
  ["#backup-make", "#backup-file", "#backup-restore", "#backup-discard", "#backup-links"].forEach((selector) => {
    $(selector).disabled = busy;
  });
}

function backupRenderLinksFields() {
  $("#backup-passphrase-fields").hidden = !$("#backup-links").checked;
}

async function backupError(response) {
  let text = "";
  try {
    text = (await response.json()).error || "";
  } catch (error) {
    text = "";
  }
  return new Error(text || t("err.server", { status: response.status, hint: "" }));
}

// --- maken -------------------------------------------------------------------------------------------------------

async function backupMake() {
  if (backupState.busy) return;
  const links = $("#backup-links").checked;
  const passphrase = $("#backup-passphrase").value;
  const minimum = backupState.info?.minPassphraseLength || 8;
  backupMessage("");
  $("#backup-timing").textContent = "";
  if (links) {
    if (passphrase.length < minimum) return backupMessage(t("backup.passphrase_short", { minimum }));
    if (passphrase !== $("#backup-passphrase-repeat").value) return backupMessage(t("backup.passphrase_mismatch"));
  }
  backupSetBusy(true);
  try {
    // De server bouwt het bestand op schijf; zolang hij bezig is valt er niets te tellen.
    backupShowProgress({ label: t("backup.progress.making") });
    const response = await fetch("/api/backup", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ links, passphrase }),
    });
    if (!response.ok) throw await backupError(response);
    const total = Number(response.headers.get("Content-Length")) || 0;
    const serverSeconds = Number(response.headers.get("X-Backup-Seconds")) || 0;
    const startedDownload = performance.now();
    const chunks = [];
    let received = 0;
    const reader = response.body.getReader();
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      chunks.push(value);
      received += value.length;
      backupShowProgress({
        label: t("backup.progress.downloading"),
        value: received,
        max: total,
        note: total
          ? t("backup.note.bytes", { done: backupMegabytes(received), total: backupMegabytes(total) })
          : t("backup.note.bytes_only", { done: backupMegabytes(received) }),
      });
    }
    const name = /filename="?([^";]+)"?/.exec(response.headers.get("Content-Disposition") || "")?.[1] || "mealieshopper-backup.json";
    const link = document.createElement("a");
    link.href = URL.createObjectURL(new Blob(chunks, { type: "application/json" }));
    link.download = name;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 10000);
    $("#backup-timing").textContent = t("backup.timing_make", {
      server: backupSeconds(serverSeconds),
      download: backupSeconds((performance.now() - startedDownload) / 1000),
      size: backupMegabytes(received),
    });
    backupMessage(t("backup.made", { name }), "success");
    $("#backup-passphrase").value = "";
    $("#backup-passphrase-repeat").value = "";
  } catch (error) {
    backupMessage(error.message);
  } finally {
    backupHideProgress();
    backupSetBusy(false);
  }
}

// --- kiezen en controleren ----------------------------------------------------------------------------------------

// XMLHttpRequest meldt hoeveel bytes er zijn verstuurd (fetch niet); daarna leest de server het bestand en is er niets te tellen.
function backupUpload(file) {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", "/api/backup/stage");
    request.setRequestHeader("Content-Type", "application/octet-stream");
    request.upload.onprogress = (event) => {
      if (!event.lengthComputable) return;
      backupShowProgress({
        label: t("backup.progress.uploading"),
        value: event.loaded,
        max: event.total,
        note: t("backup.note.bytes", { done: backupMegabytes(event.loaded), total: backupMegabytes(event.total) }),
      });
    };
    request.upload.onload = () => backupShowProgress({ label: t("backup.progress.reading") });
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

async function backupChoose() {
  const file = $("#backup-file").files[0];
  await backupDiscard({ quiet: true });
  if (!file || backupState.busy) return;
  backupMessage("");
  $("#backup-timing").textContent = "";
  backupSetBusy(true);
  const started = performance.now();
  try {
    const summary = await backupUpload(file);
    backupState.timing = { upload: (performance.now() - started) / 1000 };
    backupState.token = summary.token;
    backupState.summary = summary;
    backupRenderSummary(summary);
  } catch (error) {
    $("#backup-file").value = "";
    backupMessage(error.message);
  } finally {
    backupHideProgress();
    backupSetBusy(false);
  }
}

function backupRenderSummary(summary) {
  const when = summary.createdAt ? new Date(summary.createdAt) : null;
  $("#backup-made").textContent = t("backup.made_at", {
    date: when && !Number.isNaN(when.getTime()) ? when.toLocaleString(I18N.locale(), { dateStyle: "medium", timeStyle: "short" }) : "?",
    version: summary.appVersion || "?",
  });
  $("#backup-summary").innerHTML = Object.keys(summary.counts)
    .map((group) => `<li>${escapeHtml(t(`backup.count.${group}`, { count: Number(summary.counts[group]).toLocaleString(I18N.locale()) }))}</li>`)
    .join("");
  const names = summary.links.map((link) => t(`backup.link.${link}`)).join(", ");
  $("#backup-links-line").textContent = summary.links.length ? t("backup.links_in", { links: names }) : t("backup.links_none");
  $("#backup-restore-passphrase-field").hidden = !summary.needsPassphrase;
  $("#backup-restore-passphrase").value = "";
  $("#backup-confirm").hidden = false;
}

async function backupDiscard({ quiet = false } = {}) {
  const token = backupState.token;
  backupState.token = "";
  backupState.summary = null;
  $("#backup-confirm").hidden = true;
  if (!quiet) $("#backup-file").value = "";
  if (!token) return;
  try {
    await fetch(`/api/backup/stage/${token}`, { method: "DELETE", credentials: "same-origin" });
  } catch (error) {
    // het bestand op de server wordt na een uur vanzelf opgeruimd
  }
}

// --- terugzetten --------------------------------------------------------------------------------------------------

async function backupRestore() {
  if (!backupState.token || backupState.busy) return;
  backupMessage("");
  backupSetBusy(true);
  try {
    await jsonFetch("/api/backup/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: backupState.token, passphrase: $("#backup-restore-passphrase").value }),
    });
  } catch (error) {
    // Een verkeerd wachtwoord laat het bestand en het token staan: de beheerder kan het meteen opnieuw proberen.
    backupMessage(error.message);
    backupSetBusy(false);
    return;
  }
  backupRemember(BACKUP_TIMING_KEY, JSON.stringify(backupState.timing));
  backupState.token = "";
  $("#backup-confirm").hidden = true;
  $("#backup-file").value = "";
  backupFollow();
}

function backupRemember(key, value) {
  try {
    sessionStorage.setItem(key, value);
  } catch (error) {
    // zonder opslag ziet de beheerder de tijden na het herladen niet; dat is prima
  }
}

function backupStoredTiming() {
  try {
    const stored = sessionStorage.getItem(BACKUP_TIMING_KEY);
    sessionStorage.removeItem(BACKUP_TIMING_KEY);
    return stored ? JSON.parse(stored) : null;
  } catch (error) {
    return null;
  }
}

function backupStopFollowing() {
  clearTimeout(backupState.poller);
  backupState.poller = null;
}

// Volgt de klus op de server: elke seconde vragen. Een weggevallen verbinding stopt het terugzetten niet, dus dan vragen we gewoon opnieuw.
function backupFollow() {
  backupStopFollowing();
  const tick = async () => {
    backupState.poller = null;
    if (!$("#settings-system").classList.contains("is-active")) return; // het scherm is weg: de klus loopt zelf door
    let job;
    try {
      job = (await jsonFetch("/api/backup/jobs/latest")).job;
    } catch (error) {
      backupState.poller = setTimeout(tick, BACKUP_POLL_MS);
      return;
    }
    if (job && job.status === "running") {
      backupSetBusy(true);
      backupRenderJob(job);
      backupState.poller = setTimeout(tick, BACKUP_POLL_MS);
      return;
    }
    backupHideProgress();
    backupSetBusy(false);
    if (job && !backupState.finishing) {
      backupState.finishing = true;
      try {
        await backupFinish(job);
      } finally {
        backupState.finishing = false;
      }
    }
  };
  tick();
}

function backupRenderJob(job) {
  const leave = t("backup.note.leave");
  if (job.phase === "checking") {
    backupShowProgress({
      label: t("backup.progress.checking"),
      value: job.step,
      max: job.steps,
      note: `${t("backup.note.step", { step: job.step, steps: job.steps })} · ${leave}`,
    });
  } else {
    backupShowProgress({ label: t("backup.progress.replacing"), note: leave });
  }
}

// De uitkomst melden we één keer: tonen, dan wegklikken. Lukt het wegklikken niet, dan niet herladen: de pagina zou dezelfde
// afgeronde klus telkens opnieuw laten zien.
async function backupFinish(job) {
  let dismissed = true;
  try {
    await jsonFetch(`/api/backup/jobs/${job.id}`, { method: "DELETE" });
  } catch (error) {
    dismissed = false;
  }
  if (job.status === "failed") {
    backupMessage(job.error || t("err.generic"));
    return;
  }
  const summary = job.summary || {};
  const notes = [t("backup.done")];
  if (summary.skippedProfiles) notes.push(t("backup.skipped_profiles", { count: summary.skippedProfiles }));
  if (summary.linksKept) notes.push(t("backup.links_kept"));
  backupMessage(notes.join(" "), "success");
  const earlier = backupStoredTiming() || {};
  $("#backup-timing").textContent = t(earlier.upload ? "backup.timing_restore" : "backup.timing_restore_server", {
    upload: backupSeconds(earlier.upload),
    checking: backupSeconds(job.checkingSeconds),
    replacing: backupSeconds(job.replacingSeconds),
  });
  if (dismissed) {
    backupRemember(BACKUP_TIMING_KEY, JSON.stringify({ ...earlier, restoreText: $("#backup-timing").textContent }));
    setTimeout(() => window.location.reload(), 2500); // de voorkeuren, taal en thema kunnen veranderd zijn
  }
}

// Bij het openen van het scherm: weer aansluiten bij een klus die nog loopt (of net klaar is) en de tijden van de vorige keer tonen.
async function loadBackup() {
  if (!backupState.info) {
    try {
      backupState.info = await jsonFetch("/api/backup");
    } catch (error) {
      backupMessage(error.message);
      return;
    }
  }
  try {
    const stored = sessionStorage.getItem(BACKUP_TIMING_KEY);
    const earlier = stored ? JSON.parse(stored) : null;
    if (earlier?.restoreText) {
      $("#backup-timing").textContent = earlier.restoreText;
      sessionStorage.removeItem(BACKUP_TIMING_KEY);
      backupMessage(t("backup.done"), "success");
    }
  } catch (error) {
    // zonder opslag valt dit weg
  }
  backupFollow();
}

$("#backup-links").addEventListener("change", backupRenderLinksFields);
$("#backup-make").addEventListener("click", backupMake);
$("#backup-file").addEventListener("change", backupChoose);
$("#backup-restore").addEventListener("click", backupRestore);
$("#backup-discard").addEventListener("click", () => backupDiscard());
backupRenderLinksFields();
