"""De rangorde van de aanbiedingen: de laagste prijs van het afgelopen jaar per product (zie prices._annotate).

"Beste" is voor de eigenaar: de prijs van nu is de laagste prijs van de afgelopen twaalf maanden. Die laagste prijs komt uit vier
bronnen, alles wat de app van het product bewaart: eigen aanbiedingen (de actieprijs per stuk), folderacties voor de hele lijn
(per stuk, line_offers.per_piece), aankopen (wat je per stuk betaalde) en bewaarde gewone prijzen. De aanbieding van deze week
zelf telt niet mee: die is wat we vergelijken.

Een punt is `(dag, prijs per stuk, bron)`. `year_low` geeft per aanbieding:

* `status`: `lowest` als de actieprijs van nu op of onder de laagste van de afgelopen 365 dagen ligt (binnen een halve cent),
  `above` als hij erboven ligt, `unknown` zonder prijs van nu, zonder punt in het jaar of met een dekking korter dan een jaar.
* `price`, `month` en `source`: de laagste prijs in het jaar, de maand waarin hij het laatst gold en waar hij vandaan kwam.
* `coveredSince`: de oudste dag waarop een bron iets van het product weet. Pas vanaf 365 dagen dekking is "laagste in een jaar"
  een uitspraak en geen toeval van een app die het product drie weken volgt.
* `above`: het verschil met de laagste, `0.0` bij `lowest`.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

YEAR_DAYS: int = 365
HALF_CENT: float = 0.005

Point = tuple[str, float, str]


def _empty() -> dict[str, Any]:
    return {
        "status": "unknown",
        "price": None,
        "month": None,
        "source": None,
        "coveredSince": None,
        "above": None,
    }


def year_low(
    effective: float | None, points: list[Point], today: str
) -> dict[str, Any]:
    """De laagste prijs van de afgelopen twaalf maanden en hoe de prijs van nu zich daartoe verhoudt (zie de module)."""
    valid: list[Point] = [
        point
        for point in points
        if point[0] and point[1] is not None and point[1] > 0 and point[0][:10] <= today
    ]
    if effective is None or not valid:
        return _empty()
    covered_since: str = min(point[0][:10] for point in valid)
    since: str = (date.fromisoformat(today) - timedelta(days=YEAR_DAYS)).isoformat()
    in_year: list[Point] = [point for point in valid if point[0][:10] > since]
    covered: int = (date.fromisoformat(today) - date.fromisoformat(covered_since)).days
    result: dict[str, Any] = {**_empty(), "coveredSince": covered_since}
    if covered < YEAR_DAYS or not in_year:
        return result
    lowest: float = min(point[1] for point in in_year)
    last: Point = max(
        (point for point in in_year if abs(point[1] - lowest) <= HALF_CENT),
        key=lambda point: point[0],
    )
    result.update(price=round(lowest, 2), month=last[0][:7], source=last[2])
    if effective <= lowest + HALF_CENT:
        result.update(status="lowest", above=0.0)
    else:
        result.update(status="above", above=round(effective - lowest, 2))
    return result
