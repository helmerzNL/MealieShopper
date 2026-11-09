// Het tabblad Aanbiedingen: de aanbiedingen van een winkel (AH Bonus, Picnic, Jumbo en Crisp) per categorie, en receptvoorstellen
// waarvan ingredienten in de aanbieding zijn. Alles komt uit een lokale kopie; die wordt elke dag ververst en kan de
// beheerder onder Instellingen > Koppelen meteen synchroniseren.
// Laadt na basket.js en gebruikt STORES, state, formatPrice, formatSyncTime, getWeekBounds, weekFrom, weekStartOf, weekOffsetText en setPlanWeek uit app.js.
//
// Bij AH staan ook je persoonlijke aanbiedingen (de Bonus Box) in de lijst, bovenaan en met een eigen label; met de schakelaar
// "Alleen Bonus Box" zie je alleen die. De schakelaar onthoudt deze browser.
//
// De week is die van het weekmenu (state.weekOffset): plan je volgende week, dan staan hier de aanbiedingen van volgende week, en kies
// je hier een andere week, dan gaat het weekmenu mee. Eerdere weken zijn er niet: verlopen aanbiedingen staan in de Prijsgeschiedenis.

const OFFERS_STORES = ["ah", "picnic", "jumbo", "crisp"];
const OFFER_ROWS = 3; // zoveel rijen aanbiedingen staan er eerst; de knop Meer voegt er steeds zoveel toe
const OFFERS_PERSONAL_KEY = "ms-offers-personal";
const OFFERS_GOOD_KEY = "ms-offers-good";
const OFFERS_SORT_KEY = "ms-offers-sort";
const OFFERS_VIEW_KEY = "ms-offers-view";
const OFFERS_FILTERS_KEY = "ms-offers-filters"; // per winkel: "ms-offers-filters:ah"
const OFFERS_BRAND_CHIPS = 12; // zoveel merken staan er open in de zijbalk, de rest achter Meer merken
const OFFERS_SORTS = ["best", "store", "discount", "unit", "lowest"];
const OFFERS_VIEWS = ["cards", "compact"];
const OFFER_COMPACT_ROWS = 8; // in de compacte weergave staan er per "rij" van de knop Meer zoveel regels
const offersState = {
  store: "ah", supported: true, offers: [], categories: [], category: "all", course: "all", recipes: [], rows: OFFER_ROWS, weekStart: "",
  personal: null, personalOnly: false, search: "", goodOnly: false, sort: "best", view: "cards", filters: null, brandsOpen: false,
  // chosen: zelf een winkel gekozen (menu of adres); tot dan volgt de pagina de voorkeurssupermarkt (Instellingen, Voorkeuren).
  // waiting: de pagina wacht nog op de koppeling van die winkel, zodat niet eerst de aanbiedingen van AH voorbijflitsen.
  chosen: false, waiting: false,
};
try {
  offersState.personalOnly = localStorage.getItem(OFFERS_PERSONAL_KEY) === "1";
  offersState.goodOnly = localStorage.getItem(OFFERS_GOOD_KEY) === "1";
  const sort = localStorage.getItem(OFFERS_SORT_KEY);
  if (OFFERS_SORTS.includes(sort)) offersState.sort = sort;
  const view = localStorage.getItem(OFFERS_VIEW_KEY);
  if (OFFERS_VIEWS.includes(view)) offersState.view = view;
} catch {
  // Zonder opslag staan de schakelaars uit en is de volgorde die van de winkel.
}

function offersRemember(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {
    // De keuze geldt dan alleen tot je de pagina herlaadt.
  }
}

// De filters in de zijbalk: merken (meerdere), eerder gekocht, minimale korting (procent onder de laagste gewone prijs van 30 dagen),
// prijs per eenheid hooguit (in een eenheid), laagste prijs in een jaar (yearLow) en zelden in de aanbieding (minder dan zes keer per
// jaar, folderacties meegeteld). Per winkel onthouden, want merken verschillen per winkel.
function offersEmptyFilters() {
  return { brands: [], bought: false, minDiscount: 0, maxUnitPrice: { value: null, unit: "kg" }, yearLow: false, rare: false };
}

function offersFilters() {
  if (!offersState.filters) offersState.filters = offersLoadFilters(offersState.store);
  return offersState.filters;
}

function offersLoadFilters(store) {
  const empty = offersEmptyFilters();
  try {
    const saved = JSON.parse(localStorage.getItem(`${OFFERS_FILTERS_KEY}:${store}`) || "null");
    if (!saved || typeof saved !== "object") return empty;
    return {
      brands: Array.isArray(saved.brands) ? saved.brands.filter((brand) => typeof brand === "string") : [],
      bought: saved.bought === true,
      minDiscount: Number.isFinite(saved.minDiscount) ? Math.max(0, Math.min(75, saved.minDiscount)) : 0,
      maxUnitPrice: { value: Number.isFinite(saved.maxUnitPrice && saved.maxUnitPrice.value) ? saved.maxUnitPrice.value : null, unit: ["kg", "l", "stuk"].includes(saved.maxUnitPrice && saved.maxUnitPrice.unit) ? saved.maxUnitPrice.unit : "kg" },
      yearLow: saved.yearLow === true,
      rare: saved.rare === true,
    };
  } catch {
    return empty;
  }
}

function offersSaveFilters() {
  offersRemember(`${OFFERS_FILTERS_KEY}:${offersState.store}`, JSON.stringify(offersFilters()));
}

// Laat de aanbieding de filters door? Elk filter is een EN. Zonder badge voldoet een aanbieding niet aan een minimale korting, zonder
// prijs per eenheid niet aan een grens; een grens in een andere eenheid dan die van het product raakt het niet.
function offersFilterMatch(offer, filters) {
  if (filters.brands.length && !filters.brands.includes(offer.brand || "")) return false;
  if (filters.bought && !offer.bought) return false;
  if (filters.minDiscount > 0 && !(offer.badge && typeof offer.badge.percent === "number" && offer.badge.percent >= filters.minDiscount)) return false;
  const limit = filters.maxUnitPrice;
  if (limit && typeof limit.value === "number") {
    const per = offer.unitPricePer;
    if (!per || typeof per.value !== "number") return false;
    if (per.unit === limit.unit && per.value > limit.value + 0.005) return false;
  }
  if (filters.yearLow && !(offer.yearLow && offer.yearLow.status === "lowest")) return false;
  if (filters.rare && !(offer.verdict && typeof offer.verdict.timesThisYear === "number" && offer.verdict.timesThisYear < OFFERS_OFTEN_PER_YEAR)) return false;
  return true;
}

// Zoals prices.OFTEN_PER_YEAR: vanaf zoveel keer per jaar komt een aanbieding vaak terug.
const OFFERS_OFTEN_PER_YEAR = 6;

// De aanbiedingen die de filters doorlaten, met de familieregel: een aanbieding blijft als een verpakking van dezelfde familie
// voldoet, en is dan gedempt (offersDimmed) als ze zelf niet voldoet. Zo zie je dat de 1000 g er is, ook als alleen de 300 g in de
// aanbieding is.
const offersDimmedIds = new Set();

function offersFilteredWithFamilies(list) {
  const filters = offersFilters();
  offersDimmedIds.clear();
  const passing = new Set();
  const families = new Set();
  list.forEach((offer) => {
    if (offersFilterMatch(offer, filters)) {
      passing.add(offer.id);
      if (offer.family && offer.family.key) families.add(offer.family.key);
    }
  });
  return list.filter((offer) => {
    if (passing.has(offer.id)) return true;
    if (offer.family && offer.family.key && families.has(offer.family.key)) {
      offersDimmedIds.add(offer.id);
      return true;
    }
    return false;
  });
}

function offersDimmed(offer) {
  return offersDimmedIds.has(offer.id);
}

// De merken in de lijst met het aantal, na de andere filters (niet het merkfilter zelf), op aantal en dan naam.
function offersBrandCounts(list) {
  const filters = { ...offersFilters(), brands: [] };
  const counts = new Map();
  list.forEach((offer) => {
    if (!offersFilterMatch(offer, filters)) return;
    const brand = offer.brand || "";
    if (brand) counts.set(brand, (counts.get(brand) || 0) + 1);
  });
  return [...counts.entries()].map(([brand, count]) => ({ brand, count })).sort((a, b) => b.count - a.count || a.brand.localeCompare(b.brand));
}

// De actieve filters als verwijderbare chips. Een merk dat deze week niet in de lijst staat blijft een chip: nooit stil wissen.
function offersActiveFilterChips() {
  const filters = offersFilters();
  const chips = filters.brands.map((brand) => ({ key: `brand:${brand}`, label: brand }));
  if (filters.bought) chips.push({ key: "bought", label: t("offers.filters.bought") });
  if (filters.minDiscount > 0) chips.push({ key: "minDiscount", label: t("offers.filters.min_discount_chip", { percent: I18N.formatNumber(filters.minDiscount) }) });
  if (typeof filters.maxUnitPrice.value === "number") {
    chips.push({ key: "maxUnitPrice", label: t("offers.filters.max_unit_chip", { price: formatPrice(filters.maxUnitPrice.value), unit: offersUnitLabel(filters.maxUnitPrice.unit) }) });
  }
  if (filters.yearLow) chips.push({ key: "yearLow", label: t("offers.filters.year_low") });
  if (filters.rare) chips.push({ key: "rare", label: t("offers.filters.rare") });
  return chips;
}

function offersUnitLabel(unit) {
  return t({ kg: "offers.unit.kg", l: "offers.unit.l" }[unit] || "offers.unit.stuk");
}

function offersActiveFilterCount() {
  return offersActiveFilterChips().length;
}

// Alleen de persoonlijke aanbiedingen (de Bonus Box): als de schakelaar aan staat en de winkel ze heeft.
function offersPersonalOnly() {
  return offersState.personalOnly && Boolean(offersState.personal && offersState.personal.supported);
}

// De aanbiedingen die de schakelaars en het zoekveld doorlaten, in de volgorde van de winkel: de Bonus Box, het zoekveld (elk woord
// moet in de naam of het merk staan, zonder hoofdletters en accenten) en "alleen echt voordelig" (het oordeel is good).
function offersFiltered() {
  let shown = offersPersonalOnly() ? offersState.offers.filter((offer) => offer.personal) : offersState.offers;
  const search = offersState.search.trim();
  if (search) shown = shown.filter((offer) => matchesWords(offerSearchText(offer), search));
  if (offersState.goodOnly) shown = shown.filter((offer) => offer.verdict && offer.verdict.kind === "good");
  return offersFilteredWithFamilies(shown);
}

// De tekst waarop het zoekveld zoekt (naam en merk, gevouwen), per aanbieding één keer berekend. Niet op de aanbieding zelf gezet:
// de lijst wordt vergeleken met het antwoord van de server om te zien of er iets veranderde (offersCache).
const offerSearchTexts = new WeakMap();

function offerSearchText(offer) {
  let text = offerSearchTexts.get(offer);
  if (text === undefined) {
    text = foldText(`${offer.title || ""} ${offer.brand || ""}`);
    offerSearchTexts.set(offer, text);
  }
  return text;
}

