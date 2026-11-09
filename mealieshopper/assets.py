"""De versie van de statische bestanden: een stuk van de SHA-256 over de inhoud van `static/`.

De pagina hangt hem achter elk script en stylesheet (`?v=`), zodat de browser na een wijziging de nieuwe versie ophaalt, en
`/healthz` noemt hem, zodat een tabblad dat al open stond ziet dat de server nieuwere scripts heeft (router.js laadt dan bij de
volgende klik de hele pagina opnieuw). Hij volgt de bestanden zelf: een versie met de hand ophogen kan niet vergeten worden.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


def asset_version(static_dir: Path) -> str:
    """12 tekens hex over de namen en de inhoud van alle bestanden onder `static_dir`, in vaste volgorde."""
    digest = hashlib.sha256()
    for path in sorted(item for item in static_dir.rglob("*") if item.is_file()):
        digest.update(path.relative_to(static_dir).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()[:12]
