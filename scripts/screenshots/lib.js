// Maakt de screenshots in docs/screenshots/<taal>/<naam>.png: een venster (desktop) en een telefoon naast elkaar,
// getekend met Playwright tegen een draaiende MealieShopper. De gegevens zijn nagebootst (zie demo.js); er wordt
// niets bij Mealie, AH, Picnic of Jumbo opgehaald en alle plaatjes zijn zelfgetekend.
const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

const BASE = process.env.SHOT_URL || "http://localhost:8000";
const OUT = path.resolve(__dirname, "../../docs/screenshots");
const DESKTOP = { width: 930, height: 604 };
// De telefoon wordt op 390 px breed getekend en in het frame op 300 px getoond, zodat de onderste menubalk past zoals op een echt toestel.
const PHONE = { width: 390, height: 813 };
const NOW = new Date("2026-10-28T10:30:00");

// Een scenario heeft: name (bestandsnaam), routes, run(page, { lang, device }), path, theme ("dark"), desktopOnly en baseUrl
// (een andere server, bijvoorbeeld voor de beta-badge).
// Een route-sleutel is een pad met een * als jokerteken ("/api/offers*"); de waarde is JSON, een functie
// (route, request) => ..., of { status, json }. Later toegevoegde routes gaan voor eerdere.
async function install(page, routes) {
  for (const [key, value] of routes) {
    await page.route(`**${key}`, async (route, request) => {
      if (typeof value === "function") return value(route, request);
      if (value && typeof value === "object" && "json" in value) {
        return route.fulfill({ status: value.status || 200, contentType: "application/json", body: JSON.stringify(value.json) });
      }
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(value) });
    });
  }
}

async function shoot(browser, scenario, lang, device, base) {
  const viewport = device === "phone" ? PHONE : DESKTOP;
  const context = await browser.newContext({ viewport, deviceScaleFactor: 2, colorScheme: scenario.theme || "light", locale: lang === "nl" ? "nl-NL" : "en-GB", timezoneId: "Europe/Amsterdam" });
  await context.addInitScript(({ lang: l, theme }) => {
    try {
      localStorage.setItem("ms-lang", l);
      localStorage.setItem("ms-theme", theme);
      localStorage.setItem("ms-nav-collapsed", "0");
    } catch (e) { /* geen opslag */ }
    document.cookie = `ms_lang=${l}; path=/`;
  }, { lang, theme: scenario.theme || "light" });
  const page = await context.newPage();
  await page.clock.setFixedTime(NOW);
  await install(page, [...base(lang), ...Object.entries(scenario.routes || {})]);
  await page.goto(`${scenario.baseUrl || BASE}${scenario.path || "/"}${device === "phone" ? ((scenario.path || "/").includes("?") ? "&" : "?") + "tabbar=1" : ""}`, { waitUntil: "networkidle" });
  // Geen gereserveerde ruimte voor de scrollbalk: in een screenshot zou dat een lege streep rechts geven.
  await page.addStyleTag({ content: "html{scrollbar-gutter:auto !important}" });
  await page.waitForTimeout(400);
  if (scenario.run) await scenario.run(page, { lang, device });
  await page.waitForTimeout(500);
  await page.mouse.move(2, 2);
  const png = await page.screenshot();
  await context.close();
  return png;
}

function frameHtml(desktopPng, phonePng, theme) {
  const d = desktopPng.toString("base64");
  const p = phonePng.toString("base64");
  return `<!doctype html><html><head><meta charset="utf-8"><style>
    *{box-sizing:border-box}html,body{margin:0}
    body{width:1376px;height:700px;overflow:hidden;position:relative;background:#e9f0eb;font-family:Inter,system-ui,sans-serif}
    .win{position:absolute;left:20px;top:35px;width:970px;height:700px;border-radius:22px 22px 0 0;background:#dde6e0;box-shadow:0 18px 50px rgba(24,37,30,.22),0 0 0 1px rgba(24,37,30,.08)}
    .dots{position:absolute;left:34px;top:34px;display:flex;gap:9px}.dots i{width:14px;height:14px;border-radius:50%;display:block}
    .url{position:absolute;left:120px;right:100px;top:33px;height:18px;border-radius:9px;background:#f3f7f4}
    .win img{position:absolute;left:20px;top:60px;width:930px;height:604px;display:block}
    .phone{position:absolute;left:1012px;top:35px;width:318px;height:700px;border-radius:46px 46px 0 0;background:#16241d;box-shadow:0 18px 50px rgba(24,37,30,.3)}
    .screen{position:absolute;left:9px;top:12px;width:300px;height:660px;border-radius:36px 36px 0 0;overflow:hidden;background:${theme === "dark" ? "#0f1613" : "#fff"}}
    .phone img{position:absolute;left:0;top:28px;width:300px;height:625px;display:block}
    .island{position:absolute;left:129px;top:20px;width:60px;height:16px;border-radius:9px;background:#16241d;z-index:2}
  </style></head><body>
    <div class="win"><div class="dots"><i style="background:#ee6a5f"></i><i style="background:#f5bd4f"></i><i style="background:#61c554"></i></div><div class="url"></div><img src="data:image/png;base64,${d}"></div>
    <div class="phone"><div class="island"></div><div class="screen"><img src="data:image/png;base64,${p}"></div></div>
  </body></html>`;
}

async function run(scenarios, base) {
  const args = process.argv.slice(2);
  const arg = (name) => { const i = args.indexOf(`--${name}`); return i >= 0 ? args[i + 1] : null; };
  const only = arg("only") ? arg("only").split(",") : null;
  const langs = arg("lang") ? arg("lang").split(",") : ["nl", "en"];
  const browser = await chromium.launch();
  const framer = await (await browser.newContext({ viewport: { width: 1376, height: 700 } })).newPage();
  let made = 0;
  for (const scenario of scenarios) {
    if (only && !only.includes(scenario.name)) continue;
    for (const lang of langs) {
      try {
        const desktop = await shoot(browser, scenario, lang, "desktop", base);
        const phone = await shoot(browser, scenario, lang, scenario.desktopOnly ? "desktop" : "phone", base);
        await framer.setContent(frameHtml(desktop, scenario.desktopOnly ? desktop : phone, scenario.theme || "light"));
        const dir = path.join(OUT, lang);
        fs.mkdirSync(dir, { recursive: true });
        await framer.screenshot({ path: path.join(dir, `${scenario.name}.png`) });
        made += 1;
        console.log(`ok   ${lang}/${scenario.name}`);
      } catch (error) {
        console.error(`FOUT ${lang}/${scenario.name}: ${error.message.split("\n")[0]}`);
        process.exitCode = 1;
      }
    }
  }
  await browser.close();
  console.log(`${made} afbeeldingen gemaakt in ${OUT}`);
}

module.exports = { run, NOW };
