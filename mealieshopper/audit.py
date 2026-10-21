"""Auditlog: wat MealieShopper met Albert Heijn, Picnic en Jumbo bespreekt, en wat AI-assistenten via MCP vragen.

Zo zie je precies hoe alles werkt: welke verzoeken naar de winkels gaan, wat die antwoorden, hoe lang het
duurde en welk MCP-verzoek van welke assistent erachter zat.

* Uitgaand verkeer wordt op één plek vastgelegd, in `requests.Session.send`. Dat dekt elke aanroep van
  `requests.get/post/request` in de hele app, ook nieuwe, zonder dat die iets hoeven te weten. De dienst
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

import contextvars
import csv
import io
import json
import logging
import re
import secrets
import threading
import time
from contextlib import closing, contextmanager
from datetime import timedelta
from os import environ
from typing import Any, Callable, Iterator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
from flask import Flask, Response, has_request_context, jsonify, request

from . import auth
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
    r"""(?P<value>"[^"]*"|'[^']*'|[^\s&,;}]+)""",
    re.I,
)
_BEARER = re.compile(r"(Bearer\s+)[\w.~+/=-]+", re.I)

_TEXT_TYPES = ("json", "text", "xml", "javascript", "x-www-form-urlencoded", "graphql")

# Wie een verzoek veroorzaakt: de MCP-aanroep (corr + herkomst) die op dat moment loopt.
_corr: contextvars.ContextVar[str] = contextvars.ContextVar("audit_corr", default="")
_actor: contextvars.ContextVar[str] = contextvars.ContextVar("audit_actor", default="")
_quiet: contextvars.ContextVar[bool] = contextvars.ContextVar("audit_quiet", default=False)
_depth: contextvars.ContextVar[int] = contextvars.ContextVar("audit_depth", default=0)

_init_lock = threading.Lock()
_initialised: set[str] = set()
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
    if kind and not any(marker in kind for marker in _TEXT_TYPES):
        return f"[{kind.split(';')[0].strip()}, {size} bytes]", size
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


def service_for(url: str) -> str:
    host = (urlsplit(url or "").hostname or "").lower()
    for service, domains in SERVICE_DOMAINS.items():
        if any(host == domain or host.endswith("." + domain) for domain in domains):
            return service
    return ""


# --- opslag ----------------------------------------------------------------------------------------

def _connect():
    conn = auth.connect()
    key = str(auth.db_path())
    if key not in _initialised:
        with _init_lock:
            conn.executescript(SCHEMA)
            conn.commit()
            _initialised.add(key)
    return conn


def _now() -> str:
    return auth.utcnow().isoformat()


def _prune(conn) -> None:
    days = retention_days()
    if days:
        conn.execute("DELETE FROM audit_log WHERE ts < ?", ((auth.utcnow() - timedelta(days=days)).isoformat(),))
    conn.execute("DELETE FROM audit_log WHERE id <= (SELECT MAX(id) FROM audit_log) - ?", (MAX_ROWS,))


def record(
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
    """Schrijf één regel. Mislukt het schrijven, dan gaat de aanvraag zelf gewoon door."""
    global _since_prune
    if not enabled():
        return None
    try:
        with closing(_connect()) as conn, conn:
            cursor = conn.execute(
                "INSERT INTO audit_log (ts, kind, service, action, method, url, status, ok, duration_ms, actor, corr, request, response, req_bytes, resp_bytes, error, detail) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    _now(), kind, service, action[:200], method, url[:2000], status, 1 if ok else 0, int(duration_ms), actor[:200], corr,
                    request_body, response_body, req_bytes, resp_bytes, redact_text(error)[:500],
                    json.dumps(detail, ensure_ascii=False) if detail else "",
                ),
            )
            row_id = cursor.lastrowid
            _since_prune += 1
            if _since_prune >= PRUNE_EVERY:
                _since_prune = 0
                _prune(conn)
        return row_id
    except Exception:  # noqa: BLE001 - de log mag nooit een verzoek laten mislukken
        log.warning("Kon geen auditregel schrijven", exc_info=True)
        return None


# --- herkomst --------------------------------------------------------------------------------------

