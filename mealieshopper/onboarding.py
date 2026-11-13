"""Eerste start: welke stappen moet een nieuwe installatie nog doorlopen?

Een installatie is "nieuw" zolang er geen manier is om in te loggen (passkey of wachtwoord),
er niet bewust voor "zonder beveiliging" is gekozen en de onboarding niet is afgerond. Daarna
leidt de app de gebruiker langs de beveiliging en de uitleg voor Mealie, Albert Heijn, Jumbo en Picnic.

De status wordt uit de aanwezige gegevens afgeleid; alleen "bezig" en "klaar" staan als vlag
in de database. Zo blijft een bestaande installatie (met passkey of wachtwoord) buiten beeld.
"""

from __future__ import annotations

import ipaddress
from typing import Any

from quart import Quart, jsonify

from . import ah, auth, crisp, jumbo, mealie, picnic, urls
from .i18n import tr

SETTING_ACTIVE = "ONBOARDING_ACTIVE"
SETTING_DONE = "ONBOARDING_DONE"

# Adressen die alleen binnen je eigen netwerk bestaan: daar werkt geen passkey en is geen echt domein (de lijst staat in urls.py).
PRIVATE_SUFFIXES = urls.PRIVATE_SUFFIXES


async def _flag(key: str) -> bool:
    return await auth.get_setting(key) == "1"


def hostname_info(host: str) -> dict[str, Any]:
    """Is dit een echte domeinnaam (waarmee een passkey kan werken)?"""
    host = (host or "").strip().lower().rstrip(".")
    if host.startswith("[") and "]" in host:  # IPv6 in een URL
        host = host[1 : host.index("]")]
    try:
        ipaddress.ip_address(host)
        return {"host": host, "isIp": True, "isDomain": False, "reason": tr("een IP-adres", "an IP address")}
    except ValueError:
        pass
    if host == "localhost" or host.endswith(".localhost"):
        return {"host": host, "isIp": False, "isDomain": False, "reason": tr("localhost", "localhost")}
    if "." not in host:
        return {"host": host, "isIp": False, "isDomain": False, "reason": tr("een naam zonder domein", "a name without a domain")}
    if host.endswith(PRIVATE_SUFFIXES):
        return {
            "host": host,
            "isIp": False,
            "isDomain": False,
            "reason": tr("een naam die alleen in je eigen netwerk bestaat", "a name that only exists on your own network"),
        }
    return {"host": host, "isIp": False, "isDomain": True, "reason": ""}


def environment() -> dict[str, Any]:
    """Wat weet de server over hoe je MealieShopper opent, en welke inlogmethode past daarbij?"""
    info = hostname_info(auth.rp_id())
    secure = auth.request_is_secure()
    passkey_possible = info["isDomain"] and secure
    if passkey_possible:
        why = ""
    elif not info["isDomain"]:
        why = tr(
            f"Je opent MealieShopper via {info['reason']} ({info['host']}). Een passkey vraagt een echte domeinnaam.",
            f"You are opening MealieShopper through {info['reason']} ({info['host']}). A passkey needs a real domain name.",
        )
    else:
        why = tr(
            "Je opent MealieShopper zonder HTTPS. Een passkey vraagt een beveiligde verbinding (https://).",
            "You are opening MealieShopper without HTTPS. A passkey needs a secure connection (https://).",
        )
    return {
        "host": info["host"],
        "hasDomain": info["isDomain"],
        "secure": secure,
        "passkeyAvailable": passkey_possible,
        "passkeyUnavailableReason": why,
        "origin": auth.request_origin(),
    }


async def _integrations_configured() -> bool:
    try:
        return (
            bool(await mealie.mealie_url() and await mealie.mealie_token())
            or bool(await ah.configured_refresh_token())
            or bool(await jumbo.configured_refresh_token())
            or bool(await picnic.configured_auth_key())
            or bool(await crisp.configured_client_id())
        )
    except Exception:  # noqa: BLE001 - een onleesbare database telt niet als "al ingesteld"
        return False


async def is_fresh() -> bool:
    if await auth.credentials_exist() or await auth.auth_mode():
        return False
    if not auth.auth_enabled() and await _integrations_configured():
        return False  # bestaande installatie met inloggen uit en al gekoppeld
    return True


async def needed() -> bool:
    if await _flag(SETTING_DONE):
        return False
    return await _flag(SETTING_ACTIVE) or await is_fresh()


async def begin() -> None:
    """Onthoud dat de onboarding is begonnen, ook als er daarna een inlogmethode bijkomt."""
    if not await _flag(SETTING_DONE) and not await _flag(SETTING_ACTIVE) and await is_fresh():
        await auth.set_setting(SETTING_ACTIVE, "1")


async def complete() -> None:
    await auth.set_setting(SETTING_DONE, "1")
    await auth.set_setting(SETTING_ACTIVE, "0")


def register_routes(app: Quart) -> None:
    @app.get("/api/onboarding/status")
    async def onboarding_status():
        env = environment()
        required = await auth.auth_required()
        mode = await auth.auth_mode()
        if not auth.auth_enabled():
            auth_mode = "off"
        elif mode:
            auth_mode = mode
        elif await auth.passkey_count():
            auth_mode = "passkey"
        elif await auth.password_configured():
            auth_mode = "password"
        else:
            auth_mode = ""
        return jsonify(
            {
                "needed": await needed(),
                **env,
                "authEnabled": auth.auth_enabled(),
                "authRequired": required,
                "authDone": await auth.credentials_exist() or mode == "none" or not auth.auth_enabled(),
                "authMode": auth_mode,
                "authenticated": bool(await auth.current_user()) or not required,
                "minPasswordLength": 10,
            }
        )

    @app.post("/api/onboarding/start")
    async def onboarding_start():
        await begin()
        return jsonify({"needed": await needed()})

    @app.post("/api/onboarding/complete")
    async def onboarding_complete():
        # Valt onder de gewone inlogcontrole: zonder sessie (als inloggen aan staat) komt dit er niet door.
        await complete()
        return jsonify({"needed": False})