// De categorieën met het aantal dat ook door de schakelaars en het zoekveld komt. Een categorie zonder treffers valt weg.
function offersShownCategories() {
  const counts = {};
  offersFiltered().forEach((offer) => {
    counts[offer.category] = (counts[offer.category] || 0) + 1;
  });
  return offersState.categories.filter((category) => counts[category.id]).map((category) => ({ id: category.id, count: counts[category.id] }));
}

// De sorteerkeuze. Winkelvolgorde is de lijst zoals hij is. Hoogste korting: de badge aflopend, gemeten voor claim bij gelijk, zonder
// badge onderaan. Laagste prijs per eenheid: eerst kilo, dan liter, dan stuk (zoals het userscript), daarbinnen oplopend, zonder
// eenheid onderaan. Laagste ooit eerst: de trede van het prijslabel, daarbinnen op korting. Stabiel: bij gelijk blijft de winkelvolgorde.
const OFFER_UNIT_ORDER = { kg: 0, l: 1, stuk: 2 };
const OFFER_LABEL_ORDER = { lowest_ever: 0, lowest_12m: 1, lowest_6m: 2, lowest_seen: 3 };

function offerDiscountKey(offer) {
  const badge = offer.badge;
  if (!badge || typeof badge.percent !== "number") return [1, 0, 1];
  return [0, -badge.percent, badge.source === "history" ? 0 : 1];
}

// Beste eerst: wat nu op of onder de laagste prijs van de afgelopen twaalf maanden staat (prices.yearLow) en per eenheid niet
// duurder is dan een andere verpakking van hetzelfde product, op korting en dan op prijs per eenheid. Daarna de rest op korting,
// daarbinnen hoe weinig het boven de laagste van het jaar zit.
function offerBestKey(offer) {
  const low = offer.yearLow || {};
  const cheaper = offer.verdict && offer.verdict.cheaperPack;
  const percent = offer.badge && typeof offer.badge.percent === "number" ? offer.badge.percent : -1;
  const above = typeof low.above === "number" ? low.above : 999;
  const perUnit = offer.unitPricePer && typeof offer.unitPricePer.value === "number" ? offer.unitPricePer.value : 999;
  return [low.status === "lowest" && !cheaper ? 0 : 1, -percent, above, perUnit];
}

function offerSortKey(offer) {
  if (offersState.sort === "best") return offerBestKey(offer);
  if (offersState.sort === "discount") return offerDiscountKey(offer);
  if (offersState.sort === "unit") {
    const per = offer.unitPricePer;
    return per && typeof per.value === "number" ? [0, OFFER_UNIT_ORDER[per.unit] ?? 3, per.value] : [1, 3, 0];
  }
  if (offersState.sort === "lowest") {
    const kind = offer.priceLabel && offer.priceLabel.kind;
    return [OFFER_LABEL_ORDER[kind] ?? 4, ...offerDiscountKey(offer)];
  }
  return [0];
}

// Verpakkingsfamilies (prices.family): de aanbiedingen van hetzelfde product in zijn verpakkingen op een kaart. De kop van de groep
// is haar eerste aanbieding in de sorteervolgorde die niet per eenheid duurder is dan een andere verpakking, anders gewoon de
// eerste, en de groep staat op de plek van die kop. In winkelvolgorde blijft de groep bij haar eerste aanbieding staan.
function offerFamilies(sorted) {
  const byKey = new Map();
  sorted.forEach((offer, index) => {
    const key = offer.family && offer.family.key ? offer.family.key : `id:${offer.id}`;
    let group = byKey.get(key);
    if (!group) {
      group = { key, head: offer, offers: [], packs: (offer.family && offer.family.packs) || [], at: index };
      byKey.set(key, group);
    }
    group.offers.push(offer);
  });
  const groups = [...byKey.values()];
  if (offersState.sort === "store") return groups;
  groups.forEach((group) => {
    const head = group.offers.find((offer) => !(offer.verdict && offer.verdict.cheaperPack)) || group.offers[0];
    group.head = head;
    group.at = sorted.indexOf(head);
    group.offers = [head, ...group.offers.filter((offer) => offer !== head)];
  });
  return groups.sort((a, b) => a.at - b.at);
}

// "duurder per kilo dan 1000 g": de aanbieding is per eenheid duurder dan een andere verpakking van hetzelfde product.
function offerPackWarning(offer) {
  const pack = offer.verdict && offer.verdict.cheaperPack;
  if (!pack) return "";
  return t("offers.pack.pricier_than", { size: pack.unitSize || pack.title || "", unit: t({ kg: "offers.unit.kg", l: "offers.unit.l" }[(pack.unitPrice || {}).unit] || "offers.unit.stuk") });
}

// De regels van de verpakkingen onder een kaart: maat, prijs, prijs per eenheid, een punt voor "in de aanbieding", de goedkoopste
// per eenheid vet. Een regel die per eenheid duurder is dan de goedkoopste krijgt de melding. Niets bij een verpakking.
function offerPackRows(offer) {
  const family = offer.family || {};
  const packs = family.packs || [];
  const note = family.unknownPacks ? `<small class="muted offer-packs__note">${escapeHtml(t("offers.pack.unknown"))}</small>` : "";
  if (packs.length < 2) return note;
  const cheapest = packs.find((pack) => pack.cheapest);
  const rows = packs
    .map((pack) => {
      const member = offerById(pack.productId);
      const pricier = cheapest && !pack.cheapest && pack.unitPrice && cheapest.unitPrice && pack.unitPrice.unit === cheapest.unitPrice.unit && pack.unitPrice.value > cheapest.unitPrice.value;
      const cls = ["offer-pack", pack.cheapest ? "offer-pack--cheapest" : "", pack.onOffer ? "offer-pack--offer" : "", member && offersDimmed(member) ? "offer-pack--dimmed" : ""].filter(Boolean).join(" ");
      const warning = pricier && pack.onOffer ? `<small class="offer-pack__warning">${escapeHtml(t("offers.pack.pricier_than", { size: cheapest.unitSize || "", unit: t({ kg: "offers.unit.kg", l: "offers.unit.l" }[cheapest.unitPrice.unit] || "offers.unit.stuk") }))}</small>` : "";
      return `<li class="${cls}" data-zoom="${escapeHtml(pack.productId)}">
        <span class="offer-pack__size">${pack.onOffer ? '<i class="offer-pack__dot" aria-hidden="true"></i>' : ""}${escapeHtml(pack.unitSize || "")}</span>
        <span class="offer-pack__price">${escapeHtml(formatPrice(pack.price))}</span>
        <span class="offer-pack__unit">${escapeHtml(offerPerUnitText(pack.unitPrice))}${pack.cheapest ? ` <b>${escapeHtml(t("offers.pack.cheapest"))}</b>` : ""}</span>
        ${warning}
      </li>`;
    })
    .join("");
  return `<ul class="offer-packs" aria-label="${escapeHtml(t("offers.pack.list"))}">${rows}</ul>${note}`;
}

function offersSorted(list) {
  if (offersState.sort === "store") return list;
  const keyed = list.map((offer, index) => ({ offer, index, key: offerSortKey(offer) }));
  keyed.sort((a, b) => {
    for (let at = 0; at < Math.max(a.key.length, b.key.length); at += 1) {
      const diff = (a.key[at] ?? 0) - (b.key[at] ?? 0);
      if (diff) return diff;
    }
    return a.index - b.index;
  });
  return keyed.map((item) => item.offer);
}

// Het label van een persoonlijke aanbieding: groen als ze geactiveerd is, geel als dat nog moet in de app van de winkel.
function offerPersonalBadge(offer) {
  if (!offer.personal) return "";
  const state = offer.activated === true ? "on" : offer.activated === false ? "off" : "";
  const text = t(state === "on" ? "offers.personal.badge_on" : state === "off" ? "offers.personal.badge_off" : "offers.personal.badge");
  return `<small class="offer-card__personal${state ? ` offer-card__personal--${state}` : ""}">${escapeHtml(text)}</small>`;
}

// De week van de aanbiedingen: die van het weekmenu, maar nooit een eerdere dan deze (staat het weekmenu op vorige week, dan deze week).
function offersWeekOffset() {
  return Math.max(0, state.weekOffset);
}

// De aanbiedingen lopen per week van maandag t/m zondag (de bonusweek van AH), ook als het weekmenu op een andere dag begint.
// Bij een weekmenu-week hoort de bonusweek waar haar middelste dag in valt: die overlapt het meest (za 7 t/m vr 13 nov: ma 9 t/m zo 15 nov).
function bonusWeekOf(planWeek) {
  return weekFrom(weekStartOf(new Date(`${planWeek.days[3]}T00:00:00`), 1));
}

function offersWeek() {
  return bonusWeekOf(getWeekBounds(offersWeekOffset()));
}

// De week begint nu op een andere dag (zie applyWeekStart). Hoort er daardoor een andere bonusweek bij, dan die opnieuw laden.
function offersWeekStartChanged() {
  if (offersState.weekStart && offersState.weekStart !== offersWeek().start) showOffers();
  else renderOffersWeek();
}

// Zolang niet bekend is op welke dag de week begint, wacht de pagina (showOffers). Is het niet binnen een paar seconden bekend,
// dan laadt hij toch, met maandag: een trage of kapotte Mealie-status mag de aanbiedingen niet tegenhouden.
const OFFERS_WEEK_START_WAIT = 3000;

function offersWeekStartKnown() {
  clearTimeout(offersState.weekStartTimer);
  offersState.weekStartTimer = 0;
  if (offersState.waitingWeek && offersTabActive()) showOffers();
}

function renderOffersWeek() {
  const offset = offersWeekOffset();
  const week = offersWeek();
  document.querySelectorAll(".offers-week").forEach((bar) => {
    bar.querySelector(".offers-week__range").textContent = week.label;
    bar.querySelector(".offers-week__offset").textContent = weekOffsetText(offset);
    bar.querySelector('[data-offers-week="-1"]').disabled = offset === 0;
  });
}

function offersChangeWeek(step) {
  const offset = Math.max(0, offersWeekOffset() + step);
  if (offset === state.weekOffset) return;
  setPlanWeek(offset);
  showOffers();
}

// De winkels die op Aanbiedingen te kiezen zijn. In de prijsgeschiedenis zijn dat ze allemaal: daar staan ook prijzen uit
// een prijsarchief of van kassabonnen, van een winkel waar je geen account aan gekoppeld hebt.
function offersStores() {
  return OFFERS_STORES.filter((key) => offersHistoryActive() || key === "ah" || state.linkedStores[key]);
}

function offersTabActive() {
  return Boolean(document.querySelector("#offers.is-active"));
}

function offersHistoryActive() {
  return Boolean(document.querySelector("#offers-history.is-active"));
}

// Aanbiedingen opent met de voorkeurssupermarkt, tot je zelf een winkel kiest; is die (nog) niet gekoppeld, dan met AH. Wacht
// een adres nog op een koppeling (router.pending), dan beslist dat adres. Staat de pagina niet open, dan laadt er niets: dat
// doet showOffers bij het openen.
function offersFollowPreferred() {
  if (offersState.chosen || (typeof router !== "undefined" && router.pending)) return;
  const store = defaultStoreOf(offersStores());
  if (store === offersState.store) return;
  if (offersTabActive()) {
    offersSwitchStore(store, { chosen: false });
    return;
  }
  offersState.store = store;
  offersState.category = "all";
  offersState.course = "all";
  if (typeof offerHistoryStoreChanged === "function") offerHistoryStoreChanged();
}

