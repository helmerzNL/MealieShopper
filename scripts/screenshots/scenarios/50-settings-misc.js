// Instellingen (Koppelen, Auditlog, Systeem), AI-apps, Over, de beta-build en het donkere thema.
const { DISHES, WEEK } = require("../demo");

// Open een scherm via de functies van de app (zoals het avatar-menu dat doet) en wacht even op het laden.
const openSettings = (subtab) => async (page) => {
  await page.evaluate((name) => openSettings(name), subtab);
  await page.waitForTimeout(700);
};

// Alle vier de koppelingen zijn gelukt; de blokken staan dan ingeklapt en de aanbiedingen zijn vandaag opgehaald.
const OFFERS_STATUS = {
  enabled: true,
  hour: 5,
  nextRun: "2026-10-29T04:00:00Z",
  lastRun: "2026-10-28T04:00:00Z",
  lastTrigger: "scheduled",
  lastResult: { stores: {} },
  stores: {
    ah: { linked: true, syncedAt: "2026-10-28T04:00:21Z", total: 412 },
    picnic: { linked: true, syncedAt: "2026-10-28T04:00:48Z", total: 186 },
    jumbo: { linked: true, syncedAt: "2026-10-28T04:01:09Z", total: 354 },
  },
};

// Een AI-sleutel van Claude is ingesteld, dus het blok AI-beoordeling staat ingeklapt.
const AI_REVIEW = {
  active: "anthropic",
  chosen: "",
  batchSize: 20,
  defaultMaxPairs: 60,
  maxPairs: 120,
  recipesTab: true,
  providers: {
    anthropic: { name: "Claude (Anthropic)", configured: true, source: "app", hint: "…x7Q2", model: "", defaultModel: "claude-sonnet-4-5" },
    openai: { name: "ChatGPT (OpenAI)", configured: false, source: "", hint: "", model: "", defaultModel: "gpt-5-mini" },
  },
};

const LINK_ROUTES = { "/api/offers/status": OFFERS_STATUS, "/api/offers/ai": AI_REVIEW };

// Klap het blok van één winkel open en zet het bovenaan het scherm, onder de vaste balk.
const openStoreBlock = (store, fill) => async (page) => {
  await openSettings("settings-link")(page);
  await page.click(`#${store}-login-toggle`);
  if (fill) await fill(page);
  await page.evaluate((name) => {
    const box = document.getElementById(`${name}-login-box`);
    window.scrollTo({ top: box.getBoundingClientRect().top + window.scrollY - 84 });
  }, store);
};

const settingsScenarios = [
  {
    name: "settings-stores",
    routes: LINK_ROUTES,
    run: async (page) => {
      await openSettings("settings-link")(page);
      // De vier meldingen en daaronder de ingeklapte blokken, zonder de kop van de pagina.
      await page.evaluate(() => window.scrollTo({ top: document.getElementById("mealie-status").getBoundingClientRect().top + window.scrollY - 84 }));
    },
  },
  {
    name: "settings-picnic",
    routes: LINK_ROUTES,
    run: openStoreBlock("picnic", async (page) => {
      await page.fill("#picnic-email", "anna@example.nl");
      await page.fill("#picnic-password", "voorbeeldwachtwoord");
      await page.evaluate(() => document.activeElement.blur());
    }),
  },
  {
    name: "settings-jumbo",
    routes: LINK_ROUTES,
    run: openStoreBlock("jumbo"),
  },
];


// ---- Auditlog ------------------------------------------------------------------------------------------------

const entry = (id, time, service, kind, action, actor, status, ms, extra = {}) => ({
  id, ts: `2026-10-28T${time}Z`, service, kind, action, actor, ok: status < 400, status, durationMs: ms, error: "", ...extra,
});

