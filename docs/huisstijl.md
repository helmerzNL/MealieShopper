# MealieShopper huisstijl

Van recept naar geregeld.

## Richting

Een praktisch, vriendelijk hulpmiddel tussen recepten, weekmenu en boodschappen.
Het beeldmerk is een boodschappentasje met een M in de bovenrand. Het favicon
vereenvoudigt deze M voor kleine formaten. Gebruik het beeldmerk onafhankelijk
van de logo's van Mealie en Albert Heijn.

## Ontwerpkeuzes

| Keuze | Basis | Toepassing |
| --- | --- | --- |
| M-tasje | Goedgekeurd merkconcept | Logo, appicoon en vereenvoudigd favicon |
| Basilicumgroen | Goedgekeurd palet | Primaire acties, merk en actieve navigatie |
| Neutrale oppervlakken | Bestaande app en kleurregels | Rust bij het scannen van recepten en lijsten |
| Manrope / Inter | Goedgekeurde typografie | Koppen / bediening en lopende tekst |
| Material Design Icons | Icoonrichtlijnen | Eén iconenset in de interface, zonder gemengde sets |

## Ontwerptokens

De tokens staan als CSS-variabelen in `static/styles.css` (`:root`).

| Rol | Waarde | Token |
| --- | --- | --- |
| Merk / primaire actie | `#176B45` | `--primary` |
| Hover / sterk accent | `#105235` | `--primary-strong` |
| Selectievlak | `#EAF5EE` | `--selection` |
| Decoratief citroenaccent | `#F2CF45` | `--lemon` |
| Achtergrond | `#F5F7F6` | `--bg` |
| Oppervlak | `#FFFFFF` | `--surface` |
| Tekst | `#18251E` | `--text` |
| Ondersteunende tekst | `#59665E` | `--muted` |
| Rand | `#D6DFD9` | `--border` |
| Radius: klein, bediening, kaart, groot, pil | 4 / 8 / 12 / 16 / 999 px | `--radius-sm`, `--radius-control`, `--radius-card`, `--radius-lg`, `--radius-pill` |

