"""De lijnacties van het oordeel als `prices.LineSpan` (begin, eind, prijs per stuk) in plaats van dicts.

Het oordeel bij de aanbiedingen leest van een lijnactie alleen het begin, het eind en de prijs. Als dicts hield het er bij AH ruim
20.000 tegelijk vast, en een volle ronde van de GC moest die allemaal nalopen (20-11-2026). Deze tests zeggen dat de tuples
hetzelfde antwoord geven als de dicts waar ze uit komen.
"""

from __future__ import annotations

import random
from typing import Any

from mealieshopper import prices


def _history(seed: int) -> tuple[dict[str, list[str]], dict[str, dict[str, Any]]]:
    """Een willekeurige geschiedenis van folderproducten en per product de folders van zijn lijn."""
    chance: random.Random = random.Random(seed)
    folders: list[str] = [f"folder:{n}" for n in range(8)]
    history: dict[str, dict[str, Any]] = {}
    for folder in folders:
        episodes: list[dict[str, Any]] = []
        for _ in range(chance.randint(0, 12)):
            day: int = chance.randint(1, 28)
            month: int = chance.randint(1, 12)
            start: str = f"2026-{month:02d}-{day:02d}"
            price: float = round(chance.uniform(0.5, 6), 2)
            episodes.append(
                {
                    "start": start,
                    "end": None if chance.random() < 0.2 else f"2026-{month:02d}-{min(day + 6, 28):02d}",
                    "validFrom": start,
                    "validUntil": None,
                    "price": None if chance.random() < 0.15 else price,
                    "priceBefore": None if chance.random() < 0.3 else round(price * chance.uniform(1.0, 1.6), 2),
                    "promoLabel": chance.choice(["", "2e halve prijs", "1+1 gratis", "2 voor 3.00", "25% korting"]),
                }
            )
        history[folder] = {"title": f"Folder {folder}", "episodes": episodes}
    wanted: dict[str, list[str]] = {f"p{n}": chance.sample(folders, chance.randint(1, 4)) for n in range(6)}
    return wanted, history


def test_a_span_says_what_the_dict_says() -> None:
    for seed in range(200):
        wanted, history = _history(seed)
        weeks: dict[str, list[dict[str, Any]]] = prices._line_weeks(wanted, history)
        spans: dict[str, list[prices.LineSpan]] = prices._line_spans(wanted, history)
        assert set(spans) == set(weeks)
        for product_id, lines in weeks.items():
            expected: list[prices.LineSpan] = [(str(line["start"]), str(line.get("end") or line["start"]), line.get("price")) for line in lines]
            assert spans[product_id] == expected, (seed, product_id)


def test_without_own_spans_keeps_what_without_own_weeks_keeps() -> None:
    chance: random.Random = random.Random(7)
    for seed in range(200):
        wanted, history = _history(seed)
        weeks: dict[str, list[dict[str, Any]]] = prices._line_weeks(wanted, history)
        spans: dict[str, list[prices.LineSpan]] = prices._line_spans(wanted, history)
        for product_id, lines in weeks.items():
            own: list[dict[str, Any]] = [
                {"start": f"2026-{chance.randint(1, 12):02d}-{chance.randint(1, 20):02d}", "end": None if chance.random() < 0.3 else "2026-12-31"}
                for _ in range(chance.randint(0, 3))
            ]
            kept: list[dict[str, Any]] = prices._without_own_weeks(lines, own)
            assert prices._without_own_spans(spans[product_id], own) == [
                (str(line["start"]), str(line.get("end") or line["start"]), line.get("price")) for line in kept
            ], (seed, product_id)


def test_the_verdict_asks_for_spans() -> None:
    import asyncio
    from unittest import mock

    seen: list[Any] = []

    async def line_offers(store: str, catalogue: Any, ids: list[str], fallback: Any = None, *, weeks: Any = None) -> dict[str, list[Any]]:
        seen.append(weeks)
        return {}

    with mock.patch.object(prices, "_line_offers", line_offers):
        asyncio.run(prices._verdict_lines("ah", object(), [{"id": "1", "title": "AH Melk", "unitSize": "1 l"}], ["1"]))
    assert seen == [prices._line_spans]
