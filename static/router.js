// Adressen per scherm: /recipes, /offers/history, /stores/ah/orders/123, /cart, ... Het scherm dat open staat bepaalt het adres
// (routerSync) en een adres opent het scherm (routerApply): bij het laden van de pagina, bij terug en vooruit, en bij een link.
// De server geeft op al deze adressen dezelfde pagina terug (auth.PAGE_ROOTS), dus herladen en een bladwijzer werken.
//
// Navigatie zijn echte links (<a href>). Een gewone klik blijft in de app; met Ctrl, Cmd, Shift, Alt of de middelste knop doet de
// browser wat hij met elke link doet: een nieuw tabblad of venster. Laadt na alle andere scripts en gebruikt hun functies.

const ROUTE_PANELS = {
  recipes: "recipes",
  offers: "offers",
  import: "url",
  stores: "ah-items",
  "meal-kits": "meal-kits",
  cart: "basket",
  profile: "profile",
  settings: "settings",
  about: "about",
};
const PANEL_ROOTS = Object.fromEntries(Object.entries(ROUTE_PANELS).map(([root, panel]) => [panel, root]));

// Per scherm: het stuk na de wortel -> het subtab. Het eerste subtab heeft geen eigen stuk.
const ROUTE_SUBTABS = {
  recipes: { "": "planner", search: "search" },
  offers: { "": "offers-items", recipes: "offers-suggestions", history: "offers-history" },
  profile: { "": "profile-general", security: "profile-security", ai: "profile-ai" },
  settings: { "": "settings-link", users: "settings-users", audit: "settings-audit", system: "settings-system", appearance: "settings-appearance" },
};

// Winkels: per winkel de onderdelen en hun subtab. /stores/ah/receipts/<id> is een kassabon onder Bestellingen, en
// /stores/ah/imported/<ref> een ingelezen oude bestelling (zie price_import.py).
const STORE_SECTIONS = {
  ah: { recipes: "ah-items-recepten", lists: "ah-items-lijstjes", orders: "ah-items-bestellingen" },
  jumbo: { recipes: "ah-items-jumbo-recepten", lists: "ah-items-lijstjes", orders: "ah-items-bestellingen" },
  picnic: { recipes: "ah-items-picnic", favorites: "ah-items-picnic-favorieten", orders: "ah-items-picnic-bestellingen" },
  crisp: { favorites: "ah-items-crisp-favorieten", orders: "ah-items-crisp-bestellingen" },
};

const router = { started: false, applying: 0, pending: "" };

function routerModified(event) {
  return event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey;
}

function routerActivePanel() {
  return document.querySelector(".panel.is-active")?.id || "recipes";
}

function routerActiveSubtab(panel) {
  return document.querySelector(`#${panel} .subtab.is-active[data-subtab], #${panel} .subtab-menu__item.is-active[data-subtab]`)?.dataset.subtab || "";
}

function routerSlug(table, subtab) {
  return Object.entries(table || {}).find(([, id]) => id === subtab)?.[0] || "";
}

function routerCurrentPath() {
  const panel = routerActivePanel();
  const root = PANEL_ROOTS[panel] || "recipes";
  if (panel === "ah-items") {
    const store = state.itemsStore || "ah";
    const section = routerSlug(STORE_SECTIONS[store], routerActiveSubtab(panel));
    if (section === "lists" && state.ahOpenListId) return storesPath(store, "lists", state.ahOpenListId);
    const open = typeof ordersState !== "undefined" ? ordersState.open : null;
    if (section === "orders" && open) return storesPath(store, { receipt: "receipts", imported: "imported" }[open.kind] || "orders", open.id);
    return storesPath(store, section);
  }
  const sub = routerSlug(ROUTE_SUBTABS[root], routerActiveSubtab(panel));
  if (panel === "offers") {
    const store = offersState.store && offersState.store !== "ah" ? offersState.store : "";
    if (sub !== "history") return offersPath(sub, { store });
    const product = (offerHistory.selected || "").split(":").slice(1).join(":");
    return offersPath(sub, { store, q: offerHistory.query || "", product: offerHistory.pinned ? product : "" });
  }
  return `/${root}${sub ? `/${sub}` : ""}`;
}

// ---- adres bijwerken na een wissel van scherm

function routerSync({ replace = false } = {}) {
  if (!router.started || router.applying) return;
  const path = routerCurrentPath();
  const now = window.location.pathname + window.location.search;
  if (path !== now && !(now === "/" && path === "/recipes")) {
    history[replace ? "replaceState" : "pushState"]({ ms: true }, "", path);
  }
  routerRefreshLinks();
}

// De links in de subtabbalken hangen af van de gekozen winkel; dit houdt ze gelijk met wat een klik zou openen.
function routerRefreshLinks() {
  const itemsStore = state.itemsStore || "ah";
  document.querySelectorAll("#ah-items .subtab[data-subtab]").forEach((link) => {
    const section = routerSlug(STORE_SECTIONS[itemsStore], link.dataset.subtab);
    if (section) link.setAttribute("href", storesPath(itemsStore, section));
  });
  document.querySelectorAll("#items-store-list [data-store]").forEach((link) => link.setAttribute("href", storesPath(link.dataset.store)));
  const offersStore = typeof offersState !== "undefined" && offersState.store !== "ah" ? offersState.store : "";
  document.querySelectorAll("#offers .subtab[data-subtab]").forEach((link) => {
    link.setAttribute("href", offersPath(routerSlug(ROUTE_SUBTABS.offers, link.dataset.subtab), { store: offersStore }));
  });
  document.querySelectorAll("#offers-store-list [data-store]").forEach((link) => {
    const sub = routerSlug(ROUTE_SUBTABS.offers, routerActiveSubtab("offers"));
    link.setAttribute("href", offersPath(sub, { store: link.dataset.store === "ah" ? "" : link.dataset.store }));
  });
}

