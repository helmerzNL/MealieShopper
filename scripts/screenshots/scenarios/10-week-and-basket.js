// Weekmenu naar de winkel, mandjes, recepten zoeken, winkelkeuze en maaltijdboxen.
const { DISHES, WEEK, product, perUnit, openSubtab, openBasket } = require("../demo");
const { NOW } = require("../lib");
// Je eigen recepten in Mealie staan bij Recepten zoeken bovenaan (dezelfde als in de receptkiezer van het weekmenu).
const { mealieRecipes } = require("./01-plan");

// ---- Hulpjes

const dutch = (request) => (request.headers()["accept-language"] || "").startsWith("nl");
const json = (route, body, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
const posted = (request) => {
  try {
    return JSON.parse(request.postData() || "{}");
  } catch {
    return {};
  }
};

// Scrolt zodat het element net onder de balk bovenaan staat.
const scrollTo = (page, selector, offset = 84) =>
  page.evaluate(([sel, off]) => window.scrollTo(0, document.querySelector(sel).getBoundingClientRect().top + window.scrollY - off), [selector, offset]);

const url = (id) => `https://www.example.nl/product/${id}`;

// De boodschappenlijst staat sinds 4.24.0 achter de knop Boodschappen, op de plek van het weekmenu: eerst die weergave openen.
const openShopping = async (page) => {
  await page.waitForSelector("#shop-jump");
  await page.click("#shop-jump");
};

// ---- Het weekmenu en wat erin zit: 20 ingrediënten, zoals een gewone week

const MENU = [
  [0, "dinner", "Pasta pesto met kerstomaatjes", DISHES.pesto, ["400 g spaghetti", "250 g kerstomaten", "200 g groene pesto", "80 g pijnboompitten"]],
  [1, "dinner", "Boerenkoolstamppot met rookworst", DISHES.stamppot, ["1 kg kruimige aardappelen", "500 g boerenkool", "1 rookworst"]],
  [2, "dinner", "Tomatensoep met balletjes", DISHES.soup, ["1 blik tomatenblokjes", "300 g gehakt", "1 ui"]],
  [3, "dinner", "Wraps met kip en avocado", DISHES.wrap, ["4 tortilla wraps", "300 g kipfilet", "2 avocado's"]],
  [4, "dinner", "Curry van kikkererwten", DISHES.curry, ["1 blik kikkererwten", "400 ml kokosmelk"]],
  [5, "lunch", "Pannenkoeken met appel en kaneel", DISHES.pancake, ["250 g bloem", "3 eieren", "2 appels"]],
  [6, "dinner", "Lasagne met spinazie", DISHES.lasagna, ["250 g lasagnebladen", "300 g verse spinazie"]],
];

const mealPlan = MENU.map(([day, type, name, image, ingredients]) => ({
  date: WEEK[day],
  entryType: type,
  imageUrl: image,
  title: name,
  recipe: { name },
  recipeDetail: { name, recipeIngredient: ingredients.map((display) => ({ display })) },
}));

// Hetzelfde weekmenu voor de boodschappenlijst (sinds 4.11.0), met wat je meestal al in huis hebt: olijfolie bij de wraps en
// de curry (dezelfde naam in Mealie, dus één regel) en zout bij de curry. De naam van een ingrediënt in Mealie (`food.name`) is
// waar Al in huis op vergelijkt (shopQuery in static/shopping.js).
// Een eenheid zoals Mealie hem geeft; met useAbbreviation telt "1 el" en "2 el" op tot "3 el".
const TABLESPOON = { name: "eetlepel", pluralName: "eetlepels", abbreviation: "el", useAbbreviation: true };
const STAPLES = {
  3: [{ display: "1 el olijfolie", food: { name: "olijfolie" }, quantity: 1, unit: TABLESPOON }],
  4: [
    { display: "2 el olijfolie", food: { name: "olijfolie" }, quantity: 2, unit: TABLESPOON },
    { display: "zout naar smaak", food: { name: "zout" } },
  ],
};
// Alleen maandag tot en met vrijdag, zodat de lijst met Al in huis eronder op het scherm past.
const shoppingPlan = mealPlan
  .map((entry, at) => ({
    ...entry,
    recipeDetail: { ...entry.recipeDetail, recipeIngredient: [...entry.recipeDetail.recipeIngredient, ...(STAPLES[MENU[at][0]] || [])] },
  }))
  .filter((entry) => entry.date <= WEEK[4]);

// Per ingrediënt de producten die de winkel voorstelt: [trefwoord, kleur, soort, [titel, merk, inhoud, prijs, verpakkingen, twijfel?], ...].
// Het eerste is het voorgestelde product; daarna, zoals bij het weekmenu (prices.check_list_candidates), alleen hetzelfde product in
// een andere maat of wat per eenheid goedkoper is. Bij spaghetti en aardappelen is een grotere verpakking per kilo goedkoper.
const CATALOG = [
  ["spaghetti", "#e6b84a", "pack", ["Spaghetti", "AH", "500 g", 1.49, 1], ["Spaghetti", "AH", "1 kg", 2.59, 1]],
  ["kerstomaten", "#d9503f", "pack", ["Kerstomaten", "AH", "250 g", 1.99, 1], ["Kerstomaten", "AH", "500 g", 3.99, 1]],
  ["pesto", "#6aa55a", "jar", ["Pesto verde", "AH", "190 g", 2.29, 1, true]],
  ["pijnboompitten", "#c9a36a", "pack", ["Pijnboompitten", "AH", "80 g", 3.49, 1]],
  ["aardappelen", "#b98a5a", "pack", ["Kruimige aardappelen", "AH", "2 kg", 2.79, 1], ["Kruimige aardappelen", "AH", "5 kg", 5.49, 1]],
  ["boerenkool", "#3f7f45", "pack", ["Boerenkool gesneden", "AH", "500 g", 1.79, 1]],
  ["rookworst", "#8a4b3a", "pack", ["Rookworst", "Unox", "250 g", 2.39, 1]],
  ["tomatenblokjes", "#d9503f", "jar", ["Tomatenblokjes", "AH", "400 g", 0.79, 1]],
  ["gehakt", "#c0503f", "pack", ["Rundergehakt", "AH", "500 g", 4.99, 1]],
  ["ui", "#d3a24a", "pack", ["Uien", "AH", "1 kg", 1.49, 1]],
  ["wraps", "#e6c98a", "pack", ["Tortilla wraps", "Old El Paso", "8 stuks", 2.09, 1]],
  ["kipfilet", "#e3a889", "pack", ["Kipfilet", "AH", "300 g", 4.49, 1]],
  ["avocado", "#4f8a4a", "pack", ["Avocado eetrijp", "AH", "2 stuks", 2.29, 1]],
  ["kikkererwten", "#d9b86a", "jar", ["Kikkererwten", "AH", "400 g", 0.89, 1]],
  ["kokosmelk", "#8aa3b4", "jar", ["Kokosmelk", "AH", "400 ml", 1.19, 1]],
  ["bloem", "#cfc7b4", "pack", ["Tarwebloem", "AH", "1 kg", 1.09, 1]],
  ["eieren", "#e6d3a6", "pack", ["Scharreleieren", "AH", "10 stuks", 3.19, 1]],
  ["appels", "#c85a4a", "pack", ["Elstar appels", "AH", "1 kg", 2.59, 1]],
  ["lasagnebladen", "#e8d9a0", "pack", ["Lasagnebladen", "AH", "250 g", 1.69, 1]],
  ["spinazie", "#3f7f45", "pack", ["Verse spinazie", "AH", "300 g", 1.79, 1]],
];

// Antwoord van een mandje-controle: per ingrediënt het product en het aantal verpakkingen.
function cartPreview(route, request) {
  const items = (posted(request).items || []).map((item) => {
    const found = CATALOG.find(([word]) => String(item.display || item.query).toLowerCase().includes(word));
    const candidates = found
      ? found.slice(3).map(([title, brand, unitSize, price, packs, doubt], at) => ({
          productId: `${found[0]}-${at}`,
          title,
          brand,
          unitSize,
          price,
          image: product(found[1], found[2]),
          isBonus: false,
          deal: null,
          url: url(`${found[0]}-${at}`),
          packs,
          doubt: doubt ? (dutch(request) ? "Verpakkingsgrootte onbekend" : "Pack size unknown") : null,
          unitPrice: perUnit(price, unitSize),
        }))
      : [];
    // Het voorgestelde product eerst; de rest is hetzelfde product (`same`) en soms per eenheid goedkoper (`cheaper`).
    candidates.forEach((candidate, at) => {
      if (at === 0) candidate.suggested = true;
      else Object.assign(candidate, { same: true, cheaper: candidate.unitPrice.value < candidates[0].unitPrice.value * 0.98 });
    });
    return { query: item.query, display: item.display, quantity: item.quantity ?? null, unit: item.unit ?? null, candidates };
  });
  return json(route, { items });
}

// Antwoord als het mandje gevuld is: alles wat gekozen is, komt erin.
function cartFill(route, request) {
  const items = (posted(request).items || []).map((item) => ({ quantity: item.quantity, product: { title: item.title, unitSize: item.unitSize } }));
  return json(route, { added: items.length, items, skippedItems: [] });
}

// ---- Mandjes

const ahBasket = {
  syncedAt: "2026-10-28T09:12:00+01:00",
  itemCount: 6,
  units: 7,
  total: 15.65,
  deliveryDate: "2026-10-30",
  slotStart: "2026-10-30T18:00:00",
  slotEnd: "2026-10-30T19:00:00",
  items: [
    { quantity: 2, title: "Halfvolle melk", brand: "AH", unitSize: "1 l", taxonomy: "Zuivel", amount: 2.58, image: product("#2f7fb0", "pack") },
    { quantity: 1, title: "Kerstomaten", brand: "AH", unitSize: "250 g", taxonomy: "Groente", amount: 1.99, image: product("#d9503f", "pack") },
    { quantity: 1, title: "Spaghetti", brand: "AH", unitSize: "500 g", taxonomy: "Pasta", amount: 1.49, image: product("#e6b84a", "pack") },
    { quantity: 1, title: "Kipfilet", brand: "AH", unitSize: "300 g", taxonomy: "Vlees", amount: 4.49, image: product("#e3a889", "pack") },
    { quantity: 1, title: "Kruimige aardappelen", brand: "AH", unitSize: "2 kg", taxonomy: "Groente", amount: 2.79, image: product("#b98a5a", "pack") },
    { quantity: 1, title: "Rookworst", brand: "Unox", unitSize: "250 g", taxonomy: "Vlees", bonus: true, amount: 2.39, image: product("#8a4b3a", "pack") },
  ],
};

const picnicLines = [
  ["p1", "Halfvolle melk", "1 l", 2, 1.29, null],
  ["p2", "Kerstomaten", "250 g", 1, 1.89, null],
  ["p3", "Spaghetti", "500 g", 1, 1.39, null],
  ["p4", "Kipfilet", "300 g", 1, 4.19, null],
  ["p6", "Rookworst", "250 g", 1, 2.29, 2.86],
];
const picnicCart = {
  items: picnicLines.map(([id, title, unitSize, quantity, each, listPrice]) => ({ id, title, unitSize, quantity, price: Math.round(each * quantity * 100) / 100, listPrice, maxCount: 99, available: true })),
  checkoutTotal: 10.95,
  savings: 0.57,
};
const slot = (day, from, to, minimumOrder) => ({ windowStart: `${day}T${from}:00+01:00`, windowEnd: `${day}T${to}:00+01:00`, minimumOrder });
const picnicSlots = [slot("2026-10-29", "17:00", "18:00", 35), slot("2026-10-29", "19:00", "20:00", 35), slot("2026-10-30", "08:00", "09:00", 35), slot("2026-10-30", "18:00", "19:00", 35)];

const jumboCart = {
  items: [
    { title: "Halfvolle melk 1 l", quantity: 2, price: 2.5 },
    { title: "Kerstomaten 250 g", quantity: 1, price: 1.85 },
    { title: "Spaghetti 500 g", quantity: 1, price: 1.29 },
    { title: "Kipfilet 300 g", quantity: 1, price: 4.35 },
    { title: "Pesto groen 190 g", quantity: 1, price: 2.19 },
  ],
  totalPrice: 12.18,
};

// ---- Recepten zoeken

const ahRecipe = (id, title, image, minutes, servings, courses) => ({
  id: String(id),
  title,
  webPath: `/allerhande/recept/R-R${id}`,
  images: [{ url: image }],
  totalTime: minutes,
  servings,
  courses,
});
const ahResults = {
  total: 87,
  hasNextPage: true,
  recipes: [
    ahRecipe(1001, "Pasta pesto met kip", DISHES.pesto, 25, 4, ["Hoofdgerecht"]),
    ahRecipe(1002, "Wraps met kip en avocado", DISHES.wrap, 20, 4, ["Lunch"]),
    ahRecipe(1003, "Kipcurry met rijst", DISHES.curry, 35, 4, ["Hoofdgerecht"]),
    ahRecipe(1004, "Kip uit de oven met groenten", DISHES.stamppot, 45, 4, ["Hoofdgerecht"]),
    ahRecipe(1005, "Romige kip stroganoff", DISHES.stroganoff, 30, 4, ["Hoofdgerecht"]),
    ahRecipe(1006, "Kipsalade met ei", DISHES.salad, 15, 2, ["Lunch"]),
    ahRecipe(1007, "Kip tikka masala", DISHES.soup, 40, 4, ["Hoofdgerecht"]),
    ahRecipe(1008, "Kipfilet met pesto en tomaat", DISHES.lasagna, 25, 2, ["Hoofdgerecht"]),
  ],
};

const storeRecipe = (id, name, image, minutes, portions, rating, tags = []) => ({ id, name, image, minutes, portions, rating, tags });
const jumboResults = {
  total: 24,
  recipes: [
    storeRecipe("j1", "Pasta pesto met kip", DISHES.pesto, 35, 4, 4.5, ["Italiaans"]),
    storeRecipe("j2", "Kip met champignonroomsaus", DISHES.stroganoff, 30, 4, 4.3),
    storeRecipe("j3", "Kippenpoot uit de oven", DISHES.stamppot, 50, 4, 4.4),
    storeRecipe("j4", "Kipsaté met pindasaus", DISHES.curry, 30, 4, 4.6),
    storeRecipe("j5", "Wrap met kip en sla", DISHES.wrap, 15, 2, 4.1),
    storeRecipe("j6", "Kip-geitenkaassalade", DISHES.salad, 20, 2, 4.2),
  ],
};
const picnicResults = {
  total: 4,
  recipes: [
    storeRecipe("pn1", "Kip met pesto en rucola", DISHES.pesto, 25, null, null, ["Snel"]),
    storeRecipe("pn2", "Kipcurry met kokosmelk", DISHES.curry, 30, null, null, ["Familie"]),
    storeRecipe("pn3", "Kipwraps met komkommer", DISHES.wrap, 15, null, null, ["Lunch"]),
    storeRecipe("pn4", "Kip uit de wok", DISHES.salad, 20, null, null, ["Snel"]),
  ],
};

// Crisp-recepten (sinds 2.86.0 zoekt Recepten zoeken ook bij Crisp, zodra Crisp gekoppeld is).
const crispResults = {
  total: 3,
  recipes: [
    storeRecipe("cr1", "Kip piri piri met zoete aardappel", DISHES.curry, 35, 2, null, ["Pittig"]),
    storeRecipe("cr2", "Kipsaté met atjar en rijst", DISHES.stroganoff, 30, 2, null, ["Familie"]),
    storeRecipe("cr3", "Salade met gegrilde kip en peer", DISHES.salad, 15, 2, null, ["Snel"]),
  ],
};

const kit = (key, name, headline, image, minutes, cuisine, label, base, week = "2026-W44") => ({ key, name, headline, image, url: `https://www.example.nl/${base}/${key}`, minutes, cuisine, label, week });
const helloFreshMenu = [
  kit("pasta-pesto-met-kip", "Pasta pesto met kip", "met cherrytomaatjes en pijnboompitten", DISHES.pesto, 25, "Italiaans", "Snel", "hellofresh"),
  kit("romige-champignonsoep", "Romige champignonsoep", "met geroosterd brood en bieslook", DISHES.soup, 30, "Frans", "Vegetarisch", "hellofresh"),
  kit("wraps-met-kip-en-avocado", "Wraps met kip en avocado", "met limoen en verse koriander", DISHES.wrap, 20, "Mexicaans", "Snel", "hellofresh"),
  kit("stamppot-boerenkool", "Stamppot boerenkool", "met rookworst en mosterdsaus", DISHES.stamppot, 35, "Nederlands", "Familie", "hellofresh"),
  kit("kipcurry-met-rijst", "Kipcurry met rijst", "met paprika en koriander", DISHES.curry, 35, "Indiaas", "Familie", "hellofresh"),
  kit("zalm-uit-de-oven", "Zalm uit de oven", "met geroosterde groenten", DISHES.fish, 30, "Scandinavisch", "Pescotarisch", "hellofresh"),
  kit("lasagne-met-spinazie", "Lasagne met spinazie", "met ricotta en tomatensaus", DISHES.lasagna, 40, "Italiaans", "Vegetarisch", "hellofresh"),
  kit("salade-met-geitenkaas", "Salade met geitenkaas", "met walnoten en honing", DISHES.salad, 20, "Frans", "Vegetarisch", "hellofresh"),
];
const marleySpoonMenu = [
  kit("kip-teriyaki", "Kip teriyaki met rijst", "met gebakken paksoi en sesam", DISHES.curry, 30, "Aziatisch", "Favoriet", "marleyspoon"),
  kit("ravioli-ricotta", "Ravioli met ricotta", "met salie en pijnboompitten", DISHES.pesto, 25, "Italiaans", "Vegetarisch", "marleyspoon"),
  kit("zalm-met-prei", "Zalm met roomprei", "met krieltjes uit de oven", DISHES.fish, 35, "Nederlands", "Pescotarisch", "marleyspoon"),
  kit("tacos-met-kip", "Tacos met kip", "met mango en limoenmayo", DISHES.wrap, 25, "Mexicaans", "Snel", "marleyspoon"),
  kit("stroganoff-met-rijst", "Stroganoff met rijst", "met champignons en augurk", DISHES.stroganoff, 30, "Russisch", "Familie", "marleyspoon"),
  kit("pompoensoep", "Pompoensoep met brood", "met geroosterde pompoenpitten", DISHES.soup, 30, "Nederlands", "Vegetarisch", "marleyspoon"),
  kit("bowl-met-kip", "Bowl met kip en quinoa", "met avocado en granaatappel", DISHES.salad, 25, "Modern", "Gezond", "marleyspoon"),
  kit("pannenkoeken-appel", "Pannenkoeken met appel", "met kaneelsuiker", DISHES.pancake, 20, "Nederlands", "Familie", "marleyspoon"),
];

const menuAnswer = (list) => ({ week: "2026-W44", currentWeek: "2026-W44", previous: "2026-W43", next: "2026-W45", recipes: list });
const searchKits = (list, weeks) => ({ weeks, total: list.length, recipes: list.map((recipe) => ({ ...recipe })) });

const jumboDetail = {
  id: "j1",
  name: "Pasta pesto met kip",
  image: DISHES.pesto,
  portions: 4,
  minutes: 35,
  rating: 4.5,
  ratingCount: 128,
  author: "Jumbo",
  description: "Een heerlijk recept voor doordeweeks, met producten die je zo in je mandje legt.",
  url: "https://www.example.nl/recept/pasta-pesto-met-kip",
  ingredients: ["300 g kipfilet", "250 g kerstomaten", "300 g penne", "1 potje groene pesto", "40 g pijnboompitten", "1 bakje verse basilicum"].map((line) => ({ line })),
  steps: [
    "Kook de pasta in ruim water volgens de verpakking.",
    "Snijd de kipfilet in blokjes en bak ze goudbruin in een pan met olie.",
    "Voeg de gehalveerde kerstomaten toe en bak nog 3 minuten mee.",
    "Meng de pasta met de kip, de pesto en een scheutje kookwater.",
    "Bak de pijnboompitten goudbruin in een droge pan en strooi ze met de basilicum over de pasta.",
  ],
};

// Antwoorden voor het zoeken bij alle vijf de winkels en aanbieders.
const searchRoutes = {
  "/api/mealie/recipes*": mealieRecipes,
  "/api/ah/search*": ahResults,
  "/api/jumbo/recipes*": jumboResults,
  "/api/jumbo/recipes/*": jumboDetail,
  "/api/picnic/recipes*": picnicResults,
  "/api/crisp/recipes*": crispResults,
  "/api/hellofresh/search*": searchKits(helloFreshMenu.filter((recipe) => /kip|wraps|pasta/i.test(recipe.name)), 4),
  "/api/marleyspoon/search*": searchKits(marleySpoonMenu.filter((recipe) => /kip|tacos|bowl/i.test(recipe.name)), 4),
};

async function search(page) {
  await openSubtab(page, "search");
  await page.fill("#recipe-query", "kip");
  await page.press("#recipe-query", "Enter");
  await page.waitForSelector("#search-marleyspoon-results .recipe-card");
  await page.evaluate(() => document.activeElement.blur());
  await page.waitForTimeout(500);
}

// ---- Winkels

const savedRecipes = {
  source: "cache",
  total: 12,
  syncedAt: "2026-10-27T18:05:00+01:00",
  recipes: [
    ["Boerenkoolstamppot met rookworst", DISHES.stamppot],
    ["Pasta pesto met kerstomaatjes", DISHES.pesto],
    ["Stroganoff met rijst", DISHES.stroganoff],
    ["Tomatensoep met balletjes", DISHES.soup],
    ["Curry van kikkererwten", DISHES.curry],
    ["Lasagne met spinazie", DISHES.lasagna],
    ["Wraps met kip en avocado", DISHES.wrap],
    ["Zalm uit de oven", DISHES.fish],
  ].map(([title, image], at) => ({ title, image, url: `https://www.example.nl/allerhande/recept/R-R${2000 + at}` })),
};

module.exports = [
  {
    // Het weekmenu met Picnic als winkel en het gevulde mandje na de controle.
    name: "plan-picnic",
    routes: { "/api/mealplan*": mealPlan, "/api/picnic/cart/preview": cartPreview, "/api/picnic/cart": cartFill },
    run: async (page) => {
      await openShopping(page);
      await page.waitForSelector("#add-cart");
      await page.click("#cart-store-toggle");
      await page.click('#cart-store-list [data-store="picnic"]');
      await page.click("#add-cart");
      await page.waitForSelector("#confirm-cart");
      await page.click("#confirm-cart");
      await page.waitForSelector("#view-basket");
      await scrollTo(page, "#cart-store-slot", 300);
    },
  },
  {
    // De boodschappenlijst (sinds 4.11.0), achter de knop Boodschappen en op de plek van het weekmenu (sinds 4.24.0), met een pijltje
    // terug. Het is woensdag 28 oktober, dus maandag en dinsdag tellen standaard niet mee (met de regel erover); de ui is
    // uitgevinkt (die heb je nog), en olijfolie en zout staan open onder Al in huis. Onderaan de winkelkeuze, Liever biologisch
    // en de knop naar het mandje.
    name: "shopping-list",
    routes: { "/api/mealplan*": shoppingPlan, "/api/pantry": { items: [{ name: "olijfolie" }, { name: "zout" }] } },
    run: async (page) => {
      await openShopping(page);
      await page.waitForSelector("#shop-box .shop-pantry summary");
      await page.click('#shop-box [data-shop-item="1 ui"]');
      await page.click("#shop-box .shop-pantry summary");
      await page.evaluate(() => document.activeElement && document.activeElement.blur());
      await scrollTo(page, "#shop-box");
    },
  },
  {
    // De controlelijst voor het mandje, met één gemarkeerde regel.
    name: "cart",
    routes: { "/api/mealplan*": mealPlan, "/api/ah/cart/preview": cartPreview },
    run: async (page) => {
      await openShopping(page);
      await page.waitForSelector("#add-cart");
      // De dagen die voorbij zijn tellen standaard niet mee (sinds 4.11.0); hier de hele week, met de twijfelregel van maandag.
      while (await page.locator('[data-shop-day][aria-pressed="false"]').count()) await page.locator('[data-shop-day][aria-pressed="false"]').first().click();
      await page.click("#add-cart");
      await page.waitForSelector(".cart-review");
      // De lijst zoekt in groepen (sinds 4.11.0): wachten tot de stand erboven weg is.
      await page.waitForSelector("#review-progress", { state: "hidden" });
      await scrollTo(page, ".cart-review");
    },
  },
  {
    // Het AH-mandje: de boodschappenlijst met producten, aantal en prijs.
    name: "basket",
    routes: { "/api/ah/basket": ahBasket, "/api/ah/basket/sync": {} },
    run: async (page) => openBasket(page, "ah"),
  },
  {
    // Het Picnic-mandje met plus- en minknoppen, het te betalen bedrag en de bezorgmomenten.
    name: "basket-picnic",
    routes: { "/api/picnic/cart": picnicCart, "/api/picnic/slots*": { slots: picnicSlots } },
    run: async (page, { device }) => {
      await openBasket(page, "picnic");
      // Op het bureaublad omhoog scrollen tot de bedragen en bezorgmomenten in beeld zijn.
      if (device === "desktop") await scrollTo(page, "#basket .box", 70);
    },
  },
  {
    // Het Jumbo-mandje, alleen om te bekijken.
    name: "basket-jumbo",
    routes: { "/api/ah/basket": ahBasket, "/api/ah/basket/sync": {}, "/api/jumbo/cart": jumboCart },
    run: async (page) => openBasket(page, "jumbo"),
  },
  {
    // Recepten zoeken: je eigen recepten in Mealie bovenaan, daaronder Albert Heijn.
    name: "search",
    routes: searchRoutes,
    run: async (page) => {
      await search(page);
      await scrollTo(page, "#recipes > .subtabs", 80);
    },
  },
  {
    // De resultaten van Jumbo met tijd, personen en waardering.
    name: "search-stores",
    routes: searchRoutes,
    run: async (page) => {
      await search(page);
      await scrollTo(page, "#search-jumbo", 130);
    },
  },
  {
    // Een Jumbo-recept met ingrediënten, bereiding en de knoppen Inplannen, Importeer en Bekijk bij Jumbo.
    name: "search-detail",
    routes: searchRoutes,
    run: async (page, { device }) => {
      await search(page);
      await page.click('[data-store-open^="jumbo:"]');
      await page.waitForSelector("#search-detail-import");
      await page.waitForTimeout(300);
      // Op het bureaublad staat de grote foto boven alles: scroll naar de gegevens, de knoppen en de ingrediënten.
      if (device === "desktop") await scrollTo(page, "#search-detail-body .muted", 150);
    },
  },
  {
    // De kookmodus vanuit hetzelfde Jumbo-recept: één stap tegelijk in grote letters, met Vorige en Volgende onderaan. Op het
    // bureaublad staan de ingrediënten ernaast (kipfilet en penne zijn al afgevinkt). De eerste twee stappen zijn klaar, dus dit
    // is stap 3. Sinds 4.14.0 staan eronder de ingrediënten bij de stap (de kerstomaten, automatisch gevonden: een Jumbo-recept
    // heeft geen koppelingen) en de knopjes voor een timer van 3 minuten en een eigen tijd. De timer van 3 minuten loopt al
    // 75 seconden: hij zweeft rechtsonder, boven Vorige en Volgende, met nog 1:45.
    name: "cook-mode",
    routes: searchRoutes,
    run: async (page) => {
      await search(page);
      await page.click('[data-store-open^="jumbo:"]');
      await page.waitForSelector("#search-detail-body [data-cook-start]");
      await page.check('#search-detail-body [data-cook-tick="0"]');
      await page.check('#search-detail-body [data-cook-tick="2"]');
      await page.click('#search-detail-body [data-cook-done="0"]');
      await page.click('#search-detail-body [data-cook-done="1"]');
      await page.click("#search-detail-body [data-cook-start]");
      await page.waitForSelector("#cook-mode[open]");
      await page.click('#cook-mode-timer-row [data-cook-timer-start="180"]');
      await page.waitForSelector("#cook-timers [data-cook-timer]");
      // De klok staat vast (lib.js); verzet hem, dan telt de timer bij de volgende tik af naar 1:45.
      await page.clock.setFixedTime(new Date(NOW.getTime() + 75 * 1000));
      await page.waitForTimeout(1300);
      // De klik op het knopje scrolde de stap; terug naar boven, zodat "Stap 3 van 5" in beeld is.
      await page.evaluate(() => {
        document.activeElement && document.activeElement.blur();
        document.getElementById("cook-mode-stage").scrollTop = 0;
      });
    },
  },
  {
    // De menu's van HelloFresh en Marley Spoon doorzocht op "kip".
    name: "search-meal-kits",
    routes: searchRoutes,
    run: async (page) => {
      await search(page);
      await scrollTo(page, "#search-hellofresh", 100);
    },
  },
  {
    // Het uitgeklapte winkelmenu bij Winkels: Albert Heijn, Jumbo en Picnic.
    name: "stores-menu",
    routes: { "/api/ah/recipes/saved*": savedRecipes },
    run: async (page) => {
      await page.click('[data-tab="ah-items"]');
      await page.waitForSelector("#ah-recipes .recipe-card");
      await page.click("#items-store-toggle");
      await page.waitForSelector("#items-store-list:not([hidden])");
    },
  },
  {
    // Maaltijdboxen: op het bureaublad het uitgeklapte aanbiedersmenu boven het menu van HelloFresh, op de telefoon het menu van Marley Spoon.
    name: "meal-kits",
    routes: { "/api/hellofresh/menu*": menuAnswer(helloFreshMenu), "/api/marleyspoon/menu*": menuAnswer(marleySpoonMenu) },
    run: async (page, { device }) => {
      await page.click('[data-tab="meal-kits"]');
      await page.waitForSelector("#mealkits-recipes .recipe-card");
      await page.click("#mealkits-provider-toggle");
      if (device === "phone") {
        await page.click('#mealkits-provider-list [data-provider="marleyspoon"]');
        await page.waitForFunction(() => document.querySelector("#mealkits-provider-name").textContent === "Marley Spoon");
        await page.waitForTimeout(500);
      }
    },
  },
];
