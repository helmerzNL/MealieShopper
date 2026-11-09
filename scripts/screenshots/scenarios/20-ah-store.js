// Winkels > Albert Heijn: bewaarde recepten, lijstjes, bestellingen en kassabonnen.
const { dish, product, DISHES, mealieUrl, openSubtab } = require("../demo");

// De taal van de pagina staat in de cookie ms_lang; teksten die normaal van de server komen volgen die.
async function lang(request) {
  const cookie = (await request.headerValue("cookie")) || "";
  return /ms_lang=en/.test(cookie) ? "en" : "nl";
}

// ---- bewaarde recepten -------------------------------------------------------------------------------------
const RECIPE_TITLES = [
  ["Boerenkoolstamppot met rookworst", DISHES.stamppot],
  ["Pasta pesto met kerstomaatjes", DISHES.pesto],
  ["Stroganoff met rijst", DISHES.stroganoff],
  ["Tomatensoep met balletjes", DISHES.soup],
  ["Wraps met kip en avocado", DISHES.wrap],
  ["Curry van kikkererwten", DISHES.curry],
  ["Lasagne met spinazie", DISHES.lasagna],
  ["Zalm met geroosterde groenten", DISHES.fish],
  ["Griekse salade met feta", DISHES.salad],
  ["Pannenkoeken met appel en kaneel", DISHES.pancake],
  ["Preisoep met brood", dish("#a9c96a", "#f2cf45", "#eef1dd")],
  ["Bloemkoolcurry met naan", dish("#e6c27a", "#6aa55a", "#f4ead6")],
];

const savedRecipes = {
  // Een Allerhande-link eindigt op het receptnummer (R-R…); daaraan herkent de app het recept en toont ze Bekijken en Inplannen.
  recipes: RECIPE_TITLES.map(([title, image], index) => ({ id: String(1200 + index), title, image, url: `https://www.example.nl/allerhande/recept/R-R${1200 + index}` })),
  total: RECIPE_TITLES.length,
  // De receptgroepen uit de AH-app; de keuzelijst naast de titel toont ze met Alle recepten erboven.
  groups: [
    { id: "doordeweeks", name: "Doordeweeks", isDefault: false, count: 4, recipeIds: ["1201", "1204", "1205", "1210"] },
    { id: "soep", name: "Soep", isDefault: false, count: 2, recipeIds: ["1203", "1210"] },
    { id: "weekend", name: "Weekend", isDefault: false, count: 4, recipeIds: ["1206", "1202", "1207", "1208"] },
  ],
  syncedAt: "2026-10-27T19:12:00",
  source: "local",
};

// ---- lijstjes ----------------------------------------------------------------------------------------------
const LIST_ID = "weekmarkt";

const lists = {
  lists: [
    // Korte namen: op een telefoon staan de kaarten smal naast elkaar.
    ["weekmarkt", "Weekmarkt", 14, "2026-10-27T19:20:00"],
    ["voorraad", "Voorraad", 22, "2026-10-25T09:05:00"],
    ["ontbijt", "Ontbijt", 11, "2026-10-26T08:30:00"],
    ["bbq", "Barbecue", 18, null],
    ["feest", "Feestje", 27, null],
    ["schoonmaak", "Schoonmaak", 9, "2026-10-20T11:15:00"],
  ].map(([id, name, itemCount, syncedAt]) => ({
    id, name, itemCount, updatedAt: null, synced: Boolean(syncedAt), syncedAt, syncedCount: syncedAt ? itemCount : null, source: syncedAt ? "both" : "ah",
  })),
};

