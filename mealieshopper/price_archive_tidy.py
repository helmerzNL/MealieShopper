"""Folderregels uit een prijsarchief opschonen voor ze in de Prijsgeschiedenis komen (zie price_archive.py).

Een folderarchief bevat veel regels die als los product in de Prijsgeschiedenis vreemd staan: combi-aanbiedingen voor meer
producten tegelijk ("A of B", "alle ...", "diverse soorten"), en folderproducten zonder gewicht, inhoud of aantal, waarin
prijzen van verschillende verpakkingen door elkaar lopen. Bij het importeren gebeurt daarom met elke folderregel (een
aanbieding met een product-id `folder:…`, dus nog bij geen echt product):

1. **Koppelen aan je eigen producten.** De app zoekt de regel op in wat hij zelf al kent: je aanbiedingen, bijgehouden
   prijzen en bestellingen (geen archiefregels). Eerst exact (dezelfde naam en verpakking), anders op woorden; is de
   verpakking of variant dan niet zeker, dan krijgt de regel geen prijs, maar telt hij wel als keer in de aanbieding.
2. **Een combi-aanbieding die bij geen product hoort, komt er niet in.**
3. **Een folderregel zonder bekende verpakking krijgt geen prijs.**

De koppeling en het lezen van verpakkingen komen uit `import_archive.py` van wolph, in het pakket met zijn folderarchief
(`fold`, `size`, `words`, `match_product`, `link_approximate` en `catalogue_from`, met dezelfde drempels).
"""
from __future__ import annotations

import re
import sqlite3
import unicodedata
from decimal import Decimal
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Any

# Wat er uit een prijsarchief kwam: een aanbieding met een product-id `folder:…` of het adres van een folderbron
# (supermarktaanbiedingen.com, voordeelmuis.nl), en een prijs met een openbare bron. Wat de app zelf ophaalt, heeft dat nooit.
# Een leeg adres telt niet (COALESCE: anders maakt NULL de vergelijking onbepaald). Gebruikt door **Vervangen** en hieronder.
ARCHIVE_OFFERS_SQL = (
    "(product_id LIKE 'folder:%' OR COALESCE(url, '') LIKE 'https://www.supermarktaanbiedingen.com/folders/%' "
    "OR COALESCE(url, '') LIKE 'https://www.voordeelmuis.nl/cgi-bin/v.cgi?%')"
)
ARCHIVE_PRICES_SQL = "(source IN ('public_press_release', 'public_observed_unknown'))"

GROUP = re.compile(r"\b(?:alle|of|en|diverse|varianten|combineren|bijv|bijvoorbeeld)\b", re.I)
NOISE = frozenset("ah albert heijn jumbo picnic crisp alle diverse varianten van de het een en of per stuk stuks gram kg ml liter bijv voor bonus verse vers".split())
PACKAGING_WORDS = frozenset({"los", "pak", "zak", "bak", "schaal", "fles", "pot"})
UNKNOWN_PACK = " | verpakking onbekend, prijs weggelaten"


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
    if re.search(r"\d\s*[x×]\s*\d|\b[2-9]\d*\s*(?:flesjes?|flessen|blikken?|blikjes?|pakken?|zakken?|dozen?|cups?|kuipjes?)\b|\b\d+\s*-?\s*pack\b", text, re.I):
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


@lru_cache(maxsize=100_000)
def words(title: str) -> frozenset[str]:
    return frozenset(w for w in fold(title).split() if w not in NOISE and not w.isdigit() and len(w) > 1)


def display_text(text: str) -> str:
    """Leesbare voorwaarden, zonder dat de app er een actie in leest (zoals wolph de folderteksten schrijft)."""
    result = re.sub(r"voor(?=\s*(?:€|eur(?:o)?)?\s*\d)", lambda m: m[0] + ":", text, flags=re.I)
    result = re.sub(r"\bnu(?=\s*(?:€|eur(?:o)?)?\s*\d)", lambda m: m[0] + ":", result, flags=re.I)
    result = re.sub(r"gratis", "kosteloos", result, flags=re.I)
    result = re.sub(r"halve\s*prijs", "halve-prijs", result, flags=re.I)
    result = re.sub(r"korting", "voordeel", result, flags=re.I)
    return result.replace("%", " procent")


