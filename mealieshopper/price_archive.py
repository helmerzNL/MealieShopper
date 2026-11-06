"""Prijsarchief importeren (Instellingen > Systeem): historische folder- en winkelprijzen toevoegen aan de Prijsgeschiedenis.

Een prijsarchief is een bestand met prijzen van vóór MealieShopper, bijvoorbeeld het folderarchief van wolph (AH en Jumbo
2009-2026, Picnic 2022-2023, Crisp 2021-2026). Anders dan **Back-up terugzetten** vervangt het niets: het voegt rijen toe aan
`offer_history` (aanbiedingen) en `price_observations` (prijzen), en het verbetert hooguit vooraf beschreven archiefregels.

Twee formaten, die op dezelfde regels uitkomen (`offer`, `price`, `correction`) en dezelfde controle krijgen:

* **Eigen formaat** `mealieshopper-price-archive`, versie 1: een `.jsonl`-bestand (of `.jsonl.gz`) met op de eerste regel een kop
  (`{"format": "mealieshopper-price-archive", "formatVersion": 1, ...}`) en daarna één JSON-object per regel met `type` en de
  kolommen van de tabel. De handleiding beschrijft het formaat.
* **Het pakket van de gist** zoals **Download ZIP** het geeft: `supermarket-manifest.json` met Base64-delen van
  `supermarket-archives-<datum>.tar.gz`, met daarin `data-manifest.json` en zes gegevensbestanden (`GIST_FILES`). Het losse
  `.tar.gz` mag ook. De app leest alleen die bestanden; de tar wordt nooit uitgepakt en geen script uit het pakket draait.

Het bestand wordt niet vertrouwd. Elke controlesom, elke regel en elke waarde wordt gecontroleerd; één fout weigert het hele
bestand en verandert niets. Wat er al staat gaat altijd voor: een regel die er al is, een eigen prijs op dezelfde dag of een
correctie waarvan de oude waarde niet klopt, wordt overgeslagen en in de samenvatting genoemd.

Twee stappen, zoals bij het terugzetten van een back-up (`backup.py`):

1. `stage` slaat de upload op, controleert alles, schrijft de nieuwe regels genormaliseerd naar een werkbestand en geeft een
   samenvatting. De database verandert niet; het geüploade bestand zelf is daarna weg.
2. `start_job` zet het werkbestand op de achtergrond in de database, in één korte transactie die opnieuw controleert wat er al
   staat (er kan intussen een synchronisatie zijn geweest).

Een afbeeldingsadres uit een archief neemt de app niet over: de app haalt afbeeldingen van zijn eigen winkels op, niet van een
adres uit een bestand.
"""
from __future__ import annotations

import base64
import binascii
import gzip
import hashlib
import json
import logging
import math
import os
import re
import secrets
import sqlite3
import tarfile
import threading
import time
import zipfile
import zlib
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import IO, Any, Callable, Iterator

from flask import Flask, jsonify, request

from . import audit, auth, offers, prices
from .i18n import tr

log = logging.getLogger(__name__)

FORMAT = "mealieshopper-price-archive"
FORMAT_VERSION = 1
GIST_FORMAT = "supermarket-archive-base64-parts"
GIST_FORMAT_VERSION = 1
GIST_MANIFEST = "supermarket-manifest.json"
OLD_GIST_MANIFEST = "manifest.json"  # het eerdere AH-pakket; het nieuwe pakket bevat dezelfde gegevens, gecorrigeerd
DATA_MANIFEST = "data-manifest.json"
# De gegevensbestanden in het pakket van de gist en wat elke regel wordt. De rest (bron-HTML, pdf's, scripts) lezen we niet.
GIST_FILES: dict[str, str] = {
    "ah-corrected/corrections.json": "correction",
    "ah-corrected/observations.jsonl": "offer",
    "jumbo/reports/observations.jsonl": "offer",
    "picnic/reports/offers.jsonl": "offer",
    "picnic/reports/observations.jsonl": "price",
    "crisp/reports/observations.jsonl": "price",
}
GIST_TAR_NAME = re.compile(r"supermarket-archives/(\d{4}-\d{2}-\d{2})/(.+)")

MAX_UPLOAD_BYTES = 200 * 1024 * 1024  # zoals een back-up (één grens voor een reverse proxy); de ZIP van de gist is ruim 100 MB
MAX_MEMBER_BYTES = 300 * 1024 * 1024  # uitgepakt, per bestand: tegen een bestand dat zich opblaast
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_PART_BYTES = 16 * 1024 * 1024
MAX_PARTS = 1000
MAX_LINE_BYTES = 1024 * 1024
MAX_RECORDS = 1_000_000
CHUNK = 1024 * 1024
BATCH = 5000
STAGE_TTL_SECONDS = 3600
JOB_STALE_SECONDS = 15 * 60
STAGE_PREFIX = ".price-archive-"
TOKEN_PATTERN = re.compile(r"^[0-9a-f]{32}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
STAMP_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[T ].*)?$")
CONTROL = re.compile(r"[\x00-\x1f\x7f]")
# Een stuurteken in vrije tekst (er staat een ESC in een Jumbo-folder) wordt een spatie; in een sleutel weigeren we het.
CONTROL_RUN = re.compile(r"\s*[\x00-\x1f\x7f]+\s*")
EARLIEST = date(2000, 1, 1)
MAX_PRICE = 100_000.0

STORES = offers.STORES
PRICE_SOURCES = ("public_press_release", prices.PUBLIC_OBSERVED_UNKNOWN)
OFFER_COLUMNS = (
    "store", "product_id", "title", "brand", "unit_size", "category", "price", "price_before", "promo_label", "image", "url",
    "valid_from", "valid_until", "first_seen", "last_seen", "ended_at", "syncs", "continued", "personal",
)
PRICE_COLUMNS = ("store", "product_id", "day", "source", "title", "brand", "unit_size", "image", "url", "price", "seen_at")

JOB_SCHEMA = """
-- De import van een prijsarchief die loopt of net klaar is (zie price_archive.py). Hoort niet in de back-up.
CREATE TABLE IF NOT EXISTS price_archive_jobs (
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  error_key TEXT,
  error_params TEXT,
  summary TEXT,
  seconds REAL,
  dismissed INTEGER NOT NULL DEFAULT 0,
  started_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  finished_at TEXT
);
"""

