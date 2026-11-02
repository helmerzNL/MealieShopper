const { DISHES, WEEK } = require("../demo");

// Een maaltijd zoals /api/mealplan die geeft: met het id (dan is ze aan te tikken om te wijzigen) en de porties uit de planner
// van Mealie (`servings`, zie mealie.py): `base` uit het recept, `scale` uit de planner en `planned` = base x scale.
let nextId = 101;
const entry = (day, type, name, image, ingredients, base = 4, scale = 1) => ({
  id: nextId++, date: WEEK[day], entryType: type, imageUrl: image, title: name, recipeId: `r-${nextId}`, recipe: { name },
  recipeDetail: { name, recipeIngredient: ingredients.map((display) => ({ display })) },
  servings: { scale, base, planned: base * scale },
});
// Een notitie zonder recept, zoals "Uit eten".
const note = (day, type, title, text) => ({ id: nextId++, date: WEEK[day], entryType: type, title, text, recipe: null, recipeId: null });

const entries = [
  entry(0, "dinner", "Pasta pesto met kerstomaatjes", DISHES.pesto, ["400 g spaghetti", "250 g kerstomaten", "200 g groene pesto", "80 g pijnboompitten"]),
  entry(1, "dinner", "Boerenkoolstamppot met rookworst", DISHES.stamppot, ["1 kg kruimige aardappelen", "500 g boerenkool", "1 rookworst"], 4, 1.5),
  entry(2, "dinner", "Tomatensoep met balletjes", DISHES.soup, ["1 blik tomatenblokjes", "300 g gehakt", "1 ui"], 4, 0.5),
  entry(3, "dinner", "Wraps met kip en avocado", DISHES.wrap, ["4 tortilla wraps", "300 g kipfilet", "2 avocado's"]),
  note(4, "dinner", { nl: "Uit eten", en: "Eating out" }, { nl: "Verjaardag van oma", en: "Grandma's birthday" }),
  entry(5, "lunch", "Pannenkoeken met appel en kaneel", DISHES.pancake, ["250 g bloem", "3 eieren", "2 appels"], 4, 0.5),
  entry(6, "dinner", "Lasagne met spinazie", DISHES.lasagna, ["250 g lasagnebladen", "300 g spinazie"], 6),
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
const mealPlan = (route, request) => {
  const lang = (request.headers()["accept-language"] || "").startsWith("nl") ? "nl" : "en";
  const local = (value) => (value && typeof value === "object" ? value[lang] : value);
  const body = entries.map((item) => ({ ...item, title: local(item.title), text: local(item.text) }));
  return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
};

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
];

module.exports.MEALIE_RECIPES = MEALIE_RECIPES;
module.exports.mealieRecipes = mealieRecipes;
