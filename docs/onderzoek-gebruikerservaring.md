# Onderzoek: de gebruikerservaring van MealieShopper

Doel: vaststellen waar de gebruikerservaring nog beter kan, nu het uiterlijk na vier ontwerprondes (4.6.0 tot en met 4.9.0:
typeschaal, radius, breekpunten, segmented control, ingeklapte uitleg) op orde is. Onderzocht op 9 november 2026 op versie
4.10.2: `templates/index.html`, alle 37 scripts in `static/`, `static/styles.css`, de teksten (NL en EN, sleutels gelijk), de
routes die de schermen voeden, `docs/huisstijl.md`, de schermafbeeldingen in `docs/screenshots/nl/` en de richtlijnen
`web-app-design.md` en `navigation-bar.md` van Flux76HQ/App-Guidance. Elke bevinding is in de code nagelezen; de zwaarste (alle
met een H, en de fouten) twee keer. De app is voor het onderzoek niet op een toestel gemeten: het is een analyse van code en
schermafbeeldingen.

De winst zit in drie dingen: (1) de hoofdflow van weekmenu naar mandje kost elke week hetzelfde handwerk, omdat de app niets
onthoudt, (2) terugkoppeling bij wachten, fouten en een verlopen sessie, en (3) losse eindjes in navigatie en eerste start.

## Uitkomst: vier rondes

| Ronde | Naam | Bevindingen | Stand |
|---|---|---|---|
| 1 | Vastlopers en fouten | 1.1, 1.10, 1.11, 2.1, 2.2, 2.6, 2.8, 2.9, 2.13, 2.14, 3.1, 3.3, 6.1, 6.3, 6.10, *Annuleren* met een vraag (uit 1.4) en het begin van 2.3 (meldingen worden voorgelezen) | gebouwd in 4.10.4 |
| 2 | De app onthoudt je week | 1.2 tot en met 1.9 | gebouwd in 4.11.0 |
| 3 | Terugkoppeling en bediening in één stijl | 2.3, 2.4, 2.5, 2.7, 2.10 tot 2.12, 2.15, 2.17, 2.18, 6.2, 6.4, 6.6 tot 6.9, 6.14 | gebouwd in 4.12.0 (zonder stoppen van de AI-zoekronde uit 2.12 en zonder € 0 uit 6.14; zie de besluiten van 10 november) |
| 4 | Wegwijs | 2.16, 3.2, 3.5 tot 3.8, 4.1 tot 4.4, 5.1 tot 5.3, 6.5, 6.11 tot 6.13 | gebouwd in 4.13.0 (zonder de naam van de tab Recepten uit 3.5, zonder service worker uit 6.13 en zonder zoomen in de app uit 6.5; zie de besluiten van 10 november) |

Besluiten van de eigenaar (9 november 2026):

