"""Aanbiedingen van de supermarkten en receptvoorstellen die ermee te maken zijn.

Aanbiedingen worden bij de winkel opgehaald en in SQLite bewaard; een vernieuwing vervangt de kopie van die
winkel in een keer, en mislukt het ophalen, dan blijft de vorige kopie staan. Voor de receptvoorstellen bewaren
we een index met de ingrediënten van je recepten (Mealie, Jumbo, Picnic en bewaarde AH-recepten). De voorstellen
komen daarna uit een lokale vergelijking: geen aanroep naar een winkel of een AI-dienst.

De aanbiedingen van AH (Bonus), Picnic en Jumbo worden elke dag door `offers_schedule` opgehaald, en op verzoek via
`sync_all` (de knop onder Instellingen > Koppelen) of per winkel via `sync` (de knop bij Aanbiedingen).
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
from typing import Any, Callable

from . import ah, ah_lists, offer_categories, ah_recipes, auth, jumbo, jumbo_recipes, mealie, mealie_images, picnic, picnic_recipes
from .i18n import tr

# Winkels waarvan we aanbiedingen kunnen ophalen, met de functie die dat doet.
FETCHERS: dict[str, Callable[[], list[dict[str, Any]]]] = {
    "ah": ah.get_bonus_products,
    "picnic": picnic.get_offers,
    "jumbo": jumbo.get_offers,
}
STORES = ("ah", "picnic", "jumbo")


def linked(store: str) -> bool:
    """Of we bij deze winkel kunnen vragen: AH Bonus werkt zonder inlog, Picnic en Jumbo vragen een koppeling."""
    if store == "picnic":
        return bool(picnic.configured_auth_key())
    if store == "jumbo":
        return bool(jumbo.configured_refresh_token())
    return True

MEALIE_RECIPE_LIMIT = 300
OTHER_RECIPE_LIMIT = 60
AH_NEW_RECIPES_PER_SYNC = 60
WORKERS = 8

SCHEMA = """
CREATE TABLE IF NOT EXISTS offers (
  store TEXT NOT NULL,
  product_id TEXT NOT NULL,
  position INTEGER NOT NULL,
  title TEXT NOT NULL,
  brand TEXT,
  unit_size TEXT,
  price REAL,
  price_before REAL,
  promo_label TEXT,
  image TEXT,
  url TEXT,
  valid_until TEXT,
  category TEXT,
  PRIMARY KEY (store, product_id)
);

-- Elke keer dat een product in de aanbieding was, voor altijd bewaard. Een rij is een aaneengesloten periode met dezelfde
-- prijs en hetzelfde label; zie `_record_history`. Daaruit volgt hoe vaak iets in de aanbieding was en wat de details waren.
CREATE TABLE IF NOT EXISTS offer_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  store TEXT NOT NULL,
  product_id TEXT NOT NULL,
  title TEXT NOT NULL,
  brand TEXT,
  unit_size TEXT,
  category TEXT,
  price REAL,
  price_before REAL,
  promo_label TEXT,
  image TEXT,
  url TEXT,
  valid_until TEXT,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  ended_at TEXT,
  syncs INTEGER NOT NULL DEFAULT 1,
  continued INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS offer_history_product ON offer_history (store, product_id);
CREATE INDEX IF NOT EXISTS offer_history_open ON offer_history (store, ended_at);

