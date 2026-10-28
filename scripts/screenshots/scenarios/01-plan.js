const { DISHES, WEEK } = require("../demo");

const entry = (day, type, name, image, ingredients) => ({
  date: WEEK[day], entryType: type, imageUrl: image, title: name, recipe: { name },
  recipeDetail: { name, recipeIngredient: ingredients.map((display) => ({ display })) },
});

const entries = [
  entry(0, "dinner", "Pasta pesto met kerstomaatjes", DISHES.pesto, ["400 g spaghetti", "250 g kerstomaten", "200 g groene pesto", "80 g pijnboompitten"]),
  entry(1, "dinner", "Boerenkoolstamppot met rookworst", DISHES.stamppot, ["1 kg kruimige aardappelen", "500 g boerenkool", "1 rookworst"]),
  entry(2, "dinner", "Tomatensoep met balletjes", DISHES.soup, ["1 blik tomatenblokjes", "300 g gehakt", "1 ui"]),
  entry(3, "dinner", "Wraps met kip en avocado", DISHES.wrap, ["4 tortilla wraps", "300 g kipfilet", "2 avocado's"]),
  entry(4, "dinner", "Curry van kikkererwten", DISHES.curry, ["1 blik kikkererwten", "400 ml kokosmelk"]),
  entry(5, "lunch", "Pannenkoeken met appel en kaneel", DISHES.pancake, ["250 g bloem", "3 eieren", "2 appels"]),
  entry(6, "dinner", "Lasagne met spinazie", DISHES.lasagna, ["250 g lasagnebladen", "300 g spinazie"]),
];

module.exports = [
  {
    name: "plan",
    routes: { "/api/mealplan*": entries },
    run: async (page) => {
      await page.click("#load-plan").catch(() => {});
      await page.waitForTimeout(900);
    },
  },
];
