const { DISHES, WEEK, mealieUrl } = require("../demo");

// Een maaltijd zoals /api/mealplan die geeft: met het id (dan is ze aan te tikken: een tik opent het recept, het potlood wijzigt
// haar), de porties uit de planner van Mealie (`servings`, zie mealie.py: `base` uit het recept, `scale` uit de planner en
// `planned` = base x scale), de bereidingstijd en het adres van het recept in Mealie. `extra` vult het recept aan (omschrijving, bereiding).
let nextId = 101;
const slugOf = (name) => name.toLowerCase().normalize("NFD").replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "");
const entry = (day, type, name, image, ingredients, base = 4, scale = 1, minutes = 30, extra = {}) => ({
  id: nextId++, date: WEEK[day], entryType: type, imageUrl: image, title: name, recipeId: `r-${nextId}`, recipe: { name },
  recipeDetail: { name, recipeIngredient: ingredients.map((display) => ({ display })), ...extra },
  servings: { scale, base, planned: base * scale },
  recipeMinutes: minutes, recipeUrl: `${mealieUrl}/g/home/r/${slugOf(name)}`,
});

// Het recept dat in het venster opent: de hoeveelheden zijn al omgerekend naar de geplande porties (zoals de week ze geeft).
const PESTO = {
  description: "Snelle doordeweekse pasta met romige pesto, zoete kerstomaatjes uit de pan en geroosterde pijnboompitten.",
  recipeInstructions: [
    "Kook de spaghetti in ruim water met zout beetgaar volgens de verpakking.",
    "Rooster de pijnboompitten in een droge koekenpan goudbruin en zet ze apart.",
    "Halveer de kerstomaatjes en bak ze 3 minuten in een scheutje olijfolie.",
    "Giet de pasta af en bewaar een kopje kookvocht. Meng de pasta met de pesto en een scheut kookvocht.",
    "Schep de tomaatjes erdoor en bestrooi met de pijnboompitten en de Parmezaanse kaas.",
  ].map((text) => ({ text })),
};
// Een notitie zonder recept, zoals "Uit eten".
const note = (day, type, title, text) => ({ id: nextId++, date: WEEK[day], entryType: type, title, text, recipe: null, recipeId: null });

const entries = [
  entry(0, "dinner", "Pasta pesto met kerstomaatjes", DISHES.pesto, ["400 g spaghetti", "250 g kerstomaten", "200 g groene pesto", "80 g pijnboompitten", "40 g Parmezaanse kaas", "2 el olijfolie"], 4, 1, 20, PESTO),
  entry(1, "dinner", "Boerenkoolstamppot met rookworst", DISHES.stamppot, ["1 kg kruimige aardappelen", "500 g boerenkool", "1 rookworst"], 4, 1.5, 40),
  entry(2, "dinner", "Tomatensoep met balletjes", DISHES.soup, ["1 blik tomatenblokjes", "300 g gehakt", "1 ui"], 4, 0.5, 45),
  entry(3, "dinner", "Wraps met kip en avocado", DISHES.wrap, ["4 tortilla wraps", "300 g kipfilet", "2 avocado's"], 4, 1, 20),
  note(4, "dinner", { nl: "Uit eten", en: "Eating out" }, { nl: "Verjaardag van oma", en: "Grandma's birthday" }),
  entry(5, "lunch", "Pannenkoeken met appel en kaneel", DISHES.pancake, ["250 g bloem", "3 eieren", "2 appels"], 4, 0.5, 30),
  entry(6, "dinner", "Lasagne met spinazie", DISHES.lasagna, ["250 g lasagnebladen", "300 g spinazie"], 6, 1, 60),
];

// De recepten in Mealie, als receptkaart (zie mealie.recipe_card): voor de receptkiezer bij Toevoegen en voor Recepten zoeken.
const MEALIE_RECIPES = [
  ["Kip tandoori met naan", DISHES.curry, 35, 4, ["Kip", "Doordeweeks"]],
  ["Curry van kikkererwten", DISHES.curry, 30, 4, ["Vegetarisch", "Doordeweeks"]],
  ["Zalm uit de oven met groenten", DISHES.fish, 35, 4, ["Vis"]],
  ["Stroganoff met rijst", DISHES.stroganoff, 40, 4, ["Vlees"]],
  ["Salade met geitenkaas en peer", DISHES.salad, 15, 2, ["Vegetarisch", "Snel"]],
  ["Lasagne met spinazie", DISHES.lasagna, 60, 6, ["Ovenschotel"]],
  ["Pasta pesto met kerstomaatjes", DISHES.pesto, 20, 4, ["Snel"]],
  ["Kippensoep van oma", DISHES.soup, 90, 6, ["Kip", "Soep"]],
  ["Wraps met kip en avocado", DISHES.wrap, 20, 4, ["Kip", "Snel"]],
  ["Kip teriyaki met rijst", DISHES.stroganoff, 25, 4, ["Kip"]],
].map(([name, image, minutes, portions, tags], at) => {
  const slug = name.toLowerCase().normalize("NFD").replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "");
  return { id: slug, slug, recipeId: `r-${at}`, name, image, minutes, portions, rating: null, tags };
});

const mealieRecipes = (route, request) => {
  const query = (new URL(request.url()).searchParams.get("q") || "").toLowerCase();
  const recipes = MEALIE_RECIPES.filter((recipe) => !query || recipe.name.toLowerCase().includes(query));
  return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ recipes, total: recipes.length, page: 1, more: false }) });
};

// De notitie in de taal van de pagina; de recepten zijn die van een Nederlandse Mealie en blijven Nederlands.
const planFrom = (list) => (route, request) => {
  const lang = (request.headers()["accept-language"] || "").startsWith("nl") ? "nl" : "en";
  const local = (value) => (value && typeof value === "object" ? value[lang] : value);
  const body = list.map((item) => ({ ...item, title: local(item.title), text: local(item.text) }));
  return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
};
const mealPlan = planFrom(entries);

