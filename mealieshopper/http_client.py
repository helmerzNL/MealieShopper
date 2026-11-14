"""Uitgaande http-verzoeken: een gedeelde `httpx.AsyncClient` per naam en per event loop.

Elke module die met een winkel, Mealie of GitHub praat, haalt haar client hier op met `client("naam")`. Zo
gelden overal dezelfde regels:

* een vaste User-Agent (`DEFAULT_USER_AGENT`) als de aanroeper zelf geen meegeeft, nooit die van httpx,
* geen cookies: een cookie uit een antwoord gaat nooit mee met een later verzoek,
* de omgeving telt mee zoals bij requests in 3.x: HTTP(S)_PROXY, ALL_PROXY, NO_PROXY, SSL_CERT_FILE en
  SSL_CERT_DIR (`trust_env=True`), en REQUESTS_CA_BUNDLE of anders CURL_CA_BUNDLE als eigen CA-bestand of -map,
* de hooks van het auditlog (`audit.httpx_hooks()`).

Drie verschillen met requests in 3.x blijven bewust staan:

* NO_PROXY met een IP-bereik (`192.168.0.0/16`) telt bij httpx niet. Zet er hostnamen of losse IP-adressen in.
* `.netrc` wordt niet meer gelezen: httpx kent dat alleen als eigen `auth`, en die geven we niet mee.
* SSL_CERT_FILE en SSL_CERT_DIR gelden als REQUESTS_CA_BUNDLE en CURL_CA_BUNDLE leeg zijn. requests negeerde ze.

Een `httpx.AsyncClient` hoort bij de event loop waarin hij zijn verbindingen opent. Daarom bewaren we de
clients per loop: een nieuwe loop (een test, `asyncio.run`) krijgt nieuwe clients. Een loop verdwijnt niet
vanzelf uit `_clients`: een client die een verzoek deed, houdt open verbindingen vast, en die houden de loop
vast. De regel voor een loop leeft dus zolang zijn clients open zijn. Productie heeft een loop en sluit de
clients bij het afsluiten. Elke losse `asyncio.run(...)` die een client gebruikte, eindigt daarom met
`await aclose_all()`.
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import ssl
import sys
import weakref
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from os import environ
from pathlib import Path

import httpx

from . import audit

# httpcore vraagt bij elk slot en elke verbinding `import sniffio` (`httpcore._synchronization.current_async_library`), en valt
# zonder sniffio terug op asyncio. Die import faalt dan elke keer opnieuw, en een mislukte import zoekt heel sys.path af, met
# een stat per map. Op de event loop laat elke stat de GIL los, en terwijl een andere thread rekent krijgt de loop hem pas na
# het wisselinterval terug: onder belasting een stilstand van 108 tot 145 ms (taak 9c). None in sys.modules laat de import
# meteen falen, zonder te zoeken (de taalreferentie, "The module cache"). Alleen als sniffio er echt niet is.
if "sniffio" not in sys.modules and importlib.util.find_spec("sniffio") is None:
    sys.modules["sniffio"] = None  # type: ignore[assignment]

logger: logging.Logger = logging.getLogger(__name__)

# Chrome 157 op Android, stabiel sinds 2026-11-03, opgezocht 2026-11-06
DEFAULT_USER_AGENT: str = "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Mobile Safari/537.36"

_clients: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, dict[str, httpx.AsyncClient]
] = weakref.WeakKeyDictionary()


def _headers(headers: dict[str, str] | None) -> httpx.Headers:
    """`DEFAULT_USER_AGENT`, met `headers` eroverheen (een eigen `User-Agent` wint).

    Een lege `User-Agent` of die van httpx zelf (`python-httpx/...`) geeft een ValueError: elke bron heeft een eigen of de
    standaard, nooit die van de bibliotheek."""
    wanted: httpx.Headers = httpx.Headers({"User-Agent": DEFAULT_USER_AGENT})
    wanted.update(headers or {})
    agent: str = wanted.get("User-Agent", "").strip()
    if not agent or agent.lower().startswith("python-httpx"):
        raise ValueError(f"http_client: een client zonder eigen User-Agent ({agent!r})")
    return wanted


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
    wanted: httpx.Headers = _headers(headers)
    existing: httpx.AsyncClient | None = clients.get(name)
    if existing is not None and not existing.is_closed:
        return existing
    made: httpx.AsyncClient = _new_client(
        wanted, timeout, follow_redirects, forget_cookies=True
    )
    clients[name] = made
    return made


def _verify() -> ssl.SSLContext | bool:
    """De TLS-controle: REQUESTS_CA_BUNDLE, anders CURL_CA_BUNDLE (een bestand of een map met CA's), zoals requests die las.

    Zonder die twee `True`: dan kiest httpx met `trust_env` zelf SSL_CERT_FILE, SSL_CERT_DIR of de CA's van certifi. We lezen
    de omgeving bij het maken van de client."""
    bundle: str = (
        environ.get("REQUESTS_CA_BUNDLE") or environ.get("CURL_CA_BUNDLE") or ""
    ).strip()
    if not bundle:
        return True
    if Path(bundle).is_dir():
        return ssl.create_default_context(capath=bundle)
    return ssl.create_default_context(cafile=bundle)


def _new_client(
    headers: httpx.Headers,
    timeout: float | httpx.Timeout,
    follow_redirects: bool,
    forget_cookies: bool,
) -> httpx.AsyncClient:
    """Een client met de regels van dit module. `forget_cookies` voor de gedeelde clients, een sessie houdt haar jar."""
    hooks: Mapping[str, list[audit.HttpxHook]] = audit.httpx_hooks()
    response_hooks: list[audit.HttpxHook] = list(hooks["response"])

    async def drop_cookies(_response: httpx.Response) -> None:
        # httpx heeft geen schakelaar voor cookies: hij bewaart ze vlak voor de response-hooks. Deze hook staat
        # vooraan en heeft geen `await`, dus geen ander verzoek op deze client kan de cookie nog oppikken. Ook
        # een doorverwijzing binnen hetzelfde verzoek krijgt de cookie daardoor niet mee.
        made.cookies.clear()

    if forget_cookies:
        response_hooks.insert(0, drop_cookies)
    made: httpx.AsyncClient = httpx.AsyncClient(
        headers=headers,
        timeout=timeout,
        follow_redirects=follow_redirects,
        trust_env=True,
        verify=_verify(),
        event_hooks={"request": list(hooks["request"]), "response": response_hooks},
    )
    return made


@asynccontextmanager
async def session(
    name: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float | httpx.Timeout = 30.0,
    follow_redirects: bool = True,
) -> AsyncIterator[httpx.AsyncClient]:
    """Een kortlevende client met een eigen cookiejar, voor een keer ophalen van een pagina die de gebruiker opgaf.

    Gebruik hem als de doorverwijzingen van die pagina hun eigen cookies nodig hebben: een site die bij een 302 een cookie
    zet (toestemming, een cookiecontrole) en die bij de volgende stap terug verwacht. De gedeelde `client()` wist elke
    cookie na elk antwoord en zou dan op de verkeerde pagina of in een lus eindigen. Verder gelden dezelfde regels als
    bij `client()`: `DEFAULT_USER_AGENT` tenzij `headers` een eigen `User-Agent` geven, de omgeving voor proxy en CA's,
    en de hooks van het auditlog.

    De jar hoort bij deze ene client, die bij het verlaten van het `async with`-blok sluit. Een cookie komt dus nooit bij
    een ander verzoek terecht, ook niet bij een volgende `session()` met dezelfde naam. `name` is alleen voor het
    logboek, er wordt niets onder bewaard.
    """
    logger.debug("Sessie %s geopend", name)
    async with _new_client(
        _headers(headers), timeout, follow_redirects, forget_cookies=False
    ) as made:
        yield made


async def aclose_all() -> None:
    """Sluit alle clients van de lopende event loop en vergeet ze."""
    clients: dict[str, httpx.AsyncClient] = _clients.pop(asyncio.get_running_loop(), {})
    for made in clients.values():
        await made.aclose()


def reset() -> None:
    """Vergeet alle clients van alle loops, zonder ze te sluiten (tests)."""
    _clients.clear()
