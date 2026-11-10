// ---- De kookweergave van een recept: ingrediënten en bereiding zo dat je er tijdens het koken mee werkt
// Alle schermen die een recept tonen gebruiken renderCookView: het detail bij Recepten zoeken, de winkels en de maaltijdboxen
// (renderStoreRecipeDetail in recipe_search.js) en het venster van een maaltijd in het weekmenu (renderMealRecipeBody in
// app.js). Eén model voor beide bronnen (cookRecipeFromStore en cookRecipeFromMealie):
//   { key, title, slug, linkable, ingredients: [{ text, amount, section, ref }], steps: [{ text, title, id, refs, uses, auto }] }
// `slug`, `ref`, `id` en `refs` komen van Mealie (koppelingen tussen stappen en ingrediënten); `uses` zijn de ingrediënten bij
// een stap en `auto` zegt dat ze in de tekst zijn gevonden (cookLinkSteps, issue #309).
// De ingrediënten vink je af, de stappen markeer je als klaar; de eerste open stap is dan "Nu". Die voortgang staat per
// recept in localStorage onder ms-cook-progress:<key> en vervalt 12 uur na het laatste vinkje, of als het recept een ander
// aantal regels kreeg. Een recept uit Mealie heeft in het weekmenu en bij Recepten zoeken dezelfde sleutel (mealie:<slug>),
// dus dezelfde voortgang. "Start met koken" opent de kookmodus (#cook-mode in index.html): één stap tegelijk, groot.
// "Scherm aan laten" vraagt een Screen Wake Lock zolang er een recept in beeld is; de keuze staat onder ms-cook-wake en is
// standaard uit. Laadt direct na app.js (gebruikt $, t, escapeHtml en mdiIcon).
// De kookmodus is uit te zetten (Instellingen, Weergave, Voorkeuren; THEME in theme.js): dan verbergt styles.css de balk met
// Start met koken en de vinkjes bij de ingrediënten (html.no-cook-mode), doen die vinkjes niets en blijft het scherm niet aan.

// Material Design Icons (zie static/icons/LICENSE-mdi.txt)
const MDI_CHEF_HAT = "M12.5,1.5C10.73,1.5 9.17,2.67 8.67,4.37C8.14,4.13 7.58,4 7,4A4,4 0 0,0 3,8C3,9.82 4.24,11.41 6,11.87V19H19V11.87C20.76,11.41 22,9.82 22,8A4,4 0 0,0 18,4C17.42,4 16.86,4.13 16.33,4.37C15.83,2.67 14.27,1.5 12.5,1.5M12,10.5H13V17.5H12V10.5M9,12.5H10V17.5H9V12.5M15,12.5H16V17.5H15V12.5M6,20V21A1,1 0 0,0 7,22H18A1,1 0 0,0 19,21V20H6Z";
const MDI_RESTART = "M12,4C14.1,4 16.1,4.8 17.6,6.3C20.7,9.4 20.7,14.5 17.6,17.6C15.8,19.5 13.3,20.2 10.9,19.9L11.4,17.9C13.1,18.1 14.9,17.5 16.2,16.2C18.5,13.9 18.5,10.1 16.2,7.7C15.1,6.6 13.5,6 12,6V10.6L7,5.6L12,0.6V4M6.3,17.6C3.7,15 3.3,11 5.1,7.9L6.6,9.4C5.5,11.6 5.9,14.4 7.8,16.2C8.3,16.7 8.9,17.1 9.6,17.4L9,19.4C8,19 7.1,18.4 6.3,17.6Z";
const MDI_CHECK = "M21,7L9,19L3.5,13.5L4.91,12.09L9,16.17L19.59,5.59L21,7Z";

const COOK_PROGRESS_PREFIX = "ms-cook-progress:";
const COOK_WAKE_KEY = "ms-cook-wake";
const COOK_PROGRESS_TTL = 12 * 60 * 60 * 1000;

// Een aantal vooraan een regel, zoals _NUMBER in mealieshopper/ingredients.py: "1 1/2", "3/4", "1½", "½", "1,5" of "200".
const COOK_NUMBER = "(?:\\d+[ \\u00a0]+\\d+/\\d+|\\d+/\\d+|\\d+(?:[.,]\\d+)?[ \\u00a0]?[\\u00bd\\u00bc\\u00be\\u2153\\u2154\\u215b\\u215c\\u215d\\u215e]|[\\u00bd\\u00bc\\u00be\\u2153\\u2154\\u215b\\u215c\\u215d\\u215e]|\\d+(?:[.,]\\d+)?)";
// Met "ca." of "about" ervoor, een bereik ("2-3", "2 tot 3", "2 to 3") en een veelvoud ("2 x 400").
const COOK_AMOUNT_RE = new RegExp(`^(?:(?:ca\\.?|circa|ongeveer|about|approx\\.?|\\u00b1|~)\\s*)?${COOK_NUMBER}(?:\\s*(?:-|\\u2013|tot|to)\\s*${COOK_NUMBER})?(?:\\s*[x\\u00d7]\\s*${COOK_NUMBER})?`, "i");
// De eenheden die bij het aantal horen. Minstens die van ingredients.UNITS (tests/test_recipe_view.py bewaakt dat), plus wat
// Engelse en voluit geschreven eenheden.
const COOK_UNITS = new Set("g gr gram grams kg kilo kilogram kilograms mg ml milliliter milliliters millilitre millilitres cl centiliter dl deciliter l liter liters litre litres el eetlepel eetlepels tl theelepel theelepels kl koffielepel koffielepels st stuk stuks snufje snufjes snuf mespunt mespuntje teen tenen teentje teentjes takje takjes tak takken bosje bosjes bos blik blikje blikjes blikken pak pakje pakjes pakken zak zakje zakjes plak plakje plakjes plakken schijf schijfje schijfjes schijven bol bolletje bolletjes kop kopje kopjes handje handjes handvol scheut scheutje scheutjes klont klontje klontjes rol rolletje pot potje potjes bak bakje bakjes stengel stengels steel stelen blad blaadjes blaadje bundel bundeltje doos doosje fles flesje pond ons cup cups oz ounce ounces lb lbs pound pounds tbsp tbsps tablespoon tablespoons tsp tsps teaspoon teaspoons pinch pinches clove cloves can cans slice slices bunch bunches sprig sprigs pint pints quart quarts stick sticks handful handfuls dash dashes".split(" "));
// Een maat zonder getal: "snufje zout", "een snufje zout", "a pinch of salt".
const COOK_BARE_RE = /^(?:(?:een|a|an)\s+)?(?:snufje|snufjes|snuf|mespunt|mespuntje|scheut|scheutje|scheutjes|handje|handjes|handvol|pinch|dash|handful|splash)(?=\s)/i;

