// De boodschappenlijst onder het weekmenu en de controlelijst voor het mandje. Sinds 4.11.0 een eigen script (daarvoor in
// app.js), omdat het weekmenu nu onthoudt wat je doet:
//   - de boodschappenlijst is een aanvinklijst: het vinkje is "deze week kopen", het huisje "altijd in huis" (per gebruiker
//     bewaard, /api/pantry). Wat in huis is, staat apart en gaat de zoekronde niet in;
//   - je kiest voor welke dagen je boodschappen doet; in de lopende week tellen dagen die voorbij zijn standaard niet mee;
//   - de controlelijst zoekt in groepen en laat zien hoe ver hij is, zet wat je moet nakijken bovenaan, telt een geschat totaal
//     en blijft staan als je herlaadt of een week bladert (sessionStorage);
//   - een product dat je zelf koos in plaats van het voorstel, onthoudt de server (`remember` bij het toevoegen): de volgende
//     controlelijst zet het vooraan, met "Eerder gekozen".
// Laadt na app.js: $, t, tn, th, escapeHtml, jsonFetch, message, setBusy, mdiIcon, foldText, aggregateIngredients, getWeekBounds,
// formatDate, formatPrice, cartCandidateFacts, reviewCheaperHtml, STORES, cartButtonLabel, renderStoreChoice en openLinkBlock
// komen daarvandaan.

const CART_CHUNK = 6; // zoveel ingrediënten per zoekronde: gelijk aan CART_PREVIEW_WORKERS op de server
const SHOP_STORAGE_PREFIX = "ms-shop:"; // + de eerste dag van de week: de dagen en vinkjes van die week
const REVIEW_STORAGE_KEY = "ms-cart-review";
const REVIEW_MAX_AGE_MS = 12 * 60 * 60 * 1000; // een controlelijst van gisteren heeft prijzen en aanbiedingen van gisteren
const MDI_HOME = "M10,20V14H14V20H19V12H22L12,3L2,12H5V20H10Z";
const MDI_HOME_OUTLINE = "M12 5.69L17 10.19V18H15V12H9V18H7V10.19L12 5.69M12 3L2 12H5V20H11V14H13V20H19V12H22";

const shopping = {
  pantry: new Map(), // gevouwen naam -> naam: wat je altijd in huis hebt
  pantryLoaded: false,
  week: "", // de eerste dag van de week waar dayPicks en picks bij horen
  dayPicks: {}, // dag -> true/false: wat je zelf koos; een dag zonder keuze volgt de standaard (shopDefaultDays)
  picks: {}, // gevouwen naam -> true/false: deze week toch kopen, of juist niet
  review: null, // de controlelijst: { week, store, rows, searching, stopped }
};
let reviewAbort = null; // breekt de lopende zoekronde af (Stop, Annuleren, een nieuwe lijst)

function shopRead(key) {
  try {
    return JSON.parse(window.sessionStorage.getItem(key) || "null");
  } catch {
    return null;
  }
}

function shopWrite(key, value) {
  try {
    if (value == null) window.sessionStorage.removeItem(key);
    else window.sessionStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Geen opslag, of hij is vol: de keuze geldt dan tot je de pagina herlaadt.
  }
}

// ---- De boodschappenlijst

// De zoekterm van een ingrediënt, zoals hij naar de winkel gaat: de naam uit Mealie, of de hele regel als Mealie hem niet ontleedde.
function shopQuery(ingredient) {
  return String(ingredient?.food?.name || ingredient?.display || "").trim();
}

// De naam om op te vergelijken (de voorraadkast, de vinkjes): kleine letters, zonder accenten, met enkele spaties.
function shopFold(value) {
  return foldText(value).trim().replace(/\s+/g, " ");
}

// De dagen van de week waarop een maaltijd met ingrediënten staat, oplopend.
function shopDates(entries) {
  const dates = (entries || []).filter((entry) => (entry.recipeDetail?.recipeIngredient || []).length).map((entry) => entry.date);
  return [...new Set(dates)].sort();
}

// De dagen die standaard meetellen. In de lopende week (vandaag valt erin) alleen vandaag en later: wat voorbij is, heb je al
// gegeten. Is alles voorbij, of is het een andere week, dan alle dagen.
function shopDefaultDays(dates, today, weekDays) {
  if (!weekDays.includes(today)) return dates;
  const upcoming = dates.filter((date) => date >= today);
  return upcoming.length ? upcoming : dates;
}

// De dagen waarvoor je boodschappen doet: de standaard, met wat je zelf aan- of uitzette.
function shopActiveDays(dates, today, weekDays, picks) {
  const standard = new Set(shopDefaultDays(dates, today, weekDays));
  return dates.filter((date) => (typeof picks[date] === "boolean" ? picks[date] : standard.has(date)));
}

// De regels van de lijst: per ingrediënt of het in huis is en of het deze week mee gaat.
function shopItems(ingredients, pantry, picks) {
  return ingredients.map((ingredient) => {
    const key = shopFold(shopQuery(ingredient));
    const stocked = pantry.has(key);
    return {
      key,
      ingredient,
      name: shopQuery(ingredient),
      label: ingredient.display || ingredient.food?.name || "-",
      pantry: stocked,
      checked: typeof picks[key] === "boolean" ? picks[key] : !stocked,
    };
  });
}

// De keuzes van de week die in beeld is; een andere week heeft haar eigen dagen en vinkjes.
function shopSyncWeek() {
  const week = getWeekBounds(state.weekOffset);
  if (shopping.week !== week.start) {
    const saved = shopRead(SHOP_STORAGE_PREFIX + week.start) || {};
    shopping.week = week.start;
    shopping.dayPicks = saved.days && typeof saved.days === "object" ? saved.days : {};
    shopping.picks = saved.picks && typeof saved.picks === "object" ? saved.picks : {};
  }
  return week;
}

