# CLAUDE.md

Afspraken voor het werken aan MealieShopper (voor Claude Code en andere AI-hulpen).

## Releases

**Bij elke nieuwe versie bouw je een nieuwe GitHub release.** Een versie ophogen zonder release is niet af.

1. Verhoog `VERSION` (alleen `X.Y.Z`).
2. Schrijf bovenaan `CHANGELOG.md` een kopje `## vX.Y.Z` met alle features en functies, fixes, verwijderde dingen en de pull requests (en eventuele issues) sinds de vorige versie. Die tekst wordt de release-tekst op GitHub.
3. Na het mergen naar `main` maakt `.github/workflows/release.yml` de tag en de release zelf. Controleer dat de release er staat; zo niet, start de workflow dan handmatig (`workflow_dispatch`).

`tests/test_release.py` faalt als `VERSION` geen kopje in `CHANGELOG.md` heeft.

## Werkwijze

- Maak alleen een pull request als de gebruiker erom vraagt, en merge alleen op verzoek.
- Houd Nederlands en Engels gelijk: de interface (`static/i18n/`), de README's (`README.md` en `README.en.md`) en de voorbeeld-`.env`-bestanden.
- Draai de tests met `python -m unittest discover -s tests`.
