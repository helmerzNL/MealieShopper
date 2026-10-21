// Het tabblad Mandje: het echte winkelmandje van AH, Picnic of Jumbo, voor zover gekoppeld.
// Laadt na picnic.js, jumbo.js en orders.js; elk van die scripts weet zelf hoe zijn mandje eruitziet
// (showPicnicBasket, showJumboBasket, showAhBasket). Dit script kiest alleen de winkel.

const BASKET_STORES = ["ah", "picnic", "jumbo"];
const basketState = { store: "ah" };

function basketStores() {
  return BASKET_STORES.filter((key) => key === "ah" || state.linkedStores[key]);
}

function basketTabActive() {
  return Boolean(document.querySelector("#basket.is-active"));
}

function renderBasketStoreChoice() {
  const slot = $("#basket-store-slot");
  if (!slot) return;
  const available = basketStores();
  if (!available.includes(basketState.store)) {
    let remembered = "";
    try {
      remembered = localStorage.getItem("ms-basket-store") || "";
    } catch {
      // Zonder opslag begint de pagina bij de winkel van de planner.
    }
    basketState.store = [remembered, state.preferredStore].find((key) => available.includes(key)) || "ah";
  }
  slot.innerHTML =
    available.length < 2
      ? ""
      : `<select id="basket-store" class="store-select" aria-label="${escapeHtml(t("cart.store"))}">
          ${available.map((key) => `<option value="${key}"${basketState.store === key ? " selected" : ""}>${escapeHtml(STORES[key].name)}</option>`).join("")}
        </select>`;
  $("#basket-store")?.addEventListener("change", (event) => {
    basketState.store = event.target.value;
    try {
      localStorage.setItem("ms-basket-store", basketState.store);
    } catch {
      // De keuze geldt dan alleen tot je de pagina herlaadt.
    }
    showBasket();
  });
  $("#basket-intro").textContent = available.length < 2 ? t("basket.intro_one") : t("basket.intro");
}

// Toon het mandje van de gekozen winkel. Elke winkel schrijft in #basket-content en #basket-message.
async function showBasket() {
  renderBasketStoreChoice();
  message("#basket-message", "");
  const content = $("#basket-content");
  content.innerHTML = `<p class="muted">${escapeHtml(t("basket.loading"))}</p>`;
  const stores = { ah: showAhBasket, picnic: showPicnicBasket, jumbo: showJumboBasket };
  try {
    await stores[basketState.store]();
  } catch (error) {
    content.innerHTML = "";
    message("#basket-message", error.message);
  }
}

// Vanuit de planner: ga naar het mandje van de winkel waar je net producten in zette.
function openBasket(store) {
  if (BASKET_STORES.includes(store)) basketState.store = store;
  document.querySelector('.tab[data-tab="basket"]').click();
}

$("#basket-refresh").addEventListener("click", showBasket);
window.addEventListener("ms:languagechange", () => {
  renderBasketStoreChoice();
  if (basketTabActive()) showBasket();
});
renderBasketStoreChoice();
