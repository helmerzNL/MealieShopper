"""Meet MealieShopper op verschillende Python-varianten, elk in een eigen Docker-image, op een kopie van echte gegevens.

    python scripts/benchmark.py run --data ./data/prod --out bench.json     # bouwt, meet en ruimt op
    python scripts/benchmark.py report bench.json                           # de tabel in Markdown

`--data` is een map met `auth.sqlite3` en `secret.key` (een kopie, nooit de map van een draaiende installatie). De kopie gaat
als volume naar Docker en dat volume verdwijnt na de meting. Elke container draait met `--network none`: er gaat geen enkel
verzoek naar een winkel of naar Mealie. Met `DOCKER_HOST=ssh://server` meet je op de server terwijl het script lokaal draait.

Wat er per variant gemeten wordt:

- `inproc`: het rekenwerk zonder server ertussen (het prijsoordeel bij de aanbiedingen, de prijsgeschiedenis), de importtijd
  en de opstarttijd van hypercorn tot de eerste pagina.
- `http`: de antwoordtijd van een paar zware routes, een voor een en met acht aanvragen tegelijk. De client draait altijd in
  het image van de eerste variant, zodat alleen de server verschilt.
- het piekgeheugen van de server na die aanvragen en de duur en uitslag van de testsuite.

De subcommando's `inproc`, `token` en `http` draaien in de containers en zijn niet bedoeld om zelf aan te roepen.
"""

from __future__ import annotations

import argparse
import asyncio
import http.client
import json
import os
import re
import statistics
import subprocess
import sys
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

ROOT: Path = Path(__file__).resolve().parent.parent
sys.path.insert(
    0, str(ROOT)
)  # `python scripts/benchmark.py` zet alleen scripts/ op het pad, de app staat een map hoger
IMAGE: str = "mealieshopper-bench"
SEED_VOLUME: str = "mealieshopper-bench-seed"
SERVER_NAME: str = "mealieshopper-bench-server"
PORT: int = 8000
# De server en de client elk op eigen kernen, zodat ze elkaar en de rest van de machine zo min mogelijk in de weg zitten.
SERVER_CPUS: str = "2,3"
CLIENT_CPUS: str = "4,5"
# Geen dagelijkse aanbiedingen-run en geen updatecheck tijdens de meting.
APP_ENV: tuple[str, ...] = (
    "MEALIESHOPPER_OFFERS_SCHEDULE=false",
    "MEALIESHOPPER_UPDATE_CHECK=false",
)
PATHS: tuple[str, ...] = (
    "/healthz",
    "/",
    "/api/offers?store=ah",
    "/api/offers/history?limit=50",
    "/api/prices/history?limit=50",
    "/api/prices/history?bought=1&limit=50",
)
# Onder belasting telt de route van zes seconden niet mee: die zou de hele meting bepalen.
CONCURRENT_PATHS: tuple[str, ...] = tuple(path for path in PATHS if not path.startswith("/api/offers/history"))
CONCURRENCY: int = 8
SLOW_BUDGET: float = 20.0


@dataclass(frozen=True)
class Candidate:
    name: str
    dockerfile: str  # het bestand in scripts/benchmark/
    build_arg: str
    env: tuple[str, ...] = ()
    image: str = ""  # de naam van een andere variant: zelfde image, andere omgeving

    @property
    def tag(self) -> str:
        return f"{IMAGE}:{self.image or self.name}"


CANDIDATES: tuple[Candidate, ...] = (
    Candidate("py312", "official.Dockerfile", "BASE=python:3.12-slim"),
    Candidate("py314", "official.Dockerfile", "BASE=python:3.14-slim"),
    Candidate("py315", "official.Dockerfile", "BASE=python:3.15-slim"),
    Candidate("uv312", "uv.Dockerfile", "PYTHON=3.12"),
    Candidate("uv315", "uv.Dockerfile", "PYTHON=3.15"),
    Candidate(
        "uv315-jit",
        "uv.Dockerfile",
        "PYTHON=3.15",
        env=("PYTHON_JIT=1",),
        image="uv315",
    ),
    Candidate("uv315t", "uv.Dockerfile", "PYTHON=3.15t"),
    Candidate(
        "uv315t-nogil",
        "uv.Dockerfile",
        "PYTHON=3.15t",
        env=("PYTHON_GIL=0",),
        image="uv315t",
    ),
)


