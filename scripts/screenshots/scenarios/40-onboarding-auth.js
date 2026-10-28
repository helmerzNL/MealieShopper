// Installatiehulp, inloggen, uitnodiging, toestemmingsscherm, beveiliging en gebruikers.
const { execFileSync } = require("child_process");

const HOST = "mealieshopper.example.nl";
const SERVER = `https://${HOST}`;

// Een nagebootste 2FA-sleutel: herkenbaar nep, zodat niemand hem per ongeluk gebruikt.
const MFA_SECRET = "DEMOKEYDEMOKEYDEMOKEYDEMOKEYDE";
const MFA_START = {
  otpauthUri: `otpauth://totp/MealieShopper:anna?secret=${MFA_SECRET}&issuer=MealieShopper`,
  secret: MFA_SECRET.replace(/(.{4})/g, "$1 ").trim(),
};

// Installatiehulp: de server zegt dat de eerste start nog niet is afgerond.
const onboarding = (extra = {}) => ({
  needed: true, host: HOST, hasDomain: true, secure: true, passkeyAvailable: true, passkeyUnavailableReason: "",
  origin: SERVER, authEnabled: true, authRequired: true, authDone: true, authMode: "password", authenticated: true,
  minPasswordLength: 10, ...extra,
});

// Alles ongekoppeld, behalve wat een scenario zelf aanzet.
const UNLINKED = {
  "/api/mealie/status*": { configured: false, url: "", source: "" },
  "/api/ah/auth/status*": { connected: false },
  "/api/jumbo/auth/status*": { connected: false, loginPending: false },
  "/api/picnic/auth/status*": { connected: false, twoFactorPending: false },
};

// Een stap van de installatiehulp: ga ernaartoe met de eigen functie van de pagina.
const step = (id, sub = "choose") => async (page) => {
  await page.waitForSelector("#onboarding:not([hidden])");
  await page.evaluate(([s, u]) => obGo(s, u), [id, sub]);
  await page.waitForTimeout(500);
};

// Scroll een element van de installatiehulp in beeld.
const scrollTo = (page, selector, block = "center") =>
  page.$eval(selector, (element, b) => element.scrollIntoView({ block: b }), block);

// Naar onderen scrollen tot de knoppen onder aan de stap in beeld zijn (zoals iemand zou doen om de stap af te maken).
const toActions = async (page, selector = "#ob-body > .ob-actions") => {
  await scrollTo(page, selector, "end");
  await page.evaluate(() => document.getElementById("onboarding").scrollBy(0, 40)); // wat lucht onder de knoppen
};

// Profiel openen via het avatar-menu (desktop) of de tab Profiel (telefoon), en daarna een subtab.
async function openProfile(page, subtab) {
  const trigger = page.locator("[data-user-menu-trigger]:visible").first();
  await trigger.click();
  await page.click('[data-user-action="profile"]');
  await page.waitForTimeout(300);
  if (subtab) await page.click(`[data-subtab="${subtab}"]`);
  await page.waitForTimeout(500);
}

// Het toestemmingsscherm is server-gerenderd (templates/oauth.html) en vraagt een ingelogde sessie en een
// geregistreerde AI-app. We renderen het sjabloon zelf met de Flask-app en geven dat als antwoord op de aanvraag.
// SHOT_PYTHON is de Python met de afhankelijkheden van MealieShopper (standaard python3).
function renderConsent(lang) {
  const code = [
    "import os, sys, tempfile",
    "os.environ['PASSKEY_AUTH_ENABLED'] = 'false'",
    "os.environ['MEALIESHOPPER_TESTING'] = '1'",
    "os.environ['MEALIESHOPPER_DATA_DIR'] = tempfile.mkdtemp()",
    "from flask import render_template",
    "from mealieshopper.app import create_app",
    "app = create_app()",
    `with app.test_request_context('/', headers={'Cookie': 'ms_lang=${lang}'}):`,
    `    sys.stdout.write(render_template('oauth.html', error='', client='ChatGPT', signed='demo', base='${SERVER}', username='anna'))`,
  ].join("\n");
  return execFileSync(process.env.SHOT_PYTHON || "python3", ["-c", code], {
    cwd: require("path").resolve(__dirname, "../../.."),
    encoding: "utf8",
  });
}

