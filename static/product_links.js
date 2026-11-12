// ---- Je eigen koppelingen (#238, zie product_links.py): een regel op een kassabon of in een ingelezen bestelling die de app niet
// (of verkeerd) aan een product van de winkel koppelde, koppel je hier zelf. Daarna hoort elke regel met hetzelfde artikelnummer of
// dezelfde naam bij dat product: op elke andere en elke volgende bon, in de ingelezen bestellingen en in de Prijsgeschiedenis.
//
// productLinkButton(line) geeft de knop bij een regel: Koppelen als de regel nog bij geen product hoort, anders Ander product
// kiezen. Een klik opent het venster #link-dialog: een zoekveld met de naam van de regel, de producten van die winkel om uit te
// kiezen, en Ontkoppelen als je deze regel eerder zelf koppelde. Na koppelen of ontkoppelen gaat ms:product-links uit, met de
// winkel erbij; wie een bon, bestelling of de Prijsgeschiedenis toont, tekent die dan opnieuw.
const productLinks = { line: null, results: [], run: 0 };

function productLinkStoreName(store) {
  return (typeof STORES !== "undefined" && STORES[store]?.name) || store;
}

// `line`: { store, name, article, product, productId, link }. `name` is de naam op de bon of in de mail, `article` het
// artikelnummer op een AH-bon (of null), `product` en `productId` de titel en het id van het product waar de regel nu bij hoort (of
// null) en `link` je eigen koppeling ({ kind, key }, of null). Zonder winkel of naam is er niets te koppelen.
function productLinkButton(line) {
  if (!line?.store || !String(line.name || "").trim()) return "";
  const linked = Boolean(line.product);
  const label = t(linked ? "links.change_line" : "links.link_line", { name: line.name });
  const data = [
    ["data-store", line.store],
    ["data-name", line.name],
    ["data-article", line.article ?? ""],
    ["data-product", line.product || ""],
    ["data-product-id", line.productId ?? ""],
    ["data-kind", line.link?.kind || ""],
    ["data-key", line.link?.key || ""],
    ["data-shared", line.shared ? "1" : ""],
  ]
    .map(([name, value]) => `${name}="${escapeHtml(value)}"`)
    .join(" ");
  return `<button type="button" class="icon-button product-link${linked ? "" : " product-link--todo"}" data-product-link ${data} aria-label="${escapeHtml(label)}" title="${escapeHtml(t(linked ? "links.change" : "links.link"))}">${mdiIcon(MDI_LINK)}</button>`;
}

// Een product uit de zoeker: foto, naam, en merk, inhoud en prijs eronder. Het product waar de regel nu bij hoort, valt op.
function productLinkItem(product, index, currentId) {
  const meta = [product.brand, product.unitSize, product.price ? formatPrice(product.price) : ""].filter(Boolean).join(" · ");
  const self = Boolean(currentId) && String(product.productId) === String(currentId);
  return `
    <li>
      <button type="button" class="link-item${self ? " is-self" : ""}" data-link-pick="${index}"${self ? ' aria-current="true"' : ""}>
        ${product.image ? `<img src="${escapeHtml(storeImage(product.image))}" alt="" loading="lazy" data-hide-on-error>` : `<span class="link-item__photo" aria-hidden="true">${mdiIcon(MDI_IMAGE_OUTLINE)}</span>`}
        <span class="link-item__body">
          <strong>${escapeHtml(product.title)}</strong>
          ${meta ? `<small>${escapeHtml(meta)}</small>` : ""}
        </span>
      </button>
    </li>`;
}

function renderProductLinkResults(query) {
  const line = productLinks.line;
  const results = productLinks.results;
  $("#link-dialog-list").innerHTML = results.map((product, index) => productLinkItem(product, index, line?.productId)).join("");
  message("#link-dialog-message", results.length ? "" : t("links.none", { query }), "success");
}

async function searchProductLink() {
  const line = productLinks.line;
  const query = $("#link-dialog-query").value.trim();
  if (!line || !query) return;
  const run = ++productLinks.run;
  productLinks.results = [];
  $("#link-dialog-list").innerHTML = "";
  message("#link-dialog-message", t("links.searching"), "info");
  try {
    const data = await jsonFetch(`/api/product-links/search?${new URLSearchParams({ store: line.store, q: query })}`);
    if (run !== productLinks.run) return; // een nieuwere zoekopdracht is al onderweg
    productLinks.results = data.products || [];
    renderProductLinkResults(query);
  } catch (error) {
    if (run !== productLinks.run) return;
    message("#link-dialog-message", error.message);
  }
}

