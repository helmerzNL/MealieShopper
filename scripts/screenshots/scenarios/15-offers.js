// Het tabblad Aanbiedingen: de AH Bonus van de week van het weekmenu, met de weekkeuze, de looptijd van de bonusweek, wat er al
// voor later bekend is, de categorieën en de producten met hun prijs per eenheid en oordeel. Daarnaast de Prijsgeschiedenis en
// Vergelijk verpakkingen.
const { svg, product, perUnit, WEEK } = require("../demo");

// De taal van de pagina staat in de cookie ms_lang; teksten die normaal van de server komen volgen die.
async function lang(request) {
  const cookie = (await request.headerValue("cookie")) || "";
  return /ms_lang=en/.test(cookie) ? "en" : "nl";
}

// [titel, merk, inhoud, prijs, prijs ervoor, actie, categorie, kleur, soort, minimumaantal, prijs per stuk bij de actie]
const BONUS = [
  ["Kipfilet", "AH", "2 x 300 g", 8.98, 11.98, "25% korting", "vlees", "#e3a889", "pack"],
  ["Halfvolle melk", "AH", "1,5 l", 1.39, 1.79, "2e halve prijs", "zuivel_eieren", "#5a8fd0", "bottle", 2, 1.04],
  ["Spaghetti n° 5", "Barilla", "500 g", 2.99, 4.38, "2 voor 2,99", "sauzen_conserven", "#3a62b0", "pack", 2, 1.5],
  ["Rundergehakt", "AH", "500 g", 4.99, 6.49, "voor 4,99", "vlees", "#c0503f", "pack"],
  ["Komkommer", "AH", "per stuk", 0.69, 0.99, "30% korting", "groente_fruit", "#4f8a4a", "pack"],
  ["Jong belegen kaas plakken", "Milner", "300 g", 3.49, 4.65, "25% korting", "kaas_vleeswaren", "#e6b84a", "pack"],
  ["Volkoren tijgerbrood", "AH", "800 g", 1.99, 2.69, "1+1 gratis", "brood_bakkerij", "#c9a36a", "pack", 2, 1.35],
  ["Cola zero", "Coca-Cola", "1,5 l", 3.98, 5.38, "2 voor 3,98", "frisdrank", "#2b2b2b", "bottle", 2, 1.99],
  ["Koffiebonen", "Lavazza", "1 kg", 13.99, 18.99, "voor 13,99", "koffie_thee", "#7a5236", "pack"],
  ["Kerstomaten", "AH", "250 g", 1.49, 1.99, "25% korting", "groente_fruit", "#d9503f", "pack"],
  ["Pesto alla Genovese", "Barilla", "190 g", 2.09, 2.79, "25% korting", "sauzen_conserven", "#6aa55a", "jar"],
  ["Zalmfilet", "AH", "2 stuks", 5.49, 6.99, "voor 5,49", "vis", "#e3a889", "pack"],
  ["Griekse yoghurt", "Fage", "500 g", 2.79, 3.99, "30% korting", "zuivel_eieren", "#dfe7ef", "jar"],
  ["Chips paprika", "Lay's", "225 g", 3.49, 4.98, "2 voor 3,49", "snacks_snoep", "#e8b830", "pack", 2, 1.75],
  ["Pizza margherita", "Dr. Oetker", "2 stuks", 4.99, 6.38, "voor 4,99", "diepvries", "#d9503f", "pack"],
  ["Witbier", "Hoegaarden", "6 x 30 cl", 6.49, 8.99, "voor 6,49", "bier_wijn", "#d8c27a", "bottle"],
  ["Vaatwastabletten", "Finish", "40 stuks", 8.99, 13.49, "1+1 gratis", "huishouden", "#3a62b0", "pack", 2, 6.75],
  ["Tandpasta", "Prodent", "75 ml", 1.29, 2.59, "50% korting", "drogisterie", "#5a8fd0", "pack"],
];

