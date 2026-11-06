// Gebruik: start een lege MealieShopper met PASSKEY_AUTH_ENABLED=false (zie README, "Screenshots"), zet de Python-omgeving
// van de app op het PATH (het consent-scenario gebruikt Quart) en draai
//   SHOT_URL=http://localhost:8000 node scripts/screenshots/index.js [--only plan,dark] [--lang nl]
const fs = require("fs");
const path = require("path");
const { run } = require("./lib");
const { base } = require("./demo");

const scenarios = fs
  .readdirSync(path.join(__dirname, "scenarios"))
  .filter((file) => file.endsWith(".js"))
  .sort()
  .flatMap((file) => require(path.join(__dirname, "scenarios", file)));

run(scenarios, base);