function shopSaveWeek() {
  shopWrite(SHOP_STORAGE_PREFIX + shopping.week, { days: shopping.dayPicks, picks: shopping.picks });
}

// Wat er nu op de lijst staat: de dagen, de regels, en hoeveel maaltijden van eerdere dagen niet meetellen.
function shopNow() {
  const entries = state.entries || [];
  const week = shopSyncWeek();
  const today = localIsoDate(new Date());
  const dates = shopDates(entries);
  const active = shopActiveDays(dates, today, week.days, shopping.dayPicks);
  const chosen = entries.filter((entry) => active.includes(entry.date));
  const pastOff = week.days.includes(today)
    ? entries.filter((entry) => entry.date < today && !active.includes(entry.date) && (entry.recipeDetail?.recipeIngredient || []).length).length
    : 0;
  return { dates, active, pastOff, items: shopItems(aggregateIngredients(chosen), shopping.pantry, shopping.picks) };
}

function shopItemHtml(item) {
  const label = escapeHtml(t(item.pantry ? "shop.pantry_remove" : "shop.pantry_add", { name: item.label }));
  return `
    <li class="shop-item${item.checked ? "" : " is-off"}">
      <label class="shop-item__label">
        <input type="checkbox" data-shop-item="${escapeHtml(item.key)}"${item.checked ? " checked" : ""}>
        <span>${escapeHtml(item.label)}</span>
      </label>
      <button class="shop-item__pantry" type="button" data-shop-pantry="${escapeHtml(item.key)}" data-name="${escapeHtml(item.name)}" aria-pressed="${item.pantry}" aria-label="${label}" title="${label}">${mdiIcon(item.pantry ? MDI_HOME : MDI_HOME_OUTLINE, "")}</button>
    </li>`;
}

function shopTitle(items) {
  const count = items.filter((item) => item.checked).length;
  const key = count === items.length ? "shop.title" : "shop.title_some";
  return t(key, { count: I18N.formatNumber(count), total: I18N.formatNumber(items.length) });
}

// De kop, de knop in de paginakop en de toevoegknop volgen het aantal aangevinkte regels.
function shopUpdateCount(items) {
  const count = items.filter((item) => item.checked).length;
  const title = $("#shop-title");
  if (title) title.textContent = shopTitle(items);
  const add = $("#add-cart");
  if (add && !add.classList.contains("is-busy")) add.disabled = count === 0;
  const jump = $("#shop-jump");
  if (jump) {
    jump.hidden = !items.length;
    jump.textContent = t("shop.jump", { count: I18N.formatNumber(count) });
  }
}

function renderShoppingList({ pantryOpen = null } = {}) {
  const panel = $("#ingredients-panel");
  const { dates, active, pastOff, items } = shopNow();
  if (!dates.length) {
    panel.innerHTML = "";
    if ($("#shop-jump")) $("#shop-jump").hidden = true;
    return;
  }
  const buying = items.filter((item) => !item.pantry);
  const stocked = items.filter((item) => item.pantry);
  const open = pantryOpen ?? Boolean(panel.querySelector(".shop-pantry")?.open);
  const days = dates
    .map((date) => {
      const on = active.includes(date);
      return `<button class="offer-chip offer-chip--small${on ? " is-active" : ""}" type="button" data-shop-day="${escapeHtml(date)}" aria-pressed="${on}">${escapeHtml(formatDate(date))}</button>`;
    })
    .join("");
  panel.innerHTML = `
    <div class="box ingredients" id="shop-box">
      <h2 id="shop-title" tabindex="-1">${escapeHtml(shopTitle(items))}</h2>
      <div class="shop-days" role="group" aria-labelledby="shop-days-label">
        <span class="shop-days__label" id="shop-days-label">${escapeHtml(t("shop.for"))}</span>
        ${days}
      </div>
      ${pastOff ? `<p class="muted shop-days__note">${escapeHtml(tn("shop.days_past", pastOff))}</p>` : ""}
      ${active.length ? "" : `<p class="muted shop-days__note">${escapeHtml(t("shop.days_none"))}</p>`}
      <div id="shop-message"></div>
      ${buying.length ? `<ul class="shop-list">${buying.map(shopItemHtml).join("")}</ul>` : ""}
      ${
        stocked.length
          ? `<details class="shop-pantry"${open ? " open" : ""}>
              <summary>${escapeHtml(t("shop.pantry_group", { count: I18N.formatNumber(stocked.length) }))}</summary>
              <ul class="shop-list">${stocked.map(shopItemHtml).join("")}</ul>
            </details>`
          : ""
      }
      ${items.length ? `<p class="muted shop-hint">${escapeHtml(t("shop.hint"))}</p>` : ""}
      <div class="toolbar ingredients__actions">
        <span id="cart-store-slot"></span>
        <label class="switch-row cart-organic" title="${escapeHtml(t("cart.organic_toggle_title"))}">
          <input type="checkbox" id="cart-organic"${state.auth?.preferOrganic ? " checked" : ""}>
          <span>${escapeHtml(t("cart.organic_toggle"))}</span>
        </label>
        <button class="button" id="add-cart" type="button">${escapeHtml(cartButtonLabel())}</button>
      </div>
    </div>`;
  renderStoreChoice();
  shopUpdateCount(items);
  if (!shopping.pantryLoaded) loadPantry();
}