// De winkelkeuze is het eerste uitklapmenu in de subtabbalk, zoals bij Winkels; met maar een winkel valt er niets te kiezen.
function renderOffersStore() {
  const menu = $("#offers-store-menu");
  if (!menu) return;
  const available = offersStores();
  if (!available.includes(offersState.store)) offersState.store = defaultStoreOf(available); // ontkoppeld
  offersFollowPreferred();
  menu.hidden = available.length < 2;
  $("#offers-store-name").textContent = STORES[offersState.store].name;
  $("#offers-store-list").innerHTML = available
    .map(
      (key) =>
        `<a class="subtab-menu__item${offersState.store === key ? " is-active" : ""}" data-store="${key}" href="${escapeHtml(offersPath(offersHistoryActive() ? "history" : "", { store: offersStoreParam(key) }))}" data-nav>${escapeHtml(STORES[key].name)}</a>`
    )
    .join("");
  if (typeof routerRetry === "function") routerRetry();
  // Wachtte de pagina op de koppeling van de voorkeurssupermarkt en is die nu bekend: alsnog laden (een adres dat erop wachtte,
  // deed dat hierboven al).
  if (offersState.waiting && preferredSupermarketKnown() && offersTabActive() && !(typeof router !== "undefined" && router.applying)) showOffers();
}

// `chosen`: de gebruiker koos de winkel zelf (menu of adres); anders is het de voorkeurssupermarkt (offersFollowPreferred).
function offersSwitchStore(store, { chosen = true } = {}) {
  offersState.chosen = chosen;
  offersState.store = offersStores().includes(store) ? store : "ah";
  offersState.filters = null; // per winkel onthouden: offersFilters laadt die van de nieuwe winkel
  offersState.category = "all";
  offersState.course = "all";
  showOffers();
  if (typeof offerHistoryStoreChanged === "function") offerHistoryStoreChanged();
  if (typeof routerSync === "function") routerSync();
}

// De voorwaarden van een aanbieding: vanaf hoeveel stuks, wat een stuk dan kost en tot wanneer ze geldt.
function offerConditionsText(offer) {
  const parts = [];
  // Moet de aanbieding nog beginnen, en dat pas na de maandag van de gekozen week (Jumbo wisselt op woensdag), dan staat erbij vanaf wanneer.
  const from = offer.validFrom ? new Date(`${String(offer.validFrom).slice(0, 10)}T12:00:00`) : null;
  const startsLater = offer.validity === "upcoming" && offersState.weekStart && String(offer.validFrom).slice(0, 10) > offersState.weekStart;
  if (from && !Number.isNaN(from.getTime()) && startsLater) {
    parts.push(t("offers.valid_from", { date: `${weekdayShort(from)} ${from.getDate()} ${monthShort(from)}` }));
  }
  if (offer.minQuantity > 1) parts.push(t("offers.min_quantity", { count: offer.minQuantity }));
  if (offer.unitPrice && offer.mechanic !== "fixed" && offer.unitPrice !== offer.price) {
    parts.push(t("offers.unit_price", { price: formatPrice(offer.unitPrice) }));
  }
  const until = offer.validUntil ? new Date(`${String(offer.validUntil).slice(0, 10)}T12:00:00`) : null;
  if (until && !Number.isNaN(until.getTime())) {
    parts.push(t("offers.valid_until", { date: until.toLocaleDateString(I18N.locale(), { day: "numeric", month: "short" }) }));
  }
  return parts.join(" \u00b7 ");
}

// De prijs per kilo, liter of stuk ("€ 5,00/kg"); `unit` is kg, l of stuk.
function offerPerUnitText(perUnit) {
  if (!perUnit || typeof perUnit.value !== "number") return "";
  const unit = { kg: "offers.unit.kg", l: "offers.unit.l" }[perUnit.unit] || "offers.unit.stuk";
  return t("offers.per_unit", { price: formatPrice(perUnit.value), unit: t(unit) });
}

// De chips onder de prijs (#178): komt de aanbieding vaak terug, en is een andere verpakking per eenheid goedkoper. Het oordeel zelf
// (echt voordelig, klein voordeel, niet goedkoper) zit in de badge op het plaatje. Zie prices.annotate.
// De laagste prijs van het afgelopen jaar (prices.yearLow): groen als de prijs van nu die is, anders hoeveel erboven en wanneer die
// laagste het laatst gold. Niets zonder een jaar aan prijzen.
function offerYearLowLine(offer) {
  const low = offer.yearLow;
  if (!low || !low.status || low.status === "unknown") return null;
  if (low.status === "lowest") {
    return { cls: "verdict verdict--year-low", text: t("offers.label.year_low"), title: t("offers.label.year_low_title", { month: offerMonthText(low.month, true) }) };
  }
  return {
    cls: "verdict verdict--year-above",
    text: t("offers.label.year_above", { amount: formatPrice(low.above), month: offerMonthText(low.month) }),
    title: t("offers.label.year_above_title", { price: formatPrice(low.price), month: offerMonthText(low.month, true) }),
  };
}

function offerVerdictLines(offer) {
  const verdict = offer.verdict;
  if (!verdict) return [];
  const lines = [];
  const yearLow = offerYearLowLine(offer);
  if (yearLow) lines.push(yearLow);
  if (verdict.often) {
    lines.push({ cls: "verdict verdict--often", text: t("offers.label.often", { count: verdict.timesThisYear }), title: t("offers.verdict.often", { count: verdict.timesThisYear }) });
  }
  const pack = verdict.cheaperPack;
  if (pack && pack.unitPrice) {
    lines.push({
      cls: "verdict verdict--pack",
      text: t("offers.verdict.cheaper_pack", { title: [pack.title, pack.unitSize].filter(Boolean).join(" "), price: offerPerUnitText(pack.unitPrice) }),
    });
  }
  return lines;
}

// De uitleg bij de badge: het oordeel, de laagste gewone prijs van 30 dagen waarmee vergeleken is, en de claim van de winkel als die
// hoger is. Kent de app geen gewone prijs, dan is het getal de claim van de winkel en zegt de uitleg dat. Een regel van een aankoop
// heeft geen oordeel, alleen { badge: { percent, source } }: dan het percentage met het oordeel dat bij dat getal hoort, zonder
// de vergelijkingsprijs.
function offerBadgeTitle(offer) {
  const badge = offer.badge;
  const verdict = offer.verdict || { kind: badge.percent >= 20 ? "good" : badge.percent >= 1 ? "small" : "none" };
  // Alleen de claim van de winkel, en die niet opgepompt: dan zegt de uitleg dat de app geen gewone prijs kent.
  if (badge.source === "store" && !badge.inflated) return t("offers.badge.title_store", { percent: badge.percent });
  const kind ={ good: "offers.verdict.good", small: "offers.verdict.small", none: "offers.verdict.none" }[verdict.kind];
  const parts = [kind ? t(kind, { percent: badge.percent }) : ""];
  if (typeof verdict.lowestRegular === "number") parts.push(t("offers.verdict.reference", { price: formatPrice(verdict.lowestRegular) }));
  if (badge.inflated && typeof offer.priceBefore === "number") {
    parts.push(t("offers.verdict.inflated", { before: formatPrice(offer.priceBefore), price: formatPrice(verdict.lowestRegular) }));
  }
  return parts.filter(Boolean).join(" ");
}

// De kortingsbadge op het plaatje: één getal in de kleurschaal van het userscript (50, 35, 20, 10), omlijnd als het de claim van de
// winkel is in plaats van een gemeten verschil, grijs bij 0%, niets als er geen oordeel is. `extraClass` "offer-badge--inline" zet
// hem in de tekst (een regel van een aankoop) in plaats van op een plaatje.
function offerBadge(offer, extraClass = "") {
  const badge = offer.badge;
  if (!badge || typeof badge.percent !== "number") return "";
  const step = badge.percent >= 50 ? "50" : badge.percent >= 35 ? "35" : badge.percent >= 20 ? "20" : badge.percent >= 10 ? "10" : badge.percent >= 1 ? "1" : "0";
  const cls = `offer-badge offer-badge--${step}${badge.source === "store" ? " offer-badge--store" : ""}${extraClass ? ` ${extraClass}` : ""}`;
  const text = badge.percent > 0 ? `-${badge.percent}%` : "0%";
  return `<span class="${cls}" title="${escapeHtml(offerBadgeTitle(offer))}">${escapeHtml(text)}</span>`;
}

// "mei" of "mei 2025": de maand uit "YYYY-MM", met het jaar erbij als het niet dit jaar is (of altijd, voor een tooltip).
function offerMonthText(month, withYear = false) {
  const date = new Date(`${month}-15T12:00:00`);
  if (Number.isNaN(date.getTime())) return month;
  const name = monthShort(date);
  return !withYear && date.getFullYear() === new Date().getFullYear() ? name : `${name} ${date.getFullYear()}`;
}

function offerDateText(day) {
  const date = new Date(`${String(day).slice(0, 10)}T12:00:00`);
  return Number.isNaN(date.getTime()) ? String(day) : `${date.getDate()} ${monthShort(date)} ${date.getFullYear()}`;
}

// Het prijslabel uit de geschiedenis (prices.price_label): groen voor de laagste prijs ooit, in 12 of in 6 maanden, grijs voor
// "laagste sinds we meten" en "was al eens lager".
function offerPriceLabel(offer) {
  const label = offer.priceLabel;
  if (!label || !label.kind) return null;
  const green = "offer-label offer-label--green";
  const grey = "offer-label offer-label--grey";
  if (label.kind === "lowest_ever") return { cls: green, text: t("offers.label.lowest_ever"), title: "" };
  if (label.kind === "lowest_12m") return { cls: green, text: t("offers.label.lowest_12m"), title: "" };
  if (label.kind === "lowest_6m") return { cls: green, text: t("offers.label.lowest_6m"), title: "" };
  if (label.kind === "lowest_seen") {
    return { cls: grey, text: t("offers.label.lowest_seen", { month: offerMonthText(label.month) }), title: t("offers.label.lowest_seen_title", { date: offerDateText(label.coveredSince) }) };
  }
  if (label.kind === "lower_before") {
    // Met een jaar aan prijzen zegt de regel van yearLow het preciezer dan "was al eens lager in 2010".
    if (offer.yearLow && offer.yearLow.status && offer.yearLow.status !== "unknown") return null;
    const params = { price: formatPrice(label.price), month: offerMonthText(label.month) };
    return { cls: grey, text: t("offers.label.lower_before", params), title: t("offers.label.lower_before_title", { ...params, month: offerMonthText(label.month, true) }) };
  }
  return null;
}

function offerPriceLabelHtml(offer) {
  const label = offerPriceLabel(offer);
  return label ? `<span class="${label.cls}"${label.title ? ` title="${escapeHtml(label.title)}"` : ""}>${escapeHtml(label.text)}</span>` : "";
}

