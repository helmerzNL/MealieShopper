"""Zet het prijsarchief van de gist van wolph om naar één bestand in het formaat mealieshopper-price-archive.

Het pakket op https://gist.github.com/wolph/195c8964d4e63eb1774fc14ba35b4f09 bestaat uit Base64-delen van een .tar.gz met zes
gegevensbestanden (AH, Jumbo, Picnic, Crisp en de AH-correcties). Dit script controleert elke controlesom, zoals de app dat
doet, en schrijft de regels als `offer`, `price` en `correction` in één `.jsonl.gz` (het eigen formaat van MealieShopper,
beschreven in docs/handleiding.md onder *Prijsarchief importeren*). Dat bestand is veel kleiner dan de ZIP en importeer je
onder Instellingen, Systeem, Prijsarchief importeren.

Alleen de standaardbibliotheek van Python 3.9 of nieuwer; het script hoort niet bij de app en heeft Flask niet nodig. Het
voert niets uit het pakket uit en pakt niets uit naar schijf: de tar wordt alleen gelezen.

Gebruik:
    python3 scripts/price_archive_from_gist.py BRON [-o prijsarchief.jsonl.gz] [--compact] [--no-gzip]

BRON is een van:
    https://gist.github.com/wolph/195c8964d4e63eb1774fc14ba35b4f09   de gist zelf (het script haalt de delen op)
    gist.zip                                                         de ZIP van Download ZIP
    supermarket-archives-20261106.tar.gz                             het archief, als je restore_other.py al draaide
    een map                                                          de uitgepakte ZIP, of supermarket-archives/<datum>

Zonder --compact blijven de herkomstvelden (bronbedragen, Git-commits, oorspronkelijke titels) in elke regel staan; de app
negeert ze bij het importeren, maar zo blijft na te gaan waar een prijs vandaan komt. --compact laat ze weg.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import gzip
import hashlib
import json
import os
import re
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
import zlib
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import IO, Any, Callable, Iterator

FORMAT = "mealieshopper-price-archive"
FORMAT_VERSION = 1
GIST_FORMAT = "supermarket-archive-base64-parts"
GIST_MANIFEST = "supermarket-manifest.json"
DATA_MANIFEST = "data-manifest.json"
DEFAULT_GIST = "https://gist.github.com/wolph/195c8964d4e63eb1774fc14ba35b4f09"
# De gegevensbestanden in het pakket en wat elke regel wordt; gelijk aan GIST_FILES in mealieshopper/price_archive.py.
GIST_FILES: dict[str, str] = {
    "ah-corrected/corrections.json": "correction",
    "ah-corrected/observations.jsonl": "offer",
    "jumbo/reports/observations.jsonl": "offer",
    "picnic/reports/offers.jsonl": "offer",
    "picnic/reports/observations.jsonl": "price",
    "crisp/reports/observations.jsonl": "price",
}
TAR_NAME = re.compile(r"supermarket-archives/(\d{4}-\d{2}-\d{2})/(.+)")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
GIST_URL = re.compile(r"^https://gist\.github(?:usercontent)?\.com/(?:(?P<user>[\w.-]+)/)?(?P<id>[0-9a-f]{20,40})(?:[/#?].*)?$")
# De kolommen van de tabellen in de app; met --compact blijft alleen dit over.
OFFER_COLUMNS = (
    "store", "product_id", "title", "brand", "unit_size", "category", "price", "price_before", "promo_label", "image", "url",
    "valid_from", "valid_until", "first_seen", "last_seen", "ended_at", "syncs", "continued", "personal",
)
PRICE_COLUMNS = ("store", "product_id", "day", "source", "title", "brand", "unit_size", "image", "url", "price", "seen_at")
CORRECTION_COLUMNS = ("store", "url", "old_price", "old_before", "new_price", "new_before")
LICENSE = "Openbare brongegevens, met hun oorspronkelijke rechten; de Picnic-gegevens komen van Checkjebon (MIT-licentie)."
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_PART_BYTES = 16 * 1024 * 1024
MAX_ARCHIVE_BYTES = 300 * 1024 * 1024
USER_AGENT = "MealieShopper price archive converter"


class ConversionError(Exception):
    """Een fout in de bron: een controlesom die niet klopt, een ontbrekend bestand of iets wat geen pakket is."""


# --- de delen vinden ----------------------------------------------------------------------------------------------


def _bare(name: Any) -> bool:
    return isinstance(name, str) and bool(name) and "/" not in name and "\\" not in name and name not in (".", "..")


def _read_manifest(data: bytes) -> dict[str, Any]:
    try:
        manifest = json.loads(data)
    except ValueError:
        raise ConversionError(f"{GIST_MANIFEST} is geen geldige JSON") from None
    if not isinstance(manifest, dict) or manifest.get("format") != GIST_FORMAT:
        raise ConversionError(f"{GIST_MANIFEST} beschrijft geen pakket van het formaat {GIST_FORMAT}")
    if manifest.get("formatVersion") != 1:
        raise ConversionError(f"{GIST_MANIFEST} heeft formaatversie {manifest.get('formatVersion')!r}; dit script kent versie 1")
    size = manifest.get("archiveBytes")
    if isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= MAX_ARCHIVE_BYTES:
        raise ConversionError(f"{GIST_MANIFEST}: ongeldige archiveBytes")
    if not _bare(manifest.get("archiveFile")) or not SHA256.match(str(manifest.get("archiveSha256") or "")):
        raise ConversionError(f"{GIST_MANIFEST}: ongeldige archiveFile of archiveSha256")
    parts = manifest.get("parts")
    if not isinstance(parts, list) or not parts:
        raise ConversionError(f"{GIST_MANIFEST}: geen delen")
    for part in parts:
        if not isinstance(part, dict) or not _bare(part.get("file")) or not SHA256.match(str(part.get("sha256") or "")):
            raise ConversionError(f"{GIST_MANIFEST}: een deel heeft een ongeldige naam of controlesom")
    return manifest


def _assemble(manifest: dict[str, Any], fetch: Callable[[str], bytes], target: IO[bytes]) -> None:
    """Schrijft het .tar.gz uit de Base64-delen naar `target`, met de controlesom van elk deel en van het geheel."""
    digest = hashlib.sha256()
    total = 0
    for number, part in enumerate(manifest["parts"], 1):
        name = part["file"]
        content = fetch(name)
        if len(content) > MAX_PART_BYTES:
            raise ConversionError(f"{name} is groter dan {MAX_PART_BYTES // (1024 * 1024)} MB")
        if hashlib.sha256(content).hexdigest() != part["sha256"]:
            raise ConversionError(f"de controlesom van {name} klopt niet; download het pakket opnieuw")
        try:
            raw = base64.b64decode(content.strip(), validate=True)
        except (binascii.Error, ValueError):
            raise ConversionError(f"{name} is geen geldige Base64") from None
        total += len(raw)
        if total > manifest["archiveBytes"]:
            raise ConversionError(f"de controlesom van {manifest['archiveFile']} klopt niet")
        digest.update(raw)
        target.write(raw)
        progress(f"deel {number} van {len(manifest['parts'])} gecontroleerd")
    if total != manifest["archiveBytes"] or digest.hexdigest() != manifest["archiveSha256"]:
        raise ConversionError(f"de controlesom van {manifest['archiveFile']} klopt niet")


def _bounded(handle: IO[bytes], limit: int, name: str) -> bytes:
    data = handle.read(limit + 1)
    if len(data) > limit:
        raise ConversionError(f"{name} is te groot")
    return data


def _from_zip(path: Path, target: IO[bytes]) -> None:
    try:
        bundle = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise ConversionError(f"{path} is geen geldige ZIP") from None
    with bundle:
        entries = {info.filename: info for info in bundle.infolist() if not info.is_dir()}
        manifests = [name for name in entries if PurePosixPath(name).name == GIST_MANIFEST and len(PurePosixPath(name).parts) <= 2]
        if len(manifests) != 1:
            raise ConversionError(f"de ZIP bevat geen {GIST_MANIFEST}; kies de ZIP van de gist met de supermarket-archive-delen")
        folder = PurePosixPath(manifests[0]).parent

        def fetch(name: str) -> bytes:
            info = entries.get(name if str(folder) == "." else f"{folder}/{name}")
            if info is None:
                raise ConversionError(f"de ZIP mist {name}")
            with bundle.open(info) as handle:
                return _bounded(handle, MAX_PART_BYTES, name)

        with bundle.open(entries[manifests[0]]) as handle:
            manifest = _read_manifest(_bounded(handle, MAX_MANIFEST_BYTES, GIST_MANIFEST))
        _assemble(manifest, fetch, target)


def _from_folder(path: Path, target: IO[bytes]) -> None:
    def fetch(name: str) -> bytes:
        part = path / name
        if not part.is_file():
            raise ConversionError(f"de map mist {name}")
        with part.open("rb") as handle:
            return _bounded(handle, MAX_PART_BYTES, name)

    with (path / GIST_MANIFEST).open("rb") as handle:
        manifest = _read_manifest(_bounded(handle, MAX_MANIFEST_BYTES, GIST_MANIFEST))
    _assemble(manifest, fetch, target)


def _download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return _bounded(response, MAX_PART_BYTES, url)
    except OSError as exc:
        raise ConversionError(f"ophalen van {url} mislukte: {exc}") from None


def _from_gist(url: str, target: IO[bytes], download: Callable[[str], bytes] = _download) -> None:
    """Haalt het manifest en de delen op als losse bestanden van de gist (de nieuwste versie), zonder de hele ZIP."""
    match = GIST_URL.match(url.strip())
    if not match:
        raise ConversionError(f"{url} is geen adres van een gist")
    if not match.group("user"):
        raise ConversionError(f"geef het volledige adres van de gist, met de gebruikersnaam (zoals {DEFAULT_GIST})")
    base = f"https://gist.githubusercontent.com/{match.group('user')}/{match.group('id')}/raw/"
    progress(f"ophalen van {base}{GIST_MANIFEST}")
    manifest = _read_manifest(download(base + GIST_MANIFEST))
    _assemble(manifest, lambda name: download(base + name), target)


# --- de gegevensbestanden lezen -----------------------------------------------------------------------------------


def _lines(handle: IO[bytes], digest: Any) -> Iterator[tuple[int, bytes]]:
    """De regels van een bestand in het archief, regel voor regel; elke byte telt mee in de controlesom."""
    number = 0
    total = 0
    for line in iter(lambda: handle.readline(MAX_PART_BYTES), b""):
        number += 1
        total += len(line)
        digest.update(line)
        if total > MAX_ARCHIVE_BYTES:
            raise ConversionError("een gegevensbestand is te groot")
        if line.strip():
            yield number, line


def _records(name: str, handle: IO[bytes], digest: Any) -> Iterator[tuple[str, dict[str, Any]]]:
    kind = GIST_FILES[name]
    if kind == "correction":
        content = _bounded(handle, MAX_ARCHIVE_BYTES, name)
        digest.update(content)
        try:
            items = json.loads(content)
        except ValueError:
            raise ConversionError(f"{name} is geen geldige JSON") from None
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            raise ConversionError(f"{name} is geen lijst met correcties")
        for item in items:
            # De correcties in het pakket gaan allemaal over Albert Heijn en noemen de winkel niet.
            yield kind, {"store": "ah", **item}
        return
    for number, line in _lines(handle, digest):
        try:
            row = json.loads(line)
        except ValueError:
            raise ConversionError(f"{name}, regel {number}: geen geldige JSON") from None
        if not isinstance(row, dict):
            raise ConversionError(f"{name}, regel {number}: geen JSON-object")
        yield kind, row


def _line(kind: str, row: dict[str, Any], compact: bool) -> dict[str, Any]:
    if compact:
        columns = {"offer": OFFER_COLUMNS, "price": PRICE_COLUMNS, "correction": CORRECTION_COLUMNS}[kind]
        row = {key: row[key] for key in columns if key in row}
    else:
        row = {key: value for key, value in row.items() if key != "type"}
    return {"type": kind, **row}


def _tar_members(tar_path: Path) -> Iterator[tuple[str, str, int, IO[bytes]]]:
    """De bestanden die we nodig hebben uit het .tar.gz, in één doorgang en zonder iets uit te pakken."""
    try:
        with tarfile.open(tar_path, "r|gz") as tar:
            for member in tar:
                match = TAR_NAME.fullmatch(member.name)
                if not match or not member.isfile():
                    continue
                name = match.group(2)
                if name != DATA_MANIFEST and name not in GIST_FILES:
                    continue
                handle = tar.extractfile(member)
                if handle is not None:
                    yield f"supermarket-archives/{match.group(1)}", name, member.size, handle
    except (tarfile.TarError, EOFError, zlib.error, OSError) as exc:
        raise ConversionError(f"het archief is beschadigd: {exc}") from None


def _folder_members(folder: Path) -> Iterator[tuple[str, str, int, IO[bytes]]]:
    """Dezelfde bestanden uit een uitgepakte map supermarket-archives/<datum>."""
    package = f"supermarket-archives/{folder.resolve().name}"
    for name in [DATA_MANIFEST, *GIST_FILES]:
        file = folder / name
        if not file.is_file():
            raise ConversionError(f"de map mist {name}")
        with file.open("rb") as handle:
            yield package, name, file.stat().st_size, handle


def _write_body(members: Iterator[tuple[str, str, int, IO[bytes]]], body: IO[str], compact: bool) -> tuple[str, dict[str, int]]:
    """Schrijft elke regel naar `body` en controleert daarna de controlesommen uit data-manifest.json.

    Klopt een controlesom niet, dan volgt een fout en gebruikt `convert` wat er geschreven is niet.
    """
    counts = {"offer": 0, "price": 0, "correction": 0}
    digests: dict[str, str] = {}
    data_manifest: Any = None
    package: str | None = None
    try:
        for root, name, size, handle in members:
            if package is None:
                package = root
            elif package != root:
                raise ConversionError("het archief bevat meer dan één pakket")
            if size > MAX_ARCHIVE_BYTES:
                raise ConversionError(f"{name} is te groot")
            if name in digests or (name == DATA_MANIFEST and data_manifest is not None):
                raise ConversionError(f"{name} staat twee keer in het archief")
            if name == DATA_MANIFEST:
                try:
                    data_manifest = json.loads(_bounded(handle, MAX_MANIFEST_BYTES, name))
                except ValueError:
                    raise ConversionError(f"{DATA_MANIFEST} is geen geldige JSON") from None
                continue
            digest = hashlib.sha256()
            for kind, row in _records(name, handle, digest):
                body.write(json.dumps(_line(kind, row, compact), ensure_ascii=False) + "\n")
                counts[kind] += 1
            digests[name] = digest.hexdigest()
            progress(f"{name}: gelezen")
    except (tarfile.TarError, EOFError, zlib.error) as exc:
        raise ConversionError(f"het archief is beschadigd: {exc}") from None
    if package is None:
        raise ConversionError("dit is geen archief met supermarket-archives/<datum>/")
    if not isinstance(data_manifest, dict):
        raise ConversionError(f"het archief mist {DATA_MANIFEST}")
    for name in GIST_FILES:
        if name not in digests:
            raise ConversionError(f"het archief mist {name}")
        if data_manifest.get(name) != digests[name]:
            raise ConversionError(f"de controlesom van {name} klopt niet")
    return package, counts


# --- omzetten -----------------------------------------------------------------------------------------------------

QUIET = False


def progress(text: str) -> None:
    if not QUIET:
        print(text, file=sys.stderr)


def _source(source: str, work: Path, download: Callable[[str], bytes]) -> tuple[Iterator[tuple[str, str, int, IO[bytes]]], Path | None]:
    """De bestanden van het pakket, en het .tar.gz als dat het oorspronkelijke archief is (de controlesom gaat in de kop)."""
    tar_path = work / "archive.tar.gz"
    path = Path(source)
    if source.strip().startswith(("http://", "https://")):
        with tar_path.open("wb") as target:
            _from_gist(source, target, download)
        return _tar_members(tar_path), tar_path
    if path.is_dir():
        if (path / DATA_MANIFEST).is_file():
            return _folder_members(path), None
        folders = [path] if (path / GIST_MANIFEST).is_file() else [p for p in path.iterdir() if p.is_dir() and (p / GIST_MANIFEST).is_file()]
        if len(folders) != 1:
            raise ConversionError(f"{path} bevat geen {GIST_MANIFEST} en geen {DATA_MANIFEST}")
        with tar_path.open("wb") as target:
            _from_folder(folders[0], target)
        return _tar_members(tar_path), tar_path
    if path.is_file():
        with path.open("rb") as handle:
            head = handle.read(4)
        if head.startswith(b"PK\x03\x04"):
            with tar_path.open("wb") as target:
                _from_zip(path, target)
            return _tar_members(tar_path), tar_path
        if head.startswith(b"\x1f\x8b"):
            return _tar_members(path), path
        raise ConversionError(f"{path} is geen ZIP en geen .tar.gz")
    raise ConversionError(f"{source} bestaat niet")


def convert(source: str, output: Path, compact: bool = False, compress: bool = True,
            download: Callable[[str], bytes] = _download) -> dict[str, Any]:
    """Zet `source` om naar `output` en geeft per soort het aantal regels."""
    with tempfile.TemporaryDirectory(prefix="price-archive-") as folder:
        work = Path(folder)
        members, archive = _source(source, work, download)
        body_path = work / "body.jsonl"
        with body_path.open("w", encoding="utf-8", newline="\n") as body:
            package_name, counts = _write_body(members, body, compact)
        header: dict[str, Any] = {
            "format": FORMAT,
            "formatVersion": FORMAT_VERSION,
            "createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "source": f"{package_name} (gist van wolph, omgezet met scripts/price_archive_from_gist.py)",
            "license": LICENSE,
        }
        if archive is not None:
            header["sourceArchiveSha256"] = _sha256(archive)
        temporary = output.with_name(output.name + ".tmp")
        try:
            with (gzip.open(temporary, "wb", compresslevel=9) if compress else temporary.open("wb")) as out:
                out.write((json.dumps(header, ensure_ascii=False) + "\n").encode("utf-8"))
                with body_path.open("rb") as body:
                    for chunk in iter(lambda: body.read(1024 * 1024), b""):
                        out.write(chunk)
            os.replace(temporary, output)
        except BaseException:
            if temporary.exists():
                temporary.unlink()
            raise
    return {"package": package_name, "lines": counts, "output": str(output), "bytes": output.stat().st_size}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    global QUIET
    parser = argparse.ArgumentParser(description="Zet het prijsarchief van de gist om naar het formaat mealieshopper-price-archive.")
    parser.add_argument("source", nargs="?", default=DEFAULT_GIST, help=f"gist-adres, ZIP, .tar.gz of map (standaard {DEFAULT_GIST})")
    parser.add_argument("-o", "--output", type=Path, help="het bestand om te schrijven (standaard prijsarchief.jsonl.gz)")
    parser.add_argument("--compact", action="store_true", help="alleen de kolommen van de app, zonder herkomstvelden")
    parser.add_argument("--no-gzip", action="store_true", help="schrijf een gewone .jsonl in plaats van .jsonl.gz")
    parser.add_argument("-q", "--quiet", action="store_true", help="geen voortgang tonen")
    args = parser.parse_args(argv)
    QUIET = args.quiet
    output = args.output or Path("prijsarchief.jsonl" if args.no_gzip else "prijsarchief.jsonl.gz")
    try:
        result = convert(args.source, output, compact=args.compact, compress=not args.no_gzip)
    except ConversionError as exc:
        print(f"Fout: {exc}", file=sys.stderr)
        return 1
    lines = result["lines"]
    print(
        f"{result['output']}: {lines['offer']} aanbiedingen, {lines['price']} prijzen en {lines['correction']} correcties "
        f"uit {result['package']} ({result['bytes'] / (1024 * 1024):.1f} MB). "
        "Importeer het onder Instellingen, Systeem, Prijsarchief importeren."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
