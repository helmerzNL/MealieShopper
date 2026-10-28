// Winkels > Picnic (recepten, favorieten, bestellingen) en Winkels > Jumbo (lijstjes, bestellingen, recepten).
const { DISHES, dish, product } = require("../demo");

// De data-uri's zijn lang; een scenario gebruikt ze per naam.
const P = {
  melk: product("#2f7d4f", "pack"),
  kipfilet: product("#c9566e", "pack"),
  tomaten: product("#d9503f", "pack"),
  spaghetti: product("#e6b84a", "pack"),
  pesto: product("#4f9a52", "jar"),
  spinazie: product("#3f8a4a", "pack"),
  wraps: product("#5b7fa6", "pack"),
  brood: product("#b9824a", "pack"),
  aardappelen: product("#8a4b3a", "pack"),
  rookworst: product("#e08a3c", "pack"),
  yoghurt: product("#6fa0c9", "pack"),
  kokosmelk: product("#d9c48a", "pack"),
  ei: product("#e8dcc6", "pack"),
  sap: product("#e08a3c", "bottle"),
};

// Tijdstippen met de Nederlandse winteroffset (de klok gaat op 25 oktober 2026 terug).
const at = (day, time) => `${day}T${time}:00+01:00`;
const atSummer = (day, time) => `${day}T${time}:00+02:00`;

// Naar Winkels, kies de winkel in het uitklapmenu en open een tab.
async function openStore(page, store, subtab) {
  await page.click('[data-tab="ah-items"]');
  await page.click("#items-store-toggle");
  await page.click(`#items-store-list [data-store="${store}"]`);
  await page.waitForTimeout(600);
  if (subtab) {
    await page.click(`[data-subtab="${subtab}"]`);
    await page.waitForTimeout(700);
  }
}

// Scrol de pagina: de kop blijft staan, en zo is de inhoud van het scherm te zien in plaats van alleen het menu.
const scrollTo = (page, y) => page.evaluate((top) => window.scrollTo(0, top), y);

// ---- Picnic
const picnicRecipes = [
  ["p1", "Pasta met geroosterde tomaten en burrata", DISHES.pesto, 25, ["Vegetarisch"]],
  ["p2", "Kip teriyaki met rijst en broccoli", DISHES.curry, 30, ["Kip"]],
  ["p3", "Zalm uit de oven met citroenpuree", DISHES.fish, 35, ["Vis"]],
  ["p4", "Groene curry met kikkererwten", DISHES.stamppot, 30, ["Vegetarisch", "Pittig"]],
  ["p5", "Wraps met gehakt en maïs", DISHES.wrap, 20, ["Snel"]],
  ["p6", "Romige tomatensoep met basilicum", DISHES.soup, 25, ["Soep"]],
  ["p7", "Lasagne met spinazie en ricotta", DISHES.lasagna, 55, ["Oven"]],
  ["p8", "Stroganoff van champignons", DISHES.stroganoff, 30, ["Vegetarisch"]],
].map(([id, name, image, minutes, tags]) => ({ id, name, image, minutes, tags }));

const picnicFavorites = [
  ["f1", "Halfvolle melk", "1 l", 1.29, P.melk],
  ["f2", "Kipfilet", "300 g", 4.19, P.kipfilet],
  ["f3", "Cherrytomaten", "250 g", 1.99, P.tomaten],
  ["f4", "Spaghetti", "500 g", 1.19, P.spaghetti],
  ["f5", "Verse groene pesto", "190 g", 2.49, P.pesto, true],
  ["f6", "Babyspinazie", "200 g", 1.79, P.spinazie],
  ["f7", "Tortilla wraps", "8 stuks", 2.29, P.wraps],
  ["f8", "Volkoren brood", "800 g", 2.19, P.brood],
  ["f9", "Griekse yoghurt", "500 g", 1.99, P.yoghurt, true],
  ["f10", "Kokosmelk", "400 ml", 1.39, P.kokosmelk],
].map(([id, title, unitSize, price, image, isBonus]) => ({ id, title, unitSize, price, image, isBonus: Boolean(isBonus) }));

const picnicItems = [
  ["s1", "Halfvolle melk", 3, "1 l", 1.29, P.melk],
  ["s2", "Kruimige aardappelen", 1, "2 kg", 2.65, P.aardappelen],
  ["s3", "Rookworst", 2, "250 g", 2.29, P.rookworst],
  ["s4", "Kipfilet", 2, "300 g", 4.19, P.kipfilet],
  ["s5", "Babyspinazie", 1, "300 g", 1.79, P.spinazie],
  ["s6", "Tortilla wraps", 1, "8 stuks", 2.49, P.wraps],
  ["s7", "Volkoren brood", 1, "800 g", 2.19, P.brood],
].map(([id, title, quantity, unitSize, price, image]) => ({ id, title, quantity, unitSize, price, image }));

