"""Bewakers van de omzetting naar asyncio: wat na Taak 22 nergens meer in `mealieshopper/` mag staan, en wat een verzoek,
een http-client en het afsluiten van de app moeten doen.

Elke test leest de bron of draait de app, zodat een terugval (een vergeten `time.sleep`, een synchrone verbinding in de
event loop, een client zonder User-Agent) hier faalt en niet pas in productie.
"""

from __future__ import annotations

import ast
import asyncio
import os
import re
import subprocess
import sys
import threading
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from unittest import mock

import httpx
import pytest
from quart import Quart
from quart.typing import TestClientProtocol as QuartClient

from mealieshopper import http_client, tasks
from mealieshopper.app import create_app

SOURCE: Path = Path(__file__).resolve().parent.parent / "mealieshopper"

# Wat in geen enkele module meer mag staan: requests, threads en een slaap die de event loop vasthoudt.
FORBIDDEN: tuple[str, ...] = ("import requests", "from requests", "threading.Thread(", "threading.Lock(", "ThreadPoolExecutor", "time.sleep(")

# De bewaker van de event loop (loop_guard.py) draait juist in een eigen thread: een taak op de loop kan zichzelf niet betrappen.
THREAD_ALLOWED: frozenset[str] = frozenset({"loop_guard.py", "compute.py", "name_keys.py"})
# Wat die modules daarvoor mogen gebruiken. compute.py heeft een eigen pool van daemonthreads (`compute.run`) met een slot om het
# maken ervan, dat alleen microseconden wordt vastgehouden. name_keys.py heeft een slot in `TextMemo` om het weghalen en wisselen
# van een generatie, alleen bij een misser: twee rekenthreads mogen de volgorde niet tegelijk inkorten. Het slot wordt hooguit een
# paar ms vastgehouden (de kopie van een volle generatie als tuple). Een `ThreadPoolExecutor` of `time.sleep` blijft ook daar verboden.
THREAD_PATTERNS: tuple[str, ...] = ("threading.Thread(", "threading.Lock(")

# Waar `asyncio.to_thread` mag: alleen rond rekenwerk en ander werk dat geen await kent (zie regels.md).
TO_THREAD_ALLOWED: dict[str, str] = {
    "image_cache.py": "Pillow meet een afbeelding",
    "product_images.py": "Pillow verkleint een productfoto",
    "auth.py": "py_webauthn controleert een passkey",
    "auth_password.py": "scrypt voor wachtwoorden",
    "backup.py": "zip, versleuteling en de synchrone sqlite3-fases van de back-up",
    "about.py": "git rev-parse (subprocess) voor de commit",
    "audit.py": "de export van tot 5000 regels als JSON of CSV",
    "factory_reset.py": "de afbeeldingencache wissen (shutil.rmtree over duizenden bestanden)",
    "compression.py": "een antwoord met tekst verpakken met gzip (de lijst met aanbiedingen is 3,7 MB)",
    "price_archive.py": "een prijsarchief uitpakken en controleren, invoegen en exporteren (zip, tar, gzip, tot een miljoen regels)",
    "uploads.py": "een stuk van een upload (tot 4 MB) naar schijf schrijven",
}

# Waar `sqlite3.connect(` mag: in thread_db, de verbinding voor de fasen van de back-up en het prijsarchief, die als geheel in
# een thread lopen, en in backup.py voor de lege kopie van het schema in het geheugen (`_schema_copy`, ook in de thread). De
# app zelf gebruikt aiosqlite.
SQLITE_CONNECT_ALLOWED: frozenset[str] = frozenset({"backup.py", "thread_db.py"})

# Blokkerende aanroepen die nooit direct in een `async def` mogen staan (een geneste functie of lambda telt apart: die loopt
# in een thread of later).
BLOCKING_IN_ASYNC: frozenset[str] = frozenset(
    {
        "time.sleep", "sqlite3.connect", "os.system",
        "subprocess.run", "subprocess.call", "subprocess.check_call", "subprocess.check_output", "subprocess.Popen",
    }
)

# Een bestand openen in een `async def` mag alleen hier, met de reden.
OPEN_IN_ASYNC_ALLOWED: dict[tuple[str, str], str] = {
    ("auth.py", "master_secret"): "een keer `secret.key` aanmaken (64 tekens), alleen als die er nog niet is",
    ("backup.py", "stage"): "het geüploade bestand in stukken van hoogstens een paar MB naar de lokale schijf schrijven",
    ("backup.py", "backup_create"): "de gemaakte back-up openen om hem te streamen naar de browser",
    ("price_archive.py", "stage"): "het geüploade archief in stukken van hoogstens een paar MB naar de lokale schijf schrijven",
    ("price_archive.py", "price_archive_export"): "de gemaakte export openen om hem te streamen naar de browser",
}
OPENERS: frozenset[str] = frozenset({"open", "os.open", "os.fdopen", "io.open"})