# --- meldingen --------------------------------------------------------------------------------------------------

MESSAGES: dict[str, tuple[str, str]] = {
    "not_an_archive": (
        "Dit is geen prijsarchief. Kies de ZIP van het pakket, het .tar.gz-archief of een .jsonl-bestand in het formaat mealieshopper-price-archive.",
        "This is not a price archive. Choose the ZIP of the package, the .tar.gz archive or a .jsonl file in the mealieshopper-price-archive format.",
    ),
    "old_package": (
        "Dit is het eerdere AH-pakket. Kies de ZIP met supermarket-manifest.json: die bevat dezelfde AH-gegevens, gecorrigeerd, en ook Jumbo, Picnic en Crisp.",
        "This is the earlier AH package. Choose the ZIP with supermarket-manifest.json: it holds the same AH data, corrected, plus Jumbo, Picnic and Crisp.",
    ),
    "newer_version": (
        "Dit prijsarchief heeft formaatversie {found}; deze versie van MealieShopper kent versie {supported}. Werk MealieShopper eerst bij.",
        "This price archive has format version {found}; this version of MealieShopper knows version {supported}. Update MealieShopper first.",
    ),
    "too_large": ("Het bestand is groter dan {limit} MB.", "The file is larger than {limit} MB."),
    "too_many": ("Het archief heeft meer dan {limit} regels.", "The archive has more than {limit} lines."),
    "bad_structure": ("Het archief is beschadigd of bewerkt: {detail}.", "The archive is damaged or edited: {detail}."),
    "checksum": (
        "De controlesom van {file} klopt niet. Download het pakket opnieuw.",
        "The checksum of {file} does not match. Download the package again.",
    ),
    "missing_file": ("Het archief mist {file}.", "The archive is missing {file}."),
    "bad_row": ("{file}, regel {line}: {reason}", "{file}, line {line}: {reason}"),
    "empty": ("Het archief bevat geen prijzen.", "The archive contains no prices."),
    "stage_missing": (
        "Het gekozen prijsarchief is niet meer beschikbaar. Kies het bestand opnieuw.",
        "The chosen price archive is no longer available. Choose the file again.",
    ),
    "busy": ("Er loopt al een import van een prijsarchief. Wacht tot die klaar is.", "A price archive import is already running. Wait until it has finished."),
    "interrupted": (
        "De import is onderbroken (bijvoorbeeld door een herstart van de app). Er is niets gewijzigd; kies het bestand opnieuw.",
        "The import was interrupted (for example by a restart of the app). Nothing was changed; choose the file again.",
    ),
    "failed": (
        "De import is mislukt. Er is niets gewijzigd. Kijk in het logboek van de container voor de reden.",
        "The import failed. Nothing was changed. See the container log for the reason.",
    ),
    "unknown_job": ("Deze klus bestaat niet (meer).", "This job no longer exists."),
}

# Waarom een regel niet klopt; komt in `bad_row` als {reason}.
REASONS: dict[str, tuple[str, str]] = {
    "json": ("geen geldige JSON", "not valid JSON"),
    "object": ("geen JSON-object", "not a JSON object"),
    "too_long_line": ("de regel is te lang", "the line is too long"),
    "type": ("onbekende soort regel {value} (offer, price of correction)", "unknown kind of line {value} (offer, price or correction)"),
    "missing": ("{field} ontbreekt", "{field} is missing"),
    "wrong_type": ("{field} heeft een verkeerd type", "{field} has the wrong type"),
    "too_long": ("{field} is te lang", "{field} is too long"),
    "control": ("{field} bevat stuurtekens", "{field} contains control characters"),
    "store": ("onbekende winkel {value}", "unknown store {value}"),
    "date": ("{field} is geen geldige datum", "{field} is not a valid date"),
    "date_range": ("{field} ligt buiten 2000 tot vandaag", "{field} is outside 2000 to today"),
    "url": ("{field} is geen https-adres", "{field} is not an https address"),
    "price": ("{field} is geen geldige prijs", "{field} is not a valid price"),
    "number": ("{field} is geen geldig getal", "{field} is not a valid number"),
    "source": (
        "bron {value} is niet toegestaan (alleen public_press_release of public_observed_unknown)",
        "source {value} is not allowed (only public_press_release or public_observed_unknown)",
    ),
    "personal": ("een archief kan geen persoonlijke aanbieding (Bonus Box) bevatten", "an archive cannot contain a personal offer (Bonus Box)"),
    "seen_at": ("seen_at valt niet op day", "seen_at is not on day"),
}


class ArchiveError(Exception):
    """Een melding voor de beheerder, als sleutel plus waarden; de tekst volgt pas bij het lezen, in de taal van de lezer."""

    def __init__(self, code: str, status: int = 400, **params: Any):
        super().__init__(code)
        self.key = code
        self.status = status
        self.params = params


class RowError(ValueError):
    """Een regel die niet klopt; `read` maakt er een ArchiveError met bestand en regelnummer van."""

    def __init__(self, reason: str, field: str = "", value: Any = ""):
        super().__init__(reason)
        self.reason = reason
        self.field = field
        self.value = str(value)[:80]


def describe(key: str, params: dict[str, Any] | None = None) -> str:
    params = dict(params or {})
    if key == "bad_row":
        nl, en = REASONS.get(str(params.get("reason")), REASONS["json"])
        try:
            params["reason"] = tr(nl, en).format(field=params.get("field", ""), value=params.get("value", ""))
        except (KeyError, IndexError):
            params["reason"] = tr(nl, en)
    nl, en = MESSAGES.get(key, MESSAGES["failed"])
    text = tr(nl, en)
    try:
        return text.format(**params)
    except (KeyError, IndexError):
        return text


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _key(*parts: Any) -> bytes:
    """Een korte sleutel voor een set: zo blijft het geheugen klein, ook met honderdduizenden archiefregels."""
    return hashlib.blake2b("\x1f".join("" if part is None else str(part) for part in parts).encode("utf-8"), digest_size=16).digest()


# --- waarden controleren ------------------------------------------------------------------------------------------


