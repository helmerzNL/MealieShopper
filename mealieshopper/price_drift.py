"""Rekenfuncties voor de meting van het prijsverloop tussen aanbiedingen (`scripts/folder_price_margin.py`).

Alleen zuivere functies: geen database, geen bestanden. De meting bepaalt later welke prijsmarge nodig is om een folderregel aan
een product te koppelen."""

from __future__ import annotations

from datetime import date
from itertools import pairwise
from math import ceil
from typing import Any

# (naam, grens in dagen): een paar valt in de eerste rij waarvan de grens groot genoeg is.
GAPS: tuple[tuple[str, int], ...] = (
    ("tot 3 maanden", 92),
    ("tot 1 jaar", 365),
    ("tot 2 jaar", 730),
    ("tot 5 jaar", 1826),
    ("langer", 10**6),
)


def pairs(points: list[tuple[str, float]]) -> list[tuple[int, float]]:
    """Per paar opeenvolgende punten (dag als `YYYY-MM-DD`, prijs, op volgorde van dag) het aantal dagen ertussen en het relatieve
    verschil `abs(b - a) / a`, op vier decimalen. Een paar waarvan de eerste of tweede prijs 0 of lager is, telt niet."""
    result: list[tuple[int, float]] = []
    for (day_a, a), (day_b, b) in pairwise(points):
        if a <= 0 or b <= 0:
            continue
        days: int = (date.fromisoformat(day_b[:10]) - date.fromisoformat(day_a[:10])).days
        result.append((days, round(abs(b - a) / a, 4)))
    return result


def percentile(values: list[float], fraction: float) -> float | None:
    """Het percentiel volgens de nearest-rank-methode: de waarde op plaats `ceil(fraction * n)` van de gesorteerde lijst.
    Een lege lijst geeft None."""
    if not values:
        return None
    ordered: list[float] = sorted(values)
    return ordered[max(ceil(fraction * len(ordered)), 1) - 1]


def summary(found: list[tuple[int, float]]) -> list[dict[str, Any]]:
    """Per rij van `GAPS` het aantal paren en het 50e, 90e en 95e percentiel (nearest-rank) van het verschil. Een lege rij heeft
    aantal 0 en percentielen None."""
    buckets: dict[str, list[float]] = {name: [] for name, _ in GAPS}
    for days, diff in found:
        name: str = next(name for name, limit in GAPS if days <= limit)
        buckets[name].append(diff)
    return [
        {
            "gap": name,
            "count": len(buckets[name]),
            "p50": percentile(buckets[name], 0.5),
            "p90": percentile(buckets[name], 0.9),
            "p95": percentile(buckets[name], 0.95),
        }
        for name, _ in GAPS
    ]


def valid_day(value: Any) -> str | None:
    """De dag (`YYYY-MM-DD`) uit een tekst, of None als er geen geldige dag in staat (ook bij een lege tekst)."""
    text: str = str(value or "")[:10]
    try:
        date.fromisoformat(text)
    except ValueError:
        return None
    return text


def chain_offers(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """De rijen van een product (op volgorde van `first_seen` en id, met de sleutels `day`, `price_before`, `continued`) als
    aanbiedingen. Een rij met `continued` hoort bij de rij ervoor (`prices._offer_chains`): een aanbieding is een keten.
    Per keten: `day` van de eerste rij, `price_before` de nieuwste die niet leeg is (None als geen rij er een heeft) en `rows`.
    Een keten waarvan de eerste rij geen geldige dag heeft, valt weg."""
    chains: list[list[dict[str, Any]]] = []
    for item in rows:
        if chains and item["continued"]:
            chains[-1].append(item)
        else:
            chains.append([item])
    offers: list[dict[str, Any]] = []
    for chain in chains:
        day: str | None = valid_day(chain[0]["day"])
        claimed: list[float] = [r["price_before"] for r in chain if r["price_before"] is not None]
        if day is not None:
            offers.append({"day": day, "price_before": claimed[-1] if claimed else None, "rows": chain})
    return offers


def daily_points(offers: list[dict[str, Any]]) -> list[tuple[str, float]]:
    """Eén punt (dag, van-prijs) per dag uit de aanbiedingen van een product, op volgorde van dag. Bij twee aanbiedingen op een
    dag telt de laatste. Een aanbieding zonder van-prijs geeft geen punt."""
    by_day: dict[str, float] = {}
    for offer in offers:
        if offer["price_before"] is not None:
            by_day[offer["day"]] = float(offer["price_before"])
    return sorted(by_day.items())
