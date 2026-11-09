// Hovervensters op de aankopenlijst: bij een productfoto het product groot, bij een bestelling of bon de inhoud per categorie.
// Alleen met een muis (popoverAllowed), na 400 ms stilstaan, weg zodra de muis het element en het venster verlaat of bij Escape,
// altijd binnen het scherm. De gegevens komen uit de detailroute van de aankoop, één keer per aankoop (purchaseDetailCache, gewist
// door loadOrders). Geen van die routes doet een verzoek aan AH. Laadt na order_views.js: orderLines en orderGroups doen het werk.
//
// popoverAllowed, popoverPlace, popoverProduct en popoverPurchase zijn puur (geen DOM), zodat de tests ze in Node draaien.

// Hoe lang stilstaan voordat het venster komt, en hoe lang de muis over de ruimte tussen element en venster mag doen (ms). De
// ruimte ertussen en de afstand tot de rand van het scherm (px). Hooguit zes fotootjes per categorie, daarna "+N", en bij meer dan
// veertien categorieën kleinere fotootjes, zodat het venster op het scherm past.
const POPOVER_DELAY = 400;
const POPOVER_GRACE = 150;
const POPOVER_GAP = 8;
const POPOVER_EDGE = 16;
const POPOVER_THUMBS = 6;
const POPOVER_SMALL_ABOVE = 14;

// De details per aankoop (winkel:soort:id), als belofte: twee hovers op dezelfde aankoop delen één verzoek.
const purchaseDetailCache = new Map();

// Alleen een apparaat dat kan aanwijzen met een fijne aanwijzer (een muis, een trackpad). Op een telefoon of tablet niets: een
// tik opent de aankoop zoals altijd. De standaard is een pijlfunctie: window.matchMedia los aanroepen geeft "Illegal invocation".
function popoverAllowed(mq = (query) => window.matchMedia(query)) {
  return Boolean(mq("(hover: hover) and (pointer: fine)").matches);
}

// De plek van het venster (fixed, in pixels van het scherm): onder het element, en als dat niet past erboven. Past het rechts
// niet, dan schuift het naar links. Past het boven noch onder, dan zo dicht mogelijk onder het element binnen het scherm (het is
// nooit hoger dan het scherm min 32 px, zie de CSS).
function popoverPlace(anchorRect, size, viewport) {
  const maxLeft = viewport.width - POPOVER_EDGE - size.width;
  const maxTop = viewport.height - POPOVER_EDGE - size.height;
  const left = Math.max(POPOVER_EDGE, Math.min(anchorRect.left, maxLeft));
  const below = anchorRect.bottom + POPOVER_GAP;
  const above = anchorRect.top - POPOVER_GAP - size.height;
  let top = Math.max(POPOVER_EDGE, Math.min(below, maxTop));
  if (below <= maxTop) top = below;
  else if (above >= POPOVER_EDGE) top = above;
  return { left: Math.round(left), top: Math.round(top) };
}

// De plek van de popup bij een aanbieding (offers.js): rechts van de kaart als daar minstens POPOVER_SIDE_MIN px over is, anders
// links, en de popup wordt zo breed als de ruimte daar toelaat (hooguit size.width). Past hij aan geen van beide kanten, dan
// eronder, anders erboven, op volle breedte. Naast de kaart op de hoogte van de kaart, altijd binnen de randen van het scherm.
const POPOVER_SIDE_MIN = 420;

function offerPopupPlace(anchorRect, size, viewport) {
  const maxTop = viewport.height - POPOVER_EDGE - size.height;
  const clampTop = (value) => Math.round(Math.max(POPOVER_EDGE, Math.min(value, maxTop)));
  const roomRight = viewport.width - POPOVER_EDGE - anchorRect.right - POPOVER_GAP;
  const roomLeft = anchorRect.left - POPOVER_GAP - POPOVER_EDGE;
  if (roomRight >= POPOVER_SIDE_MIN || roomLeft >= POPOVER_SIDE_MIN) {
    const right = roomRight >= POPOVER_SIDE_MIN; // rechts gaat voor: daar kijkt de lezer heen
    const width = Math.round(Math.min(size.width, right ? roomRight : roomLeft));
    const left = right ? anchorRect.right + POPOVER_GAP : anchorRect.left - POPOVER_GAP - width;
    return { left: Math.round(left), top: clampTop(anchorRect.top), width };
  }
  const maxLeft = viewport.width - POPOVER_EDGE - size.width;
  const across = Math.round(Math.max(POPOVER_EDGE, Math.min(anchorRect.left, maxLeft)));
  const below = anchorRect.bottom + POPOVER_GAP;
  const above = anchorRect.top - POPOVER_GAP - size.height;
  if (below <= maxTop) return { left: across, top: Math.round(below), width: size.width };
  if (above >= POPOVER_EDGE) return { left: across, top: Math.round(above), width: size.width };
  return { left: across, top: clampTop(below), width: size.width };
}

