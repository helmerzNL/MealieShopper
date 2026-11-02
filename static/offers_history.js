// Het tabblad Aanbiedingen > Prijsgeschiedenis: zoek een product en zie in een grafiek wanneer het in de aanbieding was en voor
// welke prijs, wat het normaal kostte en wat je ervoor betaalde (/api/prices/history: de aanbiedingen van elke synchronisatie, de
// gewone prijs uit zoekopdrachten en bestellingen, en je aankopen uit bestellingen en kassabonnen).
// De grafiek is inline SVG zonder bibliotheek; de tabel eronder geeft dezelfde gegevens voor schermlezers.
// Laadt na offers.js en gebruikt offersState, offersTabActive, STORES, jsonFetch, message, formatPrice en escapeHtml.

const OFFER_HISTORY_LIMIT = 30;
const OFFER_HISTORY_MIN_DAYS = 84; // de grafiek toont minstens twaalf weken
const OFFER_HISTORY_BAR = 12;
const OFFER_HISTORY_DAY = 86400000;
const OFFER_HISTORY_LABEL_HEIGHT = 14;
const OFFER_HISTORY_CHAR_WIDTH = 7; // een schatting voor de vette prijstekst van 12 px; ruim genomen, zodat labels elkaar niet raken
const offerHistory = { query: "", products: [], total: 0, selected: "", loadId: 0, timer: 0, loadedStore: "" };

function offerHistoryKey(product) {
  return `${product.store}:${product.productId}`;
}

// Een ISO-datum ("2026-10-26") als dagnummer, onafhankelijk van tijdzone; zo rekenen we met dagen in plaats van tijdstippen.
function offerHistoryDay(iso) {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso || ""));
  return match ? Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])) / OFFER_HISTORY_DAY : null;
}

function offerHistoryToday() {
  const now = new Date();
  return Date.UTC(now.getFullYear(), now.getMonth(), now.getDate()) / OFFER_HISTORY_DAY;
}

function offerHistoryDayText(day, withYear = false) {
  const options = { day: "numeric", month: "short", timeZone: "UTC" };
  if (withYear) options.year = "numeric";
  return new Date(day * OFFER_HISTORY_DAY).toLocaleDateString(I18N.locale(), options);
}

