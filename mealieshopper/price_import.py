"""Oude aankopen inlezen voor de Prijsgeschiedenis (#178): bestellingen van voor MealieShopper, uit de bevestigingsmails van de winkel.

AH geeft in de app en de API alleen de bestellingen van de laatste maanden. Wat ervoor ligt, staat nog in je mailbox. Een AI-assistent
(Claude, ChatGPT) zet die mails om naar JSON met `prompt()`. Die JSON plak je in de Prijsgeschiedenis (`POST /api/prices/import`).

- **Dezelfde bestelling telt een keer.** Elke bestelling heeft een datum (de bezorg- of afhaaldag) en, als de mail hem noemt, een
  bestelnummer. Een bestelling die al is ingelezen (zelfde bestelnummer, of zonder nummer: zelfde dag en totaal), of die de app al uit
  je account kent (zelfde nummer, of een bestelling op dezelfde dag), wordt overgeslagen. Ook bij het lezen (`prices`) telt een
  ingelezen bestelling niet als de app dezelfde bestelling later toch uit je account haalt.
- Een regel heeft geen id van de webwinkel, alleen de naam uit de mail. `prices` koppelt hem op naam aan een bekend product, zoals een
  regel van een kassabon. Past de naam nergens, dan staat hij als eigen product in de lijst.
- `paid` is wat je per stuk betaalde (met de korting op dat product), `regular` de prijs per stuk zonder korting.
- De ingelezen aankopen staan in `imported_purchases` en gaan mee in de back-up. `remove(store)` haalt ze weer weg.
- **Ook bij Bestellingen.** Een ingelezen bestelling staat bij de afgelopen bestellingen van die winkel (`list_orders`, `get_order`),
  net als een bestelling uit je account, en de regels tellen mee als je in je aankopen zoekt (`search_lines`). Een ingelezen
  bestelling die de app inmiddels ook uit je account kent, staat er niet nog een keer. `remove(store, ref)` haalt één bestelling weg.
- **Zoals een bestelling uit je account (#229).** De mail noemt ook de status en het bezorgmoment: die lezen we in (`status`,
  `slot_start`, `slot_end`) en tonen we zoals de winkel ze bij een eigen bestelling toont. Een geannuleerde bestelling lezen we niet
  in. Elke regel koppelen we bij het lezen op naam aan een product van de winkel (`prices.link_names`, dezelfde koppeling als in de
  Prijsgeschiedenis), zodat een ingelezen bestelling dezelfde foto's, merken en inhoud heeft als een bestelling uit je account.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import AsyncIterator, Callable, Iterable
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from typing import Any

import aiosqlite

from . import auth, offers, purchase_lines
from .i18n import tr

STORES = offers.STORES
MAX_TEXT_CHARS = 5_000_000
MAX_ORDERS = 5000
MAX_ITEMS = 1000
MAX_NAME_CHARS = 300
MAX_PROBLEMS = 20

SCHEMA = """
-- Aankopen uit bevestigingsmails (zie price_import.py). `order_ref` is het bestelnummer, of zonder nummer `dag:<datum>:<totaal>`.
CREATE TABLE IF NOT EXISTS imported_purchases (
  store TEXT NOT NULL,
  order_ref TEXT NOT NULL,
  position INTEGER NOT NULL,
  day TEXT NOT NULL,
  title TEXT NOT NULL,
  quantity REAL NOT NULL,
  paid REAL,
  regular REAL,
  order_total REAL,
  imported_at TEXT NOT NULL,
  status TEXT,
  slot_start TEXT,
  slot_end TEXT,
  PRIMARY KEY (store, order_ref, position)
);
CREATE INDEX IF NOT EXISTS imported_purchases_day ON imported_purchases (store, day);
"""

# Kolommen die er later bij kwamen (#229): de status en het bezorgmoment uit de mail, net als `order_total` per regel. Een database
# van daarvoor krijgt ze bij het eerste gebruik; de rijen van toen houden NULL.
_ADDED_COLUMNS = (("status", "TEXT"), ("slot_start", "TEXT"), ("slot_end", "TEXT"))

# De status uit de mail, in de woorden die een assistent kan gebruiken, naar de twee die we bewaren. Een bestelling die bezorgd of
# afgehaald is, heet "delivered"; een geannuleerde lezen we niet in.
STATUS_DELIVERED, STATUS_CANCELLED = "delivered", "cancelled"
_STATUS_WORDS = {
    **dict.fromkeys(("delivered", "collected", "picked up", "picked_up", "completed", "done", "bezorgd", "afgeleverd", "geleverd",
                     "afgehaald", "opgehaald"), STATUS_DELIVERED),
    **dict.fromkeys(("cancelled", "canceled", "cancel", "geannuleerd"), STATUS_CANCELLED),
}

STORE_NAMES = {"ah": "Albert Heijn", "jumbo": "Jumbo", "picnic": "Picnic", "crisp": "Crisp"}

_PROMPT_NL = """Je krijgt een of meer bevestigingsmails van bestellingen bij {store}. Zet ze om naar JSON, precies in de vorm hieronder.
Geef alleen de JSON, zonder uitleg ervoor of erna.

