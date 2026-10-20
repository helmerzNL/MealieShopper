"""Versie van deze installatie en de componenten en integraties die MealieShopper gebruikt.

Het versienummer (vX.Y.Z) staat in het bestand VERSION in de hoofdmap en wordt bij een release
verhoogd. De commit komt uit de build van de Docker-image (build-argument GIT_COMMIT, in de
omgeving als MEALIESHOPPER_COMMIT); bij lokaal draaien uit git zelf. De versies van de Python-pakketten
worden live uit de installatie gelezen, zodat ze altijd kloppen.
"""
from __future__ import annotations

import platform
import sqlite3
import subprocess
from functools import lru_cache
from importlib import metadata
from os import environ
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
COFFEE_URL = "https://buymeacoffee.com/flux76"

# (id, groep, naam, pakket in pip, vaste versie, licentie, website)
COMPONENTS: list[tuple[str, str, str, str | None, str | None, str | None, str]] = [
    ("mealie", "integrations", "Mealie", None, None, None, "https://mealie.io"),
    ("ah", "integrations", "Albert Heijn", None, None, None, "https://www.ah.nl"),
    ("allerhande", "integrations", "Allerhande", None, None, None, "https://www.ah.nl/allerhande"),
    ("webauthn", "integrations", "WebAuthn (passkeys)", None, None, None, "https://www.w3.org/TR/webauthn-2/"),
    ("totp", "integrations", "TOTP (RFC 6238)", None, None, None, "https://www.rfc-editor.org/rfc/rfc6238"),
    ("python", "server", "Python", None, "python", "PSF-2.0", "https://www.python.org"),
    ("flask", "server", "Flask", "Flask", None, "BSD-3-Clause", "https://flask.palletsprojects.com"),
    ("gunicorn", "server", "Gunicorn", "gunicorn", None, "MIT", "https://gunicorn.org"),
    ("pyjwt", "server", "PyJWT", "PyJWT", None, "MIT", "https://pyjwt.readthedocs.io"),
    ("cryptography", "server", "cryptography", "cryptography", None, "Apache-2.0 OR BSD-3-Clause", "https://cryptography.io"),
    ("cbor2", "server", "cbor2", "cbor2", None, "MIT", "https://github.com/agronholm/cbor2"),
    ("requests", "server", "Requests", "requests", None, "Apache-2.0", "https://requests.readthedocs.io"),
    ("sqlite", "server", "SQLite", None, "sqlite", "Public domain", "https://sqlite.org"),
    ("mdi", "interface", "Material Design Icons", None, "7.4.47", "Apache-2.0", "https://pictogrammers.com/library/mdi/"),
    ("qrcode", "interface", "qrcode-generator", None, "2.0.4", "MIT", "https://github.com/kazuhikoarase/qrcode-generator"),
    ("inter", "interface", "Inter", None, None, "OFL-1.1", "https://rsms.me/inter/"),
    ("manrope", "interface", "Manrope", None, None, "OFL-1.1", "https://github.com/sharanda/manrope"),
]


def read_version() -> str:
    configured = environ.get("MEALIESHOPPER_VERSION", "").strip()
    if configured:
        return configured.lstrip("vV")
    try:
        text = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        text = ""
    return text.lstrip("vV") or "0.0.0"


def read_commit() -> str:
    configured = environ.get("MEALIESHOPPER_COMMIT", "").strip()
    if configured and configured.lower() != "unknown":
        return configured[:7]
    if not (ROOT / ".git").exists():
        return ""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short=7", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=3, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


@lru_cache(maxsize=1)
def build_info() -> dict[str, str]:
    version, commit = read_version(), read_commit()
    return {"version": version, "commit": commit, "label": f"v{version}" + (f" ({commit})" if commit else "")}


def _component_version(package: str | None, fixed: str | None) -> str:
    if fixed == "python":
        return platform.python_version()
    if fixed == "sqlite":
        return sqlite3.sqlite_version
    if fixed:
        return fixed
    if package:
        try:
            return metadata.version(package)
        except metadata.PackageNotFoundError:
            return ""
    return ""


def about() -> dict[str, Any]:
    return {
        **build_info(),
        "coffeeUrl": COFFEE_URL,
        "components": [
            {
                "id": ident,
                "group": group,
                "name": name,
                "version": _component_version(package, fixed),
                "license": license_name or "",
                "url": url,
            }
            for ident, group, name, package, fixed, license_name, url in COMPONENTS
        ],
    }
