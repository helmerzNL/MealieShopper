"""Auditlog: wat MealieShopper met Albert Heijn, Picnic en Jumbo bespreekt, en wat AI-assistenten via MCP vragen.

Zo zie je precies hoe alles werkt: welke verzoeken naar de winkels gaan, wat die antwoorden, hoe lang het
duurde en welk MCP-verzoek van welke assistent erachter zat.

* Uitgaand verkeer wordt op één plek vastgelegd, in de hooks uit `httpx_hooks()`: `http_client` hangt ze aan
  elke client. Dat dekt elk verzoek in de hele app, ook nieuwe, zonder dat die iets hoeven te weten. De dienst
  volgt uit de host (ah.nl, picnicinternational.com, jumbo.com); ander verkeer (Mealie, GitHub) blijft erbuiten.
* MCP-verzoeken (initialize, tools/list, tools/call) en de OAuth-stappen waarmee een assistent koppelt komen
  uit `mcp_http.py`. Een verzoek aan de winkels dat tijdens een MCP-tool loopt, krijgt dezelfde `corr`
  (correlatie-id) als het MCP-verzoek, zodat je bij een tool ziet welke winkelverzoeken eruit volgden.

Geheimen worden nooit bewaard: wachtwoorden, tokens, codes, cookies en e-mailadressen worden vóór het
opslaan vervangen door `[verborgen]`, in de URL, het verzoek én het antwoord. Headers bewaren we niet.
Bodies zijn ingekort en na `MEALIESHOPPER_AUDIT_RETENTION_DAYS` dagen (standaard 30) wist de app alles.
Alleen de beheerder kan de log lezen of wissen.
"""
from __future__ import annotations

import asyncio
import contextvars
import csv
import io
import json
import logging
import re
import secrets
import time
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager, contextmanager
from dataclasses import dataclass
from datetime import timedelta
from os import environ
from typing import Any, Awaitable, Callable, Iterator, Mapping, TypeAlias
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import aiosqlite
import httpx
from quart import Quart, Response, has_request_context, jsonify, request
from werkzeug.exceptions import HTTPException

# `tasks` importeert http_client, en dat importeert deze module: wie als eerste geladen wordt, ziet de ander nog half. Daarom
# gebruikt deze module `tasks` alleen binnen functies, nooit bij het laden.
from . import auth, tasks, telemetry
from .i18n import tr

log = logging.getLogger(__name__)

OFF = {"0", "false", "no", "off"}
HIDDEN = "[verborgen]"
DEFAULT_BODY_CHARS = 4000
DEFAULT_RETENTION_DAYS = 30
MAX_ROWS = 20_000
PRUNE_EVERY = 200  # opruimen na zoveel nieuwe regels
MAX_PARSE_BYTES = 1_000_000  # grotere antwoorden knippen we in zonder ze te ontleden
PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
MAX_EXPORT = 5000  # zoveel regels (de nieuwste) in één export
CSV_COLUMNS = ("id", "ts", "kind", "service", "action", "method", "url", "status", "ok", "durationMs", "actor", "corr", "error", "request", "response")

# Welke host bij welke dienst hoort.
SERVICE_DOMAINS: dict[str, tuple[str, ...]] = {
    "ah": ("ah.nl",),
    "picnic": ("picnicinternational.com",),
    "jumbo": ("jumbo.com",),
    "crisp": ("crispapp.nl",),
    # Lidl en Aldi: de aanbiedingenpagina en de productfoto's. scene7.com is het gedeelde CDN van Adobe, dus alleen de host van Aldi.
    "lidl": ("lidl.nl", "imgproxy-retcat.assets.schwarz"),
    "aldi": ("aldi.nl", "s7g10.scene7.com"),
    "hellofresh": ("hellofresh.nl",),
    "marleyspoon": ("marleyspoon.nl", "marleyspoon.com"),
}
SERVICES = tuple(SERVICE_DOMAINS) + ("mcp",)

# Sleutels waarvan de waarde nooit in de log komt (vergelijking op de genormaliseerde naam).
_SENSITIVE_KEY = re.compile(
    r"pass(?:word|wd)?|secret|token|authori[sz]ation|cookie|verifier|credential|signature|otp|session|"
    r"^code$|^key$|^pin$|^nonce$|^request$|^e?mail$|e-?mail(?:address)?|^user(?:name)?$|^login$|phone",
    re.I,
)
_TEXT_SECRET = re.compile(
    r"""(?P<key>["']?(?:pass(?:word|wd)?|secret|[\w-]*token|authori[sz]ation|cookie|code_verifier|x-picnic-auth)["']?\s*[:=]\s*)"""
    r"""(?P<value>"[^"]*"|'[^']*'|(?:(?:bearer|basic|token)[ \t]+)?[^\s&,;}]+)""",  # ook met het schema ervoor, zoals "Authorization: Bearer <token>"
    re.I,
)
_BEARER = re.compile(r"(Bearer\s+)[\w.~+/=-]+", re.I)

_TEXT_TYPES = ("json", "text", "xml", "javascript", "x-www-form-urlencoded", "graphql")

# Wie een verzoek veroorzaakt: de MCP-aanroep (corr + herkomst) die op dat moment loopt.
_corr: contextvars.ContextVar[str] = contextvars.ContextVar("audit_corr", default="")
_actor: contextvars.ContextVar[str] = contextvars.ContextVar("audit_actor", default="")
_quiet: contextvars.ContextVar[bool] = contextvars.ContextVar("audit_quiet", default=False)

_since_prune = 0

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    service TEXT NOT NULL,
    action TEXT NOT NULL,
    method TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    status INTEGER,
    ok INTEGER NOT NULL DEFAULT 1,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    actor TEXT NOT NULL DEFAULT '',
    corr TEXT NOT NULL DEFAULT '',
    request TEXT NOT NULL DEFAULT '',
    response TEXT NOT NULL DEFAULT '',
    req_bytes INTEGER,
    resp_bytes INTEGER,
    error TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS audit_log_service ON audit_log (service, id);