// De pasta pesto zoals Mealie hem geeft met de koppelingen tussen stappen en ingrediënten (sinds 4.14.0, issue #309): elk
// ingrediënt een referenceId, elke stap een id en zijn ingredientReferences. Met de slug kan de kookmodus koppelen.
// Stap 3 (de tomaatjes) heeft in Mealie alleen de kerstomaten; de olijfolie vinkt het scenario erbij.
const uuid = (n) => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const PESTO_LINKS = [[0], [3], [1], [2], [3, 4]]; // per stap de ingrediënten (index in de lijst hieronder)
const linkedPesto = (() => {
  const pesto = entries[0];
  const recipeIngredient = pesto.recipeDetail.recipeIngredient.map((item, at) => ({ ...item, referenceId: uuid(at + 1) }));
  const recipeInstructions = PESTO.recipeInstructions.map((step, at) => ({
    ...step,
    id: uuid(101 + at),
    ingredientReferences: PESTO_LINKS[at].map((index) => ({ referenceId: uuid(index + 1) })),
  }));
  const slug = slugOf(pesto.title);
  return { ...pesto, recipe: { ...pesto.recipe, slug }, recipeDetail: { ...pesto.recipeDetail, slug, recipeIngredient, recipeInstructions } };
})();
const linkedPlan = planFrom([linkedPesto, ...entries.slice(1)]);

const planRoutes = {
  "/api/mealplan*": mealPlan,
  "/api/mealplan/options": { entryTypes: ["breakfast", "lunch", "dinner", "side", "snack", "drink", "dessert"] },
  "/api/mealie/recipes*": mealieRecipes,
};

module.exports = [
  {
    name: "plan",
    routes: planRoutes,
    run: async (page) => {
      await page.click("#load-plan").catch(() => {});
      await page.waitForTimeout(900);
    },
  },
  {
    // Toevoegen bij een dag: de dag, de maaltijd, Recept of Notitie, en de receptkiezer met de recepten uit Mealie en Verras me.
    name: "plan-add",
    routes: planRoutes,
    run: async (page) => {
      await page.waitForSelector(`[data-plan-add="${WEEK[4]}"]`);
      await page.click(`[data-plan-add="${WEEK[4]}"]`);
      await page.waitForSelector("#plan-recipe-results .plan-recipe");
      await page.evaluate(() => document.activeElement && document.activeElement.blur());
      await page.waitForTimeout(400);
    },
  },
  {
    // Een tik op een maaltijd opent het recept: de ingrediënten voor de geplande porties, de bereiding, Open in Mealie en Wijzigen.
    name: "meal-recipe",
    routes: planRoutes,
    run: async (page, { device }) => {
      await page.waitForSelector("[data-plan-view]");
      await page.click("[data-plan-view]");
      await page.waitForSelector("#meal-recipe-dialog[open]");
      await page.evaluate(() => document.activeElement && document.activeElement.blur());
      // Op het bureaublad vult de foto het venster: scrol naar de maaltijd, zodat ook de ingrediënten in beeld zijn.
      if (device === "desktop") {
        await page.evaluate(() => {
          const dialog = document.getElementById("meal-recipe-dialog");
          dialog.scrollTop = document.getElementById("meal-recipe-type").offsetTop - 24;
        });
      }
      await page.waitForTimeout(400);
    },
  },
  {
    // Koppelen in de kookmodus (sinds 4.14.0): bij een recept uit Mealie vink je aan welke ingrediënten bij een stap horen, en
    // Opslaan schrijft dat meteen naar Mealie. De eerste twee stappen zijn klaar, dus de kookmodus opent op stap 3 (de
    // tomaatjes, met de knop voor een timer van 3 minuten). Koppelen opent de lijst van alle ingrediënten: de kerstomaten staan
    // al aangevinkt (zo staat het in Mealie), de olijfolie vinkt het scenario erbij. Er wordt niets opgeslagen.
    name: "cook-link",
    routes: { ...planRoutes, "/api/mealplan*": linkedPlan },
    run: async (page, { device }) => {
      await page.waitForSelector("[data-plan-view]");
      await page.click("[data-plan-view]");
      await page.waitForSelector("#meal-recipe-dialog[open] [data-cook-start]");
      await page.click('#meal-recipe-dialog [data-cook-done="0"]');
      await page.click('#meal-recipe-dialog [data-cook-done="1"]');
      await page.click("#meal-recipe-dialog [data-cook-start]");
      await page.waitForSelector("#cook-mode[open]");
      await page.click("#cook-mode-link");
      await page.waitForSelector("#cook-mode-link-form:not([hidden])");
      await page.check(`#cook-mode-link-items [data-cook-link-ref][value="${uuid(6)}"]`);
      // Het vinkje scrolde de stap. Op de telefoon terug naar boven (de stap, de vraag en de lijst passen); op het bureaublad
      // past de grote tekst van de stap er niet bij, dus daar staat het blok Bij deze stap bovenaan, met Opslaan in beeld.
      await page.evaluate((desktop) => {
        document.activeElement && document.activeElement.blur();
        const stage = document.getElementById("cook-mode-stage");
        const uses = document.getElementById("cook-mode-uses");
        stage.scrollTop = desktop ? uses.getBoundingClientRect().top - stage.getBoundingClientRect().top + stage.scrollTop - 12 : 0;
      }, device === "desktop");
    },
  },
];

module.exports.MEALIE_RECIPES = MEALIE_RECIPES;
module.exports.mealieRecipes = mealieRecipes;
