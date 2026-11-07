"""Fabrieksinstellingen: wist alle gegevens van deze installatie en start de app opnieuw.

Na het wissen is MealieShopper een nieuwe installatie: de database (gebruikers, passkeys, wachtwoorden, koppelingen
met Mealie en de supermarkten, bewaarde lijsten, bestellingen, aanbiedingen, het auditlog) en het geheim waarmee
opgeslagen gegevens zijn versleuteld (`secret.key`) zijn weg, en de app begint weer bij de onboarding.

Wat blijft staan, staat buiten de data-map: de omgevingsvariabelen en de image zelf.

Veiligheid in het kort:
- Alleen de beheerder (`/api/system` staat in `auth.ADMIN_PREFIXES`).
- Een ingelogde sessie is niet genoeg: de aanvraag moet zelf bewijzen dat de beheerder erachter zit, met het
  wachtwoord (en de code bij tweestapsverificatie) of met een passkey, en met het getypte woord `RESET`.
- Foute pogingen tellen mee voor dezelfde begrenzing als inloggen.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import signal
from pathlib import Path
from typing import Any

from quart import Quart, jsonify, request

from . import auth, auth_password, backup, price_archive
from .i18n import tr

CONFIRM_PHRASE = "RESET"
RESTART_DELAY_SECONDS = 1.5  # genoeg om het antwoord nog te laten aankomen

# Wat de app in de data-map bewaart. De database kan naast het hoofdbestand een logbestand hebben (WAL/journal).
DATA_FILES = tuple(f"auth.sqlite3{suffix}" for suffix in ("", "-wal", "-shm", "-journal")) + (auth.SECRET_FILE,)

# Wat de app klaarzet naast de database: een upload in stukken (`<voorvoegsel><token>.part`, uploads.py), het bestand dat nog
# gecontroleerd wordt en wat op bevestiging wacht (`.upload`, `.rows`, en een `.job` als het proces hard stopte). Daar staan
# gegevens in, dus de reset haalt ze weg. Elk soort heeft zijn eigen voorvoegsel; een nieuw soort hoort hier bij (#276).
STAGED_PREFIXES = (backup.STAGE_PREFIX, price_archive.STAGE_PREFIX)


def restart_supported() -> bool:
    """Kan de app zichzelf opnieuw starten? Alleen in een container (`/.dockerenv`) of als Home Assistant-add-on
    (`/data/options.json`): daar stopt het proces en start het herstartbeleid (`unless-stopped` of `always` in
    compose, de watchdog van Home Assistant) het weer."""
    return Path("/.dockerenv").exists() or Path("/data/options.json").exists()


async def wipe_data() -> list[str]:
    """Verwijdert de database en het geheim. Geeft de namen van de bestanden die er waren.

    Het werk op schijf loopt in een thread: de afbeeldingencache kan duizenden bestanden bevatten. Daarna vergeet auth
    wat het onthield, op de event loop.
    """
    removed: list[str] = await asyncio.to_thread(_remove_files)
    auth.forget_runtime_state()  # zonder herstart maakt de volgende aanvraag een nieuwe, lege database
    return removed


def _remove_files() -> list[str]:
    """De bestanden van `wipe_data`, als geheel voor `asyncio.to_thread`: geen database-verbinding, alleen de schijf."""
    removed: list[str] = []
    for name in DATA_FILES:
        path = auth.data_dir() / name
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        removed.append(name)
    images = auth.data_dir() / "images"  # de afbeeldingencache (image_cache.py): bestanden op schijf, geen gegevens, maar wel van deze installatie
    if images.is_dir():
        shutil.rmtree(images, ignore_errors=True)
        removed.append("images")
    for prefix in STAGED_PREFIXES:  # klaargezette back-ups en prijsarchieven bevatten ook gegevens; alleen direct in de data-map
        for path in sorted(auth.data_dir().glob(f"{prefix}*")):
            try:
                path.unlink()
            except OSError:
                continue
            removed.append(path.name)
    return removed


def restart() -> None:
    """Stuurt SIGTERM naar dit proces. Hypercorn sluit dan netjes af (`after_serving` draait), waarna het
    herstartbeleid van compose of de watchdog van Home Assistant de container opnieuw start.

    Dat werkt omdat de Dockerfile hypercorn met `--workers 0` start: de app draait dan in het proces met de signal
    handlers van hypercorn. Met `--workers 1` zou dit het kindproces zonder handlers treffen, dat zonder `after_serving`
    sterft. `tests/test_restart.py` start hypercorn met de CMD uit de Dockerfile en controleert dit."""
    os.kill(os.getpid(), signal.SIGTERM)


async def _owner_methods() -> dict[str, bool]:
    return {
        "password": await auth.password_configured(auth.OWNER_ID),
        "mfa": await auth.mfa_enabled(auth.OWNER_ID),
        "passkey": auth.auth_enabled() and await auth.passkey_count(auth.OWNER_ID) > 0,
    }


async def _check_passkey(uid: str, credential: Any) -> auth_password.ErrorReply | None:
    """None als de passkey van de beheerder de uitdaging goed ondertekende, anders het foutantwoord.

    Onder hetzelfde slot als inloggen: gelijktijdige pogingen tellen een voor een mee voor de begrenzing.
    """
    credential = credential if isinstance(credential, dict) else {}
    async with auth_password.attempt_lock():
        if await auth_password.locked_seconds():
            return await auth_password._too_many()
        challenge = await auth.pop_challenge(f"reset:{uid}")
        if not challenge:
            return jsonify({"error": tr("Deze bevestiging is verlopen. Probeer het opnieuw.", "This confirmation has expired. Try again.")}), 400
        async with auth.connect() as conn, conn.execute(
            "SELECT public_key FROM passkey_credentials WHERE id = ? AND user_id = ?",
            (str(credential.get("id") or ""), uid),
        ) as cursor:
            stored = await cursor.fetchone()
        try:
            if not stored:
                raise ValueError("Unknown credential")
            # De webauthn-controle is rekenwerk: in een thread, met een kopie van de context (voor `request`).
            await auth.check_assertion(credential, challenge, bytes(stored["public_key"]))
        except Exception:  # noqa: BLE001 - elke afwijking in de ondertekening is een mislukte bevestiging
            await auth_password.register_failure()
            return jsonify({"error": tr("De passkey is niet geaccepteerd.", "The passkey was not accepted.")}), 403
        await auth_password.reset_failures()
    return None


def register_routes(app: Quart) -> None:
    @app.get("/api/system/factory-reset")
    async def factory_reset_info():
        return jsonify(
            {
                "phrase": CONFIRM_PHRASE,
                "authRequired": await auth.auth_required(),
                "methods": await _owner_methods(),
                "restart": restart_supported(),
            }
        )

    @app.post("/api/system/factory-reset/options")
    async def factory_reset_options():
        """De uitdaging voor de passkey waarmee de beheerder het wissen bevestigt."""
        if not await auth.auth_required() or not (await _owner_methods())["passkey"]:
            return jsonify({"error": tr("Bevestigen met een passkey kan hier niet.", "Confirming with a passkey is not possible here.")}), 400
        async with auth.connect() as conn, conn.execute(
            "SELECT id FROM passkey_credentials WHERE user_id = ? ORDER BY created_at", (auth.OWNER_ID,)
        ) as cursor:
            rows = await cursor.fetchall()
        challenge = auth.make_challenge()
        await auth.store_challenge(f"reset:{auth.OWNER_ID}", challenge)
        return jsonify(
            {
                "status": "ok",
                "options": {
                    "challenge": auth.b64url_encode(challenge),
                    "timeout": 60000,
                    "rpId": auth.rp_id(),
                    "allowCredentials": [{"type": "public-key", "id": row["id"]} for row in rows],
                    "userVerification": "preferred",
                },
            }
        )

    @app.post("/api/system/factory-reset")
    async def factory_reset():
        body = await request.get_json(silent=True) or {}
        if str(body.get("confirm") or "").strip() != CONFIRM_PHRASE:
            return jsonify({"error": tr(f"Typ {CONFIRM_PHRASE} om te bevestigen.", f"Type {CONFIRM_PHRASE} to confirm.")}), 400

        if await auth.auth_required():
            user = await auth.current_user()
            if not user or user["id"] != auth.OWNER_ID:
                return jsonify({"error": tr("Dit kan alleen de beheerder.", "Only the administrator can do this.")}), 403
            if body.get("credential"):
                wrong = await _check_passkey(user["id"], body.get("credential"))
            elif (await _owner_methods())["password"]:
                wrong = await auth_password.reauthenticate(user["id"], body)
            else:
                wrong = jsonify({"error": tr("Bevestig met je passkey.", "Confirm with your passkey.")}), 400
            if wrong:
                return wrong

        restarting = restart_supported()
        removed = await wipe_data()
        app.logger.warning("Fabrieksinstellingen: %d bestand(en) uit de data-map verwijderd (%s)", len(removed), ", ".join(removed))
        if restarting:
            # Eerst het antwoord, dan stopt het proces zichzelf: een timer op de event loop, geen thread.
            asyncio.get_running_loop().call_later(RESTART_DELAY_SECONDS, restart)
        return await auth.clear_cookie_response({"status": "ok", "restarting": restarting})
