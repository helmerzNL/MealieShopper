"""Gedeelde pytest-fixtures voor de tests van MealieShopper."""

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import respx
from quart import Quart
from quart.typing import TestClientProtocol

# Ook zonder de fixture `data_dir` (een kale `pytest`-run, unittest-tests) mag `before_serving` de dagelijkse
# aanbiedingen-thread niet starten. Dit moet vooraf, voor de eerste import van mealieshopper.
os.environ.setdefault("MEALIESHOPPER_TESTING", "1")

from mealieshopper import auth  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402


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