// Ook €0,00 is een prijs op de as (formatPrice geeft bij nul niets).
function offerHistoryPrice(value) {
  return `€${Number(value || 0).toLocaleString(I18N.locale(), { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function offerHistoryPeriodText(episode) {
  const from = offerHistoryDay(episode.start);
  const to = offerHistoryDay(episode.end);
  if (from === null) return "";
  if (episode.open || to === null) return t("offers.history.period_open", { from: offerHistoryDayText(from) });
  if (to === from) return offerHistoryDayText(from);
  return t("offers.history.period", { from: offerHistoryDayText(from), to: offerHistoryDayText(to) });
}

function offerHistoryBarTitle(episode) {
  const price = typeof episode.price === "number" ? offerHistoryPrice(episode.price) : t("offers.history.no_price");
  return [offerHistoryPeriodText(episode), price, episode.promoLabel].filter(Boolean).join(" · ");
}

// Een rond stapje voor de prijsas: 0,25 - 0,50 - 1 - 2 - 5 - 10 ...
function offerHistoryStep(max) {
  const wanted = max / 4;
  for (const step of [0.25, 0.5, 1, 2, 5, 10, 20, 50, 100]) {
    if (step >= wanted) return step;
  }
  return 100;
}

// De grafiek: tijd langs de x-as, prijs langs de y-as. Elke keer in de aanbieding is een balk op de actieprijs, van de eerste
// tot de laatste dag van de aanbieding; een aanbieding die nog loopt eindigt in een pijl. De stippellijn is de gewone prijs: een
// trap als die door de tijd veranderde (elke trede loopt door tot de volgende keer dat we een andere prijs zagen). Een stip is een
// aankoop, op de prijs per stuk die je betaalde.
function offerHistoryChart(product, width) {
  const episodes = (product.episodes || []).filter((episode) => offerHistoryDay(episode.start) !== null);
  const segments = (product.regular || []).filter((segment) => offerHistoryDay(segment.from) !== null && typeof segment.price === "number");
  const purchases = (product.purchases || []).filter((purchase) => offerHistoryDay(purchase.date) !== null && typeof purchase.price === "number");
  if (!episodes.length && !segments.length && !purchases.length) return "";
  const W = Math.max(280, Math.floor(width));
  const H = W < 480 ? 220 : 260;
  const left = 58;
  const right = 14;
  const top = 22;
  const bottom = 32;
  const today = offerHistoryToday();
  const bars = episodes.map((episode) => {
    const start = offerHistoryDay(episode.start);
    const end = episode.open || !episode.end ? today : offerHistoryDay(episode.end);
    return { episode, start, end: Math.max(start, end) + 1 };
  });
  const steps = segments.map((segment) => ({ segment, start: offerHistoryDay(segment.from), end: (offerHistoryDay(segment.to) ?? offerHistoryDay(segment.from)) + 1 }));
  const dots = purchases.map((purchase) => ({ purchase, day: offerHistoryDay(purchase.date) }));
  // Een paar dagen ruimte rechts, zodat de pijl van een lopende aanbieding en het woord "vandaag" niet worden afgekapt.
  const xmax = Math.max(today + 1, ...bars.map((bar) => bar.end), ...steps.map((step) => step.end), ...dots.map((dot) => dot.day + 1)) + 5;
  let xmin = Math.min(...bars.map((bar) => bar.start), ...steps.map((step) => step.start), ...dots.map((dot) => dot.day));
  if (xmax - xmin < OFFER_HISTORY_MIN_DAYS) xmin = xmax - OFFER_HISTORY_MIN_DAYS;
  xmin -= 1;
  const withYear = xmax - xmin > 330;
  const x = (day) => left + ((day - xmin) / (xmax - xmin)) * (W - left - right);
  const prices = [
    ...episodes.map((episode) => episode.price),
    ...segments.map((segment) => segment.price),
    ...purchases.map((purchase) => purchase.price),
  ].filter((price) => typeof price === "number");
  const flatRegular = !segments.length && typeof product.regularPrice === "number";
  if (flatRegular) prices.push(product.regularPrice);
  const step = offerHistoryStep(Math.max(...prices, 1));
  const ymax = Math.ceil((Math.max(...prices, 1) * 1.1) / step) * step;
  const y = (value) => top + (1 - value / ymax) * (H - top - bottom);

  const parts = [];
  for (let value = 0; value <= ymax + 1e-9; value += step) {
    parts.push(`<line class="ohc-grid" x1="${left}" x2="${W - right}" y1="${y(value)}" y2="${y(value)}"/>`);
    parts.push(`<text class="ohc-axis" x="${left - 8}" y="${y(value) + 4}" text-anchor="end">${escapeHtml(offerHistoryPrice(value))}</text>`);
  }
  const maxTicks = Math.max(2, Math.floor((W - left - right) / (withYear ? 104 : 84)));
  const tickEvery = 7 * Math.max(1, Math.ceil((xmax - xmin) / 7 / maxTicks));
  for (let day = xmin + ((7 - ((xmin + 3) % 7)) % 7); day <= xmax; day += tickEvery) {
    parts.push(`<line class="ohc-grid ohc-grid--x" x1="${x(day)}" x2="${x(day)}" y1="${top}" y2="${H - bottom}"/>`);
    const nearEdge = x(day) > W - right - 22;
    parts.push(`<text class="ohc-axis" x="${nearEdge ? W - right : x(day)}" y="${H - bottom + 18}" text-anchor="${nearEdge ? "end" : "middle"}">${escapeHtml(offerHistoryDayText(day, withYear))}</text>`);
  }
  // Plekken die al bezet zijn (tekst, balken en stippen); een prijslabel gaat alleen op een vrije plek.
  const taken = [];
  const take = (x0, x1, y0, y1) => taken.push({ x0: x0 - 2, x1: x1 + 2, y0: y0 - 1, y1: y1 + 1 });
  const regularLabel = (price, labelX) => {
    const text = t("offers.history.chart.regular", { price: offerHistoryPrice(price) });
    const startX = Math.min(Math.max(labelX, left + 6), W - right - text.length * 6.2);
    take(startX, startX + text.length * 6.2, y(price) - 16, y(price) - 2);
    parts.push(`<text class="ohc-regular-label" x="${startX}" y="${y(price) - 5}" text-anchor="start">${escapeHtml(text)}</text>`);
  };
  if (flatRegular) {
    parts.push(`<line class="ohc-regular" x1="${left}" x2="${W - right}" y1="${y(product.regularPrice)}" y2="${y(product.regularPrice)}"/>`);
    regularLabel(product.regularPrice, left + 6);
  }
  steps.forEach((current, index) => {
    const next = steps[index + 1];
    const x0 = x(current.start);
    const x1 = Math.max(next ? x(next.start) : x(current.end), x0 + 2);
    const cy = y(current.segment.price);
    const title = t("offers.history.chart.regular_period", {
      price: offerHistoryPrice(current.segment.price),
      period: offerHistoryPeriodText({ start: current.segment.from, end: current.segment.to }),
    });
    parts.push(`<g class="ohc-step" tabindex="0"><title>${escapeHtml(title)}</title><line class="ohc-step-hit" x1="${x0}" x2="${x1}" y1="${cy}" y2="${cy}"/><line class="ohc-regular" x1="${x0}" x2="${x1}" y1="${cy}" y2="${cy}"/></g>`);
    if (next) parts.push(`<line class="ohc-regular" x1="${x1}" x2="${x1}" y1="${cy}" y2="${y(next.segment.price)}"/>`);
  });
  if (steps.length) {
    const last = steps[steps.length - 1];
    regularLabel(last.segment.price, x(last.start) + 6);
  }
  if (today >= xmin && today <= xmax) {
    parts.push(`<line class="ohc-today" x1="${x(today + 0.5)}" x2="${x(today + 0.5)}" y1="${top}" y2="${H - bottom}"/>`);
    parts.push(`<text class="ohc-axis ohc-axis--today" x="${x(today + 0.5)}" y="${top - 7}" text-anchor="middle">${escapeHtml(t("offers.history.chart.today"))}</text>`);
  }
  const drawn = [];
  for (const bar of bars) {
    const price = bar.episode.price;
    if (typeof price !== "number") continue;
    const x0 = x(bar.start);
    const x1 = Math.max(x(bar.end), x0 + 4);
    const cy = y(price);
    const open = bar.episode.open;
    drawn.push({ x0, x1, cy, price });
    take(x0, x1 + (open ? 9 : 0), cy - OFFER_HISTORY_BAR / 2, cy + OFFER_HISTORY_BAR / 2);
    parts.push(
      `<g class="ohc-bar${open ? " ohc-bar--open" : ""}" tabindex="0"><title>${escapeHtml(offerHistoryBarTitle(bar.episode))}</title>` +
        `<rect x="${x0}" y="${cy - OFFER_HISTORY_BAR / 2}" width="${x1 - x0}" height="${OFFER_HISTORY_BAR}" rx="3"/>` +
        (open ? `<path d="M${x1 - 1} ${cy - 8}L${x1 + 9} ${cy}L${x1 - 1} ${cy + 8}Z"/>` : "") +
        `</g>`
    );
  }
  for (const dot of dots) {
    const cx = x(dot.day + 0.5);
    const cy = y(dot.purchase.price);
    take(cx - 5, cx + 5, cy - 5, cy + 5);
    parts.push(`<g class="ohc-paid" tabindex="0"><title>${escapeHtml(offerHistoryPurchaseTitle(dot.purchase))}</title><circle cx="${cx}" cy="${cy}" r="4.5"/></g>`);
  }
  // De prijs bij een balk: erboven, anders eronder, anders een regel hoger of lager. Past hij nergens zonder een ander label of
  // een balk te raken, dan blijft hij weg; de prijs staat altijd nog in het hoverlabel en in de tabel. De nieuwste balk gaat voor.
  for (const bar of drawn.slice().reverse()) {
    const text = offerHistoryPrice(bar.price);
    const width = text.length * OFFER_HISTORY_CHAR_WIDTH;
    const labelX = Math.min(Math.max((bar.x0 + bar.x1) / 2 - width / 2, left), W - right - width);
    const above = bar.cy - OFFER_HISTORY_BAR / 2 - 5;
    const below = bar.cy + OFFER_HISTORY_BAR / 2 + OFFER_HISTORY_LABEL_HEIGHT - 1;
    const step = OFFER_HISTORY_LABEL_HEIGHT + 1;
    for (const baseline of [above, below, above - step, below + step]) {
      const y0 = baseline - OFFER_HISTORY_LABEL_HEIGHT + 2;
      const y1 = baseline + 3;
      if (y0 < top - 2 || y1 > H - bottom + 2) continue;
      if (taken.some((box) => labelX < box.x1 && labelX + width > box.x0 && y0 < box.y1 && y1 > box.y0)) continue;
      take(labelX, labelX + width, y0, y1);
      parts.push(`<text class="ohc-price" x="${labelX}" y="${baseline}" text-anchor="start">${escapeHtml(text)}</text>`);
      break;
    }
  }
  const label = t("offers.history.chart.label_full", { product: product.title, offers: tn("offers.history.item.times", product.timesOnOffer || 0), bought: tn("offers.history.item.bought", product.timesBought || 0) });
  return `<svg class="ohc" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="${escapeHtml(label)}">${parts.join("")}</svg>`;
}

// Waar een aankoop vandaan komt. Een kassabon toont de prijs zonder de korting onderaan de bon; dat staat onder de tabel.
function offerHistorySource(source) {
  return source === "receipt" ? t("offers.history.source.receipt") : t("offers.history.source.order");
}

function offerHistoryQuantity(purchase) {
  return typeof purchase.quantity === "number" && purchase.quantity > 0 ? I18N.formatNumber(purchase.quantity) : "";
}

function offerHistoryPurchaseTitle(purchase) {
  const day = offerHistoryDay(purchase.date);
  return [
    day === null ? "" : offerHistoryDayText(day, true),
    t("offers.history.paid_each", { price: offerHistoryPrice(purchase.price) }),
    offerHistoryQuantity(purchase) ? `${offerHistoryQuantity(purchase)}×` : "",
    offerHistorySource(purchase.source),
  ]
    .filter(Boolean)
    .join(" · ");
}

function offerHistoryStat(label, value) {
  return `<div class="ohc-stat"><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`;
}

function offerHistoryLegend(product) {
  const items = [];
  if ((product.episodes || []).length) items.push(`<li><span class="ohc-key ohc-key--offer" aria-hidden="true"></span>${escapeHtml(t("offers.history.legend.offer"))}</li>`);
  if ((product.regular || []).length || typeof product.regularPrice === "number") items.push(`<li><span class="ohc-key ohc-key--regular" aria-hidden="true"></span>${escapeHtml(t("offers.history.legend.regular"))}</li>`);
  if ((product.purchases || []).length) items.push(`<li><span class="ohc-key ohc-key--paid" aria-hidden="true"></span>${escapeHtml(t("offers.history.legend.paid"))}</li>`);
  return items.length > 1 ? `<ul class="ohc-legend">${items.join("")}</ul>` : "";
}

function offerHistoryDetail(product) {
  const meta = [product.brand, product.unitSize].filter(Boolean).join(" · ");
  const lastDay = offerHistoryDay(product.lastActivity || product.lastSeen);
  const lastSeen = lastDay === null ? "" : offerHistoryDayText(lastDay, true);
  const number = (value) => typeof value === "number";
  const stats = [
    offerHistoryStat(t("offers.history.stat.times"), I18N.formatNumber(product.timesOnOffer || 0)),
    number(product.lowestPrice) ? offerHistoryStat(t("offers.history.stat.lowest"), offerHistoryPrice(product.lowestPrice)) : "",
    number(product.highestPrice) && product.highestPrice !== product.lowestPrice
      ? offerHistoryStat(t("offers.history.stat.highest"), offerHistoryPrice(product.highestPrice))
      : "",
    number(product.regularPrice) ? offerHistoryStat(t("offers.history.stat.regular"), offerHistoryPrice(product.regularPrice)) : "",
    number(product.lowestRegular) && number(product.highestRegular) && product.lowestRegular !== product.highestRegular
      ? offerHistoryStat(t("offers.history.stat.regular_range"), `${offerHistoryPrice(product.lowestRegular)} – ${offerHistoryPrice(product.highestRegular)}`)
      : "",
    product.timesBought ? offerHistoryStat(t("offers.history.stat.bought"), I18N.formatNumber(product.timesBought)) : "",
    number(product.lastPaid) ? offerHistoryStat(t("offers.history.stat.last_paid"), offerHistoryPrice(product.lastPaid)) : "",
    number(product.averagePaid) && product.timesBought > 1 ? offerHistoryStat(t("offers.history.stat.average_paid"), offerHistoryPrice(product.averagePaid)) : "",
    lastSeen ? offerHistoryStat(t("offers.history.stat.last"), lastSeen) : "",
  ].join("");
  const offerRows = (product.episodes || [])
    .slice()
    .reverse()
    .map(
      (episode) =>
        `<tr><td>${escapeHtml(offerHistoryPeriodText(episode))}</td><td>${escapeHtml(typeof episode.price === "number" ? offerHistoryPrice(episode.price) : "")}</td><td>${escapeHtml(episode.promoLabel || "")}</td></tr>`
    )
    .join("");
  const purchaseRows = (product.purchases || [])
    .slice()
    .reverse()
    .map((purchase) => {
      const day = offerHistoryDay(purchase.date);
      return `<tr><td>${escapeHtml(day === null ? "" : offerHistoryDayText(day, true))}</td><td>${escapeHtml(offerHistoryPrice(purchase.price))}</td><td>${escapeHtml(offerHistoryQuantity(purchase))}</td><td>${escapeHtml(offerHistorySource(purchase.source))}</td></tr>`;
    })
    .join("");
  const link = product.url
    ? `<a href="${escapeHtml(product.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(t("offers.history.open", { store: STORES[product.store].name }))}</a>`
    : "";
  return `
    <div class="ohc-head">
      ${product.image ? `<img src="${escapeHtml(product.image)}" alt="" loading="lazy" data-hide-on-error>` : ""}
      <div>
        <h3>${escapeHtml(product.title)}</h3>
        ${meta ? `<p class="muted">${escapeHtml(meta)}</p>` : ""}
        <p class="${product.onOfferNow ? "bonus" : "muted"}">${escapeHtml(t(product.onOfferNow ? "offers.history.now" : "offers.history.not_now"))}${link ? ` · ${link}` : ""}</p>
      </div>
    </div>
    <dl class="ohc-stats">${stats}</dl>
    <div class="ohc-chart" id="offers-history-chart">${offerHistoryChart(product, offerHistoryChartWidth())}</div>
    ${offerHistoryLegend(product)}
    ${
      offerRows
        ? `<table class="ohc-table">
      <caption>${escapeHtml(t("offers.history.table.offers"))}</caption>
      <thead><tr><th scope="col">${escapeHtml(t("offers.history.table.period"))}</th><th scope="col">${escapeHtml(t("offers.history.table.price"))}</th><th scope="col">${escapeHtml(t("offers.history.table.action"))}</th></tr></thead>
      <tbody>${offerRows}</tbody>
    </table>`
        : ""
    }
    ${
      purchaseRows
        ? `<table class="ohc-table">
      <caption>${escapeHtml(t("offers.history.table.purchases"))}</caption>
      <thead><tr><th scope="col">${escapeHtml(t("offers.history.table.date"))}</th><th scope="col">${escapeHtml(t("offers.history.table.paid"))}</th><th scope="col">${escapeHtml(t("offers.history.table.quantity"))}</th><th scope="col">${escapeHtml(t("offers.history.table.source"))}</th></tr></thead>
      <tbody>${purchaseRows}</tbody>
    </table>
    ${(product.purchases || []).some((purchase) => purchase.source === "receipt") ? `<p class="muted ohc-note">${escapeHtml(t("offers.history.receipt_note"))}</p>` : ""}`
        : ""
    }`;
}

