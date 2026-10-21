// Gebruikers: de beheerder nodigt mensen uit, een uitgenodigde maakt een account aan.
// Laadt na app.js en gebruikt daar de hulpfuncties van ($, message, jsonFetch, t, ...).

// ---- Rol: wat een gewone gebruiker niet ziet
function applyRole() {
  const auth = state.auth || {};
  const signedIn = Boolean(auth.authenticated) || !auth.enabled;
  const owner = !auth.enabled || auth.isOwner !== false;
  document.querySelectorAll("[data-admin-only]").forEach((element) => {
    const isUsersTab = element.dataset.subtab === "beheer-users";
    // Gebruikers beheren kan alleen als inloggen aanstaat; koppelen kan alleen de beheerder.
    element.hidden = isUsersTab ? !(signedIn && owner && auth.enabled) : signedIn && !owner;
  });
  document.querySelectorAll("[data-regular-only]").forEach((element) => {
    element.hidden = !(signedIn && !owner);
  });
  const active = document.querySelector("#beheer [data-subtab].is-active");
  if (active && active.closest("[hidden]:not(.subtab-menu__list)")) document.querySelector('[data-subtab="beheer-security"]')?.click();
  document.documentElement.classList.toggle("is-regular-user", signedIn && !owner);
  const nameInput = $("#pw-username");
  if (nameInput) nameInput.readOnly = signedIn && !owner; // alleen de beheerder kan de naam wijzigen
}

// ---- Gebruikers en uitnodigingen (beheerder)
const usersState = { users: [], invites: [] };

function userMethods(user) {
  const methods = [];
  if (user.passkeys) methods.push(t("users.method.passkey"));
  if (user.password) methods.push(t("users.method.password"));
  if (user.mfa) methods.push(t("users.method.mfa"));
  return methods.length ? methods.join(" · ") : t("users.method.none");
}

function renderUsers() {
  const list = $("#users-list");
  if (!list) return;
  list.innerHTML = usersState.users
    .map((user) => {
      const owner = user.role === "owner";
      return `
        <div class="credential-row">
          <div>
            <strong>${escapeHtml(user.username)}</strong>${owner ? ` <span class="user-badge">${escapeHtml(t("users.owner"))}</span>` : ""}
            <small>${escapeHtml(userMethods(user))} · ${escapeHtml(t("users.since", { date: formatCredentialDate(user.createdAt).slice(0, 10) }))}</small>
          </div>
          ${
            owner
              ? ""
              : `<div class="row-actions">
                  <button class="button button--secondary" type="button" data-user-reset="${escapeHtml(user.id)}">${escapeHtml(t("users.reset"))}</button>
                  <button class="button button--secondary" type="button" data-user-delete="${escapeHtml(user.id)}">${escapeHtml(t("users.delete"))}</button>
                </div>`
          }
        </div>`;
    })
    .join("");
  const pending = $("#invites-list");
  pending.innerHTML = usersState.invites.length
    ? usersState.invites
        .map((invite) => {
          const what =
            invite.kind === "reset"
              ? t("users.pending_reset", { name: invite.username })
              : invite.username
                ? t("users.pending_new_named", { name: invite.username })
                : t("users.pending_new");
          return `
            <div class="credential-row">
              <div>
                <strong>${escapeHtml(what)}</strong>
                <small>${escapeHtml(t("users.expires", { date: formatCredentialDate(invite.expiresAt).slice(0, 10) }))}</small>
              </div>
              <button class="button button--secondary" type="button" data-invite-revoke="${escapeHtml(invite.id)}">${escapeHtml(t("users.revoke"))}</button>
            </div>`;
        })
        .join("")
    : `<p class="muted">${escapeHtml(t("users.none_pending"))}</p>`;
}

async function loadUsers() {
  message("#users-message", "");
  try {
    const data = await jsonFetch("/api/users");
    usersState.users = data.users || [];
    usersState.invites = data.invites || [];
    renderUsers();
  } catch (error) {
    message("#users-message", `${t("users.load_failed")} ${error.message}`);
  }
}

function showInviteLink(created, name) {
  const target = $("#invite-result");
  target.innerHTML = `
    <div class="message success">
      <p>${escapeHtml(t("users.link_ready", { name }))}</p>
      <div class="form-row">
        <input class="input" id="invite-link" readonly value="${escapeHtml(created.url)}" aria-label="${escapeHtml(t("users.link_label"))}">
        <button class="button" type="button" id="invite-copy">${escapeHtml(t("users.copy"))}</button>
      </div>
      <small>${escapeHtml(t("users.link_note", { date: formatCredentialDate(created.expiresAt).slice(0, 10) }))}</small>
    </div>`;
  const input = $("#invite-link");
  input.focus();
  input.select();
  $("#invite-copy").addEventListener("click", async (event) => {
    try {
      await navigator.clipboard.writeText(created.url);
    } catch {
      input.select();
      document.execCommand("copy");
    }
    event.currentTarget.textContent = t("users.copied");
  });
}