Citroen is decoratief; gebruik het niet als tekst op wit (contrast 1,5 : 1). Fouten
blijven rood (`--danger`) en krijgen een tekstuele uitleg. Focusringen zijn zichtbaar
groen (3 px, `:focus-visible`). Bij twijfel in een keuze (bijvoorbeeld in "Controleer je
mandje") gebruiken we een amberkleurig waarschuwingsvlak met uitleg, geen citroen.

Aanvullend, afgeleid van het palet: `--surface-muted` (`#EEF3F0`) voor plaatsvervangers en
codevlakken, en melding-tinten voor succes, fout en waarschuwing. `tests/test_brand.py`
bewaakt dat de tokens kloppen en dat alle tekst-/achtergrondparen WCAG AA halen
(4,5 : 1 voor tekst, 3 : 1 voor het beeldmerk).

## Diepte, beweging en componenten

Het palet verandert niet; de afwerking wel. Aanvullende tokens in `:root` (en in het donkere thema):

| Rol | Token |
| --- | --- |
| Schaduwniveaus: rand/veld, kaart, hover/menu, venster | `--shadow-xs`, `--shadow-sm`, `--shadow-md`, `--shadow-lg` |
| Lichtrand en schaduw van een primaire knop | `--btn-shadow`, `--btn-shadow-hover` |
| Focushalo naast de ring (velden) | `--ring` |
| Sterkere rand bij hover | `--border-strong` |
| Kleur van placeholdertekst (4,8 : 1 op wit) | `--placeholder` |
| Mat glas van de balk bovenaan, gloed bovenaan de pagina | `--topbar-glass`, `--page-glow` |
| Eén vloeiende curve voor alle overgangen | `--ease` |

Regels:

- **Dieptelagen.** Pagina (`--bg`), kaart (`--shadow-sm`), oplichtende kaart of menu (`--shadow-md`), venster of inlogkaart (`--shadow-lg`). Gebruik niet meer dan twee niveaus naast elkaar.
- **Navigatie.** Vanaf 860 px staat het hoofdmenu links (het breekpunt van App-Guidance), met alleen onder het open onderdeel zijn subtabs; het markeert het actieve onderdeel met `--selection` en een groen streepje. Het tweede niveau (`.subtabs`) is een segmentbediening met een oplichtend actief segment; op een telefoon is dat één regel die horizontaal scrolt, en het gekozen segment scrolt in beeld. Loopt de regel door, dan vervaagt de rand aan de kant waar nog segmenten buiten beeld staan (`has-more-start`, `has-more-end`, sinds 4.13.0). Na een schermwissel en na Terug staat de focus op de inhoud (`#main`, zonder focusrand: het is geen bediening), en Terug zet je op de plek in de lijst waar je was. *Vernieuwen* staat alleen in het menu van de geïnstalleerde app (`.user-menu__item--standalone`). Een scherm zonder eigen kaarttitel heeft een `.page-title`. Een volledig groen vlak is voorbehouden aan primaire acties, het actieve filter (`.offer-chip`) en de pil van de huidige dag.
- **Knoppen en velden.** Primair heeft een lichte bovenrand en geeft 1 px indruk bij `:active`; secundair heeft een rand en `--shadow-xs`. Velden krijgen bij focus een rand van 2 px in `--primary` met een halo van `--ring`.
- **Meldingen.** Een gekleurde balk links (4 px) naast de bestaande kleur en tekst; kleur alleen is nooit de enige aanwijzing. Vier soorten: fout, waarschuwing, succes en neutraal (`info`, voor *Laden...* en mededelingen). `message()` geeft de melding een rol (`alert` of `status`), zodat een schermlezer hem voorleest.
- **Vragen en korte meldingen** (sinds 4.12.0). Een vraag staat in een eigen venster (`confirmDialog` in `app.js`, `#confirm-dialog`), nooit in `window.confirm`: de knop zegt wat hij doet (*Verwijderen*, *Ontkoppelen*), bij iets dat niet terug kan is hij rood en staat de focus op *Annuleren*. Wat geen eigen vak heeft, is een toast onderaan (`toast()`): een succes (*Opgeslagen*) verdwijnt na vier seconden, een fout blijft tot je hem sluit.
- **Laden** (sinds 4.12.0): een draaiend icoon met tekst in een vak van 160 px (`loadingHtml`, `.loading-box`), en in het weekmenu lege dagkaarten, zodat de pagina niet verspringt als de inhoud komt.
- **Prijzen en acties in een lijst.** `.product-card__price` is vet in Manrope; *Bonus* is een pil (`.product-card .bonus`). Op een aanbiedingskaart is de actie van de winkel ("2 voor 2,99") een vette regel tekst in plaats van een rode pil (`.offer-card .bonus`).
- **Kortingsbadge** (`offerBadge`, sinds 4.8.0): naar het oordeel, niet naar vijf kleurtrappen. Echt voordelig (`--good`) is vol groen uit `--success-text`, een klein voordeel (`--small`) neutraal, *Geen korting* (`--none`) gedempt met het woord in plaats van "0%", en een claim van de winkel (`--store`) heeft een stippelrand. Geen rood: dat is voor fouten. De randen zijn een `border`, zodat ze ook in een regel van tekst (`--inline`) blijven.
- **Inzichten** onder de prijs op een aanbiedingskaart (`.offer-insights`): het prijslabel en de chips van het oordeel in één rij, in twee tonen: goed nieuws in de groene statuskleur, de rest neutraal. *Vergelijk verpakkingen* en *Prijsgeschiedenis* zijn twee randloze icoonknoppen rechtsonder.
- **Chips met een aantal**: de naam en daarachter gedempt het getal (`.offer-chip__count`, "Vlees 412"). Op een telefoon staat een rij categorieën op één regel die opzij scrolt (`.offer-cats--scroll`). Sorteren is een keuzelijst met een zichtbaar label.
- **Receptkaart** (`recipeCardTitle` en `recipeCardActions` in `app.js`): de titel is een knop die met zijn `::after` de hele kaart beslaat, dus een tik op de kaart opent het recept; de focusring staat om de kaart. Onderaan alleen *Inplannen*, secundair, met de naam van het recept verborgen voor een schermlezer. In de selectiemodus van In bulk importeren selecteert een tik de kaart.
- **Kerncijfers** (`.stats`, naar App-Guidance): een klein label in hoofdletters boven een vet, tabulair getal, zoals bovenaan het mandje. Uitleg die je niet steeds nodig hebt staat in een `<details class="help">` ("Hoe werkt dit?").
- **Weeknavigatie**: overal dezelfde pijlen (MDI-chevrons in een `.icon-button`) en `.week-label`; *Deze week* staat er alleen als je een andere week bekijkt.
- **Segmentbediening** (`.segmented` met `.segmented__item`, sinds 4.9.0): voor een keuze binnen een scherm, zoals de weergave van Aanbiedingen, de weergaven van aankopen en bestellingen en *Recept* of *Notitie* in het plan-venster. Hetzelfde ingedeukte spoor als `.subtabs`, met segmenten van 44 px; actief via `aria-pressed`, `is-active` of een gekozen radio. `.subtabs` is navigatie (links, met `aria-current="page"` op het open tabblad), `.segmented` een keuze; meng ze niet.
- **Uitleg** (sinds 4.9.0): een scherm of blok begint met één regel die zegt wat het is. De rest staat in een `<details class="help">` met de samenvatting *Hoe werkt dit?* (`common.help`). Waarschuwingen, fouten, wat destructief is en statusregels staan nooit in zo'n blok.
- **Breekpunten**: drie, 640 px (telefoon), 860 px (menu links) en 1200 px (breed), met 641 en 1199 als tegenhanger, in CSS en in JavaScript. Hangt iets af van de ruimte van een vak in plaats van het scherm (de compacte lijst van Aanbiedingen, de controlelijst, een dagkaart), dan is het een container query.
- **Beweging.** Korte overgangen van 150-280 ms met `--ease`; alles uit bij `prefers-reduced-motion`.
- **Hover alleen met een muis** (sinds 4.12.0): elke `:hover`-regel staat in `@media (hover: hover)`, want op een aanraakscherm blijft een hover-stijl na een tik hangen. `tests/test_controls_js.py` laat een `:hover` daarbuiten falen.
- **Tikvlakken van 44 px.** Een nieuwe knop is 44 px. De bestaande kleine knoppen (min en plus, het potlood en de plus in het weekmenu, verwijderen bij een aankoop) houden hun maat en krijgen bij `(pointer: coarse)` een onzichtbaar tikvlak van 44 px.
- **Randen van invoervelden** gebruiken `--control-border` (3,1 : 1 op het vlak in elk thema); `--border` (1,4 : 1) is voor kaarten en scheidingslijnen.
- **Vandaag.** In het weekmenu krijgt de dag van vandaag `.day-card--today` met het label *Vandaag* (`plan.today`).

## Typografie en bediening

Manrope voor koppen en het woordmerk, Inter voor de interface. Lettertypen worden
lokaal geladen; de OFL-licenties staan naast de fonts in `static/fonts/`. Lettergroottes komen uit
een schaal van acht stappen (sinds 4.9.0, in plaats van 21 losse waarden):

| Token | Grootte | Voor |
| --- | --- | --- |
| `--text-xs` | 12 px | labels, badges, kleine meta |
| `--text-sm` | 13 px | hulptekst, meta, chips |
| `--text-md` | 14 px | ondersteunende tekst, tabellen |
| `--text-ui` | 15 px | knoppen, tabs, tekst in kaarten |
| `--text-base` | 16 px | lopende tekst en invoer |
| `--text-lg` | 18 px | kop van een blok of venster |
| `--text-xl` | 20 px | vakkop (`h2`), kerncijfer |
| `--text-2xl` | 24 px | schermtitel |

Een letterlijke grootte mag alleen voor wat geen lopende tekst is (het label van de menubalk, de maat van een
teken-icoon, de kolomkoppen van de kassabon, de initialen in de grote avatar) en de `clamp()` van de kookmodus,
elk met een opmerking; `tests/test_brand.py` houdt dat bij. Knoppen zijn semibold en minimaal 44 px hoog.
Invoervelden zijn 16 px, zodat iOS niet inzoomt bij het focussen. Interface-iconen komen uit
[Material Design Icons](https://pictogrammers.com/library/mdi/) (Pictogrammers, Apache 2.0;
licentie in `static/icons/LICENSE-mdi.txt`). Ze staan als SVG-pad inline in de HTML en nemen de
tekstkleur over (`fill: currentColor`); de naam van het icoon staat in `data-mdi`. Gebruik voor
nieuwe iconen dezelfde set, zonder eigen lijniconen ertussen.

## Bestanden

Alle beeldbestanden staan in `static/brand/`:

- `icon.svg`: appicoon met afgeronde achtergrond.
- `mark.svg`, `mark-white.svg`, `mark-mono.svg`: transparante beeldmerken.
- `wordmark.svg`, `wordmark-white.svg`, `wordmark-mono.svg`: horizontale logo's.
- `favicon.svg`, `favicon.ico` (16, 32 en 48 px), `favicon-16.png`, `favicon-32.png`, `favicon-48.png`.
- `icon-192.png`, `icon-512.png`: appiconen met ondoorzichtige achtergrond.
- `icon-maskable-512.png`: volle achtergrond; het beeldmerk blijft binnen de
  centrale veilige cirkel met diameter 80% van het icoon.
- `apple-touch-icon.png`: 180 x 180 px, ondoorzichtig.
- `social-card.png`: 1200 x 630 px deelafbeelding.
- `brand-preview.png`: overzicht van iconen en kleuren.

SVG-woordmerken bevatten bewerkbare tekst en vereisen Manrope op het apparaat of in
het ontwerpdocument voor identieke weergave. Maak voor drukwerk of uitwisseling met
externe ontwerpers een kopie met tekst omgezet naar contouren.

Laat minstens een kwart van de breedte van het tasje vrij rondom het logo. Rek het logo
niet uit. Gebruik het witte beeldmerk op groen en het groene op wit.

## PWA en hergebruik

`static/manifest.webmanifest` koppelt de appiconen aan een zelfstandig venster. De HTML
koppelt favicon, Apple-icoon, themakleur en deelafbeelding. Installatie hangt af van
browserondersteuning en een veilige HTTPS-verbinding. Er is geen offlinefunctie
toegevoegd: recepten en boodschappen blijven netwerkafhankelijk.

| Platform | Wat de app instelt | Let op |
| --- | --- | --- |
| iOS / iPadOS (Safari) | `apple-touch-icon.png` (180 px, ondoorzichtig), `apple-mobile-web-app-capable`, titel en statusbalkstijl | iOS negeert de iconen uit het manifest. Installeren via Delen > Zet op beginscherm; alleen via HTTPS. Er is geen eigen opstartscherm (splash) ingesteld; dat kan later met `apple-touch-startup-image`. |
| Android (Chrome, Edge en andere Chromium-browsers) | Manifest met iconen van 192 en 512 px, een apart maskable icoon, `display: standalone`, themakleur | Het maskable icoon wordt door de launcher bijgesneden; het beeldmerk blijft binnen de veilige cirkel. HTTPS vereist. |
| Windows (Edge, Chrome) | Hetzelfde manifest; `msapplication-TileColor` en `-TileImage` voor vastgezette sites | Installeren via het installatie-icoon in de adresbalk. De geïnstalleerde app gebruikt de iconen en de themakleur uit het manifest. |

`id`, `scope` en `start_url` zijn `/`: de app moet op de hoofdweg van haar domein draaien
(bijvoorbeeld `https://mealieshopper.sandmount.nl`), niet onder een pad.

Herbouw exports met `python scripts/build_brand.py` (Pillow nodig, alleen tijdens
het exporteren). De Quart-app heeft voor deze assets geen extra dependency nodig. De
SVG's, PNG's en het ICO-bestand komen uit dezelfde geometrie in dat script.

Gebruik echte gerechtfoto's met natuurlijk licht. Houd boodschappenlijsten rustig.
Schrijf kort en concreet: "Zoek bij AH", "Zoek recepten", "Je boodschappen staan klaar".

## Navigatie op een telefoon

In de geïnstalleerde app (PWA) op een klein scherm (tot 640 px breed) staat het hoofdmenu als
zwevende, glazen **onderste menubalk**, zoals een native tabbalk op iOS: vijf items met een
MDI-icoon en een kort label (`Recepten`, `Aanbod`, `Winkels`, `Boxen` en `Meer` met een hamburger-icoon), een
groen vlak onder het actieve item en minimaal 56 px hoog. `Meer` opent het menu dat op een groter scherm onder de
avatar in de kop zit: avatar en naam, een lijn, `Mandje`, `Profiel`, `Instellingen`, `Over`, een lijn, `Donkere modus`
(of `Lichte modus`) en de andere taal (`English` of `Nederlands`), een lijn en `Uitloggen`, elk met een MDI-icoon. `Mandje` staat op een klein scherm (tot 640 px, ook in een gewone browser) alleen in dat menu,
niet in de balk; `Meer` licht op als het mandje, het profiel, de instellingen of `Over` open staat. De balk zweeft 12 px van de randen en
houdt rekening met de veilige zone (home-indicator) via `env(safe-area-inset-*)`; daarvoor staat
de viewport op `viewport-fit=cover`. Wisselen van tab brengt je terug naar boven. Tussen 641 en
859 px, en in een gewone browser tot 859 px, staan de tabs bovenaan, met hetzelfde icoon links van de tekst; vanaf 860 px
staat het menu links.

- **Twee stijlen, één balk** (zoals [`navigation-bar.md`](https://github.com/Flux76HQ/App-Guidance/blob/main/shared/guidelines/navigation-bar.md)
  van Flux76HQ/App-Guidance). `data-nav` op `<html>` kiest het uiterlijk: `ios` is de zwevende
  glazen balk hierboven, `android` de navigatiebalk van Material 3 (over de volle breedte op de rand,
  een vlak dat 10% naar `--primary` neigt, geen glas, een pil van 64 x 32 achter het actieve icoon en
  een vet label). *Automatisch* (`auto`, de standaard) volgt het toestel: het script in de kop zet
  `data-platform` (een iPad meldt zich als Mac) en kiest daarmee, vóór de eerste weergave, uit de
  kopie van de voorkeur in `ms-appearance`. De keuze is een voorkeur van het account
  (`navbarStyle`, `navbarGlass`); bewaard wordt `auto`, niet wat dat op dit toestel is.
- **Hoogte:** `--tabbar-h` (82 px iOS, 84 px Android) is de enige hoogte die de pagina kent: de
  ruimte onder `.shell` en de balk van *In bulk importeren* volgen hem.
- **Iconen:** de actieve tab toont het gevulde MDI-icoon, de andere het omlijnde (`.tab-icon--outline`,
  als MDI er een heeft: Boxen en Meer niet). Buiten de balk staat er alleen het gevulde.
- **Glas:** `rgba(var(--tabbar-rgb), var(--nav-alpha))` met `blur(var(--nav-blur))`. De schuif
  *Glaseffect* (0 tot 100, standaard 60) wordt in JavaScript gewone waarden, met de schaal van
  3dprint_pricing: dekking `1 - 0,0085 x` (1 tot 0,15) en vervaging `10 + 0,3 x` px (10 tot 40 px); de
  standaard in `styles.css` is die van 60 (0,49 en 28 px). Geen berekening in `rgba()` of `blur()`: dan laat Safari het glas weg. Zonder
  ondersteuning (`@supports`) en bij `prefers-reduced-transparency` is de balk dicht.
- **Leesbaarheid:** de balk heeft eigen kleuren (`--tabbar-ink`, `--tabbar-ink-active`), omdat
  `--muted` en `--primary` boven een foto onder 4,5 : 1 zakken. Ze halen 4,5 : 1 boven zowel een
  witte als een zwarte foto tot dekking 0,75 (schuifstand 29); onder die dekking haalt geen
  labelkleur dat boven beide. Doorzichtiger glas is een bewuste keuze van de gebruiker, zoals in
  3dprint_pricing, en de uitleg bij de schuif zegt dat. `tests/test_tabbar.py` rekent het na voor licht,
  donker en donkergrijs van stand 0 tot 29, en `tests/test_appearance.py` voor elke accentkleur.
- **Beweging:** een korte overgang en een indrukeffect, uit bij `prefers-reduced-motion`.
- **Bekijken zonder installeren:** open de app in de browser van je telefoon met `?tabbar=1`
  (onthouden voor dat tabblad; `?tabbar=0` zet hem weer uit). De klasse `has-tabbar` op `<html>`
  bepaalt of de balk er is.
- **Zoals een native app:** alleen in de geïnstalleerde app (de klasse `is-standalone` op `<html>`,
  gezet door het script in de kop) staat de viewport op `maximum-scale=1, user-scalable=no` en
  `html, body` op `touch-action: pan-x pan-y` (geen knijp- en dubbeltikzoom), en op
  `overscroll-behavior: none`: de pagina veert niet mee als je bovenaan naar beneden sleept, dus de
  plakkende titelbalk blijft staan en Android ververst de pagina niet. In een gewone browser blijft
  zoomen werken; daar is het een kwestie van toegankelijkheid.

## Weergave per gebruiker

Onder **Instellingen, Weergave** kiest iedere gebruiker het thema, de achtergrond van het donkere
thema en een accentkleur; de keuze staat bij het profiel (`theme`, `darkBackground`, `accent` in
`PUT /api/profile/preferences`) en een kopie in `localStorage` (`ms-appearance`), zodat het script in
de kop de kleuren voor de eerste weergave zet. Het basilicumgroen blijft de standaard en het merk: het
logo, het manifest en de deelafbeelding veranderen niet.

- **Accentkleur.** `static/theme.js` maakt van één kleur de tinten `--accent-light`, `--accent-dark` en
  hun `-strong`, `-on` en `-ink`, en zet ze met `data-accent` op `<html>`. De lichtheid schuift (de tint
  blijft) tot de kleur 4,5 : 1 haalt op wit, `--bg`, `--surface-muted` en het selectievlak, en in het
  donkere thema op de vlakken van beide achtergronden. `-ink` is het label van de actieve tab in de
  onderste menubalk, nagerekend op het glas boven een zwarte en een witte foto. De rest volgt in
  `styles.css` uit `--primary` met `color-mix` (`--selection`, `--selection-border`, `--ring`,
  `--page-glow`, `--tabbar-tint`, de knopschaduw). Meldingen (`--success-*`, `--danger-*`,
  `--warning-*`), kortingsbadges en categoriekleuren volgen de accentkleur nooit.
- **Donkergrijs.** `data-dark-bg="grey"` vervangt in het donkere thema alleen de neutrale vlakken,
  tekst en randen (`#121314`, `#1B1C1E`, `#232527`), met hetzelfde contrast als het groene thema.
- `tests/test_appearance.py` rekent de tinten na voor de tien keuzes en voor lastige eigen kleuren
  (wit, zwart, citroen, felgroen ...).
- **Indeling.** Weergave heeft twee inklapbare blokken (`.box.fold`), met de modifier
  `.fold--compact`: dicht is een blok alleen zijn kop van 18 px (8 px boven en onder de knop van
  44 px), open loopt het door, met tussenkoppen van 16 px. *Voorkeuren* heeft de kookmodus; *Thema* de
  accentkleur, het thema met de achtergrond van het donkere thema, en de navigatiebalk, gescheiden door
  een lijn (`.fold__section`). Zonder keuze staan de blokken dicht; de browser onthoudt welke open
  waren (`ms-fold-appearance-*`).
- **Koppelen** gebruikt dezelfde compacte blokken: *Aanbiedingen* (standaard open), *AI-beoordeling
  van koppelingen* (standaard dicht), *Mealie* en de winkels (dicht zodra gekoppeld). Een nieuw
  inklapbaar blok krijgt altijd `.fold--compact`; het hoge `.box.fold` blijft alleen voor Profiel,
  Beveiliging.
- **Schakelaar.** `.switch` is een echt selectievakje met `role="switch"`, getekend als schuifje:
  aan gevuld in `--primary` met het bolletje in `--on-primary`, uit een rand in `--placeholder`
  (3 : 1 of meer) met een bolletje in `--muted`. Een zichtbare focusring zoals elke bediening.
- **Kookmodus uit** zet `no-cook-mode` op `<html>` (ook al in het script in de kop); dan verbergt
  `styles.css` bij een recept de balk met Start met koken en de vinkjes bij de ingrediënten.

## Taal en thema

De kop van de app heeft alleen het logo en de avatar (sinds 4.7.0). Taal en thema staan in het menu onder de avatar
(op een telefoon onder `Meer`) en bij Instellingen, Voorkeuren: het menu-item zegt wat een tik doet (`Donkere modus`
of `Lichte modus`, en de andere taal in die taal zelf). De slogan staat alleen op de inlogkaart.

Op de inlogpagina, bij een uitnodiging en in de installatiehulp, waar er nog geen menu is, staat een taalkiezer met de
knop voor licht en donker ernaast. De taalkiezer is tekst, `NL` en `EN`, geen vlaggen: een vlag staat voor een land,
niet voor een taal. Twee knoppen van minstens 44 px, met de taalnaam in de eigen taal als `aria-label` en `aria-pressed`
voor de gekozen taal; de gekozen taal heeft het selectievlak met zijn rand. Zie het onderdeel Taal in de
[handleiding](handleiding.md#taal).

## Wat is gecontroleerd

- Het manifest is geldig en Chromium (de engine van Chrome en Edge) meldt geen
  installatiefouten; getest met het ontwikkelprotocol van de browser.
- Manifest, iconen en lettertypen zijn bereikbaar zonder in te loggen.
- Maten van alle PNG's en het ICO-bestand, het ontbrekende alfakanaal bij de Apple- en
  maskable iconen, en het contrast van alle kleurparen (`tests/test_brand.py`).

Niet gecontroleerd op echte apparaten: het installeren op iOS, Android en Windows, het
bijsnijden van het maskable icoon door een launcher, en de weergave van de deelafbeelding
in chat- en socialmediaapps. Controleer die na het uitrollen met HTTPS.
