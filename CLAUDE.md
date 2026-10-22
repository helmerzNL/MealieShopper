# CLAUDE.md

Afspraken voor het werken aan MealieShopper (voor Claude Code en andere AI-hulpen).

## Branches

**Er gaan geen pull requests meer rechtstreeks naar `main`.** Alle commits en pull requests gaan naar `beta`; daar draait de beta/dev-build, zodat we kunnen blijven ontwikkelen zonder de stabiele versie te raken.

- Werk op een eigen branch en richt de pull request op `beta` (nooit op `main`).
- Elke push naar `beta` bouwt de container `ghcr.io/helmerznl/mealieshopper:beta` (en `X.Y.Z-beta`) en maakt een pre-release `vX.Y.Z-beta` (`docker.yml` en `release.yml`).
- `main` is de stabiele lijn. Die krijgt alleen een pull request vanuit `beta` (de promotie), op verzoek van de gebruiker. Dat bouwt `latest` en de stabiele release `vX.Y.Z`. `pr-target.yml` laat elke andere pull request naar `main` falen.
- De check `Pull request target` en het verbod op rechtstreeks pushen naar `main` zet de eigenaar aan in de branch protection van GitHub; dat kan niet vanuit de code.

## Releases

**Bij elke nieuwe versie bouw je een nieuwe GitHub release.** Een versie ophogen zonder release is niet af.

1. Verhoog `VERSION` (alleen `X.Y.Z`).
2. Schrijf bovenaan `CHANGELOG.md` een kopje `## vX.Y.Z` met alle features en functies, fixes, verwijderde dingen en de pull requests (en eventuele issues) sinds de vorige versie. Die tekst wordt de release-tekst op GitHub.
3. Na het mergen naar `beta` maakt `.github/workflows/release.yml` de pre-release `vX.Y.Z-beta`; na de promotie naar `main` de stabiele release `vX.Y.Z`. Controleer dat de release er staat; zo niet, start de workflow dan handmatig (`workflow_dispatch`).

`tests/test_release.py` faalt als `VERSION` geen kopje in `CHANGELOG.md` heeft.

## Werkwijze

- Maak alleen een pull request als de gebruiker erom vraagt (naar `beta`, zie Branches), en merge alleen op verzoek.
- Houd Nederlands en Engels gelijk: de interface (`static/i18n/`), de README's (`README.md` en `README.en.md`) en de voorbeeld-`.env`-bestanden.
- Draai de tests met `python -m unittest discover -s tests`.
