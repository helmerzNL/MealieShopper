"""Aanbiedingen van de supermarkten en receptvoorstellen die ermee te maken zijn.

Aanbiedingen worden bij de winkel opgehaald en in SQLite bewaard; een vernieuwing vervangt de kopie van die
winkel in een keer, en mislukt het ophalen, dan blijft de vorige kopie staan. Voor de receptvoorstellen bewaren
we een index met de ingrediënten van je recepten (Mealie, Jumbo, Picnic en bewaarde AH-recepten). De voorstellen
komen daarna uit een lokale vergelijking: geen aanroep naar een winkel of een AI-dienst (de optionele AI-beoordeling in `ai_review.py` draait alleen op een knop).

De aanbiedingen van AH (Bonus), Picnic en Jumbo worden elke dag door `offers_schedule` opgehaald, en op verzoek via
`sync_all` (de knop onder Instellingen > Koppelen) of per winkel via `sync` (de knop bij Aanbiedingen).
"""
from __future__ import annotations

import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import date, datetime, timezone
from typing import Any, Callable, Iterable

from . import about, ah, ah_lists, deals, offer_categories, offer_courses, offer_translate, ah_recipes, auth, jumbo, jumbo_recipes, mealie, mealie_images, picnic, picnic_recipes
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
  valid_from TEXT,
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
  valid_from TEXT,
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
  course TEXT,
  PRIMARY KEY (source, recipe_id)
);

-- Koppelingen die je als onjuist hebt gemeld: dit product hoort niet bij dit hoofdwoord van een ingrediënt (in deze winkel).
CREATE TABLE IF NOT EXISTS offer_rejections (
  store TEXT NOT NULL,
  product_id TEXT NOT NULL,
  head TEXT NOT NULL,
  ingredient TEXT NOT NULL,
  title TEXT NOT NULL,
  reported_at TEXT NOT NULL,
  reason TEXT,
  source TEXT,
  PRIMARY KEY (store, product_id, head)
);

-- Wat een AI (zie ai_review.py) van een koppeling vond: `ok` of `wrong`, per winkel, product en hoofdwoord van het ingrediënt. Zo wordt elk
-- paar maar een keer beoordeeld, ook als je een melding later ongedaan maakt.
CREATE TABLE IF NOT EXISTS offer_verdicts (
  store TEXT NOT NULL,
  product_id TEXT NOT NULL,
  head TEXT NOT NULL,
  ingredient TEXT NOT NULL,
  product_title TEXT NOT NULL,
  verdict TEXT NOT NULL,
  reason TEXT,
  provider TEXT,
  model TEXT,
  judged_at TEXT NOT NULL,
  PRIMARY KEY (store, product_id, head)
);

-- Het resultaat van het zoeken door een AI (zie ai_match.py): per winkel en recept welke ingrediëntregel aan welk product gekoppeld is, en
-- per doorzocht recept bij welke versie van het aanbod en welke ingrediënten dat gebeurde.
CREATE TABLE IF NOT EXISTS ai_recipe_matches (
  store TEXT NOT NULL,
  source TEXT NOT NULL,
  recipe_id TEXT NOT NULL,
  line_index INTEGER NOT NULL,
  ingredient TEXT NOT NULL,
  product_id TEXT NOT NULL,
  product_title TEXT NOT NULL,
  provider TEXT,
  model TEXT,
  matched_at TEXT NOT NULL,
  PRIMARY KEY (store, source, recipe_id, line_index)
);

