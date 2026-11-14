// Het tabblad Aanbiedingen > Gevolgde producten (watched_products.py): losse artikelen waarvan de server elke nacht de prijs, de maat,
// de verpakking en de afbeelding bewaart, los van de aanbiedingen. Zoek bij een winkel of plak een productlink van ah.nl of
// jumbo.com, volg een product, en zie per product een lijn van de gewone prijs, wat er veranderde en de bewaarde afbeeldingen.
// De Prijsgeschiedenis (offers_history.js) en de Losse aankopen (loose_names.js) gebruiken watchedIs, watchedBadge en watchedFollow.
// Laadt na offers_history.js en gebruikt offersState, STORES, jsonFetch, message, escapeHtml, storeImage, offerHistoryPrice,
// offerHistoryDay, offerHistoryDayText en priceHistoryPath.

const watched = { products: [], results: [], keys: new Set(), selected: "", loaded: false, loadId: 0, searchId: 0, busy: false };
const WATCHED_CHART_HEIGHT = 180;
// De soorten verpakking van watched_products.packaging_type, met hun tekst.
const WATCHED_PACKAGING = {
  multipack: "watched.pack.multipack",
  can: "watched.pack.can",
  bottle: "watched.pack.bottle",
  jar: "watched.pack.jar",
  carton: "watched.pack.carton",
  tub: "watched.pack.tub",
  tube: "watched.pack.tube",
  bag: "watched.pack.bag",
  box: "watched.pack.box",
  bunch: "watched.pack.bunch",
  piece: "watched.pack.piece",
  weighed: "watched.pack.weighed",
};
// Wat er tussen twee dagen veranderde (watched_products.changes).
const WATCHED_CHANGE = {
  regular: "watched.change.regular",
  unitSize: "watched.change.unit_size",
  packaging: "watched.change.packaging",
  title: "watched.change.title",
  image: "watched.change.image",
};

function watchedKey(store, productId) {
  return `${store}:${productId}`;
}

function watchedIs(store, productId) {
  return watched.keys.has(watchedKey(store, productId));
}

function watchedPackaging(kind) {
  return kind && WATCHED_PACKAGING[kind] ? t(WATCHED_PACKAGING[kind]) : t("watched.pack.unknown");
}

// De markering bij een gevolgd product: een los artikel, geen aanbieding. Ook in de Prijsgeschiedenis.
function watchedBadge() {
  return `<span class="badge badge--watched" title="${escapeHtml(t("watched.badge_title"))}">${escapeHtml(t("watched.badge"))}</span>`;
}

function watchedPrice(value) {
  return typeof value === "number" ? offerHistoryPrice(value) : "–";
}

function watchedChangeText(change) {
  const label = t(WATCHED_CHANGE[change.field] || "watched.change.title");
  const show = (value) => (change.field === "regular" ? watchedPrice(value) : change.field === "packaging" ? watchedPackaging(value) : String(value));
  const day = offerHistoryDayText(offerHistoryDay(change.day), true);
  if (change.field === "image") return `${day}: ${label}`;
  return `${day}: ${label} ${show(change.from)} → ${show(change.to)}`;
}

function watchedTrend(product) {
  if (typeof product.change !== "number" || !product.firstRegular) return "";
  if (Math.abs(product.change) < 0.005) return t("watched.trend.same", { since: offerHistoryDayText(offerHistoryDay(product.firstRegular.day), true) });
  const amount = offerHistoryPrice(Math.abs(product.change));
  const percent = typeof product.changePercent === "number" ? ` (${product.change > 0 ? "+" : "−"}${I18N.formatNumber(Math.abs(product.changePercent))}%)` : "";
  const since = offerHistoryDayText(offerHistoryDay(product.firstRegular.day), true);
  return t(product.change > 0 ? "watched.trend.up" : "watched.trend.down", { amount: amount + percent, since });
}

// ---- HTML

