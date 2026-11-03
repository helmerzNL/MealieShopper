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
"""
from __future__ import annotations

import json
import re
from contextlib import closing
from datetime import date, datetime, timezone
from typing import Any

from . import auth, offers
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
  PRIMARY KEY (store, order_ref, position)
);
CREATE INDEX IF NOT EXISTS imported_purchases_day ON imported_purchases (store, day);
"""

STORE_NAMES = {"ah": "Albert Heijn", "jumbo": "Jumbo", "picnic": "Picnic", "crisp": "Crisp"}

_PROMPT_NL = """Je krijgt een of meer bevestigingsmails van bestellingen bij {store}. Zet ze om naar JSON, precies in de vorm hieronder.
Geef alleen de JSON, zonder uitleg ervoor of erna.

{{"store": "{key}", "orders": [
  {{"orderNumber": "123456789", "date": "2025-03-14", "total": 141.53, "items": [
    {{"name": "AH Bananen tros", "quantity": 2, "price": 1.19, "total": 2.38, "discount": 0}},
    {{"name": "AH Greenfields Beefburger naturel 2 stuks", "quantity": 16, "price": 4.99, "total": 79.84, "discount": 39.92}}
  ]}}
]}}

Regels:
1. Eén object per bestelling. Komt dezelfde bestelling in meer mails voor (bevestiging, wijziging), neem dan alleen de laatste versie.
2. "date" is verplicht: de bezorg- of afhaaldatum van de bestelling als JJJJ-MM-DD. Staat die er niet in, neem dan de datum van de mail.
   MealieShopper herkent aan deze datum (en aan het bestelnummer) een bestelling die er al is, zodat niets dubbel telt.
3. "orderNumber" is het bestelnummer uit de mail, als tekst. Staat er geen nummer in, gebruik dan null.
4. "total" is het totaalbedrag van de bestelling dat je betaalde, of null als dat er niet staat.
5. Per product: "name" precies zoals in de mail, "quantity" het aantal, "price" de prijs per stuk zonder korting, "total" het
   regelbedrag zonder korting en "discount" de korting op dit product (uit het Bonusvoordeel of een vergelijkbaar overzicht) als
   positief getal, of 0.
6. Laat weg: statiegeld, tasjes, bezorg- en servicekosten, koopzegels, klapkratten en producten die gratis zijn toegevoegd.
7. Bedragen als getal met een punt (1.29), zonder euroteken."""

_PROMPT_EN = """You get one or more order confirmation emails from {store}. Turn them into JSON, exactly in the shape below.
Give only the JSON, with no explanation before or after it.

{{"store": "{key}", "orders": [
  {{"orderNumber": "123456789", "date": "2025-03-14", "total": 141.53, "items": [
    {{"name": "AH Bananen tros", "quantity": 2, "price": 1.19, "total": 2.38, "discount": 0}},
    {{"name": "AH Greenfields Beefburger naturel 2 stuks", "quantity": 16, "price": 4.99, "total": 79.84, "discount": 39.92}}
  ]}}
]}}

Rules:
1. One object per order. When the same order is in more than one email (confirmation, change), only take the last version.
2. "date" is required: the delivery or pick-up date of the order as YYYY-MM-DD. When it is not in the email, use the date of the email.
   MealieShopper uses this date (and the order number) to recognise an order it already has, so nothing is counted twice.
3. "orderNumber" is the order number from the email, as text. When there is no number, use null.
4. "total" is the total amount of the order that you paid, or null when it is not there.
5. Per product: "name" exactly as in the email, "quantity" the number of items, "price" the price per item without discount, "total"
   the line amount without discount and "discount" the discount on this product (from the Bonus savings or a similar overview) as a
   positive number, or 0.
6. Leave out: deposits, bags, delivery and service costs, savings stamps, crates and products that were added for free.
7. Amounts as a number with a dot (1.29), without a euro sign."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect():
    auth.ensure_db()
    conn = auth.connect()
    conn.executescript(SCHEMA)
    return conn


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
    zonder producten slaan we over en staat in de problemen."""
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
        raw_items = raw.get("items") if isinstance(raw.get("items"), list) else []
        items = [item for item in (_item(entry) for entry in raw_items[:MAX_ITEMS]) if item]
        if not items:
            problems.append(tr(f"Bestelling {index} ({day}): geen bruikbare producten.", f"Order {index} ({day}): no usable products."))
            continue
        total = _amount(raw.get("total"))
        orders.append({
            "ref": _order_ref(raw.get("orderNumber"), day, total), "number": str(raw.get("orderNumber") or "") or None,
            "date": day, "total": round(total, 2) if total is not None else None, "items": items,
        })
    return key, orders, problems


