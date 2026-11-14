"""Schrijf de release-tekst voor een versie uit CHANGELOG.md.

Een pre-release (beta) krijgt de sectie `## vX.Y.Z` van die versie. Een stabiele release krijgt met `--since` alle
versies sinds de vorige stabiele release, zodat wat alleen als beta is uitgekomen ook in de stabiele release staat.

Gebruik:
    python scripts/release_notes.py v1.2.0                  de sectie van v1.2.0
    python scripts/release_notes.py v1.2.0 --since v1.0.0   alle versies na v1.0.0 tot en met v1.2.0
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HEADING = re.compile(r"^## (v\d+\.\d+\.\d+)\s*$", re.M)


def notes_for(version: str, changelog: Path = ROOT / "CHANGELOG.md") -> str:
    """De tekst onder `## <version>`, tot het volgende `## `-kopje. Leeg als de sectie ontbreekt."""
    text = changelog.read_text(encoding="utf-8")
    match = re.search(rf"^## {re.escape(version)}\s*$(.*?)(?=^## |\Z)", text, re.M | re.S)
    return match.group(1).strip() if match else ""


def _number(version: str) -> tuple[int, ...] | None:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", version.strip())
    return tuple(int(part) for part in match.groups()) if match else None


def versions_since(version: str, previous: str, changelog: Path = ROOT / "CHANGELOG.md") -> list[str]:
    """De versies in CHANGELOG.md na `previous` tot en met `version`, nieuwste eerst."""
    upper, lower = _number(version), _number(previous)
    if upper is None:
        return []
    found = HEADING.findall(changelog.read_text(encoding="utf-8"))
    chosen = [v for v in found if (lower is None or _number(v) > lower) and _number(v) <= upper]
    return sorted(chosen, key=_number, reverse=True)


def notes_since(version: str, previous: str, changelog: Path = ROOT / "CHANGELOG.md") -> str:
    """De release-tekst van een stabiele release: alle versies sinds de vorige stabiele release `previous`.

    Zonder vorige release, of als er maar één versie tussen zit, is dit hetzelfde als `notes_for(version)`. Heeft
    `version` zelf geen sectie, dan is het leeg, net als bij `notes_for`: dan faalt de workflow in plaats van een
    release met alleen oudere versies te maken.
    """
    own = notes_for(version, changelog)
    versions = versions_since(version, previous, changelog) if previous and own else []
    if len(versions) <= 1:
        return own
    listed = ", ".join(versions[::-1])
    parts = [f"Alles sinds de vorige stabiele versie {previous}: {listed}. / Everything since the previous stable version {previous}: {listed}."]
    parts += [f"## {v}\n\n{notes_for(v, changelog)}" for v in versions]
    return "\n\n".join(parts)


def current_version() -> str:
    return "v" + (ROOT / "VERSION").read_text(encoding="utf-8").strip()


if __name__ == "__main__":
    args = sys.argv[1:]
    previous = ""
    if "--since" in args:
        index = args.index("--since")
        previous = args[index + 1] if index + 1 < len(args) else ""
        del args[index : index + 2]
    version = args[0] if args else current_version()
    notes = notes_since(version, previous)
    if not notes:
        sys.exit(f"CHANGELOG.md heeft geen sectie '## {version}'")
    print(notes)
