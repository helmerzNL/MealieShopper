// De bestelpagina: één bestelling (AH, Jumbo, ingelezen) of kassabon per categorie, als kaarten, als tabel of als bon, met de
// weergavebalk van purchase_views.js (prefix "order"), een zoekveld en de categoriechips (categories.js). Laadt na
// purchase_views.js. OpenPurchase in orders.js haalt de aankoop op en roept renderOrderPage aan. Wisselen van weergave, zoeken,
// filteren en sorteren tekenen opnieuw uit de aankoop die er al staat, zonder verzoek.
//
// orderLines en orderGroups zijn puur (geen DOM): de hovervensters op de aankopenlijst gebruiken ze ook.

const MDI_VIEW_CATEGORY = "M19,13H3A1,1 0 0,0 2,14V20A1,1 0 0,0 3,21H19A1,1 0 0,0 20,20V14A1,1 0 0,0 19,13M19,3H3A1,1 0 0,0 2,4V10A1,1 0 0,0 3,11H19A1,1 0 0,0 20,10V4A1,1 0 0,0 19,3Z";

// De weergaven, en welke bij welke soort aankoop horen: Bon alleen bij een kassabon. Een bestelling begint per categorie, een bon
// als bon. De weergave wordt per soort bewaard (ms-orders-view als JSON), sorteren en kaartgrootte één keer voor beide.
const ORDER_VIEWS = ["category", "cards", "table", "paper"];
const ORDER_SORT_KEYS = ["category", "name", "amount", "discount"];
const ORDER_SCOPE_VIEWS = { order: ["category", "cards", "table"], receipt: ["category", "cards", "table", "paper"] };
const ORDER_VIEW_DEFAULTS = { order: "category", receipt: "paper" };
const orderView = viewState("order", { view: ORDER_VIEW_DEFAULTS, sort: { key: "category", dir: "asc" }, options: { size: "medium" } }, { views: ORDER_SCOPE_VIEWS, sorts: ORDER_SORT_KEYS, sizes: PURCHASE_SIZES, options: [] }, { scopes: ["order", "receipt"] });

// De aankoop die open staat, met zijn regels en wat er gezocht en gekozen is. `key` herkent dezelfde aankoop bij opnieuw tekenen
// (een bon waarvan de producten net gekoppeld zijn): dan blijven het zoeken en de chip staan.
const orderPage = { purchase: null, kind: null, store: "ah", key: "", lines: [], query: "", words: [], category: "all" };

function orderScope(kind) {
  return kind === "receipt" ? "receipt" : "order";
}

function orderViewLoad() {
  orderView.load();
}

// De weergave voor een soort. Wat er niet bij past (Bon bij een bestelling) wordt de standaard van die soort.
function orderViewFor(scope) {
  const view = orderView.viewFor(scope);
  return ORDER_SCOPE_VIEWS[scope].includes(view) ? view : ORDER_VIEW_DEFAULTS[scope];
}

// Wat een regel bespaarde: (gewone prijs - betaald per stuk) x aantal als de gewone prijs hoger ligt, anders niets. Zelfde som als
// purchase_lines.enrich op de server (op een bon is de gewone prijs zonder geschiedenis de prijs vóór de bonus).
function orderLineSaved(line) {
  const { regularPrice: regular, paidEach: paid, quantity } = line;
  return regular !== null && paid !== null && regular > paid && quantity > 0 ? Math.round((regular - paid) * quantity * 100) / 100 : 0;
}