- **Sessie:** de eigenaar koos op 9 november 30 dagen met verlenging bij gebruik. Diezelfde dag kwam [#303](https://github.com/helmerzNL/MealieShopper/pull/303) op `beta` (4.10.3): 90 dagen, instelbaar met `MEALIESHOPPER_SESSION_DAYS`, verlenging bij gebruik en uitloggen dat de sessie intrekt. Dat lost de duur uit 2.1 op; ronde 1 voegt alleen de afhandeling van een verlopen sessie in de frontend toe.
- **Mandje op de telefoon: niets veranderen** (3.4 vervalt; het blijft in het menu *Meer*, zoals `docs/huisstijl.md` beschrijft).
- **Dagen die voorbij zijn tellen in de lopende week standaard niet mee** (bij 1.5, gebouwd in 4.11.0). Dat is nieuw gedrag; de eigenaar bevestigde het op 10 november. Een tik op de dag neemt hem toch mee.
- **Importeren via URL blijft uit het menu** (een besluit van eerder, bewaakt door `tests/test_tabbar.py`). Bevinding 3.3 is
  daarom anders opgelost: een link die je in het zoekveld van *Recepten zoeken* plakt, wordt geïmporteerd.

Besluiten van de eigenaar (10 november 2026), over wat de vier rondes bewust lieten liggen:

- **De tab *Recepten* houdt zijn naam** (3.5 vervalt). *Recepten* is één begrip: de hoofdtab zijn je eigen recepten in
  Mealie, en per winkel staan je favoriete recepten van die winkel, die je naar Mealie importeert. De bevinding noemde dat
  vier betekenissen; het is dezelfde betekenis op vier plekken.
- **Zoomen in de geïnstalleerde app blijft uit** (uit 6.5). Een bewuste keuze, zoals `docs/huisstijl.md` beschrijft.
- **De AI-zoekronde krijgt geen stopknop** (uit 2.12). De knop *Recepten zoeken met AI* staat op één plek (Aanbiedingen,
  Recepten), alleen voor de beheerder met een AI-sleutel, en start alleen na een vraag met de geschatte tokens. Een ronde is
  standaard hooguit 120 recepten in drie aanvragen (`DEFAULT_MAX_RECIPES` en `GROUP_SIZE` in `mealieshopper/ai_match.py`), en
  er draait niets automatisch. Stoppen zou hooguit twee aanvragen besparen; dat is een extra route op de server niet waard.
- **Een prijs van € 0 blijft leeg, voor nu** (uit 6.14). Een lege prijs betekent op veel plekken "onbekend"; `formatPrice`
  blijft zoals hij is.
- **Een service worker staat nog open** (uit 6.13). De app zonder verbinding kunnen gebruiken is handig; de eigenaar denkt
  erover na. Tot dat besluit geldt `docs/huisstijl.md`: er is geen offlinefunctie.

Prioriteit in de tabellen: **H** raakt elke week of is een fout, **M** merkbaar, **L** afwerking. De regelnummers zijn die van 4.10.2.

## Wat goed is en zo moet blijven

- Het mandje gaat nooit vol zonder controlelijst (`static/app.js:1977`); opnieuw zoeken vervangt alleen die ene regel (`:1880`).
- De hint *Per kg goedkoper … Kies*, de foto per gekozen product, en fouten die in beeld scrollen (`:1945`).
- De weekcache met de twee weken erna alvast opgehaald; de kookstand met wake lock (`static/recipe_view.js:453`).
- De installatiehulp hervat een half afgemaakte Picnic-login en zet de focus op de titel van elke stap (`static/onboarding.js:90`).
- De fabrieksreset: zin overtypen, wachtwoord of passkey, en dan pas de laatste vraag (`static/system.js:107`).
- Koppelstatus met tekst en icoon, nooit kleur alleen; optimistisch opslaan dat terugdraait bij een fout (`static/appearance.js:37`).

## Bevindingen

### 1. Van weekmenu naar mandje (de kern van de app)

| # | P | Wat de gebruiker merkt | Bewijs |
|---|---|---|---|
| 1.1 | H | **Fout:** wissel je van winkel terwijl de controlelijst open staat, dan gaan de product-id's van de oude winkel naar de nieuwe. | De winkelkeuze zet `state.store` maar laat `#cart-result` staan (`static/app.js:1512-1523`); bevestigen post naar `currentStore().api` (`:1704`, `:1890`) |
| 1.2 | H | Een gecorrigeerd product wordt nooit onthouden: dezelfde verkeerde keuze herstel je elke week. | `cart_preview` zoekt elke keer opnieuw (`mealieshopper/app.py:1047-1108`); `product_links` is alleen voor bonregels |
| 1.3 | H | Zout, olie en bloem gaan elke week mee de zoekronde in; er is geen "al in huis". | De lijst is een platte `<ul>` (`static/app.js:894-900`); er bestaat al een `PANTRY`-lijst, maar alleen voor aanbiedingen (`mealieshopper/offers.py:869`) |
| 1.4 | H | De controlelijst raakt makkelijk kwijt: herladen, een week bladeren, of Annuleren (zonder vraag) wist twintig keuzes. | `loadMealPlan` leegt `#cart-result` (`static/app.js:1008`); `#cancel-cart` (`:1918`) |
| 1.5 | M | Altijd de hele week, ook dagen die al voorbij zijn; je kunt geen maaltijd of dag uitsluiten. | `aggregateIngredients(state.entries)` (`static/app.js:1952`) |
| 1.6 | M | Lang wachten zonder voortgang: alleen de knoptekst *Producten zoeken...*, geen "12 van 25". | `static/app.js:1954-1957`; de server doet zes regels tegelijk (`mealieshopper/app.py:1107`) |
| 1.7 | M | Het product kies je in een systeemkeuzelijst met een lange regel tekst; op een telefoon wordt die afgekapt. Het aantal is een kaal getalveld, terwijl de lijstjes een `−`/`+`-knop hebben. | `<select class="review-product">` (`static/app.js:1783`), `review-qty` (`:1793`), `renderQuantityControl` bestaat al (`:2664`) |
| 1.8 | M | Geen totaalbedrag in de controlelijst, en twijfelregels staan tussen de rest. | `renderCartReview` (`static/app.js:1798-1812`) |
| 1.9 | M | De knop *Voeg toe aan mandje* staat onder zeven dagkaarten en de hele ingrediëntenlijst. | `static/app.js:901-907`; `web-app-design.md` zet de hoofdknop in de paginakop |
| 1.10 | M | Zonder AH-koppeling loopt de flow pas bij de laatste klik vast, na het nalopen van de hele lijst. | AH is altijd een keuze (`static/app.js:1480`); de fout komt van `ah.get_user_token` (`mealieshopper/ah.py:461`) als 424 |
| 1.11 | L | Na toevoegen bij AH ontbreekt *Mandje bekijken*; de andere winkels hebben hem wel. | `static/app.js:1694` |
| 1.12 | L | Vanuit Recepten zoeken plant de dialoog standaard vandaag in, niet de week die je bekeek. | `static/app.js:1213` |

### 2. Terugkoppeling, fouten en sessie

| # | P | Wat de gebruiker merkt | Bewijs |
|---|---|---|---|
| 2.1 | H | Na 24 uur verloopt de sessie altijd (geen verlenging). Staat de app dan open, dan verschijnt bij elke actie het woord *Unauthorized*, zonder inlogscherm. In de geïnstalleerde app gebeurt dat dagelijks. | Vast `exp` en cookie van 24 uur, alleen gezet bij inloggen (`mealieshopper/auth.py:27`, `:510`, `:645`); `jsonFetch` kent geen 401 (`static/app.js:140-160`) |
| 2.2 | M | Geen netwerk: de ruwe browsertekst (*Failed to fetch*, *Load failed*) komt in beeld. | `jsonFetch` vangt een afgewezen `fetch` niet (`static/app.js:141`) |
| 2.3 | M | Meldingen staan in een vak per scherm, soms buiten beeld, en worden niet voorgelezen. Er is geen toast. | `message()` (`static/app.js:162`); `#planner-message`, `#cart-result`, `#basket-message` hebben geen `aria-live` |
| 2.4 | M | Laden is overal een regel tekst; de pagina verspringt als de inhoud komt. Geen skeletons. | `basket.loading` (`static/basket.js:61`), `static/app.js:1018-1020` |
| 2.5 | M | Elf vragen via `window.confirm`: buiten de huisstijl, zonder ongedaan maken. | o.a. `static/app.js:1310`, `:2760`, `static/picnic.js:619`, `static/users.js:156` |
| 2.6 | M | Ontkoppelen van Mealie, Jumbo of Picnic gaat in één klik, terwijl opnieuw koppelen bij AH en Jumbo een computer met console vraagt. | `static/app.js:2325`, `static/jumbo.js:86`, `static/picnic.js:108` |
| 2.7 | M | Opslaan werkt per blok anders: Voorkeuren slaat stil op (geen "Opgeslagen"), Profiel heeft een knop met bevestiging. | `static/appearance.js:39-66`, `static/profile.js:150-168` |
| 2.8 | L | Een foutmelding bij importeren via URL gaat zonder escapen de pagina in. | `static/app.js:2073`, `:3250` |
| 2.9 | M | *Laden...* en *Wachten op passkey* staan in een groene succesmelding: `message()` kent geen neutrale soort, en kan geen knop of link dragen (dus teksten als *ga naar Instellingen > Koppelen > Jumbo*). | `static/app.js:1020`, `:335`; soorten in `static/styles.css:1025-1044` |
| 2.10 | M | De bestellingenlijst is leeg of toont de vorige winkel terwijl drie verzoeken na elkaar lopen. | `loadOrders` (`static/orders.js:286-311`) |
| 2.11 | M | *Alles ophalen* bij aanbiedingen is één lang verzoek voor zes winkels, zonder voortgang per winkel; een proxy-timeout wordt een 504-melding. | `static/offers.js:1725-1740`, `mealieshopper/app.py:913` |
| 2.12 | M | De AI-receptzoeker raakt een lopende taak kwijt na één netwerkhapering en kan niet stoppen; back-up en bulkimport proberen wel opnieuw. | `static/ai_review.js:163-187` tegenover `static/backup.js:288` |
| 2.13 | M | Uitloggen kan stil mislukken (geen `try`); op een gedeeld toestel denk je dat je bent uitgelogd. | `static/app.js:3336-3339` |
| 2.14 | M | Aanbiedingen wegfilteren tot nul geeft een leeg rooster, zonder tekst of *Filters wissen* ter plekke; ook met het zoekveld. | `renderOfferGrid` (`static/offers.js:963-975`) |
| 2.15 | M | Zeven velden hebben alleen een placeholder als label; de wachtwoordregels staan alleen in de installatiehulp, niet bij uitnodigen, wachtwoord wijzigen of het back-upwachtwoord. | `templates/index.html:609`, `:943`, `:1444`, `:1675`, `:1720`, `:1875`, `:1913`; `:174-186`, `:1411-1428` |
| 2.16 | M | Albert Heijn kun je niet ontkoppelen: geen knop en geen route, de andere winkels wel. | Geen `/api/ah/auth/logout` in `mealieshopper/app.py` |
| 2.17 | L | Sommige lege toestanden noemen geen volgende stap (*Geen lijstjes gevonden.*, *Er staan nog geen recepten in Mealie.*). | `static/i18n/nl.js:1061`, `:984`, `:1108` |
| 2.18 | L | Teksten: Crisp ontbreekt in vijf opsommingen van winkels; *Verversen* naast *Vernieuwen*; *mandje*, *winkelmandje* en *boodschappenlijst* voor hetzelfde; `...` naast `…`; *koppelen* betekent drie dingen; `err.proxy_hint` over container-logs krijgt elke gebruiker; één vaste Nederlandse tekst (*Ingelogd.*) en vijftien Engelse serverfouten. | `static/i18n/nl.js:1127`, `:1569`, `:1592`, `:912-915`; `static/app.js:408`; `mealieshopper/auth.py:699-887` |

Daarnaast, al goed: back-up en terugzetten (fasen, voortgang, hervatten), de bulkimport met *Stop*, en verouderde antwoorden
die worden genegeerd (`mealPlanRequest`, `offersLoadId`).

### 3. Navigatie

| # | P | Wat de gebruiker merkt | Bewijs |
|---|---|---|---|
| 3.1 | M | Elk scherm heet *MealieShopper*: tabbladen, geschiedenis en schermlezers zien geen verschil. | Geen `document.title` in `static/`; `templates/index.html:89` |
| 3.2 | M | Terug springt altijd naar boven: je plek in een lange lijst is weg. Na een schermwissel blijft de focus op de aangeklikte link. | `history.scrollRestoration = "manual"` (`static/app.js:3317`), `showPanel` (`:2920-2942`) |
| 3.3 | M | *Recept importeren via URL* bestaat, maar nergens is een ingang: alleen via `/import` in de adresbalk (het is in een eerdere versie bewust uit het menu gehaald). | Paneel `#url` (`templates/index.html:938`), route in `static/router.js:11`, geen link ernaartoe |
| 3.4 | M | Op een telefoon staat het Mandje achter *Meer*, Maaltijdboxen niet. Bewuste keuze uit de huisstijl, maar het is de laatste stap van de hoofdflow, en er is geen teller. | `static/styles.css:6638-6641`, `docs/huisstijl.md:153` |
| 3.5 | M | *Recepten* betekent vier dingen (hoofdtab, bij Aanbiedingen, per winkel), en het weekmenu, het hart van de app, heet in het menu niet zo. **Besluit 10 november: blijft zo;** het is één begrip op meer plekken (zie de besluiten bovenaan). | `templates/index.html:530`, `:577`, `:788`, `:956` |
| 3.6 | M | De winkelkeuze wordt op vijf plekken apart bewaard; Jumbo kiezen in het weekmenu opent daarna niet het Jumbo-mandje. Ook drie verschillende bedieningen (uitklapmenu, `<select>`, knoppen). | `static/app.js:1515`, `static/basket.js:31`, `static/app.js:1460` |
| 3.7 | L | De bekeken week staat niet in het adres: herladen op volgende week geeft deze week. | `static/router.js:23`, `state.weekOffset` |
| 3.8 | L | Subtabs scrollen op een telefoon horizontaal zonder aanwijzing dat er meer is. | `static/styles.css:480-491` |

### 4. Eerste start

| # | P | Wat de gebruiker merkt | Bewijs |
|---|---|---|---|
| 4.1 | M | Zes vaste stappen: AH, Jumbo en Picnic moet je één voor één overslaan. Er is geen vraag "welke winkels gebruik je?". | `OB_STEPS` (`static/onboarding.js:4`) |
| 4.2 | M | Crisp ontbreekt in de installatiehulp en in de welkomsttekst. | `static/onboarding.js:4`, `static/i18n/nl.js:1127` |
| 4.3 | M | AH en Jumbo koppelen vraagt een computer met console; wie op een telefoon begint, loopt twee keer vast. | `static/i18n/nl.js:1173` |
| 4.4 | L | *Klaar* noemt geen eerste taak; *Mealie koppelen* vanaf het weekmenu opent Instellingen zonder naar het Mealie-blok te scrollen. | `static/onboarding.js:389-416`, `static/app.js:3276-3280` |

### 5. Aanbiedingen en instellingen

| # | P | Wat de gebruiker merkt | Bewijs |
|---|---|---|---|
| 5.1 | M | Een aanbieding kun je alleen bekijken: er is geen knop om hem in je mandje of op je lijstje te zetten. | Kaartvoet met alleen vergelijken en geschiedenis (`static/offers.js:615`); de routes `/api/<winkel>/cart` nemen al een `productId` aan |
| 5.2 | M | Persoonlijke instellingen staan verdeeld over Profiel en Instellingen, Voorkeuren; *Liever biologisch* staat alleen bij het weekmenu. | `templates/index.html:1369`, `:1511`, `static/app.js:903` |
| 5.3 | M | Koppelen begint met hulpmiddelen; Mealie en de winkels, waar het om gaat, staan onderaan, met de verkenners voor ontwikkelaars ertussen. | `templates/index.html:1514-1860` |

### 6. Mobiel en toegankelijkheid

| # | P | Wat de gebruiker merkt | Bewijs |
|---|---|---|---|
| 6.1 | H | **Fout:** in de onderste menubalk hebben de tabs voor een schermlezer geen naam: het lange label is verborgen met `display: none`, het korte heeft `aria-hidden`. | `templates/index.html:530-559`, `static/styles.css:4768`; `tests/test_tabbar.py:75` gaat uit van het tegendeel |
| 6.2 | H | Veel tikvlakken zijn kleiner dan 44 px, juist op schermen die je staand gebruikt: `−`/`+` 36 px, potlood en `+` in het weekmenu 36 px, verwijderen bij aankopen 30 px, iconen in de compacte aanbiedingenlijst 32 px. Er is geen regel voor `pointer: coarse`. | `static/styles.css:1806`, `:3009`, `:3435`, `:5786`, `:5992`, `:6119`, `:8500` |
| 6.3 | M | Vensters zijn begrensd met `100vh`, nergens `dvh`: op iOS Safari kan de onderkant, met de enige knop *Sluiten*, achter de werkbalk vallen. | `static/styles.css:1593`, `:1689`, `:2370`, `:7903`, `:7967` |
| 6.4 | M | Ongeveer 50 van de 66 `:hover`-regels gelden ook op een aanraakscherm: een knop blijft na een tik groen. | o.a. `static/styles.css:857`, `:1820`, `:7411` |
| 6.5 | M | In de geïnstalleerde app kun je niet zoomen (bewuste keuze), ook niet op een kassabon of productfoto, en er is geen *vernieuwen* op elk scherm nu omlaag slepen uit staat. | `templates/index.html:32`, `static/styles.css:4665-4668` |
| 6.6 | M | De installatiehulp heet een modaal venster, maar Tab loopt de app erachter in. | `templates/index.html:471`, `static/onboarding.js:38-50` |
| 6.7 | M | Elke balk en stip in de prijsgrafiek is een Tab-stop die een schermlezer niet kan lezen (`role="img"` op de grafiek). | `static/offers_history.js:233-307` |
| 6.8 | M | Uitleg die alleen in een `title` staat (de kortingsbadge, het oordeel) is op een aanraakscherm onbereikbaar. | `static/offers.js:562`, `:602`, `:650` |
| 6.9 | M | Randen van invoervelden en kaarten halen 1,4 : 1; de richtlijn vraagt 3 : 1 voor randen van bediening. | `--border` in `static/styles.css:42`, `:122`, `:168`; `web-app-design.md` regel 97 |
| 6.10 | M | Zoeken in aanbiedingen rekent bij elke toetsaanslag vier keer over alle aanbiedingen, zonder vertraging; de andere zoekvelden wachten 250 ms. | `static/offers.js:1460-1463`, `:1072-1097` |
| 6.11 | M | Zoektekst, categorie en *Meer* bij aanbiedingen, en de filters bij aankopen, zijn weg na herladen of Terug. | `static/offers.js:15-19`, `static/orders.js:394-405` |
| 6.12 | M | Aankopen tekent de hele geschiedenis in één keer, en opent ook op een telefoon in de tabelweergave. | `static/purchase_views.js:230`, `:438-480` |
| 6.13 | L | Geen service worker: de geïnstalleerde app opent zonder verbinding op de foutpagina van de browser. Het manifest heeft geen `shortcuts`. | `static/manifest.webmanifest`; `docs/huisstijl.md:134` |
| 6.14 | L | `formatPrice` geeft niets terug bij € 0; gefilterde aantallen worden niet voorgelezen; er is geen link *naar de inhoud*. | `static/app.js:1348`, `templates/index.html:804` |

Al goed: overal een echte `<dialog>`, een zichtbare focusring, invoervelden van 16 px, `lang` volgt de taal, afbeeldingen
laden lui met vaste maten, en de aanbiedingenlijst laadt in delen.

## Rondes 3 en 4 in het kort

Ronde 3 maakt de terugkoppeling en bediening gelijk: een toast en een eigen bevestigingsdialoog in plaats van de vragen via
`window.confirm`, laadtoestanden met vaste ruimte, tikvlakken van 44 px, `:hover` alleen met een muis, randcontrast, labels en
wachtwoordregels, en de teksten uit 2.18. Ronde 4 gaat over wegwijs worden: de installatiehulp met *welke winkels gebruik je?*
en Crisp, de volgorde van Koppelen, *Liever biologisch* bij Voorkeuren, terug naar je plek in een lijst, de week in het adres,
één winkelkeuze, een aanbieding direct in je mandje, en een ontkoppelknop voor Albert Heijn.

### Wat bewust niet is gedaan

De eigenaar besliste op 10 november 2026 over deze vijf punten; de redenen staan bij de besluiten bovenaan.

| Bevinding | Wat er niet is gedaan | Besluit |
|---|---|---|
| 3.5 | De tab *Recepten* een andere naam geven. Dat raakt de huisstijl, alle schermafbeeldingen en `tests/test_tabbar.py`. | Blijft zo: *Recepten* is één begrip. |
| 6.5 | Zoomen in de geïnstalleerde app. Wel gedaan uit 6.5: *Vernieuwen* in het menu *Meer*, nu omlaag slepen in de app niet ververst. | Blijft uit, een bewuste keuze. |
| 2.12 | De AI-zoekronde stoppen; dat vraagt een route op de server. Wel gedaan uit 2.12: de pagina blijft de ronde volgen na een netwerkhapering. | Niet bouwen: een ronde is klein en start alleen na een vraag. |
| 6.14 | `formatPrice` bij € 0 een prijs laten tonen. | Blijft zo, voor nu. |
| 6.13 | Een service worker. `docs/huisstijl.md` zegt dat er geen offlinefunctie is, en een service worker brengt een eigen cache mee, naast de versiecontrole die er al is. Wel gedaan uit 6.13: de snelkoppelingen in het manifest. | Open: de eigenaar denkt erover na. |

Albert Heijn ontkoppelen (2.16) en een aanbieding in het mandje (5.1) zijn getest met nagebootste antwoorden, niet tegen de
echte routes van de winkels.