// Nieuwste bovenaan. Een AI-assistent (Claude) vraagt via MCP iets aan; de winkelverzoeken die daaruit volgen staan eronder.
const AUDIT = [
  entry(9, "10:21:44", "ah", "outbound", "GET /mobile-services/product/search/v2", "mcp:Claude", 200, 164, {
    method: "GET", url: "https://api.ah.nl/mobile-services/product/search/v2?query=halfvolle%20melk&sortOn=RELEVANCE&size=5",
    reqBytes: 0, respBytes: 18432, detail: { redirects: 0 },
    request: "",
    response: JSON.stringify({ page: { totalElements: 38, size: 5 }, products: [{ webshopId: 441199, title: "AH Halfvolle melk", salesUnitSize: "1 l", priceBeforeBonus: 1.59, currentPrice: 1.29 }] }),
    related: [{ id: 8, ts: "2026-10-28T10:21:44Z", service: "mcp", kind: "mcp", action: "search_ah_products", actor: "mcp:Claude", ok: true, status: 200, durationMs: 212 }],
  }),
  entry(8, "10:21:44", "mcp", "mcp", "search_ah_products", "mcp:Claude", 200, 212),
  entry(7, "10:12:03", "ah", "outbound", "GraphQL OrderFulfillments", "app:anna", 200, 455, {
    method: "POST", url: "https://api.ah.nl/graphql", reqBytes: 612, respBytes: 9120, detail: { redirects: 0 },
    request: JSON.stringify({ operationName: "OrderFulfillments", variables: { status: "OPEN", first: 10 } }),
    response: JSON.stringify({ data: { orderFulfillments: { result: [{ orderId: 2048113, statusCode: "OPEN", totalPrice: { amount: 63.4 } }] } } }),
    related: [],
  }),
  entry(6, "09:58:31", "jumbo", "outbound", "GraphQL OnlineOrdersAndStoreReceipts", "app:anna", 403, 96, { error: "HTTP 403", method: "POST", url: "https://www.jumbo.com/api/graphql" }),
  entry(5, "09:41:10", "picnic", "outbound", "GET /api/15/cart", "app:anna", 200, 301),
  entry(4, "09:40:58", "mcp", "mcp", "get_week_menu", "mcp:Claude", 200, 188),
  entry(3, "09:14:52", "jumbo", "outbound", "GraphQL OnlineOrdersAndStoreReceipts", "app:anna", 200, 540),
  entry(2, "08:05:12", "picnic", "outbound", "GET /api/15/deliveries/summary", "app:anna", 200, 277),
  entry(1, "04:00:21", "ah", "outbound", "GET /mobile-services/bonuses", "system", 200, 633),
];

const AUDIT_LIST = {
  entries: AUDIT.map(({ request, response, related, detail, url, method, reqBytes, respBytes, ...row }) => row),
  next: null,
  total: 1284,
  last24h: { ah: { total: 6, errors: 0 }, picnic: { total: 2, errors: 0 }, jumbo: { total: 3, errors: 1 }, mcp: { total: 14, errors: 0 } },
  settings: { enabled: true, bodies: true, retentionDays: 30 },
};

const AUDIT_ROUTES = {
  "/api/audit?*": AUDIT_LIST,
  "/api/audit/*": (route, request) => {
    const id = Number(new URL(request.url()).pathname.split("/").pop());
    const found = AUDIT.find((item) => item.id === id) || AUDIT[0];
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ...found, related: found.related || [] }) });
  },
};

// De samenvatting en de lijst bovenaan het scherm, zonder de kop van de pagina.
const scrollToAudit = (page) =>
  page.evaluate(() => window.scrollTo({ top: document.getElementById("audit-summary").getBoundingClientRect().top + window.scrollY - 84 }));

const auditScenarios = [
  {
    name: "audit",
    routes: AUDIT_ROUTES,
    run: async (page) => {
      await openSettings("settings-audit")(page);
      await scrollToAudit(page);
    },
  },
  {
    name: "audit-detail",
    routes: AUDIT_ROUTES,
    run: async (page) => {
      await openSettings("settings-audit")(page);
      await page.click('[data-audit-id="9"] summary');
      await page.waitForTimeout(600);
      await page.evaluate(() => window.scrollTo({ top: document.querySelector('[data-audit-id="9"]').getBoundingClientRect().top + window.scrollY - 84 }));
    },
  },
];

// ---- AI-apps (Profiel) -----------------------------------------------------------------------------------