CREATE TABLE IF NOT EXISTS ai_recipe_runs (
  store TEXT NOT NULL,
  source TEXT NOT NULL,
  recipe_id TEXT NOT NULL,
  offers_synced_at TEXT NOT NULL,
  ingredients_hash TEXT NOT NULL,
  matched_count INTEGER NOT NULL,
  provider TEXT,
  model TEXT,
  searched_at TEXT NOT NULL,
  PRIMARY KEY (store, source, recipe_id)
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
    # Een kopie van vóór de looptijd heeft geen begindatum; die komt bij de volgende synchronisatie.
    if "valid_from" not in {row["name"] for row in conn.execute("PRAGMA table_info(offers)")}:
        conn.execute("ALTER TABLE offers ADD COLUMN valid_from TEXT")
    # Een geschiedenis van vóór de looptijd heeft geen begindatum; oude rijen houden `first_seen` als begin.
    if "valid_from" not in {row["name"] for row in conn.execute("PRAGMA table_info(offer_history)")}:
        conn.execute("ALTER TABLE offer_history ADD COLUMN valid_from TEXT")
    # Een index van vóór de soorten gerecht heeft die kolom nog niet; de soort volgt bij de volgende keer inlezen.
    if "course" not in {row["name"] for row in conn.execute("PRAGMA table_info(offer_recipes)")}:
        conn.execute("ALTER TABLE offer_recipes ADD COLUMN course TEXT")
    # Een melding van v2.66.0 heeft nog geen reden en bron.
    rejection_columns = {row["name"] for row in conn.execute("PRAGMA table_info(offer_rejections)")}
    for column in ("reason", "source"):
        if column not in rejection_columns:
            conn.execute(f"ALTER TABLE offer_rejections ADD COLUMN {column} TEXT")
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _valid_store(store: Any) -> str:
    store = str(store or "").strip().lower()
    if store not in STORES:
        raise ValueError(tr("Onbekende winkel (kies ah, picnic of jumbo)", "Unknown store (choose ah, picnic or jumbo)"))
    return store


# --- aanbiedingen -------------------------------------------------------------------------------


def signature(store: Any) -> str:
    """Verandert precies dan als `load(store)` een ander antwoord geeft: bij een nieuwe synchronisatie, op een nieuwe dag (aanbiedingen
    lopen af) en bij een nieuwe versie van de app (de indeling en de voorwaarden worden bij het laden uit de bewaarde tekst afgeleid).
    Goedkoop: een rij uit de database. Dient als ETag, zodat een browser met de lijst al in de cache geen nieuwe hoeft te krijgen."""
    store = _valid_store(store)
    with closing(_connect()) as conn:
        meta = conn.execute("SELECT synced_at FROM offers_meta WHERE store = ?", (store,)).fetchone()
    return "|".join((store, meta["synced_at"] if meta else "", date.today().isoformat(), about.read_version()))


def load(store: Any) -> dict[str, Any]:
    """De lokale kopie van de aanbiedingen van een winkel; `syncedAt` is None zolang er nooit is opgehaald."""
    store = _valid_store(store)
    with closing(_connect()) as conn:
        meta = conn.execute("SELECT synced_at FROM offers_meta WHERE store = ?", (store,)).fetchone()
        rows = conn.execute(
            """
            SELECT product_id, title, brand, unit_size, price, price_before, promo_label, image, url, valid_from, valid_until, category
            FROM offers WHERE store = ? ORDER BY position
            """,
            (store,),
        ).fetchall()
    all_offers = [
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
            "validFrom": row["valid_from"],
            "validUntil": row["valid_until"],
            # Een kopie van vóór de categorieën krijgt de categorie alsnog uit de titel.
            "category": row["category"] or offer_categories.categorize(row["title"]),
            # Wat de aanbieding inhoudt: soort, minimumaantal, prijs per stuk bij de voorwaarde en of ze nog geldt.
            **deals.describe(
                row["promo_label"], price_now=row["price"], price_before=row["price_before"],
                valid_from=row["valid_from"], valid_until=row["valid_until"],
            ),
        }
        for row in rows
    ]
    # Tussen twee synchronisaties kan een aanbieding aflopen; die laten we niet meer zien.
    offers = [offer for offer in all_offers if offer["validity"] != deals.EXPIRED]
    return {
        "store": store,
        "offers": offers,
        "total": len(offers),
        "expired": len(all_offers) - len(offers),
        "categories": offer_categories.summarize([offer["category"] for offer in offers]),
        "period": deals.common_period(offers),
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
                "validFrom": offer.get("validFrom"),
                "validUntil": offer.get("validUntil"),
                "category": offer_categories.categorize(offer["title"], offer.get("categoryHint")),
            }
        )
    rows = [
        (
            store, item["id"], position, item["title"], item["brand"], item["unitSize"], item["price"], item["priceBefore"],
            item["promoLabel"], item["image"], item["url"], item["validFrom"], item["validUntil"], item["category"],
        )
        for position, item in enumerate(items)
    ]
    with closing(_connect()) as conn, conn:
        conn.execute("DELETE FROM offers WHERE store = ?", (store,))
        conn.executemany(
            """
            INSERT OR REPLACE INTO offers
              (store, product_id, position, title, brand, unit_size, price, price_before, promo_label, image, url, valid_from, valid_until, category)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
    gesloten en komt het later terug als een nieuwe keer. Begint de looptijd op een andere dag dan die van de open rij (beide
    bekend), dan is het een nieuwe aanbiedingsperiode: ook dan een nieuwe keer, ook al is de prijs gelijk.
    """
    current = {item["id"]: item for item in items}
    handled: set[str] = set()
    changed: set[str] = set()
    for row in conn.execute("SELECT id, product_id, price, price_before, promo_label, valid_from FROM offer_history WHERE store = ? AND ended_at IS NULL", (store,)).fetchall():
        item = current.get(row["product_id"])
        new_period = item is not None and bool(row["valid_from"]) and bool(item["validFrom"]) and row["valid_from"] != item["validFrom"]
        if new_period:
            conn.execute("UPDATE offer_history SET ended_at = last_seen WHERE id = ?", (row["id"],))
            continue
        if item is not None and row["product_id"] not in handled and _details(item["price"], item["priceBefore"], item["promoLabel"]) == _details(row["price"], row["price_before"], row["promo_label"]):
            handled.add(row["product_id"])
            conn.execute(
                """
                UPDATE offer_history SET last_seen = ?, syncs = syncs + 1, title = ?, brand = ?, unit_size = ?, category = ?,
                  image = ?, url = ?, valid_from = COALESCE(?, valid_from), valid_until = ? WHERE id = ?
                """,
                (now, item["title"], item["brand"], item["unitSize"], item["category"], item["image"], item["url"], item["validFrom"], item["validUntil"], row["id"]),
            )
            continue
        if item is not None:
            changed.add(row["product_id"])
        conn.execute("UPDATE offer_history SET ended_at = last_seen WHERE id = ?", (row["id"],))
    conn.executemany(
        """
        INSERT INTO offer_history
          (store, product_id, title, brand, unit_size, category, price, price_before, promo_label, image, url, valid_from, valid_until,
           first_seen, last_seen, continued)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (store, item["id"], item["title"], item["brand"], item["unitSize"], item["category"], item["price"], item["priceBefore"],
             item["promoLabel"], item["image"], item["url"], item["validFrom"], item["validUntil"], now, now, 1 if item["id"] in changed else 0)
            for item in items
            if item["id"] not in handled
        ],
    )


def _episode_span(row) -> dict[str, Any]:
    """De dagen waarop een periode in de aanbieding was, als ISO-datums voor de grafiek.

    Het begin is de begindatum van de winkel, of de dag dat wij het product voor het eerst zagen. Het einde is de einddatum van
    de winkel, of de dag dat we het voor het laatst zagen. Een doorlopende aanbieding die nog loopt heeft geen einde (`open`).
    """
    start = deals.parse_date(row["valid_from"]) or deals.parse_date(row["first_seen"])
    end = deals.parse_date(row["valid_until"])
    is_open = end is None and row["ended_at"] is None
    if end is None and not is_open:
        end = deals.parse_date(row["ended_at"])
    if start is not None and end is not None and end < start:
        start = end
    return {"start": start.isoformat() if start else None, "end": end.isoformat() if end else None, "open": is_open}


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
                "validFrom": row["valid_from"],
                "validUntil": row["valid_until"],
                "syncs": row["syncs"],
                **_episode_span(row),
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
        product["highestPrice"] = max(prices) if prices else None
        before = [e["priceBefore"] for e in product["episodes"] if isinstance(e["priceBefore"], (int, float))]
        product["regularPrice"] = max(before) if before else None
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
    # Engelse recepten (veel Mealie-imports): eenheden, voegwoorden en bijwoorden.
    "tbsp", "tsp", "cup", "cups", "pinch", "slice", "slices", "clove", "cloves", "piece", "pieces", "can", "cans",
    "bunch", "handful", "and", "with", "to", "the", "an", "some", "taste", "optional", "about", "fresh", "chopped",
    "sliced", "diced", "minced", "grated", "large", "small", "medium", "pound", "lb", "oz",
}


def _is_noise(word: str) -> bool:
    """Een eenheid of bijwoord, of de verbogen vorm van een bijvoeglijk woord uit de lijst ("geraspte" naast "geraspt")."""
    return word in NOISE or (word.endswith("e") and word[:-1] in NOISE)
# Basisingrediënten die altijd in huis zijn: een aanbieding daarvoor maakt een recept niet interessanter en ze tellen niet mee in
# "x van y ingrediënten in de aanbieding". Een ingrediënt is basis als het hoofdwoord er een is of erop eindigt (zeezout, roomboter,
# olijfolie, balsamicoazijn).
PANTRY = (
    "zout", "peper", "water", "ijs", "olie", "boter", "bloem", "suiker", "azijn", "bouillon", "bouillonblokje",
    # Kruidenpoeders (knoflookpoeder, onion powder) en de Engelse basis.
    "poeder", "powder", "salt", "pepper", "oil", "butter", "flour", "sugar", "vinegar", "stock", "broth", "cornstarch", "maizena",
)
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
PANTRY_STEMS = {_stem(word) for word in PANTRY}


def is_basic(word: str) -> bool:
    """Of een (gestemd) woord een basisingrediënt is: zelf in de lijst, of een samenstelling die erop eindigt."""
    return word in PANTRY_STEMS or any(len(basic) >= 4 and word.endswith(basic) for basic in PANTRY_STEMS)


# "all'uovo", "all' uovo": een Italiaanse toevoeging aan een pasta ("tagliatelle all'uovo"), geen eigen product. Zonder deze regel is "uovo" het
# hoofdwoord van "spaghetti all'uovo" en past het ingrediënt bij elke pasta all'uovo.
ITALIAN_ADDITION = re.compile(r"\ball?['\u2019]\s*\w+")


def tokens(text: Any) -> list[str]:
    """De betekenisvolle woorden van een ingrediëntregel of producttitel, gestemd en zonder eenheden."""
    folded = ITALIAN_ADDITION.sub(" ", ah_lists._fold(re.sub(r"\([^)]*\)", " ", str(text or ""))))
    words = re.findall(r"[a-z]+", folded)
    return [_stem(word) for word in words if len(word) > 1 and not _is_noise(word)]


# Voorzetsels in een ingrediëntregel: wat erna staat beschrijft het ingrediënt ("gehakt met ui", "kipfilet in blokjes",
# "olie voor het bakken") en is niet het ingrediënt zelf.
INGREDIENT_CONNECTORS = re.compile(r"\b(?:met|in|op|zonder|voor|van)\b")
# Een theelepel of een snuf is een kruid, geen product om bij de aanbiedingen te zoeken (1 tsp salt, 2 tl kurkuma, een snufje nootmuskaat).
SEASONING_QUANTITY = re.compile(r"\b(?:tsp|tl|theelepels?|teaspoons?|snuf(?:je)?s?|mespunt(?:je)?s?|pinch(?:es)?)\b")
# Een eetlepel van een gedroogde specerij ("2 tbsp paprika", "1 el kurkuma") is een kruid. Verse groente in een eetlepel komt nauwelijks voor,
# dus zo'n regel zoekt geen paprika of kerriesaus bij de aanbiedingen.
SPOON_QUANTITY = re.compile(r"\b(?:tbsp|el|eetlepels?|tablespoons?)\b")
DRIED_SPICES = {
    _stem(word) for word in ("paprika", "kurkuma", "turmeric", "komijn", "cumin", "kaneel", "cinnamon", "cayenne", "nootmuskaat", "nutmeg",
                             "kerrie", "curry")
}
# Een regel die naar een ander deel van het recept verwijst ("Spice mixture (below)") is een kopje en geen ingrediënt.
HEADING_NOTE = re.compile(r"\((?:see\s+)?(?:below|above|hieronder|hierboven|zie[^)]*)\)")


def head_of(ingredient: Any) -> str | None:
    """Het hoofdwoord van een ingrediënt (in het Nederlands het laatste woord vóór een voorzetsel), of None voor basisingrediënten."""
    text = str(ingredient or "")
    lowered = ah_lists._fold(text)
    if SEASONING_QUANTITY.search(lowered) or HEADING_NOTE.search(lowered):
        return None
    folded = ah_lists._fold(re.sub(r"\([^)]*\)", " ", text))
    words = tokens(INGREDIENT_CONNECTORS.split(folded, 1)[0]) or tokens(text)
    # Alleen basis- en smaakwoorden ("zout en peper", "verse kruiden"), of een basis als hoofdwoord ("milde olijfolie"). Een smaakwoord
    # aan het eind van een langere regel ("zuivelspread knoflook en kruiden") blijft staan: het ingrediënt telt mee maar zoekt geen product.
    if not words or all(is_basic(word) or word in WEAK_HEADS for word in words) or (is_basic(words[-1]) and words[-1] not in WEAK_HEADS):
        return None
    if words[-1] in DRIED_SPICES and SPOON_QUANTITY.search(lowered):
        return None
    return words[-1]


def matches(head: str, offer_stems: set[str]) -> bool:
    if head in offer_stems:
        return True
    if len(head) < 3:
        return False
    for stem in offer_stems:
        if len(stem) > len(head) and stem.startswith(head):
            rest = stem[len(head):]
            linked = re.sub(r"^(?:en|s)", "", rest)  # tomaten|puree, kippen|soep: een voegklank hoort niet bij het woord
            # Beide kanten controleren: bij sinaasappel|sap is de s geen voegklank maar het begin van "sap".
            if not any(part in rest or part in linked for part in DERIVED_STEMS):
                return True
    return False


def saving_of(offer: dict[str, Any]) -> float:
    """Wat een stuk goedkoper is dan normaal als je de voorwaarde van de aanbieding haalt.

    Met de gewone prijs en een herkende aanbieding ("2e halve prijs", "2 voor 5") rekenen we met de prijs per stuk die daaruit
    volgt, want de prijs die de winkel noemt kan de gewone prijs of de actieprijs zijn. Anders is het verschil tussen de oude en
    de huidige prijs.
    """
    before, unit = offer.get("priceBefore"), offer.get("unitPrice")
    if before and unit is not None:
        return max(0.0, before - unit)
    return max(0.0, (before or 0) - (offer.get("price") or 0))


# --- een ingrediënt aan een aanbieding koppelen ------------------------------------------------------
# Woorden waarmee een producttitel een samengesteld product aankondigt: "Gehakt met ui", "Tonijn in olijfolie",
# "Champignon saus romig en vol". Wat erachter staat hoort bij het product, niet bij het ingrediënt.
CONNECTORS = {"met", "in", "op", "zonder", "voor", "en", "of", "&"}
# Een woord in de titel dat hiermee eindigt maakt het product een bereide variant (champignonsaus, kruidenmix,
# sinaasappelsap). Zo'n product is het ingrediënt alleen als het ingrediënt zelf dat woord noemt.
PRODUCT_TYPES = (
    "saus", "soep", "mix", "maaltijd", "salade", "dressing", "pesto", "bouillon", "chips", "koek", "taart",
    "pizza", "ketchup", "jam", "sap", "drank", "snack", "gerecht", "spread", "dip", "burger", "nugget", "schotel",
)
# Dezelfde bewerking als de woorden in de titel, anders komt "maaltijd" (titel: "maltijd") er nooit mee overeen.
PRODUCT_STEMS = tuple(_stem(kind) for kind in PRODUCT_TYPES)
# Smaakwoorden zijn geen ingrediënt om een product bij te zoeken: "kruiden" vindt anders elke kruidenmix.
WEAK_HEADS = {_stem(word) for word in ("kruiden", "specerijen", "smaakmakers", "garnering", "topping")}
# Een soort in plaats van een product: bij "curry sauce" of "bread roll" bepaalt het andere woord welk product het is. Zo'n ingrediënt
# past alleen bij een aanbieding waar die andere woorden ook in staan (niet "Sweet chilli sauce" of "Bifi Roll").
GENERIC_HEADS = {
    _stem(word) for word in (
        "saus", "sauce", "soep", "soup", "kaas", "cheese", "worst", "sausage", "brood", "bread", "rol", "roll", "broodje", "dressing",
        "drank", "vlees", "vis", "groente", "fruit",
    )
}


def offer_parts(title: Any) -> tuple[list[str], list[str]]:
    """De titel van een aanbieding in twee delen: het product zelf, en wat erbij hoort na `met`, `in`, `en` enzovoort.

    "AH Gehakt met ui 3-pack" wordt (["ah", "gehakt"], ["ui"]). Merk, aantallen en verpakking tellen niet mee.
    """
    core: list[str] = []
    rest: list[str] = []
    target = core
    for word in re.findall(r"[a-z0-9]+|&", ITALIAN_ADDITION.sub(" ", ah_lists._fold(title))):
        if word in CONNECTORS:
            if target is core and core:
                target = rest
            continue
        if len(word) < 2 or _is_noise(word) or word == "pack" or word.isdigit():
            continue
        target.append(_stem(word))
    return core, rest


def _covered(stem: str, ingredient_stems: set[str]) -> bool:
    """Of een woord uit de titel ook in het ingrediënt staat (ook als het een samenstelling is: zalm en zalmfilet)."""
    return stem in ingredient_stems or any(
        len(other) >= 3 and len(stem) >= 3 and (stem.startswith(other) or other.startswith(stem)) for other in ingredient_stems
    )


def _variant_key(word: str) -> str:
    """Een woord zonder buigings-e, dubbele klinkers en meervoud, zodat rode/rood, witte/wit en chinese/chinees hetzelfde zijn."""
    word = word[:-1] if word.endswith("e") and len(word) > 3 else word
    word = re.sub(r"([aeou])\1", r"\1", word)
    word = re.sub(r"([^aeiou])\1$", r"\1", word)
    return word[:-1] if word.endswith("s") and len(word) > 4 else word


# Eigenschappen die een product onderscheiden: een rode paprika is geen gele, wokgroente op z'n Chinees geen Provençaalse en halfvolle melk geen volle.
# Noemen ingrediënt én aanbieding een eigenschap uit dezelfde groep en zijn die verschillend, dan is het een ander product.
_VARIANT_WORDS: tuple[tuple[str, ...], ...] = (
    # kleur
    ("rood", "rode", "geel", "gele", "groen", "groene", "oranje", "wit", "witte", "zwart", "zwarte", "bruin", "bruine", "paars", "paarse",
     "blauw", "blauwe"),
    # keuken of streek
    ("chinees", "chinese", "provencaals", "provencaalse", "italiaans", "italiaanse", "mexicaans", "mexicaanse", "thai", "thais", "thaise",
     "indiaas", "indiase", "grieks", "griekse", "spaans", "spaanse", "frans", "franse", "hollands", "hollandse", "oosters", "oosterse",
     "mediterraan", "mediterrane", "japans", "japanse", "koreaans", "koreaanse", "turks", "turkse", "marokkaans", "marokkaanse",
     "amerikaans", "amerikaanse", "surinaams", "surinaamse", "indonesisch", "indonesische", "aziatisch", "aziatische"),
    # vetgehalte
    ("mager", "magere", "halfvol", "halfvolle", "vol", "volle"),
)


def _variant_canon(words: tuple[str, ...]) -> dict[str, str]:
    """Alle vormen van de woorden in een groep (met en zonder -e, en zoals `_stem` ze maakt: groen wordt gro) naar één vaste vorm."""
    canon: dict[str, str] = {}
    for word in words:
        fixed = _variant_key(word)
        for form in (word, _stem(word), word[:-1] if word.endswith("e") else word, _stem(word[:-1] if word.endswith("e") else word)):
            canon[_variant_key(form)] = fixed
    return canon


VARIANT_GROUPS: tuple[dict[str, str], ...] = tuple(_variant_canon(words) for words in _VARIANT_WORDS)


def variant_clash(ingredient_stems: Iterable[str], title_stems: Iterable[str]) -> bool:
    """Of ingrediënt en aanbieding een eigenschap uit dezelfde groep (kleur, keuken, vetgehalte) noemen die niet overeenkomt."""
    for group in VARIANT_GROUPS:
        wanted = {group[key] for stem in ingredient_stems if (key := _variant_key(stem)) in group}
        offered = {group[key] for stem in title_stems if (key := _variant_key(stem)) in group}
        if wanted and offered and not wanted & offered:
            return True
    return False


def match_offer(
    head: str, ingredient_stems: set[str], core: list[str], rest: list[str], main_stems: set[str] | None = None
) -> int | None:
    """Of een aanbieding het ingrediënt is. None: nee. Anders het aantal woorden in de titel dat niet bij het ingrediënt
    hoort; hoe minder, hoe beter de aanbieding past.

    `main_stems` zijn de woorden van het ingrediënt vóór een voorzetsel; zonder die opgave zijn het alle woorden.
    """
    if not head or head in WEAK_HEADS or not matches(head, set(core)):
        return None
    # Een soort ("sauce", "roll"): de andere woorden van het ingrediënt moeten er ook in staan.
    if head in GENERIC_HEADS and any(not _covered(stem, set(core)) for stem in (main_stems or ingredient_stems) if stem != head):
        return None
    # Samengesteld product ("Gehakt met ui"): alleen als het ingrediënt de rest ook noemt.
    if any(not _covered(stem, ingredient_stems) for stem in rest):
        return None
    # Bereide variant ("champignonsaus"): alleen als het ingrediënt dat zelf ook is.
    for stem in core:
        for kind in PRODUCT_STEMS:
            if stem.endswith(kind) and not any(kind in other for other in ingredient_stems):
                return None
    # Een andere kleur, keuken of vetgehalte ("Provençaalse wokgroente" tegenover "Wokgroente Chinees").
    if variant_clash(main_stems or ingredient_stems, [*core, *rest]):
        return None
    return sum(1 for stem in core if not _covered(stem, ingredient_stems))


# Aanbiedingen die nooit een ingrediënt zijn (shampoo met avocado, kattenvoer met kip).
NOT_FOOD = {"drogisterie", "huishouden", "baby", "huisdier"}
# Een rauw product past niet bij een bereid product: paprika is geen paprikachips of focaccia met paprika, kipfilet geen kipwrap.
RAW_CATEGORIES = {"groente_fruit", "vlees", "vis", "zuivel_eieren", "kaas_vleeswaren"}
PREPARED_CATEGORIES = {"brood_bakkerij", "maaltijden", "snacks_snoep"}


def ingredient_kind(ingredient: Any) -> str:
    """De soort van een ingrediënt (groente en fruit, vlees, ...), uit het deel vóór een voorzetsel: bij "roomkaas met bieslook" is dat kaas."""
    folded = ah_lists._fold(re.sub(r"\([^)]*\)", " ", str(ingredient or "")))
    return offer_categories.categorize(INGREDIENT_CONNECTORS.split(folded, 1)[0].strip() or ingredient)


def link_offer(
    ingredient: str,
    offer: dict[str, Any],
    parts: tuple[list[str], list[str]] | None = None,
    *,
    head: str | None = None,
    kind: str | None = None,
) -> int | None:
    """Of deze aanbieding bij deze ingrediëntregel hoort. None: nee. Anders het aantal woorden in de titel dat niet bij het
    ingrediënt hoort (minder is beter).

    `parts`, `head` en `kind` (de soort van het ingrediënt) kun je meegeven als je ze al hebt: bij veel recepten scheelt dat veel rekenwerk.
    """
    head = head if head is not None else head_of(ingredient)
    if not head or offer.get("category") in NOT_FOOD:
        return None
    kind = kind if kind is not None else ingredient_kind(ingredient)
    if kind in RAW_CATEGORIES and offer.get("category") in PREPARED_CATEGORIES:
        return None
    # Twee verschillende rauwe soorten: een aanbieding in vlees waar een ui in zit ("Pie runderstoof ui") is geen ui, een groenteburger geen broccoli.
    if kind in RAW_CATEGORIES and offer.get("category") in RAW_CATEGORIES and offer["category"] != kind:
        return None
    core, rest = parts or offer_parts(offer["title"])
    folded = ah_lists._fold(re.sub(r"\([^)]*\)", " ", str(ingredient)))
    main_stems = set(tokens(INGREDIENT_CONNECTORS.split(folded, 1)[0]) or tokens(ingredient))
    return match_offer(head, set(tokens(ingredient)), core, rest, main_stems)


# Bij gelijke dekking staat het eigen recept (Mealie) boven hetzelfde recept uit een winkel.
SOURCE_RANK = {"mealie": 0, "ah": 1, "jumbo": 2, "picnic": 3}


def _title_key(title: Any) -> str:
    """De titel van een recept zonder hoofdletters, accenten en leestekens, om hetzelfde recept uit twee bronnen te herkennen."""
    return re.sub(r"[^a-z0-9]+", " ", ah_lists._fold(title)).strip()


def merge_duplicates(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Hetzelfde recept uit meerdere bronnen (bijvoorbeeld een AH-recept dat je ook in Mealie hebt) wordt één kaart.

    De lijst moet al gesorteerd zijn, de beste eerst: die blijft staan en krijgt in `alsoIn` de andere bronnen. Twee recepten met dezelfde
    titel uit dezelfde bron blijven allebei staan: dat zijn twee recepten.
    """
    kept: dict[str, dict[str, Any]] = {}
    merged: list[dict[str, Any]] = []
    for item in results:
        key = _title_key(item["title"])
        first = kept.get(key)
        if first and item["source"] != first["source"] and item["source"] not in first["alsoIn"]:
            first["alsoIn"].append(item["source"])
            continue
        item["alsoIn"] = []
        merged.append(item)
        kept.setdefault(key, item)
    return merged


# Het adres van een Mealie-recept zoals het in de index staat: /recipe/<recept> (voor v2.65.1) of /g/<groep>/r/<recept>.
_MEALIE_RECIPE_PATH = re.compile(r"/(?:recipe|g/[^/]+/r)/([^/?#]+)/?$")


def _mealie_recipe_url(stored: str) -> str:
    """Het huidige adres van een Mealie-recept, ook als de index nog het oude adres bewaart (zonder opnieuw inlezen).

    Mealie wil /g/<groep>/r/<recept>; tot v2.65.0 bewaarden we /recipe/<recept>. Het adres wordt daarom bij het tonen opnieuw
    opgebouwd uit het recept in het bewaarde adres. Is Mealie niet gekoppeld of niet te bereiken, dan blijft het bewaarde adres staan.
    """
    match = _MEALIE_RECIPE_PATH.search(stored or "")
    if not match or not mealie.mealie_url():
        return stored
    try:
        return mealie.recipe_page_url(match.group(1))
    except Exception:  # noqa: BLE001 - een link die niet lukt mag de voorstellen niet blokkeren
        return stored


def _rejection_head(ingredient: Any) -> str:
    """Het hoofdwoord waaronder een gemelde koppeling wordt bewaard: dat van de eerste (Nederlandse) vorm van de ingrediëntregel."""
    text = str(ingredient or "").strip()
    head = head_of(offer_translate.variants(text)[0]) if text else None
    if not head:
        raise ValueError(tr("Dit ingrediënt kan niet worden gemeld", "This ingredient cannot be reported"))
    return head


def report_mismatch(store: Any, ingredient: Any, offer_id: Any, reason: Any = "", source: str = "app") -> dict[str, Any]:
    """Meld dat dit product niet bij dit ingrediënt hoort. De koppeling komt niet meer terug, ook niet bij andere recepten
    met hetzelfde hoofdwoord in deze winkel; `undo_mismatch` haalt de melding weer weg.

    `reason` is een korte toelichting (van een AI-assistent) en `source` wie meldde: `app` (jij, met de vlag) of `assistant`."""
    store = _valid_store(store)
    head = _rejection_head(ingredient)
    with closing(_connect()) as conn:
        offer = conn.execute("SELECT title FROM offers WHERE store = ? AND product_id = ?", (store, str(offer_id))).fetchone()
        if offer is None:
            raise ValueError(tr("Dit product staat niet meer in de aanbiedingen", "This product is no longer in the offers"))
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO offer_rejections (store, product_id, head, ingredient, title, reported_at, reason, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (store, str(offer_id), head, str(ingredient).strip(), offer["title"], _now(), " ".join(str(reason or "").split())[:300], source),
            )
    return {"store": store, "offerId": str(offer_id), "head": head}


def undo_mismatch(store: Any, ingredient: Any, offer_id: Any) -> dict[str, Any]:
    """Haalt een melding van `report_mismatch` weer weg."""
    store = _valid_store(store)
    head = _rejection_head(ingredient)
    with closing(_connect()) as conn, conn:
        conn.execute("DELETE FROM offer_rejections WHERE store = ? AND product_id = ? AND head = ?", (store, str(offer_id), head))
    return {"store": store, "offerId": str(offer_id), "head": head}


def list_mismatches(store: Any = None, limit: int = 100) -> dict[str, Any]:
    """De gemelde koppelingen, de nieuwste eerst: ingrediënt, product, wie meldde en waarom."""
    store = _valid_store(store) if store else None
    where, params = ("WHERE store = ?", (store,)) if store else ("", ())
    with closing(_connect()) as conn:
        rows = conn.execute(
            f"SELECT store, product_id, head, ingredient, title, reported_at, reason, source FROM offer_rejections {where} ORDER BY reported_at DESC LIMIT ?",
            (*params, max(1, min(500, int(limit)))),
        ).fetchall()
        total = conn.execute(f"SELECT COUNT(*) FROM offer_rejections {where}", params).fetchone()[0]
    return {
        "total": total,
        "reports": [
            {"store": r["store"], "offerId": r["product_id"], "ingredient": r["ingredient"], "mainWord": r["head"], "product": r["title"],
             "reportedAt": r["reported_at"], "reason": r["reason"] or "", "source": r["source"] or "app"}
            for r in rows
        ],
    }


def ingredients_hash(ingredients: list[str]) -> str:
    """Een vingerafdruk van de ingrediëntregels van een recept: verandert het recept, dan is het AI-resultaat voor dat recept verouderd."""
    return hashlib.sha1("\n".join(ingredients).encode("utf-8")).hexdigest()


def _ai_search_data(store: str) -> tuple[dict[tuple[str, str], str], dict[tuple[str, str], list[tuple[str, str]]]]:
    """Wat het zoeken door een AI voor deze winkel heeft opgeleverd: per recept de vingerafdruk van de ingrediënten waarmee gezocht is en
    de koppelingen (ingrediëntregel, product)."""
    with closing(_connect()) as conn:
        runs = conn.execute("SELECT source, recipe_id, ingredients_hash FROM ai_recipe_runs WHERE store = ?", (store,)).fetchall()
        rows = conn.execute(
            "SELECT source, recipe_id, ingredient, product_id FROM ai_recipe_matches WHERE store = ? ORDER BY line_index", (store,)
        ).fetchall()
    matches: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for row in rows:
        matches.setdefault((row["source"], row["recipe_id"]), []).append((row["ingredient"], row["product_id"]))
    return {(row["source"], row["recipe_id"]): row["ingredients_hash"] for row in runs}, matches


def _verdicts(store: str) -> dict[tuple[str, str], str]:
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT product_id, head, verdict FROM offer_verdicts WHERE store = ?", (store,)).fetchall()
    return {(row["product_id"], row["head"]): row["verdict"] for row in rows}


def _rejections(store: str) -> set[tuple[str, str]]:
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT product_id, head FROM offer_rejections WHERE store = ?", (store,)).fetchall()
    return {(row["product_id"], row["head"]) for row in rows}


def suggest(store: Any, limit: int = 30, course: Any = None, cap: int = 100) -> dict[str, Any]:
    """Recepten uit de index die zoveel mogelijk met de aanbiedingen van deze winkel te maken zijn, de beste eerst.

    Basisingrediënten (zout, olie, boter ...) tellen niet mee. De volgorde is: het grootste deel van de ingrediënten in de aanbieding,
    dan het grootste aantal, dan de grootste besparing. `course` beperkt de lijst tot één soort gerecht (zie `offer_courses`); de
    aantallen per soort in `courses` gelden voor alle voorstellen, zodat je ziet wat er bij een andere keuze zou staan.
    """
    course = str(course or "").strip().lower()
    if course in ("", "all"):
        course = ""
    elif course not in offer_courses.COURSES:
        raise ValueError(tr("Onbekende soort gerecht", "Unknown kind of dish"))
    offers = load(store)
    rejected = _rejections(offers["store"])
    verdicts = _verdicts(offers["store"])
    pair_judged: dict[tuple[str, str], bool] = {}
    ai_runs, ai_matches = _ai_search_data(offers["store"])
    ai_searched = 0
    offers_by_id = {str(offer["id"]): offer for offer in offers["offers"]}
    prepared = [(offer, *offer_parts(offer["title"])) for offer in offers["offers"]]
    with closing(_connect()) as conn:
        meta = conn.execute("SELECT synced_at, summary FROM offer_recipes_meta WHERE id = 1").fetchone()
        rows = conn.execute("SELECT source, recipe_id, title, image, url, ingredients, course FROM offer_recipes").fetchall()
    results: list[dict[str, Any]] = []
    for row in rows:
        ingredients = json.loads(row["ingredients"])
        # Een Engelse regel (veel Mealie-imports) vertalen we eerst naar het Nederlands, met een variant per Nederlandse naam.
        translated = {text: offer_translate.variants(text) for text in ingredients}
        heads = [(text, head_of(translated[text][0])) for text in ingredients]
        # Hetzelfde ingrediënt twee keer in een recept ("1 el paprika", "2 el paprika") telt één keer.
        unique: dict[str, str] = {}
        for text, head in heads:
            if head:
                unique.setdefault(head, text)
        heads = [(text, head) for head, text in unique.items()]
        if not heads:
            continue
        found: list[dict[str, Any]] = []
        pair_found: dict[tuple[str, str], bool] = {}
        ai_key = (row["source"], row["recipe_id"])
        if ai_runs.get(ai_key) == ingredients_hash(ingredients):
            # Een AI heeft dit recept doorzocht (zie ai_match.py): haar koppelingen vervangen de onze, voor zover het product nog in het aanbod
            # staat, niet gemeld is en de regel een hoofdwoord heeft. Een regel met hetzelfde hoofdwoord telt één keer, zoals hierboven.
            ai_searched += 1
            by_head = {head: text for text, head in heads}
            taken: set[str] = set()
            for line_text, product_id in ai_matches.get(ai_key, []):
                offer = offers_by_id.get(product_id)
                try:
                    line_head = _rejection_head(line_text)
                except ValueError:
                    continue
                text = by_head.get(line_head)
                if offer is None or text is None or product_id in taken or (product_id, line_head) in rejected:
                    continue
                taken.add(product_id)
                by_head.pop(line_head)
                found.append({"ingredient": text, "offer": offer, "ai": "search"})
                pair_found[(product_id, line_head)] = (product_id, line_head) in verdicts
        else:
            used: set[str] = set()
            for text, head in heads:
                options = [(line, head_of(line), ingredient_kind(line)) for line in translated[text]]
                options = [(line, option_head, kind) for line, option_head, kind in options if option_head]
                best: tuple[int, dict[str, Any]] | None = None
                for offer, core, rest in prepared:
                    if offer["id"] in used or (str(offer["id"]), head) in rejected:
                        continue
                    for line, option_head, kind in options:
                        extra = link_offer(line, offer, (core, rest), head=option_head, kind=kind)
                        if extra is not None and (best is None or extra < best[0]):
                            best = (extra, offer)
                if best:
                    used.add(best[1]["id"])
                    pair = (str(best[1]["id"]), head)
                    match = {"ingredient": text, "offer": best[1]}
                    if verdicts.get(pair) == "ok":
                        match["ai"] = "ok"  # een AI heeft deze koppeling goedgekeurd (zie ai_review.py)
                    found.append(match)
                    pair_found[pair] = pair in verdicts
        needed = 1 if len(heads) <= 3 else 2
        if len(found) < needed:
            continue
        pair_judged.update(pair_found)
        saving = sum(saving_of(item["offer"]) for item in found)
        matched_texts = {item["ingredient"] for item in found}
        results.append(
            {
                "source": row["source"],
                "id": row["recipe_id"],
                "title": row["title"],
                "image": row["image"] or "",
                "url": (_mealie_recipe_url(row["url"]) if row["source"] == "mealie" else row["url"]) or "",
                "course": row["course"] or offer_courses.OTHER,
                "ingredientCount": len(heads),
                "matchedCount": len(found),
                "matched": found,
                # De ingrediënten (zonder basis) die niet in de aanbieding zijn, om te zien wat er nog bij moet.
                "missing": [text for text, _ in heads if text not in matched_texts],
                "saving": round(saving, 2),
            }
        )
    results.sort(
        key=lambda item: (-item["matchedCount"] / item["ingredientCount"], -item["matchedCount"], -item["saving"], SOURCE_RANK.get(item["source"], 9), item["title"])
    )
    results = merge_duplicates(results)
    courses = offer_courses.summarize([item["course"] for item in results])
    shown = [item for item in results if not course or item["course"] == course]
    return {
        "store": offers["store"],
        "recipes": shown[: max(1, min(cap, int(limit)))],
        "total": len(shown),
        "courses": courses,
        "course": course or "all",
        "offersSyncedAt": offers["syncedAt"],
        # Hoeveel van de unieke koppelingen in de voorstellen een AI al beoordeeld heeft (zie ai_review.py).
        "ai": {
            "judged": sum(pair_judged.values()), "unjudged": len(pair_judged) - sum(pair_judged.values()),
            "searched": ai_searched, "recipes": len(rows),
        },
        "indexedAt": meta["synced_at"] if meta else None,
        "sources": json.loads(meta["summary"]) if meta else {},
    }


def unique_pairs(store: Any) -> list[dict[str, Any]]:
    """De koppelingen van alle receptvoorstellen als lijst van unieke paren (hoofdwoord van het ingrediënt + product), het vaakst gebruikte eerst.

    Hetzelfde paar komt in veel recepten voor en één oordeel (`report_mismatch`) geldt voor al die recepten, dus elk paar staat één keer in
    de lijst, met het aantal recepten, een paar voorbeelden en het hoofdwoord (`head`). Al gemelde paren staan er niet meer in."""
    suggestions = suggest(store, limit=10**6, cap=10**6)
    pairs: dict[tuple[str, str], dict[str, Any]] = {}
    for recipe in suggestions["recipes"]:
        for item in recipe["matched"]:
            try:
                head = _rejection_head(item["ingredient"])
            except ValueError:
                continue
            offer = item["offer"]
            pair = pairs.setdefault(
                (str(offer["id"]), head),
                {
                    "ingredient": item["ingredient"],
                    "head": head,
                    "offerId": str(offer["id"]),
                    "product": offer["title"],
                    "brand": offer.get("brand") or "",
                    "unitSize": offer.get("unitSize") or "",
                    "category": offer.get("category") or "",
                    "recipes": 0,
                    "exampleRecipes": [],
                },
            )
            pair["recipes"] += 1
            if len(pair["exampleRecipes"]) < 3:
                pair["exampleRecipes"].append(recipe["title"])
    return sorted(pairs.values(), key=lambda pair: (-pair["recipes"], pair["product"], pair["ingredient"]))


def review_pairs(store: Any, limit: int = 40, offset: int = 0) -> dict[str, Any]:
    """`unique_pairs` voor de MCP-tool `review_offer_matches`: een stuk van de lijst, zonder het interne hoofdwoord."""
    ordered = unique_pairs(store)
    start = max(0, int(offset))
    page = [{key: value for key, value in pair.items() if key != "head"} for pair in ordered[start : start + max(1, min(100, int(limit)))]]
    return {"store": _valid_store(store), "total": len(ordered), "offset": start, "pairs": page}


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
            "course": offer_courses.course_of(recipe.get("recipeCategory"), recipe.get("tags")),
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
                "url": jumbo_recipes.source_url(recipe), "ingredients": lines,
                "course": offer_courses.course_of(recipe.get("course"), card.get("course"))}

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
                "url": picnic_recipes.source_url(recipe["id"]), "ingredients": lines,
                "course": offer_courses.course_of(card.get("tags"))}

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        return [item for item in pool.map(one, cards[:OTHER_RECIPE_LIMIT]) if item]