// De voorraadkast van deze gebruiker, een keer per sessie. Mislukt het, dan is de lijst gewoon zonder: alles staat aangevinkt.
async function loadPantry() {
  shopping.pantryLoaded = true;
  try {
    const data = await jsonFetch("/api/pantry");
    shopping.pantry = new Map((data.items || []).map((item) => [shopFold(item.name), item.name]));
  } catch {
    shopping.pantryLoaded = false;
    return;
  }
  if (shopping.pantry.size && state.entries) renderShoppingList();
}

// Een ingrediënt in de voorraadkast zetten of eruit halen. De lijst volgt meteen; lukt het bewaren niet, dan gaat het terug.
async function setPantry(key, name, present) {
  const before = new Map(shopping.pantry);
  if (present) shopping.pantry.set(key, name);
  else shopping.pantry.delete(key);
  delete shopping.picks[key]; // de regel volgt weer de standaard: in huis is uit, niet in huis is aan
  shopSaveWeek();
  renderShoppingList({ pantryOpen: present ? true : null });
  document.querySelector(`[data-shop-pantry="${CSS.escape(key)}"]`)?.focus();
  try {
    const data = await jsonFetch("/api/pantry", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, present }),
    });
    shopping.pantry = new Map((data.items || []).map((item) => [shopFold(item.name), item.name]));
  } catch (error) {
    shopping.pantry = before;
    renderShoppingList();
    message("#shop-message", error.message);
  }
}

$("#ingredients-panel").addEventListener("click", (event) => {
  const day = event.target.closest("[data-shop-day]");
  if (day) {
    shopping.dayPicks[day.dataset.shopDay] = day.getAttribute("aria-pressed") !== "true";
    shopSaveWeek();
    renderShoppingList();
    document.querySelector(`[data-shop-day="${CSS.escape(day.dataset.shopDay)}"]`)?.focus();
    return;
  }
  const pantry = event.target.closest("[data-shop-pantry]");
  if (pantry) {
    setPantry(pantry.dataset.shopPantry, pantry.dataset.name, pantry.getAttribute("aria-pressed") !== "true");
    return;
  }
  if (event.target.closest("#add-cart")) addToCart();
});

$("#ingredients-panel").addEventListener("change", (event) => {
  if (event.target.id === "cart-organic") {
    savePreferOrganic(event.target);
    return;
  }
  const key = event.target.dataset?.shopItem;
  if (key === undefined) return;
  shopping.picks[key] = event.target.checked;
  shopSaveWeek();
  // Hetzelfde ingrediënt kan twee regels hebben (200 g en 2 el): ze gaan samen mee of samen niet.
  document.querySelectorAll(`[data-shop-item="${CSS.escape(key)}"]`).forEach((box) => {
    box.checked = event.target.checked;
    box.closest(".shop-item").classList.toggle("is-off", !box.checked);
  });
  shopUpdateCount(shopNow().items);
});

$("#shop-jump").addEventListener("click", () => {
  const title = $("#shop-title");
  if (!title) return;
  title.scrollIntoView({ block: "start" });
  title.focus({ preventScroll: true });
});

// ---- De controlelijst

function cartSelection(row, candidate, quantity) {
  return {
    query: row.query,
    productId: candidate.productId,
    quantity,
    title: candidate.title,
    price: candidate.price,
    unitSize: candidate.unitSize,
    image: candidate.image,
  };
}

// De winkel waarvoor de open controlelijst is gezocht ("" als er geen open staat). Zoeken en toevoegen vanuit de lijst gaan
// altijd naar die winkel, ook als de winkelkeuze erboven intussen iets anders zegt (een koppeling die wegvalt, een andere tab).
function cartReviewStore() {
  const store = document.querySelector("#cart-result .cart-review")?.dataset.store || "";
  return STORES[store] ? store : "";
}

function cartReviewApi() {
  return (STORES[cartReviewStore()] || currentStore()).api;
}

// Een regel van de controlelijst voor een ingrediënt dat nog gezocht wordt.
function reviewNewRow(item) {
  return { query: item.query, display: item.display, quantity: item.quantity, unit: item.unit, state: "pending", candidates: [], pick: 0, packs: 1, include: true, searched: false, attention: false };
}

// Het antwoord van de winkel in de regel zetten. `attention` blijft daarna staan: een regel verspringt niet van groep als je
// een ander product kiest.
function reviewFill(row, found, { searched = false } = {}) {
  row.candidates = found?.candidates || [];
  row.error = found?.error || "";
  row.organic = Boolean(found?.organic);
  row.organicMissing = Boolean(found?.organicMissing);
  row.rememberedMissing = found?.rememberedMissing || "";
  row.pick = 0;
  row.packs = row.candidates[0]?.packs || 1;
  row.state = row.candidates.length ? "found" : "missing";
  row.skipped = false; // er is gezocht, ook als er niets gevonden is
  row.searched = searched;
  if (!searched) row.attention = !row.candidates.length || Boolean(row.candidates[0].doubt) || Boolean(row.rememberedMissing);
  row.open = !searched && row.candidates.length > 1 && Boolean(row.candidates[0].doubt); // bij twijfel staan de opties er meteen
  return row;
}

function reviewGroup(row) {
  return row.state === "pending" ? "pending" : row.attention ? "attention" : "fine";
}

// Het geschatte bedrag van wat aangevinkt is: prijs maal verpakkingen, zonder acties zoals 2 voor de prijs van 1.
function reviewTotal(rows) {
  let total = 0;
  for (const row of rows) {
    if (!row.include || !row.candidates.length) continue;
    const price = Number(row.candidates[row.pick]?.price);
    if (Number.isFinite(price)) total += price * row.packs;
  }
  return Math.round(total * 100) / 100;
}

