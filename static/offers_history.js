// Het tabblad Aanbiedingen > Prijsgeschiedenis: zoek een product en zie in een grafiek wanneer het in de aanbieding was en voor
// welke prijs, wat het normaal kostte en wat je ervoor betaalde (/api/prices/history: de aanbiedingen van elke synchronisatie, de
// gewone prijs uit zoekopdrachten en bestellingen, en je aankopen uit bestellingen en kassabonnen).
// De grafiek is inline SVG zonder bibliotheek; de tabel eronder geeft dezelfde gegevens voor schermlezers.
// De schakelaar "Alleen eerder gekocht" toont alleen de producten die je minstens één keer kocht (?bought=1); de keuze blijft bewaard.
// Van een aanbieding die je kocht bewaart de server de afbeelding (product_images.py); die staan bij het product, met de naam van toen.
// Laadt na offers.js en gebruikt offersState, offersTabActive, STORES, jsonFetch, message, formatPrice en escapeHtml.

const OFFER_HISTORY_LIMIT = 30;
const OFFER_HISTORY_MIN_DAYS = 84; // de grafiek toont minstens twaalf weken
const OFFER_HISTORY_BAR = 12;
const OFFER_HISTORY_DAY = 86400000;
const OFFER_HISTORY_LABEL_HEIGHT = 14;
const OFFER_HISTORY_CHAR_WIDTH = 7; // een schatting voor de vette prijstekst van 12 px; ruim genomen, zodat labels elkaar niet raken
const OFFER_HISTORY_BOUGHT_KEY = "ms-offers-history-bought";
// `pinned`: het product is gekozen (met een klik of uit het adres) en hoort dus in het adres (router.js). `pin`: het product-id
// uit het adres (een link van een aanbieding of bon). De server zet het altijd bovenaan de lijst, ook als de naam niet past of
// als het alleen een aanbieding heeft die nog moet beginnen; een nieuwe zoekterm laat het los.
const offerHistory = { query: "", bought: false, products: [], total: 0, selected: "", pinned: false, pin: "", loadId: 0, timer: 0, loadedStore: "", unsynced: null, ranges: {} };
// De keuzes voor de periode van de grafiek, in dagen terug vanaf vandaag ("all": alles wat er is).
const OFFER_HISTORY_RANGES = { "1y": 365, "3y": 1095 };

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
  return [offerHistoryPeriodText(episode), price, offerHistoryLabel(episode)].filter(Boolean).join(" · ");
}

// De actietekst van een periode; een persoonlijke aanbieding (AH: de Bonus Box) staat erbij als zodanig.
// Een aanbieding die nog moet beginnen (prices._upcoming): "Vanaf 9 nov. in de aanbieding", met `full` ook de prijs en het label.
function offerHistoryUpcomingText(upcoming, full = false) {
  const day = offerHistoryDay(upcoming.validFrom);
  const text = t("offers.history.upcoming", { date: day === null ? "" : offerHistoryDayText(day) });
  if (!full) return text;
  const price = typeof upcoming.price === "number" ? offerHistoryPrice(upcoming.price) : "";
  // "Bonus Box 15% korting" noemt de Bonus Box al: dan niet nog eens ervoor.
  const badge = upcoming.personal && !String(upcoming.promoLabel || "").includes(t("offers.personal.badge")) ? t("offers.personal.badge") : "";
  return [text, price, badge, upcoming.promoLabel].filter(Boolean).join(" · ");
}

// Een folderactie voor de hele lijn: wanneer, de naam in de folder en de actie.
function offerHistoryLineTitle(line) {
  return [offerHistoryPeriodText(line), line.title, offerHistoryLinePrice(line), line.promoLabel].filter(Boolean).join(" · ");
}

// De prijs per stuk van een folderactie (line_offers.per_piece), met de gewone prijs erbij als die bekend is.
function offerHistoryLinePrice(line) {
  if (typeof line.price !== "number") return "";
  const price = t("offers.history.line_price", { price: offerHistoryPrice(line.price) });
  return typeof line.priceBefore === "number" ? `${price} (${t("offers.history.line_was", { price: offerHistoryPrice(line.priceBefore) })})` : price;
}

// Hoe vaak in de aanbieding: de eigen aanbiedingen en de folderacties voor de hele lijn samen.
function offerHistoryTimes(product) {
  return (product.timesOnOffer || 0) + (product.timesInLineOffer || 0);
}

function offerHistoryLineText(product) {
  return product.timesInLineOffer ? t("offers.history.item.from_archive", { count: I18N.formatNumber(product.timesInLineOffer) }) : "";
}

function offerHistoryLabel(episode) {
  return [episode.personal ? t("offers.personal.badge") : "", episode.promoLabel].filter(Boolean).join(" · ");
}

// Een rond stapje voor de prijsas: 0,25 - 0,50 - 1 - 2 - 5 - 10 ...
function offerHistoryStep(max) {
  const wanted = max / 4;
  for (const step of [0.25, 0.5, 1, 2, 5, 10, 20, 50, 100]) {
    if (step >= wanted) return step;
  }
  return 100;
}

// De eerste dag van wat het product zelf heeft (aanbiedingen, gewone prijzen, aankopen, waarnemingen), zonder de folderacties
// voor de hele lijn: die gaan in een folderarchief tot 2009 terug.
function offerHistoryOwnStart(product) {
  const days = [
    ...(product.episodes || []).map((episode) => offerHistoryDay(episode.start)),
    ...(product.regular || []).map((segment) => offerHistoryDay(segment.from)),
    ...(product.purchases || []).map((purchase) => offerHistoryDay(purchase.date)),
    ...(product.observed || []).map((point) => offerHistoryDay(point.date)),
  ].filter((day) => day !== null);
  return days.length ? Math.min(...days) : null;
}