// [titel, merk, inhoud, prijs, kleur, soort, aantal, standaardaantal, bonus]
const LIST_ITEMS = [
  ["Halfvolle melk", "Huismerk", "1 l", 1.29, "#2f7d57", "bottle", 2, 2, true],
  ["Kipfilet", "Huismerk", "300 g", 4.49, "#e08a3c", "pack", 1, 1, false],
  ["Kerstomaten", "Huismerk", "250 g", 1.99, "#d9503f", "pack", 1, 1, false],
  ["Spaghetti", "Huismerk", "500 g", 1.49, "#e6b84a", "pack", 3, 1, false],
  ["Groene pesto", "Huismerk", "190 g", 2.19, "#6aa55a", "jar", 1, 1, false],
  ["Kruimige aardappelen", "Huismerk", "2 kg", 2.79, "#9a6a4a", "pack", 1, 1, false],
  ["Rookworst", "Gelderse", "250 g", 2.39, "#8a4b3a", "pack", 1, 1, true],
  ["Griekse yoghurt", "Huismerk", "500 g", 1.89, "#5b7fa6", "pack", 2, 2, false],
  ["Volkoren brood", "Bakkerij", "800 g", 2.25, "#b98a5a", "pack", 1, 1, false],
  ["Biologische eieren", "Huismerk", "10 stuks", 3.39, "#d9c9a8", "pack", 1, 1, false],
  ["Avocado", "Huismerk", "2 stuks", 2.49, "#4f8a4f", "pack", 2, 2, false],
  ["Kikkererwten", "Huismerk", "400 g", 0.99, "#c9a36a", "jar", 2, 2, false],
  ["Kokosmelk", "Huismerk", "400 ml", 1.35, "#e8e2d2", "jar", 1, 1, false],
  ["Appelsap", "Huismerk", "1 l", 1.65, "#e6a23a", "bottle", 1, 1, false],
].map(([title, brand, unitSize, price, color, kind, quantity, defaultQuantity, isBonus], index) => ({
  id: `item-${index}`, position: index, productId: `p${index}`, url: null, quantity, defaultQuantity, edited: quantity !== defaultQuantity,
  product: { id: `p${index}`, url: null, title, brand, unitSize, price, image: product(color, kind), isBonus },
}));

const listDetail = {
  id: LIST_ID, name: "Weekmarkt", totalSize: LIST_ITEMS.length, items: LIST_ITEMS, editedCount: LIST_ITEMS.filter((item) => item.edited).length,
  syncedAt: "2026-10-27T19:20:00", source: "local",
};

// ---- bestellingen en kassabonnen ---------------------------------------------------------------------------
async function orders(route, request) {
  const nl = (await lang(request)) === "nl";
  const open = (id, date, total, items, text) => ({
    id, kind: "open", state: "OPEN", stateText: text, shoppingType: "HOME_DELIVERY", method: "DELIVERY", total, deliveryDate: date,
    slotStart: "18:00", slotEnd: "20:00", itemsSynced: true, syncedAt: "2026-10-28T09:40:00", itemCount: items,
    statusKind: "planned", statusText: text,
  });
  const done = (id, date, total, items) => ({
    id, kind: "closed", state: "DELIVERED", stateText: nl ? "Geleverd" : "Delivered", shoppingType: "HOME_DELIVERY", method: "DELIVERY", total, deliveryDate: date,
    slotStart: "17:00", slotEnd: "19:00", itemsSynced: true, syncedAt: "2026-10-28T09:40:00", itemCount: items,
    statusKind: "delivered", statusText: null,
  });
  await route.fulfill({
    json: {
      syncedAt: "2026-10-28T09:40:00",
      orders: [
        open(41, "2026-10-30", 87.45, 32, nl ? "Bezorging gepland" : "Delivery planned"),
        done(40, "2026-10-23", 74.2, 28),
        done(39, "2026-10-16", 91.8, 35),
        done(38, "2026-10-09", 63.55, 24),
        done(37, "2026-10-02", 82.1, 30),
        done(36, "2026-09-25", 58.9, 21),
      ],
    },
  });
}

