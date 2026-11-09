// Nagebootste gegevens voor de screenshots: zelfgetekende gerechten en producten (geen foto's van derden) en
// de antwoorden die elke pagina bij het laden vraagt. Een scenario overschrijft wat het anders nodig heeft.
const svg = (body) => "data:image/svg+xml;utf8," + encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120">${body}</svg>`);

// Een bord van boven met wat eten erop; kleuren per gerecht.
function dish(food, accent, bg = "#e6efe7") {
  return svg(
    `<rect width="120" height="120" fill="${bg}"/><circle cx="60" cy="60" r="48" fill="#fbfbf8"/><circle cx="60" cy="60" r="40" fill="#efefe9"/>` +
      `<circle cx="60" cy="60" r="31" fill="${food}"/><circle cx="48" cy="52" r="7" fill="${accent}"/><circle cx="70" cy="66" r="9" fill="${accent}"/>` +
      `<circle cx="64" cy="46" r="5" fill="#f2cf45"/><circle cx="52" cy="72" r="5" fill="#f2cf45"/>`
  );
}

// Een verpakking (pak, fles of potje) in een kleur.
function product(color, kind = "pack") {
  const shape =
    kind === "bottle"
      ? `<rect x="46" y="14" width="28" height="14" rx="5" fill="${color}"/><rect x="36" y="26" width="48" height="82" rx="14" fill="${color}"/><rect x="42" y="52" width="36" height="30" rx="5" fill="#fff"/>`
      : kind === "jar"
        ? `<rect x="34" y="18" width="52" height="14" rx="5" fill="#8a948d"/><rect x="30" y="30" width="60" height="78" rx="12" fill="${color}"/><rect x="38" y="52" width="44" height="30" rx="5" fill="#fff"/>`
        : `<rect x="32" y="12" width="56" height="96" rx="9" fill="${color}"/><rect x="40" y="48" width="40" height="30" rx="5" fill="#fff"/><circle cx="60" cy="63" r="8" fill="${color}"/>`;
  return svg(`<rect width="120" height="120" fill="#fff"/>${shape}`);
}

const DISHES = {
  pesto: dish("#6aa55a", "#d9503f"),
  stamppot: dish("#7fae66", "#c9a36a"),
  soup: dish("#d9503f", "#f2cf45", "#f1e6dc"),
  wrap: dish("#e6b84a", "#6aa55a", "#f3ecd9"),
  curry: dish("#e08a3c", "#f2cf45", "#f5e5d3"),
  pancake: dish("#e6c98a", "#c9703a", "#f6eedd"),
  lasagna: dish("#d6704a", "#f2cf45", "#f3e0dc"),
  stroganoff: dish("#b98a5a", "#e8dcc6", "#e9e3dc"),
  salad: dish("#8dbb6a", "#d9503f", "#e3eedd"),
  fish: dish("#e3a889", "#6aa55a", "#e0ebf0"),
};

const mealieUrl = "https://mealie.example.nl";

// Het versienummer van deze checkout, zodat Over en de updatecheck bij elke release kloppen; "nieuwer" is de volgende minor.
const VERSION = require("fs").readFileSync(require("path").join(__dirname, "../../VERSION"), "utf8").trim();
const NEWER = VERSION.replace(/^(\d+)\.(\d+)\..*$/, (all, major, minor) => `${major}.${Number(minor) + 1}.0`);

// Antwoorden die elke pagina bij het laden vraagt: ingelogd als eigenaar, alles gekoppeld, geen update.
function base(lang) {
  return [
    ["/api/auth/status*", { authenticated: true, enabled: true, configured: true, setupRequired: false, isOwner: true, role: "owner", userId: "u-anna", username: "anna", displayName: "Anna de Vries", hasAvatar: false, avatarVersion: 0, methods: { passkey: true, password: true }, passwordConfigured: true, mfaEnabled: true, credentialCount: 1, recoveryCodesLeft: 8, rpId: "mealieshopper.example.nl", rpOrigins: ["https://mealieshopper.example.nl"], envEnabled: false }],
    ["/api/onboarding/status*", { needed: false, authEnabled: true, authRequired: true, authDone: true, authMode: "password", authenticated: true, minPasswordLength: 10 }],
    ["/api/ah/auth/status*", { connected: true, verified: true, type: "refresh" }],
    ["/api/picnic/auth/status*", { connected: true, verified: true, twoFactorPending: false }],
    ["/api/jumbo/auth/status*", { connected: true, verified: true, loginPending: false }],
    ["/api/crisp/auth/status*", { connected: true, verified: true, codePending: false, country: "nl" }],
    ["/api/mealie/status*", { configured: true, url: mealieUrl, source: "app", connected: true, verified: true, version: "v2.8.0", username: "anna" }],
    ["/api/update", { available: false, current: VERSION, latest: VERSION }],
  ];
}

// De datum van het weekmenu: maandag van de week van 28 oktober 2026 (woensdag).
const WEEK = ["2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30", "2026-10-31", "2026-11-01"];

// De prijs per kilo, liter of stuk zoals prices.unit_price die geeft ({ value, unit }), uit een verpakking als "2 x 300 g",
// "1,5 l", "6 x 30 cl" of "8 stuks". Geen bekende maat: null.
function perUnit(price, unitSize) {
  const text = String(unitSize || "").toLowerCase().replace(",", ".");
  if (typeof price !== "number") return null;
  if (/^per stuk$/.test(text)) return { value: price, unit: "stuk" };
  const match = /^(?:(\d+)\s*x\s*)?(\d+(?:\.\d+)?)\s*(kg|g|l|cl|ml|stuks?)$/.exec(text);
  if (!match) return null;
  const amount = Number(match[1] || 1) * Number(match[2]);
  const [factor, unit] = { kg: [1, "kg"], g: [0.001, "kg"], l: [1, "l"], cl: [0.01, "l"], ml: [0.001, "l"], stuk: [1, "stuk"], stuks: [1, "stuk"] }[match[3]];
  return { value: Math.round((price / (amount * factor)) * 100) / 100, unit };
}

// Een subtab openen. Vanaf 860 px staan de subtabs in het zijmenu en is de link in de balk verborgen: dan via de DOM, zoals
// routerOpenSubtab in static/router.js dat ook doet.
async function openSubtab(page, id) {
  const link = page.locator(`[data-subtab="${id}"]`).first();
  if (await link.isVisible()) await link.click();
  else await page.evaluate((sub) => document.querySelector(`[data-subtab="${sub}"]`).click(), id);
}

// Het tabblad Mandje openen en een winkel kiezen in de keuzelijst bovenaan. Gedeeld door de mandjes van AH, Picnic, Jumbo en
// Crisp.
async function openBasket(page, store) {
  // Op een telefoon staat Mandje niet in de menubalk maar onder Meer (het menu van de avatar): dan dat menu-item, dat naar /cart gaat.
  const tab = page.locator('[data-tab="basket"]');
  if (await tab.isVisible()) await tab.click();
  else await page.evaluate(() => document.querySelector('[data-user-action="basket"]').click());
  await page.waitForSelector("#basket-store");
  if (store) await page.selectOption("#basket-store", store);
  await page.waitForTimeout(700);
}

module.exports = { svg, dish, product, DISHES, base, WEEK, mealieUrl, VERSION, NEWER, perUnit, openSubtab, openBasket };