// Eén regel van een bestelling, bon of ingelezen bestelling in de vorm die de weergaven gebruiken. Bij een bon is `title` de titel
// van het gekoppelde webwinkelproduct (anders de naam op de bon) en `name` de naam op de bon. Zonder koppeling geen link en geen
// foto. `id` is de plaats in de aankoop (sorteren op categorie houdt binnen een categorie die volgorde). `linkLine` is wat de knop
// Koppelen of Ander product kiezen (#238, product_links.js) van de regel moet weten: alleen bij een bon of ingelezen bestelling
// (receiptLinkLine en importedLinkLine in orders.js), bij een bestelling uit je account null.
function orderLine(item, index, kind, store) {
  const receipt = kind === "receipt";
  const product = receipt ? item.product || null : null;
  const name = receipt ? String(item.name || "") : "";
  const title = String((receipt ? (product && product.title) || name : item.title || item.name) || "");
  const productId = receipt ? (product ? product.id : null) : item.productId ?? null;
  const quantity = Number(item.quantity) || 0;
  const amount = typeof item.amount === "number" ? item.amount : null;
  const each = quantity > 0 && amount !== null ? amount / quantity : null;
  const bonus = receipt ? (typeof item.bonus === "number" ? item.bonus : 0) : Boolean(item.bonus);
  // Wat je per stuk betaalde rekent de server (purchase_lines.enrich: op een bon de prijs min de bonus per stuk). De prijs is die
  // op de bon of in de bestelling, bij één stuk op een bon vaak alleen het bedrag. Bij een ingelezen bestelling wat je betaalde.
  const paidEach = typeof item.paidEach === "number" ? item.paidEach : null;
  const price = kind === "imported" ? paidEach : typeof item.price === "number" ? item.price : each;
  const line = {
    id: index,
    title,
    name,
    brand: item.brand || "",
    unitSize: item.unitSize || "",
    image: (product ? product.image : item.image) || null,
    quantity,
    price,
    paidEach,
    amount,
    category: CATEGORY_ORDER.includes(item.category) ? item.category : "overig",
    discount: typeof item.discount === "number" ? item.discount : null,
    discountSource: item.discountSource || null,
    timesBefore: Number(item.timesBefore) || 0,
    bonus,
    regularPrice: typeof item.regularPrice === "number" ? item.regularPrice : null,
    productId,
    href: title && (!receipt || product) ? priceHistoryPath(store, title, productId || "") : null,
    linkLine: receipt ? receiptLinkLine(item) : kind === "imported" ? importedLinkLine(item, store) : null,
  };
  line.saved = orderLineSaved(line);
  line.paid = orderLinePaid(line, receipt);
  return line;
}

// Wat een regel kostte na de bonus. Een bon noemt het bedrag vóór de bonus: daar gaat de bonus van de regel af (het bedrag min de
// bonus, en zonder bedrag wat je per stuk betaalde maal het aantal). Een bestelling en een ingelezen bestelling noemen het bedrag
// dat je betaalde.
function orderLinePaid(line, receipt) {
  if (!receipt) return line.amount;
  if (line.amount !== null) return Math.round(Math.max(line.amount - (Number(line.bonus) || 0), 0) * 100) / 100;
  return line.paidEach !== null && line.quantity > 0 ? Math.round(line.paidEach * line.quantity * 100) / 100 : null;
}

function orderLines(purchase, kind, store) {
  return (purchase.items || []).map((item, index) => orderLine(item, index, kind, store));
}

// Zoeken op de titel en op de naam op de bon (alle woorden moeten passen, zoals in de aankopenlijst), en de chip van een categorie.
function orderFilter(lines, words, category) {
  return lines.filter(
    (line) => (category === "all" || line.category === category) && (!words.length || matchesQuery(line.title, words) || matchesQuery(line.name, words))
  );
}

// Sorteren op categorie (de volgorde van de aanbiedingen), naam, bedrag of korting. Een regel zonder waarde staat onderaan, in
// beide richtingen. Gelijke waarden houden de volgorde van de aankoop. De regels zelf blijven zoals ze waren.
function orderSortLines(lines, sort = orderView.sort) {
  const { key, dir } = sort;
  const sign = dir === "asc" ? 1 : -1;
  const category = (line) => {
    const index = CATEGORY_ORDER.indexOf(line.category);
    return index < 0 ? CATEGORY_ORDER.indexOf("overig") : index;
  };
  const value = (line) => (key === "category" ? category(line) : key === "name" ? String(line.title || "") : key === "amount" ? line.amount : line.discount);
  const position = (a, b) => (a.id ?? 0) - (b.id ?? 0);
  return [...lines].sort((a, b) => {
    const va = value(a);
    const vb = value(b);
    const missingA = va === null || va === undefined;
    const missingB = vb === null || vb === undefined;
    if (missingA || missingB) return missingA === missingB ? position(a, b) : missingA ? 1 : -1;
    const order = typeof va === "number" ? va - vb : va.localeCompare(vb, undefined, { sensitivity: "base" });
    return order ? order * sign : position(a, b);
  });
}

