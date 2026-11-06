"""Maakt `backup-3.13.1.json`: een back-up van MealieShopper 3.13.1 op een lege installatie, voor de test die een
back-up van een 3.x-versie terugzet (`test_restore_of_a_3_x_backup_file_succeeds` in tests/test_backup.py).

Het script draait op de code van 3.13.1, niet op die van deze checkout. Zo maak je het bestand opnieuw:

    git worktree add <scratch>/wt-3131 135bd86
    .venv/bin/python tests/fixtures/make_backup_3131.py <scratch>/wt-3131 tests/fixtures/backup-3.13.1.json
    git worktree remove <scratch>/wt-3131

`<scratch>` is een tijdelijke map buiten de checkout (het bestand in de repository is zo gemaakt, in de scratchmap
van de sessie).

Commit 135bd86 is de release 3.13.1 (Flask en requests, die de venv nog heeft). Het script maakt een lege datamap,
zet er een AH-lijstje met twee artikelen, twee aanbiedingen van AH (met hun geschiedenis) en de eerste dag van de week
in, met de functies van 3.13.1 zelf, en schrijft de back-up zonder koppelingen. Er gaat niets over het netwerk: het
ophalen van de aanbiedingen is vervangen door een vaste lijst.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any

OFFERS: list[dict[str, Any]] = [
    {
        "id": "wi1001", "title": "Spam in blik", "brand": "Hormel", "unitSize": "340 g", "price": 2.49, "priceBefore": 3.29,
        "promoLabel": "25% korting", "image": "", "url": "", "validFrom": "2026-11-02", "validUntil": "2026-11-08",
    },
    {
        "id": "wi1002", "title": "Scharreleieren", "brand": "AH", "unitSize": "10 stuks", "price": 2.99, "priceBefore": None,
        "promoLabel": "2e halve prijs", "image": "", "url": "", "validFrom": "2026-11-02", "validUntil": "2026-11-08",
    },
]

LIST: dict[str, Any] = {
    "id": "lijst-1",
    "name": "Ontbijt",
    "items": [
        {"id": 1, "productId": 1001, "quantity": 2, "product": {"title": "Spam in blik", "brand": "Hormel", "unitSize": "340 g", "price": 3.29}},
        {"id": 2, "productId": 1002, "quantity": 1, "product": {"title": "Scharreleieren", "brand": "AH", "unitSize": "10 stuks", "price": 2.99}},
    ],
}


def main(source: Path, target: Path) -> None:
    data: tempfile.TemporaryDirectory[str] = tempfile.TemporaryDirectory()
    try:
        os.environ["MEALIESHOPPER_DATA_DIR"] = data.name
        os.environ["MEALIESHOPPER_TESTING"] = "1"
        sys.path.insert(0, str(source))
        from mealieshopper import about, ah_lists, auth, backup, mealie, offers  # noqa: PLC0415 - pas na het zetten van de datamap

        version: str = about.read_version()
        if version != "3.13.1" or not Path(backup.__file__).resolve().is_relative_to(source):
            raise SystemExit(f"verwacht de code van 3.13.1 uit {source}, gevonden: {version} in {backup.__file__}")
        auth.init_db()
        ah_lists.save_snapshot(LIST)
        offers.FETCHERS["ah"] = lambda: [dict(offer) for offer in OFFERS]
        offers.sync("ah")
        auth.set_setting(mealie.SETTING_WEEK_START, "1")
        summary: dict[str, Any] = backup.write_backup(target.resolve(), links=False)
        print(summary)
    finally:
        data.cleanup()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("gebruik: make_backup_3131.py <worktree van 3.13.1> <doelbestand>")
    main(Path(sys.argv[1]).resolve(), Path(sys.argv[2]))
