// ---- Timers bij het koken (issue #309)
// In de kookmodus staan onder een stap knopjes voor de tijden die erin staan ("10-12 minuten" geeft een timer van 10 minuten),
// en een knop om zelf een timer te zetten. Er kunnen meer timers tegelijk lopen. Ze zweven rechtsonder (#cook-timers): in de
// kookmodus boven Vorige en Volgende, daarbuiten boven de menubalk; in te klappen tot alleen de tijd.
// Een timer blijft lopen bij een andere stap, als de kookmodus dicht gaat en als de pagina opnieuw laadt: de eindtijd staat
// in localStorage onder ms-cook-timers. Bij 0 klinkt een alarm (Web Audio, zonder geluidsbestand), trilt een telefoon en,
// als de app niet in beeld is, komt er een systeemmelding (Notification, via de service worker van cook_sw.js als die er is).
// Het alarm herhaalt tot je op Stop tikt (het geluid stopt na COOK_ALARM_MAX). Een browser kan een web-app op de achtergrond
// pauzeren (een vergrendelde telefoon): dan stuurt de server de melding met Web Push (issue #311, zie "Push van de server"
// onderaan en mealieshopper/push.py). Laadt na recipe_view.js.

// Material Design Icons (zie static/icons/LICENSE-mdi.txt)
const MDI_TIMER_OUTLINE = "M12,20A7,7 0 0,1 5,13A7,7 0 0,1 12,6A7,7 0 0,1 19,13A7,7 0 0,1 12,20M19.03,7.39L20.45,5.97C20,5.46 19.55,5 19.04,4.56L17.62,6C16.07,4.74 14.12,4 12,4A9,9 0 0,0 3,13A9,9 0 0,0 12,22C17,22 21,17.97 21,13C21,10.88 20.26,8.93 19.03,7.39M11,14H13V8H11M15,1H9V3H15V1Z";
const MDI_TIMER_PAUSE = "M14,19H18V5H14M6,19H10V5H6V19Z";
const MDI_TIMER_PLAY = "M8,5.14V19.14L19,12.14L8,5.14Z";
const MDI_TIMER_STOP = "M18,18H6V6H18V18Z";
const MDI_TIMER_PLUS = "M19,13H13V19H11V13H5V11H11V5H13V11H19V13Z";
const MDI_TIMER_CHEVRON = "M7.41,15.41L12,10.83L16.59,15.41L18,14L12,8L6,14L7.41,15.41Z";

const COOK_TIMERS_KEY = "ms-cook-timers";
const COOK_TIMERS_MAX = 8;
const COOK_TIMER_LIMIT = 24 * 60 * 60; // seconden; langer is geen kooktimer
const COOK_TIMER_KEEP = 10 * 60 * 1000; // een afgelopen timer blijft na herladen nog zo lang staan (en gaat dan weer af)
const COOK_ALARM_MAX = 2 * 60 * 1000; // zo lang klinkt het alarm hooguit; de timer blijft rood tot je stopt
const COOK_PUSH_KEY = "ms-cook-push"; // "1" zolang de server lopende timers van deze browser kan hebben
const COOK_PUSH_WAIT = 5000; // zo lang wachten we hooguit op de service worker, en het uitloggen op het afmelden voor push

// Getallen in woorden, voor "een kwartier", "twee uur", "ten minutes".
const COOK_NUMBER_WORDS = { een: 1, "één": 1, a: 1, an: 1, one: 1, twee: 2, two: 2, drie: 3, three: 3, vier: 4, four: 4, vijf: 5, five: 5, zes: 6, six: 6, zeven: 7, seven: 7, acht: 8, eight: 8, negen: 9, nine: 9, tien: 10, ten: 10, twintig: 20, twenty: 20, dertig: 30, thirty: 30, half: 0.5, anderhalf: 1.5, "½": 0.5, "¼": 0.25, "¾": 0.75 };
const COOK_TIME_NUMBER = "(\\d+(?:[.,]\\d+)?(?:\\s*[½¼¾])?|[½¼¾]|een|één|an?|one|twee|two|drie|three|vier|four|vijf|five|zes|six|zeven|seven|acht|eight|negen|nine|tien|ten|twintig|twenty|dertig|thirty|anderhalf|half)";
// Een tijd met een eenheid ("10 minuten", "1,5 uur", "30 sec", "2u", "10-12 min", "5 tot 7 minuten"), of een kwartier.
const COOK_TIME_RE = new RegExp(`(?<![\\p{L}\\d])${COOK_TIME_NUMBER}(?:\\s*(?:-|–|tot|to|or|of|à)\\s*${COOK_TIME_NUMBER})?\\s*(seconden|seconde|seconds|second|secs|sec|minuten|minuut|minutes|minute|mins|min|uren|uur|hours|hour|hrs|hr|u|h|kwartier)(?![\\p{L}])`, "giu");
const COOK_TIME_UNITS = { sec: 1, min: 60, uur: 3600, kwartier: 900 };