// De regels per categorie, in de volgorde van de aanbiedingen. Een categorie zonder regels staat er niet. Per groep de stuks, de
// soorten (regels), het subtotaal (wat de regels na de bonus kostten, `paid`, zodat de subtotalen van een bon optellen tot wat je
// betaalde) en wat er bespaard is. De regels houden de volgorde die ze binnenkregen.
function orderGroups(lines) {
  const groups = new Map();
  for (const line of lines) {
    const id = CATEGORY_ORDER.includes(line.category) ? line.category : "overig";
    if (!groups.has(id)) groups.set(id, { id, lines: [], units: 0, kinds: 0, amount: 0, saved: 0 });
    const group = groups.get(id);
    group.lines.push(line);
    group.units += Number(line.quantity) || 0;
    group.kinds += 1;
    group.amount += Number(line.paid ?? line.amount) || 0;
    group.saved += Number(line.saved) || 0;
  }
  return CATEGORY_ORDER.filter((id) => groups.has(id)).map((id) => {
    const group = groups.get(id);
    return { ...group, amount: Math.round(group.amount * 100) / 100, saved: Math.round(group.saved * 100) / 100 };
  });
}

// De badges van een regel: de korting (dezelfde badge als bij de aanbiedingen, maar in de tekst in plaats van op een plaatje), hoe
// vaak je het eerder kocht en de bonus.
function orderLineBadges(line) {
  const parts = [offerBadge({ badge: { percent: line.discount, source: line.discountSource } }, "offer-badge--inline")];
  if (line.timesBefore > 0) parts.push(badge("before", t("orders.before", { count: line.timesBefore })));
  if (line.bonus) parts.push(badge("bonus", t("list.bonus"), typeof line.bonus === "number" ? `-${money(line.bonus)}` : ""));
  return parts.filter(Boolean).join(" ");
}

// De naam van een regel, als link naar de prijsgeschiedenis zodra de regel bij een webwinkelproduct hoort.
function orderLineName(line) {
  return line.href ? `<a href="${escapeHtml(line.href)}" data-route>${escapeHtml(line.title)}</a>` : escapeHtml(line.title);
}

// De knop Koppelen of Ander product kiezen (#238, product_links.js) bij een regel van een bon of ingelezen bestelling, anders niets.
function orderLineLink(line) {
  return line.linkLine ? productLinkButton(line.linkLine) : "";
}

// "13 stuks in 8 soorten", met het meervoud van beide.
function orderGroupSummary(group) {
  return t("orders.group.summary", { units: tn("orders.group.units", group.units), kinds: tn("orders.group.kinds", group.kinds) });
}

// Een compacte regel in een categoriesectie: foto, naam (link naar de prijsgeschiedenis), merk en inhoud, badges, aantal, bedrag.
// Achter de naam van een regel van een bon of ingelezen bestelling de knop Koppelen (#238), zoals op het bonnetje.
function orderCategoryLine(line) {
  const name = orderLineName(line) + orderLineLink(line);
  const meta = [line.brand, line.unitSize].filter(Boolean).join(" · ");
  const photo = line.image ? `<img src="${escapeHtml(storeImage(line.image))}" alt="" loading="lazy" data-hide-on-error>` : "";
  const badges = orderLineBadges(line);
  return `<li class="cat-line">
      <span class="cat-line__photo">${photo}</span>
      <span class="cat-line__body">
        <span class="cat-line__name">${name}</span>
        ${meta ? `<small class="cat-line__meta">${escapeHtml(meta)}</small>` : ""}
        ${badges ? `<span class="cat-line__badges">${badges}</span>` : ""}
      </span>
      <span class="cat-line__qty">${escapeHtml(line.quantity)}x</span>
      <span class="cat-line__amount">${escapeHtml(money(line.amount))}</span>
    </li>`;
}

