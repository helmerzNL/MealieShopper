"""Taal van de meldingen die de server zelf aan de gebruiker teruggeeft.

De browser zet de gekozen taal (nl of en) in de cookie `ms_lang`. Zonder cookie, of met een
andere waarde, blijft het Nederlands. Meldingen schrijf je als `tr("Nederlands", "English")`.
"""
from __future__ import annotations

from flask import has_request_context, request

LANGUAGES = ("nl", "en")
COOKIE_NAME = "ms_lang"
DEFAULT = "nl"


def current_lang() -> str:
    if not has_request_context():
        return DEFAULT
    value = request.cookies.get(COOKIE_NAME, "")
    return value if value in LANGUAGES else DEFAULT


def tr(nl: str, en: str) -> str:
    return en if current_lang() == "en" else nl
