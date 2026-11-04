// Het zijmenu op een groot scherm als boom: onder elk onderdeel staan zijn subtabs (Winkels > Recepten, Lijstjes,
// Bestellingen), zodat je meteen op Bestellingen klikt in plaats van eerst op Winkels. De subtabbalk boven de inhoud
// verdwijnt dan, alleen een keuzemenu erin (de winkel bij Winkels en Aanbiedingen) blijft staan. De balk komt terug als het
// menu is ingeklapt tot pictogrammen, op een scherm onder 1024px, of als de boom niet in de hoogte van het scherm past
// (html.nav-tree valt dan weg, zie styles.css).
//
// De boom is een kopie van de subtabbalken: dezelfde links (router.js opent ze), dezelfde teksten en dezelfde zichtbaarheid.
// Een MutationObserver op de balken en op het menu houdt hem gelijk, dus de winkelkeuze, een taalwissel en een wissel van
// scherm hoeven hier niets van te weten.

const NAV_TREE_TOP = 88; // de bovenkant van het vastgeplakte menu, als in `.shell > .tabs` in styles.css
const NAV_TREE_BOTTOM = 24; // ruimte onder het menu
const NAV_TREE_WIDE = "(min-width: 1024px)";

const navTree = { observers: [], frame: 0 };

function navTreeLinks(panel) {
  return [...panel.querySelectorAll(":scope > .subtabs a.subtab[data-subtab]")].filter((link) => !link.hidden);
}

function navTreeItem(link, current) {
  const text = escapeHtml(link.textContent.trim());
  const href = escapeHtml(link.getAttribute("href") || "");
  return `<a class="tab-tree__item${current ? " is-current" : ""}" href="${href}" data-route${current ? ' aria-current="page"' : ""}>${text}</a>`;
}

// Past de boom onder het menu? Zo niet, dan de subtabbalken weer, want een menu dat van het scherm loopt helpt niemand.
function navTreeFit() {
  const root = document.documentElement;
  if (!window.matchMedia(NAV_TREE_WIDE).matches) {
    root.classList.remove("nav-tree");
    return;
  }
  const collapsed = root.classList.contains("nav-collapsed");
  root.classList.add("nav-tree");
  if (collapsed) return; // ingeklapt is de boom toch niet te zien, meten gebeurt pas bij het uitklappen
  const tabs = document.getElementById("main-tabs");
  const fits = NAV_TREE_TOP + tabs.scrollHeight + NAV_TREE_BOTTOM <= window.innerHeight;
  root.classList.toggle("nav-tree", fits);
}

function navTreeRender() {
  navTree.frame = 0;
  document.querySelectorAll("#main-tabs .tab[data-tab]").forEach((tab) => {
    const panel = document.getElementById(tab.dataset.tab);
    const bar = panel ? panel.querySelector(":scope > .subtabs") : null;
    const links = bar ? navTreeLinks(panel) : [];
    const next = tab.nextElementSibling;
    let tree = next && next.classList.contains("tab-tree") ? next : null;
    if (!links.length) {
      if (tree) tree.remove();
      if (bar) bar.classList.remove("subtabs--tree", "subtabs--empty");
      return;
    }
    if (!tree) {
      tree = document.createElement("div");
      tree.className = "tab-tree";
      tab.after(tree);
    }
    const active = tab.classList.contains("is-active");
    tree.innerHTML = links.map((link) => navTreeItem(link, active && link.classList.contains("is-active"))).join("");
    bar.classList.add("subtabs--tree");
    bar.classList.toggle("subtabs--empty", !bar.querySelector(".subtab-menu:not([hidden])"));
  });
  navTreeFit();
  navTree.observers.forEach((observer) => observer.takeRecords()); // wat het tekenen zelf veranderde, is geen reden om opnieuw te tekenen
}

function navTreeSchedule() {
  if (navTree.frame) return;
  navTree.frame = window.requestAnimationFrame(navTreeRender);
}

function navTreeStart() {
  const tabs = document.getElementById("main-tabs");
  if (!tabs) return;
  const watch = (target, options) => {
    const observer = new MutationObserver(navTreeSchedule);
    observer.observe(target, options);
    navTree.observers.push(observer);
  };
  // Welk onderdeel open staat, en of het menu is ingeklapt.
  watch(tabs, { attributes: true, attributeFilter: ["class"], subtree: true });
  watch(document.documentElement, { attributes: true, attributeFilter: ["class"] });
  // De balken: zichtbaarheid (de winkelkeuze), het actieve subtab, de adressen (router.js) en de teksten (de taal).
  document.querySelectorAll("#main-tabs .tab[data-tab]").forEach((tab) => {
    const bar = document.querySelector(`#${tab.dataset.tab} > .subtabs`);
    if (bar) watch(bar, { attributes: true, attributeFilter: ["class", "hidden", "href"], childList: true, characterData: true, subtree: true });
  });
  window.addEventListener("resize", navTreeSchedule);
  window.addEventListener("ms:languagechange", navTreeSchedule);
  navTreeRender();
}

navTreeStart();
