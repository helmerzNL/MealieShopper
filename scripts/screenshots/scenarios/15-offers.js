// Het tabblad Aanbiedingen: de AH Bonus van de week van het weekmenu, met de weekkeuze, de looptijd van de bonusweek, wat er al
// voor later bekend is, de categorieën en de producten met hun prijs per eenheid en oordeel. Daarnaast de Prijsgeschiedenis en
// Vergelijk verpakkingen.
const { product, perUnit, WEEK } = require("../demo");

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