# De synchrone helpers van een fase zijn alleen voor haar thread: een `async def` roept ze nooit zelf aan. Met de module
# erbij: `organizers.apply` in recipe_bulk is iets anders dan `price_archive.apply`. Een kale naam hoort bij de module zelf.
THREAD_ONLY: frozenset[str] = frozenset(
    {
        *(f"backup.{name}" for name in (
            "_jobs_connect", "_job_update", "write_backup", "check_rows", "replace_everything", "read_plan",
            "read_file", "_read_file", "_schema_copy", "unseal", "_check_staged",
        )),
        "json_pieces.loads", "thread_db.connect", "price_archive.apply", "price_archive._check_upload",
        "price_archive_export.write_export", "price_archive_export.write_site_export", "uploads._write_chunk",
    }
)

# Een httpx-client maken of een losse aanroep van httpx (zonder de regels van http_client) mag alleen in http_client.py.
HTTPX_DIRECT: frozenset[str] = frozenset(
    {"httpx.AsyncClient", "httpx.Client"} | {f"httpx.{verb}" for verb in ("get", "post", "put", "patch", "delete", "head", "options", "request", "stream")}
)


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


def import_names(tree: ast.Module) -> dict[str, str]:
    """De namen uit de importtabel naar hun volle naam: `import subprocess as sp` geeft sp -> subprocess, `from time import
    sleep` geeft sleep -> time.sleep."""
    names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names[alias.asname or alias.name.split(".")[0]] = alias.name if alias.asname else alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for alias in node.names:
                names[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return names


def called_name(call: ast.Call, names: dict[str, str]) -> str:
    """De volle naam van wat `call` aanroept, met de aliassen uit de importtabel opgelost."""
    text: str = ast.unparse(call.func)
    head, _, rest = text.partition(".")
    full: str | None = names.get(head)
    return (f"{full}.{rest}" if rest else full) if full else text


def relative_names(tree: ast.Module) -> dict[str, str]:
    """De namen uit relatieve imports (`from . import backup`, `from .price_archive_export import write_export`, ook binnen een
    functie) naar module.naam: backup -> backup, write_export -> price_archive_export.write_export."""
    names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level:
            for alias in node.names:
                names[alias.asname or alias.name] = f"{node.module}.{alias.name}" if node.module else alias.name
    return names


def module_qualified(module: str, called: str, relative: dict[str, str]) -> str:
    """De naam van een aanroep met zijn module: een kale naam hoort bij `module`, tenzij een relatieve import hem binnenhaalt."""
    head, dot, rest = called.partition(".")
    if head in relative:
        return relative[head] + (dot + rest if dot else "")
    return called if dot else f"{module}.{called}"


def thread_only_calls(module: str, tree: ast.Module) -> list[str]:
    """De aanroepen in een `async def` van `module` van een helper die alleen in een thread hoort (THREAD_ONLY)."""
    relative: dict[str, str] = relative_names(tree)
    names: dict[str, str] = import_names(tree)
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef):
            for call in async_body_calls(node):
                qualified: str = module_qualified(module, called_name(call, names), relative)
                if qualified in THREAD_ONLY:
                    found.append(f"{module}.py:{call.lineno} in {node.name}: {qualified}")
    return found


def async_functions() -> Iterator[tuple[str, ast.AsyncFunctionDef, dict[str, str]]]:
    for name, text in modules():
        tree: ast.Module = ast.parse(text)
        names: dict[str, str] = import_names(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef):
                yield name, node, names


def test_no_module_uses_requests_threading_or_blocking_sleep() -> None:
    found: list[str] = [
        f"{name}:{number}: {code.strip()}"
        for name, text in modules()
        for number, code in code_lines(text)
        if any(pattern in code for pattern in FORBIDDEN if not (name in THREAD_ALLOWED and pattern in THREAD_PATTERNS))
    ]
    assert found == []
    # Ook als requests ergens anders in de omgeving staat: met requests onvindbaar laadt de app gewoon.
    script: str = "import sys\nsys.modules['requests'] = None\nfrom mealieshopper.app import create_app\ncreate_app()\nprint('ok')\n"
    loaded = subprocess.run([sys.executable, "-c", script], cwd=SOURCE.parent, capture_output=True, text=True, timeout=60,
                            env={**os.environ, "MEALIESHOPPER_TESTING": "1"})
    assert (loaded.returncode, loaded.stdout.strip()) == (0, "ok"), loaded.stderr[-2000:]
    assert not re.search(r"^requests\b", (SOURCE.parent / "requirements.txt").read_text(encoding="utf-8"), re.M | re.I)


def test_to_thread_only_around_cpu_work() -> None:
    used: dict[str, list[int]] = {}
    for name, text in modules():
        for number, code in code_lines(text):
            if "to_thread(" in code:
                used.setdefault(name, []).append(number)
    assert set(used) <= set(TO_THREAD_ALLOWED), {name: lines for name, lines in used.items() if name not in TO_THREAD_ALLOWED}