// AH geeft zo'n 3.000 aanbiedingen per bonusweek, per categorie zoveel als hieronder. De eerste 18 zijn die hierboven; de rest
// herhaalt per categorie de eerste aanbieding met een ander id. Het tabblad toont eerst drie rijen; de categorieën en het aantal
// tellen alles mee.
const PER_CATEGORY = {
  vlees: 412, groente_fruit: 388, zuivel_eieren: 341, kaas_vleeswaren: 296, sauzen_conserven: 274, frisdrank: 251, snacks_snoep: 238,
  brood_bakkerij: 187, diepvries: 176, huishouden: 167, drogisterie: 145, koffie_thee: 142, bier_wijn: 131, vis: 99,
};
// Het oordeel per aanbieding (zie prices.annotate), op titel: [soort, procent onder de laagste gewone prijs van 30 dagen, die prijs,
// extra]. Extra: best (laagste actieprijs in een jaar), often (zo vaak dit jaar), inflated (de prijs ervoor is hoger dan wat het de
// laatste 30 dagen kostte), pack (een verpakking die per eenheid goedkoper is: [titel, inhoud, prijs]).
const VERDICTS = {
  Kipfilet: ["good", 25, 11.98, { best: true }],
  "Halfvolle melk": ["small", 13, 1.19],
  "Spaghetti n° 5": ["good", 32, 2.19, { often: 7 }],
  Rundergehakt: ["none", 0, 4.99, { inflated: true, pack: ["Rundergehakt", "1000 g", 8.98] }],
  Komkommer: ["good", 30, 0.99],
  "Jong belegen kaas plakken": ["small", 9, 3.85, { often: 9 }],
  "Volkoren tijgerbrood": ["good", 50, 2.69, { best: true }],
  "Cola zero": ["small", 15, 2.35],
  Koffiebonen: ["good", 26, 18.99],
  Kerstomaten: ["none", 0, 1.49, { inflated: true }],
  "Pesto alla Genovese": ["good", 25, 2.79],
  Zalmfilet: ["small", 8, 5.99],
};
function verdictOf(title) {
  const [kind, percent, lowestRegular, extra = {}] = VERDICTS[title] || ["unknown", null, null];
  const pack = extra.pack ? { productId: "wi-pack", title: extra.pack[0], unitSize: extra.pack[1], price: extra.pack[2], unitPrice: perUnit(extra.pack[2], extra.pack[1]), onOffer: false } : null;
  return {
    kind, percent, lowestRegular, inflated: Boolean(extra.inflated), timesThisYear: extra.often || 2, often: Boolean(extra.often),
    bestInYear: Boolean(extra.best), cheaperPack: pack,
  };
}

const toOffer = ([title, brand, unitSize, price, priceBefore, promoLabel, category, color, kind, minQuantity = 1, unitPrice = null], at) => ({
  id: `wi${100000 + at}`, title, brand, unitSize, price, priceBefore, promoLabel, category,
  image: product(color, kind), url: `https://www.example.nl/producten/product/wi${100000 + at}`,
  validFrom: WEEK[0], validUntil: WEEK[6], validity: "current", minQuantity, unitPrice: unitPrice || price,
  mechanic: /voor \d/.test(promoLabel) && minQuantity === 1 ? "fixed" : "discount",
  unitPricePer: perUnit(unitPrice || price, unitSize), verdict: verdictOf(title),
});
const offers = BONUS.map(toOffer);
for (const [category, count] of Object.entries(PER_CATEGORY)) {
  const first = BONUS.find((item) => item[6] === category);
  for (let have = BONUS.filter((item) => item[6] === category).length; have < count; have += 1) offers.push(toOffer(first, offers.length));
}
const categories = Object.entries(PER_CATEGORY).sort((a, b) => b[1] - a[1]).map(([id, count]) => ({ id, count }));

const offersAnswer = {
  store: "ah", offers, total: offers.length, expired: 0, categories,
  period: { from: WEEK[0], until: WEEK[6] },
  week: { from: WEEK[0], until: WEEK[6], current: true }, later: 2931, undated: 0,
  syncedAt: "2026-10-28T05:00:21Z", supported: true,
};

// ---- Prijsgeschiedenis (/api/prices/history): de aanbiedingen, de gewone prijs door de tijd en wat je betaalde.

