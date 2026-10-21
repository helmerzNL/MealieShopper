// Jumbo: koppelen (een regel uit de browserconsole plakken, zoals bij Albert Heijn) en verkennen.
// Laadt na app.js en gebruikt daar de hulpfuncties van ($, message, jsonFetch, setBusy, t, ...).
// De keuze van de supermarkt in de planner staat in app.js (renderStoreChoice).

const jumboState = { connected: false, report: null };

function renderJumboStatus() {
  $("#jumbo-unlink").hidden = !jumboState.connected;
  $("#jumbo-check").hidden = !jumboState.connected;
  message("#jumbo-status", jumboState.connected ? t("jumbo.linked") : t("jumbo.not_linked"), jumboState.connected ? "success" : "error");
}

async function refreshJumboStatus() {
  try {
    const data = await jsonFetch("/api/jumbo/auth/status");
    jumboState.connected = Boolean(data.connected);
    state.linkedStores.jumbo = jumboState.connected;
    renderJumboStatus();
  } catch (error) {
    message("#jumbo-status", error.message);
  }
  renderStoreChoice();
}

// ---- Het Jumbo-mandje in het tabblad Mandje (alleen lezen)
async function showJumboBasket() {
  const cart = await jsonFetch("/api/jumbo/cart");
  const items = cart.items || [];
  const lines = items
    .map((item) => {
      const price = item.price ? escapeHtml(formatPrice(item.price)) : "";
      return `<li class="basket-line"><span class="basket-title">${escapeHtml(item.title || "-")}</span><span class="basket-qty">${escapeHtml(item.quantity)}\u00d7</span><span class="basket-price">${price}</span><span></span></li>`;
    })
    .join("");
  const total = cart.totalPrice ? `<p><strong>${escapeHtml(t("picnic.basket_total", { amount: formatPrice(cart.totalPrice) }))}</strong></p>` : "";
  $("#basket-content").innerHTML = `
    ${items.length ? `<ul class="basket-lines">${lines}</ul>` : `<p class="muted">${escapeHtml(t("basket.jumbo_empty"))}</p>`}
    ${total}
    <p class="muted">${escapeHtml(t("basket.jumbo_hint"))}</p>`;
}

async function checkJumboConnection() {
  message("#jumbo-status", t("ah.checking"), "success");
  try {
    const data = await jsonFetch("/api/jumbo/auth/status?verify=1");
    if (data.verified) message("#jumbo-status", t("jumbo.works"), "success");
    else message("#jumbo-status", data.connected ? t("jumbo.broken", { error: data.error || "" }) : t("jumbo.not_linked"));
  } catch (error) {
    message("#jumbo-status", error.message);
  }
}

async function linkJumbo() {
  const button = $("#jumbo-link");
  const text = $("#jumbo-code").value.trim();
  if (!text) {
    message("#jumbo-message", t("jumbo.paste_first"));
    return;
  }
  setBusy(button, true, t("jumbo.linking"));
  message("#jumbo-message", "");
  try {
    await jsonFetch("/api/jumbo/auth", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code: text }),
    });
    $("#jumbo-code").value = "";
    message("#jumbo-message", t("jumbo.saved"), "success");
    await refreshJumboStatus();
  } catch (error) {
    message("#jumbo-message", error.message);
  } finally {
    setBusy(button, false, t("jumbo.linking"));
  }
}

async function unlinkJumbo() {
  try {
    await jsonFetch("/api/jumbo/auth/logout", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    jumboState.connected = false;
    state.linkedStores.jumbo = false;
    renderJumboStatus();
    renderStoreChoice();
  } catch (error) {
    message("#jumbo-status", error.message);
  }
}

// ---- Verkenner (alleen de beheerder)
function renderJumboExploreSummary() {
  const report = jumboState.report;
  if (report) $("#jumbo-explore-summary").textContent = t("explore.summary", { ok: report.ok, total: report.probes.length });
}

async function runJumboExplore() {
  const button = $("#jumbo-explore-run");
  setBusy(button, true);
  message("#jumbo-explore-message", "");
  try {
    jumboState.report = await jsonFetch("/api/jumbo/explore", { method: "POST" });
    renderJumboExploreSummary();
    $("#jumbo-explore-output").textContent = JSON.stringify(jumboState.report, null, 2);
    $("#jumbo-explore-report").hidden = false;
    if (!jumboState.report.linked) message("#jumbo-explore-message", t("jumbo.explore_not_linked"));
  } catch (error) {
    message("#jumbo-explore-message", error.message);
  } finally {
    setBusy(button, false);
  }
}

$("#jumbo-link").addEventListener("click", linkJumbo);
$("#jumbo-check").addEventListener("click", checkJumboConnection);
$("#jumbo-unlink").addEventListener("click", unlinkJumbo);
$("#jumbo-explore-run").addEventListener("click", runJumboExplore);
$("#jumbo-explore-copy").addEventListener("click", async (event) => {
  const text = $("#jumbo-explore-output").textContent;
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const range = document.createRange();
    range.selectNodeContents($("#jumbo-explore-output"));
    getSelection().removeAllRanges();
    getSelection().addRange(range);
    document.execCommand("copy");
  }
  event.currentTarget.textContent = t("users.copied");
  setTimeout(() => (event.currentTarget.textContent = t("users.copy")), 2000);
});