function watchedResultHtml(product) {
  const meta = [product.brand, product.unitSize, product.packaging ? watchedPackaging(product.packaging) : ""].filter(Boolean).join(" · ");
  const offer = product.onOffer ? ` <span class="bonus">${escapeHtml(product.offerLabel || t("watched.on_offer"))}</span>` : "";
  const regular = product.onOffer && typeof product.regular === "number" ? ` · ${t("watched.regular_short", { price: watchedPrice(product.regular) })}` : "";
  const button = product.watched
    ? `<button class="button button--secondary" type="button" disabled>${escapeHtml(t("watched.following"))}</button>`
    : `<button class="button" type="button" data-watch-add="${escapeHtml(product.productId)}" data-store="${escapeHtml(product.store)}">${escapeHtml(t("watched.follow"))}</button>`;
  return `<li class="watched-result">
      ${product.image ? `<img src="${escapeHtml(storeImage(product.image))}" alt="" loading="lazy" data-hide-on-error>` : '<span class="watched-result__noimg"></span>'}
      <div>
        <strong>${escapeHtml(product.title)}</strong>
        <small>${escapeHtml(storeDisplayName(product.store))}${meta ? ` · ${escapeHtml(meta)}` : ""}</small>
        <small>${escapeHtml(watchedPrice(product.price))}${offer}${escapeHtml(regular)}</small>
      </div>
      ${button}
    </li>`;
}

function watchedItemHtml(product) {
  const key = watchedKey(product.store, product.productId);
  const active = key === watched.selected;
  const meta = [storeDisplayName(product.store), product.unitSize, product.packaging ? watchedPackaging(product.packaging) : ""].filter(Boolean).join(" · ");
  const trend = watchedTrend(product);
  const cls = typeof product.change === "number" && product.change > 0.005 ? "watched-up" : typeof product.change === "number" && product.change < -0.005 ? "watched-down" : "";
  return `<li><button class="ohc-item${active ? " is-active" : ""}" type="button" data-watched-key="${escapeHtml(key)}"${active ? ' aria-current="true"' : ""}>
      <strong>${escapeHtml(product.title)}</strong>
      <small>${escapeHtml(meta)}</small>
      <small>${escapeHtml(watchedPrice(typeof product.regular === "number" ? product.regular : product.price))}${trend ? ` · <span class="${cls}">${escapeHtml(trend)}</span>` : ""}${product.missingSince ? ` · ${escapeHtml(t("watched.missing"))}` : ""}</small>
    </button></li>`;
}

// De lijn van de gewone prijs per dag; een dag met een aanbieding is een rode stip op de prijs die je toen betaalde.
function watchedChart(product, width) {
  const points = (product.points || []).filter((point) => offerHistoryDay(point.day) !== null);
  if (!points.length) return `<p class="muted">${escapeHtml(t("watched.no_points"))}</p>`;
  const values = points.flatMap((point) => [point.regular, point.price]).filter((value) => typeof value === "number");
  const days = points.map((point) => offerHistoryDay(point.day));
  const first = Math.min(...days);
  const last = Math.max(...days, first + 6);
  const low = Math.min(...values);
  const high = Math.max(...values);
  const pad = Math.max((high - low) * 0.15, 0.1);
  const bottom = Math.max(0, low - pad);
  const top = high + pad;
  const left = 52;
  const right = 12;
  const height = WATCHED_CHART_HEIGHT;
  const x = (day) => left + ((day - first) / (last - first)) * (width - left - right);
  const y = (value) => 10 + (1 - (value - bottom) / (top - bottom)) * (height - 34);
  const line = points.filter((point) => typeof point.regular === "number").map((point) => `${x(offerHistoryDay(point.day)).toFixed(1)},${y(point.regular).toFixed(1)}`);
  const steps = [bottom, (bottom + top) / 2, top];
  const axis = steps
    .map((value) => `<text x="${left - 6}" y="${y(value) + 4}" text-anchor="end">${escapeHtml(offerHistoryPrice(value))}</text><line x1="${left}" x2="${width - right}" y1="${y(value)}" y2="${y(value)}" class="watched-chart__grid"/>`)
    .join("");
  const offers = points
    .filter((point) => point.onOffer && typeof point.price === "number")
    .map((point) => `<circle class="watched-chart__offer" cx="${x(offerHistoryDay(point.day))}" cy="${y(point.price)}" r="3.5"><title>${escapeHtml(`${offerHistoryDayText(offerHistoryDay(point.day), true)} · ${offerHistoryPrice(point.price)} · ${point.offerLabel || t("watched.on_offer")}`)}</title></circle>`)
    .join("");
  const dots = points
    .filter((point) => typeof point.regular === "number")
    .map((point) => `<circle class="watched-chart__dot" cx="${x(offerHistoryDay(point.day))}" cy="${y(point.regular)}" r="2.5"><title>${escapeHtml(`${offerHistoryDayText(offerHistoryDay(point.day), true)} · ${offerHistoryPrice(point.regular)}`)}</title></circle>`)
    .join("");
  const labels = `<text x="${left}" y="${height - 6}">${escapeHtml(offerHistoryDayText(first, true))}</text><text x="${width - right}" y="${height - 6}" text-anchor="end">${escapeHtml(offerHistoryDayText(last, true))}</text>`;
  return `<svg class="watched-chart" viewBox="0 0 ${width} ${height}" width="100%" role="img" aria-label="${escapeHtml(t("watched.chart_label", { title: product.title }))}">
      ${axis}${line.length > 1 ? `<polyline class="watched-chart__line" points="${line.join(" ")}"/>` : ""}${dots}${offers}${labels}
    </svg>`;
}