// Een categorie met een gekleurde kop (naam, stuks en soorten, bespaard, subtotaal) en de regels eronder.
function orderCategorySection(group) {
  const saved = group.saved > 0 ? badge("saved", t("orders.group.saved", { amount: money(group.saved) })) : "";
  return `<section class="cat-section cat--${escapeHtml(group.id)}">
      <header class="cat-section__head">
        <h3 class="cat-section__title"><i class="offer-chip__dot" aria-hidden="true"></i>${escapeHtml(categoryLabel(group.id))}</h3>
        <span class="cat-section__count">${escapeHtml(orderGroupSummary(group))}</span>
        ${saved}
        <span class="cat-section__amount">${escapeHtml(money(group.amount))}</span>
      </header>
      <ul class="cat-section__lines">${group.lines.map(orderCategoryLine).join("")}</ul>
    </section>`;
}

function orderTableRow(line) {
  const name = orderLineName(line) + orderLineLink(line);
  const bonus = line.bonus ? ` ${badge("bonus", t("list.bonus"), typeof line.bonus === "number" ? `-${money(line.bonus)}` : "")}` : "";
  return `<tr class="order-table__row cat--${escapeHtml(line.category)}">
      <td class="order-table__cat"><i class="offer-chip__dot" aria-hidden="true"></i>${escapeHtml(categoryLabel(line.category))}</td>
      <td class="order-table__name">${name}${line.brand ? `<small>${escapeHtml(line.brand)}</small>` : ""}${bonus}</td>
      <td>${escapeHtml(line.unitSize)}</td>
      <td class="purchase-table__amount">${escapeHtml(line.quantity)}</td>
      <td class="purchase-table__amount">${escapeHtml(money(line.price))}</td>
      <td>${offerBadge({ badge: { percent: line.discount, source: line.discountSource } }, "offer-badge--inline")}</td>
      <td class="purchase-table__amount">${line.timesBefore > 0 ? escapeHtml(t("orders.before", { count: line.timesBefore })) : ""}</td>
      <td class="purchase-table__amount">${escapeHtml(money(line.amount))}</td>
    </tr>`;
}

// De tabel: categorie, product, inhoud, aantal, prijs, korting, eerder, bedrag. Een klik op een kop met een sorteersleutel sorteert
// (zoals de aankopentabel). De knoppen heten order-table__sort, zodat de aankopenlijst ze niet als de hare ziet.
function orderTable(lines, sort = orderView.sort) {
  const head = (key, label, className = "") => {
    const cls = className ? ` class="${className}"` : "";
    if (!key) return `<th${cls}>${escapeHtml(t(label))}</th>`;
    const aria = sort.key === key ? ` aria-sort="${sort.dir === "asc" ? "ascending" : "descending"}"` : "";
    return `<th${cls}${aria}><button class="order-table__sort" type="button" data-sort="${key}">${escapeHtml(t(label))}</button></th>`;
  };
  const amount = "purchase-table__amount";
  const header = [
    head("category", "orders.col.category"),
    head("name", "orders.col.product"),
    head("", "orders.col.unit"),
    head("", "orders.col.quantity", amount),
    head("", "orders.col.price", amount),
    head("discount", "orders.col.discount"),
    head("", "orders.col.before", amount),
    head("amount", "orders.col.amount", amount),
  ].join("");
  return `<div class="purchase-table-wrap order-table-wrap"><table class="purchase-table order-table"><thead><tr>${header}</tr></thead><tbody>${lines.map(orderTableRow).join("")}</tbody></table></div>`;
}

