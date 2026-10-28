// Winkels > Albert Heijn: bewaarde recepten, lijstjes, bestellingen en kassabonnen.
const { dish, product, DISHES } = require("../demo");

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
  recipes: RECIPE_TITLES.map(([title, image], index) => ({ title, image, url: `https://www.example.nl/allerhande/recept/${index + 1}` })),
  total: RECIPE_TITLES.length,
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
  });
  const done = (id, date, total, items) => ({
    id, kind: "closed", state: "DELIVERED", stateText: nl ? "Geleverd" : "Delivered", shoppingType: "HOME_DELIVERY", method: "DELIVERY", total, deliveryDate: date,
    slotStart: "17:00", slotEnd: "19:00", itemsSynced: true, syncedAt: "2026-10-28T09:40:00", itemCount: items,
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

// [titel, merk, inhoud, taxonomie, aantal, prijs, bonus, kleur, soort]
const ORDER_ITEMS = [
  ["Halfvolle melk", "Huismerk", "1 l", "Zuivel", 2, 1.29, true, "#2f7d57", "bottle"],
  ["Kipfilet", "Huismerk", "300 g", "Vlees", 2, 4.49, false, "#e08a3c", "pack"],
  ["Kerstomaten", "Huismerk", "250 g", "Groente", 2, 1.99, false, "#d9503f", "pack"],
  ["Spaghetti", "Huismerk", "500 g", "Pasta", 2, 1.49, false, "#e6b84a", "pack"],
  ["Kruimige aardappelen", "Huismerk", "2 kg", "Aardappelen", 1, 2.79, false, "#9a6a4a", "pack"],
  ["Rookworst", "Gelderse", "250 g", "Vleeswaren", 2, 2.39, true, "#8a4b3a", "pack"],
  ["Griekse yoghurt", "Huismerk", "500 g", "Zuivel", 2, 1.89, false, "#5b7fa6", "pack"],
  ["Volkoren brood", "Bakkerij", "800 g", "Brood", 1, 2.25, false, "#b98a5a", "pack"],
  ["Biologische eieren", "Huismerk", "10 stuks", "Eieren", 1, 3.39, false, "#d9c9a8", "pack"],
  ["Avocado", "Huismerk", "2 stuks", "Fruit", 2, 2.49, false, "#4f8a4f", "pack"],
  ["Kikkererwten", "Huismerk", "400 g", "Conserven", 2, 0.99, false, "#c9a36a", "jar"],
  ["Appelsap", "Huismerk", "1 l", "Sap", 1, 1.65, false, "#e6a23a", "bottle"],
].map(([title, brand, unitSize, taxonomy, quantity, price, bonus, color, kind], index) => ({
  productId: `o${index}`, title, brand, unitSize, quantity, amount: Math.round(quantity * price * (bonus ? 0.8 : 1) * 100) / 100, price,
  priceBeforeBonus: bonus ? price : null, bonus, taxonomy, image: product(color, kind),
}));

async function orderDetail(route, request) {
  const nl = (await lang(request)) === "nl";
  await route.fulfill({
    json: {
      id: 41, kind: "open", state: "OPEN", stateText: nl ? "Bezorging gepland" : "Delivery planned", shoppingType: "HOME_DELIVERY", method: "DELIVERY",
      total: 87.45, deliveryDate: "2026-10-30", slotStart: "18:00", slotEnd: "20:00", itemsSynced: true, syncedAt: "2026-10-28T09:40:00",
      itemCount: ORDER_ITEMS.length, items: ORDER_ITEMS,
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
].map(([dateTime, total, itemCount], index) => ({ id: `kb-${index + 1}`, dateTime, total, detailsSynced: true, itemCount }));

const receiptList = { syncedAt: "2026-10-28T09:41:00", receipts: RECEIPTS };

// De kassabon van 27 oktober, zoals op papier: aantal, omschrijving, prijs en bedrag.
const receiptDetail = {
  ...RECEIPTS[0],
  items: [
    ["AH HALFVOLLE MELK", 2, 1.29, 2.58],
    ["AH KIPFILET", 1, 4.49, 4.49],
    ["AH KERSTOMATEN", 1, 1.99, 1.99],
    ["AH SPAGHETTI", 2, 1.49, 2.98],
    ["AH PESTO VERDE", 1, 2.19, 2.19],
    ["GELDERSE ROOKWORST", 1, 2.39, 2.39],
    ["AH GRIEKSE YOGHURT", 2, 1.89, 3.78],
    ["AH VOLKORENBROOD", 1, 2.25, 2.25],
    ["AH BIO EIEREN 10 ST", 1, 3.39, 3.39],
    ["AVOCADO 2 STUKS", 2, 2.49, 4.98],
    ["AH KIKKERERWTEN", 2, 0.99, 1.98],
    ["AH KOKOSMELK", 1, 1.35, 1.35],
    ["APPELSAP 1 L", 1, 1.65, 1.65],
    ["AH FRISDRANK COLA ZERO", 3, 1.19, 3.57],
  ].map(([name, quantity, price, amount], index) => ({ productId: `r${index}`, name, quantity, price, amount })),
  discounts: [
    { name: "BONUS HALFVOLLE MELK", amount: -0.52 },
    { name: "BONUS ROOKWORST", amount: -0.48 },
  ],
  payments: [{ method: "PIN", amount: 41.36 }],
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
};

// Naar Winkels > Albert Heijn en daar naar het gevraagde onderdeel.
const openStores = (subtab) => async (page) => {
  await page.click('[data-tab="ah-items"]');
  await page.waitForTimeout(500);
  if (subtab) await page.click(`[data-subtab="${subtab}"]`);
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

module.exports = [
  {
    name: "recipes",
    routes,
    run: async (page) => {
      await openStores()(page);
      await scrollTo(page, "#ah-items-recepten .box");
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
      await scrollTo(page, "#ah-orders-main");
    },
  },
  {
    name: "order",
    routes,
    run: async (page) => {
      await openStores("ah-items-bestellingen")(page);
      await page.click('[data-order-id="41"]');
      await page.waitForTimeout(800);
    },
  },
  {
    name: "receipts",
    routes,
    run: async (page) => {
      await openStores("ah-items-bestellingen")(page);
      await scrollTo(page, "#ah-receipts-block");
    },
  },
  {
    name: "receipt",
    routes,
    run: async (page) => {
      await openStores("ah-items-bestellingen")(page);
      await page.click('[data-receipt-id="kb-1"]');
      await page.waitForTimeout(800);
      await scrollTo(page, "#ah-order-detail");
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
      await scrollTo(page, "#ah-purchase-results h3");
    },
  },
];
