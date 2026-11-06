"""Bewakers van de omzetting naar asyncio: wat na Taak 22 nergens meer in `mealieshopper/` mag staan, en wat een verzoek,
een http-client en het afsluiten van de app moeten doen.

Elke test leest de bron of draait de app, zodat een terugval (een vergeten `time.sleep`, een synchrone verbinding in de
event loop, een client zonder User-Agent) hier faalt en niet pas in productie.
"""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import re
import threading
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest
from quart import Quart
from quart.typing import TestClientProtocol as QuartClient

from mealieshopper import http_client, tasks
from mealieshopper.app import create_app

SOURCE: Path = Path(__file__).resolve().parent.parent / "mealieshopper"

# Wat in geen enkele module meer mag staan: requests, threads en een slaap die de event loop vasthoudt.
FORBIDDEN: tuple[str, ...] = ("import requests", "from requests", "threading.Thread(", "threading.Lock(", "ThreadPoolExecutor", "time.sleep(")

# Waar `asyncio.to_thread` mag: alleen rond rekenwerk en ander werk dat geen await kent (zie regels.md).
TO_THREAD_ALLOWED: dict[str, str] = {
    "image_cache.py": "Pillow meet een afbeelding",
    "product_images.py": "Pillow verkleint een productfoto",
    "auth.py": "py_webauthn controleert een passkey",
    "auth_password.py": "scrypt voor wachtwoorden",
    "backup.py": "zip, versleuteling en de synchrone sqlite3-fases van de back-up",
    "about.py": "git rev-parse (subprocess) voor de commit",
}

# Waar `sqlite3.connect(` mag: alleen de fases van de back-up, die als geheel in een thread lopen. De app zelf gebruikt aiosqlite.
SQLITE_CONNECT_ALLOWED: frozenset[str] = frozenset({"backup.py"})

# Blokkerende aanroepen die nooit direct in een `async def` mogen staan (een geneste functie of lambda telt apart: die loopt
# in een thread of later).
BLOCKING_IN_ASYNC: frozenset[str] = frozenset({"time.sleep", "sqlite3.connect", "subprocess.run", "subprocess.Popen", "subprocess.check_output"})

# Een bestand openen in een `async def` mag alleen hier, met de reden.
OPEN_IN_ASYNC_ALLOWED: dict[tuple[str, str], str] = {
    ("auth.py", "master_secret"): "een keer `secret.key` aanmaken (64 tekens), alleen als die er nog niet is",
    ("backup.py", "stage"): "het geüploade bestand in stukken van hoogstens een paar MB naar de lokale schijf schrijven",
    ("backup.py", "backup_create"): "de gemaakte back-up openen om hem te streamen naar de browser",
}
OPENERS: frozenset[str] = frozenset({"open", "os.open", "os.fdopen", "io.open"})

# De synchrone helpers van de back-up zijn alleen voor de thread van een fase: een `async def` roept ze nooit zelf aan.
THREAD_ONLY: frozenset[str] = frozenset({"_thread_connect", "_jobs_connect", "_job_update", "write_backup", "check_rows", "replace_everything", "read_plan", "unseal", "_check_staged"})


def modules() -> Iterator[tuple[str, str]]:
    for path in sorted(SOURCE.glob("*.py")):
        yield path.name, path.read_text(encoding="utf-8")


def code_lines(text: str) -> Iterator[tuple[int, str]]:
    """De regels van een module zonder commentaar en docstrings: alleen wat Python uitvoert."""
    tree: ast.Module = ast.parse(text)
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first: ast.stmt = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                docstrings.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))
    for number, line in enumerate(text.splitlines(), 1):
        code: str = line.split("#", 1)[0]
        if number not in docstrings and code.strip():
            yield number, code


def async_body_calls(function: ast.AsyncFunctionDef) -> Iterator[ast.Call]:
    """De aanroepen in de body van `function` zelf, zonder geneste functies, lambdas en klassen."""
    stack: list[ast.AST] = list(function.body)
    while stack:
        node: ast.AST = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        if isinstance(node, ast.Call):
            yield node
        stack.extend(ast.iter_child_nodes(node))


def async_functions() -> Iterator[tuple[str, ast.AsyncFunctionDef]]:
    for name, text in modules():
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.AsyncFunctionDef):
                yield name, node


