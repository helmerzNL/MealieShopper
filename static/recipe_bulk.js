// Recepten van AH en Jumbo in één keer in Mealie zetten: kaarten selecteren, of een hele groep of lijst (alleen de beheerder).
// Laadt na app.js, picnic.js en jumbo.js en gebruikt daar de hulpfuncties van ($, jsonFetch, postJson, message, setBusy, t, ...).
// De import loopt op de server (recipe_bulk.py). Dit script volgt hem elke twee seconden, ook na het herladen van de pagina.

const RECIPE_BULK_POLL_MS = 2000;
const RECIPE_BULK_SECONDS_PER_RECIPE = 4; // ongeveer: ophalen bij de winkel, de pauze ertussen en de import in Mealie
const RECIPE_BULK_GRIDS = { ah: "#ah-recipes", jumbo: "#jumbo-favorites" };
const RECIPE_BULK_STORES = { ah: "Albert Heijn", jumbo: "Jumbo" };
// De codes van de server, elk met een eigen tekst (zo blijven de sleutels letterlijk in de code staan).
const RECIPE_BULK_ERRORS = {
  ah_blocked: "bulk.error.ah_blocked",
  jumbo_blocked: "bulk.error.jumbo_blocked",
  jumbo_auth: "bulk.error.jumbo_auth",
  mealie_denied: "bulk.error.mealie_denied",
  mealie_lookup: "bulk.error.mealie_lookup",
  too_many_failures: "bulk.error.too_many_failures",
  list_failed: "bulk.error.list_failed",
  list_unknown: "bulk.error.list_unknown",
  interrupted: "bulk.error.interrupted",
  internal: "bulk.error.internal",
};
const RECIPE_BULK_ITEM_ERRORS = {
  no_content: "bulk.item.no_content",
  store_failed: "bulk.item.store_failed",
  mealie_failed: "bulk.item.mealie_failed",
  limit: "bulk.item.limit",
  cancelled: "bulk.item.cancelled",
  stopped: "bulk.item.stopped",
};
const RECIPE_BULK_STATUS = {
  pending: "bulk.status.pending",
  busy: "bulk.status.busy",
  imported: "bulk.status.imported",
  existing: "bulk.status.existing",
  failed: "bulk.status.failed",
  skipped: "bulk.status.skipped",
};
const RECIPE_BULK_WARNINGS = {
  organizers_denied: "bulk.warning.organizers_denied",
  organizers_failed: "bulk.warning.organizers_failed",
  tagging_failed: "bulk.warning.tagging_failed",
};
const RECIPE_BULK_REPORT_CLASS = { imported: "is-new", existing: "is-known", failed: "is-problem", skipped: "is-problem" };

const recipeBulk = {
  selecting: { ah: false, jumbo: false },
  selected: { ah: new Map(), jumbo: new Map() }, // id -> titel, in de volgorde van kiezen
  limits: { selection: 50, imports: 250, items: 1000 },
  job: null, // de laatste stand van de server
  timer: 0,
  resumed: false,
  startedHere: false, // deze pagina startte de import: toon het resultaat als hij klaar is
  request: null, // wat de bevestiging gaat starten
};

function recipeBulkIsOwner() {
  const auth = state.auth || {};
  return !auth.enabled || (Boolean(auth.authenticated) && auth.isOwner !== false);
}

function recipeBulkRunning() {
  return recipeBulk.job?.state === "running";
}

function recipeBulkEl(kind, store) {
  return document.querySelector(`[data-bulk-${kind}="${store}"]`);
}

function recipeBulkCards(store) {
  const grid = $(RECIPE_BULK_GRIDS[store]);
  return grid ? [...grid.querySelectorAll(`.recipe-card[data-store-recipe^="${store}:"]`)] : [];
}

// De gekozen groep of lijst: `category` is de naam die de recepten in Mealie als categorie krijgen ("" bij Alle recepten
// en de Jumbo-Favorieten: dat is geen eigen indeling). Null als er bij Jumbo geen lijst te kiezen is.
function recipeBulkGroup(store) {
  if (store === "ah") {
    const group = state.ahRecipeGroups.find((item) => item.id === state.ahRecipeGroup);
    if (group) return { id: group.id, name: group.name, count: group.count, category: group.name };
    return { id: "", name: "", count: state.ahRecipes.length, category: "" };
  }
  const list = typeof currentJumboRecipeList === "function" ? currentJumboRecipeList() : null;
  if (!list) return null;
  const count = Number(list.count) || Number(jumboFavorites.total) || 0;
  return { id: list.id, name: list.name, count, category: list.kind === "favorites" ? "" : list.name, kind: list.kind };
}

