"""Instellingen van de server die de beheerder in de app kan wijzigen: het publieke adres en de MCP-server.

Voorrang bij het publieke adres: wat de beheerder in de app instelt, dan de omgevingsvariabele
`MEALIESHOPPER_PUBLIC_BASE_URL`, en anders het adres waarmee de pagina is geopend (via de
`X-Forwarded-*`-headers van de reverse proxy). Bij de MCP-server geldt de schakelaar in de app; is die
nooit gezet, dan bepaalt `MEALIESHOPPER_MCP` (standaard aan).
"""
from __future__ import annotations

import json
from os import environ
from typing import Any
from urllib.parse import urlsplit

from flask import Flask, jsonify, request

from . import auth
from .i18n import tr

SETTING_BASE_URL = "PUBLIC_BASE_URL"
SETTING_MCP = "MCP_ENABLED"
SETTING_TOOLS_OFF = "MCP_TOOLS_OFF"  # JSON-lijst met de namen van uitgezette MCP-tools
OFF = {"0", "false", "no", "off"}


def detected_base_url() -> str:
    """Het adres waarmee deze aanvraag binnenkwam, zoals de reverse proxy het doorgeeft."""
    scheme = request.headers.get("X-Forwarded-Proto") or request.scheme or "http"
    host = request.headers.get("X-Forwarded-Host") or request.host
    return f"{scheme}://{host}".rstrip("/")


def normalize_base_url(value: Any) -> str:
    """Een geldig publiek adres (alleen schema, host en eventueel poort), of een ValueError met uitleg."""
    text = str(value or "").strip().rstrip("/")
    try:
        parts = urlsplit(text)
        parts.port  # noqa: B018 - een ongeldige poort geeft hier een ValueError
    except ValueError:
        raise ValueError(tr("Dit is geen geldig adres.", "This is not a valid address.")) from None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(tr("Begin met http:// of https:// en geef een hostnaam op.", "Start with http:// or https:// and give a host name."))
    if parts.username or parts.password or parts.path or parts.query or parts.fragment:
        raise ValueError(tr(
            "Gebruik alleen het adres zelf, zonder pad, zoekopdracht of inloggegevens (bijvoorbeeld https://mealieshopper.jouwdomein.nl).",
            "Use just the address itself, without path, query or credentials (for example https://mealieshopper.yourdomain.com).",
        ))
    return f"{parts.scheme}://{parts.netloc}".lower()


def configured_base_url() -> tuple[str, str]:
    """(adres, bron): bron is "setting" (in de app ingesteld), "env" of "auto" (herkend uit de aanvraag)."""
    override = auth.get_setting(SETTING_BASE_URL).strip().rstrip("/")
    if override:
        return override, "setting"
    from_env = (environ.get("MEALIESHOPPER_PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if from_env:
        return from_env, "env"
    return detected_base_url(), "auto"


def public_base_url() -> str:
    return configured_base_url()[0]


def mcp_setting() -> tuple[bool, str]:
    """(aan, bron): bron is "setting" (schakelaar in de app) of "default" (omgeving, anders aan)."""
    value = auth.get_setting(SETTING_MCP)
    if value:
        return value == "1", "setting"
    return environ.get("MEALIESHOPPER_MCP", "true").strip().lower() not in OFF, "default"


def disabled_tools() -> set[str]:
    """De MCP-tools die de beheerder heeft uitgezet. Alles staat standaard aan."""
    try:
        names = json.loads(auth.get_setting(SETTING_TOOLS_OFF) or "[]")
    except ValueError:
        return set()
    return {name for name in names if isinstance(name, str)} if isinstance(names, list) else set()


def tool_enabled(name: str) -> bool:
    return name not in disabled_tools()


def set_tools_enabled(changes: dict[str, bool]) -> None:
    """Zet tools aan of uit (een deel van de lijst is genoeg); onbekende namen worden niet bewaard."""
    off = disabled_tools()
    for name, enabled in changes.items():
        (off.discard if enabled else off.add)(name)
    auth.set_setting(SETTING_TOOLS_OFF, json.dumps(sorted(off)))


def tools_payload() -> list[dict[str, Any]]:
    from . import mcp  # lokaal: mcp importeert dit module

    off = disabled_tools()
    return [{"name": tool["name"], "title": tool["annotations"]["title"], "readOnly": tool["annotations"]["readOnlyHint"], "enabled": tool["name"] not in off} for tool in mcp.all_tool_descriptors()]


def settings_payload() -> dict[str, Any]:
    base, source = configured_base_url()
    enabled, mcp_source = mcp_setting()
    return {
        "publicBaseUrl": base,
        "publicBaseUrlSource": source,
        "publicBaseUrlOverride": auth.get_setting(SETTING_BASE_URL),
        "detectedBaseUrl": detected_base_url(),
        "envBaseUrl": (environ.get("MEALIESHOPPER_PUBLIC_BASE_URL") or "").strip().rstrip("/"),
        "mcpEnabled": enabled,
        "mcpSource": mcp_source,
        "mcpTools": tools_payload(),
        "loginOn": auth.auth_required() and auth.credentials_exist(),
    }


def register_routes(app: Flask) -> None:
    # Alleen de beheerder: /api/server-settings staat in auth.ADMIN_PREFIXES, ook voor lezen.
    @app.get("/api/server-settings")
    def server_settings_get():
        return jsonify(settings_payload())

    @app.put("/api/server-settings")
    def server_settings_put():
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or not ({"publicBaseUrl", "mcpEnabled", "mcpTools"} & set(body)):
            return jsonify({"error": tr("Geen instelling opgegeven.", "No setting given.")}), 400
        updates: dict[str, str] = {}
        if "publicBaseUrl" in body:
            raw = body["publicBaseUrl"]
            if raw is None or str(raw).strip() == "":
                updates[SETTING_BASE_URL] = ""  # terug naar automatisch
            else:
                try:
                    updates[SETTING_BASE_URL] = normalize_base_url(raw)
                except ValueError as exc:
                    return jsonify({"error": str(exc)}), 400
        if "mcpEnabled" in body:
            if not isinstance(body["mcpEnabled"], bool):
                return jsonify({"error": tr("mcpEnabled moet waar of onwaar zijn.", "mcpEnabled must be true or false.")}), 400
            updates[SETTING_MCP] = "1" if body["mcpEnabled"] else "0"
        tool_changes: dict[str, bool] = {}
        if "mcpTools" in body:
            from . import mcp  # lokaal: mcp importeert dit module

            changes = body["mcpTools"]
            known = {tool["name"] for tool in mcp.all_tool_descriptors()}
            if not isinstance(changes, dict) or not all(isinstance(value, bool) for value in changes.values()):
                return jsonify({"error": tr("mcpTools moet een object met waar/onwaar per tool zijn.", "mcpTools must be an object with true/false per tool.")}), 400
            unknown = sorted(set(changes) - known)
            if unknown:
                return jsonify({"error": tr(f"Onbekende tool: {', '.join(unknown)}", f"Unknown tool: {', '.join(unknown)}")}), 400
            tool_changes = changes
        for key, value in updates.items():
            auth.set_setting(key, value)
        if tool_changes:
            set_tools_enabled(tool_changes)
        return jsonify(settings_payload())