def test_no_module_uses_requests_threading_or_blocking_sleep() -> None:
    found: list[str] = [
        f"{name}:{number}: {code.strip()}"
        for name, text in modules()
        for number, code in code_lines(text)
        if any(pattern in code for pattern in FORBIDDEN)
    ]
    assert found == []
    assert importlib.util.find_spec("requests") is None, "requests staat niet meer in requirements.txt en hoort niet in de venv"
    assert not re.search(r"^requests\b", (SOURCE.parent / "requirements.txt").read_text(encoding="utf-8"), re.M | re.I)


def test_to_thread_only_around_cpu_work() -> None:
    used: dict[str, list[int]] = {}
    for name, text in modules():
        for number, code in code_lines(text):
            if "to_thread(" in code:
                used.setdefault(name, []).append(number)
    assert set(used) <= set(TO_THREAD_ALLOWED), {name: lines for name, lines in used.items() if name not in TO_THREAD_ALLOWED}


def test_sqlite3_connect_only_in_the_backup_thread() -> None:
    found: set[str] = {name for name, text in modules() for _, code in code_lines(text) if "sqlite3.connect(" in code}
    assert found <= SQLITE_CONNECT_ALLOWED


def test_no_async_def_blocks_the_event_loop() -> None:
    blocking: list[str] = []
    opened: list[str] = []
    thread_only: list[str] = []
    for name, function in async_functions():
        for call in async_body_calls(function):
            called: str = ast.unparse(call.func)
            where: str = f"{name}:{call.lineno} in {function.name}: {called}"
            if called in BLOCKING_IN_ASYNC:
                blocking.append(where)
            if called in OPENERS or (isinstance(call.func, ast.Attribute) and call.func.attr == "open" and called not in ("http_client.open",)):
                if (name, function.name) not in OPEN_IN_ASYNC_ALLOWED:
                    opened.append(where)
            if called.split(".")[-1] in THREAD_ONLY:
                thread_only.append(where)
    assert blocking == []
    assert opened == []
    assert thread_only == [], "alleen via asyncio.to_thread"


@pytest.fixture
async def open_client(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[QuartClient]:
    """Een client op een app zonder inloggen, zodat /api/about zijn werk doet in plaats van een 401 te geven."""
    monkeypatch.setenv("PASSKEY_AUTH_ENABLED", "false")
    app: Quart = create_app()
    async with app.test_app() as test_app:
        yield test_app.test_client()


async def test_a_request_leaves_the_thread_count_unchanged(open_client: QuartClient) -> None:
    """Een verzoek zonder rekenwerk start geen thread die blijft staan: geen vergeten `to_thread` en geen aiosqlite-verbinding
    (een thread per verbinding) die openblijft."""
    assert (await open_client.get("/api/about")).status_code == 200
    before: int = threading.active_count()  # na de eerste keer: de commit van git staat dan vast (een thread van de loop)
    for _ in range(20):
        assert (await open_client.get("/api/about")).status_code == 200
    deadline: float = time.monotonic() + 2
    while threading.active_count() > before and time.monotonic() < deadline:
        await asyncio.sleep(0.01)  # een gesloten aiosqlite-verbinding stopt haar thread net na het sluiten
    assert threading.active_count() == before


async def test_every_http_client_has_an_explicit_user_agent() -> None:
    try:
        assert http_client.client("bewaker").headers["User-Agent"] == http_client.DEFAULT_USER_AGENT
        assert http_client.client("bewaker-eigen", headers={"User-Agent": "MealieShopper-test"}).headers["User-Agent"] == "MealieShopper-test"
        async with http_client.session("bewaker-sessie") as made:
            assert made.headers["User-Agent"] == http_client.DEFAULT_USER_AGENT
        async with http_client.session("bewaker-sessie", headers={"User-Agent": "MealieShopper-test"}) as made:
            assert made.headers["User-Agent"] == "MealieShopper-test"
        for refused in ("", "  ", f"python-httpx/{httpx.__version__}"):
            with pytest.raises(ValueError):
                http_client.client(f"bewaker-{refused!r}", headers={"User-Agent": refused})
            with pytest.raises(ValueError):
                async with http_client.session("bewaker-geweigerd", headers={"User-Agent": refused}):
                    pass
        assert not http_client.DEFAULT_USER_AGENT.startswith("python-httpx")
    finally:
        await http_client.aclose_all()


async def test_after_serving_cancels_tasks_and_closes_clients(app: Quart) -> None:
    async with app.test_app():
        made: httpx.AsyncClient = http_client.client("bewaker-afsluiten")
        forever: asyncio.Task[None] = tasks.spawn("bewaker-afsluiten", asyncio.Event().wait())
        assert "bewaker-afsluiten" in tasks.running()
    assert tasks.running() == {}
    assert forever.cancelled()
    assert made.is_closed