# --- in de container: rekenwerk zonder server -----------------------------------------------------------------------------


def interpreter_info() -> dict[str, Any]:
    """Welke interpreter dit is. Roep dit aan na het importeren van de app: een extensie zonder free-threaded ondersteuning
    zet de GIL bij het importeren weer aan."""
    jit: Any = getattr(sys, "_jit", None)
    gil_enabled: Callable[[], bool] | None = getattr(sys, "_is_gil_enabled", None)
    return {
        "version": sys.version.split()[0],
        "build": sys.version,
        "gil": gil_enabled() if gil_enabled else True,
        "jit_available": bool(jit and jit.is_available()),
        "jit_enabled": bool(jit and jit.is_enabled()),
    }


async def _timed(action: Callable[[], Awaitable[Any]], rounds: int) -> list[float]:
    samples: list[float] = []
    for _ in range(rounds):
        started: float = time.perf_counter()
        await action()
        samples.append(time.perf_counter() - started)
    return samples


async def _inproc_tasks(rounds: int) -> dict[str, list[float]]:
    """Het rekenwerk achter de zwaarste routes, rechtstreeks aangeroepen. De eerste ronde is koud en telt mee in de lijst."""
    from mealieshopper import auth, offers, prices

    await auth.init_db()
    today: str = date.today().isoformat()

    async def verdicts(store: str) -> None:
        data: dict[str, Any] = await offers.load(store, today)
        await prices.annotate(data["store"], data["offers"])
        json.dumps(data, default=str)

    async def all_verdicts() -> None:
        for store in offers.STORES:
            await verdicts(store)

    async def price_history(bought: bool) -> None:
        json.dumps(await prices.history(None, "", 200, bought=bought), default=str)

    async def offers_history() -> None:
        json.dumps(await offers.history(None, "", 200), default=str)

    return {
        "verdicts_ah": await _timed(lambda: verdicts("ah"), rounds),
        "verdicts_all_stores": await _timed(all_verdicts, rounds),
        "price_history": await _timed(lambda: price_history(False), rounds),
        "price_history_bought": await _timed(lambda: price_history(True), rounds),
        "offers_history": await _timed(offers_history, rounds),
    }


def _import_times(rounds: int) -> list[float]:
    """Hoe lang `import run` duurt in een verse interpreter: het laden en compileren van de hele app."""
    samples: list[float] = []
    for _ in range(rounds):
        started: float = time.perf_counter()
        subprocess.run(
            [sys.executable, "-c", "import run"],
            check=True,
            cwd=ROOT,
            env={**os.environ, "MEALIESHOPPER_TESTING": "1"},
        )
        samples.append(time.perf_counter() - started)
    return samples


def _get(
    connection: http.client.HTTPConnection, path: str, token: str = ""
) -> tuple[int, int]:
    """Een GET over een bestaande verbinding: de status en het aantal bytes."""
    connection.request(
        "GET", path, headers={"Authorization": f"Bearer {token}"} if token else {}
    )
    response: http.client.HTTPResponse = connection.getresponse()
    return response.status, len(response.read())


def wait_until_ready(host: str, port: int, timeout: float) -> float:
    """Wacht tot `/healthz` een 200 geeft en geeft terug hoe lang dat duurde."""
    started: float = time.perf_counter()
    while time.perf_counter() - started < timeout:
        try:
            connection: http.client.HTTPConnection = http.client.HTTPConnection(
                host, port, timeout=5
            )
            status, _ = _get(connection, "/healthz")
            connection.close()
            if status == 200:
                return time.perf_counter() - started
        except OSError:
            pass
        time.sleep(0.005)
    raise TimeoutError(
        f"De server op {host}:{port} was na {timeout:.0f} seconden nog niet klaar"
    )