const settingsUsers = {
  users: [
    { id: "u-anna", username: "anna", displayName: "Anna de Vries", role: "owner", passkeys: 1, password: true, mfa: true, createdAt: "2026-10-21T09:12:00", hasAvatar: false, avatarVersion: 0 },
    { id: "u-daan", username: "daan", displayName: "Daan de Vries", role: "user", passkeys: 0, password: true, mfa: false, createdAt: "2026-10-22T18:40:00", hasAvatar: false, avatarVersion: 0 },
    { id: "u-lotte", username: "lotte", displayName: "Lotte Bakker", role: "user", passkeys: 1, password: false, mfa: false, createdAt: "2026-10-25T07:55:00", hasAvatar: false, avatarVersion: 0 },
  ],
  invites: [{ id: "i-mila", kind: "new", username: "mila", expiresAt: "2026-11-04T12:00:00" }],
};

module.exports = [
  // Stap 1: beveiliging kiezen. Een nieuwe installatie: nog niet ingelogd, nog geen beveiliging.
  {
    name: "ob-choose",
    routes: {
      "/api/auth/status*": { authenticated: false, enabled: false, configured: false, setupRequired: true, isOwner: true, role: "owner", methods: {}, passwordConfigured: false, mfaEnabled: false },
      "/api/onboarding/status*": onboarding({ authDone: false, authMode: "", authenticated: false }),
      "/api/onboarding/start": { needed: true },
      ...UNLINKED,
    },
    run: async (page) => {
      await page.waitForSelector("#onboarding:not([hidden])");
      await page.waitForTimeout(400);
    },
  },

  // Stap 1, tweestapsverificatie: de QR-code en het invulveld.
  {
    name: "ob-mfa",
    routes: {
      "/api/onboarding/status*": onboarding(),
      "/api/onboarding/start": { needed: true },
      "/api/auth/mfa/start": MFA_START,
      ...UNLINKED,
    },
    run: async (page) => {
      await step("auth", "mfa-setup")(page);
      await page.waitForSelector("[data-mfa-confirm]");
      await scrollTo(page, "#ob-progress", "start"); // de kop valt weg, de knoppen onderaan blijven in beeld
      await page.evaluate(() => document.getElementById("onboarding").scrollBy(0, -12));
    },
  },

  // Stap 2: Mealie koppelen, met adres en token ingevuld.
  {
    name: "ob-mealie",
    routes: { "/api/onboarding/status*": onboarding(), "/api/onboarding/start": { needed: true }, ...UNLINKED },
    run: async (page) => {
      await step("mealie")(page);
      await page.fill("#ob-mealie-url", "https://mealie.example.nl");
      await page.fill("#ob-mealie-token", "demo-token-niet-echt");
      await toActions(page);
    },
  },

  // Stap 3: Albert Heijn, met de uitleg, de inlogknop en de geplakte regel uit de console.
  {
    name: "ob-ah",
    routes: { "/api/onboarding/status*": onboarding(), "/api/onboarding/start": { needed: true }, ...UNLINKED },
    run: async (page) => {
      await step("ah")(page);
      await page.fill("#ob-ah-token", "appie://login-exit?code=3f9a1c7e5b2d4a60");
      await toActions(page);
    },
  },

  // Stap 4: Jumbo, hetzelfde als bij Albert Heijn.
  {
    name: "ob-jumbo",
    routes: { "/api/onboarding/status*": onboarding(), "/api/onboarding/start": { needed: true }, ...UNLINKED },
    run: async (page) => {
      await step("jumbo")(page);
      await page.fill("#ob-jumbo-token", "jumbo://callbacks/login?code=8d41b0c2e6f7a359");
      await toActions(page);
    },
  },

  // Stap 5: Picnic, met het veld voor de bevestigingscode erbij.
  {
    name: "ob-picnic",
    routes: {
      "/api/onboarding/status*": onboarding(),
      "/api/onboarding/start": { needed: true },
      ...UNLINKED,
      "/api/picnic/auth/status*": { connected: false, twoFactorPending: true },
    },
    run: async (page) => {
      await step("picnic")(page);
      await page.fill("#ob-picnic-email", "anna@example.nl");
      await page.fill("#ob-picnic-code", "482913");
      await toActions(page);
    },
  },

  // Laatste stap: alles gekoppeld (de basis van demo.js).
  {
    name: "ob-done",
    routes: { "/api/onboarding/status*": onboarding(), "/api/onboarding/start": { needed: true } },
    run: step("done"),
  },

  // De inlogpagina met wachtwoord en passkey.
  {
    name: "login",
    routes: {
      "/api/auth/status*": { authenticated: false, enabled: true, configured: true, setupRequired: false, isOwner: true, role: "owner", methods: { password: true, passkey: true }, passwordConfigured: true, mfaEnabled: false, credentialCount: 1 },
      "/api/onboarding/status*": onboarding({ needed: false, authenticated: false }),
    },
    run: async (page) => {
      await page.waitForSelector("#auth-gate:not([hidden])");
      await page.fill("#login-username", "anna");
    },
  },

  // Een uitgenodigde maakt een account aan via de uitnodigingslink (#invite=...).
  {
    name: "invite",
    path: "/?uitnodiging=1#invite=demo-uitnodiging",
    routes: {
      "/api/auth/status*": { authenticated: false, enabled: true, configured: true, setupRequired: false, isOwner: true, role: "owner", methods: { password: true, passkey: true }, passwordConfigured: true, mfaEnabled: false, credentialCount: 1 },
      "/api/onboarding/status*": onboarding({ needed: false, authenticated: false }),
      "/api/invites/info": { kind: "new", username: "mila", invitedBy: "Anna de Vries", expiresAt: "2026-11-04T12:00:00" },
    },
    run: async (page) => {
      await page.waitForSelector("#invite-gate:not([hidden])");
      await page.waitForTimeout(300);
    },
  },

  // Het toestemmingsscherm van een AI-app (server-gerenderd, zie renderConsent).
  {
    name: "consent",
    path: "/oauth/authorize?demo=1",
    routes: {
      "/oauth/authorize*": async (route) => {
        // Het eerste verzoek heeft de taalcookie nog niet; de taal van het venster (nl-NL of en-GB) wel.
        const lang = (route.request().headers()["accept-language"] || "").startsWith("en") ? "en" : "nl";
        await route.fulfill({ status: 200, contentType: "text/html; charset=utf-8", body: renderConsent(lang) });
      },
    },
  },

  // Profiel > Beveiliging: wachtwoord, tweestapsverificatie en passkeys.
  {
    name: "security",
    routes: {
      "/api/auth/credentials": { credentials: [
        { id: "c1", credential_name: "MacBook van Anna", username: "anna", created_at: "2026-10-21T09:15:00", last_used_at: "2026-10-27T19:02:00" },
        { id: "c2", credential_name: "iPhone van Anna", username: "anna", created_at: "2026-10-23T08:30:00", last_used_at: "2026-10-28T07:41:00" },
      ] },
    },
    run: async (page) => {
      await openProfile(page, "profile-security");
      await page.mouse.wheel(0, 0);
    },
  },

  // Instellingen > Gebruikers: de beheerder met twee gebruikers, een openstaande uitnodiging en het uitnodigformulier.
  {
    name: "users",
    routes: {
      "/api/users": settingsUsers,
      "/api/users/invites": { url: `${SERVER}/#invite=demo-uitnodiging`, expiresAt: "2026-11-04T12:00:00" },
    },
    run: async (page, { device }) => {
      const trigger = page.locator("[data-user-menu-trigger]:visible").first();
      await trigger.click();
      await page.click('[data-user-action="settings"]');
      await page.waitForTimeout(300);
      await page.click('[data-subtab="settings-users"]');
      await page.waitForTimeout(600);
      await page.fill("#invite-name", "mila");
      await page.click("#invite-create-form button[type=submit]");
      await page.waitForTimeout(500);
      await scrollTo(page, "#invite-result", device === "phone" ? "end" : "end");
    },
  },
];