const cookTimers = { list: [], ticker: 0, endings: new Map(), audio: null, ringing: 0, ringStarted: 0, collapsed: false, seq: 0, custom: false };
const cookPush = { key: "", busy: null, again: false };

function cookTimeUnit(word) {
  const unit = String(word).toLowerCase();
  if (unit === "kwartier") return "kwartier";
  if (unit.startsWith("s")) return "sec";
  if (unit.startsWith("m")) return "min";
  return "uur";
}

function cookTimeNumber(text) {
  const value = String(text ?? "").trim().toLowerCase();
  if (value in COOK_NUMBER_WORDS) return COOK_NUMBER_WORDS[value];
  const fraction = /[½¼¾]$/.exec(value);
  const whole = parseFloat(value.replace(",", ".").replace(/[½¼¾]$/, "").trim());
  const part = fraction ? COOK_NUMBER_WORDS[fraction[0]] : 0;
  if (Number.isNaN(whole)) return fraction ? part : NaN;
  return whole + part;
}

// De tijden in een stap, als [{ seconds, text }]: de eerste waarde van een bereik ("10-12 minuten" is 10 minuten; liever te
// vroeg kijken dan te laat), "1 uur en 15 minuten" samen, zonder dubbele, hooguit drie.
function cookDurations(text) {
  const source = String(text ?? "");
  const found = [];
  for (const match of source.matchAll(COOK_TIME_RE)) {
    const unit = cookTimeUnit(match[3]);
    let amount = cookTimeNumber(match[1]);
    if (unit === "kwartier" && Number.isNaN(amount)) amount = 1;
    if (!Number.isFinite(amount) || amount <= 0) continue;
    if (/^[uh]$/i.test(match[3]) && !/^\d/.test(match[1])) continue; // "een u" is geen tijd
    const seconds = Math.round(amount * COOK_TIME_UNITS[unit]);
    if (seconds < 1 || seconds > COOK_TIMER_LIMIT) continue;
    const previous = found[found.length - 1];
    const between = previous ? source.slice(previous.end, match.index) : "";
    if (previous && previous.unit === "uur" && unit === "min" && /^\s*(?:en|and|,)?\s*$/i.test(between)) {
      previous.seconds += seconds; // "1 uur en 15 minuten"
      previous.end = match.index + match[0].length;
      previous.unit = "min";
      continue;
    }
    found.push({ seconds, unit, end: match.index + match[0].length });
  }
  const unique = [];
  for (const item of found) {
    if (!unique.some((other) => other.seconds === item.seconds)) unique.push({ seconds: item.seconds });
  }
  return unique.slice(0, 3);
}