// De weergaven, sorteersleutels en kaartgroottes van de balk, met de labels in de taal van nu. Bon alleen bij een kassabon.
function orderBarSpec(kind) {
  const views = [
    { id: "category", icon: MDI_VIEW_CATEGORY, label: t("orders.view.category") },
    { id: "cards", icon: MDI_VIEW_CARDS, label: t("orders.view.cards") },
    { id: "table", icon: MDI_VIEW_TABLE, label: t("orders.view.table") },
  ];
  if (kind === "receipt") views.push({ id: "paper", icon: MDI_RECEIPT, label: t("orders.view.paper") });
  return {
    views,
    sorts: [
      { key: "category", label: t("orders.sort.category"), dir: "asc" },
      { key: "name", label: t("orders.sort.name"), dir: "asc" },
      { key: "amount", label: t("orders.sort.amount"), dir: "desc" },
      { key: "discount", label: t("orders.sort.discount"), dir: "desc" },
    ],
    sizes: true,
    options: [],
  };
}

// Een klik op een tabelkop: dezelfde sleutel wisselt de richting, een andere begint in zijn eigen richting (categorie en naam
// oplopend, bedrag en korting aflopend), net als in het sorteermenu.
function orderSortBy(key) {
  orderView.sortBy(key, null, viewSortStart(orderBarSpec(orderPage.kind), key));
}

// De statusregel links in de balk: hoeveel producten en categorieën, of hoeveel er na zoeken en filteren te zien zijn.
function orderStatusText(shown, total, categories) {
  if (shown !== total) return t("orders.page.shown", { shown: I18N.formatNumber(shown), total: I18N.formatNumber(total) });
  return [tn("orders.page.products", total), tn("orders.page.categories", categories)].join(" · ");
}

// De chips: een per categorie in deze aankoop, met het aantal regels dat door het zoeken komt. Een categorie zonder treffers valt
// weg, behalve de gekozen, zodat je die weer uit kunt zetten.
function orderChips(lines, searched, active) {
  const present = new Set(lines.map((line) => line.category));
  const categories = CATEGORY_ORDER.filter((id) => present.has(id))
    .map((id) => ({ id, count: searched.filter((line) => line.category === id).length }))
    .filter((category) => category.count || category.id === active);
  return categoryChips({ categories, active, allLabel: t("offers.all", { count: I18N.formatNumber(searched.length) }) });
}

// Tekent de gekozen weergave uit de aankoop die er staat: de chips, de statusregel en de producten. Bon tekent het bonnetje met de
// gevonden regels opgelicht (receiptPaper in orders.js) en heeft geen chips. De andere weergaven laten alleen zien wat door het
// zoeken en de chip komt.
function renderOrderItems() {
  const { purchase, kind, lines, words, category } = orderPage;
  if (!purchase) return;
  const view = orderViewFor(orderScope(kind));
  const paper = view === "paper";
  const box = $("#ah-order-items");
  const searched = orderFilter(lines, words, "all");
  const visible = orderSortLines(paper ? searched : orderFilter(lines, words, category));
  const chips = $("#order-categories");
  chips.innerHTML = orderChips(lines, searched, category);
  chips.hidden = paper || !lines.length;
  const status = document.getElementById(viewStatusId("order"));
  if (status) status.textContent = lines.length ? orderStatusText(visible.length, lines.length, new Set(lines.map((line) => line.category)).size) : "";
  const summary = kind === "receipt" ? receiptBonusSummary(purchase) : "";
  if (!lines.length) box.innerHTML = `<p class="muted">${escapeHtml(t("orders.items_missing"))}</p>`;
  else if (paper) box.innerHTML = receiptPaper(purchase, words);
  else if (!visible.length) box.innerHTML = `<p class="muted">${escapeHtml(t("orders.search_none", { query: orderPage.query.trim() || categoryLabel(category) }))}</p>`;
  else if (view === "cards") box.innerHTML = `<div class="product-grid product-grid--${escapeHtml(orderView.options.size)}">${visible.map((line) => productCard(line)).join("")}</div>${summary}`;
  else if (view === "table") box.innerHTML = orderTable(visible, orderView.sort) + summary;
  else {
    const groups = orderGroups(visible);
    if (orderView.sort.key === "category" && orderView.sort.dir === "desc") groups.reverse();
    box.innerHTML = `<div class="cat-sections">${groups.map(orderCategorySection).join("")}</div>${summary}`;
  }
  syncViewBar("order", orderView, orderBarSpec(kind));
  window.requestAnimationFrame(fitReceiptBox);
}