def current_actor() -> str:
    """`mcp:<app>` binnen een MCP-aanroep, `app:<gebruiker>` voor een scherm in de app, anders `system`."""
    actor = _actor.get()
    if actor:
        return actor
    if has_request_context():
        try:
            user = auth.current_user()
        except Exception:  # noqa: BLE001
            user = None
        return f"app:{user['username']}" if user else "app"
    return "system"


def propagate(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Voor een ThreadPoolExecutor: de threads erven anders niet van wie het verzoek komt."""
    corr, actor = _corr.get(), current_actor()

    def run(*args: Any, **kwargs: Any) -> Any:
        tokens = (_corr.set(corr), _actor.set(actor))
        try:
            return fn(*args, **kwargs)
        finally:
            _corr.reset(tokens[0])
            _actor.reset(tokens[1])

    return run


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


def _log_outbound(prepared: requests.PreparedRequest, response: requests.Response | None, error: Exception | None, started: float, stream: bool) -> None:
    service = service_for(prepared.url or "")
    if not service:
        return
    quiet = _quiet.get()
    method = (prepared.method or "").upper()
    content_type = (response.headers.get("Content-Type", "") if response is not None else "")
    if quiet and method == "GET" and response is not None and not any(kind in content_type for kind in ("html", "json")):
        return  # losse stijlen, scripts en plaatjes van de loginpagina
    request_body, req_bytes = ("", 0)
    request_json: Any = None
    body = prepared.body if isinstance(prepared.body, (bytes, str)) else None
    if body:
        request_type = prepared.headers.get("Content-Type", "")
        if "json" in request_type:
            try:
                request_json = json.loads(body)
            except ValueError:
                request_json = None
        request_body, req_bytes = summarize_body(body, request_type)
        if quiet:
            request_body = ""
    response_body, resp_bytes = "", None
    if response is not None:
        if stream:
            resp_bytes = int(response.headers.get("Content-Length") or 0) or None
            response_body = "[stream]"
        else:
            response_body, resp_bytes = summarize_body(response.content, content_type)
            if quiet:
                response_body = ""
    status = response.status_code if response is not None else None
    ok = error is None and response is not None and response.status_code < 400
    detail: dict[str, Any] = {}
    if content_type:
        detail["contentType"] = content_type.split(";")[0].strip()
    if response is not None and response.history:
        detail["redirects"] = len(response.history)
        detail["finalUrl"] = redact_url(response.url)
    if quiet:
        detail["loginProxy"] = True
    record(
        kind="outbound",
        service=service,
        action=_action_for(method, prepared.url or "", request_json),
        method=method,
        url=redact_url(prepared.url or ""),
        status=status,
        ok=ok,
        duration_ms=round((time.perf_counter() - started) * 1000),
        actor=current_actor(),
        corr=_corr.get(),
        request_body=request_body,
        response_body=response_body,
        req_bytes=req_bytes or None,
        resp_bytes=resp_bytes,
        error=f"{type(error).__name__}: {error}" if error else "",
        detail=detail or None,
    )


def install() -> None:
    """Hang de log aan `requests.Session.send`. Veilig om vaker aan te roepen."""
    original = requests.Session.send
    if getattr(original, "_audited", False):
        return

    def audited_send(self: requests.Session, prepared: requests.PreparedRequest, **kwargs: Any) -> requests.Response:
        # Een redirect gaat via send() opnieuw door dezelfde sessie: alleen het eerste verzoek loggen,
        # met het eindresultaat en het aantal doorverwijzingen.
        if _depth.get() or not enabled() or not service_for(prepared.url or ""):
            return original(self, prepared, **kwargs)
        token = _depth.set(1)
        started = time.perf_counter()
        response: requests.Response | None = None
        error: Exception | None = None
        try:
            response = original(self, prepared, **kwargs)
            return response
        except Exception as exc:  # noqa: BLE001 - alles wat requests gooit, wordt na het loggen opnieuw gegooid
            error = exc
            raise
        finally:
            _depth.reset(token)
            try:
                _log_outbound(prepared, response, error, started, bool(kwargs.get("stream")))
            except Exception:  # noqa: BLE001
                log.warning("Kon een uitgaand verzoek niet loggen", exc_info=True)

    audited_send._audited = True  # type: ignore[attr-defined]
    requests.Session.send = audited_send  # type: ignore[method-assign]


# --- MCP en OAuth ----------------------------------------------------------------------------------

def _rpc_text(result: dict[str, Any]) -> str:
    return "\n".join(str(part.get("text", "")) for part in result.get("content") or [] if isinstance(part, dict))


def mcp_message(message: Any, handler: Callable[[Any], dict[str, Any] | None], identity: dict[str, Any] | None) -> dict[str, Any] | None:
    """Verwerk één JSON-RPC-bericht en leg het vast, met de winkelverzoeken die eruit volgen."""
    rpc = message.get("method") if isinstance(message, dict) else None
    params = message.get("params") if isinstance(message, dict) and isinstance(message.get("params"), dict) else {}
    if not enabled() or not isinstance(rpc, str) or rpc == "ping" or rpc.startswith("notifications/"):
        return handler(message)
    identity = identity or {}
    app_name = str(identity.get("name") or "")
    actor = f"mcp:{app_name}" if app_name else "mcp"
    corr = secrets.token_hex(8)
    tokens = (_corr.set(corr), _actor.set(actor))
    started = time.perf_counter()
    reply: dict[str, Any] | None = None
    failure: Exception | None = None
    try:
        reply = handler(message)
        return reply
    except Exception as exc:  # noqa: BLE001
        failure = exc
        raise
    finally:
        _corr.reset(tokens[0])
        _actor.reset(tokens[1])
        try:
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
            if failure:
                error = f"{type(failure).__name__}: {failure}"
            elif isinstance(rpc_error, dict):
                error = str(rpc_error.get("message") or "")
            elif tool_error:
                error = " ".join(response_text.split())[:300]
            record(
                kind="mcp",
                service="mcp",
                action=tool or rpc,
                method=rpc,
                url="/mcp",
                status=200 if not failure else 500,
                ok=not (failure or rpc_error or tool_error),
                duration_ms=round((time.perf_counter() - started) * 1000),
                actor=actor,
                corr=corr,
                request_body=request_body,
                response_body=response_body,
                req_bytes=req_bytes or None,
                resp_bytes=resp_bytes or None,
                error=error,
                detail={"user": identity.get("username") or "", "clientId": identity.get("client_id") or ""},
            )
        except Exception:  # noqa: BLE001
            log.warning("Kon een MCP-verzoek niet loggen", exc_info=True)


def mcp_rejected(status: int, reason: str, *, header_present: bool = False) -> None:
    """Een verzoek aan /mcp dat niet werd toegelaten (geen of ongeldig token, te groot, ...)."""
    record(
        kind="mcp",
        service="mcp",
        action=reason,
        method=request.method,
        url="/mcp",
        status=status,
        ok=False,
        actor="mcp",
        error=reason,
        detail={"token": "ongeldig / invalid" if header_present else "ontbreekt / missing", "ip": request.remote_addr or ""},
    )


def _log_oauth(response):
    if request.method == "OPTIONS" or not request.path.startswith("/oauth/") or not enabled():
        return response
    data: dict[str, Any] = {}
    if request.is_json:
        parsed = request.get_json(silent=True)
        data = parsed if isinstance(parsed, dict) else {}
    else:
        data = {**request.args.to_dict(), **request.form.to_dict()}
    request_body, req_bytes = summarize_body(json.dumps(data, ensure_ascii=False, default=str) if data else "", "application/json")
    client_id = str(data.get("client_id") or "")
    name = str(data.get("client_name") or "")
    if client_id and not name:
        try:
            from . import oauth

            name = (oauth.get_client(client_id) or {}).get("name") or ""
        except Exception:  # noqa: BLE001
            name = ""
    grant = str(data.get("grant_type") or data.get("decision") or "")
    record(
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


def list_entries(
    *, service: str = "", kind: str = "", only_errors: bool = False, query: str = "", limit: int = PAGE_SIZE, before: int | None = None
) -> dict[str, Any]:
    limit = max(1, min(MAX_PAGE_SIZE, limit))
    where, params = _filters(service, kind, only_errors, query, before)
    sql = "SELECT * FROM audit_log" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC LIMIT ?"
    with closing(_connect()) as conn:
        rows = conn.execute(sql, (*params, limit + 1)).fetchall()
        since = (auth.utcnow() - timedelta(hours=24)).isoformat()
        counts = conn.execute(
            "SELECT service, COUNT(*) AS total, SUM(CASE WHEN ok = 0 THEN 1 ELSE 0 END) AS errors FROM audit_log WHERE ts >= ? GROUP BY service",
            (since,),
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) AS n FROM audit_log").fetchone()["n"]
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


def export_entries(*, service: str = "", kind: str = "", only_errors: bool = False, query: str = "") -> list[dict[str, Any]]:
    """De nieuwste regels die bij de filters passen, oudste eerst, mét verzoek en antwoord."""
    where, params = _filters(service, kind, only_errors, query, None)
    sql = "SELECT * FROM audit_log" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC LIMIT ?"
    with closing(_connect()) as conn:
        rows = conn.execute(sql, (*params, MAX_EXPORT)).fetchall()
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


def get_entry(entry_id: int) -> dict[str, Any] | None:
    with closing(_connect()) as conn:
        row = conn.execute("SELECT * FROM audit_log WHERE id = ?", (entry_id,)).fetchone()
        if not row:
            return None
        entry = _row(row, full=True)
        if row["corr"]:
            if row["kind"] == "mcp":
                related = conn.execute("SELECT * FROM audit_log WHERE corr = ? AND kind = 'outbound' ORDER BY id", (row["corr"],)).fetchall()
            else:
                related = conn.execute("SELECT * FROM audit_log WHERE corr = ? AND kind = 'mcp' ORDER BY id", (row["corr"],)).fetchall()
            entry["related"] = [_row(item, full=False) for item in related]
        else:
            entry["related"] = []
    return entry


def clear(actor: str = "") -> int:
    with closing(_connect()) as conn, conn:
        removed = conn.execute("DELETE FROM audit_log").rowcount
    record(kind="audit", service="audit", action="log cleared", actor=actor, detail={"removed": removed})
    return removed


def register_routes(app: Flask) -> None:
    """Alleen de beheerder: `/api/audit` staat in auth.ADMIN_PREFIXES, ook voor lezen."""
    app.after_request(_log_oauth)

    @app.get("/api/audit")
    def audit_list():
        try:
            before = int(request.args.get("before") or 0) or None
            limit = int(request.args.get("limit") or PAGE_SIZE)
        except ValueError:
            return jsonify({"error": tr("Ongeldige waarde.", "Invalid value.")}), 400
        return jsonify(
            list_entries(
                service=request.args.get("service", ""),
                kind=request.args.get("kind", ""),
                only_errors=request.args.get("status") == "error",
                query=request.args.get("q", "").strip()[:100],
                limit=limit,
                before=before,
            )
        )

    @app.get("/api/audit/export")
    def audit_export():
        entries = export_entries(
            service=request.args.get("service", ""),
            kind=request.args.get("kind", ""),
            only_errors=request.args.get("status") == "error",
            query=request.args.get("q", "").strip()[:100],
        )
        user = auth.current_user()
        record(kind="audit", service="audit", action="log exported", actor=f"app:{user['username']}" if user else "app", detail={"entries": len(entries)})
        stamp = auth.utcnow().strftime("%Y%m%d-%H%M%S")
        if request.args.get("format") == "csv":
            body, mimetype, name = "\ufeff" + entries_csv(entries), "text/csv", f"mealieshopper-audit-{stamp}.csv"  # BOM: Excel leest anders de accenten verkeerd
        else:
            body, mimetype, name = json.dumps({"exportedAt": _now(), "entries": entries}, ensure_ascii=False, indent=1), "application/json", f"mealieshopper-audit-{stamp}.json"
        return Response(body, mimetype=mimetype, headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})

    @app.get("/api/audit/<int:entry_id>")
    def audit_entry(entry_id: int):
        entry = get_entry(entry_id)
        if not entry:
            return jsonify({"error": tr("Regel niet gevonden.", "Entry not found.")}), 404
        return jsonify(entry)

    @app.delete("/api/audit")
    def audit_clear():
        user = auth.current_user()
        removed = clear(f"app:{user['username']}" if user else "app")
        return jsonify({"status": "ok", "removed": removed})
