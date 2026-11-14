"""Het gedeelde bestand met koppelingen van oude namen aan producten, een per winkel (`shared_link_data/<winkel>.json`).

Het bestand hoort bij de app en gaat mee in het image. Per koppeling staan er alleen `name` (de gevouwen naam, `name_keys.fold`),
`productId`, `title` en `unitSize`. Het bestand is gesorteerd op `name` en heeft een koppeling per regel, zodat een wijziging in
git een regel is. `parse` leest strikt, want het bestand is een grens van het systeem.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Iterable, Mapping
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, NamedTuple

import aiosqlite

from . import auth, name_keys, offers
from .i18n import tr

log: logging.Logger = logging.getLogger(__name__)

DATA_DIR: Path = Path(__file__).resolve().parent / "shared_link_data"
VERSION: int = 1
_LINK_KEYS: tuple[str, ...] = ("name", "productId", "title", "unitSize")
STORES: tuple[str, ...] = offers.SHOP_STORES  # dezelfde winkels als `product_links.STORES`, zonder die module te importeren
MAX_NAME_CHARS: int = 300
MAX_ID_CHARS: int = 64


class SharedLink(NamedTuple):
    name: str
    product_id: str
    title: str
    unit_size: str


def _parse_link(raw: Any, index: int) -> SharedLink:
    if not isinstance(raw, dict) or set(raw) != set(_LINK_KEYS):
        raise ValueError(f"koppeling {index} moet precies de velden {', '.join(_LINK_KEYS)} hebben")
    for field in _LINK_KEYS:
        if not isinstance(raw[field], str):
            raise ValueError(f"koppeling {index}: {field} moet tekst zijn")  # noqa: TRY004
    return SharedLink(raw["name"], raw["productId"], raw["title"], raw["unitSize"])


def parse(text: str) -> list[SharedLink]:
    """De koppelingen uit de tekst van een bestand. ValueError met een leesbare reden bij een ongeldige vorm."""
    try:
        data: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"geen geldige JSON: {exc}") from exc
    if not isinstance(data, dict) or set(data) != {"version", "links"}:
        raise ValueError("het bestand moet precies de velden version en links hebben")
    if data["version"] != VERSION or isinstance(data["version"], bool):
        raise ValueError(f"versie {data['version']!r} wordt niet ondersteund, verwacht {VERSION}")
    if not isinstance(data["links"], list):
        raise ValueError("links moet een lijst zijn")  # noqa: TRY004
    return [_parse_link(raw, index) for index, raw in enumerate(data["links"])]


def problems(links: list[SharedLink]) -> list[str]:
    """Wat er mis is met de lijst, een melding per fout. Leeg als het bestand in orde is."""
    found: list[str] = []
    names: list[str] = [link.name for link in links]
    if names != sorted(names):
        found.append("de koppelingen zijn niet gesorteerd op name")
    seen: set[str] = set()
    by_key: dict[str, SharedLink] = {}
    for link in links:
        if link.name in seen:
            found.append(f"de naam {link.name!r} staat twee keer in het bestand")
        seen.add(link.name)
        if not link.product_id.strip():
            found.append(f"{link.name!r} heeft een leeg productId")
        if link.name != name_keys.fold(link.name):
            found.append(f"{link.name!r} is niet gevouwen (name_keys.fold geeft {name_keys.fold(link.name)!r})")
        other: SharedLink = by_key.setdefault(name_keys.key(link.name), link)
        if other.name != link.name and other.product_id != link.product_id:
            found.append(f"{other.name!r} en {link.name!r} hebben dezelfde sleutel maar een ander product")
    return found


def _dump_link(link: SharedLink) -> str:
    values: tuple[str, ...] = (link.name, link.product_id, link.title, link.unit_size)
    return "  " + json.dumps(dict(zip(_LINK_KEYS, values)), ensure_ascii=False)


def dump(links: Iterable[SharedLink]) -> str:
    """De tekst van een bestand: gesorteerd op name, een koppeling per regel, eindigend op een regeleinde."""
    lines: list[str] = [_dump_link(link) for link in sorted(links, key=lambda link: link.name)]
    body: str = ",\n".join(lines)
    return f'{{"version": {VERSION}, "links": [\n' + (body + "\n" if body else "") + "]}\n"


_cache: dict[str, Mapping[str, SharedLink]] = {}


def _read(store: str) -> Mapping[str, SharedLink]:
    path: Path = DATA_DIR / f"{store}.json"
    try:
        links: list[SharedLink] = parse(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError) as exc:
        log.warning(
            "Het gedeelde bestand met koppelingen voor %s is onbruikbaar en wordt overgeslagen: %s",
            store,
            exc,
        )
        return MappingProxyType({})
    found: list[str] = problems(links)
    if found:
        log.warning("Het gedeelde bestand met koppelingen voor %s klopt niet, eerste probleem: %s", store, found[0])
    by_key: dict[str, SharedLink] = {}
    for link in links:
        by_key.setdefault(name_keys.key(link.name), link)  # de eerste per sleutel wint
    return MappingProxyType(by_key)


def load(store: str) -> Mapping[str, SharedLink]:
    """De koppelingen van een winkel, met `name_keys.key(name)` als sleutel, alleen-lezen. Per proces gecachet. Leeg bij een
    ontbrekend of kapot bestand, met een waarschuwing in het logboek."""
    if store not in _cache:
        _cache[store] = _read(store)
    return _cache[store]


def active(store: str, rejected: set[tuple[str, str]]) -> dict[str, SharedLink]:
    """De koppelingen van `load` zonder de afgewezen (`rejected`: sleutel en productnummer). Een nieuwe dict: wat `load` geeft
    blijft ongemoeid. Wijst het bestand later naar een ander product, dan telt die koppeling weer."""
    return {key: link for key, link in load(store).items() if (key, link.product_id) not in rejected}


# --- "Klopt niet": per installatie een gedeelde koppeling uitzetten. Het bestand zelf verandert niet. -------------------------

SCHEMA: str = """
-- Een gedeelde koppeling (`shared_links`) die deze installatie afwees: de sleutel van de naam (`name_keys.key`) en het product
-- uit het bestand. Wijst het bestand na een update naar een ander product, dan geldt die koppeling wel.
CREATE TABLE IF NOT EXISTS shared_link_rejections (
  store TEXT NOT NULL,
  key TEXT NOT NULL,
  product_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (store, key, product_id)
);
"""


async def ensure(conn: aiosqlite.Connection) -> aiosqlite.Connection:
    """Maak de tabel als die er nog niet is. Voor de `_connect` van `prices` en van deze module."""
    await auth.ensure_schema(conn, "shared_link_rejections", SCHEMA)
    return conn


@asynccontextmanager
async def _connect() -> AsyncIterator[aiosqlite.Connection]:
    """Een verbinding met de tabel. Sluit zonder commit, zoals `auth.connect`."""
    await auth.ensure_db()
    async with auth.connect() as conn:
        yield await ensure(conn)


async def rejected(conn: aiosqlite.Connection, store: str) -> set[tuple[str, str]]:
    """De afgewezen koppelingen van een winkel als (sleutel, productnummer), uit een open verbinding (die `ensure` al zag)."""
    rows = await conn.execute_fetchall("SELECT key, product_id FROM shared_link_rejections WHERE store = ?", (store,))
    return {(row["key"], row["product_id"]) for row in rows}


def _in_file(store: str, key: str, product: str) -> None:
    """ValueError als (sleutel, productnummer) geen koppeling uit het gedeelde bestand van `store` is (`load`). Zo bewaart Klopt niet
    alleen een echte koppeling, en geen rij voor elke naam die iemand instuurt (elke rij maakt de catalogus opnieuw)."""
    link: SharedLink | None = load(store).get(key)
    if link is None or link.product_id != product:
        raise ValueError(tr("Deze koppeling staat niet in het gedeelde bestand.", "This link is not in the shared file."))


def _identity(store: Any, name: Any, product_id: Any) -> tuple[str, str, str]:
    """(winkel, sleutel, productnummer) uit een verzoek, van een koppeling uit het gedeelde bestand (`_in_file`). ValueError met een
    leesbare melding bij een ongeldige invoer."""
    key_store: str = str(store or "").strip().lower()
    if key_store not in STORES:
        raise ValueError(tr("Onbekende winkel", "Unknown store"))
    text: str = " ".join(str(name or "").split())[:MAX_NAME_CHARS]
    product: str = " ".join(str(product_id or "").split())[:MAX_ID_CHARS]
    if not text or not product:
        raise ValueError(tr("Een naam en een productnummer zijn nodig", "A name and a product number are required"))
    key: str = name_keys.key(text)
    _in_file(key_store, key, product)
    return key_store, key, product


async def reject(store: Any, name: Any, product_id: Any) -> bool:
    """Zet een gedeelde koppeling uit voor deze installatie. Geeft of er iets veranderde (False als hij al uit stond)."""
    key_store, key, product = _identity(store, name, product_id)
    now: str = datetime.now(timezone.utc).isoformat(timespec="seconds")
    async with _connect() as conn:
        cursor: aiosqlite.Cursor = await conn.execute(
            "INSERT OR IGNORE INTO shared_link_rejections (store, key, product_id, created_at) VALUES (?, ?, ?, ?)",
            (key_store, key, product, now),
        )
        await conn.commit()
    return cursor.rowcount > 0


async def restore(store: Any, name: Any, product_id: Any) -> bool:
    """Zet een afgewezen gedeelde koppeling weer aan. Geeft of er iets veranderde (False als hij niet uit stond)."""
    key_store, key, product = _identity(store, name, product_id)
    async with _connect() as conn:
        cursor: aiosqlite.Cursor = await conn.execute(
            "DELETE FROM shared_link_rejections WHERE store = ? AND key = ? AND product_id = ?",
            (key_store, key, product),
        )
        await conn.commit()
    return cursor.rowcount > 0