// De periode van de grafiek. Gaat er iets verder terug dan een jaar, dan zijn er knoppen (1 jaar, 3 jaar, alles). Zonder keuze
// toont de grafiek de kortste periode met alles van het product zelf erin: zeventien jaar folderarchief drukt anders je eigen
// aankopen en aanbiedingen in de laatste paar pixels.
function offerHistoryRange(product, wanted) {
  const today = offerHistoryToday();
  const own = offerHistoryOwnStart(product);
  const lineDays = (product.lineOffers || []).map((line) => offerHistoryDay(line.start)).filter((day) => day !== null);
  const earliest = Math.min(...(own === null ? [] : [own]), ...lineDays);
  if (!Number.isFinite(earliest) || earliest >= today - OFFER_HISTORY_RANGES["1y"]) return { choice: "all", options: [], from: null };
  const options = ["1y", ...(earliest < today - OFFER_HISTORY_RANGES["3y"] ? ["3y"] : []), "all"];
  let choice = options.includes(wanted) ? wanted : "all";
  if (!options.includes(wanted) && own !== null) {
    choice = options.find((key) => key === "all" || own >= today - OFFER_HISTORY_RANGES[key]);
  }
  return { choice, options, from: choice === "all" ? null : today - OFFER_HISTORY_RANGES[choice] };
}