def source_title(row: dict[str, Any]) -> str:
    """De naam uit de folder: `source_title` als het archief die meegeeft, anders de titel zonder "Folder: "."""
    title = row.get("source_title") or row.get("title") or ""
    title = str(title)
    return title[len("Folder: "):] if title.startswith("Folder: ") else title


def _texts(row: dict[str, Any]) -> str:
    return f"{source_title(row)} {row.get('description') or ''}"


def is_group(row: dict[str, Any]) -> bool:
    """Een aanbieding voor meer producten tegelijk ("A of B", "alle ...", "diverse soorten")."""
    return bool(GROUP.search(_texts(row)))


def known_pack(row: dict[str, Any]) -> bool:
    """Noemt de folder een gewicht, inhoud of aantal (één, niet "2 of 4 stuks")?"""
    return bool(row.get("unit_size")) or size(_texts(row)) is not None


def is_archive_product(row: dict[str, Any]) -> bool:
    return isinstance(row.get("product_id"), str) and row["product_id"].startswith("folder:")


STORES = ("ah", "jumbo", "picnic", "crisp")
TEXT_FIELDS = ("source_title", "title", "description", "unit_size", "promo_label", "product_id")


def _plain(row: dict[str, Any]) -> bool:
    """Zijn de velden die het opschonen leest tekst (of leeg)? Anders laat het de regel aan de controle over."""
    return all(row.get(field) is None or isinstance(row.get(field), str) for field in TEXT_FIELDS)


