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
STATUS_CANDIDATES = ("CLOSED", "ALL")
# Mogelijke manieren om meer dan de eerste 10 afgeronde bestellingen te vragen; AH meldt zelf welke argumenten bestaan.
PAGING_CANDIDATES = ("limit: 5", "first: 5", "pagination: {offset: 0, limit: 5}", "page: 1", "offset: 5")


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


def _probe(
    name: str,
    call: Callable[[], Any],
    summarize: Callable[[Any], Any] | None = None,
    *,
    include_error_message: bool = True,
) -> tuple[dict[str, Any], Any]:
    try:
        result = call()
    except requests.RequestException as exc:
        return {"name": name, "ok": False, "error": f"netwerkfout: {type(exc).__name__}"}, None
    except Exception as exc:  # noqa: BLE001 - een route die faalt is precies wat we willen weten
        error = str(exc)[:300] if include_error_message else type(exc).__name__
        return {"name": name, "ok": False, "error": error}, None
    return {"name": name, "ok": True, **({"info": summarize(result)} if summarize else {"shape": shape(result)})}, result


def _type_name(spec: Any) -> str:
    """Een GraphQL-type als tekst, bijvoorbeeld `Int!` of `[String]`."""
    if not isinstance(spec, dict):
        return "?"
    if spec.get("kind") == "NON_NULL":
        return f"{_type_name(spec.get('ofType'))}!"
    if spec.get("kind") == "LIST":
        return f"[{_type_name(spec.get('ofType'))}]"
    return str(spec.get("name") or "?")


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

    closed_data: Any = None
    # Welke statussen kent AH nog meer? Een onbekende waarde geeft een foutmelding, en die kan de geldige waarden noemen.
    for status in STATUS_CANDIDATES:
        query = ah.FULFILLMENTS_QUERY.replace("status: OPEN", f"status: {status}")
        report, data = _probe(f"bestellingen met status {status}", lambda q=query: _raw_graphql(q))
        probes.append(report)
        if status == "CLOSED":
            closed_data = data

    def results_of(data: Any) -> list[dict[str, Any]]:
        return [entry for entry in ((((data or {}).get("orderFulfillments") or {}).get("result")) or []) if isinstance(entry, dict)]

    # Hoe pagineer je door de afgeronde bestellingen? De namen van argumenten en statussen zijn geen persoonlijke gegevens.
    def introspect(data: Any) -> Any:
        fields = {field["name"]: field for field in ((data.get("__type") or {}).get("fields") or [])}
        field = fields.get("orderFulfillments") or {}
        return {"orderFulfillments-argumenten": [f'{arg["name"]}: {_type_name(arg.get("type"))}' for arg in field.get("args") or []]}

    report, _ = _probe("argumenten van orderFulfillments (introspectie)", lambda: _raw_graphql("query { __type(name: \"Query\") { fields { name args { name type { kind name ofType { kind name ofType { kind name } } } } } } }"), introspect)
    probes.append(report)
    report, _ = _probe("statussen van FulfillmentStatus (introspectie)", lambda: _raw_graphql("query { __type(name: \"FulfillmentStatus\") { enumValues { name } } }"),
                       lambda data: [value["name"] for value in (data.get("__type") or {}).get("enumValues") or []])
    probes.append(report)
    for extra in PAGING_CANDIDATES:
        query = ah.FULFILLMENTS_QUERY.replace("status: OPEN", f"status: CLOSED, {extra}")
        report, _ = _probe(f"afgeronde bestellingen met {extra}", lambda q=query: _raw_graphql(q), lambda data: {"aantal": len(results_of(data))})
        probes.append(report)

    closed = results_of(closed_data)
    delivered = next((entry for entry in closed if (entry.get("delivery") or {}).get("status") != "CANCELLED"), None)
    open_id = (results_of(fulfillments) or [{}])[0].get("orderId") or (active or {}).get("id")
    for label, order_id in (("afgeleverde bestelling", (delivered or {}).get("orderId")), ("open bestelling", open_id)):
        if order_id:
            report, _ = _probe(f"inhoud van een {label}: GET order/v1/{{id}}/details-grouped-by-taxonomy",
                               lambda oid=order_id: _raw_get(f"/mobile-services/order/v1/{int(oid)}/details-grouped-by-taxonomy"))
        else:
            report = {"name": f"inhoud van een {label}", "ok": False, "error": "overgeslagen: er is geen bestelling om te proberen"}
        probes.append(report)

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