const cookViews = new Map(); // weergave -> model; een weergave is één keer renderCookView (data-cook-view)
const cookProgressCache = new Map(); // sleutel -> voortgang, ook als localStorage niet werkt
const cookWake = { lock: null, busy: false, again: false, wanted: null };
const cookMode = { view: 0, key: "", index: 0, opener: null, swipe: null, linking: -1, saving: false };
let cookViewSeq = 0;

// Staat de kookmodus aan? Zonder theme.js (de tests in Node) wel.
function cookEnabled() {
  return typeof THEME === "undefined" || THEME.appearance().cookingMode !== false;
}

function cookText(value) {
  return String(value ?? "").replace(/\s+/g, " ").trim();
}

function cookFormat(value) {
  return typeof I18N !== "undefined" ? I18N.formatNumber(value) : String(value);
}

// Het aantal en de eenheid vooraan een regel ("400 g", "1½ el", "2-3 teentjes", "snufje"), zodat ze vet kunnen; "" als de
// regel niet zo begint ("zout naar smaak", "7-up", "00-meel") of als er na het aantal geen ingrediënt staat ("400 g").
// `units` zijn de namen van de eenheid uit Mealie, voor eenheden die niet in COOK_UNITS staan ("fl oz").
function cookSplitAmount(text, units = []) {
  const line = String(text || "");
  const match = COOK_AMOUNT_RE.exec(line);
  if (!match) {
    const bare = COOK_BARE_RE.exec(line);
    return bare && line.slice(bare[0].length).trim() ? bare[0] : "";
  }
  let amount = match[0];
  let rest = line.slice(amount.length);
  const glued = !/^\s/.test(rest);
  if (rest && glued && !/^\p{L}/u.test(rest)) return ""; // "7-up", "100% sap", "5-kruidenpoeder"
  const spaced = rest.trimStart();
  const space = rest.slice(0, rest.length - spaced.length);
  const hint = units
    .map((unit) => cookText(unit))
    .filter(Boolean)
    .sort((a, b) => b.length - a.length)
    .find((unit) => spaced.toLowerCase().startsWith(unit.toLowerCase()) && /^(?:$|[\s,;.(])/.test(spaced.slice(unit.length)));
  const word = /^(\p{L}+)\.?(?=$|[\s,;(])/u.exec(spaced);
  if (hint) {
    const dot = spaced.slice(hint.length).startsWith(".") ? "." : "";
    amount += space + spaced.slice(0, hint.length) + dot;
  } else if (word && COOK_UNITS.has(word[1].toLowerCase())) {
    amount += space + word[0];
  } else if (glued) {
    return ""; // "4personen": geen eenheid die we kennen
  }
  return line.slice(amount.length).trim() ? amount : "";
}

// Een referenceId of id van Mealie (een UUID), klein geschreven; "" als het er geen is.
function cookRef(value) {
  const text = String(value ?? "").trim().toLowerCase();
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(text) ? text : "";
}

function cookIngredient(text, section = "", units = [], ref = "") {
  const line = cookText(text);
  return { text: line, amount: cookSplitAmount(line, units), section: cookText(section), ref: cookRef(ref) };
}

// Een stap. `id` en `refs` komen van Mealie: het id van de stap en de referenceId's van de ingrediënten die eraan gekoppeld
// zijn (issue #309). cookLinkSteps zet daarna `uses` (de ingrediënten bij deze stap, als index) en `auto` (gevonden in de tekst).
function cookStep(text, title = "", id = "", refs = []) {
  const linked = [...new Set((Array.isArray(refs) ? refs : []).map(cookRef).filter(Boolean))];
  return { text: String(text ?? "").trim(), title: cookText(title), id: cookRef(id), refs: linked, uses: [], auto: false };
}

// Een recept uit het detailscherm (/api/<winkel>/recipes/<id>): ingredients [{line, section?, ref?}], steps [tekst] en eventueel
// stepTitles, stepIds en stepRefs (Mealie, even lang als steps). Een sectie op een lege regel schuift door naar de volgende regel.
function cookRecipeFromStore(store, recipe) {
  const titles = Array.isArray(recipe?.stepTitles) ? recipe.stepTitles : [];
  const ids = Array.isArray(recipe?.stepIds) ? recipe.stepIds : [];
  const refs = Array.isArray(recipe?.stepRefs) ? recipe.stepRefs : [];
  const ingredients = [];
  let section = "";
  for (const item of recipe?.ingredients || []) {
    const line = typeof item === "string" ? item : item?.line;
    section = cookText(typeof item === "object" ? item?.section : "") || section;
    const ingredient = cookIngredient(line, section, [], typeof item === "object" ? item?.ref : "");
    if (!ingredient.text) continue;
    ingredients.push(ingredient);
    section = "";
  }
  const steps = (recipe?.steps || [])
    .map((step, index) =>
      typeof step === "string"
        ? cookStep(step, titles[index], ids[index], refs[index])
        : cookStep(step?.text, step?.title ?? titles[index], ids[index], refs[index]))
    .filter((step) => step.text);
  const slug = store === "mealie" ? cookText(recipe?.slug || recipe?.id) : "";
  return cookLinkSteps({ key: `${store}:${recipe?.id ?? recipe?.slug ?? ""}`, title: String(recipe?.name || ""), slug, ingredients, steps });
}

// Een recept zoals Mealie het geeft (recipeDetail van een maaltijd, al omgerekend naar de porties). Dezelfde regels als
// mealie.recipe_detail: een regel zonder tekst valt weg en geeft zijn kopje (title) door; een lege stap valt weg.
function cookRecipeFromMealie(detail, key, title) {
  const ingredients = [];
  let section = "";
  for (const item of detail?.recipeIngredient || []) {
    if (!item || typeof item !== "object") continue;
    section = cookText(item.title) || section;
    const unit = item.unit && typeof item.unit === "object" ? item.unit : {};
    const ingredient = cookIngredient(item.display || item.note, section, [unit.name, unit.abbreviation, unit.pluralName, unit.pluralAbbreviation], item.referenceId);
    if (!ingredient.text) continue;
    ingredients.push(ingredient);
    section = "";
  }
  const steps = (detail?.recipeInstructions || [])
    .filter((step) => step && typeof step === "object")
    .map((step) => cookStep(step.text, step.title, step.id, (Array.isArray(step.ingredientReferences) ? step.ingredientReferences : []).map((item) => item?.referenceId)))
    .filter((step) => step.text);
  const slug = String(key || "").startsWith("mealie:") ? String(key).slice("mealie:".length) : "";
  return cookLinkSteps({ key, title: String(title || detail?.name || ""), slug, ingredients, steps });
}

// ---- Ingrediënten bij een stap (issue #309)
// Een stap met koppelingen in Mealie toont die ingrediënten. Zonder koppelingen zoekt de app ze in de tekst van de stap
// ("snijd de ui" vindt "1 rode ui, gesnipperd"); dat staat er dan bij als "automatisch gevonden". Een recept uit Mealie met
// id's en referenceId's kan per stap gekoppeld worden (cookLinkable); dat gaat meteen naar Mealie (cookSaveLinks).
// Wat in deze pagina is opgeslagen, onthoudt cookLinkOverrides, zodat een recept dat later opnieuw wordt opgebouwd
// (het weekmenu heeft nog het recept van bij het laden) de nieuwe koppelingen toont.
const cookLinkOverrides = new Map(); // "<slug> <stap-id>" -> referenceId's

function cookLinkSteps(recipe) {
  const byRef = new Map();
  recipe.ingredients.forEach((item, index) => {
    if (item.ref && !byRef.has(item.ref)) byRef.set(item.ref, index);
  });
  for (const step of recipe.steps) {
    const saved = recipe.slug && step.id ? cookLinkOverrides.get(`${recipe.slug} ${step.id}`) : undefined;
    if (saved) step.refs = [...saved];
    const linked = [...new Set(step.refs.map((ref) => byRef.get(ref)).filter((index) => index !== undefined))].sort((a, b) => a - b);
    step.uses = linked.length ? linked : cookMatchStep(recipe.ingredients, step);
    step.auto = !linked.length && step.uses.length > 0;
  }
  recipe.linkable = Boolean(recipe.slug) && byRef.size > 0;
  return recipe;
}

// De ingrediënten bij een stap (indexen); een model zonder `uses` (ouder of nagemaakt) heeft er geen.
function cookUses(step) {
  return Array.isArray(step?.uses) ? step.uses : [];
}

function cookLinkable(recipe, step) {
  return Boolean(recipe?.linkable && step?.id);
}

// Woorden die nooit het ingrediënt zelf zijn: lidwoorden, voorzetsels, "naar smaak" en hoe het gesneden is.
const COOK_MATCH_SKIP = new Set("de het een van met voor en of in op aan uit bij tot te naar smaak eventueel optioneel extra vers verse gedroogd gedroogde fijn fijne fijngehakt fijngehakte gehakte gesneden gesnipperd gesnipperde geraspt geraspte grof grove klein kleine groot grote middelgrote halve hele the a an of and or for with to in on at into from taste optional fresh dried chopped minced sliced diced grated finely large small medium whole".split(" "));

function cookFold(text) {
  return String(text ?? "").toLowerCase().normalize("NFD").replace(/\p{M}/gu, "");
}

function cookWords(text) {
  return cookFold(text).match(/\p{L}+(?:['’]\p{L}+)*/gu) || [];
}

// De stam van een woord, zodat meervoud en verkleinwoord hetzelfde zijn: "uien" en "ui", "tomaten" en "tomaat", "eieren"
// en "ei", "kippen" en "kip", "boontjes" en "bonen", "paprika's" en "paprika", "onions" en "onion", "olives" en "olive".
function cookStem(word) {
  let stem = cookFold(word).replace(/['’]s$/, "");
  for (const end of ["tjes", "tje", "jes", "je", "eren", "en", "s"]) {
    if (stem.length - end.length >= 2 && stem.endsWith(end)) {
      stem = stem.slice(0, -end.length);
      break;
    }
  }
  stem = stem.replace(/([^aeiou])\1$/, "$1").replace(/([aeiou])\1/g, "$1");
  return stem.length > 3 ? stem.replace(/e$/, "") : stem; // "olive" en "olives", "tomato" en "tomatoes"
}

// Waar een regel om gaat: het laatste woord voor een komma of haakje, zonder de hoeveelheid ("2 tenen knoflook, fijngehakt"
// is knoflook). "zout en peper naar smaak" gaat om zout en om peper.
function cookIngredientStems(item) {
  const name = item.text.slice(item.amount.length).replace(/\([^)]*\)/g, " ").split(/[,;:]/)[0];
  const stems = [];
  for (const part of name.split(/\s+(?:en|of|and|or)\s+|\s*[&/+]\s*/i)) {
    const words = cookWords(part).filter((word) => !COOK_MATCH_SKIP.has(word) && !COOK_UNITS.has(word));
    const stem = words.length ? cookStem(words[words.length - 1]) : "";
    if (stem.length >= 2 && !stems.includes(stem)) stems.push(stem);
  }
  return stems;
}

// Hoort een woord uit de stap bij een ingrediënt? Als de stammen gelijk zijn, of als de ene samenstelling op de andere
// eindigt: "olijfolie" bij olie, "rundergehakt" bij gehakt, "kookroom" bij room. Niet op het begin: "tomaat" is geen
// "tomatenblokjes". De kortste stam heeft dan minstens 3 letters ("ui" moet gelijk zijn).
function cookStemsMatch(ingredient, word) {
  if (ingredient === word) return true;
  const [short, long] = ingredient.length < word.length ? [ingredient, word] : [word, ingredient];
  return short.length >= 3 && long.endsWith(short);
}

function cookMatchStep(ingredients, step) {
  const words = [...new Set(cookWords(`${step.title} ${step.text}`).filter((word) => !COOK_MATCH_SKIP.has(word)).map(cookStem))].filter((stem) => stem.length >= 2);
  if (!words.length) return [];
  const found = [];
  ingredients.forEach((item, index) => {
    if (cookIngredientStems(item).some((stem) => words.some((word) => cookStemsMatch(stem, word)))) found.push(index);
  });
  return found;
}

// ---- Voortgang
function cookEmptyProgress(recipe) {
  return { at: 0, n: [recipe.ingredients.length, recipe.steps.length], ingredients: new Set(), steps: new Set() };
}

function cookProgressFits(progress, recipe, now = Date.now()) {
  if (!progress || !Array.isArray(progress.n)) return false;
  if (progress.n[0] !== recipe.ingredients.length || progress.n[1] !== recipe.steps.length) return false;
  return !progress.at || (now - progress.at < COOK_PROGRESS_TTL && progress.at <= now + 60000);
}

function cookLoad(recipe, now = Date.now()) {
  try {
    const raw = localStorage.getItem(COOK_PROGRESS_PREFIX + recipe.key);
    if (!raw) return cookEmptyProgress(recipe);
    const data = JSON.parse(raw);
    if (!data || !Number.isFinite(data.at) || !data.at) return cookEmptyProgress(recipe);
    if (!cookProgressFits({ at: data.at, n: data.n }, recipe, now)) return cookEmptyProgress(recipe);
    const pick = (list, size) => new Set((Array.isArray(list) ? list : []).filter((index) => Number.isInteger(index) && index >= 0 && index < size));
    return { at: data.at, n: [recipe.ingredients.length, recipe.steps.length], ingredients: pick(data.i, recipe.ingredients.length), steps: pick(data.s, recipe.steps.length) };
  } catch {
    return cookEmptyProgress(recipe); // geen opslag of een kapotte stand: dan begin je opnieuw
  }
}

function cookSave(recipe, progress) {
  const key = COOK_PROGRESS_PREFIX + recipe.key;
  try {
    if (!progress.ingredients.size && !progress.steps.size) {
      localStorage.removeItem(key);
      return;
    }
    const sorted = (set) => [...set].sort((a, b) => a - b);
    localStorage.setItem(key, JSON.stringify({ at: progress.at, n: progress.n, i: sorted(progress.ingredients), s: sorted(progress.steps) }));
  } catch {
    // Geen opslag (privévenster, vol): de voortgang blijft dan alleen tot je de pagina herlaadt.
  }
}

// Verlopen en kapotte standen opruimen, eenmaal bij het laden.
function cookPrune(now = Date.now()) {
  try {
    const keys = [];
    for (let index = 0; index < localStorage.length; index += 1) {
      const key = localStorage.key(index);
      if (key && key.startsWith(COOK_PROGRESS_PREFIX)) keys.push(key);
    }
    for (const key of keys) {
      let data = null;
      try {
        data = JSON.parse(localStorage.getItem(key));
      } catch {
        data = null;
      }
      if (!data || !Number.isFinite(data.at) || now - data.at >= COOK_PROGRESS_TTL) localStorage.removeItem(key);
    }
  } catch {
    // Geen opslag: niets op te ruimen.
  }
}

function cookProgress(recipe) {
  const cached = cookProgressCache.get(recipe.key);
  if (cookProgressFits(cached, recipe)) return cached;
  const loaded = cookLoad(recipe);
  cookProgressCache.set(recipe.key, loaded);
  return loaded;
}

function cookStarted(progress) {
  return progress.ingredients.size > 0 || progress.steps.size > 0;
}

// De stap waar je bent: de eerste die nog niet klaar is. Pas als je begonnen bent (iets afgevinkt), anders staat er bij
// het rondkijken al "Nu" bij stap 1; -1 als alles klaar is.
function cookCurrentStep(recipe, progress) {
  if (!cookStarted(progress)) return -1;
  return recipe.steps.findIndex((_, index) => !progress.steps.has(index));
}

function cookProgressText(recipe, progress) {
  const total = recipe.steps.length;
  const done = progress.steps.size;
  if (total && done >= total) return t("cook.progress_all");
  return t("cook.progress", { done: cookFormat(done), total: cookFormat(total) });
}

function cookTickedText(recipe, progress) {
  return t("cook.ticked", { done: cookFormat(progress.ingredients.size), total: cookFormat(recipe.ingredients.length) });
}

function cookPercent(recipe, progress) {
  return recipe.steps.length ? Math.round((progress.steps.size / recipe.steps.length) * 100) : 0;
}

function cookToggle(set, index, on = !set.has(index)) {
  if (on) set.add(index);
  else set.delete(index);
}

// ---- Weergave
function cookWakeSupported() {
  return typeof navigator !== "undefined" && "wakeLock" in navigator;
}

function cookWakeWanted() {
  if (cookWake.wanted !== null) return cookWake.wanted;
  try {
    cookWake.wanted = localStorage.getItem(COOK_WAKE_KEY) === "1";
  } catch {
    cookWake.wanted = false;
  }
  return cookWake.wanted;
}

function cookWakeRemember(on) {
  cookWake.wanted = Boolean(on);
  try {
    localStorage.setItem(COOK_WAKE_KEY, on ? "1" : "0");
  } catch {
    // Geen opslag: de keuze geldt dan tot je de pagina herlaadt.
  }
}

function cookHeading(level, className, html) {
  const tag = `h${Math.min(Math.max(level, 2), 6)}`;
  return `<${tag} class="${className}">${html}</${tag}>`;
}

function cookWakeHtml() {
  return `<label class="switch-row cook__wake" data-cook-wake-row title="${escapeHtml(t("cook.wake_hint"))}" data-i18n-attr="title:cook.wake_hint"${cookWakeSupported() ? "" : " hidden"}>
      <input type="checkbox" role="switch" data-cook-wake${cookWakeWanted() ? " checked" : ""}>
      <span data-i18n="cook.wake">${escapeHtml(t("cook.wake"))}</span>
    </label>`;
}

function cookBarHtml(recipe, progress) {
  const steps = recipe.steps.length > 0;
  if (!steps && !recipe.ingredients.length) return "";
  return `<div class="cook__bar">
    ${steps ? `<button class="button cook__start" type="button" data-cook-start>${mdiIcon(MDI_CHEF_HAT)}<span data-i18n="cook.start">${escapeHtml(t("cook.start"))}</span></button>` : ""}
    ${steps ? `<div class="cook__progress">
      <span class="cook__status" role="status" data-cook-status>${escapeHtml(cookProgressText(recipe, progress))}</span>
      <span class="cook__meter" aria-hidden="true"><i data-cook-meter style="width: ${cookPercent(recipe, progress)}%"></i></span>
    </div>` : ""}
    <button class="button button--secondary cook__reset" type="button" data-cook-reset${cookStarted(progress) ? "" : " hidden"}>${mdiIcon(MDI_RESTART)}<span data-i18n="cook.reset">${escapeHtml(t("cook.reset"))}</span></button>
    ${cookWakeHtml()}
  </div>`;
}

function cookIngredientHtml(item, index, checked) {
  const text = item.amount
    ? `<span class="cook-ingredient__amount">${escapeHtml(item.amount)}</span>${escapeHtml(item.text.slice(item.amount.length))}`
    : escapeHtml(item.text);
  const off = cookEnabled() ? "" : " disabled"; // kookmodus uit: de regel is gewone tekst, een tik vinkt niets af
  return `<li class="cook-ingredient"><label><input type="checkbox" data-cook-tick="${index}"${checked ? " checked" : ""}${off}><span class="cook-ingredient__text">${text}</span></label></li>`;
}

// De ingrediënten, per sectie een eigen lijst met een kopje. Ook in het paneel van de kookmodus.
function cookIngredientsHtml(recipe, progress, level) {
  const groups = [];
  recipe.ingredients.forEach((item, index) => {
    if (!groups.length || item.section) groups.push({ section: item.section, items: [] });
    groups[groups.length - 1].items.push({ item, index });
  });
  return groups
    .map(({ section, items }) => `${section ? cookHeading(level, "cook__section", escapeHtml(section)) : ""}<ul class="cook-ingredients" role="list">${items
      .map(({ item, index }) => cookIngredientHtml(item, index, progress.ingredients.has(index)))
      .join("")}</ul>`)
    .join("");
}

function cookStepsHtml(recipe, progress, level) {
  const current = cookCurrentStep(recipe, progress);
  const items = recipe.steps.map((step, index) => {
    const done = progress.steps.has(index);
    const now = index === current;
    return `<li class="cook-step${done ? " is-done" : ""}${now ? " is-current" : ""}" data-cook-step="${index}"${now ? ' aria-current="step"' : ""}>
        <button class="cook-step__toggle" type="button" data-cook-done="${index}" aria-pressed="${done}" aria-label="${escapeHtml(t("cook.step_toggle", { n: cookFormat(index + 1) }))}"><span class="cook-step__number" aria-hidden="true">${escapeHtml(cookFormat(index + 1))}</span>${mdiIcon(MDI_CHECK, "mdi cook-step__check")}</button>
        <div class="cook-step__body">
          <span class="cook-step__now" data-i18n="cook.now">${escapeHtml(t("cook.now"))}</span>
          ${step.title ? cookHeading(level, "cook-step__title", escapeHtml(step.title)) : ""}
          <p class="cook-step__text">${escapeHtml(step.text)}</p>
          <p class="cook-step__uses" data-cook-uses="${index}"${cookUses(step).length ? "" : " hidden"}>${cookUsesHtml(recipe, step)}</p>
        </div>
      </li>`;
  });
  return `<ol class="cook-steps" role="list">${items.join("")}</ol>`;
}

// De ingrediënten bij een stap in de lijst met stappen, kort: "400 g spaghetti · 1 bosje basilicum".
function cookUsesHtml(recipe, step) {
  if (!cookUses(step).length) return "";
  const items = cookUses(step).map((index) => {
    const item = recipe.ingredients[index];
    if (!item) return "";
    const text = item.amount ? `<b>${escapeHtml(item.amount)}</b>${escapeHtml(item.text.slice(item.amount.length))}` : escapeHtml(item.text);
    return `<span class="cook-step__use">${text}</span>`;
  });
  const label = t(step.auto ? "cook.uses_found" : "cook.uses");
  return `<span class="visually-hidden">${escapeHtml(label)}: </span>${items.filter(Boolean).join('<span aria-hidden="true"> · </span>')}`;
}

// De ingrediënten bij de stap in de kookmodus: met vinkjes, gelijk met de lijst van alle ingrediënten (data-cook-tick).
function cookModeUsesHtml(recipe, step, progress) {
  return cookUses(step)
    .filter((index) => recipe.ingredients[index])
    .map((index) => cookIngredientHtml(recipe.ingredients[index], index, progress.ingredients.has(index)))
    .join("");
}

// Het formulier om ingrediënten aan een stap te koppelen: alle ingrediënten met een referenceId, per sectie, met de
// ingrediënten die er nu bij staan (ook die automatisch gevonden zijn) al aangevinkt.
function cookLinkFormHtml(recipe, step) {
  const chosen = new Set(cookUses(step));
  return recipe.ingredients
    .map((item, index) => {
      if (!item.ref) return "";
      const heading = item.section ? `<p class="cook-link__section">${escapeHtml(item.section)}</p>` : "";
      const text = item.amount ? `<b>${escapeHtml(item.amount)}</b>${escapeHtml(item.text.slice(item.amount.length))}` : escapeHtml(item.text);
      return `${heading}<label class="cook-link__item"><input type="checkbox" value="${escapeHtml(item.ref)}" data-cook-link-ref${chosen.has(index) ? " checked" : ""}><span>${text}</span></label>`;
    })
    .join("");
}

// De weergaven die niet meer in de pagina staan vergeten (hun HTML is vervangen).
function cookPruneViews(keep) {
  if (typeof document === "undefined") return;
  for (const view of [...cookViews.keys()]) {
    if (view !== keep && !document.querySelector(`[data-cook-view="${view}"]`)) cookViews.delete(view);
  }
}

// De kookweergave als HTML: een balk (Start met koken, voortgang, Opnieuw beginnen, Scherm aan laten) en daaronder de
// ingrediënten naast de stappen. `headingLevel` is het niveau van de kopjes Ingrediënten en Bereiding.
function renderCookView(recipe, options = {}) {
  const level = Number(options.headingLevel) || 3;
  cookViewSeq += 1;
  const view = cookViewSeq;
  cookPruneViews(view);
  cookViews.set(view, recipe);
  const progress = cookProgress(recipe);
  const count = recipe.ingredients.length ? ` <span class="cook__count" data-cook-ticked>${escapeHtml(cookTickedText(recipe, progress))}</span>` : "";
  return `<div class="cook" data-cook-key="${escapeHtml(recipe.key)}" data-cook-view="${view}">
    ${cookBarHtml(recipe, progress)}
    <div class="cook__columns">
      <div class="cook__ingredients">
        ${cookHeading(level, "cook__heading", `<span data-i18n="rsearch.ingredients">${escapeHtml(t("rsearch.ingredients"))}</span>${count}`)}
        ${recipe.ingredients.length ? cookIngredientsHtml(recipe, progress, level + 1) : `<p class="muted">${escapeHtml(options.noIngredients || "")}</p>`}
      </div>
      <div class="cook__steps">
        ${cookHeading(level, "cook__heading", `<span data-i18n="rsearch.steps">${escapeHtml(t("rsearch.steps"))}</span>`)}
        ${recipe.steps.length ? cookStepsHtml(recipe, progress, level + 1) : `<p class="muted">${escapeHtml(options.noSteps || "")}</p>`}
        ${options.stepsNote ? `<p class="muted cook__note">${escapeHtml(options.stepsNote)}</p>` : ""}
      </div>
    </div>
  </div>`;
}

// ---- Bijwerken: alleen de toestand op de bestaande elementen, zonder de weergave opnieuw op te bouwen
function cookApply(root, recipe, progress) {
  const current = cookCurrentStep(recipe, progress);
  const enabled = cookEnabled();
  root.querySelectorAll("[data-cook-tick]").forEach((input) => {
    input.checked = progress.ingredients.has(Number(input.dataset.cookTick));
    input.disabled = !enabled;
  });
  root.querySelectorAll("[data-cook-step]").forEach((item) => {
    const index = Number(item.dataset.cookStep);
    const done = progress.steps.has(index);
    item.classList.toggle("is-done", done);
    item.classList.toggle("is-current", index === current);
    if (index === current) item.setAttribute("aria-current", "step");
    else item.removeAttribute("aria-current");
    const toggle = item.querySelector("[data-cook-done]");
    toggle?.setAttribute("aria-pressed", String(done));
    toggle?.setAttribute("aria-label", t("cook.step_toggle", { n: cookFormat(index + 1) }));
  });
  root.querySelectorAll("[data-cook-status]").forEach((element) => {
    element.textContent = cookProgressText(recipe, progress);
  });
  root.querySelectorAll("[data-cook-meter]").forEach((element) => {
    element.style.width = `${cookPercent(recipe, progress)}%`;
  });
  root.querySelectorAll("[data-cook-ticked]").forEach((element) => {
    element.textContent = cookTickedText(recipe, progress);
  });
  root.querySelectorAll("[data-cook-uses]").forEach((element) => {
    const step = recipe.steps[Number(element.dataset.cookUses)];
    element.innerHTML = step ? cookUsesHtml(recipe, step) : "";
    element.hidden = !cookUses(step).length;
  });
  root.querySelectorAll("[data-cook-reset]").forEach((button) => {
    button.hidden = !cookStarted(progress);
  });
  root.querySelectorAll("[data-cook-wake]").forEach((input) => {
    input.checked = cookWakeWanted();
  });
  root.querySelectorAll("[data-cook-wake-row]").forEach((row) => {
    row.hidden = !cookWakeSupported();
  });
}

function cookRecipeOf(root) {
  return cookViews.get(Number(root?.dataset.cookView)) || null;
}

function cookSync(key) {
  document.querySelectorAll("[data-cook-key]").forEach((root) => {
    const recipe = root.dataset.cookKey === key ? cookRecipeOf(root) : null;
    if (recipe) cookApply(root, recipe, cookProgress(recipe));
  });
  if (cookMode.key === key && $("#cook-mode")?.open) cookModeRender();
}

function cookSyncAll() {
  document.querySelectorAll("[data-cook-key]").forEach((root) => {
    const recipe = cookRecipeOf(root);
    if (recipe) cookApply(root, recipe, cookProgress(recipe));
  });
  if ($("#cook-mode")?.open) cookModeRender();
}

function cookChange(root, change) {
  const recipe = cookRecipeOf(root);
  if (!recipe) return;
  const progress = cookProgress(recipe);
  change(progress, recipe);
  progress.at = Date.now();
  cookSave(recipe, progress);
  cookSync(recipe.key);
}

// ---- Scherm aan laten (Screen Wake Lock). Alleen via HTTPS of localhost en in een browser die het kent; anders staat de
// schakelaar er niet. De browser laat de lock los als het tabblad verborgen raakt; bij terugkomen vragen we hem opnieuw.
function cookShowing() {
  if ($("#cook-mode")?.open) return true;
  return [...document.querySelectorAll("[data-cook-key]")].some((root) => root.getClientRects().length > 0);
}

function cookWakeShould() {
  return cookEnabled() && cookWakeSupported() && cookWakeWanted() && document.visibilityState === "visible" && cookShowing();
}

// Vraagt de lock aan of geeft hem vrij, naar cookWakeShould. Een aanroep terwijl een vorige nog loopt, zorgt voor een
// nieuwe ronde daarna: zo klopt de toestand ook als je net wegging terwijl de browser de lock nog gaf.
async function cookWakeUpdate() {
  if (cookWake.busy) {
    cookWake.again = true;
    return;
  }
  cookWake.busy = true;
  try {
    do {
      cookWake.again = false;
      try {
        const should = cookWakeShould();
        if (should && !cookWake.lock) {
          const lock = await navigator.wakeLock.request("screen");
          cookWake.lock = lock;
          lock.addEventListener?.("release", () => {
            if (cookWake.lock === lock) cookWake.lock = null;
          });
          if (!cookWakeShould()) cookWake.again = true;
        } else if (!should && cookWake.lock) {
          const lock = cookWake.lock;
          cookWake.lock = null;
          await lock.release();
        }
      } catch {
        // Geweigerd (batterijbesparing, geen toestemming): het scherm gaat dan gewoon uit, zoals altijd.
      }
    } while (cookWake.again);
  } finally {
    cookWake.busy = false;
  }
}

// ---- Kookmodus: één stap tegelijk, schermvullend
function cookModeRecipe() {
  return cookViews.get(cookMode.view) || null;
}

// Wat een knop doet. "next" (de knop) markeert de stap als klaar en gaat door; op de laatste stap rondt hij af en sluit.
// "forward" (pijltje, vegen) doet hetzelfde, maar sluit niet. "prev" bladert alleen terug.
function cookModeMove(index, total, action) {
  const last = Math.max(total - 1, 0);
  const at = Math.min(Math.max(index, 0), last);
  if (action === "prev") return { index: Math.max(at - 1, 0), markDone: false, close: false };
  if (action === "next" || action === "forward") {
    if (at < last) return { index: at + 1, markDone: true, close: false };
    return { index: last, markDone: true, close: action === "next" };
  }
  return { index: at, markDone: false, close: false };
}

function cookModePanel(open) {
  $("#cook-mode-panel").hidden = !open;
  $("#cook-mode-ingredients").setAttribute("aria-expanded", String(open));
  $("#cook-mode").classList.toggle("cook-mode--panel", open);
}

function cookModeRender() {
  const recipe = cookModeRecipe();
  if (!recipe || !recipe.steps.length) return;
  const progress = cookProgress(recipe);
  const total = recipe.steps.length;
  const index = Math.min(Math.max(cookMode.index, 0), total - 1);
  const step = recipe.steps[index];
  const done = progress.steps.has(index);
  const last = index === total - 1;
  $("#cook-mode-title").textContent = recipe.title;
  $("#cook-mode-counter").textContent = t("cook.step", { n: cookFormat(index + 1), total: cookFormat(total) });
  const title = $("#cook-mode-step-title");
  title.textContent = step.title;
  title.hidden = !step.title;
  $("#cook-mode-text").textContent = step.text;
  const doneButton = $("#cook-mode-done");
  doneButton.setAttribute("aria-pressed", String(done));
  doneButton.classList.toggle("is-done", done);
  $("#cook-mode-step").classList.toggle("is-done", done);
  $("#cook-mode-prev").disabled = index === 0;
  const next = $("#cook-mode-next-label");
  next.dataset.i18n = last ? "cook.finish" : "cook.next";
  next.textContent = t(next.dataset.i18n);
  $("#cook-mode-meter").style.width = `${cookPercent(recipe, progress)}%`;
  cookModeUses(recipe, step, index, progress);
  cookApply($("#cook-mode"), recipe, progress);
  // De timers (cook_timers.js) tonen de tijden uit deze stap.
  window.dispatchEvent(new CustomEvent("ms:cookstep", { detail: { recipe, index, step } }));
}

// De ingrediënten bij de stap, en bij een recept uit Mealie de knop Koppelen met het formulier.
function cookModeUses(recipe, step, index, progress) {
  const linkable = cookLinkable(recipe, step);
  const editing = linkable && cookMode.linking === index;
  const uses = cookUses(step);
  $("#cook-mode-uses").hidden = !uses.length && !linkable;
  $("#cook-mode-uses-list").innerHTML = editing ? "" : cookModeUsesHtml(recipe, step, progress);
  $("#cook-mode-uses-list").hidden = editing || !uses.length;
  $("#cook-mode-uses-auto").hidden = editing || !step.auto;
  $("#cook-mode-uses-none").hidden = editing || uses.length > 0;
  const link = $("#cook-mode-link");
  link.hidden = !linkable || editing;
  link.setAttribute("aria-expanded", String(editing));
  const form = $("#cook-mode-link-form");
  if (editing && form.hidden) {
    $("#cook-mode-link-items").innerHTML = cookLinkFormHtml(recipe, step);
    message("#cook-mode-link-message", "");
  }
  form.hidden = !editing;
}

function cookModeLinking(index) {
  cookMode.linking = index;
  cookModeRender();
  if (index >= 0) $("#cook-mode-link-items input")?.focus({ preventScroll: true });
}

// Slaat de koppelingen van één stap op in Mealie en werkt elk recept met dezelfde slug bij (het weekmenu en Recepten
// zoeken kunnen hetzelfde recept tegelijk open hebben).
async function cookSaveLinks(recipe, index, refs) {
  const step = recipe.steps[index];
  const data = await jsonFetch(`/api/mealie/recipes/${encodeURIComponent(recipe.slug)}/steps/${encodeURIComponent(step.id)}/ingredients`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refs }),
  });
  const saved = (Array.isArray(data?.refs) ? data.refs : refs).map(cookRef).filter(Boolean);
  cookLinkOverrides.set(`${recipe.slug} ${step.id}`, saved);
  for (const model of new Set([recipe, ...cookViews.values()])) {
    if (model.slug === recipe.slug) cookLinkSteps(model);
  }
  return saved;
}

function openCookMode(root, opener) {
  const recipe = cookRecipeOf(root);
  const dialog = $("#cook-mode");
  if (!recipe || !recipe.steps.length || !dialog) return;
  const progress = cookProgress(recipe);
  const open = recipe.steps.findIndex((_, index) => !progress.steps.has(index));
  Object.assign(cookMode, { view: Number(root.dataset.cookView), key: recipe.key, index: open < 0 ? 0 : open, opener, swipe: null, linking: -1, saving: false });
  dialog.dataset.cookKey = recipe.key;
  dialog.dataset.cookView = root.dataset.cookView;
  $("#cook-mode-ingredients-list").innerHTML = cookIngredientsHtml(recipe, progress, 4);
  $("#cook-mode-ingredients").hidden = !recipe.ingredients.length;
  const wide = typeof window.matchMedia === "function" && window.matchMedia("(min-width: 860px)").matches;
  cookModePanel(wide && recipe.ingredients.length > 0);
  cookModeRender();
  if (!dialog.open) dialog.showModal();
  $("#cook-mode-counter").focus({ preventScroll: true });
  cookWakeUpdate();
}

function cookModeAct(action) {
  const recipe = cookModeRecipe();
  if (!recipe) return;
  const from = Math.min(Math.max(cookMode.index, 0), recipe.steps.length - 1);
  const move = cookModeMove(from, recipe.steps.length, action);
  cookMode.index = move.index;
  if (move.index !== from) cookMode.linking = -1; // een andere stap: het formulier van de vorige stap gaat dicht
  if (move.markDone) cookChange($("#cook-mode"), (progress) => progress.steps.add(from));
  if (move.close) $("#cook-mode").close();
  else cookModeRender();
  if (move.index !== from) $("#cook-mode-stage").scrollTop = 0; // een nieuwe stap begint bovenaan
}

function cookModeClosed() {
  const dialog = $("#cook-mode");
  const opener = cookMode.opener;
  delete dialog.dataset.cookKey;
  delete dialog.dataset.cookView;
  Object.assign(cookMode, { view: 0, key: "", index: 0, opener: null, swipe: null, linking: -1, saving: false });
  $("#cook-mode-link-form").hidden = true;
  if (opener?.isConnected) opener.focus({ preventScroll: true });
  cookWakeUpdate();
}

// ---- Bediening. De weergaven worden met innerHTML opgebouwd; daarom luistert één handler op het document.
document.addEventListener("click", (event) => {
  const target = event.target instanceof Element ? event.target : null;
  const start = target?.closest("[data-cook-start]");
  const done = target?.closest("[data-cook-done]");
  const reset = target?.closest("[data-cook-reset]");
  const root = (start || done || reset)?.closest("[data-cook-key]");
  if (root && start) openCookMode(root, start);
  else if (root && done) cookChange(root, (progress) => cookToggle(progress.steps, Number(done.dataset.cookDone)));
  else if (root && reset) {
    cookChange(root, (progress) => {
      progress.ingredients.clear();
      progress.steps.clear();
    });
    root.querySelector("[data-cook-start], [data-cook-tick]")?.focus({ preventScroll: true }); // de knop zelf is nu weg
  }
  // Een klik kan het recept uit beeld halen (terug, een tab, een link): kijk daarna of het scherm nog aan moet.
  if (cookWake.lock || cookWakeWanted()) setTimeout(cookWakeUpdate);
});

document.addEventListener("change", (event) => {
  const input = event.target;
  if (!(input instanceof HTMLInputElement)) return;
  if (input.matches("[data-cook-tick]")) {
    const root = input.closest("[data-cook-key]");
    if (root) cookChange(root, (progress) => cookToggle(progress.ingredients, Number(input.dataset.cookTick), input.checked));
  } else if (input.matches("[data-cook-wake]")) {
    cookWakeRemember(input.checked);
    document.querySelectorAll("[data-cook-wake]").forEach((other) => {
      other.checked = input.checked;
    });
    cookWakeUpdate();
  }
});

document.addEventListener("visibilitychange", cookWakeUpdate);
// Een venster dat sluit (ook het recept van een maaltijd): `close` bubbelt niet, maar komt wel langs in de capture-fase.
document.addEventListener("close", () => setTimeout(cookWakeUpdate), true);
window.addEventListener("popstate", () => {
  if ($("#cook-mode")?.open) $("#cook-mode").close();
  setTimeout(cookWakeUpdate);
});
window.addEventListener("ms:languagechange", cookSyncAll);
// De kookmodus aan of uit (Instellingen, Weergave): de vinkjes en het scherm-aan volgen meteen. Uit sluit een open kookmodus.
window.addEventListener("ms:appearancepaint", () => {
  if (!cookEnabled() && $("#cook-mode")?.open) $("#cook-mode").close();
  cookSyncAll();
  cookWakeUpdate();
});

$("#cook-mode-prev").addEventListener("click", () => cookModeAct("prev"));
$("#cook-mode-next").addEventListener("click", () => cookModeAct("next"));
$("#cook-mode-close").addEventListener("click", () => $("#cook-mode").close());
$("#cook-mode-done").addEventListener("click", () => {
  const index = cookMode.index;
  cookChange($("#cook-mode"), (progress) => cookToggle(progress.steps, index));
});
$("#cook-mode-ingredients").addEventListener("click", () => cookModePanel($("#cook-mode-panel").hidden));
// Ingrediënten koppelen aan de stap (recept uit Mealie): het formulier opent op de plek van de lijst; Opslaan schrijft naar Mealie.
$("#cook-mode-link").addEventListener("click", () => cookModeLinking(cookMode.index));
$("#cook-mode-link-cancel").addEventListener("click", () => {
  cookModeLinking(-1);
  $("#cook-mode-link").focus({ preventScroll: true });
});
$("#cook-mode-link-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const recipe = cookModeRecipe();
  const index = cookMode.linking;
  if (!recipe || index < 0 || cookMode.saving) return;
  const refs = [...$("#cook-mode-link-items").querySelectorAll("[data-cook-link-ref]:checked")].map((input) => input.value);
  const save = $("#cook-mode-link-save");
  cookMode.saving = true;
  save.disabled = true;
  message("#cook-mode-link-message", t("cook.link_saving"), "info");
  try {
    await cookSaveLinks(recipe, index, refs);
    if (cookMode.linking === index) cookMode.linking = -1;
    cookSyncAll();
    $("#cook-mode-link").focus({ preventScroll: true });
  } catch (error) {
    message("#cook-mode-link-message", error.message || t("cook.link_failed"));
  } finally {
    cookMode.saving = false;
    save.disabled = false;
  }
});
$("#cook-mode").addEventListener("close", cookModeClosed);
// Esc sluit de kookmodus; op een smal scherm sluit hij eerst het ingrediëntenpaneel dat over de stap ligt.
$("#cook-mode").addEventListener("cancel", (event) => {
  const narrow = typeof window.matchMedia === "function" && !window.matchMedia("(min-width: 860px)").matches;
  if (cookMode.linking >= 0) {
    event.preventDefault(); // Esc sluit eerst het formulier om te koppelen
    cookModeLinking(-1);
    $("#cook-mode-link").focus({ preventScroll: true });
  } else if (narrow && !$("#cook-mode-panel").hidden) {
    event.preventDefault();
    cookModePanel(false);
  }
});
$("#cook-mode").addEventListener("keydown", (event) => {
  if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
  if (event.target instanceof Element && event.target.closest("input, select, textarea")) return;
  const action = { ArrowRight: "forward", PageDown: "forward", ArrowLeft: "prev", PageUp: "prev" }[event.key];
  if (!action) return;
  event.preventDefault();
  cookModeAct(action);
});
// Vegen op de stap: naar links is de volgende stap, naar rechts de vorige. Alleen met een vinger of pen, niet met de muis.
$("#cook-mode-stage").addEventListener("pointerdown", (event) => {
  cookMode.swipe = event.pointerType === "mouse" ? null : { x: event.clientX, y: event.clientY };
});
$("#cook-mode-stage").addEventListener("pointerup", (event) => {
  const swipe = cookMode.swipe;
  cookMode.swipe = null;
  if (!swipe) return;
  const dx = event.clientX - swipe.x;
  const dy = event.clientY - swipe.y;
  if (Math.abs(dx) > 60 && Math.abs(dx) > 2 * Math.abs(dy)) cookModeAct(dx < 0 ? "forward" : "prev");
});
$("#cook-mode-stage").addEventListener("pointercancel", () => {
  cookMode.swipe = null;
});

cookPrune();