// [titel, merk, inhoud, taxonomie, categorie, aantal, prijs, bonus, eerder, kleur, soort]. Sinds 3.8.0 geeft de detailroute per
// regel de categorie, wat je per stuk betaalde, de gewone prijs (hier de prijs vóór bonus van AH), de korting en hoe vaak je het
// eerder kocht.
const ORDER_ITEMS = [
  ["Halfvolle melk", "Huismerk", "1 l", "Zuivel", "zuivel_eieren", 2, 1.29, true, 12, "#2f7d57", "bottle"],
  ["Kipfilet", "Huismerk", "300 g", "Vlees", "vlees", 2, 4.49, false, 5, "#e08a3c", "pack"],
  ["Kerstomaten", "Huismerk", "250 g", "Groente", "groente_fruit", 2, 1.99, false, 6, "#d9503f", "pack"],
  ["Spaghetti", "Huismerk", "500 g", "Pasta", "maaltijden", 2, 1.49, false, 5, "#e6b84a", "pack"],
  ["Kruimige aardappelen", "Huismerk", "2 kg", "Aardappelen", "groente_fruit", 1, 2.79, false, 4, "#9a6a4a", "pack"],
  ["Rookworst", "Gelderse", "250 g", "Vleeswaren", "kaas_vleeswaren", 2, 2.39, true, 7, "#8a4b3a", "pack"],
  ["Griekse yoghurt", "Huismerk", "500 g", "Zuivel", "zuivel_eieren", 2, 1.89, false, 4, "#5b7fa6", "pack"],
  ["Volkoren brood", "Bakkerij", "800 g", "Brood", "brood_bakkerij", 1, 2.25, false, 0, "#b98a5a", "pack"],
  ["Biologische eieren", "Huismerk", "10 stuks", "Eieren", "zuivel_eieren", 1, 3.39, false, 6, "#d9c9a8", "pack"],
  ["Avocado", "Huismerk", "2 stuks", "Fruit", "groente_fruit", 2, 2.49, false, 3, "#4f8a4f", "pack"],
  ["Kikkererwten", "Huismerk", "400 g", "Conserven", "sauzen_conserven", 2, 0.99, false, 2, "#c9a36a", "jar"],
  ["Appelsap", "Huismerk", "1 l", "Sap", "frisdrank", 1, 1.65, false, 0, "#e6a23a", "bottle"],
].map(([title, brand, unitSize, taxonomy, category, quantity, price, bonus, before, color, kind], index) => {
  const paidEach = Math.round(price * (bonus ? 0.8 : 1) * 100) / 100;
  return {
    productId: `o${index}`, title, brand, unitSize, quantity, amount: Math.round(quantity * paidEach * 100) / 100, price: paidEach,
    priceBeforeBonus: bonus ? price : null, bonus, taxonomy, image: product(color, kind), category, paidEach,
    regularPrice: bonus ? price : null, discount: bonus ? 20 : null, discountSource: bonus ? "store" : null, timesBefore: before,
  };
});
const ORDER_SAVED = Math.round(ORDER_ITEMS.reduce((sum, item) => sum + (item.regularPrice ? (item.regularPrice - item.paidEach) * item.quantity : 0), 0) * 100) / 100;

async function orderDetail(route, request) {
  const nl = (await lang(request)) === "nl";
  await route.fulfill({
    json: {
      id: 41, kind: "open", state: "OPEN", stateText: nl ? "Bezorging gepland" : "Delivery planned", shoppingType: "HOME_DELIVERY", method: "DELIVERY",
      total: 87.45, deliveryDate: "2026-10-30", slotStart: "18:00", slotEnd: "20:00", itemsSynced: true, syncedAt: "2026-10-28T09:40:00",
      itemCount: ORDER_ITEMS.length, items: ORDER_ITEMS, saved: ORDER_SAVED, savedPercent: Math.round((ORDER_SAVED / (87.45 + ORDER_SAVED)) * 100),
      statusKind: "planned", statusText: nl ? "Bezorging gepland" : "Delivery planned",
    },
  });
}