// Wat er naar het mandje gaat. `remember`: je koos zelf een ander product dan het eerste (het voorstel of je eerdere keuze), of
// zocht het zelf; de server onthoudt dat bij het ingrediënt (`query` blijft dus het ingrediënt, ook na zelf zoeken).
function reviewSelection(rows) {
  return rows
    .filter((row) => row.include && row.candidates.length)
    .map((row) => {
      const packs = Math.min(99, Math.max(1, parseInt(row.packs, 10) || 1));
      const selection = cartSelection(row, row.candidates[row.pick], packs);
      if (row.searched || row.pick !== 0) selection.remember = true;
      return selection;
    });
}

function reviewCounts(rows) {
  const found = rows.filter((row) => row.candidates.length);
  return {
    total: rows.length,
    pending: rows.filter((row) => row.state === "pending").length,
    skipped: rows.filter((row) => row.skipped).length, // niet gezocht: gestopt of mislukt
    missing: rows.filter((row) => row.state === "missing").length,
    included: found.filter((row) => row.include).length,
    doubts: found.filter((row) => row.candidates[row.pick].doubt).length,
    attention: rows.filter((row) => reviewGroup(row) === "attention").length,
    fine: rows.filter((row) => reviewGroup(row) === "fine").length,
  };
}

function reviewSearchHtml(row) {
  // Zoekveld bij een regel: bij een niet gevonden regel staat het er direct, bij een gevonden regel onder de opties (Wijzig),
  // voor als het product dat je zoekt er niet tussen staat (paksoi heet bij AH pak choi).
  return `
    <form class="review-search">
      <input type="search" class="review-search-input" value="${escapeHtml(row.term || row.query)}" aria-label="${escapeHtml(t("cart.search_label"))}" enterkeyhint="search">
      <button class="button button--secondary" type="submit">${escapeHtml(t("cart.search_button"))}</button>
      <small class="review-search-status" role="status"></small>
    </form>`;
}

function reviewThumbHtml(candidate) {
  // De foto van het gekozen product, zodat je ziet wat er in het mandje komt; Picnic heeft geen productpagina, dus daar
  // is dit de enige manier om het product te zien. Tikken opent hem in het groot (openReviewPhoto). De knop blijft
  // altijd staan, zodat de kolommen gelijk blijven als er geen foto is of hij niet laadt; dan is hij uitgeschakeld.
  const label = escapeHtml(t("cart.photo_open", { title: candidate.title }));
  const src = candidate.image ? ` src="${escapeHtml(storeImage(candidate.image))}"` : " hidden";
  return `<button class="review-thumb" type="button" aria-label="${label}" title="${label}"${candidate.image ? "" : " disabled"}><img${src} alt="" width="56" height="56" loading="lazy"></button>`;
}

function openReviewPhoto(candidate) {
  // De foto in het groot. Het grote formaat (imageLarge) geven AH en Picnic mee; laadt dat niet, of is er geen, dan
  // valt het venster terug op de gewone foto.
  const image = $("#photo-dialog-image");
  const large = candidate.imageLarge || candidate.image;
  if (!large) return;
  image.dataset.fallback = large !== candidate.image ? storeImage(candidate.image) : "";
  image.hidden = false;
  image.src = storeImage(large);
  $("#photo-dialog-title").textContent = candidate.title;
  $("#photo-dialog-meta").textContent = [candidate.unitSize, formatPrice(candidate.price)].filter(Boolean).join(" · ");
  $("#photo-dialog").showModal();
}

// De opties van een regel: keuzerondjes met foto, naam en dezelfde feiten als voorheen in de keuzelijst, en het zoekveld.
function reviewOptionsHtml(row, index) {
  const options = row.candidates
    .map((candidate, at) => {
      const image = candidate.image
        ? `<img class="review-option__image" src="${escapeHtml(storeImage(candidate.image))}" alt="" width="40" height="40" loading="lazy" data-hide-on-error>`
        : '<span class="review-option__image" aria-hidden="true"></span>';
      const tags = [candidate.remembered ? t("cart.remembered") : "", candidate.suggested ? t("cart.suggested") : ""].filter(Boolean);
      return `
        <label class="review-option${at === row.pick ? " is-chosen" : ""}">
          <input type="radio" name="review-pick-${index}" value="${at}"${at === row.pick ? " checked" : ""}>
          ${image}
          <span class="review-option__text">
            <strong>${escapeHtml(candidate.title)}</strong>
            <small>${escapeHtml([...cartCandidateFacts(candidate), ...tags].join(" · "))}</small>
          </span>
        </label>`;
    })
    .join("");
  return `
    <div class="review-options">
      <fieldset class="review-options__list">
        <legend class="visually-hidden">${escapeHtml(t("cart.options", { ingredient: row.display }))}</legend>
        ${options}
      </fieldset>
      ${reviewSearchHtml(row)}
    </div>`;
}

