"""Prijsarchief exporteren (Instellingen > Systeem): alle aanbiedingen en prijzen als ZIP, een bestand per winkel en maand.

Bedoeld om de gegevens te delen, bijvoorbeeld in een GitHub-repo: pak de ZIP uit in de repo en commit. Een andere MealieShopper
importeert het weer met **Prijsarchief importeren**, ook als de ZIP van de repo (GitHub, *Code > Download ZIP*), waarin alles in
een map `<repo>-<branch>/` staat.

    mealieshopper-price-archive.json   de kop van het eigen formaat, met de lijst van bestanden en de aantallen
    README.md                          wat erin staat, per winkel
    offers/<winkel>/<jjjj-mm>.jsonl     aanbiedingen (`offer_history`), op de maand waarin ze beginnen
    prices/<winkel>/<jjjj-mm>.jsonl     prijzen (`price_observations`), op de maand van de dag

Elke regel in een `.jsonl`-bestand is een regel van het eigen formaat `mealieshopper-price-archive` (`type` en de kolommen; zie
price_archive.py). Erin gaan alle aanbiedingen en prijzen, ook wat eerder uit een prijsarchief kwam, behalve een persoonlijke
aanbieding (Bonus Box): die hoort bij jou en kan ook niet in een archief. Een afbeelding gaat alleen als adres mee.

Dezelfde gegevens geven dezelfde bestanden, met de regels in een vaste volgorde: zo laat git bij een nieuwe export alleen zien wat
er veranderde. Alleen regels die de import aanneemt gaan erin (dezelfde controle als bij het importeren); een regel die dat niet
doet, telt als overgeslagen.

**Exporteren voor data.mealieshopper.nl** (`write_site_export`) maakt dezelfde regels in de vorm van die site: per winkel een
collectie, met lijsten van regels in losse JSON-bestanden en een JSON Schema per collectie. Zo zet je ze in de datarepo
(`helmerzNL/data.mealieshopper.nl`), en zo haalt **Aanbiedingen ophalen** ze er weer af (price_archive.py):

    MEALIESHOPPER-EXPORT.md                   wat erin staat en hoe je het instuurt (niet zelf insturen)
    data/<winkel>/offers/<jjjj>-<nnn>.json     aanbiedingen, op het jaar waarin ze beginnen
    data/<winkel>/prices/<jjjj>-<nnn>.json     prijzen, op het jaar van de dag
    schemas/<winkel>.schema.json               het schema van de collectie (`site_schema`)

De namen zijn die van de gist van wolph (een nieuw volgnummer zodra een document 4 MiB zou passeren), zodat een export zijn
documenten pad voor pad vervangt. Elk document staat er zoals de site het publiceert (twee spaties inspringen, UTF-8, een
regeleinde aan het eind), dus de SHA-256 op de site is die van het bestand.
"""
from __future__ import annotations

import json
import re
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any, Iterable

from . import about, offers, thread_db
from .price_archive import (
    EXPORT_MANIFEST, FORMAT, FORMAT_VERSION, MAX_PRICE, OFFER_COLUMNS, PRICE_COLUMNS, PRICE_SOURCES, SITE_FORMAT, RowError, Today,
    normalise_offer, normalise_price,
)

OFFER_FIELDS = tuple(column for column in OFFER_COLUMNS if column != "personal")  # altijd 0: een persoonlijke gaat er niet in
PRICE_FIELDS = PRICE_COLUMNS
MONTH = re.compile(r"^(\d{4}-\d{2})-\d{2}")
STORE_NAMES = {"ah": "Albert Heijn", "jumbo": "Jumbo", "picnic": "Picnic", "crisp": "Crisp"}

# Vaste volgorde: per winkel en maand, daarbinnen op datum en product. `id` alleen als laatste, voor twee gelijke regels.
OFFERS_SQL = f"""
SELECT {", ".join(OFFER_FIELDS)} FROM offer_history WHERE personal = 0
ORDER BY store, substr(COALESCE(valid_from, first_seen), 1, 7), COALESCE(valid_from, first_seen), valid_until, product_id, url,
  first_seen, id
"""
PRICES_SQL = f"SELECT {', '.join(PRICE_FIELDS)} FROM price_observations ORDER BY store, substr(day, 1, 7), day, product_id"


def _month(value: Any) -> str | None:
    match = MONTH.match(value) if isinstance(value, str) else None
    return match[1] if match else None


