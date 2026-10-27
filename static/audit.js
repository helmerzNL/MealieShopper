// Auditlog (Beheer > Auditlog): het verkeer met AH, Picnic, Jumbo en HelloFresh en de verzoeken van AI-assistenten (MCP).
// Laadt na app.js en gebruikt daar de hulpfuncties van ($, message, jsonFetch, t, escapeHtml, ...).

const auditState = { entries: [], next: null, total: 0, last24h: {}, settings: null, loading: false, again: false, open: new Set(), details: {} };
// Letterlijke sleutels (geen samengestelde), zodat controle op ongebruikte vertalingen ze vindt.
const AUDIT_SERVICE_KEYS = {
  ah: "audit.service.ah",
  picnic: "audit.service.picnic",
  jumbo: "audit.service.jumbo",
  hellofresh: "audit.service.hellofresh",
  mcp: "audit.service.mcp",
  audit: "audit.service.audit",
};
const AUDIT_KIND_KEYS = {
  outbound: "audit.kind.outbound",
  mcp: "audit.kind.mcp",
  oauth: "audit.kind.oauth",
  audit: "audit.kind.audit",
};
const AUDIT_SERVICES = Object.keys(AUDIT_SERVICE_KEYS);

function auditQuery(before, extra = { limit: "50" }) {
  const params = new URLSearchParams(extra);
  const service = $("#audit-service").value;
  const status = $("#audit-status").value;
  const query = $("#audit-search").value.trim();
  if (service) params.set("service", service);
  if (status) params.set("status", status);
  if (query) params.set("q", query);
  if (before) params.set("before", String(before));
  return params.toString();
}

async function loadAudit({ append = false } = {}) {
  if (auditState.loading) {
    auditState.again = !append; // een filter dat tijdens het laden wijzigt, krijgt daarna alsnog zijn beurt
    return;
  }
  auditState.loading = true;
  auditState.again = false;
  if (!append) message("#audit-message", "");
  try {
    const data = await jsonFetch(`/api/audit?${auditQuery(append ? auditState.next : null)}`);
    auditState.entries = append ? auditState.entries.concat(data.entries) : data.entries;
    auditState.next = data.next;
    auditState.total = data.total;
    auditState.last24h = data.last24h || {};
    auditState.settings = data.settings || null;
  } catch (error) {
    message("#audit-message", error.message);
  } finally {
    auditState.loading = false;
    renderAudit();
    if (auditState.again) loadAudit();
  }
}

function auditServiceLabel(service) {
  return AUDIT_SERVICE_KEYS[service] ? t(AUDIT_SERVICE_KEYS[service]) : service;
}

function auditActorLabel(actor) {
  const [kind, ...rest] = String(actor || "system").split(":");
  const name = rest.join(":");
  if (kind === "mcp") return name ? t("audit.actor.mcp", { name }) : t("audit.actor.mcp_unknown");
  if (kind === "app") return name ? t("audit.actor.app", { name }) : t("audit.actor.app_anon");
  return t("audit.actor.system");
}

function auditKindLabel(kind) {
  return AUDIT_KIND_KEYS[kind] ? t(AUDIT_KIND_KEYS[kind]) : kind;
}

