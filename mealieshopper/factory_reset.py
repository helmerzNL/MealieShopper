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

import os
import signal
import sys
import threading
from contextlib import closing
from typing import Any

from flask import Flask, jsonify, request

from . import auth, auth_password
from .i18n import tr

CONFIRM_PHRASE = "RESET"
RESTART_DELAY_SECONDS = 1.5  # genoeg om het antwoord nog te laten aankomen

# Wat de app in de data-map bewaart. De database kan naast het hoofdbestand een logbestand hebben (WAL/journal).
DATA_FILES = tuple(f"auth.sqlite3{suffix}" for suffix in ("", "-wal", "-shm", "-journal")) + (auth.SECRET_FILE,)


def restart_supported() -> bool:
    """Kan de app zichzelf opnieuw starten? Alleen onder gunicorn (zoals in de container): die hoofdtaak stopt
    en Docker start de container weer (restart-beleid `unless-stopped` of `always`)."""
    return "gunicorn.arbiter" in sys.modules


def wipe_data() -> list[str]:
    """Verwijdert de database en het geheim; geeft de namen van de bestanden die er waren."""
    removed = []
    for name in DATA_FILES:
        path = auth.data_dir() / name
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        removed.append(name)
    for path in auth.data_dir().glob(".restore-*"):  # een klaargezette back-up bevat ook gegevens
        try:
            path.unlink()
        except OSError:
            continue
        removed.append(path.name)
    auth.forget_runtime_state()  # zonder herstart maakt de volgende aanvraag een nieuwe, lege database
    return removed


def restart() -> None:
    """Stopt de gunicorn-hoofdtaak (de container herstart dan); lukt dat niet, dan alleen deze werker."""
    for pid in (os.getppid(), os.getpid()):
        try:
            os.kill(pid, signal.SIGTERM)
            return
        except OSError:
            continue


def _owner_methods() -> dict[str, bool]:
    return {
        "password": auth.password_configured(auth.OWNER_ID),
        "mfa": auth.mfa_enabled(auth.OWNER_ID),
        "passkey": auth.auth_enabled() and auth.passkey_count(auth.OWNER_ID) > 0,
    }


def _check_passkey(uid: str, credential: Any):
    """None als de passkey van de beheerder de uitdaging goed ondertekende; anders het foutantwoord."""
    if auth_password.locked_seconds():
        return auth_password._too_many()
    challenge = auth.pop_challenge(f"reset:{uid}")
    credential = credential if isinstance(credential, dict) else {}
    if not challenge:
        return jsonify({"error": tr("Deze bevestiging is verlopen. Probeer het opnieuw.", "This confirmation has expired. Try again.")}), 400
    with closing(auth.connect()) as conn:
        stored = conn.execute(
            "SELECT public_key FROM passkey_credentials WHERE id = ? AND user_id = ?",
            (str(credential.get("id") or ""), uid),
        ).fetchone()
    try:
        if not stored:
            raise ValueError("Unknown credential")
        auth.check_assertion(credential, challenge, bytes(stored["public_key"]))
    except Exception:  # noqa: BLE001 - elke afwijking in de ondertekening is een mislukte bevestiging
        auth_password.register_failure()
        return jsonify({"error": tr("De passkey is niet geaccepteerd.", "The passkey was not accepted.")}), 403
    auth_password.reset_failures()
    return None


def register_routes(app: Flask) -> None:
    @app.get("/api/system/factory-reset")
    def factory_reset_info():
        return jsonify(
            {
                "phrase": CONFIRM_PHRASE,
                "authRequired": auth.auth_required(),
                "methods": _owner_methods(),
                "restart": restart_supported(),
            }
        )

    @app.post("/api/system/factory-reset/options")
    def factory_reset_options():
        """De uitdaging voor de passkey waarmee de beheerder het wissen bevestigt."""
        if not auth.auth_required() or not _owner_methods()["passkey"]:
            return jsonify({"error": tr("Bevestigen met een passkey kan hier niet.", "Confirming with a passkey is not possible here.")}), 400
        with closing(auth.connect()) as conn:
            rows = conn.execute("SELECT id FROM passkey_credentials WHERE user_id = ? ORDER BY created_at", (auth.OWNER_ID,)).fetchall()
        challenge = auth.make_challenge()
        auth.store_challenge(f"reset:{auth.OWNER_ID}", challenge)
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
    def factory_reset():
        body = request.get_json(silent=True) or {}
        if str(body.get("confirm") or "").strip() != CONFIRM_PHRASE:
            return jsonify({"error": tr(f"Typ {CONFIRM_PHRASE} om te bevestigen.", f"Type {CONFIRM_PHRASE} to confirm.")}), 400

        if auth.auth_required():
            user = auth.current_user()
            if not user or user["id"] != auth.OWNER_ID:
                return jsonify({"error": tr("Dit kan alleen de beheerder.", "Only the administrator can do this.")}), 403
            if body.get("credential"):
                wrong = _check_passkey(user["id"], body.get("credential"))
            elif _owner_methods()["password"]:
                wrong = auth_password.reauthenticate(user["id"], body)
            else:
                wrong = jsonify({"error": tr("Bevestig met je passkey.", "Confirm with your passkey.")}), 400
            if wrong:
                return wrong

        restarting = restart_supported()
        removed = wipe_data()
        app.logger.warning("Fabrieksinstellingen: %d bestand(en) uit de data-map verwijderd (%s)", len(removed), ", ".join(removed))
        if restarting:
            timer = threading.Timer(RESTART_DELAY_SECONDS, restart)
            timer.daemon = True
            timer.start()
        return auth.clear_cookie_response({"status": "ok", "restarting": restarting})