{{"store": "{key}", "orders": [
  {{"orderNumber": "123456789", "date": "2025-03-14", "status": "delivered", "deliveryWindow": {{"start": "18:00", "end": "20:00"}},
    "total": 141.53, "items": [
    {{"name": "AH Bananen tros", "quantity": 2, "price": 1.19, "total": 2.38, "discount": 0}},
    {{"name": "AH Greenfields Beefburger naturel 2 stuks", "quantity": 16, "price": 4.99, "total": 79.84, "discount": 39.92}}
  ]}}
]}}

Regels:
1. Eén object per bestelling. Komt dezelfde bestelling in meer mails voor (bevestiging, wijziging, annulering), neem dan alleen de
   laatste versie.
2. "date" is verplicht: de bezorg- of afhaaldatum van de bestelling als JJJJ-MM-DD. Staat die er niet in, neem dan de datum van de mail.
   MealieShopper herkent aan deze datum (en aan het bestelnummer) een bestelling die er al is, zodat niets dubbel telt.
3. "orderNumber" is het bestelnummer uit de mail, als tekst. Staat er geen nummer in, gebruik dan null.
4. "status": "delivered" voor een bestelling die bezorgd of afgehaald is, ook als de mail alleen de bevestiging is en de datum voorbij
   is; "cancelled" als de mail zegt dat de bestelling geannuleerd is (die leest MealieShopper niet in); anders null.
5. "deliveryWindow" is het bezorg- of afhaalmoment uit de mail, met "start" en "end" als UU:MM (bij "Tussen 18:00 en 20:00 uur" is
   "start" 18:00 en "end" 20:00). Staat er één tijd, laat "end" dan null; staat er geen tijd, gebruik null.
6. "total" is het totaalbedrag van de bestelling dat je betaalde, of null als dat er niet staat.
7. Per product: "name" precies zoals in de mail, "quantity" het aantal, "price" de prijs per stuk zonder korting, "total" het
   regelbedrag zonder korting en "discount" de korting op dit product (uit het Bonusvoordeel of een vergelijkbaar overzicht) als
   positief getal, of 0.
8. Laat weg: statiegeld, tasjes, bezorg- en servicekosten, koopzegels, klapkratten en producten die gratis zijn toegevoegd.
9. Bedragen als getal met een punt (1.29), zonder euroteken."""

_PROMPT_EN = """You get one or more order confirmation emails from {store}. Turn them into JSON, exactly in the shape below.
Give only the JSON, with no explanation before or after it.

{{"store": "{key}", "orders": [
  {{"orderNumber": "123456789", "date": "2025-03-14", "status": "delivered", "deliveryWindow": {{"start": "18:00", "end": "20:00"}},
    "total": 141.53, "items": [
    {{"name": "AH Bananen tros", "quantity": 2, "price": 1.19, "total": 2.38, "discount": 0}},
    {{"name": "AH Greenfields Beefburger naturel 2 stuks", "quantity": 16, "price": 4.99, "total": 79.84, "discount": 39.92}}
  ]}}
]}}

Rules:
1. One object per order. When the same order is in more than one email (confirmation, change, cancellation), only take the last
   version.
2. "date" is required: the delivery or pick-up date of the order as YYYY-MM-DD. When it is not in the email, use the date of the email.
   MealieShopper uses this date (and the order number) to recognise an order it already has, so nothing is counted twice.
3. "orderNumber" is the order number from the email, as text. When there is no number, use null.
4. "status": "delivered" for an order that was delivered or picked up, also when the email is only the confirmation and the date has
   passed; "cancelled" when the email says the order was cancelled (MealieShopper does not import those); otherwise null.
