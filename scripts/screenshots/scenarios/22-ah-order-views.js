// Winkels > Albert Heijn, versie 3.8.0: een bestelling per categorie, het hovervenster van een bestelling op de lijst en de
// melding als AH het koppelen even stillegt. De gegevens zijn nagebootst zoals de detail- en lijstroutes ze sinds 3.8.0 geven
// (categorie, gewone prijs van 30 dagen, korting, eerder gekocht, bespaard en status). Ook de lijst als kaarten en als overzicht
// (purchases-cards en purchases-overview); die waren eerder één keer met de hand gemaakt, bij 3.4.0.
const { product } = require("../demo");

async function lang(request) {
  const cookie = (await request.headerValue("cookie")) || "";
  return /ms_lang=en/.test(cookie) ? "en" : "nl";
}

// [titel, merk, inhoud, categorie, aantal, prijs, gewone prijs van 30 dagen, eerder, bonus, kleur, soort]
const LINES = [
  ["Kerstomaten", "Huismerk", "250 g", "groente_fruit", 2, 1.99, null, 6, false, "#d9503f", "pack"],
  ["Kruimige aardappelen", "Huismerk", "2 kg", "groente_fruit", 1, 2.79, null, 4, false, "#9a6a4a", "pack"],
  ["Avocado", "Huismerk", "2 stuks", "groente_fruit", 2, 1.99, 2.49, 3, true, "#4f8a4f", "pack"],
  ["Bananen", "Huismerk", "5 stuks", "groente_fruit", 1, 1.69, null, 9, false, "#e6c84a", "pack"],
  ["Spinazie", "Huismerk", "300 g", "groente_fruit", 1, 1.49, null, 2, false, "#3f7f3f", "pack"],
  ["Kipfilet", "Huismerk", "300 g", "vlees", 2, 4.49, null, 5, false, "#e08a3c", "pack"],
  ["Rookworst", "Gelderse", "250 g", "kaas_vleeswaren", 2, 1.79, 2.39, 7, true, "#8a4b3a", "pack"],
  ["Jong belegen kaas plakken", "Huismerk", "190 g", "kaas_vleeswaren", 1, 2.89, null, 3, false, "#e8c45a", "pack"],
  ["Halfvolle melk", "Huismerk", "1 l", "zuivel_eieren", 2, 0.99, 1.29, 12, true, "#2f7d57", "bottle"],
  ["Griekse yoghurt", "Huismerk", "500 g", "zuivel_eieren", 2, 1.89, null, 4, false, "#5b7fa6", "pack"],
  ["Biologische eieren", "Huismerk", "10 stuks", "zuivel_eieren", 1, 3.39, null, 6, false, "#d9c9a8", "pack"],
  ["Spaghetti", "Huismerk", "500 g", "maaltijden", 2, 1.49, null, 5, false, "#e6b84a", "pack"],
  ["Kikkererwten", "Huismerk", "400 g", "sauzen_conserven", 2, 0.99, null, 2, false, "#c9a36a", "jar"],
  ["Groene pesto", "Huismerk", "190 g", "sauzen_conserven", 1, 1.64, 2.19, 1, true, "#6aa55a", "jar"],
  ["Appelsap", "Huismerk", "1 l", "frisdrank", 1, 1.65, null, 0, false, "#e6a23a", "bottle"],
  ["Cola zero", "Huismerk", "1,5 l", "frisdrank", 3, 0.89, 1.19, 4, true, "#3a2a2a", "bottle"],
];

const ITEMS = LINES.map(([title, brand, unitSize, category, quantity, price, regular, before, bonus, color, kind], index) => ({
  productId: `o${index}`, title, brand, unitSize, quantity, price, paidEach: price, amount: Math.round(quantity * price * 100) / 100,
  priceBeforeBonus: bonus ? regular : null, bonus, taxonomy: null, image: product(color, kind), category,
  regularPrice: regular, discount: regular ? Math.round(((regular - price) / regular) * 100) : null, discountSource: regular ? "history" : null,
  timesBefore: before,
}));

const SAVED = Math.round(ITEMS.reduce((sum, item) => sum + (item.regularPrice ? (item.regularPrice - item.price) * item.quantity : 0), 0) * 100) / 100;
const TOTAL = Math.round(ITEMS.reduce((sum, item) => sum + item.amount, 0) * 100) / 100;

