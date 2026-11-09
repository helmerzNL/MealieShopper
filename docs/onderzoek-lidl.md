# Onderzoek: Lidl

Doel: vaststellen of MealieShopper Lidl kan koppelen naast Albert Heijn, Jumbo, Picnic en Crisp, en zo ja, op welk niveau:
het mandje vullen, producten en prijzen zoeken, de aanbiedingen of de kassabonnen van Lidl Plus. Dit is een spike van
8 november 2026. Gelezen zijn de openbare pagina's van `www.lidl.nl`, `robots.txt`, de sitemap, de folder-API van de
Schwarz-groep, de voorwaarden van Lidl en Lidl Plus, en de broncode van open-source projecten voor Lidl Plus. Er is niet
ingelogd en er is geen account gebruikt. De andere supermarkten staan in [onderzoek-supermarkten.md](onderzoek-supermarkten.md).

## Status

Gebouwd in v4.5.0: Lidl staat als **winkel met alleen aanbiedingen** in MealieShopper (`mealieshopper/lidl.py`), zonder
account en zonder mandje.

- **Wel**:
  - de aanbiedingen van de week in het tabblad Aanbiedingen, ook in de dagelijkse synchronisatie;
  - prijzen "met Lidl Plus", met dat label erbij;
  - de aanbiedings- en prijsgeschiedenis, de receptvoorstellen en de AI-functies die op de aanbiedingen werken;
  - het prijsarchief (exporteren en importeren);
  - productfoto's in de afbeeldingencache en het verkeer in het auditlog.
