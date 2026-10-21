"""Verkenner voor de onofficiële Picnic-routes.

Picnic kan zijn API zonder bericht veranderen. Deze verkenner roept de leesroutes aan met jouw
Picnic-koppeling en meldt per route of het werkt en **welke velden er in het antwoord zitten**.
Er wordt niets gewijzigd en niets bewaard.

Zoals bij `ah_explore` bevat het rapport bewust geen persoonlijke gegevens: namen, adressen, prijzen,
producten en id's worden vervangen door hun type. Zo kun je het rapport veilig delen.
"""
from __future__ import annotations

from typing import Any

from . import picnic
from .ah_explore import _probe, shape

SEARCH_TERM = "melk"


def explore() -> dict[str, Any]:
    """Roep de leesroutes aan en geef een rapport met de vorm van de antwoorden."""
    probes: list[dict[str, Any]] = []

    report, page = _probe(
        "zoeken: GET pages/search-page-results",
        lambda: picnic._authed("GET", f"/pages/search-page-results?search_term={SEARCH_TERM}"),
        lambda data: {"producten gevonden": len(picnic.products_from_search_page(data)),
                      "eerste product": shape(next(iter(picnic.products_from_search_page(data)), None))},
    )
    probes.append(report)
    if page is not None:
        # De ruwe boom is groot; de vorm van één tegel is wat we nodig hebben om het zoeken te herstellen.
        tile = next((n for n in picnic._walk(page.get("body")) if n.get("type") == "SELLING_UNIT_TILE"), None)
        probes.append({"name": "een zoektegel (ruwe vorm)", "ok": tile is not None, "shape": shape(tile)})

    for name, path, method, body in (
        ("winkelmandje: GET cart", "/cart", "GET", None),
        ("leveringen: POST deliveries/summary", "/deliveries/summary", "POST", []),
        ("lopende leveringen: POST deliveries/summary [CURRENT]", "/deliveries/summary", "POST", ["CURRENT"]),
    ):
        report, _ = _probe(name, lambda m=method, p=path, b=body: picnic._authed(m, p, body=b))
        probes.append(report)

    return {
        "note": "Alleen de vorm van de antwoorden: geen namen, adressen, prijzen of id's. Veilig te delen.",
        "linked": bool(picnic.configured_auth_key()),
        "probes": probes,
        "ok": sum(1 for probe in probes if probe["ok"]),
    }