def _text(row: dict[str, Any], field: str, limit: int, required: bool = False, clean: bool = False) -> str | None:
    value = row.get(field)
    if value is None or value == "":
        if required:
            raise RowError("missing", field)
        return None
    if not isinstance(value, str):
        raise RowError("wrong_type", field)
    if len(value) > limit:
        raise RowError("too_long", field)
    if CONTROL.search(value):
        if not clean:
            raise RowError("control", field)
        value = CONTROL_RUN.sub(" ", value)
    if required and not value.strip():
        raise RowError("missing", field)
    return value


def _store(row: dict[str, Any]) -> str:
    store = row.get("store")
    if store is None or store == "":
        raise RowError("missing", "store")
    if store not in STORES:
        raise RowError("store", "store", store)
    return str(store)


def _date(row: dict[str, Any], field: str, latest: date, required: bool = False) -> str | None:
    value = row.get(field)
    if value is None or value == "":
        if required:
            raise RowError("missing", field)
        return None
    if not isinstance(value, str):
        raise RowError("wrong_type", field)
    if not DATE_PATTERN.match(value):
        raise RowError("date", field)
    try:
        day = date.fromisoformat(value)
    except ValueError:
        raise RowError("date", field) from None
    if not EARLIEST <= day <= latest:
        raise RowError("date_range", field)
    return value


def _stamp(row: dict[str, Any], field: str, latest: date, required: bool = False) -> str | None:
    value = row.get(field)
    if value is None or value == "":
        if required:
            raise RowError("missing", field)
        return None
    if not isinstance(value, str):
        raise RowError("wrong_type", field)
    if len(value) > 40 or not STAMP_PATTERN.match(value):
        raise RowError("date", field)
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        raise RowError("date", field) from None
    if not EARLIEST <= moment.date() <= latest:
        raise RowError("date_range", field)
    return value


def _price(row: dict[str, Any], field: str, required: bool = False, positive: bool = False) -> float | None:
    if field not in row or row[field] is None:
        if required:
            raise RowError("missing", field)
        return None
    value = row[field]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RowError("price", field)
    number = float(value)
    if not math.isfinite(number) or number < 0 or number > MAX_PRICE or (positive and number <= 0):
        raise RowError("price", field)
    return number


def _url(row: dict[str, Any], field: str, required: bool = False) -> str | None:
    value = _text(row, field, 2000, required)
    if value is None:
        return None
    if not value.startswith("https://") or len(value) <= len("https://") or " " in value:
        raise RowError("url", field)
    return value


def _integer(row: dict[str, Any], field: str, default: int, lowest: int, highest: int) -> int:
    value = row.get(field)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or not lowest <= value <= highest:
        raise RowError("number", field)
    return value


class Today:
    """De grenzen van vandaag: een prijs ligt niet in de toekomst, een looptijd hooguit een jaar."""

    def __init__(self) -> None:
        self.today = date.today()
        self.later = self.today + timedelta(days=366)


def normalise_offer(row: dict[str, Any], bounds: Today) -> tuple[Any, ...]:
    """Een aanbieding uit een archief als rij voor `offer_history` (in de volgorde van OFFER_COLUMNS)."""
    store = _store(row)
    product_id = _text(row, "product_id", 100, required=True)
    title = _text(row, "title", 1000, required=True, clean=True)
    brand = _text(row, "brand", 300, clean=True)
    unit_size = _text(row, "unit_size", 300, clean=True)
    category = _text(row, "category", 300, clean=True)
    price = _price(row, "price")
    price_before = _price(row, "price_before")
    promo_label = _text(row, "promo_label", 2000, clean=True)
    url = _url(row, "url", required=True)
    valid_from = _date(row, "valid_from", bounds.later)
    valid_until = _date(row, "valid_until", bounds.later)
    first_seen = _stamp(row, "first_seen", bounds.later, required=True)
    last_seen = _stamp(row, "last_seen", bounds.later, required=True)
    ended_at = _stamp(row, "ended_at", bounds.later) or last_seen
    syncs = _integer(row, "syncs", 1, 1, 1_000_000)
    continued = _integer(row, "continued", 0, 0, 1)
    if _integer(row, "personal", 0, 0, 1):
        raise RowError("personal", "personal")
    return (store, product_id, title, brand, unit_size, category, price, price_before, promo_label, "", url,
            valid_from, valid_until, first_seen, last_seen, ended_at, syncs, continued, 0)


def normalise_price(row: dict[str, Any], bounds: Today) -> tuple[Any, ...]:
    """Een prijs uit een archief als rij voor `price_observations` (in de volgorde van PRICE_COLUMNS)."""
    store = _store(row)
    product_id = _text(row, "product_id", 100, required=True)
    day = _date(row, "day", bounds.today, required=True)
    source = row.get("source")
    if source is None or source == "":
        raise RowError("missing", "source")
    if source not in PRICE_SOURCES:
        raise RowError("source", "source", source)
    title = _text(row, "title", 1000, required=True, clean=True)
    brand = _text(row, "brand", 300, clean=True)
    unit_size = _text(row, "unit_size", 300, clean=True)
    url = _url(row, "url")
    price = _price(row, "price", required=True, positive=True)
    seen_at = _stamp(row, "seen_at", bounds.later) or f"{day}T00:00:00+00:00"
    if datetime.fromisoformat(seen_at).date().isoformat() != day:
        raise RowError("seen_at", "seen_at")
    return (store, product_id, day, str(source), title, brand, unit_size, "", url, price, seen_at)


def normalise_correction(row: dict[str, Any]) -> tuple[Any, ...]:
    """Een correctie: (winkel, url, oude prijs, oude prijs ervoor, nieuwe prijs, nieuwe prijs ervoor)."""
    store = _store(row)
    url = _url(row, "url", required=True)
    values = []
    for field in ("old_price", "old_before", "new_price", "new_before"):
        if field not in row:
            raise RowError("missing", field)
        values.append(_price(row, field))
    return (store, url, *values)


# --- lezen --------------------------------------------------------------------------------------------------------


def _json_line(raw: bytes) -> Any:
    def reject(constant: str) -> Any:
        raise ValueError(constant)

    try:
        return json.loads(raw, parse_constant=reject)
    except ValueError:
        raise RowError("json") from None


