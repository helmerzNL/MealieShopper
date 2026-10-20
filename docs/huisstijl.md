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
| Afgeronde lijnvormen | Icoonrichtlijnen | Consistente vormtaal; geen gemengde iconensets |

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
| Kaart / bediening | 12 px / 8 px radius | `--radius-card` / `--radius-control` |

Citroen is decoratief; gebruik het niet als tekst op wit (contrast 1,5 : 1). Fouten
blijven rood (`--danger`) en krijgen een tekstuele uitleg. Focusringen zijn zichtbaar
groen (3 px, `:focus-visible`). Bij twijfel in een keuze (bijvoorbeeld in "Controleer je
mandje") gebruiken we een amberkleurig waarschuwingsvlak met uitleg, geen citroen.

Aanvullend, afgeleid van het palet: `--surface-muted` (`#EEF3F0`) voor plaatsvervangers en
codevlakken, en melding-tinten voor succes, fout en waarschuwing. `tests/test_brand.py`
bewaakt dat de tokens kloppen en dat alle tekst-/achtergrondparen WCAG AA halen
(4,5 : 1 voor tekst, 3 : 1 voor het beeldmerk).

## Typografie en bediening

Manrope voor koppen en het woordmerk, Inter voor de interface. Lettertypen worden
lokaal geladen; de OFL-licenties staan naast de fonts in `static/fonts/`. Basis 16 px,
ondersteunende tekst 14 px, paginatitels 24-32 px. Compacte metadata mogen kleiner zijn.
Knoppen zijn semibold en minimaal 44 px hoog. Invoervelden zijn 16 px, zodat iOS niet
inzoomt bij het focussen. Gebruik voor toekomstige interface-iconen één lijniconenset met
ronde uiteinden (zie `.icon` in de stylesheet).

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
het exporteren). De Flask-app heeft voor deze assets geen extra dependency nodig. De
SVG's, PNG's en het ICO-bestand komen uit dezelfde geometrie in dat script.

Gebruik echte gerechtfoto's met natuurlijk licht. Houd boodschappenlijsten rustig.
Schrijf kort en concreet: "Zoek bij AH", "Weekmenu laden", "Je boodschappen staan klaar".

## Wat is gecontroleerd

- Het manifest is geldig en Chromium (de engine van Chrome en Edge) meldt geen
  installatiefouten; getest met het ontwikkelprotocol van de browser.
- Manifest, iconen en lettertypen zijn bereikbaar zonder in te loggen.
- Maten van alle PNG's en het ICO-bestand, het ontbrekende alfakanaal bij de Apple- en
  maskable iconen, en het contrast van alle kleurparen (`tests/test_brand.py`).

Niet gecontroleerd op echte apparaten: het installeren op iOS, Android en Windows, het
bijsnijden van het maskable icoon door een launcher, en de weergave van de deelafbeelding
in chat- en socialmediaapps. Controleer die na het uitrollen met HTTPS.