// ---- Je receptenlijsten bij Jumbo (tab Winkels > Recepten): Favorieten en eigen lijstjes, per pagina, openen en importeren
const jumboFavorites = { lists: [], listsLoaded: false, recipes: [], total: 0, page: 0, hasNext: false, loaded: false };

function renderJumboRecipeLists() {
  const select = $("#jumbo-recipe-list");
  const keep = select.value;
  select.innerHTML = jumboFavorites.lists
    .map((list) => `<option value="${escapeHtml(list.id)}">${escapeHtml(`${list.name} (${I18N.formatNumber(list.count)})`)}</option>`)
    .join("");
  if (jumboFavorites.lists.some((list) => list.id === keep)) select.value = keep;
  select.hidden = jumboFavorites.lists.length < 2; // met alleen Favorieten valt er niets te kiezen
}

async function loadJumboRecipeLists() {
  try {
    jumboFavorites.lists = (await jsonFetch("/api/jumbo/recipe-lists")).lists || [];
  } catch {
    jumboFavorites.lists = []; // het overzicht lukt niet: toon alleen je Favorieten
  }
  jumboFavorites.listsLoaded = true;
  renderJumboRecipeLists();
}

function currentJumboRecipeList() {
  return jumboFavorites.lists.find((list) => list.id === $("#jumbo-recipe-list").value);
}

function renderJumboFavorites() {
  const data = jumboFavorites;
  $("#jumbo-favorites").innerHTML = data.recipes.map((recipe) => renderStoreRecipeCard("jumbo", recipe)).join("");
  $("#jumbo-favorites-status").textContent = data.recipes.length ? t("rsearch.count", { shown: I18N.formatNumber(data.recipes.length), total: I18N.formatNumber(data.total) }) : "";
  $("#jumbo-favorites-more").hidden = !data.hasNext;
  if (data.loaded && !data.recipes.length) message("#jumbo-favorites-message", t("jumbo.favorites.none"), "success");
}

async function loadJumboFavorites(append = false) {
  const button = append ? $("#jumbo-favorites-more") : $("#load-jumbo-favorites");
  setBusy(button, true);
  message("#jumbo-favorites-message", "");
  if (!jumboFavorites.listsLoaded) await loadJumboRecipeLists();
  const page = append ? jumboFavorites.page + 1 : 1;
  const list = currentJumboRecipeList();
  // Favorieten hebben hun eigen aanvraag; een eigen lijstje wordt op id gevraagd.
  const url = list && list.kind !== "favorites" ? `/api/jumbo/recipe-lists/${encodeURIComponent(list.id)}?page=${page}` : `/api/jumbo/favorite-recipes?page=${page}`;
  try {
    const answer = await jsonFetch(url);
    const known = new Set(append ? jumboFavorites.recipes.map((recipe) => String(recipe.id)) : []);
    const fresh = (answer.recipes || []).filter((recipe) => !known.has(String(recipe.id)));
    jumboFavorites.recipes = append ? [...jumboFavorites.recipes, ...fresh] : fresh;
    jumboFavorites.total = answer.total ?? jumboFavorites.recipes.length;
    jumboFavorites.page = page;
    // Een pagina zonder nieuwe recepten: stop met "Meer recepten", anders blijft die knop eindeloos staan.
    jumboFavorites.hasNext = Boolean(answer.hasNext) && (!append || Boolean(fresh.length));
    jumboFavorites.loaded = true;
    renderJumboFavorites();
  } catch (error) {
    if (!append) jumboFavorites.recipes = [];
    jumboFavorites.hasNext = false;
    renderJumboFavorites();
    message("#jumbo-favorites-message", error.message);
  } finally {
    setBusy(button, false);
  }
}

$("#jumbo-recipe-list").addEventListener("change", () => loadJumboFavorites(false));
$("#load-jumbo-favorites").addEventListener("click", () => loadJumboFavorites(false));
$("#jumbo-favorites-more").addEventListener("click", () => loadJumboFavorites(true));
$("#jumbo-recipe-back").addEventListener("click", () => showRecipeList(FAVORITES_HOST));
$("#jumbo-favorites").addEventListener("click", (event) => {
  const open = event.target.closest("[data-store-open]");
  const importButton = event.target.closest("[data-store-import]");
  if (open) openStoreRecipe("jumbo", open.dataset.storeOpen.split(":", 2)[1], FAVORITES_HOST);
  if (importButton) importStoreRecipe("jumbo", importButton.dataset.storeImport.split(":", 2)[1], importButton, importButton.closest(".recipe-card").querySelector(".recipe-import-result"));
});

window.addEventListener("ms:languagechange", () => {
  renderJumboStatus();
  renderStoreChoice();
  renderJumboExploreSummary();
  if (jumboFavorites.listsLoaded) renderJumboRecipeLists();
  if (jumboFavorites.loaded) renderJumboFavorites();
});
