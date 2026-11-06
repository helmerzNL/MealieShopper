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
"""
from __future__ import annotations

import json
import re
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any, Iterable

from . import about, offers
from .price_archive import EXPORT_MANIFEST, FORMAT, FORMAT_VERSION, OFFER_COLUMNS, PRICE_COLUMNS, RowError, Today, _connect, normalise_offer, normalise_price

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
    """Schrijft de export naar `path` (een ZIP) en geeft de kop terug: de bestanden en hoeveel regels per winkel."""
    bounds = Today()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    stamp = (now.year, now.month, now.day, now.hour, now.minute, now.second)
    with closing(_connect()) as conn, zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
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