- **Niet**:
  - het mandje vullen: Lidl verkoopt geen boodschappen online;
  - producten zoeken en verpakkingen vergelijken (zie [Zoeken en prijzen](#zoeken-en-prijzen));
  - Lidl als voorkeurssupermarkt: die opent ook Winkels, en daar staat voor Lidl niets;
  - de kassabonnen van Lidl Plus (vervolgstap A).

## Uitkomst

**Een mandje kan niet; de aanbiedingen kunnen wel, zonder account.**

- Lidl Nederland verkoopt geen boodschappen online. De webshop op lidl.nl is alleen voor non-food. Bij food staat "Alleen
  in de winkel" en `onlineAvailable: false`. Er is geen bezorging en geen Click & Collect voor boodschappen.
- De aanbiedingen van de week staan op een openbare pagina, met per product een blok JSON: titel, prijs, oude prijs,
  actietekst, looptijd, verpakking, foto en de prijs met Lidl Plus. `robots.txt` staat die pagina toe.
- De vaste schapprijs van het gewone assortiment staat niet online. Een prijsvergelijking met Lidl kan dus alleen op de
  aanbiedingen.
- De kassabonnen van Lidl Plus zijn op te halen via de app-API, maar inloggen gaat alleen in een echte browser, vanwege
  reCAPTCHA.

## Wat is vastgesteld

| Onderdeel | Bevinding |
| --- | --- |
| Online boodschappen | Geen. De zoekresultaten markeren food als "Alleen in de winkel" (badge `IN_STORE`, `onlineAvailable: false`); alleen non-food is "Leverbaar". De footer van lidl.nl noemt alleen bezorging van de non-food-webshop (DHL, DynaLogic). `supermarkt/checkjebon` laat Lidl weg: "No online assortment". |
| Aanbiedingenpagina | `https://www.lidl.nl/c/aanbiedingen/a10008785` (gelinkt vanaf de homepage) verwijst met `301` naar `/c/store/aanbiedingen/a10008785` op dezelfde host. Op 8 november stonden er 47 productkaarten op. Elke kaart heeft een attribuut `data-grid-data` met JSON (HTML-escaped). |
| Velden per kaart | `productId` (uniek), `fullTitle` (soms met `- ` ervoor), `brand.name` (of `null`), `category` (`Food`, `F+V`, `P+F`, `NonFood`), `canonicalUrl` (`/p/<slug>/p<id>`), `image` (PNG op `www.lidl.nl/assets`), `storeStartDate` en `storeEndDate` (unix, de periode in de winkel), `timezoneId` (`Europe/Amsterdam`), `price` (`price`, `packaging.text`, `basePrice.text`) en `regionsPrices` (zie hieronder). |
| Prijs en actie | `regionsPrices["1"].currentPrice`: `price`, `startDate` en `endDate` (ISO in UTC; een einddatum in 2200 betekent een vaste prijs), `discount` met `discountText` (`XXL`, `Op=Op!`, `Actie`, `Megapak`, `-25%`), `deletedPrice`, `percentageDiscount` en `absoluteDiscount`. `startDate` is het moment van publiceren, niet de eerste dag in de winkel: daarvoor is `storeStartDate`. |
| Lidl Plus | `regionsPrices["1"].currentLidlPlusPrice.price`: `price`, `oldPrice`, `discount.deletedPrice`, `discount.discountText` (`-31%`), `startDate` en `endDate`; een niveau hoger `lidlPlus[]` met `lidlPlusText` ("met Lidl Plus") en `highlightText`. Op 8 november hadden alle 17 kaarten met een vaste prijs een Lidl Plus-prijs. Geen kaart had zowel een gewone korting als een Lidl Plus-prijs. |
| Regio's | Lidl kent zes prijsregio's. Op deze pagina staat alleen regio `"1"`, en `regionsV2` wijst de bekeken regio's naar die prijs. |
| Zoek-API | `https://www.lidl.nl/q/api/search?q=…&assortment=NL&locale=nl_NL&version=v2.0.0` geeft JSON, maar `robots.txt` heeft `Disallow: *search?q=*`. Daarbij dekt hij het voedselassortiment slecht: "kaas" gaf 80 resultaten, waarvan 11 in "Eten & drinken"; "milbona" (het zuivelmerk van Lidl) geen enkel Milbona-product. |
| Productpagina's | 13.504 in `https://www.lidl.nl/p/export/NL/nl/product_sitemap.xml.gz`, en toegestaan. De JSON-LD (`Product`) heeft bij gewone food geen prijs: alleen `availability: InStoreOnly`. |
| Folder | `https://endpoints.leaflets.schwarz/v4/overview?client_locale=lidl/nl-NL` geeft de folders (19 op 8 november) met `offerStartDate` en `offerEndDate`. `/v4/flyer?flyer_identifier=…` geeft per pagina afbeeldingen en trefwoorden, maar geen europrijzen bij food. De host heeft geen `robots.txt`. |
| `robots.txt` | `User-agent: *` met onder meer `Disallow: /user-api/*`, `*search?q=*`, `*?offset=*`, `*id=*`, `*sort=*`. De aanbiedingenpagina en de productpagina's vallen daar niet onder. |
| User-Agent | Zowel een browser als `MealieShopper/<versie> (+https://github.com/helmerzNL/MealieShopper)` krijgen de volledige pagina. |

## Lidl Plus

| Onderdeel | Bevinding |
| --- | --- |
| Inloggen | OAuth 2 met PKCE bij `accounts.lidl.com` (`/connect/authorize` en `/connect/token`), client `LidlPlusNativeClient`, redirect `com.lidlplus.app://callback`. De loginpagina heeft reCAPTCHA: inloggen gaat alleen in een echte browser. Het refresh-token verandert bij elke vernieuwing; het oude werkt dan niet meer. |
| Kassabonnen | `GET https://tickets.lidlplus.com/api/v2/NL/tickets` (lijst) en `/api/v3/NL/tickets/{id}`: per regel artikelnummer, barcode, aantal, stukprijs, korting, statiegeld en btw. |
| Coupons | Lijst via `/app/api/v2/promotionslist` (met `Country` en een winkel-id), activeren met `POST /app/api/v1/promotions/<id>/activation`. |
| Projecten | [erwindamsma/lidl-receipts-analyser](https://github.com/erwindamsma/lidl-receipts-analyser) (MIT, voor Nederland, login in de eigen browser) is het beste voorbeeld. [yagueto/lidl-plus](https://github.com/yagueto/lidl-plus) ("Working as of 25/09/2026", login met Selenium in een zichtbare browser). [Andre0512/lidl-plus](https://github.com/Andre0512/lidl-plus) (MIT) wordt niet onderhouden en het inloggen werkt niet meer (issues #24 en #27). [klassm/homeassistant-lidl-plus](https://github.com/klassm/homeassistant-lidl-plus) activeert coupons. |
| Catalogus | Geen project gevonden dat via de app een catalogus met prijzen voor Nederland leest. |

## Voorwaarden

| Document | Wat erin staat |
| --- | --- |
| [Deelnamevoorwaarden Lidl Plus](https://www.lidl.nl/c/lidl-plus-algemene-voorwaarden/s10008391) (april 2026, versie 2.2) | Art. 2: "Deelname is alleen toegestaan voor privégebruik." Art. 3: "Het is verboden om andere personen toegang te geven tot uw account." Art. 5.1: geen gebruik op "gemanipuleerde eindapparaten (bijv. door jailbreaking/rooting)". Art. 10: Lidl kan het contract "te allen tijde zonder opgave van reden beëindigen" en gegevens tijdelijk blokkeren. Niets over scrapen, bots of reverse engineering. |
| [Algemene voorwaarden](https://www.lidl.nl/c/algemene-voorwaarden/s10004350) | Voorwaarden voor de webshop (Thuiswinkel), niets over het gebruik van de website of over scrapen. Een aparte pagina met gebruiksvoorwaarden voor de site is er niet. |

Wat dit betekent:

- Voor de aanbiedingen is geen account nodig. MealieShopper leest één openbare pagina die `robots.txt` toestaat, één keer
  per dag en als je zelf op synchroniseren drukt. Daar geldt geen voorwaarde over tools, zoals bij Picnic, Jumbo en Crisp.
- De kassabonnen van Lidl Plus vragen het account van de gebruiker. Daarvoor geldt dat Lidl het account zonder reden mag
  opzeggen, en een huishouden deelt het account in MealieShopper; art. 3 verbiedt anderen toegang te geven. Dat is een
  aparte keuze (vervolgstap A).
- Dit is een vergelijking van teksten, geen juridisch advies.

## Hoe Lidl in de code past

Lidl is een **winkel met alleen aanbiedingen**: `offers.OFFER_ONLY_STORES` naast `offers.SHOP_STORES` (de winkels met een
mandje). `offers.STORES` is beide samen.

- `mealieshopper/lidl.py`: `get_offers()` voor `offers.FETCHERS`, en een losse parser `offers_from_html(text, today)`.
  - Per kaart geldt een gewone aanbieding met een einddatum. Anders geldt de Lidl Plus-prijs, met het label
    "-31% met Lidl Plus" en de gewone prijs als oude prijs. Een vaste prijs zonder actie valt weg.
  - `validFrom` is `storeStartDate`, `validUntil` is de einddatum van de prijs, beide in Nederlandse tijd
    (`deals.local_day`).
  - `NonFood` en `P+F` (planten en bloemen) vallen weg.
  - Een lege lijst is een fout (`LidlError`). Dan blijft de vorige kopie staan, zoals bij Crisp.
- `offers.linked()` noemt Lidl niet, dus Lidl telt als gekoppeld en doet mee in `sync_all` en de dagelijkse run.
- Alles wat alleen lokale tabellen leest, kent Lidl via `offers.STORES`: de aanbiedings- en prijsgeschiedenis, de
  receptvoorstellen, `ai_match`, `ai_review`, het prijsarchief en de MCP-tools voor aanbiedingen.
- Wat bij de winkel zoekt of een bestelling importeert, kent Lidl bewust niet:
  - `prices.compare` en `link_candidates` (Vergelijk verpakkingen en koppelen, 400 met uitleg);
  - `product_links`;
  - `price_import`.
- Foto's: `www.lidl.nl` en `imgproxy-retcat.assets.schwarz` in `image_cache.store_for`, `STORE_IMAGE_HOSTS` en
  `audit.SERVICE_DOMAINS`.
- Frontend: `OFFER_ONLY_STORES` in `static/app.js` (naam, `storeDisplayName`, `storeWithoutLink`), `OFFERS_STORES` in
  `static/offers.js`, en een uitleg boven de aanbiedingen.

## Besluit

De eigenaar besloot op 8 november 2026, na de eerste bevindingen, om Lidl en Aldi meteen in te bouwen als winkels met
alleen aanbiedingen, en het onderzoek naar de andere supermarkten vast te leggen. Lidl en Aldi worden geen keuze als
voorkeurssupermarkt, non-food valt weg, de Lidl Plus-prijzen staan erbij met dat label, en MealieShopper stuurt zijn eigen
User-Agent.

## Wat niet is vastgesteld

- Of de aanbiedingenpagina alle aanbiedingen van de week toont, of een selectie. Doorbladeren met `?offset=` verbiedt
  `robots.txt`. De folder heeft vaak meer producten dan de 47 kaarten.
- Of de prijzen in de andere vijf regio's ooit afwijken van regio `"1"`.
- Of Lidl verzoeken vanaf servers of datacenters gaat weren (er zit een CDN voor, Myra).
- Of `storeStartDate` er altijd is. Zo niet, dan neemt MealieShopper de begindatum van de prijs.
- Hoe lang het inloggen bij Lidl Plus werkt zoals in `lidl-receipts-analyser`.

## Vervolg

- **A. Kassabonnen van Lidl Plus.** Eenmalig inloggen in de eigen browser en het refresh-token plakken, zoals bij AH. Het
  nieuwe token na elke vernieuwing bewaren. De bonnen gaan naar `imported_purchases` (`price_import.py`): dan tellen ze mee
  in de prijsgeschiedenis en staan ze bij Bestellingen. Eerst de voorwaarden (art. 3, een gedeeld account) met de eigenaar
  doornemen.
- **B. Een boodschappenlijst voor in de winkel**, als vervanging van het mandje.
- **C. De vaste prijs.** De kaarten met een vaste prijs (einddatum 2200) en `futurePrices` als gewone prijs in `prices`,
  zodat het oordeel over een aanbieding niet alleen op de bewering van Lidl rust.
- **D. Een schakelaar per winkel**, voor wie geen Lidl- of Aldi-aanbiedingen wil zien.

## Bronnen

- [lidl.nl/c/aanbiedingen/a10008785](https://www.lidl.nl/c/aanbiedingen/a10008785), [lidl.nl/robots.txt](https://www.lidl.nl/robots.txt)
  en [lidl.nl/static/sitemap.xml](https://www.lidl.nl/static/sitemap.xml)
- [Folder-API van de Schwarz-groep](https://endpoints.leaflets.schwarz/v4/overview?client_locale=lidl/nl-NL)
- [Deelnamevoorwaarden Lidl Plus](https://www.lidl.nl/c/lidl-plus-algemene-voorwaarden/s10008391) en
  [Algemene voorwaarden Lidl](https://www.lidl.nl/c/algemene-voorwaarden/s10004350)
- [Klantenservice: "Kunnen mijn boodschappen ook bezorgd worden?"](https://customercare.lidl.nl/SelfServiceNL/s/article/Kunnen-mijn-boodschappen-ook-bezorgd-worden)
  (alleen via een zoekmachine gelezen; de pagina laadde niet)
- [erwindamsma/lidl-receipts-analyser](https://github.com/erwindamsma/lidl-receipts-analyser),
  [yagueto/lidl-plus](https://github.com/yagueto/lidl-plus), [Andre0512/lidl-plus](https://github.com/Andre0512/lidl-plus)
  en [klassm/homeassistant-lidl-plus](https://github.com/klassm/homeassistant-lidl-plus)
- [supermarkt/checkjebon](https://github.com/supermarkt/checkjebon)