class _Writer:
    """Schrijft de regels per winkel en maand naar de ZIP, en houdt bij wat erin ging."""

    def __init__(self, archive: zipfile.ZipFile, stamp: tuple[int, int, int, int, int, int]):
        self.archive = archive
        self.stamp = stamp
        self.files: list[str] = []
        self.counts: dict[str, dict[str, int]] = {"offers": {}, "prices": {}}
        self.skipped = {"offers": 0, "prices": 0}
        self.span: dict[str, list[str]] = {}
        self._name: str | None = None
        self._handle: IO[bytes] | None = None

    def write(self, folder: str, store: str, month: str, row: dict[str, Any], kind: str) -> None:
        name = f"{folder}/{store}/{month}.jsonl"
        if name != self._name:
            self.close()
            info = zipfile.ZipInfo(name, date_time=self.stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            self._handle = self.archive.open(info, "w", force_zip64=True)
            self._name = name
            self.files.append(name)
        assert self._handle is not None
        line = {"type": kind, **row}
        self._handle.write((json.dumps(line, ensure_ascii=False) + "\n").encode("utf-8"))
        self.counts[folder][store] = self.counts[folder].get(store, 0) + 1
        span = self.span.setdefault(store, [month, month])
        span[0], span[1] = min(span[0], month), max(span[1], month)

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
        self._handle, self._name = None, None


def _rows(conn: Any, sql: str, fields: Iterable[str]) -> Iterable[dict[str, Any]]:
    names = tuple(fields)
    for values in conn.execute(sql):
        yield dict(zip(names, values))


def write_export(path: Path) -> dict[str, Any]:
    """Schrijft de export naar `path` (een ZIP) en geeft de kop terug: de bestanden en hoeveel regels per winkel.

    Als geheel voor `asyncio.to_thread`, na `price_archive.ensure_schema`."""
    bounds = Today()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    stamp = (now.year, now.month, now.day, now.hour, now.minute, now.second)
    with closing(thread_db.connect()) as conn, zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        writer = _Writer(archive, stamp)
        try:
            for row in _rows(conn, OFFERS_SQL, OFFER_FIELDS):
                month = _month(row["valid_from"]) or _month(row["first_seen"])
                try:
                    normalise_offer(row, bounds)  # wat de import niet aanneemt, gaat er niet in
                except RowError:
                    month = None
                if month is None or row["store"] not in offers.STORES:
                    writer.skipped["offers"] += 1
                    continue
                writer.write("offers", row["store"], month, row, "offer")
            for row in _rows(conn, PRICES_SQL, PRICE_FIELDS):
                month = _month(row["day"])
                try:
                    normalise_price(row, bounds)
                except RowError:
                    month = None
                if month is None or row["store"] not in offers.STORES:
                    writer.skipped["prices"] += 1
                    continue
                writer.write("prices", row["store"], month, row, "price")
        finally:
            writer.close()
        header = {
            "format": FORMAT,
            "formatVersion": FORMAT_VERSION,
            "layout": "folders",
            "createdAt": now.isoformat().replace("+00:00", "Z"),
            "source": f"MealieShopper {about.read_version()}",
            "license": "",
            "files": writer.files,
            "counts": writer.counts,
            "skipped": writer.skipped,
        }
        for name, text in ((EXPORT_MANIFEST, json.dumps(header, ensure_ascii=False, indent=2) + "\n"), ("README.md", readme(header, writer.span))):
            info = zipfile.ZipInfo(name, date_time=stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, text.encode("utf-8"))
    return header


def readme(header: dict[str, Any], span: dict[str, list[str]]) -> str:
    """Een korte uitleg voor wie de map bekijkt, zoals op GitHub, in het Nederlands en het Engels."""
    rows = []
    for store, name in STORE_NAMES.items():
        offer_count = header["counts"]["offers"].get(store, 0)
        price_count = header["counts"]["prices"].get(store, 0)
        if offer_count or price_count:
            first, last = span.get(store, ["", ""])
            rows.append(f"| {name} | {offer_count} | {price_count} | {first} | {last} |")
    table = "\n".join(rows) or "| - | 0 | 0 | | |"
    day = header["createdAt"][:10]
    return f"""# Prijsarchief van MealieShopper

Aanbiedingen en prijzen van supermarkten, geëxporteerd door {header["source"]} op {day}. Persoonlijke aanbiedingen (Bonus Box)
staan er niet in.

| Winkel | Aanbiedingen | Prijzen | Van | Tot |
| --- | ---: | ---: | --- | --- |
{table}

Elke map `offers/<winkel>/` (aanbiedingen) en `prices/<winkel>/` (prijzen) heeft per maand een bestand `<jjjj-mm>.jsonl`, met één
JSON-object per regel in het formaat `mealieshopper-price-archive`. `{EXPORT_MANIFEST}` is de kop, met de lijst van bestanden.
Importeren in MealieShopper: **Instellingen, Systeem, Prijsarchief importeren**, met de ZIP van deze map (op GitHub: **Code,
Download ZIP**).

Dit bestand en `{EXPORT_MANIFEST}` maakt de app bij elke export opnieuw.

## English

Supermarket offers and prices, exported by {header["source"]} on {day}. Personal offers (Bonus Box) are not included. Each folder
`offers/<store>/` (offers) and `prices/<store>/` (prices) has one file `<yyyy-mm>.jsonl` per month, with one JSON object per line in
the `mealieshopper-price-archive` format; `{EXPORT_MANIFEST}` is the header, listing the files. To import into MealieShopper:
**Settings, System, Import price archive**, with the ZIP of this folder (on GitHub: **Code, Download ZIP**).
"""


# --- exporteren voor data.mealieshopper.nl -------------------------------------------------------------------------

SITE_DOC_BYTES = 4 * 1024 * 1024  # zoals de gist; de site zelf staat 10 MB per document toe
SITE_README = "MEALIESHOPPER-EXPORT.md"
SITE_REPOSITORY = "https://github.com/helmerzNL/data.mealieshopper.nl"
YEAR = re.compile(r"^(\d{4})-\d{2}-\d{2}")
DATE = r"^\d{4}-\d{2}-\d{2}$"
STAMP = r"^\d{4}-\d{2}-\d{2}(?:[T ].*)?$"
# De velden die de gists van wolph meesturen en die de app niet bewaart: in het schema toegestaan, zodat ook zijn documenten
# erin passen (aanbiedingen: de foldertekst en de koppeling; prijzen: de commit van de bron).
GIST_TEXT_FIELDS = ("source_title", "description", "raw_old", "raw_now", "reason", "matched_title")
GIST_PRICE_FIELDS = ("source_commit",)


def _text(highest: int, required: bool = False) -> dict[str, Any]:
    if required:
        return {"type": "string", "minLength": 1, "maxLength": highest}
    return {"type": ["string", "null"], "maxLength": highest}


def site_schema(store: str) -> dict[str, Any]:
    """Het JSON Schema van de collectie `store` op data.mealieshopper.nl: een lijst van aanbiedingen en prijzen, met dezelfde
    regels als de import (`normalise_offer` en `normalise_price`). Elke regel is precies één van de twee."""
    name = STORE_NAMES[store]
    url = {"type": ["string", "null"], "maxLength": 2000, "pattern": r"^https://[^ ]+$"}
    amount = {"type": ["number", "null"], "minimum": 0, "maximum": MAX_PRICE}
    stamp = {"type": "string", "maxLength": 40, "pattern": STAMP}
    offer = {
        "title": "Aanbieding / offer",
        "type": "object",
        "required": ["store", "product_id", "title", "first_seen", "last_seen"],
        "properties": {
            "store": {"const": store},
            "product_id": _text(100, required=True),
            "title": _text(1000, required=True),
            "brand": _text(300),
            "unit_size": _text(300),
            "category": _text(300),
            "price": amount,
            "price_before": amount,
            "promo_label": _text(2000),
            "image": {"type": ["string", "null"], "maxLength": 2000},
            "url": url,
            "valid_from": {"type": ["string", "null"], "pattern": DATE},
            "valid_until": {"type": ["string", "null"], "pattern": DATE},
            "first_seen": stamp,
            "last_seen": stamp,
            "ended_at": {**stamp, "type": ["string", "null"]},
            "syncs": {"type": "integer", "minimum": 1, "maximum": 1_000_000},
            "continued": {"enum": [0, 1]},
            "personal": {"const": 0},
            **{field: {"type": "string"} for field in GIST_TEXT_FIELDS},
            "match_kind": {"enum": ["exact", "approximate"]},
            "match_score": {"type": "number"},
        },
        "additionalProperties": False,
    }
    price = {
        "title": "Prijs / price",
        "type": "object",
        "required": ["store", "product_id", "day", "source", "title", "price"],
        "properties": {
            "store": {"const": store},
            "product_id": _text(100, required=True),
            "day": {"type": "string", "pattern": DATE},
            "source": {"enum": list(PRICE_SOURCES)},
            "title": _text(1000, required=True),
            "brand": _text(300),
            "unit_size": _text(300),
            "image": {"type": ["string", "null"], "maxLength": 2000},
            "url": url,
            "price": {"type": "number", "exclusiveMinimum": 0, "maximum": MAX_PRICE},
            "seen_at": {**stamp, "type": ["string", "null"]},
            **{field: {"type": "string"} for field in GIST_PRICE_FIELDS},
        },
        "additionalProperties": False,
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": f"{name}: aanbiedingen en prijzen / offers and prices",
        "description": (
            f"Aanbiedingen (offers/) en prijzen (prices/) van {name} in EUR, uit MealieShopper. "
            f"{name} offers (offers/) and prices (prices/) in EUR, from MealieShopper."
        ),
        "type": "array",
        "items": {"oneOf": [offer, price]},
    }


def site_text(data: Any) -> str:
    """Een document zoals data.mealieshopper.nl het publiceert (`serialize` in scripts/datastore.py van de site)."""
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def _site_https(value: Any) -> str | None:
    return value if isinstance(value, str) and value.startswith("https://") and " " not in value and len(value) <= 2000 else None


class _SiteWriter:
    """Schrijft de regels per winkel, soort en jaar als JSON-lijsten van hoogstens `limit` bytes, zoals de site ze publiceert."""

    def __init__(self, archive: zipfile.ZipFile, stamp: tuple[int, int, int, int, int, int], limit: int = SITE_DOC_BYTES):
        self.archive = archive
        self.stamp = stamp
        self.limit = limit
        self.files: list[str] = []
        self.counts: dict[str, dict[str, int]] = {"offers": {}, "prices": {}}
        self.skipped = {"offers": 0, "prices": 0}
        self.span: dict[str, list[str]] = {}
        self._numbers: dict[tuple[str, str, str], int] = {}
        self._key: tuple[str, str, str] | None = None
        self._items: list[str] = []
        self._size = 0

    def write(self, folder: str, store: str, year: str, record: dict[str, Any]) -> None:
        # Een regel in de lijst: zijn eigen JSON met twee spaties extra ingesprongen, zoals json.dumps(lijst, indent=2) doet.
        text = "\n".join("  " + line for line in json.dumps(record, ensure_ascii=False, indent=2).split("\n"))
        size = len(text.encode("utf-8"))
        key = (store, folder, year)
        if key != self._key:
            self.flush()
            self._key = key
        elif self._items and self._size + 2 + size > self.limit:  # ",\n" ervoor
            self.flush()
        self._size = self._size + 2 + size if self._items else len("[\n") + size + len("\n]\n")
        self._items.append(text)
        self.counts[folder][store] = self.counts[folder].get(store, 0) + 1
        span = self.span.setdefault(store, [year, year])
        span[0], span[1] = min(span[0], year), max(span[1], year)

    def flush(self) -> None:
        if not self._items or self._key is None:
            return
        store, folder, year = self._key
        number = self._numbers.get(self._key, 0) + 1
        self._numbers[self._key] = number
        name = f"data/{store}/{folder}/{year}-{number:03d}.json"
        info = zipfile.ZipInfo(name, date_time=self.stamp)
        info.compress_type = zipfile.ZIP_DEFLATED
        self.archive.writestr(info, ("[\n" + ",\n".join(self._items) + "\n]\n").encode("utf-8"))
        self.files.append(name)
        self._items, self._size = [], 0


def write_site_export(path: Path, limit: int = SITE_DOC_BYTES) -> dict[str, Any]:
    """Schrijft de export voor data.mealieshopper.nl naar `path` (een ZIP) en geeft de kop terug, zoals `write_export`.

    Elke regel staat erin zoals de import hem zou opslaan (`normalise_offer`, `normalise_price`), met de bron van een prijs en
    het adres van een afbeelding zoals ze waren. Als geheel voor `asyncio.to_thread`, na `price_archive.ensure_schema`."""
    bounds = Today()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    stamp = (now.year, now.month, now.day, now.hour, now.minute, now.second)
    with closing(thread_db.connect()) as conn, zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        writer = _SiteWriter(archive, stamp, limit)
        for row in _rows(conn, OFFERS_SQL, OFFER_FIELDS):
            try:
                record = dict(zip(OFFER_COLUMNS, normalise_offer(row, bounds)))
            except RowError:
                writer.skipped["offers"] += 1
                continue
            match = YEAR.match(record["valid_from"] or record["first_seen"])
            if match is None or record["store"] not in offers.STORES:
                writer.skipped["offers"] += 1
                continue
            record.pop("personal")
            record["image"] = _site_https(row["image"])
            writer.write("offers", record["store"], match[1], record)
        for row in _rows(conn, PRICES_SQL, PRICE_FIELDS):
            try:
                record = dict(zip(PRICE_COLUMNS, normalise_price(row, bounds)))
            except RowError:
                writer.skipped["prices"] += 1
                continue
            if record["store"] not in offers.STORES:
                writer.skipped["prices"] += 1
                continue
            record["source"] = row["source"]  # al gecontroleerd; `search` blijft `search`
            record["image"] = _site_https(row["image"])
            writer.write("prices", record["store"], record["day"][:4], record)
        writer.flush()
        stores = [store for store in offers.STORES if store in writer.span]
        for store in stores:
            info = zipfile.ZipInfo(f"schemas/{store}.schema.json", date_time=stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, site_text(site_schema(store)).encode("utf-8"))
        header = {
            "format": SITE_FORMAT,
            "createdAt": now.isoformat().replace("+00:00", "Z"),
            "source": f"MealieShopper {about.read_version()}",
            "files": writer.files,
            "schemas": [f"schemas/{store}.schema.json" for store in stores],
            "counts": writer.counts,
            "skipped": writer.skipped,
        }
        info = zipfile.ZipInfo(SITE_README, date_time=stamp)
        info.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(info, site_readme(header, writer.span).encode("utf-8"))
    return header


def site_readme(header: dict[str, Any], span: dict[str, list[str]]) -> str:
    """Wat er in de export staat en hoe je hem instuurt, in het Nederlands en het Engels."""
    rows = []
    for store, name in STORE_NAMES.items():
        offer_count = header["counts"]["offers"].get(store, 0)
        price_count = header["counts"]["prices"].get(store, 0)
        if offer_count or price_count:
            first, last = span.get(store, ["", ""])
            rows.append(f"| {name} (`{store}`) | {offer_count} | {price_count} | {first} | {last} |")
    table = "\n".join(rows) or "| - | 0 | 0 | | |"
    day = header["createdAt"][:10]
    return f"""# Export voor data.mealieshopper.nl

Aanbiedingen en prijzen van supermarkten, geëxporteerd door {header["source"]} op {day}, in de vorm van
[data.mealieshopper.nl](https://data.mealieshopper.nl). Persoonlijke aanbiedingen (Bonus Box) staan er niet in.

| Collectie | Aanbiedingen | Prijzen | Van | Tot |
| --- | ---: | ---: | --- | --- |
{table}

## Insturen

1. Zet de mappen `data/` en `schemas/` in de datarepo ({SITE_REPOSITORY}): met git, of op GitHub met **Add file, Upload
   files** (hoogstens 100 bestanden per keer). Dit bestand stuur je niet mee.
2. Staat er voor een winkel al een zip met dezelfde documenten (zoals die uit de gist), haal die dan in dezelfde pull request
   weg; anders meldt de controle twee bronnen voor hetzelfde adres.
3. Open een pull request en wacht op de check **Inzending controleren**.

Een nieuwe export geeft voor dezelfde gegevens dezelfde bestanden, dus git laat alleen zien wat er veranderde. Importeren in
MealieShopper: **Instellingen, Systeem, Prijsarchief importeren** met deze ZIP, of **Aanbiedingen ophalen** zodra het online
staat.

## English

Supermarket offers and prices, exported by {header["source"]} on {day}, in the layout of
[data.mealieshopper.nl](https://data.mealieshopper.nl). Personal offers (Bonus Box) are not included. To submit: put the folders
`data/` and `schemas/` into the data repository ({SITE_REPOSITORY}), with git or on GitHub with **Add file, Upload files** (at
most 100 files at a time), without this file. If a store already has a zip with the same documents (such as the one from the
gist), remove it in the same pull request. Then open a pull request and wait for the check **Inzending controleren**. To import
into MealieShopper: **Settings, System, Import price archive** with this ZIP, or **Fetch offers** once it is online.
"""