// ---- een adres openen

function routerTab(panel) {
  return document.querySelector(`.tab[data-tab="${panel}"]`);
}

function routerOpenSubtab(subtab) {
  const link = document.querySelector(`[data-subtab="${subtab}"]`);
  if (link && !link.hidden) link.click();
}

function routerIsOwner() {
  const auth = state.auth || {};
  return !auth.enabled || auth.isOwner !== false;
}

function routerApplyStores(parts) {
  showPanel("ah-items", routerTab("ah-items"));
  const [store, part = "", id = ""] = parts;
  if (store && STORE_SECTIONS[store] && store !== state.itemsStore) {
    if (!itemsStores().includes(store)) {
      router.pending = window.location.href; // de koppelingen zijn nog niet geladen: zie routerRetry
      return;
    }
    switchItemsStore(store);
  }
  const section = part === "receipts" || part === "imported" ? "orders" : part;
  const subtab = STORE_SECTIONS[state.itemsStore]?.[section];
  if (subtab) routerOpenSubtab(subtab);
  if (section === "lists") {
    if (id) openAhList(id, { push: false });
    else if (state.ahOpenListId) showAhListsOverview();
  }
  if (section === "orders" && typeof openPurchase === "function") {
    if (id) openPurchase({ receipts: "receipt", imported: "imported" }[part] || "order", id);
    else showPurchaseOverview();
  }
}

function routerApplyOffers(sub, params) {
  const store = params.get("store") || "ah";
  if (store !== offersState.store && OFFERS_STORES.includes(store)) {
    if (!offersStores().includes(store)) router.pending = window.location.href;
    else offersSwitchStore(store);
  }
  if (sub === "history") {
    $("#offers-history-search").value = params.get("q") || "";
    const product = params.get("product");
    offerHistory.pinned = Boolean(product);
    if (product) offerHistory.selected = `${offersState.store}:${product}`;
  }
  showPanel("offers", routerTab("offers"));
  routerOpenSubtab(ROUTE_SUBTABS.offers[sub] || "offers-items");
}

function routerApply(href) {
  const url = new URL(href, window.location.origin);
  const parts = url.pathname.split("/").filter(Boolean).map((part) => decodeURIComponent(part));
  const root = parts[0] || "recipes";
  const panel = ROUTE_PANELS[root];
  router.applying++;
  router.pending = "";
  try {
    if (!panel) {
      showPanel("recipes", routerTab("recipes"));
      routerOpenSubtab("planner");
    } else if (panel === "ah-items") {
      routerApplyStores(parts.slice(1));
    } else if (panel === "offers") {
      routerApplyOffers(parts[1] || "", url.searchParams);
    } else {
      const highlighted = ["profile", "settings", "about"].includes(panel) ? null : routerTab(panel);
      let subtab = ROUTE_SUBTABS[root]?.[parts[1] || ""];
      // Instellingen zijn van de beheerder, op Weergave na: een gewone gebruiker komt altijd daar uit.
      if (panel === "settings" && !routerIsOwner()) {
        subtab = "settings-appearance";
        if (url.pathname !== "/settings/appearance") history.replaceState({ ms: true }, "", "/settings/appearance");
      }
      showPanel(panel, highlighted);
      if (subtab) routerOpenSubtab(subtab);
    }
  } finally {
    router.applying--;
  }
  routerRefreshLinks();
}

function routerNavigate(href) {
  const url = new URL(href, window.location.origin);
  if (url.pathname + url.search !== window.location.pathname + window.location.search) history.pushState({ ms: true }, "", url.pathname + url.search);
  routerApply(url.href);
}

// Een winkel die in het adres staat maar waarvan de koppeling pas later binnenkomt: dan alsnog openen.
function routerRetry() {
  if (!router.pending || router.applying) return;
  const pending = router.pending;
  const url = new URL(pending);
  if (url.href !== window.location.href) {
    router.pending = "";
    return;
  }
  routerApply(pending);
}

// Na het inloggen een keer: het scherm uit het adres openen.
function routerStart() {
  if (router.started) return;
  router.started = true;
  routerApply(window.location.href);
}

// Gewone klik op een navigatielink: in de app blijven. Met een toets erbij laat de browser hem in een nieuw tabblad openen;
// dan mag de klik het scherm hier niet wisselen, dus stopt hij voordat de eigen handler van de link hem ziet.
document.addEventListener(
  "click",
  (event) => {
    const link = event.target.closest("a[data-nav], a[data-route]");
    if (!link) return;
    if (routerModified(event)) {
      event.stopImmediatePropagation();
      return;
    }
    event.preventDefault();
    if (link.hasAttribute("data-route")) routerNavigate(link.href);
  },
  true
);

window.addEventListener("popstate", () => {
  if (router.started) routerApply(window.location.href);
});
