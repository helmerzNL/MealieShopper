# MealieShopper

MealieShopper is een kleine Python/Flask app die Mealie koppelt aan Albert Heijn:

- AH/Allerhande recepten zoeken en importeren in Mealie.
- Recepten importeren via URL.
- Weekmenu uit Mealie ophalen en ingredienten naar AH zoeken.
- AH refresh token controleren en gebruiken voor het winkelmandje.

## Configuratie

Zet deze omgevingsvariabelen in je lokale shell, `.env`-loader of containeromgeving:

```env
MEALIE_URL=https://mealie.jouwdomein.nl
MEALIE_API_TOKEN=
AH_REFRESH_TOKEN=
```

`AH_REFRESH_TOKEN` is alleen nodig voor het vullen van het AH winkelmandje.

## Lokaal draaien

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python run.py
```

De app luistert standaard op `http://localhost:8000`.

## Docker

```powershell
docker build -t mealieshopper .
docker run --rm -p 8000:8000 `
  -e MEALIE_URL=https://mealie.jouwdomein.nl `
  -e MEALIE_API_TOKEN=... `
  -e AH_REFRESH_TOKEN=... `
  mealieshopper
```

## GitHub Container Registry

De workflow in `.github/workflows/docker.yml` bouwt en pusht automatisch naar:

```text
ghcr.io/<owner>/<repo>:latest
```

Dit gebeurt bij pushes naar `main` en kan ook handmatig via `workflow_dispatch`.