// De lijst: één open bestelling en negen afgeleverde (genoeg om de rij van het hovervenster bovenaan te zetten), met de foto's, de namen en het bespaarde bedrag voor de kaarten en het overzicht.
async function orders(route, request) {
  const nl = (await lang(request)) === "nl";
  const row = (id, date, total, items, open) => ({
    id, kind: open ? "open" : "closed", state: open ? "OPEN" : "DELIVERED", stateText: open ? (nl ? "Bezorging gepland" : "Delivery planned") : nl ? "Geleverd" : "Delivered",
    shoppingType: "HOME_DELIVERY", method: "DELIVERY", total, deliveryDate: date, slotStart: "18:00", slotEnd: "20:00", itemsSynced: true,
    syncedAt: "2026-10-28T09:40:00", itemCount: items, images: ITEMS.slice(id % 4, (id % 4) + 6).map((item) => item.image),
    productNames: ITEMS.slice(id % 4, (id % 4) + 4).map((item) => item.title), savings: open ? null : Math.round((SAVED - (id % 3)) * 100) / 100,
    bonusCount: 4, statusKind: open ? "planned" : "delivered", statusText: open ? (nl ? "Bezorging gepland" : "Delivery planned") : null,
  });
  await route.fulfill({
    json: {
      syncedAt: "2026-10-28T09:40:00",
      orders: [row(41, "2026-10-30", 87.45, 32, true), row(40, "2026-10-23", TOTAL, ITEMS.length, false), row(39, "2026-10-16", 91.8, 35, false), row(38, "2026-10-09", 63.55, 24, false), row(37, "2026-10-02", 82.1, 30, false), row(36, "2026-09-25", 58.9, 21, false),
        row(35, "2026-09-18", 71.35, 27, false), row(34, "2026-09-11", 66.4, 25, false), row(33, "2026-09-04", 94.2, 36, false), row(32, "2026-08-28", 52.75, 19, false)],
    },
  });
}

// Elke bestelling heeft dezelfde regels; alleen het nummer, de datum en de status volgen het adres.
async function orderDetail(route, request) {
  const id = Number(new URL(request.url()).pathname.split("/").pop()) || 40;
  const open = id === 41;
  await route.fulfill({
    json: {
      id, kind: open ? "open" : "closed", state: open ? "OPEN" : "DELIVERED", stateText: null, shoppingType: "HOME_DELIVERY", method: "DELIVERY",
      total: TOTAL, deliveryDate: open ? "2026-10-30" : "2026-10-23", slotStart: "18:00", slotEnd: "20:00", itemsSynced: true,
      syncedAt: "2026-10-28T09:40:00", itemCount: ITEMS.length, items: ITEMS, saved: SAVED,
      savedPercent: Math.round((SAVED / (TOTAL + SAVED)) * 100), statusKind: open ? "planned" : "delivered", statusText: null,
    },
  });
}

// Twee bonnen: de nieuwste is gekoppeld (de foto's en namen van de webwinkelproducten), de oudere nog niet (het pictogram van de
// bon en de afgekorte namen op de bon). De kaarten en het overzicht tonen zo allebei; de tabel toont geen foto's of namen.
const LINKED = [8, 2, 3, 4, 9, 0].map((index) => ITEMS[index]);
const receipts = {
  syncedAt: "2026-10-28T09:41:00",
  receipts: [
    ["2026-10-27T17:42:00", 41.36, 14, LINKED.map((item) => item.image), LINKED.slice(0, 4).map((item) => item.title), 2.15],
    ["2026-10-20T18:31:00", 56.2, 22, [], ["AH HALFVOLLE MELK", "AVOCADO 2 STUKS"], 1.0],
  ].map(([dateTime, total, itemCount, images, productNames, savings], index) => ({
    id: `kb-${index + 1}`, dateTime, total, detailsSynced: true, itemCount, payment: "PIN", images, productNames,
    savings, statusKind: "receipt", statusText: null,
  })),
};

// De koppelpauze: AH gaf om 09:12 een 429, het koppelen wacht tot 21:12. Met werk dat nog open staat.
const LINKING = {
  pausedUntil: "2026-10-28T20:12:00+00:00",
  pausedSince: "2026-10-28T08:12:00+00:00",
  pausedStatus: 429,
  budget: { used: 37, limit: 120, resetAt: null },
  todo: { lines: 19, receipts: 6, images: 12 },
  lastRound: { at: "2026-10-28T02:00:00+00:00", trigger: "nightly", searches: 40, found: 12, missing: 28, failed: 0, images: 9 },
  nextRound: "2026-10-29T02:00:00+00:00",
  running: null,
};

const routes = {
  "/api/ah/orders": orders,
  "/api/ah/orders/*": orderDetail,
  "/api/ah/receipts": receipts,
  "/api/ah/linking": LINKING,
};

// Het koppelen loopt gewoon: geen pauze, budget over en niets te doen, dus geen melding boven de lijst (linkingNotice in
// static/linking.js). Voor de screenshots van de weergaven, waar de melding alleen ruimte zou kosten.
const QUIET = {
  ...LINKING,
  pausedUntil: null,
  pausedSince: null,
  pausedStatus: null,
  budget: { used: 12, limit: 120, resetAt: null },
  todo: { lines: 0, receipts: 0, images: 0 },
};
const listRoutes = { ...routes, "/api/ah/linking": QUIET };

