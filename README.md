# MealieShopper

MealieShopper is een kleine Python/Flask app die Mealie koppelt aan Albert Heijn:

- AH/Allerhande recepten zoeken en importeren in Mealie.
- Recepten importeren via URL.
- Weekmenu uit Mealie ophalen en ingredienten naar AH zoeken.
- AH OAuth-login opslaan en gebruiken voor AH lijstjes en het winkelmandje.
- Passkey/WebAuthn login voor de app.

## Configuratie

Alle omgevingsvariabelen zijn optioneel: bij de eerste start loopt de app je door de
beveiliging en de koppelingen (zie *Eerste start*). Zet ze in je lokale shell, een `.env`-loader of
de containeromgeving (`.env.local.example` en `.env.unraid.example` laten alles zien):

```env
# Sterk aanbevolen: lang, vast en willekeurig (openssl rand -hex 32)
MEALIESHOPPER_AUTH_SECRET=<lange-stabiele-random-string>
MEALIESHOPPER_DATA_DIR=./data
MEALIESHOPPER_PUBLIC_BASE_URL=http://localhost:8000

# Alleen voor passkeys, met een domeinnaam en HTTPS
RP_NAME=MealieShopper
RP_ID=mealieshopper.jouwdomein.nl
RP_ORIGINS=https://mealieshopper.jouwdomein.nl

# Optioneel: koppel Mealie en AH liever in de app (Beheer)
MEALIE_URL=https://mealie.jouwdomein.nl
MEALIE_API_TOKEN=
AH_REFRESH_TOKEN=

# Noodgreep: inloggen helemaal uitzetten
PASSKEY_AUTH_ENABLED=false
```

Je hoeft geen geheim in te stellen. Zonder `MEALIESHOPPER_AUTH_SECRET` maakt de app bij de eerste start
zelf een willekeurig geheim en bewaart dat in de data-map (`secret.key`, alleen leesbaar voor de
eigenaar). Daaruit volgen twee aparte sleutels: een voor de sessies en een voor de versleuteling van je
opgeslagen koppelingen (Mealie-token, AH-token) en het MFA-geheim. **Neem de hele data-map, `secret.key`
ook, mee in je back-ups**: zonder dat bestand zijn de koppelingen en sessies onleesbaar en koppel je
Mealie en Albert Heijn opnieuw (de app laat dat zien als "niet gekoppeld").

Zet je zelf `MEALIESHOPPER_AUTH_SECRET`, dan staat de sleutel niet op schijf naast de database, bijvoorbeeld
omdat je hem uit een secret manager haalt. Gebruik dan een lange, vaste, willekeurige waarde
(`openssl rand -hex 32`) en verander hem niet achteraf, want dan moet je alles opnieuw koppelen.

### Mealie koppelen

Koppel Mealie in de app: **Beheer > Mealie**. Vul het adres van je Mealie in en plak een API-token
(in Mealie: je profiel, onderdeel API-tokens, `Token genereren`). MealieShopper controleert het adres
en de token eerst bij Mealie zelf; klopt er iets niet, dan krijg je een duidelijke melding en wordt er
niets bewaard. Daarna staat het adres en de token versleuteld in de SQLite database onder
`MEALIESHOPPER_DATA_DIR`; de token wordt nooit meer getoond. Een nieuw weekmenu laden of een recept
importeren werkt direct, zonder herstart.

`MEALIE_URL` en `MEALIE_API_TOKEN` blijven werken, maar zijn optioneel. Een koppeling uit de app gaat
voor op de omgevingsvariabelen; de twee bronnen worden nooit gemengd, zodat een token nooit naar een
ander adres gaat. Met `Ontkoppelen` verwijder je de koppeling uit de app, waarna een eventuele
omgevingsconfiguratie weer geldt.

