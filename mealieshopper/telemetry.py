"""Meet waar een aanvraag zijn tijd laat, als `MEALIESHOPPER_TELEMETRY` aan staat. Standaard uit: dan gebeurt hier niets.

Een pagina die tien seconden laadt, zegt niet welke route traag was, en ook niet of die route op de database wachtte, zelf
rekende of achter een andere aanvraag in de rij stond. Met de vlag aan houdt de app dat per aanvraag bij:

- De duur van de hele aanvraag, gemeten om de app heen (`Middleware`), dus met inloggen, de route, het verpakken en het
  versturen erbij.
- De tijd in de database: elke bewerking van een aiosqlite-verbinding uit `auth.connect` (`watch`), met het aantal en de
  traagste. Die tijd is wachten op de thread van de verbinding, dus ook het wachten op een slot van een andere schrijver.
- Andere fasen die een module zelf aangeeft met `span`, zoals het verpakken met gzip (compression.py).
- Hoe lang de event loop stilstond, en waar (`loop_guard.LoopGuard`). De app draait in een proces met een event loop: rekent
  een route daar seconden op, dan wacht elke andere aanvraag mee. Een bewakerthread leest dan de stack van de loop, dus de
  melding noemt de functie en de regel die de loop vasthield, en de aanvragen die op dat moment liepen. Wachtte de loop zelf,
  dan noemt ze ook de andere threads die bezig waren: een daarvan hield de GIL vast.
- Met `MEALIESHOPPER_TELEMETRY_GC` ook aan: elke ronde van de garbage collector van `gc_watch.GC_SECONDS` (20 ms) of langer,
  met de generatie en de thread (`gc_watch.GcWatch`). Een volle ronde houdt de GIL vast, dus ook die houdt de loop tegen.

Waar je het ziet:

- De header `Server-Timing` op elk antwoord, die de browser toont bij Netwerk, Timing.
- Een regel in het log voor elke aanvraag van `MEALIESHOPPER_TELEMETRY_SLOW_MS` milliseconden of langer (standaard 500), en
  voor elke keer dat de event loop `STALL_SECONDS` (100 ms) of langer stilstond.
- `GET /api/telemetry` (alleen de beheerder, `auth.ADMIN_PATHS`): de cijfers per route sinds de start, de laatste trage
  aanvragen, de laatste keren dat de event loop stilstond en de laatste lange rondes van de GC.

Alles staat in het geheugen en begint bij een herstart opnieuw. Van een adres bewaart de app het pad en alleen de parameters
uit `SAFE_PARAMS`, van een query alleen de tekst zonder de waarden: de app geeft waarden altijd apart mee (`?`).
"""

from __future__ import annotations

import asyncio
import gc
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable, Generator, MutableMapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from os import environ
from typing import Any
from urllib.parse import parse_qsl, urlencode

from quart import Quart, jsonify, request

from mealieshopper import gc_watch, loop_guard

ON: frozenset[str] = frozenset({"1", "true", "yes", "on"})
DEFAULT_SLOW_MS: int = 500
STALL_SECONDS: float = 0.1  # de grens van het project: de loop blokkeert nooit langer dan 100 ms
STALL_FRAMES: int = 6  # zoveel regels van de stack noemt een melding
RECENT: int = 200  # zoveel duren per route voor de mediaan en het 95e percentiel
MAX_ROUTES: int = 300
KEPT_SLOW: int = 100
KEPT_STALLS: int = 100
KEPT_FINISHED: int = 50  # zoveel pas afgeronde aanvragen onthoudt een melding van een stilstand
SQL_CHARS: int = 200
SAFE_PARAMS: frozenset[str] = frozenset({"store", "week", "limit", "bought"})
OTHER_ROUTES: str = "(overige routes)"

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

logger: logging.Logger = logging.getLogger("mealieshopper.telemetry")


def enabled() -> bool:
    return environ.get("MEALIESHOPPER_TELEMETRY", "false").strip().lower() in ON