// Scrollt een blok onder de vaste balk bovenaan, `gap` pixels van de bovenrand.
const scrollTo = (page, selector, gap = 84) =>
  page.evaluate(([target, offset]) => {
    window.scrollTo(0, document.querySelector(target).getBoundingClientRect().top + window.scrollY - offset);
  }, [selector, gap]);

// Kiest een weergave in de balk van de lijst of de bestelpagina (prefix purchase of order).
const pickView = async (page, prefix, view) => {
  await page.click(`#${prefix}-view-switch [data-view="${view}"]`);
  await page.waitForTimeout(400);
};

// De lijst in de weergave Kaarten of Overzicht (bindViewBar in static/purchase_views.js), met de balk (telling, weergaven,
// Sorteren en Opties) bovenaan, zodat de kaarten er heel onder passen. Op een telefoon staat Kaarten al aan (de standaard sinds
// 4.13.0); de klik zet daar Overzicht.
// In het venster gaat het menu links dicht (Menu inklappen): op 930 px breed krijgen de kaarten en de rijen van het overzicht,
// met hun strook van 356 px, anders te weinig ruimte. Op een telefoon is er geen menu links.
const listView = (view) => async (page) => {
  await page.waitForSelector('[data-popover="purchase"]');
  const toggle = page.locator("#nav-toggle");
  if (await toggle.isVisible()) await toggle.click();
  await pickView(page, "purchase", view);
  await page.waitForSelector(view === "cards" ? "#ah-purchase-cards .purchase-card" : "#ah-purchase-overview .purchase-overview__row");
  // De muis weg van de balk: na het scrollen zou hij op een kaart of foto staan en het hovervenster openen (popover.js).
  await page.mouse.move(2, 2);
  await page.evaluate(() => document.activeElement && document.activeElement.blur());
  await page.waitForTimeout(400);
  // De filters net achter de vaste balk bovenaan (.topbar), zodat er geen rand van de bedragvelden onder uitsteekt; de balk van de
  // lijst staat dan met zijn eigen marge eronder. Gemeten in de pagina: de balk is in het venster en op de telefoon niet even hoog.
  await page.evaluate(() => {
    const top = document.querySelector(".topbar").getBoundingClientRect().bottom;
    const filters = document.querySelector("#ah-orders-view .purchase-filters").getBoundingClientRect().bottom + window.scrollY;
    window.scrollTo(0, filters - top);
  });
};

module.exports = [
  {
    // Een bestelling per categorie: statusbadge en bespaard in de kop, de balk, het zoekveld, de chips en de gekleurde secties.
    name: "order-categories",
    path: "/stores/ah/orders/40",
    routes,
    run: async (page) => {
      await page.waitForSelector(".cat-section");
      await page.evaluate(() => document.activeElement && document.activeElement.blur());
      await page.waitForTimeout(400);
      // De titel net onder de vaste balk, zodat er onder de chips nog secties in beeld staan.
      await scrollTo(page, "#ah-order-badges", 116);
    },
  },
  {
    // Het venster van een bestelling bij een muis op de rij in de tabel. Op de telefoon is er geen hover: daar alleen de tabel.
    name: "order-hover",
    path: "/stores/ah/orders",
    routes,
    keepMouse: true,
    run: async (page, { device }) => {
      await page.waitForSelector('[data-popover="purchase"]');
      await pickView(page, "purchase", "table");
      const row = '.purchase-table__row[data-purchase-id="40"]';
      await page.waitForSelector(row);
      if (device === "phone") {
        await page.mouse.move(2, 2);
        await scrollTo(page, "#ah-linking-notice");
        return;
      }
      // De rij bovenaan, zodat het venster eronder past.
      await scrollTo(page, row, 96);
      const box = await page.locator(row).boundingBox();
      await page.mouse.move(box.x + 120, box.y + 20);
      await page.waitForSelector(".popover--purchase .popover__cat", { state: "visible" });
      await page.waitForTimeout(300);
    },
  },
  {
    // De melding boven de lijst als AH het koppelen stillegt, met de knop Nu toch proberen voor de beheerder.
    name: "linking-notice",
    path: "/stores/ah/orders",
    routes,
    run: async (page) => {
      await page.waitForSelector("#ah-linking-notice .message");
      await scrollTo(page, "#ah-orders-view");
    },
  },
  {
    // De lijst als kaarten: per aankoop een collage van vier foto's, de datum, de statusbadge en de streep van de soort, het
    // bezorgmoment of de betaalwijze, producten en bedrag, het bespaarde bedrag en de eerste productnamen.
    name: "purchases-cards",
    path: "/stores/ah/orders",
    routes: listRoutes,
    run: listView("cards"),
  },
  {
    // De lijst als overzicht: per rij de datum en het bedrag, de details op een regel, de productnamen en rechts een strook van
    // zes foto's (of het pictogram van een bon zonder gekoppelde producten).
    name: "purchases-overview",
    path: "/stores/ah/orders",
    routes: listRoutes,
    run: listView("overview"),
  },
];