const RECEIPTS = [
  ["2026-10-27T17:42:00", 41.36, 14],
  ["2026-10-24T12:08:00", 18.75, 7],
  ["2026-10-20T18:31:00", 56.2, 22],
  ["2026-10-15T08:55:00", 9.43, 4],
  ["2026-10-11T16:20:00", 33.9, 13],
  ["2026-10-06T19:02:00", 27.18, 10],
].map(([dateTime, total, itemCount], index) => ({ id: `kb-${index + 1}`, dateTime, total, detailsSynced: true, itemCount, statusKind: "receipt", statusText: null }));

const receiptList = { syncedAt: "2026-10-28T09:41:00", receipts: RECEIPTS };

// De kassabon van 27 oktober, zoals op papier: aantal, omschrijving, prijs en bedrag. De meeste regels zijn gekoppeld aan een
// webwinkelproduct (naam en foto, voor de kaarten); twee niet, die houden de afgekorte naam van de bon. Sinds 3.8.0 geeft de
// detailroute per regel ook de categorie, de bonus van de bonusregels, wat je per stuk betaalde en de korting, en voor de bon wat
// je bespaarde en hoeveel regels nog zonder product zijn (`linkingTodo`).
const receiptDetail = {
  ...RECEIPTS[0],
  items: [
    ["AH HALFVOLLE MELK", 2, 1.29, 2.58, "AH Halfvolle melk 1 l", "zuivel_eieren", 0.52, 12],
    ["AH KIPFILET", 1, 4.49, 4.49, "AH Kipfilet 300 g", "vlees", 0, 5],
    ["AH KERSTOMATEN", 1, 1.99, 1.99, "AH Kerstomaten 250 g", "groente_fruit", 0, 6],
    ["AH SPAGHETTI", 2, 1.49, 2.98, "AH Spaghetti 500 g", "maaltijden", 0, 5],
    ["AH PESTO VERDE", 1, 2.19, 2.19, "AH Pesto verde 190 g", "sauzen_conserven", 0, 1],
    ["GELDERSE ROOKWORST", 1, 2.39, 2.39, "Gelderse rookworst 250 g", "kaas_vleeswaren", 0.48, 7],
    ["AH GRIEKSE YOGHURT", 2, 1.89, 3.78, "AH Griekse yoghurt 500 g", "zuivel_eieren", 0, 4],
    ["AH VOLKORENBROOD", 1, 2.25, 2.25, "AH Volkorenbrood heel", "brood_bakkerij", 0, 0],
    ["AH BIO EIEREN 10 ST", 1, 3.39, 3.39, "AH Biologisch scharreleieren 10 stuks", "zuivel_eieren", 0, 6],
    ["AVOCADO 2 STUKS", 2, 2.49, 4.98, null, "groente_fruit", 0, 0],
    ["AH KIKKERERWTEN", 2, 0.99, 1.98, "AH Kikkererwten 400 g", "sauzen_conserven", 0, 2],
    ["AH KOKOSMELK", 1, 1.35, 1.35, "AH Kokosmelk 400 ml", "sauzen_conserven", 0, 1],
    ["APPELSAP 1 L", 1, 1.65, 1.65, null, "frisdrank", 0, 0],
    ["AH FRISDRANK COLA ZERO", 3, 1.19, 3.57, "AH Frisdrank cola zero 1,5 l", "frisdrank", 0, 4],
  ].map(([name, quantity, price, amount, title, category, bonus, before], index) => ({
    productId: `r${index}`, name, quantity, price, amount,
    product: title ? { id: `p${index}`, title, image: LIST_ITEMS[index % LIST_ITEMS.length].product.image } : null,
    category, bonus, paidEach: Math.round((price - bonus / quantity) * 100) / 100, regularPrice: bonus ? price : null,
    discount: bonus ? Math.round((bonus / amount) * 100) : null, discountSource: bonus ? "store" : null, timesBefore: title ? before : 0,
  })),
  discounts: [
    { name: "BONUS HALFVOLLE MELK", amount: -0.52 },
    { name: "BONUS ROOKWORST", amount: -0.48 },
  ],
  payments: [{ method: "PIN", amount: 41.36 }],
  saved: 1.0,
  savedPercent: 2,
  statusKind: "receipt",
  statusText: null,
  linkingTodo: { unlinked: 2, due: 2 },
};