// Rundergehakt: de gewone prijs was € 4,99, ging twee weken voor de aanbieding naar € 6,49, en de aanbieding is weer € 4,99.
// [begin, einde, prijs, prijs ervoor, actie]
const MINCE_OFFERS = [
  ["2026-01-12", "2026-01-18", 4.49, 5.79, "voor 4,49"],
  ["2026-03-23", "2026-03-29", 4.79, 5.99, "20% korting"],
  ["2026-06-01", "2026-06-07", 4.49, 5.99, "25% korting"],
  ["2026-08-17", "2026-08-23", 3.99, 4.99, "20% korting"],
  ["2026-10-26", "2026-11-01", 4.99, 6.49, "voor 4,99"],
];
// [van, tot, gewone prijs]
const MINCE_REGULAR = [
  ["2025-11-03", "2026-02-15", 5.79],
  ["2026-02-16", "2026-05-24", 5.99],
  ["2026-05-25", "2026-09-06", 5.49],
  ["2026-09-07", "2026-10-11", 4.99],
  ["2026-10-12", "2026-10-25", 6.49],
];
// [dag, betaald per stuk, aantal, bron]
const MINCE_BOUGHT = [
  ["2025-12-04", 5.79, 1, "receipt"], ["2026-01-15", 4.49, 2, "order"], ["2026-02-26", 5.99, 1, "receipt"], ["2026-04-09", 5.99, 1, "order"],
  ["2026-06-03", 4.49, 2, "order"], ["2026-07-16", 5.49, 1, "receipt"], ["2026-08-19", 3.99, 2, "order"], ["2026-09-24", 4.99, 1, "receipt"],
  ["2026-10-08", 4.99, 1, "order"], ["2026-10-21", 6.49, 1, "receipt"],
];
const mincePurchases = MINCE_BOUGHT.map(([date, price, quantity, source], at) => ({ date, price, regularPrice: null, quantity, source, ref: `b${at}` }));
const minceUnits = mincePurchases.reduce((sum, p) => sum + p.quantity, 0);
const mince = {
  store: "ah", productId: "wi100003", title: "Rundergehakt", brand: "AH", unitSize: "500 g", image: offers[3].image, url: offers[3].url,
  category: "vlees", onOfferNow: true, firstSeen: "2026-01-12T05:00:00Z", lastSeen: "2026-10-28T05:00:21Z",
  episodes: MINCE_OFFERS.map(([start, end, price, priceBefore, promoLabel]) => ({
    from: `${start}T05:00:00Z`, to: end < "2026-10-28" ? `${end}T05:00:00Z` : null, lastSeen: `${end < "2026-10-28" ? end : "2026-10-28"}T05:00:00Z`,
    price, priceBefore, promoLabel, validFrom: start, validUntil: end, syncs: 7, start, end, open: false,
  })),
  timesOnOffer: MINCE_OFFERS.length, lowestPrice: 3.99, highestPrice: 4.99,
  regular: MINCE_REGULAR.map(([from, to, price]) => ({ from, to, price })),
  regularPrice: 6.49, regularSince: "2026-10-12", regularPerUnit: perUnit(6.49, "500 g"), lowestRegular: 4.99, highestRegular: 6.49,
  purchases: mincePurchases, timesBought: mincePurchases.length, lastPaid: 6.49, lowestPaid: 3.99,
  averagePaid: Math.round((mincePurchases.reduce((sum, p) => sum + p.price * p.quantity, 0) / minceUnits) * 100) / 100,
  lastActivity: "2026-10-28",
};
// De andere producten in de lijst links: [id, titel, merk, inhoud, keren in de aanbieding, laagste actieprijs, keren gekocht, nu in de aanbieding]
const others = [
  ["wi100000", "Kipfilet", "AH", "2 x 300 g", 4, 8.49, 6, true],
  ["wi100001", "Halfvolle melk", "AH", "1,5 l", 6, 1.04, 14, true],
  ["wi100002", "Spaghetti n° 5", "Barilla", "500 g", 7, 1.5, 3, true],
  ["wi200417", "Pindakaas", "Calvé", "650 g", 3, 3.99, 5, false],
  ["wi100008", "Koffiebonen", "Lavazza", "1 kg", 5, 12.99, 2, true],
].map(([productId, title, brand, unitSize, timesOnOffer, lowestPrice, timesBought, onOfferNow]) => ({
  store: "ah", productId, title, brand, unitSize, timesOnOffer, lowestPrice, highestPrice: lowestPrice, timesBought, onOfferNow,
  episodes: [], regular: [], purchases: [], regularPrice: null, regularPerUnit: null, lastActivity: "2026-10-28",
}));
const historyAnswer = { products: [mince, ...others], total: others.length + 1, unsynced: { ah: { orders: 0, receipts: 0 } } };