function recipeBulkGroupLabel(store, group) {
  const count = I18N.formatNumber(group.count);
  if (store === "jumbo") return t("bulk.whole_list", { count });
  return group.id ? t("bulk.whole_group", { count }) : t("bulk.whole_saved", { count });
}

// Na elke keer dat de kaarten opnieuw getekend zijn (renderSavedRecipes, renderJumboFavorites): de vakjes en knoppen bijwerken.
function recipeBulkDecorate(store) {
  const grid = $(RECIPE_BULK_GRIDS[store]);
  if (!grid) return;
  const selecting = recipeBulk.selecting[store];
  const selected = recipeBulk.selected[store];
  grid.classList.toggle("is-selecting", selecting);
  for (const card of recipeBulkCards(store)) {
    const id = card.dataset.storeRecipe.slice(store.length + 1);
    let check = card.querySelector(".bulk-check");
    if (!selecting) {
      check?.remove();
      card.classList.remove("is-selectable", "is-selected");
      continue;
    }
    if (!check) {
      const title = card.querySelector("h3")?.textContent.trim() || "";
      card.insertAdjacentHTML(
        "afterbegin",
        `<label class="bulk-check"><input type="checkbox" data-bulk-id="${escapeHtml(id)}" data-bulk-store="${store}"><span class="visually-hidden">${escapeHtml(t("bulk.choose", { title }))}</span></label>`
      );
      check = card.querySelector(".bulk-check");
    }
    const on = selected.has(id);
    check.querySelector("input").checked = on;
    card.classList.add("is-selectable");
    card.classList.toggle("is-selected", on);
  }
  recipeBulkRenderTools(store);
  if (!recipeBulk.resumed) recipeBulkResume();
}

function recipeBulkRenderTools(store) {
  const tools = recipeBulkEl("tools", store);
  if (!tools) return;
  const selecting = recipeBulk.selecting[store];
  const cards = recipeBulkCards(store);
  const select = recipeBulkEl("select", store);
  select.hidden = !cards.length && !selecting;
  select.setAttribute("aria-pressed", String(selecting));

  const group = recipeBulkGroup(store);
  const groupButton = recipeBulkEl("group", store);
  groupButton.hidden = !(recipeBulkIsOwner() && group && group.count > 0 && (store === "ah" || group.id));
  if (!groupButton.hidden) groupButton.textContent = recipeBulkGroupLabel(store, group);
  groupButton.disabled = recipeBulkRunning();

  const status = recipeBulkEl("status", store);
  const job = recipeBulk.job;
  status.hidden = !recipeBulkRunning();
  if (!status.hidden) {
    status.textContent = t("bulk.running", { store: job.storeName || "", done: I18N.formatNumber(job.done || 0), total: I18N.formatNumber(job.total || 0) });
  }
  tools.hidden = select.hidden && groupButton.hidden && status.hidden;

  const bar = recipeBulkEl("bar", store);
  bar.hidden = !selecting;
  const count = recipeBulk.selected[store].size;
  recipeBulkEl("count", store).textContent = tn("bulk.selected", count);
  const run = recipeBulkEl("import", store);
  run.textContent = tn("bulk.import_selected", count);
  run.disabled = !count || recipeBulkRunning();
}

function recipeBulkNote(store, text) {
  const note = recipeBulkEl("note", store);
  if (!note) return;
  note.textContent = text || "";
  note.hidden = !text;
}

// Zet een recept in of uit de selectie. Geeft false als het maximum bereikt is.
function recipeBulkToggle(store, id, title, on) {
  const selected = recipeBulk.selected[store];
  if (!on) {
    selected.delete(id);
    recipeBulkNote(store, "");
    return true;
  }
  if (!selected.has(id) && selected.size >= recipeBulk.limits.selection) {
    recipeBulkNote(store, t("bulk.selection_max", { max: I18N.formatNumber(recipeBulk.limits.selection) }));
    return false;
  }
  selected.set(id, title);
  return true;
}