5. "deliveryWindow" is the delivery or pick-up time from the email, with "start" and "end" as HH:MM (with "Between 18:00 and 20:00"
   "start" is 18:00 and "end" is 20:00). With one time, make "end" null; without a time, use null.
6. "total" is the total amount of the order that you paid, or null when it is not there.
7. Per product: "name" exactly as in the email, "quantity" the number of items, "price" the price per item without discount, "total"
   the line amount without discount and "discount" the discount on this product (from the Bonus savings or a similar overview) as a
   positive number, or 0.
8. Leave out: deposits, bags, delivery and service costs, savings stamps, crates and products that were added for free.
9. Amounts as a number with a dot (1.29), without a euro sign."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_COLUMNS_SQL: str = "PRAGMA table_info(imported_purchases)"
_ADD_COLUMN_SQL: str = "ALTER TABLE imported_purchases ADD COLUMN {name} {kind}"


def _missing_columns(rows: Iterable[Any]) -> list[tuple[str, str]]:
    """De kolommen van `_ADDED_COLUMNS` die niet in `PRAGMA table_info` (`rows`) staan."""
    present: set[str] = {row["name"] for row in rows}
    return [(name, kind) for name, kind in _ADDED_COLUMNS if name not in present]


def _added_elsewhere(exc: sqlite3.OperationalError) -> bool:
    """Een andere verbinding voegde de kolom net toe: twee die hem tegelijk zien ontbreken mogen niet struikelen over elkaars
    ALTER TABLE."""
    return "duplicate column" in str(exc)


def _connect() -> sqlite3.Connection:
    """Een sqlite3-verbinding met het schema, voor `backup.ensure_schemas`. De rest gebruikt `_aconnect`."""
    auth.ensure_db()
    conn = auth.connect()
    conn.executescript(SCHEMA)
    for name, kind in _missing_columns(conn.execute(_COLUMNS_SQL)):
        _add_column(conn, name, kind)
    return conn


def _add_column(conn: sqlite3.Connection, name: str, kind: str) -> None:
    """De migratie (zie `_added_elsewhere`)."""
    try:
        conn.execute(_ADD_COLUMN_SQL.format(name=name, kind=kind))
    except sqlite3.OperationalError as exc:
        if not _added_elsewhere(exc):
            raise


@asynccontextmanager
async def _aconnect() -> AsyncIterator[aiosqlite.Connection]:
    """Async tweeling van `_connect`: hetzelfde schema en dezelfde migratie. Sluit zonder commit, zoals `auth.aconnect`."""
    await auth.aensure_db()
    async with auth.aconnect() as conn:
        await conn.executescript(SCHEMA)
        for name, kind in _missing_columns(await conn.execute_fetchall(_COLUMNS_SQL)):
            try:
                await conn.execute(_ADD_COLUMN_SQL.format(name=name, kind=kind))
            except sqlite3.OperationalError as exc:
                if not _added_elsewhere(exc):
                    raise
        yield conn


def _store(value: Any) -> str:
    store = str(value or "").strip().lower()
    if store not in STORES:
        raise ValueError(tr("Onbekende winkel.", "Unknown store."))
    return store


def prompt(store: Any) -> str:
    """De prompt voor een AI-assistent die bevestigingsmails omzet naar de JSON die `run` inleest."""
    key = _store(store)
    return tr(_PROMPT_NL, _PROMPT_EN).format(store=STORE_NAMES.get(key, key), key=key)


def _strip_fence(text: str) -> str:
    """Een assistent zet JSON vaak tussen ```json en ```. Dat halen we weg."""
    match = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    return match.group(1) if match else text