def _tables(conn) -> set[str]:
    return {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def account_orders(conn, store: str) -> tuple[set[str], set[str]]:
    """(bestelnummers, dagen) van de bestellingen die de app uit je account van de winkel heeft."""
    table = f"{store}_orders"
    if table not in _tables(conn):
        return set(), set()
    numbers, days = set(), set()
    for row in conn.execute(f"SELECT CAST(order_id AS TEXT) AS id, substr(COALESCE(delivery_date, ''), 1, 10) AS day FROM {table}"):
        numbers.add(str(row["id"]))
        if row["day"]:
            days.add(row["day"])
    return numbers, days


def _duplicate(conn, store: str, order: dict[str, Any], seen: set[str], numbers: set[str], days: set[str]) -> str | None:
    """Waarom een bestelling er al is, of None als ze nieuw is."""
    if order["ref"] in seen:
        return tr("staat twee keer in deze JSON", "is twice in this JSON")
    if conn.execute("SELECT 1 FROM imported_purchases WHERE store = ? AND order_ref = ? LIMIT 1", (store, order["ref"])).fetchone():
        return tr("is al ingelezen", "was already imported")
    if order["number"] and order["number"] in numbers:
        return tr("staat al in je bestellingen", "is already in your orders")
    if order["date"] in days:
        return tr("op deze dag staat al een bestelling in je account", "your account already has an order on this day")
    return None


def run(data: Any, store: Any, dry_run: bool = False, now: str | None = None) -> dict[str, Any]:
    """Lees de bestellingen in (of, met `dry_run`, laat alleen zien wat er zou gebeuren). Geeft per bestelling of ze nieuw is."""
    now = now or _now()
    key, orders, problems = parse(data, store, date.fromisoformat(now[:10]))
    if not orders:
        raise ValueError(problems[0] if problems else tr("Er staan geen bestellingen in.", "There are no orders in it."))
    report, fresh, seen = [], [], set()
    with closing(_connect()) as conn:
        numbers, days = account_orders(conn, key)
        for order in orders:
            reason = _duplicate(conn, key, order, seen, numbers, days)
            seen.add(order["ref"])
            report.append({"ref": order["ref"], "date": order["date"], "total": order["total"], "items": len(order["items"]),
                           "new": reason is None, "reason": reason})
            if reason is None:
                fresh.append(order)
        if not dry_run and fresh:
            with conn:
                conn.executemany(
                    """INSERT INTO imported_purchases (store, order_ref, position, day, title, quantity, paid, regular, order_total, imported_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [(key, order["ref"], position, order["date"], item["title"], item["quantity"], item["paid"], item["regular"],
                      order["total"], now) for order in fresh for position, item in enumerate(order["items"])],
                )
    return {
        "store": key, "dryRun": bool(dry_run), "orders": report, "new": len(fresh), "duplicates": len(report) - len(fresh),
        "lines": sum(len(order["items"]) for order in fresh), "problems": problems[:MAX_PROBLEMS], "problemCount": len(problems),
        "imported": 0 if dry_run else sum(len(order["items"]) for order in fresh),
    }


def summary(store: Any) -> dict[str, Any]:
    """Wat er van een winkel is ingelezen: aantal bestellingen en regels, eerste en laatste dag, en de prompt."""
    key = _store(store)
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT COUNT(DISTINCT order_ref) AS orders, COUNT(*) AS lines, MIN(day) AS first, MAX(day) AS last FROM imported_purchases WHERE store = ?",
            (key,),
        ).fetchone()
    return {"store": key, "orders": row["orders"], "lines": row["lines"], "first": row["first"], "last": row["last"], "prompt": prompt(key)}


def remove(store: Any) -> dict[str, Any]:
    """Haal alle ingelezen aankopen van een winkel weg."""
    key = _store(store)
    with closing(_connect()) as conn, conn:
        removed = conn.execute("SELECT COUNT(DISTINCT order_ref) FROM imported_purchases WHERE store = ?", (key,)).fetchone()[0]
        conn.execute("DELETE FROM imported_purchases WHERE store = ?", (key,))
    return {"store": key, "removed": removed}