function recipeBulkSelectAll(store) {
  for (const card of recipeBulkCards(store)) {
    const input = card.querySelector("[data-bulk-id]");
    if (!input || input.checked) continue;
    if (!recipeBulkToggle(store, input.dataset.bulkId, card.querySelector("h3")?.textContent.trim() || "", true)) break;
  }
  recipeBulkDecorate(store);
}

function recipeBulkStopSelecting(store) {
  recipeBulk.selecting[store] = false;
  recipeBulk.selected[store].clear();
  recipeBulkNote(store, "");
  recipeBulkDecorate(store);
}

// ---- Bevestigen

function recipeBulkConfirmSelection(store) {
  const selected = recipeBulk.selected[store];
  if (!selected.size) return;
  const group = recipeBulkGroup(store);
  const ids = [...selected.keys()];
  recipeBulkConfirm({
    path: "/api/mealie/bulk-import",
    body: { store, ids, group: group?.category ? group.id : "", titles: Object.fromEntries(selected) },
    store,
    kind: "selection",
    count: ids.length,
    name: "",
    category: group?.category || "",
  });
}

function recipeBulkConfirmGroup(store) {
  const group = recipeBulkGroup(store);
  if (!group) return;
  recipeBulkConfirm({
    path: "/api/mealie/bulk-import/group",
    body: { store, group: group.id },
    store,
    kind: store === "jumbo" ? "list" : group.id ? "group" : "saved",
    count: group.count,
    name: group.name,
    category: group.category,
  });
}

function recipeBulkConfirmLines(request) {
  const store = RECIPE_BULK_STORES[request.store];
  const what = {
    selection: () => tn("bulk.confirm.selection", request.count, { store }),
    group: () => tn("bulk.confirm.group", request.count, { name: request.name }),
    list: () => tn("bulk.confirm.list", request.count, { name: request.name }),
    saved: () => tn("bulk.confirm.saved", request.count),
  }[request.kind]();
  const lines = [
    what,
    request.category ? t("bulk.confirm.tag_category", { tag: store, category: request.category }) : t("bulk.confirm.tag", { tag: store }),
    t("bulk.confirm.existing"),
  ];
  if (request.kind !== "selection" && request.count > recipeBulk.limits.imports) {
    lines.push(t("bulk.confirm.limit", { max: I18N.formatNumber(recipeBulk.limits.imports) }));
  }
  const fetches = Math.min(request.count, recipeBulk.limits.imports);
  lines.push(tn("bulk.confirm.time", Math.max(1, Math.ceil((fetches * RECIPE_BULK_SECONDS_PER_RECIPE) / 60))));
  return lines;
}

function recipeBulkShowDialog() {
  const dialog = $("#bulk-dialog");
  if (!dialog.open) dialog.showModal();
}

function recipeBulkConfirm(request) {
  if (recipeBulkRunning()) {
    recipeBulkRender();
    recipeBulkShowDialog();
    return;
  }
  recipeBulk.request = request;
  $("#bulk-dialog-intro").innerHTML = recipeBulkConfirmLines(request).map((line) => `<p>${escapeHtml(line)}</p>`).join("");
  $("#bulk-progress").hidden = true;
  $("#bulk-report").innerHTML = "";
  message("#bulk-dialog-message", "");
  $("#bulk-run").hidden = false;
  $("#bulk-run").disabled = false;
  $("#bulk-stop").hidden = true;
  recipeBulkShowDialog();
}

async function recipeBulkRun() {
  const request = recipeBulk.request;
  if (!request) return;
  const button = $("#bulk-run");
  setBusy(button, true, t("bulk.starting"));
  message("#bulk-dialog-message", "");
  try {
    const job = await postJson(request.path, request.body);
    recipeBulk.request = null;
    recipeBulk.startedHere = true;
    recipeBulk.selecting[request.store] = false;
    recipeBulk.selected[request.store].clear();
    recipeBulkDecorate(request.store);
    recipeBulkWatch(job);
  } catch (error) {
    message("#bulk-dialog-message", error.message);
  } finally {
    setBusy(button, false);
  }
}

// ---- Volgen

