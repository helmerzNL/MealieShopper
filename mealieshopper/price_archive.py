"""Prijsarchief importeren (Instellingen > Systeem): historische folder- en winkelprijzen toevoegen aan de Prijsgeschiedenis.

Een prijsarchief is een bestand met prijzen van vóór MealieShopper, bijvoorbeeld het folderarchief van wolph (AH en Jumbo
2009-2026, Picnic 2022-2023, Crisp 2021-2026). Anders dan **Back-up terugzetten** vervangt het niets: het voegt rijen toe aan
`offer_history` (aanbiedingen) en `price_observations` (prijzen), en het verbetert hooguit vooraf beschreven archiefregels.

Drie formaten, die op dezelfde regels uitkomen (`offer`, `price`, `correction`) en dezelfde controle krijgen:

* **Eigen formaat** `mealieshopper-price-archive`, versie 1: een `.jsonl`-bestand (of `.jsonl.gz`) met op de eerste regel een kop
  (`{"format": "mealieshopper-price-archive", "formatVersion": 1, ...}`) en daarna één JSON-object per regel met `type` en de
  kolommen van de tabel. De handleiding beschrijft het formaat. Of als map, zoals **Prijsarchief exporteren** hem maakt
  (price_archive_export.py): een ZIP met `mealieshopper-price-archive.json` (de kop, met `files`) en per winkel en maand een
  `.jsonl`-bestand met alleen regels, ook als de ZIP van een GitHub-repo, waarin alles in één map staat.
* **Het pakket van de gist** zoals **Download ZIP** het geeft: `supermarket-manifest.json` met Base64-delen van
  `supermarket-archives-<datum>.tar.gz`, met daarin `data-manifest.json` en zes gegevensbestanden (`GIST_FILES`). Het losse
  `.tar.gz` mag ook. De app leest alleen die bestanden; de tar wordt nooit uitgepakt en geen script uit het pakket draait.
* **De vorm van data.mealieshopper.nl**: per winkel lijsten van regels in JSON-bestanden in `offers/` en `prices/` (de map zegt
  wat een regel is; zonder `type`). Als ZIP met die documenten (de export daarvoor, de datarepo, de ZIP die de site
  instuurt), als de gist van wolph met `data-site-manifest.json` en Base64-delen van die ZIP (elke controlesom uit het
  manifest telt), of rechtstreeks van de site met **Aanbiedingen ophalen** (`fetch_site`, met de SHA-256 uit index.json).

Elke folderregel (een aanbieding met een product-id `folder:…`) gaat na de controle door het opschonen in price_archive_tidy.py:
koppelen aan de producten die de app zelf kent, en een folderregel die nergens bij hoort komt er niet in. Zo staan er in de
Prijsgeschiedenis geen vreemde producten. Met `?keep_unlinked=1` bij het uploaden blijven die regels (voor een eigen archief).

Het bestand wordt niet vertrouwd. Elke controlesom, elke regel en elke waarde wordt gecontroleerd; één fout weigert het hele
bestand en verandert niets. Wat er al staat gaat altijd voor: een regel die er al is, een eigen prijs op dezelfde dag of een
correctie waarvan de oude waarde niet klopt, wordt overgeslagen en in de samenvatting genoemd.

Twee stappen, zoals bij het terugzetten van een back-up (`backup.py`):

1. `stage` slaat de upload op, controleert alles, schrijft de nieuwe regels genormaliseerd naar een werkbestand en geeft een
   samenvatting. De database verandert niet; het geüploade bestand zelf is daarna weg.
2. `start_job` zet het werkbestand op de achtergrond in de database, in één korte transactie die opnieuw controleert wat er al
   staat (er kan intussen een synchronisatie zijn geweest). Met **Vervangen** (`replace`) gaat in die transactie eerst alles
   eruit wat eerder uit een prijsarchief kwam (ARCHIVE_OFFERS_SQL en ARCHIVE_PRICES_SQL); wat de app zelf ophaalde blijft.

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
import asyncio
import secrets
import sqlite3
import tarfile
import time
import zipfile
import zlib
from collections.abc import AsyncIterable, AsyncIterator
from contextlib import aclosing, asynccontextmanager, closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import IO, Any, Callable, Iterator

import aiosqlite
import httpx
from quart import Quart, Response, jsonify, request

from . import audit, auth, http_client, offers, prices, tasks, thread_db, uploads
from .price_archive_tidy import ARCHIVE_OFFERS_SQL, ARCHIVE_PRICES_SQL, Tidy
from .i18n import tr

log = logging.getLogger(__name__)

FORMAT = "mealieshopper-price-archive"
FORMAT_VERSION = 1
GIST_FORMAT = "supermarket-archive-base64-parts"
GIST_FORMAT_VERSION = 1
GIST_MANIFEST = "supermarket-manifest.json"
OLD_GIST_MANIFEST = "manifest.json"  # het eerdere AH-pakket; het nieuwe pakket bevat dezelfde gegevens, gecorrigeerd
DATA_MANIFEST = "data-manifest.json"
EXPORT_MANIFEST = "mealieshopper-price-archive.json"  # de eigen export als map (price_archive_export.py)
EXPORT_FILE = re.compile(r"^(?:offers|prices|corrections)/[a-z]+/[0-9A-Za-z._-]+\.jsonl$")
MAX_EXPORT_FILES = 5000
MAX_EXPORT_BYTES = 2 * 1024 * 1024 * 1024  # uitgepakt, alle bestanden samen
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
# De vorm van data.mealieshopper.nl: per winkel een collectie met lijsten van regels in `offers/` en `prices/` (de map zegt wat
# een regel is). Zo komen ze van de site, uit de eigen export daarvoor (price_archive_export.py) en uit de gist van wolph, die
# ze als Base64-delen van een ZIP meestuurt met `data-site-manifest.json`.
SITE_FORMAT = "data.mealieshopper.nl"
DATA_SITE_URL = "https://data.mealieshopper.nl"
SITE_MANIFEST = "data-site-manifest.json"
# `observations/`: prijsmetingen zonder bekende actie (de gists van Picnic en Crisp), in de vorm van een prijs.
SITE_KINDS: dict[str, str] = {"offers": "offer", "prices": "price", "observations": "price"}
SITE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.json$")  # een bestandsnaam zoals de site hem toestaat
SITE_COLLECTION = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
SITE_UPLOAD_PATH = re.compile(r"^data/([a-z0-9][a-z0-9-]{0,62})/[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.zip$")
MAX_SITE_DOC_BYTES = 16 * 1024 * 1024  # per document; de site zelf staat 10 MB toe
MAX_CATALOG_BYTES = 5 * 1024 * 1024  # index.json van de site
SITE_TIMEOUT = 60.0
SITE_USER_AGENT = "MealieShopper (+https://www.mealieshopper.nl)"

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
# Een schapprijs uit een archief (`search`: wat een MealieShopper bij het zoeken zag) wordt `archive_search`: een gewone prijs,
# die **Vervangen** herkent als archiefregel en die niet meetelt als product van jou bij het koppelen (price_archive_tidy.py).
ARCHIVE_SEARCH = "archive_search"
PRICE_SOURCES = ("public_press_release", prices.PUBLIC_OBSERVED_UNKNOWN, "search", ARCHIVE_SEARCH)
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

# De klus is klaar: in de transactie van het invoegen (`apply`), ook als de klus bij het afsluiten al onderbroken heette.
JOB_DONE_SQL = (
    "UPDATE price_archive_jobs SET status = 'done', error_key = NULL, error_params = NULL, summary = ?, seconds = ?,"
    " updated_at = ?, finished_at = ? WHERE id = ?"
)

# --- meldingen --------------------------------------------------------------------------------------------------

MESSAGES: dict[str, tuple[str, str]] = {
    "not_an_archive": (
        "Dit is geen prijsarchief. Kies de ZIP van het pakket of van de gist voor data.mealieshopper.nl, het .tar.gz-archief, een export van MealieShopper (ook die voor data.mealieshopper.nl) of een .jsonl-bestand in het formaat mealieshopper-price-archive.",
        "This is not a price archive. Choose the ZIP of the package or of the gist for data.mealieshopper.nl, the .tar.gz archive, an export from MealieShopper (also the one for data.mealieshopper.nl) or a .jsonl file in the mealieshopper-price-archive format.",
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
    "unlisted_file": (
        "{file} staat niet in data-site-manifest.json. Download het pakket opnieuw.",
        "{file} is not listed in data-site-manifest.json. Download the package again.",
    ),
    "too_many_files": ("Het archief heeft meer dan {limit} bestanden.", "The archive has more than {limit} files."),
    "site_unreachable": (
        "data.mealieshopper.nl is niet bereikbaar ({detail}). Probeer het later opnieuw.",
        "data.mealieshopper.nl cannot be reached ({detail}). Try again later.",
    ),
    "site_catalog": (
        "De catalogus van data.mealieshopper.nl (index.json) klopt niet: {detail}.",
        "The catalogue of data.mealieshopper.nl (index.json) is not valid: {detail}.",
    ),
    "site_checksum": (
        "{file} op data.mealieshopper.nl klopt niet met de catalogus. Probeer het later opnieuw.",
        "{file} on data.mealieshopper.nl does not match the catalogue. Try again later.",
    ),
    "site_empty": (
        "Op data.mealieshopper.nl staan nog geen aanbiedingen of prijzen om op te halen.",
        "There are no offers or prices on data.mealieshopper.nl to fetch yet.",
    ),
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
        "bron {value} is niet toegestaan (alleen public_press_release, public_observed_unknown, search of archive_search)",
        "source {value} is not allowed (only public_press_release, public_observed_unknown, search or archive_search)",
    ),
    "personal": ("een archief kan geen persoonlijke aanbieding (Bonus Box) bevatten", "an archive cannot contain a personal offer (Bonus Box)"),
    "seen_at": ("seen_at valt niet op day", "seen_at is not on day"),
    "list": ("het document is geen lijst van regels", "the document is not a list of lines"),
    "duplicate_key": ("de sleutel {value} staat twee keer in hetzelfde object", "the key {value} appears twice in the same object"),
    "store_folder": ("winkel {value} hoort niet in deze map", "store {value} does not belong in this folder"),
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
    url = _url(row, "url")  # Picnic en Crisp hebben geen webwinkel, dus hun aanbiedingen geen adres
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
    stored = ARCHIVE_SEARCH if source in ("search", ARCHIVE_SEARCH) else str(source)
    return (store, product_id, day, stored, title, brand, unit_size, "", url, price, seen_at)


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


class _DuplicateKey(ValueError):
    def __init__(self, key: str):
        super().__init__(key)
        self.key = key


def _strict_json(raw: bytes) -> Any:
    """Een document van data.mealieshopper.nl, zo streng als de site het leest: UTF-8 (een BOM mag), geen NaN of Infinity, en
    geen sleutel twee keer in hetzelfde object (die zou ongemerkt verdwijnen)."""
    def reject(constant: str) -> Any:
        raise ValueError(constant)

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise _DuplicateKey(key)
            result[key] = value
        return result

    try:
        return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=pairs, parse_constant=reject)
    except _DuplicateKey as exc:
        raise RowError("duplicate_key", value=exc.key) from None
    except (ValueError, RecursionError):  # UnicodeDecodeError is een ValueError
        raise RowError("json") from None


def site_document(name: str) -> tuple[str, str | None] | None:
    """(soort, winkel van de map) als `name` een document in de vorm van data.mealieshopper.nl is, anders None.

    Een document staat direct in een map `offers/` of `prices/`, ergens in de ZIP: `offers/2015-001.json` (de ZIP uit de gist),
    `data/ah/offers/2015-001.json` (de export en de datarepo, ook met een map erboven) of `ah/offers/2015-001.json` (de site).
    Staat er een winkel boven die map, dan hoort elke regel bij die winkel."""
    parts = name.split("/")
    if len(parts) < 2 or parts[-2] not in SITE_KINDS or not SITE_NAME.match(parts[-1]):
        return None
    if any(part in ("", ".", "..") or "\\" in part for part in parts):
        return None
    store = parts[-3] if len(parts) >= 3 and parts[-3] in STORES else None
    return SITE_KINDS[parts[-2]], store


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
            if self._export_manifest() is not None:
                return "export"
            names = self._zip_names()
            if any(_near_top(name, SITE_MANIFEST) for name in names):
                return "site-gist"
            if any(_near_top(name, GIST_MANIFEST) or _near_top(name, OLD_GIST_MANIFEST) for name in names):
                return "zip"
            if any(site_document(name) for name in names):
                return "site"
            return "zip"  # geeft de melding van het pakket (geen prijsarchief)
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
        if self.kind == "export":
            yield from self._from_export()
            return
        if self.kind == "site":
            self._site_meta()
            yield from self._from_site(self.path)
            return
        if self.kind == "site-gist":
            yield from self._from_site_gist()
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

    # De eigen export als map (price_archive_export.py): de kop in mealieshopper-price-archive.json, de regels per bestand.
    def _export_manifest(self) -> str | None:
        """Het pad van mealieshopper-price-archive.json in de ZIP (bovenin, of één map diep zoals in de ZIP van een GitHub-repo)."""
        try:
            with zipfile.ZipFile(self.path) as archive:
                found = [info.filename for info in archive.infolist()
                         if not info.is_dir() and PurePosixPath(info.filename).name == EXPORT_MANIFEST
                         and len(PurePosixPath(info.filename).parts) <= 2]
        except (zipfile.BadZipFile, OSError):
            raise ArchiveError("not_an_archive") from None
        return found[0] if len(found) == 1 else None

    def _from_export(self) -> Iterator[tuple[str, dict[str, Any], str, int]]:
        manifest_name = self._export_manifest()
        if manifest_name is None:
            raise ArchiveError("not_an_archive")
        folder = str(PurePosixPath(manifest_name).parent)
        prefix = "" if folder == "." else folder + "/"
        try:
            with zipfile.ZipFile(self.path) as archive:
                by_name = {info.filename: info for info in archive.infolist() if not info.is_dir()}
                with archive.open(by_name[manifest_name]) as handle:
                    try:
                        header = json.loads(_bounded(handle, MAX_MANIFEST_BYTES))
                    except ValueError:
                        raise ArchiveError("bad_structure", detail=EXPORT_MANIFEST) from None
                self._own_header(header)
                files = header.get("files")
                if (not isinstance(files, list) or len(files) > MAX_EXPORT_FILES or len(set(map(str, files))) != len(files)
                        or not all(isinstance(name, str) and EXPORT_FILE.match(name) for name in files)):
                    raise ArchiveError("bad_structure", detail=f"{EXPORT_MANIFEST}: files")
                infos = []
                for name in files:
                    info = by_name.get(prefix + name)
                    if info is None:
                        raise ArchiveError("missing_file", file=name)
                    infos.append((name, info))
                # De opgegeven grootte is een echte grens: zipfile leest nooit meer dan file_size (en controleert de CRC).
                if sum(info.file_size for _, info in infos) > MAX_EXPORT_BYTES:
                    raise ArchiveError("too_large", 413, limit=MAX_EXPORT_BYTES // (1024 * 1024))
                for name, info in infos:
                    number = 0
                    try:
                        with archive.open(info) as handle:
                            for number, raw in _lines(handle):
                                data = _json_line(raw)
                                if not isinstance(data, dict):
                                    raise RowError("object")
                                kind = data.get("type")
                                if kind not in ("offer", "price", "correction"):
                                    raise RowError("type", "type", kind)
                                yield str(kind), data, name, number
                    except RowError as exc:
                        raise ArchiveError("bad_row", file=name, line=number or "?", reason=exc.reason, field=exc.field, value=exc.value) from None
        except (zipfile.BadZipFile, zlib.error, OSError, EOFError):
            raise ArchiveError("bad_structure", detail="zip") from None

    def _zip_names(self) -> list[str]:
        try:
            with zipfile.ZipFile(self.path) as archive:
                return [info.filename for info in archive.infolist() if not info.is_dir()]
        except (zipfile.BadZipFile, OSError):
            raise ArchiveError("not_an_archive") from None

    # De ZIP van de gist: Base64-delen naar één .tar.gz, met de controlesommen uit supermarket-manifest.json.
    def _from_zip(self) -> Path:
        try:
            archive = zipfile.ZipFile(self.path)
        except (zipfile.BadZipFile, OSError):
            raise ArchiveError("not_an_archive") from None
        with archive:
            entries = [info for info in archive.infolist() if not info.is_dir()]
            by_name = {info.filename: info for info in entries}
            manifests = [info for info in entries if _near_top(info.filename, GIST_MANIFEST)]
            if len(manifests) != 1:
                if not manifests and any(_near_top(info.filename, OLD_GIST_MANIFEST) for info in entries):
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
            return self._join_parts(
                archive, by_name, folder, [(name, expected, None) for name, expected in parts],
                manifest["archiveBytes"], manifest["archiveSha256"], manifest["archiveFile"], ".tar.gz",
            )

    def _join_parts(
        self, archive: zipfile.ZipFile, by_name: dict[str, zipfile.ZipInfo], folder: PurePosixPath,
        parts: list[tuple[str, str, int | None]], size: int, sha256: str, label: str, suffix: str,
    ) -> Path:
        """Zet de Base64-delen (naam, SHA-256, eventueel lengte) achter elkaar in een werkbestand en controleert elk deel en het
        geheel (`size` bytes met SHA-256 `sha256`). Geeft het pad; bij een fout blijft er niets staan."""
        self.work_dir.mkdir(parents=True, exist_ok=True)
        target = self.work_dir / f"{STAGE_PREFIX}{secrets.token_hex(8)}{suffix}"
        digest = hashlib.sha256()
        total = 0
        try:
            with target.open("wb") as out:
                os.chmod(target, 0o600)
                for name, expected, length in parts:
                    info = by_name.get(name if str(folder) == "." else f"{folder}/{name}")
                    if info is None:
                        raise ArchiveError("missing_file", file=name)
                    if info.file_size > MAX_PART_BYTES:
                        raise ArchiveError("too_large", 413, limit=MAX_PART_BYTES // (1024 * 1024))
                    with archive.open(info) as handle:
                        content = _bounded(handle, MAX_PART_BYTES)
                    if (length is not None and len(content) != length) or hashlib.sha256(content).hexdigest() != expected:
                        raise ArchiveError("checksum", file=name)
                    try:
                        raw = base64.b64decode(content.strip(), validate=True)
                    except (binascii.Error, ValueError):
                        raise ArchiveError("bad_structure", detail=name) from None
                    total += len(raw)
                    if total > size:
                        raise ArchiveError("checksum", file=label)
                    digest.update(raw)
                    out.write(raw)
            if total != size or digest.hexdigest() != sha256:
                raise ArchiveError("checksum", file=label)
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

    # De vorm van data.mealieshopper.nl: lijsten van regels in offers/ en prices/ (zie `site_document`).
    def _from_site(
        self, path: Path, documents: dict[str, dict[str, Any]] | None = None, prefix: str = "", store: str | None = None,
    ) -> Iterator[tuple[str, dict[str, Any], str, int]]:
        """De regels van elk document in de ZIP `path`, op naam gesorteerd. Met `documents` (uit data-site-manifest.json, op
        `prefix` + naam in de ZIP) moet elk genoemd document er zijn met dezelfde lengte, SHA-256 en hetzelfde aantal regels, en
        geen ander. Met `store` hoort elke regel bij die winkel, net als bij een winkel in het pad."""
        name = ""
        number = 0
        try:
            with zipfile.ZipFile(path) as archive:
                found: list[tuple[str, zipfile.ZipInfo, str, str | None]] = []
                for info in archive.infolist():
                    document = None if info.is_dir() else site_document(info.filename)
                    if document is not None:
                        found.append((prefix + info.filename, info, *document))
                found.sort(key=lambda item: item[0])
                if len(found) > MAX_EXPORT_FILES:
                    raise ArchiveError("too_many_files", limit=MAX_EXPORT_FILES)
                # De opgegeven grootte is een echte grens: zipfile leest nooit meer dan file_size (en controleert de CRC).
                if sum(item[1].file_size for item in found) > MAX_EXPORT_BYTES:
                    raise ArchiveError("too_large", 413, limit=MAX_EXPORT_BYTES // (1024 * 1024))
                if documents is not None:
                    names = {item[0] for item in found}
                    for wanted in documents:
                        if wanted not in names:
                            raise ArchiveError("missing_file", file=wanted)
                    for item in found:
                        if item[0] not in documents:
                            raise ArchiveError("unlisted_file", file=item[0])
                elif not found:
                    raise ArchiveError("not_an_archive")
                for name, info, kind, folder_store in found:
                    number = 0
                    if info.file_size > MAX_SITE_DOC_BYTES:
                        raise ArchiveError("too_large", 413, limit=MAX_SITE_DOC_BYTES // (1024 * 1024))
                    with archive.open(info) as handle:
                        raw = _bounded(handle, MAX_SITE_DOC_BYTES)
                    expected = documents.get(name) if documents is not None else None
                    if expected is not None and (len(raw) != expected["bytes"] or hashlib.sha256(raw).hexdigest() != expected["sha256"]):
                        raise ArchiveError("checksum", file=name)
                    data = _strict_json(raw)
                    if not isinstance(data, list):
                        raise RowError("list")
                    if expected is not None and expected["items"] is not None and len(data) != expected["items"]:
                        raise ArchiveError("checksum", file=name)
                    owner = store or folder_store
                    for number, item in enumerate(data, 1):
                        if not isinstance(item, dict):
                            raise RowError("object")
                        if owner is not None and isinstance(item.get("store"), str) and item["store"] != owner:
                            raise RowError("store_folder", "store", item["store"])
                        yield kind, item, name, number
        except RowError as exc:
            raise ArchiveError("bad_row", file=name, line=number or "?", reason=exc.reason, field=exc.field, value=exc.value) from None
        except (zipfile.BadZipFile, zlib.error, OSError, EOFError, NotImplementedError):
            raise ArchiveError("bad_structure", detail="zip") from None

    def _site_meta(self) -> None:
        """De bron in de samenvatting: data.mealieshopper.nl met de commit uit index.json, als die in de ZIP staat (zoals bij
        **Aanbiedingen ophalen** en de branch `publicatie`)."""
        meta = {"format": SITE_FORMAT, "source": "", "createdAt": "", "license": ""}
        try:
            with zipfile.ZipFile(self.path) as archive:
                found = [info for info in archive.infolist() if not info.is_dir() and _near_top(info.filename, "index.json")]
                if len(found) == 1:
                    with archive.open(found[0]) as handle:
                        catalog = json.loads(_bounded(handle, MAX_CATALOG_BYTES))
                    if isinstance(catalog, dict) and catalog.get("site") == DATA_SITE_URL:
                        commit = str(catalog.get("commit") or "")[:7]
                        meta["source"] = f"{SITE_FORMAT} ({commit})" if commit else SITE_FORMAT
                        meta["createdAt"] = str(catalog.get("generated_at") or "")[:40]
                        license_ = catalog.get("license")
                        meta["license"] = str(license_.get("name") or "")[:200] if isinstance(license_, dict) else ""
        except (zipfile.BadZipFile, zlib.error, OSError, EOFError, ValueError, ArchiveError):
            pass
        self.meta = meta

    # De gist voor data.mealieshopper.nl: per winkel Base64-delen van de ZIP die je op de site instuurt.
    def _from_site_gist(self) -> Iterator[tuple[str, dict[str, Any], str, int]]:
        try:
            archive = zipfile.ZipFile(self.path)
        except (zipfile.BadZipFile, OSError):
            raise ArchiveError("not_an_archive") from None
        with archive:
            entries = [info for info in archive.infolist() if not info.is_dir()]
            by_name = {info.filename: info for info in entries}
            manifests = [info for info in entries if _near_top(info.filename, SITE_MANIFEST)]
            if len(manifests) != 1:
                raise ArchiveError("not_an_archive")
            folder = PurePosixPath(manifests[0].filename).parent
            try:
                with archive.open(manifests[0]) as handle:
                    manifest = json.loads(_bounded(handle, MAX_MANIFEST_BYTES))
            except ValueError:
                raise ArchiveError("bad_structure", detail=SITE_MANIFEST) from None
            except (zipfile.BadZipFile, zlib.error, OSError, EOFError):
                raise ArchiveError("bad_structure", detail="zip") from None
            packages = self._site_manifest(manifest)
            self.meta = {
                "format": SITE_FORMAT,
                "source": ", ".join(package["uploadPath"] for package in packages)[:200],
                "createdAt": str(manifest.get("created") or "")[:40],
                "license": "",
            }
            for package in packages:
                target = self._join_parts(
                    archive, by_name, folder, package["parts"], package["zipBytes"], package["sha256"],
                    package["uploadPath"], ".zip",
                )
                try:
                    yield from self._from_site(target, package["documents"], prefix=package["store"] + "/", store=package["store"])
                finally:
                    target.unlink(missing_ok=True)

    @staticmethod
    def _site_manifest(manifest: Any) -> list[dict[str, Any]]:
        """De pakketten uit data-site-manifest.json, elk gecontroleerd: winkel, uploadpad, de ZIP en zijn delen, en de documenten
        die erin horen (met lengte, SHA-256 en aantal regels)."""
        packages = manifest.get("packages") if isinstance(manifest, dict) else None
        if not isinstance(packages, list) or not 0 < len(packages) <= len(STORES):
            raise ArchiveError("bad_structure", detail=f"{SITE_MANIFEST}: packages")

        def bad(field: str) -> ArchiveError:
            return ArchiveError("bad_structure", detail=f"{SITE_MANIFEST}: {field}")

        def whole(value: Any, lowest: int, highest: int) -> bool:
            return isinstance(value, int) and not isinstance(value, bool) and lowest <= value <= highest

        result: list[dict[str, Any]] = []
        part_names: set[str] = set()
        stores: set[str] = set()
        for package in packages:
            if not isinstance(package, dict):
                raise bad("packages")
            store = package.get("store")
            upload = package.get("uploadPath")
            match = SITE_UPLOAD_PATH.match(upload) if isinstance(upload, str) else None
            if store not in STORES or match is None or match[1] != store or store in stores:
                raise bad("uploadPath")
            stores.add(store)
            if not whole(package.get("zipBytes"), 1, MAX_MEMBER_BYTES):
                raise bad("zipBytes")
            if not SHA256_PATTERN.match(str(package.get("sha256") or "")):
                raise bad("sha256")
            parts = package.get("parts")
            if not isinstance(parts, list) or not 0 < len(parts) <= MAX_PARTS:
                raise bad("parts")
            joined: list[tuple[str, str, int | None]] = []
            for part in parts:
                if not isinstance(part, dict) or not isinstance(part.get("file"), str) or not _bare(part["file"]):
                    raise bad("parts")
                if part["file"] in part_names or not whole(part.get("bytes"), 1, MAX_PART_BYTES):
                    raise bad("parts")
                if not SHA256_PATTERN.match(str(part.get("sha256") or "")):
                    raise bad("parts")
                part_names.add(part["file"])
                joined.append((part["file"], part["sha256"], part["bytes"]))
            documents = package.get("documents")
            if not isinstance(documents, list) or not 0 < len(documents) <= MAX_EXPORT_FILES:
                raise bad("documents")
            listed: dict[str, dict[str, Any]] = {}
            for document in documents:
                path = document.get("path") if isinstance(document, dict) else None
                if not isinstance(path, str) or not path.startswith(f"{store}/") or path in listed:
                    raise bad("documents")
                inner = site_document(path[len(store) + 1:])
                if inner is None or inner[1] is not None:  # direct in offers/ of prices/ van de ZIP
                    raise bad("documents")
                items = document.get("items")
                if not whole(document.get("bytes"), 0, MAX_SITE_DOC_BYTES) or not SHA256_PATTERN.match(str(document.get("sha256") or "")):
                    raise bad("documents")
                if items is not None and not whole(items, 0, MAX_RECORDS):
                    raise bad("documents")
                listed[path] = {"bytes": document["bytes"], "sha256": document["sha256"], "items": items}
            result.append({
                "store": store, "uploadPath": upload, "zipBytes": package["zipBytes"], "sha256": package["sha256"],
                "parts": joined, "documents": listed,
            })
        return result

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


def _near_top(name: str, wanted: str) -> bool:
    """`name` is het bestand `wanted`, bovenin de ZIP of één map diep (zoals in de ZIP van GitHub)."""
    path = PurePosixPath(name)
    return path.name == wanted and len(path.parts) <= 2


def _bare(name: str) -> bool:
    return bool(name) and len(name) <= 200 and "/" not in name and "\\" not in name and name not in (".", "..") and not CONTROL.search(name)


# --- vergelijken met wat er staat ---------------------------------------------------------------------------------


class Plan:
    """Wat een archief zou toevoegen aan de database zoals die nu is: per winkel nieuw, al aanwezig of overgeslagen.

    De regels voor dubbel komen uit wolphs `insert_rows`: een aanbieding met dezelfde winkel, url en looptijd is er al, en een
    aanbieding die de app zelf al kent (zelfde product en looptijd) ook. Een prijs met dezelfde winkel, product en dag is er al;
    staat er een andere prijs, dan gaat die van de app voor.

    Met `without_archive` telt alleen wat de app zelf ophaalde: zo ziet het eruit als de archiefregels eerst verdwijnen
    (**Vervangen**).
    """

    def __init__(self, conn: sqlite3.Connection, without_archive: bool = False):
        self.offer_urls: set[bytes] = set()
        self.offer_products: set[bytes] = set()
        self.max_offer_id = 0
        self.without_archive = without_archive
        offers_where = f" WHERE NOT {ARCHIVE_OFFERS_SQL}" if without_archive else ""
        prices_where = f" WHERE NOT {ARCHIVE_PRICES_SQL}" if without_archive else ""
        for row in conn.execute(f"SELECT id, store, product_id, url, valid_from, valid_until FROM offer_history{offers_where}"):
            self.max_offer_id = max(self.max_offer_id, int(row["id"]))
            if row["url"]:
                self.offer_urls.add(_key(row["store"], row["url"], row["valid_from"], row["valid_until"]))
            self.offer_products.add(_key(row["store"], row["product_id"], row["valid_from"], row["valid_until"]))
        self.prices: dict[bytes, float] = {
            _key(row["store"], row["product_id"], row["day"]): float(row["price"])
            for row in conn.execute(f"SELECT store, product_id, day, price FROM price_observations{prices_where}")
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
        by_url = _key(store, url, valid_from, valid_until) if url else None
        by_product = _key(store, product_id, valid_from, valid_until)
        if by_url is not None and by_url in self.offer_urls:
            stats["offers"]["duplicate"] += 1
            return False
        if by_product in self.offer_products:
            stats["offers"]["duplicate" if by_url is None else "covered"] += 1
            return False
        if by_url is not None:
            self.offer_urls.add(by_url)
        else:
            self.offer_products.add(by_product)  # zonder adres: hetzelfde product met dezelfde looptijd is dezelfde aanbieding
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
        archive = f" AND NOT {ARCHIVE_OFFERS_SQL}" if self.without_archive else ""
        for row in conn.execute(f"SELECT id, store, product_id, url, price, price_before FROM offer_history WHERE id <= ? AND url IS NOT NULL{archive}", (self.max_offer_id,)):
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


def check(archive: Archive, plans: list[Plan], write: Callable[[str, tuple[Any, ...]], None] | None = None, tidy: Tidy | None = None) -> int:
    """Leest en controleert het hele archief tegen elk `plan`; `write` krijgt elke geldige regel. Geeft het aantal regels.

    Met `tidy` gaat elke gecontroleerde aanbieding en prijs door het opschonen (price_archive_tidy.py): koppelen aan je eigen
    producten, en een folderregel of archiefprijs die nergens bij hoort eruit.
    """
    bounds = Today()
    count = 0
    for kind, data, name, number in archive.records():
        count += 1
        if count > MAX_RECORDS:
            raise ArchiveError("too_many", limit=MAX_RECORDS)
        try:
            if kind == "offer":
                row = normalise_offer(data, bounds)  # eerst controleren: ook een regel die wegvalt, moet kloppen
                if tidy is not None:
                    data = tidy.offer(data)
                    if data is None:
                        continue
                    row = normalise_offer(data, bounds)
                for plan in plans:
                    plan.offer(row)
                if write:
                    write("o", row)
            elif kind == "price":
                row = normalise_price(data, bounds)
                if tidy is not None:
                    data = tidy.price(data)
                    if data is None:
                        continue
                    row = normalise_price(data, bounds)
                for plan in plans:
                    plan.price(row)
                if write:
                    write("p", row)
            else:
                correction = normalise_correction(data)
                for plan in plans:
                    plan.correction(correction)
        except RowError as exc:
            raise ArchiveError("bad_row", file=name, line=number, reason=exc.reason, field=exc.field, value=exc.value) from None
    if not count:
        raise ArchiveError("empty")
    return count


def _store_list(plan: Plan) -> list[dict[str, Any]]:
    """Per winkel, in de volgorde van de app (een JSON-object zou Flask op naam sorteren)."""
    return [{"store": store, **plan.stores[store]} for store in STORES if store in plan.stores]


def archived(conn: sqlite3.Connection) -> dict[str, int]:
    """Hoeveel aanbiedingen en prijzen er nu uit een prijsarchief komen (wat **Vervangen** eerst weghaalt)."""
    return {
        "offers": conn.execute(f"SELECT COUNT(*) FROM offer_history WHERE {ARCHIVE_OFFERS_SQL}").fetchone()[0],
        "prices": conn.execute(f"SELECT COUNT(*) FROM price_observations WHERE {ARCHIVE_PRICES_SQL}").fetchone()[0],
    }


def _outcome(plan: Plan, corrections: dict[str, int]) -> dict[str, Any]:
    stores = _store_list(plan)
    return {
        "stores": stores,
        "corrections": corrections,
        "new": sum(s["offers"]["new"] + s["prices"]["new"] for s in stores) + corrections["apply"],
    }


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


async def ensure_schema() -> None:
    """De tabellen van de aanbiedingen en prijzen, zodat een lege installatie ze ook heeft (`prices._connect` maakt ze).

    Op de event loop, voordat een fase naar haar thread gaat: de thread werkt daarna alleen met `thread_db.connect`. Controleren, invoegen en
    exporteren lezen of schrijven tot een miljoen regels in een transactie: dat is bulkwerk, zoals de fasen van de back-up."""
    async with prices._connect():
        pass


async def stage(body: AsyncIterable[bytes], content_length: int | None, keep_unlinked: bool = False) -> dict[str, Any]:
    """Slaat de upload op, controleert alles en zet de nieuwe regels klaar. De database verandert niet.

    Het lichaam komt in stukken binnen (`request.body`) en gaat zo naar schijf, nooit helemaal in het geheugen."""
    limit: int = MAX_UPLOAD_BYTES // (1024 * 1024)
    if content_length is not None and content_length > MAX_UPLOAD_BYTES:
        raise ArchiveError("too_large", 413, limit=limit)
    directory: Path = auth.data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    remove_old_staged_files()
    token: str = secrets.token_hex(16)
    upload: Path = _upload_path(token)
    received: int = 0
    try:
        with upload.open("wb") as handle:
            os.chmod(upload, 0o600)
            async for chunk in body:
                received += len(chunk)
                if received > MAX_UPLOAD_BYTES:
                    raise ArchiveError("too_large", 413, limit=limit)
                handle.write(chunk)  # lokale schijf en stukken van hoogstens een paar MB: dat mag op de loop
    except BaseException:
        upload.unlink(missing_ok=True)
        raise
    return await _check(token, upload, keep_unlinked)


async def stage_upload(token: str, size: Any, keep_unlinked: bool = False) -> dict[str, Any]:
    """Als `stage`, voor een archief dat in stukken binnenkwam (uploads.py): het krijgt hetzelfde token."""
    remove_old_staged_files()
    upload: Path = _upload_path(token)
    (await uploads.take(STAGE_PREFIX, token, size)).replace(upload)
    return await _check(token, upload, keep_unlinked)


async def _check(token: str, upload: Path, keep_unlinked: bool) -> dict[str, Any]:
    """`_check_upload` als geheel in een thread: uitpakken, lezen en vergelijken is reken- en bulkwerk."""
    try:
        await ensure_schema()
    except BaseException:
        upload.unlink(missing_ok=True)
        raise
    return await asyncio.to_thread(_check_upload, token, upload, keep_unlinked)


# --- ophalen van data.mealieshopper.nl ------------------------------------------------------------------------------


def site_files(catalog: Any) -> list[tuple[str, int, str]]:
    """De documenten om op te halen uit index.json van de site, als (pad, lengte, SHA-256).

    Alleen collecties met de naam van een winkel, en daarin alleen `<winkel>/offers/<naam>.json` en `<winkel>/prices/<naam>.json`:
    de rest van de site is voor iets anders. Het adres maakt de app zelf uit DATA_SITE_URL en het pad; de `url` uit de catalogus
    volgt hij niet. Een document dat erbij hoort maar geen geldige lengte of SHA-256 heeft, weigert de hele catalogus."""
    collections = catalog.get("collections") if isinstance(catalog, dict) else None
    if not isinstance(collections, list):
        raise ArchiveError("site_catalog", 502, detail="collections")
    found: list[tuple[str, int, str]] = []
    seen: set[str] = set()
    total = 0
    for collection in collections:
        if not isinstance(collection, dict) or collection.get("name") not in STORES:
            continue
        store = str(collection["name"])
        files = collection.get("files")
        if not isinstance(files, list):
            raise ArchiveError("site_catalog", 502, detail=f"{store}: files")
        for entry in files:
            path = entry.get("path") if isinstance(entry, dict) else None
            parts = path.split("/") if isinstance(path, str) else []
            if len(parts) != 3 or parts[0] != store or parts[1] not in SITE_KINDS or not SITE_NAME.match(parts[2]):
                continue
            size, digest = entry.get("bytes"), entry.get("sha256")
            if isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= MAX_SITE_DOC_BYTES:
                raise ArchiveError("site_catalog", 502, detail=f"{path}: bytes")
            if not isinstance(digest, str) or not SHA256_PATTERN.match(digest) or path.lower() in seen:
                raise ArchiveError("site_catalog", 502, detail=f"{path}: sha256")
            seen.add(path.lower())
            total += size
            found.append((path, size, digest))
    if len(found) > MAX_EXPORT_FILES:
        raise ArchiveError("too_many_files", limit=MAX_EXPORT_FILES)
    if total > MAX_EXPORT_BYTES:
        raise ArchiveError("too_large", 413, limit=MAX_EXPORT_BYTES // (1024 * 1024))
    if not found:
        raise ArchiveError("site_empty", 404)
    return sorted(found)


def _site_reason(exc: httpx.HTTPError) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    return type(exc).__name__


async def _site_get(path: str, limit: int) -> bytes:
    """Eén bestand van de site, hoogstens `limit` bytes. Geen doorverwijzing: alleen data.mealieshopper.nl zelf."""
    client: httpx.AsyncClient = http_client.client(
        "data-site", headers={"User-Agent": SITE_USER_AGENT}, timeout=SITE_TIMEOUT, follow_redirects=False
    )
    chunks: list[bytes] = []
    received = 0
    try:
        async with client.stream("GET", f"{DATA_SITE_URL}/{path}") as response:
            if response.status_code != 200:
                raise ArchiveError("site_unreachable", 502, detail=f"HTTP {response.status_code}")
            async for chunk in response.aiter_bytes():
                received += len(chunk)
                if received > limit:
                    raise ArchiveError("site_checksum", 502, file=path)
                chunks.append(chunk)
    except httpx.HTTPError as exc:
        raise ArchiveError("site_unreachable", 502, detail=_site_reason(exc)) from None
    return b"".join(chunks)


def _site_zip(path: Path) -> zipfile.ZipFile:
    archive = zipfile.ZipFile(path, "w", zipfile.ZIP_STORED, allowZip64=True)
    os.chmod(path, 0o600)
    return archive


async def fetch_site(keep_unlinked: bool = False) -> dict[str, Any]:
    """**Aanbiedingen ophalen**: de aanbiedingen en prijzen van data.mealieshopper.nl, klaargezet zoals een upload.

    De catalogus (index.json) zegt welke documenten er zijn, met hun lengte en SHA-256. Elk document wordt opgehaald en
    gecontroleerd, en komt als `data/<winkel>/offers|prices/<naam>.json` in een ZIP, met index.json erbij voor de bron. Die ZIP
    krijgt daarna precies de controle van een geüpload archief (`_check`). Klopt er iets niet, dan blijft er niets staan."""
    remove_old_staged_files()
    raw_catalog: bytes = await _site_get("index.json", MAX_CATALOG_BYTES)
    try:
        catalog: Any = json.loads(raw_catalog)
    except ValueError:
        raise ArchiveError("site_catalog", 502, detail="JSON") from None
    files: list[tuple[str, int, str]] = site_files(catalog)
    auth.data_dir().mkdir(parents=True, exist_ok=True)
    token: str = secrets.token_hex(16)
    upload: Path = _upload_path(token)
    try:
        archive: zipfile.ZipFile = await asyncio.to_thread(_site_zip, upload)
        try:
            await asyncio.to_thread(archive.writestr, "index.json", raw_catalog)
            for path, size, digest in files:
                body: bytes = await _site_get(path, size)
                if len(body) != size or hashlib.sha256(body).hexdigest() != digest:
                    raise ArchiveError("site_checksum", 502, file=path)
                await asyncio.to_thread(archive.writestr, f"data/{path}", body)  # tot 16 MB naar schijf
        finally:
            await asyncio.to_thread(archive.close)
    except BaseException:
        upload.unlink(missing_ok=True)
        raise
    return await _check(token, upload, keep_unlinked)


def _upload_path(token: str) -> Path:
    _stage_path(token)  # controleert het token
    return auth.data_dir() / f"{STAGE_PREFIX}{token}.upload"


def _check_upload(token: str, upload: Path, keep_unlinked: bool = False) -> dict[str, Any]:
    """Controleert het geüploade archief en zet de nieuwe regels klaar; het archief zelf gaat daarna weg.

    Met `keep_unlinked` blijven folderregels die bij geen product van jou horen (price_archive_tidy.Tidy).
    """
    directory = auth.data_dir()
    rows_path = _stage_path(token)
    try:
        if not upload.stat().st_size:
            raise ArchiveError("not_an_archive")
        archive = Archive(upload, directory)
        with closing(thread_db.connect()) as conn:
            plan = Plan(conn)
            replacing = Plan(conn, without_archive=True)
            tidy = Tidy(conn, keep_unlinked=keep_unlinked)
            with rows_path.open("w", encoding="utf-8") as out:
                os.chmod(rows_path, 0o600)
                lines = check(archive, [plan, replacing], lambda kind, row: out.write(json.dumps([kind, list(row)], ensure_ascii=False) + "\n"), tidy)
            corrections, _ = plan.resolve_corrections(conn)
            replace_corrections, _ = replacing.resolve_corrections(conn)
            with rows_path.open("a", encoding="utf-8") as out:
                for row in plan.corrections:
                    out.write(json.dumps(["c", list(row)], ensure_ascii=False) + "\n")
            existing = archived(conn)
        summary = summarize(archive, plan, corrections, lines)
        summary["archived"] = existing
        summary["tidied"] = dict(tidy.stats)
        summary["keepUnlinked"] = keep_unlinked
        summary["replace"] = _outcome(replacing, replace_corrections)
    except BaseException:
        rows_path.unlink(missing_ok=True)
        raise
    finally:
        upload.unlink(missing_ok=True)  # het bestand zelf hebben we niet meer nodig; de nieuwe regels staan klaar
    return {"token": token, **summary}


async def discard(token: str) -> None:
    _stage_path(token).unlink(missing_ok=True)


# --- importeren ---------------------------------------------------------------------------------------------------


def _staged_rows(path: Path) -> Iterator[tuple[str, tuple[Any, ...]]]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            kind, values = json.loads(line)
            yield kind, tuple(values)


def apply(
    path: Path, replace: bool = False, on_commit: Callable[[sqlite3.Connection, dict[str, Any]], None] | None = None
) -> dict[str, Any]:
    """Zet de klaargezette regels in één transactie in de database, na een nieuwe vergelijking met wat er dan staat.

    Met `replace` gaat eerst alles eruit wat eerder uit een prijsarchief kwam (ARCHIVE_OFFERS_SQL en ARCHIVE_PRICES_SQL), in
    dezelfde transactie: mislukt er iets, dan staat alles er nog. `on_commit` krijgt de verbinding en de samenvatting vlak voor
    de COMMIT, in dezelfde transactie (de klus legt daar vast dat hij klaar is, zoals `backup.replace_everything`). Voor
    `asyncio.to_thread`, na `ensure_schema`.
    """
    started = time.monotonic()
    conn = thread_db.connect()
    conn.isolation_level = None  # we sturen BEGIN en COMMIT zelf
    try:
        conn.execute("PRAGMA busy_timeout = 30000")  # een lopende synchronisatie mag even afmaken
        conn.execute("PRAGMA cache_size = -65536")  # zo houden andere verbindingen langer leestoegang
        conn.execute("BEGIN IMMEDIATE")
        removed = {"offers": 0, "prices": 0}
        if replace:
            removed["offers"] = conn.execute(f"DELETE FROM offer_history WHERE {ARCHIVE_OFFERS_SQL}").rowcount
            removed["prices"] = conn.execute(f"DELETE FROM price_observations WHERE {ARCHIVE_PRICES_SQL}").rowcount
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
        stores = _store_list(plan)
        summary: dict[str, Any] = {
            "stores": stores,
            "corrections": corrections,
            "offers": sum(s["offers"]["new"] for s in stores),
            "prices": sum(s["prices"]["new"] for s in stores),
            "replaced": replace,
            "removed": removed,
            "seconds": round(time.monotonic() - started, 2),
        }
        if on_commit is not None:
            on_commit(conn, summary)
        conn.execute("COMMIT")
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        conn.close()
    return summary


# --- klussen ------------------------------------------------------------------------------------------------------


@asynccontextmanager
async def _ajobs_connect() -> AsyncIterator[aiosqlite.Connection]:
    """Een verbinding met de tabel van de klussen. Sluit zonder commit, zoals `auth.connect`."""
    await auth.ensure_db()
    async with auth.connect() as conn:
        await auth.ensure_schema(conn, "price_archive_jobs", JOB_SCHEMA)
        yield conn


def _job_view(row: sqlite3.Row) -> dict[str, Any]:
    params = json.loads(row["error_params"]) if row["error_params"] else {}
    return {
        "id": row["id"],
        "status": row["status"],
        "error": describe(row["error_key"], params) if row["error_key"] else None,
        "summary": json.loads(row["summary"]) if row["summary"] else None,
        "seconds": row["seconds"],
    }


async def _give_up_stale_jobs(conn: aiosqlite.Connection) -> None:
    cutoff: str = (datetime.now(timezone.utc) - timedelta(seconds=JOB_STALE_SECONDS)).isoformat(timespec="seconds")
    await conn.execute(
        "UPDATE price_archive_jobs SET status = 'failed', error_key = 'interrupted', finished_at = ? WHERE status = 'running' AND updated_at < ?",
        (_now(), cutoff),
    )


async def _finish_job(job_id: str, **fields: Any) -> None:
    fields["updated_at"] = fields["finished_at"] = _now()
    assignments: str = ", ".join(f"{name} = ?" for name in fields)
    async with _ajobs_connect() as conn:
        await conn.execute(f"UPDATE price_archive_jobs SET {assignments} WHERE id = ?", (*fields.values(), job_id))
        await conn.commit()


async def _interrupt_job(job_id: str) -> None:
    """Een geannuleerde klus (de app sluit af) krijgt de stand die `_give_up_stale_jobs` hem na een herstart zou geven.

    Alleen zolang hij nog loopt: heeft de klus zelf al vastgelegd dat hij klaar is, dan klopt dat."""
    now: str = _now()
    async with _ajobs_connect() as conn:
        await conn.execute(
            "UPDATE price_archive_jobs SET status = 'failed', error_key = 'interrupted', finished_at = ?, updated_at = ?"
            " WHERE id = ? AND status = 'running'",
            (now, now, job_id),
        )
        await conn.commit()


async def latest_job() -> dict[str, Any] | None:
    """De klus die loopt, of de laatst afgeronde die de beheerder nog niet heeft weggeklikt."""
    async with _ajobs_connect() as conn:
        await _give_up_stale_jobs(conn)
        await conn.commit()
        row: sqlite3.Row | None = await auth.fetchone(
            conn, "SELECT * FROM price_archive_jobs WHERE status = 'running' OR dismissed = 0 ORDER BY started_at DESC, rowid DESC LIMIT 1"
        )
    return _job_view(row) if row else None


async def dismiss_job(job_id: str) -> bool:
    async with _ajobs_connect() as conn:
        cursor: aiosqlite.Cursor = await conn.execute(
            "UPDATE price_archive_jobs SET dismissed = 1 WHERE id = ? AND status != 'running'", (str(job_id),)
        )
        await conn.commit()
    return cursor.rowcount > 0


async def start_job(token: str, replace: bool = False) -> dict[str, Any]:
    """Neemt de klaargezette regels over en zet ze op de achtergrond in de database (met `replace`: eerst de oude eruit)."""
    path: Path = _stage_path(token)
    if not path.is_file():
        raise ArchiveError("stage_missing", 404)
    running: Path = path.with_name(path.name + ".job")
    job_id: str = secrets.token_hex(8)
    actor: str = await audit.current_actor()
    async with _ajobs_connect() as conn:
        await conn.execute("BEGIN IMMEDIATE")
        try:
            await _give_up_stale_jobs(conn)
            if await auth.fetchone(conn, "SELECT 1 FROM price_archive_jobs WHERE status = 'running'"):
                raise ArchiveError("busy", 409)
            try:
                os.replace(path, running)  # een token werkt één keer
            except FileNotFoundError:
                raise ArchiveError("stage_missing", 404) from None
            now: str = _now()
            await conn.execute("UPDATE price_archive_jobs SET dismissed = 1 WHERE status != 'running'")
            await conn.execute(
                "INSERT INTO price_archive_jobs (id, status, started_at, updated_at) VALUES (?, 'running', ?, ?)", (job_id, now, now)
            )
            await conn.commit()
        except BaseException:
            await conn.rollback()
            raise
    tasks.spawn(f"price-archive-{job_id}", run_job(job_id, running, actor, replace))
    return {"id": job_id}


async def run_job(job_id: str, staged: Path, actor: str = "system", replace: bool = False) -> None:
    """De klus zelf, als achtergrondtask. Het invoegen (`apply`) loopt als geheel in een thread en legt in zijn eigen
    transactie vast dat de klus klaar is (`JOB_DONE_SQL`).

    Een fout geeft een mislukte klus (de database is dan niet veranderd). Bij het afsluiten van de app wordt de task
    geannuleerd: de klus heet dan onderbroken, net als na een herstart. Was het invoegen al in zijn thread bezig, dan loopt dat
    door. Komt het nog tot een COMMIT, dan heet de klus klaar, want de stand gaat mee in die transactie. Het auditlog krijgt de
    import via `audit.record` (de enige schrijver van het auditlog), na de COMMIT.
    """
    started: float = time.monotonic()

    def done(conn: sqlite3.Connection, summary: dict[str, Any]) -> None:
        now: str = _now()
        conn.execute(JOB_DONE_SQL, (json.dumps(summary), round(time.monotonic() - started, 2), now, now, job_id))

    work: asyncio.Future[dict[str, Any]] | None = None
    auditing: bool = False  # de auditregel is onderweg: een annulering daarna schrijft hem niet nog een keer
    try:
        try:
            await ensure_schema()
            work = asyncio.ensure_future(asyncio.to_thread(apply, staged, replace, done))
            summary: dict[str, Any] = await asyncio.shield(work)  # een annulering stopt de thread niet: we zien hem afkomen
            auditing = True
            await _record_import(summary, actor, started)
        except Exception:  # noqa: BLE001 - de beheerder krijgt een algemene zin, het logboek de hele fout
            log.exception("Import van een prijsarchief mislukt")
            try:
                await _finish_job(job_id, status="failed", error_key="failed", error_params="{}")
            except sqlite3.Error:
                log.exception("Kon de mislukte klus niet vastleggen")
    except asyncio.CancelledError:
        log.warning("Import van een prijsarchief onderbroken: de app sluit af")
        try:
            await _interrupt_job(job_id)  # wacht op het schrijfslot van een lopend invoegen, en laat een klare klus staan
        except sqlite3.Error:
            log.warning("Kon de onderbroken klus niet vastleggen (het invoegen houdt de database vast)", exc_info=True)
        if work is not None and not auditing:
            await _record_when_committed(work, actor, started)
        raise
    finally:
        staged.unlink(missing_ok=True)


async def _record_import(summary: dict[str, Any], actor: str, started: float) -> None:
    log.info("Prijsarchief geïmporteerd: %s aanbiedingen, %s prijzen", summary["offers"], summary["prices"])
    await audit.record(
        kind="task", service="price-archive", action="import", actor=actor, duration_ms=int((time.monotonic() - started) * 1000),
        detail={"offers": summary["offers"], "prices": summary["prices"], "corrections": summary["corrections"],
                "replaced": summary["replaced"], "removed": summary["removed"]},
    )


async def _record_when_committed(work: asyncio.Future[dict[str, Any]], actor: str, started: float) -> None:
    """Na een annulering: wacht op het invoegen in zijn thread en schrijft de auditregel als het tot een COMMIT kwam.

    Het afsluiten wacht hier hoogstens `tasks.SHUTDOWN_TIMEOUT` op. Duurt het invoegen langer, dan staat de klus wel op klaar
    (dat ging mee in de transactie), maar ontbreekt de auditregel."""
    await asyncio.wait({work})
    if work.cancelled() or work.exception() is not None:
        log.warning("Het onderbroken invoegen kwam niet tot een COMMIT: er is niets gewijzigd")
        return
    await _record_import(work.result(), actor, started)


# --- routes -------------------------------------------------------------------------------------------------------


def _error(exc: ArchiveError) -> tuple[Response, int]:
    return jsonify({"error": describe(exc.key, exc.params), "code": exc.key}), exc.status


def register_routes(app: Quart) -> None:
    # Alleen de beheerder: /api/price-archive staat in auth.ADMIN_PREFIXES.
    @app.get("/api/price-archive")
    async def price_archive_info() -> Response:
        return jsonify({"maxBytes": MAX_UPLOAD_BYTES})

    @app.post("/api/price-archive/stage")
    async def price_archive_stage() -> Response | tuple[Response, int]:
        # Zoals het klaarzetten van een back-up: geen grens op de duur van het geheel, wel op de stilte (body_limits.py).
        try:
            async with aclosing(uploads.read_while_arriving(request.body, request.body_timeout)) as body:
                return jsonify(await stage(body, request.content_length, keep_unlinked=request.args.get("keep_unlinked") == "1"))
        except ArchiveError as exc:
            return _error(exc)

    # Hetzelfde in stukken (uploads.py): zo komt ook een archief van 100 MB door een proxy die grote verzoeken weigert.
    uploads.register_routes(app, "/api/price-archive/uploads", STAGE_PREFIX, MAX_UPLOAD_BYTES)

    @app.post("/api/price-archive/uploads/<token>/stage")
    async def price_archive_stage_upload(token: str) -> Response | tuple[Response, int]:
        body: Any = await request.get_json(silent=True)
        size: Any = body.get("size") if isinstance(body, dict) else None  # geen object: zonder size, zoals `{}`
        try:
            return jsonify(await stage_upload(token, size, keep_unlinked=request.args.get("keep_unlinked") == "1"))
        except uploads.UploadError as exc:
            return uploads.error_response(exc)
        except ArchiveError as exc:
            return _error(exc)

    # **Aanbiedingen ophalen**: van data.mealieshopper.nl, daarna dezelfde samenvatting en import als bij een upload.
    @app.post("/api/price-archive/fetch")
    async def price_archive_fetch() -> Response | tuple[Response, int]:
        try:
            return jsonify(await fetch_site(keep_unlinked=request.args.get("keep_unlinked") == "1"))
        except ArchiveError as exc:
            return _error(exc)

    @app.delete("/api/price-archive/stage/<token>")
    async def price_archive_unstage(token: str) -> Response | tuple[Response, int]:
        try:
            await discard(token)
        except ArchiveError as exc:
            return _error(exc)
        return jsonify({"status": "ok"})

    @app.post("/api/price-archive/jobs")
    async def price_archive_start() -> Response | tuple[Response, int]:
        body: Any = await request.get_json(silent=True) or {}
        try:
            job: dict[str, Any] = await start_job(str(body.get("token") or ""), replace=body.get("replace") is True)
        except ArchiveError as exc:
            return _error(exc)
        return jsonify(job), 202

    # De export (price_archive_export.py): een ZIP met een bestand per winkel en maand, of met `?format=site` in de vorm van
    # data.mealieshopper.nl. POST, zoals de back-up: het werk gebeurt pas als je op de knop drukt, niet bij een link die iets
    # vooraf ophaalt.
    @app.post("/api/price-archive/export")
    async def price_archive_export() -> Response:
        from .price_archive_export import write_export, write_site_export  # pas hier: price_archive_export importeert dit bestand

        site: bool = request.args.get("format") == "site"
        started: float = time.monotonic()
        auth.data_dir().mkdir(parents=True, exist_ok=True)
        path: Path = auth.data_dir() / f"{STAGE_PREFIX}export-{secrets.token_hex(8)}.zip"
        try:
            await ensure_schema()
            # alle regels lezen en zippen: bulk- en rekenwerk
            header: dict[str, Any] = await asyncio.to_thread(write_site_export if site else write_export, path)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        offers_total: int = sum(header["counts"]["offers"].values())
        prices_total: int = sum(header["counts"]["prices"].values())
        detail: dict[str, Any] = {"offers": offers_total, "prices": prices_total, "files": len(header["files"]), "skipped": header["skipped"]}
        if site:
            detail["format"] = "site"
        await audit.record(
            kind="task", service="price-archive", action="export", actor=await audit.current_actor(),
            duration_ms=int((time.monotonic() - started) * 1000), detail=detail,
        )
        name: str = ("mealieshopper-data-" if site else "mealieshopper-prijsarchief-") + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + ".zip"
        # Het bestand is van het bestandssysteem af zodra het open is: er blijft niets staan, ook niet bij een afgebroken download.
        handle: IO[bytes] = path.open("rb")
        size: int = os.fstat(handle.fileno()).st_size
        path.unlink(missing_ok=True)

        async def stream() -> AsyncIterator[bytes]:
            # Async, zodat Quart niet elk stuk via een thread leest (dat doet hij bij een gewone generator). Lokale schijf.
            try:
                while chunk := handle.read(CHUNK):
                    yield chunk
            finally:
                handle.close()

        response: Response = Response(stream(), mimetype="application/zip", headers={
            "Content-Length": str(size),
            "Content-Disposition": f'attachment; filename="{name}"',
            "Cache-Control": "no-store",
            "X-Export-Offers": str(offers_total),
            "X-Export-Prices": str(prices_total),
            "X-Export-Skipped": str(sum(header["skipped"].values())),
        })
        # Geen RESPONSE_TIMEOUT, zoals bij de back-up: een grote export over een trage verbinding mag gewoon lang duren.
        response.timeout = None
        return response

    @app.get("/api/price-archive/jobs/latest")
    async def price_archive_latest() -> Response:
        return jsonify({"job": await latest_job()})

    @app.delete("/api/price-archive/jobs/<job_id>")
    async def price_archive_dismiss(job_id: str) -> Response | tuple[Response, int]:
        if not await dismiss_job(job_id):
            return _error(ArchiveError("unknown_job", 404))
        return jsonify({"status": "ok"})
