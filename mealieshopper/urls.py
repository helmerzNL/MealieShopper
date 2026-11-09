"""Een openbare http(s)-URL die de app voor een gebruiker mag ophalen.

Een recept importeren haalt een adres op dat de gebruiker (of een assistent via de MCP-server) opgeeft. Een adres op het eigen
netwerk hoort daar niet bij: dat zou de server zelf naar Mealie, een NAS of een router laten kijken. `public_http_url` laat alleen
http en https door en weigert lokale en interne hostnamen en niet-openbare IP-adressen. De controle is op de hostnaam, zonder
DNS-resolutie en zonder redirects te volgen (bewuste keuze van de eigenaar).
"""

from __future__ import annotations

import ipaddress
from typing import Any
from urllib.parse import urlsplit

# Namen die alleen binnen je eigen netwerk bestaan (ook onboarding.py gebruikt deze lijst voor de passkey-uitleg).
PRIVATE_SUFFIXES: tuple[str, ...] = (
    ".localhost", ".local", ".lan", ".home", ".home.arpa", ".internal", ".localdomain", ".intranet", ".private", ".corp", ".box",
    ".svc", ".cluster.local",
)

SCHEME_MESSAGE: str = "Geef een recept-URL op die met http:// of https:// begint"
PRIVATE_MESSAGE: str = "Dit adres staat op een eigen netwerk. Importeer alleen recepten van het internet."


class UnsafeUrl(ValueError):
    """Geen openbaar http(s)-adres: de melding is de reden, in het Nederlands."""


def private_host(hostname: str) -> bool:
    """Een adres op je eigen netwerk: localhost, een lokaal domein, een naam zonder punt, een niet-openbaar IP-adres, of een
    getal dat geen canoniek adres is ("127.1", "0x7f.0.0.1", "0177.0.0.1": de resolver maakt daar wel een adres van)."""
    host: str = hostname.strip("[]").rstrip(".").casefold()
    if host == "localhost" or host.endswith(PRIVATE_SUFFIXES) or "." not in host and ":" not in host:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        last: str = host.rsplit(".", 1)[-1]
        return last.isdigit() or last.startswith("0x")  # een numerieke naam die niet canoniek is: weigeren
    return not address.is_global


def public_http_url(value: Any) -> str:
    """De URL, gestript, als het een openbaar http(s)-adres is. Anders `UnsafeUrl` met de reden."""
    url: str = str(value or "").strip() if isinstance(value, str) else ""
    parts = urlsplit(url) if url else None
    if parts is None or parts.scheme not in ("http", "https") or not parts.hostname:
        raise UnsafeUrl(SCHEME_MESSAGE)
    if private_host(parts.hostname):
        raise UnsafeUrl(PRIVATE_MESSAGE)
    return url