def _indexed(source: str) -> dict[str, tuple[list[str], str | None]]:
    """Wat er van een bron al in de index staat: per recept de ingrediënten en de soort (None als die nog niet is ingelezen)."""
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT recipe_id, ingredients, course FROM offer_recipes WHERE source = ?", (source,)).fetchall()
    return {row["recipe_id"]: (json.loads(row["ingredients"]), row["course"]) for row in rows}


def _collect_ah() -> list[dict[str, Any]]:
    saved = ah_recipes.load()
    if not saved or not saved["recipes"]:
        raise LookupError(tr("geen bewaarde recepten (haal ze eerst op bij Winkels > Albert Heijn > Recepten)", "no saved recipes (fetch them first under Stores > Albert Heijn > Recipes)"))
    known = _indexed("ah")
    fetched = 0
    items: list[dict[str, Any]] = []
    for recipe in saved["recipes"]:
        recipe_id = str(recipe["id"])
        lines, course = known.get(recipe_id, (None, None))
        # Een recept dat al in de index staat maar nog geen soort heeft (ingelezen vóór de soorten) halen we opnieuw op.
        if lines is None or course is None:
            if fetched >= AH_NEW_RECIPES_PER_SYNC:
                if lines is None:
                    continue
            else:
                fetched += 1
                try:
                    jsonld = ah.get_recipe_jsonld(int(recipe_id))
                    lines = jsonld.get("recipeIngredient") or lines or []
                    course = offer_courses.course_of(jsonld.get("recipeCategory"))
                except Exception:  # noqa: BLE001
                    if lines is None:
                        continue
        if lines:
            items.append({"id": recipe_id, "title": recipe["title"], "image": recipe.get("image") or "",
                          "url": recipe.get("url") or "", "ingredients": list(lines), "course": course})
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
                "INSERT OR REPLACE INTO offer_recipes (source, recipe_id, title, image, url, ingredients, course) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(source, item["id"], item["title"], item["image"], item["url"], json.dumps(item["ingredients"]), item.get("course"))
                 for item in items],
            )
        conn.execute(
            """
            INSERT INTO offer_recipes_meta (id, synced_at, summary) VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET synced_at = excluded.synced_at, summary = excluded.summary
            """,
            (_now(), json.dumps(summary)),
        )
    return {"indexedAt": _now(), "sources": summary, "total": sum(len(items) for items in collected.values())}