def test_httpx_clients_are_only_made_in_http_client() -> None:
    """Elke client komt uit `http_client.client()` of `session()`: met de User-Agent, de omgeving en de hooks van het auditlog."""
    found: list[str] = []
    for name, text in modules():
        if name == "http_client.py":
            continue
        tree: ast.Module = ast.parse(text)
        names: dict[str, str] = import_names(tree)
        found += [f"{name}:{node.lineno}: {called_name(node, names)}" for node in ast.walk(tree) if isinstance(node, ast.Call) and called_name(node, names) in HTTPX_DIRECT]
    assert found == []


def test_sqlite3_connect_only_in_the_backup_thread() -> None:
    found: set[str] = {name for name, text in modules() for _, code in code_lines(text) if "sqlite3.connect(" in code}
    assert found <= SQLITE_CONNECT_ALLOWED


def test_no_async_def_blocks_the_event_loop() -> None:
    blocking: list[str] = []
    opened: list[str] = []
    thread_only: list[str] = []
    for name, function, names in async_functions():
        for call in async_body_calls(function):
            called: str = called_name(call, names)
            where: str = f"{name}:{call.lineno} in {function.name}: {called}"
            if called in BLOCKING_IN_ASYNC:
                blocking.append(where)
            if called in OPENERS or (isinstance(call.func, ast.Attribute) and call.func.attr == "open" and called not in ("http_client.open",)):
                if (name, function.name) not in OPEN_IN_ASYNC_ALLOWED:
                    opened.append(where)
    assert blocking == []
    assert opened == []
    for name, text in modules():
        thread_only += thread_only_calls(name.removesuffix(".py"), ast.parse(text))
    assert thread_only == [], "alleen via asyncio.to_thread"


def test_the_thread_only_check_knows_the_module_of_a_call() -> None:
    """Een `async def` die `price_archive.apply` zelf aanroept is fout, `organizers.apply` (recipe_bulk) niet. Een kale naam
    hoort bij de module zelf, een relatieve import wijst naar de module waar hij vandaan komt."""
    source: str = (
        "from . import backup, price_archive\n"
        "async def wrong():\n"
        "    price_archive.apply(path)\n"
        "    backup.read_file(path)\n"
        "    _check_upload(token, path)\n"
        "    from .price_archive_export import write_export\n"
        "    write_export(path)\n"
        "async def right():\n"
        "    await organizers.apply(slug)\n"
        "    await asyncio.to_thread(price_archive.apply, path)\n"
        "    def in_a_thread():\n"
        "        price_archive.apply(path)\n"
    )
    found: list[str] = thread_only_calls("price_archive", ast.parse(source))
    assert sorted(entry.split(": ", 1)[1] for entry in found) == [
        "backup.read_file", "price_archive._check_upload", "price_archive.apply", "price_archive_export.write_export",
    ]
    assert all(" in wrong: " in entry for entry in found)
    # Dezelfde kale naam in een andere module is een andere functie.
    elsewhere: list[str] = [entry.split(": ", 1)[1] for entry in thread_only_calls("recipe_bulk", ast.parse(source))]
    assert "price_archive._check_upload" not in elsewhere and "price_archive.apply" in elsewhere


@pytest.fixture
async def open_client(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[QuartClient]:
    """Een client op een app zonder inloggen, zodat /api/about zijn werk doet in plaats van een 401 te geven."""
    monkeypatch.setenv("PASSKEY_AUTH_ENABLED", "false")
    app: Quart = create_app()
    async with app.test_app() as test_app:
        yield test_app.test_client()


async def test_a_request_leaves_the_thread_count_unchanged(open_client: QuartClient) -> None:
    """Een verzoek zonder rekenwerk geeft niets aan een thread en laat geen thread achter: geen vergeten `to_thread` (dat
    zou de thread van de executor hergebruiken en het aantal gelijk laten, dus we kijken naar `run_in_executor` zelf) en
    geen aiosqlite-verbinding (een thread per verbinding) die openblijft."""
    assert (await open_client.get("/api/about")).status_code == 200  # de commit van git staat daarna vast
    loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
    before: set[threading.Thread] = set(threading.enumerate())
    with mock.patch.object(loop, "run_in_executor", wraps=loop.run_in_executor) as executor:
        for _ in range(20):
            assert (await open_client.get("/api/about")).status_code == 200
    assert executor.call_count == 0, "een verzoek zonder rekenwerk ging naar een thread"
    deadline: float = time.monotonic() + 2
    while set(threading.enumerate()) - before and time.monotonic() < deadline:
        await asyncio.sleep(0.01)  # een gesloten aiosqlite-verbinding stopt haar thread net na het sluiten
    assert set(threading.enumerate()) - before == set()


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
