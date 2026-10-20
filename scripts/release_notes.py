"""Schrijf de release-tekst voor een versie: de sectie `## vX.Y.Z` uit CHANGELOG.md.

Gebruik: python scripts/release_notes.py v1.2.0
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def notes_for(version: str, changelog: Path = ROOT / "CHANGELOG.md") -> str:
    """De tekst onder `## <version>`, tot het volgende `## `-kopje. Leeg als de sectie ontbreekt."""
    text = changelog.read_text(encoding="utf-8")
    match = re.search(rf"^## {re.escape(version)}\s*$(.*?)(?=^## |\Z)", text, re.M | re.S)
    return match.group(1).strip() if match else ""


def current_version() -> str:
    return "v" + (ROOT / "VERSION").read_text(encoding="utf-8").strip()


if __name__ == "__main__":
    version = sys.argv[1] if len(sys.argv) > 1 else current_version()
    notes = notes_for(version)
    if not notes:
        sys.exit(f"CHANGELOG.md heeft geen sectie '## {version}'")
    print(notes)
