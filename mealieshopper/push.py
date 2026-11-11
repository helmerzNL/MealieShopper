"""Web Push voor de kooktimers (issue #311): de melding komt van de server, ook als de telefoon vergrendeld is.

Sinds 4.14.0 piept een kooktimer bij 0 en komt er een systeemmelding, maar alleen zolang de app draait: een vergrendelde
telefoon pauzeert de web-app. Hier stuurt de server de melding met Web Push (RFC 8030). Die komt ook als de app gepauzeerd
of gesloten is.

- **Een abonnement per toestel** (`push_subscriptions`). De app vraagt bij de push-dienst van de browser een abonnement aan
  (`PushManager.subscribe`, via de service worker `static/cook_sw.js`) en stuurt het mee met haar timers. Een abonnement
  hoort bij een browser, bij de gebruiker en bij de sessie waarmee het binnenkwam. Uitloggen haalt het weg
  (`forget_session`), net als een gebruiker verwijderen (`forget_user`) en de fabrieksinstellingen (die wissen de hele
  database). Een sessie die niet meer geldt (overal uitgelogd, een nieuw wachtwoord) krijgt niets meer (`_session_alive`).
- **De timers op de server** (`cook_timers`). Bij elke wijziging stuurt de app al haar lopende timers met hun resttijd
  (`PUT /api/cook/timers`). De server rekent de eindtijd uit met zijn eigen klok, plus `GRACE_SECONDS`. Een gepauzeerde of
  gestopte timer staat er dan niet meer in en krijgt geen push. Draait de app nog als een timer afloopt, dan meldt ze dat
  binnen die marge, en komt er geen tweede melding.
- **Versturen.** Een achtergrondtaak (`start`) slaapt tot de eerstvolgende eindtijd en verstuurt dan (`send_due`). Het
  versleutelen (RFC 8291, `aes128gcm`) doet `http_ece`, het ondertekenen (VAPID, RFC 8292) `py_vapid`: de twee
  bibliotheken van web-push-libs waarop ook pywebpush draait. pywebpush zelf brengt requests en aiohttp mee; de app
  verstuurt met haar eigen httpx-client (`http_client`). Een abonnement dat de push-dienst weigert (404 of 410, of 403:
  gemaakt met een andere sleutel) haalt de server weg.
- **De VAPID-sleutel.** De server maakt hem een keer aan en bewaart de privésleutel versleuteld in `app_settings`, zoals de
  andere geheimen (`auth.secret_box`). Hij zit niet in de back-up, net als de abonnementen: die horen bij deze sleutel.
- **Alleen push-diensten** (`PUSH_HOSTS`). De server stuurt alleen naar de push-diensten van Google (FCM), Apple, Mozilla en
  Microsoft, nooit naar een ander adres dat een browser opgeeft.
- **Zo min mogelijk erin.** De inhoud is versleuteld; de push-dienst ziet alleen dat er een bericht is. Erin staan de naam
  van de timer, het recept en de stap, en het pad van de pagina om te openen.

Beide tabellen staan bewust in `backup.EXCLUDED_TABLES`: een abonnement hoort bij een browser, een timer duurt minuten.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlsplit

import aiosqlite
import http_ece
import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from py_vapid import Vapid02, VapidException
from quart import Quart, jsonify

from . import about, auth, http_client, request_body, tasks
from .i18n import tr

log = logging.getLogger(__name__)

SETTING_VAPID_KEY = "PUSH_VAPID_PRIVATE_KEY"  # versleuteld in app_settings, zoals de andere geheimen

# De push-diensten van de browsers: Google (Chrome, Edge op Android, Samsung Internet, Opera, Brave; Chromium zonder de
# sleutels van Google gebruikt jmt17.google.com), Mozilla (Firefox), Apple (Safari en de geïnstalleerde app op een iPhone) en
# Microsoft (Edge op Windows). Een domein telt met zijn subdomeinen.
PUSH_HOSTS: tuple[str, ...] = (
    "fcm.googleapis.com", "android.googleapis.com", "jmt17.google.com", "push.services.mozilla.com", "push.apple.com", "notify.windows.com",
)

MAX_ENDPOINT_CHARS = 1024
MAX_TIMERS = 8  # zoals COOK_TIMERS_MAX in static/cook_timers.js
MAX_TIMER_SECONDS = 24 * 60 * 60  # zoals COOK_TIMER_LIMIT: langer is geen kooktimer
MAX_TITLE_CHARS = 120
MAX_BODY_CHARS = 300
MAX_URL_CHARS = 500
GRACE_SECONDS = 2.0  # zo lang na de eindtijd wacht de server: draait de app nog, dan meldt ze de timer zelf
TTL_SECONDS = 10 * 60  # zo lang bewaart de push-dienst de melding voor een toestel zonder verbinding (COOK_TIMER_KEEP)
STALE_SECONDS = TTL_SECONDS  # een timer die al zo lang voorbij is (de server stond uit), krijgt geen push meer
MAX_ATTEMPTS = 3  # bij een tijdelijke fout (geen verbinding, 429, 5xx) proberen we het zo vaak
RETRY_SECONDS = 5.0  # na de eerste mislukte poging, daarna twee keer zo lang
CLAIM_SECONDS = 60.0  # een timer die een worker zo lang geleden pakte maar niet afmaakte (gestopt), pakt een ander
CHECK_SECONDS = 60.0  # zo vaak kijkt de taak hoe dan ook (een timer van een andere worker, een klok die verspringt)
BATCH = 50  # zoveel timers per ronde
SEND_TIMEOUT = 15.0
JWT_SECONDS = 12 * 60 * 60  # hoe lang het VAPID-token geldt; RFC 8292 staat hooguit 24 uur toe
LOCAL_CONTACT = "mailto:mealieshopper@localhost"  # het contact zonder https-adres (alleen bij proberen op localhost)

_B64URL = re.compile(r"[A-Za-z0-9_-]{1,200}={0,2}")
_TIMER_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")

SCHEMA = """
-- Een push-abonnement van een browser (push.py). `endpoint` is het adres bij de push-dienst, `p256dh` en `auth` zijn de
-- sleutels van de browser (base64url). `session_id` en `session_epoch` komen van de sessie waarmee het binnenkwam; `contact`
-- is het publieke adres van de app op dat moment (de `sub` van het VAPID-token).
CREATE TABLE IF NOT EXISTS push_subscriptions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL,
  endpoint TEXT NOT NULL UNIQUE,
  p256dh TEXT NOT NULL,
  auth TEXT NOT NULL,
  session_id TEXT NOT NULL DEFAULT '',
  session_epoch INTEGER NOT NULL DEFAULT 0,
  contact TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS push_subscriptions_user ON push_subscriptions (user_id);