function reviewRowHtml(row, index) {
  if (row.state === "pending") {
    return `
      <div class="review-row is-pending" data-row="${index}">
        ${mdiIcon(MDI_LOADING, "review-pending__icon")}
        <div class="review-ingredient">
          <strong>${escapeHtml(row.display)}</strong>
          <small class="review-note">${escapeHtml(t("cart.pending"))}</small>
        </div>
      </div>`;
  }
  if (!row.candidates.length) {
    // Niet gevonden (of niet gezocht): de gebruiker zoekt zelf verder, bijvoorbeeld onder een andere naam.
    return `
      <div class="review-row is-missing" data-row="${index}">
        <input type="checkbox" disabled aria-label="${escapeHtml(t("cart.include"))}">
        <div class="review-ingredient">
          <strong>${escapeHtml(row.display)}</strong>
          <small class="review-note">${escapeHtml(row.error || t("cart.not_found"))}</small>
        </div>
        ${reviewSearchHtml(row)}
      </div>`;
  }
  const candidate = row.candidates[row.pick];
  const stepLabel = (key) => escapeHtml(t(key, { title: candidate.title }));
  const link = candidate.url
    ? `<a class="review-link" href="${escapeHtml(candidate.url)}" target="_blank" rel="noopener noreferrer" title="${escapeHtml(t("cart.view_title"))}">${escapeHtml(t("cart.view"))} ${ICON_EXTERNAL}</a>`
    : '<span class="review-link"></span>';
  return `
    <div class="review-row${candidate.doubt ? " is-doubt" : ""}${row.open ? " is-open" : ""}" data-row="${index}">
      <input type="checkbox" class="review-include"${row.include ? " checked" : ""} aria-label="${escapeHtml(t("cart.include"))}">
      <div class="review-ingredient">
        <strong>${escapeHtml(row.display)}</strong>
        ${candidate.remembered ? `<small class="review-remembered"><span class="review-remembered__tag">${escapeHtml(t("cart.remembered"))}</span> <button class="link-button review-forget" type="button">${escapeHtml(t("cart.forget"))}</button></small>` : ""}
        ${row.rememberedMissing ? `<small class="review-note">${escapeHtml(t("cart.remembered_missing", { title: row.rememberedMissing }))}</small>` : ""}
        <small class="review-note">${escapeHtml(candidate.doubt || "")}</small>
        ${row.organicMissing ? `<small class="review-organic-missing">${escapeHtml(t("cart.organic_missing"))}</small>` : ""}
        <small class="review-cheaper" role="status">${reviewCheaperHtml(row, row.pick)}</small>
      </div>
      ${reviewThumbHtml(candidate)}
      <button class="review-product review-chosen" type="button" aria-expanded="${Boolean(row.open)}" aria-label="${escapeHtml(t("cart.change_label", { ingredient: row.display, title: candidate.title }))}">
        <span class="review-chosen__text">
          <strong>${escapeHtml(candidate.title)}</strong>
          <small>${escapeHtml(cartCandidateFacts(candidate).join(" · "))}</small>
        </span>
        <span class="review-chosen__change" aria-hidden="true">${escapeHtml(t(row.open ? "cart.change_done" : "cart.change"))}</span>
      </button>
      ${link}
      <div class="qty review-qty">
        <button type="button" class="qty__step" data-review-step="-1" aria-label="${stepLabel("list.qty_minus")}">${mdiIcon(MDI_MINUS)}</button>
        <input class="qty__input" type="number" inputmode="numeric" min="1" max="99" step="1" value="${escapeHtml(row.packs)}" aria-label="${escapeHtml(t("cart.packs"))}">
        <button type="button" class="qty__step" data-review-step="1" aria-label="${stepLabel("list.qty_plus")}">${mdiIcon(MDI_PLUS)}</button>
      </div>
      ${row.open ? reviewOptionsHtml(row, index) : ""}
    </div>`;
}

function reviewRowEl(index) {
  return document.querySelector(`#cart-result .review-row[data-row="${index}"]`);
}

// Een regel in zijn groep zetten, op zijn plek in de volgorde van de boodschappenlijst.
function reviewPlace(index) {
  const row = shopping.review.rows[index];
  reviewRowEl(index)?.remove();
  const list = document.querySelector(`#review-${reviewGroup(row)} .review-list`);
  if (!list) return; // de lijst is uit beeld (een andere week): hij wordt getekend als je terugkomt
  const next = [...list.children].find((el) => Number(el.dataset.row) > index);
  if (next) next.insertAdjacentHTML("beforebegin", reviewRowHtml(row, index));
  else list.insertAdjacentHTML("beforeend", reviewRowHtml(row, index));
}

// Een regel opnieuw tekenen waar hij staat; `focus` is een selector binnen de regel.
function reviewRedraw(index, focus = "") {
  const el = reviewRowEl(index);
  if (!el) return;
  el.outerHTML = reviewRowHtml(shopping.review.rows[index], index);
  if (focus) reviewRowEl(index)?.querySelector(focus)?.focus();
}

// De controlelijst bewaren voor herladen en terugbladeren: zonder de vlag "zoekt nog" en zonder wat alleen het scherm aangaat.
function reviewSave() {
  const review = shopping.review;
  if (!review) {
    shopWrite(REVIEW_STORAGE_KEY, null);
    return;
  }
  shopWrite(REVIEW_STORAGE_KEY, { week: review.week, store: review.store, rows: review.rows.map(({ open, ...row }) => row), savedAt: Date.now() });
}