// De rij onderaan de kaart: Vergelijk verpakkingen (een knop) en Prijsgeschiedenis (een link), in dezelfde stijl op één lijn.
function offerCardFoot(offer) {
  const compare = offer.unitPricePer
    ? `<button class="link-button offer-card__compare" type="button" data-compare="${escapeHtml(offer.id)}">${escapeHtml(t("offers.compare.open"))}</button>`
    : "<span></span>";
  const history = `<a class="link-button offer-card__history" href="${escapeHtml(priceHistoryPath(offersState.store, offer.title, offer.id))}" data-route>${escapeHtml(t("offers.card.history"))}</a>`;
  return `<div class="offer-card__foot">${compare}${history}</div>`;
}

// Het plaatje van een aanbieding met de badge en het prijslabel erop (of kaal, in de compacte rij). Zonder plaatje een leeg vlak
// van dezelfde hoogte. De link naar de winkel zit alleen om het plaatje (de titel is de link voor schermlezers), de badge en het
// label staan ernaast, zodat een schermlezer ze wel leest.
function offerThumb(offer, extraClass = "", bare = false) {
  const image = offer.image ? `<img src="${escapeHtml(storeImage(offer.image))}" alt="" loading="lazy" data-hide-on-error>` : mdiIcon(MDI_IMAGE_OUTLINE, "mdi offer-card__thumb-icon");
  const overlay = bare ? "" : `${offerBadge(offer)}${offerPriceLabelHtml(offer)}`;
  const link = offer.url
    ? `<a class="offer-card__thumb-link" href="${escapeHtml(offer.url)}" target="_blank" rel="noopener noreferrer" tabindex="-1" aria-hidden="true">${image}</a>`
    : `<span class="offer-card__thumb-link">${image}</span>`;
  return `<div class="offer-card__thumb${offer.image ? "" : " offer-card__thumb--empty"}${extraClass ? ` ${extraClass}` : ""}" data-zoom="${escapeHtml(offer.id)}">${link}${overlay}</div>`;
}

// De prijs op de kaart: wat een stuk met de actie kost, met daarnaast doorgestreept de laagste gewone prijs van 30 dagen als de app
// die kent, anders de "van"-prijs van de winkel.
function offerPriceHtml(offer) {
  const now = typeof offer.unitPrice === "number" && offer.unitPrice > 0 ? offer.unitPrice : offer.price;
  if (!now) return "";
  const verdict = offer.verdict || {};
  const before = typeof verdict.lowestRegular === "number" ? verdict.lowestRegular : offer.priceBefore;
  const was = before && before > now + 0.005 ? ` <s>${escapeHtml(formatPrice(before))}</s>` : "";
  return `<small class="product-card__price">${escapeHtml(formatPrice(now))}${was}</small>`;
}

function offerProductCard(offer) {
  const meta = [offer.brand, offer.unitSize].filter(Boolean).join(" · ");
  const name = `<strong>${escapeHtml(offer.title)}</strong>`;
  const title = offer.url ? `<a class="offer-card__link" href="${escapeHtml(offer.url)}" target="_blank" rel="noopener noreferrer">${name}</a>` : name;
  const perUnit = offerPerUnitText(offer.unitPricePer);
  const conditions = offerConditionsText(offer);
  const chips = offerVerdictLines(offer)
    .map((line) => `<small class="${line.cls}"${line.title ? ` title="${escapeHtml(line.title)}"` : ""}>${escapeHtml(line.text)}</small>`)
    .join("");
  return `
    <article class="product-card offer-card" data-offer="${escapeHtml(offer.id)}" tabindex="0">
      ${offerThumb(offer)}
      <div class="offer-card__body">
        ${title}
        ${meta ? `<small>${escapeHtml(meta)}</small>` : ""}
        ${offer.promoLabel ? `<small class="bonus">${escapeHtml(offer.promoLabel)}</small>` : ""}
        ${offerPersonalBadge(offer)}
        ${conditions ? `<small>${escapeHtml(conditions)}</small>` : ""}
        ${offerPriceHtml(offer)}
        ${perUnit ? `<small class="offer-card__per-unit">${escapeHtml(perUnit)}</small>` : ""}
        ${chips}
        ${offerPackRows(offer)}
      </div>
      ${offerCardFoot(offer)}
    </article>`;
}

// De compacte weergave: één regel per aanbieding, met de badge als eigen kolom, de titel met inhoud en actie, de prijs, de prijs per
// eenheid, het prijslabel (of de chip "n× dit jaar") en twee iconen voor Vergelijk verpakkingen en Prijsgeschiedenis.
function offerProductRow(offer) {
  const name = `<strong>${escapeHtml(offer.title)}</strong>`;
  const title = offer.url ? `<a class="offer-card__link" href="${escapeHtml(offer.url)}" target="_blank" rel="noopener noreferrer">${name}</a>` : name;
  const promo = offer.promoLabel ? `<b class="offer-row__promo">${escapeHtml(offer.promoLabel)}</b>` : "";
  const meta = [offer.unitSize ? escapeHtml(offer.unitSize) : "", promo].filter(Boolean).join(" · ");
  const perUnit = offerPerUnitText(offer.unitPricePer);
  const label = offerPriceLabel(offer);
  const often = offerVerdictLines(offer).find((line) => line.cls.includes("verdict--often"));
  const warning = offerPackWarning(offer);
  const history = warning
    ? `<small class="verdict verdict--pack">${escapeHtml(warning)}</small>`
    : label
    ? `<span class="${label.cls}"${label.title ? ` title="${escapeHtml(label.title)}"` : ""}>${escapeHtml(label.text)}</span>`
    : often ? `<small class="${often.cls}" title="${escapeHtml(often.title)}">${escapeHtml(often.text)}</small>` : "";
  const compare = offer.unitPricePer
    ? `<button class="icon-button" type="button" data-compare="${escapeHtml(offer.id)}" title="${escapeHtml(t("offers.compare.open"))}" aria-label="${escapeHtml(t("offers.compare.open"))}">${mdiIcon(MDI_COMPARE_HORIZONTAL)}</button>`
    : "";
  const chart = `<a class="icon-button" href="${escapeHtml(priceHistoryPath(offersState.store, offer.title, offer.id))}" data-route title="${escapeHtml(t("offers.card.history"))}" aria-label="${escapeHtml(t("offers.card.history"))}">${mdiIcon(MDI_CHART_LINE)}</a>`;
  return `
    <article class="product-card offer-row" data-offer="${escapeHtml(offer.id)}" tabindex="0">
      ${offerThumb(offer, "offer-row__thumb", true)}
      <span class="offer-row__badge">${offerBadge(offer)}</span>
      <div class="offer-row__product">${title}${meta ? `<small>${meta}</small>` : ""}${offerPersonalBadge(offer)}</div>
      <span class="offer-row__price">${offerPriceHtml(offer)}</span>
      <small class="offer-row__unit">${escapeHtml(perUnit)}</small>
      <span class="offer-row__history">${history}</span>
      <div class="offer-row__links">${compare}${chart}</div>
    </article>`;
}

function offerRowHead() {
  const cells = ["", t("offers.row.discount"), t("offers.row.product"), t("offers.row.price"), t("offers.row.unit"), t("offers.row.history"), ""];
  return `<div class="offer-row offer-row--head" aria-hidden="true">${cells.map((cell) => `<span>${escapeHtml(cell)}</span>`).join("")}</div>`;
}

// ---- De popup met de prijsgeschiedenis: met een muis bij het aanwijzen van een kaart (na een korte pauze, zoals de hovers bij de
// aankopen in popover.js), zonder muis opent een tik op het plaatje hetzelfde als venster. De inhoud is de prijsgeschiedenis
// van offers_history.js (kop, kerncijfers, grafiek met de periodeknoppen, de laatste aankopen) plus een link naar de volledige
// pagina. Het product komt per product van /api/prices/history?product=..., een keer, en blijft in het geheugen van de pagina.
// Bij een verpakkingsfamilie wissel je met knoppen van verpakking zonder de popup te sluiten.

const OFFER_POPUP_WIDTH = 640;
const OFFER_POPUP_MIN_SCREEN = 900; // smaller: geen popup bij hover, wel het venster bij een tik
const OFFER_POPUP_PURCHASES = 8;
const offerZoomCache = new Map(); // winkel|id -> belofte van het product uit /api/prices/history
const offerPopupProducts = new Map(); // winkel:id -> het geladen product, voor de periodeknoppen (offers_history.js)
const offerPopup = { timer: 0, hideTimer: 0, id: "", anchor: null };

function offerZoomFinePointer() {
  return window.matchMedia("(hover: hover) and (pointer: fine)").matches;
}

function offerPopupAllowed() {
  return offerZoomFinePointer() && window.innerWidth >= OFFER_POPUP_MIN_SCREEN;
}

async function loadOfferZoomHistory(store, id) {
  const key = `${store}|${id}`;
  if (!offerZoomCache.has(key)) {
    offerZoomCache.set(
      key,
      jsonFetch(`/api/prices/history?store=${encodeURIComponent(store)}&product=${encodeURIComponent(id)}`)
        .then((data) => (data.products && data.products[0]) || null)
        .then((product) => {
          if (product) offerPopupProducts.set(`${store}:${id}`, product);
          return product;
        })
        .catch(() => null) // de popup is een extraatje: zonder geschiedenis blijft de kop staan
    );
  }
  return offerZoomCache.get(key);
}

// Het geladen product van een popup, voor de periodeknoppen van de grafiek (offers_history.js zoekt hier als de klik in de popup valt).
function offerPopupProduct(key) {
  return offerPopupProducts.get(key) || null;
}

// De aanbieding als product voor de kop van de popup, zolang de geschiedenis nog laadt.
function offerAsProduct(store, offer) {
  return {
    store, productId: String(offer.id), title: offer.title || "", brand: offer.brand || "", unitSize: offer.unitSize || "", image: offer.image || "",
    url: offer.url || "", onOfferNow: true, episodes: [], purchases: [], observed: [], lineOffers: [], savedImages: [],
  };
}

// De inhoud van de popup en van het venster: de kop van de prijsgeschiedenis (compact), de verpakkingen als knoppen bij een familie,
// de kerncijfers, de grafiek en de laatste aankopen, en de link naar de volledige prijsgeschiedenis. Zonder product (nog aan het
// laden, of mislukt) alleen de kop en een laadregel.
function offerPopupHtml(offer, product, packs, activeId = null) {
  const store = offersState.store;
  const shown = product || offerAsProduct(store, offer);
  const buttons = packs.length > 1
    ? `<div class="offer-popup__packs" role="group" aria-label="${escapeHtml(t("offers.popup.packs"))}">${packs
        .map((pack) => `<button type="button" class="offer-chip offer-chip--small${String(pack.productId) === String(activeId) ? " is-active" : ""}" data-popup-pack="${escapeHtml(pack.productId)}" aria-pressed="${String(pack.productId) === String(activeId)}">${escapeHtml(pack.unitSize || pack.productId)}</button>`)
        .join("")}</div>`
    : "";
  const body = product
    ? `${offerHistoryStats(product)}${offerHistoryChartBlock(product, { width: OFFER_POPUP_WIDTH - 32 })}${offerHistoryPurchasesTable(product, { limit: OFFER_POPUP_PURCHASES })}`
    : `<p class="muted offer-popup__loading">${escapeHtml(t("offers.popup.loading"))}</p>`;
  return `<div class="offer-popup__inner">
      ${offerHistoryHead(shown, { compact: true })}
      ${buttons}
      ${body}
      <p class="offer-popup__foot"><a href="${escapeHtml(priceHistoryPath(store, shown.title, shown.productId))}" data-route>${escapeHtml(t("offers.popup.full_history"))}</a></p>
    </div>`;
}