function auditBytes(value) {
  if (value === null || value === undefined) return "-";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toLocaleString(I18N.locale(), { maximumFractionDigits: 1 })} kB`;
  return `${(value / 1024 / 1024).toLocaleString(I18N.locale(), { maximumFractionDigits: 1 })} MB`;
}

function auditTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString(I18N.locale(), { dateStyle: "short", timeStyle: "medium" });
}

function auditStatusChip(entry) {
  const text = entry.status ? String(entry.status) : "-";
  return `<span class="audit-status ${entry.ok ? "is-ok" : "is-error"}">${escapeHtml(text)}</span>`;
}

// Opgeslagen bodies zijn compacte JSON; leesbaar maken als het (nog) kan, anders de tekst zoals bewaard.
function auditPretty(text) {
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch (error) {
    return text;
  }
}

function auditBlock(titleKey, body) {
  return `<h4>${escapeHtml(t(titleKey))}</h4><pre class="audit-body">${escapeHtml(body ? auditPretty(body) : t("audit.detail.nothing"))}</pre>`;
}

function auditRelatedHtml(entry) {
  if (!entry.related) return "";
  if (entry.kind === "mcp") {
    const rows = entry.related
      .map(
        (item) =>
          `<li>${escapeHtml(auditServiceLabel(item.service))} · <strong>${escapeHtml(item.action)}</strong> ${auditStatusChip(item)} <span class="muted">${escapeHtml(t("audit.ms", { ms: item.durationMs }))}</span></li>`
      )
      .join("");
    return `<h4>${escapeHtml(t("audit.detail.caused"))}</h4>${rows ? `<ul class="audit-related">${rows}</ul>` : `<p class="muted">${escapeHtml(t("audit.detail.caused_none"))}</p>`}`;
  }
  if (!entry.related.length) return "";
  const rows = entry.related.map((item) => `<li><strong>${escapeHtml(item.action)}</strong> · ${escapeHtml(auditActorLabel(item.actor))} <span class="muted">${escapeHtml(auditTime(item.ts))}</span></li>`).join("");
  return `<h4>${escapeHtml(t("audit.detail.cause"))}</h4><ul class="audit-related">${rows}</ul>`;
}

// Kopiëren kan alleen in een veilige context (https of localhost); anders vallen we terug op een tijdelijk tekstvak.
async function auditCopyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (error) {
    const box = document.createElement("textarea");
    box.value = text;
    box.style.cssText = "position:fixed;opacity:0";
    document.body.appendChild(box);
    box.select();
    let copied = false;
    try {
      copied = document.execCommand("copy");
    } catch (failure) {
      copied = false;
    }
    box.remove();
    return copied;
  }
}

function auditExport(format) {
  window.location.href = `/api/audit/export?${auditQuery(null, { format })}`;
}

async function auditCopyList() {
  try {
    const data = await jsonFetch(`/api/audit/export?${auditQuery(null, { format: "json" })}`);
    const ok = await auditCopyText(JSON.stringify(data.entries, null, 2));
    message("#audit-message", ok ? t("audit.copied_count", { count: data.entries.length }) : t("audit.copy_failed"), ok ? "success" : "error");
  } catch (error) {
    message("#audit-message", error.message);
  }
}

function auditDetailHtml(entry) {
  const facts = [];
  if (entry.url) facts.push(`<dt>${escapeHtml(t("audit.detail.address"))}</dt><dd><code>${escapeHtml(`${entry.method} ${entry.url}`.trim())}</code></dd>`);
  facts.push(`<dt>${escapeHtml(t("audit.detail.who"))}</dt><dd>${escapeHtml(auditActorLabel(entry.actor))}${entry.detail?.user ? ` (${escapeHtml(entry.detail.user)})` : ""}</dd>`);
  if (entry.reqBytes || entry.respBytes) {
    facts.push(`<dt></dt><dd class="muted">${escapeHtml(t("audit.detail.size", { sent: auditBytes(entry.reqBytes || 0), received: auditBytes(entry.respBytes || 0) }))}</dd>`);
  }
  if (entry.detail?.redirects) facts.push(`<dt></dt><dd class="muted">${escapeHtml(t("audit.detail.redirects", { count: entry.detail.redirects }))}</dd>`);
  if (entry.detail?.loginProxy) facts.push(`<dt></dt><dd class="muted">${escapeHtml(t("audit.detail.loginproxy"))}</dd>`);
  const error = entry.error ? `<h4>${escapeHtml(t("audit.detail.error"))}</h4><pre class="audit-body audit-body--error">${escapeHtml(entry.error)}</pre>` : "";
  const bodies = entry.kind === "oauth" || entry.kind === "audit" ? "" : auditBlock("audit.detail.request", entry.request) + auditBlock("audit.detail.response", entry.response);
  const copy = `<button class="button button--secondary audit-copy" type="button" data-audit-copy="${entry.id}">${escapeHtml(t("audit.copy"))}</button>`;
  return `<dl class="audit-facts">${facts.join("")}</dl>${error}${auditRelatedHtml(entry)}${bodies}<div class="security-actions">${copy}</div>`;
}

function renderAuditSummary() {
  const chips = AUDIT_SERVICES.filter((service) => auditState.last24h[service])
    .map((service) => {
      const { total, errors } = auditState.last24h[service];
      const failed = errors ? ` · <span class="audit-failed">${escapeHtml(t("audit.failed", { count: errors }))}</span>` : "";
      return `<span class="audit-chip"><strong>${escapeHtml(auditServiceLabel(service))}</strong> ${escapeHtml(String(total))}${failed}</span>`;
    })
    .join("");
  $("#audit-summary").innerHTML = chips ? `<span class="muted">${escapeHtml(t("audit.last24"))}</span>${chips}` : "";
}

function renderAudit() {
  const settings = auditState.settings;
  const notices = [];
  if (settings && !settings.enabled) notices.push(["warning", t("audit.disabled")]);
  else if (settings && !settings.bodies) notices.push(["warning", t("audit.bodies_off")]);
  $("#audit-notices").innerHTML = notices.map(([type, text]) => `<div class="message ${type}">${escapeHtml(text)}</div>`).join("");
  if (settings) {
    $("#audit-privacy").textContent = settings.retentionDays ? t("audit.privacy", { days: settings.retentionDays }) : t("audit.privacy_forever");
  }
  renderAuditSummary();

  const list = $("#audit-list");
  if (auditState.loading && !auditState.entries.length) {
    list.innerHTML = `<p class="muted">${escapeHtml(t("audit.loading"))}</p>`;
  } else if (!auditState.entries.length) {
    const filtered = $("#audit-service").value || $("#audit-status").value || $("#audit-search").value.trim();
    list.innerHTML = `<p class="muted">${escapeHtml(t(filtered ? "audit.empty_filtered" : "audit.empty"))}</p>`;
  } else {
    list.innerHTML = auditState.entries
      .map((entry) => {
        const open = auditState.open.has(entry.id);
        const detail = auditState.details[entry.id];
        return `
          <details class="audit-entry${entry.ok ? "" : " is-error"}" data-audit-id="${entry.id}"${open ? " open" : ""}>
            <summary>
              <span class="audit-time">${escapeHtml(auditTime(entry.ts))}</span>
              <span class="audit-service audit-service--${escapeHtml(entry.service)}">${escapeHtml(auditServiceLabel(entry.service))}</span>
              <span class="audit-action">
                <strong>${escapeHtml(entry.action)}</strong>
                <small>${escapeHtml(auditKindLabel(entry.kind))} · ${escapeHtml(auditActorLabel(entry.actor))}${entry.error ? ` · ${escapeHtml(entry.error)}` : ""}</small>
              </span>
              ${auditStatusChip(entry)}
              <span class="audit-duration muted">${escapeHtml(t("audit.ms", { ms: entry.durationMs }))}</span>
            </summary>
            <div class="audit-detail">${open ? (detail ? (detail.failed ? `<div class="message error">${escapeHtml(t("audit.detail.failed", { detail: detail.failed }))}</div>` : auditDetailHtml(detail)) : `<p class="muted">${escapeHtml(t("audit.loading"))}</p>`) : ""}</div>
          </details>`;
      })
      .join("");
  }
  $("#audit-more").hidden = !auditState.next;
  $("#audit-total").textContent = t("audit.total", { count: auditState.total.toLocaleString(I18N.locale()) });
}

async function openAuditEntry(id) {
  auditState.open.add(id);
  if (auditState.details[id]) return;
  try {
    auditState.details[id] = await jsonFetch(`/api/audit/${id}`);
  } catch (error) {
    auditState.details[id] = { failed: error.message };
  }
  const element = document.querySelector(`#audit-list [data-audit-id="${id}"] .audit-detail`);
  if (element && auditState.open.has(id)) {
    const detail = auditState.details[id];
    element.innerHTML = detail.failed ? `<div class="message error">${escapeHtml(t("audit.detail.failed", { detail: detail.failed }))}</div>` : auditDetailHtml(detail);
  }
}

