"""Startpunt van de container: zet de opties van de Home Assistant-add-on om in omgevingsvariabelen en start de server.

Buiten Home Assistant verandert er niets: zonder `/data/options.json` (het bestand waarin de Supervisor de opties van een
add-on zet) start dit direct het commando uit de Dockerfile (gunicorn) of het commando dat je zelf meegeeft.

Alleen de standaardbibliotheek, zodat dit ook werkt voordat de app is geladen.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

OPTIONS_FILE = "/data/options.json"

# Optie in config.yaml -> omgevingsvariabele van de app. Een lege of ontbrekende optie zet niets: dan geldt wat de app zelf
# als standaard heeft (en wat de beheerder in de app instelt gaat toch voor).
OPTION_ENV = {
    "mealie_url": "MEALIE_URL",
    "mealie_api_token": "MEALIE_API_TOKEN",
    "public_base_url": "MEALIESHOPPER_PUBLIC_BASE_URL",
    "auth_secret": "MEALIESHOPPER_AUTH_SECRET",
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "openai_api_key": "OPENAI_API_KEY",
    "picnic_country_code": "PICNIC_COUNTRY_CODE",
    "offers_schedule": "MEALIESHOPPER_OFFERS_SCHEDULE",
    "offers_sync_hour": "MEALIESHOPPER_OFFERS_SYNC_HOUR",
    "mcp": "MEALIESHOPPER_MCP",
    "update_check": "MEALIESHOPPER_UPDATE_CHECK",
    "audit_log": "MEALIESHOPPER_AUDIT_LOG",
    "audit_retention_days": "MEALIESHOPPER_AUDIT_RETENTION_DAYS",
    "rp_id": "RP_ID",
    "rp_origins": "RP_ORIGINS",
    "rp_name": "RP_NAME",
}


def _text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).strip()


def env_from_options(options: dict[str, Any]) -> dict[str, str]:
    """De omgevingsvariabelen die bij de ingevulde opties horen; onbekende, lege en null-opties vallen weg."""
    env = {}
    for option, name in OPTION_ENV.items():
        value = options.get(option)
        if value is None:
            continue
        text = _text(value)
        if text:
            env[name] = text
    return env


def load_options(path: str = OPTIONS_FILE) -> dict[str, Any]:
    """De opties van de add-on, of {} buiten Home Assistant of als het bestand onleesbaar is."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def main(argv: list[str]) -> None:
    os.environ.update(env_from_options(load_options(os.environ.get("MEALIESHOPPER_OPTIONS_FILE", OPTIONS_FILE))))
    command = argv[1:]
    if not command:
        sys.exit("docker-entrypoint.py: geen commando om te starten")
    os.execvp(command[0], command)


if __name__ == "__main__":
    main(sys.argv)