// De toestand van het koppelen (GET /api/ah/linking): geen pauze, wat budget over en wat werk voor de nacht.
const linking = {
  pausedUntil: null, pausedSince: null, pausedStatus: null, budget: { used: 12, limit: 120, resetAt: null },
  todo: { lines: 9, receipts: 4, images: 3 }, lastRound: null, nextRound: "2026-10-29T02:00:00+00:00", running: null,
};

// Zoeken op "frisdrank": producten, kassabonnen met de passende regels en bestellingen.
const purchaseHits = {
  query: "frisdrank",
  products: [
    { product: "AH Frisdrank cola zero", lastDate: "2026-10-27", times: 4, totalQuantity: 9 },
    { product: "Frisdrank sinaasappel", lastDate: "2026-10-20", times: 2, totalQuantity: 4 },
    { product: "Frisdrank limoen", lastDate: "2026-10-06", times: 1, totalQuantity: 2 },
  ],
  occurrences: [
    { source: "bestelling", product: "AH Frisdrank cola zero", brand: "Huismerk", quantity: 3, amount: 3.57, date: "2026-10-23", id: "40", open: false },
    { source: "bestelling", product: "Frisdrank sinaasappel", brand: "Huismerk", quantity: 2, amount: 2.38, date: "2026-10-16", id: "39", open: false },
  ],
  total: 7,
};

const receiptHits = {
  query: "frisdrank",
  total: 3,
  unsearched: 0,
  receipts: [
    { ...RECEIPTS[0], matches: [{ name: "AH FRISDRANK COLA ZERO", quantity: 3, amount: 3.57 }] },
    { ...RECEIPTS[2], matches: [{ name: "FRISDRANK SINAASAPPEL", quantity: 2, amount: 2.38 }, { name: "AH FRISDRANK COLA ZERO", quantity: 1, amount: 1.19 }] },
    { ...RECEIPTS[5], matches: [{ name: "FRISDRANK LIMOEN", quantity: 2, amount: 2.38 }] },
  ],
};

// Koppelen (#238): de producten van AH bij "AVOCADO 2 STUKS", een regel die de app niet herkende.
const linkSearch = {
  store: "ah",
  query: "AVOCADO 2 STUKS",
  products: [
    ["a1", "AH Avocado eetrijp 2 stuks", "AH", "2 stuks", 2.49, "#4f8a4f"],
    ["a2", "AH Avocado", "AH", "per stuk", 1.29, "#3f7a3f"],
    ["a3", "AH Biologisch Avocado 2 stuks", "AH Biologisch", "2 stuks", 2.99, "#6aa55a"],
    ["a4", "AH Guacamole", "AH", "200 g", 2.19, "#9cc26a"],
  ].map(([productId, title, brand, unitSize, price, color]) => ({ productId, title, brand, unitSize, price, image: product(color, "pack") })),
};

const routes = {
  "/api/ah/recipes/saved*": savedRecipes,
  "/api/ah/lists": lists,
  "/api/ah/lists/*": listDetail,
  "/api/ah/lists/search*": { query: "", items: {} },
  "/api/ah/orders": orders,
  "/api/ah/orders/*": orderDetail,
  "/api/ah/receipts": receiptList,
  "/api/ah/receipts/*": receiptDetail,
  "/api/ah/receipts/search*": receiptHits,
  "/api/ah/purchases/search*": purchaseHits,
  "/api/ah/linking": linking,
  "/api/product-links/search*": linkSearch,
};

