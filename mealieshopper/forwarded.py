"""Forwarded-headers voor ASGI: de vervanger van werkzeugs `ProxyFix(x_for=1, x_proto=1, x_host=1, x_port=1)`.

Achter een reverse proxy (SWAG, nginx) ziet de app anders `http`, de interne hostnaam en het adres van de proxy. Deze
middleware zet het schema, de host, de poort en het client-adres uit `X-Forwarded-*` in de scope voordat Quart er een
verzoek van maakt. Zonder zulke headers gaat de scope onaangeroerd door.
"""

from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

Scope = MutableMapping[str, Any]
ASGIApp = Callable[[Scope, Callable[..., Awaitable[Any]], Callable[..., Awaitable[Any]]], Awaitable[None]]

DEFAULT_PORTS: dict[str, int] = {"http": 80, "https": 443, "ws": 80, "wss": 443}
WEBSOCKET_SCHEMES: dict[str, str] = {"http": "ws", "https": "wss"}


def _parse_port(value: str) -> int | None:
    """Een poort uit een header: alleen ASCII-cijfers (`int()` weigert bv. "²") en 1 tot en met 65535, anders None."""
    text: str = value.strip()
    # Eerst de lengte: int() weigert meer dan 4300 cijfers met een ValueError.
    if len(text) <= 5 and text.isascii() and text.isdigit() and 1 <= int(text) <= 65535:
        return int(text)
    return None


def _split_host(value: str) -> tuple[str, int | None]:
    """Splitst "host:poort" (ook "[::1]:poort") in host en poort. Een ongeldige poort telt als afwezig."""
    host: str = value.strip()
    if host.startswith("["):
        end: int = host.find("]")
        rest: str = host[end + 1 :]
        host = host[: end + 1]
    else:
        host, _, rest = host.partition(":")
        rest = f":{rest}" if rest else ""
    return host, _parse_port(rest[1:]) if rest.startswith(":") else None


class ForwardedHeaders:
    def __init__(self, app: ASGIApp, *, hops: int = 1) -> None:
        if hops < 1:
            raise ValueError("hops moet minstens 1 zijn, anders telt de eerste (te vervalsen) waarde")
        self.app: ASGIApp = app
        self.hops: int = hops

    async def __call__(self, scope: Scope, receive: Callable[..., Awaitable[Any]], send: Callable[..., Awaitable[Any]]) -> None:
        if scope["type"] in ("http", "websocket"):
            scope = self._rewrite(scope)
        await self.app(scope, receive, send)

    def _last_hops(self, value: str | None) -> str | None:
        """De `hops`-de waarde van achteren (zoals `ProxyFix._get_real_value`), of None als er minder waarden zijn dan `hops`.

        Geldt voor elke forwarded-header: de proxy voegt zijn waarde achteraan toe, dus alles ervoor kan de client vervalsen."""
        if not value:
            return None
        parts: list[str] = [part.strip() for part in value.split(",") if part.strip()]
        return parts[-self.hops] if len(parts) >= self.hops else None

    def _rewrite(self, scope: Scope) -> Scope:
        headers: dict[str, str] = {}
        for name, value in scope.get("headers", ()):
            key: str = name.decode("latin-1").lower()
            text: str = value.decode("latin-1")
            # Herhaalde regels van een forwarded-header tellen als een kommalijst (zoals ProxyFix), in volgorde van binnenkomst.
            headers[key] = f"{headers[key]},{text}" if key in headers and key.startswith("x-forwarded-") else headers.get(key, text)
        forwarded_for: str | None = self._last_hops(headers.get("x-forwarded-for"))
        proto: str = (self._last_hops(headers.get("x-forwarded-proto")) or "").lower()
        host_value: str = self._last_hops(headers.get("x-forwarded-host")) or ""
        port_value: str = self._last_hops(headers.get("x-forwarded-port")) or ""
        if not (forwarded_for or proto or host_value or port_value):
            return scope

        new: dict[str, Any] = dict(scope)  # ondiepe kopie: de scope van de server zelf blijft zoals hij was
        scheme: str = new.get("scheme", "http")
        if proto in ("http", "https"):
            scheme = WEBSOCKET_SCHEMES[proto] if scope["type"] == "websocket" else proto
            new["scheme"] = scheme
        if forwarded_for:
            client: Any = scope.get("client")
            new["client"] = (forwarded_for, client[1] if client else 0)
        if host_value or port_value:
            old_host, old_port = _split_host(headers.get("host", ""))
            host, host_port = _split_host(host_value) if host_value else (old_host, old_port)
            port: int | None = _parse_port(port_value) or host_port
            if port is None:
                port = DEFAULT_PORTS.get(scheme, 80)
            new["server"] = (host, port)
            if host:
                shown: str = host if port == DEFAULT_PORTS.get(scheme) else f"{host}:{port}"
                new["headers"] = [(n, v) for n, v in scope["headers"] if n.lower() != b"host"] + [(b"host", shown.encode("latin-1"))]
        return new
