// Het mandje bij de aanbiedingen: op elke kaart en in elke regel het aantal dat al in het mandje van de winkel zit, met min, een
// invulveld en plus. Een wijziging staat meteen op het scherm en gaat na een korte pauze als exact aantal naar de winkel (0 haalt
// het product weg). Bij AH is het mandje de boodschappenlijst (ah.set_shopping_list_quantity), bij Picnic en Crisp het echte mandje.
// Jumbo kent geen exact aantal en heeft hier geen knoppen. Er wordt nooit een bestelling geplaatst.

const OFFER_CART_STORES = ["ah", "picnic", "crisp"];
const OFFER_CART_MAX = 99;
const OFFER_CART_PAUSE = 400; // zo lang na de laatste klik gaat het aantal naar de winkel: drie keer plus is een verzoek
const OFFER_CART_FRESH = 60000; // binnen een minuut vraagt een nieuwe lading van de pagina het mandje niet opnieuw op

// ready: het mandje van deze winkel is gelezen. counts: product-id -> aantal. timers: product-id -> wachtende verzending.
const offerCart = { store: "", ready: false, counts: new Map(), timers: new Map(), loadId: 0, loadedAt: 0 };

// AH is altijd een keuze (zonder koppeling mislukt het lezen en blijven de knoppen weg), de andere winkels alleen gekoppeld.
function offerCartAvailable(store) {
  return OFFER_CART_STORES.includes(store) && (store === "ah" || Boolean(state.linkedStores[store]));
}

function offerCartCounts(cart) {
  const counts = new Map();
  for (const item of (cart && cart.items) || []) {
    if (item && item.id != null && item.quantity > 0) counts.set(String(item.id), (counts.get(String(item.id)) || 0) + item.quantity);
  }
  return counts;
}

function offerCartQuantity(id) {
  return offerCart.counts.get(String(id)) || 0;
}

// De knoppen van een aanbieding. Verborgen tot het mandje gelezen is; een folderactie voor een hele lijn is geen product.
function offerCartControl(offer) {
  const id = String(offer.id);
  if (id.startsWith("folder:")) return "";
  const quantity = offerCartQuantity(id);
  const label = (key) => escapeHtml(t(key, { title: offer.title || "" }));
  return `<div class="qty offer-cart${quantity ? "" : " offer-cart--empty"}" data-cart="${escapeHtml(id)}"${offerCart.ready ? "" : " hidden"}>
      <button type="button" class="qty__step" data-cart-step="-1" aria-label="${label("list.qty_minus")}">${mdiIcon(MDI_MINUS)}</button>
      <input class="qty__input" type="number" inputmode="numeric" min="0" max="${OFFER_CART_MAX}" step="1" value="${quantity}" aria-label="${label("offers.cart.quantity")}">
      <button type="button" class="qty__step" data-cart-step="1" aria-label="${label("offers.cart.add")}" title="${label("offers.cart.add")}">${mdiIcon(MDI_PLUS)}</button>
    </div>`;
}

// Zet de aantallen op het scherm gelijk aan offerCart, voor een product of voor alle. Een veld waarin getypt wordt blijft staan.
function paintOfferCart(id = null) {
  const root = $("#offers-products");
  if (!root) return;
  root.querySelectorAll("[data-cart]").forEach((box) => {
    if (id !== null && box.dataset.cart !== String(id)) return;
    const quantity = offerCartQuantity(box.dataset.cart);
    box.hidden = !offerCart.ready;
    box.classList.toggle("offer-cart--empty", quantity === 0);
    const input = box.querySelector(".qty__input");
    if (input && document.activeElement !== input) input.value = String(quantity);
  });
}

// Leest het mandje van de winkel van de aanbiedingen. Mislukt dat (niet gekoppeld, winkel onbereikbaar), dan blijven de knoppen weg.
async function loadOfferCart(store, { force = false } = {}) {
  if (!force && offerCart.store === store && offerCart.ready && Date.now() - offerCart.loadedAt < OFFER_CART_FRESH) return;
  const loadId = ++offerCart.loadId;
  offerCart.timers.forEach((timer) => clearTimeout(timer));
  offerCart.timers.clear();
  if (offerCart.store !== store) {
    offerCart.ready = false;
    offerCart.counts = new Map();
  }
  offerCart.store = store;
  paintOfferCart();
  if (!offerCartAvailable(store)) {
    offerCart.ready = false;
    paintOfferCart();
    return;
  }
  try {
    const cart = await jsonFetch(`/api/${store}/cart`);
    if (loadId !== offerCart.loadId) return;
    offerCart.counts = offerCartCounts(cart);
    offerCart.ready = true;
    offerCart.loadedAt = Date.now();
  } catch (error) {
    if (loadId !== offerCart.loadId) return;
    offerCart.ready = false;
  }
  paintOfferCart();
}

// Stuurt het aantal van een product naar de winkel. Het antwoord is het hele mandje: dat wint, behalve voor producten waarvan
// nog een wijziging wacht. Bij een fout de melding en het mandje opnieuw van de winkel, zodat het scherm weer klopt.
async function sendOfferCartQuantity(store, id) {
  offerCart.timers.delete(id);
  const quantity = offerCartQuantity(id);
  const offer = offerById(id);
  try {
    const cart = await jsonFetch(`/api/${store}/cart/items/${encodeURIComponent(id)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ quantity, title: (offer && offer.title) || "" }),
    });
    if (offerCart.store !== store) return;
    const counts = offerCartCounts(cart);
    offerCart.timers.forEach((timer, pending) => {
      if (offerCart.counts.has(pending)) counts.set(pending, offerCart.counts.get(pending));
      else counts.delete(pending);
    });
    offerCart.counts = counts;
    offerCart.loadedAt = Date.now();
    message("#offers-message", "");
    paintOfferCart();
  } catch (error) {
    if (offerCart.store !== store) return;
    message("#offers-message", error.message);
    loadOfferCart(store, { force: true });
  }
}

// Een nieuw aantal voor een product: meteen op het scherm, en na een korte pauze naar de winkel.
function setOfferCartQuantity(id, value) {
  if (!offerCart.ready) return;
  const key = String(id);
  const quantity = Math.max(0, Math.min(OFFER_CART_MAX, Math.round(Number(value) || 0)));
  if (quantity) offerCart.counts.set(key, quantity);
  else offerCart.counts.delete(key);
  paintOfferCart(key);
  clearTimeout(offerCart.timers.get(key));
  const store = offerCart.store;
  offerCart.timers.set(key, setTimeout(() => sendOfferCartQuantity(store, key), OFFER_CART_PAUSE));
}

$("#offers-products").addEventListener("click", (event) => {
  const step = event.target.closest("[data-cart-step]");
  if (!step) return;
  const box = step.closest("[data-cart]");
  setOfferCartQuantity(box.dataset.cart, offerCartQuantity(box.dataset.cart) + Number(step.dataset.cartStep));
});

$("#offers-products").addEventListener("change", (event) => {
  const box = event.target.closest("[data-cart]");
  if (!box || !event.target.classList.contains("qty__input")) return;
  setOfferCartQuantity(box.dataset.cart, event.target.value);
  event.target.value = String(offerCartQuantity(box.dataset.cart)); // een aantal buiten 0 t/m 99 staat er weer binnen
});