// Een duur als tekst op een knop: "10 min", "1 u 15 min", "45 sec".
function cookDurationLabel(seconds) {
  const total = Math.max(0, Math.round(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const rest = total % 60;
  const parts = [];
  if (hours) parts.push(t("cook.timer_hours", { n: hours }));
  if (minutes) parts.push(t("cook.timer_minutes_short", { n: minutes }));
  if (rest && !hours) parts.push(t("cook.timer_seconds_short", { n: rest }));
  return parts.join(" ") || t("cook.timer_seconds_short", { n: 0 });
}

// De klok van een lopende timer: 9:05, of 1:02:30 boven een uur. Naar boven afgerond: bij 0:01 is er nog iets over.
function cookClock(ms) {
  const total = Math.max(0, Math.ceil(ms / 1000));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = String(total % 60).padStart(2, "0");
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${seconds}` : `${minutes}:${seconds}`;
}

function cookTimerLeft(timer, now = Date.now()) {
  if (timer.state === "pause") return timer.left;
  if (timer.state === "ring") return 0;
  return Math.max(0, timer.end - now);
}

// ---- Opslag
function cookTimersSave() {
  try {
    if (!cookTimers.list.length) localStorage.removeItem(COOK_TIMERS_KEY);
    else localStorage.setItem(COOK_TIMERS_KEY, JSON.stringify(cookTimers.list));
  } catch {
    // Geen opslag (privévenster, vol): de timers lopen dan alleen tot je de pagina herlaadt.
  }
}

// De timers van vorige keer: een lopende loopt door, een die intussen afliep gaat af (als dat korter dan COOK_TIMER_KEEP
// geleden is), een kapotte of oude valt weg.
function cookTimersLoad(now = Date.now()) {
  let data = [];
  try {
    data = JSON.parse(localStorage.getItem(COOK_TIMERS_KEY) || "[]");
  } catch {
    data = [];
  }
  const list = [];
  for (const item of Array.isArray(data) ? data : []) {
    if (!item || typeof item !== "object" || typeof item.id !== "string") continue;
    const total = Number(item.total);
    if (!Number.isFinite(total) || total < 1 || total > COOK_TIMER_LIMIT) continue;
    const timer = { id: item.id, total, label: String(item.label || ""), title: String(item.title || ""), key: String(item.key || ""), step: Number(item.step) || 0 };
    if (item.state === "pause" && Number.isFinite(item.left) && item.left > 0) list.push({ ...timer, state: "pause", left: item.left, end: 0 });
    else if (item.state === "run" && Number.isFinite(item.end) && item.end > now) list.push({ ...timer, state: "run", end: item.end, left: 0 });
    else if ((item.state === "run" || item.state === "ring") && Number.isFinite(item.end) && now - item.end < COOK_TIMER_KEEP) list.push({ ...timer, state: "ring", end: item.end, left: 0 });
  }
  return list.slice(0, COOK_TIMERS_MAX);
}

// ---- Bediening van de timers
function cookTimerFind(id) {
  return cookTimers.list.find((timer) => timer.id === id) || null;
}

// Een nieuwe timer. Loopt er al een voor dezelfde stap met dezelfde tijd, dan komt er geen tweede bij.
function cookTimerAdd(seconds, info = {}, now = Date.now()) {
  const total = Math.round(Number(seconds));
  if (!Number.isFinite(total) || total < 1 || total > COOK_TIMER_LIMIT) return null;
  const same = cookTimers.list.find((timer) => timer.state !== "ring" && timer.key === (info.key || "") && timer.step === (info.step || 0) && timer.total === total);
  if (same) return same;
  if (cookTimers.list.length >= COOK_TIMERS_MAX) return null;
  cookTimers.seq += 1;
  const timer = {
    id: `${now.toString(36)}-${cookTimers.seq}`,
    total,
    label: String(info.label || ""),
    title: String(info.title || ""),
    key: String(info.key || ""),
    step: Number(info.step) || 0,
    state: "run",
    end: now + total * 1000,
    left: 0,
  };
  cookTimers.list.push(timer);
  return timer;
}

function cookTimerPause(timer, now = Date.now()) {
  if (timer.state !== "run") return;
  timer.left = Math.max(0, timer.end - now);
  timer.state = "pause";
  timer.end = 0;
}

function cookTimerResume(timer, now = Date.now()) {
  if (timer.state !== "pause") return;
  timer.end = now + timer.left;
  timer.left = 0;
  timer.state = "run";
}

function cookTimerRemove(id) {
  cookTimers.list = cookTimers.list.filter((timer) => timer.id !== id);
}

// Zet afgelopen timers op "ring"; geeft de timers terug die nu net afgingen.
function cookTimersDue(now = Date.now()) {
  const due = [];
  for (const timer of cookTimers.list) {
    if (timer.state === "run" && timer.end <= now) {
      timer.state = "ring";
      due.push(timer);
    }
  }
  return due;
}

// ---- Weergave
function cookTimerName(timer) {
  const step = timer.step ? t("cook.timer_step", { n: cookFormat(timer.step) }) : "";
  return [step, cookDurationLabel(timer.total)].filter(Boolean).join(" · ");
}

function cookTimerHtml(timer, now = Date.now()) {
  const ringing = timer.state === "ring";
  const paused = timer.state === "pause";
  const name = cookTimerName(timer);
  const clock = ringing ? t("cook.timer_done") : cookClock(cookTimerLeft(timer, now));
  const toggle = ringing
    ? ""
    : `<button class="icon-button cook-timer__button" type="button" data-cook-timer-toggle="${escapeHtml(timer.id)}" aria-label="${escapeHtml(t(paused ? "cook.timer_resume" : "cook.timer_pause", { name }))}">${mdiIcon(paused ? MDI_TIMER_PLAY : MDI_TIMER_PAUSE)}</button>`;
  return `<li class="cook-timer${ringing ? " is-ringing" : ""}${paused ? " is-paused" : ""}" data-cook-timer="${escapeHtml(timer.id)}">
      <span class="cook-timer__info">
        <span class="cook-timer__name">${escapeHtml(name)}</span>
        <span class="cook-timer__clock" data-cook-timer-clock>${escapeHtml(clock)}</span>
      </span>
      ${toggle}
      <button class="icon-button cook-timer__button cook-timer__stop" type="button" data-cook-timer-stop="${escapeHtml(timer.id)}" aria-label="${escapeHtml(t("cook.timer_stop", { name }))}">${mdiIcon(MDI_TIMER_STOP)}</button>
    </li>`;
}

// De klok van de timer die het eerst afloopt, voor de ingeklapte kop.
function cookTimersNext(now = Date.now()) {
  const running = cookTimers.list.filter((timer) => timer.state === "run").map((timer) => cookTimerLeft(timer, now));
  return running.length ? cookClock(Math.min(...running)) : "";
}

function cookTimersHtml(now = Date.now()) {
  const count = cookTimers.list.length;
  const ringing = cookTimers.list.some((timer) => timer.state === "ring");
  const collapsed = cookTimers.collapsed && !ringing; // een timer die afgaat is altijd te zien
  const next = collapsed ? `<span class="cook-timers__next" data-cook-timers-next>${escapeHtml(cookTimersNext(now))}</span>` : "";
  return `<div class="cook-timers__head">
      <button class="cook-timers__toggle" type="button" data-cook-timers-collapse aria-expanded="${!collapsed}">${mdiIcon(MDI_TIMER_OUTLINE)}${next}<span>${escapeHtml(t("cook.timers_count", { n: cookFormat(count) }))}</span>${mdiIcon(MDI_TIMER_CHEVRON, "mdi cook-timers__chevron")}</button>
    </div>
    <ul class="cook-timers__list" role="list"${collapsed ? " hidden" : ""}>${cookTimers.list.map((timer) => cookTimerHtml(timer, now)).join("")}</ul>`;
}

// De knopjes onder een stap: een per tijd uit de tekst, en een om zelf een tijd te kiezen.
function cookTimerRowHtml(step) {
  const chips = cookDurations(`${step?.title || ""} ${step?.text || ""}`).map(
    (item) => `<button class="button button--secondary cook-mode__timer" type="button" data-cook-timer-start="${item.seconds}">${mdiIcon(MDI_TIMER_OUTLINE)}<span>${escapeHtml(cookDurationLabel(item.seconds))}</span></button>`
  );
  const custom = cookTimers.custom
    ? `<form class="cook-timer-new" data-cook-timer-form>
        <label><span class="visually-hidden">${escapeHtml(t("cook.timer_minutes"))}</span><input type="number" inputmode="numeric" min="1" max="600" step="1" value="5" required data-cook-timer-minutes></label>
        <span aria-hidden="true">${escapeHtml(t("cook.timer_minutes"))}</span>
        <button class="button" type="submit">${escapeHtml(t("cook.timer_go"))}</button>
        <button class="button button--secondary" type="button" data-cook-timer-custom-cancel>${escapeHtml(t("cook.link_cancel"))}</button>
      </form>`
    : `<button class="button button--secondary cook-mode__timer" type="button" data-cook-timer-custom>${mdiIcon(MDI_TIMER_PLUS)}<span>${escapeHtml(t("cook.timer_custom"))}</span></button>`;
  return chips.join("") + custom;
}

// ---- Alarm: piepjes via Web Audio. De AudioContext ontstaat bij het starten van een timer (een tik), want een browser laat
// pas geluid toe na een handeling van de gebruiker; later kan hij dan ook zonder tik afgaan.
function cookAudioReady() {
  try {
    const Context = window.AudioContext || window.webkitAudioContext;
    if (!Context) return null;
    if (!cookTimers.audio) cookTimers.audio = new Context();
    if (cookTimers.audio.state === "suspended") cookTimers.audio.resume?.();
    return cookTimers.audio;
  } catch {
    return null;
  }
}

function cookBeep() {
  const audio = cookTimers.audio;
  if (!audio) return;
  try {
    if (audio.state === "suspended") audio.resume?.();
    const start = audio.currentTime + 0.02;
    for (let index = 0; index < 3; index += 1) {
      const at = start + index * 0.25;
      const tone = audio.createOscillator();
      const gain = audio.createGain();
      tone.type = "square";
      tone.frequency.setValueAtTime(880, at);
      gain.gain.setValueAtTime(0.0001, at);
      gain.gain.exponentialRampToValueAtTime(0.25, at + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, at + 0.18);
      tone.connect(gain).connect(audio.destination);
      tone.start(at);
      tone.stop(at + 0.2);
    }
  } catch {
    // Geen geluid mogelijk: de timer staat er rood en de melding komt toch.
  }
}

function cookAlarmTick(now = Date.now()) {
  const ringing = cookTimers.list.some((timer) => timer.state === "ring");
  if (!ringing) {
    cookTimers.ringStarted = 0;
    return;
  }
  if (!cookTimers.ringStarted) cookTimers.ringStarted = now;
  if (now - cookTimers.ringStarted > COOK_ALARM_MAX) return;
  if (now - cookTimers.ringing < 1500) return;
  cookTimers.ringing = now;
  cookBeep();
  try {
    navigator.vibrate?.([300, 150, 300]);
  } catch {
    // Geen trilfunctie.
  }
}

// ---- Systeemmelding. Een service worker (cook_sw.js) kan een melding tonen op Android en in een geïnstalleerde app; zonder
// service worker probeert de pagina het zelf (desktop). Toestemming vragen we bij de eerste timer, na een tik.
function cookNotifySupported() {
  return typeof window !== "undefined" && "Notification" in window;
}

async function cookNotifyRegister() {
  try {
    if (!("serviceWorker" in navigator) || !window.isSecureContext) return null;
    const url = $("#cook-timers")?.dataset.worker;
    if (!url) return null;
    return await navigator.serviceWorker.register(url, { scope: new URL(".", new URL(url, location.href)).pathname });
  } catch {
    return null; // geen service worker (http, oude browser): dan de melding zonder
  }
}

function cookNotifyAsk() {
  if (!cookNotifySupported()) return;
  try {
    if (Notification.permission === "default") {
      Promise.resolve(Notification.requestPermission()).then((answer) => {
        if (answer === "granted") cookNotifyRegister().then(() => cookPushSync()); // de timer die net startte, ook bij de server
      }).catch(() => {});
    } else if (Notification.permission === "granted") {
      cookNotifyRegister();
    }
  } catch {
    // Geen meldingen in deze browser.
  }
}

// De tekst van de melding, in de app en in de push van de server dezelfde: welke timer, welk recept, welke stap.
function cookNotifyTitle(timer) {
  return t("cook.timer_notify_title", { name: cookDurationLabel(timer.total) });
}

function cookNotifyBody(timer) {
  return [timer.title, timer.step ? t("cook.timer_step", { n: cookFormat(timer.step) }) : ""].filter(Boolean).join(" · ");
}

async function cookNotify(timer) {
  if (!cookNotifySupported() || Notification.permission !== "granted") return;
  if (document.visibilityState === "visible" && document.hasFocus?.()) return; // in beeld: het alarm in de pagina is genoeg
  const tag = `cook-timer-${timer.id}`;
  const options = { body: cookNotifyBody(timer), tag, renotify: true, requireInteraction: true, vibrate: [300, 150, 300, 150, 300], data: { url: location.href } };
  try {
    const registration = "serviceWorker" in navigator ? await navigator.serviceWorker.getRegistration($("#cook-timers")?.dataset.worker || undefined) : null;
    if (registration?.showNotification) {
      // Kwam de push van de server al (de app was gepauzeerd), dan staat de melding er al: geen tweede.
      if ((await registration.getNotifications?.({ tag }))?.length) return;
      await registration.showNotification(cookNotifyTitle(timer), options);
      return;
    }
    new Notification(cookNotifyTitle(timer), options);
  } catch {
    // Android zonder service worker kent `new Notification` niet: dan alleen het alarm in de pagina.
  }
}

// ---- Push van de server (issue #311). Een vergrendelde telefoon pauzeert de app, en dan komt er geen alarm en geen melding.
// Daarom stuurt de app bij elke wijziging haar lopende timers met hun resttijd naar de server (PUT /api/cook/timers), samen
// met het push-abonnement van deze browser; de server stuurt bij de eindtijd een melding met Web Push (mealieshopper/push.py).
// Gaat een timer af terwijl de app draait, dan haalt ze hem meteen bij de server weg: er komt geen tweede melding. Zonder
// HTTPS, zonder push in de browser (een iPhone buiten de geïnstalleerde app) of zonder toestemming blijft alles zoals het was.
function cookPushSupported() {
  try {
    return typeof window !== "undefined" && window.isSecureContext === true && "serviceWorker" in navigator && "PushManager" in window && cookNotifySupported() && Notification.permission === "granted";
  } catch {
    return false;
  }
}

// Of de server lopende timers van deze browser kan hebben: dan moet hij ook horen dat ze gestopt zijn. Met true of false zet je het.
function cookPushPending(value) {
  try {
    if (value === true) localStorage.setItem(COOK_PUSH_KEY, "1");
    else if (value === false) localStorage.removeItem(COOK_PUSH_KEY);
    return localStorage.getItem(COOK_PUSH_KEY) === "1";
  } catch {
    return value ?? true; // zonder opslag liever een keer te veel melden dan een gestopte timer laten afgaan
  }
}

// De timers zoals de server ze nodig heeft: alleen de lopende, met hun resttijd (de klok van de server kan anders lopen dan
// die van de telefoon) en de tekst van de melding. Een gepauzeerde timer, of een die afgaat, krijgt geen push.
function cookPushTimers(now = Date.now()) {
  return cookTimers.list
    .filter((timer) => timer.state === "run" && timer.end > now)
    .map((timer) => ({ id: timer.id, left: Math.max(1, Math.ceil(timer.end - now)), title: cookNotifyTitle(timer), body: cookNotifyBody(timer) }));
}

// De publieke sleutel van de server (base64url) als bytes, voor PushManager.subscribe.
function cookPushKeyBytes(key) {
  try {
    const text = String(key || "").replaceAll("-", "+").replaceAll("_", "/");
    return Uint8Array.from(atob(text + "=".repeat((4 - (text.length % 4)) % 4)), (char) => char.charCodeAt(0));
  } catch {
    return new Uint8Array(0);
  }
}

// Of een abonnement met deze sleutel gemaakt is. Na de fabrieksinstellingen heeft de server een nieuwe: dan opnieuw aanvragen.
function cookPushSameKey(subscription, key) {
  const current = subscription?.options?.applicationServerKey;
  if (!current) return true; // de browser zegt het niet: dan houden we het
  const bytes = new Uint8Array(current);
  return bytes.length === key.length && bytes.every((value, index) => value === key[index]);
}

// Een abonnement kan pas met een actieve service worker: wacht daarop, hooguit COOK_PUSH_WAIT.
function cookWorkerActive(registration) {
  const worker = registration?.installing || registration?.waiting;
  if (!registration || registration.active || !worker) return Promise.resolve(registration);
  return new Promise((resolve) => {
    const done = () => {
      if (worker.state !== "activated" && worker.state !== "redundant") return;
      worker.removeEventListener("statechange", done);
      resolve(registration);
    };
    worker.addEventListener("statechange", done);
    setTimeout(() => resolve(registration), COOK_PUSH_WAIT);
  });
}

// Het push-abonnement van deze browser met de sleutel van de server, of null zonder service worker of push.
async function cookPushSubscription() {
  const registration = await cookWorkerActive(await cookNotifyRegister());
  if (!registration?.pushManager || !registration.active) return null;
  if (!cookPush.key) cookPush.key = String((await jsonFetch("/api/push/key")).publicKey || "");
  const key = cookPushKeyBytes(cookPush.key);
  if (key.length !== 65) return null;
  let subscription = await registration.pushManager.getSubscription();
  if (subscription && !cookPushSameKey(subscription, key)) {
    await subscription.unsubscribe().catch(() => false); // gemaakt met een oude sleutel van de server
    subscription = null;
  }
  return subscription || registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key });
}

async function cookPushSend() {
  if (!cookPushTimers().length && !cookPushPending()) return; // de server heeft niets van deze browser
  const subscription = await cookPushSubscription();
  if (!subscription) return;
  const timers = cookPushTimers(); // pas nu: een abonnement aanvragen kan even duren, en de resttijd moet kloppen
  const data = await jsonFetch("/api/cook/timers", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ subscription: subscription.toJSON(), timers, url: `${location.pathname}${location.search}` }),
  });
  cookPushPending(timers.length > 0);
  if (data?.publicKey && data.publicKey !== cookPush.key) {
    cookPush.key = data.publicKey; // de server heeft intussen een andere sleutel: nog een keer, met een nieuw abonnement
    cookPush.again = true;
  }
}

// Meld de timers bij de server. Een wijziging terwijl er al een melding onderweg is, gaat daarna. Een fout (geen verbinding,
// de push-dienst weigert) laat de timers in de app gewoon werken.
function cookPushSync() {
  if (!cookPushSupported()) return Promise.resolve(false);
  cookPush.again = true;
  if (cookPush.busy) return cookPush.busy;
  cookPush.busy = (async () => {
    try {
      while (cookPush.again) {
        cookPush.again = false;
        await cookPushSend();
      }
      return true;
    } catch {
      return false;
    } finally {
      cookPush.busy = null;
    }
  })();
  return cookPush.busy;
}

// Uitloggen (app.js): het abonnement van deze browser weg, bij de server en bij de push-dienst. De server haalt het bij het
// uitloggen ook zelf weg. Het uitloggen wacht hooguit COOK_PUSH_WAIT.
async function cookPushForget() {
  cookPushPending(false);
  const forget = (async () => {
    try {
      const url = $("#cook-timers")?.dataset.worker;
      const registration = url && "serviceWorker" in navigator ? await navigator.serviceWorker.getRegistration(url) : null;
      const subscription = await registration?.pushManager?.getSubscription();
      if (!subscription) return;
      await jsonFetch("/api/push/subscription", { method: "DELETE", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ endpoint: subscription.endpoint }) }).catch(() => null);
      await subscription.unsubscribe();
    } catch {
      // Geen push in deze browser.
    }
  })();
  await Promise.race([forget, new Promise((resolve) => setTimeout(resolve, COOK_PUSH_WAIT))]);
}

// ---- Zweven: de timers staan in het bovenste open venster (een modaal venster ligt boven de rest van de pagina), anders
// onderaan de pagina. In de kookmodus houdt de stap onderaan ruimte vrij (--cook-timers-room), zodat er niets achter valt.
function cookTimersHost() {
  const open = [...document.querySelectorAll("dialog[open]")];
  return open[open.length - 1] || document.body;
}

function cookTimersRender(now = Date.now()) {
  const tray = $("#cook-timers");
  if (!tray) return;
  const host = cookTimersHost();
  if (tray.parentElement !== host) host.append(tray);
  tray.classList.toggle("cook-timers--cook-mode", host.id === "cook-mode");
  tray.hidden = cookTimers.list.length === 0;
  if (tray.hidden) {
    tray.innerHTML = "";
  } else {
    // Alleen de klokken bijwerken als dezelfde timers er staan: dan blijft de focus op een knop staan.
    const ids = [...tray.querySelectorAll("[data-cook-timer]")].map((item) => `${item.dataset.cookTimer}:${item.className}`).join(",");
    const want = cookTimers.list.map((timer) => `${timer.id}:cook-timer${timer.state === "ring" ? " is-ringing" : ""}${timer.state === "pause" ? " is-paused" : ""}`).join(",");
    const collapsed = cookTimers.collapsed && !cookTimers.list.some((timer) => timer.state === "ring");
    const list = tray.querySelector(".cook-timers__list");
    if (ids !== want || !list || list.hidden !== collapsed) {
      tray.innerHTML = cookTimersHtml(now);
    } else {
      tray.querySelectorAll("[data-cook-timer]").forEach((item) => {
        const timer = cookTimerFind(item.dataset.cookTimer);
        const clock = item.querySelector("[data-cook-timer-clock]");
        if (timer && clock) clock.textContent = timer.state === "ring" ? t("cook.timer_done") : cookClock(cookTimerLeft(timer, now));
      });
      const next = tray.querySelector("[data-cook-timers-next]");
      if (next) next.textContent = cookTimersNext(now);
    }
  }
  const room = !tray.hidden && host.id === "cook-mode" ? Math.ceil(tray.getBoundingClientRect().height) + 12 : 0;
  $("#cook-mode")?.style.setProperty("--cook-timers-room", `${room}px`);
}

function cookTimersTick() {
  const now = Date.now();
  const due = cookTimersDue(now);
  for (const timer of due) {
    cookTimersSave();
    cookNotify(timer);
    cookTimers.ringing = 0; // meteen piepen
  }
  if (due.length) cookPushSync(); // de app draait en meldt het zelf: de server hoeft geen push meer te sturen
  cookAlarmTick(now);
  cookTimersRender(now);
  cookTimersSchedule();
}

// Een interval zolang er timers zijn, en per lopende timer een losse timeout op zijn eindtijd: een browser vertraagt
// herhaalde timers in een tabblad op de achtergrond, een losse timeout veel minder.
function cookTimersSchedule() {
  const busy = cookTimers.list.length > 0;
  if (busy && !cookTimers.ticker) cookTimers.ticker = setInterval(cookTimersTick, 500);
  if (!busy && cookTimers.ticker) {
    clearInterval(cookTimers.ticker);
    cookTimers.ticker = 0;
  }
  const running = new Set();
  for (const timer of cookTimers.list) {
    if (timer.state !== "run") continue;
    running.add(timer.id);
    const scheduled = cookTimers.endings.get(timer.id);
    if (scheduled?.end === timer.end) continue;
    if (scheduled) clearTimeout(scheduled.handle);
    cookTimers.endings.set(timer.id, { end: timer.end, handle: setTimeout(cookTimersTick, Math.max(0, timer.end - Date.now()) + 20) });
  }
  for (const [id, scheduled] of cookTimers.endings) {
    if (!running.has(id)) {
      clearTimeout(scheduled.handle);
      cookTimers.endings.delete(id);
    }
  }
}

function cookTimersChanged() {
  cookTimersSave();
  cookPushSync(); // starten, pauzeren, verder laten lopen en stoppen gaan ook naar de server
  cookTimersTick();
  if ($("#cook-mode")?.open) cookTimerRowRender();
}

// ---- De knopjes onder de stap in de kookmodus
const cookTimerStep = { recipe: null, index: -1, step: null };

function cookTimerRowRender() {
  const row = $("#cook-mode-timer-row");
  if (!row) return;
  row.innerHTML = cookTimerStep.step ? cookTimerRowHtml(cookTimerStep.step) : "";
  row.hidden = !cookTimerStep.step;
}

function cookTimerStartFromStep(seconds) {
  const { recipe, index } = cookTimerStep;
  cookAudioReady();
  cookNotifyAsk();
  const timer = cookTimerAdd(seconds, { key: recipe?.key || "", title: recipe?.title || "", step: index >= 0 ? index + 1 : 0 });
  if (!timer) {
    toast(t("cook.timer_full", { n: COOK_TIMERS_MAX }), "error");
    return;
  }
  cookTimers.collapsed = false;
  cookTimersChanged();
  // De timers zweven onderaan; de stap houdt daar ruimte vrij (scroll-padding), zodat de knopjes in beeld blijven.
  $("#cook-mode-timer-row")?.scrollIntoView?.({ block: "nearest" });
}

// De kookmodus toont een stap (ook opnieuw, na een vinkje): alleen bij een andere stap komen er nieuwe knopjes, zodat een
// half ingevulde eigen tijd blijft staan.
window.addEventListener("ms:cookstep", (event) => {
  const { recipe, index, step } = event.detail || {};
  const other = cookTimerStep.recipe !== recipe || cookTimerStep.index !== index || cookTimerStep.step !== step;
  if (!other && $("#cook-mode-timer-row")?.childElementCount) return;
  cookTimers.custom = false;
  Object.assign(cookTimerStep, { recipe: recipe || null, index: Number.isInteger(index) ? index : -1, step: step || null });
  cookTimerRowRender();
  cookTimersRender();
});

document.addEventListener("click", (event) => {
  const target = event.target instanceof Element ? event.target : null;
  if (!target) return;
  const start = target.closest("[data-cook-timer-start]");
  const toggle = target.closest("[data-cook-timer-toggle]");
  const stop = target.closest("[data-cook-timer-stop]");
  if (start) {
    cookTimerStartFromStep(Number(start.dataset.cookTimerStart));
  } else if (target.closest("[data-cook-timer-custom]")) {
    cookTimers.custom = true;
    cookTimerRowRender();
    $("#cook-mode-timer-row [data-cook-timer-minutes]")?.focus();
  } else if (target.closest("[data-cook-timer-custom-cancel]")) {
    cookTimers.custom = false;
    cookTimerRowRender();
    $("#cook-mode-timer-row [data-cook-timer-custom]")?.focus();
  } else if (toggle) {
    const timer = cookTimerFind(toggle.dataset.cookTimerToggle);
    if (timer?.state === "pause") cookTimerResume(timer);
    else if (timer) cookTimerPause(timer);
    cookAudioReady();
    cookTimersChanged();
    $(`#cook-timers [data-cook-timer-toggle="${CSS.escape(toggle.dataset.cookTimerToggle)}"]`)?.focus();
  } else if (stop) {
    cookTimerRemove(stop.dataset.cookTimerStop);
    cookTimersChanged();
    if (cookTimers.list.length) $("#cook-timers [data-cook-timer-stop]")?.focus();
    else if ($("#cook-mode")?.open) $("#cook-mode-counter")?.focus({ preventScroll: true });
  } else if (target.closest("[data-cook-timers-collapse]")) {
    cookTimers.collapsed = !cookTimers.collapsed;
    cookTimersRender();
    $("#cook-timers [data-cook-timers-collapse]")?.focus();
  }
});

document.addEventListener("submit", (event) => {
  const form = event.target instanceof HTMLFormElement && event.target.matches("[data-cook-timer-form]") ? event.target : null;
  if (!form) return;
  event.preventDefault();
  const minutes = Number(form.querySelector("[data-cook-timer-minutes]")?.value);
  if (!Number.isFinite(minutes) || minutes <= 0) return;
  cookTimers.custom = false;
  cookTimerStartFromStep(Math.round(minutes * 60));
});

// Een venster dat opent of sluit: de timers verhuizen mee (een modaal venster bedekt de rest van de pagina).
document.addEventListener("close", () => setTimeout(() => cookTimersRender()), true);
window.addEventListener("ms:languagechange", () => {
  if ($("#cook-timers")) $("#cook-timers").innerHTML = "";
  cookTimersRender();
  cookTimerRowRender();
});
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") cookTimersTick();
});

cookTimers.list = cookTimersLoad();
cookTimersSave();
cookTimersTick();
cookPushSync(); // na herladen: wat de server heeft, klopt weer met wat hier loopt