**Let op:** zonder `MEALIESHOPPER_AUTH_SECRET` (of `JWT_SECRET`) wordt de sleutel voor je opgeslagen
koppelingen afgeleid van de omgevingsvariabele `MEALIE_URL`. Stel daarom eerst
`MEALIESHOPPER_AUTH_SECRET` in voordat je `MEALIE_URL` uit je omgeving haalt of wijzigt; anders kan
MealieShopper je opgeslagen koppelingen (ook die van Albert Heijn) niet meer ontsleutelen en moet je
opnieuw koppelen. De app waarschuwt hiervoor in Beheer > Mealie.

`AH_REFRESH_TOKEN` is optioneel en dient alleen als startwaarde. Normaal koppel
je via de tab `AH koppelen`; MealieShopper slaat de refresh token daarna
versleuteld op in de SQLite database onder `MEALIESHOPPER_DATA_DIR`. AH kan de
refresh token bij elke vernieuwing vervangen; de nieuwste waarde wordt steeds in
de database bewaard en gaat voor op de omgevingsvariabele. Houd de data-map dus
op persistente opslag en draai één instance.

### AH koppelen

De login loopt direct via `login.ah.nl` in je eigen browser (op een computer):

1. Klik op `Inloggen bij Albert Heijn`; er opent een nieuw tabblad.
2. Open in dat tabblad de ontwikkelaarsconsole (`F12`) voordat je inlogt.
3. Log in. De pagina lijkt vast te lopen, maar in de console staat een rode
   regel met `appie://login-exit?code=...`.
4. Plak die regel in MealieShopper en klik op `Koppelen`. De code werkt maar
   een keer en verloopt snel. Een foutmelding over een verlopen of gebruikte
   code betekent: opnieuw inloggen.

Met `Koppeling controleren` zie je of de opgeslagen token nog werkt. De
ingebouwde proxy onder `/api/ah/auth/proxy` bestaat nog, maar wordt niet meer
door de interface gebruikt.
`MEALIESHOPPER_AUTH_SECRET` moet stabiel blijven; wijzigen logt bestaande
sessies uit. De passkeys zelf worden opgeslagen in SQLite onder
`MEALIESHOPPER_DATA_DIR`.

## Weekmenu met afbeeldingen

`Weekmenu laden` toont bij elk gerecht het plaatje uit Mealie. De browser vraagt het plaatje
aan MealieShopper (`/api/mealie/recipes/<id>/image`); die haalt het met je API-token bij Mealie
op en bewaart het in de SQLite database (tabel `mealie_images`). Zo werkt het ook als je Mealie
niet rechtstreeks vanaf je telefoon bereikbaar is, en vraagt een volgende keer niets meer aan
Mealie. Verandert het plaatje in Mealie, dan haalt MealieShopper het opnieuw op; is Mealie
onbereikbaar, dan blijft het oude plaatje staan. Recepten zonder plaatje tonen alleen tekst.

## Weekmenu naar het AH-mandje

`Voeg toe aan Albert Heijn mandje` zoekt per ingredient de beste vijf producten en rekent uit
hoeveel verpakkingen je nodig hebt, met de verpakkingsgrootte die AH bij het product geeft:
6 eieren bij "10 stuks" is een doosje, 500 g bij "250 g" zijn er twee, en "2 el" olie is een
fles. Het aantal is altijd 1 tot en met 99.

Is de app ergens niet zeker van (onbekende verpakkingsgrootte, gewicht tegen stuks, of meer
dan 10 verpakkingen), dan verschijnt een controlelijst. Daar kies je per regel het product en
het aantal verpakkingen, vink je aan wat erin moet en bevestig je. Is er nergens twijfel, dan
gaat het mandje meteen vol.

## AH Items

Het tabblad `AH Items` heeft twee onderdelen:

- **AH Recepten**: je bewaarde Allerhande-recepten (de receptencollectie van je
  AH-account) ophalen en in Mealie importeren. Op een telefoon staan minstens twee
  recepten naast elkaar, op een groter scherm zoveel als er passen; bovenaan zoek je in je
  recepten. `Ophalen bij AH` (daarna `Vernieuwen`) haalt alle recepten op en bewaart ze in
  MealieShopper (tabellen `ah_saved_recipes` en `ah_saved_recipes_meta`). Het tabblad zelf
  toont daarna de lokale kopie en roept AH niet meer aan, tot je zelf vernieuwt; mislukt een
  vernieuwing, dan blijft de vorige kopie staan. Dit gebruikt alleen je AH-koppeling;
  een wachtwoord of browser is niet nodig. Het importeren haalt de receptgegevens
  via de AH-API op en geeft ze als schema.org-recept aan Mealie, omdat AH de
  websitepagina's voor servers blokkeert.
