// Profiel en het avatar-menu: de avatar met een uitklapmenu (Profiel, Instellingen, Uitloggen) en de profielpagina
// (displaynaam en avatar). Op een groot scherm staat de avatar in de kop, op een klein scherm in de tabbalk.
// Laadt na app.js en gebruikt daar de hulpfuncties van ($, message, jsonFetch, setBusy, t, showPanel, ...).

const AVATAR_SIZE = 256;
const AVATAR_ACCEPTED = ["image/png", "image/jpeg", "image/webp"];

function profileSignedIn() {
  const auth = state.auth || {};
  return Boolean(auth.authenticated) || !auth.enabled;
}

// Wat er in het menu staat: de displaynaam, anders de gebruikersnaam, anders "Beheerder" (inloggen staat uit).
function profileName() {
  const auth = state.auth || {};
  return (auth.displayName || auth.username || "").trim() || t("user.admin");
}

function profileInitials(name) {
  const words = String(name || "").trim().split(/\s+/).filter(Boolean);
  const letters = words.length > 1 ? [words[0], words[words.length - 1]].map((word) => Array.from(word)[0]) : Array.from(words[0] || "?").slice(0, 2);
  return letters.join("").toUpperCase();
}

// Een avatar tekenen: de afbeelding als die er is, anders de initialen. De initialen blijven eronder staan, zodat een
// afbeelding die niet laadt er niet leeg uitziet.
function profileAvatarHtml({ name, userId, hasAvatar, version }) {
  const image = hasAvatar
    ? `<img src="/api/profile/avatar/${encodeURIComponent(userId || "owner")}?v=${encodeURIComponent(version || 0)}" alt="" data-hide-on-error>`
    : "";
  return `<span class="avatar__initials">${escapeHtml(profileInitials(name))}</span>${image}`;
}

function renderProfileAvatars() {
  const auth = state.auth || {};
  const html = profileAvatarHtml({ name: profileName(), userId: auth.userId, hasAvatar: auth.hasAvatar, version: auth.avatarVersion });
  document.querySelectorAll("[data-avatar]").forEach((element) => {
    element.innerHTML = html;
  });
}

function renderUserMenu() {
  const auth = state.auth || {};
  const visible = profileSignedIn();
  $("#user-menu").hidden = !visible;
  $("#tab-avatar").hidden = !visible;
  if (!visible) profileMenuClose();
  const name = profileName();
  $("#user-menu-name").textContent = name;
  $("#user-menu-who-name").textContent = name;
  $("#user-menu-who-user").textContent = auth.username && auth.username !== name ? auth.username : "";
  $("#auth-logout").hidden = !auth.enabled;
  renderProfileAvatars();
  renderProfilePage();
}

// ---- Het menu
let profileMenuTrigger = null;

function profileMenuClose() {
  const list = $("#user-menu-list");
  if (list.hidden) return;
  list.hidden = true;
  document.querySelectorAll("[data-user-menu-trigger]").forEach((button) => button.setAttribute("aria-expanded", "false"));
  profileMenuTrigger = null;
}

function profileMenuOpen(trigger) {
  const list = $("#user-menu-list");
  list.hidden = false;
  profileMenuTrigger = trigger;
  trigger.setAttribute("aria-expanded", "true");
  // Onder de knop in de kop; boven de knop in de onderste tabbalk (daar is onder geen ruimte).
  const rect = trigger.getBoundingClientRect();
  const below = rect.top < window.innerHeight / 2;
  list.style.top = below ? `${Math.round(rect.bottom + 8)}px` : "auto";
  list.style.bottom = below ? "auto" : `${Math.round(window.innerHeight - rect.top + 8)}px`;
  // Aan de kant waar de knop zit uitlijnen, zodat het menu binnen het scherm blijft.
  const alignLeft = rect.left + rect.width / 2 < window.innerWidth / 2;
  list.style.left = alignLeft ? `${Math.max(8, Math.round(rect.left))}px` : "auto";
  list.style.right = alignLeft ? "auto" : `${Math.max(8, Math.round(window.innerWidth - rect.right))}px`;
  list.querySelector("button:not([hidden])")?.focus();
}