function offerHistoryChartWidth() {
  const box = $("#offers-history-detail");
  return box && box.clientWidth ? box.clientWidth : 640;
}

function renderOfferHistory() {
  const list = $("#offers-history-list");
  const detail = $("#offers-history-detail");
  const status = $("#offers-history-status");
  const products = offerHistory.products;
  if (!products.length) {
    list.innerHTML = "";
    detail.innerHTML = "";
    status.textContent = offerHistory.query ? t("offers.history.none", { query: offerHistory.query }) : t("offers.history.empty");
    return;
  }
  status.textContent = tn("offers.history.count", offerHistory.total, { shown: products.length });
  list.innerHTML = products
    .map((product) => {
      const key = offerHistoryKey(product);
      const lowest = typeof product.lowestPrice === "number" ? ` · ${t("offers.history.item.lowest", { price: offerHistoryPrice(product.lowestPrice) })}` : "";
      const bought = product.timesBought ? ` · ${tn("offers.history.item.bought", product.timesBought)}` : "";
      const meta = [product.brand, product.unitSize].filter(Boolean).join(" · ");
      return `<li><button class="ohc-item${key === offerHistory.selected ? " is-active" : ""}" type="button" data-key="${escapeHtml(key)}" aria-pressed="${key === offerHistory.selected}">
        <strong>${escapeHtml(product.title)}</strong>
        ${meta ? `<small>${escapeHtml(meta)}</small>` : ""}
        <small>${escapeHtml(tn("offers.history.item.times", product.timesOnOffer || 0))}${escapeHtml(lowest)}${escapeHtml(bought)}${product.onOfferNow ? ` · <span class="bonus">${escapeHtml(t("offers.history.now"))}</span>` : ""}</small>
      </button></li>`;
    })
    .join("");
  const chosen = products.find((product) => offerHistoryKey(product) === offerHistory.selected);
  detail.innerHTML = chosen ? offerHistoryDetail(chosen) : "";
}