- **AH Lijstjes**: je favoriete AH-lijstjes als kaarten naast elkaar (op een telefoon
  minstens twee). Het zoekveld bovenaan filtert terwijl je typt, op de naam van het
  lijstje en op de producten in gesynchroniseerde lijstjes. Een lijstje opent op dezelfde
  plek; de pijl linksboven (of de terugknop van je telefoon) brengt je terug naar het
  overzicht, met je zoekterm en scrollpositie. Met `Synchroniseren` (per lijstje) of
  `Alles synchroniseren` bewaart MealieShopper een kopie van het lijstje, inclusief
  productnaam, merk, inhoud, prijs en afbeelding, in de SQLite database onder
  `MEALIESHOPPER_DATA_DIR` (tabellen `ah_lists` en `ah_list_items`). Een geopend
  lijstje toont de kopie als die er is; de producten linken naar ah.nl.
  Werkt de AH-koppeling niet, dan blijven de gesynchroniseerde lijstjes zichtbaar
  en staat er een melding met de AH-fout. Een nieuwe synchronisatie vervangt de
  kopie; mislukt die, dan blijft de vorige kopie staan.

## Opgeruimd

De AH website-koppeling (inloggen met e-mailadres en wachtwoord via een headless
Chromium) en de Jumbo-koppeling zijn verwijderd, samen met Selenium en Chromium in
de Docker-image. Bij het opstarten wist MealieShopper de eventueel nog opgeslagen
`AH_USERNAME`, `AH_PASSWORD`, `AH_COOKIES` en `JUMBO_*`-gegevens uit de database.
De omgevingsvariabelen `AH_USERNAME`, `AH_PASSWORD`, `JUMBO_USERNAME` en
`JUMBO_PASSWORD` worden niet meer gelezen en kunnen uit je `.env` of compose.

## Huisstijl

Het beeldmerk (een boodschappentasje met een M), de kleuren, de typografie en de PWA-instellingen
staan beschreven in [`docs/huisstijl.md`](docs/huisstijl.md). Beeldbestanden staan in
`static/brand/`, lettertypen in `static/fonts/`. Herbouw de exports met
`python scripts/build_brand.py` (Pillow nodig, alleen tijdens het exporteren).

## Eerste start (onboarding)

Op een nieuwe installatie opent MealieShopper een installatiehulp van vier stappen:

1. **Beveiliging.** De app kijkt hoe je hem opent.
   - *Met een domeinnaam en HTTPS* (ook via `RP_ID` en een reverse proxy): je kiest een
     **passkey** (aanbevolen) of een gebruikersnaam met wachtwoord.
   - *Zonder domeinnaam* (IP-adres, `localhost`, `.local`, of zonder HTTPS): passkeys werken
     dan niet. Je kunt **gebruikersnaam en wachtwoord** instellen, of bewust **zonder
     beveiliging** verder gaan (met een waarschuwing en een bevestiging). Na het wachtwoord
     kun je **tweestapsverificatie** (TOTP, een authenticator-app) aanzetten, ook optioneel.
     Je krijgt dan 8 herstelcodes die je één keer te zien krijgt.
2. **Mealie koppelen**: uitleg voor het API-token, met het formulier (adres en token) meteen in de
   stap. MealieShopper controleert ze bij Mealie voordat er iets wordt bewaard; daarna ga je door.
3. **Albert Heijn koppelen**: uitleg voor de inlog via de browserconsole, met de inlogknop en het
   plakveld in de stap (alleen op een computer). Overslaan kan; je kunt het later onder Beheer doen.
4. **Klaar**: een overzicht van wat er staat. Ontbreekt er een koppeling, dan brengt een knop je
   terug naar die stap, zonder de installatiehulp te verlaten.