// De detailroute van een aankoop: een bestelling bij de winkel van de lijst (AH of Jumbo), een kassabon bij AH (alleen lezen,
// het opzoeken van producten is een aparte POST), een ingelezen bestelling bij zijn winkel.
function purchaseDetailUrl(kind, id, store) {
  const ref = encodeURIComponent(id);
  if (kind === "receipt") return `/api/ah/receipts/${ref}`;
  if (kind === "imported") return `/api/imported-orders/${encodeURIComponent(store)}/${ref}`;
  return `/api/${encodeURIComponent(store)}/orders/${ref}`;
}

// De sleutel van een aankoop in purchaseDetailCache (purchaseDetail en popoverForget).
function purchaseDetailKey(kind, id, store) {
  return `${store}:${kind}:${id}`;
}

// Eén aankoop vergeten, zodat de volgende hover zijn details opnieuw ophaalt. refreshReceipt (orders.js) doet dat met een bon na
// een ronde koppelen. loadOrders wist de hele cache.
function popoverForget(key) {
  purchaseDetailCache.delete(key);
}

// De details van een aankoop, één keer per aankoop per lading van de lijst. Een verzoek dat mislukt blijft niet bewaard, zodat
// de volgende hover het opnieuw probeert.
function purchaseDetail(kind, id, store) {
  const key = purchaseDetailKey(kind, id, store);
  if (!purchaseDetailCache.has(key)) {
    const request = jsonFetch(purchaseDetailUrl(kind, id, store));
    purchaseDetailCache.set(key, request);
    request.catch(() => {
      if (purchaseDetailCache.get(key) === request) purchaseDetailCache.delete(key);
    });
  }
  return purchaseDetailCache.get(key);
}

// Het productvenster: de foto op 180 px, naam, merk en inhoud, de categoriechip, "2x € 1,19 = € 2,38" met de kortingsbadge, en
// hoe vaak je het eerder kocht (niets bij nul). De prijs en dag van die eerdere aankoop staan er niet: dat kost een verzoek per
// product, en de prijs van deze aankoop staat al in de regel erboven. De linkerrand in de kleur van de categorie.
function popoverProduct(line) {
  const meta = [line.brand, line.unitSize].filter(Boolean).join(" · ");
  // Wat je per stuk betaalde, en dat keer het aantal: op een bon is het bedrag van de regel de prijs vóór de bonus.
  const each = line.paidEach ?? line.price;
  const paid = each !== null && line.quantity > 0 ? Math.round(each * line.quantity * 100) / 100 : null;
  const amount = paid !== null ? t("popover.amount", { quantity: line.quantity, price: money(each), amount: money(paid) }) : money(line.amount);
  const discount = offerBadge({ badge: { percent: line.discount, source: line.discountSource } }, "offer-badge--inline");
  const before = line.timesBefore > 0 ? t("popover.before", { count: line.timesBefore }) : "";
  const photo = line.image ? `<img class="popover__photo" src="${escapeHtml(storeImage(line.image))}" alt="" data-hide-on-error>` : "";
  return `<div class="popover--product cat--${escapeHtml(line.category)}">
      ${photo}
      <div class="popover__info">
        <strong class="popover__title">${escapeHtml(line.title)}</strong>
        ${meta ? `<small class="popover__meta">${escapeHtml(meta)}</small>` : ""}
        ${categoryChip({ id: line.category, label: categoryLabel(line.category) }, { tag: "span" })}
        <span class="popover__amount">${escapeHtml(amount)}${discount ? ` ${discount}` : ""}</span>
        ${before ? `<small class="popover__history">${escapeHtml(before)}</small>` : ""}
      </div>
    </div>`;
}

// Hooguit zes fotootjes van een categorie, plus "+N" voor de regels die er niet bij staan (ook die zonder foto).
function popoverThumbs(lines, small) {
  const shown = lines.filter((line) => line.image).slice(0, POPOVER_THUMBS);
  const rest = lines.length - shown.length;
  const photos = shown.map((line) => `<img src="${escapeHtml(storeImage(line.image))}" alt="" loading="lazy" data-hide-on-error>`).join("");
  const more = rest > 0 ? `<span class="popover__more">${escapeHtml(t("popover.more", { count: rest }))}</span>` : "";
  return `<span class="popover__thumbs${small ? " popover__thumbs--small" : ""}">${photos}${more}</span>`;
}