// ---- Alleen eerder gekocht en Bewaarde afbeeldingen (2.92): kipfilet heet tegenwoordig "Scharrel kipfilet" en kreeg een nieuwe foto;
// de bewaarde afbeeldingen laten zien dat het hetzelfde product is. Zelfgetekend: een schaaltje met kipfilet en een etiket.
function tray(label, band, text) {
  return svg(
    `<rect width="120" height="120" fill="#fff"/><rect x="14" y="30" width="92" height="64" rx="12" fill="#dfe5e1"/>` +
      `<rect x="20" y="36" width="80" height="52" rx="9" fill="#f4f6f5"/><ellipse cx="46" cy="60" rx="20" ry="13" fill="#f2c3a6"/>` +
      `<ellipse cx="74" cy="62" rx="20" ry="13" fill="#eeb595"/><rect x="58" y="70" width="44" height="22" rx="4" fill="${label}"/>` +
      `<rect x="58" y="70" width="44" height="7" rx="3" fill="${band}"/><text x="80" y="88" font-family="Arial,sans-serif" font-size="8" font-weight="700" fill="#fff" text-anchor="middle">${text}</text>`
  );
}
const KIP_IMAGES = [
  ["AH Scharrel kipfilet", "2026-09-14", tray("#2f7d57", "#f2cf45", "SCHARREL")],
  ["AH Kipfilet", "2026-04-20", tray("#3a62b0", "#e8eef8", "KIPFILET")],
  ["AH Kipfilet", "2025-11-12", tray("#3a62b0", "#d9503f", "KIPFILET")],
];
// [begin, einde, prijs, prijs ervoor, actie]
const KIP_OFFERS = [
  ["2026-02-02", "2026-02-08", 8.49, 10.98, "25% korting"],
  ["2026-05-11", "2026-05-17", 8.98, 11.49, "voor 8,98"],
  ["2026-08-03", "2026-08-09", 8.49, 11.98, "30% korting"],
  ["2026-10-26", "2026-11-01", 8.98, 11.98, "25% korting"],
];
const KIP_REGULAR = [
  ["2025-11-03", "2026-03-01", 10.98],
  ["2026-03-02", "2026-07-05", 11.49],
  ["2026-07-06", "2026-10-25", 11.98],
];
const KIP_BOUGHT = [
  ["2025-11-13", 10.98, 1, "order"], ["2026-01-08", 10.98, 1, "receipt"], ["2026-02-05", 8.49, 2, "order"], ["2026-04-23", 11.49, 1, "receipt"],
  ["2026-05-14", 8.98, 2, "order"], ["2026-08-06", 8.49, 2, "order"], ["2026-09-17", 11.98, 1, "receipt"], ["2026-10-22", 11.98, 1, "order"],
];
const kipPurchases = KIP_BOUGHT.map(([date, price, quantity, source], at) => ({ date, price, regularPrice: null, quantity, source, ref: `k${at}` }));
const kipUnits = kipPurchases.reduce((sum, p) => sum + p.quantity, 0);
const kip = {
  store: "ah", productId: "wi100000", title: "Scharrel kipfilet", brand: "AH", unitSize: "2 x 300 g", image: KIP_IMAGES[0][2], url: offers[0].url,
  category: "vlees", onOfferNow: true, firstSeen: "2026-02-02T05:00:00Z", lastSeen: "2026-10-28T05:00:21Z",
  episodes: KIP_OFFERS.map(([start, end, price, priceBefore, promoLabel]) => ({
    from: `${start}T05:00:00Z`, to: end < "2026-10-28" ? `${end}T05:00:00Z` : null, lastSeen: `${end < "2026-10-28" ? end : "2026-10-28"}T05:00:00Z`,
    price, priceBefore, promoLabel, validFrom: start, validUntil: end, syncs: 7, start, end, open: false,
  })),
  timesOnOffer: KIP_OFFERS.length, lowestPrice: 8.49, highestPrice: 8.98,
  regular: KIP_REGULAR.map(([from, to, price]) => ({ from, to, price })),
  regularPrice: 11.98, regularSince: "2026-07-06", regularPerUnit: perUnit(11.98, "2 x 300 g"), lowestRegular: 10.98, highestRegular: 11.98,
  purchases: kipPurchases, timesBought: kipPurchases.length, lastPaid: 11.98, lowestPaid: 8.49,
  averagePaid: Math.round((kipPurchases.reduce((sum, p) => sum + p.price * p.quantity, 0) / kipUnits) * 100) / 100,
  lastActivity: "2026-10-28",
  savedImages: KIP_IMAGES.map(([title, day, url], at) => ({ id: 30 - at, url, title, savedAt: `${day}T05:00:12Z`, width: 800, height: 800 })),
};
// Met de schakelaar aan staan alleen producten die je kocht in de lijst.
const bought = [
  ["wi100003", "Rundergehakt", "AH", "500 g", 5, 3.99, 10, true],
  ["wi100001", "Halfvolle melk", "AH", "1,5 l", 6, 1.04, 14, true],
  ["wi200417", "Pindakaas", "Calvé", "650 g", 3, 3.99, 5, false],
  ["wi100008", "Koffiebonen", "Lavazza", "1 kg", 5, 12.99, 2, true],
  ["wi200522", "Volkoren wraps", "AH", "8 stuks", 2, 1.49, 4, false],
].map(([productId, title, brand, unitSize, timesOnOffer, lowestPrice, timesBought, onOfferNow]) => ({
  store: "ah", productId, title, brand, unitSize, timesOnOffer, lowestPrice, highestPrice: lowestPrice, timesBought, onOfferNow,
  episodes: [], regular: [], purchases: [], regularPrice: null, regularPerUnit: null, lastActivity: "2026-10-28",
}));
const historyBoughtAnswer = { products: [kip, ...bought], total: 186, unsynced: { ah: { orders: 0, receipts: 0 } } };

