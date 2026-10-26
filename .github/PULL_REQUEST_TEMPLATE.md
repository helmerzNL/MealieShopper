## Samenvatting

<!-- Wat verandert er en waarom, in twee of drie zinnen. Koppel het issue: "Closes #123". -->

## Compose- en .env-bestanden

<!--
Verplicht als deze pull request een Compose-bestand (docker-compose*.yml) of een voorbeeld-.env
(.env.example, .env.local.example, .env.unraid.example) raakt. Schrijf het als instructie voor de beheerder, niet als
samenvatting van de diff: de exacte variabelenaam, de standaardwaarde, of die met de hand moet worden toegevoegd,
de exacte regel in Compose als die is veranderd, en of een bestaande installatie ongewijzigd blijft werken of vóór de
volgende update moet worden aangepast.
Geldt het niet, schrijf dan precies: "Geen wijzigingen in Compose- of .env-bestanden in deze pull request."
-->

## Grenzen

<!--
Verplicht als deze pull request een grens voor grootte, aantal of tijd verandert (bijvoorbeeld de 200 MB van een
back-upbestand of de 120 seconden van Gunicorn). Noem de nieuwe waarde als getal waar een beheerder iets mee kan
("de grootste aanvraag is nu 60 MB; wat ervoor staat moet minstens dat doorlaten"), niet als instelling van één
proxy. Anders: "Geen wijzigingen in grenzen in deze pull request."
-->

## Versie en release

<!--
Een nieuwe versie hoort bij een nieuwe GitHub release (zie CLAUDE.md). Vul in wat van toepassing is.
Alleen documentatie of tests, zonder invloed op de app? Schrijf: "Geen nieuwe versie nodig."
-->

- [ ] `VERSION` is verhoogd naar `X.Y.Z`, **of** deze pull request is geen nieuwe versie.
- [ ] `CHANGELOG.md` heeft bovenaan een kopje `## vX.Y.Z` met de features en functies, fixes, verwijderde dingen en de pull requests en issues sinds de vorige versie. Die tekst wordt de releasetekst.
- [ ] Na het mergen naar `beta` staat de pre-release `vX.Y.Z-beta` er (anders `release.yml` met `workflow_dispatch` starten).

## Checklist

- [ ] De pull request richt zich op `beta`.
- [ ] `python -m unittest discover -s tests` slaagt.
- [ ] Nederlands en Engels zijn gelijk: de interface (`static/i18n/`), `README.md` en `README.en.md`, en de voorbeeld-`.env`-bestanden.
- [ ] Er staan geen geheimen of `.env`-bestanden in deze pull request.
- [ ] Een nieuwe tabel met gegevens staat in `backup.DATA_TABLES` (of bewust in `EXCLUDED_TABLES`) en in `tests/test_backup.py`.
- [ ] Wat een standaard is (passkeys, OAuth, JWT, TOTP en dergelijke) gebruikt een bestaande open-sourcebibliotheek, hier genoemd, of de uitzondering is uitgelegd.
- [ ] De documentatie is bijgewerkt waar nodig.
