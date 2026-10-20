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

from typing import Any, Callable

import requests

from . import ah

# Velden met een vaste, niet-persoonlijke vocabulaire: hier tonen we de waarde, bij de rest alleen het type.
VOCABULARY_KEYS = {
    "state", "orderState", "statusCode", "statusDescription", "shoppingType", "deliveryType", "method", "status",
}
MAX_DEPTH = 7
# Statussen waarmee we vragen naar niet-open bestellingen. Een onbekende waarde geeft een fout van AH,
# en die fout noemt vaak de geldige waarden.
STATUS_CANDIDATES = ("CLOSED", "DELIVERED", "COMPLETED", "ALL")


def shape(value: Any, key: str = "", depth: int = 0) -> Any:
    """De vorm van een JSON-waarde: structuur en types, zonder gegevens."""
    if depth >= MAX_DEPTH:
        return "…"
    if isinstance(value, dict):
        return {name: shape(inner, name, depth + 1) for name, inner in value.items()}
    if isinstance(value, list):
        return [shape(value[0], key, depth + 1), f"×{len(value)}"] if value else ["leeg"]
    if value is None:
        return "null"
    if isinstance(value, str) and key in VOCABULARY_KEYS:
        return f"str: {value[:40]}"
    return type(value).__name__


def _probe(name: str, call: Callable[[], Any]) -> tuple[dict[str, Any], Any]:
    try:
        result = call()
    except requests.RequestException as exc:
        return {"name": name, "ok": False, "error": f"netwerkfout: {type(exc).__name__}"}, None
    except Exception as exc:  # noqa: BLE001 - een route die faalt is precies wat we willen weten
        return {"name": name, "ok": False, "error": str(exc)[:300]}, None
    return {"name": name, "ok": True, "shape": shape(result)}, result


def _raw_get(path: str, params: dict[str, Any] | None = None) -> Any:
    return ah._get_json(path, params)


def _raw_graphql(query: str, variables: dict[str, Any] | None = None) -> Any:
    return ah._graphql(ah.get_user_token(), query, variables or {}, "AH")


def explore() -> dict[str, Any]:
    """Roep alle leesroutes aan en geef een rapport met de vorm van de antwoorden."""
    probes: list[dict[str, Any]] = []

    report, active = _probe("bestelmandje: GET order/v1/summaries/active", lambda: _raw_get("/mobile-services/order/v1/summaries/active", {"sortBy": "DEFAULT"}))
    probes.append(report)

    report, fulfillments = _probe("open bestellingen: GraphQL orderFulfillments(status: OPEN)", lambda: _raw_graphql(ah.FULFILLMENTS_QUERY))
    probes.append(report)

    # Welke statussen kent AH nog meer? Een onbekende waarde geeft een foutmelding, en die kan de geldige waarden noemen.
    for status in STATUS_CANDIDATES:
        query = ah.FULFILLMENTS_QUERY.replace("status: OPEN", f"status: {status}")
        report, _ = _probe(f"bestellingen met status {status}", lambda q=query: _raw_graphql(q))
        probes.append(report)

    order_id = None
    results = (((fulfillments or {}).get("orderFulfillments") or {}).get("result")) or []
    if results and isinstance(results[0], dict):
        order_id = results[0].get("orderId")
    order_id = order_id or (active or {}).get("id")
    if order_id:
        report, _ = _probe("inhoud van één bestelling: GET order/v1/{id}/details-grouped-by-taxonomy",
                           lambda: _raw_get(f"/mobile-services/order/v1/{int(order_id)}/details-grouped-by-taxonomy"))
        probes.append(report)
    else:
        probes.append({"name": "inhoud van één bestelling", "ok": False, "error": "overgeslagen: er is geen open of actieve bestelling om te proberen"})

    report, receipts = _probe("kassabonnen: GraphQL posReceiptsPage", lambda: _raw_graphql(ah.RECEIPTS_QUERY, {"offset": 0, "limit": 3}))
    probes.append(report)
    page = (((receipts or {}).get("posReceiptsPage") or {}).get("posReceipts")) or []
    if page and isinstance(page[0], dict) and page[0].get("id"):
        report, _ = _probe("een kassabon: GraphQL posReceiptDetails", lambda: _raw_graphql(ah.RECEIPT_QUERY, {"id": str(page[0]["id"])}))
        probes.append(report)
    else:
        probes.append({"name": "een kassabon: GraphQL posReceiptDetails", "ok": False, "error": "overgeslagen: er zijn geen kassabonnen om te proberen"})

    return {
        "note": "Alleen de vorm van de antwoorden: geen namen, adressen, prijzen of id's. Veilig te delen.",
        "linked": bool(ah.configured_refresh_token()),
        "probes": probes,
        "ok": sum(1 for probe in probes if probe["ok"]),
    }
