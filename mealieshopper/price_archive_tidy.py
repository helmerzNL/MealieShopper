"""Folderregels uit een prijsarchief opschonen voor ze in de Prijsgeschiedenis komen (zie price_archive.py).

Een folderarchief bevat veel regels die als los product in de Prijsgeschiedenis vreemd staan: combi-aanbiedingen voor meer
producten tegelijk ("A of B", "alle ...", "diverse soorten"), en folderproducten zonder gewicht, inhoud of aantal, waarin
prijzen van verschillende verpakkingen door elkaar lopen. Bij het importeren gebeurt daarom met elke folderregel (een
aanbieding met een product-id `folder:…`, dus nog bij geen echt product):

1. **Koppelen aan je eigen producten, alleen exact.** De app zoekt de regel op in wat hij zelf al kent: je aanbiedingen,
   bijgehouden prijzen en bestellingen (geen archiefregels), met dezelfde naam en dezelfde verpakking. Op woorden koppelt
   de app niet, en een koppeling op woorden die het archief zelf al maakte, is onzeker (verpakking of variant staat niet
   vast) en komt er niet in.
2. **Een folderregel die nergens bij hoort, komt er niet in**, en een combi-aanbieding ("A of B", "combineren mogelijk",
   "naar keuze") nooit.
3. **Alleen de actie als tekst** ("2 voor: 5.00, 2 schalen"), zonder "Folder |", "bron: …" en "koppeling onzeker".

Een prijs uit een archief zonder product (`folder:…`, de Picnic- en Crisp-prijzen) komt er net zo alleen in als hij exact bij
een product van jou hoort (`Tidy.price`).

Met `keep_unlinked` (voor een eigen archief) blijven ongekoppelde regels en prijzen als eigen product staan. Een onzekere
koppeling komt er dan ook in, maar los van het product waar het archief hem aan hing: als eigen folderproduct met de naam uit
de folder (`detach`). Zo staat er geen "AH Goudse oude kaas 48" uit 2009 bij "AH Oude kaas L". Een ongekoppelde folderregel
zonder gewicht, inhoud of aantal krijgt geen prijs.

Een combi-aanbieding blijft met `keep_unlinked` ook, als eigen folderproduct en nooit bij een product van jou: "Alle Dr. Oetker
Casa di Mama pizza's" is een actie voor de hele lijn, en dat is in een folderarchief het grootste deel (bij AH twee op de drie
regels). De Prijsgeschiedenis toont zo'n lijnactie bij elk product waarvan de naam alle woorden van de actie bevat
(line_offers.py).

De koppeling en het lezen van verpakkingen komen uit `import_archive.py` van wolph, in het pakket met zijn folderarchief
(`fold`, `size`, `match_product` en `catalogue_from`).
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import unicodedata
from decimal import Decimal
from functools import lru_cache
from typing import Any

# Wat er uit een prijsarchief kwam: een aanbieding met een product-id `folder:…` of het adres van een folderbron
# (supermarktaanbiedingen.com, voordeelmuis.nl), en een prijs met een openbare bron. Wat de app zelf ophaalt, heeft dat nooit.
# Een leeg adres telt niet (COALESCE: anders maakt NULL de vergelijking onbepaald). Gebruikt door **Vervangen** en hieronder.
ARCHIVE_OFFERS_SQL = (
    "(product_id LIKE 'folder:%' OR COALESCE(url, '') LIKE 'https://www.supermarktaanbiedingen.com/folders/%' "
    "OR COALESCE(url, '') LIKE 'https://www.voordeelmuis.nl/cgi-bin/v.cgi?%')"
)
ARCHIVE_PRICES_SQL = "(source IN ('public_press_release', 'public_observed_unknown', 'archive_search'))"

GROUP = re.compile(r"\b(?:alle|of|en|diverse|varianten|combineren|bijv|bijvoorbeeld|naar\s+keuze)\b", re.I)
ARCHIVE_URLS = ("https://www.supermarktaanbiedingen.com/folders/", "https://www.voordeelmuis.nl/cgi-bin/v.cgi?")


def _clean(value: str) -> str:
    return " ".join(value.split())


@lru_cache(maxsize=150_000)
def fold(value: str) -> str:
    text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    return _clean(re.sub(r"[^a-z0-9]+", " ", text))


@lru_cache(maxsize=100_000)
def size(text: str) -> tuple[str, Decimal] | None:
    """De verpakking als (soort, hoeveelheid) als de tekst er precies één noemt, anders None."""
    if re.search(r"\d+(?:[.,]\d+)?\s*(?:-|/|tot|t/m|of|en)\s*\d+(?:[.,]\d+)?\s*(?:gram|gr|g|kg|kilo|ml|cl|liter|l)\b", text, re.I):
        return None
    if re.search(
        r"\d\s*[x×]\s*\d|\b[2-9]\d*\s*(?:flesjes?|flessen|blikken?|blikjes?|pakken?|zakken?|dozen?|cups?|kuipjes?)\b|\b\d+\s*-?\s*pack\b", text, re.I
    ):
        return None
    if fold(text) == "per stuk":
        return "count", Decimal(1)
    pattern = r"(?<![\d.,])(\d+(?:[.,]\d+)?)\s*(kilogram|kilograms|kilo|kg|gram|gr|g|milliliter|ml|cl|liter|litre|l|stuks|stuk)\b"
    values = set()
    for match in re.finditer(pattern, text.lower()):
        unit = match[2]
        value = Decimal(match[1].replace(",", "."))
        kind = "count" if unit in {"stuks", "stuk"} else "volume" if unit in {"milliliter", "ml", "cl", "liter", "litre", "l"} else "mass"
        if unit in {"kilogram", "kilograms", "kilo", "kg", "liter", "litre", "l"}:
            value *= 1000
        elif unit == "cl":
            value *= 10
        values.add((kind, value))
    return next(iter(values)) if len(values) == 1 else None


def source_title(row: dict[str, Any]) -> str:
    """De naam uit de bron: `source_title` als het archief die meegeeft, anders de titel zonder "Folder: " of "Archief: "."""
    title = str(row.get("source_title") or row.get("title") or "")
    for prefix in ("Folder: ", "Archief: "):
        if title.startswith(prefix):
            return title[len(prefix) :]
    return title


def _texts(row: dict[str, Any]) -> str:
    return f"{source_title(row)} {row.get('description') or ''}"


def is_group(row: dict[str, Any]) -> bool:
    """Een aanbieding voor meer producten tegelijk ("A of B", "alle ...", "diverse soorten")."""
    return bool(GROUP.search(_texts(row)))


def known_pack(row: dict[str, Any]) -> bool:
    """Noemt de folder een gewicht, inhoud of aantal (één, niet "2 of 4 stuks")?

    Zonder `description` telt ook de actietekst: een regel uit een export van de app (zoals die voor data.mealieshopper.nl)
    heeft de foldertekst alleen nog als korte actietekst, en daar staat de verpakking dan in."""
    if row.get("unit_size") or size(_texts(row)) is not None:
        return True
    label = row.get("promo_label")
    return not row.get("description") and isinstance(label, str) and size(str(short_label(label))) is not None


def is_archive_product(row: dict[str, Any]) -> bool:
    return isinstance(row.get("product_id"), str) and row["product_id"].startswith("folder:")


def is_archive_line(row: dict[str, Any]) -> bool:
    """Een folderregel: nog bij geen product (`folder:…`), of door het archief al gekoppeld (het adres van een folderbron)."""
    return is_archive_product(row) or (isinstance(row.get("url"), str) and row["url"].startswith(ARCHIVE_URLS))


def is_uncertain(row: dict[str, Any]) -> bool:
    """Een koppeling op woorden (al gemaakt in het archief): verpakking of variant staat niet vast."""
    return row.get("match_kind") == "approximate" or "koppeling onzeker" in str(row.get("promo_label") or "")


# Het product van een losgemaakte onzekere koppeling: per winkel en foldernaam een, zodat elke keer dat dezelfde regel in de
# folder stond bij hetzelfde product komt. Het voorvoegsel houdt hem apart van de id's die het archief zelf maakt.
DETACHED_PREFIX = "folder:"
FOLDER_BRAND = "Folderarchief"


def detach(row: dict[str, Any]) -> dict[str, Any] | None:
    """Een onzekere koppeling als eigen folderproduct: het id komt uit de naam in de folder (`source_title`), de titel wordt
    "Folder: <naam>", zonder merk van de winkel, verpakking, categorie of plaatje van het product waar het archief hem aan hing
    (die horen bij dat product, niet bij de folderregel). None zonder naam uit de folder: de titel is dan die van het product."""
    title = str(row.get("source_title") or "").strip()
    if not title:
        return None
    key = f"detached|{row.get('store')}|{fold(title)}"
    product_id = DETACHED_PREFIX + hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
    return {
        **row,
        "product_id": product_id,
        "title": f"Folder: {title}",
        "brand": FOLDER_BRAND,
        "unit_size": None,
        "category": None,
        "image": "",
        "match_kind": None,
    }


def short_label(label: Any) -> Any:
    """Alleen de actie uit een foldertekst: "Folder | 2 voor: 5.00, 2 schalen | bron: … | koppeling onzeker: …" wordt
    "2 voor: 5.00, 2 schalen". De dubbele punt na "voor" blijft: zo leest de app er geen actie in (deals.parse_mechanic) en
    deelt hij de prijs, die al per stuk is, niet nog een keer. Een tekst die niet met "Folder | " begint, blijft zoals hij is."""
    if not isinstance(label, str) or not label.startswith("Folder | "):
        return label
    parts = label.split(" | ")
    text = parts[1].strip() if len(parts) > 1 else ""
    # De bronsite plakt stukken aan elkaar ("2 VOOR: 1.00Los", "voordeelFles", "FrankrijkVOOR") en schrijft acties in hoofdletters.
    text = re.sub(r"(?<=[a-z0-9.,])(?=[A-Z][a-z])", " ", text)
    text = re.sub(r"(?<=[a-z])(?=[A-Z]{2,})", " ", text)
    text = re.sub(r"\b[A-Z]{3,}\b", lambda m: m[0].lower(), text)
    return " ".join(text.split())


SOURCE_PRICES = re.compile(r"\bbron: (?:oud=(?P<old>[^,|]+?)\s*,\s*)?actie=(?P<now>[^,|]+?)\s*(?:\||$)")


def line_label(label: Any) -> Any:
    """De actietekst van een lijnactie (`short_label`), met de prijzen uit de bron erachter: het archief geeft een lijnactie
    geen prijs ("prijs onzeker"), maar de folder noemde hem wel. "Folder | 2 dozen | bron: oud=2 stuks 5.38, actie=3.50 | ..."
    wordt "2 dozen, actie 3.50, was 2 stuks 5.38"."""
    short = short_label(label)
    found = SOURCE_PRICES.search(label) if isinstance(label, str) and label.startswith("Folder | ") else None
    if not found:
        return short
    text = str(short or "").strip()
    now, old = found["now"].strip(), (found["old"] or "").strip()
    # Een onbekende waarde ("?") zegt niets, en een prijs die de actietekst al noemt ("2 voor: 5.99") hoeft er niet nog eens bij.
    parts = [text, f"actie {now}" if now not in ("", "?") and now not in text else "", f"was {old}" if old not in ("", "?") else ""]
    return ", ".join(part for part in parts if part)


# Gelijk aan `offers.STORES` (een test controleert dat); deze module importeert niets van de app, zodat hij in een thread kan draaien.
STORES = ("ah", "jumbo", "picnic", "crisp", "lidl", "aldi")
TEXT_FIELDS = ("source_title", "title", "description", "unit_size", "promo_label", "product_id")


def _plain(row: dict[str, Any]) -> bool:
    """Zijn de velden die het opschonen leest tekst (of leeg)? Anders laat het de regel aan de controle over."""
    return all(row.get(field) is None or isinstance(row.get(field), str) for field in TEXT_FIELDS)


class Linker:
    """Koppelt folderregels aan de producten die de app zelf kent, per winkel (`catalogue_from` van wolph)."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.catalogues: dict[str, dict[str, list[dict[str, str]]]] = {}

    def catalogue(self, store: str) -> dict[str, list[dict[str, str]]]:
        """Jouw producten van een winkel, per opgevouwen naam, zonder regels uit een archief."""
        if store in self.catalogues:
            return self.catalogues[store]
        result: dict[str, list[dict[str, str]]] = {}
        seen = set()
        tables = {row[0] for row in self.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        queries = [
            (f"SELECT DISTINCT product_id, title, unit_size FROM offer_history WHERE store = ? AND NOT {ARCHIVE_OFFERS_SQL}", "offer_history"),
            (
                f"SELECT DISTINCT product_id, title, unit_size FROM price_observations WHERE store = ? AND NOT {ARCHIVE_PRICES_SQL}",
                "price_observations",
            ),
            (f'SELECT DISTINCT product_id, title, unit_size FROM "{store}_order_items"', f"{store}_order_items"),
        ]
        for sql, table in queries:
            if table not in tables:
                continue
            params = (store,) if "store = ?" in sql else ()
            for product_id, title, unit_size in self.conn.execute(sql, params):
                if product_id is None or not str(product_id).strip() or str(product_id).startswith("folder:"):
                    continue
                product = {"product_id": str(product_id), "title": title or "", "unit_size": unit_size or ""}
                key = (product["product_id"], product["title"], product["unit_size"])
                if key in seen:
                    continue
                seen.add(key)
                result.setdefault(fold(product["title"]), []).append(product)
        self.catalogues[store] = result
        return result

    def link(self, row: dict[str, Any]) -> bool:
        """True als de regel exact bij een product hoort (dezelfde naam en verpakking, `match_product`); de regel is dan bijgewerkt."""
        title = source_title(row)
        if not title:
            return False
        exact = self._exact(row, title, self.catalogue(str(row.get("store") or "")).get(fold(title), []))
        if exact:
            row.update(product_id=exact, title=title, brand=None, match_kind="exact")
        return bool(exact)

    @staticmethod
    def _exact(row: dict[str, Any], title: str, products: list[dict[str, str]]) -> str | None:
        """`match_product`: dezelfde naam en dezelfde verpakking, bij precies één product, en nooit bij een combi.

        Dezelfde verpakking: dezelfde hoeveelheid (`size`), of letterlijk dezelfde tekst, zoals bij een multipack ("6 x 300 ml"),
        waar `size` geen hoeveelheid uit haalt.
        """
        if is_group(row) or row.get("price") is None:
            return None
        unit = row.get("unit_size") or ""
        pack = size(unit)
        if not pack and not fold(unit):
            return None

        def same_pack(product: dict[str, str]) -> bool:
            other = product["unit_size"] or ""
            return size(other) == pack if pack else fold(other) == fold(unit)

        matches = {p["product_id"] for p in products if fold(p["title"]) == fold(title) and same_pack(p)}
        return next(iter(matches)) if len(matches) == 1 else None


class Tidy:
    """Wat er met een folderregel uit een archief gebeurt voor ze in de database komt.

    Standaard: alleen regels die exact (dezelfde naam en verpakking) bij een product van jou horen, of die het archief zelf al
    exact koppelde; nooit een combi-aanbieding of een onzekere koppeling; en met alleen de actie als tekst. Met `keep_unlinked`
    (voor een eigen archief) blijven ongekoppelde regels en onzekere koppelingen staan, combi's niet, en een ongekoppelde regel
    zonder gewicht, inhoud of aantal krijgt geen prijs.
    """

    def __init__(self, conn: sqlite3.Connection, keep_unlinked: bool = False):
        self.linker = Linker(conn)
        self.keep_unlinked = keep_unlinked
        # exact: de app koppelde de regel aan een product van jou; linkedByArchive: het archief koppelde hem al aan een product.
        # pricesExact en pricesUnlinked: hetzelfde voor prijzen uit een archief (Picnic, Crisp).
        # detached: een onzekere koppeling, met keep_unlinked los van het product gezet (`detach`).
        # lineOffers: een combi-aanbieding, met keep_unlinked bewaard als eigen folderproduct (`_line_offer`).
        self.stats = {
            "exact": 0,
            "linkedByArchive": 0,
            "unlinked": 0,
            "uncertain": 0,
            "detached": 0,
            "groups": 0,
            "lineOffers": 0,
            "unknownPack": 0,
            "pricesExact": 0,
            "pricesUnlinked": 0,
        }

    def _line_offer(self, row: dict[str, Any]) -> dict[str, Any] | None:
        """Een combi-aanbieding als eigen folderproduct, of None (zonder `keep_unlinked`, of zonder naam uit de folder).

        Nooit bij een product van jou, ook niet als het archief hem al aan een product hing: die koppeling is dan losgemaakt
        (`detach`). De prijzen van het archief blijven, ook zonder één gewicht, inhoud of aantal: bij een lijnactie is de prijs per
        stuk wat telt, en de Prijsgeschiedenis maakt hem per product kloppend (`line_offers.per_piece`)."""
        if not self.keep_unlinked:
            return None
        kept = dict(row) if is_archive_product(row) else detach(row)
        if kept is None:
            return None
        kept["promo_label"] = line_label(kept.get("promo_label"))
        return kept

    def offer(self, row: dict[str, Any]) -> dict[str, Any] | None:
        """De regel zoals hij geïmporteerd wordt, of None als hij er niet in hoort.

        De regel is dan al gecontroleerd (`normalise_offer` in price_archive.py): een kapotte regel weigert het hele bestand,
        ook als hij hier zou wegvallen. Een regel die niet uit een folderarchief komt, blijft ongemoeid.
        """
        if not is_archive_line(row) or row.get("store") not in STORES or not _plain(row):
            return row
        if is_group(row):
            kept = self._line_offer(row)
            if kept is None:
                self.stats["groups"] += 1
                return None
            self.stats["lineOffers"] += 1
            return kept
        if not is_archive_product(row) and is_uncertain(row):  # het archief koppelde de regel op woorden aan een product
            detached = detach(row) if self.keep_unlinked else None
            if detached is None:
                self.stats["uncertain"] += 1
                return None
            self.stats["detached"] += 1
            row = detached  # verder als folderregel: exact koppelen aan een product van jou, of als eigen product
        if not is_archive_product(row):  # het archief koppelde de regel al exact aan een product
            self.stats["linkedByArchive"] += 1
        elif self.linker.link(row):
            self.stats["exact"] += 1
        elif not self.keep_unlinked:
            self.stats["unlinked"] += 1
            return None
        elif not known_pack(row) and (row.get("price") is not None or row.get("price_before") is not None):
            row["price"] = row["price_before"] = None
            self.stats["unknownPack"] += 1
        row["promo_label"] = short_label(row.get("promo_label"))
        return row

    def price(self, row: dict[str, Any]) -> dict[str, Any] | None:
        """Een prijs uit een archief zonder product (`folder:…`, zoals de Picnic- en Crisp-prijzen), of None.

        Net als een folderregel: alleen als hij exact (dezelfde naam en verpakking) bij een product van jou hoort, en dan bij dat
        product. Met `keep_unlinked` blijft een prijs die nergens bij hoort als eigen product staan ("Archief: …"). De bron blijft
        zoals hij is, zodat **Vervangen** hem herkent. Een prijs met een echt product-id blijft ongemoeid.
        """
        if not is_archive_product(row) or row.get("store") not in STORES or not _plain(row):
            return row
        if self.linker.link(row):
            self.stats["pricesExact"] += 1
            return row
        if self.keep_unlinked:
            return row
        self.stats["pricesUnlinked"] += 1
        return None