// De koppen, de voortgang, het totaal en de toevoegknop volgen de regels.
function reviewSummary() {
  const review = shopping.review;
  if (!review) return;
  reviewSave();
  if (!$("#confirm-cart")) return; // uit beeld: bewaard, niet getekend
  const counts = reviewCounts(review.rows);
  for (const [group, key] of [["attention", "cart.group_attention"], ["fine", "cart.group_fine"], ["pending", "cart.group_pending"]]) {
    const section = $(`#review-${group}`);
    section.hidden = counts[group] === 0;
    section.querySelector("h3").textContent = t(key, { count: I18N.formatNumber(counts[group]) });
  }
  $("#review-intro").textContent = counts.doubts ? tn("cart.doubts", counts.doubts) : t("cart.review_intro");
  const hint = $("#review-missing-hint");
  hint.hidden = counts.missing === 0;
  if (counts.missing) hint.textContent = tn("cart.missing_hint", counts.missing);
  const done = counts.total - counts.pending - counts.skipped;
  const progress = $("#review-progress");
  progress.hidden = !review.searching && !review.stopped;
  $("#review-progress-text").textContent = t(review.searching ? "cart.progress" : "cart.stopped", { done: I18N.formatNumber(done), total: I18N.formatNumber(counts.total) });
  $("#review-stop").hidden = !review.searching;
  const total = reviewTotal(review.rows);
  $("#review-total").textContent = total ? t("cart.total", { total: formatPrice(total) }) : "";
  const confirm = $("#confirm-cart");
  if (!confirm.classList.contains("is-busy")) {
    confirm.disabled = counts.included === 0 || review.searching;
    confirm.textContent = tn("cart.confirm", counts.included);
  }
}

function renderCartReview() {
  const review = shopping.review;
  if (!review) return;
  const group = (name) => `<section class="review-group" id="review-${name}" hidden><h3></h3><div class="review-list"></div></section>`;
  $("#cart-result").innerHTML = `
    <div class="box cart-review" data-store="${escapeHtml(review.store)}">
      <h2 id="cart-review-title" tabindex="-1">${escapeHtml(t("cart.review_title"))}</h2>
      <p class="muted" id="review-intro"></p>
      <p class="muted" id="review-missing-hint" hidden></p>
      <div class="review-progress" id="review-progress" hidden>
        <span id="review-progress-text" role="status"></span>
        <button class="button button--secondary" id="review-stop" type="button">${escapeHtml(t("cart.stop"))}</button>
      </div>
      ${group("attention")}${group("fine")}${group("pending")}
      <div class="cart-review__actions">
        <span class="cart-review__total" id="review-total" title="${escapeHtml(t("cart.total_title"))}"></span>
        <button class="button" id="confirm-cart" type="button"></button>
        <button class="button button--secondary" id="cancel-cart" type="button">${escapeHtml(t("common.cancel"))}</button>
      </div>
    </div>`;
  review.rows.forEach((row, index) => reviewPlace(index));
  reviewSummary();
}

// De controlelijst wegdoen: de lopende zoekronde stopt, en de bewaarde kopie gaat mee.
function discardCartReview() {
  reviewAbort?.abort();
  reviewAbort = null;
  shopping.review = null;
  reviewSave();
  $("#cart-result").innerHTML = "";
}

// Na het tekenen van het weekmenu: de controlelijst van deze week terugzetten, uit het geheugen (een week gebladerd) of uit de
// opslag (herladen). Een regel die nog gezocht werd, is dan niet gezocht; je zoekt hem per regel alsnog.
function restoreCartReview() {
  const week = getWeekBounds(state.weekOffset).start;
  if (!shopping.review) {
    const saved = shopRead(REVIEW_STORAGE_KEY);
    if (!saved || !Array.isArray(saved.rows) || !STORES[saved.store] || Date.now() - Number(saved.savedAt || 0) > REVIEW_MAX_AGE_MS) return;
    shopping.review = { week: saved.week, store: saved.store, rows: saved.rows, searching: false, stopped: false };
  }
  const review = shopping.review;
  if (review.week !== week) return; // de lijst van een andere week: die staat er weer als je terugbladert
  if (!review.searching) {
    for (const row of review.rows) {
      if (row.state === "pending") Object.assign(row, { state: "missing", attention: true, skipped: true, error: t("cart.not_searched") });
    }
  }
  renderCartReview();
}

function showCartResult(data, notFound, store = state.store) {
  const lines = (data.items || [])
    .map((item) => {
      const size = item.product.unitSize ? ` (${escapeHtml(item.product.unitSize)})` : "";
      return `<li>${escapeHtml(item.quantity)}x ${escapeHtml(item.product.title)}${size}</li>`;
    })
    .join("");
  // Picnic, Jumbo en Crisp melden zelf ook welke producten ze niet in het mandje kregen (of al op het lijstje stonden).
  const names = store === "ah" ? notFound : [...notFound, ...(data.skippedItems || [])];
  const missing = names.length ? th("cart.missing", { count: names.length, names: names.join(", ") }) : "";
  const ready = STORES[store].ready;
  $("#cart-result").innerHTML = `
    <div class="message success" role="status">${th(ready, { added: data.added }).replace("{missing}", missing)}</div>
    ${lines ? `<ul class="cart-lines">${lines}</ul>` : ""}
    <p><button class="button button--secondary" id="view-basket" type="button">${escapeHtml(t("basket.view"))}</button></p>
  `;
  // Bij elke winkel: waar je het mandje kunt bekijken (bij Picnic en Crisp ook aanpassen). Tot 4.10.4 ontbrak de knop bij AH.
  $("#view-basket").addEventListener("click", () => openBasket(store));
}

async function submitCart(selection, notFound) {
  const store = cartReviewStore() || state.store;
  const data = await jsonFetch(`/api/${STORES[store].api}/cart`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ items: selection }),
  });
  // Het staat in het mandje: de controlelijst is klaar, ook de bewaarde kopie.
  shopping.review = null;
  reviewSave();
  showCartResult(data, notFound, store);
}