const picnicPast = [
  ["d1", "2026-10-22", "17:00", "18:00", 52.4, 11],
  ["d2", "2026-10-15", "18:00", "19:00", 47.85, 9],
  ["d3", "2026-10-08", "17:00", "18:00", 61.2, 14],
  ["d4", "2026-10-01", "18:00", "19:00", 38.95, 8],
].map(([id, day, from, to, total, itemCount]) => ({
  id, status: "COMPLETED", windowStart: atSummer(day, from), windowEnd: atSummer(day, to), deliveredStart: atSummer(day, from), total, itemsSynced: true, itemCount,
}));
const picnicUpcoming = [{ id: "d0", status: "CURRENT", windowStart: at("2026-10-29", "17:00"), windowEnd: at("2026-10-29", "18:00"), total: 44.7 }];

// ---- Jumbo
const jumboRecipes = [
  ["j1", "Kip tikka masala met rijst", DISHES.curry, 35, 4, 4.5, ["Kip", "Indiaas"]],
  ["j2", "Pasta pesto met kerstomaatjes", DISHES.pesto, 20, 4, 4.2, ["Vegetarisch"]],
  ["j3", "Hutspot met draadjesvlees", DISHES.stamppot, 90, 4, 4.4, ["Hollands"]],
  ["j4", "Zalmfilet met groene asperges", DISHES.fish, 25, 2, 4.6, ["Vis"]],
  ["j5", "Pannenkoeken met appel en kaneel", DISHES.pancake, 30, 4, 4.7, ["Zoet"]],
  ["j6", "Griekse salade met feta", DISHES.salad, 15, 2, 4.1, ["Vegetarisch", "Snel"]],
  ["j7", "Lasagne met spinazie", DISHES.lasagna, 60, 6, 4.3, ["Oven"]],
  ["j8", "Stroganoff met rijst", DISHES.stroganoff, 30, 4, 4.0, ["Rund"]],
].map(([id, name, image, minutes, portions, rating, tags]) => ({ id, name, image, minutes, portions, rating, tags }));

const jumboRecipeDetail = {
  id: "j1", name: "Kip tikka masala met rijst", image: DISHES.curry, portions: 4, minutes: 35, rating: 4.5, ratingCount: 128, author: "Jumbo",
  description: "Een heerlijk recept voor doordeweeks, met producten die je zo in je mandje legt.",
  url: "https://www.jumbo.com/recepten/kip-tikka-masala-met-rijst",
  ingredients: ["500 g kipfilet", "1 ui", "2 teentjes knoflook", "3 el tikka masala pasta", "400 g tomatenblokjes", "200 ml kokosmelk", "300 g basmatirijst", "1 bosje verse koriander"].map((line) => ({ line })),
  steps: [
    "Snijd de kip in blokjes en snipper de ui. Pers de knoflook.",
    "Bak de kip in een pan met olie in 5 minuten bruin. Voeg de ui en knoflook toe en bak 2 minuten mee.",
    "Roer de tikka masala pasta erdoor, voeg de tomatenblokjes en de kokosmelk toe en laat 15 minuten zachtjes sudderen.",
    "Kook ondertussen de rijst volgens de verpakking. Serveer de kip over de rijst met de gehakte koriander.",
  ],
  tip: "Wil je het pittiger? Voeg een gesnipperde rode peper toe aan de ui.",
  nutrition: [{ name: "Energie", quantity: 612, unit: "kcal" }, { name: "Vet", quantity: 21, unit: "g" }, { name: "Koolhydraten", quantity: 63, unit: "g" }, { name: "Eiwit", quantity: 42, unit: "g" }],
};

const jumboLists = [
  { id: "JUMBO", name: "Boodschappenlijstje", itemCount: 12, synced: true, syncedAt: at("2026-10-28", "09:15"), syncedCount: 12, source: "both" },
  { id: "l-week", name: "Weekboodschappen", itemCount: 24, synced: true, syncedAt: at("2026-10-27", "18:20"), syncedCount: 24, source: "both" },
  { id: "l-bbq", name: "Barbecue in de tuin", itemCount: 15, synced: true, syncedAt: at("2026-10-20", "11:05"), syncedCount: 15, source: "both" },
  { id: "l-fav", name: "Favorieten", itemCount: 38, synced: true, syncedAt: at("2026-10-26", "20:40"), syncedCount: 36, source: "both" },
  { id: "l-baby", name: "Verjaardagstaart", itemCount: 9, synced: false, source: "store" },
  { id: "l-pasta", name: "Pastaavond", itemCount: 11, synced: true, syncedAt: at("2026-10-12", "17:30"), syncedCount: 11, source: "both" },
];

