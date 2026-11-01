// Het tabblad Aanbiedingen: de AH Bonus van de week van het weekmenu, met de weekkeuze, de looptijd van de bonusweek, wat er al
// voor later bekend is, de categorieën en de producten.
const { product, WEEK } = require("../demo");

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
const toOffer = ([title, brand, unitSize, price, priceBefore, promoLabel, category, color, kind, minQuantity = 1, unitPrice = null], at) => ({
  id: `wi${100000 + at}`, title, brand, unitSize, price, priceBefore, promoLabel, category,
  image: product(color, kind), url: `https://www.example.nl/producten/product/wi${100000 + at}`,
  validFrom: WEEK[0], validUntil: WEEK[6], validity: "current", minQuantity, unitPrice: unitPrice || price,
  mechanic: /voor \d/.test(promoLabel) && minQuantity === 1 ? "fixed" : "discount",
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

module.exports = [
  {
    name: "offers",
    routes: { "/api/offers*": offersAnswer },
    run: async (page, { device }) => {
      await page.click('[data-tab="offers"]');
      await page.waitForSelector("#offers-products .product-card");
      // Op het bureaublad de week, de bonusweek, de categorieën en de eerste producten; op de telefoon passen de categorieën
      // en de producten niet samen, daar staan de producten bovenaan.
      const [selector, offset] = device === "phone" ? ["#offers-products", 160] : ["#offers-items .offers-week", 150];
      await page.evaluate(([sel, off]) => window.scrollTo(0, document.querySelector(sel).getBoundingClientRect().top + window.scrollY - off), [selector, offset]);
      await page.waitForTimeout(400);
    },
  },
];
