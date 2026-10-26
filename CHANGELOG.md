# Changelog

Elke release heeft hier een eigen kopje `## vX.Y.Z` en wordt bij het mergen naar `main` automatisch een GitHub release (zie [Releases](#releases)). De tekst onder het kopje is de release-tekst op GitHub.

## v2.77.0

*English summary: The Offers screen has a new Price history tab. Search for a product and see in a chart how often it was on offer and at what price: every time is a bar over the store's validity period, with a dashed line for the regular price and an arrow for an offer that is still running. The history now also keeps the start date of each offer, so the bars show the real offer week; the same deal in a new week counts as a new time.*

### Nieuw in 2.77.0

- **Nieuw subtab Prijsgeschiedenis bij Aanbiedingen.** Zoek een product van de gekozen winkel (AH, Picnic of Jumbo) en kies het uit de lijst. De grafiek toont elke keer dat het in de aanbieding was als een balk op de actieprijs, over de looptijd van de aanbieding; een aanbieding die nog loopt eindigt in een pijl, en een stippellijn geeft de gewone prijs. Daarboven staan het aantal keren, de laagste en de hoogste actieprijs, de gewone prijs en wanneer het product voor het laatst is gezien; eronder staat een tabel met dezelfde periodes (ook voor schermlezers). De tab werkt in licht en donker en op een telefoon. Hij gebruikt de bestaande route `GET /api/offers/history`, die ook voor alle ingelogde gebruikers openstaat.
- **De geschiedenis bewaart nu de begindatum van de aanbieding** (`valid_from` in `offer_history`, wordt vanzelf toegevoegd). De balken tonen daardoor de echte week van de winkel in plaats van het moment dat MealieShopper het product toevallig zag. De route geeft per periode `validFrom`, `start`, `end` en `open`, en per product `regularPrice` en `highestPrice`.
- **Dezelfde actie in een nieuwe week telt als een nieuwe keer.** Begint de looptijd op een andere dag dan die van de open periode, dan sluit MealieShopper die en begint een nieuwe, ook als prijs en label gelijk zijn. Zo klopt *3 keer in de aanbieding* ook bij een actie die elke week terugkomt.

#### Goed om te weten bij het bijwerken

- **De geschiedenis van vóór deze versie heeft nog geen begindatum van de winkel.** Die rijen tonen de balk van de eerste tot de laatste synchronisatie; nieuwe synchronisaties vullen de begindatum aan zonder iets op te splitsen. Een back-up van een eerdere versie laat zich gewoon terugzetten.
- De pagina laadt een nieuw script (`offers_history.js`); laad de pagina één keer opnieuw als de tab niet vanzelf verschijnt.
- Compose- of `.env`-bestanden zijn niet gewijzigd.

## v2.76.0

*English summary: Jumbo offers now carry their period too. Jumbo gives each promotion a start and an end, so the Offers screen can show "Valid Wed 21 Oct to Tue 27 Oct" above the Jumbo offers, like it does for AH Bonus. The Jumbo explorer asks for the real fields.*

### Nieuw in 2.76.0

- **De looptijd van de Jumbo-aanbiedingen.** Jumbo geeft per promotie een begin en een einde (`start { iso }` en `end { iso }`, met een echt Jumbo-antwoord bevestigd). Bij de aanbiedingen van Jumbo staat nu dezelfde regel bovenaan als bij AH, bijvoorbeeld *Geldig wo 21 okt t/m di 27 okt*, en elke kaart toont *t/m* de einddatum. Een verlopen Jumbo-aanbieding verdwijnt na de einddatum uit de lijst.
- Heeft een product meer promoties (een weekactie en een seizoensactie), dan telt de promotie die nu loopt en het eerst eindigt. Jumbo's weekactie loopt van woensdag tot en met dinsdag, niet van maandag tot en met zondag.
- Kent Jumbo de velden ooit niet meer, dan zoekt de app verder zonder looptijd, in plaats van te stoppen met ophalen.
- **De Jumbo-verkenner** (Beheer, Winkels) vraagt de looptijd altijd mee en probeert geen veldnamen meer die niet bestaan (`startDate`, `endDate`, `validFrom`, `validUntil`).

#### Goed om te weten bij het bijwerken

- Synchroniseer de Jumbo-aanbiedingen één keer opnieuw: een kopie van vóór deze versie heeft nog geen looptijd.
- Compose- of `.env`-bestanden zijn niet gewijzigd.

## v2.75.0

*English summary: The AH Bonus now shows the period the offers are valid for at the top of the Offers screen (for example "Valid Mon 26 Oct to Sun 1 Nov"), taken from the start and end dates AH gives per offer. Ongoing "volume discount" offers, which AH gives the end date 31 Dec 2999, no longer show a bogus "until 31 Dec".*

### Nieuw in 2.75.0

- **De looptijd van de bonus staat bovenaan de aanbiedingen.** Boven het aantal aanbiedingen staat nu bijvoorbeeld *Geldig ma 26 okt t/m zo 1 nov*: de looptijd die de meeste aanbiedingen delen. Die komt uit de begin- en einddatum die AH per aanbieding meegeeft (`bonusStartDate` en `bonusEndDate`, met een echt AH-antwoord bevestigd). Geeft een winkel geen datums, dan blijft de regel weg.
- **De begindatum wordt bewaard** (`valid_from` bij de lokale kopie van de aanbiedingen). Een kopie van vóór deze versie krijgt hem bij de volgende synchronisatie; zolang staat er geen periode bovenaan.

### Opgelost in 2.75.0

- **Doorlopende aanbiedingen** ("5% volume voordeel" en dergelijke) hebben bij AH `31-12-2999` als einddatum. Ze stonden daardoor met *t/m 31 dec* op de kaart. Nu hebben ze geen einddatum, en tellen ze niet mee in de looptijd bovenaan.

#### Goed om te weten bij het bijwerken

- Pas de database niet handmatig aan: de nieuwe kolom wordt vanzelf toegevoegd. Een back-up van een eerdere versie laat zich gewoon terugzetten.
- Compose- of `.env`-bestanden zijn niet gewijzigd.

## v2.74.0

*English summary: Settings > System has a new Backup and restore card. One click makes a single file with everything in the database, including the links with Mealie, Albert Heijn, Picnic, Jumbo and the AI keys (encrypted with a password you choose) and the whole history of the (bonus) offers, and restoring it brings the app back to a working state, also on a new server. The restore is checked completely before anything changes, is all or nothing, shows its progress and keeps running on the server if you leave the page.*

### Nieuw in 2.74.0

- **Back-up maken en terugzetten in de app (Instellingen, Systeem).** Eén bestand (`mealieshopper-backup-….json`) met alles wat in de database zit: de koppelingen met Mealie, Albert Heijn, Picnic, Jumbo en de AI-sleutels, de kopieën van je lijstjes, bewaarde recepten, bestellingen en kassabonnen, de aanbiedingen van nu **en de hele historie van de (bonus)aanbiedingen**, de beoordelingen van aanbiedingen bij recepten, de profielen en de keuzes bij AI en MCP. Terugzetten, ook op een nieuwe server, geeft een werkende app.
- **De koppelingen gaan versleuteld mee, met een wachtwoord dat je zelf kiest** (scrypt en Fernet). Het wachtwoord wordt nergens bewaard. Zonder het vinkje *Neem de koppelingen mee* bevat het bestand geen koppelingen en is er geen wachtwoord nodig. Op de nieuwe server worden de koppelingen opnieuw versleuteld met de sleutel van die server.
- **Wat inlogt op MealieShopper zit er nooit in:** passkeys, wachtwoorden, tweestapsverificatie, herstelcodes, uitnodigingen, sessies, de toegang van AI-assistenten (MCP) en `secret.key`. Het auditlog, de bewaarde receptafbeeldingen en het webadres van de server blijven buiten de back-up.
- **Alles of niets.** De server controleert het hele bestand (onderdelen, kolommen, waarden, afbeeldingen) en laat zien wat erin zit voordat er iets verandert; daarna vervangt hij de gegevens in één transactie. Een afgekeurd bestand of een fout halverwege laat alles zoals het was. Een bestand van een nieuwere versie wordt geweigerd met de melding dat je eerst moet bijwerken.
- **Je ziet wat er gebeurt.** Maken, downloaden, uploaden, controleren en vervangen hebben elk een naam en, waar te tellen, een getal en een balk; daarna staat er hoe lang elk deel duurde. Het terugzetten is werk van de server: de pagina verlaten of sluiten stopt niets, en het scherm pakt de voortgang weer op.
- Het patroon volgt [`backup-restore.md`](https://github.com/Flux76HQ/App-Guidance/blob/main/shared/guidelines/backup-restore.md) van App-Guidance, met één bewuste afwijking: de koppelingen mogen mee, versleuteld.

#### Goed om te weten bij het bijwerken

- **Een back-up bevat geen inloggegevens van de app.** Zet je terug op een nieuwe installatie, stel dan eerst het inloggen in en zet daarna de back-up terug.
- **Een reverse proxy moet een back-up doorlaten.** Een back-upbestand mag tot 200 MB zijn (in de praktijk enkele megabytes). Sta je een kleinere aanvraag toe, dan geeft de proxy een foutmelding bij het terugzetten.
- **Het scherm Systeem heeft een nieuw script.** Laad de pagina één keer opnieuw als het scherm niet vanzelf ververst.
- Compose- of `.env`-bestanden zijn niet gewijzigd.

## v2.73.1

### Sinds de vorige stabiele versie (v2.72.1)

Deze release brengt `main` van v2.72.1 naar v2.73.1. De tussenliggende versies (2.72.2 tot en met 2.73.0) waren alleen als pre-release (`-beta`) beschikbaar; dit is wat er in totaal verandert. De volledige tekst per versie staat verderop in dit bestand.

*English summary of everything since v2.72.1: filling the AH basket from the week menu no longer fails when two recipes need the same product (409 "Grocery items cannot be duplicate"); AH product searches are retried when AH drops the connection, and an open order at AH ("Server in order mode", 412) is explained in plain words; in the check list of the week menu you can search for products that were not found, and for an alternative to a product that was found, under a search term of your own; and passkeys are now checked by the open-source py_webauthn library (existing passkeys keep working).*

- **Het mandje vullen vanuit het weekmenu geeft geen 409 meer (2.72.3).** Hadden twee recepten hetzelfde product nodig (bijvoorbeeld melk), dan weigerde AH het met `409 Grocery items cannot be duplicate`. Regels met hetzelfde product worden nu samengevoegd, met de aantallen opgeteld (maximaal 99).
- **Een verbroken verbinding bij AH is niet meer fataal, en een open bestelling wordt uitgelegd (2.72.4).** Zoeken naar producten probeert een verbroken verbinding tot drie keer opnieuw. Weigert AH de boodschappenlijst met `412 Server in order mode`, dan staat er ergens een bestelling open (bijvoorbeeld op een ander apparaat) en zegt MealieShopper dat je die eerst moet afsluiten.
- **Niet gevonden producten zoek je zelf op, en bij een gevonden product zoek je een alternatief (2.73.0 en 2.73.1).** In de controlelijst van het weekmenu heeft elke regel een zoekveld met een eigen zoekterm (bijvoorbeeld "pak choi" voor "paksoi"). Dat geldt voor Albert Heijn, Picnic en Jumbo.
- **Passkeys worden gecontroleerd door py_webauthn (2.72.2).** De eigen WebAuthn-controle is vervangen door de open-sourcebibliotheek [py_webauthn](https://github.com/duo-labs/py_webauthn). Het gedrag blijft hetzelfde en bestaande passkeys blijven werken.

#### Goed om te weten bij het bijwerken

- **Bouw de container opnieuw:** `requirements.txt` heeft nieuwe afhankelijkheden voor passkeys (`webauthn`, `pyasn1`, `pyasn1_modules`, `pyOpenSSL`; `cbor2` en `cryptography` zijn nieuwer). Haal je de image `:latest` op, dan zit dat erin; bouw je zelf, bouw dan opnieuw.
- **Het weekmenu heeft nieuwe knoppen in de controlelijst.** Ververst een scherm niet vanzelf, laad de pagina dan één keer opnieuw (in de geïnstalleerde app: sluiten en weer openen).
- **Het mandje vullen lukt niet zolang er een AH-bestelling open staat.** Sluit die eerst af (bijvoorbeeld in de AH-app of op de website van een ander apparaat) en probeer het opnieuw.

### Nieuw in 2.73.1

- **Ook bij een gevonden product zoek je een alternatief (2.73.1).** In de controlelijst van het weekmenu heeft elke regel met producten nu de knop *Zoek een ander product*. Die klapt een zoekveld uit onder de regel, met de zoekterm erin. Zoek je met een eigen term, dan vervangt de lijst met opties van die regel door de nieuwe resultaten (met het aantal verpakkingen opnieuw berekend uit de oorspronkelijke hoeveelheid) en blijft het vinkje aan. Vindt de zoekopdracht niets, dan staat dat bij de regel en houdt de regel zijn huidige producten. De andere regels blijven zoals je ze had ingesteld. Dit geldt voor Albert Heijn, Picnic en Jumbo. De versie van de statische bestanden is opgehoogd, zodat de browser het nieuwe script en de nieuwe stijlen laadt; laad de pagina één keer opnieuw als het scherm niet vanzelf ververst.

## v2.73.0

*English summary: products that could not be found when filling the basket from the week menu can now be searched for one by one in the check list, under a search term of your own (for example "pak choi" for "paksoi"), and added after all.*

- **Niet gevonden producten zoek je zelf op (2.73.0).** In de controlelijst van het weekmenu heeft elke regel die niet gevonden werd nu een zoekveld met de zoekterm erin (aan te passen) en een knop *Zoeken*. Vind je met je eigen term wel producten, dan wordt de regel een gewone regel met keuzelijst, aantal verpakkingen (berekend uit de oorspronkelijke hoeveelheid) en vinkje, en gaat hij mee in het mandje. Vind je niets, dan staat dat bij de regel en kun je een andere term proberen. Een zoekopdracht die door een storing mislukte (zoals een verbroken verbinding) probeer je op dezelfde manier opnieuw. Wat je bij de andere regels hebt gekozen blijft staan. Dit geldt voor Albert Heijn, Picnic en Jumbo, want ze delen de controlelijst.
- **Boven de lijst staat hoeveel producten nog niet gevonden zijn**, en de melding na het vullen noemt alleen wat echt ontbreekt. De versie van de statische bestanden is opgehoogd, zodat de browser het nieuwe script en de nieuwe stijlen laadt. Laad de pagina één keer opnieuw als het scherm niet vanzelf ververst.
- **Test.** Een enkele regel opnieuw zoeken geeft de oorspronkelijke hoeveelheid en eenheid terug en rekent de verpakkingen daarmee uit.

## v2.72.4

*English summary: AH product searches are retried when AH drops the connection, and a basket fill that AH refuses because an order is open ("Server in order mode", 412) now says so in plain words: close the open order (for example on another device) first.*

- **Een verbroken verbinding bij AH is niet meer fataal (2.72.4).** Het weekmenu zoekt elk ingrediënt apart bij AH, en AH verbrak daarbij af en toe de verbinding (`Connection reset by peer`), waarna dat ingrediënt ontbrak. Zoeken naar producten en bonusproducten probeert het nu tot drie keer, met een korte pauze. Een antwoord van AH met een fout (zoals 500) wordt niet herhaald, en het vullen van het mandje zelf ook niet, zodat er niets dubbel kan binnenkomen.
- **Duidelijke melding bij een open bestelling.** Weigert AH de boodschappenlijst met `412 Server in order mode`, dan staat er ergens een bestelling open, bijvoorbeeld in de AH-app of op de website van een ander apparaat. MealieShopper zegt dat nu: sluit die bestelling eerst af en probeer het daarna opnieuw, in plaats van de ruwe melding te tonen.
- **Tests.** Opnieuw proberen na een verbroken verbinding, opgeven na drie pogingen, geen herhaling bij een foutantwoord, en de melding bij 412.

## v2.72.3

*English summary: filling the AH basket from the week menu no longer fails with "Grocery items cannot be duplicate" (409) when two recipes need the same product; those lines are now merged into one with the quantities added up.*

- **Weekmenu in het AH-mandje geeft geen 409 meer (2.72.3).** Hadden twee recepten in het weekmenu hetzelfde product nodig (bijvoorbeeld melk), dan stuurde MealieShopper dat product twee keer in één verzoek mee en weigerde AH het met `409 Grocery items cannot be duplicate`. Regels met hetzelfde AH-product worden nu vóór het versturen samengevoegd tot één regel, met de aantallen opgeteld (maximaal 99, de grens van AH). Dat geldt ook voor het mandje vullen vanuit een AH-lijstje en voor de aanroep van `/api/ah/cart`.
- **Test.** Dubbele producten gaan één keer naar AH, met opgetelde aantallen, ook als het product-ID als tekst binnenkomt.

## v2.72.2

*English summary: passkeys are now checked by the open-source py_webauthn library instead of our own code; behaviour is the same, and passkeys that were already registered keep working.*

- **Passkeys worden gecontroleerd door py_webauthn (2.72.2).** De eigen controle van de aanmaak- en inlogantwoorden (clientgegevens, uitdaging, origin, RP-ID, vlaggen en handtekening) is vervangen door de open-sourcebibliotheek [py_webauthn](https://github.com/duo-labs/py_webauthn), zoals de bouwregel van Flux76 voorschrijft: een standaard implementeer je met een bestaande module. Het gedrag blijft hetzelfde: alleen ES256, gebruikersaanwezigheid verplicht, verificatie niet (zodat wachtwoordmanagers als Enpass blijven werken) en de tellercontrole blijft uit. Bestaande passkeys blijven werken, de opgeslagen sleutels hebben hetzelfde formaat.
- **Nieuwe afhankelijkheden.** `requirements.txt` krijgt `webauthn`, `pyasn1`, `pyasn1_modules` en `pyOpenSSL`, en `cbor2` en `cryptography` gaan naar de versies die py_webauthn vraagt (`cbor2` 6.1.5, `cryptography` 50.0.2). Ze staan ook bij *Over* en in de README's. Bouw de container opnieuw bij het bijwerken.
- **Tests.** Een passkey zonder gebruikersverificatie (zoals Enpass meldt) kan aanmaken en inloggen; een passkey zonder gebruikersaanwezigheid, van een andere origin of voor een andere uitdaging wordt geweigerd. De versie van de statische bestanden is opgehoogd, zodat de browser de nieuwe teksten bij *Over* laadt.

## v2.72.1

### Sinds de vorige stabiele versie (v2.67.2)

Deze release brengt `main` van v2.67.2 naar v2.72.1. De tussenliggende versies (2.68.0 tot en met 2.72.0) waren alleen als pre-release (`-beta`) beschikbaar; dit is wat er in totaal verandert. De volledige tekst per versie staat verderop in dit bestand.

*English summary of everything since v2.67.2: optional AI help with your own Claude or ChatGPT API key (review the matches between recipe ingredients and offers, and search your recipes for the products on offer; nothing happens without a key); recipe lines are parsed into quantity, unit and ingredient when you import a recipe into Mealie and when Mealie did not analyze them; putting the week menu in the basket now always shows the check list first; and the build you install (`:latest` or `:beta`) is chosen with `MEALIESHOPPER_TAG` in a `.env` file next to `docker-compose.yml`, with a "beta" badge in the app on a beta build.*

- **Het weekmenu gaat nooit meer ongezien in het mandje (2.72.0 en 2.72.1).** De knop in de planner toont altijd eerst de controlelijst, met per ingrediënt het gekozen product, de andere keuzes, het aantal verpakkingen en een vinkje; pas na het bevestigen gaan de producten in het mandje (Albert Heijn, Picnic en Jumbo). Voorheen werd het mandje meteen gevuld als de app nergens twijfelde. De versie van de statische bestanden is opgehoogd, zodat de browser het nieuwe script laadt.
- **Optionele AI-hulp met je eigen sleutel (2.68.0 en 2.69.0).** Onder *Instellingen > Koppelen* vul je een API-sleutel in van Claude (Anthropic) en/of ChatGPT (OpenAI), of zet je `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`. De sleutel wordt versleuteld bewaard en nooit meer getoond. Met een sleutel verschijnt het subtabblad *Recepten* bij Aanbiedingen en krijgt de beheerder de knop *Recepten zoeken met AI*: na een bevestiging met een schatting van het aantal tokens zoekt de AI in je eigen recepten (Mealie, bewaarde AH-recepten, Jumbo-favorieten, Picnic-recepten) bij de producten in de aanbieding. Alleen de tekst van ingrediënten en producten wordt verstuurd, alleen als je op de knop drukt; de AI kan geen producten of regels verzinnen, want alles gaat op nummer. Zonder sleutel verandert er niets en gaat er niets naar een AI-dienst.
- **Eenheden en ingrediënten bij het importeren in Mealie (2.69.1 en 2.70.0).** Na een import splitst MealieShopper regels als `200 ml melk` in aantal, eenheid en ingrediënt en koppelt die aan de eenheden en ingrediënten van Mealie (ontbrekende worden aangemaakt), zodat het recept schaalt en de boodschappenlijst optelt. Dat geldt voor Allerhande, Jumbo, Picnic, een gewone receptpagina en de MCP-tool `import_recipe_to_mealie`. Regels die Mealie niet had geanalyseerd worden ook bij het ophalen van het recept herkend, zodat het mandje niet meer "niet gevonden" meldt. Mislukt het analyseren, dan blijft het recept zoals Mealie het importeerde.
- **De build kies je met `MEALIESHOPPER_TAG` (2.71.0).** `docker-compose.yml` haalt `ghcr.io/helmerznl/mealieshopper:${MEALIESHOPPER_TAG:-latest}` op en geeft dezelfde waarde door aan de app, die op een beta-build een **beta**-badge toont (ook bij Over; `GET /api/about` geeft `channel`). Er is een nieuw voorbeeldbestand `.env.example`. Zonder de variabele blijft het `latest`.
- **Nieuwe routes (alleen beheerder):** `GET` en `PUT /api/offers/ai`, `POST /api/offers/ai/test`, `POST /api/offers/ai/review`, `GET /api/offers/ai/search/plan`, `POST /api/offers/ai/search` en `GET /api/offers/ai/search/status`. De MCP-tools zijn niet veranderd.

#### Goed om te weten bij het bijwerken

- **Het weekmenu werkt anders:** je ziet nu altijd eerst de controlelijst voordat het mandje wordt gevuld. Ververst een scherm niet vanzelf, laad de pagina dan één keer opnieuw (in de geïnstalleerde app: sluiten en weer openen).
- **`docker-compose.yml` is veranderd:** de image-tag komt nu uit `MEALIESHOPPER_TAG` (standaard `latest`). Gebruik je een eigen kopie van het compose-bestand, neem die regel dan over of laat alles zoals het was; er verandert dan niets.
- **De AI-functies zijn optioneel en staan uit zonder sleutel.** De aanroepen naar de AI-aanbieders komen niet in het auditlog en de sleutel komt nergens in een log of API-antwoord. De kwaliteit van de AI-koppelingen en de grootte van de aanvragen bij een groot aanbod zijn nog niet op echte gegevens gecontroleerd: begin met een kleine ronde.
- De database wordt zo nodig vanzelf aangevuld (nieuwe tabellen `offer_verdicts`, `ai_recipe_matches` en `ai_recipe_runs`); je hoeft niets te migreren.

Pull requests sinds v2.67.2: #118 tot en met #123.

*English summary: Fix for 2.72.0. The static-files version was not raised, so browsers and the installed app kept running the old script, which still filled the basket straight away when the app had no doubts. The version is raised now, so every browser loads the script that always shows the check list first.*

### Opgelost in 2.72.1

- **Het weekmenu ging in sommige situaties nog steeds direct in het mandje.** In 2.72.0 hebben we de controlelijst altijd getoond, maar de versie van de statische bestanden (`MEALIESHOPPER_ASSET_VERSION`) bleef gelijk. Daardoor bleef een browser of de geïnstalleerde app het oude script gebruiken, dat het mandje meteen vulde zodra de app nergens twijfelde. De versie is opgehoogd (20261023-42), zodat het nieuwe script wordt geladen en de controlelijst altijd verschijnt.

### Goed om te weten

- Ververst een scherm nog niet vanzelf, laad de pagina dan één keer opnieuw (in de geïnstalleerde app: sluiten en weer openen).

## v2.72.0

*English summary: Putting the week menu in the basket now always shows the check list first, with the product choices next to every line, even when the app has no doubts. You can untick lines and change the product or number of packs; only after you confirm are the products put in your basket (Albert Heijn, Picnic and Jumbo).*

### Gewijzigd in 2.72.0

- **Het weekmenu gaat nooit meer ongezien in het mandje.** Voorheen verscheen de controlelijst alleen als de app ergens twijfelde; anders werd het mandje meteen gevuld. Nu toont de knop in de planner altijd eerst de lijst: per ingrediënt het gekozen product met de andere keuzes ernaast, het aantal verpakkingen en een vinkje. Haal een vinkje weg als een regel toch niet klopt, of kies een ander product of aantal. Pas met `Voeg … toe aan mandje` gaan de producten erin. Geldt voor Albert Heijn, Picnic en Jumbo.
- Regels waar de app niet zeker van is blijven gemarkeerd; de uitleg boven de lijst past zich aan als er niets gemarkeerd is.

## v2.71.0

*English summary: The build you install (`:latest` or `:beta`) is now set in one place: `MEALIESHOPPER_TAG` in a `.env` file next to `docker-compose.yml`. The compose file pulls `ghcr.io/helmerznl/mealieshopper:${MEALIESHOPPER_TAG:-latest}` and passes the same value to the app, which shows a "beta" badge next to the wordmark (and under About) on a beta build. Without the variable nothing changes: `latest`, no badge. New `.env.example`.*

### Nieuw in 2.71.0

- **De build (`:latest` of `:beta`) stel je in via `.env`.** `docker-compose.yml` haalt nu `ghcr.io/helmerznl/mealieshopper:${MEALIESHOPPER_TAG:-latest}` op en geeft dezelfde `MEALIESHOPPER_TAG` door aan de container. Zet `MEALIESHOPPER_TAG=beta` in `.env` naast de compose-file (voorbeeld: `.env.example`), draai `docker compose pull && docker compose up -d` en je draait de beta-build. Zonder de variabele verandert er niets: `latest`.
- **Een beta-badge in de app.** Bij een beta-build (de tag bevat `beta`, dus ook `2.71.0-beta`) staat er een **beta**-badge naast het woordmerk in de kop, en bij Over staat *beta* achter de versie. `GET /api/about` geeft ook `channel` (`stable` of `beta`).

### Goed om te weten

- Compose leest `.env` zelf voor de image-tag; `.env.unraid` is alleen voor de app. Staat `MEALIESHOPPER_TAG` alleen in `.env.unraid`, dan wint de waarde uit `.env` (of `latest`), zodat image en badge altijd bij elkaar passen. Wil je `.env.unraid` toch gebruiken: `docker compose --env-file .env.unraid up -d`.
- Draai je de container met `docker run`, zet dan zelf `-e MEALIESHOPPER_TAG=beta` bij de beta-image om de badge te tonen.

## v2.70.0

*English summary: When a recipe is imported into Mealie, its ingredient lines are now parsed into quantity, unit and ingredient. Mealie stores imported lines as plain text ("200 ml milk"); MealieShopper now splits them and links them to Mealie's own units and foods (missing ones are created), so the recipe scales, the shopping list adds up and the display is right. Also includes the 2.69.1 fix for lines Mealie did not analyze.*

### Nieuw in 2.70.0

- **Bij het importeren in Mealie worden de eenheden en ingrediënten goed verwerkt.** Mealie bewaart een geïmporteerde regel als losse tekst (`200 ml melk`, `1 el maïzena`) in de notitie. Direct na de import haalt MealieShopper het aantal, de eenheid en het ingrediënt eruit en koppelt die aan de eenheden en ingrediënten van Mealie. Zo kent Mealie ze zelf: het recept kan schalen, de boodschappenlijst telt hoeveelheden op en de weergave klopt.
- **Eenheden worden herkend onder hun eigen naam.** `gr` en `gram` zijn de eenheid *gram* (`g`), `el` is *eetlepel*, enzovoort; staat de eenheid al in Mealie (op naam, meervoud, afkorting of alias), dan wordt die gebruikt. Een eenheid of ingrediënt dat Mealie nog niet kent, wordt aangemaakt (zoals ook de ingrediëntenparser van Mealie zelf doet).
- **Geldt voor elke manier van importeren:** Allerhande, Jumbo, Picnic, een gewone receptpagina en de MCP-tool `import_recipe_to_mealie`. Regels die Mealie al zelf analyseerde, en regels zonder aantal of ingrediënt (`peper naar smaak`), blijven zoals ze zijn.
- **De import zelf kan hierdoor niet mislukken.** Lukt het analyseren niet (Mealie onbereikbaar, geen rechten), dan blijft het recept zoals Mealie het importeerde en staat er een waarschuwing in het logboek.

## v2.69.1

*English summary: Recipe lines that Mealie did not analyze (quantity 1 with the text "200 ml milk" in the note, shown as "1 200 ml milk") are now split into quantity, unit and ingredient when the recipe is fetched. They show as "200 ml milk" and the store search uses "milk" instead of the whole text, so these lines are found in the cart.*

### Opgelost in 2.69.1

- **Ingrediënten die Mealie niet analyseerde worden nu goed herkend.** Een recept waarvan Mealie de ingrediënten niet had geanalyseerd, bewaart een regel als `200 ml melk` in de notitie, met aantal 1 en zonder eenheid of ingrediënt. Het weekmenu toonde dat als *1 200 ml melk* en het boodschappenmandje zocht in de winkel op die hele tekst, dus "9 niet gevonden". MealieShopper haalt nu bij het ophalen van het recept het aantal, de eenheid en het ingrediënt uit zo'n regel (`200`, `ml`, `melk`), zodat hij zich gedraagt als een regel die Mealie zelf analyseerde: *200 ml melk*, zoeken op *melk* en het aantal verpakkingen op basis van 200 ml.
- **Wat er wordt herkend:** hele en decimale getallen (`1,5`), breuken (`1/2`, `1 1/2`, `½`), bereiken (`2-3 el`, het hoogste aantal telt), `2x`, een eenheid zonder aantal (`snufje zout` is 1 snufje), en een toevoeging na een komma (`1 ui, fijngesneden`) of tussen haakjes.
- **Wat ongemoeid blijft:** regels die Mealie al analyseerde (met een ingrediënt of een eenheid), regels met een eigen aantal (`2` + `400 g tomaten`) en regels zonder aantal of ingrediënt (`peper naar smaak`).
- Dit geldt overal waar MealieShopper Mealie-recepten leest: het weekmenu, de boodschappenmandjes, de receptenindex voor de aanbiedingen en de MCP-tools.

## v2.69.0

*English summary: The AI now searches recipes for the products on offer, instead of only approving existing matches. The "Review with AI" button became "Search recipes with AI": after a confirmation with a token estimate, a round of up to 120 recipes from all your sources (Mealie, saved AH recipes, Jumbo favorites, Picnic recipes) is sent to Claude or ChatGPT together with the numbered food offers of the chosen store; the AI says which ingredient line fits which product number. Numbers that do not exist or are used twice are dropped, so nothing can be made up. The round runs in the background with visible progress (also after a reload). For the recipes the AI searched, its matches replace those of MealieShopper (check mark "Found by AI"); other recipes keep ours. A recipe is searched again only when the offer or its ingredients changed. Only product and ingredient text is sent, only when you press the button. No live search in the stores' recipe catalogues yet.*

### Aangepast in 2.69.0

- **De AI zoekt nu zelf recepten bij de producten in de aanbieding.** De knop *Beoordelen met AI* heet nu *Recepten zoeken met AI*. De AI krijgt het genummerde aanbod van de gekozen winkel (alleen eten) en een groep van 40 recepten met genummerde ingrediëntregels, en zegt per recept welke regel met welk productnummer gekoppeld kan worden. Producten en regels gaan op nummer: onbekende of dubbele nummers en basisingrediënten worden weggelaten, dus er kan niets verzonnen worden.
- **Bronnen:** alle recepten die MealieShopper al heeft: je Mealie-recepten, bewaarde AH-recepten, Jumbo-favorieten en Picnic-recepten. De AI zoekt niet live in het hele receptenaanbod van de winkels.
- **De AI vervangt per recept onze koppelingen.** Een recept dat de AI doorzocht toont haar koppelingen (met het vinkje *Gevonden door AI*), ook als dat er geen zijn: dan valt het recept weg. Een recept dat nog niet doorzocht is, houdt de koppelingen van MealieShopper. Producten die uit het aanbod zijn verdwenen en koppelingen die je met de vlag meldde, vallen weg.
- **Kosten onder controle.** Vooraf zie je een schatting van het aantal tokens en het aantal recepten (maximaal 120 per ronde, 40 per aanvraag; bij Claude staat het aanbod in een blok dat de aanbieder kan cachen); na afloop het gebruikte aantal. Een recept wordt pas opnieuw doorzocht als het aanbod van de winkel of de ingrediënten van het recept veranderd zijn, dus een volgende ronde kost alleen wat nieuw is.
- **Op de achtergrond.** Een ronde loopt in een eigen draad, één tegelijk, met een voortgangsregel (*12 van 120 recepten doorzocht...*) die ook na het herladen van de pagina weer verschijnt. Een fout (sleutel ongeldig, tegoed op) bewaart wat al klaar was; de volgende ronde gaat verder.
- **Nieuwe routes (alleen beheerder):** `GET /api/offers/ai/search/plan`, `POST /api/offers/ai/search` en `GET /api/offers/ai/search/status`; `GET /api/offers/ai` geeft ook de stand van een lopende ronde. Nieuwe tabellen `ai_recipe_matches` en `ai_recipe_runs`.

### Goed om te weten

- Het beoordelen van bestaande koppelingen (`POST /api/offers/ai/review`, de oordelen en hun vinkjes) bestaat nog als API, maar heeft geen knop meer.
- De echte aanbieders zijn niet aangeroepen tijdens de ontwikkeling: de kwaliteit van de koppelingen, de grootte van de aanvragen bij een groot aanbod (bijvoorbeeld Jumbo) en het cachen bij Claude zijn het eerste om op echte gegevens te controleren. Begin met een kleine ronde (de bevestiging toont de schatting).
- De AI beoordeelt alleen recepten uit de index; eerst *Recepten inlezen* als je nieuwe recepten hebt.

## v2.68.0

*English summary: Optional AI review of the matches between recipe ingredients and products on offer, with your own API key from Claude (Anthropic) and/or ChatGPT (OpenAI). Enter the key under Settings > Link (administrator only; stored encrypted, never shown again) or set `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`; choose the provider and model and test the key. With a key, the Recipes tab under Offers appears and the administrator gets a "Review with AI" button: each unique ingredient + product pair is sent once (only that text, nothing else), the verdict is kept, a rejected match is hidden like a reported one (source `ai`) and an approved one gets a check mark. It runs only when you press the button, asks for confirmation, and shows the tokens used. Without a key nothing changes.*

### Nieuw in 2.68.0

- **Een Claude- of ChatGPT-sleutel om de koppelingen te laten beoordelen (optioneel).** Onder *Instellingen, Koppelen, AI-beoordeling van koppelingen* vul je een API-sleutel in van Claude (Anthropic) en/of ChatGPT (OpenAI), kies je welke aanbieder gebruikt wordt (of *Automatisch*) en pas je zo nodig het model aan (standaard `claude-haiku-5-5` en `gpt-4o-mini`). `Testen` controleert sleutel en model met een kleine aanroep. De sleutel wordt versleuteld bewaard en nooit meer getoond; als startwaarde werken ook `ANTHROPIC_API_KEY` en `OPENAI_API_KEY`.
- **Beoordelen met AI.** Met een sleutel verschijnt het subtab *Recepten* bij Aanbiedingen (ook zonder `MEALIESHOPPER_OFFERS_RECIPES`), met voor de beheerder de knop *Beoordelen met AI*. Na een bevestiging worden de nog niet beoordeelde koppelingen beoordeeld, tot 200 per keer, per 40 in een aanvraag. Elk uniek paar (hoofdwoord van het ingrediënt + product, per winkel) wordt één keer beoordeeld; het oordeel wordt bewaard (tabel `offer_verdicts`), ook als je een melding later ongedaan maakt.
- **Het oordeel werkt zoals de vlag.** Een afgekeurde koppeling verdwijnt (en het volgende passende product neemt de plaats in); de melding staat bij `list_offer_mismatches` met bron `ai` en de reden. Een goedgekeurde koppeling krijgt een vinkje (MDI *check-circle-outline*), en boven de kaarten staat hoeveel koppelingen al beoordeeld zijn. Een melding van jou of van een assistent gaat voor.
- **Wat er verstuurd wordt.** Alleen de tekst van het ingrediënt en van het product (naam, merk, inhoud, categorie), geen recepttitels, namen of adressen, en alleen als je op de knop drukt. De AI krijgt de opdracht de teksten als gegevens te behandelen en niet te volgen wat erin staat; haar antwoord is beperkt tot een vast schema. Na afloop staat het aantal gebruikte tokens in de melding; een prijs in euro's wordt niet getoond (prijzen veranderen).
- **Nieuwe routes (alleen de beheerder):** `GET` en `PUT /api/offers/ai`, `POST /api/offers/ai/test` en `POST /api/offers/ai/review`.

### Goed om te weten

- Zonder sleutel verandert er niets: er gaat niets naar een AI-dienst en het tabblad *Recepten* blijft verborgen (tenzij `MEALIESHOPPER_OFFERS_RECIPES=true`).
- De AI beoordeelt wat het zoeken al vindt; ze zoekt zelf geen nieuwe koppelingen.
- De aanroepen naar de AI-aanbieders komen niet in het auditlog en de sleutel komt nergens in een log, foutmelding of API-antwoord. Is de sleutel ongeldig, staat het model niet meer bij de aanbieder of is het tegoed op, dan staat dat leesbaar in de melding; wat al beoordeeld was, blijft bewaard.

## v2.67.2

### Sinds de vorige stabiele versie (v2.53.0)

Deze release brengt `main` van v2.53.0 naar v2.67.2. De tussenliggende versies (2.53.1 tot en met 2.67.1) waren alleen als pre-release (`-beta`) beschikbaar; dit is wat er in totaal verandert. De volledige tekst per versie staat verderop in dit bestand.

*English summary of everything since v2.53.0: the new Offers tab (offers of Albert Heijn, Picnic and Jumbo, in categories, refreshed daily in the background, with a permanent history, the conditions and end date of each offer, and a cache so switching store is quick); recipe suggestions based on the offers of the chosen store (hidden by default in this release: set `MEALIESHOPPER_OFFERS_RECIPES=true` to show them), with clickable linked products, English ingredients translated for matching, a flag to report wrong matches and three new MCP tools so ChatGPT or Claude can review the matches; a reorganised interface (Settings > Link on one page, an avatar menu with Profile and Settings, a vertical menu on large screens, a week menu that loads by itself, the same subtab order for every store); links to Mealie recipes that work again; and Settings > System with a factory reset.*

- **Aanbiedingen (2.53.0 tot en met 2.67.1).** Een nieuw tabblad **Aanbiedingen** met de aanbiedingen van Albert Heijn, Picnic en Jumbo, per categorie, met oude en nieuwe prijs, de voorwaarden (*bij 2 stuks*) en de einddatum. Een achtergrondtaak ververst ze elke dag (standaard rond 5 uur) en de beheerder synchroniseert ze meteen onder *Instellingen > Koppelen*. Elke synchronisatie komt ook in een blijvende geschiedenis (`GET /api/offers/history`). Bij het wisselen van winkel toont de pagina een laadicoon en onthoudt ze de aanbiedingen; `GET /api/offers` stuurt een `ETag` mee.
- **Receptvoorstellen (verborgen in deze release).** Recepten uit Mealie, Jumbo, Picnic en AH worden vergeleken met de aanbiedingen van de gekozen winkel, met de meeste ingrediënten in de aanbieding bovenaan, een filter op soort gerecht en per recept wat er niet in de aanbieding is. Gekoppelde producten zijn aan te klikken (venster met plaatje, prijs en voorwaarden), Engelse ingrediënten worden vertaald voor het vergelijken, hetzelfde recept uit twee bronnen is één kaart en de koppelingen zijn strenger (basisingrediënten, bereide producten, kleur, keuken en vetgehalte). Omdat de koppelingen nog niet goed genoeg zijn, staat het subtabblad *Recepten* sinds v2.67.0 standaard uit; met `MEALIESHOPPER_OFFERS_RECIPES=true` komt het terug.
- **Koppelingen beoordelen (2.66.0 en 2.67.0).** Een onjuiste koppeling meld je met een kleine vlag of de knop *Klopt niet*; de melding blijft bewaard (tabel `offer_rejections`) en geldt voor het hoofdwoord in die winkel. ChatGPT en Claude kunnen de koppelingen via de MCP-server beoordelen met de nieuwe tools `review_offer_matches`, `report_offer_mismatch` en `list_offer_mismatches`; er draait geen AI in MealieShopper zelf.
- **Nieuwe indeling.** *Instellingen > Koppelen* is één pagina met de status van Mealie en de winkels bovenaan; een avatar-menu met Profiel, Instellingen en Over vervangt het tabblad Beheer; op een groot scherm staat het hoofdmenu links en is het inklapbaar; het weekmenu laadt vanzelf met een cache van 30 minuten; de subtabs onder *Winkels* staan bij elke winkel in dezelfde volgorde (Recepten eerst) en *AH Recepten* heet nu *Recepten*.
- **Instellingen > Systeem.** Het tabblad *Updates* heet nu *Systeem* en bevat de updatecheck en de beheerdersfunctie *Fabrieksinstellingen* (v2.63.0 verderop).
- **Opgelost.** Links naar Mealie-recepten gebruiken weer het adres dat Mealie zelf gebruikt (`/g/<groep>/r/<recept>`), ook na het importeren van een recept en in de MCP-tools.
- **Nieuwe routes** (alleen beheerder, tenzij anders vermeld): `GET /api/offers`, `POST /api/offers/sync`, `GET /api/offers/recipes`, `POST /api/offers/recipes/sync`, `GET /api/offers/status`, `POST /api/offers/sync-all`, `GET /api/offers/history`, `POST` en `DELETE /api/offers/rejections`, `GET /api/system/factory-reset`, `POST /api/system/factory-reset/options` en `POST /api/system/factory-reset`. **Nieuwe MCP-tools:** `review_offer_matches`, `report_offer_mismatch` en `list_offer_mismatches`.
- **Verwijderd.** De knop *Aanbiedingen ophalen* op het tabblad Aanbiedingen (de aanbiedingen komen uit de lokale kopie), het aparte tabje *Mealie* (nu onderdeel van *Koppelen*) en het tabblad *Beheer* (nu in het avatar-menu).

#### Goed om te weten bij het bijwerken

- **Nieuwe, optionele instellingen:** `MEALIESHOPPER_OFFERS_SYNC_HOUR` (uur van de dagelijkse run, standaard 5), `MEALIESHOPPER_OFFERS_SCHEDULE=false` om de dagelijkse taak uit te zetten en `MEALIESHOPPER_OFFERS_RECIPES=true` om de receptvoorstellen onder Aanbiedingen weer te tonen. Zonder aanpassing werkt alles zoals voorheen, met de receptvoorstellen verborgen.
- **Zet je de receptvoorstellen aan, lees je recepten dan één keer opnieuw in** (*Aanbiedingen > Recepten > Recepten inlezen*): de soort gerecht wordt daarbij bewaard. Tot dan staan alle recepten onder *overig*.
- De database wordt zo nodig vanzelf aangevuld (onder meer de kolom *categorie*, de geschiedenis van de aanbiedingen en de tabel met gemelde koppelingen); je hoeft niets te migreren.
- De aanbiedingen van Picnic en Jumbo komen uit onofficiële routes en kunnen veranderen. Geeft een winkel niets terug, dan staat dat als melding bij *Koppelen* en bij *Aanbiedingen*.
- De screenshots in `docs/screenshots/` kunnen nog een oudere indeling tonen.

Pull requests sinds v2.53.0: #89 tot en met #115.

*English summary: The subtabs under Stores are now in the same order for every store: Recipes, Lists, Orders for Albert Heijn and Jumbo, and Recipes, Favorites, Orders for Picnic. Jumbo showed Lists, Orders, Recipes before. The Albert Heijn tab "AH Recipes" is now just "Recipes", and switching store always opens the Recipes tab of that store (before, Albert Heijn and Jumbo started at Lists).*

### Aangepast in 2.67.2

- **De subtabs onder Winkels staan bij elke winkel in dezelfde volgorde.** Albert Heijn: *Recepten, Lijstjes, Bestellingen*. Jumbo: *Recepten, Lijstjes, Bestellingen* (Recepten stond achteraan). Picnic: *Recepten, Favorieten, Bestellingen*.
- **Het tabblad *AH Recepten* heet nu *Recepten*.** De winkelkeuze ernaast laat al zien dat het om Albert Heijn gaat. De melding bij het indexeren van recepten noemt nu *Winkels > Albert Heijn > Recepten*.
- **Wisselen van winkel opent altijd het tabblad *Recepten* van die winkel.** Voorheen begonnen Albert Heijn en Jumbo bij *Lijstjes*, en bleef een open tabblad staan als het bij de nieuwe winkel bestond. Lijstjes en bestellingen laden voor de nieuwe winkel zodra je ze opent.

## v2.67.1

*English summary: When you switch store under Offers, the list of the previous store now disappears at once and a spinning icon (Material Design Icons "loading") with "Loading the offers of {store}…" shows until the new offers are in. Before, the old list stayed on screen under the new store name, so it looked as if nothing happened. A slow answer from a store you already switched away from no longer overwrites the list. With reduced motion set in the system the icon stands still and the text stays. The offers are also cached now: a store you already saw shows at once when you switch back (and is checked in the background), and the browser only gets the list again when it changed (ETag, 304).*

### Opgelost in 2.67.1

- **Bij het wisselen van winkel was niet te zien dat er geladen werd.** De lijst van de vorige winkel bleef staan, onder de naam van de nieuwe winkel, tot de nieuwe aanbiedingen binnen waren. Nu verdwijnt die lijst meteen en staat er een draaiend icoon (MDI *loading*) met *Aanbiedingen van Jumbo laden…*; daarna komen de aanbiedingen. Dat geldt ook bij het openen van het tabblad en bij een taalwissel.
- **Een traag antwoord van de vorige winkel overschrijft de lijst niet meer.** Wissel je snel twee keer, dan telt alleen de laatste winkel. Een fout haalt het icoon weg en toont de foutmelding.
- **Minder beweging.** Staat in je systeem *beweging verminderen* aan, dan draait het icoon niet; de tekst blijft staan.

### Nieuw in 2.67.1

- **De aanbiedingen worden gecachet.** Wissel je terug naar een winkel die je al zag, dan staat de lijst er meteen (zonder icoon) en controleert de pagina op de achtergrond of er iets veranderd is; alleen bij een andere lijst wordt het scherm vernieuwd, dus je plek en je *Meer* blijven staan.
- **De browser vraagt alleen of er iets veranderd is.** `GET /api/offers` stuurt een `ETag` mee (`Cache-Control: private, no-cache`). Is de lijst niet veranderd, dan volgt een `304` zonder de lijst te laden of te versturen. De ETag verandert bij een nieuwe synchronisatie van die winkel, op een nieuwe dag (aanbiedingen lopen af) en bij een nieuwe versie van de app.

### Goed om te weten

- De server zelf is niet traag: 3000 aanbiedingen laden kost ± 30 ms, dus de database cachen heeft weinig zin. De wachttijd zat in het ophalen en tekenen van de lijst, en tot 2.67.0 ook in de receptvoorstellen die bij elke winkelwissel mee werden berekend; die staan sinds 2.67.0 uit (`MEALIESHOPPER_OFFERS_RECIPES`).
- De cache in de pagina geldt tot je de pagina herlaadt; daarna zorgt de ETag ervoor dat het ophalen klein blijft.

## v2.67.0

*English summary: The recipe suggestions under Offers are hidden for now (set `MEALIESHOPPER_OFFERS_RECIPES=true` to show them again), because the matches are not good enough yet. ChatGPT and Claude can now review the matches through the MCP server: `review_offer_matches` lists the unique ingredient + product pairs of a store, `report_offer_mismatch` reports a wrong one (with a reason, undoable) and `list_offer_mismatches` shows the reports. Reports from the assistant and from the flag in the app share one list and one effect: the match is hidden and the next fitting product takes its place. No AI runs inside MealieShopper itself: your own assistant does the judging, with your own subscription.*

### Aangepast in 2.67.0

- **De receptvoorstellen onder Aanbiedingen zijn verborgen.** Het subtabblad *Recepten* staat standaard uit, zolang de koppelingen niet goed genoeg zijn. Met `MEALIESHOPPER_OFFERS_RECIPES=true` komt het terug. De aanbiedingen zelf en de API (`/api/offers/recipes`) blijven werken.

### Nieuw in 2.67.0

- **ChatGPT en Claude beoordelen de koppelingen, via de MCP-server.** Drie nieuwe tools: `review_offer_matches` (de unieke paren ingrediënt + product van een winkel, het vaakst gebruikte eerst, met het aantal recepten en voorbeelden; `limit` en `offset` om verder te lezen), `report_offer_mismatch` (een paar als onjuist melden, met een korte reden; `undo` haalt de melding weg) en `list_offer_mismatches` (de gemelde koppelingen, met wie meldde en waarom). Vraag het bijvoorbeeld zo: *"Beoordeel met MealieShopper de koppelingen van Jumbo en meld de onjuiste."*
- **Eén meldlijst.** Meldingen van de assistent en van de vlag in de app (v2.66.0) staan in dezelfde tabel en hebben hetzelfde effect: de koppeling verdwijnt, ook bij andere recepten met hetzelfde hoofdwoord in die winkel, en het volgende passende product neemt de plaats in. De tabel heeft er twee kolommen bij (`reason`, `source`); meldingen van 2.66.0 blijven werken.
- **Geen AI in MealieShopper zelf.** Er gaat niets automatisch naar een AI-dienst en er zijn geen API-sleutels nodig: je assistent doet het oordelen, met je eigen abonnement, en alleen als jij erom vraagt.

### Goed om te weten

- `report_offer_mismatch` is de enige schrijftool voor aanbiedingen; ze verwijdert niets en is ongedaan te maken. Je kunt haar, net als elke andere tool, uitzetten onder Profiel, AI-apps.
- De kleine vlag in de app is verborgen samen met het tabblad; dat de melding daar beter in beeld moet komen, wordt opgepakt als het tabblad terugkomt.

## v2.66.0

*English summary: A wrong match between a recipe ingredient and an offer can now be reported with one click: a small flag (the Material Design Icons "flag-outline") next to every matched product, and a "Not a match" button in the product window. The match does not come back, also not in other recipes with the same main ingredient in that store, and the next best product takes its place. You can undo it right after reporting, and a report survives the daily sync. Still no AI involved.*

### Nieuw in 2.66.0

- **Een onjuiste koppeling melden.** Naast elk gekoppeld product onder een receptvoorstel staat een kleine vlag (het MDI-icoon *flag-outline*); in het productvenster staat dezelfde melding als knop *Klopt niet*. Daarna staat er een melding met *Ongedaan maken*, en de koppeling is weg. Heeft de winkel een ander product dat wel past, dan komt dat ervoor in de plaats.
- **Een melding geldt voor het hoofdwoord, in die winkel.** *Kipfilet → AH Kipfilet met saus* melden verbergt die koppeling ook in andere recepten met kipfilet. Bij een andere winkel is het een ander product en blijft het staan. Een Engelse regel (*chicken breast*) wordt gemeld onder het Nederlandse hoofdwoord, net zoals hij wordt gekoppeld.
- **Meldingen blijven bewaard.** Ze staan in een eigen tabel (`offer_rejections`) en overleven de dagelijkse synchronisatie en het opnieuw inlezen van de recepten. Je kunt zo ook zien welke koppelingen niet kloppen, om de regels beter te maken.
- **Nieuwe route:** `POST /api/offers/rejections` meldt een koppeling (`store`, `ingredient`, `offerId`) en `DELETE` met dezelfde velden trekt de melding in.

## v2.65.2

*English summary: The same recipe from two sources (for example an AH recipe you also have in Mealie) is now one card, with "also in Albert Heijn" next to it; the recipe with the best coverage stays, and with equal coverage your own recipe from Mealie. An ingredient with a colour, cuisine or fat content (red bell pepper, Provençal stir-fry vegetables, semi-skimmed milk) no longer matches an offer that names a different one (yellow, Chinese, whole milk).*

### Opgelost in 2.65.2

- **Hetzelfde recept uit twee bronnen is één kaart.** *Supersnelle spaghetti met runderballetjes* stond twee keer in de lijst, één keer uit Mealie en één keer uit Albert Heijn. Recepten met dezelfde titel (hoofdletters, accenten en leestekens tellen niet mee) uit verschillende bronnen worden nu samengevoegd. Het recept met de beste dekking blijft staan, bij gelijke dekking het eigen recept uit Mealie, en op de kaart staat *ook in Albert Heijn*. De aantallen bij *Alle* en bij de soorten gerecht tellen het recept één keer. Twee recepten met dezelfde titel uit dezelfde bron blijven allebei staan: dat zijn twee recepten.
- **Een andere kleur, keuken of vetgehalte is een ander product.** *400 g Provençaalse wokgroente* hoorde bij *AH Wokgroente Chinees*. Noemen het ingrediënt en de aanbieding allebei een kleur (rood, geel, groen, oranje, wit, zwart, bruin, paars, blauw), een keuken of streek (Chinees, Provençaals, Italiaans, Thais, Spaans, …) of een vetgehalte (mager, halfvol, vol) en zijn die verschillend, dan is het geen match: *rode paprika* past niet bij *Paprika geel*, *halfvolle melk* niet bij *Volle melk*. Noemt maar één kant zoiets (*wokgroente*, *Wokgroente Chinees*) of noemt de aanbieding meerdere (*Paprika rood geel groen*), dan blijft het een match. *Rode* en *rood*, *witte* en *wit* en *Chinese* en *Chinees* gelden als hetzelfde woord.

### Goed om te weten

- De kaart toont de bron van het recept dat blijft staan; *ook in …* noemt de bronnen van de samengevoegde recepten.
- De API en de MCP-tools zijn niet veranderd, op het extra veld `alsoIn` bij elk recept in `GET /api/offers/recipes` na.

## v2.65.1

*English summary: Links to Mealie recipes now use the address Mealie itself uses, `/g/<group>/r/<recipe>`, instead of `/recipe/<recipe>` (which gave an error page). This also fixes the link after importing a recipe and the links of the MCP tools, and recipes that were read in before are corrected without reading them again. Six more wrong links between ingredients and offers are gone: a vegetable burger for broccoli, a pie with beef stew for onions, a cream cheese dip for cream cheese, tortelloni with ricotta for ricotta, "spaghetti all'uovo" matching "tagliatelle all'uovo", and a noodle meal for "vegetables".*

### Opgelost in 2.65.1

- **De links naar Mealie-recepten klopten niet.** De knop *Bekijken* bij een receptvoorstel opende `/recipe/<recept>`, terwijl Mealie `/g/<groep>/r/<recept>` gebruikt (bijvoorbeeld `/g/zb/r/witlof-met-ham-en-kaas-uit-de-oven`). MealieShopper vraagt de groep (`groupSlug`) één keer aan Mealie (`/api/users/self`, met je eigen token), onthoudt die een uur en bouwt daarmee het adres op. Bij een oudere Mealie die geen groep geeft, of als Mealie even niet antwoordt, blijft het oude adres staan; na een mislukte poging probeert MealieShopper het na een minuut opnieuw en blokkeert het niets.
  - Dit geldt ook voor de link na het **importeren van een recept** (AH, Picnic, Jumbo, een adres) en voor de **MCP-tools** (`get_week_menu`, `import_recipe_to_mealie`).
  - Recepten die al in de index staan, hoeven niet opnieuw ingelezen: het adres wordt bij het tonen opnieuw opgebouwd uit het bewaarde adres.
- **Zes verkeerde koppelingen uit de receptvoorstellen bij AH**:
  - *500 g broccoli* hoorde bij *AH Terra Groenteburger broccoli paprika* en *2 middelgrote uien* bij *AH Pie runderstoof ui*. Een aanbieding in een andere rauwe soort (vlees) dan het ingrediënt (groente) is een samengesteld product waar het ingrediënt maar een deel van is; die telt niet meer.
  - *150 g koelverse roomkaas met bieslook* hoorde bij *AH Mascarpone roomkaas chili dip*. Een *dip*, *burger*, *nugget* of *schotel* is een bereid product en telt alleen als het ingrediënt dat zelf vraagt.
  - *125 g ricotta* hoorde bij *AH Tortelloni verdi ricotta spinaci*. Gevulde pasta (tortelloni, tortellini, ravioli, cannelloni, gnocchi) en pastavormen (tagliatelle, fusilli, …) zijn nu een maaltijd, ook als er ricotta in zit; ricotta, mascarpone, burrata, pecorino, gorgonzola en camembert zijn kaas.
  - *250 g verse spaghetti all'uovo* hoorde bij *AH Tagliatelle all' uovo*: *uovo* was het hoofdwoord. Een Italiaanse toevoeging (*all'uovo*) telt niet meer mee, dus het hoofdwoord is *spaghetti*.
  - *Verspakket plaattaart groente* hoorde bij *Knorr Good noodles groente*. *Groente* en *fruit* zijn nu net als *saus* en *kaas* een soort: de andere woorden van het ingrediënt moeten er ook in staan.

### Goed om te weten

- De API en de MCP-tools zijn niet veranderd, op het adres van een Mealie-recept na.

## v2.65.0

*English summary: English ingredients (many Mealie imports) can now be matched with the Dutch offers. Before comparing, each ingredient line is translated into Dutch from a built-in list of a few hundred common ingredients and phrases ("2 tbsp mayo" becomes "2 tbsp mayonaise", "chicken thighs" becomes "kippendijen", "kipdijfilet" and "kippendij"); nothing is sent to a translation service. A Dutch line is left as it is and the recipe card still shows the line as written in the recipe. A tablespoon of a dried spice ("2 tbsp paprika") is a seasoning and no longer looks for bell peppers.*

### Nieuw in 2.65.0

- **Engelse ingrediënten worden vertaald voor het vergelijken.** Een nieuwe module, `mealieshopper/offer_translate.py`, zet een Engelse regel om in het Nederlands, op woorden en uit een vaste lijst (een paar honderd gangbare ingrediënten en samenstellingen: groente en fruit, vlees en vis, zuivel, brood en pasta, sauzen, bakproducten, noten en specerijen). Aantallen, eenheden en wat tussen haakjes staat blijven staan; een meervoud (*tomatoes*, *cherries*) en hoofdletters maken niet uit; de langste frase wint (*cream cheese* wordt *roomkaas* en niet *room kaas*). Er gaat niets naar een vertaaldienst.
- **Meerdere Nederlandse namen worden allemaal geprobeerd.** De winkels noemen hetzelfde product niet gelijk: *chicken thighs* wordt *kippendijen*, *kipdijfilet* en *kippendij*, *ground beef* wordt *rundergehakt* en *gehakt*, *spring onion* wordt *bosui* en *lente-ui*. De aanbieding die het beste past wint.
- **De kaart toont de regel zoals hij in het recept staat** (Engels), met het Nederlandse product erachter, en *Niet in de aanbieding* noemt de oorspronkelijke regel.

### Aangepast in 2.65.0

- **Een eetlepel van een gedroogde specerij is een kruid.** *2 tbsp paprika*, *1 el kurkuma*, *1 tbsp cumin*, *2 el kerrie* en *1 tablespoon cinnamon* zoeken geen product meer bij de aanbiedingen. Voorheen werd *2 tbsp paprika* gekoppeld aan een zak paprika's, terwijl er paprikapoeder in een kruidenmix bedoeld werd. Een gewone *rode paprika* of *1 paprika* blijft een groente.

### Goed om te weten

- **De lijst is niet volledig.** Een Engels woord dat er niet in staat, blijft Engels en vindt dus meestal niets. Woorden die in beide talen gelijk zijn (*pasta*, *pesto*, *mozzarella*, *avocado*) hebben geen vertaling nodig. Ontbreekt een ingrediënt dat je vaak tegenkomt, geef het door; het is één regel in de lijst.
- Een Nederlandse regel verandert niet: de tests controleren dat voor een reeks gangbare Nederlandse regels, en dat opnieuw vertalen van een vertaalde regel niets verandert.
- Alleen de ingrediëntregels worden vertaald, niet de titels van recepten of de aanbiedingen (die zijn Nederlands).
- De API en de MCP-tools zijn niet veranderd.

## v2.64.0

*English summary: The products linked to a recipe's ingredients can be clicked. A dialog shows the product (picture, name, brand and size, the offer, the price with the old price, the conditions) and which ingredient from which recipe it is linked to, so you see at once whether the link is right. A button opens the store's own page in a separate window; Picnic has no web shop, so there the dialog says there is no product page. The dialog works with the keyboard (Enter opens, Esc closes), also closes with a click outside it, and fits a phone and the dark theme.*

### Nieuw in 2.64.0

- **Gekoppelde producten zijn aan te klikken.** Onder een receptvoorstel is `→ AH Zalmfilet 2-pack` nu een knop. Die opent een venster met het plaatje, de naam, het merk en de inhoud, de actie (*1+1 gratis*), de prijs met de oude prijs, de voorwaarden (*bij 2 stuks · € 2,50 per stuk · t/m 25 okt*) en de zin *Gekoppeld aan "100 g zalmfilet" uit Romige tagliatelle met zalm*. Zo zie je meteen of het product bij het ingrediënt past.
- **Bekijk bij de winkel.** Een knop *Bekijk bij Albert Heijn* of *Bekijk bij Jumbo* opent de pagina van het product in een los venster (hetzelfde als *Bekijk product* bij het mandje). Picnic heeft geen webwinkel; daar staat in het venster dat er geen productpagina is en ontbreekt de knop.
- Het venster sluit met *Sluiten*, met Esc of met een klik ernaast. Met het toetsenbord opent Enter op de productnaam het venster, en na het sluiten staat de focus weer op die naam. Het past op een telefoon en volgt het donkere thema.

### Goed om te weten

- Een productplaatje dat niet laadt wordt in het venster verborgen en niet uit de pagina gehaald (anders zou het venster daarna geen plaatjes meer kunnen tonen).
- Alleen bij de receptvoorstellen; de kaarten bij Aanbiedingen > Aanbiedingen openen nog steeds direct de winkelpagina in een nieuw tabblad.
- De API en de MCP-tools zijn niet veranderd.

## v2.63.1

*English summary: Fewer wrong links between recipe ingredients and offers. A kind of product ("curry sauce", "bread roll") only fits an offer that has the other words too, a raw product (bell pepper, chicken fillet) no longer fits a prepared one (crisps, focaccia, a wrap), shampoo, cat food and other non-food offers are left out, and Lay's/Bifi and similar snack brands are snacks instead of "vegetables". A line with a teaspoon or a pinch, a spice powder, English basics (salt, oil, flour ...) and a heading such as "Spice mixture (below)" no longer count as ingredients, and the same ingredient twice in a recipe counts once. In the two recipes that were reported, "5 of 20" became 1 of 8 (and is no longer suggested), and the shampoo is gone from the sushi bowl.*

### Opgelost in 2.63.1

- **Verkeerde koppelingen in de receptvoorstellen**, uit twee gemelde recepten:
  - *Bread roll* hoorde bij *Bifi Roll XXL*, *1 tbsp curry sauce* bij *Sweet chilli sauce*. Bij een soort (*saus*, *sauce*, *soep*, *kaas*, *brood*, *rol*, *worst*, *dressing*, *drank*, *vlees*, *vis*) moeten de andere woorden van het ingrediënt ook in de aanbieding staan.
  - *Paprika* hoorde bij *Lay's Paprika* en bij *AH Focaccia paprika olijf*. Een rauw product (groente en fruit, vlees, vis, zuivel en eieren, kaas en vleeswaren) past niet bij een bereid product (brood en bakkerij, maaltijden, snacks). Lay's, Doritos, Pringles, Cheetos, Bifi, Bugles en Tostitos zijn nu snacks; voorheen telde *Lay's Paprika* als groente. Dat verbetert ook de categorieën bij de aanbiedingen.
  - *Avocado's* hoorde bij *Avocado olie shampoo*. Aanbiedingen van drogisterie, huishouden, baby en huisdier doen niet meer mee aan de receptvoorstellen.
- **Wat geen ingrediënt is, telt niet meer mee** in *x van y* en in *Niet in de aanbieding*: een regel met een theelepel of snuf (*1 tsp salt*, *½ tsp cayenne*, *2 tl kurkuma*, *1 snuf nootmuskaat*), kruidenpoeders (*onion powder*, *knoflookpoeder*), de Engelse basis (*salt*, *pepper*, *oil*, *butter*, *flour*, *sugar*, *vinegar*, *stock*, *cornstarch*, *maizena*) en een kopje als *Spice mixture (below)* of *Kruidenmix (hieronder)*. Engelse eenheden en bijwoorden (*tbsp*, *tsp*, *cup*, *and*, *with*, *chopped*, ...) tellen niet als woorden van het ingrediënt, en een verbogen bijvoeglijk woord (*geraspte*, *gerookte*) telt net als *geraspt* niet mee.
- **Hetzelfde ingrediënt twee keer in een recept telt één keer** (*1 tsp paprika* en *2 tbsp paprika* waren twee ingrediënten met twee verschillende aanbiedingen).
- In de twee gemelde recepten werd *Grillworst* van "5 van 20" naar 1 van 8 (en wordt daarom niet meer voorgesteld, de enige koppeling is *mayo* → *Hellmann's*), en de *Warme sushibowl* verloor de shampoo.

### Goed om te weten

- **Engelse receptregels** (veel Mealie-imports) kunnen niet met Nederlandse aanbiedingen worden vergeleken: alleen woorden die in beide talen gelijk zijn (*paprika*, *mayo*, *avocado*) matchen. Zo'n recept toont nu minder valse koppelingen, maar blijft meestal onder de ondergrens.
- *Komkommer* hoort nog bij *AH Tomaat komkommer M* (een titel met een tweede groente); of dat klopt, is uit de titel alleen niet te zien. Gereedschap als *grillpan* telt nog als ingrediënt dat niet in de aanbieding is.
- De API en de MCP-tools zijn niet veranderd.

## v2.63.0

*English summary: Settings > Updates is now Settings > System. Under the update check there is a new Clean-up block with a factory reset. It warns about the consequences, asks for the word RESET and for the administrator to prove again who they are (password and, with two-step verification, the code; or a passkey), then deletes the whole database and the secret key, logs everybody out and restarts the app (under gunicorn with a restart policy), which starts at the onboarding again.*

### Nieuw in 2.63.0

- **Fabrieksinstellingen.** Onder *Instellingen > Systeem > Opschonen* zet de beheerder MealieShopper terug naar een nieuwe installatie. Dat verwijdert de database (gebruikers, wachtwoorden, passkeys, koppelingen, lijstjes, bestellingen, aanbiedingen, auditlog) en `secret.key`. De knop opent eerst een waarschuwing met de gevolgen en vraagt daarna om het woord `RESET`, om opnieuw te bewijzen wie je bent (wachtwoord, en bij tweestapsverificatie ook de code of een herstelcode; of een passkey) en om een laatste bevestiging. Foute pogingen tellen mee voor dezelfde begrenzing als inloggen.
- **Opnieuw starten.** Na het wissen stopt de app zichzelf (onder gunicorn, zoals in de container; de container start door het restart-beleid weer op) en laadt de pagina vanzelf opnieuw zodra de app terug is, bij de onboarding. Draait de app op een andere manier, dan meldt hij dat je hem zelf opnieuw moet starten.
- **Nieuwe routes** (alleen beheerder): `GET /api/system/factory-reset`, `POST /api/system/factory-reset/options` (de passkey-uitdaging) en `POST /api/system/factory-reset`.

### Aangepast in 2.63.0

- Het tabblad *Updates* in de Instellingen heet nu *Systeem* en bevat de updatecheck en het nieuwe blok *Opschonen*.
- De passkey-controle van het inloggen staat nu in `auth.check_assertion`, zodat het wissen dezelfde controle gebruikt.

## v2.62.0

*English summary: The recipe suggestions are about what you can cook with the offers of the chosen store. Above the recipes it now says so ("Recipes with as many ingredients as possible from the offers at Jumbo"), basics such as salt, oil, butter and flour no longer count towards "x of y ingredients on offer", the recipes with the largest share on offer come first, and each recipe lists what is not on offer. At the top there is a filter for the kind of dish (breakfast, lunch, starter, soup, main course, side dish, dessert, snack, other) with the number of recipes per kind. The kind comes from the recipe's source, so read the recipes once more after updating. A recipe card now says where the recipe comes from ("Recipe from Albert Heijn") instead of a bare store name that read like the store of the offers.*

### Nieuw in 2.62.0

- **Filter op soort gerecht.** Bovenaan Aanbiedingen > Recepten staat een rij keuzes: ontbijt, lunch, voorgerecht, soep, hoofdgerecht, bijgerecht, dessert, snack en overig, met het aantal recepten per soort. Alleen soorten die voorkomen staan erbij. De aantallen gelden voor alle voorstellen van de gekozen winkel, het filter beperkt de lijst. Een andere winkel zet het filter terug op alles. De soort komt uit de bron: de gang bij Jumbo, de categorieën en tags in Mealie en de categorie van het recept bij AH. Picnic geeft geen gang mee; die recepten staan onder overig.
- **Boven de recepten staat van welke winkel de aanbiedingen zijn** (*Recepten met zoveel mogelijk ingrediënten uit de aanbiedingen van Jumbo*), met de uitleg dat basisingrediënten niet meetellen en de recepten met de meeste ingrediënten in de aanbieding bovenaan staan.
- **Wat er niet in de aanbieding is.** Onder de gekoppelde ingrediënten staat *Niet in de aanbieding: …*, zodat je ziet wat je er nog bij moet halen.
- `GET /api/offers/recipes` krijgt `course` (een van de soorten, of `all`) en geeft per recept `course` en `missing`, en `courses` met de aantallen per soort.

### Aangepast in 2.62.0

- **Basisingrediënten tellen niet meer mee.** Zout, peper, water, ijs, olie, boter, bloem, suiker, azijn, bouillon en kruiden zijn niet langer een ingrediënt dat in de aanbieding hoort te zijn. Een regel telt als basis als het hoofdwoord er een is of erop eindigt (*milde olijfolie*, *roomboter*, *zeezout*, *balsamicoazijn*). Het aantal in *x van y ingrediënten in de aanbieding* wordt daardoor kleiner (*2 van 6* in plaats van *2 van 7*). Een smaakwoord aan het eind van een langere regel (*zuivelspread knoflook en kruiden*) telt nog mee als ingrediënt dat niet te koppelen is.
- **De receptkaart zegt waar het recept vandaan komt.** De winkelkeuze bij Aanbiedingen > Recepten bepaalt alleen met welke aanbiedingen de recepten worden vergeleken; de recepten komen uit al je bronnen. Achter het aantal ingrediënten stond de bron van het recept ("· Albert Heijn"), wat las alsof het AH-aanbiedingen waren, ook als je Jumbo had gekozen. Nu staat er *Recept uit Albert Heijn*.

### Goed om te weten

- **Lees je recepten één keer opnieuw in** (`Recepten inlezen`): de soort wordt daarbij bewaard. Tot dan staan alle recepten onder *overig*. AH-recepten die al in de index staan maar nog geen soort hebben, worden bij het inlezen opnieuw opgehaald, binnen de 60 nieuwe AH-recepten per keer.
- De volgorde is: het grootste deel van de ingrediënten in de aanbieding, dan het grootste aantal, dan de grootste besparing. Een recept moet minstens twee koppelingen hebben (één bij een recept met hooguit drie ingrediënten).
- De soorten komen uit vrije tekst van de bron en worden op woorden herkend (*Avondeten* en *Main course* zijn hoofdgerecht). Een categorie die je zelf in Mealie hebt bedacht en niet herkend wordt, valt onder overig.

## v2.61.0

*English summary: Offers now say what they involve and when they stop. Each offer shows from how many items it applies, what an item then costs and until when (AH gives an end date). The offer text is read for the kinds seen in the explorer reports, including Picnic's "2e = 50% korting" (the second item at 50% off, which was read as 50% off everything) and "nu €1,99". Offers whose end date has passed no longer show up in the list or the recipe suggestions between two syncs. The saving in recipe suggestions uses the price per item under the offer's condition instead of the difference between the old and the current price the store names, which can be wrong for "1+1 free" or "2nd half price". Ingredients are also linked to offers more strictly (a product that only contains the ingredient, such as mince with onion or tuna in olive oil, no longer counts). This brings the v2.53.0 work from `main` (the `deals` module, the `promo` fields and the offer part of the explorers) into the offers of `beta`.*

### Nieuw in 2.61.0

- **De voorwaarden bij een aanbieding.** Op de kaart staat naast het label (bijvoorbeeld *1+1 gratis*) nu *bij 2 stuks*, de prijs per stuk bij die voorwaarde en *t/m 25 okt* als de winkel een einddatum geeft. Dat is wat in het eerste rapport ontbrak: een aanbieding "2 voor 5" liet alleen de prijs zien.
- **Verlopen aanbiedingen verdwijnen.** Staat de einddatum in het verleden, dan laat de lijst de aanbieding niet meer zien en telt ze niet mee in de receptvoorstellen, ook al is er nog niet opnieuw gesynchroniseerd. `GET /api/offers` meldt hoeveel er zijn verborgen (`expired`). Bij AH komt de einddatum van de winkel zelf (`bonusEndDate`); Picnic en Jumbo geven geen datum, dus die blijven staan tot de volgende synchronisatie.
- **De module `mealieshopper/deals.py`** (uit v2.53.0 van `main`) leest de actietekst en rekent de prijs per stuk uit. Nieuw is *2e = 50% korting* (Picnic), dat als percentage over alles werd gelezen, en *nu €1,99*.

### Opgelost in 2.61.0

- **Verkeerd gekoppelde ingrediënten in de receptvoorstellen.** Een ingrediënt hoorde bij een aanbieding zodra zijn woord *ergens* in de titel stond. Bij *Romige tagliatelle met zalm* stond daardoor "7 van 7 ingrediënten in de aanbieding" met onder meer *AH Gehakt met ui* voor de ui, *Champignon saus romig en vol* voor de champignons, *Tonijnstukken in olijfolie* voor de olijfolie, *Spinazie met room* voor de verse spinazie en *Kruidenmix gehakt* voor de kruiden, en een besparing van € 11,41. Nu geldt:
  - de titel van de aanbieding wordt gesplitst bij *met*, *in*, *op*, *zonder*, *voor*, *en* en *&*; alleen het deel ervoor is het product. Wat erachter staat telt alleen als het ingrediënt het zelf noemt;
  - een bereide variant (*saus*, *soep*, *mix*, *maaltijd*, *salade*, *sap*, *pizza*, *spread* en een paar meer) telt alleen als het ingrediënt dat woord zelf bevat;
  - een smaakwoord (*kruiden*, *specerijen*) zoekt geen product;
  - past er meer dan één aanbieding, dan wint die met de minste woorden eromheen, niet de eerste in de lijst.
  Het voorbeeld houdt nu 2 van 7 over (tagliatelle en zalmfilet); de besparing telt alleen die twee.
- **Het hoofdwoord van een ingrediënt** stopt bij *met*, *in*, *op*, *zonder*, *voor* en *van*: bij *gehakt met ui* is dat gehakt (voorheen ui), bij *kipfilet in blokjes* kipfilet (voorheen blokjes) en bij *olie voor het bakken* olie.
- **Sinaasappel was ook sinaasappelsap.** De controle op afgeleide producten haalde de *s* van *sap* weg als voegklank. Daardoor telde *Sinaasappelsap* voor *sinaasappels*.

### Aangepast in 2.61.0

- **De besparing in de receptvoorstellen** rekent met de prijs per stuk onder de voorwaarde van de aanbieding. Voorheen was het de oude minus de huidige prijs die de winkel noemt; bij *1+1 gratis* op € 4,50 leidde dat tot € 1,50 in plaats van € 2,25. Zonder herkende aanbieding of zonder gewone prijs is het gedrag zoals eerder.
- De uitleg bij de verkenners van AH, Picnic en Jumbo noemt het onderdeel *aanbiedingen*.

### Goed om te weten

- Bij AH is niet bevestigd of `currentPrice` bij een aanbieding met voorwaarde de gewone prijs of de actieprijs is. De besparing gebruikt daarom de gewone prijs (`priceBeforeBonus`) en de actietekst, en blijft op nul als een van beide ontbreekt.
- De API en de MCP-tools zijn niet veranderd, op de extra velden `mechanic`, `minQuantity`, `unitPrice`, `validity` en `expired` bij `GET /api/offers` na.

## v2.60.2

*English summary: Picnic's offers are now read from where the explorer report showed them: the discount text next to products on the home page ("1+1 free", "20% off", "2 for €5", "now €1.99") and the action pages behind the cards under "Acties van de week" (`promo-group-deep-dive`), where every product is an offer. The label is the tile's text, or else the card's title. Picnic gives no old price.*

### Aangepast in 2.60.2

- **Picnic-aanbiedingen komen nu uit de startpagina.** Het rapport van de verkenner liet zien hoe ze erin staan: bij sommige producttegels staat een kortingtekst ernaast (*1+1 gratis*, *20% korting*, *2 voor €5*, *nu €1.99*, *2e = 50% korting*), en de kaarten onder *Acties van de week* openen een actiepagina (`promo-group-deep-dive`) met de producten van die actie. MealieShopper neemt de producten met een kortingtekst en leest die actiepagina's (met hun parameters, en de pagina's die ze zelf later laden), waar alle producten aanbiedingen zijn. Het label is de kortingtekst van de tegel, of anders de titel van de kaart (bijvoorbeeld *Appels en peren*). Recepten en andere pagina's worden niet gevolgd.
- **Rustig bij Picnic.** Tussen de pagina's zit een korte pauze en er worden hooguit veertig pagina's per synchronisatie gelezen, zodat Picnic niet tijdelijk blokkeert.
- De verwijzingen in de app komen met `nl.picnic-supermarkt://` en een parameter erachter; die worden nu herkend en doorgegeven.
- De Picnic-verkenner leest nu ook de eerste drie actiepagina's, voor als hun opbouw anders blijkt dan verwacht.

### Goed om te weten

- Picnic geeft geen oude prijs bij een aanbieding, dus *oude prijs* ontbreekt; de prijs is die van de tegel.
- Dit is gebouwd op het verkenner-rapport, zonder de actiepagina's zelf te hebben gezien. Geeft een synchronisatie weinig of niets, deel dan het deel van het rapport met *aanbiedingen: GET pages/promo-group-deep-dive...*.

## v2.60.1

*English summary: The Picnic explorer is lighter (no more than a few dozen requests instead of more than a hundred, so Picnic no longer blocks the network halfway through; the old favorites exploration needs `?extended=1`) and reports more about the offers on the home page: the texts next to products and where the "Acties" cards lead.*

### Aangepast in 2.60.1

- **De Picnic-verkenner stuurt veel minder verzoeken.** Het vorige rapport liep halverwege vast op een 403: de verkenning van de favorieten-pagina's stuurde ruim honderd verzoeken achter elkaar en Picnic blokkeert dan tijdelijk. Die verkenning (de favorieten zijn klaar) draait nu alleen met `POST /api/picnic/explore?extended=1`; het gewone rapport blijft ruim onder de veertig verzoeken.
- **Meer over de Picnic-aanbiedingen in het rapport.** Het rapport van de startpagina liet zien dat de aanbiedingen onder *Acties van de week* kaarten per merk of productgroep zijn (geen producttegels) en dat de producttegels geen korting-teken hebben. Het rapport meldt nu ook welke kortingachtige teksten bij de tegels staan (zoals een percentage of een actieprijs, met cijfers als #, zonder productnamen) en waar de kaarten onder *Acties* naartoe leiden (vorm van hun actie en verwijzing, zonder inhoud).

### Goed om te weten

- Picnic-aanbiedingen worden nog niet gevonden: de herkenning wacht op dit rapport.

## v2.60.0

*English summary: The Offers tab shows categories with a count ("5x Vegetables & Fruit", "10x Meat") for every store; clicking one shows its offers. Three rows show first and a "More" button adds three rows at a time. The "Fetch offers" button is gone: the offers come from the local copy, refreshed daily or via Settings > Link. Every sync now also writes the offers to a permanent history in the database (when, price, old price, label, validity), available through `GET /api/offers/history`.*

### Nieuw in 2.60.0

- **Categorieën bij de aanbiedingen, voor alle winkels.** Boven de aanbiedingen staat per categorie een knop met het aantal ("5x Groente & Fruit", "10x Vlees", "9x Frisdrank & Sap"), plus **Alle**. Een klik toont de aanbiedingen van die categorie. De indeling (negentien categoriën, van Groente & Fruit tot Huisdier en Overig) maakt MealieShopper zelf uit de productnaam, omdat AH, Picnic en Jumbo geen gelijke categorieën geven; een categorie-hint van de winkel telt alleen mee als de naam niets zegt. De categorie wordt bij het synchroniseren bewaard.
- **Drie rijen, dan Meer.** Er staan eerst drie rijen aanbiedingen (het aantal per rij hangt van de breedte af); de knop **Meer** toont er steeds drie extra.
- **Geschiedenis van de aanbiedingen.** Elke synchronisatie schrijft de aanbiedingen ook in een geschiedenis in de database, voor altijd bewaard: per product wanneer het in de aanbieding was, met prijs, oude prijs, label, geldigheid, categorie en het aantal synchronisaties dat het erbij stond. Een aaneengesloten periode telt als een keer; een prijswijziging tijdens dezelfde aanbieding niet; komt een product na een onderbreking terug, dan is dat een nieuwe keer. `GET /api/offers/history` (met `store`, `q` en `limit`) toont per product hoe vaak het in de aanbieding was en de details. Het ophalen blijft de lokale kopie vervangen, en mislukt het ophalen dan blijven de kopie en de geschiedenis ongewijzigd.

### Verwijderd in 2.60.0

- **De knop *Aanbiedingen ophalen* op het tabblad Aanbiedingen.** De aanbiedingen komen uit de lokale kopie, die elke dag wordt ververst; de beheerder synchroniseert ze meteen onder **Instellingen > Koppelen**.

### Goed om te weten

- Een database van een eerdere versie wordt vanzelf bijgewerkt (de kolom *categorie* komt erbij); bestaande aanbiedingen krijgen hun categorie uit de naam. De geschiedenis begint bij de eerstvolgende synchronisatie.
- Jumbo-aanbiedingen worden via zoekopdrachten gevonden; een product dat bij een synchronisatie net niet in die zoekresultaten staat telt als weg en als een nieuwe keer als het terugkomt.
- De MCP-tools zijn niet veranderd.

## v2.59.1

*English summary: When Picnic answers the login with a bare "403 Forbidden" page (the gateway in front of the API refusing this network, not a wrong password), MealieShopper now says so and what to try, instead of the generic "Picnic error". Picnic's offers are now looked for on the home page (Discover), because the app has no offers tab; the Picnic explorer reports on those pages first.*

### Aangepast in 2.59.1

- **Duidelijke melding als Picnic het netwerk weert.** Antwoordt Picnic bij het inloggen met een kale *403 Forbidden*-pagina (geen JSON), dan komt dat van de poort vóór de API en niet van de login zelf. MealieShopper meldt nu dat Picnic verbindingen vanaf dit netwerk weigert, dat het niet aan je e-mailadres of wachtwoord ligt, en dat je het later of vanaf een thuisnetwerk kunt proberen (Picnic weert soms server- en datacenter-adressen en blokkeert tijdelijk na veel pogingen). Voorheen stond er alleen *Picnic fout*. Een 403 die de API zelf als JSON geeft blijft een gewone fout.

- **Picnic-aanbiedingen zoeken op de startpagina.** De verkenner liet zien dat de tabbalk van de Picnic-app geen aanbiedingen-tab heeft (alleen Ontdek, Favorieten, Koken, Zoeken en Mandje), dus de eerdere zoektocht naar zo'n tab vond niets. MealieShopper leest nu de startpagina (*Ontdek*) en de pagina's waar die over aanbiedingen naar verwijst, ook de pagina's die ze zelf later laadt, en houdt alleen producten over met een korting-teken (een oude prijs, prijsklassen of een actielabel). Staan er geen aanbiedingen, dan meldt MealieShopper dat en vraagt het rapport van de verkenner.
- **De Picnic-verkenner begint met de aanbiedingen.** Bovenaan het rapport staat per pagina de opbouw, hoeveel producttegels een korting-teken hebben, welke velden en decorators ze hebben en welke pagina's er later laden. Zonder namen, prijzen of id's.

### Goed om te weten

- Het inloggen zelf is niet veranderd, alleen wat je te zien krijgt als Picnic het netwerk weigert. Dat het inloggen daarna weer lukte past bij een tijdelijke blokkade.
- Welk veld Picnic bij een productkorting precies gebruikt is nog niet bekend: de verkenner heeft er een tegel zonder korting van laten zien. Draai hem opnieuw als de lijst leeg blijft.

## v2.59.0

*English summary: The offers of Picnic and Jumbo are now fetched and shown on the Offers tab, next to Albert Heijn. A background task fetches the offers of all stores once a day (default around 5 am; `MEALIESHOPPER_OFFERS_SYNC_HOUR`, switch off with `MEALIESHOPPER_OFFERS_SCHEDULE=false`), and under Settings > Link a new "Sync offers" button fetches them right away. Picnic's offers come from the app's offers tab; Jumbo has no route for all offers, so they are found by searching common terms and keeping products with an action.*

### Nieuw in 2.59.0

- **Aanbiedingen van Picnic en Jumbo.** Het tabblad **Aanbiedingen** toont nu ook de aanbiedingen van Picnic en Jumbo, zodra die gekoppeld zijn, met de oude en de nieuwe prijs en het label. Picnic wordt gelezen van de aanbiedingen-tab van de app (via `/bootstrap`, met een paar bekende paginanamen als terugval). Jumbo heeft geen route die alles in een keer geeft: MealieShopper zoekt op ruim vijftig gangbare termen en houdt de producten met een actieprijs of actielabel over. Dat zijn dus de aanbiedingen bij die termen, niet per se alle.
- **Elke dag vanzelf.** Een achtergrondtaak haalt een keer per dag de aanbiedingen van alle winkels op, standaard rond 5 uur (tijd van de container). Is de app dan uit, dan volgt het bij de volgende start; meerdere workers voeren de dagelijkse run maar een keer uit. Het uur stel je in met `MEALIESHOPPER_OFFERS_SYNC_HOUR`, uitzetten kan met `MEALIESHOPPER_OFFERS_SCHEDULE=false`.
- **Synchroniseren onder Instellingen > Koppelen.** Een nieuw blok **Aanbiedingen** met de knop **Aanbiedingen synchroniseren** (alleen de beheerder) haalt de aanbiedingen van alle winkels meteen op. Eronder staat per winkel hoeveel aanbiedingen er zijn en wanneer ze zijn opgehaald, of dat de winkel niet gekoppeld is of het ophalen mislukte, plus wanneer de dagelijkse taak voor het laatst liep en weer loopt.
- Nieuwe routes: `GET /api/offers/status` en `POST /api/offers/sync-all` (alleen de beheerder).

### Aangepast in 2.59.0

- Een winkel die niet gekoppeld is of waar het ophalen mislukt blokkeert de andere winkels niet; de vorige kopie van die winkel blijft staan.

### Goed om te weten

- De routes van Picnic en Jumbo zijn onofficieel en kunnen veranderen. Geeft een winkel niets terug, dan staat dat als melding bij Koppelen en bij Aanbiedingen.
- De MCP-tools zijn niet veranderd.

## v2.58.0

*English summary: On a large screen (1024 px and wider) the main navigation now sits in a vertical menu on the left instead of above the page. It can be collapsed to icons only (with the name as a tooltip) with the button at the bottom of the menu; the choice is remembered. On smaller screens nothing changes.*

### Nieuw in 2.58.0

- **Navigatiemenu links op een groot scherm.** Vanaf 1024 px breed staan Recepten, Aanbiedingen, URL importeren, Winkels en Mandje onder elkaar in een menu links van de pagina, dat meescrolt. Op kleinere schermen staat het menu zoals altijd boven de pagina, en in de app op een telefoon als menubalk onderaan.
- **Inklapbaar.** Onderaan het menu klapt **Menu inklappen** het in tot alleen de pictogrammen (de naam staat dan als tooltip); met dezelfde knop klap je het weer uit. MealieShopper onthoudt je keuze.

### Goed om te weten

- De API en de MCP-tools zijn niet veranderd.

## v2.57.0

*English summary: The week menu loads by itself when you open it, and the two following weeks are fetched lazily in the background; every week is cached for 30 minutes, so paging through weeks is instant. "Load week menu" is now a sync icon next to the week. The store picker and "Add to basket" moved below the shopping list, and the picker uses the same drop-down as the store picker on Stores. The Basket tab moved to the right of Stores.*

### Nieuw in 2.57.0

- **Het weekmenu laadt vanzelf.** Zodra Mealie gekoppeld is en je het weekmenu opent, staat de week er al; je hoeft niet meer op een knop te drukken. De twee weken erna worden rustig op de achtergrond opgehaald.
- **Weken worden onthouden.** Een opgehaalde week blijft 30 minuten bewaard, dus vorige en volgende week openen direct. Bladeren met de pijltjes laadt de week zelf.

### Aangepast in 2.57.0

- **Synchroniseren in plaats van "Weekmenu laden".** Naast de week staat nu een synchroniseericoon (hetzelfde als bij het Mandje) dat de week opnieuw uit Mealie haalt; de tekst is weg.
- **Winkelkeuze en "Voeg toe aan mandje" onder het boodschappenlijstje.** Ze stonden erboven; nu staan ze eronder. De winkelkeuze is een uitklapmenu in dezelfde stijl als de winkelkeuze bij **Winkels**.
- **Het tabblad Mandje staat rechts van Winkels** (voorheen tweede, na Recepten), ook in de onderste menubalk van de app.

### Goed om te weten

- De API en de MCP-tools zijn niet veranderd.
- Pull requests: #96.

## v2.56.1

*English summary: On Settings > Link the "linked" banners are now as wide as the collapsible blocks below them. On small screens the banners show an icon and the name instead of the text, coloured by status (link icon on green = linked, check mark on dark green = linked and working, crossed-out link on red = not linked), two next to each other. The AH banner now says "Albert Heijn linked" and the Mealie banner just "Mealie linked" (no address). "About" moved from the tab bar into the avatar menu, between Settings and Log out. The blocks are titled just Mealie, Albert Heijn, Jumbo and Picnic (instead of "Link Mealie" and "Log in to ..."), and Jumbo now comes before Picnic, also in the banners.*

### Aangepast in 2.56.1

- **De "gekoppeld"-meldingen zijn even breed als de blokken eronder** (ze liepen over de volle breedte).
- **Kortere titels op de blokken:** *Mealie*, *Albert Heijn*, *Jumbo* en *Picnic* in plaats van *Mealie koppelen* en *Inloggen bij ...*.
- **Nieuwe volgorde:** Albert Heijn, Jumbo, Picnic, zowel bij de meldingen als bij de blokken.
- **Over zit nu in het avatar-menu**, tussen *Instellingen* en *Uitloggen*; de tab Over is uit de tabbalk (die heeft nu zes plekken). Op een klein scherm lichtt de avatar op als Over open is.
- **De Mealie-melding zegt nu *Mealie gekoppeld*** (zonder het adres).
- **Op een klein scherm staan er pictogrammen in de meldingen** in plaats van *gekoppeld*, *nog niet gekoppeld* en *gekoppeld en werkt*: een schakel op groen (gekoppeld), een vinkje op donkergroen (gekoppeld en werkt) en een doorgestreepte schakel op rood (niet gekoppeld), met de naam van de dienst. Ook op een smal scherm staan twee meldingen naast elkaar. De tekst blijft voor schermlezers staan.
- **De AH-melding zegt nu *Albert Heijn gekoppeld*** (was *AH account gekoppeld*), net als *Albert Heijn nog niet gekoppeld* en *Albert Heijn gekoppeld en werkt*.
- `MEALIESHOPER_ASSET_VERSION` is opgehoogd (20261023-31).

### Goed om te weten

- Verder verandert er niets.

## v2.56.0

*English summary: The "Logged in as ..." chip is replaced by an avatar with a drop-down menu (Profile, Settings for the administrator, Log out). On small screens the avatar sits in the tab bar in place of Settings. Profile has General (display name and avatar upload), Security (one Password block with Two-step verification inside it, plus Passkeys) and AI apps (for everyone). Settings, administrator only, has Link, Users, Audit log and Updates. The main tab Settings is gone.*

### Nieuw in 2.56.0

- **Avatar met uitklapmenu in plaats van "Ingelogd als ...".** Menu: **Profiel**, **Instellingen** (alleen de beheerder) en **Uitloggen**. Het werkt ook zonder inloggen (dan zonder Uitloggen), zodat je het wachtwoord en de koppelingen kunt instellen. Op een klein scherm staat de avatar in de tabbalk op de plek van het Beheer-icoon.
- **Profiel > Algemeen:** je displaynaam en een avatar uploaden (PNG, JPEG of WebP; de browser verkleint hem tot 256 x 256, hoogstens 256 kB, geen SVG). Zonder avatar zie je je initialen. De displaynaam staat ook in Instellingen > Gebruikers, met de avatar.
- **Profiel > Beveiliging:** het blok *Wachtwoord* bevat nu ook *Tweestapsverificatie* (geen eigen blok meer), met daaronder *Passkeys*.
- **Profiel > AI-apps** is voor iedereen.
- **Instellingen** (alleen de beheerder): *Koppelen*, *Gebruikers*, *Auditlog* en *Updates*. Het bolletje bij een nieuwe versie staat nu op de avatar en bij Instellingen.
- **Nieuwe routes:** `GET/PUT /api/profile`, `POST/DELETE /api/profile/avatar` en `GET /api/profile/avatar/<gebruiker>`. De avatar staat in de database; het type wordt aan de inhoud gecontroleerd.

### Aangepast in 2.56.0

- **De hoofdtab Beheer is vervallen.** Teksten die ernaar wezen zeggen nu *Instellingen > Koppelen*, *Profiel > Beveiliging* en *Profiel > AI-apps*. De onderste menubalk heeft nog zeven plekken: de avatar staat waar Beheer stond.
- `MEALIESHOPER_ASSET_VERSION` is opgehoogd (20261023-29).

### Goed om te weten

- Verder verandert er niets aan het koppelen, de MCP-tools of de rechten. De screenshots in `docs/screenshots/` laten nog de oude indeling zien.

## v2.55.0

*English summary: Settings > Link is now a plain sub-tab with one page instead of a drop-down menu. The page shows the four "linked" banners (Mealie, Albert Heijn, Picnic, Jumbo) on top, two next to each other, followed by one collapsible block per service: Mealie first, the stores below it. The Unlink button now sits next to the Link button inside the block of the store (together with Check link). The Mealie block is collapsible too and folds up once Mealie is linked.*

### Aangepast in 2.55.0

- **Koppelen is een gewoon subtab** (geen uitklapmenu meer) en toont direct de koppelpagina. De aparte pagina's per winkel en de pagina Mealie zijn samengevoegd: bovenaan de statussen, daaronder de blokken **Mealie koppelen**, **Inloggen bij Albert Heijn**, **Inloggen bij Picnic** en **Inloggen bij Jumbo**.
- **De vier "gekoppeld"-meldingen staan bovenaan, twee naast elkaar** (op een smal scherm onder elkaar).
- **Het blok Mealie is ook inklapbaar** en klapt in zodra Mealie gekoppeld is, net als de blokken van de winkels. De knop *Mealie koppelen* in de melding bovenaan Weekmenu opent het blok altijd.
- **Ontkoppelen staat naast Koppelen.** In het blok van elke winkel staan *Koppelen* (bij Picnic *Inloggen bij Picnic*), *Ontkoppelen* (Picnic, Jumbo en Mealie) en *Koppeling controleren* naast elkaar, zichtbaar zodra de winkel gekoppeld is.
- Eerder in deze ronde (niet apart uitgebracht): menu Koppelen met de winkels, uniforme titels *Inloggen bij ...*, inklapbare blokken en de melding bovenaan.
- `MEALIESHOPER_ASSET_VERSION` is opgehoogd (20261023-28).

### Goed om te weten

- Verder verandert er niets aan het koppelen, de verkenners, de MCP-tools of de API. De screenshots in `docs/screenshots/` laten nog de oude indeling zien.

## v2.54.0

*English summary: Settings > Stores is now Settings > **Link**: one drop-down menu with Albert Heijn, Picnic, Jumbo and Mealie (Mealie no longer has its own tab). The three store pages have the same layout: the "account linked" banner (with Check link, and Unlink for Picnic and Jumbo) on top, and one collapsible block "Log in to ..." that also holds "Finish linking" and the route explorer. The block folds up once the store is linked. The titles are now all "Log in to ...", where Albert Heijn still said "Log in with". Texts that point to Settings > Stores now say Settings > Link.*

### Aangepast in 2.54.0

- **Nieuw submenu Koppelen onder Beheer.** Het uitklapmenu met de winkels is verhuisd naar **Koppelen** en Mealie staat er nu ook in (het tabje Mealie is vervallen). Het menu bevat Albert Heijn, Picnic, Jumbo en Mealie en is alleen voor de beheerder.
- **Alle titels heten nu "Inloggen bij ...".** Bij Albert Heijn stond nog *Inloggen via Albert Heijn*.
- **Inklapbaar blok per winkel.** *Inloggen bij ...*, *Koppeling afronden* en *Routes verkennen* staan nu in een blok dat je in- en uitklapt. Het blok klapt vanzelf in zodra de winkel gekoppeld is en staat open zolang dat nog niet zo is (ook als Picnic om een bevestigingscode vraagt). Een keuze die je zelf maakt blijft staan tot je de pagina herlaadt.
- **De melding "account gekoppeld" staat bovenaan**, boven het blok, met de knoppen *Koppeling controleren* (en *Ontkoppelen* bij Picnic en Jumbo) eronder. Ze blijven zichtbaar als het blok is ingeklapt. De bevestigingscode van Picnic staat nu in het inklapbare blok.
- **Teksten die naar de plek wijzen** (meldingen van de app, de installatiehulp en de README's) zeggen nu *Beheer > Koppelen > ...* in plaats van *Beheer > Winkels > ...*.
- `MEALIESHOPER_ASSET_VERSION` is opgehoogd (20261023-27), zodat browsers de nieuwe bestanden laden.

### Goed om te weten

- Verder verandert er niets aan het koppelen, de verkenners, de MCP-tools of de API. De screenshots in `docs/screenshots/` laten nog de oude indeling zien en volgen bij een volgende ronde.

## v2.53.1

*English summary: The Offers tab now follows the layout of the Stores tab: a store drop-down menu (shown once a second store is linked) followed by two sub-tabs, **Offers** and **Recipes**, instead of a store select box and two blocks below each other. Nothing changes in what the offers and the suggestions do.*

### Aangepast in 2.53.1

- **Aanbiedingen heeft dezelfde opzet als Winkels.** De winkelkeuze is nu het eerste uitklapmenu in de subtabbalk (het verschijnt zodra naast Albert Heijn ook Picnic of Jumbo gekoppeld is, en toont de gekozen winkel), gevolgd door de subtabs **Aanbiedingen** (de producten) en **Recepten** (de receptvoorstellen en `Recepten inlezen`). De keuzelijst boven de blokken is vervallen; de gekozen winkel wordt nog steeds onthouden.
- `MEALIESHOPER_ASSET_VERSION` is opgehoogd (20261023-26), zodat browsers de nieuwe bestanden laden.

### Goed om te weten

- Verder verandert er niets aan de aanbiedingen, de receptvoorstellen, de koppelingen of de API.

## v2.53.0 (beta, tabblad Aanbiedingen)

*English summary: New main tab **Offers**: shows the offers of a store (for now Albert Heijn Bonus, no AH link needed) and suggests recipes with ingredients on offer, from your Mealie recipes, Jumbo favourites, Picnic recipes and saved AH recipes. Matching runs locally, without an AI service. Picnic and Jumbo offers follow once their route is known: the explorers help find it. The main menu now has seven tabs, which also fits the installed app's bottom bar again.*

### Nieuw in 2.53.0

- **Tab Aanbiedingen.** Toont de aanbiedingen van een winkel met oude en nieuwe prijs en het label, bewaard in een lokale kopie met het tijdstip van ophalen. Nu voor Albert Heijn (Bonus, werkt zonder AH-koppeling); `Aanbiedingen ophalen` vernieuwt de kopie en bij een fout blijft de vorige staan.
- **Receptvoorstellen.** `Recepten inlezen` leest de ingrediënten van je Mealie-recepten, Jumbo-favorieten, Picnic-recepten en bewaarde AH-recepten in. Daarna toont het tabblad de recepten waarvan ingrediënten in de aanbieding zijn, gerangschikt op het aandeel in de aanbieding en de geschatte besparing. Het vergelijken gebeurt lokaal (zonder meervoud, eenheden en basisingrediënten als zout en water); er gaat niets naar een AI-dienst.
- **Nieuwe routes:** `GET /api/offers`, `POST /api/offers/sync`, `GET /api/offers/recipes` en `POST /api/offers/recipes/sync`.
- **AH-verkenner** (`POST /api/ah/explore`) probeert nu ook de bonuspagina en meldt de vorm van het antwoord en het aantal gevonden producten.

### Aangepast in 2.53.0

- **Zeven tabs in het hoofdmenu.** De onderste menubalk van de geïnstalleerde app heeft weer evenveel kolommen als tabs (die stond sinds 2.52.0 op zeven bij zes tabs).
- `MEALIESHOPER_ASSET_VERSION` is opgehoogd (20261023-25), zodat browsers de nieuwe bestanden laden.

### Goed om te weten

- De bonusroute van AH is onofficieel en nog niet getest tegen de echte dienst. Antwoordt de bonuspagina niet, dan zoekt MealieShopper producten met de bonusvlag; draai bij twijfel de AH-verkenner.
- Aanbiedingen van Picnic en Jumbo zijn er nog niet: voor beide moet eerst de juiste pagina of query worden gevonden (draai hun verkenner op je installatie).

## v2.53.0

*English summary: First step towards reliable offers for recipes, without AI. The stores' offers are no longer reduced to a yes/no flag: the offer text (such as "2nd half price" or "2 for 5"), the regular price and, where the store gives it, the period are kept, and a new module turns them into one shape with the kind of offer, the minimum quantity, the price per item and whether the offer is still valid. The check list of the week menu shows the offer and until when it applies, and says so when it has expired. The explorers of AH, Picnic and Jumbo now also report what an offer looks like (fields, offer texts and dates; no prices or products) so the store fields can be confirmed with real answers. Offers are not yet matched to recipes; that comes after the explorer reports.*

### Nieuw in 2.53.0

- **Aanbiedingen als eigen onderdeel.** De nieuwe module `mealieshopper/deals.py` leest de actietekst van de winkels ("2e halve prijs", "1+1 gratis", "2 voor 5,00", "25% korting", "€ 1,50 korting") en rekent uit wat één stuk kost als je de voorwaarde haalt. Wat niet met zekerheid te lezen is blijft "onbekend", zodat er geen verkeerde prijs verschijnt. Ook de looptijd wordt gecontroleerd: een aanbieding is geldig, verlopen, nog niet begonnen of zonder datums.
- **De actie, de gewone prijs en de looptijd blijven bewaard.** Bij AH, Jumbo en Picnic bevatten producten met een aanbieding nu een extra veld `promo`. Voorheen bleef alleen over of er een aanbieding was; Jumbo gooide de actietekst weg en AH de gewone prijs.
- **De verkenners laten aanbiedingen zien.** Bij **AH-routes**, **Picnic-routes** en **Jumbo-routes verkennen** staat nu een onderdeel "aanbiedingen" met de velden rond een aanbieding en de actieteksten en datums die de winkel gebruikt. Er staan geen prijzen of producten in, dus het rapport blijft veilig te delen. Bij Jumbo zoekt de verkenner zelf uit welke extra promotievelden bestaan.

### Aangepast in 2.53.0

- **De controlelijst van het weekmenu toont de aanbieding.** Bij een product met een aanbieding staat nu de actie erbij en tot wanneer die geldt ("2e halve prijs t/m 25 okt"). Is de aanbieding verlopen, dan staat er "aanbieding verlopen".

### Goed om te weten

- Of AH, Jumbo en Picnic de actietekst en looptijd echt meegeven en onder welke veldnamen, is nog niet met echte antwoorden bevestigd. Ontbreekt iets, dan blijft het leeg en toont de lijst alleen "Bonus". Draai de verkenners en deel het rapport om dit vast te stellen.
- Er worden nog geen aanbiedingen aan recepten gekoppeld; dat volgt na de verkennerrapporten.
- De API en de MCP-tools zijn niet veranderd, op het extra veld `deal` bij de producten van de controlelijst na.

## v2.52.0

*English summary: "Week menu to shop" and "Find recipes" are merged into one main tab, **Recipes**, with two subtabs: **Week menu** (renamed from "Week menu to shop") and **Find recipes**, both with an icon. The main menu goes from seven to six tabs.*

### Aangepast in 2.52.0

- **Eén tab Recepten met subtabs.** *Weekmenu naar winkel* en *Recepten zoeken* staan niet meer los in het hoofdmenu, maar als subtabs onder de nieuwe tab **Recepten**. Het hoofdmenu heeft daardoor zes tabs in plaats van zeven. Recepten opent op het weekmenu; het logo brengt je daar nog steeds naartoe.
- **Weekmenu naar winkel heet nu Weekmenu**, en beide subtabs hebben een Material Design-icoon (een kalender bij Weekmenu, een vergrootglas bij Recepten zoeken). De teksten zijn in het Nederlands en het Engels bijgewerkt en de README's noemen de nieuwe indeling.
- **De versie van de statische bestanden is opgehoogd** (`MEALIESHOPPER_ASSET_VERSION`, 20261023-23), zodat browsers en de geïnstalleerde app de nieuwe vertalingen en opmaak laden. Zonder dat toonde de tab de sleutel `nav.recipes` en waren de subtab-iconen te groot. De iconen hebben nu ook een vaste grootte.

### Goed om te weten

- Verder verandert er niets aan wat de koppelingen, de MCP-tools of de API doen.

## v2.51.2

*English summary: The planner no longer offers "Jumbo (shopping list)": groceries for Jumbo always go in the Jumbo basket. A remembered choice of the removed option falls back to Albert Heijn. The Jumbo shopping list itself is still shown under Stores > Lists. The API route that wrote to that list is unchanged but no longer used by the app.*

### Verwijderd in 2.51.2

- **De keuze "Jumbo (boodschappenlijstje)" in de planner.** Bij het weekmenu kies je nog tussen Albert Heijn, Picnic en Jumbo; bij Jumbo gaan de ingrediënten altijd in het Jumbo-mandje, niet meer op het boodschappenlijstje. Had je die keuze onthouden, dan staat de planner voortaan op Albert Heijn. Het boodschappenlijstje van Jumbo blijft te zien onder **Winkels > Lijstjes**.
- De bijbehorende teksten zijn weggehaald (de knop, de melding na het toevoegen en de melding dat iets al op het lijstje stond) en de uitleg bij het koppelen van Jumbo noemt het lijstje niet meer als plek voor je boodschappen.

## v2.51.1

*English summary: The "Stores" menu under Settings no longer carries a fixed "Stores" title: its button shows the chosen store (Albert Heijn, Picnic or Jumbo), just like the store picker on the Stores page. The static-file version is raised so browsers load the new script.*

### Aangepast in 2.51.1

- **Beheer > Winkels zonder titel.** De knop van het winkelmenu onder Beheer toont niet meer de vaste titel *Winkels*, maar de gekozen winkel (*Albert Heijn*, *Picnic* of *Jumbo*), zodat de balk er hetzelfde uitziet als bij de winkelkeuze op de pagina **Winkels**. Voor schermlezers heet de knop nog steeds *Winkels*.

### Goed om te weten

- Verder verandert er niets aan wat de koppelingen, de MCP-tools of de API doen.
- Pull requests: bouwt voort op #80 (winkels onder één menu bij Beheer) en #83 (winkelkeuze op Winkels).

## v2.51.0

*English summary: The store picker on the Stores page is now the first drop-down menu in the sub-tab bar, styled like the "Stores" menu under Settings, instead of a separate select box above it. The button shows the chosen store; with only one linked store it stays hidden, as before. The static-file version is raised again so browsers load the new scripts and styles.*

### Nieuw in 2.51.0

- **Winkelkeuze in het menu van Winkels.** De winkelkeuze bovenaan de pagina **Winkels** (Albert Heijn, Jumbo, Picnic) is geen losse keuzelijst meer, maar het eerste uitklapmenu in de subtabbalk, in dezelfde stijl als **Beheer > Winkels**. De knop toont de gekozen winkel; met één gekoppelde winkel blijft hij verborgen, zoals voorheen.

### Goed om te weten

- Het wisselen van winkel werkt zoals voorheen: Picnic begint bij de recepten, Albert Heijn en Jumbo bij de lijstjes.
- Verder verandert er niets aan wat de koppelingen, de MCP-tools of de API doen.
- Pull requests: bouwt voort op #80 (winkels onder één menu bij Beheer) en #82 (versie 2.50.0).

## v2.50.0

*English summary: Version number set to 2.50.0. The static-file version (cache buster) is raised, so browsers and the installed app load the current scripts and translations. Without it, the new "Stores" menu under Settings (added in 2.12.0) could show its raw text key "settings.tab.stores" instead of "Stores"/"Winkels" until the cache was cleared. Nothing else changes.*

### Nieuw in 2.50.0

- **Versienummer 2.50.0.** De versie van MealieShopper is op verzoek van de beheerder op 2.50.0 gezet.
- **Winkelmenu toont weer "Winkels".** Na 2.12.0 kon de knop **Winkels** onder Beheer de sleutel `settings.tab.stores` tonen in plaats van de tekst, omdat een browser of de geïnstalleerde app de oude vertaalbestanden uit zijn cache bleef gebruiken. De versie van de statische bestanden (`MEALIESHOPPER_ASSET_VERSION`) is opgehoogd, zodat de nieuwe bestanden worden geladen.

### Goed om te weten

- Ziet een browser de sleutel toch nog, vernieuw de pagina dan een keer geforceerd (of sluit en open de app opnieuw).
- Verder verandert er niets aan wat de koppelingen, de MCP-tools of de API doen.
- Pull requests: de wijziging die hierop voortbouwt is #80 (winkels onder één menu bij Beheer, 2.12.0).

## v2.13.0

*English summary: The setup guide on the first start now also links Jumbo and Picnic. It has six steps instead of four: Security, Mealie, Albert Heijn, Jumbo, Picnic, Done. The Jumbo step works like Albert Heijn (log in on jumbo.com, paste the line from the browser console); the Picnic step takes your email address and password and, when Picnic asks for it, the code you get by text message or email, all in the same step. Both steps can be skipped, and the final overview lists them with a button to go back. An existing installation with login switched off that only has Jumbo or Picnic linked no longer gets the setup guide either.*

### Nieuw in 2.13.0

- **Jumbo en Picnic in de installatiehulp.** De installatiehulp op de eerste start heeft nu zes stappen: *Beveiliging*, *Mealie*, *Albert Heijn*, *Jumbo*, *Picnic* en *Klaar*. Je hoeft niet meer naar Beheer om je supermarkten te koppelen.
- **Jumbo koppelen.** Werkt net als Albert Heijn: de uitleg voor de inlog via de browserconsole, de knop **Inloggen bij Jumbo** en het plakveld staan in de stap zelf. Dit kan alleen op een computer; gebruik je Jumbo niet of zit je op een telefoon, dan sla je de stap over.
- **Picnic koppelen.** Vul je e-mailadres en wachtwoord in. Vraagt Picnic om een code, dan verschijnt in dezelfde stap een veld voor de code en wordt hij meteen per sms verstuurd; met de knoppen eronder vraag je hem opnieuw aan, of per e-mail. Is de login halverwege afgebroken (bijvoorbeeld door te verversen), dan staat het codeveld er na het openen van de stap weer. Je wachtwoord wordt niet bewaard.
- **Overzicht.** De laatste stap toont ook Jumbo en Picnic, met een knop om alsnog te koppelen als er een ontbreekt. Na afloop is de winkelkeuze in de planner meteen bijgewerkt.
- **Welkomsttekst en README.** De welkomsttekst en de waarschuwing bij *Zonder beveiliging verder* noemen nu alle supermarkten, en de sectie *Eerste start* van de README beschrijft de zes stappen.

### Goed om te weten

- Beide stappen zijn optioneel en gebruiken dezelfde koppelingen als **Beheer > Winkels**; wat je in de installatiehulp koppelt, kun je daar later gewoon wijzigen of ontkoppelen.
- Een bestaande installatie met inloggen uit die al aan Jumbo of Picnic gekoppeld is (en dus niet aan Mealie of Albert Heijn), krijgt de installatiehulp niet meer te zien.

## v2.12.0

*English summary: Under Settings the three store tabs (Albert Heijn, Picnic, Jumbo) are now merged into one "Stores" menu with a drop-down list. The menu button stays highlighted while one of its stores is open, closes when you pick a store, click elsewhere or press Escape, and works on phones too. The settings tab bar is shorter and tidier; nothing changes in what the pages do. Texts that point to the old tabs now say "Settings > Stores > Albert Heijn" (or Picnic, Jumbo).*

### Nieuw in 2.12.0

- **Winkels onder één menu bij Beheer.** De drie winkeltabs *Albert Heijn*, *Picnic* en *Jumbo* staan niet meer los naast elkaar, maar onder één knop **Winkels** met een uitklapmenu eronder. Kies een winkel in de lijst om de koppeling, de instellingen en de verkenner van die winkel te openen. De knop **Winkels** blijft gemarkeerd zolang een van de winkels open staat; het menu klapt dicht als je een winkel kiest, ergens anders klikt of op `Esc` drukt. Het werkt ook op een telefoon.
- **Kortere tabbalk.** Onder Beheer staan nu *Winkels*, *Mealie*, *Beveiliging*, *AI-apps*, *Gebruikers* (alleen de beheerder, met inloggen aan), *Auditlog* en *Updates*. Een gewone gebruiker ziet het winkelmenu niet, net als de losse winkeltabs eerder.
- **Verwijzingen bijgewerkt.** Meldingen, de onboarding, de README's en de voorbeeld-`.env`-bestanden zeggen nu *Beheer > Winkels > Albert Heijn* (of *Picnic*, *Jumbo*) in plaats van *Beheer > Albert Heijn*.

### Goed om te weten

- Alleen de indeling van het menu verandert: de koppelingen, de MCP-tools en de API werken zoals voorheen.
- Pull requests: nog geen; deze versie volgt uit de vraag om de winkels onder Beheer samen te voegen in een menu met submenu.

## v2.11.0

*English summary: New Settings > Audit log (administrator only). It records every request MealieShopper makes to Albert Heijn, Picnic and Jumbo (method, address, status, duration, request and answer) and every request an AI assistant makes over MCP (tool, input, result), including the steps where an assistant links up. A store request made while an MCP tool runs is linked to that tool call, so you can see exactly what an assistant's question caused. Filter by store or errors, search, open an entry for details, export (JSON or CSV) or copy entries, clear the log. Passwords, tokens, codes, cookies and e-mail addresses are replaced by "[verborgen]" before storing; headers are never stored; entries are deleted after 30 days. Four optional settings: MEALIESHOPPER_AUDIT_LOG, _BODIES, _BODY_CHARS and _RETENTION_DAYS.*

### Nieuw in 2.11.0

- **Auditlog.** Onder **Beheer > Auditlog** (alleen de beheerder) staat het verkeer met Albert Heijn, Picnic en Jumbo: elk verzoek met tijd, methode en adres, uitkomst, duur, het verzoek en het antwoord. Een GraphQL-verzoek aan Jumbo of AH heet naar zijn operatie (bijvoorbeeld *GraphQL FavoriteRecipes*). Fouten en netwerkstoringen staan er ook in, en een doorverwijzing telt als één verzoek.
- **MCP-verzoeken van AI-assistenten.** Elk `initialize`, `tools/list` en `tools/call` komt in de log, met de app (bijvoorbeeld *Claude*) en gebruiker erachter, de ingevoerde argumenten en het resultaat. Ook geweigerde verzoeken (geen of ongeldig token, te groot, onbekend protocol) en de OAuth-stappen waarmee een assistent koppelt staan erin; van die stappen bewaren we nooit de tokens.
- **Wat veroorzaakte wat.** Een winkelverzoek dat tijdens een MCP-tool loopt, krijgt hetzelfde correlatie-id als die tool. Bij een MCP-verzoek zie je welke winkelverzoeken eruit volgden (of dat het antwoord uit de lokale kopie kwam), en bij een winkelverzoek door welk MCP-verzoek het kwam.
- **Filteren en zoeken.** Per winkel (Albert Heijn, Picnic, Jumbo, AI-assistenten), alleen fouten, zoeken op actie, adres of fout, en `Meer laden` voor oudere regels. Bovenaan staat per dienst het aantal verzoeken en fouten van de laatste 24 uur. Met `Log wissen` wis je alles; dat blijft zelf als regel in de log staan.
- **Exporteren en kopiëren.** `Exporteren (JSON)` en `Exporteren (CSV)` downloaden de log met de gekozen filters (hooguit de nieuwste 5000 regels, met verzoek en antwoord); `Lijst kopiëren` zet die regels als JSON op het klembord en `Kopiëren` bij een geopende regel doet dat voor die ene regel. Een export komt zelf als regel in de log. In de CSV krijgt een cel die met `=`, `+`, `-` of `@` begint een voorvoegsel, zodat een spreadsheet er geen formule van maakt.
- **Instellingen.** `MEALIESHOPPER_AUDIT_LOG` (uit met `false`), `MEALIESHOPPER_AUDIT_BODIES` (alleen wie, wat en uitkomst bewaren), `MEALIESHOPPER_AUDIT_BODY_CHARS` (standaard 4000) en `MEALIESHOPPER_AUDIT_RETENTION_DAYS` (standaard 30, `0` wist nooit). Zie de README.
- API: `GET /api/audit` (met `service`, `status=error`, `q`, `limit`, `before`), `GET /api/audit/<id>`, `GET /api/audit/export?format=json|csv` en `DELETE /api/audit`, alleen voor de beheerder.

### Goed om te weten

- **Geheimen worden niet bewaard.** Wachtwoorden, tokens, codes, cookies, sessies en e-mailadressen worden vóór het opslaan vervangen door `[verborgen]`, in het adres, het verzoek en het antwoord. Headers bewaren we niet.
- **Antwoorden kunnen persoonlijke gegevens bevatten**, zoals je bestellingen. Alleen de beheerder ziet de log, en na 30 dagen worden regels gewist. Wil je dat niet, zet dan `MEALIESHOPPER_AUDIT_BODIES=false`.
- Verkeer met Mealie en GitHub (de updatecheck) staat bewust niet in de log. Van de AH-loginproxy bewaren we alleen de pagina's, geen stijlen, scripts of inhoud.
- De log legt alleen vast: er verandert niets aan wat de koppelingen of de MCP-tools doen.

## v2.10.0

*English summary: Your own Jumbo recipe lists (such as "Kerst") are now next to Favourites under Stores > Recipes (choose Jumbo): a list picker with the recipe counts, 24 recipes per page with "More recipes", and the same "View" and "Import" buttons. They come from jumbo.com's own stored request (getRecipeListById). The MCP tools list_jumbo_recipe_lists and get_jumbo_recipe_list replace list_jumbo_favorite_recipes (get_jumbo_recipe_list without a list gives the favourites), and the explorer got a probe for an own list. Built from the real request of jumbo.com but tried only with mocked responses so far; the answer is read by its shape (recipe cards and paginationInfo), so a different nesting does no harm.*

### Nieuw in 2.10.0

- **Je eigen receptenlijstjes bij Jumbo.** Onder **Winkels > Recepten** (keuze *Jumbo*) staat nu een keuzelijst met *Favorieten* en je eigen lijstjes (zoals *Kerst*), met het aantal recepten erbij. Elk lijstje werkt als de Favorieten: per 24 met `Meer recepten`, `Bekijken` voor de ingrediënten en de bereiding, en `Importeer` in Mealie. Heb je geen eigen lijstjes of lukt het overzicht niet, dan zie je alleen je Favorieten zoals voorheen.
- **MCP.** `list_jumbo_recipe_lists` (je lijsten met aantallen) en `get_jumbo_recipe_list` (de recepten van een lijst, op naam of id; zonder opgave de Favorieten). Ze vervangen `list_jumbo_favorite_recipes` uit 2.9.0.
- **Verkenner.** **Jumbo-routes verkennen** toont nu ook de proef *een eigen receptenlijst* (aantal en vorm, geen titels).

### Goed om te weten

- Het antwoord van de lijst lezen we aan zijn vorm (de receptkaarten en `paginationInfo`), want de vorm van een eigen lijstje kennen we alleen uit de aanvraag. Klopt het totaal niet, dan telt MealieShopper wat er binnenkomt.
- Alleen getest met nagebootste antwoorden in de vorm van de echte.

## v2.9.2

*English summary: Fix: the Albert Heijn basket is now read correctly. AH nests the product in each shopping-list line (`productDetails.product`) and spells the ticked-off flag `strikedthrough`, so every line used to be skipped. The basket now shows the products with image, brand, size and price. Paging through past orders uses the confirmed row offset.*

### Opgelost in 2.9.2

- **Het AH-mandje bleef leeg.** Het rapport van **AH-routes verkennen** liet zien dat AH het product in elke regel van de boodschappenlijst onder `productDetails.product` zet en het vinkje `strikedthrough` noemt; de eerste versie zocht op een andere plek en sloeg daardoor elke regel over. Het mandje leest nu de regels goed en toont per product de afbeelding, het merk, de inhoud, de prijs, de bonus en het bedrag (aantal x prijs). Afgevinkte regels staan er nog steeds niet bij; een vrije tekstregel zonder product blijft staan.
- **Afgelopen bestellingen:** de verkenner bevestigde dat `offset` rijen telt (bij jou 21 afgeronde bestellingen). Het bladeren is daarop vereenvoudigd en de controle in de verkenner is verwijderd.

## v2.9.1

*English summary: Fix: the Albert Heijn basket in the Basket tab was empty because it showed the active web-shop order, while the weekly menu writes to your shopping list (what you see in the AH app). It now shows that shopping list and refreshes it when you open the tab.*

### Opgelost in 2.9.1

- **Het AH-mandje in het tabblad Mandje toonde niets**, ook als je producten via het weekmenu in je AH-mandje had gezet en ze in de AH-app zag. Die producten staan op je **boodschappenlijst** bij AH, terwijl het tabblad de actieve bestelling van de webwinkel las (die vaak leeg is). Het tabblad toont nu die boodschappenlijst (naam en aantal; afgevinkte regels staan er niet bij), haalt hem op zodra je het tabblad opent en bewaart een kopie, die blijft staan als AH even niet bereikbaar is.
- De tool `get_ah_basket` van de AI-assistent leest dezelfde lijst.
- **AH-routes verkennen** meldt nu ook de vorm van de boodschappenlijst. De vorm van dat antwoord is nog niet tegen een echt account gezien: staat er niets of iets geks, draai de verkenner dan en deel het rapport.

## v2.9.0

*English summary: Your favourite Jumbo recipes are now under Stores > Recipes (choose Jumbo): 24 per page with "More recipes", "View" (ingredients, method, tip, nutrition) and "Import" into Mealie. They come from jumbo.com's own stored request (FavoriteRecipes). A read-only MCP tool (list_jumbo_favorite_recipes) and an explorer probe were added. Your own recipe lists (such as "Kerst") are not shown yet. The recipe view is now shared between Find recipes and the favourites. Built from the real answer of jumbo.com but tried only with mocked responses so far.*

### Nieuw in 2.9.0

- **Je favoriete Jumbo-recepten.** Onder **Winkels > Recepten** (keuze *Jumbo*) staan je favorieten van jumbo.com, per 24 met `Meer recepten`. `Bekijken` toont de ingrediënten, de bereiding, de tip en de voedingswaarden; `Importeer` zet het recept in Mealie. Alle Jumbo-recepten zoek je bij **Recepten zoeken**.
- **MCP.** `list_jumbo_favorite_recipes` (alleen lezen); met `get_jumbo_recipe` haalt een AI-assistent de ingrediënten en bereiding op.
- **Verkenner.** **Jumbo-routes verkennen** toont nu ook de proef *favoriete recepten* (aantallen en vorm, geen titels).

### Goed om te weten

- **Eigen receptenlijstjes ontbreken nog** (zoals *Kerst*). Het overzicht kent ze, maar de aanvraag die de recepten van zo'n lijstje geeft, is alleen bekend voor *Favorieten*.
- Alleen getest met nagebootste antwoorden in de vorm van de echte.

## v2.8.1

*English summary: Picnic orders. Under Stores > Picnic there is a new Orders tab. Upcoming deliveries are fetched live; delivered orders are synced to a local copy in MealieShopper (date, delivery window, status, total and the products with quantity, size and price), so they stay viewable without a connection to Picnic. Sync fetches the overview and the products of up to 25 orders at a time, newest first; a delivered order is never deleted again. Open an order and put all its products back in your Picnic basket with one button (an order is never placed). The MCP tools list_picnic_orders and get_picnic_order read the local copy; get_picnic_delivery fetches one delivery live. No address is stored. Note: this account has no deliveries yet (the explorer shows an empty list), so the shape of an order is built from the known Picnic API and unverified; the explorer now also reports the shape of the first delivery as soon as there is one.*

### Nieuw

- **Bestellingen van Picnic.** Onder **Winkels > Picnic** staat een tab *Bestellingen* met je aankomende bezorgingen (live bij Picnic opgehaald) en je afgeleverde bestellingen: dag, bezorgvenster, status (*Gepland of onderweg*, *Geleverd*, *Geannuleerd*) en totaalbedrag, de nieuwste eerst.
- **Afgeleverde bestellingen worden lokaal bewaard.** De knop *Synchroniseren* haalt het overzicht van je leveringen bij Picnic op en bewaart de afgeleverde bestellingen in MealieShopper, met hun producten. Daarna lees je ze uit die kopie: ook zonder verbinding met Picnic zijn ze te bekijken en te bevragen. Per keer komen de producten van hooguit 25 bestellingen binnen, de nieuwste eerst (*… bestellingen nog zonder producten: synchroniseer opnieuw*). Een afgeleverde bestelling verandert niet meer en wordt nooit verwijderd, ook niet als Picnic hem later niet meer toont; lopende en geannuleerde bestellingen bewaren we niet. Je afleveradres wordt niet bewaard. Is Picnic niet bereikbaar, dan staat bij de aankomende bestellingen een melding en blijven de bewaarde bestellingen zichtbaar.
- **De producten van een bestelling.** Open een bestelling voor de producten met aantal, inhoud en prijs. Met **Alles in mandje** zet je ze met hun aantallen opnieuw in je Picnic-mandje; er wordt nooit een bestelling geplaatst.
- **AI-assistent.** Nieuw: `list_picnic_orders` en `get_picnic_order` lezen de lokale kopie (werkt ook zonder Picnic); `get_picnic_delivery` haalt één levering live op. `list_picnic_deliveries` (al aanwezig) blijft de live lijst, ook met lopende leveringen. Alles alleen lezen en zonder adres.
- API: `GET /api/picnic/orders`, `GET /api/picnic/orders/<id>` en `POST /api/picnic/orders/sync` voor de lokale kopie; `GET /api/picnic/deliveries/<id>` geeft één levering live met haar producten; `GET /api/picnic/deliveries` is nu nieuwste eerst en geeft ook de bezorgtijd (`deliveredStart`, `deliveredEnd`).

### Verkenner

- Nieuw: het aantal leveringen in het overzicht, en, zodra er een levering is, de vorm van de eerste (`GET deliveries/<id>`), zonder waarden.

### Goed om te weten

- **Nog niet met echte gegevens geprobeerd.** Je Picnic-account heeft nog geen leveringen (het verkennerrapport toont een lege lijst), dus ik heb de vorm van een bestelling niet gezien. De lijst en het detail zijn gebouwd naar de bekende Picnic-API en lezen alles voorzichtig: ontbreekt er een veld, dan blijft dat veld leeg in plaats van dat er iets stukgaat. Heb je straks een eerste bezorging, draai dan de verkenner opnieuw en deel het rapport; het onderdeel *bestelling: GET deliveries/<id>* laat zien of de producten goed binnenkomen.
- Tot die tijd staat er *Nog niet gesynchroniseerd* of *Je hebt nog geen bestellingen bij Picnic.*
- Recepten die je met een bestelling hebt besteld (*Opnieuw bestellen* bij de recepten) volgen pas als er bestellingen zijn om dat aan te zien.

## v2.8.0

*English summary: "Find recipes" now searches Albert Heijn, Jumbo and Picnic at the same time (Jumbo and Picnic as soon as they are linked). Results are grouped per store, with "More recipes" for AH and Jumbo; a failing store leaves the others in place. For Jumbo and Picnic, "View" opens the recipe in the same tab with ingredients and method, and "Import" puts it in Mealie. Picnic has no recipe search, so MealieShopper filters your saved recipes and the cookbook by name. The separate Jumbo search under Stores > Recipes from 2.7.0 moved here, and AH results now show the result of an import under the card.*

### Verbeterd in 2.8.0

- **Recepten zoeken bij AH, Jumbo en Picnic tegelijk.** Eén zoekveld; de resultaten staan per winkel bij elkaar. Jumbo en Picnic doen mee zodra ze gekoppeld zijn. Een winkel die niet antwoordt of een fout geeft, laat de andere resultaten staan, met de melding bij die winkel.
- **Meer recepten.** In plaats van Vorige en Volgende laden AH en Jumbo de volgende pagina onder de resultaten die er al staan (`Meer recepten`).
- **Bekijken en importeren bij Jumbo en Picnic.** `Bekijken` opent het recept in hetzelfde tabblad met ingrediënten, bereiding en (bij Jumbo) tip en voedingswaarden; `Importeer` zet het in Mealie.
- **Picnic doorzoeken op naam.** Picnic heeft geen zoekfunctie voor recepten; MealieShopper filtert je bewaarde recepten en het kookboek op de woorden uit je zoekopdracht (accenten en hoofdletters tellen niet).
- **Het Jumbo-zoekveld uit 2.7.0 is verplaatst** van Winkels > Recepten naar Recepten zoeken. Bij AH verschijnt het resultaat van een import nu onder de kaart.

## v2.7.0

*English summary: Jumbo recipes are in MealieShopper. Under Stores > Recipes (choose Jumbo) you search all recipes of jumbo.com, open one to see the ingredients, method, tip and nutrition values, and import it into Mealie as a schema.org recipe (with the jumbo.com link as its source). Pasting a jumbo.com recipe link into the importer also goes through the Jumbo API when Jumbo is linked. Two read-only MCP tools (search_jumbo_recipes, get_jumbo_recipe) and explorer probes were added. Your own recipe lists (Favourites and custom lists) are not shown yet: Jumbo's request that returns the recipes of one list is still needed. Built from the real answers of jumbo.com (getRecipe, getRecipesListers, getRecipeLists) but tried only with mocked responses so far.*

### Nieuw in 2.7.0

- **Recepten van Jumbo.** Onder **Winkels > Recepten** (keuze *Jumbo*) zoek je in alle recepten van jumbo.com, per 24 met `Meer recepten`. `Bekijken` toont de ingrediënten (met de hoeveelheden van het recept), de bereiding, de tip, de voedingswaarden en een link naar het recept op jumbo.com.
- **Importeren in Mealie.** `Importeer` zet het recept in Mealie als schema.org-recept met de link naar jumbo.com als bron, dus Mealie hoeft de Jumbo-pagina niet op te halen (jumbo.com weert servers). Een receptlink van jumbo.com (`jumbo.com/recepten/...`) in de importfunctie gaat, als Jumbo gekoppeld is, ook via de Jumbo-API. Receptnummers met een streepje (`1416890-7`, van Smulweb) werken ook.
- **MCP.** `search_jumbo_recipes` en `get_jumbo_recipe` (alleen lezen).
- **Verkenner.** **Jumbo-routes verkennen** toont nu ook het zoeken, je receptenlijsten, één recept en recepten op id (alleen vormen en aantallen, geen titels).

### Goed om te weten

- **Je receptenlijsten ontbreken nog.** Het overzicht van de site (`getRecipeLists`) kent je lijsten (Favorieten en eigen lijstjes) met hun aantallen, maar toont per lijst alleen titel en plaatje. De recepten van één lijst komen uit een andere aanvraag, waarvan de opgeslagen aanvraag (`operationName`, hash en variabelen) nog nodig is. Bladeren per categorie volgt ook daarna.
- Alleen getest met nagebootste antwoorden in de vorm van de echte.

## v2.6.2

*English summary: Picnic favorites now show only your own favorites. The explorer report, with two products hearted in the Picnic app, showed how the app knows: the Favorites tab lists suggested products under a "Favorieten" heading, and the product page of a favorite carries `likedSellables: {["s…"]: true}` in the code of its heart button (false for the others). MealieShopper now takes the products under the "Favorieten" heading and keeps those whose product page says true. That is one request per product (at most 30), done lazily: the app checks a few at a time and shows the favorites it has found so far, and the server remembers each product's answer for 30 minutes (the Load button forgets it). If Picnic says nothing, the list stays empty with an explanation, as before.*

### Nieuw

- **Favorieten van Picnic: alleen je eigen favorieten.** Het verkennerrapport met twee favoriete producten liet zien hoe de app het weet: de tab *Favorieten* toont voorgestelde producten onder de kop *Favorieten* en een aantal gangpaden, en op de productpagina van een favoriet zet Picnic in de code van de hartjesknop `likedSellables: {["s…"]: true}` (bij de andere producten `false`). In het rapport waren dat precies de eerste twee producten. MealieShopper neemt nu de producten onder de kop *Favorieten* (17 in het rapport) en houdt over wat zijn productpagina als favoriet markeert.
- Dat kost één verzoek per product (hooguit 30), maar **lui en met geheugen**: de app controleert er een paar tegelijk en toont onderweg de favorieten die al gevonden zijn (*Nog 12 producten controleren…*). De server onthoudt per product 30 minuten wat hij wist, en de pagina van de tab twee minuten, dus de volgende keer gaat het direct. De knop *Laden* vergeet dat geheugen en controleert alles opnieuw; zet je in de Picnic-app een hartje, gebruik dan die knop. Een product dat niet laadt wordt overgeslagen en pas na twee minuten opnieuw geprobeerd; is de koppeling verlopen, dan zie je die melding.
- Zegt Picnic niets over favorieten, dan blijft de lijst leeg met de uitleg uit 2.4.3, in plaats van alle producten van de pagina. Zijn er geen favorieten, dan staat er *Je Picnic-favorieten zijn leeg.*
- `list_picnic_favorites` (MCP) gebruikt dezelfde lijst.

### Goed om te weten

- **Alleen wat onder de kop *Favorieten* staat wordt gecontroleerd.** Heb je meer favorieten dan Picnic daar toont (in het rapport 17 plekken), dan missen er mogelijk een paar.
- Zoeken in de favorieten werkt zoals eerder; de knop *In mandje* zet het product in je Picnic-mandje.
- *Opnieuw bestellen* bij de recepten is nog leeg: je account heeft nog niets laten bezorgen.

## v2.6.1

*English summary: The explorer report found how the Picnic app changes a favorite: `POST pages/task/toggle-sellable-favorite` with `{"payload": {"sellable_id": "s…", "status": "UNLIKE"}}`, after which the app reloads the "zero order" page of the Favorites tab. So that page does change with your favorites. Reading them is still open: the product page's heart starts from a state the app fills itself. The explorer now reports the outline of the Favorites page (headings and runs of product tiles), the pages it loads lazily, and the true/false values the server puts in the heart code of a handful of product pages, to see which one marks your favorites. No behaviour change in the app.*

### Verkenner

- Uit het rapport: een favoriet weghalen gaat met **`POST pages/task/toggle-sellable-favorite`** met `{"payload": {"sellable_id": "s…", "status": "UNLIKE"}}` (waarschijnlijk `LIKE` om er een toe te voegen). Daarna laadt de app de pagina `purchase-page-…-zero-order-targeted` opnieuw: die pagina verandert dus mee met je favorieten.
- `/bootstrap` heeft naast de tabs geen favorieten (alleen instellingen), en alle gegokte routes geven 404.
- Nieuw: de opbouw van de pagina van de tab Favorieten in leesvolgorde: koppen en groepen producttegels (`kop: …`, `tegels: 12`). Zo zien we of de favorieten een eigen kopje hebben en hoeveel producten eronder staan. Alleen korte teksten van onderdelen zonder producten komen erin, nooit productnamen.
- Nieuw: de pagina's die de Favorieten-pagina zelf later laadt (`pageConfig`), met hun opbouw en de markering `saved`.
- Nieuw: de waar/onwaar-waarden die de server in de hartjescode van de productpagina's van acht producten zet. Verschillen die per product, dan is dat de markering van je favorieten.

### Goed om te weten

- De app zelf verandert niet; de Favorieten-tab toont nog de uitleg uit 2.4.3.
- De verkenner roept `toggle-sellable-favorite` niet aan: alleen lezen.

## v2.6.0

*English summary: There is now a dark/light mode button next to the language flags (top right, on the login page and in the setup guide). The choice is kept in your browser; without a choice MealieShopper follows your device setting, also when that changes. The AI consent page follows the device setting too.*

### Nieuw in 2.6.0

- **Donkere en lichte modus.** Naast de vlaggetjes staat een knop (een maan in het lichte thema, een zon in het donkere) waarmee je wisselt. Hij staat overal waar de vlaggen staan: bovenaan de app, op de inlogpagina, bij een uitnodiging en in de installatiehulp. De keuze blijft in je browser bewaard (`ms-theme`); zonder keuze volgt MealieShopper de instelling van je apparaat, ook als die tijdens het gebruik wisselt. Het thema wordt al in de kop van de pagina gezet, zodat een donker scherm niet eerst licht oplicht. De themakleur van de browser (`theme-color`) wisselt mee.
- **Donker thema in de huisstijl:** basilicumgroen blijft het accent (lichter op donker, met donkere tekst op groene knoppen), meldingen houden hun kleuren en de onderste menubalk in de geinstalleerde app krijgt een donkere variant. De kassabon en de QR-code blijven licht, zoals op papier.
- **Toestemmingsscherm voor AI-apps** (OAuth) volgt de instelling van je apparaat.

## v2.5.2

*English summary: The last explorer report shows where the favorites are NOT: the Favorites tab of an account without deliveries is a "zero order" page of suggested products (`purchase-page-…-zero-order-targeted`), and the heart on a product reads `getGlobalState("sellable-favorites").likedSellables`, a state the app fills itself, not something in the page. The explorer now reports the full code of the heart button, the pages that load lazily, the fields of `/bootstrap` next to the tabs, the "remove favorite" dialog with the right parameters and a handful of guessed favorites routes, to find what fills that state. No behaviour change in the app.*

### Verkenner

- Uit het rapport: de tab *Favorieten* van een account zonder leveringen toont een pagina met voorgestelde producten (`purchase-page-…-zero-order-targeted`); daar staat niets over jouw favorieten op. Het hartje bij een product leest `getGlobalState("sellable-favorites").likedSellables`: een toestand die de app zelf vult. De pagina's bevatten dus geen favorieten; de app haalt ze ergens anders.
- Nieuw: de volledige code van de hartjesknop op de productpagina (met getallen en id's vervangen door `<id>`), zodat te zien is of er een route of actie achter zit.
- Nieuw: welke pagina's er later nog worden geladen (`pageConfig`: pagina en namen van de parameters).
- Nieuw: de velden van `/bootstrap` naast de tabs (alleen de vorm), in de hoop dat de toestand `sellable-favorites` daar zit.
- Nieuw: de bevestiging bij een favoriet weghalen met parameters als zoekopdracht (de vorige keer was het een 404 omdat de parameters in de verkeerde vorm stonden), en een paar gokken voor favorietenroutes (`/favorites`, `/sellable-favorites`, …).
- *Opnieuw bestellen* is leeg omdat het account nog niets heeft laten bezorgen (`purchases-meals-plan-item-empty`).

### Goed om te weten

- De app zelf verandert niet. Zonder de bron van `sellable-favorites` blijft de lijst op de Favorieten-tab leeg met een uitleg.
- De code van de hartjesknop is Picnics eigen code en kan in uitzonderlijke gevallen de naam van een product bevatten (een melding als *toegevoegd aan favorieten*); persoonlijke gegevens staan er niet in.

## v2.5.1

*English summary: The explorer report shows this account has 0 completed deliveries, so the Favorites tab points to the "no purchases" page, whose `saved` field is empty and the page `purchases-page-root` only has two products; none of them says which products you favorited. The explorer now reports how those pages (and the product page and the "remove favorite" dialog) fetch their data: addresses, requests, waiting components and state, with numbers and ids redacted, so the real favorites source can be found. No behaviour change in the app.*

### Verkenner

- Uit het rapport: dit account heeft **0 afgeronde leveringen**. Daarom wijst de tab *Favorieten* naar de pagina voor een account zonder aankopen, en bevatten de pagina's `purchases-page-root` (2 producten, de rest laadt later) en `purchases_page_no_consent_root` geen markering van favorieten. Het account heeft wel de functies `MANUAL_FAVORITES` en `PPP_PURCHASES_TAB`.
- Nieuw: per pagina (`purchases_page_no_products_root`, `purchases-page-root`, `purchased-deep-dive-page-content`, `meals-purchase-page-root`, de bevestiging bij een favoriet weghalen en een productpagina) hoe die pagina haar gegevens ophaalt: adressen en verzoeken, wachtonderdelen (`SUSPENSE`), de toestand (`state`) en stukjes code over favorieten of opslaan. Getallen en id's zijn vervangen door `<id>`; er komen geen producten of teksten in.
- *Opnieuw bestellen* is leeg omdat het account nog niets heeft laten bezorgen; dat is waarschijnlijk geen fout.

### Goed om te weten

- De app zelf verandert niet; dit is weer een verkenstap om te zien waar Picnic de handmatige favorieten vandaan haalt.

## v2.5.0

*English summary: All your past Albert Heijn orders are now fetched, not just the latest 10: MealieShopper pages back through them. The products of delivered orders are read correctly (AH's "amount" is a quantity, so the line amount is quantity times price, which also fixes orders already stored). Cancelled orders no longer show up as open orders.*

### Nieuw in 2.5.0

- **Al je afgelopen bestellingen**, niet alleen de laatste 10. Uit het rapport van **AH-routes verkennen** bleek dat AH het argument `offset` kent; MealieShopper bladert nu door de afgeronde bestellingen (10 per pagina, nieuwste eerst) tot de oudste. Of `offset` rijen of pagina's telt weten we nog niet zeker; het ophalen probeert beide en neemt wat werkt. Een volgende synchronisatie stopt zodra ze bij bestellingen komt die al bewaard zijn. De producten van maximaal 25 bestellingen komen per keer binnen; staat er "nog zonder producten", synchroniseer dan opnieuw.
- **Geannuleerde bestellingen** komen soms ook bij de open bestellingen voorbij. Die worden nu overgeslagen, en een bestelling die je eerder als open had en later is geannuleerd, wordt verwijderd.
- **Bedragen van productregels kloppen.** Bij AH is `amount` in een bestelling een aantal, geen bedrag. Het bedrag van een regel is nu aantal x prijs. Dat geldt ook voor bestellingen die al waren bewaard: het wordt bij het tonen uitgerekend, je hoeft niets opnieuw op te halen.
- **Verkenner:** de introspectie (staat bij AH uit) en de pagineringsgokken zijn vervangen door een controle hoe `offset` telt, met alleen aantallen en geen id's.

## v2.4.3

*English summary: Picnic favorites no longer fall back to listing every product on the page. The explorer report showed that the Favorites tab of this account points to the "no purchases" page (`purchases_page_no_products_root`): 183 products and a `saved` field that is empty, so there is nothing that says which products are yours. Without a marker MealieShopper now shows no products and says why, and it also tries the `purchases-page-root` page. The explorer gets probes for the purchases pages, the product page's favorite fields and the account's delivery count and purchases consent, so the real source of your favorites can be found next.*

### Opgelost in 2.4.3

- **Favorieten van Picnic toonden nog steeds alle producten.** Het verkennerrapport laat zien waarom: de tab *Favorieten* van dit account wijst naar `purchases_page_no_products_root`, de pagina voor een account zonder aankopen. Daarop staan 183 producten en één leeg veld `saved`; er is dus niets wat zegt welke producten van jou zijn. Zonder markering toont MealieShopper nu **geen producten** maar de melding *Picnic geeft in de app-gegevens niet door welke producten je als favoriet hebt opgeslagen…*, in plaats van de hele pagina als favorieten te presenteren. `list_picnic_favorites` (MCP) geeft dan een lege lijst met `onlyFavorites: false`.
- **Ook de pagina `purchases-page-root` wordt geprobeerd** als de pagina van de tab niets markeert. Markeert die wel producten, dan komen alleen die in de lijst.

### Verkenner

- Nieuw: de pagina's `purchases-page-root` (met en zonder parameters) en `purchases_page_no_consent_root`, met per pagina de markering `saved`.
- Nieuw: welke velden over favorieten er op de productpagina's van de eerste drie producten staan, met hun waarde (alleen waar/onwaar/getal, geen teksten).
- Nieuw: het aantal afgeronde leveringen en de toestemming voor aankopen van je account (alleen een getal en waar/onwaar), plus de namen van functies met `favoriet`, `purchase` of `saved` erin. Daaruit blijkt of Picnic je favorieten voor dit account op een andere plek bewaart.

### Goed om te weten

- **Je favorieten staan er nog niet.** Dit is een tussenstap: eerst niet meer iets verkeerds tonen. Deel na het bijwerken het verkennerrapport; de nieuwe onderdelen *favorieten: …* laten zien waar Picnic je echte favorieten bewaart.

## v2.4.2

*English summary: Fixes "The update check failed (HTTP 403 (rate limit))". The check no longer uses api.github.com, where anonymous requests are limited to 60 per hour per IP address (easily used up on a shared address), but the public releases page, which GitHub redirects to the latest release tag. Nothing else changes.*

### Opgelost in 2.4.2

- **Updatecheck: `HTTP 403 (rate limit)`.** GitHub beperkt anonieme aanvragen naar `api.github.com` tot 60 per uur per IP-adres; op een gedeeld adres was die limiet al op, en dan faalde de updatecheck. De check kijkt nu op `github.com/helmerzNL/MealieShopper/releases/latest`, dat GitHub doorverwijst naar de nieuwste release-tag. Die pagina valt niet onder de API-limiet. Er verandert verder niets: nog steeds hooguit om de zes uur, zonder informatie over jou of de installatie.

## v2.4.1

*English summary: Fixes the Picnic favorites showing all products instead of only your favorites. The Favorites tab of the Picnic app lists far more products than the ones you saved (197 in the explorer report), and the page marks yours with a `saved` field. MealieShopper now keeps only the products Picnic marks as saved, and when the page does not say which are favorites it shows all of them with a notice instead of silently pretending. The explorer reports how that field looks. The MCP tool list_picnic_favorites says whether it filtered.*

### Opgelost in 2.4.1

- **Favorieten van Picnic toonden alle producten.** De Favorieten-tab van de Picnic-app wijst naar een pagina met veel meer producten dan je eigen favorieten (197 in het verkennerrapport). MealieShopper nam ze allemaal mee. Op die pagina staat een veld `saved` dat markeert welke producten je hebt opgeslagen; nu blijven alleen die over. Dat kan een waar/onwaar-vlag bij elk product zijn of een lijst met id's van opgeslagen producten.
- **Geen stil gokken.** Zegt de pagina niet welke producten favoriet zijn, dan staan alle producten van de pagina in de lijst, met de melding *Picnic geeft niet aan welke producten je als favoriet hebt opgeslagen, dus dit zijn alle … producten van de pagina*. Zijn er markeringen maar is er geen enkele favoriet, dan is de lijst leeg. Het antwoord van `GET /api/picnic/favorites` bevat nu ook `total` en `filtered`; `list_picnic_favorites` (MCP) meldt met `onlyFavorites` of er is gefilterd.
- **De verkenner meldt hoe de markering eruitziet:** de vorm van `saved`, hoe vaak het veld voorkomt en hoeveel producten we als favoriet, niet favoriet of onbekend zien. Zo is te zien of het filteren klopt.

### Goed om te weten

- **De markering is afgeleid uit alleen een veldnaam** (`saved`, uit het vorige rapport); de waarden heb ik niet gezien. Staat er na het bijwerken nog steeds een lijst met alle producten, of een lege lijst terwijl je wel favorieten hebt, deel dan het verkennerrapport: het nieuwe onderdeel *de markering `saved`* laat zien hoe Picnic het opslaat.
- De Favorieten-tab van jouw app wijst naar `purchases_page_no_products_root`. Het kan zijn dat Picnic voor accounts zonder afgeronde bestellingen een andere pagina toont dan voor accounts met bestellingen.

## v2.4.0

*English summary: Your Albert Heijn orders are back. Under Stores > Orders you see your upcoming orders (still open, with their delivery time) and your past orders (delivered, kept for good), next to your receipts, and the order basket is back under the Basket tab. The "coming soon" lines are gone.*

### Nieuw in 2.4.0

- **Bestellingen bij Albert Heijn zijn terug.** Onder **Winkels > Bestellingen** staan je **aankomende bestellingen** (nog open, met bezorgmoment en totaal, de eerstvolgende eerst) en je **afgelopen bestellingen** (geleverd, de nieuwste eerst), boven je kassabonnen. Open een bestelling voor de producten. Wat is afgeleverd blijft voor altijd bewaard en geannuleerde bestellingen worden overgeslagen.
- **Het AH-bestelmandje is terug** onder het tabblad **Mandje**, met een knop om te synchroniseren. Is er geen actieve bestelling, dan is het mandje gewoon leeg.
- Het synchroniseer-icoon haalt weer in één keer het mandje, de open en afgeronde bestellingen en de kassabonnen op; een deel dat mislukt stopt de rest niet. De producten van maximaal 15 bestellingen komen per keer binnen (staat er "nog zonder producten", synchroniseer dan opnieuw).
- Het zoekveld zoekt weer ook in de producten van je bestellingen, naast de kassabonnen.
- De "komen binnenkort"-regels zijn weggehaald en `ORDERS_COMING_SOON` bestaat niet meer.
- AH geeft per keer de laatste 10 afgeronde bestellingen. Hoe je verder terug komt, zoekt **AH-routes verkennen** (Beheer > Albert Heijn) uit; draai die en deel het rapport.

## v2.3.0

*English summary: Picnic is now a choice in the store list under Stores, next to Albert Heijn and Jumbo. Choosing Picnic shows two tabs: Recipes (the Picnic recipes from v2.2.0, no longer a separate tab) and the new Favorites, the products from the Favorites tab of the Picnic app, each with a button to put it in your Picnic basket. MealieShopper finds the favorites page itself through the tab bar that Picnic sends to its app (/bootstrap). A read-only MCP tool, list_picnic_favorites, shows them to your AI assistant. The explorer now reports that tab bar. Not tried against a real account yet.*

### Nieuw in 2.3.0

- **Picnic in de keuzelijst bij Winkels.** Naast **Albert Heijn** en **Jumbo** staat nu **Picnic** (zodra het gekoppeld is). Kies je Picnic, dan blijven er twee tabs over: **Recepten** (de Picnic-recepten uit 2.2.0, niet meer een los tabblad) en **Favorieten**. Lijstjes en Bestellingen horen bij AH en Jumbo en verdwijnen bij Picnic; wissel je terug, dan komen de tabs van die winkel terug.
- **Favorieten van Picnic.** De producten uit de tab Favorieten van de Picnic-app, met naam, inhoud, prijs en een aanbiedingslabel, en een knop **In mandje** die er één in je Picnic-mandje zet (er wordt niets besteld). Zoeken op naam kan ook. Nieuwe route: `GET /api/picnic/favorites`.
- **Zelf de favorietenpagina vinden.** Geen enkele library kent de pagina, maar Picnic stuurt zijn app de tabbalk (`/bootstrap`) met per tab de pagina waar hij naartoe gaat. MealieShopper zoekt de tab die over favorieten gaat en haalt die pagina op. Staan er op die pagina zelf geen producten, dan volgt MealieShopper de pagina's waarnaar hij verwijst (maximaal vier), omdat de inhoud van een tab bij Picnic vaak op een aparte pagina staat.
- **Nieuwe MCP-tool, alleen lezen:** `list_picnic_favorites` (naam, inhoud, prijs en aanbieding). Net als de andere Picnic-tools afzonderlijk uit te zetten bij AI-apps.
- **De verkenner meldt de tabbalk** van de app (id, soort, titel, label en pagina per tab) en welke pagina als Favorieten is gekozen, met de vorm van die pagina. Zo is te zien waarom de favorieten leeg blijven als dat gebeurt.

### Goed om te weten

- **Nog niet tegen een echt account geprobeerd.** Het zoeken van de Favorieten-tab op naam (*favoriet*/*favorite* in de id, titel, label of pagina) is een aanname. Blijft de lijst leeg of staat er een melding, deel dan het verkennerrapport: daarin staat de hele tabbalk.
- Antwoordt Picnic voor een pagina met tekst in plaats van JSON, dan kan MealieShopper de favorieten niet lezen en meldt dat.

## v2.2.0

*English summary: Your Picnic recipes are now in the app. Under Stores there is a new Picnic recipes tab with the lists Saved (Cook > Saved), Order again and the Cookbook, as cards like the AH recipes. Opening a recipe shows the ingredients and the preparation, and an Import button puts the recipe in Mealie. The cookbook names are read from the tiles (the explorer showed they have no name in their data), and the route explorer now measures what is on the pages that were empty and looks for the favorites page through field names and ids, without any texts. The favorite products are not found yet.*

### Nieuw in 2.2.0

- **Picnic-recepten in de app.** Onder **Winkels** staat een tabblad **Picnic-recepten** zodra Picnic gekoppeld is. Kies de lijst: **Bewaard** (Koken > Bewaard in de Picnic-app), **Opnieuw bestellen** (recepten die je bestelde) of het **Kookboek**. De recepten staan als kaarten, net als de AH-recepten, met afbeelding, kooktijd en labels, en je kunt zoeken op naam.
- **Een recept openen** toont de ingrediënten (met de hoeveelheid die het recept nodig heeft, bijvoorbeeld *150 g Rode uien*) en de bereidingsstappen, gelezen uit de receptpagina van Picnic. Nieuwe route: `GET /api/picnic/recipes/<id>`.
- **Importeren in Mealie.** Elke kaart en elk geopend recept heeft een knop **Importeer**. MealieShopper stuurt het recept als schema.org Recipe naar Mealie (net als bij de AH-recepten), met naam, afbeelding, porties, tijd, ingrediënten en stappen; Picnic-recepten hebben geen webpagina, dus als bron staat de site van Picnic. Nieuwe route: `POST /api/picnic/recipes/<id>/import`. Is Mealie niet gekoppeld, dan staat er wat je moet doen.
- **Kookboek en Opnieuw bestellen lezen.** De tegels van het kookboek hebben geen naam in hun gegevens (de verkenner liet het zien); de naam wordt nu uit de tekst van de tegel gelezen. `list_picnic_recipes` (MCP) kent nu ook de bron `ordered` en meldt kooktijd en labels.
- **De verkenner zoekt de favorieten.** Hij telt wat er op de lege pagina's staat (soorten onderdelen), zoekt op de zoek-, product- en receptpagina's naar veldnamen en ids rond *favoriet* (alleen namen, nooit teksten van producten of recepten) en probeert nog zeven paginanamen.

### Goed om te weten

- **Nog niet tegen een echt account geprobeerd.** De receptpagina's worden gelezen zoals de community-library `picnic-api` dat doet en getest met nagebootste pagina's in de vorm uit jouw verkennerrapport. Staan er namen of ingrediënten verkeerd, deel dan het verkennerrapport; de ingrediënten en stappen staan op vaste plekken in de pagina die nog niet met een echt recept zijn bevestigd. De afbeelding van een recept staat in het formaat *medium* (kaart) of *large* (recept).
- **Opnieuw bestellen** gaf in het vorige rapport een pagina zonder receptonderdelen; of dat aan de pagina of aan je account ligt, laat de nieuwe telling in het rapport zien.
- **De favoriete producten** (de tab Favorieten in de Picnic-app) staan er nog niet in: de pagina is nog niet gevonden.

## v2.1.1

*English summary: The products of a Jumbo order should now come in. Jumbo's own error message from 1.14.6 showed that the order query expects the order number as a Float (`Variable "$orderId" of type "String!" used in position expecting type "Float!"`), so MealieShopper now asks for it that way first (a plain number). The other variants stay as a fallback. Tried only with mocked responses so far: sync again under Stores > Orders > Jumbo.*

### Opgelost in 2.1.1

- **Producten van Jumbo-bestellingen.** De foutmelding uit 1.14.6 liet zien dat Jumbo bij `order(orderId: …)` een `Float!` verwacht (*Variable "$orderId" of type "String!" used in position expecting type "Float!"*). Dat type en de naam `orderId` bleken dus goed, alleen het type niet. MealieShopper vraagt de bestelling nu eerst met een `Float!` (het nummer als gewoon getal); de andere varianten blijven als terugval. Synchroniseer opnieuw onder **Winkels > Bestellingen > Jumbo**; per keer komen de producten van de 25 nieuwste bestellingen die ze nog missen binnen.

## v2.1.0

*English summary: MealieShopper can now read your Picnic recipes: the ones you saved in the Picnic app and the cookbook, with name and id. For now they are available through a new read-only MCP tool, `list_picnic_recipes`, and the route `GET /api/picnic/recipes`; nothing is imported into Mealie and the app has no recipe screen for them yet, because the explorer report did not show the ingredients yet. The route explorer now also reports where the names, ingredients and images sit in a recipe and tries more pages for the favorites.*

### Nieuw in 2.1.0

- **Picnic-recepten lezen.** De recepten die je in de Picnic-app hebt opgeslagen en het kookboek van Picnic, met naam en id. Dit is gebouwd op de vorm die de verkenner bij jouw account vond: elk receptonderdeel op een pagina heeft `recipe_id` en `recipe_name` in zijn analysegegevens. Nieuwe route: `GET /api/picnic/recipes?source=saved|cookbook`.
- **Nieuwe MCP-tool, alleen lezen:** `list_picnic_recipes` (`source`: `saved` of `cookbook`, en `limit`). Je AI-assistent kan zo je Picnic-recepten bekijken. Er wordt niets opgeslagen of aan je mandje toegevoegd. Net als de andere Picnic-tools afzonderlijk uit te zetten bij AI-apps.
- **De verkenner vertelt meer over recepten.** Hij meldt per receptpagina welke velden de analysegegevens van een receptonderdeel hebben, welke soorten onderdelen erin zitten (en welke velden die hebben, zonder inhoud), en telt de producttegels op elke pagina. Zo zien we waar de ingrediënten, de bereiding en de afbeelding staan.
- **Meer pagina's voor de favorieten.** Nog eens vijftien waarschijnlijke paginanamen worden geprobeerd. Uit het vorige rapport bleek dat `purchased-deep-dive-page-content` wel bestaat; het nieuwe rapport laat zien of dat een lijst producten is.

### Goed om te weten

- **De recepten staan nog niet in het scherm van de app** en worden nog niet in Mealie geïmporteerd: het rapport liet de namen en id's zien, maar nog niet de ingrediënten en de bereiding van een recept. Die komen na het volgende rapport van de verkenner.
- **De favorieten zijn nog niet gevonden.** Geen enkele library kent de pagina en de gokken uit het vorige rapport gaven een 404. Weet je waar in de Picnic-app je Favorieten staan (welk scherm of tabblad)? Dat helpt bij het zoeken.

## v2.0.0

*English summary: The container image is now built for both linux/amd64 and linux/arm64. Before, only amd64 was published, so on an ARM NAS (Synology, Raspberry Pi) the container failed to start with "exec gunicorn failed: Exec format error". Pull the image again to get the right architecture. The image is now also tagged with its version (for example 2.0.0). This is a major version number because the published image changes, not because anything in the app breaks.*

### Opgelost in 2.0.0

- **De image start nu ook op een ARM-NAS.** Op een Synology (of een Raspberry Pi) stopte de container direct met `[FATAL tini] exec gunicorn failed: Exec format error`, omdat de image alleen voor `linux/amd64` werd gebouwd. De workflow `.github/workflows/docker.yml` bouwt en publiceert nu een image voor `linux/amd64` én `linux/arm64`, onder dezelfde tag (`latest`). Docker kiest zelf de juiste architectuur.
- **De image heeft nu ook een versietag.** Naast `latest` en de commit krijgt de image de tag uit `VERSION` (bijvoorbeeld `ghcr.io/helmerznl/mealieshopper:2.0.0`), zodat je een vaste versie kunt vastzetten.

### Goed om te weten

- Draai je MealieShopper op een ARM-NAS? Haal de image opnieuw op (`docker compose pull && docker compose up -d`, of in Container Manager de image bijwerken) zodat je de arm64-versie krijgt.
- Het nieuwe hoofdnummer (2.0.0) komt doordat de gepubliceerde image verandert; in de app zelf is niets wat breekt of wat je moet aanpassen.
- De build duurt langer, omdat de arm64-versie op een amd64-server wordt nagebootst (QEMU). Er verandert niets aan de app zelf.

## v1.15.0

*English summary: Two things for Picnic. The basket now adds up: a discounted line shows the price without discount struck through and what you pay, products that are not available right now are marked with a notice, and the plus button and quantity field stop at the maximum Picnic allows per product. And the route explorer now also checks Picnic's recipe pages (saved recipes, the cookbook, the recipes home and a recipe's details) and tries a list of likely pages for the favorites; that is the first step toward reading your Picnic recipes and favorites into MealieShopper, so nothing is imported yet. Errors in the explorer report now show only the status and error code.*

### Nieuw in 1.15.0

- **Picnic-routes verkennen kijkt naar recepten.** De verkenner roept de receptpagina's van Picnic aan (opgeslagen recepten, kookboek, receptenstartpagina en de details van één recept, zoals de community-libraries `picnic-api` en `mcp-de-picnic` ze kennen) en meldt hoeveel receptonderdelen erop staan, welke velden zo'n onderdeel heeft en naar welke pagina's de pagina verwijst, zonder namen of id's. Is een antwoord geen JSON, dan meldt het rapport dat en de grootte.
- **Zoeken naar de favorieten.** Picnic heeft een favorietenpagina, maar geen library noemt hem. De verkenner probeert een lijst waarschijnlijke paginanamen; een pagina die bestaat wordt met zijn vorm en verwijzingen gemeld, de rest geeft een 404.
- Nog **niets wordt ingelezen**: eerst moet de vorm van die pagina's bekend zijn. Draai de verkenner (Beheer > Picnic) en deel het rapport; daarmee bouwen we het inlezen van je recepten en favorieten.

### Opgelost in 1.15.0

- **Regelprijzen in het Picnic-mandje tellen op tot het totaal.** Bij een aanbieding of lidmaatschapskorting stond de prijs zonder korting, waardoor de regels meer opleverden dan je totaal (bij het testmandje €35,90 tegen €35,03). Nu staat bij zo'n regel de prijs zonder korting doorgestreept en daarachter wat je betaalt. Dit geldt ook voor `get_picnic_basket` (veld `price`, en `listPrice` als er korting is).
- **Waarschuwing bij niet-beschikbare producten.** Picnic meldt per product of het nu leverbaar is. Een product dat dat niet is, krijgt *Niet beschikbaar* achter de naam, en boven het mandje staat dat zulke producten niet worden bezorgd. Ook in `get_picnic_basket` (veld `available`).
- **Maximum per product.** De plus-knop en het aantalveld stoppen bij het maximum dat Picnic per bestelling toestaat en melden het als je er overheen typt. Zet je het via `PUT /api/picnic/cart/items/<product>` toch hoger, dan weigert MealieShopper dat voordat er iets verandert. Veld `maxCount`.

### Goed om te weten

- De kortingsprijs is afgeleid uit de vorm van een echt gevuld mandje: Picnic geeft twee bedragen per regel en het lagere is wat je betaalt. Controleer na het bijwerken dat de regels optellen tot het totaal. Waarom een product niet beschikbaar is, meldt Picnic niet in het mandje.
- In het verkennerrapport staat bij een mislukte route nu alleen de statuscode en de foutcode (bijvoorbeeld `404 NOT_FOUND`), niet meer de tekst van Picnic zelf. Bij alle verkenners tonen de velden `type`, `schema`, `component` en `actionType` hun waarde: dat zijn vaste namen van onderdelen, geen persoonlijke gegevens.

## v1.14.6

*English summary: When Jumbo refuses the requests for the products of an order, the sync message now says what Jumbo answered (with order numbers and ids removed) instead of only "RuntimeError", and the explorer shows the same. This is a diagnostic step: the products of an order still need Jumbo's own stored request (operationName, hash, variables) from the browser's Network tab.*

### Verbeterd in 1.14.6

- **Duidelijke reden als de producten van Jumbo-bestellingen niet binnenkomen.** De melding na het synchroniseren zei alleen `RuntimeError`. Nu staat erin wat Jumbo antwoordde op de acht varianten van de aanvraag (bijvoorbeeld de HTTP-status en de foutmelding), met bestelnummers en id's weggehaald. **Jumbo-routes verkennen** toont dezelfde tekst bij de proef *een bestelling met producten*. De lijst met bestellingen werkt al; de producten wachten tot de opgeslagen aanvraag van de bestelpagina bekend is (`operationName`, hash en variabelen uit het tabblad Netwerk).

## v1.14.5

*English summary: When the update check fails, the Updates tab and the server log now say why (for example "HTTP 403 (rate limit)", "Timeout" or "ConnectionError") instead of only "failed".*

### Verbeterd in 1.14.5

- **Updatecheck: de oorzaak van een fout.** Mislukt de updatecheck, dan staat er nu bij Beheer > Updates ook waarom (bijvoorbeeld `HTTP 403 (rate limit)`, `Timeout`, `ConnectionError` of `SSLError`), en de serverlog bevat de volledige melding. Zo zie je of de server GitHub (`api.github.com`) niet kan bereiken of dat GitHub het verzoek weigert.

## v1.14.0

*English summary: Your completed Jumbo orders are now in MealieShopper under Stores > Orders (choose Jumbo): date, delivery time, total and the products, kept as a local copy, with a search over everything you bought and two read-only MCP tools (list_jumbo_orders, get_jumbo_order). The order list comes from jumbo.com's own stored request (OnlineOrdersAndStoreReceipts); the products of one order are requested with ordinary queries built from the field names of the website's answer, because the stored request for the order detail is not known yet. If Jumbo refuses those, the list still syncs and the sync reports why, and the explorer shows which variant worked. Your delivery address, customer number and reservation number are never stored. Store receipts and running orders are not shown for Jumbo yet. Built from the real answers of jumbo.com but tried only with mocked responses so far.*

### Nieuw in 1.14.0

- **Je Jumbo-bestellingen.** Onder **Winkels > Bestellingen** (keuze *Jumbo*) staan je afgeronde bestellingen met datum, bezorgtijd, totaal en producten (naam, merk, pakmaat, aantal, bedrag, aanbieding en categorie). Synchroniseren haalt de lijst op (nieuwste eerst, per 10) en daarna de producten van maximaal 25 bestellingen die ze nog missen; herhaal het tot niets meer ontbreekt. Een afgeronde bestelling blijft bewaard, ook als Jumbo hem later niet meer toont; geannuleerde bestellingen bewaart MealieShopper niet. Producten die Jumbo niet meer kent (`__MISSING_PRODUCT__`) staan met merk en artikelnummer.
- **Zoeken in alles wat je kocht.** Het zoekveld bovenaan Bestellingen zoekt in de producten van je Jumbo-bestellingen (naam en merk) en toont per product wanneer en hoe vaak je het kocht.
- **Privacy.** Het afleveradres, het winkeladres, je klantnummer en het reserveringsnummer worden niet bewaard en komen niet in het verkennerrapport.
- **MCP.** `list_jumbo_orders` en `get_jumbo_order` (alleen lezen, uit de lokale kopie).
- **Verkenner.** **Jumbo-routes verkennen** toont nu ook de bestellingenlijst (aantallen, statussen, vorm van één bestelling, via welk transport) en de bestelling met producten (aantal regels, vorm, en welke variant van de aanvraag werkt).
- **Bestellingen per winkel.** `orders.js` werkt nu per winkel (de keuze bovenaan Winkels): bij Jumbo ontbreken het bestelmandje en de kassabonnen, bij AH verandert er niets (het bestelmandje en de bestellingen van AH blijven "binnenkort").

### Goed om te weten

- **Producten van een bestelling: nog niet bevestigd.** De opgeslagen aanvraag van de lijst is bekend, die van één bestelling nog niet. MealieShopper probeert daarom gewone aanvragen (`order(orderId: …)` en varianten) met de veldnamen uit het antwoord van de website. Weigert Jumbo die, dan staat de lijst er wel en meldt de synchronisatie de reden; de verkenner laat zien wat er gebeurt. Het herstel is dan een nieuwe constante in `mealieshopper/jumbo.py` met de `operationName`, hash en variabelen van de aanvraag uit het tabblad Netwerk.
- **Niet gedaan:** winkelbonnen (je account heeft er geen; de synchronisatie meldt wel hoeveel Jumbo er kent) en lopende bestellingen. Alles is getest met nagebootste antwoorden in de vorm van de echte; probeer het synchroniseren op je eigen server en draai de verkenner.

## v1.13.1

*English summary: Fixes the Updates tab showing raw text keys such as "update.title". The browser kept using the old cached text files because the asset version was not raised in 1.13.0; it is now. After updating, a hard refresh (Ctrl+Shift+R) may still be needed once.*

### Opgelost in 1.13.1

- **Teksten bij Beheer > Updates.** In 1.13.0 stonden er sleutelnamen zoals `update.title` in plaats van de teksten, omdat de browser nog de oude, bewaarde tekstbestanden gebruikte. Het versienummer van de scripts en teksten (`ASSET_VERSION`) is verhoogd, zodat de nieuwe bestanden worden opgehaald. Zie bij een hardnekkige cache één keer een harde refresh (`Ctrl`+`Shift`+`R`) of open de app opnieuw.

## v1.13.0

*English summary: Settings has a new Updates tab with an update check. MealieShopper looks at GitHub for the latest release and compares it with the running version. When a newer version exists, the administrator gets a notice under the tab bar (with "View" and "Later"), a dot on the Settings tab and on Updates, and the Updates tab links to the release notes and says how to update. The answer is kept for six hours (or ask again with "Check now"), nothing about you or the installation is sent, and `MEALIESHOPPER_UPDATE_CHECK=false` turns it off.*

### Nieuw in 1.13.0

- **Updatecheck.** Bij **Beheer > Updates** staat welke versie er draait en wat de nieuwste release op GitHub is, met een knop **Nu controleren**, een link naar de release-notes en uitleg hoe je bijwerkt (nieuwste image ophalen en de container herstarten).
- **Melding bij een nieuwe versie.** Is er een nieuwere release, dan krijgt de beheerder onder de tabbalk een melding met **Bekijken** (brengt je naar Beheer > Updates) en **Later** (verbergt de melding voor die versie; bij de volgende versie komt hij terug). Het bolletje bij **Beheer** en **Updates** blijft staan tot je bijwerkt. Gewone gebruikers zien niets.
- **Rustig voor GitHub en voor je privacy.** De app vraagt alleen de laatste gepubliceerde release op, hooguit om de zes uur en met een rem op **Nu controleren**. Er gaat geen informatie over jou of de installatie mee. Is GitHub niet bereikbaar, dan staat er een melding en blijft een eerder gevonden update zichtbaar.

### Goed om te weten

- Uitzetten kan met de omgevingsvariabele `MEALIESHOPPER_UPDATE_CHECK=false` (zie de README); het tabblad zegt dan dat de check uit staat.
- Alleen de beheerder ziet en gebruikt de updatecheck (`/api/update`).

## v1.12.0

*English summary: There is a new Basket tab with a store list (Albert Heijn, Picnic, Jumbo, as far as linked). Picnic's basket moved here from the planner, with the quantity, remove and empty controls and the delivery slots; Jumbo's basket is shown read-only; the Albert Heijn order basket moved here from Stores > Orders (it still says "coming soon"). After filling a Picnic or Jumbo basket from the week menu, a "View basket" button takes you to the tab. The tab bar now has seven tabs, so two short Dutch labels got shorter ("Menu", "Import") to fit a phone.*

### Nieuw in 1.12.0

- **Tabblad Mandje.** Na het Weekmenu staat nu een eigen tabblad met een keuzelijst voor de winkels die gekoppeld zijn: **Albert Heijn**, **Picnic** en **Jumbo**. De keuze wordt onthouden in je browser. Met het vernieuw-icoon laad je het mandje opnieuw.
- **Picnic:** het mandje dat je eerder in de planner opende staat hier, met aantallen aanpassen, product weghalen, mandje legen (na een bevestiging), wat je moet betalen en wat je lidmaatschap bespaart. De eerstvolgende bezorgmomenten staan eronder en blijven staan als je het mandje aanpast.
- **Jumbo:** je echte Jumbo-mandje, alleen lezen (producten, aantallen, prijzen en totaal). Aanpassen en bestellen doe je bij Jumbo.
- **Albert Heijn:** het bestelmandje is van **Winkels > Bestellingen** naar hier verhuisd. AH levert het nog niet, dus er staat nog dat het binnenkort komt; zodra dat kan, werkt het synchroniseren vanuit dit tabblad.
- **Vanuit de planner:** na het vullen van een Picnic- of Jumbo-mandje staat er een knop **Mandje bekijken** die je naar het tabblad brengt, met die winkel gekozen. Het mandje en de bezorgmomenten staan niet meer onder het weekmenu.

### Goed om te weten

- De tabbalk heeft nu **zeven tabs**. Zodat ze op een telefoon van 360 px naast elkaar passen, zijn twee korte labels in de onderbalk korter geworden: *Weekmenu* is *Menu* en *Importeren* is *Import*. De lange namen in de bovenbalk op een groot scherm blijven hetzelfde.
- Het Jumbo-mandje is bewust alleen-lezen: het aanpassen van aantallen bij Jumbo is nog niet uitgeprobeerd.

## v1.11.0

*English summary: Your own Jumbo lists ("Mijn lijstjes") and Favourites are now in MealieShopper next to the shopping list, with the same cards, sync, search, adjustable quantities and "Add to basket" as the AH lists. Jumbo serves them through stored (persisted) queries that the website itself uses, so MealieShopper sends the same requests; if Jumbo renews those hashes with a new site version the explorer reports PersistedQueryNotFound and the two hashes in jumbo.py need refreshing. The old "Jumbo-lijstje" is now called "Boodschappenlijstje", and the planner choice "Jumbo (lijstje)" became "Jumbo (boodschappenlijstje)". Two read-only MCP changes: list_jumbo_lists, and get_jumbo_list takes an optional list name. Built from the real answers of jumbo.com but tried only with mocked responses so far.*

### Nieuw in 1.11.0

- **Je eigen Jumbo-lijstjes en Favorieten.** Onder **Winkels > Lijstjes** (keuze *Jumbo*) staan nu alle lijstjes uit *Mijn lijstjes* van Jumbo, naast het boodschappenlijstje. Elk lijstje werkt als een AH-lijstje: synchroniseren naar een lokale kopie, zoeken, het aantal aanpassen (jouw aanpassing blijft bij een volgende synchronisatie staan en je kunt terugzetten naar Jumbo's aantal) en met `In mandje` alles in je Jumbo-mandje zetten. De producten hebben merk, pakmaat, prijs, aanbieding en een link naar het product. Lijstjes met meer dan 25 producten haalt MealieShopper in meerdere stappen op.
- **Opgeslagen queries.** Jumbo's website vraagt deze lijstjes met *persisted queries*: alleen een hash en de variabelen, geen querytekst. MealieShopper stuurt dezelfde aanvraag (`GetCustomerProductLists` en `GetProductList`). Welk transport (POST of GET) en welke clientnaam Jumbo wil is niet bekend; MealieShopper probeert ze na elkaar en onthoudt wat werkt. Verandert Jumbo de hashes, dan geeft de app een foutmelding en toont **Jumbo-routes verkennen** `PersistedQueryNotFound`; zie `PERSISTED_QUERIES` in `mealieshopper/jumbo.py` en de README om ze te verversen.
- **Het boodschappenlijstje** heet nu *Boodschappenlijstje* (voorheen *Jumbo-lijstje*), en de keuze in de planner *Jumbo (boodschappenlijstje)*, zodat het niet met je eigen lijstjes verward wordt. Het blijft het lijstje met losse regels waar de planner ingrediënten op zet.
- **MCP, alleen lezen:** nieuw `list_jumbo_lists` (boodschappenlijstje, eigen lijstjes en Favorieten); `get_jumbo_list` neemt een optioneel `list` (id of deel van de naam, bijvoorbeeld "favorieten"), zonder opgave het boodschappenlijstje.
- **Jumbo-routes verkennen** probeert nu ook beide opgeslagen queries: hoeveel eigen lijstjes en favorieten, de vorm van een lijstje en van een product, en met welk transport en welke clientnaam het werkte. Zonder titels, prijzen of id's.

### Goed om te weten

- De antwoordvormen komen uit de echte aanvragen van jumbo.com (dank aan wie ze uit de browser kopieerde); de code is tot nu toe alleen met nagebootste antwoorden in die vorm getest. Draai **Jumbo-routes verkennen** en synchroniseer één lijstje; meld wat afwijkt.
- Je volgt andere lijstjes op Jumbo? Die toont MealieShopper niet (Jumbo geeft er alleen een aantal van).
- Nog niet voor Jumbo: bonnen, bestellingen en de stap in de installatiehulp.

## v1.10.0

*English summary: You can now manage your Picnic basket from MealieShopper: change the quantity of a product, remove a product, or empty the whole basket (with a confirmation). Changes go straight to your real Picnic basket; an order is never placed. These are not available through MCP. The basket editing routes follow the unofficial Picnic API and have not been tried against a live basket yet.*

### Nieuw in 1.10.0

- **Je Picnic-mandje beheren in de planner.** Kies je Picnic als supermarkt, dan staat naast de keuzelijst de knop **Picnic-mandje bekijken**. Het mandje verschijnt ook vanzelf na het vullen. Per product pas je het aantal aan (min, plus of typen), haal je het uit het mandje (kruisje) of leeg je het hele mandje, dat laatste na een bevestiging. Onderaan staat wat je moet betalen en wat je lidmaatschap bespaart.
- **Zo kun je het weekmenu vaker naar Picnic sturen** zonder dat aantallen verdubbelen: kijk na het vullen in het mandje en zet aantallen terug waar nodig.
- **Directe wijzigingen, nooit een bestelling.** Elke aanpassing gaat meteen naar je echte Picnic-mandje; bestellen en een bezorgmoment kiezen doe je in de Picnic-app.
- Nieuwe routes: `PUT /api/picnic/cart/items/<product>` met `{"quantity": n}` (0 haalt het product weg, 0 tot en met 99) en `POST /api/picnic/cart/clear` met `{"confirm": true}`. Zonder die bevestiging gebeurt er niets.
- **Niet via MCP.** Net als het vullen kan een AI-assistent het mandje niet aanpassen of legen; de MCP-tools blijven alleen-lezen.

### Goed om te weten

- Picnic kent alleen toevoegen en verwijderen. Een exact aantal zetten leest daarom eerst het mandje en voegt het verschil toe of haalt het weg.
- **Nog niet met een echt gevuld mandje geprobeerd.** De routes (`cart/add_product`, `cart/remove_product`, `cart/clear`) volgen de community-libraries; het mandje van het testaccount was leeg. Weigert Picnic `cart/clear`, dan haalt MealieShopper elk product apart weg. Meld wat afwijkt.

### Intern

- Nieuwe test `test_static_scripts`: faalt als twee scripts in `static/` dezelfde globale functie verschillend definiëren (de oorzaak van het Jumbo-probleem uit 1.9.1).

## v1.9.1

*English summary: Fixes Picnic (and Jumbo) error messages that had become too vague: a refused login, a wrong code or an expired link explain themselves again. Messages that can contain a store's own answer still never reach the screen.*

### Opgelost in 1.9.1

- **Duidelijke meldingen bij Picnic en Jumbo.** Een geweigerde login, een onjuiste of verlopen code en een verlopen koppeling zeiden alleen nog "aanvraag ongeldig". Ze noemen nu weer wat er aan de hand is, bijvoorbeeld "controleer e-mailadres en wachtwoord".
- **Het antwoord van de winkel zelf blijft uit beeld.** Die meldingen zijn teksten van MealieShopper. Wat Picnic of Jumbo zelf terugstuurt, gaat alleen naar het logboek; bij een storing zie je een algemene melding (`Picnic fout`).
- **Jumbo stond niet meer in de supermarktkeuze van de planner.** Bij het samenvoegen van de Picnic- en Jumbo-wijzigingen was in `picnic.js` een oude `renderStoreChoice` teruggekomen die de nieuwe uit `app.js` overschreef (alleen AH en Picnic, en een gekozen Jumbo sprong terug naar AH). Die kopie is weg. Een nieuwe test (`test_static_scripts`) faalt voortaan als twee scripts dezelfde functie verschillend definiëren.
- **Koppeling controleren bij Picnic** toont bij een verlopen koppeling de uitleg (opnieuw koppelen) en bij andere fouten alleen het soort fout.

## v1.9.0

*English summary: The Jumbo shopping list is now in MealieShopper. The tab "AH Items" is renamed "Stores" and, once Jumbo is linked, has a store choice at the top: the Jumbo list shows up under Lists with the same cards, quantities and "Add to basket" as the AH lists. The planner gets a fourth choice, "Jumbo (list)", which puts the ingredients on the Jumbo list instead of in the basket. A new read-only MCP tool, get_jumbo_list, shows the list. Also fixes a stale script that hid Jumbo from the planner's store list. Not yet tried against a live Jumbo list: the explorer under Settings > Jumbo now checks the list routes too.*

### Nieuw in 1.9.0

- **Het Jumbo-lijstje.** Onder **Winkels > Lijstjes** zie je, na de keuze *Jumbo*, het ene lijstje van Jumbo met dezelfde kaart, synchroniseer-knop, zoekveld en aanpasbare aantallen als de AH-lijstjes. Het lijstje bestaat uit tekst en soms een product; voor regels met een product haalt MealieShopper titel, pakmaat en prijs erbij. Jumbo kent geen aantallen op het lijstje, dus die zijn lokaal (standaard 1). Afgevinkte regels slaat MealieShopper over.
- **`In mandje`** zet het hele lijstje in het Jumbo-mandje: regels met een product gaan direct, regels met alleen tekst zoekt MealieShopper eerst op in het assortiment van Jumbo. Wat niet te vinden of toe te voegen is, meldt de app.
- **Ingrediënten op het Jumbo-lijstje.** In de planner staat nu naast *Jumbo* ook *Jumbo (lijstje)*: de ingrediënten gaan dan op het boodschappenlijstje in plaats van in het mandje. Wat er al op staat slaat MealieShopper over; staat het lijstje in de Jumbo-app uit, dan zet MealieShopper het aan.
- **Het tabblad heet nu Winkels** (voorheen *AH Items*). Is Jumbo gekoppeld, dan staat bovenaan een keuzelijst met Albert Heijn en Jumbo; bij Jumbo ontbreken de recepten. De teksten in de lijsten zijn winkel-neutraal gemaakt ("In mandje", "Op lijstje: 2" in plaats van "AH: 2").
- **Nieuwe MCP-tool, alleen lezen:** `get_jumbo_list`. Een regel die geen product is, staat erin met alleen zijn tekst. Het lijstje vullen kan niet via MCP.
- **Jumbo-routes verkennen** controleert nu ook het lijstje: de vorm van `shoppingList`, het schrijfpad `editShoppingListItems` (alleen met een lege lijst, dus er wordt niets toegevoegd) en de productgegevens van een regel. Bij een schemafout van Jumbo (een veld of type dat niet bestaat) toont het rapport nu de validatiemelding van Jumbo zelf (alleen veld- en typenamen uit onze eigen query, geen persoonsgegevens); andere fouten blijven alleen hun type tonen.
- Nieuwe routes onder `/api/jumbo/lists` en `/api/jumbo/list`.

### Opgelost in 1.9.0

- **Jumbo ontbrak in de keuzelijst van de planner.** Een verouderd tweede `renderStoreChoice` in `static/picnic.js` (teruggekomen bij het samenvoegen van de Picnic-bezorgmomenten) overschreef de versie die AH, Picnic en Jumbo kent. Het duplicaat is weg en een test bewaakt dat een scriptfunctie maar één keer bestaat.

### Goed om te weten

- De vorm van het Jumbo-lijstje komt uit het community-project `jumbo-mcp`. Of Jumbo precies deze velden en typenamen kent is nog **niet met een echt lijstje bevestigd**; elke query valt terug op een variant zonder foutfragment (zoals bij het mandje bleek nodig). Draai **Jumbo-routes verkennen**, zet één regel op het lijstje en meld wat afwijkt. Het schrijven naar het lijstje is een echte wijziging in je Jumbo-account; er wordt niets besteld.
- Nog niet voor Jumbo: bonnen, bestellingen en de stap in de installatiehulp (volgen in aparte versies).

## v1.8.0

*English summary: Picnic now shows the next available delivery slots under the filled basket and through a new read-only MCP tool, `list_picnic_delivery_slots`. The basket also reports what you actually pay and what your membership saves. The Picnic route explorer checks more routes (account, lists, store, suggestions, product page) so the next steps can be built on real answers. Choosing a slot and placing an order remain in the Picnic app.*

### Nieuw in 1.8.0

- **Bezorgmomenten van Picnic.** Na het vullen van je Picnic-mandje laat de planner de eerstvolgende beschikbare bezorgmomenten zien, met de minimale bestelwaarde. Picnic levert die in het mandje-antwoord mee; ze zijn dus live en er wordt niets gekozen of gereserveerd. Het bezorgmoment kies je zelf in de Picnic-app. Nieuwe route: `GET /api/picnic/slots`.
- **Nieuwe MCP-tool, alleen lezen:** `list_picnic_delivery_slots` (beschikbare bezorgmomenten met begin, einde, uiterste bestelmoment en minimale bestelwaarde). Net als de andere Picnic-tools afzonderlijk uit te zetten bij AI-apps.
- **Mandje met echt totaal.** `get_picnic_basket` en `GET /api/picnic/cart` melden nu ook wat je werkelijk betaalt (`checkoutTotal`, met bezorgkosten en statiegeld) en wat je lidmaatschap bespaart (`savings`).
- **Picnic-routes verkennen kijkt verder.** Naast zoeken, mandje en leveringen worden nu ook account, lijstjes, winkel, suggesties en de productpagina aangeroepen. Alleen lezen en zonder persoonlijke gegevens in het rapport. Zo zien we welke uitbreidingen (lijstjes, productinfo) mogelijk zijn.

### Goed om te weten

- Het zoeken, het mandje en de leveringsroutes zijn bevestigd met een echt Picnic-account: de verkenner gaf voor alle vijf routes een antwoord. De vorm van de bezorgmomenten is daaruit afgeleid. De vorm van een levering en van een gevuld mandje is nog niet gezien (het account had geen leveringen en een leeg mandje); meld wat afwijkt.
- Een bezorgmoment kiezen of een bestelling plaatsen kan niet vanuit MealieShopper, en dat blijft zo.

## v1.7.1

*English summary: Fixes adding products to the Jumbo basket. The explorer, run on a real server, showed that Jumbo's addBasketLines answer cannot be a BasketError, so every add would have failed validation. The query no longer names that type and an error now reports the type Jumbo answered with. The explorer also shows the type name and tries to list the possible answers. The same run confirmed that Jumbo accepts the server, the token refresh, search and reading the basket work.*

### Opgelost in 1.7.1

- **Producten toevoegen aan het Jumbo-mandje** mislukte altijd met een validatiefout van Jumbo (`Fragment cannot be spread here as objects of type "AddBasketLinesResult" can never be of type "BasketError"`). De foutvariant van `addBasketLines` heet in Jumbo's schema anders dan bij `activeBasket`. De query noemt dat type nu niet meer; bij een weigering meldt de app de typenaam die Jumbo teruggaf (bijvoorbeeld `Jumbo weigerde het toevoegen aan het mandje (…)`).
- **Jumbo-routes verkennen** toont bij het (lege) toevoegen nu de typenaam van het antwoord (`Basket` of Jumbo's foutvariant) en probeert de mogelijke antwoorden op te vragen; introspectie staat bij Jumbo meestal uit, en dan zegt de foutmelding dat.

### Goed om te weten

- Het rapport van de verkenner op een echte server **bevestigde**: Jumbo laat de server toe (`auth.jumbo.com` en `www.jumbo.com/api/graphql` antwoorden gewoon), het token vernieuwen werkt, zoeken werkt (met pakmaat en plaatje) en het mandje lezen werkt.
- Het toevoegen zelf is met deze versie nog **niet met een echt product bevestigd**. Zet één product in het Jumbo-mandje en meld de uitkomst; de foutmelding noemt nu de typenaam, waarmee een volgende versie ook de reden kan tonen.

## v1.7.0

*English summary: MealieShopper can now fill a Jumbo basket too. Jumbo moved from REST to Auth0 and GraphQL since the removed v1.2.0 link, so linking now works like Albert Heijn: the administrator logs in in their own browser and pastes the jumbo://callbacks/login line from the console. No headless browser, no stored password. The planner's supermarket list now offers Albert Heijn, Picnic and Jumbo. Jumbo may refuse a server address, so the explorer under Settings > Jumbo first checks reachability. Not yet tried against a live Jumbo account.*

### Nieuw in 1.7.0

- **Jumbo naast Albert Heijn en Picnic.** De beheerder koppelt Jumbo onder **Beheer > Jumbo**: inloggen in je eigen browser en de regel `jumbo://callbacks/login?code=...` uit de console plakken, net als bij AH. Jouw browser lost de bot-controle van Jumbo op, dus er is **geen headless browser (Chromium/Selenium)** nodig. MealieShopper bewaart geen wachtwoord, alleen het refresh-token van Jumbo, versleuteld, en bewaart steeds de nieuwste als Jumbo het vervangt.
- **Zoeken en mandje.** Per ingrediënt zoekt MealieShopper de beste Jumbo-producten (met aanbieding, pakmaat en een link naar het product), rekent het aantal verpakkingen uit en laat twijfelgevallen eerst controleren. De producten komen direct in je echte Jumbo-mandje; er wordt nooit een bestelling geplaatst. Mislukt één product, dan staan de andere er wel in en meldt de app welke ontbreken.
- **De keuzelijst in de planner** biedt nu Albert Heijn, Picnic en Jumbo, voor zover gekoppeld. De controlelijst toont bij AH en Jumbo een link naar het product (`Bekijk product`).
- **Twee nieuwe MCP-tools, alleen lezen:** `search_jumbo_products` en `get_jumbo_basket`. Het mandje vullen kan niet via MCP.
- **Jumbo-routes verkennen** (alleen de beheerder): controleert eerst of Jumbo jouw server toelaat (`auth.jumbo.com` en `www.jumbo.com/api/graphql`) en roept daarna zoeken en het mandje aan; het mandje vullen alleen met een lege lijst. Zonder namen, prijzen of id's, dus veilig te delen.
- Nieuwe routes onder `/api/jumbo/` (koppelen, zoeken, mandje); koppelen en verkennen zijn alleen voor de beheerder.

### Goed om te weten

- **Jumbo kan een server weigeren.** Een eerdere Jumbo-koppeling (v1.2.0) liep vast op de bot-bescherming van Jumbo voor server- en datacenter-adressen. Die routes zijn inmiddels anders (Auth0 en GraphQL in plaats van REST), maar of Jumbo jouw server toelaat is niet vooraf te weten: draai **Jumbo-routes verkennen**. Weigert Jumbo, dan werkt de koppeling vanaf die plek niet en komt er geen headless browser terug.
- De Jumbo-API is **niet officieel** en zonder bericht te wijzigen. De aanroepen volgen de community-projecten `jumbo-mcp` en `jumbo-cli`. Deze versie is getest met nagebootste antwoorden en **nog niet met een echt Jumbo-account**. Vooral het mandje vullen (`addBasketLines`) is alleen elders met cookie-login gezien; gebruik de verkenner en meld wat afwijkt.
- Nog niet voor Jumbo: boodschappenlijstje, kassabonnen, bestellingen, de installatiehulp en recepten.

### Intern

- De winkelmand-route die producten zoekt en in het mandje zet is gedeeld tussen Picnic en Jumbo (`store_cart` in `app.py`); `static/app.js` kent de winkels in één tabel (`STORES`). De tekst bij de productlink in de controlelijst is winkel-neutraal gemaakt.

## v1.6.0

*English summary: MealieShopper can now fill a Picnic basket as well as an Albert Heijn one. The administrator links Picnic under Settings > Picnic (email, password and the code Picnic sends), and the planner then lets you choose the supermarket. Products go straight into the real Picnic basket; an order is never placed. Three read-only MCP tools show Picnic products, your basket and your deliveries. The Picnic API is unofficial and has not been tried against a live account yet: use the explorer under Settings > Picnic to check which routes work.*

### Nieuw in 1.6.0

- **Picnic naast Albert Heijn.** De beheerder koppelt Picnic onder **Beheer > Picnic** met e-mailadres, wachtwoord en de code die Picnic per sms of e-mail stuurt. Daarna kies je in de planner naar welke supermarkt het weekmenu gaat; de keuze wordt onthouden in je browser. Het wachtwoord wordt niet bewaard, alleen de sessie die Picnic teruggeeft, versleuteld. Picnic vernieuwt die sessie zelf; MealieShopper bewaart de nieuwste.
- **Zelfde controlelijst als bij AH.** Per ingrediënt zoekt MealieShopper de beste Picnic-producten, rekent het aantal verpakkingen uit en laat je twijfelgevallen eerst controleren. Een Picnic-product heeft geen webpagina, dus de regel `Bekijk bij AH` ontbreekt daar.
- **Direct in het echte Picnic-mandje.** Anders dan bij AH (een boodschappenlijstje) komen de producten in je winkelmandje. Er wordt nooit een bestelling geplaatst: controleer het mandje in de Picnic-app en reken daar af. Mislukt één product, dan staan de andere er wel in en meldt de app welke ontbreken.
- **Drie nieuwe MCP-tools, alleen lezen:** `search_picnic_products`, `get_picnic_basket` en `list_picnic_deliveries`. Ze halen live bij Picnic op (zonder adres) en zijn bij AI-apps afzonderlijk uit te zetten. Het mandje vullen kan niet via MCP.
- **Picnic-routes verkennen** (alleen de beheerder): toont welke onofficiële Picnic-routes werken en welke velden erin zitten, zonder namen, adressen, prijzen of id's.
- Nieuwe routes onder `/api/picnic/` (koppelen, zoeken, mandje, leveringen); koppelen en verkennen zijn alleen voor de beheerder. Optioneel: `PICNIC_COUNTRY_CODE` (`NL`, `DE` of `FR`).

### Goed om te weten

- De Picnic-API is **niet officieel** en zonder bericht te wijzigen; de aanroepen volgen de community-libraries `python-picnic-api2` en `picnic-api`. Deze versie is getest met nagebootste antwoorden en **nog niet met een echt Picnic-account**: gebruik **Picnic-routes verkennen** om te zien wat er werkt, en meld wat afwijkt.
- Nog niet voor Picnic: lokale kopie van leveringen, de installatiehulp en lijstjes, kassabonnen en recepten (Picnic kent die niet op dezelfde manier).

### Intern

- De verpakkingsrekenmachine (`parse_pack_size`, `assess_packs`, `normalize_quantity`) staat nu in `mealieshopper/packs.py` en is voor alle supermarkten gedeeld; `ah.py` blijft ze doorgeven, dus bestaande aanroepen werken ongewijzigd. De controlelijst-route is gedeeld tussen AH en Picnic.

## v1.5.3

*English summary: Click the logo in the header to go to the start screen (the first tab).*

### Nieuw in 1.5.3

- **Het logo in de kop is klikbaar** en brengt je naar het startscherm (Weekmenu naar winkel), vanaf elke tab. Met Ctrl, Cmd of de middelste muisknop opent het startscherm in een nieuw tabblad. Het logo is ook met het toetsenbord te bereiken.

## v1.5.2

*English summary: You can now search within your receipts: type a product (for example "soda") and see which receipts contain it, with the matching lines, and the matching lines are highlighted when you open a receipt.*

### Nieuw in 1.5.2

- **Zoeken in kassabonnen.** Typ een product (bijvoorbeeld "frisdrank") in het zoekveld en je ziet op welke kassabonnen het staat, met per bon de regels die passen. Zoeken negeert hoofdletters en accenten en alle woorden moeten erin staan. Open je een bon vanuit de zoekresultaten, dan zijn de passende regels geel gemarkeerd.
- Bonnen waarvan de producten nog niet zijn opgehaald kunnen nog niet worden doorzocht; de app zegt hoeveel dat er zijn en vraagt om opnieuw te synchroniseren.
- Nieuwe route `GET /api/ah/receipts/search?q=`, lokaal en alleen voor ingelogde gebruikers.

## v1.5.1

*English summary: Receipts now look like an Albert Heijn paper receipt. Delivered orders are fetched from AH (the latest 10 closed orders per request) and an empty order basket is no longer an error. Because AH's order routes are not available right now, the order basket and orders show a "coming soon" line and only receipts are synced. The explorer was extended to find out how to reach further back than 10 delivered orders.*

### Nieuw in 1.5.1

- **Kassabonnen zien eruit als een AH-kassabon**: een strook papier met aantal, omschrijving, prijs en bedrag, de bonusregels, het totaal en de betaalwijze, zonder eurotekens zoals op papier. Het past ook op een telefoon en blijft licht in de donkere modus.
- **Het bestelmandje en de bestellingen komen binnenkort.** AH had een storing en de bestelroutes zijn nog niet beschikbaar, dus de app toont daar een regel dat het binnenkort komt en het synchroniseer-icoon haalt alleen kassabonnen op. De code staat klaar: in `static/orders.js` zet je `ORDERS_COMING_SOON` op `false` om mandje en bestellingen weer te tonen. De bijbehorende MCP-tools blijven bestaan en zijn bij AI-apps uit te zetten.
- **Afgeronde bestellingen** haalt MealieShopper nu ook op uit AH (de verkenner liet zien dat dat kan met status `CLOSED`). AH geeft er per keer de laatste 10; wat is afgeleverd wordt voor altijd bewaard, geannuleerde bestellingen worden overgeslagen. Van maximaal 15 bestellingen per keer komen de producten binnen; de rest volgt bij de volgende synchronisatie.
- **Een leeg bestelmandje is geen fout meer.** Zonder actieve bestelling antwoordt AH met een 404; dat geldt nu als een leeg mandje.
- **Verkenner uitgebreid** (alleen de beheerder): een lijst in het rapport toont de samenvoeging van alle elementen (alle statussen, getaltypes) en de verkenner onderzoekt nu hoe je verder terug komt dan 10 afgeronde bestellingen (argumenten van AH, vijf pagineringsvarianten) en wat er in een afgeleverde bestelling zit.

## v1.5.0

*English summary: MealieShopper can now keep your Albert Heijn order basket, orders and in-store receipts as a local copy, so you can browse and search your purchase history without a connection to AH, and your AI assistant can query it. Delivered orders are archived permanently once an open order has been delivered. The administrator can switch each MCP tool off individually. An owner-only explorer reports which of the unofficial AH routes work, to find out how to fetch past orders.*

### Nieuw in 1.5.0

- **Bestelmandje, bestellingen en kassabonnen** onder AH Items > Bestellingen. Het bestelmandje is de actieve bestelling bij AH (en dus iets anders dan de boodschappenlijst waar "In AH-mandje" naartoe schrijft; dat staat ook in de interface). Open bestellingen komen uit AH; kassabonnen zijn je aankopen in de winkel.
- **Alles staat in een lokale kopie.** Bekijken en zoeken gaan uit die kopie en vragen nooit iets aan AH, dus de bestelhistorie is ook te zien als AH niet bereikbaar is. Alleen het synchroniseer-icoon praat met AH; mislukt dat, dan blijft de kopie staan en zie je de fout.
- **Slim synchroniseren.** Een volgende synchronisatie haalt alleen op wat nieuw is. Het bestelmandje is een momentopname. Een open bestelling wordt bijgewerkt zolang hij open is; verdwijnt hij uit de open bestellingen, dan haalt MealieShopper hem een laatste keer op en bewaart hem als **afgeleverd**, voor altijd. Kassabonnen komen er in porties van 40 bij, zodat een synchronisatie kort blijft.
- **Zoeken in al je aankopen**: wanneer en hoe vaak kocht ik iets, in bestellingen en op kassabonnen, met het detail van elke bestelling en bon.
- **AI-assistenten** kunnen dit bevragen met zes nieuwe tools (alleen lezen, uit de lokale kopie, met het tijdstip van de laatste synchronisatie): `get_ah_basket`, `list_ah_orders`, `get_ah_order`, `list_ah_receipts`, `get_ah_receipt` en `search_ah_purchases`. Het toestemmingsscherm noemt dit.
- **Tools afzonderlijk uitzetten.** De beheerder zet bij AI-apps elke MCP-tool los aan of uit, voor alle gebruikers. Een uitgezette tool verdwijnt uit het overzicht van de assistent en wordt geweigerd als hij toch wordt aangeroepen; andere gebruikers zien in de lijst dat hij is uitgezet.
- **Privacy:** het afleveradres wordt niet opgehaald en niet bewaard, alleen datum en tijdvak. Bestellingen en kassabonnen horen bij het gedeelde AH-account en zijn voor alle gebruikers te zien, net als de lijstjes.
- **AH-routes verkennen** (alleen de beheerder, onder Beheer > Albert Heijn): roept de onofficiële leesroutes aan en meldt welke werken en welke velden erin zitten, ook welke statussen AH voor bestellingen kent. Er wordt niets gewijzigd of bewaard en het rapport bevat geen namen, adressen, prijzen of id's. Daarmee zoeken we uit hoe afgeleverde bestellingen uit het verleden zijn op te vragen.
- Een bestelling waarvan je het nummer kent, is al op te halen met de code (`add_order_by_id`); de knop daarvoor volgt zodra de verkenner laat zien hoe AH de lijst van afgeleverde bestellingen geeft.

### Goed om te weten

- Deze AH-routes zijn onofficieel en niet door ons tegen een echt account geprobeerd: ze volgen de community-bibliotheek `appie-go`. Gebruik **AH-routes verkennen** als iets niet werkt en deel het rapport.

## v1.4.1

### Fixes

- **Aangepaste aantallen in een AH-lijstje blijven staan bij synchroniseren.** Synchroniseren voegt nieuwe items toe en haalt weg wat bij AH is verdwenen, maar laat een aantal dat jij hebt aangepast met rust; alleen het aantal van AH (waar de resetknop naar terugzet) wordt ververst. Regels worden nu gekoppeld op het id dat AH aan de regel geeft (en daarna op het product), zodat een regel dezelfde regel blijft bij een andere volgorde, een nieuw id of een product dat er twee keer in staat. Het bijwerken gebeurt in één transactie, zodat een aanpassing tijdens het synchroniseren niet verloren gaat. Na het synchroniseren staat er wat er is gebeurd, bijvoorbeeld "2 nieuwe items, 1 aangepast aantal behouden."
- **Een mislukte opslag van een aantal is nu zichtbaar.** Mislukte het opslaan, dan werden de aantallen stil teruggezet en wiste het opnieuw laden de foutmelding; nu verschijnt de melding na het laden.
- **De resetknop vraagt eerst om bevestiging.** Je ziet hoeveel aangepaste aantallen worden teruggezet naar die van AH, en kunt annuleren.
- **Een AI-app koppelen terwijl je nog niet bent ingelogd werkt nu.** De toestemmingspagina stuurde je naar de inlog, maar na het inloggen bleef je in de app staan en kwam je niet meer bij het toestemmingsscherm. Nu keert de pagina na het inloggen terug naar de toestemming, en alleen naar een adres van `/oauth/authorize` (geen open redirect). Dit gebeurde alleen als je op dat moment niet was ingelogd; wie al was ingelogd merkte er niets van.

## v1.4.0

*English summary: MealieShopper now has an MCP server, so you can ask Claude, ChatGPT or GitHub Copilot about your week menu, recipes and AH lists and let them import a recipe into Mealie. Login is OAuth 2.1 with PKCE; every token belongs to the user who gave consent. There is deliberately no tool that fills the AH basket or deletes anything.*

### Nieuw in 1.4.0

- **MCP-server** op `<adres>/mcp` (Streamable HTTP, zonder sessies of streams), zodat je Claude, ChatGPT of Copilot je weekmenu, recepten en lijstjes kunt laten bekijken en een recept in Mealie kunt laten importeren. Tools: `get_week_menu`, `search_allerhande_recipes`, `list_saved_ah_recipes`, `list_ah_lists`, `get_ah_list`, `search_ah_list_items`, `search_ah_products` en `import_recipe_to_mealie`. Er is bewust **geen** tool die het AH-mandje vult of iets verwijdert; tools zijn gemarkeerd als alleen-lezen of (de import) als aanmakend.
- **Inloggen met OAuth 2.1** voor alle assistenten: metadata volgens RFC 9728 en RFC 8414, dynamic client registration (alleen publieke clients, alleen https-, loopback- of app-redirects), verplichte PKCE (S256), een toestemmingsscherm in het Nederlands en Engels, en intrekken via `/oauth/revoke`.
- **Per gebruiker.** Elke code en elk token hoort bij de gebruiker die toestemming gaf en geeft alleen die rechten. Een nieuw wachtwoord, uitgezette tweestapsverificatie, een nieuwe inloglink of het verwijderen van de gebruiker maakt zijn of haar tokens ongeldig.
- **Veilig bewaard.** Tokens staan alleen als hash in de database. Toegangstokens gelden een uur; verversingstokens 30 dagen en worden bij elk gebruik vervangen. Een verversingstoken of code die twee keer wordt gebruikt, trekt de hele reeks in.
- **Beheer > AI-apps** voor elke gebruiker: het adres van de MCP-server met een kopieerknop, uitleg voor Claude, Claude Code, ChatGPT en Copilot, wat de assistent kan, en de gekoppelde apps met een knop om toegang in te trekken. Bij een adres zonder https volgt een waarschuwing.
- **Instellen in de app, alleen door de beheerder.** Bij Beheer > AI-apps staat een schakelaar om de MCP-server aan of uit te zetten en het publieke adres van MealieShopper. Dat adres wordt automatisch herkend (met de `X-Forwarded-*`-headers van je proxy) en kan worden overschreven; voorrang: ingesteld in de app, dan `MEALIESHOPPER_PUBLIC_BASE_URL`, dan automatisch. Het adres gebruiken ook de uitnodigingslinks, de AI-koppeling en het voorbeeldplaatje. De schakelaar gaat voor op `MEALIESHOPPER_MCP`, dat nu alleen de beginwaarde geeft. Gewone gebruikers zien de instellingen niet en krijgen een 403 op `/api/server-settings`; verandert het adres, dan moeten gekoppelde AI-apps opnieuw toestemming vragen.
- **Altijd achter inloggen.** De server staat alleen aan als inloggen aanstaat, zodat de MCP-ingang nooit open staat op een installatie zonder beveiliging.
- README (nl en en) met een hoofdstuk over AI-assistenten, screenshots en de nieuwe variabele; de `.env`-voorbeelden zijn bijgewerkt.
- **De reset-opdracht in de README** laat nu ook alle sessies en AI-apps vervallen (de sessieteller gaat omhoog), zodat een gestolen token niet blijft werken na een reset.
- **Geen netwerktoegang via een recept-URL.** `import_recipe_to_mealie` weigert adressen op een eigen netwerk (`localhost`, privé-IP's, `.local`), zodat een assistent die door een tekst op een webpagina op het verkeerde been wordt gezet Mealie geen interne adressen laat ophalen.
- **Registratie blijft beschikbaar.** Apps die zich registreren maar nooit toestemming krijgen, verdwijnen na een dag (en na 15 minuten als de lijst vol zit), zodat niemand de registratie kan blokkeren.

## v1.3.0

*English summary: MealieShopper now supports multiple users. The first user is the administrator, who links Mealie and Albert Heijn and manages the users. Housemates join through a one-time invitation link and choose their own password or passkey; they can use the basic functions (week menu, recipes, lists, basket) but cannot link or manage anything.*

### Nieuw in 1.3.0

- **Meerdere gebruikers.** De eerste gebruiker is de **beheerder**: die koppelt Mealie en Albert Heijn en beheert de gebruikers. Iedereen die je uitnodigt is een **gebruiker** met de basisfuncties: weekmenu bekijken en in het AH-mandje zetten, recepten zoeken en importeren, AH-lijstjes en -recepten bekijken, lijstjes in het mandje zetten en aantallen aanpassen.
- **Uitnodigen met een link.** Onder Beheer > Gebruikers maak je een link die één keer werkt en na 7 dagen verloopt. De uitgenodigde kiest zelf een gebruikersnaam en een wachtwoord of passkey; de beheerder kent het wachtwoord dus nooit. De link wordt alleen gehasht bewaard en je ziet hem maar één keer.
- **Inlog kwijt? Nieuwe inloglink.** De beheerder maakt per gebruiker een resetlink. De oude inlog (wachtwoord, passkeys en tweestapsverificatie) wordt pas vervangen als de nieuwe is ingesteld.
- **Gebruikers verwijderen** haalt het account en alle inloggegevens weg. De beheerder kan niet worden verwijderd.
- **Eigen inlog per gebruiker.** Wachtwoord, tweestapsverificatie (TOTP en herstelcodes) en passkeys zijn per gebruiker, en een nieuw wachtwoord logt alleen die gebruiker overal uit (de sessieteller staat nu per gebruiker). Een gebruiker ziet en beheert alleen de eigen passkeys en kan de eigen naam niet wijzigen.
- **Koppelen alleen voor de beheerder, ook op de server.** Mealie en AH koppelen en de gebruikersbeheer-API geven een gewone gebruiker een 403. De status van de koppelingen blijft voor iedereen leesbaar. In de interface zien gebruikers de tabs Albert Heijn, Mealie en Gebruikers niet, en bij een ontbrekende Mealie-koppeling staat "Vraag de beheerder om Mealie te koppelen."
- **De reset-opdracht in de README** verwijdert nu ook de andere gebruikers en openstaande uitnodigingen, zodat de installatiehulp na een reset weer schoon begint.
- README (nl en en) uitgebreid met een hoofdstuk over gebruikers en uitnodigen, met screenshots.
- **Fix:** de melding bij een niet-gekoppeld AH-account verwees nog naar de oude tab "AH koppelen"; die noemt nu Beheer > Albert Heijn en de beheerder.
- Bestaande installaties hoeven niets te doen: de bestaande gebruiker blijft de beheerder en behoudt zijn sessies.

## v1.2.0

*English summary: this is the first tagged release. It covers everything from v1.0.0 onwards: a week menu that fills your Albert Heijn basket, recipe search and import into Mealie, saved AH recipes and favourite lists (with local copies, search, editable quantities and a one-press "add the whole list to the basket"), a first-run onboarding with passkey or password (+ optional two-step verification) login, a Dutch/English interface, an About tab, installable PWA and a bilingual README with screenshots.*

### Nieuw in 1.2.0 (sinds v1.0.0)

- **Een lijstje in één keer in je AH-mandje.** In een geopend lijstje zet de knop **In AH-mandje** alle producten met de huidige aantallen in je winkelmandje ([#34](https://github.com/helmerzNL/MealieShopper/pull/34)).
- **Aantallen in een lijstje aanpassen.** Met `−`/`+` of door te typen (1 tot 99). Een aangepast aantal blijft staan na een nieuwe synchronisatie en het AH-aantal staat ernaast (`AH: 2`).
- **Reset-icoon** dat alle aangepaste aantallen terugzet naar de waarden van AH.
- **Synchroniseren als MDI-icoon** in plaats van tekst, per lijstkaart, in het lijstje en bij "Alles synchroniseren".
- **Nieuwe README** in het Nederlands en Engels, met screenshots van desktop en telefoon, de integraties en de koffie-melding onderaan ([#34](https://github.com/helmerzNL/MealieShopper/pull/34)).
- **Het geheim wordt automatisch aangemaakt** (`secret.key` in de data-map) en de `.env`-voorbeelden zijn tweetalig ([#33](https://github.com/helmerzNL/MealieShopper/pull/33)).
- **Fix:** de twijfelmeldingen bij het AH-mandje zijn nu ook in het Engels.
- Bestaande databases krijgen vanzelf de extra kolom voor de AH-aantallen; er is niets te migreren.

### Alle functies vanaf v1.0.0

**Weekmenu naar je AH-mandje**
- Het weekmenu uit Mealie, met de plaatjes uit Mealie ([#30](https://github.com/helmerzNL/MealieShopper/pull/30)), en alle ingrediënten in één keer in je AH-winkelmandje.
- Het aantal verpakkingen volgt de verpakkingsgrootte, binnen 1 tot 99. Is de app er niet zeker van, dan kies je zelf in een controlelijst ([#24](https://github.com/helmerzNL/MealieShopper/pull/24)), met bij elke regel een link naar het product bij AH ([#25](https://github.com/helmerzNL/MealieShopper/pull/25)).

**Recepten**
- Allerhande-recepten zoeken via GraphQL ([#28](https://github.com/helmerzNL/MealieShopper/pull/28)) en importeren in Mealie via de AH-API, zonder 403 ([#22](https://github.com/helmerzNL/MealieShopper/pull/22)).
- Een recept importeren via een URL, met een terugval voor pagina's die Mealie zelf niet kan ophalen.

**AH Items**
- Tabblad met **AH Recepten** (je bewaarde recepten, [#19](https://github.com/helmerzNL/MealieShopper/pull/19) t/m [#21](https://github.com/helmerzNL/MealieShopper/pull/21)) en **AH Lijstjes** (favoriete lijstjes, [#15](https://github.com/helmerzNL/MealieShopper/pull/15) t/m [#17](https://github.com/helmerzNL/MealieShopper/pull/17)).
- Lijstjes en recepten worden lokaal bewaard en blijven zichtbaar als AH niet meewerkt. Zoeken in lijstjes kijkt ook naar de producten erin.
- Responsive kaarten voor telefoon en groot scherm, met een cache voor recepten en plaatjes ([#30](https://github.com/helmerzNL/MealieShopper/pull/30)).

**Koppelen en beveiligen**
- Albert Heijn koppelen via de echte `login.ah.nl`, zodat je passkey en de AH-inlogknop werken ([#2](https://github.com/helmerzNL/MealieShopper/pull/2), [#3](https://github.com/helmerzNL/MealieShopper/pull/3), [#14](https://github.com/helmerzNL/MealieShopper/pull/14)).
- Mealie koppelen in de app onder Beheer > Mealie ([#27](https://github.com/helmerzNL/MealieShopper/pull/27)).
- Onboarding bij de eerste start: inloggen met een passkey (met domeinnaam en HTTPS) of een wachtwoord met optionele tweestapsverificatie, daarna Mealie en AH koppelen ([#31](https://github.com/helmerzNL/MealieShopper/pull/31), [#32](https://github.com/helmerzNL/MealieShopper/pull/32)).
- Sessies worden ondertekend met een willekeurige, bewaarde sleutel ([#32](https://github.com/helmerzNL/MealieShopper/pull/32)).

**Interface**
- Nederlands en Engels, te wisselen met de vlaggetjes (ook in de onboarding) ([#32](https://github.com/helmerzNL/MealieShopper/pull/32)).
- Tabblad **Over** met versie en commit, de gebruikte componenten en integraties, en een koffie-verzoek ([#32](https://github.com/helmerzNL/MealieShopper/pull/32)).
- Huisstijl en installeerbare app (PWA) ([#26](https://github.com/helmerzNL/MealieShopper/pull/26)), Material Design Icons in het hoofdmenu ([#29](https://github.com/helmerzNL/MealieShopper/pull/29)) en een zwevende tabbalk op de telefoon ([#28](https://github.com/helmerzNL/MealieShopper/pull/28)).

**Uitrol**
- Docker-image via GitHub Actions naar `ghcr.io/helmerznl/mealieshopper`, voorbeeldbestanden voor Docker Compose en Unraid.

### Fixes en opruiming sinds het begin

- AH-requests volgen het API-profiel van `appie-go` ([#1](https://github.com/helmerzNL/MealieShopper/pull/1)); AH-lijstjes en -recepten via GraphQL omdat de REST-routes 404 geven ([#16](https://github.com/helmerzNL/MealieShopper/pull/16), [#20](https://github.com/helmerzNL/MealieShopper/pull/20), [#28](https://github.com/helmerzNL/MealieShopper/pull/28)).
- Fouten van externe diensten komen als 424 terug, zodat proxies de echte foutmelding laten staan ([#18](https://github.com/helmerzNL/MealieShopper/pull/18)).
- AH-aantallen blijven binnen 1 tot 99, want AH weigert alles daarbuiten.
- Een AH-token of -code wordt aan de serverkant herkend ([#3](https://github.com/helmerzNL/MealieShopper/pull/3)).
- **Verwijderd:** de eerdere Jumbo-koppeling ([#4](https://github.com/helmerzNL/MealieShopper/pull/4)), de AH-website-login met een headless browser en daarmee Selenium en Chromium uit de image ([#9](https://github.com/helmerzNL/MealieShopper/pull/9) t/m [#13](https://github.com/helmerzNL/MealieShopper/pull/13), opgeruimd in [#23](https://github.com/helmerzNL/MealieShopper/pull/23)).

### Issues

Er zijn tot nu toe geen GitHub-issues geopend of openstaand. Alle wijzigingen liepen via pull requests [#1](https://github.com/helmerzNL/MealieShopper/pull/1) t/m [#34](https://github.com/helmerzNL/MealieShopper/pull/34).

### Goed om te weten

- De AH-koppeling gebruikt de **niet-officiële** app-API van AH, die zonder bericht kan veranderen.
- Zonder `secret.key` (of `MEALIESHOPPER_AUTH_SECRET`) zijn opgeslagen koppelingen en sessies onleesbaar. Neem de hele data-map mee in je back-ups.

## v1.0.0

De eerste genummerde versie: het versienummer en het tabblad Over kwamen met [#32](https://github.com/helmerzNL/MealieShopper/pull/32). Alles wat daarvoor is gebouwd staat onder v1.2.0 hierboven, bij "Alle functies vanaf v1.0.0". Voor deze versie is geen aparte GitHub release gemaakt.

## Releases

Bij **elke** nieuwe versie hoort een nieuwe GitHub release:

1. Verhoog `VERSION` (alleen `X.Y.Z`).
2. Voeg bovenaan dit bestand een kopje `## vX.Y.Z` toe met wat er nieuw is: functies, fixes, verwijderde dingen en de pull requests en issues erbij.
3. Merge naar `main`. De workflow `.github/workflows/release.yml` maakt dan de tag `vX.Y.Z` en de GitHub release, met de tekst onder dat kopje. Bestaat de release al, dan gebeurt er niets.

`tests/test_release.py` laat de pull request falen als `VERSION` geen eigen kopje in dit bestand heeft.