def _amount(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("€", "").replace(" ", "")
    if "," in text and "." not in text:
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def _day(value: Any, today: date) -> str | None:
    text = str(value or "").strip()[:10]
    try:
        day = date.fromisoformat(text)
    except ValueError:
        return None
    return day.isoformat() if date(2000, 1, 1) <= day <= today else None


def _status(value: Any) -> tuple[str | None, bool]:
    """(status, herkend) uit de mail: "delivered", "cancelled" of None. Een onbekende tekst geeft (None, False)."""
    text = " ".join(str(value or "").strip().lower().split())
    if not text or text in {"null", "none", "onbekend", "unknown"}:
        return None, True
    status = _STATUS_WORDS.get(text)
    return status, status is not None


_CLOCK = re.compile(r"(?<!\d)(\d{1,2})[:.](\d{2})(?!\d)")


def _clock(value: Any) -> str | None:
    """Een tijd als UU:MM ("18:00", "8.30", "2025-03-14T18:00:00"), of None."""
    match = _CLOCK.search(str(value or ""))
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    return f"{hour:02d}:{minute:02d}" if hour < 24 and minute < 60 else None


def _window(value: Any) -> tuple[str | None, str | None, bool]:
    """(begin, eind, bruikbaar) van het bezorgmoment uit de mail: {"start": "18:00", "end": "20:00"} of de tekst "18:00-20:00".
    Zonder tijd is dat (None, None, True); een tijd die niet te lezen is of een eind voor het begin (None, None, False)."""
    if value in (None, "", {}) or (isinstance(value, str) and value.strip().lower() in {"null", "none"}):
        return None, None, True
    if isinstance(value, dict):
        raw_start, raw_end = value.get("start"), value.get("end")
    else:
        times = _CLOCK.findall(str(value))
        raw_start, raw_end = (":".join(times[0]) if times else value), (":".join(times[1]) if len(times) > 1 else None)
    start, end = _clock(raw_start), _clock(raw_end)
    if (raw_start not in (None, "") and start is None) or (raw_end not in (None, "") and end is None):
        return None, None, False
    if start is None and end is not None:
        start, end = end, None
    if start and end and end <= start:
        return None, None, False
    return start, end, True


def _order_ref(number: Any, day: str, total: float | None) -> str:
    text = str(number).strip() if number not in (None, "") else ""
    if text and text.lower() not in {"null", "none"}:
        return text[:64]
    return f"dag:{day}:{total:.2f}" if total is not None else f"dag:{day}"


def _item(raw: Any) -> dict[str, Any] | None:
    """Een productregel, of None als die niet bruikbaar is (geen naam, geen aantal of niets betaald)."""
    if not isinstance(raw, dict):
        return None
    name = " ".join(str(raw.get("name") or "").split())[:MAX_NAME_CHARS]
    quantity = _amount(raw.get("quantity"))
    if not name or quantity is None or quantity <= 0:
        return None
    price = _amount(raw.get("price"))
    total = _amount(raw.get("total"))
    if total is None and price is not None:
        total = price * quantity
    if price is None and total is not None:
        price = total / quantity
    if total is None or total <= 0:
        return None
    discount = abs(_amount(raw.get("discount")) or 0.0)
    paid = max(0.0, total - discount) / quantity
    return {"title": name, "quantity": quantity, "paid": round(paid, 2), "regular": round(price, 2) if price is not None else None}


def parse(data: Any, store: Any, today: date | None = None) -> tuple[str, list[dict[str, Any]], list[str]]:
    """(winkel, bestellingen, problemen) uit de JSON van de assistent: tekst of al gelezen. Een bestelling zonder bruikbare datum of
    zonder producten, of een geannuleerde, slaan we over en staat in de problemen. Een status of bezorgmoment dat niet te lezen is,
    laten we leeg; de bestelling telt wel, met een opmerking in `notes`."""
    key = _store(store)
    today = today or datetime.now(timezone.utc).date()
    if isinstance(data, str):
        if len(data) > MAX_TEXT_CHARS:
            raise ValueError(tr("De tekst is te lang.", "The text is too long."))
        try:
            data = json.loads(_strip_fence(data))
        except ValueError as exc:
            raise ValueError(tr("Dit is geen geldige JSON.", "This is not valid JSON.")) from exc
    if isinstance(data, dict):
        named = str(data.get("store") or "").strip().lower()
        if named and named != key:
            raise ValueError(tr(f"De JSON is van een andere winkel ({named}).", f"The JSON is for another store ({named})."))
        data = data.get("orders")
    if not isinstance(data, list):
        raise ValueError(tr("Er staat geen lijst met bestellingen in.", "There is no list of orders in it."))
    if len(data) > MAX_ORDERS:
        raise ValueError(tr(f"Hooguit {MAX_ORDERS} bestellingen per keer.", f"At most {MAX_ORDERS} orders at a time."))
    orders, problems = [], []
    for index, raw in enumerate(data, 1):
        if not isinstance(raw, dict):
            problems.append(tr(f"Bestelling {index}: geen object.", f"Order {index}: not an object."))
            continue
        day = _day(raw.get("date"), today)
        if day is None:
            problems.append(tr(f"Bestelling {index}: geen geldige datum (JJJJ-MM-DD, niet in de toekomst).",
                               f"Order {index}: no valid date (YYYY-MM-DD, not in the future)."))
            continue
        status, known = _status(raw.get("status"))
        if status == STATUS_CANCELLED:
            problems.append(tr(f"Bestelling {index} ({day}): geannuleerd, niet ingelezen.", f"Order {index} ({day}): cancelled, not imported."))
            continue
        raw_items = raw.get("items") if isinstance(raw.get("items"), list) else []
        items = [item for item in (_item(entry) for entry in raw_items[:MAX_ITEMS]) if item]
        if not items:
            problems.append(tr(f"Bestelling {index} ({day}): geen bruikbare producten.", f"Order {index} ({day}): no usable products."))
            continue
        notes = [] if known else [tr(f"status \"{str(raw.get('status'))[:40]}\" onbekend, leeg gelaten",
                                     f"status \"{str(raw.get('status'))[:40]}\" unknown, left empty")]
        slot_start, slot_end, readable = _window(raw.get("deliveryWindow"))
        if not readable:
            notes.append(tr("bezorgmoment niet te lezen, leeg gelaten", "delivery time not readable, left empty"))
        total = _amount(raw.get("total"))
        orders.append({
            "ref": _order_ref(raw.get("orderNumber"), day, total), "number": str(raw.get("orderNumber") or "") or None,
            "date": day, "total": round(total, 2) if total is not None else None, "items": items,
            "status": status, "slotStart": slot_start, "slotEnd": slot_end, "notes": notes,
        })
    return key, orders, problems


_TABLES_SQL: str = "SELECT name FROM sqlite_master WHERE type = 'table'"


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {row["name"] for row in conn.execute(_TABLES_SQL)}


async def _atables(conn: aiosqlite.Connection) -> set[str]:
    """Async tweeling van `_tables` (die blijft voor `account_orders`)."""
    return {row["name"] for row in await conn.execute_fetchall(_TABLES_SQL)}


def _account_orders_sql(store: str) -> tuple[set[str], str]:
    """(de tabellen die er moeten zijn, de SQL) voor `account_orders`: per bestelling met producten het nummer en de dag."""
    table, items = f"{store}_orders", f"{store}_order_items"
    return {table, items}, f"""SELECT CAST(o.order_id AS TEXT) AS id, substr(COALESCE(o.delivery_date, ''), 1, 10) AS day FROM {table} o
            WHERE EXISTS (SELECT 1 FROM {items} i WHERE i.order_id = o.order_id)"""


def _account_sets(rows: Iterable[Any]) -> tuple[set[str], set[str]]:
    numbers: set[str] = set()
    days: set[str] = set()
    for row in rows:
        numbers.add(str(row["id"]))
        if row["day"]:
            days.add(row["day"])
    return numbers, days


def account_orders(conn: sqlite3.Connection, store: str) -> tuple[set[str], set[str]]:
    """(bestelnummers, dagen) van de bestellingen die de app met producten uit je account van de winkel heeft.

    Een bestelling waarvan de app de producten (nog) niet kon ophalen, telt niet mee (#222): Jumbo weigert de details van oude
    bestellingen, die dan alleen als kop in `jumbo_orders` staan. Zo'n bestelling is er voor de prijzen nog niet, dus de ingelezen
    versie uit je mails mag erbij. Komen de producten later toch binnen, dan telt de bestelling weer mee en valt de ingelezen versie
    weg (bij Bestellingen, in de prijzen en in het zoeken), zodat niets dubbel telt.

    Synchroon voor `prices.bought_ids` (`product_images`, tot Taak 16), de rest gebruikt `aaccount_orders`."""
    needed, sql = _account_orders_sql(store)
    if not needed <= _tables(conn):
        return set(), set()
    return _account_sets(conn.execute(sql))


async def aaccount_orders(conn: aiosqlite.Connection, store: str) -> tuple[set[str], set[str]]:
    """Async tweeling van `account_orders`."""
    needed, sql = _account_orders_sql(store)
    if not needed <= await _atables(conn):
        return set(), set()
    return _account_sets(await conn.execute_fetchall(sql))


async def _duplicate(
    conn: aiosqlite.Connection, store: str, order: dict[str, Any], seen: set[str], numbers: set[str], days: set[str]
) -> str | None:
    """Waarom een bestelling er al is, of None als ze nieuw is."""
    if order["ref"] in seen:
        return tr("staat twee keer in deze JSON", "is twice in this JSON")
    if await conn.execute_fetchall("SELECT 1 FROM imported_purchases WHERE store = ? AND order_ref = ? LIMIT 1", (store, order["ref"])):
        return tr("is al ingelezen", "was already imported")
    if order["number"] and order["number"] in numbers:
        return tr("staat al in je bestellingen", "is already in your orders")
    if order["date"] in days:
        return tr("op deze dag staat al een bestelling in je account", "your account already has an order on this day")
    return None


async def run(data: Any, store: Any, dry_run: bool = False, now: str | None = None) -> dict[str, Any]:
    """Lees de bestellingen in (of, met `dry_run`, laat alleen zien wat er zou gebeuren). Geeft per bestelling of ze nieuw is."""
    now = now or _now()
    key, orders, problems = parse(data, store, date.fromisoformat(now[:10]))
    if not orders:
        raise ValueError(problems[0] if problems else tr("Er staan geen bestellingen in.", "There are no orders in it."))
    report, fresh, seen = [], [], set()
    async with _aconnect() as conn:
        numbers, days = await aaccount_orders(conn, key)
        for order in orders:
            reason = await _duplicate(conn, key, order, seen, numbers, days)
            seen.add(order["ref"])
            report.append({"ref": order["ref"], "date": order["date"], "total": order["total"], "items": len(order["items"]),
                           "status": order["status"], "slotStart": order["slotStart"], "slotEnd": order["slotEnd"],
                           "notes": order["notes"], "new": reason is None, "reason": reason})
            if reason is None:
                fresh.append(order)
        if not dry_run and fresh:
            await conn.executemany(
                """INSERT INTO imported_purchases (store, order_ref, position, day, title, quantity, paid, regular, order_total,
                                                   imported_at, status, slot_start, slot_end)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [(key, order["ref"], position, order["date"], item["title"], item["quantity"], item["paid"], item["regular"],
                  order["total"], now, order["status"], order["slotStart"], order["slotEnd"])
                 for order in fresh for position, item in enumerate(order["items"])],
            )
            await conn.commit()
    return {
        "store": key, "dryRun": bool(dry_run), "orders": report, "new": len(fresh), "duplicates": len(report) - len(fresh),
        "lines": sum(len(order["items"]) for order in fresh), "problems": problems[:MAX_PROBLEMS], "problemCount": len(problems),
        "imported": 0 if dry_run else sum(len(order["items"]) for order in fresh),
    }


async def summary(store: Any) -> dict[str, Any]:
    """Wat er van een winkel is ingelezen: aantal bestellingen en regels, eerste en laatste dag, en de prompt."""
    key = _store(store)
    async with _aconnect() as conn:
        async with conn.execute(
            "SELECT COUNT(DISTINCT order_ref) AS orders, COUNT(*) AS lines, MIN(day) AS first, MAX(day) AS last FROM imported_purchases WHERE store = ?",
            (key,),
        ) as cursor:
            row: aiosqlite.Row = await cursor.fetchone()
    return {"store": key, "orders": row["orders"], "lines": row["lines"], "first": row["first"], "last": row["last"], "prompt": prompt(key)}


async def remove(store: Any, ref: Any = None) -> dict[str, Any]:
    """Haal de ingelezen aankopen van een winkel weg: alle, of met `ref` alleen die ene bestelling."""
    key = _store(store)
    only = str(ref) if ref not in (None, "") else None
    where, args = ("store = ? AND order_ref = ?", (key, only)) if only is not None else ("store = ?", (key,))
    async with _aconnect() as conn:
        async with conn.execute(f"SELECT COUNT(DISTINCT order_ref) FROM imported_purchases WHERE {where}", args) as cursor:
            removed: int = (await cursor.fetchone())[0]
        await conn.execute(f"DELETE FROM imported_purchases WHERE {where}", args)
        await conn.commit()
    return {"store": key, "removed": removed, **({"ref": only} if only is not None else {})}


# --- ingelezen bestellingen bij Bestellingen ------------------------------------------------------

def _quantity(value: Any) -> int | float:
    """2.0 wordt 2: zo staat een aantal ook in de bestellingen uit je account."""
    number = float(value or 0)
    return int(number) if number.is_integer() else number


SUMMARY_NAMES = 4  # zoveel productnamen per aankoop in de lijst (de kaarten en het overzicht); de foto's staan in ah_orders
SUMMARY_IMAGES = 6  # zoals ah_orders en jumbo_orders: zoveel foto's per bestelling in de lijst


def split_lines(value: Any) -> list[str]:
    """Een GROUP_CONCAT met een regeleinde als scheiding terug als lijst, zonder lege waarden (ook voor de andere lijstroutes)."""
    return [part for part in str(value or "").split("\n") if part]


async def _order_rows(conn: aiosqlite.Connection, store: str, ref: str | None = None, summary: bool = False) -> list[Any]:
    """Per ingelezen bestelling één rij. Een bestelling met hetzelfde nummer of op dezelfde dag als een bestelling uit je account
    staat daar al (net als in `prices._imported_rows`) en komt er dus niet nog een keer bij. Met `summary` ook de eerste namen."""
    numbers, days = await aaccount_orders(conn, store)
    where, args = ("store = ? AND order_ref = ?", (store, ref)) if ref is not None else ("store = ?", (store,))
    names = f"""(SELECT GROUP_CONCAT(title, char(10)) FROM (SELECT title FROM imported_purchases n
                    WHERE n.store = p.store AND n.order_ref = p.order_ref AND title != '' ORDER BY position LIMIT {SUMMARY_NAMES})) AS summary_names,"""
    rows = await conn.execute_fetchall(
        f"""SELECT order_ref, MIN(day) AS day, MAX(order_total) AS order_total, MIN(imported_at) AS imported_at, COUNT(*) AS lines,
                   {names if summary else ""}
                   MAX(status) AS status, MAX(slot_start) AS slot_start, MAX(slot_end) AS slot_end,
                   SUM(quantity) AS units, SUM(COALESCE(paid, 0) * quantity) AS paid_total
            FROM imported_purchases p WHERE {where} GROUP BY order_ref ORDER BY day DESC, order_ref DESC""",
        args,
    )
    return [row for row in rows if row["order_ref"] not in numbers and row["day"] not in days]


def _moment(day: str, clock: Any) -> str | None:
    """Dag en tijd van het bezorgmoment zonder tijdzone ("2022-01-12T18:00"), zoals AH ze geeft: de tijd uit de mail is de lokale."""
    return f"{day}T{clock}" if clock else None


def _bonus(paid: Any, regular: Any) -> bool:
    return paid is not None and regular is not None and paid < regular - 0.004


def _order_json(store: str, row: Any, summary: bool = False) -> dict[str, Any]:
    ref = row["order_ref"]
    total = row["order_total"] if row["order_total"] is not None else round(row["paid_total"] or 0, 2)
    return {
        "id": ref, "ref": ref, "store": store, "imported": True, "kind": "imported", "number": None if ref.startswith("dag:") else ref,
        "date": row["day"], "deliveryDate": row["day"], "total": total, "itemCount": row["lines"], "units": _quantity(row["units"]),
        # Een ingelezen bestelling is altijd een aankoop die je deed: ze telt in de Prijsgeschiedenis als gekocht, en een geannuleerde
        # lezen we niet in. Zonder status uit de mail (zoals bij een import van vóór 3.5.0) is ze dus afgeleverd, zoals elke
        # afgelopen bestelling uit je account.
        "status": row["status"] or STATUS_DELIVERED,
        "slotStart": _moment(row["day"], row["slot_start"]), "slotEnd": _moment(row["day"], row["slot_end"]),
        "importedAt": row["imported_at"], **({"productNames": split_lines(row["summary_names"])} if summary else {}),
    }


async def _lines(conn: aiosqlite.Connection, store: str, ref: str | None = None) -> list[Any]:
    where, args = ("store = ? AND order_ref = ?", (store, ref)) if ref is not None else ("store = ?", (store,))
    return list(await conn.execute_fetchall(
        f"SELECT order_ref, title, quantity, paid, regular FROM imported_purchases WHERE {where} ORDER BY order_ref, position", args
    ))


def _summary(lines: list[Any], linked: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """`images`, `savings` en `bonusCount` van een bestelling, zoals `jumbo_orders` ze voor de lijst geeft: de foto's van de
    gekoppelde producten, wat de korting op de producten scheelde en hoeveel regels korting hadden."""
    images: list[str] = []
    for line in lines:
        image = linked.get(line["title"], {}).get("image")
        if image and image not in images and len(images) < SUMMARY_IMAGES:
            images.append(image)
    bonus = [line for line in lines if _bonus(line["paid"], line["regular"])]
    savings = sum((line["regular"] - line["paid"]) * line["quantity"] for line in bonus)
    status_kind, status_text = purchase_lines.status_kind("imported", {})
    return {"images": images, "savings": round(savings, 2), "bonusCount": len(bonus), "statusKind": status_kind, "statusText": status_text}


async def list_orders(store: Any, summary: bool = False) -> dict[str, Any]:
    """De ingelezen bestellingen van een winkel, de nieuwste eerst, zoals ze bij de afgelopen bestellingen staan. Met `summary`
    (de lijstroute) per bestelling ook `productNames` (de eerste vier namen), `images`, `savings` en `bonusCount`, voor de tabel,
    de kaarten en het overzicht."""
    from . import prices  # prices leest ook price_import; pas hier ophalen houdt de import rond

    key = _store(store)
    async with _aconnect() as conn:
        rows = await _order_rows(conn, key, summary=summary)
        if not summary or not rows:
            return {"store": key, "orders": [_order_json(key, row, summary) for row in rows]}
        per_order: dict[str, list[Any]] = {}
        for line in await _lines(conn, key):
            per_order.setdefault(line["order_ref"], []).append(line)
    linked = await prices.link_names(key, {line["title"] for lines in per_order.values() for line in lines})
    return {"store": key, "orders": [
        {**_order_json(key, row, summary), **_summary(per_order.get(row["order_ref"], []), linked)} for row in rows
    ]}


async def get_order(store: Any, ref: Any) -> dict[str, Any] | None:
    """Eén ingelezen bestelling met de producten uit de mail, of None als die er niet (meer) is. Elke regel heeft, als de naam bij
    een product van de winkel hoort, ook `productId`, `image`, `brand` en `unitSize` van dat product (zie `prices.link_names`), en
    `link` als je die naam zelf koppelde (#238), en de velden van `purchase_lines.enrich`: de korting is die tegen de prijs uit de mail
    (`price`, `discountSource` `store`), want voor deze regels bewaren we geen gewone prijs van 30 dagen. `status` blijft die uit de mail,
    `statusKind` is `imported`."""
    from . import prices

    key = _store(store)
    ref = str(ref or "")
    async with _aconnect() as conn:
        rows = await _order_rows(conn, key, ref)
        if not rows:
            return None
        lines = await _lines(conn, key, ref)
    linked = await prices.link_names(key, {line["title"] for line in lines})
    items = []
    for line in lines:
        product = linked.get(line["title"], {})
        items.append({
            "title": line["title"], "quantity": _quantity(line["quantity"]), "paid": line["paid"], "price": line["regular"],
            "amount": round(line["paid"] * line["quantity"], 2) if line["paid"] is not None else None,
            "bonus": _bonus(line["paid"], line["regular"]),
            "productId": product.get("productId"), "image": product.get("image"), "brand": product.get("brand"),
            "unitSize": product.get("unitSize"),
            # De naam van het product in de winkel, en je eigen koppeling van deze naam (#238): zo kan het scherm *Ander product
            # kiezen* en *Ontkoppelen* tonen.
            "productTitle": product.get("title"), "link": product.get("link"),
        })
    async with _aconnect() as conn:
        totals = await purchase_lines.enrich(conn, key, "imported", rows[0]["day"], items)
    kind, text = purchase_lines.status_kind("imported", rows[0])
    return {**_order_json(key, rows[0]), "items": items, **totals, "statusKind": kind, "statusText": text}


async def search_lines(conn: aiosqlite.Connection, store: str, words: list[str], fold: Callable[[Any], str]) -> list[dict[str, Any]]:
    """De regels van de ingelezen bestellingen waar alle woorden in staan, in de vorm van `<winkel>_orders.search_purchases`
    (`fold` is de vouwfunctie van die zoekopdracht). Een bestelling die de app ook uit je account kent, telt niet mee."""
    if "imported_purchases" not in await _atables(conn):
        return []
    numbers, days = await aaccount_orders(conn, store)
    found: list[dict[str, Any]] = []
    for row in await conn.execute_fetchall("SELECT order_ref, day, title, quantity, paid FROM imported_purchases WHERE store = ?", (store,)):
        if row["order_ref"] in numbers or row["day"] in days or not all(word in fold(row["title"]) for word in words):
            continue
        found.append({
            "source": "bestelling", "imported": True, "product": row["title"], "brand": None, "quantity": _quantity(row["quantity"]),
            "amount": round(row["paid"] * row["quantity"], 2) if row["paid"] is not None else None, "date": row["day"],
            "id": row["order_ref"], "open": False,
        })
    return found