function offerById(id) {
  return offersState.offers.find((offer) => String(offer.id) === String(id)) || null;
}

// De verpakkingen van de familie van een aanbieding, voor de knoppen in de popup; een product van een verpakking dat zelf geen
// aanbieding is (de 1000 g tegen gewone prijs) krijgt een kale aanbieding uit de familieregel.
function offerPopupPacks(offer) {
  return (offer.family && offer.family.packs) || [];
}

function offerForPack(offer, packId) {
  const found = offerById(packId);
  if (found) return found;
  const pack = offerPopupPacks(offer).find((item) => String(item.productId) === String(packId));
  return pack ? { ...offer, id: pack.productId, unitSize: pack.unitSize, price: pack.price, title: pack.title || offer.title } : offer;
}

function hideOfferPopup() {
  const box = $("#offer-popup");
  if (box.hidden && !offerPopup.timer) return; // bij elke scroll: niets te doen als de popup al dicht is
  clearTimeout(offerPopup.timer);
  clearTimeout(offerPopup.hideTimer);
  offerPopup.timer = 0;
  offerPopup.hideTimer = 0;
  offerPopup.id = "";
  offerPopup.anchor = null;
  box.hidden = true;
  box.innerHTML = "";
}

// Eerst op volle breedte meten (de hoogte hangt van de breedte af), dan de plek en de breedte zetten en bij een smallere popup
// de hoogte nog een keer meten voor de plek binnen de onderrand.
function placeOfferPopup(box, anchor) {
  const rect = anchor.getBoundingClientRect();
  const viewport = { width: window.innerWidth, height: window.innerHeight };
  box.style.width = "";
  let spot = offerPopupPlace(rect, { width: box.offsetWidth || OFFER_POPUP_WIDTH, height: box.offsetHeight }, viewport);
  if (spot.width < box.offsetWidth) {
    box.style.width = `${spot.width}px`;
    spot = offerPopupPlace(rect, { width: spot.width, height: box.offsetHeight }, viewport);
  }
  box.style.left = `${spot.left}px`;
  box.style.top = `${spot.top}px`;
}

// Toont de popup voor een aanbieding bij een kaart: eerst de kop (die staat al in de aanbieding), de rest zodra het product er is.
function showOfferPopup(anchor, offer, packId = null) {
  const box = $("#offer-popup");
  const shownId = String(packId || offer.id);
  const shown = offerForPack(offer, shownId);
  const packs = offerPopupPacks(offer);
  box.innerHTML = offerPopupHtml(shown, offerPopupProducts.get(`${offersState.store}:${shownId}`) || null, packs, shownId);
  box.hidden = false;
  offerPopup.id = shownId;
  offerPopup.anchor = anchor;
  box.dataset.offer = String(offer.id);
  placeOfferPopup(box, anchor);
  loadOfferZoomHistory(offersState.store, shownId).then((product) => {
    if (box.hidden || offerPopup.id !== shownId || offerPopup.anchor !== anchor) return;
    box.innerHTML = offerPopupHtml(shown, product, packs, shownId);
    placeOfferPopup(box, anchor);
  });
}

function scheduleOfferPopup(anchor, packId = null) {
  const offer = offerById(anchor.dataset.offer);
  if (!offer) return;
  clearTimeout(offerPopup.hideTimer);
  offerPopup.hideTimer = 0;
  const shownId = String(packId || offer.id);
  if (offerPopup.id === shownId && offerPopup.anchor === anchor) return;
  clearTimeout(offerPopup.timer);
  offerPopup.timer = setTimeout(() => {
    offerPopup.timer = 0;
    showOfferPopup(anchor, offer, packId);
  }, POPOVER_DELAY);
}

function delayHideOfferPopup() {
  clearTimeout(offerPopup.timer);
  offerPopup.timer = 0;
  clearTimeout(offerPopup.hideTimer);
  offerPopup.hideTimer = setTimeout(hideOfferPopup, POPOVER_GRACE);
}

// Zonder muis: dezelfde inhoud als venster.
function openOfferZoomDialog(offer, packId = null) {
  const dialog = $("#offer-zoom-dialog");
  const body = $("#offer-zoom-dialog-body");
  const shownId = String(packId || offer.id);
  const shown = offerForPack(offer, shownId);
  const packs = offerPopupPacks(offer);
  body.dataset.offer = String(offer.id);
  body.innerHTML = offerPopupHtml(shown, offerPopupProducts.get(`${offersState.store}:${shownId}`) || null, packs, shownId);
  if (!dialog.open) dialog.showModal();
  loadOfferZoomHistory(offersState.store, shownId).then((product) => {
    if (!dialog.open || body.dataset.offer !== String(offer.id)) return;
    body.innerHTML = offerPopupHtml(shown, product, packs, shownId);
  });
}

// Hoeveel producten er in een rij van het raster passen; een verborgen raster geeft de ruwe waarde, dan gokken we.
function offerColumns() {
  const columns = getComputedStyle($("#offers-products")).gridTemplateColumns;
  return columns.includes("(") ? 3 : Math.max(1, columns.split(" ").filter(Boolean).length);
}

// Alias: de chips en het label wonen sinds 3.8 in categories.js.
const offerCategoryLabel = categoryLabel;

function renderOfferCategories() {
  const total = offersFiltered().length;
  const categories = offersShownCategories();
  if (offersState.category !== "all" && !categories.some((category) => category.id === offersState.category)) offersState.category = "all";
  $("#offers-categories").hidden = !total;
  $("#offers-categories").innerHTML = categoryChips({
    categories,
    active: offersState.category,
    total,
    allLabel: t("offers.all", { count: I18N.formatNumber(total) }),
  });
}

function renderOfferGrid() {
  const base = offersFiltered();
  const shown = offersSorted(offersState.category === "all" ? base : base.filter((offer) => offer.category === offersState.category));
  const compact = offersState.view === "compact";
  const grid = $("#offers-products");
  grid.classList.toggle("product-grid--compact", compact); // eerst de klasse, dan pas de kolommen meten
  const limit = offersState.rows * (compact ? OFFER_COMPACT_ROWS : offerColumns());
  // In de kaartenweergave een kaart per verpakkingsfamilie (offerFamilies), in de compacte weergave een regel per verpakking.
  const units = compact ? shown : offerFamilies(shown).map((group) => group.head);
  const cards = units.slice(0, limit).map(compact ? offerProductRow : offerProductCard).join("");
  grid.innerHTML = compact && units.length ? offerRowHead() + cards : cards;
  $("#offers-more").hidden = units.length <= limit;
}

// "Geldig ma 26 okt t/m zo 1 nov": de looptijd die de meeste aanbiedingen delen, of niets als de winkel er geen geeft.
function offersPeriodText(period) {
  if (!period) return "";
  const from = new Date(`${String(period.from).slice(0, 10)}T12:00:00`);
  const until = new Date(`${String(period.until).slice(0, 10)}T12:00:00`);
  if (Number.isNaN(from.getTime()) || Number.isNaN(until.getTime())) return "";
  const day = (date) => `${weekdayShort(date)} ${date.getDate()} ${monthShort(date)}`;
  return t("offers.period", { from: day(from), until: day(until) });
}

// De schakelaar "Alleen Bonus Box", alleen bij een winkel met persoonlijke aanbiedingen (AH). Ernaast: hoeveel je er hebt geactiveerd,
// of waarom er geen zijn (geen koppeling, of het ophalen mislukte; dan staan die van de vorige keer er nog).
function renderOffersPersonal() {
  const info = offersState.personal;
  const show = Boolean(offersState.supported && info && info.supported);
  $("#offers-personal").hidden = !show;
  if (!show) return;
  const input = $("#offers-personal-only");
  input.checked = offersState.personalOnly;
  input.disabled = !info.linked && !info.count;
  $("#offers-personal-label").textContent = info.count ? t("offers.personal.switch_count", { count: I18N.formatNumber(info.count) }) : t("offers.personal.switch");
  const personal = offersState.offers.filter((offer) => offer.personal);
  const known = personal.filter((offer) => offer.activated === true || offer.activated === false);
  let note = "";
  if (!info.linked) note = t("offers.personal.unlinked");
  else if (info.error) note = t("offers.personal.error", { error: info.error });
  else if (known.length) note = t("offers.personal.activated", { count: I18N.formatNumber(known.filter((offer) => offer.activated).length), total: I18N.formatNumber(known.length) });
  else if (!personal.length && offersPersonalOnly()) note = t("offers.personal.none");
  $("#offers-personal-note").textContent = note;
  $("#offers-personal-note").hidden = !note;
}

function offersTogglePersonal(on) {
  offersState.personalOnly = on;
  offersState.rows = OFFER_ROWS;
  try {
    localStorage.setItem(OFFERS_PERSONAL_KEY, on ? "1" : "0");
  } catch {
    // De keuze geldt dan alleen tot je de pagina herlaadt.
  }
  renderOffersPersonal();
  renderOfferCategories();
  renderOfferGrid();
}