// ---- in bulk importeren (2.98) -----------------------------------------------------------------------------
// De groep Doordeweeks in één keer naar Mealie: drie nieuw, één stond er al. De server geeft codes; de app maakt er tekst van.
const BULK_GROUP = savedRecipes.groups[0];
const bulkJob = {
  state: "done",
  phase: "",
  store: "ah",
  storeName: "Albert Heijn",
  group: { id: BULK_GROUP.id, name: BULK_GROUP.name },
  category: BULK_GROUP.name,
  whole: true,
  groupSize: BULK_GROUP.count,
  items: BULK_GROUP.recipeIds.map((id, index) => ({
    id,
    title: savedRecipes.recipes.find((recipe) => recipe.id === id).title,
    status: index === 2 ? "existing" : "imported",
    slug: `recept-${id}`,
    mealieUrl: `${mealieUrl}/g/thuis/r/recept-${id}`,
    error: "",
  })),
  warnings: [],
  error: "",
  startedBy: "anna",
  startedAt: "2026-10-28T09:20:00+00:00",
  finishedAt: "2026-10-28T09:20:18+00:00",
  total: 4,
  done: 4,
  imported: 3,
  existing: 1,
  failed: 0,
  skipped: 0,
  remaining: 0,
  limits: { selection: 50, imports: 250, items: 1000 },
};

const bulkRoutes = {
  ...routes,
  "/api/mealie/bulk-import**": (route, request) => {
    const started = request.method() === "POST";
    const body = started ? bulkJob : { state: "idle", limits: bulkJob.limits };
    return route.fulfill({ status: started ? 202 : 200, contentType: "application/json", body: JSON.stringify(body) });
  },
};

// ---- de weekplanner van de AH-app (4.2, losse producten 4.3) -----------------------------------------------
// Vanaf vandaag (woensdag 28 oktober) wat in de AH-app gepland staat: Allerhande-recepten, losse producten (met `standalone`
// voor soep en pizza, zoals de server die zet), een eigen recept en notities. Naar weekmenu (samen als menu, soep en pizza
// los): de recepten, op donderdag een menu van drie producten, op vrijdag de soep die al in Mealie staat, en op zaterdag de
// pizza als eigen recept. Eén recept stond er al.
const plannerRecipe = (id, title, image) => ({ kind: "recipe", recipeId: id, title, image, url: `https://www.example.nl/allerhande/recept/R-R${id}` });
const plannerProduct = (id, title, standalone = false) => ({ kind: "product", productId: id, title, standalone, url: `https://www.example.nl/producten/product/wi${id}` });
const ahPlanner = {
  days: [
    { date: "2026-10-28", items: [plannerRecipe(1201, "Pasta pesto met kerstomaatjes", DISHES.pesto)] },
    {
      date: "2026-10-29",
      items: [plannerProduct(4101, "Kipburgers smokey 2 stuks"), plannerProduct(4102, "Krieltjes met rozemarijn"), plannerProduct(4103, "Sperziebonen")],
    },
    { date: "2026-10-30", items: [{ kind: "note", text: "Soep" }, plannerProduct(4201, "Chinese tomatensoep", true)] },
    { date: "2026-10-31", items: [{ kind: "note", text: "Pizza" }, plannerProduct(4301, "Pizza margherita", true)] },
    { date: "2026-11-01", items: [plannerRecipe(1205, "Curry van kikkererwten", DISHES.curry)] },
    { date: "2026-11-02", items: [{ kind: "own_recipe", recipeId: 77, title: "Oma's erwtensoep" }] },
    { date: "2026-11-03", items: [plannerRecipe(1207, "Zalm met geroosterde groenten", DISHES.fish)] },
  ],
  incomplete: false,
};
const PLANNER_OUTCOME = { 1201: ["already_planned", false], 1205: ["planned", false], 1207: ["planned", true] };

