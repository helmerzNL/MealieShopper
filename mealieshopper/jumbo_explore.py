"""Verkenner voor de onofficiële Jumbo-routes.

Jumbo kan zijn API zonder bericht veranderen en weert soms server-adressen. Deze verkenner begint daarom
met een bereikbaarheidstest (zonder inloggen) en roept daarna met jouw Jumbo-koppeling de leesroutes aan.
Er wordt niets gewijzigd en niets bewaard: ook het mandje vullen wordt alleen met een lege lijst
geprobeerd, om te zien of die route en het invoertype bestaan.

Zoals bij `ah_explore` bevat het rapport bewust geen persoonlijke gegevens: namen, prijzen, producten en
id's worden vervangen door hun type. Zo kun je het rapport veilig delen.
"""
from __future__ import annotations

from typing import Any

import requests

from . import jumbo
from .ah_explore import _probe, shape

EXAMPLE_TERM = "melk"


def _reach(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
    """Of Jumbo dit adres beantwoordt, en of het antwoord een bot-blokkade lijkt (HTML in plaats van JSON)."""
    response = requests.request(
        method, url, timeout=20, headers={"User-Agent": jumbo.WEB_USER_AGENT, "Accept": "application/json"}, **kwargs
    )
    kind = (response.headers.get("content-type") or "").split(";")[0].strip()
    return {
        "status": response.status_code,
        "contentType": kind,
        "looksLikeBotBlock": response.status_code in {403, 429, 503} or kind == "text/html",
    }


def _empty_add_probe() -> dict[str, Any]:
    """Het mandje-vullen-pad met een lege lijst: er wordt niets toegevoegd, we zien alleen of het pad klopt.

    `typename` is `Basket` bij succes of de naam van Jumbo's foutvariant (een typenaam, geen persoonsgegeven).
    """
    data = jumbo._graphql(
        jumbo.ADD_LINES_MUTATION, {"input": {"lines": [], "type": "ECOMMERCE"}}, "BasketPageAddBasketItems"
    )
    return {"typename": (data.get("addBasketLines") or {}).get("__typename")}


def _possible_result_types() -> list[str]:
    """Welke antwoorden kan `addBasketLines` geven? Introspectie staat bij Jumbo meestal uit; de fout zegt dat dan."""
    data = jumbo._graphql(
        'query AddBasketLinesResultTypes { __type(name: "AddBasketLinesResult") { possibleTypes { name } } }',
        None,
        "AddBasketLinesResultTypes",
    )
    return [str(entry.get("name")) for entry in ((data.get("__type") or {}).get("possibleTypes") or []) if isinstance(entry, dict)]


def explore() -> dict[str, Any]:
    """Controleer de bereikbaarheid en roep de leesroutes aan; geef een rapport met de vorm van de antwoorden."""
    probes: list[dict[str, Any]] = []

    for name, method, url, kwargs in (
        ("bereikbaar: auth.jumbo.com (OpenID-configuratie)", "GET", f"{jumbo.AUTH_BASE}/.well-known/openid-configuration", {}),
        ("bereikbaar: www.jumbo.com/api/graphql (zonder inloggen)", "POST", jumbo.GRAPHQL_URL,
         {"json": {"operationName": "Reach", "query": "query Reach { __typename }"}}),
    ):
        report, _ = _probe(name, lambda m=method, u=url, k=kwargs: _reach(m, u, **k), lambda data: data)
        probes.append(report)

    linked = bool(jumbo.configured_refresh_token())
    report, _ = _probe("toegangstoken vernieuwen (auth.jumbo.com/oauth/token)", lambda: jumbo.get_access_token(force=True), lambda _: "ok")
    probes.append(report)

    report, _ = _probe(
        "zoeken: GraphQL searchProducts",
        lambda: jumbo.search_products(EXAMPLE_TERM, 3),
        lambda products: {
            "producten gevonden": len(products),
            "velden gebruikt voor de query": jumbo.SEARCH_EXTRA_FIELDS[jumbo._search_variant] or "(alleen de kernvelden)",
            "eerste product": shape(products[0] if products else None),
        },
    )
    probes.append(report)

    report, _ = _probe("mandje: GraphQL activeBasket", jumbo.get_cart)
    probes.append(report)

    report, _ = _probe("mandje vullen: addBasketLines met een lege lijst (voegt niets toe)", _empty_add_probe, lambda data: data)
    probes.append(report)

    report, _ = _probe("typen van het antwoord op addBasketLines (introspectie)", _possible_result_types, lambda names: names or ["leeg"])
    probes.append(report)

    return {
        "note": "Alleen de vorm van de antwoorden: geen namen, prijzen of id's. Veilig te delen.",
        "linked": linked,
        "probes": probes,
        "ok": sum(1 for probe in probes if probe["ok"]),
    }
