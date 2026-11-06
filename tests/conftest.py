"""Gedeelde pytest-fixtures voor de tests van MealieShopper."""

import ipaddress
import os
import socket
import threading
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import respx
from quart import Quart
from quart.typing import TestClientProtocol

# Ook zonder de fixture `data_dir` (een kale `pytest`-run, unittest-tests) mag `before_serving` de dagelijkse
# aanbiedingen-thread niet starten. Dit moet vooraf, voor de eerste import van mealieshopper.
os.environ.setdefault("MEALIESHOPPER_TESTING", "1")

from mealieshopper import auth  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402


# Hosts die een test mag bereiken: alleen deze machine. De back-up- en TLS-tests draaien een lokale server, respx en de
# testclient van Quart openen geen socket.
LOOPBACK_NAMES: frozenset[str] = frozenset({"localhost"})
_network_attempts: list[str] = []
_network_lock: threading.Lock = threading.Lock()


def _is_loopback(host: Any) -> bool:
    """Of `host` (een naam, een IP-adres, bytes of None voor een passieve lookup) op deze machine blijft."""
    if host is None:
        return True
    name: str = host.decode() if isinstance(host, bytes) else str(host)
    if name.lower() in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(name.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


def _refuse(host: Any) -> None:
    """Onthoud de poging (een `except Exception` in de app mag hem niet verstoppen) en weiger hem."""
    with _network_lock:
        _network_attempts.append(repr(host))
    raise RuntimeError(f"Test probeert het netwerk te bereiken: {host}")


def _guarded_address(sock: socket.socket, address: Any) -> None:
    if sock.family in (socket.AF_INET, socket.AF_INET6) and isinstance(address, tuple) and not _is_loopback(address[0]):
        _refuse(address[0])


@pytest.fixture(autouse=True, scope="session")
def network_guard() -> Iterator[None]:
    """Geen test bereikt het netwerk: elke verbinding of DNS-lookup buiten loopback geeft een RuntimeError.

    Gepatcht zijn `socket.socket.connect`, `socket.socket.connect_ex`, `socket.create_connection` en `socket.getaddrinfo`,
    voor de hele sessie. Een lek wordt een fout van de test (zie `no_network_attempts`), ook als de app de RuntimeError opvangt.
    """
    connect: Callable[..., Any] = socket.socket.connect
    connect_ex: Callable[..., Any] = socket.socket.connect_ex
    create_connection: Callable[..., Any] = socket.create_connection
    getaddrinfo: Callable[..., Any] = socket.getaddrinfo

    def guarded_connect(sock: socket.socket, address: Any) -> None:
        _guarded_address(sock, address)
        connect(sock, address)

    def guarded_connect_ex(sock: socket.socket, address: Any) -> int:
        _guarded_address(sock, address)
        result: int = connect_ex(sock, address)
        return result

    def guarded_create_connection(address: tuple[Any, ...], *args: Any, **kwargs: Any) -> socket.socket:
        if not _is_loopback(address[0]):
            _refuse(address[0])
        made: socket.socket = create_connection(address, *args, **kwargs)
        return made

    def guarded_getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> list[Any]:
        if not _is_loopback(host):
            _refuse(host)
        found: list[Any] = getaddrinfo(host, *args, **kwargs)
        return found

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(socket.socket, "connect", guarded_connect)
        patch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
        patch.setattr(socket, "create_connection", guarded_create_connection)
        patch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
        yield


@pytest.fixture(autouse=True)
def no_network_attempts(network_guard: None) -> Iterator[None]:
    """Een test die het netwerk probeerde te bereiken faalt, ook als de code de weigering heeft opgevangen."""
    with _network_lock:
        _network_attempts.clear()
    yield
    with _network_lock:
        tried: list[str] = list(_network_attempts)
        _network_attempts.clear()
    if tried:
        pytest.fail(f"Test probeert het netwerk te bereiken: {', '.join(sorted(set(tried)))}")


@pytest.fixture(autouse=True)
def fresh_auth_state() -> None:
    """Elke test begint zonder onthouden database en geheimen.

    `create_app()` maakt de database niet meer aan (dat doet `before_serving`), dus een test die zelf een data-map
    instelt en `auth.ensure_db()` aanroept, mag niet leunen op de vlag van een eerdere test.
    """
    auth.forget_runtime_state()


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Tijdelijke datamap, ruimt zichzelf op via tmp_path en monkeypatch."""
    monkeypatch.setenv("MEALIESHOPPER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MEALIESHOPPER_TESTING", "1")
    yield tmp_path


@pytest.fixture
def respx_mock() -> Iterator[respx.MockRouter]:
    """respx-router die niet eist dat elke route wordt aangeroepen."""
    with respx.mock(assert_all_called=False) as router:
        yield router


@pytest.fixture
def app(data_dir: Path) -> Quart:
    """De app uit create_app(), gebouwd in de tijdelijke datamap."""
    return create_app()


@pytest.fixture
async def client(app: Quart) -> AsyncIterator[TestClientProtocol]:
    """Async testclient van de app: `before_serving` en `after_serving` lopen een keer per test."""
    async with app.test_app() as test_app:
        yield test_app.test_client()