// Zoekt één regel opnieuw met de zoekterm uit het veld, voor een niet gevonden regel en voor een gevonden regel waar je een
// ander product wilt. Alleen deze regel verandert; vindt de zoekopdracht niets, dan houdt de regel zijn huidige producten.
async function reviewSearch(index) {
  const review = shopping.review;
  const row = review.rows[index];
  const el = reviewRowEl(index);
  const input = el.querySelector(".review-search-input");
  const button = el.querySelector(".review-search button");
  const status = el.querySelector(".review-search-status");
  const term = input.value.trim();
  if (!term) return;
  input.disabled = true;
  button.disabled = true;
  status.textContent = t("cart.searching");
  try {
    const preview = await jsonFetch(`/api/${STORES[review.store].api}/cart/preview`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items: [{ query: term, display: row.display, quantity: row.quantity ?? null, unit: row.unit ?? null }] }),
    });
    if (shopping.review !== review) return;
    const found = (preview.items || [])[0];
    if (found?.candidates?.length) {
      reviewFill(row, found, { searched: true });
      row.term = term; // de zoekterm in het veld; `query` blijft het ingrediënt, waar de keuze bij onthouden wordt
      reviewRedraw(index, ".review-chosen");
      reviewSummary();
      return;
    }
    status.textContent = found?.error || t("cart.search_none", { term });
  } catch (error) {
    status.textContent = error.message;
  }
  input.disabled = false;
  button.disabled = false;
  input.focus();
}

// De keuze bij dit ingrediënt vergeten: het product blijft in deze lijst gekozen, maar staat de volgende keer niet meer vooraan.
async function reviewForget(index, button) {
  const review = shopping.review;
  const row = review.rows[index];
  button.disabled = true;
  try {
    await jsonFetch("/api/cart/choices/forget", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ store: review.store, query: row.query }),
    });
  } catch (error) {
    button.disabled = false;
    message("#shop-message", error.message);
    return;
  }
  if (shopping.review !== review) return;
  for (const candidate of row.candidates) delete candidate.remembered;
  reviewRedraw(index, ".review-chosen");
  reviewSummary();
}

async function reviewConfirm(button) {
  const review = shopping.review;
  const selection = reviewSelection(review.rows);
  if (!selection.length) return;
  setBusy(button, true, t("cart.adding"));
  try {
    await submitCart(selection, review.rows.filter((row) => !row.candidates.length).map((row) => row.display));
  } catch (error) {
    setBusy(button, false);
    $("#cart-result > .message.error")?.remove(); // een tweede poging stapelt geen foutvakken
    $("#cart-result").insertAdjacentHTML("afterbegin", `<div class="message error" role="alert">${escapeHtml(error.message)}</div>`);
    // De knop staat in de balk onderaan; de melding staat boven de lijst en moet dus in beeld komen.
    $("#cart-result .message.error").scrollIntoView({ block: "center" });
    reviewSummary();
  }
}

$("#cart-result").addEventListener("click", (event) => {
  const review = shopping.review;
  if (!review) return;
  if (event.target.closest("#review-stop")) {
    review.stopped = true;
    reviewAbort?.abort();
    return;
  }
  if (event.target.closest("#cancel-cart")) {
    // Twintig regels nalopen en dan per ongeluk Annuleren: eerst vragen (sinds 4.10.4).
    confirmDialog(t("cart.cancel_confirm"), { danger: true, confirm: t("cart.cancel_yes") }).then((sure) => {
      if (sure && shopping.review === review) discardCartReview();
    });
    return;
  }
  const confirm = event.target.closest("#confirm-cart");
  if (confirm) {
    reviewConfirm(confirm);
    return;
  }
  const el = event.target.closest(".review-row[data-row]");
  if (!el) return;
  const index = Number(el.dataset.row);
  const row = review.rows[index];
  if (event.target.closest(".review-chosen")) {
    row.open = !row.open;
    reviewRedraw(index, row.open ? ".review-option input:checked" : ".review-chosen");
    return;
  }
  const pick = event.target.closest("[data-pick]");
  if (pick) {
    // "Kies": de goedkoopste optie per eenheid, alsof je hem bij de opties kiest (ook het aantal verpakkingen rekent mee).
    row.pick = Number(pick.dataset.pick);
    row.packs = row.candidates[row.pick].packs || 1;
    reviewRedraw(index, ".review-chosen");
    reviewSummary();
    return;
  }
  const step = event.target.closest("[data-review-step]");
  if (step) {
    row.packs = Math.min(99, Math.max(1, (parseInt(row.packs, 10) || 1) + Number(step.dataset.reviewStep)));
    el.querySelector(".qty__input").value = row.packs;
    reviewSummary();
    return;
  }
  if (event.target.closest(".review-thumb")) {
    openReviewPhoto(row.candidates[row.pick]);
    return;
  }
  const forget = event.target.closest(".review-forget");
  if (forget) {
    reviewForget(index, forget);
    return;
  }
  const link = event.target.closest("a.review-link");
  if (link) {
    event.preventDefault();
    openProductPopup(link.href);
  }
});

$("#cart-result").addEventListener("change", (event) => {
  const review = shopping.review;
  const el = event.target.closest(".review-row[data-row]");
  if (!review || !el) return;
  const index = Number(el.dataset.row);
  const row = review.rows[index];
  if (event.target.classList.contains("review-include")) {
    row.include = event.target.checked;
    reviewSummary();
  } else if (event.target.matches(".review-option input")) {
    // Een ander product: het aantal verpakkingen rekent mee. De opties blijven open, zodat de pijltjestoetsen blijven werken.
    row.pick = Number(event.target.value);
    row.packs = row.candidates[row.pick].packs || 1;
    reviewRedraw(index, ".review-option input:checked");
    reviewSummary();
  } else if (event.target.classList.contains("qty__input")) {
    row.packs = Math.min(99, Math.max(1, parseInt(event.target.value, 10) || 1));
    event.target.value = row.packs;
    reviewSummary();
  }
});

