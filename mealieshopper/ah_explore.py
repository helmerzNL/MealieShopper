"""Verkenner voor de onofficiële AH-routes voor bestellingen, mandje en kassabonnen.

De routes zijn niet gedocumenteerd en kunnen veranderen, en hoe je afgeleverde bestellingen opvraagt
weten we nog niet. Deze verkenner roept de leesroutes met jouw AH-koppeling aan en meldt per route of
het werkt en **welke velden er in het antwoord zitten**. Er wordt niets gewijzigd en niets bewaard.

Het rapport bevat bewust geen persoonlijke gegevens: namen, adressen, prijzen, producten en id's
worden vervangen door hun type (`str`, `int`, ...). Alleen een korte lijst velden die een vaste
vocabulaire hebben (status, bezorgwijze, betaalmethode) toont de waarde, zodat je kunt zien welke
statussen AH kent. Zo kun je het rapport veilig delen.
"""
from __future__ import annotations

import inspect
import re
from datetime import date
from typing import Any, Callable

import httpx

from . import ah, deals

# Velden met een vaste, niet-persoonlijke vocabulaire: hier tonen we de waarde, bij de rest alleen het type.
VOCABULARY_KEYS = {
    "state", "orderState", "statusCode", "statusDescription", "shoppingType", "deliveryType", "method", "status",
    # Pagina's van Picnic: soorten onderdelen en analysegegevens, geen persoonlijke tekst.
    "type", "schema", "component", "actionType",
}
MAX_DEPTH = 7
# Statussen waarmee we vragen naar niet-open bestellingen. Een onbekende waarde geeft een fout van AH,
# en die fout noemt vaak de geldige waarden.
STATUS_CANDIDATES = ("CLOSED", "ALL")


def _union(shapes: list[Any]) -> Any:
    """Voeg de vormen van meerdere elementen samen: velden samenvoegen, verschillende types en waarden naast elkaar."""
    first = shapes[0]
    if all(item == first for item in shapes):
        return first
    if all(isinstance(item, dict) for item in shapes):
        return {key: _union([item[key] for item in shapes if key in item]) for key in dict.fromkeys(k for item in shapes for k in item)}
    if all(isinstance(item, list) for item in shapes):
        return [_union([entry for item in shapes for entry in item[:1]])] if any(item[:1] for item in shapes) else ["leeg"]
    texts = [item[5:] if isinstance(item, str) and item.startswith("str: ") else item for item in shapes if isinstance(item, str)]
    if len(texts) == len(shapes) and any(item.startswith("str: ") for item in shapes if isinstance(item, str)):
        return "str: " + "|".join(dict.fromkeys(texts))
    return "|".join(dict.fromkeys(str(item) if not isinstance(item, (dict, list)) else "…" for item in shapes))


def shape(value: Any, key: str = "", depth: int = 0) -> Any:
    """De vorm van een JSON-waarde: structuur en types, zonder gegevens. Een lijst toont de samenvoeging van alle elementen."""
    if depth >= MAX_DEPTH:
        return "…"
    if isinstance(value, dict):
        return {name: shape(inner, name, depth + 1) for name, inner in value.items()}
    if isinstance(value, list):
        return [_union([shape(entry, key, depth + 1) for entry in value]), f"×{len(value)}"] if value else ["leeg"]
    if value is None:
        return "null"
    if isinstance(value, str) and key in VOCABULARY_KEYS:
        return f"str: {value[:40]}"
    return type(value).__name__


# Zoektermen waarvan we verwachten dat er in de week producten in de aanbieding zijn.
PROMO_TERMS = ("kip", "kaas", "pasta", "bier", "ijs", "groente")
# Velden die met een aanbieding te maken kunnen hebben, en de velden waarvan we de tekst willen zien.
PROMO_FIELD_RE = re.compile(r"bonus|price|discount|promo|label|mechanic|valid|start|end|period|decorator|unit", re.I)
PROMO_TEXT_RE = re.compile(r"mechanism|mechanic|label|description|period|date|text|type|start|end|valid|iso", re.I)
MAX_PROMO_TEXTS = 40


def _promo_texts(node: Any, key: str, found: dict[str, set[str]]) -> None:
    if isinstance(node, dict):
        for name, value in node.items():
            _promo_texts(value, name, found)
    elif isinstance(node, list):
        for value in node:
            _promo_texts(value, key, found)
    elif isinstance(node, str) and node and PROMO_TEXT_RE.search(key):
        found.setdefault(key, set()).add(node[:60])