function watchedTableHtml(product) {
  const rows = (product.points || [])
    .slice()
    .reverse()
    .map((point) => `<tr>
        <td>${escapeHtml(offerHistoryDayText(offerHistoryDay(point.day), true))}</td>
        <td>${escapeHtml(watchedPrice(point.regular))}</td>
        <td>${point.onOffer ? `<span class="bonus">${escapeHtml(watchedPrice(point.price))}</span>${point.offerLabel ? ` <small>${escapeHtml(point.offerLabel)}</small>` : ""}` : "–"}</td>
        <td>${escapeHtml(point.unitSize || "–")}</td>
        <td>${escapeHtml(watchedPackaging(point.packaging))}</td>
      </tr>`)
    .join("");
  return `<details class="watched-table"><summary>${escapeHtml(tn("watched.table_title", (product.points || []).length))}</summary>
      <table class="ohc-table">
        <thead><tr><th>${escapeHtml(t("watched.col.day"))}</th><th>${escapeHtml(t("watched.col.regular"))}</th><th>${escapeHtml(t("watched.col.offer"))}</th><th>${escapeHtml(t("watched.col.size"))}</th><th>${escapeHtml(t("watched.col.packaging"))}</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </details>`;
}

function watchedImagesHtml(product) {
  const images = product.savedImages || [];
  if (!images.length) return "";
  return `<section class="watched-images"><h4>${escapeHtml(t("watched.images"))}</h4><ul>${images
    .map((image) => `<li><img src="${escapeHtml(image.url)}" alt="${escapeHtml(image.title)}" loading="lazy" data-hide-on-error><small>${escapeHtml(offerHistoryDayText(offerHistoryDay(image.savedAt), true))}</small></li>`)
    .join("")}</ul></section>`;
}