// Een geopende aankoop tekenen: de status- en bespaard-badge in de kop, de balk, het zoekveld en de chips, en de weergave. Een
// nieuwe aankoop begint met de zoekterm van de aankopenlijst (de gevonden regels staan dan meteen apart) en zonder chip.
function renderOrderPage(purchase, kind, store) {
  const key = `${kind}:${store}:${purchase.id ?? purchase.ref ?? ""}`;
  if (key !== orderPage.key) {
    orderPage.query = ordersState.query || "";
    orderPage.category = "all";
    $("#order-search").value = orderPage.query;
  }
  Object.assign(orderPage, { purchase, kind, store, key, lines: orderLines(purchase, kind, store), words: orderWords(orderPage.query) });
  orderView.useScope(orderScope(kind));
  const badges = [purchaseStatusBadge({ kind, statusKind: purchase.statusKind || (kind === "receipt" ? "receipt" : "delivered"), statusText: purchase.statusText })];
  if (purchase.saved > 0) {
    const amount = money(purchase.saved);
    const text = purchase.savedPercent ? t("orders.saved_percent", { amount, percent: I18N.formatNumber(purchase.savedPercent) }) : t("orders.group.saved", { amount });
    badges.push(badge("saved", text));
  }
  $("#ah-order-badges").innerHTML = badges.join(" ");
  $("#ah-order-badges").hidden = false;
  const bar = viewBarElement("order");
  renderViewBar("order", orderView, orderBarSpec(kind));
  bar.hidden = !orderPage.lines.length;
  $("#order-filter").hidden = !orderPage.lines.length;
  renderOrderItems();
}

// Terug naar de lijst (of een andere aankoop die laadt): niets van de vorige aankoop blijft staan.
function orderPageClose() {
  Object.assign(orderPage, { purchase: null, kind: null, key: "", lines: [], query: "", words: [], category: "all" });
  $("#ah-order-badges").innerHTML = "";
  $("#ah-order-badges").hidden = true;
  viewBarElement("order").hidden = true;
  $("#order-filter").hidden = true;
  $("#order-categories").innerHTML = "";
}

function orderWords(query) {
  return foldText(query).split(/\s+/).filter(Boolean);
}

orderViewLoad();
// Een andere weergave, sortering of kaartgrootte begint bovenaan het vak (dat op een breed scherm zelf scrolt).
bindViewBar("order", orderView, () => {
  if (!orderPage.purchase) return;
  renderOrderItems();
  $("#ah-order-items").scrollTop = 0;
});
$("#order-search").addEventListener("input", (event) => {
  orderPage.query = event.target.value;
  orderPage.words = orderWords(orderPage.query);
  renderOrderItems();
});
// Een chip kiest die categorie. Nog een keer op de gekozen chip en je ziet weer alles.
$("#order-categories").addEventListener("click", (event) => {
  const chip = event.target.closest("[data-cat]");
  if (!chip) return;
  orderPage.category = chip.dataset.cat === orderPage.category ? "all" : chip.dataset.cat;
  renderOrderItems();
});
$("#ah-order-items").addEventListener("click", (event) => {
  const sort = event.target.closest(".order-table__sort");
  if (sort) orderSortBy(sort.dataset.sort);
});
// Een andere taal: de balk, de badges en de weergave opnieuw, met de labels in die taal.
window.addEventListener("ms:languagechange", () => {
  if (orderPage.purchase) renderOrderPage(orderPage.purchase, orderPage.kind, orderPage.store);
});