// ---- Oude aankopen importeren (2.93): de JSON die een AI-assistent van de bevestigingsmails maakte, en wat Controleren daarvan zegt.
const IMPORT_JSON = JSON.stringify(
  {
    store: "ah",
    orders: [
      { orderNumber: "46512873", date: "2025-05-22", total: 118.62, items: [
        { name: "AH Bananen tros", quantity: 2, price: 1.19, total: 2.38, discount: 0 },
        { name: "AH Kipfilet 2 x 300 g", quantity: 2, price: 10.98, total: 21.96, discount: 5.49 },
        { name: "AH Halfvolle melk 1,5 l", quantity: 3, price: 1.79, total: 5.37, discount: 0 },
      ] },
      { orderNumber: "46733019", date: "2025-06-05", total: 131.4, items: [
        { name: "AH Rundergehakt 500 g", quantity: 2, price: 5.99, total: 11.98, discount: 0 },
        { name: "Barilla Spaghetti n° 5", quantity: 2, price: 2.19, total: 4.38, discount: 1.39 },
      ] },
    ],
  },
  null,
  2
);
// [bestelnummer of null, dag, producten, reden waarom niet nieuw (nl, en)]
const IMPORT_ORDERS = [
  ["46512873", "2025-05-22", 26, ["is al ingelezen", "was already imported"]],
  ["46733019", "2025-06-05", 31],
  ["46958402", "2025-06-19", 24],
  [null, "2025-07-03", 28],
  ["47402117", "2025-07-17", 22],
  ["48467731", "2025-10-02", 30, ["staat al in je bestellingen", "is already in your orders"]],
];
const importSummary = async (route) => {
  // De prompt komt van de server zelf (price_import.prompt), de telling is nagebootst.
  let prompt = "";
  try {
    prompt = (await (await route.fetch()).json()).prompt || "";
  } catch (e) { /* zonder server geen prompt */ }
  return route.fulfill({ contentType: "application/json", body: JSON.stringify({ store: "ah", orders: 6, lines: 151, first: "2025-01-16", last: "2025-05-22", prompt }) });
};
const importCheck = async (route, request) => {
  const l = await lang(request);
  const orders = IMPORT_ORDERS.map(([number, date, items, reason]) => ({
    ref: number || `dag:${date}`, date, total: null, items, new: !reason, reason: reason ? reason[l === "en" ? 1 : 0] : null,
  }));
  const fresh = orders.filter((order) => order.new);
  return route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({
      store: "ah", dryRun: true, orders, new: fresh.length, duplicates: orders.length - fresh.length,
      lines: fresh.reduce((sum, order) => sum + order.items, 0), problems: [], problemCount: 0, imported: 0,
    }),
  });
};