def _startup_times(rounds: int) -> list[float]:
    """Van het starten van hypercorn tot de eerste pagina, zoals het commando in de Dockerfile het doet."""
    samples: list[float] = []
    command: list[str] = [
        "hypercorn",
        "--bind",
        f"127.0.0.1:{PORT + 1}",
        "--workers",
        "0",
        "run:app",
    ]
    for _ in range(rounds):
        server: subprocess.Popen[bytes] = subprocess.Popen(
            command, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        try:
            samples.append(wait_until_ready("127.0.0.1", PORT + 1, 120))
        finally:
            server.terminate()
            server.wait(30)
    return samples


def command_inproc(args: argparse.Namespace) -> None:
    result: dict[str, Any] = {"tasks": asyncio.run(_inproc_tasks(args.rounds))}
    result["info"] = interpreter_info()
    result["tasks"]["import"] = _import_times(args.rounds)
    result["tasks"]["startup"] = _startup_times(args.rounds)
    print(json.dumps(result))


async def _token() -> str:
    from mealieshopper import auth

    await auth.init_db()
    async with auth.connect() as conn:
        row: Any = await auth.fetchone(
            conn, "SELECT username FROM users WHERE id=?", (auth.OWNER_ID,)
        )
    if row is None:
        raise SystemExit(
            "De kopie heeft geen beheerder: zonder gebruiker is er geen sessie om mee te meten"
        )
    return await auth.create_token(auth.OWNER_ID, str(row["username"]))


def command_token(_: argparse.Namespace) -> None:
    print(asyncio.run(_token()))


# --- in de container: de client -------------------------------------------------------------------------------------------


def _sequential(host: str, port: int, token: str, path: str, count: int) -> list[float]:
    """De antwoordtijden van `count` aanvragen na elkaar. Een trage route stopt na `SLOW_BUDGET` seconden, met minstens vijf."""
    connection: http.client.HTTPConnection = http.client.HTTPConnection(host, port, timeout=120)
    samples: list[float] = []
    budget_started: float = time.perf_counter()
    for index in range(count + 3):  # de eerste drie warmen op en tellen niet mee
        started: float = time.perf_counter()
        status, _ = _get(connection, path, token)
        if status != 200:
            raise SystemExit(f"{path} gaf {status}: deze route kan niet gemeten worden")
        if index >= 3:
            samples.append(time.perf_counter() - started)
        if len(samples) >= 5 and time.perf_counter() - budget_started > SLOW_BUDGET:
            break
    connection.close()
    return samples


def _settle(host: str, port: int, timeout: float) -> None:
    """Wacht tot de server niets meer op de achtergrond rekent: bij de start warmt hij de aanbiedingen van elke winkel op, en
    zolang dat loopt wacht elke aanvraag daarop. Klaar is veertig keer achter elkaar een `/healthz` binnen 20 ms."""
    connection: http.client.HTTPConnection = http.client.HTTPConnection(host, port, timeout=120)
    started: float = time.perf_counter()
    quick: int = 0
    while quick < 40:
        if time.perf_counter() - started > timeout:
            raise SystemExit(f"De server was na {timeout:.0f} seconden nog bezig met opwarmen")
        asked: float = time.perf_counter()
        _get(connection, "/healthz")
        quick = quick + 1 if time.perf_counter() - asked < 0.02 else 0
        time.sleep(0.05)
    connection.close()


def _concurrent(host: str, port: int, token: str, count: int) -> float:
    """Acht clients die samen `count` keer alle routes opvragen: het aantal aanvragen per seconde."""
    failures: list[str] = []

    def worker() -> None:
        connection: http.client.HTTPConnection = http.client.HTTPConnection(
            host, port, timeout=120
        )
        for _ in range(count):
            for path in CONCURRENT_PATHS:
                status, _ = _get(connection, path, token)
                if status != 200:
                    failures.append(f"{path}: {status}")
        connection.close()

    workers: list[threading.Thread] = [
        threading.Thread(target=worker) for _ in range(CONCURRENCY)
    ]
    started: float = time.perf_counter()
    for thread in workers:
        thread.start()
    for thread in workers:
        thread.join()
    elapsed: float = time.perf_counter() - started
    if failures:
        raise SystemExit(f"Aanvragen mislukt onder belasting: {sorted(set(failures))}")
    return CONCURRENCY * count * len(CONCURRENT_PATHS) / elapsed


def command_http(args: argparse.Namespace) -> None:
    host: str = "127.0.0.1"
    wait_until_ready(host, PORT, 180)
    connection: http.client.HTTPConnection = http.client.HTTPConnection(
        host, PORT, timeout=300
    )
    started: float = time.perf_counter()
    status, _ = _get(connection, "/api/offers?store=ah", args.token)
    first_offers: float = time.perf_counter() - started
    connection.close()
    if status != 200:
        raise SystemExit(f"/api/offers gaf {status}")
    result: dict[str, Any] = {"first_offers": [first_offers]}
    _settle(host, PORT, 300)
    for path in PATHS:
        result[path] = _sequential(host, PORT, args.token, path, args.requests)
    result["requests_per_second"] = [
        _concurrent(host, PORT, args.token, max(1, args.requests // 5))
    ]
    print(json.dumps(result))


# --- op de host: bouwen, meten, opruimen ----------------------------------------------------------------------------------


def docker(
    *args: str, stdin: bytes | None = None, check: bool = True
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["docker", *args], input=stdin, capture_output=True, check=check
    )


def _last_json(output: bytes) -> dict[str, Any]:
    """De laatste regel van de uitvoer is het resultaat, wat ervoor staat zijn waarschuwingen van de interpreter."""
    return json.loads(output.decode().strip().splitlines()[-1])


def build(candidate: Candidate) -> str:
    """Bouwt het image en geeft een lege tekst terug, of het einde van de foutmelding als de build mislukt."""
    built: subprocess.CompletedProcess[bytes] = docker(
        "build",
        "-q",
        "-f",
        str(ROOT / "scripts" / "benchmark" / candidate.dockerfile),
        "--build-arg",
        candidate.build_arg,
        "-t",
        candidate.tag,
        str(ROOT),
        check=False,
    )
    return "" if built.returncode == 0 else built.stderr.decode()[-1500:]


def seed(data: Path) -> None:
    """Zet `auth.sqlite3` en `secret.key` uit `data` in een Docker-volume, ook als Docker op een andere machine draait."""
    for name in ("auth.sqlite3", "secret.key"):
        if not (data / name).is_file():
            raise SystemExit(f"{data / name} ontbreekt")
    docker("volume", "rm", "-f", SEED_VOLUME)
    docker("volume", "create", SEED_VOLUME)
    packed: bytes = subprocess.run(
        ["tar", "-c", "-C", str(data), "auth.sqlite3", "secret.key"],
        capture_output=True,
        check=True,
    ).stdout
    docker(
        "run",
        "--rm",
        "-i",
        "-v",
        f"{SEED_VOLUME}:/seed",
        CANDIDATES[0].tag,
        "tar",
        "-x",
        "-C",
        "/seed",
        stdin=packed,
    )


def _run_args(candidate: Candidate, cpus: str) -> list[str]:
    """De opties van elke meetcontainer: geen netwerk, vaste kernen, de kopie alleen-lezen en `/data` in het geheugen."""
    env: list[str] = [
        part for pair in (*APP_ENV, *candidate.env) for part in ("-e", pair)
    ]
    return [
        "--network",
        "none",
        "--cpuset-cpus",
        cpus,
        "-v",
        f"{SEED_VOLUME}:/seed:ro",
        "--tmpfs",
        "/data",
        *env,
    ]


def measure_inproc(candidate: Candidate, rounds: int) -> dict[str, Any]:
    script: str = (
        f"cp -a /seed/. /data/ && python scripts/benchmark.py inproc --rounds {rounds}"
    )
    return _last_json(
        docker(
            "run",
            "--rm",
            *_run_args(candidate, SERVER_CPUS),
            candidate.tag,
            "sh",
            "-c",
            script,
        ).stdout
    )


def measure_http(candidate: Candidate, requests: int) -> dict[str, Any]:
    """Start de server zoals de Dockerfile dat doet en laat de client van de eerste variant de routes opvragen."""
    docker("rm", "-f", SERVER_NAME, check=False)
    serve: str = f"cp -a /seed/. /data/ && exec hypercorn --bind 0.0.0.0:{PORT} --workers 0 run:app"
    docker(
        "run",
        "-d",
        "--name",
        SERVER_NAME,
        *_run_args(candidate, SERVER_CPUS),
        candidate.tag,
        "sh",
        "-c",
        serve,
    )
    try:
        client: list[str] = [
            "run",
            "--rm",
            "--network",
            f"container:{SERVER_NAME}",
            "--cpuset-cpus",
            CLIENT_CPUS,
            CANDIDATES[0].tag,
        ]
        # Pas als de server antwoordt staat de kopie in /data en kan het token gemaakt worden.
        docker(*client, "python", "scripts/benchmark.py", "wait")
        token: str = (
            docker("exec", SERVER_NAME, "python", "scripts/benchmark.py", "token")
            .stdout.decode()
            .strip()
            .splitlines()[-1]
        )
        result: dict[str, Any] = _last_json(
            docker(
                *client,
                "python",
                "scripts/benchmark.py",
                "http",
                "--token",
                token,
                "--requests",
                str(requests),
            ).stdout
        )
        status: str = docker(
            "exec", SERVER_NAME, "cat", "/proc/1/status"
        ).stdout.decode()
        peak: re.Match[str] | None = re.search(r"VmHWM:\s+(\d+) kB", status)
        if peak is not None:
            result["peak_rss_mb"] = [int(peak.group(1)) / 1024]
        return result
    finally:
        docker("rm", "-f", SERVER_NAME, check=False)


def measure_tests(candidate: Candidate) -> dict[str, Any]:
    env: list[str] = [
        part
        for pair in ("MEALIESHOPPER_TESTING=1", *candidate.env)
        for part in ("-e", pair)
    ]
    started: float = time.perf_counter()
    ran: subprocess.CompletedProcess[bytes] = docker(
        "run",
        "--rm",
        "--network",
        "none",
        "--cpuset-cpus",
        SERVER_CPUS,
        *env,
        candidate.tag,
        "python",
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        check=False,
    )
    lines: list[str] = ran.stdout.decode().strip().splitlines()
    failed: list[str] = sorted(
        line.split(" - ")[0] for line in lines if line.startswith(("FAILED ", "ERROR "))
    )
    return {
        "seconds": time.perf_counter() - started,
        "exit": ran.returncode,
        "summary": lines[-1] if lines else "",
        "failed": failed,
    }


def _merge(total: dict[str, list[float]], new: dict[str, list[float]]) -> None:
    for key, samples in new.items():
        total.setdefault(key, []).extend(samples)


def _save(results: dict[str, Any], out: Path) -> None:
    out.write_text(json.dumps(results, indent=1))


def _failure(error: subprocess.CalledProcessError) -> str:
    return (error.stderr or error.stdout or b"").decode()[-1500:] or str(error)


def command_run(args: argparse.Namespace) -> None:
    chosen: list[Candidate] = [
        c for c in CANDIDATES if not args.only or c.name in args.only
    ]
    results: dict[str, Any] = {}
    for candidate in chosen:
        print(f"bouwen: {candidate.name}", flush=True)
        results[candidate.name] = {"build_error": build(candidate), "samples": {}}
    seed(args.data)
    try:
        usable: list[Candidate] = [
            c for c in chosen if not results[c.name]["build_error"]
        ]
        # De rondes door elkaar: wat er in de tussentijd op de machine verandert, treft elke variant even hard.
        for round_number in range(1, args.rounds + 1):
            for candidate in usable:
                print(f"ronde {round_number}: {candidate.name}", flush=True)
                entry: dict[str, Any] = results[candidate.name]
                try:
                    inproc: dict[str, Any] = measure_inproc(candidate, args.task_rounds)
                    entry["info"] = inproc["info"]
                    _merge(entry["samples"], inproc["tasks"])
                    _merge(entry["samples"], measure_http(candidate, args.requests))
                except subprocess.CalledProcessError as failure:
                    entry["run_error"] = _failure(failure)
                    print(f"  mislukt: {entry['run_error'][-300:]}", flush=True)
                _save(results, args.out)
        if not args.skip_tests:
            for candidate in usable:
                print(f"tests: {candidate.name}", flush=True)
                results[candidate.name]["tests"] = measure_tests(candidate)
                _save(results, args.out)
    finally:
        docker(
            "volume", "rm", "-f", SEED_VOLUME, check=False
        )  # de kopie bevat tokens: nooit laten staan
        if not args.keep_images:
            docker("rmi", "-f", *sorted({c.tag for c in chosen}), check=False)
    _save(results, args.out)
    print(report(results))


# --- het rapport ----------------------------------------------------------------------------------------------------------

ROWS: tuple[tuple[str, str, str], ...] = (
    ("import", "`import run`", "ms"),
    ("startup", "opstarten tot de eerste pagina", "ms"),
    ("first_offers", "eerste `/api/offers` na de start", "ms"),
    ("verdicts_ah", "prijsoordeel AH", "ms"),
    ("verdicts_all_stores", "prijsoordeel alle winkels", "ms"),
    ("price_history", "prijsgeschiedenis (200)", "ms"),
    ("price_history_bought", "prijsgeschiedenis, gekocht (200)", "ms"),
    ("offers_history", "aanbiedingengeschiedenis (200)", "ms"),
    *((path, f"`GET {path}`", "ms") for path in PATHS),
    ("requests_per_second", f"aanvragen per seconde, {CONCURRENCY} tegelijk", "/s"),
    ("peak_rss_mb", "piekgeheugen server", "MB"),
)


def _cell(samples: list[float] | None, unit: str, baseline: list[float] | None) -> str:
    if not samples:
        return "-"
    scale: float = 1000 if unit == "ms" else 1
    median: float = statistics.median(samples) * scale
    text: str = f"{median:.1f}" if median < 100 else f"{median:.0f}"
    if len(samples) > 1:
        text += f" ({min(samples) * scale:.0f}-{max(samples) * scale:.0f})"
    if baseline and samples is not baseline:
        text += f" {(median / (statistics.median(baseline) * scale) - 1) * 100:+.0f}%"
    return text


def report(results: dict[str, Any]) -> str:
    """De tabel in Markdown: de mediaan, tussen haakjes de laagste en hoogste meting, en het verschil met de eerste variant."""
    names: list[str] = [name for name, entry in results.items() if entry["samples"]]
    lines: list[str] = [
        "| meting | " + " | ".join(names) + " |",
        "|---|" + "---:|" * len(names),
    ]
    for key, label, unit in ROWS:
        baseline: list[float] | None = (
            results[names[0]]["samples"].get(key) if names else None
        )
        cells: list[str] = [
            _cell(results[name]["samples"].get(key), unit, baseline) for name in names
        ]
        lines.append(f"| {label} ({unit}) | " + " | ".join(cells) + " |")
    lines.append("")
    for name, entry in results.items():
        info: dict[str, Any] = entry.get("info", {})
        tests: dict[str, Any] = entry.get("tests", {})
        facts: list[str] = [
            f"Python {info.get('version', '?')}",
            f"GIL {'aan' if info.get('gil', True) else 'uit'}",
            f"JIT {'aan' if info.get('jit_enabled') else 'uit'}",
        ]
        if tests:
            facts.append(f"tests in {tests['seconds']:.0f} s: {tests['summary']}")
        for problem in ("build_error", "run_error"):
            if entry.get(problem):
                facts.append(f"{problem}: {entry[problem].strip().splitlines()[-1]}")
        lines.append(f"- `{name}`: " + ", ".join(facts))
    return "\n".join(lines)


def command_report(args: argparse.Namespace) -> None:
    print(report(json.loads(args.results.read_text())))


def main() -> None:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description=(__doc__ or "").splitlines()[0]
    )
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser(
        "run", help="bouw de images, meet elke variant en ruim op"
    )
    run.add_argument(
        "--data",
        type=Path,
        required=True,
        help="map met een kopie van auth.sqlite3 en secret.key",
    )
    run.add_argument("--out", type=Path, default=Path("benchmark.json"))
    run.add_argument(
        "--rounds", type=int, default=3, help="hoe vaak elke variant aan de beurt komt"
    )
    run.add_argument(
        "--task-rounds",
        type=int,
        default=5,
        help="herhalingen van het rekenwerk per ronde",
    )
    run.add_argument(
        "--requests", type=int, default=30, help="aanvragen per route per ronde"
    )
    run.add_argument(
        "--only",
        nargs="*",
        help=f"alleen deze varianten ({', '.join(c.name for c in CANDIDATES)})",
    )
    run.add_argument("--skip-tests", action="store_true")
    run.add_argument("--keep-images", action="store_true")
    run.set_defaults(handler=command_run)
    shown = commands.add_parser("report", help="de tabel van een eerdere meting")
    shown.add_argument("results", type=Path)
    shown.set_defaults(handler=command_report)
    inproc = commands.add_parser("inproc")
    inproc.add_argument("--rounds", type=int, default=5)
    inproc.set_defaults(handler=command_inproc)
    commands.add_parser("token").set_defaults(handler=command_token)
    commands.add_parser("wait").set_defaults(
        handler=lambda _: wait_until_ready("127.0.0.1", PORT, 180)
    )
    client = commands.add_parser("http")
    client.add_argument("--token", required=True)
    client.add_argument("--requests", type=int, default=30)
    client.set_defaults(handler=command_http)
    args: argparse.Namespace = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
