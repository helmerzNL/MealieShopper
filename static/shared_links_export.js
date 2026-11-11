// De export van je eigen koppelingen voor een pull request (shared_links_export.py), alleen voor de beheerder. De knop
// `#loose-export` staat op de pagina Losse aankopen (loose_names.js verhuist hem naar de kop) en toont hoeveel nieuwe koppelingen er
// klaarstaan. Een klik opent `#export-dialog`: de lijst met een vinkje per koppeling, de afwijkingen van het bestand (standaard uit),
// de download en de stappen. De app praat niet met GitHub: de gebruiker stelt de wijziging zelf voor. Laadt na loose_names.js.
//
// Bovenaan staan de zuivere functies (de teller, de sleutels in het verzoek, de HTML van de dialoog), daaronder het ophalen en de knoppen.

const sharedExport = { store: "", pending: null, run: 0 };

// ---- zuivere functies

// Zoveel nieuwe koppelingen staan er klaar. Afwijkingen tellen niet mee: die zijn een keuze, geen nieuws.
function exportCount(pending) {
  return Array.isArray(pending?.new) ? pending.new.length : 0;
}

function exportButtonLabel(pending) {
  return pending ? t("loose.export_count", { count: exportCount(pending) }) : t("loose.export");
}

// De sleutels die de dialoog aanvinkt als hij opent: alle nieuwe koppelingen, nooit een afwijking.
function exportDefaults(pending) {
  return (pending?.new || []).map((item) => item.key);
}

// De sleutels voor het verzoek: wat aangevinkt is (`checked`, een lijst of Set sleutels) en ook echt klaarstaat, in de volgorde van de lijst.
function exportKeys(pending, checked) {
  const wanted = new Set(checked || []);
  return [...(pending?.new || []), ...(pending?.conflicts || [])].map((item) => item.key).filter((key) => wanted.has(key));
}

function exportRequestBody(store, pending, checked) {
  return { store, keys: exportKeys(pending, checked) };
}

function exportProduct(title, unitSize) {
  return [title, unitSize].filter(Boolean).join(" · ");
}

function exportRowHtml(item, checked, extra) {
  return `
        <li>
          <label class="export-row">
            <input type="checkbox" data-export-key="${escapeHtml(item.key)}"${checked ? " checked" : ""}>
            <span class="export-row__body">
              <strong>${escapeHtml(item.name)}</strong>
              ${extra.map((line) => `<small>${escapeHtml(line)}</small>`).join("")}
            </span>
          </label>
        </li>`;
}

function exportNewHtml(pending) {
  const items = (pending.new || []).map((item) => exportRowHtml(item, true, [exportProduct(item.title, item.unitSize)])).join("");
  if (!items) return "";
  return `
      <section class="export-section">
        <h4>${escapeHtml(tn("loose.export_new", pending.new.length))}</h4>
        <ul class="export-list">${items}</ul>
      </section>`;
}

// Een afwijking: het product uit het bestand naast het jouwe. Het vinkje staat uit, want aanvinken vervangt een regel van het bestand.
function exportConflictsHtml(pending) {
  const items = (pending.conflicts || [])
    .map((item) =>
      exportRowHtml(item, false, [
        t("loose.export_yours", { product: exportProduct(item.title, item.unitSize) }),
        t("loose.export_theirs", { product: item.sharedTitle || item.sharedProductId }),
      ])
    )
    .join("");
  if (!items) return "";
  return `
      <section class="export-section">
        <h4>${escapeHtml(t("loose.export_conflicts"))}</h4>
        <p class="muted">${escapeHtml(t("loose.export_conflicts_note"))}</p>
        <ul class="export-list">${items}</ul>
      </section>`;
}

// Eigen namen met dezelfde sleutel en een ander product: allebei weggelaten, en dit zegt waarom ze ontbreken.
function exportAmbiguousHtml(pending) {
  const items = (pending.ambiguous || [])
    .map((entry) => `<li class="export-note">${escapeHtml(t("loose.export_ambiguous", { names: (entry.names || []).join(", ") }))}</li>`)
    .join("");
  if (!items) return "";
  return `
      <section class="export-section">
        <h4>${escapeHtml(t("loose.export_ambiguous_title"))}</h4>
        <ul class="export-list">${items}</ul>
      </section>`;
}