function recipeBulkWatch(job) {
  const wasRunning = recipeBulkRunning();
  recipeBulk.job = job;
  if (job.limits) recipeBulk.limits = job.limits;
  clearTimeout(recipeBulk.timer);
  if (recipeBulkRunning()) recipeBulk.timer = setTimeout(recipeBulkTick, RECIPE_BULK_POLL_MS);
  for (const store of Object.keys(RECIPE_BULK_GRIDS)) recipeBulkRenderTools(store);
  if (!recipeBulk.request) recipeBulkRender();
  // Je eigen import is klaar terwijl het venster dicht was: laat het resultaat zien.
  if (wasRunning && !recipeBulkRunning() && recipeBulk.startedHere && !recipeBulk.request) {
    recipeBulk.startedHere = false;
    recipeBulkShowDialog();
  }
}

async function recipeBulkTick() {
  let job;
  try {
    job = await jsonFetch("/api/mealie/bulk-import");
  } catch {
    recipeBulk.timer = setTimeout(recipeBulkTick, RECIPE_BULK_POLL_MS * 2); // even geen verbinding: later opnieuw
    return;
  }
  // De server weet niets meer van de import die liep: de app is opnieuw gestart.
  if (job.state === "idle" && recipeBulkRunning()) job = { ...recipeBulk.job, state: "error", error: "interrupted" };
  recipeBulkWatch(job);
}

// Eén keer per pagina: loopt er al een import (van jou of iemand anders), dan de voortgang tonen.
async function recipeBulkResume() {
  recipeBulk.resumed = true;
  try {
    const job = await jsonFetch("/api/mealie/bulk-import");
    if (job.limits) recipeBulk.limits = job.limits;
    if (job.state === "running") recipeBulkWatch(job);
  } catch {
    // geen stand: dan is er ook niets te volgen
  }
}

function recipeBulkJobLabel(job) {
  const store = job.storeName || "";
  let what;
  if (!job.whole) what = tn("bulk.job.selection", job.total || 0, { store });
  else if (job.group?.name) what = t("bulk.job.group", { store, name: job.group.name });
  else what = t("bulk.job.saved", { store });
  return job.startedBy ? `${what} ${t("bulk.job.by", { name: job.startedBy })}` : what;
}

function recipeBulkItem(item) {
  const title = escapeHtml(item.title || item.id);
  const name = item.mealieUrl ? `<a href="${escapeHtml(item.mealieUrl)}" target="_blank" rel="noopener">${title}</a>` : `<span>${title}</span>`;
  const why = RECIPE_BULK_ITEM_ERRORS[item.error] ? ` · ${t(RECIPE_BULK_ITEM_ERRORS[item.error])}` : "";
  const status = t(RECIPE_BULK_STATUS[item.status] || RECIPE_BULK_STATUS.pending) + why;
  return `<li class="${RECIPE_BULK_REPORT_CLASS[item.status] || ""}">${name}<small>${escapeHtml(status)}</small></li>`;
}

function recipeBulkSummary(job) {
  if (job.state === "error") return { text: t(RECIPE_BULK_ERRORS[job.error] || RECIPE_BULK_ERRORS.internal), type: "error" };
  if (job.state === "cancelled") return { text: t("bulk.cancelled"), type: "warning" };
  if (job.remaining) return { text: tn("bulk.done_remaining", job.remaining), type: "warning" };
  if (job.failed) return { text: tn("bulk.done_failed", job.failed), type: "warning" };
  return { text: t("bulk.done"), type: "success" };
}

function recipeBulkRender() {
  const job = recipeBulk.job;
  if (!job || job.state === "idle") return;
  const running = job.state === "running";
  const preparing = running && job.phase === "preparing";
  $("#bulk-dialog-intro").innerHTML = `<p>${escapeHtml(recipeBulkJobLabel(job))}</p>`;
  $("#bulk-progress").hidden = false;
  const bar = $("#bulk-progress-bar");
  bar.max = Math.max(1, job.total || 0);
  if (preparing) bar.removeAttribute("value");
  else bar.value = job.done || 0;
  $("#bulk-progress-label").textContent = preparing
    ? tn("bulk.preparing", job.total || 0)
    : t("bulk.progress", { done: I18N.formatNumber(job.done || 0), total: I18N.formatNumber(job.total || 0) });
  const counts = [
    tn("bulk.count.imported", job.imported || 0),
    tn("bulk.count.existing", job.existing || 0),
    tn("bulk.count.failed", job.failed || 0),
  ];
  if (job.skipped) counts.push(tn("bulk.count.skipped", job.skipped));
  $("#bulk-progress-counts").textContent = counts.join(" · ");
  const notes = (job.warnings || []).filter((code) => RECIPE_BULK_WARNINGS[code]).map((code) => `<div class="message warning">${escapeHtml(t(RECIPE_BULK_WARNINGS[code]))}</div>`);
  if (running) {
    $("#bulk-dialog-message").innerHTML = notes.join("");
  } else {
    const summary = recipeBulkSummary(job);
    $("#bulk-dialog-message").innerHTML = `<div class="message ${summary.type}">${escapeHtml(summary.text)}</div>${notes.join("")}`;
  }
  $("#bulk-report").innerHTML = (job.items || []).map(recipeBulkItem).join("");
  $("#bulk-run").hidden = true;
  const mine = !job.startedBy || job.startedBy === state.auth?.username;
  $("#bulk-stop").hidden = !(running && (mine || recipeBulkIsOwner()));
}