async function loadOfferHistory() {
  const loadId = ++offerHistory.loadId;
  const store = offersState.store;
  const query = $("#offers-history-search").value.trim();
  offerHistory.query = query;
  const loading = $("#offers-history-loading");
  if (!offerHistory.products.length) {
    loading.innerHTML = `${mdiIcon(MDI_LOADING, "offers-loading__icon")}<span>${escapeHtml(t("offers.history.loading"))}</span>`;
    loading.hidden = false;
  }
  try {
    const params = new URLSearchParams({ store, q: query, limit: String(OFFER_HISTORY_LIMIT) });
    const data = await jsonFetch(`/api/prices/history?${params}`);
    if (loadId !== offerHistory.loadId) return;
    offerHistory.products = data.products || [];
    offerHistory.total = data.total || 0;
    offerHistory.loadedStore = store;
    if (!offerHistory.products.some((product) => offerHistoryKey(product) === offerHistory.selected)) {
      offerHistory.selected = offerHistory.products.length ? offerHistoryKey(offerHistory.products[0]) : "";
    }
    message("#offers-history-message", "");
    renderOfferHistory();
  } catch (error) {
    if (loadId !== offerHistory.loadId) return;
    message("#offers-history-message", error.message);
  } finally {
    if (loadId === offerHistory.loadId) loading.hidden = true;
  }
}

