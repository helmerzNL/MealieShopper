// Crisp: het mandje (tabblad Mandje), Winkels > Crisp (recepten, favorieten en bestellingen) en het koppelen met een code per mail.
const { product, DISHES, openSubtab } = require("../demo");

// Tijdstippen met de Nederlandse winteroffset (de klok gaat op 25 oktober 2026 terug).
const at = (day, time) => `${day}T${time}:00+01:00`;
const atSummer = (day, time) => `${day}T${time}:00+02:00`;

// Naar Winkels, kies Crisp in het uitklapmenu en open een tab.
async function openCrisp(page, subtab) {
  await page.click('[data-tab="ah-items"]');
  await page.click("#items-store-toggle");
  await page.click('#items-store-list [data-store="crisp"]');
  await page.waitForTimeout(600);
  await openSubtab(page, subtab);
  await page.waitForTimeout(700);
}

const scrollTo = (page, y) => page.evaluate((top) => window.scrollTo(0, top), y);

// ---- Het mandje: [id, titel, inhoud, aantal, prijs per stuk, prijs ervoor]
const cartLines = [
  ["c1", "Biologische halfvolle melk", "1 l", 2, 1.65, null],
  ["c2", "Zuurdesem desembrood", "600 g", 1, 3.95, null],
  ["c3", "Kipdijfilet", "400 g", 1, 6.49, 7.99],
  ["c4", "Cherrytomaten", "250 g", 1, 2.29, null],
  ["c5", "Oude kaas van de boer", "300 g", 1, 5.95, null],
  ["c6", "Scharreleieren", "6 stuks", 1, 2.89, null],
];
const crispCart = {
  items: cartLines.map(([id, title, unitSize, quantity, each, listPrice]) => ({
    id, title, unitSize, quantity, price: Math.round(each * quantity * 100) / 100, listPrice: listPrice && Math.round(listPrice * quantity * 100) / 100, maxCount: 24,
  })),
  totalPrice: 26.87,
  checkoutTotal: 26.87,
  minimumOrder: 40,
  stillNeeded: 13.13,
};

// ---- Bewaarde recepten (Door jou bewaard): [id, naam, foto, minuten, porties, ondertitel]
const savedRecipes = [
  ["5501", "Kip piri piri met zoete aardappel", DISHES.curry, 35, 2, "met limoenyoghurt"],
  ["5502", "Kipsaté met atjar en rijst", DISHES.stroganoff, 30, 2, "met kroepoek"],
  ["5503", "Salade met gegrilde kip en peer", DISHES.salad, 15, 2, "met walnoten"],
  ["5504", "Romige pompoensoep", DISHES.soup, 25, 2, "met zuurdesembrood"],
  ["5505", "Zalm met citroen en krieltjes", DISHES.fish, 30, 2, "uit de oven"],
  ["5506", "Pasta pesto met burrata", DISHES.pesto, 20, 2, "met cherrytomaten"],
].map(([id, name, image, minutes, portions, tag]) => ({ id, name, image, minutes, portions, tags: [tag] }));

// ---- Favorieten: [id, titel, inhoud, prijs, kleur, soort, in de aanbieding]
const favorites = [
  ["f1", "Biologische halfvolle melk", "1 l", 1.65, "#5a8fd0", "bottle"],
  ["f2", "Zuurdesem desembrood", "600 g", 3.95, "#c9a36a", "pack"],
  ["f3", "Kipdijfilet", "400 g", 6.49, "#e3a889", "pack", true],
  ["f4", "Oude kaas van de boer", "300 g", 5.95, "#e6b84a", "pack"],
  ["f5", "Griekse yoghurt", "500 g", 2.75, "#dfe7ef", "jar"],
  ["f6", "Verse pasta tagliatelle", "250 g", 2.99, "#e6c98a", "pack"],
  ["f7", "Biologische bananen", "5 stuks", 2.19, "#e8c13a", "pack"],
  ["f8", "Hummus naturel", "200 g", 2.49, "#d9b86a", "jar", true],
].map(([id, title, unitSize, price, color, kind, isBonus]) => ({ id, title, unitSize, price, image: product(color, kind), isBonus: Boolean(isBonus) }));

// ---- Bestellingen: aankomend (live) en bezorgd (lokale kopie)
const upcoming = [{ id: "10482", status: "ready", windowStart: at("2026-10-30", "18:00"), windowEnd: at("2026-10-30", "20:00"), total: 64.35, itemCount: 19 }];
const past = [
  ["10431", "2026-10-23", "18:00", "20:00", 58.9, 17, at],
  ["10377", "2026-10-16", "17:00", "19:00", 72.15, 22, atSummer],
  ["10318", "2026-10-09", "18:00", "20:00", 49.6, 14, atSummer],
  ["10262", "2026-10-02", "19:00", "21:00", 61.4, 18, atSummer],
].map(([id, day, from, to, total, itemCount, when]) => ({
  id, status: "done", deliveryDate: day, windowStart: when(day, from), windowEnd: when(day, to), total, itemsSynced: true, itemCount,
}));

module.exports = [
  {
    // Het Crisp-mandje: aantallen aanpassen, weghalen of legen, en hoeveel er nog ontbreekt tot het minimum.
    name: "basket-crisp",
    routes: { "/api/crisp/cart": crispCart },
    run: async (page, { device }) => {
      await page.click('[data-tab="basket"]');
      await page.waitForSelector("#basket-store");
      await page.selectOption("#basket-store", "crisp");
      await page.waitForSelector("#basket-content .basket-line");
      // Op het bureaublad omhoog scrollen tot het bedrag en het minimum in beeld zijn.
      if (device === "desktop") {
        await page.evaluate(() => window.scrollTo(0, document.querySelector("#basket .box").getBoundingClientRect().top + window.scrollY - 70));
      }
      await page.waitForTimeout(400);
    },
  },
  {
    // Je bewaarde Crisp-recepten: bekijken, inplannen en (met Selecteren) in bulk in Mealie.
    name: "crisp-recipes",
    routes: { "/api/crisp/recipes*": { source: "saved", recipes: savedRecipes, total: savedRecipes.length } },
    run: async (page) => {
      await openCrisp(page, "ah-items-crisp-recepten");
      await page.waitForSelector("#crisp-recipes .recipe-card");
      await scrollTo(page, 170);
    },
  },
  {
    name: "crisp-favorites",
    routes: { "/api/crisp/favorites*": { products: favorites } },
    run: async (page) => {
      await openCrisp(page, "ah-items-crisp-favorieten");
      await page.waitForSelector("#crisp-favorites .product-card");
      await scrollTo(page, 180);
    },
  },
  {
    name: "crisp-orders",
    routes: {
      "/api/crisp/orders": { orders: past, syncedAt: at("2026-10-28", "09:14") },
      "/api/crisp/orders/open": { orders: upcoming },
    },
    run: async (page) => {
      await openCrisp(page, "ah-items-crisp-bestellingen");
      await page.waitForSelector("#crisp-orders-past .list-card");
      await scrollTo(page, 180);
    },
  },
];

module.exports.crispCart = crispCart;