async function recipeBulkStop() {
  const button = $("#bulk-stop");
  setBusy(button, true, t("bulk.stopping"));
  try {
    recipeBulkWatch(await postJson("/api/mealie/bulk-import/cancel"));
  } catch (error) {
    message("#bulk-dialog-message", error.message);
  } finally {
    setBusy(button, false);
  }
}

// ---- Klikken

document.addEventListener("click", (event) => {
  const target = event.target;
  const action = target.closest("[data-bulk-select], [data-bulk-all], [data-bulk-clear], [data-bulk-import], [data-bulk-group], [data-bulk-status]");
  if (action) {
    const data = action.dataset;
    if (data.bulkSelect) {
      const store = data.bulkSelect;
      if (recipeBulk.selecting[store]) recipeBulkStopSelecting(store);
      else {
        recipeBulk.selecting[store] = true;
        recipeBulkDecorate(store);
      }
    } else if (data.bulkAll) recipeBulkSelectAll(data.bulkAll);
    else if (data.bulkClear) recipeBulkStopSelecting(data.bulkClear);
    else if (data.bulkImport) recipeBulkConfirmSelection(data.bulkImport);
    else if (data.bulkGroup) recipeBulkConfirmGroup(data.bulkGroup);
    else if (data.bulkStatus) {
      recipeBulkRender();
      recipeBulkShowDialog();
    }
    return;
  }
  // Een tik op een kaart (niet op een knop of link) zet hem in of uit de selectie.
  const card = target.closest(".recipe-grid.is-selecting .recipe-card.is-selectable");
  if (!card || target.closest("button, a, input, label, select")) return;
  const input = card.querySelector("[data-bulk-id]");
  if (!input) return;
  input.checked = !input.checked;
  input.dispatchEvent(new Event("change", { bubbles: true }));
});

document.addEventListener("change", (event) => {
  const input = event.target.closest("[data-bulk-id]");
  if (!input) return;
  const store = input.dataset.bulkStore;
  const card = input.closest(".recipe-card");
  if (!recipeBulkToggle(store, input.dataset.bulkId, card?.querySelector("h3")?.textContent.trim() || "", input.checked)) input.checked = false;
  card?.classList.toggle("is-selected", input.checked);
  recipeBulkRenderTools(store);
});

// Een andere groep of lijst: begin opnieuw met kiezen (de kaarten worden daarna opnieuw getekend).
$("#ah-recipe-group").addEventListener("change", () => {
  recipeBulk.selected.ah.clear();
  recipeBulkNote("ah", "");
  recipeBulkDecorate("ah");
});
$("#jumbo-recipe-list").addEventListener("change", () => {
  recipeBulk.selected.jumbo.clear();
  recipeBulkNote("jumbo", "");
  recipeBulkDecorate("jumbo");
});

$("#bulk-run").addEventListener("click", recipeBulkRun);
$("#bulk-stop").addEventListener("click", recipeBulkStop);
$("#bulk-close").addEventListener("click", () => $("#bulk-dialog").close());
$("#bulk-dialog").addEventListener("close", () => {
  recipeBulk.request = null;
});

window.addEventListener("ms:languagechange", () => {
  for (const store of Object.keys(RECIPE_BULK_GRIDS)) recipeBulkDecorate(store);
  if (!$("#bulk-dialog").open) return;
  if (recipeBulk.request) recipeBulkConfirm(recipeBulk.request);
  else recipeBulkRender();
});