$("#cart-result").addEventListener("submit", (event) => {
  const el = event.target.closest(".review-row[data-row]");
  if (!shopping.review || !el || !event.target.classList.contains("review-search")) return;
  event.preventDefault();
  reviewSearch(Number(el.dataset.row));
});

// Een foto die niet laadt: verbergen (niet weghalen), een ander product kiezen gebruikt hem opnieuw.
$("#cart-result").addEventListener(
  "error",
  (event) => {
    const thumb = event.target.closest?.(".review-thumb");
    if (!thumb) return;
    event.target.hidden = true;
    thumb.disabled = true;
  },
  true
);

// Albert Heijn staat altijd in de winkelkeuze, ook zonder koppeling: zoeken kan dan wel, toevoegen niet. Tot 4.10.4 liep je
// daar pas tegenaan na het nalopen van de hele controlelijst. Nu zegt het weekmenu het vooraf, met de weg naar Koppelen.
function cartStoreUnlinkedHtml() {
  if (state.store !== "ah" || state.ahConnected !== false) return "";
  const action = isRegularUser()
    ? `<span>${escapeHtml(t("cart.ah_ask_admin"))}</span>`
    : `<button class="button inline-button" type="button" id="cart-link-store">${escapeHtml(t("cart.ah_link"))}</button>`;
  return `<div class="message warning message--action" role="alert"><span>${escapeHtml(t("cart.ah_unlinked"))}</span>${action}</div>`;
}

// Het weekmenu naar het mandje: de aangevinkte ingrediënten in groepen zoeken. Elke groep staat meteen in de controlelijst, met
// de stand erboven (12 van 25 gezocht) en Stop. Wat bij Stop of een fout nog niet gezocht is, blijft staan als "niet gezocht":
// dat zoek je per regel alsnog. Het mandje gaat nooit zonder controle vol.
async function addToCart() {
  const items = shopNow().items.filter((item) => item.checked);
  if (!items.length) return;
  const unlinked = cartStoreUnlinkedHtml();
  if (unlinked) {
    discardCartReview();
    $("#cart-result").innerHTML = unlinked;
    $("#cart-link-store")?.addEventListener("click", () => openLinkBlock("ah"));
    return;
  }
  discardCartReview();
  const requests = items.map(({ ingredient, name }) => ({
    query: name,
    display: ingredient.display || ingredient.food?.name || "",
    // Het aantal verpakkingen rekent de server uit met de verpakkingsgrootte van de winkel.
    quantity: ingredient.quantity ?? null,
    unit: ingredient.unit?.name || ingredient.unit?.abbreviation || null,
  }));
  const review = { week: getWeekBounds(state.weekOffset).start, store: state.store, rows: requests.map(reviewNewRow), searching: true, stopped: false };
  const controller = new AbortController();
  shopping.review = review;
  reviewAbort = controller;
  const button = $("#add-cart");
  setBusy(button, true, t("cart.searching"));
  renderCartReview();
  $("#cart-review-title").scrollIntoView({ block: "start" });
  let failure = "";
  try {
    for (let at = 0; at < requests.length; at += CART_CHUNK) {
      const preview = await jsonFetch(`/api/${STORES[review.store].api}/cart/preview`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: controller.signal,
        body: JSON.stringify({
          // Vergelijk de verpakkingen: na het voorgestelde product alleen hetzelfde product of wat per eenheid goedkoper is.
          compare: true,
          // Liever biologisch (#218): eerst een biologisch product van dezelfde soort, als de winkel het heeft.
          organic: Boolean(state.auth?.preferOrganic),
          items: requests.slice(at, at + CART_CHUNK),
        }),
      });
      if (shopping.review !== review) return; // intussen weggedaan of vervangen
      (preview.items || []).forEach((found, offset) => {
        const index = at + offset;
        if (!review.rows[index]) return;
        reviewFill(review.rows[index], found);
        reviewPlace(index);
      });
      reviewSummary();
    }
  } catch (error) {
    if (shopping.review !== review) return;
    if (error.name !== "AbortError") failure = error.message;
  } finally {
    const add = $("#add-cart"); // de lijst kan intussen opnieuw getekend zijn: de knop van nu
    setBusy(add, false);
    if (add) add.textContent = cartButtonLabel();
    if (reviewAbort === controller) reviewAbort = null;
  }
  // Mislukt voordat er iets gezocht is (geen verbinding, de winkel weigert): één melding, geen lijst met twintig keer dezelfde fout.
  if (failure && review.rows.every((row) => row.state === "pending")) {
    discardCartReview();
    $("#cart-result").innerHTML = `<div class="message error" role="alert">${escapeHtml(failure)}</div>`;
    shopUpdateCount(shopNow().items);
    return;
  }
  // Gestopt of halverwege mislukt: wat nog niet gezocht is, wordt een regel met een zoekveld; de fout staat een keer boven de lijst.
  review.searching = false;
  review.rows.forEach((row, index) => {
    if (row.state !== "pending") return;
    Object.assign(row, { state: "missing", attention: true, skipped: true, error: t("cart.not_searched") });
    reviewPlace(index);
  });
  if (failure) {
    review.stopped = true;
    $("#cart-result .cart-review")?.insertAdjacentHTML("beforebegin", `<div class="message error" role="alert">${escapeHtml(failure)}</div>`);
  }
  reviewSummary();
  shopUpdateCount(shopNow().items);
}