document.querySelectorAll("[data-user-menu-trigger]").forEach((trigger) => {
  trigger.addEventListener("click", (event) => {
    event.stopPropagation();
    if (profileMenuTrigger === trigger) profileMenuClose();
    else {
      profileMenuClose();
      profileMenuOpen(trigger);
    }
  });
});
document.addEventListener("click", (event) => {
  if (!event.target.closest("#user-menu-list")) profileMenuClose();
});
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape" || $("#user-menu-list").hidden) return;
  const trigger = profileMenuTrigger;
  profileMenuClose();
  trigger?.focus();
});
window.addEventListener("resize", profileMenuClose);
$("#user-menu-list").addEventListener("click", (event) => {
  const item = event.target.closest("button");
  if (!item) return;
  profileMenuClose();
  if (item.dataset.userAction === "profile") openProfile();
  if (item.dataset.userAction === "settings") openSettings();
  if (item.dataset.userAction === "about") showPanel("about");
});

// ---- De profielpagina
function renderProfilePage() {
  const auth = state.auth || {};
  const name = $("#profile-name");
  if (name && document.activeElement !== name) name.value = auth.displayName || "";
  const username = $("#profile-username");
  if (username) username.value = auth.username || "";
  const remove = $("#profile-avatar-remove");
  if (remove) remove.hidden = !auth.hasAvatar;
}

function profileApply(data) {
  state.auth = { ...state.auth, displayName: data.displayName, hasAvatar: data.hasAvatar, avatarVersion: data.avatarVersion };
  renderUserMenu();
  if (typeof usersState !== "undefined" && $("#settings-users.is-active") && typeof renderUsers === "function") loadUsers();
}

$("#profile-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("#profile-save");
  message("#profile-message", "");
  setBusy(button, true);
  try {
    const data = await jsonFetch("/api/profile", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ displayName: $("#profile-name").value }),
    });
    profileApply(data);
    message("#profile-message", t("profile.saved"), "success");
  } catch (error) {
    message("#profile-message", error.message);
  } finally {
    setBusy(button, false);
  }
});

// De afbeelding in de browser verkleinen tot een vierkant (uit het midden gesneden): kleiner om te sturen en te bewaren.
function profileResize(file) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const image = new Image();
    image.onload = () => {
      URL.revokeObjectURL(url);
      const side = Math.min(image.naturalWidth, image.naturalHeight);
      if (!side) return reject(new Error(t("profile.avatar.invalid")));
      const canvas = document.createElement("canvas");
      canvas.width = AVATAR_SIZE;
      canvas.height = AVATAR_SIZE;
      const context = canvas.getContext("2d");
      context.fillStyle = "#ffffff";
      context.fillRect(0, 0, AVATAR_SIZE, AVATAR_SIZE);
      context.drawImage(image, (image.naturalWidth - side) / 2, (image.naturalHeight - side) / 2, side, side, 0, 0, AVATAR_SIZE, AVATAR_SIZE);
      canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error(t("profile.avatar.invalid")))), "image/jpeg", 0.88);
    };
    image.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error(t("profile.avatar.invalid")));
    };
    image.src = url;
  });
}

$("#profile-avatar-upload").addEventListener("click", () => $("#profile-avatar-file").click());
$("#profile-avatar-file").addEventListener("change", async (event) => {
  const input = event.target;
  const file = input.files && input.files[0];
  input.value = "";
  if (!file) return;
  const button = $("#profile-avatar-upload");
  message("#profile-avatar-message", "");
  if (!AVATAR_ACCEPTED.includes(file.type)) return message("#profile-avatar-message", t("profile.avatar.invalid"));
  setBusy(button, true);
  try {
    const blob = await profileResize(file);
    profileApply(await jsonFetch("/api/profile/avatar", { method: "POST", headers: { "Content-Type": blob.type }, body: blob }));
  } catch (error) {
    message("#profile-avatar-message", error.message);
  } finally {
    setBusy(button, false);
  }
});

$("#profile-avatar-remove").addEventListener("click", async () => {
  message("#profile-avatar-message", "");
  try {
    profileApply(await jsonFetch("/api/profile/avatar", { method: "DELETE" }));
  } catch (error) {
    message("#profile-avatar-message", error.message);
  }
});

window.addEventListener("ms:languagechange", renderUserMenu);