def promo_report(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Hoe de aanbiedingen van een winkel eruitzien: de velden eromheen en de teksten die erin staan.

    Alleen velden die met prijs of aanbieding te maken hebben worden bekeken. Van getallen (prijzen) zien we
    het type, niet de waarde; van tekstvelden zoals de actietekst ("2e halve prijs") en datums de waarde.
    Producten en id's komen er niet in voor.
    """
    subsets = [{k: v for k, v in item.items() if PROMO_FIELD_RE.search(k)} for item in items if isinstance(item, dict)]
    if not subsets:
        return {"met aanbieding": 0}
    found: dict[str, set[str]] = {}
    for subset in subsets:
        _promo_texts(subset, "", found)
    texts = {key: sorted(values)[:MAX_PROMO_TEXTS] for key, values in sorted(found.items())}
    return {
        "met aanbieding": len(subsets),
        "velden": _union([shape(subset) for subset in subsets]),
        "teksten": texts,
    }


# Een netwerkfout van een winkelclient: alle winkelclients praten via httpx.
NETWORK_ERRORS: tuple[type[Exception], ...] = (httpx.HTTPError,)


async def resolved(value: Any) -> Any:
    """`value`, of de uitkomst ervan als het een awaitable is.

    De winkelclients zijn async, maar een probe mag ook een gewone functie zijn die meteen een waarde geeft (een samenvatting van
    iets dat al is opgehaald). Zo werkt een probe met beide.
    """
    return await value if inspect.isawaitable(value) else value


async def promo_probe(terms: tuple[str, ...], fetch: Callable[[str], Any], is_bonus: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
    """Zoek de termen en geef `promo_report` van de producten met een aanbieding. `fetch` geeft een lijst producten, of een awaitable daarvan."""
    total: int = 0
    bonus: list[dict[str, Any]] = []
    for term in terms:
        items: list[dict[str, Any]] = await resolved(fetch(term))
        total += len(items)
        bonus.extend(item for item in items if is_bonus(item))
    return {"gezocht": list(terms), "producten": total, **promo_report(bonus)}


_DIGITS = re.compile(r"\d")


def nutrition_report(data: Any) -> dict[str, Any]:
    """De vorm van een voedingstabel van AH: het aantal tabellen, per regel `type`, `name` en de waarde met elk cijfer als 9
    ("9999 kJ (999 kcal)"), en of `ah.product_nutrition` er energie uit haalt."""
    product = data.get("product") if isinstance(data, dict) else None
    trade = product.get("tradeItem") if isinstance(product, dict) else None
    tables = trade.get("nutritions") if isinstance(trade, dict) else None
    tables = [table for table in tables if isinstance(table, dict)] if isinstance(tables, list) else []
    rows = [row for row in (tables[0].get("nutrients") or []) if isinstance(row, dict)] if tables else []
    read = ah.product_nutrition(rows)
    return {
        "tabellen": len(tables),
        "regels": [{"type": row.get("type"), "name": row.get("name"), "value": _DIGITS.sub("9", str(row.get("value") or ""))} for row in rows],
        "gelezen": sorted(read) if read else [],
    }


async def _probe(name: str, call: Callable[[], Any], summarize: Callable[[Any], Any] | None = None) -> tuple[dict[str, Any], Any]:
    """Roep `call` aan (synchroon of async, zie `resolved`) en geef het rapport van die route en de uitkomst (None bij een fout)."""
    try:
        result = await resolved(call())
    except NETWORK_ERRORS as exc:
        return {"name": name, "ok": False, "error": f"netwerkfout: {type(exc).__name__}"}, None
    except Exception as exc:  # noqa: BLE001 - een route die faalt is precies wat we willen weten
        return {"name": name, "ok": False, "error": str(exc)[:300]}, None
    return {"name": name, "ok": True, **({"info": summarize(result)} if summarize else {"shape": shape(result)})}, result


async def _raw_get(path: str, params: dict[str, Any] | None = None) -> Any:
    return await ah._get_json(path, params)


async def _raw_graphql(query: str, variables: dict[str, Any] | None = None) -> Any:
    return await ah._graphql(await ah.get_user_token(), query, variables or {}, "AH")


async def _anonymous_graphql(query: str, variables: dict[str, Any] | None = None) -> Any:
    """Zoals `_raw_graphql`, met het anonieme token: voor wat ook zonder AH-koppeling werkt, zoals de bonus."""
    return await ah._graphql(await ah.get_token(), query, variables or {}, "AH")


async def explore() -> dict[str, Any]:
    """Roep alle leesroutes aan en geef een rapport met de vorm van de antwoorden."""
    probes: list[dict[str, Any]] = []

    report, _ = await _probe("mandje (boodschappenlijst): GET shoppinglist/v2/items", lambda: _raw_get("/mobile-services/shoppinglist/v2/items"))
    probes.append(report)

    async def search(term: str) -> list[dict[str, Any]]:
        found: Any = await _raw_get("/mobile-services/product/search/v2", {"query": term, "page": 0, "size": 25, "sortOn": "RELEVANCE"})
        return (found or {}).get("products") or []

    # De voedingswaarden van een product (4.9.0, de weekplanner van de AH-app): hoe de tabel eruitziet, zodat te zien is of het
    # lezen in ah.product_nutrition klopt. De cijfers worden 9, de rest blijft (soorten en eenheden zijn geen persoonlijke gegevens).
    async def nutrition_of_a_product() -> Any:
        products = await search("melk")
        product_id = next((item.get("webshopId") for item in products if item.get("webshopId")), None)
        if not product_id:
            raise RuntimeError("geen product gevonden om te proberen")
        return await _anonymous_graphql(ah.PRODUCT_NUTRITION_QUERY, {"id": int(product_id)})

    report, _ = await _probe(
        "voedingswaarden van een product: GraphQL product.tradeItem.nutritions",
        nutrition_of_a_product,
        nutrition_report,
    )
    probes.append(report)

    report, _ = await _probe(
        "aanbiedingen: GET product/search/v2 (de velden van producten in de bonus)",
        lambda: promo_probe(PROMO_TERMS, search, lambda item: bool(item.get("isBonus"))),
        lambda data: data,
    )
    probes.append(report)

    report, active = await _probe("bestelmandje: GET order/v1/summaries/active", lambda: _raw_get("/mobile-services/order/v1/summaries/active", {"sortBy": "DEFAULT"}))
    probes.append(report)

    report, fulfillments = await _probe("open bestellingen: GraphQL orderFulfillments(status: OPEN)", lambda: _raw_graphql(ah.FULFILLMENTS_QUERY))
    probes.append(report)

    closed_data: Any = None
    # Welke statussen kent AH nog meer? Een onbekende waarde geeft een foutmelding, en die kan de geldige waarden noemen.
    for status in STATUS_CANDIDATES:
        query = ah.FULFILLMENTS_QUERY.replace("status: OPEN", f"status: {status}")
        report, data = await _probe(f"bestellingen met status {status}", lambda q=query: _raw_graphql(q))
        probes.append(report)
        if status == "CLOSED":
            closed_data = data

    def results_of(data: Any) -> list[dict[str, Any]]:
        return [entry for entry in ((((data or {}).get("orderFulfillments") or {}).get("result")) or []) if isinstance(entry, dict)]

    closed = results_of(closed_data)
    delivered = next((entry for entry in closed if (entry.get("delivery") or {}).get("status") != "CANCELLED"), None)
    open_id = (results_of(fulfillments) or [{}])[0].get("orderId") or (active or {}).get("id")
    for label, order_id in (("afgeleverde bestelling", (delivered or {}).get("orderId")), ("open bestelling", open_id)):
        if order_id:
            report, _ = await _probe(f"inhoud van een {label}: GET order/v1/{{id}}/details-grouped-by-taxonomy",
                               lambda oid=order_id: _raw_get(f"/mobile-services/order/v1/{int(oid)}/details-grouped-by-taxonomy"))
        else:
            report = {"name": f"inhoud van een {label}", "ok": False, "error": "overgeslagen: er is geen bestelling om te proberen"}
        probes.append(report)

    report, receipts = await _probe("kassabonnen: GraphQL posReceiptsPage", lambda: _raw_graphql(ah.RECEIPTS_QUERY, {"offset": 0, "limit": 3}))
    probes.append(report)
    page = (((receipts or {}).get("posReceiptsPage") or {}).get("posReceipts")) or []
    if page and isinstance(page[0], dict) and page[0].get("id"):
        report, _ = await _probe("een kassabon: GraphQL posReceiptDetails", lambda: _raw_graphql(ah.RECEIPT_QUERY, {"id": str(page[0]["id"])}))
        probes.append(report)
    else:
        probes.append({"name": "een kassabon: GraphQL posReceiptDetails", "ok": False, "error": "overgeslagen: er zijn geen kassabonnen om te proberen"})

    # Aanbiedingen (Bonus): werkt met het anonieme token, dus ook zonder AH-koppeling. Eerst de bonus per bonusperiode (GraphQL), die
    # MealieShopper gebruikt; per periode hoeveel acties en producten er zijn, zodat te zien is of de bonus van volgende week er al staat.
    def bonus_periods(data: Any) -> dict[str, Any]:
        promotions = [p for p in (data or {}).get("bonusPromotions") or [] if isinstance(p, dict) and p.get("promotionType") in ah.BONUS_PROMOTION_TYPES]
        return {
            "periodes": [
                {
                    "week": period.get("weekNumber"), "van": period.get("startDate"), "tot": period.get("endDate"),
                    "acties": sum(1 for p in promotions if p.get("periodStart") == period.get("startDate")),
                    "producten": sum(len(p.get("products") or []) for p in promotions if p.get("periodStart") == period.get("startDate")),
                }
                for period in (data or {}).get("bonusPeriods") or [] if isinstance(period, dict)
            ],
            "soorten": sorted({str(p.get("promotionType")) for p in (data or {}).get("bonusPromotions") or [] if isinstance(p, dict)}),
        }

    report, _ = await _probe("aanbiedingen: GraphQL bonusPeriods en bonusPromotions",
                       lambda: _anonymous_graphql(ah.BONUS_PROMOTIONS_QUERY), bonus_periods)
    probes.append(report)

    async def bonus_page() -> Any:
        return await ah._request_json(
            "GET",
            f"{ah.AH_API_BASE}{ah.BONUS_SECTION_PATH}",
            headers=ah._auth_headers(await ah.get_token(), include_content_type=False),
            params={"date": date.today().isoformat(), "application": "AH", "promotionType": "NATIONAL"},
        )

    report, bonus = await _probe("aanbiedingen: GET bonuspage/v2/section", bonus_page)
    probes.append(report)
    if report["ok"]:
        report["productCount"] = len(ah.bonus_products_in(bonus))

    # De Bonus Box (persoonlijke bonus): vraagt de AH-koppeling. Per route alleen aantallen, soorten actie en activatiestatussen, geen producten.
    monday, sunday = deals.week_of(date.today().isoformat())
    start, end = monday.isoformat(), sunday.isoformat()

    def bonus_box(promotions: list[Any], products: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "acties": len(promotions),
            "producten": len(products),
            "soorten": sorted({str(p.get("promotionType")) for p in promotions if isinstance(p, dict)}),
            "activatie": sorted({str(p.get("activationStatus")) for p in promotions if isinstance(p, dict)}),
            "geactiveerd": sum(1 for product in products if product.get("activated")),
        }

    report, _ = await _probe(
        "Bonus Box: GraphQL bonusPromotions(filterSet: APP_BONUS_BOX)",
        lambda: _raw_graphql(ah.BONUS_BOX_QUERY, {"periodStart": start, "periodEnd": end, "weekNumber": monday.isocalendar()[1]}),
        lambda data: bonus_box(list((data or {}).get("bonusPromotions") or []), ah.bonus_box_promotions((data or {}).get("bonusPromotions"), start, end)),
    )
    probes.append(report)

    def bonus_box_rest(data: Any) -> dict[str, Any]:
        sections = data if isinstance(data, list) else [data]
        entries = [entry for section in sections if isinstance(section, dict) for entry in section.get("bonusGroupOrProducts") or [] if isinstance(entry, dict)]
        nodes = [entry.get("product") or entry.get("bonusGroup") for entry in entries]
        return bonus_box([node for node in nodes if isinstance(node, dict)], ah.bonus_box_in(data, start, end))

    report, _ = await _probe(f"Bonus Box: GET {ah.BONUS_BOX_PATH}", lambda: _raw_get(ah.BONUS_BOX_PATH, {"bonusStartDate": start}), bonus_box_rest)
    probes.append(report)

    return {
        "note": "Alleen de vorm van de antwoorden: geen namen, adressen, prijzen of id's. Veilig te delen.",
        "linked": bool(await ah.configured_refresh_token()),
        "probes": probes,
        "ok": sum(1 for probe in probes if probe["ok"]),
    }