CREATE TABLE IF NOT EXISTS offers_meta (
  store TEXT PRIMARY KEY,
  synced_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS offer_recipes (
  source TEXT NOT NULL,
  recipe_id TEXT NOT NULL,
  title TEXT NOT NULL,
  image TEXT,
  url TEXT,
  ingredients TEXT NOT NULL,
  PRIMARY KEY (source, recipe_id)
);

CREATE TABLE IF NOT EXISTS offer_recipes_meta (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  synced_at TEXT NOT NULL,
  summary TEXT NOT NULL
);
"""


def _connect():
    conn = auth.connect()
    conn.executescript(SCHEMA)
    # Een database van vóór de categorieën heeft die kolom nog niet.
    if "category" not in {row["name"] for row in conn.execute("PRAGMA table_info(offers)")}:
        conn.execute("ALTER TABLE offers ADD COLUMN category TEXT")
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _valid_store(store: Any) -> str:
    store = str(store or "").strip().lower()
    if store not in STORES:
        raise ValueError(tr("Onbekende winkel (kies ah, picnic of jumbo)", "Unknown store (choose ah, picnic or jumbo)"))
    return store


# --- aanbiedingen -------------------------------------------------------------------------------


def load(store: Any) -> dict[str, Any]:
    """De lokale kopie van de aanbiedingen van een winkel; `syncedAt` is None zolang er nooit is opgehaald."""
    store = _valid_store(store)
    with closing(_connect()) as conn:
        meta = conn.execute("SELECT synced_at FROM offers_meta WHERE store = ?", (store,)).fetchone()
        rows = conn.execute(
            """
            SELECT product_id, title, brand, unit_size, price, price_before, promo_label, image, url, valid_until, category
            FROM offers WHERE store = ? ORDER BY position
            """,
            (store,),
        ).fetchall()
    offers = [
        {
            "id": row["product_id"],
            "title": row["title"],
            "brand": row["brand"],
            "unitSize": row["unit_size"],
            "price": row["price"],
            "priceBefore": row["price_before"],
            "promoLabel": row["promo_label"] or "",
            "image": row["image"] or "",
            "url": row["url"] or "",
            "validUntil": row["valid_until"],
            # Een kopie van vóór de categorieën krijgt de categorie alsnog uit de titel.
            "category": row["category"] or offer_categories.categorize(row["title"]),
        }
        for row in rows
    ]
    return {
        "store": store,
        "offers": offers,
        "total": len(offers),
        "categories": offer_categories.summarize([offer["category"] for offer in offers]),
        "syncedAt": meta["synced_at"] if meta else None,
        "supported": store in FETCHERS,
    }


def sync(store: Any) -> dict[str, Any]:
    """Haal de aanbiedingen van een winkel op, vervang de lokale kopie en schrijf ze weg in de geschiedenis.

    Mislukt het ophalen, dan verandert er niets: de vorige kopie en de geschiedenis blijven zoals ze waren.
    """
    store = _valid_store(store)
    fetch = FETCHERS.get(store)
    if not fetch:
        raise ValueError(tr("Aanbiedingen van deze winkel zijn nog niet beschikbaar", "Offers from this store are not available yet"))
    offers = fetch()
    now = _now()
    items = []
    seen: set[str] = set()
    for offer in offers:
        if not offer.get("id") or not offer.get("title") or str(offer["id"]) in seen:
            continue
        seen.add(str(offer["id"]))
        items.append(
            {
                "id": str(offer["id"]),
                "title": str(offer["title"]),
                "brand": offer.get("brand"),
                "unitSize": offer.get("unitSize"),
                "price": offer.get("price"),
                "priceBefore": offer.get("priceBefore"),
                "promoLabel": offer.get("promoLabel") or "",
                "image": offer.get("image") or "",
                "url": offer.get("url") or "",
                "validUntil": offer.get("validUntil"),
                "category": offer_categories.categorize(offer["title"], offer.get("categoryHint")),
            }
        )
    rows = [
        (
            store, item["id"], position, item["title"], item["brand"], item["unitSize"], item["price"], item["priceBefore"],
            item["promoLabel"], item["image"], item["url"], item["validUntil"], item["category"],
        )
        for position, item in enumerate(items)
    ]
    with closing(_connect()) as conn, conn:
        conn.execute("DELETE FROM offers WHERE store = ?", (store,))
        conn.executemany(
            """
            INSERT OR REPLACE INTO offers
              (store, product_id, position, title, brand, unit_size, price, price_before, promo_label, image, url, valid_until, category)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        _record_history(conn, store, items, now)
        conn.execute(
            """
            INSERT INTO offers_meta (store, synced_at) VALUES (?, ?)
            ON CONFLICT(store) DO UPDATE SET synced_at = excluded.synced_at
            """,
            (store, now),
        )
    return load(store)


# --- geschiedenis -------------------------------------------------------------------------------


def _details(price: Any, price_before: Any, label: Any) -> tuple[Any, Any, str]:
    return (price, price_before, label or "")


def _record_history(conn, store: str, items: list[dict[str, Any]], now: str) -> None:
    """Schrijf de aanbiedingen van deze synchronisatie in `offer_history`.

    Per product loopt er hooguit een open rij (`ended_at` leeg). Staat het product nog in de aanbieding met dezelfde prijs en
    hetzelfde label, dan wordt die rij bijgewerkt. Veranderen prijs of label, dan wordt de rij gesloten en begint een nieuwe die
    `continued` krijgt: het is dezelfde aanbieding, dus telt hij niet als een nieuwe keer. Is het product weg, dan wordt de rij
    gesloten en komt het later terug als een nieuwe keer.
    """
    current = {item["id"]: item for item in items}
    handled: set[str] = set()
    changed: set[str] = set()
    for row in conn.execute("SELECT id, product_id, price, price_before, promo_label FROM offer_history WHERE store = ? AND ended_at IS NULL", (store,)).fetchall():
        item = current.get(row["product_id"])
        if item is not None and row["product_id"] not in handled and _details(item["price"], item["priceBefore"], item["promoLabel"]) == _details(row["price"], row["price_before"], row["promo_label"]):
            handled.add(row["product_id"])
            conn.execute(
                """
                UPDATE offer_history SET last_seen = ?, syncs = syncs + 1, title = ?, brand = ?, unit_size = ?, category = ?,
                  image = ?, url = ?, valid_until = ? WHERE id = ?
                """,
                (now, item["title"], item["brand"], item["unitSize"], item["category"], item["image"], item["url"], item["validUntil"], row["id"]),
            )
            continue
        if item is not None:
            changed.add(row["product_id"])
        conn.execute("UPDATE offer_history SET ended_at = last_seen WHERE id = ?", (row["id"],))
    conn.executemany(
        """
        INSERT INTO offer_history
          (store, product_id, title, brand, unit_size, category, price, price_before, promo_label, image, url, valid_until,
           first_seen, last_seen, continued)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (store, item["id"], item["title"], item["brand"], item["unitSize"], item["category"], item["price"], item["priceBefore"],
             item["promoLabel"], item["image"], item["url"], item["validUntil"], now, now, 1 if item["id"] in changed else 0)
            for item in items
            if item["id"] not in handled
        ],
    )


def history(store: Any = None, query: Any = "", limit: Any = 50) -> dict[str, Any]:
    """Hoe vaak producten in de aanbieding waren en wat de details waren, per product, het laatst gezien bovenaan.

    `store` en `query` (een deel van de titel) zijn optioneel. `timesOnOffer` telt aaneengesloten periodes: een prijswijziging
    tijdens dezelfde aanbieding telt niet als een nieuwe keer.
    """
    stores = [_valid_store(store)] if store else list(STORES)
    pattern = "%" + str(query or "").strip().replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_") + "%"
    try:
        limit = max(1, min(200, int(limit)))
    except (TypeError, ValueError):
        limit = 50
    marks = ",".join("?" * len(stores))
    with closing(_connect()) as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM offer_history WHERE store IN ({marks}) AND title LIKE ? ESCAPE '\\'
            ORDER BY store, product_id, first_seen, id
            """,
            (*stores, pattern),
        ).fetchall()
    products: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        product = products.setdefault(
            (row["store"], row["product_id"]),
            {"store": row["store"], "productId": row["product_id"], "episodes": [], "timesOnOffer": 0},
        )
        product["episodes"].append(
            {
                "from": row["first_seen"],
                "to": None if row["ended_at"] is None else row["ended_at"],
                "lastSeen": row["last_seen"],
                "price": row["price"],
                "priceBefore": row["price_before"],
                "promoLabel": row["promo_label"] or "",
                "validUntil": row["valid_until"],
                "syncs": row["syncs"],
            }
        )
        product["timesOnOffer"] += 0 if row["continued"] else 1
        # De nieuwste rij geeft titel en plaatje.
        product.update(title=row["title"], brand=row["brand"], unitSize=row["unit_size"], category=row["category"] or offer_categories.categorize(row["title"]),
                       image=row["image"] or "", url=row["url"] or "", firstSeen=product.get("firstSeen") or row["first_seen"],
                       lastSeen=row["last_seen"], onOfferNow=row["ended_at"] is None)
    for product in products.values():
        prices = [e["price"] for e in product["episodes"] if isinstance(e["price"], (int, float))]
        product["lowestPrice"] = min(prices) if prices else None
    ranked = sorted(products.values(), key=lambda p: (p["lastSeen"], p["timesOnOffer"]), reverse=True)
    return {"products": ranked[:limit], "total": len(ranked)}


def sync_all() -> dict[str, Any]:
    """Haal de aanbiedingen van alle winkels op. Een winkel die niet gekoppeld is of mislukt laat de rest staan.

    Per winkel: `count` en `syncedAt` na een geslaagde vernieuwing, `skipped` als de winkel niet gekoppeld is,
    `error` als het ophalen mislukte (de vorige kopie blijft dan staan).
    """
    results: dict[str, Any] = {}
    for store in STORES:
        if not linked(store):
            results[store] = {"skipped": tr("niet gekoppeld", "not linked")}
            continue
        try:
            data = sync(store)
            results[store] = {"count": data["total"], "syncedAt": data["syncedAt"]}
        except Exception as exc:  # noqa: BLE001 - winkels zijn onofficieel; de bewaarde kopie blijft staan
            results[store] = {"error": str(exc)[:300] or tr("mislukt", "failed")}
    return {"stores": results, "finishedAt": _now()}


def overview() -> dict[str, Any]:
    """Per winkel: wanneer de aanbiedingen voor het laatst zijn opgehaald en hoeveel er staan."""
    with closing(_connect()) as conn:
        metas = {row["store"]: row["synced_at"] for row in conn.execute("SELECT store, synced_at FROM offers_meta")}
        counts = {row["store"]: row["n"] for row in conn.execute("SELECT store, COUNT(*) AS n FROM offers GROUP BY store")}
    return {
        store: {"linked": linked(store), "syncedAt": metas.get(store), "total": counts.get(store, 0)}
        for store in STORES
    }


# --- ingrediënten en aanbiedingen vergelijken ----------------------------------------------------

# Woorden die geen ingrediënt zijn: eenheden, hoeveelheden, bijwoorden en bereidingswoorden.
NOISE = {
    "g", "gr", "gram", "kg", "kilo", "mg", "ml", "cl", "dl", "l", "liter", "el", "eetlepel", "eetlepels",
    "tl", "theelepel", "theelepels", "koffielepel", "stuk", "stuks", "st", "teen", "tenen", "teentje", "teentjes",
    "blik", "blikje", "blikken", "pak", "pakje", "pakken", "zak", "zakje", "bos", "bosje", "snuf", "snufje",
    "plak", "plakje", "plakjes", "fles", "flesje", "bakje", "portie", "handvol", "scheutje", "scheut", "beetje",
    "naar", "smaak", "eventueel", "optioneel", "ca", "circa", "of", "en", "de", "het", "een", "in", "van", "met",
    "voor", "uit", "op", "per", "zonder", "plus", "extra", "vers", "verse", "gesneden", "fijngesneden",
    "gesnipperd", "geraspt", "gepeld", "gewassen", "grof", "fijn", "groot", "grote", "klein", "kleine",
    "middelgrote", "gekookt", "gebakken", "gerookt", "diepvries", "bijvoorbeeld", "bv", "uitgelekt", "halve",
    "hele", "heel", "half", "tot", "ongeveer", "stukje", "stukjes", "blad", "blaadjes",
}
# Basisingrediënten die altijd in huis zijn: een aanbieding daarvoor maakt een recept niet interessanter.
PANTRY = {"zout", "peper", "water", "ijs"}
# Een aanbieding waarvan het woord hiermee verder gaat is een ander product dan het ingrediënt (tomatenpuree, kippensoep).
DERIVED_PREFIXES = ("puree", "saus", "soep", "sap", "drank", "chips", "reep", "koek", "taart", "pizza", "ketchup", "jam", "smaak")


def _stem(token: str) -> str:
    """Een woord zonder meervoud en dubbele klinkers, zodat 'tomaten' en 'tomaat' op elkaar passen."""
    if len(token) > 5 and token.endswith("eren"):
        token = token[:-4]
    elif len(token) >= 4 and token.endswith("en"):
        token = token[:-2]
    elif len(token) > 3 and token.endswith("s") and not token.endswith(("ss", "us", "is")) and not re.search(r"[aeou]{2}s$", token):
        token = token[:-1]
    token = re.sub(r"([aeou])\1", r"\1", token)
    return re.sub(r"([^aeiou])\1$", r"\1", token)


DERIVED_STEMS = tuple(_stem(prefix) for prefix in DERIVED_PREFIXES)


def tokens(text: Any) -> list[str]:
    """De betekenisvolle woorden van een ingrediëntregel of producttitel, gestemd en zonder eenheden."""
    folded = ah_lists._fold(re.sub(r"\([^)]*\)", " ", str(text or "")))
    words = re.findall(r"[a-z]+", folded)
    return [_stem(word) for word in words if len(word) > 1 and word not in NOISE]


def head_of(ingredient: Any) -> str | None:
    """Het hoofdwoord van een ingrediënt (in het Nederlands het laatste woord), of None voor basisingrediënten."""
    words = tokens(ingredient)
    if not words or all(word in {_stem(item) for item in PANTRY} for word in words):
        return None
    return words[-1]


def matches(head: str, offer_stems: set[str]) -> bool:
    if head in offer_stems:
        return True
    if len(head) < 3:
        return False
    for stem in offer_stems:
        if len(stem) > len(head) and stem.startswith(head):
            rest = re.sub(r"^(?:en|s)", "", stem[len(head):])  # tomaten|puree, kippen|soep
            if not any(part in rest for part in DERIVED_STEMS):
                return True
    return False


def suggest(store: Any, limit: int = 30) -> dict[str, Any]:
    """Recepten uit de index waarvan ingrediënten in de aanbieding zijn, de beste eerst."""
    offers = load(store)
    prepared = [(offer, set(tokens(offer["title"]))) for offer in offers["offers"]]
    with closing(_connect()) as conn:
        meta = conn.execute("SELECT synced_at, summary FROM offer_recipes_meta WHERE id = 1").fetchone()
        rows = conn.execute("SELECT source, recipe_id, title, image, url, ingredients FROM offer_recipes").fetchall()
    results: list[dict[str, Any]] = []
    for row in rows:
        ingredients = json.loads(row["ingredients"])
        heads = [(text, head_of(text)) for text in ingredients]
        heads = [(text, head) for text, head in heads if head]
        if not heads:
            continue
        found: list[dict[str, Any]] = []
        used: set[str] = set()
        for text, head in heads:
            for offer, stems in prepared:
                if offer["id"] not in used and matches(head, stems):
                    used.add(offer["id"])
                    found.append({"ingredient": text, "offer": offer})
                    break
        needed = 1 if len(heads) <= 3 else 2
        if len(found) < needed:
            continue
        saving = sum(
            max(0.0, (item["offer"]["priceBefore"] or 0) - (item["offer"]["price"] or 0)) for item in found
        )
        results.append(
            {
                "source": row["source"],
                "id": row["recipe_id"],
                "title": row["title"],
                "image": row["image"] or "",
                "url": row["url"] or "",
                "ingredientCount": len(heads),
                "matchedCount": len(found),
                "matched": found,
                "saving": round(saving, 2),
            }
        )
    results.sort(key=lambda item: (-item["matchedCount"] / item["ingredientCount"], -item["matchedCount"], -item["saving"], item["title"]))
    return {
        "store": offers["store"],
        "recipes": results[: max(1, min(100, int(limit)))],
        "total": len(results),
        "offersSyncedAt": offers["syncedAt"],
        "indexedAt": meta["synced_at"] if meta else None,
        "sources": json.loads(meta["summary"]) if meta else {},
    }


# --- de receptenindex ---------------------------------------------------------------------------


def _mealie_ingredient(item: Any) -> str:
    if not isinstance(item, dict):
        return ""
    food = item.get("food") if isinstance(item.get("food"), dict) else {}
    return str(food.get("name") or item.get("note") or item.get("display") or item.get("originalText") or "").strip()


def _collect_mealie() -> list[dict[str, Any]]:
    summaries = mealie.list_recipes(MEALIE_RECIPE_LIMIT)

    def one(summary: dict[str, Any]) -> dict[str, Any] | None:
        try:
            recipe = mealie.get_recipe(summary["slug"])
        except Exception:  # noqa: BLE001 - een recept dat niet lukt slaan we over
            return None
        lines = [line for line in (_mealie_ingredient(i) for i in recipe.get("recipeIngredient") or []) if line]
        if not lines:
            return None
        return {
            "id": str(recipe.get("id") or summary["slug"]),
            "title": str(recipe.get("name") or summary["slug"]),
            "image": mealie_images.image_path(recipe) or "",
            "url": mealie.recipe_page_url(summary["slug"]),
            "ingredients": lines,
        }

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        return [item for item in pool.map(one, summaries) if item]


def _collect_jumbo() -> list[dict[str, Any]]:
    if not jumbo.configured_refresh_token():
        raise LookupError(tr("niet gekoppeld", "not linked"))
    cards = jumbo_recipes.get_favorites(1).get("recipes") or []

    def one(card: dict[str, Any]) -> dict[str, Any] | None:
        try:
            recipe = jumbo_recipes.get_recipe(card["id"])
        except Exception:  # noqa: BLE001
            return None
        lines = [i["name"] for i in recipe["ingredients"] if i.get("name")]
        if not lines:
            return None
        return {"id": recipe["id"], "title": recipe["name"], "image": recipe.get("image") or "",
                "url": jumbo_recipes.source_url(recipe), "ingredients": lines}

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        return [item for item in pool.map(one, cards[:OTHER_RECIPE_LIMIT]) if item]


def _collect_picnic() -> list[dict[str, Any]]:
    if not picnic.configured_auth_key():
        raise LookupError(tr("niet gekoppeld", "not linked"))
    cards: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in ("saved", "cookbook"):
        for card in picnic_recipes.get_recipes(source):
            if card["id"] not in seen:
                seen.add(card["id"])
                cards.append(card)

    def one(card: dict[str, Any]) -> dict[str, Any] | None:
        try:
            recipe = picnic_recipes.get_recipe(card["id"])
        except Exception:  # noqa: BLE001
            return None
        lines = [str(i.get("name") or i.get("line") or "").strip() for i in recipe["ingredients"]]
        lines = [line for line in lines if line]
        if not lines:
            return None
        return {"id": recipe["id"], "title": recipe["name"], "image": recipe.get("image") or "",
                "url": picnic_recipes.source_url(recipe["id"]), "ingredients": lines}

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        return [item for item in pool.map(one, cards[:OTHER_RECIPE_LIMIT]) if item]


def _indexed(source: str) -> dict[str, list[str]]:
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT recipe_id, ingredients FROM offer_recipes WHERE source = ?", (source,)).fetchall()
    return {row["recipe_id"]: json.loads(row["ingredients"]) for row in rows}


def _collect_ah() -> list[dict[str, Any]]:
    saved = ah_recipes.load()
    if not saved or not saved["recipes"]:
        raise LookupError(tr("geen bewaarde recepten (haal ze eerst op bij Winkels > AH Recepten)", "no saved recipes (fetch them first under Stores > AH Recipes)"))
    known = _indexed("ah")
    fetched = 0
    items: list[dict[str, Any]] = []
    for recipe in saved["recipes"]:
        recipe_id = str(recipe["id"])
        lines = known.get(recipe_id)
        if lines is None:
            if fetched >= AH_NEW_RECIPES_PER_SYNC:
                continue
            fetched += 1
            try:
                lines = ah.get_recipe_jsonld(int(recipe_id)).get("recipeIngredient") or []
            except Exception:  # noqa: BLE001
                continue
        if lines:
            items.append({"id": recipe_id, "title": recipe["title"], "image": recipe.get("image") or "",
                          "url": recipe.get("url") or "", "ingredients": list(lines)})
    return items


COLLECTORS: tuple[tuple[str, Callable[[], list[dict[str, Any]]]], ...] = (
    ("mealie", _collect_mealie),
    ("jumbo", _collect_jumbo),
    ("picnic", _collect_picnic),
    ("ah", _collect_ah),
)


def sync_recipes() -> dict[str, Any]:
    """Lees de ingrediënten van je recepten in de index. Een bron die niet lukt of niet gekoppeld is laat de rest staan."""
    summary: dict[str, Any] = {}
    collected: dict[str, list[dict[str, Any]]] = {}
    for source, collect in COLLECTORS:
        try:
            collected[source] = collect()
            summary[source] = {"count": len(collected[source])}
        except LookupError as exc:
            summary[source] = {"count": 0, "skipped": str(exc)}
        except Exception as exc:  # noqa: BLE001 - een bron die faalt mag de andere niet blokkeren
            summary[source] = {"count": 0, "error": str(exc)[:200] or tr("mislukt", "failed")}
    with closing(_connect()) as conn, conn:
        for source, items in collected.items():
            conn.execute("DELETE FROM offer_recipes WHERE source = ?", (source,))
            conn.executemany(
                "INSERT OR REPLACE INTO offer_recipes (source, recipe_id, title, image, url, ingredients) VALUES (?, ?, ?, ?, ?, ?)",
                [(source, item["id"], item["title"], item["image"], item["url"], json.dumps(item["ingredients"])) for item in items],
            )
        conn.execute(
            """
            INSERT INTO offer_recipes_meta (id, synced_at, summary) VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET synced_at = excluded.synced_at, summary = excluded.summary
            """,
            (_now(), json.dumps(summary)),
        )
    return {"indexedAt": _now(), "sources": summary, "total": sum(len(items) for items in collected.values())}