def gc_enabled() -> bool:
    """Of de telemetrie ook de rondes van de GC meet. Alleen samen met `MEALIESHOPPER_TELEMETRY`."""
    return environ.get("MEALIESHOPPER_TELEMETRY_GC", "false").strip().lower() in ON


def slow_ms() -> int:
    """Vanaf hoeveel milliseconden een aanvraag in het log komt. `0` logt elke aanvraag."""
    try:
        return max(
            0,
            int(environ.get("MEALIESHOPPER_TELEMETRY_SLOW_MS", "").strip() or DEFAULT_SLOW_MS),
        )
    except ValueError:
        return DEFAULT_SLOW_MS


def _ms(seconds: float) -> float:
    return round(seconds * 1000, 1)


def _safe_target(path: str, query_string: bytes) -> str:
    """Het pad met alleen de parameters uit `SAFE_PARAMS`: een zoekterm of een code uit een inlog hoort niet in het log."""
    kept: list[tuple[str, str]] = [
        (key, value) for key, value in parse_qsl(query_string.decode("latin-1"), keep_blank_values=True) if key in SAFE_PARAMS
    ]
    return f"{path}?{urlencode(kept)}" if kept else path


@dataclass
class Recording:
    """Wat er over een aanvraag gemeten is. `phases` in seconden, `counts` het aantal keren per fase."""

    method: str
    target: str
    started: float = field(default_factory=time.perf_counter)
    at: float = field(default_factory=time.time)
    route: str = ""
    status: int = 0
    size: int = 0
    phases: dict[str, float] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    connections: int = 0
    slowest_sql: str = ""
    slowest_sql_seconds: float = 0.0
    seconds: float = 0.0

    def add(self, name: str, seconds: float) -> None:
        self.phases[name] = self.phases.get(name, 0.0) + seconds
        self.counts[name] = self.counts.get(name, 0) + 1

    def add_query(self, seconds: float, label: str) -> None:
        self.add("db", seconds)
        if seconds > self.slowest_sql_seconds:
            self.slowest_sql_seconds = seconds
            self.slowest_sql = label

    def server_timing(self) -> str:
        """De waarde van `Server-Timing`: de tijd tot nu toe (tot de eerste byte) en elke fase."""
        parts: list[str] = [f"total;dur={_ms(time.perf_counter() - self.started)}"]
        for name, seconds in self.phases.items():
            parts.append(f'{name};dur={_ms(seconds)};desc="{self.counts[name]}x"')
        return ", ".join(parts)

    def as_dict(self) -> dict[str, Any]:
        return {
            "at": datetime.fromtimestamp(self.at, UTC).isoformat(timespec="seconds"),
            "method": self.method,
            "target": self.target,
            "route": self.route,
            "status": self.status,
            "ms": _ms(self.seconds),
            "bytes": self.size,
            "phases": {name: {"ms": _ms(seconds), "count": self.counts[name]} for name, seconds in self.phases.items()},
            "connections": self.connections,
            "slowestQuery": {
                "ms": _ms(self.slowest_sql_seconds),
                "sql": self.slowest_sql,
            }
            if self.slowest_sql
            else None,
        }

    def log_line(self) -> str:
        phases: str = ", ".join(f"{name} {round(seconds * 1000)} ms in {self.counts[name]}x" for name, seconds in self.phases.items())
        line: str = (
            f"{self.method} {self.target} {self.status} in {round(self.seconds * 1000)} ms ({phases or 'geen fasen gemeten'}, {self.size} bytes)"
        )
        if self.slowest_sql:
            line += f". Traagste query {round(self.slowest_sql_seconds * 1000)} ms: {self.slowest_sql}"
        return line


_current: ContextVar[Recording | None] = ContextVar("mealieshopper_telemetry", default=None)


def current() -> Recording | None:
    """De meting van de aanvraag die nu loopt, of None: de vlag staat uit, of dit is werk op de achtergrond."""
    return _current.get()


@contextmanager
def span(name: str) -> Generator[None]:
    """Telt de tijd van dit blok bij fase `name` van de lopende aanvraag. Zonder lopende meting doet het niets."""
    recording: Recording | None = _current.get()
    if recording is None:
        yield
        return
    started: float = time.perf_counter()
    try:
        yield
    finally:
        recording.add(name, time.perf_counter() - started)


