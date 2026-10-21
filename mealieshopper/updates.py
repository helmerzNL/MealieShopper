"""Updatecheck: kijkt op GitHub of er een nieuwere release is dan de versie die nu draait.

Alleen de laatste (gepubliceerde, niet-voorlopige) release van de repository wordt opgevraagd, via de
doorverwijzing van github.com/<repo>/releases/latest naar de tag; er gaat geen informatie over de
installatie of de gebruikers mee. Dat gaat bewust niet via api.github.com: anonieme aanvragen daar zijn
beperkt tot 60 per uur per IP-adres, en een server die dat adres deelt (of veel van GitHub haalt) kreeg
daardoor "HTTP 403 (rate limit)". Het antwoord blijft in het geheugen staan, zodat
GitHub niet bij elke paginaweergave wordt gevraagd. Uitzetten kan met `MEALIESHOPPER_UPDATE_CHECK=false`.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from datetime import datetime, timezone
from os import environ
from typing import Any

import requests

from . import about

REPO = "helmerzNL/MealieShopper"
LATEST_URL = f"https://github.com/{REPO}/releases/latest"
TAG = re.compile(r"/releases/tag/(v?\d+(?:\.\d+)*)(?:[/?#]|$)")
OFF = {"0", "false", "no", "off"}
SUCCESS_TTL = 6 * 3600  # seconden dat een gelukt antwoord blijft staan
FAILURE_TTL = 10 * 60  # na een mislukte poging wachten we even met een nieuwe
MIN_FORCE_GAP = 30  # "nu controleren" mag GitHub niet vaker dan dit raken (anoniem: 60 aanvragen per uur)
TIMEOUT = 8

log = logging.getLogger(__name__)
_lock = threading.Lock()
_cache: dict[str, Any] = {"at": 0.0, "ttl": 0.0, "result": None}


def enabled() -> bool:
    return environ.get("MEALIESHOPPER_UPDATE_CHECK", "").strip().lower() not in OFF


def parse_version(text: str) -> tuple[int, ...]:
    """'v1.12.0' -> (1, 12, 0); leeg als er geen versienummer in staat."""
    match = re.match(r"^\s*[vV]?(\d+(?:\.\d+)*)", text or "")
    return tuple(int(part) for part in match.group(1).split(".")) if match else ()


def is_newer(latest: str, current: str) -> bool:
    a, b = parse_version(latest), parse_version(current)
    if not a or not b:
        return False
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


def describe_error(exc: Exception) -> str:
    """Een korte, leesbare oorzaak voor de beheerder, bijvoorbeeld 'HTTP 403 (rate limit)' of 'ConnectionError'."""
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        code = exc.response.status_code
        hint = " (rate limit)" if code in (403, 429) else ""
        return f"HTTP {code}{hint}"
    if isinstance(exc, requests.exceptions.SSLError):
        return "SSLError (certificaat)"
    if isinstance(exc, requests.Timeout):
        return "Timeout"
    if isinstance(exc, requests.ConnectionError):
        return "ConnectionError (geen verbinding of DNS)"
    return f"{type(exc).__name__}: {str(exc)[:80]}".rstrip(": ")


def _fetch_latest() -> dict[str, str]:
    response = requests.get(
        LATEST_URL,
        headers={"User-Agent": "MealieShopper-update-check"},
        timeout=TIMEOUT,
        allow_redirects=False,
    )
    if response.status_code == 404:  # de repository is niet te zien
        return {"version": "", "url": ""}
    if not 300 <= response.status_code < 400:
        response.raise_for_status()
        raise ValueError(f"onverwacht antwoord van GitHub (HTTP {response.status_code})")
    location = response.headers.get("Location", "")
    match = TAG.search(location)
    if not match:
        if location.rstrip("/").endswith("/releases"):  # er is nog geen release gepubliceerd
            return {"version": "", "url": ""}
        raise ValueError("onverwachte doorverwijzing van GitHub")
    version = match.group(1).lstrip("vV")
    return {"version": version, "url": f"https://github.com/{REPO}/releases/tag/v{version}"}


def _payload(latest: dict[str, str] | None, error: bool, checked_at: float, detail: str = "") -> dict[str, Any]:
    current = about.build_info()["version"]
    latest = latest or {"version": "", "url": ""}
    return {
        "enabled": True,
        "current": current,
        "latest": latest["version"],
        "available": is_newer(latest["version"], current),
        "url": latest["url"],
        "checkedAt": datetime.fromtimestamp(checked_at, timezone.utc).isoformat(timespec="seconds") if checked_at else "",
        "error": error,
        "errorDetail": detail,
        "repo": REPO,
    }


def status(force: bool = False) -> dict[str, Any]:
    """Het resultaat van de updatecheck; `force` slaat het bewaarde antwoord over (met een korte rem)."""
    if not enabled():
        return {**_payload(None, False, 0), "enabled": False}
    now = time.time()
    with _lock:
        cached = _cache["result"]
        age = now - _cache["at"]
        if cached is not None and (age < _cache["ttl"] if not force else age < MIN_FORCE_GAP):
            return cached
        try:
            result = _payload(_fetch_latest(), False, now)
            ttl = SUCCESS_TTL
        except (requests.RequestException, ValueError) as exc:
            detail = describe_error(exc)
            log.warning("Updatecheck mislukt (%s): %s", detail, exc)
            # Een eerder gelukt antwoord blijft bruikbaar; alleen de foutmelding erbij.
            previous = cached or _payload(None, True, 0)
            result = {**previous, "error": True, "errorDetail": detail}
            ttl = FAILURE_TTL
        _cache.update(at=now, ttl=ttl, result=result)
        return result


def reset() -> None:
    with _lock:
        _cache.update(at=0.0, ttl=0.0, result=None)