def _bounded(handle: IO[bytes], limit: int) -> bytes:
    data = handle.read(limit + 1)
    if len(data) > limit:
        raise ArchiveError("too_large", 413, limit=limit // (1024 * 1024) or 1)
    return data


def _lines(handle: IO[bytes], digest: Any | None = None) -> Iterator[tuple[int, bytes]]:
    """De regels van een bestand, met regelnummer; met `digest` telt elke byte mee in de controlesom."""
    number = 0
    total = 0
    while True:
        line = handle.readline(MAX_LINE_BYTES + 1)
        if not line:
            return
        number += 1
        total += len(line)
        if digest is not None:
            digest.update(line)
        if total > MAX_MEMBER_BYTES:
            raise ArchiveError("too_large", 413, limit=MAX_MEMBER_BYTES // (1024 * 1024))
        if len(line) > MAX_LINE_BYTES and not line.endswith(b"\n"):
            raise RowError("too_long_line")
        if line.strip():
            yield number, line


class Archive:
    """Een geüpload bestand, gelezen als regels (`offer`, `price`, `correction`) met waar ze vandaan komen."""

    def __init__(self, path: Path, work_dir: Path):
        self.path = path
        self.work_dir = work_dir
        self.meta: dict[str, Any] = {}
        self.kind = self._detect()

    def _detect(self) -> str:
        with self.path.open("rb") as handle:
            head = handle.read(8)
        if head.startswith(b"PK\x03\x04"):
            return "zip"
        if head.startswith(b"\x1f\x8b"):
            try:
                with gzip.open(self.path, "rb") as handle:
                    start = handle.read(512)
            except (OSError, EOFError, zlib.error):
                raise ArchiveError("not_an_archive") from None
            if len(start) >= 262 and start[257:262] == b"ustar":
                return "tar"
            if start.lstrip().startswith(b"{"):
                return "jsonl.gz"
            raise ArchiveError("not_an_archive")
        if head.lstrip().startswith(b"{"):
            return "jsonl"
        raise ArchiveError("not_an_archive")

    def records(self) -> Iterator[tuple[str, dict[str, Any], str, int]]:
        """(soort, regel, bestand, regelnummer) voor elke regel. Een fout in het bestand is een ArchiveError."""
        if self.kind in ("jsonl", "jsonl.gz"):
            yield from self._own()
            return
        tar_path = self.path
        temporary: Path | None = None
        try:
            if self.kind == "zip":
                temporary = self._from_zip()
                tar_path = temporary
            yield from self._from_tar(tar_path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    # Het eigen formaat: een kop, daarna één regel per prijs.
    def _own(self) -> Iterator[tuple[str, dict[str, Any], str, int]]:
        name = FORMAT
        opener: Callable[..., IO[bytes]] = gzip.open if self.kind == "jsonl.gz" else open
        number = 0
        try:
            with opener(self.path, "rb") as handle:
                header: Any = None
                for number, raw in _lines(handle):
                    try:
                        data = _json_line(raw)
                    except RowError:
                        if header is None:
                            raise ArchiveError("not_an_archive") from None
                        raise
                    if header is None:
                        header = data
                        self._own_header(header)
                        continue
                    if not isinstance(data, dict):
                        raise RowError("object")
                    kind = data.get("type")
                    if kind not in ("offer", "price", "correction"):
                        raise RowError("type", "type", kind)
                    yield str(kind), data, name, number
                if header is None:
                    raise ArchiveError("not_an_archive")
        except RowError as exc:
            raise ArchiveError("bad_row", file=name, line=number or "?", reason=exc.reason, field=exc.field, value=exc.value) from None
        except (OSError, EOFError, zlib.error):
            raise ArchiveError("bad_structure", detail="gzip") from None

    def _own_header(self, header: Any) -> None:
        if not isinstance(header, dict) or header.get("format") != FORMAT:
            raise ArchiveError("not_an_archive")
        version = header.get("formatVersion")
        if isinstance(version, int) and not isinstance(version, bool) and version > FORMAT_VERSION:
            raise ArchiveError("newer_version", found=version, supported=FORMAT_VERSION)
        if version != FORMAT_VERSION or isinstance(version, bool):
            raise ArchiveError("bad_structure", detail="formatVersion")
        self.meta = {
            "format": FORMAT,
            "source": str(header.get("source") or "")[:200],
            "createdAt": str(header.get("createdAt") or "")[:40],
            "license": str(header.get("license") or "")[:200],
        }

    # De ZIP van de gist: Base64-delen naar één .tar.gz, met de controlesommen uit supermarket-manifest.json.
    def _from_zip(self) -> Path:
        try:
            archive = zipfile.ZipFile(self.path)
        except (zipfile.BadZipFile, OSError):
            raise ArchiveError("not_an_archive") from None
        with archive:
            entries = [info for info in archive.infolist() if not info.is_dir()]
            by_name = {info.filename: info for info in entries}

            def near_top(info: zipfile.ZipInfo, name: str) -> bool:
                path = PurePosixPath(info.filename)
                return path.name == name and len(path.parts) <= 2

            manifests = [info for info in entries if near_top(info, GIST_MANIFEST)]
            if len(manifests) != 1:
                if not manifests and any(near_top(info, OLD_GIST_MANIFEST) for info in entries):
                    raise ArchiveError("old_package")
                raise ArchiveError("not_an_archive")
            manifest_info = manifests[0]
            folder = PurePosixPath(manifest_info.filename).parent
            with archive.open(manifest_info) as handle:
                try:
                    manifest = json.loads(_bounded(handle, MAX_MANIFEST_BYTES))
                except ValueError:
                    raise ArchiveError("bad_structure", detail=GIST_MANIFEST) from None
            parts = self._gist_manifest(manifest)
            self.work_dir.mkdir(parents=True, exist_ok=True)
            target = self.work_dir / f"{STAGE_PREFIX}{secrets.token_hex(8)}.tar.gz"
            digest = hashlib.sha256()
            total = 0
            try:
                with target.open("wb") as out:
                    os.chmod(target, 0o600)
                    for name, expected in parts:
                        info = by_name.get(name if str(folder) == "." else f"{folder}/{name}")
                        if info is None:
                            raise ArchiveError("missing_file", file=name)
                        if info.file_size > MAX_PART_BYTES:
                            raise ArchiveError("too_large", 413, limit=MAX_PART_BYTES // (1024 * 1024))
                        with archive.open(info) as handle:
                            content = _bounded(handle, MAX_PART_BYTES)
                        if hashlib.sha256(content).hexdigest() != expected:
                            raise ArchiveError("checksum", file=name)
                        try:
                            raw = base64.b64decode(content.strip(), validate=True)
                        except (binascii.Error, ValueError):
                            raise ArchiveError("bad_structure", detail=name) from None
                        total += len(raw)
                        if total > manifest["archiveBytes"]:
                            raise ArchiveError("checksum", file=manifest["archiveFile"])
                        digest.update(raw)
                        out.write(raw)
                if total != manifest["archiveBytes"] or digest.hexdigest() != manifest["archiveSha256"]:
                    raise ArchiveError("checksum", file=manifest["archiveFile"])
            except ArchiveError:
                target.unlink(missing_ok=True)
                raise
            except (zipfile.BadZipFile, zlib.error, OSError, EOFError):
                target.unlink(missing_ok=True)
                raise ArchiveError("bad_structure", detail="zip") from None
            except BaseException:
                target.unlink(missing_ok=True)
                raise
        return target

    @staticmethod
    def _gist_manifest(manifest: Any) -> list[tuple[str, str]]:
        if not isinstance(manifest, dict) or manifest.get("format") != GIST_FORMAT:
            raise ArchiveError("not_an_archive")
        version = manifest.get("formatVersion")
        if isinstance(version, int) and not isinstance(version, bool) and version > GIST_FORMAT_VERSION:
            raise ArchiveError("newer_version", found=version, supported=GIST_FORMAT_VERSION)
        if version != GIST_FORMAT_VERSION:
            raise ArchiveError("bad_structure", detail="formatVersion")
        size = manifest.get("archiveBytes")
        name = manifest.get("archiveFile")
        if isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= MAX_MEMBER_BYTES:
            raise ArchiveError("bad_structure", detail="archiveBytes")
        if not isinstance(name, str) or not _bare(name) or not SHA256_PATTERN.match(str(manifest.get("archiveSha256") or "")):
            raise ArchiveError("bad_structure", detail="archiveFile")
        parts = manifest.get("parts")
        if not isinstance(parts, list) or not 0 < len(parts) <= MAX_PARTS:
            raise ArchiveError("bad_structure", detail="parts")
        result = []
        for part in parts:
            if not isinstance(part, dict) or not isinstance(part.get("file"), str) or not _bare(part["file"]):
                raise ArchiveError("bad_structure", detail="parts")
            if not SHA256_PATTERN.match(str(part.get("sha256") or "")):
                raise ArchiveError("bad_structure", detail="parts")
            result.append((part["file"], part["sha256"]))
        if len({name for name, _ in result}) != len(result):
            raise ArchiveError("bad_structure", detail="parts")
        return result

    # Het .tar.gz van het pakket: alleen data-manifest.json en de zes gegevensbestanden, in één doorgang gelezen.
    def _from_tar(self, path: Path) -> Iterator[tuple[str, dict[str, Any], str, int]]:
        data_manifest: Any = None
        seen: dict[str, str] = {}
        root: str | None = None
        try:
            with tarfile.open(path, "r|gz") as tar:
                for member in tar:
                    match = GIST_TAR_NAME.fullmatch(member.name)
                    if not match or not member.isfile():
                        continue
                    name = match.group(2)
                    if name != DATA_MANIFEST and name not in GIST_FILES:
                        continue
                    if root is None:
                        root = match.group(1)
                    elif root != match.group(1):
                        raise ArchiveError("bad_structure", detail="supermarket-archives")
                    if member.size > MAX_MEMBER_BYTES:
                        raise ArchiveError("too_large", 413, limit=MAX_MEMBER_BYTES // (1024 * 1024))
                    if name in seen or (name == DATA_MANIFEST and data_manifest is not None):
                        raise ArchiveError("bad_structure", detail=name)
                    handle = tar.extractfile(member)
                    if handle is None:
                        continue
                    if name == DATA_MANIFEST:
                        try:
                            data_manifest = json.loads(_bounded(handle, MAX_MANIFEST_BYTES))
                        except ValueError:
                            raise ArchiveError("bad_structure", detail=DATA_MANIFEST) from None
                        continue
                    digest = hashlib.sha256()
                    yield from self._gist_file(name, handle, digest)
                    seen[name] = digest.hexdigest()
        except (tarfile.TarError, OSError, EOFError, zlib.error):
            raise ArchiveError("bad_structure", detail="tar.gz") from None
        if root is None:
            raise ArchiveError("not_an_archive")
        if not isinstance(data_manifest, dict):
            raise ArchiveError("missing_file", file=DATA_MANIFEST)
        for name in GIST_FILES:
            if name not in seen:
                raise ArchiveError("missing_file", file=name)
            if data_manifest.get(name) != seen[name]:
                raise ArchiveError("checksum", file=name)
        self.meta = {"format": GIST_FORMAT, "source": f"supermarket-archives/{root}", "createdAt": root, "license": ""}

    @staticmethod
    def _gist_file(name: str, handle: IO[bytes], digest: Any) -> Iterator[tuple[str, dict[str, Any], str, int]]:
        kind = GIST_FILES[name]
        try:
            if kind == "correction":
                content = handle.read(MAX_MEMBER_BYTES + 1)
                digest.update(content)
                if len(content) > MAX_MEMBER_BYTES:
                    raise ArchiveError("too_large", 413, limit=MAX_MEMBER_BYTES // (1024 * 1024))
                items = _json_line(content)
                if not isinstance(items, list):
                    raise RowError("object")
                for number, item in enumerate(items, 1):
                    if not isinstance(item, dict):
                        raise ArchiveError("bad_row", file=name, line=number, reason="object", field="", value="")
                    # De correcties in het pakket gaan allemaal over Albert Heijn en noemen de winkel niet.
                    yield kind, {"store": "ah", **item}, name, number
                return
            for number, raw in _lines(handle, digest):
                try:
                    data = _json_line(raw)
                    if not isinstance(data, dict):
                        raise RowError("object")
                except RowError as exc:
                    raise ArchiveError("bad_row", file=name, line=number, reason=exc.reason, field=exc.field, value=exc.value) from None
                yield kind, data, name, number
        except RowError as exc:
            raise ArchiveError("bad_row", file=name, line="?", reason=exc.reason, field=exc.field, value=exc.value) from None


def _bare(name: str) -> bool:
    return bool(name) and len(name) <= 200 and "/" not in name and "\\" not in name and name not in (".", "..") and not CONTROL.search(name)


# --- vergelijken met wat er staat ---------------------------------------------------------------------------------


class Plan:
    """Wat een archief zou toevoegen aan de database zoals die nu is: per winkel nieuw, al aanwezig of overgeslagen.

    De regels voor dubbel komen uit wolphs `insert_rows`: een aanbieding met dezelfde winkel, url en looptijd is er al, en een
    aanbieding die de app zelf al kent (zelfde product en looptijd) ook. Een prijs met dezelfde winkel, product en dag is er al;
    staat er een andere prijs, dan gaat die van de app voor.
    """

    def __init__(self, conn: sqlite3.Connection):
        self.offer_urls: set[bytes] = set()
        self.offer_products: set[bytes] = set()
        self.max_offer_id = 0
        for row in conn.execute("SELECT id, store, product_id, url, valid_from, valid_until FROM offer_history"):
            self.max_offer_id = max(self.max_offer_id, int(row["id"]))
            if row["url"]:
                self.offer_urls.add(_key(row["store"], row["url"], row["valid_from"], row["valid_until"]))
            self.offer_products.add(_key(row["store"], row["product_id"], row["valid_from"], row["valid_until"]))
        self.prices: dict[bytes, float] = {
            _key(row["store"], row["product_id"], row["day"]): float(row["price"])
            for row in conn.execute("SELECT store, product_id, day, price FROM price_observations")
        }
        self.corrections: list[tuple[Any, ...]] = []
        self.stores: dict[str, dict[str, Any]] = {}

    def _store(self, store: str) -> dict[str, Any]:
        return self.stores.setdefault(store, {
            "offers": {"new": 0, "duplicate": 0, "covered": 0},
            "prices": {"new": 0, "duplicate": 0, "kept": 0},
            "earliest": None, "latest": None,
        })

    @staticmethod
    def _span(stats: dict[str, Any], day: str | None) -> None:
        if not day:
            return
        day = day[:10]
        stats["earliest"] = day if stats["earliest"] is None or day < stats["earliest"] else stats["earliest"]
        stats["latest"] = day if stats["latest"] is None or day > stats["latest"] else stats["latest"]

    def offer(self, row: tuple[Any, ...]) -> bool:
        """True als de aanbieding nieuw is (en dus in de database moet)."""
        store, product_id, url, valid_from, valid_until = row[0], row[1], row[10], row[11], row[12]
        stats = self._store(store)
        self._span(stats, valid_from or row[13])
        self._span(stats, valid_until or row[14])
        by_url = _key(store, url, valid_from, valid_until)
        if by_url in self.offer_urls:
            stats["offers"]["duplicate"] += 1
            return False
        if _key(store, product_id, valid_from, valid_until) in self.offer_products:
            stats["offers"]["covered"] += 1
            return False
        self.offer_urls.add(by_url)
        stats["offers"]["new"] += 1
        return True

    def price(self, row: tuple[Any, ...]) -> bool:
        store, product_id, day, price = row[0], row[1], row[2], row[9]
        stats = self._store(store)
        self._span(stats, day)
        key = _key(store, product_id, day)
        existing = self.prices.get(key)
        if existing is not None:
            stats["prices"]["duplicate" if abs(existing - price) <= 0.005 else "kept"] += 1
            return False
        self.prices[key] = price
        stats["prices"]["new"] += 1
        return True

    def correction(self, row: tuple[Any, ...]) -> None:
        self.corrections.append(row)

    def resolve_corrections(self, conn: sqlite3.Connection) -> tuple[dict[str, int], list[tuple[Any, ...]]]:
        """Welke correcties gaan door: alleen op één archiefregel van vóór de import, en alleen als de oude waarde klopt."""
        counts = {"apply": 0, "already": 0, "missing": 0, "conflict": 0}
        updates: list[tuple[Any, ...]] = []
        if not self.corrections:
            return counts, updates
        wanted = {(row[0], row[1]) for row in self.corrections}
        found: dict[tuple[str, str], list[sqlite3.Row]] = {}
        for row in conn.execute("SELECT id, store, product_id, url, price, price_before FROM offer_history WHERE id <= ? AND url IS NOT NULL", (self.max_offer_id,)):
            if (row["store"], row["url"]) in wanted:
                found.setdefault((row["store"], row["url"]), []).append(row)
        for store, url, old_price, old_before, new_price, new_before in self.corrections:
            rows = found.get((store, url), [])
            if not rows:
                counts["missing"] += 1
                continue
            if len(rows) != 1 or not str(rows[0]["product_id"]).startswith("folder:"):
                counts["conflict"] += 1
                continue
            current = (rows[0]["price"], rows[0]["price_before"])
            if _same(current, (new_price, new_before)):
                counts["already"] += 1
            elif _same(current, (old_price, old_before)):
                counts["apply"] += 1
                updates.append((new_price, new_before, rows[0]["id"], old_price, old_before))
            else:
                counts["conflict"] += 1
        return counts, updates


def _same(left: tuple[Any, ...], right: tuple[Any, ...]) -> bool:
    for a, b in zip(left, right):
        if a is None or b is None:
            if a is not b and not (a is None and b is None):
                return False
        elif abs(float(a) - float(b)) > 0.0005:
            return False
    return True


def check(archive: Archive, plan: Plan, write: Callable[[str, tuple[Any, ...]], None] | None = None) -> int:
    """Leest en controleert het hele archief tegen `plan`; `write` krijgt elke nieuwe regel. Geeft het aantal regels."""
    bounds = Today()
    count = 0
    for kind, data, name, number in archive.records():
        count += 1
        if count > MAX_RECORDS:
            raise ArchiveError("too_many", limit=MAX_RECORDS)
        try:
            if kind == "offer":
                row = normalise_offer(data, bounds)
                if plan.offer(row) and write:
                    write("o", row)
            elif kind == "price":
                row = normalise_price(data, bounds)
                if plan.price(row) and write:
                    write("p", row)
            else:
                plan.correction(normalise_correction(data))
        except RowError as exc:
            raise ArchiveError("bad_row", file=name, line=number, reason=exc.reason, field=exc.field, value=exc.value) from None
    if not count:
        raise ArchiveError("empty")
    return count


def _store_list(plan: Plan) -> list[dict[str, Any]]:
    """Per winkel, in de volgorde van de app (een JSON-object zou Flask op naam sorteren)."""
    return [{"store": store, **plan.stores[store]} for store in STORES if store in plan.stores]


def summarize(archive: Archive, plan: Plan, corrections: dict[str, int], lines: int) -> dict[str, Any]:
    stores = _store_list(plan)
    return {
        "format": archive.meta.get("format", ""),
        "source": archive.meta.get("source", ""),
        "createdAt": archive.meta.get("createdAt", ""),
        "license": archive.meta.get("license", ""),
        "lines": lines,
        "stores": stores,
        "corrections": corrections,
        "new": sum(s["offers"]["new"] + s["prices"]["new"] for s in stores) + corrections["apply"],
    }


# --- klaargezet bestand (stage) -----------------------------------------------------------------------------------


def _stage_path(token: str) -> Path:
    if not TOKEN_PATTERN.match(str(token or "")):
        raise ArchiveError("stage_missing", 404)
    return auth.data_dir() / f"{STAGE_PREFIX}{token}.rows"


def remove_old_staged_files() -> None:
    cutoff = time.time() - STAGE_TTL_SECONDS
    for path in auth.data_dir().glob(f"{STAGE_PREFIX}*"):
        try:
            if not path.name.endswith(".job") and path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            continue


def _connect() -> sqlite3.Connection:
    """De tabellen van de aanbiedingen en prijzen, zodat een lege installatie ze ook heeft."""
    return prices._connect()


def stage(stream: Any, content_length: int | None) -> dict[str, Any]:
    """Slaat de upload op, controleert alles en zet de nieuwe regels klaar. De database verandert niet."""
    limit = MAX_UPLOAD_BYTES // (1024 * 1024)
    if content_length is not None and content_length > MAX_UPLOAD_BYTES:
        raise ArchiveError("too_large", 413, limit=limit)
    directory = auth.data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    remove_old_staged_files()
    token = secrets.token_hex(16)
    upload = directory / f"{STAGE_PREFIX}{token}.upload"
    rows_path = _stage_path(token)
    received = 0
    try:
        with upload.open("wb") as handle:
            os.chmod(upload, 0o600)
            while True:
                chunk = stream.read(CHUNK)
                if not chunk:
                    break
                received += len(chunk)
                if received > MAX_UPLOAD_BYTES:
                    raise ArchiveError("too_large", 413, limit=limit)
                handle.write(chunk)
        if not received:
            raise ArchiveError("not_an_archive")
        archive = Archive(upload, directory)
        with closing(_connect()) as conn:
            plan = Plan(conn)
            with rows_path.open("w", encoding="utf-8") as out:
                os.chmod(rows_path, 0o600)
                lines = check(archive, plan, lambda kind, row: out.write(json.dumps([kind, list(row)], ensure_ascii=False) + "\n"))
            corrections, _ = plan.resolve_corrections(conn)
            with rows_path.open("a", encoding="utf-8") as out:
                for row in plan.corrections:
                    out.write(json.dumps(["c", list(row)], ensure_ascii=False) + "\n")
        summary = summarize(archive, plan, corrections, lines)
    except BaseException:
        rows_path.unlink(missing_ok=True)
        raise
    finally:
        upload.unlink(missing_ok=True)  # het bestand zelf hebben we niet meer nodig; de nieuwe regels staan klaar
    return {"token": token, **summary}


def discard(token: str) -> None:
    _stage_path(token).unlink(missing_ok=True)


# --- importeren ---------------------------------------------------------------------------------------------------


def _staged_rows(path: Path) -> Iterator[tuple[str, tuple[Any, ...]]]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            kind, values = json.loads(line)
            yield kind, tuple(values)


def apply(path: Path) -> dict[str, Any]:
    """Zet de klaargezette regels in één transactie in de database, na een nieuwe vergelijking met wat er dan staat."""
    started = time.monotonic()
    conn = _connect()
    conn.isolation_level = None  # we sturen BEGIN en COMMIT zelf
    try:
        conn.execute("PRAGMA busy_timeout = 30000")  # een lopende synchronisatie mag even afmaken
        conn.execute("PRAGMA cache_size = -65536")  # zo houden andere verbindingen langer leestoegang
        conn.execute("BEGIN IMMEDIATE")
        plan = Plan(conn)
        offers_batch: list[tuple[Any, ...]] = []
        prices_batch: list[tuple[Any, ...]] = []
        offer_sql = f"INSERT INTO offer_history ({', '.join(OFFER_COLUMNS)}) VALUES ({', '.join('?' for _ in OFFER_COLUMNS)})"
        price_sql = f"INSERT INTO price_observations ({', '.join(PRICE_COLUMNS)}) VALUES ({', '.join('?' for _ in PRICE_COLUMNS)})"
        for kind, row in _staged_rows(path):
            if kind == "o" and plan.offer(row):
                offers_batch.append(row)
            elif kind == "p" and plan.price(row):
                prices_batch.append(row)
            elif kind == "c":
                plan.correction(row)
            if len(offers_batch) >= BATCH:
                conn.executemany(offer_sql, offers_batch)
                offers_batch.clear()
            if len(prices_batch) >= BATCH:
                conn.executemany(price_sql, prices_batch)
                prices_batch.clear()
        if offers_batch:
            conn.executemany(offer_sql, offers_batch)
        if prices_batch:
            conn.executemany(price_sql, prices_batch)
        corrections, updates = plan.resolve_corrections(conn)
        for new_price, new_before, row_id, _old_price, _old_before in updates:
            conn.execute("UPDATE offer_history SET price = ?, price_before = ? WHERE id = ?", (new_price, new_before, row_id))
        conn.execute("COMMIT")
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        conn.close()
    stores = _store_list(plan)
    return {
        "stores": stores,
        "corrections": corrections,
        "offers": sum(s["offers"]["new"] for s in stores),
        "prices": sum(s["prices"]["new"] for s in stores),
        "seconds": round(time.monotonic() - started, 2),
    }


# --- klussen ------------------------------------------------------------------------------------------------------


def _jobs_connect() -> sqlite3.Connection:
    auth.ensure_db()
    conn = auth.connect()
    conn.executescript(JOB_SCHEMA)
    return conn


def _job_view(row: sqlite3.Row) -> dict[str, Any]:
    params = json.loads(row["error_params"]) if row["error_params"] else {}
    return {
        "id": row["id"],
        "status": row["status"],
        "error": describe(row["error_key"], params) if row["error_key"] else None,
        "summary": json.loads(row["summary"]) if row["summary"] else None,
        "seconds": row["seconds"],
    }


def _give_up_stale_jobs(conn: sqlite3.Connection) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=JOB_STALE_SECONDS)).isoformat(timespec="seconds")
    conn.execute(
        "UPDATE price_archive_jobs SET status = 'failed', error_key = 'interrupted', finished_at = ? WHERE status = 'running' AND updated_at < ?",
        (_now(), cutoff),
    )


def _finish_job(job_id: str, **fields: Any) -> None:
    fields["updated_at"] = fields["finished_at"] = _now()
    assignments = ", ".join(f"{name} = ?" for name in fields)
    with closing(_jobs_connect()) as conn, conn:
        conn.execute(f"UPDATE price_archive_jobs SET {assignments} WHERE id = ?", (*fields.values(), job_id))


def latest_job() -> dict[str, Any] | None:
    """De klus die loopt, of de laatst afgeronde die de beheerder nog niet heeft weggeklikt."""
    with closing(_jobs_connect()) as conn, conn:
        _give_up_stale_jobs(conn)
        row = conn.execute(
            "SELECT * FROM price_archive_jobs WHERE status = 'running' OR dismissed = 0 ORDER BY started_at DESC, rowid DESC LIMIT 1"
        ).fetchone()
    return _job_view(row) if row else None


def dismiss_job(job_id: str) -> bool:
    with closing(_jobs_connect()) as conn, conn:
        cursor = conn.execute("UPDATE price_archive_jobs SET dismissed = 1 WHERE id = ? AND status != 'running'", (str(job_id),))
    return cursor.rowcount > 0


def start_job(token: str) -> dict[str, Any]:
    """Neemt de klaargezette regels over en zet ze op de achtergrond in de database."""
    path = _stage_path(token)
    if not path.is_file():
        raise ArchiveError("stage_missing", 404)
    running = path.with_name(path.name + ".job")
    job_id = secrets.token_hex(8)
    actor = audit.current_actor()
    with closing(_jobs_connect()) as conn:
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        try:
            _give_up_stale_jobs(conn)
            if conn.execute("SELECT 1 FROM price_archive_jobs WHERE status = 'running'").fetchone():
                raise ArchiveError("busy", 409)
            try:
                os.replace(path, running)  # een token werkt één keer
            except FileNotFoundError:
                raise ArchiveError("stage_missing", 404) from None
            now = _now()
            conn.execute("UPDATE price_archive_jobs SET dismissed = 1 WHERE status != 'running'")
            conn.execute(
                "INSERT INTO price_archive_jobs (id, status, started_at, updated_at) VALUES (?, 'running', ?, ?)", (job_id, now, now)
            )
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    thread = threading.Thread(target=run_job, args=(job_id, running, actor), name=f"price-archive-{job_id}", daemon=True)
    thread.start()
    return {"id": job_id}


def run_job(job_id: str, staged: Path, actor: str = "system") -> None:
    started = time.monotonic()
    try:
        summary = apply(staged)
        _finish_job(job_id, status="done", summary=json.dumps(summary), seconds=round(time.monotonic() - started, 2))
        log.info("Prijsarchief geïmporteerd: %s aanbiedingen, %s prijzen", summary["offers"], summary["prices"])
        audit.record(
            kind="task", service="price-archive", action="import", actor=actor, duration_ms=int((time.monotonic() - started) * 1000),
            detail={"offers": summary["offers"], "prices": summary["prices"], "corrections": summary["corrections"]},
        )
    except Exception:  # noqa: BLE001 - de beheerder krijgt een algemene zin, het logboek de hele fout
        log.exception("Import van een prijsarchief mislukt")
        try:
            _finish_job(job_id, status="failed", error_key="failed", error_params="{}")
        except sqlite3.Error:
            log.exception("Kon de mislukte klus niet vastleggen")
    finally:
        staged.unlink(missing_ok=True)


# --- routes -------------------------------------------------------------------------------------------------------


def _error(exc: ArchiveError):
    return jsonify({"error": describe(exc.key, exc.params), "code": exc.key}), exc.status


def register_routes(app: Flask) -> None:
    # Alleen de beheerder: /api/price-archive staat in auth.ADMIN_PREFIXES.
    @app.get("/api/price-archive")
    def price_archive_info():
        return jsonify({"maxBytes": MAX_UPLOAD_BYTES})

    @app.post("/api/price-archive/stage")
    def price_archive_stage():
        try:
            return jsonify(stage(request.stream, request.content_length))
        except ArchiveError as exc:
            return _error(exc)

    @app.delete("/api/price-archive/stage/<token>")
    def price_archive_unstage(token: str):
        try:
            discard(token)
        except ArchiveError as exc:
            return _error(exc)
        return jsonify({"status": "ok"})

    @app.post("/api/price-archive/jobs")
    def price_archive_start():
        body = request.get_json(silent=True) or {}
        try:
            job = start_job(str(body.get("token") or ""))
        except ArchiveError as exc:
            return _error(exc)
        return jsonify(job), 202

    @app.get("/api/price-archive/jobs/latest")
    def price_archive_latest():
        return jsonify({"job": latest_job()})

    @app.delete("/api/price-archive/jobs/<job_id>")
    def price_archive_dismiss(job_id: str):
        if not dismiss_job(job_id):
            return _error(ArchiveError("unknown_job", 404))
        return jsonify({"status": "ok"})