Een bestaande installatie (met een passkey of wachtwoord) ziet de installatiehulp niet.
Staat inloggen uit via `PASSKEY_AUTH_ENABLED=false`, dan valt de beveiligingsstap weg.
Wachtwoord, tweestapsverificatie en passkeys beheer je daarna onder Beheer > Beveiliging.

Over de beveiliging van het wachtwoord:

- Het wachtwoord wordt met scrypt gehasht en nooit bewaard of gelogd. Minstens 10 tekens.
- Na 5 mislukte pogingen per IP-adres (of 25 in totaal) is inloggen 15 minuten geblokkeerd.
- Het TOTP-geheim staat versleuteld in de database, een code werkt één keer, en een
  herstelcode werkt één keer (alleen de SHA-256 staat in de database).
- Een nieuw wachtwoord of uitgezette tweestapsverificatie logt andere apparaten uit.
- Zolang de eerste inlogmethode nog niet is ingesteld kan wie het adres als eerste opent de
  installatie claimen (dat gold al voor de eerste passkey). Doe de eerste start daarom op een
  netwerk dat je vertrouwt, of meteen nadat de container draait.
- Het IP-adres komt uit `X-Forwarded-For` van je reverse proxy (een stap); zet MealieShopper
  daarom niet rechtstreeks op internet zonder proxy die dat veld zelf instelt.

## Taal

De interface is er in het Nederlands en het Engels. Met de vlaggetjes (rechtsboven in de interface,
op de inlogpagina en in de installatiehulp) wissel je; de keuze blijft in je browser bewaard
(`localStorage`) en staat ook in de cookie `ms_lang`, zodat de meldingen van de server (inloggen,
wachtwoord, tweestapsverificatie, beveiligingsstap) in dezelfde taal komen. Zonder keuze volgt de
app de taal van je browser, en anders het Nederlands. Wat Mealie en Albert Heijn zelf teruggeven
(receptnamen, productnamen en hun foutmeldingen) blijft zoals zij het sturen.

De teksten staan in `static/i18n/nl.js` en `static/i18n/en.js`, één tekst per regel. In de HTML staat
de Nederlandse tekst gewoon in de pagina, met `data-i18n="sleutel"` erbij; in de scripts gebruik je
`t("sleutel", { naam })` (gewone tekst), `th(...)` (tekst met opmaak; waarden worden ge-escaped) en
`tn("sleutel", aantal)` (meervoud: `sleutel.one` en `sleutel.other`). Een taal toevoegen betekent een
bestand `static/i18n/<taal>.js`, een vlag en de taal in `static/i18n.js` en `mealieshopper/i18n.py`.
`tests/test_i18n.py` bewaakt dat beide woordenboeken dezelfde sleutels en plaatshouders hebben, dat elke
gebruikte sleutel bestaat en dat de HTML overeenkomt met `nl.js`.

## Over en het versienummer

Het tabblad **Over** toont het versienummer als `vX.Y.Z (commit)`, de integraties en componenten die
MealieShopper gebruikt (met de versies van de geïnstalleerde Python-pakketten) en een link om me een
koffie te geven.

Het versienummer staat in het bestand `VERSION` (alleen `X.Y.Z`); verhoog het bij een release. De commit
komt uit de build: de Dockerfile heeft het build-argument `GIT_COMMIT` (de GitHub Actions workflow zet het
op `github.sha`) en lokaal wordt hij uit git gelezen. Voor een handmatige build:

```bash
docker build --build-arg GIT_COMMIT=$(git rev-parse HEAD) -t mealieshopper .
```

`MEALIESHOPPER_VERSION` en `MEALIESHOPPER_COMMIT` kunnen beide waarden overschrijven.

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
  -e MEALIESHOPPER_AUTH_SECRET=... `
  mealieshopper
```

## Unraid met Docker Compose

Kopieer `.env.unraid.example` naar `.env.unraid` en pas aan wat je wilt (alles is optioneel; zonder
dat bestand start de container ook, met Docker Compose 2.24 of nieuwer) en start daarna:

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
