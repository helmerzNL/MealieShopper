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
        days: int = (
            date.fromisoformat(day_b[:10]) - date.fromisoformat(day_a[:10])
        ).days
        result.append((days, round(abs(b - a) / a, 4)))
    return result


def percentile(values: list[float], fraction: float) -> float | None:
    """Het percentiel volgens de nearest-rank-methode: de waarde op plaats `ceil(fraction * n)` van de gesorteerde lijst.
    Een lege lijst geeft None."""
    if not values:
        return None
    ordered: list[float] = sorted(values)
    return ordered[max(ceil(fraction * len(ordered)), 1) - 1]


def summary(pairs: list[tuple[int, float]]) -> list[dict[str, Any]]:
    """Per rij van `GAPS` het aantal paren en het 50e, 90e en 95e percentiel (nearest-rank) van het verschil. Een lege rij heeft
    aantal 0 en percentielen None."""
    buckets: dict[str, list[float]] = {name: [] for name, _ in GAPS}
    for days, diff in pairs:
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