// De zijbalk: de merken met hun aantal, de schakelaars en de grenzen zoals ze staan, de teller op de knop en de actieve chips.
function renderOffersFilters() {
  const filters = offersFilters();
  const brands = offersBrandCounts(offersState.offers.filter((offer) => (!offersPersonalOnly() || offer.personal) && (!offersState.goodOnly || (offer.verdict && offer.verdict.kind === "good"))));
  const shown = offersState.brandsOpen ? brands : brands.slice(0, OFFERS_BRAND_CHIPS);
  // Een gekozen merk dat deze week niet in de lijst staat, blijft zichtbaar als chip, met 0.
  filters.brands.forEach((brand) => {
    if (!shown.some((item) => item.brand === brand)) shown.push({ brand, count: brands.find((item) => item.brand === brand)?.count || 0 });
  });
  $("#offers-filter-brands").innerHTML = shown
    .map((item) => `<button class="offer-chip offer-chip--small${filters.brands.includes(item.brand) ? " is-active" : ""}" type="button" data-brand="${escapeHtml(item.brand)}" aria-pressed="${filters.brands.includes(item.brand)}">${escapeHtml(item.brand)} <small>${I18N.formatNumber(item.count)}</small></button>`)
    .join("");
  const more = $("#offers-filter-brands-more");
  more.hidden = brands.length <= OFFERS_BRAND_CHIPS;
  more.textContent = offersState.brandsOpen ? t("offers.filters.fewer_brands") : t("offers.filters.more_brands", { count: I18N.formatNumber(brands.length - OFFERS_BRAND_CHIPS) });
  $("#offers-filter-bought").checked = filters.bought;
  $("#offers-filter-year-low").checked = filters.yearLow;
  $("#offers-filter-rare").checked = filters.rare;
  $("#offers-filter-discount").value = String(filters.minDiscount);
  $("#offers-filter-discount-out").textContent = filters.minDiscount > 0 ? t("offers.filters.min_discount_value", { percent: I18N.formatNumber(filters.minDiscount) }) : t("offers.filters.min_discount_any");
  const price = $("#offers-filter-unit-price");
  if (document.activeElement !== price) price.value = typeof filters.maxUnitPrice.value === "number" ? String(filters.maxUnitPrice.value) : "";
  const unit = $("#offers-filter-unit");
  unit.innerHTML = ["kg", "l", "stuk"].map((name) => `<option value="${name}"${name === filters.maxUnitPrice.unit ? " selected" : ""}>${escapeHtml(t("offers.filters.per_unit", { unit: offersUnitLabel(name) }))}</option>`).join("");
  const count = offersActiveFilterCount();
  $("#offers-filters-count").textContent = count ? ` (${I18N.formatNumber(count)})` : "";
  const active = $("#offers-active-filters");
  active.hidden = !count;
  active.innerHTML = count
    ? offersActiveFilterChips()
        .map((chip) => `<button class="offer-chip offer-chip--small is-active" type="button" data-remove="${escapeHtml(chip.key)}" aria-label="${escapeHtml(t("offers.filters.remove", { filter: chip.label }))}">${escapeHtml(chip.label)} <span aria-hidden="true">&times;</span></button>`)
        .join("") + `<button class="link-button" type="button" data-remove="all">${escapeHtml(t("offers.filters.clear"))}</button>`
    : "";
}

// De werkbalk: het zoekveld, de schakelaar "alleen echt voordelig" en de sorteerchips, zoals de keuzes nu staan.
function renderOffersTools() {
  $("#offers-search").value = offersState.search;
  $("#offers-good-only").checked = offersState.goodOnly;
  renderOffersFilters();
  $("#offers-sort").querySelectorAll("[data-sort]").forEach((chip) => {
    const active = chip.dataset.sort === offersState.sort;
    chip.classList.toggle("is-active", active);
    chip.setAttribute("aria-pressed", String(active));
  });
  $("#offers-view").querySelectorAll("[data-view]").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.view === offersState.view));
  });
}

// Na een keuze in de werkbalk: terug naar de eerste rijen en de categorieën en het raster opnieuw tekenen.
function offersRedraw() {
  hideOfferPopup();
  offersState.rows = OFFER_ROWS;
  renderOffersTools();
  renderOffersStatusGood();
  renderOfferCategories();
  renderOfferGrid();
}

// Achter de status ("631 aanbiedingen, opgehaald op ..."): hoeveel daarvan echt voordelig zijn, als die schakelaar aan staat.
function renderOffersStatusGood() {
  const status = $("#offers-status");
  const base = status.dataset.base || "";
  if (!base) return;
  // Over dezelfde aanbiedingen als het totaal ervoor: niet binnen het zoekveld of de Bonus Box, die telt het totaal ook niet.
  const good = offersState.goodOnly ? offersState.offers.filter((offer) => offer.verdict && offer.verdict.kind === "good").length : 0;
  const parts = [base];
  if (offersState.goodOnly) parts.push(t("offers.status.good", { count: I18N.formatNumber(good) }));
  // Met een filter in de zijbalk: hoeveel er door de filters komen, van het totaal. Net als het aantal echt voordelig: over alle
  // aanbiedingen van de week, niet binnen het zoekveld.
  if (offersActiveFilterCount()) {
    const filters = offersFilters();
    const through = offersState.offers.filter((offer) => offersFilterMatch(offer, filters)).length;
    parts.push(t("offers.status.filtered", { shown: I18N.formatNumber(through), total: I18N.formatNumber(offersState.offers.length) }));
  }
  status.textContent = parts.join(" ");
}

function renderOffers(data) {
  offersState.supported = data.supported;
  offersState.offers = data.offers || [];
  offersState.categories = data.categories || [];
  offersState.personal = data.personal || null;
  if (offersState.category !== "all" && !offersState.categories.some((category) => category.id === offersState.category)) offersState.category = "all";
  offersState.rows = OFFER_ROWS;
  const status = $("#offers-status");
  if (!data.supported) {
    status.textContent = t("offers.unsupported", { store: STORES[offersState.store].name });
    offersState.offers = [];
    offersState.categories = [];
  } else {
    status.textContent = data.syncedAt ? tn("offers.status", data.total, { when: formatSyncTime(data.syncedAt) }) : t("offers.never");
  }
  const periodText = data.supported ? offersPeriodText(data.period) : "";
  $("#offers-period").textContent = periodText;
  $("#offers-period").hidden = !periodText;
  renderOffersWeekNotes(data);
  status.dataset.base = data.supported ? status.textContent : "";
  renderOffersStatusGood();
  renderOffersPersonal();
  renderOffersTools();
  renderOfferCategories();
  renderOfferGrid();
}

// Onder het aantal: wat er buiten de gekozen week valt. Aanbiedingen die pas later beginnen (de bonus van volgende week, die AH in het
// weekend al doorgeeft) met een knop naar de week erna; voor een andere week dan deze de aanbiedingen zonder looptijd, die de winkel
// alleen voor vandaag doorgeeft; en een lege week die nog niet bekend is.
function renderOffersWeekNotes(data) {
  const later = data.supported && data.syncedAt ? data.later || 0 : 0;
  $("#offers-later").hidden = !later;
  $("#offers-later-text").textContent = later ? tn("offers.later", later) : "";
  if (!data.supported || !data.syncedAt || !data.week || data.week.current) return;
  const notes = [];
  if (!data.total) notes.push(t("offers.week.empty"));
  if (data.undated) notes.push(tn("offers.undated", data.undated, { store: STORES[offersState.store].name }));
  if (notes.length) $("#offers-status").textContent += ` ${notes.join(" ")}`;
}

function offersChooseCategory(category) {
  offersState.category = category;
  offersState.rows = OFFER_ROWS;
  renderOfferCategories();
  renderOfferGrid();
}

function offersShowMore() {
  offersState.rows += OFFER_ROWS;
  renderOfferGrid();
}

// De lijst van de vorige winkel maakt plaats voor een draaiend icoon zolang de aanbiedingen van de gekozen winkel binnenkomen.
function renderOffersLoading(storeName = STORES[offersState.store].name) {
  $("#offers-status").textContent = "";
  $("#offers-period").hidden = true;
  $("#offers-later").hidden = true;
  $("#offers-personal").hidden = true;
  $("#offers-categories").hidden = true;
  $("#offers-products").innerHTML = "";
  $("#offers-products").setAttribute("aria-busy", "true");
  $("#offers-more").hidden = true;
  const box = $("#offers-loading");
  box.innerHTML = `${mdiIcon(MDI_LOADING, "offers-loading__icon")}<span>${escapeHtml(t("offers.loading", { store: storeName }))}</span>`;
  box.hidden = false;
}

function endOffersLoading() {
  $("#offers-loading").hidden = true;
  $("#offers-products").removeAttribute("aria-busy");
}

// Wisselt iemand tijdens het laden van winkel, dan telt alleen de laatste aanvraag; een traag antwoord van de vorige winkel wordt genegeerd.
let offersLoadId = 0;
// De laatst geladen lijst per winkel en week. Wissel je terug naar een winkel of week die je al zag, dan staat die lijst er meteen en controleren
// we op de achtergrond of er iets veranderd is; alleen bij een andere lijst wordt het scherm vernieuwd. De browser doet hetzelfde met een ETag (304).
const offersCache = {};

// De aanvraag die nog loopt, per winkel en week. Opent de pagina, dan vragen het openen van het scherm en het openen van het subtab
// allebei de lijst op; de tweede wacht dan op het antwoord van de eerste in plaats van hem nog eens op te vragen.
let offersFetch = null;

function offersRequest(key, url) {
  if (offersFetch && offersFetch.key === key) return offersFetch.promise;
  const promise = jsonFetch(url).finally(() => {
    if (offersFetch && offersFetch.promise === promise) offersFetch = null;
  });
  offersFetch = { key, promise };
  return promise;
}

async function showOffers() {
  const loadId = ++offersLoadId;
  // In de prijsgeschiedenis alleen de winkelkeuze: de aanbiedingen van de week horen daar niet, en een winkel zonder
  // koppeling heeft er geen (/api/offers geeft dan een fout).
  if (offersHistoryActive()) {
    offersState.waiting = false;
    renderOffersStore();
    return;
  }
  // Is nog niet bekend of de voorkeurssupermarkt gekoppeld is, dan wachten we daarop (renderOffersStore laadt dan alsnog).
  offersState.waiting = !offersState.chosen && !preferredSupermarketKnown();
  renderOffersStore();
  if (loadId !== offersLoadId) return; // renderOffersStore wisselde naar de voorkeurssupermarkt en laadde die al
  renderOffersWeek();
  message("#offers-message", "");
  if (offersState.waiting) {
    renderOffersLoading(STORES[preferredSupermarket()].name);
    return;
  }
  // En op de eerste dag van de week (offersWeekStartKnown laadt dan alsnog), anders vraagt de pagina eerst de verkeerde week op.
  offersState.waitingWeek = !state.weekStartKnown;
  if (offersState.waitingWeek) {
    renderOffersLoading();
    if (!offersState.weekStartTimer) offersState.weekStartTimer = setTimeout(weekStartSettled, OFFERS_WEEK_START_WAIT);
    return;
  }
  const store = offersState.store;
  const week = offersWeek().start;
  const key = `${store}|${week}`;
  offersState.weekStart = week;
  const cached = offersCache[key];
  if (cached) renderOffers(cached);
  else renderOffersLoading();
  try {
    const data = await offersRequest(key, `/api/offers?store=${encodeURIComponent(store)}&week=${encodeURIComponent(week)}`);
    if (loadId !== offersLoadId) return;
    endOffersLoading();
    if (!cached || JSON.stringify(cached) !== JSON.stringify(data)) renderOffers(data);
    offersCache[key] = data;
  } catch (error) {
    if (loadId !== offersLoadId) return;
    endOffersLoading();
    message("#offers-message", error.message);
  }
  showOfferRecipes();
}

// Het vinkje bij een koppeling die een AI heeft gevonden (zoeken) of goedgekeurd (beoordelen).
function offerAiBadge(item) {
  const label = item.ai === "search" ? t("aisearch.badge") : item.ai === "ok" ? t("aireview.badge") : "";
  return label ? `<span class="offer-ai" title="${escapeHtml(label)}" role="img" aria-label="${escapeHtml(label)}">${mdiIcon(MDI_CHECK_CIRCLE_OUTLINE)}</span>` : "";
}

