"""De nachtelijke synchronisatie van je bestellingen en kassabonnen.

De bestelpagina haalt ze op als je daar op de knop drukt. Zonder die knop bleef een bestelling die inmiddels bezorgd was op
"open" staan en ontbraken haar producten in de prijsgeschiedenis. De dagelijkse run (`offers_schedule.run_all`) haalt ze
daarom ook op, voor elke winkel die gekoppeld is. Een winkel die het niet doet, houdt de rest niet tegen.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable
from typing import Any, Callable, NamedTuple

from . import ah, ah_orders, crisp, crisp_orders, jumbo, jumbo_orders, picnic, picnic_orders

log = logging.getLogger(__name__)

Sync = Callable[[], Awaitable[dict[str, Any]]]


class _Store(NamedTuple):
    name: str
    linked: Callable[[], Awaitable[str]]  # het token van de koppeling, leeg als de winkel niet gekoppeld is
    parts: tuple[tuple[str, Sync], ...]  # (naam in de uitkomst, de synchronisatie)


# Lambda's: zo geldt de functie van het moment van aanroepen (een test vervangt hem).
_STORES: tuple[_Store, ...] = (
    _Store("ah", lambda: ah.configured_refresh_token(),
           (("orders", lambda: ah_orders.sync_orders()), ("receipts", lambda: ah_orders.sync_receipts()))),
    _Store("jumbo", lambda: jumbo.configured_refresh_token(), (("orders", lambda: jumbo_orders.sync_orders()),)),
    _Store("picnic", lambda: picnic.configured_auth_key(), (("orders", lambda: picnic_orders.sync_orders()),)),
    _Store("crisp", lambda: crisp.configured_client_id(), (("orders", lambda: crisp_orders.sync_orders()),)),
)


async def _is_linked(store: _Store) -> bool:
    try:
        return bool(await store.linked())
    except Exception:  # noqa: BLE001 - een winkel waarvan de koppeling niet te lezen is, slaan we over
        log.warning("Koppeling van %s lezen voor de nachtelijke synchronisatie mislukt", store.name, exc_info=True)
        return False


async def _sync_part(store: str, part: str, sync: Sync) -> dict[str, Any]:
    try:
        return await sync()
    except Exception as exc:  # noqa: BLE001 - de winkels zijn onofficieel: de volgende nacht opnieuw
        log.warning("Nachtelijke synchronisatie van %s (%s) mislukt: %s", store, part, exc)
        return {"error": str(exc) or type(exc).__name__}


async def sync_nightly() -> dict[str, dict[str, dict[str, Any]]] | None:
    """Haal van elke gekoppelde winkel de bestellingen op (en van AH de kassabonnen). Geeft per winkel en onderdeel de uitkomst
    van de synchronisatie, of {"error": ...} als die mislukte. None als er geen winkel gekoppeld is."""
    result: dict[str, dict[str, dict[str, Any]]] = {}
    for store in _STORES:
        if await _is_linked(store):
            result[store.name] = {part: await _sync_part(store.name, part, sync) for part, sync in store.parts}
    return result or None