// `toggle` bubbelt niet; met capture vangen we het op de lijst.
$("#audit-list").addEventListener(
  "toggle",
  (event) => {
    const entry = event.target.closest?.("[data-audit-id]");
    if (!entry) return;
    const id = Number(entry.dataset.auditId);
    if (entry.open) openAuditEntry(id);
    else auditState.open.delete(id);
  },
  true
);

async function clearAudit() {
  if (!window.confirm(t("audit.clear_confirm"))) return;
  try {
    const data = await jsonFetch("/api/audit", { method: "DELETE" });
    auditState.open.clear();
    auditState.details = {};
    await loadAudit();
    message("#audit-message", t("audit.cleared", { count: data.removed }), "success");
  } catch (error) {
    message("#audit-message", error.message);
  }
}

$("#audit-list").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-audit-copy]");
  if (!button) return;
  const entry = auditState.details[button.dataset.auditCopy];
  if (!entry) return;
  const ok = await auditCopyText(JSON.stringify(entry, null, 2));
  button.textContent = t(ok ? "audit.copied" : "audit.copy_failed");
});

let auditSearchTimer = null;
$("#audit-export-json").addEventListener("click", () => auditExport("json"));
$("#audit-export-csv").addEventListener("click", () => auditExport("csv"));
$("#audit-copy-list").addEventListener("click", auditCopyList);
$("#audit-refresh").addEventListener("click", () => {
  auditState.details = {};
  loadAudit();
});
$("#audit-more").addEventListener("click", () => loadAudit({ append: true }));
$("#audit-clear").addEventListener("click", clearAudit);
$("#audit-service").addEventListener("change", () => loadAudit());
$("#audit-status").addEventListener("change", () => loadAudit());
$("#audit-search").addEventListener("input", () => {
  clearTimeout(auditSearchTimer);
  auditSearchTimer = setTimeout(() => loadAudit(), 300);
});
window.addEventListener("ms:languagechange", () => renderAudit());