const plannerRoutes = {
  ...routes,
  "/api/ah/planner": ahPlanner,
  "/api/ah/planner/to-meal-plan": (route, request) => {
    const day = JSON.parse(request.postData() || "{}");
    const results = (day.recipeIds || []).map((recipeId) => {
      const [status, imported] = PLANNER_OUTCOME[recipeId] || ["planned", false];
      return { date: day.date, kind: "recipe", title: "", recipeId, status, imported, slug: `recept-${recipeId}`, mealieUrl: `${mealieUrl}/g/thuis/r/recept-${recipeId}` };
    });
    const products = day.products || [];
    if (products.length > 1) {
      results.push({ date: day.date, kind: "menu", title: day.note || products.map((product) => product.title).join(", "), productIds: products.map((product) => product.productId), status: "planned", imported: true, slug: "menu", mealieUrl: `${mealieUrl}/g/thuis/r/menu` });
    } else if (products.length) {
      // De soep stond al in Mealie (zelfde naam); de pizza wordt een eigen recept.
      const known = products[0].productId === 4201;
      results.push({ date: day.date, kind: "product", title: products[0].title, productIds: [products[0].productId], status: "planned", imported: !known, slug: "product", mealieUrl: `${mealieUrl}/g/thuis/r/product` });
    }
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ results }) });
  },
};

// Naar Winkels > Albert Heijn en daar naar het gevraagde onderdeel.
const openStores = (subtab) => async (page) => {
  await page.click('[data-tab="ah-items"]');
  await page.waitForTimeout(500);
  if (subtab) await openSubtab(page, subtab);
  await page.waitForTimeout(700);
};

const openLists = async (page) => {
  await openStores("ah-items-lijstjes")(page);
  await page.click("#load-ah-lists");
  await page.waitForTimeout(700);
};

// Scrollt een blok onder de vaste balk bovenaan.
const scrollTo = (page, selector) =>
  page.evaluate((target) => {
    window.scrollTo(0, document.querySelector(target).getBoundingClientRect().top + window.scrollY - 84);
  }, selector);

// Na het kiezen van een weergave op de bestelpagina: de muis weg (anders staat er een hover op een chip die onder de knop
// schoof) en naar de weergave zelf. Op een telefoon vullen de chips het eerste scherm, daar meteen naar de regels.
async function settleOrderView(page, device) {
  await page.mouse.move(2, 2);
  await page.waitForTimeout(400);
  await scrollTo(page, device === "phone" ? "#ah-order-items" : "#order-categories");
}