async function createInvite(event) {
  event.preventDefault();
  const name = $("#invite-name").value.trim();
  message("#users-message", "");
  try {
    const created = await jsonFetch("/api/users/invites", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: name }),
    });
    $("#invite-name").value = "";
    showInviteLink(created, name || t("users.someone"));
    await loadUsers();
  } catch (error) {
    message("#users-message", error.message);
  }
}

$("#invite-create-form").addEventListener("submit", createInvite);

$("#beheer-users").addEventListener("click", async (event) => {
  const reset = event.target.closest("[data-user-reset]");
  const remove = event.target.closest("[data-user-delete]");
  const revoke = event.target.closest("[data-invite-revoke]");
  try {
    if (reset) {
      const user = usersState.users.find((entry) => entry.id === reset.dataset.userReset);
      const created = await jsonFetch(`/api/users/${encodeURIComponent(reset.dataset.userReset)}/reset`, { method: "POST" });
      showInviteLink(created, user?.username || "");
      await loadUsers();
    } else if (remove) {
      const user = usersState.users.find((entry) => entry.id === remove.dataset.userDelete);
      if (!window.confirm(t("users.delete_confirm", { name: user?.username || "" }))) return;
      await jsonFetch(`/api/users/${encodeURIComponent(remove.dataset.userDelete)}`, { method: "DELETE" });
      await loadUsers();
    } else if (revoke) {
      await jsonFetch(`/api/users/invites/${encodeURIComponent(revoke.dataset.inviteRevoke)}`, { method: "DELETE" });
      await loadUsers();
    }
  } catch (error) {
    message("#users-message", error.message);
  }
});

// ---- Uitgenodigde: account aanmaken via de link (#invite=...)
function inviteTokenFromUrl() {
  const match = /(?:^#|&)invite=([^&]+)/.exec(window.location.hash);
  return match ? decodeURIComponent(match[1]) : "";
}

async function loadInvite() {
  const token = inviteTokenFromUrl();
  if (!token) return;
  state.invite = { token, info: null, error: "" };
  try {
    const response = await fetch("/api/invites/info", { headers: { "X-Invite-Token": token }, credentials: "same-origin" });
    const data = await response.json();
    if (response.ok) state.invite.info = data;
    else state.invite.error = data.error || t("invite.invalid");
  } catch (error) {
    state.invite.error = error.message;
  }
}

function leaveInvite() {
  state.invite = null;
  history.replaceState(null, "", window.location.pathname + window.location.search);
}

function renderInviteGate() {
  const gate = $("#invite-gate");
  if (!gate) return;
  const invite = state.invite;
  gate.hidden = !invite;
  if (!invite) return;
  const info = invite.info;
  const reset = info?.kind === "reset";
  $("#invite-title").textContent = info ? (reset ? t("invite.title_reset") : t("invite.title_new")) : t("invite.title_invalid");
  $("#invite-description").textContent = info
    ? t(reset ? "invite.desc_reset" : "invite.desc_new", { name: info.invitedBy || "admin" })
    : "";
  if (!info) message("#invite-message", invite.error || t("invite.invalid"));
  $("#invite-form").hidden = !info;
  const username = $("#invite-username");
  if (info && document.activeElement !== username && !username.value) username.value = info.username || "";
  username.readOnly = Boolean(reset);
  $("#invite-submit").textContent = reset ? t("invite.submit_reset") : t("invite.submit_new");
  const unavailable = webauthnUnavailableReason();
  $("#invite-passkey-box").hidden = !info;
  $("#invite-or").hidden = !info;
  $("#invite-passkey").disabled = Boolean(unavailable);
  $("#invite-passkey").title = unavailable || "";
}

async function submitInvitePassword(event) {
  event.preventDefault();
  const button = $("#invite-submit");
  message("#invite-message", "");
  if ($("#invite-password").value !== $("#invite-password2").value) {
    message("#invite-message", t("ob.password.mismatch"));
    return;
  }
  button.disabled = true;
  try {
    await jsonFetch("/api/invites/password", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: state.invite.token, username: $("#invite-username").value, password: $("#invite-password").value }),
    });
    await finishInvite();
  } catch (error) {
    message("#invite-message", error.message);
  } finally {
    button.disabled = false;
  }
}

async function submitInvitePasskey() {
  const button = $("#invite-passkey");
  button.disabled = true;
  message("#invite-message", "");
  try {
    await registerPasskey({
      username: $("#invite-username").value.trim(),
      credentialName: t("invite.passkey_name"),
      messageTarget: "#invite-message",
      invite: state.invite.token,
    });
    await finishInvite();
  } catch (error) {
    message("#invite-message", error.name === "NotAllowedError" ? t("passkey.cancelled") : error.message);
  } finally {
    button.disabled = false;
  }
}

async function finishInvite() {
  for (const id of ["invite-password", "invite-password2"]) $(`#${id}`).value = "";
  leaveInvite();
  await refreshAuthStatus();
}

$("#invite-form").addEventListener("submit", submitInvitePassword);
$("#invite-passkey").addEventListener("click", submitInvitePasskey);