// De lijst met tools komt van de server zelf (in de gekozen taal); wij zetten alleen de koppeling en de apps neer.
const aiScenarios = [
  {
    name: "ai",
    routes: {
      "/api/mcp/status": async (route) => {
        const real = await (await route.fetch()).json();
        const status = {
          ...real,
          enabled: true,
          reason: "",
          canConfigure: true,
          secure: true,
          url: "https://mealieshopper.example.nl/mcp",
          apps: [
            { clientId: "c-claude", name: "Claude", lastUsedAt: "2026-10-28T10:21:44Z" },
            { clientId: "c-chatgpt", name: "ChatGPT", lastUsedAt: "2026-10-26T19:03:10Z" },
          ],
        };
        return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(status) });
      },
      "/api/server-settings": async (route) => {
        const real = await (await route.fetch()).json();
        const settings = {
          ...real,
          publicBaseUrl: "https://mealieshopper.example.nl",
          publicBaseUrlSource: "env",
          publicBaseUrlOverride: "",
          detectedBaseUrl: "https://mealieshopper.example.nl",
          envBaseUrl: "https://mealieshopper.example.nl",
          mcpEnabled: true,
          mcpSource: "setting",
          loginOn: true,
        };
        return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(settings) });
      },
    },
    run: async (page) => {
      await page.evaluate(() => openProfile("profile-ai"));
      await page.waitForTimeout(900);
      // Het blok "AI-assistenten koppelen" met het adres en de uitleg per assistent bovenaan.
      await page.evaluate(() => window.scrollTo({ top: document.getElementById("ai-url").closest(".box").getBoundingClientRect().top + window.scrollY - 84 }));
    },
  },
];

// ---- Over, updates, beta en donker thema ---------------------------------------------------------------------

const plan = require("./01-plan.js")[0]; // het weekmenu van het voorbeeld, voor de beta- en de donkere variant

// De componentenlijst komt van de server zelf; alleen de versie en de commit zetten we vast.
const about = async (route) => {
  const real = await (await route.fetch()).json();
  const info = { ...real, version: "2.80.1", commit: "7c3e9a4", label: "v2.80.1 (7c3e9a4)", channel: "stable" };
  return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(info) });
};

// Een nieuwere release op GitHub: de melding bovenaan, het bolletje bij het profiel en het tabblad Systeem.
const UPDATE = {
  enabled: true,
  available: true,
  current: "2.80.1",
  latest: "2.81.0",
  url: "https://github.com/helmerzNL/MealieShopper/releases/tag/v2.81.0",
  checkedAt: "2026-10-28T09:55:12Z",
  error: false,
};

// De beta-build toont een badge naast het woordmerk. De server tekent die zelf bij MEALIESHOPPER_TAG=beta; hier
// zetten we dezelfde badge in de pagina, zodat er geen tweede server nodig is.
const BETA_BADGE =
  '<span class="beta-badge" id="beta-badge" title="Dit is een beta-versie: hij kan nog fouten bevatten." data-i18n-attr="title:brand.beta_title">beta</span>';
const betaPage = async (route, request) => {
  const url = new URL(request.url());
  if (request.resourceType() !== "document" || url.pathname !== "/") return route.fallback();
  const response = await route.fetch();
  const html = (await response.text()).replace("Mealie<span>Shopper</span></h1>", `Mealie<span>Shopper</span></h1>${BETA_BADGE}`);
  return route.fulfill({ response, body: html });
};

const miscScenarios = [
  {
    name: "about",
    routes: { "/api/about": about },
    run: async (page) => {
      await page.evaluate(() => showPanel("about"));
      await page.waitForTimeout(900);
    },
  },
  {
    name: "updates",
    routes: { "/api/update": UPDATE },
    run: async (page) => {
      await openSettings("settings-system")(page);
    },
  },
  {
    name: "beta",
    routes: { ...plan.routes, "": betaPage },
    run: plan.run,
  },
  {
    name: "dark",
    theme: "dark",
    routes: plan.routes,
    run: plan.run,
  },
];

module.exports = [...settingsScenarios, ...auditScenarios, ...aiScenarios, ...miscScenarios];