CREATE INDEX IF NOT EXISTS push_subscriptions_session ON push_subscriptions (session_id);
-- Een lopende kooktimer van een abonnement. `due_at` is het moment van de push (Unix-tijd in seconden, de klok van de
-- server); `claimed_at` zet de worker die hem verstuurt.
CREATE TABLE IF NOT EXISTS cook_timers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  subscription_id INTEGER NOT NULL,
  timer_id TEXT NOT NULL,
  due_at REAL NOT NULL,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  url TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  claimed_at REAL,
  UNIQUE (subscription_id, timer_id)
);
CREATE INDEX IF NOT EXISTS cook_timers_due ON cook_timers (due_at);
"""


@asynccontextmanager
async def _connect() -> AsyncIterator[aiosqlite.Connection]:
    """Een verbinding met de tabellen. Sluit zonder commit, zoals `auth.connect`."""
    await auth.ensure_db()
    async with auth.connect() as conn:
        await auth.ensure_schema(conn, "push", SCHEMA)
        yield conn


# ---- Controleren wat de browser stuurt


def push_endpoint(value: Any) -> bool:
    """Of `value` een adres bij een bekende push-dienst is: https, standaardpoort, zonder inloggegevens, een host uit
    `PUSH_HOSTS`. De server stuurt nooit naar een ander adres (geen verzoeken naar het eigen netwerk via de app)."""
    if not isinstance(value, str) or len(value) > MAX_ENDPOINT_CHARS or "\\" in value or any(ch.isspace() for ch in value):
        return False
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        return False
    if parts.scheme != "https" or parts.username is not None or parts.password is not None or port not in (None, 443):
        return False
    host = (parts.hostname or "").lower()
    return any(host == domain or host.endswith("." + domain) for domain in PUSH_HOSTS)


def _key_bytes(value: Any, size: int) -> bytes | None:
    """Een sleutel in base64url van precies `size` bytes, of None."""
    if not isinstance(value, str) or not _B64URL.fullmatch(value):
        return None
    try:
        data = auth.b64url_decode(value.rstrip("="))
    except ValueError:
        return None
    return data if len(data) == size else None


def parse_subscription(value: Any) -> dict[str, str]:
    """Het abonnement uit de browser (`PushSubscription.toJSON()`): het adres en de sleutels, gecontroleerd. Een ValueError
    met uitleg als het niet klopt."""
    if not isinstance(value, dict):
        raise ValueError(tr("Er ontbreekt een push-abonnement.", "A push subscription is missing."))
    endpoint = value.get("endpoint")
    if not push_endpoint(endpoint):
        raise ValueError(tr("Dit push-adres hoort niet bij een bekende push-dienst.", "This push address does not belong to a known push service."))
    keys = value.get("keys") if isinstance(value.get("keys"), dict) else {}
    public = _key_bytes(keys.get("p256dh"), 65)
    secret = _key_bytes(keys.get("auth"), 16)
    try:
        if public is None or secret is None:
            raise ValueError
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), public)  # een punt op de curve, anders kan versleutelen niet
    except ValueError:
        raise ValueError(tr("De sleutels van dit push-abonnement kloppen niet.", "The keys of this push subscription are not valid.")) from None
    return {"endpoint": str(endpoint), "p256dh": auth.b64url_encode(public), "auth": auth.b64url_encode(secret)}


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit] if isinstance(value, (str, int, float)) and not isinstance(value, bool) else ""


def parse_timers(value: Any, now: float) -> list[dict[str, Any]]:
    """De lopende timers uit de browser, als [{id, due, title, body}]. `left` is de resttijd in milliseconden; de eindtijd
    rekenen we met de klok van de server (die van de telefoon kan anders lopen), plus `GRACE_SECONDS`. Een ValueError als de
    lijst niet klopt; een tweede timer met hetzelfde id vervangt de eerste."""
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_TIMERS:
        raise ValueError(tr(f"Stuur hooguit {MAX_TIMERS} timers.", f"Send at most {MAX_TIMERS} timers."))
    found: dict[str, dict[str, Any]] = {}
    for item in value:
        timer_id = item.get("id") if isinstance(item, dict) else None
        left = item.get("left") if isinstance(item, dict) else None
        if not isinstance(timer_id, str) or not _TIMER_ID.fullmatch(timer_id):
            raise ValueError(tr("Een timer heeft geen geldig id.", "A timer has no valid id."))
        if isinstance(left, bool) or not isinstance(left, (int, float)) or not math.isfinite(left) or not 0 < left <= MAX_TIMER_SECONDS * 1000:
            raise ValueError(tr("Een timer heeft geen geldige resttijd.", "A timer has no valid remaining time."))
        found[timer_id] = {
            "id": timer_id,
            "due": now + left / 1000 + GRACE_SECONDS,
            "title": _text(item.get("title"), MAX_TITLE_CHARS) or tr("Timer klaar", "Timer done"),
            "body": _text(item.get("body"), MAX_BODY_CHARS),
        }
    return list(found.values())


def page_url(value: Any) -> str:
    """Het pad van de pagina die een tik op de melding opent: alleen een pad op deze app, anders de startpagina."""
    text = str(value or "") if isinstance(value, str) else ""
    if not text.startswith("/") or text.startswith("//") or "\\" in text or len(text) > MAX_URL_CHARS or any(ch.isspace() for ch in text):
        return "/"
    return text


def contact(base_url: Any) -> str:
    """De `sub` van het VAPID-token: het publieke adres van de app (een push-dienst als die van Apple eist een https-adres of
    een mailto), of `LOCAL_CONTACT` zonder https."""
    try:
        parts = urlsplit(str(base_url or ""))
    except ValueError:
        return LOCAL_CONTACT
    if parts.scheme != "https" or not parts.hostname:
        return LOCAL_CONTACT
    return f"https://{parts.netloc}".lower()


# ---- De VAPID-sleutel


def _encode_private(key: ec.EllipticCurvePrivateKey) -> str:
    return auth.b64url_encode(key.private_numbers().private_value.to_bytes(32, "big"))


def _decode_private(value: str) -> ec.EllipticCurvePrivateKey | None:
    try:
        number = int.from_bytes(auth.b64url_decode(value), "big")
        return ec.derive_private_key(number, ec.SECP256R1())
    except ValueError:
        return None


async def private_key() -> ec.EllipticCurvePrivateKey:
    """De VAPID-sleutel van deze installatie. De eerste keer maken we hem aan; met INSERT OR IGNORE houden twee workers die
    tegelijk beginnen allebei de eerste. Is de bewaarde sleutel onleesbaar (een ander `secret.key`), dan komt er een nieuwe:
    de oude abonnementen weigert de push-dienst dan (403) en de app vraagt een nieuw aan."""
    stored = _decode_private(await auth.get_secret(SETTING_VAPID_KEY))
    if stored is not None:
        return stored
    made = ec.generate_private_key(ec.SECP256R1())
    encrypted = (await auth.secret_box()).encrypt(_encode_private(made).encode("ascii")).decode("ascii")
    async with auth.connect() as conn:
        await conn.execute(
            "INSERT OR IGNORE INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)",
            (SETTING_VAPID_KEY, encrypted, auth.utcnow().isoformat()),
        )
        await conn.commit()
    stored = _decode_private(await auth.get_secret(SETTING_VAPID_KEY))
    if stored is not None:
        return stored
    await auth.set_secret(SETTING_VAPID_KEY, _encode_private(made))  # de oude was onleesbaar
    return made


def public_key_of(key: ec.EllipticCurvePrivateKey) -> str:
    """De publieke sleutel zoals de browser hem wil (`applicationServerKey`): het punt, ongecomprimeerd, in base64url."""
    return auth.b64url_encode(key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))


async def public_key() -> str:
    return public_key_of(await private_key())


# ---- Opslaan en vergeten


async def save_timers(
    user_id: str,
    subscription: Mapping[str, str],
    timers: list[dict[str, Any]],
    *,
    url: str = "/",
    session: tuple[str, int] = ("", 0),
    contact_url: str = "",
) -> int:
    """Bewaart het abonnement (een bestaand adres krijgt de nieuwe gebruiker, sessie en sleutels) en vervangt al zijn timers
    door `timers`. Geeft het aantal timers."""
    stamp = auth.utcnow().isoformat()
    session_id, session_epoch = session
    async with _connect() as conn:
        await conn.execute(
            """
            INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth, session_id, session_epoch, contact, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(endpoint) DO UPDATE SET user_id = excluded.user_id, p256dh = excluded.p256dh, auth = excluded.auth,
              session_id = excluded.session_id, session_epoch = excluded.session_epoch, contact = excluded.contact,
              updated_at = excluded.updated_at
            """,
            (user_id, subscription["endpoint"], subscription["p256dh"], subscription["auth"], session_id, session_epoch, contact_url, stamp, stamp),
        )
        row = await auth.fetchone(conn, "SELECT id FROM push_subscriptions WHERE endpoint = ?", (subscription["endpoint"],))
        assert row is not None
        await conn.execute("DELETE FROM cook_timers WHERE subscription_id = ?", (row["id"],))
        await conn.executemany(
            "INSERT INTO cook_timers (subscription_id, timer_id, due_at, title, body, url) VALUES (?, ?, ?, ?, ?, ?)",
            [(row["id"], timer["id"], timer["due"], timer["title"], timer["body"], url) for timer in timers],
        )
        await conn.commit()
    _poke()
    return len(timers)


async def _forget(where: str, params: tuple[Any, ...]) -> int:
    """Verwijdert de abonnementen die aan `where` voldoen, met hun timers. Geeft het aantal abonnementen."""
    async with _connect() as conn:
        await conn.execute(f"DELETE FROM cook_timers WHERE subscription_id IN (SELECT id FROM push_subscriptions WHERE {where})", params)
        cursor = await conn.execute(f"DELETE FROM push_subscriptions WHERE {where}", params)
        await conn.commit()
        return cursor.rowcount


async def forget(user_id: str, endpoint: Any) -> int:
    """Het abonnement van deze browser weg (de app vraagt het bij uitloggen)."""
    if not isinstance(endpoint, str) or not endpoint:
        return 0
    return await _forget("user_id = ? AND endpoint = ?", (user_id, endpoint))


async def forget_session(session_id: str) -> int:
    """Uitloggen: de abonnementen die met deze sessie binnenkwamen, gaan weg."""
    if not session_id:
        return 0
    return await _forget("session_id = ?", (session_id,))


async def forget_user(user_id: str) -> int:
    """Een verwijderde gebruiker neemt zijn abonnementen mee."""
    return await _forget("user_id = ?", (user_id,))


async def _drop_subscription(subscription_id: int) -> None:
    await _forget("id = ?", (subscription_id,))


async def _drop_timer(row_id: int) -> None:
    async with _connect() as conn:
        await conn.execute("DELETE FROM cook_timers WHERE id = ?", (row_id,))
        await conn.commit()


async def _session_alive(conn: aiosqlite.Connection, row: Mapping[str, Any]) -> bool:
    """Of de sessie van het abonnement nog geldt. Zonder sessie (de app zonder inloggen) alleen zolang inloggen niet nodig is."""
    if not row["session_id"]:
        return not await auth.auth_required()
    user = await auth.fetchone(conn, "SELECT session_epoch FROM users WHERE id = ?", (row["user_id"],))
    if user is None or int(user["session_epoch"]) != int(row["session_epoch"]):
        return False
    return not await auth._session_revoked(conn, row["session_id"])


# ---- Versturen


def _client() -> httpx.AsyncClient:
    return http_client.client(
        "push", headers={"User-Agent": f"MealieShopper/{about.version()}"}, timeout=SEND_TIMEOUT, follow_redirects=False
    )


def encrypt(payload: bytes, p256dh: str, secret: str) -> bytes:
    """`payload` versleuteld voor een browser (RFC 8291, `aes128gcm`) met een eigen tijdelijke sleutel per bericht."""
    return http_ece.encrypt(
        payload,
        private_key=ec.generate_private_key(ec.SECP256R1()),
        dh=auth.b64url_decode(p256dh),
        auth_secret=auth.b64url_decode(secret),
        version="aes128gcm",
    )


def vapid_headers(endpoint: str, key: ec.EllipticCurvePrivateKey, sub: str, now: float | None = None) -> dict[str, str]:
    """De header `Authorization: vapid t=..., k=...` voor de push-dienst van `endpoint` (RFC 8292)."""
    parts = urlsplit(endpoint)
    claims = {"aud": f"{parts.scheme}://{parts.netloc}", "sub": sub, "exp": int((time.time() if now is None else now) + JWT_SECONDS)}
    # no-strict: py_vapid kent alleen https-adressen zonder poort; RFC 8292 staat elk https-adres toe.
    signed: dict[str, str] = Vapid02(private_key=key, conf={"no-strict": True}).sign(claims)
    return signed


async def send(subscription: Mapping[str, Any], payload: dict[str, Any], key: ec.EllipticCurvePrivateKey) -> int:
    """Stuurt `payload` (JSON) versleuteld naar de push-dienst van het abonnement en geeft de HTTP-status terug."""
    endpoint = str(subscription["endpoint"])
    if not push_endpoint(endpoint):
        raise ValueError("geen push-dienst")
    body = encrypt(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), subscription["p256dh"], subscription["auth"])
    headers = {
        **vapid_headers(endpoint, key, contact(subscription["contact"])),
        "TTL": str(TTL_SECONDS),
        "Urgency": "high",  # een telefoon in de slaapstand haalt hem meteen op
        "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream",
    }
    response = await _client().post(endpoint, content=body, headers=headers)
    return response.status_code


def _host(endpoint: Any) -> str:
    """Alleen de host, voor het logboek: het hele adres van een abonnement hoort er niet in."""
    return urlsplit(str(endpoint or "")).hostname or "?"


async def _deliver(row: Mapping[str, Any], key: ec.EllipticCurvePrivateKey, now: float) -> str:
    """De push van een timer die aan de beurt is. Geeft wat er gebeurde: sent, retry, gone, signed_out, stale of failed."""
    async with _connect() as conn:
        alive = await _session_alive(conn, row)
    if not alive:
        await _drop_subscription(row["subscription_id"])
        return "signed_out"
    if now - float(row["due_at"]) > STALE_SECONDS:
        await _drop_timer(row["id"])
        return "stale"
    payload = {"type": "cook-timer", "id": row["timer_id"], "title": row["title"], "body": row["body"], "url": row["url"]}
    status: int | None
    try:
        status = await send(row, payload, key)
    except httpx.HTTPError as exc:
        log.info("Push naar %s mislukt: %s", _host(row["endpoint"]), type(exc).__name__)
        status = None
    except (ValueError, http_ece.ECEException, VapidException):  # het abonnement zelf is onbruikbaar (sleutels, adres)
        log.warning("Push-abonnement bij %s is onbruikbaar en is verwijderd", _host(row["endpoint"]))
        await _drop_subscription(row["subscription_id"])
        return "gone"
    if status is not None and 200 <= status < 300:
        await _drop_timer(row["id"])
        return "sent"
    if status in (403, 404, 410):  # verlopen, opgezegd, of met een andere sleutel gemaakt
        await _drop_subscription(row["subscription_id"])
        return "gone"
    attempts = int(row["attempts"]) + 1
    if (status is None or status == 429 or status >= 500) and attempts < MAX_ATTEMPTS:
        async with _connect() as conn:
            await conn.execute(
                "UPDATE cook_timers SET attempts = ?, due_at = ?, claimed_at = NULL WHERE id = ?",
                (attempts, now + RETRY_SECONDS * 2 ** (attempts - 1), row["id"]),
            )
            await conn.commit()
        return "retry"
    log.warning("Push naar %s geweigerd (%s)", _host(row["endpoint"]), status if status is not None else "geen verbinding")
    await _drop_timer(row["id"])
    return "failed"


async def send_due(now: float | None = None) -> float:
    """Verstuurt de push van elke timer die aan de beurt is. Geeft het aantal seconden tot de volgende (hooguit
    `CHECK_SECONDS`). Een worker pakt een timer eerst (`claimed_at`): zo stuurt er maar een de push."""
    now = time.time() if now is None else now
    stale_claim = now - CLAIM_SECONDS
    async with _connect() as conn:
        await conn.execute("DELETE FROM cook_timers WHERE subscription_id NOT IN (SELECT id FROM push_subscriptions)")
        rows = await conn.execute_fetchall(
            """
            SELECT t.id, t.timer_id, t.due_at, t.title, t.body, t.url, t.attempts, s.id AS subscription_id, s.user_id, s.endpoint,
              s.p256dh, s.auth, s.session_id, s.session_epoch, s.contact
            FROM cook_timers t JOIN push_subscriptions s ON s.id = t.subscription_id
            WHERE t.due_at <= ? AND (t.claimed_at IS NULL OR t.claimed_at < ?)
            ORDER BY t.due_at LIMIT ?
            """,
            (now, stale_claim, BATCH),
        )
        claimed = []
        for row in rows:
            cursor = await conn.execute(
                "UPDATE cook_timers SET claimed_at = ? WHERE id = ? AND (claimed_at IS NULL OR claimed_at < ?)", (now, row["id"], stale_claim)
            )
            if cursor.rowcount == 1:
                claimed.append(row)
        await conn.commit()
    if claimed:
        key = await private_key()
        for row in claimed:
            try:
                await _deliver(row, key, now)
            except Exception:  # noqa: BLE001 - een fout bij een timer houdt de andere niet tegen
                log.exception("Push van een kooktimer mislukt")
                await _drop_timer(row["id"])
    async with _connect() as conn:
        upcoming = await auth.fetchone(conn, "SELECT MIN(due_at) AS due FROM cook_timers WHERE claimed_at IS NULL OR claimed_at < ?", (stale_claim,))
    if upcoming is None or upcoming["due"] is None:
        return CHECK_SECONDS
    return max(0.0, min(CHECK_SECONDS, float(upcoming["due"]) - now))


# ---- De achtergrondtaak

_task: asyncio.Task[None] | None = None
_wake: asyncio.Event | None = None


def _poke() -> None:
    """Een timer erbij of eraf: de taak rekent opnieuw uit hoe lang hij slaapt."""
    if _wake is not None:
        _wake.set()


async def _loop(wake: asyncio.Event) -> None:
    while True:
        wake.clear()  # voor het versturen: een timer die er intussen bij komt, maakt de taak meteen weer wakker
        try:
            wait = await send_due()
        except Exception:  # noqa: BLE001 - de taak moet blijven draaien, ook na een fout
            log.exception("Push van de kooktimers mislukt")
            wait = CHECK_SECONDS
        try:
            await asyncio.wait_for(wake.wait(), timeout=wait)
        except TimeoutError:
            pass


def start() -> None:
    """Start de achtergrondtaak (een keer per proces). Moet binnen een lopende event loop worden aangeroepen."""
    global _task, _wake
    if _task is not None and not _task.done():
        return
    _wake = asyncio.Event()
    _task = tasks.spawn("cook-push", _loop(_wake))


# ---- Routes


def register_routes(app: Quart, public_base_url: Callable[[], Awaitable[str]]) -> None:
    @app.get("/api/push/key")
    async def push_key():
        """De publieke VAPID-sleutel, voor `PushManager.subscribe` in de browser."""
        return jsonify({"publicKey": await public_key()})

    @app.put("/api/cook/timers")
    async def cook_timers_put():
        """De lopende timers van deze browser, met zijn push-abonnement. Vervangt wat de server van deze browser had."""
        body = await request_body.json_object()
        try:
            subscription = parse_subscription(body.get("subscription"))
            timers = parse_timers(body.get("timers"), time.time())
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        user = await auth.current_user()
        count = await save_timers(
            user["id"] if user else auth.OWNER_ID,  # zonder inloggen doet de app alles als de eigenaar
            subscription,
            timers,
            url=page_url(body.get("url")),
            session=await auth.current_session() if user else ("", 0),
            contact_url=contact(await public_base_url()),
        )
        return jsonify({"timers": count, "publicKey": await public_key()})

    @app.delete("/api/push/subscription")
    async def push_subscription_delete():
        """Het abonnement van deze browser weg, met zijn timers."""
        body = await request_body.json_object()
        return jsonify({"removed": await forget(await auth.acting_user_id(), body.get("endpoint"))})