// De grafiek: tijd langs de x-as, prijs langs de y-as. Elke keer in de aanbieding is een balk op de actieprijs, van de eerste
// tot de laatste dag van de aanbieding; een aanbieding die nog loopt eindigt in een pijl. De stippellijn is de gewone prijs: een
// trap als die door de tijd veranderde (elke trede loopt door tot de volgende keer dat we een andere prijs zagen). Een stip is een
// aankoop, op de prijs per stuk die je betaalde. Een folderactie voor de hele lijn (prices._line_offers) is een oranje balk op de
// prijs per stuk, of een streepje onderaan als het folderarchief er geen prijs bij noemt. `range` is een keuze van
// `offerHistoryRange` ("1y", "3y", "all"); zonder keuze kiest die zelf.
function offerHistoryChart(product, width, range = undefined) {
  const episodes = (product.episodes || []).filter((episode) => offerHistoryDay(episode.start) !== null);
  const lineOffers = (product.lineOffers || []).filter((line) => offerHistoryDay(line.start) !== null);
  const segments = (product.regular || []).filter((segment) => offerHistoryDay(segment.from) !== null && typeof segment.price === "number");
  const purchases = (product.purchases || []).filter((purchase) => offerHistoryDay(purchase.date) !== null && typeof purchase.price === "number");
  const observed = (product.observed || []).filter((point) => offerHistoryDay(point.date) !== null && typeof point.price === "number");
  if (!episodes.length && !segments.length && !purchases.length && !observed.length && !lineOffers.length) return "";
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
  const measured = observed.map((point) => ({ point, day: offerHistoryDay(point.date) }));
  let lines = lineOffers.map((line) => {
    const start = offerHistoryDay(line.start);
    return { line, start, end: Math.max(start, offerHistoryDay(line.end) ?? start) + 1 };
  });
  // Alleen wat in de gekozen periode valt; een periode die ervoor begon, begint op de rand.
  const from = offerHistoryRange(product, range ?? offerHistory.ranges[offerHistoryKey(product)]).from;
  const clip = (items) => (from === null ? items : items.filter((item) => item.end > from).map((item) => ({ ...item, start: Math.max(item.start, from) })));
  const inRange = (items) => (from === null ? items : items.filter((item) => item.day >= from));
  const shownBars = clip(bars);
  const shownSteps = clip(steps);
  const shownDots = inRange(dots);
  const shownMeasured = inRange(measured);
  lines = clip(lines);
  // Een paar dagen ruimte rechts, zodat de pijl van een lopende aanbieding en het woord "vandaag" niet worden afgekapt.
  const xmax = Math.max(today + 1, ...shownBars.map((bar) => bar.end), ...shownSteps.map((step) => step.end), ...shownDots.map((dot) => dot.day + 1), ...shownMeasured.map((dot) => dot.day + 1), ...lines.map((line) => line.end)) + 5;
  let xmin = from ?? Math.min(today, ...shownBars.map((bar) => bar.start), ...shownSteps.map((step) => step.start), ...shownDots.map((dot) => dot.day), ...shownMeasured.map((dot) => dot.day), ...lines.map((line) => line.start));
  if (xmax - xmin < OFFER_HISTORY_MIN_DAYS) xmin = xmax - OFFER_HISTORY_MIN_DAYS;
  xmin -= 1;
  const withYear = xmax - xmin > 330;
  const x = (day) => left + ((day - xmin) / (xmax - xmin)) * (W - left - right);
  const prices = [
    ...shownBars.map((bar) => bar.episode.price),
    ...shownSteps.map((step) => step.segment.price),
    ...shownDots.map((dot) => dot.purchase.price),
    ...shownMeasured.map((dot) => dot.point.price),
    ...lines.map((line) => line.line.price),
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
  shownSteps.forEach((current, index) => {
    const next = shownSteps[index + 1];
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
  if (shownSteps.length) {
    const last = shownSteps[shownSteps.length - 1];
    regularLabel(last.segment.price, x(last.start) + 6);
  }
  if (today >= xmin && today <= xmax) {
    parts.push(`<line class="ohc-today" x1="${x(today + 0.5)}" x2="${x(today + 0.5)}" y1="${top}" y2="${H - bottom}"/>`);
    parts.push(`<text class="ohc-axis ohc-axis--today" x="${x(today + 0.5)}" y="${top - 7}" text-anchor="middle">${escapeHtml(t("offers.history.chart.today"))}</text>`);
  }
  for (const line of lines) {
    const x0 = x(line.start);
    const priced = typeof line.line.price === "number";
    const x1 = Math.max(x(line.end), x0 + (priced ? 5 : 3));
    const y0 = priced ? y(line.line.price) - 4 : H - bottom - 9;
    const height = 8;
    take(x0, x1, y0, y0 + height);
    parts.push(
      `<g class="ohc-line${priced ? " ohc-line--priced" : ""}" tabindex="0"><title>${escapeHtml(offerHistoryLineTitle(line.line))}</title>` +
        `<rect x="${x0}" y="${y0}" width="${x1 - x0}" height="${height}" rx="${priced ? 3 : 1.5}"/></g>`
    );
  }
  const drawn = [];
  for (const bar of shownBars) {
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
  for (const dot of shownDots) {
    const cx = x(dot.day + 0.5);
    const cy = y(dot.purchase.price);
    take(cx - 5, cx + 5, cy - 5, cy + 5);
    parts.push(`<g class="ohc-paid" tabindex="0"><title>${escapeHtml(offerHistoryPurchaseTitle(dot.purchase))}</title><circle cx="${cx}" cy="${cy}" r="4.5"/></g>`);
  }
  for (const dot of shownMeasured) {
    const cx = x(dot.day + 0.5);
    const cy = y(dot.point.price);
    take(cx - 5, cx + 5, cy - 5, cy + 5);
    const title = [offerHistoryDayText(dot.day, true), offerHistoryPrice(dot.point.price), dot.point.unitSize, t("offers.history.observed_note")].filter(Boolean).join(" - ");
    parts.push(`<g class="ohc-observed" tabindex="0"><title>${escapeHtml(title)}</title><path d="M${cx} ${cy - 5}L${cx + 5} ${cy}L${cx} ${cy + 5}L${cx - 5} ${cy}Z"/></g>`);
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
  const offersText = [tn("offers.history.item.times", offerHistoryTimes(product)), offerHistoryLineText(product)].filter(Boolean).join(", ");
  const label = t("offers.history.chart.label_full", { product: product.title, offers: offersText, bought: tn("offers.history.item.bought", product.timesBought || 0), observed: tn("offers.history.item.observed", observed.length) });
  return `<svg class="ohc" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="${escapeHtml(label)}">${parts.join("")}</svg>`;
}

// Waar een aankoop vandaan komt. Een kassabon toont de prijs zonder de korting onderaan de bon; dat staat onder de tabel.
function offerHistorySource(source) {
  if (source === "import") return t("offers.history.source.import");
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
  if ((product.observed || []).length) items.push(`<li><span class="ohc-key ohc-key--observed" aria-hidden="true"></span>${escapeHtml(t("offers.history.legend.observed"))}</li>`);
  if ((product.lineOffers || []).length) items.push(`<li><span class="ohc-key ohc-key--line" aria-hidden="true"></span>${escapeHtml(t("offers.history.legend.line"))}</li>`);
  return items.length > 1 || (product.observed || []).length || (product.lineOffers || []).length ? `<ul class="ohc-legend">${items.join("")}</ul>` : "";
}

// De bewaarde afbeeldingen van een product dat je kocht, de nieuwste eerst, elk met de naam die het product toen had: zo zie je of
// het hetzelfde product is, ook als de naam veranderde. Een klik opent de afbeelding in het groot (hooguit 1024 pixels breed).
function offerHistoryImages(product) {
  const images = product.savedImages || [];
  if (!images.length) return "";
  const items = images
    .map((image) => {
      const day = offerHistoryDay(image.savedAt);
      const date = day === null ? "" : offerHistoryDayText(day, true);
      const size = Number(image.width) > 0 && Number(image.height) > 0 ? ` width="${Number(image.width)}" height="${Number(image.height)}"` : "";
      return `<li><figure>
        <a href="${escapeHtml(image.url)}" target="_blank" rel="noopener"><img src="${escapeHtml(image.url)}" alt="${escapeHtml(t("offers.history.images.alt", { title: image.title, date }))}" loading="lazy"${size}></a>
        <figcaption>${escapeHtml(image.title)}${date ? `<small>${escapeHtml(t("offers.history.images.saved", { date }))}</small>` : ""}</figcaption>
      </figure></li>`;
    })
    .join("");
  return `<section class="ohc-images" aria-labelledby="ohc-images-title">
      <h4 id="ohc-images-title">${escapeHtml(t("offers.history.images.title"))}</h4>
      <p class="muted">${escapeHtml(t("offers.history.images.explain"))}</p>
      <ul>${items}</ul>
    </section>`;
}

// De kop van een product: plaatje, naam, merk en verpakking, de status (nu in de aanbieding, vanaf, of niet) met de link naar de
// winkel en Vergelijk verpakkingen. `compact` (de popup bij een aanbieding) zet een grotere foto links en laat de vergelijkknop weg.
function offerHistoryHead(product, { compact = false } = {}) {
  const meta = [product.brand, product.unitSize].filter(Boolean).join(" · ");
  const image = ((product.savedImages || [])[0] || {}).url || product.image;
  const status = product.onOfferNow ? t("offers.history.now")
    : product.upcoming ? offerHistoryUpcomingText(product.upcoming, true)
    : t((product.observed || []).length && !(product.episodes || []).length ? "offers.history.no_confirmed_offer" : "offers.history.not_now");
  const link = product.url
    ? `<a href="${escapeHtml(product.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(t("offers.history.open", { store: STORES[product.store].name }))}</a>`
    : "";
  const compare = !compact && product.regularPerUnit
    ? `<button class="link-button" type="button" data-compare="${escapeHtml(product.productId)}" data-store="${escapeHtml(product.store)}">${escapeHtml(t("offers.compare.open"))}</button>`
    : "";
  return `<div class="ohc-head${compact ? " ohc-head--compact" : ""}">
      ${image ? `<img src="${escapeHtml(storeImage(image))}" alt="" loading="lazy" data-hide-on-error>` : ""}
      <div>
        <h3>${escapeHtml(product.title)}</h3>
        ${meta ? `<p class="muted">${escapeHtml(meta)}</p>` : ""}
        <p class="${product.onOfferNow || product.upcoming ? "bonus" : "muted"}">${escapeHtml(status)}${link ? ` · ${link}` : ""}</p>
        ${compare}
      </div>
    </div>`;
}

// De kerncijfers: keer in de aanbieding, laagste en hoogste actieprijs, gewone prijs, wat je betaalde, laatst gezien.
function offerHistoryStats(product) {
  const lastDay = offerHistoryDay(product.lastActivity || product.lastSeen);
  const lastSeen = lastDay === null ? "" : offerHistoryDayText(lastDay, true);
  const number = (value) => typeof value === "number";
  const linePrices = (product.lineOffers || []).map((line) => line.price).filter(number);
  const lineLowest = linePrices.length ? Math.min(...linePrices) : null;
  const stats = [
    offerHistoryStat(t("offers.history.stat.times"), I18N.formatNumber(offerHistoryTimes(product))),
    product.timesInLineOffer ? offerHistoryStat(t("offers.history.stat.line_offers"), I18N.formatNumber(product.timesInLineOffer)) : "",
    lineLowest !== null ? offerHistoryStat(t("offers.history.stat.line_lowest"), offerHistoryPrice(lineLowest)) : "",
    number(product.lowestPrice) ? offerHistoryStat(t("offers.history.stat.lowest"), offerHistoryPrice(product.lowestPrice)) : "",
    number(product.highestPrice) && product.highestPrice !== product.lowestPrice
      ? offerHistoryStat(t("offers.history.stat.highest"), offerHistoryPrice(product.highestPrice))
      : "",
    number(product.regularPrice) ? offerHistoryStat(t("offers.history.stat.regular"), offerHistoryPrice(product.regularPrice)) : "",
    product.regularPerUnit ? offerHistoryStat(t("offers.history.stat.regular_per_unit"), offerPerUnitText(product.regularPerUnit)) : "",
    number(product.lowestRegular) && number(product.highestRegular) && product.lowestRegular !== product.highestRegular
      ? offerHistoryStat(t("offers.history.stat.regular_range"), `${offerHistoryPrice(product.lowestRegular)} – ${offerHistoryPrice(product.highestRegular)}`)
      : "",
    (product.observed || []).length ? offerHistoryStat(t("offers.history.stat.observed"), I18N.formatNumber(product.observed.length)) : "",
    product.timesBought ? offerHistoryStat(t("offers.history.stat.bought"), I18N.formatNumber(product.timesBought)) : "",
    number(product.lastPaid) ? offerHistoryStat(t("offers.history.stat.last_paid"), offerHistoryPrice(product.lastPaid)) : "",
    number(product.averagePaid) && product.timesBought > 1 ? offerHistoryStat(t("offers.history.stat.average_paid"), offerHistoryPrice(product.averagePaid)) : "",
    lastSeen ? offerHistoryStat(t("offers.history.stat.last"), lastSeen) : "",
  ].join("");
  return `<dl class="ohc-stats">${stats}</dl>`;
}

// De aankopen als tabel, nieuwste eerst: datum, betaald per stuk, aantal en de bron met een link naar de bestelling of bon. Met
// `limit` de laatste zoveel, met eronder hoeveel er nog meer zijn. Leeg zonder aankopen.
function offerHistoryPurchasesTable(product, { limit = 0 } = {}) {
  const purchases = (product.purchases || []).slice().reverse();
  const shown = limit > 0 ? purchases.slice(0, limit) : purchases;
  if (!shown.length) return "";
  const rows = shown
    .map((purchase) => {
      const day = offerHistoryDay(purchase.date);
      // De bron linkt naar de bestelling of kassabon zelf, als de app die kan tonen.
      const source = escapeHtml(offerHistorySource(purchase.source));
      const path = purchasePath(product.store, purchase.source, purchase.ref);
      const sourceCell = path ? `<a href="${escapeHtml(path)}" data-route>${source}</a>` : source;
      return `<tr><td>${escapeHtml(day === null ? "" : offerHistoryDayText(day, true))}</td><td>${escapeHtml(offerHistoryPrice(purchase.price))}</td><td>${escapeHtml(offerHistoryQuantity(purchase))}</td><td>${sourceCell}</td></tr>`;
    })
    .join("");
  const more = purchases.length > shown.length ? `<p class="muted ohc-note">${escapeHtml(t("offers.history.purchases_more", { count: I18N.formatNumber(purchases.length - shown.length) }))}</p>` : "";
  const receipt = shown.some((purchase) => purchase.source === "receipt") ? `<p class="muted ohc-note">${escapeHtml(t("offers.history.receipt_note"))}</p>` : "";
  return `<table class="ohc-table">
      <caption>${escapeHtml(t("offers.history.table.purchases"))}</caption>
      <thead><tr><th scope="col">${escapeHtml(t("offers.history.table.date"))}</th><th scope="col">${escapeHtml(t("offers.history.table.paid"))}</th><th scope="col">${escapeHtml(t("offers.history.table.quantity"))}</th><th scope="col">${escapeHtml(t("offers.history.table.source"))}</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
    ${more}${receipt}`;
}

function offerHistoryDetail(product) {
  const offerRows = (product.episodes || [])
    .slice()
    .reverse()
    .map(
      (episode) =>
        `<tr><td>${escapeHtml(offerHistoryPeriodText(episode))}</td><td>${escapeHtml(typeof episode.price === "number" ? offerHistoryPrice(episode.price) : "")}</td><td>${escapeHtml(offerHistoryLabel(episode))}</td></tr>`
    )
    .join("");
  const lineRows = (product.lineOffers || [])
    .slice()
    .reverse()
    .map(
      (line) =>
        `<tr><td>${escapeHtml(offerHistoryPeriodText(line))}</td><td>${escapeHtml(typeof line.price === "number" ? offerHistoryPrice(line.price) : "")}</td><td>${escapeHtml(String(line.title || "").replace(/^Folder: /, ""))}${line.promoLabel ? `<small class="ohc-line-action">${escapeHtml(line.promoLabel)}</small>` : ""}</td></tr>`
    )
    .join("");
  const observedRows = (product.observed || []).slice().reverse().map((point) => {
    const day = offerHistoryDay(point.date);
    const source = /^https?:\/\//i.test(point.sourceUrl || "")
      ? `<a href="${escapeHtml(point.sourceUrl)}" target="_blank" rel="noopener noreferrer">${escapeHtml(t("offers.history.observed_source"))}</a>`
      : "";
    return `<tr><td>${escapeHtml(day === null ? "" : offerHistoryDayText(day, true))}</td><td>${escapeHtml(offerHistoryPrice(point.price))}</td><td>${escapeHtml(point.unitSize || "")}</td><td>${source}</td></tr>`;
  }).join("");
  return `
    ${offerHistoryHead(product)}
    ${offerHistoryStats(product)}
    ${offerHistoryChartBlock(product)}
    ${offerHistoryLegend(product)}
    ${observedRows ? `<p class="muted ohc-note">${escapeHtml(t("offers.history.observed_note"))}</p>
    <table class="ohc-table">
      <caption>${escapeHtml(t("offers.history.table.observed"))}</caption>
      <thead><tr><th scope="col">${escapeHtml(t("offers.history.table.date"))}</th><th scope="col">${escapeHtml(t("offers.history.table.price"))}</th><th scope="col">${escapeHtml(t("offers.history.table.size"))}</th><th scope="col">${escapeHtml(t("offers.history.table.source"))}</th></tr></thead>
      <tbody>${observedRows}</tbody>
    </table>` : ""}
    ${offerHistoryImages(product)}
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
      lineRows
        ? `<table class="ohc-table">
      <caption>${escapeHtml(t("offers.history.table.line_offers"))}</caption>
      <thead><tr><th scope="col">${escapeHtml(t("offers.history.table.period"))}</th><th scope="col">${escapeHtml(t("offers.history.table.price_each"))}</th><th scope="col">${escapeHtml(t("offers.history.table.folder_name"))}</th></tr></thead>
      <tbody>${lineRows}</tbody>
    </table>
    <p class="muted ohc-note">${escapeHtml(t("offers.history.line_note"))}</p>`
        : ""
    }
    ${offerHistoryPurchasesTable(product)}`;
}

function offerHistoryRangeLabel(key) {
  if (key === "1y") return t("offers.history.range.1y");
  return key === "3y" ? t("offers.history.range.3y") : t("offers.history.range.all");
}

// De knoppen voor de periode en de grafiek; een klik op een knop tekent alleen dit blok opnieuw.
function offerHistoryChartBlock(product) {
  const range = offerHistoryRange(product, offerHistory.ranges[offerHistoryKey(product)]);
  const buttons = range.options.length
    ? `<div class="ohc-range" role="group" aria-label="${escapeHtml(t("offers.history.range.label"))}">${range.options
        .map((key) => `<button type="button" class="ohc-range-button" data-range="${key}" aria-pressed="${key === range.choice}">${escapeHtml(offerHistoryRangeLabel(key))}</button>`)
        .join("")}</div>`
    : "";
  return `<div id="offers-history-chart-block">${buttons}<div class="ohc-chart" id="offers-history-chart">${offerHistoryChart(product, offerHistoryChartWidth(), range.choice)}</div></div>`;
}

function offerHistoryChartWidth() {
  const box = $("#offers-history-detail");
  return box && box.clientWidth ? box.clientWidth : 640;
}

// Van hoeveel bestellingen en kassabonnen de producten nog niet zijn opgehaald: die aankopen ontbreken nog in de geschiedenis.
function offerHistoryUnsyncedText() {
  const counts = offerHistory.unsynced || {};
  const parts = [
    counts.orders ? tn("offers.history.unsynced_orders", counts.orders) : "",
    counts.receipts ? tn("offers.history.unsynced_receipts", counts.receipts) : "",
  ].filter(Boolean);
  return parts.length ? t("offers.history.unsynced_note", { what: parts.join(` ${t("offers.history.unsynced_and")} `) }) : "";
}

function renderOfferHistory() {
  const list = $("#offers-history-list");
  const detail = $("#offers-history-detail");
  const status = $("#offers-history-status");
  const products = offerHistory.products;
  const unsynced = offerHistoryUnsyncedText();
  if (!products.length) {
    list.innerHTML = "";
    detail.innerHTML = "";
    const none = offerHistory.query
      ? t(offerHistory.bought ? "offers.history.none_bought" : "offers.history.none", { query: offerHistory.query })
      : t(offerHistory.bought ? "offers.history.empty_bought" : "offers.history.empty");
    status.textContent = [none, unsynced].filter(Boolean).join(" ");
    return;
  }
  status.textContent = [tn("offers.history.count", offerHistory.total, { shown: products.length }), unsynced].filter(Boolean).join(" ");
  list.innerHTML = products
    .map((product) => {
      const key = offerHistoryKey(product);
      const lowest = typeof product.lowestPrice === "number" ? ` · ${t("offers.history.item.lowest", { price: offerHistoryPrice(product.lowestPrice) })}` : "";
      const observed = (product.observed || []).length ? ` · ${tn("offers.history.item.observed", product.observed.length)}` : "";
      const bought = product.timesBought ? ` · ${tn("offers.history.item.bought", product.timesBought)}` : "";
      const line = offerHistoryLineText(product) ? ` (${offerHistoryLineText(product)})` : "";
      const meta = [product.brand, product.unitSize].filter(Boolean).join(" · ");
      const href = priceHistoryPath(product.store, offerHistory.query, product.productId);
      return `<li><a class="ohc-item${key === offerHistory.selected ? " is-active" : ""}" data-key="${escapeHtml(key)}" href="${escapeHtml(href)}" data-nav${key === offerHistory.selected ? ' aria-current="true"' : ""}>
        <strong>${escapeHtml(product.title)}</strong>
        ${meta ? `<small>${escapeHtml(meta)}</small>` : ""}
        <small>${escapeHtml(tn("offers.history.item.times", offerHistoryTimes(product)))}${escapeHtml(line)}${escapeHtml(lowest)}${escapeHtml(bought)}${escapeHtml(observed)}${product.onOfferNow ? ` · <span class="bonus">${escapeHtml(t("offers.history.now"))}</span>` : product.upcoming ? ` · <span class="bonus">${escapeHtml(offerHistoryUpcomingText(product.upcoming))}</span>` : ""}</small>
      </a></li>`;
    })
    .join("");
  const chosen = products.find((product) => offerHistoryKey(product) === offerHistory.selected);
  detail.innerHTML = chosen ? offerHistoryDetail(chosen) : "";
  window.requestAnimationFrame(() => offerHistoryFitList(list)); // na dit tekenen verdwijnt de laadmelding erboven nog
}

// Op een breed scherm passen de lijst en het product samen precies op het scherm: elke kolom krijgt de hoogte van waar de
// kolommen beginnen tot waar de pagina ophoudt (pageFitHeight in app.js, gemeten aan de kolommen samen, want de lijst alleen
// kan korter zijn dan het product) en scrolt daarbinnen. Op een smal scherm geldt het maximum uit styles.css.
function offerHistoryFitList(list) {
  const height = pageFitHeight(list.parentElement, { minWidth: 900 });
  const value = height === null ? "" : `${height}px`;
  list.style.maxHeight = value;
  $("#offers-history-detail").style.maxHeight = value;
}

async function loadOfferHistory() {
  const loadId = ++offerHistory.loadId;
  const store = offersState.store;
  const query = $("#offers-history-search").value.trim();
  const bought = $("#offers-history-bought").checked;
  offerHistory.query = query;
  const loading = $("#offers-history-loading");
  if (!offerHistory.products.length) {
    loading.innerHTML = `${mdiIcon(MDI_LOADING, "offers-loading__icon")}<span>${escapeHtml(t("offers.history.loading"))}</span>`;
    loading.hidden = false;
  }
  try {
    const params = new URLSearchParams({ store, q: query, limit: String(OFFER_HISTORY_LIMIT) });
    if (bought) params.set("bought", "1");
    if (offerHistory.pin) params.set("pin", offerHistory.pin);
    const data = await jsonFetch(`/api/prices/history?${params}`);
    if (loadId !== offerHistory.loadId) return;
    offerHistory.bought = bought;
    offerHistory.products = data.products || [];
    offerHistory.total = data.total || 0;
    offerHistory.unsynced = (data.unsynced || {})[store] || null;
    offerHistory.loadedStore = store;
    if (!offerHistory.products.some((product) => offerHistoryKey(product) === offerHistory.selected)) {
      offerHistory.selected = offerHistory.products.length ? offerHistoryKey(offerHistory.products[0]) : "";
    }
    message("#offers-history-message", "");
    renderOfferHistory();
    if (typeof routerSync === "function") routerSync({ replace: true }); // de zoekterm in het adres, zonder een stap terug per letter
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
  offerHistory.pinned = false; // een nieuwe zoekterm: het gekozen product hoort niet meer in het adres
  offerHistory.pin = "";
  clearTimeout(offerHistory.timer);
  offerHistory.timer = setTimeout(loadOfferHistory, 250);
});
$("#offers-history-form").addEventListener("submit", (event) => {
  event.preventDefault();
  clearTimeout(offerHistory.timer);
  loadOfferHistory();
});
// De schakelaar "Alleen eerder gekocht": de keuze blijft bewaard in deze browser.
try {
  $("#offers-history-bought").checked = localStorage.getItem(OFFER_HISTORY_BOUGHT_KEY) === "1";
} catch {
  // Zonder opslag staat de schakelaar uit.
}
$("#offers-history-bought").addEventListener("change", (event) => {
  try {
    localStorage.setItem(OFFER_HISTORY_BOUGHT_KEY, event.target.checked ? "1" : "0");
  } catch {
    // De keuze geldt dan alleen tot je de pagina herlaadt.
  }
  clearTimeout(offerHistory.timer);
  loadOfferHistory();
});
$("#offers-history-list").addEventListener("click", (event) => {
  const item = event.target.closest("[data-key]");
  if (!item) return;
  offerHistory.selected = item.dataset.key;
  offerHistory.pinned = true;
  renderOfferHistory();
  if (typeof routerSync === "function") routerSync();
});
document.querySelector('.subtab[data-subtab="offers-history"]').addEventListener("click", () => {
  priceImportShowButton();
  loadOfferHistory();
});
window.addEventListener("ms:languagechange", () => {
  if (offerHistory.products.length || offerHistory.loadedStore) renderOfferHistory();
});
let offerHistoryResizeFrame = 0;
window.addEventListener("resize", () => {
  if (!document.querySelector("#offers-history.is-active")) return;
  cancelAnimationFrame(offerHistoryResizeFrame);
  offerHistoryResizeFrame = requestAnimationFrame(renderOfferHistory);
});

// Vergelijk verpakkingen (#178): één zoekopdracht bij de winkel naar hetzelfde product in andere maten, op prijs per kilo, liter of stuk.
// Kleur naar plek in de lijst (goedkoopste groen, duurste rood), zoals in het userscript van wolph. Geopend vanaf een aanbieding
// (offers.js) of vanaf de prijsgeschiedenis. Standaard alleen hetzelfde soort product (zie prices._same_kind); de filters erboven
// werken op wat er al gevonden is, zonder nieuwe zoekopdracht. Het gekozen product blijft altijd staan, om mee te vergelijken.
const packCompare = { loadId: 0, data: null };

function packCompareRankClass(rank) {
  if (rank <= 0.2) return "rank-1";
  if (rank <= 0.4) return "rank-2";
  if (rank <= 0.6) return "rank-3";
  if (rank <= 0.8) return "rank-4";
  return "rank-5";
}

function packCompareItem(candidate, rank) {
  const tags = [
    candidate.self ? t("offers.compare.this") : "",
    !candidate.self && candidate.same ? t("offers.compare.same") : "",
    candidate.cheaper ? t("offers.compare.cheaper") : "",
    candidate.onOffer ? t("offers.compare.on_offer") : "",
  ].filter(Boolean);
  const name = escapeHtml([candidate.title, candidate.unitSize].filter(Boolean).join(" · "));
  const title = candidate.url ? `<a href="${escapeHtml(candidate.url)}" target="_blank" rel="noopener noreferrer">${name}</a>` : name;
  return `<li class="compare-item${candidate.self ? " is-self" : ""}${candidate.same ? " is-same" : ""}">
    <span class="compare-item__unit ${packCompareRankClass(rank)}">${escapeHtml(offerPerUnitText(candidate.unitPrice))}</span>
    <span class="compare-item__body"><span>${title}</span>
      <small class="muted">${escapeHtml([formatPrice(candidate.price), ...tags].filter(Boolean).join(" · "))}</small></span>
  </li>`;
}

function packCompareVisible() {
  const candidates = (packCompare.data && packCompare.data.candidates) || [];
  const kind = $("#compare-filter-kind").checked;
  const brand = $("#compare-filter-brand").checked;
  const onOffer = $("#compare-filter-offer").checked;
  const text = $("#compare-filter-text").value.trim();
  return candidates.filter(
    (candidate) =>
      candidate.self ||
      ((!kind || candidate.sameKind) && (!brand || candidate.sameBrand) && (!onOffer || candidate.onOffer) && (!text || matchesWords(candidate.title, text)))
  );
}

// Het antwoord bovenaan: wat per eenheid het goedkoopst is van wat er nu te zien is, vergeleken met het gekozen product.
function packCompareSummary(visible) {
  const self = visible.find((candidate) => candidate.self);
  const others = visible.filter((candidate) => !candidate.self);
  if (!self || !self.unitPrice || !others.length) return "";
  const cheapest = others[0];
  const unit = t({ kg: "offers.unit.kg", l: "offers.unit.l" }[self.unitPrice.unit] || "offers.unit.stuk");
  if (cheapest.unitPrice.value < self.unitPrice.value - 0.005) {
    return t("offers.compare.cheaper_than", {
      title: [cheapest.title, cheapest.unitSize].filter(Boolean).join(" "),
      amount: formatPrice(self.unitPrice.value - cheapest.unitPrice.value),
      unit,
    });
  }
  return t("offers.compare.cheapest", { unit, count: I18N.formatNumber(visible.length) });
}

// Uitleg in het venster (laden, de telling, niets gevonden) als grijze tekst; alleen een echte fout staat in een rood vak (message).
function packCompareNote(text) {
  $("#compare-dialog-message").innerHTML = `<p class="muted">${escapeHtml(text)}</p>`;
}

function renderPackCompare() {
  const data = packCompare.data;
  if (!data) return;
  const candidates = data.candidates || [];
  const visible = packCompareVisible();
  $("#compare-dialog-filters").hidden = candidates.length < 2;
  $("#compare-dialog-summary").textContent = packCompareSummary(visible);
  const others = visible.filter((candidate) => !candidate.self).length;
  if (candidates.length < 2) packCompareNote(t("offers.compare.none"));
  else if (!others) packCompareNote(t("offers.compare.no_match"));
  else packCompareNote(t("offers.compare.count", { shown: I18N.formatNumber(visible.length), total: I18N.formatNumber(candidates.length) }));
  // De kleur volgt de plek in wat er te zien is: de goedkoopste groen, de duurste rood.
  $("#compare-dialog-list").innerHTML = visible.map((candidate, index) => packCompareItem(candidate, visible.length > 1 ? index / (visible.length - 1) : 0)).join("");
}

async function openPackCompare(store, productId) {
  const loadId = ++packCompare.loadId;
  const dialog = $("#compare-dialog");
  packCompare.data = null;
  $("#compare-filter-kind").checked = true;
  $("#compare-filter-brand").checked = false;
  $("#compare-filter-offer").checked = false;
  $("#compare-filter-text").value = "";
  $("#compare-dialog-filters").hidden = true;
  $("#compare-dialog-summary").textContent = "";
  $("#compare-dialog-title").textContent = t("offers.compare.title");
  $("#compare-dialog-meta").textContent = "";
  $("#compare-dialog-list").innerHTML = "";
  packCompareNote(t("offers.compare.loading"));
  if (!dialog.open) dialog.showModal();
  try {
    const data = await jsonFetch(`/api/prices/compare?${new URLSearchParams({ store, id: productId })}`);
    if (loadId !== packCompare.loadId) return;
    const product = data.product || {};
    packCompare.data = data;
    $("#compare-dialog-title").textContent = product.title || t("offers.compare.title");
    $("#compare-dialog-meta").textContent = [product.unitSize, offerPerUnitText(product.unitPrice), t("offers.compare.searched", { query: data.query })]
      .filter(Boolean)
      .join(" · ");
    renderPackCompare();
  } catch (error) {
    if (loadId !== packCompare.loadId) return;
    message("#compare-dialog-message", error.message);
  }
}

for (const id of ["#compare-filter-kind", "#compare-filter-brand", "#compare-filter-offer"]) $(id).addEventListener("change", renderPackCompare);
$("#compare-filter-text").addEventListener("input", renderPackCompare);
window.addEventListener("ms:languagechange", () => {
  if (packCompare.data) renderPackCompare();
});
$("#compare-dialog-close").addEventListener("click", () => $("#compare-dialog").close());
$("#compare-dialog").addEventListener("click", (event) => {
  if (event.target === event.currentTarget) event.currentTarget.close();
});
$("#offers-history-detail").addEventListener("click", (event) => {
  const button = event.target.closest("[data-compare]");
  if (button) openPackCompare(button.dataset.store, button.dataset.compare);
  const range = event.target.closest("[data-range]");
  const product = offerHistory.products.find((item) => offerHistoryKey(item) === offerHistory.selected);
  if (range && product) {
    offerHistory.ranges[offerHistoryKey(product)] = range.dataset.range;
    $("#offers-history-chart-block").outerHTML = offerHistoryChartBlock(product);
  }
});

// Oude aankopen importeren (#178): bestellingen uit bevestigingsmails, omgezet naar JSON door een AI-assistent met de prompt van de
// server (price_import.py). Eerst controleren (dryRun): wat nieuw is en wat er al is. Pas daarna importeren, met dezelfde tekst.
// Alleen de beheerder ziet de knop (priceImportIsOwner in orders.js); de server laat ook alleen de beheerder toe. Dezelfde dialoog
// opent ook bij Bestellingen, voor de winkel die daar gekozen is; de ingelezen bestellingen staan daar bij de afgelopen bestellingen.
const priceImport = { store: "", prompt: "", checked: null, busy: false };

function priceImportShowButton() {
  $("#offers-history-import").hidden = !priceImportIsOwner();
}

function priceImportStoreName() {
  return (STORES[priceImport.store] && STORES[priceImport.store].name) || priceImport.store;
}

function priceImportDay(iso) {
  const day = offerHistoryDay(iso);
  return day === null ? iso || "" : offerHistoryDayText(day, true);
}

function renderPriceImportStatus(summary) {
  const store = priceImportStoreName();
  $("#import-dialog-status").textContent = summary.orders
    ? tn("offers.import.status", summary.orders, { store, first: priceImportDay(summary.first), last: priceImportDay(summary.last) })
    : t("offers.import.status_none", { store });
}

function renderPriceImportReport(report) {
  const orders = (report.orders || []).map((order) => {
    // De status en het bezorgmoment uit de mail (#229), zoals ze straks bij Bestellingen staan, en wat er niet te lezen was.
    const what = [priceImportDay(order.date), slotLabel(order.slotStart, order.slotEnd), order.ref.startsWith("dag:") ? "" : `#${order.ref}`, tn("offers.import.items", order.items)].filter(Boolean).join(" · ");
    const state = [order.new ? t("offers.import.new") : order.reason, order.status === "delivered" ? t("orders.kind.delivered") : "", ...(order.notes || [])].filter(Boolean).join(" · ");
    return `<li class="${order.new ? "is-new" : "is-known"}"><span>${escapeHtml(what)}</span> <small>${escapeHtml(state || "")}</small></li>`;
  });
  const problems = (report.problems || []).map((problem) => `<li class="is-problem"><small>${escapeHtml(problem)}</small></li>`);
  const header = report.problemCount ? [`<li class="is-problem"><strong>${escapeHtml(tn("offers.import.problems", report.problemCount))}</strong></li>`] : [];
  $("#import-report").innerHTML = [...orders, ...header, ...problems].join("");
}

async function loadPriceImportSummary() {
  const summary = await jsonFetch(`/api/prices/import?${new URLSearchParams({ store: priceImport.store })}`);
  priceImport.prompt = summary.prompt || "";
  $("#import-prompt").textContent = priceImport.prompt;
  renderPriceImportStatus(summary);
}

async function openPriceImport(store) {
  priceImport.store = store;
  priceImport.checked = null;
  $("#import-run").disabled = true;
  $("#import-report").innerHTML = "";
  message("#import-dialog-message", "");
  $("#import-dialog").showModal();
  try {
    await loadPriceImportSummary();
  } catch (error) {
    message("#import-dialog-message", error.message);
  }
}

async function priceImportPost(dryRun) {
  const data = $("#import-data").value;
  if (!data.trim()) {
    message("#import-dialog-message", t("offers.import.empty"));
    return null;
  }
  return jsonFetch("/api/prices/import", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ store: priceImport.store, data, dryRun }),
  });
}

async function checkPriceImport() {
  if (priceImport.busy) return;
  priceImport.busy = true;
  $("#import-run").disabled = true;
  try {
    const report = await priceImportPost(true);
    if (!report) return;
    renderPriceImportReport(report);
    priceImport.checked = report.new ? $("#import-data").value : null;
    $("#import-run").disabled = !report.new;
    const summary = t("offers.import.checked", { new: I18N.formatNumber(report.new), duplicates: I18N.formatNumber(report.duplicates), lines: I18N.formatNumber(report.lines) });
    message("#import-dialog-message", report.new ? summary : `${summary} ${t("offers.import.nothing_new")}`, report.new ? "success" : "info");
  } catch (error) {
    $("#import-report").innerHTML = "";
    message("#import-dialog-message", error.message);
  } finally {
    priceImport.busy = false;
  }
}

async function runPriceImport() {
  if (priceImport.busy || priceImport.checked !== $("#import-data").value) return;
  priceImport.busy = true;
  $("#import-run").disabled = true;
  try {
    const report = await priceImportPost(false);
    if (!report) return;
    renderPriceImportReport(report);
    priceImport.checked = null;
    message("#import-dialog-message", report.imported ? tn("offers.import.done", report.imported) : t("offers.import.nothing_new"), "success");
    await loadPriceImportSummary();
    importedOrdersChanged(priceImport.store);
  } catch (error) {
    message("#import-dialog-message", error.message);
  } finally {
    priceImport.busy = false;
  }
}

async function copyPriceImportPrompt() {
  try {
    await navigator.clipboard.writeText(priceImport.prompt);
    message("#import-dialog-message", t("offers.import.copied"), "success");
  } catch {
    $("#import-dialog .import-prompt").open = true;
    message("#import-dialog-message", t("offers.import.copy_failed"));
  }
}

$("#offers-history-import").addEventListener("click", () => openPriceImport(offersState.store));
// Hier ingelezen, of bij Bestellingen met de prullenbak van een bestelling verwijderd: de Prijsgeschiedenis opnieuw als die
// openstaat (anders laadt het subtab hem toch opnieuw bij het openen). Alles in één keer weghalen kan niet meer in de dialoog:
// elke ingelezen bestelling heeft haar eigen prullenbak.
window.addEventListener("ms:imported-orders", () => {
  if (document.querySelector("#offers-history.is-active")) loadOfferHistory();
});
// Na een eigen koppeling (#238): de aankopen van die regel staan nu bij het gekozen product.
window.addEventListener("ms:product-links", () => {
  if (document.querySelector("#offers-history.is-active")) loadOfferHistory();
});
$("#import-copy").addEventListener("click", copyPriceImportPrompt);
$("#import-check").addEventListener("click", checkPriceImport);
$("#import-run").addEventListener("click", runPriceImport);
$("#import-data").addEventListener("input", () => {
  $("#import-run").disabled = priceImport.checked === null || priceImport.checked !== $("#import-data").value;
});
$("#import-close").addEventListener("click", () => $("#import-dialog").close());
$("#import-dialog").addEventListener("click", (event) => {
  if (event.target === event.currentTarget) event.currentTarget.close();
});
