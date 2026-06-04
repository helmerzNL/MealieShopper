# MealieShopper

MealieShopper is een kleine Python/Flask app die Mealie koppelt aan Albert Heijn:

- AH/Allerhande recepten zoeken en importeren in Mealie.
- Recepten importeren via URL.
- Weekmenu uit Mealie ophalen en ingredienten naar AH zoeken.
- AH OAuth-login opslaan en gebruiken voor AH lijstjes en het winkelmandje.
- Passkey/WebAuthn login voor de app.

## Configuratie

Zet deze omgevingsvariabelen in je lokale shell, `.env`-loader of containeromgeving:

```env
MEALIE_URL=https://mealie.jouwdomein.nl
MEALIE_API_TOKEN=
AH_REFRESH_TOKEN=
AH_AUTH_REDIRECT_URI=appie://login-exit
PASSKEY_AUTH_ENABLED=true
MEALIESHOPPER_AUTH_SECRET=<lange-stabiele-random-string>
MEALIESHOPPER_DATA_DIR=./data
MEALIESHOPPER_PUBLIC_BASE_URL=http://localhost:8000
RP_NAME=MealieShopper
RP_ID=localhost
RP_ORIGINS=http://localhost:8000
```

`AH_REFRESH_TOKEN` is optioneel. Normaal log je in via de tab `AH koppelen`;
MealieShopper slaat de refresh token daarna versleuteld op in de SQLite database
onder `MEALIESHOPPER_DATA_DIR`. De omgevingsvariabele blijft bruikbaar als
override voor installaties die secrets liever via Docker/Unraid beheren.
De AH OAuth-login gebruikt standaard `AH_AUTH_REDIRECT_URI=appie://login-exit`.
Als AH niet direct naar MealieShopper terugstuurt, plak je na AH-login de code
of volledige `appie://login-exit?code=...` URL in de tab `AH koppelen`.
`MEALIESHOPPER_AUTH_SECRET` moet stabiel blijven; wijzigen logt bestaande
sessies uit. De passkeys zelf worden opgeslagen in SQLite onder
`MEALIESHOPPER_DATA_DIR`.

## Passkeys

Bij een nieuwe installatie toont MealieShopper eerst een setupscherm voor de
eerste owner passkey. Zodra die bestaat, zijn de app en API-routes beschermd
met een HttpOnly sessiecookie.

Na het inloggen kun je via de tab `Beveiliging` extra passkeys toevoegen,
bijvoorbeeld voor je telefoon en laptop. Daar kun je oude passkeys ook
verwijderen; de laatste passkey blijft beschermd tegen verwijderen zolang
passkey-auth aan staat.

Voor passkeys moet de browser een secure context hebben. `localhost` werkt via
HTTP, maar op Unraid of een ander LAN-hostname heb je normaal HTTPS nodig via
een reverse proxy. Zet dan:

```env
MEALIESHOPPER_PUBLIC_BASE_URL=https://mealieshopper.sandmount.nl
RP_ID=mealieshopper.sandmount.nl
RP_ORIGINS=https://mealieshopper.sandmount.nl
```

`RP_ID` is alleen de hostname zonder schema of poort. `RP_ORIGINS` is de
volledige URL waarmee je de app in de browser opent.

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
  -v /mnt/user/appdata/mealieshopper:/data `
  -e MEALIE_URL=https://mealie.jouwdomein.nl `
  -e MEALIE_API_TOKEN=... `
  -e AH_REFRESH_TOKEN=... `
  -e MEALIESHOPPER_AUTH_SECRET=... `
  -e RP_ID=localhost `
  -e RP_ORIGINS=http://localhost:8000 `
  mealieshopper
```

## Unraid met Docker Compose

Kopieer `.env.unraid.example` naar `.env.unraid`, vul de waarden in en start daarna:

```bash
docker compose up -d
```

De compose gebruikt standaard:

```text
ghcr.io/helmerznl/mealieshopper:latest
```

Voor Sandmount/Unraid staat het voorbeeld op:

```env
RP_ID=mealieshopper.sandmount.nl
RP_ORIGINS=https://mealieshopper.sandmount.nl
```

De meegeleverde compose publiceert de app op hostpoort `5959` en mount de
SQLite data vast naar:

```yaml
ports:
  - "5959:8000"
volumes:
  - "/mnt/user/appdata/mealieshopper:/data"
```

Krijg je direct een `Internal Server Error` of een melding dat de auth database
niet bereikbaar is, controleer dan of de appdata-map bestaat en schrijfbaar is:

```bash
mkdir -p /mnt/user/appdata/mealieshopper
```

Controleer ook dat je compose deze mount heeft:

```yaml
volumes:
  - "/mnt/user/appdata/mealieshopper:/data"
```

Op Unraid kun je de rechten herstellen met:

```bash
mkdir -p /mnt/user/appdata/mealieshopper
chown -R nobody:users /mnt/user/appdata/mealieshopper
chmod -R ug+rwX /mnt/user/appdata/mealieshopper
```

## GitHub Container Registry

De workflow in `.github/workflows/docker.yml` bouwt en pusht automatisch naar:

```text
ghcr.io/helmerznl/mealieshopper:latest
```

Dit gebeurt bij pushes naar `main` en kan ook handmatig via `workflow_dispatch`.