module.exports = [
  {
    // De weekplanner van de AH-app (4.2), na Naar weekmenu: de samenvatting boven de dagen.
    name: "ah-planner",
    routes: plannerRoutes,
    run: async (page) => {
      await openStores("ah-items-weekplanner")(page);
      await page.waitForSelector("#ah-planner-to-meal-plan:not([disabled])");
      await page.click("#ah-planner-to-meal-plan");
      await page.waitForSelector("#ah-planner-message .message");
      await scrollTo(page, "#ah-items-weekplanner .box");
    },
  },
  {
    name: "recipes",
    routes,
    run: async (page) => {
      await openStores()(page);
      await scrollTo(page, "#ah-items-recepten .box");
    },
  },
  {
    // Een receptgroep uit de AH-app gekozen (2.94): alleen de recepten van die groep, in de volgorde van de groep.
    name: "recipe-groups",
    routes,
    run: async (page) => {
      await openStores()(page);
      await page.waitForSelector("#ah-recipe-group:not([hidden])");
      await page.selectOption("#ah-recipe-group", "doordeweeks");
      await page.evaluate(() => document.activeElement && document.activeElement.blur());
      await page.waitForTimeout(500);
      await scrollTo(page, "#ah-items-recepten .box");
    },
  },
  {
    // In bulk importeren (2.98): met Selecteren een vakje op elke kaart en onderaan de balk met Importeer N in Mealie.
    name: "recipe-bulk",
    routes: bulkRoutes,
    run: async (page) => {
      await openStores()(page);
      await page.waitForSelector("#ah-recipe-group:not([hidden])");
      await page.selectOption("#ah-recipe-group", "doordeweeks");
      await page.evaluate(() => document.activeElement && document.activeElement.blur());
      await page.click('[data-bulk-select="ah"]');
      const boxes = page.locator("#ah-recipes [data-bulk-id]");
      await boxes.nth(0).check();
      await boxes.nth(2).check();
      await page.waitForTimeout(400);
      await scrollTo(page, "#ah-items-recepten .box");
    },
  },
  {
    // De beheerder importeert de hele groep: het venster met het verslag per recept, met een link naar Mealie.
    name: "recipe-bulk-report",
    routes: bulkRoutes,
    run: async (page) => {
      await openStores()(page);
      await page.waitForSelector("#ah-recipe-group:not([hidden])");
      await page.selectOption("#ah-recipe-group", "doordeweeks");
      await page.evaluate(() => document.activeElement && document.activeElement.blur());
      await page.click('[data-bulk-group="ah"]');
      await page.click("#bulk-run");
      await page.waitForTimeout(500);
    },
  },
  { name: "lists", routes, run: openLists },
  {
    name: "list",
    routes,
    run: async (page) => {
      await openLists(page);
      await page.click(`[data-ah-list-id="${LIST_ID}"]`);
      await page.waitForTimeout(800);
    },
  },
  {
    name: "history",
    routes,
    run: async (page) => {
      await openStores("ah-items-bestellingen")(page);
      await scrollTo(page, "#ah-orders-view");
    },
  },
  {
    // Een bestelling als tabel (de weergave per categorie staat in order-categories, scenario 22).
    name: "order",
    routes,
    run: async (page, { device }) => {
      await openStores("ah-items-bestellingen")(page);
      await page.click('[data-order-id="41"]');
      await page.waitForTimeout(800);
      await page.click('[data-view-bar="order"] [data-view="table"]');
      await settleOrderView(page, device);
    },
  },
  {
    name: "receipts",
    routes,
    run: async (page) => {
      // Sinds de ene tabel met aankopen: alleen de kassabonnen aangevinkt.
      await openStores("ah-items-bestellingen")(page);
      await page.uncheck("#ah-filter-orders");
      await page.waitForTimeout(400);
      await scrollTo(page, "#ah-orders-view");
    },
  },
  {
    name: "receipt",
    routes,
    run: async (page) => {
      await openStores("ah-items-bestellingen")(page);
      await page.click('[data-receipt-id="kb-1"]');
      await page.waitForTimeout(800);
      await page.click('[data-view-bar="order"] [data-view="paper"]');
      await page.waitForTimeout(400);
      await scrollTo(page, "#ah-order-detail");
    },
  },
  {
    // Dezelfde bon in de weergave Kaarten van de weergavebalk (tot 3.7 de productweergave van de bon).
    name: "receipt-products",
    routes,
    run: async (page, { device }) => {
      await openStores("ah-items-bestellingen")(page);
      await page.click('[data-receipt-id="kb-1"]');
      await page.waitForTimeout(800);
      await page.click('[data-view-bar="order"] [data-view="cards"]');
      await settleOrderView(page, device);
    },
  },
  {
    name: "receipt-link",
    routes,
    run: async (page) => {
      await openStores("ah-items-bestellingen")(page);
      await page.click('[data-receipt-id="kb-1"]');
      await page.waitForTimeout(800);
      await page.click('#ah-order-items [data-product-link][data-name="AVOCADO 2 STUKS"]');
      await page.waitForSelector("#link-dialog[open] .link-item");
      await page.waitForTimeout(400);
    },
  },
  {
    name: "receipt-search",
    routes,
    run: async (page) => {
      await openStores("ah-items-bestellingen")(page);
      await page.fill("#ah-purchase-filter", "frisdrank");
      await page.waitForTimeout(1200);
      await page.evaluate(() => document.activeElement.blur());
      await scrollTo(page, "#ah-orders-view");
    },
  },
];