// ---- Vergelijk verpakkingen (/api/prices/compare): wat de zoekopdracht "Rundergehakt" bij AH vond, per kilo naast elkaar.
// [id, titel, merk, inhoud, prijs, in de aanbieding, zelfde soort, zelfde product]
const FOUND = [
  ["wi301", "AH Half-om-half gehakt", "AH", "500 g", 4.19, false, false, false],
  ["wi302", "AH Rundergehakt", "AH", "1000 g", 8.98, false, true, true],
  ["wi303", "Greenfields Rundergehakt", "Greenfields", "2 x 500 g", 9.49, false, true, false],
  ["wi100003", "AH Rundergehakt", "AH", "500 g", 4.99, true, true, true],
  ["wi304", "AH Rundergehakt minder vet", "AH", "500 g", 5.49, false, true, false],
  ["wi305", "AH Gehaktballen", "AH", "400 g", 4.49, false, false, false],
  ["wi306", "AH Rundergehakt", "AH", "175 g", 2.29, false, true, true],
  ["wi307", "AH Biologisch Rundergehakt", "AH Biologisch", "300 g", 4.29, false, true, false],
];
const mine = perUnit(4.99, "500 g");
const compareAnswer = {
  store: "ah",
  product: { id: "wi100003", title: "Rundergehakt", brand: "AH", unitSize: "500 g", price: 4.99, onOffer: true, promoLabel: "voor 4,99", unitPrice: mine },
  query: "Rundergehakt", unit: "kg", kind: "rundergehakt",
  candidates: FOUND.map(([id, title, brand, unitSize, price, onOffer, sameKind, same], index) => {
    const unitPrice = perUnit(price, unitSize);
    const self = id === "wi100003";
    return {
      id, title, brand, unitSize, price, onOffer, image: null, url: `https://www.example.nl/producten/product/${id}`, unitPrice,
      self, sameKind, sameBrand: self || brand === "AH", same, cheaper: !self && unitPrice.value < mine.value * 0.98, rank: Math.round((index / (FOUND.length - 1)) * 100) / 100,
    };
  }),
};

