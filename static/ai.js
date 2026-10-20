// AI-apps: het adres van de MCP-server, uitleg per assistent en de apps die toegang hebben.
// Laadt na app.js en gebruikt daar de hulpfuncties ($, message, jsonFetch, t, ...).

const aiState = { status: null, settings: null };

function aiGuides(url) {
  const claudeCode = `claude mcp add --transport http mealieshopper ${url}`;
  const copilot = JSON.stringify({ servers: { mealieshopper: { type: "http", url } } }, null, 2);
  const guide = (title, steps, code) => `
    <details class="ai-guide">
      <summary>${escapeHtml(t(title))}</summary>
      <p>${escapeHtml(t(steps))}</p>
      ${code ? `<pre class="ai-code"><code>${escapeHtml(code)}</code></pre>` : ""}
    </details>`;
  return [
    guide("ai.guide.claude", "ai.guide.claude_steps"),
    guide("ai.guide.claude_code", "ai.guide.claude_code_steps", claudeCode),
    guide("ai.guide.chatgpt", "ai.guide.chatgpt_steps"),
    guide("ai.guide.copilot", "ai.guide.copilot_steps", copilot),
  ].join("");
}

function renderAi() {
  const status = aiState.status;
  if (!status) return;
  const on = Boolean(status.enabled);
  $("#ai-body").hidden = !on;
  $("#ai-tools-box").hidden = !on;
  $("#ai-apps-box").hidden = !on;
  message("#ai-message", on ? "" : status.reason === "disabled" ? t("ai.off.disabled") : t("ai.off.no_login"), "warning");
  if (!on) return;
  $("#ai-url").value = status.url;
  $("#ai-insecure").innerHTML = status.secure ? "" : `<div class="message warning">${escapeHtml(t("ai.insecure"))}</div>`;
  const open = [...document.querySelectorAll("#ai-guides details")].map((element) => element.open);
  $("#ai-guides").innerHTML = aiGuides(status.url);
  document.querySelectorAll("#ai-guides details").forEach((element, index) => (element.open = Boolean(open[index])));
  $("#ai-tools").innerHTML = status.tools
    .map((tool) => `<li><strong>${escapeHtml(tool.title)}</strong> <span class="user-badge">${escapeHtml(t(tool.readOnly ? "ai.tool.read" : "ai.tool.write"))}</span></li>`)
    .join("");
  $("#ai-apps").innerHTML = status.apps.length
    ? status.apps
        .map(
          (app) => `
            <div class="credential-row">
              <div>
                <strong>${escapeHtml(app.name)}</strong>
                <small>${escapeHtml(app.lastUsedAt ? t("ai.apps_last_used", { date: formatCredentialDate(app.lastUsedAt) }) : t("ai.apps_never_used"))}</small>
              </div>
              <button class="button button--secondary" type="button" data-ai-revoke="${escapeHtml(app.clientId)}">${escapeHtml(t("ai.apps_revoke"))}</button>
            </div>`
        )
        .join("")
    : `<p class="muted">${escapeHtml(t("ai.apps_none"))}</p>`;
}

function renderAiSettings() {
  const settings = aiState.settings;
  const box = $("#ai-settings-box");
  if (!box) return;
  box.hidden = !settings; // alleen de beheerder krijgt de instellingen
  if (!settings) return;
  const checkbox = $("#ai-enabled");
  checkbox.checked = settings.mcpEnabled;
  $("#ai-enabled-note").textContent =
    settings.mcpSource === "default" && !settings.mcpEnabled
      ? t("ai.enabled_note_env")
      : !settings.loginOn
        ? t("ai.off.no_login")
        : t("ai.enabled_note");
  const input = $("#ai-base-url");
  if (document.activeElement !== input) input.value = settings.publicBaseUrlOverride || "";
  input.placeholder = settings.publicBaseUrlSource === "setting" ? settings.detectedBaseUrl : settings.publicBaseUrl;
  $("#ai-base-auto").disabled = settings.publicBaseUrlSource !== "setting";
  const source = {
    setting: t("ai.base_note_setting", { url: settings.publicBaseUrl, detected: settings.detectedBaseUrl }),
    env: t("ai.base_note_env", { url: settings.publicBaseUrl }),
    auto: t("ai.base_note_auto", { url: settings.publicBaseUrl }),
  }[settings.publicBaseUrlSource];
  $("#ai-base-note").textContent = `${source} ${t("ai.base_warning")}`;
}

async function saveAiSettings(change, button) {
  message("#ai-settings-message", "");
  if (button) button.disabled = true;
  try {
    aiState.settings = await jsonFetch("/api/server-settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(change),
    });
    message("#ai-settings-message", t("ai.settings_saved"), "success");
    await loadAi();
  } catch (error) {
    message("#ai-settings-message", error.message);
    renderAiSettings();
  } finally {
    if (button) button.disabled = false;
  }
}

async function loadAi() {
  try {
    aiState.status = await jsonFetch("/api/mcp/status");
    aiState.settings = aiState.status.canConfigure ? await jsonFetch("/api/server-settings") : null;
    renderAiSettings();
    renderAi();
  } catch (error) {
    message("#ai-message", `${t("ai.load_failed")} ${error.message}`);
  }
}

$("#ai-copy").addEventListener("click", async (event) => {
  const input = $("#ai-url");
  try {
    await navigator.clipboard.writeText(input.value);
  } catch {
    input.select();
    document.execCommand("copy");
  }
  event.currentTarget.textContent = t("users.copied");
  setTimeout(() => (event.currentTarget.textContent = t("users.copy")), 2000);
});

$("#ai-apps").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-ai-revoke]");
  if (!button) return;
  const app = aiState.status?.apps.find((entry) => entry.clientId === button.dataset.aiRevoke);
  if (!window.confirm(t("ai.apps_revoke_confirm", { name: app?.name || "" }))) return;
  try {
    await jsonFetch(`/api/mcp/apps/${encodeURIComponent(button.dataset.aiRevoke)}`, { method: "DELETE" });
    await loadAi();
  } catch (error) {
    message("#ai-message", error.message);
  }
});

$("#ai-enabled").addEventListener("change", (event) => saveAiSettings({ mcpEnabled: event.target.checked }, event.target));
$("#ai-base-save").addEventListener("click", (event) => saveAiSettings({ publicBaseUrl: $("#ai-base-url").value.trim() }, event.currentTarget));
$("#ai-base-auto").addEventListener("click", (event) => saveAiSettings({ publicBaseUrl: "" }, event.currentTarget));

window.addEventListener("ms:languagechange", () => {
  renderAiSettings();
  renderAi();
});
