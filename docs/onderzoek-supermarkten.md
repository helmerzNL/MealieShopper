# Onderzoek: andere supermarkten

Doel: vaststellen welke andere Nederlandse supermarkten een plek kunnen krijgen naast Albert Heijn, Jumbo, Picnic en
Crisp, en op welk niveau: een volledige winkel (zoeken, mandje vullen, bestellingen) of alleen de aanbiedingen. Dit is een
spike van 8 november 2026, naast het [onderzoek naar Lidl](onderzoek-lidl.md). Gelezen zijn de openbare pagina's,
`robots.txt` en voorwaarden van de ketens, de marktaandelen van NielsenIQ en de broncode van open-source projecten die
prijzen van supermarkten ophalen. Er is niet ingelogd en er is geen account gebruikt. Aldi is daarna ingebouwd (zie
[Status](#status)).

## Status

Gebouwd in v4.4.0: **Aldi** staat, net als Lidl, als winkel met alleen aanbiedingen in MealieShopper
(`mealieshopper/aldi.py`), zonder account en zonder mandje. De andere ketens zijn niet gebouwd; het voorstel staat onder
[Voorstel](#voorstel).

## Overzicht

Marktaandeel: NielsenIQ, 2025 (vrijgave januari 2026). Dirk, Lidl, Vomar, DekaMarkt, Boni en Nettorama vallen daar onder
"overig" (22,4%); Lidl had in 2024 11,6%. Dirk laat zijn aandeel niet publiceren.

| Keten | Online mandje (2026) | Toegang | Aandeel | Voorstel |
| --- | --- | --- | --- | --- |
| **Aldi** | Nee | Aanbiedingen als JSON in `www.aldi.nl/aanbiedingen.html`; product-zoeken via Algolia | 5,9% | **Gebouwd** (aanbiedingen) |
| **Plus** | Ja: bezorgen (ook binnen 2 uur) en afhalen, vanaf € 25 | OutSystems: `www.plus.nl/screenservices/...`, anoniem met een vaste CSRF-header | 8,1% | **Hoog**: volledige winkel |
| **Hoogvliet** | Ja: afhalen (vanaf € 50) en bezorgen (vanaf € 60), ook een app | Magento 2 GraphQL: `hoogvliet.nl/graphql`, header `Store: hoogvliet_nl`, anoniem | 1,9% | **Middel-hoog**: volledige winkel, regionaal |
| **Dirk** | Nee: de app heeft alleen een boodschappenlijst | GraphQL: `web-gateway.dirk.nl/graphql`, alleen een `Origin`-header, met `listOffers` | overig | **Middel**: aanbiedingen, zoals Lidl en Aldi |
| Spar | Ja, per winkel (bezorgen of afhalen) | geen API bekend | 1,7% | Laag: assortiment per winkel |
| Poiesz | Ja: bezorgen of afhalen | geen API bekend | 1,1% | Laag: alleen het noorden |
| Ekoplaza | Ja (online bestellen, afhaalpunten) | geen API bekend | – | Laag: niche |
| Vomar | Nee | `api.vomar.nl/api/v1/article/...` | overig | Laag |
| DekaMarkt | Nee: bezorgen is gestopt | `api.dekamarkt.nl/v1/assortmentcache/...` met een sleutel uit de app | overig | Laag |
| Coop | Opgegaan in Plus (laatste winkel juli 2025) | – | 0,3% | Afvallen |
| Boni | Eigen webshop gesloten; winkels worden Nettorama | – | overig | Afvallen |
| Nettorama | Nee | – | overig | Afvallen |
| Flink | Flitsbezorger, geen supermarkt met een eigen assortiment en aanbiedingen | – | – | Afvallen |

"Toegang" komt uit de broncode van open-source projecten (zie [Bronnen](#bronnen)). Behalve bij Aldi zijn er geen
verzoeken aan deze API's gedaan.

## Aldi

| Onderdeel | Bevinding |
| --- | --- |
| Online boodschappen | Geen. |
| Aanbiedingenpagina | `https://www.aldi.nl/aanbiedingen.html` (Next.js). In `<script id="__NEXT_DATA__">` staat onder `props.pageProps` een sleutel `apiData`: een JSON-tekst met een lijst als `[["OFFER_GET", {"req": {"locale": "nl", "week": "current"}, "res": {...}}]]`. |
| Producten | `res.algoliaDataMap`: 201 producten op 8 november, met `objectID`, `name`, `brandName`, `salesUnit` ("1 kg", "Per stuk"), `assets[]` (scene7), `productSlug`, `mainCategoryID`, `hierarchicalCategories.lvl0`, `isAvailable`, `currentPrice` en `promotionPrices[]`. 33 hebben geen `currentPrice`. |
| Prijs | `currentPrice`: `priceValue`, `strikePrice.strikePriceValue` (oude prijs), `basePrice[]`, `priceTagLabels.promoText1` (`OP=OP` 121 keer, `-15%`, `2 VOOR`, en een keer de rommel `-- Select option --`), `validFrom` en `validUntil` (unix). |
| Valkuil | Rond de wissel van de week staat in `currentPrice` soms al de prijs **na** de actie (84 producten op 8 november, zonder label). `promotionPrices[]` geeft de actieperiodes expliciet, met `validFromLocalDate` en `validUntilLocalDate`, en `categories[]` geeft per groep ("Vanaf maandag") een `startDate` en `endDate` met de `productIds`. |
| "2 VOOR" | De prijs geldt voor twee: Crystal Clear heeft `priceValue: 3`, label "2 VOOR" en "Per stuk 1.50" in de omschrijving. |
| Productpagina | `https://www.aldi.nl/product/<productSlug>.html` (gecontroleerd: 200, een onbekende slug geeft 404). |
| Foto's | `https://s7g10.scene7.com/is/image/aldinord/...`: het origineel is ongeveer 261 kB; met `?wid=500&hei=500` ongeveer 48 kB. |
| Volgende week | De pagina heeft `hasNextWeekoffers` en een label voor volgende week, maar die gegevens laadt de browser pas later; ze staan niet in de HTML. |
| `robots.txt` | Minimaal; de aanbiedingenpagina en de productpagina's zijn toegestaan. |
| Voorwaarden | Geen gebruiksvoorwaarden voor de website gevonden. |

Hoe het in de code past: als Lidl (zie [onderzoek-lidl.md](onderzoek-lidl.md#hoe-lidl-in-de-code-past)).

- `mealieshopper/aldi.py` met `get_offers()` en een losse parser `offers_from_html(text, today)`.
- **Een periode per product:**
  - de periodes komen uit `promotionPrices[]`;
  - zonder die periodes telt `currentPrice` alleen als die binnen het venster van de groep valt;
  - "N VOOR" wordt een stukprijs met het label "2 voor 3,00", dat `deals.parse_mechanic` leest als x_for_y;
  - een product dat alleen in non-foodgroepen staat (speelgoed, kleding, elektronica, enzovoort), valt weg.

## Plus

- Volledige webwinkel met mandje.
- Product-API: OutSystems op `www.plus.nl/screenservices/...`, zonder login.
  - Eerst `/moduleservices/moduleversioninfo` voor de versie.
  - Dan `POST /screenservices/ECP_Composition_CW/ProductLists/PLP_Content/DataActionGetProductListAndCategoryInfo`
    met een vaste anonieme `x-csrftoken`.
  - Velden: `OriginalPrice`, `NewPrice`, `Product_Subtitle` (verpakking), `PromotionLabel` en de looptijd van de actie.
  - `moduleVersion` en `apiVersion` veranderen bij elke uitrol; een client leest ze opnieuw en probeert het nog een keer.
- Het mandje heet `ECP_Cart_CW/ActionCreateCart`. Hoe je een mandje met login vult, is niet uitgezocht.
- `robots.txt` sluit `/screenservices` niet uit.
- Voorwaarden: aanvullende voorwaarden 5.1, kopiëren van informatie alleen "voor persoonlijk huishoudelijk gebruik".

## Hoogvliet

- Volledige webwinkel met mandje (afhalen en bezorgen). De site is Next.js op Magento 2.
- GraphQL op `hoogvliet.nl/graphql` met de header `Store: hoogvliet_nl`, zonder login.
  - Velden: `price_range.regular_price` en `final_price`, `sub_title_attributes` (inhoud, eenheid en prijs per eenheid) en
    `promotion_meta_data` (actietekst en einddatum).
  - Magento heeft standaard ook mandjesbewerkingen in dezelfde GraphQL; niet geprobeerd.
- `robots.txt` sluit `/search` en de oude `/INTERSHOP`-paden uit, `/graphql` niet.
- Voorwaarden: art. 9, niet kopiëren "behalve voor persoonlijk gebruik".

## Dirk

- Geen online boodschappen; de app heeft alleen een boodschappenlijst.
- GraphQL op `web-gateway.dirk.nl/graphql`, alleen met `Origin: https://www.dirk.nl`, zonder sleutel of login.
  - Queries: `listDepartments`, `listWebGroupProducts`, `products(storeId: …)` en `listOffers`.
  - Velden: `normalPrice`, `offerPrice`, `packaging` en de looptijd van de actie.
- `robots.txt`: `Allow: /`.
- Disclaimer: "Informatie is alleen voor persoonlijk gebruik" en "Gebruik geen tools die overlast veroorzaken".

## Voorwaarden

Bij geen van de vier kandidaten (Plus, Hoogvliet, Dirk, Aldi) staat in de gelezen delen een expliciet verbod op scrapen of
tools, zoals bij Picnic, Jumbo en Crisp. Plus, Hoogvliet en Dirk staan het kopiëren van informatie toe voor persoonlijk
gebruik; dat past bij een app die je zelf host voor je eigen huishouden. Dit is een vergelijking van teksten, geen
juridisch advies.

## Een winkel met alleen aanbiedingen in de code

Lidl en Aldi zijn de eerste winkels zonder mandje en zonder account. Voor een volgende, zoals Dirk, zijn dit de plekken:

- **Backend:**
  - een module met `get_offers()` en een losse parser, met een eigen `…Error(RuntimeError)`;
  - `offers.py`: `FETCHERS`, `OFFER_ONLY_STORES` en `STORE_NAMES`;
  - `audit.SERVICE_DOMAINS`, `image_cache` (`HEADERS` en de hosts in `store_for`) en `about.COMPONENTS`;
  - `prices._STORE_WORDS`.
  - Geen geheimen, geen tabellen en geen routes: `offers`, `offer_history` en `offers_meta` staan al in de back-up.
- **Frontend:**
  - `OFFER_ONLY_STORES` en `STORE_IMAGE_HOSTS` in `static/app.js`;
  - `OFFERS_STORES` in `static/offers.js`;
  - `AUDIT_SERVICE_KEYS` in `static/audit.js` en de keuzelijst in `templates/index.html`.
- **Teksten:**
  - `static/i18n/nl.js` en `en.js` (`audit.service.*` en `about.desc.*`);
  - de README's en de handleidingen;
  - de Home Assistant-vertalingen.
- **Tests:** een eigen testbestand met een fixture, plus `test_offers_stores`, `test_audit`, `test_image_cache`,
  `test_store_images`, `test_about` en `test_mcp`.

Wat bij de winkel zoekt of een bestelling importeert (`prices.compare`, `link_candidates`, `product_links` en
`price_import`), gebruikt `offers.SHOP_STORES` en hoeft niet te veranderen.

## Voorstel

1. **Dirk, aanbiedingen.** Klein: dezelfde vorm als Lidl en Aldi, met een GraphQL zonder login.
2. **Plus, volledige winkel.** De grootste keten met een online mandje. Eerst uitzoeken hoe inloggen en het mandje
   werken (een eigen spike, zoals bij Crisp).
3. **Hoogvliet, volledige winkel.** Een standaard Magento-GraphQL, maar regionaal.
4. De rest wacht tot iemand erom vraagt.

## Bronnen

- [NielsenIQ: marktaandelen 2024-2025](https://nielseniq.com/wp-content/uploads/sites/4/2026/01/NielsenIQ-Vrijgave-Marktaandelen-2024-2025.pdf)
  en [Levensmiddelenkrant over Circana](https://www.levensmiddelenkrant.nl/circana--ah--picnic-en-dirk-winnen-meeste-marktaandeel)
- [aldi.nl/aanbiedingen.html](https://www.aldi.nl/aanbiedingen.html) en [aldi.nl/robots.txt](https://www.aldi.nl/robots.txt)
- [plus.nl/info-bezorgen](https://www.plus.nl/info-bezorgen), [aanvullende voorwaarden Plus](https://www.plus.nl/voorwaarden/aanvullende-voorwaarden)
  en [plus.nl/robots.txt](https://www.plus.nl/robots.txt)
- [hoogvliet.nl/online-bestellen](https://hoogvliet.nl/online-bestellen), [algemene voorwaarden Hoogvliet](https://hoogvliet.nl/algemene-voorwaarden)
  en [hoogvliet.nl/robots.txt](https://hoogvliet.nl/robots.txt)
- [De Dirk-app](https://www.dirk.nl/meer/over-dirk/dirk-app), [disclaimer Dirk](https://www.dirk.nl/disclaimer-en-responsible-disclosure)
  en [dirk.nl/robots.txt](https://www.dirk.nl/robots.txt)
- [poiesz-supermarkten.nl/boodschappen-bestellen](https://www.poiesz-supermarkten.nl/boodschappen-bestellen),
  [spar.nl](https://www.spar.nl/), [ekoplaza.nl](https://www.ekoplaza.nl/), [dekamarkt.nl](https://www.dekamarkt.nl/) en
  [vomar.nl/klantenservice](https://www.vomar.nl/klantenservice)
- [supermarkt/checkjebon](https://github.com/supermarkt/checkjebon) (MIT): prijzen van onder meer AH, Aldi, Dirk,
  Hoogvliet, Jumbo, Plus, Spar en Vomar, zonder aanbiedingen
- [shelly-nas/supermarket-scraper](https://github.com/shelly-nas/supermarket-scraper) (`scraper/src/clients/PlusSite.ts`,
  `DirkGraphql.ts`, `HoogvlietGraphql.ts` en `AldiAlgolia.ts`): geen licentie, dus alleen als naslag voor de eindpunten
- [boodschrappen/boodschrappen](https://github.com/boodschrappen/boodschrappen) (crawlers voor Vomar en DekaMarkt) en
  [digitalrebelz/supermarket-price-compare](https://github.com/digitalrebelz/supermarket-price-compare) (MIT)
