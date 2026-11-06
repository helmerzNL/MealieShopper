"""Uitgaande http-verzoeken: een gedeelde `httpx.AsyncClient` per naam en per event loop.

Elke module die met een winkel, Mealie of GitHub praat, haalt haar client hier op met `client("naam")`. Zo
gelden overal dezelfde regels:

* een vaste User-Agent (`DEFAULT_USER_AGENT`) als de aanroeper zelf geen meegeeft, nooit die van httpx,
* geen cookies: een cookie uit een antwoord gaat nooit mee met een later verzoek,
* `trust_env=False`: proxy-variabelen en `SSL_CERT_FILE` of `SSL_CERT_DIR` uit de omgeving tellen niet mee,
* de hooks van het auditlog (`audit.httpx_hooks()`).

Een `httpx.AsyncClient` hoort bij de event loop waarin hij zijn verbindingen opent. Daarom bewaren we de
clients per loop: een nieuwe loop (een test, `asyncio.run`) krijgt nieuwe clients. Een loop verdwijnt niet
vanzelf uit `_clients`: een client die een verzoek deed, houdt open verbindingen vast, en die houden de loop
vast. De regel voor een loop leeft dus zolang zijn clients open zijn. Productie heeft een loop en sluit de
clients bij het afsluiten. Elke losse `asyncio.run(...)` die een client gebruikte, eindigt daarom met
`await aclose_all()`.
"""

from __future__ import annotations

import asyncio
import weakref
from collections.abc import Mapping

import httpx

from . import audit

# Chrome 157 op Android, stabiel sinds 2026-11-03, opgezocht 2026-11-06
DEFAULT_USER_AGENT: str = "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Mobile Safari/537.36"

_clients: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, dict[str, httpx.AsyncClient]
] = weakref.WeakKeyDictionary()


def client(
    name: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float | httpx.Timeout = 30.0,
    follow_redirects: bool = True,
) -> httpx.AsyncClient:
    """De client `name` van de lopende event loop. Bestaat hij nog niet, dan maken we hem aan.

    Alleen aan te roepen binnen een lopende loop (dus vanuit een coroutine): daarbuiten geeft
    `asyncio.get_running_loop()` een RuntimeError. `headers` komen over de standaard heen, dus een eigen
    `User-Agent` wint. De instellingen van de eerste aanroep voor een naam gelden, latere aanroepen met andere
    instellingen krijgen zonder melding dezelfde client terug. Headers of een time-out voor een enkel verzoek
    geef je aan dat verzoek zelf mee.
    """
    loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
    clients: dict[str, httpx.AsyncClient] = _clients.setdefault(loop, {})
    wanted: httpx.Headers = httpx.Headers({"User-Agent": DEFAULT_USER_AGENT})
    wanted.update(headers or {})
    existing: httpx.AsyncClient | None = clients.get(name)
    if existing is not None and not existing.is_closed:
        return existing
    made: httpx.AsyncClient = _build(wanted, timeout, follow_redirects)
    clients[name] = made
    return made


def _build(
    headers: httpx.Headers, timeout: float | httpx.Timeout, follow_redirects: bool
) -> httpx.AsyncClient:
    hooks: Mapping[str, list[audit.HttpxHook]] = audit.httpx_hooks()

    async def forget_cookies(_response: httpx.Response) -> None:
        # httpx heeft geen schakelaar voor cookies: hij bewaart ze vlak voor de response-hooks. Deze hook staat
        # vooraan en heeft geen `await`, dus geen ander verzoek op deze client kan de cookie nog oppikken. Ook
        # een doorverwijzing binnen hetzelfde verzoek krijgt de cookie daardoor niet mee.
        made.cookies.clear()

    made: httpx.AsyncClient = httpx.AsyncClient(
        headers=headers,
        cookies=None,
        timeout=timeout,
        follow_redirects=follow_redirects,
        trust_env=False,
        event_hooks={
            "request": list(hooks["request"]),
            "response": [forget_cookies, *hooks["response"]],
        },
    )
    return made


async def aclose_all() -> None:
    """Sluit alle clients van de lopende event loop en vergeet ze."""
    clients: dict[str, httpx.AsyncClient] = _clients.pop(asyncio.get_running_loop(), {})
    for made in clients.values():
        await made.aclose()


def reset() -> None:
    """Vergeet alle clients van alle loops, zonder ze te sluiten (tests)."""
    _clients.clear()
