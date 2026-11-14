"""Kiest de gewone prijs van een product voor een dag uit gedateerde prijzen.

Pure module, importeert niets van de app. De punten komen uit aankopen, de
zoekcache en aanbiedingen. Een aankoop (order, receipt, import) weegt zwaarder
dan een zoekresultaat, en dat weegt zwaarder dan een aanbieding.
"""

from datetime import date, timedelta
from typing import NamedTuple

SOURCE_RANK: dict[str, int] = {
    "order": 0,
    "receipt": 0,
    "import": 0,
    "search": 1,
    "offer": 2,
}
FRESH_DAYS: int = 30
JUDGED_DAYS: int = 365
_UNKNOWN_RANK: int = max(SOURCE_RANK.values()) + 1


class RegularPoint(NamedTuple):
    day: str
    price: float
    source: str


class Chosen(NamedTuple):
    price: float
    day: str
    source: str
    fresh: bool
    judged: bool


def _rank(point: RegularPoint) -> int:
    return SOURCE_RANK.get(point.source, _UNKNOWN_RANK)


def _chosen(point: RegularPoint, fresh: bool, judged: bool) -> Chosen:
    return Chosen(point.price, point.day, point.source, fresh, judged)


def _newest(points: list[RegularPoint]) -> RegularPoint:
    """Nieuwste dag, bij gelijke dag de laagste rang."""
    latest: str = max(p.day for p in points)
    return min((p for p in points if p.day == latest), key=_rank)


def _lowest(points: list[RegularPoint]) -> RegularPoint:
    """Laagste prijs, bij gelijke prijs de nieuwste dag, dan de laagste rang."""
    lowest_price: float = min(p.price for p in points)
    return _newest([p for p in points if p.price == lowest_price])


def choose(points: list[RegularPoint], on: str) -> Chosen | None:
    """De gewone prijs op dag `on` (ISO), of None zonder punten tot en met `on`."""
    usable: list[RegularPoint] = [p for p in points if p.day <= on]
    if not usable:
        return None
    today: date = date.fromisoformat(on)
    fresh_from: str = (today - timedelta(days=FRESH_DAYS)).isoformat()
    fresh: list[RegularPoint] = [p for p in usable if p.day >= fresh_from]
    if fresh:
        return _chosen(_lowest(fresh), True, True)
    newest: RegularPoint = _newest(usable)
    judged_from: str = (today - timedelta(days=JUDGED_DAYS)).isoformat()
    return _chosen(newest, False, newest.day >= judged_from)