CREATE INDEX IF NOT EXISTS audit_log_corr ON audit_log (corr);
CREATE INDEX IF NOT EXISTS audit_log_ts ON audit_log (ts);
"""


# --- instellingen ----------------------------------------------------------------------------------

def enabled() -> bool:
    return environ.get("MEALIESHOPPER_AUDIT_LOG", "true").strip().lower() not in OFF


def bodies_enabled() -> bool:
    return environ.get("MEALIESHOPPER_AUDIT_BODIES", "true").strip().lower() not in OFF


def _int_env(name: str, default: int) -> int:
    try:
        return max(0, int(environ.get(name, "").strip() or default))
    except ValueError:
        return default


def body_chars() -> int:
    return _int_env("MEALIESHOPPER_AUDIT_BODY_CHARS", DEFAULT_BODY_CHARS)


def retention_days() -> int:
    return _int_env("MEALIESHOPPER_AUDIT_RETENTION_DAYS", DEFAULT_RETENTION_DAYS)


def settings_payload() -> dict[str, Any]:
    return {"enabled": enabled(), "bodies": bodies_enabled(), "bodyChars": body_chars(), "retentionDays": retention_days()}


# --- afschermen ------------------------------------------------------------------------------------

def _redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: (HIDDEN if isinstance(k, str) and _SENSITIVE_KEY.search(k) and v not in (None, "", [], {}) else _redact_value(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    return value


def redact_text(text: str) -> str:
    """Vrije tekst: haal waarden achter bekende geheime namen en Bearer-tokens weg."""
    text = _TEXT_SECRET.sub(lambda m: m.group("key") + (f'"{HIDDEN}"' if m.group("value")[:1] in "\"'" else HIDDEN), text)
    return _BEARER.sub(r"\1" + HIDDEN, text)


def redact_url(url: str) -> str:
    """De URL zonder fragment, met afgeschermde waarden bij geheime queryparameters."""
    parts = urlsplit(url or "")
    query = urlencode(
        [(k, HIDDEN if _SENSITIVE_KEY.search(k) else v) for k, v in parse_qsl(parts.query, keep_blank_values=True)],
        safe="[]",
    )
    netloc = parts.hostname or ""
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, query, ""))


def _limit(text: str) -> str:
    cap = body_chars()
    if len(text) <= cap:
        return text
    return text[:cap] + f"\n… ({len(text) - cap} tekens ingekort / characters cut)"


def summarize_body(raw: bytes | str | None, content_type: str = "") -> tuple[str, int]:
    """(afgeschermde, ingekorte tekst, ware grootte in bytes) van een body."""
    if raw is None or raw == b"" or raw == "":
        return "", 0
    data = raw.encode("utf-8") if isinstance(raw, str) else bytes(raw)
    size = len(data)
    if not bodies_enabled():
        return "", size
    kind = (content_type or "").lower()
    if not _is_text(kind):
        return _binary_label(kind, size), size
    if size > MAX_PARSE_BYTES:
        return _limit(redact_text(data[: body_chars() * 2].decode("utf-8", "replace"))), size
    text = data.decode("utf-8", "replace")
    try:
        if "x-www-form-urlencoded" in kind:
            parsed: Any = {k: v for k, v in parse_qsl(text, keep_blank_values=True)}
        else:
            parsed = json.loads(text)
    except ValueError:
        return _limit(redact_text(text)), size
    return _limit(json.dumps(_redact_value(parsed), ensure_ascii=False, separators=(",", ":"))), size


def _is_text(content_type: str) -> bool:
    """Of `summarize_body` de body als tekst toont. Zonder type gaan we uit van tekst."""
    kind: str = (content_type or "").lower()
    return not kind or any(marker in kind for marker in _TEXT_TYPES)


def _binary_label(content_type: str, size: int | None) -> str:
    kind: str = (content_type or "").lower().split(";")[0].strip()
    return f"[{kind}, {size} bytes]" if size is not None else f"[{kind}]"


def service_for(url: str) -> str:
    host = (urlsplit(url or "").hostname or "").lower()
    for service, domains in SERVICE_DOMAINS.items():
        if any(host == domain or host.endswith("." + domain) for domain in domains):
            return service
    return ""


# --- opslag ----------------------------------------------------------------------------------------

def _now() -> str:
    return auth.utcnow().isoformat()


_INSERT_SQL: str = (
    "INSERT INTO audit_log (ts, kind, service, action, method, url, status, ok, duration_ms, actor, corr, request, response, req_bytes, resp_bytes, error, detail) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def _row_values(
    *,
    kind: str,
    service: str,
    action: str,
    method: str,
    url: str,
    status: int | None,
    ok: bool,
    duration_ms: int,
    actor: str,
    corr: str,
    request_body: str,
    response_body: str,
    req_bytes: int | None,
    resp_bytes: int | None,
    error: str,
    detail: dict[str, Any] | None,
) -> tuple[str | int | None, ...]:
    """De waarden voor `_INSERT_SQL`, ingekort en geredigeerd. Voor `record`."""
    return (
        _now(), kind, service, action[:200], method, url[:2000], status, 1 if ok else 0, int(duration_ms), actor[:200], corr,
        request_body, response_body, req_bytes, resp_bytes, redact_text(error)[:500],
        json.dumps(detail, ensure_ascii=False) if detail else "",
    )


@asynccontextmanager
async def _connect() -> AsyncIterator[aiosqlite.Connection]:
    """Een verbinding op `auth.connect` met het schema, een keer per proces en database (`auth.ensure_schema`). Geen commit
    bij het verlaten."""
    async with auth.connect() as conn:
        await auth.ensure_schema(conn, "audit", SCHEMA)
        yield conn


async def _prune(conn: aiosqlite.Connection) -> None:
    days = retention_days()
    if days:
        await conn.execute("DELETE FROM audit_log WHERE ts < ?", ((auth.utcnow() - timedelta(days=days)).isoformat(),))
    await conn.execute("DELETE FROM audit_log WHERE id <= (SELECT MAX(id) FROM audit_log) - ?", (MAX_ROWS,))


async def record(
    *,
    kind: str,
    service: str,
    action: str,
    method: str = "",
    url: str = "",
    status: int | None = None,
    ok: bool = True,
    duration_ms: int = 0,
    actor: str = "",
    corr: str = "",
    request_body: str = "",
    response_body: str = "",
    req_bytes: int | None = None,
    resp_bytes: int | None = None,
    error: str = "",
    detail: dict[str, Any] | None = None,
) -> int | None:
    """Schrijf één regel en wacht tot hij er staat (het id, of None). Mislukt het schrijven, dan gaat de aanvraag zelf gewoon
    door. De regel gaat via de schrijver van deze event loop (`_Writer`): een verbinding voor alle regels, geen nieuwe per
    regel. Na het afsluiten (`tasks.cancel_all`, `tasks.stopped`) is er geen schrijver meer: dan schrijft `record` de regel
    zelf (`_write_direct`)."""
    if not enabled():
        return None
    values: tuple[str | int | None, ...] = _row_values(
        kind=kind, service=service, action=action, method=method, url=url, status=status, ok=ok, duration_ms=duration_ms,
        actor=actor, corr=corr, request_body=request_body, response_body=response_body, req_bytes=req_bytes,
        resp_bytes=resp_bytes, error=error, detail=detail,
    )
    try:
        if tasks.stopped():
            return await _write_direct(values)
        return await _writer().submit(values)
    except Exception:  # noqa: BLE001 - de log mag nooit een verzoek laten mislukken
        log.warning("Kon geen auditregel schrijven", exc_info=True)
        return None


async def _write_direct(values: tuple[str | int | None, ...]) -> int | None:
    """Een regel op een eigen verbinding, voor `record` na het afsluiten: dan is er geen schrijver die nog wacht."""
    async with _connect() as conn:
        cursor: aiosqlite.Cursor = await conn.execute(_INSERT_SQL, values)
        await conn.commit()
        return cursor.lastrowid


# Hoe lang de schrijver zijn verbinding openhoudt zonder nieuwe regels. Daarna sluit hij hem en stopt hij: de volgende regel
# start een nieuwe schrijver.
WRITER_IDLE_SECONDS: float = 2.0
_INSERT_LABEL: str = "INSERT audit_log"  # zo staat de regel in de telemetrie van de aanvraag (`telemetry.Recording.add_query`)


@dataclass
class _Pending:
    """Een regel die op de schrijver wacht: de database waar hij heen moet, de waarden, de uitkomst voor `record`, en de meting
    van de aanvraag die hem schreef (`telemetry.current`): daar telt de tijd van de regel bij."""

    path: str
    values: tuple[str | int | None, ...]
    done: asyncio.Future[int | None]
    recording: telemetry.Recording | None


class _Writer:
    """Schrijft de regels van `record` voor een event loop, met een verbinding zolang er regels komen.

    Elke `aiosqlite.connect` start een eigen thread, en op de event loop wacht `Thread.start` tot die thread draait. Terwijl
    een rekenthread de GIL heeft, kost dat de loop een wisselinterval per overdracht: met een verbinding per uitgaand verzoek gaf
    dat stilstanden van 131 ms (taak 9c). Nu een task die de wachtrij leegmaakt, de regels die samen klaarstaan in een transactie
    schrijft, en zijn verbinding pas na `WRITER_IDLE_SECONDS` sluit.

    De task loopt in een lege context (`contextvars.Context`): anders erft hij die van de eerste aanroeper, met zijn aanvraag en
    zijn meting, en zou `auth.connect` elke volgende regel bij die al lang klare aanvraag tellen. De tijd van een regel telt nu
    bij de aanvraag die hem schreef (`_Pending.recording`). `tasks.cancel_all` annuleert de task niet, maar laat hem leeglopen
    (`_stop_writers`): wat in de wachtrij staat, komt er nog in."""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop: asyncio.AbstractEventLoop = loop
        self.queue: asyncio.Queue[_Pending | None] = asyncio.Queue()
        self.task: asyncio.Task[None] | None = None
        self.stopping: bool = False

    def submit(self, values: tuple[str | int | None, ...]) -> asyncio.Future[int | None]:
        pending: _Pending = _Pending(str(auth.db_path()), values, self.loop.create_future(), telemetry.current())
        self.queue.put_nowait(pending)
        if self.task is None:
            self.task = self.loop.create_task(self._drain(), name="auditlog-schrijver", context=contextvars.Context())
            tasks.wait_at_shutdown(self.task)
        return pending.done

    def stop(self) -> None:
        """Laat de schrijver schrijven wat klaarstaat en dan stoppen, zonder op nieuwe regels te wachten."""
        self.stopping = True
        self.queue.put_nowait(None)  # maakt een schrijver wakker die op de eerste regel wacht

    async def _next_batch(self) -> list[_Pending]:
        """De regels die klaarstaan, na hooguit `WRITER_IDLE_SECONDS` wachten op de eerste. Leeg als er niets kwam. Ook wat
        precies bij de time-out binnenkwam, hoort erbij: dat staat dan nog in de wachtrij."""
        batch: list[_Pending | None] = []
        if self.queue.empty() and not self.stopping:
            try:
                batch.append(await asyncio.wait_for(self.queue.get(), WRITER_IDLE_SECONDS))
            except TimeoutError:
                pass
        batch += _left(self.queue)
        return [pending for pending in batch if pending is not None]

    async def _drain(self) -> None:
        stack: AsyncExitStack = AsyncExitStack()
        conn: aiosqlite.Connection | None = None
        path: str = ""
        batch: list[_Pending] = []
        try:
            while batch := await self._next_batch():
                for group in _by_path(batch):
                    if conn is None or group[0].path != path:
                        await stack.aclose()
                        conn, path = await _open(stack, group), group[0].path
                    if conn is not None and not await _write(conn, group):
                        await stack.aclose()
                        conn = None
                batch = []
        finally:
            self._retire()  # meteen, voor het sluiten: een regel van hierna gaat naar een nieuwe schrijver
            _settle([*batch, *(pending for pending in _left(self.queue) if pending is not None)])
            await stack.aclose()

    def _retire(self) -> None:
        if _WRITERS.get(self.loop) is self:
            del _WRITERS[self.loop]


async def _open(stack: AsyncExitStack, group: list[_Pending]) -> aiosqlite.Connection | None:
    """Een verbinding voor de schrijver, of None als openen mislukte: dan krijgt elke aanroeper van `group` None."""
    try:
        return await stack.enter_async_context(_connect())
    except Exception:  # de log mag nooit een verzoek laten mislukken
        log.warning("Kon geen auditregel schrijven", exc_info=True)
        _settle(group)
        return None


def _by_path(batch: list[_Pending]) -> list[list[_Pending]]:
    """De regels per database, in volgorde. Er is er vrijwel altijd een: alleen tests wisselen van map."""
    groups: list[list[_Pending]] = []
    for pending in batch:
        if groups and groups[-1][0].path == pending.path:
            groups[-1].append(pending)
        else:
            groups.append([pending])
    return groups


async def _write(conn: aiosqlite.Connection, group: list[_Pending]) -> bool:
    """Schrijft de regels in een transactie en geeft elke aanroeper zijn id, met de tijd van zijn regel in zijn meting. False
    als het mislukte: dan krijgt elke aanroeper None en opent de schrijver een nieuwe verbinding."""
    global _since_prune
    try:
        ids: list[int | None] = []
        for pending in group:
            started: float = time.perf_counter()
            cursor: aiosqlite.Cursor = await conn.execute(_INSERT_SQL, pending.values)
            if pending.recording is not None:
                pending.recording.add_query(time.perf_counter() - started, _INSERT_LABEL)
            ids.append(cursor.lastrowid)
            _since_prune += 1
        if _since_prune >= PRUNE_EVERY:
            _since_prune = 0
            await _prune(conn)
        await conn.commit()
    except Exception:  # de log mag nooit een verzoek laten mislukken
        log.warning("Kon geen auditregel schrijven", exc_info=True)
        _settle(group)
        return False
    for pending, row_id in zip(group, ids, strict=True):
        if not pending.done.done():
            pending.done.set_result(row_id)
    return True


def _left(queue: asyncio.Queue[_Pending | None]) -> list[_Pending | None]:
    found: list[_Pending | None] = []
    while not queue.empty():
        found.append(queue.get_nowait())
    return found


def _settle(pending: list[_Pending]) -> None:
    """Een aanroeper die nog wacht krijgt None: zijn regel is niet geschreven (een fout, of de schrijver werd gestopt)."""
    for item in pending:
        if not item.done.done():
            item.done.set_result(None)


# De schrijver per event loop. Hij haalt zichzelf hier weg als hij stopt (`_Writer._retire`), dus een loop blijft niet hangen.
_WRITERS: dict[asyncio.AbstractEventLoop, _Writer] = {}


def _writer() -> _Writer:
    """De schrijver van de lopende event loop. Een test heeft een eigen loop, dus een eigen schrijver."""
    loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
    found: _Writer | None = _WRITERS.get(loop)
    if found is None:
        tasks.on_cancel_all(_stop_writers)
        found = _WRITERS[loop] = _Writer(loop)
    return found


def _stop_writers() -> None:
    """Voor `tasks.cancel_all`, op de event loop: de schrijver van deze loop schrijft wat klaarstaat en stopt. Een regel van
    hierna schrijft `record` direct (`tasks.stopped`), ook als er nooit een schrijver was: dat onthoudt `cancel_all` zelf, want
    deze haak hangt pas aan `cancel_all` bij de eerste schrijver. `cancel_all` wacht op de schrijver (`tasks.wait_at_shutdown`)."""
    writer: _Writer | None = _WRITERS.pop(asyncio.get_running_loop(), None)
    if writer is not None:
        writer.stop()


def _retire_writers() -> None:
    """Na het wissen of terugzetten van de database (`auth.forget_cached_data`): elke schrijver sluit zijn verbinding met het oude
    bestand na wat er al klaarstond, en de volgende regel opent een nieuwe schrijver op het nieuwe bestand."""
    for loop, writer in list(_WRITERS.items()):
        del _WRITERS[loop]
        try:
            loop.call_soon_threadsafe(writer.stop)
        except RuntimeError:  # de loop is dicht
            continue


auth.remember_cache(_retire_writers)


# --- herkomst --------------------------------------------------------------------------------------

async def current_actor() -> str:
    """`mcp:<app>` binnen een MCP-aanroep, `app:<gebruiker>` voor een scherm in de app, anders `system`."""
    actor = _actor.get()
    if actor:
        return actor
    if has_request_context():
        try:
            user = await auth.current_user()
        except Exception:  # noqa: BLE001
            user = None
        return f"app:{user['username']}" if user else "app"
    return "system"


@contextmanager
def login_proxy() -> Iterator[None]:
    """De AH-loginproxy stuurt hele webpagina's door; daarvan bewaren we alleen de pagina's, geen bodies."""
    token = _quiet.set(True)
    try:
        yield
    finally:
        _quiet.reset(token)


# --- uitgaand verkeer ------------------------------------------------------------------------------

def _action_for(method: str, url: str, request_json: Any) -> str:
    if isinstance(request_json, dict) and isinstance(request_json.get("operationName"), str):
        return f"GraphQL {request_json['operationName']}"[:200]
    path = urlsplit(url).path or "/"
    return f"{method} {path}"


@dataclass(frozen=True)
class _Outbound:
    """Een uitgaand verzoek en zijn antwoord, zoals het in de log komt."""

    method: str
    url: str
    request_body: bytes | str | None
    request_type: str
    status: int | None  # None: geen antwoord
    content_type: str = ""
    response_body: bytes | None = None  # None: niet gelezen, alleen `response_length` is bekend
    response_length: int | None = None  # uit Content-Length
    streamed: bool = False
    redirects: int = 0
    final_url: str = ""


def _content_length(headers: Mapping[str, str]) -> int | None:
    try:
        return int(headers.get("Content-Length") or 0) or None
    except ValueError:
        return None


def _response_summary(outbound: _Outbound, quiet: bool) -> tuple[str, int | None]:
    if outbound.streamed:
        return "[stream]", outbound.response_length
    if outbound.response_body is None:
        label: str = _binary_label(outbound.content_type, outbound.response_length) if bodies_enabled() and outbound.content_type else ""
        return ("" if quiet else label), outbound.response_length
    text: str
    size: int
    text, size = summarize_body(outbound.response_body, outbound.content_type)
    return ("" if quiet else text), size


async def _record_outbound(outbound: _Outbound, error: Exception | None, duration_ms: int) -> None:
    service: str = service_for(outbound.url)
    if not service:
        return
    quiet: bool = _quiet.get()
    method: str = outbound.method.upper()
    content_type: str = outbound.content_type
    if quiet and method == "GET" and outbound.status is not None and not any(kind in content_type for kind in ("html", "json")):
        return  # losse stijlen, scripts en plaatjes van de loginpagina
    request_body: str = ""
    req_bytes: int = 0
    request_json: Any = None
    if outbound.request_body:
        if "json" in outbound.request_type:
            try:
                request_json = json.loads(outbound.request_body)
            except ValueError:
                request_json = None
        request_body, req_bytes = summarize_body(outbound.request_body, outbound.request_type)
        if quiet:
            request_body = ""
    response_body: str
    resp_bytes: int | None
    response_body, resp_bytes = _response_summary(outbound, quiet) if outbound.status is not None else ("", None)
    ok: bool = error is None and outbound.status is not None and outbound.status < 400
    detail: dict[str, Any] = {}
    if content_type:
        detail["contentType"] = content_type.split(";")[0].strip()
    if outbound.redirects:
        detail["redirects"] = outbound.redirects
        detail["finalUrl"] = redact_url(outbound.final_url)
    if quiet:
        detail["loginProxy"] = True
    await record(
        kind="outbound",
        service=service,
        action=_action_for(method, outbound.url, request_json),
        method=method,
        url=redact_url(outbound.url),
        status=outbound.status,
        ok=ok,
        duration_ms=duration_ms,
        actor=await current_actor(),
        corr=_corr.get(),
        request_body=request_body,
        response_body=response_body,
        req_bytes=req_bytes or None,
        resp_bytes=resp_bytes,
        error=f"{type(error).__name__}: {error}" if error else "",
        detail=detail or None,
    )


async def _safe_record_outbound(outbound: _Outbound, error: Exception | None, duration_ms: int) -> None:
    try:
        await _record_outbound(outbound, error, duration_ms)
    except Exception:  # noqa: BLE001 - de log mag nooit een verzoek laten mislukken
        log.warning("Kon een uitgaand verzoek niet loggen", exc_info=True)


# --- uitgaand verkeer via httpx --------------------------------------------------------------------
#
# `http_client` hangt deze hooks aan elke client. De request-hook loopt voor elk verzoek, ook voor elke
# doorverwijzing die httpx volgt. httpx geeft het volgende verzoek een kopie van de `extensions` mee, dus wat
# de hook daar bij het eerste verzoek neerzet, ziet hij bij elke volgende stap terug. Zo leggen we alleen het
# eerste verzoek vast, met het eindresultaat en het aantal doorverwijzingen.
#
# Een fout (time-out, geen verbinding) bereikt de response-hook niet: httpx gooit hem voordat er een
# antwoord is. Zo'n verzoek komt dus niet in het auditlog, alleen in wat de aanroeper zelf logt. Dat is
# een bewuste keuze: anders moest elke aanroep door een omhulsel in plaats van door de hooks.

HttpxHook: TypeAlias = Callable[..., Awaitable[None]]

_HTTPX_STARTED: str = "audit_started"
_HTTPX_ORIGIN: str = "audit_origin"
_HTTPX_REDIRECTS: str = "audit_redirects"


@dataclass(frozen=True)
class _HttpxOrigin:
    """Het eerste verzoek van een aanroep: dat komt in de log, ook als httpx doorverwijzingen volgt."""

    method: str
    url: str
    body: bytes | None
    content_type: str


async def _httpx_request(request: httpx.Request) -> None:
    try:
        _httpx_mark_start(request)
    except Exception:  # noqa: BLE001 - de log mag nooit een verzoek laten mislukken
        log.warning("Kon een uitgaand verzoek niet loggen", exc_info=True)


def _httpx_mark_start(request: httpx.Request) -> None:
    extensions: dict[str, Any] = request.extensions
    if _HTTPX_STARTED in extensions:
        extensions[_HTTPX_REDIRECTS] = extensions.get(_HTTPX_REDIRECTS, 0) + 1
        return
    url: str = str(request.url)
    if not enabled() or not service_for(url):
        return
    try:
        body: bytes | None = request.content
    except httpx.RequestNotRead:
        body = None  # een gestreamde upload: die lezen we niet
    extensions[_HTTPX_STARTED] = time.perf_counter()
    extensions[_HTTPX_ORIGIN] = _HttpxOrigin(request.method, url, body or None, request.headers.get("Content-Type", "") if body else "")


async def _httpx_response(response: httpx.Response) -> None:
    try:
        read: bool | None = _httpx_should_read(response)
    except Exception:  # noqa: BLE001 - de log mag nooit een verzoek laten mislukken
        log.warning("Kon een uitgaand verzoek niet loggen", exc_info=True)
        return
    if read is None:
        return
    if read:
        # Buiten de try: een fout bij het lezen van de body hoort bij het verzoek zelf en gaat naar de aanroeper.
        await response.aread()
    try:
        await _httpx_log(response, read)
    except Exception:  # noqa: BLE001 - de log mag nooit een verzoek laten mislukken
        log.warning("Kon een uitgaand verzoek niet loggen", exc_info=True)


def _httpx_should_read(response: httpx.Response) -> bool | None:
    """None als dit verzoek niet in de log komt, anders of de hook de body moet lezen.

    De hook loopt voordat httpx de body leest. We lezen hem alleen als hij in de log komt: een plaatje of een
    download blijft zo ongelezen, ook als de aanroeper streamt. Een gestreamde tekst wordt hier wel in het
    geheugen gelezen, de aanroeper leest daarna gewoon uit die kopie.
    """
    if not isinstance(response.request.extensions.get(_HTTPX_ORIGIN), _HttpxOrigin):
        return None
    return bodies_enabled() and not _quiet.get() and _is_text(response.headers.get("Content-Type", ""))


async def _httpx_log(response: httpx.Response, read: bool) -> None:
    extensions: dict[str, Any] = response.request.extensions
    origin: _HttpxOrigin = extensions[_HTTPX_ORIGIN]
    outbound: _Outbound = _Outbound(
        method=origin.method,
        url=origin.url,
        request_body=origin.body,
        request_type=origin.content_type,
        status=response.status_code,
        content_type=response.headers.get("Content-Type", ""),
        response_body=response.content if read else None,
        response_length=None if read else _content_length(response.headers),
        redirects=extensions.get(_HTTPX_REDIRECTS, 0),
        final_url=str(response.url),
    )
    duration_ms: int = round((time.perf_counter() - extensions[_HTTPX_STARTED]) * 1000)
    if response.has_redirect_location:
        # Of httpx deze doorverwijzing volgt, staat pas vast na de hooks: volgt hij niet, dan zet hij zonder
        # tussenliggende `await` `response.next_request`. De eerste stap van een nieuwe task loopt in de volgende
        # ronde van de loop, dan is dat dus bekend. Volgt hij wel, dan logt de hook bij het laatste antwoord.
        # `tasks.spawn` houdt de task vast, anders kan hij halverwege worden opgeruimd.
        tasks.spawn("audit-redirect", _record_unfollowed_redirect(response, outbound, duration_ms))
        return
    await _record_outbound(outbound, None, duration_ms)


async def _record_unfollowed_redirect(response: httpx.Response, outbound: _Outbound, duration_ms: int) -> None:
    if response.next_request is not None:
        await _safe_record_outbound(outbound, None, duration_ms)


def httpx_hooks() -> dict[str, list[HttpxHook]]:
    """De `event_hooks` voor een `httpx.AsyncClient`."""
    return {"request": [_httpx_request], "response": [_httpx_response]}


# --- MCP en OAuth ----------------------------------------------------------------------------------

def _rpc_text(result: dict[str, Any]) -> str:
    return "\n".join(str(part.get("text", "")) for part in result.get("content") or [] if isinstance(part, dict))


def _mcp_parts(message: Any) -> tuple[str | None, dict[str, Any]]:
    """De JSON-RPC-methode en de params van een bericht (None en {} als het geen geldig bericht is)."""
    rpc = message.get("method") if isinstance(message, dict) else None
    params = message.get("params") if isinstance(message, dict) and isinstance(message.get("params"), dict) else {}
    return rpc, params


def _mcp_unlogged(rpc: str | None) -> bool:
    """Berichten die nooit een regel krijgen: ping, notificaties en alles zonder methode."""
    return not isinstance(rpc, str) or rpc == "ping" or rpc.startswith("notifications/")


def _mcp_actor(identity: dict[str, Any] | None) -> str:
    app_name = str((identity or {}).get("name") or "")
    return f"mcp:{app_name}" if app_name else "mcp"


def _mcp_fields(rpc: str, params: dict[str, Any], identity: dict[str, Any] | None, actor: str, corr: str, started: float, reply: dict[str, Any] | None, failure: BaseException | None) -> dict[str, Any]:
    """De velden van de regel voor een verwerkt MCP-bericht."""
    identity = identity or {}
    tool = str(params.get("name") or "") if rpc == "tools/call" else ""
    arguments = params.get("arguments") if rpc == "tools/call" else params
    request_body, req_bytes = summarize_body(json.dumps(arguments, ensure_ascii=False, default=str) if arguments else "", "application/json")
    result = (reply or {}).get("result") if isinstance(reply, dict) else None
    rpc_error = (reply or {}).get("error") if isinstance(reply, dict) else None
    tool_error = bool(isinstance(result, dict) and result.get("isError"))
    if rpc == "tools/call" and isinstance(result, dict):
        response_text = _rpc_text(result)
    elif rpc == "tools/list" and isinstance(result, dict):
        response_text = f"{len(result.get('tools') or [])} tools"
    elif isinstance(result, dict):
        response_text = json.dumps(result, ensure_ascii=False)
    else:
        response_text = ""
    response_body, resp_bytes = summarize_body(response_text, "text/plain")
    if rpc == "tools/list":
        response_body = response_text
    error = ""
    if isinstance(failure, asyncio.CancelledError):
        error = tr("onderbroken", "interrupted")
    elif failure:
        error = f"{type(failure).__name__}: {failure}"
    elif isinstance(rpc_error, dict):
        error = str(rpc_error.get("message") or "")
    elif tool_error:
        error = " ".join(response_text.split())[:300]
    return {
        "kind": "mcp",
        "service": "mcp",
        "action": tool or rpc,
        "method": rpc,
        "url": "/mcp",
        "status": 499 if isinstance(failure, asyncio.CancelledError) else 500 if failure else 200,
        "ok": not (failure or rpc_error or tool_error),
        "duration_ms": round((time.perf_counter() - started) * 1000),
        "actor": actor,
        "corr": corr,
        "request_body": request_body,
        "response_body": response_body,
        "req_bytes": req_bytes or None,
        "resp_bytes": resp_bytes or None,
        "error": error,
        "detail": {"user": identity.get("username") or "", "clientId": identity.get("client_id") or ""},
    }


async def mcp_message(message: Any, handler: Callable[[Any], Awaitable[dict[str, Any] | None]], identity: dict[str, Any] | None) -> dict[str, Any] | None:
    """Verwerk één JSON-RPC-bericht en leg het vast, met de winkelverzoeken die eruit volgen."""
    rpc, params = _mcp_parts(message)
    if not enabled() or _mcp_unlogged(rpc):
        return await handler(message)
    actor = _mcp_actor(identity)
    corr = secrets.token_hex(8)
    tokens = (_corr.set(corr), _actor.set(actor))
    started = time.perf_counter()
    reply: dict[str, Any] | None = None
    failure: BaseException | None = None
    try:
        reply = await handler(message)
        return reply
    except asyncio.CancelledError as exc:
        failure = exc  # een weggevallen client annuleert de handler: vastleggen als onderbroken
        raise
    except Exception as exc:  # noqa: BLE001
        failure = exc
        raise
    finally:
        _corr.reset(tokens[0])
        _actor.reset(tokens[1])
        try:
            await record(**_mcp_fields(str(rpc), params, identity, actor, corr, started, reply, failure))
        except Exception:  # noqa: BLE001
            log.warning("Kon een MCP-verzoek niet loggen", exc_info=True)


def _mcp_rejected_fields(status: int, reason: str, header_present: bool) -> dict[str, Any]:
    return {
        "kind": "mcp",
        "service": "mcp",
        "action": reason,
        "method": request.method,
        "url": "/mcp",
        "status": status,
        "ok": False,
        "actor": "mcp",
        "error": reason,
        "detail": {"token": "ongeldig / invalid" if header_present else "ontbreekt / missing", "ip": request.remote_addr or ""},
    }


async def mcp_rejected(status: int, reason: str, *, header_present: bool = False) -> None:
    """Een verzoek aan /mcp dat niet werd toegelaten (geen of ongeldig token, te groot, ...)."""
    await record(**_mcp_rejected_fields(status, reason, header_present))


async def _log_oauth(response):
    if request.method == "OPTIONS" or not request.path.startswith("/oauth/") or not enabled():
        return response
    data: dict[str, Any] = {}
    try:
        if request.is_json:
            parsed = await request.get_json(silent=True)
            data = parsed if isinstance(parsed, dict) else {}
        else:
            data = {**request.args.to_dict(), **(await request.form).to_dict()}
    except HTTPException:
        # Een lichaam boven de grens (413) of te traag (408): de route gaf die fout al, het auditlog legt hem vast zonder lichaam.
        data = request.args.to_dict()
    request_body, req_bytes = summarize_body(json.dumps(data, ensure_ascii=False, default=str) if data else "", "application/json")
    client_id = str(data.get("client_id") or "")
    name = str(data.get("client_name") or "")
    if client_id and not name:
        try:
            from . import oauth

            name = (await oauth.get_client(client_id) or {}).get("name") or ""
        except Exception:  # noqa: BLE001
            name = ""
    grant = str(data.get("grant_type") or data.get("decision") or "")
    await record(
        kind="oauth",
        service="mcp",
        action=f"{request.method} {request.path}" + (f" ({grant})" if grant else ""),
        method=request.method,
        url=request.path,
        status=response.status_code,
        ok=response.status_code < 400,
        actor=f"mcp:{name}" if name else "mcp",
        request_body=request_body,
        req_bytes=req_bytes or None,
        detail={"clientId": client_id, "ip": request.remote_addr or ""},
    )
    return response


# --- lezen en wissen -------------------------------------------------------------------------------

def _row(row, *, full: bool) -> dict[str, Any]:
    try:
        detail = json.loads(row["detail"]) if row["detail"] else {}
    except ValueError:
        detail = {}
    entry = {
        "id": row["id"],
        "ts": row["ts"],
        "kind": row["kind"],
        "service": row["service"],
        "action": row["action"],
        "method": row["method"],
        "url": row["url"],
        "status": row["status"],
        "ok": bool(row["ok"]),
        "durationMs": row["duration_ms"],
        "actor": row["actor"],
        "corr": row["corr"],
        "reqBytes": row["req_bytes"],
        "respBytes": row["resp_bytes"],
        "error": row["error"],
        "detail": detail,
    }
    if full:
        entry["request"] = row["request"]
        entry["response"] = row["response"]
    return entry


async def list_entries(
    *, service: str = "", kind: str = "", only_errors: bool = False, query: str = "", limit: int = PAGE_SIZE, before: int | None = None
) -> dict[str, Any]:
    limit = max(1, min(MAX_PAGE_SIZE, limit))
    where, params = _filters(service, kind, only_errors, query, before)
    sql = "SELECT * FROM audit_log" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC LIMIT ?"
    async with _connect() as conn:
        rows = list(await conn.execute_fetchall(sql, (*params, limit + 1)))
        since = (auth.utcnow() - timedelta(hours=24)).isoformat()
        counts = await conn.execute_fetchall(
            "SELECT service, COUNT(*) AS total, SUM(CASE WHEN ok = 0 THEN 1 ELSE 0 END) AS errors FROM audit_log WHERE ts >= ? GROUP BY service",
            (since,),
        )
        found = await auth.fetchone(conn, "SELECT COUNT(*) AS n FROM audit_log")
    total = found["n"] if found else 0
    more = len(rows) > limit
    entries = [_row(row, full=False) for row in rows[:limit]]
    return {
        "entries": entries,
        "next": entries[-1]["id"] if more and entries else None,
        "total": total,
        "last24h": {row["service"]: {"total": row["total"], "errors": row["errors"] or 0} for row in counts},
        "settings": settings_payload(),
    }


def _filters(service: str, kind: str, only_errors: bool, query: str, before: int | None) -> tuple[list[str], list[Any]]:
    where: list[str] = []
    params: list[Any] = []
    if service in SERVICES or service == "audit":
        where.append("service = ?")
        params.append(service)
    if kind in ("outbound", "mcp", "oauth", "audit"):
        where.append("kind = ?")
        params.append(kind)
    if only_errors:
        where.append("ok = 0")
    if query:
        like = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where.append("(action LIKE ? ESCAPE '\\' OR url LIKE ? ESCAPE '\\' OR error LIKE ? ESCAPE '\\' OR actor LIKE ? ESCAPE '\\')")
        params += [like] * 4
    if before:
        where.append("id < ?")
        params.append(before)
    return where, params


async def export_entries(*, service: str = "", kind: str = "", only_errors: bool = False, query: str = "") -> list[dict[str, Any]]:
    """De nieuwste regels die bij de filters passen, oudste eerst, mét verzoek en antwoord."""
    where, params = _filters(service, kind, only_errors, query, None)
    sql = "SELECT * FROM audit_log" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC LIMIT ?"
    async with _connect() as conn:
        rows = list(await conn.execute_fetchall(sql, (*params, MAX_EXPORT)))
    return [_row(row, full=True) for row in reversed(rows)]


def _csv_cell(value: Any) -> Any:
    # Een cel die met = + - @ begint, zou in een spreadsheet als formule draaien.
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def entries_csv(entries: list[dict[str, Any]]) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(CSV_COLUMNS)
    for entry in entries:
        writer.writerow([_csv_cell(entry.get(column)) for column in CSV_COLUMNS])
    return out.getvalue()


async def get_entry(entry_id: int) -> dict[str, Any] | None:
    async with _connect() as conn:
        row = await auth.fetchone(conn, "SELECT * FROM audit_log WHERE id = ?", (entry_id,))
        if not row:
            return None
        entry = _row(row, full=True)
        if row["corr"]:
            if row["kind"] == "mcp":
                related = await conn.execute_fetchall("SELECT * FROM audit_log WHERE corr = ? AND kind = 'outbound' ORDER BY id", (row["corr"],))
            else:
                related = await conn.execute_fetchall("SELECT * FROM audit_log WHERE corr = ? AND kind = 'mcp' ORDER BY id", (row["corr"],))
            entry["related"] = [_row(item, full=False) for item in related]
        else:
            entry["related"] = []
    return entry


async def clear(actor: str = "") -> int:
    async with _connect() as conn:
        cursor = await conn.execute("DELETE FROM audit_log")
        await conn.commit()
    removed = cursor.rowcount
    await record(kind="audit", service="audit", action="log cleared", actor=actor, detail={"removed": removed})
    return removed


def register_routes(app: Quart) -> None:
    """Alleen de beheerder: `/api/audit` staat in auth.ADMIN_PREFIXES, ook voor lezen."""
    app.after_request(_log_oauth)

    @app.get("/api/audit")
    async def audit_list():
        try:
            before = int(request.args.get("before") or 0) or None
            limit = int(request.args.get("limit") or PAGE_SIZE)
        except ValueError:
            return jsonify({"error": tr("Ongeldige waarde.", "Invalid value.")}), 400
        return jsonify(
            await list_entries(
                service=request.args.get("service", ""),
                kind=request.args.get("kind", ""),
                only_errors=request.args.get("status") == "error",
                query=request.args.get("q", "").strip()[:100],
                limit=limit,
                before=before,
            )
        )

    @app.get("/api/audit/export")
    async def audit_export():
        entries = await export_entries(
            service=request.args.get("service", ""),
            kind=request.args.get("kind", ""),
            only_errors=request.args.get("status") == "error",
            query=request.args.get("q", "").strip()[:100],
        )
        user = await auth.current_user()
        await record(kind="audit", service="audit", action="log exported", actor=f"app:{user['username']}" if user else "app", detail={"entries": len(entries)})
        stamp = auth.utcnow().strftime("%Y%m%d-%H%M%S")
        # Tot MAX_EXPORT regels omzetten is rekenwerk van een paar MB: in een thread, de event loop bedient intussen de rest.
        if request.args.get("format") == "csv":
            body, mimetype, name = "\ufeff" + await asyncio.to_thread(entries_csv, entries), "text/csv", f"mealieshopper-audit-{stamp}.csv"  # BOM: Excel leest anders de accenten verkeerd
        else:
            exported: dict[str, Any] = {"exportedAt": _now(), "entries": entries}
            body = await asyncio.to_thread(json.dumps, exported, ensure_ascii=False, indent=1)
            mimetype, name = "application/json", f"mealieshopper-audit-{stamp}.json"
        return Response(body, mimetype=mimetype, headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})

    @app.get("/api/audit/<int:entry_id>")
    async def audit_entry(entry_id: int):
        entry = await get_entry(entry_id)
        if not entry:
            return jsonify({"error": tr("Regel niet gevonden.", "Entry not found.")}), 404
        return jsonify(entry)

    @app.delete("/api/audit")
    async def audit_clear():
        user = await auth.current_user()
        removed = await clear(f"app:{user['username']}" if user else "app")
        return jsonify({"status": "ok", "removed": removed})