function offerRecipeCard(recipe, index) {
  const matched = recipe.matched
    .map(
      (item, at) =>
        `<li>${escapeHtml(item.ingredient)} <button class="offer-match" type="button" data-recipe="${index}" data-match="${at}" aria-label="${escapeHtml(t("offers.product.open", { product: item.offer.title }))}">→ ${escapeHtml(item.offer.title)}</button>${offerAiBadge(item)}<button class="offer-flag" type="button" data-recipe="${index}" data-match="${at}" title="${escapeHtml(t("offers.report.label"))}" aria-label="${escapeHtml(t("offers.report.aria", { product: item.offer.title }))}">${mdiIcon(MDI_FLAG_OUTLINE)}</button></li>`
    )
    .join("");
  const saving = recipe.saving ? `<p class="bonus">${escapeHtml(t("offers.saving", { amount: formatPrice(recipe.saving) }))}</p>` : "";
  // Staat hetzelfde recept ook in een andere bron, dan is het één kaart en staat daar de andere bron bij.
  const alsoIn = recipe.alsoIn && recipe.alsoIn.length
    ? ` \u00b7 ${t("offers.also_in", { sources: recipe.alsoIn.map((source) => t(`offers.source.${source}`)).join(", ") })}`
    : "";
  const missing = recipe.missing && recipe.missing.length ? `<p class="muted">${escapeHtml(t("offers.recipe.missing", { list: recipe.missing.join(", ") }))}</p>` : "";
  return `
    <article class="recipe-card">
      ${recipe.image ? `<img src="${escapeHtml(storeImage(recipe.image))}" alt="${escapeHtml(recipe.title)}" loading="lazy" data-hide-on-error>` : ""}
      <h3>${escapeHtml(recipe.title)}</h3>
      <p class="muted">${escapeHtml(t("offers.match", { matched: recipe.matchedCount, total: recipe.ingredientCount }))}</p>
      <p class="muted">${escapeHtml(t("offers.recipe_from", { source: t(`offers.source.${recipe.source}`) }))}${alsoIn}</p>
      <ul class="offer-matches">${matched}</ul>
      ${missing}
      ${saving}
      ${recipe.url ? `<div class="recipe-card__actions"><a class="button button--secondary" href="${escapeHtml(recipe.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(t("rsearch.open"))}</a></div>` : ""}
    </article>`;
}

// Het product dat aan een ingrediënt is gekoppeld, in een venster: zo zie je meteen of de koppeling klopt.
let offerDialogMatch = null;

function openOfferProduct(recipe, item) {
  const offer = item.offer;
  offerDialogMatch = item;
  $("#offer-product-title").textContent = offer.title;
  $("#offer-product-meta").textContent = [offer.brand, offer.unitSize].filter(Boolean).join(" \u00b7 ");
  $("#offer-product-label").textContent = [offer.personal ? t("offers.personal.badge") : "", offer.promoLabel].filter(Boolean).join(" \u00b7 ");
  const was = offer.priceBefore ? ` <s>${escapeHtml(formatPrice(offer.priceBefore))}</s>` : "";
  $("#offer-product-price").innerHTML = offer.price ? `<strong>${escapeHtml(formatPrice(offer.price))}</strong>${was}` : "";
  $("#offer-product-conditions").textContent = offerConditionsText(offer);
  const image = $("#offer-product-image");
  image.hidden = !offer.image;
  if (offer.image) image.src = storeImage(offer.image);
  else image.removeAttribute("src");
  $("#offer-product-match").textContent = t("offers.product.match", { ingredient: item.ingredient, recipe: recipe.title });
  const open = $("#offer-product-open");
  open.hidden = !offer.url;
  open.textContent = t("offers.product.store", { store: STORES[offersState.store].name });
  open.dataset.url = offer.url || "";
  $("#offer-product-nopage").hidden = Boolean(offer.url);
  $("#offer-product-dialog").showModal();
}

function renderOfferCourses(courses) {
  const total = courses.reduce((sum, course) => sum + course.count, 0);
  const chips = [{ id: "all", label: t("offers.all", { count: I18N.formatNumber(total) }) }].concat(
    courses.map((course) => ({
      id: course.id,
      label: t("offers.chip", { count: I18N.formatNumber(course.count), category: t(`offers.course.${course.id}`) }),
    }))
  );
  $("#offers-courses").hidden = !total;
  $("#offers-courses").innerHTML = chips
    .map(
      (chip) =>
        `<button class="offer-chip${offersState.course === chip.id ? " is-active" : ""}" type="button" data-course="${escapeHtml(chip.id)}" aria-pressed="${offersState.course === chip.id}">${escapeHtml(chip.label)}</button>`
    )
    .join("");
}

function offersChooseCourse(course) {
  offersState.course = course;
  showOfferRecipes();
}

function offerIndexSummary(sources) {
  return Object.entries(sources || {})
    .map(([name, info]) => {
      const label = t(`offers.source.${name}`);
      if (info.error) return `${label}: ${info.error}`;
      if (info.skipped) return `${label}: ${info.skipped}`;
      return `${label}: ${I18N.formatNumber(info.count)}`;
    })
    .join(" · ");
}

async function showOfferRecipes() {
  // Het subtabblad Recepten staat standaard verborgen (MEALIESHOPPER_OFFERS_RECIPES); dan is er niets te laden.
  if (document.querySelector('.subtab[data-subtab="offers-suggestions"]').hidden) return;
  const status = $("#offers-recipes-status");
  const list = $("#offers-recipes");
  message("#offers-recipes-message", "");
  $("#offers-recipes-heading").textContent = t("offers.recipes.heading", { store: STORES[offersState.store].name });
  if (!offersState.supported) {
    status.textContent = "";
    list.innerHTML = "";
    $("#offers-courses").hidden = true;
    return;
  }
  try {
    const course = offersState.course === "all" ? "" : `&course=${encodeURIComponent(offersState.course)}`;
    const week = `&week=${encodeURIComponent(offersWeek().start)}`;
    const data = await jsonFetch(`/api/offers/recipes?store=${encodeURIComponent(offersState.store)}${course}${week}`);
    // Staat de gekozen soort er na het opnieuw inlezen niet meer bij, dan tonen we weer alles.
    if (offersState.course !== "all" && !(data.courses || []).some((item) => item.id === offersState.course)) {
      offersState.course = "all";
      return showOfferRecipes();
    }
    renderOfferCourses(data.courses || []);
    if (!data.indexedAt) {
      status.textContent = t("offers.recipes.never");
    } else if (!data.offersSyncedAt) {
      status.textContent = t("offers.recipes.no_offers");
    } else {
      status.textContent =
        (data.recipes.length ? tn("offers.recipes.count", data.total) : t("offers.recipes.none")) +
        ` · ${t("offers.recipes.indexed", { when: formatSyncTime(data.indexedAt) })} (${offerIndexSummary(data.sources)})`;
    }
    offersState.recipes = data.recipes;
    list.innerHTML = data.recipes.map(offerRecipeCard).join("");
    if (typeof renderOfferAi === "function") renderOfferAi(data.ai);
  } catch (error) {
    message("#offers-recipes-message", error.message);
  }
}

// Een koppeling als onjuist melden: ze komt niet meer terug, ook niet bij andere recepten met hetzelfde ingrediënt. Ongedaan maken kan direct.
async function reportOfferMismatch(item, undo = false) {
  const message_ = $("#offers-recipes-message");
  try {
    await jsonFetch("/api/offers/rejections", {
      method: undo ? "DELETE" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ store: offersState.store, ingredient: item.ingredient, offerId: item.offer.id }),
    });
    await showOfferRecipes();
    if (undo) return;
    message_.innerHTML = `<div class="message success message--action" role="status"><span>${escapeHtml(
      t("offers.report.done", { product: item.offer.title, ingredient: item.ingredient })
    )}</span><button class="inline-button" type="button" id="offers-report-undo">${escapeHtml(t("offers.report.undo"))}</button></div>`;
    $("#offers-report-undo").addEventListener("click", () => reportOfferMismatch(item, true));
  } catch (error) {
    message("#offers-recipes-message", error.message);
  }
}

async function offersIndexRecipes() {
  const button = $("#offers-index");
  message("#offers-recipes-message", "");
  setBusy(button, true, t("offers.recipes.indexing"));
  try {
    await jsonFetch("/api/offers/recipes/sync", { method: "POST" });
    await showOfferRecipes();
  } catch (error) {
    message("#offers-recipes-message", error.message);
  } finally {
    setBusy(button, false, t("offers.recipes.indexing"));
  }
}

$("#offers-more").addEventListener("click", offersShowMore);
// Vergelijk verpakkingen: het venster staat in offers_history.js (openPackCompare).
$("#offers-products").addEventListener("click", (event) => {
  const button = event.target.closest("[data-compare]");
  if (button) openPackCompare(offersState.store, button.dataset.compare);
});
$("#offers-recipes").addEventListener("click", (event) => {
  const button = event.target.closest(".offer-match, .offer-flag");
  if (!button) return;
  const recipe = offersState.recipes[Number(button.dataset.recipe)];
  const item = recipe && recipe.matched[Number(button.dataset.match)];
  if (!item) return;
  if (button.classList.contains("offer-flag")) reportOfferMismatch(item);
  else openOfferProduct(recipe, item);
});
$("#offer-product-report").addEventListener("click", () => {
  const item = offerDialogMatch;
  $("#offer-product-dialog").close();
  if (item) reportOfferMismatch(item);
});
$("#offer-product-open").addEventListener("click", (event) => {
  if (event.currentTarget.dataset.url) openProductPopup(event.currentTarget.dataset.url);
});
$("#offer-product-close").addEventListener("click", () => $("#offer-product-dialog").close());
// Een plaatje dat niet laadt verbergen we, en halen we niet weg (zoals data-hide-on-error doet): het venster gebruikt hetzelfde plaatje opnieuw.
$("#offer-product-image").addEventListener("error", (event) => {
  event.target.hidden = true;
});
// Een klik naast het venster (op de achtergrond) sluit het ook.
$("#offer-product-dialog").addEventListener("click", (event) => {
  if (event.target === event.currentTarget) event.currentTarget.close();
});
$("#offers-courses").addEventListener("click", (event) => {
  const chip = event.target.closest("[data-course]");
  if (chip) offersChooseCourse(chip.dataset.course);
});
$("#offers-categories").addEventListener("click", (event) => {
  const chip = event.target.closest("[data-cat]");
  if (chip) offersChooseCategory(chip.dataset.cat);
});
$("#offers-personal-only").addEventListener("change", (event) => offersTogglePersonal(event.target.checked));
$("#offers-search").addEventListener("input", (event) => {
  offersState.search = event.target.value;
  offersRedraw();
});
$("#offers-good-only").addEventListener("change", (event) => {
  offersState.goodOnly = event.target.checked;
  offersRemember(OFFERS_GOOD_KEY, offersState.goodOnly ? "1" : "0");
  offersRedraw();
});
// De zijbalk met filters: elke wijziging bewaart de filters voor deze winkel en tekent de lijst opnieuw.
function offersFilterChanged() {
  offersSaveFilters();
  offersRedraw();
}
$("#offers-filter-brands").addEventListener("click", (event) => {
  const chip = event.target.closest("[data-brand]");
  if (!chip) return;
  const filters = offersFilters();
  const brand = chip.dataset.brand;
  filters.brands = filters.brands.includes(brand) ? filters.brands.filter((item) => item !== brand) : [...filters.brands, brand];
  offersFilterChanged();
});
$("#offers-filter-brands-more").addEventListener("click", () => {
  offersState.brandsOpen = !offersState.brandsOpen;
  renderOffersFilters();
});
for (const [id, key] of [["#offers-filter-bought", "bought"], ["#offers-filter-year-low", "yearLow"], ["#offers-filter-rare", "rare"]]) {
  $(id).addEventListener("change", (event) => {
    offersFilters()[key] = event.target.checked;
    offersFilterChanged();
  });
}
$("#offers-filter-discount").addEventListener("input", (event) => {
  offersFilters().minDiscount = Math.max(0, Math.min(75, Number(event.target.value) || 0));
  offersFilterChanged();
});
$("#offers-filter-unit-price").addEventListener("input", (event) => {
  const value = Number(String(event.target.value).replace(",", "."));
  offersFilters().maxUnitPrice.value = event.target.value.trim() && Number.isFinite(value) && value > 0 ? value : null;
  offersFilterChanged();
});
$("#offers-filter-unit").addEventListener("change", (event) => {
  offersFilters().maxUnitPrice.unit = event.target.value;
  offersFilterChanged();
});
$("#offers-active-filters").addEventListener("click", (event) => {
  const button = event.target.closest("[data-remove]");
  if (!button) return;
  const key = button.dataset.remove;
  const filters = offersFilters();
  if (key === "all") offersState.filters = offersEmptyFilters();
  else if (key.startsWith("brand:")) filters.brands = filters.brands.filter((brand) => `brand:${brand}` !== key);
  else if (key === "minDiscount") filters.minDiscount = 0;
  else if (key === "maxUnitPrice") filters.maxUnitPrice.value = null;
  else filters[key] = false;
  offersFilterChanged();
});
$("#offers-filters-clear").addEventListener("click", () => {
  offersState.filters = offersEmptyFilters();
  offersFilterChanged();
});
// Op een smal scherm is de zijbalk een uitschuifpaneel: de knop Filters opent hem, de achtergrond, het kruisje en Escape sluiten hem.
function offersFiltersOpen(open) {
  $("#offers-filters").classList.toggle("is-open", open);
  $("#offers-filters-backdrop").hidden = !open;
  $("#offers-filters-toggle").setAttribute("aria-expanded", String(open));
  document.body.classList.toggle("offers-filters-open", open);
}
$("#offers-filters-toggle").addEventListener("click", () => offersFiltersOpen(!$("#offers-filters").classList.contains("is-open")));
$("#offers-filters-close").addEventListener("click", () => offersFiltersOpen(false));
$("#offers-filters-backdrop").addEventListener("click", () => offersFiltersOpen(false));
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && $("#offers-filters").classList.contains("is-open")) offersFiltersOpen(false);
});
$("#offers-sort").addEventListener("click", (event) => {
  const chip = event.target.closest("[data-sort]");
  if (!chip || !OFFERS_SORTS.includes(chip.dataset.sort)) return;
  offersState.sort = chip.dataset.sort;
  offersRemember(OFFERS_SORT_KEY, offersState.sort);
  offersRedraw();
});
// De popup: met een muis bij het aanwijzen van een kaart of een regel (een verpakkingsregel toont die verpakking), met het
// toetsenbord bij focus, en zonder muis opent een tik op het plaatje hetzelfde als venster.
function offerPopupAnchor(target) {
  const anchor = target.closest("[data-offer]"); // een kaart of een regel, niet de kopregel van de compacte lijst
  if (!anchor) return null;
  const pack = target.closest("[data-zoom]");
  return { anchor, packId: pack && pack.classList.contains("offer-pack") ? pack.dataset.zoom : null };
}
$("#offers-products").addEventListener("mouseover", (event) => {
  if (!offerPopupAllowed()) return;
  const found = offerPopupAnchor(event.target);
  if (found) scheduleOfferPopup(found.anchor, found.packId);
});
$("#offers-products").addEventListener("mouseout", (event) => {
  const found = offerPopupAnchor(event.target);
  if (!found) return;
  const to = event.relatedTarget;
  if (to && (found.anchor.contains(to) || $("#offer-popup").contains(to))) return;
  delayHideOfferPopup();
});
$("#offers-products").addEventListener("focusin", (event) => {
  if (!offerPopupAllowed()) return;
  const found = offerPopupAnchor(event.target);
  if (found) scheduleOfferPopup(found.anchor, found.packId);
});
$("#offers-products").addEventListener("click", (event) => {
  const thumb = event.target.closest("[data-zoom]");
  if (!thumb || offerZoomFinePointer()) return;
  const found = offerPopupAnchor(event.target);
  const offer = found ? offerById(found.anchor.dataset.offer) : null;
  if (!offer) return;
  event.preventDefault();
  openOfferZoomDialog(offer, found.packId);
});
$("#offer-popup").addEventListener("mouseenter", () => {
  clearTimeout(offerPopup.hideTimer);
  offerPopup.hideTimer = 0;
});
$("#offer-popup").addEventListener("mouseleave", (event) => {
  const to = event.relatedTarget;
  if (to && offerPopup.anchor && offerPopup.anchor.contains(to)) return;
  delayHideOfferPopup();
});
// Een andere verpakking in de popup of het venster: hetzelfde product, de geschiedenis van die verpakking.
document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-popup-pack]");
  if (!button) return;
  const popup = button.closest("#offer-popup");
  const dialogBody = button.closest("#offer-zoom-dialog-body");
  const offer = offerById((popup || dialogBody || {}).dataset?.offer);
  if (!offer) return;
  if (popup && offerPopup.anchor) showOfferPopup(offerPopup.anchor, offer, button.dataset.popupPack);
  else if (dialogBody) openOfferZoomDialog(offer, button.dataset.popupPack);
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") hideOfferPopup();
});
// Scrollen van de pagina (niet in de popup zelf) haalt de popup weg: de kaart staat dan ergens anders.
document.addEventListener("scroll", (event) => {
  if (event.target instanceof Node && $("#offer-popup").contains(event.target)) return;
  hideOfferPopup();
}, { capture: true, passive: true });
// De popup staat vast op het scherm en hangt onder <body>, want binnen de inhoud rekent position: fixed vanaf het vak dat scrolt.
document.body.appendChild($("#offer-popup"));
$("#offer-zoom-close").addEventListener("click", () => $("#offer-zoom-dialog").close());
$("#offer-zoom-dialog").addEventListener("click", (event) => {
  if (event.target === event.currentTarget) event.currentTarget.close();
});
$("#offers-view").addEventListener("click", (event) => {
  const button = event.target.closest("[data-view]");
  if (!button || !OFFERS_VIEWS.includes(button.dataset.view)) return;
  offersState.view = button.dataset.view;
  offersRemember(OFFERS_VIEW_KEY, offersState.view);
  offersRedraw();
});
let offersResizeFrame = 0;
window.addEventListener("resize", () => {
  if (!offersTabActive()) return;
  cancelAnimationFrame(offersResizeFrame);
  offersResizeFrame = requestAnimationFrame(renderOfferGrid);
});
$("#offers-index").addEventListener("click", offersIndexRecipes);
document.querySelectorAll(".offers-week").forEach((bar) =>
  bar.addEventListener("click", (event) => {
    const button = event.target.closest("[data-offers-week]");
    if (button && !button.disabled) offersChangeWeek(Number(button.dataset.offersWeek));
  })
);
$("#offers-later-show").addEventListener("click", () => offersChangeWeek(1));
$("#offers-store-list").addEventListener("click", (event) => {
  const item = event.target.closest("[data-store]");
  if (!item) return;
  setSubtabMenu($("#offers-store-menu"), false);
  offersSwitchStore(item.dataset.store);
});
window.addEventListener("ms:languagechange", () => {
  renderOffersStore();
  renderOffersWeek();
  if (offersTabActive()) showOffers();
});
renderOffersStore();
renderOffersWeek();

// ---- Instellingen > Koppelen: de aanbiedingen van alle winkels synchroniseren, en wat de dagelijkse taak deed.
const offersSync = { status: null, result: null };

function offersSyncStoreLine(store, info, failure) {
  const name = STORES[store].name;
  if (failure) return t("offers.sync.store.error", { store: name, error: failure });
  if (!info.linked) return t("offers.sync.store.skipped", { store: name });
  if (!info.syncedAt) return t("offers.sync.store.never", { store: name });
  return tn("offers.sync.store.ok", info.total, { store: name, when: formatSyncTime(info.syncedAt) }) + offersSyncPersonalText(info.personal);
}

// Bij AH: hoeveel aanbiedingen er uit je Bonus Box komen, of waarom het ophalen daarvan mislukte.
function offersSyncPersonalText(personal) {
  if (!personal || !personal.linked) return "";
  if (personal.error) return ` (${t("offers.sync.personal.error", { error: personal.error })})`;
  return personal.count ? ` (${tn("offers.sync.personal.count", personal.count)})` : "";
}

function renderOffersSync() {
  const status = offersSync.status;
  if (!status) return;
  const failures = {};
  for (const [store, info] of Object.entries((offersSync.result || status.lastResult || {}).stores || {})) {
    if (info.error) failures[store] = info.error;
  }
  $("#offers-sync-stores").innerHTML = OFFERS_STORES.map(
    (store) => `<li class="${failures[store] ? "offers-sync-list__failed" : ""}">${escapeHtml(offersSyncStoreLine(store, status.stores[store], failures[store]))}</li>`
  ).join("");
  const lines = [];
  if (status.enabled) {
    lines.push(t("offers.sync.schedule", { hour: String(status.hour).padStart(2, "0"), next: formatSyncTime(status.nextRun) }));
  } else {
    lines.push(t("offers.sync.off"));
  }
  if (status.lastRun) {
    lines.push(t("offers.sync.last", { when: formatSyncTime(status.lastRun), trigger: t(status.lastTrigger === "scheduled" ? "offers.sync.trigger.scheduled" : "offers.sync.trigger.manual") }));
  }
  $("#offers-sync-schedule").textContent = lines.join(" ");
  applyStoreFold("offers", false); // inklapbaar onder Instellingen > Koppelen, standaard open (app.js)
}

async function loadOffersSyncStatus() {
  try {
    offersSync.status = await jsonFetch("/api/offers/status");
    renderOffersSync();
  } catch (error) {
    message("#offers-sync-message", error.message);
  }
}

async function offersSyncAll() {
  const button = $("#offers-sync-all");
  message("#offers-sync-message", "");
  setBusy(button, true, t("offers.sync.running"));
  try {
    offersSync.result = await jsonFetch("/api/offers/sync-all", { method: "POST" });
    await loadOffersSyncStatus();
    const failed = Object.values(offersSync.result.stores).some((info) => info.error);
    message("#offers-sync-message", failed ? t("offers.sync.partial") : t("offers.sync.done"), failed ? "error" : "success");
    if (offersTabActive()) showOffers();
  } catch (error) {
    message("#offers-sync-message", error.message);
  } finally {
    setBusy(button, false, t("offers.sync.running"));
  }
}

$("#offers-sync-all").addEventListener("click", offersSyncAll);
window.addEventListener("ms:languagechange", renderOffersSync);