function watchedDetailHtml(product, width) {
  const image = ((product.savedImages || [])[0] || {}).url || (product.image ? storeImage(product.image) : "");
  const link = product.url
    ? `<a href="${escapeHtml(product.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(t("offers.history.open", { store: storeDisplayName(product.store) }))}</a>`
    : "";
  const history = `<a href="${escapeHtml(priceHistoryPath(product.store, product.title, product.productId))}" data-nav>${escapeHtml(t("watched.open_history"))}</a>`;
  const unit = product.unitPrice ? `${offerHistoryPrice(product.unitPrice.value)} / ${product.unitPrice.unit}` : "–";
  const stats = [
    [t("watched.stat.now"), product.onOffer ? `${watchedPrice(product.price)} (${product.offerLabel || t("watched.on_offer")})` : watchedPrice(product.price)],
    [t("watched.stat.regular"), watchedPrice(product.regular)],
    [t("watched.stat.first"), product.firstRegular ? `${watchedPrice(product.firstRegular.price)} (${offerHistoryDayText(offerHistoryDay(product.firstRegular.day), true)})` : "–"],
    [t("watched.stat.range"), typeof product.lowest === "number" ? `${watchedPrice(product.lowest)} – ${watchedPrice(product.highest)}` : "–"],
    [t("watched.stat.unit"), unit],
    [t("watched.stat.packaging"), `${product.unitSize || "–"} · ${watchedPackaging(product.packaging)}`],
  ]
    .map(([label, value]) => offerHistoryStat(label, value))
    .join("");
  const changes = (product.changes || []).slice().reverse();
  return `<div class="ohc-head">
      ${image ? `<img src="${escapeHtml(image)}" alt="" loading="lazy" data-hide-on-error>` : ""}
      <div>
        <h3>${escapeHtml(product.title)}</h3>
        <p>${watchedBadge()}</p>
        <p class="muted">${escapeHtml([product.brand, storeDisplayName(product.store)].filter(Boolean).join(" · "))}${link ? ` · ${link}` : ""} · ${history}</p>
        ${watchedTrend(product) ? `<p><strong>${escapeHtml(watchedTrend(product))}</strong></p>` : ""}
        ${product.missingSince ? `<p class="muted">${escapeHtml(t("watched.missing_since", { date: offerHistoryDayText(offerHistoryDay(product.missingSince), true) }))}</p>` : ""}
      </div>
    </div>
    <dl class="ohc-stats">${stats}</dl>
    ${watchedChart(product, width)}
    <p class="muted watched-legend"><span class="watched-legend__line"></span>${escapeHtml(t("watched.legend.regular"))} <span class="watched-legend__offer"></span>${escapeHtml(t("watched.legend.offer"))}</p>
    ${changes.length ? `<section><h4>${escapeHtml(t("watched.changes"))}</h4><ul class="watched-changes">${changes.map((change) => `<li>${escapeHtml(watchedChangeText(change))}</li>`).join("")}</ul></section>` : ""}
    ${watchedImagesHtml(product)}
    ${watchedTableHtml(product)}
    <p><button class="button button--secondary" type="button" data-watch-remove="${escapeHtml(product.productId)}" data-store="${escapeHtml(product.store)}">${escapeHtml(t("watched.unfollow"))}</button></p>`;
}

// ---- tekenen en laden

function renderWatchedResults() {
  $("#watched-results").innerHTML = watched.results.map(watchedResultHtml).join("");
}

function renderWatched() {
  const list = $("#watched-list");
  const detail = $("#watched-detail");
  const status = $("#watched-status");
  if (!watched.products.length) {
    list.innerHTML = "";
    detail.innerHTML = "";
    status.textContent = watched.loaded ? t("watched.empty") : "";
    return;
  }
  status.textContent = tn("watched.count", watched.products.length);
  if (!watched.products.some((product) => watchedKey(product.store, product.productId) === watched.selected)) {
    watched.selected = watchedKey(watched.products[0].store, watched.products[0].productId);
  }
  list.innerHTML = watched.products.map(watchedItemHtml).join("");
  const chosen = watched.products.find((product) => watchedKey(product.store, product.productId) === watched.selected);
  const width = Math.max(280, Math.min(720, Math.round(detail.clientWidth || 600) - 8));
  detail.innerHTML = chosen ? watchedDetailHtml(chosen, width) : "";
}

async function loadWatchedKeys() {
  try {
    const data = await jsonFetch("/api/watched/keys");
    watched.keys = new Set(data.keys || []);
  } catch {
    // Zonder de lijst staat er geen markering in de Prijsgeschiedenis; verder werkt alles.
  }
}

async function loadWatched() {
  const loadId = ++watched.loadId;
  try {
    const data = await jsonFetch("/api/watched");
    if (loadId !== watched.loadId) return;
    watched.products = data.products || [];
    watched.keys = new Set(watched.products.map((product) => watchedKey(product.store, product.productId)));
    watched.loaded = true;
    renderWatched();
  } catch (error) {
    if (loadId === watched.loadId) message("#watched-message", error.message);
  }
}

async function searchWatched() {
  const query = $("#watched-search").value.trim();
  const searchId = ++watched.searchId;
  message("#watched-message", "");
  if (!query) {
    watched.results = [];
    renderWatchedResults();
    return;
  }
  $("#watched-results").innerHTML = `<li class="muted">${escapeHtml(t("watched.searching"))}</li>`;
  try {
    const params = new URLSearchParams({ store: $("#watched-store").value, q: query });
    const data = await jsonFetch(`/api/watched/search?${params}`);
    if (searchId !== watched.searchId) return;
    watched.results = data.products || [];
    // Een geplakte link van een andere winkel: de keuze volgt de link.
    if (data.store && $("#watched-store").value !== data.store) $("#watched-store").value = data.store;
    renderWatchedResults();
    if (!watched.results.length) $("#watched-results").innerHTML = `<li class="muted">${escapeHtml(t("watched.no_results", { query }))}</li>`;
  } catch (error) {
    if (searchId !== watched.searchId) return;
    $("#watched-results").innerHTML = "";
    message("#watched-message", error.message);
  }
}

