"""De regels van een aankoop verrijkt voor de detailroutes: categorie, korting, eerder gekocht, bespaard en status.

Eén plek voor de vier soorten aankopen (een bestelling van AH of Jumbo, een kassabon van AH en een ingelezen bestelling), zodat de
bestelpagina ze op dezelfde manier toont. De korting is die tegen de gewone prijs van 30 dagen (de kolom `regular_price`, die
`prices.fill_regular_prices` vult), en zonder die prijs de prijs ervoor die de winkel zelf noemt.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping
from typing import Any

import aiosqlite

from . import product_links
from .offer_categories import purchase_category

log = logging.getLogger(__name__)

KINDS: tuple[str, ...] = ("order", "receipt", "imported")
# Kortere namen (na het weglaten van "ah") passen op te veel regels van een bon.
MIN_KEY: int = 3


def discount_percent(regular: float | None, paid: float | None) -> int | None:
    """De korting in hele procenten: `(regular - paid) / regular * 100`, hooguit 100. None zonder prijzen, bij een gewone prijs van
    nul of minder, of als de korting onder 1% blijft (ook als je meer betaalde)."""
    if regular is None or paid is None or regular <= 0:
        return None
    raw: float = (regular - paid) / regular * 100
    return min(round(raw), 100) if raw >= 1 else None


def _fold(name: Any) -> str:
    """Kleine letters zonder spaties en leestekens: "AH ROOMKAAS" en "AHROOMKAAS" worden allebei "ahroomkaas"."""
    return re.sub(r"[\W_]+", "", str(name or "").casefold())


def _keys(name: Any) -> set[str]:
    """De gevouwen naam, en dezelfde naam zonder het huismerk vooraan: een bonusregel heet "AHKOMKOMMERP", de regel "KOMKOMMER"."""
    folded: str = _fold(name)
    keys: set[str] = {folded, folded.removeprefix("ah")}
    return {key for key in keys if len(key) >= MIN_KEY}


def _matches(bonus_keys: set[str], line_keys: set[str]) -> bool:
    return any(
        a.startswith(b) or b.startswith(a) for a in bonus_keys for b in line_keys
    )


def _bonus_amount(discount: Any) -> float | None:
    """Het bedrag van een bonusregel, positief. None voor iets dat geen korting is (een positief of onleesbaar bedrag)."""
    amount: float | None = _number(discount.get("amount")) if isinstance(discount, Mapping) else None
    return -amount if amount is not None and amount < 0 else None


def _assign_bonus(lines: list[dict[str, Any]], discounts: Any) -> tuple[dict[int, float], float]:
    """Verdeel de bonusregels over de regels van de bon (zie `match_receipt_bonus`). Geeft per regelindex het bonusbedrag en het
    totaal van de bonusregels die bij geen enkele regel passen."""
    line_keys: list[set[str]] = [_keys(line.get("name")) for line in lines]
    found: dict[int, float] = {}
    unmatched: float = 0.0
    for discount in discounts if isinstance(discounts, list) else []:
        amount: float | None = _bonus_amount(discount)
        if amount is None:
            continue
        keys: set[str] = _keys(discount.get("name"))
        index: int | None = next((index for index, candidates in enumerate(line_keys) if keys and candidates and _matches(keys, candidates)), None)
        if index is None:
            unmatched += amount
        else:
            found[index] = round(found.get(index, 0.0) + amount, 2)
    # Een regel krijgt nooit meer bonus dan hij kostte (een actie over meer artikelen hangt aan de eerste regel). Wat erboven
    # valt telt, net als een bonusregel die nergens past, alleen in het totaal: zo geven de lijst en de detailroute dezelfde som.
    for index, amount in found.items():
        cap: float = _line_amount(lines[index])
        if amount > cap:
            unmatched += amount - cap
            found[index] = cap
    return found, round(unmatched, 2)


def _line_amount(line: Mapping[str, Any]) -> float:
    """Wat een bonregel vóór de bonus kostte: de prijs per stuk (`_receipt_price`) maal het aantal, nul zonder aantal of prijs."""
    quantity: float = _number(line.get("quantity")) or 0.0
    price: float | None = _receipt_price(line, quantity)
    return round(price * quantity, 2) if price is not None and quantity > 0 else 0.0


def match_receipt_bonus(lines: list[dict[str, Any]], discounts: list[dict[str, Any]]) -> dict[int, float]:
    """Per regelindex het bonusbedrag (positief) van de bonusregels die bij die regel horen, hooguit wat de regel kostte.

    Een bonusregel hoort bij een regel als de ene gevouwen naam (`_keys`) een voorvoegsel is van de andere: AH kort de namen op de bon
    af. Een bonusregel telt één keer, bij de eerste passende regel in de volgorde van de bon. Een regel kan meer bonusregels krijgen.
    """
    return _assign_bonus(lines, discounts)[0]


def unmatched_bonus(lines: list[dict[str, Any]], discounts: list[dict[str, Any]]) -> float:
    """Het bedrag (positief) van de bonusregels die bij geen regel van de bon passen, plus de bonus boven het bedrag van een regel. Dat
    telt alleen in het totaal (`enrich`)."""
    return _assign_bonus(lines, discounts)[1]


async def _tables(conn: aiosqlite.Connection) -> set[str]:
    return {
        row[0]
        for row in await conn.execute_fetchall("SELECT name FROM sqlite_master WHERE type = 'table'")
    }


async def _count(conn: aiosqlite.Connection, sql: str, ids: list[str], before_day: str) -> dict[str, int]:
    marks: str = ", ".join("?" * len(ids))
    return {
        str(row[0]): int(row[1])
        for row in await conn.execute_fetchall(sql.format(marks=marks), (*ids, before_day))
    }


# De aankoopdag van een bestelling, zoals in `prices`: de bezorgdag, anders de dag waarop de app hem voor het eerst zag.
_ORDER_DAY: str = (
    "COALESCE(NULLIF(substr(o.delivery_date, 1, 10), ''), substr(o.first_seen, 1, 10))"
)


async def times_before(
    conn: aiosqlite.Connection | None, store: str, product_ids: Iterable[Any], before_day: str
) -> dict[str, int]:
    """Hoe vaak je elk product eerder kocht: het aantal bestellingen (`<store>_order_items`) van vóór `before_day` met dat product,
    plus bij AH het aantal kassabonnen van vóór die dag met een regel die bij dat product hoort (je eigen koppeling, #238, of de
    automatische in `ah_receipt_products`: `product_links.RECEIPT_LINE_JOIN_SQL`).

    Een aankoop telt één keer, ook als het product er op twee regels staat. Een product dat je niet eerder kocht ontbreekt.
    """
    ids: list[str] = sorted(
        {
            str(product_id)
            for product_id in product_ids
            if product_id not in (None, "", 0)
        }
    )
    if conn is None or not ids or store not in ("ah", "jumbo"):
        return {}
    if store == "ah":
        await product_links.aensure(conn)  # de tabel en `ms_fold` voor de bonregels, ook op een verbinding van `price_import`
    tables: set[str] = await _tables(conn)
    counts: dict[str, int] = {}
    if {f"{store}_orders", f"{store}_order_items"} <= tables:
        counts = await _count(
            conn,
            f"""
            SELECT CAST(i.product_id AS TEXT), COUNT(DISTINCT i.order_id) FROM {store}_order_items i
            JOIN {store}_orders o ON o.order_id = i.order_id
            WHERE CAST(i.product_id AS TEXT) IN ({{marks}}) AND {_ORDER_DAY} < ? GROUP BY 1""",
            ids,
            before_day,
        )
    if (
        store == "ah"
        and {"ah_receipts", "ah_receipt_items", "ah_receipt_products", "product_links"}
        <= tables
    ):
        product: str = product_links.RECEIPT_LINE_PRODUCT_SQL
        receipts: dict[str, int] = await _count(
            conn,
            f"""
            SELECT CAST({product} AS TEXT), COUNT(DISTINCT l.receipt_id) FROM ah_receipt_items l
            JOIN ah_receipts r ON r.receipt_id = l.receipt_id
            {product_links.RECEIPT_LINE_JOIN_SQL}
            WHERE CAST({product} AS TEXT) IN ({{marks}}) AND substr(r.date_time, 1, 10) < ? GROUP BY 1""",
            ids,
            before_day,
        )
        for product_id, count in receipts.items():
            counts[product_id] = counts.get(product_id, 0) + count
    return counts


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _product_id(kind: str, item: Mapping[str, Any]) -> Any:
    """Het product van de webwinkel: bij een bonregel uit de koppeling (`product`), anders `productId`."""
    if kind == "receipt":
        product: Any = item.get("product")
        return product.get("id") if isinstance(product, Mapping) else None
    return item.get("productId")


def _title(kind: str, item: Mapping[str, Any]) -> str:
    if kind == "receipt":
        product: Any = item.get("product")
        linked: Any = product.get("title") if isinstance(product, Mapping) else None
        return str(linked or item.get("name") or "")
    return str(item.get("title") or "")


def _receipt_price(item: Mapping[str, Any], quantity: float) -> float | None:
    """De prijs per stuk op een bon: `price`, en zonder prijs (AH zet bij één stuk alleen het bedrag op de bon) het bedrag gedeeld
    door het aantal."""
    price: float | None = _number(item.get("price"))
    amount: float | None = _number(item.get("amount"))
    if price is None and amount is not None and quantity > 0:
        return amount / quantity
    return price


def _paid_each(kind: str, item: Mapping[str, Any], quantity: float) -> float | None:
    """Wat je per stuk betaalde. Bestelling: `price`, ingelezen: `paid`, bon: de prijs (`_receipt_price`) min de bonus per stuk,
    nooit onder nul."""
    if kind == "imported":
        return _number(item.get("paid"))
    if kind != "receipt":
        return _number(item.get("price"))
    price: float | None = _receipt_price(item, quantity)
    if price is None or quantity <= 0:
        return price
    return max(price - (_number(item.get("bonus")) or 0.0) / quantity, 0.0)


def _store_regular(kind: str, item: Mapping[str, Any]) -> float | None:
    """De prijs ervoor die de winkel zelf noemt: bij een bestelling `priceBeforeBonus`, bij een ingelezen bestelling de prijs uit de
    mail (`price`) en op een bon de prijs op de regel als er een bonus bij hoort (de bon noemt de prijs vóór de bonus,
    `_receipt_price`)."""
    if kind == "order":
        return _number(item.get("priceBeforeBonus"))
    if kind == "imported":
        return _number(item.get("price"))
    quantity: float = _number(item.get("quantity")) or 0.0
    return _receipt_price(item, quantity) if (_number(item.get("bonus")) or 0.0) > 0 else None


def _enrich_line(store: str, kind: str, item: dict[str, Any], counts: dict[str, int]) -> tuple[float, float]:
    """Zet de velden van `enrich` op één regel. Geeft wat de regel kostte en wat hij bespaarde (allebei 0 zonder aantal of prijs)."""
    quantity: float = _number(item.get("quantity")) or 0.0
    paid: float | None = _paid_each(kind, item, quantity)
    history: float | None = _number(item.get("regularPrice"))
    regular: float | None = history if history is not None else _store_regular(kind, item)
    product_id: Any = _product_id(kind, item)
    item["category"] = purchase_category(store, item.get("taxonomy"), _title(kind, item))
    item["regularPrice"] = regular
    item["discountSource"] = None if regular is None else ("history" if history is not None else "store")
    item["discount"] = discount_percent(regular, paid) if quantity > 0 else None
    item["timesBefore"] = counts.get(str(product_id), 0) if product_id not in (None, "", 0) else 0
    item["paidEach"] = round(paid, 2) if paid is not None else None
    if quantity <= 0 or paid is None:
        return 0.0, 0.0
    saved: float = (regular - paid) * quantity if regular is not None and regular > paid else 0.0
    return paid * quantity, saved


async def enrich(
    conn: aiosqlite.Connection | None, store: str, kind: str, day: str, items: list[dict[str, Any]], *, unmatched_bonus: float = 0.0
) -> dict[str, Any]:
    """Zet op elke regel `category`, `regularPrice`, `discount`, `discountSource`, `timesBefore` en `paidEach`. Past de dicts aan.

    `kind` is `order`, `receipt` of `imported`. `regularPrice` op de regel is bij binnenkomst de kolom `regular_price` (de gewone
    prijs van 30 dagen, `discountSource` `history`). Zonder die prijs wordt het de prijs ervoor van de winkel (`_store_regular`,
    `discountSource` `store`), anders None. Een regel zonder aantal krijgt geen korting. Een bonregel heeft `bonus` (het bedrag van
    `match_receipt_bonus`). Geeft `saved` (de som van `(regularPrice - paidEach) * quantity` waar dat positief is) en `savedPercent`
    (`saved` als deel van wat de regels zonder korting hadden gekost) voor de aankoop.

    `unmatched_bonus` is het bedrag van de bonusregels van een bon die bij geen regel passen (`unmatched_bonus()`). Het telt mee in
    `saved` en gaat af van wat je betaalde, dus niet van een regel.
    """
    if kind not in KINDS:
        raise ValueError(f"onbekende soort aankoop: {kind}")
    counts: dict[str, int] = await times_before(conn, store, [_product_id(kind, item) for item in items], day)
    paid_total: float = 0.0
    saved: float = max(unmatched_bonus, 0.0)
    for item in items:
        paid, line_saved = _enrich_line(store, kind, item, counts)
        paid_total += paid
        saved += line_saved
    paid_total = max(paid_total - max(unmatched_bonus, 0.0), 0.0)
    saved = round(saved, 2)
    return {"saved": saved, "savedPercent": discount_percent(paid_total + saved, paid_total) if saved > 0 else None}


def status_kind(kind: str, row: Mapping[str, Any] | Any) -> tuple[str, str | None]:
    """De soort status van een aankoop met de tekst van de winkel: `delivered`, `planned` (met de `state_text` van AH, Jumbo heeft die
    niet), `imported` of `receipt`. De detailroutes zetten hem in `statusKind` en `statusText`, naast een bestaand veld `status`."""
    if kind == "imported":
        return "imported", None
    if kind == "receipt":
        return "receipt", None
    if row["kind"] == "delivered":
        return "delivered", None
    keys: Iterable[str] = row.keys()
    return "planned", (row["state_text"] or None) if "state_text" in keys else None


async def fill_regular(
    store: str,
    *,
    order_ids: list[Any] | None = None,
    receipt_ids: list[Any] | None = None,
) -> None:
    """Vul de gewone prijs van 30 dagen bij de regels van de aankoop die je opent (`prices.fill_regular_prices`). Een fout komt in
    het log en laat de aankoop gewoon zien: de korting valt dan terug op de prijs ervoor van de winkel."""
    # prices leest ah_orders en price_import, die deze module gebruiken: pas hier ophalen houdt de import rond.
    from . import prices

    try:
        await prices.fill_regular_prices(store, order_ids=order_ids, receipt_ids=receipt_ids)
    except Exception:  # noqa: BLE001 - de gewone prijs is een extraatje, de aankoop is wat je wilde zien
        log.warning(
            "Gewone prijs bij het openen van een aankoop mislukt voor %s",
            store,
            exc_info=True,
        )