// Een regel per categorie: bolletje, naam, "13 st · 8 srt", de fotootjes en het subtotaal.
function popoverGroup(group, small) {
  return `<li class="popover__cat cat--${escapeHtml(group.id)}">
      <i class="offer-chip__dot" aria-hidden="true"></i>
      <span class="popover__cat-name">${escapeHtml(categoryLabel(group.id))}</span>
      <span class="popover__cat-count">${escapeHtml(t("popover.group", { units: tn("popover.units", group.units), kinds: tn("popover.kinds", group.kinds) }))}</span>
      ${popoverThumbs(group.lines, small)}
      <span class="popover__cat-amount">${escapeHtml(money(group.amount))}</span>
    </li>`;
}

// Het venster van een bestelling of bon: de kop (de dag en de statusbadge), het nummer met hoeveel producten en categorieën, een
// regel per categorie (orderGroups, de volgorde van de aanbiedingen), onderaan het bespaarde bedrag en het totaal. `row` is een
// rij van purchaseRows, `detail` het antwoord van de detailroute.
function popoverPurchase(row, detail) {
  const lines = orderLines(detail, row.kind, row.store || "ah");
  const groups = orderGroups(lines);
  const small = groups.length > POPOVER_SMALL_ABOVE;
  const summary = lines.length
    ? [row.kind === "order" ? t("orders.order_title", { id: row.id }) : "", tn("orders.page.products", lines.length), tn("orders.page.categories", groups.length)]
    : [t("orders.items_pending")];
  const saved = Number(detail.saved) > 0
    ? badge("saved", typeof detail.savedPercent === "number"
      ? t("orders.saved_percent", { amount: money(detail.saved), percent: detail.savedPercent })
      : t("orders.group.saved", { amount: money(detail.saved) }))
    : "";
  const total = detail.total ?? row.total;
  const foot = total !== null && total !== undefined ? `<strong class="popover__total">${escapeHtml(t("popover.total", { amount: money(total) }))}</strong>` : "";
  return `<div class="popover--purchase">
      <header class="popover__head"><strong>${escapeHtml(row.label)}</strong>${purchaseStatusBadge(row)}</header>
      <p class="popover__summary">${escapeHtml(summary.filter(Boolean).join(" · "))}</p>
      ${groups.length ? `<ul class="popover__cats">${groups.map((group) => popoverGroup(group, small)).join("")}</ul>` : ""}
      ${saved || foot ? `<footer class="popover__foot">${saved}${foot}</footer>` : ""}
    </div>`;
}

// De link van het element onder een venster: een foto ligt binnen de link (kaart, overzicht), een rij of kaart heeft hem erin.
function popoverAnchorLink(target) {
  return target.closest("a[data-nav]") || target.querySelector("a[data-nav]") || null;
}