const jumboOrders = [
  { id: "o-open", kind: "open", stateText: "Bezorging gepland", deliveryDate: "2026-10-30", slotStart: at("2026-10-30", "18:00"), slotEnd: at("2026-10-30", "20:00"), total: 63.45, itemsSynced: true, itemCount: 17 },
  ["o1", "2026-10-23", "18:00", "20:00", 58.2, 15],
  ["o2", "2026-10-16", "17:00", "19:00", 71.35, 21],
  ["o3", "2026-10-09", "18:00", "20:00", 49.8, 13],
  ["o4", "2026-10-02", "19:00", "21:00", 66.1, 18],
  ["o5", "2026-09-25", "17:00", "19:00", 54.95, 14],
].map((order) => (Array.isArray(order)
  ? { id: order[0], kind: "delivered", deliveryDate: order[1], slotStart: atSummer(order[1], order[2]), slotEnd: atSummer(order[1], order[3]), total: order[4], itemsSynced: true, itemCount: order[5] }
  : order));

module.exports = [
  {
    name: "picnic-recipes",
    routes: { "/api/picnic/recipes*": { recipes: picnicRecipes } },
    run: async (page) => {
      await openStore(page, "picnic");
      await scrollTo(page, 170);
    },
  },
  {
    name: "picnic-favorites",
    routes: { "/api/picnic/favorites*": { products: picnicFavorites, total: 10, filtered: true, pending: 0 } },
    run: async (page) => {
      await openStore(page, "picnic", "ah-items-picnic-favorieten");
      await scrollTo(page, 180);
    },
  },
  {
    name: "picnic-orders",
    routes: {
      "/api/picnic/orders": { orders: picnicPast, syncedAt: at("2026-10-28", "09:12") },
      "/api/picnic/deliveries*": { deliveries: picnicUpcoming },
    },
    run: async (page) => {
      await openStore(page, "picnic", "ah-items-picnic-bestellingen");
      await scrollTo(page, 180);
    },
  },
  {
    name: "picnic-order",
    routes: {
      "/api/picnic/orders": { orders: picnicPast, syncedAt: at("2026-10-28", "09:12") },
      "/api/picnic/deliveries*": { deliveries: picnicUpcoming },
      "/api/picnic/orders/*": { ...picnicPast[0], items: picnicItems },
    },
    run: async (page) => {
      await openStore(page, "picnic", "ah-items-picnic-bestellingen");
      await page.click('[data-picnic-order="d1"]');
      await page.waitForTimeout(700);
    },
  },
  {
    name: "jumbo-lists",
    routes: { "/api/jumbo/lists": { lists: jumboLists } },
    run: async (page) => {
      await openStore(page, "jumbo", "ah-items-lijstjes");
      await scrollTo(page, 90);
    },
  },
  {
    name: "jumbo-orders",
    routes: { "/api/jumbo/orders": { orders: jumboOrders, syncedAt: at("2026-10-28", "09:10") } },
    run: async (page) => {
      await openStore(page, "jumbo", "ah-items-bestellingen");
      await scrollTo(page, 170);
    },
  },
  {
    name: "jumbo-recipes",
    routes: {
      "/api/jumbo/recipe-lists": { lists: [{ id: "fav", name: "Favorieten", count: 24, kind: "favorites" }, { id: "doordeweeks", name: "Doordeweeks", count: 12, kind: "own" }, { id: "feestdagen", name: "Feestdagen", count: 8, kind: "own" }] },
      "/api/jumbo/favorite-recipes*": { recipes: jumboRecipes, total: 24, hasNext: true },
    },
    run: async (page) => {
      await openStore(page, "jumbo");
      await scrollTo(page, 170);
    },
  },
  {
    name: "jumbo-recipe",
    routes: {
      "/api/jumbo/recipe-lists": { lists: [{ id: "fav", name: "Favorieten", count: 24, kind: "favorites" }] },
      "/api/jumbo/favorite-recipes*": { recipes: jumboRecipes, total: 24, hasNext: false },
      "/api/jumbo/recipes/*": jumboRecipeDetail,
    },
    run: async (page) => {
      await openStore(page, "jumbo");
      await page.click('[data-store-open="jumbo:j1"]');
      await page.waitForTimeout(800);
      // De foto is groot: scrol naar de ingrediënten, zodat ook de bereiding, de tip en de voedingswaarden in beeld komen.
      await page.evaluate(() => window.scrollTo(0, document.querySelector("#jumbo-recipe-body h3").getBoundingClientRect().top + window.scrollY - 130));
    },
  },
];
