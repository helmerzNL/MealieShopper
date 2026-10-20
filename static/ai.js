// AI-apps: het adres van de MCP-server, uitleg per assistent en de apps die toegang hebben.
// Laadt na app.js en gebruikt daar de hulpfuncties ($, message, jsonFetch, t, ...).

const aiState = { status: null };

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

async function loadAi() {
  try {
    aiState.status = await jsonFetch("/api/mcp/status");
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

window.addEventListener("ms:languagechange", renderAi);
