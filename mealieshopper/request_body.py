"""De JSON-body van een aanvraag als object.

Bijna elke POST-route leest `await request.get_json(silent=True) or {}` en roept dan `.get` aan. Een body die wel JSON is maar
geen object (`"x"`, `123`, `true`, `[1]`) kwam daar als string, getal of lijst doorheen en gaf een 500 op de `.get`. Hier leest
`json_object` de body een keer en geeft een `BadBody` voor alles wat geen object is, die `register` omzet in een 400 met een
leesbare fout. Geen body, `null`, `[]`, `{}`, een lege string en `false` blijven een leeg object: die werkten altijd al zo.
"""

from __future__ import annotations

from typing import Any

from quart import Quart, Response, jsonify, request

from .i18n import tr


class BadBody(ValueError):
    """De body is JSON, maar geen object."""


async def json_object() -> dict[str, Any]:
    """De JSON-body als dict: een leeg object zonder body of bij een lege waarde, anders `BadBody` als het geen object is."""
    body: Any = await request.get_json(silent=True)
    if not body:
        return {}
    if isinstance(body, dict):
        return body
    raise BadBody(type(body).__name__)


def register(app: Quart) -> None:
    """Zet `BadBody` om in een 400 met de fout in de taal van de gebruiker."""

    @app.errorhandler(BadBody)
    async def _bad_body(_exc: BadBody) -> tuple[Response, int]:
        return jsonify({"error": tr("De aanvraag moet een JSON-object zijn.", "The request must be a JSON object.")}), 400
