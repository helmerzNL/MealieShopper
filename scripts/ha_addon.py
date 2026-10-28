"""Houd de Home Assistant-add-ons gelijk aan VERSION en aan elkaar.

`homeassistant/mealieshopper` is de bron (stabiel). Dit script zet daar de versie uit `VERSION` in en maakt
`homeassistant/mealieshopper-beta` ervan: dezelfde bestanden, met een eigen naam, slug, poort en de versie `X.Y.Z-beta`
(de tag die docker.yml bij een push naar beta bouwt).

Gebruik:
    python scripts/ha_addon.py           schrijf de add-ons bij
    python scripts/ha_addon.py --check   controleer alleen (exitcode 1 bij een verschil); de tests doen dit ook
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STABLE = ROOT / "homeassistant" / "mealieshopper"
BETA = ROOT / "homeassistant" / "mealieshopper-beta"
CONFIG = "config.yaml"


def _replace_line(text: str, pattern: str, replacement: str) -> str:
    text, count = re.subn(pattern, replacement, text, count=1, flags=re.M)
    if not count:
        raise SystemExit(f"config.yaml mist een regel die past bij {pattern!r}")
    return text


def stable_config(version: str) -> str:
    return _replace_line((STABLE / CONFIG).read_text(encoding="utf-8"), r'^version: ".*"$', f'version: "{version}"')


def beta_config(version: str) -> str:
    text = stable_config(version)
    text = text.replace(
        "# Home Assistant-add-on van MealieShopper (stabiele lijn). De map ../mealieshopper-beta is hiervan afgeleid:\n"
        "# pas alleen deze map aan en draai `python scripts/ha_addon.py`.",
        "# AFGELEID VAN ../mealieshopper door `python scripts/ha_addon.py`: pas dit bestand niet met de hand aan.",
    )
    text = _replace_line(text, r"^name: .*$", "name: MealieShopper (beta)")
    text = _replace_line(text, r"^slug: .*$", "slug: mealieshopper_beta")
    text = _replace_line(text, r'^version: ".*"$', f'version: "{version}-beta"')
    text = _replace_line(text, r"^description: .*$", "description: Development version of MealieShopper (beta), with its own data folder")
    text = _replace_line(text, r"^  8000/tcp: 5959$", "  8000/tcp: 5960")
    return text


def expected_files(version: str) -> dict[Path, bytes]:
    """Alle bestanden die er (naast de handgeschreven bronmap) moeten staan, met hun inhoud."""
    files = {STABLE / CONFIG: stable_config(version).encode("utf-8"), BETA / CONFIG: beta_config(version).encode("utf-8")}
    for source in sorted(STABLE.rglob("*")):
        if source.is_file() and source.name != CONFIG:
            files[BETA / source.relative_to(STABLE)] = source.read_bytes()
    return files


def current_version() -> str:
    return (ROOT / "VERSION").read_text(encoding="utf-8").strip()


def differences(version: str) -> list[str]:
    problems = []
    expected = expected_files(version)
    for path, content in expected.items():
        if not path.is_file():
            problems.append(f"ontbreekt: {path.relative_to(ROOT)}")
        elif path.read_bytes() != content:
            problems.append(f"wijkt af: {path.relative_to(ROOT)}")
    if BETA.is_dir():
        for path in sorted(BETA.rglob("*")):
            if path.is_file() and path not in expected:
                problems.append(f"hoort er niet: {path.relative_to(ROOT)}")
    return problems


def write(version: str) -> None:
    for path, content in expected_files(version).items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    for path in sorted(BETA.rglob("*")):
        if path.is_file() and not (STABLE / path.relative_to(BETA)).exists() and path.name != CONFIG:
            path.unlink()


if __name__ == "__main__":
    version = current_version()
    if "--check" in sys.argv[1:]:
        problems = differences(version)
        print("\n".join(problems) or "De add-ons zijn in orde.")
        sys.exit(1 if problems else 0)
    write(version)
    print(f"De add-ons staan op {version} (stabiel) en {version}-beta.")