// Offers.js roept dit aan na het wisselen van winkel: de lijst en de grafiek horen bij de gekozen winkel.
function offerHistoryStoreChanged() {
  offerHistory.selected = "";
  offerHistory.products = [];
  if (document.querySelector("#offers-history.is-active")) loadOfferHistory();
  else offerHistory.loadedStore = "";
}

$("#offers-history-search").addEventListener("input", () => {
  clearTimeout(offerHistory.timer);
  offerHistory.timer = setTimeout(loadOfferHistory, 250);
});
$("#offers-history-form").addEventListener("submit", (event) => {
  event.preventDefault();
  clearTimeout(offerHistory.timer);
  loadOfferHistory();
});
$("#offers-history-list").addEventListener("click", (event) => {
  const item = event.target.closest("[data-key]");
  if (!item) return;
  offerHistory.selected = item.dataset.key;
  renderOfferHistory();
});
document.querySelector('.subtab[data-subtab="offers-history"]').addEventListener("click", loadOfferHistory);
window.addEventListener("ms:languagechange", () => {
  if (offerHistory.products.length || offerHistory.loadedStore) renderOfferHistory();
});
let offerHistoryResizeFrame = 0;
window.addEventListener("resize", () => {
  if (!document.querySelector("#offers-history.is-active")) return;
  cancelAnimationFrame(offerHistoryResizeFrame);
  offerHistoryResizeFrame = requestAnimationFrame(renderOfferHistory);
});