// Een hovervenster voor alles in `root` dat op `selector` past, met één mouseover- en één mouseout-handler (gedelegeerd, dus ook
// voor wat later getekend wordt). `load(target)` geeft de HTML van het venster, of null voor geen venster. Het venster is een
// <div class="popover" role="tooltip"> onder <body>, vast op het scherm. Op een knop in het element komt het niet (de prullenbak
// van een ingelezen bestelling); een link wel, en een klik daarop werkt gewoon en sluit het venster.
function hoverPopover(root, selector, load) {
  const popover = document.createElement("div");
  popover.className = "popover";
  popover.setAttribute("role", "tooltip");
  popover.hidden = true;
  document.body.append(popover);
  let anchor = null; // het element onder de muis
  let shown = null; // het element waarvan het venster te zien is
  let dismissed = null; // weggeklikt met Escape: niet opnieuw tot de muis eraf is geweest
  let timer = 0;
  let graceTimer = 0;
  let ticket = 0; // een antwoord dat binnenkomt nadat de muis verder is, wordt niet getoond

  const hide = () => {
    window.clearTimeout(timer);
    window.clearTimeout(graceTimer);
    ticket += 1;
    anchor = null;
    shown = null;
    popover.hidden = true;
    popover.innerHTML = "";
  };
  const leave = () => {
    window.clearTimeout(timer);
    window.clearTimeout(graceTimer);
    if (shown) graceTimer = window.setTimeout(hide, POPOVER_GRACE);
    else hide();
  };
  const show = async (target) => {
    const mine = ++ticket;
    let html;
    try {
      html = await load(target);
    } catch (error) {
      html = `<p class="popover__error">${escapeHtml(t("popover.failed", { error: error.message }))}</p>`;
    }
    // Intussen weggegaan, opnieuw getekend of weggeschoven: geen venster (:hover zegt of de muis er nu echt op staat).
    if (mine !== ticket || anchor !== target || !target.isConnected || !target.matches(":hover")) return;
    if (!html) {
      hide();
      return;
    }
    popover.innerHTML = html;
    popover.style.left = "0px";
    popover.style.top = "0px";
    popover.hidden = false;
    const size = { width: popover.offsetWidth, height: popover.offsetHeight };
    const viewport = { width: document.documentElement.clientWidth, height: window.innerHeight };
    const { left, top } = popoverPlace(target.getBoundingClientRect(), size, viewport);
    popover.style.left = `${left}px`;
    popover.style.top = `${top}px`;
    shown = target;
  };

  root.addEventListener("mouseover", (event) => {
    if (!popoverAllowed()) return;
    const target = event.target.closest(selector);
    if (!target || !root.contains(target) || event.target.closest("button")) {
      if (anchor) leave();
      return;
    }
    if (target === dismissed) return;
    if (target === anchor) {
      window.clearTimeout(graceTimer);
      return;
    }
    // Een ander element: het oude venster weg, en na 400 ms stilstaan het nieuwe.
    hide();
    anchor = target;
    timer = window.setTimeout(() => show(target), POPOVER_DELAY);
  });
  root.addEventListener("mouseout", (event) => {
    const target = event.target.closest(selector);
    if (!target) return;
    const to = event.relatedTarget;
    if (to && (target.contains(to) || popover.contains(to))) return;
    if (target === dismissed) dismissed = null;
    if (target === anchor) leave();
  });
  popover.addEventListener("mouseenter", () => window.clearTimeout(graceTimer));
  // Het venster ligt soms over het element zelf (past het boven noch onder, dan schuift het eroverheen). Een klik erop is dan een
  // klik op de link van het element: dezelfde weg door de router, en met een toets erbij een nieuw tabblad. Een link of knop in
  // het venster doet zijn eigen werk.
  popover.addEventListener("click", (event) => {
    if (event.target.closest("a, button")) return;
    const link = shown ? popoverAnchorLink(shown) : null;
    hide();
    if (!link) return;
    if (routerModified(event)) window.open(link.href, "_blank", "noopener");
    else link.click();
  });
  popover.addEventListener("mouseleave", (event) => {
    const to = event.relatedTarget;
    if (anchor && to && anchor.contains(to)) return;
    leave();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || !anchor) return;
    dismissed = anchor;
    hide();
  });
  // Een klik buiten het venster (een link in de lijst, ook met Cmd/Ctrl: die stopt de router in de capture-fase op document, maar
  // deze handler staat er eerder, want popover.js laadt voor router.js) en een wissel van adres (Terug, Alt+Links) sluiten het.
  document.addEventListener("click", (event) => { if (!popover.contains(event.target)) hide(); }, { capture: true });
  window.addEventListener("popstate", hide);
  // Het venster staat vast op het scherm: schuift de pagina, dan staat het element ergens anders. Dan gaat het venster weg en
  // begint het wachten opnieuw: staat de muis na het schuiven nog 400 ms op hetzelfde element, dan komt het terug op de nieuwe plek
  // (komt de muis op een ander element, dan doet mouseover de rest). Schuiven in het venster zelf mag.
  window.addEventListener("scroll", (event) => {
    if (!anchor || popover.contains(event.target)) return;
    const target = anchor;
    hide();
    anchor = target;
    timer = window.setTimeout(() => show(target), POPOVER_DELAY);
  }, { capture: true, passive: true });
}

// De aankoop bij een element in de lijst (de rij, de kaart, of een foto erin), als rij van purchaseRows.
function popoverPurchaseRow(target) {
  const host = target.closest("[data-purchase-kind]");
  if (!host) return null;
  const { purchaseKind: kind, purchaseId: id } = host.dataset;
  return purchaseRows().find((row) => row.kind === kind && row.id === id) || null;
}

// Wat het venster op de aankopenlijst toont: bij een foto het product (de regel met die foto), anders de aankoop.
async function purchasePopoverLoad(target) {
  const row = popoverPurchaseRow(target);
  if (!row) return null;
  const detail = await purchaseDetail(row.kind, row.id, row.store);
  if (target.dataset.popover !== "product") return popoverPurchase(row, detail);
  const line = orderLines(detail, row.kind, row.store).find((entry) => entry.image === target.dataset.image);
  return line ? popoverProduct(line) : null;
}

// Eén handler voor de hele lijst: de tabel, de kaarten en het overzicht staan in #ah-orders-view.
const purchaseList = document.getElementById("ah-orders-view");
if (purchaseList) hoverPopover(purchaseList, "[data-popover]", purchasePopoverLoad);
