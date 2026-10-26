## Samenvatting / Summary

<!--
NL: Wat verandert er en waarom, in twee of drie zinnen. Koppel het issue: "Closes #123".
EN: What changes and why, in two or three sentences. Link the issue: "Closes #123".
-->

## Compose- en .env-bestanden / Compose and .env files

<!--
NL: Verplicht als deze pull request een Compose-bestand (docker-compose*.yml) of een voorbeeld-.env
(.env.example, .env.local.example, .env.unraid.example) raakt. Schrijf het als instructie voor de beheerder, niet als
samenvatting van de diff: de exacte variabelenaam, de standaardwaarde, of die met de hand moet worden toegevoegd,
de exacte regel in Compose als die is veranderd, en of een bestaande installatie ongewijzigd blijft werken of vóór de
volgende update moet worden aangepast.
Geldt het niet, schrijf dan precies: "Geen wijzigingen in Compose- of .env-bestanden in deze pull request."

EN: Required when this pull request touches a Compose file (docker-compose*.yml) or an example .env
(.env.example, .env.local.example, .env.unraid.example). State it as an operator instruction, not a diff summary:
the exact variable name, its default, whether it must be added by hand, the exact Compose line if it changed, and
whether an existing installation keeps working untouched or must be edited before the next update.
If nothing applies, write exactly: "No changes to Compose or .env files in this pull request."
-->

## Grenzen / Limits

<!--
NL: Verplicht als deze pull request een grens voor grootte, aantal of tijd verandert (bijvoorbeeld de 200 MB van een
back-upbestand of de 120 seconden van Gunicorn). Noem de nieuwe waarde als getal waar een beheerder iets mee kan
("de grootste aanvraag is nu 60 MB; wat ervoor staat moet minstens dat doorlaten"), niet als instelling van één
proxy. Anders: "Geen wijzigingen in grenzen in deze pull request."

EN: Required when this pull request raises or lowers a size, rate or timeout limit (for example the 200 MB of a
backup file or the 120 seconds of Gunicorn). State the new value as a number an operator can act on ("the largest
request is now 60 MB; whatever runs in front must allow at least that"), never as one proxy's setting. Otherwise
write "No limit changes in this pull request."
-->

## Versie en release / Version and release

<!--
NL: Een nieuwe versie hoort bij een nieuwe GitHub release (zie CLAUDE.md). Vul in wat van toepassing is.
Alleen documentatie of tests, zonder invloed op de app? Schrijf: "Geen nieuwe versie nodig."
EN: A new version comes with a new GitHub release (see CLAUDE.md). Fill in what applies.
Only documentation or tests, with no effect on the app? Write: "No new version needed."
-->

- [ ] `VERSION` is verhoogd naar `X.Y.Z`, **of** deze pull request is geen nieuwe versie. / `VERSION` is raised to `X.Y.Z`, **or** this pull request is not a new version.
- [ ] `CHANGELOG.md` heeft bovenaan een kopje `## vX.Y.Z` met de features en functies, fixes, verwijderde dingen en de pull requests en issues sinds de vorige versie; die tekst wordt de releasetekst. / `CHANGELOG.md` has a heading `## vX.Y.Z` at the top with the features and functions, fixes, removed things and the pull requests and issues since the previous version; that text becomes the release text.
- [ ] Na het mergen naar `beta` staat de pre-release `vX.Y.Z-beta` er (anders `release.yml` starten met `workflow_dispatch`). / After merging to `beta` the pre-release `vX.Y.Z-beta` exists (otherwise start `release.yml` with `workflow_dispatch`).

## Checklist

- [ ] De pull request richt zich op `beta`. / The pull request targets `beta`.
- [ ] `python -m unittest discover -s tests` slaagt. / `python -m unittest discover -s tests` passes.
- [ ] Nederlands en Engels zijn gelijk: de interface (`static/i18n/`), `README.md` en `README.en.md`, en de voorbeeld-`.env`-bestanden. / Dutch and English match: the interface (`static/i18n/`), `README.md` and `README.en.md`, and the example `.env` files.
- [ ] Er staan geen geheimen of `.env`-bestanden in deze pull request. / No secrets or `.env` files are in this pull request.
- [ ] Een nieuwe tabel met gegevens staat in `backup.DATA_TABLES` (of bewust in `EXCLUDED_TABLES`) en in `tests/test_backup.py`. / A new data table is in `backup.DATA_TABLES` (or deliberately in `EXCLUDED_TABLES`) and in `tests/test_backup.py`.
- [ ] Wat een standaard is (passkeys, OAuth, JWT, TOTP en dergelijke) gebruikt een bestaande open-sourcebibliotheek, hier genoemd, of de uitzondering is uitgelegd. / Anything defined by a standard (passkeys, OAuth, JWT, TOTP and the like) uses an existing open-source library, named here, or the exception is explained.
- [ ] De documentatie is bijgewerkt waar nodig. / Documentation is updated where relevant.