// Alleen een https-adres wordt een link, en die opent in een nieuw tabblad zonder dat de pagina daar bij kan.
function exportLinkHtml(pending, store) {
  const url = String(pending.fileUrl || "");
  if (!/^https:\/\//i.test(url)) return "";
  const text = t("loose.export_link", { file: `${store}.json` });
  return `<p><a href="${escapeHtml(url)}" target="_blank" rel="noopener">${escapeHtml(text)}</a></p>`;
}

function exportStepsHtml(pending, store) {
  return `
      <section class="export-section">
        <h4>${escapeHtml(t("loose.export_steps_title"))}</h4>
        <ol class="export-steps">
          <li>${escapeHtml(t("loose.export_step1"))}</li>
          <li>${escapeHtml(t("loose.export_step2"))}</li>
          <li>${escapeHtml(t("loose.export_step3"))}</li>
        </ol>
        ${exportLinkHtml(pending, store)}
      </section>`;
}

function exportDialogHtml(pending, store) {
  const intro = `<p class="muted">${escapeHtml(t("loose.export_intro", { store: storeDisplayName(store) }))}</p>`;
  return intro + exportNewHtml(pending) + exportConflictsHtml(pending) + exportAmbiguousHtml(pending) + exportStepsHtml(pending, store);
}

// ---- de knop

function exportRenderButton() {
  const button = $("#loose-export");
  if (!button) return;
  button.textContent = exportButtonLabel(sharedExport.pending);
  button.disabled = exportCount(sharedExport.pending) === 0;
}

// De stand van de winkel ophalen en de knop tekenen. Een nieuwere vraag (andere winkel, nieuwe koppeling) maakt een oud antwoord waardeloos.
async function exportRefresh(store) {
  if (!store) return;
  const run = ++sharedExport.run;
  if (store !== sharedExport.store) {
    sharedExport.store = store;
    sharedExport.pending = null;
    exportRenderButton();
  }
  try {
    const pending = await jsonFetch(`/api/shared-links/export?${new URLSearchParams({ store })}`);
    if (run !== sharedExport.run) return;
    sharedExport.pending = pending;
  } catch (error) {
    if (run !== sharedExport.run) return;
    sharedExport.pending = null;
    looseMessage(error.message);
  }
  exportRenderButton();
}

// ---- de dialoog

function exportChecked() {
  return [...document.querySelectorAll("#export-dialog-body [data-export-key]")].filter((box) => box.checked).map((box) => box.dataset.exportKey);
}

function exportRenderDownload() {
  const button = $("#export-download");
  button.textContent = t("loose.export_download", { file: `${sharedExport.store}.json` });
  button.disabled = exportKeys(sharedExport.pending, exportChecked()).length === 0;
}

function exportOpen() {
  const dialog = $("#export-dialog");
  $("#export-dialog-body").innerHTML = exportDialogHtml(sharedExport.pending, sharedExport.store);
  message("#export-dialog-message", "");
  exportRenderDownload();
  if (!dialog.open) dialog.showModal();
}

async function exportOnOpen(button) {
  if (button.disabled || !sharedExport.store) return;
  looseMessage("");
  setBusy(button, true);
  try {
    sharedExport.pending = await jsonFetch(`/api/shared-links/export?${new URLSearchParams({ store: sharedExport.store })}`);
  } catch (error) {
    looseMessage(error.message);
    return;
  } finally {
    setBusy(button, false);
    exportRenderButton();
  }
  exportOpen();
}

// De fout van de server (`{error}`) als tekst, of de gewone melding bij een antwoord zonder die vorm.
async function exportFailure(response) {
  try {
    const data = await response.json();
    if (data?.error) return String(data.error);
  } catch (error) {
    // geen JSON: de gewone melding hieronder
  }
  return t("err.server", { status: response.status, hint: "" });
}

// Bewaart het bestand via een tijdelijk adres met `download`, en geeft dat adres daarna vrij.
function exportSave(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

async function exportFetchFile(store, keys) {
  let response;
  try {
    response = await fetch("/api/shared-links/export", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ store, keys }),
    });
  } catch (error) {
    throw new Error(t("err.offline"));
  }
  if (!response.ok) throw new Error(await exportFailure(response));
  return response.blob();
}

async function exportDownload(button) {
  if (button.disabled) return;
  const { store, pending } = sharedExport;
  const body = exportRequestBody(store, pending, exportChecked());
  const filename = `${store}.json`;
  message("#export-dialog-message", "");
  setBusy(button, true);
  try {
    exportSave(await exportFetchFile(store, body.keys), filename);
    message("#export-dialog-message", t("loose.export_saved", { file: filename }), "success");
  } catch (error) {
    message("#export-dialog-message", error.message);
  } finally {
    setBusy(button, false);
  }
}

function exportStart() {
  const button = $("#loose-export");
  const dialog = $("#export-dialog");
  if (!button || !dialog) return;
  button.addEventListener("click", () => exportOnOpen(button));
  $("#export-download").addEventListener("click", (event) => exportDownload(event.currentTarget));
  $("#export-dialog-body").addEventListener("change", exportRenderDownload);
  $("#export-dialog-close").addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", (event) => {
    if (event.target === event.currentTarget) dialog.close();
  });
  const refresh = (event) => exportRefresh(event.detail?.store);
  window.addEventListener("ms:loose-loaded", refresh);
  window.addEventListener("ms:loose-confirmed", refresh);
  window.addEventListener("ms:product-links", (event) => {
    if (sharedExport.store && event.detail?.store === sharedExport.store) exportRefresh(sharedExport.store);
  });
  window.addEventListener("ms:languagechange", exportRenderButton);
  exportRenderButton();
}

exportStart();