module.exports = [
  {
    name: "offers",
    routes: { "/api/offers*": offersAnswer },
    run: async (page, { device }) => {
      await page.click('[data-tab="offers"]');
      await page.waitForSelector("#offers-products .product-card");
      // Op het bureaublad de categorieën en de eerste rij producten met hun prijs per eenheid en oordeel; op de telefoon passen de
      // categorieën en de producten niet samen, daar staan de producten bovenaan.
      const [selector, offset] = device === "phone" ? ["#offers-products", 160] : ["#offers-products", 215];
      await page.evaluate(([sel, off]) => window.scrollTo(0, document.querySelector(sel).getBoundingClientRect().top + window.scrollY - off), [selector, offset]);
      await page.waitForTimeout(400);
    },
  },
  {
    // De Prijsgeschiedenis van rundergehakt: de aanbiedingen als balken, de gewone prijs als trap en elke aankoop als stip.
    name: "price-history",
    routes: { "/api/offers*": offersAnswer, "/api/prices/history*": historyAnswer },
    run: async (page, { device }) => {
      await page.click('[data-tab="offers"]');
      await page.click('.subtab[data-subtab="offers-history"]');
      await page.waitForSelector("#offers-history-chart svg");
      await page.evaluate(() => document.activeElement && document.activeElement.blur());
      // De grafiek met daarboven de laatste kerngetallen.
      const offset = device === "phone" ? 150 : 200;
      await page.evaluate((off) => window.scrollTo(0, document.querySelector("#offers-history-chart").getBoundingClientRect().top + window.scrollY - off), offset);
      await page.waitForTimeout(400);
    },
  },
  {
    // Alleen eerder gekocht staat aan; bij kipfilet staan de bewaarde afbeeldingen: de nieuwe naam en foto, en de oude.
    name: "price-images",
    path: "/offers/history",
    routes: {
      "/api/offers*": offersAnswer,
      "/api/prices/history*": (route, request) =>
        route.fulfill({ contentType: "application/json", body: JSON.stringify(/bought=1/.test(request.url()) ? historyBoughtAnswer : historyAnswer) }),
    },
    run: async (page, { device }) => {
      await page.waitForSelector("#offers-history-list .ohc-item");
      await page.check("#offers-history-bought");
      await page.waitForTimeout(800);
      // Rundergehakt blijft gekozen (dat kocht je ook): kies kipfilet, het eerste product in de lijst.
      await page.click("#offers-history-list li:first-child .ohc-item");
      await page.waitForSelector(".ohc-images figure");
      await page.evaluate(() => document.activeElement && document.activeElement.blur());
      if (device === "phone") {
        // Op de telefoon staan de lijst en het product onder elkaar: de bewaarde afbeeldingen in beeld.
        await page.evaluate(() => window.scrollTo(0, document.querySelector(".ohc-images").getBoundingClientRect().top + window.scrollY - 150));
      } else {
        // De schakelaar en de lijst links, en rechts het product met de bewaarde afbeeldingen.
        await page.evaluate(() => {
          const detail = document.querySelector("#offers-history-detail");
          const images = document.querySelector(".ohc-images");
          window.scrollTo(0, document.querySelector("#offers-history-bought").getBoundingClientRect().top + window.scrollY - 140);
          if (detail.scrollHeight > detail.clientHeight) detail.scrollTop = images.offsetTop - detail.offsetTop - 12;
        });
      }
      await page.waitForTimeout(400);
    },
  },
  {
    // Oude aankopen importeren: de JSON van de assistent geplakt en gecontroleerd; per bestelling of ze nieuw is.
    name: "price-import",
    path: "/offers/history",
    routes: { "/api/offers*": offersAnswer, "/api/prices/history*": historyAnswer, "/api/prices/import*": (route, request) => (request.method() === "POST" ? importCheck(route, request) : importSummary(route)) },
    run: async (page) => {
      await page.waitForSelector("#offers-history-import:not([hidden])");
      await page.click("#offers-history-import");
      await page.waitForSelector("#import-dialog-status:not(:empty)");
      await page.fill("#import-data", IMPORT_JSON);
      await page.click("#import-check");
      await page.waitForSelector("#import-report li");
      await page.evaluate(() => document.activeElement && document.activeElement.blur());
      // De JSON vanaf het begin, en het venster onderaan: de controle per bestelling en de knoppen.
      await page.evaluate(() => {
        document.querySelector("#import-data").scrollTop = 0;
        const box = document.querySelector("#import-dialog"); // het venster zelf scrolt
        box.scrollTop = box.scrollHeight;
      });
      await page.waitForTimeout(400);
    },
  },
  {
    // Vergelijk verpakkingen bij de aanbieding van rundergehakt: dezelfde soort, de goedkoopste per kilo bovenaan.
    name: "compare",
    routes: { "/api/offers*": offersAnswer, "/api/prices/compare*": compareAnswer },
    run: async (page) => {
      await page.click('[data-tab="offers"]');
      await page.waitForSelector("#offers-products [data-compare]");
      // Rundergehakt staat niet altijd in de eerste rijen: open het venster zoals de knop dat doet.
      await page.evaluate(() => openPackCompare("ah", "wi100003"));
      await page.waitForSelector("#compare-dialog-list .compare-item");
      await page.evaluate(() => document.activeElement && document.activeElement.blur());
      await page.waitForTimeout(400);
    },
  },
];