class Linker:
    """Koppelt folderregels aan de producten die de app zelf kent, per winkel (`catalogue_from` van wolph)."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.catalogues: dict[str, dict[str, list[dict[str, str]]]] = {}
        self.indexes: dict[str, dict[str, set[str]]] = {}
        self.candidates: dict[tuple[str, str], list[dict[str, str]]] = {}

    def catalogue(self, store: str) -> dict[str, list[dict[str, str]]]:
        """Jouw producten van een winkel, per opgevouwen naam, zonder regels uit een archief."""
        if store in self.catalogues:
            return self.catalogues[store]
        result: dict[str, list[dict[str, str]]] = {}
        seen = set()
        tables = {row[0] for row in self.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        queries = [
            (f"SELECT DISTINCT product_id, title, unit_size FROM offer_history WHERE store = ? AND NOT {ARCHIVE_OFFERS_SQL}", "offer_history"),
            (f"SELECT DISTINCT product_id, title, unit_size FROM price_observations WHERE store = ? AND NOT {ARCHIVE_PRICES_SQL}", "price_observations"),
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
        index: dict[str, set[str]] = {}
        for title in result:
            for word in words(title):
                index.setdefault(word, set()).add(title)
        self.catalogues[store], self.indexes[store] = result, index
        return result

    def link(self, row: dict[str, Any]) -> str | None:
        """'exact' of 'approximate' als de regel nu bij een product hoort (de regel is dan bijgewerkt), anders None."""
        store = str(row.get("store") or "")
        title = source_title(row)
        if not title:
            return None
        catalogue = self.catalogue(store)
        exact = self._exact(row, title, catalogue.get(fold(title), []))
        if exact:
            row.update(product_id=exact, title=title, brand=None, match_kind="exact")
            return "exact"
        key = (store, fold(title))
        if key not in self.candidates:
            names = set().union(*(self.indexes[store].get(w, set()) for w in words(key[1])))
            self.candidates[key] = [product for name in sorted(names) for product in catalogue[name]]
        return "approximate" if self._approximate(row, title, self.candidates[key]) else None

    @staticmethod
    def _exact(row: dict[str, Any], title: str, products: list[dict[str, str]]) -> str | None:
        """`match_product`: dezelfde naam en dezelfde verpakking, bij precies één product, en nooit bij een combi."""
        if is_group(row):
            return None
        pack = size(row.get("unit_size") or "")
        if not pack or row.get("price") is None:
            return None
        matches = {p["product_id"] for p in products if fold(p["title"]) == fold(title) and size(p["unit_size"] or "") == pack}
        return next(iter(matches)) if len(matches) == 1 else None

    @staticmethod
    def _approximate(row: dict[str, Any], title: str, products: list[dict[str, str]]) -> bool:
        """`link_approximate`: op woorden, alleen bij een duidelijke winnaar; zonder prijs als verpakking of variant onzeker is."""
        source = fold(title)
        source_words = words(source)
        if not source_words:
            return False
        pack = size(row.get("unit_size") or "")
        house = str(row.get("store") or "")
        ranked: dict[str, tuple[float, dict[str, str]]] = {}
        for product in products:
            target = fold(product["title"])
            if source.startswith(house + " ") != target.startswith(house + " "):
                continue
            target_words = words(target)
            common = len(source_words & target_words)
            if not common or not target_words:
                continue
            overlap = .4 * common / len(source_words) + .4 * common / len(target_words)
            if overlap < .48:
                continue
            score = overlap + .2 * SequenceMatcher(None, source, target).ratio()
            if pack and size(product["unit_size"] or "") == pack:
                score += .1
            pid = product["product_id"]
            if pid not in ranked or score > ranked[pid][0]:
                ranked[pid] = (score, product)
        ordered = sorted(ranked.values(), key=lambda pair: pair[0], reverse=True)
        if not ordered or ordered[0][0] < .78 or (len(ordered) > 1 and ordered[0][0] - ordered[1][0] < .06):
            return False
        score, chosen = ordered[0]
        same_pack = pack is not None and size(chosen["unit_size"] or "") == pack
        row.update(product_id=chosen["product_id"], title=chosen["title"], brand=None, match_kind="approximate",
                   match_score=round(min(score, 1.0), 3))
        row["promo_label"] = (row.get("promo_label") or "") + " | koppeling onzeker: " + display_text(title)
        if not same_pack or is_group(row) or words(title) - PACKAGING_WORDS != words(chosen["title"]) - PACKAGING_WORDS:
            row.update(price=None, price_before=None)
            row["promo_label"] += " | verpakking of variant onzeker"
        return True


class Tidy:
    """Wat er met een aanbieding uit een archief gebeurt voor ze wordt gecontroleerd: koppelen, combi's weg, onzekere prijzen leeg."""

    def __init__(self, conn: sqlite3.Connection):
        self.linker = Linker(conn)
        self.stats = {"exact": 0, "approximate": 0, "groups": 0, "unknownPack": 0}

    def offer(self, row: dict[str, Any]) -> dict[str, Any] | None:
        """De regel zoals hij geïmporteerd wordt, of None als hij er niet in hoort.

        Dit draait vóór de controle van de regel (`normalise_offer`): een regel die we niet kunnen lezen (een onbekende winkel,
        een getal waar tekst hoort) laten we ongemoeid, zodat de controle hem met een duidelijke reden weigert.
        """
        if not is_archive_product(row) or row.get("store") not in STORES or not _plain(row):
            return row
        linked = self.linker.link(row)
        if linked:
            self.stats[linked] += 1
            return row
        if is_group(row):
            self.stats["groups"] += 1
            return None
        if not known_pack(row) and (row.get("price") is not None or row.get("price_before") is not None):
            row["price"] = row["price_before"] = None
            row["promo_label"] = (row.get("promo_label") or "") + UNKNOWN_PACK
            self.stats["unknownPack"] += 1
        return row