// Volg een product. `product` is wat de pagina al weet (naam, maat, prijs, afbeelding): de server gebruikt het als de winkel nu niet
// antwoordt. Ook voor de Prijsgeschiedenis en de Losse aankopen. Geeft het gevolgde product, of gooit een fout.
async function watchedFollow(store, productId, product = null) {
  const data = await jsonFetch("/api/watched", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ store, productId: String(productId), product }),
  });
  watched.keys.add(watchedKey(store, productId));
  return data;
}

async function watchedUnfollow(store, productId) {
  await jsonFetch(`/api/watched/${encodeURIComponent(store)}/${encodeURIComponent(productId)}`, { method: "DELETE" });
  watched.keys.delete(watchedKey(store, productId));
}

function watchedFillStores() {
  const select = $("#watched-store");
  const current = select.value || (typeof offersState !== "undefined" && STORES[offersState.store] ? offersState.store : "ah");
  select.innerHTML = Object.keys(STORES)
    .map((key) => `<option value="${escapeHtml(key)}"${key === current ? " selected" : ""}>${escapeHtml(STORES[key].name)}</option>`)
    .join("");
}

$("#watched-form").addEventListener("submit", (event) => {
  event.preventDefault();
  searchWatched();
});
// Een geplakte link zoekt meteen.
$("#watched-search").addEventListener("paste", () => setTimeout(() => {
  if (/^\s*(https?:\/\/|www\.)/i.test($("#watched-search").value)) searchWatched();
}, 0));
$("#watched-results").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-watch-add]");
  if (!button) return;
  const found = watched.results.find((product) => product.productId === button.dataset.watchAdd && product.store === button.dataset.store);
  button.disabled = true;
  try {
    const added = await watchedFollow(button.dataset.store, button.dataset.watchAdd, found || null);
    if (found) found.watched = true;
    watched.selected = watchedKey(added.store, added.productId);
    renderWatchedResults();
    message("#watched-message", t("watched.added", { title: added.title }), "success");
    await loadWatched();
  } catch (error) {
    button.disabled = false;
    message("#watched-message", error.message);
  }
});
$("#watched-list").addEventListener("click", (event) => {
  const item = event.target.closest("[data-watched-key]");
  if (!item) return;
  watched.selected = item.dataset.watchedKey;
  renderWatched();
});
$("#watched-detail").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-watch-remove]");
  if (!button) return;
  const product = watched.products.find((item) => item.productId === button.dataset.watchRemove && item.store === button.dataset.store);
  if (!(await confirmDialog(t("watched.unfollow_confirm", { title: product ? product.title : "" }), { confirm: t("watched.unfollow"), danger: true }))) return;
  button.disabled = true;
  try {
    await watchedUnfollow(button.dataset.store, button.dataset.watchRemove);
    watched.results.forEach((result) => {
      if (result.store === button.dataset.store && result.productId === button.dataset.watchRemove) result.watched = false;
    });
    renderWatchedResults();
    await loadWatched();
  } catch (error) {
    button.disabled = false;
    message("#watched-message", error.message);
  }
});
$("#watched-refresh").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  message("#watched-message", t("watched.refreshing"), "info");
  try {
    const result = await jsonFetch("/api/watched/refresh", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    message("#watched-message", t("watched.refreshed", { updated: I18N.formatNumber(result.updated || 0), missing: I18N.formatNumber(result.missing || 0) }), "success");
    await loadWatched();
  } catch (error) {
    message("#watched-message", error.message);
  } finally {
    button.disabled = false;
  }
});
document.querySelector('.subtab[data-subtab="offers-watched"]').addEventListener("click", () => {
  watchedFillStores();
  loadWatched();
});
window.addEventListener("ms:languagechange", () => {
  if (watched.loaded) {
    renderWatched();
    renderWatchedResults();
  }
});
loadWatchedKeys();
