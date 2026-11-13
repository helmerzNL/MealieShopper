"""Gedeelde pytest-fixtures voor de tests van MealieShopper."""

import ipaddress
import os
import shutil
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
# De ronde langs ahprijsindex.nl (ah_price_index.py) hoort bij de dagelijkse run. In de tests staat ze uit, tenzij een test
# haar zelf aanzet: zo start een test van de dagelijkse run geen task die na de test nog in de database leest.
os.environ.setdefault("MEALIESHOPPER_PRICE_INDEX", "false")

import loop_watch
import volume

from mealieshopper import ah_orders, auth, prices
from mealieshopper.app import create_app

loop_watch.install()  # elke event loop in de tests draait onder de bewaker van de event loop


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
    if (
        sock.family in (socket.AF_INET, socket.AF_INET6)
        and isinstance(address, tuple)
        and not _is_loopback(address[0])
    ):
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

    def guarded_create_connection(
        address: tuple[Any, ...], *args: Any, **kwargs: Any
    ) -> socket.socket:
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


# De clients nemen proxy en CA's uit de omgeving over (zoals requests). Een proxy of CA-bestand van de ontwikkelaar mag de
# tests niet sturen: de tests die het gedrag nagaan, zetten de variabelen zelf.
CLIENT_ENVIRONMENT: tuple[str, ...] = tuple(
    name
    for base in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")
    for name in (base, base.lower())
) + ("SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE")


@pytest.fixture(autouse=True, scope="session")
def no_client_environment() -> Iterator[None]:
    with pytest.MonkeyPatch.context() as patch:
        for name in CLIENT_ENVIRONMENT:
            patch.delenv(name, raising=False)
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
        pytest.fail(
            f"Test probeert het netwerk te bereiken: {', '.join(sorted(set(tried)))}"
        )


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


@pytest.fixture(scope="session")
def volume_template(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """De volumedatabase (`tests/volume.py`), een keer per run gebouwd. Een test werkt op een kopie: `volume_dir`.

    Aan het eind van de sessie gaat de map weg: pytest bewaart de tijdelijke mappen van de laatste drie runs, en de database
    is ruim 100 MB."""
    folder: Path = tmp_path_factory.mktemp("volume")
    volume.build(folder)
    yield folder
    shutil.rmtree(folder, ignore_errors=True)


def _remove_database(folder: Path) -> None:
    """De kopie van de volumedatabase met haar WAL-bestanden weg, zodat de tijdelijke map van een test klein blijft."""
    for name in (volume.DB_NAME, f"{volume.DB_NAME}-wal", f"{volume.DB_NAME}-shm"):
        (folder / name).unlink(missing_ok=True)


@pytest.fixture
def volume_dir(
    volume_template: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    """Een kopie van de volumedatabase als datamap, met de klok op `volume.TODAY`, zonder inloggen en zonder AH.

    `prices._CATALOGUES` gaat ervoor en erna leeg: de catalogus van AH is op deze schaal groot, en de sleutel is het pad van
    de database, dus elke kopie zou er anders een laten staan. Na de test gaat de kopie weg (ruim 100 MB per test).
    """
    shutil.copyfile(volume_template / volume.DB_NAME, tmp_path / volume.DB_NAME)
    monkeypatch.setenv("MEALIESHOPPER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MEALIESHOPPER_TESTING", "1")
    monkeypatch.setenv(
        "PASSKEY_AUTH_ENABLED", "false"
    )  # zoals de tests van de aanbiedingen
    volume.freeze_clock(monkeypatch)
    monkeypatch.setattr(ah_orders, "resolve_receipt_names", volume.no_receipt_lookup)
    prices._CATALOGUES.clear()
    yield tmp_path
    prices._CATALOGUES.clear()
    _remove_database(tmp_path)


@pytest.fixture
async def volume_client(volume_dir: Path) -> AsyncIterator[TestClientProtocol]:
    """Async testclient van een app op de volumedatabase."""
    app: Quart = create_app()
    async with app.test_app() as test_app:
        yield test_app.test_client()


@pytest.fixture(autouse=True)
def loop_watch_check(request: pytest.FixtureRequest) -> Iterator[None]:
    """Na elke test: de stilstanden van de event loop die de app veroorzaakte, in `enforce` een fout, in `report` genoteerd.

    Een stilstand zonder regel van de app (een `time.sleep` in de test zelf) telt niet, en `@pytest.mark.may_block("reden")`
    stelt een test vrij.
    """
    marker: Any = request.node.get_closest_marker("may_block")
    try:
        loop_watch.exemption_reason(
            marker
        )  # een markering zonder reden faalt voor de test, niet erna
    except ValueError as error:
        pytest.fail(str(error), pytrace=False)
    loop_watch.drain()  # wat een vorige loop nog meldde, hoort niet bij deze test
    yield
    message: str | None = loop_watch.check(
        request.node.nodeid, marker, loop_watch.MODE, loop_watch.drain()
    )
    if message:
        pytest.fail(message, pytrace=False)


def pytest_terminal_summary(terminalreporter: Any) -> None:
    lines: list[str] = loop_watch.summary_lines()
    if not lines:
        return
    terminalreporter.section(
        f"stilstanden van de event loop (> {round(loop_watch.LIMIT * 1000)} ms, binnenste regel van de app)"
    )
    for line in lines:
        terminalreporter.write_line(line)