function productLinksChanged(store) {
  window.dispatchEvent(new CustomEvent("ms:product-links", { detail: { store } }));
}

function setProductLinkBusy(busy) {
  for (const button of document.querySelectorAll("#link-dialog button, #link-dialog input")) button.disabled = busy;
}

async function pickProductLink(index) {
  const line = productLinks.line;
  const product = productLinks.results[index];
  if (!line || !product) return;
  setProductLinkBusy(true);
  message("#link-dialog-message", t("links.saving"), "success");
  try {
    await jsonFetch("/api/product-links", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ store: line.store, name: line.name, article: line.article || null, product }),
    });
    $("#link-dialog").close();
    productLinksChanged(line.store);
  } catch (error) {
    message("#link-dialog-message", error.message);
  } finally {
    setProductLinkBusy(false);
  }
}

async function removeProductLink() {
  const line = productLinks.line;
  if (!line?.link) return;
  setProductLinkBusy(true);
  try {
    await jsonFetch(`/api/product-links/${encodeURIComponent(line.store)}/${encodeURIComponent(line.link.kind)}/${encodeURIComponent(line.link.key)}`, {
      method: "DELETE",
    });
    $("#link-dialog").close();
    productLinksChanged(line.store);
  } catch (error) {
    message("#link-dialog-message", error.message);
  } finally {
    setProductLinkBusy(false);
  }
}

// Klopt niet: de koppeling uit het gedeelde bestand (shared_links.py) geldt niet meer voor deze installatie. Die naam hoort daarna
// weer bij de automatische koppeling, of bij een product dat je zelf kiest.
async function rejectSharedLink() {
  const line = productLinks.line;
  if (!line?.shared || !line.productId) return;
  setProductLinkBusy(true);
  try {
    await jsonFetch("/api/shared-links/reject", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ store: line.store, name: line.name, productId: line.productId }),
    });
    $("#link-dialog").close();
    productLinksChanged(line.store);
  } catch (error) {
    message("#link-dialog-message", error.message);
  } finally {
    setProductLinkBusy(false);
  }
}

// Opent het venster voor de regel achter een knop van productLinkButton, en zoekt meteen op de naam van de regel.
function openProductLink(button) {
  const data = button.dataset;
  const line = {
    store: data.store,
    name: data.name,
    article: data.article || null,
    product: data.product || null,
    productId: data.productId || null,
    link: data.kind && data.key ? { kind: data.kind, key: data.key } : null,
    shared: data.shared === "1" && !(data.kind && data.key),
  };
  productLinks.line = line;
  productLinks.results = [];
  $("#link-dialog-title").textContent = t(line.product ? "links.change" : "links.link");
  $("#link-dialog-intro").textContent = t("links.intro", { name: line.name, store: productLinkStoreName(line.store) });
  $("#link-dialog-current").textContent = line.product ? t(line.link ? "links.current_own" : "links.current", { title: line.product }) : "";
  $("#link-dialog-current").hidden = !line.product;
  $("#link-dialog-unlink").hidden = !line.link;
  $("#link-dialog-shared").hidden = !line.shared;
  $("#link-dialog-reject").hidden = !line.shared;
  $("#link-dialog-query").value = line.name;
  $("#link-dialog-list").innerHTML = "";
  message("#link-dialog-message", "");
  setProductLinkBusy(false);
  const dialog = $("#link-dialog");
  if (!dialog.open) dialog.showModal();
  $("#link-dialog-query").focus();
  searchProductLink();
}

document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-product-link]");
  if (!button) return;
  event.preventDefault();
  openProductLink(button);
});
$("#link-dialog-form").addEventListener("submit", (event) => {
  event.preventDefault();
  searchProductLink();
});
$("#link-dialog-list").addEventListener("click", (event) => {
  const item = event.target.closest("[data-link-pick]");
  if (item) pickProductLink(Number(item.dataset.linkPick));
});
$("#link-dialog-unlink").addEventListener("click", removeProductLink);
$("#link-dialog-reject").addEventListener("click", rejectSharedLink);
$("#link-dialog-close").addEventListener("click", () => $("#link-dialog").close());
$("#link-dialog").addEventListener("click", (event) => {
  if (event.target === event.currentTarget) event.currentTarget.close();
});
$("#link-dialog").addEventListener("close", () => {
  productLinks.run++; // een zoekopdracht die nog loopt, tekent niets meer
  productLinks.line = null;
});
