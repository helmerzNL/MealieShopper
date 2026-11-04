"""De afbeeldingencache: elke productfoto van een winkel een keer ophalen en daarna voor altijd van schijf serveren.

De interface toont foto's van AH, Jumbo, Picnic en Crisp (bestellingen, het mandje, aanbiedingen, zoekresultaten, recepten).
Rechtstreeks van de winkel laden is hotlinken: het vertelt de winkel wat iemand bekijkt en breekt zodra een adres verandert.
Daarom vraagt de interface ze op via `/api/images/<token>` (`storeImage()` in static/app.js, het token is het winkeladres in
URL-veilige base64). De route haalt een foto die er nog niet is op zoals een browser dat zou doen (zie `HEADERS`), bewaart
het origineel in `<datamap>/images/<ab>/<sha256-van-het-adres>.<ext>` en serveert daarna altijd het bestand.

De tabel `image_cache` (in auth.sqlite3) kent per adres de host, het pad, het type, de maten, wanneer het is opgehaald en de
mislukkingen. Ze staat niet in de back-up (backup.EXCLUDED_TABLES): de cache is opnieuw op te bouwen. Een nachtelijke ronde
(`prefetch`, vanuit offers_schedule) haalt in een rustig tempo binnen wat nog ontbreekt.
"""

from __future__ import annotations

import base64
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import auth

SCHEMA = """
CREATE TABLE IF NOT EXISTS image_cache (
  id INTEGER PRIMARY KEY,
  url TEXT NOT NULL UNIQUE,
  host TEXT NOT NULL,
  path TEXT,
  content_type TEXT,
  bytes INTEGER,
  width INTEGER,
  height INTEGER,
  digest TEXT,
  fetched_at TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  last_attempt_at TEXT,
  error TEXT,
  seen_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS image_cache_todo ON image_cache (fetched_at, seen_at);
"""

# Welke hosts foto's van een winkel zijn. Een adres van een andere host gaat niet door de cache (en de route geeft er 404 op):
# dit is geen open proxy.
_PICNIC_SUFFIX = ".picnicinternational.com"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _connect():
    conn = auth.connect()
    conn.executescript(SCHEMA)
    return conn


def images_dir() -> Path:
    return auth.data_dir() / "images"


def store_for(url: Any) -> str | None:
    """De winkel bij een adres (`ah`, `jumbo`, `picnic`, `crisp`), of None als het geen foto van een winkel is."""
    if not isinstance(url, str) or not url:
        return None
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return None
    host = parts.hostname or ""
    if host == "static.ah.nl":
        return "ah"
    if host == "jumbo.com" or host.endswith(".jumbo.com"):
        return "jumbo"
    if host.startswith("storefront-prod.") and host.endswith(_PICNIC_SUFFIX):
        return "picnic"
    if host == "crispapp.nl":
        return "crisp"
    return None


def token_for(url: str) -> str:
    """Het winkeladres als URL-veilige base64 zonder `=`: het stuk achter /api/images/."""
    return base64.urlsafe_b64encode(url.encode("utf-8")).decode("ascii").rstrip("=")


def url_for(token: Any) -> str | None:
    """Het adres achter een token, of None als het niet decodeert of geen foto van een winkel is."""
    if not isinstance(token, str) or not token:
        return None
    try:
        url = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    return url if store_for(url) else None


def register(url: str, now: str | None = None) -> int:
    """Zet een adres in de tabel (of ververs `seen_at`) en geef het id."""
    now = now or _now()
    host = urlsplit(url).hostname or ""
    with closing(_connect()) as conn, conn:
        conn.execute(
            "INSERT INTO image_cache (url, host, seen_at) VALUES (?, ?, ?) ON CONFLICT(url) DO UPDATE SET seen_at = excluded.seen_at",
            (url, host, now),
        )
        return int(
            conn.execute("SELECT id FROM image_cache WHERE url = ?", (url,)).fetchone()[
                0
            ]
        )