def _query_label(function: Callable[..., Any], args: tuple[Any, ...]) -> str:
    """De tekst van de query (zonder waarden), of de naam van de bewerking als er geen query is (`commit`, `close`)."""
    if args and isinstance(args[0], str):
        return " ".join(args[0].split())[:SQL_CHARS]
    return str(getattr(function, "__name__", "") or type(function).__name__)


def watch(conn: Any, opened: float | None = None) -> None:
    """Meet elke bewerking van deze aiosqlite-verbinding voor de lopende aanvraag. `opened` is het moment (`perf_counter`)
    waarop het openen begon: die tijd telt mee als de eerste bewerking.

    aiosqlite stuurt elke bewerking, ook die van een cursor, door `Connection._execute` naar de thread van de verbinding.
    Die ene plek wordt hier per verbinding vervangen. `tests/test_telemetry.py` faalt als een nieuwe aiosqlite dat anders doet.
    """
    recording: Recording | None = _current.get()
    if recording is None:
        return
    recording.connections += 1
    if opened is not None:
        recording.add_query(time.perf_counter() - opened, "connect")
    inner: Callable[..., Awaitable[Any]] = conn._execute

    async def timed(function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        started: float = time.perf_counter()
        try:
            return await inner(function, *args, **kwargs)
        finally:
            recording.add_query(time.perf_counter() - started, _query_label(function, args))

    conn._execute = timed


def watch_each(conn: Any, opened: float | None = None) -> None:
    """Zoals `watch`, voor een verbinding die openblijft (readers.py): het openen telt voor de aanvraag van nu, en elke bewerking
    voor de aanvraag die op dat moment loopt, niet voor de aanvraag die de verbinding opende."""
    recording: Recording | None = _current.get()
    if recording is not None:
        recording.connections += 1
        if opened is not None:
            recording.add_query(time.perf_counter() - opened, "connect")
    inner: Callable[..., Awaitable[Any]] = conn._execute

    async def timed(function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        current: Recording | None = _current.get()
        if current is None:
            return await inner(function, *args, **kwargs)
        started: float = time.perf_counter()
        try:
            return await inner(function, *args, **kwargs)
        finally:
            current.add_query(time.perf_counter() - started, _query_label(function, args))

    conn._execute = timed


@dataclass
class RouteStats:
    count: int = 0
    seconds: float = 0.0
    longest: float = 0.0
    db_seconds: float = 0.0
    queries: int = 0
    recent: deque[float] = field(default_factory=lambda: deque(maxlen=RECENT))

    def add(self, recording: Recording) -> None:
        self.count += 1
        self.seconds += recording.seconds
        self.longest = max(self.longest, recording.seconds)
        self.db_seconds += recording.phases.get("db", 0.0)
        self.queries += recording.counts.get("db", 0)
        self.recent.append(recording.seconds)

    def percentile(self, fraction: float) -> float:
        ordered: list[float] = sorted(self.recent)
        return ordered[min(len(ordered) - 1, int(len(ordered) * fraction))] if ordered else 0.0

    def as_dict(self, route: str) -> dict[str, Any]:
        return {
            "route": route,
            "count": self.count,
            "totalMs": _ms(self.seconds),
            "meanMs": _ms(self.seconds / self.count),
            "p50Ms": _ms(self.percentile(0.5)),
            "p95Ms": _ms(self.percentile(0.95)),
            "maxMs": _ms(self.longest),
            "dbMeanMs": _ms(self.db_seconds / self.count),
            "queriesMean": round(self.queries / self.count, 1),
        }


class Telemetry:
    """De cijfers van een app sinds de start: per route, de laatste trage aanvragen en de stilstand van de event loop."""

    def __init__(self, slow_seconds: float) -> None:
        self.slow_seconds: float = slow_seconds
        self.since: float = time.time()
        self.routes: dict[str, RouteStats] = {}
        self.slow: deque[Recording] = deque(maxlen=KEPT_SLOW)
        self.stalls: deque[dict[str, Any]] = deque(maxlen=KEPT_STALLS)
        self.collections: deque[dict[str, Any]] = deque(maxlen=gc_watch.KEPT)
        self.loop: asyncio.AbstractEventLoop | None = None  # waar `collected` het loggen heen stuurt
        self.active: dict[int, Recording] = {}
        self.finished: deque[tuple[float, str]] = deque(maxlen=KEPT_FINISHED)

    def finish(self, recording: Recording) -> None:
        recording.seconds = time.perf_counter() - recording.started
        self.finished.append((recording.started + recording.seconds, f"{recording.method} {recording.target}"))
        key: str = f"{recording.method} {recording.route or '(geen route)'}"
        if key not in self.routes and len(self.routes) >= MAX_ROUTES:
            key = OTHER_ROUTES
        self.routes.setdefault(key, RouteStats()).add(recording)
        if recording.seconds >= self.slow_seconds:
            self.slow.append(recording)
            logger.warning("Trage aanvraag: %s", recording.log_line())

    def stalled(self, stall: loop_guard.Stall) -> None:
        """De event loop stond stil: bewaar en meld het, met de stack en de aanvragen die toen liepen.

        Loopt in de bewakerthread, niet op de loop, en pas als de loop weer aan de beurt is: de aanvraag die blokkeerde is dan
        vaak al klaar. Daarom telt ook een aanvraag mee die tijdens de stilstand eindigde. Alles wordt als kopie gelezen.
        """
        since: float = time.perf_counter() - stall.seconds
        labels: list[str] = [f"{recording.method} {recording.target}" for recording in list(self.active.values())]
        labels += [label for end, label in list(self.finished) if end >= since]
        targets: list[str] = list(dict.fromkeys(labels))
        frames: tuple[loop_guard.Frame, ...] = stall.app_frames or stall.stack
        self.stalls.append(
            {
                "at": datetime.now(UTC).isoformat(timespec="seconds"),
                "ms": _ms(stall.seconds),
                "active": targets,
                "stack": [frame.short() for frame in reversed(frames[-STALL_FRAMES:])],
                # wachtte de loop zelf, dan de andere threads die bezig waren: een daarvan hield de GIL vast
                "threads": [thread.short() for thread in stall.busy_threads],
            }
        )
        logger.warning(
            "De event loop stond %d ms stil tijdens: %s. Stack: %s",
            round(stall.seconds * 1000),
            ", ".join(targets) or "werk op de achtergrond",
            stall.describe(STALL_FRAMES),
        )

    def collected(self, collection: gc_watch.Collection) -> None:
        """Een lange ronde van de GC (`gc_watch.GcWatch`): bewaar hem, en meld hem als hij de loop had kunnen tegenhouden.

        Loopt in de callback van de GC: alleen een append, en het loggen gaat naar de loop (`call_soon_threadsafe`). Een handler
        van het log neemt een slot, en dat hoort niet midden in de GC."""
        self.collections.append({"at": datetime.now(UTC).isoformat(timespec="seconds"), **collection.as_dict()})
        loop: asyncio.AbstractEventLoop | None = self.loop
        if collection.seconds >= STALL_SECONDS and loop is not None:
            try:
                loop.call_soon_threadsafe(self._log_collection, collection)
            except RuntimeError:  # de loop is dicht
                pass

    def _log_collection(self, collection: gc_watch.Collection) -> None:
        logger.warning("Een ronde van de garbage collector hield de GIL vast: %s", collection.describe())

    def gc_counts(self) -> dict[str, Any]:
        """Voor `snapshot`: de lange rondes, en hoeveel objecten bevroren zijn. De app bevriest niets (gc_watch.py), dus `frozen`
        blijft wat Python zelf bij het starten meegeeft: 375 bij 3.12 (ook een kale `python -I -S`), 0 bij 3.15. Stijgt het, dan
        riep een bibliotheek `gc.freeze()` aan en volgt de GC die objecten niet meer.

        `counters` zijn de tellers van `gc.get_count()`, geen aantallen objecten: voor generatie 0 de objecten erbij sinds de
        laatste ronde, voor 1 en 2 het aantal rondes van de generatie eronder. `gc.get_freeze_count()` loopt de bevroren lijst af
        (2,5 ms bij 600.000 objecten, gemeten): goed voor deze pagina van de beheerder, niet voor elke aanvraag."""
        return {
            "collections": list(reversed(self.collections)),
            "frozen": gc.get_freeze_count(),
            "counters": list(gc.get_count()),
        }

    def snapshot(self) -> dict[str, Any]:
        routes: list[tuple[str, RouteStats]] = sorted(self.routes.items(), key=lambda item: item[1].seconds, reverse=True)
        return {
            "since": datetime.fromtimestamp(self.since, UTC).isoformat(timespec="seconds"),
            "slowMs": round(self.slow_seconds * 1000),
            "stallMs": round(STALL_SECONDS * 1000),
            "routes": [stats.as_dict(route) for route, stats in routes],
            "slow": [recording.as_dict() for recording in reversed(self.slow)],
            "stalls": list(reversed(self.stalls)),
            "gc": self.gc_counts(),
        }


class Middleware:
    """Meet elke HTTP-aanvraag om de app heen en zet `Server-Timing` op het antwoord."""

    def __init__(self, app: ASGIApp, telemetry: Telemetry) -> None:
        self.app: ASGIApp = app
        self.telemetry: Telemetry = telemetry

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        recording: Recording = Recording(
            method=str(scope.get("method") or ""),
            target=_safe_target(str(scope.get("path") or ""), scope.get("query_string") or b""),
        )

        async def timed_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                recording.status = int(message.get("status") or 0)
                message["headers"] = [
                    *message.get("headers", []),
                    (b"server-timing", recording.server_timing().encode("latin-1")),
                ]
            elif message["type"] == "http.response.body":
                recording.size += len(message.get("body") or b"")
            await send(message)

        token = _current.set(recording)
        self.telemetry.active[id(recording)] = recording
        try:
            await self.app(scope, receive, timed_send)
        finally:
            _current.reset(token)
            self.telemetry.active.pop(id(recording), None)
            self.telemetry.finish(recording)


def register(app: Quart) -> Telemetry | None:
    """Hangt de meting om `app` als de vlag aan staat, en geeft de cijfers van deze app. Staat de vlag uit: None, en niets verandert.

    Roep dit aan na de andere middleware (de meting hoort buitenom) en voor de eerste `before_request` van de app, zodat de
    route ook bekend is van een aanvraag die het inloggen tegenhoudt.
    """
    if not enabled():
        return None
    telemetry: Telemetry = Telemetry(slow_ms() / 1000)
    app.asgi_app = Middleware(app.asgi_app, telemetry)  # type: ignore[method-assign]
    app.extensions["mealieshopper_telemetry"] = telemetry
    guard: list[loop_guard.LoopGuard] = []
    collections: gc_watch.GcWatch | None = gc_watch.GcWatch(on_collection=telemetry.collected) if gc_enabled() else None

    @app.before_request
    async def _name_route() -> None:
        recording: Recording | None = _current.get()
        if recording is not None and request.url_rule is not None:
            recording.route = request.url_rule.rule

    @app.before_serving
    async def _start_watching() -> None:
        watcher: loop_guard.LoopGuard = loop_guard.LoopGuard(asyncio.get_running_loop(), STALL_SECONDS, telemetry.stalled)
        watcher.start()  # op de loop zelf, zodat de bewaker de thread van de loop kent
        guard.append(watcher)
        if collections is not None:
            telemetry.loop = asyncio.get_running_loop()
            collections.start()

    @app.after_serving
    async def _stop_watching() -> None:
        # `stop` wacht op de thread van de bewaker (`Thread.join`, hooguit `loop_guard.JOIN_TIMEOUT`): niet op de loop.
        for watcher in guard:
            await asyncio.to_thread(watcher.stop)
        guard.clear()
        if collections is not None:
            collections.stop()

    @app.get("/api/telemetry")
    async def telemetry_snapshot():
        return jsonify(telemetry.snapshot())

    return telemetry
